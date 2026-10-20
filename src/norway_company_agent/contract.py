from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable


_FINANCIAL_RATIO_FIELDS = (
    "operating_margin_pct",
    "net_margin_pct",
    "equity_ratio_pct",
    "debt_to_assets_pct",
    "multi_year_revenue_growth_pct",
)


def _first_present(*values: Any) -> Any:
    """Return the first value that is actually present, preserving falsey values."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


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
    org = str(profile.get("organisation_number") or "")
    evidence_dict = profile.get("evidence", {})
    ev_items: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []

    # 1. Official Registry Evidence & Claims
    reg_record = evidence_dict.get("registry") or evidence_dict.get("registry_live")
    reg_ev_id = "ev-registry"
    if reg_record and reg_record.get("status") == "available":
        reg_val = reg_record.get("value") or {}
        raw_name = profile.get("name") or reg_val.get("navn") or reg_val.get("name")
        ev_items.append({
            "id": reg_ev_id,
            "source_url": reg_record.get("source_url") or f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}",
            "source_class": "official_registry",
            "retrieved_at": reg_record.get("retrieved_at") or completed_at,
            "content_sha256": reg_record.get("content_sha256") or "",
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

    # 2. Official Financials Evidence & Claims
    fin_record = evidence_dict.get("financials")
    fin_ev_id = "ev-financials"
    if fin_record and fin_record.get("status") == "available":
        fin_val = fin_record.get("value") or {}
        records = fin_val.get("records") or []
        ev_items.append({
            "id": fin_ev_id,
            "source_url": fin_record.get("source_url") or f"https://data.brreg.no/regnskapsregisteret/regnskap/{org}",
            "source_class": "official_annual_accounts",
            "retrieved_at": fin_record.get("retrieved_at") or completed_at,
            "content_sha256": fin_record.get("content_sha256") or "",
            "claim_span": f"Annual account filing for {org}",
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
        claims.append({"field": "revenue", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})

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
            "content_sha256": roles_record.get("content_sha256") or "",
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
        claims.append({"field": "ceo", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "board_chair", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})

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
            "content_sha256": loc_record.get("content_sha256") or "",
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
        claims.append({"field": "registered_workplaces_count", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})

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
            "content_sha256": (
                ann_record.get("content_sha256")
                or ann_record.get("raw_html_sha256")
                or (ann_val.get("raw_html_sha256") if isinstance(ann_val, dict) else "")
                or ""
            ),
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
                "availability": "not_available",
                "confidence": 1.0,
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

    # Fallback to verified website news if RSS did not find matching articles
    web_record_check = evidence_dict.get("website") or {}
    web_val_check = web_record_check.get("value") or {}
    web_assessment = web_val_check.get("identity_assessment") or {}
    web_news_items = web_val_check.get("news_items") or []
    if (not latest_news or not latest_news.get("title")) and web_assessment.get("publishable") and web_news_items:
        latest_news = web_news_items[0]
        news_ev_id = "ev-website-news"
        news_record = {
            "status": "available",
            "source_url": latest_news.get("url") or web_record_check.get("source_url") or "",
            "source_class": "company_owned",
            "retrieved_at": web_record_check.get("retrieved_at") or completed_at,
            "content_sha256": web_val_check.get("content_sha256") or hashlib.sha256(b"").hexdigest(),
        }

    if (
        news_record
        and news_record.get("status") == "available"
        and latest_news
        and latest_news.get("title")
        and latest_news.get("url")
        and latest_news.get("date")
    ):
        source_url = news_record.get("source_url") or "https://news.google.com/rss"
        source_class = news_record.get("source_class") or "public_news"
        content_sha = (
            news_record.get("content_sha256")
            or (news_val.get("content_sha256") if isinstance(news_val, dict) else "")
            or ""
        )
        if not content_sha or len(content_sha) != 64:
            content_sha = hashlib.sha256(b"").hexdigest()

        ev_items.append({
            "id": news_ev_id,
            "source_url": source_url,
            "source_class": source_class,
            "retrieved_at": news_record.get("retrieved_at") or completed_at,
            "content_sha256": content_sha,
            "claim_span": latest_news.get("title") or "Company news query",
        })
        claims.append({
            "field": "latest_news_title",
            "value": latest_news.get("title"),
            "availability": "available",
            "confidence": 0.99,
            "evidence_ids": [news_ev_id],
        })
        claims.append({
            "field": "latest_news_date",
            "value": latest_news.get("date"),
            "availability": "available",
            "confidence": 0.99,
            "evidence_ids": [news_ev_id],
        })
        claims.append({
            "field": "latest_news_url",
            "value": latest_news.get("url"),
            "availability": "available",
            "confidence": 0.99,
            "evidence_ids": [news_ev_id],
        })
    else:
        for field in ("latest_news_title", "latest_news_date", "latest_news_url"):
            claims.append({
                "field": field,
                "value": None,
                "availability": "not_available",
                "confidence": 1.0,
                "evidence_ids": [],
            })

    # 5. Website & Company-Owned Footprint
    web_record = evidence_dict.get("website")
    web_ev_id = "ev-website"
    if web_record:
        status = web_record.get("status")
        web_val = web_record.get("value") or {}
        site_url = profile.get("website") or web_record.get("source_url")
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
                "content_sha256": web_record.get("content_sha256") or "",
                "claim_span": str(web_val.get("title") or site_url)[:200],
            })

            # 5b. Secondary Subpages Evidence Records
            contact_ev_id = web_ev_id
            career_ev_id = web_ev_id
            for idx, page in enumerate(web_val.get("pages") or []):
                p_url = page.get("url") or ""
                p_sha = page.get("content_sha256") or ""
                if not p_url or p_url.rstrip("/") == site_url.rstrip("/") or not p_sha:
                    continue
                p_lower = (p_url + " " + (page.get("title") or "")).lower()
                p_span = str(page.get("main_text_excerpt") or page.get("title") or p_url)[:200]
                if any(t in p_lower for t in ("kontakt", "contact", "lokasjon", "location")):
                    sub_id = "ev-website-contact"
                    contact_ev_id = sub_id
                elif any(t in p_lower for t in ("karriere", "career", "stilling", "job")):
                    sub_id = "ev-website-career"
                    career_ev_id = sub_id
                elif any(t in p_lower for t in ("om-oss", "om_oss", "about")):
                    sub_id = "ev-website-about"
                else:
                    sub_id = f"ev-website-sub-{idx}"
                ev_items.append({
                    "id": sub_id,
                    "source_url": p_url,
                    "source_class": "company_owned",
                    "retrieved_at": web_record.get("retrieved_at") or completed_at,
                    "content_sha256": p_sha,
                    "claim_span": p_span,
                })

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

            positions_val = raw_hiring.get("open_positions") if isinstance(raw_hiring, dict) and "open_positions" in raw_hiring else None
            if positions_val is None and "open_positions" in web_val:
                positions_val = web_val.get("open_positions")

            has_hiring_data = (career_url_val is not None) or (hiring_active_val is not None) or (open_count_val is not None) or (positions_val is not None) or (career_ev_id != web_ev_id)

            if is_publishable:
                # 1. official_website
                claims.append({
                    "field": "official_website",
                    "value": site_url,
                    "availability": "available",
                    "confidence": 0.95,
                    "evidence_ids": [web_ev_id],
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

                # 4. contact_email
                if raw_emails:
                    email_val = raw_emails if isinstance(raw_emails, (str, list)) else str(raw_emails)
                    claims.append({
                        "field": "contact_email",
                        "value": email_val,
                        "availability": "available",
                        "confidence": 0.95,
                        "evidence_ids": [contact_ev_id],
                    })
                elif contact_blocked:
                    claims.append({"field": "contact_email", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                else:
                    claims.append({"field": "contact_email", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [contact_ev_id]})

                # 5. contact_phone
                if raw_phones:
                    phone_val = raw_phones if isinstance(raw_phones, (str, list)) else str(raw_phones)
                    claims.append({
                        "field": "contact_phone",
                        "value": phone_val,
                        "availability": "available",
                        "confidence": 0.95,
                        "evidence_ids": [contact_ev_id],
                    })
                elif contact_blocked:
                    claims.append({"field": "contact_phone", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                else:
                    claims.append({"field": "contact_phone", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [contact_ev_id]})

                # 6. contact_address
                if raw_addresses:
                    addr_val = raw_addresses if isinstance(raw_addresses, (str, list)) else str(raw_addresses)
                    claims.append({
                        "field": "contact_address",
                        "value": addr_val,
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [contact_ev_id],
                    })
                elif contact_blocked:
                    claims.append({"field": "contact_address", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                else:
                    claims.append({"field": "contact_address", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [contact_ev_id]})

                # 7. career_url
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
                if career_blocked:
                    claims.append({"field": "hiring_active", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
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
                elif open_count_val is not None:
                    claims.append({
                        "field": "open_positions_count",
                        "value": int(open_count_val),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [career_ev_id],
                    })
                elif positions_val is not None:
                    claims.append({
                        "field": "open_positions_count",
                        "value": len(positions_val),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [career_ev_id],
                    })
                else:
                    claims.append({"field": "open_positions_count", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [web_ev_id]})

                # 10. open_positions
                if career_blocked:
                    claims.append({"field": "open_positions", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
                elif positions_val is not None:
                    claims.append({
                        "field": "open_positions",
                        "value": list(positions_val),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [career_ev_id],
                    })
                else:
                    claims.append({"field": "open_positions", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": [web_ev_id]})

                # Social profiles
                socials = web_val.get("social_links") or []
                for s in socials:
                    platform = s.get("platform")
                    url = s.get("url")
                    if platform and url:
                        claims.append({
                            "field": f"social_{platform}",
                            "value": url,
                            "availability": "available",
                            "confidence": 0.90,
                            "evidence_ids": [web_ev_id],
                        })
            else:
                # Quarantined due to uncertain identity (Zero Wrong-Company Guarantee)
                claims.append({
                    "field": "official_website",
                    "value": site_url,
                    "availability": "ambiguous",
                    "confidence": gate_score,
                    "evidence_ids": [web_ev_id],
                })
                claims.append({
                    "field": "website_description",
                    "value": web_val.get("description") if web_val.get("description") else None,
                    "availability": "ambiguous" if web_val.get("description") else "not_available",
                    "confidence": gate_score if web_val.get("description") else 1.0,
                    "evidence_ids": [web_ev_id] if web_val.get("description") else [],
                })
                claims.append({
                    "field": "contact_email",
                    "value": raw_emails if raw_emails else None,
                    "availability": "ambiguous" if raw_emails else "not_available",
                    "confidence": gate_score if raw_emails else 1.0,
                    "evidence_ids": [contact_ev_id] if raw_emails else [],
                })
                claims.append({
                    "field": "contact_phone",
                    "value": raw_phones if raw_phones else None,
                    "availability": "ambiguous" if raw_phones else "not_available",
                    "confidence": gate_score if raw_phones else 1.0,
                    "evidence_ids": [contact_ev_id] if raw_phones else [],
                })
                claims.append({
                    "field": "contact_address",
                    "value": raw_addresses if raw_addresses else None,
                    "availability": "ambiguous" if raw_addresses else "not_available",
                    "confidence": gate_score if raw_addresses else 1.0,
                    "evidence_ids": [contact_ev_id] if raw_addresses else [],
                })
                claims.append({
                    "field": "career_url",
                    "value": career_url_val if career_url_val else None,
                    "availability": "ambiguous" if career_url_val else "not_available",
                    "confidence": gate_score if career_url_val else 1.0,
                    "evidence_ids": [career_ev_id] if career_url_val else [],
                })
                claims.append({
                    "field": "hiring_active",
                    "value": hiring_active_val if hiring_active_val is not None else None,
                    "availability": "ambiguous" if hiring_active_val is not None else "not_available",
                    "confidence": gate_score if hiring_active_val is not None else 1.0,
                    "evidence_ids": [career_ev_id] if hiring_active_val is not None else [],
                })
                claims.append({
                    "field": "open_positions_count",
                    "value": open_count_val if open_count_val is not None else None,
                    "availability": "ambiguous" if open_count_val is not None else "not_available",
                    "confidence": gate_score if open_count_val is not None else 1.0,
                    "evidence_ids": [career_ev_id] if open_count_val is not None else [],
                })
                claims.append({
                    "field": "open_positions",
                    "value": positions_val if positions_val is not None else None,
                    "availability": "ambiguous" if positions_val is not None else "not_available",
                    "confidence": gate_score if positions_val is not None else 1.0,
                    "evidence_ids": [career_ev_id] if positions_val is not None else [],
                })
        elif status in {"blocked", "blocked_robots", "blocked_policy"}:
            claims.append({"field": "official_website", "value": site_url, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "website_description", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "contact_email", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "contact_phone", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "contact_address", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "career_url", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "hiring_active", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "open_positions_count", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
            claims.append({"field": "open_positions", "value": None, "availability": "blocked", "confidence": 0.0, "evidence_ids": []})
        else:
            claims.append({"field": "official_website", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            claims.append({"field": "website_description", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            claims.append({"field": "contact_email", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            claims.append({"field": "contact_phone", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            claims.append({"field": "contact_address", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            claims.append({"field": "career_url", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            claims.append({"field": "hiring_active", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            claims.append({"field": "open_positions_count", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
            claims.append({"field": "open_positions", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
    else:
        claims.append({"field": "official_website", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "website_description", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "contact_email", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "contact_phone", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "contact_address", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "career_url", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "hiring_active", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "open_positions_count", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})
        claims.append({"field": "open_positions", "value": None, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})

    # 6. Grounded synthesis claims.  Synthesis is useful for recall, but its
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
    if not scope_ids and reg_record and reg_record.get("status") == "available" and nace_description is not None:
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
