"""Google News RSS Connector & Norwegian Exact Title Matching.

Provides bounded, exact-title matching and RSS extraction for Norwegian corporate entities.
"""
from __future__ import annotations

import email.utils
import hashlib
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any


UA = "SignalpostResearchPOC/1.0 (https://builderr.ai; bounded qualification run)"
ALLOWED_PREDECESSORS = {
    "av",
    "for",
    "fra",
    "hos",
    "i",
    "med",
    "om",
    "på",
    "til",
    "og",
    "kjøper",
    "velger",
}
LEGAL_SUFFIXES = {"as", "asa", "sa", "ba", "da", "ans", "enk", "nuf", "sti"}


def exact_title_match(company_name: str, title: str, allow_suffixless: bool = True) -> bool:
    """Check if company_name matches a title according to Norwegian headline grammar rules.

    - Tokenizes company name and title (stripping publisher suffix like ' - E24').
    - Company tokens must match a contiguous slice in title tokens.
    - If match does not start at index 0, the immediate predecessor token must be in ALLOWED_PREDECESSORS.
    - Rejects matches that are part of other longer company names or arbitrary phrases.
    """
    company_tokens = re.findall(r"[a-z0-9æøå]+", str(company_name or "").casefold())
    clean_title = str(title or "")
    for sep in (" - ", " – ", " — "):
        if sep in clean_title:
            clean_title = clean_title.rsplit(sep, 1)[0]
            break

    title_tokens = re.findall(r"[a-z0-9æøå]+", clean_title.casefold())
    raw_title_tokens = re.findall(r"[a-z0-9æøåA-ZÆØÅ]+", clean_title)
    if not company_tokens or not title_tokens or len(company_tokens) > len(title_tokens):
        return False

    def sequence_matches(sequence: list[str], *, allow_initial: bool = False, require_capitalized: bool = False) -> bool:
        if not sequence or len(sequence) > len(title_tokens):
            return False
        for index in range(len(title_tokens) - len(sequence) + 1):
            if title_tokens[index : index + len(sequence)] != sequence:
                continue
            if require_capitalized and len(raw_title_tokens) == len(title_tokens):
                matched_raw = raw_title_tokens[index : index + len(sequence)]
                if not all(
                    tok[0].isupper() or tok[0].isdigit()
                    for tok in matched_raw
                    if tok.casefold() not in {"og", "i", "av", "på", "for", "til", "med"}
                ):
                    continue
            if index == 0 or title_tokens[index - 1] in ALLOWED_PREDECESSORS:
                return True
            if allow_initial and len(sequence) >= 2 and len(title_tokens[index - 1]) == 1:
                return True
        return False

    ends_with_suffix = bool(company_tokens and company_tokens[-1] in LEGAL_SUFFIXES)
    if sequence_matches(company_tokens, require_capitalized=not ends_with_suffix):
        return True

    # News headlines often omit a company's legal suffix or prefix its legal
    # name with an owner's one-letter initial. Relax only distinctive
    # multi-token names, and keep the original full-name path above unchanged.
    # allow_suffixless=False is used for small companies: many are named after
    # their owner ("STIAN KROKEN AS"), and a headline about the person or about a
    # same-named business elsewhere must not become this company's news.
    if not allow_suffixless:
        return False
    core_tokens = list(company_tokens)
    while core_tokens and core_tokens[-1] in LEGAL_SUFFIXES:
        core_tokens.pop()
    if len(core_tokens) >= 2 and sequence_matches(core_tokens, allow_initial=True, require_capitalized=True):
        return True
    return False


def _local_name(tag: Any) -> str:
    """Return an XML element's local name, including namespaced RSS feeds."""
    return str(getattr(tag, "tag", tag) or "").rsplit("}", 1)[-1].casefold()


def _child_text(element: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in list(element):
        if _local_name(child) in wanted:
            text = "".join(child.itertext()).strip()
            if text:
                return text
            href = str(child.attrib.get("href") or "").strip()
            if href:
                return href
    return ""


NEWS_FAILURE_LIMIT = 5
_NEWS_FAILURES = [0]


def fetch_company_news_rss(
    company_name: str,
    orgnr: str = "",
    years: int = 2,
    timeout: float = 8.0,
    http_fetcher: Any = None,
    _allow_core_fallback: bool = True,
    allow_suffixless: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch and filter Google News RSS articles for a Norwegian company.

    Returns:
        (evidence_record, metrics)
    """
    clean_name = str(company_name or "").strip()
    query = urllib.parse.quote(f'"{clean_name}" when:{years}y')
    url = f"https://news.google.com/rss/search?q={query}&hl=no&gl=NO&ceid=NO:no"

    if not clean_name:
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        evidence_record = {
            "field": "news",
            "status": "not_available",
            "source_type": "public_news",
            "source_class": "public_news",
            "source_url": url,
            "content_sha256": hashlib.sha256(b"").hexdigest(),
            "retrieved_at": retrieved_at,
            "value": {"items": [], "latest": None},
        }
        metrics = {
            "requests": 0,
            "bytes": 0,
            "latencies_ms": [],
            "third_party_cost_usd": 0.0,
        }
        return evidence_record, metrics

    if http_fetcher is None and _NEWS_FAILURES[0] >= NEWS_FAILURE_LIMIT:
        # Google News is refusing this run (503/429/timeouts): stop paying ~8 s per company.
        return {
            "field": "news",
            "status": "blocked",
            "note": f"Google News RSS circuit open after {NEWS_FAILURE_LIMIT} consecutive failures",
            "source_type": "public_news",
            "source_class": "public_news",
            "source_url": url,
            "content_sha256": "",
            "retrieved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "value": {"items": [], "latest": None},
        }, {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}

    headers = {
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml",
    }
    t0 = time.monotonic()
    raw = b""

    try:
        if http_fetcher is not None:
            try:
                res = http_fetcher(url, headers, timeout)
            except TypeError:
                try:
                    res = http_fetcher(url, headers=headers, timeout=timeout)
                except TypeError:
                    try:
                        res = http_fetcher(url, timeout)
                    except TypeError:
                        res = http_fetcher(url)

            if isinstance(res, (bytes, bytearray)):
                raw = bytes(res)
            elif isinstance(res, str):
                raw = res.encode("utf-8")
            elif hasattr(res, "read"):
                read_data = res.read()
                raw = read_data.encode("utf-8") if isinstance(read_data, str) else bytes(read_data)
            elif hasattr(res, "content"):
                c_data = res.content
                raw = c_data.encode("utf-8") if isinstance(c_data, str) else bytes(c_data)
            elif hasattr(res, "body"):
                b_data = res.body
                raw = b_data.encode("utf-8") if isinstance(b_data, str) else bytes(b_data)
            else:
                raw = bytes(res or b"")
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read(2_000_000)

        duration_ms = max(1, int((time.monotonic() - t0) * 1000))
        if http_fetcher is None:
            _NEWS_FAILURES[0] = 0
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        root = ET.fromstring(raw)
        items: list[dict[str, Any]] = []
        seen: set[Any] = set()

        for item_elem in root.iter():
            if _local_name(item_elem) != "item":
                continue
            title = _child_text(item_elem, "title")
            link = _child_text(item_elem, "link", "guid")
            source_elem = next((child for child in list(item_elem) if _local_name(child) == "source"), None)
            publisher = ""
            if source_elem is not None and source_elem.text:
                publisher = str(source_elem.text).strip()
            if not publisher and " - " in title:
                publisher = title.rsplit(" - ", 1)[1].strip()

            if not link or not title:
                continue

            if not exact_title_match(clean_name, title, allow_suffixless=allow_suffixless):
                continue

            dedup_key = (title.casefold(), publisher.casefold())
            if dedup_key in seen or link in seen:
                continue
            seen.add(dedup_key)
            seen.add(link)

            pub_date_raw = _child_text(item_elem, "pubdate", "date", "published", "updated")
            date_iso = None
            published_at_iso = None
            if pub_date_raw:
                try:
                    dt = email.utils.parsedate_to_datetime(pub_date_raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    date_iso = dt.strftime("%Y-%m-%d")
                    published_at_iso = dt.isoformat().replace("+00:00", "Z")
                except Exception:
                    try:
                        iso_value = pub_date_raw[:-1] + "+00:00" if pub_date_raw.endswith(("Z", "z")) else pub_date_raw
                        dt = datetime.fromisoformat(iso_value)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        else:
                            dt = dt.astimezone(timezone.utc)
                        date_iso = dt.strftime("%Y-%m-%d")
                        published_at_iso = dt.isoformat().replace("+00:00", "Z")
                    except Exception:
                        date_iso = None
                        published_at_iso = None

            if not date_iso:
                continue

            item_dict: dict[str, Any] = {
                "title": title,
                "url": link,
                "date": date_iso,
                "published_at": published_at_iso,
                "publisher": publisher,
            }
            if orgnr:
                item_dict["organisation_number"] = str(orgnr)
            items.append(item_dict)

        items.sort(key=lambda it: (str(it.get("published_at") or it.get("date") or ""), it["title"]), reverse=True)

        fallback_metrics_used: dict[str, Any] | None = None
        if not items and _allow_core_fallback and allow_suffixless:
            core_tokens = re.findall(r"[a-z0-9æøå]+", clean_name.casefold())
            while core_tokens and core_tokens[-1] in LEGAL_SUFFIXES:
                core_tokens.pop()
            if len(core_tokens) >= 2:
                fallback_record, fallback_metrics = fetch_company_news_rss(
                    " ".join(core_tokens),
                    orgnr=orgnr,
                    years=years,
                    timeout=timeout,
                    http_fetcher=http_fetcher,
                    _allow_core_fallback=False,
                )
                fallback_metrics = {
                    "requests": 1 + fallback_metrics.get("requests", 0),
                    "bytes": len(raw) + fallback_metrics.get("bytes", 0),
                    "latencies_ms": [duration_ms] + list(fallback_metrics.get("latencies_ms", [])),
                    "third_party_cost_usd": fallback_metrics.get("third_party_cost_usd", 0.0),
                }
                if fallback_record.get("status") == "available":
                    return fallback_record, fallback_metrics
                fallback_metrics_used = fallback_metrics

        evidence_record = {
            "field": "news",
            "status": "available" if items else "not_available",
            "source_type": "public_news",
            "source_class": "public_news",
            "source_url": url,
            "content_sha256": raw_sha256,
            "retrieved_at": retrieved_at,
            "value": {
                "items": items,
                "latest": items[0] if items else None,
            },
        }
        if orgnr:
            evidence_record["source_row_key"] = str(orgnr)

        metrics = {
            "requests": fallback_metrics_used.get("requests", 1) if fallback_metrics_used else 1,
            "bytes": fallback_metrics_used.get("bytes", len(raw)) if fallback_metrics_used else len(raw),
            "latencies_ms": fallback_metrics_used.get("latencies_ms", [duration_ms]) if fallback_metrics_used else [duration_ms],
            "third_party_cost_usd": fallback_metrics_used.get("third_party_cost_usd", 0.0) if fallback_metrics_used else 0.0,
        }
        return evidence_record, metrics

    except Exception as exc:
        if http_fetcher is None and not isinstance(exc, ET.ParseError):
            _NEWS_FAILURES[0] += 1
        duration_ms = max(1, int((time.monotonic() - t0) * 1000))
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        evidence_record = {
            "field": "news",
            "status": "failed",
            "error": f"{type(exc).__name__}: {str(exc)[:180]}",
            "source_type": "public_news",
            "source_class": "public_news",
            "source_url": url,
            "content_sha256": hashlib.sha256(raw).hexdigest() if raw else "",
            "retrieved_at": retrieved_at,
            "value": {"items": [], "latest": None},
        }
        if orgnr:
            evidence_record["source_row_key"] = str(orgnr)

        metrics = {
            "requests": 1,
            "bytes": len(raw) if raw else 0,
            "latencies_ms": [duration_ms],
            "third_party_cost_usd": 0.0,
        }
        return evidence_record, metrics


SERPER_NEWS_URL = "https://google.serper.dev/news"
SERPER_NEWS_FAILURE_LIMIT = 5
_SERPER_NEWS_FAILURES = [0]
_REL_DATE = re.compile(r"(?i)^\s*(\d+)\s+(minute|min|hour|day|week|month|year)s?\s+ago\s*$")


def _serper_news_date(raw: str, now: datetime | None = None) -> str | None:
    """Serper returns '3 days ago' or 'Jun 4, 2025' (hl=en). Return YYYY-MM-DD or None."""
    from datetime import timedelta

    now = now or datetime.now(timezone.utc)
    text = str(raw or "").strip()
    if not text:
        return None
    m = _REL_DATE.match(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).casefold()
        delta = {"minute": timedelta(minutes=n), "min": timedelta(minutes=n), "hour": timedelta(hours=n), "day": timedelta(days=n),
                 "week": timedelta(weeks=n), "month": timedelta(days=30 * n), "year": timedelta(days=365 * n)}[unit]
        # Month/year offsets are approximate: report only when within a week to avoid false precision.
        if unit in {"month", "year"}:
            return None
        return (now - delta).strftime("%Y-%m-%d")
    for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def fetch_serper_news(
    company_name: str,
    api_key: str,
    orgnr: str = "",
    *,
    timeout: float = 8.0,
    http_post: Any = None,
    allow_suffixless: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Google News results via Serper; same strict headline-name rule as the RSS path.

    After SERPER_NEWS_FAILURE_LIMIT consecutive failures (exhausted credits, 4xx,
    timeouts) no further calls are made in this run; the caller falls back to RSS.
    """
    import json as _json

    clean_name = str(company_name or "").strip()
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    query = f'"{clean_name}"'
    source_url = f"{SERPER_NEWS_URL}?q={urllib.parse.quote(query)}"
    empty_metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
    if http_post is None and _SERPER_NEWS_FAILURES[0] >= SERPER_NEWS_FAILURE_LIMIT:
        return {"field": "news", "status": "failed", "error": "Serper news circuit open", "source_type": "public_news",
                "source_class": "public_news", "source_url": source_url, "content_sha256": "", "retrieved_at": retrieved_at,
                "value": {"items": [], "latest": None}}, empty_metrics
    if not clean_name or not api_key:
        return {"field": "news", "status": "not_available", "source_type": "public_news", "source_class": "public_news",
                "source_url": source_url, "content_sha256": "", "retrieved_at": retrieved_at, "value": {"items": [], "latest": None}}, empty_metrics
    payload = _json.dumps({"q": query, "gl": "no", "hl": "en", "num": 20}).encode()
    t0 = time.monotonic()
    try:
        if http_post is not None:
            raw = http_post(SERPER_NEWS_URL, payload, timeout)
        else:
            req = urllib.request.Request(SERPER_NEWS_URL, data=payload, method="POST",
                                         headers={"X-API-KEY": api_key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(2_000_000)
        body = _json.loads(raw)
        if http_post is None:
            _SERPER_NEWS_FAILURES[0] = 0
    except Exception as exc:
        if http_post is None:
            _SERPER_NEWS_FAILURES[0] += 1
        return {"field": "news", "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:160]}", "source_type": "public_news",
                "source_class": "public_news", "source_url": source_url, "content_sha256": "", "retrieved_at": retrieved_at,
                "value": {"items": [], "latest": None}}, {"requests": 1, "bytes": 0, "latencies_ms": [int((time.monotonic() - t0) * 1000)], "third_party_cost_usd": 0.0}
    duration_ms = max(1, int((time.monotonic() - t0) * 1000))
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in body.get("news") or [] if isinstance(body, dict) else []:
        title = str(row.get("title") or "").strip()
        link = str(row.get("link") or "").strip()
        date_iso = _serper_news_date(str(row.get("date") or ""))
        if not (title and link.startswith(("http://", "https://")) and date_iso) or link in seen:
            continue
        if not exact_title_match(clean_name, title, allow_suffixless=allow_suffixless):
            continue
        seen.add(link)
        item = {"title": title, "url": link, "date": date_iso, "published_at": None, "publisher": str(row.get("source") or "").strip(),
                "source": "serper_news", "content_sha256": hashlib.sha256(_json.dumps(row, sort_keys=True).encode()).hexdigest()}
        if orgnr:
            item["organisation_number"] = str(orgnr)
        items.append(item)
    items.sort(key=lambda it: (it["date"], it["title"]), reverse=True)
    record = {
        "field": "news", "status": "available" if items else "not_available", "source_type": "public_news", "source_class": "public_news",
        "source_url": source_url, "content_sha256": hashlib.sha256(raw if isinstance(raw, bytes) else str(raw).encode()).hexdigest(),
        "retrieved_at": retrieved_at, "value": {"items": items[:10], "latest": items[0] if items else None},
    }
    if orgnr:
        record["source_row_key"] = str(orgnr)
    return record, {"requests": 1, "bytes": len(raw) if isinstance(raw, bytes) else 0, "latencies_ms": [duration_ms], "third_party_cost_usd": 0.001}
