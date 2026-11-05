from __future__ import annotations

import io
import json
import logging
import os
import unittest
import urllib.error
import urllib.parse
from unittest.mock import MagicMock, patch

from norway_company_agent.discovery import (
    BLOCKED_DISCOVERY_HOSTS,
    build_company_search_query,
    choose_search_candidate,
    fetch_brave_search,
    fetch_serper_search,
    get_search_crawl_candidates,
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
    def test_fetch_brave_search_request_headers_url_and_parsed_results(self, mock_urlopen):
        raw_response = json.dumps({
            "web": {
                "results": [{
                    "title": "Halvorsen Maskin AS",
                    "url": "https://halvorsenmaskin.no/",
                    "description": "Maskinentreprenør 815011422",
                }]
            }
        }).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = raw_response
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        query = '"HALVORSEN MASKIN AS" 815011422 TROMSØ'
        api_key = "test-brave-key"
        results, metric = fetch_brave_search(query, api_key=api_key, timeout=7.5)

        self.assertEqual(results, [{
            "url": "https://halvorsenmaskin.no/",
            "title": "Halvorsen Maskin AS",
            "snippet": "Maskinentreprenør 815011422",
            "rank": 1,
            "provider": "brave_search_api",
            "query": query,
        }])
        self.assertEqual(metric["requests"], 1)
        self.assertEqual(metric["bytes"], len(raw_response))
        self.assertEqual(metric["third_party_cost_usd"], 0.0)
        self.assertGreaterEqual(len(metric["latencies_ms"]), 1)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        parsed_url = urllib.parse.urlparse(req.full_url)
        self.assertEqual(
            f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}",
            "https://api.search.brave.com/res/v1/web/search",
        )
        self.assertEqual(urllib.parse.parse_qs(parsed_url.query), {
            "q": [query],
            "count": ["10"],
            "country": ["no"],
            "search_lang": ["nb"],
            "safesearch": ["moderate"],
            "spellcheck": ["0"],
        })
        self.assertEqual(req.headers.get("Accept"), "application/json")
        self.assertEqual(req.headers.get("Accept-encoding"), "identity")
        self.assertEqual(req.headers.get("Cache-control"), "no-cache")
        self.assertEqual(req.headers.get("User-agent"), "builderr-signalpost-poc/0.1 (+https://builderr.ai)")
        self.assertEqual(req.headers.get("X-subscription-token"), api_key)
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 7.5)

    @patch("urllib.request.urlopen")
    def test_fetch_brave_search_handles_failures_with_serper_metrics_shape(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.search.brave.com/res/v1/web/search",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b'{"message": "Rate limit exceeded"}'),
        )

        results, metric = fetch_brave_search("query", api_key="test-key")

        self.assertEqual(results, [])
        self.assertEqual(metric, {"requests": 1, "bytes": 0, "latencies_ms": [0], "third_party_cost_usd": 0.0})

    @patch("urllib.request.urlopen")
    def test_fetch_brave_search_handles_malformed_json_cleanly(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html>502 Bad Gateway</html>"
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        results, metric = fetch_brave_search("query", api_key="test-key")

        self.assertEqual(results, [])
        self.assertEqual(metric, {"requests": 1, "bytes": 0, "latencies_ms": [0], "third_party_cost_usd": 0.0})

    @patch("urllib.request.urlopen")
    def test_fetch_brave_search_handles_network_failures_cleanly(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection failed")

        results, metric = fetch_brave_search("query", api_key="test-key")

        self.assertEqual(results, [])
        self.assertEqual(metric, {"requests": 1, "bytes": 0, "latencies_ms": [0], "third_party_cost_usd": 0.0})

    def test_fetch_brave_search_returns_zero_requests_when_no_api_key(self):
        results, metric = fetch_brave_search("query", api_key="")
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

    @patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "brave-key", "BRAVE_SEARCH_COST_PER_REQUEST_USD": "0.002"}, clear=True)
    @patch("run_agent.fetch_brave_search")
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_website")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_prefers_brave_discovery_when_configured(
        self, mock_official, mock_website, mock_serper, mock_brave
    ):
        mock_official.return_value = ({}, [])
        mock_brave.return_value = ([], {"requests": 1, "bytes": 10, "latencies_ms": [5], "third_party_cost_usd": 0.002})
        mock_website.return_value = (
            {"status": "not_found", "value": {}},
            {"requests": 1, "bytes": 0, "latencies_ms": [5]},
        )
        profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "website": None,
            "evidence": {},
        }

        _, metric = enrich_profile(profile, ["website"])

        self.assertEqual(mock_brave.call_count, 2)
        mock_serper.assert_not_called()
        self.assertEqual(metric["third_party_cost_usd"], 0.004)

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

        self.assertEqual(mock_serper.call_count, 2)
        mock_serper.assert_has_calls([
            unittest.mock.call('"HALVORSEN MASKIN AS" TROMSØ', 'test-placeholder'),
            unittest.mock.call('"HALVORSEN MASKIN AS" 815011422', 'test-placeholder'),
        ])
        self.assertIsNone(enriched.get("website"))
        self.assertEqual(enriched["evidence"]["website"]["status"], "not_found")
        self.assertEqual(metric["third_party_cost_usd"], 0.002)

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


class CompanyNumberProfileAndConflictGateTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "organisation_number": "912345678",
            "name": "NORDIC CLEAN ENERGY AS",
            "municipality": "BERGEN",
            "evidence": {
                "locations": {
                    "value": {
                        "locations": [
                            {"organisation_number": "912345679", "name": "NORDIC CLEAN ENERGY AVD BERGEN", "address": {"postnummer": "5001"}},
                            {"orgnr": "912345680", "name": "NORDIC CLEAN ENERGY AVD OSLO", "address": {"postnummer": "0150"}},
                        ]
                    }
                }
            }
        }

    def test_build_company_number_profile_extracts_all_subunits(self):
        from norway_company_agent.identity import build_company_number_profile
        num_prof = build_company_number_profile(self.profile)
        self.assertEqual(num_prof["primary_org"], "912345678")
        self.assertIn("912345679", num_prof["subunit_orgs"])
        self.assertIn("912345680", num_prof["subunit_orgs"])
        self.assertIn("912345678", num_prof["all_known_orgs"])
        self.assertIn("912345679", num_prof["all_known_orgs"])
        self.assertEqual(num_prof["municipality"], "BERGEN")

    def test_verify_candidate_accepts_subunit_number(self):
        from norway_company_agent.identity import build_company_number_profile, verify_candidate_identity
        num_prof = build_company_number_profile(self.profile)
        # Page displays subunit number 912345679
        candidate_text = "Welcome to Nordic Clean Energy Bergen branch org nr 912 345 679"
        res = verify_candidate_identity("https://linkedin.com/company/nordic-clean-energy", candidate_text, num_prof)
        self.assertTrue(res["publishable"])
        self.assertFalse(res["conflict"])
        self.assertEqual(res["score"], 1.0)
        self.assertIn("912345679", res["matched_orgs"])

    def test_verify_candidate_rejects_conflicting_number_different_company(self):
        from norway_company_agent.identity import build_company_number_profile, verify_candidate_identity
        num_prof = build_company_number_profile(self.profile)
        # Different company with same name has org nr 987654321
        candidate_text = "Nordic Clean Energy AS, Trondheim. Org nr 987 654 321"
        res = verify_candidate_identity("https://facebook.com/nordiccleanenergy", candidate_text, num_prof)
        self.assertFalse(res["publishable"])
        self.assertTrue(res["conflict"])
        self.assertEqual(res["score"], 0.0)
        self.assertIn("conflicting organisation number", res["reason"])

    def test_extract_social_candidates_filters_conflicts_and_keeps_matches(self):
        from norway_company_agent.discovery import extract_social_candidates
        search_results = [
            # Legitimate company LinkedIn mentioning primary org nr
            {
                "url": "https://www.linkedin.com/company/nordic-clean-energy",
                "title": "Nordic Clean Energy | LinkedIn",
                "snippet": "Nordic Clean Energy AS (org 912345678) develops renewable infrastructure in Bergen.",
            },
            # Legitimate company Facebook matching name and town without conflicting numbers
            {
                "url": "https://www.facebook.com/nordiccleanenergyas",
                "title": "Nordic Clean Energy AS - Bergen - Facebook",
                "snippet": "Official page for Nordic Clean Energy AS based in Bergen.",
            },
            # Conflicting company on Instagram with same name but different org number
            {
                "url": "https://www.instagram.com/nordiccleanenergy",
                "title": "Nordic Clean Energy (@nordiccleanenergy) • Instagram",
                "snippet": "Nordic Clean Energy, Org nr 888777666, Stavanger solar panels.",
            },
            # Directory / aggregator link that shouldn't be counted as social
            {
                "url": "https://proff.no/selskap/nordic-clean-energy-as/bergen/energiforsyning/IF2W3Y4016D",
                "title": "Nordic Clean Energy AS - Bergen - Se Regnskap",
                "snippet": "Informasjon om Nordic Clean Energy AS...",
            },
        ]
        candidates = extract_social_candidates(self.profile, search_results)
        urls = [c["url"] for c in candidates]
        self.assertIn("https://www.linkedin.com/company/nordic-clean-energy", urls)
        self.assertIn("https://www.facebook.com/nordiccleanenergyas", urls)
        # Conflicting Instagram rejected!
        self.assertNotIn("https://www.instagram.com/nordiccleanenergy", urls)
        # Aggregator rejected!
        self.assertNotIn("https://proff.no/selskap/nordic-clean-energy-as/bergen/energiforsyning/IF2W3Y4016D", urls)

    def test_revenue_trajectory_up_and_down_narratives(self):
        from norway_company_agent.synthesis import compute_financial_metrics, synthesize_executive_profile
        # Test revenue went up
        records_up = [
            {"year": 2023, "revenue": 15000000, "operating_result": 2000000, "assets": 10000000, "equity": 5000000},
            {"year": 2022, "revenue": 10000000, "operating_result": 1000000, "assets": 8000000, "equity": 4000000},
        ]
        metrics_up = compute_financial_metrics(records_up)
        self.assertEqual(metrics_up["multi_year_revenue_growth_pct"], 50.0)
        self.assertIn("Revenue went up by +50.0%", metrics_up["revenue_trajectory_narrative"])

        # Test revenue went down
        records_down = [
            {"year": 2023, "revenue": 6000000, "operating_result": -500000, "assets": 8000000, "equity": 2000000},
            {"year": 2022, "revenue": 10000000, "operating_result": 1000000, "assets": 9000000, "equity": 4000000},
        ]
        metrics_down = compute_financial_metrics(records_down)
        self.assertEqual(metrics_down["multi_year_revenue_growth_pct"], -40.0)
        self.assertIn("Revenue went down by -40.0%", metrics_down["revenue_trajectory_narrative"])

    def test_extract_domain_from_email(self):
        from norway_company_agent.discovery import extract_domain_from_email
        # Generic domains should be filtered out
        self.assertIsNone(extract_domain_from_email("user@gmail.com"))
        self.assertIsNone(extract_domain_from_email("ola.nordmann@online.no"))
        self.assertIsNone(extract_domain_from_email("board@styrerommet.no"))
        self.assertIsNone(extract_domain_from_email("contact@hotmail.com"))
        self.assertIsNone(extract_domain_from_email(None))
        self.assertIsNone(extract_domain_from_email(""))

        # Legitimate corporate email domains should be extracted
        self.assertEqual(extract_domain_from_email("post@slakteriet-as.no"), "slakteriet-as.no")
        self.assertEqual(extract_domain_from_email("ole.c@svega.no"), "svega.no")
        self.assertEqual(extract_domain_from_email("resepsjon@lysebu.no"), "lysebu.no")

    def test_cost_governor_trips_circuit_breaker_when_budget_exhausted(self):
        from run_agent import CostGovernor
        governor = CostGovernor(max_budget_usd=0.005)
        # First 5 calls spend 0.001 each -> allowed
        for _ in range(5):
            self.assertTrue(governor.try_spend(0.001))
        self.assertEqual(governor.spent_usd, 0.005)

        # 6th call exceeds the budget -> circuit breaker trips and returns False
        self.assertFalse(governor.try_spend(0.001))
        self.assertEqual(governor.spent_usd, 0.005)

        # Resetting governor re-enables spending
        governor.reset(max_budget_usd=1.0)
        self.assertEqual(governor.spent_usd, 0.0)
        self.assertTrue(governor.try_spend(0.001))


class TestSearchCandidateDecoupling(unittest.TestCase):
    def setUp(self):
        from run_agent import _GLOBAL_COST_GOVERNOR
        _GLOBAL_COST_GOVERNOR.reset(max_budget_usd=9.50)
        self.profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "evidence": {},
        }

    def test_get_search_crawl_candidates_returns_candidates_matching_name_tokens_even_if_not_in_host(self):
        results = [
            {
                "url": "https://entreprenor-nord.no",
                "title": "Halvorsen Maskin AS - Velkommen",
                "snippet": "Vi tilbyr graving og maskintjenester i Tromsø.",
                "rank": 1,
            }
        ]
        # In score_search_candidate, candidate is rejected because name is not in host and score < 0.6
        scored = score_search_candidate(self.profile, results[0])
        self.assertFalse(scored["publishable_candidate"])
        self.assertEqual(scored["status"], "rejected")

        # In get_search_crawl_candidates, decoupled selection allows it for in-memory fetching
        candidates = get_search_crawl_candidates(self.profile, results, limit=3)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://entreprenor-nord.no/")
        self.assertIn("halvorsen", candidates[0]["title"].lower())

    def test_get_search_crawl_candidates_filters_blocked_directories_and_aggregators(self):
        results = [
            {"url": "https://proff.no/selskap/halvorsen-maskin-as/123", "title": "Proff - Halvorsen Maskin AS", "rank": 1},
            {"url": "https://gulesider.no/halvorsen+maskin/bedrift", "title": "Gule Sider", "rank": 2},
            {"url": "https://1881.no/halvorsen-maskin", "title": "1881", "rank": 3},
            {"url": "https://northdata.com/Halvorsen+Maskin+AS", "title": "North Data", "rank": 4},
            {"url": "https://halvorsenmaskin.no", "title": "Halvorsen Maskin AS", "rank": 5},
        ]
        candidates = get_search_crawl_candidates(self.profile, results, limit=5)
        # Only the genuine company domain must be accepted
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://halvorsenmaskin.no/")

    def test_get_search_crawl_candidates_filters_directory_path_patterns(self):
        results = [
            {"url": "https://random-aggregator.com/bedrift/halvorsen-maskin", "title": "Halvorsen Maskin", "rank": 1},
            {"url": "https://some-portal.org/selskap/halvorsen", "title": "Halvorsen Maskin", "rank": 2},
            {"url": "https://halvorsenmaskin.no/om-oss", "title": "Om Halvorsen Maskin AS", "rank": 3},
        ]
        candidates = get_search_crawl_candidates(self.profile, results, limit=5)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://halvorsenmaskin.no/om-oss")

    def test_get_search_crawl_candidates_filters_social_platforms(self):
        results = [
            {"url": "https://linkedin.com/company/halvorsen-maskin", "title": "LinkedIn - Halvorsen Maskin", "rank": 1},
            {"url": "https://facebook.com/halvorsenmaskin", "title": "Facebook - Halvorsen Maskin", "rank": 2},
            {"url": "https://halvorsenmaskin.no", "title": "Halvorsen Maskin AS", "rank": 3},
        ]
        candidates = get_search_crawl_candidates(self.profile, results, limit=5)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://halvorsenmaskin.no/")

    def test_get_search_crawl_candidates_respects_limit(self):
        results = [
            {"url": f"https://site{i}.no", "title": f"Halvorsen Maskin {i}", "rank": i}
            for i in range(1, 10)
        ]
        candidates = get_search_crawl_candidates(self.profile, results, limit=3)
        self.assertEqual(len(candidates), 3)

    def test_get_search_crawl_candidates_handles_empty_inputs_gracefully(self):
        self.assertEqual(get_search_crawl_candidates(self.profile, []), [])
        self.assertEqual(get_search_crawl_candidates({}, [{"url": "https://test.no"}]), [])
        self.assertEqual(get_search_crawl_candidates(self.profile, [{"url": "https://halvorsen.no"}], limit=0), [])

    @patch.dict(os.environ, {"SERPER_API_KEY": "test-placeholder"})
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_website")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_inspects_multiple_candidates_until_identity_verification_passes(
        self, mock_official, mock_website, mock_serper
    ):
        mock_official.return_value = ({}, [])
        # Candidate 1 is parked, Candidate 2 is genuine
        mock_serper.return_value = (
            [
                {"url": "https://parked-candidate.no/", "title": "Halvorsen Maskin AS", "rank": 1},
                {"url": "https://genuine-candidate.no/", "title": "Halvorsen Maskin AS", "rank": 2},
            ],
            {"requests": 1, "bytes": 500, "latencies_ms": [100], "third_party_cost_usd": 0.001},
        )

        def mock_fetch_website_side_effect(url, timeout=5.0):
            if "parked" in url:
                return (
                    {
                        "field": "website",
                        "status": "available",
                        "source_type": "registry_linked_company_website",
                        "source_url": url,
                        "value": {
                            "title": "Domain Parked",
                            "main_text_excerpt": "This domain is for sale.",
                            "identity_text_excerpt": "",
                        },
                    },
                    {"requests": 1, "bytes": 500, "latencies_ms": [40]},
                )
            else:
                return (
                    {
                        "field": "website",
                        "status": "available",
                        "source_type": "registry_linked_company_website",
                        "source_url": url,
                        "value": {
                            "title": "Halvorsen Maskin AS",
                            "main_text_excerpt": "Maskinentreprenør i Tromsø. Org.nr: 815011422.",
                            "identity_text_excerpt": "Halvorsen Maskin AS 815011422",
                        },
                    },
                    {"requests": 1, "bytes": 1500, "latencies_ms": [60]},
                )

        mock_website.side_effect = mock_fetch_website_side_effect

        enriched, metric = enrich_profile(self.profile, ["website"])

        # Candidate 1 was inspected & rejected, Candidate 2 was inspected & published!
        self.assertEqual(enriched.get("website"), "https://genuine-candidate.no/")
        self.assertEqual(enriched["evidence"]["website"]["value"]["identity_assessment"]["publishable"], True)
        self.assertEqual(mock_website.call_count, 2)

    @patch.dict(os.environ, {"SERPER_API_KEY": "test-placeholder"})
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_website")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_fallback_search_query_discovers_website_when_primary_returns_empty(
        self, mock_official, mock_website, mock_serper
    ):
        mock_official.return_value = ({}, [])
        # Primary query returns empty, fallback query returns genuine candidate
        mock_serper.side_effect = [
            ([], {"requests": 1, "bytes": 100, "latencies_ms": [50], "third_party_cost_usd": 0.001}),
            (
                [{"url": "https://halvorsenmaskin.no/", "title": "Halvorsen Maskin AS", "rank": 1}],
                {"requests": 1, "bytes": 500, "latencies_ms": [60], "third_party_cost_usd": 0.001},
            ),
        ]
        mock_website.return_value = (
            {
                "field": "website",
                "status": "available",
                "source_type": "registry_linked_company_website",
                "source_url": "https://halvorsenmaskin.no/",
                "value": {
                    "title": "Halvorsen Maskin AS",
                    "main_text_excerpt": "Org.nr 815011422",
                    "identity_text_excerpt": "Halvorsen Maskin AS 815011422",
                },
            },
            {"requests": 1, "bytes": 1000, "latencies_ms": [50]},
        )

        enriched, metric = enrich_profile(self.profile, ["website"])

        self.assertEqual(mock_serper.call_count, 2)
        mock_serper.assert_has_calls([
            unittest.mock.call('"HALVORSEN MASKIN AS" TROMSØ', 'test-placeholder'),
            unittest.mock.call('"HALVORSEN MASKIN AS" 815011422', 'test-placeholder'),
        ])
        self.assertEqual(enriched.get("website"), "https://halvorsenmaskin.no/")
        self.assertEqual(metric["third_party_cost_usd"], 0.002)

    @patch.dict(os.environ, {"SERPER_API_KEY": "test-placeholder"})
    @patch("run_agent.fetch_serper_search")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_skips_fallback_query_when_cost_governor_budget_exhausted(
        self, mock_official, mock_serper
    ):
        from run_agent import _GLOBAL_COST_GOVERNOR
        mock_official.return_value = ({}, [])
        mock_serper.return_value = ([], {"requests": 1, "bytes": 100, "latencies_ms": [50], "third_party_cost_usd": 0.001})

        # Cap budget to exactly 0.001 so primary succeeds but fallback query is barred
        _GLOBAL_COST_GOVERNOR.reset(max_budget_usd=0.001)

        enriched, metric = enrich_profile(self.profile, ["website"])

        # Fallback query must NOT be attempted because budget is exhausted
        mock_serper.assert_called_once()
        self.assertEqual(metric["third_party_cost_usd"], 0.001)
        self.assertIsNone(enriched.get("website"))


if __name__ == "__main__":
    unittest.main()


