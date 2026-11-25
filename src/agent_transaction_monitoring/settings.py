"""Runtime configuration and the model pricing table.

Settings come from environment variables prefixed ``ATM_`` (no ``.env`` file is read).
The Anthropic API key is not part of these settings: the SDK reads it from the
environment itself, so it never passes through application code.
"""

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_transaction_monitoring.domain import FrozenModel

DEFAULT_MODEL = "claude-haiku-5-5"
# src/agent_transaction_monitoring/settings.py -> repository root /prompts
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

Effort = Literal["low", "medium", "high"]
TOKENS_PER_PRICE_UNIT = 1_000_000  # prices are quoted per million tokens

NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class TokenPrices(FrozenModel):
    """Prices in integer micro-USD per million tokens (1 USD == 1_000_000 micro-USD)."""

    input: NonNegativeInt
    output: NonNegativeInt
    cache_write_5m: NonNegativeInt
    cache_read: NonNegativeInt


class ModelPricing(FrozenModel):
    """A model's rate cards. Prompts longer than the threshold use the long-context card."""

    standard: TokenPrices
    long_context: TokenPrices
    long_context_threshold_tokens: NonNegativeInt


# Source: https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-11-25;
# the page shows no "last updated" date). First-party API, standard tier, no data-residency
# multiplier. Claude Haiku 5.5 has two rows on that page: "for prompts up to 100,000 tokens"
# and "for prompts over 100,000 tokens", the source of the long-context card below. The page
# counts all input tokens toward that threshold, cache reads and cache writes included, which
# is what TokenUsage.prompt_tokens computes. Cache reads are 0.1x input, 5-minute cache
# writes 1.25x input. Update together with the model default; an unpriced model is rejected.
MODEL_PRICING: Mapping[str, ModelPricing] = MappingProxyType(
    {
        "claude-haiku-5-5": ModelPricing(
            standard=TokenPrices(
                input=100_000, output=500_000, cache_write_5m=125_000, cache_read=10_000
            ),
            long_context=TokenPrices(
                input=500_000, output=2_500_000, cache_write_5m=625_000, cache_read=50_000
            ),
            long_context_threshold_tokens=100_000,
        ),
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATM_", frozen=True, extra="forbid")

    model: str = DEFAULT_MODEL
    # Thinking tokens count toward this cap; leave room beyond the short JSON answer.
    max_tokens: Annotated[int, Field(gt=0, le=16_000)] = 2048
    effort: Effort = "low"
    request_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 60.0
    max_retries: Annotated[int, Field(ge=0, le=5)] = 2
    prompt_version: Annotated[str, Field(pattern=r"^v[0-9]+$")] = "v1"
    prompts_dir: Path = DEFAULT_PROMPTS_DIR

    @model_validator(mode="after")
    def _model_is_priced(self) -> Self:
        if self.model not in MODEL_PRICING:
            raise ValueError(f"no pricing for model {self.model!r}; add it to MODEL_PRICING")
        return self
