"""Turn rule findings into alerts and application-owned decisions.

Pure functions: the caller passes the clock in. Nothing here accepts, reads or imports a
model assessment, and ``tests/test_decision_isolation.py`` checks that this stays true.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from types import MappingProxyType

from agent_transaction_monitoring.domain import (
    Alert,
    AlertDecision,
    AlertOutcome,
    AlertSeverity,
    DecisionSource,
    derived_id,
)
from agent_transaction_monitoring.rules import Finding, RuleId

RULE_SEVERITY: Mapping[RuleId, AlertSeverity] = MappingProxyType(
    {
        RuleId.NO_MANDATE: AlertSeverity.HIGH,
        RuleId.MANDATE_NOT_ACTIVE: AlertSeverity.HIGH,
        RuleId.PAYEE_NOT_ALLOWED: AlertSeverity.HIGH,
        # Spending beyond the operator's authorisation, same category as a disallowed payee.
        RuleId.PER_TX_LIMIT_EXCEEDED: AlertSeverity.HIGH,
        RuleId.PERIOD_LIMIT_EXCEEDED: AlertSeverity.HIGH,
        RuleId.POSSIBLE_STRUCTURING: AlertSeverity.HIGH,
    }
)

# The rule engine never dismisses: a dismissal needs a human reviewer or a later policy.
SEVERITY_OUTCOME: Mapping[AlertSeverity, AlertOutcome] = MappingProxyType(
    {
        AlertSeverity.HIGH: AlertOutcome.ESCALATE,
        AlertSeverity.MEDIUM: AlertOutcome.NEEDS_REVIEW,
        AlertSeverity.LOW: AlertOutcome.NEEDS_REVIEW,
    }
)


def alert_id_for(finding: Finding) -> str:
    """Deterministic id, so re-evaluating the same transaction yields the same alert."""
    return derived_id(f"alert:{finding.rule_id.value}", finding.transaction_ids[0])


def raise_alerts(findings: Iterable[Finding], agent_id: str, raised_at: datetime) -> list[Alert]:
    return [
        Alert(
            alert_id=alert_id_for(finding),
            rule_id=finding.rule_id.value,
            agent_id=agent_id,
            transaction_ids=finding.transaction_ids,
            severity=RULE_SEVERITY[finding.rule_id],
            reason=finding.detail,
            raised_at=raised_at,
        )
        for finding in findings
    ]


def decide(alert: Alert, decided_at: datetime) -> AlertDecision:
    """The rule engine's initial decision for an alert, from its severity alone."""
    outcome = SEVERITY_OUTCOME[alert.severity]
    return AlertDecision(
        alert_id=alert.alert_id,
        outcome=outcome,
        decided_by=DecisionSource.RULE_ENGINE,
        rationale=(
            f"rule {alert.rule_id} raised a {alert.severity.value}-severity alert; "
            f"rule engine policy maps that to {outcome.value}"
        ),
        decided_at=decided_at,
    )
