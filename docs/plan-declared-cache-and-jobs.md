# Plan: declared caches and job-posting recall

Status, 16 Nov 2026, branch `feat/declared-cache-nav-jobs`:
- **Done in code:**
  - **Part A**
    - `cache.py` loader (sha256-checked, falls back to no cache);
    - `scripts/cache/pack_cache.py` (manifest, 40 MB budget);
    - `declared_caches` section in the run report;
    - declaration in `SUBMISSION.md`;
    - `make cache`.
  - **Part B**
    - `scripts/cache/fetch_brreg_bulk.py` (sub-unit → parent from the Brreg bulk CSV);
    - `scripts/cache/build_nav_cache.py` (walk / entries / pack, resumable);
    - runtime: incremental feed walk from the cache cursor, live re-check of cached ads (`nav_jobs._confirm_cached_ads`), and prefetch with a higher frequency limit on the incremental set.
  - **Part D1:** `job_postings_ld.py` (schema.org `JobPosting` on career and ATS pages), run as step D1 before the optional model step.
  - **Tests:** `tests/test_declared_cache.py` and `tests/test_job_postings_ld.py`.
- **Running:** the NAV feed walk over the last **365 days** (`build_nav_cache.py --since-days 365`, the default), about 3 pages per day of feed, so about 1,100 pages.
  - A full-history walk was abandoned: after 78 minutes and 1,561 pages it was still in the first 75 minutes of NAV's 14 June 2023 bulk import (about 3 s per page, and pages chain sequentially).
  - An ad that is still active but has not changed for over a year is outside the cache. It can still be found by the live search fallback.
- **NAV cache built (16 Nov):** a 765-page walk over 365 days found 168,356 ads, 9,795 of them ACTIVE.
  - After fetching the entries: 9,285 active ads with a 9-digit employer, at 5,107 companies. 4,702 of those ads were posted by a sub-unit and mapped to the parent.
  - The file is 756 KB.
- **Live check:** 8 of the 1,000 cohort companies have cached ads, against 2 jobs found before. The smoke set has 0/100.
  - All 8 were confirmed live, with 0 rejected; each was posted under a sub-unit org number.
  - The ceiling is the base rate: about 1.2% of the universe has an open NAV ad.
- **Full cohort-1000 run with the cache:**
  - 8/1000 companies have published jobs (before: 2), and 8 cached ads were checked, 8 confirmed, 0 rejected.
  - 1,576 s, 10,808 requests (limit max(2,000, 20/org) = 20,000), 0 silent drops, and every qualification check is true.
  - The older `out/report-1000.json` (an earlier commit) took 2,293 s and 8,186 requests. It is not a clean A/B: the request count also moved with e2099ab/f8bde6a.
- **Staleness hardening:**
  - The sub-unit → parent link from the cache is re-checked live in Brreg (`underenheter/{orgnr}`, active, `overordnetEnhet` = company) before a sub-unit ad is accepted.
  - A cache older than `NAV_FEED_DAYS` (45) walks the normal 45-day window instead of from its cursor.
- **Tests:** the full suite (623 tests) passes.
- **D1 live smoke (100 companies, no cache):** 0 postings, and none were rejected wrongly.
  - Only 14/100 companies have a verified site, and only 1 has a career-page link (slakteriet-as.no/karriere/), which carries no JSON-LD.
  - D1's reach is capped by website discovery, so it pays off only after Part C. The NAV cache is the main job lever for small companies.
  - The 14 sites were checked with a hermetic rerun of D1; the run report shows no errors and all qualification checks true.
- **Not started:** Part C (website cache) and D2 (ATS adapters).
Assumption: the official run has **no paid keys**. Every step below works with free public sources only. Where a source needs a token, it is one published for everyone (NAV's public feed token).

## Why

- The harness says: *"External caches must be declared. Cached public-universe material is allowed, but the official score still enforces source timestamps, refresh behavior and the same evidence cutoff for everyone."* (`docs/official/signalpost-evaluation-harness.md:61`)
- Website discovery at run time is slow and has few sources without keys (Wikidata, email domain, slug guessing, registry homepage). We verify about 12–21% of companies.
- Jobs are at almost zero: 0/100 in the smoke run and 2/1,000 in the large run. The NAV matcher (`nav_jobs._prefetch_by_orgnr`) has three blind spots:
  1. It only walks ads modified in the last **45 days**, so ads that are active but older are missed.
  2. It only fetches feed entries whose `businessName` shares a word with the legal name, and only when that word appears in 25 or fewer ads. Brand names are missed (for example, an ad posted as "Rema 1000 Lade" by a company registered as "LADE DAGLIGVARE AS"; this is illustrative, not a measured case), and so are common words.
  3. The 5-minute feed walk happens on every run.
- Facts checked live (16 Nov):
  - the NAV feed (`pam-stilling-feed.nav.no/api/v1/feed`) goes back to **June 2023** with 1,000 items per page;
  - entries marked `INACTIVE` have no `ad_content`, so their employer can only be captured while the ad is active;
  - the universe has 411,160 org numbers, 44,855 with a registry homepage and 59,821 with employees.

## The core rule (unchanged)

**A cache only ever holds candidates. It never holds published evidence.** Every claim is re-fetched live during the run, and its source URL, `retrieved_at` and `content_sha256` come from that live fetch. If a cached candidate fails live verification, it is not published and the normal discovery steps run. This keeps precision intact, keeps timestamps honest, and satisfies the evidence cutoff.

---

## Part A: the cache container (shared by B and C)

**Location:** `data/cache/`, committed with the code, so the frozen commit is reproducible and the evaluator needs no extra network host.

```
data/cache/
  manifest.json                  # declared: sources, build times, row counts, sha256 of each file, builder commit
  brreg-subunits.jsonl.gz        # subunit orgnr -> parent orgnr, name, address, hjemmeside, epost (official bulk)
  website-candidates.jsonl.gz    # per org: ranked candidate URLs + offline verdict + proof page URL
  nav-jobs.jsonl.gz              # active NAV ads keyed by employer orgnr (+ feed cursor in manifest)
```

**Size budget:** under 40 MB in total. The universe file is already 12.6 MB gzipped. The website cache stores about 100k rows of short URLs, and the NAV cache about 25k ads with titles only (the ad text is re-fetched live).

**Runtime loader:** new `src/norway_company_agent/cache.py`.
- `load_cache(dir) -> DeclaredCache` is lazy and thread-safe.
- It checks every file's sha256 against `manifest.json`. On a mismatch it **ignores the cache** and logs the reason in the report. It never crashes.
- Accessors:
  - `website_candidates(org) -> list[Candidate]`
  - `nav_ads_for(orgs: set[str]) -> list[CachedAd]`
  - `nav_cursor() -> str | None`
  - `parent_of(subunit_org) -> str | None`
- Environment variables: `SIGNALPOST_CACHE=1` (on by default), and `SIGNALPOST_CACHE_DIR`. `make test` sets `SIGNALPOST_CACHE=0`, except in the tests that use a fixture cache.

**Declaration:**
- `run.declared_caches` in the report: name, build time, sha256, rows, hits used and hits rejected live.
- A "Declared caches" section in `SUBMISSION.md` and the README: what each cache contains, its sources and their terms, how it was built, and that it holds candidates only.
- The same line goes in the next email to Builderr.

**Builder scripts:** new `scripts/cache/`, all resumable (they append to a JSONL work file and skip done keys) and polite (per-host rate limit, robots.txt, the same User-Agent):
- `fetch_brreg_bulk.py`
- `build_website_cache.py`
- `build_nav_cache.py`
- `pack_cache.py`, which writes the manifest

A `make cache` target runs them in order.

---

## Part B: NAV job cache (do first: most certain gain, smallest effort)

### B1. Offline build (`scripts/cache/build_nav_cache.py`)
1. Fetch the public token.
2. Walk `/api/v1/feed` **from the beginning** (June 2023), not from 45 days back. Keep the latest status per uuid and record the final `next_url` id as the **cursor**. That is about 1–2k pages at ~3 s each, roughly 1–2 hours offline.
3. For every uuid whose status is `ACTIVE`, fetch `/api/v1/feedentry/{uuid}` with 4 workers. That is roughly 20–30k entries, about 2 hours. Store:
   - `uuid`, `employer.orgnr`, `employer.name`, `employer.homepage`, `title`, `published`, `expires`, `modified`.
4. Map each `employer.orgnr` to its **parent** through `brreg-subunits.jsonl.gz`. NAV ads usually carry the *underenhet* org number, so this catches ads even when the run's locations module misses a sub-unit.
5. Write `nav-jobs.jsonl.gz` with one row per (parent orgnr, uuid), plus `nav_cursor` and `nav_built_at` in the manifest.
6. Side product: write every `employer.homepage` to the website-candidate build (Part C). Keying it by org number makes it a strong candidate.

### B2. Runtime (`nav_jobs.py`)
Replace `_build_feed_index(days)` with `_build_feed_index_from_cache(cache)`:
1. **Incremental walk:** start the feed at the cached cursor and walk to the end. This covers only the changes since the cache was built: about 2 pages per day of cache age, so 2–3 minutes when the cache is 30 days old instead of 5+. It records each uuid's new status and `businessName`.
2. **Cached ads for the batch:** take `cache.nav_ads_for(batch orgs ∪ their sub-units)`.
   - Drop any ad the incremental walk saw become `INACTIVE`, and any whose `expires` date has passed.
   - Re-fetch the feed entry live for each remaining ad (one request per ad, a few dozen per batch). This provides fresh evidence and confirms the ad is still `ACTIVE` and the org number still matches.
3. **Ads that are new since the cache:** keep today's word-overlap prefetch, but only over the incremental entries, and with the document-frequency limit raised from 25 to 200. The incremental set is small, so this is affordable.
4. **Fallback:** if there is no cache or it fails the checksum, today's 45-day walk runs unchanged.
5. Claims and evidence stay exactly as they are now (`_append_posting`): source class `public_job_board`, the public arbeidsplassen ad URL, and the sha of the live payload.

**If the incremental walk hits the time limit**, cached ads that were re-confirmed live are still published. Status is `failed` (not `not_available`) only when neither the cache nor the walk could be checked.

### B3. Tests (hermetic)
- A fixture cache with 3 ads (own org, a sub-unit, a different org) and a mocked feed.
  - A cached ad that the incremental walk marked `INACTIVE` is not published.
  - A live entry whose orgnr differs from the cached one is not published.
  - An expired ad is not published.
- A checksum mismatch falls back to the 45-day walk.
- Mapping a sub-unit ad to its parent works without the locations module.
- Runtime: with a 30-day-old fixture cursor, the walk stops at the end of the feed.

### B4. Acceptance
On `tests/fixtures/cohort-1000.jsonl`:
- the number of companies with ≥1 NAV posting rises clearly from 2 (gather data before setting a target number);
- every posting's `employer.orgnr` is in {org, sub-units};
- the NAV stage of the run takes less wall time than before.

---

## Part C: website candidate cache (biggest recall lever)

### C1. Candidate sources, all free
| Source | How | Strength |
|---|---|---|
| Brreg bulk `enheter/lastned` | `hjemmeside`, `epostadresse` for all 411k | Official |
| Brreg bulk `underenheter/lastned` | sub-unit `hjemmeside`, email domains and **names** (brands) | Official. Not used today beyond the locations module |
| NAV feed (Part B) | `employer.homepage` on org-number-keyed ads | Exact org link |
| Wikidata | one SPARQL query for **all** items with P2333 and P856 (instead of one query per 150 companies at run time) | Org-linked |
| Slugs | legal name, sub-unit names and former names → `.no`/`.com` slugs, kept only if DNS resolves | Weak; needs hard proof |
| Common Crawl (phase C4) | `.no` pages that print an org number | Exact text; candidate only |

### C2. Offline verification (`scripts/cache/build_website_cache.py`)
- **Run the same gate code as the agent.** Move `_try_candidate_site` and `_require_hard_site_proof` out of `run_agent.py` into a new `src/norway_company_agent/site_verify.py`, so the builder and the runtime share one implementation. This refactor has value on its own.
- **Scope, in tiers to bound the crawl:**
  - **Tier 1:** orgs with a registry, sub-unit, NAV or Wikidata URL, or with employees. That is about 90–100k orgs, full verification.
  - **Tier 2:** all other orgs, except housing co-ops (BRL/ESEK/SAM) and dormant holding companies. Slug candidates that resolve in DNS get verification; the rest are skipped.
  - Housing co-ops and dormant holding companies are not crawled.
- **Each row records:**
  - `org`, `candidates: [{url, source, verdict: verified|name_only|rejected|unreachable, proof_type, proof_url, checked_at}]`, `tried_slugs`.
  - The **proof URL** matters: at run time the agent fetches the homepage and that one page, instead of searching contact pages again.
- **Throughput:** about 8 hosts in parallel, 1 request per second per host, sharded and resumable. Expect 1–2 days of wall time on a home connection for Tier 1. Run a 2,000-org pilot first (C5).

### C3. Runtime (new first step in `enrich_profile` and `_discover_website`)
1. **Step A-cache** runs before Wikidata. For each cached candidate with verdict `verified`, best first:
   - call `site_verify.try_candidate_site(origin="declared_cache", proof_hint_url=proof_url)`;
   - this fetches the homepage live, applies the name check and conflict check, then looks for hard proof **on the proof URL first** and on the usual contact pages after that.
   - A cache hit gets the **same** gate as a guessed domain. A registry-declared URL in the cache keeps the registry rule.
2. If the live check fails (the site changed), fall through to the existing discovery steps, which skip the slugs in `tried_slugs` that were already proven dead.
3. Candidates with verdict `name_only` are tried after `verified` ones, still requiring hard proof.
4. **Provenance:**
   - the evidence `source_type` is `declared_cache_<original source>_verified_company_website`;
   - the claim's evidence holds the live URL, timestamp and hash;
   - `identity_assessment.reasons` names the cache and its build date.
   - This also fixes the mislabelled `registry_linked_company_website` (P0.4 in the scoring plan) for these paths.
5. The batch-level shared-domain check runs unchanged.

### C4. Common Crawl reverse index (after C1–C3 have been measured)
- Read the CC columnar index (Parquet) with DuckDB over HTTPS. Rows are sorted by `url_surtkey`, so filtering on `url_surtkey >= 'no,' AND < 'no-'` skips most row groups.
- Keep homepages and contact, about, privacy and terms paths, and fetch only those WARC records by byte range from `data.commoncrawl.org`.
- Extract org numbers next to an "org.nr", "foretaksregisteret" or "MVA" label. Output `domain → {orgnr}`, then drop domains that print 3 or more org numbers (directories and accountants).
- Join to the universe and add the results as candidates with `source=commoncrawl`, verified in C2 like any other.
- Gain: brand domains that cannot be guessed from the legal name. Do a spike first (see "Order of work") to measure how many `.no` records there are, the bytes involved, and the hit rate.

### C5. Pilot and acceptance
- **Pilot:** build the cache for the orgs in `cohort-1000.jsonl` and `smoke-companies.jsonl` only (about 1,100 orgs, a few hours).
- **Compare** before and after on those batches:
  - verified websites (today 21/100 and 125/1,000);
  - runtime;
  - requests.
- **Precision gate:** hand-review **every** newly verified site with `scripts/audit_external_claims.py`. Proceed to the full universe build only if new sites are ≥97% correct.
- **Leak check:** the pilot measures the method on companies we already know. It does not hide anything, because the full cache covers the whole public universe and Builderr's batch is drawn from it.

---

## Part D: keyless career and ATS job extraction

Today `_extract_job_vacancies` keeps only *titles* from JSON-LD and CSS. It has no URL, no dates and no employer check, so very little gets published.

### D1. Structured postings from JSON-LD `JobPosting`
- New `website.extract_jobposting_ld(url, soup) -> list[Posting]`. It reads:
  - `title`, `url` (or the page URL), `datePosted`, `validThrough`;
  - `hiringOrganization.name` and `sameAs`, `identifier` if present;
  - `jobLocation.address.addressCountry` and `addressLocality`.
- **Publish only when all of these hold:**
  1. the page is on the verified domain, or on a known ATS host reached by a link from the verified site;
  2. `hiringOrganization.name` matches the legal name (the same phrase rule as press news) or the verified site's own organisation name;
  3. the location is Norway (or not given, on a `.no` site);
  4. `validThrough` is today or later, or `datePosted` is within 120 days;
  5. on a group ATS, the company name is in the posting.
- **Crawl:** from the career URL already found on verified sites, fetch the listing page, then up to 10 job-detail links on the same host or ATS. That is about 11 requests and only for companies with a career page, which keeps it inside the per-company request budget.

### D2. ATS adapters (one spike decides which ones)
- **ATS platforms to check:** Teamtailor, Webcruiter, ReachMee, Jobylon, Recman, Easycruit, HR-manager, Varbi, Jobbnorge.
- **For each one, check:**
  - a public listing endpoint (JSON, RSS, or detail pages with JobPosting JSON-LD);
  - that robots.txt allows it;
  - that the listing is per company (by subdomain or path).
- **Implementation:** each adapter returns the same `Posting` shape and goes through the D1 gate.
- **Out of scope:** FINN, LinkedIn and Indeed, which restrict automated access under the source policy.

### D3. Cache link
The website builder (C2) also records the career URL and ATS slug for each verified org. At run time, D1 starts straight from that URL.

### D4. Tests
Fixture HTML for:
- a valid JobPosting;
- an expired one;
- one with a foreign location;
- one with another employer on a group ATS;
- one with no `hiringOrganization`.

Only the valid one is published, with URL, dates and evidence.

---

## Order of work

| Step | What | Effort | Depends on |
|---|---|---|---|
| 0 | Spikes: full NAV feed (pages, number of active ads, time); Brreg bulk downloads; 3 ATS formats; CC `.no` row and byte count | 1 day | |
| 1 | Part A container, loader, manifest, report declaration, fallback | 1 day | |
| 2 | Part B: NAV cache builder and runtime (B1–B3), measure on cohort-1000 | 2 days | 1 |
| 3 | Move the gate into `site_verify.py` (no behaviour change; all 570 tests still pass) | 0.5 day | |
| 4 | Part C pilot (C1–C3, C5 on about 1,100 orgs) plus a hand precision review | 2 days | 1, 3 |
| 5 | Part D1 JobPosting extractor plus the adapters that passed the spike | 2 days | 3 |
| 6 | Full-universe website cache build (Tier 1, then Tier 2) | 1–2 days of wall time | 4 passing |
| 7 | C4 Common Crawl index, if the spike looked good | 2–3 days | 6 |
| 8 | Full 1,000 run at HEAD, refresh the smoke artifacts, docs and declaration, email Builderr | 0.5 day | all |

Steps 2 and 5 can ship on their own before the big cache build.

## Risks and how they're handled

- **Builderr reads "declared cache" more narrowly than we do.** Send one short email before step 6 describing the cache, which holds candidates only and is re-verified live. The NAV cache (step 2) is low risk either way, because each ad is re-fetched live.
- **A stale cache publishes a dead site or a closed ad.** This can't happen: everything is re-fetched live, and a failed check means nothing is published.
- **A precision regression from new candidate volume.** The gate is the same and so are the hard-proof rules. The pilot needs ≥97% on a hand review before scaling up.
- **Repository bloat.** The 40 MB budget is enforced in `pack_cache.py`. If it's exceeded, drop Tier 2 rows with verdict `rejected`.
- **A cache that gets old across daily batches.** NAV's incremental walk covers any age, with time proportional to the age. Website candidates change slowly. The report shows the cache age.
