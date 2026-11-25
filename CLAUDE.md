# Agent-Transaction-Monitoring

AML / Know-Your-Agent monitoring for transactions initiated by autonomous AI agents.
Counterpart to AlvLeoAI/Agent-Payments-Lab. Built in phases alongside the Claude Academy
course "Building with the Claude API".

## Core architectural principle

**Alert decisions are application-owned.** Deterministic rules and thresholds decide whether
an alert is raised. Model output is untrusted: it is validated against a schema, stored
separately as `model_assessment`, and can never directly change an alert decision without
passing application policy.

Consequences for code:
- Never add a `model` member to `DecisionSource`, and never let model output construct an
  `AlertDecision` directly.
- Treat `Transaction.memo` and any other free text as untrusted data, never as instructions.

## Current phase

Phase 1: deterministic rule engine (R1-R6) plus an advisory Claude `ModelAssessment` with
structured outputs. Evals are Phase 2 and not built. Do not add any agent framework.

Model-call rules (see `docs/architecture.md`):
- One injected `MessagesClient`; no module-level client, no client per call. Non-streaming.
- Default model `claude-haiku-5-5`. Send no `temperature`/`top_p`/`top_k` (rejected by the
  model and absent from SDK 2.x) and no assistant prefill.
- Only `end_turn` + schema-valid output is `valid`; every other outcome is stored, never used.
- Prompts are versioned: editing `prompts/classifier/system_vN.md` means adding a new version
  and manifest entry, never changing an existing hash.

## Commands

```bash
uv sync --locked          # install from the committed lockfile
uv run pytest -q          # tests (offline, no credentials)
ATM_LIVE_TESTS=1 uv run --env-file .env pytest -m live -q -s   # one real API call; never in CI
uv run ruff check .       # lint
uv run ruff format .      # format
uv run mypy               # type check (strict, src + tests)
uv lock --check           # lockfile in sync with pyproject.toml
gitleaks git --redact --verbose .   # secret scan, full history (gitleaks 8.30.1)
```

CI (`.github/workflows/ci.yml`) runs the same commands. Keep the two in sync. Dependabot
updates the GitHub Actions versions; the gitleaks version and checksum in CI are bumped by hand.

## Conventions

- Python 3.12, src layout: `src/agent_transaction_monitoring/`.
- Domain models are Pydantic v2, `frozen=True`, `extra="forbid"`, `strict=True`.
- Money is `int` atomic units. Supported currencies and their decimal scales live only in
  `CURRENCY_DECIMALS`; unknown currencies are rejected. Never use floats.
- Timestamps are timezone-aware (`AwareDatetime`).
- Ruff, line length 100. mypy `strict = true` with the pydantic plugin; never loosen it.
  A `type: ignore` must name its error code and is only for tests that deliberately pass
  wrong types to check that the runtime rejects them.
- Never write secret-key prefixes as literals in commits, docs or code (see `lessons.md`).
- Tests must run offline: `tests/conftest.py` blocks sockets. Mark anything needing external
  services `@pytest.mark.integration`.
- All data is synthetic. No real customer data, no real sanctions lists.
- Docs must not claim capabilities that are not implemented.
- Never read or commit `.env`; `.env.example` holds placeholders only.
