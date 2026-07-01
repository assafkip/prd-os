"""Tests for prd_map_runner.py.

Covers:
  - build produces schema-valid JSON + a markdown sibling.
  - Language accounting: file counts and extensions per language.
  - Manifest detection for python (pyproject.toml) and node (package.json).
  - Test framework detection reads manifest contents.
  - Entry points pick up main.py / index.ts at repo root, ignore tests/.
  - Dotenv names are recorded; dotenv contents are NEVER read.
  - Ignored dirs (node_modules, .venv, Reports/) do not contribute to counts.
  - TODO / FIXME markers are counted.
  - build without --force refuses to overwrite an existing map.
  - status reports exists=false when map is absent.
  - status reports freshness metrics when map exists.
  - status flags stale when age_days exceeds the threshold.
  - Schema valid: every produced map satisfies codebase_map.schema.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MAP_RUNNER = PLUGIN_ROOT / "scripts" / "prd_map_runner.py"
SCHEMA_PATH = PLUGIN_ROOT / "schemas" / "codebase_map.schema.json"


@pytest.fixture
def run_map_runner() -> Callable[..., subprocess.CompletedProcess]:
    def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(repo)
        return subprocess.run(
            [sys.executable, str(MAP_RUNNER), *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            env=env,
        )

    return _run


def _bootstrap(repo: Path, write_config) -> None:
    write_config(
        repo,
        {
            "config_schema_version": 1,
            "prds_dir": ".prd-os/prds",
            "issues_dir": ".prd-os/issues",
            "findings_dir": ".prd-os/findings",
            "state_dir": ".claude/state",
            "codebase_map_path": ".prd-os/codebase-map.json",
        },
    )


def _seed_python_project(repo: Path) -> None:
    """Populate the fake repo with a small but realistic shape."""
    (repo / "pyproject.toml").write_text(
        "[project]\nname = \"demo\"\n[tool.pytest.ini_options]\nminversion = \"7\"\n"
    )
    (repo / "README.md").write_text("# Demo\n")
    (repo / "CLAUDE.md").write_text("# Demo context\n")

    src = repo / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text("def main():\n    pass  # TODO tighten this\n")
    (src / "util.py").write_text("# FIXME rename\nvalue = 1\n")

    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_demo.py").write_text("def test_ok():\n    assert True\n")

    # Directories that must NOT be scanned.
    node_modules = repo / "node_modules" / "foo"
    node_modules.mkdir(parents=True)
    (node_modules / "polluted.py").write_text(
        "# This file lives under node_modules and must be ignored.\n"
    )
    reports = repo / "Reports"
    reports.mkdir()
    (reports / "big.pdf").write_text("binary-blob")

    # Dotenv: name recorded, contents never read.
    (repo / ".env").write_text("SECRET=must_not_be_read\n")
    (repo / ".env.example").write_text("SECRET=example\n")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def test_build_writes_json_and_markdown(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)

    result = run_map_runner(fake_repo, "build")
    assert result.returncode == 0, result.stderr

    json_path = fake_repo / ".prd-os" / "codebase-map.json"
    md_path = fake_repo / ".prd-os" / "codebase-map.md"
    assert json_path.is_file()
    assert md_path.is_file()

    payload = json.loads(json_path.read_text())
    assert payload["schema_version"] == 1
    assert payload["built_at"].endswith("Z")


def test_build_detects_python_language_and_ignores_node_modules(
    fake_repo, write_config, run_map_runner
):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    result = run_map_runner(fake_repo, "build")
    assert result.returncode == 0, result.stderr

    payload = json.loads((fake_repo / ".prd-os" / "codebase-map.json").read_text())
    langs = payload["languages"]
    assert "python" in langs
    assert ".py" in langs["python"]["extensions"]

    # node_modules and Reports must not contribute.
    # __init__.py + main.py + util.py + tests/test_demo.py = 4 files expected.
    assert langs["python"]["file_count"] == 4

    # Neither node_modules nor Reports may appear in the directory tree.
    dir_paths = {d["path"] for d in payload["directory_tree"]}
    assert "node_modules" not in dir_paths
    assert "Reports" not in dir_paths
    # Top-level FILES must not be miscounted as directories.
    # `pyproject.toml`, `README.md`, `CLAUDE.md`, `.env` all sit at the root.
    assert "pyproject.toml" not in dir_paths
    assert "README.md" not in dir_paths
    assert "CLAUDE.md" not in dir_paths
    assert ".env" not in dir_paths
    # But src and tests should.
    assert "src" in dir_paths
    assert "tests" in dir_paths


def test_build_detects_pyproject_and_pytest(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    assert run_map_runner(fake_repo, "build").returncode == 0
    payload = json.loads((fake_repo / ".prd-os" / "codebase-map.json").read_text())

    manifest_kinds = {m["kind"] for m in payload["package_manifests"]}
    assert "pyproject.toml" in manifest_kinds
    assert payload["test_framework"] == "pytest"


def test_build_detects_package_json_and_jest(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    (fake_repo / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "0.0.1",
                "devDependencies": {"jest": "^29.0.0"},
            }
        )
    )
    (fake_repo / "src").mkdir()
    (fake_repo / "src" / "index.ts").write_text("export const x = 1\n")
    assert run_map_runner(fake_repo, "build").returncode == 0
    payload = json.loads((fake_repo / ".prd-os" / "codebase-map.json").read_text())
    assert payload["test_framework"] == "jest"
    assert "typescript" in payload["languages"]
    assert "src/index.ts" in payload["entry_points"]


def test_build_records_dotenv_names_only(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    assert run_map_runner(fake_repo, "build").returncode == 0
    payload = json.loads((fake_repo / ".prd-os" / "codebase-map.json").read_text())

    env_files = payload["env_files_present"]
    assert ".env" in env_files
    assert ".env.example" in env_files

    # Security invariant: the SECRET value must never leak into any artifact.
    json_text = (fake_repo / ".prd-os" / "codebase-map.json").read_text()
    md_text = (fake_repo / ".prd-os" / "codebase-map.md").read_text()
    assert "must_not_be_read" not in json_text
    assert "must_not_be_read" not in md_text


def test_build_counts_todos(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    assert run_map_runner(fake_repo, "build").returncode == 0
    payload = json.loads((fake_repo / ".prd-os" / "codebase-map.json").read_text())
    # main.py has 1 TODO, util.py has 1 FIXME => 2.
    assert payload["todo_count"] == 2


def test_build_entry_points_ignore_tests(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    assert run_map_runner(fake_repo, "build").returncode == 0
    payload = json.loads((fake_repo / ".prd-os" / "codebase-map.json").read_text())

    entries = payload["entry_points"]
    assert "src/demo/main.py" in entries
    # tests/test_demo.py is not an entry point.
    assert not any(e.startswith("tests/") for e in entries)


def test_build_refuses_to_overwrite_without_force(
    fake_repo, write_config, run_map_runner
):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    assert run_map_runner(fake_repo, "build").returncode == 0
    second = run_map_runner(fake_repo, "build")
    assert second.returncode == 2
    assert "--force" in second.stderr


def test_build_force_overwrites(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    assert run_map_runner(fake_repo, "build").returncode == 0
    (fake_repo / "src" / "demo" / "extra.py").write_text("# new file\n")
    result = run_map_runner(fake_repo, "build", "--force")
    assert result.returncode == 0, result.stderr
    payload = json.loads((fake_repo / ".prd-os" / "codebase-map.json").read_text())
    assert payload["languages"]["python"]["file_count"] == 5


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_missing_map(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    result = run_map_runner(fake_repo, "status")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["exists"] is False


def test_status_reports_fresh_map(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    assert run_map_runner(fake_repo, "build").returncode == 0
    result = run_map_runner(fake_repo, "status")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["exists"] is True
    assert payload["stale"] is False
    assert payload["age_days"] is not None


def test_status_flags_stale_map(fake_repo, write_config, run_map_runner):
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    assert run_map_runner(fake_repo, "build").returncode == 0

    # Backdate the built_at timestamp to force staleness.
    json_path = fake_repo / ".prd-os" / "codebase-map.json"
    payload = json.loads(json_path.read_text())
    backdated = datetime.now(timezone.utc) - timedelta(days=45)
    payload["built_at"] = backdated.strftime("%Y-%m-%dT%H:%M:%SZ")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    result = run_map_runner(fake_repo, "status", "--max-age-days", "30")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["stale"] is True
    assert any("age_days" in r for r in payload["stale_reasons"])


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_build_output_matches_schema(fake_repo, write_config, run_map_runner):
    jsonschema = pytest.importorskip("jsonschema")
    _bootstrap(fake_repo, write_config)
    _seed_python_project(fake_repo)
    assert run_map_runner(fake_repo, "build").returncode == 0
    payload = json.loads((fake_repo / ".prd-os" / "codebase-map.json").read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(payload, schema)
