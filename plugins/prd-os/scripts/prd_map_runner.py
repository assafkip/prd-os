#!/usr/bin/env python3
"""Codebase-map builder for the prd-os plugin.

Subcommands:
  build                    Scan the repo, write codebase-map.json + .md
  status                   Print map freshness (exists, age_days, git distance)

The map is facts-only. It documents what files exist, what languages and
frameworks the repo uses, and where the entry points are. It does not
distill "conventions" or "patterns" — that is intentionally deferred. A PRD
author should be able to read this and answer "what already exists" without
hallucinating.

Security invariants (enforced here, not delegated to callers):
  - .env and .env.* files: name recorded in `env_files_present`, contents
    never read. This matches the repo's .claude/rules/security.md policy.
  - No file under an ignored dir is read (node_modules, .venv, __pycache__,
    dist, build, Reports/, etc.).
  - All scans are bounded by MAX_FILES_SCANNED so a pathologically large
    repo cannot hang the runner.

Output layout under the configured codebase_map_path (default
`.prd-os/codebase-map.json`):
  - <codebase_map_path>       the structured JSON map
  - <codebase_map_path>.md     the rendered human-readable view
(the .md sibling is derived via Config.codebase_map_md_path)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config, ConfigError, load as load_config  # noqa: E402


SCHEMA_VERSION = 1

# Directories we never descend into. Build artifacts, dependency caches,
# and the KTLYST-specific Reports/ dir (large PDFs, token-discipline rule).
IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",
        "out",
        ".next",
        ".nuxt",
        ".cache",
        ".terraform",
        "coverage",
        ".coverage",
        "htmlcov",
        ".idea",
        ".vscode",
        "Reports",
    }
)

# Language detection by extension. Keep to the common set; exotic langs can
# be added as needed. Order here is only cosmetic.
LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".scala": "scala",
    ".lua": "lua",
    ".R": "r",
    ".r": "r",
    ".dart": "dart",
    ".elm": "elm",
    ".ex": "elixir",
    ".exs": "elixir",
}

MANIFEST_KINDS = (
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "composer.json",
)

LINT_CONFIG_NAMES = (
    ".eslintrc",
    ".eslintrc.json",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.yml",
    ".eslintrc.yaml",
    "eslint.config.js",
    "eslint.config.mjs",
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.js",
    "prettier.config.js",
    "biome.json",
    ".flake8",
    "setup.cfg",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".mypy.ini",
    ".rubocop.yml",
    ".clippy.toml",
    ".editorconfig",
)

ENTRY_POINT_STEMS = (
    "main",
    "app",
    "server",
    "index",
    "cli",
    "__main__",
)

NOTABLE_FILE_NAMES = (
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".github/workflows",
    "pyproject.toml",
    "package.json",
    "tsconfig.json",
)

TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
DOTENV_RE = re.compile(r"^\.env(\..+)?$")

# Bounds. If a real repo exceeds these, we still produce a map; we just mark
# the scan as partial via `ignored_paths`. Never hang.
MAX_FILES_SCANNED = 50_000
MAX_TODO_BYTES_PER_FILE = 2 * 1024 * 1024  # 2 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha(repo_root: Path) -> Optional[str]:
    if not (repo_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _relpath(repo_root: Path, p: Path) -> str:
    try:
        return str(p.resolve().relative_to(repo_root))
    except ValueError:
        return str(p)


def _is_ignored(rel: Path) -> bool:
    return any(part in IGNORED_DIR_NAMES for part in rel.parts)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def _scan(repo_root: Path) -> dict:
    """Walk the repo once, collect everything we need in a single pass."""
    languages: dict[str, dict] = {}
    manifests: list[dict] = []
    lint_configs: list[str] = []
    entry_points: set[str] = set()
    directory_counts: dict[str, int] = {}
    notable: dict[str, bool] = {name: False for name in NOTABLE_FILE_NAMES}
    env_files: list[str] = []
    todo_count = 0
    total_loc = 0
    files_scanned = 0
    partial = False

    for dirpath, dirnames, filenames in os.walk(repo_root):
        # Prune in-place so os.walk does not descend into ignored dirs.
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIR_NAMES]

        current = Path(dirpath)
        rel_current = current.relative_to(repo_root)

        # Record top-level directory presence (file count aggregated below).
        if rel_current.parts:
            top = rel_current.parts[0]
            directory_counts.setdefault(top, 0)

        # Notable workflows dir (the directory itself, not a file).
        if rel_current.parts[:2] == (".github", "workflows"):
            notable[".github/workflows"] = True

        for name in filenames:
            files_scanned += 1
            if files_scanned > MAX_FILES_SCANNED:
                partial = True
                break

            path = current / name
            rel = path.relative_to(repo_root)
            rel_str = str(rel)

            # Dotenv detection — names only, never read.
            if DOTENV_RE.match(name):
                env_files.append(rel_str)
                # Dotenv files are not counted as source, skip the rest.
                continue

            # Top-level directory file count. Only aggregate when the file
            # actually lives inside a directory (not a bare file at repo root).
            # Top-level files would otherwise be miscounted as directories.
            if len(rel.parts) > 1:
                directory_counts[rel.parts[0]] = directory_counts.get(rel.parts[0], 0) + 1

            # Notable files (exact basename match at any depth for files like
            # README.md; exact rel path match for nested ones handled above).
            if name in notable:
                notable[name] = True

            # Manifests.
            if name in MANIFEST_KINDS:
                manifests.append({"path": rel_str, "kind": name})

            # Lint configs.
            if name in LINT_CONFIG_NAMES:
                lint_configs.append(rel_str)
            elif name.startswith(".eslintrc") or name.startswith(".prettierrc"):
                # Catch variant suffixes (e.g. .eslintrc.local) conservatively.
                lint_configs.append(rel_str)

            # Language accounting.
            ext = path.suffix
            lang = LANGUAGE_BY_EXT.get(ext)
            if lang is not None:
                bucket = languages.setdefault(
                    lang, {"file_count": 0, "extensions": set()}
                )
                bucket["file_count"] += 1
                bucket["extensions"].add(ext)

                # Entry-point heuristic: filename stem in ENTRY_POINT_STEMS
                # and the file sits near the repo root or inside a src-like
                # top-level directory. Avoid test dirs.
                stem = path.stem
                top_dir = rel.parts[0] if rel.parts else ""
                if (
                    stem in ENTRY_POINT_STEMS
                    and top_dir not in ("tests", "test", "__tests__")
                    and "test" not in rel.parts[:-1]
                ):
                    entry_points.add(rel_str)

                # TODO + LOC accounting (only for source files).
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                if 0 < size <= MAX_TODO_BYTES_PER_FILE:
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        text = ""
                    if text:
                        total_loc += text.count("\n") + (0 if text.endswith("\n") else 1)
                        todo_count += len(TODO_RE.findall(text))

        if partial:
            break

    # Test framework detection.
    test_framework = _detect_test_framework(repo_root, languages, manifests)

    # Shape up for JSON serialization.
    lang_out = {
        name: {
            "file_count": data["file_count"],
            "extensions": sorted(data["extensions"]),
        }
        for name, data in sorted(languages.items())
    }

    dir_out = sorted(
        [{"path": p, "file_count": c} for p, c in directory_counts.items()],
        key=lambda x: (-x["file_count"], x["path"]),
    )

    manifests.sort(key=lambda m: m["path"])
    lint_configs = sorted(set(lint_configs))
    env_files.sort()

    ignored = sorted(IGNORED_DIR_NAMES)
    if partial:
        ignored.append(f"scan truncated at {MAX_FILES_SCANNED} files")

    return {
        "languages": lang_out,
        "package_manifests": manifests,
        "test_framework": test_framework,
        "lint_configs": lint_configs,
        "entry_points": sorted(entry_points),
        "directory_tree": dir_out,
        "notable_files": notable,
        "env_files_present": env_files,
        "todo_count": todo_count,
        "total_loc": total_loc,
        "ignored_paths": ignored,
    }


def _detect_test_framework(
    repo_root: Path, languages: dict, manifests: list[dict]
) -> Optional[str]:
    """Best-effort test framework detection from manifest contents."""
    # pyproject.toml -> pytest / unittest
    for manifest in manifests:
        path = repo_root / manifest["path"]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if manifest["kind"] == "pyproject.toml" and "pytest" in text:
            return "pytest"
        if manifest["kind"] == "package.json":
            if '"vitest"' in text:
                return "vitest"
            if '"jest"' in text:
                return "jest"
            if '"mocha"' in text:
                return "mocha"
        if manifest["kind"] == "Cargo.toml":
            return "cargo"
        if manifest["kind"] == "Gemfile" and "rspec" in text.lower():
            return "rspec"

    # Fall back to conventional test dirs.
    if (repo_root / "tests").is_dir() and "python" in languages:
        return "pytest"
    if (repo_root / "__tests__").is_dir() and (
        "typescript" in languages or "javascript" in languages
    ):
        return "jest"
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_markdown(payload: dict) -> str:
    """Deterministic markdown rendering of the JSON map. No LLM."""
    lines: list[str] = []
    lines.append("# Codebase map")
    lines.append("")
    lines.append(f"Schema version: {payload['schema_version']}")
    lines.append(f"Built at: {payload['built_at']}")
    if payload.get("git_sha"):
        lines.append(f"Git sha: `{payload['git_sha']}`")
    lines.append(f"Repo root: `{payload['repo_root']}`")
    lines.append("")

    languages = payload.get("languages", {})
    if languages:
        lines.append("## Languages")
        lines.append("")
        for name, data in languages.items():
            exts = ", ".join(f"`{e}`" for e in data["extensions"])
            lines.append(f"- {name}: {data['file_count']} files ({exts})")
        lines.append("")

    manifests = payload.get("package_manifests", [])
    if manifests:
        lines.append("## Package manifests")
        lines.append("")
        for m in manifests:
            lines.append(f"- `{m['path']}` ({m['kind']})")
        lines.append("")

    tf = payload.get("test_framework")
    if tf:
        lines.append(f"## Test framework")
        lines.append("")
        lines.append(f"Primary: {tf}")
        lines.append("")

    lint = payload.get("lint_configs", [])
    if lint:
        lines.append("## Lint / format configs")
        lines.append("")
        for p in lint:
            lines.append(f"- `{p}`")
        lines.append("")

    entry = payload.get("entry_points", [])
    if entry:
        lines.append("## Entry points")
        lines.append("")
        for p in entry:
            lines.append(f"- `{p}`")
        lines.append("")

    tree = payload.get("directory_tree", [])
    if tree:
        lines.append("## Top-level directories")
        lines.append("")
        lines.append("| Path | Files |")
        lines.append("| --- | ---: |")
        for entry in tree:
            lines.append(f"| `{entry['path']}` | {entry['file_count']} |")
        lines.append("")

    notable = {k: v for k, v in payload.get("notable_files", {}).items() if v}
    if notable:
        lines.append("## Notable files present")
        lines.append("")
        for name in sorted(notable):
            lines.append(f"- `{name}`")
        lines.append("")

    env_files = payload.get("env_files_present", [])
    if env_files:
        lines.append("## Dotenv files")
        lines.append("")
        lines.append(
            "Contents intentionally not read. Names only:"
        )
        lines.append("")
        for p in env_files:
            lines.append(f"- `{p}`")
        lines.append("")

    lines.append("## Stats")
    lines.append("")
    lines.append(f"- Approx total LOC: {payload.get('total_loc', 0)}")
    lines.append(f"- TODO / FIXME / HACK / XXX markers: {payload.get('todo_count', 0)}")
    lines.append("")

    lines.append("## Ignored paths")
    lines.append("")
    for p in payload.get("ignored_paths", []):
        lines.append(f"- `{p}`")
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_build(cfg: Config, args: argparse.Namespace) -> int:
    scan = _scan(cfg.repo_root)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "built_at": _now_iso(),
        "git_sha": _git_sha(cfg.repo_root),
        "repo_root": str(cfg.repo_root),
        **scan,
    }

    json_path = cfg.codebase_map_path
    md_path = cfg.codebase_map_md_path

    if json_path.exists() and not args.force:
        sys.stderr.write(
            f"codebase map already exists at {json_path}; pass --force to rebuild.\n"
        )
        return 2

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    md_path.write_text(_render_markdown(payload))

    summary = {
        "built": str(json_path.relative_to(cfg.repo_root)),
        "markdown": str(md_path.relative_to(cfg.repo_root)),
        "languages": len(payload["languages"]),
        "manifests": len(payload["package_manifests"]),
        "entry_points": len(payload["entry_points"]),
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    json_path = cfg.codebase_map_path
    if not json_path.exists():
        print(
            json.dumps(
                {
                    "exists": False,
                    "message": f"no codebase map at {json_path.relative_to(cfg.repo_root)}. Run /prd-map.",
                },
                indent=2,
            )
        )
        return 0

    try:
        payload = json.loads(json_path.read_text())
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"{json_path}: invalid JSON: {exc}\n")
        return 2

    built_at = payload.get("built_at")
    age_days: Optional[float] = None
    if built_at:
        try:
            built_dt = datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            age_days = (datetime.now(timezone.utc) - built_dt).total_seconds() / 86400
        except ValueError:
            age_days = None

    git_sha_built = payload.get("git_sha")
    git_sha_now = _git_sha(cfg.repo_root)
    commits_ahead: Optional[int] = None
    if git_sha_built and git_sha_now and git_sha_built != git_sha_now:
        try:
            result = subprocess.run(
                [
                    "git",
                    "rev-list",
                    "--count",
                    f"{git_sha_built}..HEAD",
                ],
                cwd=str(cfg.repo_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip().isdigit():
                commits_ahead = int(result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            commits_ahead = None

    stale = False
    reasons: list[str] = []
    if age_days is not None and age_days > args.max_age_days:
        stale = True
        reasons.append(f"age_days={age_days:.1f} > max_age_days={args.max_age_days}")
    if commits_ahead is not None and commits_ahead > args.max_commits_ahead:
        stale = True
        reasons.append(
            f"commits_ahead={commits_ahead} > max_commits_ahead={args.max_commits_ahead}"
        )

    print(
        json.dumps(
            {
                "exists": True,
                "path": str(json_path.relative_to(cfg.repo_root)),
                "built_at": built_at,
                "age_days": round(age_days, 2) if age_days is not None else None,
                "git_sha_built": git_sha_built,
                "git_sha_now": git_sha_now,
                "commits_ahead": commits_ahead,
                "stale": stale,
                "stale_reasons": reasons,
            },
            indent=2,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prd_map_runner.py",
        description="Build and inspect the prd-os codebase map.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="Scan the repo and write the map.")
    p_build.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing codebase map.",
    )
    p_build.set_defaults(func=cmd_build)

    p_status = sub.add_parser(
        "status", help="Report whether the current map is fresh."
    )
    p_status.add_argument(
        "--max-age-days",
        type=float,
        default=30.0,
        help="Warn when the map is older than this many days (default: 30).",
    )
    p_status.add_argument(
        "--max-commits-ahead",
        type=int,
        default=50,
        help="Warn when HEAD is more than N commits ahead of git_sha at build (default: 50).",
    )
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
