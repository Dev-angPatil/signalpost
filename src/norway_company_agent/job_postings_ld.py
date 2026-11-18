"""Open job postings from schema.org ``JobPosting`` data on career pages (no API key).

Many career sites and applicant-tracking systems (ATS) embed JSON-LD
``JobPosting`` objects so search engines can list their jobs. This module reads
them deterministically from a career page on the verified company site (or a
company-named ATS page linked from it) and from up to ``MAX_DETAIL_PAGES``
job-detail pages linked from that page.

A posting is published only when:
  1. the page is on the verified site's domain, or on a known ATS host;
  2. the hiring organisation is this company: on an ATS page the full legal name
     (without the legal form) must be in ``hiringOrganization.name``; on the
     company's own domain the name may be absent, the legal name, or the brand
     that is the site's own domain label;
  3. the job is in Norway (or no country is given);
  4. it is current: ``validThrough`` is today or later, or, without it,
     ``datePosted`` is within ``MAX_AGE_DAYS``.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from bs4 import BeautifulSoup

MAX_DETAIL_PAGES = 10
MAX_POSTINGS = 10
MAX_AGE_DAYS = 120
LEGAL_SUFFIXES = {"as", "asa", "ans", "da", "sa", "ba", "nuf", "enk", "iks", "ks", "sf", "norge", "norway"}
NORWAY = {"no", "nor", "norway", "norge", "noreg"}
JOB_LINK_HINT = re.compile(r"(?i)/(?:jobs?|stilling(?:er)?|ledige[-_]stillinger|karriere|career|careers|vacanc\w*|position|annonse|ad)(?:/|-|\b)")


def _fold_tokens(text: Any) -> list[str]:
    text = unicodedata.normalize("NFKD", str(text or "")).replace("ø", "o").replace("Ø", "o").replace("æ", "ae").replace("Æ", "ae")
    text = text.encode("ascii", "ignore").decode().casefold()
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def _core(name: Any) -> list[str]:
    return [t for t in _fold_tokens(name) if t not in LEGAL_SUFFIXES]


def _host(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def _registered(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _iter_objects(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _iter_objects(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                yield from _iter_objects(node[key])


def jobpostings_from_html(html: str) -> list[dict[str, Any]]:
    """Every JSON-LD object typed JobPosting in the page."""
    soup = BeautifulSoup(html or "", "lxml")
    out: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for obj in _iter_objects(data):
            types = obj.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in types:
                out.append(obj)
    return out


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _org_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("legalName") or "")
    if isinstance(value, list) and value:
        return _org_name(value[0])
    return str(value or "")


def _locations(value: Any) -> list[dict[str, str]]:
    out = []
    for loc in value if isinstance(value, list) else [value]:
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address") if isinstance(loc.get("address"), dict) else loc
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name") or country.get("identifier")
        out.append({"country": str(country or "").strip(), "locality": str(addr.get("addressLocality") or "").strip()})
    return out


def _is_ats(host: str, ats_hosts: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in ats_hosts)


def gate_posting(
    item: dict[str, Any], profile: dict[str, Any], page_url: str, site_host: str, ats_hosts: tuple[str, ...], today: date,
) -> tuple[dict[str, Any] | None, str]:
    """(posting, "") when publishable for this company, else (None, reason)."""
    title = " ".join(str(item.get("title") or item.get("name") or "").split())
    if not title or len(title) > 200:
        return None, "no title"
    page_host = _host(page_url)
    own_domain = bool(site_host) and _registered(page_host) == _registered(site_host)
    on_ats = _is_ats(page_host, ats_hosts)
    if not own_domain and not on_ats:
        return None, "page is neither on the verified site nor on a known ATS host"

    core = set(_core(profile.get("name")))
    hiring = set(_core(_org_name(item.get("hiringOrganization"))))
    if on_ats and not own_domain:
        if not core or not core <= hiring:
            return None, "ATS posting's hiringOrganization does not carry the legal name"
    elif hiring:
        brand = set(_core(site_host.split(".")[0]))
        brand_ok = bool(hiring & brand) and len("".join(hiring & brand)) >= 4
        if not (core and core <= hiring) and not brand_ok:
            return None, "hiringOrganization is another organisation"

    locations = _locations(item.get("jobLocation"))
    countries = {_fold_tokens(loc["country"])[0] for loc in locations if _fold_tokens(loc["country"])}
    if countries and not (countries & NORWAY):
        return None, "job is outside Norway"

    valid = _parse_date(item.get("validThrough"))
    posted = _parse_date(item.get("datePosted"))
    if valid is not None:
        if valid < today:
            return None, "validThrough has passed"
    elif posted is None or posted < today - timedelta(days=MAX_AGE_DAYS) or posted > today + timedelta(days=1):
        return None, "no validThrough and datePosted missing or older than the freshness window"

    url = urllib.parse.urljoin(page_url, str(item.get("url") or "")) if item.get("url") else page_url
    url_host = _host(url)
    if not ((site_host and _registered(url_host) == _registered(site_host)) or _is_ats(url_host, ats_hosts)):
        url = page_url  # never point a claim off the career site
    locality = next((loc["locality"] for loc in locations if loc["locality"]), None)
    return {
        "title": title,
        "url": url,
        "published": posted.isoformat() if posted else None,
        "expires": valid.isoformat() if valid else None,
        "location": locality,
        "hiring_organization": _org_name(item.get("hiringOrganization")) or None,
        "source": "company_career_page",
        "extraction": "schema_org_jobposting",
    }, ""


def _job_links(html: str, page_url: str, site_host: str, ats_hosts: tuple[str, ...]) -> list[str]:
    soup = BeautifulSoup(html or "", "lxml")
    page_host = _host(page_url)
    out: list[str] = []
    for a in soup.select("a[href]"):
        href = urllib.parse.urljoin(page_url, str(a.get("href") or "")).split("#")[0]
        if not href.startswith(("http://", "https://")) or href.rstrip("/") == page_url.rstrip("/"):
            continue
        host = _host(href)
        if not (host == page_host or (site_host and _registered(host) == _registered(site_host)) or _is_ats(host, ats_hosts)):
            continue
        path = urllib.parse.urlparse(href).path
        # A detail page sits below a job-like path segment and has a slug or id after it.
        if JOB_LINK_HINT.search(path) and len([s for s in path.split("/") if s]) >= 2 and href not in out:
            out.append(href)
    return out[:MAX_DETAIL_PAGES]


def extract_ld_career_postings(
    profile: dict[str, Any],
    page_url: str,
    html: str,
    content_sha256: str,
    *,
    site_host: str,
    ats_hosts: tuple[str, ...],
    fetch: Callable[[str], tuple[str | None, str | None, str | None]],
    today: date | None = None,
) -> dict[str, Any]:
    """Postings from the career page's JSON-LD, else from its job-detail pages."""
    today = today or datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    out: dict[str, Any] = {"postings": [], "rejected": [], "requests": 0}

    def take(items: list[dict[str, Any]], url: str, sha: str) -> None:
        for item in items:
            posting, reason = gate_posting(item, profile, url, site_host, ats_hosts, today)
            if not posting:
                out["rejected"].append({"title": str(item.get("title") or "")[:120], "url": url, "reason": reason})
                continue
            if any(p["url"] == posting["url"] and p["title"] == posting["title"] for p in out["postings"]):
                continue
            posting.update({
                "career_page_url": page_url,
                "content_sha256": sha or hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest(),
                "retrieved_at": now,
                "claim_span": f"schema.org JobPosting on {url}: title={posting['title']}; hiringOrganization={posting.get('hiring_organization')}; "
                              f"datePosted={posting.get('published')}; validThrough={posting.get('expires')}",
            })
            out["postings"].append(posting)

    take(jobpostings_from_html(html), page_url, content_sha256)
    if not out["postings"]:
        for link in _job_links(html, page_url, site_host, ats_hosts):
            if len(out["postings"]) >= MAX_POSTINGS:
                break
            detail_html, final_url, sha = fetch(link)
            out["requests"] += 1
            if detail_html:
                take(jobpostings_from_html(detail_html), final_url or link, sha or "")
    out["postings"] = out["postings"][:MAX_POSTINGS]
    return out
