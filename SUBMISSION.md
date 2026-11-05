# Signalpost submission package

This repository is submitted as a reproducible agent, not as a static company
profile archive. Builderr supplies the official organisation-number batch at run
time.

## Submission contents

Send `submit@builderr.ai`:

1. Public repository URL: `https://github.com/Dev-angPatil/signalpost`
2. Exact commit hash to freeze.
3. Live Hosted Evaluator Dashboard: `https://signalpost-showcase.onrender.com`
4. Committed Static Showcase (no execution needed): `docs/index.html`
5. The 100-company smoke-test report: `out/smoke-report.json`.
6. The one-command evaluator invocation below.
7. Agent name and contact information.
8. Models, APIs, licences, source-rights policy, and expected cost per batch.

The repository may contain larger local artifacts such as
`out/profiles-1000.jsonl`, but those are for local validation and do not replace
the official runtime batch.

## Evaluator command

```bash
uv sync --locked
uv run --locked --env-file .env python run_agent.py \
  --input <input.jsonl> \
  --output <output.jsonl> \
  --profiles-output <profiles.jsonl> \
  --observations-output <observations.jsonl> \
  --report <report.json> \
  --showcase <showcase.html> \
  --checkpoint-every 50
```

The evaluator supplies any approved server-side secrets through environment
variables. No private API key is committed or required for the registry and
company-site path.

## Declared sources & optional paid keys

- **Authoritative Norwegian Registers ($0.00 Cost — No API Keys Required):**
  - Brønnøysundregistrene Enhetsregisteret & Regnskapsregisteret open REST APIs (`data.brreg.no`).
  - Brønnøysund Kunngjøringer public statutory notice scrapers (`w2.brreg.no`).
  - Statutory registered email domains (`epostadresse`) for direct, zero-cost corporate site discovery.
- **Optional Web Search Discovery (Choose ONLY ONE of these 3 options, or none):**
  - **Option 1: Google Search via Serper (`SERPER_API_KEY`)**
    - High-precision Google search adapter via Serper (`https://google.serper.dev/search`).
    - *Cost:* ~$0.001 / query. (100 companies: ~$0.08 USD; 1,000 companies: ~$0.60 USD).
  - **Option 2: Brave Search API (`BRAVE_SEARCH_API_KEY`)**
    - Privacy-first independent web index adapter (`https://api.search.brave.com/res/v1/web/search`).
    - *Cost:* ~$0.005 / query (defaulted automatically, or set `BRAVE_SEARCH_COST_PER_REQUEST_USD`).
  - **Option 3: Google Custom Search API (`GOOGLE_SEARCH_API_KEY` & `GOOGLE_SEARCH_ENGINE_ID`)**
    - Official Google Cloud Programmable Search Engine JSON API (`https://customsearch.googleapis.com/customsearch/v1`).
    - *Cost:* 100 free queries/day, then ~$0.005 / query.
  - *Provider Auto-Detection:* The agent automatically uses whichever ONE key is supplied in `.env`. Alternatively, set `WEB_SEARCH_PROVIDER=serper|brave|google|auto` to explicitly declare your preferred engine.
- **Optional Platform Footprint Keys (Enrichment only):**
  - `GOOGLE_PLACES_API_KEY` & `GOOGLE_PLACES_COST_PER_REQUEST_USD`: Official Places API for conservative physical location & rating footprints (~$0.017 / query).
  - `YOUTUBE_DATA_API_KEY`: Official YouTube Data v3 API for verified corporate subscriber/video footprints (free quota).

Every published external observation must pass the exact-entity identity gate (`verify_candidate_identity`) and carry a source URL, retrieval timestamp, SHA-256 hash, evidence span, and identity proof. Search results are transient and never serve as claim evidence on their own.

## Cost, limits & circuit breaker

- **Default Execution Cost:** **$0.00 USD** (100% free registry and verified corporate email domain discovery).
- **Hard Circuit Breaker (`CostGovernor`):** Built-in thread-safe budget controller actively bounds cumulative third-party spend at **$9.50 USD** (configurable via `MAX_THIRD_PARTY_COST_USD`). If spend approaches the challenge budget, paid requests automatically trip the breaker and safely fallback to free discovery. Under no circumstances will spend exceed the $10.00 limit.
- **Request & Runtime Budgets:** Audited against challenge bounds (<= 20 reqs/company, <= $10.00 cost, <= 45 minutes runtime). Exactly one terminal envelope is emitted for every input organisation number.

## Security

- Never put API keys in this repository, command arguments, URLs, reports, or
  generated evidence.
- Use `.env.example` as the variable-name template only.
- If a credential was ever exposed during development, revoke or rotate it
  before publishing the repository.
