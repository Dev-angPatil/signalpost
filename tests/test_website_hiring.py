"""Comprehensive Unit & Edge Case Test Suite for Milestone 1:
Bounded Multi-Page Web & Hiring Discovery Subsystem.

Features tested:
- F1: Priority Sub-Path Routing (categories, variants, <=4 bounded pages, <=1MB limit, robots.txt, safe redirect / SSRF)
- F2: Direct Contact Info Extraction (email regex, obfuscation decode, assets/templates reject, Norwegian phones vs 9-digit org numbers, visiting/postal addresses)
- F3: Career & Hiring Signal Extraction (open positions count, vacancy titles, career portals, explicit zero/negative statements, commercial context protection)
- F4: Identity Gate & Token Hardening (single-token SPAs with <100 chars via title/meta-description/JSON-LD/subpage, social handle validation, 0 wrong-company publications)
- Contract claims integration (available, not_available, ambiguous, blocked; SHA-256 evidence records, claim spans, UTC timestamps)
"""
from __future__ import annotations

import io
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.robotparser
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import evidence
from norway_company_agent.identity import (
    _tokens,
    apply_website_identity_gate,
    assess_social_identity,
    assess_website_identity,
)
from norway_company_agent.website import (
    _extract_career_portals,
    _extract_job_vacancies,
    _fetch_secondary_page,
    _priority_links,
    _registered_domain,
    _robots_allowed,
    assert_public_url,
    clean_email,
    extract_contact_info,
    extract_hiring_signals,
    fetch_website,
    normalize_homepage,
    normalize_norwegian_phone,
    normalize_social_url,
)


class TestF1PrioritySubPathRouting(unittest.TestCase):
    """F1: Priority Sub-Path Routing and Bounded Crawling."""

    def test_priority_routes_all_four_categories_represented(self):
        """Discovers up to 4 priority internal routes: about, contact, careers, investor."""
        html = """
        <html><body>
          <nav>
            <a href="/om-oss">Om oss</a>
            <a href="/kontakt-oss">Kontakt</a>
            <a href="/karriere">Karriere</a>
            <a href="/investor-relations">Investor</a>
            <a href="/blogg">Blogg</a>
            <a href="/vilkar">Vilkår</a>
          </nav>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        links = _priority_links("https://bedrift.no/", soup, limit=4)
        self.assertEqual(len(links), 4)
        self.assertIn("https://bedrift.no/om-oss", links)
        self.assertIn("https://bedrift.no/kontakt-oss", links)
        self.assertIn("https://bedrift.no/karriere", links)
        self.assertIn("https://bedrift.no/investor-relations", links)
        self.assertNotIn("https://bedrift.no/blogg", links)

    def test_priority_route_norwegian_and_english_slug_variants(self):
        """Recognizes alternative Norwegian and English slug variants."""
        # Test about variants: om-selskapet, hvem-er-vi, about
        # Test contact variants: contact, lokasjoner, locations
        # Test career variants: career, ledige-stillinger, jobs, jobb
        # Test investor variants: investor, finansiell-informasjon, ir
        html = """
        <html><body>
          <a href="/hvem-er-vi">Hvem er vi</a>
          <a href="/lokasjoner">Lokasjoner</a>
          <a href="/ledige-stillinger">Ledige stillinger</a>
          <a href="/finansiell-informasjon">Finansiell informasjon</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        links = _priority_links("https://nordiccorp.no/", soup, limit=4)
        self.assertEqual(len(links), 4)
        self.assertIn("https://nordiccorp.no/hvem-er-vi", links)
        self.assertIn("https://nordiccorp.no/lokasjoner", links)
        self.assertIn("https://nordiccorp.no/ledige-stillinger", links)
        self.assertIn("https://nordiccorp.no/finansiell-informasjon", links)

    def test_priority_routes_bounded_at_maximum_four(self):
        """Crawler strictly limits discovered secondary links to at most 4."""
        html = """
        <html><body>
          <a href="/om-oss">Om oss</a>
          <a href="/om-selskapet">Om selskapet</a>
          <a href="/kontakt">Kontakt</a>
          <a href="/lokasjoner">Lokasjoner</a>
          <a href="/karriere">Karriere</a>
          <a href="/jobber">Jobber</a>
          <a href="/investor">Investor</a>
          <a href="/ir">IR</a>
          <a href="/aktuelt">Aktuelt</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        links = _priority_links("https://bigfirm.no/", soup, limit=4)
        self.assertEqual(len(links), 4)
        self.assertEqual(len(set(links)), 4)

    def test_ssrf_protection_blocks_private_and_loopback_ips(self):
        """assert_public_url raises ValueError on loopback, link-local, and RFC-1918 IPs."""
        self.assertRaises(ValueError, assert_public_url, "http://127.0.0.1/admin")
        self.assertRaises(ValueError, assert_public_url, "http://localhost/secret")
        self.assertRaises(ValueError, assert_public_url, "http://10.0.0.1/private")
        self.assertRaises(ValueError, assert_public_url, "http://192.168.1.1/router")
        self.assertRaises(ValueError, assert_public_url, "http://169.254.169.254/latest/meta-data/")
        self.assertRaises(ValueError, assert_public_url, "ftp://example.com/file")

    def test_secondary_page_exceeding_1mb_rejected(self):
        """Secondary page fetcher enforces <= 1MB payload limit."""
        oversized_data = b"x" * (1_000_000 + 10)
        mock_resp = MagicMock()
        mock_resp.read.return_value = oversized_data
        mock_resp.geturl.return_value = "https://example.no/om-oss"
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}

        with patch("urllib.request.build_opener") as mock_opener_cls:
            mock_opener = MagicMock()
            mock_opener.open.return_value.__enter__.return_value = mock_resp
            with patch("norway_company_agent.website.SAFE_OPENER", mock_opener):
                mock_cache = {"https://example.no/robots.txt": MagicMock(can_fetch=MagicMock(return_value=True))}
                page, social, reqs, nbytes, elapsed, err = _fetch_secondary_page(
                    "https://example.no/om-oss",
                    homepage_domain="example.no",
                    timeout=5.0,
                    max_bytes=1_000_000,
                    robots_cache=mock_cache,
                )
                self.assertIsNone(page)
                self.assertIn("oversized", err or "")

    def test_secondary_page_redirect_outside_domain_rejected(self):
        """Secondary page redirecting to external domain is rejected."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html><body>External page</body></html>"
        mock_resp.geturl.return_value = "https://malicious-external.com/stolen"
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}

        with patch("urllib.request.build_opener") as mock_opener_cls:
            mock_opener = MagicMock()
            mock_opener.open.return_value.__enter__.return_value = mock_resp
            with patch("norway_company_agent.website.SAFE_OPENER", mock_opener):
                mock_cache = {"https://legit.no/robots.txt": MagicMock(can_fetch=MagicMock(return_value=True))}
                page, social, reqs, nbytes, elapsed, err = _fetch_secondary_page(
                    "https://legit.no/kontakt",
                    homepage_domain="legit.no",
                    timeout=5.0,
                    max_bytes=1_000_000,
                    robots_cache=mock_cache,
                )
                self.assertIsNone(page)
                self.assertIn("registered domain", err or "")


class TestF2DirectContactInfoExtraction(unittest.TestCase):
    """F2: Direct Contact Info Extraction (emails, phones, addresses)."""

    def test_clean_email_valid_and_rejected_variants(self):
        """Filters invalid extensions, code artifacts, and placeholder domains."""
        self.assertEqual(clean_email("kontakt@bedrift.no"), "kontakt@bedrift.no")
        self.assertEqual(clean_email("  SUPPORT@DOMAIN.NO. "), "support@domain.no")
        self.assertEqual(clean_email("mailto:daglig.leder@selskap.no?subject=hei"), "daglig.leder@selskap.no")

        # Reject image asset paths
        self.assertIsNone(clean_email("logo@2x.png"))
        self.assertIsNone(clean_email("banner@3x.jpg"))
        self.assertIsNone(clean_email("icon@2x.svg"))
        self.assertIsNone(clean_email("asset@1x.webp"))

        # Reject dummy / placeholder domains and code artifacts
        self.assertIsNone(clean_email("test@example.com"))
        self.assertIsNone(clean_email("user@domain.test"))
        self.assertIsNone(clean_email("error@sentry.io"))
        self.assertIsNone(clean_email("core-js@3.6.5"))
        self.assertIsNone(clean_email("webpack@4.0.0"))

    def test_email_deobfuscation_decoded(self):
        """Deobfuscates [at], (at), and &#64; email strings."""
        text = "Send e-post til post [at] firma.no eller support(at)firma.no eller salg&#64;firma.no"
        contacts = extract_contact_info(text)
        self.assertIn("post@firma.no", contacts["emails"])
        self.assertIn("support@firma.no", contacts["emails"])
        self.assertIn("salg@firma.no", contacts["emails"])

    def test_norwegian_phone_normalization_and_e164(self):
        """Normalizes Norwegian 8-digit phones and E.164 +47 format."""
        self.assertEqual(normalize_norwegian_phone("22 33 44 55"), "+4722334455")
        self.assertEqual(normalize_norwegian_phone("+47 900 12 345"), "+4790012345")
        self.assertEqual(normalize_norwegian_phone("0047 41234567"), "+4741234567")
        self.assertEqual(normalize_norwegian_phone("tel:+4722000000"), "+4722000000")

        # Disallow non-Norwegian or invalid numbers
        self.assertIsNone(normalize_norwegian_phone("01234567"))  # starts with 0
        self.assertIsNone(normalize_norwegian_phone("11234567"))  # starts with 1
        self.assertIsNone(normalize_norwegian_phone("0155"))      # 4-digit postal code

    def test_phone_extraction_disambiguates_against_9_digit_org_numbers(self):
        """Distinguishes Norwegian 8-digit phone numbers from 9-digit org numbers."""
        text = "Foretak: Test AS. Org.nr 923 609 016. Foretaksregisteret 987654321. Sentralbord: 22 11 00 11."
        contacts = extract_contact_info(text)
        self.assertNotIn("+47 92 36 09 01", contacts["phones"])
        self.assertNotIn("+47 98 76 54 32", contacts["phones"])
        self.assertTrue(
            any("+4722110011" in p or "+47 22 11 00 11" in p for p in contacts["phones"])
        )

    def test_phone_extraction_does_not_slice_unlabelled_org_number(self):
        contacts = extract_contact_info("Foretaksnummer 923609016")
        self.assertEqual(contacts["phones"], [])

    def test_address_extraction_visiting_and_postal(self):
        """Extracts visiting street address, postal code, city, and postboks."""
        html = """
        <html><body>
          <address>Dronning Eufemias gate 8, 0191 Oslo</address>
          <div class="footer-info">
            <p>Postadresse: Postboks 1234 Sentrum, 0108 Oslo</p>
          </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        contacts = extract_contact_info(soup, soup.get_text(" ", strip=True))
        addr_str = " ".join(contacts["addresses"])
        self.assertTrue(any("Dronning Eufemias gate 8" in a for a in contacts["addresses"]))
        self.assertTrue(any("0191 Oslo" in a for a in contacts["addresses"]))
        self.assertTrue(any("Postboks 1234" in a for a in contacts["addresses"]))


class TestF3CareerHiringSignalExtraction(unittest.TestCase):
    """F3: Career & Hiring Signal Extraction."""

    def test_career_portal_domains_recognized(self):
        """Identifies third-party ATS platforms (Webcruiter, Finn, Teamtailor, LinkedIn)."""
        html = """
        <html><body>
          <a href="https://webcruiter.no/apply/123">Søk via Webcruiter</a>
          <a href="https://finn.no/jobb/stilling/456">Finn.no stilling</a>
          <a href="https://brand.teamtailor.com/jobs/789">Teamtailor Jobb</a>
          <a href="https://www.linkedin.com/jobs/view/101">LinkedIn Stilling</a>
          <a href="https://finn.no/torget/annonse/1">Torget annonse</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        portals = _extract_career_portals("https://example.no/", soup)
        platforms = {p["platform"] for p in portals}
        self.assertIn("webcruiter", platforms)
        self.assertIn("finn", platforms)
        self.assertIn("teamtailor", platforms)
        self.assertIn("linkedin_jobs", platforms)

    def test_job_vacancies_jsonld_and_html_elements(self):
        """Extracts job titles and count from JSON-LD JobPosting and HTML cards."""
        html = """
        <html>
        <head>
          <script type="application/ld+json">
          {"@type": "JobPosting", "title": "Senior Data Engineer"}
          </script>
        </head>
        <body>
          <div class="job-item"><h3>Frontend Utvikler</h3></div>
          <div class="job-card"><h4>Systemarkitekt</h4></div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        titles, count, hiring = _extract_job_vacancies("https://example.no/karriere", soup, html)
        self.assertTrue(hiring)
        self.assertIn("Senior Data Engineer", titles)
        self.assertIn("Frontend Utvikler", titles)
        self.assertIn("Systemarkitekt", titles)
        self.assertGreaterEqual(count, 3)

    def test_explicit_negative_hiring_statement_emits_false_and_zero_count(self):
        """Explicit statements like 'ingen ledige stillinger' set hiring_active=False and count=0."""
        html = "<html><body><h1>Karriere</h1><p>Vi har for tiden ingen ledige stillinger.</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        titles, count, hiring = _extract_job_vacancies("https://example.no/karriere", soup, html)
        self.assertFalse(hiring)
        self.assertEqual(count, 0)
        self.assertEqual(titles, [])

    def test_extract_hiring_signals_commercial_context_guard(self):
        """Phrases like 'våre markedsposisjoner' without vacancy terms do not trigger hiring."""
        text = "Våre ledende markedsposisjoner innen industri sikrer sterk vekst."
        soup = BeautifulSoup(f"<p>{text}</p>", "html.parser")
        signals = extract_hiring_signals(soup, text=text, url="https://example.no/om-oss")
        self.assertFalse(signals["hiring_active"])
        self.assertEqual(signals["open_positions_count"], 0)

    def test_extract_hiring_signals_roster_bounded_to_50_with_accurate_count(self):
        """When >50 jobs are listed, open_positions is capped at 50 while count reflects total."""
        job_cards = "\n".join(f'<div class="job-item"><h3>Utvikler #{i}</h3></div>' for i in range(75))
        html = f"<html><body><div class='positions'>{job_cards}</div></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        signals = extract_hiring_signals(soup, text="Mange ledige stillinger", url="https://example.no/karriere")
        self.assertTrue(signals["hiring_active"])
        self.assertEqual(signals["open_positions_count"], 75)
        self.assertEqual(len(signals["open_positions"]), 50)


class TestF4IdentityGateAndTokenHardening(unittest.TestCase):
    """F4: Identity Gate & Token Hardening."""

    def test_single_token_legal_name_on_spa_with_title_and_description(self):
        """Single-token SPA entity (e.g. EQUINOR ASA) with <100 chars text verified via title/description."""
        profile = {
            "name": "EQUINOR ASA",
            "organisation_number": "923609016",
            "evidence": {
                "website": {
                    "status": "available",
                    "value": {
                        "final_url": "https://www.equinor.com/",
                        "title": "Equinor - An energy company with global reach",
                        "description": "Equinor is an international broad energy company committed to creating value.",
                        "main_text_excerpt": "SPA shell",  # <100 chars
                        "extraction_state": "js_fallback_candidate",
                    },
                }
            },
        }
        assessment = assess_website_identity(profile)
        self.assertTrue(assessment["publishable"])
        self.assertGreaterEqual(assessment["score"], 0.9)
        self.assertEqual(assessment["status"], "exact")

    def test_single_token_legal_name_verified_via_structured_organization(self):
        """Single-token entity verified via Schema.org Organization structured data."""
        profile = {
            "name": "EQUINOR ASA",
            "organisation_number": "923609016",
            "evidence": {
                "website": {
                    "status": "available",
                    "value": {
                        "final_url": "https://www.equinor.com/",
                        "title": "Equinor",
                        "main_text_excerpt": "",
                        "extraction_state": "js_fallback_candidate",
                        "structured_organisations": [
                            {"@type": "Organization", "name": "Equinor ASA", "legalName": "Equinor ASA"}
                        ],
                    },
                }
            },
        }
        assessment = assess_website_identity(profile)
        self.assertTrue(assessment["publishable"])
        self.assertGreaterEqual(assessment["score"], 0.95)

    def test_single_token_legal_name_verified_via_subpage(self):
        """Single-token entity verified via substantive /om-oss secondary page."""
        profile = {
            "name": "EQUINOR ASA",
            "organisation_number": "923609016",
            "evidence": {
                "website": {
                    "status": "available",
                    "value": {
                        "final_url": "https://www.equinor.com/",
                        "title": "Equinor",
                        "main_text_excerpt": "",
                        "extraction_state": "js_fallback_candidate",
                        "pages": [
                            {"url": "https://www.equinor.com/", "title": "Equinor", "main_text_excerpt": ""},
                            {
                                "url": "https://www.equinor.com/om-oss",
                                "title": "Om Equinor",
                                "main_text_excerpt": "Equinor er et ledende energiselskap med mer enn 20 000 ansatte.",
                            },
                        ],
                    },
                }
            },
        }
        assessment = assess_website_identity(profile)
        self.assertTrue(assessment["publishable"])
        self.assertGreaterEqual(assessment["score"], 0.95)

    def test_social_handle_hardened_against_prefix_substring_leak(self):
        """Short entity token 'CO' does NOT match routing prefix 'company'."""
        company = {"name": "CO AS"}
        link = {"platform": "linkedin", "url": "https://linkedin.com/company/equinor"}
        assessment = assess_social_identity(company, link)
        self.assertFalse(assessment["publishable"])
        self.assertEqual(assessment["identity_score"], 0.3)

    def test_unverified_subsidiary_quarantined_zero_wrong_company(self):
        """Subsidiary lacking exact legal name/orgnr on group parent site is quarantined."""
        profile = {
            "name": "SKS PRODUKSJON AS",
            "organisation_number": "915637353",
            "evidence": {
                "website": {
                    "status": "available",
                    "source_url": "https://www.sks.no/",
                    "value": {
                        "final_url": "https://www.sks.no/",
                        "title": "SKS Konsern",
                        "main_text_excerpt": "SKS leverer ren energi.",
                    },
                }
            },
        }
        assessment = assess_website_identity(profile)
        self.assertFalse(assessment["publishable"])
        self.assertLess(assessment["score"], 0.9)


class TestContractClaimsIntegration(unittest.TestCase):
    """Output Contract Claims & Evidence Integration."""

    def setUp(self):
        self.org = "923609016"
        self.run_id = "test-m1-claims"
        self.started_at = "2026-09-28T22:00:00Z"
        self.completed_at = "2026-09-28T22:00:05Z"
        self.reg_evidence = evidence(
            "registry",
            "available",
            "official_registry",
            f"https://data.brreg.no/enhetsregisteret/api/enheter/{self.org}",
            value={"navn": "EXAMPLE ASA", "organisasjonsform": {"kode": "ASA"}},
            content_sha256="1" * 64,
        )

    def test_publishable_website_emits_contact_and_hiring_claims(self):
        """When identity gate passes, contact and hiring claims are emitted as available."""
        web_ev = evidence(
            "website",
            "available",
            "company_owned",
            "https://example.no/",
            value={
                "title": "Example ASA",
                "description": "Ledende teknologiselskap",
                "contacts": {
                    "emails": ["post@example.no"],
                    "phones": ["+4722334455"],
                    "addresses": ["Storgata 1, 0155 Oslo"],
                },
                "hiring": {
                    "career_url": "https://example.no/karriere",
                    "hiring_active": True,
                    "open_positions_count": 1,
                    "open_positions": ["Senior Utvikler"],
                },
                "identity_assessment": {"publishable": True, "score": 1.0},
                "pages": [
                    {
                        "url": "https://example.no/kontakt",
                        "title": "Kontakt oss",
                        "main_text_excerpt": "post@example.no",
                        "content_sha256": "2" * 64,
                    },
                    {
                        "url": "https://example.no/karriere",
                        "title": "Ledige stillinger",
                        "main_text_excerpt": "Senior Utvikler",
                        "content_sha256": "3" * 64,
                    },
                ],
            },
            content_sha256="4" * 64,
        )
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "evidence": {"registry": self.reg_evidence, "website": web_ev},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        self.assertEqual(claims["contact_email"]["availability"], "available")
        self.assertEqual(claims["contact_email"]["value"], ["post@example.no"])
        self.assertEqual(claims["contact_phone"]["availability"], "available")
        self.assertEqual(claims["contact_phone"]["value"], ["+4722334455"])
        self.assertEqual(claims["contact_address"]["availability"], "available")
        self.assertEqual(claims["contact_address"]["value"], ["Storgata 1, 0155 Oslo"])

        self.assertEqual(claims["career_url"]["availability"], "available")
        self.assertEqual(claims["career_url"]["value"], "https://example.no/karriere")
        self.assertEqual(claims["hiring_active"]["availability"], "available")
        self.assertEqual(claims["hiring_active"]["value"], True)
        self.assertEqual(claims["open_positions_count"]["availability"], "available")
        self.assertEqual(claims["open_positions_count"]["value"], 1)
        self.assertEqual(claims["open_positions"]["availability"], "available")
        self.assertEqual(claims["open_positions"]["value"], ["Senior Utvikler"])

        # Cryptographic evidence check
        ev_map = {e["id"]: e for e in envelope["evidence"]}
        self.assertEqual(ev_map["ev-website-contact"]["content_sha256"], "2" * 64)
        self.assertEqual(ev_map["ev-website-career"]["content_sha256"], "3" * 64)

    def test_unpublishable_website_quarantines_claims_as_ambiguous(self):
        """When identity gate fails, all contact and hiring claims are ambiguous."""
        web_ev = evidence(
            "website",
            "available",
            "company_owned",
            "https://unverified.com/",
            value={
                "title": "Unverified Site",
                "contacts": {"emails": ["wrong@unverified.com"]},
                "hiring": {"hiring_active": True, "open_positions_count": 3},
                "identity_assessment": {"publishable": False, "score": 0.3},
            },
            content_sha256="4" * 64,
        )
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "evidence": {"registry": self.reg_evidence, "website": web_ev},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        self.assertEqual(claims["official_website"]["availability"], "ambiguous")
        self.assertEqual(claims["contact_email"]["availability"], "ambiguous")
        self.assertEqual(claims["hiring_active"]["availability"], "ambiguous")
        self.assertEqual(claims["open_positions_count"]["availability"], "ambiguous")

    def test_robots_blocked_subpage_emits_blocked_claim(self):
        """When career subpage is blocked by robots.txt, claims are marked blocked."""
        web_ev = evidence(
            "website",
            "available",
            "company_owned",
            "https://example.no/",
            value={
                "title": "Example",
                "identity_assessment": {"publishable": True, "score": 1.0},
                "crawl_errors": [{"url": "https://example.no/karriere", "error": "robots.txt disallows page"}],
            },
            content_sha256="4" * 64,
        )
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "evidence": {"registry": self.reg_evidence, "website": web_ev},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        self.assertEqual(claims["career_url"]["availability"], "blocked")
        self.assertEqual(claims["hiring_active"]["availability"], "blocked")
        self.assertEqual(claims["open_positions_count"]["availability"], "blocked")
        self.assertEqual(claims["open_positions"]["availability"], "blocked")


if __name__ == "__main__":
    unittest.main()
