# W1MER

*Only one Writer, Many Explorers Read*

**English** | [中文](docs/README-zh.md)

**W1MER** is an orchestration paradigm for long-running autonomous agent
teams working on a shared codebase.

Named after the classic SWMR (Single Writer / Multiple Readers) concurrency
model, it constrains multi-agent parallelism with two hard rules:

- **One writer at a time.** A single implementer owns compilation and commits
  (compile exclusivity). No two agents build or write code concurrently.
- **Many readers in parallel.** Explorers and reviewers are read-only and
  run in parallel batches — investigation and review never wait for the
  compiler.

## Why I built my own wheel

Most off-the-shelf multi-agent frameworks (GSD, orchestrator-workers, ...)
hold a "the more parallelism the better" philosophy, letting multiple
write-capable agents run concurrently.

But when I used them on a Rust project, that philosophy became a burden —
agents fought over the compiler and the test runner, CPU usage stayed pinned,
and it even shut my machine down a few times! After all that churn, little
got done and a lot of tokens went to waste!

It's clear that on compiled-language projects (Rust, C++, ...), staying
simultaneously fast, token-efficient, and smooth-running is no easy feat —
though it's not an "impossible triangle" either!

Drawing on my experience building large Rust projects, I distilled a complete
multi-agent development framework for compiled codebases: keep compilation
exclusively owned by a single writer while running read-only work in
parallel, squeezing value out of what would otherwise be idle wait time. This
makes long-running, autonomous, efficient development of compiled projects
actually possible!

## Core mechanisms

### Three-concurrent pipelined scheduling

Development is divided into multiple **main batches** and **sub batches**;
each batch is centered on completing one task.

Normally, agents run in **main batches** with three-way concurrency. Each
**main batch** runs three agents in parallel, each with a distinct duty:

```
Reviewer:    re-reviews the task completed last batch (read-only, writes a review)
Implementer: completes the current task (only writer; compiles & commits)
Explorer:    investigates the next task (read-only, writes a plan)
```

> Put it elegantly: one batch holds the "past", the "present", and the
> "future" at once XD

### Review independence & fix loop

When a **main batch** hits an abnormal condition, development enters a
**sub batch** with a single Fixer:

1. When the Reviewer finds problems, the Fixer fixes them and updates the
   review document;
2. When the Implementer is interrupted mid-way, the Fixer finishes the
   half-done work and reports.

Once fixed, development returns to the **main batch**, and the Reviewer now
reviews the Fixer's fixes.

> Depending on complexity, **sub batches** can nest inside **sub batches**
> (when the Fixer fixes wrong)

### Interrupt recovery

An agent that exits abnormally or returns empty is treated as an *unfinished
stage*, recovered per role:

1. Explorer / Reviewer: relaunch
2. Implementer / Fixer: dispatch a Fixer agent to finish the half-done work,
   taking on the Implementer's reporting duty

### Rolling hierarchical IDs

Tasks use unbounded rolling numeric IDs (`05`, `05.1`, `05.1.1`, ...). Any
node can branch deeper on demand.

For example: when the Reviewer finds follow-up problems, it appends `05.1`,
`05.1.1`, keeping cross-references stable.

Ordering is pre-order traversal (parent first, children right behind).

### Standardized series-document handling

*Instead of having AI read one ever-growing document, split it into "one
index + many detailed documents"*

I generalized this pattern from real development, so I believe it applies to
any series of documents — performance results, review reports, implementation
outcomes, bug root causes, and more.

W1MER ships a reusable way to operate on such document series, wrapping the
create/read/update/delete of this complex yet efficient pattern into a
lightweight CLI — from creating a series, adding new entries, to rebuilding
the index.

Agents no longer need to read how to maintain this machinery one by one; they
participate directly through the CLI, staying crash-free and highly available
over the long run.

### Cache-friendly layered codebase docs

As a project iterates, the traditional "write codebase once, use everywhere"
approach actually adds noise.

In my view, a codebase doc should stay stable overall while its details keep
updating — this helps agents understand the current state better than sending
them back to stale docs that then force them to read even more source code.

W1MER's codebase docs are split into two layers:

- **Stable layer** (fixed read order `STACK → STRUCTURE → ARCHITECTURE →
  INTEGRATIONS → CONVENTIONS`): a long-lived project map, designed to change
  rarely. The fixed read order yields a stable prompt prefix that reliably
  hits provider context caches.
- **Dynamic layer** (recent changes, field details): fully isolated from the
  stable layer, forming its own document series, also queryable quickly via
  the CLI.

In every **main batch**, the Reviewer also watches for architecture changes,
summarizing a brief architecture-impact note in the review document.

Architecture docs are updated periodically: increments are merged into the
stable documents and the changelog is cleared. This reduces cache misses and
extra output, keeping the bill friendly.

## Project layout

```
W1MER/
├── README.md           # this file (English)
├── docs/               # paradigm & operating specs
│   └── README-zh.md    # Chinese README
└── tools/              # metadata archive CLI (init/new/set/list/build/sync)
```

## Status

Design phase. The paradigm spec lives in `docs/`; the archive CLI is a
skeleton — feedback and contributions welcome.
