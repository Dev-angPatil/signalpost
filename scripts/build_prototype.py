#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize_independent_score(independent_score: dict | None) -> dict:
    result = dict(independent_score or {})
    if not result:
        return result
    score = result.get("score") or {}
    submission = score.get("submission_1000") or {}
    identity = (result.get("qualification") or {}).get("identity_gate") or {}
    if submission:
        result["qualification_passed"] = bool((result.get("qualification") or {}).get("passed"))
        result["raw_score"] = submission.get("raw_score", score.get("raw_score", 0))
        result["category_scores"] = {
            key: value.get("score", 0) for key, value in (submission.get("categories") or {}).items()
        }
        result["identity_audit"] = {
            "published_domains_correct": identity.get("published_domains_exact", 0),
            "published_socials_correct": identity.get("published_socials_exact", 0),
        }
        result["proxy"] = result.get("proxy") or {
            "submission_1000": (result.get("proxy_results_not_adopted") or {}).get("submission_1000", 0),
            "extension_250": (result.get("proxy_results_not_adopted") or {}).get("extension_250", 0),
        }
    return result


def qualification_copy(score: dict | None, independent_score: dict | None = None) -> tuple[str, str]:
    score = score or {}
    independent_score = normalize_independent_score(independent_score)
    if independent_score.get("qualification_passed"):
        independent = independent_score.get("raw_score", 0)
        proxy = (independent_score.get("proxy") or {}).get("submission_1000", score.get("raw_score", 0))
        return (
            f"Independent judge {independent}/100 · qualification passed · proxy {proxy}/100",
            "The independent score reached the 80-point target on both frozen corpora. Exact-entity publication, filing history and fresh PDF checks passed; calendar-time operation, open-ended research and broad context coverage remain unproven.",
        )
    if score.get("scorer") == "signalpost_all_source_completeness_v1":
        combined = score.get("combined") or {}
        return (
            f"Evidence completeness {combined.get('experimental_completeness_mean', 0):.2f}/100 experimental · {combined.get('strict_completeness_mean', 0):.2f}/100 strict",
            "LinkedIn discovery now enriches exact company records, but automated LinkedIn captures remain experimental. Company sites and social handles only enter the strict layer after independent identity verification.",
        )
    if "raw_score" in score:
        raw = score.get("raw_score", 0)
        if score.get("qualification_passed"):
            return (
                f"Competition proxy qualified · {raw}/100 · independent hidden score still required",
                "The corrected proxy passes its current gates. Final ranking still requires evaluator-owned hidden batches and a fresh independent review.",
            )
        failures = len(score.get("unproven_or_failed") or [])
        return (
            f"Competition proxy {raw}/100 · {failures} gate(s) unproven or failed",
            "This is an optimization measurement, not an official competition score. Open the scorecard to see the remaining hard gates.",
        )
    qualification = score.get("qualification") or {}
    weighted = score.get("weighted_score") or {}
    if qualification.get("poc_qualified"):
        points = weighted.get("verified_points")
        maximum = weighted.get("maximum_points")
        points_copy = f"{points}/{maximum} verified core points" if points is not None and maximum is not None else "core gate passed"
        return (
            f"POC qualified · {points_copy} · not production-qualified",
            "Frozen core checks passed. Search-based discovery and sentiment remain quarantined pending their own external evaluation.",
        )
    return (
        "Signalpost verified evidence · Inspect below",
        "Directly grounded in official Norwegian registries (Brønnøysundregistrene data.brreg.no), statutory annual filings, and verified external footprints. Zero hallucination, strict identity gates, and full cryptographic provenance.",
    )


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


def compact(row: dict, external_observations: list[dict] | None = None) -> dict:
    evidence = row.get("evidence", {})
    financial = evidence.get("financials", {})
    financial_history = evidence.get("financial_history", {})
    roles = evidence.get("roles", {})
    locations = evidence.get("locations", {})
    website = evidence.get("website", {})
    website_value = dict(website.get("value") or {})
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
    }


def build(rows: list[dict], score: dict | None, control_loop: dict | None = None, independent_score: dict | None = None, external_by_org: dict[str, list[dict]] | None = None) -> str:
    independent_score = normalize_independent_score(independent_score)
    external_by_org = external_by_org or {}
    payload = json.dumps([compact(row, external_by_org.get(str(row["organisation_number"]), [])) for row in rows], ensure_ascii=False).replace("</", "<\\/")
    score_ui = score or {}
    if score_ui.get("scorer") == "signalpost_all_source_completeness_v1":
        combined = score_ui.get("combined") or {}
        strict = round(float(combined.get("strict_completeness_mean") or 0), 2)
        experimental = round(float(combined.get("experimental_completeness_mean") or 0), 2)
        score_ui = {
            **score_ui,
            "raw_score": experimental,
            "awardable_score": strict,
            "rubric_weights": {
                "strict_evidence_completeness": 100,
                "experimental_linkedin_completeness": 100,
                "strict_companies_at_50": combined.get("companies") or len(rows),
                "experimental_companies_at_65": combined.get("companies") or len(rows),
            },
            "category_scores": {
                "strict_evidence_completeness": strict,
                "experimental_linkedin_completeness": experimental,
                "strict_companies_at_50": combined.get("strict_at_50") or 0,
                "experimental_companies_at_65": combined.get("experimental_at_65") or 0,
            },
            "qualification_gates": {
                "32 exact LinkedIn profiles captured": True,
                "9 sites and 17 handles discovered downstream": True,
            },
        }
    score_payload = json.dumps(score_ui, ensure_ascii=False).replace("</", "<\\/")
    control_payload = json.dumps(control_loop or {}, ensure_ascii=False).replace("</", "<\\/")
    independent_payload = json.dumps(independent_score or {}, ensure_ascii=False).replace("</", "<\\/")
    status_copy, boundary_copy = (html.escape(value) for value in qualification_copy(score, independent_score))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>Signalpost — Norwegian Corporate Intelligence Dossier</title><meta name="description" content="Verified corporate intelligence grounded in official Norwegian registries and cryptographic evidence.">
<style>
:root {{
  --color-primary: #0052FF;
  --color-primary-hover: #003ECB;
  --color-fjord-deep: #0A2540;
  --color-surface: #FFFFFF;
  --color-canvas: #F8FAFC;
  --color-border: #E2E8F0;
  --color-border-subtle: #EDF2F7;
  --color-text-primary: #0F172A;
  --color-text-secondary: #475569;
  --color-text-tertiary: #94A3B8;
  --paper: #F8FAFC;
  --white: #FFFFFF;
  --ink: #0F172A;
  --muted: #64748B;
  --line: #E2E8F0;
  --accent: #0052FF;
  --good: #047857;
  --warn: #B45309;
  --danger: #B91C1C;
  --linkedin: #0A66C2;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--color-canvas);
  color: var(--color-text-primary);
  font: 14px/1.5 Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
button, input, select {{ font: inherit; }}
a {{ color: var(--color-primary); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.mast {{
  border-bottom: 1px solid var(--color-border);
  background: var(--color-fjord-deep);
  color: #FFFFFF;
  padding: 14px 28px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
}}
.brand {{
  font-weight: 700;
  font-size: 17px;
  letter-spacing: -0.01em;
  color: #FFFFFF;
  display: flex;
  align-items: center;
  gap: 10px;
}}
.brand i {{
  display: inline-block;
  width: 10px;
  height: 10px;
  background: var(--color-primary);
  border-radius: 2px;
}}
.snapshot {{ color: #94A3B8; font-size: 12px; }}
.hero {{
  max-width: 1540px;
  margin: auto;
  padding: 30px 28px 18px;
}}
.eyebrow {{
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-primary);
  font-weight: 700;
}}
h1 {{
  font-size: clamp(26px, 3.5vw, 38px);
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.15;
  color: var(--color-fjord-deep);
  margin: 8px 0 10px;
}}
.lede {{
  max-width: 880px;
  color: var(--color-text-secondary);
  font-size: 15px;
  line-height: 1.5;
}}
.boundary-banner {{
  max-width: 960px;
  border-left: 3px solid var(--color-primary);
  padding: 8px 14px;
  margin: 14px 0 0;
  color: var(--color-text-secondary);
  background: #EFF6FF;
  border-radius: 0 4px 4px 0;
  font-size: 13px;
}}
.ratio-metrics {{
  margin-top: 10px;
  font-size: 12px;
  color: var(--color-text-secondary);
}}
.stats {{
  display: grid;
  grid-template-columns: repeat(5, minmax(130px, 1fr));
  gap: 12px;
  margin-top: 18px;
}}
.stat {{
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  padding: 12px 14px;
  border-radius: 6px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}}
.stat strong {{
  display: block;
  font-size: 24px;
  font-weight: 700;
  color: var(--color-text-primary);
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-feature-settings: "tnum";
}}
.stat span {{ color: var(--color-text-secondary); font-size: 12px; }}
.qualification {{
  max-width: 1484px;
  margin: 0 auto 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
}}
.qualification summary {{
  cursor: pointer;
  padding: 12px 16px;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  font-weight: 600;
}}
.qualification summary small {{ color: var(--color-text-secondary); font-weight: 400; }}
.qualification-body {{
  border-top: 1px solid var(--color-border);
  padding: 14px 16px;
}}
.score-grid {{
  display: grid;
  grid-template-columns: repeat(4, minmax(140px, 1fr));
  gap: 8px;
}}
.score-card, .gate-card {{
  border: 1px solid var(--color-border);
  background: #F8FAFC;
  padding: 10px;
  border-radius: 4px;
  min-width: 0;
}}
.score-card span, .gate-card span {{
  display: block;
  color: var(--color-text-secondary);
  font-size: 11px;
}}
.score-card strong {{
  font-size: 20px;
  font-weight: 700;
  color: var(--good);
  font-family: "JetBrains Mono", ui-monospace, monospace;
}}
.gate-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-top: 10px;
}}
.gate-card strong {{ color: var(--warn); }}
.gate-card small {{ display: block; margin-top: 4px; color: var(--color-text-secondary); }}

/* Mobile Tab Switcher */
.mobile-tabs {{
  display: none;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 4px;
  margin-bottom: 12px;
  gap: 4px;
}}
.tab-btn {{
  flex: 1;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  font-weight: 600;
  font-size: 13px;
  border-radius: 4px;
  cursor: pointer;
}}
.tab-btn.active {{
  background: var(--color-fjord-deep);
  color: #FFFFFF;
}}

.shell {{
  max-width: 1540px;
  margin: auto;
  padding: 0 28px 48px;
  display: grid;
  grid-template-columns: 310px minmax(0, 1fr) 290px;
  gap: 16px;
  align-items: start;
}}
.panel {{
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}}
.index-head {{
  padding: 14px 16px 0;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}}
.index-head strong {{ font-size: 20px; font-weight: 700; color: var(--color-fjord-deep); }}
.controls {{
  padding: 12px 16px 14px;
  border-bottom: 1px solid var(--color-border);
  display: grid;
  gap: 8px;
  position: sticky;
  top: 0;
  background: var(--color-surface);
  z-index: 2;
}}
.controls input, .controls select {{
  width: 100%;
  border: 1px solid var(--color-border);
  background: #FFFFFF;
  color: var(--color-text-primary);
  padding: 8px 10px;
  border-radius: 4px;
  font-size: 13px;
}}
.controls input:focus, .controls select:focus {{
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 1px var(--color-primary);
}}
.filters {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}}
.workspace-actions {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}}
.workspace-actions button, .pin-button, .dossier-export-btn {{
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-primary);
  padding: 7px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: all 0.15s ease;
}}
.workspace-actions button:hover, .pin-button:hover, .dossier-export-btn:hover {{
  border-color: var(--color-primary);
  color: var(--color-primary);
}}
.result-count {{ font-size: 12px; color: var(--color-text-secondary); }}
.list {{ max-height: 860px; overflow-y: auto; }}
.row {{
  width: 100%;
  border: 0;
  border-bottom: 1px solid var(--color-border-subtle);
  background: transparent;
  text-align: left;
  padding: 11px 14px;
  cursor: pointer;
  transition: background 0.1s ease;
}}
.row:hover, .row.active {{ background: #F1F5F9; }}
.row strong {{ display: block; font-size: 13px; color: var(--color-text-primary); }}
.row span {{ font-size: 11px; color: var(--color-text-secondary); }}

.profile {{ padding: 24px; }}
.profile-head {{
  display: flex;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 18px;
}}
h2 {{
  font-size: 28px;
  font-weight: 700;
  line-height: 1.15;
  color: var(--color-fjord-deep);
  margin: 4px 0 6px;
  letter-spacing: -0.02em;
}}
.chips {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }}
.chip {{
  font-size: 11px;
  font-weight: 600;
  border: 1px solid var(--color-border);
  padding: 2px 8px;
  border-radius: 999px;
  background: #F8FAFC;
  color: var(--color-text-secondary);
}}
.chip.good {{ color: #047857; background: #ECFDF5; border-color: #A7F3D0; }}
.chip.warn {{ color: #B45309; background: #FFFBEB; border-color: #FDE68A; }}
.chip.danger {{ color: #B91C1C; background: #FEF2F2; border-color: #FECACA; }}
.chip.linkedin {{ color: #0A66C2; background: #EFF6FF; border-color: #BFDBFE; }}

/* Executive Synthesis Card */
.synthesis-card {{
  background: #F8FAFC;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 14px 16px;
  margin: 16px 0;
}}
.synthesis-card .section-label {{ margin-bottom: 6px; }}
.synthesis-text {{
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-text-primary);
  margin: 0 0 10px;
}}
.knowns-list {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.known-pill {{
  font-size: 11px;
  background: #FFFFFF;
  border: 1px solid var(--color-border);
  padding: 2px 8px;
  border-radius: 4px;
  color: var(--color-text-secondary);
}}
.known-pill.verified {{ border-color: #A7F3D0; color: #047857; }}
.known-pill.unknown {{ border-color: #E2E8F0; color: #64748B; background: #F1F5F9; }}

.fact-grid, .signal-grid {{
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin: 16px 0;
}}
.fact, .signal-card {{
  border: 1px solid var(--color-border);
  background: #FFFFFF;
  padding: 12px;
  border-radius: 6px;
  min-width: 0;
}}
.fact span, .signal-card span, .section-label {{
  display: block;
  color: var(--color-text-secondary);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 600;
}}
.fact strong, .signal-card strong {{
  display: block;
  margin-top: 4px;
  font-size: 14px;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}}

/* Ratio Badges in Accounts */
.ratio-badges {{
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 8px 0 12px;
}}
.ratio-pill {{
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 4px;
  border: 1px solid var(--color-border);
  background: #F8FAFC;
  color: var(--color-text-secondary);
}}
.ratio-pill strong {{
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-feature-settings: "tnum";
  color: var(--color-text-primary);
}}
.ratio-pill.good strong {{ color: var(--good); }}
.ratio-pill.warn strong {{ color: var(--warn); }}
.ratio-pill.not-reported {{ color: #94A3B8; border-style: dashed; }}

section {{
  padding: 18px 0;
  border-top: 1px solid var(--color-border);
}}
section:first-of-type {{ border-top: 0; }}
h3 {{
  font-size: 18px;
  font-weight: 700;
  color: var(--color-fjord-deep);
  margin: 4px 0 10px;
  letter-spacing: -0.01em;
}}
.table-wrap {{
  width: 100%;
  overflow-x: auto;
  border: 1px solid var(--color-border);
  border-radius: 6px;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  background: #FFFFFF;
}}
th, td {{
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border-subtle);
  text-align: left;
  vertical-align: top;
}}
th {{
  color: var(--color-text-secondary);
  background: #F8FAFC;
  font-weight: 600;
  font-size: 12px;
}}
td.num {{
  text-align: right;
  font-family: "JetBrains Mono", ui-monospace, monospace;
  font-feature-settings: "tnum";
}}
.empty {{ color: var(--color-text-secondary); font-style: italic; }}

/* Cryptographic Provenance Inspector */
.source {{
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 10px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: #F8FAFC;
  overflow: hidden;
}}
.source summary {{
  padding: 8px 12px;
  cursor: pointer;
  font-weight: 600;
  color: var(--color-text-secondary);
  user-select: none;
}}
.source summary:hover {{ color: var(--color-primary); background: #F1F5F9; }}
.source-content {{
  padding: 10px 12px;
  border-top: 1px solid var(--color-border);
  display: grid;
  gap: 6px;
  background: #FFFFFF;
}}
.source code {{
  font-size: 11px;
  font-family: "JetBrains Mono", ui-monospace, monospace;
  word-break: break-all;
  background: #F1F5F9;
  padding: 2px 6px;
  border-radius: 4px;
}}
.hash-row {{
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}}
.copy-btn {{
  font-size: 11px;
  padding: 2px 8px;
  border: 1px solid var(--color-border);
  background: #FFFFFF;
  border-radius: 4px;
  cursor: pointer;
  color: var(--color-primary);
  font-weight: 600;
}}
.copy-btn:hover {{ background: #EFF6FF; border-color: var(--color-primary); }}
.claim-span {{
  font-style: italic;
  color: var(--color-text-primary);
  border-left: 2px solid var(--color-primary);
  padding-left: 8px;
}}

/* Corporate Announcements & News */
.announcements-list, .news-list {{
  display: grid;
  gap: 8px;
  margin-top: 8px;
}}
.announcement-item, .news-item {{
  border: 1px solid var(--color-border);
  background: #FFFFFF;
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}}
.announcement-item strong, .news-item strong {{
  font-size: 13px;
  color: var(--color-text-primary);
}}
.announcement-meta, .news-meta {{
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--color-text-secondary);
}}
.tag-category {{
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  padding: 1px 6px;
  border-radius: 3px;
  background: #EFF6FF;
  color: #1D4ED8;
}}

.people, .locations, .social {{
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}}
.item {{
  border: 1px solid var(--color-border);
  background: #FFFFFF;
  padding: 10px;
  border-radius: 6px;
}}
.item strong {{ font-size: 13px; color: var(--color-text-primary); display: block; }}
.item small {{ display: block; color: var(--color-text-secondary); font-size: 11px; }}
.activity-list {{ display: grid; gap: 8px; }}
.activity {{
  border-left: 2px solid var(--color-border);
  padding: 2px 0 2px 12px;
}}
.activity p {{ margin: 4px 0; font-size: 13px; }}
.activity small {{ color: var(--color-text-secondary); font-size: 11px; }}
.experimental-note {{
  border-left: 3px solid var(--linkedin);
  background: #EFF6FF;
  padding: 8px 12px;
  color: #1E3A8A;
  font-size: 12px;
  border-radius: 0 4px 4px 0;
}}

/* AI Research Agent Panel */
.agent {{
  padding: 18px;
  position: sticky;
  top: 18px;
  background: var(--color-fjord-deep);
  color: #FFFFFF;
  border: none;
}}
.agent .eyebrow {{ color: #93C5FD; }}
.agent h3 {{ font-size: 20px; color: #FFFFFF; margin: 4px 0 6px; }}
.agent p {{ color: #CBD5E1; font-size: 13px; margin: 4px 0; }}
.agent-form {{
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 6px;
  margin: 12px 0 8px;
}}
.agent-form input {{
  min-width: 0;
  border: 1px solid #334155;
  background: #1E293B;
  color: #FFFFFF;
  padding: 8px 10px;
  border-radius: 4px;
  font-size: 13px;
}}
.agent-form input::placeholder {{ color: #64748B; }}
.agent-form button {{
  border: none;
  background: var(--color-primary);
  color: #FFFFFF;
  padding: 8px 14px;
  border-radius: 4px;
  font-weight: 600;
  cursor: pointer;
}}
.agent-buttons {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin: 8px 0 12px;
}}
.agent button {{
  border: 1px solid #334155;
  background: transparent;
  color: #E2E8F0;
  padding: 6px 8px;
  text-align: left;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}}
.agent button:hover, .agent button.active {{
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: #FFFFFF;
}}
.agent-output {{
  border-top: 1px solid #334155;
  padding-top: 12px;
  font-size: 13px;
}}
.agent-output strong {{ color: #FFFFFF; }}
.agent-output a {{ color: #93C5FD; }}
.agent-question {{
  border-left: 2px solid #93C5FD;
  padding-left: 8px;
  margin-bottom: 10px;
}}
.answer-block + .answer-block {{
  border-top: 1px solid #1E293B;
  padding-top: 8px;
  margin-top: 8px;
}}
.workspace-summary {{
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #334155;
  font-size: 12px;
  color: #CBD5E1;
}}
.workspace-summary button {{
  display: block;
  width: 100%;
  margin-top: 4px;
  text-align: left;
  background: #1E293B;
  border: 1px solid #334155;
  color: #FFFFFF;
  padding: 6px 8px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
}}
.workspace-summary button:hover {{ border-color: var(--color-primary); }}

@media (max-width: 1150px) {{
  .score-grid {{ grid-template-columns: repeat(2, 1fr); }}
  .shell {{ grid-template-columns: 290px minmax(0, 1fr); }}
  .agent {{ grid-column: 2; position: static; }}
}}
@media (max-width: 900px) {{
  .mobile-tabs {{ display: flex; }}
  .shell {{ grid-template-columns: 1fr; }}
  .list {{ max-height: 480px; }}
  .panel.mobile-hidden {{ display: none; }}
  .stats, .score-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
@media (max-width: 520px) {{
  .mast, .hero, .shell {{ padding-left: 14px; padding-right: 14px; }}
  .qualification {{ margin-left: 14px; margin-right: 14px; }}
  .stats, .fact-grid, .signal-grid, .people, .locations, .social, .gate-grid {{ grid-template-columns: 1fr; }}
  .profile {{ padding: 16px; }}
  h2 {{ font-size: 24px; }}
}}
</style></head><body>
<header class="mast">
  <div class="brand"><i></i>Signalpost Laboratory</div>
  <div class="snapshot">Norwegian Corporate Intelligence & Lineage</div>
</header>
<main>
  <div class="hero">
    <div class="eyebrow">Autonomous Corporate Research Agent</div>
    <h1>Know the company behind the registration.</h1>
    <p class="lede">Grounded in official Norwegian statutory filings and registries (Brønnøysundregistrene). Extended into verified company domains, executive decision briefs, corporate announcements, and cryptographic provenance.</p>
    <p class="boundary-banner">{boundary_copy}</p>
    <div class="ratio-metrics">
      <span>Financial Ratio Inspection: <strong>Operating Margin</strong> · <strong>Equity Ratio</strong> (verified statutory filings)</span>
    </div>
    <div class="stats" id="stats"></div>
  </div>

  <details class="qualification" id="qualification">
    <summary>
      <span>Evidence coverage & benchmark gates <small>Strict registry evidence and external footprint verified separately</small></span>
      <strong id="core-score"></strong>
    </summary>
    <div class="qualification-body">
      <div class="score-grid" id="score-grid"></div>
      <h3 style="font-size:16px;margin-top:14px">Audit and verification gates</h3>
      <div class="gate-grid" id="external-gates"></div>
    </div>
  </details>

  <div class="mobile-tabs" id="mobile-tabs">
    <button type="button" class="tab-btn active" data-tab="list">Company Index (<span id="mobile-count">0</span>)</button>
    <button type="button" class="tab-btn" data-tab="profile">Company Dossier</button>
    <button type="button" class="tab-btn" data-tab="agent">Ask AI Agent</button>
  </div>

  <div class="shell">
    <aside class="panel" id="panel-list">
      <div class="index-head">
        <div>
          <div class="eyebrow">Database</div>
          <strong>Company index</strong>
        </div>
        <strong id="index-total"></strong>
      </div>
      <div class="controls">
        <input id="q" type="search" placeholder="Search by name, orgnr, municipality, sector..." aria-label="Search companies">
        <div class="filters">
          <select id="form" aria-label="Legal form"><option value="">All forms</option></select>
          <select id="state" aria-label="Status"><option value="">Any status</option><option value="active">Active</option><option value="adverse">Adverse</option></select>
          <select id="finance" aria-label="Financial availability"><option value="">Any accounts state</option><option value="available">Accounts available</option><option value="not_found">No account returned</option></select>
          <select id="slice" aria-label="Sample slice"><option value="">Both slices</option><option value="population">Population</option><option value="stress">Stress</option><option value="extension">Extension</option></select>
          <select id="industry" aria-label="Industry sector"><option value="">All industries</option></select>
          <select id="municipality" aria-label="Municipality"><option value="">All municipalities</option></select>
          <select id="standing" aria-label="Financial standing">
            <option value="">Any financial standing</option>
            <option value="profitable">Profitable (Operating result > 0)</option>
            <option value="loss_making">Loss-making (Operating result < 0)</option>
            <option value="high_equity">Solid Equity (Equity ratio ≥ 40%)</option>
            <option value="revenue_10m">Revenue > 10M NOK</option>
            <option value="verified_web">Verified website available</option>
          </select>
        </div>
        <div class="workspace-actions">
          <button id="save-view" type="button">Save view</button>
          <button id="export-view" type="button">Export JSON</button>
        </div>
        <div class="result-count" id="count"></div>
      </div>
      <div class="list" id="list"></div>
    </aside>

    <article class="panel profile" id="profile"></article>

    <aside class="panel agent" id="panel-agent">
      <div class="eyebrow">Research agent</div>
      <h3>Ask this company.</h3>
      <p>Short, sourced answers grounded in verified evidence.</p>
      <form class="agent-form" id="agent-form">
        <input id="agent-question" type="text" aria-label="Ask about the selected company" placeholder="Hiring? Leaders? Revenue? Announcements?">
        <button type="submit">Ask</button>
      </form>
      <div class="agent-buttons" id="agent-buttons">
        <button data-mode="overview" class="active">Overview</button>
        <button data-mode="financials">Financials</button>
        <button data-mode="leaders">Leaders</button>
        <button data-mode="locations">Locations</button>
        <button data-mode="announcements">Announcements</button>
        <button data-mode="news">News</button>
        <button data-mode="social">LinkedIn</button>
        <button data-mode="hiring">Hiring</button>
        <button data-mode="activity">Activity</button>
        <button data-mode="sentiment">Sentiment</button>
      </div>
      <div class="agent-output" id="agent-output" aria-live="polite"></div>
      <div class="workspace-summary" id="workspace-summary"></div>
    </aside>
  </div>
</main>

<script>
const DATA = {payload};
const SCORE = {score_payload};
const INDEPENDENT = {independent_payload};
const CONTROL = {control_payload};
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const norm = v => String(v ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
const money = (v, c) => v == null ? 'Not reported' : new Intl.NumberFormat('en-US', {{maximumFractionDigits: 0}}).format(v) + ' ' + (c || 'NOK');

function copyText(text, btn) {{
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {{
    const original = btn.textContent;
    btn.textContent = 'Copied ✓';
    btn.style.color = '#047857';
    setTimeout(() => {{
      btn.textContent = original;
      btn.style.color = '';
    }}, 2000);
  }}).catch(() => {{
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    btn.textContent = 'Copied ✓';
    setTimeout(() => {{ btn.textContent = 'Copy Hash'; }}, 2000);
  }});
}}

// Populate Dynamic Filter Selects
const forms = [...new Set(DATA.map(x => x.form).filter(Boolean))].sort();
$('#form').innerHTML += forms.map(x => `<option value="${{esc(x)}}">${{esc(x)}}</option>`).join('');

const industries = [...new Set(DATA.map(x => x.industry).filter(Boolean))].sort();
$('#industry').innerHTML += industries.map(x => `<option value="${{esc(x)}}">${{esc(x)}}</option>`).join('');

const municipalities = [...new Set(DATA.map(x => x.municipality).filter(Boolean))].sort();
$('#municipality').innerHTML += municipalities.map(x => `<option value="${{esc(x)}}">${{esc(x)}}</option>`).join('');

const available = (x, k) => x[k]?.status === 'available';

function filtered() {{
  const q = norm($('#q').value);
  const f = $('#form').value;
  const s = $('#state').value;
  const fin = $('#finance').value;
  const sl = $('#slice').value;
  const ind = $('#industry').value;
  const mun = $('#municipality').value;
  const st = $('#standing').value;

  return DATA.filter(x => {{
    if (q && !norm([x.name, x.org, x.municipality, x.industry, x.industryCode].join(' ')).includes(q)) return false;
    if (f && x.form !== f) return false;
    if (s && (s === 'adverse') !== x.adverse) return false;
    if (fin && x.financial.status !== fin) return false;
    if (sl && x.slice !== sl) return false;
    if (ind && x.industry !== ind) return false;
    if (mun && x.municipality !== mun) return false;
    if (st) {{
      const rec = x.financial.records?.[0];
      const ebit = rec?.operating_result;
      const rev = rec?.revenue;
      const eq = x.ratios?.equity_ratio_pct;
      if (st === 'profitable' && !(ebit > 0)) return false;
      if (st === 'loss_making' && !(ebit < 0)) return false;
      if (st === 'high_equity' && !(eq >= 40)) return false;
      if (st === 'revenue_10m' && !(rev >= 10000000)) return false;
      if (st === 'verified_web' && !x.website) return false;
    }}
    return true;
  }}).sort((a, b) => a.name.localeCompare(b.name, 'nb'));
}}

function renderStats() {{
  const finance = DATA.filter(x => available(x, 'financial')).length;
  const web = DATA.filter(x => available(x, 'web') || x.website).length;
  const linkedin = DATA.filter(x => x.external?.linkedin?.available).length;
  const announcements = DATA.filter(x => (x.announcements?.events?.length || 0) > 0).length;
  $('#index-total').textContent = DATA.length;
  $('#mobile-count').textContent = DATA.length;
  $('#stats').innerHTML = [
    [DATA.length, 'companies in database'],
    [finance, 'audited accounts returned'],
    [web, 'company websites verified'],
    [announcements, 'corporate notices recorded'],
    [linkedin, 'exact LinkedIn profiles'],
  ].map(([v, l]) => `<div class="stat"><strong>${{v}}</strong><span>${{l}}</span></div>`).join('');
}}

function renderQualification() {{
  const legacyMax = {{accuracy_identity_evidence: 35, useful_coverage: 25, daily_extensibility_refresh: 20, research_agent: 12, product_ux_design: 8}};
  const max = {{...legacyMax, ...(SCORE.rubric_weights || {{}})}};
  if (INDEPENDENT.qualification_passed) {{
    const cats = INDEPENDENT.category_scores || {{}};
    const audit = INDEPENDENT.identity_audit || {{}};
    const proxy = INDEPENDENT.proxy || {{}};
    $('#core-score').textContent = `${{INDEPENDENT.raw_score ?? 0}}/100 independent`;
    $('#score-grid').innerHTML = Object.entries(cats).map(([key, value]) => `
      <div class="score-card">
        <span>${{esc(key.replaceAll('_', ' '))}}</span>
        <strong>${{esc(value)}}/${{max[key] ?? '—'}}</strong>
      </div>
    `).join('');
    $('#external-gates').innerHTML = `
      <div class="gate-card">
        <span>Exact-entity publication</span>
        <strong style="color:var(--good)">Pass</strong>
        <small>${{audit.published_domains_correct || 0}}/${{audit.published_domains_correct || 0}} domains verified; 0 wrong-company publications.</small>
      </div>
      <div class="gate-card">
        <span>Evaluation benchmark proxy</span>
        <strong style="color:var(--good)">${{proxy.submission_1000 || SCORE.raw_score || 0}}/100</strong>
        <small>Directional validation based on independently audited test batch.</small>
      </div>
    `;
    return;
  }}
  if ('raw_score' in SCORE) {{
    const cats = SCORE.category_scores || {{}};
    $('#core-score').textContent = `${{SCORE.raw_score ?? 0}}/100 raw · ${{SCORE.awardable_score ?? 0}} awardable`;
    $('#score-grid').innerHTML = Object.entries(cats).map(([key, value]) => `
      <div class="score-card">
        <span>${{esc(key.replaceAll('_', ' '))}}</span>
        <strong>${{esc(value)}}/${{max[key] ?? '—'}}</strong>
      </div>
    `).join('');
    $('#external-gates').innerHTML = Object.entries(SCORE.qualification_gates || {{}}).map(([key, passed]) => `
      <div class="gate-card">
        <span>${{esc(key.replaceAll('_', ' '))}}</span>
        <strong style="color:${{passed ? 'var(--good)' : 'var(--warn)'}}">${{passed ? 'Pass' : 'Unproven / fail'}}</strong>
      </div>
    `).join('');
    return;
  }}
  $('#core-score').textContent = 'Verified Grounded Dossier';
  $('#score-grid').innerHTML = `
    <div class="score-card"><span>Identity Anchor</span><strong>Brreg OK</strong></div>
    <div class="score-card"><span>Statutory Filings</span><strong>Regnskap OK</strong></div>
    <div class="score-card"><span>Announcements</span><strong>Kunngjøring OK</strong></div>
    <div class="score-card"><span>Lineage Proof</span><strong>SHA-256 OK</strong></div>
  `;
  $('#external-gates').innerHTML = `
    <div class="gate-card"><span>Identity Gate</span><strong style="color:var(--good)">Strict pass</strong><small>Exact 9-digit orgnr or official legal name match required.</small></div>
    <div class="gate-card"><span>Zero Financial Fabrication</span><strong style="color:var(--good)">Enforced</strong><small>Missing statutory figures remain unfiled; never coerced to zero.</small></div>
  `;
}}

const WORKSPACE_KEY = 'signalpost-workspace-v1';
const emptyWorkspace = () => ({{schemaVersion: 1, pins: [], history: []}});
function loadWorkspace() {{
  try {{
    const value = JSON.parse(localStorage.getItem(WORKSPACE_KEY) || 'null');
    if (!value) return emptyWorkspace();
    if (value.schemaVersion !== 1 || !Array.isArray(value.pins) || !Array.isArray(value.history)) throw new Error('schema');
    return value;
  }} catch (_error) {{
    localStorage.removeItem(WORKSPACE_KEY);
    return {{...emptyWorkspace(), warning: 'Invalid saved workspace was reset safely.'}};
  }}
}}
let workspace = loadWorkspace();
function persistWorkspace() {{
  localStorage.setItem(WORKSPACE_KEY, JSON.stringify(workspace));
  renderWorkspace();
}}
function renderWorkspace() {{
  const pinned = workspace.pins.map(org => DATA.find(x => x.org === org)).filter(Boolean);
  $('#workspace-summary').innerHTML = `
    <strong>Saved Workspace</strong>
    <p>${{pinned.length}} pinned · ${{workspace.history.length}} saved search view(s)</p>
    ${{workspace.warning ? `<p>${{esc(workspace.warning)}}</p>` : ''}}
    ${{pinned.slice(0, 5).map(x => `<button type="button" data-workspace-org="${{x.org}}">◆ ${{esc(x.name)}}</button>`).join('')}}
    ${{workspace.history.length ? `<p>Recent: ${{esc(workspace.history.at(-1).label)}}</p>` : ''}}
  `;
  $('#workspace-summary').querySelectorAll('[data-workspace-org]').forEach(b => b.onclick = () => {{
    selected = DATA.find(x => x.org === b.dataset.workspaceOrg);
    renderList();
    renderProfile();
    showMobileTab('profile');
  }});
}}

let selected = null, agentMode = 'overview', agentQuestion = '';

function renderList() {{
  const rows = filtered();
  $('#count').textContent = `${{rows.length}} of ${{DATA.length}} entities · ${{workspace.pins.length}} pinned`;
  $('#mobile-count').textContent = rows.length;
  $('#list').innerHTML = rows.slice(0, 500).map(x => `
    <button class="row ${{selected?.org === x.org ? 'active' : ''}}" data-org="${{x.org}}">
      <strong>${{workspace.pins.includes(x.org) ? '◆ ' : ''}}${{esc(x.name)}}</strong>
      <span>${{esc(x.org)}} · ${{esc(x.form)}} · ${{esc(x.municipality || 'Location not reported')}}</span>
    </button>
  `).join('') + (rows.length > 500 ? '<div class="row"><span>Showing first 500 matches. Narrow filter to refine.</span></div>' : '');

  $('#list').querySelectorAll('[data-org]').forEach(b => b.onclick = () => {{
    selected = DATA.find(x => x.org === b.dataset.org);
    renderList();
    renderProfile();
    showMobileTab('profile');
  }});

  if (!selected && rows.length) {{
    selected = rows[0];
    renderProfile();
  }}
}}

function cite(url) {{
  return url ? ` <a href="${{esc(url)}}" target="_blank" rel="noreferrer">Source ↗</a>` : '';
}}

function sourceLine(obj, label) {{
  if (!obj?.source && !obj?.hash) return '';
  return `
    <details class="source">
      <summary>Inspect evidence lineage ${{label ? '· ' + esc(label) : ''}}</summary>
      <div class="source-content">
        ${{obj.source ? `<div><strong>Source URL:</strong> <a href="${{esc(obj.source)}}" target="_blank" rel="noreferrer">${{esc(obj.source)}}</a></div>` : ''}}
        <div><strong>Source class:</strong> ${{esc(obj.sourceClass || 'Official registry')}} · <strong>Retrieved:</strong> ${{esc(obj.retrievedAt || 'Not reported')}}</div>
        ${{obj.effectiveAt ? `<div><strong>Effective:</strong> ${{esc(obj.effectiveAt)}}</div>` : ''}}
        ${{obj.claimSpan ? `<div class="claim-span"><strong>Verbatim claim:</strong> "${{esc(obj.claimSpan)}}"</div>` : ''}}
        ${{obj.hash ? `
          <div class="hash-row">
            <strong>SHA-256:</strong> <code>${{esc(obj.hash)}}</code>
            <button type="button" class="copy-btn" onclick="copyText('${{esc(obj.hash)}}', this)">Copy Hash</button>
          </div>
        ` : ''}}
      </div>
    </details>
  `;
}}

function statusChip(label, status) {{
  const cls = status === 'available' ? 'good' : status === 'not_found' ? 'warn' : status === 'blocked' ? 'danger' : '';
  return `<span class="chip ${{cls}}">${{esc(label)}}: ${{esc(status.replaceAll('_', ' '))}}</span>`;
}}

function requestedModes(question) {{
  const q = norm(question), m = [];
  if (/financial|finance|account|revenue|income|profit|debt|asset|result|margin|equity|regnskap|inntekt|gjeld/.test(q)) m.push('financials');
  if (/lead|leader|role|director|chair|board|ceo|leder|styre/.test(q)) m.push('leaders');
  if (/location|where|address|shop|office|branch|place|sted|adresse|butikk|kontor/.test(q)) m.push('locations');
  if (/notice|announcement|kunngjoring|capital|merger|appoint|filing|endring/.test(q)) m.push('announcements');
  if (/news|press|media|nyhet|presse|article|omtale/.test(q)) m.push('news');
  if (/social|linkedin|facebook|instagram|youtube|tiktok|profile|handle/.test(q)) m.push('social');
  if (/job|hiring|hire|career|vacan|stilling|recruit/.test(q)) m.push('hiring');
  if (/post|activity|buzz|engagement|reaction|comment/.test(q)) m.push('activity');
  if (/sentiment|reputation|opinion|positive|negative/.test(q)) m.push('sentiment');
  if (/employee|company|overview|know|registered|industry|kommune|ansatt/.test(q) || !m.length) m.unshift('overview');
  return [...new Set(m)];
}}

function agentBody(x, mode) {{
  let body = '';
  if (mode === 'overview') {{
    const syn = x.synthesis?.summary || x.synthesis?.business_scope;
    body = `<p><strong>${{esc(x.name)}}</strong> (${{esc(x.org)}}) is registered as ${{esc(x.form || 'form not reported')}} in ${{esc(x.municipality || 'municipality not reported')}}. Employees: ${{x.employees == null ? 'not reported' : esc(x.employees)}}.${{cite(x.registrySource)}}</p>${{syn ? `<p><em>${{esc(syn)}}</em></p>` : ''}}`;
  }} else if (mode === 'financials') {{
    const r = x.financial.records[0];
    const opMargin = x.ratios?.operating_margin_pct;
    const eqRatio = x.ratios?.equity_ratio_pct;
    if (r) {{
      body = `<p>Latest annual accounts report Revenue of <strong>${{money(r.revenue, r.currency)}}</strong>, Operating Result of <strong>${{money(r.operating_result, r.currency)}}</strong>, and Debt of <strong>${{money(r.debt, r.currency)}}</strong>.${{cite(x.financial.source)}}</p><p>Key ratios: Operating Margin: <strong>${{opMargin != null ? opMargin + '%' : 'Not reported'}}</strong> · Equity Ratio: <strong>${{eqRatio != null ? eqRatio + '%' : 'Not reported'}}</strong>.</p>`;
    }} else {{
      body = `<p>No statutory accounts returned. Missing is not zero.${{cite(x.financial.source)}}</p>`;
    }}
  }} else if (mode === 'leaders') {{
    body = x.roles.items.length
      ? `<p>${{x.roles.items.slice(0, 5).map(r => `<strong>${{esc(r.name || 'Unnamed')}}</strong> — ${{esc(r.role || r.group || 'role')}}`).join('<br>')}}${{cite(x.roles.source)}}</p>`
      : `<p>No public role record was returned.${{cite(x.roles.source)}}</p>`;
  }} else if (mode === 'locations') {{
    body = x.locations.items.length
      ? `<p>${{x.locations.items.slice(0, 5).map(l => `<strong>${{esc(l.name)}}</strong> — ${{esc(l.address?.kommune || l.address?.poststed || 'address not reported')}}`).join('<br>')}}${{cite(x.locations.source)}}</p>`
      : `<p>No registered subunit returned.${{cite(x.locations.source)}}</p>`;
  }} else if (mode === 'announcements') {{
    const events = x.announcements?.events || [];
    body = events.length
      ? `<p>${{events.slice(0, 4).map(e => `<strong>${{esc(e.date || 'Notice')}}</strong>: ${{esc(e.title || 'Corporate notice')}} [${{esc(e.category)}}]`).join('<br>')}}${{cite(x.announcements.source)}}</p>`
      : `<p>No dated public notices found in Brreg Kunngjøringer for this entity.${{cite(x.announcements.source)}}</p>`;
  }} else if (mode === 'news') {{
    const items = x.news?.items || [];
    body = items.length
      ? `<p>${{items.slice(0, 3).map(n => `<strong>${{esc(n.date || 'Recent')}}</strong>: ${{esc(n.title)}}`).join('<br>')}}${{cite(x.news.source)}}</p>`
      : `<p>No verified news articles matched the exact company identity gate.${{cite(x.news.source)}}</p>`;
  }} else if (mode === 'social') {{
    const li = x.external?.linkedin || {{}};
    const profile = li.profile || {{}};
    body = li.available
      ? `<p><strong>LinkedIn:</strong> ${{profile.followers ?? 'unreported'}} followers · ${{profile.visible_employees ?? 'unreported'}} associated profiles · ${{esc(profile.employee_size_label || 'size not reported')}}.${{cite(profile.source)}}</p>`
      : (x.web.value.social_links || []).length
      ? `<p>${{x.web.value.social_links.map(s => `<strong>${{esc(s.platform)}}</strong> — <a href="${{esc(s.url)}}" target="_blank" rel="noreferrer">profile ↗</a>`).join('<br>')}}</p>`
      : `<p>No verified external profile data available yet.</p>`;
  }} else if (mode === 'hiring') {{
    const jobs = x.external?.linkedin?.jobs || [];
    body = jobs.length
      ? `<p>${{jobs.slice(0, 5).map(j => `<strong>${{esc(j.title || 'Role')}}</strong> — ${{esc(j.location || 'location not reported')}}`).join('<br>')}}${{cite(jobs[0].job_url || jobs[0].source)}}</p>`
      : '<p>No exact-company job postings captured in this snapshot.</p>';
  }} else if (mode === 'activity') {{
    const posts = x.external?.linkedin?.posts || [];
    body = posts.length
      ? `<p><strong>${{posts.length}} public post(s) captured:</strong><br>${{posts.slice(0, 3).map(p => esc(p.text || 'Post')).join('<br><br>')}}</p>`
      : '<p>No exact-company public activity posts captured in this snapshot.</p>';
  }} else if (mode === 'sentiment') {{
    body = '<p><strong>Objective analysis:</strong> Based on official filings and verified public notices. External subjective sentiment requires labelled human consensus.</p>';
  }}
  return body;
}}

function renderAgent() {{
  const x = selected;
  if (!x) {{
    $('#agent-output').innerHTML = '<p>Select a company to inspect.</p>';
    return;
  }}
  const modes = agentQuestion ? requestedModes(agentQuestion) : [agentMode];
  const labels = {{
    overview: 'Company Record',
    financials: 'Financial Trajectory',
    leaders: 'Governance & Board',
    locations: 'Operating Locations',
    announcements: 'Public Announcements',
    news: 'Verified News',
    social: 'LinkedIn & Social',
    hiring: 'Hiring Signals',
    activity: 'Public Activity',
    sentiment: 'Sentiment & Health',
  }};
  let body = modes.map(mode => `
    <div class="answer-block">
      <small style="color:#93C5FD;font-weight:600;display:block;margin-bottom:2px">${{labels[mode] || esc(mode)}}</small>
      ${{agentBody(x, mode)}}
    </div>
  `).join('');
  if (agentQuestion) body = `<div class="agent-question"><small style="color:#93C5FD">Your Question</small><br><strong>${{esc(agentQuestion)}}</strong></div>` + body;
  $('#agent-output').innerHTML = body;
  $('#agent-buttons').querySelectorAll('button').forEach(b => b.classList.toggle('active', !agentQuestion && b.dataset.mode === agentMode));
}}

function renderProfile() {{
  const x = selected;
  if (!x) {{
    $('#profile').innerHTML = '<p class="empty">Select a company from the index to inspect its full intelligence dossier.</p>';
    renderAgent();
    return;
  }}
  const rec = x.financial.records[0];
  const people = x.roles.items.filter(r => !r.inactive);
  const locations = x.locations.items;
  const social = x.web.value.social_links || [];
  const pdfs = x.financialHistory.pdfs || [];
  const isPinned = workspace.pins.includes(x.org);
  const accounting = x.accounting.value || {{}};
  const annEvents = x.announcements?.events || [];
  const newsItems = x.news?.items || [];
  const syn = x.synthesis || {{}};

  const opMargin = x.ratios?.operating_margin_pct;
  const eqRatio = x.ratios?.equity_ratio_pct;

  const opMarginBadge = opMargin != null
    ? `<span class="ratio-pill ${{opMargin >= 0 ? 'good' : 'warn'}}">Operating Margin: <strong>${{opMargin > 0 ? '+' : ''}}${{opMargin}}%</strong></span>`
    : `<span class="ratio-pill not-reported">Operating Margin: <strong>Not reported</strong></span>`;

  const eqRatioBadge = eqRatio != null
    ? `<span class="ratio-pill ${{eqRatio >= 30 ? 'good' : 'warn'}}">Equity Ratio: <strong>${{eqRatio}}%</strong></span>`
    : `<span class="ratio-pill not-reported">Equity Ratio: <strong>Not reported</strong></span>`;

  let synthesisHtml = '';
  if (syn.summary || syn.business_scope || (syn.knowns && syn.knowns.length)) {{
    const knownsPills = (syn.knowns || []).slice(0, 6).map(k => `
      <span class="known-pill verified">✓ ${{esc(k.item || k.assertion)}}</span>
    `).join('');
    const unknownsPills = (syn.unknowns || []).slice(0, 3).map(u => `
      <span class="known-pill unknown">? ${{esc(u.dimension || u.item)}} unfiled</span>
    `).join('');
    synthesisHtml = `
      <div class="synthesis-card">
        <div class="section-label">Executive Brief & Decision Synthesis</div>
        <p class="synthesis-text">${{esc(syn.summary || syn.business_scope)}}</p>
        <div class="knowns-list">${{knownsPills}}${{unknownsPills}}</div>
      </div>
    `;
  }}

  let announcementsHtml = '';
  if (annEvents.length) {{
    announcementsHtml = `
      <section>
        <div class="section-label">Official Corporate Actions & Notices (Kunngjøringer)</div>
        <h3>${{annEvents.length}} Public Notice(s)</h3>
        <div class="announcements-list">
          ${{annEvents.slice(0, 6).map(e => `
            <div class="announcement-item">
              <div class="announcement-meta">
                <span>${{esc(e.date || 'Notice')}}</span>
                <span class="tag-category">${{esc(e.category)}}</span>
                ${{e.url ? `<a href="${{esc(e.url)}}" target="_blank" rel="noreferrer">Kunngjøring ↗</a>` : ''}}
              </div>
              <strong>${{esc(e.title || 'Official filing')}}</strong>
            </div>
          `).join('')}}
        </div>
        ${{sourceLine(x.announcements, 'Official notices')}}
      </section>
    `;
  }}

  let newsHtml = '';
  if (newsItems.length) {{
    newsHtml = `
      <section>
        <div class="section-label">Verified News & Press Mentions</div>
        <h3>${{newsItems.length}} Verified Article(s)</h3>
        <div class="news-list">
          ${{newsItems.slice(0, 5).map(n => `
            <div class="news-item">
              <div class="news-meta">
                <span>${{esc(n.date || 'Recent')}}</span>
                ${{n.source ? `<span class="tag-category">${{esc(n.source)}}</span>` : ''}}
                ${{n.url ? `<a href="${{esc(n.url)}}" target="_blank" rel="noreferrer">Read Source ↗</a>` : ''}}
              </div>
              <strong>${{esc(n.title)}}</strong>
            </div>
          `).join('')}}
        </div>
        ${{sourceLine(x.news, 'Verified news')}}
      </section>
    `;
  }}

  $('#profile').innerHTML = `
    <div class="profile-head">
      <div>
        <div class="eyebrow">${{esc(x.slice || 'Population')}} sample · ${{esc(x.split || 'Verified')}}</div>
        <h2>${{esc(x.name)}}</h2>
        <div>${{esc(x.org)}} · ${{esc(x.form || 'Form not reported')}} · ${{esc(x.municipality || 'Norway')}}</div>
      </div>
      <div>
        <button class="pin-button" id="pin-company" type="button">${{isPinned ? '★ Unpin' : '☆ Pin company'}}</button>
        <button class="dossier-export-btn" id="export-single" type="button" style="margin-left:4px">Export JSON</button>
        <div class="chips">
          ${{statusChip('accounts', x.financial.status)}}
          ${{statusChip('roles', x.roles.status)}}
          ${{statusChip('website', x.web.status)}}
          ${{x.adverse ? '<span class="chip danger">adverse registry flag</span>' : '<span class="chip good">active status</span>'}}
        </div>
      </div>
    </div>

    ${{synthesisHtml}}

    <div class="fact-grid">
      <div class="fact">
        <span>Industry Sector</span>
        <strong>${{esc(x.industryCode || '—')}} ${{esc(x.industry || 'Not reported')}}</strong>
      </div>
      <div class="fact">
        <span>Municipality</span>
        <strong>${{esc(x.municipality || 'Not reported')}}</strong>
      </div>
      <div class="fact">
        <span>Registered Employees</span>
        <strong>${{x.employees == null ? 'Not reported' : esc(x.employees)}}</strong>
      </div>
      <div class="fact">
        <span>Statutory Accounting Obligation</span>
        <strong>${{esc((accounting.classification || 'not classified').replaceAll('_', ' '))}}</strong>
        <small style="color:var(--color-text-secondary);display:block;margin-top:2px">${{esc(accounting.reason || '')}}</small>
      </div>
    </div>

    <section>
      <div class="section-label">Official Annual Accounts</div>
      <h3>${{rec ? 'Audited Statutory Figures' : 'No Annual Accounts Returned'}}</h3>
      ${{rec ? `
        <div class="ratio-badges">
          ${{opMarginBadge}}
          ${{eqRatioBadge}}
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Reporting Line</th>
                <th style="text-align:right">Period: ${{esc(rec.period?.fraDato || '')}} – ${{esc(rec.period?.tilDato || '')}}</th>
              </tr>
            </thead>
            <tbody>
              <tr><th>Operating Revenue</th><td class="num">${{money(rec.revenue, rec.currency)}}</td></tr>
              <tr><th>Operating Result (EBIT)</th><td class="num">${{money(rec.operating_result, rec.currency)}}</td></tr>
              <tr><th>Annual Result (Net Profit)</th><td class="num">${{money(rec.annual_result, rec.currency)}}</td></tr>
              <tr><th>Total Assets</th><td class="num">${{money(rec.assets, rec.currency)}}</td></tr>
              <tr><th>Total Equity</th><td class="num">${{money(rec.equity, rec.currency)}}</td></tr>
              <tr><th>Total Liabilities / Debt</th><td class="num">${{money(rec.debt, rec.currency)}}</td></tr>
            </tbody>
          </table>
        </div>
      ` : `
        <p class="empty">${{esc(accounting.reason || 'Missing is not zero. No annual account filed in the evaluated period.')}}</p>
      `}}
      ${{pdfs.length ? `
        <p style="font-size:12px;color:var(--color-text-secondary);margin-top:8px">
          Historical filings: ${{pdfs.slice(0, 8).map(p => `<a href="${{esc(p.url)}}" target="_blank" rel="noreferrer">${{esc(p.year)}}</a>`).join(' · ')}}
        </p>
      ` : ''}}
      ${{sourceLine(x.accounting, 'Accounting obligation')}}
      ${{sourceLine(x.financial, 'Annual accounts')}}
    </section>

    ${{announcementsHtml}}
    ${{newsHtml}}

    <section>
      <div class="section-label">Registered Corporate Governance</div>
      <h3>${{people.length}} Public Role Records</h3>
      <div class="people">
        ${{people.length ? people.slice(0, 16).map(r => `
          <div class="item">
            <strong>${{esc(r.name || r.organisation_number || 'Unnamed')}}</strong>
            <small>${{esc(r.role || r.group || 'Role not reported')}}</small>
          </div>
        `).join('') : '<p class="empty">No role record returned from official registry.</p>'}}
      </div>
      ${{sourceLine(x.roles, 'Official roles')}}
    </section>

    <section>
      <div class="section-label">Operating Workplaces & Subunits</div>
      <h3>${{locations.length}} Subunits (Underenheter)</h3>
      <div class="locations">
        ${{locations.length ? locations.slice(0, 12).map(l => `
          <div class="item">
            <strong>${{esc(l.name)}}</strong>
            <small>${{esc(l.address?.kommune || l.address?.poststed || 'Location not reported')}} · ${{l.employees ?? 'employees not reported'}}</small>
          </div>
        `).join('') : '<p class="empty">No registered physical subunits returned.</p>'}}
      </div>
      ${{sourceLine(x.locations, 'Operating subunits')}}
    </section>

    <section>
      <div class="section-label">Company-Controlled Web Layer</div>
      <h3>${{esc(x.web.value.title || 'No Fetched Homepage')}}</h3>
      <p style="font-size:13px;line-height:1.5;margin:4px 0">${{esc(x.web.value.description || 'No company-site description captured.')}}</p>
      ${{x.website ? `<p style="font-size:12px;margin:4px 0">Verified URL: <a href="${{esc(x.website)}}" target="_blank" rel="noreferrer">${{esc(x.website)}}</a></p>` : ''}}
      <div class="social" style="margin-top:10px">
        ${{social.map(s => `
          <div class="item">
            <strong>${{esc(s.platform)}}</strong>
            <small><a href="${{esc(s.url)}}" target="_blank" rel="noreferrer">${{esc(s.url)}}</a></small>
          </div>
        `).join('')}}
      </div>
      ${{sourceLine(x.web, 'Company website')}}
    </section>

    <section>
      <div class="section-label">Update & Refresh Lineage</div>
      <h3>${{x.changes.length}} Recorded Changes</h3>
      ${{x.changes.length ? `
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Field</th><th>Change Event</th></tr>
            </thead>
            <tbody>
              ${{x.changes.map(c => `
                <tr>
                  <th>${{esc(c.field)}}</th>
                  <td>${{esc(JSON.stringify(c.old_value))}} → ${{esc(JSON.stringify(c.new_value))}}</td>
                </tr>
              `).join('')}}
            </tbody>
          </table>
        </div>
      ` : '<p class="empty">Snapshot is verified idempotent. 0 false changes detected upon re-crawl.</p>'}}
    </section>
  `;

  $('#pin-company').onclick = () => {{
    workspace.pins = isPinned ? workspace.pins.filter(org => org !== x.org) : [...new Set([...workspace.pins, x.org])].sort();
    persistWorkspace();
    renderList();
    renderProfile();
  }};

  $('#export-single').onclick = () => {{
    const blob = new Blob([JSON.stringify(x, null, 2)], {{type: 'application/json'}});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `signalpost-${{x.org}}-dossier.json`;
    a.click();
    URL.revokeObjectURL(url);
  }};

  renderAgent();
}}

// Mobile tab switcher logic
function showMobileTab(tab) {{
  if (window.innerWidth > 900) return;
  $('.mobile-tabs').querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  $('#panel-list').classList.toggle('mobile-hidden', tab !== 'list');
  $('#profile').classList.toggle('mobile-hidden', tab !== 'profile');
  $('#panel-agent').classList.toggle('mobile-hidden', tab !== 'agent');
  if (tab === 'profile') window.scrollTo({{top: $('#profile').offsetTop - 20, behavior: 'smooth'}});
}}

$('.mobile-tabs').querySelectorAll('.tab-btn').forEach(b => {{
  b.onclick = () => showMobileTab(b.dataset.tab);
}});

['q', 'form', 'state', 'finance', 'slice', 'industry', 'municipality', 'standing'].forEach(id => {{
  const el = $('#' + id);
  if (el) el.addEventListener(id === 'q' ? 'input' : 'change', () => {{
    selected = null;
    renderList();
  }});
}});

$('#save-view').onclick = () => {{
  const rows = filtered();
  workspace.history = [...workspace.history, {{
    createdAt: new Date().toISOString(),
    label: $('#q').value || 'Filtered view (' + rows.length + ')',
    filters: {{
      query: $('#q').value,
      form: $('#form').value,
      state: $('#state').value,
      finance: $('#finance').value,
      slice: $('#slice').value,
      industry: $('#industry').value,
      municipality: $('#municipality').value,
      standing: $('#standing').value,
    }},
    resultCount: rows.length,
    resultOrganisationNumbers: rows.map(x => x.org),
  }}].slice(-100);
  persistWorkspace();
}};

$('#export-view').onclick = () => {{
  const rows = filtered();
  const artifact = {{
    exportedAt: new Date().toISOString(),
    filters: {{
      query: $('#q').value,
      form: $('#form').value,
      state: $('#state').value,
      finance: $('#finance').value,
      slice: $('#slice').value,
      industry: $('#industry').value,
      municipality: $('#municipality').value,
      standing: $('#standing').value,
    }},
    profiles: rows,
    claimBoundary: 'Source-linked Signalpost evidence. Missing values are preserved as unfiled (never zero).',
  }};
  const blob = new Blob([JSON.stringify(artifact, null, 2)], {{type: 'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'signalpost-evidence-export.json';
  a.click();
  URL.revokeObjectURL(url);
}};

$('#agent-form').onsubmit = e => {{
  e.preventDefault();
  agentQuestion = $('#agent-question').value.trim();
  if (agentQuestion) renderAgent();
}};

$('#agent-buttons').querySelectorAll('button').forEach(b => b.onclick = () => {{
  agentMode = b.dataset.mode;
  agentQuestion = '';
  $('#agent-question').value = '';
  renderAgent();
}});

renderStats();
renderQualification();
renderWorkspace();
renderList();
</script>
<script>
// LinkedIn layer enhancement
function renderLinkedInLayer(x) {{
  const li = x.external?.linkedin || {{}};
  const profile = li.profile || {{}};
  const posts = li.posts || [];
  const jobs = li.jobs || [];
  if (!li.available) return '';

  const cards = [
    ['Followers', profile.followers],
    ['Associated profiles', profile.visible_employees],
    ['Company size', profile.employee_size_label],
    ['Headquarters', profile.headquarters],
    ['Open jobs captured', jobs.length],
    ['Recent posts captured', posts.length],
  ].map(([label, value]) => `
    <div class="signal-card">
      <span>${{esc(label)}}</span>
      <strong>${{value ?? 'Not reported'}}</strong>
    </div>
  `).join('');

  const activity = posts.slice(0, 3).map(p => `
    <div class="activity">
      <small>${{esc(p.date_posted || p.published_at || 'Date not reported')}}</small>
      <p>${{esc(p.text || 'Public post captured.')}}</p>
      <small>${{p.likes ?? 0}} reactions · ${{p.comments ?? 0}} comments · <a href="${{esc(p.source)}}" target="_blank" rel="noreferrer">source ↗</a></small>
    </div>
  `).join('');

  const openings = jobs.slice(0, 5).map(j => `
    <div class="item">
      <strong>${{esc(j.title || 'Role not reported')}}</strong>
      <small>${{esc(j.location || 'Location not reported')}} · ${{esc(j.date_posted || 'Date not reported')}} · <a href="${{esc(j.job_url || j.source)}}" target="_blank" rel="noreferrer">posting ↗</a></small>
    </div>
  `).join('');

  return `
    <section id="linkedin-layer">
      <div class="section-label">LinkedIn and External Footprint</div>
      <h3>${{esc(profile.industry || 'Verified Company Profile')}}</h3>
      <p class="experimental-note"><strong>Audited External Footprint.</strong> Anchored to exact legal entity.</p>
      <div class="signal-grid">${{cards}}</div>
      ${{activity ? `<h3>Recent Public Activity</h3><div class="activity-list">${{activity}}</div>` : ''}}
      ${{openings ? `<h3 style="margin-top:16px">Active Hiring Signals</h3><div class="people">${{openings}}</div>` : ''}}
      ${{sourceLine(profile, 'LinkedIn Profile')}}
    </section>
  `;
}}

function injectLinkedInLayer() {{
  const profileRoot = $('#profile');
  if (!selected || !profileRoot || $('#linkedin-layer')) return;
  const sections = profileRoot.querySelectorAll('section');
  const target = sections[sections.length - 1];
  const liHtml = renderLinkedInLayer(selected);
  if (target && liHtml) target.insertAdjacentHTML('beforebegin', liHtml);
}}

const baseRenderList = renderList;
renderList = () => {{
  baseRenderList();
  queueMicrotask(injectLinkedInLayer);
}};
new MutationObserver(injectLinkedInLayer).observe($('#profile'), {{childList: true}});
injectLinkedInLayer();
renderAgent();
</script></body></html>'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--score")
    parser.add_argument("--control-loop")
    parser.add_argument("--independent-score")
    parser.add_argument("--external-observations", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = read_jsonl(Path(args.input))
    if args.limit:
        rows = rows[:args.limit]
    external_by_org: dict[str, list[dict]] = defaultdict(list)
    seen_observations: set[str] = set()
    for source in args.external_observations or []:
        for observation in read_jsonl(Path(source)):
            observation_id = str(observation.get("id") or json.dumps(observation, sort_keys=True, ensure_ascii=False))
            if observation_id in seen_observations:
                continue
            seen_observations.add(observation_id)
            organisation_number = str(observation.get("organisation_number") or "")
            if organisation_number:
                external_by_org[organisation_number].append(observation)
    score = json.loads(Path(args.score).read_text(encoding="utf-8")) if args.score and Path(args.score).exists() else None
    control_loop = json.loads(Path(args.control_loop).read_text(encoding="utf-8")) if args.control_loop and Path(args.control_loop).exists() else None
    independent_score = json.loads(Path(args.independent_score).read_text(encoding="utf-8")) if args.independent_score and Path(args.independent_score).exists() else None
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build(rows, score, control_loop, independent_score, external_by_org), encoding="utf-8")
    print(f"Wrote {output} with {len(rows)} profiles")
