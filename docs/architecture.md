# Architecture

> Status: Phase 0 stub. Only the domain contracts in
> `src/agent_transaction_monitoring/domain.py` exist. No rules, no model calls, no storage.

## Core principle

**Alert decisions are application-owned.**

- Deterministic rules and thresholds decide whether an alert is raised.
- Model output is untrusted. It is validated against a schema, stored separately as
  `model_assessment`, and can never directly change an alert decision without passing
  application policy.

In the current contracts this shows up as:

- `AlertDecision.decided_by` is a `DecisionSource`, which has `rule_engine`,
  `application_policy` and `human_reviewer` members and deliberately no `model` member.
- `AlertDecision.rationale` is required and cannot be blank, so every outcome, including a
  dismissal, records why.
- `Transaction.memo` is treated as untrusted free text (see `threat-model.md`).

## Planned shape (not implemented)

```
transactions ──► deterministic rules ──► Alert ──► application policy ──► AlertDecision
                                            │              ▲
                                            ▼              │ (advisory only)
                                     model (Phase 1+) ──► model_assessment
```

- **Input:** sanitized audit exports from AlvLeoAI/Agent-Payments-Lab (not yet wired).
- **Rules:** mandate checks (per-transaction limit, period limit, counterparty allow-list,
  validity window) and pattern checks such as structuring.
- **Model:** Anthropic Messages API, added in Phase 1. Its output is parsed into a schema;
  output that fails validation is never used.
- **`ModelAssessment` (planned, deferred from Phase 0):** a separate model, linked to an
  `Alert` by `alert_id` rather than embedded in it. It will store:
  - the model id;
  - the prompt version;
  - a hash of the raw model output;
  - the schema validation status.

  A `ModelAssessment` can never produce an `AlertDecision`. Decisions come only from the
  sources in `DecisionSource`, and a model's view reaches a decision only through
  application policy.

## Money

Amounts are integers in atomic units. Supported currencies and their decimal scales live in
one table, `CURRENCY_DECIMALS` in `domain.py`; today that is `USDC` and `SIM-USDC`, both with
six decimals. Unknown currencies are rejected, and floats are rejected at the model boundary.
