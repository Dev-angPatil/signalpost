"""Declared caches of public-universe material (candidates only, never evidence).

The Builderr harness allows cached public-universe material when it is declared.
Every file under ``data/cache/`` is listed in ``manifest.json`` with its sha256,
source and build time. Anything read from the cache is a *candidate*: the agent
re-fetches it live and applies the normal identity checks before publishing, so
source URLs, retrieval times and content hashes in claims always come from the
live run.

If the manifest is missing, a file's hash does not match, or
``SIGNALPOST_CACHE=0``, the cache is ignored and the agent behaves exactly as
it would without it.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
NAV_FILE = "nav-jobs.jsonl.gz"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DeclaredCache:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.manifest: dict[str, Any] = {}
        self.files: dict[str, dict[str, Any]] = {}  # name -> manifest entry + "status"
        self.problems: list[str] = []
        self._nav_by_org: dict[str, list[dict[str, Any]]] | None = None
        self._lock = threading.Lock()
        self.usage: dict[str, int] = {"nav_cached_ads_checked": 0, "nav_cached_ads_confirmed": 0, "nav_cached_ads_rejected": 0}
        manifest_path = self.directory / "manifest.json"
        try:
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.problems.append("manifest.json not found")
            return
        except Exception as exc:
            self.problems.append(f"manifest.json unreadable: {exc}")
            return
        for name, entry in (self.manifest.get("files") or {}).items():
            path = self.directory / name
            status = "ok"
            if not path.exists():
                status = "missing"
            elif _sha256(path) != entry.get("sha256"):
                status = "sha256_mismatch"
            if status != "ok":
                self.problems.append(f"{name}: {status}")
            self.files[name] = {**entry, "status": status}

    def has(self, name: str) -> bool:
        return (self.files.get(name) or {}).get("status") == "ok"

    def meta(self, name: str) -> dict[str, Any]:
        return dict(self.files.get(name) or {})

    # NAV vacancy cache -------------------------------------------------------
    def _load_nav(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            if self._nav_by_org is None:
                by_org: dict[str, list[dict[str, Any]]] = {}
                if self.has(NAV_FILE):
                    with gzip.open(self.directory / NAV_FILE, "rt", encoding="utf-8") as fh:
                        for line in fh:
                            row = json.loads(line)
                            by_org.setdefault(row["org"], []).append(row)
                            if row.get("employer_orgnr") and row["employer_orgnr"] != row["org"]:
                                by_org.setdefault(row["employer_orgnr"], []).append(row)
                self._nav_by_org = by_org
            return self._nav_by_org

    def nav_available(self) -> bool:
        return self.has(NAV_FILE) and bool(self.meta(NAV_FILE).get("cursor_page_url"))

    def nav_ads_for(self, orgs: set[str]) -> list[dict[str, Any]]:
        by_org = self._load_nav()
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for org in sorted(orgs):
            for row in by_org.get(org, []):
                if row["uuid"] not in seen:
                    seen.add(row["uuid"])
                    rows.append(row)
        # Newest first, so a per-company fetch cap keeps the freshest ads.
        rows.sort(key=lambda r: (r.get("published") or "", r["uuid"]), reverse=True)
        return rows

    def count(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.usage[key] = self.usage.get(key, 0) + n

    def declaration(self) -> dict[str, Any]:
        """What the run report states about the caches it used."""
        return {
            "enabled": True,
            "directory": str(self.directory.relative_to(DEFAULT_DIR.parents[1])) if self.directory.is_relative_to(DEFAULT_DIR.parents[1]) else str(self.directory),
            "policy": "Cached public-universe material used as candidates only; every published claim is re-fetched and re-verified live.",
            "files": {
                name: {k: entry.get(k) for k in ("description", "source", "built_at", "rows", "sha256", "status")}
                for name, entry in self.files.items()
            },
            "problems": list(self.problems),
            "usage": dict(self.usage),
        }


_STATE: dict[str, Any] = {"loaded": False, "cache": None}
_STATE_LOCK = threading.Lock()


def get_cache() -> DeclaredCache | None:
    """The process-wide cache, or None when disabled or absent."""
    if os.environ.get("SIGNALPOST_CACHE", "1") == "0":
        return None
    with _STATE_LOCK:
        if not _STATE["loaded"]:
            directory = Path(os.environ.get("SIGNALPOST_CACHE_DIR") or DEFAULT_DIR)
            cache = DeclaredCache(directory)
            _STATE["cache"] = cache if cache.manifest else None
            _STATE["loaded"] = True
        return _STATE["cache"]


def reset_cache() -> None:
    """Forget the loaded cache (tests, or a changed SIGNALPOST_CACHE_DIR)."""
    with _STATE_LOCK:
        _STATE["loaded"] = False
        _STATE["cache"] = None


def declaration() -> dict[str, Any]:
    cache = get_cache()
    if cache is None:
        reason = "disabled (SIGNALPOST_CACHE=0)" if os.environ.get("SIGNALPOST_CACHE", "1") == "0" else "no manifest"
        return {"enabled": False, "reason": reason}
    return cache.declaration()
