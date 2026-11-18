"""Open job postings from NAV arbeidsplassen, tied to the exact organisation number.

arbeidsplassen.nav.no is the Norwegian Labour and Welfare Administration's
public job board (robots.txt: ``Disallow:`` empty, i.e. crawling permitted).
Search hits are candidates only. Each candidate ad page is fetched and its
embedded ``employer.orgnr`` must equal the company's organisation number or
one of its registered sub-units before anything is published.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from norway_company_agent import cache as cache_module

SEARCH_URL = "https://arbeidsplassen.nav.no/stillinger/api/search"
AD_URL = "https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"
LEGAL_SUFFIXES = {"as", "asa", "ans", "da", "sa", "ba", "nuf", "enk", "iks", "ks", "brl", "borettslag", "stiftelsen", "stiftelse"}
MAX_AD_FETCHES = 5


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fold_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().casefold()
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def _core_tokens(name: str) -> list[str]:
    return [t for t in _fold_tokens(name) if t not in LEGAL_SUFFIXES]


_RATE_LOCK = threading.Lock()
_LAST_REQUEST = {"search": 0.0, "page": 0.0, "feed": 0.0}
_BLOCKED_UNTIL = {"search": 0.0, "page": 0.0, "feed": 0.0}
MIN_INTERVAL_S = float(os.environ.get("NAV_MIN_INTERVAL_S", "0.6"))


class NavThrottled(Exception):
    pass


def _get(url: str, timeout: float, accept: str, channel: str = "page", headers: dict[str, str] | None = None) -> tuple[bytes | None, int]:
    """Polite, globally rate-limited GET shared by all worker threads."""
    requests = 0
    for attempt in range(2):
        with _RATE_LOCK:
            now = time.monotonic()
            if now < _BLOCKED_UNTIL[channel] and attempt == 0 and _BLOCKED_UNTIL[channel] - now > 30:
                raise NavThrottled("NAV rate limit cool-down in effect")
            interval = 0.0 if channel == "feed" else MIN_INTERVAL_S
            wait = max(_LAST_REQUEST[channel] + interval, _BLOCKED_UNTIL[channel]) - now
            if wait > 0:
                time.sleep(min(wait, 30))
            _LAST_REQUEST[channel] = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept, **(headers or {})})
        requests += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(8_000_000), requests
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    retry_after = float(exc.headers.get("Retry-After") or 0)
                except ValueError:
                    retry_after = 0
                backoff = max(retry_after, 3.0 * (2 ** attempt))
                with _RATE_LOCK:
                    _BLOCKED_UNTIL[channel] = max(_BLOCKED_UNTIL[channel], time.monotonic() + backoff)
                continue
            return None, requests
        except Exception:
            return None, requests
    raise NavThrottled("NAV returned HTTP 429 after retries")


_SEARCH_FAILURES = [0]
SEARCH_FAILURE_LIMIT = 5


def _search(query: str, timeout: float) -> tuple[list[dict[str, Any]], int]:
    if _SEARCH_FAILURES[0] >= SEARCH_FAILURE_LIMIT:
        raise NavThrottled("NAV search unreachable in this run (circuit open)")
    url = f"{SEARCH_URL}?{urllib.parse.urlencode({'q': query, 'size': 25})}"
    try:
        raw, n = _get(url, timeout, "application/json", channel="search")
    except NavThrottled:
        _SEARCH_FAILURES[0] += 1
        raise
    if not raw:
        _SEARCH_FAILURES[0] += 1
        return [], n
    _SEARCH_FAILURES[0] = 0
    try:
        hits = json.loads(raw).get("hits", {}).get("hits", [])
    except Exception:
        return [], n
    return [h.get("_source") or {} for h in hits if isinstance(h, dict)], n


def _extract_json_object(text: str, start: int) -> str | None:
    depth, in_str, esc = 0, False, False
    for i in range(start, min(len(text), start + 400_000)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_ad_page(html: str) -> dict[str, Any] | None:
    """Pull adData and employer out of the Next.js flight payload."""
    text = html.replace('\\"', '"')
    ad: dict[str, Any] = {}
    i = text.find('"adData":{')
    if i >= 0:
        for key in ("id", "status", "title", "published", "expires", "jobTitle", "sourceUrl", "engagementType"):
            m = re.compile(r'"%s":"((?:[^"\\]|\\.)*)"' % key).search(text, i, i + 6000)
            if m:
                ad[key] = m.group(1)
        m = re.compile(r'"applicationUrl":"((?:[^"\\]|\\.)*)"').search(text, i, i + 6000)
        if m:
            ad["applicationUrl"] = m.group(1)
    j = text.find('"employer":{"orgnr"')
    if j < 0:
        return None
    emp: dict[str, Any] = {}
    for key in ("orgnr", "name", "homepage", "linkedinPage", "facebookPage", "twitterAddress"):
        m = re.compile(r'"%s":(?:"((?:[^"\\]|\\.)*)"|null)' % key).search(text, j, j + 4000)
        if m and m.group(1):
            emp[key] = m.group(1)
    loc = re.compile(r'"location(?:List)?":\[\{[^\]]*?"city":"([^"]*)"').search(text, i if i >= 0 else 0)
    if loc:
        ad["city"] = loc.group(1)
    return {"ad": ad, "employer": emp}


FEED_BASE = "https://pam-stilling-feed.nav.no"
FEED_DAYS = int(os.environ.get("NAV_FEED_DAYS", "45"))
FEED_MAX_SECONDS = float(os.environ.get("NAV_FEED_MAX_SECONDS", "720"))
_FEED_LOCK = threading.Lock()
_FEED_STATE: dict[str, Any] = {"built": False, "index": None}


def _build_feed_index(days: int, timeout: float = 30.0, *, start_path: str | None = None, since_label: str | None = None) -> dict[str, Any] | None:
    """Walk NAV's official job-vacancy feed from ``days`` ago; keep ads still ACTIVE.

    With ``start_path`` (a feed page URL path saved in the declared cache) the
    walk starts at that page instead and covers only changes since the cache
    was built.

    The feed is NAV's documented bulk channel and its bearer token is published
    openly at /api/publicToken (no personal credential is involved).
    """
    started = time.monotonic()
    raw, _ = _get(f"{FEED_BASE}/api/publicToken", timeout, "text/plain", channel="feed")
    if not raw:
        return None
    token = raw.decode("utf-8", errors="replace").strip().splitlines()[-1].strip()
    if start_path:
        since = since_label or f"cache cursor {start_path}"
        url: str | None = FEED_BASE + start_path
        headers = {"Authorization": f"Bearer {token}"}
    else:
        since = datetime.fromtimestamp(time.time() - days * 86400, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        url = f"{FEED_BASE}/api/v1/feed"
        headers = {"Authorization": f"Bearer {token}", "If-Modified-Since": since}
    entries: dict[str, dict[str, Any]] = {}
    pages = 0
    requests = 1
    complete = False
    while url and time.monotonic() - started < FEED_MAX_SECONDS:
        body, n = _get(url, timeout, "application/json", channel="feed", headers=headers)
        requests += n
        if not body:
            break
        try:
            page = json.loads(body)
        except Exception:
            break
        pages += 1
        headers.pop("If-Modified-Since", None)
        for item in page.get("items") or []:
            fe = item.get("_feed_entry") or {}
            uuid = fe.get("uuid") or item.get("id")
            if uuid:
                entries[uuid] = {
                    "uuid": uuid,
                    "status": fe.get("status"),
                    "title": fe.get("title") or item.get("title"),
                    "businessName": fe.get("businessName"),
                    "municipal": fe.get("municipal"),
                    "modified": fe.get("sistEndret") or item.get("date_modified"),
                }
        nxt = page.get("next_url")
        if not nxt:
            complete = True
            break
        url = FEED_BASE + nxt
    if pages == 0:
        return None  # feed unreachable (token rotated, 5xx, timeout): caller falls back to search
    active = {u: e for u, e in entries.items() if str(e.get("status")) == "ACTIVE"}
    token_index: dict[str, set[str]] = {}
    for uuid, entry in active.items():
        for tok in set(_fold_tokens(entry.get("businessName") or "")):
            token_index.setdefault(tok, set()).add(uuid)
    return {
        "entries": active,
        "statuses": {u: str(e.get("status")) for u, e in entries.items()},
        "tokens": token_index,
        "pages": pages,
        "requests": requests,
        "complete": complete,
        "since": since,
        "token": token,
        "built_at": _utc_now(),
        "seconds": round(time.monotonic() - started, 1),
        "started_monotonic": started,
    }


PREFETCH_MAX_FEED_DF = 25
PREFETCH_MAX_FEED_DF_INCREMENTAL = int(os.environ.get("NAV_PREFETCH_MAX_DF_INCREMENTAL", "200"))
MAX_CACHED_AD_FETCHES = int(os.environ.get("NAV_MAX_CACHED_AD_FETCHES", "10"))


def _declared_cache():
    """The declared cache when it holds a usable NAV vacancy index, else None."""
    try:
        cache = cache_module.get_cache()
    except Exception:
        return None
    return cache if cache is not None and cache.nav_available() else None

PREFETCH_WORKERS = int(os.environ.get("NAV_PREFETCH_WORKERS", "4"))
PREFETCH_MAX_ENTRIES = int(os.environ.get("NAV_PREFETCH_MAX_ENTRIES", "4000"))
PREFETCH_EXTRA_SECONDS = float(os.environ.get("NAV_PREFETCH_EXTRA_SECONDS", "420"))


def _prefetch_by_orgnr(index: dict[str, Any], batch_names: list[str], max_df: int = PREFETCH_MAX_FEED_DF) -> None:
    """Fetch feed entries whose employer shares a distinctive word with the batch.

    The resulting map is keyed by the employer organisation number from the
    official feed entry, so matching afterwards is exact and name-independent.
    """
    from concurrent.futures import ThreadPoolExecutor

    tokens = {t for name in batch_names for t in _core_tokens(name) if len(t) >= 3}
    uuids: set[str] = set()
    for tok in tokens:
        bucket = index["tokens"].get(tok) or set()
        if 0 < len(bucket) <= max_df:
            uuids |= bucket
    uuids_list = sorted(uuids)[:PREFETCH_MAX_ENTRIES]
    by_org: dict[str, list[dict[str, Any]]] = {}

    deadline = float(index.get("started_monotonic") or time.monotonic()) + FEED_MAX_SECONDS + PREFETCH_EXTRA_SECONDS
    index["prefetch_complete"] = True

    def one(uuid: str) -> tuple[str, bytes | None]:
        if time.monotonic() > deadline:
            index["prefetch_complete"] = False
            return uuid, None
        try:
            raw, _ = _get(f"{FEED_BASE}/api/v1/feedentry/{uuid}", 8.0, "application/json",
                          channel="feed", headers={"Authorization": f"Bearer {index['token']}"})
        except NavThrottled:
            raw = None
        return uuid, raw

    fetched = 0
    with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
        for uuid, raw in pool.map(one, uuids_list):
            fetched += 1
            if not raw:
                continue
            parsed = parse_feed_entry(raw)
            if not parsed:
                continue
            org = re.sub(r"\D", "", str(parsed["employer"].get("orgnr") or ""))
            if len(org) == 9:
                by_org.setdefault(org, []).append({"uuid": uuid, "parsed": parsed, "raw_sha": hashlib.sha256(raw).hexdigest()})
    index["by_org"] = by_org
    index["prefetched_uuids"] = set(uuids_list)
    index["prefetch_requests"] = fetched


def _cache_age_days(meta: dict[str, Any]) -> float:
    try:
        built = datetime.fromisoformat(str(meta.get("built_at") or "").replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - built).total_seconds() / 86400
    except ValueError:
        return float("inf")


def get_feed_index(batch_names: list[str] | None = None) -> dict[str, Any] | None:
    if FEED_DAYS <= 0:
        return None
    with _FEED_LOCK:
        if not _FEED_STATE["built"]:
            try:
                index = None
                cache = _declared_cache()
                if cache is not None:
                    meta = cache.meta(cache_module.NAV_FILE)
                    if _cache_age_days(meta) <= FEED_DAYS:
                        # Declared cache: walk only the feed changes since it was built.
                        index = _build_feed_index(
                            FEED_DAYS, start_path=meta["cursor_page_url"],
                            since_label=f"declared cache built {meta.get('built_at')} + feed changes since",
                        )
                    else:
                        # An old cache: walking from its cursor would be longer than the normal
                        # window, so walk the normal window. Cached ads are still re-checked live.
                        index = _build_feed_index(FEED_DAYS)
                    if index is not None:
                        index["cache"] = cache
                        if batch_names:
                            # The incremental set is small, so common name words are affordable.
                            _prefetch_by_orgnr(index, batch_names, max_df=PREFETCH_MAX_FEED_DF_INCREMENTAL)
                if index is None:
                    index = _build_feed_index(FEED_DAYS)
                    if index is not None and batch_names:
                        _prefetch_by_orgnr(index, batch_names)
                _FEED_STATE["index"] = index
            except Exception:
                _FEED_STATE["index"] = None
            _FEED_STATE["built"] = True
        return _FEED_STATE["index"]


def wait_for_feed_index(timeout_s: float) -> dict[str, Any] | None:
    """Wait at most timeout_s for the background build; None if it is still running."""
    end = time.monotonic() + max(0.0, timeout_s)
    while time.monotonic() < end:
        if feed_ready():
            return _FEED_STATE["index"]
        time.sleep(0.5)
    return _FEED_STATE["index"] if feed_ready() else None


def feed_ready() -> bool:
    """True once the shared feed index is built (or disabled / failed)."""
    return FEED_DAYS <= 0 or bool(_FEED_STATE["built"])


def start_feed_build(batch_names: list[str] | None = None) -> threading.Thread:
    thread = threading.Thread(target=get_feed_index, args=(batch_names,), name="nav-feed-index", daemon=True)
    thread.start()
    return thread


def _feed_candidates(index: dict[str, Any], core: list[str]) -> list[dict[str, Any]]:
    sets = [index["tokens"].get(tok, set()) for tok in set(core)]
    if not sets or any(not s for s in sets):
        return []
    uuids = set.intersection(*sets)
    rows = [index["entries"][u] for u in uuids]
    rows.sort(key=lambda r: str(r.get("modified") or ""), reverse=True)
    return rows


def _html_text(value: Any) -> str:
    from html import unescape
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", str(value or ""))
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(unescape(text).split())


def parse_feed_entry(raw: bytes) -> dict[str, Any] | None:
    """Map an official /api/v1/feedentry payload onto the ad/employer shape."""
    try:
        data = json.loads(raw)
    except Exception:
        return None
    content = data.get("ad_content") or {}
    employer = content.get("employer") or {}
    if not employer.get("orgnr"):
        return None
    locations = content.get("workLocations") or []
    city = next((loc.get("city") or loc.get("municipal") for loc in locations if isinstance(loc, dict) and (loc.get("city") or loc.get("municipal"))), None)
    link = str(content.get("link") or "")
    return {
        "ad": {
            "id": data.get("uuid"),
            "status": data.get("status"),
            "title": content.get("title"),
            "published": content.get("published"),
            "expires": content.get("expires"),
            "jobTitle": content.get("jobtitle"),
            "applicationUrl": content.get("applicationUrl"),
            "sourceUrl": content.get("sourceurl"),
            "city": city,
            "link": link if link.startswith("https://arbeidsplassen.nav.no/") else None,
            "description": _html_text(content.get("description"))[:2500],
            "engagementType": content.get("engagementtype"),
            "extent": content.get("extent"),
            "positionCount": content.get("positioncount"),
            "applicationDue": content.get("applicationDue"),
            "sector": content.get("sector"),
        },
        "employer": {k: employer.get(k) for k in ("orgnr", "name", "homepage") if employer.get(k)},
    }


def _append_posting(result: dict[str, Any], parsed: dict[str, Any], ad_url: str, sha: str, src: dict[str, Any]) -> None:
    ad = parsed["ad"]
    ad_url = ad.get("link") or ad_url
    if str(ad.get("status") or "ACTIVE") != "ACTIVE":
        return
    if any(p["url"] == ad_url for p in result["postings"]):
        return
    title = (ad.get("title") or src.get("title") or "").strip()
    if not title:
        return
    published = (ad.get("published") or src.get("published") or "")[:10] or None
    expires = (ad.get("expires") or src.get("expires") or "")[:10] or None
    if expires and expires < datetime.now(timezone.utc).date().isoformat():
        return
    emp_org = re.sub(r"\D", "", str(parsed["employer"].get("orgnr") or ""))
    city = ad.get("city") or next((loc.get("city") for loc in src.get("locationList") or [] if loc.get("city")), None) or (src.get("municipal") if src.get("municipal") not in (None, "?") else None)
    result["postings"].append({
        "title": title[:200],
        "url": ad_url,
        "published": published,
        "expires": expires,
        "location": city,
        "job_title": ad.get("jobTitle") or (src.get("properties") or {}).get("jobtitle"),
        "application_url": ad.get("applicationUrl") or None,
        "original_source_url": ad.get("sourceUrl") or None,
        "employer_orgnr": emp_org,
        "employer_name": parsed["employer"].get("name"),
        "description_excerpt": ad.get("description") or None,
        "engagement_type": ad.get("engagementType") or None,
        "extent": ad.get("extent") or None,
        "position_count": ad.get("positionCount") or None,
        "application_due": ad.get("applicationDue") or None,
        "content_sha256": sha,
        "retrieved_at": _utc_now(),
        "claim_span": f"employer.orgnr={emp_org} ({parsed['employer'].get('name')}); title={title[:120]}; published={published}",
    })
    if result["employer"] is None:
        result["employer"] = {**parsed["employer"], "source_url": ad_url, "content_sha256": sha}


BRREG_SUBUNIT_URL = "https://data.brreg.no/enhetsregisteret/api/underenheter/{orgnr}"
_SUBUNIT_PARENTS: dict[str, str | None] = {}


def _live_subunit_parent(subunit: str, timeout: float) -> tuple[str | None, int]:
    """Parent org number of an active Brreg sub-unit right now (None if closed or unknown)."""
    if subunit in _SUBUNIT_PARENTS:
        return _SUBUNIT_PARENTS[subunit], 0
    parent = None
    req = urllib.request.Request(BRREG_SUBUNIT_URL.format(orgnr=subunit), headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read(1_000_000))
        if not data.get("slettedato") and not data.get("nedleggelsesdato"):
            parent = re.sub(r"\D", "", str(data.get("overordnetEnhet") or "")) or None
    except Exception:
        parent = None
    _SUBUNIT_PARENTS[subunit] = parent
    return parent, 1


def _confirm_cached_ads(
    index: dict[str, Any], org: str, accepted_orgs: set[str], skip: set[str], result: dict[str, Any], timeout: float,
) -> list[dict[str, Any]]:
    """Re-fetch this company's ads from the declared cache; keep those still ACTIVE for this employer.

    The cache only says where to look. Each ad is fetched live from the official
    feed, so the published claim's hash and retrieval time come from this run.
    """
    cache = index["cache"]
    statuses = index.get("statuses") or {}
    today = datetime.now(timezone.utc).date().isoformat()
    confirmed: list[dict[str, Any]] = []
    checked = 0
    for row in cache.nav_ads_for(accepted_orgs):
        if checked >= MAX_CACHED_AD_FETCHES:
            break
        uuid = row["uuid"]
        if uuid in skip or statuses.get(uuid, "ACTIVE") != "ACTIVE":
            continue  # already matched, or closed since the cache was built
        if row.get("expires") and row["expires"] < today:
            continue
        checked += 1
        try:
            raw, n = _get(f"{FEED_BASE}/api/v1/feedentry/{uuid}", timeout, "application/json",
                          channel="feed", headers={"Authorization": f"Bearer {index['token']}"})
        except NavThrottled:
            result["note"] = "NAV rate limited while re-checking cached ads; remaining ads skipped"
            break
        result["requests"] += n
        cache.count("nav_cached_ads_checked")
        parsed = parse_feed_entry(raw) if raw else None
        emp_org = re.sub(r"\D", "", str((parsed or {}).get("employer", {}).get("orgnr") or ""))
        allowed = set(accepted_orgs)
        if (parsed and emp_org and emp_org not in allowed and row.get("org") == org and emp_org == row.get("employer_orgnr")):
            # The cache mapped this sub-unit to the company from the Brreg bulk file at build
            # time. Sub-units can move or close, so the link is re-checked live in Brreg.
            parent, n = _live_subunit_parent(emp_org, timeout)
            result["requests"] += n
            if parent == org:
                allowed.add(emp_org)
        if not parsed or emp_org not in allowed or str(parsed["ad"].get("status") or "") != "ACTIVE":
            cache.count("nav_cached_ads_rejected")
            result["rejected"].append({"uuid": uuid, "employer_orgnr": emp_org or None, "reason": "cached ad no longer active for this employer"})
            continue
        cache.count("nav_cached_ads_confirmed")
        confirmed.append({"uuid": uuid, "parsed": parsed, "raw_sha": hashlib.sha256(raw).hexdigest()})
    return confirmed


def fetch_nav_job_postings(
    profile: dict[str, Any],
    subunit_orgs: set[str] | None = None,
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    started = time.monotonic()
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    name = str(profile.get("name") or "")
    accepted_orgs = {org} | {re.sub(r"\D", "", s) for s in (subunit_orgs or set())}
    core = _core_tokens(name)
    result: dict[str, Any] = {
        "status": "not_available",
        "source_url": f"{SEARCH_URL}?q={urllib.parse.quote(' '.join(core))}",
        "retrieved_at": _utc_now(),
        "postings": [],
        "employer": None,
        "rejected": [],
        "requests": 0,
    }
    if len(org) != 9 or not core or (len(core) == 1 and len(core[0]) < 3):
        result["status"] = "not_applicable"
        return result

    index = get_feed_index()
    core_set = set(core)
    candidates = []
    exact_entries: list[dict[str, Any]] = []
    if index is not None:
        for acc in sorted(accepted_orgs):
            exact_entries.extend((index.get("by_org") or {}).get(acc, []))
        prefetched = index.get("prefetched_uuids") or set()
        candidates = [c for c in _feed_candidates(index, core) if c["uuid"] not in prefetched]
        result["source_url"] = f"{FEED_BASE}/api/v1/feed (active ads modified since {index['since']})"
        hits = []
    else:
        try:
            hits, n = _search(" ".join(core), timeout)
        except NavThrottled as exc:
            result["status"] = "blocked"
            result["note"] = str(exc)
            result["requests"] += 1
            return result
        result["requests"] += n
    for src in hits:
        names = " ".join(str(x or "") for x in (src.get("businessName"), (src.get("employer") or {}).get("name"), (src.get("properties") or {}).get("employer")))
        if core_set.issubset(set(_fold_tokens(names))) and src.get("uuid") and str(src.get("status") or "ACTIVE") == "ACTIVE":
            candidates.append(src)
    if index is not None and index.get("cache") is not None:
        exact_entries.extend(_confirm_cached_ads(index, org, accepted_orgs, {ex["uuid"] for ex in exact_entries}, result, timeout))
    for ex in exact_entries:
        _append_posting(result, ex["parsed"], AD_URL.format(uuid=ex["uuid"]), ex["raw_sha"], {})
    fetched = 0
    for src in candidates:
        if fetched >= MAX_AD_FETCHES:
            break
        fetched += 1
        ad_url = AD_URL.format(uuid=src["uuid"])
        try:
            if index is not None:
                raw, n = _get(f"{FEED_BASE}/api/v1/feedentry/{src['uuid']}", timeout, "application/json",
                              channel="feed", headers={"Authorization": f"Bearer {index['token']}"})
            else:
                raw, n = _get(ad_url, timeout, "text/html")
        except NavThrottled:
            result["note"] = "NAV rate limited during ad verification; remaining candidates skipped"
            break
        result["requests"] += n
        if not raw:
            continue
        parsed = parse_feed_entry(raw) if index is not None else parse_ad_page(raw.decode("utf-8", errors="replace"))
        if not parsed:
            continue
        emp_org = re.sub(r"\D", "", str(parsed["employer"].get("orgnr") or ""))
        if emp_org not in accepted_orgs:
            result["rejected"].append({"uuid": src["uuid"], "employer_orgnr": emp_org, "reason": "employer orgnr differs"})
            continue
        _append_posting(result, parsed, ad_url, hashlib.sha256(raw).hexdigest(), src)
    if result["postings"]:
        result["status"] = "available"
        result["source_url"] = result["postings"][0]["url"]
    elif index is not None and (not index.get("complete") or index.get("prefetch_complete") is False):
        # The source was only partly checked: do not report "no open postings".
        result["status"] = "failed"
        result["note"] = "NAV vacancy feed index incomplete (time limit or feed error); postings not fully checked"
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result
