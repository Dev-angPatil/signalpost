"""Tier 5: Adversarial Hardening & Security E2E Test Suite (tests/test_e2e_tier5.py).

Comprehensive security and robustness testing:
- SSRF prevention (loopback, RFC 1918 private ranges, AWS/GCP cloud metadata 169.254.169.254, non-HTTP schemes)
- Cross-Site Scripting (XSS) injection sanitization in HTML showcase generation
- Norwegian character encoding resilience (æ, ø, å, Æ, Ø, Å)
- Payload truncation and memory bounding on oversized responses
- Safe redirect validation against internal network targets
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.contract import build_output_envelope
from norway_company_agent.evidence import evidence, utc_now
from norway_company_agent.showcase import generate_showcase_html
from norway_company_agent.synthesis import synthesize_executive_profile
from norway_company_agent.website import (
    SafeRedirectHandler,
    assert_public_url,
    fetch_website,
    normalize_homepage,
)


class TestTier5AdversarialHardening(unittest.TestCase):
    """Tier 5: SSRF prevention, XSS escaping, encoding resilience, and boundary defense."""

    def test_t5_01_ssrf_rejects_loopback_127_0_0_1(self) -> None:
        """Verify assert_public_url rejects loopback IPv4 address 127.0.0.1."""
        with self.assertRaises(ValueError) as ctx:
            assert_public_url("http://127.0.0.1:8080/metrics")
        self.assertIn("blocked", str(ctx.exception).lower())

    def test_t5_02_ssrf_rejects_localhost_hostname(self) -> None:
        """Verify assert_public_url rejects localhost and .local domains."""
        with self.assertRaises(ValueError) as ctx:
            assert_public_url("http://localhost/admin")
        self.assertIn("local", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx2:
            assert_public_url("http://internal.localhost:3000/")
        self.assertIn("local", str(ctx2.exception).lower())

    def test_t5_03_ssrf_rejects_private_10_0_0_0_network(self) -> None:
        """Verify assert_public_url rejects RFC 1918 10.0.0.0/8 private network."""
        with self.assertRaises(ValueError) as ctx:
            assert_public_url("http://10.0.0.1:80/status")
        self.assertIn("blocked", str(ctx.exception).lower())

    def test_t5_04_ssrf_rejects_private_192_168_network(self) -> None:
        """Verify assert_public_url rejects RFC 1918 192.168.0.0/16 private network."""
        with self.assertRaises(ValueError) as ctx:
            assert_public_url("http://192.168.1.1/setup")
        self.assertIn("blocked", str(ctx.exception).lower())

    def test_t5_05_ssrf_rejects_cloud_metadata_169_254_169_254(self) -> None:
        """Verify assert_public_url rejects cloud instance metadata service (169.254.169.254)."""
        with self.assertRaises(ValueError) as ctx:
            assert_public_url("http://169.254.169.254/latest/meta-data/")
        self.assertIn("blocked", str(ctx.exception).lower())

    def test_t5_06_ssrf_rejects_non_http_schemes(self) -> None:
        """Verify assert_public_url rejects file://, ftp://, gopher:// schemes."""
        for invalid_url in (
            "file:///etc/passwd",
            "ftp://ftp.example.com/data",
            "gopher://gopher.example.com/",
            "javascript:alert(1)",
        ):
            with self.subTest(url=invalid_url):
                with self.assertRaises(ValueError):
                    assert_public_url(invalid_url)

    def test_t5_07_xss_escaping_in_company_name_showcase(self) -> None:
        """Verify <script> tags injected into company name are escaped in HTML output."""
        xss_profile = {
            "organisation_number": "999000111",
            "name": "<script>alert('pwned')</script> NORDIC AS",
            "legal_form": "AS",
            "municipality": "OSLO",
            "industry": "Consulting",
            "evidence": {},
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "showcase_xss.html"
            res = generate_showcase_html([xss_profile], out_file)
            content = out_file.read_text(encoding="utf-8")

            # Must NOT contain raw unescaped script tag in profile rendering
            self.assertNotIn("<h2><script>alert('pwned')</script>", content)
            self.assertTrue(out_file.is_file())

    def test_t5_08_xss_escaping_in_website_description(self) -> None:
        """Verify script tag breakout is neutralized and template employs esc() sanitization."""
        xss_web_profile = {
            "organisation_number": "999000222",
            "name": "SAFE CORP AS",
            "legal_form": "AS",
            "municipality": "BERGEN",
            "evidence": {
                "website": evidence(
                    "website",
                    "available",
                    "company_website",
                    "https://safecorp.no",
                    value={
                        "title": "</script><script>alert(1)</script> Safe Corp",
                        "description": "We are <b onmouseover=alert(1)>great</b> at security.",
                    },
                    content_sha256="1" * 64,
                )
            },
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "showcase_desc_xss.html"
            generate_showcase_html([xss_web_profile], out_file)
            content = out_file.read_text(encoding="utf-8")

            # Must NOT allow closing script tag breakout
            self.assertNotIn("</script><script>alert(1)</script>", content)
            # Must verify esc() wrapper is present for description rendering in template
            self.assertIn("esc(x.web.value.description", content)

    def test_t5_09_norwegian_special_characters_utf8_resilience(self) -> None:
        """Verify æ, ø, å, Æ, Ø, Å roundtrip cleanly through envelopes and synthesis."""
        norwegian_entity = {
            "organisation_number": "987654321",
            "name": "BLÅBÆR & TØRRFISK VESTFJORDEN AS",
            "legal_form": "AS",
            "municipality": "BØMLO",
            "industry": "Foredling av fisk, krepsdyr og bløtdyr",
            "ceo": "Åsmund Ødegård",
            "board_chair": "Kjærlaug Ås",
            "evidence": {
                "registry": evidence(
                    "registry",
                    "available",
                    "official_registry",
                    "https://data.brreg.no/enheter/987654321",
                    value={"navn": "BLÅBÆR & TØRRFISK VESTFJORDEN AS"},
                    content_sha256="f" * 64,
                )
            },
        }

        envelope = build_output_envelope(
            norwegian_entity,
            run_id="norwegian-chars-run",
            started_at=utc_now(),
            completed_at=utc_now(),
        )

        envelope_json = json.dumps(envelope, ensure_ascii=False)
        self.assertIn("BLÅBÆR & TØRRFISK VESTFJORDEN AS", envelope_json)
        self.assertIn("BØMLO", envelope_json)

        synth = synthesize_executive_profile(norwegian_entity)
        self.assertIn("BLÅBÆR", synth["summary"])
        self.assertIn("BØMLO", synth["summary"])
        self.assertIn("Åsmund Ødegård", synth["summary"])

    def test_t5_10_extreme_html_payload_byte_bounds(self) -> None:
        """Verify fetch_website respects max_bytes limit on oversized payload."""
        with patch("urllib.request.build_opener") as mock_opener:
            mock_resp = MagicMock()
            mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
            # Return payload exceeding 100 bytes limit
            mock_resp.read.return_value = b"A" * 200
            mock_resp.geturl.return_value = "https://example.com"
            mock_opener.return_value.open.return_value.__enter__.return_value = mock_resp

            with patch("norway_company_agent.website._robots_allowed", return_value=True):
                with patch("norway_company_agent.website.assert_public_url"):
                    record, metrics = fetch_website(
                        "https://example.com",
                        max_bytes=100,
                        timeout=5.0,
                    )

            self.assertEqual(record["status"], "blocked")
            self.assertIn("exceeds byte limit", record["note"])

    def test_t5_11_malicious_redirect_to_internal_ip_blocked(self) -> None:
        """Verify SafeRedirectHandler blocks redirects to internal private IPs."""
        handler = SafeRedirectHandler()
        req = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1:8080/secret")
        self.assertIn("blocked", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
