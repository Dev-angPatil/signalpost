# Signalpost — final submission

**To:** submit@builderr.ai
**Subject:** Signalpost: revision request — please use commit 0822db1

Hi Builderr team,

I would like a revision of my previous Signalpost submission: please use this commit hash instead. Since the last scored version I have added a declared cache, improved the UI, and brought precision above 95% (99.5% of external claims pass an independent live re-check).

- **Repository:** https://github.com/Dev-angPatil/signalpost
- **Branch:** `release/final-submission`
- **Commit:** `0822db18e3f347c4510e12b7886a5cae18ace55d`
- **Company browser (100-company smoke run):** https://dev-angpatil.github.io/signalpost/ (same page as `docs/index.html`)

## Run command

```bash
uv sync --locked
cp .env.example .env   # no keys required
uv run --locked --env-file .env python run_agent.py \
  --input <input.jsonl> --output <output.jsonl> \
  --profiles-output <profiles.jsonl> --observations-output <observations.jsonl> \
  --report <report.json> --showcase <showcase.html> --checkpoint-every 50
```

## Results

- **100-company smoke** (`out/smoke-report.json`, `out/smoke-envelopes.jsonl`, `out/smoke-showcase.html`):
  - 100/100 terminal envelopes, 0 source errors, 147 s, 1,445 requests, $0.00, every qualification check true.
  - An independent live re-check of every external claim: 147/147 pass (`out/smoke-live-check.csv`).
  - A re-run with `--prior-profiles` reports 1 change in 100 companies (a guessed website not found again).
- **1,000-company local test:** 1,000/1,000 envelopes in 25 minutes, about 13 requests per company, $0.00, 0 source errors, every qualification check true. Live re-check: 1,361 of 1,368 external claims pass (99.5%); the other seven are checker artefacts such as "77A" versus "77 A". This run used commit 584a67d. Later commits changed only the summary wording, the refresh comparison and the company browser, and all 1,000 summaries were rebuilt on the final code with no errors.
- **Builderr's own 100-company sample** (builderr.ai/signalpost): 669 of 673 external claims pass (99.4%).

## What changed since my last scored version

**Precision (now above 95%)**
- Websites need the org number, a registered sub-unit number or the registered address on the site.
  - A site the company itself registered also qualifies when its domain is the whole legal name and the page names the company.
  - A site showing another organisation's number is never used.
  - Parked pages, placeholders, directory listings, frame forwarders, empty pages and bot-check pages are never websites.
- Contact details are published only when they belong to the company:
  - emails on its own domain;
  - phone numbers from phone links or labels;
  - addresses only when they match a registered address.
- **News:** only dated articles that open on the company's own site, plus press headlines that carry the full legal name.
- **Jobs:** only real postings, never "career page exists". NAV ads must carry the company's or a sub-unit's org number.
- **Failures are reported, not hidden:** an unreadable source is `failed` (after one retry), and a blocked site's URL is never published.

**Declared cache**
- `data/cache/` holds active NAV job ads (9,499 ads from 5,197 employers, built 2026-11-20), declared in `data/cache/manifest.json`.
- The cache holds candidates only. Each ad is re-fetched live and published only if it is still active and its employer org number matches.

**Company browser (UI)**
- Every run writes a company browser built from the scored envelopes. It works on desktop and phone.
- **Find and compare:** search by name, org number, place or person; filters; side-by-side compare of up to four companies.
- **Verify:** every fact carries a source chip, like a chat answer citing its sources. Pressing it opens the evidence: source address, retrieval time, reporting period, SHA-256 and the quoted text.
  - Registry facts open Brønnøysund's readable page, with the raw document we read one click away.
- **Coverage:** five signal lamps per company (company record, accounts, people & places, website, public footprint). Each area is marked found, not found, can't tell, blocked or could not be read, with the reason.
- **Run panel:** the run's checks, recomputed from the envelopes.

**Summary**
- Written in plain English from published facts only, and it cites every source it uses. It covers:
  - what the company is;
  - how its money moved: profit or loss, a turnaround, the multi-year trend, equity;
  - what changed: registry notices translated, with the original kept;
  - signals: a loss, negative equity, revenue up or down 25% or more, a new CEO, a young company, hiring;
  - what was not found, and what could not be checked and why;
  - the date its sources were read.

**Refresh and reliability**
- A source a run could not read (network failure, blocked site, bot-check page) is never reported as a change; earlier evidence stands.
- Registry calls are paced with Retry-After-aware retries and a retry pass.

## API keys: free and paid

The agent needs **no keys** and costs **$0.00** without them. Every key is optional, is read only from environment variables, and is skipped cleanly when absent. With keys set, total spend is capped at $9.50 per batch, and each paid call reserves its worst-case cost before it runs.

**Free, no key needed:**
- Brønnøysundregistrene open APIs: Enhetsregisteret, Regnskapsregisteret, roles, sub-units (NLOD 2.0)
- Kunngjøringer public notices
- NAV job-vacancy feed (NAV's published public token)
- Wikidata (CC0)
- Google News RSS
- Company websites (robots.txt honoured)

**Optional keys, most benefit for the least cost first. If possible, please use these in your run, starting from the top:**

1. **`SERPER_API_KEY`** (paid, about $0.001 per query; capped at $2.50 for search and $0.75 for news per batch)
   - Finds websites for companies the registry does not list, through Google results. Websites drive social profiles, news, contacts and jobs.
   - Adds Google News results, which helps when the free RSS feed is throttled.
   - Every candidate still has to pass the identity proof.
2. **`ANTHROPIC_API_KEY`** (paid; uses Claude Haiku 5.5 for the small tasks and Claude Sonnet 5.5 for the company analysis; capped by the same breaker)
   - Haiku 5.5 reads career pages for job postings, reranks search results and searches for websites of high-priority companies. These cost fractions of a cent per call.
   - Sonnet 5.5 writes the company analysis from a numbered fact sheet. Every sentence must cite a fact, and uncited sentences are dropped. It runs only for companies with a website, jobs or news.
3. **`YOUTUBE_DATA_API_KEY`** (free, quota-based): channel statistics for companies whose own site links a YouTube channel. Small gain, no cost.
4. **`BRAVE_SEARCH_API_KEY`** or **`GOOGLE_SEARCH_API_KEY`** (paid, about $0.005 per query): alternatives to Serper. Only one search key is needed.
5. **`GOOGLE_PLACES_API_KEY`** (paid, about $0.035 per request; capped at $2.50 per batch): place listings as extra website candidates and place observations. The highest cost per call, so it comes last.

## Models, licences, cost

- **Models:** none without a key. With `ANTHROPIC_API_KEY`, Claude Haiku 5.5 and Claude Sonnet 5.5 as above.
- **Licences:** Brønnøysund data under NLOD 2.0 and Wikidata under CC0. Company websites are read as robots.txt allows.
- **Expected cost per batch:** $0.00 without keys. With keys, at most $9.50.

Agent: Signalpost · Contact: officialdevangpatil@gmail.com

Thank you,
Devang Patil
