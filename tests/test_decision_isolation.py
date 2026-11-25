"""Proof that a ModelAssessment cannot create or modify an AlertDecision.

Three independent checks:

1. Structure: the decision side (domain, rules, engine) never imports the model side, and
   the model side (assessment, llm_client, prompts, settings) never refers to decisions.
2. Types: no function in the package takes a ModelAssessment and returns an AlertDecision,
   and the assessment record has no decision-typed field.
3. Behaviour: for every validation status and every risk level the model could claim,
   including the prompt-injection memo, the pipeline's decisions are identical to the
   decisions made with no assessor at all.
"""

import json
from typing import Any

import pytest

from agent_transaction_monitoring.assessment import ModelAssessment, ValidationStatus
from agent_transaction_monitoring.domain import AlertDecision, Mandate, Transaction
from agent_transaction_monitoring.pipeline import monitor_transaction
from agent_transaction_monitoring.prompts import Prompt
from agent_transaction_monitoring.settings import Settings

from isolation_scan import (
    DECISION_NAMES,
    ModuleSource,
    callables,
    decision_references,
    forbidden_imports,
    imports,
    modules,
    relative_name,
    scan_signatures,
)
from support import (
    FIXED_NOW,
    VALID_OUTPUT,
    FakeMessagesClient,
    connection_error,
    make_assessor,
    make_message,
)

PACKAGE = "agent_transaction_monitoring"

DECISION_SIDE = ("domain", "rules", "engine")
MODEL_SIDE = ("assessment", "llm_client", "prompts", "settings")
# Modules that may see both sides. The root __init__ has no code.
NEUTRAL = ("", "pipeline")

MODEL_SIDE_MODULES = tuple(f"{PACKAGE}.{m}" for m in MODEL_SIDE)
DECISION_FORBIDDEN = (*MODEL_SIDE_MODULES, f"{PACKAGE}.pipeline", "anthropic")
MODEL_FORBIDDEN = (f"{PACKAGE}.engine", f"{PACKAGE}.pipeline")


def side(module: ModuleSource) -> str:
    """The top-level module a (sub)module belongs to, e.g. ``prompts`` for ``prompts.x``."""
    return relative_name(PACKAGE, module.name).split(".")[0]


def on_side(sides: tuple[str, ...]) -> list[ModuleSource]:
    return [m for m in modules(PACKAGE) if side(m) in sides]


def decision_side_violations(package_modules: list[ModuleSource]) -> dict[str, set[str]]:
    found = {m.name: forbidden_imports(m, DECISION_FORBIDDEN) for m in package_modules}
    return {k: v for k, v in found.items() if v}


def model_side_violations(package_modules: list[ModuleSource]) -> dict[str, set[str]]:
    found = {
        m.name: forbidden_imports(m, MODEL_FORBIDDEN) | decision_references(m)
        for m in package_modules
    }
    return {k: v for k, v in found.items() if v}


def test_every_module_is_classified() -> None:
    sides = {side(m) for m in modules(PACKAGE)}
    assert sides == {*DECISION_SIDE, *MODEL_SIDE, *NEUTRAL}


def test_scan_sees_subpackages_and_relative_imports() -> None:
    by_name = {m.name: m for m in modules(PACKAGE)}
    assert f"{PACKAGE}.prompts" in by_name  # a subpackage, not a plain module
    engine = by_name[f"{PACKAGE}.engine"]
    assert f"{PACKAGE}.domain.AlertDecision" in imports(engine)


def test_decision_side_never_imports_the_model_side() -> None:
    # settings holds no decision logic, but the decision side has no reason to read it either.
    assert decision_side_violations(on_side(DECISION_SIDE)) == {}


def test_model_side_never_refers_to_decisions() -> None:
    assert model_side_violations(on_side(MODEL_SIDE)) == {}


def test_no_function_turns_an_assessment_into_a_decision() -> None:
    scan = scan_signatures(PACKAGE)
    assert scan.converts == []
    # Only the engine makes decisions, and only the model side handles assessments (to
    # build, log and store them). Nothing on the decision side or in the pipeline does.
    assert scan.returns_decision == ["engine.decide"]
    assert scan.takes_assessment
    assert {name.split(".")[0] for name in scan.takes_assessment} <= set(MODEL_SIDE)


def test_static_and_class_methods_are_scanned() -> None:
    # TransactionAssessor.request_parameters is a staticmethod; the scan must see it.
    scanned = {name for name, _ in callables(PACKAGE)}
    assert "assessment.TransactionAssessor.request_parameters" in scanned


def test_assessment_record_has_no_decision_fields() -> None:
    for name, field in ModelAssessment.model_fields.items():
        assert not any(n in repr(field.annotation) for n in DECISION_NAMES), name


# --- Behaviour --------------------------------------------------------------------------

MODEL_RESPONSES: dict[str, Any] = {
    "valid low risk, memo obeyed": make_message(
        json.dumps({**VALID_OUTPUT, "risk_level": "low", "memo_influence_suspected": False})
    ),
    "valid high risk, memo flagged": make_message(json.dumps(VALID_OUTPUT)),
    "schema invalid, tries to decide": make_message(
        json.dumps({**VALID_OUTPUT, "decision": "dismiss", "decided_by": "model"})
    ),
    "refused": make_message(None, stop_reason="refusal"),
    "truncated": make_message('{"risk_level": "lo', stop_reason="max_tokens"),
    "unexpected stop": make_message(json.dumps(VALID_OUTPUT), stop_reason="pause_turn"),
    "api error": connection_error(),
}


@pytest.mark.parametrize("response", MODEL_RESPONSES.values(), ids=MODEL_RESPONSES.keys())
def test_injection_memo_decisions_are_unaffected_by_any_assessment(
    response: Any,
    injection_transaction: Transaction,
    mandate: Mandate,
    settings: Settings,
    classifier_prompt: Prompt,
) -> None:
    mandates = {mandate.mandate_id: mandate}
    baseline = monitor_transaction(injection_transaction, mandates, [], now=FIXED_NOW)

    client = FakeMessagesClient(response)
    assessor = make_assessor(client, settings, classifier_prompt)
    result = monitor_transaction(
        injection_transaction, mandates, [], now=FIXED_NOW, assessor=assessor
    )

    assert len(client.requests) == 1
    assert result.assessment is not None
    assert result.decisions == baseline.decisions
    assert result.alerts == baseline.alerts
    (decision,) = result.decisions
    assert isinstance(decision, AlertDecision)
    assert decision.decided_by == "rule_engine"
    assert decision.outcome == "escalate"


def test_flagged_memo_is_recorded_as_evidence_only(
    injection_transaction: Transaction,
    mandate: Mandate,
    settings: Settings,
    classifier_prompt: Prompt,
) -> None:
    client = FakeMessagesClient(MODEL_RESPONSES["valid high risk, memo flagged"])
    result = monitor_transaction(
        injection_transaction,
        {mandate.mandate_id: mandate},
        [],
        now=FIXED_NOW,
        assessor=make_assessor(client, settings, classifier_prompt),
    )
    assert result.assessment is not None
    assert result.assessment.validation_status is ValidationStatus.VALID
    assert result.assessment.assessment is not None
    assert result.assessment.assessment.memo_influence_suspected is True
    assert result.assessment.alert_ids == tuple(a.alert_id for a in result.alerts)
