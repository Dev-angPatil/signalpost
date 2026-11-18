"""Declared cache: integrity checks and the NAV cached-ad path (hermetic)."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from norway_company_agent import cache as cache_module
from norway_company_agent import nav_jobs

ORG = "923609016"
SUB = "973001111"
OTHER = "999999999"
FUTURE = "2099-12-31"


def _write_cache(directory: Path, rows: list[dict], *, corrupt: bool = False) -> None:
    path = directory / cache_module.NAV_FILE
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"version": 1, "files": {cache_module.NAV_FILE: {
        "sha256": "0" * 64 if corrupt else sha, "rows": len(rows), "built_at": "2026-11-16T00:00:00Z",
        "source": "test", "cursor_page_url": "/api/v1/feed/cursor-page",
    }}}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _row(uuid: str, org: str = ORG, employer: str | None = None, expires: str = FUTURE) -> dict:
    return {"org": org, "employer_orgnr": employer or org, "uuid": uuid, "title": f"Job {uuid}",
            "published": "2026-11-01", "expires": expires, "modified": "2026-11-01T10:00:00+01:00"}


def _entry(uuid: str, orgnr: str, status: str = "ACTIVE") -> bytes:
    if status != "ACTIVE":
        return json.dumps({"uuid": uuid, "status": status}).encode()
    return json.dumps({"uuid": uuid, "status": status, "ad_content": {
        "title": f"Live title {uuid}", "published": "2026-11-01T08:00:00", "expires": FUTURE + "T00:00:00",
        "link": f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}",
        "employer": {"orgnr": orgnr, "name": "NORDLYS TESTFIRMA AS"},
    }}).encode()


class DeclaredCacheIntegrity(unittest.TestCase):
    def test_hash_mismatch_disables_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(Path(tmp), [_row("a")], corrupt=True)
            cache = cache_module.DeclaredCache(tmp)
            self.assertFalse(cache.has(cache_module.NAV_FILE))
            self.assertFalse(cache.nav_available())
            self.assertTrue(any("sha256_mismatch" in p for p in cache.problems))
            self.assertEqual(cache.nav_ads_for({ORG}), [])

    def test_missing_manifest_means_no_cache(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"SIGNALPOST_CACHE": "1", "SIGNALPOST_CACHE_DIR": tmp}):
            cache_module.reset_cache()
            try:
                self.assertIsNone(cache_module.get_cache())
                self.assertFalse(cache_module.declaration()["enabled"])
            finally:
                cache_module.reset_cache()

    def test_disabled_by_env(self):
        with mock.patch.dict(os.environ, {"SIGNALPOST_CACHE": "0"}):
            cache_module.reset_cache()
            try:
                self.assertIsNone(cache_module.get_cache())
            finally:
                cache_module.reset_cache()

    def test_lookup_by_parent_and_subunit_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            older = {**_row("old"), "published": "2026-01-01"}
            _write_cache(Path(tmp), [older, _row("new"), _row("viasub", employer=SUB)])
            cache = cache_module.DeclaredCache(tmp)
            self.assertTrue(cache.nav_available())
            self.assertEqual([r["uuid"] for r in cache.nav_ads_for({ORG})][-1], "old")
            self.assertEqual([r["uuid"] for r in cache.nav_ads_for({SUB})], ["viasub"])


class CachedNavAds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _write_cache(Path(self.tmp.name), [
            _row("live-ok"),
            _row("closed-live"),           # feed entry is INACTIVE now
            _row("closed-in-walk"),        # incremental walk saw it close
            _row("other-employer"),        # live employer differs
            _row("expired", expires="2020-01-01"),
            _row("sub-ad", employer=SUB),  # sub-unit ad mapped to parent in the cache
        ])
        self.cache = cache_module.DeclaredCache(self.tmp.name)
        self.saved_state = dict(nav_jobs._FEED_STATE)
        nav_jobs._FEED_STATE.update({"built": True, "index": {
            "entries": {}, "tokens": {}, "statuses": {"closed-in-walk": "INACTIVE"}, "by_org": {},
            "prefetched_uuids": set(), "complete": True, "prefetch_complete": True,
            "since": "declared cache", "token": "t", "cache": self.cache,
        }})
        self.fetched: list[str] = []
        payloads = {
            "live-ok": _entry("live-ok", ORG),
            "closed-live": _entry("closed-live", ORG, status="INACTIVE"),
            "other-employer": _entry("other-employer", OTHER),
            "sub-ad": _entry("sub-ad", SUB),
            "expired": _entry("expired", ORG),
            "closed-in-walk": _entry("closed-in-walk", ORG),
        }

        def fake_get(url, timeout, accept, channel="page", headers=None):
            uuid = url.rsplit("/", 1)[-1]
            self.fetched.append(uuid)
            return payloads.get(uuid), 1

        self.patch = mock.patch.object(nav_jobs, "_get", side_effect=fake_get)
        self.patch.start()
        self.parents = {SUB: ORG}
        self.parent_patch = mock.patch.object(nav_jobs, "_live_subunit_parent", side_effect=lambda sub, timeout: (self.parents.get(sub), 1))
        self.parent_patch.start()

    def tearDown(self):
        self.patch.stop()
        self.parent_patch.stop()
        nav_jobs._FEED_STATE.clear()
        nav_jobs._FEED_STATE.update(self.saved_state)
        self.tmp.cleanup()

    def test_only_live_confirmed_ads_are_published(self):
        result = nav_jobs.fetch_nav_job_postings({"organisation_number": ORG, "name": "NORDLYS TESTFIRMA AS"}, set())
        urls = sorted(p["url"].rsplit("/", 1)[-1] for p in result["postings"])
        self.assertEqual(urls, ["live-ok", "sub-ad"])
        self.assertEqual(result["status"], "available")
        # Closed-in-walk and expired ads are not even fetched.
        self.assertNotIn("closed-in-walk", self.fetched)
        self.assertNotIn("expired", self.fetched)
        rejected = {r["uuid"] for r in result["rejected"]}
        self.assertEqual(rejected, {"closed-live", "other-employer"})
        # Evidence comes from the live payload, not the cache.
        ok = next(p for p in result["postings"] if p["url"].endswith("live-ok"))
        self.assertEqual(ok["title"], "Live title live-ok")
        self.assertEqual(ok["content_sha256"], hashlib.sha256(_entry("live-ok", ORG)).hexdigest())
        self.assertEqual(self.cache.usage["nav_cached_ads_confirmed"], 2)

    def test_live_subunit_list_is_honoured_and_unrelated_company_gets_nothing(self):
        # The run's own registry sub-unit list for a company is authoritative.
        result = nav_jobs.fetch_nav_job_postings({"organisation_number": OTHER, "name": "ANNET FIRMA AS"}, {SUB})
        self.assertEqual([p["url"].rsplit("/", 1)[-1] for p in result["postings"]], ["sub-ad"])
        result2 = nav_jobs.fetch_nav_job_postings({"organisation_number": "811111112", "name": "TREDJE FIRMA AS"}, set())
        self.assertEqual(result2["postings"], [])

    def test_subunit_moved_to_another_parent_since_build_is_rejected(self):
        self.parents[SUB] = OTHER  # live Brreg: the sub-unit now belongs to someone else
        result = nav_jobs.fetch_nav_job_postings({"organisation_number": ORG, "name": "NORDLYS TESTFIRMA AS"}, set())
        self.assertEqual([p["url"].rsplit("/", 1)[-1] for p in result["postings"]], ["live-ok"])
        self.assertIn("sub-ad", {r["uuid"] for r in result["rejected"]})

    def test_fetch_cap(self):
        with mock.patch.object(nav_jobs, "MAX_CACHED_AD_FETCHES", 1):
            nav_jobs.fetch_nav_job_postings({"organisation_number": ORG, "name": "NORDLYS TESTFIRMA AS"}, set())
        self.assertEqual(len(self.fetched), 1)


class CacheAge(unittest.TestCase):
    def test_old_cache_walks_normal_window_not_from_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(Path(tmp), [_row("a")])  # built 2026-11-16
            cache = cache_module.DeclaredCache(tmp)
            calls = []
            saved = dict(nav_jobs._FEED_STATE)
            nav_jobs._FEED_STATE.update({"built": False, "index": None})
            try:
                with mock.patch.object(nav_jobs, "_declared_cache", return_value=cache), \
                     mock.patch.object(nav_jobs, "_cache_age_days", return_value=nav_jobs.FEED_DAYS + 1), \
                     mock.patch.object(nav_jobs, "_build_feed_index", side_effect=lambda *a, **k: calls.append(k) or None):
                    nav_jobs.get_feed_index()
            finally:
                nav_jobs._FEED_STATE.clear()
                nav_jobs._FEED_STATE.update(saved)
            self.assertNotIn("start_path", calls[0])


class IncrementalWalk(unittest.TestCase):
    def test_walk_starts_at_cache_cursor_without_if_modified_since(self):
        calls = []

        def fake_get(url, timeout, accept, channel="page", headers=None):
            calls.append((url, dict(headers or {})))
            if url.endswith("/api/publicToken"):
                return b"token-xyz\n", 1
            return json.dumps({"items": [{"_feed_entry": {"uuid": "n1", "status": "ACTIVE", "businessName": "NORDLYS TESTFIRMA AS"}}], "next_url": None}).encode(), 1

        with mock.patch.object(nav_jobs, "_get", side_effect=fake_get):
            index = nav_jobs._build_feed_index(45, start_path="/api/v1/feed/cursor-page", since_label="cache")
        feed_url, headers = calls[1]
        self.assertEqual(feed_url, nav_jobs.FEED_BASE + "/api/v1/feed/cursor-page")
        self.assertNotIn("If-Modified-Since", headers)
        self.assertTrue(index["complete"])
        self.assertEqual(index["statuses"], {"n1": "ACTIVE"})
        self.assertIn("n1", index["tokens"]["nordlys"])


if __name__ == "__main__":
    unittest.main()
