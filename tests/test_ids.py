"""Derived ids: readable and hashed forms live in disjoint namespaces (audit finding A1)."""

import hashlib

import pytest

from agent_transaction_monitoring.assessment import assessment_id_for
from agent_transaction_monitoring.domain import (
    HASHED_KEY_MARKER,
    IDENTIFIER_MAX_LENGTH,
    derived_id,
)
from agent_transaction_monitoring.engine import alert_id_for
from agent_transaction_monitoring.rules import Finding, RuleId

LONG_KEY = "k" * IDENTIFIER_MAX_LENGTH
# A valid transaction id that spells out exactly what hashing LONG_KEY produces.
LOOKALIKE_KEY = HASHED_KEY_MARKER + hashlib.sha256(LONG_KEY.encode()).hexdigest()


def alert_id(rule: RuleId, transaction_id: str) -> str:
    return alert_id_for(Finding(rule_id=rule, transaction_ids=(transaction_id,), detail="x"))


@pytest.mark.parametrize("rule", list(RuleId))
def test_lookalike_key_never_collides_for_any_rule(rule: RuleId) -> None:
    assert alert_id(rule, LOOKALIKE_KEY) != alert_id(rule, LONG_KEY)


@pytest.mark.parametrize("version", ["v1", "v2", "v10"])
def test_lookalike_key_never_collides_for_assessments(version: str) -> None:
    assert assessment_id_for(version, LOOKALIKE_KEY) != assessment_id_for(version, LONG_KEY)


def test_keys_with_the_marker_always_take_the_hashed_form() -> None:
    key = "sha256-short"
    digest = hashlib.sha256(key.encode()).hexdigest()
    assert derived_id("alert:no_mandate", key) == f"alert:no_mandate:sha256-{digest}"


def test_readable_form_is_kept_for_ordinary_keys() -> None:
    assert derived_id("alert:no_mandate", "tx-1") == "alert:no_mandate:tx-1"
    assert derived_id("alert:no_mandate", "tx-sha256-1") == "alert:no_mandate:tx-sha256-1"
