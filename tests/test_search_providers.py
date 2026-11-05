from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from norway_company_agent.discovery import (
    fetch_google_search,
    parse_google_web_results,
    resolve_search_provider,
    execute_web_search,
)
from run_agent import enrich_profile


class TestSearchProviders(unittest.TestCase):
    def test_parse_google_web_results(self):
        payload = {
            "items": [
                {
                    "title": "Acme Solutions AS - Offisiell nettside",
                    "link": "https://www.acmesolutions.no",
                    "snippet": "Velkommen til Acme Solutions AS. Org.nr 999888777 i Oslo.",
                },
                {
                    "title": "Acme on Facebook",
                    "link": "https://www.facebook.com/acme",
                    "snippet": "Offisiell side",
                },
                {
                    # Missing link - should be skipped
                    "title": "Invalid item",
                },
            ]
        }
        parsed = parse_google_web_results(payload, query='"Acme Solutions AS" Oslo')
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["url"], "https://www.acmesolutions.no")
        self.assertEqual(parsed[0]["title"], "Acme Solutions AS - Offisiell nettside")
        self.assertEqual(parsed[0]["snippet"], "Velkommen til Acme Solutions AS. Org.nr 999888777 i Oslo.")
        self.assertEqual(parsed[0]["rank"], 1)
        self.assertEqual(parsed[0]["provider"], "google_custom_search_api")
        self.assertEqual(parsed[0]["query"], '"Acme Solutions AS" Oslo')

        self.assertEqual(parsed[1]["rank"], 2)
        self.assertEqual(parsed[1]["url"], "https://www.facebook.com/acme")

    def test_fetch_google_search_empty_key(self):
        results, metric = fetch_google_search("query", "")
        self.assertEqual(results, [])
        self.assertEqual(metric["requests"], 0)
        self.assertEqual(metric["third_party_cost_usd"], 0.0)

    @patch("urllib.request.urlopen")
    def test_fetch_google_search_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "items": [
                {
                    "title": "Acme AS",
                    "link": "https://acme.no",
                    "snippet": "Acme AS org 123456789",
                }
            ]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        results, metric = fetch_google_search(
            "Acme",
            api_key="test-google-key",
            cx="test-cx-id",
            cost_per_request_usd=0.005,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://acme.no")
        self.assertEqual(metric["requests"], 1)
        self.assertEqual(metric["third_party_cost_usd"], 0.005)

    @patch("urllib.request.urlopen")
    def test_fetch_google_search_exception_handling(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Google API Rate Limit / Network Error")

        results, metric = fetch_google_search(
            "Acme",
            api_key="test-google-key",
            cost_per_request_usd=0.005,
        )
        self.assertEqual(results, [])
        self.assertEqual(metric["requests"], 1)
        self.assertEqual(metric["third_party_cost_usd"], 0.005)

    def test_resolve_search_provider_empty(self):
        provider, cfg = resolve_search_provider({})
        self.assertIsNone(provider)
        self.assertEqual(cfg, {})

    def test_resolve_search_provider_serper(self):
        env = {"SERPER_API_KEY": "serper-secret"}
        provider, cfg = resolve_search_provider(env)
        self.assertEqual(provider, "serper")
        self.assertEqual(cfg["api_key"], "serper-secret")
        self.assertEqual(cfg["cost_per_request"], 0.001)

    def test_resolve_search_provider_brave_default_cost(self):
        env = {"BRAVE_SEARCH_API_KEY": "brave-secret"}
        provider, cfg = resolve_search_provider(env)
        self.assertEqual(provider, "brave")
        self.assertEqual(cfg["api_key"], "brave-secret")
        self.assertEqual(cfg["cost_per_request"], 0.005)

    def test_resolve_search_provider_brave_custom_cost(self):
        env = {
            "BRAVE_SEARCH_API_KEY": "brave-secret",
            "BRAVE_SEARCH_COST_PER_REQUEST_USD": "0.008",
        }
        provider, cfg = resolve_search_provider(env)
        self.assertEqual(provider, "brave")
        self.assertEqual(cfg["cost_per_request"], 0.008)

    def test_resolve_search_provider_google(self):
        env = {
            "GOOGLE_SEARCH_API_KEY": "google-secret",
            "GOOGLE_SEARCH_ENGINE_ID": "cx-12345",
        }
        provider, cfg = resolve_search_provider(env)
        self.assertEqual(provider, "google")
        self.assertEqual(cfg["api_key"], "google-secret")
        self.assertEqual(cfg["cx"], "cx-12345")
        self.assertEqual(cfg["cost_per_request"], 0.005)

    def test_resolve_search_provider_explicit_override(self):
        env = {
            "WEB_SEARCH_PROVIDER": "google",
            "SERPER_API_KEY": "serper-key",
            "BRAVE_SEARCH_API_KEY": "brave-key",
            "GOOGLE_SEARCH_API_KEY": "google-key",
            "GOOGLE_SEARCH_ENGINE_ID": "cx-99",
        }
        provider, cfg = resolve_search_provider(env)
        self.assertEqual(provider, "google")
        self.assertEqual(cfg["api_key"], "google-key")

        env["WEB_SEARCH_PROVIDER"] = "brave"
        provider, cfg = resolve_search_provider(env)
        self.assertEqual(provider, "brave")
        self.assertEqual(cfg["api_key"], "brave-key")

    @patch("run_agent.fetch_google_search")
    @patch("run_agent.fetch_website")
    @patch("run_agent.fetch_official_modules")
    def test_run_agent_with_google_search_provider(self, mock_official, mock_website, mock_google):
        mock_official.return_value = ({}, [])
        mock_google.return_value = (
            [
                {
                    "url": "https://www.halvorsenmaskin.no/",
                    "title": "Halvorsen Maskin AS",
                    "snippet": "Maskinentreprenør Tromsø org 815011422",
                    "rank": 1,
                    "provider": "google_custom_search_api",
                    "query": "query",
                }
            ],
            {"requests": 1, "bytes": 1200, "latencies_ms": [120], "third_party_cost_usd": 0.005},
        )
        mock_website.return_value = (
            {
                "field": "website",
                "status": "available",
                "source_type": "registry_linked_company_website",
                "source_url": "https://www.halvorsenmaskin.no/",
                "value": {
                    "title": "Halvorsen Maskin AS",
                    "main_text_excerpt": "Maskinentreprenør i Tromsø. Org.nr: 815011422.",
                    "identity_text_excerpt": "Halvorsen Maskin AS 815011422",
                },
            },
            {"requests": 1, "bytes": 2000, "latencies_ms": [80]},
        )

        test_profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "evidence": {},
        }

        with patch.dict(os.environ, {
            "SERPER_API_KEY": "",
            "BRAVE_SEARCH_API_KEY": "",
            "GOOGLE_SEARCH_API_KEY": "test-google-key",
            "GOOGLE_SEARCH_ENGINE_ID": "test-cx",
        }):
            enriched, metric = enrich_profile(test_profile, ["website"])

        self.assertEqual(enriched.get("website"), "https://www.halvorsenmaskin.no/")
        self.assertEqual(metric["third_party_cost_usd"], 0.005)
        mock_google.assert_called_once()


if __name__ == "__main__":
    unittest.main()
