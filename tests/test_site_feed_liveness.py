from __future__ import annotations

import json
import unittest
from unittest import mock

from norway_company_agent import site_feeds


class SiteFeedLivenessTest(unittest.TestCase):
    def test_dead_and_off_site_permalinks_are_dropped(self):
        posts = json.dumps([
            {"link": "https://www.acme.no/live-post/", "title": {"rendered": "Ny avtale med kommunen"}, "date": "2026-10-01T10:00:00"},
            {"link": "https://www.acme.no/dead-post/", "title": {"rendered": "Grafana your data"}, "date": "2026-10-07T10:00:00"},
            {"link": "https://www.acme.no/moved-post/", "title": {"rendered": "Vi flytter kontoret"}, "date": "2026-09-01T10:00:00"},
        ]).encode()

        def fake_fetch(url, accept, timeout):
            if "wp-json" in url:
                return posts, url
            if "live-post" in url:
                return b"<html>Ny avtale med kommunen</html>", url
            if "moved-post" in url:
                return b"<html>parked</html>", "https://parking.example.com/"
            return None, None  # 404

        with mock.patch.object(site_feeds, "_fetch", side_effect=fake_fetch), \
                mock.patch.object(site_feeds, "_robots_allowed", return_value=True):
            res = site_feeds.fetch_site_feed_items("https://www.acme.no/")
        self.assertEqual([i["url"] for i in res["items"]], ["https://www.acme.no/live-post/"])
        self.assertTrue(res["items"][0]["page_sha256"])
        self.assertEqual(res["requests"], 4)

    def test_all_dead_returns_nothing(self):
        posts = json.dumps([{"link": "https://www.acme.no/a/", "title": {"rendered": "Gammel nyhet her"}, "date": "2026-10-01T10:00:00"}]).encode()

        def fake_fetch(url, accept, timeout):
            return (posts, url) if "wp-json" in url else (None, None)

        with mock.patch.object(site_feeds, "_fetch", side_effect=fake_fetch), \
                mock.patch.object(site_feeds, "_robots_allowed", return_value=True):
            res = site_feeds.fetch_site_feed_items("https://www.acme.no/")
        self.assertEqual(res["items"], [])


if __name__ == "__main__":
    unittest.main()
