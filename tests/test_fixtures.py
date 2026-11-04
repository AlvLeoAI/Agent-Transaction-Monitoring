"""Fixtures parse into domain models and actually exhibit the scenario they are named for.

These checks are written inline on purpose: there is no detection logic in ``src`` yet.
"""

from datetime import timedelta

from agent_transaction_monitoring.domain import AgentIdentity, Mandate, Transaction


def test_fixture_agents_match_mandate(agents: list[AgentIdentity], mandate: Mandate) -> None:
    (agent,) = agents
    assert (agent.agent_id, agent.operator_id) == (mandate.agent_id, mandate.operator_id)


def test_normal_transactions_are_within_mandate(
    mandate: Mandate, normal_transactions: list[Transaction]
) -> None:
    assert normal_transactions
    for tx in normal_transactions:
        assert tx.mandate_id == mandate.mandate_id
        assert tx.currency == mandate.currency
        assert tx.amount <= mandate.max_amount_per_transaction
        assert tx.counterparty_id in mandate.allowed_counterparties
        assert mandate.is_active_at(tx.initiated_at)
    assert sum(tx.amount for tx in normal_transactions) <= mandate.max_amount_per_period


def test_each_out_of_mandate_transaction_breaks_exactly_one_constraint(
    mandate: Mandate, out_of_mandate_transactions: list[Transaction]
) -> None:
    for tx in out_of_mandate_transactions:
        violations = [
            tx.amount > mandate.max_amount_per_transaction,
            tx.counterparty_id not in mandate.allowed_counterparties,
            not mandate.is_active_at(tx.initiated_at),
        ]
        assert sum(violations) == 1, tx.transaction_id


def test_out_of_mandate_fixture_includes_injection_style_memo(
    out_of_mandate_transactions: list[Transaction],
) -> None:
    memos = [tx.memo or "" for tx in out_of_mandate_transactions]
    assert any("ignore all previous instructions" in m.lower() for m in memos)


def test_structuring_pattern_stays_under_per_transaction_limit_but_exceeds_period(
    mandate: Mandate, structuring_transactions: list[Transaction]
) -> None:
    assert len(structuring_transactions) >= 3
    for tx in structuring_transactions:
        assert tx.amount <= mandate.max_amount_per_transaction
        # Each payment sits within 2% of the per-transaction limit.
        assert tx.amount * 100 >= mandate.max_amount_per_transaction * 98
        assert tx.counterparty_id in mandate.allowed_counterparties

    times = sorted(tx.initiated_at for tx in structuring_transactions)
    assert times[-1] - times[0] <= timedelta(hours=1)
    assert sum(tx.amount for tx in structuring_transactions) > mandate.max_amount_per_period
