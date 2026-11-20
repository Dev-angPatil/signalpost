# ARCHITECTURE.md — Signalpost Master System Architecture

Architectural blueprint and component specifications for the **Signalpost Autonomous Norwegian Corporate Intelligence System**.

> For modular deep-dive specifications on specific subsystems, see the [`docs/architecture/`](docs/architecture/README.md) suite:
> - [1. System Overview & Orchestration](docs/architecture/SYSTEM_OVERVIEW.md)
> - [2. Identity Resolution & Anti-Contamination Gates](docs/architecture/IDENTITY_AND_GATING.md)
> - [3. Data Pipeline, Contracts & Evidence Lineage](docs/architecture/DATA_PIPELINE_AND_STORAGE.md)
> - [4. External Integrations & Connectors](docs/architecture/INTEGRATIONS_AND_CONNECTORS.md)
> - [5. Cost, Performance & Resource Governance](docs/architecture/COST_AND_RESOURCE_GOVERNANCE.md)

---

## 1. High-Level System Architecture

Signalpost is structured around an **Anchor-Enrich-Verify-Synthesize** pipeline. The 9-digit Norwegian organisation number (*organisasjonsnummer* / *orgnr*) serves as the immutable root of trust. External claims from websites, search engines, job boards, or news sources are admitted into published envelopes only if they pass cryptographic and deterministic identity gates.

```mermaid
flowchart TD
    IN[Input: Batch of 9-Digit Norwegian Org Numbers] --> ORCH[run_agent.py Orchestrator]
    
    subgraph Layer1["1. Identity Anchor Layer (official.py)"]
        ORCH --> ANCHOR[Brønnøysund Enhetsregisteret API / Bulk Cache]
        ANCHOR --> ENTITY[Legal Name, Legal Form, NACE Sector, Addresses, Registered Web/Email]
    end
    
    subgraph Layer2["2. Official Enrichment Layer (official.py, announcements.py)"]
        ENTITY --> FIN[Regnskapsregisteret: Audited Annual Accounts]
        ENTITY --> ROLES[Roles API: Management, Board & Signatories]
        ENTITY --> SUB[Subunits API: Workplaces & Physical Locations]
        ENTITY --> NOTICES[Kunngjøringer: Statutory Notices & Bankruptcies]
    end
    
    subgraph Layer3["3. Verified Web & Discovery Layer (discovery.py, website.py, identity.py)"]
        ENTITY --> DISC[Candidate Discovery: Wikidata, Email Domain, Search, Domain Guessing]
        DISC --> GATE{Identity & Conflict Gate<br/>site_proof.py & identity.py}
        GATE -- "On-site 9-digit proof / Address phrase" --> CRAWL[Bounded Crawl: Socials, News, Careers]
        GATE -- "Foreign orgnr / Conflicting number" --> QUAR[Quarantine: Conflict Detected]
        GATE -- "Uncorroborated Name Only" --> AMBIG[Quarantine: 'ambiguous' State]
    end

    subgraph Layer4["4. Background & Batch Post-Passes (nav_jobs.py, ai_enrich.py)"]
        ORCH -. Background Thread .-> NAV[NAV Arbeidsplassen 45-Day Vacancy Index]
        CRAWL --> SHARED[Batch Quarantine: Disallow Shared Domains]
        NAV --> JOBMATCH[Match Vacancies by employer.orgnr]
        SHARED --> SYNTH[Executive Synthesis & Revenue Trajectory]
        JOBMATCH --> SYNTH
        SYNTH --> AI{Optional Claude 5.5<br/>Fact-Sheet Analysis}
    end

    subgraph Layer5["5. Terminal Contract Assembly (contract.py, evidence.py)"]
        AI --> LINEAGE[Cryptographic Lineage: SHA-256 Hashes & UTC Timestamps]
        SYNTH --> LINEAGE
        LINEAGE --> ENV[out/envelopes.jsonl (OUTPUT_CONTRACT.md)]
        LINEAGE --> OBS[out/observations.jsonl (External Footprint)]
        LINEAGE --> PROF[out/profiles.jsonl (Full Dossiers)]
        LINEAGE --> REP[out/report.json (Audit & Cost Summary)]
        LINEAGE --> UI[out/showcase.html (Interactive Dossier)]
    end
```

---

## 2. Core Architectural Components

The codebase in `src/norway_company_agent/` enforces identity isolation, non-fabrication of financials, and strict evidence lineage across modular components:

### 2.1 Identity Anchor & Statutory Registers (`official.py`, `announcements.py`)
- **Authority:** Brønnøysundregistrene Open REST APIs (`data.brreg.no`) and Kunngjøringer notice scrapers (`w2.brreg.no`).
- **Entity Details:** Extracts canonical entity metadata (legal form `AS`, `ENK`, `DA`, `ASA`, NACE sector code, registration date, registered address, employee headcount, statutory email, and homepage URL).
- **Zero Fabricated Financials:** Extracts Revenue, Operating Profit, Net Annual Profit, Assets, Debt, and Equity. Filings are sorted newest-first; company accounts (`SELSKAP`) are preferred over group accounts (`KONSERN`). Absence is never converted to zero; explicit `not_available` or `not_applicable` states are emitted with legal rationale.
- **Roles & Workplaces:** Maps active management (`daglig leder`), board chair (`styreleder`), board members, and physical branch workplaces (`underenheter`).
- **Statutory Notices:** Scrapes and categorizes official bankruptcy, liquidation, capital change, and board change announcements.

### 2.2 Multi-Source Web Discovery (`discovery.py`, `web_discovery.py`)
- Discovers website candidates through a priority waterfall:
  1. **Wikidata SPARQL:** Batched lookup of property `P2333` (Norwegian orgnr) -> `P856` (official website).
  2. **Statutory Email Domain:** Derives corporate domains from registered `epostadresse` (excluding free-mail providers).
  3. **Multi-Provider Web Search:** Unified engine supporting Serper, Brave Search, or Google Custom Search (auto-detected or configured via `WEB_SEARCH_PROVIDER`).
  4. **Google Places Text Search:** Queries business listings and verifies address matches.
  5. **Algorithmic Domain Guessing:** Resolves parallel DNS probes for `.no` and `.com` permutations of legal names and registered trade aliases.
  6. **Claude Haiku Web Search:** Targeted web search tool calls for high-priority missing sites.

### 2.3 Identity & Conflict Gates (`identity.py`, `site_proof.py`, `publication_gates.py`)
- **Multi-Number Profile (`CompanyNumberProfile`):** Consolidates the primary orgnr, all registered sub-unit orgnrs, group orgnrs, postal codes, and municipality.
- **Dual-Action Conflict Gate:**
  - *Positive Proof:* Accepts candidates that display the primary orgnr, any registered sub-unit orgnr, or registered street address + postcode beside the full legal name.
  - *Conflict Rejection:* If candidate page text contains 9-digit numbers that match *none* of the company's registered numbers, it is immediately quarantined with `conflict: True`.
- **Shared Domain Quarantine (`quarantine_shared_domains`):** At batch completion, any domain claimed by multiple companies in the batch is revoked from all companies except the one whose exact orgnr appears on that site.

### 2.4 Verified Web Crawling & Content Extraction (`website.py`, `site_feeds.py`, `job_postings_ld.py`)
- Bounded, polite crawling across verified homepages and statutory subpages (`/kontakt`, `/om-oss`, `/karriere`, `/nyheter`, `/personvern`) with strict `robots.txt` compliance and timeout bounds.
- **Social Profiles:** Extracts social handles (LinkedIn, Facebook, Instagram, YouTube, X). Handles must start or end with the distinctive legal name; third-party CMS/site-builder handles are dropped.
- **Career & Job Discovery:** Extracts schema.org `JobPosting` JSON-LD data and identifies outbound ATS platforms (Finn jobb, Webcruiter, Teamtailor, Recman, Jobbnorge).
- **Company News:** Mines dated articles via WordPress REST API, RSS/Atom feeds, JSON-LD news articles, and structured blog paths.

### 2.5 NAV Arbeidsplassen Vacancy Connector (`nav_jobs.py`)
- Ingests active vacancies from the official NAV feed (`https://pam-stilling-feed.nav.no/api/v1/feed`) using NAV's open public token.
- Background worker builds an in-memory index of active ads modified in the last 45 days.
- Matches job postings **strictly by `employer.orgnr`** matching the company or its registered sub-units.

### 2.6 Executive Synthesis & Trajectory Engine (`synthesis.py`)
- Computes multi-year revenue growth trajectories across filed statutory accounts, calculating CAGR, YoY revenue delta, and plain-language trend sentences ("Revenue went up/down by...").
- Generates human-scannable executive summaries opening with decision-relevant facts (bankruptcy notice, open vacancies, revenue trajectory, or balance sheet), leadership, and identified unknowns.

### 2.7 AI Enrichment & Grounded Fact Sheets (`ai_enrich.py`)
- When enabled via `ANTHROPIC_API_KEY`, Claude Sonnet 5.5 receives a numbered fact sheet of pre-verified evidence.
- Every generated statement must cite a valid fact ID (`[fact-X]`). Sentences lacking valid citations or stating numbers absent from cited facts are purged by the deterministic citation verifier.

### 2.8 Evidence Lineage & Cryptographic Integrity (`evidence.py`)
- Every published claim maps to an explicit evidence ID.
- Evidence records contain canonical source URL, data classification (`official_registry`, `company_owned`, `licensed_feed`), ISO-8601 UTC timestamp, verbatim text snippet (`claim_span`), and the SHA-256 hash of the retrieved source payload.

### 2.9 Contract Assembly & Operational Auditing (`contract.py`, `batch.py`, `run_agent.py`)
- Enforces strict compliance with `OUTPUT_CONTRACT.md`, emitting terminal JSONL envelopes with explicit availability states (`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`).
- Emits clean external observation records (`out/observations.jsonl`).
- Validates batch integrity via `audit_envelopes`, verifying claim/evidence reference integrity and non-fabrication guarantees.

### 2.10 Interactive Showcase Browser (`ui/build_ui.py`, `showcase.py`)
- Generates a standalone, dependency-free HTML dashboard (`out/showcase.html` / `docs/index.html`) using the Nordic Corporate Intelligence design system.
- Supports client-side instant search, multi-year financial tables, status filtering, and one-click SHA-256 evidence inspection.

---

## 3. Technology Stack & Design Decisions

| Subsystem | Technology | Rationale |
| :--- | :--- | :--- |
| **Runtime** | Python >= 3.12 (`uv`) | High-performance execution, modern typing, locked hermetic dependency management. |
| **Data Structures** | Standard Dicts & Dataclasses | Zero runtime schema overhead, transparent serialization, fast batch operations. |
| **Validation** | Pydantic v2 | Strict JSON schema compliance and type enforcement where required. |
| **HTTP Transport** | `urllib` / `http.client` | Lightweight, zero-bloat standard library networking with socket pooling and exponential back-off. |
| **HTML Parsing** | BeautifulSoup4, lxml, Trafilatura | Fast, fault-tolerant DOM parsing, microdata extraction, and text content extraction. |
| **Concurrency** | `ThreadPoolExecutor` | Efficient I/O-bound scaling across 8+ worker threads within the 45-minute runtime budget. |
| **Verification Suite** | `unittest` & `pytest` | 642 passing tests across 5 hermetic testing tiers, running fully offline in under 15 seconds. |

---

## 4. Boundary & Constraint Enforcement

1. **Strict Cost Ceilings:** Thread-safe `CostGovernor` enforces a global hard limit ($9.50 USD), and `BudgetSlices` enforce per-provider caps. Zero-cost baseline ($0.00) operates when keys are omitted.
2. **Execution Clocks:** Soft deadline (34m) halts optional web crawling; hard deadline (41m) halts AI analysis, guaranteeing terminal outputs are always written before timeouts.
3. **Hermetic Offline Testing:** `make test` runs with proxy blackholes (`HTTP_PROXY=http://127.0.0.1:9`), preventing accidental network leakage during verification.
4. **Resilience & Resumption:** Intermediate state checkpoints every $N$ companies (`--checkpoint-every 50`) enable seamless resumption via `--resume`.
