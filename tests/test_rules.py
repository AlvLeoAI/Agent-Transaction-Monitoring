from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent_transaction_monitoring.domain import MAX_PERIOD_SECONDS, Mandate, Transaction
from agent_transaction_monitoring.rules import (
    STRUCTURING_MIN_TRANSACTIONS,
    RuleId,
    evaluate,
    rule_period_limit_exceeded,
    rule_possible_structuring,
    window_transactions,
)

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def make_tx(base: Transaction, **overrides: Any) -> Transaction:
    return Transaction.model_validate({**base.model_dump(), **overrides})


def make_mandate(base: Mandate, **overrides: Any) -> Mandate:
    return Mandate.model_validate({**base.model_dump(), **overrides})


def rule_ids(findings: list[Any]) -> set[RuleId]:
    return {f.rule_id for f in findings}


@pytest.fixture
def mandates(mandate: Mandate) -> dict[str, Mandate]:
    return {mandate.mandate_id: mandate}


@pytest.fixture
def base_tx(normal_transactions: list[Transaction]) -> Transaction:
    return make_tx(normal_transactions[0], initiated_at=T0)


# --- Phase 0 fixtures -----------------------------------------------------------------


def test_normal_transactions_raise_nothing(
    normal_transactions: list[Transaction], mandates: dict[str, Mandate]
) -> None:
    for i, tx in enumerate(normal_transactions):
        assert evaluate(tx, mandates, normal_transactions[:i]) == []


OUT_OF_MANDATE_DESIGN = {
    "tx-oom-over-limit": RuleId.PER_TX_LIMIT_EXCEEDED,
    "tx-oom-unknown-counterparty": RuleId.PAYEE_NOT_ALLOWED,
    "tx-oom-expired-mandate": RuleId.MANDATE_NOT_ACTIVE,
}


def test_design_table_covers_every_out_of_mandate_fixture(
    out_of_mandate_transactions: list[Transaction],
) -> None:
    assert {tx.transaction_id for tx in out_of_mandate_transactions} == set(OUT_OF_MANDATE_DESIGN)


@pytest.mark.parametrize("with_history", [False, True])
def test_each_out_of_mandate_fixture_triggers_exactly_its_rule(
    out_of_mandate_transactions: list[Transaction],
    normal_transactions: list[Transaction],
    mandates: dict[str, Mandate],
    with_history: bool,
) -> None:
    history = normal_transactions if with_history else []
    for tx in out_of_mandate_transactions:
        findings = evaluate(tx, mandates, history)
        assert [f.rule_id for f in findings] == [OUT_OF_MANDATE_DESIGN[tx.transaction_id]]
        assert findings[0].transaction_ids == (tx.transaction_id,)


def test_structuring_fixture_fires_only_once_the_period_limit_is_crossed(
    structuring_transactions: list[Transaction], mandates: dict[str, Mandate]
) -> None:
    *early, last = structuring_transactions
    for i, tx in enumerate(early):
        assert evaluate(tx, mandates, structuring_transactions[:i]) == []

    findings = evaluate(last, mandates, early)
    assert [f.rule_id for f in findings] == [
        RuleId.PERIOD_LIMIT_EXCEEDED,
        RuleId.POSSIBLE_STRUCTURING,
    ]
    expected_ids = (last.transaction_id, *(t.transaction_id for t in early))
    assert all(f.transaction_ids == expected_ids for f in findings)


# --- R1 no_mandate ----------------------------------------------------------------------


def test_r1_missing_mandate_id_skips_every_other_rule(
    base_tx: Transaction, mandates: dict[str, Mandate]
) -> None:
    tx = make_tx(base_tx, mandate_id=None, counterparty_id="cp-unknown", amount=10**15)
    findings = evaluate(tx, mandates)
    assert rule_ids(findings) == {RuleId.NO_MANDATE}
    assert "no mandate_id" in findings[0].detail


@pytest.mark.parametrize(
    ("tx_overrides", "mandate_overrides", "detail"),
    [
        ({"mandate_id": "mandate-missing"}, {}, "not known"),
        ({}, {"agent_id": "agent-other"}, "belongs to agent agent-other"),
        ({}, {"currency": "SIM-USDC"}, "denominated in SIM-USDC"),
    ],
)
def test_r1_unusable_mandate_counts_as_no_mandate(
    base_tx: Transaction,
    mandate: Mandate,
    tx_overrides: dict[str, Any],
    mandate_overrides: dict[str, Any],
    detail: str,
) -> None:
    other = make_mandate(mandate, **mandate_overrides)
    findings = evaluate(make_tx(base_tx, **tx_overrides), {other.mandate_id: other})
    assert rule_ids(findings) == {RuleId.NO_MANDATE}
    assert detail in findings[0].detail


# --- R2 mandate_not_active ------------------------------------------------------------


def test_r2_validity_window_is_half_open(
    base_tx: Transaction, mandate: Mandate, mandates: dict[str, Mandate]
) -> None:
    at_start = make_tx(base_tx, initiated_at=mandate.valid_from)
    at_end = make_tx(base_tx, initiated_at=mandate.valid_until)
    before = make_tx(base_tx, initiated_at=mandate.valid_from - timedelta(seconds=1))
    assert evaluate(at_start, mandates) == []
    assert rule_ids(evaluate(at_end, mandates)) == {RuleId.MANDATE_NOT_ACTIVE}
    assert rule_ids(evaluate(before, mandates)) == {RuleId.MANDATE_NOT_ACTIVE}


# --- R3 payee_not_allowed -------------------------------------------------------------


def test_r3_empty_allow_list_denies_every_payee(base_tx: Transaction, mandate: Mandate) -> None:
    closed = make_mandate(mandate, allowed_counterparties=frozenset())
    assert rule_ids(evaluate(base_tx, {closed.mandate_id: closed})) == {RuleId.PAYEE_NOT_ALLOWED}


# --- R4 per_tx_limit_exceeded ---------------------------------------------------------


def test_r4_limit_is_inclusive(
    base_tx: Transaction, mandate: Mandate, mandates: dict[str, Mandate]
) -> None:
    limit = mandate.max_amount_per_transaction
    assert evaluate(make_tx(base_tx, amount=limit), mandates) == []
    over = evaluate(make_tx(base_tx, amount=limit + 1), mandates)
    assert rule_ids(over) == {RuleId.PER_TX_LIMIT_EXCEEDED}


# --- R5 period_limit_exceeded ---------------------------------------------------------


@pytest.fixture
def small_mandate(mandate: Mandate) -> Mandate:
    # per-tx 100, period 150 over one hour: easy arithmetic for window tests.
    return make_mandate(
        mandate, max_amount_per_transaction=100, max_amount_per_period=150, period_seconds=3600
    )


def prior(base: Transaction, tx_id: str, seconds_before: int, amount: int) -> Transaction:
    return make_tx(
        base,
        transaction_id=tx_id,
        initiated_at=T0 - timedelta(seconds=seconds_before),
        amount=amount,
    )


def test_r5_counts_prior_spend_inside_the_window(
    base_tx: Transaction, small_mandate: Mandate
) -> None:
    tx = make_tx(base_tx, amount=80)
    history = [prior(base_tx, "p1", 600, 80)]
    findings = rule_period_limit_exceeded(tx, small_mandate, history)
    assert [f.transaction_ids for f in findings] == [(tx.transaction_id, "p1")]


def test_r5_window_excludes_payment_exactly_one_period_earlier(
    base_tx: Transaction, small_mandate: Mandate
) -> None:
    tx = make_tx(base_tx, amount=80)
    assert rule_period_limit_exceeded(tx, small_mandate, [prior(base_tx, "p1", 3600, 80)]) == []
    hit = rule_period_limit_exceeded(tx, small_mandate, [prior(base_tx, "p1", 3599, 80)])
    assert len(hit) == 1


def test_r5_limit_is_inclusive(base_tx: Transaction, small_mandate: Mandate) -> None:
    tx = make_tx(base_tx, amount=70)
    assert rule_period_limit_exceeded(tx, small_mandate, [prior(base_tx, "p1", 60, 80)]) == []


def test_window_ignores_duplicates_self_future_and_other_scopes(
    base_tx: Transaction, small_mandate: Mandate
) -> None:
    tx = make_tx(base_tx, amount=80)
    p1 = prior(base_tx, "p1", 60, 10)
    history = [
        p1,
        p1,  # duplicate id
        tx,  # the transaction itself
        prior(base_tx, "future", -60, 500),
        make_tx(p1, transaction_id="other-agent", agent_id="agent-other"),
        make_tx(p1, transaction_id="other-mandate", mandate_id="mandate-other"),
        make_tx(p1, transaction_id="no-mandate", mandate_id=None),
        make_tx(p1, transaction_id="other-currency", currency="SIM-USDC"),
    ]
    assert [t.transaction_id for t in window_transactions(tx, small_mandate, history)] == ["p1"]


# --- R6 possible_structuring ----------------------------------------------------------


def test_r6_needs_the_minimum_number_of_payments(
    base_tx: Transaction, small_mandate: Mandate
) -> None:
    assert STRUCTURING_MIN_TRANSACTIONS == 3
    two = [prior(base_tx, "p1", 60, 90)]
    assert rule_possible_structuring(make_tx(base_tx, amount=90), small_mandate, two) == []

    three = [prior(base_tx, "p1", 120, 60), prior(base_tx, "p2", 60, 60)]
    findings = rule_possible_structuring(make_tx(base_tx, amount=60), small_mandate, three)
    assert [f.transaction_ids for f in findings] == [(base_tx.transaction_id, "p1", "p2")]


def test_r6_sum_must_exceed_period_limit(base_tx: Transaction, small_mandate: Mandate) -> None:
    history = [prior(base_tx, "p1", 120, 50), prior(base_tx, "p2", 60, 50)]
    assert rule_possible_structuring(make_tx(base_tx, amount=50), small_mandate, history) == []


def test_r6_ignores_payments_at_or_over_the_per_tx_limit(
    base_tx: Transaction, small_mandate: Mandate
) -> None:
    history = [prior(base_tx, "big", 120, 100), prior(base_tx, "p2", 60, 60)]
    assert rule_possible_structuring(make_tx(base_tx, amount=60), small_mandate, history) == []
    # The current payment being at the limit also rules out R6.
    split = [prior(base_tx, "p1", 120, 60), prior(base_tx, "p2", 60, 60)]
    assert rule_possible_structuring(make_tx(base_tx, amount=100), small_mandate, split) == []


def test_rule_ids_are_stable() -> None:
    assert [r.value for r in RuleId] == [
        "no_mandate",
        "mandate_not_active",
        "payee_not_allowed",
        "per_tx_limit_exceeded",
        "period_limit_exceeded",
        "possible_structuring",
    ]


# --- Window edge cases (audit findings A5, A6) -----------------------------------------


def test_window_includes_payments_at_the_same_instant(
    base_tx: Transaction, small_mandate: Mandate
) -> None:
    tx = make_tx(base_tx, amount=60)
    same_instant = [prior(base_tx, "b-same", 0, 60), prior(base_tx, "a-same", 0, 60)]
    window = window_transactions(tx, small_mandate, same_instant)
    assert [t.transaction_id for t in window] == ["a-same", "b-same"]  # ties ordered by id
    assert rule_ids(rule_period_limit_exceeded(tx, small_mandate, same_instant)) == {
        RuleId.PERIOD_LIMIT_EXCEEDED
    }
    assert rule_ids(rule_possible_structuring(tx, small_mandate, same_instant)) == {
        RuleId.POSSIBLE_STRUCTURING
    }


def test_window_result_does_not_depend_on_history_order(
    base_tx: Transaction, small_mandate: Mandate
) -> None:
    tx = make_tx(base_tx, amount=40)
    history = [
        prior(base_tx, "p3", 60, 40),
        prior(base_tx, "p1", 1800, 40),
        prior(base_tx, "old", 3600, 40),  # exactly one period earlier: aged out
        prior(base_tx, "p2", 600, 40),
    ]
    orders = [history, list(reversed(history)), sorted(history, key=lambda t: t.transaction_id)]
    results = [
        (
            [t.transaction_id for t in window_transactions(tx, small_mandate, h)],
            rule_period_limit_exceeded(tx, small_mandate, h),
            rule_possible_structuring(tx, small_mandate, h),
        )
        for h in orders
    ]
    assert results[0][0] == ["p1", "p2", "p3"]  # oldest first
    assert results[0][1] and results[0][2]
    assert all(r == results[0] for r in results)


def test_window_near_the_earliest_datetime_does_not_overflow(
    base_tx: Transaction, mandate: Mandate
) -> None:
    year_one = datetime(1, 1, 1, tzinfo=UTC)
    longest = make_mandate(mandate, period_seconds=MAX_PERIOD_SECONDS)
    tx = make_tx(base_tx, initiated_at=year_one + timedelta(hours=1), amount=10)
    history = [make_tx(base_tx, transaction_id="p-early", initiated_at=year_one, amount=10)]
    assert [t.transaction_id for t in window_transactions(tx, longest, history)] == ["p-early"]
    assert rule_period_limit_exceeded(tx, longest, history) == []
