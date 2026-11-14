"""Regression tests for the November rescore fixes (precision, jobs, accounts order)."""
from __future__ import annotations

import unittest

from norway_company_agent.identity import assess_social_identity
from norway_company_agent.nav_jobs import parse_ad_page
from norway_company_agent.official import normalize_financials
from norway_company_agent.site_proof import find_identity_proof
from norway_company_agent.website import _is_junk_news


def _profile(name: str, org: str = "968841084") -> dict:
    return {
        "name": name,
        "organisation_number": org,
        "evidence": {
            "registry": {"value": {"forretningsadresse": {"adresse": ["Eikeneset 40"], "postnummer": "5259"}}},
        },
    }


class SiteProofTests(unittest.TestCase):
    def test_org_number_with_spaces_is_proof(self):
        proof = find_identity_proof("Akita AS · Org.nr. 968 841 084 MVA", _profile("AKITA AS"))
        self.assertEqual(proof["type"], "organisation_number")

    def test_registered_address_with_postcode_is_proof(self):
        proof = find_identity_proof("Akita AS – besøk oss: Eikeneset 40, 5259 Hjellestad", _profile("AKITA AS"))
        self.assertEqual(proof["type"], "registered_address")

    def test_partial_name_beside_group_address_is_not_proof(self):
        prof = _profile("AKITA HOLDING NORD AS")
        self.assertIsNone(find_identity_proof("Akita – Eikeneset 40, 5259 Hjellestad", prof))
        self.assertIsNotNone(find_identity_proof("Akita Holding Nord AS, Eikeneset 40, 5259 Hjellestad", prof))

    def test_shared_address_without_own_name_is_not_proof(self):
        text = "Enata Eiendom AS c/o Saja Eiendom AS Eikeneset 40, 5259 Hjellestad"
        self.assertIsNone(find_identity_proof(text, _profile("AKITA AS")))

    def test_name_alone_is_not_proof(self):
        self.assertIsNone(find_identity_proof("Norsk Akitaklubb – alt om akita", _profile("AKITA AS")))

    def test_other_org_number_is_not_proof(self):
        self.assertIsNone(find_identity_proof("Org.nr 968841085", _profile("AKITA AS")))

    def test_directory_page_listing_many_companies_is_not_proof(self):
        text = "Våre selskaper: Akita AS org.nr 968 841 084, Beta AS org.nr 911 111 111, Gamma AS org nr 922 222 222"
        self.assertIsNone(find_identity_proof(text, _profile("AKITA AS")))

    def test_subunit_number_is_proof(self):
        proof = find_identity_proof("orgnr 938348626", _profile("AKITA AS"), {"938348626"})
        self.assertEqual(proof["type"], "subunit_organisation_number")


class SocialHandleTests(unittest.TestCase):
    def check(self, name, url):
        return assess_social_identity(_profile(name), {"platform": "facebook", "url": url})["publishable"]

    def test_name_inside_handle_rejected(self):
        self.assertFalse(self.check("AKITA AS", "https://facebook.com/norskakitaklubb"))

    def test_name_prefix_or_suffix_accepted(self):
        self.assertTrue(self.check("AUTOBUDGET AS", "https://facebook.com/autobudgetbilforhandler"))
        self.assertTrue(self.check("NORMORIA AS", "https://facebook.com/kulturhusetNormoria"))

    def test_template_and_manager_handles_rejected(self):
        self.assertFalse(self.check("LØWEHJERTE DESIGN AS", "http://twitter.com/squarespace"))
        self.assertFalse(self.check("HOVLY BORETTSLAG", "https://instagram.com/boligbyggelaget_usbl"))


class NewsHygieneTests(unittest.TestCase):
    def test_contact_author_and_menu_items_are_junk(self):
        self.assertTrue(_is_junk_news("Nå blir det krav", "https://fisk.no/kontakt-oss/3-redaksjonen", "", "2026-10-14"))
        self.assertTrue(_is_junk_news("Bygde nytt liv", "https://x.no/author/27168609bd294ff/", "", "2026-04-16"))
        self.assertTrue(_is_junk_news("Om oss", "https://x.no/nyheter/om", "", "2026-04-16"))
        self.assertTrue(_is_junk_news("Fremtidig sak", "https://x.no/nyheter/a", "", "2999-01-01"))
        self.assertTrue(_is_junk_news("Elementor #6565", "https://lysebu.no/elementor-6565/", "", "2025-01-01"))
        self.assertTrue(_is_junk_news("Hei, verden!", "https://skognfysio-mt.no/hei-verden/", "", "2017-07-18"))

    def test_real_article_kept(self):
        self.assertFalse(_is_junk_news("Slakteriet satsar på menneska", "https://slakteriet-as.no/slakteriet-satsar-pa-menneska/", "", "2025-09-26"))


class FinancialOrderTests(unittest.TestCase):
    def test_latest_filing_first(self):
        body = [
            {"id": i, "regnskapstype": "SELSKAP", "regnskapsperiode": {"tilDato": f"{y}-12-31"}}
            for i, y in enumerate((2023, 2024, 2025))
        ]
        records = normalize_financials(body)["records"]
        self.assertEqual([r["period"]["tilDato"][:4] for r in records], ["2025", "2024", "2023"])

    def test_company_accounts_preferred_over_group(self):
        body = [
            {"id": 1, "regnskapstype": "KONSERN", "regnskapsperiode": {"tilDato": "2025-12-31"}},
            {"id": 2, "regnskapstype": "SELSKAP", "regnskapsperiode": {"tilDato": "2025-12-31"}},
        ]
        records = normalize_financials(body)["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["account_type"], "SELSKAP")


class NavParseTests(unittest.TestCase):
    def test_parse_employer_orgnr_from_flight_payload(self):
        html = (
            'xx{\\"adData\\":{\\"id\\":\\"u1\\",\\"status\\":\\"ACTIVE\\",\\"title\\":\\"Lagermedarbeider\\",'
            '\\"published\\":\\"2026-11-04T09:00:00Z\\",\\"expires\\":\\"2026-11-20T23:00:00Z\\",'
            '\\"application\\":{\\"applicationUrl\\":\\"https://ex.no/apply\\"}},'
            '\\"employer\\":{\\"orgnr\\":\\"974661012\\",\\"name\\":\\"Ex AS\\",\\"homepage\\":\\"https://ex.no\\",\\"linkedinPage\\":null}}'
        )
        parsed = parse_ad_page(html)
        self.assertEqual(parsed["employer"]["orgnr"], "974661012")
        self.assertEqual(parsed["employer"]["homepage"], "https://ex.no")
        self.assertNotIn("linkedinPage", parsed["employer"])
        self.assertEqual(parsed["ad"]["title"], "Lagermedarbeider")
        self.assertEqual(parsed["ad"]["published"][:10], "2026-11-04")

    def test_missing_employer_returns_none(self):
        self.assertIsNone(parse_ad_page("<html>no ad</html>"))


if __name__ == "__main__":
    unittest.main()
