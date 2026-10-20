from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.evaluate_research_agent import evaluate_suite
from norway_company_agent.research import answer_profile


HASH = hashlib.sha256(b"fixture").hexdigest()


def record(module: str, value: dict) -> dict:
    return {
        "status": "available",
        "source_url": f"https://{module}.example/source",
        "retrieved_at": "2026-10-20T00:00:00Z",
        "content_sha256": HASH,
        "source_class": f"official_{module}",
        "value": value,
    }


def profile(org: str, name: str, municipality: str, employees: int, revenue: int) -> dict:
    return {
        "organisation_number": org,
        "name": name,
        "legal_form": "AS",
        "municipality": municipality,
        "employees": employees,
        "evidence": {
            "registry": record("registry", {}),
            "financials": record(
                "financials",
                {
                    "records": [{
                        "period": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
                        "revenue": revenue,
                        "operating_result": 20,
                        "annual_result": 15,
                        "assets": 100,
                        "debt": 40,
                    }]
                },
            ),
            "roles": record("roles", {"roles": [{"name": "Ada Example", "role": "Daglig leder"}]}),
            "locations": record("locations", {"locations": [{"name": name, "address": "Main street 1"}]}),
            "website": record("website", {"identity_assessment": {"publishable": True}}),
        },
    }


class ResearchEvaluatorTests(unittest.TestCase):
    def test_v3_suite_scores_claims_plans_abstention_and_workspace(self):
        rows = [
            profile("123456789", "ALPHA AS", "OSLO", 10, 200),
            profile("987654321", "BETA AS", "BERGEN", 3, 100),
        ]
        suite = {
            "corpus": "synthetic v3",
            "qa_cases": [{
                "id": "qa01",
                "organisation_number": "123456789",
                "question": "Report the annual accounts, registered leaders, and operating location evidence.",
                "minimum_facts": 8,
                "required_claims": ["Reporting period", "Revenue", "Registered subunit"],
            }],
            "screen_cases": [{
                "id": "screen01",
                "query": "companies in Oslo with more than 5 employees top 1 by revenue",
                "oracle": {
                    "filters": [
                        {"field": "municipality", "operator": "eq", "value": "OSLO"},
                        {"field": "employees", "operator": ">", "value": 5},
                    ],
                    "sort": {"field": "revenue", "direction": "desc", "limit": 1},
                },
            }],
            "unsupported_cases": [{"id": "unsupported01", "query": "companies with positive Glassdoor sentiment"}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate_suite(rows, suite, Path(tmp) / "workspace.json")

        self.assertEqual(report["suite_schema"], "v3")
        self.assertEqual(report["corpus_coverage"]["missing_qa_organisations"], [])
        self.assertEqual(report["qa"]["claim_recall"], 1.0)
        self.assertEqual(report["screens"]["plan_accuracy"], 1.0)
        self.assertTrue(report["screens"]["citations_complete"])
        self.assertEqual(report["unsupported"]["abstention_rate"], 1.0)
        self.assertTrue(report["external_footprint_qa_passed"])
        self.assertTrue(report["qualification_passed"])

    def test_missing_qa_corpus_is_reported_and_cannot_qualify(self):
        suite = {
            "corpus": "missing corpus",
            "qa_cases": [{
                "id": "qa-missing",
                "organisation_number": "111111111",
                "question": "What revenue is reported?",
                "minimum_facts": 1,
                "required_claims": ["Revenue"],
            }],
            "screen_cases": [],
            "unsupported_cases": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate_suite([], suite, Path(tmp) / "workspace.json")

        self.assertEqual(report["corpus_coverage"]["missing_qa_organisations"], ["111111111"])
        self.assertEqual(report["corpus_coverage"]["qa_coverage"], 0.0)
        self.assertFalse(report["external_footprint_qa_passed"])
        self.assertFalse(report["qualification_passed"])

    def test_v1_suite_remains_supported_and_emits_proxy_gate(self):
        rows = [profile("123456789", "ALPHA AS", "OSLO", 10, 200)]
        suite = {
            "corpus": "synthetic v1",
            "single_company": {
                "organisation_number": "123456789",
                "question": "What revenue is reported?",
                "minimum_facts": 1,
            },
            "screens": [{
                "query": "companies in Oslo with more than 5 employees",
                "expected_organisation_numbers": ["123456789"],
                "expected_filter_fields": ["municipality", "employees"],
            }],
            "unsupported": {"query": "companies with positive Glassdoor sentiment", "must_abstain": True},
        }

        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate_suite(rows, suite, Path(tmp) / "workspace.json")

        self.assertEqual(report["suite_schema"], "v1")
        self.assertTrue(report["external_footprint_qa_passed"])
        self.assertTrue(report["qualification_passed"])

    def test_available_zero_subunit_source_is_reported_as_grounded_count(self):
        row = profile("123456789", "ALPHA AS", "OSLO", 10, 200)
        row["evidence"]["locations"]["value"] = {"locations": []}
        answer = answer_profile(row, "Show registered operating location evidence.")

        location_facts = [item for item in answer["facts"] if item["claim"] == "Registered subunit"]
        self.assertEqual(len(location_facts), 1)
        self.assertEqual(location_facts[0]["value"], {"count": 0})
        self.assertTrue(location_facts[0]["content_sha256"])

    def test_physical_subunit_wording_retrieves_registered_locations(self):
        row = profile("123456789", "ALPHA AS", "OSLO", 10, 200)
        answer = answer_profile(row, "List supported financial results and physical subunits.")

        self.assertTrue(any(item["claim"] == "Registered subunit" for item in answer["facts"]))

    def test_empty_screen_result_is_not_mislabeled_as_missing_provenance(self):
        suite = {
            "corpus": "empty screen",
            "qa_cases": [],
            "screen_cases": [{
                "id": "screen-empty",
                "query": "companies in Oslo with more than 5 employees",
                "oracle": {
                    "filters": [
                        {"field": "municipality", "operator": "eq", "value": "OSLO"},
                        {"field": "employees", "operator": ">", "value": 5},
                    ],
                    "sort": None,
                },
            }],
            "unsupported_cases": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate_suite([profile("987654321", "BETA AS", "BERGEN", 3, 100)], suite, Path(tmp) / "workspace.json")

        self.assertEqual(report["screens"]["results"][0]["result_count"], 0)
        self.assertTrue(report["screens"]["citations_complete"])


if __name__ == "__main__":
    unittest.main()
