"""Contract tests for domain models: immutability, strict typing, invariants."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from agent_transaction_monitoring.domain import (
    CURRENCY_DECIMALS,
    Alert,
    AlertDecision,
    AlertOutcome,
    AlertSeverity,
    DecisionSource,
    Mandate,
    Transaction,
    UnsupportedCurrencyError,
    atomic_units_per_unit,
    currency_decimals,
    to_atomic,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _transaction(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "transaction_id": "tx-1",
        "agent_id": "agent-1",
        "mandate_id": "mandate-1",
        "counterparty_id": "cp-1",
        "amount": 1_000_000,
        "currency": "USDC",
        "initiated_at": NOW,
    }
    return data | overrides


def _mandate(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "mandate_id": "mandate-1",
        "agent_id": "agent-1",
        "operator_id": "operator-1",
        "currency": "USDC",
        "max_amount_per_transaction": to_atomic(100, "USDC"),
        "max_amount_per_period": to_atomic(500, "USDC"),
        "period_seconds": 86_400,
        "allowed_counterparties": frozenset({"cp-1"}),
        "valid_from": NOW,
        "valid_until": NOW + timedelta(days=30),
    }
    return data | overrides


def test_supported_currencies_are_an_explicit_allow_list() -> None:
    assert dict(CURRENCY_DECIMALS) == {"USDC": 6, "SIM-USDC": 6}


@pytest.mark.parametrize("currency", ["USDC", "SIM-USDC"])
def test_scale_lookup_for_supported_currencies(currency: str) -> None:
    assert currency_decimals(currency) == 6
    assert atomic_units_per_unit(currency) == 1_000_000
    assert to_atomic(3, currency) == 3_000_000


@pytest.mark.parametrize("currency", ["EUR", "usdc", "USDT", ""])
def test_scale_lookup_rejects_unsupported_currency(currency: str) -> None:
    with pytest.raises(UnsupportedCurrencyError):
        currency_decimals(currency)


def test_currency_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        CURRENCY_DECIMALS["EUR"] = 2  # type: ignore[index]


@pytest.mark.parametrize("currency", ["EUR", "usdc", "USDT"])
def test_transaction_rejects_unsupported_currency(currency: str) -> None:
    with pytest.raises(ValidationError, match="unsupported currency"):
        Transaction(**_transaction(currency=currency))


def test_mandate_rejects_unsupported_currency() -> None:
    with pytest.raises(ValidationError, match="unsupported currency"):
        Mandate(**_mandate(currency="EUR"))


@pytest.mark.parametrize("amount", [1.5, 1_000_000.0, "1000000", True])
def test_transaction_amount_rejects_non_integers(amount: object) -> None:
    with pytest.raises(ValidationError):
        Transaction(**_transaction(amount=amount))


@pytest.mark.parametrize("amount", [0, -1])
def test_transaction_amount_must_be_positive(amount: int) -> None:
    with pytest.raises(ValidationError):
        Transaction(**_transaction(amount=amount))


def test_transaction_is_immutable() -> None:
    tx = Transaction(**_transaction())
    with pytest.raises(ValidationError):
        tx.amount = 2  # type: ignore[misc]


def test_transaction_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Transaction(**_transaction(risk_score=0.1))


def test_transaction_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        Transaction(**_transaction(initiated_at=datetime(2026, 3, 1, 12, 0)))  # noqa: DTZ001


def test_transaction_memo_length_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Transaction(**_transaction(memo="x" * 1025))


def test_mandate_rejects_inverted_validity_window() -> None:
    with pytest.raises(ValidationError, match="valid_until"):
        Mandate(**_mandate(valid_until=NOW - timedelta(seconds=1)))


def test_mandate_rejects_per_transaction_limit_above_period_limit() -> None:
    with pytest.raises(ValidationError, match="max_amount_per_transaction"):
        Mandate(**_mandate(max_amount_per_transaction=to_atomic(600, "USDC")))


def test_mandate_validity_window_is_half_open() -> None:
    mandate = Mandate(**_mandate())
    assert mandate.is_active_at(mandate.valid_from)
    assert not mandate.is_active_at(mandate.valid_until)


def test_alert_requires_at_least_one_transaction() -> None:
    with pytest.raises(ValidationError):
        Alert(
            alert_id="alert-1",
            rule_id="rule-1",
            agent_id="agent-1",
            transaction_ids=(),
            severity=AlertSeverity.HIGH,
            reason="test",
            raised_at=NOW,
        )


def test_decision_source_has_no_model_member() -> None:
    assert {s.value for s in DecisionSource} == {
        "rule_engine",
        "application_policy",
        "human_reviewer",
    }


@pytest.mark.parametrize("rationale", ["", "   ", "\n\t"])
def test_dismiss_decision_requires_non_blank_rationale(rationale: str) -> None:
    with pytest.raises(ValidationError, match="rationale"):
        AlertDecision(
            alert_id="alert-1",
            outcome=AlertOutcome.DISMISS,
            decided_by=DecisionSource.HUMAN_REVIEWER,
            rationale=rationale,
            decided_at=NOW,
        )


def test_dismiss_decision_with_rationale_is_accepted() -> None:
    decision = AlertDecision(
        alert_id="alert-1",
        outcome=AlertOutcome.DISMISS,
        decided_by=DecisionSource.HUMAN_REVIEWER,
        rationale="  Counterparty verified against operator invoice.  ",
        decided_at=NOW,
    )
    assert decision.rationale == "Counterparty verified against operator invoice."


def test_alert_decision_cannot_be_attributed_to_a_model() -> None:
    with pytest.raises(ValidationError):
        AlertDecision(
            alert_id="alert-1",
            outcome=AlertOutcome.DISMISS,
            decided_by="model",  # type: ignore[arg-type]
            rationale="model said low risk",
            decided_at=NOW,
        )
