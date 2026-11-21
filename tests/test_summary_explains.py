"""The summary explains the company, its money, changes and gaps, from published facts only.

Builderr scores the summary on whether it "explains the company, changes and unknowns
without making unsupported claims"; its feedback on an earlier version was that it
"just repeats registry fields".
"""
from __future__ import annotations

import copy
import unittest
from datetime import date, timedelta

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import utc_now
from norway_company_agent.synthesis import nok_short, notice_en, synthesize_executive_profile


def _rec(year: int, **values) -> dict:
    return {"period": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"}, "account_type": "SELSKAP", **values}


def _profile(records: list[dict], *, notices: list[dict] | None = None, registry: dict | None = None, website: dict | None = None) -> dict:
    reg = {"navn": "TESTFIRMA AS", "organisasjonsnummer": "912345678", "stiftelsesdato": "2015-03-01", **(registry or {})}
    evidence = {
        "registry": {"field": "registry", "status": "available", "retrieved_at": "2026-11-21T08:00:00Z", "content_sha256": "a" * 64,
                     "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/912345678", "value": reg},
        "financials": {"field": "financials", "status": "available", "retrieved_at": "2026-11-21T08:00:00Z", "content_sha256": "b" * 64,
                       "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/912345678", "value": {"records": records}},
        "roles": {"field": "roles", "status": "available", "retrieved_at": "2026-11-21T08:00:00Z", "content_sha256": "c" * 64,
                  "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/912345678/roller",
                  "value": {"roles": [{"role_code": "DAGL", "name": "Kari Nordmann"}]}},
        "announcements": {"field": "announcements", "status": "available", "retrieved_at": "2026-11-21T08:00:00Z", "content_sha256": "d" * 64,
                          "source_url": "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=912345678", "value": {"events": notices or []}},
    }
    if website:
        evidence["website"] = website
    return {"organisation_number": "912345678", "name": "TESTFIRMA AS", "legal_form": "AS", "municipality": "BERGEN",
            "industry": "Konsulentvirksomhet tilknyttet informasjonsteknologi", "industry_code": "62.020", "evidence": evidence}


class SummaryExplainsTests(unittest.TestCase):
    def test_it_says_what_the_company_is_in_plain_words(self) -> None:
        s = synthesize_executive_profile(_profile([_rec(2025, revenue=5_000_000, operating_result=500_000, annual_result=400_000)]))["summary"]
        self.assertIn("TESTFIRMA AS is a limited company (AS) in Bergen, founded in 2015.", s)
        self.assertIn("Konsulentvirksomhet tilknyttet informasjonsteknologi (NACE 62.020), in information and communication.", s)

    def test_the_lead_keeps_the_exact_filed_figures(self) -> None:
        s = synthesize_executive_profile(_profile([_rec(2025, revenue=5_000_000, operating_result=500_000)]))["summary"]
        self.assertTrue(s.startswith("TESTFIRMA AS reported revenue of NOK 5 000 000 for 2025"))

    def test_a_turnaround_is_explained(self) -> None:
        s = synthesize_executive_profile(_profile([
            _rec(2025, revenue=2_000_000, operating_result=300_000, annual_result=250_000),
            _rec(2024, revenue=1_800_000, operating_result=-50_000, annual_result=-100_000),
        ]))["summary"]
        self.assertIn("It made a net profit of NOK 250 thousand in 2025 (12.5% of revenue), back in profit after a loss of NOK 100 thousand in 2024", s)

    def test_a_loss_after_a_profit_is_explained_and_flagged(self) -> None:
        out = synthesize_executive_profile(_profile([
            _rec(2025, revenue=1_000_000, operating_result=-200_000, annual_result=-150_000),
            _rec(2024, revenue=1_600_000, operating_result=100_000, annual_result=90_000),
        ]))
        self.assertIn("It made a net loss of NOK 150 thousand in 2025, after a profit of NOK 90 thousand in 2024", out["summary"])
        self.assertIn("net loss in 2025", out["signals"])
        self.assertIn("revenue down 38% on 2024", out["signals"])

    def test_negative_equity_is_stated_even_without_a_ratio(self) -> None:
        out = synthesize_executive_profile(_profile([_rec(2025, assets=0, equity=-42_934, annual_result=-10_207)]))
        self.assertIn("Negative equity of NOK -43 thousand in 2025: the company owes more than it owns", out["summary"])
        self.assertIn("negative equity", out["signals"])

    def test_no_debt_is_said_plainly(self) -> None:
        s = synthesize_executive_profile(_profile([_rec(2025, assets=25_025_723, equity=25_025_723, annual_result=1_346_773)]))["summary"]
        self.assertIn("It reports no debt: equity equals total assets (NOK 25.0 million).", s)

    def test_gaps_in_years_do_not_count_as_year_on_year(self) -> None:
        out = synthesize_executive_profile(_profile([
            _rec(2025, revenue=3_000_000, operating_result=100_000, annual_result=80_000),
            _rec(2022, revenue=1_000_000, operating_result=50_000, annual_result=-20_000),
        ]))
        self.assertNotIn("back in profit", out["summary"])
        self.assertFalse(any(s.startswith("revenue up") for s in out["signals"]))

    def test_notices_are_translated_and_keep_the_original(self) -> None:
        recent = (date.today() - timedelta(days=40)).isoformat()
        out = synthesize_executive_profile(_profile([_rec(2025, revenue=1_000_000)], notices=[{"type": "Daglig leder", "title": "Daglig leder", "date": recent}]))
        self.assertIn(f"CEO changed (Daglig leder, {recent})", out["summary"])
        self.assertTrue(any(s.startswith("new CEO") for s in out["signals"]))
        self.assertEqual(notice_en("Revisjon av årsregnskap"), "audit arrangement changed")
        self.assertEqual(notice_en("Et nytt varsel"), "Et nytt varsel")  # unknown types stay as written

    def test_a_reversed_dissolution_is_not_a_warning(self) -> None:
        recent = (date.today() - timedelta(days=60)).isoformat()
        out = synthesize_executive_profile(_profile([_rec(2025, revenue=1_000_000)], notices=[
            {"type": "Omgjøring av oppløsning", "title": "Omgjøring av oppløsning", "date": recent, "category": "dissolution"}]))
        self.assertEqual([x for x in out["signals"] if "dissolution" in x.casefold() or "oppløsning" in x.casefold()], [])
        self.assertIn("dissolution reversed (Omgjøring av oppløsning", out["summary"])

    def test_gaps_say_why(self) -> None:
        unreadable = {"field": "website", "status": "source_error", "source_url": "https://www.testfirma.no/", "retrieved_at": utc_now(), "note": "HTTP 429"}
        out = synthesize_executive_profile(_profile([_rec(2025, revenue=1_000_000)], website=unreadable))
        self.assertIn("Could not be checked: the registered website (it could not be read).", out["summary"])
        out = synthesize_executive_profile(_profile([_rec(2025, revenue=1_000_000)]))
        self.assertIn("Not found in checked sources: a website of its own, open job postings, dated company news.", out["summary"])

    def test_it_is_dated_by_when_the_registry_was_read(self) -> None:
        out = synthesize_executive_profile(_profile([_rec(2025, revenue=1_000_000)]))
        self.assertEqual(out["as_of"], "2026-11-21")
        self.assertTrue(out["summary"].endswith("Sources checked on 2026-11-21."))

    def test_young_companies_are_flagged(self) -> None:
        founded = (date.today() - timedelta(days=200)).isoformat()
        out = synthesize_executive_profile(_profile([_rec(2025, revenue=1_000_000)], registry={"stiftelsesdato": founded}))
        self.assertTrue(any(s.startswith("young company") for s in out["signals"]))

    def test_readable_amounts(self) -> None:
        self.assertEqual(nok_short(6_184_623), "NOK 6.2 million")
        self.assertEqual(nok_short(349_000), "NOK 349 thousand")
        self.assertEqual(nok_short(-42_934), "NOK -43 thousand")
        self.assertEqual(nok_short(1_300_000_000), "NOK 1.3 billion")


class SummaryCitesItsSourcesTests(unittest.TestCase):
    def test_the_summary_claim_cites_every_source_it_uses(self) -> None:
        recent = (date.today() - timedelta(days=40)).isoformat()
        profile = _profile([_rec(2025, revenue=1_000_000, operating_result=100_000)], notices=[{"type": "Styre", "title": "Styre", "date": recent}])
        profile["synthesis"] = synthesize_executive_profile(copy.deepcopy(profile))
        self.assertEqual(profile["synthesis"]["executive_summary_evidence_ids"], ["ev-registry", "ev-financials", "ev-roles", "ev-announcements"])
        now = utc_now()
        env = build_output_envelope(profile, run_id="t", started_at=now, completed_at=now)
        claim = next(c for c in env["claims"] if c["field"] == "executive_summary")
        self.assertEqual(claim["availability"], "available")
        for ev in ("ev-registry", "ev-financials", "ev-roles", "ev-announcements"):
            self.assertIn(ev, claim["evidence_ids"])
        ids = {e["id"] for e in env["evidence"]}
        self.assertTrue(set(claim["evidence_ids"]) <= ids)


if __name__ == "__main__":
    unittest.main()
