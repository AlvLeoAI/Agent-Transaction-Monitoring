"""Docs stay in step with the code and CI (audit findings A7 and A10)."""

import re
from pathlib import Path

import pytest

from agent_transaction_monitoring.assessment import LOG_FIELDS

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "src" / "agent_transaction_monitoring"
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
CI = ROOT / ".github" / "workflows" / "ci.yml"

# Backticked module references in headings: `name.py` or `name/`.
MODULE_REF = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.py|/))`")


def headings(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line.startswith("#")]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_headings_name_only_modules_that_exist(doc: Path) -> None:
    for heading in headings(doc):
        for ref in MODULE_REF.findall(heading):
            assert (PACKAGE_DIR / ref.rstrip("/")).exists(), f"{doc.name}: {heading!r}"


def ci_commands() -> list[str]:
    """Single-line `run:` commands, plus the script each multi-line step runs."""
    text = CI.read_text()
    single = re.findall(r"^\s+run: (?!\|)(.+)$", text, flags=re.MULTILINE)
    scripts = re.findall(r"\b(scripts/[\w./-]+\.py)\b", text)
    return [*single, *scripts]


def readme_quick_start() -> str:
    text = (ROOT / "README.md").read_text()
    section = text[text.index("## Quick start") :]
    return section[: section.index("\n## ", 1)] if "\n## " in section[1:] else section


def test_ci_has_the_expected_commands() -> None:
    commands = ci_commands()
    assert "uv lock --check" in commands
    assert "scripts/check_wheel_prompts.py" in commands


@pytest.mark.parametrize("command", ci_commands())
def test_readme_check_list_covers_every_ci_command(command: str) -> None:
    assert command in readme_quick_start(), command


def test_architecture_logging_section_lists_exactly_the_log_fields() -> None:
    text = (ROOT / "docs" / "architecture.md").read_text()
    section = text[text.index("### Logging") :]
    section = section[: section.index("\n#", 1)]
    paragraphs = section.split("\n\n")
    field_list = next(p for p in paragraphs if p.startswith("`assessment_id`"))
    assert re.findall(r"`(\w+)`", field_list) == list(LOG_FIELDS)
    assert "`transaction_id` is the only transaction field allowed in logs" in section
