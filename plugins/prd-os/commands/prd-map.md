---
description: Build a codebase map so PRDs are written with repo context, not blind
allowed-tools: Bash
argument-hint: "[--force]"
---

Scan the current repo and write a structured codebase map to
`.prd-os/codebase-map.json` (canonical) plus `.prd-os/codebase-map.md`
(human-readable sibling). The map records languages, package manifests,
entry points, top-level directory structure, and lint/test config. It is
facts-only — no pattern distillation, no opinions.

Why this exists:
- `/prd-start` produces PRDs. Without a codebase map, the author relies on
  memory about what already exists in the repo.
- Codex review in `/prd-review` can cite the map when checking scope.
- The map is versioned (`schema_version: 1`) and carries `built_at` +
  `git_sha` so `/prd-map status` can report staleness.

Security: dotenv files (`.env`, `.env.*`) have their names recorded but
contents are never read. `Reports/`, `node_modules/`, `.venv/`,
`__pycache__/`, and similar are skipped entirely.

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prd_map_runner.py" build ${1:-}
```

If the map already exists, the runner exits non-zero. Pass `--force` to
rebuild:

```bash
/prd-map --force
```

To check whether the current map is stale without rebuilding:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prd_map_runner.py" status
```

Default staleness thresholds: 30 days since last build, 50 commits ahead of
`git_sha` at build. Override with `--max-age-days` or `--max-commits-ahead`.

Do not auto-run this inside other commands. The map is a deliberate,
author-triggered artifact so PRD work stays grounded in a known snapshot
rather than an ever-shifting scan.
