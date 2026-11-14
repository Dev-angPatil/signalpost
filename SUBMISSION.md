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
- **NAV arbeidsplassen job postings ($0.00 — no personal credential):**
  - Official NAV job-vacancy feed `https://pam-stilling-feed.nav.no/api/v1/feed` and `/api/v1/feedentry/{uuid}`. The bearer token is NAV's openly published public token (`/api/publicToken`), fetched at run time.
  - One shared index of ACTIVE ads modified in the last `NAV_FEED_DAYS` (default 45) is built in the background at batch start. Employer-name matches are candidates only; a posting is published **only when `employer.orgnr` equals the company's organisation number or one of its registered sub-units**. Each claim links the public ad page on arbeidsplassen.nav.no.
  - If the feed is unavailable, the agent falls back to the public search endpoint with a global rate limit and 429 back-off, and reports `blocked` rather than `not_available` when throttled.
- **Verified company-site feeds:** WordPress `/wp-json/wp/v2/posts` and `/feed/` on sites that already passed the identity gate; same-domain items with a publication date only.
- **Identity proof for non-registry websites:** a domain guessed from the name or proposed by search is published only if the org number, a sub-unit number, or the registered street address + postcode appears on the site (homepage, `/kontakt`, `/kontakt-oss`, `/om-oss`, `/contact`, `/about`). Name-only matches are quarantined as `ambiguous`.
- **Website discovery (candidates only; every website still needs the org number or registered address on the site itself):**
  - *Wikidata* (CC0, free): one SPARQL query per 150 companies, Norwegian org number (P2333) → official website (P856). Accepted when the site also passes the legal-name check, or on on-site proof.
  - *Registry aliases* (free): recent former names, sub-unit names and brand names seen in search results are used to generate search queries and domain guesses. Identity checks always use the legal name.
  - *Serper* (`SERPER_API_KEY`, ~$0.001/query): name + municipality, then one follow-up query (model-suggested or alias). Search results are never evidence.
  - *Claude Haiku 5.5 rerank* (`ANTHROPIC_API_KEY`): reads the result snippets only; it may only pick URLs that appear in the results and brand names that appear verbatim in them.
  - *Google Places* (`GOOGLE_PLACES_API_KEY`): for companies likely to have a listing; a place's website is a candidate only when the place address matches the registered address. The same lookup feeds the place-summary observation (one request per company).
  - *Claude Haiku 5.5 web search* (`web_search_20250305`, $10/1,000 searches): only for high-priority companies still without a site; only URLs returned by the search tool are kept. Directory, registry and social hosts are blocked.
  - *One website, one company*: a website claimed by several companies in the batch stays published only where that company's own org number is on it.
  - *Social profiles seen only in search results are never published*; social links come from the verified site or from the employer profile of an org-number-matched NAV ad.
- **Career pages:** on a verified site (or an ATS subdomain named after the company), Claude Haiku 5.5 lists open postings; each title must appear verbatim in the page, links must stay on the career site, and only postings in Norway are kept.
- **News:** with `SERPER_API_KEY`, Google News results come from Serper's news endpoint (same strict headline-name rule as the RSS path); otherwise Google News RSS with a circuit breaker.
- **Budget slices:** search $1.25, rerank $0.50, Places $2.00, model web search $1.25, careers $0.50, news $0.75, analysis $3.00, all under the $9.50 run cap. The report lists reserved spend per slice.
- **Optional model analysis — Anthropic Claude API (`ANTHROPIC_API_KEY`, model `claude-sonnet-5-5`):**
  - Runs after all gates, only for companies with a verified website, NAV job postings or dated news (≈15% of a random batch).
  - The model receives a numbered fact sheet built from already-published evidence and returns structured JSON: cited summary sentences, a business description, per-ad job analysis (role category, seniority, employment type, work mode, key requirements) and news event labels.
  - It never decides identity or adds facts: sentences citing no fact, an unknown fact, or a number absent from the cited facts are dropped before publication. Model-derived claims carry evidence pointing at the cited source plus the analysis hash.
  - *Cost:* ≈$0.01–0.02 per analysed company, ≈$2–4 per 1,000-company batch, inside the `MAX_THIRD_PARTY_COST_USD` circuit breaker. Without a key the step is skipped and the template summary is used.
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
