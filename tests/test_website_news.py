"""Focused regression tests for bounded, identity-safe website news extraction."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.website import (  # noqa: E402
    NORWEGIAN_MONTHS,
    _extract_page_news,
    _normalize_news_date,
    _same_site_article_url,
)


class WebsiteNewsExtractionTests(unittest.TestCase):
    def test_jsonld_dates_are_merged_with_cards_and_sorted_by_activity_date(self):
        html = """
        <html>
          <head>
            <script type="application/ld+json">
            {
              "@graph": [
                {
                  "@type": "NewsArticle",
                  "headline": "Ny avtale med kommunen",
                  "url": "https://example.no/nyheter/avtale",
                  "datePublished": "2025-05-06T10:30:00+02:00",
                  "dateModified": "2025-05-07T10:30:00+02:00"
                },
                {
                  "@type": "Article",
                  "headline": "Ny årsrapport publisert",
                  "mainEntityOfPage": {"@id": "/nyheter/arsrapport"},
                  "dateModified": "2025-04-03T08:00:00Z"
                }
              ]
            }
            </script>
          </head>
          <body>
            <article class="news-item">
              <h2><a href="/nyheter/avtale">Ny avtale med kommunen</a></h2>
              <time datetime="2025-05-06T10:30:00+02:00">06.05.2025</time>
            </article>
            <article class="news-item">
              <h2><a href="/nyheter/eldre">Eldre pressemelding</a></h2>
              <time>01.02.2025</time>
            </article>
          </body>
        </html>
        """

        items = _extract_page_news(
            "https://example.no/nyheter",
            BeautifulSoup(html, "lxml"),
            html,
        )

        self.assertEqual(
            items,
            [
                {
                    "title": "Ny avtale med kommunen",
                    "url": "https://example.no/nyheter/avtale",
                    "date": "2025-05-06",
                },
                {
                    "title": "Ny årsrapport publisert",
                    "url": "https://example.no/nyheter/arsrapport",
                    "date": "2025-04-03",
                },
                {
                    "title": "Eldre pressemelding",
                    "url": "https://example.no/nyheter/eldre",
                    "date": "2025-02-01",
                },
            ],
        )

    def test_common_article_meta_tags_support_iso_and_norwegian_dates(self):
        html = """
        <html>
          <head>
            <meta property="og:published_time" content="2025-06-07T23:45:00+02:00">
          </head>
          <body>
            <h1>Sommeroppdatering</h1>
            <p>Publisert 07.06.2025</p>
          </body>
        </html>
        """

        items = _extract_page_news(
            "https://example.no/press/sommeroppdatering",
            BeautifulSoup(html, "lxml"),
            html,
        )

        self.assertEqual(
            items,
            [
                {
                    "title": "Sommeroppdatering",
                    "url": "https://example.no/press/sommeroppdatering",
                    "date": "2025-06-07",
                }
            ],
        )

    def test_offsite_article_urls_are_not_published(self):
        html = """
        <html><body>
          <article class="news-item">
            <h2><a href="https://unrelated.example/press-release">Urelatert melding</a></h2>
            <time datetime="2025-01-02T09:00:00Z">02.01.2025</time>
          </article>
        </body></html>
        """

        items = _extract_page_news(
            "https://example.no/nyheter",
            BeautifulSoup(html, "lxml"),
            html,
        )

        self.assertEqual(items, [])

    def test_listing_cards_without_article_links_are_not_published(self):
        html = """
        <html><body>
          <article class="news-item"><h2>Uklart innlegg A</h2><time>02.01.2025</time></article>
          <article class="news-item"><h2>Uklart innlegg B</h2><time>01.01.2025</time></article>
        </body></html>
        """
        items = _extract_page_news(
            "https://example.no/nyheter",
            BeautifulSoup(html, "lxml"),
            html,
        )
        self.assertEqual(items, [])

    def test_extract_page_news_norwegian_textual_dates(self):
        """Extracts article cards with textual Norwegian month dates."""
        html = """
        <html><body>
          <div class="news-item">
            <h3><a href="/nyheter/mai-avtale">Avtale inngått</a></h3>
            <p class="dato">14. mai 2024</p>
          </div>
          <div class="news-item">
            <h3><a href="/nyheter/okt-oppdatering">Høstoppdatering</a></h3>
            <span class="published">3. okt. 2025</span>
          </div>
          <div class="news-item">
            <h3><a href="/nyheter/des-avslutning">Årsavslutning</a></h3>
            <span class="post-date">28. desember 2023</span>
          </div>
        </body></html>
        """
        items = _extract_page_news(
            "https://example.no/nyheter",
            BeautifulSoup(html, "lxml"),
            html,
        )
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["date"], "2025-10-03")
        self.assertEqual(items[0]["title"], "Høstoppdatering")
        self.assertEqual(items[1]["date"], "2024-05-14")
        self.assertEqual(items[1]["title"], "Avtale inngått")
        self.assertEqual(items[2]["date"], "2023-12-28")
        self.assertEqual(items[2]["title"], "Årsavslutning")

    def test_extract_page_news_modern_cms_selectors(self):
        """Extracts news articles from modern CMS layouts (Webflow, WordPress, etc.)."""
        html = """
        <html><body>
          <div class="w-dyn-item">
            <h2><a href="/nyheter/webflow-post">Webflow CMS Sak</a></h2>
            <div class="date">2025-08-15</div>
          </div>
          <article class="wp-block-post">
            <h2><a href="/nyheter/wp-post">WordPress Gutenberg Sak</a></h2>
            <time datetime="2025-08-10">10.08.2025</time>
          </article>
          <div class="blog-item">
            <h2><a href="/nyheter/blog-post">Squarespace Blogg Sak</a></h2>
            <span class="article-date">01.08.2025</span>
          </div>
        </body></html>
        """
        items = _extract_page_news(
            "https://example.no/nyheter",
            BeautifulSoup(html, "lxml"),
            html,
        )
        self.assertEqual(len(items), 3)
        titles = {item["title"] for item in items}
        self.assertIn("Webflow CMS Sak", titles)
        self.assertIn("WordPress Gutenberg Sak", titles)
        self.assertIn("Squarespace Blogg Sak", titles)

    def test_normalize_news_date_norwegian_months_coverage(self):
        """Tests _normalize_news_date across Norwegian month strings and abbreviations."""
        self.assertEqual(_normalize_news_date("14. mai 2024"), "2024-05-14")
        self.assertEqual(_normalize_news_date("3. okt. 2025"), "2025-10-03")
        self.assertEqual(_normalize_news_date("3. oktober 2025"), "2025-10-03")
        self.assertEqual(_normalize_news_date("1. januar 2026"), "2026-01-01")
        self.assertEqual(_normalize_news_date("28. feb 2024"), "2024-02-28")
        self.assertEqual(_normalize_news_date("15. september 2023"), "2023-09-15")
        self.assertIn("mai", NORWEGIAN_MONTHS)
        self.assertIn("okt", NORWEGIAN_MONTHS)

    def test_same_site_article_url_subdomain_and_rejections(self):
        """Verifies _same_site_article_url permits company-owned subdomains and rejects off-domain links."""
        base = "https://example.no/"
        sub_article = "https://nyheter.example.no/2025/03/ny-kontrakt"
        offsite = "https://other.no/artikkel"

        self.assertEqual(_same_site_article_url(base, sub_article), sub_article)
        self.assertIsNone(_same_site_article_url(base, offsite))


if __name__ == "__main__":
    unittest.main()
