from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from norway_company_agent.discovery import (
    generate_candidate_domain_slugs,
    probe_candidate_domains,
)
from run_agent import CostGovernor, _GLOBAL_COST_GOVERNOR, enrich_profile


class TestCandidateDomainSlugGeneration(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "registered_office": "Tromsø",
        }

    def test_generate_candidate_domain_slugs_standard_company(self):
        slugs = generate_candidate_domain_slugs(self.profile)
        self.assertIn("halvorsenmaskin.no", slugs)
        self.assertIn("halvorsen-maskin.no", slugs)
        self.assertIn("halvorsen.no", slugs)
        self.assertIn("halvorsen-maskin-tromso.no", slugs)
        self.assertLessEqual(len(slugs), 4)

    def test_generate_candidate_domain_slugs_single_distinctive_token(self):
        p = {"name": "ADALIA AS", "municipality": "OSLO"}
        slugs = generate_candidate_domain_slugs(p)
        self.assertIn("adalia.no", slugs)
        self.assertIn("adalia-oslo.no", slugs)
        self.assertIn("adalia.com", slugs)
        self.assertLessEqual(len(slugs), 4)

    def test_generate_candidate_domain_slugs_filters_generic_tokens(self):
        p = {"name": "NORDIC CLEAN ENERGY GRUPPEN AS", "municipality": "BERGEN"}
        slugs = generate_candidate_domain_slugs(p)
        self.assertIn("nordiccleanenergy.no", slugs)
        self.assertIn("nordic-clean-energy.no", slugs)
        # "gruppen" and "as" must not be part of the core slug
        self.assertNotIn("nordiccleanenergygruppenas.no", slugs)

    def test_generate_candidate_domain_slugs_norwegian_characters_aa_variant(self):
        p = {"name": "ÅS ELEKTRO AS", "municipality": "ÅS"}
        slugs = generate_candidate_domain_slugs(p)
        # Should generate 'a' and 'aa' variations for 'å'
        has_aa_variant = any("aa" in s for s in slugs)
        has_a_variant = any("as" in s for s in slugs)
        self.assertTrue(has_aa_variant or has_a_variant)

    def test_generate_candidate_domain_slugs_norwegian_characters_o_ae(self):
        p = {"name": "BØ OG VÆRN AS", "municipality": "BØ"}
        slugs = generate_candidate_domain_slugs(p)
        # 'ø' normalized to 'o', 'æ' normalized to 'ae'
        self.assertTrue(any("bo" in s for s in slugs))

    def test_generate_candidate_domain_slugs_empty_name_returns_empty_list(self):
        self.assertEqual(generate_candidate_domain_slugs({"name": ""}), [])
        self.assertEqual(generate_candidate_domain_slugs({}), [])


class TestDomainProbing(unittest.TestCase):
    def test_probe_candidate_domains_empty_input_returns_none(self):
        self.assertIsNone(probe_candidate_domains([]))
        self.assertIsNone(probe_candidate_domains([""]))

    @patch("norway_company_agent.discovery.assert_public_url")
    @patch("urllib.request.build_opener")
    def test_probe_candidate_domains_returns_responding_url_on_head_200(
        self, mock_build_opener, mock_assert_public
    ):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.geturl.return_value = "https://www.halvorsenmaskin.no/"
        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        metrics = {"requests": 0, "latencies_ms": []}
        result = probe_candidate_domains(
            ["halvorsenmaskin.no"],
            timeout=2.0,
            metrics=metrics,
        )

        self.assertEqual(result, "https://www.halvorsenmaskin.no/")
        self.assertEqual(metrics["requests"], 1)
        self.assertEqual(len(metrics["latencies_ms"]), 1)

    @patch("norway_company_agent.discovery.assert_public_url")
    @patch("urllib.request.build_opener")
    def test_probe_candidate_domains_handles_redirect(
        self, mock_build_opener, mock_assert_public
    ):
        mock_resp = MagicMock()
        mock_resp.status = 301
        mock_resp.geturl.return_value = "https://halvorsenmaskin.no/"
        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        result = probe_candidate_domains(["halvorsenmaskin.no"], timeout=2.0)
        self.assertEqual(result, "https://halvorsenmaskin.no/")

    @patch("norway_company_agent.discovery.assert_public_url")
    @patch("urllib.request.build_opener")
    def test_probe_candidate_domains_fallback_to_get_on_405(
        self, mock_build_opener, mock_assert_public
    ):
        # HEAD raises 405 Method Not Allowed, GET succeeds with 200
        err_405 = urllib.error.HTTPError(
            url="https://www.halvorsenmaskin.no",
            code=405,
            msg="Method Not Allowed",
            hdrs={},
            fp=None,
        )
        mock_get_resp = MagicMock()
        mock_get_resp.status = 200
        mock_get_resp.geturl.return_value = "https://www.halvorsenmaskin.no/"

        mock_opener = MagicMock()
        mock_opener.open.side_effect = [
            err_405,
            MagicMock(__enter__=MagicMock(return_value=mock_get_resp)),
        ]
        mock_build_opener.return_value = mock_opener

        result = probe_candidate_domains(["halvorsenmaskin.no"], timeout=2.0)
        self.assertEqual(result, "https://www.halvorsenmaskin.no/")
        self.assertEqual(mock_opener.open.call_count, 2)

    @patch("norway_company_agent.discovery.assert_public_url")
    @patch("urllib.request.build_opener")
    def test_probe_candidate_domains_returns_none_when_all_fail(
        self, mock_build_opener, mock_assert_public
    ):
        mock_opener = MagicMock()
        mock_opener.open.side_effect = urllib.error.URLError("Connection refused")
        mock_build_opener.return_value = mock_opener

        result = probe_candidate_domains(["nonexistent-co-1.no", "nonexistent-co-2.no"], timeout=2.0)
        self.assertIsNone(result)

    @patch("norway_company_agent.discovery.assert_public_url")
    def test_probe_candidate_domains_respects_ssrf_blocking(self, mock_assert_public):
        mock_assert_public.side_effect = ValueError("Private, loopback addresses blocked")
        result = probe_candidate_domains(["internal-intranet.no"], timeout=2.0)
        self.assertIsNone(result)

    @patch("norway_company_agent.discovery.assert_public_url")
    @patch("urllib.request.build_opener")
    def test_probe_candidate_domains_apex_fallback_when_www_does_not_resolve(
        self, mock_build_opener, mock_assert_public
    ):
        # www raises ValueError (NXDOMAIN), apex domain resolves and responds 200
        def assert_public_side_effect(url):
            if "www." in url:
                raise ValueError("Hostname did not resolve")
            return None

        mock_assert_public.side_effect = assert_public_side_effect
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.geturl.return_value = "https://halvorsenmaskin.no/"
        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        result = probe_candidate_domains(["halvorsenmaskin.no"], timeout=2.0)
        self.assertEqual(result, "https://halvorsenmaskin.no/")

    @patch("norway_company_agent.discovery.assert_public_url")
    @patch("urllib.request.build_opener")
    def test_probe_candidate_domains_records_metrics_for_get_fallback(
        self, mock_build_opener, mock_assert_public
    ):
        err_405 = urllib.error.HTTPError(
            url="https://www.halvorsenmaskin.no",
            code=405,
            msg="Method Not Allowed",
            hdrs={},
            fp=None,
        )
        mock_get_resp = MagicMock()
        mock_get_resp.status = 200
        mock_get_resp.geturl.return_value = "https://www.halvorsenmaskin.no/"

        mock_opener = MagicMock()
        mock_opener.open.side_effect = [
            err_405,
            MagicMock(__enter__=MagicMock(return_value=mock_get_resp)),
        ]
        mock_build_opener.return_value = mock_opener

        metrics = {"requests": 0, "latencies_ms": []}
        result = probe_candidate_domains(["halvorsenmaskin.no"], timeout=1.8, metrics=metrics)
        self.assertEqual(result, "https://www.halvorsenmaskin.no/")
        self.assertEqual(metrics["requests"], 2)
        self.assertEqual(len(metrics["latencies_ms"]), 2)
        # Verify timeout was passed to opener
        for call_args in mock_opener.open.call_args_list:
            self.assertEqual(call_args.kwargs.get("timeout"), 1.8)


class TestZeroCostProbingIntegrationInRunAgent(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "organisation_number": "815011422",
            "name": "HALVORSEN MASKIN AS",
            "municipality": "TROMSØ",
            "website": None,
            "evidence": {},
        }

    @patch("run_agent.fetch_official_modules", return_value=({}, []))
    @patch("run_agent.probe_candidate_domains", return_value="https://www.halvorsenmaskin.no/")
    @patch("run_agent.fetch_website")
    def test_enrich_profile_uses_domain_probing_when_search_absent(
        self, mock_website, mock_probe, mock_official
    ):
        mock_website.return_value = (
            {
                "field": "website",
                "status": "available",
                "source_type": "registry_linked_company_website",
                "source_url": "https://www.halvorsenmaskin.no/",
                "value": {
                    "title": "Halvorsen Maskin AS",
                    "main_text_excerpt": "Maskinentreprenør i Tromsø. Org.nr 815 011 422.",
                    "identity_text_excerpt": "Halvorsen Maskin AS 815011422",
                },
            },
            {"requests": 1, "bytes": 1024, "latencies_ms": [50]},
        )

        # Clear search API keys to ensure zero-cost autonomous mode
        with patch.dict("os.environ", {}, clear=True):
            enriched, metric = enrich_profile(self.profile, ["website"])

        mock_probe.assert_called_once()
        self.assertEqual(enriched.get("website"), "https://www.halvorsenmaskin.no/")
        self.assertEqual(enriched["evidence"]["website"]["value"]["identity_assessment"]["publishable"], True)
        self.assertEqual(metric["third_party_cost_usd"], 0.0)

    @patch("run_agent.fetch_official_modules", return_value=({}, []))
    @patch("run_agent.probe_candidate_domains", return_value="https://www.halvorsenmaskin.no/")
    @patch("run_agent.fetch_website")
    def test_enrich_profile_quarantines_probed_domain_if_identity_gate_fails(
        self, mock_website, mock_probe, mock_official
    ):
        # Probed domain is parked placeholder
        mock_website.return_value = (
            {
                "field": "website",
                "status": "available",
                "source_type": "registry_linked_company_website",
                "source_url": "https://www.halvorsenmaskin.no/",
                "value": {
                    "title": "Domain Parked",
                    "main_text_excerpt": "This domain is for sale at Sedo.",
                    "identity_text_excerpt": "",
                },
            },
            {"requests": 1, "bytes": 512, "latencies_ms": [40]},
        )

        with patch.dict("os.environ", {}, clear=True):
            enriched, metric = enrich_profile(self.profile, ["website"])

        mock_probe.assert_called_once()
        # Must NOT be published!
        self.assertIsNone(enriched.get("website"))
        self.assertEqual(metric["third_party_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
