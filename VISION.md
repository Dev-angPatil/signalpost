# VISION.md — Signalpost: Autonomous Norwegian Company Intelligence

Vision and product roadmap for the Signalpost autonomous corporate research agent.

## 1. Executive Summary & Core Mission
**Signalpost** is an autonomous, zero-hallucination company intelligence agent designed for the Norwegian corporate ecosystem. Given any 9-digit Norwegian organisation number (*organisasjonsnummer*), Signalpost deterministically verifies legal identity, aggregates authoritative corporate records, audits financial filings, tracks executive leadership and physical workplaces, and discovers verified web presence and public activity—all anchored by cryptographic content evidence and strict anti-contamination gates.

## 2. Core Problem Solved
1. **The Hallucination & Wrong-Company Pitfall:** 
   LLMs and loose web scrapers routinely conflate similarly named companies, confuse parent corporations with distinct operating subsidiaries, or fabricate financial metrics. In high-stakes business diligence (investing, hiring, B2B sales), a fabricated financial number or wrong-company attribution is catastrophic.
2. **Ephemerality of Web Evidence:** 
   Traditional company aggregators provide static facts without source lineage. Signalpost links every single claimed fact to an immutable evidence span, timestamp, content hash, and source classification.
3. **Stale Corporate Data:** 
   Corporate structures, financials, and leadership shift constantly. Signalpost implements an idempotent refresh mechanism that detects real material changes over time without creating duplicate or spurious records.

## 3. Target User Experience (UX)
- **Zero Configuration Run:** A single, transparent CLI invocation (`python run_agent.py --input batch.jsonl --output envelopes.jsonl --report report.json`) that executes deterministically within declared runtime, rate, and cost limits ($0 external API cost).
- **Scannable & Verifiable Dossier:** Every profile produces an authoritative terminal JSON envelope containing:
  - Exact legal identity and public trade name.
  - Audited annual financial statements (Revenue, EBIT, Net Result, Total Debt, Assets).
  - Key roles (CEO, Board Chairperson, Board Members).
  - Physical subunits and workplace locations.
  - Verified website, contact channels, and social footprints.
  - Claim-to-evidence references with explicit availability states (`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`).
- **Interactive Decision Workspace:** Clean, minimalist UI/HTML viewer for desktop and mobile enabling fast entity search, cross-company comparison, and source inspection.

## 4. Anti-Bloat & Core-First Philosophy
- **Anti-Clutter:** No extraneous abstractions or speculative third-party paid dependencies.
- **Official Identity Anchor First:** Official Norwegian registries (Brønnøysundregistrene Enhetsregisteret & Regnskapsregisteret) serve as the ground truth anchor. Web discovery is permitted only when strictly corroborated by the entity's 9-digit registration number or exact legal name.
- **Honest Absence:** Missing values are explicitly recorded as `not_available` or `not_applicable`; absence is never converted to zero.

## 5. Success Criteria & Qualification Metrics
- **Qualification Score:** >= 65/100 overall score on official daily test batches.
- **Coverage & Recall:** > 21/35 coverage across core field families (Official Identity, Annual Financials, Roles, Locations, Verified Web/Social).
- **Precision & Attribution:** 100% exact entity matching; 0 material wrong-company publications; 0 fabricated financial figures.
- **Operational Boundaries:**
  - 100% terminal envelope completion (0 dropped or unhandled inputs).
  - Execution runtime: <= 45 minutes for 100-company batches.
  - Request budget: <= 2,000 outbound network requests.
  - Third-party API cost: $0.00 declared cost (utilizing official free public registry APIs and open web crawling).
