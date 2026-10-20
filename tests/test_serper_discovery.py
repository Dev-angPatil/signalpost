from __future__ import annotations

import io
import json
import logging
import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from norway_company_agent.discovery import (
    BLOCKED_DISCOVERY_HOSTS,
    build_company_search_query,
    choose_search_candidate,
    fetch_serper_search,
    parse_serper_web_results,
    score_search_candidate,
)
from run_agent import enrich_profile


class TestSerperDiscovery(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "evidence": {},
        }

    def test_build_company_search_query_with_municipality(self):
        query = build_company_search_query(self.profile)
        self.assertEqual(query, '"HALVORSEN MASKIN AS" 815011422 TROMSØ')

    def test_build_company_search_query_without_municipality(self):
        p = {"organisation_number": "815011422", "name": "HALVORSEN MASKIN AS", "municipality": ""}
        query = build_company_search_query(p)
        self.assertEqual(query, '"HALVORSEN MASKIN AS" 815011422')

    def test_build_company_search_query_handles_extra_whitespace(self):
        p = {"organisation_number": " 815 011 422 ", "name": "  HALVORSEN   MASKIN  AS  ", "municipality": "  TROMSØ  "}
        query = build_company_search_query(p)
        self.assertEqual(query, '"HALVORSEN MASKIN AS" 815011422 TROMSØ')

    def test_build_company_search_query_missing_name_or_org_raises_error(self):
        with self.assertRaises(ValueError):
            build_company_search_query({"organisation_number": "815011422", "name": ""})
        with self.assertRaises(ValueError):
            build_company_search_query({"organisation_number": "", "name": "HALVORSEN MASKIN AS"})

    def test_parse_serper_web_results_formats_candidates_correctly(self):
        sample_payload = {
            "searchParameters": {"q": "test", "gl": "no", "hl": "no", "num": 10},
            "organic": [
                {
                    "title": "Halvorsen Maskin AS - Maskinentreprenør Tromsø",
                    "link": "https://halvorsenmaskin.no/",
                    "snippet": "Halvorsen Maskin AS er en maskinentreprenør i Tromsø. Org.nr: 815 011 422.",
                    "position": 1,
                },
                {
                    "title": "Proff - Halvorsen Maskin AS",
                    "link": "https://proff.no/selskap/halvorsen-maskin-as/troms%C3%B8/maskinentrepren%C3%B8rer/IF6XG4000CG",
                    "snippet": "Regnskapstall, roller og adresser for Halvorsen Maskin AS.",
                    "position": 2,
                },
                {
                    "title": "Missing link item",
                    "snippet": "No link provided",
                },
            ],
        }
        results = parse_serper_web_results(sample_payload, query='"HALVORSEN MASKIN AS" 815011422 TROMSØ')
        self.assertEqual(len(results), 2)

        first = results[0]
        self.assertEqual(first["url"], "https://halvorsenmaskin.no/")
        self.assertEqual(first["title"], "Halvorsen Maskin AS - Maskinentreprenør Tromsø")
        self.assertEqual(first["snippet"], "Halvorsen Maskin AS er en maskinentreprenør i Tromsø. Org.nr: 815 011 422.")
        self.assertEqual(first["rank"], 1)
        self.assertEqual(first["provider"], "serper_search_api")
        self.assertEqual(first["query"], '"HALVORSEN MASKIN AS" 815011422 TROMSØ')

        second = results[1]
        self.assertEqual(second["url"], "https://proff.no/selskap/halvorsen-maskin-as/troms%C3%B8/maskinentrepren%C3%B8rer/IF6XG4000CG")
        self.assertEqual(second["rank"], 2)
        self.assertEqual(second["provider"], "serper_search_api")

    def test_parse_serper_web_results_handles_empty_or_malformed_payload(self):
        self.assertEqual(parse_serper_web_results({}, query="test"), [])
        self.assertEqual(parse_serper_web_results({"organic": None}, query="test"), [])
        self.assertEqual(parse_serper_web_results({"organic": "not-a-list"}, query="test"), [])

    def test_parse_serper_web_results_uses_fallback_rank_for_invalid_position(self):
        payload = {
            "organic": [
                {"link": "https://halvorsenmaskin.no/", "position": "not-a-number"},
                {"link": "https://halvorsenmaskin.no/om-oss", "position": None},
            ]
        }
        results = parse_serper_web_results(payload, query="company")
        self.assertEqual([result["rank"] for result in results], [1, 2])

    @patch("urllib.request.urlopen")
    def test_fetch_serper_search_request_headers_and_payload(self, mock_urlopen):
        raw_response = json.dumps({
            "organic": [
                {
                    "title": "Halvorsen Maskin AS",
                    "link": "https://halvorsenmaskin.no/",
                    "snippet": "Maskinentreprenør 815011422",
                    "position": 1,
                }
            ]
        }).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = raw_response
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        query = '"HALVORSEN MASKIN AS" 815011422 TROMSØ'
        api_key = "test-secret-key-12345"
        results, metric = fetch_serper_search(query, api_key=api_key, timeout=7.5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://halvorsenmaskin.no/")
        self.assertEqual(metric["requests"], 1)
        self.assertEqual(metric["bytes"], len(raw_response))
        self.assertEqual(metric["third_party_cost_usd"], 0.001)
        self.assertGreaterEqual(len(metric["latencies_ms"]), 1)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://google.serper.dev/search")
        self.assertEqual(req.headers.get("X-api-key"), api_key)
        self.assertEqual(req.headers.get("Content-type"), "application/json")
        sent_body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(sent_body, {"q": query, "gl": "no", "hl": "no", "num": 10})

    @patch("urllib.request.urlopen")
    def test_fetch_serper_search_handles_http_errors_cleanly(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://google.serper.dev/search",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b'{"message": "Rate limit exceeded"}'),
        )
        results, metric = fetch_serper_search("query", api_key="test-key")
        self.assertEqual(results, [])
        self.assertEqual(metric, {"requests": 1, "bytes": 0, "latencies_ms": [0], "third_party_cost_usd": 0.0})

    @patch("urllib.request.urlopen")
    def test_fetch_serper_search_handles_timeout_cleanly(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("Connection timed out")
        results, metric = fetch_serper_search("query", api_key="test-key")
        self.assertEqual(results, [])
        self.assertEqual(metric, {"requests": 1, "bytes": 0, "latencies_ms": [0], "third_party_cost_usd": 0.0})

    @patch("urllib.request.urlopen")
    def test_fetch_serper_search_handles_malformed_json_cleanly(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html>502 Bad Gateway</html>"
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        results, metric = fetch_serper_search("query", api_key="test-key")
        self.assertEqual(results, [])
        self.assertEqual(metric, {"requests": 1, "bytes": 0, "latencies_ms": [0], "third_party_cost_usd": 0.0})

    def test_fetch_serper_search_returns_zero_requests_when_no_api_key(self):
        results, metric = fetch_serper_search("query", api_key="")
        self.assertEqual(results, [])
        self.assertEqual(metric, {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0})

    @patch("urllib.request.urlopen")
    def test_api_key_security_never_exposed_in_urls_or_error_logs(self, mock_urlopen):
        secret_api_key = "super_secret_serper_token_xyz987"
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://google.serper.dev/search",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"message": "Invalid API key"}'),
        )

        with self.assertLogs(level=logging.DEBUG) as captured_logs:
            logging.getLogger().info("Starting test request")
            results, metric = fetch_serper_search("test query", api_key=secret_api_key)
            logging.getLogger().info("Finished test request")

        req = mock_urlopen.call_args[0][0]
        # Never exposed in full_url query parameters
        self.assertNotIn(secret_api_key, req.full_url)
        # Never exposed in logged strings
        for log_msg in captured_logs.output:
            self.assertNotIn(secret_api_key, log_msg)

    def test_candidate_scoring_rejects_aggregators(self):
        aggregators = [
            "https://proff.no/selskap/halvorsen-maskin-as/troms%C3%B8/IF6XG4000CG",
            "https://www.1881.no/maskinentreprenoer/maskinentreprenoer-troms-og-finnmark/maskinentreprenoer-tromsoe/halvorsen-maskin-as_101654315S1/",
            "https://www.gulesider.no/halvorsen+maskin+as+troms%C3%B8/66531988/bedrift",
            "https://purehelp.no/m/company/details/halvorsenmaskinas/815011422",
            "https://linkedin.com/company/halvorsen-maskin-as",
            "https://facebook.com/halvorsenmaskin",
        ]
        for url in aggregators:
            with self.subTest(url=url):
                res = {
                    "url": url,
                    "title": "HALVORSEN MASKIN AS 815011422",
                    "snippet": "Organisasjonsnummer 815011422 Halvorsen Maskin AS Tromsø",
                    "rank": 1,
                    "provider": "serper_search_api",
                }
                assessment = score_search_candidate(self.profile, res)
                self.assertEqual(assessment["status"], "rejected")
                self.assertEqual(assessment["score"], 0.0)
                self.assertFalse(assessment["publishable_candidate"])

    def test_candidate_scoring_accepts_genuine_company_domain(self):
        res = {
            "url": "https://halvorsenmaskin.no/",
            "title": "Halvorsen Maskin AS - Maskinentreprenør i Tromsø",
            "snippet": "Halvorsen Maskin AS tilbyr maskinentreprenørtjenester i Tromsø. Org nr 815011422.",
            "rank": 1,
            "provider": "serper_search_api",
            "query": '"HALVORSEN MASKIN AS" 815011422 TROMSØ',
        }
        assessment = score_search_candidate(self.profile, res)
        self.assertEqual(assessment["status"], "accepted_for_crawl")
        self.assertGreaterEqual(assessment["score"], 0.75)
        self.assertTrue(assessment["publishable_candidate"])
        self.assertEqual(assessment["url"], "https://halvorsenmaskin.no/")

    def test_candidate_scoring_holds_in_review_if_name_not_in_host(self):
        # Result matches org number and all title tokens, but domain is a third-party portal without company name
        res = {
            "url": "https://tromso-nyheter.no/",
            "title": "HALVORSEN MASKIN AS - Tromsø",
            "snippet": "Orgnr 815011422 Halvorsen Maskin AS",
            "rank": 1,
            "provider": "serper_search_api",
        }
        assessment = score_search_candidate(self.profile, res)
        # Must not be accepted for crawl because legal name is not in hostname
        self.assertFalse(assessment["publishable_candidate"])
        self.assertEqual(assessment["status"], "review")

    def test_candidate_scoring_does_not_treat_short_name_as_hostname_prefix(self):
        profile = {
            "organisation_number": "999999999",
            "name": "CO AS",
            "municipality": "OSLO",
        }
        result = {
            "url": "https://company.no/",
            "title": "CO AS",
            "snippet": "Organisasjonsnummer 999999999",
            "rank": 1,
            "provider": "serper_search_api",
        }
        assessment = score_search_candidate(profile, result)
        self.assertFalse(assessment["publishable_candidate"])
        self.assertNotIn("normalized legal name appears in candidate hostname", assessment["reasons"])

    def test_drift_1_as_rejects_lokalebedrifter_aggregator(self):
        drift_profile = {
            "organisation_number": "985721548",
            "name": "DRIFT 1 AS",
            "municipality": "OSLO",
        }
        res = {
            "url": "https://lokalebedrifter.no/bedrift/985721548/drift-1-as",
            "title": "DRIFT 1 AS - Oslo",
            "snippet": "Organisasjonsnummer 985 721 548",
            "rank": 1,
            "provider": "serper_search_api",
            "query": '"DRIFT 1 AS" 985721548 OSLO',
        }
        assessment = score_search_candidate(drift_profile, res)
        self.assertFalse(assessment["publishable_candidate"])
        self.assertEqual(assessment["status"], "rejected")
        self.assertEqual(assessment["score"], 0.0)
        self.assertEqual(assessment["rank"], 1)
        self.assertEqual(assessment["provider"], "serper_search_api")
        self.assertEqual(assessment["query"], '"DRIFT 1 AS" 985721548 OSLO')

    def test_directory_subpath_urls_rejected(self):
        subpaths = [
            "/bedrift/985721548/drift-1-as",
            "/selskap/halvorsen-maskin-as/tromso/123",
            "/firma/halvorsen-maskin",
            "/companies/norway/oslo/drift",
            "/oppslag/drift-1",
            "/enheter/815011422",
            "/bedrifter/tromso/halvorsen",
            "/company/halvorsen-maskin",
        ]
        for subpath in subpaths:
            with self.subTest(subpath=subpath):
                res = {
                    "url": f"https://arbitrary-unlisted-portal.no{subpath}",
                    "title": "HALVORSEN MASKIN AS",
                    "snippet": "Organisasjonsnummer 815011422",
                    "rank": 1,
                    "provider": "serper_search_api",
                    "query": "query",
                }
                assessment = score_search_candidate(self.profile, res)
                self.assertFalse(assessment["publishable_candidate"])
                self.assertEqual(assessment["status"], "rejected")
                self.assertEqual(assessment["score"], 0.0)
                self.assertEqual(assessment["rank"], 1)
                self.assertEqual(assessment["provider"], "serper_search_api")
                self.assertEqual(assessment["query"], "query")

    def test_all_expanded_blocked_discovery_hosts(self):
        expanded_hosts = [
            "lokalebedrifter.no", "byndle.no", "firmabasen.no", "kredittsjekk.no", "nabonytt.no",
            "falio.no", "generate.no", "kontakte.no", "haandverkerportalen.no", "graveoppdrag.no",
            "graveplassen.no", "virksomhet.brreg.no", "brreg.no", "proffforvalt.no", "regnskapstall.no",
            "ratsit.no", "dn.no", "e24.no", "finansavisen.no", "infobel.com",
        ]
        for host in expanded_hosts:
            with self.subTest(host=host):
                self.assertIn(host, BLOCKED_DISCOVERY_HOSTS)
                res = {
                    "url": f"https://{host}/",
                    "title": "HALVORSEN MASKIN AS",
                    "snippet": "815011422",
                    "rank": 2,
                    "provider": "serper_search_api",
                    "query": "test query",
                }
                assessment = score_search_candidate(self.profile, res)
                self.assertEqual(assessment["status"], "rejected")
                self.assertFalse(assessment["publishable_candidate"])
                self.assertEqual(assessment["score"], 0.0)
                self.assertEqual(assessment["rank"], 2)
                self.assertEqual(assessment["provider"], "serper_search_api")
                self.assertEqual(assessment["query"], "test query")

    def test_candidate_scoring_schema_uniformity_across_all_branches(self):
        test_cases = [
            # Invalid URL
            {"url": "not-a-url", "title": "Test", "rank": 1, "provider": "serper", "query": "q1"},
            # Blocked host
            {"url": "https://proff.no/", "title": "Test", "rank": 2, "provider": "serper", "query": "q2"},
            # Directory subpath
            {"url": "https://unknown.no/bedrift/123", "title": "Test", "rank": 3, "provider": "serper", "query": "q3"},
            # Genuine accepted
            {
                "url": "https://halvorsenmaskin.no/",
                "title": "Halvorsen Maskin AS - Tromsø",
                "snippet": "Org nr 815011422",
                "rank": 4,
                "provider": "serper",
                "query": "q4",
            },
            # Review status
            {
                "url": "https://tromso-nyheter.no/",
                "title": "HALVORSEN MASKIN AS",
                "snippet": "Org nr 815011422",
                "rank": 5,
                "provider": "serper",
                "query": "q5",
            },
        ]
        required_keys = {"status", "score", "publishable_candidate", "url", "host", "rank", "provider", "query", "reasons", "method"}
        for case in test_cases:
            with self.subTest(url=case["url"]):
                res = score_search_candidate(self.profile, case)
                self.assertEqual(set(res.keys()), required_keys)
                self.assertEqual(res["rank"], case["rank"])
                self.assertEqual(res["provider"], case["provider"])
                self.assertEqual(res["query"], case["query"])

    def test_batch_operations_report_dynamic_third_party_cost(self):
        import sys
        import tempfile
        from pathlib import Path
        import run_agent

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_file = tmp / "input.jsonl"
            output_file = tmp / "envelopes.jsonl"
            report_file = tmp / "report.json"
            profiles_file = tmp / "profiles.jsonl"

            input_file.write_text('{"organisation_number": "815011422"}\n{"organisation_number": "985721548"}\n', encoding="utf-8")

            def mock_anchor(org: str):
                return {
                    "organisation_number": org,
                    "name": f"COMPANY {org}",
                    "legal_form": "AS",
                    "evidence": {},
                }, type("Metric", (), {"bytes_received": 100, "elapsed_ms": 10})()

            def mock_enrich(p, modules):
                p["evidence"] = {}
                metric = {
                    "requests": 2,
                    "bytes": 500,
                    "latencies_ms": [50],
                    "third_party_cost_usd": 0.001,
                }
                return p, metric

            with patch("run_agent.fetch_live_entity_anchor", side_effect=mock_anchor):
                with patch("run_agent.enrich_profile", side_effect=mock_enrich):
                    cmd_args = [
                        "run_agent.py",
                        "--input", str(input_file),
                        "--output", str(output_file),
                        "--profiles-output", str(profiles_file),
                        "--report", str(report_file),
                    ]
                    with patch.object(sys, "argv", cmd_args):
                        run_agent.main()

            self.assertTrue(report_file.is_file())
            report_data = json.loads(report_file.read_text(encoding="utf-8"))
            # 2 companies * 0.001 = 0.002 USD
            self.assertEqual(report_data["operations"]["declared_third_party_cost_usd"], 0.002)

    @patch.dict(os.environ, {"SERPER_API_KEY": "test-placeholder"})
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_website")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_skips_serper_if_website_already_exists_from_brreg(
        self, mock_official, mock_website, mock_serper
    ):
        mock_official.return_value = ({}, [])
        mock_website.return_value = (
            {"status": "available", "value": {"title": "Company Site", "identity_assessment": {"publishable": True, "score": 1.0}}},
            {"requests": 1, "bytes": 1000, "latencies_ms": [50]},
        )
        profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "website": "https://existing-registry-site.no/",
            "evidence": {},
        }
        enriched, metric = enrich_profile(profile, ["website"])

        # Serper must NOT be queried because website was already present from registry
        mock_serper.assert_not_called()
        mock_website.assert_called_once_with("https://existing-registry-site.no/", timeout=5.0)
        self.assertEqual(enriched["website"], "https://existing-registry-site.no/")
        self.assertEqual(metric["third_party_cost_usd"], 0.0)

    @patch.dict(os.environ, {"SERPER_API_KEY": "test-placeholder"})
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_website")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_discovers_website_via_serper_and_promotes_when_publishable(
        self, mock_official, mock_website, mock_serper
    ):
        mock_official.return_value = ({}, [])
        mock_serper.return_value = (
            [
                {
                    "url": "https://halvorsenmaskin.no/",
                    "title": "Halvorsen Maskin AS",
                    "snippet": "Entreprenør 815011422",
                    "rank": 1,
                    "provider": "serper_search_api",
                    "query": '"HALVORSEN MASKIN AS" 815011422 TROMSØ',
                }
            ],
            {"requests": 1, "bytes": 500, "latencies_ms": [120], "third_party_cost_usd": 0.001},
        )
        # Mock crawled website record that passes identity gate
        mock_website.return_value = (
            {
                "field": "website",
                "status": "available",
                "source_type": "registry_linked_company_website",
                "source_url": "https://halvorsenmaskin.no/",
                "value": {
                    "title": "Halvorsen Maskin AS",
                    "main_text_excerpt": "Halvorsen Maskin AS org.nr 815011422",
                    "identity_text_excerpt": "Halvorsen Maskin AS 815011422",
                },
            },
            {"requests": 1, "bytes": 2048, "latencies_ms": [80]},
        )

        profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "website": None,
            "evidence": {},
        }
        enriched, metric = enrich_profile(profile, ["website"])

        mock_serper.assert_called_once()
        self.assertEqual(enriched["website"], "https://halvorsenmaskin.no/")
        self.assertIn("website", enriched["evidence"])
        self.assertEqual(enriched["evidence"]["website"]["value"]["identity_assessment"]["publishable"], True)
        self.assertEqual(metric["requests"], 2)  # 1 serper + 1 website fetch
        self.assertEqual(metric["third_party_cost_usd"], 0.001)

    @patch.dict(os.environ, {"SERPER_API_KEY": "test-placeholder"})
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_website")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_quarantines_website_when_identity_gate_fails(
        self, mock_official, mock_website, mock_serper
    ):
        mock_official.return_value = ({}, [])
        mock_serper.return_value = (
            [
                {
                    "url": "https://halvorsenmaskin.no/",
                    "title": "Halvorsen Maskin AS",
                    "snippet": "815011422",
                    "rank": 1,
                    "provider": "serper_search_api",
                    "query": '"HALVORSEN MASKIN AS" 815011422 TROMSØ',
                }
            ],
            {"requests": 1, "bytes": 500, "latencies_ms": [120], "third_party_cost_usd": 0.001},
        )
        # Mock crawled website whose body text doesn't corroborate the legal entity
        mock_website.return_value = (
            {
                "field": "website",
                "status": "available",
                "source_type": "registry_linked_company_website",
                "source_url": "https://halvorsenmaskin.no/",
                "value": {
                    "title": "Parked Domain",
                    "main_text_excerpt": "Domain parked by hoster",
                    "identity_text_excerpt": "",
                },
            },
            {"requests": 1, "bytes": 1024, "latencies_ms": [60]},
        )

        profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "website": None,
            "evidence": {},
        }
        enriched, metric = enrich_profile(profile, ["website"])

        mock_serper.assert_called_once()
        # Must NOT be promoted to official profile website
        self.assertIsNone(enriched.get("website"))
        self.assertIn("website", enriched["evidence"])
        self.assertEqual(enriched["evidence"]["website"]["value"]["identity_assessment"]["publishable"], False)
        self.assertEqual(metric["third_party_cost_usd"], 0.001)

    @patch.dict(os.environ, {"SERPER_API_KEY": "test-placeholder"})
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_abstains_when_serper_returns_no_candidates(
        self, mock_official, mock_serper
    ):
        mock_official.return_value = ({}, [])
        mock_serper.return_value = ([], {"requests": 1, "bytes": 0, "latencies_ms": [100], "third_party_cost_usd": 0.001})

        profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "website": None,
            "evidence": {},
        }
        enriched, metric = enrich_profile(profile, ["website"])

        mock_serper.assert_called_once()
        self.assertIsNone(enriched.get("website"))
        self.assertEqual(enriched["evidence"]["website"]["status"], "not_found")
        self.assertEqual(metric["third_party_cost_usd"], 0.001)

    @patch.dict(os.environ, {}, clear=True)
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_disables_optional_discovery_without_serper_key(
        self, mock_official, mock_serper
    ):
        mock_official.return_value = ({}, [])
        profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "website": None,
            "evidence": {},
        }

        enriched, _ = enrich_profile(profile, ["website"])

        mock_serper.assert_not_called()
        self.assertEqual(enriched["evidence"]["website"]["status"], "not_found")

    @patch.dict(os.environ, {}, clear=True)
    @patch("run_agent.fetch_website")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_does_not_count_live_anchor_twice(
        self, mock_official, mock_website
    ):
        mock_official.return_value = ({}, [])
        mock_website.return_value = (
            {"status": "not_found", "value": {}},
            {"requests": 0, "bytes": 0, "latencies_ms": []},
        )
        profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "website": None,
            "evidence": {},
            "_live_anchor": ["registry"],
        }

        _, metric = enrich_profile(profile, ["website"])

        self.assertEqual(metric["requests"], 0)

    @patch.dict(os.environ, {}, clear=True)
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_does_not_fetch_live_registry_anchor_twice(self, mock_official):
        mock_official.return_value = ({}, [])
        profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "website": None,
            "evidence": {},
            "_live_anchor": ["registry"],
        }

        enrich_profile(profile, ["registry_live"])

        mock_official.assert_called_once_with("815011422", set())

    @patch("run_agent.enrich_profile", side_effect=RuntimeError("enrichment exploded"))
    @patch("run_agent.fetch_live_entity_anchor")
    def test_run_agent_accounts_anchor_request_when_enrichment_is_shielded(
        self, mock_anchor, mock_enrich
    ):
        import sys
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_file = tmp / "input.jsonl"
            output_file = tmp / "envelopes.jsonl"
            report_file = tmp / "report.json"
            profiles_file = tmp / "profiles.jsonl"
            input_file.write_text('{"organisation_number": "815011422"}\n', encoding="utf-8")
            mock_anchor.return_value = (
                {
                    "organisation_number": "815011422",
                    "name": "HALVORSEN MASKIN AS",
                    "legal_form": "AS",
                    "evidence": {},
                },
                type("Metric", (), {"bytes_received": 100, "elapsed_ms": 10})(),
            )
            cmd_args = [
                "run_agent.py", "--input", str(input_file),
                "--output", str(output_file), "--profiles-output", str(profiles_file),
                "--report", str(report_file), "--modules", "registry",
            ]
            with patch.object(sys, "argv", cmd_args):
                __import__("run_agent").main()

            report_data = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(report_data["operations"]["total_requests"], 1)
            self.assertEqual(report_data["terminal_envelopes_emitted"], 1)


if __name__ == "__main__":
    unittest.main()
