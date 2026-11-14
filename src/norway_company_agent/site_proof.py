"""Hard identity proof for websites the registry does not declare.

A domain guessed from the company name (or proposed by a search engine) is only
a candidate. Name tokens on the page are not proof: "B. J. FISK AS" is not
fisk.no and "AKITA AS" is not akita.no. A non-registry site is promoted only
when the page itself carries the exact organisation number (or a registered
sub-unit number), or the registered street address together with its postcode.
"""
from __future__ import annotations

import hashlib
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from typing import Any

from .website import SAFE_OPENER, USER_AGENT, _registered_domain, _robots_allowed, assert_public_url

CONTACT_PATHS = ("/kontakt", "/kontakt-oss", "/om-oss", "/contact")
MAX_BYTES = 1_500_000


_NORDIC = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "å": "a", "Å": "a"})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").translate(_NORDIC)).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"\s+", " ", text)


def _visible_text(raw_html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;|&#160;", " ", html)
    return re.sub(r"\s+", " ", html)


def _org_pattern(org: str) -> re.Pattern[str]:
    # 968841084, 968 841 084, 968.841.084, NO 968841084 MVA
    return re.compile(r"(?<!\d)" + r"[\s. -]?".join(re.escape(d) for d in org) + r"(?!\d)")


def registry_addresses(profile: dict[str, Any]) -> list[tuple[str, str]]:
    """(street line, postcode) pairs from the registry and sub-units."""
    out: list[tuple[str, str]] = []
    reg = ((profile.get("evidence") or {}).get("registry") or {}).get("value") or {}
    candidates = [reg.get("forretningsadresse"), reg.get("postadresse"), reg.get("beliggenhetsadresse")]
    locs = (((profile.get("evidence") or {}).get("locations") or {}).get("value") or {}).get("locations") or []
    candidates.extend(loc.get("address") for loc in locs if isinstance(loc, dict))
    for addr in candidates:
        if not isinstance(addr, dict):
            continue
        postcode = str(addr.get("postnummer") or "").strip()
        for line in addr.get("adresse") or []:
            line = str(line or "").strip()
            # Require a street name plus a house number; PO boxes are too generic.
            if postcode and re.search(r"[A-Za-zÆØÅæøå]{3,}.*\d", line) and not re.match(r"(?i)(postboks|pb\.?|boks|c/o)\b", line):
                out.append((line, postcode))
    return out


_ORG_LABELLED = re.compile(r"(?i)(?:org\.?\s*(?:nr|no|nummer)?\.?|foretaksregisteret|mva)\D{0,12}((?:NO\s?)?\d{3}[\s.\u00a0]?\d{3}[\s.\u00a0]?\d{3})(?!\d)")


def _labelled_org_numbers(text: str) -> set[str]:
    return {re.sub(r"\D", "", m.group(1)) for m in _ORG_LABELLED.finditer(text or "")}


def find_identity_proof(text: str, profile: dict[str, Any], subunit_orgs: set[str] | None = None) -> dict[str, Any] | None:
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    own = {org} | set(subunit_orgs or set())
    if len(_labelled_org_numbers(text) - own) >= 2:
        # Directory, group or accountant page listing several other entities: not exact proof.
        return None
    if len(org) == 9:
        m = _org_pattern(org).search(text)
        if m:
            s = max(0, m.start() - 80)
            return {"type": "organisation_number", "value": org, "span": text[s:m.end() + 40].strip()}
    for sub in sorted(subunit_orgs or set()):
        m = _org_pattern(sub).search(text)
        if m:
            s = max(0, m.start() - 80)
            return {"type": "subunit_organisation_number", "value": sub, "span": text[s:m.end() + 40].strip()}
    folded = _fold(text)
    legal = {"as", "asa", "ans", "da", "sa", "ba", "nuf", "enk", "iks", "ks", "brl", "borettslag", "og", "and"}
    name_tokens = [t for t in re.split(r"[^a-z0-9]+", _fold(str(profile.get("name") or ""))) if len(t) >= 3 and t not in legal]
    for street, postcode in registry_addresses(profile):
        f_street = _fold(street)
        idx = folded.find(f_street)
        if idx < 0:
            continue
        window = folded[max(0, idx - 150): idx + len(f_street) + 150]
        # Shared offices (c/o, managers, accountants) carry many companies' addresses:
        # the company's own name must sit next to the address, and c/o lines are refused.
        if "c/o" in folded[max(0, idx - 60): idx] or not any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", window) for t in name_tokens):
            continue
        if re.search(r"(?<!\d)" + re.escape(postcode) + r"(?!\d)", folded[max(0, idx - 200): idx + 300]):
            return {"type": "registered_address", "value": f"{street}, {postcode}", "span": text[max(0, idx - 40): idx + len(street) + 60].strip()}
    return None


def _fetch_text(url: str, timeout: float) -> tuple[str | None, str | None, str | None, int]:
    try:
        assert_public_url(url)
    except ValueError:
        return None, None, None, 0
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with SAFE_OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES or "html" not in resp.headers.get("content-type", "").lower():
                return None, None, None, 1
            return _visible_text(raw.decode("utf-8", errors="replace")), resp.geturl(), hashlib.sha256(raw).hexdigest(), 1
    except Exception:
        return None, None, None, 1


def corroborate_site_identity(url: str, profile: dict[str, Any], subunit_orgs: set[str] | None = None, timeout: float = 3.5) -> dict[str, Any]:
    """Fetch the homepage and a few contact pages; return proof or none."""
    started = time.monotonic()
    requests = 0
    base_domain = _registered_domain(url)
    parsed = urllib.parse.urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    robots: dict[str, Any] = {}
    misses = 0
    targets = [url] + [root + p for p in CONTACT_PATHS]
    for target in targets:
        try:
            allowed = _robots_allowed(target, timeout, cache=robots)
        except Exception:
            break  # host does not resolve or is not public
        if not allowed:
            requests += 1
            continue
        text, final_url, sha, n = _fetch_text(target, timeout)
        requests += n
        if not text or not final_url or _registered_domain(final_url) != base_domain:
            misses += 1
            if misses >= 2:
                break
            continue
        proof = find_identity_proof(text, profile, subunit_orgs)
        if proof:
            return {
                "verified": True,
                "proof": {**proof, "source_url": final_url, "content_sha256": sha},
                "requests": requests,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        if time.monotonic() - started > 8:
            break
    return {"verified": False, "proof": None, "requests": requests, "elapsed_ms": int((time.monotonic() - started) * 1000)}


GENERIC_NAME_TOKENS = {
    "holding", "invest", "investering", "eiendom", "eiendommer", "drift", "utvikling", "service", "services",
    "bygg", "consult", "consulting", "gruppen", "group", "norge", "norway", "nordic", "partner", "partners",
    "capital", "management", "handel", "transport", "regnskap", "data", "media", "design", "with", "og",
}


def location_tokens(profile: dict[str, Any]) -> set[str]:
    reg = ((profile.get("evidence") or {}).get("registry") or {}).get("value") or {}
    out: set[str] = set()
    for key in ("forretningsadresse", "postadresse", "beliggenhetsadresse"):
        addr = reg.get(key)
        if isinstance(addr, dict):
            for k in ("kommune", "poststed"):
                v = _fold(str(addr.get(k) or "")).strip()
                if len(v) >= 3:
                    out.add(v)
            pc = str(addr.get("postnummer") or "").strip()
            if len(pc) == 4:
                out.add(pc)
    muni = _fold(str(profile.get("municipality") or "")).strip()
    if len(muni) >= 3:
        out.add(muni)
    return out


def name_domain_location_proof(profile: dict[str, Any], host: str, text: str, core_tokens: list[str]) -> dict[str, Any] | None:
    """Multi-word legal name == domain label, plus registered location on the site."""
    label = _fold(host.split(":")[0]).removeprefix("www.").split(".")[0].replace("-", "")
    core = [t for t in core_tokens if t]
    if len(core) < 2 or label != "".join(core):
        return None
    if all(t in GENERIC_NAME_TOKENS or len(t) < 3 for t in core):
        return None
    folded = _fold(text)
    for loc in sorted(location_tokens(profile), key=len, reverse=True):
        pattern = r"(?<![0-9a-z])" + re.escape(loc) + r"(?![0-9a-z])"
        m = re.search(pattern, folded)
        if m:
            return {"type": "name_domain_and_registered_location", "value": f"{label} / {loc}",
                    "span": text[max(0, m.start() - 60): m.end() + 60].strip()}
    return None
