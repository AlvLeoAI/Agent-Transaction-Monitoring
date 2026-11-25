MERGE AFTER FIXES
Audited `main...phase-1.1-audit-fixes` (`347b267..2bd1db7`). All offline validation commands pass (`uv run pytest -q`: 207 passed, 1 skipped; `uv run mypy`: 0 issues in 28 source files; `uv run ruff check .`: clean; `uv run ruff format --check .`: clean; `uv lock --check`: clean; `gitleaks git --redact --verbose .`: 15 commits, 0 leaks).
All seven Phase 1 findings (A1–A7) are CLOSED. However, auditing the isolation scanner and the new logging tests turned up one medium bypass in `isolation_scan.names()` (A8) that interacts directly with the relaxed `MODEL_SIDE` rule, plus two low findings (A9, A10).
Phase 1 findings status (A1–A7)
   * A1 — CLOSED. domain.py now routes any key starting with `HASHED_KEY_MARKER` (`"sha256-"`) through the hashed branch, making readable and hashed namespaces disjoint (and `assessment_id_for` in assessment.py shares `derived_id`). Proved by test_ids.py (`test_lookalike_key_never_collides_for_any_rule`, `test_lookalike_key_never_collides_for_assessments`, `test_keys_with_the_marker_always_take_the_hashed_form`, `test_readable_form_is_kept_for_ordinary_keys`).
   * A2 — CLOSED. isolation_scan.py walks nested subpackages via `pkgutil.walk_packages`, resolves relative imports (including `from . import x` and `from ..x import y`), and unwraps `staticmethod`, `classmethod`, and `property`. Proved against throwaway bypass modules in test_isolation_scan.py and on the real package in test_decision_isolation.py. (See new finding A8 below for an aliased-import gap in `isolation_scan.names()`.)
   * A3 — CLOSED. Non-valid assessments log one warning built from `LOG_FIELDS` via `_logged` (assessment.py). Proved across all six non-valid outcomes (and zero warnings on `valid`) with memo and raw-output canaries plus a non-vacuousness guard in test_assessment_logging.py. (See new findings A9 and A10 below.)
   * A4 — CLOSED. Every remaining branch of `ModelAssessment._consistent` (`raw_output`/`raw_output_sha256` nullability pairing, `error_type` iff `api_error`, and `api_error` having no usage and zero cost) is proved by `test_inconsistent_record_is_rejected` in test_assessment.py.
   * A5 — CLOSED. Same-instant inclusion (`<= tx.initiated_at`), tie-breaking by ID, and order-independence of `history` are proved by test_rules.py; all six rules mapping to `HIGH` and the exact `SEVERITY_OUTCOME` table (`HIGH -> ESCALATE`, `MEDIUM`/`LOW -> NEEDS_REVIEW`) are proved by test_engine.py.
   * A6 — CLOSED. `Mandate.period_seconds` is bounded by `MAX_PERIOD_SECONDS = 366 * 86_400` (domain.py) and `window_transactions` computes `timedelta(0) <= tx.initiated_at - other.initiated_at < period` (rules.py), eliminating `OverflowError` near `datetime.min`. Proved by test_domain.py (`test_mandate_period_is_capped_at_366_days`) and test_rules.py (`test_window_near_the_earliest_datetime_does_not_overflow`).
   * A7 — CLOSED. architecture.md now references `prompts/` and README.md lists every CI command. Proved by test_docs.py (`test_headings_name_only_modules_that_exist`, `test_ci_has_the_expected_commands`, `test_readme_check_list_covers_every_ci_command`).
Verification of the decision-isolation rule change
Checked test_decision_isolation.py and isolation_scan.py:
   1. Closed and explicit `MODEL_SIDE`: Yes. `MODEL_SIDE = ("assessment", "llm_client", "prompts", "settings")`, `DECISION_SIDE = ("domain", "rules", "engine")`, and `NEUTRAL = ("", "pipeline")` are explicit tuples (test_decision_isolation.py). Submodules under a subpackage (e.g. `prompts.x`) inherit their top-level side via `side(m)` (test_decision_isolation.py) and are walked by `pkgutil.walk_packages`.
   2. Unclassified new module fails the tests: Yes. `test_every_module_is_classified` asserts exact equality `sides == {*DECISION_SIDE, *MODEL_SIDE, *NEUTRAL}` (test_decision_isolation.py), so any new top-level module or subpackage (e.g. `policy.py` or `storage/`) fails immediately, and any function inside it that takes a `ModelAssessment` or returns an `AlertDecision` also fails `test_no_function_turns_an_assessment_into_a_decision`. (Minor caveat: `pkgutil.walk_packages` only descends into directories containing `__init__.py`, not PEP 420 namespace directories.)
   3. Does the relaxation open a path from an assessment to a decision?
      * In current production code: No. The only functions taking `ModelAssessment` are `assessment.log_fields` and `assessment._logged` (assessment.py), neither of which references decisions or imports `engine`/`pipeline`; `engine.decide` remains the sole function returning `AlertDecision`; and `pipeline` stays outside `MODEL_SIDE` (cannot accept a `ModelAssessment` parameter) and is verified behaviourally in `test_injection_memo_decisions_are_unaffected_by_any_assessment`.
      * In the structural guardrail itself: One real gap exists (finding A8 below). Because `MODEL_SIDE` is allowed to import `domain` and is now allowed to define functions taking `ModelAssessment`, the guardrail preventing `MODEL_SIDE` from constructing `AlertDecision`/`AlertOutcome`/`DecisionSource` relies on `isolation_scan.names()`, which currently drops the original symbol name on `as`-aliased imports.
New findings
   * ID: A8
   * Severity: medium
   * Location: isolation_scan.py and test_decision_isolation.py / test_decision_isolation.py
   * Evidence: In `isolation_scan.names()`:

```
python
```

elif isinstance(node, ast.alias):
    found.add(node.asname or node.name)
When an import uses an alias, `node.asname or node.name` records only the alias and drops `node.name`. Because `MODEL_FORBIDDEN` blocks only `engine` and `pipeline` (not `domain`, which `assessment.py` legitimately imports), a model-side module writing `from agent_transaction_monitoring.domain import AlertDecision as AD, AlertOutcome as AO, DecisionSource as DS` adds only `{"AD", "AO", "DS"}` to `names(m)` and passes `test_model_side_never_refers_to_decisions`. Combined with PR #6 allowing `MODEL_SIDE` functions to take `ModelAssessment`, a model-side function accepting `ModelAssessment` and returning `Any`/`object` (or a container) could construct `AD(...)` without tripping either `test_model_side_never_refers_to_decisions` or `test_no_function_turns_an_assessment_into_a_decision`.
   * Why it matters: A single `as`-alias import silently defeats the AST check that guarantees model-side modules—which are now explicitly permitted to accept `ModelAssessment`—never refer to decision types.
   * Suggested fix: In `isolation_scan.names()`, record `node.name` unconditionally for `ast.alias` (and also record `node.asname` when present). Add a throwaway-package regression test in `tests/test_isolation_scan.py` asserting that `from agent_transaction_monitoring.domain import AlertDecision as AD` is caught by `names()` / `model_side_violations`.
   * ID: A9
   * Severity: low
   * Location: threat-model.md and test_assessment_logging.py / test_assessment_logging.py
   * Evidence: `docs/threat-model.md` (T3) claims: `"Memo text, the prompt and raw output are never logged, and a test with canary strings checks every log record (implemented)."` However, `tests/test_assessment_logging.py` defines and checks only `MEMO_CANARY` and `RAW_CANARY`; it does not inject or check a prompt canary (nor check `classifier_prompt.text` against log records), and for the `api_error` cases the fake SDK exceptions do not contain a canary in their message/body.
   * Why it matters: An accidental log statement emitting `prompt.text` (or `str(exc)` from an API error) would violate the documented T3 guarantee while `test_assessment_logging.py` continues to pass.
   * Suggested fix: Include a prompt canary (e.g. a `Prompt` with a unique canary string, or asserting a distinctive slice of `classifier_prompt.text` never appears in any log record) and embed a canary in the fake `APIStatusError`/`APIConnectionError` payloads checked by `assert_no_canaries`.
   * ID: A10
   * Severity: low
   * Location: assessment.py and threat-model.md
   * Evidence: Phase 1's warning logs omitted all transaction fields (`status`, `stop_reason`, `request_id`, error count only). PR #6 added `transaction_id` and `assessment_id` (which embeds `transaction_id` verbatim whenever `len <= 128`) to `LOG_FIELDS` (`extra=fields`) and interpolated `transaction_id=%s` directly into the warning message.
   * Why it matters: While `transaction_id` is constrained to `Identifier` (`^[A-Za-z0-9_.:-]+$`, preventing log injection) and is disclosed in `docs/threat-model.md`, it is still an untrusted transaction field now reaching application logs—a broader log surface than Phase 1 and a potential identifier/correlation leak once real exports are ingested (before T3's planned redaction exists).
   * Suggested fix: Confirm whether raw `transaction_id` is intended in application logs or whether logging `request_id` and an opaque/hashed identifier is preferable; if `transaction_id` is an intentional correlation-ID exception, document that boundary explicitly in `docs/architecture.md` alongside `LOG_FIELDS`.
Claims verified
   * A1 — Disjoint readable/hashed derived ID namespaces: confirmed in domain.py, assessment.py, and architecture.md; proved by test_ids.py.
   * A2 — Isolation scanner resolves relative imports, unwraps `staticmethod`/`classmethod`/`property`, walks subpackages, and rejects unclassified modules: confirmed in isolation_scan.py and test_decision_isolation.py; proved by test_isolation_scan.py and test_decision_isolation.py (subject to A8).
   * A3 — Non-valid assessments emit one structured warning from `LOG_FIELDS`, valid assessments emit no warning, and memo/raw-output canaries never appear in any log record: confirmed in assessment.py; proved by test_assessment_logging.py (subject to A9, A10).
   * A4 — All branches of `ModelAssessment._consistent` reject inconsistent records: confirmed in assessment.py; proved by test_assessment.py.
   * A5 — Closed upper window boundary, tie-breaking, order-independent history, all rules `HIGH`, and exact `SEVERITY_OUTCOME` mapping: confirmed in rules.py and engine.py; proved by test_rules.py and test_engine.py.
   * A6 — `Mandate.period_seconds` capped at 366 days and overflow-free age-based window test: confirmed in domain.py and rules.py; proved by test_domain.py and test_rules.py.
   * A7 — Docs module headings and README quick-start check list stay synced with `src/` and CI: confirmed in architecture.md and README.md; proved by test_docs.py.
   * Dependabot `uv_build` / `uv-build` ignore rule matches CI `setup-uv` `0.11.7` pin and `pyproject.toml` `[build-system]` range: confirmed in dependabot.yml, ci.yml, pyproject.toml, and CLAUDE.md.
