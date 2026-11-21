# Signalpost: Autonomous Norwegian Company Intelligence Agent

> **Autonomous corporate research, audited annual accounts, executive leadership, and verified company evidence for Norwegian enterprises.**
>
> 🌐 **Live company browser (100-company smoke run):** [https://dev-angpatil.github.io/signalpost/](https://dev-angpatil.github.io/signalpost/): find a company, compare up to four, and press **Verify** on any fact to see its source, retrieval time, SHA-256 and quoted text. Works on desktop and phone.  
> 📁 **Same page in the repo:** [`docs/index.html`](docs/index.html) (open directly in a browser, no code needed)

**Evaluator command** (single command after setup; zero external keys required, optional keys in `.env.example`):

```bash
uv sync --locked
cp .env.example .env
uv run --locked --env-file .env python run_agent.py \
  --input <input.jsonl> --output <envelopes.jsonl> \
  --profiles-output <profiles.jsonl> --observations-output <observations.jsonl> \
  --report <report.json> --showcase <showcase.html> --checkpoint-every 50
```

Every run also writes a company browser for its own results (`--showcase`, or `<envelopes>.html` next to the envelopes when the flag is left out): open it in any browser, no server needed. See *Company browser* below.

Signalpost is an autonomous company intelligence agent built for the **Builderr Signalpost Challenge**. Given any 9-digit Norwegian organisation number (*organisasjonsnummer*), it anchors identity in official Norwegian public registries, audits annual financial filings, extracts executive management, verifies physical workplaces, and crawls company websites under strict anti-contamination identity gates.

---

## 🌟 Key Capabilities & Anti-Hallucination Guarantees

- **Anchor-Enrich-Verify Pipeline:** Grounded strictly in official Norwegian government APIs (`data.brreg.no` Enhetsregisteret & Regnskapsregisteret).
- **Zero Fabricated Financials:** Direct audited statutory figures (Revenue, EBIT, Net Result, Assets, Debt, Equity) with reporting periods and currencies. Absence is never converted to zero.
- **Strict Anti-Contamination Gate:** Web crawling requires 9-digit orgnr corroboration, sub-unit matching, or exact legal name verification before claims are published. Uncorroborated domains are quarantined as `ambiguous`.
- **Dual-Action Conflict Gate:** Rejects candidate sites displaying 9-digit numbers that match none of the company's registered numbers, eliminating false matches across namesake companies.
- **Cryptographic Lineage:** Every single published fact is linked to a unique evidence span, canonical source URL, retrieval timestamp, and SHA-256 hash.
- **Single Reproducible Command:** Runs deterministically out-of-the-box via `uv` with **$0.00 declared external API cost** when optional providers are disabled.
- **100% Contract Compliant:** Emits terminal JSONL envelopes adhering to `OUTPUT_CONTRACT.md` with explicit states (`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`).

### Publication Gates

| Claim Family | Published Only When |
| :--- | :--- |
| **Official website (registry-declared)** | Legal name on the homepage, the org number / registered address (with the company's own name beside it) on the site, or a domain that spells the whole legal name with the name on the page (no foreign org number, not parked or a placeholder). An unreadable registered site (after one retry) is `failed`. |
| **Official website (search, email domain, guessed domain)** | The org number, a registered sub-unit number, or the registered street address + postcode **with the full legal name beside it as one phrase** appears on the site (homepage, `/kontakt`, `/kontakt-oss`, `/om-oss`, `/personvern`, `/kjopsbetingelser`, `/vilkar`, `/contact`). Scattered words are rejected. Uncorroborated name matches are quarantined as `ambiguous`. |
| **Company page on a group / brand site** | The page URL is published, but only that specific page's content is attributed to the company: group-wide news, contacts, and career links are dropped. |
| **Social profiles** | Hyperlinked from a verified site (or declared on an orgnr-verified NAV vacancy) **and** the handle starts or ends with the distinctive legal name. Site-builder (`twitter.com/squarespace`) and housing-manager handles are rejected. |
| **Job postings** | NAV Arbeidsplassen ad whose `employer.orgnr` equals the org number or a registered sub-unit, or an active posting on the verified site from the last 120 days. |
| **Company news** | Dated same-domain article on the verified site (RSS / WordPress feed, JSON-LD, or article URL); CMS placeholder posts are dropped. Press headlines must carry the full legal name (name without "AS" accepted only for firms with 10+ employees or NOK 20M+ revenue). |
| **Annual accounts** | Newest filing first, company accounts (`SELSKAP`) preferred over group accounts (`KONSERN`) for the same period. |
| **Model analysis (optional, Claude Sonnet 5.5)** | Works strictly from a numbered fact sheet of already-published evidence. Every sentence must cite fact IDs; uncited or hallucinated statements are pruned. Skipped without an API key. |

---

## 🖥️ Company browser (find, compare, verify)

Every run writes one HTML page for its results, built from the same envelopes Builderr scores ([`ui/`](ui/)). The hosted copy of the 100-company smoke run is at **https://dev-angpatil.github.io/signalpost/** (also [`docs/index.html`](docs/index.html)).

- **Find:** search by name, org number, place or person. Filter by industry, municipality, finances, legal form and signals (verified website, hiring, news, board or capital change, bankruptcy), plus **All five areas** and **Can't tell**. Sort by information, revenue, growth, margin, equity ratio, employees or latest activity. Export the list as CSV.
- **Compare:** tick up to four companies and open them side by side; the strongest value in each row is marked.
- **Verify:** every published fact has a **Verify** button (key figures, website, social profiles, CEO and board chair, news, jobs, public notices, and the full *Published facts* list). It opens the evidence record the fact cites: source address, retrieval time, reporting period, SHA-256 and the quoted text, with buttons to open the source (at the quoted text where the browser supports it) and copy the quote or hash.
- **Coverage:** each company shows its five areas (company record, annual accounts, people & places, website, public footprint) as *found*, *not found*, *can't tell*, *blocked* or *could not be read*, with the reason. Nothing missing is shown as zero.
- **Run panel:** above the list, the run's checks recomputed from the envelopes (one envelope per input, every published fact backed by a hashed source, no value on an unavailable claim) and the report's qualification checks, companies found per area, requests, runtime, cost, run id and commit.
- **Desktop and phone:** one-column layout on phones, dark mode, keyboard navigation (`/` focuses search), links to a company (`#org=<orgnr>`).
- **Any batch size:** up to 250 companies the page is one portable file; above that each company's evidence is in `<page>_files/` and loads when the company is opened (works from `file://` and static hosting).

Rebuild the page from saved outputs without re-running the agent:
```bash
PYTHONPATH=src uv run --locked python ui/build_ui.py \
  --input out/smoke-profiles.jsonl --envelopes out/smoke-envelopes.jsonl \
  --report out/smoke-report.json --output out/browser.html
```

---

## ✅ Latest verified results

| Run | Code | Result | Live re-check of every external claim |
|---|---|---|---|
| 100-company smoke (`out/smoke-*`, `docs/index.html`) | `346211b` | 100/100 envelopes, 0 source errors, 193 s, 1,569 requests, $0.00, every qualification check true; re-run with `--prior-profiles` reports 0 changes | 149/149 |
| 1,000 companies (local test) | `584a67d` (agent code unchanged since) | 1,000/1,000 envelopes in 25 min, 13,132 requests, $0.00, 0 source errors, every qualification check true | 1,361/1,368 (99.5%); the rest are checker artefacts |
| Builderr's own 100-company sample (builderr.ai/signalpost) | `584a67d` | 73 of Builderr's 97 websites found on the same domain | 669/673 (99.4%) after re-checking transient fetch failures |

The live re-check is `scripts/verify_claims_live.py`: it re-opens every cited page with a plain HTTP client and tests the claim against it, independently of the agent's own gates.

---

## 🚀 Quickstart & One-Command Execution

Follow these steps to run the agent. Without keys it costs $0.00 and honours robots.txt.

### Prerequisites
- Python >= 3.12 (`.python-version` pins Python 3.12.13)
- `uv` (fast Python package manager)
- Docker (optional)

### 0. Clean-Machine Setup
From the repository root, install locked dependencies:
```bash
uv sync --locked
cp .env.example .env
```

Search and platform integrations are optional; leave their keys blank to run the zero-cost statutory registry and company-site path. When a paid provider is enabled, set its per-request estimate so the report declares the cost.

### 1. Single Company Research
Research any live Norwegian company:
```bash
uv run --locked --env-file .env python run_agent.py --org 923609016 --output out/test-envelope.jsonl --report out/test-report.json
```

### 2. Batch Research & Evaluation
Run across an input batch of organisation numbers with streaming checkpointing and the company browser:
```bash
uv run --locked --env-file .env python run_agent.py \
  --input tests/fixtures/smoke-companies.jsonl \
  --output out/envelopes.jsonl \
  --profiles-output out/profiles.jsonl \
  --observations-output out/observations.jsonl \
  --report out/report.json \
  --showcase out/showcase.html \
  --checkpoint-every 50
```

### 3. Change Detection & Refresh Replay
Verify deterministic refresh idempotency between profile versions:
```bash
uv run --locked --env-file .env python scripts/run_refresh_replay.py \
  --manifest tests/fixtures/refresh-snapshots.json \
  --output out/refresh-demo.json
```

---

## 🧪 Automated Testing

Run the complete hermetic test suite offline:
```bash
make test
```

The test command runs with proxy isolation (`HTTP_PROXY=http://127.0.0.1:9`), disables network access, and executes all **739 passing tests** across 5 tiers in under 30 seconds.

### Individual Testing Tiers:
- **Tier 1 (Feature Coverage):** `PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests/test_e2e_tier1.py`
- **Tier 2 (Boundary & Corner Cases):** `PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests/test_e2e_tier2.py`
- **Tier 3 (Cross-Feature Interactions):** `PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests/test_e2e_tier3.py`
- **Tier 4 (Real-World Scenarios & Cohorts):** `PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests/test_e2e_tier4.py`
- **Tier 5 (Adversarial Hardening & Security):** `PYTHONPATH=src:. uv run --offline --no-env-file --locked python -m unittest tests/test_e2e_tier5.py`

### Research-Agent Evaluation Fixtures
Evaluate profile corpora against deterministic QA fixtures:
```bash
PYTHONPATH=src:. uv run --offline --no-env-file --locked python scripts/evaluate_research_agent.py \
  --input out/profiles-1000.jsonl \
  --suite tests/fixtures/research-agent-suite-v3-fresh.json \
  --output out/research-evaluation.json \
  --workspace out/research-workspace.json
```

---

## 🐳 Docker Support

Build the container image:
```bash
docker build -t signalpost:local .
```

Run batch research in Docker:
```bash
mkdir -p out
docker run --rm --env-file .env -v "$PWD/out:/app/out" signalpost:local \
  --org 923609016 --output out/test-envelope.jsonl --report out/test-report.json
```

---

## 📊 Evaluation Contract & Output Schema

Each output envelope in `out/envelopes.jsonl` adheres strictly to [`OUTPUT_CONTRACT.md`](OUTPUT_CONTRACT.md):

```json
{
  "organisation_number": "923609016",
  "run": {
    "run_id": "signalpost-1790627168",
    "started_at": "2026-09-28T20:26:08Z",
    "completed_at": "2026-09-28T20:26:35Z",
    "terminal_status": "completed"
  },
  "claims": [
    {
      "field": "legal_name",
      "value": "EQUINOR ASA",
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-registry-0"]
    },
    {
      "field": "revenue",
      "value": 67956000000.0,
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-financials-0"]
    }
  ],
  "evidence": [
    {
      "id": "ev-financials-0",
      "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
      "source_class": "official_annual_accounts",
      "retrieved_at": "2026-09-28T20:26:13Z",
      "content_sha256": "98489e866c0fe849cf7467efe87041696c200763e6b40a5c4194e089346d1593",
      "claim_span": "Salgsinntekter: 67956000000 NOK for period 2024-01-01 to 2024-12-31"
    }
  ],
  "changes": [],
  "errors": [],
  "operations": {
    "requests": 14,
    "runtime_ms": 18450,
    "third_party_cost_usd": 0.0
  }
}
```

---

## 📁 Repository Structure

```
├── ARCHITECTURE.md                 # Master system architecture & component topology
├── docs/architecture/              # Modular architecture deep-dive specifications
│   ├── README.md                   # Architecture documentation index
│   ├── SYSTEM_OVERVIEW.md          # End-to-end pipeline & execution lifecycle
│   ├── IDENTITY_AND_GATING.md      # Anti-contamination & conflict gates
│   ├── DATA_PIPELINE_AND_STORAGE.md# Output contracts, schemas & SHA-256 lineage
│   ├── INTEGRATIONS_AND_CONNECTORS.md# Public registers, feeds & search providers
│   └── COST_AND_RESOURCE_GOVERNANCE.md# CostGovernor, budget slices & deadlines
├── DESIGN.md                       # Nordic Corporate Intelligence UI design tokens
├── OUTPUT_CONTRACT.md              # Minimal output contract specification
├── SUBMISSION.md                   # Builderr official evaluator declaration
├── pyproject.toml                  # Pinned dependencies and tool configurations
├── run_agent.py                    # Primary CLI runner & pipeline orchestrator
├── src/
│   └── norway_company_agent/
│       ├── ai_enrich.py            # Claude Sonnet/Haiku fact-sheet synthesis
│       ├── announcements.py        # Brønnøysund Kunngjøringer public notice scraping
│       ├── batch.py                # Batch reading, validation & checkpointing
│       ├── contract.py             # OUTPUT_CONTRACT claims & evidence mapping
│       ├── discovery.py            # Multi-provider search & domain guessing
│       ├── evidence.py             # SHA-256 cryptographic hashing & timestamping
│       ├── http.py                 # Resilient HTTP transport with connection pooling
│       ├── identity.py             # CompanyNumberProfile & conflict gate
│       ├── job_postings_ld.py      # Schema.org JobPosting JSON-LD parsing
│       ├── nav_jobs.py             # NAV Arbeidsplassen public vacancy feed connector
│       ├── news.py                 # Google News RSS press headline discovery
│       ├── observations.py         # Exact-identity company site & hiring observations
│       ├── official.py             # Brønnøysund & Regnskapsregisteret REST API client
│       ├── official_external.py    # Google Places & YouTube observation extractors
│       ├── publication_gates.py    # Strict claim family publication criteria
│       ├── refresh.py              # Idempotent diff and change detection
│       ├── site_feeds.py           # Same-domain RSS/Atom & WordPress REST news
│       ├── site_proof.py           # Hard on-site orgnr & address proof; registry-declared domain rule
│       ├── synthesis.py            # Financial ratios, revenue trajectory & summaries
│       ├── web_discovery.py        # Priority sorting, Wikidata, clocks & budgets
│       ├── website.py              # Bounded polite crawler & social links extractor
│       └── workspace.py            # Saved query & pin management
├── scripts/                        # Evaluation, verification & replay utilities
├── tests/                          # 5-tier hermetic test suite (739 passing tests)
├── ui/                             # Company browser written after every run
│   ├── build_ui.py                 # Builds the page (inline evidence, or per-company files above 250)
│   ├── envelope_view.py            # Verify payloads, coverage states & run checks from the envelopes
│   ├── data_model.py               # Profile fields the page displays
│   └── template.html               # The page: find, compare, verify; desktop & phone
└── out/                            # Generated run artifacts (envelopes, profiles, report)
```

---

## 📄 Submission Details

- **Hosted company browser (smoke run):** https://dev-angpatil.github.io/signalpost/
- **Evaluator Command:** `uv run --locked --env-file .env python run_agent.py --input <input.jsonl> --output <output.jsonl> --profiles-output <profiles.jsonl> --observations-output <observations.jsonl> --report <report.json> --showcase <showcase.html> --checkpoint-every 50`
- **Supported APIs:** Official Brønnøysundregistrene Enhetsregisteret & Regnskapsregisteret Open REST APIs (`data.brreg.no`), Kunngjøringer notices (`w2.brreg.no`), NAV Arbeidsplassen vacancy feed (`pam-stilling-feed.nav.no`), optional web search discovery (supports Serper, Brave, or Google Custom Search interchangeably), optional Google Places and YouTube APIs, and optional Anthropic Claude AI enrichment.
- **Expected Cost per Run:** Registry and company-site runs are strictly **$0.00 USD**. When optional paid keys are enabled, the thread-safe `CostGovernor` enforces a hard **$9.50 USD** circuit breaker well below the **$10.00 USD** challenge limit.
- **Scale Deliverables:** 1,000 completed company profiles in about 25 minutes (13 requests per company), within the 45-minute runtime budget, plus the company browser for the run.
