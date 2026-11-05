"""Small, opt-in connectors for official Google Places and YouTube APIs.

The functions in this module deliberately stop at conservative summaries.  They do
not fetch reviews or video text, and they do not turn a name-only API match into a
company observation.  ``urlopen`` and ``fetcher`` are injectable so API behaviour
can be tested without credentials or network access.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable


PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

PLACES_TEXT_FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.rating",
        "places.userRatingCount",
    )
)
PLACES_DETAILS_FIELD_MASK = ",".join(
    (
        "id",
        "displayName",
        "formattedAddress",
        "websiteUri",
        "googleMapsUri",
        "rating",
        "userRatingCount",
    )
)
YOUTUBE_PARTS = "snippet,statistics"
YOUTUBE_FIELDS = (
    "items(id,snippet(title,description,customUrl,channelId,publishedAt),"
    "statistics(viewCount,subscriberCount,videoCount))"
)


class ObservationRows(list[dict[str, Any]]):
    """List-compatible observations with non-publishable connector telemetry."""

    def __init__(self, rows: list[dict[str, Any]], metrics: dict[str, Any]):
        super().__init__(rows)
        self.metrics = metrics


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _domain(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    return (urllib.parse.urlparse(raw).hostname or "").casefold().removeprefix("www.")


def _website(profile: dict[str, Any]) -> dict[str, Any]:
    return ((profile.get("evidence") or {}).get("website") or {})


def _verified_site(profile: dict[str, Any]) -> str:
    website = _website(profile)
    identity = ((website.get("value") or {}).get("identity_assessment") or {})
    if website.get("status") != "available" or identity.get("publishable") is not True:
        return ""
    if identity.get("status", "exact") != "exact":
        return ""
    return str((website.get("value") or {}).get("final_url") or website.get("source_url") or "").strip()


def _registry(profile: dict[str, Any]) -> dict[str, Any]:
    return ((profile.get("evidence") or {}).get("registry") or {}).get("value") or {}


def _registry_value(registry: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = registry.get(key)
        if value is not None:
            return value
    return None


def _registry_address(profile: dict[str, Any]) -> str:
    registry = _registry(profile)
    address = _registry_value(registry, "forretningsadresse", "business_address")
    if isinstance(address, dict):
        address = " ".join(
            str(address.get(key) or "")
            for key in ("adresse", "postnummer", "poststed", "land")
        )
    if address:
        return str(address)
    return " ".join(
        str(_registry_value(registry, key) or "")
        for key in (
            "forretningsadresse.adresse",
            "forretningsadresse.postnummer",
            "forretningsadresse.poststed",
        )
    ).strip()


def _address_matches(profile: dict[str, Any], candidate: str) -> bool:
    expected = set(_normalise(_registry_address(profile)).split())
    actual = set(_normalise(candidate).split())
    return bool(expected and expected.issubset(actual))


def _candidate_name(candidate: dict[str, Any]) -> str:
    display = candidate.get("displayName")
    return str(display.get("text") if isinstance(display, dict) else display or "")


def _candidate_org(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("organisationNumber")
        or candidate.get("organizationNumber")
        or candidate.get("organisation_number")
        or ""
    )


def _identity_proof(profile: dict[str, Any], candidate: dict[str, Any], *, channel: bool = False) -> list[dict[str, Any]]:
    """Return strong proof, or an empty list for a name-only/ambiguous match."""
    org = str(profile.get("organisation_number") or "")
    candidate_org = _candidate_org(candidate)
    if org and candidate_org == org:
        return [{"type": "organisation_number", "value": org}]

    site = _verified_site(profile)
    candidate_site = str(candidate.get("websiteUri") or "")
    site_match = bool(site and candidate_site and _domain(site) == _domain(candidate_site))
    if site_match:
        return [{"type": "verified_site_cross_link", "source_url": site, "value": candidate_site}]

    if channel:
        linked = []
        for item in ((_website(profile).get("value") or {}).get("social_links") or []):
            if isinstance(item, dict) and str(item.get("platform") or "").casefold() == "youtube":
                linked.append(str(item.get("url") or ""))
        custom_url = str((candidate.get("snippet") or {}).get("customUrl") or "")
        channel_id = str(candidate.get("id") or "")
        channel_urls = {
            custom_url.rstrip("/"),
            f"https://www.youtube.com/{custom_url.lstrip('/')}".rstrip("/"),
            f"https://www.youtube.com/channel/{channel_id}".rstrip("/"),
        }
        if any(item.rstrip("/") in channel_urls for item in linked if item):
            return [{"type": "verified_site_cross_link", "value": next(item for item in linked if item.rstrip("/") in channel_urls)}]

    exact_name = _normalise(profile.get("name") or _registry_value(_registry(profile), "navn"))
    candidate_name = _normalise(_candidate_name(candidate) if not channel else (candidate.get("snippet") or {}).get("title"))
    if exact_name and exact_name == candidate_name:
        if _address_matches(profile, str(candidate.get("formattedAddress") or candidate.get("address") or "")):
            return [{"type": "exact_legal_name_and_address", "value": _candidate_name(candidate)}]
        if site_match:
            return [{"type": "exact_legal_name_and_website", "value": _candidate_name(candidate)}]
        if channel and site and _normalise(_domain(site)) in _normalise((candidate.get("snippet") or {}).get("description")):
            return [{"type": "exact_legal_name_and_website", "value": _candidate_name(candidate), "source_url": site}]
    return []


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "url_error"
    return type(exc).__name__.casefold()


def _invoke(fetch: Callable[..., Any], request: urllib.request.Request, timeout: float) -> Any:
    try:
        return fetch(request, timeout=timeout)
    except TypeError:
        return fetch(request)


def _request_json(
    request: urllib.request.Request,
    *,
    timeout: float,
    urlopen: Callable[..., Any] | None,
    fetcher: Callable[..., Any] | None,
) -> tuple[Any, bytes, dict[str, Any]]:
    started = time.monotonic()
    fetch = fetcher or urlopen or urllib.request.urlopen
    try:
        response = _invoke(fetch, request, timeout)
        if isinstance(response, dict):
            raw = json.dumps(response, ensure_ascii=False, sort_keys=True).encode()
            status = 200
            body = response
        else:
            context = response if hasattr(response, "__enter__") else _ResponseContext(response)
            with context as active:
                status = int(getattr(active, "status", getattr(active, "code", 200)))
                raw = active.read()
            body = json.loads(raw)
        latency_ms = int((time.monotonic() - started) * 1000)
        metrics = {
            "status": "ok" if 200 <= status < 300 else "error",
            "request_count": 1,
            "requests": 1,
            "bytes_received": len(raw),
            "bytes": len(raw),
            "latency_ms": latency_ms,
            "latencies_ms": [latency_ms],
            "estimated_cost_usd": None,
            "cost_usd": None,
            "third_party_cost_usd": None,
            "error_count": 0,
            "abstention_count": 0,
            "http_status": status,
        }
        if not 200 <= status < 300:
            metrics.update({"status": "error", "error": f"http_{status}"})
            return None, raw, metrics
        return body, raw, metrics
    except (ValueError, json.JSONDecodeError) as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return None, b"", {
            "status": "error", "request_count": 1, "requests": 1, "bytes_received": 0, "bytes": 0,
            "latency_ms": latency_ms,
            "latencies_ms": [latency_ms],
            "estimated_cost_usd": None, "cost_usd": None, "third_party_cost_usd": None,
            "error_count": 1, "abstention_count": 0, "error": _safe_error(exc),
        }
    except Exception as exc:  # connector failures must abstain safely
        latency_ms = int((time.monotonic() - started) * 1000)
        return None, b"", {
            "status": "error", "request_count": 1, "requests": 1, "bytes_received": 0, "bytes": 0,
            "latency_ms": latency_ms,
            "latencies_ms": [latency_ms],
            "estimated_cost_usd": None, "cost_usd": None, "third_party_cost_usd": None,
            "error_count": 1, "abstention_count": 0, "error": _safe_error(exc),
        }


class _ResponseContext:
    def __init__(self, response: Any):
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, *_args):
        close = getattr(self.response, "close", None)
        if close:
            close()
        return False


def _empty(metrics: dict[str, Any], reason: str) -> ObservationRows:
    return ObservationRows([], {**metrics, "status": "abstained", "abstention_reason": reason, "abstention_count": 1})


def _common(profile: dict[str, Any], source_url: str, raw: bytes, metrics: dict[str, Any], proof: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "organisation_number": str(profile.get("organisation_number") or ""),
        "source_url": source_url,
        "retrieved_at": _utc_now(),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "exact_entity": True,
        "identity_proof": proof,
        "acquisition_mode": "official_api",
        "rights_status": "approved",
        "source_class": "official_platform_api",
        "metrics": dict(metrics),
    }


def google_places_text_search(
    profile: dict[str, Any],
    api_key: str,
    *,
    urlopen: Callable[..., Any] | None = None,
    fetcher: Callable[..., Any] | None = None,
    timeout: float = 20.0,
) -> ObservationRows:
    """Run Places API (New) Text Search and emit one exact place summary at most."""
    if not api_key:
        return _empty({"request_count": 0, "bytes_received": 0, "latency_ms": 0, "estimated_cost_usd": None, "cost_usd": None}, "missing_api_key")
    query = str(profile.get("name") or _registry_value(_registry(profile), "navn") or "").strip()
    if not query or not str(profile.get("organisation_number") or "").isdigit():
        return _empty({"request_count": 0, "bytes_received": 0, "latency_ms": 0, "estimated_cost_usd": None, "cost_usd": None}, "missing_identity_input")
    payload = json.dumps({"textQuery": query, "languageCode": "no", "regionCode": "NO", "pageSize": 5}).encode()
    request = urllib.request.Request(
        PLACES_TEXT_SEARCH_URL,
        data=payload,
        headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": PLACES_TEXT_FIELD_MASK, "Content-Type": "application/json"},
        method="POST",
    )
    body, raw, metrics = _request_json(request, timeout=timeout, urlopen=urlopen, fetcher=fetcher)
    if body is None:
        return ObservationRows([], metrics)
    candidates = body.get("places") if isinstance(body, dict) else None
    candidates = candidates if isinstance(candidates, list) else []
    matches = [(item, _identity_proof(profile, item)) for item in candidates if isinstance(item, dict)]
    matches = [(item, proof) for item, proof in matches if proof]
    if len(matches) != 1:
        return _empty(metrics, "no_exact_match" if not matches else "ambiguous_exact_match")
    candidate, proof = matches[0]
    place_id = str(candidate.get("id") or "")
    source_url = PLACES_TEXT_SEARCH_URL
    evidence = f"{_candidate_name(candidate)}; {candidate.get('formattedAddress') or ''}; rating={candidate.get('rating')}; reviews={candidate.get('userRatingCount')}"
    row = {
        **_common(profile, source_url, raw, metrics, proof),
        "id": "google-places-summary-" + hashlib.sha256(f"{profile['organisation_number']}|{place_id}".encode()).hexdigest()[:24],
        "platform": "google_places",
        "signal_type": "place_summary",
        "evidence_span": evidence[:1200],
        "metrics": {**metrics, "place_id": place_id, "name": _candidate_name(candidate), "address": candidate.get("formattedAddress"), "website": candidate.get("websiteUri"), "maps_url": candidate.get("googleMapsUri"), "rating": candidate.get("rating"), "rating_scale": 5, "review_count": candidate.get("userRatingCount")},
        "strategy": "official_places_identity_and_summary",
    }
    return ObservationRows([row], {**metrics, "status": "published"})


def google_places_place_details(
    profile: dict[str, Any],
    place_id: str,
    api_key: str,
    *,
    urlopen: Callable[..., Any] | None = None,
    fetcher: Callable[..., Any] | None = None,
    timeout: float = 20.0,
) -> ObservationRows:
    """Fetch a Places API (New) detail record with a strict field mask."""
    if not api_key or not place_id:
        return _empty({"request_count": 0, "bytes_received": 0, "latency_ms": 0, "estimated_cost_usd": None, "cost_usd": None}, "missing_api_key_or_place_id")
    escaped = urllib.parse.quote(str(place_id), safe="/")
    url = PLACES_DETAILS_URL.format(place_id=escaped)
    request = urllib.request.Request(url, headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": PLACES_DETAILS_FIELD_MASK, "Accept": "application/json"})
    body, raw, metrics = _request_json(request, timeout=timeout, urlopen=urlopen, fetcher=fetcher)
    if body is None:
        return ObservationRows([], metrics)
    candidate = body if isinstance(body, dict) else {}
    proof = _identity_proof(profile, candidate)
    if not proof:
        return _empty(metrics, "no_exact_match")
    place_key = str(candidate.get("id") or place_id)
    source_url = url
    evidence = f"{_candidate_name(candidate)}; {candidate.get('formattedAddress') or ''}; rating={candidate.get('rating')}; reviews={candidate.get('userRatingCount')}"
    row = {
        **_common(profile, source_url, raw, metrics, proof),
        "id": "google-places-summary-" + hashlib.sha256(f"{profile['organisation_number']}|{place_key}".encode()).hexdigest()[:24],
        "platform": "google_places", "signal_type": "place_summary", "evidence_span": evidence[:1200],
        "metrics": {**metrics, "place_id": place_key, "name": _candidate_name(candidate), "address": candidate.get("formattedAddress"), "website": candidate.get("websiteUri"), "maps_url": candidate.get("googleMapsUri"), "rating": candidate.get("rating"), "rating_scale": 5, "review_count": candidate.get("userRatingCount")},
        "strategy": "official_places_identity_and_summary",
    }
    return ObservationRows([row], {**metrics, "status": "published"})


def youtube_channel_lookup(
    profile: dict[str, Any],
    channel_id: str,
    api_key: str,
    *,
    urlopen: Callable[..., Any] | None = None,
    fetcher: Callable[..., Any] | None = None,
    timeout: float = 20.0,
) -> ObservationRows:
    """Fetch public channel identity/statistics from YouTube Data API v3."""
    if not api_key or not channel_id:
        return _empty({"request_count": 0, "bytes_received": 0, "latency_ms": 0, "estimated_cost_usd": None, "cost_usd": None}, "missing_api_key_or_channel_id")
    selector_key = "id"
    selector_value = str(channel_id)
    if selector_value.startswith("http"):
        parsed = urllib.parse.urlparse(selector_value)
        if "/@" in parsed.path:
            selector_key = "forHandle"
            selector_value = parsed.path.split("/@", 1)[1].strip("/")
            selector_value = "@" + selector_value
        elif "/channel/" in parsed.path:
            selector_value = parsed.path.split("/channel/", 1)[1].strip("/")
    elif selector_value.startswith("@"):
        selector_key = "forHandle"
    params = urllib.parse.urlencode({"part": YOUTUBE_PARTS, "fields": YOUTUBE_FIELDS, selector_key: selector_value})
    public_url = f"{YOUTUBE_CHANNELS_URL}?{params}"
    request = urllib.request.Request(
        f"{public_url}&key={urllib.parse.quote(api_key, safe='')}",
        headers={"Accept": "application/json"},
    )
    body, raw, metrics = _request_json(request, timeout=timeout, urlopen=urlopen, fetcher=fetcher)
    if body is None:
        return ObservationRows([], metrics)
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        return _empty(metrics, "channel_not_found_or_ambiguous")
    candidate = items[0]
    proof = _identity_proof(profile, candidate, channel=True)
    if not proof:
        return _empty(metrics, "no_exact_match")
    snippet = candidate.get("snippet") or {}
    stats = candidate.get("statistics") or {}
    row = {
        **_common(profile, public_url, raw, metrics, proof),
        "id": "youtube-channel-metrics-" + hashlib.sha256(f"{profile['organisation_number']}|{candidate.get('id') or channel_id}".encode()).hexdigest()[:24],
        "platform": "youtube", "signal_type": "profile_metrics",
        "evidence_span": f"{snippet.get('title') or ''}; subscribers={stats.get('subscriberCount')}; views={stats.get('viewCount')}; videos={stats.get('videoCount')}",
        "metrics": {**metrics, "channel_id": candidate.get("id") or channel_id, "channel_title": snippet.get("title"), "subscriber_count": _integer(stats.get("subscriberCount")), "view_count": _integer(stats.get("viewCount")), "video_count": _integer(stats.get("videoCount"))},
        "strategy": "official_youtube_channel_summary",
    }
    return ObservationRows([row], {**metrics, "status": "published"})


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# Descriptive aliases for callers that prefer a fetch_* naming convention.
fetch_google_places_text_search = google_places_text_search
fetch_google_places_place_details = google_places_place_details
fetch_youtube_channel = youtube_channel_lookup
