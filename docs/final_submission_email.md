# Signalpost — final submission

**To:** submit@builderr.ai
**Subject:** Signalpost: revision request — please use commit 0822db1

Hi Builderr team,

I would like a revision of my previous Signalpost submission: please use this commit hash instead. Since the last scored version I have added a declared cache, improved the UI, and brought precision above 95% (99.5% of external claims pass an independent live re-check).

- **Repository:** https://github.com/Dev-angPatil/signalpost
- **Branch:** `release/final-submission`
- **Commit:** `0822db18e3f347c4510e12b7886a5cae18ace55d`
- **Company browser (UI):** https://dev-angpatil.github.io/signalpost/

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

- **100-company smoke** (`out/smoke-report.json`): 100/100 envelopes, 0 source errors, 147 s, $0.00, every qualification check true; live re-check of every external claim 147/147.
- **1,000-company test:** 1,000/1,000 envelopes in 25 minutes, $0.00; live re-check 1,361 of 1,368 claims (99.5%).
- **Builderr's own 100-company sample:** 669 of 673 claims pass (99.4%).

## What changed

- **Precision above 95%:**
  - A website must show the company's org number or registered address, or be its own registered domain named on the page.
  - Contacts, news and jobs are published only when they demonstrably belong to the company.
  - Unreadable sources are reported as `failed`, never as absent.
- **Declared cache:** active NAV job ads in `data/cache/` (9,499 ads, declared in `manifest.json`). These are candidates only; each ad is re-fetched live and checked against the employer's org number.
- **Improved UI** (https://dev-angpatil.github.io/signalpost/, written by every run):
  - find and compare companies;
  - a source chip on every fact that opens its evidence (source, retrieval time, SHA-256, quoted text);
  - five coverage lamps per company;
  - works on desktop and phone.
- **Summary:** plain-English explanation of the company, its finances, recent changes, warning signs and what could not be found, citing every source.
- **Refresh:** a source that could not be read is never reported as a change.

## Optional API keys

The agent needs no keys and costs $0.00 without them. These optional keys add coverage; with any of them set, total spend is capped at $9.50 per batch. Listed with the most benefit for the least cost first. **If possible, please use them in your run, starting from the top:**

1. **`SERPER_API_KEY`** (paid, ~$0.001 per query; capped at $2.50 search + $0.75 news): finds websites the registry doesn't list, which unlocks social profiles, news, contacts and jobs. Also adds Google News results.
2. **`ANTHROPIC_API_KEY`** (paid; Claude Haiku 5.5 for small tasks at fractions of a cent, Claude Sonnet 5.5 for the company analysis): reads career pages for job postings, reranks search results, and writes a cited analysis per company.
3. **`YOUTUBE_DATA_API_KEY`** (free, quota-based): channel statistics for companies whose site links a YouTube channel.
4. **`BRAVE_SEARCH_API_KEY`** or **`GOOGLE_SEARCH_API_KEY`** (paid, ~$0.005 per query): alternatives to Serper; only one search key is needed.
5. **`GOOGLE_PLACES_API_KEY`** (paid, ~$0.035 per request; capped at $2.50): place listings as extra website candidates.

Agent: Signalpost · Contact: officialdevangpatil@gmail.com

Thank you,
Devang Patil
