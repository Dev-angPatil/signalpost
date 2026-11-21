"""What the company browser shows from the scored envelopes.

The envelope is what Builderr grades, so every "Verify" panel, coverage state and
run check on the page is read from it, never re-derived from the profile:

- verify_payload: each published claim with the evidence records it cites
  (source address, source class, retrieval time, reporting period, SHA-256 and the
  verbatim span);
- coverage: the five areas a reader looks for, each with its availability state
  and a plain reason when it is not available;
- run_summary: run metadata, coverage per area and checks recomputed from the
  envelopes themselves.
"""
from __future__ import annotations

import json
import re
from typing import Any

AREAS = (
    ("company", "Company record"),
    ("accounts", "Annual accounts"),
    ("people", "People & places"),
    ("website", "Website"),
    ("footprint", "Public footprint"),
)

# Most informative state first when an area has several claims.
_STATE_ORDER = ("available", "ambiguous", "blocked", "failed", "not_applicable", "not_available")

REASONS = {
    "not_available": "Looked for in the checked sources and not found.",
    "ambiguous": "A candidate was found but could not be tied to this exact company, so nothing from it is published.",
    "blocked": "The source's robots.txt or access policy does not allow our crawler.",
    "failed": "The source could not be read (timeout or rate limit, after one retry).",
    "not_applicable": "Does not apply to this kind of entity.",
}

_GROUPS = (
    ("summary", "Summary", re.compile(r"^executive_summary$")),
    ("accounts", "Annual accounts", re.compile(
        r"^(revenue|sales_revenue|operating_|annual_|profit_|net_|tax_|wage_|assets|current_assets|fixed_assets|cash_|"
        r"receivables|equity|debt|short_term_debt|long_term_debt|currency|latest_financial_year|latest_submitted_accounts|"
        r"accounts_|audit_|multi_year_|debt_to_)")),
    ("people", "People & places", re.compile(
        r"^(ceo|board_chair|leadership|locations|registered_workplaces_count|latest_board_appointment_date)$")),
    ("website", "Website & profiles", re.compile(r"^(official_website|website_|contact_|social_|external_company_site)")),
    ("activity", "Hiring & dated activity", re.compile(
        r"^(job_posting|open_positions|hiring_active|career_url|news_|latest_news_|external_activity|external_job_board|"
        r"corporate_action_|latest_corporate_action|corporate_announcements_count|latest_legal_filing_date|"
        r"latest_capital_change_date|latest_insolvency_date)")),
)
GROUP_LABELS = {key: label for key, label, _ in _GROUPS} | {"company": "Company record"}

_MAX_VALUE_CHARS = 600


def claim_group(field: str) -> str:
    for key, _, pattern in _GROUPS:
        if pattern.search(field):
            return key
    return "company"


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = json.dumps(value)
    elif isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value):
        text = ", ".join(str(v) for v in value)
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _MAX_VALUE_CHARS else text[: _MAX_VALUE_CHARS - 1] + "…"


def _period(record: dict[str, Any]) -> str | None:
    period = record.get("reporting_period")
    if isinstance(period, dict) and (period.get("from") or period.get("to")):
        return f"{period.get('from') or '?'} to {period.get('to') or '?'}"
    if isinstance(period, str) and period:
        return period
    return None


def verify_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    """Published claims and the evidence they cite, compacted for the page.

    claims: [field, group, value text, confidence, [evidence ids]]
    evidence: {id: [source_url, source_class, retrieved_at, sha256, claim_span, period, effective_at]}
    """
    evidence = {
        str(ev.get("id")): [
            ev.get("source_url"), ev.get("source_class"), ev.get("retrieved_at"), ev.get("content_sha256"),
            ev.get("claim_span"), _period(ev), ev.get("effective_at"),
        ]
        for ev in envelope.get("evidence") or [] if isinstance(ev, dict) and ev.get("id")
    }
    claims = []
    cited: set[str] = set()
    for claim in envelope.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("availability") != "available":
            continue
        field = str(claim.get("field") or "")
        ids = [str(i) for i in claim.get("evidence_ids") or [] if str(i) in evidence]
        cited.update(ids)
        claims.append([field, claim_group(field), _value_text(claim.get("value")), claim.get("confidence"), ids])
    return {"claims": claims, "evidence": {k: v for k, v in evidence.items() if k in cited}}


def _state(claims: dict[str, dict[str, Any]], fields: list[str]) -> str:
    states = [claims[f].get("availability") for f in fields if f in claims]
    for state in _STATE_ORDER:
        if state in states:
            return state
    return "not_available"


def coverage(envelope: dict[str, Any]) -> list[dict[str, str]]:
    """The five areas with their availability state and, when not available, why."""
    def rank(claim: dict[str, Any] | None) -> int:
        state = (claim or {}).get("availability")
        return _STATE_ORDER.index(state) if state in _STATE_ORDER else len(_STATE_ORDER)

    claims: dict[str, dict[str, Any]] = {}
    for claim in envelope.get("claims") or []:
        if isinstance(claim, dict) and claim.get("field"):
            field = claim["field"]
            if field not in claims or rank(claim) < rank(claims[field]):  # a repeated field keeps its best state
                claims[field] = claim
    footprint_fields = [f for f in claims if re.match(r"^(social_|news_\d+_title|latest_news_title|job_posting(_\d+)?_title|open_positions$|external_activity_title)", f)]
    website_state = _state(claims, ["official_website"])
    states = {
        "company": _state(claims, ["legal_name"]),
        "accounts": _state(claims, ["latest_financial_year", "annual_result", "revenue", "assets"]),
        "people": _state(claims, ["ceo", "board_chair", "leadership", "locations"]),
        "website": website_state,
    }
    footprint = _state(claims, footprint_fields) if footprint_fields else "not_available"
    if footprint != "available" and website_state in ("blocked", "failed", "ambiguous"):
        footprint = website_state  # profiles, news and jobs on the site could not be read either
    states["footprint"] = footprint
    out = []
    for key, label in AREAS:
        state = states[key]
        reason = "" if state == "available" else REASONS.get(state, REASONS["not_available"])
        if key == "footprint" and state == "not_available":
            reason = "No company-owned social profile, dated news or open job posting was found in the checked sources."
        out.append({"key": key, "label": label, "state": state, "reason": reason})
    return out


def published_facts_are_backed(envelope: dict[str, Any]) -> bool:
    """Every available claim cites at least one evidence record with a source address and a SHA-256."""
    evidence = {str(ev.get("id")): ev for ev in envelope.get("evidence") or [] if isinstance(ev, dict)}
    for claim in envelope.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("availability") != "available":
            continue
        ids = claim.get("evidence_ids") or []
        if not ids:
            return False
        for i in ids:
            ev = evidence.get(str(i))
            if not ev or not ev.get("source_url") or not re.fullmatch(r"[0-9a-f]{64}", str(ev.get("content_sha256") or "")):
                return False
    return True


def unavailable_claims_carry_no_value(envelope: dict[str, Any]) -> bool:
    """A value on a claim that is not `available` would read as an unevidenced claim."""
    return all(
        claim.get("value") in (None, "", [], {})
        for claim in envelope.get("claims") or []
        if isinstance(claim, dict) and claim.get("availability") != "available"
    )


def run_summary(envelopes: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run facts and checks recomputed from the envelopes, plus what the run passes in."""
    meta = dict(meta or {})
    per_area = {key: 0 for key, _ in AREAS}
    cant_tell = 0
    published = 0
    for env in envelopes:
        areas = coverage(env)
        for area in areas:
            if area["state"] == "available":
                per_area[area["key"]] += 1
        if any(a["state"] == "ambiguous" for a in areas):
            cant_tell += 1
        published += sum(1 for c in env.get("claims") or [] if isinstance(c, dict) and c.get("availability") == "available")
    orgs = [str(env.get("organisation_number") or "") for env in envelopes]
    expected = meta.get("expected_count")
    checks = [
        {"label": "One terminal envelope per input company",
         "ok": len(set(orgs)) == len(orgs) and (expected is None or len(orgs) == expected)},
        {"label": "Every published fact cites a source with a SHA-256 hash", "ok": all(published_facts_are_backed(env) for env in envelopes)},
        {"label": "Nothing marked unavailable carries a value", "ok": all(unavailable_claims_carry_no_value(env) for env in envelopes)},
    ]
    for key, label in (("zero_wrong_company_matches", "No company matched to another company's site"),
                       ("zero_fabricated_financials", "No financial value invented or set to zero"),
                       ("request_budget_within_limit", "Within the request budget"),
                       ("cost_budget_within_limit", "Within the cost budget"),
                       ("runtime_budget_within_limit", "Within the runtime budget")):
        if key in (meta.get("qualification") or {}):
            checks.append({"label": label, "ok": bool(meta["qualification"][key])})
    return {
        "run_id": meta.get("run_id"),
        "completed_at": meta.get("completed_at"),
        "commit": meta.get("commit"),
        "companies": len(envelopes),
        "published_facts": published,
        "requests": meta.get("requests"),
        "runtime_seconds": meta.get("runtime_seconds"),
        "cost_usd": meta.get("cost_usd"),
        "coverage": [{"key": key, "label": label, "companies": per_area[key]} for key, label in AREAS],
        "cant_tell": cant_tell,
        "checks": checks,
    }
