"""The company browser written after every run: Verify panels, coverage states and run checks.

Everything the page says about a fact is read from the scored envelope, so these tests
build envelopes with the real contract builder and check what reaches the page.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
if str(UI) not in sys.path:
    sys.path.insert(0, str(UI))

import build_ui  # noqa: E402
import envelope_view  # noqa: E402
import run_agent  # noqa: E402
from norway_company_agent.contract import build_output_envelope  # noqa: E402
from norway_company_agent.evidence import utc_now  # noqa: E402


def _profile(org: str = "916523173", *, website: str = "available") -> dict:
    now = utc_now()
    evidence = {
        "registry": {"field": "registry", "status": "available", "retrieved_at": now, "content_sha256": "a" * 64,
                     "source_url": f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}",
                     "value": {"navn": "ALTNET AS", "hjemmeside": "www.altnet.no"}},
    }
    if website == "available":
        evidence["website"] = {"field": "website", "status": "available", "source_url": "https://www.altnet.no/", "retrieved_at": now,
                               "content_sha256": "c" * 64,
                               "value": {"final_url": "https://www.altnet.no/", "title": "Altnet AS", "main_text_excerpt": "Altnet AS",
                                         "content_sha256": "c" * 64, "pages": [], "social_links": [],
                                         "identity_assessment": {"publishable": True, "status": "exact", "score": 1.0}}}
    elif website == "ambiguous":
        evidence["website"] = {"field": "website", "status": "available", "source_url": "https://www.vestbo.no/", "retrieved_at": now,
                               "content_sha256": "c" * 64,
                               "value": {"final_url": "https://www.vestbo.no/", "title": "Vestbo BBL", "content_sha256": "c" * 64, "pages": [],
                                         "identity_assessment": {"publishable": False, "status": "related_or_uncertain", "score": 0.3}}}
    elif website == "failed":
        evidence["website"] = {"field": "website", "status": "source_error", "source_url": "https://www.altnet.no/", "retrieved_at": now, "note": "HTTP 429"}
    return {"organisation_number": org, "name": "ALTNET AS", "legal_form": "AS", "municipality": "OSLO", "evidence": evidence}


def _envelope(profile: dict) -> dict:
    now = utc_now()
    return build_output_envelope(json.loads(json.dumps(profile)), run_id="t", started_at=now, completed_at=now)


class VerifyPayloadTests(unittest.TestCase):
    def test_every_published_claim_is_listed_with_the_evidence_it_cites(self) -> None:
        env = _envelope(_profile())
        payload = envelope_view.verify_payload(env)
        published = [c for c in env["claims"] if c["availability"] == "available"]
        self.assertEqual(len(payload["claims"]), len(published))
        for field, group, value, conf, ids in payload["claims"]:
            self.assertIn(group, envelope_view.GROUP_LABELS)
            for i in ids:
                url, cls, at, sha, span, period, effective = payload["evidence"][i]
                self.assertTrue(url and at and re.fullmatch(r"[0-9a-f]{64}", sha), (field, i))
        site = next(c for c in payload["claims"] if c[0] == "official_website")
        self.assertEqual(site[1], "website")
        self.assertEqual(site[2], "https://www.altnet.no/")

    def test_unpublished_claims_and_uncited_evidence_stay_off_the_page(self) -> None:
        env = _envelope(_profile(website="ambiguous"))
        payload = envelope_view.verify_payload(env)
        self.assertNotIn("official_website", [c[0] for c in payload["claims"]])
        cited = {i for c in payload["claims"] for i in c[4]}
        self.assertEqual(set(payload["evidence"]), cited)

    def test_values_are_plain_text_and_bounded(self) -> None:
        self.assertEqual(envelope_view._value_text(["a@x.no", "b@x.no"]), "a@x.no, b@x.no")
        self.assertEqual(envelope_view._value_text(None), "")
        self.assertLessEqual(len(envelope_view._value_text("x" * 5000)), 600)


class CoverageTests(unittest.TestCase):
    def _states(self, website: str) -> dict:
        return {a["key"]: a for a in envelope_view.coverage(_envelope(_profile(website=website)))}

    def test_five_areas_in_order_with_reasons_only_when_missing(self) -> None:
        areas = envelope_view.coverage(_envelope(_profile()))
        self.assertEqual([a["key"] for a in areas], ["company", "accounts", "people", "website", "footprint"])
        for a in areas:
            self.assertEqual(a["reason"] == "", a["state"] == "available", a)

    def test_website_states_reach_the_page(self) -> None:
        self.assertEqual(self._states("available")["website"]["state"], "available")
        self.assertEqual(self._states("ambiguous")["website"]["state"], "ambiguous")
        failed = self._states("failed")
        self.assertEqual(failed["website"]["state"], "failed")
        self.assertIn("could not be read", failed["website"]["reason"])
        # Nothing could be read from the site, so the footprint is unknown too, not absent.
        self.assertEqual(failed["footprint"]["state"], "failed")
        self.assertEqual(self._states("none")["website"]["state"], "not_available")


class RunSummaryTests(unittest.TestCase):
    def test_checks_are_recomputed_from_the_envelopes(self) -> None:
        envs = [_envelope(_profile("916523173")), _envelope(_profile("937739265", website="ambiguous"))]
        summary = envelope_view.run_summary(envs, {"expected_count": 2, "qualification": {"zero_wrong_company_matches": True}})
        self.assertEqual(summary["companies"], 2)
        self.assertTrue(all(c["ok"] for c in summary["checks"]), summary["checks"])
        self.assertEqual(summary["cant_tell"], 1)
        self.assertEqual({a["key"]: a["companies"] for a in summary["coverage"]}["website"], 1)

    def test_a_broken_envelope_fails_its_check(self) -> None:
        env = _envelope(_profile())
        bad = json.loads(json.dumps(env))
        next(c for c in bad["claims"] if c["field"] == "official_website")["evidence_ids"] = []
        leaky = json.loads(json.dumps(env))
        next(c for c in leaky["claims"] if c["availability"] != "available")["value"] = "https://elsewhere.no/"
        self.assertFalse(envelope_view.published_facts_are_backed(bad))
        self.assertFalse(envelope_view.unavailable_claims_carry_no_value(leaky))
        summary = envelope_view.run_summary([bad], {"expected_count": 2})
        self.assertFalse(all(c["ok"] for c in summary["checks"]))


def _page_data(html: str) -> list[dict]:
    m = re.search(r"const RAW = (\[.*?\]);\nconst BUILT", html, re.S)
    return json.loads(m.group(1))


class BuildPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _profile()
        self.env = _envelope(self.profile)

    def test_small_run_embeds_evidence_coverage_and_run_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            side = Path(tmp) / "index_files"
            html = build_ui.build([self.profile], envelopes=[self.env], run_meta={"expected_count": 1, "commit": "584a67d"}, sidecar_dir=side)
            self.assertFalse(side.exists())
        row = _page_data(html)[0]
        self.assertIsInstance(row["vf"], dict)
        self.assertEqual([a[0] for a in row["cov"]], ["company", "accounts", "people", "website", "footprint"])
        self.assertIn('"commit": "584a67d"', html)
        self.assertIn("const SIDECAR = null", html)
        self.assertNotIn("/*__SIGNALPOST_", html)

    def test_large_run_writes_one_evidence_file_per_company(self) -> None:
        rows, envs = [], []
        for i in range(build_ui.INLINE_LIMIT + 1):
            org = f"9{i:08d}"
            p = _profile(org)
            rows.append(p)
            envs.append(_envelope(p))
        with tempfile.TemporaryDirectory() as tmp:
            side = Path(tmp) / "index_files"
            html = build_ui.build(rows, envelopes=envs, sidecar_dir=side)
            files = sorted(side.glob("*.js"))
            self.assertEqual(len(files), len(rows))
            text = files[0].read_text(encoding="utf-8")
            self.assertTrue(text.startswith("window.__SP_VERIFY__&&window.__SP_VERIFY__("))
        self.assertIn('const SIDECAR = "index_files"', html)
        self.assertTrue(all(r["vf"] == 1 for r in _page_data(html)))

    def test_page_text_cannot_break_out_of_the_script(self) -> None:
        self.profile["evidence"]["website"]["value"]["title"] = "</script><script>alert(1)</script>"
        env = _envelope(self.profile)
        html = build_ui.build([self.profile], envelopes=[env])
        self.assertNotIn("</script><script>alert(1)", html)

    def test_facts_cite_their_source_and_areas_link_to_their_section(self) -> None:
        html = build_ui.build([self.profile], envelopes=[self.env])
        # Facts carry a source chip and open the evidence; there is no separate Verify button.
        self.assertIn('class="cite-chip"', html)
        self.assertIn('class="vt"', html)
        self.assertNotIn("vbtn", html)
        # Each coverage area is a button that takes the reader to its part of the page.
        self.assertIn('data-area="${esc(a.key)}"', html)
        for section in ("sec-company", "sec-accounts", "sec-people", "row-website", "sec-activity"):
            self.assertIn(section, html)

    def test_page_builds_without_envelopes(self) -> None:
        html = build_ui.build([self.profile])
        self.assertIn("const RUN = null", html)
        self.assertNotIn('"vf"', html)


class RunAgentWritesPageTests(unittest.TestCase):
    def test_page_is_written_next_to_the_envelopes_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "envelopes.html"
            mode = run_agent._write_showcase([self.profile_for()], html_path, [_envelope(self.profile_for())], {"expected_count": 1})
            self.assertEqual(mode, "ui")
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("All run checks passed", html)  # the template's label for a clean run
            self.assertIn('"cov"', html)

    @staticmethod
    def profile_for() -> dict:
        return _profile()


if __name__ == "__main__":
    unittest.main()
