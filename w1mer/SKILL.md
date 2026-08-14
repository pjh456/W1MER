---
name: w1mer
description: >-
  W1MER (Only one Writer, Many Explorers Read) orchestration paradigm for
  long-running autonomous agent teams on compiled codebases. Use when running
  multiple agents on a Rust/C++/compiled project — schedules main batches
  (reviewer + implementer + explorer), single-writer compile exclusivity,
  sub-batch fixer loops, rolling hierarchical task IDs, and a metadata-driven
  document archive CLI. Triggers: "w1mer", "main batch", "single writer",
  "multi-agent scheduling", "orchestrate agents", "compile exclusivity",
  "map codebase", "parallel mappers".
---

# W1MER — Only one Writer, Many Explorers Read

An orchestration paradigm for long-running autonomous agent teams working on
a shared codebase, built for **compiled languages** (Rust, C++, ...) where
the compiler is a shared, exclusive bottleneck.

Two hard constraints drive everything:

- **One writer at a time.** A single implementer owns compilation and commits
  (compile exclusivity). No two agents build or write code concurrently.
- **Many readers in parallel.** Explorers and reviewers are read-only and run
  in parallel batches — investigation and review never wait for the compiler.

## When to use

- Running multiple agents on a **compiled** project.
- You want autonomous long-running development (YOLO mode) without agents
  fighting over the compiler.
- You need cheap, token-efficient coordination documents.

## Roles

| Role | Duty | Reads | Writes |
|------|------|-------|--------|
| **Reviewer** | Re-reviews the task completed last batch, based on **git-committed** code (never working-tree half-done work) | code, git history | review docs |
| **Implementer** | Completes the current task; compiles & commits | code, plans | code, commits |
| **Explorer** | Investigates the next task (read-only, writes a plan) | code, docs | plan docs |
| **Fixer** | Fixes issues from review / finishes half-done work (sub-batch only) | code, review docs | code, commits |

## Scheduling loop

**Main batch** (normal operation) — three agents in parallel, each at a
different pipeline stage:

```
Reviewer     re-reviews last batch's committed task   (read-only)
Implementer  completes the current task               (only writer)
Explorer     investigates the next task               (read-only)
```

**Sub batch** (on problems) — a single Fixer replaces the implementer:

1. Reviewer found issues → Fixer fixes them, updates the review doc.
2. Implementer interrupted mid-way → Fixer finishes the half-done work.
3. Return to main batch; the next Reviewer reviews the Fixer's committed fixes.
4. Sub batches can nest when a fix introduces a new problem.

**Interrupt recovery** — an agent that exits abnormally / returns empty is an
*unfinished stage*:

- Explorer / Reviewer → relaunch.
- Implementer, working tree clean (`git status` shows no half-done work) →
  relaunch the Implementer to redo the task.
- Implementer, working tree dirty / Fixer → dispatch a Fixer to finish the
  half-done work (a repair job stays a Fixer's job).

## Task IDs

Tasks use **unbounded rolling hierarchical IDs**: `05`, `05.1`, `05.1.1`, ...
Any node can branch deeper on demand (e.g. a reviewer finding follow-up
problems appends `05.1`, `05.1.1`) without touching parent IDs — keeping
cross-references stable. Ordering is pre-order traversal (parent first,
children right behind).

## Document archive

Planning documents use a **single-index + many-detail-files** pattern,
managed by a lightweight CLI (`w1mer`). Agents operate through the CLI, never
by hand-editing indexes:

```sh
w1mer init                    # scaffold the planning directory
w1mer new <type> [--parent <id>] [--title "..."]   # create an entry
w1mer set <type> <id> --state <state>              # update state
w1mer list [--type <type>]                         # list entries
w1mer build                                        # regenerate all INDEX files
w1mer sync [--apply]                               # compact deltas into stable docs
```

The type registry lives in `w1mer.yaml` (directory, file-naming pattern,
state machine). States live in YAML frontmatter of content files — content is
the source of truth; INDEX files are build artifacts (single-direction sync).

## Layered codebase docs

Codebase docs split into two layers for provider context-cache friendliness:

- **Stable layer** (fixed read order `STACK → STRUCTURE → ARCHITECTURE →
  INTEGRATIONS → CONVENTIONS`): long-lived maps, rarely change — a stable
  prompt prefix that hits caches.
- **Dynamic layer** (recent changes, field details): isolated, never inserted
  into the stable prefix; read on demand via the CLI.

Writes are throttled: the Reviewer records a short architecture-impact note in
its review doc; a periodic **compact** merges deltas into the stable docs and
clears the changelog.

## Map codebase

Initialize or refresh the stable layer (`.w1mer/codebase/`) with **3 parallel
read-only mappers**, one per focus area:

```
mapper-tech  → STACK.md, INTEGRATIONS.md
mapper-arch  → ARCHITECTURE.md, STRUCTURE.md
mapper-conv  → CONVENTIONS.md
```

Mappers write documents directly and return confirmations only. Run after
`w1mer init` and after major refactors. Full spec in
`references/map-codebase.md`.

## Details

Full operating specs live in `references/` — read them before your first
orchestration, then as needed:

- `references/roles.md` — role capabilities, validation, commit discipline.
- `references/scheduling.md` — batch lifecycle, interrupt recovery, context.
- `references/archive.md` — CLI reference, type registry, ID rules.
- `references/codebase.md` — layered docs, compact flow, cache strategy.
- `references/map-codebase.md` — parallel stable-layer mapping.

Host-specific agent definitions live in `hosts/` (opencode, claude-code),
shipped inside the skill. Install the skill (e.g. `npx skills add <owner>/w1mer
-a opencode -g` or copy this directory), then register host agents and the CLI:

```sh
python3 <skill-dir>/scripts/w1mer.py install [--host opencode|claude-code|all]
                                            [--link] [--bin-dir <dir>]
```

`install` copies each host's agent definitions to its agent directory
(opencode → `~/.config/opencode/agents/`, claude-code → `~/.claude/agents/`)
and puts a `w1mer` launcher on PATH (symlink the skill script with `--link`).
`install --list` prints the target locations without changing anything.
