from __future__ import annotations

import io
import unittest
import urllib.error
from unittest import mock

from norway_company_agent import website
from norway_company_agent.synthesis import long_run_base


def _rec(start: str, end: str, revenue: float) -> dict:
    return {"period": {"fraDato": start, "tilDato": end}, "revenue": revenue}


class RobotsMissingFileTest(unittest.TestCase):
    def _http_error(self, code: int):
        return urllib.error.HTTPError("https://x.no/robots.txt", code, "err", {}, io.BytesIO(b""))

    def test_missing_robots_allows_every_later_page(self):
        cache: dict = {}
        with mock.patch.object(website, "assert_public_url"), \
             mock.patch.object(website.SAFE_OPENER, "open", side_effect=self._http_error(404)):
            self.assertTrue(website._robots_allowed("https://x.no/", 5, cache=cache))
            self.assertTrue(website._robots_allowed("https://x.no/kontakt", 5, cache=cache))
            self.assertTrue(website._robots_allowed("https://x.no/nyheter/a", 5, cache=cache))

    def test_forbidden_robots_disallows(self):
        cache: dict = {}
        with mock.patch.object(website, "assert_public_url"), \
             mock.patch.object(website.SAFE_OPENER, "open", side_effect=self._http_error(403)):
            self.assertFalse(website._robots_allowed("https://x.no/", 5, cache=cache))
            self.assertFalse(website._robots_allowed("https://x.no/kontakt", 5, cache=cache))

    def test_unreachable_robots_allows_one_get_only(self):
        cache: dict = {}
        with mock.patch.object(website, "assert_public_url"), \
             mock.patch.object(website.SAFE_OPENER, "open", side_effect=TimeoutError()):
            self.assertTrue(website._robots_allowed("https://x.no/", 5, cache=cache))
            self.assertFalse(website._robots_allowed("https://x.no/kontakt", 5, cache=cache))


class LongRunBaseTest(unittest.TestCase):
    def test_span_uses_calendar_years_with_gap(self):
        recs = [_rec("2025-01-01", "2025-12-31", 10), _rec("2024-01-01", "2024-12-31", 9), _rec("2022-01-01", "2022-12-31", 8)]
        rec, span = long_run_base(recs)
        self.assertEqual(span, 3)
        self.assertEqual(rec["revenue"], 8)

    def test_short_first_period_is_skipped(self):
        recs = [_rec("2026-01-01", "2026-12-31", 10), _rec("2025-01-01", "2025-12-31", 9),
                _rec("2024-01-01", "2024-12-31", 8), _rec("2023-06-03", "2023-12-31", 1)]
        rec, span = long_run_base(recs)
        self.assertEqual((rec["revenue"], span), (8, 2))

    def test_too_short_history(self):
        recs = [_rec("2025-01-01", "2025-12-31", 10), _rec("2024-01-01", "2024-12-31", 9), _rec("2024-06-01", "2024-12-31", 3)]
        self.assertIsNone(long_run_base(recs))


if __name__ == "__main__":
    unittest.main()
