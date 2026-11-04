"""Shared fixtures. Tests run offline: any socket connection attempt fails the test."""

import socket
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from agent_transaction_monitoring.domain import AgentIdentity, Mandate, Transaction

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load[T](model: type[T], filename: str) -> list[T]:
    raw = (FIXTURES_DIR / filename).read_bytes()
    return TypeAdapter(list[model]).validate_json(raw)  # type: ignore[valid-type]


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


@pytest.fixture
def agents() -> list[AgentIdentity]:
    return _load(AgentIdentity, "agents.json")


@pytest.fixture
def mandate() -> Mandate:
    (only,) = _load(Mandate, "mandates.json")
    return only


@pytest.fixture
def normal_transactions() -> list[Transaction]:
    return _load(Transaction, "transactions_normal.json")


@pytest.fixture
def out_of_mandate_transactions() -> list[Transaction]:
    return _load(Transaction, "transactions_out_of_mandate.json")


@pytest.fixture
def structuring_transactions() -> list[Transaction]:
    return _load(Transaction, "transactions_structuring.json")
