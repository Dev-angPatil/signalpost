"""Automated unit tests covering all 4 core precision hardening fixes for Builderr re-score submission:
1. False job posting: Require real career page before reporting job titles from website scraper.
2. Generic-word names: Require capitalized headline tokens for ordinary-word company names (e.g., 'Bare Brød').
3. Old job ads as news: Exclude job postings older than 120 days from both news and jobs claims.
4. Lowercase host normalization: Normalize website URLs so schemes and hostnames are lowercase (e.g. WWW.NORMORIA.NO).
"""
from __future__ import annotations

import datetime
from datetime import timezone, timedelta
import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import utc_now
from norway_company_agent.news import exact_title_match
from norway_company_agent.website import (
    fetch_website,
    is_current_site_job,
    is_site_job,
    normalize_homepage,
)


def _base_profile(name="TEST AS", org="999888777", website="https://test.no/", **extra):
    profile = {
        "organisation_number": org,
        "name": name,
        "legal_form": "AS",
        "municipality": "OSLO",
        "website": website,
        "evidence": {
            "registry": {
                "field": "registry",
                "status": "available",
                "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/" + org,
                "retrieved_at": utc_now(),
                "content_sha256": "0" * 64,
                "value": {"navn": name, "organisasjonsnummer": org},
            },
            "website": {
                "field": "website",
                "status": "available",
                "source_url": website,
                "retrieved_at": utc_now(),
                "content_sha256": "1" * 64,
                "value": {
                    "final_url": website,
                    "title": name,
                    "identity_assessment": {"publishable": True, "score": 1.0},
                    "pages": [],
                    "news_items": [],
                    "social_links": [],
                },
            },
        },
    }
    profile.update(extra)
    return profile


class TestPrecisionFix1CareerPageRequired(unittest.TestCase):
    """Fix 1: Website scraper & claim generator must NOT report open job positions without a real career page."""

    @patch("norway_company_agent.website._robots_allowed", return_value=True)
    @patch("norway_company_agent.website.assert_public_url", return_value=None)
    @patch("norway_company_agent.website.SAFE_OPENER.open")
    def test_physiotherapy_clinic_without_career_page_scrapes_zero_jobs(self, mock_open, _mock_public, _mock_robots):
        """A clinic homepage mentioning 'Fysioterapeut' on team/services page without a career page yields 0 jobs."""
        clinic_html = """<!DOCTYPE html>
        <html>
        <head><title>Klinikk Pluss AS</title></head>
        <body>
            <h1>Velkommen til Klinikk Pluss</h1>
            <p>Våre dyktige behandlere:</p>
            <div>Ola Nordmann - Fysioterapeut og manuellterapeut</div>
            <div>Kari Hansen - Fysioterapeut</div>
            <a href="/om-oss">Om oss</a>
            <a href="/kontakt">Kontakt oss</a>
        </body>
        </html>
        """.encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = clinic_html
        mock_resp.geturl.return_value = "https://klinikkpluss.no/"
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.__enter__.return_value = mock_resp
        mock_open.return_value = mock_resp
        fake_opener = MagicMock(); fake_opener.open = mock_open

        with patch("norway_company_agent.website.urllib.request.build_opener", return_value=fake_opener):
            record, metric = fetch_website("https://klinikkpluss.no/", timeout=3.0)

        self.assertEqual(record["status"], "available")
        val = record["value"]
        # Without a visited career page or external portal, scraper must not report vacancies
        self.assertEqual(val.get("job_titles") or [], [])
        self.assertIsNone(val.get("career_url"))
        self.assertIsNone(val.get("hiring_active"))
        self.assertIsNone(val.get("job_count"))

    def test_clinic_without_career_page_claims_published_as_not_available(self):
        """In contract envelope, when no real career page was crawled, open_positions must be not_available."""
        profile = _base_profile(name="KLINIKK PLUSS AS", website="https://klinikkpluss.no/")
        profile["evidence"]["website"]["value"].update({
            "job_titles": [],
            "career_url": None,
            "hiring_active": None,
            "open_postings": [],
        })

        env = build_output_envelope(profile, run_id="test", started_at=utc_now(), completed_at=utc_now())
        claims = {c["field"]: c for c in env["claims"]}

        self.assertEqual(claims["open_positions"]["availability"], "not_available")
        self.assertIsNone(claims["open_positions"]["value"])
        self.assertEqual(claims["open_positions_count"]["availability"], "not_available")
        self.assertIsNone(claims["open_positions_count"]["value"])

        # Must NOT emit spurious available job posting titles or URLs
        available_fields = {c["field"] for c in env["claims"] if c["availability"] == "available"}
        self.assertNotIn("job_posting_title", available_fields)
        self.assertNotIn("job_posting_url", available_fields)

    def test_clinic_with_genuine_career_page_publishes_open_positions(self):
        """When a real career page was visited/verified, genuine open positions must be published."""
        profile = _base_profile(name="KLINIKK PLUSS AS", website="https://klinikkpluss.no/")
        profile["evidence"]["website"]["value"].update({
            "career_url": "https://klinikkpluss.no/karriere",
            "job_titles": ["Fysioterapeut"],
            "hiring_active": True,
            "open_postings": [{"title": "Fysioterapeut", "url": "https://klinikkpluss.no/karriere/fysio-1"}],
            "pages": [{"url": "https://klinikkpluss.no/karriere", "title": "Karriere", "content_sha256": "2" * 64}],
        })

        env = build_output_envelope(profile, run_id="test", started_at=utc_now(), completed_at=utc_now())
        claims = {c["field"]: c for c in env["claims"]}

        self.assertEqual(claims["open_positions"]["availability"], "available")
        self.assertEqual(claims["open_positions"]["value"], ["Fysioterapeut"])
        self.assertEqual(claims["job_posting_title"]["value"], "Fysioterapeut")
        self.assertEqual(claims["job_posting_url"]["value"], "https://klinikkpluss.no/karriere/fysio-1")


class TestPrecisionFix2GenericWordHeadlineMatching(unittest.TestCase):
    """Fix 2: Headline matching for dictionary-word names (e.g. 'Bare Brød') requires capitalized tokens."""

    def test_bare_brod_capitalized_headline_matches(self):
        """Capitalized proper nouns in press headlines must match the company."""
        self.assertTrue(exact_title_match("BARE BRØD AS", "Bare Brød åpner nytt bakeri i sentrum"))
        self.assertTrue(exact_title_match("BARE BRØD AS", "Suksess for Bare Brød etter ett års drift"))
        self.assertTrue(exact_title_match("BARE BRØD AS", "Bare Brød AS leverer rekordresultat"))
        self.assertTrue(exact_title_match("BARE BRØD", "Bare Brød satser i Bergen - E24"))

    def test_bare_brod_lowercase_ordinary_phrases_rejected(self):
        """Headlines with lowercase ordinary phrases must be strictly rejected."""
        # 1. Lowercase in middle of sentence
        self.assertFalse(exact_title_match("BARE BRØD AS", "Nå er det bare brød og vann for studentene"))
        # 2. Sentence initial capitalization where second word is lowercase
        self.assertFalse(exact_title_match("BARE BRØD AS", "Bare brød er ikke nok til frokost"))
        self.assertFalse(exact_title_match("BARE BRØD AS", "Studentene lever på bare brød og nudler"))

    def test_other_generic_word_companies(self):
        """Other common dictionary-word company names must follow the same capitalization requirement."""
        # 'GODT BRØD AS'
        self.assertTrue(exact_title_match("GODT BRØD AS", "Godt Brød utvider til Stavanger"))
        self.assertFalse(exact_title_match("GODT BRØD AS", "Godt brød er viktig til suppen"))
        self.assertFalse(exact_title_match("GODT BRØD AS", "Vi baker godt brød hver morgen"))

        # 'RETT OG SLETT AS' (particle 'og' allowed in lowercase)
        self.assertTrue(exact_title_match("RETT OG SLETT AS", "Rett og Slett åpner ny filial"))
        self.assertFalse(exact_title_match("RETT OG SLETT AS", "Dette er rett og slett for dårlig"))


class TestPrecisionFix3OldJobAdsExcludedFromNews(unittest.TestCase):
    """Fix 3: Job postings older than 120 days must be dropped from BOTH news and jobs claims."""

    def test_is_site_job_identifies_job_postings_regardless_of_age(self):
        now = datetime.datetime.now(timezone.utc).date()
        old_date = (now - timedelta(days=150)).isoformat()
        recent_date = (now - timedelta(days=20)).isoformat()

        old_job = {"title": "Seniorkonsulent søkes", "kind": "job_posting", "date": old_date, "url": "https://test.no/job/1"}
        recent_job = {"title": "Juniorutvikler", "kind": "job_posting", "date": recent_date, "url": "https://test.no/job/2"}
        real_article = {"title": "Selskapet feirer 10 år i dag", "kind": "article", "date": old_date, "url": "https://test.no/news/1"}

        self.assertTrue(is_site_job(old_job))
        self.assertTrue(is_site_job(recent_job))
        self.assertFalse(is_site_job(real_article))

        # Recent is a current job; old is NOT a current job
        self.assertTrue(is_current_site_job(recent_job, max_age_days=120))
        self.assertFalse(is_current_site_job(old_job, max_age_days=120))

    def test_old_job_posting_dropped_from_both_news_and_jobs(self):
        now = datetime.datetime.now(timezone.utc).date()
        old_date = (now - timedelta(days=150)).isoformat()

        profile = _base_profile(name="NORDIC CONSULTING AS", website="https://nordic.no/")
        profile["evidence"]["website"]["value"]["news_items"] = [
            {
                "title": "Vi søker erfaren regnskapsfører",
                "kind": "job_posting",
                "date": old_date,
                "url": "https://nordic.no/karriere/regnskap-gammel",
            }
        ]

        env = build_output_envelope(profile, run_id="test", started_at=utc_now(), completed_at=utc_now())
        claims = {c["field"]: c for c in env["claims"]}

        # 1. Must NOT appear in news
        self.assertEqual(claims["latest_news_title"]["availability"], "not_available")
        self.assertIsNone(claims["latest_news_title"]["value"])
        available_fields = {c["field"] for c in env["claims"] if c["availability"] == "available"}
        self.assertNotIn("news_1_title", available_fields)

        # 2. Must NOT appear in open jobs (older than 120 days)
        self.assertEqual(claims["open_positions"]["availability"], "not_available")
        self.assertNotIn("job_posting_title", available_fields)

    def test_old_job_does_not_mask_older_legitimate_news(self):
        now = datetime.datetime.now(timezone.utc).date()
        job_date = (now - timedelta(days=130)).isoformat()
        news_date = (now - timedelta(days=160)).isoformat()

        profile = _base_profile(name="NORDIC CONSULTING AS", website="https://nordic.no/")
        profile["evidence"]["website"]["value"]["news_items"] = [
            {
                "title": "Gammel stillingsutlysning: Sjåfør søkes",
                "kind": "job_posting",
                "date": job_date,
                "url": "https://nordic.no/jobb/sjafor",
            },
            {
                "title": "Vant prestisjefylt innovasjonspris",
                "kind": "article",
                "date": news_date,
                "url": "https://nordic.no/nyheter/prisvinner",
            },
        ]

        env = build_output_envelope(profile, run_id="test", started_at=utc_now(), completed_at=utc_now())
        claims = {c["field"]: c for c in env["claims"]}

        # Legitimate article must be published as news; job ad must be dropped
        self.assertEqual(claims["latest_news_title"]["availability"], "available")
        self.assertEqual(claims["latest_news_title"]["value"], "Vant prestisjefylt innovasjonspris")
        self.assertEqual(claims["latest_news_date"]["value"], news_date)


class TestPrecisionFix4LowercaseHostNormalization(unittest.TestCase):
    """Fix 4: Website URLs must normalize schemes and hostnames to lowercase."""

    def test_normalize_homepage_lowercases_host_and_scheme(self):
        # Bare uppercase host
        self.assertEqual(normalize_homepage("WWW.NORMORIA.NO"), "https://www.normoria.no/")
        # Scheme and uppercase host
        self.assertEqual(normalize_homepage("https://WWW.NORMORIA.NO/"), "https://www.normoria.no/")
        self.assertEqual(normalize_homepage("HTTP://WWW.NORMORIA.NO"), "http://www.normoria.no/")
        # Mixed-case host with path
        self.assertEqual(
            normalize_homepage("HTTPS://SubDomain.Normoria.NO/Om-Oss/"),
            "https://subdomain.normoria.no/Om-Oss/",
        )

    def test_official_website_claim_published_with_lowercased_hostname(self):
        """In contract envelope, official_website claim must publish with a lowercase hostname."""
        for raw_site in ["WWW.NORMORIA.NO", "https://WWW.NORMORIA.NO/", "HTTPS://WWW.NORMORIA.NO"]:
            with self.subTest(raw_site=raw_site):
                profile = _base_profile(name="NORMORIA AS", website=raw_site)
                profile["evidence"]["website"]["source_url"] = raw_site
                profile["evidence"]["website"]["value"]["final_url"] = raw_site

                env = build_output_envelope(profile, run_id="test", started_at=utc_now(), completed_at=utc_now())
                site_claim = next(c for c in env["claims"] if c["field"] == "official_website")

                self.assertEqual(site_claim["availability"], "available")
                self.assertEqual(site_claim["value"], "https://www.normoria.no/")


if __name__ == "__main__":
    unittest.main()
