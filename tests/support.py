"""Test doubles for the Anthropic client. Nothing here touches the network."""

import json
from datetime import UTC, datetime
from typing import Any

import anthropic
import httpx2
from anthropic.types import Message

from agent_transaction_monitoring.assessment import TransactionAssessor
from agent_transaction_monitoring.llm_client import ClassifierRequest
from agent_transaction_monitoring.prompts import Prompt
from agent_transaction_monitoring.settings import Settings

FIXED_NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
SERVED_MODEL = "claude-haiku-5-5"
API_URL = "https://api.anthropic.com/v1/messages"

VALID_OUTPUT: dict[str, Any] = {
    "risk_level": "high",
    "typologies": ["misdirection"],
    "rationale": "Payment to a counterparty outside the mandate allow-list.",
    "memo_influence_suspected": True,
}


def fixed_clock() -> datetime:
    return FIXED_NOW


def make_message(
    text: str | None = None,
    *,
    stop_reason: str | None = "end_turn",
    model: str = SERVED_MODEL,
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
    with_thinking: bool = False,
    refusal_category: str | None = None,
    request_id: str | None = "req_test_0001",
) -> Message:
    """Build a real SDK ``Message`` as the API would return it."""
    content: list[dict[str, Any]] = []
    if with_thinking:
        # Thinking text is omitted by default: empty string plus a signature.
        content.append({"type": "thinking", "thinking": "", "signature": "sig"})
    if text is not None:
        content.append({"type": "text", "text": text})
    stop_details = (
        {"type": "refusal", "category": refusal_category, "explanation": None}
        if stop_reason == "refusal"
        else None
    )
    message = Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": stop_details,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
            },
        }
    )
    message._request_id = request_id
    return message


def valid_message(**overrides: Any) -> Message:
    return make_message(json.dumps({**VALID_OUTPUT, **overrides}))


class FakeMessagesClient:
    """Implements ``MessagesClient``: returns a canned message or raises a canned error."""

    def __init__(self, result: Message | Exception) -> None:
        self._result = result
        self.requests: list[ClassifierRequest] = []

    def create(self, request: ClassifierRequest) -> Message:
        self.requests.append(request)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def status_error(
    status: int,
    request_id: str | None = "req_err_0001",
    *,
    message: str = "server error",
    body: dict[str, Any] | None = None,
) -> anthropic.APIStatusError:
    headers = {"request-id": request_id} if request_id else {}
    response = httpx2.Response(
        status, headers=headers, request=httpx2.Request("POST", API_URL), json=body or {}
    )
    return anthropic.InternalServerError(message, response=response, body=body)


def connection_error(message: str = "Connection error.") -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(message=message, request=httpx2.Request("POST", API_URL))


def make_assessor(
    client: FakeMessagesClient, settings: Settings, prompt: Prompt
) -> TransactionAssessor:
    return TransactionAssessor(client, settings, prompt, fixed_clock)
