# W1MER — Layered Codebase Docs

## Problem

A codebase documented once and never updated becomes noise: agents read stale
docs, then must dig into source to reconstruct reality. But rewriting the
whole codebase docset on every change breaks prompt-cache prefixes and burns
tokens.

Solution: the codebase docset stays **stable overall** while **details keep
updating** — two physically separated layers, and the stable layer itself
splits into a cheap global prefix + per-module docs.

## Layers

### Stable layer — `.w1mer/codebase/`

- `INDEX.md` — module registry (`module | doc | last_sync | note`); the
  `w1mer sync` tag namespace.
- Overview docs, fixed read order, project-wide, cheap to refresh:
  `STACK → STRUCTURE → CONVENTIONS`.
- One doc per module (`<module>.md`): responsibilities, key types, internal
  data flow, contracts, invariants.

- The **fixed read order** of the overview docs yields a stable prompt
  prefix that reliably hits provider context caches; module docs are read on
  demand, only for the module touched.
- Module docs are generated **one mapper per module** (map-reduce), so a
  mapper never reads the whole codebase.

### Dynamic layer — `.w1mer/detail/`

A separate series directory following the standard INDEX + detail-files
pattern, managed by the CLI (`w1mer new detail`). Holds everything too
volatile for the stable layer: recent changes, field-level deep dives,
concerns/known debt, architecture-impact deltas.

- Never inserted into the stable prefix — read on demand via the CLI.
- `CHANGES.md` is the changelog where reviewers record architecture-impact
  deltas during main batches.

## Write throttling

- In every main batch, the Reviewer records a short **architecture-impact
  note** into `detail/CHANGES.md` (what changed, which contracts moved).
- A periodic **compact** merges accumulated deltas into the target module
  doc, then clears the changelog.
- One cache invalidation per compact; long stable periods in between → fewer
  cache misses and less extra output.

## Compact flow

```
reviewer notes ── accumulate in detail/CHANGES.md ── compact ──> codebase/<module>.md
                                           ^                        |
                                           └──── changelog cleared ─┘
```

A periodic compact (`w1mer sync --apply`, or reviewed by the orchestrator
first) turns the accumulated notes into module-doc updates. Delta lines are
tagged with their target module, e.g. `- [gc] contract X moved` (module
names from `codebase/INDEX.md`). Untagged lines are listed as unassigned for
manual triage. When a doc drifts too far to patch, re-map that module with
one mapper (see `references/map-codebase.md`).
