"""Further facts published from official registry and annual-account records (hermetic)."""
from __future__ import annotations

import unittest

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import evidence
from norway_company_agent.official import normalize_financials

ORG = "920936830"


def _filing(year: int, revenue: float | None, *, wage: float | None = 369441.0) -> dict:
    driftsinntekter = {"salgsinntekter": revenue, "sumDriftsinntekter": revenue} if revenue is not None else {}
    driftskostnad = {"sumDriftskostnad": 65934272.0}
    if wage is not None:
        driftskostnad["loennskostnad"] = wage
    return {
        "journalnr": f"{year + 1}000001",
        "regnskapstype": "SELSKAP",
        "regnskapsperiode": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"},
        "valuta": "NOK",
        "avviklingsregnskap": False,
        "revisjon": {"ikkeRevidertAarsregnskap": False, "fravalgRevisjon": False},
        "regnkapsprinsipper": {"smaaForetak": True},
        "egenkapitalGjeld": {
            "egenkapital": {"sumEgenkapital": 3905741.0},
            "gjeldOversikt": {"sumGjeld": 18489104.0, "kortsiktigGjeld": {"sumKortsiktigGjeld": 18489104.0}, "langsiktigGjeld": {"sumLangsiktigGjeld": 0.0}},
        },
        "eiendeler": {"sumEiendeler": 22394845.0, "sumBankinnskuddOgKontanter": 11880112.0, "omloepsmidler": {"sumOmloepsmidler": 22280596.0}},
        "resultatregnskapResultat": {
            "ordinaertResultatFoerSkattekostnad": 1014917.0,
            "ordinaertResultatSkattekostnad": 246184.0,
            "aarsresultat": 768733.0,
            "finansresultat": {"nettoFinans": 499920.0},
            "driftsresultat": {"driftsresultat": 514997.0, "driftsinntekter": driftsinntekter, "driftskostnad": driftskostnad},
        },
    }


REGISTRY = {
    "organisasjonsnummer": ORG,
    "navn": "TRONDHEIM STAGE AS",
    "organisasjonsform": {"kode": "AS"},
    "stiftelsesdato": "2018-05-28",
    "vedtektsdato": "2018-05-28",
    "registrertIMvaregisteret": True,
    "registrertIForetaksregisteret": True,
    "erIKonsern": False,
    "kapital": {"belop": 30000.0, "valuta": "NOK", "type": "Aksjekapital", "antallAksjer": 300, "innfortDato": "2018-06-04"},
    "historiskeNavn": [{"navn": "EVENT & MORE AS", "fraDato": "2018-06-04 12:56:30", "tilDato": "2018-10-30 10:40:20"}],
    "naeringskode1": {"kode": "90.390", "beskrivelse": "Andre tjenester"},
    "naeringskode2": {"kode": "82.300", "beskrivelse": "Kongress-, messe- og utstillingsvirksomhet"},
    "institusjonellSektorkode": {"kode": "2100", "beskrivelse": "Private aksjeselskaper mv."},
    "konkurs": False,
    "underAvvikling": False,
}


def _profile(filings: list[dict], roles_status: str = "available") -> dict:
    return {
        "organisation_number": ORG,
        "name": "TRONDHEIM STAGE AS",
        "legal_form": "AS",
        "evidence": {
            "registry": evidence("registry", "available", "official_registry_live", f"https://data.brreg.no/enhetsregisteret/api/enheter/{ORG}", value=REGISTRY, content_sha256="a" * 64),
            "financials": evidence("financials", "available", "official_annual_accounts", f"https://data.brreg.no/regnskapsregisteret/regnskap/{ORG}", value=normalize_financials(filings), content_sha256="b" * 64),
            "roles": evidence("roles", roles_status, "official_roles", f"https://data.brreg.no/enhetsregisteret/api/enheter/{ORG}/roller", note="HTTP 503 after 5 attempts"),
        },
    }


def _envelope(profile: dict) -> dict:
    return build_output_envelope(profile, run_id="t", started_at="2026-11-19T00:00:00Z", completed_at="2026-11-19T00:01:00Z")


def _claims(env: dict) -> dict[str, dict]:
    return {c["field"]: c for c in env["claims"]}


class RegistryExtraFactsTests(unittest.TestCase):
    def test_registry_facts_are_published_with_registry_evidence(self) -> None:
        claims = _claims(_envelope(_profile([_filing(2025, 131332705.0)])))
        self.assertEqual(claims["founding_date"]["value"], "2018-05-28")
        self.assertEqual(claims["share_capital"]["value"]["amount"], 30000.0)
        self.assertEqual(claims["share_capital"]["value"]["shares"], 300)
        self.assertEqual(claims["former_names"]["value"][0], {"name": "EVENT & MORE AS", "from": "2018-06-04", "to": "2018-10-30"})
        self.assertIs(claims["vat_registered"]["value"], True)
        self.assertIs(claims["part_of_corporate_group"]["value"], False)
        self.assertEqual(claims["secondary_industry_codes"]["value"][0]["code"], "82.300")
        self.assertEqual(claims["institutional_sector"]["value"], "Private aksjeselskaper mv.")
        for field in ("founding_date", "share_capital", "former_names", "vat_registered"):
            self.assertEqual(claims[field]["evidence_ids"], ["ev-registry"])

    def test_absent_registry_fields_are_not_invented(self) -> None:
        profile = _profile([_filing(2025, 1.0)])
        profile["evidence"]["registry"]["value"] = {"organisasjonsnummer": ORG, "navn": "X AS"}
        claims = _claims(_envelope(profile))
        for field in ("founding_date", "share_capital", "former_names", "vat_registered", "secondary_industry_codes"):
            self.assertNotEqual(claims.get(field, {}).get("availability"), "available", field)


class AccountExtraFactsTests(unittest.TestCase):
    def test_latest_year_line_items_and_period(self) -> None:
        env = _envelope(_profile([_filing(2024, 91270842.0), _filing(2025, 131332705.0)]))
        claims = _claims(env)
        self.assertEqual(claims["revenue"]["value"], 131332705.0)
        self.assertEqual(claims["wage_costs"]["value"], 369441.0)
        self.assertEqual(claims["profit_before_tax"]["value"], 1014917.0)
        self.assertEqual(claims["long_term_debt"]["value"], 0.0)  # stated as 0 in the filing
        self.assertIs(claims["accounts_audited"]["value"], True)
        self.assertEqual(claims["accounts_reporting_period"]["value"]["to"], "2025-12-31")

    def test_unstated_line_items_are_not_published_as_zero(self) -> None:
        claims = _claims(_envelope(_profile([_filing(2025, None, wage=None)])))
        self.assertNotIn("wage_costs", claims)
        self.assertNotIn("sales_revenue", claims)
        self.assertEqual(claims["revenue"]["availability"], "not_available")

    def test_prior_years_have_their_own_dated_evidence(self) -> None:
        env = _envelope(_profile([_filing(2023, 66449269.0), _filing(2024, 91270842.0), _filing(2025, 131332705.0)]))
        claims = _claims(env)
        evidence_by_id = {e["id"]: e for e in env["evidence"]}
        for year, revenue in (("2024", 91270842.0), ("2023", 66449269.0)):
            claim = claims[f"annual_accounts_{year}"]
            self.assertEqual(claim["value"]["revenue"], revenue)
            ev = evidence_by_id[claim["evidence_ids"][0]]
            self.assertEqual(ev["reporting_period"], {"from": f"{year}-01-01", "to": f"{year}-12-31"})
            self.assertEqual(len(ev["content_sha256"]), 64)
        self.assertNotIn("annual_accounts_2025", claims)  # the latest year is published as flat fields


class FailedSourceStateTests(unittest.TestCase):
    def test_failed_roles_fetch_is_reported_as_failed_not_absent(self) -> None:
        claims = _claims(_envelope(_profile([_filing(2025, 1.0)], roles_status="source_error")))
        self.assertEqual(claims["ceo"]["availability"], "failed")
        self.assertEqual(claims["board_chair"]["availability"], "failed")


if __name__ == "__main__":
    unittest.main()


class PlaceholderDescriptionTests(unittest.TestCase):
    def test_template_descriptions_are_rejected(self) -> None:
        from norway_company_agent.website import is_placeholder_description
        for text in ("Your home page description", "Just another WordPress site", "Lorem ipsum dolor", "Description", "Hjem"):
            self.assertTrue(is_placeholder_description(text), text)
        for text in ("Norbond AS leverer maling og lakk til bilverksteder.", "Rørleggerfirma i Bergen siden 1987"):
            self.assertFalse(is_placeholder_description(text), text)


class DeletedEntityTests(unittest.TestCase):
    def test_deletion_date_is_published(self) -> None:
        profile = _profile([_filing(2025, 1.0)])
        profile["evidence"]["registry"]["value"] = {**REGISTRY, "slettedato": "2026-08-31"}
        claims = _claims(_envelope(profile))
        self.assertEqual(claims["deleted_from_register_date"]["value"], "2026-08-31")
        self.assertEqual(claims["deleted_from_register_date"]["evidence_ids"], ["ev-registry"])


class DistressLeadTests(unittest.TestCase):
    def test_deleted_after_merger_is_not_called_bankrupt(self) -> None:
        from norway_company_agent.synthesis import synthesize_executive_profile
        profile = {
            "name": "YTRE MATRE ENERGI AS", "organisation_number": "985594651", "legal_form": "AS",
            "evidence": {"registry": {"status": "available", "value": {"slettedato": "2026-08-31", "konkurs": False, "underAvvikling": False}}},
            "announcements": {"events": [{"category": "dissolution", "title": "Sletting etter fusjon", "date": "2026-08-31"}]},
        }
        summary = synthesize_executive_profile(profile)["summary"]
        self.assertTrue(summary.startswith("DELETED: YTRE MATRE ENERGI AS was deleted"), summary)
        self.assertIn("Sletting etter fusjon", summary)
        self.assertNotIn("BANKRUPTCY", summary)

    def test_voluntary_liquidation_is_not_called_bankruptcy(self) -> None:
        from norway_company_agent.synthesis import synthesize_executive_profile
        profile = {
            "name": "AVVIKLES AS", "organisation_number": "999999999", "legal_form": "AS", "adverse": True,
            "evidence": {"registry": {"status": "available", "value": {"konkurs": False, "underAvvikling": True}}},
        }
        summary = synthesize_executive_profile(profile)["summary"]
        self.assertTrue(summary.startswith("UNDER LIQUIDATION"), summary)

    def test_old_resolved_notice_does_not_lead(self) -> None:
        from norway_company_agent.synthesis import synthesize_executive_profile
        profile = {
            "name": "AS BOSBØEN JAGTSELSKAB", "organisation_number": "929210263", "legal_form": "AS",
            "evidence": {"registry": {"status": "available", "value": {"konkurs": False, "underAvvikling": False}}},
            "announcements": {"events": [{"category": "insolvency", "title": "Varsel om tvangsoppløsning", "date": "2013-04-26"}]},
        }
        summary = synthesize_executive_profile(profile)["summary"]
        self.assertFalse(summary.startswith(("INSOLVENCY", "COMPANY IN BANKRUPTCY")), summary)
