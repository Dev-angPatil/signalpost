"""Paid-API spend: prices, worst-case reservations, and the run-wide cap seeing every dollar."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import run_agent
from norway_company_agent import ai_enrich, claude_cost, web_discovery
from norway_company_agent.claude_cost import reserve_usd, usage_cost


def _usage(inp=0, written=0, read=0, out=0):
    return SimpleNamespace(input_tokens=inp, cache_creation_input_tokens=written, cache_read_input_tokens=read, output_tokens=out)


class UsageCostTests(unittest.TestCase):
    def test_sonnet_5_5_bills_cache_writes_at_1_25x_and_reads_at_0_05x(self) -> None:
        # 1M uncached in ($2) + 1M written ($2.50) + 1M read ($0.10) + 1M out ($10)
        self.assertAlmostEqual(usage_cost("claude-sonnet-5-5", _usage(1_000_000, 1_000_000, 1_000_000, 1_000_000)), 14.60, places=4)

    def test_haiku_5_5_reads_at_0_1x_and_long_prompts_cost_more(self) -> None:
        self.assertAlmostEqual(usage_cost("claude-haiku-5-5", _usage(read=50_000)), 0.0005, places=7)  # 50K x $0.10 x 0.1
        self.assertAlmostEqual(usage_cost("claude-haiku-5-5", _usage(inp=50_000, out=1_000)), 0.0055, places=6)
        self.assertAlmostEqual(usage_cost("claude-haiku-5-5", _usage(inp=200_000, out=1_000)), 0.1025, places=6)  # $0.50 / $2.50 above 100K

    def test_web_searches_are_billed(self) -> None:
        self.assertAlmostEqual(usage_cost("claude-haiku-5-5", _usage(), searches=2), 0.02, places=6)

    def test_an_unknown_model_is_billed_at_the_highest_listed_price(self) -> None:
        unknown = usage_cost("claude-new-fallback", _usage(inp=1_000_000, out=1_000_000))
        self.assertAlmostEqual(unknown, sum(claude_cost.UNKNOWN_PRICE), places=4)
        self.assertGreaterEqual(unknown, usage_cost("claude-sonnet-5-5", _usage(inp=1_000_000, out=1_000_000)))

    def test_missing_usage_costs_nothing(self) -> None:
        self.assertEqual(usage_cost("claude-sonnet-5-5", None), 0.0)


class ReservationTests(unittest.TestCase):
    def test_reservation_covers_a_call_that_uses_every_output_token(self) -> None:
        chars, max_tokens = 9_000, 16_000
        worst = usage_cost("claude-sonnet-5-5", _usage(written=int(chars / claude_cost.CHARS_PER_TOKEN), out=max_tokens))
        self.assertGreaterEqual(reserve_usd("claude-sonnet-5-5", chars, max_tokens), worst)

    def test_each_model_call_reserves_at_least_its_maximum_output(self) -> None:
        p_out = claude_cost.prices(web_discovery.SEARCH_MODEL)[1]
        self.assertGreaterEqual(web_discovery.RERANK_RESERVE_USD, web_discovery.RERANK_MAX_TOKENS * p_out / 1e6)
        self.assertGreaterEqual(web_discovery.CAREERS_RESERVE_USD, web_discovery.CAREERS_MAX_TOKENS * p_out / 1e6)
        self.assertGreaterEqual(web_discovery.AGENT_SEARCH_RESERVE_USD,
                                web_discovery.AGENT_MAX_TOKENS * p_out / 1e6 + web_discovery.AGENT_MAX_SEARCHES * claude_cost.WEB_SEARCH_USD)

    def test_analysis_reserves_its_worst_case_before_calling(self) -> None:
        profile = {"organisation_number": "916523173", "name": "ALTNET AS", "evidence": {}}
        facts = [{"id": "W1", "kind": "website", "text": "Altnet AS builds websites", "source_url": "https://www.altnet.no/", "evidence_id": "ev-website"}]
        reserved = ai_enrich.reserve_for_call(profile, facts)
        self.assertGreaterEqual(reserved, ai_enrich.MAX_OUTPUT_TOKENS * claude_cost.prices(ai_enrich.MODEL)[1] / 1e6)
        asked: list[float] = []
        original = ai_enrich.build_fact_sheet
        ai_enrich.build_fact_sheet = lambda p: facts
        try:
            meta = ai_enrich.enrich_with_ai(profile, lambda est: asked.append(est) or False)
        finally:
            ai_enrich.build_fact_sheet = original
        self.assertEqual(meta.get("skipped"), "budget")  # refused before any request was made
        self.assertEqual(asked, [reserved])


class RunWideCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gov = run_agent.CostGovernor(max_budget_usd=1.00)
        self.budget = web_discovery.BudgetSlices()
        self.budget.configure(self.gov.try_spend, self.gov.refund, self.gov.charge)
        self.budget.caps["analysis"] = 5.0

    def test_cheaper_calls_are_refunded_to_the_run(self) -> None:
        self.assertTrue(self.budget.try_spend("analysis", 0.60))
        self.budget.settle("analysis", 0.60, 0.10)
        self.assertAlmostEqual(self.gov.spent_usd, 0.10, places=4)

    def test_an_overrun_is_charged_to_the_run_and_blocks_further_calls(self) -> None:
        self.assertTrue(self.budget.try_spend("analysis", 0.60))
        self.budget.settle("analysis", 0.60, 0.95)  # e.g. a pricier fallback model answered
        self.assertAlmostEqual(self.gov.spent_usd, 0.95, places=4)
        self.assertFalse(self.budget.try_spend("analysis", 0.10))  # the run cap now sees the real spend

    def test_the_run_cap_never_exceeds_ten_dollars(self) -> None:
        gov = run_agent.CostGovernor(max_budget_usd=9.50)
        spent = 0.0
        while gov.try_spend(0.17):  # worst-case Sonnet 5.5 analysis reservations
            spent += 0.17
        self.assertLessEqual(spent, 9.50)


if __name__ == "__main__":
    unittest.main()
