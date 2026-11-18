"""schema.org JobPosting extraction and its publication gate (hermetic)."""
from __future__ import annotations

import json
import unittest
from datetime import date

from norway_company_agent.job_postings_ld import extract_ld_career_postings, jobpostings_from_html

TODAY = date(2026, 11, 16)
ATS = ("teamtailor.com", "webcruiter.no")
PROFILE = {"organisation_number": "979598009", "name": "SLAKTERIET BREKKE AS"}


def page(*postings, extra: str = "") -> str:
    scripts = "".join(f'<script type="application/ld+json">{json.dumps(p)}</script>' for p in postings)
    return f"<html><head>{scripts}</head><body>{extra}</body></html>"


def jp(title="Produksjonsleder", org="Slakteriet Brekke AS", country="NO", valid="2026-12-31", posted="2026-11-01", url=None):
    item = {"@context": "https://schema.org", "@type": "JobPosting", "title": title,
            "hiringOrganization": {"@type": "Organization", "name": org} if org else None,
            "jobLocation": {"@type": "Place", "address": {"addressCountry": country, "addressLocality": "Brekke"}} if country else None,
            "validThrough": valid, "datePosted": posted}
    if url:
        item["url"] = url
    return {k: v for k, v in item.items() if v is not None}


def run(html, page_url="https://slakteriet-as.no/karriere/", site_host="slakteriet-as.no", fetch=None):
    return extract_ld_career_postings(PROFILE, page_url, html, "a" * 64, site_host=site_host, ats_hosts=ATS,
                                      fetch=fetch or (lambda url: (None, None, None)), today=TODAY)


class JobPostingGate(unittest.TestCase):
    def test_valid_posting_published_with_dates_and_evidence(self):
        res = run(page(jp(url="/karriere/produksjonsleder")))
        self.assertEqual(len(res["postings"]), 1)
        p = res["postings"][0]
        self.assertEqual(p["title"], "Produksjonsleder")
        self.assertEqual(p["url"], "https://slakteriet-as.no/karriere/produksjonsleder")
        self.assertEqual((p["published"], p["expires"], p["location"]), ("2026-11-01", "2026-12-31", "Brekke"))
        self.assertEqual(p["source"], "company_career_page")
        self.assertIn("schema.org JobPosting", p["claim_span"])

    def test_expired_rejected(self):
        self.assertEqual(run(page(jp(valid="2026-10-01")))["postings"], [])

    def test_stale_without_valid_through_rejected(self):
        self.assertEqual(run(page(jp(valid=None, posted="2026-01-01")))["postings"], [])
        self.assertEqual(len(run(page(jp(valid=None, posted="2026-10-01")))["postings"]), 1)

    def test_foreign_location_rejected(self):
        self.assertEqual(run(page(jp(country="SE")))["postings"], [])
        self.assertEqual(len(run(page(jp(country="Norge")))["postings"]), 1)

    def test_other_employer_on_own_domain_rejected(self):
        self.assertEqual(run(page(jp(org="Nordfisk Holding AS")))["postings"], [])

    def test_brand_name_equal_to_domain_accepted_on_own_domain(self):
        self.assertEqual(len(run(page(jp(org="Slakteriet")))["postings"]), 1)

    def test_missing_hiring_org_accepted_only_on_own_domain(self):
        self.assertEqual(len(run(page(jp(org=None)))["postings"]), 1)
        ats = run(page(jp(org=None)), page_url="https://slakteriet.teamtailor.com/jobs")
        self.assertEqual(ats["postings"], [])

    def test_group_ats_needs_full_legal_name(self):
        url = "https://group.teamtailor.com/jobs/1"
        self.assertEqual(run(page(jp(org="Slakteriet Bergen AS")), page_url=url)["postings"], [])
        self.assertEqual(len(run(page(jp(org="Slakteriet Brekke AS")), page_url=url)["postings"]), 1)

    def test_unknown_third_party_host_rejected(self):
        self.assertEqual(run(page(jp()), page_url="https://jobs.example.com/x")["postings"], [])

    def test_offsite_url_replaced_by_page_url(self):
        res = run(page(jp(url="https://tracker.example.net/apply?id=1")))
        self.assertEqual(res["postings"][0]["url"], "https://slakteriet-as.no/karriere/")

    def test_graph_and_list_shapes(self):
        html = page({"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, jp(title="A")]}, [jp(title="B")])
        self.assertEqual(sorted(i["title"] for i in jobpostings_from_html(html)), ["A", "B"])

    def test_detail_pages_followed_when_listing_has_no_jsonld(self):
        listing = page(extra='<a href="/karriere/operator-123">Operatør</a><a href="/om-oss">Om</a>'
                             '<a href="https://evil.example.com/karriere/x">x</a>')
        fetched = []

        def fetch(url):
            fetched.append(url)
            return page(jp(title="Operatør")), url, "b" * 64

        res = run(listing, fetch=fetch)
        self.assertEqual(fetched, ["https://slakteriet-as.no/karriere/operator-123"])
        self.assertEqual([p["title"] for p in res["postings"]], ["Operatør"])
        self.assertEqual(res["postings"][0]["content_sha256"], "b" * 64)
        self.assertEqual(res["requests"], 1)


if __name__ == "__main__":
    unittest.main()
