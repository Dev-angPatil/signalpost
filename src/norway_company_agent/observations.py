"""Build publishable observations from an enriched company profile.

This module is intentionally side-effect free.  It only composes the two existing
company-site extractors over evidence already captured in the profile; it never
fetches, infers, or enriches a website.
"""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

from scripts.extract_company_site_activity import observation as site_activity_observation
from scripts.extract_company_site_news import observations as site_news_observations

from .external_footprint import publishable_observation
from .website import _registered_domain


def _exact_company_site(profile: dict[str, Any]) -> bool:
    """Return whether the profile has an available, exact company-site snapshot."""
    if not str(profile.get("organisation_number") or "").isdigit():
        return False

    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}

    # The extractor's publishable flag is the canonical identity gate.  When a
    # status is present, require the explicit exact state as an additional guard
    # against callers passing a hand-built or quarantined assessment.
    return (
        website.get("status") == "available"
        and bool(identity.get("publishable"))
        and identity.get("status", "exact") == "exact"
    )


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").casefold().removeprefix("www.")


def _captured_career_page(value: dict[str, Any]) -> tuple[str, str] | None:
    """Return a same-site career page and its captured content hash."""
    site_url = str(value.get("final_url") or "")
    site_domain = _registered_domain(site_url)
    if not site_domain:
        return None
    hiring = value.get("hiring") or {}
    requested_url = str(hiring.get("career_url") or value.get("career_url") or "").strip()
    pages = value.get("pages") or []

    candidates = []
    if requested_url:
        candidates.append(requested_url)
    candidates.extend(
        str(page.get("url") or "")
        for page in pages
        if any(
            token in str(page.get("url") or "").casefold()
            for token in ("/career", "/karriere", "/jobb", "/job", "/stilling")
        )
    )

    for candidate in candidates:
        if not candidate or _registered_domain(candidate) != site_domain:
            continue
        for page in pages:
            page_url = str(page.get("url") or "")
            digest = str(page.get("content_sha256") or "")
            if page_url.rstrip("/") == candidate.rstrip("/") and len(digest) == 64:
                return page_url, digest
    return None


def _job_observations(profile: dict[str, Any]) -> list[dict[str, Any]]:
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    hiring = value.get("hiring") or {}
    page = _captured_career_page(value)
    if not page or hiring.get("hiring_active") is not True:
        return []

    titles: list[str] = []
    for item in hiring.get("open_positions") or value.get("open_positions") or []:
        if isinstance(item, dict):
            title = item.get("title") or item.get("name")
        else:
            title = item
        title = str(title or "").strip()
        if title and title not in titles:
            titles.append(title)

    count = hiring.get("open_positions_count")
    if count is None:
        count = value.get("open_positions_count")
    try:
        count = int(count) if count is not None else len(titles)
    except (TypeError, ValueError):
        count = len(titles)
    if count <= 0 and not titles:
        return []

    identity = value.get("identity_assessment") or {}
    source_url, digest = page
    common = {
        "organisation_number": str(profile["organisation_number"]),
        "platform": "company_site",
        "source_url": source_url,
        "retrieved_at": website.get("retrieved_at"),
        "content_sha256": digest,
        "exact_entity": True,
        "identity_proof": list(identity.get("promotion_proof") or []) + [
            {"type": "website_identity_gate", "status": identity.get("status"), "score": identity.get("score")}
        ],
        "acquisition_mode": "permitted_public_page",
        "rights_status": "approved",
        "source_class": "company_site",
        "strategy": "jobs_feed_discovery",
    }

    if not titles:
        titles = [f"Open positions ({count})"]
    rows = []
    for title in titles:
        row = {
            **common,
            "id": "company-site-job-" + hashlib.sha256(
                f"{profile['organisation_number']}|{source_url}|{title}".encode()
            ).hexdigest()[:24],
            "signal_type": "job_posting",
            "evidence_span": f"{title}; active open positions reported: {count}",
            "metrics": {
                "title": title,
                "open_positions_count": count,
                "interpretation": "Active positions extracted from the exact company-site career page.",
            },
        }
        rows.append(row)
    return rows


def _social_link_observations(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Publish company-declared social handles without fetching platform pages.

    The verified company site is the evidence for the relationship "this company
    links to this public profile".  No platform-side metric or content is inferred
    here, which keeps the observation within the company-owned source boundary.
    """
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    source_url = str(value.get("final_url") or website.get("source_url") or "").strip()
    digest = str(value.get("content_sha256") or website.get("content_sha256") or "").strip()
    if not source_url or len(digest) != 64:
        return []

    proof = list(identity.get("promotion_proof") or []) + [
        {"type": "website_identity_gate", "status": identity.get("status"), "score": identity.get("score")}
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for link in value.get("social_links") or []:
        if not isinstance(link, dict):
            continue
        platform = str(link.get("platform") or "").strip().casefold()
        profile_url = str(link.get("url") or "").strip()
        if not platform or not profile_url or platform == "company_site":
            continue
        key = (platform, profile_url)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "id": "company-social-handle-" + hashlib.sha256(
                f"{profile['organisation_number']}|{platform}|{profile_url}|{digest}".encode()
            ).hexdigest()[:24],
            "organisation_number": str(profile["organisation_number"]),
            "platform": platform,
            "signal_type": "profile_handle",
            "source_url": source_url,
            "retrieved_at": website.get("retrieved_at"),
            "content_sha256": digest,
            "exact_entity": True,
            "identity_proof": proof,
            "acquisition_mode": "permitted_public_page",
            "rights_status": "approved",
            "source_class": "company_site",
            "evidence_span": f"Verified company site links to {platform} profile: {profile_url}",
            "metrics": {
                "profile_url": profile_url,
                "interpretation": "Company-declared public profile link; no platform-side metrics fetched.",
            },
            "strategy": "verified_handle_extraction",
        })
    return rows


def _career_portal_observations(
    profile: dict[str, Any],
    site_url: str | None = None,
    career_portals: list[dict[str, Any]] | None = None,
    page_hash: str | None = None,
    fetched_at: str | None = None,
) -> list[dict[str, Any]]:
    """Publish company-declared ATS and job portal links without fetching third-party pages.

    The verified company site is the evidence for the relationship:
    'this company officially declares this career/recruitment portal'.
    """
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    source_url = site_url or str(value.get("final_url") or website.get("source_url") or "").strip()
    digest = page_hash or str(value.get("content_sha256") or website.get("content_sha256") or "").strip()
    retrieved_at = fetched_at or website.get("retrieved_at")
    if not source_url or len(digest) != 64:
        return []

    if career_portals is None:
        hiring = value.get("hiring") or {}
        portals = hiring.get("career_portals") or value.get("career_portals") or []
    else:
        portals = career_portals
    if not portals:
        return []

    proof = list(identity.get("promotion_proof") or []) + [
        {"type": "website_identity_gate", "status": identity.get("status"), "score": identity.get("score")}
    ]

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in portals:
        if not isinstance(item, dict):
            continue
        portal_platform = str(item.get("platform") or "").strip().casefold()
        portal_url = str(item.get("url") or "").strip()
        if not portal_platform or not portal_url:
            continue
        key = (portal_platform, portal_url)
        if key in seen:
            continue
        seen.add(key)

        obs_id = "company-career-portal-" + hashlib.sha256(
            f"{profile['organisation_number']}|{portal_platform}|{portal_url}|{digest}".encode()
        ).hexdigest()[:24]

        anchor_text = str(item.get("anchor_text") or "").strip()
        title = anchor_text or f"Career portal on {portal_platform}"
        rows.append({
            "id": obs_id,
            "organisation_number": str(profile["organisation_number"]),
            "platform": "job_board",
            "signal_type": "job_posting",
            "source_url": source_url,
            "retrieved_at": retrieved_at,
            "content_sha256": digest,
            "exact_entity": True,
            "identity_proof": proof,
            "acquisition_mode": "permitted_public_page",
            "rights_status": "approved",
            "source_class": "company_site",
            "evidence_span": f"Verified company site declares career portal on {portal_platform}: {portal_url}",
            "metrics": {
                "title": title,
                "portal_platform": portal_platform,
                "portal_url": portal_url,
                "anchor_text": anchor_text,
                "interpretation": f"Company-declared career portal link to {portal_platform}; no external platform scraping required.",
            },
            "strategy": "verified_career_portal_link",
        })
    return rows


def build_observations(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deduplicated, publishable company-site observations for one profile.

    The result contains the verified site-surface observation and all valid dated
    site-news observations produced from the captured snapshot.  If no dated
    news item is available, the news extractor's bounded news-page fallback is
    retained.  Invalid evidence is discarded by the shared publication schema.
    """
    candidates: list[dict[str, Any]] = []
    candidates.extend(profile.get("_external_observations") or [])
    if _exact_company_site(profile):
        activity = site_activity_observation(profile)
        if activity:
            candidates.append(activity)
        candidates.extend(_social_link_observations(profile))
        candidates.extend(_career_portal_observations(profile))
        candidates.extend(_job_observations(profile))
        candidates.extend(site_news_observations(profile))

    # Extractor IDs are deterministic.  Keeping the first occurrence preserves
    # the extractor's ordering while removing repeated captured items.
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in candidates:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in seen_ids or not publishable_observation(item):
            continue
        seen_ids.add(item_id)
        result.append(item)
    return result


def observations(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Alias matching the plural extractor API used by the site-news script."""
    return build_observations(profile)


def publishable_observations(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Descriptive alias for callers that want the publication boundary explicit."""
    return build_observations(profile)
