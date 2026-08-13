# W1MER — Layered Codebase Docs

## Problem

A codebase documented once and never updated becomes noise: agents read stale
docs, then must dig into source to reconstruct reality. But rewriting the
whole codebase docset on every change breaks prompt-cache prefixes and burns
tokens.

Solution: the codebase docset stays **stable overall** while **details keep
updating** — two physically separated layers.

## Two layers

### Stable layer — `.w1mer/codebase/`

Long-lived project map, fixed read order, five files only:

```
STACK → STRUCTURE → ARCHITECTURE → INTEGRATIONS → CONVENTIONS
```

- Designed to change rarely.
- The **fixed read order** yields a stable prompt prefix that reliably hits
  provider context caches.
- Content: stack/toolchain, crate layout, architecture & data flow, module
  boundaries/contracts, conventions.
- **Nothing else lives here.** Volatile content belongs in `detail/`.

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
- A periodic **compact** merges accumulated deltas into the stable `codebase/`
  docs, then clears the changelog.
- One cache invalidation per compact; long stable periods in between → fewer
  cache misses and less extra output.

## Compact flow

```
reviewer notes ── accumulate in detail/CHANGES.md ── compact ──> codebase/ stable docs updated
                                          ^                              |
                                          └──── changelog cleared ───────┘
```

A periodic **compact** pass (orchestrator or a dedicated agent) turns the
accumulated notes into a merge draft for human/agent confirmation before
applying.
