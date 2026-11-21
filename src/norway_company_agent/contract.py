from __future__ import annotations

import hashlib
import json
import math
import urllib.parse
import re
from typing import Any, Iterable

from .external_footprint import publishable_observation
from .identity import assess_social_identity
from .publication_gates import apply_publication_gates, filter_site_news, gate_social_links, social_handle


_FINANCIAL_RATIO_FIELDS = (
    "operating_margin_pct",
    "net_margin_pct",
    "equity_ratio_pct",
    "debt_to_assets_pct",
    "multi_year_revenue_growth_pct",
)




_ADDR_LABEL_WORDS = {
    "adresse", "addresse", "besøksadresse", "postadresse", "kontoradresse", "fakturaadresse", "kontakt",
    "oss", "besøk", "org", "telefon", "tlf", "tel", "phone", "følg", "send", "contact", "address", "visit",
    "post", "e-post", "email", "mail", "åpningstider", "åpningstid", "finn", "vei", "veien",
    "norge", "norway", "noreg",
}
_ADDR_RE = re.compile(
    r"(?<![\w])((?:[A-ZÆØÅ][\wæøåéü.'-]*)(?:\s[\wæøåÆØÅéü.'-]+){0,3}?)\s(\d{1,4}\s?[A-Za-z]?)\s*,?\s+(\d{4})\s+([A-Za-zÆØÅæøå][A-Za-zÆØÅæøå-]+(?:\s[A-Za-zÆØÅæøå-]+)?)"
)


_STREET_TYPE = {"gate", "gata", "vei", "veg", "veien", "vegen", "plass", "torg", "allé", "alle", "brygge", "kai", "kaia", "bakke", "bakken", "terrasse"}
_STREET_PREFIX = {"nedre", "øvre", "store", "lille", "gamle", "nordre", "søndre", "vestre", "østre", "st.", "sankt", "kong", "dronning"}
_GENITIVE_COMPOUND = re.compile(r"s(?:gate|gata|vei|veg|veien|vegen|plass|torg|allé|alle|brygge)$")


def _street_words(words: list[str]) -> list[str]:
    """The street name at the end of a captured phrase, without labels or names in front of it.

    "Personvernerklæring Nedre Vollgate" -> "Nedre Vollgate"; "Flyt Design Lilleborgeveien" ->
    "Lilleborgeveien"; "Sven Bruns gate" and "Haakon Sheteligsplass" keep the person's name.
    """
    words = [w for w in words if w]
    if not words or any(ch.isdigit() for ch in words[-1]):
        return []
    street = [words[-1].lstrip("-.").rstrip(".")]
    i = len(words) - 2
    if street[0].casefold() in _STREET_TYPE and i >= 0:
        street.insert(0, words[i]); i -= 1
    def is_name(w: str) -> bool:
        return w[:1].isupper() and w.casefold().strip(".:") not in _ADDR_LABEL_WORDS and not any(c.isdigit() for c in w)
    # A genitive street name ("Sven Bruns gate", "Sheteligsplass") carries the person's first name.
    if i >= 0 and (street[0].endswith("s") and len(street) == 2 or _GENITIVE_COMPOUND.search(street[0].casefold())) and is_name(words[i]) \
            and words[i].casefold() not in _STREET_PREFIX and words[i].casefold() not in {"as", "asa", "sa", "da"}:
        street.insert(0, words[i]); i -= 1
    while i >= 0 and words[i].casefold() in _STREET_PREFIX:
        street.insert(0, words[i]); i -= 1
    if not street[-1][:1].isalpha() or street[0].casefold().strip(".:") in _ADDR_LABEL_WORDS - {"vei", "veien"}:
        return []
    if not street[0][:1].isupper():
        return []  # run-together page text ("ossMaridalsveien") or a lone street type ("vei 29")
    return street


def _registered_post_places(profile: dict[str, Any]) -> dict[str, str]:
    places: dict[str, str] = {}
    ev = profile.get("evidence") or {}
    reg = ((ev.get("registry") or ev.get("registry_live") or {}).get("value")) or {}
    addrs = [reg.get("forretningsadresse"), reg.get("postadresse"), profile.get("registered_address"), profile.get("business_address")]
    for loc in (((ev.get("locations") or {}).get("value") or {}).get("locations") or []):
        if isinstance(loc, dict):
            addrs.append(loc.get("address"))
    for a in addrs:
        if isinstance(a, dict) and a.get("postnummer") and a.get("poststed"):
            places[str(a["postnummer"]).strip()] = str(a["poststed"]).strip().casefold()
    return places


def _street_key(text: str) -> str:
    """'Jens Rings vei. 29' and 'Jens Rings vei 29' compare equal; 'Mølnåsen 77 A' equals 'Mølnåsen 77A'."""
    return re.sub(r"[^0-9a-zæøåäöü]", "", str(text or "").casefold())


def _registered_streets(profile: dict[str, Any]) -> set[tuple[str, str]]:
    """(postcode, street key) for every registered address: main unit, postal address and sub-units."""
    out: set[tuple[str, str]] = set()
    ev = profile.get("evidence") or {}
    reg = ((ev.get("registry") or ev.get("registry_live") or {}).get("value")) or {}
    addrs = [reg.get("forretningsadresse"), reg.get("postadresse"), profile.get("registered_address"), profile.get("business_address")]
    for loc in (((ev.get("locations") or {}).get("value") or {}).get("locations") or []):
        if isinstance(loc, dict):
            addrs.append(loc.get("address"))
    for a in addrs:
        if isinstance(a, dict) and a.get("postnummer"):
            for line in a.get("adresse") or []:
                if line:
                    out.add((str(a["postnummer"]).strip(), _street_key(line)))
    return out


def clean_registered_addresses(raw: Any, profile: dict[str, Any]) -> list[str]:
    """Website addresses normalised to 'Street 1, 0123 CITY', kept only when they are a registered street + postcode."""
    if not raw:
        return []
    places = _registered_post_places(profile)
    if not places:
        return []
    items = [raw] if isinstance(raw, str) else [str(x) for x in raw if x]
    streets = _registered_streets(profile)
    out: list[str] = []
    for text in items:
        flat = " ".join(text.split())
        for m in _ADDR_RE.finditer(flat):
            words = _street_words(m.group(1).split())
            if not words:
                continue
            postcode, city_raw = m.group(3), m.group(4)
            city = places.get(postcode)
            if not city:
                continue
            city_words = city_raw.split()
            # The city must be the registered poststed; any trailing page word ("Bodø Vil") is cut off.
            if " ".join(city_words).casefold() == city:
                pass
            elif city_words[0].casefold() == city:
                city_raw = city_words[0]
            else:
                continue
            value = f"{' '.join(words)} {m.group(2).replace(' ', '')}, {postcode} {city_raw}"
            # Only a registered address of this company (main unit, postal or a sub-unit):
            # other buildings in the same town ("Omsorgsbygget Storgata 10", a venue) are not its address.
            if (postcode, _street_key(f"{' '.join(words)} {m.group(2)}")) not in streets:
                continue
            if value.casefold() not in {v.casefold() for v in out}:
                out.append(value)
    return out

def _generic_job_title(title: Any) -> bool:
    from .website import GENERIC_JOB_HEADING
    return not title or bool(GENERIC_JOB_HEADING.search(" ".join(str(title).split()).casefold().strip(" .:!")))


def own_domain_emails(raw: Any, site_url: str | None, registry_email: str | None) -> list[str]:
    """Emails whose domain is the verified site's registered domain or the registry email domain."""
    if not raw:
        return []
    items = [raw] if isinstance(raw, str) else [str(x) for x in raw if x]
    allowed: set[str] = set()
    if site_url:
        from .website import _registered_domain
        dom = _registered_domain(site_url)
        if dom:
            allowed.add(dom.casefold())
    if registry_email and "@" in str(registry_email):
        allowed.add(str(registry_email).rsplit("@", 1)[1].strip().casefold().removeprefix("www."))
    out: list[str] = []
    for email in items:
        email = email.strip()
        if "@" not in email:
            continue
        host = email.rsplit("@", 1)[1].casefold().rstrip(".")
        if any(host == d or host.endswith("." + d) for d in allowed) and email not in out:
            out.append(email)
    return out

def _stable_content_hash(value: Any) -> str:
    """Return a deterministic hash for an evidence payload lacking a source hash.

    Connectors normally provide a hash of the retrieved bytes.  Test fixtures and
    older connectors may only provide a structured value; hashing that canonical
    value keeps the terminal evidence record inspectable without inventing a
    source URL or silently emitting an empty hash.
    """
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evidence_hash(record: dict[str, Any], value: Any = None) -> str:
    raw = record.get("content_sha256") or record.get("raw_html_sha256")
    if isinstance(value, dict):
        raw = raw or value.get("content_sha256") or value.get("raw_html_sha256")
    if isinstance(raw, str) and len(raw) == 64:
        return raw
    return _stable_content_hash(value if value is not None else record)


def _event_hash(event: dict[str, Any], fallback_record: dict[str, Any]) -> str:
    raw = event.get("content_sha256") or event.get("sha256")
    if isinstance(raw, str) and len(raw) == 64:
        return raw
    fallback_hash = fallback_record.get("content_sha256") or fallback_record.get("raw_html_sha256")
    if isinstance(fallback_hash, str) and len(fallback_hash) == 64:
        return fallback_hash
    return _stable_content_hash({
        "date": event.get("date"),
        "title": event.get("title") or event.get("type"),
        "description": event.get("description"),
        "source_url": event.get("source_url") or fallback_record.get("source_url"),
    })


def _first_present(*values: Any) -> Any:
    """Return the first value that is actually present, preserving falsey values."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


# Further line items of the latest filing.  A line the filing does not state is
# simply not published (never coerced to zero).
_EXTRA_ACCOUNT_FIELDS = (
    "profit_before_tax",
    "tax_expense",
    "sales_revenue",
    "operating_costs",
    "wage_costs",
    "net_financial_items",
    "current_assets",
    "fixed_assets",
    "cash_and_bank",
    "receivables",
    "short_term_debt",
    "long_term_debt",
)
_YEAR_SUMMARY_FIELDS = ("revenue", "operating_result", "profit_before_tax", "annual_result", "assets", "equity", "debt", "wage_costs")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _extra_account_claims(record: dict[str, Any], evidence_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in _EXTRA_ACCOUNT_FIELDS:
        if _is_number(record.get(key)):
            out.append({"field": key, "value": record[key], "availability": "available", "confidence": 0.99, "evidence_ids": [evidence_id]})
    not_audited = record.get("not_audited")
    if isinstance(not_audited, bool):
        out.append({"field": "accounts_audited", "value": not not_audited, "availability": "available", "confidence": 0.99, "evidence_ids": [evidence_id]})
    if isinstance(record.get("audit_opted_out"), bool):
        out.append({"field": "audit_opted_out", "value": record["audit_opted_out"], "availability": "available", "confidence": 0.99, "evidence_ids": [evidence_id]})
    return out


def _account_year_summary(record: dict[str, Any]) -> dict[str, Any] | None:
    period = record.get("period") if isinstance(record.get("period"), dict) else {}
    period_to = str(period.get("tilDato") or "")[:10]
    if len(period_to) < 4 or not period_to[:4].isdigit():
        return None
    figures = {k: record[k] for k in _YEAR_SUMMARY_FIELDS if _is_number(record.get(k))}
    if not figures:
        return None
    return {
        "year": period_to[:4],
        "period_from": str(period.get("fraDato") or "")[:10],
        "period_to": period_to,
        "account_type": record.get("account_type") or "SELSKAP",
        "currency": record.get("currency") or "NOK",
        **figures,
    }


def _registry_extra_claims(registry_value: dict[str, Any]) -> list[tuple[str, Any]]:
    """Further facts stated in the Enhetsregisteret record we already hold."""
    rv = registry_value if isinstance(registry_value, dict) else {}
    out: list[tuple[str, Any]] = []
    if rv.get("slettedato"):
        out.append(("deleted_from_register_date", str(rv["slettedato"])[:10]))
    if rv.get("stiftelsesdato"):
        out.append(("founding_date", str(rv["stiftelsesdato"])[:10]))
    if rv.get("vedtektsdato"):
        out.append(("articles_of_association_date", str(rv["vedtektsdato"])[:10]))
    capital = rv.get("kapital") if isinstance(rv.get("kapital"), dict) else {}
    if _is_number(capital.get("belop")):
        share_capital = {"amount": capital["belop"], "currency": capital.get("valuta") or "NOK"}
        for src, dst in (("type", "type"), ("antallAksjer", "shares"), ("innfortDato", "registered_on")):
            if capital.get(src) is not None:
                share_capital[dst] = capital[src]
        out.append(("share_capital", share_capital))
    former = [
        {"name": h.get("navn"), "from": str(h.get("fraDato") or "")[:10] or None, "to": str(h.get("tilDato") or "")[:10] or None}
        for h in rv.get("historiskeNavn") or []
        if isinstance(h, dict) and h.get("navn")
    ]
    if former:
        out.append(("former_names", former))
    for src, field in (
        ("registrertIMvaregisteret", "vat_registered"),
        ("registrertIForetaksregisteret", "in_business_register"),
        ("erIKonsern", "part_of_corporate_group"),
    ):
        if isinstance(rv.get(src), bool):
            out.append((field, rv[src]))
    secondary = [
        {"code": n.get("kode"), "description": n.get("beskrivelse")}
        for n in (rv.get("naeringskode2"), rv.get("naeringskode3"))
        if isinstance(n, dict) and n.get("kode")
    ]
    if secondary:
        out.append(("secondary_industry_codes", secondary))
    sector = rv.get("institusjonellSektorkode") if isinstance(rv.get("institusjonellSektorkode"), dict) else {}
    if sector.get("beskrivelse"):
        out.append(("institutional_sector", sector["beskrivelse"]))
    if rv.get("overordnetEnhet"):
        out.append(("parent_unit_organisation_number", str(rv["overordnetEnhet"])))
    return out


def _record_missing_state(record: dict[str, Any] | None) -> tuple[str, float]:
    """Translate an evidence status into the contract's honest missing state."""
    if not record:
        return "not_available", 1.0
    status = str(record.get("status") or "")
    if status == "not_applicable":
        return "not_applicable", 1.0
    if status in {"blocked", "blocked_robots", "blocked_policy"}:
        return "blocked", 0.0
    if status in {"failed", "source_error", "error"}:
        return "failed", 0.0
    return "not_available", 1.0


def _append_source_claim(
    claims: list[dict[str, Any]],
    field: str,
    value: Any,
    *,
    record: dict[str, Any] | None,
    evidence_id: str,
    confidence: float = 0.99,
) -> None:
    """Append a claim whose value is supported by one source record.

    Empty values remain ``None``.  A checked source is retained as an evidence
    reference for a missing field; an absent or failed source is never made to
    look like a successful observation.
    """
    if record and record.get("status") == "available":
        if value is not None:
            claims.append({
                "field": field,
                "value": value,
                "availability": "available",
                "confidence": confidence,
                "evidence_ids": [evidence_id],
            })
            return
        missing_state, missing_confidence = _record_missing_state(record)
        claims.append({
            "field": field,
            "value": None,
            "availability": missing_state,
            "confidence": missing_confidence,
            "evidence_ids": [evidence_id],
        })
        return

    missing_state, missing_confidence = _record_missing_state(record)
    claims.append({
        "field": field,
        "value": None,
        "availability": missing_state,
        "confidence": missing_confidence,
        "evidence_ids": [],
    })


def _append_numeric_source_claim(
    claims: list[dict[str, Any]],
    field: str,
    value: Any,
    *,
    record: dict[str, Any] | None,
    evidence_id: str,
) -> None:
    """Append a finite numeric synthesis metric only when its source is live."""
    numeric_value = value
    if isinstance(numeric_value, bool) or not isinstance(numeric_value, (int, float)):
        numeric_value = None
    elif not math.isfinite(float(numeric_value)):
        numeric_value = None
    _append_source_claim(
        claims,
        field,
        numeric_value,
        record=record,
        evidence_id=evidence_id,
    )


def _canonical_synthesis_evidence_id(value: Any, available_ids: set[str]) -> str | None:
    """Map synthesis' historical source aliases to the terminal IDs."""
    if value is None:
        return None
    source_id = str(value)
    aliases = {
        "ev-regnskap": "ev-financials",
        "ev-subunits": "ev-locations",
        "registry": "ev-registry",
        "official_registry": "ev-registry",
        "official_registry_live": "ev-registry",
        "financials": "ev-financials",
        "financial": "ev-financials",
        "official_regnskap": "ev-financials",
        "official_annual_accounts": "ev-financials",
        "roles": "ev-roles",
        "official_roles": "ev-roles",
        "locations": "ev-locations",
        "subunits": "ev-locations",
        "official_subunits": "ev-locations",
        "website": "ev-website",
        "company_owned": "ev-website",
        "announcements": "ev-announcements",
        "official_public_notices": "ev-announcements",
        "news": "ev-news",
    }
    source_id = aliases.get(source_id, source_id)
    return source_id if source_id in available_ids else None


def _synthesis_entry_evidence_ids(entry: Any, available_ids: set[str]) -> list[str]:
    """Resolve explicit or well-defined synthesis item provenance."""
    if not isinstance(entry, dict):
        return []
    raw_ids: list[Any] = []
    if isinstance(entry.get("evidence_ids"), list):
        raw_ids.extend(entry["evidence_ids"])
    if entry.get("evidence_id") is not None:
        raw_ids.append(entry.get("evidence_id"))
    if entry.get("source") is not None:
        raw_ids.append(entry.get("source"))

    resolved = []
    for raw_id in raw_ids:
        canonical = _canonical_synthesis_evidence_id(raw_id, available_ids)
        if canonical and canonical not in resolved:
            resolved.append(canonical)
    if resolved:
        return resolved

    # ``synthesis.py`` uses stable item names for facts whose source is
    # deterministic.  Infer only those mappings; arbitrary text is never
    # treated as provenance.
    item = str(entry.get("item") or entry.get("field") or "").replace(" ", "_").lower()
    inferred_source = (
        "ev-registry" if item in {"legal_name", "legal_form", "municipality", "registered_municipality", "employees"}
        else "ev-roles" if item in {"ceo", "chair", "board_chair"}
        else "ev-financials" if item in {"statutory_accounts", "annual_accounts", "financials", "revenue", "operating_result"}
        else "ev-locations" if item in {"registered_workplaces_count", "operating_subunits", "locations"}
        else "ev-website" if item in {"website", "website_identity_quarantine"}
        else None
    )
    return [inferred_source] if inferred_source in available_ids else []


def _explicit_synthesis_evidence_ids(synthesis: dict[str, Any], available_ids: set[str]) -> list[str]:
    """Read optional synthesis-level provenance without trusting free-form text."""
    raw_ids: list[Any] = []
    for key in (
        "evidence_ids",
        "evidence_id",
        "source_ids",
        "business_scope_evidence_ids",
        "executive_summary_evidence_ids",
        "summary_evidence_ids",
    ):
        value = synthesis.get(key)
        if isinstance(value, list):
            raw_ids.extend(value)
        elif value is not None:
            raw_ids.append(value)
    resolved = []
    for raw_id in raw_ids:
        canonical = _canonical_synthesis_evidence_id(raw_id, available_ids)
        if canonical and canonical not in resolved:
            resolved.append(canonical)
    return resolved


def _synthesis_field_evidence_ids(
    synthesis: dict[str, Any],
    keys: tuple[str, ...],
    available_ids: set[str],
) -> list[str]:
    """Resolve provenance for one synthesis field, if it is explicitly supplied."""
    resolved = []
    for key in keys:
        value = synthesis.get(key)
        values = value if isinstance(value, list) else [value]
        for raw_id in values:
            canonical = _canonical_synthesis_evidence_id(raw_id, available_ids)
            if canonical and canonical not in resolved:
                resolved.append(canonical)
    return resolved


def build_output_envelope(
    profile: dict[str, Any],
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    operations_metric: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Constructs a terminal envelope strictly complying with OUTPUT_CONTRACT.md while

    preserving the extended modules and profile for the evaluation harness.
    """
    apply_publication_gates(profile)
    org = str(profile.get("organisation_number") or "")
    evidence_dict = profile.get("evidence", {})
    ev_items: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []

    # 1. Official Registry Evidence & Claims
    reg_record = evidence_dict.get("registry") or evidence_dict.get("registry_live")
    reg_ev_id = "ev-registry"
    reg_val: dict[str, Any] = {}
    if reg_record and reg_record.get("status") == "available":
        reg_val = reg_record.get("value") or {}
        raw_name = profile.get("name") or reg_val.get("navn") or reg_val.get("name")
        ev_items.append({
            "id": reg_ev_id,
            "source_url": reg_record.get("source_url") or f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}",
            "source_class": "official_registry",
            "retrieved_at": reg_record.get("retrieved_at") or completed_at,
            "content_sha256": _evidence_hash(reg_record, reg_val),
            "claim_span": f"{raw_name} ({org})",
        })

        if raw_name:
            claims.append({
                "field": "legal_name",
                "value": raw_name,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [reg_ev_id],
            })
        else:
            claims.append({"field": "legal_name", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [reg_ev_id]})

        legal_form = profile.get("legal_form") or (reg_val.get("organisasjonsform") or {}).get("kode")
        if legal_form:
            claims.append({
                "field": "legal_form",
                "value": legal_form,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [reg_ev_id],
            })
        else:
            claims.append({"field": "legal_form", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [reg_ev_id]})

        municipality = profile.get("municipality") or profile.get("registered_office")
        if municipality:
            claims.append({
                "field": "registered_municipality",
                "value": municipality,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [reg_ev_id],
            })

        industry_code = profile.get("industry_code") or profile.get("nace_code")
        if industry_code:
            claims.append({
                "field": "industry_code",
                "value": industry_code,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [reg_ev_id],
            })

        employees = profile.get("employees")
        if employees is not None:
            claims.append({
                "field": "employees",
                "value": employees,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [reg_ev_id],
            })
        else:
            claims.append({
                "field": "employees",
                "value": None,
                "availability": "not_available",
                "confidence": 0.99,
                "evidence_ids": [reg_ev_id],
            })
    else:
        # Registry record absent or failed
        state = "not_available" if (reg_record or {}).get("status") in {"not_found", "404"} else "failed"
        claims.append({"field": "legal_name", "value": None, "availability": state, "confidence": 0.0, "evidence_ids": []})

    # Additional official identity/account metadata.  These values are read
    # from the anchored registry snapshot (or the normalized profile fields
    # populated from it); no fallback text is synthesized here.
    registry_value = (
        reg_record.get("value")
        if isinstance(reg_record, dict) and isinstance(reg_record.get("value"), dict)
        else {}
    )
    registry_nace = registry_value.get("naeringskode1") or registry_value.get("nace") or {}
    if not isinstance(registry_nace, dict):
        registry_nace = {}

    nace_description = _first_present(
        registry_nace.get("beskrivelse"),
        registry_nace.get("description"),
        profile.get("nace_description"),
        profile.get("nace_desc"),
        profile.get("industry_label"),
        profile.get("industry"),
    )
    latest_submitted_accounts = _first_present(
        registry_value.get("sisteInnsendteAarsregnskap"),
        profile.get("latest_submitted_accounts"),
        profile.get("latest_account_year"),
    )
    registered_address = _first_present(
        registry_value.get("forretningsadresse"),
        profile.get("registered_address"),
        profile.get("business_address"),
    )
    postal_address = _first_present(
        registry_value.get("postadresse"),
        profile.get("postal_address"),
    )
    registration_status = _first_present(
        registry_value.get("registration_status"),
        registry_value.get("status"),
        profile.get("registration_status"),
        profile.get("registry_status"),
        profile.get("entity_status"),
        profile.get("company_status"),
        profile.get("status"),
    )
    if registration_status is None and "registrertIEnhetsregisteret" in registry_value:
        registration_status = registry_value.get("registrertIEnhetsregisteret")
    registration_date = _first_present(
        registry_value.get("registreringsdatoEnhetsregisteret"),
        profile.get("registration_date"),
        profile.get("registered_at"),
    )
    bankruptcy_status = (
        registry_value.get("konkurs")
        if "konkurs" in registry_value
        else profile.get("bankrupt") if "bankrupt" in profile else None
    )
    liquidation_status = (
        registry_value.get("underAvvikling")
        if "underAvvikling" in registry_value
        else profile.get("liquidating") if "liquidating" in profile else None
    )

    for field, value in (
        ("nace_description", nace_description),
        ("latest_submitted_accounts", latest_submitted_accounts),
        ("registered_address", registered_address),
        ("postal_address", postal_address),
        ("registration_status", registration_status),
        ("registration_date", registration_date),
        ("bankrupt", bankruptcy_status),
        ("liquidating", liquidation_status),
    ):
        _append_source_claim(
            claims,
            field,
            value,
            record=reg_record,
            evidence_id=reg_ev_id,
        )
    if reg_record and reg_record.get("status") == "available":
        for field, value in _registry_extra_claims(registry_value):
            _append_source_claim(claims, field, value, record=reg_record, evidence_id=reg_ev_id)

    # 2. Official Financials Evidence & Claims
    fin_record = evidence_dict.get("financials")
    fin_ev_id = "ev-financials"
    if fin_record and fin_record.get("status") == "available":
        fin_val = fin_record.get("value") or {}
        records = fin_val.get("records") or []
        latest_period = (records[0].get("period") if records and isinstance(records[0], dict) else None) or {}
        period_from = str(latest_period.get("fraDato") or "")[:10] if isinstance(latest_period, dict) else ""
        period_to = str(latest_period.get("tilDato") or "")[:10] if isinstance(latest_period, dict) else ""
        account_type = (records[0].get("account_type") if records and isinstance(records[0], dict) else None) or "SELSKAP"
        ev_items.append({
            "id": fin_ev_id,
            "source_url": fin_record.get("source_url") or f"https://data.brreg.no/regnskapsregisteret/regnskap/{org}",
            "source_class": "official_annual_accounts",
            "retrieved_at": fin_record.get("retrieved_at") or completed_at,
            "content_sha256": _evidence_hash(fin_record, fin_val),
            # Reporting period of the figures published from this filing.
            **({"reporting_period": {"from": period_from, "to": period_to}, "effective_at": period_to} if period_to else {}),
            "claim_span": (f"Annual accounts ({account_type}) for {org}, reporting period {period_from} to {period_to}"
                           if period_to else f"Annual account filing for {org}"),
        })
        if records:
            latest_rec = records[0]
            period = latest_rec.get("period") or {}
            year_claim = period.get("tilDato", "")[:4] if isinstance(period, dict) else None
            if year_claim:
                claims.append({
                    "field": "latest_financial_year",
                    "value": year_claim,
                    "availability": "available",
                    "confidence": 0.99,
                    "evidence_ids": [fin_ev_id],
                })
            for f_key in ("revenue", "operating_result", "annual_result", "assets", "debt", "equity"):
                val = latest_rec.get(f_key)
                if val is not None:
                    claims.append({
                        "field": f_key,
                        "value": val,
                        "availability": "available",
                        "confidence": 0.99,
                        "evidence_ids": [fin_ev_id],
                    })
                else:
                    claims.append({
                        "field": f_key,
                        "value": None,
                        "availability": "not_available",
                        "confidence": 0.99,
                        "evidence_ids": [fin_ev_id],
                    })
            if latest_rec.get("currency"):
                claims.append({
                    "field": "currency",
                    "value": latest_rec.get("currency"),
                    "availability": "available",
                    "confidence": 0.99,
                    "evidence_ids": [fin_ev_id],
                })
            claims.extend(_extra_account_claims(latest_rec, fin_ev_id))
            if period_to:
                claims.append({
                    "field": "accounts_reporting_period",
                    "value": {"from": period_from, "to": period_to, "account_type": account_type},
                    "availability": "available",
                    "confidence": 0.99,
                    "evidence_ids": [fin_ev_id],
                })
            # Earlier filings in the same register response: one dated claim per
            # year, each tied to its own evidence record and reporting period.
            for prior in records[1:]:
                if not isinstance(prior, dict):
                    continue
                summary = _account_year_summary(prior)
                if not summary:
                    continue
                year = summary["year"]
                prior_ev_id = f"ev-financials-{year}"
                ev_items.append({
                    "id": prior_ev_id,
                    "source_url": fin_record.get("source_url") or f"https://data.brreg.no/regnskapsregisteret/regnskap/{org}",
                    "source_class": "official_annual_accounts",
                    "retrieved_at": fin_record.get("retrieved_at") or completed_at,
                    "content_sha256": _evidence_hash(fin_record, fin_val),
                    "reporting_period": {"from": summary["period_from"], "to": summary["period_to"]},
                    "effective_at": summary["period_to"],
                    "claim_span": (f"Annual accounts ({prior.get('account_type') or 'SELSKAP'}) for {org}, "
                                   f"reporting period {summary['period_from']} to {summary['period_to']}"),
                })
                claims.append({
                    "field": f"annual_accounts_{year}",
                    "value": summary,
                    "availability": "available",
                    "confidence": 0.99,
                    "evidence_ids": [prior_ev_id],
                })
        else:
            claims.append({
                "field": "revenue",
                "value": None,
                "availability": "not_available",
                "confidence": 0.99,
                "evidence_ids": [fin_ev_id],
            })
    elif fin_record and fin_record.get("status") == "not_applicable":
        claims.append({"field": "revenue", "value": None, "availability": "not_applicable", "confidence": 1.0, "evidence_ids": []})
    else:
        fin_state, fin_confidence = _record_missing_state(fin_record)
        claims.append({"field": "revenue", "value": None, "availability": fin_state, "confidence": fin_confidence, "evidence_ids": []})

    # Financial ratios are published only from the deterministic synthesis
    # produced over official annual-account evidence.  A ratio in an
    # ungrounded/free-form synthesis is deliberately abstained from.
    synthesis_value = profile.get("synthesis") if isinstance(profile.get("synthesis"), dict) else {}
    financial_health = synthesis_value.get("financial_health") if isinstance(synthesis_value.get("financial_health"), dict) else {}
    financial_value = fin_record.get("value") if isinstance(fin_record, dict) else {}
    official_financial_records = (
        financial_value.get("records")
        if isinstance(financial_value, dict) and isinstance(financial_value.get("records"), list)
        else []
    )
    has_official_financial_records = bool(
        fin_record
        and fin_record.get("status") == "available"
        and official_financial_records
    )
    for ratio_field in _FINANCIAL_RATIO_FIELDS:
        _append_numeric_source_claim(
            claims,
            ratio_field,
            financial_health.get(ratio_field) if has_official_financial_records else None,
            record=fin_record,
            evidence_id=fin_ev_id,
        )

    # 3. Roles Evidence & Claims
    roles_record = evidence_dict.get("roles")
    roles_ev_id = "ev-roles"
    if roles_record and roles_record.get("status") == "available":
        roles_val = roles_record.get("value") or {}
        role_items = roles_val.get("roles") or []
        ev_items.append({
            "id": roles_ev_id,
            "source_url": roles_record.get("source_url") or f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}/roller",
            "source_class": "official_roles",
            "retrieved_at": roles_record.get("retrieved_at") or completed_at,
            "content_sha256": _evidence_hash(roles_record, roles_val),
            "claim_span": f"{len(role_items)} role records",
        })
        ceo_name = next((r.get("name") for r in role_items if "daglig leder" in str(r.get("role", "")).lower() and not r.get("inactive")), None)
        chair_name = next((r.get("name") for r in role_items if any(t in str(r.get("role", "")).lower() for t in ("styrets leder", "styreleder")) and not r.get("inactive")), None)
        if ceo_name:
            claims.append({"field": "ceo", "value": ceo_name, "availability": "available", "confidence": 0.99, "evidence_ids": [roles_ev_id]})
        else:
            claims.append({"field": "ceo", "value": None, "availability": "not_available", "confidence": 0.99, "evidence_ids": [roles_ev_id]})

        if chair_name:
            claims.append({"field": "board_chair", "value": chair_name, "availability": "available", "confidence": 0.99, "evidence_ids": [roles_ev_id]})
        else:
            claims.append({"field": "board_chair", "value": None, "availability": "not_available", "confidence": 0.99, "evidence_ids": [roles_ev_id]})
    else:
        roles_state, roles_confidence = _record_missing_state(roles_record)
        claims.append({"field": "ceo", "value": None, "availability": roles_state, "confidence": roles_confidence, "evidence_ids": []})
        claims.append({"field": "board_chair", "value": None, "availability": roles_state, "confidence": roles_confidence, "evidence_ids": []})

    _append_source_claim(
        claims,
        "leadership",
        role_items if roles_record and roles_record.get("status") == "available" else None,
        record=roles_record,
        evidence_id=roles_ev_id,
    )

    # 4. Locations Evidence & Claims
    loc_record = evidence_dict.get("locations")
    loc_ev_id = "ev-locations"
    if loc_record and loc_record.get("status") == "available":
        loc_val = loc_record.get("value") or {}
        loc_items = loc_val.get("locations") or []
        ev_items.append({
            "id": loc_ev_id,
            "source_url": loc_record.get("source_url") or f"https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=1000",
            "source_class": "official_subunits",
            "retrieved_at": loc_record.get("retrieved_at") or completed_at,
            "content_sha256": _evidence_hash(loc_record, loc_val),
            "claim_span": f"{len(loc_items)} subunits registered",
        })
        claims.append({
            "field": "registered_workplaces_count",
            "value": len(loc_items),
            "availability": "available",
            "confidence": 0.99,
            "evidence_ids": [loc_ev_id],
        })
    else:
        loc_state, loc_confidence = _record_missing_state(loc_record)
        claims.append({"field": "registered_workplaces_count", "value": None, "availability": loc_state, "confidence": loc_confidence, "evidence_ids": []})

    _append_source_claim(
        claims,
        "locations",
        loc_items if loc_record and loc_record.get("status") == "available" else None,
        record=loc_record,
        evidence_id=loc_ev_id,
    )

    # 4b. Announcements Evidence & Claims
    ann_record = evidence_dict.get("announcements")
    if not ann_record and isinstance(profile.get("announcements"), dict):
        ann_record = profile.get("announcements")

    ann_ev_id = "ev-announcements"
    if ann_record and ann_record.get("status") == "available":
        ann_val = ann_record.get("value") if isinstance(ann_record.get("value"), dict) else ann_record
        ann_events = ann_val.get("events") if isinstance(ann_val.get("events"), list) else []
        ann_cats = ann_val.get("categories") if isinstance(ann_val.get("categories"), dict) else {}
        source_url = ann_record.get("source_url") or f"https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr={org}"

        ev_items.append({
            "id": ann_ev_id,
            "source_url": source_url,
            "source_class": "official_public_notices",
            "retrieved_at": ann_record.get("retrieved_at") or completed_at,
            "content_sha256": _evidence_hash(ann_record, ann_val),
            "claim_span": f"{len(ann_events)} corporate announcements recorded",
        })
        count_val = len(ann_events) if ann_events else int(ann_val.get("corporate_actions_count") or 0)
        claims.append({
            "field": "corporate_announcements_count",
            "value": count_val,
            "availability": "available",
            "confidence": 0.99,
            "evidence_ids": [ann_ev_id],
        })

        dated_events = [e for e in ann_events if isinstance(e, dict) and e.get("date")]
        dated_events.sort(key=lambda x: str(x.get("date")), reverse=True)

        # Preserve every dated notice as an independently inspectable claim.
        # The aggregate announcements record is useful for coverage, but its
        # span cannot substantiate an arbitrary historical title or date.
        for event_index, event in enumerate(dated_events, start=1):
            event_ev_id = f"ev-announcement-{event_index}"
            event_url = str(event.get("source_url") or source_url)
            if event_url.startswith("/"):
                event_url = f"https://w2.brreg.no{event_url}"
            elif not event_url.startswith("http"):
                event_url = f"https://w2.brreg.no/kunngjoring/{event_url.lstrip('/')}"
            event_title = event.get("title") or event.get("type") or event.get("description") or "Kunngjøring"
            event_date = event.get("date")
            ev_items.append({
                "id": event_ev_id,
                "source_url": event_url,
                "source_class": "official_public_notice",
                "retrieved_at": event.get("retrieved_at") or ann_record.get("retrieved_at") or completed_at,
                "content_sha256": _event_hash(event, ann_record),
                "claim_span": f"{event_date}: {event_title}",
            })
            for suffix, value in (("date", event_date), ("title", event_title), ("url", event_url)):
                claims.append({
                    "field": f"corporate_action_{event_index}_{suffix}",
                    "value": value,
                    "availability": "available",
                    "confidence": 0.99,
                    "evidence_ids": [event_ev_id],
                })

        if dated_events:
            latest_action = dated_events[0]
            action_url = str(latest_action.get("source_url") or source_url)
            if action_url.startswith("/"):
                action_url = f"https://w2.brreg.no{action_url}"
            elif not action_url.startswith("http"):
                action_url = f"https://w2.brreg.no/kunngjoring/{action_url.lstrip('/')}"

            latest_title = latest_action.get("title") or latest_action.get("type") or None

            claims.append({
                "field": "latest_corporate_action_date",
                "value": latest_action.get("date"),
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [ann_ev_id],
            })
            claims.append({
                "field": "latest_corporate_action_title",
                "value": latest_title,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [ann_ev_id],
            })
            claims.append({
                "field": "latest_corporate_action_url",
                "value": action_url,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [ann_ev_id],
            })
        else:
            claims.append({
                "field": "latest_corporate_action_date",
                "value": None,
                "availability": "not_available",
                "confidence": 1.0,
                "evidence_ids": [ann_ev_id],
            })
            claims.append({
                "field": "latest_corporate_action_title",
                "value": None,
                "availability": "not_available",
                "confidence": 1.0,
                "evidence_ids": [ann_ev_id],
            })
            claims.append({
                "field": "latest_corporate_action_url",
                "value": None,
                "availability": "not_available",
                "confidence": 1.0,
                "evidence_ids": [ann_ev_id],
            })

        cap_date = ann_cats.get("capital_change", {}).get("latest_date")
        if not cap_date:
            for ev in dated_events:
                if ev.get("category") == "capital_change" and ev.get("date"):
                    cap_date = ev.get("date")
                    break

        if cap_date:
            claims.append({
                "field": "latest_capital_change_date",
                "value": cap_date,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [ann_ev_id],
            })
        else:
            claims.append({
                "field": "latest_capital_change_date",
                "value": None,
                "availability": "not_available",
                "confidence": 1.0,
                "evidence_ids": [ann_ev_id],
            })

        board_date = ann_cats.get("board_appointment", {}).get("latest_date")
        if not board_date:
            for ev in dated_events:
                if ev.get("category") == "board_appointment" and ev.get("date"):
                    board_date = ev.get("date")
                    break

        if board_date:
            claims.append({
                "field": "latest_board_appointment_date",
                "value": board_date,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [ann_ev_id],
            })
        else:
            claims.append({
                "field": "latest_board_appointment_date",
                "value": None,
                "availability": "not_available",
                "confidence": 1.0,
                "evidence_ids": [ann_ev_id],
            })

        for category, field in (
            ("legal_filing", "latest_legal_filing_date"),
            ("insolvency", "latest_insolvency_date"),
        ):
            category_value = ann_cats.get(category)
            category_date = category_value.get("latest_date") if isinstance(category_value, dict) else None
            if not category_date:
                category_date = next(
                    (
                        event.get("date")
                        for event in dated_events
                        if event.get("category") == category and event.get("date")
                    ),
                    None,
                )
            claims.append({
                "field": field,
                "value": category_date,
                "availability": "available" if category_date else "not_available",
                "confidence": 0.99 if category_date else 1.0,
                "evidence_ids": [ann_ev_id],
            })
    else:
        ann_state, ann_confidence = _record_missing_state(ann_record)
        for field in (
            "corporate_announcements_count",
            "latest_corporate_action_date",
            "latest_corporate_action_title",
            "latest_corporate_action_url",
            "latest_capital_change_date",
            "latest_board_appointment_date",
            "latest_legal_filing_date",
            "latest_insolvency_date",
        ):
            claims.append({
                "field": field,
                "value": None,
                "availability": ann_state,
                "confidence": ann_confidence,
                "evidence_ids": [],
            })

    # 4c. Google News RSS Evidence & Claims
    news_record = evidence_dict.get("news")
    if not news_record and isinstance(profile.get("news"), dict):
        news_record = profile.get("news")

    news_ev_id = "ev-news"
    news_val = (
        news_record.get("value")
        if isinstance(news_record, dict) and isinstance(news_record.get("value"), dict)
        else (news_record if isinstance(news_record, dict) else {})
    )
    news_items = news_val.get("items") if isinstance(news_val, dict) and isinstance(news_val.get("items"), list) else []
    latest_news = (
        news_val.get("latest")
        if isinstance(news_val, dict) and isinstance(news_val.get("latest"), dict)
        else (news_items[0] if news_items and isinstance(news_items[0], dict) else None)
    )

    # Company-site news and press items are both published, newest first. Each item
    # carries its own evidence (URL, hash, span) and its own source class, so a
    # 2019 blog post never hides a 2026 press article and vice versa.
    web_record_check = evidence_dict.get("website") or {}
    web_val_check = web_record_check.get("value") or {}
    web_assessment = web_val_check.get("identity_assessment") or {}
    site_news: list[dict[str, Any]] = []
    if web_assessment.get("publishable"):
        from .website import _is_junk_news, is_site_job, looks_like_news_item
        seen_news: set[tuple[str, str]] = set()
        from .website import drop_placeholder_cohorts
        for item in drop_placeholder_cohorts(web_val_check.get("news_items") or []):
            if not (isinstance(item, dict) and item.get("title") and item.get("date") and item.get("url")):
                continue
            if is_site_job(item) or not looks_like_news_item(item) or _is_junk_news(str(item["title"]), str(item["url"]), "", str(item["date"])):
                continue
            key_url, key_title = str(item["url"]).rstrip("/"), str(item["title"]).casefold()
            if any(key_url == u or key_title == t for u, t in seen_news):
                continue
            seen_news.add((key_url, key_title))
            site_news.append(item)
    _verified_site_url = str(web_val_check.get("final_url") or web_record_check.get("source_url") or "")
    site_news = filter_site_news(site_news, site_url=_verified_site_url, nace=profile.get("industry_code") or profile.get("nace_code"))
    _gated_social_urls = {
        str(link.get("url")) for link in gate_social_links(
            web_val_check.get("social_links") or [], site_url=_verified_site_url, legal_name=str(profile.get("name") or ""))
    } if web_assessment.get("publishable") else set()
    site_news_record = {
        "status": "available",
        "source_url": web_val_check.get("final_url") or web_record_check.get("source_url") or "",
        "source_class": "company_owned",
        "retrieved_at": web_record_check.get("retrieved_at") or completed_at,
        "content_sha256": web_val_check.get("content_sha256") or hashlib.sha256(b"").hexdigest(),
    }
    press_available = bool(news_record and news_record.get("status") == "available")
    press_news = [
        item for item in (news_items if press_available else [])
        if isinstance(item, dict) and item.get("title") and item.get("date") and item.get("url")
    ]
    if press_available and not press_news and isinstance(latest_news, dict) and latest_news.get("title") and latest_news.get("date") and latest_news.get("url"):
        press_news = [latest_news]
    site_urls = {str(item["url"]).rstrip("/") for item in site_news}
    combined_news: list[tuple[dict[str, Any], dict[str, Any], str]] = [(item, site_news_record, "ev-website-news") for item in site_news]
    combined_news += [(item, news_record, "ev-news") for item in press_news if str(item["url"]).rstrip("/") not in site_urls]
    combined_news.sort(key=lambda row: (str(row[0].get("date")), row[2] == "ev-website-news"), reverse=True)
    combined_news = combined_news[:15]

    for news_index, (item, record, _group_id) in enumerate(combined_news, start=1):
        news_item_ev_id = f"ev-news-{news_index}"
        news_url = str(item.get("url"))
        news_title = str(item.get("title"))
        news_date = item.get("date")
        ev_items.append({
            "id": news_item_ev_id,
            "source_url": news_url,
            "source_class": record.get("source_class") or "public_news",
            "retrieved_at": item.get("retrieved_at") or record.get("retrieved_at") or completed_at,
            "content_sha256": _event_hash(item, record),
            "claim_span": f"{news_date}: {news_title}",
        })
        for suffix, value in (("date", news_date), ("title", news_title), ("url", news_url)):
            claims.append({
                "field": f"news_{news_index}_{suffix}",
                "value": value,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [news_item_ev_id],
            })

    # Group evidence records (also cited by the model summary): one for company-site
    # news, one for the press query, each only when it produced published items.
    for group_id, record, default_url in (("ev-website-news", site_news_record, ""), ("ev-news", news_record or {}, "https://news.google.com/rss")):
        group_items = [item for item, _rec, gid in combined_news if gid == group_id]
        if not group_items:
            continue
        content_sha = str(record.get("content_sha256") or (news_val.get("content_sha256") if group_id == "ev-news" and isinstance(news_val, dict) else "") or "")
        if len(content_sha) != 64:
            content_sha = _stable_content_hash({"items": [(i.get("date"), i.get("title"), i.get("url")) for i in group_items]})
        ev_items.append({
            "id": group_id,
            "source_url": record.get("source_url") or default_url or group_items[0].get("url"),
            "source_class": record.get("source_class") or "public_news",
            "retrieved_at": record.get("retrieved_at") or completed_at,
            "content_sha256": content_sha,
            "claim_span": str(group_items[0].get("title") or "Company news"),
        })

    if combined_news:
        latest_item, _latest_record, latest_group = combined_news[0]
        for suffix in ("title", "date", "url"):
            claims.append({
                "field": f"latest_news_{suffix}",
                "value": latest_item.get(suffix),
                "availability": "available",
                "confidence": 0.99,
                # The item's own record (its article URL) first, then the feed it came from.
                "evidence_ids": ["ev-news-1", latest_group],
            })
    else:
        news_state, news_confidence = _record_missing_state(news_record)
        for field in ("latest_news_title", "latest_news_date", "latest_news_url"):
            claims.append({
                "field": field,
                "value": None,
                "availability": news_state,
                "confidence": news_confidence,
                "evidence_ids": [],
            })

    # 5. Website & Company-Owned Footprint
    web_record = evidence_dict.get("website")
    web_ev_id = "ev-website"
    if web_record:
        status = web_record.get("status")
        web_val = web_record.get("value") or {}
        site_url = profile.get("website") or web_record.get("source_url")
        from .website import normalize_homepage
        # The registry stores homepages as free text ("WWW.NORMORIA.NO", "www.gjerstad.com"):
        # publish a real URL (the one that was actually fetched).
        if site_url and not str(site_url).lower().startswith(("http://", "https://")):
            fetched = str(web_val.get("final_url") or web_record.get("source_url") or "")
            site_url = fetched if fetched.lower().startswith(("http://", "https://")) else (normalize_homepage(site_url) or "https://" + str(site_url).strip().lower().lstrip("/"))
        if site_url:
            parsed = urllib.parse.urlsplit(str(site_url))
            if any(c.isupper() for c in (parsed.scheme + parsed.netloc)):
                norm_url = normalize_homepage(site_url)
                if norm_url:
                    site_url = norm_url
                else:
                    site_url = urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, parsed.fragment))
        assessment = web_val.get("identity_assessment") or {}
        is_publishable = bool(assessment.get("publishable"))
        gate_score = float(assessment.get("score") or 0.3)

        if status == "available" and site_url:
            # 5a. Primary Homepage Evidence Record
            ev_items.append({
                "id": web_ev_id,
                "source_url": site_url,
                "source_class": "company_owned",
                "retrieved_at": web_record.get("retrieved_at") or completed_at,
                "content_sha256": _evidence_hash(web_record, web_val),
                "claim_span": str(web_val.get("title") or site_url)[:200],
            })

            # 5b. Secondary Subpages Evidence Records
            from .website import is_career_url
            contact_ev_id = web_ev_id
            career_ev_id = web_ev_id
            # Each captured page gets its own evidence id, so a contact detail or a
            # career page can cite the page it was actually read from.
            page_ev_ids: dict[str, str] = {}
            used_sub_ids: set[str] = set()
            for idx, page in enumerate(web_val.get("pages") or []):
                p_url = page.get("url") or ""
                p_sha = page.get("content_sha256") or ""
                if not p_url or p_url.rstrip("/") == site_url.rstrip("/") or not p_sha or p_url in page_ev_ids:
                    continue
                p_lower = (p_url + " " + (page.get("title") or "")).lower()
                p_span = str(page.get("main_text_excerpt") or page.get("title") or p_url)[:200]
                p_segments = [seg for seg in urllib.parse.urlsplit(p_url).path.casefold().split("/") if seg]
                if any(re.match(r"(?:kontakt|contact|lokasjon|location|finn-oss)", seg) for seg in p_segments):
                    base_id = "ev-website-contact"
                elif is_career_url(p_url):
                    base_id = "ev-website-career"
                elif any(t in p_lower for t in ("om-oss", "om_oss", "about")):
                    base_id = "ev-website-about"
                else:
                    base_id = f"ev-website-sub-{idx}"
                sub_id = base_id if base_id not in used_sub_ids else f"{base_id}-{idx}"
                used_sub_ids.add(sub_id)
                page_ev_ids[p_url] = sub_id
                if base_id == "ev-website-contact" and contact_ev_id == web_ev_id:
                    contact_ev_id = sub_id
                if base_id == "ev-website-career" and career_ev_id == web_ev_id:
                    career_ev_id = sub_id
                ev_items.append({
                    "id": sub_id,
                    "source_url": p_url,
                    "source_class": "company_owned",
                    "retrieved_at": web_record.get("retrieved_at") or completed_at,
                    "content_sha256": p_sha if len(p_sha) == 64 else _stable_content_hash(page),
                    "claim_span": p_span,
                })

            def contact_sources(kind: str, values: Any) -> list[str]:
                """Evidence ids of the pages each published contact value was read from."""
                vals = [values] if isinstance(values, str) else [str(v) for v in values or []]
                ids: list[str] = []
                for v in vals:
                    key = re.sub(r"\D", "", v) if kind == "phones" else v.casefold()
                    if kind == "addresses":
                        key = _street_key(v.split(",")[0])
                    found = web_ev_id
                    for page in web_val.get("pages") or []:
                        sub = page_ev_ids.get(page.get("url") or "")
                        if not sub:
                            continue
                        page_vals = ((page.get("contacts") or {}).get(kind)) or []
                        norm = [re.sub(r"\D", "", str(x)) if kind == "phones" else str(x).casefold() for x in page_vals]
                        if kind == "addresses":
                            hit = bool(key) and any(key in _street_key(x) for x in norm)
                        else:
                            hit = key in norm
                        if hit:
                            found = sub
                            break
                    if found not in ids:
                        ids.append(found)
                return ordered_ev_ids(ids)

            def ordered_ev_ids(ids: list[str]) -> list[str]:
                return [item["id"] for item in ev_items if item["id"] in set(ids)] or [contact_ev_id]

            # 5c. Subpage-Level Block Detection (robots.txt)
            crawl_errors = web_val.get("crawl_errors") or []
            career_blocked = any(
                any(t in str(err.get("url", "")).lower() for t in ("karriere", "career", "job", "stilling"))
                and any(b in str(err.get("error", "")).lower() for b in ("robot", "block", "denied", "disallow"))
                for err in crawl_errors
            )
            contact_blocked = any(
                any(t in str(err.get("url", "")).lower() for t in ("kontakt", "contact"))
                and any(b in str(err.get("error", "")).lower() for b in ("robot", "block", "denied", "disallow"))
                for err in crawl_errors
            )

            # 5d. Resolve Contact Values
            raw_contacts = web_val.get("contacts") or {}
            raw_emails = raw_contacts.get("emails") if isinstance(raw_contacts, dict) else None
            if raw_emails is None:
                raw_emails = web_val.get("contact_email") or web_val.get("emails")
            raw_phones = raw_contacts.get("phones") if isinstance(raw_contacts, dict) else None
            if raw_phones is None:
                raw_phones = web_val.get("contact_phone") or web_val.get("phones")
            raw_addresses = raw_contacts.get("addresses") if isinstance(raw_contacts, dict) else None
            if raw_addresses is None:
                raw_addresses = web_val.get("contact_address") or web_val.get("addresses")

            # 5e. Resolve Hiring Values
            raw_hiring = web_val.get("hiring") or {}
            career_url_val = raw_hiring.get("career_url") if isinstance(raw_hiring, dict) else None
            if career_url_val is None:
                career_url_val = web_val.get("career_url")

            hiring_active_val = raw_hiring.get("hiring_active") if isinstance(raw_hiring, dict) and "hiring_active" in raw_hiring else None
            if hiring_active_val is None and "hiring_active" in web_val:
                hiring_active_val = web_val.get("hiring_active")

            open_count_val = raw_hiring.get("open_positions_count") if isinstance(raw_hiring, dict) and "open_positions_count" in raw_hiring else None
            if open_count_val is None and "open_positions_count" in web_val:
                open_count_val = web_val.get("open_positions_count")

            open_postings = web_val.get("open_postings") or raw_hiring.get("open_postings") or []
            positions_val = raw_hiring.get("open_positions") if isinstance(raw_hiring, dict) and "open_positions" in raw_hiring else None
            if positions_val is None and "open_positions" in web_val:
                positions_val = web_val.get("open_positions")
            if positions_val is None and "job_titles" in web_val:
                positions_val = web_val.get("job_titles")
            if not positions_val and open_postings:
                positions_val = [p["title"] for p in open_postings if isinstance(p, dict) and p.get("title")]

            if positions_val:
                from .website import GENERIC_JOB_HEADING
                positions_val = [t for t in positions_val if not GENERIC_JOB_HEADING.search(" ".join(str(t).split()).casefold())]
                open_postings = [p for p in open_postings if not (isinstance(p, dict) and GENERIC_JOB_HEADING.search(" ".join(str(p.get("title") or "").split()).casefold()))]

            has_hiring_data = (career_url_val is not None) or (hiring_active_val is not None) or (open_count_val is not None) or (positions_val is not None) or (career_ev_id != web_ev_id)

            if is_publishable:
                # 1. official_website
                site_ev_ids = [web_ev_id]
                site_confidence = 0.95
                registry_proof = next((p for p in assessment.get("promotion_proof") or []
                                       if isinstance(p, dict) and p.get("type") == "registry_declared_domain_spells_legal_name"), None)
                if registry_proof:
                    # Proof is the company's own Brønnøysund declaration (homepage or e-mail
                    # domain) plus a domain that spells its whole legal name: cite that
                    # declaration verbatim next to the fetched homepage.
                    declared_key = "epostadresse" if registry_proof.get("origin") == "registry_email_domain" else "hjemmeside"
                    declared = str(reg_val.get(declared_key) or "").strip()
                    if declared and reg_record and reg_record.get("status") == "available":
                        ev_items.append({
                            "id": "ev-registry-website",
                            "source_url": reg_record.get("source_url") or f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}",
                            "source_class": "official_registry",
                            "retrieved_at": reg_record.get("retrieved_at") or completed_at,
                            "content_sha256": _evidence_hash(reg_record, reg_val),
                            "claim_span": f'"{declared_key}":"{declared}"',  # as Brønnøysund serves it
                        })
                        site_ev_ids.append("ev-registry-website")
                    site_confidence = 0.85
                claims.append({
                    "field": "official_website",
                    "value": site_url,
                    "availability": "available",
                    "confidence": site_confidence,
                    "evidence_ids": site_ev_ids,
                })
                # 2. website_title
                if web_val.get("title"):
                    claims.append({
                        "field": "website_title",
                        "value": web_val.get("title"),
                        "availability": "available",
                        "confidence": 0.95,
                        "evidence_ids": [web_ev_id],
                    })
                # 3. website_description
                if web_val.get("description"):
                    claims.append({
                        "field": "website_description",
                        "value": web_val.get("description"),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [web_ev_id],
                    })
                else:
                    claims.append({
                        "field": "website_description",
                        "value": None,
                        "availability": "not_available",
                        "confidence": 1.0,
                        "evidence_ids": [web_ev_id],
                    })

                # 4. contact_email: only addresses on the company's own verified domain or the
                # domain it registered with Brreg. Footer addresses of web agencies, media groups,
                # sister companies, site-builder placeholders (info@mysite.com) and typos are dropped.
                raw_emails = own_domain_emails(raw_emails, site_url, reg_val.get("epostadresse") if reg_record else None)
                if raw_emails:
                    email_val = raw_emails if isinstance(raw_emails, (str, list)) else str(raw_emails)
                    claims.append({
                        "field": "contact_email",
                        "value": email_val,
                        "availability": "available",
                        "confidence": 0.95,
                        "evidence_ids": contact_sources("emails", email_val),
                    })
                elif reg_record and reg_record.get("status") == "available" and reg_val.get("epostadresse"):
                    claims.append({
                        "field": "contact_email",
                        "value": str(reg_val["epostadresse"]).strip(),
                        "availability": "available",
                        "confidence": 0.99,
                        "evidence_ids": [reg_ev_id],
                    })
                elif contact_blocked:
                    claims.append({"field": "contact_email", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                else:
                    claims.append({"field": "contact_email", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [contact_ev_id]})

                # 5. contact_phone
                if isinstance(raw_phones, list):
                    # "+4791704447" and "+47 91 70 44 47" are one number: keep the first spelling.
                    seen_digits: set[str] = set()
                    deduped_phones = []
                    for ph in raw_phones:
                        digits = re.sub(r"\D", "", str(ph))
                        digits = digits[2:] if digits.startswith("47") and len(digits) == 10 else digits
                        if digits and digits not in seen_digits:
                            seen_digits.add(digits)
                            deduped_phones.append(ph)
                    raw_phones = deduped_phones
                if raw_phones:
                    phone_val = raw_phones if isinstance(raw_phones, (str, list)) else str(raw_phones)
                    claims.append({
                        "field": "contact_phone",
                        "value": phone_val,
                        "availability": "available",
                        "confidence": 0.95,
                        "evidence_ids": contact_sources("phones", phone_val),
                    })
                elif reg_record and reg_record.get("status") == "available" and (reg_val.get("telefon") or reg_val.get("mobil")):
                    claims.append({
                        "field": "contact_phone",
                        "value": str(reg_val.get("telefon") or reg_val.get("mobil")).strip(),
                        "availability": "available",
                        "confidence": 0.99,
                        "evidence_ids": [reg_ev_id],
                    })
                elif contact_blocked:
                    claims.append({"field": "contact_phone", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                else:
                    claims.append({"field": "contact_phone", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [contact_ev_id]})

                # 6. contact_address: only clean "street no, postcode city" strings whose postcode and
                # city are one of the company's own registered addresses (main unit or sub-units).
                raw_addresses = clean_registered_addresses(raw_addresses, profile)
                if raw_addresses:
                    addr_val = raw_addresses if isinstance(raw_addresses, (str, list)) else str(raw_addresses)
                    claims.append({
                        "field": "contact_address",
                        "value": addr_val,
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": contact_sources("addresses", addr_val),
                    })
                elif contact_blocked:
                    claims.append({"field": "contact_address", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                else:
                    claims.append({"field": "contact_address", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [contact_ev_id]})

                # 7. career_url: cite the career page itself when it was captured.
                if career_url_val and page_ev_ids.get(career_url_val):
                    career_ev_id = page_ev_ids[career_url_val]
                if career_url_val:
                    claims.append({
                        "field": "career_url",
                        "value": career_url_val,
                        "availability": "available",
                        "confidence": 0.95,
                        "evidence_ids": [career_ev_id],
                    })
                elif career_blocked:
                    claims.append({"field": "career_url", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                else:
                    claims.append({"field": "career_url", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [web_ev_id]})

                # 8. hiring_active
                open_postings = web_val.get("open_postings") or raw_hiring.get("open_postings") or []
                has_real_career = bool((career_url_val and career_url_val.rstrip("/").lower() != site_url.rstrip("/").lower()) or open_postings)
                if career_blocked:
                    claims.append({"field": "hiring_active", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                elif not has_real_career:
                    claims.append({"field": "hiring_active", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [web_ev_id]})
                elif hiring_active_val is not None:
                    claims.append({
                        "field": "hiring_active",
                        "value": bool(hiring_active_val),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [career_ev_id],
                    })
                elif has_hiring_data and open_count_val is not None:
                    claims.append({
                        "field": "hiring_active",
                        "value": bool(open_count_val > 0),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [career_ev_id],
                    })
                else:
                    claims.append({"field": "hiring_active", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [web_ev_id]})

                # 9. open_positions_count
                if career_blocked:
                    claims.append({"field": "open_positions_count", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                elif not has_real_career:
                    claims.append({"field": "open_positions_count", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [web_ev_id]})
                elif open_count_val is not None:
                    claims.append({
                        "field": "open_positions_count",
                        "value": int(open_count_val),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [career_ev_id],
                    })
                elif positions_val:
                    claims.append({
                        "field": "open_positions_count",
                        "value": len(positions_val),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [career_ev_id],
                    })
                else:
                    claims.append({"field": "open_positions_count", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [web_ev_id]})

                # 10. open_positions & verified job postings
                if career_blocked:
                    claims.append({"field": "open_positions", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                elif not has_real_career:
                    claims.append({"field": "open_positions", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [web_ev_id]})
                elif positions_val:
                    claims.append({
                        "field": "open_positions",
                        "value": list(positions_val),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [career_ev_id],
                    })
                    first_job_title = open_postings[0]["title"] if open_postings and isinstance(open_postings[0], dict) else (positions_val[0] if positions_val else None)
                    first_job_url = (open_postings[0].get("url") if open_postings and isinstance(open_postings[0], dict) else None) or career_url_val or site_url
                    if first_job_title:
                        claims.append({
                            "field": "job_posting_title",
                            "value": first_job_title,
                            "availability": "available",
                            "confidence": 0.90,
                            "evidence_ids": [career_ev_id],
                        })
                    if first_job_title and first_job_url:
                        claims.append({
                            "field": "job_posting_url",
                            "value": first_job_url,
                            "availability": "available",
                            "confidence": 0.90,
                            "evidence_ids": [career_ev_id],
                        })
                    for p_idx, p_item in enumerate(open_postings[:5], start=1):
                        if isinstance(p_item, dict) and p_item.get("title"):
                            claims.append({
                                "field": f"job_posting_{p_idx}_title",
                                "value": p_item["title"],
                                "availability": "available",
                                "confidence": 0.90,
                                "evidence_ids": [career_ev_id],
                            })
                            if p_item.get("url"):
                                claims.append({
                                    "field": f"job_posting_{p_idx}_url",
                                    "value": p_item["url"],
                                    "availability": "available",
                                    "confidence": 0.90,
                                    "evidence_ids": [career_ev_id],
                                })
                else:
                    claims.append({"field": "open_positions", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [web_ev_id]})

                # Social profiles
                socials = [s for s in (web_val.get("social_links") or []) if str(s.get("url")) in _gated_social_urls]
                for s in socials:
                    platform = s.get("platform")
                    url = s.get("url")
                    if platform and url:
                        check = assess_social_identity(profile, s)
                        if check.get("publishable"):
                            claims.append({
                                "field": f"social_{platform}",
                                "value": url,
                                "availability": "available",
                                "confidence": 0.90,
                                "evidence_ids": [web_ev_id],
                            })
            else:
                # Quarantined due to uncertain identity (Zero Wrong-Company Guarantee)
                # Value is strictly None so no unverified external data is published as fact.
                claims.append({
                    "field": "official_website",
                    "value": None,
                    "availability": "ambiguous",
                    "confidence": gate_score,
                    "evidence_ids": [web_ev_id],
                })
                claims.append({
                    "field": "website_description",
                    "value": None,
                    "availability": "ambiguous" if web_val.get("description") else "not_available",
                    "confidence": gate_score if web_val.get("description") else 1.0,
                    "evidence_ids": [web_ev_id] if web_val.get("description") else [],
                })
                claims.append({
                    "field": "contact_email",
                    "value": None,
                    "availability": "ambiguous" if raw_emails else "not_available",
                    "confidence": gate_score if raw_emails else 1.0,
                    "evidence_ids": [contact_ev_id] if raw_emails else [],
                })
                claims.append({
                    "field": "contact_phone",
                    "value": None,
                    "availability": "ambiguous" if raw_phones else "not_available",
                    "confidence": gate_score if raw_phones else 1.0,
                    "evidence_ids": [contact_ev_id] if raw_phones else [],
                })
                claims.append({
                    "field": "contact_address",
                    "value": None,
                    "availability": "ambiguous" if raw_addresses else "not_available",
                    "confidence": gate_score if raw_addresses else 1.0,
                    "evidence_ids": [contact_ev_id] if raw_addresses else [],
                })
                claims.append({
                    "field": "career_url",
                    "value": None,
                    "availability": "ambiguous" if career_url_val else "not_available",
                    "confidence": gate_score if career_url_val else 1.0,
                    "evidence_ids": [career_ev_id] if career_url_val else [],
                })
                claims.append({
                    "field": "hiring_active",
                    "value": None,
                    "availability": "ambiguous" if hiring_active_val is not None else "not_available",
                    "confidence": gate_score if hiring_active_val is not None else 1.0,
                    "evidence_ids": [career_ev_id] if hiring_active_val is not None else [],
                })
                claims.append({
                    "field": "open_positions_count",
                    "value": None,
                    "availability": "ambiguous" if open_count_val is not None else "not_available",
                    "confidence": gate_score if open_count_val is not None else 1.0,
                    "evidence_ids": [career_ev_id] if open_count_val is not None else [],
                })
                claims.append({
                    "field": "open_positions",
                    "value": None,
                    "availability": "ambiguous" if positions_val is not None else "not_available",
                    "confidence": gate_score if positions_val is not None else 1.0,
                    "evidence_ids": [career_ev_id] if positions_val is not None else [],
                })
        elif status in {"blocked", "blocked_robots", "blocked_policy"}:
            # The URL itself stays unpublished: a value with no evidence reads as a claim.
            claims.append({"field": "official_website", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "website_description", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "contact_email", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "contact_phone", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "contact_address", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "career_url", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "hiring_active", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "open_positions_count", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "open_positions", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
        else:
            # A registered site that could not be read (timeouts, 429/5xx after one retry)
            # is unknown, not absent: those fields are `failed`.
            site_state = "failed" if status == "source_error" else "not_available"
            site_conf = 0.0 if site_state == "failed" else 1.0
            claims.append({"field": "official_website", "value": None, "availability": site_state, "confidence": site_conf, "evidence_ids": []})
            claims.append({"field": "website_description", "value": None, "availability": site_state, "confidence": site_conf, "evidence_ids": []})
            if reg_record and reg_record.get("status") == "available" and reg_val.get("epostadresse"):
                claims.append({"field": "contact_email", "value": str(reg_val["epostadresse"]).strip(), "availability": "available", "confidence": 0.99, "evidence_ids": [reg_ev_id]})
            else:
                claims.append({"field": "contact_email", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            if reg_record and reg_record.get("status") == "available" and (reg_val.get("telefon") or reg_val.get("mobil")):
                claims.append({"field": "contact_phone", "value": str(reg_val.get("telefon") or reg_val.get("mobil")).strip(), "availability": "available", "confidence": 0.99, "evidence_ids": [reg_ev_id]})
            else:
                claims.append({"field": "contact_phone", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            claims.append({"field": "contact_address", "value": None, "availability": site_state, "confidence": site_conf, "evidence_ids": []})
            claims.append({"field": "career_url", "value": None, "availability": site_state, "confidence": site_conf, "evidence_ids": []})
            claims.append({"field": "hiring_active", "value": None, "availability": site_state, "confidence": site_conf, "evidence_ids": []})
            claims.append({"field": "open_positions_count", "value": None, "availability": site_state, "confidence": site_conf, "evidence_ids": []})
            claims.append({"field": "open_positions", "value": None, "availability": site_state, "confidence": site_conf, "evidence_ids": []})
    else:
        claims.append({"field": "official_website", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "website_description", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        if reg_record and reg_record.get("status") == "available" and reg_val.get("epostadresse"):
            claims.append({"field": "contact_email", "value": str(reg_val["epostadresse"]).strip(), "availability": "available", "confidence": 0.99, "evidence_ids": [reg_ev_id]})
        else:
            claims.append({"field": "contact_email", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        if reg_record and reg_record.get("status") == "available" and (reg_val.get("telefon") or reg_val.get("mobil")):
            claims.append({"field": "contact_phone", "value": str(reg_val.get("telefon") or reg_val.get("mobil")).strip(), "availability": "available", "confidence": 0.99, "evidence_ids": [reg_ev_id]})
        else:
            claims.append({"field": "contact_phone", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "contact_address", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "career_url", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "hiring_active", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "open_positions_count", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "open_positions", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})

    # Search results are candidates, not evidence (source policy): only profiles
    # declared by the employer on an orgnr-verified NAV ad are published from here.
    # Profiles linked from the verified company site are emitted with the website.
    discovered_socials = [ds for ds in profile.get("_discovered_socials") or [] if ds.get("source") == "nav_employer_profile"]
    for ds in discovered_socials:
        p = ds.get("platform")
        u = ds.get("url")
        if p and u and social_handle(p, u) and not any(c.get("field") == f"social_{p}" for c in claims):
            check = assess_social_identity(profile, ds)
            if not check.get("publishable"):
                continue
            ev_id = f"ev-social-{p}"
            ds_hash = hashlib.sha256(json.dumps(ds, sort_keys=True).encode("utf-8")).hexdigest()
            ev_items.append({
                "id": ev_id,
                "source_url": ds.get("evidence_url") or u,
                "source_class": "public_social_profile",
                "retrieved_at": completed_at,
                "content_sha256": ds_hash,
                "claim_span": f"{profile.get('name')} {p} verified profile ({u})",
            })
            claims.append({
                "field": f"social_{p}",
                "value": u,
                "availability": "available",
                "confidence": 0.90,
                "evidence_ids": [ev_id],
            })

    from .website import is_current_site_job
    # 5z. Open job postings from NAV arbeidsplassen (employer orgnr verified)
    #     plus job-like posts on the verified company site.
    jobs_record = evidence_dict.get("jobs") or {}
    jobs_val = jobs_record.get("value") or {}
    postings = [p for p in (jobs_val.get("postings") or []) if isinstance(p, dict) and p.get("title") and p.get("url")]
    web_rec_jobs = evidence_dict.get("website") or {}
    web_val_jobs = web_rec_jobs.get("value") or {}
    if (web_val_jobs.get("identity_assessment") or {}).get("publishable"):
        for item in web_val_jobs.get("news_items") or []:
            if is_current_site_job(item) and item.get("url") and item.get("title"):
                if not any(p.get("url") == item["url"] or p.get("title", "").casefold() == str(item["title"]).casefold() for p in postings):
                    postings.append({
                        "title": item["title"], "url": item["url"], "published": item.get("date"),
                        "source": "company_site", "content_sha256": web_val_jobs.get("content_sha256"),
                        "retrieved_at": web_rec_jobs.get("retrieved_at"),
                        "claim_span": f"{item.get('date')}: {item['title']}",
                    })
    if postings:
        claims[:] = [c for c in claims if c.get("field") not in {"hiring_active", "job_posting_title", "job_posting_url", "open_positions_count", "open_positions"} and not str(c.get("field", "")).startswith("job_posting_")]
        first_ev = None
        for j_idx, post in enumerate(postings[:10], start=1):
            j_ev = f"ev-job-{j_idx}"
            first_ev = first_ev or j_ev
            is_site = post.get("source") in {"company_site", "company_career_page"}
            sha = str(post.get("content_sha256") or "")
            ev_items.append({
                "id": j_ev,
                "source_url": post["url"],
                "source_class": "company_owned" if is_site else "public_job_board",
                "retrieved_at": post.get("retrieved_at") or completed_at,
                "content_sha256": sha if len(sha) == 64 else hashlib.sha256(json.dumps(post, sort_keys=True, default=str).encode()).hexdigest(),
                "claim_span": str(post.get("claim_span") or post["title"])[:300],
                "effective_at": post.get("published"),
            })
            for suffix, value in (
                ("title", post["title"]), ("url", post["url"]), ("published", post.get("published")),
                ("expires", post.get("expires")), ("location", post.get("location")),
            ):
                if value:
                    claims.append({"field": f"job_posting_{j_idx}_{suffix}", "value": value, "availability": "available", "confidence": 0.99, "evidence_ids": [j_ev]})
        claims.append({"field": "job_posting_title", "value": postings[0]["title"], "availability": "available", "confidence": 0.99, "evidence_ids": [first_ev]})
        claims.append({"field": "job_posting_url", "value": postings[0]["url"], "availability": "available", "confidence": 0.99, "evidence_ids": [first_ev]})
        claims.append({"field": "open_positions_count", "value": len(postings), "availability": "available", "confidence": 0.95, "evidence_ids": [first_ev]})
        claims.append({"field": "hiring_active", "value": True, "availability": "available", "confidence": 0.99, "evidence_ids": [first_ev]})

    # 5ai. Model analysis over cited facts (optional). Each claim points at the
    #      source of the fact it was derived from; the analysis hash pins the output.
    ai = profile.get("ai_analysis") or {}
    # Per-item model labels (event type, materiality, seniority, ...) are judgement
    # calls, shown in the UI but not published as scored claims unless opted in.
    # The validated, cited executive summary is published through synthesis.
    import os as _os
    if ai.get("status") == "available" and _os.environ.get("SIGNALPOST_AI_CLAIMS", "0") == "1":
        ai_facts = {f["id"]: f for f in ai.get("facts") or [] if isinstance(f, dict) and f.get("id")}
        ai_sha = str(ai.get("content_sha256") or "")
        ai_counter = [0]

        def ai_evidence(fact_ids: list[str], note: str) -> str | None:
            cited = [ai_facts[f] for f in fact_ids if f in ai_facts and ai_facts[f].get("source_url")]
            if not cited or len(ai_sha) != 64:
                return None
            ai_counter[0] += 1
            ev_id = f"ev-ai-{ai_counter[0]}"
            ev_items.append({
                "id": ev_id,
                "source_url": cited[0]["source_url"],
                "source_class": "model_analysis_of_cited_facts",
                "retrieved_at": ai.get("generated_at") or completed_at,
                "content_sha256": ai_sha,
                "claim_span": f"{note} | model={ai.get('model')} | cited: " + "; ".join(f"[{c['id']}] {c['text'][:160]}" for c in cited)[:600],
            })
            return ev_id

        if ai.get("business_description"):
            bd = ai["business_description"]
            ev_id = ai_evidence(bd.get("fact_ids") or [], "business description")
            if ev_id:
                claims.append({"field": "ai_business_description", "value": bd["text"], "availability": "available", "confidence": 0.85, "evidence_ids": [ev_id]})
        for w_idx, wp in enumerate(ai.get("watch_points") or [], start=1):
            ev_id = ai_evidence(wp.get("fact_ids") or [], "watch point")
            if ev_id:
                claims.append({"field": f"ai_watch_point_{w_idx}", "value": wp["text"], "availability": "available", "confidence": 0.8, "evidence_ids": [ev_id]})
        for job in ai.get("jobs") or []:
            fid = str(job.get("fact_id") or "")
            if not fid.startswith("J") or not fid[1:].isdigit():
                continue
            ev_id = ai_evidence([fid], "job ad analysis")
            if not ev_id:
                continue
            j_idx = int(fid[1:])
            for key in ("role_category", "seniority", "employment", "work_mode", "one_line"):
                if job.get(key):
                    claims.append({"field": f"job_posting_{j_idx}_{key}", "value": job[key], "availability": "available", "confidence": 0.8, "evidence_ids": [ev_id]})
            if job.get("key_requirements"):
                claims.append({"field": f"job_posting_{j_idx}_key_requirements", "value": list(job["key_requirements"]), "availability": "available", "confidence": 0.8, "evidence_ids": [ev_id]})
        for n_idx, item in enumerate(ai.get("news") or [], start=1):
            ev_id = ai_evidence([str(item.get("fact_id") or "")], "news classification")
            if not ev_id:
                continue
            fact = ai_facts.get(str(item.get("fact_id")), {})
            claims.append({"field": f"ai_news_{n_idx}_item", "value": fact.get("text"), "availability": "available", "confidence": 0.9, "evidence_ids": [ev_id]})
            for key in ("event_type", "materiality", "one_line"):
                if item.get(key):
                    claims.append({"field": f"ai_news_{n_idx}_{key}", "value": item[key], "availability": "available", "confidence": 0.8, "evidence_ids": [ev_id]})

    # 6. Exact-entity external observations.  These are only published when
    # the observation has passed the shared identity, evidence, and rights
    # validator.  Provider output itself is never treated as a claim without
    # this boundary.
    external_observations = [
        item for item in (profile.get("_external_observations") or [])
        if isinstance(item, dict) and publishable_observation(item)
    ]
    for observation in external_observations:
        observation_id = str(observation.get("id") or "")
        evidence_id = f"ev-external-{observation_id}"
        _obs_kind = (str(observation.get("platform") or ""), str(observation.get("signal_type") or ""))
        if _obs_kind == ("company_site", "public_post") and str(observation.get("source_url") or "").rstrip("/") not in site_urls:
            continue  # same gates as site news
        if _obs_kind[1] == "profile_handle":
            _obs_url = str((observation.get("metrics") or {}).get("profile_url") or observation.get("profile_url") or "")
            if not social_handle(_obs_kind[0], _obs_url):
                continue  # a post, video or share link is not the company's profile
        metrics = observation.get("metrics") or {}
        ev_items.append({
            "id": evidence_id,
            "source_url": observation.get("source_url"),
            "source_class": observation.get("source_class") or "external_platform_api",
            "retrieved_at": observation.get("retrieved_at") or completed_at,
            "content_sha256": observation.get("content_sha256"),
            "claim_span": observation.get("evidence_span") or observation_id,
        })
        platform = str(observation.get("platform") or "external")
        signal = str(observation.get("signal_type") or "signal")
        if platform == "youtube" and signal == "profile_metrics":
            for key, field in (
                ("channel_title", "youtube_channel_title"),
                ("subscriber_count", "youtube_subscriber_count"),
                ("view_count", "youtube_view_count"),
                ("video_count", "youtube_video_count"),
            ):
                if metrics.get(key) is not None:
                    claims.append({"field": field, "value": metrics[key], "availability": "available", "confidence": 0.99, "evidence_ids": [evidence_id]})
        elif platform == "google_places" and signal == "place_summary":
            for key, field in (
                ("name", "google_places_name"),
                ("address", "google_places_address"),
                ("rating", "google_places_rating"),
                ("review_count", "google_places_review_count"),
                ("website", "google_places_website"),
                ("maps_url", "google_places_maps_url"),
            ):
                if metrics.get(key) is not None:
                    claims.append({"field": field, "value": metrics[key], "availability": "available", "confidence": 0.99, "evidence_ids": [evidence_id]})
        elif signal == "profile_handle":
            profile_url = metrics.get("profile_url") or observation.get("profile_url")
            if profile_url:
                check = assess_social_identity(profile, {"platform": platform, "url": profile_url})
                if check.get("publishable") and not any(c.get("field") == f"social_{platform}" for c in claims):
                    claims.append({
                        "field": f"social_{platform}",
                        "value": profile_url,
                        "availability": "available",
                        "confidence": 0.99,
                        "evidence_ids": [evidence_id],
                    })
        elif signal == "job_posting" and not _generic_job_title(metrics.get("title") or observation.get("evidence_span")):
            claims.append({
                "field": "job_posting_title",
                "value": metrics.get("title") or observation.get("evidence_span"),
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [evidence_id],
            })
        elif signal == "public_post":
            claims.append({
                "field": "external_activity_title",
                "value": observation.get("evidence_span"),
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [evidence_id],
            })
        else:
            claims.append({
                "field": f"external_{platform}_{signal}_observed",
                "value": observation.get("evidence_span") or metrics or True,
                "availability": "available",
                "confidence": 0.99,
                "evidence_ids": [evidence_id],
            })

    # 7. Grounded synthesis claims.  Synthesis is useful for recall, but its
    # prose and counters are not evidence by themselves.  Only source IDs
    # already materialized in this terminal envelope can authorize a value.
    available_evidence_ids = {item["id"] for item in ev_items}

    def ordered_ids(ids: Iterable[str]) -> list[str]:
        wanted = set(ids)
        return [item["id"] for item in ev_items if item["id"] in wanted]

    def append_grounded_claim(field: str, value: Any, evidence_ids: Iterable[str]) -> None:
        ids = ordered_ids(evidence_ids)
        if value is not None and ids:
            claims.append({
                "field": field,
                "value": value,
                "availability": "available",
                "confidence": 0.90,
                "evidence_ids": ids,
            })
        else:
            claims.append({
                "field": field,
                "value": None,
                "availability": "not_available",
                "confidence": 1.0,
                "evidence_ids": ids,
            })

    scope_value = _first_present(synthesis_value.get("business_scope"))
    scope_ids = _synthesis_field_evidence_ids(
        synthesis_value,
        ("business_scope_evidence_ids", "business_scope_source_ids"),
        available_evidence_ids,
    )
    website_support_record = evidence_dict.get("website") or {}
    website_support_value = website_support_record.get("value") or {}
    website_support_assessment = website_support_value.get("identity_assessment") or {}
    website_scope_ok = (
        website_support_record.get("status") == "available"
        and website_support_assessment.get("publishable")
        and website_support_value.get("description")
        and web_ev_id in available_evidence_ids
    )
    if synthesis_value.get("business_scope_basis") == "website":
        # The text is the verified site's own description: the registry record does not contain it.
        scope_ids = [web_ev_id] if website_scope_ok else []
    if not scope_ids and synthesis_value.get("business_scope_basis") != "website" and reg_record and reg_record.get("status") == "available" and nace_description is not None:
        scope_ids.append(reg_ev_id)
    if (
        not scope_ids
        and website_support_record.get("status") == "available"
        and website_support_assessment.get("publishable")
        and website_support_value.get("description")
        and web_ev_id in available_evidence_ids
    ):
        scope_ids.append(web_ev_id)
    append_grounded_claim("business_scope", scope_value, scope_ids)

    knowns = synthesis_value.get("knowns")
    unknowns = synthesis_value.get("unknowns")
    known_entry_ids: list[list[str]] = []
    if isinstance(knowns, list):
        known_entry_ids = [
            _synthesis_entry_evidence_ids(entry, available_evidence_ids)
            for entry in knowns
        ]
    backed_known_entry_ids = [entry_ids for entry_ids in known_entry_ids if entry_ids]
    knowns_supported = isinstance(knowns, list) and bool(backed_known_entry_ids)
    known_support_ids = ordered_ids(
        evidence_id
        for entry_ids in backed_known_entry_ids
        for evidence_id in entry_ids
    )
    explicit_summary_ids = _synthesis_field_evidence_ids(
        synthesis_value,
        ("executive_summary_evidence_ids", "executive_summary_source_ids", "summary_evidence_ids", "summary_source_ids"),
        available_evidence_ids,
    )
    summary_value = _first_present(
        synthesis_value.get("executive_summary"),
        synthesis_value.get("summary"),
    )
    summary_anchor = str(profile.get("name") or profile.get("organisation_number") or "").casefold()
    summary_is_entity_anchored = summary_anchor and summary_anchor in str(summary_value).casefold()
    if (
        not isinstance(knowns, list)
        and summary_value is not None
        and summary_is_entity_anchored
        and not explicit_summary_ids
    ):
        inferred_summary_ids = []
        if reg_record and reg_record.get("status") == "available" and (profile.get("name") or registry_value.get("navn")):
            inferred_summary_ids.append(reg_ev_id)
        if has_official_financial_records:
            inferred_summary_ids.append(fin_ev_id)
        if roles_record and roles_record.get("status") == "available" and role_items:
            inferred_summary_ids.append(roles_ev_id)
        if loc_record and loc_record.get("status") == "available" and loc_items:
            inferred_summary_ids.append(loc_ev_id)
        if ann_record and ann_record.get("status") == "available" and ann_events:
            inferred_summary_ids.append(ann_ev_id)
        if (
            website_support_record.get("status") == "available"
            and website_support_assessment.get("publishable")
            and website_support_value.get("description")
            and web_ev_id in available_evidence_ids
        ):
            inferred_summary_ids.append(web_ev_id)
        explicit_summary_ids.extend(inferred_summary_ids)
    summary_ids = ordered_ids([*explicit_summary_ids, *known_support_ids])
    append_grounded_claim("executive_summary", summary_value, summary_ids)

    if knowns_supported:
        append_grounded_claim("known_facts_count", len(backed_known_entry_ids), known_support_ids)
    else:
        append_grounded_claim("known_facts_count", None, ())

    unknown_entry_ids: list[list[str]] = []
    if isinstance(unknowns, list):
        unknown_entry_ids = [
            _synthesis_entry_evidence_ids(entry, available_evidence_ids)
            for entry in unknowns
        ]
    unknown_support_ids = ordered_ids(
        [
            *known_support_ids,
            *_explicit_synthesis_evidence_ids(synthesis_value, available_evidence_ids),
            *(
                evidence_id
                for entry_ids in unknown_entry_ids
                for evidence_id in entry_ids
            ),
        ]
    )
    unknowns_supported = isinstance(unknowns, list) and bool(unknown_support_ids)
    if unknowns_supported:
        append_grounded_claim("unreported_dimensions_count", len(unknowns), unknown_support_ids)
    else:
        append_grounded_claim("unreported_dimensions_count", None, ())

    ops = operations_metric or profile.get("run_metrics") or {
        "requests": 0,
        "runtime_ms": 0,
        "third_party_cost_usd": 0,
    }

    # Derive terminal status
    has_failed_claims = any(c.get("availability") == "failed" for c in claims)
    has_reg = reg_record and reg_record.get("status") == "available"
    terminal_status = "completed" if has_reg else ("not_found" if (reg_record or {}).get("status") in {"not_found", "404"} else "failed")

    envelope = {
        "organisation_number": org,
        "run": {
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "terminal_status": terminal_status,
        },
        "claims": claims,
        "evidence": ev_items,
        "changes": changes or profile.get("changes") or [],
        "errors": errors or profile.get("errors") or [],
        "operations": {
            "requests": int(ops.get("requests", 0)),
            "runtime_ms": int(ops.get("runtime_ms") or (ops.get("latencies_ms") and sum(ops["latencies_ms"])) or 0),
            "third_party_cost_usd": float(ops.get("third_party_cost_usd", 0.0)),
        },
        # Maintain evaluator compatibility
        "state": "complete" if terminal_status == "completed" else terminal_status,
        "profile": profile,
    }
    return envelope
