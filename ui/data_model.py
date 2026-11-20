# Copied verbatim from scripts/build_prototype.py (read_jsonl, compute_ratios, compact)
# so the new UI can evolve without touching the original builder.
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def compute_ratios(records: list[dict]) -> dict:
    if not records:
        return {"operating_margin_pct": None, "equity_ratio_pct": None}
    latest = records[0]
    rev = latest.get("revenue")
    ebit = latest.get("operating_result")
    assets = latest.get("assets")
    equity = latest.get("equity")
    debt = latest.get("debt")

    op_margin = round((ebit / rev) * 100, 2) if rev and rev > 0 and ebit is not None else None
    eq_ratio = round((equity / assets) * 100, 2) if assets and assets > 0 and equity is not None else None
    if eq_ratio is None and assets and assets > 0 and debt is not None:
        eq_ratio = round(((assets - debt) / assets) * 100, 2)

    return {
        "operating_margin_pct": op_margin,
        "equity_ratio_pct": eq_ratio,
    }



def _registry_facts(record: dict) -> dict:
    """Further Enhetsregisteret facts shown in the Company panel (same record, same source)."""
    rv = record.get("value") if record.get("status") == "available" and isinstance(record.get("value"), dict) else {}
    capital = rv.get("kapital") if isinstance(rv.get("kapital"), dict) else {}
    return {
        "founded": str(rv.get("stiftelsesdato") or "")[:10] or None,
        "deletedOn": str(rv.get("slettedato") or "")[:10] or None,
        "shareCapital": capital.get("belop"),
        "shareCapitalCurrency": capital.get("valuta") or "NOK",
        "vatRegistered": rv.get("registrertIMvaregisteret") if isinstance(rv.get("registrertIMvaregisteret"), bool) else None,
        "inGroup": rv.get("erIKonsern") if isinstance(rv.get("erIKonsern"), bool) else None,
        "formerNames": [
            {"name": h.get("navn"), "to": str(h.get("tilDato") or "")[:10] or None}
            for h in rv.get("historiskeNavn") or [] if isinstance(h, dict) and h.get("navn")
        ],
    }

def compact(row: dict, external_observations: list[dict] | None = None) -> dict:
    evidence = row.get("evidence", {})
    financial = evidence.get("financials", {})
    financial_history = evidence.get("financial_history", {})
    roles = evidence.get("roles", {})
    locations = evidence.get("locations", {})
    website = evidence.get("website", {})
    website_value = dict(website.get("value") or {})
    # Only profiles the envelope also publishes: declared by the employer on an
    # org-number-matched NAV ad and passing the handle identity rule. Profiles seen
    # only in search results are candidates, never shown as the company's.
    nav_socials = [ds for ds in row.get("_discovered_socials") or [] if ds.get("source") == "nav_employer_profile"]
    if nav_socials:
        from norway_company_agent.identity import assess_social_identity
        existing_socials = website_value.setdefault("social_links", [])
        for ds in nav_socials:
            if any(es.get("platform") == ds.get("platform") for es in existing_socials):
                continue
            if assess_social_identity(row, ds).get("publishable"):
                existing_socials.append({"platform": ds["platform"], "url": ds["url"]})
    if website.get("status") == "available" and not website_value.get("title"):
        website_value["title"] = "Company site fetched"
    identity_assessment = website_value.get("identity_assessment") or {}
    if website.get("status") == "available" and identity_assessment and not identity_assessment.get("publishable"):
        website_value["quarantined_title"] = website_value.get("title")
        website_value["quarantined_description"] = website_value.get("description")
        website_value["quarantined_social_count"] = len(website_value.get("discovered_social_links") or [])
        website_value["title"] = "Registry-linked site — identity not verified"
        website_value["description"] = "Fetched content is retained as discovered evidence but is not attributed to this legal entity."
    live = evidence.get("registry_live", {})
    accounting = evidence.get("accounting_obligation", {})
    external_observations = external_observations or []

    def observation_meta(item: dict) -> dict:
        return {
            "source": item.get("source_url"),
            "retrievedAt": item.get("retrieved_at"),
            "publishedAt": item.get("published_at") or item.get("date_published"),
            "hash": item.get("content_sha256"),
            "rightsStatus": item.get("rights_status"),
            "sourceClass": item.get("source_class"),
        }

    linkedin_profiles = [
        item for item in external_observations
        if item.get("platform") == "linkedin" and item.get("signal_type") == "profile_metrics" and item.get("exact_entity")
    ]
    linkedin_workforce = [
        item for item in external_observations
        if item.get("platform") == "linkedin" and item.get("signal_type") == "workforce_snapshot" and item.get("exact_entity")
    ]
    linkedin_posts = [
        item for item in external_observations
        if item.get("platform") == "linkedin" and item.get("signal_type") == "public_post" and item.get("exact_entity")
    ]
    linkedin_jobs = [
        item for item in external_observations
        if item.get("platform") == "linkedin" and item.get("signal_type") == "job_posting" and item.get("exact_entity")
    ]
    verified_handles = {}
    for item in external_observations:
        if item.get("signal_type") != "profile_handle" or not item.get("exact_entity"):
            continue
        url = item.get("profile_url") or item.get("source_url")
        if url:
            verified_handles[(item.get("platform"), url)] = {
                "platform": item.get("platform"),
                "url": url,
                "rightsStatus": item.get("rights_status"),
            }
    linkedin_profile = linkedin_profiles[-1] if linkedin_profiles else None
    linkedin_headcount = linkedin_workforce[-1] if linkedin_workforce else None
    external = {
        "handles": list(verified_handles.values()),
        "linkedin": {
            "available": bool(linkedin_profile),
            "profile": ({**(linkedin_profile.get("metrics") or {}), **observation_meta(linkedin_profile)} if linkedin_profile else {}),
            "workforce": ({**(linkedin_headcount.get("metrics") or {}), **observation_meta(linkedin_headcount)} if linkedin_headcount else {}),
            "posts": [
                {
                    **(item.get("metrics") or {}),
                    "text": item.get("evidence_span"),
                    **observation_meta(item),
                }
                for item in linkedin_posts[:10]
            ],
            "jobs": [
                {
                    **(item.get("metrics") or {}),
                    "text": item.get("evidence_span"),
                    **observation_meta(item),
                }
                for item in linkedin_jobs[:10]
            ],
        },
    }

    def meta(record: dict) -> dict:
        return {
            "status": record.get("status", "not_run"),
            "source": record.get("source_url"),
            "sourceClass": record.get("source_class") or record.get("source_type"),
            "retrievedAt": record.get("retrieved_at"),
            "effectiveAt": record.get("effective_at") or record.get("as_of"),
            "hash": record.get("content_sha256"),
            "rowKey": record.get("source_row_key"),
            "claimSpan": record.get("claim_span"),
        }

    # Extract Financial Records safely
    fin_records = (financial.get("value") or {}).get("records") or []
    if not fin_records and isinstance(row.get("financial"), dict):
        direct_f = row["financial"]
        if "records" in direct_f:
            fin_records = direct_f.get("records") or []
        else:
            fin_records = [direct_f]

    # Executive Synthesis & Financial Ratios
    synthesis_raw = row.get("synthesis") or {}
    fin_health = synthesis_raw.get("financial_health") or {}
    fallback_ratios = compute_ratios(fin_records)
    op_margin = fin_health.get("operating_margin_pct")
    if op_margin is None:
        op_margin = fallback_ratios.get("operating_margin_pct")
    eq_ratio = fin_health.get("equity_ratio_pct")
    if eq_ratio is None:
        eq_ratio = fallback_ratios.get("equity_ratio_pct")

    ratios = {
        "operating_margin_pct": op_margin,
        "equity_ratio_pct": eq_ratio,
        "multi_year_revenue_growth_pct": fin_health.get("multi_year_revenue_growth_pct"),
        "revenue_trajectory_badge": fin_health.get("revenue_trajectory_badge"),
        "revenue_trajectory_status": fin_health.get("revenue_trajectory_status"),
        "revenue_trajectory_narrative": fin_health.get("revenue_trajectory_narrative"),
    }

    synthesis_data = {
        "summary": synthesis_raw.get("summary") or synthesis_raw.get("business_scope") or "",
        "business_scope": synthesis_raw.get("business_scope") or "",
        "knowns": synthesis_raw.get("knowns") or [],
        "unknowns": synthesis_raw.get("unknowns") or [],
    }

    # Corporate Announcements (Brreg Kunngjøringer)
    ann_evidence = evidence.get("announcements") or {}
    ann_val = ann_evidence.get("value") or row.get("announcements") or {}
    ann_events = ann_val.get("events") or []
    announcements_data = {
        **meta(ann_evidence),
        "events": [
            {
                "date": e.get("date"),
                "title": e.get("title") or e.get("description"),
                "category": e.get("category", "filing"),
                "url": e.get("url") or ann_evidence.get("source_url"),
            }
            for e in ann_events[:15]
        ],
        "count": len(ann_events),
    }

    # Verified News
    news_evidence = evidence.get("news") or {}
    news_val = news_evidence.get("value") or row.get("news") or {}
    news_items = news_val.get("items") or []
    news_data = {
        **meta(news_evidence),
        "items": [
            {
                "title": item.get("title"),
                "date": item.get("date") or item.get("published_at"),
                "url": item.get("url"),
                "source": item.get("source") or item.get("publisher"),
            }
            for item in news_items[:10]
        ],
    }
    for item in news_data["items"]:
        label = ((row.get("ai_analysis") or {}).get("status") == "available") and next(
            (n for n in (row.get("ai_analysis") or {}).get("news") or [] if n.get("source_url") == item.get("url")), None)
        if label:
            item["ai"] = label

    # Open job postings (NAV arbeidsplassen, employer orgnr matched) + model analysis
    ai_raw = row.get("ai_analysis") or {}
    ai_ok = ai_raw.get("status") == "available"
    ai_jobs = {j.get("fact_id"): j for j in (ai_raw.get("jobs") or [])} if ai_ok else {}
    jobs_evidence = evidence.get("jobs") or {}
    postings = [p for p in ((jobs_evidence.get("value") or {}).get("postings") or []) if isinstance(p, dict)]
    jobs_data = {
        **meta(jobs_evidence),
        "items": [
            {
                "title": p.get("title"),
                "url": p.get("url"),
                "published": p.get("published"),
                "expires": p.get("expires"),
                "location": p.get("location"),
                "engagement": p.get("engagement_type"),
                "extent": p.get("extent"),
                "positions": p.get("position_count"),
                "ai": ai_jobs.get(f"J{i}"),
            }
            for i, p in enumerate(postings[:6], start=1)
        ],
    }

    # Dated posts on the verified company site, merged with press items for display
    ai_news_by_url = {n.get("source_url"): n for n in (ai_raw.get("news") or [])} if ai_ok else {}
    site_news = []
    if (website_value.get("identity_assessment") or {}).get("publishable"):
        from norway_company_agent.website import _is_junk_news, is_site_job, looks_like_news_item
        from norway_company_agent.website import drop_placeholder_cohorts
        for item in drop_placeholder_cohorts(website_value.get("news_items") or []):
            if isinstance(item, dict) and item.get("title") and item.get("date") and item.get("url") \
                    and not is_site_job(item) and looks_like_news_item(item) \
                    and not _is_junk_news(str(item["title"]), str(item["url"]), "", str(item["date"])):
                site_news.append({"title": item["title"], "date": item["date"], "url": item["url"], "source": "company website"})
    site_news.sort(key=lambda n: str(n["date"]), reverse=True)

    facts_by_id = {f.get("id"): f for f in (ai_raw.get("facts") or []) if isinstance(f, dict)}

    def cite(entry: dict) -> dict:
        return {"text": entry.get("text"), "sources": [
            {"id": fid, "url": facts_by_id.get(fid, {}).get("source_url"), "kind": facts_by_id.get(fid, {}).get("kind")}
            for fid in entry.get("fact_ids") or []
        ]}

    ai_data = {
        "available": ai_ok,
        "model": ai_raw.get("model"),
        "factCount": len(facts_by_id),
        "sentences": [cite(e) for e in (ai_raw.get("summary_sentences") or [])] if ai_ok else [],
        "watch": [cite(e) for e in (ai_raw.get("watch_points") or [])] if ai_ok else [],
        "description": cite(ai_raw["business_description"]) if ai_ok and ai_raw.get("business_description") else None,
    }

    municipality_str = row.get("municipality") or row.get("registered_office") or ""
    industry_code_str = row.get("industry_code") or row.get("nace_code") or ""
    industry_label_str = row.get("industry_label") or row.get("industry") or row.get("industry_code") or "Not reported"

    return {
        "org": str(row["organisation_number"]),
        "name": row["name"],
        "form": row.get("legal_form") or "AS",
        "employees": row.get("employees"),
        "municipality": municipality_str,
        "industryCode": industry_code_str,
        "industry": industry_label_str,
        "website": row.get("website"),
        "adverse": bool(row.get("bankrupt") or row.get("liquidating") or row.get("adverse")),
        "slice": row.get("sample_slice"),
        "split": row.get("evaluation_split"),
        "latestAccounts": row.get("latest_submitted_accounts"),
        "registrySource": evidence.get("registry", {}).get("source_url"),
        "registry": meta(evidence.get("registry", {})),
        "registryFacts": _registry_facts(evidence.get("registry", {})),
        "accounting": {**meta(accounting), "value": accounting.get("value") or {}},
        "financial": {**meta(financial), "records": fin_records[:3]},
        "financialHistory": {**meta(financial_history), "pdfs": (financial_history.get("value") or {}).get("pdfs", [])},
        "roles": {**meta(roles), "items": (roles.get("value") or {}).get("roles", [])[:30]},
        "locations": {**meta(locations), "items": (locations.get("value") or {}).get("locations", [])[:30]},
        "web": {**meta(website), "value": website_value},
        "liveStatus": live.get("status", "not_run"),
        "changes": row.get("change_history") or [],
        "external": external,
        "synthesis": synthesis_data,
        "ratios": ratios,
        "announcements": announcements_data,
        "news": news_data,
        "siteNews": [{**n, "ai": ai_news_by_url.get(n["url"])} for n in site_news[:8]],
        "jobs": jobs_data,
        "ai": ai_data,
    }


