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

import ast
import importlib
import inspect
import json
import pkgutil
from pathlib import Path
from typing import Any, get_type_hints

import pytest

import agent_transaction_monitoring
from agent_transaction_monitoring.assessment import ModelAssessment, ValidationStatus
from agent_transaction_monitoring.domain import AlertDecision, Mandate, Transaction
from agent_transaction_monitoring.pipeline import monitor_transaction
from agent_transaction_monitoring.prompts import Prompt
from agent_transaction_monitoring.settings import Settings

from support import (
    FIXED_NOW,
    VALID_OUTPUT,
    FakeMessagesClient,
    connection_error,
    make_assessor,
    make_message,
)

PACKAGE = "agent_transaction_monitoring"
PACKAGE_DIR = Path(agent_transaction_monitoring.__file__).parent

DECISION_SIDE = ("domain", "rules", "engine")
MODEL_SIDE = ("assessment", "llm_client", "prompts", "settings")
DECISION_NAMES = {"AlertDecision", "AlertOutcome", "DecisionSource", "decide"}


def _tree(module: str) -> ast.Module:
    return ast.parse((PACKAGE_DIR / f"{module}.py").read_text())


def _imports(module: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _names(module: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name)
    return found


def test_every_module_is_classified() -> None:
    modules = {m.name for m in pkgutil.iter_modules([str(PACKAGE_DIR)])}
    assert modules == {*DECISION_SIDE, *MODEL_SIDE, "pipeline"}


@pytest.mark.parametrize("module", DECISION_SIDE)
def test_decision_side_never_imports_the_model_side(module: str) -> None:
    forbidden = {f"{PACKAGE}.{m}" for m in (*MODEL_SIDE, "pipeline")} | {"anthropic"}
    # settings holds no decision logic, but the decision side has no reason to read it either.
    assert _imports(module).isdisjoint(forbidden)


@pytest.mark.parametrize("module", MODEL_SIDE)
def test_model_side_never_refers_to_decisions(module: str) -> None:
    assert _imports(module).isdisjoint({f"{PACKAGE}.engine", f"{PACKAGE}.pipeline"})
    assert _names(module).isdisjoint(DECISION_NAMES)


def _callables() -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    for info in pkgutil.iter_modules([str(PACKAGE_DIR)]):
        module = importlib.import_module(f"{PACKAGE}.{info.name}")
        for name, obj in vars(module).items():
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            if inspect.isfunction(obj):
                found.append((f"{info.name}.{name}", obj))
            elif inspect.isclass(obj):
                for meth_name, meth in vars(obj).items():
                    if inspect.isfunction(meth):
                        found.append((f"{info.name}.{name}.{meth_name}", meth))
    return found


def test_no_function_turns_an_assessment_into_a_decision() -> None:
    takes_assessment: list[str] = []
    returns_decision: list[str] = []
    for qualname, func in _callables():
        hints = {k: repr(v) for k, v in get_type_hints(func).items()}
        returns = hints.pop("return", "")
        params = " ".join(hints.values())
        if "ModelAssessment" in params:
            takes_assessment.append(qualname)
        if "AlertDecision" in returns:
            returns_decision.append(qualname)
            assert "ModelAssessment" not in params, qualname
            assert "ModelOutput" not in params, qualname
    # Only the engine makes decisions, and today nothing consumes an assessment at all.
    assert returns_decision == ["engine.decide"]
    assert takes_assessment == []


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
