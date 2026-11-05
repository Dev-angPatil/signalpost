from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.score_competition_v3 import research_qualification_gate


class ScoreProxyTests(unittest.TestCase):
    def test_uses_explicit_research_gate_when_present(self):
        self.assertTrue(research_qualification_gate({"external_footprint_qa_passed": True, "qualification_passed": False}))
        self.assertFalse(research_qualification_gate({"external_footprint_qa_passed": False, "qualification_passed": True}))

    def test_supports_legacy_research_reports_without_explicit_gate(self):
        self.assertTrue(research_qualification_gate({"qualification_passed": True}))
        self.assertFalse(research_qualification_gate({"qualification_passed": False}))


if __name__ == "__main__":
    unittest.main()
