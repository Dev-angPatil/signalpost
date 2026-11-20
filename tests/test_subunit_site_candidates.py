from __future__ import annotations

import unittest

import run_agent
from norway_company_agent.official import normalize_locations


def _profile(contacts, registry_email=None):
    return {
        "organisation_number": "999666337",
        "evidence": {
            "registry": {"status": "available", "value": {"epostadresse": registry_email}},
            "locations": {"status": "available", "value": {"locations": [], "subunit_contacts": contacts}},
        },
    }


class SubunitContactsTest(unittest.TestCase):
    def test_normalize_locations_keeps_contacts_out_of_location_items(self):
        body = {"_embedded": {"underenheter": [
            {"organisasjonsnummer": "811545252", "navn": "A AVD", "epostadresse": "post@acme.no", "hjemmeside": "www.acme.no"},
            {"organisasjonsnummer": "811545253", "navn": "B AVD"},
            {"organisasjonsnummer": "811545254", "navn": "C AVD", "epostadresse": "x@old.no", "slettedato": "2020-01-01"},
        ]}}
        value = normalize_locations(body)
        self.assertEqual(len(value["locations"]), 3)
        self.assertNotIn("email", value["locations"][0])
        self.assertEqual(value["subunit_contacts"], [
            {"organisation_number": "811545252", "homepage": "www.acme.no", "email": "post@acme.no"},
        ])

    def test_candidates_rank_by_frequency_and_skip_generic_blocked_and_registry_domains(self):
        profile = _profile([
            {"homepage": None, "email": "a@gmail.com"},
            {"homepage": "https://shop.acme.no/start", "email": "a@acme.no"},
            {"homepage": None, "email": "b@acme.no"},
            {"homepage": "www.facebook.com/acme", "email": "c@regnskap.no"},
            {"homepage": None, "email": "d@main.no"},
        ], registry_email="post@main.no")
        self.assertEqual(run_agent._subunit_site_candidates(profile), [
            "https://www.acme.no",
            "https://shop.acme.no/start",
            "https://www.regnskap.no",
        ])

    def test_no_contacts_no_candidates(self):
        self.assertEqual(run_agent._subunit_site_candidates(_profile([])), [])
        self.assertEqual(run_agent._subunit_site_candidates({"evidence": {}}), [])


if __name__ == "__main__":
    unittest.main()
