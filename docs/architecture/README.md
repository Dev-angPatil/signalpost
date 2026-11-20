# Signalpost System Architecture Documentation

Welcome to the architectural specifications for **Signalpost**, the autonomous Norwegian corporate intelligence system.

Signalpost is engineered to research, enrich, audit, and verify corporate intelligence for any 9-digit Norwegian organisation number (*organisasjonsnummer*) without hallucination, wrong-company attribution, or unverified claims.

---

## Architecture Navigation Index

| Document | Focus & Contents |
| :--- | :--- |
| **[Master System Architecture](../../ARCHITECTURE.md)** | Top-level system overview, component topology, high-level Mermaid flowcharts, and technical principles. |
| **[1. System Overview & Orchestration](SYSTEM_OVERVIEW.md)** | End-to-end pipeline lifecycle (Anchor-Enrich-Verify-Synthesize), worker concurrency, background job synchronization (NAV feed, Wikidata), checkpointing, and run clock deadlines. |
| **[2. Identity Resolution & Anti-Contamination Gates](IDENTITY_AND_GATING.md)** | The root-of-trust philosophy, `CompanyNumberProfile`, name token parsing, hard site proofing (`site_proof.py`), conflict detection, shared domain quarantine, and publication gates. |
| **[3. Data Pipeline, Contracts & Evidence Lineage](DATA_PIPELINE_AND_STORAGE.md)** | Evaluation envelope contract (`OUTPUT_CONTRACT.md`), claim availability states, cryptographic SHA-256 evidence model, external observations stream (`observations.jsonl`), and idempotent state diffing. |
| **[4. External Integrations & Connectors](INTEGRATIONS_AND_CONNECTORS.md)** | Detailed specifications for all data sources: Brønnøysundregistrene (Enhetsregisteret, Regnskapsregisteret, Kunngjøringer), NAV Arbeidsplassen vacancy feed, Wikidata SPARQL, Search providers (Serper, Brave, Google Custom Search), Google Places, YouTube, and Anthropic Claude. |
| **[5. Cost, Performance & Resource Governance](COST_AND_RESOURCE_GOVERNANCE.md)** | Thread-safe `CostGovernor`, per-task `BudgetSlices`, soft/hard deadlines (`web_discovery.CLOCK`), rate limiting, offline hermetic testing, and the zero-cost ($0.00) execution baseline. |

---

## Architectural Principles at a Glance

1. **Immutable Root of Trust:** The 9-digit Norwegian organisation number (*orgnr*) is authoritative. Registry records from Brønnøysundregistrene anchor entity identity.
2. **Zero Fabricated Financials:** Financial fields (Revenue, Operating Result, Net Result, Assets, Debt, Equity) are extracted directly from audited statutory filings. Absence is never coerced to zero; explicit `not_available` states are emitted with legal rationale.
3. **Strict Dual-Action Conflict Gate:** Candidate websites, social profiles, and news articles must match the primary orgnr or registered sub-units. If an external candidate displays a 9-digit number belonging to a different company, it is immediately quarantined with `conflict: True`.
4. **Cryptographic Lineage:** Every single published claim is tied to an explicit evidence block carrying the source URL, source class, ISO-8601 UTC timestamp, and SHA-256 hash of the retrieved payload.
5. **Cost-Governed & Hermetic:** Operates deterministically with $0.00 external cost out-of-the-box using open public endpoints, and enforces hard budget ceilings and circuit breakers when optional third-party providers are configured.
