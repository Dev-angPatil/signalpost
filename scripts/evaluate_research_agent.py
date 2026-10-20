#!/usr/bin/env python3
"""Evaluate the deterministic research agent against v1, v2, or v3 fixtures.

The v1 fixture contains expected result IDs.  The newer v2/v3 fixtures contain
QA claim requirements and screen-query oracles, but intentionally do not ship a
gold result set for every screen.  The report keeps that boundary explicit:
screen result recall is ``None`` for v2/v3 rather than being inferred from the
provided profile corpus.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.research import answer_profile, screen_profiles  # noqa: E402
from norway_company_agent.workspace import empty_workspace, record_screen, save_workspace  # noqa: E402


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    orgs = [str(row.get("organisation_number") or "") for row in rows]
    if len(orgs) != len(set(orgs)):
        raise ValueError("Input profiles must contain unique organisation numbers")
    return rows


def _citation_complete(item: dict[str, Any]) -> bool:
    return bool(item.get("source_url") and item.get("retrieved_at") and item.get("content_sha256"))


def _canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.casefold()
    return value


def _plan_projection(plan: dict[str, Any]) -> dict[str, Any]:
    filters = [
        {
            "field": item.get("field"),
            "operator": item.get("operator"),
            "value": _canonical_value(item.get("value")),
        }
        for item in plan.get("filters", [])
    ]
    sort = plan.get("sort")
    projected_sort = None
    if isinstance(sort, dict):
        projected_sort = {
            "field": sort.get("field"),
            "direction": sort.get("direction"),
            "limit": sort.get("limit"),
        }
    return {"filters": filters, "sort": projected_sort}


def _evaluate_qa_case(row: dict[str, Any] | None, spec: dict[str, Any]) -> dict[str, Any]:
    if row is None:
        return {
            "id": spec.get("id"),
            "organisation_number": str(spec.get("organisation_number") or ""),
            "supported": False,
            "missing": ["profile row"],
            "facts": 0,
            "required_claims": spec.get("required_claims", []),
            "present_claims": [],
            "provenance_complete": False,
        }

    answer = answer_profile(row, spec["question"])
    facts = answer.get("facts", [])
    present_claims = {str(item.get("claim") or "") for item in facts}
    required_claims = [str(item) for item in spec.get("required_claims", [])]
    missing = [claim for claim in required_claims if claim not in present_claims]
    provenance_complete = all(_citation_complete(item) for item in facts)
    minimum_facts = int(spec.get("minimum_facts") or 0)
    if len(facts) < minimum_facts:
        missing.append(f"minimum_facts:{minimum_facts}")
    if not provenance_complete:
        missing.append("claim provenance")
    return {
        "id": spec.get("id"),
        "organisation_number": str(spec.get("organisation_number") or ""),
        "supported": not missing,
        "missing": missing,
        "facts": len(facts),
        "required_claims": required_claims,
        "present_claims": sorted(present_claims),
        "provenance_complete": provenance_complete,
        "unsupported_or_uncertain": answer.get("unsupported_or_uncertain", []),
    }


def _screen_specs(suite: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if isinstance(suite.get("screens"), list):
        return suite["screens"], "v1"
    if isinstance(suite.get("screen_cases"), list):
        return suite["screen_cases"], "v2/v3"
    return [], "unknown"


def _unsupported_specs(suite: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(suite.get("unsupported"), dict):
        return [suite["unsupported"]]
    return list(suite.get("unsupported_cases") or [])


def evaluate_suite(rows: list[dict[str, Any]], suite: dict[str, Any], workspace_path: str | Path) -> dict[str, Any]:
    """Evaluate one suite without claiming coverage outside the supplied rows."""
    by_org = {str(row.get("organisation_number")): row for row in rows}
    qa_specs: list[dict[str, Any]]
    if isinstance(suite.get("single_company"), dict):
        qa_specs = [suite["single_company"]]
        qa_mode = "v1"
    else:
        qa_specs = list(suite.get("qa_cases") or [])
        qa_mode = "v2/v3"

    qa_results = [
        _evaluate_qa_case(by_org.get(str(spec.get("organisation_number"))), spec)
        for spec in qa_specs
    ]
    requested_qa_orgs = [str(spec.get("organisation_number") or "") for spec in qa_specs]
    missing_qa_orgs = sorted({org for org in requested_qa_orgs if org not in by_org})
    qa_supported = sum(bool(item["supported"]) for item in qa_results)
    required_claim_total = sum(len(item["required_claims"]) for item in qa_results)
    required_claim_found = sum(
        len(set(item["required_claims"]) & set(item["present_claims"])) for item in qa_results
    )
    qa_report = {
        "cases": len(qa_results),
        "supported_cases": qa_supported,
        "support_rate": qa_supported / len(qa_results) if qa_results else 1.0,
        "required_claims": required_claim_total,
        "required_claims_found": required_claim_found,
        "claim_recall": required_claim_found / required_claim_total if required_claim_total else 1.0,
        "provenance_complete": all(item["provenance_complete"] for item in qa_results),
        "results": qa_results,
    }

    screen_specs, screen_mode = _screen_specs(suite)
    screen_results: list[dict[str, Any]] = []
    exact_membership = 0
    exact_plans = 0
    citations_complete_count = 0
    workspace = empty_workspace()
    for spec in screen_specs:
        result = screen_profiles(rows, spec["query"])
        actual_orgs = [str(item.get("organisation_number")) for item in result["results"]]
        citations_complete = all(
            _citation_complete(citation)
            for item in result["results"]
            for citation in item.get("citations", [])
        )
        citations_complete_count += int(citations_complete)
        expected_fields = spec.get("expected_filter_fields")
        plan_exact = False
        membership_exact = None
        if screen_mode == "v1":
            expected_orgs = [str(item) for item in spec.get("expected_organisation_numbers", [])]
            membership_exact = actual_orgs == expected_orgs
            plan_exact = [item.get("field") for item in result["plan"].get("filters", [])] == list(expected_fields or [])
            exact_membership += int(membership_exact)
        else:
            oracle = spec.get("oracle") or {}
            plan_exact = _plan_projection(result["plan"]) == _plan_projection(oracle)
            expected_orgs = None
        exact_plans += int(plan_exact)
        workspace = record_screen(workspace, result, pin_organisations=actual_orgs[:1])
        screen_results.append({
            "id": spec.get("id"),
            "query": spec["query"],
            "expected_organisation_numbers": expected_orgs,
            "actual_organisation_numbers": actual_orgs,
            "membership_exact": membership_exact,
            "plan_exact": plan_exact,
            "citations_complete": citations_complete,
            "result_count": len(actual_orgs),
            "plan": result["plan"],
        })

    workspace_path = Path(workspace_path)
    save_workspace(workspace_path, workspace)
    saved_work = workspace_path.exists() and len(workspace.get("history", [])) == len(screen_specs)
    screen_count = len(screen_specs)
    plan_accuracy = exact_plans / screen_count if screen_count else 1.0
    citation_rate = citations_complete_count / screen_count if screen_count else 1.0
    membership_rate = exact_membership / screen_count if screen_mode == "v1" and screen_count else None
    screen_report = {
        "cases": screen_count,
        "mode": screen_mode,
        "plan_accuracy": plan_accuracy,
        "citations_complete": citation_rate == 1.0,
        "citation_rate": citation_rate,
        "membership_accuracy": membership_rate,
        "result_recall": membership_rate if screen_mode == "v1" else None,
        "result_recall_measured": screen_mode == "v1",
        "results": screen_results,
    }

    unsupported_results = []
    for spec in _unsupported_specs(suite):
        result = screen_profiles(rows, spec["query"])
        expected = bool(spec.get("must_abstain", True))
        actual = bool(result.get("abstained"))
        unsupported_results.append({
            "id": spec.get("id"),
            "query": spec["query"],
            "expected_abstain": expected,
            "abstained": actual,
            "correct": expected == actual,
            "reason": result.get("reason"),
        })
    unsupported_correct = sum(bool(item["correct"]) for item in unsupported_results)
    unsupported_report = {
        "cases": len(unsupported_results),
        "correct": unsupported_correct,
        "abstention_rate": unsupported_correct / len(unsupported_results) if unsupported_results else 1.0,
        "results": unsupported_results,
    }

    provenance_export = bool(qa_report["provenance_complete"] and screen_report["citations_complete"])
    if qa_mode == "v1":
        points = {
            "cited_single_company_qa": 4 if qa_report["support_rate"] == 1.0 else 0,
            "cross_company_screening": 3 * float(membership_rate or 0),
            "exact_inspectable_filters": 2 * plan_accuracy,
            "unsupported_abstention": 1 if unsupported_report["abstention_rate"] == 1.0 else 0,
            "saved_history": 1 if saved_work and bool(workspace.get("pins")) else 0,
            "provenance_export": 1 if provenance_export else 0,
        }
    else:
        points = {
            "cited_single_company_qa": 4 * qa_report["support_rate"],
            "cross_company_screening": 3 * plan_accuracy,
            "exact_inspectable_filters": 2 * plan_accuracy,
            "unsupported_abstention": 1 * unsupported_report["abstention_rate"],
            "saved_history": 1 if saved_work else 0,
            "provenance_export": 1 if provenance_export else 0,
        }

    corpus_coverage = {
        "qa_requested": len(requested_qa_orgs),
        "qa_present": len(requested_qa_orgs) - len(missing_qa_orgs),
        "qa_coverage": (len(requested_qa_orgs) - len(missing_qa_orgs)) / len(requested_qa_orgs) if requested_qa_orgs else 1.0,
        "missing_qa_organisations": missing_qa_orgs,
        "screen_scope": "provided_profile_corpus",
        "screen_result_recall_measured": screen_mode == "v1",
    }
    qualification = bool(
        not missing_qa_orgs
        and qa_report["support_rate"] == 1.0
        and screen_report["plan_accuracy"] == 1.0
        and unsupported_report["abstention_rate"] == 1.0
        and saved_work
        and provenance_export
    )
    report = {
        "corpus": suite.get("corpus"),
        "suite_schema": "v1" if qa_mode == "v1" else "v3",
        "profiles": len(rows),
        "questions": len(qa_specs) + len(screen_specs) + len(unsupported_results),
        "corpus_coverage": corpus_coverage,
        "qa": qa_report,
        "screens": screen_report,
        "unsupported": unsupported_report,
        "saved_work": saved_work,
        "provenance_export": provenance_export,
        "points": points,
        "score": round(sum(points.values()), 3),
        "maximum": 12,
        "claim_boundary": (
            "v1 screen membership is compared with expected organisation IDs; "
            "v2/v3 screen result recall is not measured because their fixtures provide "
            "query oracles rather than gold result sets."
        ),
        "external_footprint_qa_passed": qualification,
        "qualification_passed": qualification,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Signalpost research retrieval and screening fixtures.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    rows = read_jsonl(args.input)
    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    report = evaluate_suite(rows, suite, args.workspace)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"qa", "screens", "unsupported"}}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["qualification_passed"] else 1)


if __name__ == "__main__":
    main()
