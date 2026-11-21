# Signalpost — final submission

**To:** submit@builderr.ai
**Subject:** Signalpost final version — Dev-angPatil/signalpost

Hi Builderr team,

Please freeze and score this version of Signalpost:

- **Repository:** https://github.com/Dev-angPatil/signalpost
- **Branch:** `release/final-submission`
- **Commit:** `f84c9ca3f483fa231d82985270dcb8e7512e21ec`
- **Company browser (smoke run):** https://dev-angpatil.github.io/signalpost/ (the same page is `docs/index.html`)

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

`out/smoke-report.json` (with `out/smoke-envelopes.jsonl` and `out/smoke-showcase.html`; the same page is at `docs/index.html`), produced by this commit's code (346211b; f84c9ca adds only the smoke files):
- 100/100 terminal envelopes, 0 source errors, 193 s, 1,569 requests, $0.00 third-party cost, every qualification check true.
- Refresh: re-running with `--prior-profiles` produces 0 change events across all 100 companies.
- An independent live re-check of every external claim passed 149/149 (`out/smoke-live-check.csv`). It covers websites, social links, news, jobs, contact emails, phones and addresses (on every page each value cites), career pages and website descriptions.

Local 1,000-company test with the command above on the agent code of this commit (584a67d; later commits change only the company browser and docs): 1,000/1,000 envelopes in 25 minutes, 13,132 requests (about 13 per company), $0.00, 0 source errors, every qualification check true. Every external claim was re-opened live: 1,361 of 1,368 pass (99.5%). The seven others are checker artefacts (address spacing such as "77A" vs "77 A", a page titled "Work with us", a description paraphrased by the site) and Newsec's group homepage, which is the homepage the registry lists for the company.

We also ran Builderr's own 100-company sample from builderr.ai/signalpost. The first live pass gave 653 of 673; the 16 extra fails came from pages that served the checker a bad response during the run and all passed on a re-check, giving 669 of 673 (99.4%). The four others are an address written "23 E" on the page, two profiles the checker could not find linked from the homepage, and a headline worded slightly differently on the page.

## What changed since the last scored version

**Company browser (this round)**
- Every run writes a company browser for its results (`--showcase`, or `<envelopes>.html` next to the envelopes), read from the envelopes we are scored on.
- **Find:** search by name, org number, place or person; filters, including "All five areas" and "Can't tell".
- **Compare:** up to four companies side by side.
- **Verify:** a Verify button on every published fact opens its evidence record: source address, retrieval time, reporting period, SHA-256 and the quoted text, with a link that opens the source at the quote.
- **Coverage:** each company shows its five areas as found, not found, can't tell, blocked or could not be read, with the reason.
- **Run panel:** companies per area and the run's checks, recomputed from the envelopes.
- Works on desktop and phone. Above 250 companies, each company's evidence loads when it is opened.

**Websites (this round)**
- A website the company itself registered in Brønnøysund (homepage or e-mail domain) is also accepted when its domain is the company's whole legal name and the page's own text names the company. It is refused if the site shows another organisation's number or is parked, a placeholder, a directory listing, a frame forwarder or empty. The claim cites the homepage and the registry field verbatim (`"hjemmeside":"…"`). Guessed or searched domains still need the org number or the registered address on the site.
- Registered sites that answer 429/5xx or time out are retried once with a longer timeout. A site that still cannot be read is reported as `failed`, not `not_available`, and a blocked site's URL is no longer published as a value.

**Precision**
- Every contact detail cites the page it was read from (contact page, staff page or homepage), and each captured page has its own evidence id.
- Career pages are recognised by a whole URL path segment (`/karriere`, `/ledige-stillinger`, `/jobb-i-…`) or a `karriere.` subdomain. Words inside other words (`/forestillinger`, `/utstillinger`, `spir-jobbfrukt`) and career words in the domain name no longer count.
- Hiring status is published only from what a career page actually shows: readable vacancy titles, an explicit count, or an explicit "no vacancies" notice. A career page or ATS link alone gives no hiring claim, and a link to an ATS portal is never reported as a job posting.
- Undated news-listing pages ("Nyheter – Firma AS") are never published as company activity; only dated articles are.
- `latest_board_appointment_date` uses board notices only. Auditor and CEO notices no longer count.
- Year-on-year revenue growth is reported only between consecutive full-year filings.
- A website-derived business description cites the website, not the registry record.
- A press headline is not matched when the company name runs on into a longer capitalised name ("Parat Media" is not PARAT).
- No-reply addresses and text-glued addresses are never published as contact emails.
- Contact details from a website are published only when they belong to the company:
  - emails on its own verified domain or its registered email domain;
  - phone numbers from `tel:` links, structured data or a phone label;
  - addresses only when the street and postcode are one of its registered addresses (main unit, postal address or a sub-unit), reduced to street, postcode and city.
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
