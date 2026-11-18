#!/usr/bin/env python3
"""List every externally sourced claim in a run, with the proof that tied it to the company.

Precision review aid: the qualification bar is 95% of checked claims about the
right company. This prints, per claim family, how each published claim was
verified, and writes a reviewable table of the claims that rest on anything
weaker than the company's own organisation number.

    PYTHONPATH=src uv run python scripts/audit_external_claims.py \
        --profiles out/smoke-profiles.jsonl --envelopes out/smoke-envelopes.jsonl \
        --output out/smoke-precision-audit.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

STRONG = {"organisation_number", "subunit_organisation_number"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def website_basis(profile: dict) -> tuple[str, str]:
    """(basis class, human-readable detail) for the published website."""
    web = (profile.get("evidence") or {}).get("website") or {}
    value = web.get("value") or {}
    assessment = value.get("identity_assessment") or {}
    proofs = [p for p in assessment.get("promotion_proof") or [] if isinstance(p, dict)]
    types = [str(p.get("type")) for p in proofs]
    source = str(web.get("source_type") or "")
    if any(t in STRONG for t in types):
        basis = "org_number_on_site"
    elif "registered_address" in types:
        basis = "registered_address_with_legal_name"
    elif any(t.startswith("name_domain") for t in types):
        basis = "name_equals_domain_plus_corroboration"
    elif any("wikidata" in t for t in types):
        basis = "wikidata_org_number_listing_plus_name"
    elif any("nav_employer" in t for t in types):
        basis = "nav_employer_homepage_plus_name"
    elif source.startswith("registry_linked"):
        basis = "registry_declared_plus_legal_name"
    else:
        basis = f"other:{source}"
    span = next((str(p.get("span") or p.get("value") or "") for p in proofs), "") or "; ".join(assessment.get("reasons") or [])
    return basis, " ".join(span.split())[:220]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("envelopes_pos", nargs="?", type=Path, help="Path to envelopes.jsonl (positional)")
    ap.add_argument("--profiles", type=Path, help="Path to profiles.jsonl")
    ap.add_argument("--envelopes", type=Path, help="Path to envelopes.jsonl")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    envelopes_path = args.envelopes or args.envelopes_pos
    if not envelopes_path:
        ap.error("the following arguments are required: --envelopes or envelopes positional argument")

    profiles_path = args.profiles
    if not profiles_path:
        candidate = envelopes_path.with_name(envelopes_path.name.replace("envelopes", "profiles"))
        if candidate.exists():
            profiles_path = candidate
        else:
            ap.error("the following arguments are required: --profiles (could not infer from envelopes path)")

    profiles = {str(p["organisation_number"]): p for p in read_jsonl(profiles_path)}
    families: Counter[str] = Counter()
    companies: dict[str, set[str]] = {}
    website_basis_counts: Counter[str] = Counter()
    review: list[dict] = []

    for env in read_jsonl(envelopes_path):
        org = str(env["organisation_number"])
        profile = profiles.get(org) or {}
        name = profile.get("name")
        basis, detail = website_basis(profile)
        for claim in env.get("claims") or []:
            if claim.get("availability") != "available":
                continue
            field = str(claim.get("field") or "")
            value = claim.get("value")
            if field == "official_website":
                family, why = "website", basis
                website_basis_counts[basis] += 1
            elif field.startswith("social_"):
                family, why = "social", f"linked from site verified by {basis}; handle carries the legal name"
            elif field.startswith("news_") and field.endswith("_url"):
                press = "news.google.com" in str(value) or not str(value).startswith(str((profile.get("evidence") or {}).get("website", {}).get("value", {}).get("final_url") or "~")[:12])
                family = "news"
                why = "press headline carrying the legal name" if press else f"article on site verified by {basis}"
            elif field.startswith("job_posting_") and field.endswith("_url"):
                family = "jobs"
                why = "NAV ad, employer org number matched" if "arbeidsplassen.nav.no" in str(value) else f"posting on site verified by {basis}"
            elif field.startswith("google_places_") and field.endswith("_name"):
                family, why = "places", "Google Places exact legal name and address / verified site"
            else:
                continue
            families[family] += 1
            companies.setdefault(family, set()).add(org)
            if not (family == "website" and basis == "org_number_on_site") and not why.startswith("NAV ad"):
                review.append({"organisation_number": org, "name": name, "family": family, "field": field, "value": value,
                               "basis": why, "detail": detail if family in {"website", "social"} else ""})

    summary = {
        "companies": len(profiles),
        "claims_by_family": dict(families),
        "companies_by_family": {k: len(v) for k, v in companies.items()},
        "website_basis": dict(website_basis_counts.most_common()),
        "claims_needing_human_review": len(review),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    for row in review:
        if row["family"] in {"website", "social"}:
            print(f"  [{row['family']}] {row['organisation_number']} {str(row['name'])[:34]:34} -> {row['value']} | {row['basis']} | {row['detail'][:110]}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"summary": summary, "review": review}, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
