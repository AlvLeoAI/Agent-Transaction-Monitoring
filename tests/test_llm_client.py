"""The adapter, exercised through the real SDK with an in-memory HTTP transport.

This pins down the request body that actually leaves the process, which is what the
audit record claims was sent. No socket is opened.
"""

import json
from typing import Any

import anthropic
import httpx2
import pytest

from agent_transaction_monitoring.assessment import (
    OUTPUT_JSON_SCHEMA,
    ModelAssessment,
    TransactionAssessor,
)
from agent_transaction_monitoring.domain import Transaction
from agent_transaction_monitoring.llm_client import (
    AnthropicMessagesClient,
    build_messages_client,
)
from agent_transaction_monitoring.prompts import Prompt
from agent_transaction_monitoring.settings import Settings

from support import VALID_OUTPUT, fixed_clock

RESPONSE_BODY: dict[str, Any] = {
    "id": "msg_wire",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-5-5",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": json.dumps(VALID_OUTPUT)},
    ],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "stop_details": None,
    "usage": {"input_tokens": 900, "output_tokens": 120},
}


class Recorder:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = RESPONSE_BODY if body is None else body
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body, headers={"request-id": "req_wire_0001"})


def wire_assessor(recorder: Recorder, settings: Settings, prompt: Prompt) -> TransactionAssessor:
    sdk = anthropic.Anthropic(
        api_key="test-key-not-real",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(recorder)),
    )
    return TransactionAssessor(AnthropicMessagesClient(sdk), settings, prompt, fixed_clock)


@pytest.fixture
def wire(
    settings: Settings, classifier_prompt: Prompt, injection_transaction: Transaction
) -> tuple[dict[str, Any], ModelAssessment]:
    """The JSON body that left the process, and the assessment recorded for it."""
    recorder = Recorder()
    assessment = wire_assessor(recorder, settings, classifier_prompt).assess(
        injection_transaction, [], []
    )
    assert assessment.validation_status == "valid"
    assert assessment.request_id == "req_wire_0001"
    (request,) = recorder.requests
    assert request.url.path == "/v1/messages"
    body: dict[str, Any] = json.loads(request.content)
    return body, assessment


@pytest.fixture
def sent(wire: tuple[dict[str, Any], ModelAssessment]) -> dict[str, Any]:
    return wire[0]


def test_sampling_parameters_are_absent_and_recorded_as_null(
    wire: tuple[dict[str, Any], ModelAssessment],
) -> None:
    body, assessment = wire
    params = assessment.request_parameters
    for key in ("temperature", "top_p", "top_k"):
        assert key not in body
        assert getattr(params, key) is None


def test_recorded_thinking_and_effort_are_what_was_sent(
    wire: tuple[dict[str, Any], ModelAssessment],
) -> None:
    body, assessment = wire
    params = assessment.request_parameters
    assert body["thinking"] == params.thinking.model_dump() == {"type": "adaptive"}
    assert body["output_config"]["effort"] == params.effort == "low"
    assert body["max_tokens"] == params.max_tokens


def test_body_matches_settings_and_schema(sent: dict[str, Any], settings: Settings) -> None:
    assert sent["model"] == settings.model
    assert sent["max_tokens"] == settings.max_tokens
    assert sent["output_config"] == {
        "effort": settings.effort,
        "format": {"type": "json_schema", "schema": OUTPUT_JSON_SCHEMA},
    }
    assert "stream" not in sent or sent["stream"] is False


def test_single_user_turn_and_no_prefill(sent: dict[str, Any], classifier_prompt: Prompt) -> None:
    assert sent["system"] == classifier_prompt.text
    assert [m["role"] for m in sent["messages"]] == ["user"]
    assert "<transaction_memo>" in sent["messages"][0]["content"]


def test_http_error_becomes_api_error_record(
    settings: Settings, classifier_prompt: Prompt, injection_transaction: Transaction
) -> None:
    recorder = Recorder(
        status=400,
        body={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}},
    )
    assessment = wire_assessor(recorder, settings, classifier_prompt).assess(
        injection_transaction, [], []
    )
    assert assessment.validation_status == "api_error"
    assert assessment.error_type == "BadRequestError"
    assert assessment.error_status_code == 400
    assert assessment.request_id == "req_wire_0001"


def test_startup_builds_one_configured_client(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    client = build_messages_client(settings)
    assert isinstance(client, AnthropicMessagesClient)
    assert client._client.max_retries == settings.max_retries
    assert client._client.timeout == settings.request_timeout_seconds
