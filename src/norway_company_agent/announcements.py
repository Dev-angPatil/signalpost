from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from typing import Any

from bs4 import BeautifulSoup

CATEGORY_KEYWORDS = {
    "capital_change": [
        "kapitalforhøyelse",
        "kapitalnedsettelse",
        "innbetaling av aksjekapital",
        "erverv av egne aksjer",
        "aksjekapital",
        "aksjesplitt",
        "utbytte",
    ],
    "board_appointment": [
        "endring av styre",
        "nytt styre",
        "valg av styre",
        "styrets leder",
        "daglig leder",
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
        "godkjent årsregnskap",
        "årsregnskap",
        "endring av revisor",
        "endring av foretaksnavn",
        "endring av forretningsadresse",
        "fusjon",
        "fisjon",
        "omdanning",
    ],
}


def classify_notice(notice: dict[str, Any]) -> dict[str, Any]:
    title = str(notice.get("title") or "").strip()
    desc = str(notice.get("description") or "").strip()
    combined = f"{title} {desc}".casefold()
    category = "other"
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            category = cat
            break
    return {
        "category": category,
        "type": title or "Kunngjøring",
        "date": notice.get("date"),
        "description": desc,
        "source_url": notice.get("source_url", ""),
        "sha256": notice.get("sha256")
        or hashlib.sha256(f"{title}{notice.get('date')}".encode()).hexdigest(),
    }


def classify_all_notices(notices: list[dict[str, Any]]) -> dict[str, Any]:
    events = [classify_notice(n) for n in notices]
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
        if ev.get("date") and (cur_latest is None or ev["date"] > cur_latest):
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
    raw_payload = f"orgnr={clean_org}".encode()

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
                    "corporate_actions_count": 0,
                    "status": "failed",
                    "error": f"HTTP {status_code}",
                    "raw_html_sha256": hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else "",
                }
        except Exception as exc:
            return {
                "events": [],
                "corporate_actions_count": 0,
                "status": "failed",
                "error": str(exc),
                "raw_html_sha256": "",
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
                    "corporate_actions_count": 0,
                    "status": "failed",
                    "error": f"HTTP {status_code}",
                    "raw_html_sha256": hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else "",
                }
        except urllib.error.HTTPError as exc:
            return {
                "events": [],
                "corporate_actions_count": 0,
                "status": "failed",
                "error": f"HTTP {exc.code}",
                "raw_html_sha256": "",
            }
        except Exception as exc:
            return {
                "events": [],
                "corporate_actions_count": 0,
                "status": "failed",
                "error": str(exc),
                "raw_html_sha256": "",
            }

    decoded = raw_bytes.decode("windows-1252", errors="replace")
    content_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else ""

    soup = BeautifulSoup(decoded, "html.parser")
    table = soup.find("table")
    notices: list[dict[str, Any]] = []
    if table:
        for row in table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            raw_date = cols[0].get_text(strip=True)
            d_match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", raw_date)
            if not d_match:
                continue
            iso_date = f"{d_match.group(3)}-{d_match.group(2)}-{d_match.group(1)}"
            t_text = cols[1].get_text(strip=True)
            if not t_text:
                continue
            a_tag = cols[1].find("a")
            source_link = (
                a_tag["href"]
                if a_tag and a_tag.has_attr("href")
                else f"https://w2.brreg.no/kunngjoring/hent_nr.jsp?orgnr={clean_org}"
            )
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
        "requests": 1,
    }
