"""Publication gates for site news and social links (hermetic)."""
import unittest
from datetime import date

from norway_company_agent.publication_gates import filter_site_news, gate_social_links, social_handle

TODAY = date(2026, 11, 16)


def item(title, day, url="https://firma.no/nyheter/sak"):
    return {"title": title, "date": day, "url": url}


def news(items, site="https://firma.no/", nace="43.320"):
    return [i["title"] for i in filter_site_news(items, site_url=site, nace=nace, today=TODAY)]


def links(*urls):
    out = []
    for u in urls:
        host = u.split("/")[2]
        out.append({"platform": host.split(".")[0], "url": u})
    return out


class SiteNews(unittest.TestCase):
    def test_recent_real_item_kept(self):
        self.assertEqual(news([item("Firma kjøper ny servicebåt", "2026-11-02")]), ["Firma kjøper ny servicebåt"])

    def test_stale_and_future_items_dropped(self):
        self.assertEqual(news([item("Skytebana blir klar til våren", "2015-06-04"), item("Dette er fra fremtiden", "2027-03-01")]), [])

    def test_bulk_same_day_pages_dropped_but_others_kept(self):
        rows = [item(f"Produkt nummer {n} modell", "2026-01-26", f"https://firma.no/en/p{n}") for n in range(3)]
        rows.append(item("Firma inntar Sverige med stil", "2026-02-20"))
        self.assertEqual(news(rows), ["Firma inntar Sverige med stil"])

    def test_short_titles_dropped(self):
        self.assertEqual(news([item("Rebotec", "2026-08-16"), item("Rita Ørnes", "2025-11-14"), item("801 Sport CAB 2026", "2026-05-01")]), [])

    def test_evergreen_guides_and_seo_pages_dropped(self):
        rows = [item("Hva koster det å gå til psykolog i Sandnes?", "2026-01-18"), item("Bygge terrasse: Slik planlegger du uteplassen", "2026-06-08"),
                item("Concrete Data Loggers for Data Centers Norway | SmartRock", "2026-10-04"), item("10 mest stilte spørsmål om ekstralys", "2026-11-03"),
                item("AI-søk forklart: så slipper du å søke", "2026-06-23"), item("Lede setter beredskap etter farevarsel om uvær", "2026-10-23")]
        self.assertEqual(news(rows), ["Lede setter beredskap etter farevarsel om uvær"])

    def test_media_company_site_articles_dropped(self):
        self.assertEqual(news([item("Mann reagerte på barnestreker", "2026-11-16")], nace="58.130"), [])

    def test_other_country_section_dropped(self):
        rows = [item("Pelly i rörelse – dåtid och framtid", "2025-10-21", "https://pellybutik.se/pelly-i-rorelse/"),
                item("Ny butikk åpnet i Oslo", "2026-03-01", "https://pellybutik.se/no/ny-butikk/")]
        self.assertEqual(news(rows, site="https://www.pellybutik.se/no/"), ["Ny butikk åpnet i Oslo"])

    def test_plain_subpath_site_is_not_a_country_section(self):
        self.assertEqual(len(news([item("100 kunder uten strøm søndag", "2026-10-25", "https://lede.no/kunde/om/x")], site="https://lede.no/kunde/")), 1)


class SocialLinks(unittest.TestCase):
    def gate(self, urls, site="https://firma.no/", name="FIRMA AS"):
        return [l["url"] for l in gate_social_links(links(*urls), site_url=site, legal_name=name)]

    def test_profile_shapes(self):
        self.assertEqual(social_handle("facebook", "https://facebook.com/SoltvedtMaskinAs"), "soltvedtmaskinas")
        self.assertIsNone(social_handle("facebook", "https://facebook.com/SoltvedtMaskinAs/videos/2242275782724504"))
        self.assertEqual(social_handle("facebook", "https://facebook.com/p/Follo-Boligtakst-As-100054243350269"), "follo-boligtakst-as-100054243350269")
        self.assertIsNone(social_handle("facebook", "https://facebook.com/sharer.php"))
        self.assertIsNone(social_handle("instagram", "https://instagram.com/p/Cabc123"))
        self.assertEqual(social_handle("linkedin", "https://linkedin.com/company/moen-marin-as"), "moen-marin-as")
        self.assertIsNone(social_handle("linkedin", "https://linkedin.com/in/ola-nordmann"))
        self.assertEqual(social_handle("youtube", "https://youtube.com/@Livskartet"), "livskartet")
        self.assertIsNone(social_handle("youtube", "https://youtube.com/watch"))

    def test_one_profile_per_platform_and_no_post_urls(self):
        got = self.gate(["https://facebook.com/SoltvedtMaskinAs/videos/22", "https://facebook.com/SoltvedtMaskinAs", "https://facebook.com/annen"])
        self.assertEqual(got, ["https://facebook.com/SoltvedtMaskinAs"])

    def test_global_brand_accounts_dropped_when_norwegian_ones_exist(self):
        got = self.gate(["https://facebook.com/JYSK.no", "https://instagram.com/jysknorge", "https://linkedin.com/company/jysk", "https://tiktok.com/@jysk"], name="JYSK AS")
        self.assertEqual(got, ["https://facebook.com/JYSK.no", "https://instagram.com/jysknorge"])

    def test_no_country_accounts_means_nothing_dropped(self):
        urls = ["https://facebook.com/moenmarin", "https://linkedin.com/company/moen-marin-as"]
        self.assertEqual(self.gate(urls), urls)

    def test_country_word_in_legal_name_disables_rule(self):
        urls = ["https://instagram.com/dia_proff_norge_as", "https://tiktok.com/@diaproff"]
        self.assertEqual(self.gate(urls, name="DIA PROFF NORGE AS"), urls)

    def test_country_page_of_foreign_group_site_publishes_no_socials(self):
        self.assertEqual(self.gate(["https://linkedin.com/company/ntt-data-business-solutions"], site="https://nttdata-solutions.com/no/"), [])
        self.assertEqual(len(self.gate(["https://linkedin.com/company/nimmo-as"], site="https://nimmo.no/no/")), 1)
        own = ["https://facebook.com/Nimmoas", "https://linkedin.com/company/nimmo-as"]
        self.assertEqual(self.gate(own, site="https://nimmo.app/no", name="NIMMO AS"), own)


if __name__ == "__main__":
    unittest.main()


class SourceLevelGate(unittest.TestCase):
    def test_profile_lists_are_gated_in_place_and_jobs_kept(self):
        from norway_company_agent.publication_gates import apply_publication_gates
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
        profile = {"name": "FIRMA AS", "industry_code": "43.320", "evidence": {"website": {"value": {
            "final_url": "https://firma.no/", "identity_assessment": {"publishable": True},
            "news_items": [
                {"title": "Skytebana blir klar til våren", "date": "2015-06-04", "url": "https://firma.no/a"},
                {"title": "Firma kjøper ny servicebåt", "date": recent, "url": "https://firma.no/b"},
                {"title": "Ledig stilling: elektriker søkes", "date": recent, "url": "https://firma.no/c", "kind": "job_posting"},
            ],
            "social_links": [{"platform": "facebook", "url": "https://facebook.com/firma/videos/1"},
                             {"platform": "facebook", "url": "https://facebook.com/firma"}],
        }}}}
        apply_publication_gates(profile)
        apply_publication_gates(profile)  # idempotent
        value = profile["evidence"]["website"]["value"]
        self.assertEqual(sorted(i["url"][-1] for i in value["news_items"]), ["b", "c"])
        self.assertEqual([l["url"] for l in value["social_links"]], ["https://facebook.com/firma"])

    def test_unverified_site_is_left_alone(self):
        from norway_company_agent.publication_gates import apply_publication_gates
        profile = {"evidence": {"website": {"value": {"identity_assessment": {"publishable": False}, "news_items": [{"title": "x", "date": "2015-01-01", "url": "u"}]}}}}
        apply_publication_gates(profile)
        self.assertEqual(len(profile["evidence"]["website"]["value"]["news_items"]), 1)
