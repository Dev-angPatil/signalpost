"""Tests verifying the fixes for Builderr evaluation feedback:
1. Zero wrong-company claims (Løwehjerte / Squarespace, Hovly Borettslag / USBL).
2. Verified company news and job postings linked from the company's own site.
3. Buyer-first executive summary stating the source and leading with material facts.
"""
from __future__ import annotations

import unittest
from bs4 import BeautifulSoup

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import evidence
from norway_company_agent.identity import (
    apply_website_identity_gate,
    assess_social_identity,
    assess_website_identity,
)
from norway_company_agent.observations import build_observations
from norway_company_agent.synthesis import synthesize_executive_profile
from norway_company_agent.website import (
    _extract_job_vacancies,
    _extract_page_news,
    _same_site_article_url,
    _social_links,
    normalize_social_url,
    structured_social_links,
)


class TestZeroWrongCompanyGuarantees(unittest.TestCase):
    """Ensure claims are strictly tied to the company and third-party platforms are eliminated."""

    def test_squarespace_social_handle_rejected(self):
        """Squarespace social handle must be rejected even on a verified company site."""
        lowehjerte = {
            "name": "LØWEHJERTE DESIGN AS",
            "organisation_number": "987832827",
            "legal_form": "AS",
            "municipality": "LUSTER",
        }
        squarespace_x = {"platform": "x", "url": "https://x.com/squarespace"}
        squarespace_fb = {"platform": "facebook", "url": "https://facebook.com/squarespace"}
        squarespace_li = {"platform": "linkedin", "url": "https://linkedin.com/company/squarespace"}

        self.assertFalse(assess_social_identity(lowehjerte, squarespace_x)["publishable"])
        self.assertFalse(assess_social_identity(lowehjerte, squarespace_fb)["publishable"])
        self.assertFalse(assess_social_identity(lowehjerte, squarespace_li)["publishable"])

        # Also rejected at normalize_social_url level
        self.assertIsNone(normalize_social_url("http://twitter.com/squarespace"))
        self.assertIsNone(normalize_social_url("https://facebook.com/squarespace"))
        self.assertIsNone(normalize_social_url("https://linkedin.com/company/squarespace"))

    def test_usbl_manager_social_handle_rejected_for_borettslag(self):
        """Housing manager USBL accounts must be rejected for specific housing cooperatives."""
        hovly = {
            "name": "HOVLY BORETTSLAG",
            "organisation_number": "853954292",
            "legal_form": "BRL",
            "municipality": "SANDEFJORD",
        }
        usbl_ig = {"platform": "instagram", "url": "https://instagram.com/boligbyggelaget_usbl"}
        usbl_li = {"platform": "linkedin", "url": "https://linkedin.com/company/usbl"}

        self.assertFalse(assess_social_identity(hovly, usbl_ig)["publishable"])
        self.assertFalse(assess_social_identity(hovly, usbl_li)["publishable"])

        # Also rejected at normalize_social_url level
        self.assertIsNone(normalize_social_url("https://instagram.com/boligbyggelaget_usbl"))
        self.assertIsNone(normalize_social_url("https://linkedin.com/company/usbl"))

    def test_unpublishable_website_quarantines_claims_without_publishing_values(self):
        """When a website fails identity gate (e.g. USBL site for Hovly Borettslag),

        contract claims MUST have value: None rather than publishing the manager's site/info.
        """
        hovly_reg = evidence(
            "registry",
            "available",
            "official_registry_live",
            "https://data.brreg.no/enhetsregisteret/api/enheter/853954292",
            value={
                "navn": "HOVLY BORETTSLAG",
                "organisasjonsform": {"kode": "BRL"},
                "forretningsadresse": {"kommune": "SANDEFJORD"},
                "epostadresse": "styret@hovly.no",
            },
        )
        usbl_web = evidence(
            "website",
            "available",
            "company_owned",
            "https://www.usbl.no/",
            value={
                "title": "Boligbyggelaget Usbl",
                "description": "Usbl er et av Norges største boligbyggelag",
                "contacts": {"emails": ["post@usbl.no"], "phones": ["+4722983800"]},
                "social_links": [{"platform": "instagram", "url": "https://instagram.com/boligbyggelaget_usbl"}],
                "identity_assessment": {"publishable": False, "score": 0.3, "reasons": ["partial overlap"]},
            },
            content_sha256="c" * 64,
        )
        profile = {
            "name": "HOVLY BORETTSLAG",
            "organisation_number": "853954292",
            "legal_form": "BRL",
            "evidence": {"registry": hovly_reg, "website": usbl_web},
        }
        envelope = build_output_envelope(
            profile,
            run_id="run-hovly",
            started_at="2026-11-14T00:00:00Z",
            completed_at="2026-11-14T00:00:01Z",
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        # Official website value is None (quarantined)
        self.assertIsNone(claims["official_website"]["value"])
        self.assertEqual(claims["official_website"]["availability"], "ambiguous")

        # Website description is None
        self.assertIsNone(claims["website_description"]["value"])

        # No instagram claim about USBL was published!
        self.assertNotIn("social_instagram", claims)


class TestCompanyNewsAndJobPostings(unittest.TestCase):
    """Ensure verified company news and job postings linked from the company's own site are published."""

    def test_company_owned_website_news_published_as_claims(self):
        """Articles extracted from verified company site must be emitted as news claims with company_owned evidence."""
        web_evidence = evidence(
            "website",
            "available",
            "company_owned",
            "https://example.no/",
            value={
                "identity_assessment": {"publishable": True, "score": 1.0},
                "news_items": [
                    {
                        "title": "Quarterly Expansion in Bergen",
                        "date": "2026-09-15",
                        "url": "https://example.no/aktuelt/expansion-bergen",
                    },
                    {
                        "title": "New Green Energy Partnership",
                        "date": "2026-08-01",
                        "url": "https://example.no/aktuelt/green-partnership",
                    },
                ],
            },
            content_sha256="f" * 64,
        )
        profile = {
            "name": "NORDIC ENERGY AS",
            "organisation_number": "912345678",
            "legal_form": "AS",
            "evidence": {"website": web_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id="run-news",
            started_at="2026-11-14T00:00:00Z",
            completed_at="2026-11-14T00:00:01Z",
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        self.assertEqual(claims["latest_news_title"]["value"], "Quarterly Expansion in Bergen")
        self.assertEqual(claims["latest_news_date"]["value"], "2026-09-15")
        self.assertEqual(claims["latest_news_url"]["value"], "https://example.no/aktuelt/expansion-bergen")
        self.assertEqual(claims["latest_news_title"]["availability"], "available")

        ev_by_id = {e["id"]: e for e in envelope["evidence"]}
        news_ev = ev_by_id[claims["latest_news_title"]["evidence_ids"][0]]
        self.assertEqual(news_ev["source_class"], "company_owned")

    def test_job_posting_title_and_url_published(self):
        """Open job vacancies on company site must emit job_posting_title and job_posting_url claims."""
        web_evidence = evidence(
            "website",
            "available",
            "company_owned",
            "https://example.no/",
            value={
                "identity_assessment": {"publishable": True, "score": 1.0},
                "hiring": {
                    "hiring_active": True,
                    "open_positions_count": 2,
                    "open_positions": ["Senior Data Engineer", "UX Designer"],
                    "open_postings": [
                        {"title": "Senior Data Engineer", "url": "https://example.no/karriere/data-engineer"},
                        {"title": "UX Designer", "url": "https://example.no/karriere/ux-designer"},
                    ],
                    "career_url": "https://example.no/karriere",
                },
            },
            content_sha256="f" * 64,
        )
        profile = {
            "name": "NORDIC SOFTWARE AS",
            "organisation_number": "923456789",
            "legal_form": "AS",
            "evidence": {"website": web_evidence},
        }
        envelope = build_output_envelope(
            profile,
            run_id="run-jobs",
            started_at="2026-11-14T00:00:00Z",
            completed_at="2026-11-14T00:00:01Z",
        )
        claims = {c["field"]: c for c in envelope["claims"]}

        self.assertEqual(claims["job_posting_title"]["value"], "Senior Data Engineer")
        self.assertEqual(claims["job_posting_url"]["value"], "https://example.no/karriere/data-engineer")
        self.assertEqual(claims["job_posting_title"]["availability"], "available")
        self.assertEqual(claims["hiring_active"]["value"], True)

    def test_asset_extensions_rejected_from_news_article_urls(self):
        """Images and document assets must never be extracted as news article URLs."""
        self.assertIsNone(_same_site_article_url("https://example.no", "/banner.webp"))
        self.assertIsNone(_same_site_article_url("https://example.no", "/image.jpg"))
        self.assertIsNone(_same_site_article_url("https://example.no", "/doc.pdf"))
        self.assertIsNone(_same_site_article_url("https://example.no", "/wp-content/uploads/2024/05/logo.png"))
        self.assertEqual(
            _same_site_article_url("https://example.no", "/nyheter/2026/09/expansion"),
            "https://example.no/nyheter/2026/09/expansion",
        )


class TestBuyerFirstExecutiveSummary(unittest.TestCase):
    """Ensure summary starts with one concrete fact a buyer would want and states its source."""

    def test_buyer_first_verified_website_lead(self):
        profile = {
            "name": "LØWEHJERTE DESIGN AS",
            "organisation_number": "987832827",
            "legal_form": "AS",
            "municipality": "LUSTER",
            "website": "www.lowehjerte.no",
            "evidence": {
                "website": {
                    "status": "available",
                    "source_url": "https://www.lowehjerte.no/",
                    "value": {
                        "description": "Grafisk design og visuell kommunikasjon",
                        "identity_assessment": {"publishable": True},
                    },
                }
            },
        }
        brief = synthesize_executive_profile(profile)
        summary = brief["summary"]

        self.assertTrue(summary.startswith("According to the company's verified website (lowehjerte.no)"))
        self.assertIn("LØWEHJERTE DESIGN AS operates as Grafisk design og visuell kommunikasjon", summary)
        self.assertNotIn("Executive brief for LØWEHJERTE DESIGN AS (987832827", summary)

    def test_buyer_first_statutory_accounts_lead(self):
        profile = {
            "name": "NORDIC TRADING AS",
            "organisation_number": "911222333",
            "legal_form": "AS",
            "municipality": "BERGEN",
            "evidence": {
                "financials": {
                    "value": {
                        "records": [
                            {"year": 2024, "revenue": 50000000.0, "operating_result": 7500000.0, "assets": 30000000.0, "equity": 18000000.0, "currency": "NOK"}
                        ]
                    }
                }
            },
        }
        brief = synthesize_executive_profile(profile)
        summary = brief["summary"]

        self.assertTrue(summary.startswith("According to statutory annual accounts filed with Regnskapsregisteret for 2024"))
        self.assertIn("50 000 000 NOK in revenue with an operating margin of 15.0%", summary)
        self.assertIn("equity ratio of 60.0%", summary)
        self.assertNotIn("Executive brief for NORDIC TRADING AS", summary)

    def test_buyer_first_bankruptcy_lead(self):
        profile = {
            "name": "KONKURSRAMMET AS",
            "organisation_number": "988776655",
            "legal_form": "AS",
            "adverse": True,
            "announcements": {
                "events": [{"category": "insolvency", "title": "Konkursåpning", "date": "2025-08-20"}],
            },
        }
        brief = synthesize_executive_profile(profile)
        summary = brief["summary"]

        self.assertTrue(summary.startswith("COMPANY IN BANKRUPTCY: According to official statutory notices from Brønnøysundregistrene (Kunngjøringer)"))
        self.assertIn("KONKURSRAMMET AS (988776655) is subject to active insolvency/bankruptcy proceedings", summary)


if __name__ == "__main__":
    unittest.main()
