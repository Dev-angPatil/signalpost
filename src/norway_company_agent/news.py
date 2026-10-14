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


def exact_title_match(company_name: str, title: str) -> bool:
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
    if not company_tokens or not title_tokens or len(company_tokens) > len(title_tokens):
        return False

    for index in range(len(title_tokens) - len(company_tokens) + 1):
        if title_tokens[index : index + len(company_tokens)] != company_tokens:
            continue
        if index == 0 or title_tokens[index - 1] in ALLOWED_PREDECESSORS:
            return True
    return False


def fetch_company_news_rss(
    company_name: str,
    orgnr: str = "",
    years: int = 2,
    timeout: float = 8.0,
    http_fetcher: Any = None,
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
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        root = ET.fromstring(raw)
        items: list[dict[str, Any]] = []
        seen: set[Any] = set()

        for item_elem in root.findall(".//item"):
            title = str(item_elem.findtext("title") or "").strip()
            link = str(item_elem.findtext("link") or "").strip()
            source_elem = item_elem.find("source")
            publisher = ""
            if source_elem is not None and source_elem.text:
                publisher = str(source_elem.text).strip()
            if not publisher and " - " in title:
                publisher = title.rsplit(" - ", 1)[1].strip()

            if not link or not title:
                continue

            if not exact_title_match(clean_name, title):
                continue

            dedup_key = (title.casefold(), publisher.casefold())
            if dedup_key in seen or link in seen:
                continue
            seen.add(dedup_key)
            seen.add(link)

            pub_date_raw = item_elem.findtext("pubDate")
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
            "requests": 1,
            "bytes": len(raw),
            "latencies_ms": [duration_ms],
            "third_party_cost_usd": 0.0,
        }
        return evidence_record, metrics

    except Exception as exc:
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
