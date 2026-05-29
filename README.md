# prd-os

Standalone Claude Code plugin for PRD authoring and DSSE issue execution.

One plugin, one install, one version. Turns rough ideas into reviewable PRDs, gates them through Codex review, triages findings, decomposes into atomic issue specs, and executes those issues with scope enforcement and receipts.

## Questions people ask

**How do I stop Claude Code from creeping outside the task scope?**
Snapshot the allowed files into state before work starts, then block edits to anything else. prd-os does this at `/issue-approve`: it freezes the `allowed_files` set, and a hook blocks any Edit or Write outside it. Widening scope takes an explicit `/issue-amend` with a written reason, logged to the audit trail.

**How do I get a real PRD workflow inside Claude Code instead of a chat thread?**
prd-os adds eleven slash commands that move an idea through a state machine: draft, review, triage, approve, split into atomic issues, then execute each one with scope enforcement and receipts. The PRD ends up as the permanent record of what shipped and what changed mid-build.

**How do I keep Claude from auto-applying code-review findings?**
Make triage explicit. prd-os never auto-applies a Codex finding. Every finding needs a disposition: accepted, rejected, or deferred. Rejections and defers require a written rationale on disk. Closeout stays blocked until every in-scope finding is dispositioned.

**How is prd-os different from a PRD template or asking ChatGPT to write a PRD?**
A template is a document. prd-os is a state machine with gates. It runs an independent Codex reviewer with cold context, enforces file scope at write time, caps review recursion, and writes a full audit trail. The free plugin here does this. The paid build adds worked examples, setup guides, and support: https://claudedaddy.gumroad.com/l/ayfunm

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

## Design guardrails

### Bounded recursion
Review is capped per issue. Default: 2 standard rounds, 1 adversarial round. Hitting the cap stops Codex from being called again. Override is explicit opt-in via `ISSUE_ALLOW_REVIEW_REPEAT=1`, one retry at a time. Closeout never auto-relaunches review. The stop-hook uses signature exhaustion to cap same-signature firings, so a misbehaving hook cannot loop forever.

### Scope is snapshotted, not live
`allowed_files`, `required_checks`, and `disallowed_files` are snapshotted into state at `/issue-approve`. Mid-issue spec edits do not expand the review surface. Widening scope requires `/issue-amend` with a non-empty `--reason`. Amend re-snapshots, clears `verified` + `reviewed` receipts (forcing re-verification), and appends a before/after entry to the permanent audit record.

### Triage is explicit, rationale is required
Codex findings are never auto-applied. Every finding needs a disposition:
- `accepted`: fix now, inside `allowed_files`
- `rejected`: Codex misread scope or hallucinated. Rationale required.
- `deferred`: valid but out of contract. Rationale required. Optional `--followup-issue-id` to link a tracker.

Closeout is gated on zero in-scope pending findings. Out-of-scope findings (paths outside `allowed_files`) are flagged `out_of_scope=true` by the writer and filtered from the gate as informational only.

### Full audit trail on disk
- Every finding is JSONL: `id`, `source` (codex-review vs codex-adversarial), `severity`, `affected_path`, `disposition`, `rationale`, `created_at`, `resolved_at`.
- Every amendment carries a timestamp, reason, and before/after snapshot.
- On close, amendments flush to an `## Amendments` footer on the spec. The spec ends up as the permanent record of what shipped, what changed mid-build, and why.

### Independent reviewer
Codex runs as a separate CLI process with cold context. No shared conversation history with the builder. The scope filter (`allowed_files`) is passed into the Codex focus text inline, so findings stay inside the contracted surface. The builder cannot silence the reviewer. Findings land on disk before triage.

### Known limits (what is NOT automated)
- **New PRDs from findings are manual.** Deferred findings can carry a `--followup-issue-id`, but promoting a finding to a full PRD is a founder action via `/prd-start`. The system grows the backlog by surfacing; humans decide what becomes a PRD.
- **Cross-PRD conflict detection is manual.** If Codex flags something that was explicitly rejected in a sibling PRD, nothing cross-references that. The rationale is traceable on disk, but the catch is yours.
- **Amend is not a retry button.** If the spec was fine and the implementation was wrong, fix the implementation. Amend is for genuine mid-build spec corrections.

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

---

I built this to run my own PRDs and issues through Claude Code, and I open-sourced it. This repo is the free plugin.

The paid **prd-os kit** adds the worked end-to-end example, per-OS setup guides, and a 210-test suite shipped green: https://claudedaddy.gumroad.com/l/ayfunm

More kits for founders building on Claude Code: https://claudedaddy.gumroad.com

Want one wired to your own repo, or a larger Claude Code reliability system built around it? I build these for teams. Book a call: https://calendar.app.google/cMFvhvDsfi9iyWYy9
