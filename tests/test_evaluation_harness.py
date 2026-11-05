"""Hermetic regression tests for evaluator-owned score and audit gates."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import evaluate_external_footprint


def _observation() -> dict[str, object]:
    return {
        "id": "obs-1",
        "organisation_number": "123456789",
        "platform": "company_site",
        "signal_type": "company_profile",
        "source_url": "https://example.no/company",
        "retrieved_at": "2026-10-20T00:00:00Z",
        "content_sha256": "a" * 64,
        "exact_entity": True,
        "identity_proof": "legal name and organisation number",
        "acquisition_mode": "permitted_public_page",
        "rights_status": "approved",
    }


class TestEvaluationHarness(unittest.TestCase):
    def test_audit_size_gate_counts_labeled_observations_not_unrelated_labels(self) -> None:
        """A large label file must not pass when only one observation is audited."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = root / "profiles.jsonl"
            observations = root / "observations.jsonl"
            labels = root / "labels.jsonl"
            output = root / "report.json"
            profiles.write_text(json.dumps({"organisation_number": "123456789"}) + "\n", encoding="utf-8")
            observations.write_text(json.dumps(_observation()) + "\n", encoding="utf-8")
            labels.write_text(
                "".join(
                    json.dumps(
                        {
                            "id": "obs-1" if index == 0 else f"unrelated-{index}",
                            "exact_entity": True,
                            "metric_correct": True,
                            "sentiment_correct": True,
                        }
                    )
                    + "\n"
                    for index in range(100)
                ),
                encoding="utf-8",
            )

            argv = [
                "evaluate_external_footprint.py",
                "--profiles",
                str(profiles),
                "--observations",
                str(observations),
                "--labels",
                str(labels),
                "--output",
                str(output),
                "--minimum-audit",
                "100",
            ]
            with patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit) as raised:
                    evaluate_external_footprint.main()

            self.assertEqual(raised.exception.code, 1)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["audited_observations"], 1)
            self.assertFalse(report["audit_size_gate"])
            self.assertFalse(report["qualification_passed"])

    def test_duplicate_observation_ids_are_rejected_before_scoring(self) -> None:
        """Duplicate IDs must not be allowed to inflate audited publication counts."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = root / "profiles.jsonl"
            observations = root / "observations.jsonl"
            labels = root / "labels.jsonl"
            output = root / "report.json"
            profiles.write_text(json.dumps({"organisation_number": "123456789"}) + "\n", encoding="utf-8")
            observations.write_text(
                json.dumps(_observation()) + "\n" + json.dumps(_observation()) + "\n",
                encoding="utf-8",
            )
            labels.write_text(
                json.dumps(
                    {
                        "id": "obs-1",
                        "exact_entity": True,
                        "metric_correct": True,
                        "sentiment_correct": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            argv = [
                "evaluate_external_footprint.py",
                "--profiles",
                str(profiles),
                "--observations",
                str(observations),
                "--labels",
                str(labels),
                "--output",
                str(output),
            ]
            with patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(ValueError, "Observation IDs must be unique"):
                    evaluate_external_footprint.main()


if __name__ == "__main__":
    unittest.main()
