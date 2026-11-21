"""Claude API prices and spend accounting, shared by every model call in the agent.

Prices are USD per million tokens on the Anthropic API (checked 2026-11-21 against the
Claude API reference). Two rules keep the declared cost honest and the run under its cap:

- usage_cost() bills what a response reports: uncached input, cache writes (1.25x input,
  5-minute TTL), cache reads (model-specific multiplier) and output, plus server-side
  web searches.
- reserve_usd() is the most a call can cost (its input estimate plus max_tokens of
  output). Budgets reserve that before a call and settle to the real cost after, so
  calls running in parallel can never push the run past its cap.
"""
from __future__ import annotations

from typing import Any

# (input, output) USD per million tokens.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
}
# Haiku 5.5 bills prompts above 100K tokens at a higher rate.
LONG_PROMPT_PRICES: dict[str, tuple[int, tuple[float, float]]] = {
    "claude-haiku-5-5": (100_000, (0.50, 2.50)),
}
# Cache reads as a share of the input price; 0.1x unless listed.
CACHE_READ_MULT: dict[str, float] = {"claude-sonnet-5-5": 0.05, "claude-opus-5-5": 0.05}
CACHE_WRITE_MULT = 1.25  # 5-minute TTL
WEB_SEARCH_USD = 0.01    # per server-side web search ($10 / 1,000)
# A model missing from the table (e.g. a server-side fallback) is billed at the highest
# listed price, so an unknown model can only over-state cost, never hide it.
UNKNOWN_PRICE: tuple[float, float] = max(PRICES.values())
CHARS_PER_TOKEN = 3.0    # conservative for Norwegian text and JSON (fewer chars per token => more tokens)


def prices(model: str, prompt_tokens: int = 0) -> tuple[float, float]:
    base = PRICES.get(str(model), UNKNOWN_PRICE)
    long = LONG_PROMPT_PRICES.get(str(model))
    if long and prompt_tokens > long[0]:
        return long[1]
    return base


def usage_cost(model: str, usage: Any, searches: int = 0) -> float:
    """What a response cost, from its usage block."""
    uncached = int(getattr(usage, "input_tokens", 0) or 0)
    written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    p_in, p_out = prices(model, uncached + written + read)
    read_mult = CACHE_READ_MULT.get(str(model), 0.1)
    return round((uncached * p_in + written * p_in * CACHE_WRITE_MULT + read * p_in * read_mult + out * p_out) / 1_000_000
                 + searches * WEB_SEARCH_USD, 6)


def reserve_usd(model: str, input_chars: int, max_tokens: int, searches: int = 0) -> float:
    """The most one call can cost: all input written to cache, every output token used."""
    in_tokens = int(input_chars / CHARS_PER_TOKEN) + 1
    p_in, p_out = prices(model, in_tokens)
    return round((in_tokens * p_in * CACHE_WRITE_MULT + max_tokens * p_out) / 1_000_000 + searches * WEB_SEARCH_USD, 6)
