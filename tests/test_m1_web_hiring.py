"""Unit & Integration Test Suite for Milestone 1: Web Presence & Hiring Intelligence.

Tests cover:
- Contract claims integration (available, not_available, ambiguous, blocked)
- SHA-256 evidence record linking and verbatim spans
- Subpage routing and robots.txt blocked subpages
- SPAs / JS shell fallback handling and single-token identity hardening
- Social handle token hardening against substring leakage
- Contact info extraction and normalization (emails, E.164 phones, addresses)
- Career portal links and job vacancy extraction
- Zero wrong-company quarantine guarantees
"""
from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import urllib.robotparser
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import evidence
from norway_company_agent.identity import assess_social_identity, assess_website_identity
from norway_company_agent.website import (
    _extract_career_portals,
    _extract_job_vacancies,
    _priority_links,
    _robots_allowed,
    clean_email,
    extract_contact_info,
    normalize_norwegian_phone,
)


class M1ContractClaimsTests(unittest.TestCase):
    def setUp(self):
        self.org = "923609016"
        self.run_id = "test-m1-run"
        self.started_at = "2026-09-28T21:00:00Z"
        self.completed_at = "2026-09-28T21:00:05Z"
        self.reg_evidence = evidence(
            "registry",
            "available",
            "official_registry",
            f"https://data.brreg.no/enhetsregisteret/api/enheter/{self.org}",
            value={"navn": "EXAMPLE ASA", "organisasjonsform": {"kode": "ASA"}},
            content_sha256="a" * 64,
        )

    def test_full_publishable_website_emits_all_available_claims(self):
        """Verified publishable website emits available claims for all 8 M1 fields."""
        web_evidence = evidence(
            "website",
            "available",
            "company_owned",
            "https://example.no/",
            value={
                "title": "Example ASA - Leading Provider",
                "description": "Innovative solutions across Norway",
                "contacts": {
                    "emails": ["post@example.no", "support@example.no"],
                    "phones": ["+4722000000"],
                    "addresses": ["Storgata 1, 0155 Oslo"],
                },
                "hiring": {
                    "career_url": "https://example.no/karriere",
                    "hiring_active": True,
                    "open_positions_count": 2,
                    "open_positions": ["Senior Fullstack Utvikler", "Regnskapsfører"],
                },
                "identity_assessment": {"publishable": True, "score": 1.0},
                "pages": [
                    {
                        "url": "https://example.no/kontakt",
                        "title": "Kontakt oss",
                        "main_text_excerpt": "Kontakt Example ASA på post@example.no eller tlf +4722000000",
                        "content_sha256": "b" * 64,
                    },
                    {
                        "url": "https://example.no/karriere",
                        "title": "Karriere og stillinger",
                        "main_text_excerpt": "Vi søker Senior Fullstack Utvikler og Regnskapsfører",
                        "content_sha256": "c" * 64,
                    },
                ],
            },
            content_sha256="d" * 64,
        )
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "legal_form": "ASA",
            "website": "https://example.no/",
            "evidence": {"registry": self.reg_evidence, "website": web_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        # 1. Verify claims availability and values
        self.assertEqual(claims["official_website"]["availability"], "available")
        self.assertEqual(claims["website_description"]["availability"], "available")
        self.assertEqual(claims["website_description"]["value"], "Innovative solutions across Norway")

        self.assertEqual(claims["contact_email"]["availability"], "available")
        self.assertEqual(claims["contact_email"]["value"], ["post@example.no", "support@example.no"])
        self.assertIn("ev-website-contact", claims["contact_email"]["evidence_ids"])

        self.assertEqual(claims["contact_phone"]["availability"], "available")
        self.assertEqual(claims["contact_phone"]["value"], ["+4722000000"])

        self.assertEqual(claims["contact_address"]["availability"], "available")
        self.assertEqual(claims["contact_address"]["value"], ["Storgata 1, 0155 Oslo"])

        self.assertEqual(claims["career_url"]["availability"], "available")
        self.assertEqual(claims["career_url"]["value"], "https://example.no/karriere")
        self.assertIn("ev-website-career", claims["career_url"]["evidence_ids"])

        self.assertEqual(claims["hiring_active"]["availability"], "available")
        self.assertEqual(claims["hiring_active"]["value"], True)

        self.assertEqual(claims["open_positions_count"]["availability"], "available")
        self.assertEqual(claims["open_positions_count"]["value"], 2)

        self.assertEqual(claims["open_positions"]["availability"], "available")
        self.assertEqual(claims["open_positions"]["value"], ["Senior Fullstack Utvikler", "Regnskapsfører"])

        # 2. Verify subpage cryptographic evidence
        ev_map = {e["id"]: e for e in envelope["evidence"]}
        self.assertIn("ev-website", ev_map)
        self.assertIn("ev-website-contact", ev_map)
        self.assertIn("ev-website-career", ev_map)
        self.assertEqual(ev_map["ev-website-contact"]["content_sha256"], "b" * 64)
        self.assertEqual(ev_map["ev-website-career"]["content_sha256"], "c" * 64)
        self.assertEqual(ev_map["ev-website-contact"]["source_class"], "company_owned")

    def test_explicit_zero_positions_on_checked_career_page(self):
        """Career page checked with 0 positions emits available with value 0, not not_available."""
        web_evidence = evidence(
            "website",
            "available",
            "company_owned",
            "https://example.no/",
            value={
                "hiring": {
                    "career_url": "https://example.no/karriere",
                    "hiring_active": False,
                    "open_positions_count": 0,
                    "open_positions": [],
                },
                "identity_assessment": {"publishable": True, "score": 1.0},
                "pages": [
                    {
                        "url": "https://example.no/karriere",
                        "title": "Karriere",
                        "main_text_excerpt": "Vi har for tiden ingen ledige stillinger.",
                        "content_sha256": "e" * 64,
                    }
                ],
            },
            content_sha256="d" * 64,
        )
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "evidence": {"registry": self.reg_evidence, "website": web_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        self.assertEqual(claims["hiring_active"]["availability"], "available")
        self.assertEqual(claims["hiring_active"]["value"], False)
        self.assertEqual(claims["open_positions_count"]["availability"], "available")
        self.assertEqual(claims["open_positions_count"]["value"], 0)
        self.assertEqual(claims["open_positions"]["availability"], "available")
        self.assertEqual(claims["open_positions"]["value"], [])

    def test_empty_site_unchecked_hiring_and_contacts(self):
        """Site without contact info or career section emits not_available with None."""
        web_evidence = evidence(
            "website",
            "available",
            "company_owned",
            "https://example.no/",
            value={"title": "Example ASA", "identity_assessment": {"publishable": True, "score": 1.0}},
            content_sha256="d" * 64,
        )
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "evidence": {"registry": self.reg_evidence, "website": web_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        for field in (
            "website_description",
            "contact_email",
            "contact_phone",
            "contact_address",
            "career_url",
            "hiring_active",
            "open_positions_count",
            "open_positions",
        ):
            self.assertEqual(claims[field]["availability"], "not_available")
            self.assertIsNone(claims[field]["value"])
            self.assertEqual(claims[field]["confidence"], 1.0)

    def test_unpublishable_website_quarantines_all_external_claims(self):
        """Identity gate failure must quarantine all extracted claims as ambiguous (Zero Wrong-Company)."""
        web_evidence = evidence(
            "website",
            "available",
            "company_owned",
            "https://parent.test/",
            value={
                "title": "Parent Corp",
                "description": "Parent corporation services",
                "contacts": {"emails": ["kontakt@parent.test"], "phones": ["+4722110000"]},
                "hiring": {"hiring_active": True, "open_positions_count": 5},
                "identity_assessment": {"publishable": False, "score": 0.35, "reasons": ["partial legal-name overlap only"]},
            },
            content_sha256="d" * 64,
        )
        profile = {
            "organisation_number": self.org,
            "name": "SUBSIDIARY AS",
            "evidence": {"registry": self.reg_evidence, "website": web_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        self.assertEqual(claims["official_website"]["availability"], "ambiguous")
        self.assertEqual(claims["website_description"]["availability"], "ambiguous")
        self.assertEqual(claims["contact_email"]["availability"], "ambiguous")
        self.assertEqual(claims["contact_phone"]["availability"], "ambiguous")
        self.assertEqual(claims["hiring_active"]["availability"], "ambiguous")
        self.assertEqual(claims["open_positions_count"]["availability"], "ambiguous")

        # Zero claims from this unverified source may have available status!
        for c in envelope["claims"]:
            if (
                "website" in c["field"]
                or "contact" in c["field"]
                or "hiring" in c["field"]
                or "positions" in c["field"]
                or "career" in c["field"]
            ):
                self.assertNotEqual(c["availability"], "available")

    def test_subpage_robots_blocked_marks_career_claims_blocked(self):
        """When /karriere is blocked by robots.txt, career claims are marked blocked."""
        web_evidence = evidence(
            "website",
            "available",
            "company_owned",
            "https://example.no/",
            value={
                "title": "Example ASA",
                "contacts": {"emails": ["post@example.no"]},
                "identity_assessment": {"publishable": True, "score": 1.0},
                "crawl_errors": [{"url": "https://example.no/karriere", "error": "robots.txt disallows page"}],
            },
            content_sha256="d" * 64,
        )
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "evidence": {"registry": self.reg_evidence, "website": web_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        self.assertEqual(claims["contact_email"]["availability"], "available")
        self.assertEqual(claims["career_url"]["availability"], "blocked")
        self.assertEqual(claims["hiring_active"]["availability"], "blocked")
        self.assertEqual(claims["open_positions_count"]["availability"], "blocked")
        self.assertEqual(claims["open_positions"]["availability"], "blocked")

    def test_blocked_entire_website_emits_blocked_claims(self):
        """When homepage is blocked, all website claims are emitted as blocked."""
        web_evidence = evidence(
            "website",
            "blocked",
            "company_owned",
            "https://blocked.no/",
            note="robots.txt disallows this user agent",
        )
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "website": "https://blocked.no/",
            "evidence": {"registry": self.reg_evidence, "website": web_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        self.assertEqual(claims["official_website"]["availability"], "blocked")
        for f in (
            "website_description",
            "contact_email",
            "contact_phone",
            "contact_address",
            "career_url",
            "hiring_active",
            "open_positions_count",
            "open_positions",
        ):
            self.assertEqual(claims[f]["availability"], "blocked")
            self.assertIsNone(claims[f]["value"])

    def test_missing_website_emits_all_claims_not_available(self):
        """Company with no registered website emits not_available for all 8 web claims."""
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "evidence": {"registry": self.reg_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        for f in (
            "official_website",
            "website_description",
            "contact_email",
            "contact_phone",
            "contact_address",
            "career_url",
            "hiring_active",
            "open_positions_count",
            "open_positions",
        ):
            self.assertEqual(claims[f]["availability"], "not_available")
            self.assertIsNone(claims[f]["value"])

    def test_terminal_envelope_schema_conformance(self):
        """Verify strict conformance with OUTPUT_CONTRACT.md schema."""
        web_evidence = evidence(
            "website",
            "available",
            "company_owned",
            "https://example.no/",
            value={"title": "Example", "identity_assessment": {"publishable": True, "score": 1.0}},
            content_sha256="d" * 64,
        )
        profile = {
            "organisation_number": self.org,
            "name": "EXAMPLE ASA",
            "evidence": {"registry": self.reg_evidence, "website": web_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id=self.run_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )

        self.assertEqual(envelope["organisation_number"], self.org)
        self.assertIn("run", envelope)
        self.assertIn("claims", envelope)
        self.assertIn("evidence", envelope)
        self.assertIn("operations", envelope)
        self.assertEqual(envelope["run"]["terminal_status"], "completed")

        for claim in envelope["claims"]:
            self.assertIn("field", claim)
            self.assertIn("value", claim)
            self.assertIn(
                claim["availability"],
                {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"},
            )
            self.assertIsInstance(claim["confidence"], (int, float))
            self.assertIsInstance(claim["evidence_ids"], list)

        for ev in envelope["evidence"]:
            self.assertIn("id", ev)
            self.assertIn("source_url", ev)
            self.assertIn("source_class", ev)
            self.assertIn("content_sha256", ev)
            self.assertIn("claim_span", ev)
            self.assertEqual(len(ev["content_sha256"]), 64)


class M1IdentityHardeningTests(unittest.TestCase):
    def test_spa_single_token_legal_name_with_meta_description_is_publishable(self):
        """Single-token SPA entity (e.g. Equinor ASA) with <100 chars text verified via meta description."""
        profile = {
            "organisation_number": "923609016",
            "name": "EQUINOR ASA",
            "evidence": {
                "website": {
                    "status": "available",
                    "value": {
                        "final_url": "https://www.equinor.com/",
                        "title": "Equinor - Energy company",
                        "description": "Equinor is an international broad energy company committed to creating value.",
                        "main_text_excerpt": "Minimal SPA JS shell",  # <100 chars
                        "extraction_state": "js_fallback_candidate",
                    },
                }
            },
        }
        assessment = assess_website_identity(profile)
        self.assertTrue(assessment["publishable"])
        self.assertGreaterEqual(assessment["score"], 0.95)

    def test_spa_single_token_legal_name_with_subpage_is_publishable(self):
        """Single-token entity with empty homepage text verified via substantive /om-oss subpage."""
        profile = {
            "organisation_number": "923609016",
            "name": "EQUINOR ASA",
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
                                "main_text_excerpt": "Equinor er et bredt energiselskap med mer enn 20 000 dedikerte medarbeidere over hele verden.",
                            },
                        ],
                    },
                }
            },
        }
        assessment = assess_website_identity(profile)
        self.assertTrue(assessment["publishable"])
        self.assertGreaterEqual(assessment["score"], 0.95)

    def test_spa_single_token_with_structured_org_is_publishable(self):
        """Single-token entity verified via Schema.org Organization."""
        profile = {
            "organisation_number": "923609016",
            "name": "EQUINOR ASA",
            "evidence": {
                "website": {
                    "status": "available",
                    "value": {
                        "final_url": "https://www.equinor.com/",
                        "title": "Equinor",
                        "main_text_excerpt": "",
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

    def test_spa_single_token_with_spa_title_is_publishable(self):
        """Single-token entity on SPA candidate page verified via descriptive title."""
        profile = {
            "organisation_number": "923609016",
            "name": "EQUINOR ASA",
            "evidence": {
                "website": {
                    "status": "available",
                    "value": {
                        "final_url": "https://www.equinor.com/",
                        "title": "Equinor ASA Official Portal",
                        "main_text_excerpt": "",
                        "extraction_state": "js_fallback_candidate",
                    },
                }
            },
        }
        assessment = assess_website_identity(profile)
        self.assertTrue(assessment["publishable"])
        self.assertGreaterEqual(assessment["score"], 0.95)

    def test_social_handle_token_hardening_prevents_substring_leak(self):
        """Short 2-letter tokens (e.g. 'co' or 'pa') must NOT match substrings of platform routing prefixes."""
        company = {"name": "CO AS"}
        # 'co' is inside 'company', but URL handle is for 'equinor'
        link = {"platform": "linkedin", "url": "https://linkedin.com/company/equinor"}
        assessment = assess_social_identity(company, link)
        self.assertFalse(assessment["publishable"])
        self.assertEqual(assessment["identity_score"], 0.3)

    def test_social_handle_exact_token_match_for_single_token_entity(self):
        """Single-token entity correctly verifies against exact social handle."""
        equinor = {"name": "Equinor ASA"}
        link = {"platform": "linkedin", "url": "https://linkedin.com/company/equinor"}
        assessment = assess_social_identity(equinor, link)
        self.assertTrue(assessment["publishable"])
        self.assertGreaterEqual(assessment["identity_score"], 0.95)


class M1ContactExtractionTests(unittest.TestCase):
    def test_clean_email_filters_assets_and_placeholders(self):
        """Valid emails pass; asset filenames and placeholder domains are rejected."""
        self.assertEqual(clean_email("post@example.no"), "post@example.no")
        self.assertEqual(clean_email("  SUPPORT@DOMAIN.NO.  "), "support@domain.no")
        self.assertEqual(clean_email("mailto:kari.nordmann@bedrift.no?subject=Inquiry"), "kari.nordmann@bedrift.no")

        # Reject image assets disguised as emails
        self.assertIsNone(clean_email("logo@2x.png"))
        self.assertIsNone(clean_email("icon@3x.webp"))
        self.assertIsNone(clean_email("header@preview.jpg"))

        # Reject placeholder domains
        self.assertIsNone(clean_email("user@example.com"))
        self.assertIsNone(clean_email("name@domain.com"))
        self.assertIsNone(clean_email("test@sentry.io"))
        self.assertIsNone(clean_email("contact@wixpress.com"))

    def test_normalize_norwegian_phone(self):
        """Valid Norwegian phones normalized to +47XXXXXXXX; org numbers and postal codes rejected."""
        self.assertEqual(normalize_norwegian_phone("22 00 00 00"), "+4722000000")
        self.assertEqual(normalize_norwegian_phone("+47 900 12 345"), "+4790012345")
        self.assertEqual(normalize_norwegian_phone("0047 41234567"), "+4741234567")
        self.assertEqual(normalize_norwegian_phone("tel:+4722000000"), "+4722000000")

        # Reject 9-digit Norwegian organisation numbers
        self.assertIsNone(normalize_norwegian_phone("923 609 016"))
        self.assertIsNone(normalize_norwegian_phone("985628572"))

        # Reject 4-digit postal codes
        self.assertIsNone(normalize_norwegian_phone("0155"))
        self.assertIsNone(normalize_norwegian_phone("5020"))

        # Reject invalid start digits (Norwegian national numbers start with 2-9)
        self.assertIsNone(normalize_norwegian_phone("01234567"))
        self.assertIsNone(normalize_norwegian_phone("11234567"))

    def test_extract_contact_info_comprehensive(self):
        """Extract contacts from anchor tags, microdata, footer, and body text."""
        html = """
        <html>
        <head>
            <title>Test Bedrift AS</title>
        </head>
        <body>
            <a href="mailto:kontakt@bedrift.no">Send e-post</a>
            <a href="tel:+4722334455">Ring oss</a>
            <div itemscope itemtype="https://schema.org/LocalBusiness">
                <span itemprop="email">support@bedrift.no</span>
                <span itemprop="telephone">+47 91234567</span>
                <span itemprop="address">Storgata 10, 0155 Oslo</span>
            </div>
            <footer>
                <p>Besøksadresse: Henrik Ibsens gate 4, 0255 Oslo</p>
                <p>Postadresse: Postboks 1234 Sentrum, 0108 Oslo</p>
                <p>Org.nr: 987654321</p>
                <p>Tlf: 22 11 00 11</p>
            </footer>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        contacts = extract_contact_info(soup, soup.get_text(" ", strip=True), registered_domain="bedrift.no")

        self.assertIn("kontakt@bedrift.no", contacts["emails"])
        self.assertIn("support@bedrift.no", contacts["emails"])
        self.assertIn("+4722334455", contacts["phones"])
        self.assertIn("+4791234567", contacts["phones"])
        self.assertIn("+4722110011", contacts["phones"])

        # Address verification
        addr_text = " ".join(contacts["addresses"])
        self.assertTrue(any("0155 Oslo" in a for a in contacts["addresses"]))
        self.assertTrue(any("Postboks 1234" in a for a in contacts["addresses"]))


class M1HiringExtractionTests(unittest.TestCase):
    def test_career_portal_extraction(self):
        """Detect and extract external ATS career portals declared on company page."""
        html = """
        <html>
        <body>
            <a href="https://webcruiter.no/job-apply/123">Søk via Webcruiter</a>
            <a href="https://finn.no/jobb/stilling/987654">Se våre stillinger på Finn</a>
            <a href="https://company.teamtailor.com/">Teamtailor Karriereside</a>
            <a href="https://linkedin.com/jobs/view/555">LinkedIn Jobs</a>
            <a href="https://other.com/news">Annen lenke</a>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        portals = _extract_career_portals("https://example.no/", soup)
        platforms = {p["platform"] for p in portals}
        self.assertIn("webcruiter", platforms)
        self.assertIn("finn", platforms)
        self.assertIn("teamtailor", platforms)
        self.assertIn("linkedin_jobs", platforms)
        self.assertNotIn("other", platforms)

    def test_job_vacancies_and_count_extraction(self):
        """Extract job listings from Schema.org JobPosting and HTML job cards."""
        html = """
        <html>
        <head>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org/",
                "@type": "JobPosting",
                "title": "Senior Cloud Architect",
                "description": "Drive our cloud transition."
            }
            </script>
        </head>
        <body>
            <div class="job-card">
                <h3>Frontend Developer (React/TypeScript)</h3>
            </div>
            <div class="career-position">
                <h4 class="job-title">DevOps Engineer</h4>
            </div>
            <p>Vi har 3 ledige stillinger ved vårt hovedkontor.</p>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        titles, count, hiring = _extract_job_vacancies("https://example.no/karriere", soup, html)
        self.assertTrue(hiring)
        self.assertIn("Senior Cloud Architect", titles)
        self.assertIn("Frontend Developer (React/TypeScript)", titles)
        self.assertIn("DevOps Engineer", titles)
        self.assertGreaterEqual(count, 3)

    def test_explicit_zero_jobs_detected(self):
        """Negative vacancy phrase explicitly detected as 0 positions."""
        html = """
        <html>
        <body>
            <h1>Karriere</h1>
            <p>Vi har for tiden ingen ledige stillinger hos oss.</p>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        titles, count, hiring = _extract_job_vacancies("https://example.no/karriere", soup, html)
        self.assertEqual(count, 0)
        self.assertFalse(hiring)
        self.assertEqual(titles, [])


class M1RoutingAndCachingTests(unittest.TestCase):
    def test_priority_links_category_balance_prevents_clumping(self):
        """Ensure priority subpage selection balances across categories and avoids route clumping."""
        html = """
        <html>
        <body>
            <!-- Multiple about links that would clump in naive ranking -->
            <a href="/om-oss">Om oss</a>
            <a href="/om-oss/historie">Vår historie</a>
            <a href="/om-oss/visjon">Visjon</a>
            <a href="/om-oss/baerekraft">Bærekraft</a>
            <!-- Vital contact and career links -->
            <a href="/kontakt">Kontakt oss</a>
            <a href="/karriere">Ledige stillinger</a>
            <a href="/investor">Investor Relations</a>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        links = _priority_links("https://example.no/", soup, limit=4)

        # Must select exactly 4 links
        self.assertEqual(len(links), 4)

        # Must contain one link from each distinct category
        has_about = any("om-oss" in link for link in links)
        has_contact = any("kontakt" in link for link in links)
        has_career = any("karriere" in link for link in links)
        has_investor = any("investor" in link for link in links)

        self.assertTrue(has_about, "Should include an about page")
        self.assertTrue(has_contact, "Should include a contact page")
        self.assertTrue(has_career, "Should include a career page")
        self.assertTrue(has_investor, "Should include an investor page")

    def test_robots_allowed_host_caching(self):
        """Host-level robots cache avoids redundant network requests for the same host."""
        mock_cache = {}
        fake_parser = MagicMock(spec=urllib.robotparser.RobotFileParser)
        fake_parser.can_fetch.return_value = True
        mock_cache["https://cached-host.no/robots.txt"] = fake_parser

        # Should use cached parser directly without making network call
        with patch("urllib.request.Request") as mock_req:
            allowed = _robots_allowed("https://cached-host.no/karriere", timeout=5.0, cache=mock_cache)
            self.assertTrue(allowed)
            mock_req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
