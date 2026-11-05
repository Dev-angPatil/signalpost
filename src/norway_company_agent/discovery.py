from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from typing import Any

import tldextract

from .website import USER_AGENT, SafeRedirectHandler, assert_public_url, normalize_homepage


DIRECTORY_AND_AGGREGATOR_HOSTS = {
    # Directory, aggregator, and credit check portals
    "proff.no", "purehelp.no", "1881.no", "gulesider.no", "firmalisten.no", "companywall.no",
    "firmadatabasen.no", "sokfirma.no", "yra.no", "northdata.com", "nor47business.com",
    "lokalebedrifter.no", "byndle.no", "firmabasen.no", "kredittsjekk.no", "nabonytt.no",
    "falio.no", "generate.no", "kontakte.no", "haandverkerportalen.no", "graveoppdrag.no",
    "graveplassen.no", "virksomhet.brreg.no", "brreg.no", "proffforvalt.no", "regnskapstall.no",
    "ratsit.no", "dn.no", "e24.no", "finansavisen.no", "infobel.com",
}
SOCIAL_PLATFORM_HOSTS = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "tiktok.com",
}
BLOCKED_DISCOVERY_HOSTS = DIRECTORY_AND_AGGREGATOR_HOSTS | SOCIAL_PLATFORM_HOSTS

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
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
GOOGLE_CUSTOM_SEARCH_ENDPOINT = "https://customsearch.googleapis.com/customsearch/v1"


def build_company_search_query(profile: dict[str, Any], *, include_org: bool = True) -> str:
    name = " ".join(str(profile.get("name") or "").split())
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    municipality = " ".join(str(profile.get("municipality") or "").split())
    if not name or (include_org and not org):
        raise ValueError("Company discovery requires a legal name and organisation number")
    if include_org and org:
        if municipality:
            return f'"{name}" {org} {municipality}'.strip()
        return f'"{name}" {org}'.strip()
    if municipality:
        return f'"{name}" {municipality}'.strip()
    return f'"{name}"'.strip()


def build_name_town_search_query(profile: dict[str, Any]) -> str:
    """Build clean company name and town search query as requested by Builderr."""
    name = " ".join(str(profile.get("name") or "").split())
    municipality = " ".join(str(profile.get("municipality") or profile.get("registered_office") or "").split())
    if not name:
        raise ValueError("Company discovery requires a legal name")
    if municipality:
        return f'"{name}" {municipality}'.strip()
    return f'"{name}"'.strip()


GENERIC_EMAIL_DOMAINS = {
    "gmail.com",
    "online.no",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "live.no",
    "styrerommet.no",
    "icloud.com",
    "me.com",
    "broadpark.no",
    "frisurf.no",
    "c2i.net",
    "start.no",
    "bluezone.no",
    "getmail.no",
    "lyse.net",
    "vikenfiber.no",
    "tele2.se",
}


def extract_domain_from_email(email_str: str | None) -> str | None:
    """Extract a company-specific domain from an official registered email address."""
    if not email_str or "@" not in str(email_str):
        return None
    raw_domain = str(email_str).rsplit("@", 1)[-1].strip().lower()
    clean_domain = re.sub(r"[^a-z0-9.-]", "", raw_domain)
    if not clean_domain or "." not in clean_domain or clean_domain in GENERIC_EMAIL_DOMAINS:
        return None
    return clean_domain


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


def fetch_brave_search(
    query: str,
    api_key: str,
    timeout: float = 10.0,
    cost_per_request_usd: float = 0.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not api_key:
        return [], {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}

    url = BRAVE_SEARCH_ENDPOINT + "?" + urllib.parse.urlencode({
        "q": query,
        "count": 10,
        "country": "no",
        "search_lang": "nb",
        "safesearch": "moderate",
        "spellcheck": "0",
    })
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)",
        "X-Subscription-Token": api_key,
    })
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            elapsed = int((time.monotonic() - started) * 1000)
            data = json.loads(raw.decode("utf-8"))
            results = parse_brave_web_results(data, query=query)
            metric = {
                "requests": 1,
                "bytes": len(raw),
                "latencies_ms": [elapsed],
                "third_party_cost_usd": cost_per_request_usd,
            }
            return results, metric
    except Exception:
        return [], {"requests": 1, "bytes": 0, "latencies_ms": [0], "third_party_cost_usd": cost_per_request_usd}


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


def fetch_google_search(
    query: str,
    api_key: str,
    cx: str = "",
    timeout: float = 10.0,
    cost_per_request_usd: float = 0.005,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not api_key:
        return [], {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
    params: dict[str, Any] = {
        "key": api_key,
        "q": query,
        "gl": "no",
        "hl": "no",
        "num": 10,
    }
    if cx:
        params["cx"] = cx
    url = GOOGLE_CUSTOM_SEARCH_ENDPOINT + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            elapsed = int((time.monotonic() - started) * 1000)
            data = json.loads(raw.decode("utf-8"))
            results = parse_google_web_results(data, query=query)
            metric = {
                "requests": 1,
                "bytes": len(raw),
                "latencies_ms": [elapsed],
                "third_party_cost_usd": cost_per_request_usd,
            }
            return results, metric
    except Exception:
        return [], {"requests": 1, "bytes": 0, "latencies_ms": [0], "third_party_cost_usd": cost_per_request_usd}


def parse_google_web_results(payload: dict[str, Any], *, query: str) -> list[dict[str, Any]]:
    items = payload.get("items") or []
    parsed = []
    for rank, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not item.get("link"):
            continue
        parsed.append({
            "url": item.get("link"),
            "title": str(item.get("title") or ""),
            "snippet": str(item.get("snippet") or ""),
            "rank": rank,
            "provider": "google_custom_search_api",
            "query": query,
        })
    return parsed


def resolve_search_provider(env: dict[str, str] | None = None) -> tuple[str | None, dict[str, Any]]:
    """Resolves active web search provider from environment variables.

    Supports 3 interchangeable options (only ONE is needed, or None for $0 statutory mode):
    1. 'serper': SERPER_API_KEY (~$0.001 / req)
    2. 'brave': BRAVE_SEARCH_API_KEY (defaults to ~$0.005 / req)
    3. 'google': GOOGLE_SEARCH_API_KEY or GOOGLE_CUSTOM_SEARCH_API_KEY (+ optional GOOGLE_SEARCH_ENGINE_ID or GOOGLE_CSE_ID, ~$0.005 / req)

    Can be explicitly controlled via WEB_SEARCH_PROVIDER ('serper', 'brave', 'google', or 'auto').
    Returns (provider_name, config_dict) or (None, {}).
    """
    import os

    environ = os.environ if env is None else env

    forced_provider = (environ.get("WEB_SEARCH_PROVIDER") or "").strip().lower()

    # 1. Serper
    serper_key = (environ.get("SERPER_API_KEY") or "").strip()

    # 2. Brave
    brave_key = (environ.get("BRAVE_SEARCH_API_KEY") or "").strip()
    brave_cost_raw = environ.get("BRAVE_SEARCH_COST_PER_REQUEST_USD")
    try:
        brave_cost = float(brave_cost_raw) if (brave_cost_raw is not None and str(brave_cost_raw).strip()) else 0.005
    except ValueError:
        brave_cost = 0.005

    # 3. Google Custom Search
    google_key = (environ.get("GOOGLE_SEARCH_API_KEY") or environ.get("GOOGLE_CUSTOM_SEARCH_API_KEY") or "").strip()
    google_cx = (environ.get("GOOGLE_SEARCH_ENGINE_ID") or environ.get("GOOGLE_CSE_ID") or "").strip()
    google_cost_raw = environ.get("GOOGLE_SEARCH_COST_PER_REQUEST_USD")
    try:
        google_cost = float(google_cost_raw) if (google_cost_raw is not None and str(google_cost_raw).strip()) else 0.005
    except ValueError:
        google_cost = 0.005

    if forced_provider == "serper" and serper_key:
        return "serper", {"api_key": serper_key, "cost_per_request": 0.001}
    elif forced_provider == "brave" and brave_key:
        return "brave", {"api_key": brave_key, "cost_per_request": brave_cost}
    elif forced_provider == "google" and google_key:
        return "google", {"api_key": google_key, "cx": google_cx, "cost_per_request": google_cost}

    # Auto-detection priority if not explicitly forced or forced is 'auto':
    if serper_key:
        return "serper", {"api_key": serper_key, "cost_per_request": 0.001}
    elif brave_key:
        return "brave", {"api_key": brave_key, "cost_per_request": brave_cost}
    elif google_key:
        return "google", {"api_key": google_key, "cx": google_cx, "cost_per_request": google_cost}

    return None, {}


def execute_web_search(
    query: str,
    provider: str,
    config: dict[str, Any],
    timeout: float = 10.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if provider == "serper":
        return fetch_serper_search(query, config["api_key"], timeout=timeout)
    elif provider == "brave":
        return fetch_brave_search(
            query,
            config["api_key"],
            timeout=timeout,
            cost_per_request_usd=config.get("cost_per_request", 0.005),
        )
    elif provider == "google":
        return fetch_google_search(
            query,
            config["api_key"],
            cx=config.get("cx", ""),
            timeout=timeout,
            cost_per_request_usd=config.get("cost_per_request", 0.005),
        )
    return [], {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}


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


def get_search_crawl_candidates(
    profile: dict[str, Any],
    results: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Select ranked search candidates for in-memory crawl and identity verification.

    Decouples pre-fetch filtering from final publication: candidates that match
    distinctive legal name tokens in title, snippet, or URL (and are not blocked
    directory/aggregator hosts) are returned for in-memory inspection.
    """
    if not results or limit <= 0:
        return []

    name_tokens = [token for token in _tokens(profile.get("name")) if token not in GENERIC_NAME_TOKENS]
    if not name_tokens:
        name_tokens = _tokens(profile.get("name"))

    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    municipality_tokens = set(_tokens(profile.get("municipality")))

    candidates = []
    seen_urls: set[str] = set()

    for result in results:
        url = result.get("url") or result.get("link")
        normalized = normalize_homepage(url)
        if not normalized:
            continue

        parsed = urllib.parse.urlparse(normalized)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_DISCOVERY_HOSTS):
            continue

        path = (parsed.path or "").strip()
        path_lower = path.casefold()
        normalized_path = path_lower if path_lower.endswith("/") else path_lower + "/"
        if any(pat in normalized_path for pat in DIRECTORY_PATH_PATTERNS):
            continue

        if normalized in seen_urls:
            continue

        title = str(result.get("title") or "")
        snippet = str(result.get("snippet") or "")
        title_tokens = _tokens(title)
        snippet_tokens = _tokens(snippet)
        host_tokens = _tokens(host)
        evidence_tokens = set(title_tokens + snippet_tokens + host_tokens)
        evidence_digits = re.sub(r"\D", "", f"{title} {snippet}")

        org_match = bool(org and org in evidence_digits)
        all_name_tokens_in_title = bool(name_tokens and set(name_tokens).issubset(set(title_tokens)))
        all_name_tokens = bool(name_tokens and set(name_tokens).issubset(evidence_tokens))
        primary_name_token = name_tokens[0] if name_tokens else ""
        primary_token_match = bool(primary_name_token and primary_name_token in evidence_tokens)
        name_in_host = _match_name_in_sld(name_tokens, host)
        any_name_token_in_host = any(t in host for t in name_tokens) if name_tokens else False
        municipality_match = bool(municipality_tokens and municipality_tokens <= set(snippet_tokens))

        # Relevance check: candidate must match org, all name tokens, primary token, or name in host
        if not (org_match or all_name_tokens or primary_token_match or name_in_host or any_name_token_in_host):
            continue

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
        elif primary_token_match:
            score += 0.15
            reasons.append("primary distinctive legal-name token appears in result evidence")

        if name_in_host:
            score += 0.30
            reasons.append("normalized legal name appears in candidate hostname")
        elif any_name_token_in_host:
            score += 0.15
            reasons.append("distinctive legal-name token appears in candidate hostname")

        if municipality_match:
            score += 0.10
            reasons.append("registry municipality appears in result snippet")

        score = min(score, 1.0)
        rank = result.get("rank") or 10_000
        publishable = bool(score >= 0.75 and name_in_host and (org_match or all_name_tokens_in_title))

        seen_urls.add(normalized)
        candidates.append({
            "url": normalized,
            "host": host,
            "rank": rank,
            "score": round(score, 3),
            "title": title,
            "snippet": snippet,
            "provider": result.get("provider") or "search_api",
            "query": result.get("query") or "",
            "reasons": reasons or ["distinctive legal-name match"],
            "status": "accepted_for_crawl" if publishable else "review",
            "publishable_candidate": publishable,
        })

    # Sort candidates: higher score first, lower search rank second
    candidates.sort(key=lambda item: (-item.get("score", 0.0), item.get("rank") or 10_000, item.get("url") or ""))
    return candidates[:limit]


def generate_candidate_domain_slugs(profile: dict[str, Any]) -> list[str]:
    """Generate candidate .no domain slugs from distinctive legal name tokens."""
    raw_name = str(profile.get("name") or "")
    tokens = [token for token in _tokens(raw_name) if token not in GENERIC_NAME_TOKENS]
    if not tokens:
        tokens = _tokens(raw_name)
    if not tokens:
        return []

    slugs: list[str] = []

    # Pattern 1: Concatenated distinctive tokens (e.g. firmanavn.no)
    concat = "".join(tokens)
    if len(concat) >= 3:
        slugs.append(f"{concat}.no")

    # Pattern 2: Hyphenated distinctive tokens (e.g. firma-navn.no)
    if len(tokens) >= 2:
        hyphen = "-".join(tokens)
        if len(hyphen) >= 3 and f"{hyphen}.no" not in slugs:
            slugs.append(f"{hyphen}.no")

    # Pattern 3: First distinctive token if strong / distinctive (e.g. halvorsen.no)
    if len(tokens) >= 2 and len(tokens[0]) >= 4 and tokens[0] not in {"nordic", "norsk", "total", "service", "eiendom"}:
        single = f"{tokens[0]}.no"
        if single not in slugs:
            slugs.append(single)

    # Pattern 4: Municipality combination (e.g. firmanavn-kommune.no)
    municipality = profile.get("municipality") or profile.get("registered_office") or ""
    muni_tokens = [t for t in _tokens(municipality) if t not in GENERIC_NAME_TOKENS]
    if muni_tokens and len(tokens) <= 2:
        muni = muni_tokens[0]
        geo_hyphen = f"{'-'.join(tokens)}-{muni}.no"
        if geo_hyphen not in slugs:
            slugs.append(geo_hyphen)

    # Pattern 5: 'aa' substitution for Norwegian 'å' / 'Å'
    if "å" in raw_name.casefold():
        name_aa = raw_name.casefold().replace("å", "aa")
        tokens_aa = [t for t in _tokens(name_aa) if t not in GENERIC_NAME_TOKENS]
        if tokens_aa and tokens_aa != tokens:
            aa_concat = f"{''.join(tokens_aa)}.no"
            if aa_concat not in slugs:
                slugs.append(aa_concat)
            if len(tokens_aa) >= 2:
                aa_hyphen = f"{'-'.join(tokens_aa)}.no"
                if aa_hyphen not in slugs:
                    slugs.append(aa_hyphen)

    # Pattern 6: Concatenated distinctive tokens with .com (e.g. firmanavn.com)
    if len(concat) >= 4 and f"{concat}.com" not in slugs and len(slugs) < 4:
        slugs.append(f"{concat}.com")

    return slugs[:4]


def probe_candidate_domains(
    domains: list[str],
    timeout: float = 2.0,
    metrics: dict[str, Any] | None = None,
) -> str | None:
    """Send rapid HTTP HEAD/GET probes to check responsiveness of candidate domains.

    Returns the responding URL if alive and public, or None.
    Bounded by timeout <= 2.0s per probe request.
    """
    if not domains:
        return None

    opener = urllib.request.build_opener(SafeRedirectHandler())

    for domain_entry in domains:
        domain_str = str(domain_entry or "").strip()
        if not domain_str:
            continue

        if domain_str.startswith(("http://", "https://")):
            urls_to_probe = [domain_str]
        else:
            clean = domain_str.lower().removeprefix("www.")
            urls_to_probe = [f"https://www.{clean}", f"https://{clean}"]

        for target_url in urls_to_probe:
            try:
                assert_public_url(target_url)
            except ValueError:
                continue

            started = time.monotonic()
            req = urllib.request.Request(
                target_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
                method="HEAD",
            )
            try:
                with opener.open(req, timeout=timeout) as resp:
                    elapsed = int((time.monotonic() - started) * 1000)
                    if metrics is not None:
                        metrics["requests"] = metrics.get("requests", 0) + 1
                        metrics.setdefault("latencies_ms", []).append(elapsed)
                    if resp.status in {200, 301, 302, 307, 308}:
                        final_url = resp.geturl()
                        try:
                            assert_public_url(final_url)
                            return final_url
                        except ValueError:
                            pass
            except urllib.error.HTTPError as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                if metrics is not None:
                    metrics["requests"] = metrics.get("requests", 0) + 1
                    metrics.setdefault("latencies_ms", []).append(elapsed)
                if exc.code in {403, 405}:
                    try:
                        started_get = time.monotonic()
                        req_get = urllib.request.Request(
                            target_url,
                            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                            method="GET",
                        )
                        with opener.open(req_get, timeout=timeout) as resp_get:
                            elapsed_get = int((time.monotonic() - started_get) * 1000)
                            if metrics is not None:
                                metrics["requests"] = metrics.get("requests", 0) + 1
                                metrics.setdefault("latencies_ms", []).append(elapsed_get)
                            if resp_get.status == 200:
                                final_url = resp_get.geturl()
                                assert_public_url(final_url)
                                return final_url
                    except Exception:
                        pass
            except Exception:
                elapsed = int((time.monotonic() - started) * 1000)
                if metrics is not None:
                    metrics["requests"] = metrics.get("requests", 0) + 1
                    metrics.setdefault("latencies_ms", []).append(elapsed)
                continue

    return None


def extract_social_candidates(profile: dict[str, Any], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract and verify candidate social profiles from search results."""
    from .identity import assess_social_identity

    verified_socials: list[dict[str, Any]] = []
    seen_platforms: set[str] = set()
    for res in results:
        raw_url = str(res.get("url") or "")
        parsed = urllib.parse.urlparse(raw_url)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        platform = None
        if "linkedin.com" in host and "/company/" in raw_url:
            platform = "linkedin"
        elif "facebook.com" in host and not any(p in raw_url for p in ("/login", "/sharer", "/dialog")):
            platform = "facebook"
        elif "instagram.com" in host and not any(p in raw_url for p in ("/accounts", "/p/")):
            platform = "instagram"
        elif "youtube.com" in host and any(p in raw_url for p in ("/channel/", "/c/", "/user/", "/@")):
            platform = "youtube"
        elif "x.com" in host or "twitter.com" in host:
            platform = "twitter"

        if not platform or platform in seen_platforms:
            continue

        assessment = assess_social_identity(
            profile,
            {"platform": platform, "url": raw_url},
            context_text=f"{res.get('title', '')} {res.get('snippet', '')}",
        )
        if assessment.get("publishable"):
            verified_socials.append({
                "platform": platform,
                "url": raw_url,
                "source": "search_discovery",
                "identity_score": assessment.get("identity_score", 0.9),
                "reason": assessment.get("reason", "verified exact entity"),
            })
            seen_platforms.add(platform)

    return verified_socials
