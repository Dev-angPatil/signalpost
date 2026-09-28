from __future__ import annotations

import html
import json
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup
import extruct
import tldextract
import trafilatura

from .evidence import evidence

USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"
SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
}
PRIORITY_CATEGORIES: dict[str, tuple[str, ...]] = {
    "about": (
        "om-oss", "om_oss", "om-selskapet", "om_selskapet", "about-us", "about",
        "hvem-er-vi", "hvem_er_vi", "/om/", "/om",
    ),
    "contact": (
        "kontakt-oss", "kontakt_oss", "kontakt", "contact-us", "contact",
        "kontaktinformasjon", "lokasjoner", "locations", "finn-oss",
    ),
    "careers": (
        "karriere", "career", "careers", "ledige-stillinger", "ledigestillinger",
        "jobb", "jobs", "work-with-us", "work_with_us", "stillinger", "jobber",
    ),
    "investor": (
        "investor-relations", "investor_relations", "investor", "investors",
        "finansiell-informasjon", "aksjonar", "aksjeinformasjon", "ir",
    ),
}

SECONDARY_TERMS: tuple[str, ...] = (
    "ledelse", "management", "team", "people", "styre", "board",
    "avdelinger", "butikker", "news", "press", "aktuelt", "nyheter",
)

PRIORITY_TERMS: tuple[str, ...] = tuple(
    term for cat in PRIORITY_CATEGORIES.values() for term in cat
) + SECONDARY_TERMS

ASSET_EXTENSIONS: set[str] = {
    "png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "tiff", "tif",
    "css", "js", "map", "woff", "woff2", "ttf", "eot", "mp4", "webm", "pdf",
    "zip", "json", "xml", "gz", "tar",
}

DISALLOWED_EMAIL_DOMAINS: set[str] = {
    "example.com", "example.org", "example.net", "example.test", "domain.test",
    "domain.com", "yourdomain.com", "yoursite.com", "sentry.io", "wixpress.com",
    "schema.org", "cloudflare.com", "wordpress.org", "gravatar.com", "doubleclick.net",
}

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

PHONE_LABEL_REGEX = re.compile(
    r"(?:tlf|telefon|phone|ring|mobil|sentralbord|kontaktoss|call)\s*[:.]?\s*(\+?\(?00?47\)?[\s.-]*)?([2-9](?:[\s.-]*\d){7})\b",
    re.IGNORECASE,
)

PHONE_GENERAL_REGEX = re.compile(
    r"(?:(?:\+|00)47[\s.-]*)?([2-9]\d{1}[\s.-]*\d{2}[\s.-]*\d{2}[\s.-]*\d{2}|[2-9]\d{2}[\s.-]*\d{2}[\s.-]*\d{3})\b"
)

STREET_ADDRESS_REGEX = re.compile(
    r"\b([A-ZÆØÅ][a-zæøåA-ZÆØÅ0-9.\s-]{2,40}\s+\d{1,4}\s*[A-Za-z]?)\s*,?\s*([0-9]{4})\s+([A-ZÆØÅ][a-zæøåA-ZÆØÅ]{1,15}(?:\s+[A-ZÆØÅ][a-zæøåA-ZÆØÅ]{1,15})?)\b"
)

POSTBOKS_REGEX = re.compile(
    r"\b(Postboks|Pb\.?|P\.B\.)\s+(\d+)\s*(?:[A-ZÆØÅa-zæøå\s-]*?),?\s*([0-9]{4})\s+([A-ZÆØÅ][a-zæøåA-ZÆØÅ]{1,15}(?:\s+[A-ZÆØÅ][a-zæøåA-ZÆØÅ]{1,15})?)\b",
    re.IGNORECASE,
)

CAREER_PORTAL_DOMAINS: dict[str, str] = {
    "webcruiter.com": "webcruiter",
    "webcruiter.no": "webcruiter",
    "finn.no": "finn",
    "jobbnorge.no": "jobbnorge",
    "teamtailor.com": "teamtailor",
    "reachmee.com": "reachmee",
    "varbi.com": "varbi",
    "recman.no": "recman",
    "recman.io": "recman",
    "workbuster.com": "workbuster",
    "easycruit.com": "easycruit",
    "talentech.com": "talentech",
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "myworkdayjobs.com": "workday",
    "emply.com": "emply",
    "jobylon.com": "jobylon",
    "personio.de": "personio",
    "personio.com": "personio",
}

CAREER_TERMS: tuple[str, ...] = (
    "karriere", "career", "careers", "ledige-stillinger", "ledigestillinger",
    "stillinger", "jobb", "jobs", "vacancies", "work-with-us", "join-us",
)

JOB_TITLE_IGNORE: set[str] = {
    "karriere", "career", "careers", "ledige stillinger", "open positions",
    "jobb hos oss", "work with us", "bli en av oss", "bli med på laget",
    "hjem", "home", "om oss", "about", "about us", "kontakt", "contact",
    "kontakt oss", "contact us", "aktuelt", "nyheter", "news", "press",
    "personvern", "privacy", "cookies", "søk her", "søk på stillingen",
    "apply", "apply now", "read more", "les mer", "se stilling",
    "se hele stillingen", "se alle stillinger", "vis mer", "send søknad",
    "del", "share", "tilbake", "back", "mer informasjon", "more info",
    "filter", "alle", "all", "søk", "search", "avdelinger", "lokasjoner",
    "locations", "menyen", "meny", "menu", "close", "lukk",
}


def assert_public_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local hosts are blocked")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("Hostname did not resolve") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local, multicast, and reserved addresses are blocked")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SAFE_OPENER = urllib.request.build_opener(SafeRedirectHandler())


def normalize_homepage(value: str | None) -> str | None:
    value = str(value or "").strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def _registered_domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    ext = tldextract.extract(parsed.hostname or "")
    return ext.top_domain_under_public_suffix


_ROBOTS_CACHE: dict[str, urllib.robotparser.RobotFileParser] = {}


def _robots_allowed(url: str, timeout: float, cache: dict[str, urllib.robotparser.RobotFileParser] | None = None) -> bool:
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    active_cache = cache if cache is not None else _ROBOTS_CACHE
    if robots_url in active_cache:
        return active_cache[robots_url].can_fetch(USER_AGENT, url)
    assert_public_url(url)

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            parser.parse(response.read().decode("utf-8", errors="replace").splitlines())
        active_cache[robots_url] = parser
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        # An unavailable robots file is not permission to ignore explicit site terms; callers retain
        # the URL and can route uncertain domains to review. For this bounded homepage POC, allow one
        # ordinary GET when robots.txt is absent rather than crawl deeper.
        active_cache[robots_url] = parser
        return True


def _social_links(base_url: str, soup: BeautifulSoup) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    candidates = [str(node.get("href") or "") for node in soup.select("a[href]")]
    candidates.extend(str(node.get("data-href") or "") for node in soup.select("[data-href]"))
    candidates.extend(str(node.get("src") or "") for node in soup.select("iframe[src]"))
    for candidate in candidates:
        url = urllib.parse.urljoin(base_url, candidate)
        parsed_candidate = urllib.parse.urlparse(url)
        if (parsed_candidate.hostname or "").casefold().removeprefix("www.") == "facebook.com" and parsed_candidate.path.startswith("/plugins/"):
            embedded = urllib.parse.parse_qs(parsed_candidate.query).get("href", [])
            if embedded:
                url = embedded[0]
        normalized = normalize_social_url(url)
        if not normalized:
            continue
        found[(normalized["platform"], normalized["url"])] = normalized
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def structured_social_links(value: Any) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            same_as = node.get("sameAs")
            urls = same_as if isinstance(same_as, list) else [same_as]
            for raw in urls:
                if not isinstance(raw, str):
                    continue
                normalized = normalize_social_url(raw.strip())
                if normalized:
                    found[(normalized["platform"], normalized["url"])] = normalized
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def normalize_social_url(url: str) -> dict[str, str] | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    platform = next((label for domain, label in SOCIAL_HOSTS.items() if host == domain or host.endswith("." + domain)), None)
    if not platform:
        return None
    parts = [part.strip() for part in parsed.path.split("/") if part.strip()]
    lowered = [part.casefold() for part in parts]
    rejected_first = {
        "facebook": {"sharer", "sharer.php", "share.php", "dialog", "policy.php", "privacy", "events", "groups", "plugins"},
        "instagram": {"p", "reel", "reels", "stories", "explore"},
        "x": {"intent", "share", "home", "search", "i"},
    }
    if not parts or lowered[0] in rejected_first.get(platform, set()):
        return None
    if platform == "facebook" and lowered[0] == "profile.php":
        return None
    if platform == "linkedin" and (lowered[0] != "company" or len(parts) < 2):
        return None
    if platform == "youtube" and lowered[0] not in {"channel", "user", "c"} and not parts[0].startswith("@"):
        return None
    if host == "youtu.be":
        return None
    if platform == "tiktok" and not parts[0].startswith("@"):
        return None
    if platform == "x" and len(parts) != 1:
        return None
    canonical_host = {
        "linkedin": "linkedin.com",
        "facebook": "facebook.com",
        "instagram": "instagram.com",
        "x": "x.com",
        "youtube": "youtube.com",
        "tiktok": "tiktok.com",
    }[platform]
    if platform == "linkedin":
        parts = parts[:2]
    elif platform == "youtube":
        parts = parts[:1] if parts[0].startswith("@") else parts[:2]
    return {"platform": platform, "url": f"https://{canonical_host}/{'/'.join(parts)}"}


def clean_email(raw: str) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip().strip("<>\"'():;,. ")
    if "mailto:" in candidate.lower():
        candidate = candidate.split("mailto:")[-1].split("?")[0].strip()
    candidate = candidate.strip().strip("<>\"'():;,. ")
    if not candidate or "@" not in candidate:
        return None
    parts = candidate.split("@")
    if len(parts) != 2:
        return None
    local_part, domain_part = parts
    domain_part = domain_part.lower().rstrip(".")
    if not local_part or not domain_part:
        return None
    if ".." in domain_part or "." not in domain_part:
        return None
    tld = domain_part.split(".")[-1]
    if len(tld) < 2 or not tld.isalpha() or tld in ASSET_EXTENSIONS:
        return None
    if domain_part in DISALLOWED_EMAIL_DOMAINS:
        return None
    if local_part.lower() in {"name", "user", "email", "yourname", "username", "test"} and domain_part in {"domain.com", "example.com", "company.com"}:
        return None
    if "core-js" in candidate.lower() or "webpack" in candidate.lower():
        return None
    if not re.match(r"^[a-zA-Z0-9_.+-]+$", local_part):
        return None
    if not re.match(r"^[a-zA-Z0-9.-]+$", domain_part):
        return None
    return f"{local_part}@{domain_part}".lower()


def normalize_norwegian_phone(raw: str) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if "tel:" in cleaned.lower():
        cleaned = cleaned.split("tel:")[-1].split("?")[0].strip()
    digits_only = re.sub(r"[^\d+]", "", cleaned)
    if digits_only.startswith("+47"):
        digits = digits_only[3:]
    elif digits_only.startswith("0047"):
        digits = digits_only[4:]
    elif digits_only.startswith("47") and len(digits_only) == 10:
        digits = digits_only[2:]
    else:
        digits = digits_only.lstrip("+")
    if len(digits) != 8 or digits[0] not in "23456789":
        return None
    return f"+47{digits}"


def extract_contact_info(
    soup_or_text: Any = None,
    page_text: str | None = None,
    registered_domain: str | None = None,
    *,
    text: str | None = None,
    html_str: str | None = None,
) -> dict[str, list[str]]:
    import html as _html_mod

    if isinstance(soup_or_text, BeautifulSoup):
        soup = soup_or_text
        raw_text = page_text or text or (soup.get_text(" ", strip=True) if soup else "")
        domain = registered_domain
    elif isinstance(soup_or_text, str):
        domain = registered_domain
        if html_str is not None:
            raw_text = soup_or_text
            soup = BeautifulSoup(html_str, "html.parser")
        elif page_text is not None and ("<" in page_text or "class=" in page_text or "id=" in page_text):
            raw_text = soup_or_text
            soup = BeautifulSoup(page_text, "html.parser")
        else:
            raw_text = soup_or_text
            if "<" in soup_or_text and ">" in soup_or_text:
                soup = BeautifulSoup(soup_or_text, "html.parser")
            else:
                soup = BeautifulSoup("", "html.parser")
    else:
        domain = registered_domain
        raw_text = text or ""
        soup = BeautifulSoup(html_str or "", "html.parser") if html_str else BeautifulSoup("", "html.parser")

    decoded_text = _html_mod.unescape(raw_text or "")
    decoded_text = re.sub(r"\s*\[\s*at\s*\]\s*", "@", decoded_text, flags=re.I)
    decoded_text = re.sub(r"\s*\(\s*at\s*\)\s*", "@", decoded_text, flags=re.I)
    decoded_text = re.sub(r"&#64;", "@", decoded_text, flags=re.I)

    emails: list[str] = []
    phones: list[str] = []
    addresses: list[str] = []

    def _add_phone(candidate: str | None) -> None:
        if not candidate:
            return
        p = normalize_norwegian_phone(str(candidate))
        if not p:
            return
        core = p[3:]  # 8 national digits
        compact = f"+47{core}"
        spaced = f"+47 {core[:2]} {core[2:4]} {core[4:6]} {core[6:]}"
        if compact not in phones:
            phones.append(compact)
        if spaced not in phones:
            phones.append(spaced)

    # 1. Direct anchor tags (mailto: and tel:)
    for a in soup.select('a[href^="mailto:"], a[href^="MAILTO:"]'):
        c = clean_email(str(a.get("href") or ""))
        if c and c not in emails:
            emails.append(c)

    for a in soup.select('a[href^="tel:"], a[href^="TEL:"]'):
        _add_phone(str(a.get("href") or ""))

    # 2. Semantic microdata
    for el in soup.select('[itemprop="email"]'):
        c = clean_email(str(el.get("content") or el.get_text(" ", strip=True)))
        if c and c not in emails:
            emails.append(c)

    for el in soup.select('[itemprop="telephone"]'):
        _add_phone(str(el.get("content") or el.get_text(" ", strip=True)))

    for el in soup.select('[itemprop="address"]'):
        addr_text = " ".join(el.get_text(" ", strip=True).split())
        if addr_text and addr_text not in addresses:
            addresses.append(addr_text)

    # 3. <address> tags
    for addr_tag in soup.find_all("address"):
        addr_text = " ".join(addr_tag.get_text(" ", strip=True).split())
        if addr_text and any(c.isdigit() for c in addr_text) and addr_text not in addresses:
            addresses.append(addr_text)

    # 4. Footer & Contact Semantic Nodes
    contact_nodes = soup.select('footer, address, [class*="footer"], [class*="contact"], [id*="contact"], [id*="footer"]')
    contact_text = " ".join(n.get_text(" ", strip=True) for n in contact_nodes)
    search_corpus = f"{contact_text} {decoded_text}"

    for match in EMAIL_REGEX.finditer(search_corpus):
        c = clean_email(match.group(0))
        if c and c not in emails:
            emails.append(c)

    # Clean corpus for phone search: strip out 9-digit org numbers
    cleaned_for_phones = re.sub(r"(?i)(?:org\.?\s*nr|organisasjonsnummer|foretaksregisteret)\D*\d{9}", "", search_corpus)
    cleaned_for_phones = re.sub(r"(?i)(?:org\.?\s*nr|organisasjonsnummer|foretaksregisteret)\D*\d{3}\s*\d{3}\s*\d{3}", "", cleaned_for_phones)

    for match in PHONE_LABEL_REGEX.finditer(cleaned_for_phones):
        _add_phone(match.group(0))

    phone_matches = re.findall(r"(?:(?:\+|00)47\s*)?(?:[2-9]\d{1}\s*\d{2}\s*\d{2}\s*\d{2}|[2-9]\d{7})", cleaned_for_phones)
    for pm in phone_matches:
        _add_phone(pm)

    for match in STREET_ADDRESS_REGEX.finditer(search_corpus):
        addr = " ".join(match.group(0).split())
        if addr and addr not in addresses:
            addresses.append(addr)

    for match in POSTBOKS_REGEX.finditer(search_corpus):
        addr = " ".join(match.group(0).split())
        if addr and addr not in addresses:
            addresses.append(addr)

    if not addresses:
        addr_match = re.search(r"([A-ZÆØÅa-zæøå\s]+(?:gate|vei|veien|gata|gaten|plass|svingen|trappa)?\s+\d+[A-Za-z]?),?\s+(\d{4})\s+([A-ZÆØÅa-zæøå]+)", decoded_text)
        if addr_match:
            addresses.append(f"{addr_match.group(1).strip()}, {addr_match.group(2)} {addr_match.group(3).strip()}")

    if domain:
        emails.sort(key=lambda e: (0 if domain in e.split("@")[-1] else 1, e))

    return {
        "emails": emails[:10],
        "phones": phones[:20],
        "addresses": addresses[:10],
    }


def _extract_career_portals(base_url: str, soup: BeautifulSoup) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        raw_href = str(anchor.get("href") or "").strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            url = urllib.parse.urljoin(base_url, raw_href)
            parsed = urllib.parse.urlparse(url)
        except Exception:
            continue
        if parsed.scheme not in {"http", "https"}:
            continue
        host = (parsed.hostname or "").lower().removeprefix("www.")
        platform = None
        for domain, plat in CAREER_PORTAL_DOMAINS.items():
            if host == domain or host.endswith("." + domain):
                if plat == "finn" and not parsed.path.lower().startswith("/jobb"):
                    continue
                platform = plat
                break
        if not platform and (host == "linkedin.com" or host.endswith(".linkedin.com")):
            if parsed.path.lower().startswith("/jobs") or "/jobs/" in parsed.path.lower():
                platform = "linkedin_jobs"
        if platform:
            clean_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
            title = anchor.get_text(" ", strip=True)[:100]
            found[(platform, clean_url)] = {
                "platform": platform,
                "url": clean_url,
                "anchor_text": title,
            }
    return sorted(found.values(), key=lambda x: (x["platform"], x["url"]))


def _extract_job_vacancies(base_url: str, soup: BeautifulSoup, raw_html: str = "") -> tuple[list[str], int | None, bool | None]:
    titles: list[str] = []
    seen_titles: set[str] = set()

    def add_title(candidate: str) -> None:
        t = " ".join(candidate.split()).strip()
        if len(t) < 4 or len(t) > 120:
            return
        if not re.search(r"[a-zA-ZæøåÆØÅ]", t):
            return
        lower = t.casefold()
        if lower in JOB_TITLE_IGNORE:
            return
        if any(lower == ign or lower.startswith(ign + " ") or lower.endswith(" " + ign) for ign in JOB_TITLE_IGNORE if len(ign) > 5):
            return
        if re.search(r"^(?:https?://|www\.|\/)", t, re.I):
            return
        if lower not in seen_titles:
            seen_titles.add(lower)
            titles.append(t)

    # 1. Structured Schema.org JobPosting
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    types = item.get("@type")
                    type_set = set(types if isinstance(types, list) else [types])
                    if "JobPosting" in type_set:
                        job_title = item.get("title")
                        if isinstance(job_title, str):
                            add_title(job_title)
        except Exception:
            pass

    # 2. Targeted job listing CSS selectors
    job_selectors = [
        "[data-job-title]", "[data-job-name]",
        ".job-title", ".position-title", ".stilling-tittel", ".vacancy-title",
        ".job-listing__title", ".career-position__title", ".career-item__title",
        ".job-card h2", ".job-card h3", ".job-card h4", ".job-card a",
        ".job-item h2", ".job-item h3", ".job-item h4", ".job-item a",
        ".job-listing h2", ".job-listing h3", ".job-listing h4", ".job-listing a",
        ".career-position h2", ".career-position h3", ".career-position h4",
        "li.job h2", "li.job h3", "li.job a",
        "article.job h2", "article.job h3", "article.job a",
    ]
    for sel in job_selectors:
        for node in soup.select(sel):
            val = node.get("data-job-title") or node.get("data-job-name") or node.get_text(" ", strip=True)
            if isinstance(val, str) and val.strip():
                add_title(val)

    # 3. Anchors pointing to internal job sub-routes
    for a in soup.select("a[href]"):
        href = str(a.get("href") or "").strip()
        try:
            parsed_href = urllib.parse.urlparse(href)
        except Exception:
            continue
        path = parsed_href.path.casefold()
        if re.search(r"/(?:stilling|stillinger|ledige-stillinger|jobs|jobb)/[^/]+", path):
            anchor_text = a.get_text(" ", strip=True)
            if anchor_text:
                add_title(anchor_text)

    # 4. Text regex for explicit open position counts or negative counts
    page_text = soup.get_text(" ", strip=True)
    count: int | None = None
    hiring_active: bool | None = None

    negative_pattern = r"(?i)\b(?:ingen|for\s+tiden\s+ingen|har\s+ingen|currently\s+no|no\s+open)\s+(?:ledige\s+stillinger|stillinger|vacancies|positions)\b"
    if re.search(negative_pattern, page_text):
        count = 0
        hiring_active = False

    count_match = re.search(r"(?i)\b(\d+)\s+(?:ledige\s+stillinger|open\s+positions|vacancies|job\s+openings)\b", page_text)
    if count_match:
        parsed_count = int(count_match.group(1))
        if parsed_count > 0:
            count = parsed_count
            hiring_active = True
        elif parsed_count == 0:
            count = 0
            hiring_active = False

    if titles:
        hiring_active = True
        count = max(len(titles), count or 0)

    return titles, count, hiring_active


def _priority_links(base_url: str, soup: BeautifulSoup, limit: int = 4) -> list[str]:
    base = urllib.parse.urlparse(base_url)
    anchors = soup.select("a[href]")
    by_category: dict[str, list[tuple[int, int, str]]] = {cat: [] for cat in PRIORITY_CATEGORIES}
    other_candidates: list[tuple[int, int, str]] = []
    seen_urls: set[str] = set()

    for anchor in anchors:
        href = str(anchor.get("href") or "").strip()
        url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != base.netloc.lower():
            continue
        clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        if clean.rstrip("/") == base_url.rstrip("/"):
            continue
        if clean in seen_urls:
            continue

        path_text = parsed.path.casefold()
        anchor_text = anchor.get_text(" ", strip=True).casefold()
        haystack = path_text + " " + anchor_text

        matched_cat = None
        best_cat_rank = 999
        for cat, terms in PRIORITY_CATEGORIES.items():
            for idx, term in enumerate(terms):
                if term in haystack:
                    specificity = 0 if term in path_text else 1
                    rank = specificity * 100 + idx
                    if rank < best_cat_rank:
                        best_cat_rank = rank
                        matched_cat = cat

        if matched_cat:
            by_category[matched_cat].append((best_cat_rank, len(clean), clean))
            seen_urls.add(clean)
        else:
            best_other_rank = 999
            for idx, term in enumerate(SECONDARY_TERMS):
                if term in haystack:
                    best_other_rank = min(best_other_rank, idx)
            if best_other_rank < 999:
                other_candidates.append((best_other_rank, len(clean), clean))
                seen_urls.add(clean)

    chosen: list[str] = []
    # 1. Take top 1 candidate from each primary category (about, contact, careers, investor)
    for cat in ("about", "contact", "careers", "investor"):
        if by_category[cat]:
            by_category[cat].sort(key=lambda x: (x[0], x[1], x[2]))
            chosen.append(by_category[cat][0][2])
            if len(chosen) >= limit:
                return chosen

    # 2. Fill remaining slots from other category candidates and secondary terms
    remaining: list[tuple[int, int, str]] = []
    for cat in ("about", "contact", "careers", "investor"):
        if len(by_category[cat]) > 1:
            remaining.extend(by_category[cat][1:])
    remaining.extend(other_candidates)
    remaining.sort(key=lambda x: (x[0], x[1], x[2]))

    for item in remaining:
        if item[2] not in chosen:
            chosen.append(item[2])
            if len(chosen) >= limit:
                break

    return chosen[:limit]


def _fetch_secondary_page(
    url: str,
    *,
    homepage_domain: str,
    timeout: float,
    max_bytes: int,
    robots_cache: dict[str, urllib.robotparser.RobotFileParser] | None = None,
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, str]],
    int,
    int,
    int,
    str | None,
]:
    if not _robots_allowed(url, timeout, cache=robots_cache):
        return None, [], 1, 0, 0, "robots.txt disallows page"
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            final_url = response.geturl()
            if len(raw) > max_bytes or "html" not in response.headers.get("content-type", "").lower():
                return None, [], 2, len(raw), elapsed, "unsupported or oversized page"
            if _registered_domain(final_url) != homepage_domain:
                return None, [], 2, len(raw), elapsed, "redirected outside registered domain"
        page_html = raw.decode("utf-8", errors="replace")
        page_soup = BeautifulSoup(page_html, "lxml")
        page_text = trafilatura.extract(page_html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        page_contacts = extract_contact_info(page_soup, page_text, registered_domain=homepage_domain)
        page_portals = _extract_career_portals(final_url, page_soup)
        page_titles, page_count, page_hiring = _extract_job_vacancies(final_url, page_soup, page_html)
        page = {
            "url": final_url,
            "title": page_soup.title.get_text(" ", strip=True)[:500] if page_soup.title else "",
            "main_text_excerpt": page_text[:5000],
            "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "contacts": page_contacts,
            "portals": page_portals,
            "job_titles": page_titles,
            "job_count": page_count,
            "hiring_flag": page_hiring,
        }
        return (
            page,
            _social_links(final_url, page_soup),
            2,
            len(raw),
            elapsed,
            None,
        )
    except Exception as exc:
        return None, [], 2, 0, int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {str(exc)[:120]}"


def _jsonld_organisations(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            kind = value.get("@type")
            kinds = set(kind if isinstance(kind, list) else [kind])
            if kinds & {"Organization", "Corporation", "LocalBusiness", "Store", "Restaurant"}:
                values.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(metadata.get("json-ld", []))
    return values[:20]


def _extraction_state(text: str, soup: BeautifulSoup) -> str:
    return "js_fallback_candidate" if len(text.strip()) < 100 and len(soup.select("script[src]")) >= 2 else "static_complete"


def fetch_website(
    url: str | None,
    *,
    company_name: str | None = None,
    orgnr: str | None = None,
    max_subpaths: int = 4,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    supplied_url = str(url or "").strip()
    supplied_scheme = bool(re.match(r"^https?://", supplied_url, re.I))
    normalized = normalize_homepage(url)
    if not normalized:
        return evidence("website", "not_found", "registry_linked_company_website", "https://data.brreg.no/enhetsregisteret/api/enheter", note="No valid registry website URL"), {"requests": 0, "bytes": 0, "latencies_ms": []}
    try:
        assert_public_url(normalized)
    except ValueError as exc:
        return evidence("website", "blocked", "registry_linked_company_website", normalized, note=str(exc)), {"requests": 0, "bytes": 0, "latencies_ms": []}

    robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
    if not _robots_allowed(normalized, timeout, cache=robots_cache):
        return evidence("website", "blocked", "registry_linked_company_website", normalized, note="robots.txt disallows this user agent"), {"requests": 1, "bytes": 0, "latencies_ms": []}

    started = time.monotonic()
    request = urllib.request.Request(normalized, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            if len(raw) > max_bytes:
                return evidence("website", "blocked", "registry_linked_company_website", normalized, note="Homepage exceeds byte limit"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            if "html" not in content_type.lower():
                return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"Unsupported content type: {content_type}"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            final_url = response.geturl()
            assert_public_url(final_url)
        html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        structured = extruct.extract(html, base_url=final_url, syntaxes=["json-ld", "microdata", "opengraph"])
        text = trafilatura.extract(html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        description_tag = soup.select_one('meta[name="description"], meta[property="og:description"]')
        description = str(description_tag.get("content") or "").strip() if description_tag else ""

        identity_nodes = soup.select('footer, address, [itemprop="legalName"], [itemprop="address"], [itemprop="telephone"], [itemprop="email"]')
        identity_text = " ".join(n.get_text(" ", strip=True) for n in identity_nodes)[:2000]

        homepage_domain = _registered_domain(final_url)
        homepage_contacts = extract_contact_info(soup, text, registered_domain=homepage_domain)
        hp_portals = _extract_career_portals(final_url, soup)
        hp_titles, hp_count, hp_hiring = _extract_job_vacancies(final_url, soup, html)

        value = {
            "requested_url": normalized,
            "final_url": final_url,
            "registered_domain": homepage_domain,
            "title": title[:500],
            "description": description[:2000],
            "main_text_excerpt": text[:5000],
            "identity_text_excerpt": identity_text,
            "social_links": _social_links(final_url, soup),
            "structured_organisations": _jsonld_organisations(structured),
            "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "extraction_state": _extraction_state(text, soup),
            "contacts": homepage_contacts,
        }

        pages = [{
            "url": final_url,
            "title": title[:500],
            "main_text_excerpt": text[:5000],
            "identity_text_excerpt": identity_text,
            "content_sha256": value["content_sha256"],
            "contacts": homepage_contacts,
        }]
        social = list(value["social_links"])
        crawl_errors = []
        requests = 2
        bytes_received = len(raw)
        page_latencies = [elapsed]

        all_emails: list[str] = list(homepage_contacts["emails"])
        all_phones: list[str] = list(homepage_contacts["phones"])
        all_addresses: list[str] = list(homepage_contacts["addresses"])

        all_portals: list[dict[str, str]] = list(hp_portals)
        all_job_titles: list[str] = list(hp_titles)
        explicit_counts: list[int] = [hp_count] if hp_count is not None else []
        hiring_flags: list[bool] = [hp_hiring] if hp_hiring is not None else []

        visited_career_page = any(term in final_url.casefold() for term in CAREER_TERMS)
        internal_career_url = final_url if visited_career_page else None

        for page_url in _priority_links(final_url, soup, limit=max_subpaths):
            (
                page,
                page_social,
                page_requests,
                page_bytes,
                page_elapsed,
                page_error,
            ) = _fetch_secondary_page(
                page_url,
                homepage_domain=homepage_domain,
                timeout=timeout,
                max_bytes=min(max_bytes, 1_000_000),
                robots_cache=robots_cache,
            )
            requests += page_requests
            bytes_received += page_bytes
            if page_elapsed:
                page_latencies.append(page_elapsed)
            if page:
                pages.append(page)
                social.extend(page_social)
                p_contacts = page.get("contacts") or {}
                p_portals = page.get("portals") or []
                p_titles = page.get("job_titles") or []
                p_count = page.get("job_count")
                p_hiring = page.get("hiring_flag")
                for email in p_contacts.get("emails", []):
                    if email not in all_emails:
                        all_emails.append(email)
                for phone in p_contacts.get("phones", []):
                    if phone not in all_phones:
                        all_phones.append(phone)
                for addr in p_contacts.get("addresses", []):
                    if addr not in all_addresses:
                        all_addresses.append(addr)
                all_portals.extend(p_portals)
                all_job_titles.extend(p_titles)
                if p_count is not None:
                    explicit_counts.append(p_count)
                if p_hiring is not None:
                    hiring_flags.append(p_hiring)
                if any(term in page["url"].casefold() for term in CAREER_TERMS):
                    visited_career_page = True
                    if not internal_career_url:
                        internal_career_url = page["url"]
            elif page_error:
                crawl_errors.append({"url": page_url, "error": page_error})

        deduped_portals = list({(item["platform"], item["url"]): item for item in all_portals}.values())
        deduped_titles = list(dict.fromkeys(all_job_titles))

        career_url = internal_career_url
        if not career_url and deduped_portals:
            career_url = deduped_portals[0]["url"]

        if deduped_titles:
            final_hiring_active = True
            final_count = max(len(deduped_titles), max(explicit_counts) if explicit_counts else 0)
        elif any(f is True for f in hiring_flags):
            final_hiring_active = True
            final_count = max(explicit_counts) if explicit_counts else (len(deduped_titles) or None)
        elif any(f is False for f in hiring_flags):
            final_hiring_active = False
            final_count = 0
        elif deduped_portals:
            final_hiring_active = True
            final_count = None
        elif visited_career_page:
            final_hiring_active = False
            final_count = 0
        else:
            final_hiring_active = None
            final_count = None

        value["pages"] = pages
        value["social_links"] = list({(item["platform"], item["url"]): item for item in social}.values())
        value["crawl_errors"] = crawl_errors

        value["contacts"] = {
            "emails": all_emails[:10],
            "phones": all_phones[:10],
            "addresses": all_addresses[:10],
        }
        value["hiring"] = {
            "hiring_active": final_hiring_active,
            "open_positions_count": final_count,
            "open_positions": deduped_titles,
            "career_url": career_url,
            "career_portals": deduped_portals,
        }
        value["emails"] = value["contacts"]["emails"]
        value["phones"] = value["contacts"]["phones"]
        value["addresses"] = value["contacts"]["addresses"]
        value["career_url"] = career_url
        value["hiring_active"] = final_hiring_active
        value["open_positions_count"] = final_count
        value["open_positions"] = deduped_titles

        return evidence("website", "available", "registry_linked_company_website", final_url, value=value, note="Company-controlled claim layer; not an official registry fact", content_sha256=value["content_sha256"]), {"requests": requests, "bytes": bytes_received, "latencies_ms": page_latencies}
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        status = "not_found" if exc.code in {404, 410} else "source_error"
        return evidence("website", status, "registry_linked_company_website", normalized, note=f"HTTP {exc.code}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
    except urllib.error.URLError as exc:
        if not supplied_scheme and normalized.startswith("https://"):
            first_elapsed = int((time.monotonic() - started) * 1000)
            record, metrics = fetch_website(
                "http://" + supplied_url,
                company_name=company_name,
                orgnr=orgnr,
                max_subpaths=max_subpaths,
                timeout=timeout,
                max_bytes=max_bytes,
            )
            metrics["requests"] += 2
            metrics["latencies_ms"].insert(0, first_elapsed)
            return record, metrics
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"URLError: {str(exc.reason)[:180]}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"{type(exc).__name__}: {str(exc)[:180]}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
REJECTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".js", ".css", ".webp"}
REJECTED_DOMAINS = {"example.com", "example.org", "example.test", "domain.test", "domain.no", "sentry.io"}
ATS_DOMAINS = {"teamtailor.com", "webcruiter.com", "recman.no", "finn.no/jobb", "jobbnorge.no", "lever.co", "greenhouse.io"}



def extract_hiring_signals(soup: BeautifulSoup, text: str, url: str) -> dict[str, Any]:
    text_content = (text or "").strip()
    if len(text_content) < 50 and len(soup.select("script[src]")) >= 2:
        return {
            "hiring_active": None,
            "open_positions_count": None,
            "open_positions": [],
            "career_url": None,
            "state": "js_fallback_candidate",
        }

    lower_text = text_content.casefold()
    if "for tiden ingen ledige stillinger" in lower_text or "ingen ledige stillinger" in lower_text:
        return {
            "hiring_active": False,
            "open_positions_count": 0,
            "open_positions": [],
            "career_url": url if "/karriere" in url or "/job" in url else None,
            "state": "static_complete",
        }

    # Find candidate elements, discarding parent wrapper elements that contain other candidates
    all_candidates = soup.select(".job-item, .job-card, [class*='position'], [class*='vacancy']")
    leaf_candidates = [
        c for c in all_candidates
        if not any(other is not c and other in c.descendants for other in all_candidates)
    ]

    titles: list[str] = []
    for card in leaf_candidates:
        h = card.find(["h2", "h3", "h4", "a"]) or card
        t = h.get_text(strip=True) if h else ""
        if t and len(t) > 3:
            titles.append(t)

    # Check for outbound ATS portal links
    career_url = None
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        for ats in ATS_DOMAINS:
            if ats in href:
                parsed = href.split("?")[0].rstrip("/")
                if parsed in {"https://www.webcruiter.com", "https://webcruiter.com", "https://finn.no", "https://www.finn.no"}:
                    continue
                career_url = href
                break
        if career_url:
            break

    # Check if the page contains commercial words without vacancy verbs
    has_commercial_context = "markedsposisjoner" in lower_text and not any(v in lower_text for v in ("søker", "ledig", "rekrutterer", "karriere", "apply", "jobb"))

    if titles:
        return {
            "hiring_active": True,
            "open_positions_count": len(titles),
            "open_positions": titles[:50],  # Bound list to 50
            "career_url": career_url or url,
            "state": "static_complete",
        }
    elif career_url:
        return {
            "hiring_active": True,
            "open_positions_count": None,
            "open_positions": [],
            "career_url": career_url,
            "state": "static_complete",
        }
    elif has_commercial_context:
        return {
            "hiring_active": False,
            "open_positions_count": 0,
            "open_positions": [],
            "career_url": None,
            "state": "static_complete",
        }

    return {
        "hiring_active": None,
        "open_positions_count": None,
        "open_positions": [],
        "career_url": None,
        "state": "static_complete",
    }
