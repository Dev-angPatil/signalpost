#!/usr/bin/env python3
"""Build the declared NAV vacancy cache: active ads keyed by employer org number.

Source: NAV's official job-vacancy feed (pam-stilling-feed.nav.no) with the
public bearer token NAV publishes at /api/publicToken. Nothing in the cache is
published as-is: at run time every cached ad is re-fetched live and re-checked
(status ACTIVE, employer.orgnr, expiry) before it becomes a claim.

Resumable. Work files live in data/cache-work/:
  nav-walk.jsonl     one line per feed page: {"page_url", "next_url", "items": [...]}
  nav-entries.jsonl  one line per fetched feed entry of an ACTIVE ad

Phases:
  walk    walk the feed from the first page changed in the last --since-days (default 365) to the end
  entries fetch /api/v1/feedentry/{uuid} for every ad whose latest status is ACTIVE
  pack    write data/cache/nav-jobs.jsonl.gz (+ data/cache/nav-jobs.meta.json)
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent import nav_jobs  # noqa: E402

WORK = ROOT / "data" / "cache-work"
OUT = ROOT / "data" / "cache"
WALK = WORK / "nav-walk.jsonl"
ENTRIES = WORK / "nav-entries.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _token() -> str:
    raw = None
    for attempt in range(5):
        raw, _ = nav_jobs._get(f"{nav_jobs.FEED_BASE}/api/publicToken", 30, "text/plain", channel="feed")
        if raw:
            break
        time.sleep(10 * (attempt + 1))
    if not raw:
        raise SystemExit("could not fetch NAV public token")
    return raw.decode("utf-8", "replace").strip().splitlines()[-1].strip()


def _read_jsonl(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line cut off by an interrupted run


def walk(max_pages: int | None, since_days: int | None) -> None:
    pages = list(_read_jsonl(WALK))
    if pages and not pages[-1].get("next_url"):
        # Top up a finished walk: re-read its last page (it may have grown and gained
        # a next_url) and follow the feed to its current end. Later lines win in
        # latest_status(), so re-reading a page is safe.
        print(f"walk was complete ({len(pages)} pages); topping up from {pages[-1]['page_url']}")
        url = nav_jobs.FEED_BASE + pages[-1]["page_url"]
    elif pages:
        url = nav_jobs.FEED_BASE + pages[-1]["next_url"]
    else:
        url = f"{nav_jobs.FEED_BASE}/api/v1/feed"
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    if not pages and since_days:
        # The feed starts with NAV's June 2023 bulk import (thousands of pages of
        # long-closed ads). Start at the first page changed in the window instead.
        since = datetime.fromtimestamp(time.time() - since_days * 86400, tz=timezone.utc)
        headers["If-Modified-Since"] = since.strftime("%a, %d %b %Y %H:%M:%S GMT")
        (WORK / "nav-walk.since.json").write_text(json.dumps({"since_days": since_days, "since": since.isoformat()}) + "\n")
    done = 0
    started = time.monotonic()
    with WALK.open("a", encoding="utf-8") as fh:
        while url and (max_pages is None or done < max_pages):
            body = None
            for attempt in range(4):
                body, _ = nav_jobs._get(url, 60, "application/json", channel="feed", headers=headers)
                if body:
                    break
                time.sleep(5 * (attempt + 1))
                if attempt == 1:
                    headers["Authorization"] = f"Bearer {_token()}"  # token may have rotated
            if not body:
                raise SystemExit(f"feed page failed repeatedly: {url}")
            page = json.loads(body)
            items = []
            for item in page.get("items") or []:
                fe = item.get("_feed_entry") or {}
                uuid = fe.get("uuid") or item.get("id")
                if uuid:
                    items.append({
                        "uuid": uuid,
                        "status": fe.get("status"),
                        "businessName": fe.get("businessName"),
                        "title": fe.get("title") or item.get("title"),
                        "modified": fe.get("sistEndret") or item.get("date_modified"),
                    })
            nxt = page.get("next_url")
            fh.write(json.dumps({"page_url": url[len(nav_jobs.FEED_BASE):], "page_id": page.get("id"), "next_url": nxt, "items": items}, ensure_ascii=False) + "\n")
            fh.flush()
            headers.pop("If-Modified-Since", None)
            done += 1
            if done % 20 == 0:
                last = items[-1]["modified"] if items else "?"
                print(f"walk: {len(pages) + done} pages, last modified {last}, {time.monotonic() - started:.0f}s", flush=True)
            url = nav_jobs.FEED_BASE + nxt if nxt else None
    print(f"walk: wrote {done} pages; complete={url is None}")


def latest_status() -> tuple[dict[str, dict], dict]:
    latest: dict[str, dict] = {}
    last_page = None
    for page in _read_jsonl(WALK):
        last_page = page
        for item in page["items"]:
            latest[item["uuid"]] = item  # feed is ordered by modification time: later wins
    return latest, (last_page or {})


def entries(workers: int) -> None:
    latest, _ = latest_status()
    active = sorted(u for u, e in latest.items() if e.get("status") == "ACTIVE")
    have = {e["uuid"] for e in _read_jsonl(ENTRIES)}
    todo = [u for u in active if u not in have]
    print(f"entries: {len(active)} active ads, {len(have)} already fetched, {len(todo)} to fetch")
    token = {"value": _token()}

    def one(uuid: str):
        for attempt in range(3):
            try:
                raw, _ = nav_jobs._get(f"{nav_jobs.FEED_BASE}/api/v1/feedentry/{uuid}", 30, "application/json",
                                       channel="feed", headers={"Authorization": f"Bearer {token['value']}"})
            except nav_jobs.NavThrottled:
                time.sleep(10)
                continue
            if raw:
                return uuid, raw
            time.sleep(2 * (attempt + 1))
        return uuid, None

    started = time.monotonic()
    n = 0
    with ENTRIES.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=workers) as pool:
        for uuid, raw in pool.map(one, todo):
            n += 1
            row = {"uuid": uuid, "fetched_at": _now()}
            if raw:
                parsed = nav_jobs.parse_feed_entry(raw)
                row["sha256"] = hashlib.sha256(raw).hexdigest()
                if parsed:
                    ad, emp = parsed["ad"], parsed["employer"]
                    row.update({
                        "status": ad.get("status"),
                        "employer_orgnr": re.sub(r"\D", "", str(emp.get("orgnr") or "")),
                        "employer_name": emp.get("name"),
                        "homepage": emp.get("homepage"),
                        "title": ad.get("title"),
                        "published": (ad.get("published") or "")[:10] or None,
                        "expires": (ad.get("expires") or "")[:10] or None,
                    })
                else:
                    row["status"] = "NO_EMPLOYER"
            else:
                row["status"] = "FETCH_FAILED"
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            if n % 500 == 0:
                fh.flush()
                print(f"entries: {n}/{len(todo)} in {time.monotonic() - started:.0f}s", flush=True)
    print(f"entries: fetched {n}")


def _subunit_parents() -> dict[str, str]:
    path = WORK / "brreg-subunits.jsonl.gz"  # from scripts/cache/fetch_brreg_bulk.py
    if not path.exists():
        print("warning: brreg-subunits.jsonl.gz missing; sub-unit ads stay under the sub-unit org number")
    parents: dict[str, str] = {}
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("parent"):
                    parents[row["org"]] = row["parent"]
    return parents


def pack() -> None:
    latest, last_page = latest_status()
    if last_page.get("next_url"):
        raise SystemExit("walk is not complete; run the walk phase first")
    parents = _subunit_parents()
    today = datetime.now(timezone.utc).date().isoformat()
    rows: dict[tuple[str, str], dict] = {}
    for e in _read_jsonl(ENTRIES):
        if e.get("status") != "ACTIVE" or len(e.get("employer_orgnr") or "") != 9:
            continue
        if (latest.get(e["uuid"]) or {}).get("status") != "ACTIVE":
            continue
        if e.get("expires") and e["expires"] < today:
            continue
        emp = e["employer_orgnr"]
        org = parents.get(emp, emp)
        rows[(org, e["uuid"])] = {
            "org": org,
            "employer_orgnr": emp,
            "uuid": e["uuid"],
            "title": (e.get("title") or "")[:200],
            "employer_name": e.get("employer_name"),
            "homepage": e.get("homepage"),
            "published": e.get("published"),
            "expires": e.get("expires"),
            "modified": (latest.get(e["uuid"]) or {}).get("modified"),
        }
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "nav-jobs.jsonl.gz"
    ordered = sorted(rows.values(), key=lambda r: (r["org"], r["uuid"]))
    with gzip.GzipFile(out, "wb", mtime=0) as gz:  # mtime=0: same content, same bytes
        for row in ordered:
            gz.write((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    meta = {
        "source": "https://pam-stilling-feed.nav.no/api/v1/feed (NAV public token)",
        "built_at": _now(),
        "cursor_page_url": last_page["page_url"],
        "window": json.loads((WORK / "nav-walk.since.json").read_text()) if (WORK / "nav-walk.since.json").exists() else "full feed history",
        "last_modified_seen": max((e.get("modified") or "" for e in latest.values()), default=None),
        "active_ads": len(ordered),
        "companies": len({r["org"] for r in ordered}),
        "subunit_ads_mapped_to_parent": sum(1 for r in ordered if r["org"] != r["employer_orgnr"]),
    }
    (OUT / "nav-jobs.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["walk", "entries", "pack", "all"])
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--since-days", type=int, default=365,
                    help="fresh walk starts at ads changed in the last N days (0 = full history from June 2023)")
    args = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    if args.phase in ("walk", "all"):
        walk(args.max_pages, args.since_days or None)
    if args.phase in ("entries", "all"):
        entries(args.workers)
    if args.phase in ("pack", "all"):
        pack()


if __name__ == "__main__":
    main()
