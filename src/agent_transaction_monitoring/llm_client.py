"""The one place that talks to the Anthropic API.

``MessagesClient`` is the seam: the assessment layer depends on that protocol, the
application creates one ``AnthropicMessagesClient`` at startup and injects it, and tests
inject a fake. There is no module-level client and no client per call.

Requests are non-streaming. If streaming is ever added, the audit record must be built
only from the final assembled message, never from partial events.
"""

from collections.abc import Mapping
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types import Message, ThinkingConfigAdaptiveParam

from agent_transaction_monitoring.domain import FrozenModel
from agent_transaction_monitoring.settings import Effort, Settings


class ThinkingSetting(FrozenModel):
    """The ``thinking`` request field, as sent and as recorded on the assessment."""

    type: Literal["adaptive"]

    def to_param(self) -> ThinkingConfigAdaptiveParam:
        return {"type": self.type}


# Claude Haiku 5.5 thinks adaptively by default; sending it explicitly keeps the request and
# the audit record in agreement instead of relying on a server-side default.
ADAPTIVE_THINKING = ThinkingSetting(type="adaptive")


class ClassifierRequest(FrozenModel):
    """Everything that varies between requests. Built by the assessment layer.

    The adapter sends these fields as they are, and the assessment records its request
    parameters from this same object, so the record cannot drift from what was sent.
    """

    model: str
    max_tokens: int
    effort: Effort
    thinking: ThinkingSetting
    system: str
    user_content: str
    output_schema: Mapping[str, Any]


class MessagesClient(Protocol):
    def create(self, request: ClassifierRequest) -> Message: ...


class AnthropicMessagesClient:
    """Adapter from ``ClassifierRequest`` to ``client.messages.create``.

    No ``temperature``, ``top_p`` or ``top_k``: Claude Haiku 5.5 rejects non-default
    sampling values, and SDK 2.x (API version 2026-10-21) no longer accepts them. No
    assistant prefill either: the last message is always the user turn, and the output
    shape comes from ``output_config.format``.
    """

    def __init__(self, client: anthropic.Anthropic) -> None:
        self._client = client

    def create(self, request: ClassifierRequest) -> Message:
        return self._client.messages.create(
            model=request.model,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=[{"role": "user", "content": request.user_content}],
            thinking=request.thinking.to_param(),
            output_config={
                "effort": request.effort,
                "format": {"type": "json_schema", "schema": dict(request.output_schema)},
            },
        )


def build_messages_client(settings: Settings) -> AnthropicMessagesClient:
    """Create the application's single client. Call once at startup."""
    return AnthropicMessagesClient(
        anthropic.Anthropic(
            timeout=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )
    )
