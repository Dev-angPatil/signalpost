#!/usr/bin/env python3
"""Write data/cache/manifest.json: the declaration of every cache file.

For each data file it records sha256, size, row count, source and build time
(from the file's ``<name>.meta.json`` written by its builder). The runtime
loader (norway_company_agent.cache) refuses any file whose hash differs.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache"
MAX_TOTAL_BYTES = 40 * 1024 * 1024

DESCRIPTIONS = {
    "nav-jobs.jsonl.gz": "Active NAV job ads keyed by employer org number (sub-units mapped to parent). Candidates only: each ad is re-fetched live from the NAV feed and must still be ACTIVE with a matching employer.orgnr before publication.",
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", type=Path, default=CACHE)
    args = ap.parse_args()
    files = {}
    total = 0
    for path in sorted(args.cache_dir.glob("*.jsonl.gz")):
        meta_path = path.with_name(path.name.replace(".jsonl.gz", ".meta.json"))
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            rows = sum(1 for _ in fh)
        size = path.stat().st_size
        total += size
        files[path.name] = {
            "description": DESCRIPTIONS.get(path.name, meta.get("description", "")),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": size,
            "rows": rows,
            **{k: v for k, v in meta.items() if k != "description"},
        }
    if total > MAX_TOTAL_BYTES:
        raise SystemExit(f"cache is {total / 1e6:.1f} MB, over the {MAX_TOTAL_BYTES / 1e6:.0f} MB budget")
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        commit = None
    manifest = {
        "version": 1,
        "declared_as": "Cached public-universe material (Builderr harness: external caches must be declared)",
        "policy": "Candidates only. No cached value is published; every claim is re-fetched live and passes the normal identity checks.",
        "packed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "builder_commit": commit,
        "files": files,
    }
    (args.cache_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
