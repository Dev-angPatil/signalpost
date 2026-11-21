"""Registry-declared websites whose domain spells the whole legal name, and transient fetch retries.

Benchmarked against Builderr's own 100-company crawl (builderr.ai/signalpost): sites the
company registered itself (atea.com for ATEA ASA) were withheld for lack of an org number
on the page, and a single HTTP 429 lost a registered site and its five social profiles.
"""
from __future__ import annotations

import unittest

import run_agent
from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import utc_now
from norway_company_agent.identity import apply_website_identity_gate, build_company_number_profile
from norway_company_agent.site_proof import domain_spells_legal_name, registry_domain_name_proof
from norway_company_agent.website import fetch_website_with_retry, is_transient_fetch_failure


def _profile(org: str, name: str, homepage: str | None = None, email: str | None = None) -> dict:
    reg = {"navn": name, "organisasjonsnummer": org}
    if homepage:
        reg["hjemmeside"] = homepage
    if email:
        reg["epostadresse"] = email
    return {
        "organisation_number": org,
        "name": name,
        "legal_form": name.rsplit(" ", 1)[-1],
        "municipality": "OSLO",
        "website": homepage,
        "evidence": {
            "registry": {"field": "registry", "status": "available", "retrieved_at": utc_now(), "content_sha256": "a" * 64,
                         "source_url": f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}", "value": reg},
        },
    }


def _site(url: str, title: str, text: str) -> dict:
    return {"field": "website", "status": "available", "source_url": url, "retrieved_at": utc_now(), "content_sha256": "c" * 64,
            "value": {"requested_url": url, "final_url": url, "title": title, "main_text_excerpt": text,
                      "identity_text_excerpt": "", "content_sha256": "c" * 64, "pages": [], "social_links": []}}


class DomainSpellsLegalNameTests(unittest.TestCase):
    def test_whole_name_domains_qualify(self) -> None:
        self.assertTrue(domain_spells_legal_name("ATEA ASA", "https://www.atea.com/"))
        self.assertTrue(domain_spells_legal_name("A PLACE TO STAY STAVANGER AS", "https://aplacetostaystavanger.com/"))
        self.assertTrue(domain_spells_legal_name("ZAPTEC ASA", "www.zaptec.com"))
        self.assertTrue(domain_spells_legal_name("LOTUSHJELPEN", "https://www.lotushjelpen.org/"))

    def test_nordic_letters_in_both_spellings_and_idn(self) -> None:
        self.assertTrue(domain_spells_legal_name("VIKA ENTREPRENØR AS", "https://vikaentreprenor.no/"))
        self.assertTrue(domain_spells_legal_name("VIKA ENTREPRENØR AS", "https://www.xn--vikaentreprenr-2qb.no/"))
        self.assertTrue(domain_spells_legal_name("MØLLER BIL AS", "https://moellerbil.no/"))

    def test_part_of_the_name_is_not_enough(self) -> None:
        # Shorter names are where parents, sisters and chains live: "Vedal AS" is not VEDAL INVESTOR AS.
        self.assertFalse(domain_spells_legal_name("VEDAL INVESTOR AS", "https://vedal.no/"))
        self.assertFalse(domain_spells_legal_name("FØRERKORTSENTERET NAMSOS AS", "https://forerkortsenteret.no/"))
        self.assertFalse(domain_spells_legal_name("WORK SYSTEM NORWAY AS", "https://www.worksystem.no/"))
        self.assertFalse(domain_spells_legal_name("TGS ASA", "https://www.tgsnopec.com/"))
        self.assertFalse(domain_spells_legal_name("SAMEIET ST OLAV", "https://bate.no/"))

    def test_generic_or_short_names_never_qualify(self) -> None:
        self.assertFalse(domain_spells_legal_name("BYGG AS", "https://bygg.no/"))
        self.assertFalse(domain_spells_legal_name("HOLDING GROUP AS", "https://holdinggroup.no/"))
        self.assertFalse(domain_spells_legal_name("ABC AS", "https://abc.no/"))


class RegistryDomainNameProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _profile("920237126", "ATEA ASA", homepage="www.atea.com")

    def test_registry_declared_site_with_whole_name_domain_is_proven(self) -> None:
        proof = registry_domain_name_proof(self.profile, "https://www.atea.com/", "Atea - IT-infrastruktur", origin="registry_declared", conflict=False, parked=False)
        self.assertEqual(proof["type"], "registry_declared_domain_spells_legal_name")
        self.assertEqual(proof["origin"], "registry_declared")

    def test_page_that_does_not_name_the_company_is_not_proof(self) -> None:
        # Bot walls, empty pages and frame forwarders (seen live: atea.com, lotushjelpen.org, noror.no).
        for text in ("Verifying...", "", "www.atea.com Your user agent does not support iframes"):
            self.assertIsNone(registry_domain_name_proof(self.profile, "https://www.atea.com/", text, origin="registry_declared", conflict=False, parked=False), text)

    def test_guessed_or_searched_domains_still_need_hard_proof(self) -> None:
        for origin in ("domain_probe", "search_candidate", "registry_subunit", "wikidata"):
            self.assertIsNone(registry_domain_name_proof(self.profile, "https://www.atea.com/", "Atea", origin=origin, conflict=False, parked=False))

    def test_conflict_parked_or_foreign_org_number_refuses(self) -> None:
        url = "https://www.atea.com/"
        self.assertIsNone(registry_domain_name_proof(self.profile, url, "Atea", origin="registry_declared", conflict=True, parked=False))
        self.assertIsNone(registry_domain_name_proof(self.profile, url, "Atea", origin="registry_declared", conflict=False, parked=True))
        self.assertIsNone(registry_domain_name_proof(self.profile, url, "Atea AS org.nr. 976 145 690", origin="registry_declared", conflict=False, parked=False))
        # Its own number is fine.
        self.assertIsNotNone(registry_domain_name_proof(self.profile, url, "Atea ASA org.nr. 920 237 126", origin="registry_declared", conflict=False, parked=False))


class RequireHardSiteProofTests(unittest.TestCase):
    def _run(self, profile: dict, site: dict, origin: str) -> tuple[bool, dict]:
        gated = apply_website_identity_gate(profile, site)
        num = build_company_number_profile(profile)
        metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
        ok = run_agent._require_hard_site_proof(profile, gated, site["source_url"], num, metrics, origin, allow_network=False)
        return ok, gated

    def test_registered_site_naming_the_company_is_published_on_registry_and_domain(self) -> None:
        profile = _profile("916523173", "ALTNET AS", homepage="www.altnet.no")
        site = _site("https://www.altnet.no/", "SEO | WEBUTVIKLING | BACKUP", "Altnet leverer totale løsninger for websider.")
        ok, gated = self._run(profile, site, "registry_declared")
        self.assertTrue(ok)
        assessment = gated["website"]["value"]["identity_assessment"]
        self.assertTrue(assessment["publishable"])
        self.assertEqual(assessment["promotion_proof"][0]["type"], "registry_declared_domain_spells_legal_name")

    def test_registered_bot_walled_site_is_not_published(self) -> None:
        profile = _profile("920237126", "ATEA ASA", homepage="www.atea.com")
        ok, _ = self._run(profile, _site("https://www.atea.com/", "Verifying...", ""), "registry_declared")
        self.assertFalse(ok)

    def test_directory_listing_and_frame_forwarder_are_not_websites(self) -> None:
        cases = [
            (_profile("930116246", "MOSLETH AS", email="post@mosleth.no"), _site("https://www.mosleth.no/", "Index of /",
             "Index of / Name Last Modified Size domeneparkering.php Mosleth")),
            (_profile("936343120", "NORØR AS", email="post@noror.no"), _site("https://www.noror.no/", "www.noror.no",
             "Norør AS. Your user agent does not support iframes.")),
        ]
        for profile, site in cases:
            ok, _ = self._run(profile, site, "registry_email_domain")
            self.assertFalse(ok, profile["name"])

    def test_same_site_found_by_guessing_is_not_published(self) -> None:
        profile = _profile("916523173", "ALTNET AS")
        site = _site("https://www.altnet.no/", "SEO | WEBUTVIKLING | BACKUP", "Altnet leverer totale løsninger for websider.")
        ok, gated = self._run(profile, site, "domain_probe")
        self.assertFalse(ok)
        self.assertFalse(gated["website"]["value"]["identity_assessment"]["publishable"])

    def test_registered_site_showing_another_org_number_stays_quarantined(self) -> None:
        profile = _profile("920237126", "ATEA ASA", homepage="www.atea.com")
        site = _site("https://www.atea.com/", "Atea", "Atea AS, organisasjonsnummer 976145690, Karvesvingen 5, Oslo")
        ok, gated = self._run(profile, site, "registry_declared")
        self.assertFalse(ok)

    def test_registered_parked_page_stays_quarantined(self) -> None:
        profile = _profile("989086561", "HCAG HOLDING AS", homepage="www.hcagholding.no")
        site = _site("http://www.hcagholding.no/", "www.hcagholding.no is parked", "This domain is parked free of charge")
        ok, _ = self._run(profile, site, "registry_declared")
        self.assertFalse(ok)

    def test_manager_site_registered_by_housing_cooperative_stays_quarantined(self) -> None:
        profile = _profile("979214502", "STIFTELSEN HAVNAHUSET BU- OG SERVICESENTER", homepage="vestbo.no")
        site = _site("https://vestbo.no/", "Vestbo: ditt lokale boligbyggelag på Vestlandet - Vestbo BBL", "Vestbo BBL")
        ok, _ = self._run(profile, site, "registry_declared")
        self.assertFalse(ok)


class RegistryWebsiteEnvelopeTests(unittest.TestCase):
    def _envelope(self, profile: dict) -> dict:
        now = utc_now()
        return build_output_envelope(profile, run_id="t", started_at=now, completed_at=now)

    def _claim(self, env: dict, field: str) -> dict:
        return next(c for c in env["claims"] if c["field"] == field)

    def test_registry_proof_cites_the_declaration_verbatim(self) -> None:
        profile = _profile("920237126", "ATEA ASA", homepage="www.atea.com")
        site = _site("https://www.atea.com/", "Verifying...", "")
        site["value"]["identity_assessment"] = {"publishable": True, "status": "exact", "score": 1.0, "promotion_proof": [
            {"type": "registry_declared_domain_spells_legal_name", "origin": "registry_declared", "value": "atea / ATEA ASA", "span": "https://www.atea.com/"}]}
        profile["evidence"]["website"] = site
        env = self._envelope(profile)
        claim = self._claim(env, "official_website")
        self.assertEqual(claim["availability"], "available")
        self.assertEqual(claim["confidence"], 0.85)
        self.assertIn("ev-registry-website", claim["evidence_ids"])
        ev = next(e for e in env["evidence"] if e["id"] == "ev-registry-website")
        self.assertEqual(ev["claim_span"], '"hjemmeside":"www.atea.com"')
        self.assertEqual(ev["source_class"], "official_registry")
        self.assertEqual(len(ev["content_sha256"]), 64)
        # Every cited id resolves to an evidence record.
        ids = {e["id"] for e in env["evidence"]}
        self.assertTrue(set(claim["evidence_ids"]) <= ids)

    def test_email_domain_proof_cites_the_registered_email(self) -> None:
        profile = _profile("937739265", "EPINOR AS", email="post@epinor.net")
        site = _site("https://www.epinor.net/", "", "")
        site["value"]["identity_assessment"] = {"publishable": True, "status": "exact", "score": 1.0, "promotion_proof": [
            {"type": "registry_declared_domain_spells_legal_name", "origin": "registry_email_domain"}]}
        profile["evidence"]["website"] = site
        env = self._envelope(profile)
        ev = next(e for e in env["evidence"] if e["id"] == "ev-registry-website")
        self.assertEqual(ev["claim_span"], '"epostadresse":"post@epinor.net"')

    def test_unreadable_registered_site_is_failed_not_absent(self) -> None:
        profile = _profile("999164137", "ZAPTEC ASA", homepage="www.zaptec.com")
        profile["evidence"]["website"] = {"field": "website", "status": "source_error", "source_url": "https://www.zaptec.com/",
                                          "retrieved_at": utc_now(), "note": "HTTP 429"}
        env = self._envelope(profile)
        for field in ("official_website", "website_description", "career_url", "open_positions"):
            claim = self._claim(env, field)
            self.assertEqual(claim["availability"], "failed", field)
            self.assertIsNone(claim["value"])

    def test_missing_site_stays_not_available(self) -> None:
        profile = _profile("923304290", "AKSLAIL AS")
        profile["evidence"]["website"] = {"field": "website", "status": "not_found", "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter",
                                          "retrieved_at": utc_now(), "note": "No valid registry website URL"}
        env = self._envelope(profile)
        self.assertEqual(self._claim(env, "official_website")["availability"], "not_available")

    def test_blocked_site_publishes_no_url(self) -> None:
        profile = _profile("982472369", "HBEF AS", homepage="www.hbef.no")
        profile["evidence"]["website"] = {"field": "website", "status": "blocked", "source_url": "https://www.hbef.no/",
                                          "retrieved_at": utc_now(), "note": "robots.txt disallows this user agent"}
        env = self._envelope(profile)
        claim = self._claim(env, "official_website")
        self.assertEqual(claim["availability"], "blocked")
        self.assertIsNone(claim["value"])


class FetchRetryTests(unittest.TestCase):
    def _fake(self, *records: dict):
        calls: list[float] = []
        seq = list(records)

        def fetch(url, *, timeout, **kwargs):
            calls.append(timeout)
            return seq.pop(0), {"requests": 2, "bytes": 10, "latencies_ms": [5]}
        return fetch, calls

    def test_429_is_retried_once_with_longer_timeout_and_summed_metrics(self) -> None:
        ok = _site("https://www.zaptec.com/", "Zaptec", "Zaptec ASA")
        fetch, calls = self._fake({"status": "source_error", "note": "HTTP 429"}, ok)
        slept: list[float] = []
        record, metrics = fetch_website_with_retry("https://www.zaptec.com/", timeout=5.0, fetch=fetch, sleep=slept.append)
        self.assertEqual(record["status"], "available")
        self.assertEqual(record["fetch_attempts"], 2)
        self.assertEqual(calls, [5.0, 8.0])
        self.assertEqual(slept, [3.0])
        self.assertEqual(metrics["requests"], 4)

    def test_timeout_that_persists_stays_source_error(self) -> None:
        fetch, calls = self._fake({"status": "source_error", "note": "URLError: timed out"}, {"status": "source_error", "note": "URLError: timed out"})
        record, metrics = fetch_website_with_retry("https://tgs.com/", fetch=fetch, sleep=lambda s: None)
        self.assertEqual(record["status"], "source_error")
        self.assertEqual(len(calls), 2)
        self.assertEqual(metrics["requests"], 4)

    def test_definitive_answers_are_not_retried(self) -> None:
        for rec in ({"status": "source_error", "note": "HTTP 403"}, {"status": "not_found", "note": "HTTP 404"},
                    {"status": "blocked", "note": "robots.txt disallows this user agent"},
                    {"status": "source_error", "note": "URLError: [Errno -2] Name or service not known"}):
            self.assertFalse(is_transient_fetch_failure(rec), rec)
            fetch, calls = self._fake(rec)
            fetch_website_with_retry("https://x.no/", fetch=fetch, sleep=lambda s: None)
            self.assertEqual(len(calls), 1, rec)

    def test_zero_retries_when_the_clock_is_short(self) -> None:
        fetch, calls = self._fake({"status": "source_error", "note": "HTTP 503"})
        fetch_website_with_retry("https://x.no/", fetch=fetch, retries=0, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
