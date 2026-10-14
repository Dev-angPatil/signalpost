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
- `SERPER_API_KEY`: "9f084e95c9ed03b7390b2c6849b13a524b2983b2" (for Google web search discovery)

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

