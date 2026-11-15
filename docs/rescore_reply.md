# Signalpost — re-score request

**To:** submit@builderr.ai
**Subject:** Signalpost re-score request — Dev-angPatil/signalpost

Hi Builderr team,

Please re-run Signalpost on:

- **Repository:** https://github.com/Dev-angPatil/signalpost
- **Branch:** `fix/precision-jobs-rescore`
- **Commit:** `d2a2cc8aabc40c75ce0c900adf13952b74cb90ac`

Run command (unchanged):

```bash
uv sync --locked
cp .env.example .env   # no keys required
uv run --locked --env-file .env python run_agent.py \
  --input <input.jsonl> --output <output.jsonl> \
  --profiles-output <profiles.jsonl> --observations-output <observations.jsonl> \
  --report <report.json> --showcase <showcase.html> --checkpoint-every 50
```

## What changed against your three points

**1. Wrong-company claims.**
- Websites the registry does not list (guessed domains, search results, registry email domains) are now published only when the site shows the exact org number, a registered sub-unit number, or the registered street address and postcode next to the company's own name. A name match alone is quarantined as `ambiguous`.
- Pages listing other companies' org numbers, and shared `c/o` addresses, are refused as proof.
- A social handle must start or end with the company's distinctive legal name (this removes `twitter.com/squarespace` and `instagram.com/boligbyggelaget_usbl`).
- On our 100-company smoke set this removed 9 previously published websites, several of which were wrong (e.g. B. J. FISK AS → fisk.no, AKITA AS → Norsk Akita Klubb, LAB HOLDING AS → a US LLC).
- Official website URLs now canonicalize schemes and hostnames to lowercase conforming to RFC 3986 (e.g. `https://WWW.NORMORIA.NO/` becomes `https://www.normoria.no/`).
- Press article matching for generic-word company names (e.g. "Bare Brød"): requires capitalized headline tokens for non-suffix company words, strictly rejecting common phrase occurrences (e.g. "bare brød og vann") while matching genuine capitalized press headlines.

**2. News and job postings.**
- Job postings come from NAV's official vacancy feed (`pam-stilling-feed.nav.no`, public token). A posting is published only when its `employer.orgnr` equals the org number or a registered sub-unit, and each claim links the public ad on arbeidsplassen.nav.no.
- Website job vacancies require a visited career page or verified external job portal before reporting open positions, preventing staff roles/occupations on clinic pages (e.g. "Fysioterapeut" on team pages) from being reported as active vacancies without a career page.
- Stale job postings (>120 days old) are strictly excluded from both active jobs and news claims, preventing expired job ads from leaking into company news.
- Job posts on the verified company site from the last 120 days also count.
- Dated news now also comes from the verified site's own RSS/WordPress feed, with publication date and article URL on the company's domain.
- Contact, author, tag and listing pages and CMS placeholder posts are dropped.

**3. Summary.** The summary now opens with one buyer-relevant fact and its source:
- an open posting (NAV / company site), a revenue trend (Regnskapsregisteret), or a bankruptcy notice (Kunngjøringer);
- then leadership from the roles register, the latest dated activity, and what was not found.

We also fixed a bug that reported the oldest filing year as the latest: the register returns accounts oldest-first.

**Keys we would like you to set for our run** (all optional; the agent skips a step cleanly without its key):
- `SERPER_API_KEY` — website and news search (~$1–2 per 1,000 companies)
- `ANTHROPIC_API_KEY` — Claude Haiku 5.5 for search reranking, web search on leftover companies and career pages; Claude Sonnet 5.5 for cited summaries
- `GOOGLE_PLACES_API_KEY` with `GOOGLE_PLACES_COST_PER_REQUEST_USD=0.035` — address-matched local businesses

Total spend is capped at $9.50 per batch with per-task caps; the run report lists spend per task.

**Model key request.** This version adds an optional Claude analysis layer (claude-sonnet-5-5). It writes cited summaries, analyses job ads and labels news, all over facts that already passed the identity gates, and sentences that are not supported are dropped automatically. The playbook says you can supply a model key for scoring: could you set `ANTHROPIC_API_KEY` for our run? Expected spend is about $2–4 per 1,000-company batch, capped by our $9.50 circuit breaker. Without a key the agent skips this step and uses the template summary.

100-company smoke result: `out/smoke-report.json` (100/100 terminal envelopes, $0.00 third-party cost, about 6 minutes).

Agent: Signalpost · Contact: officialdevangpatil@gmail.com
