# Agent-Transaction-Monitoring

Compliance monitoring (AML / Know-Your-Agent) for payments initiated by autonomous AI agents.
It is the counterpart to [AlvLeoAI/Agent-Payments-Lab](https://github.com/AlvLeoAI/Agent-Payments-Lab)
and will later consume that project's sanitized audit exports.

Built incrementally alongside the Claude Academy course *Building with the Claude API*.

## Status: Phase 0 (scaffold)

What exists today:

- Typed, immutable domain contracts: `AgentIdentity`, `Mandate`, `Transaction`, `Alert`,
  `AlertDecision` (`src/agent_transaction_monitoring/domain.py`).
- Synthetic fixtures: normal, out-of-mandate, and structuring-like transactions
  (`tests/fixtures/`).
- Tests, lint, format check, and CI.

## Limits

This is a portfolio project, not a compliance product. Today it does **not**:

- detect anything: there are no rules or alerting logic yet;
- call any LLM (the Anthropic SDK arrives in Phase 1);
- screen against sanctions lists or use any real customer data;
- persist data or expose an API.

## Core principle

Alert decisions are application-owned. Deterministic rules and thresholds decide whether an
alert is raised. Model output is untrusted: it is validated against a schema, stored
separately as `model_assessment`, and can never directly change an alert decision without
passing application policy. See [docs/architecture.md](docs/architecture.md).

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync --locked
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

Tests run offline and need no credentials; network access is blocked inside the test run.

## Docs

- [Architecture](docs/architecture.md)
- [Threat model](docs/threat-model.md)
