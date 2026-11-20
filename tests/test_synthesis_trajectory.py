from __future__ import annotations

import unittest

from norway_company_agent.synthesis import compute_financial_metrics


def _recs(*revenues):
    return [{"year": str(2025 - i), "revenue": r, "operating_result": 1.0} for i, r in enumerate(revenues)]


class TrajectoryTest(unittest.TestCase):
    def test_one_year_rise_below_long_run_level_is_a_rebound(self):
        m = compute_financial_metrics(_recs(259_213, 122_410, 900_000, 1_100_000))
        self.assertEqual(m["revenue_trajectory_status"], "rebound")
        self.assertLess(m["cagr_pct"], 0)
        self.assertIn("rebound", m["revenue_trajectory_badge"])

    def test_one_year_dip_above_long_run_level_is_a_dip(self):
        m = compute_financial_metrics(_recs(150, 200, 100))
        self.assertEqual(m["revenue_trajectory_status"], "dip")

    def test_consistent_growth_unchanged(self):
        m = compute_financial_metrics(_recs(300, 200, 100))
        self.assertEqual(m["revenue_trajectory_status"], "steady_growth")


if __name__ == "__main__":
    unittest.main()
