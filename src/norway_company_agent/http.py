from __future__ import annotations

import email.utils
import json
import hashlib
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass
class FetchResult:
    url: str
    status: int
    elapsed_ms: int
    bytes_received: int
    body: Any = None
    error: str | None = None
    content_sha256: str | None = None
    retrieved_at: str | None = None
    effective_at: str | None = None
    attempts: int = 1


# Statuses worth retrying: throttling and transient server/gateway faults.
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
# Definitive answers: the record does not exist.
TERMINAL_NOT_FOUND = {404, 410}
MAX_RETRY_AFTER_SECONDS = 30.0

# Injectable for hermetic tests.
_urlopen: Callable[..., Any] = urllib.request.urlopen
_sleep: Callable[[float], None] = time.sleep
_clock: Callable[[], float] = time.monotonic


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class HostPacer:
    """Spaces request starts to one host across all threads (min interval per host)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_start: dict[str, float] = {}

    def interval_for(self, host: str) -> float:
        if host.endswith("data.brreg.no"):
            rps = _env_float("SIGNALPOST_BRREG_RPS", 8.0)
            return 1.0 / rps if rps > 0 else 0.0
        return 0.0

    def wait(self, host: str) -> None:
        interval = self.interval_for(host)
        if interval <= 0:
            return
        with self._lock:
            now = _clock()
            start = max(now, self._next_start.get(host, 0.0))
            self._next_start[host] = start + interval
        delay = start - now
        if delay > 0:
            _sleep(delay)

    def back_off(self, host: str, seconds: float) -> None:
        """After a throttle answer, push every thread's next start to this host back."""
        with self._lock:
            self._next_start[host] = max(self._next_start.get(host, 0.0), _clock() + seconds)

    def reset(self) -> None:
        with self._lock:
            self._next_start.clear()


PACER = HostPacer()


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _backoff_seconds(attempt: int) -> float:
    base = _env_float("SIGNALPOST_HTTP_BACKOFF_BASE", 1.0)
    return base * (2**attempt) * (0.75 + 0.5 * random.random())


def fetch_json(url: str, *, timeout: float = 20.0, attempts: int | None = None) -> FetchResult:
    """GET a JSON document, retrying throttling and transient faults.

    Retries 408/425/429/5xx, timeouts, connection errors and truncated JSON with
    exponential backoff (honouring Retry-After, capped). 404/410 and other 4xx are
    returned at once. The final failure keeps its real status code and an error text
    that names the cause and the number of attempts.
    """
    if attempts is None:
        attempts = max(1, int(_env_float("SIGNALPOST_HTTP_ATTEMPTS", 5)))
    host = urllib.parse.urlsplit(url).hostname or ""
    last_error = "request failed"
    last_status = 0
    for attempt in range(attempts):
        PACER.wait(host)
        started = _clock()
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)"},
        )
        retry_after: float | None = None
        try:
            with _urlopen(request, timeout=timeout) as response:
                raw = response.read()
                elapsed = int((_clock() - started) * 1000)
                return FetchResult(url, response.status, elapsed, len(raw), json.loads(raw), content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now(), attempts=attempt + 1)
        except urllib.error.HTTPError as exc:
            elapsed = int((_clock() - started) * 1000)
            try:
                raw = exc.read() or b""
            except Exception:
                raw = b""
            if exc.code in TERMINAL_NOT_FOUND:
                return FetchResult(url, exc.code, elapsed, len(raw), error=f"HTTP {exc.code}", content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now(), attempts=attempt + 1)
            last_status = exc.code
            last_error = f"HTTP {exc.code}"
            try:  # keep the server's own explanation, e.g. an unsupported accounts template
                message = json.loads(raw).get("message") if raw else None
            except (ValueError, AttributeError):
                message = None
            if message:
                last_error = f"HTTP {exc.code}: {str(message)[:160]}"
            if exc.code not in RETRYABLE_STATUSES:
                return FetchResult(url, exc.code, elapsed, len(raw), error=last_error, retrieved_at=_utc_now(), attempts=attempt + 1)
            retry_after = _retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            last_status = 0
            last_error = type(exc).__name__
        if attempt + 1 < attempts:
            delay = _backoff_seconds(attempt)
            if retry_after is not None:
                delay = max(delay, min(retry_after, MAX_RETRY_AFTER_SECONDS))
            if last_status == 429:
                PACER.back_off(host, delay)
            _sleep(delay)
    return FetchResult(url, last_status, 0, 0, error=f"{last_error} after {attempts} attempts", retrieved_at=_utc_now(), attempts=attempts)
