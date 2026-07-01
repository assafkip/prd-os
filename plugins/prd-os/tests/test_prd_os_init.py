"""Tests for `/prd-os-init` (prd_os_init.py) and the config-default alignment
between config.py and issue_runner.py.

Resolves three dogfooding defects:
  - issue_runner.py defaulted issues_dir to `issues` while config.py used
    `.prd-os/issues`, so a no-config repo split specs to one place and the
    runner looked in another.
  - `/prd-os-init` was referenced by the config error but never implemented.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
PRD_OS_INIT = SCRIPTS_DIR / "prd_os_init.py"


def _run(script: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(repo)
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
    )


# --- prd_os_init -------------------------------------------------------------


def test_init_creates_and_validates_config(fake_repo, import_config):
    config_path = fake_repo / ".prd-os" / "config.json"
    assert not config_path.exists()
    r = _run(PRD_OS_INIT, fake_repo)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "initialized" in out
    assert config_path.is_file()
    # The written config loads under the strict reader.
    cfg = import_config.load(fake_repo, strict=True)
    assert cfg.issues_dir == (fake_repo / ".prd-os" / "issues").resolve()


def test_init_is_idempotent_and_non_destructive(fake_repo):
    config_path = fake_repo / ".prd-os" / "config.json"
    config_path.parent.mkdir(parents=True)
    custom = {"config_schema_version": 1, "issues_dir": ".prd-os/issues", "custom_key": "keep"}
    config_path.write_text(json.dumps(custom))
    r = _run(PRD_OS_INIT, fake_repo)
    assert r.returncode == 0, r.stderr
    assert "exists" in json.loads(r.stdout)
    # Untouched: the custom key survives.
    assert json.loads(config_path.read_text())["custom_key"] == "keep"


def test_init_force_overwrites(fake_repo):
    config_path = fake_repo / ".prd-os" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"config_schema_version": 1, "custom_key": "gone"}))
    r = _run(PRD_OS_INIT, fake_repo, "--force")
    assert r.returncode == 0, r.stderr
    assert "initialized" in json.loads(r.stdout)
    assert "custom_key" not in json.loads(config_path.read_text())


def test_init_then_prd_runner_works(fake_repo, run_prd_runner):
    # End-to-end: a fresh repo can be initialized and immediately drive the PRD
    # runner, which previously failed with "config not found".
    assert _run(PRD_OS_INIT, fake_repo).returncode == 0
    r = run_prd_runner(fake_repo, "new", "demo-slug", "--owner", "tester")
    assert r.returncode == 0, r.stderr
    assert "created" in json.loads(r.stdout)


# --- config-default alignment (issue_runner vs config.py) --------------------


def test_issue_runner_default_issues_dir_matches_config(fake_repo, run_runner, write_issue_spec):
    # NO config written: the runner must fall back to `.prd-os/issues`, the same
    # default config.py (and prd_split) use. Regression guard for the mismatch.
    issues_dir = fake_repo / ".prd-os" / "issues"
    write_issue_spec(issues_dir, "align-check", allowed_files=["src/x.py"])
    r = run_runner(fake_repo, "load", "align-check")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["loaded"] == "align-check"
