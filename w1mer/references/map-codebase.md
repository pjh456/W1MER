# W1MER — Map Codebase

Initializes or refreshes `.w1mer/codebase/` (overview docs + per-module docs)
with a **map-reduce flow**: the orchestrator does the cheap global scan, one
read-only mapper per module does the deep dive, and the orchestrator
assembles the cross-module view from the reports. No mapper ever reads the
whole codebase.

## When to run

- After `w1mer init`, first time — full map.
- After significant refactors — re-map the affected module(s) only.
- Onboarding an unfamiliar codebase.

Skip for trivial codebases (<5 files) — write the docs by hand instead.

## Layout

```
codebase/
  INDEX.md       module registry (module | doc | last_sync | note)
  STACK.md       project-wide: toolchain, build, test (from manifests)
  STRUCTURE.md   project-wide: layout, module list, cross-module data flow
  CONVENTIONS.md project-wide: naming, error/logging patterns
  <module>.md    one per module: responsibilities, key types, data flow,
                 contracts, invariants
```

Read order for agents: `STACK → STRUCTURE → CONVENTIONS` (stable prefix),
then the doc of the module they touch (on demand).

## Map-reduce flow

1. **Map — orchestrator (cheap scan).** Read manifests, top-level layout,
   lint/format config; discover the modules. Write `STACK.md`,
   `CONVENTIONS.md`, the module list + scaffold of `STRUCTURE.md`, and the
   module table in `INDEX.md`.
2. **Map — mappers (parallel).** Spawn **one read-only mapper per module**
   (a read-only batch; no writer involved). Each mapper:
   1. Explores its module only (Glob / Grep / Read — never compiles).
   2. Writes `codebase/<module>.md` directly.
   3. Returns a short structured report: module, responsibilities, provides,
      consumes.
3. **Reduce — orchestrator.** Assemble the cross-module data flow in
   `STRUCTURE.md` from the reports (the orchestrator never needs the full
   module docs). Update `last_sync: <commit>` per module in `INDEX.md`.
   Commit the map if the project tracks `.w1mer/`.

## Re-map (incremental)

- A refactored module: spawn one mapper for that module only.
- `w1mer sync --apply` folds small reviewer deltas into the module docs
  directly; re-map when a doc drifts too far to patch.

## Mapper rules

- **Read-only against code.** Mappers explore and write only their module
  doc — never touch source, never compile, never commit.
- **Stay inside the module.** Boundaries/contracts only, no other modules'
  internals.
- **Never read forbidden files** (`.env`, credentials, keys, certs). Note
  existence only, never contents — output is committed to git.
- **Write current state only.** No temporal language, no speculation.
- **Always include file paths** with backticks — the docs guide navigation.
- **Be prescriptive** (used by future implementers): "use pattern X" beats
  "pattern X is used".
- **Return the structured report only** (~10 lines), never the full doc.
