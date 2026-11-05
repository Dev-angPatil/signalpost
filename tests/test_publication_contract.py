from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.showcase import generate_showcase_html
from norway_company_agent.synthesis import compute_financial_metrics, synthesize_executive_profile


class TestPublicationLayer(unittest.TestCase):
    def test_each_dated_action_and_news_item_is_a_grounded_claim(self):
        profile = {
            "organisation_number": "923609016",
            "name": "EXACT AS",
            "evidence": {
                "registry": {"status": "available", "value": {"navn": "EXACT AS"}, "source_url": "https://registry.example"},
                "announcements": {
                    "status": "available",
                    "source_url": "https://notices.example",
                    "value": {
                        "events": [
                            {"date": "2025-01-02", "title": "Old notice", "category": "legal_filing", "source_url": "https://notices.example/1", "sha256": "1" * 64},
                            {"date": "2026-02-03", "title": "New notice", "category": "capital_change", "source_url": "https://notices.example/2", "sha256": "2" * 64},
                        ]
                    },
                },
                "news": {
                    "status": "available",
                    "source_url": "https://news.example/rss",
                    "content_sha256": "3" * 64,
                    "value": {"items": [
                        {"date": "2026-03-04", "title": "News item", "url": "https://news.example/1", "content_sha256": "4" * 64},
                    ]},
                },
            },
        }
        envelope = build_output_envelope(profile, run_id="r", started_at="s", completed_at="c")
        claims = {item["field"]: item for item in envelope["claims"]}
        for field, value, evidence_id in (
            ("corporate_action_1_date", "2026-02-03", "ev-announcement-1"),
            ("corporate_action_1_title", "New notice", "ev-announcement-1"),
            ("corporate_action_2_date", "2025-01-02", "ev-announcement-2"),
            ("news_1_date", "2026-03-04", "ev-news-1"),
            ("news_1_title", "News item", "ev-news-1"),
        ):
            self.assertEqual(claims[field]["value"], value)
            self.assertEqual(claims[field]["evidence_ids"], [evidence_id])
        evidence = {item["id"]: item for item in envelope["evidence"]}
        self.assertEqual(evidence["ev-announcement-1"]["content_sha256"], "2" * 64)
        self.assertIn("2026-02-03", evidence["ev-announcement-1"]["claim_span"])
        self.assertIn("New notice", evidence["ev-announcement-1"]["claim_span"])
        self.assertEqual(evidence["ev-news-1"]["content_sha256"], "4" * 64)

    def test_ratios_ignore_untrusted_precomputed_metrics(self):
        records = [{"revenue": 100, "operating_result": 20, "assets": 200, "equity": 100, "debt": 100}]
        profile = {"name": "RATIO AS", "legal_form": "AS", "evidence": {"financials": {"status": "available", "value": {"records": records}}}, "metrics": {"operating_margin_pct": 999}}
        self.assertEqual(compute_financial_metrics(records)["operating_margin_pct"], 20.0)
        self.assertEqual(synthesize_executive_profile(profile)["financial_health"]["operating_margin_pct"], 20.0)

    def test_news_item_without_own_hash_uses_rss_snapshot_hash(self):
        rss_hash = "d" * 64
        profile = {
            "organisation_number": "923609016",
            "name": "RSS AS",
            "evidence": {
                "registry": {"status": "available", "value": {"navn": "RSS AS"}},
                "news": {
                    "status": "available",
                    "source_url": "https://news.google.com/rss/search?q=rss",
                    "content_sha256": rss_hash,
                    "value": {"items": [{
                        "title": "RSS AS opens a new office",
                        "date": "2026-03-04",
                        "url": "https://news.example/rss-as-office",
                    }]},
                },
            },
        }

        envelope = build_output_envelope(profile, run_id="r", started_at="s", completed_at="c")
        evidence = {item["id"]: item for item in envelope["evidence"]}
        self.assertEqual(evidence["ev-news-1"]["content_sha256"], rss_hash)

    def test_ratios_abstain_on_non_numeric_or_non_finite_filing_values(self):
        metrics = compute_financial_metrics([{"revenue": "100", "operating_result": 20, "assets": float("nan"), "equity": 1}])
        self.assertIsNone(metrics["operating_margin_pct"])
        self.assertIsNone(metrics["equity_ratio_pct"])

    def test_missing_locations_are_not_published_as_zero_known_fact(self):
        from norway_company_agent.synthesis import build_knowns_and_unknowns

        knowns, unknowns = build_knowns_and_unknowns({"name": "NO LOCATIONS AS", "legal_form": "AS", "evidence": {}})
        self.assertFalse(any(item["item"] == "registered_workplaces_count" for item in knowns))
        self.assertTrue(any(item["item"] == "operating_subunits" and item["status"] == "not_available" for item in unknowns))

    def test_failed_publication_sources_keep_failed_availability(self):
        profile = {
            "organisation_number": "923609016",
            "evidence": {
                "registry": {"status": "available", "value": {"navn": "FAILED AS"}},
                "financials": {"status": "failed", "error": "upstream timeout"},
                "announcements": {"status": "failed", "error": "upstream timeout"},
                "news": {"status": "blocked", "error": "policy"},
            },
        }
        claims = {item["field"]: item for item in build_output_envelope(profile, run_id="r", started_at="s", completed_at="c")["claims"]}
        self.assertEqual(claims["corporate_announcements_count"]["availability"], "failed")
        self.assertEqual(claims["latest_corporate_action_date"]["availability"], "failed")
        self.assertEqual(claims["latest_news_title"]["availability"], "blocked")
        self.assertEqual(claims["revenue"]["availability"], "failed")

    def test_showcase_contains_inspectable_event_lineage_payload(self):
        profile = {
            "organisation_number": "923609016", "name": "HTML AS",
            "evidence": {"registry": {"status": "available", "source_url": "https://registry.example", "content_sha256": "a" * 64, "value": {"navn": "HTML AS"}},
                         "announcements": {"status": "available", "content_sha256": "b" * 64, "value": {"events": [{"date": "2026-01-01", "title": "Filed", "source_url": "https://notice.example", "sha256": "c" * 64}]}}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            html = generate_showcase_html([profile], Path(tmp) / "showcase.html")
        self.assertIn("Filed", html)
        self.assertIn("2026-01-01", html)
        self.assertIn("c" * 64, html)

    def test_showcase_ratios_are_recomputed_from_filing_records(self):
        profile = {
            "organisation_number": "923609016", "name": "RATIO HTML AS",
            "financial": {"records": [{"revenue": 100, "operating_result": 20, "assets": 200, "equity": 100}]},
            "synthesis": {"financial_health": {"operating_margin_pct": 999, "equity_ratio_pct": 999}},
            "evidence": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            html = generate_showcase_html([profile], Path(tmp) / "showcase.html")
        self.assertIn('"operating_margin_pct": 20.0', html)
        self.assertNotIn('"operating_margin_pct": 999', html)


if __name__ == "__main__":
    unittest.main()
