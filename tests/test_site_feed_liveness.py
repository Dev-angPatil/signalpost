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


class PageRegionAndNoVacancyTest(unittest.TestCase):
    def test_page_region_article_is_not_a_news_card(self):
        from bs4 import BeautifulSoup
        from norway_company_agent.website import _extract_page_news
        html = """<html><body><article class="page-regions">
          <h1>Samfunnsnyttig forskning siden 1979</h1><time datetime="2026-11-03">3. nov</time>
          <a href="/nyheter/tre-regioner">Tre regioner</a><a href="/om-oss">Om oss</a>
          <a href="/publikasjoner">Publikasjoner</a><a href="/prosjekter">Prosjekter</a>
          <h2>Nyheter</h2></article></body></html>"""
        items = _extract_page_news("https://www.example.no/", BeautifulSoup(html, "lxml"), html)
        self.assertEqual(items, [])

    def test_no_vacancy_titles_are_junk(self):
        from norway_company_agent.website import _is_junk_news
        for title in ("Det er for tiden ingen ledige stillinger i Parat", "Vi har ingen ledige stillinger nå", "No vacancies at the moment"):
            self.assertTrue(_is_junk_news(title, "https://www.example.no/a/b", "", "2026-01-01"), title)
        self.assertFalse(_is_junk_news("Ledige stillinger: prosjektleder", "https://www.example.no/a/b", "", "2026-01-01"))


class CatalogueAndDuplicateTitleTest(unittest.TestCase):
    def test_catalogue_paths_and_cms_duplicates_are_junk(self):
        from norway_company_agent.website import _is_junk_news
        self.assertTrue(_is_junk_news("Sculptra – Original Content", "https://k.no/nyheter/sculptra/", "", "2026-03-17"))
        self.assertTrue(_is_junk_news("Sculptra behandling", "https://k.no/behandlinger/sculptra-original/", "", "2026-03-17"))
        self.assertTrue(_is_junk_news("Varmepumpe", "https://k.no/tjenester/varmepumpe/", "", "2026-03-17"))
        self.assertTrue(_is_junk_news("Ny side (kopi)", "https://k.no/2026/03/ny-side/", "", "2026-03-17"))
        self.assertFalse(_is_junk_news("Vi åpner ny avdeling i Bergen", "https://k.no/2026/03/17/ny-avdeling/", "", "2026-03-17"))
        self.assertFalse(_is_junk_news("Copy that: vi vant pris", "https://k.no/nyheter/pris/", "", "2026-03-17"))
