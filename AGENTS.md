# AGENTS.md — AI Agent Context & Engineering Guidelines

This document provides complete system context, architectural invariants, code conventions, and operational workflows for AI coding agents (Antigravity, Cursor, Copilot, Claude Code, Aider, etc.) working on the **Signalpost** repository.

---

## 1. Project Purpose & Challenge Context

**Signalpost** is an autonomous Norwegian corporate intelligence system built for the **Builderr Signalpost Challenge**.
- **Objective:** Given a batch of 9-digit Norwegian organisation numbers (*organisasjonsnummer* / *orgnr*), retrieve corporate intelligence, audited annual accounts, executive leadership, physical branch locations, active job vacancies, press news, and verified web/social footprints.
- **Evaluation Criteria (100 Points Total):**
  - **Recall & Coverage (50 pts):** Verified web/social presence, job postings, company news, and locations across companies.
  - **Precision & Evidence (30 pts):** 100% correct company attribution; zero wrong-company publications; valid source URLs, timestamps, and content hashes.
  - **Useful Summary (12 pts):** Clear, decision-relevant executive synthesis without unsupported claims.
  - **Usability (8 pts):** Scannable, mobile-responsive HTML dossier with evidence verification.
- **Disqualification Conditions:**
  - Material wrong-company publication (attributing Company B's website, jobs, or social media to Company A).
  - Fabricating financial line items (turning missing accounts into 0.0).
  - Exceeding the third-party API spend ceiling ($10.00 USD per 1,000-company run).
  - Batch timeouts (exceeding 45 minutes wall-clock time).

---

## 2. Immutable Architectural Invariants

Whenever you modify or add code, you **MUST** strictly uphold these 6 rules:

### Rule 1: The 9-Digit Orgnr is the Immutable Root of Trust
- Data directly from Brønnøysundregistrene (`data.brreg.no`) anchors the company's identity.
- Data from the open web (search engines, domain guessing, social media, press) is **only a candidate**.
- A candidate domain is **NEVER** published based on name alone. It requires hard proof: the primary 9-digit orgnr, a registered sub-unit orgnr, or registered street address + postcode in contiguous phrase with the full legal name.

### Rule 2: Zero Fabricated Financials
- Never coerce missing accounts or missing line items to `0.0`.
- If an entity has no filed statutory accounts in Regnskapsregisteret (e.g. newly registered, or exempt sole proprietorship), emit `availability: "not_available"` or `"not_applicable"` with explicit legal rationale.

### Rule 3: Strict Conflict Gate
- Any candidate website or page that displays a 9-digit number (`\b[89]\d{8}\b`) that does **not** belong to the target company, its registered sub-units, or its group entities **MUST** be flagged with `conflict: True` and immediately quarantined.
- Never weaken the conflict gate to increase recall; precision is prioritized over coverage.

### Rule 4: Cryptographic Evidence Lineage
- Every published claim in `OUTPUT_CONTRACT.md` **MUST** reference a valid evidence item.
- Every evidence record must contain:
  1. `id`: Deterministic identifier (e.g. `ev-registry-0`)
  2. `source_url`: Canonical URL
  3. `source_class`: `official_registry` | `company_owned` | `licensed_feed`
  4. `retrieved_at`: ISO-8601 UTC timestamp
  5. `content_sha256`: SHA-256 hex digest of the raw response payload
  6. `claim_span`: Verbatim textual excerpt supporting the claim

### Rule 5: Strict Cost Ceilings & Zero-Cost Baseline
- By default, Signalpost runs with **$0.00 USD declared cost** using open Norwegian endpoints.
- All paid providers (Serper, Brave, Google Places, Anthropic) are guarded by the thread-safe `CostGovernor` and `BudgetSlices`.
- The hard circuit breaker trips at **$9.50 USD** to guarantee the $10.00 challenge ceiling is never breached.

### Rule 6: Monotonic Run Clocks & Deadlines
- Evaluations run under a 45-minute budget.
- The orchestrator checks `web_discovery.CLOCK`:
  - **Soft Deadline (34 min):** Halts optional web discovery and press crawling.
  - **Hard Deadline (41 min):** Halts AI analysis and background threads; immediately serializes outputs.

---

## 3. Repository Architecture & File Map

```
signalpost/
├── run_agent.py                    # Primary CLI runner & batch orchestrator
├── Makefile                        # Fast hermetic test target (make test)
├── pyproject.toml                  # Pinned Python dependencies (Python >= 3.12)
├── ARCHITECTURE.md                 # Master system architecture specification
├── OUTPUT_CONTRACT.md              # Output JSON schema definition
├── docs/
│   ├── architecture/               # Deep-dive architecture specifications
│   │   ├── SYSTEM_OVERVIEW.md      # Pipeline orchestration & worker lifecycle
│   │   ├── IDENTITY_AND_GATING.md  # Conflict gate, name parsing, site proofing
│   │   ├── DATA_PIPELINE_AND_STORAGE.md # Envelopes, profiles, observations
│   │   ├── INTEGRATIONS_AND_CONNECTORS.md # Brreg, NAV, Wikidata, Search, Claude
│   │   └── COST_AND_RESOURCE_GOVERNANCE.md # Budget slices, clocks, rate limits
│   └── index.html                  # Live static showcase dossier
├── src/norway_company_agent/
│   ├── official.py                 # Enhetsregisteret & Regnskapsregisteret REST client
│   ├── announcements.py            # Kunngjøringer statutory notice scraping
│   ├── discovery.py                # Multi-provider search resolver & domain guessing
│   ├── web_discovery.py            # Priority ordering, Wikidata SPARQL, clock & budget
│   ├── identity.py                 # CompanyNumberProfile, assess_website_identity, conflict gate
│   ├── site_proof.py               # Hard on-site orgnr and address proofing
│   ├── publication_gates.py        # Strict claim family publication rules
│   ├── website.py                  # Polite bounded crawler & social extractor
│   ├── nav_jobs.py                 # NAV Arbeidsplassen vacancy feed connector
│   ├── job_postings_ld.py          # Schema.org JobPosting JSON-LD extractor
│   ├── news.py                     # Google News RSS press headline discovery
│   ├── site_feeds.py               # WordPress REST & RSS/Atom company news
│   ├── official_external.py        # Google Places & YouTube observation extractors
│   ├── observations.py             # Exact-identity external observations
│   ├── ai_enrich.py                # Claude Sonnet/Haiku fact-sheet grounded analysis
│   ├── synthesis.py                # Financial ratios, revenue trajectory & summaries
│   ├── contract.py                 # OUTPUT_CONTRACT claim & evidence assembly
│   ├── batch.py                    # Batch parsing, checkpointing, shared domain quarantine
│   ├── refresh.py                  # Idempotent profile diffing engine
│   ├── evidence.py                 # SHA-256 hashing & ISO timestamp utilities
│   └── http.py                     # Resilient HTTP client with retry and pooling
├── tests/                          # 5-tier test suite (690 tests, 100% offline)
├── ui/                             # HTML template & data model for showcase browser
└── out/                            # Target directory for execution artifacts
```

---

## 4. Development & Testing Commands

### Setup
```bash
uv sync --locked
cp .env.example .env
```

### Running Tests
The test suite is **100% hermetic and offline**. It blocks all network access via a blackhole proxy (`127.0.0.1:9`).

```bash
# Run complete test suite (690 tests in ~15s)
make test

# Run a specific test file
PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests/test_e2e_tier1.py

# Run a specific test case
PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests.test_e2e_tier1.TestE2ETier1.test_financials_extraction
```

### Running the Agent CLI
```bash
# Single company research
uv run --locked --env-file .env python run_agent.py \
  --org 923609016 \
  --output out/test-envelope.jsonl \
  --report out/test-report.json

# Batch research with checkpointing and showcase
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

## 5. Coding Conventions for AI Agents

1. **Python 3.12+ Standard Library Focus:**
   - Prefer lightweight standard library mechanisms (`urllib.request`, `http.client`, `dataclasses`, `json`) over heavy runtime dependencies.
   - Fast parsing uses `BeautifulSoup4`, `lxml`, and `trafilatura`.
2. **Deterministic Data Handling:**
   - Internal profile data is passed as clean dictionaries and dataclasses.
   - Always validate keys using `.get()` with safe defaults to prevent `KeyError` crashes in concurrent worker pools.
3. **Hermetic Test Practices:**
   - When writing tests, **never** make real outbound network requests.
   - Mock all HTTP interactions using `unittest.mock.patch` or frozen response fixtures in `tests/fixtures/`.
   - Ensure `make test` continues to pass 100% of tests.
4. **Never Bypass Publication Gates:**
   - If adding a new claim type, register it in `OUTPUT_CONTRACT.md`, `contract.py`, and `publication_gates.py`.
   - Ensure it includes an evidence linkage with an active SHA-256 content hash.
