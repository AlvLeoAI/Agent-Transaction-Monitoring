import pytest
from pydantic import ValidationError

from agent_transaction_monitoring.settings import DEFAULT_MODEL, MODEL_PRICING, Settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.model == DEFAULT_MODEL == "claude-haiku-5-5"
    assert settings.effort == "low"
    assert settings.prompt_version == "v1"


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATM_EFFORT", "medium")
    monkeypatch.setenv("ATM_MAX_TOKENS", "4096")
    settings = Settings()
    assert (settings.effort, settings.max_tokens) == ("medium", 4096)


def test_unpriced_model_is_rejected_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATM_MODEL", "claude-unpriced")
    with pytest.raises(ValidationError, match="no pricing"):
        Settings()


def test_every_priced_model_has_integer_prices() -> None:
    for pricing in MODEL_PRICING.values():
        for card in (pricing.standard, pricing.long_context):
            assert all(isinstance(v, int) for v in card.model_dump().values())


def test_settings_are_frozen() -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.model = "other"  # type: ignore[misc]
