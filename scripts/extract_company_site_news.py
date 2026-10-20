#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse


NEWS_PATH = re.compile(r"/(?:news|press|aktuelt|nyheter|artikler|blog)(?:/|$)", re.I)


def _build_observation(
    profile: dict,
    website: dict,
    identity: dict,
    *,
    url: str,
    digest: str,
    title: str,
    published_at: str | None = None,
    captured_news_pages: int = 0,
    strategy: str = "company_site_activity",
) -> dict:
    org = str(profile["organisation_number"])
    row = {
        "id": "company-site-news-" + hashlib.sha256(f"{org}|{url}|{published_at or ''}|{title}".encode()).hexdigest()[:24],
        "organisation_number": org,
        "platform": "company_site",
        "signal_type": "public_post",
        "source_url": url,
        "retrieved_at": website.get("retrieved_at"),
        "content_sha256": digest,
        "exact_entity": True,
        "identity_proof": [{"type": "website_identity_gate", "score": identity.get("score"), "method": identity.get("method")}],
        "acquisition_mode": "permitted_public_page",
        "rights_status": "approved",
        "source_class": "company_site",
        "evidence_span": title[:1200],
        "metrics": {"captured_news_pages": captured_news_pages, "interpretation": "Company-owned activity; not independent sentiment."},
        "strategy": strategy,
    }
    if published_at:
        row["published_at"] = published_at
    return row


def observations(profile: dict) -> list[dict]:
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    if website.get("status") != "available" or not identity.get("publishable"):
        return []

    site_digest = str(value.get("content_sha256") or "")
    item_rows: list[dict] = []
    for item in value.get("news_items") or []:
        url = str(item.get("url") or "")
        title = str(item.get("title") or "").strip()
        published_at = str(item.get("date") or "").strip()
        digest = str(item.get("content_sha256") or site_digest)
        if (
            not url.startswith(("http://", "https://"))
            or not title
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published_at)
            or len(digest) != 64
        ):
            continue
        item_rows.append(_build_observation(
            profile,
            website,
            identity,
            url=url,
            digest=digest,
            title=title,
            published_at=published_at,
            captured_news_pages=len(value.get("news_items") or []),
            strategy="company_site_news_item",
        ))
    if item_rows:
        item_rows.sort(key=lambda item: (str(item.get("published_at") or ""), item["source_url"]), reverse=True)
        return item_rows

    pages = [
        page for page in (value.get("pages") or [])
        if NEWS_PATH.search(urlparse(str(page.get("url") or "")).path)
    ]
    if not pages:
        return []
    # Prefer an individual article over an archive page when the bounded crawl
    # captured both. One observation is enough to prove site activity without
    # rewarding a site for repeated navigation links.
    pages.sort(key=lambda page: (-len([part for part in urlparse(str(page.get("url") or "")).path.split("/") if part]), str(page.get("url") or "")))
    page = pages[0]
    url = str(page.get("url") or "")
    digest = str(page.get("content_sha256") or site_digest)
    if not url.startswith(("http://", "https://")) or len(digest) != 64:
        return []
    title = str(page.get("title") or "Company news/activity page").strip()
    return [_build_observation(
        profile,
        website,
        identity,
        url=url,
        digest=digest,
        title=title,
        captured_news_pages=len(pages),
    )]


def observation(profile: dict) -> dict | None:
    """Backward-compatible single-observation view of the newest site item."""
    rows = observations(profile)
    return rows[0] if rows else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract company-owned activity from exact-site bounded news pages.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    profiles = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [item for profile in profiles for item in observations(profile)]
    Path(args.output).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    report = {
        "connector": "exact_company_site_news_activity_v1",
        "profiles": len(profiles),
        "companies_with_activity": len({str(item["organisation_number"]) for item in rows}),
        "observations": len(rows),
        "claim_boundary": "Company-owned activity only; never treated as independent sentiment.",
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
