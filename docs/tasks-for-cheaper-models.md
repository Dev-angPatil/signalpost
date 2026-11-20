# Tasks for cheaper, faster models

Each task is self-contained: goal, inputs, steps, and a check that shows it is done.
They take time but need no design decisions. Do them in order; T1 matters most.

Rules for every task:
- Do not weaken or remove a verification gate. If a fix needs a judgment call, stop and write the question at the bottom of this file.
- Run `make test` before finishing. It must print `OK`.
- Do not commit. Leave changes in the working tree.

Background: qualification needs a score of at least 65 and at least 95% of checked claims correct. Last score was about 53 with 89% correct (50 of 56). Registry claims are safe. The risk is in about 420 external claims per 1,000 companies: websites (111), social links (111), news items (179) and jobs (17).

---

## T1. Hand-check every external claim (most important)

**Goal:** a verdict for each of the 355 claims in `out/cohort-1000-precision-audit.json` (list under the review key), so the wrong ones can be fixed with gates.

**Steps**
1. For each row, open the URL. Decide whether it belongs to the company with that organisation number and legal name (compare address, org number, people, industry against the row's registry data).
2. For news rows also check: is it really a dated news item about this company, and is the date shown on the page the date we publish?
3. For social rows: does the profile belong to this legal company, not a namesake, a parent brand or a person?
4. Write `out/cohort-1000-precision-verdicts.csv` with columns: `org,family,url,verdict,reason`. Verdict is `correct`, `wrong` or `unsure`. Give one short reason for every `wrong` or `unsure`.

**Look closely at**
- Websites with basis `name_equals_domain_plus_corroboration` (12) and single-token name matches such as `normoria.no`, `mersalg.no`, `sqq.no`.
- Websites on shared platforms, for example `minskole.no/sandnesfriskole`.
- Social handles that differ from the legal name, for example `kulturhusetNormoria`, `UppigardNatadal`, `autobudgetbilforhandler`.
- Old news, for example GROVASTØYLEN AS (994306618) with an item from 2015.

**Done when:** the CSV has 355 rows and the last line of your report gives counts of correct, wrong and unsure per family. No code changes in this task.

## T2. Same check on a fresh sample

**Goal:** confirm the result on companies never used in development.

**Steps**
1. Pick 100 organisation numbers at random from Brreg that are not in `tests/fixtures/cohort-1000.jsonl` or the smoke fixture.
2. Run the agent on them (see the evaluator command at the top of `README.md`), then `scripts/audit_external_claims.py` on the output.
3. Repeat T1's verdict steps. Write `out/fresh-100-precision-verdicts.csv`.

**Done when:** the CSV exists and the report states the share of correct claims.

## T3. Why are 889 of 1,000 companies without a website?

**Goal:** data to decide whether a website cache is worth building. Only 111 of 1,000 companies get a verified site, and news, social links and site jobs all depend on it.

**Steps**
1. Take 60 companies from the 1,000-company run that have accounts filed but no `official_website` claim.
2. Search for each by hand. Record in `out/website-miss-analysis.csv`: `org,name,has_site,site_url,org_number_on_site,why_we_missed`.
3. For `why_we_missed` use one of: `no_site`, `domain_differs_from_name`, `site_has_no_org_number`, `only_facebook_page`, `site_blocked_or_js`, `other`.

**Done when:** 60 rows, plus a count per reason. No code changes.

## T4. Do not call old registry notices "recent"

**Goal:** the summary says "Recent registry notices: Forretningsadresse (2020-01-01)". A 2020 notice is not recent, and a reviewer may mark the sentence wrong.

**Steps**
1. Find where the summary builds the "Recent registry notices" sentence (`grep -rn "Recent registry notices" src run_agent.py`).
2. Use "Recent registry notices" only for notices dated within the last 12 months. For older ones say "Latest registry notices".
3. Add a unit test with one notice of each age.

**Done when:** `make test` prints `OK` and the new test covers both wordings.

## T5. Same line-splitting bug in the helper scripts

**Goal:** `text.splitlines()` splits JSONL rows that contain Unicode line separators. The runner's writer now escapes them, but old output files and other readers still break.

**Steps**
1. `grep -rn "splitlines()" --include=*.py src run_agent.py scripts`.
2. Where the line is reading JSONL (a `json.loads(line)` follows), replace `.splitlines()` with `.split("\n")`. Leave the others (`website.py:235`, `nav_jobs.py:185`, plain org-number lists) alone.

**Done when:** `make test` prints `OK`, and `scripts/audit_external_claims.py` still runs on a run's output.

## T6. Regenerate the stale smoke artifacts

**Goal:** `out/current-smoke-*` date from 14 November and no longer match the code.

**Steps:** rerun the smoke command that produced them (see `SUBMISSION.md`), with the declared cache enabled. Check the report shows `silent_drops: 0`.

**Done when:** the six `out/current-smoke-*` files are new and the report's qualification checks are all true.
