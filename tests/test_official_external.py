from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external_footprint import validate_observation  # noqa: E402
from norway_company_agent.official_external import (  # noqa: E402
    google_places_place_details,
    google_places_text_search,
    youtube_channel_lookup,
)


class _Response:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def _profile() -> dict:
    return {
        "organisation_number": "923609016",
        "name": "Example AS",
        "evidence": {
            "registry": {
                "status": "available",
                "value": {
                    "navn": "Example AS",
                    "forretningsadresse.adresse": "Storgata 1",
                    "forretningsadresse.postnummer": "0155",
                    "forretningsadresse.poststed": "Oslo",
                },
            },
            "website": {
                "status": "available",
                "source_url": "https://example.no/",
                "retrieved_at": "2026-10-23T12:00:00Z",
                "value": {
                    "final_url": "https://example.no/",
                    "identity_assessment": {"publishable": True, "status": "exact"},
                    "social_links": [
                        {"platform": "youtube", "url": "https://www.youtube.com/@example"}
                    ],
                },
            },
        },
    }


class OfficialExternalTests(unittest.TestCase):
    def test_places_text_search_uses_explicit_mask_and_emits_publishable_summary(self):
        raw = json.dumps(
            {
                "places": [
                    {
                        "id": "places/abc123",
                        "displayName": {"text": "Example AS"},
                        "formattedAddress": "Storgata 1, 0155 Oslo, Norway",
                        "websiteUri": "https://example.no/",
                        "googleMapsUri": "https://maps.google.com/?cid=abc123",
                        "rating": 4.7,
                        "userRatingCount": 23,
                    }
                ]
            }
        ).encode()
        requests = []

        def urlopen(request, timeout):
            requests.append((request, timeout))
            return _Response(raw)

        rows = google_places_text_search(_profile(), "secret", urlopen=urlopen)

        self.assertEqual(len(rows), 1)
        self.assertTrue(all(not validate_observation(row) for row in rows))
        self.assertEqual(rows[0]["platform"], "google_places")
        self.assertEqual(rows[0]["acquisition_mode"], "official_api")
        self.assertEqual(rows[0]["rights_status"], "approved")
        self.assertEqual(rows[0]["content_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertIn("rating", rows[0]["metrics"])
        self.assertNotIn("reviews", rows[0]["metrics"])
        self.assertEqual(rows[0]["metrics"]["request_count"], 1)
        self.assertIn("places.id", requests[0][0].get_header("X-goog-fieldmask"))
        self.assertNotIn("secret", rows[0]["source_url"])

    def test_place_details_does_not_publish_without_strong_identity(self):
        raw = json.dumps(
            {
                "id": "places/abc123",
                "displayName": {"text": "Example AS"},
                "formattedAddress": "A Different Road 9, Oslo, Norway",
                "websiteUri": "https://unrelated.example/",
                "rating": 4.9,
                "userRatingCount": 100,
            }
        ).encode()

        rows = google_places_place_details(
            _profile(), "abc123", "secret", urlopen=lambda request, timeout: _Response(raw)
        )

        self.assertEqual(rows, [])

    def test_youtube_lookup_requires_verified_site_cross_link_and_avoids_review_text(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "id": "UCexample",
                        "snippet": {
                            "title": "Example AS",
                            "description": "Official channel",
                            "customUrl": "@example",
                        },
                        "statistics": {
                            "subscriberCount": "42",
                            "viewCount": "1234",
                            "videoCount": "7",
                        },
                    }
                ]
            }
        ).encode()

        rows = youtube_channel_lookup(
            _profile(), "UCexample", "secret", urlopen=lambda request, timeout: _Response(raw)
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(validate_observation(row) == [])
        self.assertEqual(row["signal_type"], "profile_metrics")
        self.assertEqual(row["metrics"]["subscriber_count"], 42)
        self.assertIn("site_cross_link", json.dumps(row["identity_proof"]))
        self.assertNotIn("review", row)
        self.assertEqual(row["metrics"]["request_count"], 1)

    def test_youtube_lookup_abstains_on_http_error_with_safe_metrics(self):
        rows = youtube_channel_lookup(
            _profile(), "UCmissing", "secret", urlopen=lambda request, timeout: _Response(b"{}", 404)
        )

        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
