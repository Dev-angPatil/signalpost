# Original User Request

## 2026-09-28T20:41:56Z

Extend the Signalpost Norwegian company intelligence agent to achieve an 80+ point competitive score on the Builderr benchmark by implementing bounded multi-page crawling, official public notice events, executive decision synthesis, and generating the full 1,000-profile submission artifact.

Working directory: /home/deu/Coding Repos/Active/signalpost
Integrity mode: demo

## Requirements

### R1. Bounded Multi-Page Web & Hiring Discovery
Crawl verified company websites across up to 4 key sub-paths (/om-oss, /kontakt, /karriere, /investor) to extract business descriptions, direct contact info, open job listings, and verified social profiles without violating identity gates.

### R2. Official Corporate Actions & Announcements Integration
Query official Norwegian public notices (Brønnøysund Kunngjøringer / change announcements) to record dated capital changes, board appointments, and legal filings.

### R3. Decision-Useful Executive Synthesis
Generate structured, grounded profile summaries highlighting business scope, financial health metrics (margins, equity ratios), and governance structure with explicit knowns vs. unknowns.

### R4. Automated Interactive HTML Showcase
Provide an inspectable, mobile-responsive HTML dashboard for entity search, filtering, and direct source provenance verification.

### R5. Scale Execution & Submission Deliverables
Generate at least 1,000 validated company profiles and envelopes conforming to OUTPUT_CONTRACT.md with zero silent drops, within the 45-minute and $0 API cost budget.

## Acceptance Criteria

### Evaluation & Qualification Bar
- [ ] Overall score >= 65/100 (targeting >= 80/100) when evaluated against the competition benchmark harness.
- [ ] Exactly 0 wrong-company publications or quarantined misattributions.
- [ ] Exactly 0 fabricated financial figures; missing statutory values are preserved as not_available or not_applicable.

### Output Integrity & Lineage
- [ ] Exactly 1 terminal JSONL envelope emitted per input organisation number with valid states (available, not_available, blocked, not_applicable, ambiguous, failed).
- [ ] 100% of claims are linked to evidence records with canonical URLs, UTC timestamps, claim spans, and SHA-256 hashes.
- [ ] Idempotent refresh verified: re-running identical snapshots produces zero false change events.

### Verification Suite
- [ ] All 104+ unit and integration tests in tests/test_poc.py pass.
- [ ] Generates out/smoke-report.json for a 100-company batch confirming zero silent drops and runtime < 5 minutes.
- [ ] Generates out/profiles-1000.jsonl containing at least 1,000 completed company profiles.

### Operational Limits
- [ ] Outbound requests <= 2,000 per 100-company run.
- [ ] Declared third-party API cost is $0.00 USD.
