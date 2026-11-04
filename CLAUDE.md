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

Phase 0: scaffold only. Domain contracts, synthetic fixtures, tests, CI. No detection logic,
no LLM calls. The Anthropic SDK is added in Phase 1; do not add any agent framework.

## Commands

```bash
uv sync --locked          # install from the committed lockfile
uv run pytest -q          # tests (offline, no credentials)
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
