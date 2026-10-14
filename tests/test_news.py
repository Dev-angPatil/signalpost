"""Tests for Google News RSS connector, Norwegian exact title matching, and contract claims."""
from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.news import (
    ALLOWED_PREDECESSORS,
    exact_title_match,
    fetch_company_news_rss,
)
from run_agent import enrich_profile


MOCK_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News RSS Search</title>
    <link>https://news.google.com</link>
    <description>Google News</description>
    <item>
      <title>Equinor ASA inng\xc3\xa5r storavtale i Nordsj\xc3\xb8en - E24</title>
      <link>https://e24.no/equinor-avtale-1</link>
      <source url="https://e24.no">E24</source>
      <pubDate>Mon, 15 Jan 2024 10:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Rekordresultat fra Equinor ASA - Dagens N\xc3\xa6ringsliv</title>
      <link>https://dn.no/equinor-resultat-2</link>
      <source url="https://dn.no">Dagens N\xc3\xa6ringsliv</source>
      <pubDate>Tue, 20 Feb 2024 14:00:00 +0100</pubDate>
    </item>
    <item>
      <!-- Duplicate of the first item to test deduplication -->
      <title>Equinor ASA inng\xc3\xa5r storavtale i Nordsj\xc3\xb8en - E24</title>
      <link>https://e24.no/equinor-avtale-1</link>
      <source url="https://e24.no">E24</source>
      <pubDate>Mon, 15 Jan 2024 10:30:00 GMT</pubDate>
    </item>
    <item>
      <!-- Substring / different company: must NOT match Equinor ASA -->
      <title>Aker BP og V\xc3\xa5r Energi satser - E24</title>
      <link>https://e24.no/aker-bp-1</link>
      <source url="https://e24.no">E24</source>
      <pubDate>Wed, 21 Feb 2024 09:00:00 GMT</pubDate>
    </item>
    <item>
      <!-- Older news item for date sort testing -->
      <title>Ny toppsjef i Equinor ASA - NRK</title>
      <link>https://nrk.no/equinor-sjef-3</link>
      <source url="https://nrk.no">NRK</source>
      <pubDate>Fri, 01 Dec 2023 08:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


class TestExactTitleMatch(unittest.TestCase):
    def test_start_of_title_match(self):
        self.assertTrue(exact_title_match("Equinor ASA", "Equinor ASA øker utbyttet - E24"))
        self.assertTrue(exact_title_match("Aneo AS", "Aneo AS investerer i vindkraft - DN"))

    def test_allowed_predecessors(self):
        for prep in ALLOWED_PREDECESSORS:
            title = f"Stor satsing {prep} Equinor ASA i dag - E24"
            self.assertTrue(
                exact_title_match("Equinor ASA", title),
                f"Failed to match allowed predecessor '{prep}'",
            )

    def test_publisher_suffix_stripping(self):
        # Hyphen
        self.assertTrue(exact_title_match("Equinor ASA", "Equinor ASA får nye konsesjoner - E24"))
        # En-dash
        self.assertTrue(exact_title_match("Equinor ASA", "Equinor ASA får nye konsesjoner – Dagens Næringsliv"))
        # Em-dash
        self.assertTrue(exact_title_match("Equinor ASA", "Equinor ASA får nye konsesjoner — Finansavisen"))

    def test_norwegian_characters_and_casing(self):
        self.assertTrue(exact_title_match("Vår Energi ASA", "Vår Energi ASA satser i Barentshavet - E24"))
        self.assertTrue(exact_title_match("Blåkläder AS", "Nytt lager for Blåkläder AS - E24"))
        self.assertTrue(exact_title_match("equinor asa", "EQUINOR ASA ØKER UTBYTTET - E24"))

    def test_reject_longer_company_name_substring(self):
        # "Aneo AS" should not match "Aneo Roan Vind Holding AS"
        self.assertFalse(exact_title_match("Aneo AS", "Aneo Roan Vind Holding AS inngår avtale - E24"))
        # "Vard AS" should not match "Vard Group AS"
        self.assertFalse(exact_title_match("Vard AS", "Vard Group AS bygger nytt skip - E24"))

    def test_reject_disallowed_predecessor(self):
        # Predecessor "nedgraderer" is not in ALLOWED_PREDECESSORS
        self.assertFalse(exact_title_match("Equinor ASA", "Analytiker nedgraderer Equinor ASA - E24"))
        # Predecessor "slår" is not in ALLOWED_PREDECESSORS
        self.assertFalse(exact_title_match("Equinor ASA", "Konkurrenten slår Equinor ASA - E24"))

    def test_empty_and_invalid_inputs(self):
        self.assertFalse(exact_title_match("", "Equinor ASA - E24"))
        self.assertFalse(exact_title_match("Equinor ASA", ""))
        self.assertFalse(exact_title_match("", ""))
        self.assertFalse(exact_title_match("Equinor ASA", "Kort"))


class TestFetchCompanyNewsRSS(unittest.TestCase):
    def test_fetch_rss_parsing_and_sorting(self):
        def mock_fetcher(url, headers, timeout):
            return MOCK_RSS_XML

        record, metrics = fetch_company_news_rss(
            company_name="Equinor ASA",
            orgnr="923609016",
            years=2,
            http_fetcher=mock_fetcher,
        )

        self.assertEqual(record["status"], "available")
        self.assertEqual(record["source_class"], "public_news")
        self.assertEqual(record["source_row_key"], "923609016")
        expected_sha = hashlib.sha256(MOCK_RSS_XML).hexdigest()
        self.assertEqual(record["content_sha256"], expected_sha)
        self.assertEqual(len(record["content_sha256"]), 64)

        items = record["value"]["items"]
        # Out of 5 items in XML:
        # 1 matches (Jan 15 2024)
        # 2 matches (Feb 20 2024)
        # 3 is duplicate of 1 -> filtered
        # 4 is Aker BP -> filtered
        # 5 matches (Dec 01 2023)
        self.assertEqual(len(items), 3)

        # Strictly descending sorting by date
        # 1st item: Feb 20, 2024
        self.assertEqual(items[0]["date"], "2024-02-20")
        self.assertIn("Rekordresultat", items[0]["title"])
        self.assertEqual(items[0]["url"], "https://dn.no/equinor-resultat-2")
        self.assertEqual(items[0]["publisher"], "Dagens Næringsliv")

        # 2nd item: Jan 15, 2024
        self.assertEqual(items[1]["date"], "2024-01-15")
        self.assertIn("inngår storavtale", items[1]["title"])

        # 3rd item: Dec 01, 2023
        self.assertEqual(items[2]["date"], "2023-12-01")
        self.assertIn("Ny toppsjef", items[2]["title"])

        # Latest matches items[0]
        self.assertEqual(record["value"]["latest"], items[0])

        # Metrics check
        self.assertEqual(metrics["requests"], 1)
        self.assertEqual(metrics["bytes"], len(MOCK_RSS_XML))
        self.assertEqual(metrics["third_party_cost_usd"], 0.0)
        self.assertGreaterEqual(metrics["latencies_ms"][0], 0)

    def test_empty_company_name(self):
        record, metrics = fetch_company_news_rss(company_name="", orgnr="123456789")
        self.assertEqual(record["status"], "not_available")
        self.assertEqual(record["value"]["items"], [])
        self.assertIsNone(record["value"]["latest"])
        self.assertEqual(metrics["requests"], 0)

    def test_no_matching_items_returns_not_available(self):
        empty_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>Google News</title>
            <item>
              <title>Helt annet selskap vinner anbud - E24</title>
              <link>https://e24.no/annet</link>
              <pubDate>Mon, 15 Jan 2024 10:30:00 GMT</pubDate>
            </item>
          </channel>
        </rss>"""
        record, metrics = fetch_company_news_rss(
            company_name="Ukjent Firma AS",
            orgnr="999999999",
            http_fetcher=lambda u, h, t: empty_rss,
        )
        self.assertEqual(record["status"], "not_available")
        self.assertEqual(record["value"]["items"], [])
        self.assertIsNone(record["value"]["latest"])
        self.assertEqual(metrics["requests"], 1)

    def test_http_error_handling(self):
        def failing_fetcher(url, headers, timeout):
            raise ConnectionError("Network unreachable")

        record, metrics = fetch_company_news_rss(
            company_name="Equinor ASA",
            orgnr="923609016",
            http_fetcher=failing_fetcher,
        )
        self.assertEqual(record["status"], "failed")
        self.assertIn("ConnectionError", record.get("error", ""))
        self.assertEqual(record["value"]["items"], [])
        self.assertIsNone(record["value"]["latest"])
        self.assertEqual(metrics["requests"], 1)

    def test_xml_parse_error_handling(self):
        record, metrics = fetch_company_news_rss(
            company_name="Equinor ASA",
            orgnr="923609016",
            http_fetcher=lambda u, h, t: b"Not XML content",
        )
        self.assertEqual(record["status"], "failed")
        self.assertTrue("ParseError" in record.get("error", "") or "syntax error" in record.get("error", ""))
        self.assertEqual(metrics["requests"], 1)

    def test_fetcher_response_object_variations(self):
        class MockResponse:
            def __init__(self, content):
                self.content = content

        record, metrics = fetch_company_news_rss(
            company_name="Equinor ASA",
            http_fetcher=lambda u, h, t: MockResponse(MOCK_RSS_XML),
        )
        self.assertEqual(record["status"], "available")
        self.assertEqual(len(record["value"]["items"]), 3)


class TestContractOutputEnvelopeNewsClaims(unittest.TestCase):
    def test_envelope_with_available_news(self):
        profile = {
            "organisation_number": "923609016",
            "name": "EQUINOR ASA",
            "evidence": {
                "registry": {
                    "id": "ev-registry",
                    "status": "available",
                    "source_class": "official_registry",
                    "source_url": "https://data.brreg.no",
                    "value": {"navn": "EQUINOR ASA"},
                },
                "news": {
                    "field": "news",
                    "status": "available",
                    "source_type": "public_news",
                    "source_class": "public_news",
                    "source_url": "https://news.google.com/rss/search?q=%22EQUINOR%20ASA%22",
                    "content_sha256": "a" * 64,
                    "retrieved_at": "2026-10-14T10:00:00Z",
                    "value": {
                        "items": [
                            {
                                "title": "Equinor ASA øker utbyttet",
                                "url": "https://e24.no/equinor-1",
                                "date": "2024-02-20",
                                "publisher": "E24",
                            }
                        ],
                        "latest": {
                            "title": "Equinor ASA øker utbyttet",
                            "url": "https://e24.no/equinor-1",
                            "date": "2024-02-20",
                            "publisher": "E24",
                        },
                    },
                },
            },
        }

        envelope = build_output_envelope(
            profile,
            run_id="run-test-news-1",
            started_at="2026-10-14T10:00:00Z",
            completed_at="2026-10-14T10:00:05Z",
        )

        ev_map = {ev["id"]: ev for ev in envelope["evidence"]}
        self.assertIn("ev-news", ev_map)
        ev_news = ev_map["ev-news"]
        self.assertEqual(ev_news["source_class"], "public_news")
        self.assertEqual(ev_news["content_sha256"], "a" * 64)
        self.assertEqual(len(ev_news["content_sha256"]), 64)
        self.assertEqual(ev_news["claim_span"], "Equinor ASA øker utbyttet")

        claims_map = {c["field"]: c for c in envelope["claims"]}
        self.assertIn("latest_news_title", claims_map)
        self.assertIn("latest_news_date", claims_map)
        self.assertIn("latest_news_url", claims_map)

        c_title = claims_map["latest_news_title"]
        self.assertEqual(c_title["value"], "Equinor ASA øker utbyttet")
        self.assertEqual(c_title["availability"], "available")
        self.assertEqual(c_title["confidence"], 0.99)
        self.assertEqual(c_title["evidence_ids"], ["ev-news"])

        c_date = claims_map["latest_news_date"]
        self.assertEqual(c_date["value"], "2024-02-20")
        self.assertEqual(c_date["availability"], "available")
        self.assertEqual(c_date["confidence"], 0.99)
        self.assertEqual(c_date["evidence_ids"], ["ev-news"])

        c_url = claims_map["latest_news_url"]
        self.assertEqual(c_url["value"], "https://e24.no/equinor-1")
        self.assertEqual(c_url["availability"], "available")
        self.assertEqual(c_url["confidence"], 0.99)
        self.assertEqual(c_url["evidence_ids"], ["ev-news"])

    def test_envelope_with_empty_or_failed_news(self):
        # Empty items in news evidence
        profile_empty = {
            "organisation_number": "999999999",
            "name": "NO NEWS AS",
            "evidence": {
                "registry": {
                    "id": "ev-registry",
                    "status": "available",
                    "source_class": "official_registry",
                    "value": {"navn": "NO NEWS AS"},
                },
                "news": {
                    "status": "not_available",
                    "content_sha256": "b" * 64,
                    "value": {"items": [], "latest": None},
                },
            },
        }

        envelope_empty = build_output_envelope(
            profile_empty,
            run_id="run-test-news-2",
            started_at="2026-10-14T10:00:00Z",
            completed_at="2026-10-14T10:00:05Z",
        )

        ev_ids = [ev["id"] for ev in envelope_empty["evidence"]]
        self.assertNotIn("ev-news", ev_ids)

        claims_map = {c["field"]: c for c in envelope_empty["claims"]}
        for field in ("latest_news_title", "latest_news_date", "latest_news_url"):
            claim = claims_map[field]
            self.assertIsNone(claim["value"])
            self.assertEqual(claim["availability"], "not_available")
            self.assertEqual(claim["confidence"], 1.0)
            self.assertEqual(claim["evidence_ids"], [])

    def test_envelope_with_absent_news_record(self):
        profile = {
            "organisation_number": "888888888",
            "name": "ABSENT NEWS AS",
            "evidence": {
                "registry": {
                    "id": "ev-registry",
                    "status": "available",
                    "source_class": "official_registry",
                    "value": {"navn": "ABSENT NEWS AS"},
                },
            },
        }

        envelope = build_output_envelope(
            profile,
            run_id="run-test-news-3",
            started_at="2026-10-14T10:00:00Z",
            completed_at="2026-10-14T10:00:05Z",
        )

        claims_map = {c["field"]: c for c in envelope["claims"]}
        for field in ("latest_news_title", "latest_news_date", "latest_news_url"):
            claim = claims_map[field]
            self.assertIsNone(claim["value"])
            self.assertEqual(claim["availability"], "not_available")
            self.assertEqual(claim["confidence"], 1.0)
            self.assertEqual(claim["evidence_ids"], [])


class TestRunAgentNewsEnrichment(unittest.TestCase):
    def test_enrich_profile_invokes_news_module(self):
        profile = {
            "organisation_number": "923609016",
            "name": "EQUINOR ASA",
            "evidence": {},
        }

        mock_record = {
            "field": "news",
            "status": "available",
            "source_type": "public_news",
            "source_class": "public_news",
            "source_url": "https://news.google.com/rss",
            "retrieved_at": "2026-10-14T10:00:00Z",
            "content_sha256": "c" * 64,
            "value": {
                "items": [{"title": "Headline", "url": "https://e24.no", "date": "2024-01-01"}],
                "latest": {"title": "Headline", "url": "https://e24.no", "date": "2024-01-01"},
            },
        }
        mock_metric = {"requests": 1, "bytes": 500, "latencies_ms": [50], "third_party_cost_usd": 0.0}

        with patch("run_agent.fetch_company_news_rss", return_value=(mock_record, mock_metric)) as mock_fn:
            enriched, metrics = enrich_profile(profile, ["news"])

            mock_fn.assert_called_once_with("EQUINOR ASA", "923609016")
            self.assertEqual(enriched["evidence"]["news"], mock_record)
            self.assertEqual(enriched["news"], mock_record["value"])
            self.assertEqual(metrics["requests"], 1)
            self.assertEqual(metrics["bytes"], 500)

    def test_enrich_profile_skips_news_when_not_requested(self):
        profile = {
            "organisation_number": "923609016",
            "name": "EQUINOR ASA",
            "evidence": {},
        }
        with patch("run_agent.fetch_company_news_rss") as mock_fn:
            enriched, metrics = enrich_profile(profile, ["website"])
            mock_fn.assert_not_called()
            self.assertNotIn("news", enriched.get("evidence", {}))


class TestNewsAdversarialAndEdgeCases(unittest.TestCase):
    def test_query_url_encoding(self):
        recorded_url = []

        def capturing_fetcher(url, headers, timeout):
            recorded_url.append(url)
            return b"<rss><channel></channel></rss>"

        fetch_company_news_rss("A & B Gruppen AS", years=3, http_fetcher=capturing_fetcher)
        self.assertTrue(recorded_url)
        url = recorded_url[0]
        self.assertIn("q=%22A%20%26%20B%20Gruppen%20AS%22%20when%3A3y", url)
        self.assertIn("hl=no&gl=NO&ceid=NO:no", url)

    def test_multi_token_company_name_matching(self):
        name = "Kongsberg Gruppen ASA"
        self.assertTrue(exact_title_match(name, "Kongsberg Gruppen ASA inngår forsvarsavtale - E24"))
        self.assertTrue(exact_title_match(name, "Milliardkontrakt for Kongsberg Gruppen ASA i USA - DN"))
        self.assertTrue(exact_title_match(name, "Samarbeid med Kongsberg Gruppen ASA"))
        # Partial match of some tokens should fail
        self.assertFalse(exact_title_match(name, "Kongsberg vinner kontrakt - E24"))
        self.assertFalse(exact_title_match(name, "Gruppen ASA vinner kontrakt - E24"))

    def test_malformed_pubdate_items_skipped(self):
        feed_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Equinor ASA satser - E24</title>
              <link>https://e24.no/1</link>
              <pubDate>This is not a date</pubDate>
            </item>
            <item>
              <title>Equinor ASA godkjent - E24</title>
              <link>https://e24.no/2</link>
              <pubDate>Mon, 15 Jan 2024 10:30:00 GMT</pubDate>
            </item>
          </channel>
        </rss>"""
        record, _ = fetch_company_news_rss("Equinor ASA", http_fetcher=lambda u, h, t: feed_xml)
        items = record["value"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://e24.no/2")
        self.assertEqual(items[0]["date"], "2024-01-15")

    def test_missing_link_item_skipped(self):
        feed_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Equinor ASA satser - E24</title>
              <link></link>
              <pubDate>Mon, 15 Jan 2024 10:30:00 GMT</pubDate>
            </item>
          </channel>
        </rss>"""
        record, _ = fetch_company_news_rss("Equinor ASA", http_fetcher=lambda u, h, t: feed_xml)
        self.assertEqual(record["status"], "not_available")
        self.assertEqual(record["value"]["items"], [])

    def test_strict_contract_invariants(self):
        # 1. Available news test
        prof_avail = {
            "organisation_number": "123456789",
            "name": "TEST AS",
            "evidence": {
                "news": {
                    "field": "news",
                    "status": "available",
                    "content_sha256": "d" * 64,
                    "value": {
                        "items": [{"title": "T", "url": "https://u", "date": "2024-01-01"}],
                        "latest": {"title": "T", "url": "https://u", "date": "2024-01-01"},
                    },
                }
            },
        }
        env_avail = build_output_envelope(prof_avail, run_id="r1", started_at="s", completed_at="c")
        news_ev = next((e for e in env_avail["evidence"] if e["id"] == "ev-news"), None)
        self.assertIsNotNone(news_ev)
        self.assertEqual(news_ev["source_class"], "public_news")
        for f in ("latest_news_title", "latest_news_date", "latest_news_url"):
            claim = next(c for c in env_avail["claims"] if c["field"] == f)
            self.assertIsNotNone(claim["value"])
            self.assertEqual(claim["availability"], "available")
            self.assertEqual(claim["confidence"], 0.99)
            self.assertEqual(claim["evidence_ids"], ["ev-news"])

        # 2. Not available news test
        prof_not_avail = {
            "organisation_number": "123456789",
            "name": "TEST AS",
            "evidence": {
                "news": {
                    "field": "news",
                    "status": "not_available",
                    "content_sha256": "e" * 64,
                    "value": {"items": [], "latest": None},
                }
            },
        }
        env_not_avail = build_output_envelope(prof_not_avail, run_id="r2", started_at="s", completed_at="c")
        self.assertIsNone(next((e for e in env_not_avail["evidence"] if e["id"] == "ev-news"), None))
        for f in ("latest_news_title", "latest_news_date", "latest_news_url"):
            claim = next(c for c in env_not_avail["claims"] if c["field"] == f)
            self.assertIsNone(claim["value"])
            self.assertEqual(claim["availability"], "not_available")
            self.assertEqual(claim["confidence"], 1.0)
            self.assertEqual(claim["evidence_ids"], [])


if __name__ == "__main__":
    unittest.main()
