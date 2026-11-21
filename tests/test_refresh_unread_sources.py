"""Refresh: a source this run could not read is not a change (false-change rate)."""
from __future__ import annotations

import copy
import unittest

from norway_company_agent.refresh import diff_profile


def _profile(**over) -> dict:
    p = {
        "organisation_number": "898886182", "name": "ADALIA AS", "legal_form": "AS", "municipality": "SANDEFJORD",
        "website": "www.adalia.no", "employees": 3,
        "evidence": {
            "registry": {"status": "available", "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/898886182"},
            "financials": {"status": "available", "value": {"records": [{"revenue": 1696500.0}]}},
            "roles": {"status": "available", "value": {"roles": [{"name": "Iren Therese Lervik", "role_code": "DAGL"}]}},
            "website": {"status": "available", "value": {"title": "Adalia", "description": "Film", "social_links": []}},
        },
    }
    p.update(over)
    return p


class RefreshUnreadSourcesTests(unittest.TestCase):
    def test_an_identical_rerun_has_no_changes(self) -> None:
        self.assertEqual(diff_profile(_profile(), _profile()), [])

    def test_failed_sources_are_not_changes(self) -> None:
        cur = _profile(name=None, legal_form=None, municipality=None, website=None, employees=None)
        cur["evidence"] = {
            "registry": {"status": "source_error", "note": "URLError after 5 attempts"},
            "financials": {"status": "source_error"},
            "roles": {"status": "failed"},
            "website": {"status": "not_found"},  # never looked for: the registry record was missing
        }
        self.assertEqual(diff_profile(_profile(), cur), [])

    def test_a_real_answer_is_still_a_change(self) -> None:
        cur = copy.deepcopy(_profile())
        cur["evidence"]["roles"]["value"]["roles"] = [{"name": "Ny Leder", "role_code": "DAGL"}]
        cur["employees"] = 5
        fields = {c["field"] for c in diff_profile(_profile(), cur)}
        self.assertEqual(fields, {"roles.roles", "registry.employees"})

    def test_a_site_that_is_now_not_found_is_a_change_when_the_registry_was_read(self) -> None:
        cur = copy.deepcopy(_profile())
        cur["evidence"]["website"] = {"status": "not_found"}
        self.assertIn("website.title", {c["field"] for c in diff_profile(_profile(), cur)})

    def test_a_bot_check_page_is_not_a_change(self) -> None:
        prev = copy.deepcopy(_profile())
        prev["evidence"]["website"]["value"].update({"title": "One moment, please...", "description": ""})
        cur = copy.deepcopy(_profile())
        cur["evidence"]["website"]["value"].update({"title": "Sandnes friskole - Nyheter", "description": "Denne nettsiden tilhører Sandnes friskole."})
        self.assertEqual(diff_profile(prev, cur), [])

    def test_empty_values_are_not_changes(self) -> None:
        prev = copy.deepcopy(_profile())
        prev["evidence"]["website"]["value"]["description"] = ""
        cur = copy.deepcopy(_profile())
        cur["evidence"]["website"]["value"]["description"] = None
        self.assertEqual(diff_profile(prev, cur), [])


if __name__ == "__main__":
    unittest.main()
