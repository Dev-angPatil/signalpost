"""Focused tests for composing exact company-site observations."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external_footprint import publishable_observation  # noqa: E402
from norway_company_agent.observations import build_observations  # noqa: E402


def _profile() -> dict:
    return {
        "organisation_number": "923609016",
        "evidence": {
            "website": {
                "status": "available",
                "source_url": "https://example.test/",
                "retrieved_at": "2026-08-23T00:00:00Z",
                "value": {
                    "final_url": "https://example.test/",
                    "content_sha256": "a" * 64,
                    "identity_assessment": {
                        "publishable": True,
                        "status": "exact",
                        "score": 1.0,
                        "method": "deterministic_name_org_evidence_v2",
                        "promotion_proof": [{"type": "legal_name", "value": "Example AS"}],
                    },
                    "pages": [{"url": "https://example.test/", "content_sha256": "a" * 64}],
                    "social_links": [{"platform": "linkedin", "url": "https://linkedin.com/company/example"}],
                    "news_items": [
                        {
                            "title": "New contract",
                            "url": "https://example.test/aktuelt/new-contract",
                            "date": "2026-08-20",
                            "content_sha256": "b" * 64,
                        },
                        {
                            "title": "New contract",
                            "url": "https://example.test/aktuelt/new-contract",
                            "date": "2026-08-20",
                            "content_sha256": "b" * 64,
                        },
                        {
                            "title": "New office",
                            "url": "https://example.test/aktuelt/new-office",
                            "date": "2026-08-10",
                        },
                    ],
                },
            }
        },
    }


class CompanySiteObservationsTests(unittest.TestCase):
    def test_verified_company_site_social_links_emit_publishable_handle_observations(self):
        rows = build_observations(_profile())

        handles = [row for row in rows if row["signal_type"] == "profile_handle"]

        self.assertEqual(len(handles), 1)
        self.assertEqual(handles[0]["platform"], "linkedin")
        self.assertEqual(handles[0]["metrics"]["profile_url"], "https://linkedin.com/company/example")
        self.assertEqual(handles[0]["source_url"], "https://example.test/")
        self.assertEqual(handles[0]["content_sha256"], "a" * 64)
        self.assertEqual(handles[0]["strategy"], "verified_handle_extraction")
        self.assertTrue(publishable_observation(handles[0]))

    def test_composes_profile_surface_and_deduplicated_dated_news_with_provenance(self):
        profile = _profile()

        rows = build_observations(profile)

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["strategy"], "company_site_activity")
        self.assertEqual(rows[1]["signal_type"], "profile_handle")
        self.assertEqual(
            [row.get("published_at") for row in rows[2:]],
            ["2026-08-20", "2026-08-10"],
        )
        self.assertEqual(rows[2]["source_url"], "https://example.test/aktuelt/new-contract")
        self.assertEqual(rows[2]["content_sha256"], "b" * 64)
        self.assertEqual(rows[3]["content_sha256"], "a" * 64)
        self.assertEqual({row["retrieved_at"] for row in rows}, {"2026-08-23T00:00:00Z"})
        self.assertTrue(all(row["identity_proof"] for row in rows))
        self.assertTrue(all(row["acquisition_mode"] == "permitted_public_page" for row in rows))
        self.assertTrue(all(row["rights_status"] == "approved" for row in rows))
        self.assertTrue(all(len(row["content_sha256"]) == 64 for row in rows))
        self.assertTrue(all(publishable_observation(row) for row in rows))
        self.assertEqual(
            [row["id"] for row in rows],
            [row["id"] for row in build_observations(profile)],
        )

    def test_unavailable_quarantined_and_non_exact_sites_emit_nothing(self):
        for status, assessment in (
            ("failed", {"publishable": True, "status": "exact"}),
            ("quarantined", {"publishable": True, "status": "exact"}),
            ("available", {"publishable": False, "status": "related_or_uncertain"}),
            ("available", {"publishable": True, "status": "related_or_uncertain"}),
            ("available", {"publishable": True, "status": "review"}),
        ):
            profile = _profile()
            website = profile["evidence"]["website"]
            website["status"] = status
            website["value"]["identity_assessment"] = assessment
            self.assertEqual(build_observations(profile), [])

    def test_invalid_captured_items_are_not_promoted(self):
        profile = _profile()
        value = profile["evidence"]["website"]["value"]
        value["news_items"] = [
            {"title": "Missing URL", "date": "2026-08-20"},
            {"title": "Missing date", "url": "https://example.test/aktuelt/missing-date"},
            {
                "title": "Bad hash",
                "url": "https://example.test/aktuelt/bad-hash",
                "date": "2026-08-10",
                "content_sha256": "short",
            },
        ]

        rows = build_observations(profile)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["strategy"], "company_site_activity")
        self.assertEqual(rows[1]["strategy"], "verified_handle_extraction")

    def test_verified_active_career_page_emits_dated_job_observations(self):
        profile = _profile()
        value = profile["evidence"]["website"]["value"]
        value["hiring"] = {
            "career_url": "https://example.test/karriere",
            "hiring_active": True,
            "open_positions_count": 2,
            "open_positions": ["Senior Engineer", "Product Manager"],
        }
        value["pages"].append({
            "url": "https://example.test/karriere",
            "content_sha256": "c" * 64,
        })

        rows = build_observations(profile)

        jobs = [row for row in rows if row["signal_type"] == "job_posting"]
        self.assertEqual(len(jobs), 2)
        self.assertEqual({row["source_url"] for row in jobs}, {"https://example.test/karriere"})
        self.assertEqual({row["content_sha256"] for row in jobs}, {"c" * 64})
        self.assertEqual({row["metrics"]["title"] for row in jobs}, {"Senior Engineer", "Product Manager"})
        self.assertTrue(all(row["strategy"] == "jobs_feed_discovery" for row in jobs))
        self.assertTrue(all(publishable_observation(row) for row in jobs))

    def test_external_career_url_without_captured_page_is_not_promoted(self):
        profile = _profile()
        profile["evidence"]["website"]["value"]["hiring"] = {
            "career_url": "https://jobs.example.test/company/example",
            "hiring_active": True,
            "open_positions_count": 1,
            "open_positions": ["Senior Engineer"],
        }

        rows = build_observations(profile)

        self.assertFalse(any(row["signal_type"] == "job_posting" for row in rows))


if __name__ == "__main__":
    unittest.main()
