"""One real call to the Anthropic API.

Skipped unless ATM_LIVE_TESTS=1 and ANTHROPIC_API_KEY are both set, and always skipped
when CI is set (GitHub Actions sets it). Run by hand, loading the key from .env for this
one command only:

    ATM_LIVE_TESTS=1 uv run --env-file .env pytest -m live -q -s

It assesses a benign transaction from the normal fixture, so anything other than a
``valid`` assessment is a defect: a refusal here means the prompt or request is wrong.
It says nothing about assessment quality (that is Phase 2's evals). ``-s`` shows the
printed summary; pytest also shows it when the test fails.
"""

import os
from datetime import UTC, datetime

import pytest

from agent_transaction_monitoring.assessment import TransactionAssessor, ValidationStatus
from agent_transaction_monitoring.domain import Transaction
from agent_transaction_monitoring.llm_client import build_messages_client
from agent_transaction_monitoring.prompts import load_classifier_prompt
from agent_transaction_monitoring.settings import Settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ATM_LIVE_TESTS") != "1"
        or not os.environ.get("ANTHROPIC_API_KEY")
        or bool(os.environ.get("CI")),
        reason="live test: set ATM_LIVE_TESTS=1 and ANTHROPIC_API_KEY, outside CI",
    ),
]


def test_live_assessment_of_a_benign_transaction(
    normal_transactions: list[Transaction],
) -> None:
    settings = Settings()
    assessor = TransactionAssessor(
        build_messages_client(settings),
        settings,
        load_classifier_prompt(settings.prompts_dir, settings.prompt_version),
        lambda: datetime.now(UTC),
    )
    tx = normal_transactions[0]
    a = assessor.assess(tx, [], [])

    print(
        "\nlive assessment:"
        f" status={a.validation_status.value}"
        f" stop_reason={a.stop_reason}"
        f" model={a.model}"
        f" request_id={a.request_id}"
        f" usage={a.usage.model_dump() if a.usage else None}"
        f" cost_micro_usd={a.cost_micro_usd}"
        f" errors={list(a.validation_errors)}"
        f" refusal_category={a.refusal_category}"
    )

    assert a.validation_status is ValidationStatus.VALID
    assert a.assessment is not None
    assert a.model is not None and a.model.startswith("claude-haiku-5-5")
    assert a.request_id
    assert a.usage is not None and a.usage.output_tokens > 0
    assert a.cost_micro_usd > 0
