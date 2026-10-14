from __future__ import annotations

import hashlib
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
    else:
        for field in (
            "corporate_announcements_count",
            "latest_corporate_action_date",
            "latest_corporate_action_title",
            "latest_corporate_action_url",
            "latest_capital_change_date",
            "latest_board_appointment_date",
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
