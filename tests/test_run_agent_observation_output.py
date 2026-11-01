from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import run_agent  # noqa: E402


def _profile() -> dict:
    return {
        "organisation_number": "923609016",
        "name": "EXAMPLE AS",
        "legal_form": "AS",
        "municipality": "OSLO",
        "evidence": {
            "website": {
                "status": "available",
                "source_url": "https://example.test/",
                "retrieved_at": "2026-10-20T00:00:00Z",
                "value": {
                    "final_url": "https://example.test/",
                    "content_sha256": "a" * 64,
                    "identity_assessment": {
                        "publishable": True,
                        "status": "exact",
                        "score": 1.0,
                        "promotion_proof": [{"type": "legal_name", "value": "EXAMPLE AS"}],
                    },
                    "pages": [{"url": "https://example.test/", "content_sha256": "a" * 64}],
                    "news_items": [
                        {
                            "title": "New contract",
                            "url": "https://example.test/aktuelt/new-contract",
                            "date": "2026-10-19",
                            "content_sha256": "b" * 64,
                        }
                    ],
                },
            }
        },
        "run_metrics": {
            "requests": 1,
            "bytes": 100,
            "latencies_ms": [10],
            "third_party_cost_usd": 0.0,
        },
    }


class RunAgentObservationOutputTests(unittest.TestCase):
    def test_audit_envelopes_detects_missing_claim_evidence_and_budget_violations(self):
        envelope = {
            "organisation_number": "923609016",
            "claims": [{
                "field": "legal_name",
                "value": "EXAMPLE AS",
                "availability": "available",
                "evidence_ids": [],
            }],
            "evidence": [],
        }

        audit = run_agent.audit_envelopes(
            [envelope],
            expected_count=1,
            operations={"requests": 2001, "third_party_cost_usd": 10.01},
        )

        self.assertFalse(audit["envelope_contract_valid"])
        self.assertFalse(audit["all_claims_evidence_backed"])
        self.assertFalse(audit["request_budget_within_limit"])
        self.assertFalse(audit["cost_budget_within_limit"])

    def test_main_writes_publishable_observations_and_reports_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "envelopes.jsonl"
            profiles = root / "profiles.jsonl"
            report = root / "report.json"
            observations = root / "observations.jsonl"

            with patch.object(
                run_agent,
                "fetch_live_entity_anchor",
                return_value=(_profile(), SimpleNamespace(bytes_received=100, elapsed_ms=10)),
            ), \
                patch.object(run_agent, "enrich_profile", return_value=(_profile(), _profile()["run_metrics"])):
                argv = [
                    "run_agent.py",
                    "--org", "923609016",
                    "--output", str(output),
                    "--profiles-output", str(profiles),
                    "--report", str(report),
                    "--observations-output", str(observations),
                ]
                with patch.object(sys, "argv", argv):
                    run_agent.main()

            rows = [json.loads(line) for line in observations.read_text(encoding="utf-8").splitlines() if line.strip()]
            summary = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["organisation_number"] for row in rows}, {"923609016"})
            self.assertEqual(summary["external_observations"]["emitted"], 2)
            self.assertEqual(summary["external_observations"]["companies"], 1)
            self.assertTrue(all(row["rights_status"] == "approved" for row in rows))

    def test_envelope_includes_configured_official_external_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "envelopes.jsonl"
            profiles = root / "profiles.jsonl"
            report = root / "report.json"
            observations = root / "observations.jsonl"
            external = {
                "id": "youtube-channel-metrics-example",
                "organisation_number": "923609016",
                "platform": "youtube",
                "signal_type": "profile_metrics",
                "source_url": "https://www.googleapis.com/youtube/v3/channels?part=snippet%2Cstatistics&id=UCexample",
                "retrieved_at": "2026-10-20T00:00:00Z",
                "content_sha256": "c" * 64,
                "exact_entity": True,
                "identity_proof": [{"type": "verified_site_cross_link", "value": "https://youtube.com/@example"}],
                "acquisition_mode": "official_api",
                "rights_status": "approved",
                "source_class": "official_platform_api",
                "evidence_span": "Example AS; subscribers=42; views=1234; videos=7",
                "metrics": {"subscriber_count": 42, "view_count": 1234, "video_count": 7, "channel_title": "Example AS"},
            }
            profile = _profile()
            profile["_external_observations"] = [external]

            with patch.object(run_agent, "fetch_live_entity_anchor", return_value=(profile, SimpleNamespace(bytes_received=100, elapsed_ms=10))), \
                patch.object(run_agent, "enrich_profile", return_value=(profile, profile["run_metrics"])):
                argv = [
                    "run_agent.py", "--org", "923609016",
                    "--output", str(output), "--profiles-output", str(profiles),
                    "--observations-output", str(observations), "--report", str(report),
                ]
                with patch.object(sys, "argv", argv):
                    run_agent.main()

            envelope = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
            fields = {claim["field"] for claim in envelope["claims"]}
            self.assertIn("youtube_subscriber_count", fields)
            self.assertIn("youtube_video_count", fields)
            self.assertIn("ev-external-youtube-channel-metrics-example", {item["id"] for item in envelope["evidence"]})

    def test_envelope_maps_verified_company_site_handle_to_social_claim(self):
        profile = _profile()
        profile["_external_observations"] = [{
            "id": "company-social-handle-example",
            "organisation_number": "923609016",
            "platform": "linkedin",
            "signal_type": "profile_handle",
            "source_url": "https://example.test/",
            "retrieved_at": "2026-10-20T00:00:00Z",
            "content_sha256": "e" * 64,
            "exact_entity": True,
            "identity_proof": [{"type": "website_identity_gate"}],
            "acquisition_mode": "permitted_public_page",
            "rights_status": "approved",
            "source_class": "company_site",
            "evidence_span": "Verified company site links to linkedin profile",
            "metrics": {"profile_url": "https://linkedin.com/company/example"},
        }]

        envelope = run_agent.build_output_envelope(
            profile,
            run_id="r",
            started_at="s",
            completed_at="c",
            operations_metric=profile["run_metrics"],
        )

        claim = next(item for item in envelope["claims"] if item["field"] == "social_linkedin")
        self.assertEqual(claim["value"], "https://linkedin.com/company/example")
        self.assertEqual(claim["availability"], "available")
        self.assertEqual(claim["evidence_ids"], ["ev-external-company-social-handle-example"])

    @patch.dict(
        run_agent.os.environ,
        {"GOOGLE_PLACES_API_KEY": "places-key", "GOOGLE_PLACES_COST_PER_REQUEST_USD": "0.01"},
        clear=True,
    )
    @patch("run_agent.google_places_text_search")
    def test_enrich_profile_records_official_places_observation_and_cost(self, mock_places):
        class Rows(list):
            metrics = {"requests": 1, "bytes": 20, "latencies_ms": [7]}

        mock_places.return_value = Rows([
            {
                "id": "place-1",
                "organisation_number": "923609016",
                "platform": "google_places",
                "signal_type": "place_summary",
                "source_url": "https://places.googleapis.com/v1/places:searchText",
                "retrieved_at": "2026-10-20T00:00:00Z",
                "content_sha256": "d" * 64,
                "exact_entity": True,
                "identity_proof": [{"type": "exact_legal_name_and_address"}],
                "acquisition_mode": "official_api",
                "rights_status": "approved",
                "source_class": "official_platform_api",
                "evidence_span": "Example AS; Storgata 1; rating=4.5; reviews=12",
                "metrics": {"name": "Example AS", "rating": 4.5, "review_count": 12},
            }
        ])
        profile = _profile()

        enriched, metric = run_agent.enrich_profile(profile, ["registry"])

        mock_places.assert_called_once()
        self.assertEqual(len(enriched["_external_observations"]), 1)
        self.assertEqual(metric["requests"], 1)
        self.assertEqual(metric["third_party_cost_usd"], 0.01)


if __name__ == "__main__":
    unittest.main()
