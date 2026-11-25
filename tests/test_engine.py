from datetime import UTC, datetime

import pytest

from agent_transaction_monitoring.domain import (
    IDENTIFIER_MAX_LENGTH,
    AlertOutcome,
    AlertSeverity,
    DecisionSource,
    Mandate,
    Transaction,
)
from agent_transaction_monitoring.engine import (
    RULE_SEVERITY,
    SEVERITY_OUTCOME,
    alert_id_for,
    decide,
    raise_alerts,
)
from agent_transaction_monitoring.rules import Finding, RuleId, evaluate

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def test_every_rule_has_a_severity() -> None:
    assert set(RULE_SEVERITY) == set(RuleId)


def test_spending_beyond_authorisation_is_high_severity() -> None:
    for rule in (
        RuleId.PAYEE_NOT_ALLOWED,
        RuleId.PER_TX_LIMIT_EXCEEDED,
        RuleId.PERIOD_LIMIT_EXCEEDED,
    ):
        assert RULE_SEVERITY[rule] is AlertSeverity.HIGH
        assert SEVERITY_OUTCOME[RULE_SEVERITY[rule]] is AlertOutcome.ESCALATE


def test_rule_engine_never_dismisses() -> None:
    assert AlertOutcome.DISMISS not in SEVERITY_OUTCOME.values()
    assert set(SEVERITY_OUTCOME) == set(AlertSeverity)


def test_alerts_are_deterministic_and_carry_rule_severity(
    out_of_mandate_transactions: list[Transaction], mandate: Mandate
) -> None:
    tx = out_of_mandate_transactions[1]
    findings = evaluate(tx, {mandate.mandate_id: mandate})
    first = raise_alerts(findings, tx.agent_id, NOW)
    second = raise_alerts(findings, tx.agent_id, NOW)
    assert first == second
    (alert,) = first
    assert alert.alert_id == f"alert:payee_not_allowed:{tx.transaction_id}"
    assert alert.severity is AlertSeverity.HIGH
    assert alert.transaction_ids == (tx.transaction_id,)


@pytest.mark.parametrize(
    ("index", "outcome"),
    [(0, AlertOutcome.ESCALATE), (1, AlertOutcome.ESCALATE), (2, AlertOutcome.ESCALATE)],
)
def test_decisions_come_from_the_rule_engine(
    out_of_mandate_transactions: list[Transaction],
    mandate: Mandate,
    index: int,
    outcome: AlertOutcome,
) -> None:
    tx = out_of_mandate_transactions[index]
    (alert,) = raise_alerts(evaluate(tx, {mandate.mandate_id: mandate}), tx.agent_id, NOW)
    decision = decide(alert, NOW)
    assert decision.outcome is outcome
    assert decision.decided_by is DecisionSource.RULE_ENGINE
    assert alert.rule_id in decision.rationale


MAX_LENGTH_TX_ID = "t" * IDENTIFIER_MAX_LENGTH


@pytest.mark.parametrize("rule", list(RuleId))
def test_maximum_length_transaction_id_still_gets_an_alert(rule: RuleId) -> None:
    finding = Finding(rule_id=rule, transaction_ids=(MAX_LENGTH_TX_ID,), detail="x")
    (alert,) = raise_alerts([finding], "agent-synth-001", NOW)
    assert len(alert.alert_id) <= IDENTIFIER_MAX_LENGTH
    assert alert.alert_id.startswith(f"alert:{rule.value}:sha256-")
    assert alert.alert_id == alert_id_for(finding)  # still deterministic


def test_short_transaction_ids_keep_readable_alert_ids() -> None:
    finding = Finding(rule_id=RuleId.NO_MANDATE, transaction_ids=("tx-1",), detail="x")
    assert alert_id_for(finding) == "alert:no_mandate:tx-1"
