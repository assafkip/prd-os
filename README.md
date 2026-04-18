# prd-os

Standalone Claude Code plugin for PRD authoring and DSSE issue execution.

One plugin, one install, one version. Turns rough ideas into reviewable PRDs, gates them through Codex review, triages findings, decomposes into atomic issue specs, and executes those issues with scope enforcement and receipts.

## What it ships

**PRD side (`/prd-*`):**
- `/prd-start` — capture a rough idea as a PRD draft
- `/prd-review` — send draft through `/codex:review`
- `/prd-approve` — mark PRD approved after findings are triaged
- `/prd-triage` — dispose of findings (accept / reject / defer with rationale)
- `/prd-split` — decompose an approved PRD into atomic issue specs

**Issue side (`/issue-*`):**
- `/issue-start` — load a spec and enter planning mode
- `/issue-approve` — transition to in-progress after explicit approval
- `/issue-verify` — mark verified receipt (tests / build passing)
- `/issue-review` — send branch through `/codex:review` and `/codex:adversarial-review`
- `/issue-amend` — re-snapshot scope mid-issue (clears verified + reviewed receipts)
- `/issue-closeout` — triage findings, flip spec to closed, clear state

**Hooks:**
- Scope enforcement on `Edit | Write | NotebookEdit` — blocks paths outside `allowed_files` in the active spec.
- Stop gate — fires only when a PRD or issue is active with missing receipts. Uses `stop_hook_active` + signature-exhaustion counter to prevent infinite loops.

## Install

Add the marketplace to your `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "prd-os": {
      "source": { "source": "github", "repo": "assafkip/prd-os" }
    }
  },
  "enabledPlugins": {
    "prd-os@prd-os": true
  }
}
```

Reload Claude Code. The 11 commands appear.

## Prerequisites

- **Codex CLI** installed and available as `/codex:review` / `/codex:adversarial-review`. The review gates call Codex directly.
- **Python 3** on `PATH` for hooks and runners.

## Per-repo configuration

Drop a `.prd-os/config.json` in the target repo to override defaults:

```json
{
  "prds_dir": ".prd-os/prds",
  "issues_dir": ".prd-os/issues",
  "findings_dir": ".prd-os/findings",
  "state_dir": ".claude/state"
}
```

Without a config, defaults work for any generic repo layout.

## Version

0.2.0 — merges the prior `prd-os` + `kipi-dsse` plugins into one unit.

## License

MIT.
