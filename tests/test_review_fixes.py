from __future__ import annotations

import io
import unittest
import urllib.error
from unittest import mock

from norway_company_agent import website
from norway_company_agent.synthesis import long_run_base


def _rec(start: str, end: str, revenue: float) -> dict:
    return {"period": {"fraDato": start, "tilDato": end}, "revenue": revenue}


class RobotsMissingFileTest(unittest.TestCase):
    def _http_error(self, code: int):
        return urllib.error.HTTPError("https://x.no/robots.txt", code, "err", {}, io.BytesIO(b""))

    def test_missing_robots_allows_every_later_page(self):
        cache: dict = {}
        with mock.patch.object(website, "assert_public_url"), \
             mock.patch.object(website.SAFE_OPENER, "open", side_effect=self._http_error(404)):
            self.assertTrue(website._robots_allowed("https://x.no/", 5, cache=cache))
            self.assertTrue(website._robots_allowed("https://x.no/kontakt", 5, cache=cache))
            self.assertTrue(website._robots_allowed("https://x.no/nyheter/a", 5, cache=cache))

    def test_forbidden_robots_disallows(self):
        cache: dict = {}
        with mock.patch.object(website, "assert_public_url"), \
             mock.patch.object(website.SAFE_OPENER, "open", side_effect=self._http_error(403)):
            self.assertFalse(website._robots_allowed("https://x.no/", 5, cache=cache))
            self.assertFalse(website._robots_allowed("https://x.no/kontakt", 5, cache=cache))

    def test_unreachable_robots_allows_one_get_only(self):
        cache: dict = {}
        with mock.patch.object(website, "assert_public_url"), \
             mock.patch.object(website.SAFE_OPENER, "open", side_effect=TimeoutError()):
            self.assertTrue(website._robots_allowed("https://x.no/", 5, cache=cache))
            self.assertFalse(website._robots_allowed("https://x.no/kontakt", 5, cache=cache))


class LongRunBaseTest(unittest.TestCase):
    def test_span_uses_calendar_years_with_gap(self):
        recs = [_rec("2025-01-01", "2025-12-31", 10), _rec("2024-01-01", "2024-12-31", 9), _rec("2022-01-01", "2022-12-31", 8)]
        rec, span = long_run_base(recs)
        self.assertEqual(span, 3)
        self.assertEqual(rec["revenue"], 8)

    def test_short_first_period_is_skipped(self):
        recs = [_rec("2026-01-01", "2026-12-31", 10), _rec("2025-01-01", "2025-12-31", 9),
                _rec("2024-01-01", "2024-12-31", 8), _rec("2023-06-03", "2023-12-31", 1)]
        rec, span = long_run_base(recs)
        self.assertEqual((rec["revenue"], span), (8, 2))

    def test_too_short_history(self):
        recs = [_rec("2025-01-01", "2025-12-31", 10), _rec("2024-01-01", "2024-12-31", 9), _rec("2024-06-01", "2024-12-31", 3)]
        self.assertIsNone(long_run_base(recs))


if __name__ == "__main__":
    unittest.main()


class OwnDomainEmailTest(unittest.TestCase):
    def test_only_own_or_registry_domain_kept(self):
        from norway_company_agent.contract import own_domain_emails
        self.assertEqual(own_domain_emails(["info@mysite.com", "post@preppamat.no"], "https://www.preppamat.no/", None), ["post@preppamat.no"])
        self.assertEqual(own_domain_emails(["booking@kirkenestaxi.no", "booking@kirkenestaxi.mo", "klage@finnmarktaxi.as"],
                                           "https://www.kirkenestaxi.no/", None), ["booking@kirkenestaxi.no"])
        self.assertEqual(own_domain_emails(["post@acme.no", "a@sales.acme.no"], "https://acme-shop.no/", "post@acme.no"), ["post@acme.no", "a@sales.acme.no"])
        self.assertEqual(own_domain_emails("kontakt.smb@amedia.no", "https://fanaposten.no/", None), [])


class GenericJobHeadingTest(unittest.TestCase):
    def test_headings_are_not_jobs(self):
        from norway_company_agent.website import GENERIC_JOB_HEADING
        for t in ("Stillingsannonsen", "Medarbeiderinnlogging", "Ledige stillinger", "Ledig stilling", "Logg inn", "Jobb hos oss",
                  "Sjekk ut våre ledige stillinger", "Karriereside av Teamtailor"):
            self.assertTrue(GENERIC_JOB_HEADING.search(t.casefold()), t)
        for t in ("Seniorutvikler", "Daglig leder", "Fastlege i Hole kommune", "Frontend Engineer", "Ledig stilling som tømrer"):
            self.assertFalse(GENERIC_JOB_HEADING.search(t.casefold()), t)


class RegisteredAddressTest(unittest.TestCase):
    PROFILE = {"evidence": {"registry": {"value": {
        "forretningsadresse": {"adresse": ["Storgata 47"], "postnummer": "2609", "poststed": "LILLEHAMMER"},
        "postadresse": {"adresse": ["Postboks 7800"], "postnummer": "5020", "poststed": "BERGEN"}}}}}

    def test_clean_and_registered_only(self):
        from norway_company_agent.contract import clean_registered_addresses as clean
        self.assertEqual(clean(["Norsk Litteraturfestival Storgata 47 2609 Lillehammer Besøksadresse"], self.PROFILE), ["Storgata 47, 2609 Lillehammer"])
        self.assertEqual(clean(["Personvern Postboks 7800, 5020 Bergen Facebook"], self.PROFILE), ["Postboks 7800, 5020 Bergen"])
        self.assertEqual(clean(["Aug 12, 2026 On Wednesday", "Oslo", "Prime Cargo AS Torvuttaket 26 1540 Vestby"], self.PROFILE), [])
        self.assertEqual(clean(["Tollbugata 2 2609 Bodø"], self.PROFILE), [])  # postcode registered, city is not

    def test_street_names(self):
        from norway_company_agent.contract import _street_words as sw
        self.assertEqual(sw("Kontakt Sven Bruns gate".split()), ["Sven", "Bruns", "gate"])
        self.assertEqual(sw("Personvernerklæring Nedre Vollgate".split()), ["Nedre", "Vollgate"])
        self.assertEqual(sw("Flyt Design Lilleborgeveien".split()), ["Lilleborgeveien"])
        self.assertEqual(sw("Besøk ossMaridalsveien".split()), [])


class ParkedPageTest(unittest.TestCase):
    def test_parked_placeholder_not_publishable(self):
        from norway_company_agent.identity import apply_website_identity_gate
        prof = {"organisation_number": "999999999", "name": "BIVROST AS", "evidence": {}}
        rec = {"status": "available", "value": {"title": "www.bivrost.net is parked", "main_text_excerpt": "www.bivrost.net is parked. Bivrost", "final_url": "https://www.bivrost.net/", "pages": []}}
        self.assertFalse(apply_website_identity_gate(prof, rec)["assessment"]["publishable"])
