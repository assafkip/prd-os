"""Deterministic cross-PRD findings cross-reference for the prd-os plugin.

Surfaces prior findings from *sibling* PRDs that were already `rejected` or
`deferred` (the dispositions that carry rationale) and that closely match a
pending finding on the active PRD. The point is institutional memory at triage
time: "PRD-A already settled this, here is why."

Design constraints (mirror the PRD `prd-spec-sections-and-xprd-findings`):
  - Deterministic. Token-shingle Jaccard similarity. No LLM, no DB, no network.
  - Read-only. Never writes findings or state.
  - Robust. Malformed JSONL lines, records missing fields, and unreadable
    sibling files are skipped, never fatal. The only hard error is failing to
    resolve the findings directory at all (exit 2).
  - Advisory. Callers treat any failure as non-fatal; this module never blocks
    triage.

Threshold resolution: `--threshold` flag > `config.json` key `xref_threshold`
> built-in default 0.6. Behavior is specified against the *resolved* threshold,
never the literal default, so calibration is config tuning, not a code change.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    Config,
    ConfigError,
    CONFIG_RELPATH,
    discover_repo_root,
    load as load_config,
)


DEFAULT_THRESHOLD = 0.6
SHINGLE_SIZE = 3
PRIOR_DISPOSITIONS = ("rejected", "deferred")
FINDINGS_SUFFIX = "-findings.jsonl"
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def _normalize(body: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    lowered = body.lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", no_punct).strip()


def _tokens(body: str) -> list[str]:
    return _normalize(body).split()


def _shingles(tokens: list[str], k: int) -> frozenset[str]:
    """Word-level k-shingles. When fewer than k tokens, fall back to the
    unigram token set."""
    if not tokens:
        return frozenset()
    if len(tokens) < k:
        return frozenset(tokens)
    return frozenset(
        " ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)
    )


def jaccard(a: str, b: str) -> float:
    """Jaccard similarity over word-shingle sets of two finding bodies.

    Short-body rule (symmetric): the shingle size is shared across BOTH bodies,
    `k = min(SHINGLE_SIZE, len(a_tokens), len(b_tokens))` (floor 1). This keeps
    a short body and a long body on the same granularity, so a 2-token finding
    still overlaps a similar longer finding instead of silently scoring 0.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    k = max(1, min(SHINGLE_SIZE, len(ta), len(tb)))
    sa, sb = _shingles(ta, k), _shingles(tb, k)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# IO (defensive)
# ---------------------------------------------------------------------------


def _read_records(path: Path) -> list[dict]:
    """Read a findings JSONL file, skipping malformed lines. Returns [] when
    the file is missing or unreadable rather than raising."""
    try:
        text = path.read_text()
    except OSError:
        return []
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _prd_id_from_filename(name: str) -> str:
    return name[: -len(FINDINGS_SUFFIX)]


def _coerce_threshold(value: object) -> Optional[float]:
    """Coerce a threshold to a finite float in [0, 1]. Accepts numeric strings
    (e.g. "0.75") so a config edit takes effect like the CLI flag. Returns None
    for anything non-numeric, non-finite (nan/inf), or out of range, so the
    caller falls back to the default instead of silently breaking the contract.
    """
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < 0.0 or f > 1.0:
        return None
    return f


def _resolve_threshold(repo_root: Path, override: Optional[float]) -> float:
    if override is not None:
        coerced = _coerce_threshold(override)
        return coerced if coerced is not None else DEFAULT_THRESHOLD
    cfg_path = repo_root / CONFIG_RELPATH
    try:
        data = json.loads(cfg_path.read_text())
        coerced = _coerce_threshold(data.get("xref_threshold"))
        if coerced is not None:
            return coerced
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_THRESHOLD


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def cross_reference(
    prd_id: str,
    *,
    cfg: Optional[Config] = None,
    repo_root: Optional[Path] = None,
    threshold: Optional[float] = None,
) -> list[dict]:
    """Return advisory matches for the active PRD's pending findings.

    Each match: current_finding_id, current_body, prior_prd_id,
    prior_finding_id, prior_disposition, prior_rationale, similarity.

    Read-only. Raises ConfigError only if the findings dir cannot be resolved.
    """
    if cfg is None:
        cfg = load_config(repo_root)
    if repo_root is None:
        repo_root = discover_repo_root()
    resolved_threshold = _resolve_threshold(repo_root, threshold)

    findings_dir = cfg.findings_dir
    active_path = findings_dir / f"{prd_id}{FINDINGS_SUFFIX}"
    active = _read_records(active_path)
    pending = [
        r for r in active
        if r.get("disposition") == "pending"
        and isinstance(r.get("body"), str)
        and isinstance(r.get("id"), str)
    ]
    if not pending:
        return []

    # Gather sibling findings from the top level of findings_dir only.
    # Globbing the top level naturally excludes the issue/ subdir.
    siblings: list[tuple[str, dict]] = []
    try:
        sibling_files = sorted(findings_dir.glob(f"*{FINDINGS_SUFFIX}"))
    except OSError:
        sibling_files = []
    for path in sibling_files:
        if path.resolve() == active_path.resolve():
            continue  # self-exclusion
        sib_prd = _prd_id_from_filename(path.name)
        if sib_prd == prd_id:
            continue
        for rec in _read_records(path):
            if (
                rec.get("disposition") in PRIOR_DISPOSITIONS
                and isinstance(rec.get("body"), str)
                and isinstance(rec.get("id"), str)
            ):
                siblings.append((sib_prd, rec))

    matches: list[dict] = []
    for cur in pending:
        cur_body = cur["body"]
        for sib_prd, prior in siblings:
            score = jaccard(cur_body, prior["body"])
            if score >= resolved_threshold:
                matches.append(
                    {
                        "current_finding_id": cur.get("id"),
                        "current_body": cur_body,
                        "prior_prd_id": sib_prd,
                        "prior_finding_id": prior.get("id"),
                        "prior_disposition": prior.get("disposition"),
                        "prior_rationale": prior.get("rationale"),
                        "similarity": round(score, 4),
                    }
                )
    # Strongest matches first, stable for ties.
    matches.sort(key=lambda m: m["similarity"], reverse=True)
    return matches


def format_advisory(matches: list[dict]) -> str:
    """Human-readable advisory block. Empty string when there are no matches."""
    if not matches:
        return ""
    lines = [
        "xref advisory: sibling PRDs previously settled similar findings:",
    ]
    for m in matches:
        lines.append(
            f"  - {m['current_finding_id']} ~ {m['prior_prd_id']}"
            f"/{m['prior_finding_id']} [{m['prior_disposition']}, "
            f"sim={m['similarity']}]"
        )
        rationale = m.get("prior_rationale")
        if rationale:
            lines.append(f"      rationale: {rationale}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prd", required=True, help="active PRD id")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="similarity threshold (overrides config xref_threshold)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit raw JSON array instead of the formatted advisory",
    )
    args = parser.parse_args(argv)

    try:
        matches = cross_reference(args.prd, threshold=args.threshold)
    except ConfigError as exc:
        print(f"findings_xref config error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(matches, indent=2))
    else:
        advisory = format_advisory(matches)
        if advisory:
            print(advisory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
