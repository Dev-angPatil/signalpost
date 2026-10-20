"""Regression coverage for score-focused terminal-envelope claim recall."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.contract import build_output_envelope


def _claims_by_field(envelope: dict) -> dict[str, dict]:
    return {claim["field"]: claim for claim in envelope["claims"]}


class TestContractRecallClaims(unittest.TestCase):
    def test_emits_supported_identity_governance_location_financial_and_synthesis_claims(self) -> None:
        profile = {
            "organisation_number": "923609016",
            "name": "NORDIC SYSTEMS AS",
            "legal_form": "AS",
            "industry": "Computer programming activities",
            "nace_code": "62.010",
            "latest_submitted_accounts": "2024",
            "municipality": "OSLO",
            "evidence": {
                "registry": {
                    "status": "available",
                    "source_url": "https://data.brreg.no/enheter/923609016",
                    "value": {
                        "navn": "NORDIC SYSTEMS AS",
                        "naeringskode1": {
                            "kode": "62.010",
                            "beskrivelse": "Computer programming activities",
                        },
                        "sisteInnsendteAarsregnskap": "2024",
                        "forretningsadresse": {
                            "adresse": ["Storgata 1"],
                            "postnummer": "0155",
                            "poststed": "OSLO",
                        },
                        "postadresse": {
                            "adresse": ["Postboks 10"],
                            "postnummer": "0101",
                            "poststed": "OSLO",
                        },
                        "status": "registered",
                    },
                    "content_sha256": "a" * 64,
                },
                "financials": {
                    "status": "available",
                    "source_url": "https://data.brreg.no/regnskap/923609016",
                    "value": {
                        "records": [
                            {
                                "period": {"tilDato": "2024-12-31"},
                                "revenue": 100.0,
                            }
                        ]
                    },
                    "content_sha256": "b" * 64,
                },
                "roles": {
                    "status": "available",
                    "source_url": "https://data.brreg.no/enheter/923609016/roller",
                    "value": {
                        "roles": [
                            {"role": "Daglig leder", "name": "Ada Nord", "inactive": False},
                            {"role": "Styrets leder", "name": "Bo Sør", "inactive": False},
                        ]
                    },
                    "content_sha256": "c" * 64,
                },
                "locations": {
                    "status": "available",
                    "source_url": "https://data.brreg.no/underenheter?overordnetEnhet=923609016",
                    "value": {
                        "locations": [
                            {
                                "organisation_number": "923609024",
                                "name": "NORDIC SYSTEMS BERGEN",
                                "address": {"poststed": "BERGEN"},
                            }
                        ]
                    },
                    "content_sha256": "d" * 64,
                },
                "announcements": {
                    "status": "available",
                    "source_url": "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=923609016",
                    "value": {
                        "events": [],
                        "categories": {
                            "legal_filing": {"count": 1, "latest_date": "2024-06-01"},
                            "insolvency": {"count": 0, "latest_date": None},
                        },
                    },
                    "content_sha256": "e" * 64,
                },
            },
            "synthesis": {
                "business_scope": "NORDIC SYSTEMS AS builds business software.",
                "executive_summary": "NORDIC SYSTEMS AS is led by Ada Nord.",
                "financial_health": {
                    "operating_margin_pct": 12.5,
                    "net_margin_pct": 8.0,
                    "equity_ratio_pct": 60.0,
                    "debt_to_assets_pct": 40.0,
                    "multi_year_revenue_growth_pct": 25.0,
                },
                "knowns": [
                    {"item": "legal_name", "evidence_id": "ev-registry"},
                    {"item": "ceo", "evidence_id": "ev-roles"},
                ],
                "unknowns": [
                    {"item": "website", "status": "not_available"},
                    {"item": "sentiment", "status": "not_available"},
                ],
            },
        }

        claims = _claims_by_field(
            build_output_envelope(profile, run_id="recall-1", started_at="s", completed_at="c")
        )

        self.assertEqual(claims["nace_description"]["value"], "Computer programming activities")
        self.assertEqual(claims["latest_submitted_accounts"]["value"], "2024")
        self.assertEqual(
            claims["registered_address"]["value"],
            profile["evidence"]["registry"]["value"]["forretningsadresse"],
        )
        self.assertEqual(claims["postal_address"]["value"], profile["evidence"]["registry"]["value"]["postadresse"])
        self.assertEqual(claims["registration_status"]["value"], "registered")
        self.assertEqual(claims["leadership"]["value"], profile["evidence"]["roles"]["value"]["roles"])
        self.assertEqual(claims["locations"]["value"], profile["evidence"]["locations"]["value"]["locations"])
        self.assertEqual(claims["operating_margin_pct"]["value"], 12.5)
        self.assertEqual(claims["debt_to_assets_pct"]["value"], 40.0)
        self.assertEqual(claims["business_scope"]["value"], profile["synthesis"]["business_scope"])
        self.assertEqual(claims["executive_summary"]["value"], profile["synthesis"]["executive_summary"])
        self.assertEqual(claims["known_facts_count"]["value"], 2)
        self.assertEqual(claims["unreported_dimensions_count"]["value"], 2)
        self.assertEqual(claims["latest_legal_filing_date"]["value"], "2024-06-01")
        self.assertEqual(claims["latest_legal_filing_date"]["availability"], "available")
        self.assertIsNone(claims["latest_insolvency_date"]["value"])
        self.assertEqual(claims["latest_insolvency_date"]["availability"], "not_available")
        self.assertEqual(claims["latest_legal_filing_date"]["evidence_ids"], ["ev-announcements"])
        self.assertEqual(claims["latest_insolvency_date"]["evidence_ids"], ["ev-announcements"])

        for field, evidence_id in (
            ("nace_description", "ev-registry"),
            ("latest_submitted_accounts", "ev-registry"),
            ("registered_address", "ev-registry"),
            ("postal_address", "ev-registry"),
            ("registration_status", "ev-registry"),
            ("leadership", "ev-roles"),
            ("locations", "ev-locations"),
            ("operating_margin_pct", "ev-financials"),
            ("business_scope", "ev-registry"),
            ("executive_summary", "ev-registry"),
            ("known_facts_count", "ev-registry"),
        ):
            self.assertEqual(claims[field]["availability"], "available")
            self.assertIn(evidence_id, claims[field]["evidence_ids"])

    def test_category_dates_and_synthesis_fields_abstain_without_supporting_evidence(self) -> None:
        profile = {
            "organisation_number": "999999999",
            "name": "UNSUPPORTED AS",
            "evidence": {
                "registry": {
                    "status": "available",
                    "source_url": "https://data.brreg.no/enheter/999999999",
                    "value": {"navn": "UNSUPPORTED AS"},
                    "content_sha256": "f" * 64,
                },
                "financials": {
                    "status": "available",
                    "source_url": "https://data.brreg.no/regnskap/999999999",
                    "value": {"records": []},
                    "content_sha256": "0" * 64,
                },
            },
            "synthesis": {
                "business_scope": "This unsupported sentence must not become a claim.",
                "executive_summary": "This unsupported summary must not become a claim.",
                "financial_health": {"operating_margin_pct": 99.0},
                "knowns": [{"item": "made_up", "evidence_id": "missing-evidence"}],
                "unknowns": [],
            },
        }

        claims = _claims_by_field(
            build_output_envelope(profile, run_id="recall-2", started_at="s", completed_at="c")
        )

        for field in (
            "nace_description",
            "latest_submitted_accounts",
            "registered_address",
            "postal_address",
            "registration_status",
            "leadership",
            "locations",
            "operating_margin_pct",
            "business_scope",
            "executive_summary",
            "known_facts_count",
            "unreported_dimensions_count",
            "latest_legal_filing_date",
            "latest_insolvency_date",
        ):
            self.assertIn(field, claims)
            self.assertIsNone(claims[field]["value"], field)
            self.assertNotEqual(claims[field]["availability"], "available", field)

        self.assertIsNone(claims["registered_workplaces_count"]["value"])


if __name__ == "__main__":
    unittest.main()
