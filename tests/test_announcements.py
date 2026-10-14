"""Tests for Corporate Announcements multi-table scraping, classification, and output contract claims emission."""

from __future__ import annotations

import hashlib
import unittest
from unittest.mock import MagicMock

from norway_company_agent.announcements import (
    classify_all_notices,
    classify_notice,
    fetch_announcements,
)
from norway_company_agent.contract import build_output_envelope


class TestAnnouncementsMultiTableScraping(unittest.TestCase):
    """Multi-table HTML parsing across layout tables and notice tables."""

    def test_multi_table_scraping_ignores_layout_table_0_and_extracts_subsequent(self):
        """Table 0 is an empty layout wrapper, notices are in Table 1 and Table 2."""
        mock_html = (
            "<html><body>"
            "<table id='layout-0'><tr><td><!-- empty wrapper --></td></tr></table>"
            "<table id='notices-1'>"
            "  <tr><th>Dato</th><th>Kunngjøring</th></tr>"
            "  <tr><td>02.07.2026</td><td><a href='/kunngjoring/hent_detaljer.jsp?id=101'>Kapital</a></td></tr>"
            "  <tr><td>18.08.2026</td><td><a href='hent_detaljer.jsp?id=102'>Årsregnskap</a></td></tr>"
            "</table>"
            "<table id='notices-2'>"
            "  <tr><td>04.09.2026</td><td><a href='https://w2.brreg.no/kunngjoring/hent_detaljer.jsp?id=103'>Styrets sammensetning</a></td></tr>"
            "</table>"
            "</body></html>"
        ).encode("utf-8")

        mock_fetcher = MagicMock(return_value=mock_html)
        res = fetch_announcements("923609016", http_fetcher=mock_fetcher)

        self.assertEqual(res["status"], "available")
        self.assertEqual(res["corporate_actions_count"], 3)
        self.assertEqual(len(res["events"]), 3)
        self.assertEqual(res["source_url"], "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=923609016")
        self.assertEqual(res["raw_html_sha256"], hashlib.sha256(mock_html).hexdigest())

        # Verify canonical URL normalization
        urls = {ev["title"]: ev["source_url"] for ev in res["events"]}
        self.assertEqual(urls["Kapital"], "https://w2.brreg.no/kunngjoring/hent_detaljer.jsp?id=101")
        self.assertEqual(urls["Årsregnskap"], "https://w2.brreg.no/kunngjoring/hent_detaljer.jsp?id=102")
        self.assertEqual(urls["Styrets sammensetning"], "https://w2.brreg.no/kunngjoring/hent_detaljer.jsp?id=103")

    def test_multi_table_malformed_and_irrelevant_rows_skipped(self):
        """Headers, empty rows, and single-column layout cells are ignored."""
        mock_html = (
            "<html><body>"
            "<table>"
            "  <tr><td>Kunngjøringer for foretak</td></tr>"  # 1 col
            "  <tr><td>Dato</td><td>Type</td></tr>"  # No date pattern
            "  <tr><td></td><td></td></tr>"  # Empty
            "  <tr><td>12.04.2025</td><td></td></tr>"  # Empty title
            "  <tr><td>15.05.2025</td><td>Endring av vedtekter</td></tr>"  # Valid row without <a> tag
            "</table>"
            "</body></html>"
        ).encode("utf-8")

        mock_fetcher = MagicMock(return_value=mock_html)
        res = fetch_announcements("923609016", http_fetcher=mock_fetcher)

        self.assertEqual(res["corporate_actions_count"], 1)
        self.assertEqual(res["events"][0]["date"], "2025-05-15")
        self.assertEqual(res["events"][0]["title"], "Endring av vedtekter")
        self.assertEqual(res["events"][0]["source_url"], "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=923609016")

    def test_windows_1252_norwegian_characters_handling(self):
        """Decodes Windows-1252 special characters properly."""
        mock_html = (
            b"<html><body><table>"
            b"<tr><td>01.03.2025</td><td>Kapitalforh\xf8yelse</td></tr>"
            b"<tr><td>15.02.2025</td><td>Godkjent \xe5rsregnskap</td></tr>"
            b"</table></body></html>"
        )
        res = fetch_announcements("923609016", http_fetcher=lambda u, d: mock_html)
        self.assertEqual(res["corporate_actions_count"], 2)
        titles = [ev["title"] for ev in res["events"]]
        self.assertIn("Kapitalforhøyelse", titles)
        self.assertIn("Godkjent årsregnskap", titles)

    def test_official_brreg_five_column_layout_with_spacer_cells(self):
        """Official Brreg Kunngjøringer layout (Table 4) has 5 columns: ['', 'DD.MM.YYYY', '', 'Title', '']."""
        mock_html = (
            "<html><body>"
            "<table id='layout-table-3'><tr><td>"
            "  <table id='notices-table-4'>"
            "    <tr><td></td><td>Dato</td><td></td><td>Kunngjøringstype</td><td></td></tr>"
            "    <tr><td></td><td>04.09.2026</td><td></td><td><a href='hent_en.jsp?kid=101'>Styre</a></td><td></td></tr>"
            "    <tr><td></td><td>18.08.2026</td><td></td><td><a href='hent_en.jsp?kid=102'>Godkjent årsregnskap</a></td><td></td></tr>"
            "    <tr><td></td><td>02.07.2026</td><td></td><td><a href='hent_en.jsp?kid=103'>Kapital</a></td><td></td></tr>"
            "  </table>"
            "</td></tr></table>"
            "</body></html>"
        ).encode("utf-8")

        res = fetch_announcements("923609016", http_fetcher=lambda u, d: mock_html)
        self.assertEqual(res["status"], "available")
        self.assertEqual(res["corporate_actions_count"], 3)
        self.assertEqual(len(res["events"]), 3)
        self.assertEqual(res["events"][0]["date"], "2026-09-04")
        self.assertEqual(res["events"][0]["title"], "Styre")
        self.assertEqual(res["events"][0]["category"], "board_appointment")
        self.assertEqual(res["events"][0]["source_url"], "https://w2.brreg.no/kunngjoring/hent_en.jsp?kid=101")
        self.assertEqual(res["categories"]["board_appointment"]["count"], 1)
        self.assertEqual(res["categories"]["board_appointment"]["latest_date"], "2026-09-04")
        self.assertEqual(res["categories"]["legal_filing"]["count"], 1)
        self.assertEqual(res["categories"]["capital_change"]["count"], 1)

    def test_nested_table_duplicate_rejection(self):
        """Notices enclosed inside nested layout tables should not be duplicated."""
        mock_html = (
            "<html><body>"
            "<table id='outer'>"
            "  <tr><td>"
            "    <table id='inner'>"
            "      <tr><td>"
            "        <table id='notices'>"
            "          <tr><td>10.10.2025</td><td><a href='notice1'>Aksjekapital</a></td></tr>"
            "        </table>"
            "      </td></tr>"
            "    </table>"
            "  </td></tr>"
            "</table>"
            "</body></html>"
        ).encode("utf-8")

        res = fetch_announcements("923609016", http_fetcher=lambda u, d: mock_html)
        self.assertEqual(res["status"], "available")
        self.assertEqual(res["corporate_actions_count"], 1)
        self.assertEqual(len(res["events"]), 1)
        self.assertEqual(res["events"][0]["title"], "Aksjekapital")
        self.assertEqual(res["events"][0]["date"], "2025-10-10")
        self.assertEqual(res["events"][0]["category"], "capital_change")

    def test_non_breaking_spaces_in_titles_and_dates(self):
        """Non-breaking spaces in titles and dates are normalized and properly parsed."""
        cases = [
            ("Daglig\xa0leder", "board_appointment"),
            ("Erverv\xa0av\xa0egne\xa0aksjer", "capital_change"),
            ("Endring\xa0av\xa0foretaksnavn", "legal_filing"),
            ("Innstilling\xa0av\xa0bobehandling", "insolvency"),
        ]
        for title, exp_cat in cases:
            ev = classify_notice({"title": title, "date": "2026-01-01"})
            self.assertEqual(ev["category"], exp_cat, f"Failed for {title}")

        mock_html = (
            "<html><body><table>"
            "<tr><td>01.&nbsp;09.&nbsp;2026</td><td><a href='notice1'>Daglig&nbsp;leder</a></td></tr>"
            "<tr><td>02. 09. 2026</td><td><a href='notice2'>Erverv&nbsp;av&nbsp;egne&nbsp;aksjer</a></td></tr>"
            "<tr><td> &nbsp; 03.09.2026 &nbsp; </td><td><a href='notice3'>Endring&nbsp;av&nbsp;foretaksnavn</a></td></tr>"
            "</table></body></html>"
        ).encode("utf-8")

        res = fetch_announcements("923609016", http_fetcher=lambda u, d: mock_html)
        self.assertEqual(res["corporate_actions_count"], 3)
        self.assertEqual(res["categories"]["board_appointment"]["count"], 1)
        self.assertEqual(res["categories"]["capital_change"]["count"], 1)
        self.assertEqual(res["categories"]["legal_filing"]["count"], 1)
        dates = [e["date"] for e in res["events"]]
        self.assertEqual(dates, ["2026-09-03", "2026-09-02", "2026-09-01"])



class TestDateNormalizationAndSorting(unittest.TestCase):
    """Date normalization from DD.MM.YYYY to YYYY-MM-DD and descending chronological sorting."""

    def test_events_sorted_strictly_descending_by_date(self):
        notices = [
            {"title": "Notice 2023", "date": "2023-01-15"},
            {"title": "Notice 2026", "date": "2026-09-04"},
            {"title": "Notice 2024", "date": "2024-11-20"},
            {"title": "Notice 2025", "date": "2025-06-30"},
        ]
        classified = classify_all_notices(notices)
        dates = [ev["date"] for ev in classified["events"]]
        self.assertEqual(dates, ["2026-09-04", "2025-06-30", "2024-11-20", "2023-01-15"])

    def test_date_format_normalized_during_scraping(self):
        mock_html = (
            "<html><body><table>"
            "<tr><td>05.01.2024</td><td>Aksjekapital</td></tr>"
            "<tr><td>29.12.2025</td><td>Konkurs</td></tr>"
            "</table></body></html>"
        ).encode("utf-8")
        res = fetch_announcements("123456789", http_fetcher=lambda u, d: mock_html)
        self.assertEqual(res["events"][0]["date"], "2025-12-29")
        self.assertEqual(res["events"][1]["date"], "2024-01-05")


class TestCategoryClassificationAndAggregation(unittest.TestCase):
    """Category classification across capital_change, board_appointment, legal_filing, insolvency, and other."""

    def test_capital_change_keywords(self):
        for term in ("Kapital", "Kapitalnedsettelse", "Kapitalforhøyelse", "Aksjekapital"):
            ev = classify_notice({"title": term, "date": "2025-01-01"})
            self.assertEqual(ev["category"], "capital_change", f"Failed for {term}")

    def test_board_appointment_keywords(self):
        for term in ("Styre", "Daglig leder", "Revisor", "Styrets sammensetning", "Endring av styre"):
            ev = classify_notice({"title": term, "date": "2025-01-01"})
            self.assertEqual(ev["category"], "board_appointment", f"Failed for {term}")

    def test_legal_filing_keywords(self):
        for term in ("Vedtekter", "Årsregnskap", "Endring av vedtekter", "Godkjent årsregnskap"):
            ev = classify_notice({"title": term, "date": "2025-01-01"})
            self.assertEqual(ev["category"], "legal_filing", f"Failed for {term}")

    def test_insolvency_keywords(self):
        for term in ("Konkurs", "Tvangsavvikling", "Oppløsning", "Konkursåpning"):
            ev = classify_notice({"title": term, "date": "2025-01-01"})
            self.assertEqual(ev["category"], "insolvency", f"Failed for {term}")

    def test_other_notice_keyword(self):
        ev = classify_notice({"title": "Melding om flytting", "date": "2025-01-01"})
        self.assertEqual(ev["category"], "other")

    def test_category_aggregation_counts_and_latest_dates(self):
        notices = [
            {"title": "Kapitalforhøyelse", "date": "2024-02-01"},
            {"title": "Kapitalnedsettelse", "date": "2025-07-15"},
            {"title": "Styre", "date": "2025-05-10"},
            {"title": "Daglig leder", "date": "2026-01-02"},
            {"title": "Årsregnskap", "date": "2024-08-20"},
        ]
        res = classify_all_notices(notices)
        cats = res["categories"]

        self.assertEqual(cats["capital_change"]["count"], 2)
        self.assertEqual(cats["capital_change"]["latest_date"], "2025-07-15")

        self.assertEqual(cats["board_appointment"]["count"], 2)
        self.assertEqual(cats["board_appointment"]["latest_date"], "2026-01-02")

        self.assertEqual(cats["legal_filing"]["count"], 1)
        self.assertEqual(cats["legal_filing"]["latest_date"], "2024-08-20")

        self.assertEqual(cats["insolvency"]["count"], 0)
        self.assertIsNone(cats["insolvency"]["latest_date"])

        self.assertEqual(cats["other"]["count"], 0)
        self.assertIsNone(cats["other"]["latest_date"])


class TestOutputEnvelopeContractClaims(unittest.TestCase):
    """Output envelope emission of all 6 announcement claims under ev-announcements."""

    def test_populated_announcements_emission(self):
        events = [
            {
                "title": "Daglig leder",
                "type": "Daglig leder",
                "date": "2026-09-01",
                "category": "board_appointment",
                "source_url": "https://w2.brreg.no/kunngjoring/hent_detaljer.jsp?id=1",
            },
            {
                "title": "Kapitalforhøyelse",
                "type": "Kapitalforhøyelse",
                "date": "2026-05-15",
                "category": "capital_change",
                "source_url": "https://w2.brreg.no/kunngjoring/hent_detaljer.jsp?id=2",
            },
        ]
        profile = {
            "organisation_number": "923609016",
            "name": "TESTSELSKAP AS",
            "evidence": {
                "registry": {
                    "id": "ev-registry",
                    "status": "available",
                    "source_class": "official_registry",
                    "source_url": "https://data.brreg.no",
                    "value": {"navn": "TESTSELSKAP AS"},
                },
                "announcements": {
                    "id": "ev-announcements",
                    "status": "available",
                    "source_class": "official_public_notices",
                    "source_url": "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=923609016",
                    "value": {
                        "events": events,
                        "categories": {
                            "capital_change": {"count": 1, "latest_date": "2026-05-15"},
                            "board_appointment": {"count": 1, "latest_date": "2026-09-01"},
                            "legal_filing": {"count": 0, "latest_date": None},
                            "insolvency": {"count": 0, "latest_date": None},
                            "other": {"count": 0, "latest_date": None},
                        },
                    },
                    "content_sha256": "a" * 64,
                },
            },
        }

        envelope = build_output_envelope(
            profile,
            run_id="run-test-1",
            started_at="2026-10-14T10:00:00Z",
            completed_at="2026-10-14T10:00:05Z",
        )

        claims_by_field = {c["field"]: c for c in envelope["claims"]}

        # 1. corporate_announcements_count
        c_count = claims_by_field["corporate_announcements_count"]
        self.assertEqual(c_count["value"], 2)
        self.assertEqual(c_count["availability"], "available")
        self.assertEqual(c_count["confidence"], 0.99)
        self.assertEqual(c_count["evidence_ids"], ["ev-announcements"])

        # 2. latest_corporate_action_date
        c_date = claims_by_field["latest_corporate_action_date"]
        self.assertEqual(c_date["value"], "2026-09-01")
        self.assertEqual(c_date["availability"], "available")
        self.assertEqual(c_date["confidence"], 0.99)
        self.assertEqual(c_date["evidence_ids"], ["ev-announcements"])

        # 3. latest_corporate_action_title
        c_title = claims_by_field["latest_corporate_action_title"]
        self.assertEqual(c_title["value"], "Daglig leder")
        self.assertEqual(c_title["availability"], "available")
        self.assertEqual(c_title["confidence"], 0.99)
        self.assertEqual(c_title["evidence_ids"], ["ev-announcements"])

        # 4. latest_corporate_action_url
        c_url = claims_by_field["latest_corporate_action_url"]
        self.assertEqual(c_url["value"], "https://w2.brreg.no/kunngjoring/hent_detaljer.jsp?id=1")
        self.assertEqual(c_url["availability"], "available")
        self.assertEqual(c_url["confidence"], 0.99)
        self.assertEqual(c_url["evidence_ids"], ["ev-announcements"])

        # 5. latest_capital_change_date
        c_cap = claims_by_field["latest_capital_change_date"]
        self.assertEqual(c_cap["value"], "2026-05-15")
        self.assertEqual(c_cap["availability"], "available")
        self.assertEqual(c_cap["confidence"], 0.99)
        self.assertEqual(c_cap["evidence_ids"], ["ev-announcements"])

        # 6. latest_board_appointment_date
        c_board = claims_by_field["latest_board_appointment_date"]
        self.assertEqual(c_board["value"], "2026-09-01")
        self.assertEqual(c_board["availability"], "available")
        self.assertEqual(c_board["confidence"], 0.99)
        self.assertEqual(c_board["evidence_ids"], ["ev-announcements"])

    def test_empty_announcements_handling_zero_events(self):
        """0 events results in corporate_announcements_count=0 (available) and latest_* as not_available."""
        profile = {
            "organisation_number": "999999999",
            "name": "EMPTY AS",
            "evidence": {
                "registry": {
                    "id": "ev-registry",
                    "status": "available",
                    "source_class": "official_registry",
                    "source_url": "https://data.brreg.no",
                    "value": {"navn": "EMPTY AS"},
                },
                "announcements": {
                    "id": "ev-announcements",
                    "status": "available",
                    "source_class": "official_public_notices",
                    "source_url": "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=999999999",
                    "value": {
                        "events": [],
                        "categories": {
                            "capital_change": {"count": 0, "latest_date": None},
                            "board_appointment": {"count": 0, "latest_date": None},
                            "legal_filing": {"count": 0, "latest_date": None},
                            "insolvency": {"count": 0, "latest_date": None},
                            "other": {"count": 0, "latest_date": None},
                        },
                    },
                    "content_sha256": "b" * 64,
                },
            },
        }

        envelope = build_output_envelope(
            profile,
            run_id="run-test-2",
            started_at="2026-10-14T10:00:00Z",
            completed_at="2026-10-14T10:00:05Z",
        )

        claims_by_field = {c["field"]: c for c in envelope["claims"]}

        # Count is available with value 0
        c_count = claims_by_field["corporate_announcements_count"]
        self.assertEqual(c_count["value"], 0)
        self.assertEqual(c_count["availability"], "available")
        self.assertEqual(c_count["confidence"], 0.99)
        self.assertEqual(c_count["evidence_ids"], ["ev-announcements"])

        # All 5 latest claims are not_available with value None and confidence 1.0
        for field in (
            "latest_corporate_action_date",
            "latest_corporate_action_title",
            "latest_corporate_action_url",
            "latest_capital_change_date",
            "latest_board_appointment_date",
        ):
            c = claims_by_field[field]
            self.assertIsNone(c["value"])
            self.assertEqual(c["availability"], "not_available")
            self.assertEqual(c["confidence"], 1.0)
            self.assertEqual(c["evidence_ids"], ["ev-announcements"])

    def test_missing_announcements_record_emits_all_not_available(self):
        """When announcements evidence is absent, all 6 claims are not_available with empty evidence_ids."""
        profile = {
            "organisation_number": "888888888",
            "name": "NO ANNOUNCEMENTS AS",
            "evidence": {
                "registry": {
                    "id": "ev-registry",
                    "status": "available",
                    "source_class": "official_registry",
                    "source_url": "https://data.brreg.no",
                    "value": {"navn": "NO ANNOUNCEMENTS AS"},
                },
            },
        }

        envelope = build_output_envelope(
            profile,
            run_id="run-test-3",
            started_at="2026-10-14T10:00:00Z",
            completed_at="2026-10-14T10:00:05Z",
        )

        claims_by_field = {c["field"]: c for c in envelope["claims"]}
        for field in (
            "corporate_announcements_count",
            "latest_corporate_action_date",
            "latest_corporate_action_title",
            "latest_corporate_action_url",
            "latest_capital_change_date",
            "latest_board_appointment_date",
        ):
            c = claims_by_field[field]
            self.assertIsNone(c["value"])
            self.assertEqual(c["availability"], "not_available")
            self.assertEqual(c["confidence"], 1.0)
            self.assertEqual(c["evidence_ids"], [])

    def test_partial_category_availability(self):
        """When board appointment exists but capital change does not, individual claim availabilities reflect this."""
        events = [
            {
                "title": "Nytt styre",
                "type": "Nytt styre",
                "date": "2025-04-10",
                "category": "board_appointment",
                "source_url": "https://w2.brreg.no/kunngjoring/hent_detaljer.jsp?id=9",
            }
        ]
        profile = {
            "organisation_number": "777777777",
            "name": "PARTIAL AS",
            "evidence": {
                "registry": {
                    "id": "ev-registry",
                    "status": "available",
                    "source_class": "official_registry",
                    "source_url": "https://data.brreg.no",
                    "value": {"navn": "PARTIAL AS"},
                },
                "announcements": {
                    "id": "ev-announcements",
                    "status": "available",
                    "source_class": "official_public_notices",
                    "source_url": "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=777777777",
                    "value": {
                        "events": events,
                        "categories": {
                            "capital_change": {"count": 0, "latest_date": None},
                            "board_appointment": {"count": 1, "latest_date": "2025-04-10"},
                            "legal_filing": {"count": 0, "latest_date": None},
                            "insolvency": {"count": 0, "latest_date": None},
                            "other": {"count": 0, "latest_date": None},
                        },
                    },
                    "content_sha256": "c" * 64,
                },
            },
        }

        envelope = build_output_envelope(
            profile,
            run_id="run-test-4",
            started_at="2026-10-14T10:00:00Z",
            completed_at="2026-10-14T10:00:05Z",
        )

        claims_by_field = {c["field"]: c for c in envelope["claims"]}

        # Board appointment is available
        self.assertEqual(claims_by_field["latest_board_appointment_date"]["value"], "2025-04-10")
        self.assertEqual(claims_by_field["latest_board_appointment_date"]["availability"], "available")
        self.assertEqual(claims_by_field["latest_board_appointment_date"]["confidence"], 0.99)
        self.assertEqual(claims_by_field["latest_board_appointment_date"]["evidence_ids"], ["ev-announcements"])

        # Capital change is not_available
        self.assertIsNone(claims_by_field["latest_capital_change_date"]["value"])
        self.assertEqual(claims_by_field["latest_capital_change_date"]["availability"], "not_available")
        self.assertEqual(claims_by_field["latest_capital_change_date"]["confidence"], 1.0)
        self.assertEqual(claims_by_field["latest_capital_change_date"]["evidence_ids"], ["ev-announcements"])

    def test_pipeline_c_bare_evidence_content_sha256_fallback(self):
        """Bare evidence without top-level content_sha256 falls back to raw_html_sha256 in value dict."""
        profile = {
            "organisation_number": "923609016",
            "name": "TEST AS",
            "evidence": {
                "registry": {"status": "available", "value": {"navn": "TEST AS"}},
                "announcements": {
                    "id": "ev-announcements",
                    "status": "available",
                    "source_url": "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=923609016",
                    "source_class": "official_public_notices",
                    "value": {
                        "events": [{"title": "Kapital", "date": "2026-08-01", "category": "capital_change"}],
                        "categories": {"capital_change": {"count": 1, "latest_date": "2026-08-01"}},
                        "corporate_actions_count": 1,
                        "raw_html_sha256": "feedface" * 8,
                    },
                },
            },
        }
        env = build_output_envelope(profile, run_id="r3", started_at="t", completed_at="t")
        ev = next(e for e in env["evidence"] if e["id"] == "ev-announcements")
        self.assertEqual(ev["content_sha256"], "feedface" * 8)


if __name__ == "__main__":
    unittest.main()

