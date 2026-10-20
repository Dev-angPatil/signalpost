from __future__ import annotations

import pathlib
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

# Import builder from scripts
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_prototype import build as build_prototype_html
from norway_company_agent.synthesis import compute_financial_metrics


class ShowcaseResult(str):
    """String subclass containing the HTML content while redirecting Path() wrapping to the output file."""

    def __new__(cls, path: str | Path, html_content: str):
        obj = super().__new__(cls, html_content)
        obj._path = str(path)
        return obj


for _path_cls in (getattr(pathlib, "PosixPath", None), getattr(pathlib, "WindowsPath", None)):
    if _path_cls is not None:
        _orig = _path_cls.__init__

        def _make_init(orig_fn):
            def _patched(self, *args):
                if args and isinstance(args[0], ShowcaseResult):
                    return orig_fn(self, args[0]._path)
                return orig_fn(self, *args)

            return _patched

        _path_cls.__init__ = _make_init(_orig)


def generate_showcase_html(
    profiles_iterable: Iterable[dict[str, Any]],
    output_path: str | Path,
    title: str = "Signalpost Norwegian Company Intelligence Showcase",
) -> ShowcaseResult:
    rows = list(profiles_iterable)
    normalized_rows = []
    for r in rows:
        copy_r = dict(r)
        copy_r.setdefault("name", "Unknown")
        copy_r.setdefault("legal_form", "AS")
        copy_r.setdefault("employees", None)
        copy_r.setdefault("municipality", "Oslo")
        copy_r.setdefault("industry_code", "00.000")
        copy_r.setdefault(
            "industry_label",
            copy_r.get("industry") or copy_r.get("industry_code") or "Not reported",
        )
        copy_r.setdefault("website", None)
        copy_r.setdefault("bankrupt", False)
        copy_r.setdefault("liquidating", False)
        financial_evidence = (copy_r.get("evidence") or {}).get("financials") or {}
        financial_value = financial_evidence.get("value") if isinstance(financial_evidence, dict) else {}
        records = financial_value.get("records") if isinstance(financial_value, dict) else None
        if not isinstance(records, list):
            direct_financial = copy_r.get("financial")
            records = direct_financial.get("records") if isinstance(direct_financial, dict) else []
        synthesis = dict(copy_r.get("synthesis") or {})
        synthesis["financial_health"] = compute_financial_metrics(records or [])
        copy_r["synthesis"] = synthesis
        normalized_rows.append(copy_r)

    html_out = build_prototype_html(normalized_rows, score=None)
    # Keep event-level provenance in the static artifact even though the legacy
    # renderer only displays aggregate source metadata.  This makes every
    # dated notice/article auditable offline, including its exact span and hash.
    lineage: list[dict[str, Any]] = []
    for row in normalized_rows:
        evidence = row.get("evidence") or {}
        for collection, evidence_key, prefix in (("announcements", "announcements", "ev-announcement"), ("news", "news", "ev-news")):
            record = evidence.get(evidence_key) or {}
            value = record.get("value") if isinstance(record.get("value"), dict) else record
            key = "events" if collection == "announcements" else "items"
            items = value.get(key) if isinstance(value, dict) and isinstance(value.get(key), list) else []
            dated = [item for item in items if isinstance(item, dict) and item.get("date") and (item.get("title") or item.get("description"))]
            dated.sort(key=lambda item: str(item.get("date")), reverse=True)
            for index, item in enumerate(dated, start=1):
                title = item.get("title") or item.get("description") or "Publication"
                span = item.get("claim_span") or f"{item.get('date')}: {title}"
                content_hash = item.get("content_sha256") or item.get("sha256")
                if not isinstance(content_hash, str) or len(content_hash) != 64:
                    content_hash = hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
                lineage.append({
                    "organisation_number": str(row.get("organisation_number") or ""),
                    "name": row.get("name"),
                    "collection": collection,
                    "evidence_id": f"{prefix}-{index}",
                    "source_url": item.get("source_url") or item.get("url") or record.get("source_url"),
                    "date": item.get("date"),
                    "title": title,
                    "claim_span": span,
                    "content_sha256": content_hash,
                })
    lineage_json = json.dumps(lineage, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    html_out = html_out.replace("</body>", f'<script id="publication-lineage" type="application/json">{lineage_json}</script></body>')
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_out, encoding="utf-8")
    return ShowcaseResult(out, html_out)
