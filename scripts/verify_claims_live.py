#!/usr/bin/env python3
"""Re-open every externally sourced claim's page and test the claim against it.

Independent of the agent's own gates: plain HTTP fetch, plain text matching.

    python scripts/verify_claims_live.py --envelopes out/verify/env.jsonl --output out/verify/live-check.csv

Verdicts: pass, fail (page contradicts or lacks the claim), unreachable (could not fetch).
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MONTHS = {"januar": 1, "january": 1, "februar": 2, "february": 2, "mars": 3, "march": 3, "april": 4, "mai": 5, "may": 5,
          "juni": 6, "june": 6, "juli": 7, "july": 7, "august": 8, "september": 9, "oktober": 10, "october": 10,
          "november": 11, "desember": 12, "december": 12}
LEGAL_FORMS = {"as", "asa", "sa", "ans", "da", "ba", "sti", "stiftelse", "stiftelsen", "og", "i", "for", "the", "and", "&"}


@lru_cache(maxsize=None)
def fetch(url: str) -> tuple[int, str]:
    try:
        parts = urllib.parse.urlsplit(url)
        safe = urllib.parse.urlunsplit((parts.scheme, parts.netloc.encode("idna").decode(), urllib.parse.quote(parts.path, safe="/%"),
                                        urllib.parse.quote(parts.query, safe="=&%"), ""))
        req = urllib.request.Request(safe, headers={"User-Agent": UA, "Accept-Language": "nb,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, resp.read(3_000_000).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return getattr(exc, "code", 0) or 0, ""


def text_of(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).casefold()


def words(value: str) -> list[str]:
    return [w for w in re.findall(r"[0-9a-zæøåäöé]+", value.casefold()) if w not in LEGAL_FORMS]


def date_on_page(day: str, raw: str) -> bool:
    y, m, d = day[:4], int(day[5:7]), int(day[8:10])
    low = html.unescape(raw).casefold()
    if day[:10] in low:
        return True
    if re.search(rf"\b0?{d}[./]\s?0?{m}[./]\s?(?:{y}|{y[2:]})\b", low):
        return True
    names = [n for n, num in MONTHS.items() if num == m]
    short = {n[:3] for n in names}
    return any(re.search(rf"\b0?{d}\.?\s+(?:{'|'.join(names)}|{'|'.join(short)})\.?,?\s+{y}", low)
               or re.search(rf"(?:{'|'.join(names)}|{'|'.join(short)})\.?\s+0?{d}(?:st|nd|rd|th)?,?\s+{y}", low) for _ in [0])


def check(env: dict) -> list[dict]:
    claims = {c["field"]: c for c in env["claims"] if c["availability"] == "available"}
    ev = {e["id"]: e for e in env["evidence"]}
    org = env["organisation_number"]
    name = str(claims.get("legal_name", {}).get("value") or "")
    name_words = words(name)
    rows = []

    def add(family, field, url, verdict, why):
        rows.append({"org": org, "name": name, "family": family, "field": field, "url": url, "verdict": verdict, "reason": why})

    site = claims.get("official_website", {}).get("value")
    if site:
        status, raw = fetch(site)
        txt = text_of(raw)
        if not raw:
            add("website", "official_website", site, "unreachable", f"HTTP {status}")
        elif re.search(r"\s?".join(org[i:i + 3] for i in (0, 3, 6)), txt):
            add("website", "official_website", site, "pass", "organisation number on homepage")
        elif name_words and all(w in txt for w in name_words):
            add("website", "official_website", site, "pass", "all legal-name words on homepage")
        elif name_words and "".join(name_words) in re.sub(r"[^0-9a-zæøå]", "", txt + urllib.parse.urlsplit(site).netloc):
            add("website", "official_website", site, "pass", "joined legal name on homepage or in domain")
        else:
            add("website", "official_website", site, "fail", "neither organisation number nor legal name on homepage")

    for field, claim in claims.items():
        m = re.fullmatch(r"news_(\d+)_url", field)
        if m:
            i = m.group(1)
            url, title, day = str(claim["value"]), str(claims.get(f"news_{i}_title", {}).get("value") or ""), str(claims.get(f"news_{i}_date", {}).get("value") or "")
            if "news.google.com" in url:
                hit = [w for w in name_words if len(w) > 2 and w in title.casefold()]
                ok = len(hit) >= max(1, min(2, len([w for w in name_words if len(w) > 2])))
                add("news_press", field, url, "pass" if ok else "fail", f"headline carries legal-name words {hit}" if ok else f"headline lacks the company name: {title[:80]}")
                continue
            status, raw = fetch(url)
            if not raw:
                add("news_site", field, url, "unreachable", f"HTTP {status}")
                continue
            txt = text_of(raw)
            tw = [w for w in re.findall(r"\w+", title.casefold()) if len(w) > 3]
            share = sum(w in txt for w in tw) / max(1, len(tw))
            dated = date_on_page(day, raw)
            if share >= 0.7 and dated:
                add("news_site", field, url, "pass", "title and date on page")
            elif share >= 0.7:
                add("news_site", field, url, "fail", f"title on page but date {day[:10]} not found")
            else:
                add("news_site", field, url, "fail", f"title not on page ({share:.0%} of words)")
        elif field.startswith("social_"):
            url = str(claim["value"])
            src = str(ev.get((claim.get("evidence_ids") or [""])[0], {}).get("source_url") or site or "")
            handle = urllib.parse.unquote(urllib.parse.urlsplit(url).path.strip("/").split("/")[-1]).lstrip("@").casefold()
            status, raw = fetch(src) if src.startswith("http") else (0, "")
            if not raw:
                add("social", field, url, "unreachable", f"source page {src[:60]} HTTP {status}")
            elif handle and handle in urllib.parse.unquote(html.unescape(raw)).casefold():
                add("social", field, url, "pass", f"profile is linked from {src[:60]}")
            else:
                add("social", field, url, "fail", f"profile not linked from {src[:60]}")
        elif re.fullmatch(r"job_posting_\d+_url", field):
            url = str(claim["value"])
            status, raw = fetch(url)
            title = str(claims.get(field.replace("_url", "_title"), {}).get("value") or "")
            tw = [w for w in re.findall(r"\w+", title.casefold()) if len(w) > 3]
            if not raw:
                add("jobs", field, url, "unreachable", f"HTTP {status}")
            else:
                share = sum(w in text_of(raw) for w in tw) / max(1, len(tw))
                add("jobs", field, url, "pass" if share >= 0.7 else "fail", f"title words on page: {share:.0%}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--envelopes", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    envs = [json.loads(l) for l in open(args.envelopes, encoding="utf-8").read().split("\n") if l.strip()]
    with ThreadPoolExecutor(8) as pool:
        rows = [r for part in pool.map(check, envs) for r in part]
    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["org", "name", "family", "field", "url", "verdict", "reason"])
        w.writeheader()
        w.writerows(rows)
    tally = Counter((r["family"], r["verdict"]) for r in rows)
    for fam in sorted({f for f, _ in tally}):
        print(f"{fam:11} pass={tally[(fam, 'pass')]:4} fail={tally[(fam, 'fail')]:4} unreachable={tally[(fam, 'unreachable')]:4}")
    checked = sum(v for (f, k), v in tally.items() if k != "unreachable")
    print(f"total checked={checked} pass={sum(v for (f, k), v in tally.items() if k == 'pass')} "
          f"share={sum(v for (f, k), v in tally.items() if k == 'pass') / max(1, checked):.1%}")


if __name__ == "__main__":
    main()
