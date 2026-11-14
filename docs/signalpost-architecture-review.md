# Signalpost: how the agent works

A detailed description of how the agent searches, verifies, analyses, publishes and displays company information. Written for improvement review: each stage lists what it does, the rules it enforces, what it costs, measured results, and known weaknesses.

State as of branch `fix/precision-jobs-rescore`, commit `02b3db3` (15 Nov 2026).

---

## 1. The competition and what is scored

**Task.** Builderr supplies a batch of Norwegian organisation numbers (1,000 now, may grow to 1,100). For each one the agent must return exactly one "terminal envelope": a company profile made of claims, each with evidence (source URL, retrieval time, content hash, quoted span), plus availability states (`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`).

**Scoring (100 points):**

| Points | What | How |
|---|---|---|
| 50 | Recall and coverage | Per external information type (websites, social profiles, job postings, news, probably places/reviews): 70% company coverage + 30% individual facts, measured against the union of everything every entrant and Builderr's own crawlers have verified |
| 30 | Precision and evidence | Each fact belongs to the right company and has a valid source and date |
| 12 | Useful summary | Explains the company, changes and unknowns without unsupported claims |
| 8 | Usability | A person can find, compare and verify information on desktop and mobile |

**Qualification:** an official run scoring at least 65, plus (from the reviewer email) at least 95% of checked claims correct. A material wrong-company publication blocks a run from becoming official. Ties break on fewer wrong-company publications, then higher company recall, then lower declared third-party cost.

**Our last official result** (commit `78a064d`): about 53/100, rank 29 of 104. 50 of 56 checked claims were right (89%); 6 were about a different company (e.g. `twitter.com/squarespace` for LØWEHJERTE DESIGN AS, the USBL manager's Instagram for HOVLY BORETTSLAG). The reviewer also found nothing verified for news or jobs, and said the summary only repeated registry fields.

**Rules that constrain the design** (from the official playbook and source policy):
- Search results are candidates only, never evidence. Every published fact must be re-fetched from a permitted source.
- A model may summarise or propose candidates. It must not decide identity or invent fields.
- Group, parent, franchise and brand relationships must not be collapsed into "the same company".
- LinkedIn, Meta, Glassdoor, Indeed and similar platforms restrict scraping; unofficial clients cannot be the sole support for a claim.
- API keys must be supplied by Builderr (they cannot reproduce a run that depends on our own accounts). They have said they will supply any key we ask for.
- One command must install and run on a clean machine (`uv sync --locked`, then `run_agent.py`).

---

## 2. Run flow (batch level)

Entry point: `run_agent.py main()`. Command:

```bash
uv run --locked --env-file .env python run_agent.py --input <in.jsonl> --output <envelopes.jsonl> \
  --profiles-output <profiles.jsonl> --observations-output <obs.jsonl> --report <report.json> \
  --showcase <showcase.html> --checkpoint-every 50
```

Order of operations:

1. **Read inputs.** Load the org numbers and validate the expected count. `--resume` skips companies already in the output.
2. **Registry anchors.** Fetch each company's Brønnøysund entity record (8 parallel workers, `data.brreg.no`).
3. **Background starts.**
   - The NAV job-feed index starts building in a background thread (section 5.1).
   - Wikidata is queried in one call per 150 companies for org number → official website.
4. **Per-company enrichment.** `enrich_profile`, 8 worker threads (section 3). If the NAV index isn't ready yet, the company is marked for a later job pass.
5. **Checkpoints.** Every N companies, envelopes are rebuilt and written to disk.
6. **Shared-website check.** If several companies in the batch publish the same website domain, only those whose own org number is on that site keep it (`quarantine_shared_domains`).
7. **NAV job post-pass** for companies enriched before the index was ready, followed by re-synthesis.
8. **AI analysis pass** (Claude Sonnet 5.5), only with a key and only for companies with a verified site, jobs or news (section 6).
9. **Change detection** against `--prior-profiles`, if supplied (section 8).
10. **Outputs.** Build the envelopes (`contract.build_output_envelope`), observations and the showcase HTML. Audit everything (`audit_envelopes`) and write the report.

**Measured runtime** (from India; Builderr likely runs closer to Norway):

| Batch | Runtime |
|---|---|
| 100 companies | about 6–7 min. The floor is the ~5 min NAV feed build. |
| 1,000 companies | about 33 min, about 14,150 requests |

---

## 3. Per-company enrichment (`enrich_profile`)

### 3.1 Official registers (Brønnøysundregistrene)

`official.fetch_official_modules` makes 3 calls at a time per company (configurable with `OFFICIAL_PARALLEL_REQUESTS`):

| Module | Endpoint | Provides |
|---|---|---|
| `registry_live` | `/enhetsregisteret/api/enheter/{org}` | Name, legal form, addresses, industry code, employees, email (`epostadresse`), homepage (`hjemmeside`), former names (`historiskeNavn`), bankruptcy flags |
| `financials` | `/regnskapsregisteret/regnskap/{org}` | Annual accounts (revenue, operating result, annual result, assets, equity, debt) |
| `roles` | `/roller` | CEO (DAGL), board chair (LEDE), members, auditor, accountant |
| `group` | parent/group relations | Group structure |
| `locations` | `/underenheter?overordnetEnhet={org}` | Registered workplaces (sub-units), with their own org numbers and addresses |
| `accounting_obligation` | | Whether the company must file accounts |

- **Accounts order.** The register returns filings oldest-first. The code sorts newest-first and prefers company (SELSKAP) accounts over group (KONSERN) accounts for the same period. Before this fix the agent reported 2023 as the "latest" year when 2025 existed.
- **Announcements.** `announcements.py` scrapes `w2.brreg.no/kunngjoring` statutory notices: bankruptcy, board changes, capital changes, approved accounts. These are dated, official, and tied to the org number.

### 3.2 Website discovery: sources in order

A website is the hub for social profiles, company news, career pages and the AI summary, so finding it drives most recall. Every source below only proposes candidates. Section 4 describes the identity gates that decide whether one is published.

| Step | Source | Cost | Notes |
|---|---|---|---|
| A0 | **Wikidata**: P2333 (org number) → P856 (official website) | free | One SPARQL query per 150 companies. Sites ranked `.no`, then `.com`, then shortest. Accepted on the legal-name check (Wikidata already ties the URL to the org number) or on hard proof. In a 1,000-company batch: 8 companies listed, 4 verified. |
| A | **Registry email domain** (`post@firma.no` → `firma.no`) | free | Free-mail providers are excluded. Accepted on the name check or hard proof. |
| B | **Search** (`_search_discovery`) | Serper ~$0.001 per query | Skipped for low-priority companies (section 3.3). Query 1: name + municipality. If there are no candidates, `"name" orgnr`. Then the Haiku rerank (below). If nothing verifies, a second round: the model's follow-up query, or name + municipality for up to 2 aliases. Candidates are inspected (fetched) up to 4 + 3. |
| B (rerank) | **Claude Haiku 5.5** reads up to 10 result snippets | ~$0.0005 | Returns up to 3 candidate URLs, brand names and a follow-up query. Validation: URLs must be among the shown results and not on blocked hosts; brand names must appear verbatim in the titles or snippets. |
| B1 | **Google Places** text search (name + municipality) | ~$0.035 per request (verify) | Only for companies likely to have a listing. A place's website is a candidate only when the place address contains a registered street + postcode. The response is cached so the place-summary observation reuses it (one request per company). |
| B2 | **Domain guessing**: slugs from the legal name plus up to 2 aliases (`firmanavn.no`, `.com`, hyphen variants) | free | DNS resolved in parallel within a time budget, then HEAD/GET probes. The legal-name view gets network proof only if the name check passed; alias views always get it. |
| B3 | **Claude Haiku 5.5 with the web_search tool** (`web_search_20250305`, `max_uses` 2, directory and social hosts blocked, location NO) | ~$0.02 | Only for high-priority companies still without a site. URLs are kept only if they came from `web_search_tool_result` blocks; the model's picks only set the order. |
| C | **Registry homepage** (`hjemmeside`) | free | Fetched and gated. If the name check fails, hard proof is tried. |
| C2 | **Registry site failed the check** (parent group, manager, old domain) | | Re-runs B → B3 while keeping the registry URL as the unpublished candidate. Example: CEG NORWAY AS lists gjerstad.com; verified by "Gjerstad CEG Norway AS, Gjerstadveien 373, 4993 Sundebru". |
| (job pass) | **NAV employer homepage**: the `employer.homepage` field on an org-number-matched NAV ad | free | Candidate gated by the name check (section 5.1). |

**Aliases** (`web_discovery.candidate_aliases`): brand names seen in search results, sub-unit names that differ from the legal name, and former names that ended within the last 8 years. Up to 3. They are used only to generate queries and domain slugs. Identity checks always use the legal name.

**Website fetching** (`website.fetch_website`):
- Checks: SSRF guard (`assert_public_url` blocks private, loopback and link-local addresses, including on redirects), robots.txt respected, 2 MB byte limit, HTML only.
- Reads the homepage plus up to 5 priority sub-pages (about, contact, careers, news, investor) chosen from links.
- Extracts: Trafilatura main text; JSON-LD organisation data; OpenGraph; social links; contacts (emails, phones, addresses); career portals (a known ATS host list); job-like titles; news items from CMS selectors and JSON-LD articles.

### 3.3 Spend targeting (`web_presence_priority`)

Each company gets a score that decides which paid steps run:
- **−1 (skip paid lookups):** housing co-ops and co-owned property (legal forms BRL, ESEK, SAM), and dormant holding or property companies (industry codes 64.2, 64.3, 70.1, 68.2, 00.0, with revenue under NOK 500k and no employees).
- **Otherwise the score adds up:** employees (+1 or +2), revenue ≥ NOK 1M (+1) or ≥ NOK 10M (+2), a consumer-facing industry code (+1), a registry email address (+1).
- **Thresholds:** search runs at score ≥ 0; Places at ≥ 2 and only for consumer-facing companies or ones with employees; Haiku web search at ≥ 3.

---

## 4. Identity verification (the precision layer)

This layer protects the 30 precision points and the 95% bar. Each fetched candidate goes through it before anything is published.

### 4.1 Legal-name check (`identity.assess_website_identity`)

Compares legal-name tokens (legal suffixes removed) with the homepage title, description, identity text, hostname, JSON-LD names and sub-pages. Outcomes, in order:
- Parked or for-sale page → reject.
- Business sports club (B.I.L.) pointing at the operating company's site → reject.
- Exact org number or sub-unit org number in the homepage evidence → score 1.0.
- All name tokens appear together (multi-word name) → 0.95.
- Single-word name plus substantial content → 0.95.
- Partial matches → below 0.9, not publishable.

### 4.2 Hard proof (`_require_hard_site_proof`, `site_proof.py`)

Required for every non-registry source (guessed domains, search, Places, model web search, registry email domain), and as a fallback for registry sites that fail the name check. Proof is searched for in this order:
1. **Text already captured** from the homepage and sub-pages.
2. **Contact-type pages, fetched fresh:** `/kontakt`, `/kontakt-oss`, `/om-oss`, `/personvern`, `/kjopsbetingelser`, `/vilkar`, `/contact`. Norwegian e-commerce terms and privacy pages usually carry the org number. The fetch stops after 3 misses or 8 seconds.
3. **Optional browser rendering** (`SIGNALPOST_RENDER=1`, Playwright) for sites that only render with JavaScript. Off by default.
4. **Last resort: name equals domain plus location.** The multi-word legal name equals the domain label, none of the tokens are generic, and a registered municipality, post town or postcode appears on the site.

Proof types and their safeguards:

| Proof | Safeguard |
|---|---|
| The company's org number (spaced, dotted or `NO … MVA` formats) | Rejected if the page lists 2 or more other org numbers with an "org.nr" label (directory, group or accountant pages) |
| A registered sub-unit's org number | Same directory guard |
| Registered street + postcode | All distinctive legal-name tokens must be within ~150 characters of the address; `c/o` lines are refused. This blocks a parent at a shared office: e.g. LILLEENG AS → enata.no via Enata Eiendom's c/o address, and NRP NORDIC HIGH YIELD AS → nrp.no ("NRP" beside the group address). |

Nordic letters are transliterated before matching (ø→o, æ→ae, å→a). Without this, "Lillestrøm" became "lillestrm".

### 4.3 Conflict check (`verify_candidate_identity`)

If a page shows another org number and not this company's, the candidate is quarantined. This happens before hard proof.

### 4.4 Quarantine semantics

- A rejected candidate is always marked unpublishable. Fixed bug: it used to keep a stale "publishable" flag from the name check.
- It is stored as an `ambiguous` website only if it passed the name check. An unrelated site leaves no trace and the company shows "not found".
- With a registry URL present, the registry URL stays as the unpublished candidate.

### 4.5 Batch-level check

One website, one company: a domain published for several companies in the same batch stays only where that company's own org number (or a sub-unit's) is the proof. Everyone else is quarantined and their summary is rebuilt.

### 4.6 Social profiles

- **Source:** only links on the verified company site, or the employer's Facebook/LinkedIn/Twitter declared on an org-number-matched NAV ad. Profiles seen only in search results are never published.
- **Handle rule:** the handle must equal the distinctive legal name, or start or end with it (`autobudgetbilforhandler`, `kulturhusetNormoria` pass; `norskakitaklubb` for AKITA fails).
- **Blocklist:** site builders (squarespace, wix, shopify…), housing managers (usbl, obos, tobb…) and generic words (info, support, contact…).

### 4.7 Measured effect

| | Verified websites | Notes |
|---|---|---|
| Before the precision work | 21/100 | At least 5 of 21 were wrong-company on manual review: fisk.no, akita.no (a dog club), labholding.com (a US LLC), gcc.envu.com, a bus company for a Narvik holding |
| After, on the 150 fresh companies | 12 → 14 | Both new ones checked correct by hand |
| After, 1,000 companies | 117 → 125 | Includes the 4 Wikidata sites, all correct |

---

## 5. Hiring, news and dated activity

### 5.1 NAV job postings (`nav_jobs.py`)

- **Source:** NAV's official vacancy feed, `pam-stilling-feed.nav.no`. The bearer token is NAV's public token, fetched at run time from `/api/publicToken`.
- **Index.** At batch start a background thread walks the feed from `If-Modified-Since` = 45 days ago, at about 3 s per 1,000-item page (about 88 pages, 4–5 minutes). It keeps ACTIVE ads in a token index of employer names.
- **Prefetch by org number.** For every distinctive word (in ≤25 employer names) shared with the batch's company names, the full feed entry is fetched with 4 workers (about 1,500 entries per 1,000 companies) and keyed by `employer.orgnr`.
- **Matching.**
  1. Exact org number: the company's own or any registered sub-unit's.
  2. Name candidates (all core name tokens present in the employer name) that weren't prefetched, at most 5 per company, each verified on `employer.orgnr`.
- **Published fields:** title, public ad URL, published and expiry dates (expired ads dropped), location, engagement type, extent, position count, application deadline, and an ad-text excerpt of up to 2,500 characters for the AI analysis.
- **Employer extras:** `homepage` becomes a website candidate; the declared Facebook/LinkedIn pages become social candidates.
- **Fallback:** if the feed fails, the agent uses the arbeidsplassen JSON search endpoint. It has a global rate limit, 429 back-off and a circuit breaker after 5 failures. It is strict (it 429'd after about 40 requests in testing) and doesn't cover FINN-sourced ads.
- **Limits:**
  - The feed excludes FINN-sourced ads (e.g. most Posten ads).
  - Only about 0.5% of registered companies have an active NAV ad; 2 of 1,000 matched in testing.
  - The 45-day window misses older ads that haven't been modified since.

### 5.2 Career pages on the verified site (`web_discovery.extract_career_postings`)

- **Candidate pages:** the career URL on the verified site, or an ATS portal (teamtailor, webcruiter, recman, jobylon, reachmee, varbi, easycruit…) whose subdomain or path contains a distinctive name or alias token. Foreign locale paths (`/se/`, `/dk/`, `/en-gb/`…) are dropped, because group sites list other countries' jobs.
- **Extraction:** Claude Haiku 5.5 reads the page text (up to 12k characters) and up to 150 numbered links, returning postings as structured JSON.
- **Validation:** the title must appear verbatim in the page; the link must be one of the given links and stay on the career host or a known ATS; otherwise the career page URL is used; `in_norway` must be true.
- **Freshness:** job-like posts in the site's own news (`JOB_LIKE_TITLE` regex) count as open postings only if dated within 120 days. Older ones are treated as news.
- **Measured:** 22 of 141 verified sites had a career URL. Some are false (e.g. a news article URL containing "stillinger").

### 5.3 Company news

**Verified-site news:**
- **Sources:**
  - CMS card selectors and JSON-LD articles on crawled pages.
  - WordPress REST `/wp-json/wp/v2/posts`.
  - RSS `/feed/`. When a URL matches, RSS dates override scraped ones; RSS `pubDate` is more reliable.
- **Filters (`_is_junk_news`):** contact, author, tag, category, ad-sales and about paths; menu titles; CMS placeholders ("Hello world!", "Hei, verden!", "Elementor #6565"); listing pages (`/blogg`, `/nyheter` as the last path segment); future dates.
- **URL rule:** a scraped item must have an article-like URL (`/nyhet…`, `/aktuelt/`, `/2025/06/`, `?id=…`). Feed items are accepted as articles.

**Press news (third-party):**
- **Without a Serper key:** Google News RSS search `"legal name" when:2y`.
- **With a key:** Serper's `/news` endpoint (`hl=en` so dates parse; relative dates beyond a week dropped).
- **Headline rule (`exact_title_match`):** the full legal name, or the name without the legal suffix for multi-word names, must appear as a contiguous phrase, preceded only by allowed words.
- **Circuit breaker:** after 5 consecutive failures (our IP got 503 throttled after heavy testing). Status `blocked`.

**Registry announcements** (`w2.brreg.no`) are emitted as dated public activity with their own evidence.

---

## 6. Analysis and synthesis

### 6.1 Template summary (`synthesis.synthesize_executive_profile`, always runs)

It opens with one decision-relevant fact and its source, in priority order:
1. Bankruptcy or insolvency notice (Kunngjøringer).
2. Hiring: count and newest posting (NAV or the company site).
3. Revenue for the latest year with year-on-year change and operating result (Regnskapsregisteret).
4. Balance sheet when there is no revenue (holding companies).
5. Verified-website self-description.
6. Registry identity.

Then, in order:
- what the company does (verified-site description, or industry code);
- CEO and board chair from the roles register ("who also chairs the board" when it's the same person);
- revenue (if not the lead) and negative equity;
- the latest site news or press mention;
- the two most recent non-routine registry notices ("approved accounts" notices are skipped);
- "Not found in checked sources: …".

It also produces knowns/unknowns lists and financial-health ratios: operating margin, equity ratio, debt ratio, YoY growth, CAGR, trajectory badge.

### 6.2 AI analysis (`ai_enrich.py`, optional: `ANTHROPIC_API_KEY`)

- **Eligible companies:** those with a verified site, jobs or news, about 15% of a random batch.
- **Fact sheet:** numbered facts built only from published evidence:
  - `R1` registry;
  - `A1–A3` accounts per year, with precomputed margin, YoY change and equity ratio, so the model can cite numbers rather than compute them;
  - `L1–L6` roles;
  - `W1–W2` verified-site title/description and text excerpt, marked `[company website]` as promotional;
  - `J1–J6` job ads with ad text;
  - `N1–N8` news (site first, then press);
  - `K1–K5` registry notices.
- **Model call:** Claude Sonnet 5.5, effort medium, structured JSON output, server-side refusal fallbacks (`fallbacks: "default"`). The system prompt forbids outside knowledge, says to copy numbers exactly, and treats fact text as data.
- **Output:**
  - 3–5 summary sentences, each citing fact IDs;
  - a business description;
  - per-job role category, seniority, employment type, work mode, key requirements and a one-line summary;
  - per-news event type, materiality and a one-line summary;
  - 0–3 watch points.
- **Validator:** a sentence is dropped if it cites nothing, cites an unknown ID, or contains a number (2+ digits) not present in its cited facts. Job and news items must reference J/N IDs that exist.
- **Effect:**
  - the validated summary replaces the template one (kept as `summary_template`), and the envelope's `executive_summary` cites the evidence IDs of the facts used;
  - AI job and news fields become claims, each with evidence pointing at the cited fact's source URL plus the analysis hash.
- **Cost and pacing:** about $0.01–0.02 per company. It runs as a batch pass with 6 parallel workers after NAV matching, so job facts are included.
- **Status:** tested with mocked responses only; no key has been used locally.

### 6.3 Model usage summary

| Task | Model | Tool / output | Validation |
|---|---|---|---|
| Rerank search snippets | Haiku 5.5, effort low | JSON schema | URLs from results only; brands verbatim |
| Leftover web search | Haiku 5.5, effort low | `web_search_20250305` | URLs from tool results only |
| Career page postings | Haiku 5.5, effort low | JSON schema | Verbatim titles, on-site links, Norway |
| Dossier analysis | Sonnet 5.5, effort medium | JSON schema + fallbacks | Cited facts; numbers must appear in cited facts |

Model output never decides identity; it is always checked deterministically.

---

## 7. Output contract (`contract.build_output_envelope`)

**Envelope:** `organisation_number`, `run` (ids, timestamps, `terminal_status`), `claims[]`, `evidence[]`, `changes[]`, `errors[]`, `operations` (requests, runtime, third-party cost).

**Claim families:**

| Family | Claims |
|---|---|
| Identity | `legal_name`, form, addresses, industry, employees, registration dates, bankruptcy/liquidation flags |
| Accounts | `latest_financial_year`, revenue, operating result, annual result, assets, debt, equity (missing is never zero; ENK exemption → `not_applicable`) |
| Roles | CEO, chair, board members |
| Locations | Sub-units |
| Website | `official_website` (`ambiguous` with value null when quarantined), title, description, contacts |
| Social | `social_{platform}` |
| Hiring | `hiring_active`, `open_positions_count`, `job_posting_{i}_{title,url,published,expires,location}`, AI job fields `job_posting_{i}_{role_category,seniority,employment,work_mode,one_line,key_requirements}` |
| News | `news_{i}_{date,title,url}`, `latest_news_*`, `ai_news_{i}_{item,event_type,materiality,one_line}` |
| Notices | Announcement events |
| Synthesis | `executive_summary`, `business_scope`, known/unknown counts, `ai_business_description`, `ai_watch_point_{i}` |
| External observations | Google Places summary (rating, reviews, maps URL), YouTube metrics, social handle observations, site activity |

**Evidence record:** `id`, `source_url`, `source_class` (official_registry, company_owned, public_job_board, public_news, model_analysis_of_cited_facts, …), `retrieved_at`, `content_sha256`, `claim_span`.

**Audit (`audit_envelopes`), in the report as `qualification_status`:**
- one envelope per input;
- every available claim has evidence with a URL, time and 64-hex hash;
- no wrong-company matches;
- no fabricated financials;
- request budget (≤20 per company);
- cost ≤ $10;
- runtime ≤ 45 min.

**Report extras:** `discovery` (verified websites by source type, companies with jobs), `budget_slices` (reserved spend per task), latency p50/p95, total requests and bytes.

---

## 8. Refresh and change detection

- **Change detection:** `refresh.diff_profile` compares against `--prior-profiles` and emits typed changes. The playbook asks for types like `new_role`, `closed_job`, `new_filing` and `changed_description`.
- **Replay check:** `scripts/run_refresh_replay.py` checks idempotent replay on saved snapshots.
- **Checkpoints and resume:** checkpoint writes during the run, and `--resume` for interrupted runs.
- **Weakness:** this area received no work in the rescore sprint. Check how complete the change types are, that failed refreshes keep the last known value, and how first-seen/last-seen timestamps are handled.

---

## 9. Display (what users and Builderr see)

- **Generator:** `showcase.generate_showcase_html` → `scripts/build_prototype.build`. A single static HTML file with all data embedded as JSON (about 2.3 MB for 100 companies, about 20 MB for 1,000).
- **Layout:**
  - a list with search and filters;
  - a dossier per company with:
    - an executive brief (AI sentences with clickable citation tags to each fact's source, when available, plus watch points), and known/unknown pills;
    - an Open Positions section (NAV and career-page postings with AI role chips: category, seniority, employment, work mode, a one-line summary and requirements);
    - registry facts, a financial table with ratio pills (operating margin, equity ratio, revenue trend), roles and sub-units;
    - the verified website with social links;
    - company news and press mentions merged by date, with AI event-type and "Material" chips;
    - official notices.
  - A per-source "evidence lineage" disclosure.
- **Also:** a static showcase at `docs/index.html` and a hosted Render dashboard.
- **In progress:** a separate UI rework, in `ui/` (`build_ui.py`, `data_model.py`, `template.html`) in another session.
- **Weaknesses:**
  - large files at 1,000 companies;
  - mobile layout not recently checked;
  - no comparison view between companies, though the rubric mentions "compare";
  - unclear how Builderr's own gallery renders our output.

---

## 10. Cost and budget control

- **Run-wide cap:** `CostGovernor`, $9.50 (`MAX_THIRD_PARTY_COST_USD`), with reserve and refund.
- **Per-task slices** (`web_discovery.BudgetSlices`, env `SIGNALPOST_BUDGET_*`):

| Slice | Cap (USD) | Unit cost (approx.) |
|---|---|---|
| search | 1.25 | Serper $0.001 / query |
| rerank | 0.50 | Haiku ~$0.0005 / company |
| places | 2.00 | ~$0.035 / request |
| agent_search | 1.25 | ~$0.02 / company ($0.01 per search + tokens) |
| careers | 0.50 | ~$0.001–0.002 / page |
| news | 0.75 | Serper $0.001 / query |
| analysis | 3.00 | Sonnet ~$0.01–0.02 / company |

- **Settlement:** each call reserves an estimate and is then settled to its real cost; unused reservation is refunded to both the slice and the global cap.
- **Without keys:** cost is $0.00 (registry, NAV feed, Wikidata, company sites, Google News RSS).
- **Keys to request from Builderr:** `SERPER_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_PLACES_API_KEY` (+ `GOOGLE_PLACES_COST_PER_REQUEST_USD=0.035`).

---

## 11. Robustness and runtime safeguards

| Issue | Safeguard |
|---|---|
| Catastrophic regex backtracking (an address regex stalled a batch for 30+ minutes) | Bounded quantifiers, 200k-character cap |
| Slow DNS on guessed domains | Parallel resolution with a shared time budget |
| NAV search 429 | Global rate limiter, back-off, circuit breaker, `blocked` status |
| Google News 503 throttling | Circuit breaker after 5 failures |
| Registry email-domain site unreachable (crashed about 3% of companies) | Fixed None-handling |
| Hard-proof fetches on dead hosts | Unresolvable hosts break the loop |
| Model errors, refusals, invalid JSON | Step skipped, envelope unaffected |
| Tests hitting real networks or APIs | `make test` forces dead proxies, `SIGNALPOST_AI=0`, `SIGNALPOST_WIKIDATA=0` |

Tests: 570 unit/integration tests (`make test`), including mocked Claude, Serper, Places and NAV flows.

---

## 12. Current numbers (1,000-company validation run, no paid keys)

| Metric | Value |
|---|---|
| Envelopes | 1,000 / 1,000, no silent drops |
| Runtime | ~33 min |
| Requests | ~14,150 |
| Verified websites | 125 (66 domain guessing, 53 registry homepage, 4 Wikidata, 1 registry-declared via proof, 1 email domain) |
| Companies with NAV jobs | 2 |
| Press news | Blocked in this run (Google News throttled our IP) |
| Qualification audit | All checks pass |

Estimated realistic ceiling for verified websites in a random batch: 35–45%. The sample is dominated by dormant holdings and housing co-ops.

---

## 13. Known weaknesses and improvement candidates

### Recall (50 points)
1. **Website coverage is the main limit (12.5%).** The paid paths (Serper, Haiku rerank and web search, Places) are built but have never run live. Measure them on the 150-company sample with real keys: added verified sites, cost and time, and a manual precision check.
2. **Brand names not discoverable from registry data.** The only free sources of brands are sub-unit names and former names. Ideas: brand names declared in NAV ads (`businessName` differing from the legal name, verified by org number); domains of email addresses found on verified sites.
3. **NAV covers few companies.** FINN-sourced ads are excluded from the feed. Ideas: a longer window than 45 days; matching by sub-unit address; using `employer.homepage` from all prefetched entries as a website source even without active postings.
4. **Career pages:** only the first candidate page is read per company, and only when the site is verified.
5. **Social profiles:** only site-linked or NAV-declared. Pages that aren't linked from the site are missed by design (precision).
6. **Places/reviews** is possibly its own scored type, but it currently runs only for eligible companies with a key.
7. **Common Crawl org-number index:** mine `.no` pages containing "org.nr XXXXXXXXX" into an org number → domain cache, declared as a public cache. Highest ceiling; days of work.
8. **Press news precision vs recall:** the strict headline rule misses articles that mention the company only in the body; the relaxed multi-word rule risks same-name companies.

### Precision (30 points)
1. **Name-check-only sources:** a registry homepage or a Wikidata site is accepted on the legal-name check alone (no hard proof). A parent or brand site could pass for single-word names. Consider requiring hard proof for single-token names.
2. **The name-equals-domain-plus-location proof** is the weakest hard proof; audit how often it fires.
3. **Group career pages** listing jobs from sister companies, and Norwegian group subsidiaries sharing a career site.
4. **Third-party news** about same-name entities.
5. **No measured precision at scale:** checks so far are manual spot reviews. A labelled gold set (e.g. 200 companies) would let every change be scored.

### Summary (12 points)
1. **Template summaries** for the ~85% of companies without a site, jobs or news are registry/accounts-only by necessity. Check whether more industry or peer context can be stated from registry data without unsupported claims.
2. **The AI summary** has not been evaluated live: tone, length, how often sentences get dropped, and citation density.
3. **Change narrative:** summaries rarely say what changed since the last run.

### Usability (8 points)
1. Mobile layout; size at 1,000 companies; comparison and filters; a per-claim "verify" link next to every fact.

### Operations
1. Runtime is about 33 minutes for 1,000 from India. Builderr's time limit is unknown, and a timeout scores zero.
2. Request count grew 40% with the new discovery steps (still under 20 per company).
3. No real key has yet exercised the paid paths end to end. Their failure behaviour is designed to skip cleanly, but it is untested live.
4. The refresh/diff behaviour (section 8) is the least-reviewed part.

---

## 14. File map

| File | Role |
|---|---|
| `run_agent.py` | CLI, batch orchestration, `enrich_profile`, website discovery helpers (`_discover_website`, `_search_discovery`, `_places_discovery`, `_probe_discovery`, `_agent_discovery`, `_try_candidate_site`, `_require_hard_site_proof`), NAV job pass, AI pass, audit, report |
| `src/norway_company_agent/official.py` | Brreg modules, account normalisation and sorting |
| `src/norway_company_agent/announcements.py` | Kunngjøringer notices |
| `src/norway_company_agent/website.py` | Safe fetching, crawling, extraction (contacts, socials, careers, news), junk-news filters |
| `src/norway_company_agent/identity.py` | Legal-name check, social handle rules, conflict check |
| `src/norway_company_agent/site_proof.py` | Hard proof (org number, sub-unit, address, name-equals-domain-plus-location), contact-page corroboration |
| `src/norway_company_agent/discovery.py` | Search providers (Serper/Brave/Google), candidate scoring, domain slugs and probing |
| `src/norway_company_agent/web_discovery.py` | Budget slices, priority, aliases, Wikidata, Haiku rerank/web search/career extraction, Places candidates, shared-domain quarantine, optional rendering |
| `src/norway_company_agent/nav_jobs.py` | NAV feed index, org-number prefetch, search fallback, rate limiting |
| `src/norway_company_agent/site_feeds.py` | WordPress REST and RSS news on verified sites |
| `src/norway_company_agent/news.py` | Google News RSS and Serper news, headline matching, circuit breaker |
| `src/norway_company_agent/official_external.py` | Google Places and YouTube official APIs |
| `src/norway_company_agent/synthesis.py` | Template summary, financial metrics, knowns/unknowns |
| `src/norway_company_agent/ai_enrich.py` | Fact sheet, Sonnet analysis, validator |
| `src/norway_company_agent/contract.py` | Claims and evidence envelope |
| `src/norway_company_agent/observations.py`, `scripts/extract_company_site_news.py` | External observations output |
| `src/norway_company_agent/refresh.py` | Change detection |
| `src/norway_company_agent/showcase.py`, `scripts/build_prototype.py` | HTML dossier |
| `tests/` | 570 tests; `test_rescore_precision.py`, `test_web_discovery.py`, `test_ai_enrich.py` cover the new layers |
| `SUBMISSION.md`, `README.md`, `.env.example`, `docs/rescore_reply.md` | Declarations, run instructions, keys, reply draft |
