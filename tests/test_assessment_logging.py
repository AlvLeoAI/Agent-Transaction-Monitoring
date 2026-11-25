"""Non-valid assessments are logged with identifiers only (audit finding A3, threat T3).

Every outcome runs with a memo and a raw model payload that contain distinctive canary
strings. Each non-valid outcome must produce one warning carrying the required fields,
and no log record from any logger may contain either canary.
"""

import json
import logging
from typing import Any

import pytest

from agent_transaction_monitoring.assessment import (
    LOG_FIELDS,
    ModelAssessment,
    ValidationStatus,
    log_fields,
)
from agent_transaction_monitoring.domain import Transaction
from agent_transaction_monitoring.prompts import Prompt
from agent_transaction_monitoring.settings import Settings

from support import (
    VALID_OUTPUT,
    FakeMessagesClient,
    connection_error,
    make_assessor,
    make_message,
    status_error,
)

MEMO_CANARY = "MEMO-CANARY-7f3a9c"
RAW_CANARY = "RAW-CANARY-91b2e4"
LOGGER = "agent_transaction_monitoring.assessment"

RAW_PAYLOAD = json.dumps({**VALID_OUTPUT, "rationale": f"see {RAW_CANARY}"})

NOT_VALID: dict[str, tuple[Any, ValidationStatus]] = {
    "schema_invalid": (
        make_message(f'{{"risk_level": "{RAW_CANARY}"}}'),
        ValidationStatus.SCHEMA_INVALID,
    ),
    "refused": (
        make_message(RAW_CANARY, stop_reason="refusal", refusal_category="general_harms"),
        ValidationStatus.REFUSED,
    ),
    "truncated": (make_message(RAW_PAYLOAD, stop_reason="max_tokens"), ValidationStatus.TRUNCATED),
    "unexpected_stop_reason": (
        make_message(RAW_PAYLOAD, stop_reason="pause_turn"),
        ValidationStatus.UNEXPECTED_STOP_REASON,
    ),
    "api_error_status": (status_error(529), ValidationStatus.API_ERROR),
    "api_error_connection": (connection_error(), ValidationStatus.API_ERROR),
}


@pytest.fixture
def canary_tx(injection_transaction: Transaction) -> Transaction:
    return injection_transaction.model_copy(update={"memo": f"pay now {MEMO_CANARY}"})


def run(result: Any, tx: Transaction, settings: Settings, prompt: Prompt) -> ModelAssessment:
    client = FakeMessagesClient(result)
    return make_assessor(client, settings, prompt).assess(
        tx, [("payee_not_allowed", "detail")], ["alert:payee_not_allowed:x"]
    )


def assert_no_canaries(records: list[logging.LogRecord]) -> None:
    for record in records:
        rendered = " ".join([record.getMessage(), repr(record.args), repr(vars(record))])
        assert MEMO_CANARY not in rendered, record
        assert RAW_CANARY not in rendered, record


@pytest.mark.parametrize("case", NOT_VALID.values(), ids=NOT_VALID.keys())
def test_not_valid_outcomes_log_required_fields_and_nothing_else(
    case: tuple[Any, ValidationStatus],
    canary_tx: Transaction,
    settings: Settings,
    classifier_prompt: Prompt,
    caplog: pytest.LogCaptureFixture,
) -> None:
    result, status = case
    with caplog.at_level(logging.DEBUG):
        assessment = run(result, canary_tx, settings, classifier_prompt)

    assert assessment.validation_status is status
    warnings = [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    (record,) = warnings
    expected = log_fields(assessment)
    for field in LOG_FIELDS:
        assert getattr(record, field) == expected[field], field
    # Fields added through `extra=` are plain attributes, invisible to the type checker.
    assert getattr(record, "validation_status") == status.value  # noqa: B009
    assert getattr(record, "transaction_id") == canary_tx.transaction_id  # noqa: B009
    assert_no_canaries(caplog.records)


def test_valid_outcome_logs_no_warning(
    canary_tx: Transaction,
    settings: Settings,
    classifier_prompt: Prompt,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        assessment = run(make_message(RAW_PAYLOAD), canary_tx, settings, classifier_prompt)
    assert assessment.validation_status is ValidationStatus.VALID
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert_no_canaries(caplog.records)


def test_the_canaries_really_reach_the_record(
    canary_tx: Transaction, settings: Settings, classifier_prompt: Prompt
) -> None:
    """Guard against a vacuous test: the canaries must be present where they are stored."""
    result, _ = NOT_VALID["truncated"]
    assessment = run(result, canary_tx, settings, classifier_prompt)
    assert assessment.raw_output is not None and RAW_CANARY in assessment.raw_output
    assert canary_tx.memo is not None and MEMO_CANARY in canary_tx.memo
