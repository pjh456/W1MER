# W1MER — Roles

Four agent roles, one of each in a main batch. Capabilities below are the
contract; host adapters (`hosts/`) map them to the host's agent mechanism.

## Role table

| Role | Duty | Permission | Output |
|------|------|-----------|--------|
| **Reviewer** | Re-review the task completed last batch, based on **git-committed** code | read-only: code, git history, tests **not run** | review doc |
| **Implementer** | Complete the current task; compile & commit | read + write + commit | code + commit + results |
| **Explorer** | Investigate the next task, write a plan | read-only | plan doc |
| **Fixer** | Fix review issues / finish half-done work (sub-batch only) | read + write + commit | code + commit + updated review doc |

## Reviewer

- **Review basis is git-committed code, never working-tree half-done work.**
  If the work isn't committed, there is nothing to review yet.
- Read-only: reads code and git history, writes the review doc. **Does not
  compile or run tests** — the implementer self-tests before committing.
- In every main batch, also records a short **architecture-impact note** in
  the review doc (what changed, which contracts moved).
- Review conclusions must **not** rely on the changed code self-verifying:
  correctness is derived from the unchanged side — surrounding untouched code,
  existing call contracts, test expectations, spec semantics. `git diff` only
  locates the change. For each change ask: *"if this were wrong, who would
  notice — can existing tests catch it?"*

## Implementer

- The **only writer**. Never runs concurrently with any other writing agent.
- Self-verifies before committing: build + relevant tests + lints (e.g.
  `cargo build` + `cargo test -p <crate>` + no new clippy warnings).
- Commits atomically: one logical change = one commit, semantic message, no
  internal issue numbers.
- Updates ROADMAP entry state (`[x]` + date + commit) and writes measured
  results to the archive.

## Explorer

- Read-only. Investigates the **next** task before it becomes current, so the
  next implementer can start writing immediately (investigation lead time).
- Output: plan doc with approach, risk list, change surface, expected value —
  written directly to the archive (the orchestrator receives only a short
  confirmation, not the body).

## Fixer

- Exists only in **sub batches**, triggered by a problem. Replaces the
  implementer for that round.
- Fixes **only** what the review doc lists — no new development.
- After fixing, updates the review doc state and commits. The next main-batch
  Reviewer re-reviews the fix itself (closed loop).

## Interrupt recovery

An agent that exits abnormally / returns empty is an *unfinished stage*:

| Role | Recovery |
|------|----------|
| Explorer / Reviewer | relaunch |
| Implementer, working tree clean | relaunch the Implementer — nothing half-done to finish |
| Implementer, working tree dirty | dispatch a Fixer to finish the half-done work (takes over the implementer's reporting duty) |
| Fixer | dispatch a Fixer to resume the repair |

On main-batch recovery, check the working tree (`git status`) first: clean →
relaunch the Implementer (no half-done work to fix); dirty → dispatch a Fixer
to finish the half-done work. Use a Fixer whenever a repair is the right job.

## Pre-existing problems

If an agent discovers a bug **not** introduced by the current task, report it
to the orchestrator only — never write it into archive documents (it would
pollute the task's effect record).
