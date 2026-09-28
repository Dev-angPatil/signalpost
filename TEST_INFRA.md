# TEST_INFRA.md — E2E Test Suite Architecture & Testing Infrastructure

This document is the authoritative master reference for the testing architecture, feature inventory, execution tiers, invocation commands, and verification criteria of the **Signalpost Norwegian Company Intelligence Pipeline**.

---

## 1. Architecture & Testing Philosophy

### 1.1 Opaque-Box, Requirement-Driven Testing
The Signalpost E2E testing framework is designed from the perspective of an independent, adversarial benchmark evaluator (targeting $\ge 80/100$ on the Builderr benchmark). Tests treat the system under test as an **opaque box**, validating external behavior through:
1. Public CLI entrypoints (`run_agent.py`, `scripts/build_prototype.py`).
2. Public module interface contracts defined in `PROJECT.md` and `SCOPE.md`.
3. Strict schema adherence to `OUTPUT_CONTRACT.md` and `EVALUATION_MATRIX.md`.
4. Emitted disk artifacts (`out/envelopes.jsonl`, `out/profiles-1000.jsonl`, `out/smoke-report.json`, `out/showcase.html`).
5. Cryptographic SHA-256 evidence integrity and provenance traces.

### 1.2 Progressive Testability & Evolution Bridges
To enable parallel and staged development across milestones (M1–M5), tests employ **progressive testability**:
- Tier 1 feature tests verify public interface contracts with hermetic mock fixtures and zero live network dependency.
- For modules scheduled in upcoming milestones (e.g. `announcements.py` in M2, `synthesis.py` in M3, `showcase.py` in M4), tests target the public function signatures defined in `PROJECT.md` using graceful import guards and specification-compliant contract simulations. When the underlying modules are completed, the test suite executes directly against the production implementations without modifying test logic.

### 1.3 Anti-Cheating & Integrity Invariants
The test suite strictly enforces the core integrity principles of Signalpost:
- **Zero Hallucination / Zero Fabrication**: Financial values that are missing or unfiled in official registers must be preserved as `not_available` or `not_applicable`—never synthesized as `0` or `0.0%`.
- **Zero Wrong-Company Publications**: External web, social, or hiring signals must pass deterministic identity gating (exact legal name tokens or 9-digit orgnr matching); uncertain candidates must be quarantined as `ambiguous`.
- **Zero Silent Drops**: Every input organisation number must yield exactly one terminal JSONL envelope. If an internal exception occurs, the system catches it and emits a `failed` terminal envelope.
- **Strict Cryptographic Lineage**: 100% of published factual claims must link to an evidence record containing `source_url`, `content_sha256`, `retrieved_at`, and a verbatim `claim_span`.

---

## 2. Test Hierarchy (Tiers 1 through 5)

The test suite is organized into five complementary tiers:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 5: Adversarial Hardening (XSS, SSRF, Memory Bounds, Encoding)      │
├────────────────────────────────────────────────────────────────────────┤
│ Tier 4: Real-World Scenarios (Equinor, Kongsberg, Startups, ENK, 1000) │
├────────────────────────────────────────────────────────────────────────┤
│ Tier 3: Cross-Feature Interactions (Web+Identity, Actions+Ratios, CLI) │
├────────────────────────────────────────────────────────────────────────┤
│ Tier 2: Boundary & Corner Cases (Division-by-Zero, 404/500, Quarantine)│
├────────────────────────────────────────────────────────────────────────┤
│ Tier 1: Feature Coverage E2E Suite (F1 to F15 Happy Paths, >= 75 tests)│
└────────────────────────────────────────────────────────────────────────┘
```

| Tier | File Path | Focus & Scope | Minimum Test Count | Execution Mode |
|---|---|---|:---:|---|
| **Tier 1** | `tests/test_e2e_tier1.py` | Happy-path coverage across ALL 15 features (F1 to F15) | $\ge 75$ ($\ge 5$ per feature) | Fast, hermetic, mocked I/O (< 3s) |
| **Tier 2** | `tests/test_e2e_tier2.py` | Boundary, stress, error handling, and corner case conditions | $\ge 75$ ($\ge 5$ per feature) | Fast, hermetic, mocked I/O (< 5s) |
| **Tier 3** | `tests/test_e2e_tier3.py` | Cross-feature interactions and multi-module pipelines | $\ge 15$ | Integrated pipeline (< 10s) |
| **Tier 4** | `tests/test_e2e_tier4.py` | Real-world company cohorts, 100-company smoke, scale run | $\ge 8$ | Bulk snapshot & fixture (< 30s) |
| **Tier 5** | `tests/test_e2e_tier5.py` | Adversarial attacks, injection, SSRF, memory leaks | $\ge 10$ | Hardened security suite (< 10s) |

---

## 3. Feature Inventory & Coverage Matrix (F1 through F15)

| # | Feature Code | Feature Name | Milestone | Public Module / Interface | Authoritative Source | Tier 1 Target |
|---|---|---|---|---|---|:---:|
| 1 | **F1** | Priority Sub-Path Routing | M1 | `norway_company_agent.website._priority_links` | R1, `PROJECT.md:F1`, `EVALUATION_MATRIX.md` | $\ge 5$ |
| 2 | **F2** | Direct Contact Info Extraction | M1 | `norway_company_agent.website` & `contract.py` | R1, `PROJECT.md:F2`, `OUTPUT_CONTRACT.md` | $\ge 5$ |
| 3 | **F3** | Career & Hiring Signal Extraction | M1 | `norway_company_agent.website` & `contract.py` | R1, `PROJECT.md:F3`, `EVALUATION_MATRIX.md` | $\ge 5$ |
| 4 | **F4** | Identity Gate & Token Hardening | M1 | `norway_company_agent.identity` | R1, `PROJECT.md:F4`, `EVALUATION_MATRIX.md` | $\ge 5$ |
| 5 | **F5** | Kunngjøringer HTTP Client | M2 | `norway_company_agent.announcements.fetch_announcements` | R2, `PROJECT.md:F5`, `ORIGINAL_REQUEST.md` | $\ge 5$ |
| 6 | **F6** | Notice Classification Engine | M2 | `norway_company_agent.announcements.classify_notice` | R2, `PROJECT.md:F6`, `EVALUATION_MATRIX.md` | $\ge 5$ |
| 7 | **F7** | Announcements Lineage & Claims | M2 | `norway_company_agent.contract.build_output_envelope` | R2, `PROJECT.md:F7`, `OUTPUT_CONTRACT.md` | $\ge 5$ |
| 8 | **F8** | Financial Health Metrics Engine | M3 | `norway_company_agent.synthesis.compute_financial_metrics` | R3, `PROJECT.md:F8`, `EVALUATION_MATRIX.md` | $\ge 5$ |
| 9 | **F9** | Executive Decision Brief Engine | M3 | `norway_company_agent.synthesis.synthesize_executive_profile` | R3, `PROJECT.md:F9`, `ORIGINAL_REQUEST.md` | $\ge 5$ |
| 10 | **F10** | Explicit Knowns vs. Unknowns | M3 | `norway_company_agent.synthesis.build_knowns_and_unknowns` | R3, `PROJECT.md:F10`, `OUTPUT_CONTRACT.md` | $\ge 5$ |
| 11 | **F11** | Interactive HTML Dashboard | M4 | `norway_company_agent.showcase.generate_showcase_html` | R4, `PROJECT.md:F11`, `ORIGINAL_REQUEST.md` | $\ge 5$ |
| 12 | **F12** | Streaming Execution & Shielding | M5 | `run_agent.py` & `norway_company_agent.batch` | R5, `PROJECT.md:F12`, `OUTPUT_CONTRACT.md` | $\ge 5$ |
| 13 | **F13** | Smoke Test & Report Generator | M5 | `run_agent.py` (`--report`, `out/smoke-report.json`) | R5, `PROJECT.md:F13`, `ORIGINAL_REQUEST.md` | $\ge 5$ |
| 14 | **F14** | 1,000-Profile Scale Run | M5 | `run_agent.py` (`out/profiles-1000.jsonl`) | R5, `PROJECT.md:F14`, `ORIGINAL_REQUEST.md` | $\ge 5$ |
| 15 | **F15** | Documentation Synchronization | M5 | `VISION.md`, `ARCHITECTURE.md`, `README.md` | User Rules, `PROJECT.md:F15` | $\ge 5$ |

---

## 4. Feature Specifications & Behavioral Invariants

### F1: Priority Sub-Path Routing
- **Scope**: Extract internal links matching 4 priority corporate routes:
  - *About*: `/om-oss`, `/om_oss`, `/about`, `/om-selskapet`, `/hvem-er-vi`
  - *Contact*: `/kontakt`, `/contact`, `/kontakt-oss`, `/lokasjoner`, `/locations`
  - *Careers*: `/karriere`, `/career`, `/careers`, `/ledige-stillinger`, `/jobs`, `/jobb`
  - *Investor*: `/investor`, `/investors`, `/investor-relations`, `/finansiell-informasjon`, `/ir`
- **Invariants**:
  1. Maximum 4 priority subpages fetched per entity (`limit = 4`).
  2. Subpages must match top registered domain (`_registered_domain(subpage) == _registered_domain(homepage)`).
  3. Strip hash fragments (`#...`) and query parameters; discard self-referential homepage links.
  4. Secondary pages must respect payload limits (max 1MB per secondary page).

### F2: Direct Contact Info Extraction
- **Scope**: Extract validated direct email addresses, telephone numbers, and visiting/postal addresses from crawled pages.
- **Invariants**:
  1. Strip image extensions (`.png`, `.svg`), code tokens (`webpack@`, `core-js@`), and template placeholders.
  2. Distinguish 9-digit Norwegian organisation numbers from phone numbers.
  3. Support `mailto:` and `tel:` protocol schemes, microdata `PostalAddress`, and raw text.
  4. Normalise phone numbers to E.164 (`+47...`) or standard Norwegian spaced format (`+47 22 00 00 00`).
  5. Emit envelope claims `contact_email`, `contact_phone`, `contact_address` linked to evidence.

### F3: Career & Hiring Signal Extraction
- **Scope**: Extract open position counts, job titles, external ATS links, and active hiring boolean flag.
- **Invariants**:
  1. Parse Schema.org `JobPosting` structured JSON-LD and HTML job cards.
  2. Detect outbound ATS portals (Teamtailor, Webcruiter, Finn, Recman, Jobbnorge, Lever, Greenhouse).
  3. Explicit negative statements ("*Vi har for tiden ingen ledige stillinger*") set `hiring_active: False` and `open_positions_count: 0` with state `available`.
  4. Emit envelope claims `hiring_active`, `open_positions_count`, `career_url` linked to `ev-website`.

### F4: Identity Gate & Token Hardening
- **Scope**: Deterministic verification preventing wrong-company attribution.
- **Invariants**:
  1. Support single-token legal names (e.g. `EQUINOR ASA` -> core token `equinor`) via `<title>`, `<meta description>`, JSON-LD `Organization.name`, and `/om-oss` text.
  2. Strip generic legal suffixes (`AS`, `ASA`, `ENK`, `DA`, `ANS`, `IKS`, `BA`, `SA`, `NUF`).
  3. Quarantines parked domains, hosting placeholders, sports clubs (`B.I.L.`), and umbrella group sites (`publishable: False`, availability `ambiguous`).
  4. Social handles validated against company name token sequences; mismatched handles quarantined without failing website.

### F5: Kunngjøringer HTTP Client
- **Scope**: Query official Norwegian public notices from `https://w2.brreg.no/kunngjoring/hent.jsp`.
- **Invariants**:
  1. HTTP POST with `application/x-www-form-urlencoded` body `orgnr={org}`.
  2. Decode response bytes using `windows-1252` to preserve Norwegian characters (`æ`, `ø`, `å`).
  3. If entity has zero notices, return `events: []`, `count: 0`, status `available` (proven by empty search page SHA-256).
  4. Accepts pluggable `http_fetcher=None` for dependency injection and hermetic test isolation.
  5. Declared third-party cost is strictly $0.00 USD.

### F6: Notice Classification Engine
- **Scope**: Classify public notices into structured categories:
  - `capital_change`: `Kapitalforhøyelse`, `Kapitalnedsettelse`, `Innbetaling av aksjekapital`, `Erverv av egne aksjer-fullmakt`
  - `board_appointment`: `Endring av styre`, `Nytt styre`, `Valg av styre`, `Styrets leder`, `Daglig leder`
  - `legal_filing`: `Godkjent årsregnskap`, `Endring av vedtekter`, `Endring av revisor`, `Endring av foretaksnavn`, `Fusjon`
  - `insolvency`: `Konkursåpning`, `Avslutning av konkursbehandling`, `Tvangsavvikling`, `Oppløsning`, `Sletting`
- **Invariants**:
  1. Standardize dates from `DD.MM.YYYY` to ISO `YYYY-MM-DD`.
  2. Case-insensitive and diacritic-insensitive matching.
  3. Unknown types fallback gracefully to `legal_filing` or `other` without throwing exceptions.

### F7: Announcements Lineage & Claims
- **Scope**: Emit structured claims backed by SHA-256 hashed evidence records conforming to `OUTPUT_CONTRACT.md`.
- **Invariants**:
  1. Emit claims: `corporate_actions_count`, `latest_corporate_action_date`, `latest_capital_change_date`, `latest_board_appointment_date`, `latest_legal_filing_date`, `latest_insolvency_date`.
  2. Zero notices: `corporate_actions_count` has value `0` with availability `available`; dates have value `None` with availability `not_available`.
  3. Evidence record `ev-announcements`: source class `official_public_notices`, canonical URL, non-empty 64-char lowercase SHA-256 hash.
  4. Idempotent: re-evaluating identical snapshot produces zero false change events.

### F8: Financial Health Metrics Engine
- **Scope**: Deterministic corporate financial ratio calculation from statutory annual filings:
  - Operating Margin (%) = $(\text{operating\_result} / \text{revenue}) \times 100$
  - Net Profit Margin (%) = $(\text{annual\_result} / \text{revenue}) \times 100$
  - Equity Ratio (%) = $(\text{equity} / \text{assets}) \times 100$
  - Debt-to-Assets Ratio (%) = $(\text{debt} / \text{assets}) \times 100$
  - Multi-Year Revenue Growth (%) = $((\text{rev}_t - \text{rev}_{t-1}) / \text{rev}_{t-1}) \times 100$
- **Invariants**:
  1. Division-by-zero protection: If `revenue <= 0` or missing, margin metrics must be `None` (availability `not_available`), NEVER `0.0%` or `NaN`.
  2. If `assets <= 0` or missing, equity and debt ratios must be `None` (availability `not_available`).
  3. Negative equity is preserved as a valid negative ratio (e.g. `-50.0%`, availability `available`).
  4. Zero debt in solvent company evaluates to `0.0%` with availability `available`.
  5. Statutorily exempt forms (e.g. `ENK`) evaluate to `None` with availability `not_applicable`.

### F9: Executive Decision Brief Engine
- **Scope**: Concise, strictly grounded markdown executive profile summarizing:
  1. Business Scope (official NACE classification + verified web description).
  2. Active Leadership (CEO, Board Chair, governance structure).
  3. Financial Health & Trajectory (revenue scale, margins, capitalization).
  4. Operational Footprint (registered office, subunits, verified web/social channels).
- **Invariants**:
  1. Zero speculation or hallucination.
  2. If website was quarantined (`ambiguous`), brief MUST NOT cite website copy; scope derived strictly from official NACE description.
  3. Entities undergoing bankruptcy proceedings lead prominently with insolvency warnings.
  4. Emit claims `executive_summary` and `business_scope` linked to evidence.

### F10: Explicit Knowns vs. Unknowns
- **Scope**: Two distinct structured lists:
  - `knowns`: Verified assertions with citations and evidence IDs.
  - `unknowns`: Missing, unobserved, or statutorily exempt dimensions.
- **Invariants**:
  1. Explicitly distinguish between:
     - `not_available`: Obligated item was checked and absent.
     - `not_applicable`: Statutorily exempt under Norwegian law (e.g. Accounting Act § 1-2 for `ENK`).
     - `ambiguous`: Candidate site/profile found but failed identity gate.
  2. Never infer that "missing website" proves "company has no website".
  3. Emit claims `known_facts_count` and `unreported_dimensions_count`.

### F11: Interactive HTML Dashboard Showcase
- **Scope**: Inspectable, mobile-responsive, zero-dependency HTML file (`out/showcase.html`):
  1. Instant client-side entity search (`#q`).
  2. Multi-dimensional faceted filtering (Legal form `#form`, Status `#state`, Accounts `#finance`, Sample `#slice`).
  3. Financial ratios and normalized accounting inspection table.
  4. Corporate action logs and public notice timeline.
  5. Cryptographic provenance inspector (`<details class="source">` with SHA-256 hash).
  6. Workspace actions: pin entities (`localStorage`) and export filtered results to JSON blob.
- **Invariants**:
  1. Standalone offline file: inline CSS and vanilla JS; zero external CDN dependencies.
  2. Safe escaping: XSS payloads in profile text sanitized (`<\\/` escaping and HTML entities).
  3. Zero division guard in UI: missing revenue or ratios render "Not reported", never `NaN%` or `0.0%`.

### F12: Streaming Execution & Shielding
- **Scope**: Robust batch runner (`run_agent.py`):
  1. Incremental streaming to disk (JSONL appended or flushed atomically via `.tmp` replace).
  2. Bounded memory consumption: peak RSS $< 500$ MB across batches of 100 to 1,000 entities.
  3. Top-level exception shielding: unhandled errors on an entity are caught and emit a terminal envelope with state `failed` and diagnostic errors, guaranteeing **zero silent drops**.
  4. Batch resume capability: `--resume` inspects existing output and enriches only pending organisations.
  5. Configurable checkpoint interval (`--checkpoint-every N`).

### F13: Smoke Test & Report Generator
- **Scope**: 100-company batch generating `out/smoke-report.json`.
- **Invariants**:
  1. Zero silent drops: exactly 100 envelopes emitted for 100 inputs (`report.silent_drops == 0`).
  2. Strict runtime bound: execution completes in $< 5$ minutes ($< 300.0$ seconds).
  3. Request budget bound: total outbound requests $\le 2,000$.
  4. Cost budget bound: declared third-party cost is strictly $\$0.00$ USD.
  5. Report contains required schema: `run_id`, `runtime_seconds`, `silent_drops`, `claims_summary`, `operations`, `qualification_status`.

### F14: 1,000-Profile Scale Run
- **Scope**: Scale execution over canonical 1,000-company cohort generating `out/profiles-1000.jsonl`.
- **Invariants**:
  1. Contains $\ge 1,000$ valid, non-empty JSON lines.
  2. Every profile contains 9-digit `organisation_number`, `legal_form`, and official identity anchor.
  3. 100% of lines parse with `json.loads` without error.
  4. Represents Norwegian corporate diversity ($\ge 5$ distinct legal forms: `AS`, `ASA`, `ENK`, `BRL`, `STI`, etc.).
  5. Runtime $\le 45$ minutes (2,700 seconds) and declared cost $\$0.00$ USD.

### F15: Documentation Synchronization
- **Scope**: Maintain authoritative, synchronized repository documentation:
  1. `VISION.md`: Core mission, anti-bloat philosophy, target UX, $\ge 80$ target score.
  2. `ARCHITECTURE.md`: 5-phase data flow, component map, Mermaid/ASCII flowchart, technical stack.
  3. `README.md`: One-command execution (`uv run python run_agent.py`), batch CLI flags, test commands, submission details ($0.00 cost).
- **Invariants**:
  1. Files exist and exceed 1,000 bytes.
  2. Zero unexpanded template placeholders (`TODO`, `FIXME`, `TBD`, `[placeholder]`).
  3. All referenced relative repository paths exist on disk.
  4. Numerical limits (65 qualification, 80 target, 2000 requests, 45 min runtime, $0.00 cost) are consistent across all documents.

---

## 5. Test Invocation & Verification Commands

### 5.1 Run Full Test Suite
```bash
# Using standard Python unittest runner:
uv run python -m unittest discover -s tests -p "test_e2e_*.py"

# Using pytest:
uv run --with pytest pytest -v tests/test_e2e_*.py
```

### 5.2 Run Individual Tiers
```bash
# Tier 1: Feature Coverage (>= 75 tests)
uv run python -m unittest tests/test_e2e_tier1.py

# Tier 2: Boundary & Corner Cases (>= 75 tests)
uv run python -m unittest tests/test_e2e_tier2.py

# Tier 3: Cross-Feature Interactions (>= 15 tests)
uv run python -m unittest tests/test_e2e_tier3.py

# Tier 4: Real-World Scenarios (>= 8 tests)
uv run python -m unittest tests/test_e2e_tier4.py
```

### 5.3 Run Single Feature Group in Tier 1
```bash
# Example: Run only F1 Priority Sub-Path tests
uv run python -m unittest tests.test_e2e_tier1.TestF1PrioritySubPathRouting

# Example: Run only F8 Financial Metrics tests
uv run python -m unittest tests.test_e2e_tier1.TestF8FinancialHealthMetricsEngine
```

### 5.4 Benchmark & Baseline Verification
```bash
# Run existing POC baseline tests (104 tests):
uv run python -m unittest tests/test_poc.py

# Verify smoke run report generation:
uv run python run_agent.py --input tests/fixtures/smoke-100.jsonl --output out/envelopes.jsonl --profiles-output out/profiles.jsonl --report out/smoke-report.json --showcase out/showcase.html
```

---

## 6. Coverage Thresholds & Gate Requirements

| Metric | Threshold | Verification Method |
|---|:---:|---|
| **Tier 1 Test Count** | $\ge 75$ tests | Automated count in `test_e2e_tier1.py` |
| **Tier 1 Feature Coverage** | 100% of F1–F15 ($\ge 5$ per feature) | Test class structure validation |
| **Tier 1 Execution Runtime** | $< 5.0$ seconds | `time.monotonic()` in test runner |
| **Hermetic Network Isolation** | 0 outbound network requests | Mocking / offline fixtures |
| **Silent Drops** | Exactly 0 drops | Assert `len(envelopes) == len(inputs)` |
| **Financial Hallucinations** | Exactly 0 fabrications | Assert `revenue <= 0` yields `None`, never `0.0%` |
| **Wrong-Company Publications** | Exactly 0 misattributions | Assert unverified sites marked `ambiguous` |
| **Evidence Provenance Backing** | 100% of claims backed | Assert all claims contain valid `evidence_ids` |
