"""Deterministic monitoring rules.

Every rule is a pure function over domain models: no I/O, no clock, no model calls. A rule
returns zero or more ``Finding`` objects carrying a stable ``RuleId``. Findings are facts
about a transaction; turning them into alerts and decisions happens in ``engine``.

Rules see only what the caller passes in. ``history`` is the agent's prior transactions;
the caller is responsible for supplying enough of it to cover the longest mandate period.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import Field

from agent_transaction_monitoring.domain import (
    FrozenModel,
    Identifier,
    Mandate,
    RequiredText,
    Transaction,
)

# R6 fires only when at least this many sub-limit transactions, the current one included,
# fall inside one rolling period window. Two payments that together exceed the period
# limit are a limit breach (R5), not yet a pattern.
STRUCTURING_MIN_TRANSACTIONS = 3


class RuleId(StrEnum):
    """Stable rule identifiers. Values are persisted on alerts; never rename one."""

    NO_MANDATE = "no_mandate"  # R1
    MANDATE_NOT_ACTIVE = "mandate_not_active"  # R2
    PAYEE_NOT_ALLOWED = "payee_not_allowed"  # R3
    PER_TX_LIMIT_EXCEEDED = "per_tx_limit_exceeded"  # R4
    PERIOD_LIMIT_EXCEEDED = "period_limit_exceeded"  # R5
    POSSIBLE_STRUCTURING = "possible_structuring"  # R6


class Finding(FrozenModel):
    """One rule firing on one transaction.

    ``transaction_ids`` starts with the evaluated transaction; window rules (R5, R6) append
    the prior transactions that contributed to the breach, oldest first.
    """

    rule_id: RuleId
    transaction_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    detail: RequiredText


def _finding(
    rule_id: RuleId, tx: Transaction, detail: str, related: Iterable[Transaction] = ()
) -> Finding:
    ids = (tx.transaction_id, *(t.transaction_id for t in related))
    return Finding(rule_id=rule_id, transaction_ids=ids, detail=detail)


def applicable_mandate(tx: Transaction, mandates: Mapping[str, Mandate]) -> Mandate | None:
    """The mandate that governs ``tx``, or ``None`` when no mandate can.

    A mandate that exists but belongs to another agent, or is denominated in another
    currency, cannot authorise the transaction, so it is treated as absent.
    """
    if tx.mandate_id is None:
        return None
    mandate = mandates.get(tx.mandate_id)
    if mandate is None or mandate.agent_id != tx.agent_id or mandate.currency != tx.currency:
        return None
    return mandate


def rule_no_mandate(tx: Transaction, mandates: Mapping[str, Mandate]) -> list[Finding]:
    """R1: the transaction is not governed by any usable mandate."""
    if tx.mandate_id is None:
        return [_finding(RuleId.NO_MANDATE, tx, "transaction carries no mandate_id")]
    mandate = mandates.get(tx.mandate_id)
    if mandate is None:
        detail = f"mandate {tx.mandate_id} is not known"
    elif mandate.agent_id != tx.agent_id:
        detail = f"mandate {tx.mandate_id} belongs to agent {mandate.agent_id}"
    elif mandate.currency != tx.currency:
        detail = f"mandate {tx.mandate_id} is denominated in {mandate.currency}, not {tx.currency}"
    else:
        return []
    return [_finding(RuleId.NO_MANDATE, tx, detail)]


def rule_mandate_not_active(tx: Transaction, mandate: Mandate) -> list[Finding]:
    """R2: the transaction falls outside the mandate's half-open validity window."""
    if mandate.is_active_at(tx.initiated_at):
        return []
    detail = (
        f"initiated at {tx.initiated_at.isoformat()}, outside "
        f"[{mandate.valid_from.isoformat()}, {mandate.valid_until.isoformat()})"
    )
    return [_finding(RuleId.MANDATE_NOT_ACTIVE, tx, detail)]


def rule_payee_not_allowed(tx: Transaction, mandate: Mandate) -> list[Finding]:
    """R3: the counterparty is not on the allow-list. An empty allow-list denies everyone."""
    if tx.counterparty_id in mandate.allowed_counterparties:
        return []
    return [
        _finding(
            RuleId.PAYEE_NOT_ALLOWED,
            tx,
            f"counterparty {tx.counterparty_id} is not allowed by mandate {mandate.mandate_id}",
        )
    ]


def rule_per_tx_limit_exceeded(tx: Transaction, mandate: Mandate) -> list[Finding]:
    """R4: a single transaction above the per-transaction limit (equal is allowed)."""
    if tx.amount <= mandate.max_amount_per_transaction:
        return []
    limit = mandate.max_amount_per_transaction
    detail = f"amount {tx.amount} exceeds per-transaction limit {limit}"
    return [_finding(RuleId.PER_TX_LIMIT_EXCEEDED, tx, detail)]


def window_transactions(
    tx: Transaction, mandate: Mandate, history: Sequence[Transaction]
) -> list[Transaction]:
    """Prior transactions under the same mandate inside the rolling period ending at ``tx``.

    The window is ``(tx.initiated_at - period_seconds, tx.initiated_at]``: a payment exactly
    one period earlier has aged out. ``tx`` itself and duplicate ids in ``history`` are
    excluded, so replaying a transaction never double-counts it. Oldest first.
    """
    start = tx.initiated_at - timedelta(seconds=mandate.period_seconds)
    seen = {tx.transaction_id}
    prior: list[Transaction] = []
    for other in history:
        if other.transaction_id in seen:
            continue
        if (
            other.agent_id == tx.agent_id
            and other.mandate_id == mandate.mandate_id
            and other.currency == tx.currency
            and start < other.initiated_at <= tx.initiated_at
        ):
            seen.add(other.transaction_id)
            prior.append(other)
    return sorted(prior, key=lambda t: (t.initiated_at, t.transaction_id))


def rule_period_limit_exceeded(
    tx: Transaction, mandate: Mandate, history: Sequence[Transaction]
) -> list[Finding]:
    """R5: rolling spend over ``period_seconds``, this transaction included, exceeds the limit."""
    prior = window_transactions(tx, mandate, history)
    total = tx.amount + sum(t.amount for t in prior)
    if total <= mandate.max_amount_per_period:
        return []
    detail = (
        f"rolling {mandate.period_seconds}s spend {total} exceeds period limit "
        f"{mandate.max_amount_per_period}"
    )
    return [_finding(RuleId.PERIOD_LIMIT_EXCEEDED, tx, detail, prior)]


def rule_possible_structuring(
    tx: Transaction, mandate: Mandate, history: Sequence[Transaction]
) -> list[Finding]:
    """R6: several payments, each under the per-transaction limit, add up past the period limit.

    Only transactions strictly under the per-transaction limit count, the current one
    included; a payment that is itself over the limit is R4's concern, not a split.
    """
    limit = mandate.max_amount_per_transaction
    if tx.amount >= limit:
        return []
    prior = [t for t in window_transactions(tx, mandate, history) if t.amount < limit]
    count = len(prior) + 1
    total = tx.amount + sum(t.amount for t in prior)
    if count < STRUCTURING_MIN_TRANSACTIONS or total <= mandate.max_amount_per_period:
        return []
    detail = (
        f"{count} payments each under the per-transaction limit {limit} total {total}, "
        f"above the period limit {mandate.max_amount_per_period}"
    )
    return [_finding(RuleId.POSSIBLE_STRUCTURING, tx, detail, prior)]


def evaluate(
    tx: Transaction,
    mandates: Mapping[str, Mandate],
    history: Sequence[Transaction] = (),
) -> list[Finding]:
    """Run every rule against ``tx`` and return the findings in rule order.

    When R1 fires there is no mandate to measure against, so R2 to R6 are skipped.
    """
    mandate = applicable_mandate(tx, mandates)
    if mandate is None:
        return rule_no_mandate(tx, mandates)
    return [
        *rule_mandate_not_active(tx, mandate),
        *rule_payee_not_allowed(tx, mandate),
        *rule_per_tx_limit_exceeded(tx, mandate),
        *rule_period_limit_exceeded(tx, mandate, history),
        *rule_possible_structuring(tx, mandate, history),
    ]
