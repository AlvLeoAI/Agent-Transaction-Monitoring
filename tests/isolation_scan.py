"""Static and type-level scans behind tests/test_decision_isolation.py.

Kept separate from the tests so the same scans can be pointed at a throwaway package that
contains each known bypass (tests/test_isolation_scan.py). A scan that misses a bypass
there would also miss it in the real package.

What the scans see:
- every module and subpackage, found with ``pkgutil.walk_packages``;
- absolute and relative imports, including ``from . import x``, resolved to full names;
- imported names under both their original name and any ``as`` alias;
- functions, methods, and the functions wrapped by ``staticmethod``, ``classmethod`` and
  ``property``.
"""

import ast
import importlib
import importlib.util
import inspect
import pkgutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_type_hints

DECISION_NAMES = frozenset({"AlertDecision", "AlertOutcome", "DecisionSource", "decide"})


@dataclass(frozen=True)
class ModuleSource:
    name: str  # fully qualified, e.g. "agent_transaction_monitoring.engine"
    path: Path
    is_package: bool

    @property
    def package(self) -> str:
        """The package that relative imports in this module are resolved against."""
        return self.name if self.is_package else self.name.rpartition(".")[0]

    def tree(self) -> ast.Module:
        return ast.parse(self.path.read_text())


def modules(package: str) -> list[ModuleSource]:
    """The package itself plus every module and subpackage below it."""
    root = importlib.import_module(package)
    assert root.__file__ is not None
    found = [ModuleSource(package, Path(root.__file__), True)]
    for info in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
        spec = importlib.util.find_spec(info.name)
        assert spec is not None and spec.origin is not None, info.name
        found.append(ModuleSource(info.name, Path(spec.origin), info.ispkg))
    return found


def relative_name(package: str, module: str) -> str:
    """``agent_transaction_monitoring.prompts.x`` -> ``prompts.x``; the root -> ``""``."""
    return module[len(package) + 1 :] if module != package else ""


def _resolve_from(module: ModuleSource, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    parts = module.package.split(".")
    base = ".".join(parts[: len(parts) - (node.level - 1)])
    return f"{base}.{node.module}" if node.module else base


def imports(module: ModuleSource) -> set[str]:
    """Fully qualified names this module imports, relative imports resolved.

    ``from x import y`` yields both ``x`` and ``x.y``, because ``y`` may be a submodule
    (``from . import assessment``) rather than an attribute.
    """
    found: set[str] = set()
    for node in ast.walk(module.tree()):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_from(module, node)
            found.add(target)
            found.update(f"{target}.{alias.name}" for alias in node.names)
    return found


def names(module: ModuleSource) -> set[str]:
    """Every identifier the module mentions.

    For an import alias both sides count: ``import AlertDecision as AD`` records
    ``AlertDecision`` and ``AD``, so renaming on import cannot hide a name.
    """
    found: set[str] = set()
    for node in ast.walk(module.tree()):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name)
            if node.asname:
                found.add(node.asname)
    return found


def decision_references(module: ModuleSource) -> set[str]:
    """Decision names the module mentions; the model side must have none."""
    return names(module) & DECISION_NAMES


def forbidden_imports(module: ModuleSource, forbidden: Iterable[str]) -> set[str]:
    """Imports of a forbidden module or anything inside it."""
    blocked = tuple(forbidden)
    return {
        name
        for name in imports(module)
        if any(name == f or name.startswith(f"{f}.") for f in blocked)
    }


def _unwrap(obj: Any) -> Any:
    if isinstance(obj, staticmethod | classmethod):
        return obj.__func__
    if isinstance(obj, property):
        return obj.fget
    return obj


def callables(package: str) -> list[tuple[str, Any]]:
    """Every function and method defined in the package, wrappers unwrapped."""
    found: list[tuple[str, Any]] = []
    for source in modules(package):
        module = importlib.import_module(source.name)
        prefix = relative_name(package, source.name) or package
        for name, obj in vars(module).items():
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            if inspect.isfunction(obj):
                found.append((f"{prefix}.{name}", obj))
            elif inspect.isclass(obj):
                for attr, member in vars(obj).items():
                    func = _unwrap(member)
                    if inspect.isfunction(func):
                        found.append((f"{prefix}.{name}.{attr}", func))
    return found


@dataclass(frozen=True)
class SignatureScan:
    takes_assessment: list[str]
    returns_decision: list[str]
    converts: list[str]  # take an assessment (or its output) and return a decision


def scan_signatures(package: str) -> SignatureScan:
    takes: list[str] = []
    returns: list[str] = []
    converts: list[str] = []
    for qualname, func in callables(package):
        hints = {k: repr(v) for k, v in get_type_hints(func).items()}
        ret = hints.pop("return", "")
        params = " ".join(hints.values())
        uses_assessment = "ModelAssessment" in params or "ModelOutput" in params
        if uses_assessment:
            takes.append(qualname)
        if "AlertDecision" in ret:
            returns.append(qualname)
            if uses_assessment:
                converts.append(qualname)
    return SignatureScan(takes, returns, converts)
