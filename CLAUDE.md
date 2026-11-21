# CLAUDE.md — Claude Code Guidelines for Signalpost

This document provides project context, essential commands, code architecture, and development rules for Claude Code and Anthropic assistants working on **Signalpost**.

---

## 🛠️ Essential Commands

### Environment Setup
```bash
uv sync --locked
cp .env.example .env
```

### Running Tests (100% Offline & Hermetic)
```bash
# Run all 727 tests across Tiers 1–5 (completes in ~25 seconds)
make test

# Run a single test module
PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests/test_e2e_tier1.py

# Run a single test function
PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests.test_e2e_tier1.TestE2ETier1.test_financials_extraction
```

### Running the Agent CLI
```bash
# Research a single 9-digit Norwegian organisation number
uv run --locked --env-file .env python run_agent.py \
  --org 923609016 \
  --output out/test-envelope.jsonl \
  --report out/test-report.json

# Run batch evaluation with checkpointing, observations, and HTML showcase
uv run --locked --env-file .env python run_agent.py \
  --input tests/fixtures/smoke-companies.jsonl \
  --output out/envelopes.jsonl \
  --profiles-output out/profiles.jsonl \
  --observations-output out/observations.jsonl \
  --report out/report.json \
  --showcase out/showcase.html \
  --checkpoint-every 50
```

---

## 🏛️ System Architecture Summary

Signalpost is an autonomous corporate intelligence pipeline built for the **Builderr Signalpost Challenge**. It converts 9-digit Norwegian organisation numbers into audited corporate dossiers adhering to `OUTPUT_CONTRACT.md`.

### Core Pipeline Stages:
1. **Identity Anchor (`official.py`):** Queries Brønnøysundregistrene Enhetsregisteret (`data.brreg.no`) to anchor legal name, business form, addresses, and statutory email/homepage.
2. **Official Enrichment (`official.py`, `announcements.py`):** Extracts audited annual accounts from Regnskapsregisteret (newest-first, company accounts preferred over group), management/board roles, physical branch workplaces (`underenheter`), and statutory Kunngjøringer notices.
3. **Web Discovery (`discovery.py`, `web_discovery.py`):** Multi-source candidate waterfall (Wikidata SPARQL, statutory email domain, Serper/Brave/Google Search, Google Places, algorithmic `.no`/`.com` domain guessing).
4. **Anti-Contamination & Conflict Gates (`identity.py`, `site_proof.py`):**
   - Candidate sites MUST pass legal name check and hard proof (orgnr, sub-unit number, or contiguous street address + postcode + legal name phrase). The one exception is a site the company itself registered in Brønnøysund (homepage or e-mail domain) whose domain spells its whole legal name and whose page names it (`site_proof.registry_domain_name_proof`); it still fails on any foreign org number or a parked/placeholder page. Guessed/searched domains never use it.
   - If a candidate displays any 9-digit number belonging to a different company, it is immediately quarantined with `conflict: True`.
   - Batch-wide quarantine (`quarantine_shared_domains`) revokes shared domains unless the company's own orgnr appears on-site.
5. **Crawl & Extraction (`website.py`, `nav_jobs.py`, `news.py`):** Extracts social profiles (with distinctive handle checks), job vacancies from the official NAV feed, structured `JobPosting` JSON-LD schemas, and same-domain news feeds.
6. **Executive Synthesis (`synthesis.py`, `ai_enrich.py`):** Computes multi-year revenue growth trajectories, CAGR, and badges. When Claude Sonnet is enabled, it receives a numbered fact sheet and MUST cite valid fact IDs (`[fact-X]`); uncited statements are pruned.
7. **Lineage & Contract (`contract.py`, `evidence.py`):** Emits terminal JSONL envelopes where every claim references an evidence record with canonical URL, UTC timestamp, and SHA-256 hash.

---

## ⚠️ Non-Negotiable Rules

1. **Zero Fabricated Financials:** Never coerce missing financial line items to 0.0. If accounts are missing, availability must be `not_available` or `not_applicable`.
2. **Never Weaken Conflict Gates:** Never remove or bypass the check that quarantines pages displaying foreign 9-digit org numbers. Precision is prioritized over recall.
3. **Strict Evidence Lineage:** Every single published claim must map to an evidence ID containing a SHA-256 content hash and verbatim claim span.
4. **Hermetic Test Isolation:** Tests in `tests/` must **NEVER** attempt outbound network calls. All external interactions must be mocked.
5. **Cost Ceilings:** Honor the $9.50 circuit breaker in `run_agent.py` and per-task `BudgetSlices` in `web_discovery.py`. Default runs must execute at $0.00 declared cost.

---

## 💻 Code Style & Standards

- **Python Version:** Pinned to Python 3.12 (`.python-version`).
- **Dependencies:** Managed strictly through `uv.lock`. Do not add unpinned or heavy external dependencies.
- **Networking:** Standard library `urllib` / `http.client` through `src/norway_company_agent/http.py`.
- **Parsing:** `BeautifulSoup4`, `lxml`, and `trafilatura`.
- **Typing:** Use Python 3.12 type annotations (`list[str]`, `dict[str, Any]`, `str | None`).
