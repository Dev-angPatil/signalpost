# GitHub Copilot Instructions for Signalpost

Signalpost is an autonomous company intelligence system for Norwegian enterprises built for the Builderr Signalpost Challenge.

## System Overview
- **Input:** 9-digit Norwegian organisation numbers (*organisasjonsnummer* / *orgnr*).
- **Core Pipeline:** Anchor-Enrich-Verify-Synthesize.
- **Output:** Terminal JSONL envelopes complying strictly with `OUTPUT_CONTRACT.md`, external observations (`observations.jsonl`), full dossiers (`profiles.jsonl`), and execution reports (`report.json`).

## Key Principles & Anti-Hallucination Guarantees
1. **Immutable Root of Trust:** The 9-digit orgnr anchors the company via Brønnøysundregistrene Enhetsregisteret (`data.brreg.no`). Web discovery produces candidates only.
2. **Zero Fabricated Financials:** Direct statutory figures from Regnskapsregisteret. Missing lines must emit `availability: "not_available"`, never `0.0`.
3. **Strict Conflict Gate:** If any candidate page displays a 9-digit number that does not match the company, its registered sub-units, or its group, flag `conflict: True` and quarantine immediately.
4. **Hard On-Site Proof:** Websites not in official registries require the orgnr, a sub-unit orgnr, or registered street address + postcode in contiguous phrase with the full legal name.
5. **Evidence Lineage:** Every published claim references an evidence record with canonical URL, UTC timestamp, SHA-256 content hash, and verbatim claim span.
6. **Cost Governance:** Zero-cost ($0.00) default. When third-party keys are supplied, spend is capped at $9.50 USD with atomic spend/settle and circuit breakers.

## Development Workflows
- **Dependency & Env Management:** Managed via `uv` (`uv sync --locked`).
- **Hermetic Tests:** `make test` runs 642 tests offline across 5 tiers with blackhole proxies.
- **Single Test Execution:** `PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests/test_e2e_tier1.py`.
- **Python Version:** Python 3.12 (`.python-version`).
