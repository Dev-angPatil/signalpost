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

# Norwegian e-commerce and privacy pages are legally required to name the
# business and usually carry the org number (ehandelsloven § 8, GDPR art. 13).
CONTACT_PATHS = ("/kontakt", "/kontakt-oss", "/om-oss", "/personvern", "/kjopsbetingelser", "/vilkar", "/contact")
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


_LEGAL_FORM_TOKENS = {"as", "asa", "ans", "da", "sa", "ba", "nuf", "enk", "iks", "ks", "brl", "sti", "og", "and"}
_NAME_SEP = r"(?:[^a-z0-9]{0,3}(?:(?:og|and)[^a-z0-9]{1,3})?)"


def legal_name_core_tokens(name: Any) -> list[str]:
    """Folded legal-name tokens in order, without legal-form words (keeps short tokens such as '10')."""
    folded = re.sub(r"\ba/s\b", " ", _fold(str(name or "")))
    return [t for t in re.split(r"[^a-z0-9]+", folded) if t and t not in _LEGAL_FORM_TOKENS]


def legal_name_phrase_pattern(name: Any, *, with_suffix: str | None = None) -> re.Pattern[str] | None:
    """Regex for the legal name as one contiguous phrase (tokens in order, adjacent).

    Scattered tokens are not enough: "Noramix Trade AS ... Liamyrane 13" must not
    prove "NORAMIX LIAMYRANE 10 AS" (a sister company at the same address).
    """
    tokens = legal_name_core_tokens(name)
    if not tokens or sum(len(t) for t in tokens) < 3:
        return None
    body = _NAME_SEP.join(re.escape(t) for t in tokens)
    if with_suffix:
        suffix = "a[^a-z0-9]?s" if with_suffix == "as" else re.escape(with_suffix)
        body += r"[^a-z0-9]{1,3}" + suffix
    return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])")


def find_identity_proof(
    text: str,
    profile: dict[str, Any],
    subunit_orgs: set[str] | None = None,
    *,
    max_other_orgs: int = 1,
) -> dict[str, Any] | None:
    """Exact-entity proof in page text, or None.

    max_other_orgs: how many *other* labelled organisation numbers the page may
    show. 1 by default (a parent or partner mention); callers pass 0 when the
    site did not even pass the legal-name check, so a third-party page that
    lists this company next to another entity is refused.
    """
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    own = {org} | set(subunit_orgs or set())
    if len(_labelled_org_numbers(text) - own) > max_other_orgs:
        # Directory, group or accountant page listing other entities: not exact proof.
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
    phrase = legal_name_phrase_pattern(profile.get("name"))
    if phrase is None:
        return None
    for street, postcode in registry_addresses(profile):
        f_street = _fold(street)
        idx = folded.find(f_street)
        if idx < 0:
            continue
        window = folded[max(0, idx - 200): idx + len(f_street) + 200]
        # Shared offices (c/o, managers, accountants, sister companies) carry many
        # companies' addresses: the full legal name must stand next to the address
        # as one phrase, and c/o lines are refused.
        if "c/o" in folded[max(0, idx - 60): idx] or not phrase.search(window):
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


def corroborate_site_identity(url: str, profile: dict[str, Any], subunit_orgs: set[str] | None = None, timeout: float = 3.5, max_other_orgs: int = 1) -> dict[str, Any]:
    """Fetch the homepage and a few contact pages; return proof or none."""
    started = time.monotonic()
    requests = 0
    base_domain = _registered_domain(url)
    parsed = urllib.parse.urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    robots: dict[str, Any] = {}
    misses = 0
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    own = {org} | set(subunit_orgs or set())
    other_orgs: set[str] = set()  # labelled org numbers of anyone else seen on these pages
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
            if misses >= 3:
                break
            continue
        other_orgs |= _labelled_org_numbers(text) - own
        proof = find_identity_proof(text, profile, subunit_orgs, max_other_orgs=max_other_orgs)
        if proof:
            return {
                "verified": True,
                "proof": {**proof, "source_url": final_url, "content_sha256": sha},
                "requests": requests,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "other_orgs": sorted(other_orgs),
            }
        if time.monotonic() - started > 8:
            break
    return {"verified": False, "proof": None, "requests": requests, "elapsed_ms": int((time.monotonic() - started) * 1000),
            "other_orgs": sorted(other_orgs)}


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
    """Multi-word legal name == domain label, plus independent corroboration on the site.

    Corroboration is either the exact legal name with its legal form ("Revetal
    Glasservice AS") or a registered place/postcode that is not itself part of
    the name ("Bergen" proves nothing for "Bergen Bil AS"). Pages that show any
    other labelled organisation number are refused.
    """
    label = _fold(host.split(":")[0]).removeprefix("www.").split(".")[0].replace("-", "")
    core = [t for t in core_tokens if t]
    if len(core) < 2 or label != "".join(core):
        return None
    if all(t in GENERIC_NAME_TOKENS or len(t) < 3 for t in core):
        return None
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    if _labelled_org_numbers(text) - {org}:
        return None
    folded = _fold(text)
    form = str(profile.get("legal_form") or "").casefold()
    if form in {"as", "asa"}:
        exact = legal_name_phrase_pattern(profile.get("name"), with_suffix=form)
        m = exact.search(folded) if exact else None
        if m:
            return {"type": "name_domain_and_exact_legal_name", "value": f"{label} / {profile.get('name')}",
                    "span": text[max(0, m.start() - 60): m.end() + 60].strip()}
    name_parts = set(legal_name_core_tokens(profile.get("name")))
    for loc in sorted(location_tokens(profile), key=len, reverse=True):
        loc_parts = set(re.split(r"[^a-z0-9]+", loc))
        if loc_parts & name_parts or loc.replace(" ", "") in label:
            continue  # the place is part of the name itself: not independent
        pattern = r"(?<![0-9a-z])" + re.escape(loc) + r"(?![0-9a-z])"
        m = re.search(pattern, folded)
        if m:
            return {"type": "name_domain_and_registered_location", "value": f"{label} / {loc}",
                    "span": text[max(0, m.start() - 60): m.end() + 60].strip()}
    return None


# Sources where the company itself put the URL on record in Brønnøysund: its
# registered homepage or the domain of its registered e-mail address.
REGISTRY_DECLARED_ORIGINS = {"registry_declared", "registry_email_domain"}

_NORDIC_ALT = str.maketrans({"ø": "oe", "Ø": "oe", "æ": "ae", "Æ": "ae", "å": "aa", "Å": "aa"})


def _domain_label(url: str) -> str:
    """The registrable label of a URL's host, folded to ASCII without hyphens (www.Kjøkken-AS.no -> kjokkenas)."""
    host = (urllib.parse.urlparse(url if "://" in str(url) else f"https://{url}").hostname or "").casefold()
    if not host:
        return ""
    try:
        host = ".".join(part.encode("ascii").decode("idna") if part.startswith("xn--") else part for part in host.split("."))
    except (UnicodeError, ValueError):
        pass
    registered = _registered_domain(f"https://{host}") or host.removeprefix("www.")
    return re.sub(r"[^a-z0-9]", "", _fold(registered.split(".")[0]))


def _name_spellings(name: Any) -> set[str]:
    """The whole legal name without its legal form, joined, in the usual ASCII spellings (ø -> o / oe)."""
    raw = str(name or "")
    spellings = set()
    for variant in (raw, raw.translate(_NORDIC_ALT)):
        tokens = legal_name_core_tokens(variant)
        if tokens:
            spellings.add("".join(tokens))
    return spellings


def domain_spells_legal_name(name: Any, url: str) -> bool:
    """True when the site's domain label is the company's whole legal name (legal form aside).

    "ATEA ASA" / atea.com and "A PLACE TO STAY STAVANGER AS" / aplacetostaystavanger.com
    qualify; "VEDAL INVESTOR AS" / vedal.no and "TGS ASA" / tgsnopec.com do not. A name
    made only of generic words ("BYGG AS") never qualifies, and nor does one shorter than
    four letters.
    """
    tokens = legal_name_core_tokens(name)
    if not tokens or all(t in GENERIC_NAME_TOKENS or len(t) < 3 for t in tokens):
        return False
    label = _domain_label(url)
    return len(label) >= 4 and label in _name_spellings(name)


_HOSTNAME_IN_TEXT = re.compile(r"(?i)\b(?:https?://)?(?:www\.)?[a-z0-9æøå-]+(?:\.[a-z0-9-]+)*\.(?:no|com|net|org|as|se|dk|eu|io|info|biz)\b\S*")


def names_company_in_text(name: Any, text: str) -> bool:
    """Every legal-name word (legal form aside) appears in the page's own words, hostnames removed.

    "www.noror.no" on a forwarding page does not name NORØR AS; "Altnet" in a heading names ALTNET AS.
    """
    tokens = [t for t in legal_name_core_tokens(name) if len(t) >= 2]
    if not tokens:
        return False
    words = set(re.split(r"[^a-z0-9]+", _fold(_HOSTNAME_IN_TEXT.sub(" ", text or ""))))
    words_alt = set(re.split(r"[^a-z0-9]+", _fold(_HOSTNAME_IN_TEXT.sub(" ", (text or "").translate(_NORDIC_ALT)))))
    return all(t in words for t in tokens) or all(t in words_alt for t in legal_name_core_tokens(str(name or "").translate(_NORDIC_ALT)) if len(t) >= 2)


def registry_domain_name_proof(
    profile: dict[str, Any],
    url: str,
    page_text: str,
    *,
    origin: str,
    conflict: bool,
    parked: bool,
) -> dict[str, Any] | None:
    """Proof for a site the company itself registered whose domain spells its whole legal name.

    Only for REGISTRY_DECLARED_ORIGINS: a guessed or searched domain that spells the name
    still needs the org number or registered address on the site. Refused when the page
    shows another organisation's number, when it is a parked or placeholder page, or
    when its labelled org numbers name anyone else.
    """
    if origin not in REGISTRY_DECLARED_ORIGINS or conflict or parked or not url:
        return None
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    if _labelled_org_numbers(page_text) - {org}:
        return None
    if not domain_spells_legal_name(profile.get("name"), url):
        return None
    if not names_company_in_text(profile.get("name"), page_text):
        return None  # empty, bot-walled or forwarding page: nothing on it says whose site it is
    return {
        "type": "registry_declared_domain_spells_legal_name",
        "origin": origin,
        "value": f"{_domain_label(url)} / {profile.get('name')}",
        "span": url,
    }
