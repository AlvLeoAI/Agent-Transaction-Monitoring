"""Domain contracts for agent transaction monitoring.

All models are immutable (frozen), reject unknown fields, and use strict typing so that
values such as ``"100"`` or ``100.0`` are never silently coerced into amounts.

Money is always an integer count of atomic units with ``ATOMIC_DECIMALS`` (six) decimal
places, e.g. ``1_500_000`` == 1.5 USDC. Floats are never accepted for amounts.

Core principle: alert decisions are application-owned. ``AlertDecision.decided_by`` has no
value for a model; model output, when it is introduced, is stored separately and can only
influence a decision through application policy.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

ATOMIC_DECIMALS = 6
ATOMIC_UNITS_PER_UNIT = 10**ATOMIC_DECIMALS

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3,10}$")]
AtomicAmount = Annotated[StrictInt, Field(ge=0)]
PositiveAtomicAmount = Annotated[StrictInt, Field(gt=0)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def to_atomic(whole_units: int) -> int:
    """Convert a whole number of currency units to atomic units (no fractional input)."""
    return whole_units * ATOMIC_UNITS_PER_UNIT


class AgentIdentity(_Frozen):
    """An autonomous agent and the operator accountable for it (Know-Your-Agent)."""

    agent_id: Identifier
    operator_id: Identifier
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    registered_at: AwareDatetime


class Mandate(_Frozen):
    """Operator-approved spending scope for one agent."""

    mandate_id: Identifier
    agent_id: Identifier
    operator_id: Identifier
    currency: CurrencyCode
    max_amount_per_transaction: PositiveAtomicAmount
    max_amount_per_period: PositiveAtomicAmount
    period_seconds: Annotated[StrictInt, Field(gt=0)]
    allowed_counterparties: frozenset[Identifier] = Field(
        description="Counterparties the agent may pay. Empty means none are allowed."
    )
    valid_from: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        if self.max_amount_per_transaction > self.max_amount_per_period:
            raise ValueError("max_amount_per_transaction cannot exceed max_amount_per_period")
        return self

    def is_active_at(self, moment: datetime) -> bool:
        return self.valid_from <= moment < self.valid_until


class Transaction(_Frozen):
    """A payment initiated by an agent.

    ``memo`` is free text supplied by the agent or counterparty. It is untrusted input and
    must never be interpreted as instructions (see docs/threat-model.md).
    """

    transaction_id: Identifier
    agent_id: Identifier
    mandate_id: Identifier | None
    counterparty_id: Identifier
    amount: PositiveAtomicAmount
    currency: CurrencyCode
    initiated_at: AwareDatetime
    memo: Annotated[str, StringConstraints(max_length=1024)] | None = None


class AlertSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Alert(_Frozen):
    """An alert raised by a deterministic rule over one or more transactions."""

    alert_id: Identifier
    rule_id: Identifier
    agent_id: Identifier
    transaction_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    severity: AlertSeverity
    reason: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    raised_at: AwareDatetime


class AlertOutcome(StrEnum):
    ESCALATE = "escalate"
    DISMISS = "dismiss"
    NEEDS_REVIEW = "needs_review"


class DecisionSource(StrEnum):
    """Who owns a decision. There is deliberately no ``MODEL`` member."""

    RULE_ENGINE = "rule_engine"
    APPLICATION_POLICY = "application_policy"
    HUMAN_REVIEWER = "human_reviewer"


class AlertDecision(_Frozen):
    """The application-owned outcome for an alert."""

    alert_id: Identifier
    outcome: AlertOutcome
    decided_by: DecisionSource
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    decided_at: AwareDatetime
