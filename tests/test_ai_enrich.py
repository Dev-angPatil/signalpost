"""Model analysis layer: grounded output is published, unsupported output is dropped."""
from __future__ import annotations

import json
import unittest
import unittest.mock
from types import SimpleNamespace

from norway_company_agent import ai_enrich
from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import utc_now


def _profile() -> dict:
    return {
        "organisation_number": "979598009",
        "name": "SLAKTERIET BREKKE AS",
        "legal_form": "AS",
        "municipality": "KINN",
        "industry": "Bearbeiding og konservering av fisk",
        "industry_code": "10.200",
        "evidence": {
            "registry": {"field": "registry", "status": "available", "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/979598009",
                         "retrieved_at": utc_now(), "content_sha256": "a" * 64, "value": {"navn": "SLAKTERIET BREKKE AS"}},
            "financials": {"field": "financials", "status": "available", "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/979598009",
                           "retrieved_at": utc_now(), "content_sha256": "b" * 64, "value": {"records": [
                               {"period": {"tilDato": "2025-12-31"}, "revenue": 97096245.0, "operating_result": 17775528.0, "assets": 60000000.0, "equity": 30000000.0},
                               {"period": {"tilDato": "2024-12-31"}, "revenue": 95603269.0, "operating_result": 15000000.0},
                           ]}},
            "website": {"field": "website", "status": "available", "source_url": "https://slakteriet-as.no/", "retrieved_at": utc_now(),
                        "content_sha256": "c" * 64,
                        "value": {"title": "Slakteriet", "description": "Slakteriet sløyer og pakker havets råvarer.",
                                  "main_text_excerpt": "Vi finnes i Florø og Brekke.", "identity_assessment": {"publishable": True},
                                  "news_items": [{"title": "Slakteriet satsar på menneska", "url": "https://slakteriet-as.no/2025/04/29/menneska/", "date": "2025-04-29", "source": "site_feed:feed"}]}},
            "jobs": {"field": "jobs", "status": "available", "source_url": "https://arbeidsplassen.nav.no/stillinger/stilling/u1",
                     "retrieved_at": utc_now(), "content_sha256": "d" * 64, "value": {"postings": [
                         {"title": "Produksjonsleder", "url": "https://arbeidsplassen.nav.no/stillinger/stilling/u1", "published": "2026-11-10",
                          "expires": "2026-12-01", "location": "FLORØ", "employer_orgnr": "979598009", "content_sha256": "d" * 64,
                          "description_excerpt": "Vi søker produksjonsleder med fagbrev og ledererfaring. Fast stilling."}]}},
        },
        "synthesis": {"summary": "SLAKTERIET BREKKE AS template summary."},
    }


def _model_output() -> dict:
    return {
        "summary_sentences": [
            {"text": "SLAKTERIET BREKKE AS is hiring a Produksjonsleder in FLORØ, published 2026-11-10.", "fact_ids": ["J1"]},
            {"text": "Revenue was NOK 97 096 245 in 2025, a +1.6% change versus 2024.", "fact_ids": ["A1"]},
            {"text": "Revenue will reach NOK 120 000 000 next year.", "fact_ids": ["A1"]},          # invented number
            {"text": "It is the largest fish processor in Norway.", "fact_ids": []},                 # uncited
            {"text": "It was founded by a famous family.", "fact_ids": ["Z9"]},                      # unknown fact
        ],
        "business_description": {"text": "The company says it guts and packs seafood in Florø and Brekke.", "fact_ids": ["W1", "W2"]},
        "jobs": [{"fact_id": "J1", "role_category": "manufacturing", "seniority": "lead_manager", "employment": "permanent",
                  "work_mode": "on_site", "key_requirements": ["fagbrev", "ledererfaring"], "one_line": "Leads fish-processing production."}],
        "news": [{"fact_id": "N1", "event_type": "hiring", "materiality": "low", "one_line": "Post about investing in staff."}],
        "watch_points": [],
    }


class FakeClient:
    def __init__(self, payload: dict, stop_reason: str = "end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            stop_reason=self.stop_reason, model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=2000, cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=500),
        )


class AiEnrichTests(unittest.TestCase):
    def test_fact_sheet_has_ids_and_sources(self):
        facts = ai_enrich.build_fact_sheet(_profile())
        ids = {f["id"] for f in facts}
        self.assertTrue({"R1", "A1", "A2", "W1", "J1", "N1"} <= ids)
        a1 = next(f for f in facts if f["id"] == "A1")
        self.assertIn("NOK 97 096 245", a1["text"])
        self.assertIn("+1.6%", a1["text"])
        self.assertTrue(all(f["source_url"] for f in facts))

    def test_unsupported_sentences_are_dropped(self):
        profile = _profile()
        client = FakeClient(_model_output())
        meta = ai_enrich.enrich_with_ai(profile, lambda _c: True, client=client)
        ai = profile["ai_analysis"]
        texts = [s["text"] for s in ai["summary_sentences"]]
        self.assertEqual(len(texts), 2)
        self.assertFalse(any("120 000 000" in t or "largest" in t or "famous" in t for t in texts))
        self.assertEqual(len(ai["dropped_unsupported"]), 3)
        self.assertEqual(ai["jobs"][0]["role_category"], "manufacturing")
        self.assertGreater(meta["cost_usd"], 0)
        req = client.calls[0]
        self.assertEqual(req["model"], ai_enrich.MODEL)
        self.assertEqual(req["fallbacks"], "default")
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")

    def test_refusal_or_budget_publishes_nothing(self):
        profile = _profile()
        ai_enrich.enrich_with_ai(profile, lambda _c: True, client=FakeClient(_model_output(), stop_reason="refusal"))
        self.assertEqual(profile["ai_analysis"]["status"], "failed")
        profile2 = _profile()
        meta = ai_enrich.enrich_with_ai(profile2, lambda _c: False, client=FakeClient(_model_output()))
        self.assertEqual(meta["skipped"], "budget")
        self.assertNotIn("ai_analysis", profile2)

    def test_registry_only_company_is_not_eligible(self):
        profile = _profile()
        for key in ("website", "jobs"):
            profile["evidence"].pop(key)
        self.assertFalse(ai_enrich.is_eligible(profile))

    @unittest.mock.patch.dict("os.environ", {"SIGNALPOST_AI_CLAIMS": "1"})
    def test_envelope_claims_are_evidence_backed(self):
        profile = _profile()
        ai_enrich.enrich_with_ai(profile, lambda _c: True, client=FakeClient(_model_output()))
        ai_enrich.apply_to_synthesis(profile)
        self.assertIn("hiring a Produksjonsleder", profile["synthesis"]["summary"])
        env = build_output_envelope(profile, run_id="t", started_at=utc_now(), completed_at=utc_now())
        claims = {c["field"]: c for c in env["claims"]}
        ev = {e["id"]: e for e in env["evidence"]}
        self.assertEqual(claims["job_posting_1_role_category"]["value"], "manufacturing")
        self.assertEqual(claims["ai_news_1_event_type"]["value"], "hiring")
        self.assertIn("ai_business_description", claims)
        for field in ("job_posting_1_role_category", "ai_news_1_event_type", "ai_business_description"):
            for eid in claims[field]["evidence_ids"]:
                self.assertTrue(ev[eid]["source_url"].startswith("https://"))
                self.assertEqual(len(ev[eid]["content_sha256"]), 64)
        self.assertIn("hiring a Produksjonsleder", claims["executive_summary"]["value"])


if __name__ == "__main__":
    unittest.main()


class AiClaimsDefaultTests(unittest.TestCase):
    def test_item_labels_are_not_scored_claims_by_default(self):
        profile = _profile()
        ai_enrich.enrich_with_ai(profile, lambda _c: True, client=FakeClient(_model_output()))
        ai_enrich.apply_to_synthesis(profile)
        env = build_output_envelope(profile, run_id="t", started_at=utc_now(), completed_at=utc_now())
        fields = {c["field"] for c in env["claims"]}
        self.assertFalse(any(f.startswith("ai_") or f.endswith("_role_category") for f in fields))
        summary = next(c for c in env["claims"] if c["field"] == "executive_summary")
        self.assertIn("hiring a Produksjonsleder", summary["value"])
