"""Precision rules added after auditing a 1,000-company run and the cloud review."""
from __future__ import annotations

import unittest

from norway_company_agent import web_discovery as wd
from norway_company_agent.ai_enrich import _supported
from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import utc_now
from norway_company_agent.news import exact_title_match
from norway_company_agent.site_proof import find_identity_proof, name_domain_location_proof
from norway_company_agent.website import normalize_social_url


def _profile(name, org="988456225", street="Liamyrane 13", postcode="5132", kommune="BERGEN", form="AS", **extra):
    base = {
        "organisation_number": org, "name": name, "legal_form": form, "municipality": kommune,
        "evidence": {"registry": {"value": {"forretningsadresse": {"adresse": [street], "postnummer": postcode, "kommune": kommune, "poststed": kommune}}}},
    }
    base.update(extra)
    return base


class AddressProofTests(unittest.TestCase):
    def test_sister_company_at_same_address_is_not_proof(self):
        # Real case: NORAMIX LIAMYRANE 10 AS was matched to noramix.com (Noramix Trade AS).
        text = "Kontakt: kvalitet@noramix.com eller Noramix Trade AS, Att. Kvalitet Liamyrane 13 5132 Nyborg"
        self.assertIsNone(find_identity_proof(text, _profile("NORAMIX LIAMYRANE 10 AS")))

    def test_full_name_beside_address_is_proof(self):
        text = "Felle Maskin AS Sitjevegen 234 3870 Fyresdal 909 33 132"
        proof = find_identity_proof(text, _profile("FELLE MASKIN AS", street="Sitjevegen 234", postcode="3870"))
        self.assertEqual(proof["type"], "registered_address")

    def test_name_variants_ampersand_and_as_slash(self):
        p = _profile("BYGGMESTRENE RIPEL & SVENNE AS", street="Storgata 1", postcode="0155")
        self.assertIsNotNone(find_identity_proof("Byggmestrene Ripel og Svenne, Storgata 1, 0155 Oslo", p))
        p2 = _profile("AS KOBERG", street="Sportsveien 1", postcode="3615")
        self.assertIsNotNone(find_identity_proof("Kontakt A/S Koberg Sportsveien 1 3615 Kongsberg", p2))

    def test_other_org_number_refused_when_name_check_failed(self):
        text = "Kunder: Halvorsen Maskin AS org.nr 815 011 422. Levert av Nord Regnskap AS, org.nr 999 888 111."
        p = _profile("HALVORSEN MASKIN AS", org="815011422")
        self.assertIsNotNone(find_identity_proof(text, p))                      # tolerated on a name-matched site
        self.assertIsNone(find_identity_proof(text, p, max_other_orgs=0))       # refused on any other site


class NameDomainProofTests(unittest.TestCase):
    def test_place_inside_the_name_is_not_independent(self):
        p = _profile("BERGEN BIL AS", kommune="BERGEN", postcode="5003")
        self.assertIsNone(name_domain_location_proof(p, "www.bergenbil.no", "Bergen Bil – bruktbil i Bergen", ["bergen", "bil"]))

    def test_exact_legal_name_with_form_is_accepted(self):
        p = _profile("BERGEN BIL AS", kommune="BERGEN", postcode="5003")
        proof = name_domain_location_proof(p, "bergenbil.no", "© 2026 Bergen Bil AS. Alle rettigheter.", ["bergen", "bil"])
        self.assertEqual(proof["type"], "name_domain_and_exact_legal_name")
        self.assertIsNone(name_domain_location_proof(p, "bergenbil.no", "Bergen Bil Service AS", ["bergen", "bil"]))

    def test_independent_location_is_accepted_and_other_org_refused(self):
        p = _profile("NORDIC 360 AS", kommune="HAMAR", postcode="2322")
        self.assertIsNotNone(name_domain_location_proof(p, "nordic360.no", "Vi holder til i Hamar", ["nordic", "360"]))
        self.assertIsNone(name_domain_location_proof(p, "nordic360.no", "Hamar. Org.nr 911 222 333", ["nordic", "360"]))


class PageScopeTests(unittest.TestCase):
    def test_group_hosted_page_keeps_only_its_own_content(self):
        url = "https://www.los-gruppen.no/selskap/los-elektro-stord"
        profile = {"organisation_number": "912487733", "name": "LOS ELEKTRO STORD AS", "evidence": {"website": {
            "status": "available", "source_url": url, "value": {
                "final_url": url, "identity_assessment": {"publishable": True},
                "news_items": [{"title": "Konsernnytt", "url": "https://www.los-gruppen.no/aktuelt/konsern", "date": "2026-01-01"}],
                "contacts": {"emails": ["post@los-gruppen.no"], "phones": ["55000000"], "addresses": []},
                "emails": ["post@los-gruppen.no"], "career_url": "https://www.los-gruppen.no/karriere", "hiring_active": True,
                "pages": [{"url": url, "contacts": {"emails": ["stord@los-elektro.no"], "phones": [], "addresses": []}},
                          {"url": "https://www.los-gruppen.no/karriere"}],
            }}}}
        self.assertTrue(wd.apply_page_scope(profile))
        value = profile["evidence"]["website"]["value"]
        self.assertEqual(value["news_items"], [])
        self.assertEqual(value["emails"], ["stord@los-elektro.no"])
        self.assertIsNone(value["career_url"])
        self.assertIsNone(value["hiring_active"])

    def test_locale_prefix_is_a_site_root(self):
        self.assertFalse(wd.is_page_scoped("https://www.gjerstad.com/nb/"))
        self.assertFalse(wd.is_page_scoped("https://www.naeroyfjordencamping.no/en/"))
        self.assertTrue(wd.is_page_scoped("https://www.norsenet.no/aksjefond"))


class SmallCompanyNewsTests(unittest.TestCase):
    def test_owner_named_company_needs_full_legal_name(self):
        self.assertFalse(exact_title_match("STIAN KROKEN AS", "Stian Kroken vant NM i langrenn - VG", allow_suffixless=False))
        self.assertTrue(exact_title_match("STIAN KROKEN AS", "Stian Kroken AS kjøper næringsbygg - Romerikes Blad", allow_suffixless=False))
        self.assertTrue(exact_title_match("BRING CARGO AS", "Bring Cargo sikrer seg strategisk posisjon - MTLogistikk", allow_suffixless=True))

    def test_substantial_business_threshold(self):
        self.assertFalse(wd.is_substantial_business({"employees": 2, "evidence": {}}))
        self.assertTrue(wd.is_substantial_business({"employees": 40, "evidence": {}}))


class NumberValidatorTests(unittest.TestCase):
    FACTS = {"A1": {"text": "Annual accounts 2025; revenue NOK 12 345 678; operating margin 18.3%"}}

    def test_substring_of_a_long_amount_is_not_support(self):
        self.assertFalse(_supported("Revenue grew 45% to about NOK 12 million.", ["A1"], self.FACTS))
        self.assertFalse(_supported("Revenue of NOK 345 678.", ["A1"], self.FACTS))

    def test_exact_figures_in_any_grouping_are_supported(self):
        self.assertTrue(_supported("Revenue was NOK 12 345 678 in 2025 at an 18.3% margin.", ["A1"], self.FACTS))
        self.assertTrue(_supported("Revenue was NOK 12,345,678.", ["A1"], self.FACTS))


class SocialNormalisationTests(unittest.TestCase):
    def test_company_handles_containing_generic_words_survive(self):
        self.assertIsNotNone(normalize_social_url("https://linkedin.com/company/bergen-bil-service"))
        self.assertIsNotNone(normalize_social_url("https://facebook.com/noblehotels"))
        self.assertIsNone(normalize_social_url("https://twitter.com/squarespace"))


class SharedDomainTests(unittest.TestCase):
    def test_owner_recognised_from_captured_org_number_without_recorded_proof(self):
        def site(text):
            return {"status": "available", "source_url": "https://group.no/", "value": {
                "final_url": "https://group.no/", "main_text_excerpt": text, "identity_assessment": {"publishable": True}}}
        parent = {"organisation_number": "815011422", "name": "GROUP AS", "evidence": {"website": site("Group AS, org.nr 815 011 422")}}
        child = {"organisation_number": "999888111", "name": "GROUP SERVICE AS", "evidence": {"website": site("Group AS, org.nr 815 011 422")}}
        self.assertEqual(wd.quarantine_shared_domains([parent, child]), ["999888111"])
        self.assertTrue(parent["evidence"]["website"]["value"]["identity_assessment"]["publishable"])


class NewsMergeAndUrlTests(unittest.TestCase):
    def _profile(self):
        return {
            "organisation_number": "958244819", "name": "CEG NORWAY AS", "legal_form": "AS", "website": "WWW.GJERSTAD.COM",
            "evidence": {
                "registry": {"field": "registry", "status": "available", "source_url": "https://data.brreg.no/x", "retrieved_at": utc_now(),
                             "content_sha256": "a" * 64, "value": {"navn": "CEG NORWAY AS"}},
                "website": {"field": "website", "status": "available", "source_url": "https://www.gjerstad.com/nb/", "retrieved_at": utc_now(),
                            "content_sha256": "b" * 64, "value": {
                                "final_url": "https://www.gjerstad.com/nb/", "title": "Gjerstad", "content_sha256": "b" * 64,
                                "identity_assessment": {"publishable": True},
                                "news_items": [{"title": "Gammel sak fra bloggen", "url": "https://www.gjerstad.com/nb/aktuelt/gammel/", "date": "2019-03-01"}]}},
                "news": {"field": "news", "status": "available", "source_url": "https://google.serper.dev/news?q=x", "source_class": "public_news",
                         "retrieved_at": utc_now(), "content_sha256": "c" * 64, "value": {"items": [
                             {"title": "CEG Norway AS kjøper konkurrent", "url": "https://avis.no/a", "date": "2026-10-01", "publisher": "Avis"}]}},
            },
        }

    def test_press_and_site_news_both_published_newest_first(self):
        env = build_output_envelope(self._profile(), run_id="t", started_at=utc_now(), completed_at=utc_now())
        claims = {c["field"]: c for c in env["claims"]}
        evidence = {e["id"]: e for e in env["evidence"]}
        self.assertEqual(claims["news_1_url"]["value"], "https://avis.no/a")
        self.assertEqual(claims["news_2_url"]["value"], "https://www.gjerstad.com/nb/aktuelt/gammel/")
        self.assertEqual(claims["latest_news_date"]["value"], "2026-10-01")
        self.assertEqual(claims["latest_news_title"]["evidence_ids"], ["ev-news"])
        self.assertIn("ev-news", evidence)
        self.assertIn("ev-website-news", evidence)
        self.assertEqual(evidence["ev-news-2"]["source_class"], "company_owned")
        self.assertEqual(evidence["ev-news-1"]["source_class"], "public_news")

    def test_registry_free_text_homepage_is_published_as_a_real_url(self):
        env = build_output_envelope(self._profile(), run_id="t", started_at=utc_now(), completed_at=utc_now())
        site = next(c for c in env["claims"] if c["field"] == "official_website")
        self.assertEqual(site["value"], "https://www.gjerstad.com/nb/")


if __name__ == "__main__":
    unittest.main()


class VerificationRoundTests(unittest.TestCase):
    def test_cms_demo_posts_and_their_same_day_siblings_are_dropped(self):
        from norway_company_agent.website import drop_placeholder_cohorts, _is_junk_news
        items = [
            {"title": "Dette er et innlegg", "date": "2021-04-30", "url": "https://x.no/dette-er-et-innlegg/"},
            {"title": "Hvordan få flere kunder?", "date": "2021-04-30", "url": "https://x.no/hvordan/"},
            {"title": "Ny avtale med kommunen", "date": "2026-02-01", "url": "https://x.no/nyheter/avtale/"},
        ]
        self.assertEqual([i["title"] for i in drop_placeholder_cohorts(items)], ["Ny avtale med kommunen"])
        self.assertTrue(_is_junk_news("Pressemelding", "https://x.no/aktuelt/pressemelding/", "", "2025-04-30"))
        self.assertTrue(_is_junk_news("Åpen jobbsøknad", "https://x.no/nyheter/apen-jobbsoknad/", "", "2025-04-10"))
        self.assertTrue(_is_junk_news("Unitech delivers training", "https://www.unitech.no/news-archive/", "", "2026-06-23"))

    def test_accounts_evidence_states_the_reporting_period(self):
        profile = {
            "organisation_number": "979598009", "name": "SLAKTERIET BREKKE AS", "legal_form": "AS",
            "evidence": {"financials": {"field": "financials", "status": "available", "retrieved_at": utc_now(),
                                        "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/979598009", "content_sha256": "a" * 64,
                                        "value": {"records": [{"period": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}, "account_type": "SELSKAP", "revenue": 1.0}]}}},
        }
        env = build_output_envelope(profile, run_id="t", started_at=utc_now(), completed_at=utc_now())
        fin = next(e for e in env["evidence"] if e["id"] == "ev-financials")
        self.assertEqual(fin["reporting_period"], {"from": "2025-01-01", "to": "2025-12-31"})
        self.assertIn("2025-12-31", fin["claim_span"])
