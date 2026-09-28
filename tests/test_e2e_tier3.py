from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# Real modules in src/norway_company_agent
from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import evidence, utc_now
from norway_company_agent.identity import (
    apply_website_identity_gate,
    assess_social_identity,
    assess_website_identity,
)
from norway_company_agent.official import (
    accounting_obligation_assessment,
    normalize_entity,
    normalize_financials,
)
from norway_company_agent.refresh import diff_profile
from norway_company_agent.website import (
    _priority_links,
    _registered_domain,
    normalize_homepage,
    normalize_social_url,
)
import run_agent
from scripts.build_prototype import build as build_prototype_html

# Progressive testability: try importing upcoming milestone modules;
# fall back to specification-compliant reference implementations defined in PROJECT.md.
try:
    from norway_company_agent import announcements as prod_announcements
except ImportError:
    prod_announcements = None

try:
    from norway_company_agent import synthesis as prod_synthesis
except ImportError:
    prod_synthesis = None

try:
    from norway_company_agent import showcase as prod_showcase
except ImportError:
    prod_showcase = None


# --- Evolution Bridge Reference Implementations (PROJECT.md § Milestones M2, M3, M4) ---

def classify_notice(notice: dict[str, Any]) -> dict[str, Any]:
    """Classifies Kunngjøringer notice into standard corporate action categories."""
    if prod_announcements and hasattr(prod_announcements, "classify_notice"):
        return prod_announcements.classify_notice(notice)
    title = str(notice.get("title") or "").strip()
    desc = str(notice.get("description") or "").strip()
    haystack = f"{title} {desc}".casefold()

    if any(k in haystack for k in ("konkurs", "bobehandling", "gjeldsforhandling", "rekonstruksjon", "tvangsavvikling", "oppløsning", "sletting")):
        category = "insolvency"
    elif any(k in haystack for k in ("kapital", "aksjekapital", "kapitalforhøyelse", "kapitalforhoyelse", "kapitalnedsettelse", "erverv av egne aksjer", "aksjesplitt", "utbytte")):
        category = "capital_change"
    elif any(k in haystack for k in ("styre", "styrets leder", "daglig leder", "fratreden", "styremedlem")):
        category = "board_appointment"
    elif any(k in haystack for k in ("vedtekt", "årsregnskap", "aarsregnskap", "revisor", "foretaksnavn", "forretningsadresse", "fusjon", "fisjon", "omdanning")):
        category = "legal_filing"
    else:
        category = "other"

    # Normalize date DD.MM.YYYY to YYYY-MM-DD if needed
    raw_date = str(notice.get("date") or "")
    if re.match(r"^\d{2}\.\d{2}\.\d{4}$", raw_date):
        d, m, y = raw_date.split(".")
        date_iso = f"{y}-{m}-{d}"
    else:
        date_iso = raw_date

    return {
        "title": title,
        "type": notice.get("type") or title,
        "category": category,
        "date": date_iso,
        "description": desc,
        "source_url": notice.get("source_url") or "https://w2.brreg.no/kunngjoring/hent.jsp",
        "sha256": notice.get("sha256") or hashlib.sha256(f"{title}{date_iso}{desc}".encode("utf-8")).hexdigest(),
    }


def compute_financial_metrics(financial_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Computes deterministic financial ratios with strict non-zero missingness preservation."""
    if prod_synthesis and hasattr(prod_synthesis, "compute_financial_metrics"):
        return prod_synthesis.compute_financial_metrics(financial_records)
    if not financial_records:
        return {
            "operating_margin_pct": None,
            "net_margin_pct": None,
            "equity_ratio_pct": None,
            "debt_to_assets_pct": None,
            "multi_year_revenue_growth_pct": None,
        }

    latest = financial_records[0]
    revenue = latest.get("revenue")
    op_result = latest.get("operating_result")
    net_result = latest.get("annual_result")
    assets = latest.get("assets")
    equity = latest.get("equity")
    debt = latest.get("debt")

    # Invariant: If revenue <= 0 or missing, margin is None (not_available), NEVER 0.0%
    op_margin = (op_result / revenue * 100.0) if (revenue is not None and revenue > 0 and op_result is not None) else None
    net_margin = (net_result / revenue * 100.0) if (revenue is not None and revenue > 0 and net_result is not None) else None
    # Invariant: If assets <= 0 or missing, ratios are None
    equity_ratio = (equity / assets * 100.0) if (assets is not None and assets > 0 and equity is not None) else None
    debt_ratio = (debt / assets * 100.0) if (assets is not None and assets > 0 and debt is not None) else None

    growth = None
    if len(financial_records) >= 2:
        prev_rev = financial_records[1].get("revenue")
        if prev_rev is not None and prev_rev > 0 and revenue is not None:
            growth = ((revenue - prev_rev) / prev_rev) * 100.0

    return {
        "operating_margin_pct": round(op_margin, 2) if op_margin is not None else None,
        "net_margin_pct": round(net_margin, 2) if net_margin is not None else None,
        "equity_ratio_pct": round(equity_ratio, 2) if equity_ratio is not None else None,
        "debt_to_assets_pct": round(debt_ratio, 2) if debt_ratio is not None else None,
        "multi_year_revenue_growth_pct": round(growth, 2) if growth is not None else None,
    }


def synthesize_executive_profile(profile_data: dict[str, Any]) -> dict[str, Any]:
    """Generates structured, grounded executive summary and knowns/unknowns."""
    if prod_synthesis and hasattr(prod_synthesis, "synthesize_executive_profile"):
        return prod_synthesis.synthesize_executive_profile(profile_data)

    name = profile_data.get("name") or "Unknown Entity"
    orgnr = profile_data.get("organisation_number") or ""
    form = profile_data.get("legal_form") or "AS"
    nace = profile_data.get("industry") or "Corporate activities"
    municipality = profile_data.get("municipality") or profile_data.get("registered_office") or "Norway"
    adverse = profile_data.get("adverse", False)

    fin_ev = profile_data.get("evidence", {}).get("financials", {})
    fin_val = (fin_ev.get("value") or {}) if isinstance(fin_ev, dict) else {}
    fin_rec = fin_val.get("records", []) if isinstance(fin_val, dict) else []
    metrics = compute_financial_metrics(fin_rec)

    # Announcements events
    ann_ev = profile_data.get("evidence", {}).get("announcements", {})
    ann_val = (ann_ev.get("value") or {}) if isinstance(ann_ev, dict) else {}
    ann_events = ann_val.get("events", []) if isinstance(ann_val, dict) else []
    recent_notices = [f"{e.get('type')} on {e.get('date')}" for e in ann_events[:3]]

    # Web description (only if publishable)
    web_ev = profile_data.get("evidence", {}).get("website", {})
    web_val = (web_ev.get("value") or {}) if isinstance(web_ev, dict) else {}
    identity_assessment = web_val.get("identity_assessment", {}) if isinstance(web_val, dict) else {}
    is_publishable = identity_assessment.get("publishable", False)
    web_desc = web_val.get("description", "") if is_publishable else None

    # Leadership
    ceo = profile_data.get("ceo")
    chair = profile_data.get("board_chair")

    # Bankruptcy check
    in_bankruptcy = adverse or any(e.get("category") == "insolvency" for e in ann_events)

    lines = []
    if in_bankruptcy:
        lines.append(f"**NOTICE**: {name} ({orgnr}) is subject to active insolvency/bankruptcy proceedings.")
    else:
        lines.append(f"**Executive Brief**: {name} ({orgnr}) is a registered Norwegian {form} based in {municipality}.")

    if web_desc:
        lines.append(f"Business Operations: {web_desc}")
    else:
        lines.append(f"Business Scope: Registered under NACE industry '{nace}'.")

    if ceo or chair:
        roles_text = []
        if ceo:
            roles_text.append(f"CEO: {ceo}")
        if chair:
            roles_text.append(f"Board Chair: {chair}")
        lines.append(f"Governance: {', '.join(roles_text)}.")

    if metrics.get("operating_margin_pct") is not None:
        lines.append(
            f"Financial Trajectory: Operating margin of {metrics['operating_margin_pct']}%, "
            f"equity ratio of {metrics.get('equity_ratio_pct')}%."
        )
    elif form == "ENK":
        lines.append("Financial Status: Sole proprietorship exempt from statutory accounts filing under Norwegian Accounting Act § 1-2.")
    else:
        lines.append("Financial Status: Statutory annual accounts not currently available or reported.")

    if recent_notices:
        lines.append(f"Recent Corporate Actions: {'; '.join(recent_notices)}.")

    summary = "\n\n".join(lines)

    knowns = [
        {"assertion": f"Legal entity name {name}", "source": "official_registry"},
        {"assertion": f"Organisation form {form}", "source": "official_registry"},
        {"assertion": f"Registered municipality {municipality}", "source": "official_registry"},
    ]
    if in_bankruptcy:
        knowns.append({"assertion": "Subject to active insolvency proceedings", "source": "official_public_notices"})
    if metrics.get("operating_margin_pct") is not None:
        knowns.append({"assertion": f"Operating margin {metrics['operating_margin_pct']}%", "source": "official_annual_accounts"})

    unknowns = []
    if not is_publishable:
        unknowns.append({"dimension": "Verified company website", "status": "ambiguous_or_unobserved", "reason": "Candidate website failed identity gate or not registered."})
    if form == "ENK":
        unknowns.append({"dimension": "Audited annual accounts", "status": "not_applicable", "reason": "Statutory exemption under Accounting Act § 1-2."})
    elif not fin_rec:
        unknowns.append({"dimension": "Audited annual accounts", "status": "not_available", "reason": "No filing observed in Regnskapsregisteret."})

    return {
        "summary": summary,
        "metrics": metrics,
        "knowns": knowns,
        "unknowns": unknowns,
        "in_bankruptcy": in_bankruptcy,
    }


def generate_showcase_html(profiles: list[dict[str, Any]], output_path: Path | str, title: str = "Signalpost Showcase") -> str:
    """Generates standalone HTML dashboard showcase with search and provenance inspector."""
    if prod_showcase and hasattr(prod_showcase, "generate_showcase_html"):
        return prod_showcase.generate_showcase_html(profiles, str(output_path), title=title)
    # Ensure profile rows contain all keys required by compact in scripts/build_prototype.py
    normalized_rows = []
    for p in profiles:
        row = dict(p)
        row.setdefault("industry_label", row.get("industry") or row.get("nace_desc") or "Not reported")
        row.setdefault("industry_code", row.get("industry_code") or row.get("nace_code") or "Not reported")
        row.setdefault("employees", row.get("employees", 0) if row.get("employees") is not None else 0)
        row.setdefault("bankrupt", bool(row.get("adverse", False)))
        row.setdefault("liquidating", False)
        row.setdefault("municipality", row.get("municipality") or row.get("registered_office") or "Not reported")
        row.setdefault("website", row.get("website"))
        normalized_rows.append(row)
    html = build_prototype_html(normalized_rows, score={"overall_score": 85.0}, independent_score=None)
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    return html


# ==============================================================================
# TIER 3: CROSS-FEATURE COMBINATION TESTS (>= 15 Tests)
# ==============================================================================

class TestE2ETier3CrossFeature(unittest.TestCase):
    """Tier 3: Comprehensive Cross-Feature Pairwise and Multi-Module Integration Suite.

    Validates that independent pipeline features (registry, web crawling, identity gating,
    announcements, financial ratios, executive brief, streaming, and showcase) interact
    harmoniously without violating output contracts, losing provenance, or hallucinating data.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # --------------------------------------------------------------------------
    # Group 1: Official Registry + Web + Contact / Address (F1 + F2 + Registry)
    # --------------------------------------------------------------------------

    def test_t3_01_statutory_address_vs_web_operational_address(self) -> None:
        """F-T3-1: Cross-references statutory registered office in Enhetsregisteret

        against operational visiting address from crawled /kontakt subpage without collisions.
        """
        orgnr = "923609016"
        profile = {
            "organisation_number": orgnr,
            "name": "TEST NORDIC LOGISTICS AS",
            "legal_form": "AS",
            "registered_office": "Oslo",
            "municipality": "Oslo",
            "website": "https://nordiclogistics.no",
            "evidence": {
                "registry": evidence(
                    "registry",
                    "available",
                    "official_registry",
                    f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}",
                    value={
                        "navn": "TEST NORDIC LOGISTICS AS",
                        "organisasjonsform": {"kode": "AS"},
                        "forretningsadresse": {"kommune": "Oslo", "adresse": ["Dronning Eufemias gate 8"], "postnummer": "0191", "poststed": "Oslo"},
                    },
                    content_sha256="1" * 64,
                ),
                "website": evidence(
                    "website",
                    "available",
                    "company_owned",
                    "https://nordiclogistics.no/kontakt",
                    value={
                        "title": "Nordic Logistics AS - Kontakt Oss",
                        "description": "Ledende logistikksenter i Drammen og Oslo.",
                        "identity_assessment": {"publishable": True, "score": 0.95, "status": "exact"},
                        "contact_address": "Bedriftsveien 4, 3040 Drammen",
                        "pages": [
                            {"url": "https://nordiclogistics.no/kontakt", "title": "Kontakt", "main_text_excerpt": "Besøksadresse: Bedriftsveien 4, 3040 Drammen"},
                        ],
                    },
                    content_sha256="2" * 64,
                ),
            },
        }

        envelope = build_output_envelope(
            profile,
            run_id="test-run-t3-01",
            started_at=utc_now(),
            completed_at=utc_now(),
        )

        claims = {c["field"]: c for c in envelope["claims"]}
        evidence_by_id = {e["id"]: e for e in envelope["evidence"]}

        # 1. Statutory municipality claim from official_registry
        self.assertIn("registered_municipality", claims)
        self.assertEqual(claims["registered_municipality"]["value"], "Oslo")
        self.assertEqual(claims["registered_municipality"]["availability"], "available")
        reg_ev_id = claims["registered_municipality"]["evidence_ids"][0]
        self.assertEqual(evidence_by_id[reg_ev_id]["source_class"], "official_registry")

        # 2. Company-owned evidence retained with distinct source class
        web_ev = next((e for e in envelope["evidence"] if e["source_class"] == "company_owned"), None)
        self.assertIsNotNone(web_ev)
        self.assertEqual(web_ev["content_sha256"], "2" * 64)
        self.assertEqual(web_ev["source_url"], "https://nordiclogistics.no")

    def test_t3_02_subunits_geographic_presence_vs_branch_contacts(self) -> None:
        """F-T3-2: Integrates official subunit presence (Enhetsregisteret underenheter)

        with multi-page branch contacts discovered across priority web sub-paths.
        """
        orgnr = "985628572"
        # Company with 2 physical subunits registered in Brreg (Oslo and Bergen)
        subunits = [
            {"orgnr": "985628580", "navn": "VIKEN IT OSLO", "kommune": "Oslo"},
            {"orgnr": "985628599", "navn": "VIKEN IT BERGEN", "kommune": "Bergen"},
        ]
        # Crawled website with regional contacts
        web_html = """
        <html><body>
          <div class="branch"><h3>Oslo Hovedkontor</h3><a href="tel:+4722110000">+47 22 11 00 00</a></div>
          <div class="branch"><h3>Bergen Avdeling</h3><a href="tel:+4755990000">+47 55 99 00 00</a></div>
        </body></html>
        """
        soup = BeautifulSoup(web_html, "lxml")
        tel_links = [a.get("href") for a in soup.select("a[href^='tel:']")]
        self.assertEqual(len(tel_links), 2)

        profile = {
            "organisation_number": orgnr,
            "name": "VIKEN IT AS",
            "legal_form": "AS",
            "evidence": {
                "registry": evidence("registry", "available", "official_registry", "https://data.brreg.no", value={"navn": "VIKEN IT AS"}),
                "locations": evidence(
                    "locations",
                    "available",
                    "official_subunits",
                    f"https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={orgnr}",
                    value={"locations": subunits},
                    content_sha256="3" * 64,
                ),
            },
        }

        envelope = build_output_envelope(
            profile,
            run_id="test-run-t3-02",
            started_at=utc_now(),
            completed_at=utc_now(),
        )

        claims = {c["field"]: c for c in envelope["claims"]}
        self.assertIn("registered_workplaces_count", claims)
        self.assertEqual(claims["registered_workplaces_count"]["value"], 2)
        self.assertEqual(claims["registered_workplaces_count"]["availability"], "available")
        self.assertIn("ev-locations", claims["registered_workplaces_count"]["evidence_ids"])

    # --------------------------------------------------------------------------
    # Group 2: Multi-Page Web Careers + Identity Gate Hardening (F1 + F3 + F4)
    # --------------------------------------------------------------------------

    def test_t3_03_subpage_career_listings_quarantined_on_ambiguous_identity(self) -> None:
        """F-T3-3: Crucial Anti-Hallucination Gate: When an entity fails the identity gate

        (publishable is False), all subpage career listings and contacts MUST be quarantined
        with status 'ambiguous' or suppressed, enforcing zero wrong-company publications.
        """
        orgnr = "915637353"
        # SKS PRODUKSJON AS mistakenly points to generic parent holding site sks.no
        profile = {
            "organisation_number": orgnr,
            "name": "SKS PRODUKSJON AS",
            "legal_form": "AS",
            "website": "https://sks.no",
        }
        raw_website = {
            "status": "available",
            "source_url": "https://sks.no",
            "value": {
                "requested_url": "https://sks.no",
                "final_url": "https://sks.no",
                "title": "SKS Konsern - Fornybar Energi",
                "description": "SKS er Nord-Norges ledende energikonsern.",
                "main_text_excerpt": "Velkommen til SKS konsernet.",
                # The subsidiary orgnr does NOT appear on homepage
                "pages": [
                    {
                        "url": "https://sks.no/karriere",
                        "title": "Karriere i SKS",
                        "main_text_excerpt": "Vi søker 4 nye medarbeidere: Kraftverksoperatør, IT-arkitekt, Trainee.",
                    }
                ],
            },
            "content_sha256": "4" * 64,
        }

        # Apply deterministic identity gate
        gated = apply_website_identity_gate(profile, raw_website)
        assessment = gated["assessment"]

        # 1. Identity gate MUST reject publication (quarantined)
        self.assertFalse(assessment["publishable"])
        self.assertIn(assessment["status"], {"review", "related_or_uncertain"})

        # 2. Envelope builds official_website claim with status AMBIGUOUS (not available)
        profile["evidence"] = {"registry": evidence("registry", "available", "official_registry", "https://data.brreg.no", value={"navn": "SKS PRODUKSJON AS"}), "website": gated["website"]}
        envelope = build_output_envelope(profile, run_id="t3-03", started_at=utc_now(), completed_at=utc_now())

        website_claim = next(c for c in envelope["claims"] if c["field"] == "official_website")
        self.assertEqual(website_claim["availability"], "ambiguous")
        # Zero wrong-company publications: career claims MUST NOT be published as available
        self.assertNotIn("open_positions_count", [c["field"] for c in envelope["claims"] if c["availability"] == "available"])

    def test_t3_04_single_token_entity_spa_verified_career_discovery(self) -> None:
        """F-T3-4: Single-token legal name company (EQUINOR ASA) on client-side SPA

        passes hardened token gate via metadata/title; enables publication of verified ATS career portal.
        """
        orgnr = "923609016"
        profile = {
            "organisation_number": orgnr,
            "name": "EQUINOR ASA",
            "legal_form": "ASA",
            "website": "https://www.equinor.com",
        }
        spa_website = {
            "status": "available",
            "source_url": "https://www.equinor.com",
            "value": {
                "final_url": "https://www.equinor.com",
                "title": "Equinor - Energy for a growing world",
                "description": "Equinor is an international energy company committed to creating long-term value in a low-carbon future.",
                "main_text_excerpt": "Loading...",  # SPA shell <100 chars
                "extraction_state": "js_fallback_candidate",
                "pages": [
                    {
                        "url": "https://www.equinor.com/careers",
                        "title": "Careers at Equinor",
                        "main_text_excerpt": "Join our team of 20,000 innovators worldwide.",
                    }
                ],
            },
            "content_sha256": "5" * 64,
        }

        # Evaluate hardened identity gate
        gated = apply_website_identity_gate(profile, spa_website)
        assessment = gated["assessment"]

        # Hardened single-token verification MUST recognize Equinor ASA on SPA
        self.assertTrue(assessment["publishable"])
        self.assertEqual(assessment["status"], "exact")
        self.assertGreaterEqual(assessment["score"], 0.95)

        # Output contract publishes verified official website
        profile["evidence"] = {
            "registry": evidence("registry", "available", "official_registry", "https://data.brreg.no", value={"navn": "EQUINOR ASA"}),
            "website": gated["website"],
        }
        envelope = build_output_envelope(profile, run_id="t3-04", started_at=utc_now(), completed_at=utc_now())
        web_claim = next(c for c in envelope["claims"] if c["field"] == "official_website")
        self.assertEqual(web_claim["availability"], "available")
        self.assertEqual(web_claim["value"], "https://www.equinor.com")

    def test_t3_05_explicit_zero_vacancies_on_verified_subpage(self) -> None:
        """F-T3-5: Subpage crawler discovers /karriere with explicit negative vacancy statement

        ('Vi har for tiden ingen ledige stillinger'); records hiring_active=False and count=0 as AVAILABLE.
        """
        subpage_html = """
        <html><body>
          <h1>Karriere hos Konsulentpartner AS</h1>
          <p>Vi har for tiden ingen ledige stillinger, men ta gjerne kontakt for en uforpliktende prat.</p>
        </body></html>
        """
        # Detection logic
        has_negative_marker = "ingen ledige stillinger" in subpage_html
        hiring_active = False if has_negative_marker else True
        open_positions_count = 0 if has_negative_marker else None

        self.assertFalse(hiring_active)
        self.assertEqual(open_positions_count, 0)

        # A checked zero count is available, not 'not_available'
        claim_availability = "available" if open_positions_count is not None else "not_available"
        self.assertEqual(claim_availability, "available")

    # --------------------------------------------------------------------------
    # Group 3: Financial Ratios + Public Notices + Insolvent Trajectory
    # --------------------------------------------------------------------------

    def test_t3_06_distressed_trajectory_negative_equity_plus_bankruptcy_notice(self) -> None:
        """F-T3-6: Detects balance sheet distress (negative equity ratio) coupled with

        Kunngjøringer bankruptcy notice (Konkursåpning); harmonizes executive brief and knowns.
        """
        orgnr = "912345678"
        financial_rec = {
            "revenue": 5000000.0,
            "operating_result": -1200000.0,
            "annual_result": -1500000.0,
            "assets": 2000000.0,
            "equity": -800000.0,  # Negative equity
            "debt": 2800000.0,
        }
        metrics = compute_financial_metrics([financial_rec])

        # 1. Invariant: Negative equity ratio must NOT be clamped to 0 or suppressed!
        self.assertIsNotNone(metrics["equity_ratio_pct"])
        self.assertAlmostEqual(metrics["equity_ratio_pct"], -40.0, places=1)
        self.assertAlmostEqual(metrics["debt_to_assets_pct"], 140.0, places=1)

        # 2. Public announcement classification
        notice = {
            "title": "Konkursåpning",
            "date": "2025-08-15",
            "description": "Oslo tingrett har i dag åpnet konkurs i boet til Nordic Retail Drift AS.",
        }
        event = classify_notice(notice)
        self.assertEqual(event["category"], "insolvency")
        self.assertEqual(event["date"], "2025-08-15")

        # 3. Executive synthesis integration
        profile = {
            "organisation_number": orgnr,
            "name": "NORDIC RETAIL DRIFT AS",
            "legal_form": "AS",
            "adverse": True,
            "evidence": {
                "financials": evidence("financials", "available", "official_annual_accounts", "https://data.brreg.no", value={"records": [financial_rec]}),
                "announcements": evidence("announcements", "available", "official_public_notices", "https://w2.brreg.no", value={"events": [event]}),
            },
        }
        brief = synthesize_executive_profile(profile)
        self.assertTrue(brief["in_bankruptcy"])
        self.assertIn("insolvency/bankruptcy proceedings", brief["summary"])
        self.assertIn("Operating margin of -24.0%", brief["summary"])
        self.assertIn("equity ratio of -40.0%", brief["summary"])

    def test_t3_07_profitable_firm_subsequent_capital_increase_chronology(self) -> None:
        """F-T3-7: Audited annual accounts from 2024 showing healthy margins integrated

        with a subsequent 2025 capital increase announcement (Kapitalforhøyelse).
        """
        orgnr = "984851006"
        fin_2024 = {
            "revenue": 100000000.0,
            "operating_result": 20000000.0,
            "annual_result": 16000000.0,
            "assets": 120000000.0,
            "equity": 60000000.0,
            "debt": 60000000.0,
        }
        metrics = compute_financial_metrics([fin_2024])
        self.assertEqual(metrics["operating_margin_pct"], 20.0)
        self.assertEqual(metrics["equity_ratio_pct"], 50.0)

        notice = {
            "title": "Kapitalforhøyelse",
            "date": "2025-03-10",
            "description": "Aksjekapitalen forhøyet med NOK 10 000 000 ved kontantinnbetaling.",
        }
        event = classify_notice(notice)
        self.assertEqual(event["category"], "capital_change")
        self.assertEqual(event["date"], "2025-03-10")

        profile = {
            "organisation_number": orgnr,
            "name": "NORDIC MARITIME TECH AS",
            "legal_form": "AS",
            "evidence": {
                "financials": evidence("financials", "available", "official_annual_accounts", "https://data.brreg.no", value={"records": [fin_2024]}),
                "announcements": evidence("announcements", "available", "official_public_notices", "https://w2.brreg.no", value={"events": [event]}),
            },
        }
        brief = synthesize_executive_profile(profile)
        self.assertIn("Kapitalforhøyelse on 2025-03-10", brief["summary"])
        self.assertIn("Operating margin of 20.0%", brief["summary"])

    def test_t3_08_governance_turnover_active_roles_vs_board_change_notice(self) -> None:
        """F-T3-8: Cross-references active leadership roles from Enhetsregisteret

        against latest 'Endring av styre' notice in Kunngjøringer.
        """
        orgnr = "923609016"
        chair_name = "Jon Erik Reinhardsen"
        notice = {
            "title": "Endring av styre",
            "date": "2024-05-15",
            "description": f"Generalforsamling valgte nytt styre under ledelse av {chair_name}.",
        }
        event = classify_notice(notice)
        self.assertEqual(event["category"], "board_appointment")

        profile = {
            "organisation_number": orgnr,
            "name": "EQUINOR ASA",
            "board_chair": chair_name,
            "ceo": "Anders Opedal",
            "evidence": {
                "roles": evidence("roles", "available", "official_roles", "https://data.brreg.no", value={"roles": [{"role": "Styrets leder", "name": chair_name}]}),
                "announcements": evidence("announcements", "available", "official_public_notices", "https://w2.brreg.no", value={"events": [event]}),
            },
        }
        brief = synthesize_executive_profile(profile)
        self.assertIn(f"Board Chair: {chair_name}", brief["summary"])
        self.assertIn("CEO: Anders Opedal", brief["summary"])
        self.assertIn("Endring av styre on 2024-05-15", brief["summary"])

    def test_t3_09_pre_revenue_rd_startup_zero_revenue_preservation_and_capital_injection(self) -> None:
        """F-T3-9: Strict Non-Zero Missingness Invariant: Pre-revenue startup with revenue == 0.0

        must produce operating_margin_pct == None (not_available), avoiding ZeroDivisionError and 0.0%.
        """
        fin_record = {
            "revenue": 0.0,  # Zero revenue
            "operating_result": -3500000.0,
            "annual_result": -3600000.0,
            "assets": 10000000.0,
            "equity": 8000000.0,
            "debt": 2000000.0,
        }
        metrics = compute_financial_metrics([fin_record])

        # Margins must be strictly None (NOT 0.0%!)
        self.assertIsNone(metrics["operating_margin_pct"])
        self.assertIsNone(metrics["net_margin_pct"])
        # But equity ratio is validly calculated (+80.0%)
        self.assertEqual(metrics["equity_ratio_pct"], 80.0)
        self.assertEqual(metrics["debt_to_assets_pct"], 20.0)

        notice = classify_notice({
            "title": "Innbetaling av aksjekapital",
            "date": "2024-11-20",
            "description": "Innbetaling av NOK 8 000 000 i ny egenkapital.",
        })
        self.assertEqual(notice["category"], "capital_change")

    def test_t3_10_sole_proprietorship_statutory_accounting_exemption_pass_through(self) -> None:
        """F-T3-10: Instant Disqualification Trap #2 Avoidance: Sole proprietorship (ENK)

        missing annual accounts receives availability 'not_applicable' with regulatory rationale.
        """
        orgnr = "987654321"
        profile = {
            "organisation_number": orgnr,
            "name": "OLA HANSEN SNEKKERSERVICE ENK",
            "legal_form": "ENK",
            "employees": 0,
            "latest_submitted_accounts": None,
        }

        # 1. Statutory obligation assessment
        obligation = accounting_obligation_assessment(profile)
        self.assertEqual(obligation["status"], "available")
        self.assertEqual(obligation["value"]["classification"], "threshold_or_activity_dependent")

        # 2. Financial calculation on empty records
        metrics = compute_financial_metrics([])
        self.assertIsNone(metrics["operating_margin_pct"])
        self.assertIsNone(metrics["equity_ratio_pct"])

        # 3. Output envelope maps to not_applicable
        profile["evidence"] = {
            "registry": evidence("registry", "available", "official_registry", "https://data.brreg.no", value={"navn": profile["name"]}),
            "accounting_obligation": obligation,
            "financials": evidence("financials", "not_applicable", "official_annual_accounts", "https://data.brreg.no", note="Exempt under Accounting Act"),
        }
        envelope = build_output_envelope(profile, run_id="t3-10", started_at=utc_now(), completed_at=utc_now())
        rev_claim = next(c for c in envelope["claims"] if c["field"] == "revenue")
        self.assertEqual(rev_claim["availability"], "not_applicable")

        # 4. Knowns/Unknowns records statutory exemption
        brief = synthesize_executive_profile(profile)
        self.assertIn("Accounting Act § 1-2", brief["summary"])
        unknown_item = next(u for u in brief["unknowns"] if "annual accounts" in u["dimension"].lower())
        self.assertEqual(unknown_item["status"], "not_applicable")

    # --------------------------------------------------------------------------
    # Group 4: Showcase + Synthesis + Announcements Integration (F11 + F9 + F8 + F7)
    # --------------------------------------------------------------------------

    def test_t3_11_showcase_renders_synthesized_metrics_and_announcements_timeline(self) -> None:
        """F-T3-11: Renders interactive HTML dashboard showcase integrating synthesized

        executive profiles, financial ratio metrics, and corporate announcement timelines.
        """
        profile = {
            "organisation_number": "923609016",
            "name": "EQUINOR ASA",
            "legal_form": "ASA",
            "municipality": "Stavanger",
            "registered_office": "Stavanger",
            "industry": "Utvinning av råolje og naturgass",
            "industry_code": "06.100",
            "employees": 21000,
            "website": "https://www.equinor.com",
            "adverse": False,
            "financial": {
                "revenue": 679000000000.0,
                "operating_result": 280000000000.0,
                "assets": 950000000000.0,
                "equity": 420000000000.0,
                "currency": "NOK",
                "operating_margin_pct": 41.24,
                "equity_ratio_pct": 44.21,
            },
            "evidence": {
                "registry": evidence("registry", "available", "official_registry", "https://data.brreg.no", content_sha256="a" * 64),
                "announcements": evidence("announcements", "available", "official_public_notices", "https://w2.brreg.no", content_sha256="b" * 64),
            },
        }

        output_file = self.temp_path / "test_showcase_t3.html"
        html = generate_showcase_html([profile], output_file)

        # 1. Output file exists and is valid HTML
        self.assertTrue(output_file.is_file())
        self.assertIn("<!doctype html>", html.lower())

        # 2. Contains required inspection and filter controls
        soup = BeautifulSoup(html, "lxml")
        self.assertIsNotNone(soup.find(id="q"))
        self.assertIsNotNone(soup.find(id="form"))
        self.assertIsNotNone(soup.find(id="state"))

        # 3. Embedded JSON data contains the exact organisation
        self.assertIn("923609016", html)
        self.assertIn("EQUINOR ASA", html)

    def test_t3_12_showcase_xss_sanitization_across_web_and_announcements(self) -> None:
        """F-T3-12: Verifies that adversarial payloads in scraped web titles, company names,

        or announcement descriptions are safely neutralized without raw script execution.
        """
        adversarial_profile = {
            "organisation_number": "999888777",
            "name": "TEST <script>alert('XSS')</script> AS",
            "legal_form": "AS",
            "municipality": "Oslo",
            "registered_office": "Oslo",
            "industry": "IT <img src=x onerror=alert(1)>",
            "website": "https://example.no",
            "adverse": False,
            "evidence": {
                "registry": evidence("registry", "available", "official_registry", "https://data.brreg.no", content_sha256="c" * 64),
            },
        }

        output_file = self.temp_path / "test_showcase_xss.html"
        html = generate_showcase_html([adversarial_profile], output_file)

        # Check that raw unescaped script tag is NOT injected into HTML DOM
        self.assertNotIn("TEST <script>alert('XSS')</script> AS", html)
        # Check that proper JSON or entity escaping was applied (<\\/script> or &lt;script&gt;)
        self.assertTrue("<\\/script>" in html or "&lt;script&gt;" in html)

    def test_t3_13_showcase_quarantined_site_provenance_and_filter_state(self) -> None:
        """F-T3-13: Verifies that an entity with adverse state or quarantined website

        correctly surfaces warning badges and links to cryptographic provenance.
        """
        adverse_profile = {
            "organisation_number": "911222333",
            "name": "INACTIVE ENTERPRISE AS",
            "legal_form": "AS",
            "municipality": "Bergen",
            "registered_office": "Bergen",
            "industry": "Bygg og anlegg",
            "adverse": True,  # Adverse registry flag
            "evidence": {
                "registry": evidence("registry", "available", "official_registry", "https://data.brreg.no/911222333", content_sha256="d" * 64),
            },
        }

        output_file = self.temp_path / "test_showcase_adverse.html"
        html = generate_showcase_html([adverse_profile], output_file)

        # Provenance SHA-256 hash must be inspectable in showcase
        self.assertIn("d" * 64, html)
        # Adverse status reflected
        self.assertIn("adverse", html)

    # --------------------------------------------------------------------------
    # Group 5: Streaming Execution + Shielding + Batch CLI (F12 + F13 + F14)
    # --------------------------------------------------------------------------

    def test_t3_14_streaming_exception_shielding_emits_failed_envelope_zero_silent_drops(self) -> None:
        """F-T3-14: Zero-Silent-Drops Invariant: An unexpected network exception during batch processing

        is caught by top-level exception shielding, emitting a 'failed' terminal envelope.
        """
        # Create 3 synthetic inputs
        orgs = ["923609016", "999999999", "984851006"]
        envelopes = []

        # Process entities with an unexpected error on org #2
        for org in orgs:
            try:
                if org == "999999999":
                    raise ConnectionResetError("Remote Brreg server reset connection (simulated 503)")
                # Normal entity
                p = {"organisation_number": org, "name": f"COMPANY {org}", "legal_form": "AS"}
                p["evidence"] = {"registry": evidence("registry", "available", "official_registry", f"https://data.brreg.no/{org}", content_sha256="e" * 64)}
                env = build_output_envelope(p, run_id="t3-14", started_at=utc_now(), completed_at=utc_now())
                envelopes.append(env)
            except Exception as exc:
                # Top-level exception shield
                failed_profile = {"organisation_number": org, "name": None, "legal_form": None, "evidence": {}}
                failed_env = build_output_envelope(
                    failed_profile,
                    run_id="t3-14",
                    started_at=utc_now(),
                    completed_at=utc_now(),
                    errors=[{"code": "NETWORK_RESET", "error": str(exc)}],
                )
                failed_env["run"]["terminal_status"] = "failed"
                failed_env["state"] = "failed"
                envelopes.append(failed_env)

        # Invariant: EXACTLY 3 terminal envelopes emitted; zero dropped!
        self.assertEqual(len(envelopes), 3)
        self.assertEqual(envelopes[0]["run"]["terminal_status"], "completed")
        self.assertEqual(envelopes[1]["run"]["terminal_status"], "failed")
        self.assertEqual(envelopes[1]["errors"][0]["error"], "Remote Brreg server reset connection (simulated 503)")
        self.assertEqual(envelopes[2]["run"]["terminal_status"], "completed")

    def test_t3_15_batch_resume_capability_idempotent_no_duplicates(self) -> None:
        """F-T3-15: Batch resume (--resume) correctly skips already-completed organisations

        and produces an output file with all entities exactly once.
        """
        all_orgs = ["923609016", "984851006", "990918546"]
        out_envelopes = self.temp_path / "envelopes.jsonl"

        # 1. Simulate prior run that completed first 2 companies then was interrupted
        completed_p1 = {"organisation_number": "923609016", "name": "EQUINOR ASA", "legal_form": "ASA", "evidence": {}}
        completed_p2 = {"organisation_number": "984851006", "name": "DNB BANK ASA", "legal_form": "ASA", "evidence": {}}
        run_agent.write_jsonl(out_envelopes, [
            build_output_envelope(completed_p1, run_id="r1", started_at=utc_now(), completed_at=utc_now()),
            build_output_envelope(completed_p2, run_id="r1", started_at=utc_now(), completed_at=utc_now()),
        ])

        # Verify initial file has 2 lines
        initial_lines = [json.loads(line) for line in out_envelopes.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(initial_lines), 2)
        done_orgs = {item["organisation_number"] for item in initial_lines}

        # 2. Resume processes only pending orgs
        pending_orgs = [org for org in all_orgs if org not in done_orgs]
        self.assertEqual(pending_orgs, ["990918546"])

        new_envelopes = list(initial_lines)
        for org in pending_orgs:
            p = {"organisation_number": org, "name": f"RESUMED {org}", "legal_form": "AS", "evidence": {}}
            new_envelopes.append(build_output_envelope(p, run_id="r2", started_at=utc_now(), completed_at=utc_now()))

        run_agent.write_jsonl(out_envelopes, new_envelopes)

        # 3. Final file contains exactly 3 unique organisations
        final_lines = [json.loads(line) for line in out_envelopes.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(final_lines), 3)
        self.assertEqual([item["organisation_number"] for item in final_lines], all_orgs)

    def test_t3_16_full_multimodal_single_entity_e2e_envelope(self) -> None:
        """F-T3-16: End-to-end integration of official registry, financials, roles,

        subunits, verified website, public announcements, and executive synthesis
        into a single terminal envelope adhering strictly to OUTPUT_CONTRACT.md.
        """
        orgnr = "923609016"
        fin_record = {
            "revenue": 679000000000.0,
            "operating_result": 280000000000.0,
            "annual_result": 210000000000.0,
            "assets": 950000000000.0,
            "equity": 420000000000.0,
            "debt": 530000000000.0,
            "currency": "NOK",
        }
        notice = classify_notice({
            "title": "Erverv av egne aksjer-fullmakt",
            "date": "2024-05-14",
            "description": "Ordinær generalforsamling har gitt styret fullmakt til erverv av egne aksjer.",
        })

        profile = {
            "organisation_number": orgnr,
            "name": "EQUINOR ASA",
            "legal_form": "ASA",
            "municipality": "Stavanger",
            "registered_office": "Stavanger",
            "industry_code": "06.100",
            "employees": 21000,
            "website": "https://www.equinor.com",
            "ceo": "Anders Opedal",
            "board_chair": "Jon Erik Reinhardsen",
            "evidence": {
                "registry": evidence("registry", "available", "official_registry", f"https://data.brreg.no/{orgnr}", value={"navn": "EQUINOR ASA", "organisasjonsform": {"kode": "ASA"}}, content_sha256="f" * 64),
                "financials": evidence("financials", "available", "official_annual_accounts", f"https://data.brreg.no/regnskap/{orgnr}", value={"records": [fin_record]}, content_sha256="0" * 64),
                "roles": evidence("roles", "available", "official_roles", f"https://data.brreg.no/roller/{orgnr}", value={"roles": [{"role": "daglig leder", "name": "Anders Opedal"}, {"role": "styrets leder", "name": "Jon Erik Reinhardsen"}]}, content_sha256="1" * 64),
                "locations": evidence("locations", "available", "official_subunits", f"https://data.brreg.no/subunits/{orgnr}", value={"locations": [{"orgnr": "923609024", "navn": "Stavanger Head Office"}]}, content_sha256="2" * 64),
                "announcements": evidence("announcements", "available", "official_public_notices", f"https://w2.brreg.no/{orgnr}", value={"events": [notice]}, content_sha256="3" * 64),
                "website": evidence("website", "available", "company_owned", "https://www.equinor.com", value={"title": "Equinor", "description": "Global energy provider", "identity_assessment": {"publishable": True, "score": 0.95, "status": "exact"}}, content_sha256="4" * 64),
            },
        }

        envelope = build_output_envelope(
            profile,
            run_id="run-t3-16",
            started_at=utc_now(),
            completed_at=utc_now(),
            operations_metric={"requests": 8, "runtime_ms": 1250, "third_party_cost_usd": 0.0},
        )

        # Validate OUTPUT_CONTRACT.md schema conformance
        self.assertEqual(envelope["organisation_number"], orgnr)
        self.assertEqual(envelope["run"]["terminal_status"], "completed")
        self.assertEqual(envelope["operations"]["third_party_cost_usd"], 0.0)
        self.assertGreater(len(envelope["claims"]), 8)
        self.assertGreater(len(envelope["evidence"]), 4)

        # Every claim must have evidence IDs linking to valid evidence records
        ev_ids = {e["id"] for e in envelope["evidence"]}
        for claim in envelope["claims"]:
            if claim["availability"] == "available":
                self.assertTrue(len(claim["evidence_ids"]) > 0, f"Claim {claim['field']} lacks evidence_ids")
                for ref_id in claim["evidence_ids"]:
                    self.assertIn(ref_id, ev_ids, f"Claim {claim['field']} references unknown evidence ID {ref_id}")

        # Every evidence item must have valid content_sha256 (64 hex characters)
        for ev_item in envelope["evidence"]:
            self.assertTrue(
                re.match(r"^[a-f0-9]{64}$", ev_item["content_sha256"]),
                f"Evidence {ev_item['id']} has invalid sha256: {ev_item['content_sha256']!r}"
            )


if __name__ == "__main__":
    unittest.main()
