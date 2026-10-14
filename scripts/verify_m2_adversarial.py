"""Adversarial Verification Suite for Milestone 2: Corporate Announcements Scraping & Contract Emission.

Tests chaotic HTML, large scale fuzzing, evidence hashing integrity, and contract invariants.
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


def test_chaotic_html_non_breaking_spaces_in_titles() -> dict[str, Any]:
    """Test titles with &nbsp; (\\xa0) between words in multi-word category phrases."""
    cases = [
        ("Daglig\xa0leder", "board_appointment"),
        ("Endring\xa0av\xa0styre", "board_appointment"),
        ("Styrets\xa0sammensetning", "board_appointment"),
        ("Erverv\xa0av\xa0egne\xa0aksjer", "capital_change"),
        ("Endring\xa0av\xa0vedtekter", "legal_filing"),
        ("Endring\xa0av\xa0foretaksnavn", "legal_filing"),
        ("Innstilling\xa0av\xa0bobehandling", "insolvency"),
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


def test_chaotic_html_non_breaking_spaces_in_dates() -> dict[str, Any]:
    """Test date parsing when &nbsp; or whitespace appears in date cells."""
    html_nbsp_in_date = (
        b"<html><body><table><tr><td>01.&nbsp;09.&nbsp;2026</td><td>Styre</td></tr></table></body></html>"
    )
    html_spaces_in_date = (
        b"<html><body><table><tr><td>01. 09. 2026</td><td>Styre</td></tr></table></body></html>"
    )
    html_padded_date = (
        b"<html><body><table><tr><td> &nbsp; 01.09.2026 &nbsp; </td><td>Styre</td></tr></table></body></html>"
    )

    res_nbsp = fetch_announcements("923609016", http_fetcher=lambda u, d: html_nbsp_in_date)
    res_spaces = fetch_announcements("923609016", http_fetcher=lambda u, d: html_spaces_in_date)
    res_padded = fetch_announcements("923609016", http_fetcher=lambda u, d: html_padded_date)

    return {
        "nbsp_in_date_count": res_nbsp["corporate_actions_count"],
        "spaces_in_date_count": res_spaces["corporate_actions_count"],
        "padded_date_count": res_padded["corporate_actions_count"],
    }


def test_chaotic_html_casing_and_norwegian_chars() -> dict[str, Any]:
    """Test irregular casing and Norwegian characters in UTF-8 and Windows-1252."""
    casing_cases = [
        ("kApItAlFoRhØyElSe", "capital_change"),
        ("DAGLIG LEDER", "board_appointment"),
        ("ÅRSREGNSKAP", "legal_filing"),
        ("tVaNgSaVvIkLiNg", "insolvency"),
    ]
    casing_results = {}
    for term, exp in casing_cases:
        ev = classify_notice({"title": term, "date": "2026-01-01"})
        casing_results[term] = {"expected": exp, "actual": ev["category"], "pass": ev["category"] == exp}

    encoding_results = {}
    norwegian_chars = "æøåÆØÅ"
    for enc in ("utf-8", "windows-1252"):
        html = f"<html><body><table><tr><td>01.01.2025</td><td>Kapitalforhøyelse {norwegian_chars}</td></tr></table></body></html>".encode(enc)
        res = fetch_announcements("923609016", http_fetcher=lambda u, d: html)
        count = res["corporate_actions_count"]
        title = res["events"][0]["title"] if count > 0 else ""
        cat = res["events"][0]["category"] if count > 0 else ""
        encoding_results[enc] = {
            "count": count,
            "title": title,
            "category": cat,
            "pass": count == 1 and cat == "capital_change" and norwegian_chars in title,
        }

    return {"casing": casing_results, "encoding": encoding_results}


def test_large_scale_notice_fuzzing(num_notices: int = 500) -> dict[str, Any]:
    """Test performance and correctness with num_notices across multiple HTML tables."""
    rows = []
    for i in range(num_notices):
        day = (i % 28) + 1
        month = ((i // 28) % 12) + 1
        year = 2020 + (i // 336)
        d_str = f"{day:02d}.{month:02d}.{year:04d}"
        cats = [
            "Kapitalforhøyelse",
            "Styrets sammensetning",
            "Godkjent årsregnskap",
            "Konkurs",
            "Melding om flytting",
        ]
        title = cats[i % len(cats)] + f" #{i}"
        rows.append(f"<tr><td>{d_str}</td><td><a href=\"/hent_detaljer.jsp?id={i}\">{title}</a></td></tr>")

    # Split into multiple tables
    chunk_size = 50
    table_chunks = ["<table>" + "".join(rows[j:j + chunk_size]) + "</table>" for j in range(0, len(rows), chunk_size)]
    html = ("<html><body><table id=\"empty0\"><tr><td>wrapper</td></tr></table>" + "".join(table_chunks) + "</body></html>").encode("utf-8")

    latencies = []
    for _ in range(5):
        t0 = time.perf_counter()
        res = fetch_announcements("923609016", http_fetcher=lambda u, d: html)
        latencies.append((time.perf_counter() - t0) * 1000)

    avg_latency = sum(latencies) / len(latencies)
    min_latency = min(latencies)
    max_latency = max(latencies)

    # Verify descending sort invariant
    events = res["events"]
    dates = [e["date"] for e in events]
    is_sorted_desc = all(dates[i] >= dates[i + 1] for i in range(len(dates) - 1))

    # Verify categories count sum equals total events
    cats_sum = sum(c["count"] for c in res["categories"].values())

    return {
        "num_notices": num_notices,
        "parsed_count": res["corporate_actions_count"],
        "avg_latency_ms": round(avg_latency, 2),
        "min_latency_ms": round(min_latency, 2),
        "max_latency_ms": round(max_latency, 2),
        "under_150ms": avg_latency < 150.0,
        "is_sorted_desc": is_sorted_desc,
        "categories_sum_matches": cats_sum == num_notices,
    }


def test_evidence_hashing_integrity() -> dict[str, Any]:
    """Verify that content_sha256 in ev-announcements strictly matches hashlib.sha256(raw_bytes).hexdigest()."""
    raw_bytes = (
        b"<html><body><table>"
        b"<tr><td>15.08.2026</td><td><a href='hent_detaljer.jsp?id=99'>Kapitalforh\xc3\xb8yelse</a></td></tr>"
        b"</table></body></html>"
    )
    expected_hash = hashlib.sha256(raw_bytes).hexdigest()

    ann_res = fetch_announcements("923609016", http_fetcher=lambda u, d: raw_bytes)
    fetch_sha = ann_res.get("raw_html_sha256")

    # Pipeline A: Standard run_agent.py wiring
    profile_a = {
        "organisation_number": "923609016",
        "name": "TEST AS",
        "evidence": {
            "registry": {"status": "available", "value": {"navn": "TEST AS"}},
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
    env_a = build_output_envelope(profile_a, run_id="r1", started_at="t", completed_at="t")
    ev_a = next(e for e in env_a["evidence"] if e["id"] == "ev-announcements")

    # Pipeline B: direct profile["announcements"]
    profile_b = {
        "organisation_number": "923609016",
        "name": "TEST AS",
        "evidence": {"registry": {"status": "available", "value": {"navn": "TEST AS"}}},
        "announcements": ann_res,
    }
    env_b = build_output_envelope(profile_b, run_id="r2", started_at="t", completed_at="t")
    ev_b = next(e for e in env_b["evidence"] if e["id"] == "ev-announcements")

    # Pipeline C: bare evidence without top-level content_sha256
    profile_c = {
        "organisation_number": "923609016",
        "name": "TEST AS",
        "evidence": {
            "registry": {"status": "available", "value": {"navn": "TEST AS"}},
            "announcements": {
                "id": "ev-announcements",
                "status": "available",
                "source_url": ann_res["source_url"],
                "source_class": "official_public_notices",
                "value": ann_res,
            },
        },
    }
    env_c = build_output_envelope(profile_c, run_id="r3", started_at="t", completed_at="t")
    ev_c = next(e for e in env_c["evidence"] if e["id"] == "ev-announcements")

    return {
        "expected_hash": expected_hash,
        "fetch_res_hash": fetch_sha,
        "fetch_matches_expected": fetch_sha == expected_hash,
        "pipeline_a_ev_hash": ev_a["content_sha256"],
        "pipeline_a_matches": ev_a["content_sha256"] == expected_hash,
        "pipeline_b_ev_hash": ev_b["content_sha256"],
        "pipeline_b_matches": ev_b["content_sha256"] == expected_hash,
        "pipeline_c_ev_hash": ev_c["content_sha256"],
        "pipeline_c_matches": ev_c["content_sha256"] == expected_hash,
    }


def test_invariant_enforcement_count_zero() -> dict[str, Any]:
    """Verify that when corporate_actions_count == 0, contract invariants strictly hold."""
    profile = {
        "organisation_number": "923609016",
        "name": "EMPTY AS",
        "evidence": {
            "registry": {"status": "available", "value": {"navn": "EMPTY AS"}},
            "announcements": {
                "id": "ev-announcements",
                "status": "available",
                "source_class": "official_public_notices",
                "source_url": "https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr=923609016",
                "content_sha256": "0" * 64,
                "value": {
                    "events": [],
                    "categories": {
                        "capital_change": {"count": 0, "latest_date": None},
                        "board_appointment": {"count": 0, "latest_date": None},
                        "legal_filing": {"count": 0, "latest_date": None},
                        "insolvency": {"count": 0, "latest_date": None},
                        "other": {"count": 0, "latest_date": None},
                    },
                    "corporate_actions_count": 0,
                },
            },
        },
    }

    env = build_output_envelope(profile, run_id="r", started_at="t", completed_at="t")
    claims = {c["field"]: c for c in env["claims"]}

    c_count = claims.get("corporate_announcements_count", {})
    count_valid = (
        c_count.get("value") == 0
        and c_count.get("availability") == "available"
        and c_count.get("confidence") == 0.99
        and c_count.get("evidence_ids") == ["ev-announcements"]
    )

    latest_fields = [
        "latest_corporate_action_date",
        "latest_corporate_action_title",
        "latest_corporate_action_url",
        "latest_capital_change_date",
        "latest_board_appointment_date",
    ]
    latest_claims_valid = {}
    for f in latest_fields:
        c = claims.get(f, {})
        valid = (
            c.get("value") is None
            and c.get("availability") == "not_available"
            and c.get("confidence") == 1.0
            and c.get("evidence_ids") == ["ev-announcements"]
        )
        latest_claims_valid[f] = {
            "value": c.get("value"),
            "availability": c.get("availability"),
            "confidence": c.get("confidence"),
            "evidence_ids": c.get("evidence_ids"),
            "valid": valid,
        }

    return {
        "count_claim": c_count,
        "count_valid": count_valid,
        "latest_claims_valid": latest_claims_valid,
        "all_valid": count_valid and all(v["valid"] for v in latest_claims_valid.values()),
    }


def run_all() -> None:
    print("=" * 70)
    print("RUNNING ADVERSARIAL VERIFICATION HARNESS FOR MILESTONE 2")
    print("=" * 70)

    print("\n[1] Chaotic HTML: &nbsp; in titles:")
    res_titles = test_chaotic_html_non_breaking_spaces_in_titles()
    for title, info in res_titles.items():
        status = "PASS" if info["matched"] else "FAIL"
        print(f"  [{status}] {title!r} -> Expected: {info['expected']}, Actual: {info['actual']}")

    print("\n[2] Chaotic HTML: &nbsp; and spaces in dates:")
    res_dates = test_chaotic_html_non_breaking_spaces_in_dates()
    print(f"  nbsp_in_date_count: {res_dates['nbsp_in_date_count']} (Expected: 1, got {res_dates['nbsp_in_date_count']})")
    print(f"  spaces_in_date_count: {res_dates['spaces_in_date_count']} (Expected: 1, got {res_dates['spaces_in_date_count']})")
    print(f"  padded_date_count: {res_dates['padded_date_count']} (Expected: 1, got {res_dates['padded_date_count']})")

    print("\n[3] Casing & Norwegian Characters:")
    res_casing = test_chaotic_html_casing_and_norwegian_chars()
    for term, info in res_casing["casing"].items():
        print(f"  [{'PASS' if info['pass'] else 'FAIL'}] Casing {term!r} -> {info['actual']}")
    for enc, info in res_casing["encoding"].items():
        print(f"  [{'PASS' if info['pass'] else 'FAIL'}] Encoding {enc!r} -> title: {info['title']}, cat: {info['category']}")

    print("\n[4] Large Scale Notice Fuzzing (500 notices):")
    res_fuzz = test_large_scale_notice_fuzzing(500)
    print(f"  Parsed count: {res_fuzz['parsed_count']}/{res_fuzz['num_notices']}")
    print(f"  Latency: avg={res_fuzz['avg_latency_ms']}ms, min={res_fuzz['min_latency_ms']}ms, max={res_fuzz['max_latency_ms']}ms")
    print(f"  Performance <150ms: {res_fuzz['under_150ms']}")
    print(f"  Descending date order: {res_fuzz['is_sorted_desc']}")
    print(f"  Category sum matches: {res_fuzz['categories_sum_matches']}")

    print("\n[5] Evidence Hashing Integrity:")
    res_hash = test_evidence_hashing_integrity()
    print(f"  Expected hash: {res_hash['expected_hash']}")
    print(f"  Fetch res hash: {res_hash['fetch_res_hash']} (Matches: {res_hash['fetch_matches_expected']})")
    print(f"  Pipeline A (standard run_agent): {res_hash['pipeline_a_ev_hash']} (Matches: {res_hash['pipeline_a_matches']})")
    print(f"  Pipeline B (profile[announcements]): {res_hash['pipeline_b_ev_hash']} (Matches: {res_hash['pipeline_b_matches']})")
    print(f"  Pipeline C (bare evidence without top-level content_sha256): {repr(res_hash['pipeline_c_ev_hash'])} (Matches: {res_hash['pipeline_c_matches']})")

    print("\n[6] Invariant Enforcement (corporate_actions_count == 0):")
    res_inv = test_invariant_enforcement_count_zero()
    print(f"  corporate_announcements_count invariant valid: {res_inv['count_valid']}")
    print(f"    value: {res_inv['count_claim']['value']}, avail: {res_inv['count_claim']['availability']}, conf: {res_inv['count_claim']['confidence']}")
    for f, info in res_inv["latest_claims_valid"].items():
        print(f"  {f}: valid={info['valid']} (value={info['value']}, avail={info['availability']}, conf={info['confidence']})")
    print(f"  All invariants strictly hold: {res_inv['all_valid']}")


if __name__ == "__main__":
    run_all()
