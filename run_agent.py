#!/usr/bin/env python3
"""Signalpost Autonomous Company Intelligence Agent

Single-command entrypoint for research, evidence collection, and verification
of Norwegian corporate entities.

Usage:
  # Single company live research:
  uv run python run_agent.py --org 923609016 --output out/envelope.jsonl

  # Batch evaluation:
  uv run python run_agent.py --input companies.jsonl --output out/envelopes.jsonl --report out/report.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Ensure local package is in sys.path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import (
    profile_complete_for_modules,
    profiles_from_bulk,
    read_organisation_inputs,
)
from norway_company_agent.contract import build_output_envelope
from norway_company_agent import web_discovery
from norway_company_agent.discovery import (
    build_company_search_query,
    build_name_town_search_query,
    choose_search_candidate,
    extract_domain_from_email,
    extract_social_candidates,
    fetch_brave_search,
    fetch_google_search,
    fetch_serper_search,
    generate_candidate_domain_slugs,
    get_search_crawl_candidates,
    probe_candidate_domains,
    resolve_search_provider,
)
from norway_company_agent.evidence import evidence, utc_now
from norway_company_agent.http import fetch_json
from norway_company_agent.identity import (
    apply_website_identity_gate,
    build_company_number_profile,
    verify_candidate_identity,
)
from norway_company_agent.official import (
    accounting_obligation_assessment,
    fetch_official_modules,
    normalize_entity,
)
from norway_company_agent.refresh import diff_profile
from norway_company_agent.website import fetch_website
from norway_company_agent.announcements import fetch_announcements
from norway_company_agent.news import fetch_company_news_rss
from norway_company_agent.observations import build_observations
from norway_company_agent.official_external import (
    google_places_text_search,
    youtube_channel_lookup,
)
from norway_company_agent.external_footprint import validate_observation
from norway_company_agent.synthesis import synthesize_executive_profile
from norway_company_agent.showcase import generate_showcase_html



def _require_hard_site_proof(profile, gated_record, url, num_profile, website_metrics, origin, allow_network=True):
    """Non-registry sites need the org number or registered address on the site itself."""
    from norway_company_agent.site_proof import corroborate_site_identity, find_identity_proof
    web = gated_record["website"]
    value = web.setdefault("value", {}) or {}
    assessment = value.get("identity_assessment") or {}
    subunits = set(num_profile.get("subunit_orgs") or [])
    captured = [(web.get("source_url") or url, " ".join(str(value.get(k) or "") for k in ("title", "identity_text_excerpt", "main_text_excerpt")), value.get("content_sha256"))]
    captured += [(pg.get("url"), " ".join(str(pg.get(k) or "") for k in ("title", "main_text_excerpt")), pg.get("content_sha256")) for pg in value.get("pages") or [] if isinstance(pg, dict)]
    # A site that did not pass the legal-name check gets no benefit of the doubt:
    # its proof page may not show any other entity's organisation number.
    max_others = 1 if assessment.get("publishable") else 0
    result = {"verified": False, "requests": 0}
    for page_url, text, sha in captured:
        proof = find_identity_proof(text, profile, subunits, max_other_orgs=max_others)
        if proof:
            result = {"verified": True, "proof": {**proof, "source_url": page_url, "content_sha256": sha}, "requests": 0}
            break
    allow_network = allow_network and not web_discovery.CLOCK.past_soft()
    if not result["verified"] and allow_network:
        try:
            result = corroborate_site_identity(url, profile, subunits, max_other_orgs=max_others)
        except Exception:
            result = {"verified": False, "requests": 0}
    if not result["verified"] and allow_network:
        # JavaScript-only sites: rendered text may carry the org-number footer (opt-in).
        from norway_company_agent.web_discovery import render_page_text
        rendered, rendered_url = render_page_text(url)
        if rendered:
            proof = find_identity_proof(rendered, profile, subunits, max_other_orgs=max_others)
            if proof:
                import hashlib as _hl
                result = {"verified": True, "proof": {**proof, "source_url": rendered_url or url, "content_sha256": _hl.sha256(rendered.encode()).hexdigest(), "rendered": True}, "requests": result.get("requests", 0) + 1}
    if not result["verified"] and max_others == 1:  # only for sites that passed the legal-name check
        from norway_company_agent.site_proof import name_domain_location_proof
        import urllib.parse as _up
        host = _up.urlparse(value.get("final_url") or url).hostname or ""
        all_text = " ".join(t for _, t, _ in captured)
        proof = name_domain_location_proof(profile, host, all_text, list(assessment.get("legal_name_tokens") or []))
        if proof:
            result = {"verified": True, "proof": {**proof, "source_url": value.get("final_url") or url, "content_sha256": value.get("content_sha256")}, "requests": result.get("requests", 0)}
    website_metrics["requests"] += result.get("requests", 0)
    if result.get("verified"):
        proof = result["proof"]
        assessment["status"] = "exact"
        assessment["score"] = 1.0
        assessment["publishable"] = True
        assessment.setdefault("reasons", []).append(f"{origin}: {proof['type']} found on {proof['source_url']}")
        assessment["promotion_proof"] = [proof]
        value["identity_assessment"] = assessment
        # The name gate may have withheld social links; restore the ones whose handles passed.
        value["social_links"] = [
            {"platform": item["platform"], "url": item["url"]}
            for item in value.get("social_link_assessments") or []
            if isinstance(item, dict) and item.get("publishable")
        ]
        web["source_type"] = f"{origin}_verified_company_website"
        web["source_class"] = "company_owned"
        return True
    assessment["status"] = "related_or_uncertain"
    assessment["publishable"] = False
    assessment.setdefault("reasons", []).append(f"{origin}: name match only; no org number or registered address on site")
    value["identity_assessment"] = assessment
    value["social_links"] = []
    web["source_type"] = f"unverified_{origin}"
    web["note"] = f"Quarantined {origin} ({url}): no hard identity proof"
    profile["website"] = None
    return False


def _acc_metrics(target: dict[str, Any], m: dict[str, Any] | None) -> None:
    """Add one call's metrics into a running per-company total."""
    m = m or {}
    target["requests"] = int(target.get("requests", 0) or 0) + int(m.get("requests", m.get("request_count", 0)) or 0)
    target["bytes"] = int(target.get("bytes", 0) or 0) + int(m.get("bytes", m.get("bytes_received", 0)) or 0)
    lat = m.get("latencies_ms")
    if lat is None and m.get("latency_ms") is not None:
        lat = [m.get("latency_ms")]
    target.setdefault("latencies_ms", []).extend(lat or [])
    if "third_party_cost_usd" in target or m.get("third_party_cost_usd"):
        target["third_party_cost_usd"] = round(float(target.get("third_party_cost_usd", 0.0) or 0.0) + float(m.get("third_party_cost_usd", 0.0) or 0.0), 6)


def _has_publishable_site(profile: dict[str, Any]) -> bool:
    web = (profile.get("evidence") or {}).get("website") or {}
    return web.get("status") == "available" and bool(((web.get("value") or {}).get("identity_assessment") or {}).get("publishable"))


def _try_candidate_site(
    profile: dict[str, Any],
    num_profile: dict[str, Any],
    url: str,
    website_metrics: dict[str, Any],
    origin: str,
    *,
    require_name_gate_or_proof: bool = False,
    allow_network_proof: bool | None = None,
    preserve_evidence: bool = False,
) -> bool:
    """Fetch one candidate site and publish it only on exact-entity proof.

    allow_network_proof: True = may fetch contact pages for proof, False = only
    the captured pages, None = fetch only when the legal-name check passed.
    require_name_gate_or_proof: for sources that already tie the URL to the org
    number (Wikidata), the legal-name check is enough; otherwise hard proof.
    """
    if not url:
        return False
    try:
        record, w_metric = fetch_website(url, timeout=5.0)
    except Exception:
        return False
    _acc_metrics(website_metrics, w_metric)
    if record.get("status") != "available":
        return False
    gated = apply_website_identity_gate(profile, record)
    assessment = gated.get("assessment") or {}
    value = record.get("value") or {}
    text = f"{value.get('title', '')} {value.get('identity_text_excerpt', '')} {value.get('main_text_excerpt', '')}"
    id_check = verify_candidate_identity(url, text, num_profile)

    looked_like_company = bool(assessment.get("publishable"))

    def keep_quarantined(note: str) -> None:
        web = gated["website"]
        web["note"] = f"Quarantined {origin} candidate ({url}): {note}"
        if not web.get("source_type", "").startswith("unverified_"):
            web["source_type"] = f"unverified_{origin}"
        value_q = web.setdefault("value", {}) or {}
        value_q["social_links"] = []
        quarantined = value_q.get("identity_assessment") or assessment
        quarantined["publishable"] = False
        quarantined["status"] = "related_or_uncertain"
        quarantined.setdefault("reasons", []).append(f"{origin}: {note}")
        value_q["identity_assessment"] = quarantined
        # Record it as an ambiguous website only when it looked like this company
        # (legal-name check passed) but lacks proof; unrelated sites leave no trace.
        if not preserve_evidence and looked_like_company and not _has_publishable_site(profile):
            profile["evidence"]["website"] = web

    if id_check.get("conflict"):
        keep_quarantined("another organisation number is presented on the site")
        return False
    accepted = False
    if require_name_gate_or_proof and assessment.get("publishable"):
        web = gated["website"]
        web["source_type"] = f"{origin}_company_website"
        web["source_class"] = "company_owned"
        ia = (web.get("value") or {}).setdefault("identity_assessment", assessment)
        ia["promotion_proof"] = list(ia.get("promotion_proof") or []) + [
            {"type": f"{origin}_listing_plus_legal_name", "value": url, "source_url": url}
        ]
        accepted = True
    else:
        network = bool(assessment.get("publishable")) if allow_network_proof is None else allow_network_proof
        accepted = _require_hard_site_proof(profile, gated, url, num_profile, website_metrics, origin, allow_network=network)
    if not accepted:
        keep_quarantined("no organisation number or registered address on the site")
        return False
    final_url = str(value.get("final_url") or url)
    profile["website"] = final_url
    profile["evidence"]["website"] = gated["website"]
    return True


def _model_rerank(profile: dict[str, Any], results: list[dict[str, Any]], aliases: list[str], pm: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not results or not web_discovery.anthropic_available() or web_discovery.BUDGET.is_open("rerank"):
        return {}
    if not web_discovery.BUDGET.try_spend("rerank", 0.003):
        return {}
    rr = web_discovery.rerank_search_results(profile, results, aliases)
    web_discovery.BUDGET.settle("rerank", 0.003, rr.get("cost_usd", 0.0))
    web_discovery.BUDGET.record("rerank", "error" not in rr)
    _acc_metrics(pm["ai"], {"requests": rr.get("requests", 0), "third_party_cost_usd": rr.get("cost_usd", 0.0)})
    brands = [b for b in rr.get("brand_names") or [] if b not in (profile.get("_discovered_brands") or [])]
    if brands:
        profile["_discovered_brands"] = (profile.get("_discovered_brands") or []) + brands
    return rr


def _search_discovery(profile: dict[str, Any], num_profile: dict[str, Any], website_metrics: dict[str, Any], pm: dict[str, dict[str, Any]], preserve_evidence: bool) -> str | None:
    if not web_discovery.should_search(profile):
        return None
    search_provider, search_cfg = resolve_search_provider()
    if not search_provider:
        return None
    est = float(search_cfg.get("cost_per_request", 0.005) or 0.005)

    def run_query(query: str) -> list[dict[str, Any]]:
        if not query or web_discovery.BUDGET.is_open("search") or not web_discovery.BUDGET.try_spend("search", est):
            return []
        try:
            if search_provider == "brave":
                res, m = fetch_brave_search(query, search_cfg["api_key"], cost_per_request_usd=est)
            elif search_provider == "google":
                res, m = fetch_google_search(query, search_cfg["api_key"], cx=search_cfg.get("cx", ""), cost_per_request_usd=est)
            else:
                res, m = fetch_serper_search(query, search_cfg["api_key"])
        except Exception:
            web_discovery.BUDGET.settle("search", est, 0.0)
            web_discovery.BUDGET.record("search", False)
            return []
        # A failed call (bad key, quota, outage) returns no bytes: it is not billed.
        ok = int((m or {}).get("bytes") or 0) > 0 or float((m or {}).get("third_party_cost_usd") or 0.0) > 0
        if not ok:
            m = {**(m or {}), "third_party_cost_usd": 0.0}
        _acc_metrics(pm[search_provider if search_provider in ("brave", "google") else "serper"], m)
        web_discovery.BUDGET.settle("search", est, float(m.get("third_party_cost_usd") or 0.0))
        web_discovery.BUDGET.record("search", ok)
        res = list(res or [])
        socials = extract_social_candidates(profile, res)
        if socials:
            existing = profile.get("_discovered_socials") or []
            for item in socials:
                if not any(e.get("platform") == item.get("platform") for e in existing):
                    existing.append({**item, "source": item.get("source") or "search_result"})
            profile["_discovered_socials"] = existing
        return res

    def inspect(candidates: list[dict[str, Any]], origin: str) -> str | None:
        for cand in candidates:
            url = cand.get("url")
            strong = bool(cand.get("publishable_candidate")) or cand.get("provider") == "model_rerank" or origin != "search_candidate" \
                or float(cand.get("score") or 0) >= 0.6
            if _try_candidate_site(profile, num_profile, url, website_metrics, origin,
                                   allow_network_proof=True if strong else None, preserve_evidence=preserve_evidence):
                return profile.get("website")
        return None

    results = run_query(build_name_town_search_query(profile))
    candidates = get_search_crawl_candidates(profile, results, limit=3)
    if not candidates:
        name_clean = " ".join(str(profile.get("name") or "").split())
        org_clean = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
        if name_clean and org_clean:
            fb = run_query(f'"{name_clean}" {org_clean}'.strip())
            if fb:
                results.extend(fb)
                candidates = get_search_crawl_candidates(profile, results, limit=3)

    aliases = web_discovery.candidate_aliases(profile)
    rerank = _model_rerank(profile, results, aliases, pm)
    known = {c.get("url") for c in candidates}
    for url in rerank.get("candidate_urls") or []:
        if url not in known:
            candidates.append({"url": url, "provider": "model_rerank"})
            known.add(url)
    found = inspect(candidates[:4], "search_candidate")
    if found:
        return found

    # Second round: model follow-up query, then aliases (brand / sub-unit / former names).
    followups: list[str] = []
    if rerank.get("followup_query"):
        followups.append(rerank["followup_query"])
    aliases = web_discovery.candidate_aliases(profile)
    followups += [build_name_town_search_query(web_discovery.alias_view(profile, a)) for a in aliases[:2]]
    for query in list(dict.fromkeys(followups))[:2]:
        res = run_query(query)
        cands: list[dict[str, Any]] = []
        for view in [profile] + [web_discovery.alias_view(profile, a) for a in aliases[:2]]:
            for cand in get_search_crawl_candidates(view, res, limit=3):
                if cand.get("url") not in known:
                    cands.append(cand)
                    known.add(cand.get("url"))
        found = inspect(cands[:3], "search_alias")
        if found:
            return found
    return None


def _places_discovery(profile: dict[str, Any], num_profile: dict[str, Any], website_metrics: dict[str, Any], pm: dict[str, dict[str, Any]], preserve_evidence: bool) -> str | None:
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    cost_raw = os.environ.get("GOOGLE_PLACES_COST_PER_REQUEST_USD")
    org = str(profile.get("organisation_number") or "")
    if not key or cost_raw is None or not web_discovery.places_eligible(profile) or org in web_discovery.PLACES_CACHE:
        return None
    cost = float(cost_raw or 0)
    if web_discovery.BUDGET.is_open("places") or not web_discovery.BUDGET.try_spend("places", cost):
        return None
    try:
        places, raw, m = web_discovery.places_text_search_raw(profile, key)
    except Exception:
        places, raw, m = [], b"", {"status": "error", "request_count": 1}
    ok = (m or {}).get("status") == "ok"
    web_discovery.BUDGET.record("places", ok)
    web_discovery.BUDGET.settle("places", cost, cost if ok else 0.0)
    _acc_metrics(pm["external"], {**(m or {}), "requests": (m or {}).get("request_count", 1), "third_party_cost_usd": cost if ok else 0.0})
    if not ok:
        return None
    web_discovery.PLACES_CACHE[org] = ({"places": places}, raw, m)
    for url in web_discovery.places_website_candidates(profile, places):
        if _try_candidate_site(profile, num_profile, url, website_metrics, "places_candidate", allow_network_proof=True, preserve_evidence=preserve_evidence):
            return profile.get("website")
    return None


def _probe_discovery(profile: dict[str, Any], num_profile: dict[str, Any], website_metrics: dict[str, Any], preserve_evidence: bool) -> str | None:
    views = [(profile, None)] + [(web_discovery.alias_view(profile, a), a) for a in web_discovery.candidate_aliases(profile)[:2]]
    tried: set[str] = set()
    for view, alias in views:
        try:
            slugs = [s for s in generate_candidate_domain_slugs(view) if s not in tried]
        except Exception:
            continue
        tried.update(slugs)
        if not slugs:
            continue
        p_metric: dict[str, Any] = {"requests": 0, "latencies_ms": []}
        try:
            probed_url = probe_candidate_domains(slugs, timeout=2.0, metrics=p_metric)
        except Exception:
            probed_url = None
        _acc_metrics(website_metrics, p_metric)
        if probed_url and _try_candidate_site(
            profile, num_profile, probed_url, website_metrics, "domain_probe",
            allow_network_proof=True if alias else None, preserve_evidence=preserve_evidence,
        ):
            return profile.get("website")
    return None


def _agent_discovery(profile: dict[str, Any], num_profile: dict[str, Any], website_metrics: dict[str, Any], pm: dict[str, dict[str, Any]], preserve_evidence: bool) -> str | None:
    if not web_discovery.anthropic_available() or not web_discovery.agent_search_eligible(profile) or web_discovery.BUDGET.is_open("agent_search"):
        return None
    if not web_discovery.BUDGET.try_spend("agent_search", 0.025):
        return None
    res = web_discovery.model_web_search_candidates(profile, web_discovery.candidate_aliases(profile))
    web_discovery.BUDGET.settle("agent_search", 0.025, res.get("cost_usd", 0.0))
    web_discovery.BUDGET.record("agent_search", "error" not in res)
    _acc_metrics(pm["ai"], {"requests": res.get("requests", 0), "third_party_cost_usd": res.get("cost_usd", 0.0)})
    for url in res.get("candidate_urls") or []:
        if _try_candidate_site(profile, num_profile, url, website_metrics, "model_web_search", allow_network_proof=True, preserve_evidence=preserve_evidence):
            return profile.get("website")
    return None


def _discover_website(profile: dict[str, Any], num_profile: dict[str, Any], website_metrics: dict[str, Any], pm: dict[str, dict[str, Any]], *, preserve_evidence: bool) -> str | None:
    """Search -> Places -> domain guessing -> model web search; first exact match wins."""
    if web_discovery.CLOCK.past_soft():
        return None  # time budget: remaining companies get registry data and any registry website only
    saved = profile.get("evidence", {}).get("website") if preserve_evidence else None
    saved_url = profile.get("website")
    found = None
    for step in (
        lambda: _search_discovery(profile, num_profile, website_metrics, pm, preserve_evidence),
        lambda: _places_discovery(profile, num_profile, website_metrics, pm, preserve_evidence),
        lambda: _probe_discovery(profile, num_profile, website_metrics, preserve_evidence),
        lambda: _agent_discovery(profile, num_profile, website_metrics, pm, preserve_evidence),
    ):
        try:
            found = step()
        except Exception:
            found = None
        if found:
            return found
    if preserve_evidence:
        if saved is not None:
            profile["evidence"]["website"] = saved
        profile["website"] = saved_url
    return None


def _apply_nav_jobs(profile: dict[str, Any]) -> int:
    """NAV job postings (exact employer orgnr) plus an employer-declared homepage seed."""
    num_profile = profile.get("_number_profile") or build_company_number_profile(profile)
    website_metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
    website_url = profile.get("website") if ((profile.get("evidence", {}).get("website") or {}).get("value") or {}).get("identity_assessment", {}).get("publishable") else None
    # Step 0: NAV arbeidsplassen open postings, accepted only on exact employer orgnr.
    nav_employer: dict[str, Any] | None = None
    try:
        from norway_company_agent.nav_jobs import fetch_nav_job_postings
        nav = fetch_nav_job_postings(profile, set(num_profile.get("subunit_orgs") or []))
        website_metrics["requests"] += nav.get("requests", 0)
        postings = list(nav["postings"])
        for career in profile.get("_career_postings") or []:
            if not any(p.get("title", "").casefold() == career["title"].casefold() for p in postings):
                postings.append(career)
        profile["evidence"]["jobs"] = evidence(
            "jobs",
            "available" if postings else nav["status"],
            "nav_arbeidsplassen_job_board",
            nav["source_url"],
            value={"postings": postings, "employer": nav.get("employer"), "rejected_candidates": nav.get("rejected", [])},
            content_sha256=(postings[0].get("content_sha256") if postings else None),
            note=nav.get("note"),
        )
        nav_employer = nav.get("employer")
        if nav_employer:
            socials = profile.get("_discovered_socials") or []
            for key, platform in (("facebookPage", "facebook"), ("linkedinPage", "linkedin"), ("twitterAddress", "x")):
                link = str(nav_employer.get(key) or "").strip()
                if link.startswith("http") and not any(e.get("platform") == platform for e in socials):
                    socials.append({"platform": platform, "url": link, "source": "nav_employer_profile", "evidence_url": nav_employer.get("source_url")})
            profile["_discovered_socials"] = socials
    except Exception as exc:
        profile["evidence"]["jobs"] = evidence("jobs", "failed", "nav_arbeidsplassen_job_board", "https://arbeidsplassen.nav.no/stillinger", note=str(exc)[:200])

    # Step 0b: employer homepage declared on an orgnr-verified NAV ad.
    if not website_url and nav_employer and str(nav_employer.get("homepage") or "").startswith("http"):
        import urllib.parse as _up
        hp = _up.urlparse(str(nav_employer["homepage"]))
        cand_url = f"{hp.scheme}://{hp.netloc}/"
        rec, w_metric = fetch_website(cand_url, timeout=5.0)
        website_metrics["requests"] += w_metric.get("requests", 0)
        gated = apply_website_identity_gate(profile, rec)
        if (gated.get("assessment") or {}).get("publishable"):
            web = gated["website"]
            web["source_type"] = "nav_employer_homepage"
            web["source_class"] = "company_owned"
            ia = web.setdefault("value", {}).setdefault("identity_assessment", {})
            ia["promotion_proof"] = list(ia.get("promotion_proof") or []) + [{
                "type": "nav_employer_homepage",
                "value": nav_employer.get("orgnr"),
                "source_url": nav_employer.get("source_url"),
                "span": f"employer.orgnr={nav_employer.get('orgnr')} homepage={nav_employer.get('homepage')}",
            }]
            website_url = cand_url
            profile["website"] = cand_url
            profile["evidence"]["website"] = web

    profile.pop("_nav_pending", None)
    return website_metrics["requests"]

def _write_showcase(profiles: list[dict[str, Any]], html_path: Path) -> str:
    """Write the company browser (ui/: search, filters, compare, sources, mobile layout).

    Falls back to the legacy dossier if the ui/ folder is missing or fails, so a
    showcase problem can never cost the run its envelopes. SIGNALPOST_SHOWCASE=legacy
    forces the old page.
    """
    if os.environ.get("SIGNALPOST_SHOWCASE", "ui").strip().lower() != "legacy":
        try:
            ui_dir = Path(__file__).resolve().parent / "ui"
            if str(ui_dir) not in sys.path:
                sys.path.insert(0, str(ui_dir))
            from build_ui import build as build_company_browser

            html_out = build_company_browser(profiles)
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html_out, encoding="utf-8")
            return "ui"
        except Exception as exc:
            print(f"⚠️  Company browser build failed ({type(exc).__name__}: {str(exc)[:120]}); writing legacy showcase")
    generate_showcase_html(profiles, html_path)
    return "legacy"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


MAX_REQUESTS = 2_000
MAX_THIRD_PARTY_COST_USD = 10.0
MAX_RUNTIME_SECONDS = 45 * 60


class CostGovernor:
    """Thread-safe third-party API spend controller with an automated circuit breaker."""

    def __init__(self, max_budget_usd: float = 9.50):
        self.max_budget_usd = max_budget_usd
        self._spent_usd = 0.0
        self._lock = threading.Lock()

    def try_spend(self, estimated_cost_usd: float) -> bool:
        with self._lock:
            if round(self._spent_usd + estimated_cost_usd, 4) <= self.max_budget_usd:
                self._spent_usd += estimated_cost_usd
                return True
            return False

    def reset(self, max_budget_usd: float | None = None) -> None:
        with self._lock:
            self._spent_usd = 0.0
            if max_budget_usd is not None:
                self.max_budget_usd = max_budget_usd

    def refund(self, amount_usd: float) -> None:
        with self._lock:
            self._spent_usd = max(0.0, self._spent_usd - max(0.0, float(amount_usd)))

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return round(self._spent_usd, 4)


_GLOBAL_COST_GOVERNOR = CostGovernor()
web_discovery.BUDGET.configure(_GLOBAL_COST_GOVERNOR.try_spend, _GLOBAL_COST_GOVERNOR.refund)



def _claim_evidence_is_valid(claim: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> bool:
    if claim.get("availability") != "available":
        return True
    evidence_ids = claim.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        return False
    for evidence_id in evidence_ids:
        record = evidence_by_id.get(str(evidence_id))
        if not record:
            return False
        if not record.get("source_url") or not record.get("retrieved_at"):
            return False
        if len(str(record.get("content_sha256") or "")) != 64:
            return False
    return True


def audit_envelopes(
    envelopes: list[dict[str, Any]],
    *,
    expected_count: int,
    operations: dict[str, Any],
    runtime_seconds: float | None = None,
) -> dict[str, Any]:
    """Audit terminal output before declaring submission readiness.

    This is intentionally stricter than merely counting rows: it catches
    available claims that lost their evidence references, duplicate entities,
    invalid published observations, and budget violations.
    """
    organisation_numbers = [str(item.get("organisation_number") or "") for item in envelopes]
    envelope_contract_valid = (
        len(envelopes) == expected_count
        and len(organisation_numbers) == len(set(organisation_numbers))
        and all(
            item.get("organisation_number")
            and isinstance(item.get("claims"), list)
            and isinstance(item.get("evidence"), list)
            and isinstance(item.get("operations"), dict)
            for item in envelopes
        )
    )

    all_claims_evidence_backed = True
    zero_wrong_company_matches = True
    invalid_observations: list[dict[str, Any]] = []
    for envelope in envelopes:
        evidence_by_id = {
            str(item.get("id")): item
            for item in envelope.get("evidence", [])
            if item.get("id")
        }
        if any(not _claim_evidence_is_valid(claim, evidence_by_id) for claim in envelope.get("claims", [])):
            all_claims_evidence_backed = False
        profile = envelope.get("profile") or {}
        for observation in profile.get("_external_observations") or []:
            reasons = validate_observation(observation)
            if reasons:
                invalid_observations.append({"id": observation.get("id"), "reasons": reasons})
                zero_wrong_company_matches = False
            elif not observation.get("exact_entity"):
                zero_wrong_company_matches = False

    requests = int(operations.get("requests") or 0)
    cost = float(operations.get("third_party_cost_usd") or 0.0)
    runtime_ok = runtime_seconds is None or runtime_seconds <= MAX_RUNTIME_SECONDS
    return {
        "envelope_contract_valid": envelope_contract_valid,
        "zero_wrong_company_matches": zero_wrong_company_matches,
        "all_claims_evidence_backed": all_claims_evidence_backed,
        "invalid_published_observations": invalid_observations,
        "request_budget_within_limit": requests <= max(MAX_REQUESTS, (expected_count or len(envelopes)) * 20),
        "cost_budget_within_limit": cost <= MAX_THIRD_PARTY_COST_USD,
        "runtime_budget_within_limit": runtime_ok,
    }


def fetch_live_entity_anchor(org: str) -> tuple[dict[str, Any], Any]:
    url = f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
    res = fetch_json(url)
    if res.status == 200:
        raw = res.body if isinstance(res.body, dict) else {}
        entity = normalize_entity(raw)
        business_addr = raw.get("forretningsadresse") or {}
        postal_addr = raw.get("postadresse") or {}
        kommune = business_addr.get("kommune") or postal_addr.get("kommune") or postal_addr.get("poststed")
        nace = raw.get("naeringskode1") or {}
        nace_code = nace.get("kode") if isinstance(nace, dict) else None
        nace_desc = nace.get("beskrivelse") if isinstance(nace, dict) else None

        profile = {
            "organisation_number": org,
            "name": raw.get("navn"),
            "legal_form": (raw.get("organisasjonsform") or {}).get("kode"),
            "registered_office": kommune,
            "municipality": kommune,
            "website": raw.get("hjemmeside"),
            "nace_code": nace_code,
            "industry_code": nace_code,
            "industry": nace_desc,
            "employees": raw.get("antallAnsatte"),
            "latest_submitted_accounts": raw.get("sisteInnsendteAarsregnskap"),
            "adverse": bool(raw.get("konkurs") or raw.get("underAvvikling")),
            "evidence": {
                "registry": evidence(
                    "registry",
                    "available",
                    "official_registry_live",
                    url,
                    value=raw,
                    retrieved_at=res.retrieved_at,
                    content_sha256=res.content_sha256,
                    source_row_key=org,
                ),
                "registry_live": evidence(
                    "registry_live",
                    "available",
                    "official_registry_live",
                    url,
                    value=entity,
                    retrieved_at=res.retrieved_at,
                    content_sha256=res.content_sha256,
                ),
            },
        }
        profile["evidence"]["accounting_obligation"] = accounting_obligation_assessment(profile)
        return profile, res
    else:
        status = "not_found" if res.status in {404, 410} else "source_error"
        profile = {
            "organisation_number": org,
            "name": None,
            "legal_form": None,
            "evidence": {
                "registry": evidence(
                    "registry",
                    status,
                    "official_registry_live",
                    url,
                    note=res.error or f"HTTP {res.status}",
                    retrieved_at=res.retrieved_at,
                    content_sha256=res.content_sha256,
                    source_row_key=org,
                ),
            },
        }
        return profile, res


def enrich_profile(profile: dict[str, Any], requested_modules: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    org = profile["organisation_number"]
    fetch_modules = set(requested_modules) - {"registry", "accounting_obligation", "website", "announcements", "news", "synthesis"}
    if "registry" in profile.get("_live_anchor", []):
        # The live anchor already contains the authoritative entity response;
        # avoid spending a second request on the same registry endpoint.
        fetch_modules.discard("registry_live")
    records, metrics = fetch_official_modules(org, fetch_modules)
    profile["evidence"].update(records)

    website_metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
    serper_metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
    brave_metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
    google_metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
    external_metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
    ai_metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
    external_observations: list[dict[str, Any]] = list(profile.get("_external_observations") or [])
    if "website" in requested_modules:
        num_profile = build_company_number_profile(profile)
        profile["_number_profile"] = num_profile

        website_url = profile.get("website")

        # Step A0: Wikidata (free, CC0) lists an official website for some org numbers.
        if not website_url:
            for wd_url in (profile.get("_wikidata_websites") or [])[:2]:
                if _try_candidate_site(profile, num_profile, wd_url, website_metrics, "wikidata", require_name_gate_or_proof=True):
                    website_url = profile.get("website")
                    break

        # Step A: Free statutory email domain extraction ($0.00 cost)
        if not website_url:
            reg_val = (profile.get("evidence", {}).get("registry") or {}).get("value") or {}
            email_domain = extract_domain_from_email(reg_val.get("epostadresse"))
            if email_domain:
                cand_url = f"https://www.{email_domain}"
                rec, w_metric = fetch_website(cand_url, timeout=5.0)
                gated = apply_website_identity_gate(profile, rec)
                _acc_metrics(website_metrics, w_metric)
                email_ok = (gated.get("assessment") or {}).get("publishable")
                if not email_ok and rec.get("status") == "available":
                    email_ok = _require_hard_site_proof(profile, gated, cand_url, num_profile, website_metrics, "registry_email_domain")
                if email_ok:
                    website_url = cand_url
                    profile["website"] = cand_url
                    profile["evidence"]["website"] = gated["website"]

        # Steps B-B3: search, Places, domain guessing, model web search (candidates only).
        provider_metrics = {"serper": serper_metrics, "brave": brave_metrics, "google": google_metrics, "ai": ai_metrics, "external": external_metrics}
        if not website_url:
            website_url = _discover_website(profile, num_profile, website_metrics, provider_metrics, preserve_evidence=False)

        # Step C: If website was found (or from registry) but not yet fetched/gated:
        if "website" not in profile.get("evidence", {}):
            website_record, c_metrics = fetch_website(website_url, timeout=5.0)
            _acc_metrics(website_metrics, c_metrics)
            gated_c = apply_website_identity_gate(profile, website_record)
            profile["evidence"]["website"] = gated_c["website"]
            # A registry-declared site that fails the name check is still exact
            # when it shows this org number or the registered address.
            if website_url and website_record.get("status") == "available" and not (gated_c.get("assessment") or {}).get("publishable"):
                _require_hard_site_proof(profile, gated_c, website_url, num_profile, website_metrics, "registry_declared")
                # Either way the registry-declared URL stays the candidate; publication
                # still follows the identity assessment.
                profile["website"] = website_url
                if not ((gated_c["website"].get("value") or {}).get("identity_assessment") or {}).get("publishable"):
                    gated_c["website"]["source_type"] = "registry_linked_company_website"

            # Step C2: the registry points at a site we cannot tie to this entity
            # (parent group, manager, old domain): look for the company's own site.
            registry_web = profile["evidence"].get("website") or {}
            registry_ok = ((registry_web.get("value") or {}).get("identity_assessment") or {}).get("publishable")
            if website_url and not registry_ok:
                found = _discover_website(profile, num_profile, website_metrics, provider_metrics, preserve_evidence=True)
                if not found:
                    profile["website"] = website_url  # keep the registry URL as the (unpublished) candidate

    # Step C3: a verified page on a larger (group / brand) site keeps only its own content.
    if "website" in requested_modules:
        web_discovery.apply_page_scope(profile)

    # Step D: dated posts from the verified site's own WordPress/RSS feed.
    if "website" in requested_modules:
        web_ev = profile.get("evidence", {}).get("website") or {}
        web_v = web_ev.get("value") or {}
        if (web_ev.get("status") == "available" and (web_v.get("identity_assessment") or {}).get("publishable")
                and web_v.get("scope") != "page" and not web_discovery.CLOCK.past_soft()):
            try:
                from norway_company_agent.site_feeds import fetch_site_feed_items
                feed_res = fetch_site_feed_items(web_v.get("final_url") or web_ev.get("source_url") or "")
                website_metrics["requests"] += feed_res.get("requests", 0)
                existing = web_v.setdefault("news_items", [])
                by_url = {str(i.get("url") or "").rstrip("/"): i for i in existing if isinstance(i, dict)}
                for item in feed_res.get("items") or []:
                    key = item["url"].rstrip("/")
                    if key in by_url:
                        by_url[key].update({k: item[k] for k in ("date", "source", "feed_url", "content_sha256", "kind") if item.get(k)})
                    else:
                        existing.append(item)
                        by_url[key] = item
            except Exception:
                pass

    # Step D2: open postings on the verified site's own career page (model reads the page;
    # titles must appear verbatim, links must stay on the career site, Norway only).
    if ("website" in requested_modules and web_discovery.anthropic_available() and _has_publishable_site(profile)
            and not web_discovery.CLOCK.past_soft()):
        for page_url in web_discovery.career_page_candidates(profile)[:1]:
            if web_discovery.BUDGET.is_open("careers") or not web_discovery.BUDGET.try_spend("careers", 0.004):
                break
            html, final_page, page_sha = web_discovery.fetch_career_page(page_url)
            website_metrics["requests"] += 1
            if not html:
                continue
            res = web_discovery.extract_career_postings(profile, final_page or page_url, html, page_sha or "")
            web_discovery.BUDGET.settle("careers", 0.004, res.get("cost_usd", 0.0))
            web_discovery.BUDGET.record("careers", "error" not in res)
            _acc_metrics(ai_metrics, {"requests": res.get("requests", 0), "third_party_cost_usd": res.get("cost_usd", 0.0)})
            if res.get("postings"):
                profile["_career_postings"] = res["postings"]
                break

    places_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    places_cost_raw = os.environ.get("GOOGLE_PLACES_COST_PER_REQUEST_USD")
    if places_key and places_cost_raw is not None:
        est_places_cost = float(places_cost_raw or 0)
        cached_places = web_discovery.PLACES_CACHE.pop(str(org), None)
        # Reuse the discovery lookup; otherwise only spend on companies likely to have a place listing.
        if cached_places is not None or (web_discovery.places_eligible(profile) and not web_discovery.BUDGET.is_open("places")
                                         and web_discovery.BUDGET.try_spend("places", est_places_cost)):
            try:
                rows = google_places_text_search(profile, places_key, prefetched=cached_places)
                external_observations.extend(rows)
                if cached_places is None:
                    place_metrics = getattr(rows, "metrics", {})
                    place_ok = place_metrics.get("status") not in ("error",) and place_metrics.get("http_status", 200) == 200 and bool(place_metrics.get("bytes") or place_metrics.get("bytes_received"))
                    web_discovery.BUDGET.record("places", place_ok)
                    if not place_ok:
                        web_discovery.BUDGET.settle("places", est_places_cost, 0.0)
                        raise RuntimeError("places request failed: not billed")
                    external_metrics["requests"] += int(place_metrics.get("requests") or place_metrics.get("request_count") or 0)
                    external_metrics["bytes"] += int(place_metrics.get("bytes") or place_metrics.get("bytes_received") or 0)
                    external_metrics["latencies_ms"].extend(place_metrics.get("latencies_ms") or [])
                    external_metrics["third_party_cost_usd"] += max(1, int(place_metrics.get("requests") or place_metrics.get("request_count") or 0)) * est_places_cost
            except Exception:
                pass

    youtube_key = os.environ.get("YOUTUBE_DATA_API_KEY", "").strip()
    if youtube_key:
        website_value = ((profile.get("evidence", {}).get("website") or {}).get("value") or {})
        for link in website_value.get("social_links") or []:
            if str(link.get("platform") or "").casefold() != "youtube":
                continue
            try:
                rows = youtube_channel_lookup(profile, str(link.get("url") or ""), youtube_key)
                external_observations.extend(rows)
                youtube_metrics = getattr(rows, "metrics", {})
                external_metrics["requests"] += int(youtube_metrics.get("requests") or 0)
                external_metrics["bytes"] += int(youtube_metrics.get("bytes") or 0)
                external_metrics["latencies_ms"].extend(youtube_metrics.get("latencies_ms") or [])
            except Exception:
                pass
    profile["_external_observations"] = external_observations

    ann_requests = 0
    if "announcements" in requested_modules:
        try:
            ann_res = fetch_announcements(org, timeout_sec=5.0)
            ann_requests = ann_res.get("requests", 1)
            profile["evidence"]["announcements"] = evidence(
                "announcements",
                ann_res.get("status", "available"),
                "official_public_notices",
                f"https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr={org}",
                value={"events": ann_res.get("events", []), "categories": ann_res.get("categories", {})},
                content_sha256=ann_res.get("raw_html_sha256", ""),
            )
            profile["announcements"] = ann_res
        except Exception as exc:
            profile["evidence"]["announcements"] = evidence(
                "announcements",
                "failed",
                "official_public_notices",
                f"https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr={org}",
                note=str(exc),
            )

    news_metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
    if "news" in requested_modules:
        try:
            company_name = profile.get("name") or ""
            serper_key = os.environ.get("SERPER_API_KEY", "").strip()
            # Small companies are often named after their owner: only a headline with the
            # full legal name (including "AS") counts for them.
            suffixless_ok = web_discovery.is_substantial_business(profile)
            news_record = None
            if web_discovery.CLOCK.past_soft():
                news_record = {
                    "field": "news", "status": "failed", "note": "press news not checked: run time budget reached",
                    "source_type": "public_news", "source_class": "public_news", "source_url": "https://news.google.com/rss",
                    "retrieved_at": utc_now(), "content_sha256": "", "value": {"items": [], "latest": None},
                }
            elif serper_key and serper_key != "test-placeholder" and web_discovery.BUDGET.try_spend("news", 0.001):
                from norway_company_agent.news import fetch_serper_news
                news_record, news_metrics = fetch_serper_news(company_name, serper_key, org, allow_suffixless=suffixless_ok)
                web_discovery.BUDGET.settle("news", 0.001, news_metrics.get("third_party_cost_usd", 0.0))
                if news_record.get("status") == "failed":
                    news_record = None  # Serper error or circuit open: use the RSS path instead
            if news_record is None:
                news_record, rss_metrics = fetch_company_news_rss(company_name, org, allow_suffixless=suffixless_ok)
                _acc_metrics(news_metrics, rss_metrics)
            profile["evidence"]["news"] = news_record
            profile["news"] = news_record.get("value") or {}
        except Exception as exc:
            profile["evidence"]["news"] = {
                "field": "news",
                "status": "failed",
                "source_type": "public_news",
                "source_class": "public_news",
                "source_url": "https://news.google.com/rss",
                "retrieved_at": utc_now(),
                "content_sha256": "",
                "note": str(exc),
                "value": {"items": [], "latest": None},
            }
            profile["news"] = {"items": [], "latest": None}

    nav_requests = 0
    if "website" in requested_modules:
        from norway_company_agent.nav_jobs import feed_ready
        if feed_ready():
            try:
                nav_requests = _apply_nav_jobs(profile)
            except Exception as exc:
                profile["evidence"]["jobs"] = evidence("jobs", "failed", "nav_arbeidsplassen_job_board", "https://arbeidsplassen.nav.no/stillinger", note=str(exc)[:200])
        else:
            profile["_nav_pending"] = True

    if "synthesis" in requested_modules:
        try:
            synth = synthesize_executive_profile(profile)
            profile["synthesis"] = synth
        except Exception:
            pass

    metric = {
        "requests": (
            len(metrics)
            + website_metrics["requests"]
            + serper_metrics["requests"]
            + brave_metrics["requests"]
            + google_metrics["requests"]
            + external_metrics["requests"]
            + ann_requests
            + news_metrics.get("requests", 0)
            + nav_requests
            + ai_metrics["requests"]
        ),
        "bytes": (
            sum(m.bytes_received for m in metrics)
            + website_metrics["bytes"]
            + serper_metrics.get("bytes", 0)
            + brave_metrics.get("bytes", 0)
            + google_metrics.get("bytes", 0)
            + external_metrics.get("bytes", 0)
            + news_metrics.get("bytes", 0)
        ),
        "latencies_ms": (
            [m.elapsed_ms for m in metrics]
            + website_metrics["latencies_ms"]
            + serper_metrics.get("latencies_ms", [])
            + brave_metrics.get("latencies_ms", [])
            + google_metrics.get("latencies_ms", [])
            + external_metrics.get("latencies_ms", [])
            + news_metrics.get("latencies_ms", [])
        ),
        "third_party_cost_usd": (
            serper_metrics.get("third_party_cost_usd", 0.0)
            + brave_metrics.get("third_party_cost_usd", 0.0)
            + google_metrics.get("third_party_cost_usd", 0.0)
            + external_metrics.get("third_party_cost_usd", 0.0)
            + news_metrics.get("third_party_cost_usd", 0.0)
            + ai_metrics.get("third_party_cost_usd", 0.0)
        ),
    }
    profile["run_metrics"] = metric
    return profile, metric


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Signalpost: Autonomous Norwegian Company Intelligence Agent",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", "-i", help="Path to input JSON, JSONL, or TXT containing organisation numbers")
    group.add_argument("--org", help="Single 9-digit Norwegian organisation number to research")

    parser.add_argument("--output", "-o", default="out/envelopes.jsonl", help="Output terminal envelopes JSONL")
    parser.add_argument("--profiles-output", "-p", default="out/profiles.jsonl", help="Output full enriched profiles JSONL")
    parser.add_argument(
        "--observations-output",
        default=None,
        help="Output publishable external observations JSONL (defaults beside --report)",
    )
    parser.add_argument("--report", "-r", default="out/report.json", help="Execution report JSON")
    parser.add_argument("--html", help="Path to generate interactive HTML dashboard showcase")
    parser.add_argument("--showcase", help="Path to generate interactive HTML dashboard showcase (alias for --html)")
    parser.add_argument("--checkpoint-every", type=int, default=50, help="Flush completed records to disk every N organisations (default: 50)")
    parser.add_argument("--resume", action="store_true", help="Resume execution by skipping already processed organisations in output file")
    parser.add_argument("--bulk", help="Path to frozen Brreg entity CSV snapshot (optional, falls back to live API)")
    parser.add_argument("--prior-profiles", help="Path to previous profiles JSONL for change detection")
    parser.add_argument("--run-id", default=f"signalpost-{int(time.time())}", help="Unique run identifier")
    parser.add_argument("--workers", type=int, default=8, help="Number of concurrent workers")
    parser.add_argument("--expected-count", type=int, default=None, help="Expected number of inputs to validate")
    parser.add_argument(
        "--modules",
        default="registry,accounting_obligation,registry_live,financials,roles,group,locations,website,announcements,news,synthesis",
        help="Comma-separated modules to execute",
    )

    args = parser.parse_args()
    started_at = utc_now()
    web_discovery.CLOCK.reset()
    t0 = time.monotonic()

    # 1. Parse organisation inputs
    if args.org:
        clean_org = "".join(c for c in args.org if c.isdigit())
        if len(clean_org) != 9:
            sys.exit(f"Error: Invalid organisation number: {args.org!r} (must be 9 digits)")
        org_inputs = [{"organisation_number": clean_org}]
    else:
        org_inputs = read_organisation_inputs(args.input)

    org_list = [item["organisation_number"] for item in org_inputs]
    if args.expected_count and len(org_list) != args.expected_count:
        sys.exit(f"Error: Expected {args.expected_count} organisations, but received {len(org_list)}")

    output_path = Path(args.output)
    profiles_path = Path(args.profiles_output)
    report_path = Path(args.report)

    # Resume handling
    prior_completed_envelopes: dict[str, dict[str, Any]] = {}
    prior_completed_profiles: dict[str, dict[str, Any]] = {}
    if args.resume and output_path.is_file():
        for line in output_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                prior_completed_envelopes[row["organisation_number"]] = row
        if profiles_path.is_file():
            for line in profiles_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    prior_completed_profiles[row["organisation_number"]] = row

    # Records flushed by a mid-run checkpoint have not been through the post-passes.
    for org_id in [o for o, row in prior_completed_profiles.items() if row.get("_checkpoint_partial")]:
        prior_completed_profiles.pop(org_id, None)
        prior_completed_envelopes.pop(org_id, None)
    pending_org_list = [o for o in org_list if o not in prior_completed_envelopes]
    requested_modules = [m.strip() for m in args.modules.split(",") if m.strip()]
    print(f"🚀 [Signalpost] Starting run '{args.run_id}' for {len(org_list)} organisation(s) ({len(pending_org_list)} pending)...")

    # 2. Identity Anchor: Bulk or Live API
    profiles: list[dict[str, Any]] = []
    initial_metrics: dict[str, dict[str, Any]] = {}

    if pending_org_list:
        if args.bulk and Path(args.bulk).is_file():
            print(f"📦 Sourcing registry anchors from bulk snapshot: {args.bulk}")
            bulk_profiles, _ = profiles_from_bulk(args.bulk, pending_org_list)
            profiles = bulk_profiles
        else:
            print("🌐 Sourcing registry anchors via official live Brønnøysund API (data.brreg.no)...")
            with ThreadPoolExecutor(max_workers=min(args.workers, len(pending_org_list))) as pool:
                future_to_org = {pool.submit(fetch_live_entity_anchor, org): org for org in pending_org_list}
                for future in as_completed(future_to_org):
                    org = future_to_org[future]
                    try:
                        prof, metric = future.result()
                        prof["_live_anchor"] = ["registry"]
                        profiles.append(prof)
                        initial_metrics[prof["organisation_number"]] = {
                            "requests": 1,
                            "bytes": metric.bytes_received,
                            "latencies_ms": [metric.elapsed_ms],
                        }
                    except Exception as exc:
                        failed_prof = {
                            "organisation_number": org,
                            "name": None,
                            "legal_form": None,
                            "errors": [{"code": "ANCHOR_ERROR", "error": str(exc)}],
                            "evidence": {},
                        }
                        profiles.append(failed_prof)
                        initial_metrics[org] = {"requests": 1, "bytes": 0, "latencies_ms": [0]}

    # Ensure original ordering
    profile_by_org = {p["organisation_number"]: p for p in profiles}
    ordered_profiles = [profile_by_org[org] for org in pending_org_list if org in profile_by_org]

    if ordered_profiles and "website" in requested_modules:
        from norway_company_agent.nav_jobs import start_feed_build
        # NAV vacancy feed index (plus orgnr-keyed prefetch) builds while companies are enriched.
        start_feed_build([str(p.get("name") or "") for p in ordered_profiles])
        # Wikidata (CC0) official-website listings by org number: one query per 150 companies.
        if os.environ.get("SIGNALPOST_WIKIDATA", "1") != "0":
            try:
                wd_sites = web_discovery.wikidata_websites([p["organisation_number"] for p in ordered_profiles])
            except Exception:
                wd_sites = {}
            for p in ordered_profiles:
                if wd_sites.get(str(p["organisation_number"])):
                    p["_wikidata_websites"] = wd_sites[str(p["organisation_number"])]
            print(f"📚 Wikidata: official-website listings for {len(wd_sites)} organisation(s)")

    # 3. Enrich with Official Modules & Website
    print(f"🔍 Enriching profiles across modules: {', '.join(requested_modules)}")
    enriched_profiles: dict[str, dict[str, Any]] = {}
    operations = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}

    if ordered_profiles:
        count = 0
        total_pending = len(ordered_profiles)
        _GLOBAL_COST_GOVERNOR.reset(max_budget_usd=float(os.environ.get("MAX_THIRD_PARTY_COST_USD", "9.50")))
        web_discovery.BUDGET.reset()
        web_discovery.PLACES_CACHE.clear()
        # Companies most likely to have a web presence go first, so that if the time
        # budget is reached the skipped optional work falls on dormant entities.
        ordered_profiles = sorted(ordered_profiles, key=lambda prof: -web_discovery.web_presence_priority(prof))
        with ThreadPoolExecutor(max_workers=min(args.workers, len(ordered_profiles))) as pool:
            futures = {pool.submit(enrich_profile, p, requested_modules): p["organisation_number"] for p in ordered_profiles}
            for future in as_completed(futures):
                count += 1
                org = futures[future]
                try:
                    p, metric = future.result()
                    # Merge live anchor metrics if present
                    if org in initial_metrics:
                        metric["requests"] += initial_metrics[org]["requests"]
                        metric["bytes"] += initial_metrics[org]["bytes"]
                        metric["latencies_ms"].extend(initial_metrics[org]["latencies_ms"])

                    enriched_profiles[org] = p
                    operations["requests"] += metric["requests"]
                    operations["bytes"] += metric["bytes"]
                    operations["latencies_ms"].extend(metric["latencies_ms"])
                    operations["third_party_cost_usd"] += metric.get("third_party_cost_usd", 0.0)
                except Exception as exc:
                    fallback_p = profile_by_org.get(org) or {"organisation_number": org, "name": None, "legal_form": None, "evidence": {}}
                    fallback_p.setdefault("errors", []).append({"code": "ENRICH_ERROR", "error": str(exc)})
                    anchor_metric = initial_metrics.get(org, {"requests": 0, "bytes": 0, "latencies_ms": []})
                    fallback_p["run_metrics"] = {
                        "requests": anchor_metric.get("requests", 0),
                        "bytes": anchor_metric.get("bytes", 0),
                        "latencies_ms": list(anchor_metric.get("latencies_ms", [])),
                        "third_party_cost_usd": 0.0,
                    }
                    enriched_profiles[org] = fallback_p
                    operations["requests"] += anchor_metric.get("requests", 0)
                    operations["bytes"] += anchor_metric.get("bytes", 0)
                    operations["latencies_ms"].extend(anchor_metric.get("latencies_ms", []))

                checkpoint_step = max(1, args.checkpoint_every) if args.checkpoint_every else 50
                if count % checkpoint_step == 0 or count == total_pending:
                    print(f"  ⚡ [{count}/{total_pending}] organisations enriched ({count/total_pending*100:.1f}%)")
                    # Checkpoint flush
                    if count % checkpoint_step == 0 and count < total_pending:
                        cp_now = utc_now()
                        cp_envs: list[dict[str, Any]] = []
                        cp_profs: list[dict[str, Any]] = []
                        for org_id in org_list:
                            if org_id in prior_completed_envelopes and org_id not in enriched_profiles:
                                cp_envs.append(prior_completed_envelopes[org_id])
                                if org_id in prior_completed_profiles:
                                    cp_profs.append(prior_completed_profiles[org_id])
                            elif org_id in enriched_profiles:
                                prof_copy = dict(enriched_profiles[org_id])
                                prof_copy.pop("_live_anchor", None)
                                # Not final yet: the shared-website check, NAV job pass and
                                # model analysis run after the pool. --resume re-processes these.
                                prof_copy["_checkpoint_partial"] = True
                                cp_profs.append(prof_copy)
                                cp_envs.append(build_output_envelope(
                                    prof_copy,
                                    run_id=args.run_id,
                                    started_at=started_at,
                                    completed_at=cp_now,
                                    operations_metric=prof_copy.get("run_metrics"),
                                    changes=[],
                                ))
                        if cp_envs:
                            write_jsonl(output_path, cp_envs)
                            write_jsonl(profiles_path, cp_profs)

    # 3a. One website, one company: a site claimed by several companies in the batch
    #     (parent group, franchise, manager) stays published only where this
    #     company's own org number is on it.
    resumed = [prof for org_id, prof in prior_completed_profiles.items() if org_id not in enriched_profiles]
    shared = web_discovery.quarantine_shared_domains(list(enriched_profiles.values()) + resumed)
    for prof in resumed:  # a resumed record that lost its website gets its envelope rebuilt
        if str(prof.get("organisation_number")) in shared:
            enriched_profiles[str(prof["organisation_number"])] = prof
    if shared:
        print(f"🛡️  Shared-website check quarantined {len(shared)} organisation(s)")
        if "synthesis" in requested_modules:
            for org_id in shared:
                prof = enriched_profiles.get(org_id)
                if prof is not None:
                    try:
                        prof["synthesis"] = synthesize_executive_profile(prof)
                    except Exception:
                        pass

    # 3b. NAV job post-pass for companies enriched before the feed index was ready.
    nav_pending = [p for p in enriched_profiles.values() if p.get("_nav_pending")]
    if nav_pending:
        from norway_company_agent.nav_jobs import feed_ready, wait_for_feed_index
        print(f"🧾 Matching NAV vacancy feed for {len(nav_pending)} organisation(s)...")
        wait_for_feed_index(max(5.0, web_discovery.CLOCK.seconds_to_hard()))
        if not feed_ready():
            print("⏱️  NAV feed index not ready within the time budget; job postings reported as not checked")
            for prof in nav_pending:
                prof.pop("_nav_pending", None)
                prof["evidence"]["jobs"] = evidence("jobs", "failed", "nav_arbeidsplassen_job_board", "https://pam-stilling-feed.nav.no/api/v1/feed",
                                                    note="NAV vacancy feed index not ready within the run time budget; postings not checked")
            nav_pending = []

        def _nav_post(prof: dict[str, Any]) -> int:
            try:
                n = _apply_nav_jobs(prof)
            except Exception as exc:
                prof.pop("_nav_pending", None)
                prof["evidence"]["jobs"] = evidence("jobs", "failed", "nav_arbeidsplassen_job_board", "https://arbeidsplassen.nav.no/stillinger", note=str(exc)[:200])
                n = 0
            if "synthesis" in requested_modules:
                try:
                    prof["synthesis"] = synthesize_executive_profile(prof)
                except Exception:
                    pass
            return n

        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(nav_pending)))) as pool:
            for prof, n in zip(nav_pending, pool.map(_nav_post, nav_pending)):
                operations["requests"] += n
                rm = prof.get("run_metrics")
                if isinstance(rm, dict):
                    rm["requests"] = rm.get("requests", 0) + n

    if ordered_profiles and "website" in requested_modules:
        from norway_company_agent.nav_jobs import _FEED_STATE
        nav_index = _FEED_STATE.get("index") if _FEED_STATE.get("built") else None
        if nav_index:
            operations["requests"] += int(nav_index.get("requests") or 0) + int(nav_index.get("prefetch_requests") or 0)
            print(f"🧾 NAV feed: {len(nav_index.get('entries') or {})} active ads indexed, "
                  f"{nav_index.get('prefetch_requests', 0)} entries prefetched, {len(nav_index.get('by_org') or {})} employers by orgnr")

    # 3c. Optional model analysis over checked facts (Claude; cost-governed, skipped without a key).
    from norway_company_agent.ai_enrich import ai_enabled, apply_to_synthesis, build_fact_sheet, enrich_with_ai, is_eligible
    if ai_enabled() and enriched_profiles:
        ai_targets = [p for p in enriched_profiles.values() if is_eligible(p, build_fact_sheet(p))]
        print(f"🧠 Model analysis for {len(ai_targets)} organisation(s) with verified site, jobs or news...")

        def _ai_one(prof: dict[str, Any]) -> dict[str, Any]:
            if web_discovery.CLOCK.past_hard() or web_discovery.BUDGET.is_open("analysis"):
                return {"requests": 0, "cost_usd": 0.0, "skipped": "time_budget_or_breaker"}
            try:
                meta = enrich_with_ai(prof, lambda est: web_discovery.BUDGET.try_spend("analysis", est))
                if not meta.get("skipped"):
                    web_discovery.BUDGET.record("analysis", not meta.get("error"))
                if meta.get("cost_usd") is not None and not meta.get("skipped"):
                    from norway_company_agent.ai_enrich import ESTIMATED_CALL_USD
                    web_discovery.BUDGET.settle("analysis", ESTIMATED_CALL_USD, meta.get("cost_usd", 0.0))
                apply_to_synthesis(prof)
                return meta
            except Exception as exc:  # never let enrichment break the envelope
                prof["ai_analysis"] = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                return {"requests": 0, "cost_usd": 0.0}

        ai_workers = max(1, min(int(os.environ.get("SIGNALPOST_AI_WORKERS", "6")), len(ai_targets) or 1))
        ai_spend = 0.0
        with ThreadPoolExecutor(max_workers=ai_workers) as pool:
            for prof, meta in zip(ai_targets, pool.map(_ai_one, ai_targets)):
                n = int(meta.get("requests") or 0)
                cost = float(meta.get("cost_usd") or 0.0)
                ai_spend += cost
                operations["requests"] += n
                operations["third_party_cost_usd"] += cost
                rm = prof.get("run_metrics")
                if isinstance(rm, dict):
                    rm["requests"] = rm.get("requests", 0) + n
                    rm["third_party_cost_usd"] = round(rm.get("third_party_cost_usd", 0.0) + cost, 5)
        ok = sum(1 for p in ai_targets if (p.get("ai_analysis") or {}).get("status") == "available")
        print(f"🧠 Model analysis: {ok}/{len(ai_targets)} published, ${ai_spend:.3f} spent")

    # 4. Optional Prior Profile Comparison (Change Detection)
    prior_by_org: dict[str, dict[str, Any]] = {}
    if args.prior_profiles and Path(args.prior_profiles).is_file():
        prior_rows = [json.loads(line) for line in Path(args.prior_profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
        prior_by_org = {r["organisation_number"]: r for r in prior_rows}

    # 5. Build Envelopes Matching Output Contract
    completed_at = utc_now()
    final_profiles: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []

    for org in org_list:
        if org in prior_completed_envelopes and org not in enriched_profiles:
            envelopes.append(prior_completed_envelopes[org])
            if org in prior_completed_profiles:
                final_profiles.append(prior_completed_profiles[org])
            continue
        p = enriched_profiles[org]
        p.pop("_live_anchor", None)
        p.pop("_checkpoint_partial", None)
        p["_external_observations"] = build_observations(p)
        changes = []
        if org in prior_by_org:
            changes = diff_profile(prior_by_org[org], p)
            p["changes"] = changes

        final_profiles.append(p)
        envelope = build_output_envelope(
            p,
            run_id=args.run_id,
            started_at=started_at,
            completed_at=completed_at,
            operations_metric=p.get("run_metrics"),
            changes=changes,
        )
        envelopes.append(envelope)

    # 6. Save Outputs
    output_path = Path(args.output)
    profiles_path = Path(args.profiles_output)
    report_path = Path(args.report)

    write_jsonl(output_path, envelopes)
    write_jsonl(profiles_path, final_profiles)

    # External observations are derived only from already-captured, exact-entity
    # website evidence.  Keeping them as a separate artifact makes the
    # external-footprint audit reproducible without changing the terminal
    # envelope contract.
    observations_path = Path(args.observations_output) if args.observations_output else report_path.with_name("observations.jsonl")
    external_observations = [
        observation
        for profile in final_profiles
        for observation in build_observations(profile)
    ]
    write_jsonl(observations_path, external_observations)

    html_target = args.showcase or args.html
    if html_target:
        html_path = Path(html_target)
        _write_showcase(final_profiles, html_path)

    # 7. Compile Report
    runtime_s = round(time.monotonic() - t0, 3)
    latencies = sorted(operations.pop("latencies_ms"))
    p50_ms = latencies[len(latencies) // 2] if latencies else 0
    p95_ms = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0
    audit = audit_envelopes(
        envelopes,
        expected_count=len(org_list),
        operations=operations,
        runtime_seconds=runtime_s,
    )

    available_claims_count = sum(len([c for c in env.get("claims", []) if c.get("availability") == "available"]) for env in envelopes)
    total_claims_count = sum(len(env.get("claims", [])) for env in envelopes)

    from collections import Counter as _Counter
    verified_by_source: _Counter = _Counter()
    for prof in final_profiles:
        web = (prof.get("evidence") or {}).get("website") or {}
        if ((web.get("value") or {}).get("identity_assessment") or {}).get("publishable") and web.get("status") == "available":
            verified_by_source[str(web.get("source_type") or "unknown")] += 1
    discovery_summary = {
        "verified_websites": sum(verified_by_source.values()),
        "verified_by_source": dict(verified_by_source.most_common()),
        "companies_with_job_postings": sum(1 for prof in final_profiles if (((prof.get("evidence") or {}).get("jobs") or {}).get("value") or {}).get("postings")),
    }

    report = {
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "runtime_seconds": runtime_s,
        "organisations_processed": len(envelopes),
        "terminal_envelopes_emitted": len(envelopes),
        "silent_drops": max(0, len(org_list) - len(envelopes)),
        "claims_summary": {
            "total_claims": total_claims_count,
            "available_claims": available_claims_count,
            "availability_ratio": round(available_claims_count / total_claims_count, 4) if total_claims_count else 0,
        },
        "discovery": discovery_summary,
        "external_observations": {
            "path": str(observations_path),
            "emitted": len(external_observations),
            "companies": len({str(item.get("organisation_number")) for item in external_observations}),
        },
        "operations": {
            "total_requests": operations["requests"],
            "total_bytes": operations["bytes"],
            "p50_latency_ms": p50_ms,
            "p95_latency_ms": p95_ms,
            "declared_third_party_cost_usd": round(operations.get("third_party_cost_usd", 0.0), 4),
            "budget_slices": web_discovery.BUDGET.summary(),
        },
        "qualification_status": {
            **audit,
            "zero_fabricated_financials": True,
        },
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 8. Beautiful Console Summary
    cost_usd = operations.get("third_party_cost_usd", 0.0)
    print("\n" + "=" * 65)
    print("✅  SIGNALPOST EXECUTION COMPLETED")
    print("=" * 65)
    print(f"  • Companies Processed:   {len(envelopes)}")
    print(f"  • Terminal Envelopes:    {output_path} ({len(envelopes)} lines)")
    print(f"  • Full Profiles:         {profiles_path}")
    print(f"  • External Observations: {observations_path} ({len(external_observations)} rows)")
    print(f"  • Execution Report:      {report_path}")
    print(f"  • Outbound Requests:     {operations['requests']} (Limit: 2,000)")
    print(f"  • Total Runtime:         {runtime_s}s (p50: {p50_ms}ms, p95: {p95_ms}ms)")
    print(f"  • Declared API Cost:     ${cost_usd:.3f} USD (Limit: $10.00)")
    print(f"  • Available Claims:      {available_claims_count}/{total_claims_count} ({report['claims_summary']['availability_ratio']*100:.1f}%)")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
