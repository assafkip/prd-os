"""Tests for the cross-PRD findings cross-reference (findings_xref.py) and its
deterministic wiring into findings_writer.py's `advisory` subcommand.

Covers, per the parent PRD's acceptance:
  - match / no-match
  - disposition filtering (only prior rejected/deferred surface)
  - self-exclusion (a PRD never matches its own findings)
  - malformed JSONL + short-body robustness (finding-5)
  - threshold resolution from config (finding-3)
  - the advisory subcommand invokes the xref (finding-1)
  - a raising xref does NOT change findings_writer's exit code (finding-2)
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
FINDINGS_XREF = SCRIPTS_DIR / "findings_xref.py"
FINDINGS_WRITER = SCRIPTS_DIR / "findings_writer.py"


def _load(name: str, path: Path):
    """Load a script module by path and register it under `name` so sibling
    imports (e.g. findings_writer's `import findings_xref`) resolve."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def xref():
    return _load("findings_xref", FINDINGS_XREF)


def _record(fid, body, disposition, rationale=None):
    rec = {
        "id": fid,
        "prd_id": "ignored",
        "source": "manual",
        "severity": "major",
        "disposition": disposition,
        "body": body,
        "created_at": "2026-01-01T00:00:00Z",
    }
    if rationale is not None:
        rec["rationale"] = rationale
    return rec


def _write_findings(findings_dir: Path, prd_id: str, records: list) -> Path:
    findings_dir.mkdir(parents=True, exist_ok=True)
    path = findings_dir / f"{prd_id}-findings.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _setup(repo: Path, write_config, payload_extra=None):
    payload = {"config_schema_version": 1, "findings_dir": ".prd-os/findings"}
    if payload_extra:
        payload.update(payload_extra)
    write_config(repo, payload)
    return repo / ".prd-os" / "findings"


SIMILAR_A = "the writer should add a retry loop around the network call to handle flaky timeouts"
SIMILAR_B = "the writer should add a retry loop around the network call to handle flaky timeouts gracefully"
DIFFERENT = "rename the config variable and update the changelog entry for clarity"


# --- similarity unit ---------------------------------------------------------


def test_jaccard_identical_is_one(xref):
    assert xref.jaccard(SIMILAR_A, SIMILAR_A) == 1.0


def test_jaccard_disjoint_is_zero(xref):
    assert xref.jaccard("alpha beta gamma delta", "one two three four") == 0.0


def test_short_body_uses_unigram_fallback(xref):
    # Fewer than 3 tokens: must still match identical short bodies, not 0.
    assert xref.jaccard("retry timeout", "retry timeout") == 1.0


def test_short_vs_long_is_symmetric_nonzero(xref):
    # Regression (finding-1): a 2-token body vs a similar longer body must NOT
    # score 0 just because one side is below the 3-shingle size.
    score = xref.jaccard("retry timeout", "retry timeout gracefully please now")
    assert score > 0.0
    # Symmetric regardless of argument order.
    assert score == xref.jaccard("retry timeout gracefully please now", "retry timeout")


# --- cross_reference core ----------------------------------------------------


def test_match_surfaces_prior_rejection(xref, fake_repo, write_config):
    fdir = _setup(fake_repo, write_config)
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    _write_findings(
        fdir,
        "prd-prior",
        [_record("finding-7", SIMILAR_B, "rejected", rationale="writer is idempotent")],
    )
    matches = xref.cross_reference("prd-active", repo_root=fake_repo)
    assert len(matches) == 1
    m = matches[0]
    assert m["prior_prd_id"] == "prd-prior"
    assert m["prior_finding_id"] == "finding-7"
    assert m["prior_disposition"] == "rejected"
    assert m["prior_rationale"] == "writer is idempotent"
    assert m["similarity"] >= 0.6


def test_no_match_when_dissimilar(xref, fake_repo, write_config):
    fdir = _setup(fake_repo, write_config)
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    _write_findings(fdir, "prd-prior", [_record("finding-7", DIFFERENT, "rejected", rationale="n/a")])
    assert xref.cross_reference("prd-active", repo_root=fake_repo) == []


def test_disposition_filtering(xref, fake_repo, write_config):
    fdir = _setup(fake_repo, write_config)
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    # accepted prior must NOT surface; deferred prior must.
    _write_findings(fdir, "prd-accepted", [_record("finding-2", SIMILAR_B, "accepted")])
    _write_findings(fdir, "prd-deferred", [_record("finding-3", SIMILAR_B, "deferred", rationale="later")])
    matches = xref.cross_reference("prd-active", repo_root=fake_repo)
    prds = {m["prior_prd_id"] for m in matches}
    assert prds == {"prd-deferred"}


def test_self_exclusion(xref, fake_repo, write_config):
    fdir = _setup(fake_repo, write_config)
    # The active file itself contains a rejected finding identical to its
    # pending one. It must never match itself.
    _write_findings(
        fdir,
        "prd-active",
        [
            _record("finding-1", SIMILAR_A, "pending"),
            _record("finding-2", SIMILAR_A, "rejected", rationale="self"),
        ],
    )
    assert xref.cross_reference("prd-active", repo_root=fake_repo) == []


def test_malformed_and_missing_fields_are_skipped(xref, fake_repo, write_config):
    fdir = _setup(fake_repo, write_config)
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    sibling = fdir / "prd-prior-findings.jsonl"
    sibling.write_text(
        "this is not json\n"
        + json.dumps({"id": "finding-x", "disposition": "rejected"})  # no body
        + "\n"
        + json.dumps(_record("finding-7", SIMILAR_B, "rejected", rationale="ok"))
        + "\n"
    )
    matches = xref.cross_reference("prd-active", repo_root=fake_repo)
    assert len(matches) == 1
    assert matches[0]["prior_finding_id"] == "finding-7"


def test_missing_id_records_are_skipped(xref, fake_repo, write_config):
    # Regression (finding-2): a prior record with a settled disposition + body
    # but no id must be skipped, not surfaced with a null prior_finding_id.
    fdir = _setup(fake_repo, write_config)
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    no_id = {"disposition": "rejected", "body": SIMILAR_B, "rationale": "x"}
    good = _record("finding-7", SIMILAR_B, "rejected", rationale="ok")
    _write_findings(fdir, "prd-prior", [no_id, good])
    matches = xref.cross_reference("prd-active", repo_root=fake_repo)
    assert {m["prior_finding_id"] for m in matches} == {"finding-7"}
    assert all(m["prior_finding_id"] is not None for m in matches)


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-0.5", "1.5"])
def test_invalid_threshold_falls_back_to_default(xref, fake_repo, write_config, bad):
    # Regression (finding-4): nan/inf/negative/>1 thresholds must not silently
    # suppress-all or surface-all. They fall back to the 0.6 default, so a
    # normally-matching pair still matches.
    fdir = _setup(fake_repo, write_config, {"xref_threshold": float(bad)})
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    _write_findings(fdir, "prd-prior", [_record("finding-7", SIMILAR_B, "rejected", rationale="r")])
    matches = xref.cross_reference("prd-active", repo_root=fake_repo)
    assert len(matches) == 1  # default threshold applied, pair matches


def test_invalid_cli_threshold_override_falls_back(xref, fake_repo, write_config):
    fdir = _setup(fake_repo, write_config)
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    _write_findings(fdir, "prd-prior", [_record("finding-7", SIMILAR_B, "rejected", rationale="r")])
    # nan override -> default 0.6 -> still matches.
    assert xref.cross_reference("prd-active", repo_root=fake_repo, threshold=float("nan"))


def test_config_threshold_accepts_numeric_string(xref, fake_repo, write_config):
    # Regression (finding-5): a string "0.99" in config must take effect like a
    # numeric value, not be silently ignored.
    fdir = _setup(fake_repo, write_config, {"xref_threshold": "0.99"})
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    _write_findings(fdir, "prd-prior", [_record("finding-7", SIMILAR_B, "rejected", rationale="r")])
    # 0.99 is honored (not the 0.6 default), so the non-identical pair is suppressed.
    assert xref.cross_reference("prd-active", repo_root=fake_repo) == []


def test_threshold_from_config_suppresses_partial_match(xref, fake_repo, write_config):
    # A high config threshold suppresses a non-identical pair that would match
    # at the 0.6 default.
    fdir = _setup(fake_repo, write_config, {"xref_threshold": 0.99})
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    _write_findings(fdir, "prd-prior", [_record("finding-7", SIMILAR_B, "rejected", rationale="x")])
    assert xref.cross_reference("prd-active", repo_root=fake_repo) == []
    # Explicit flag override beats config.
    assert xref.cross_reference("prd-active", repo_root=fake_repo, threshold=0.5)


def test_no_active_findings_file_is_empty(xref, fake_repo, write_config):
    _setup(fake_repo, write_config)
    assert xref.cross_reference("prd-nonexistent", repo_root=fake_repo) == []


# --- CLI ---------------------------------------------------------------------


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


def test_cli_json_output(fake_repo, write_config):
    fdir = _setup(fake_repo, write_config)
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    _write_findings(fdir, "prd-prior", [_record("finding-7", SIMILAR_B, "rejected", rationale="r")])
    r = _run(FINDINGS_XREF, fake_repo, "--prd", "prd-active", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data and data[0]["prior_prd_id"] == "prd-prior"


def test_cli_exit_2_on_missing_config(fake_repo):
    # No config.json written: config resolution fails -> exit 2.
    r = _run(FINDINGS_XREF, fake_repo, "--prd", "prd-active")
    assert r.returncode == 2
    assert "config" in r.stderr.lower()


# --- wiring into findings_writer (deterministic invocation) ------------------


def test_advisory_subcommand_invokes_xref(fake_repo, write_config):
    fdir = _setup(fake_repo, write_config)
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    _write_findings(fdir, "prd-prior", [_record("finding-7", SIMILAR_B, "rejected", rationale="settled")])
    r = _run(FINDINGS_WRITER, fake_repo, "advisory", "prd-active")
    assert r.returncode == 0, r.stderr
    assert "prd-prior" in r.stdout
    assert "settled" in r.stdout


def test_advisory_empty_when_no_match(fake_repo, write_config):
    fdir = _setup(fake_repo, write_config)
    _write_findings(fdir, "prd-active", [_record("finding-1", SIMILAR_A, "pending")])
    r = _run(FINDINGS_WRITER, fake_repo, "advisory", "prd-active")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_advisory_swallows_config_error(fake_repo):
    # Regression (finding-3): no config.json -> findings_writer.main loads config
    # and would normally exit 2. For the advisory command it must return 0 so
    # /prd-triage is never blocked. (Contrast: findings_xref.py CLI returns 2.)
    r = _run(FINDINGS_WRITER, fake_repo, "advisory", "prd-active")
    assert r.returncode == 0
    assert "xref unavailable" in r.stderr.lower()


def test_advisory_swallows_xref_failure(fake_repo, write_config, monkeypatch):
    """Non-blocking guarantee (finding-2): a raising xref must not change
    findings_writer's exit code or block triage."""
    _setup(fake_repo, write_config)
    writer = _load("findings_writer", FINDINGS_WRITER)
    xref_mod = _load("findings_xref", FINDINGS_XREF)

    def _boom(*a, **k):
        raise RuntimeError("simulated xref explosion")

    monkeypatch.setattr(xref_mod, "cross_reference", _boom)
    rc = writer.main(["--repo-root", str(fake_repo), "advisory", "prd-active"])
    assert rc == 0
