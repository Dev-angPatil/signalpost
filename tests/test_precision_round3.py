"""Precision round 3: claims a checker would mark wrong in the 2026-11-20 audit run.

Each test pins one rule:
- career pages are recognised by a whole URL path segment, never a substring or the hostname;
- a career page with no readable vacancies publishes no hiring status at all;
- ATS platform home pages and employee logins are not career portals;
- auditor and CEO notices never date a "board appointment";
- year-on-year revenue growth needs consecutive full-year filings;
- a headline match that runs on into a longer capitalised name is another entity;
- contact values cite the page they were read from, and evidence ids are unique;
- a website-derived business description cites the website, not the registry;
- glued or system e-mail addresses are not contacts.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.announcements import classify_notice
from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import evidence
from norway_company_agent.news import exact_title_match
from norway_company_agent.synthesis import compute_financial_metrics
from norway_company_agent.website import (
    _extract_career_portals,
    clean_email,
    extract_contact_info,
    is_career_url,
)


class CareerUrlTests(unittest.TestCase):
    def test_real_career_pages(self):
        for url in (
            "https://www.jibygg.no/karriere",
            "https://gokstadapotek.no/ledige-stillinger/",
            "https://www.dahl.no/jobb-i-bd",
            "https://www.lofotenbeachcamp.no/work-with-us",
            "https://karriere.skytale.no/",
            "https://firma.no/om-oss/karriere/",
            "https://firma.no/en/careers",
        ):
            self.assertTrue(is_career_url(url), url)

    def test_substrings_and_hostnames_are_not_career_pages(self):
        for url in (
            "https://stasartist.no/forestillinger",  # theatre shows
            "https://sjofartsmuseum.museumvest.no/utstillinger",  # exhibitions
            "https://www.spiroslo.no/bedrifter/spir-jobbfrukt",  # fruit baskets
            "https://delange.io/innsikt/jobbe-i-konsulentselskap",  # an article
            "https://www.gronnjobb.no/",  # "jobb" in the domain name
            "https://www.gronnjobb.no/login",
        ):
            self.assertFalse(is_career_url(url), url)

    def test_platform_home_and_logins_are_not_portals(self):
        soup = BeautifulSoup(
            '<a href="https://www.teamtailor.com/">Karriereside av Teamtailor</a>'
            '<a href="https://app.teamtailor.com/companies/x/dashboard">Medarbeiderinnlogging</a>'
            '<a href="https://firma.teamtailor.com/">Ledige stillinger</a>',
            "lxml",
        )
        urls = [p["url"] for p in _extract_career_portals("https://firma.no/", soup)]
        self.assertEqual(urls, ["https://firma.teamtailor.com/"])


class HiringClaimTests(unittest.TestCase):
    def _claims(self, hiring: dict, pages: list[dict]) -> dict:
        org = "923609016"
        reg = evidence("registry", "available", "official_registry", f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}",
                       value={"navn": "EXAMPLE ASA"}, content_sha256="a" * 64)
        web = evidence("website", "available", "company_owned", "https://example.no/",
                       value={"title": "Example", "hiring": hiring, "pages": pages,
                              "identity_assessment": {"publishable": True, "score": 1.0}},
                       content_sha256="d" * 64)
        env = build_output_envelope({"organisation_number": org, "name": "EXAMPLE ASA",
                                     "evidence": {"registry": reg, "website": web}},
                                    run_id="t", started_at="2026-11-20T00:00:00Z", completed_at="2026-11-20T00:00:01Z")
        return {c["field"]: c for c in env["claims"]}

    def test_career_page_without_readable_vacancies_publishes_no_status(self):
        claims = self._claims(
            {"career_url": "https://example.no/ledige-stillinger/", "hiring_active": None,
             "open_positions_count": None, "open_positions": []},
            [{"url": "https://example.no/ledige-stillinger/", "title": "Ledige stillinger", "content_sha256": "e" * 64}],
        )
        self.assertEqual(claims["career_url"]["availability"], "available")
        self.assertEqual(claims["career_url"]["evidence_ids"], ["ev-website-career"])
        for field in ("hiring_active", "open_positions_count", "open_positions"):
            self.assertNotEqual(claims[field]["availability"], "available", field)


class NoticeCategoryTests(unittest.TestCase):
    def test_board_category_is_board_only(self):
        self.assertEqual(classify_notice({"title": "Styre"})["category"], "board_appointment")
        for title in ("Revisor", "Daglig leder", "Rettelse av revisor"):
            self.assertNotEqual(classify_notice({"title": title})["category"], "board_appointment", title)


class GrowthTests(unittest.TestCase):
    @staticmethod
    def _rec(year: int, revenue: float) -> dict:
        return {"revenue": revenue, "period": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"}}

    def test_consecutive_years(self):
        m = compute_financial_metrics([self._rec(2025, 120.0), self._rec(2024, 100.0)])
        self.assertEqual(m["multi_year_revenue_growth_pct"], 20.0)

    def test_gap_year_gives_no_yoy(self):
        m = compute_financial_metrics([self._rec(2025, 120.0), self._rec(2023, 100.0)])
        self.assertIsNone(m["multi_year_revenue_growth_pct"])

    def test_short_prior_period_gives_no_yoy(self):
        short = {"revenue": 10.0, "period": {"fraDato": "2024-09-01", "tilDato": "2024-12-31"}}
        m = compute_financial_metrics([self._rec(2025, 120.0), short])
        self.assertIsNone(m["multi_year_revenue_growth_pct"])


class HeadlineTests(unittest.TestCase):
    def test_longer_capitalised_name_is_another_entity(self):
        self.assertFalse(exact_title_match("PARAT", "Parat Media går inn i ny periode med nytt styre - parat24"))
        self.assertTrue(exact_title_match("PARAT", "Parat streiker videre - NTB Kommunikasjon"))
        self.assertTrue(exact_title_match("PARAT", "Parat: Sikret reallønnsvekst i HUK-oppgjøret - NTB"))
        self.assertTrue(exact_title_match("AURSKOG SPAREBANK", "AURSKOG SPAREBANK: RESULTAT FØR SKATT 99M - Finansavisen"))


class ContactEvidenceTests(unittest.TestCase):
    def test_each_value_cites_its_page_and_ids_are_unique(self):
        org = "923609016"
        reg = evidence("registry", "available", "official_registry", f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}",
                       value={"navn": "EXAMPLE ASA"}, content_sha256="a" * 64)
        web = evidence("website", "available", "company_owned", "https://example.no/", value={
            "title": "Example",
            "contacts": {"emails": ["post@example.no", "kari@example.no"], "phones": ["+4722000000", "+47 22 00 00 00"], "addresses": []},
            "pages": [
                {"url": "https://example.no/kontakt", "title": "Kontakt", "content_sha256": "b" * 64,
                 "contacts": {"emails": ["post@example.no"], "phones": [], "addresses": []}},
                {"url": "https://example.no/kontakt-oss", "title": "Kontakt oss", "content_sha256": "c" * 64,
                 "contacts": {"emails": [], "phones": [], "addresses": []}},
                {"url": "https://example.no/ansatte", "title": "Ansatte", "content_sha256": "e" * 64,
                 "contacts": {"emails": ["kari@example.no"], "phones": [], "addresses": []}},
                {"url": "https://example.no/artikler/nordkontakt-60-ar", "title": "Nordkontakt 60 år", "content_sha256": "f" * 64,
                 "contacts": {"emails": [], "phones": [], "addresses": []}},
            ],
            "identity_assessment": {"publishable": True, "score": 1.0},
        }, content_sha256="d" * 64)
        env = build_output_envelope({"organisation_number": org, "name": "EXAMPLE ASA", "evidence": {"registry": reg, "website": web}},
                                    run_id="t", started_at="2026-11-20T00:00:00Z", completed_at="2026-11-20T00:00:01Z")
        ids = [e["id"] for e in env["evidence"]]
        self.assertEqual(len(ids), len(set(ids)))
        by_id = {e["id"]: e for e in env["evidence"]}
        claims = {c["field"]: c for c in env["claims"]}
        cited = {by_id[i]["source_url"] for i in claims["contact_email"]["evidence_ids"]}
        self.assertEqual(cited, {"https://example.no/kontakt", "https://example.no/ansatte"})
        # A phone seen only on the homepage cites the homepage; one spelling per number.
        self.assertEqual(claims["contact_phone"]["value"], ["+4722000000"])
        self.assertEqual([by_id[i]["source_url"] for i in claims["contact_phone"]["evidence_ids"]], ["https://example.no/"])
        # "nordkontakt" in an article slug is not a contact page.
        article = next(e for e in env["evidence"] if e["source_url"].endswith("nordkontakt-60-ar"))
        self.assertFalse(article["id"].startswith("ev-website-contact"))


class EmailTests(unittest.TestCase):
    def test_system_senders_are_not_contacts(self):
        self.assertIsNone(clean_email("noreply@aventra.no"))
        self.assertIsNone(clean_email("no-reply@firma.no"))
        self.assertEqual(clean_email("post@firma.no"), "post@firma.no")

    def test_glued_label_is_not_an_address(self):
        html = "<div><span>Frivilligansvarlig</span><span>frivillig@festival.no</span></div>"
        soup = BeautifulSoup(html, "lxml")
        found = extract_contact_info(soup, "Frivilligansvarligfrivillig@festival.no", registered_domain="festival.no")
        self.assertIn("frivillig@festival.no", found["emails"])
        self.assertNotIn("frivilligansvarligfrivillig@festival.no", found["emails"])


if __name__ == "__main__":
    unittest.main()


class AddressTests(unittest.TestCase):
    def test_only_registered_streets_are_published(self):
        from norway_company_agent.contract import clean_registered_addresses
        profile = {"evidence": {
            "registry": {"value": {"forretningsadresse": {"adresse": ["Storgata 8"], "postnummer": "6993", "poststed": "HØYANGER"}}},
            "locations": {"value": {"locations": [{"address": {"adresse": ["Mølnåsen 77 A"], "postnummer": "9415", "poststed": "HARSTAD"}}]}},
        }}
        out = clean_registered_addresses([
            "Storgata 8, 6993 Høyanger Fakturaadresse",
            "Omsorgsbygget Storgata 10, 6993 Høyanger",  # a building they manage, same town
            "Mølnåsen 77A, 9415 Harstad",
        ], profile)
        self.assertEqual(out, ["Storgata 8, 6993 Høyanger", "Mølnåsen 77A, 9415 Harstad"])
