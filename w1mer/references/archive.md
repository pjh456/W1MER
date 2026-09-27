# W1MER — Document Archive

The archive follows a **single-index + many-detail-files** pattern: each
document type keeps its detail files as the source of truth and a generated
INDEX for lookup. This pattern applies to any recurring document series —
performance results, review reports, implementation outcomes, bug root causes.

Agents interact with the archive **through the CLI**, never by hand-editing
indexes.

## CLI reference

```sh
w1mer init                       # scaffold the planning directory from templates
w1mer new <type> [--parent <id>] [--title "..."] [--doc "..." (task)]
                                  [--slug <text>] [--domain <d> (perf)]
                                  [--section perf|bug|feature|infra|backlog (task)]
                                  [--state <s>]   # default: type's first state
w1mer set <type> <id> --state <state> [--effect "..." (task rows)]
                                   [--register-if-missing (task)
                                    --title "..." --doc "..." --section <s>]
w1mer batch-start <task> [--type main|sub]  # record batch boundary, gate completeness
w1mer batch-end <task>                       # close current task (end = HEAD)
w1mer ensure <task> [--type main|sub]        # idempotent batch-start (subagent startup)
w1mer role-join <task> <role>                # record role liveness
w1mer show <task> [--stat] [--file <p> --at base|end]
w1mer status                                 # current batch state + completeness
w1mer list [--type <type>] [--sort tree]
w1mer build                      # regenerate all INDEX files
w1mer sync [--apply]             # compact deltas into stable codebase docs
```

`task` rows live in `ROADMAP.md`; `set task 01 --state done --effect "+22%"`
updates a row directly. With `--register-if-missing`, a missing row is
registered first (state `todo`, then the given `--state`/`--effect` applied),
so a reviewer can append a follow-up sub-id in one call. Other types are
per-entry files.

Batch state (`batch-start` / `batch-end` / `ensure` / `role-join` / `show` /
`status`) lives in `.w1mer/STATE.json` — a per-task ledger of base/end commits,
role liveness, and timing. It is machine-maintained: agents only touch it
through the CLI. See `references/scheduling.md` (Batch lifecycle) for the
protocol and the completeness gate.

Cell escaping: table cells are escaped on write (`|` → `\|`, `\` → `\\`)
and unescaped on read, so titles/effects may contain `|`. Hand-editing a row
with a raw `|` in a cell breaks the row — go through the CLI.

## CLI defaults

`schema.yaml` can pre-set subcommand flags under a top-level `defaults:`
block. Precedence: **command line > `defaults:` > intrinsic default**. Example:

```yaml
defaults:
  set:
    register_if_missing: true   # `set task` auto-registers a missing row
  new:
    section: perf
```

Only defaultable flags participate (currently `new`/`set`: `section`, `state`,
`register_if_missing`). A command-line flag always wins over the default.

## Type registry

Types are declared in `.w1mer/schema.yaml` (self-contained with the archive).
Each type declares:

```yaml
types:
  review:
    dir: "review"               # relative to .w1mer/
    id: "R_{roadmap}"            # derived from parent task id; "." → "_"
    file: "{id}.md"
    index: [id, task, state, doc]   # INDEX table columns
    states: [pending, ok, issues, fixed, re-reviewed]
  bug:
    dir: "bug_reason"
    id: "B{seq:03}"              # auto-incrementing sequence
    file: "{id}_{slug}.md"
    index: [id, title, error, module, doc]
    states: [open, fixed]
```

- `id` may reference a parent task (`{roadmap}`) for derived numbering, or a
  sequence (`{seq:03}`) for auto-increment.
- `file` names the detail file; `slug` is derived from the title.

## Content files & state

Each detail file carries YAML frontmatter as its source of truth:

```yaml
---
id: R_05_1
title: Re-review task 05.1
state: ok
commit: abc1234
date: 2026-08-14
---
```

`w1mer set` updates only the frontmatter `state` field.

## Single-direction index

INDEX files are **build artifacts**: `w1mer build` regenerates them by
scanning the content files. Content is the source of truth — never two-way
markdown sync. Editing an INDEX by hand is overwritten at next build.

## Rolling hierarchical IDs

Tasks use **unbounded rolling numeric IDs**: `05`, `05.1`, `05.1.1`, ...
Any node branches deeper on demand (e.g. a reviewer appending follow-up
problems) without touching parent IDs.

- Ordering is pre-order traversal: parent first, children right behind.
- Comparison: split the id on `.`, compare element-wise; a shorter prefix
  sorts before its descendants.
- Sub-batch fixes are sub-ids of the task being repaired.

## Cross-references

Derived ids keep cross-references stable across review / results / bug docs:
a task `05.1` links to `R_05_1.md` and its result record. Never batch-renumber
existing ids.
