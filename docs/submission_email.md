# Signalpost Final Submission Email Package

**To:** `submit@builderr.ai`  
**Subject:** Signalpost Submission — Autonomous Norwegian Company Intelligence Agent

---

Hi Builderr Evaluation Team,

Please find our official submission package for the **Signalpost** Norwegian company intelligence agent:

### 1. Repository & Frozen Commit
* **Public Repository URL:** `https://github.com/Dev-angPatil/signalpost`
* **Frozen Commit Hash:** `2b2d58ceb5617f83f2a1560cc6276eac359ddde6` *(or current frozen HEAD)*

### 2. Live & Static Dashboards
* **Live Hosted Evaluator Dashboard:** https://signalpost-showcase.onrender.com
* **Committed Static Showcase (offline, no execution needed):** `docs/index.html`
* **100-Company Smoke Test Report:** `out/smoke-report.json`

### 3. Agent & Contact Information
* **Agent Name:** Signalpost
* **Candidate / Submitter:** Devang Patil (`devangpatil125@gmail.com`)

### 4. One-Command Evaluator Invocation
```bash
uv sync --locked
uv run --locked --env-file .env python run_agent.py \
  --input <input.jsonl> \
  --output <output.jsonl> \
  --profiles-output <profiles.jsonl> \
  --observations-output <observations.jsonl> \
  --report <report.json> \
  --showcase <showcase.html> \
  --checkpoint-every 50
```

### 5. API Keys Configuration & Declared Sources

> **Zero Required Keys ($0.00 Default Cost):**  
> Signalpost runs completely out-of-the-box with **zero API keys** at $0.00 cost, utilizing official Brønnøysundregistrene open APIs (`data.brreg.no`), statutory Kunngjøringer notices (`w2.brreg.no`), and corporate email domain extraction.

If you choose to enable external web search discovery for missing websites, **you only need to provide ANY ONE of the following three search options** (via `.env` or runtime environment variables):

* **Option A — Google Search via Serper:**
  ```bash
  SERPER_API_KEY=your_serper_key_here
  ```
* **Option B — Brave Search API:**
  ```bash
  BRAVE_SEARCH_API_KEY=your_brave_key_here
  ```
* **Option C — Google Custom Search API:**
  ```bash
  GOOGLE_SEARCH_API_KEY=your_google_key_here
  GOOGLE_SEARCH_ENGINE_ID=your_custom_search_engine_cx
  ```

*(The agent auto-detects whichever key you provide. Alternatively, set `WEB_SEARCH_PROVIDER=auto|serper|brave|google` to explicitly specify your preference).*

**Optional Platform Footprint Keys (Enrichment only):**
* `GOOGLE_PLACES_API_KEY`: Physical location & rating footprints (~$0.017/query).
* `YOUTUBE_DATA_API_KEY`: Verified corporate video/subscriber metrics (free quota).

### 6. Models, Licences & Cost Governance
* **Synthesis & Data Model:** 100% deterministic, zero-hallucination synthesis. Every external claim is verified through strict exact-entity cryptographic proof (URL, UTC timestamp, content SHA-256, claim span).
* **Licensing:** Brønnøysundregistrene open data used under Norwegian Licence for Open Government Data (NLOD 2.0).
* **Budget & Circuit Breaker:** Built-in thread-safe `CostGovernor` actively halts paid requests at **$9.50 USD** with automatic fallback to zero-cost statutory discovery, guaranteeing total batch spend remains strictly under the $10.00 challenge ceiling.

Best regards,  
Devang Patil
