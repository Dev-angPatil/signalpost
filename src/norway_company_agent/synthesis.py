from __future__ import annotations

import math
import re
from datetime import date, timedelta
import urllib.parse

from .website import _is_junk_news, drop_placeholder_cohorts, is_current_site_job, is_site_job, looks_like_news_item
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



def _period_dates(rec: dict[str, Any]) -> tuple[date | None, date | None]:
    p = rec.get("period") or {}
    out = []
    for key in ("fraDato", "tilDato"):
        try:
            out.append(date.fromisoformat(str(p.get(key) or "")[:10]))
        except ValueError:
            out.append(None)
    return out[0], out[1]


def long_run_base(records: list[dict[str, Any]]) -> tuple[dict[str, Any], int] | None:
    """Oldest full-year filing to compare the latest against, and the span in calendar years.

    The span comes from the period end dates, so a missing year (2025, 2024, 2022) counts as
    three years, not two. A short first period (a company founded mid-year) is skipped.
    """
    if len(records) < 3:
        return None
    _, latest_end = _period_dates(records[0])
    for rec in reversed(records[2:]):
        start, end = _period_dates(rec)
        if start and end and (end - start).days < 300:
            continue  # short (first) accounting period: not comparable with a full year
        if latest_end and end:
            span = latest_end.year - end.year
        else:
            span = records.index(rec)
        rev = rec.get("revenue")
        if span >= 2 and isinstance(rev, (int, float)) and not isinstance(rev, bool) and rev > 0:
            return rec, span
        return None
    return None

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
            "revenue_trajectory_status": "not_available",
            "revenue_trajectory_badge": "Not reported",
            "revenue_trajectory_narrative": None,
            "cagr_pct": None,
            "revenue_series": [],
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

    revenue_series = []
    for item in financial_records:
        p = item.get("period") or {}
        td = str(p.get("tilDato") or "")
        yr = td[:4] if len(td) >= 4 and td[:4].isdigit() else str(item.get("year") or "")
        r = number(item.get("revenue"))
        e = number(item.get("operating_result"))
        m = round((e / r) * 100, 2) if r and r > 0 and e is not None else None
        revenue_series.append({
            "year": yr,
            "revenue": r,
            "operating_result": e,
            "margin_pct": m,
            "currency": item.get("currency") or "NOK",
        })

    rev_growth = None
    cagr_pct = None
    trajectory_status = "single_year"
    trajectory_badge = "Single Year"
    trajectory_narrative = None

    if rev == 0:
        trajectory_status = "pre_revenue"
        trajectory_badge = "Pre-Revenue (NOK 0)"
        trajectory_narrative = "Pre-revenue operations (NOK 0 revenue reported)"
    elif len(financial_records) >= 2:
        prev_rev = number(financial_records[1].get("revenue"))
        if rev is not None and prev_rev is not None and prev_rev > 0:
            rev_growth = round(((rev - prev_rev) / prev_rev) * 100, 2)
            if rev_growth > 0:
                trajectory_status = "growth"
                if len(financial_records) >= 3:
                    third_rev = number(financial_records[2].get("revenue"))
                    if third_rev and third_rev > 0 and prev_rev > third_rev:
                        trajectory_status = "steady_growth"
                trajectory_badge = f"▲ +{rev_growth}% YoY (Growing)"
                trajectory_narrative = f"Revenue went up by +{rev_growth}%"
            elif rev_growth < 0:
                prev_ebit = number(financial_records[1].get("operating_result"))
                if ebit is not None and ebit > 0 and (prev_ebit is None or prev_ebit <= 0):
                    trajectory_status = "turnaround"
                    trajectory_badge = f"▲ Turnaround ({rev_growth}% YoY)"
                    trajectory_narrative = f"Turnaround: returned to operating profitability despite revenue contracting by {rev_growth}%"
                else:
                    trajectory_status = "contraction"
                    trajectory_badge = f"▼ {rev_growth}% YoY (Declining)"
                    trajectory_narrative = f"Revenue went down by {rev_growth}%"
            else:
                trajectory_status = "stable"
                trajectory_badge = "━ Flat (0.0%)"
                trajectory_narrative = "Revenue remained unchanged"

        base = long_run_base(financial_records) if rev and rev > 0 else None
        if base:
            oldest, span_years = base
            oldest_rev = number(oldest.get("revenue"))
            if oldest_rev and oldest_rev > 0:
                cagr_pct = round((((rev / oldest_rev) ** (1 / span_years)) - 1) * 100, 2)
                # A one-year move against the longer trend is labelled as such, never as plain growth/decline.
                if rev_growth is not None and rev_growth > 0 and cagr_pct < 0:
                    trajectory_status = "rebound"
                    trajectory_badge = f"▲ +{rev_growth}% YoY (rebound; {span_years}-yr CAGR {cagr_pct}%)"
                    trajectory_narrative = f"Revenue went up by +{rev_growth}% on the prior year but remains below its level {span_years} years earlier (CAGR {cagr_pct}%)"
                elif rev_growth is not None and rev_growth < 0 and cagr_pct > 0 and trajectory_status == "contraction":
                    trajectory_status = "dip"
                    trajectory_badge = f"▼ {rev_growth}% YoY (dip; {span_years}-yr CAGR +{cagr_pct}%)"
                    trajectory_narrative = f"Revenue went down by {rev_growth}% on the prior year but is above its level {span_years} years earlier (CAGR +{cagr_pct}%)"

    return {
        "operating_margin_pct": op_margin,
        "net_margin_pct": net_margin,
        "equity_ratio_pct": eq_ratio,
        "debt_to_assets_pct": debt_ratio,
        "multi_year_revenue_growth_pct": rev_growth,
        "revenue_trajectory_status": trajectory_status,
        "revenue_trajectory_badge": trajectory_badge,
        "revenue_trajectory_narrative": trajectory_narrative,
        "cagr_pct": cagr_pct,
        "revenue_series": revenue_series,
    }


def synthesize_executive_profile(profile_data: dict[str, Any]) -> dict[str, Any]:
    from .publication_gates import apply_publication_gates
    apply_publication_gates(profile_data)
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
    role_rows = ((((profile_data.get("evidence") or {}).get("roles") or {}).get("value") or {}).get("roles") or [])
    for role in role_rows if isinstance(role_rows, list) else []:
        if not isinstance(role, dict) or role.get("inactive"):
            continue
        person = role.get("name")
        person = person[0] if isinstance(person, list) and person else person
        if not isinstance(person, str) or not person.strip():
            continue
        if role.get("role_code") == "DAGL" and not ceo:
            ceo = person.strip()
        elif role.get("role_code") == "LEDE" and not chair:
            chair = person.strip()

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
    registry_raw = ((profile_data.get("evidence") or {}).get("registry") or {}).get("value") or {}
    registry_raw = registry_raw if isinstance(registry_raw, dict) else {}
    is_bankrupt = bool(registry_raw.get("konkurs") or profile_data.get("bankrupt"))
    is_liquidating = bool(registry_raw.get("underAvvikling") or registry_raw.get("underTvangsavviklingEllerTvangsopplosning") or profile_data.get("liquidating"))
    deleted_on = str(registry_raw.get("slettedato") or "")[:10] or None
    adverse = bool(profile_data.get("adverse") or is_bankrupt or is_liquidating)
    in_bankruptcy = adverse or any(e.get("category") == "insolvency" for e in ann_events)

    site_url = (profile_data.get("evidence") or {}).get("website", {}).get("source_url") or profile_data.get("website") or ""
    site_domain = urllib.parse.urlparse(site_url).netloc.removeprefix("www.") or "company site"
    nace_clause = f" (NACE {nace_code})" if nace_code else ""

    def nok(value: Any) -> str:
        return f"NOK {value:,.0f}".replace(",", " ")

    def rec_year(rec: dict[str, Any]) -> str:
        period = rec.get("period") if isinstance(rec.get("period"), dict) else {}
        return str(str(period.get("tilDato") or "")[:4] or rec.get("year") or rec.get("fiscal_year") or "the latest filed year")

    evidence_all = profile_data.get("evidence") or {}
    jobs_val = (evidence_all.get("jobs") or {}).get("value") or {}
    postings = [p for p in jobs_val.get("postings") or [] if isinstance(p, dict) and p.get("title")]
    if is_publishable:
        for item in web_val.get("news_items") or []:
            if is_current_site_job(item) and item.get("title"):
                postings.append({"title": item["title"], "published": item.get("date"), "source": "company_site"})
    site_news = []
    if is_publishable:
        site_news = sorted(
            [n for n in drop_placeholder_cohorts(web_val.get("news_items") or []) if isinstance(n, dict) and n.get("date") and n.get("title") and not is_site_job(n)
             and looks_like_news_item(n) and not _is_junk_news(str(n["title"]), str(n.get("url") or ""), "", str(n["date"]))],
            key=lambda n: str(n.get("date")), reverse=True,
        )
    latest = records[0] if records else {}
    prev = records[1] if len(records) >= 2 else {}
    rev = latest.get("revenue")
    prev_rev = prev.get("revenue")
    year = rec_year(latest) if latest else ""

    # 1. Lead with the single most decision-relevant fact, and say where it came from.
    insolvency = next((e for e in ann_events if e.get("category") == "insolvency"), None)
    dissolution = next((e for e in ann_events if e.get("category") == "dissolution"), None)
    # With a live registry record showing no bankruptcy or liquidation, an old
    # notice describes a past matter: it stays in the notice list but does not
    # lead the summary as the company's current state.
    registry_checked = isinstance(registry_raw.get("konkurs"), bool)

    def _current(event: dict[str, Any] | None) -> bool:
        if event is None:
            return False
        if not registry_checked:
            return True
        try:
            from datetime import date as _date
            notice_day = _date.fromisoformat(str(event.get("date") or "")[:10])
        except ValueError:
            return False
        return (_date.today() - notice_day).days <= 730

    bankruptcy_notice = next((e for e in ann_events if e.get("category") == "insolvency" and "konkurs" in str(e.get("title") or "").casefold()), None)
    if not _current(bankruptcy_notice):
        bankruptcy_notice = None
    if not _current(insolvency):
        insolvency = None
    if deleted_on:
        reason = f" (registry notice: {dissolution['title']}, {dissolution.get('date')})" if dissolution and dissolution.get("title") else ""
        lead = (
            f"DELETED: {name} was deleted from the Central Coordinating Register for Legal Entities on {deleted_on}{reason} "
            "(source: Brønnøysundregistrene Enhetsregisteret)."
        )
    elif is_bankrupt or bankruptcy_notice:
        notice = bankruptcy_notice or insolvency
        when = f" ({notice.get('date')})" if notice and notice.get("date") else ""
        src = "Brønnøysundregistrene statutory notices, Kunngjøringer" if notice else "Brønnøysundregistrene Enhetsregisteret"
        lead = f"COMPANY IN BANKRUPTCY: {name} is subject to insolvency/bankruptcy proceedings{when} (source: {src})."
    elif is_liquidating:
        lead = f"UNDER LIQUIDATION: {name} is registered as being wound up (source: Brønnøysundregistrene Enhetsregisteret)."
    elif insolvency:
        when = f", {insolvency.get('date')}" if insolvency.get("date") else ""
        lead = (
            f"INSOLVENCY NOTICE: {name} has a registry notice \"{insolvency.get('title') or 'insolvency notice'}\"{when} "
            "(source: Brønnøysundregistrene statutory notices, Kunngjøringer)."
        )
    elif adverse:
        lead = f"ADVERSE REGISTRY STATUS: {name} is registered as bankrupt or in liquidation (source: Brønnøysundregistrene Enhetsregisteret)."
    elif postings:
        newest = sorted(postings, key=lambda p: str(p.get("published") or ""), reverse=True)[0]
        src = "the company's own website" if newest.get("source") in {"company_site", "company_career_page"} else "NAV arbeidsplassen, matched on employer organisation number"
        when = f", published {newest['published']}" if newest.get("published") else ""
        lead = f"{name} is hiring: {len(postings)} open posting(s), latest \"{newest['title']}\"{when} (source: {src})."
    elif isinstance(rev, (int, float)) and rev > 0:
        trend = ""
        if isinstance(prev_rev, (int, float)) and prev_rev > 0:
            change = round((rev - prev_rev) / prev_rev * 100, 1)
            direction = "up" if change > 0 else "down" if change < 0 else "flat"
            trend = f", {direction} {abs(change)}% from {nok(prev_rev)} in {rec_year(prev)}" if direction != "flat" else f", flat versus {rec_year(prev)}"
        op_res = latest.get("operating_result")
        op_clause = ""
        if isinstance(op_res, (int, float)):
            op_clause = f"; operating {'profit' if op_res >= 0 else 'loss'} {nok(abs(op_res))} ({metrics.get('operating_margin_pct')}% margin)"
        long_run = ""
        base = long_run_base(records)
        oldest_rec = base[0] if base else None
        oldest_rev = oldest_rec.get("revenue") if isinstance(oldest_rec, dict) else None
        if isinstance(oldest_rev, (int, float)) and oldest_rev > 0 and isinstance(prev_rev, (int, float)) and prev_rev > 0:
            one_year_up = rev > prev_rev
            long_run_up = rev > oldest_rev
            if one_year_up != long_run_up and rev != oldest_rev and rev != prev_rev:
                long_run = f", {'but below' if one_year_up else 'though still above'} {nok(oldest_rev)} in {rec_year(oldest_rec)}"
        lead = f"{name} reported revenue of {nok(rev)} for {year}{trend}{long_run}{op_clause} (source: annual accounts filed with Regnskapsregisteret)."
    elif latest:
        bits = []
        if isinstance(latest.get("assets"), (int, float)):
            bits.append(f"total assets {nok(latest['assets'])}")
        if isinstance(latest.get("equity"), (int, float)):
            bits.append(f"equity {nok(latest['equity'])}")
        if isinstance(latest.get("annual_result"), (int, float)):
            bits.append(f"annual result {nok(latest['annual_result'])}")
        no_rev = "no operating revenue reported" if rev in (None, 0) else ""
        lead = (
            f"{name}'s {year} annual accounts show {', '.join(bits) or 'a filed balance sheet'}"
            f"{'; ' + no_rev if no_rev else ''} (source: Regnskapsregisteret)."
        )
    elif is_publishable and web_desc:
        lead = f"{name} describes itself as: {web_desc.rstrip('. ')} (source: verified company website {site_domain})."
    else:
        article = "an" if str(form)[:1].upper() in "AEIOU" else "a"
        lead = f"{name} is {article} {form} registered in {municipality} (source: Brønnøysundregistrene Enhetsregisteret)."
    parts: list[str] = [lead]

    # 2. What the company does.
    industry_known = nace and nace.casefold() not in {"uoppgitt", "general commercial activity"}
    if is_publishable and web_desc and not lead.startswith(f"{name} describes itself"):
        scope_desc = f"{name} business operations: {web_desc}"
        # First sentence only: later sentences would read as unsourced statements of their own.
        first_sentence = re.split(r"(?<=[.!?])\s+", str(web_desc).strip())[0].rstrip(". ")
        parts.append(f"Its verified website ({site_domain}) describes it as: {first_sentence}.")
    elif industry_known:
        scope_desc = f"{name} is a Norwegian {form} in {nace}{nace_clause}, based in {municipality}." + ("" if is_publishable else " No website could be confirmed as this company's own.")
        parts.append(f"Registered activity: {nace}{nace_clause}, based in {municipality}.")
    else:
        scope_desc = f"{name} is a Norwegian {form} based in {municipality}; no industry code is registered."
        parts.append(f"Based in {municipality}; no industry code is registered.")
    if form == "ENK" and not records:
        parts.append("Under the Norwegian Accounting Act § 1-2, sole proprietorships below the statutory threshold are exempt from filing annual accounts.")

    # 3. Leadership.
    if ceo and chair and ceo.casefold() == chair.casefold():
        parts.append(f"Led by CEO {ceo}, who also chairs the board (Brønnøysund roles register).")
    elif ceo and chair:
        parts.append(f"Led by CEO {ceo}; board chaired by {chair} (Brønnøysund roles register).")
    elif ceo:
        parts.append(f"Led by CEO {ceo} (Brønnøysund roles register).")
    elif chair:
        parts.append(f"Board chaired by {chair} (Brønnøysund roles register).")

    # 4. Finances, when not already the lead.
    if records and not lead.startswith(f"{name} reported revenue") and isinstance(rev, (int, float)) and rev > 0:
        margin = metrics.get("operating_margin_pct")
        margin_clause = f", operating margin of {margin}%" if margin is not None else ""
        parts.append(f"Revenue {nok(rev)} in {year}{margin_clause} (Regnskapsregisteret).")
    eq = metrics.get("equity_ratio_pct")
    if eq is not None and eq < 0:
        parts.append(f"Negative equity (equity ratio of {eq}%) in {year} indicates balance-sheet strain.")

    # 5. Changes and dated activity.
    press = []
    news_ev = evidence_all.get("news") or {}
    if news_ev.get("status") == "available":
        press = sorted([n for n in ((news_ev.get("value") or {}).get("items") or []) if isinstance(n, dict) and n.get("title") and n.get("date")],
                       key=lambda n: str(n.get("date")), reverse=True)
    if site_news:
        n0 = site_news[0]
        parts.append(f"Latest company news on {site_domain}: \"{n0['title']}\" ({n0['date']}).")
    elif press:
        n0 = press[0]
        publisher = n0.get("publisher") or "news coverage"
        parts.append(f"Latest press mention: \"{n0['title']}\" ({n0['date']}, {publisher} via Google News).")
    routine = ("godkjent årsregnskap", "godkjente årsregnskap", "godkjent arsregnskap")
    dated_all = sorted([e for e in ann_events if e.get("date")], key=lambda e: str(e.get("date")), reverse=True)
    # Routine filings and address changes say nothing a reader can act on.
    routine += ("forretningsadresse", "postadresse", "endring av forretningsadresse", "endring av postadresse")
    dated = [e for e in dated_all if not str(e.get("type") or e.get("title") or "").casefold().startswith(routine)][:2]
    if dated:
        acts = "; ".join(f"{e.get('type') or e.get('title') or str(e.get('category') or 'notice').replace('_', ' ')} ({e.get('date')})" for e in dated)
        cutoff = date.today() - timedelta(days=365)
        has_recent = False
        for event in dated:
            try:
                event_date = date.fromisoformat(str(event.get("date"))[:10])
            except (TypeError, ValueError):
                continue
            if event_date >= cutoff:
                has_recent = True
                break
        label = "Recent registry notices" if has_recent else "Latest registry notices"
        parts.append(f"{label}: {acts}.")

    # 6. Unknowns, stated plainly.
    missing = []
    if not is_publishable:
        missing.append("a verified official website")
    if not postings:
        missing.append("open job postings")
    if not site_news and not press:
        missing.append("dated company news")
    if not records and form != "ENK":
        missing.append("filed annual accounts")
    if missing:
        parts.append(f"Not found in checked sources: {', '.join(missing)}.")

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
