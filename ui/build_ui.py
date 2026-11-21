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
from envelope_view import GROUP_LABELS, coverage, run_summary, verify_payload  # noqa: E402

PLACEHOLDER = "/*__SIGNALPOST_DATA__*/[]"
RUN_PLACEHOLDER = "/*__SIGNALPOST_RUN__*/null"
GROUPS_PLACEHOLDER = "/*__SIGNALPOST_GROUPS__*/{}"
SIDECAR_PLACEHOLDER = "/*__SIGNALPOST_SIDECAR__*/null"
BUILT_PLACEHOLDER = "__SIGNALPOST_BUILT__"
# Up to this many companies the per-fact evidence is embedded, so the page is one portable
# file. Above it, each company's evidence goes in its own small script file next to the
# page and loads when the company is opened (works from file:// and any static host).
INLINE_LIMIT = 250


def _script_json(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


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


def build(
    rows: list[dict],
    external_by_org: dict[str, list[dict]] | None = None,
    *,
    envelopes: list[dict] | None = None,
    run_meta: dict | None = None,
    sidecar_dir: Path | None = None,
) -> str:
    """The company browser for one run.

    envelopes: the run's terminal envelopes; every Verify panel, coverage state and run
    check is read from them. sidecar_dir: where to write per-company evidence files when
    the run has more than INLINE_LIMIT companies (named "<page stem>_files" next to the page).
    """
    external_by_org = external_by_org or {}
    env_by_org = {str(e.get("organisation_number")): e for e in envelopes or [] if isinstance(e, dict)}
    use_sidecar = sidecar_dir is not None and len(rows) > INLINE_LIMIT
    if use_sidecar:
        sidecar_dir.mkdir(parents=True, exist_ok=True)
    data = []
    for r in rows:
        org = str(r["organisation_number"])
        item = compact(_with_financial_health(r), external_by_org.get(org, []))
        env = env_by_org.get(org)
        if env is not None:
            item["cov"] = [[a["key"], a["state"], a["reason"]] for a in coverage(env)]
            payload = verify_payload(env)
            if use_sidecar:
                (sidecar_dir / f"{org}.js").write_text(
                    f"window.__SP_VERIFY__&&window.__SP_VERIFY__({_script_json(org)},{_script_json(payload)});\n", encoding="utf-8")
                item["vf"] = 1  # evidence is in the sidecar file
            else:
                item["vf"] = payload
        data.append(item)
    template = (HERE / "template.html").read_text(encoding="utf-8")
    for placeholder in (PLACEHOLDER, RUN_PLACEHOLDER, GROUPS_PLACEHOLDER, SIDECAR_PLACEHOLDER):
        if placeholder not in template:
            raise SystemExit(f"template.html is missing the placeholder {placeholder}")
    summary = run_summary(list(env_by_org.values()), run_meta) if envelopes is not None else None
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # Company data goes in last, so text scraped from a site can never be taken for a placeholder.
    return (template
            .replace(RUN_PLACEHOLDER, _script_json(summary))
            .replace(GROUPS_PLACEHOLDER, _script_json(GROUP_LABELS))
            .replace(SIDECAR_PLACEHOLDER, _script_json(sidecar_dir.name if use_sidecar else None))
            .replace(BUILT_PLACEHOLDER, built)
            .replace(PLACEHOLDER, _script_json(data)))


def run_meta_from_report(report: dict) -> dict:
    """Run facts for the page header, taken from run_agent's report.json."""
    ops = report.get("operations") or {}
    return {
        "run_id": report.get("run_id"),
        "completed_at": report.get("completed_at"),
        "commit": report.get("commit"),
        "expected_count": report.get("organisations_processed"),
        "requests": ops.get("total_requests"),
        "runtime_seconds": report.get("runtime_seconds"),
        "cost_usd": ops.get("declared_third_party_cost_usd"),
        "qualification": report.get("qualification_status") or {},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=str(ROOT / "out/smoke-profiles.jsonl"))
    ap.add_argument("--output", default=str(HERE / "out/index.html"))
    ap.add_argument("--external-observations", nargs="*", default=[])
    ap.add_argument("--envelopes", help="The run's envelopes JSONL (Verify panels, coverage and run checks)")
    ap.add_argument("--report", help="The run's report JSON (run id, requests, runtime and qualification checks)")
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
    envelopes = read_jsonl(Path(args.envelopes)) if args.envelopes else None
    meta = run_meta_from_report(json.loads(Path(args.report).read_text(encoding="utf-8"))) if args.report else {}
    if envelopes is not None:
        meta.setdefault("expected_count", len(envelopes))
    sidecar = out.with_name(out.stem + "_files")
    out.write_text(build(rows, external, envelopes=envelopes, run_meta=meta, sidecar_dir=sidecar), encoding="utf-8")
    print(f"Wrote {out} with {len(rows)} companies")


if __name__ == "__main__":
    main()
