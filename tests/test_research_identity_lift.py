from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.research import answer_profile


HASH = hashlib.sha256(b"identity-lift").hexdigest()


def record(module: str, value: dict) -> dict:
    return {
        "status": "available",
        "source_url": f"https://{module}.example/source",
        "retrieved_at": "2026-10-20T00:00:00Z",
        "content_sha256": HASH,
        "source_class": f"official_{module}",
        "value": value,
    }


def profile() -> dict:
    return {
        "organisation_number": "123456789",
        "name": "ALPHA AS",
        "legal_form": "AS",
        "municipality": "OSLO",
        "employees": 10,
        "evidence": {
            "registry": record("registry", {}),
            "financials": record(
                "financials",
                {
                    "records": [{
                        "period": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
                        "revenue": 200,
                        "operating_result": 20,
                        "annual_result": 15,
                        "assets": 100,
                        "debt": 40,
                    }]
                },
            ),
            "roles": record("roles", {"roles": [{"name": "Ada Example", "role": "Daglig leder"}]}),
            "locations": record("locations", {"locations": [{"name": "ALPHA AS", "address": "Main street 1"}]}),
        },
    }


class ResearchIdentityLiftTests(unittest.TestCase):
    def test_combined_finance_and_roles_include_cited_identity_anchors(self):
        row = profile()

        answer = answer_profile(row, "What revenue and leadership roles can you support?")

        identity = {item["claim"]: item for item in answer["facts"] if item["classification"] == "official_registry_fact"}
        self.assertEqual(set(identity), {
            "Registered name",
            "Organisation number",
            "Legal form",
            "Municipality",
            "Registry employee count",
        })
        self.assertTrue(all(item["source_url"] == row["evidence"]["registry"]["source_url"] for item in identity.values()))
        self.assertTrue(all(item["content_sha256"] == HASH for item in identity.values()))

    def test_single_financial_topic_does_not_receive_identity_lift(self):
        answer = answer_profile(profile(), "What revenue is reported?")

        self.assertFalse(any(item["classification"] == "official_registry_fact" for item in answer["facts"]))

    def test_missing_identity_values_are_not_invented(self):
        row = profile()
        row["municipality"] = None
        row["employees"] = ""

        answer = answer_profile(row, "Summarize the accounts and registered leaders.")

        claims = {item["claim"] for item in answer["facts"]}
        self.assertNotIn("Municipality", claims)
        self.assertNotIn("Registry employee count", claims)


if __name__ == "__main__":
    unittest.main()
