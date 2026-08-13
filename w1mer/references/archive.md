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
w1mer new <type> [--parent <id>] [--title "..."] [--slug <text>]
w1mer set <type> <id> --state <state>
w1mer list [--type <type>] [--sort tree]
w1mer build                      # regenerate all INDEX files
w1mer sync [--apply]             # compact deltas into stable codebase docs
```

## Type registry

Types are declared in `w1mer.yaml` at the project root. Each type declares:

```yaml
types:
  review:
    dir: ".w1mer/review"
    id: "R_{roadmap}"            # derived from parent task id; "." → "_"
    file: "{id}.md"
    index: [id, task, state, doc]   # INDEX table columns
    states: [pending, ok, issues, fixed, re-reviewed]
  bug:
    dir: ".w1mer/bug_reason"
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
