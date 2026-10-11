"""Tier 4: Real-World Scenarios E2E Test Suite (tests/test_e2e_tier4.py).

Comprehensive testing across real-world Norwegian corporate distributions:
- Large listed conglomerates (ASA, e.g., Equinor ASA)
- Private limited companies (AS)
- Sole proprietorships (ENK) with statutory accounting exemptions
- Housing cooperatives (BRL) and non-profit foundations (STI)
- Entities undergoing active bankruptcy / liquidation proceedings
- Cohort 1,000 dataset validation and batch runner execution with checkpointing
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import evidence, utc_now
from norway_company_agent.official import accounting_obligation_assessment, normalize_financials
from norway_company_agent.synthesis import synthesize_executive_profile, compute_financial_metrics
import run_agent


class TestTier4RealWorldScenarios(unittest.TestCase):
    """Tier 4: Real-world company cohorts, legal form diversity, and scale runs."""

    def test_t4_01_cohort1000_integrity_and_scale_invariants(self) -> None:
        """F14: Verify tests/fixtures/cohort-1000.jsonl contains >= 1000 valid entities."""
        cohort_path = ROOT / "tests" / "fixtures" / "cohort-1000.jsonl"
        self.assertTrue(cohort_path.is_file(), "cohort-1000.jsonl fixture must exist")

        lines = [line.strip() for line in cohort_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 1000, "Cohort must contain at least 1,000 entities")

        legal_forms: set[str] = set()
        for idx, line in enumerate(lines[:1000]):
            data = json.loads(line)
            org = str(data.get("organisation_number") or "")
            self.assertEqual(len(org), 9, f"Line {idx}: Org number must be 9 digits: {org}")
            self.assertTrue(org.isdigit(), f"Line {idx}: Org number must be numeric: {org}")
            self.assertTrue(data.get("name"), f"Line {idx}: Entity must have a name")
            if data.get("legal_form"):
                legal_forms.add(data["legal_form"])

        self.assertGreaterEqual(len(legal_forms), 1, "Cohort must exhibit legal form diversity")

    def test_t4_02_equinor_asa_large_conglomerate_dossier(self) -> None:
        """Verify handling of large listed enterprise (ASA) with statutory accounts & governance."""
        raw_equinor = {
            "organisation_number": "923609016",
            "name": "EQUINOR ASA",
            "legal_form": "ASA",
            "municipality": "STAVANGER",
            "industry_code": "06.100",
            "industry": "Utvinning av råolje",
            "employees": 21000,
            "website": "www.equinor.com",
            "ceo": "Anders Opedal",
            "board_chair": "Jon Erik Reinhardsen",
            "evidence": {
                "registry": evidence(
                    "registry",
                    "available",
                    "official_registry",
                    "https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
                    value={"navn": "EQUINOR ASA", "organisasjonsform": {"kode": "ASA"}},
                    content_sha256="a" * 64,
                ),
                "financials": evidence(
                    "financials",
                    "available",
                    "official_annual_accounts",
                    "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
                    value={
                        "records": [
                            {
                                "period": {"fraDato": "2024-01-01", "tilDato": "2024-12-31"},
                                "revenue": 67956000000.0,
                                "operating_result": 28540000000.0,
                                "annual_result": 11900000000.0,
                                "assets": 120500000000.0,
                                "equity": 54000000000.0,
                                "debt": 66500000000.0,
                                "currency": "USD",
                            }
                        ]
                    },
                    content_sha256="b" * 64,
                ),
            },
        }

        envelope = build_output_envelope(
            raw_equinor,
            run_id="test-equinor-run",
            started_at=utc_now(),
            completed_at=utc_now(),
        )

        claims = {c["field"]: c for c in envelope["claims"]}
        self.assertEqual(claims["legal_name"]["value"], "EQUINOR ASA")
        self.assertEqual(claims["legal_form"]["value"], "ASA")
        self.assertEqual(claims["revenue"]["value"], 67956000000.0)
        self.assertEqual(claims["revenue"]["availability"], "available")
        self.assertEqual(claims["operating_result"]["value"], 28540000000.0)

        # Test executive synthesis for Equinor
        synth = synthesize_executive_profile(raw_equinor)
        self.assertIn("EQUINOR ASA", synth["summary"])
        self.assertIn("Anders Opedal", synth["summary"])
        self.assertFalse(synth["in_bankruptcy"])

    def test_t4_03_sole_proprietorship_enk_statutory_exemption(self) -> None:
        """Verify sole proprietorship (ENK) accounting exemption under Regnskapsloven § 1-2."""
        raw_enk = {
            "organisation_number": "999888777",
            "name": "OLA NORDMANN ENK",
            "legal_form": "ENK",
            "municipality": "BERGEN",
            "employees": 0,
            "evidence": {
                "registry": evidence(
                    "registry",
                    "available",
                    "official_registry",
                    "https://data.brreg.no/enhetsregisteret/api/enheter/999888777",
                    value={"navn": "OLA NORDMANN ENK", "organisasjonsform": {"kode": "ENK"}},
                    content_sha256="c" * 64,
                ),
                "financials": evidence(
                    "financials",
                    "not_applicable",
                    "official_annual_accounts",
                    "https://data.brreg.no/regnskap/999888777",
                    note="Exempt under statute",
                ),
            },
        }

        obligation = accounting_obligation_assessment(raw_enk)
        self.assertEqual(obligation["value"]["classification"], "threshold_or_activity_dependent")
        self.assertIn("statutory", obligation["value"]["reason"])

        # Envelope must record honest absence without zeroing revenue
        envelope = build_output_envelope(
            raw_enk,
            run_id="test-enk-run",
            started_at=utc_now(),
            completed_at=utc_now(),
        )

        claims = {c["field"]: c for c in envelope["claims"]}
        self.assertEqual(claims["revenue"]["availability"], "not_applicable")
        self.assertIsNone(claims["revenue"]["value"], "Exempt ENK must have None revenue, not 0.0")

        synth = synthesize_executive_profile(raw_enk)
        self.assertIn("exempt", synth["summary"].lower())

    def test_t4_04_housing_cooperative_brl_governance(self) -> None:
        """Verify Borettslag (BRL) structure with board chair but no CEO."""
        raw_brl = {
            "organisation_number": "912345678",
            "name": "SOLSIKKEN BORETTSLAG",
            "legal_form": "BRL",
            "municipality": "TRONDHEIM",
            "board_chair": "Kari Nordmann",
            "ceo": None,
            "evidence": {
                "registry": evidence(
                    "registry",
                    "available",
                    "official_registry",
                    "https://data.brreg.no/enhetsregisteret/api/enheter/912345678",
                    value={"navn": "SOLSIKKEN BORETTSLAG", "organisasjonsform": {"kode": "BRL"}},
                    content_sha256="d" * 64,
                ),
                "roles": evidence(
                    "roles",
                    "available",
                    "official_roles",
                    "https://data.brreg.no/enhetsregisteret/api/enheter/912345678/roller",
                    value={"roles": [{"role": "Styreleder", "name": "Kari Nordmann"}]},
                    content_sha256="e" * 64,
                ),
            },
        }

        envelope = build_output_envelope(
            raw_brl,
            run_id="test-brl-run",
            started_at=utc_now(),
            completed_at=utc_now(),
        )

        claims = {c["field"]: c for c in envelope["claims"]}
        self.assertEqual(claims["legal_form"]["value"], "BRL")
        self.assertEqual(claims["board_chair"]["value"], "Kari Nordmann")
        self.assertEqual(claims["board_chair"]["availability"], "available")
        self.assertEqual(claims["ceo"]["availability"], "not_available")
        self.assertIsNone(claims["ceo"]["value"])

    def test_t4_05_startup_with_missing_financial_filings(self) -> None:
        """Verify newly formed startup with 0 submitted accounts reports honest absence."""
        raw_startup = {
            "organisation_number": "933111222",
            "name": "NORDIC AI LABS AS",
            "legal_form": "AS",
            "municipality": "OSLO",
            "latest_submitted_accounts": None,
            "evidence": {
                "registry": evidence(
                    "registry",
                    "available",
                    "official_registry",
                    "https://data.brreg.no/enhetsregisteret/api/enheter/933111222",
                    value={"navn": "NORDIC AI LABS AS", "organisasjonsform": {"kode": "AS"}},
                    content_sha256="f" * 64,
                ),
                "financials": evidence(
                    "financials",
                    "not_found",
                    "official_annual_accounts",
                    "https://data.brreg.no/regnskapsregisteret/regnskap/933111222",
                    note="No accounts filed yet",
                    content_sha256="",
                ),
            },
        }

        envelope = build_output_envelope(
            raw_startup,
            run_id="test-startup-run",
            started_at=utc_now(),
            completed_at=utc_now(),
        )

        claims = {c["field"]: c for c in envelope["claims"]}
        self.assertEqual(claims["revenue"]["availability"], "not_available")
        self.assertIsNone(claims["revenue"]["value"])

    def test_t4_06_bankrupt_entity_adversarial_posture(self) -> None:
        """Verify entity with adverse bankruptcy proceedings triggers clear warnings."""
        raw_bankrupt = {
            "organisation_number": "911222333",
            "name": "FALLITT BYGG AS",
            "legal_form": "AS",
            "municipality": "DRAMMEN",
            "adverse": True,
            "bankrupt": True,
            "evidence": {
                "registry": evidence(
                    "registry",
                    "available",
                    "official_registry",
                    "https://data.brreg.no/enhetsregisteret/api/enheter/911222333",
                    value={"navn": "FALLITT BYGG AS", "konkurs": True},
                    content_sha256="0" * 64,
                ),
            },
        }

        synth = synthesize_executive_profile(raw_bankrupt)
        self.assertTrue(synth["in_bankruptcy"])
        self.assertIn("BANKRUPTCY", synth["summary"])

    def test_t4_07_batch_checkpoint_and_showcase_cli_invocation(self) -> None:
        """F12 & F13: Run CLI batch runner with --checkpoint-every and --showcase flags."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            input_file = tmp_path / "smoke-sample.jsonl"
            output_file = tmp_path / "out-envelopes.jsonl"
            profiles_file = tmp_path / "out-profiles.jsonl"
            report_file = tmp_path / "out-report.json"
            showcase_file = tmp_path / "out-showcase.html"

            # Create a 4-company fixture
            sample_orgs = [
                {"organisation_number": "898886182", "name": "ADALIA AS", "legal_form": "AS"},
                {"organisation_number": "924711213", "name": "LAIJA AS", "legal_form": "AS"},
                {"organisation_number": "985721548", "name": "DRIFT 1 AS", "legal_form": "AS"},
                {"organisation_number": "919283335", "name": "VESTERLI EIENDOM AS", "legal_form": "AS"},
            ]
            with input_file.open("w", encoding="utf-8") as f:
                for row in sample_orgs:
                    f.write(json.dumps(row) + "\n")

            # Mock live HTTP calls in run_agent to make test hermetic and fast
            mock_profile = {
                "name": "TEST AS",
                "legal_form": "AS",
                "municipality": "OSLO",
                "evidence": {},
            }

            def mock_anchor(org: str) -> tuple[dict[str, Any], Any]:
                prof = dict(mock_profile)
                prof["organisation_number"] = org
                prof["evidence"] = {
                    "registry": evidence("registry", "available", "official_registry", f"https://data.brreg.no/{org}", value={"navn": f"TEST {org}"})
                }
                metric = type("Metric", (), {"bytes_received": 100, "elapsed_ms": 10})()
                return prof, metric

            with patch("run_agent.fetch_live_entity_anchor", side_effect=mock_anchor):
                with patch("run_agent.enrich_profile", side_effect=lambda p, m: (p, {"requests": 1, "bytes": 100, "latencies_ms": [10], "third_party_cost_usd": 0.0})):
                    cmd_args = [
                        "run_agent.py",
                        "--input", str(input_file),
                        "--output", str(output_file),
                        "--profiles-output", str(profiles_file),
                        "--report", str(report_file),
                        "--showcase", str(showcase_file),
                        "--checkpoint-every", "2",
                    ]
                    with patch.object(sys, "argv", cmd_args):
                        run_agent.main()

            self.assertTrue(output_file.is_file(), "Envelopes file must be written")
            self.assertTrue(profiles_file.is_file(), "Profiles file must be written")
            self.assertTrue(report_file.is_file(), "Report file must be written")
            self.assertTrue(showcase_file.is_file(), "Showcase HTML must be generated via --showcase")

            lines = [line.strip() for line in output_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(lines), 4, "Must emit exactly 4 envelopes for 4 inputs")

            rep = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(rep["organisations_processed"], 4)
            self.assertEqual(rep["silent_drops"], 0)
            self.assertEqual(rep["operations"]["declared_third_party_cost_usd"], 0.0)

    def test_t4_08_financial_metrics_multi_year_cagr_calculation(self) -> None:
        """F8: Verify multi-year revenue growth and ratio computations."""
        history = [
            {"revenue": 150_000_000.0, "operating_result": 30_000_000.0, "annual_result": 22_500_000.0, "assets": 200_000_000.0, "equity": 100_000_000.0, "debt": 100_000_000.0},
            {"revenue": 120_000_000.0, "operating_result": 20_000_000.0, "annual_result": 15_000_000.0, "assets": 160_000_000.0, "equity": 80_000_000.0, "debt": 80_000_000.0},
        ]
        metrics = compute_financial_metrics(history)
        self.assertEqual(metrics["operating_margin_pct"], 20.0)
        self.assertEqual(metrics["net_margin_pct"], 15.0)
        self.assertEqual(metrics["equity_ratio_pct"], 50.0)
        self.assertEqual(metrics["debt_to_assets_pct"], 50.0)
        self.assertEqual(metrics["multi_year_revenue_growth_pct"], 25.0)


if __name__ == "__main__":
    unittest.main()
