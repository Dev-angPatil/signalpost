"""Last-step publication gates for company-site news and social links.

These run after identity verification. They drop claims that are attached to
the right website but are still wrong as claims: pages that are not dated news
(products, staff pages, theme demo posts), stale items, content from another
country's section of a group site, and social accounts that are not this
company's own profile.
"""
from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

NEWS_MAX_AGE_DAYS = 730
BULK_SAME_DAY = 3
MIN_TITLE_WORDS = 3
# Newspapers, broadcasters and news agencies: articles on the site are their product, not news about them.
MEDIA_NACE_PREFIXES = ("58.1", "60.", "63.91")
COUNTRY_PATH = re.compile(r"^/(?:no|nb|nn|nb-no|no-no|norge|norway)(?:/|$)", re.I)
COUNTRY_MARK = re.compile(r"norge|norway|norsk")
LOCAL_SUFFIX = re.compile(r"[._-]no$")
LEGAL_FORM_SUFFIX = re.compile(r"(?:^|[._-]|[a-z])as[a]?$")
# Evergreen guides and SEO landing pages carry a date but are not news about the company.
EVERGREEN_TITLE = re.compile(
    r"(?i)\?\s*$|^(?:hva|hvordan|hvorfor|når|slik|how|what|why|when)\b|\bslik\s+\w+r\s+du\b|alt du trenger å vite"
    r"|\bforklart\b|\bguide[n]?\b|\btips\b|\s\|\s|\bspørsmål\b|\bfaq\b"
)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def country_section(site_url: str) -> str:
    """Return '/no/' style prefix when the verified site is one country's section of a larger site."""
    path = urllib.parse.urlsplit(site_url or "").path or "/"
    match = COUNTRY_PATH.match(path)
    return "/" + path.strip("/").split("/")[0].lower() + "/" if match else ""


def filter_site_news(items: list[dict[str, Any]], *, site_url: str, nace: str | None = None, today: date | None = None) -> list[dict[str, Any]]:
    today = today or _today()
    if str(nace or "").startswith(MEDIA_NACE_PREFIXES):
        return []
    section = country_section(site_url)
    per_day = Counter(str(i.get("date"))[:10] for i in items)
    kept = []
    for item in items:
        day = str(item.get("date"))[:10]
        try:
            age = (today - date.fromisoformat(day)).days
        except ValueError:
            continue
        if age < -1 or age > NEWS_MAX_AGE_DAYS:
            continue
        if per_day[day] >= BULK_SAME_DAY:
            continue
        if sum(1 for w in str(item.get("title") or "").split() if any(ch.isalpha() for ch in w)) < MIN_TITLE_WORDS:
            continue
        if EVERGREEN_TITLE.search(str(item.get("title") or "")):
            continue
        if section and not ((urllib.parse.urlsplit(str(item.get("url"))).path or "/").lower() + "/").startswith(section):
            continue
        kept.append(item)
    return kept


def social_handle(platform: str, url: str) -> str | None:
    """Return the account handle if the URL is an account's own profile page, else None."""
    parts = urllib.parse.urlsplit(url or "")
    segs = [urllib.parse.unquote(s) for s in parts.path.split("/") if s]
    platform = (platform or "").lower()
    if not segs:
        return None
    if platform == "facebook":
        if segs[0] == "profile.php":
            return parts.query.lower() or None
        if segs[0] in {"p", "people", "pages"} and len(segs) in (2, 3):
            return segs[1].lower()
        reserved = {"sharer", "share", "watch", "groups", "events", "photo", "photos", "videos", "login", "dialog", "hashtag", "story.php", "sharer.php"}
        return segs[0].lower() if len(segs) == 1 and segs[0].lower() not in reserved and not segs[0].endswith(".php") else None
    if platform == "linkedin":
        return segs[1].lower() if len(segs) == 2 and segs[0] in {"company", "school", "showcase"} else None
    if platform == "youtube":
        if len(segs) == 1 and segs[0].startswith("@"):
            return segs[0][1:].lower()
        return segs[1].lower() if len(segs) == 2 and segs[0] in {"channel", "c", "user"} else None
    if platform == "tiktok":
        return segs[0][1:].lower() if len(segs) == 1 and segs[0].startswith("@") else None
    if platform in {"instagram", "x", "twitter"}:
        return segs[0].lower() if len(segs) == 1 and segs[0] not in {"p", "reel", "explore", "share", "intent", "home"} else None
    return segs[-1].lower()


def gate_social_links(links: list[dict[str, Any]], *, site_url: str, legal_name: str = "") -> list[dict[str, Any]]:
    """Keep one own-profile link per platform; drop group or global-brand accounts."""
    host = (urllib.parse.urlsplit(site_url or "").hostname or "").lower()
    # A country page of a multinational's site links the group's accounts: keep
    # only handles that name Norway or the Norwegian legal entity ("firma-as").
    foreign_section = bool(country_section(site_url)) and not host.endswith(".no")
    shaped = []
    seen: set[str] = set()
    for link in links or []:
        platform = str(link.get("platform") or "").lower()
        handle = social_handle(platform, str(link.get("url") or ""))
        if not platform or not handle or platform in seen:
            continue
        seen.add(platform)
        shaped.append((link, handle))
    # A brand that runs Norway-specific accounts also has global ones; the bare
    # brand handle then belongs to the group, not to this Norwegian company.
    if not COUNTRY_MARK.search(legal_name.casefold()) and any(COUNTRY_MARK.search(h) for _, h in shaped):
        shaped = [(l, h) for l, h in shaped if COUNTRY_MARK.search(h) or LOCAL_SUFFIX.search(h)]
    if foreign_section:
        shaped = [(l, h) for l, h in shaped if COUNTRY_MARK.search(h) or LOCAL_SUFFIX.search(h) or LEGAL_FORM_SUFFIX.search(h)]
    return [l for l, _ in shaped]


def apply_publication_gates(profile: dict[str, Any]) -> None:
    """Gate the verified site's news and social links in place, at the source.

    The summary, the envelope claims, the observations and the company browser
    all read these two lists, so gating them once keeps every surface
    consistent. Job-like posts are left alone: the job checks own those.
    Safe to call repeatedly.
    """
    web = (profile.get("evidence") or {}).get("website") or {}
    value = web.get("value")
    if not isinstance(value, dict) or not (value.get("identity_assessment") or {}).get("publishable"):
        return
    from .website import is_site_job
    site_url = str(value.get("final_url") or web.get("source_url") or "")
    items = [i for i in (value.get("news_items") or []) if isinstance(i, dict)]
    if items:
        jobs = [i for i in items if is_site_job(i)]
        news = [i for i in items if not is_site_job(i) and i.get("title") and i.get("url")]
        value["news_items"] = jobs + filter_site_news(news, site_url=site_url, nace=profile.get("industry_code") or profile.get("nace_code"))
    links = [l for l in (value.get("social_links") or []) if isinstance(l, dict)]
    if links:
        value["social_links"] = gate_social_links(links, site_url=site_url, legal_name=str(profile.get("name") or ""))
