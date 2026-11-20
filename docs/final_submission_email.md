# Signalpost — final submission

**To:** submit@builderr.ai
**Subject:** Signalpost final version — Dev-angPatil/signalpost

Hi Builderr team,

Please freeze and score this version of Signalpost:

- **Repository:** https://github.com/Dev-angPatil/signalpost
- **Branch:** `release/final-submission`
- **Commit:** `<FINAL_HASH>`

## Run command

```bash
uv sync --locked
cp .env.example .env   # no keys required
uv run --locked --env-file .env python run_agent.py \
  --input <input.jsonl> --output <output.jsonl> \
  --profiles-output <profiles.jsonl> --observations-output <observations.jsonl> \
  --report <report.json> --showcase <showcase.html> --checkpoint-every 50
```

## 100-company smoke result

`out/smoke-report.json` (with `out/smoke-envelopes.jsonl` and `out/smoke-showcase.html`; the same page is at `docs/index.html`):
- 100/100 terminal envelopes, 0 source errors, 159 s, 1,420 requests, $0.00 third-party cost.
- Refresh: re-running with `--prior-profiles` changes only one company, whose website (agronordic.no) the domain probe found in one run and missed in another. No other field changed.
- An independent live re-check of every external claim passed 28/28 (`out/smoke-live-check.csv`).

Local 1,000-company test on companies we had never run before, with the command above (commit 9cd855f, before the final fixes): 1,000/1,000 envelopes in 26 minutes, 12,861 requests (about 13 per company), $0.00, 0 source errors, every qualification check true. An independent live check of its 364 external claims: 361 pass, 1 unreachable, and 2 fail, neither of them a wrong company. One was the group homepage the registry lists for Newsec. The other was a treatment page taken as news, now filtered. Re-run on the final code, the 211 companies in it with a website candidate gave 356 checked external claims. 355 pass; the 1 fail is Newsec's registry-listed group homepage. One more site could not be reached by the checker (HTTP 454).

## What changed since the last scored version

**Precision**
- Contact details from a website are published only when they belong to the company:
  - emails on its own verified domain or its registered email domain;
  - phone numbers from `tel:` links, structured data or a phone label;
  - addresses at one of its registered postcodes and cities, reduced to the street, postcode and city.
- Parked and hosting-placeholder pages are never published as a company website.
- Headings and links are never job titles: "Ledig stilling", "Stillingsannonsen", "Karriereside av Teamtailor", employee login.
- Websites the registry does not list need the org number, a registered sub-unit number or the registered address on the site. The one other route is a domain that spells a multi-word legal name, with the exact legal name and form, or a registered place, on the site and no other company's org number. A name match alone stays `ambiguous`.
- News from a company's own site is published only when the article opens on that site. Page regions, service and product pages, CMS duplicate titles and "no vacancies" notices are never news.
- Regnskapsregisteret filings the open API cannot serve (banks, pension funds, non-profits) are reported as `not_available` with the server's reason, not as failures.

**Reach**
- Pages on sites without a `robots.txt` are crawled as robots rules allow. Before, only the first page was fetched, so news and contact-page proof were missed.
- Paced registry requests with Retry-After-aware retries and a retry pass. Companies with registry gaps:
  - 166 in our earlier 1,000-company run;
  - 0 in today's fresh 1,000-company run, a different set of companies.
- About 20 more official facts per company: roles, workplaces, notices, capital and board changes.
- New source: homepages and email domains registered on the company's own sub-units. These are candidates only, behind the same hard proof.
- 16 concurrent workers, so every company finishes before the run's own soft deadline. Registry calls stay paced.

**Jobs**
- The NAV vacancy feed, plus a declared cache of active NAV ads (`data/cache/`, built 2026-11-20).
- Each cached ad is re-fetched live and published only when it is still ACTIVE and its `employer.orgnr` is the company or one of its sub-units.

**Summary**
- The summary leads with one buyer-relevant fact and its source.
- One-year revenue moves against the multi-year trend are labelled as a rebound or dip.
- Routine address notices are left out.

**Refresh**
- Stable ordering, so a re-run reports no false changes.

## Models, APIs, licences, cost

- **No keys needed.** Sources: Brønnøysundregistrene open APIs (NLOD 2.0), Kunngjøringer, NAV's public job feed and public token, Wikidata (CC0), Google News RSS, and company websites (robots.txt honoured).
- **Optional keys**, skipped cleanly when absent:
  - `SERPER_API_KEY` / `BRAVE_SEARCH_API_KEY` / `GOOGLE_SEARCH_API_KEY`
  - `GOOGLE_PLACES_API_KEY`
  - `ANTHROPIC_API_KEY`
- **Expected cost per batch:** $0.00 without keys. With keys, spend is capped at $9.50 per batch by a circuit breaker.
- **Declared cache:** `data/cache/manifest.json`. It holds candidates only; every claim is re-fetched live.

Agent: Signalpost · Contact: officialdevangpatil@gmail.com
