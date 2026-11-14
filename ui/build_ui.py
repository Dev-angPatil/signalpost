#!/usr/bin/env python3
"""Build the user-facing Signalpost company browser.

Standalone: reads a profiles JSONL and writes one self-contained HTML file.
Nothing in src/ or scripts/ is modified; the data mapping is a copy
(ui/data_model.py) and the page lives in ui/template.html.

    PYTHONPATH=src uv run python ui/build_ui.py \
        --input out/smoke-profiles.jsonl --output ui/out/index.html
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(HERE), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_model import compact, read_jsonl  # noqa: E402

PLACEHOLDER = "/*__SIGNALPOST_DATA__*/[]"
BUILT_PLACEHOLDER = "__SIGNALPOST_BUILT__"


def _with_financial_health(row: dict) -> dict:
    """Recompute ratio metrics the same way norway_company_agent.showcase does,
    without its placeholder defaults (no invented municipality or legal form)."""
    try:
        from norway_company_agent.synthesis import compute_financial_metrics
    except Exception:  # pragma: no cover - UI still works from raw records
        return row
    copy_r = dict(row)
    fin = (copy_r.get("evidence") or {}).get("financials") or {}
    value = fin.get("value") if isinstance(fin, dict) else {}
    records = value.get("records") if isinstance(value, dict) else None
    if not isinstance(records, list):
        direct = copy_r.get("financial")
        records = direct.get("records") if isinstance(direct, dict) else []
    synthesis = dict(copy_r.get("synthesis") or {})
    synthesis["financial_health"] = compute_financial_metrics(records or [])
    copy_r["synthesis"] = synthesis
    return copy_r


def build(rows: list[dict], external_by_org: dict[str, list[dict]] | None = None) -> str:
    external_by_org = external_by_org or {}
    data = [
        compact(_with_financial_health(r), external_by_org.get(str(r["organisation_number"]), []))
        for r in rows
    ]
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    template = (HERE / "template.html").read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise SystemExit("template.html is missing the data placeholder")
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return template.replace(PLACEHOLDER, payload).replace(BUILT_PLACEHOLDER, built)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=str(ROOT / "out/smoke-profiles.jsonl"))
    ap.add_argument("--output", default=str(HERE / "out/index.html"))
    ap.add_argument("--external-observations", nargs="*", default=[])
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    rows = read_jsonl(Path(args.input))
    if args.limit:
        rows = rows[: args.limit]
    external: dict[str, list[dict]] = defaultdict(list)
    for source in args.external_observations:
        for obs in read_jsonl(Path(source)):
            org = str(obs.get("organisation_number") or "")
            if org:
                external[org].append(obs)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(rows, external), encoding="utf-8")
    print(f"Wrote {out} with {len(rows)} companies")


if __name__ == "__main__":
    main()
