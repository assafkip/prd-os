---
description: Archive the active PRD (terminal state)
allowed-tools: Bash
---

Move the active PRD to `archived`, the terminal state. Archiving stamps
`status: archived` and a fresh `updated_at` onto the spec, then clears the
active-PRD state so a new PRD can start. Any PRD status can be archived
(`idea`, `draft`, `in-review`, `approved`).

This transition is intentionally not gated: archiving is how you retire a PRD,
including one you decided not to pursue. The permanent record is the spec on
disk plus its findings JSONL; archiving does not delete either.

Steps:

1. Confirm a PRD is active:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prd_runner.py" status
```

If no PRD is active, tell the author and stop.

2. Archive it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prd_runner.py" archive
```

Output is JSON:
- `{"archived": "<prd-id>"}` — archived and active state cleared.
- `{"archived": "<prd-id>", "note": "already"}` — was already archived.

Exit codes:
- `0` — archived (or already archived).
- `2` — no active PRD.
