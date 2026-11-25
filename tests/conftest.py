"""Shared fixtures. Tests run offline: any socket connection attempt fails the test.

The only exception is a test marked ``live``, which is skipped unless explicitly enabled
(see tests/test_live_smoke.py) and never runs in CI.
"""

import os
import socket
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from agent_transaction_monitoring.domain import AgentIdentity, Mandate, Transaction
from agent_transaction_monitoring.prompts import Prompt, load_classifier_prompt
from agent_transaction_monitoring.settings import Settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load[T](adapter: TypeAdapter[list[T]], filename: str) -> list[T]:
    return adapter.validate_json((FIXTURES_DIR / filename).read_bytes())


@pytest.fixture(autouse=True)
def _block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live") is not None:
        return

    def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


@pytest.fixture
def agents() -> list[AgentIdentity]:
    return _load(TypeAdapter(list[AgentIdentity]), "agents.json")


@pytest.fixture
def mandate() -> Mandate:
    (only,) = _load(TypeAdapter(list[Mandate]), "mandates.json")
    return only


@pytest.fixture
def normal_transactions() -> list[Transaction]:
    return _load(TypeAdapter(list[Transaction]), "transactions_normal.json")


@pytest.fixture
def out_of_mandate_transactions() -> list[Transaction]:
    return _load(TypeAdapter(list[Transaction]), "transactions_out_of_mandate.json")


@pytest.fixture
def structuring_transactions() -> list[Transaction]:
    return _load(TypeAdapter(list[Transaction]), "transactions_structuring.json")


@pytest.fixture(autouse=True)
def _isolate_settings(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline tests use the documented defaults, whatever ATM_* is set in the shell."""
    if request.node.get_closest_marker("live") is not None:
        return
    for name in list(os.environ):
        if name.startswith("ATM_"):
            monkeypatch.delenv(name)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def classifier_prompt(settings: Settings) -> Prompt:
    return load_classifier_prompt(settings.prompts_dir, settings.prompt_version)


@pytest.fixture
def injection_transaction(out_of_mandate_transactions: list[Transaction]) -> Transaction:
    (tx,) = [t for t in out_of_mandate_transactions if t.memo and "Ignore" in t.memo]
    return tx
