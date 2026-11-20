"""Retry, throttling and repair-pass behaviour for official registry fetches (hermetic)."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from norway_company_agent import http
from norway_company_agent.evidence import evidence


class _Resp:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body, self.status = body, status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://data.brreg.no/x", code, "err", headers, io.BytesIO(b""))


class FetchJsonRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sleeps: list[float] = []
        http.PACER.reset()
        patcher_sleep = patch.object(http, "_sleep", self.sleeps.append)
        patcher_sleep.start()
        self.addCleanup(patcher_sleep.stop)
        env = patch.dict("os.environ", {"SIGNALPOST_BRREG_RPS": "0"})
        env.start()
        self.addCleanup(env.stop)

    def _run(self, outcomes: list, **kwargs) -> tuple[http.FetchResult, int]:
        calls = {"n": 0}

        def fake_urlopen(request, timeout):
            outcome = outcomes[min(calls["n"], len(outcomes) - 1)]
            calls["n"] += 1
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with patch.object(http, "_urlopen", fake_urlopen):
            result = http.fetch_json("https://data.brreg.no/regnskapsregisteret/regnskap/123456789", **kwargs)
        return result, calls["n"]

    def test_recovers_after_throttling(self) -> None:
        ok = _Resp(json.dumps([{"id": 1}]).encode())
        result, calls = self._run([_http_error(429), _http_error(503), ok])
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body, [{"id": 1}])
        self.assertEqual(calls, 3)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_honours_retry_after_with_cap(self) -> None:
        ok = _Resp(b"{}")
        self._run([_http_error(429, retry_after="7"), ok])
        self.assertGreaterEqual(self.sleeps[0], 7.0)
        self.sleeps.clear()
        self._run([_http_error(429, retry_after="3600"), ok])
        self.assertLessEqual(self.sleeps[0], http.MAX_RETRY_AFTER_SECONDS)

    def test_not_found_is_returned_without_retry(self) -> None:
        result, calls = self._run([_http_error(404)])
        self.assertEqual((result.status, calls), (404, 1))
        self.assertEqual(self.sleeps, [])

    def test_other_client_errors_are_not_retried(self) -> None:
        result, calls = self._run([_http_error(400)])
        self.assertEqual((result.status, calls), (400, 1))

    def test_final_failure_keeps_status_and_names_attempts(self) -> None:
        result, calls = self._run([_http_error(503)], attempts=4)
        self.assertEqual(calls, 4)
        self.assertEqual(result.status, 503)
        self.assertIn("HTTP 503", result.error)
        self.assertIn("4 attempts", result.error)

    def test_connection_errors_and_truncated_json_are_retried(self) -> None:
        ok = _Resp(b'{"a": 1}')
        result, calls = self._run([urllib.error.URLError("reset"), _Resp(b'{"a":'), ok])
        self.assertEqual((result.status, calls, result.body), (200, 3, {"a": 1}))


class HostPacerTests(unittest.TestCase):
    def test_brreg_request_starts_are_spaced(self) -> None:
        now = {"t": 100.0}
        sleeps: list[float] = []
        pacer = http.HostPacer()
        with patch.object(http, "_clock", lambda: now["t"]), patch.object(http, "_sleep", sleeps.append), \
                patch.dict("os.environ", {"SIGNALPOST_BRREG_RPS": "4"}):
            for _ in range(3):
                pacer.wait("data.brreg.no")
            pacer.wait("example.no")
        self.assertEqual([round(s, 3) for s in sleeps], [0.25, 0.5])

    def test_back_off_delays_every_thread(self) -> None:
        now = {"t": 0.0}
        sleeps: list[float] = []
        pacer = http.HostPacer()
        with patch.object(http, "_clock", lambda: now["t"]), patch.object(http, "_sleep", sleeps.append), \
                patch.dict("os.environ", {"SIGNALPOST_BRREG_RPS": "4"}):
            pacer.back_off("data.brreg.no", 10.0)
            pacer.wait("data.brreg.no")
        self.assertEqual(sleeps, [10.0])


class RepairPassTests(unittest.TestCase):
    def _profile(self, financials_status: str) -> dict:
        return {
            "organisation_number": "921150601",
            "name": "VIGCON AS",
            "evidence": {
                "registry": evidence("registry", "available", "official_registry_live", "https://data.brreg.no/enhetsregisteret/api/enheter/921150601"),
                "financials": evidence("financials", financials_status, "official_annual_accounts",
                                       "https://data.brreg.no/regnskapsregisteret/regnskap/921150601", note="HTTP 429 after 5 attempts"),
                "roles": evidence("roles", "available", "official_roles", "https://data.brreg.no/enhetsregisteret/api/enheter/921150601/roller"),
            },
            "run_metrics": {"requests": 4, "bytes": 10, "latencies_ms": [1], "third_party_cost_usd": 0.0},
        }

    def _fake_modules(self, status: str):
        def fake(org, modules, fetcher=None):
            records = {m: evidence(m, status, "official_annual_accounts", f"https://data.brreg.no/{m}", value={"records": []}) for m in modules}
            results = [http.FetchResult(f"https://data.brreg.no/{m}", 200 if status == "available" else 503, 5, 50) for m in modules]
            return records, results
        return fake

    def test_failed_module_is_refetched_and_recovered(self) -> None:
        import run_agent
        enriched = {"921150601": self._profile("source_error")}
        ops = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
        with patch.object(run_agent, "fetch_official_modules", self._fake_modules("available")):
            stats = run_agent.repair_official_failures(enriched, ["registry", "financials"], ops, sleeper=lambda s: None)
        prof = enriched["921150601"]
        self.assertEqual(prof["evidence"]["financials"]["status"], "available")
        self.assertEqual((stats["modules_retried"], stats["modules_recovered"]), (1, 1))
        self.assertEqual(stats["still_failed"], {})
        self.assertEqual(prof["run_metrics"]["requests"], 5)
        self.assertEqual(ops["requests"], 1)
        self.assertFalse(prof.get("errors"))

    def test_persistent_failure_is_recorded_in_errors(self) -> None:
        import run_agent
        enriched = {"921150601": self._profile("source_error")}
        ops = {"requests": 0, "bytes": 0, "latencies_ms": []}
        with patch.object(run_agent, "fetch_official_modules", self._fake_modules("source_error")):
            stats = run_agent.repair_official_failures(enriched, ["registry"], ops, sleeper=lambda s: None)
        errors = enriched["921150601"]["errors"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["code"], "SOURCE_ERROR")
        self.assertEqual(errors[0]["module"], "financials")
        self.assertIn("regnskap/921150601", errors[0]["source_url"])
        self.assertEqual(stats["still_failed"], {"financials": 1})
        # Running the pass again does not duplicate the error entry.
        with patch.object(run_agent, "fetch_official_modules", self._fake_modules("source_error")):
            run_agent.repair_official_failures(enriched, ["registry"], ops, sleeper=lambda s: None)
        self.assertEqual(len(enriched["921150601"]["errors"]), 1)

    def test_healthy_profiles_are_left_alone(self) -> None:
        import run_agent
        enriched = {"921150601": self._profile("available")}
        calls: list = []
        with patch.object(run_agent, "fetch_official_modules", lambda *a, **k: calls.append(a) or ({}, [])):
            stats = run_agent.repair_official_failures(enriched, ["registry"], {}, sleeper=lambda s: calls.append("sleep"))
        self.assertEqual(calls, [])
        self.assertEqual(stats["companies_retried"], 0)

    def test_not_found_accounts_are_not_retried(self) -> None:
        import run_agent
        enriched = {"921150601": self._profile("not_found")}
        stats = run_agent.repair_official_failures(enriched, ["registry"], {}, sleeper=lambda s: None)
        self.assertEqual(stats["companies_retried"], 0)


if __name__ == "__main__":
    unittest.main()
