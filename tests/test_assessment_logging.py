"""Non-valid assessments are logged with identifiers only (audit findings A3, A9, A10; T3).

Every outcome runs with distinctive canary strings in each place untrusted or sensitive
text can come from: the memo, the system prompt, the raw model payload, and the message
and body of the SDK's HTTP and connection errors. Each non-valid outcome must produce one
warning carrying the required fields, and no log record from any logger, formatted with
any traceback, may contain a canary.

The transaction id is the one transaction field allowed in logs. It is a structured field
on the record and never part of the message text (A10).
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
from agent_transaction_monitoring.prompts import Prompt, sha256_hex
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
PROMPT_CANARY = "PROMPT-CANARY-c40d17"
RAW_CANARY = "RAW-CANARY-91b2e4"
STATUS_MESSAGE_CANARY = "STATUS-MESSAGE-CANARY-5e8a21"
STATUS_BODY_CANARY = "STATUS-BODY-CANARY-b37f60"
CONNECTION_CANARY = "CONNECTION-CANARY-2d9c84"
CANARIES = (
    MEMO_CANARY,
    PROMPT_CANARY,
    RAW_CANARY,
    STATUS_MESSAGE_CANARY,
    STATUS_BODY_CANARY,
    CONNECTION_CANARY,
)
LOGGER = "agent_transaction_monitoring.assessment"

RAW_PAYLOAD = json.dumps({**VALID_OUTPUT, "rationale": f"see {RAW_CANARY}"})
ERROR_BODY = {"type": "error", "error": {"type": "api_error", "message": STATUS_BODY_CANARY}}

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
    "api_error_status": (
        status_error(529, message=f"overloaded {STATUS_MESSAGE_CANARY}", body=ERROR_BODY),
        ValidationStatus.API_ERROR,
    ),
    "api_error_connection": (
        connection_error(f"reset by peer {CONNECTION_CANARY}"),
        ValidationStatus.API_ERROR,
    ),
}


@pytest.fixture
def canary_tx(injection_transaction: Transaction) -> Transaction:
    return injection_transaction.model_copy(update={"memo": f"pay now {MEMO_CANARY}"})


@pytest.fixture
def canary_prompt(classifier_prompt: Prompt) -> Prompt:
    text = f"{classifier_prompt.text}\n{PROMPT_CANARY}\n"
    return classifier_prompt.model_copy(
        update={"text": text, "sha256": sha256_hex(text.encode("utf-8"))}
    )


def run(
    result: Any, tx: Transaction, settings: Settings, prompt: Prompt
) -> tuple[ModelAssessment, FakeMessagesClient]:
    client = FakeMessagesClient(result)
    assessment = make_assessor(client, settings, prompt).assess(
        tx, [("payee_not_allowed", "detail")], ["alert:payee_not_allowed:x"]
    )
    return assessment, client


def rendered(record: logging.LogRecord) -> str:
    """Everything a handler could emit: message, args, attributes and any traceback."""
    return " ".join(
        [
            logging.Formatter().format(record),
            record.getMessage(),
            repr(record.args),
            repr(vars(record)),
        ]
    )


def assert_no_canaries(records: list[logging.LogRecord]) -> None:
    for record in records:
        text = rendered(record)
        for canary in CANARIES:
            assert canary not in text, (canary, record)


@pytest.mark.parametrize("case", NOT_VALID.values(), ids=NOT_VALID.keys())
def test_not_valid_outcomes_log_required_fields_and_nothing_else(
    case: tuple[Any, ValidationStatus],
    canary_tx: Transaction,
    settings: Settings,
    canary_prompt: Prompt,
    caplog: pytest.LogCaptureFixture,
) -> None:
    result, status = case
    with caplog.at_level(logging.DEBUG):
        assessment, _ = run(result, canary_tx, settings, canary_prompt)

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


@pytest.mark.parametrize("case", NOT_VALID.values(), ids=NOT_VALID.keys())
def test_transaction_id_is_a_structured_field_only(
    case: tuple[Any, ValidationStatus],
    canary_tx: Transaction,
    settings: Settings,
    canary_prompt: Prompt,
    caplog: pytest.LogCaptureFixture,
) -> None:
    result, _ = case
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        run(result, canary_tx, settings, canary_prompt)
    (record,) = [r for r in caplog.records if r.name == LOGGER]
    tx_id = canary_tx.transaction_id

    assert getattr(record, "transaction_id") == tx_id  # noqa: B009
    assert tx_id not in record.getMessage()
    assert tx_id not in repr(record.args)
    # The only transaction field in LOG_FIELDS is the id itself.
    transaction_fields = set(Transaction.model_fields) & set(LOG_FIELDS)
    assert transaction_fields == {"transaction_id"}


def test_valid_outcome_logs_no_warning(
    canary_tx: Transaction,
    settings: Settings,
    canary_prompt: Prompt,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        assessment, _ = run(make_message(RAW_PAYLOAD), canary_tx, settings, canary_prompt)
    assert assessment.validation_status is ValidationStatus.VALID
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert_no_canaries(caplog.records)


def test_the_canaries_really_reach_the_assessor(
    canary_tx: Transaction, settings: Settings, canary_prompt: Prompt
) -> None:
    """Guard against a vacuous test: each canary is present where the assessor sees it."""
    assessment, client = run(NOT_VALID["truncated"][0], canary_tx, settings, canary_prompt)
    assert assessment.raw_output is not None and RAW_CANARY in assessment.raw_output
    (request,) = client.requests
    assert PROMPT_CANARY in request.system
    assert MEMO_CANARY in request.user_content

    status_exc = NOT_VALID["api_error_status"][0]
    assert STATUS_MESSAGE_CANARY in str(status_exc)
    assert STATUS_BODY_CANARY in json.dumps(status_exc.body)
    assert STATUS_BODY_CANARY in status_exc.response.text
    assert CONNECTION_CANARY in str(NOT_VALID["api_error_connection"][0])
