"""End-to-end monitoring of one transaction.

Order matters and is the point of this module: rules run, alerts are raised and decisions
are made first, from rule findings alone. Only then, and only for a transaction that raised
an alert, is the optional assessor asked for advisory evidence. The assessment is attached
to the result next to the decisions; nothing here passes it back into ``decide``.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from agent_transaction_monitoring.assessment import ModelAssessment
from agent_transaction_monitoring.domain import (
    Alert,
    AlertDecision,
    FrozenModel,
    Identifier,
    Mandate,
    Transaction,
)
from agent_transaction_monitoring.engine import decide, raise_alerts
from agent_transaction_monitoring.rules import Finding, evaluate


class Assessor(Protocol):
    def assess(
        self,
        tx: Transaction,
        findings: Sequence[tuple[str, str]],
        alert_ids: Sequence[str],
    ) -> ModelAssessment: ...


class MonitoringResult(FrozenModel):
    transaction_id: Identifier
    findings: tuple[Finding, ...]
    alerts: tuple[Alert, ...]
    decisions: tuple[AlertDecision, ...]
    assessment: ModelAssessment | None


def monitor_transaction(
    tx: Transaction,
    mandates: Mapping[str, Mandate],
    history: Sequence[Transaction],
    *,
    now: datetime,
    assessor: Assessor | None = None,
) -> MonitoringResult:
    findings = tuple(evaluate(tx, mandates, history))
    alerts = tuple(raise_alerts(findings, tx.agent_id, now))
    decisions = tuple(decide(alert, now) for alert in alerts)

    assessment = None
    if assessor is not None and alerts:
        assessment = assessor.assess(
            tx,
            [(f.rule_id.value, f.detail) for f in findings],
            [a.alert_id for a in alerts],
        )
    return MonitoringResult(
        transaction_id=tx.transaction_id,
        findings=findings,
        alerts=alerts,
        decisions=decisions,
        assessment=assessment,
    )
