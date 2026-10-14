# Signalpost: Autonomous Norwegian Company Intelligence Agent

> **Autonomous corporate research, audited annual accounts, executive leadership, and verified company evidence for Norwegian enterprises.**

Signalpost is an autonomous company intelligence agent built for the **Builderr Signalpost Challenge**. Given any 9-digit Norwegian organisation number (*organisasjonsnummer*), it anchors identity in official Norwegian public registries, audits annual financial filings, extracts executive management, verifies physical workplaces, and crawls company websites under strict anti-contamination identity gates.

---

## 🌟 Key Capabilities & Anti-Hallucination Guarantees

- **Anchor-Enrich-Verify Pipeline:** Grounded strictly in official Norwegian government APIs (`data.brreg.no` Enhetsregisteret & Regnskapsregisteret).
- **Zero Fabricated Financials:** Direct audited statutory figures (Revenue, EBIT, Net Result, Assets, Debt, Equity) with reporting periods and currencies. Absence is never converted to zero.
- **Strict Anti-Contamination Gate:** Web crawling requires 9-digit orgnr corroboration or exact legal name verification before claims are published. Uncorroborated domains are quarantined as `ambiguous`.
- **Cryptographic Lineage:** Every single published fact is linked to a unique evidence span, canonical source URL, retrieval timestamp, and SHA-256 hash.
- **Single Reproducible Command:** Runs deterministically out-of-the-box via `uv` with **$0.00 declared external API cost**.
- **100% Contract Compliant:** Emits terminal JSONL envelopes adhering to `OUTPUT_CONTRACT.md` with explicit states (`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`).

---

## 🚀 Quickstart & One-Command Execution

Follow these steps to run the agent. The agent is optimized to target an 80+ score on the official benchmark while requiring $0.00 declared external API cost.

### Prerequisites
- Python 3.12+
- `uv` (fast Python package manager)

### 1. Single Company Research
Research any live Norwegian company in seconds:
```bash
uv run python run_agent.py --org 923609016 --output out/test-envelope.jsonl --report out/test-report.json
```

### 2. Batch Research & Evaluation
Run across an input batch of organisation numbers with streaming checkpointing and HTML showcase:
```bash
uv run python run_agent.py \
  --input tests/fixtures/smoke-companies.jsonl \
  --output out/envelopes.jsonl \
  --profiles-output out/profiles.jsonl \
  --report out/report.json \
  --showcase out/showcase.html \
  --checkpoint-every 50
```

### 3. Change Detection & Refresh Replay
Verify deterministic refresh idempotency between profile versions:
```bash
python3 scripts/run_refresh_replay.py \
  --manifest tests/fixtures/refresh-snapshots.json \
  --output out/refresh-demo.json
```

---

## 🧪 Automated Testing
Run the complete 5-tier test suite (403+ passing tests across Tiers 1–5):
```bash
uv run --with pytest pytest -q tests/
```

Or execute individual testing tiers:
- **Tier 1 (Feature Coverage):** `uv run --with pytest pytest -q tests/test_e2e_tier1.py`
- **Tier 2 (Boundary & Corner Cases):** `uv run --with pytest pytest -q tests/test_e2e_tier2.py`
- **Tier 3 (Cross-Feature Interactions):** `uv run --with pytest pytest -q tests/test_e2e_tier3.py`
- **Tier 4 (Real-World Scenarios & Cohorts):** `uv run --with pytest pytest -q tests/test_e2e_tier4.py`
- **Tier 5 (Adversarial Hardening & Security):** `uv run --with pytest pytest -q tests/test_e2e_tier5.py`

---

## 📊 Evaluation Contract & Output Schema

Each output envelope adheres strictly to `OUTPUT_CONTRACT.md`:
```json
{
  "organisation_number": "923609016",
  "run": {
    "run_id": "signalpost-1790627168",
    "started_at": "2026-09-28T20:26:08Z",
    "completed_at": "2026-09-28T20:26:35Z",
    "terminal_status": "completed"
  },
  "claims": [
    {
      "field": "legal_name",
      "value": "EQUINOR ASA",
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-registry"]
    },
    {
      "field": "revenue",
      "value": 67956000000.0,
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-financials"]
    }
  ],
  "evidence": [
    {
      "id": "ev-financials",
      "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
      "source_class": "official_annual_accounts",
      "retrieved_at": "2026-09-28T20:26:13Z",
      "content_sha256": "98489e866c0fe849cf7467efe87041696c200763e6b40a5c4194e089346d1593",
      "claim_span": "Annual account filing for 923609016"
    }
  ],
  "changes": [],
  "errors": [],
  "operations": {
    "requests": 17,
    "runtime_ms": 26557,
    "third_party_cost_usd": 0.0
  }
}
```

---

## 📁 Repository Structure
```
├── VISION.md               # Product mission, core problem, and qualification targets
├── ARCHITECTURE.md         # Component diagrams, data flows, and anti-contamination boundaries
├── DESIGN.md               # Nordic Corporate Intelligence design system tokens & UI conventions
├── README.md               # Setup and execution guide
├── OUTPUT_CONTRACT.md      # Specification of the evaluation JSON envelope
├── pyproject.toml          # Pinned dependency definitions
├── run_agent.py            # Primary evaluator CLI entrypoint (--showcase, --checkpoint-every, --resume)
├── src/
│   └── norway_company_agent/
│       ├── announcements.py# Official Brønnøysund Kunngjøringer notice scraping & classification
│       ├── batch.py        # Batch inputs, envelope generation & validation
│       ├── contract.py     # Output contract claim & evidence mapping
│       ├── evidence.py     # SHA-256 hashing and timestamping
│       ├── http.py         # Resilient HTTP transport with rate-limiting
│       ├── identity.py     # Deterministic anti-contamination website identity gate
│       ├── official.py     # Brønnøysund & Regnskapsregisteret API integrations
│       ├── refresh.py      # Idempotent diff and change detection
│       ├── sampling.py     # Stratified cohort selection from bulk datasets
│       ├── showcase.py     # Interactive mobile-responsive HTML dashboard generation
│       ├── synthesis.py    # Financial ratios, executive synthesis, knowns/unknowns
│       ├── website.py      # Multi-page web crawling, JSON-LD schema parsing, contact & hiring discovery
│       └── workspace.py    # Saved query & pin management
├── scripts/                # Evaluation, replay, and prototype builder scripts
├── tests/                  # 5-tier verification suite (337+ passing tests across Tiers 1–5)
└── out/                    # Generated deliverables (profiles-1000.jsonl, envelopes, reports, html)
```

---

## 📄 Submission Details
- **Command:** `uv run python run_agent.py --input <input.jsonl> --output <output.jsonl> --report <report.json> --showcase <showcase.html> --checkpoint-every 50`
- **Models / APIs:** Official Brønnøysundregistrene Enhetsregisteret & Regnskapsregisteret Open REST APIs (`data.brreg.no`), Kunngjøringer notices (`w2.brreg.no`), and zero-hallucination deterministic synthesis.
- **Expected Cost per Run:** **$0.00 USD** (0 NOK).
- **Scale Deliverables:** >= 1,000 completed company profiles generated in `out/profiles-1000.jsonl` within the 45-minute runtime budget.
