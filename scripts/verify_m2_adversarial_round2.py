"""Adversarial Verification Suite Round 2 for Milestone 2: Corporate Announcements Scraping & Contract Emission.

Thorough metamorphic fuzzing, invariant checking, performance stress testing, and edge case mining.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from norway_company_agent.announcements import (
    CATEGORY_KEYWORDS,
    classify_all_notices,
    classify_notice,
    fetch_announcements,
)
from norway_company_agent.contract import build_output_envelope


def test_chaotic_html_expanded_entities_and_whitespace() -> dict[str, Any]:
    """Test multi-word category phrases with &nbsp;, \\xa0, consecutive whitespace, and leading/trailing junk."""
    cases = [
        # board_appointment
        ("Daglig\xa0leder", "board_appointment"),
        ("Endring\xa0av\xa0styre", "board_appointment"),
        ("Styrets\xa0sammensetning", "board_appointment"),
        ("Nytt\xa0styre", "board_appointment"),
        ("Valg\xa0av\xa0styre", "board_appointment"),
        ("Styrets\xa0leder", "board_appointment"),
        # capital_change
        ("Erverv\xa0av\xa0egne\xa0aksjer", "capital_change"),
        ("Innbetaling\xa0av\xa0aksjekapital", "capital_change"),
        ("Kapitalforhøyelse", "capital_change"),
        ("Kapital\xa0nedsettelse", "capital_change"),
        # legal_filing
        ("Endring\xa0av\xa0vedtekter", "legal_filing"),
        ("Endring\xa0av\xa0foretaksnavn", "legal_filing"),
        ("Endring\xa0av\xa0forretningsadresse", "legal_filing"),
        ("Godkjent\xa0årsregnskap", "legal_filing"),
        # insolvency
        ("Innstilling\xa0av\xa0bobehandling", "insolvency"),
        ("Avslutning\xa0av\xa0konkursbehandling", "insolvency"),
        ("Konkursåpning", "insolvency"),
        ("Tvangsavvikling", "insolvency"),
    ]
    results = {}
    for title, expected_cat in cases:
        ev = classify_notice({"title": title, "date": "2026-01-01"})
        actual_cat = ev["category"]
        results[title] = {
            "expected": expected_cat,
            "actual": actual_cat,
            "matched": actual_cat == expected_cat,
        }
    return results


def test_chaotic_html_dates_variations() -> dict[str, Any]:
    """Test date parsing across various messy formats and spaces."""
    variations = [
        (b"<html><body><table><tr><td>01.&nbsp;09.&nbsp;2026</td><td>Styre</td></tr></table></body></html>", "2026-09-01"),
        (b"<html><body><table><tr><td>01. 09. 2026</td><td>Styre</td></tr></table></body></html>", "2026-09-01"),
        (b"<html><body><table><tr><td> &nbsp; 01.09.2026 &nbsp; </td><td>Styre</td></tr></table></body></html>", "2026-09-01"),
        (b"<html><body><table><tr><td>\n\t 15.08.2025 \t\n</td><td>Kapital</td></tr></table></body></html>", "2025-08-15"),
        (b"<html><body><table><tr><td>Dato: 28.02.2024</td><td>Aksjekapital</td></tr></table></body></html>", "2024-02-28"),
    ]
    results = []
    for html, exp_date in variations:
        res = fetch_announcements("923609016", http_fetcher=lambda u, d: html)
        count = res["corporate_actions_count"]
        extracted_date = res["events"][0]["date"] if count > 0 else None
        results.append({
            "count": count,
            "extracted_date": extracted_date,
            "expected_date": exp_date,
            "matched": count == 1 and extracted_date == exp_date,
        })
    return {"all_matched": all(r["matched"] for r in results), "details": results}


def test_varied_column_spacer_layouts() -> dict[str, Any]:
    """Test notice extraction across 2, 3, 5, and 7-column table structures."""
    layouts = {
        "2_col": b"<html><body><table><tr><td>01.09.2026</td><td><a href='1'>Styre</a></td></tr></table></body></html>",
        "3_col": b"<html><body><table><tr><td>01.09.2026</td><td><a href='1'>Styre</a></td><td>Publisert</td></tr></table></body></html>",
        "5_col_official": b"<html><body><table><tr><td></td><td>01.09.2026</td><td></td><td><a href='1'>Styre</a></td><td></td></tr></table></body></html>",
        "7_col_wide": b"<html><body><table><tr><td></td><td></td><td>01.09.2026</td><td></td><td><a href='1'>Styre</a></td><td></td><td></td></tr></table></body></html>",
    }
    results = {}
    for name, html in layouts.items():
        res = fetch_announcements("923609016", http_fetcher=lambda u, d: html)
        matched = (
            res["corporate_actions_count"] == 1
            and res["events"][0]["date"] == "2026-09-01"
            and res["events"][0]["title"] == "Styre"
            and res["events"][0]["category"] == "board_appointment"
            and res["events"][0]["source_url"] == "https://w2.brreg.no/kunngjoring/1"
        )
        results[name] = {
            "count": res["corporate_actions_count"],
            "matched": matched,
        }
    return results


def test_nested_table_deduplication() -> dict[str, Any]:
    """Verify that nested layout tables do NOT cause duplicate row extraction."""
    html = (
        b"<html><body>"
        b"<table id='t1'><tr><td>"
        b"  <table id='t2'><tr><td>"
        b"    <table id='t3'><tr><td>"
        b"      <table id='notices'>"
        b"        <tr><td>01.09.2026</td><td><a href='notice1'>Daglig leder</a></td></tr>"
        b"        <tr><td>15.08.2026</td><td><a href='notice2'>Kapitalnedsettelse</a></td></tr>"
        b"      </table>"
        b"    </td></tr></table>"
        b"  </td></tr></table>"
        b"</td></tr></table>"
        b"</body></html>"
    )
    res = fetch_announcements("923609016", http_fetcher=lambda u, d: html)
    return {
        "count": res["corporate_actions_count"],
        "expected": 2,
        "matched": res["corporate_actions_count"] == 2,
    }


def test_evidence_hashing_fallbacks_exhaustive() -> dict[str, Any]:
    """Verify content_sha256 integrity across all pipeline structures."""
    raw_bytes = b"<html><body><table><tr><td>01.09.2026</td><td>Styre</td></tr></table></body></html>"
    expected_hash = hashlib.sha256(raw_bytes).hexdigest()
    ann_res = fetch_announcements("923609016", http_fetcher=lambda u, d: raw_bytes)

    # Pipeline A: Standard run_agent structure
    prof_a = {
        "organisation_number": "923609016",
        "evidence": {
            "registry": {"status": "available", "value": {"navn": "A"}},
            "announcements": {
                "id": "ev-announcements",
                "status": "available",
                "source_url": ann_res["source_url"],
                "source_class": "official_public_notices",
                "content_sha256": ann_res["raw_html_sha256"],
                "value": ann_res,
            },
        },
    }
    env_a = build_output_envelope(prof_a, run_id="r1", started_at="t", completed_at="t")
    ev_a = next(e for e in env_a["evidence"] if e["id"] == "ev-announcements")

    # Pipeline B: Direct profile["announcements"]
    prof_b = {
        "organisation_number": "923609016",
        "evidence": {"registry": {"status": "available", "value": {"navn": "B"}}},
        "announcements": ann_res,
    }
    env_b = build_output_envelope(prof_b, run_id="r2", started_at="t", completed_at="t")
    ev_b = next(e for e in env_b["evidence"] if e["id"] == "ev-announcements")

    # Pipeline C: Bare evidence without top-level content_sha256
    prof_c = {
        "organisation_number": "923609016",
        "evidence": {
            "registry": {"status": "available", "value": {"navn": "C"}},
            "announcements": {
                "id": "ev-announcements",
                "status": "available",
                "source_url": ann_res["source_url"],
                "source_class": "official_public_notices",
                "value": ann_res,
            },
        },
    }
    env_c = build_output_envelope(prof_c, run_id="r3", started_at="t", completed_at="t")
    ev_c = next(e for e in env_c["evidence"] if e["id"] == "ev-announcements")

    # Pipeline D: raw_html_sha256 at top level of ann_record
    prof_d = {
        "organisation_number": "923609016",
        "evidence": {
            "registry": {"status": "available", "value": {"navn": "D"}},
            "announcements": {
                "id": "ev-announcements",
                "status": "available",
                "source_url": ann_res["source_url"],
                "source_class": "official_public_notices",
                "raw_html_sha256": expected_hash,
                "value": {"events": []},
            },
        },
    }
    env_d = build_output_envelope(prof_d, run_id="r4", started_at="t", completed_at="t")
    ev_d = next(e for e in env_d["evidence"] if e["id"] == "ev-announcements")

    return {
        "expected_hash": expected_hash,
        "pipeline_a": ev_a["content_sha256"] == expected_hash,
        "pipeline_b": ev_b["content_sha256"] == expected_hash,
        "pipeline_c": ev_c["content_sha256"] == expected_hash,
        "pipeline_d": ev_d["content_sha256"] == expected_hash,
        "all_match": all([
            ev_a["content_sha256"] == expected_hash,
            ev_b["content_sha256"] == expected_hash,
            ev_c["content_sha256"] == expected_hash,
            ev_d["content_sha256"] == expected_hash,
        ]),
    }


def test_large_scale_performance_and_sorting(num_notices: int = 500, col_mode: str = "2col") -> dict[str, Any]:
    """Test performance and descending date sorting with num_notices."""
    rows = []
    cats = [
        "Kapitalforhøyelse",
        "Styrets sammensetning",
        "Godkjent årsregnskap",
        "Konkurs",
        "Melding om flytting",
    ]
    for i in range(num_notices):
        day = (i % 28) + 1
        month = ((i // 28) % 12) + 1
        year = 2020 + (i // 336)
        d_str = f"{day:02d}.{month:02d}.{year:04d}"
        title = cats[i % len(cats)] + f" #{i}"
        if col_mode == "2col":
            rows.append(f"<tr><td>{d_str}</td><td><a href='/hent?id={i}'>{title}</a></td></tr>")
        else:
            rows.append(f"<tr><td></td><td>{d_str}</td><td></td><td><a href='/hent?id={i}'>{title}</a></td><td></td></tr>")

    chunk_size = 50
    table_chunks = ["<table>" + "".join(rows[j:j + chunk_size]) + "</table>" for j in range(0, len(rows), chunk_size)]
    html = ("<html><body><table id='empty0'><tr><td>wrapper</td></tr></table>" + "".join(table_chunks) + "</body></html>").encode("utf-8")

    latencies = []
    for _ in range(5):
        t0 = time.perf_counter()
        res = fetch_announcements("923609016", http_fetcher=lambda u, d: html)
        latencies.append((time.perf_counter() - t0) * 1000)

    avg_lat = sum(latencies) / len(latencies)
    dates = [e["date"] for e in res["events"]]
    is_sorted_desc = all(dates[i] >= dates[i + 1] for i in range(len(dates) - 1))
    cats_sum = sum(c["count"] for c in res["categories"].values())

    return {
        "num_notices": num_notices,
        "col_mode": col_mode,
        "parsed_count": res["corporate_actions_count"],
        "avg_latency_ms": round(avg_lat, 2),
        "min_latency_ms": round(min(latencies), 2),
        "max_latency_ms": round(max(latencies), 2),
        "under_150ms": avg_lat < 150.0,
        "is_sorted_desc": is_sorted_desc,
        "categories_sum_matches": cats_sum == num_notices,
    }


def test_contract_invariants_count_zero_and_missing() -> dict[str, Any]:
    """Verify invariants for count==0, missing evidence, and failed evidence."""
    # 1. Count == 0
    prof_zero = {
        "organisation_number": "923609016",
        "evidence": {
            "registry": {"status": "available", "value": {"navn": "ZERO AS"}},
            "announcements": {
                "id": "ev-announcements",
                "status": "available",
                "source_class": "official_public_notices",
                "source_url": "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=923609016",
                "value": {"events": [], "categories": {}, "corporate_actions_count": 0},
                "content_sha256": "0" * 64,
            },
        },
    }
    env_zero = build_output_envelope(prof_zero, run_id="r0", started_at="t", completed_at="t")
    claims_zero = {c["field"]: c for c in env_zero["claims"]}

    c_zero_count = claims_zero.get("corporate_announcements_count", {})
    zero_count_valid = (
        c_zero_count.get("value") == 0
        and c_zero_count.get("availability") == "available"
        and c_zero_count.get("confidence") == 0.99
        and c_zero_count.get("evidence_ids") == ["ev-announcements"]
    )

    latest_fields = [
        "latest_corporate_action_date",
        "latest_corporate_action_title",
        "latest_corporate_action_url",
        "latest_capital_change_date",
        "latest_board_appointment_date",
    ]
    zero_latests_valid = all(
        claims_zero.get(f, {}).get("value") is None
        and claims_zero.get(f, {}).get("availability") == "not_available"
        and claims_zero.get(f, {}).get("confidence") == 1.0
        and claims_zero.get(f, {}).get("evidence_ids") == ["ev-announcements"]
        for f in latest_fields
    )

    ev_zero_present = any(e["id"] == "ev-announcements" for e in env_zero["evidence"])

    # 2. Evidence completely missing
    prof_missing = {
        "organisation_number": "923609016",
        "evidence": {"registry": {"status": "available", "value": {"navn": "MISSING AS"}}},
    }
    env_missing = build_output_envelope(prof_missing, run_id="rm", started_at="t", completed_at="t")
    claims_missing = {c["field"]: c for c in env_missing["claims"]}
    all_fields = ["corporate_announcements_count"] + latest_fields
    missing_all_not_avail = all(
        claims_missing.get(f, {}).get("value") is None
        and claims_missing.get(f, {}).get("availability") == "not_available"
        and claims_missing.get(f, {}).get("confidence") == 1.0
        and claims_missing.get(f, {}).get("evidence_ids") == []
        for f in all_fields
    )
    ev_missing_absent = not any(e["id"] == "ev-announcements" for e in env_missing["evidence"])

    # 3. Evidence status == "failed"
    prof_failed = {
        "organisation_number": "923609016",
        "evidence": {
            "registry": {"status": "available", "value": {"navn": "FAILED AS"}},
            "announcements": {
                "id": "ev-announcements",
                "status": "failed",
                "source_class": "official_public_notices",
                "error": "HTTP 500",
            },
        },
    }
    env_failed = build_output_envelope(prof_failed, run_id="rf", started_at="t", completed_at="t")
    claims_failed = {c["field"]: c for c in env_failed["claims"]}
    failed_all_not_avail = all(
        claims_failed.get(f, {}).get("value") is None
        and claims_failed.get(f, {}).get("availability") == "not_available"
        and claims_failed.get(f, {}).get("confidence") == 1.0
        and claims_failed.get(f, {}).get("evidence_ids") == []
        for f in all_fields
    )
    ev_failed_absent = not any(e["id"] == "ev-announcements" for e in env_failed["evidence"])

    return {
        "zero_count_valid": zero_count_valid,
        "zero_latests_valid": zero_latests_valid,
        "ev_zero_present": ev_zero_present,
        "missing_all_not_avail": missing_all_not_avail,
        "ev_missing_absent": ev_missing_absent,
        "failed_all_not_avail": failed_all_not_avail,
        "ev_failed_absent": ev_failed_absent,
        "all_invariants_pass": (
            zero_count_valid
            and zero_latests_valid
            and ev_zero_present
            and missing_all_not_avail
            and ev_missing_absent
            and failed_all_not_avail
            and ev_failed_absent
        ),
    }


def run_all() -> None:
    print("=" * 75)
    print("RUNNING ADVERSARIAL VERIFICATION SUITE ROUND 2 (CHALLENGER)")
    print("=" * 75)

    print("\n[1] Chaotic HTML: Expanded &nbsp; in titles:")
    res_titles = test_chaotic_html_expanded_entities_and_whitespace()
    for title, info in res_titles.items():
        status = "PASS" if info["matched"] else "FAIL"
        print(f"  [{status}] {title!r:40s} -> Expected: {info['expected']:18s} Actual: {info['actual']}")

    print("\n[2] Chaotic HTML: Dates variations:")
    res_dates = test_chaotic_html_dates_variations()
    print(f"  All date variations matched: {res_dates['all_matched']}")
    for d in res_dates["details"]:
        print(f"    extracted: {d['extracted_date']}, expected: {d['expected_date']}, matched: {d['matched']}")

    print("\n[3] Varied column spacer layouts (2, 3, 5, 7 cols):")
    res_cols = test_varied_column_spacer_layouts()
    for name, info in res_cols.items():
        print(f"  [{'PASS' if info['matched'] else 'FAIL'}] {name:15s} -> count: {info['count']}, matched: {info['matched']}")

    print("\n[4] Nested table deduplication:")
    res_nest = test_nested_table_deduplication()
    print(f"  [{'PASS' if res_nest['matched'] else 'FAIL'}] Nested table count: {res_nest['count']} (Expected: {res_nest['expected']})")

    print("\n[5] Evidence hashing fallbacks (Pipelines A, B, C, D):")
    res_hash = test_evidence_hashing_fallbacks_exhaustive()
    print(f"  Expected hash: {res_hash['expected_hash']}")
    print(f"  Pipeline A (standard run_agent): {res_hash['pipeline_a']}")
    print(f"  Pipeline B (profile[announcements]): {res_hash['pipeline_b']}")
    print(f"  Pipeline C (bare evidence fallback): {res_hash['pipeline_c']}")
    print(f"  Pipeline D (top-level raw_html_sha256): {res_hash['pipeline_d']}")
    print(f"  All pipelines match: {res_hash['all_match']}")

    print("\n[6] 500-Notice Scale & Performance Fuzzing:")
    res_fuzz_2col = test_large_scale_performance_and_sorting(500, "2col")
    print(f"  [2-column] Parsed count: {res_fuzz_2col['parsed_count']}/{res_fuzz_2col['num_notices']}")
    print(f"  [2-column] Latency: avg={res_fuzz_2col['avg_latency_ms']}ms, min={res_fuzz_2col['min_latency_ms']}ms, max={res_fuzz_2col['max_latency_ms']}ms")
    print(f"  [2-column] Performance <150ms: {res_fuzz_2col['under_150ms']}")

    res_fuzz_5col = test_large_scale_performance_and_sorting(500, "5col")
    print(f"  [5-column] Parsed count: {res_fuzz_5col['parsed_count']}/{res_fuzz_5col['num_notices']}")
    print(f"  [5-column] Latency: avg={res_fuzz_5col['avg_latency_ms']}ms, min={res_fuzz_5col['min_latency_ms']}ms, max={res_fuzz_5col['max_latency_ms']}ms")
    print(f"  [5-column] Descending date order: {res_fuzz_5col['is_sorted_desc']}")
    print(f"  [5-column] Category sum matches: {res_fuzz_5col['categories_sum_matches']}")

    print("\n[7] Contract Invariants Metamorphic Probing:")
    res_inv = test_contract_invariants_count_zero_and_missing()
    print(f"  Count==0 invariants hold: {res_inv['zero_count_valid'] and res_inv['zero_latests_valid'] and res_inv['ev_zero_present']}")
    print(f"  Missing evidence invariants hold: {res_inv['missing_all_not_avail'] and res_inv['ev_missing_absent']}")
    print(f"  Failed evidence invariants hold: {res_inv['failed_all_not_avail'] and res_inv['ev_failed_absent']}")
    print(f"  All invariants pass: {res_inv['all_invariants_pass']}")


if __name__ == "__main__":
    run_all()
