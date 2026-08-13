# W1MER — Scheduling

## Batch model

Development proceeds in **main batches** (normal three-way concurrency) and
**sub batches** (single-fixer repair rounds). A batch is centered on
completing one task.

### Main batch

Three agents run in parallel, one per role, each at a different pipeline
stage:

```
Reviewer     re-reviews the task completed last batch   (read-only)
Implementer  completes the current task                 (only writer)
Explorer     investigates the next task                 (read-only)
```

- **Single-writer exclusivity**: the implementer is the only agent that
  compiles and commits. No other agent builds or runs tests while it works.
- The reviewer's re-review targets the **previously committed** task, not the
  current implementer's work-in-progress.
- The explorer is read-only and never touches the compiler, so it can share
  the batch with the implementer freely.

### Sub batch

Entered when a main batch hits an abnormal condition. A single **Fixer**
replaces the implementer:

1. Reviewer found issues → Fixer fixes them and updates the review doc.
2. Implementer interrupted mid-way → Fixer finishes the half-done work and
   reports.

Once fixed, development returns to the main batch; the next Reviewer
re-reviews the Fixer's committed fixes. Depending on complexity, sub batches
can nest inside sub batches (e.g. when a fix introduces a new problem).

## Interrupt recovery

An agent that exits abnormally / returns empty is treated as an *unfinished
stage* and recovered by role:

| Role | Recovery |
|------|----------|
| Explorer / Reviewer | relaunch |
| Implementer / Fixer | dispatch a Fixer to finish the half-done work, taking over the implementer's reporting duty |

## Context discipline

- The orchestrator receives **compressed conclusions only** from sub-agents:
  changed-file list (one line each), verification result, commit hash +
  message, leftover risks. Never full code or analysis bodies.
- Sub-agents write full content directly to archive documents; the
  orchestrator gets a "written to <doc>" confirmation.

## Output format

Each sub-agent reports:

```
Files changed:   <path> — <one line what changed>
Verification:    <tests passed / bench before→after>
Commit:          <hash> <message>
Risks left:      <none | ...>
Pre-existing:    <bugs found not caused by this task, orchestrator-only>
```
