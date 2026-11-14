"""Dated posts from a verified company site's own feeds.

Only called after the site passed the exact-identity gate. Items must stay on
the same registered domain, carry a parseable date and a real title.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any

from .website import SAFE_OPENER, USER_AGENT, _is_junk_news, _registered_domain, _robots_allowed, JOB_LIKE_TITLE

MAX_BYTES = 1_500_000


def _fetch(url: str, accept: str, timeout: float) -> tuple[bytes | None, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with SAFE_OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return None, None
            return raw, resp.geturl()
    except Exception:
        return None, None


def _clean(text: Any) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).split()).strip()


def _iso(raw: str) -> str | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except Exception:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
            return m.group(1) if m else None
    return dt.date().isoformat()


def _wp_posts(root: str, domain: str, timeout: float) -> tuple[list[dict[str, Any]], str | None, str | None]:
    url = f"{root}/wp-json/wp/v2/posts?per_page=5&_fields=link,title,date"
    raw, final = _fetch(url, "application/json", timeout)
    if not raw or not final or _registered_domain(final) != domain:
        return [], None, None
    try:
        rows = json.loads(raw)
    except Exception:
        return [], None, None
    items = []
    for row in rows if isinstance(rows, list) else []:
        title = _clean((row.get("title") or {}).get("rendered") if isinstance(row.get("title"), dict) else row.get("title"))
        items.append({"title": title, "url": row.get("link"), "date": _iso(row.get("date"))})
    return items, final, hashlib.sha256(raw).hexdigest()


def _rss(root: str, domain: str, timeout: float) -> tuple[list[dict[str, Any]], str | None, str | None]:
    raw, final = _fetch(f"{root}/feed/", "application/rss+xml, application/xml, text/xml", timeout)
    if not raw or not final or _registered_domain(final) != domain or b"<rss" not in raw[:2000] and b"<feed" not in raw[:2000]:
        return [], None, None
    try:
        tree = ET.fromstring(raw)
    except ET.ParseError:
        return [], None, None
    items = []
    for item in tree.iter():
        tag = str(item.tag).rsplit("}", 1)[-1]
        if tag not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        for child in item:
            ctag = str(child.tag).rsplit("}", 1)[-1]
            if ctag == "link" and child.get("href"):
                fields["link"] = child.get("href") or ""
            else:
                fields.setdefault(ctag, (child.text or "").strip())
        items.append({
            "title": _clean(fields.get("title")),
            "url": fields.get("link"),
            "date": _iso(fields.get("pubDate") or fields.get("published") or fields.get("updated") or ""),
        })
    return items[:10], final, hashlib.sha256(raw).hexdigest()


def fetch_site_feed_items(site_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(site_url)
    root = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    domain = _registered_domain(root)
    requests = 0
    today = datetime.now(timezone.utc).date().isoformat()
    for fetcher, path in ((_wp_posts, "/wp-json/"), (_rss, "/feed/")):
        if not _robots_allowed(root + path, timeout):
            requests += 1
            continue
        items, final, sha = fetcher(root, domain, timeout)
        requests += 1
        clean = []
        for item in items:
            url = str(item.get("url") or "")
            title = str(item.get("title") or "")
            date = item.get("date")
            if not (url.startswith("http") and len(title) >= 5 and date) or date > today:
                continue
            if _registered_domain(url) != domain or _is_junk_news(title, url, "", date):
                continue
            row = {"title": title[:200], "url": url, "date": date, "source": f"site_feed:{path.strip('/')}",
                   "feed_url": final, "content_sha256": sha}
            if JOB_LIKE_TITLE.search(title):
                row["kind"] = "job_posting"
            clean.append(row)
        if clean:
            return {"items": clean, "requests": requests}
    return {"items": [], "requests": requests}
