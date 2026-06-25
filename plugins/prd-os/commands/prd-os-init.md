---
description: Initialize prd-os in this repo (writes .prd-os/config.json)
allowed-tools: Bash
---

One-time bootstrap. Writes `.prd-os/config.json` with the default directory
layout so every runner (`prd_runner.py`, `issue_runner.py`, `prd_split.py`,
`findings_writer.py`) resolves the same paths. The runners refuse to operate
without this file; this command creates it.

Idempotent and non-destructive: if a config already exists it is left untouched.
Pass `--force` only to overwrite a customized config back to defaults.

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prd_os_init.py"
```

Output is JSON:
- `{"initialized": "<path>"}` — config written and validated.
- `{"exists": "<path>", "note": "..."}` — already initialized; nothing changed.

Exit codes:
- `0` — initialized, or already initialized.
- `2` — repo root could not be resolved (run inside a git repo), or the written
  config failed to load.

After success, the author can run `/prd-start <slug>` to create the first PRD.
