from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from typing import Any

from bs4 import BeautifulSoup

CATEGORY_KEYWORDS = {
    "capital_change": [
        "kapitalnedsettelse",
        "kapitalforhøyelse",
        "innbetaling av aksjekapital",
        "erverv av egne aksjer",
        "aksjekapital",
        "aksjesplitt",
        "utbytte",
        "kapital",
    ],
    "board_appointment": [
        "styrets sammensetning",
        "endring av styre",
        "nytt styre",
        "valg av styre",
        "styrets leder",
        "daglig leder",
        "revisor",
        "fratreden",
        "styre",
    ],
    "insolvency": [
        "konkursåpning",
        "avslutning av konkursbehandling",
        "innstilling av bobehandling",
        "tvangsavvikling",
        "oppløsning",
        "sletting",
        "gjeldsforhandling",
        "rekonstruksjon",
        "konkurs",
    ],
    "legal_filing": [
        "vedtektsdato",
        "endring av vedtekter",
        "vedtekter",
        "godkjent årsregnskap",
        "årsregnskap",
        "endring av foretaksnavn",
        "endring av forretningsadresse",
        "fusjon",
        "fisjon",
        "omdanning",
    ],
}


def classify_notice(notice: dict[str, Any]) -> dict[str, Any]:
    title = str(notice.get("title") or notice.get("type") or "").strip()
    desc = str(notice.get("description") or "").strip()
    combined = re.sub(r"\s+", " ", f"{title} {desc}".replace("\xa0", " ")).strip().casefold()
    category = "other"
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            category = cat
            break
    source_url = str(notice.get("source_url") or "")
    if source_url.startswith("/"):
        source_url = f"https://w2.brreg.no{source_url}"
    elif source_url and not source_url.startswith("http"):
        source_url = f"https://w2.brreg.no/kunngjoring/{source_url.lstrip('/')}"

    return {
        "title": title or "Kunngjøring",
        "type": title or "Kunngjøring",
        "category": category,
        "date": notice.get("date"),
        "description": desc,
        "source_url": source_url,
        "sha256": notice.get("sha256")
        or hashlib.sha256(f"{title}{notice.get('date')}".encode()).hexdigest(),
    }


def classify_all_notices(notices: list[dict[str, Any]]) -> dict[str, Any]:
    events = [classify_notice(n) for n in notices]
    events.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    categories: dict[str, dict[str, Any]] = {
        "capital_change": {"count": 0, "latest_date": None},
        "board_appointment": {"count": 0, "latest_date": None},
        "legal_filing": {"count": 0, "latest_date": None},
        "insolvency": {"count": 0, "latest_date": None},
        "other": {"count": 0, "latest_date": None},
    }
    for ev in events:
        cat = ev["category"]
        if cat not in categories:
            categories[cat] = {"count": 0, "latest_date": None}
        categories[cat]["count"] += 1
        cur_latest = categories[cat]["latest_date"]
        if ev.get("date") and (cur_latest is None or str(ev["date"]) > str(cur_latest)):
            categories[cat]["latest_date"] = ev["date"]
    return {
        "events": events,
        "categories": categories,
        "corporate_actions_count": len(events),
    }


def fetch_announcements(
    orgnr: str,
    http_fetcher: Any = None,
    timeout_sec: float = 10.0,
) -> dict[str, Any]:
    clean_org = re.sub(r"\D", "", str(orgnr or ""))
    url = "https://w2.brreg.no/kunngjoring/hent.jsp"
    canonical_source_url = f"https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr={clean_org}"
    raw_payload = f"orgnr={clean_org}".encode()

    empty_categories: dict[str, dict[str, Any]] = {
        "capital_change": {"count": 0, "latest_date": None},
        "board_appointment": {"count": 0, "latest_date": None},
        "legal_filing": {"count": 0, "latest_date": None},
        "insolvency": {"count": 0, "latest_date": None},
        "other": {"count": 0, "latest_date": None},
    }

    raw_bytes = b""
    if http_fetcher is not None:
        try:
            try:
                res = http_fetcher(url, raw_payload)
            except TypeError:
                res = http_fetcher(url, data=raw_payload, timeout=timeout_sec)

            if isinstance(res, (bytes, bytearray)):
                raw_bytes = bytes(res)
                status_code = 200
            else:
                raw_bytes = getattr(res, "content", getattr(res, "body", b"")) or b""
                status_code = getattr(res, "status_code", getattr(res, "status", 200))

            if status_code >= 400:
                return {
                    "events": [],
                    "categories": empty_categories,
                    "corporate_actions_count": 0,
                    "status": "failed",
                    "error": f"HTTP {status_code}",
                    "raw_html_sha256": hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else "",
                    "source_url": canonical_source_url,
                }
        except Exception as exc:
            return {
                "events": [],
                "categories": empty_categories,
                "corporate_actions_count": 0,
                "status": "failed",
                "error": str(exc),
                "raw_html_sha256": "",
                "source_url": canonical_source_url,
            }
    else:
        try:
            req = urllib.request.Request(
                url,
                data=raw_payload,
                headers={
                    "User-Agent": "Signalpost/1.0 (+https://github.com/builderr-ai)",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                raw_bytes = resp.read()
                status_code = resp.status
            if status_code >= 400:
                return {
                    "events": [],
                    "categories": empty_categories,
                    "corporate_actions_count": 0,
                    "status": "failed",
                    "error": f"HTTP {status_code}",
                    "raw_html_sha256": hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else "",
                    "source_url": canonical_source_url,
                }
        except urllib.error.HTTPError as exc:
            return {
                "events": [],
                "categories": empty_categories,
                "corporate_actions_count": 0,
                "status": "failed",
                "error": f"HTTP {exc.code}",
                "raw_html_sha256": "",
                "source_url": canonical_source_url,
            }
        except Exception as exc:
            return {
                "events": [],
                "categories": empty_categories,
                "corporate_actions_count": 0,
                "status": "failed",
                "error": str(exc),
                "raw_html_sha256": "",
                "source_url": canonical_source_url,
            }

    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        decoded = raw_bytes.decode("windows-1252", errors="replace")
    content_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else ""

    soup = BeautifulSoup(decoded, "html.parser")
    notices: list[dict[str, Any]] = []
    for row in soup.find_all("tr"):
        if row.find("table"):
            continue
        cols = row.find_all("td", recursive=False)
        if len(cols) < 2:
            continue

        date_idx = None
        iso_date = None
        for idx, col in enumerate(cols):
            txt = col.get_text(strip=True)
            d_match = re.search(r"(\d{2})[\s\xa0]*\.[\s\xa0]*(\d{2})[\s\xa0]*\.[\s\xa0]*(\d{4})", txt)
            if d_match:
                date_idx = idx
                iso_date = f"{d_match.group(3)}-{d_match.group(2)}-{d_match.group(1)}"
                break

        if date_idx is None or not iso_date:
            continue

        t_text = ""
        a_tag = None
        for col in cols[date_idx + 1:]:
            txt = col.get_text(strip=True)
            if txt:
                t_text = txt
                a_tag = col.find("a")
                break

        if not t_text:
            continue

        raw_link = a_tag["href"].strip() if a_tag and a_tag.has_attr("href") else ""
        if raw_link:
            if raw_link.startswith("http://") or raw_link.startswith("https://"):
                source_link = raw_link
            elif raw_link.startswith("/"):
                source_link = f"https://w2.brreg.no{raw_link}"
            else:
                source_link = f"https://w2.brreg.no/kunngjoring/{raw_link.lstrip('/')}"
        else:
            source_link = canonical_source_url

        notices.append(
            {
                "title": t_text,
                "date": iso_date,
                "description": t_text,
                "source_url": source_link,
                "sha256": hashlib.sha256(f"{iso_date}:{t_text}".encode()).hexdigest(),
            }
        )

    classified = classify_all_notices(notices)
    return {
        "events": classified["events"],
        "categories": classified["categories"],
        "corporate_actions_count": len(classified["events"]),
        "status": "available",
        "raw_html_sha256": content_hash,
        "source_url": canonical_source_url,
        "requests": 1,
    }
