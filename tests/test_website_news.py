"""Focused regression tests for bounded, identity-safe website news extraction."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.website import _extract_page_news  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
