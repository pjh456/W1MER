# W1MER

**W1MER** — *Only one Writer, Many Explorers Read* — is an orchestration
paradigm for long-running autonomous agent teams working on a shared
codebase.

Named after the classic SWMR (Single Writer / Multiple Readers) concurrency
model, it puts two hard constraints at the center of multi-agent scheduling:

- **One writer at a time.** A single implementer owns compilation and commits
  (compile exclusivity). No two agents build or write code concurrently.
- **Many readers in parallel.** Explorers and reviewers are read-only and
  run in parallel batches — investigation and review never wait for the
  compiler.

## Why

Most multi-agent frameworks (GSD, orchestrator-workers, ...) assume "more
parallelism is better" and fan out writers freely. That model breaks on
compiled codebases (Rust, C++, ...) where:

- build artifacts and `target/` directories conflict between concurrent builds,
- the compiler is the shared bottleneck,
- speculative edits by multiple writers create merge churn and review blind spots.

W1MER trades unbounded write parallelism for a **pipelined, single-writer**
rhythm: each batch advances the project on three fronts at once without ever
racing the writer.

## Core mechanisms

### Pipelined three-batch scheduling

Each batch runs three agents in parallel, each at a different pipeline stage:

```
Reviewer  ──▶ re-reviews the task completed last batch
Implementer ──▶ completes the current task  (only writer; compiles & commits)
Explorer  ──▶ investigates the next task    (read-only, writes a plan)
```

Reviewer's `cargo test` recheck is staggered against the implementer's
compilation. Investigation lead time (explorer) guarantees the next
implementer can start writing code immediately.

### Review independence & fix loop

Reviewer conclusions must **not** rely on the changed code self-verifying.
Correctness is derived from untouched surrounding code, existing call
contracts, test expectations, and spec semantics. `git diff` only locates the
change; correctness is judged from the unchanged side. Each change is probed
with: *"if this were wrong, who would notice — can existing tests catch it?"*

Problems found → next batch runs a **fixer** (writes only the fixes listed in
the review doc) → the batch after re-reviews the fix itself (closed loop).
The review stage is never skipped.

### Interrupt recovery

An agent that exits abnormally / returns empty is treated as an *unfinished
stage*, recovered per role: explorer → relaunch; implementer → fixer;
reviewer → spawn an extra reviewer. Before each batch, outstanding
interruptions are topped up before new work is pushed.

### Rolling hierarchical IDs

Tasks use unbounded rolling numeric IDs (`05`, `05.1`, `05.1.1`, ...). Any
node can branch deeper on demand — e.g. a reviewer finding a follow-up
problem appends `05.1`, `05.1.1` — without touching the main IDs, keeping the
main flow and cross-references stable. Ordering is pre-order traversal
(parent before children).

### Metadata-driven archive pipeline

All planning archives (`performance/`, `bug_reason/`, `review/`, `results/`,
...) are declared as a **type registry**: each type states its directory,
file-naming pattern, and state machine. A small CLI (`init` / `new` / `set`
/ `list` / `build` / `sync`) CRUDs entries, and `build` regenerates the
INDEX files from the content files (single-direction index — content is the
source of truth, never two-way markdown sync). States live in YAML frontmatter.

### Layered codebase docs (cache-friendly)

Codebase docs split into two layers:

- **Stable layer** (fixed read order `STACK → STRUCTURE → ARCHITECTURE →
  INTEGRATIONS → CONVENTIONS`): long-lived maps, designed to change rarely —
  a stable prompt prefix that hits provider context caches.
- **Dynamic layer** (recent changes, field details): never inserted into the
  stable prefix; appended after it or read on demand.

Writes are throttled: reviewers record only a small `## Architecture Impact`
delta into a changelog; a periodic **compact** merges deltas into the stable
docs and clears the changelog. One cache invalidation per compact, long
stable periods in between.

## Project layout

```
W1MER/
├── README.md           # this file
├── docs/               # paradigm & operating specs
│   └── ...
└── tools/              # metadata archive CLI (init/new/set/list/build/sync)
```

## Status

Design phase. The paradigm spec lives in `docs/`; the archive CLI is
skeleton — feedback and contributions welcome.
