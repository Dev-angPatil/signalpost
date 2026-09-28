# EVALUATION_MATRIX.md — Signalpost Challenge Specification & Scoring Rubric

This document is the authoritative master reference for the **Builderr Signalpost Challenge**, compiling the technical specifications, scoring algorithms, evaluation criteria, and operational boundaries from official Builderr contracts and starter briefs.

---

## 1. What to Build

Build an autonomous AI agent capable of researching Norwegian corporate entities using their 9-digit organisation number (*organisasjonsnummer*).

### Core Pipeline Requirements:
1. **Official Identity Anchor:** Query official Norwegian registries (`data.brreg.no` Enhetsregisteret & Regnskapsregisteret) for legal identity, form (`AS`, `ASA`, `ENK`), NACE industry code, registered office, official addresses, and employee count.
2. **Audited Financials:** Extract statutory annual account filings (Revenue, Operating Result / EBIT, Net Profit, Assets, Debt, Equity) with reporting periods and currencies. Never guess or fabricate financial values.
3. **Leadership & Workplaces:** Extract active executive roles (CEO / *daglig leder*, Board Chair / *styreleder*, Board Members) and physical operating branches / subunits (*underenheter*).
4. **Verified Company Web Layer:** Crawl company websites and discover social footprints (LinkedIn, Facebook, etc.). Claims must pass a strict deterministic identity gate (9-digit orgnr or exact legal name in page content) before publication.
5. **Terminal Output Contract:** For every company input, emit exactly one terminal envelope JSON adhering to `OUTPUT_CONTRACT.md` with explicit availability states (`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`).
6. **Idempotent Refresh:** Detect and record material changes between crawl snapshots without producing false change events or duplicate records.

---

## 2. Evaluation Scoring Matrix (100 Points Total)

Official evaluation uses **Scoring Version 2** (effective August 26, 2026). Agents are tested on a shared batch of companies provided by Builderr after daily cutoffs.

| Category | Points | Core Measurement & Weighting |
| :--- | :---: | :--- |
| **1. Recall and Coverage** | **50** | **70% Company Recall + 30% Individual Fact Recall** across checked reference pool.<br>• *Identity & Accounts:* Registry legal name, form, NACE, audited financials (Revenue, EBIT, Assets, Debt).<br>• *Governance & Physical Presence:* Executive roles (CEO, Chair) and operating subunits.<br>• *External Footprint:* Verified company websites, contact channels, career/jobs postings, and social links. |
| **2. Precision & Evidence** | **30** | **Fact Accuracy, Lineage & Exact Identity:**<br>• Every claim must link to an evidence record with `source_url`, `retrieved_at`, and `content_sha256`.<br>• Verbatim `claim_span` substantiating the claim.<br>• Zero tolerance for wrong-company matches. |
| **3. Decision-Useful Synthesis** | **12** | **Utility of Profile Summary:**<br>• Plain-language explanation of what the company does.<br>• Financial health, leadership changes, and growth signals.<br>• Explicit delineation between *known/verified facts* and *unknown/unreported items*. |
| **4. UX & Interaction** | **8** | **Ease of Inspection & Usability:**<br>• Clean desktop & mobile inspection UX (e.g. interactive HTML dossier).<br>• Fast filtering by industry, municipality, and financial standing.<br>• One-click evidence provenance and export capability. |
| **Total Score** | **100** | **Qualification Threshold: $\ge 65.0$ / 100** |

### How Coverage is Calculated
For each information family (e.g., job postings, social handles, financials):
$$\text{Score} = 70\% \times (\text{\% of Companies Covered}) + 30\% \times (\text{\% of Facts Found})$$
*Example:* If the reference collection has 50 job postings across 20 companies, and your agent finds 30 postings across 15 companies:
$$\text{Coverage} = 70\% \times \frac{15}{20} + 30\% \times \frac{30}{50} = 52.5\% + 18.0\% = 70.5\%$$

---

## 3. Hard Qualification Gates & Instant Failure Traps

An entry qualifies only if it scores **$\ge 65/100$** overall on an official evaluation run. A run is disqualified if any of the following occur:

> [!CAUTION]
> ### The 5 Instant Disqualification Traps:
> 1. **Material Wrong-Company Publication:** Attributing a website, brand, or external facts to the wrong legal entity immediately disqualifies the official run.
> 2. **Fabricated Financial Values:** Generating or hallucinating statutory accounting numbers immediately disqualifies the entry. Missing values must be marked `not_available` or `not_applicable`—never convert missing to zero!
> 3. **Dropped or Missing Envelopes:** Every company in the official batch must produce exactly one terminal envelope. If 100 companies are provided, exactly 100 envelopes must be emitted.
> 4. **Exceeding Resource Limits:** Runs exceeding 45 minutes runtime, 2,000 outbound requests, or $10 external API cost are not scored.
> 5. **Broken Idempotency:** Re-running the agent against the same data must produce 0 false changes and 0 duplicate records.

---

## 4. Required Output Envelope Schema (`OUTPUT_CONTRACT.md`)

Each input organisation number must yield one JSON object:

```json
{
  "organisation_number": "123456789",
  "run": {
    "run_id": "signalpost-run-001",
    "started_at": "2026-09-28T20:00:00Z",
    "completed_at": "2026-09-28T20:00:08Z",
    "terminal_status": "completed"
  },
  "claims": [
    {
      "field": "legal_name",
      "value": "EXAMPLE ASA",
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-registry"]
    },
    {
      "field": "revenue",
      "value": 150000000.0,
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-financials"]
    }
  ],
  "evidence": [
    {
      "id": "ev-registry",
      "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/123456789",
      "source_class": "official_registry",
      "retrieved_at": "2026-09-28T20:00:02Z",
      "content_sha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
      "claim_span": "EXAMPLE ASA (123456789)"
    }
  ],
  "changes": [],
  "errors": [],
  "operations": {
    "requests": 14,
    "runtime_ms": 3210,
    "third_party_cost_usd": 0.0
  }
}
```

### Valid Availability States:
- `available`: Verified information found.
- `not_available`: Source checked, data does not exist / was not filed.
- `blocked`: Remote host blocked request (HTTP 403, robots.txt).
- `not_applicable`: Field is legally exempt or irrelevant for this legal form.
- `ambiguous`: Candidate site/profile found but failed identity verification.
- `failed`: Network or server error on this entity.

---

## 5. Daily Evaluation Limits & Budgets

Every daily official test run (100 companies) enforces these hard budgets:
- **Maximum Runtime:** 45 minutes (our agent executes in < 30 seconds).
- **Maximum Outbound Requests:** 2,000 requests (our agent averages ~15 requests per company = ~1,500 total).
- **Maximum External Cost:** $10.00 USD declared cost (our agent declares **$0.00 USD**).

---

## 6. Official Submission Checklist

To submit your agent to `submit@builderr.ai`:
- [x] **Repository URL:** Your public GitHub/Git repository link.
- [x] **Exact Commit Hash:** Pinned git commit hash (e.g. `b095fc124cfb084039b026c605166afad9c357cf`).
- [x] **One-Command Run:** 
  `uv run python run_agent.py --input <input.jsonl> --output <output.jsonl> --report <report.json>`
- [x] **100-Company Smoke Test Result:** Verified execution report (`out/test-report.json`).
- [ ] **1,000+ Company Dataset:** Generated profiles archive (`out/profiles-1000.jsonl`).
- [x] **Model & API Declarations:** Brønnøysundregistrene Enhetsregisteret & Regnskapsregisteret open APIs (`data.brreg.no`).
- [x] **Expected Run Cost:** $0.00 USD / 0 NOK.

---

## 7. Index of Documentation Files in This Repository

| File Path | Description |
| :--- | :--- |
| [`EVALUATION_MATRIX.md`](file:///home/deu/Coding%20Repos/Active/signalpost/EVALUATION_MATRIX.md) | **This master specification & scoring reference** |
| [`OUTPUT_CONTRACT.md`](file:///home/deu/Coding%20Repos/Active/signalpost/OUTPUT_CONTRACT.md) | Official JSON envelope schema contract |
| [`VISION.md`](file:///home/deu/Coding%20Repos/Active/signalpost/VISION.md) | Product vision, target UX, and anti-hallucination philosophy |
| [`ARCHITECTURE.md`](file:///home/deu/Coding%20Repos/Active/signalpost/ARCHITECTURE.md) | Technical system architecture and component diagram |
| [`README.md`](file:///home/deu/Coding%20Repos/Active/signalpost/README.md) | Quickstart guide and one-command execution instructions |
| [`docs/official/signalpost-starter-brief.md`](file:///home/deu/Coding%20Repos/Active/signalpost/docs/official/signalpost-starter-brief.md) | Original Builderr starter brief |
| [`docs/official/signalpost-evaluation-harness.md`](file:///home/deu/Coding%20Repos/Active/signalpost/docs/official/signalpost-evaluation-harness.md) | Official evaluation contract and scoring terms |
| [`docs/official/signalpost-sources-policy.md`](file:///home/deu/Coding%20Repos/Active/signalpost/docs/official/signalpost-sources-policy.md) | Permitted sources, publication rules, and scraping ethics |
| [`docs/official/signalpost-agent-playbook.md`](file:///home/deu/Coding%20Repos/Active/signalpost/docs/official/signalpost-agent-playbook.md) | Strategy playbook for building the research loop |
| [`docs/official/signalpost-learning-harness.md`](file:///home/deu/Coding%20Repos/Active/signalpost/docs/official/signalpost-learning-harness.md) | Testing and improving crawler performance |
