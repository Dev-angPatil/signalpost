#!/usr/bin/env python3
"""Summarize external discovery stages for a saved Signalpost batch."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--envelopes", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    profiles, envelopes, observations = map(read_jsonl, (args.profiles, args.envelopes, args.observations))
    if len(profiles) != len(envelopes):
        raise ValueError("Profile and envelope counts differ")
    profile_by_org = {str(p["organisation_number"]): p for p in profiles}
    envelope_by_org = {str(e["organisation_number"]): e for e in envelopes}
    if set(profile_by_org) != set(envelope_by_org):
        raise ValueError("Profile and envelope organisation numbers differ")

    website_stages: Counter[str] = Counter()
    news_stages: Counter[str] = Counter()
    hiring_stages: Counter[str] = Counter()
    fields: Counter[tuple[str, str]] = Counter()
    platform_signals: Counter[tuple[str, str]] = Counter()
    examples: dict[str, list[str]] = {"website_ambiguous": [], "news_found": [], "jobs_found": []}

    for org, profile in profile_by_org.items():
        evidence = profile.get("evidence") or {}
        web = evidence.get("website") or {}
        value = web.get("value") or {}
        assessment = value.get("identity_assessment") or {}
        status = str(web.get("status") or "missing")
        if status != "available":
            website_stages[f"website_{status}"] += 1
        elif assessment.get("publishable"):
            website_stages["website_verified"] += 1
        else:
            website_stages["website_unverified"] += 1
        if status == "available" and not assessment.get("publishable") and len(examples["website_ambiguous"]) < 20:
            examples["website_ambiguous"].append(org)

        web_news = [x for x in (value.get("news_items") or []) if isinstance(x, dict) and x.get("title") and x.get("date") and x.get("url")]
        news = evidence.get("news") or {}
        news_value = news.get("value") or {}
        news_items = [x for x in (news_value.get("items") or []) if isinstance(x, dict) and x.get("title") and x.get("date") and x.get("url")]
        if web_news:
            news_stages["company_site_dated_items"] += len(web_news)
            if len(examples["news_found"]) < 20:
                examples["news_found"].append(org)
        elif news_items:
            news_stages["other_source_dated_items"] += len(news_items)
            if len(examples["news_found"]) < 20:
                examples["news_found"].append(org)
        else:
            news_stages["no_dated_item_extracted"] += 1

        hiring = value.get("hiring") or {}
        positions = hiring.get("open_positions") or value.get("open_positions") or []
        postings = value.get("open_postings") or hiring.get("open_postings") or []
        active = hiring.get("hiring_active") is True
        if active and (positions or postings):
            hiring_stages["active_with_posting"] += 1
            if len(examples["jobs_found"]) < 20:
                examples["jobs_found"].append(org)
        elif active:
            hiring_stages["active_without_title_or_url"] += 1
        else:
            hiring_stages["no_active_posting_extracted"] += 1

        for claim in envelope_by_org[org].get("claims") or []:
            field = str(claim.get("field") or "")
            if any(term in field for term in ("news", "job", "career", "social", "website")):
                fields[(field, str(claim.get("availability") or "unknown"))] += 1

    for item in observations:
        platform_signals[(str(item.get("platform") or "unknown"), str(item.get("signal_type") or "unknown"))] += 1

    report = {
        "audit": "signalpost_batch_coverage_v1",
        "companies": len(profiles),
        "observations": len(observations),
        "website_stages": dict(sorted(website_stages.items())),
        "news_stages": dict(sorted(news_stages.items())),
        "hiring_stages": dict(sorted(hiring_stages.items())),
        "external_claim_states": {f"{field}:{state}": count for (field, state), count in sorted(fields.items())},
        "observation_platform_signals": {f"{platform}:{signal}": count for (platform, signal), count in sorted(platform_signals.items())},
        "review_examples_by_stage": examples,
        "interpretation_limit": "No extracted item does not prove an opportunity existed; review source pages or independent labels before changing identity or extraction thresholds.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
