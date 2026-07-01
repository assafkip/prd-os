"""Structural checks on the slash-command markdown files.

The command files are prompt glue; their behavior is covered by the
underlying script tests. These checks just catch typos and missing frontmatter
so a stale or renamed script reference is obvious without running the
plugin live.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_DIR = PLUGIN_ROOT / "commands"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
TEMPLATES_DIR = PLUGIN_ROOT / "templates"

EXPECTED = {
    "prd-os-init.md": ["prd_os_init.py"],
    "prd-start.md": ["prd_runner.py"],
    "prd-review.md": ["prd_runner.py", "findings_writer.py"],
    "prd-triage.md": ["prd_runner.py", "findings_writer.py"],
    "prd-approve.md": ["prd_runner.py"],
    "prd-archive.md": ["prd_runner.py"],
    "prd-split.md": ["prd_split.py"],
    "prd-map.md": ["prd_map_runner.py"],
    "issue-start.md": ["issue_runner.py"],
    "issue-approve.md": ["issue_runner.py"],
    "issue-verify.md": ["issue_runner.py"],
    "issue-review.md": ["issue_runner.py", "issue_findings.py"],
    "issue-amend.md": ["issue_runner.py"],
    "issue-closeout.md": ["issue_runner.py", "issue_findings.py"],
}

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_command_file_exists(name):
    assert (COMMANDS_DIR / name).is_file(), f"missing command file: {name}"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_command_has_frontmatter_with_description(name):
    text = (COMMANDS_DIR / name).read_text()
    m = FRONTMATTER_RE.match(text)
    assert m, f"{name}: missing YAML frontmatter"
    fm = m.group(1)
    assert re.search(r"^description:\s*\S", fm, re.MULTILINE), (
        f"{name}: frontmatter missing description"
    )


@pytest.mark.parametrize("name,scripts", sorted(EXPECTED.items()))
def test_command_references_existing_scripts(name, scripts):
    text = (COMMANDS_DIR / name).read_text()
    for script in scripts:
        assert script in text, f"{name}: expected reference to {script}"
        assert (SCRIPTS_DIR / script).is_file(), (
            f"{name}: references {script} but it does not exist"
        )


def test_no_unexpected_command_files():
    actual = {p.name for p in COMMANDS_DIR.glob("*.md")}
    extra = actual - set(EXPECTED)
    assert not extra, f"unexpected command files present: {sorted(extra)}"


# --- PRD template / rubric section guards (prd-template-design-doc-sections) ---

REQUIRED_TEMPLATE_HEADERS = [
    "## Alternatives considered",
    "## Scenarios",
    "## Resolved decisions",
]


@pytest.mark.parametrize("header", REQUIRED_TEMPLATE_HEADERS)
def test_prd_template_has_design_doc_headers(header):
    text = (TEMPLATES_DIR / "prd.md").read_text()
    assert header in text, f"prd.md template missing required section: {header}"


def test_review_rubric_has_penalty_heuristic():
    text = (TEMPLATES_DIR / "review-rubric.md").read_text().lower()
    assert "penalty for being wrong" in text, (
        "review-rubric.md missing the penalty-of-being-wrong severity heuristic"
    )
