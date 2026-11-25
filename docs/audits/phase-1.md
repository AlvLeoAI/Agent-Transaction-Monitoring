MERGE AFTER FIXES
Audited `main` at `347b267` (covering Phase 1 `0d92860..ecc33c7` plus subsequent CI/dependency commits through `347b267`). Local offline validation commands all pass (`uv run pytest -q`: 158 passed, 1 skipped; `uv run mypy`: 0 issues across 23 files; `uv run ruff check .`: clean; `uv run ruff format --check .`: clean; `uv lock --check`: clean; `gitleaks git --redact --verbose .`: 14 commits, 0 leaks).
No critical or high findings: alert decisions remain strictly application-owned, model output gating is sound, untrusted transaction data is escaped inside XML tags and kept out of logs/paths, and CI supply-chain pins and cooldowns match policy. The findings below are medium/low gaps in ID domain separation, guardrail and claim test coverage, defensive bounds, and minor doc staleness.
Findings

* ID: A1
* Severity: medium
* Location: domain.py (used by engine.py and assessment.py)
* Evidence: `derived_id` returns `f"{prefix}:{key}"` when its length is `<= 128`, and otherwise returns `f"{prefix}:sha256-{hashlib.sha256(key.encode('utf-8')).hexdigest()}"`. Because `"sha256-<64-hex>"` (71 characters) is itself a valid `Identifier` (`^[A-Za-z0-9_.:-]+$`) and `len(f"{prefix}:sha256-<64-hex>") <= 99 <= 128` for every rule prefix and `assessment:v1`, a short transaction ID equal to `"sha256-" + sha256(long_tx_id)` takes the readable branch and produces the exact same `alert_id` and `assessment_id` as `long_tx_id`.
* Why it matters: Two distinct valid transactions collide to identical deterministic alert and assessment IDs. Any downstream storage or deduplication keyed on `alert_id` or `assessment_id` would overwrite or conflate records, and an agent that knows a long `transaction_id` can deliberately trigger a collision.
* Suggested fix: Make the readable and hashed namespaces disjoint—for example, take the hashed branch whenever `len(readable) > IDENTIFIER_MAX_LENGTH` or `key.startswith("sha256-")` (or tag both forms explicitly)—and add a regression test proving that `key = "sha256-" + sha256(long_key)` never collides with `long_key`.
* ID: A2
* Severity: medium
* Location: test_decision_isolation.py and test_decision_isolation.py (cited by architecture.md)
* Evidence:
   1. `_imports()` records `node.module` only when `isinstance(node, ast.ImportFrom) and node.module`, without resolving `node.level > 0` or inspecting imported alias names when `node.module is None`. A relative import such as `from .assessment import ModelAssessment` adds `"assessment"` (not `"agent_transaction_monitoring.assessment"`), and `from . import assessment` is skipped entirely—both pass `test_decision_side_never_imports_the_model_side`.
   2. `_callables()` checks `if inspect.isfunction(meth):` on `vars(obj).items()`, which returns `False` for `@classmethod` and `@staticmethod` descriptors (for example, `TransactionAssessor.request_parameters` at assessment.py is currently skipped).
   3. `test_every_module_is_classified` and `_source` only inspect top-level entries via `pkgutil.iter_modules` and `<pkg>/__init__.py`, so submodules added inside a subpackage (e.g. under `prompts/`) would not be scanned.
* Why it matters: Although production code currently respects the isolation boundary, the structural tests documented as proving the core principle can be silently bypassed by ordinary Python idioms (relative imports, `@classmethod`/`@staticmethod` factories such as `AlertDecision.from_assessment`, or subpackage modules).
* Suggested fix: Resolve relative `ast.ImportFrom` nodes against the package name (including `from . import <name>`), unwrap `classmethod` and `staticmethod` descriptors (`__func__`) in `_callables()`, and walk subpackages recursively with `pkgutil.walk_packages`.
* ID: A3
* Severity: medium
* Location: threat-model.md and assessment.py / assessment.py
* Evidence: Threat T3 marks as implemented: `"Assessment logging records status, stop reason, request id and error counts, never memo text or raw output (implemented)."` While `assessment.py` currently adheres to this, no test in `tests/` captures or inspects logs (`caplog` / `logging` is unused across the entire test suite).
* Why it matters: A future logging edit that accidentally includes `raw`, `tx.memo`, `validation_errors`, or an SDK exception message/body containing echoed prompt content would violate T3 without breaking any test.
* Suggested fix: Add tests using pytest's `caplog` fixture across `api_error`, `schema_invalid`, `refused`, `truncated`, and `unexpected_stop_reason` outcomes with a distinctive memo and raw payload, asserting that status/stop reason/request id/error count are logged and that neither memo text nor raw output appears in any log record.
* ID: A4
* Severity: low
* Location: architecture.md, assessment.py, and test_assessment.py
* Evidence: `docs/architecture.md` states: `"The record's validator enforces the invariants: an assessment exists if and only if the status is valid, the hash matches the raw output, and an api_error has no usage and no cost."` In `tests/test_assessment.py`, validator rejection is tested for `assessment` presence and `raw_output_sha256` mismatch, but no test verifies that `ModelAssessment` rejects an `api_error` with non-`None` `usage` or non-zero `cost_micro_usd`, mismatched `raw_output`/`raw_output_sha256` nullability, or missing/unexpected `error_type`.
* Why it matters: Removing the `api_error` usage/cost invariant check from `ModelAssessment._consistent` would leave the entire test suite passing despite contradicting the architecture doc.
* Suggested fix: Add negative `ModelAssessment.model_validate` tests for each remaining branch of `_consistent`: `api_error` with `usage` set, `api_error` with `cost_micro_usd > 0`, `api_error` without `error_type`, non-`api_error` with `error_type`, and `raw_output` without `raw_output_sha256` (and vice versa).
* ID: A5
* Severity: low
* Location: architecture.md, architecture.md, rules.py, test_rules.py, and test_engine.py
* Evidence:
   1. `window_transactions` documents the half-open window `(initiated_at - period_seconds, initiated_at]` and oldest-first ordering. Every window test in `tests/test_rules.py` supplies strictly earlier (`seconds_before > 0`), already-sorted history; changing `<= tx.initiated_at` to `< tx.initiated_at` or removing `sorted(...)` fails no test.
   2. `docs/architecture.md` states `"Every current rule is high and escalates; the medium and low severities map to needs_review"`. `test_spending_beyond_authorisation_is_high_severity` checks only R3/R4/R5 (R1 `NO_MANDATE` severity/outcome is never asserted in any test), and `test_rule_engine_never_dismisses` checks only that `DISMISS` is absent from `SEVERITY_OUTCOME.values()`, not that `MEDIUM` and `LOW` map to `NEEDS_REVIEW`.
* Why it matters: Same-timestamp burst payments from an autonomous agent or newest-first history feeds could silently regress without a failing test, as could an accidental downgrade of `NO_MANDATE` or remapping of `LOW`/`MEDIUM` severities.
* Suggested fix: In `tests/test_rules.py`, test a distinct prior transaction with `other.initiated_at == tx.initiated_at` and pass unsorted/reversed history to verify oldest-first ordering. In `tests/test_engine.py`, assert every `RuleId` maps to `AlertSeverity.HIGH` and assert the exact `SEVERITY_OUTCOME` mapping (`HIGH -> ESCALATE`, `MEDIUM -> NEEDS_REVIEW`, `LOW -> NEEDS_REVIEW`).
* ID: A6
* Severity: low
* Location: domain.py and rules.py
* Evidence: `Mandate.period_seconds` accepts any `StrictInt > 0` with no upper bound, and `window_transactions` computes `start = tx.initiated_at - timedelta(seconds=mandate.period_seconds)`. Composing a valid `Mandate` with a large `period_seconds` (e.g. `10**11` against a 2026 transaction, or `>= 86_400_000_000_000`)—or a normal `period_seconds` with a valid year-1 `initiated_at`—raises `OverflowError` inside `rules.evaluate`.
* Why it matters: Individually schema-valid `Mandate` and `Transaction` objects can crash the pure rule engine at evaluation time instead of producing deterministic findings.
* Suggested fix: Put a sensible upper bound on `Mandate.period_seconds` (so `timedelta(seconds=mandate.period_seconds)` cannot overflow) and check window inclusion via `(tx.initiated_at - other.initiated_at) < timedelta(seconds=mandate.period_seconds)` when `other.initiated_at <= tx.initiated_at` (which avoids subtracting past `datetime.min`).
* ID: A7
* Severity: low
* Location: architecture.md and README.md
* Evidence: `docs/architecture.md` line 73 still titles the section `## Model assessment (assessment.py, llm_client.py, prompts.py)` after commit `4578f26` moved `prompts.py` to the `prompts/` package (`prompts/__init__.py`). Also, `README.md` lines 50–60 state `"CI runs the same checks on every push and pull request"` directly after a quick-start block that omits `uv lock --check` and the wheel prompt-packaging check (both of which are accurately listed in CLAUDE.md).
* Why it matters: Stale module paths and slightly incomplete check lists cause documentation drift against the actual package layout and CI workflow.
* Suggested fix: Update the heading in `docs/architecture.md` to `prompts/` and either add `uv lock --check` and the wheel prompt check to `README.md` or note that `CLAUDE.md` lists the full CI command set.

Claims verified

* Alert decisions are application-owned; `DecisionSource` has no `model` member; `rationale` cannot be blank: confirmed in domain.py, engine.py, pipeline.py, proved by test_domain.py and behaviourally across all model outcomes (including prompt-injection memo) in test_decision_isolation.py.
* No-alert transactions never invoke the model: confirmed in pipeline.py, proved by test_pipeline.py.
* Six pure deterministic rules (R1–R6), stable `RuleId`s, R1 skips R2–R6, inclusive per-tx and period limits, strict `<` per-tx threshold and `>= 3` count for R6, half-open `[valid_from, valid_until)` validity, lower window boundary exclusion, deduplication of self/duplicates/other scopes: confirmed in rules.py, proved by test_rules.py and test_domain.py.
* Rule engine never dismisses; deterministic bounded alert and assessment IDs for 128-character transaction IDs: confirmed in engine.py and domain.py, proved by test_engine.py, test_domain.py, and test_assessment.py (subject to A1).
* Single injected non-streaming Anthropic client, no module-level client, explicit `thinking: {"type": "adaptive"}` and `output_config.effort`, no sampling params sent (`null` in audit record), no assistant prefill, wire body matches recorded `request_parameters`: confirmed in llm_client.py and assessment.py, proved end-to-end through the real SDK and `httpx2.MockTransport` in test_llm_client.py.
* Only `stop_reason == "end_turn"` with schema-valid output is `valid`; refusal (with `stop_details.category`), truncation (even if parseable), unexpected stop reasons, schema violations, thinking-only/leading-thinking blocks, and SDK `APIStatusError`/`APIError` are all stored as immutable `ModelAssessment` records with `assessment=None` and verbatim `raw_output` + SHA-256: confirmed in assessment.py and assessment.py, proved by test_assessment.py.
* Case-only enum normalization is recorded in `case_normalizations`, leaves `raw_output` and non-enum fields untouched, and rejects unknown values: confirmed in assessment.py, proved by test_assessment.py and test_assessment.py.
* Untrusted memo bounded at 1024 chars and XML-escaped inside `<transaction_memo>` alongside escaped transaction/finding tags: confirmed in domain.py and assessment.py, proved by test_domain.py and test_assessment.py.
* Integer-only money and cost arithmetic (atomic units, read-only currency allow-list, micro-USD ceiling division, 100k-token long-context rate card including cache tokens, unpriced models rejected at startup): confirmed in domain.py, settings.py, and assessment.py, proved by test_domain.py, test_settings.py, and test_assessment.py.
* Auditability fields (`requested_model`, served `model`, `request_id`, `prompt_version`, `prompt_sha256`, `request_parameters`, `usage`, `cost_micro_usd`, `priced_as`): confirmed in assessment.py and assessment.py, proved by test_assessment.py and test_llm_client.py.
* Prompt version hash pinning, package inclusion, and wheel verification: confirmed in prompts/init.py, manifest.json, check_wheel_prompts.py, and ci.yml, proved by test_prompts.py.
* Supply chain, CI hygiene, offline tests, and secret scanning: confirmed in ci.yml (SHA-pinned actions, checksum-verified gitleaks 8.30.1, `fetch-depth: 0`, `permissions: contents: read`, empty `ANTHROPIC_API_KEY`/`ATM_LIVE_TESTS`), dependabot.yml (7-day patch/minor and 14-day major cooldowns for both `github-actions` and `uv`), conftest.py (sockets blocked), test_live_smoke.py (skipped by default and in CI), and local `gitleaks git --redact --verbose .` + diff scan across full history (no real secrets).
* Docs honesty on unbuilt scope: confirmed in README.md, architecture.md, threat-model.md, and CLAUDE.md (evals, storage, API, Agent-Payments-Lab export ingestion, PII redaction, and `application_policy` consumption are clearly labelled as unbuilt/planned).
