"""Candidate generation must never publish anything the sources did not contain."""
from __future__ import annotations

import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from norway_company_agent import web_discovery as wd
from norway_company_agent.news import fetch_serper_news


def _profile(**extra) -> dict:
    base = {
        "organisation_number": "815011422",
        "name": "HALVORSEN MASKIN AS",
        "legal_form": "AS",
        "municipality": "TROMSØ",
        "industry": "Grunnarbeid",
        "industry_code": "43.120",
        "employees": 6,
        "evidence": {
            "registry": {"value": {
                "forretningsadresse": {"adresse": ["Storgata 1"], "postnummer": "9008", "poststed": "TROMSØ"},
                "historiskeNavn": [{"navn": "NORD GRAVING AS", "tilDato": "2023-01-01 10:00:00"},
                                   {"navn": "ANCIENT NAME AS", "tilDato": "2001-01-01 10:00:00"}],
            }},
            "financials": {"value": {"records": [{"period": {"tilDato": "2025-12-31"}, "revenue": 12_000_000.0}]}},
            "locations": {"value": {"locations": [{"organisation_number": "999888777", "name": "HALVORSEN MASKIN AS AVD SENJA"}]}},
        },
    }
    base.update(extra)
    return base


def _resp(text: str, stop: str = "end_turn", blocks: list | None = None, model: str = "claude-haiku-5-5"):
    content = list(blocks or []) + [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=content, stop_reason=stop, model=model,
                           usage=SimpleNamespace(input_tokens=3000, cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=200))


class FakeClient:
    def __init__(self, response):
        self.response, self.calls = response, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class PriorityAndAliasTests(unittest.TestCase):
    def test_housing_coops_and_dormant_holdings_are_skipped(self):
        self.assertEqual(wd.web_presence_priority(_profile(legal_form="BRL")), -1)
        dormant = _profile(industry_code="64.200", employees=None)
        dormant["evidence"]["financials"]["value"]["records"][0]["revenue"] = 0.0
        self.assertEqual(wd.web_presence_priority(dormant), -1)
        self.assertTrue(wd.should_search({"name": "X AS", "evidence": {}}))  # unknown -> allowed

    def test_active_consumer_company_ranks_high(self):
        p = _profile()
        self.assertGreaterEqual(wd.web_presence_priority(p), 3)
        self.assertTrue(wd.places_eligible(p))
        self.assertTrue(wd.agent_search_eligible(p))

    def test_aliases_from_subunits_recent_former_names_and_brands(self):
        p = _profile(_discovered_brands=["Halvorsen Graving"])
        aliases = wd.candidate_aliases(p, limit=5)
        self.assertIn("Halvorsen Graving", aliases)
        self.assertIn("NORD GRAVING AS", aliases)
        self.assertIn("HALVORSEN MASKIN AS AVD SENJA", aliases)
        self.assertNotIn("ANCIENT NAME AS", aliases)
        self.assertNotIn("HALVORSEN MASKIN AS", aliases)


class BudgetTests(unittest.TestCase):
    def test_slice_cap_and_global_cap_both_apply(self):
        spent = []
        budget = wd.BudgetSlices()
        budget.configure(lambda c: (spent.append(c) or sum(spent) <= 0.03))
        budget.caps["rerank"] = 0.01
        self.assertTrue(budget.try_spend("rerank", 0.006))
        self.assertFalse(budget.try_spend("rerank", 0.006))     # slice exhausted
        self.assertTrue(budget.try_spend("search", 0.02))
        self.assertFalse(budget.try_spend("search", 0.02))      # global exhausted

    def test_settle_returns_unused_reservation(self):
        refunds = []
        budget = wd.BudgetSlices()
        budget.configure(lambda c: True, refunds.append)
        budget.caps["rerank"] = 0.003
        self.assertTrue(budget.try_spend("rerank", 0.003))
        budget.settle("rerank", 0.003, 0.0005)
        self.assertAlmostEqual(budget.spent["rerank"], 0.0005)
        self.assertAlmostEqual(refunds[0], 0.0025)
        self.assertTrue(budget.try_spend("rerank", 0.002))


class WikidataTests(unittest.TestCase):
    def test_parses_bindings_and_normalises_org_numbers(self):
        body = {"results": {"bindings": [
            {"org": {"value": "815 011 422"}, "site": {"value": "https://halvorsen.no/"}},
            {"org": {"value": "815011422"}, "site": {"value": "https://halvorsen.no/"}},
            {"org": {"value": "123"}, "site": {"value": "https://bad.no/"}},
        ]}}

        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        seen = {}
        def fake_open(req, timeout):
            seen["data"] = req.data.decode()
            return R(json.dumps(body).encode())

        out = wd.wikidata_websites(["815011422"], fetch=fake_open)
        self.assertEqual(out, {"815011422": ["https://halvorsen.no/"]})
        self.assertIn("P2333", seen["data"])


class RerankTests(unittest.TestCase):
    RESULTS = [
        {"url": "https://www.proff.no/selskap/halvorsen", "title": "Halvorsen Maskin AS - Proff", "snippet": "Org.nr 815011422"},
        {"url": "https://halvorsengraving.no/", "title": "Halvorsen Graving – graving i Tromsø", "snippet": "Halvorsen Graving utfører grunnarbeid"},
    ]

    def test_only_shown_urls_and_verbatim_brands_survive(self):
        payload = {
            "candidate_urls": ["https://halvorsengraving.no/", "https://invented-by-model.no/", "https://www.proff.no/selskap/halvorsen"],
            "brand_names": ["Halvorsen Graving", "Made Up Brand"],
            "followup_query": "Halvorsen Graving Tromsø",
        }
        client = FakeClient(_resp(json.dumps(payload)))
        out = wd.rerank_search_results(_profile(), self.RESULTS, [], client=client)
        self.assertEqual(out["candidate_urls"], ["https://halvorsengraving.no/"])
        self.assertEqual(out["brand_names"], ["Halvorsen Graving"])
        self.assertEqual(out["followup_query"], "Halvorsen Graving Tromsø")
        self.assertEqual(client.calls[0]["model"], wd.SEARCH_MODEL)
        self.assertEqual(client.calls[0]["output_config"]["format"]["type"], "json_schema")
        self.assertGreater(out["cost_usd"], 0)

    def test_refusal_yields_nothing(self):
        out = wd.rerank_search_results(_profile(), self.RESULTS, [], client=FakeClient(_resp("{}", stop="refusal")))
        self.assertEqual(out["candidate_urls"], [])


class ModelWebSearchTests(unittest.TestCase):
    def test_keeps_only_hosts_present_in_search_results(self):
        blocks = [
            SimpleNamespace(type="server_tool_use", name="web_search"),
            SimpleNamespace(type="web_search_tool_result", content=[
                SimpleNamespace(url="https://halvorsengraving.no/om-oss", title="Om oss"),
                SimpleNamespace(url="https://www.gulesider.no/halvorsen", title="Gule sider"),
            ]),
        ]
        text = json.dumps({"candidate_urls": ["https://halvorsengraving.no/om-oss", "https://hallucinated.no/", "https://www.gulesider.no/halvorsen"]})
        client = FakeClient(_resp(text, blocks=blocks))
        out = wd.model_web_search_candidates(_profile(), [], client=client)
        self.assertEqual(out["candidate_urls"], ["https://halvorsengraving.no/"])
        self.assertEqual(out["searches"], 1)
        tool = client.calls[0]["tools"][0]
        self.assertEqual(tool["type"], "web_search_20250305")
        self.assertIn("proff.no", tool["blocked_domains"])
        self.assertNotIn("allowed_domains", tool)
        self.assertAlmostEqual(out["cost_usd"], 0.01 + (3000 * 0.10 + 200 * 0.50) / 1_000_000, places=6)


class PlacesTests(unittest.TestCase):
    def test_only_address_matched_places_give_candidates(self):
        places = [
            {"websiteUri": "https://halvorsengraving.no/kontakt", "formattedAddress": "Storgata 1, 9008 Tromsø, Norge"},
            {"websiteUri": "https://other.no/", "formattedAddress": "Annen vei 5, 9010 Tromsø, Norge"},
            {"websiteUri": "https://www.facebook.com/halvorsen", "formattedAddress": "Storgata 1, 9008 Tromsø"},
        ]
        self.assertEqual(wd.places_website_candidates(_profile(), places), ["https://halvorsengraving.no/"])


class CareerTests(unittest.TestCase):
    HTML = """<html><body><h1>Ledige stillinger</h1>
      <a href="/jobb/maskinforer">Maskinfører</a> Tromsø, søknadsfrist 1. desember
      <a href="https://evil.example/phish">Anleggsleder</a>
      <a href="/jobb/sverige">Projektledare</a> Stockholm
    </body></html>"""

    def _profile_with_site(self):
        p = _profile()
        p["evidence"]["website"] = {"status": "available", "source_url": "https://halvorsen.no/",
                                    "value": {"final_url": "https://halvorsen.no/", "identity_assessment": {"publishable": True},
                                              "career_url": "https://halvorsen.no/ledige-stillinger",
                                              "career_portals": [{"platform": "teamtailor", "url": "https://othergroup.teamtailor.com/jobs"},
                                                                 {"platform": "teamtailor", "url": "https://halvorsen.teamtailor.com/jobs"}]}}
        return p

    def test_candidates_are_own_site_or_name_matched_ats(self):
        pages = wd.career_page_candidates(self._profile_with_site())
        self.assertEqual(pages, ["https://halvorsen.no/ledige-stillinger", "https://halvorsen.teamtailor.com/jobs"])

    def test_postings_need_verbatim_title_norway_and_on_site_links(self):
        payload = {"postings": [
            {"title": "Maskinfører", "link_id": "L1", "location": "Tromsø", "in_norway": True, "deadline": "1. desember"},
            {"title": "Anleggsleder", "link_id": "L2", "location": "", "in_norway": True, "deadline": ""},
            {"title": "Projektledare", "link_id": "L3", "location": "Stockholm", "in_norway": False, "deadline": ""},
            {"title": "Invented Role", "link_id": "L1", "location": "", "in_norway": True, "deadline": ""},
        ]}
        out = wd.extract_career_postings(self._profile_with_site(), "https://halvorsen.no/ledige-stillinger", self.HTML, "e" * 64,
                                         client=FakeClient(_resp(json.dumps(payload))))
        titles = {p["title"]: p for p in out["postings"]}
        self.assertEqual(set(titles), {"Maskinfører", "Anleggsleder"})
        self.assertEqual(titles["Maskinfører"]["url"], "https://halvorsen.no/jobb/maskinforer")
        self.assertEqual(titles["Anleggsleder"]["url"], "https://halvorsen.no/ledige-stillinger")  # off-site link refused
        self.assertEqual(titles["Maskinfører"]["source"], "company_career_page")


class SharedDomainTests(unittest.TestCase):
    def _site(self, proof_type):
        return {"status": "available", "source_url": "https://group.no/", "value": {
            "final_url": "https://group.no/", "identity_assessment": {"publishable": True, "promotion_proof": [{"type": proof_type}]}}}

    def test_only_the_company_whose_org_number_is_on_the_site_keeps_it(self):
        a = {"organisation_number": "1", "evidence": {"website": self._site("organisation_number")}}
        b = {"organisation_number": "2", "evidence": {"website": self._site("legal_name")}}
        c = {"organisation_number": "3", "evidence": {"website": {**self._site("legal_name"), "source_url": "https://solo.no/",
                                                                   "value": {"final_url": "https://solo.no/", "identity_assessment": {"publishable": True}}}}}
        self.assertEqual(wd.quarantine_shared_domains([a, b, c]), ["2"])
        self.assertTrue(a["evidence"]["website"]["value"]["identity_assessment"]["publishable"])
        self.assertFalse(b["evidence"]["website"]["value"]["identity_assessment"]["publishable"])
        self.assertTrue(c["evidence"]["website"]["value"]["identity_assessment"]["publishable"])


class SerperNewsTests(unittest.TestCase):
    def test_strict_headline_rule_and_dates(self):
        body = {"news": [
            {"title": "Halvorsen Maskin AS vinner kontrakt", "link": "https://nordlys.no/a", "date": "Jun 4, 2025", "source": "Nordlys"},
            {"title": "Halvorsen i Bergen åpner butikk", "link": "https://bt.no/b", "date": "2 days ago", "source": "BT"},
            {"title": "Halvorsen Maskin AS ansetter", "link": "https://x.no/c", "date": "3 months ago", "source": "X"},
        ]}
        record, metrics = fetch_serper_news("HALVORSEN MASKIN AS", "key", "815011422",
                                            http_post=lambda url, payload, timeout: json.dumps(body).encode())
        self.assertEqual(record["status"], "available")
        self.assertEqual([i["url"] for i in record["value"]["items"]], ["https://nordlys.no/a"])
        self.assertEqual(record["value"]["items"][0]["date"], "2025-06-04")
        self.assertEqual(metrics["third_party_cost_usd"], 0.001)


class SearchFlowTests(unittest.TestCase):
    """End to end through run_agent: alias query finds the brand site; proof on site publishes it;
    social profiles seen only in search results are never published."""

    @patch.dict(os.environ, {"SERPER_API_KEY": "real-looking-key", "SIGNALPOST_AI": "0"})
    @patch("run_agent.probe_candidate_domains", return_value=None)
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_website")
    @patch("run_agent.fetch_official_modules", return_value=({}, []))
    @patch("norway_company_agent.nav_jobs.feed_ready", return_value=False)
    def test_alias_query_finds_brand_site(self, _nav, _official, mock_website, mock_serper, _probe):
        import run_agent
        from norway_company_agent.contract import build_output_envelope
        from norway_company_agent.evidence import utc_now

        run_agent._GLOBAL_COST_GOVERNOR.reset(max_budget_usd=9.5)
        wd.BUDGET.reset()

        def serper(query, key):
            if "NORD GRAVING" in query.upper():
                return ([{"url": "https://nordgraving.no/", "title": "Nord Graving – Tromsø", "snippet": "graving", "rank": 1},
                         {"url": "https://www.facebook.com/nordgraving", "title": "Nord Graving | Facebook", "snippet": "", "rank": 2}],
                        {"requests": 1, "bytes": 10, "latencies_ms": [5], "third_party_cost_usd": 0.001})
            return ([], {"requests": 1, "bytes": 10, "latencies_ms": [5], "third_party_cost_usd": 0.001})
        mock_serper.side_effect = serper

        def site(url, timeout=5.0):
            if "nordgraving.no" in url:
                return ({"field": "website", "status": "available", "source_url": url, "retrieved_at": utc_now(), "content_sha256": "f" * 64,
                         "value": {"final_url": url, "title": "Nord Graving", "content_sha256": "f" * 64,
                                   "main_text_excerpt": "Nord Graving er et varemerke for Halvorsen Maskin AS, org.nr. 815 011 422.",
                                   "social_links": []}},
                        {"requests": 1, "bytes": 100, "latencies_ms": [10]})
            return ({"field": "website", "status": "not_found", "source_url": "x", "value": None}, {"requests": 0, "bytes": 0, "latencies_ms": []})
        mock_website.side_effect = site

        profile = _profile(website=None)
        enriched, metric = run_agent.enrich_profile(profile, ["website"])
        self.assertEqual(enriched["website"], "https://nordgraving.no/")
        web = enriched["evidence"]["website"]
        self.assertTrue(web["value"]["identity_assessment"]["publishable"])
        self.assertEqual(web["value"]["identity_assessment"]["promotion_proof"][0]["type"], "organisation_number")
        self.assertEqual(web["source_type"], "search_alias_verified_company_website")

        env = build_output_envelope(enriched, run_id="t", started_at=utc_now(), completed_at=utc_now())
        fields = {c["field"] for c in env["claims"] if c["availability"] == "available"}
        self.assertNotIn("social_facebook", fields)   # seen only in search results


if __name__ == "__main__":
    unittest.main()


class QuarantineStateTests(unittest.TestCase):
    @patch("run_agent.fetch_website")
    def test_conflicting_org_number_candidate_is_never_publishable(self, mock_website):
        import run_agent
        from norway_company_agent.evidence import utc_now
        from norway_company_agent.identity import build_company_number_profile

        mock_website.return_value = ({
            "field": "website", "status": "available", "source_url": "https://halvorsen.no/", "retrieved_at": utc_now(),
            "content_sha256": "a" * 64,
            "value": {"final_url": "https://halvorsen.no/", "title": "Halvorsen Maskin AS",
                      "main_text_excerpt": "Halvorsen Maskin AS i Bergen, org.nr 999 888 111. Graving og transport.",
                      "identity_text_excerpt": "Halvorsen Maskin AS"},
        }, {"requests": 1, "bytes": 10, "latencies_ms": [1]})
        profile = _profile(website=None)
        num = build_company_number_profile(profile)
        ok = run_agent._try_candidate_site(profile, num, "https://halvorsen.no/", {"requests": 0, "bytes": 0, "latencies_ms": []},
                                           "domain_probe", allow_network_proof=False)
        self.assertFalse(ok)
        web = profile["evidence"].get("website")
        if web:  # recorded as ambiguous at most
            self.assertFalse(web["value"]["identity_assessment"]["publishable"])
