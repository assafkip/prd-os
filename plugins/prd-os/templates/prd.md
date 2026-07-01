---
id: {{prd_id}}
title: {{title}}
status: idea
created_at: {{created_at}}
updated_at: {{created_at}}
owner: {{owner}}
reviewers: []
findings_path: .prd-os/findings/{{prd_id}}-findings.jsonl
---

# {{title}}

## Problem

<!-- What pain is this solving? Concrete, observed, measurable. -->

## Goals

- 

## Non-goals

- 

## Proposed approach

<!-- How. Keep it scannable. Diagrams go inline as fenced blocks. -->

## Alternatives considered

<!--
Options you weighed and rejected. For each: name the option, the tradeoff, and
the one reason it lost. This is what lets the reviewer tell a deliberately
rejected path from one you never saw. Empty here reads as "no alternatives
exist," which is rarely true.

- **<option>** — Rejected: <why>.
-->

## Scenarios

<!--
Concrete end-to-end walkthroughs of the approach in use. One per distinct path.
Actor, trigger, steps, outcome. Prove the approach against the real flow, not
the abstraction.

- **<scenario name>.** <actor> does <trigger>; <steps>; <outcome>.
-->

## Resolved decisions

<!--
The counterpart to Open questions: decisions now closed, each with its
rationale. This is the running record of what was settled and why, so it does
not get re-litigated later.

- **<decision>.** Decided: <choice>. Rationale: <why>.
-->

## Risks and rollback

<!-- Blast radius, migration cost, how to back out if this ships wrong. -->

## Open questions

- 

## Issues

<!--
After review and approval, populate the fenced JSON block below with one
entry per atomic issue. `prd_split.py` reads this block verbatim and writes
one issue spec per entry.

Required keys per entry:
  - id (kebab-case, unique across the repo)
  - title (non-empty string)
  - allowed_files (non-empty list of glob patterns)
  - required_checks (non-empty list, e.g. ["pytest -q"]). The runner's
    stop-gate checks that three receipts are marked (verified, reviewed,
    findings_triaged). Those receipts are meaningless unless the spec
    documents what must be verified, so an empty list is rejected.

Optional keys:
  - priority (default p1)
  - disallowed_files, required_reviews, acceptance

IDs must match the repo's issue naming convention and must not collide with
existing issue specs.
-->

```json
[]
```
