"""The isolation scans catch each known bypass (audit finding A2).

A throwaway package is written to a temporary directory with one module per bypass. Each
test runs the same check the real isolation tests run and asserts it reports the bypass,
so a regression in the scanner fails here even while the real package is clean.
"""

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from isolation_scan import forbidden_imports, modules, relative_name, scan_signatures

TYPES = (
    "from agent_transaction_monitoring.assessment import ModelAssessment\n"
    "from agent_transaction_monitoring.domain import AlertDecision\n"
)

THROWAWAY_MODULES = {
    # The "model side" of the throwaway package.
    "assessment.py": "VALUE = 1\n",
    # Relative import of a whole module: `from . import x`.
    "bare_relative.py": "from . import assessment\n",
    # Relative import of a name from a sibling module.
    "named_relative.py": "from .assessment import VALUE\n",
    # Relative import from a parent package, two levels up.
    "sub/parent_relative.py": "from ..assessment import VALUE\n",
    # A conversion hidden behind staticmethod, classmethod and property.
    "wrapped.py": TYPES
    + "class Converter:\n"
    + "    @staticmethod\n"
    + "    def by_static(a: ModelAssessment) -> AlertDecision: ...\n"
    + "    @classmethod\n"
    + "    def by_class(cls, a: ModelAssessment) -> AlertDecision: ...\n"
    + "    @property\n"
    + "    def by_property(self) -> AlertDecision: ...\n"
    + "    def plain(self, a: ModelAssessment) -> AlertDecision: ...\n",
    # A conversion in a subpackage that a flat module listing never reaches.
    "sub/__init__.py": "",
    "sub/deeper/__init__.py": "",
    "sub/deeper/hidden.py": TYPES + "def convert(a: ModelAssessment) -> AlertDecision: ...\n",
}


@pytest.fixture
def throwaway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    package = f"throwaway_{uuid.uuid4().hex}"
    root = tmp_path / package
    root.mkdir()
    (root / "__init__.py").write_text("")
    for relative, source in THROWAWAY_MODULES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield package
    for name in [m for m in sys.modules if m == package or m.startswith(f"{package}.")]:
        del sys.modules[name]


def by_relative_name(package: str) -> dict[str, object]:
    return {relative_name(package, m.name): m for m in modules(package)}


def test_walk_reaches_nested_subpackages(throwaway: str) -> None:
    found = by_relative_name(throwaway)
    assert {"sub", "sub.deeper", "sub.deeper.hidden", "sub.parent_relative"} <= set(found)


@pytest.mark.parametrize("module", ["bare_relative", "named_relative", "sub.parent_relative"])
def test_relative_imports_of_the_model_side_are_caught(throwaway: str, module: str) -> None:
    source = {relative_name(throwaway, m.name): m for m in modules(throwaway)}[module]
    assert forbidden_imports(source, [f"{throwaway}.assessment"]) != set()


@pytest.mark.parametrize(
    "qualname",
    [
        "wrapped.Converter.by_static",
        "wrapped.Converter.by_class",
        "wrapped.Converter.plain",
        "sub.deeper.hidden.convert",
    ],
)
def test_hidden_conversions_are_caught(throwaway: str, qualname: str) -> None:
    assert qualname in scan_signatures(throwaway).converts


def test_property_returning_a_decision_is_seen(throwaway: str) -> None:
    assert "wrapped.Converter.by_property" in scan_signatures(throwaway).returns_decision
