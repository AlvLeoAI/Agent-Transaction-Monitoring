# Agent-Transaction-Monitoring

Compliance monitoring (AML / Know-Your-Agent) for payments initiated by autonomous AI agents.
It is the counterpart to [AlvLeoAI/Agent-Payments-Lab](https://github.com/AlvLeoAI/Agent-Payments-Lab)
and will later consume that project's sanitized audit exports.

Built incrementally alongside the Claude Academy course *Building with the Claude API*.

## Status: Phase 1 (rule engine and model assessment)

What exists today:

- Typed, immutable domain contracts: `AgentIdentity`, `Mandate`, `Transaction`, `Alert`,
  `AlertDecision` (`src/agent_transaction_monitoring/domain.py`).
- A deterministic rule engine with six rules: missing mandate, inactive mandate, payee not
  allowed, per-transaction limit, rolling period limit, and possible structuring. It raises
  alerts and makes the initial decision (`rules.py`, `engine.py`).
- An optional Claude assessment per alerted transaction, using structured outputs. It is
  stored as an immutable `ModelAssessment` audit record with the served model, request id,
  prompt version and hash, token usage, integer cost, raw output and a validation status
  (`assessment.py`). It is evidence for reviewers and never changes a decision.
- A versioned system prompt with a hash manifest, shipped inside the package
  (`src/agent_transaction_monitoring/prompts/classifier/`).
- Synthetic fixtures: normal, out-of-mandate, structuring-like, and a prompt-injection memo
  (`tests/fixtures/`).
- An explicit currency allow-list (`USDC`, `SIM-USDC`) with per-currency decimal scales.
- Offline tests with a fake Claude client, lint, format check, strict type checking, a
  full-history secret scan, and CI.

## Limits

This is a portfolio project, not a compliance product. Today it does **not**:

- measure how good the model's assessments are (evals are Phase 2);
- let a model assessment influence any decision (there is no policy step that reads one);
- screen against sanctions lists or use any real customer data;
- ingest Agent-Payments-Lab exports, persist data, or expose an API.

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
uv run mypy
gitleaks git --redact --verbose .   # needs gitleaks 8.30.1 installed
```

Tests run offline and need no credentials; network access is blocked inside the test run,
and the Claude client is replaced by a fake. CI runs the same checks on every push and pull
request.

### Calling Claude

The model defaults to `claude-haiku-5-5`. Configuration comes from `ATM_*` environment
variables (see [docs/architecture.md](docs/architecture.md#configuration)); the API key comes
from `ANTHROPIC_API_KEY`, which the Anthropic SDK reads itself. Neither the SDK nor the
settings read `.env`, so keep the key in a git-ignored `.env` (see `.env.example`) and let uv
load it for a single command, without exporting it into your shell:

```bash
ATM_LIVE_TESTS=1 uv run --env-file .env pytest -m live -q -s
```

The live smoke test is skipped by default and always skipped in CI. It assesses one benign
transaction and passes only if the assessment is `valid`; `-s` prints its status, stop
reason, token usage and cost. It checks that the round trip works, not how good the answer
is.

In code:

```python
from datetime import UTC, datetime

from agent_transaction_monitoring.assessment import TransactionAssessor
from agent_transaction_monitoring.llm_client import build_messages_client
from agent_transaction_monitoring.pipeline import monitor_transaction
from agent_transaction_monitoring.prompts import load_classifier_prompt
from agent_transaction_monitoring.settings import Settings

settings = Settings()
assessor = TransactionAssessor(
    build_messages_client(settings),  # create once at startup and reuse
    settings,
    load_classifier_prompt(settings.prompts_dir, settings.prompt_version),
    clock=lambda: datetime.now(UTC),
)
result = monitor_transaction(tx, mandates, history, now=datetime.now(UTC), assessor=assessor)
result.decisions  # from the rule engine only
result.assessment  # advisory ModelAssessment, or None if no alert was raised
```

## Docs

- [Architecture](docs/architecture.md)
- [Threat model](docs/threat-model.md)
