import unittest
from datetime import date, timedelta

from norway_company_agent.synthesis import synthesize_executive_profile


def profile_with_notice(notice_date):
    return {
        "organisation_number": "923609016",
        "name": "TEST COMPANY AS",
        "legal_form": "AS",
        "municipality": "OSLO",
        "industry": "General commercial activity",
        "evidence": {
            "registry": {"status": "available", "value": {}},
            "announcements": {
                "status": "available",
                "value": {"events": [{"type": "Board appointment", "date": notice_date}]},
            },
        },
    }


class CheaperModelTaskTests(unittest.TestCase):
    def test_recent_registry_notice_uses_recent_wording(self):
        summary = synthesize_executive_profile(
            profile_with_notice((date.today() - timedelta(days=30)).isoformat())
        )["summary"]
        self.assertIn("Recent registry notices:", summary)

    def test_old_registry_notice_uses_latest_wording(self):
        summary = synthesize_executive_profile(
            profile_with_notice((date.today() - timedelta(days=400)).isoformat())
        )["summary"]
        self.assertIn("Latest registry notices:", summary)
        self.assertNotIn("Recent registry notices:", summary)
