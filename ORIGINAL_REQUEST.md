# Original User Request

## 2026-09-28T20:41:56Z

Extend the Signalpost Norwegian company intelligence agent to achieve an 80+ point competitive score on the Builderr benchmark by implementing bounded multi-page crawling, official public notice events, executive decision synthesis, and generating the full 1,000-profile submission artifact.

Working directory: /home/deu/Coding Repos/Active/signalpost
Integrity mode: demo

## Requirements

### R1. Bounded Multi-Page Web & Hiring Discovery
Crawl verified company websites across up to 4 key sub-paths (/om-oss, /kontakt, /karriere, /investor) to extract business descriptions, direct contact info, open job listings, and verified social profiles without violating identity gates.

### R2. Official Corporate Actions & Announcements Integration
Query official Norwegian public notices (Brønnøysund Kunngjøringer / change announcements) to record dated capital changes, board appointments, and legal filings.

### R3. Decision-Useful Executive Synthesis
Generate structured, grounded profile summaries highlighting business scope, financial health metrics (margins, equity ratios), and governance structure with explicit knowns vs. unknowns.

### R4. Automated Interactive HTML Showcase
Provide an inspectable, mobile-responsive HTML dashboard for entity search, filtering, and direct source provenance verification.

### R5. Scale Execution & Submission Deliverables
Generate at least 1,000 validated company profiles and envelopes conforming to OUTPUT_CONTRACT.md with zero silent drops, within the 45-minute and $0 API cost budget.

## Acceptance Criteria

### Evaluation & Qualification Bar
- [ ] Overall score >= 65/100 (targeting >= 80/100) when evaluated against the competition benchmark harness.
- [ ] Exactly 0 wrong-company publications or quarantined misattributions.
- [ ] Exactly 0 fabricated financial figures; missing statutory values are preserved as not_available or not_applicable.

### Output Integrity & Lineage
- [ ] Exactly 1 terminal JSONL envelope emitted per input organisation number with valid states (available, not_available, blocked, not_applicable, ambiguous, failed).
- [ ] 100% of claims are linked to evidence records with canonical URLs, UTC timestamps, claim spans, and SHA-256 hashes.
- [ ] Idempotent refresh verified: re-running identical snapshots produces zero false change events.

### Verification Suite
- [ ] All 104+ unit and integration tests in tests/test_poc.py pass.
- [ ] Generates out/smoke-report.json for a 100-company batch confirming zero silent drops and runtime < 5 minutes.
- [ ] Generates out/profiles-1000.jsonl containing at least 1,000 completed company profiles.

### Operational Limits
- [ ] Outbound requests <= 2,000 per 100-company run.
- [ ] Declared third-party API cost is $0.00 USD.

## 2026-10-14T09:19:13Z

Improve the Recall score of the Signalpost Norwegian company intelligence agent from 14.88/50 to ≥35/50, raising the overall Builderr C300 benchmark score from 56.08/100 to ≥75/100 (well above the 65/100 qualification bar). The agent researches Norwegian companies using their 9-digit organisation number, querying official registers (Brønnøysundregistrene) and company websites to build verified intelligence dossiers. Precision is already perfect at 30/30 — the problem is purely recall: the agent finds too few facts. Specifically, the evaluator reported: "No dated-news item matched the verified pool. Capture title, direct URL, publication date, and exact company match."

Working directory: /home/deu/Coding Repos/Active/signalpost
Integrity mode: development

Environment variables available:
- `SERPER_API_KEY`: supplied out-of-band during development; never commit or publish the credential (for Google web search discovery)

## Requirements

### R1. Serper Search Adapter & Missing-Website Discovery
Around 90% of Norwegian entities in the universe lack a registered website (`hjemmeside`) in Brønnøysundregistrene.
Integrate a Serper API (`https://google.serper.dev/search`) adapter into `src/norway_company_agent/discovery.py` using `SERPER_API_KEY`.
Wire this search discovery into `run_agent.py` so that when a company has no website in Brreg, the agent queries Serper with `"{name}" {orgnr} {municipality}`, scores the candidate domain via `score_search_candidate()`, and if verified by `identity.py`, crawls it. This unlocks contact, career, hiring, and social claims for the missing 90% of companies.

### R2. Emit Individual Dated Corporate Announcements in `contract.py`
Currently, `announcements.py` already scrapes official Norwegian public notices (capital changes, board shifts, legal filings, insolvencies) from Brreg Kunngjøringer with exact dates (`YYYY-MM-DD`), titles, and links. However, `contract.py` collapses them into a single integer `corporate_announcements_count`.
Update `contract.py` to emit individual dated claims:
- `latest_corporate_action_date` (ISO date)
- `latest_corporate_action_title` (headline string)
- `latest_corporate_action_url` (direct source link)
- `latest_capital_change_date` (if applicable)
- `latest_board_appointment_date` (if applicable)
Each claim must link to `ev-announcements` with valid evidence records.

### R3. Google News RSS Integration for Dated Company News
Integrate the existing Google News RSS connector logic from `scripts/run_google_news_rss_connector.py` directly into the `run_agent.py` enrichment pipeline (`enrich_profile`):
- Query Google News RSS (`https://news.google.com/rss/search?q="{company_name}" when:2y&hl=no&gl=NO&ceid=NO:no`)
- Enforce strict company matching via `exact_title_match()` to prevent wrong-company attribution
- Parse pubDate into ISO 8601 UTC date
- Emit `latest_news_title`, `latest_news_date`, and `latest_news_url` claims backed by RSS XML SHA-256 evidence in `contract.py`

### R4. Company Website News & Press Subpath Extraction
In `src/norway_company_agent/website.py`:
- Add a news category to priority subpath crawling (`/nyheter`, `/aktuelt`, `/pressemeldinger`, `/news`, `/press`)
- Extract article titles and publication dates from `<time datetime="...">`, Schema.org `datePublished`, or Norwegian date text
- Emit `website_news_items` / `latest_news_date` claims with proper SHA-256 page evidence

### R5. Regression Safety, Operational Limits & Verification
- 100% precision safety: zero wrong-company publications across all tests.
- Zero fabricated financial figures (statutory missing values must remain `not_available` or `not_applicable`).
- Strict operational limits: ≤ 2,000 requests per 100-company batch, runtime ≤ 45 minutes, declared API cost ≤ $10.00.
- All existing 104+ unit and integration tests in `tests/test_poc.py` and tier tests must pass.
- Run the 100-company smoke test and produce `out/smoke-report.json` and `out/smoke-envelopes.jsonl` confirming 100 terminal envelopes with 0 drops.

## Acceptance Criteria

### Recall & News Improvement
- [ ] At least 10 companies in the 100-company smoke test have verified dated news claims (`latest_news_title`, `latest_news_date`, `latest_news_url` or `latest_corporate_action_date`) matching the verified pool criteria (title, direct URL, publication date, exact company match)
- [ ] Number of companies with `official_website: available` increases from 3% to ≥ 25% on the smoke test batch via Serper discovery
- [ ] All dated news items contain valid ISO 8601 dates, direct URLs, titles, and exact company match verification

### Precision & Lineage
- [ ] Zero wrong-company publications across the 100-company smoke test
- [ ] 100% of claims are linked to valid evidence records with canonical URLs, timestamps, claim spans, and SHA-256 hashes
- [ ] Idempotent refresh remains intact (0 false changes)

### Verification Suite
- [ ] `uv run --with pytest pytest -q tests/` passes with 0 failures
- [ ] `uv run python run_agent.py --input tests/fixtures/smoke-100.jsonl --output out/smoke-envelopes.jsonl --report out/smoke-report.json` completes successfully with 100 envelopes, 0 drops, and < 2,000 requests

## 2026-11-03T09:03:24Z

Maximize the external recall and coverage of the Signalpost Norwegian company intelligence agent from ~6% to >= 35% across the 411,000-entity universe, pushing the overall Builderr benchmark score to >= 80/100 while maintaining 100% precision safety, zero wrong-company publications, and challenge operational budgets.

Working directory: /home/deu/Coding Repos/Active/signalpost
Integrity mode: development

## Requirements

### R1. Aggressive Search Discovery & Candidate Gate Decoupling
Decouple crawl candidate selection from final publication in `src/norway_company_agent/discovery.py`. Permit candidate company websites from Serper/Brave search results that match primary distinctive legal name tokens to be fetched into memory, rather than requiring pre-fetch hostname containment or snippet org numbers. Let `verify_candidate_identity` on the fetched page content make the definitive publication decision. Utilize the external search API budget aggressively up to the $10.00 challenge limit via `CostGovernor`.

### R2. Algorithmic Zero-Cost Domain Probing
For entities where search APIs are exhausted or unavailable, implement algorithmic domain name generation: construct candidate domain slugs from the company’s distinctive legal name tokens (e.g., `firmanavn.no`, `firma-navn.no`), send rapid HTTP HEAD/GET probes (timeout <= 2.5s), and feed responding domains through the existing deterministic `apply_website_identity_gate`.

### R3. Subdomain Traversal & External ATS Hiring Signal Mining
In `src/norway_company_agent/website.py`, relax priority subpath routing to allow company-owned subdomains matching the top registered domain (`_registered_domain(subpage) == _registered_domain(homepage)`), such as `karriere.*`, `jobb.*`, and `nyheter.*`. Detect and record verified links to major Norwegian job platforms and ATS boards (Finn jobb, Webcruiter, Teamtailor, Recman, Jobbnorge) as company-declared career observations.

### R4. Denser News & Corporate Event Harvesting
Enhance company-site news harvesting to extract dated article titles and publication timestamps across common CMS layouts, combining them with Brreg Kunngjøringer public notice events and Google News RSS into dated claims (`latest_news_title`, `latest_news_date`, `latest_news_url`, `latest_corporate_action_*`) and external observations.

### R5. Research Evaluator Alignment & Scale Verification
Align the research evaluation suite (`tests/fixtures/research-agent-suite-v3-fresh.json` and `scripts/evaluate_research_agent.py`) so the agent achieves 12.0/12.0 points and `qualification_passed: true`. Run the full 100-company smoke test and verify the 1,000-company cohort to confirm zero silent drops, zero wrong-company matches, and >= 4x increase in published external observations.

## Verification Resources

- **Unit & Integration Suite:** `make test` (470+ tests across Tiers 1–5).
- **Smoke Benchmark Runner:**
  ```bash
  uv run --locked --env-file .env python run_agent.py \
    --input tests/fixtures/smoke-companies.jsonl \
    --output out/smoke-envelopes.jsonl \
    --profiles-output out/smoke-profiles.jsonl \
    --observations-output out/observations.jsonl \
    --report out/smoke-report.json \
    --showcase out/smoke-showcase.html \
    --checkpoint-every 50
  ```
- **Deterministic Research Evaluator:**
  ```bash
  PYTHONPATH=src:. uv run --offline --no-env-file --locked python scripts/evaluate_research_agent.py \
    --input out/smoke-profiles.jsonl \
    --suite tests/fixtures/research-agent-suite-v3-fresh.json \
    --output out/research-evaluation.json \
    --workspace out/research-workspace.json
  ```
- **Competition Proxy Scorer:** `scripts/score_competition_v3.py`

## Acceptance Criteria

### Recall & Coverage Bar
- [ ] External company coverage increases from ~6% to >= 30% on the 100-company smoke test batch.
- [ ] Number of verified external observations emitted increases by at least 4x compared to the baseline.
- [ ] Research evaluation report (`out/research-evaluation.json`) achieves `qualification_passed: true` and a score of >= 11.0 / 12.0.
- [ ] At least 25% of smoke batch companies achieve `official_website: available`.

### Precision & Lineage Invariants
- [ ] Exactly 0 wrong-company publications or quarantined misattributions across all evaluated batches.
- [ ] Exactly 0 fabricated statutory financial figures; missing numbers remain `not_available` or `not_applicable`.
- [ ] Exactly 1 terminal JSONL envelope emitted per input organisation number (0 silent drops).
- [ ] 100% of claims are backed by source URLs, UTC timestamps, verbatim spans, and SHA-256 content hashes.

### Operational Boundaries
- [ ] All 470+ tests in `make test` pass without regressions.
- [ ] 100-company smoke execution runtime <= 10 minutes.
- [ ] Total outbound requests <= 1,800 per 100 companies (challenge limit: 2,000).
- [ ] Declared third-party API spend remains strictly within the challenge budget (<= $9.50 via `CostGovernor`).

