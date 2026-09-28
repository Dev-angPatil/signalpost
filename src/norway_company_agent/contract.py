from __future__ import annotations

from typing import Any, Iterable


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
        chair_name = next((r.get("name") for r in role_items if "styrets leder" in str(r.get("role", "")).lower() and not r.get("inactive")), None)
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
        claims.append({"field": "registered_workplaces_count", "value": 0, "availability": "not_available", "confidence": 1.0, "evidence_ids": []})

    # 5. Website & Company-Owned Footprint
    web_record = evidence_dict.get("website")
    web_ev_id = "ev-website"
    if web_record:
        status = web_record.get("status")
        web_val = web_record.get("value") or {}
        site_url = web_record.get("source_url") or profile.get("website")
        assessment = web_val.get("identity_assessment") or {}
        is_publishable = bool(assessment.get("publishable"))

        if status == "available" and site_url:
            ev_items.append({
                "id": web_ev_id,
                "source_url": site_url,
                "source_class": "company_owned",
                "retrieved_at": web_record.get("retrieved_at") or completed_at,
                "content_sha256": web_record.get("content_sha256") or "",
                "claim_span": str(web_val.get("title") or site_url)[:200],
            })
            if is_publishable:
                claims.append({
                    "field": "official_website",
                    "value": site_url,
                    "availability": "available",
                    "confidence": 0.95,
                    "evidence_ids": [web_ev_id],
                })
                if web_val.get("title"):
                    claims.append({
                        "field": "website_title",
                        "value": web_val.get("title"),
                        "availability": "available",
                        "confidence": 0.95,
                        "evidence_ids": [web_ev_id],
                    })
                if web_val.get("description"):
                    claims.append({
                        "field": "website_description",
                        "value": web_val.get("description"),
                        "availability": "available",
                        "confidence": 0.90,
                        "evidence_ids": [web_ev_id],
                    })
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
                # Quarantined due to uncertain identity
                claims.append({
                    "field": "official_website",
                    "value": site_url,
                    "availability": "ambiguous",
                    "confidence": float(assessment.get("score") or 0.3),
                    "evidence_ids": [web_ev_id],
                })
        elif status in {"blocked", "blocked_robots", "blocked_policy"}:
            claims.append({
                "field": "official_website",
                "value": site_url,
                "availability": "blocked",
                "confidence": 0.0,
                "evidence_ids": [],
            })
        else:
            claims.append({
                "field": "official_website",
                "value": None,
                "availability": "not_available",
                "confidence": 1.0,
                "evidence_ids": [],
            })
    else:
        claims.append({
            "field": "official_website",
            "value": None,
            "availability": "not_available",
            "confidence": 1.0,
            "evidence_ids": [],
        })

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
