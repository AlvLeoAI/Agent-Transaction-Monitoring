from agent_transaction_monitoring.domain import Mandate, Transaction
from agent_transaction_monitoring.pipeline import monitor_transaction
from agent_transaction_monitoring.prompts import Prompt
from agent_transaction_monitoring.rules import RuleId
from agent_transaction_monitoring.settings import Settings

from support import FIXED_NOW, FakeMessagesClient, make_assessor, valid_message


def test_clean_transaction_never_calls_the_model(
    normal_transactions: list[Transaction],
    mandate: Mandate,
    settings: Settings,
    classifier_prompt: Prompt,
) -> None:
    client = FakeMessagesClient(valid_message())
    result = monitor_transaction(
        normal_transactions[0],
        {mandate.mandate_id: mandate},
        [],
        now=FIXED_NOW,
        assessor=make_assessor(client, settings, classifier_prompt),
    )
    assert (result.findings, result.alerts, result.decisions) == ((), (), ())
    assert result.assessment is None
    assert client.requests == []


def test_structuring_run_alerts_and_assesses_once(
    structuring_transactions: list[Transaction],
    mandate: Mandate,
    settings: Settings,
    classifier_prompt: Prompt,
) -> None:
    *early, last = structuring_transactions
    client = FakeMessagesClient(valid_message())
    result = monitor_transaction(
        last,
        {mandate.mandate_id: mandate},
        early,
        now=FIXED_NOW,
        assessor=make_assessor(client, settings, classifier_prompt),
    )
    assert [a.rule_id for a in result.alerts] == [
        RuleId.PERIOD_LIMIT_EXCEEDED.value,
        RuleId.POSSIBLE_STRUCTURING.value,
    ]
    assert [d.outcome.value for d in result.decisions] == ["escalate", "escalate"]
    (request,) = client.requests
    assert 'rule_id="possible_structuring"' in request.user_content
    assert result.assessment is not None
    assert result.assessment.transaction_id == last.transaction_id


def test_without_assessor_the_pipeline_is_fully_deterministic(
    out_of_mandate_transactions: list[Transaction], mandate: Mandate
) -> None:
    mandates = {mandate.mandate_id: mandate}
    for tx in out_of_mandate_transactions:
        first = monitor_transaction(tx, mandates, [], now=FIXED_NOW)
        assert first == monitor_transaction(tx, mandates, [], now=FIXED_NOW)
        assert first.assessment is None
