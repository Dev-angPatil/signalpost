# Signalpost submission package

This repository is submitted as a reproducible agent, not as a static company
profile archive. Builderr supplies the official organisation-number batch at run
time.

## Submission contents

Send `submit@builderr.ai`:

1. Public repository URL.
2. Exact commit hash to freeze.
3. The 100-company smoke-test report: `out/test-report.json`.
4. The one-command evaluator invocation below.
5. Agent name and contact information.
6. Models, APIs, licences, source-rights policy, and expected cost per batch.

The repository may contain larger local artifacts such as
`out/profiles-1000.jsonl`, but those are for local validation and do not replace
the official runtime batch.

## Evaluator command

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

The evaluator supplies any approved server-side secrets through environment
variables. No private API key is committed or required for the registry and
company-site path.

## Declared sources

- Brønnøysundregistrene Enhetsregisteret and Regnskapsregisteret open APIs.
- Brønnøysund Kunngjøringer public notices.
- Verified company-owned public websites, subject to robots and safe-URL checks.
- Optional Brave or Serper search APIs for candidate website discovery only;
  search results are transient and never serve as claim evidence.
- Optional official Google Places API for conservative place/rating summaries.
- Optional official YouTube Data API for verified channel-level metrics.

Every published external observation must pass the exact-entity gate and carry a
source URL, retrieval timestamp, SHA-256 hash, evidence span, identity proof,
approved acquisition mode, and approved rights status. Unsupported or ambiguous
observations are abstained from rather than published.

## Cost and limits

- Registry/company-site default path: `$0.00` declared third-party cost.
- Optional paid providers activate only when their per-request estimate is set.
- The report records requests, latency, and declared cost.
- The report audits the 2,000-request, `$10.00`, and 45-minute limits.
- Exactly one terminal envelope is emitted for every input organisation number.

## Security

- Never put API keys in this repository, command arguments, URLs, reports, or
  generated evidence.
- Use `.env.example` as the variable-name template only.
- If a credential was ever exposed during development, revoke or rotate it
  before publishing the repository.
