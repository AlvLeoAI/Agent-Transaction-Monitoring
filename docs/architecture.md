# Architecture

> Status: Phase 1. A deterministic rule engine raises alerts and makes the initial decision.
> An optional Claude call adds an advisory `ModelAssessment`. There is no storage, no API
> and no evaluation of assessment quality yet (evals are Phase 2).

## Core principle

**Alert decisions are application-owned.**

- Deterministic rules and thresholds decide whether an alert is raised.
- Model output is untrusted. It is validated against a schema, stored separately as a
  `ModelAssessment`, and can never directly change an alert decision without passing
  application policy.

In code this shows up as:

- `AlertDecision.decided_by` is a `DecisionSource`, which has `rule_engine`,
  `application_policy` and `human_reviewer` members and deliberately no `model` member.
- `AlertDecision.rationale` is required and cannot be blank, so every outcome records why.
- No function takes a `ModelAssessment` and returns an `AlertDecision`, the decision modules
  never import the model modules, and the model modules never refer to decisions.
  `tests/test_decision_isolation.py` checks all three, and checks that the pipeline's
  decisions are identical whatever the model returns.

## Flow for one transaction

```
Transaction + mandates + prior transactions
        │
        ▼
 rules.evaluate ──► Finding[] ──► engine.raise_alerts ──► Alert[] ──► engine.decide ──► AlertDecision[]
                       │                                     │
                       │      (only if an alert was raised)  │
                       ▼                                     ▼
              assessment.TransactionAssessor.assess ──► ModelAssessment   (evidence, stored alongside)
```

`pipeline.monitor_transaction` runs the steps in that order. Decisions are made before the
assessor is called, from rule findings alone, and the assessment is attached to the result
next to them. A transaction that raises no alert never reaches the model.

## Rule engine (`rules.py`, `engine.py`)

Pure functions over domain models: no I/O, no clock (the caller passes `now`), no model
calls. Each rule returns findings with a stable `RuleId`.

| Rule | Id | Fires when | Severity | Initial outcome |
|------|----|-----------|----------|-----------------|
| R1 | `no_mandate` | no `mandate_id`, or the mandate is unknown, belongs to another agent, or is in another currency | high | escalate |
| R2 | `mandate_not_active` | `initiated_at` outside the half-open window `[valid_from, valid_until)` | high | escalate |
| R3 | `payee_not_allowed` | counterparty not on the allow-list; an empty list denies everyone | high | escalate |
| R4 | `per_tx_limit_exceeded` | amount above the per-transaction limit (equal is allowed) | high | escalate |
| R5 | `period_limit_exceeded` | rolling spend over `period_seconds`, this transaction included, above the period limit | high | escalate |
| R6 | `possible_structuring` | at least `STRUCTURING_MIN_TRANSACTIONS` (3) payments, each strictly under the per-transaction limit, total more than the period limit within one window | high | escalate |

- When R1 fires there is no mandate to measure against, so R2 to R6 are skipped.
- The rolling window is `(initiated_at - period_seconds, initiated_at]` over prior
  transactions of the same agent, mandate and currency. Duplicate ids and the transaction
  itself are not counted twice. The caller supplies the history.
- R5 and R6 can fire together: R5 says the limit was crossed, R6 says it was crossed by
  splitting. The structuring fixture triggers both on its sixth payment.
- Alert ids are deterministic (`alert:<rule_id>:<transaction_id>`), so re-evaluation is
  idempotent.
- R3, R4 and R5 are one category, spending beyond what the operator authorised, so they
  share high severity. Every current rule is high and escalates; the `medium` and `low`
  severities map to `needs_review` and are kept for future rules.
- The rule engine never dismisses. Dismissal is left to a human reviewer or a later
  application policy.

## Model assessment (`assessment.py`, `llm_client.py`, `prompts.py`)

### Client

`llm_client.MessagesClient` is a protocol. The application builds one
`AnthropicMessagesClient` at startup with `build_messages_client(settings)` and injects it
into `TransactionAssessor`; tests inject a fake. There is no module-level client.

Each request is a single, non-streaming `client.messages.create` call:

- `model` from settings, default `claude-haiku-5-5` (`ATM_MODEL`).
- `system`: the versioned prompt file, loaded at runtime.
- One user message with the transaction in XML tags. No assistant prefill.
- `output_config.format` with a hand-written JSON schema, and `output_config.effort`
  (default `low`, `ATM_EFFORT`).
- `thinking: {"type": "adaptive"}`, which is this model's default, stated explicitly.
- No `temperature`, `top_p` or `top_k`. Claude Haiku 5.5 rejects non-default sampling values,
  and SDK 2.x, which sends API version `2026-10-21`, no longer accepts them. The audit record
  stores them as `null`, meaning not sent.

If streaming is ever added, the audit record must be built from the final assembled message.

### Output schema

The model returns four required fields: `risk_level` (`low`, `medium`, `high`),
`typologies` (a list drawn from `structuring`, `velocity`, `overspend`, `misdirection`,
`layering`, `other`), `rationale` (a short justification) and `memo_influence_suspected`
(bool). No optional fields, no unions.

The schema sent to the API only fixes structure and enum values. Limits it cannot express
are checked by `ModelOutput` after parsing: rationale length (600 characters, not blank),
at most 6 typologies, no repeats.

Enum casing is not guaranteed, so before validation the assessment layer lower-cases an enum
value that differs from a known value only by case. Each change is recorded in
`case_normalizations` with the raw value, and `raw_output` keeps the text exactly as
received. Nothing else is normalized.

### Validation statuses

| Status | When | Parsed assessment |
|--------|------|-------------------|
| `valid` | `stop_reason == "end_turn"` and the output passes validation | present |
| `schema_invalid` | `end_turn`, but the text is not JSON or fails validation | absent |
| `refused` | `stop_reason == "refusal"`; `stop_details.category` is recorded | absent |
| `truncated` | `stop_reason == "max_tokens"`, even if the partial text happens to parse | absent |
| `unexpected_stop_reason` | any other stop reason | absent |
| `api_error` | the SDK raised an API, connection or timeout error after its own retries | absent |

Every outcome produces a stored `ModelAssessment`. Invalid output is kept with its status and
errors and is never used for anything.

### `ModelAssessment` record

An immutable model linked to the transaction by `transaction_id` and to its alerts by
`alert_ids`. It stores:

- `requested_model` and `model`, the latter taken from `response.model`;
- `request_id` from the response (or from the error, for HTTP errors);
- `prompt_version` and `prompt_sha256`;
- `request_parameters`, copied from the request object the adapter sent: `max_tokens`,
  `effort` (sent as `output_config.effort`), `thinking` (the full `thinking` field, today
  `{"type": "adaptive"}`), and the sampling parameters `temperature`, `top_p` and `top_k`
  (all `null`, not sent). A test compares these with the HTTP body;
- `stop_reason`, `refusal_category`, and token usage (`input_tokens`, `output_tokens`,
  `cache_creation_input_tokens`, `cache_read_input_tokens`);
- `raw_output` and `raw_output_sha256`;
- `validation_status`, `validation_errors`, `case_normalizations`;
- `assessment`, present only when the status is `valid`;
- `error_type` and `error_status_code` for `api_error`;
- `cost_micro_usd`, an integer computed from `MODEL_PRICING` in `settings.py`, rounded up
  to the next micro-USD, and `priced_as`. Prices come from Anthropic's pricing page; the
  source URL and the date checked sit next to the table. Claude Haiku 5.5 has a higher rate
  card for prompts over 100,000 tokens, where the prompt counts input, cache-write and
  cache-read tokens.

The record's validator enforces the invariants: an assessment exists if and only if the
status is `valid`, the hash matches the raw output, and an `api_error` has no usage and no
cost.

### Prompt versioning

The system prompt lives in `prompts/classifier/system_v1.md`. `prompts/classifier/manifest.json`
maps each version to its file and SHA-256. The loader refuses a file whose hash does not
match, and a test fails if a prompt changes without a new version entry.

## Configuration

`settings.Settings` reads `ATM_*` environment variables: `ATM_MODEL`, `ATM_MAX_TOKENS`,
`ATM_EFFORT`, `ATM_REQUEST_TIMEOUT_SECONDS`, `ATM_MAX_RETRIES`, `ATM_PROMPT_VERSION`,
`ATM_PROMPTS_DIR`. A model without an entry in `MODEL_PRICING` is rejected at startup. The
API key is not a setting: the Anthropic SDK reads `ANTHROPIC_API_KEY` from the environment.

## Money

Amounts are integers in atomic units. Supported currencies and their decimal scales live in
one table, `CURRENCY_DECIMALS` in `domain.py`; today that is `USDC` and `SIM-USDC`, both with
six decimals. Unknown currencies are rejected, and floats are rejected at the model boundary.
Model cost is also an integer, in micro-USD.

## Not yet built

- Ingesting sanitized audit exports from AlvLeoAI/Agent-Payments-Lab.
- Storage for alerts, decisions and assessments.
- An `application_policy` step that could weigh an assessment; today nothing consumes one.
- Evals of assessment quality (Phase 2).
