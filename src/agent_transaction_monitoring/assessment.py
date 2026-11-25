"""Model assessment: advisory evidence from Claude, stored as an immutable audit record.

The flow for one transaction:

1. Build a request from the transaction, the rule findings and the versioned system prompt.
   Untrusted text goes inside XML data tags, escaped so it cannot close its own tag.
2. Call the injected ``MessagesClient`` once, non-streaming.
3. Classify the result: only ``stop_reason == "end_turn"`` with a schema-valid output is
   ``valid``. Everything else is stored with its status and never used.
4. Return a ``ModelAssessment``. Every outcome produces one, including API errors.

This module never imports the rule engine's decision code and never refers to
``AlertDecision``: an assessment is evidence for reviewers, not an input to a decision.
"""

import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from enum import StrEnum
from html import escape
from types import MappingProxyType
from typing import Annotated, Any, Final, Self

import anthropic
from anthropic.types import Message
from pydantic import (
    AwareDatetime,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
    model_validator,
)

from agent_transaction_monitoring.domain import (
    FrozenModel,
    Identifier,
    Transaction,
    atomic_units_per_unit,
    currency_decimals,
    derived_id,
)
from agent_transaction_monitoring.llm_client import (
    ADAPTIVE_THINKING,
    ClassifierRequest,
    MessagesClient,
    ThinkingSetting,
)
from agent_transaction_monitoring.prompts import Prompt, Sha256Hex, sha256_hex
from agent_transaction_monitoring.settings import (
    MODEL_PRICING,
    TOKENS_PER_PRICE_UNIT,
    Effort,
    Settings,
)

logger = logging.getLogger(__name__)

# --- What the model returns -------------------------------------------------------------

RATIONALE_MAX_LENGTH: Final = 600
TYPOLOGIES_MAX_ITEMS: Final = 6


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Typology(StrEnum):
    """Fixed vocabulary of patterns. Meanings are spelled out in the system prompt."""

    STRUCTURING = "structuring"
    VELOCITY = "velocity"
    OVERSPEND = "overspend"
    MISDIRECTION = "misdirection"
    LAYERING = "layering"
    OTHER = "other"


class ModelOutput(FrozenModel):
    """The model's answer after our own validation.

    The JSON schema sent to the API only fixes structure and enum values; the length and
    uniqueness limits here are enforced after parsing, because structured outputs do not
    support those constraints.
    """

    risk_level: RiskLevel
    typologies: Annotated[tuple[Typology, ...], Field(max_length=TYPOLOGIES_MAX_ITEMS)]
    rationale: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=RATIONALE_MAX_LENGTH),
    ]
    memo_influence_suspected: StrictBool

    @model_validator(mode="after")
    def _unique_typologies(self) -> Self:
        if len(set(self.typologies)) != len(self.typologies):
            raise ValueError("typologies must not repeat")
        return self


ENUM_FIELDS: Final = MappingProxyType(
    {"risk_level": frozenset(RiskLevel), "typologies": frozenset(Typology)}
)

# Sent as output_config.format. Every field required, no optionals, no unions.
OUTPUT_JSON_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "risk_level": {"type": "string", "enum": [v.value for v in RiskLevel]},
        "typologies": {
            "type": "array",
            "items": {"type": "string", "enum": [v.value for v in Typology]},
        },
        "rationale": {"type": "string"},
        "memo_influence_suspected": {"type": "boolean"},
    },
    "required": ["risk_level", "typologies", "rationale", "memo_influence_suspected"],
    "additionalProperties": False,
}


# --- The audit record -------------------------------------------------------------------


class ValidationStatus(StrEnum):
    VALID = "valid"
    SCHEMA_INVALID = "schema_invalid"
    REFUSED = "refused"
    TRUNCATED = "truncated"
    UNEXPECTED_STOP_REASON = "unexpected_stop_reason"
    API_ERROR = "api_error"


NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class RequestParameters(FrozenModel):
    """What the request actually carried, copied from the ``ClassifierRequest`` that was sent.

    ``thinking`` and ``effort`` are the values sent in ``thinking`` and
    ``output_config.effort``. ``temperature``, ``top_p`` and ``top_k`` are ``None`` because
    they were not sent: the configured model rejects non-default values and the SDK no
    longer exposes them.
    """

    max_tokens: NonNegativeInt
    effort: Effort
    thinking: ThinkingSetting
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None


class TokenUsage(FrozenModel):
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_creation_input_tokens: NonNegativeInt
    cache_read_input_tokens: NonNegativeInt

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


class CaseNormalization(FrozenModel):
    """One enum value whose case we changed, e.g. ``"High"`` -> ``"high"``."""

    field: str
    raw_value: str
    normalized_value: str


class ModelAssessment(FrozenModel):
    """Immutable audit record of one model call. Evidence only; never a decision."""

    assessment_id: Identifier
    transaction_id: Identifier
    alert_ids: tuple[Identifier, ...]
    created_at: AwareDatetime

    requested_model: str
    model: str | None = Field(description="response.model, i.e. what actually served it")
    request_id: str | None
    prompt_version: str
    prompt_sha256: Sha256Hex
    request_parameters: RequestParameters

    stop_reason: str | None
    refusal_category: str | None
    usage: TokenUsage | None
    raw_output: str | None
    raw_output_sha256: Sha256Hex | None

    validation_status: ValidationStatus
    validation_errors: tuple[str, ...]
    case_normalizations: tuple[CaseNormalization, ...]
    assessment: ModelOutput | None

    error_type: str | None
    error_status_code: int | None

    cost_micro_usd: NonNegativeInt
    priced_as: str | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        valid = self.validation_status is ValidationStatus.VALID
        if valid != (self.assessment is not None):
            raise ValueError("assessment is present if and only if validation_status is valid")
        if (self.raw_output is None) != (self.raw_output_sha256 is None):
            raise ValueError("raw_output and raw_output_sha256 go together")
        if self.raw_output is not None and self.raw_output_sha256 != sha256_hex(
            self.raw_output.encode("utf-8")
        ):
            raise ValueError("raw_output_sha256 does not match raw_output")
        api_error = self.validation_status is ValidationStatus.API_ERROR
        if api_error != (self.error_type is not None):
            raise ValueError("error_type is set if and only if validation_status is api_error")
        if api_error and (self.usage is not None or self.cost_micro_usd != 0):
            raise ValueError("an api_error has no usage and no cost")
        return self


# --- Pure helpers -----------------------------------------------------------------------


def cost_micro_usd(model: str, usage: TokenUsage) -> int:
    """Integer cost, rounded up to the next micro-USD. Raises ``KeyError`` if unpriced."""
    pricing = MODEL_PRICING[model]
    card = (
        pricing.long_context
        if usage.prompt_tokens > pricing.long_context_threshold_tokens
        else pricing.standard
    )
    numerator = (
        usage.input_tokens * card.input
        + usage.output_tokens * card.output
        + usage.cache_creation_input_tokens * card.cache_write_5m
        + usage.cache_read_input_tokens * card.cache_read
    )
    return -(-numerator // TOKENS_PER_PRICE_UNIT)


def normalize_enum_case(data: object) -> tuple[object, tuple[CaseNormalization, ...]]:
    """Lower-case enum values that differ from a known value only by case.

    Anything else is left untouched for schema validation to reject. Only applied to the
    known enum fields; every change is returned so the audit record can show it.
    """
    if not isinstance(data, dict):
        return data, ()
    out = dict(data)
    changes: list[CaseNormalization] = []

    def fix(path: str, value: object, allowed: frozenset[str]) -> object:
        if isinstance(value, str) and value not in allowed and value.lower() in allowed:
            changes.append(
                CaseNormalization(field=path, raw_value=value, normalized_value=value.lower())
            )
            return value.lower()
        return value

    if "risk_level" in out:
        out["risk_level"] = fix("risk_level", out["risk_level"], ENUM_FIELDS["risk_level"])
    if isinstance(out.get("typologies"), list):
        out["typologies"] = [
            fix(f"typologies[{i}]", v, ENUM_FIELDS["typologies"])
            for i, v in enumerate(out["typologies"])
        ]
    return out, tuple(changes)


def parse_output(
    raw: str,
) -> tuple[ModelOutput | None, tuple[str, ...], tuple[CaseNormalization, ...]]:
    """Parse and validate raw model text. Returns (output, errors, normalizations)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, (f"invalid JSON: {exc.msg} at position {exc.pos}",), ()
    normalized, changes = normalize_enum_case(data)
    try:
        # JSON mode so strict validation accepts enum strings and arrays.
        return ModelOutput.model_validate_json(json.dumps(normalized)), (), changes
    except ValidationError as exc:
        errors = tuple(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        return None, errors, changes


def format_amount(amount: int, currency: str) -> str:
    """Exact decimal rendering from integer atomic units, e.g. 990000000 USDC -> 990.000000."""
    unit = atomic_units_per_unit(currency)
    whole, frac = divmod(amount, unit)
    return f"{whole}.{frac:0{currency_decimals(currency)}d}"


def build_user_message(tx: Transaction, findings: Sequence[tuple[str, str]]) -> str:
    """Render transaction data inside XML tags. All values are escaped.

    ``findings`` is ``(rule_id, detail)`` pairs from the rule engine. The memo is escaped
    too, so text such as ``</transaction_memo>`` inside it stays data.
    """
    finding_lines = "\n".join(
        f'<finding rule_id="{escape(rule_id)}">{escape(detail)}</finding>'
        for rule_id, detail in findings
    )
    return (
        "<transaction>\n"
        f"<transaction_id>{escape(tx.transaction_id)}</transaction_id>\n"
        f"<agent_id>{escape(tx.agent_id)}</agent_id>\n"
        f"<mandate_id>{escape(tx.mandate_id or '')}</mandate_id>\n"
        f"<counterparty_id>{escape(tx.counterparty_id)}</counterparty_id>\n"
        f"<amount>{format_amount(tx.amount, tx.currency)}</amount>\n"
        f"<currency>{escape(tx.currency)}</currency>\n"
        f"<initiated_at>{tx.initiated_at.isoformat()}</initiated_at>\n"
        "</transaction>\n"
        f"<rule_findings>\n{finding_lines}\n</rule_findings>\n"
        f"<transaction_memo>{escape(tx.memo or '')}</transaction_memo>\n\n"
        "Assess the transaction above and return the assessment object."
    )


def _raw_text(message: Message) -> str:
    # Responses can start with thinking blocks; read content by type, never by position.
    return "".join(block.text for block in message.content if block.type == "text")


def _usage(message: Message) -> TokenUsage:
    u = message.usage
    return TokenUsage(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
        cache_read_input_tokens=u.cache_read_input_tokens or 0,
    )


# --- The assessor -----------------------------------------------------------------------


class TransactionAssessor:
    """Produces one ``ModelAssessment`` per call. Holds an injected client, never creates one."""

    def __init__(
        self,
        client: MessagesClient,
        settings: Settings,
        prompt: Prompt,
        clock: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._settings = settings
        self._prompt = prompt
        self._clock = clock

    @staticmethod
    def request_parameters(request: ClassifierRequest) -> RequestParameters:
        return RequestParameters(
            max_tokens=request.max_tokens,
            effort=request.effort,
            thinking=request.thinking,
        )

    def build_request(
        self, tx: Transaction, findings: Sequence[tuple[str, str]]
    ) -> ClassifierRequest:
        return ClassifierRequest(
            model=self._settings.model,
            max_tokens=self._settings.max_tokens,
            effort=self._settings.effort,
            thinking=ADAPTIVE_THINKING,
            system=self._prompt.text,
            user_content=build_user_message(tx, findings),
            output_schema=OUTPUT_JSON_SCHEMA,
        )

    def assess(
        self,
        tx: Transaction,
        findings: Sequence[tuple[str, str]],
        alert_ids: Sequence[str],
    ) -> ModelAssessment:
        common: dict[str, Any] = {
            "assessment_id": derived_id(f"assessment:{self._prompt.version}", tx.transaction_id),
            "transaction_id": tx.transaction_id,
            "alert_ids": tuple(alert_ids),
            "created_at": self._clock(),
            "requested_model": self._settings.model,
            "prompt_version": self._prompt.version,
            "prompt_sha256": self._prompt.sha256,
        }
        request = self.build_request(tx, findings)
        common["request_parameters"] = self.request_parameters(request)
        try:
            message = self._client.create(request)
        except anthropic.APIStatusError as exc:
            return self._api_error(common, exc, exc.status_code, exc.request_id)
        except anthropic.APIError as exc:
            # Connection errors and timeouts, after the SDK's own retries.
            return self._api_error(common, exc, None, None)
        return self._from_message(common, message)

    def _api_error(
        self,
        common: dict[str, Any],
        exc: anthropic.APIError,
        status_code: int | None,
        request_id: str | None,
    ) -> ModelAssessment:
        logger.warning(
            "model assessment api_error: %s status=%s request_id=%s",
            type(exc).__name__,
            status_code,
            request_id,
        )
        return ModelAssessment(
            **common,
            model=None,
            request_id=request_id,
            stop_reason=None,
            refusal_category=None,
            usage=None,
            raw_output=None,
            raw_output_sha256=None,
            validation_status=ValidationStatus.API_ERROR,
            validation_errors=(),
            case_normalizations=(),
            assessment=None,
            error_type=type(exc).__name__,
            error_status_code=status_code,
            cost_micro_usd=0,
            priced_as=None,
        )

    def _from_message(self, common: dict[str, Any], message: Message) -> ModelAssessment:
        raw = _raw_text(message)
        usage = _usage(message)
        stop_reason = message.stop_reason
        output: ModelOutput | None = None
        errors: tuple[str, ...] = ()
        changes: tuple[CaseNormalization, ...] = ()

        if stop_reason == "end_turn":
            output, errors, changes = parse_output(raw)
            status = ValidationStatus.VALID if output else ValidationStatus.SCHEMA_INVALID
        elif stop_reason == "refusal":
            status = ValidationStatus.REFUSED
        elif stop_reason == "max_tokens":
            status = ValidationStatus.TRUNCATED
        else:
            status = ValidationStatus.UNEXPECTED_STOP_REASON

        # Price by the model that served the request; fall back to the configured model.
        priced_as = message.model if message.model in MODEL_PRICING else self._settings.model
        refusal_category = (
            message.stop_details.category
            if stop_reason == "refusal" and message.stop_details is not None
            else None
        )
        if status is not ValidationStatus.VALID:
            logger.warning(
                "model assessment %s: stop_reason=%s request_id=%s errors=%d",
                status.value,
                stop_reason,
                message._request_id,
                len(errors),
            )
        return ModelAssessment(
            **common,
            model=message.model,
            request_id=message._request_id,
            stop_reason=stop_reason,
            refusal_category=refusal_category,
            usage=usage,
            raw_output=raw,
            raw_output_sha256=sha256_hex(raw.encode("utf-8")),
            validation_status=status,
            validation_errors=errors,
            case_normalizations=changes,
            assessment=output,
            error_type=None,
            error_status_code=None,
            cost_micro_usd=cost_micro_usd(priced_as, usage),
            priced_as=priced_as,
        )
