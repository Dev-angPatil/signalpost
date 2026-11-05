from __future__ import annotations

import re
import unicodedata
import urllib.parse
from typing import Any


LEGAL_AND_GENERIC = {
    "as", "asa", "ans", "da", "enk", "iks", "sa", "sam", "sti", "stiftelsen",
    "nuf", "ab", "b", "v", "limited", "ltd", "inc", "plc", "the", "og", "and",
}


def _tokens(value: Any) -> list[str]:
    text = str(value or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return [token for token in re.findall(r"[a-z0-9]+", text) if token not in LEGAL_AND_GENERIC and len(token) > 1]


def _structured_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"name", "legalName", "alternateName"} and isinstance(child, str):
                names.append(child)
            else:
                names.extend(_structured_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(_structured_names(child))
    return names


def _structured_all_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, dict):
        for v in value.values():
            strings.extend(_structured_all_strings(v))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_structured_all_strings(item))
    elif isinstance(value, str):
        strings.append(value)
    return strings


def build_company_number_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Build a comprehensive identity & number profile for cross-verification.

    Includes primary orgnr, all subunit (underenheter) orgnrs, group entity orgnrs,
    postal codes, registered phones, and distinctive name tokens.
    """
    primary_org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))

    # Subunits (underenheter)
    subunit_orgs: set[str] = set()
    locations_val = (
        (profile.get("evidence", {}).get("locations") or {}).get("value")
        or profile.get("locations")
        or {}
    )
    locations_list = locations_val.get("locations") if isinstance(locations_val, dict) else []
    if isinstance(locations_list, list):
        for loc in locations_list:
            if isinstance(loc, dict):
                raw_org = loc.get("organisation_number") or loc.get("orgnr") or loc.get("org_number") or loc.get("organisasjonsnummer")
                if raw_org:
                    sub_clean = re.sub(r"\D", "", str(raw_org))
                    if len(sub_clean) == 9:
                        subunit_orgs.add(sub_clean)

    # Group structure
    group_orgs: set[str] = set()
    group_val = (
        (profile.get("evidence", {}).get("group") or {}).get("value")
        or profile.get("group")
        or {}
    )
    if isinstance(group_val, dict):
        for k in ("parent", "ultimate_parent", "subsidiaries"):
            val = group_val.get(k)
            if isinstance(val, dict):
                g_raw = val.get("organisation_number") or val.get("orgnr")
                if g_raw:
                    g_clean = re.sub(r"\D", "", str(g_raw))
                    if len(g_clean) == 9:
                        group_orgs.add(g_clean)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        g_raw = item.get("organisation_number") or item.get("orgnr")
                        if g_raw:
                            g_clean = re.sub(r"\D", "", str(g_raw))
                            if len(g_clean) == 9:
                                group_orgs.add(g_clean)

    all_known_orgs = {primary_org} | subunit_orgs | group_orgs
    all_known_orgs.discard("")

    # Postcodes & municipalities
    postal_codes: set[str] = set()
    reg_val = (profile.get("evidence", {}).get("registry") or {}).get("value") or {}
    for addr_key in ("forretningsadresse", "postadresse"):
        addr = reg_val.get(addr_key) if isinstance(reg_val, dict) else None
        if isinstance(addr, dict) and addr.get("postnummer"):
            postal_codes.add(str(addr["postnummer"]).strip())

    municipality = " ".join(str(profile.get("municipality") or profile.get("registered_office") or "").split())
    name_tokens = _tokens(profile.get("name"))

    return {
        "primary_org": primary_org,
        "subunit_orgs": sorted(subunit_orgs),
        "group_orgs": sorted(group_orgs),
        "all_known_orgs": sorted(all_known_orgs),
        "postal_codes": sorted(postal_codes),
        "municipality": municipality,
        "name_tokens": name_tokens,
        "legal_name": profile.get("name") or "",
    }


def verify_candidate_identity(
    candidate_url: str,
    candidate_text: str,
    number_profile: dict[str, Any],
) -> dict[str, Any]:
    """Verify a discovered page or social handle against the company number profile.

    Rule:
    1. If candidate contains 9-digit numbers:
       - If ANY 9-digit number matches all_known_orgs: ACCEPTED (Strong match).
       - If NONE of the 9-digit numbers match all_known_orgs: REJECTED (Conflicting entity number).
    2. If candidate contains NO 9-digit numbers:
       - Fallback to distinctive legal name token match + municipality/town check.
    """
    raw_digits_seqs = re.findall(r"\b\d{9}\b", candidate_text)
    spaced_seqs = [s.replace(" ", "") for s in re.findall(r"\b\d{3}\s+\d{3}\s+\d{3}\b", candidate_text)]
    candidate_orgs = set(raw_digits_seqs + spaced_seqs)

    all_known_orgs = set(number_profile.get("all_known_orgs") or [])
    primary_org = number_profile.get("primary_org")
    subunit_orgs = set(number_profile.get("subunit_orgs") or [])
    name_tokens = number_profile.get("name_tokens") or []
    municipality = number_profile.get("municipality") or ""
    muni_tokens = set(_tokens(municipality))

    text_tokens = set(_tokens(candidate_text))
    url_tokens = set(_tokens(candidate_url))
    all_tokens = text_tokens | url_tokens

    # Case 1: 9-digit numbers present
    if candidate_orgs:
        matched_orgs = candidate_orgs & all_known_orgs
        if matched_orgs:
            if primary_org in matched_orgs:
                matched_type = "primary_organisation_number"
            elif matched_orgs & subunit_orgs:
                matched_type = "registered_subunit_organisation_number"
            else:
                matched_type = "registered_group_organisation_number"
            return {
                "publishable": True,
                "status": "exact",
                "score": 1.0,
                "reason": f"verified via exact {matched_type} ({sorted(matched_orgs)})",
                "matched_orgs": sorted(matched_orgs),
                "conflict": False,
            }
        else:
            return {
                "publishable": False,
                "status": "rejected",
                "score": 0.0,
                "reason": f"conflicting organisation number(s) {sorted(candidate_orgs)} on page - different company with matching name",
                "matched_orgs": [],
                "conflict": True,
            }

    # Case 2: No 9-digit numbers present
    name_match = bool(name_tokens and set(name_tokens).issubset(all_tokens))
    has_muni = bool(muni_tokens and muni_tokens.issubset(all_tokens))

    if len(name_tokens) >= 2 and name_match:
        score = 0.95 if has_muni else 0.90
        return {
            "publishable": True,
            "status": "exact" if has_muni else "review",
            "score": score,
            "reason": "all distinctive legal-name tokens matched" + (" with municipality confirmation" if has_muni else ""),
            "matched_orgs": [],
            "conflict": False,
        }
    elif len(name_tokens) == 1 and name_tokens[0] in all_tokens and has_muni:
        return {
            "publishable": True,
            "status": "exact",
            "score": 0.90,
            "reason": "single distinctive legal-name token corroborated by municipality",
            "matched_orgs": [],
            "conflict": False,
        }

    return {
        "publishable": False,
        "status": "uncertain",
        "score": 0.3,
        "reason": "insufficient distinctive entity evidence on page",
        "matched_orgs": [],
        "conflict": False,
    }


def assess_website_identity(profile: dict[str, Any]) -> dict[str, Any]:
    website = profile.get("evidence", {}).get("website", {})
    value = website.get("value") or {}
    core = _tokens(profile.get("name"))
    hostname = urllib.parse.urlparse(value.get("final_url") or website.get("source_url") or "").hostname or ""
    structured_names = _structured_names(value.get("structured_organisations") or [])
    structured_all_text = _structured_all_strings(value.get("structured_organisations") or [])
    rendered = value.get("js_fallback") or {}
    homepage_identity_parts = [
        value.get("title"), value.get("description"), value.get("identity_text_excerpt"), hostname,
        *structured_names, *structured_all_text,
        rendered.get("title"),
    ]
    candidate_parts = [
        *homepage_identity_parts, value.get("main_text_excerpt"),
        *[page.get("title") for page in value.get("pages", [])],
        *[page.get("main_text_excerpt") for page in value.get("pages", [])],
        *[page.get("identity_text_excerpt") for page in value.get("pages", [])],
    ]
    candidate_parts.append(rendered.get("main_text_excerpt"))
    candidate_text = " ".join(str(part or "") for part in candidate_parts)
    homepage_candidate_text = " ".join(str(part or "") for part in [*homepage_identity_parts, value.get("main_text_excerpt"), rendered.get("main_text_excerpt")])
    normalized_candidate_text = " ".join(_tokens(candidate_text))
    candidate_tokens = set(_tokens(candidate_text))
    org_digits = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    compact_candidate = re.sub(r"\D", "", candidate_text)
    compact_homepage_candidate = re.sub(r"\D", "", homepage_candidate_text)
    overlap = sorted(set(core) & candidate_tokens)
    ratio = len(overlap) / len(set(core)) if core else 0.0
    reasons = []
    parked_markers = (
        "domain is for sale", "domain for sale", "hugedomains", "parked at", "miss hosting",
        "her flytter snart en ny gjest", "has been informing visitors",
        "find the best information and most relevant links on all topics related to",
    )
    normalized_raw = unicodedata.normalize("NFKD", candidate_text).encode("ascii", "ignore").decode().casefold()
    homepage_token_sets = [set(_tokens(part)) for part in homepage_identity_parts if part]
    exact_homepage_name = bool(core and any(set(core).issubset(tokens) for tokens in homepage_token_sets))

    # Multi-evidence substantive check for single-token entities & SPAs
    substantive_body = len(str(value.get("main_text_excerpt") or "").strip()) >= 100
    substantive_desc = (
        len(str(value.get("description") or "").strip()) >= 40
        and bool(core and set(core).issubset(set(_tokens(value.get("description")))))
    )
    structured_org_match = bool(core and any(set(core).issubset(set(_tokens(name))) for name in structured_names))
    substantive_subpage = any(
        len(str(page.get("main_text_excerpt") or "").strip()) >= 60
        and bool(core and (
            set(core).issubset(set(_tokens(page.get("main_text_excerpt"))))
            or set(core).issubset(set(_tokens(page.get("title"))))
        ))
        for page in value.get("pages", [])
        if page.get("url") and page.get("url").rstrip("/") != str(value.get("final_url") or "").rstrip("/")
    )
    is_spa_candidate = value.get("extraction_state") == "js_fallback_candidate"
    title_tokens = set(_tokens(value.get("title")))
    substantive_spa_title = is_spa_candidate and len(str(value.get("title") or "").strip()) >= 10 and bool(core and set(core).issubset(title_tokens))

    substantive_homepage = (
        substantive_body
        or substantive_desc
        or structured_org_match
        or substantive_subpage
        or substantive_spa_title
    )

    number_profile = build_company_number_profile(profile)
    subunit_orgs = set(number_profile.get("subunit_orgs") or [])
    matched_subunit_org = next((so for so in subunit_orgs if so in compact_homepage_candidate), None)

    is_business_sports_club = bool(re.search(r"(?:^|\s)B\.?\s*I\.?\s*L\.?(?:\s|$)", str(profile.get("name") or ""), re.I))
    if any(marker in normalized_raw for marker in parked_markers):
        score = 0.1
        reasons.append("captured page is a parked, for-sale, or generic hosting placeholder")
    elif is_business_sports_club and "bedriftsidrett" not in normalized_candidate_text and "b i l" not in normalized_candidate_text:
        score = 0.3
        reasons.append("business sports-club entity points to the operating company's site without club evidence")
    elif org_digits and org_digits in compact_homepage_candidate:
        score = 1.0
        reasons.append("exact organisation number appears in homepage identity evidence")
    elif matched_subunit_org:
        score = 1.0
        reasons.append(f"exact registered sub-unit organisation number ({matched_subunit_org}) appears in homepage identity evidence")
    elif len(core) >= 2 and exact_homepage_name:
        score = 0.95
        reasons.append("all normalized legal-name tokens appear together in homepage identity evidence")
    elif len(core) == 1 and exact_homepage_name and substantive_homepage:
        score = 0.95
        if substantive_body:
            reasons.append("single distinctive legal-name token appears in homepage identity evidence with substantive content")
        elif substantive_desc or structured_org_match:
            reasons.append("single distinctive legal-name token corroborated by homepage metadata and structured data on SPA site")
        elif substantive_subpage:
            reasons.append("single distinctive legal-name token corroborated by substantive secondary pages on SPA site")
        else:
            reasons.append("single distinctive legal-name token verified via SPA title and script architecture")
    elif ratio >= 0.75 and len(overlap) >= 2:
        score = 0.85
        reasons.append("most legal-name tokens appear, but exact identity is incomplete")
    elif ratio >= 0.5 and len(overlap) >= 2:
        score = 0.65
        reasons.append("partial legal-name overlap only")
    else:
        score = 0.3
        reasons.append("registry-linked URL lacks strong exact-entity identity evidence")
    status = "exact" if score >= 0.9 else "review" if score >= 0.8 else "related_or_uncertain"
    return {
        "status": status,
        "score": score,
        "publishable": status == "exact",
        "legal_name_tokens": core,
        "matched_tokens": overlap,
        "reasons": reasons,
        "method": "deterministic_name_org_evidence_v2",
    }


def assess_social_identity(profile: dict[str, Any], link: dict[str, str], context_text: str = "") -> dict[str, Any]:
    text_to_check = f"{link.get('title', '')} {link.get('snippet', '')} {context_text} {link.get('description', '')}".strip()
    if text_to_check:
        num_prof = build_company_number_profile(profile)
        check = verify_candidate_identity(link.get("url") or "", text_to_check, num_prof)
        if check.get("conflict"):
            return {
                **link,
                "identity_score": 0.0,
                "publishable": False,
                "matched_tokens": [],
                "reason": check.get("reason"),
                "method": "number_profile_conflict_rejection_v1",
            }
        if check.get("publishable") and check.get("score") == 1.0:
            return {
                **link,
                "identity_score": 1.0,
                "publishable": True,
                "matched_tokens": check.get("matched_orgs") or [],
                "reason": check.get("reason"),
                "method": "exact_company_number_match_v1",
            }

    core = _tokens(profile.get("name"))
    parsed = urllib.parse.urlparse(link.get("url") or "")
    raw_path = urllib.parse.unquote(parsed.path).strip("/")
    for prefix in ("company/", "school/", "c/", "channel/", "user/"):
        if raw_path.lower().startswith(prefix):
            raw_path = raw_path[len(prefix):]
            break
    raw_path = raw_path.lstrip("@").strip("/")
    handle_tokens = _tokens(raw_path)
    handle_compact = "".join(handle_tokens)
    core_compact = "".join(core)

    matched = [
        token for token in core
        if token in handle_tokens or (len(token) >= 4 and any(token in ht for ht in handle_tokens))
    ]
    ratio = len(set(matched)) / len(set(core)) if core else 0.0

    if core_compact and (core_compact == handle_compact or (len(core_compact) >= 4 and core_compact in handle_compact)):
        score = 0.98 if core_compact == handle_compact else 0.95
        reason = "normalized legal-name sequence appears in the social handle"
    elif len(core) == 1 and core[0] in handle_tokens:
        score = 0.95
        reason = "single distinctive legal-name token appears in the social handle"
    elif ratio >= 0.75 and len(set(matched)) >= 2:
        score = 0.9
        reason = "most legal-name tokens appear in the social handle"
    else:
        score = 0.3
        reason = "social handle lacks strong exact-entity name evidence"
    return {
        **link,
        "identity_score": score,
        "publishable": score >= 0.9,
        "matched_tokens": matched,
        "reason": reason,
        "method": "deterministic_social_handle_identity_v2",
    }


def apply_website_identity_gate(profile: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    if website.get("status") != "available":
        return {"website": website, "assessment": None, "quarantined_social_links": 0}
    temporary_profile = {**profile, "evidence": {**profile.get("evidence", {}), "website": website}}
    value = website.get("value") or {}
    assessment = assess_website_identity(temporary_profile)
    value["identity_assessment"] = assessment
    original = list(value.get("discovered_social_links") or value.get("social_links") or [])
    value["discovered_social_links"] = original
    social_assessments = [assess_social_identity(profile, link) for link in original]
    value["social_link_assessments"] = social_assessments
    value["social_links"] = [
        {"platform": item["platform"], "url": item["url"]}
        for item in social_assessments
        if assessment["publishable"] and item["publishable"]
    ]
    website["value"] = value
    return {
        "website": website,
        "assessment": assessment,
        "quarantined_social_links": len(original) - len(value["social_links"]),
    }
