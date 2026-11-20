# Data Pipeline, Contracts & Evidence Lineage

This document describes Signalpost's data contracts, output formats, cryptographic evidence lineage, and idempotent change detection.

---

## 1. The Output Contract (`OUTPUT_CONTRACT.md`)

Signalpost outputs strictly one JSON object per organisation number in its terminal envelope stream (`out/envelopes.jsonl`). Each envelope adheres strictly to the evaluation specification.

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
      "evidence_ids": ["ev-registry-0"]
    },
    {
      "field": "revenue",
      "value": 67956000000.0,
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-financials-0"]
    },
    {
      "field": "job_postings",
      "value": null,
      "availability": "not_available",
      "confidence": 0.95,
      "evidence_ids": ["ev-jobs-0"]
    }
  ],
  "evidence": [
    {
      "id": "ev-financials-0",
      "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/923609016",
      "source_class": "official_annual_accounts",
      "retrieved_at": "2026-09-28T20:26:13Z",
      "content_sha256": "98489e866c0fe849cf7467efe87041696c200763e6b40a5c4194e089346d1593",
      "claim_span": "Salgsinntekter: 67956000000 NOK for period 2024-01-01 to 2024-12-31"
    }
  ],
  "changes": [],
  "errors": [],
  "operations": {
    "requests": 14,
    "runtime_ms": 18450,
    "third_party_cost_usd": 0.0
  }
}
```

### Claim Availability States

Signalpost distinguishes between verified facts, negative findings, blocked requests, and ambiguous discoveries:

| State | Semantic Meaning | Example Use Case |
| :--- | :--- | :--- |
| `available` | Fact was successfully found, corroborated, and passed all identity gates. | Revenue found in Regnskapsregisteret; verified homepage found. |
| `not_available` | Source was queried successfully, but no data exists for this company. | Company has 0 registered employees or no open vacancies in NAV. |
| `blocked` | Upstream source refused the request (e.g. rate limit, robots.txt, 429). | NAV feed search endpoint throttled request. |
| `not_applicable` | Field is legally or structurally invalid for this entity form. | Filing annual accounts for an exempt Sole Proprietorship (`ENK`). |
| `ambiguous` | Discovered candidate failed identity proof or conflict gate. | Website candidate matched name tokens but lacked orgnr or address proof. |
| `failed` | Network failure or unhandled system error during fetch. | Connection timeout after retries. |

---

## 2. Cryptographic Evidence Lineage (`evidence.py`)

Every published claim links to one or more `evidence_ids`. Every evidence item contains:
1. `id`: Deterministic identifier (e.g., `ev-registry-0`, `ev-web-home-0`).
2. `source_url`: Canonical URL of the original resource.
3. `source_class`: Classification of data origin (`official_registry`, `company_owned`, `licensed_feed`).
4. `retrieved_at`: ISO-8601 UTC timestamp of retrieval.
5. `content_sha256`: Hexadecimal SHA-256 hash of the raw response payload.
6. `claim_span`: Verbatim textual snippet from the retrieved source substantiating the claim.

```python
def make_evidence_record(source_url: str, content: bytes, span: str, source_class: str) -> dict:
    return {
        "id": f"ev-{hashlib.md5(source_url.encode()).hexdigest()[:8]}",
        "source_url": source_url,
        "source_class": source_class,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "claim_span": span[:300]
    }
```

---

## 3. Storage Artifacts

Signalpost generates four synchronized output artifacts during every batch run:

```
out/
├── envelopes.jsonl     # Terminal evaluation envelopes (OUTPUT_CONTRACT.md)
├── profiles.jsonl      # Enriched company dossiers (all parsed submodules)
├── observations.jsonl  # Verified external observations (exact entity footprint)
├── report.json         # Operational audit, timings, latencies, and costs
└── showcase.html       # Standalone interactive HTML browser
```

### 3.1 External Observations Stream (`observations.jsonl`)
External observations isolate verified external footprints (company website activity, dated company news, active career postings, Google Places ratings, and YouTube metrics).
- Emitted only for entities whose website evidence is verified and publishable.
- Quarantined, unavailable, or cross-domain sites emit zero observations.
- Used directly by external footprint evaluation suites (`scripts/evaluate_external_footprint.py`).

### 3.2 Full Profiles Stream (`profiles.jsonl`)
Contains the full internal representation of the company, including:
- Multi-year financial history (up to 3 fiscal years)
- Complete executive management and board member lists
- Physical branch workplaces (`underenheter`)
- Raw Kunngjøringer public notices
- Multi-page crawl extractions, social links, and meta descriptions
- Revenue trajectory calculations and badges
- Claude Sonnet fact-sheet citations (when AI enrichment is enabled)

### 3.3 Operational Report (`report.json`)
The operational report is generated by `audit_envelopes` and summarizes:
- Batch size, runtime, and silent drop count (guaranteed 0).
- Availability ratio across all claims.
- Discovery statistics (websites verified, discovery breakdown by source).
- Operations metrics: request count, bytes transferred, p50 and p95 latency percentiles.
- Cost governance: declared third-party cost vs. the $10.00 challenge ceiling.
- Qualification audit: verification that zero fabricated financials occurred and all claim/evidence links are intact.

---

## 4. Idempotent Refresh & State Diffing (`refresh.py`)

Signalpost includes an idempotent change detection engine. When provided with a previous profile snapshot via `--prior-profiles`, it compares each field between runs:

```python
diff_profile(prior_profile, current_profile) -> list[ChangeEvent]
```

Each `ChangeEvent` records:
- `field`: The modified claim (e.g. `ceo`, `revenue`, `board_chair`).
- `old_value`: Prior recorded value.
- `new_value`: Newly detected value.
- `detected_at`: ISO-8601 UTC timestamp.

**Idempotency Guarantee:** Re-running Signalpost against the same snapshot produces exactly zero change events (`changes: []`).
