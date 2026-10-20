from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from typing import Any

import tldextract

from .website import normalize_homepage


BLOCKED_DISCOVERY_HOSTS = {
    # Directory, aggregator, and credit check portals
    "proff.no", "purehelp.no", "1881.no", "gulesider.no", "firmalisten.no", "companywall.no",
    "firmadatabasen.no", "sokfirma.no", "yra.no", "northdata.com", "nor47business.com",
    "lokalebedrifter.no", "byndle.no", "firmabasen.no", "kredittsjekk.no", "nabonytt.no",
    "falio.no", "generate.no", "kontakte.no", "haandverkerportalen.no", "graveoppdrag.no",
    "graveplassen.no", "virksomhet.brreg.no", "brreg.no", "proffforvalt.no", "regnskapstall.no",
    "ratsit.no", "dn.no", "e24.no", "finansavisen.no", "infobel.com",
    # Social and media platforms
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "tiktok.com",
}
DIRECTORY_PATH_PATTERNS = (
    "/bedrift/",
    "/selskap/",
    "/firma/",
    "/companies/",
    "/oppslag/",
    "/enheter/",
    "/bedrifter/",
    "/company/",
)
GENERIC_NAME_TOKENS = {"as", "asa", "ans", "da", "enk", "sa", "nuf", "company", "norge", "norway", "gruppen", "group"}


def build_company_search_query(profile: dict[str, Any]) -> str:
    name = " ".join(str(profile.get("name") or "").split())
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    municipality = " ".join(str(profile.get("municipality") or "").split())
    if not name or not org:
        raise ValueError("Company discovery requires a legal name and organisation number")
    return f'"{name}" {org} {municipality}'.strip()


def parse_serper_web_results(payload: dict[str, Any], *, query: str) -> list[dict[str, Any]]:
    organic = payload.get("organic") or []
    if not isinstance(organic, list):
        return []
    parsed = []
    for rank, result in enumerate(organic, start=1):
        if not isinstance(result, dict) or not result.get("link"):
            continue
        raw_position = result.get("position")
        try:
            parsed_position = int(raw_position)
            result_rank = parsed_position if parsed_position > 0 else rank
        except (TypeError, ValueError):
            result_rank = rank
        parsed.append({
            "url": result.get("link"),
            "title": str(result.get("title") or ""),
            "snippet": str(result.get("snippet") or ""),
            "rank": result_rank,
            "provider": "serper_search_api",
            "query": query,
        })
    return parsed


def fetch_serper_search(query: str, api_key: str, timeout: float = 10.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not api_key:
        return [], {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
    url = "https://google.serper.dev/search"
    payload = json.dumps({"q": query, "gl": "no", "hl": "no", "num": 10}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            elapsed = int((time.monotonic() - started) * 1000)
            data = json.loads(raw.decode("utf-8"))
            results = parse_serper_web_results(data, query=query)
            metric = {
                "requests": 1,
                "bytes": len(raw),
                "latencies_ms": [elapsed],
                "third_party_cost_usd": 0.001,
            }
            return results, metric
    except Exception:
        return [], {"requests": 1, "bytes": 0, "latencies_ms": [0], "third_party_cost_usd": 0.0}


def parse_brave_web_results(payload: dict[str, Any], *, query: str) -> list[dict[str, Any]]:
    results = (payload.get("web") or {}).get("results") or []
    parsed = []
    for rank, result in enumerate(results, start=1):
        if not isinstance(result, dict) or not result.get("url"):
            continue
        parsed.append({
            "url": result.get("url"),
            "title": result.get("title") or "",
            "snippet": result.get("description") or "",
            "rank": rank,
            "provider": "brave_search_api",
            "query": query,
        })
    return parsed


def _tokens(value: Any) -> list[str]:
    text = str(value or "").translate(str.maketrans({"ø": "o", "å": "a", "æ": "ae", "Ø": "O", "Å": "A", "Æ": "AE"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return [token for token in re.findall(r"[a-z0-9]+", text) if len(token) > 1]


def _match_name_in_sld(name_tokens: list[str], host: str) -> bool:
    if not name_tokens or not host:
        return False
    ext = tldextract.extract(host)
    sld = (ext.domain or "").casefold()
    if not sld:
        return False
    sld_compact = "".join(_tokens(sld))
    name_compact = "".join(name_tokens)
    if not sld_compact or not name_compact:
        return False

    if sld_compact == name_compact:
        return True

    # A compact prefix/suffix match is useful for domains such as
    # ``halvorsenmaskinservice.no``, but is unsafe for short legal names
    # (``CO AS`` must not match ``company.no``). Exact token matches below
    # remain available for short names.
    if len(name_compact) >= 5 and (sld_compact.startswith(name_compact) or sld_compact.endswith(name_compact)):
        return True

    sld_tokens = _tokens(sld)
    if not sld_tokens:
        return False
    if sld_tokens == name_tokens:
        return True
    if len(sld_tokens) >= len(name_tokens):
        if sld_tokens[:len(name_tokens)] == name_tokens or sld_tokens[-len(name_tokens):] == name_tokens:
            return True

    return False


def score_search_candidate(profile: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    rank = result.get("rank")
    provider = result.get("provider")
    query = result.get("query")

    normalized = normalize_homepage(result.get("url"))
    if not normalized:
        return {
            "status": "rejected",
            "score": 0.0,
            "publishable_candidate": False,
            "url": result.get("url"),
            "host": "",
            "rank": rank,
            "provider": provider,
            "query": query,
            "reasons": ["invalid HTTP(S) candidate URL"],
            "method": "deterministic_search_candidate_identity_v1",
        }
    parsed = urllib.parse.urlparse(normalized)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_DISCOVERY_HOSTS):
        return {
            "status": "rejected",
            "score": 0.0,
            "publishable_candidate": False,
            "url": normalized,
            "host": host,
            "rank": rank,
            "provider": provider,
            "query": query,
            "reasons": ["directory, aggregator, or social host is not a company website candidate"],
            "method": "deterministic_search_candidate_identity_v1",
        }

    path = (parsed.path or "").strip()
    path_lower = path.casefold()
    normalized_path = path_lower if path_lower.endswith("/") else path_lower + "/"
    if any(pat in normalized_path for pat in DIRECTORY_PATH_PATTERNS):
        return {
            "status": "rejected",
            "score": 0.0,
            "publishable_candidate": False,
            "url": normalized,
            "host": host,
            "rank": rank,
            "provider": provider,
            "query": query,
            "reasons": ["directory or aggregator subpath is not a company website candidate"],
            "method": "deterministic_search_candidate_identity_v1",
        }

    name_tokens = [token for token in _tokens(profile.get("name")) if token not in GENERIC_NAME_TOKENS]
    title_tokens = _tokens(result.get("title"))
    snippet_tokens = _tokens(result.get("snippet"))
    evidence_tokens = set(title_tokens + snippet_tokens + _tokens(host))
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    evidence_digits = re.sub(r"\D", "", f"{result.get('title', '')} {result.get('snippet', '')}")
    municipality_tokens = set(_tokens(profile.get("municipality")))

    org_match = bool(org and org in evidence_digits)
    all_name_tokens = bool(name_tokens and set(name_tokens).issubset(evidence_tokens))
    all_name_tokens_in_title = bool(name_tokens and set(name_tokens).issubset(set(title_tokens)))
    name_in_host = _match_name_in_sld(name_tokens, host)
    municipality_match = bool(municipality_tokens and municipality_tokens <= set(snippet_tokens))
    score = 0.0
    reasons = []
    if org_match:
        score += 0.75
        reasons.append("exact organisation number appears in result evidence")
    if all_name_tokens_in_title:
        score += 0.45
        reasons.append("all distinctive legal-name tokens appear in the result title")
    elif all_name_tokens:
        score += 0.25
        reasons.append("all distinctive legal-name tokens appear across result evidence")
    if name_in_host:
        score += 0.3
        reasons.append("normalized legal name appears in candidate hostname")
    if municipality_match:
        score += 0.1
        reasons.append("registry municipality appears in result snippet")
    score = min(score, 1.0)
    # Registry/directory pages routinely reproduce both the legal name and org
    # number. A candidate must therefore also have the distinctive company name
    # in its hostname before it is worth crawling as a company-owned website.
    publishable_candidate = score >= 0.75 and name_in_host and (org_match or all_name_tokens_in_title)
    return {
        "status": "accepted_for_crawl" if publishable_candidate else "review" if score >= 0.6 else "rejected",
        "score": score,
        "publishable_candidate": publishable_candidate,
        "url": normalized,
        "host": host,
        "rank": rank,
        "provider": provider,
        "query": query,
        "reasons": reasons or ["insufficient exact-entity evidence"],
        "method": "deterministic_search_candidate_identity_v1",
    }


def choose_search_candidate(profile: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    assessed = [score_search_candidate(profile, result) for result in results]
    assessed.sort(key=lambda item: (-item.get("score", 0.0), item.get("rank") or 10_000, item.get("url") or ""))
    accepted = [item for item in assessed if item.get("publishable_candidate")]
    return {
        "selected": accepted[0] if accepted else None,
        "candidates": assessed,
        "abstained": not accepted,
        "policy": "A search result is only a crawl candidate. Publication still requires fetched-page exact-entity verification.",
    }
