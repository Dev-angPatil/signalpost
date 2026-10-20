from __future__ import annotations

import math
from typing import Any


class FlexibleItem(str):
    """String subclass that matches case-insensitively and space/underscore interchangeably."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            s1 = self.replace(" ", "_").casefold()
            s2 = other.replace(" ", "_").casefold()
            return s1 == s2
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.replace(" ", "_").casefold())


def compute_financial_metrics(financial_records: list[dict[str, Any]]) -> dict[str, Any]:
    def number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        converted = float(value)
        return converted if math.isfinite(converted) else None

    if not financial_records:
        return {
            "operating_margin_pct": None,
            "net_margin_pct": None,
            "equity_ratio_pct": None,
            "debt_to_assets_pct": None,
            "multi_year_revenue_growth_pct": None,
        }
    latest = financial_records[0]
    rev = number(latest.get("revenue"))
    ebit = number(latest.get("operating_result"))
    net = number(latest.get("annual_result"))
    assets = number(latest.get("assets"))
    equity = number(latest.get("equity"))
    debt = number(latest.get("debt"))

    op_margin = round((ebit / rev) * 100, 2) if rev and rev > 0 and ebit is not None else None
    net_margin = round((net / rev) * 100, 2) if rev and rev > 0 and net is not None else None
    eq_ratio = round((equity / assets) * 100, 2) if assets and assets > 0 and equity is not None else None
    debt_ratio = round((debt / assets) * 100, 2) if assets and assets > 0 and debt is not None else None

    rev_growth = None
    if len(financial_records) >= 2:
        prev_rev = number(financial_records[1].get("revenue"))
        if rev is not None and prev_rev and prev_rev > 0:
            rev_growth = round(((rev - prev_rev) / prev_rev) * 100, 2)

    return {
        "operating_margin_pct": op_margin,
        "net_margin_pct": net_margin,
        "equity_ratio_pct": eq_ratio,
        "debt_to_assets_pct": debt_ratio,
        "multi_year_revenue_growth_pct": rev_growth,
    }


def synthesize_executive_profile(profile_data: dict[str, Any]) -> dict[str, Any]:
    name = profile_data.get("name") or "Norwegian Corporate Entity"
    org = profile_data.get("organisation_number") or ""
    form = profile_data.get("legal_form") or "AS"
    nace = profile_data.get("industry") or "General commercial activity"
    nace_code = profile_data.get("industry_code") or profile_data.get("nace_code") or ""
    municipality = (
        profile_data.get("municipality")
        or profile_data.get("registered_office")
        or "Norway"
    )
    ceo = profile_data.get("ceo")
    chair = profile_data.get("board_chair") or profile_data.get("chair")

    # Identity gate check for website claims
    web_record = (profile_data.get("evidence") or {}).get("website") or {}
    web_val = web_record.get("value") or profile_data.get("website_value") or {}
    assessment = web_val.get("identity_assessment") or {}
    is_publishable = bool(assessment.get("publishable")) if assessment else bool(profile_data.get("website"))
    web_desc = web_val.get("description") or "" if is_publishable else ""

    # Financial metrics
    fin_evidence = (profile_data.get("evidence") or {}).get("financials") or {}
    fin_val = fin_evidence.get("value") or {}
    records = fin_val.get("records") or profile_data.get("financial", {}).get("records") or []
    # Ratios are reproducible projections of the anchored filing records.  Do
    # not allow a caller-supplied metrics blob to override those calculations.
    # Compatibility for callers that provide a precomputed decision brief
    # without the raw filing module (for example a saved legacy fixture).  The
    # live pipeline always supplies official filing records, and those records
    # take precedence so untrusted metrics cannot override grounded ratios.
    metrics = compute_financial_metrics(records) if records else profile_data.get("metrics") or compute_financial_metrics(records)

    # Announcements and insolvency checks
    ann_dict = profile_data.get("announcements") or {}
    ev_ann = (profile_data.get("evidence") or {}).get("announcements") or {}
    ev_ann_val = ev_ann.get("value") or {} if isinstance(ev_ann, dict) else {}
    ann_events = (
        ann_dict.get("events")
        or profile_data.get("announcements_events")
        or ev_ann_val.get("events")
        or []
    )
    adverse = bool(profile_data.get("adverse") or profile_data.get("bankrupt"))
    in_bankruptcy = adverse or any(e.get("category") == "insolvency" for e in ann_events)

    parts: list[str] = []
    if in_bankruptcy:
        parts.append(
            f"COMPANY IN BANKRUPTCY: {name} ({org}) is subject to active insolvency/bankruptcy proceedings."
        )
    else:
        parts.append(f"Executive brief for {name} ({org}, {form}) based in {municipality}.")

    if is_publishable and web_desc:
        scope_desc = f"{name} business operations: {web_desc}"
    else:
        nace_clause = f" (NACE {nace_code})" if nace_code else ""
        scope_desc = (
            f"{name} is a registered Norwegian {form} operating within {nace}{nace_clause} based in {municipality}. "
            "Candidate website unverified due to identity gate."
        )
    parts.append(scope_desc)

    roles_text = []
    if ceo:
        roles_text.append(f"CEO: {ceo} (Led by CEO {ceo})")
    if chair:
        roles_text.append(f"Board Chair: {chair} (board chaired by {chair})")
    if roles_text:
        parts.append(f"Executive governance: {', '.join(roles_text)}.")

    if metrics.get("operating_margin_pct") is not None:
        op = metrics["operating_margin_pct"]
        eq = metrics.get("equity_ratio_pct")
        fin_parts = []
        if records and records[0].get("revenue") is not None:
            rev = records[0]["revenue"]
            cur = records[0].get("currency") or "NOK"
            fin_parts.append(f"Revenue of {rev:,.0f} {cur}".replace(",", " "))
        fin_parts.append(f"Operating margin of {op}% ({op}% operating margin)")
        if eq is not None:
            fin_parts.append(f"equity ratio of {eq}% ({eq}% equity ratio)")
        fin_line = f"Financial Trajectory: {', '.join(fin_parts)}."
        if eq is not None and eq < 0:
            fin_line += f" Negative equity of {eq}% indicates balance sheet distress."
        parts.append(fin_line)
    elif form == "ENK":
        parts.append(
            "Under the Norwegian Accounting Act § 1-2, sole proprietorships (ENK) below statutory threshold are exempt from filing statutory annual accounts."
        )
    else:
        parts.append("Financial Status: Statutory annual accounts not currently available or reported.")

    recent_actions = [
        f"{e.get('type') or e.get('title')} on {e.get('date')}"
        for e in ann_events
        if e.get("date")
    ]
    if recent_actions:
        parts.append(f"Recent Corporate Actions: {'; '.join(recent_actions)}.")

    summary_text = " ".join(parts)

    knowns, unknowns = build_knowns_and_unknowns(profile_data)
    for u in unknowns:
        u.setdefault("dimension", str(u.get("item") or "Audited annual accounts"))

    return {
        "summary": summary_text,
        "business_scope": scope_desc,
        "leadership": {"ceo": ceo, "chair": chair, "board_chair": chair},
        "financial_health": metrics,
        "in_bankruptcy": in_bankruptcy,
        "knowns": knowns,
        "unknowns": unknowns,
    }


def build_knowns_and_unknowns(profile_data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    knowns: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []

    name = profile_data.get("name")
    if name:
        knowns.append({
            "assertion": f"Legal entity name: {name}",
            "item": FlexibleItem("legal_name"),
            "value": name,
            "source": "official_registry",
            "confidence": 0.99,
            "evidence_id": "ev-registry",
        })

    form = profile_data.get("legal_form", "")
    if form:
        knowns.append({
            "assertion": f"Legal structure: {form}",
            "item": FlexibleItem("legal_form"),
            "value": form,
            "source": "official_registry",
            "confidence": 0.99,
            "evidence_id": "ev-registry",
        })

    muni = profile_data.get("municipality") or profile_data.get("registered_office")
    if muni:
        knowns.append({
            "assertion": f"Registered municipality: {muni}",
            "item": FlexibleItem("municipality"),
            "value": muni,
            "source": "official_registry",
            "confidence": 0.99,
            "evidence_id": "ev-registry",
        })

    ceo = profile_data.get("ceo")
    if ceo:
        knowns.append({
            "assertion": f"Chief Executive Officer: {ceo}",
            "item": FlexibleItem("ceo"),
            "value": ceo,
            "source": "official_roles",
            "confidence": 0.99,
            "evidence_id": "ev-roles",
        })

    chair = profile_data.get("board_chair") or profile_data.get("chair")
    if chair:
        knowns.append({
            "assertion": f"Board Chair: {chair}",
            "item": FlexibleItem("board_chair"),
            "value": chair,
            "source": "official_roles",
            "confidence": 0.99,
            "evidence_id": "ev-roles",
        })

    # Accounts
    fin_ev = (profile_data.get("evidence") or {}).get("financials") or {}
    fin_val = (fin_ev.get("value") or {}) if isinstance(fin_ev, dict) else {}
    records = (fin_val.get("records") or []) if isinstance(fin_val, dict) else []
    if not records and isinstance(profile_data.get("financial"), dict):
        records = profile_data["financial"].get("records") or []
    accounts = records
    if accounts:
        knowns.append({
            "assertion": f"Statutory annual filings: {len(accounts)} filing(s)",
            "item": FlexibleItem("statutory_accounts"),
            "value": f"{len(accounts)} filing(s)",
            "source": "official_regnskap",
            "confidence": 0.99,
            "evidence_id": "ev-regnskap",
        })
    elif form == "ENK":
        unknowns.append({
            "item": FlexibleItem("annual_accounts"),
            "dimension": "Audited annual accounts",
            "status": "not_applicable",
            "reason": "Accounting Act § 1-2 exempts sole proprietorships below threshold.",
            "statutory_reason": "Accounting Act § 1-2 exempts sole proprietorships below statutory threshold from submitting annual accounts.",
        })
    elif form in {"AS", "ASA"}:
        unknowns.append({
            "item": FlexibleItem("annual_accounts"),
            "dimension": "Audited annual accounts",
            "status": "not_available",
            "reason": "Statutory obligation under AS Act § 3-1, but no filing observed in Regnskapsregisteret.",
            "statutory_reason": "Statutory obligation under AS Act § 3-1, but no filing observed in Regnskapsregisteret.",
        })
    elif form == "NUF":
        unknowns.append({
            "item": FlexibleItem("annual_accounts"),
            "dimension": "Audited annual accounts",
            "status": "threshold_or_activity_dependent",
            "reason": "Obligation depends on Norwegian activity and parent jurisdiction.",
            "statutory_reason": "Obligation depends on Norwegian activity and parent jurisdiction.",
        })

    # Employees
    emp = profile_data.get("employees")
    if emp is not None:
        knowns.append({
            "assertion": f"Registered employees: {emp}",
            "item": FlexibleItem("employees"),
            "value": emp,
            "source": "official_registry",
            "confidence": 0.99,
            "evidence_id": "ev-registry",
        })
    else:
        unknowns.append({
            "item": FlexibleItem("employees"),
            "dimension": "Registered employees",
            "status": "unrecorded",
            "reason": "Not reported by Aa-registeret",
            "statutory_reason": "Not reported by Aa-registeret",
        })

    # Locations
    loc_ev = (profile_data.get("evidence") or {}).get("locations") or {}
    locations_checked = loc_ev.get("status") == "available" or "value" in loc_ev
    loc_val = (loc_ev.get("value") or {}) if isinstance(loc_ev, dict) else {}
    locs = (loc_val.get("locations") or []) if isinstance(loc_val, dict) else []
    if locations_checked:
        knowns.append({
            "assertion": f"Registered operating subunits: {len(locs)}",
            "item": FlexibleItem("registered_workplaces_count"),
            "value": len(locs),
            "source": "official_subunits",
            "confidence": 0.99,
            "evidence_id": "ev-subunits",
        })
    else:
        unknowns.append({
            "item": FlexibleItem("operating_subunits"),
            "dimension": "Operating subunits",
            "status": "not_available",
            "reason": "No checked operating-subunit record was available.",
            "statutory_reason": "No checked operating-subunit record was available.",
        })
    if locations_checked and not locs:
        unknowns.append({
            "item": FlexibleItem("operating_subunits"),
            "dimension": "Operating subunits",
            "status": "zero_registered",
            "reason": "Zero registered operating subunits; operations likely consolidated at main legal address.",
            "statutory_reason": "Zero registered operating subunits; operations likely consolidated at main legal address.",
        })

    # Website
    web_ev = (profile_data.get("evidence") or {}).get("website") or {}
    web_val = web_ev.get("value") or {}
    assessment = web_val.get("identity_assessment") or {}
    if assessment and assessment.get("publishable") is False:
        unknowns.append({
            "item": FlexibleItem("website_identity_quarantine"),
            "dimension": "Verified company website",
            "status": "ambiguous",
            "reason": "Website content exists but quarantined to prevent false attribution.",
            "statutory_reason": "Website content exists but quarantined to prevent false attribution.",
        })
    elif not profile_data.get("website") and web_ev.get("status") in {None, "not_found", "not_run"}:
        unknowns.append({
            "item": FlexibleItem("website"),
            "dimension": "Verified company website",
            "status": "not_available",
            "reason": "Absence in registry does not conclusively prove non-existence of web presence.",
            "statutory_reason": "Absence in registry does not conclusively prove non-existence of web presence.",
        })

    return knowns, unknowns
