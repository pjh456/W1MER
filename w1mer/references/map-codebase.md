# W1MER — Map Codebase

Initializes or refreshes the **stable layer** (`.w1mer/codebase/`) by
analyzing the project with concurrent read-only mapper agents. Modeled on
GSD's map-codebase: parallel mappers write documents directly; the
orchestrator receives confirmations only.

## When to run

- After `w1mer init`, first time — generates the initial 5 stable docs.
- After significant refactors — refresh the stable map.
- Onboarding an unfamiliar codebase.

Skip for trivial codebases (<5 files) — write the docs by hand instead.

## Orchestration

Spawn **3 mapper agents in parallel** (a read-only batch; no writer involved):

| Agent | Focus | Writes |
|-------|-------|--------|
| mapper-tech | tech stack & integrations | `STACK.md`, `INTEGRATIONS.md` |
| mapper-arch | architecture & layout | `ARCHITECTURE.md`, `STRUCTURE.md` |
| mapper-conv | conventions & naming | `CONVENTIONS.md` |

Each mapper:

1. Reads its focus assignment + the corresponding template.
2. Explores the codebase read-only (Glob / Grep / Read — never compiles).
3. Writes its document(s) directly to `.w1mer/codebase/`.
4. Returns a confirmation only (document paths + line counts).

Orchestrator after all mappers confirm:

1. Verify all 5 documents exist (path + line count).
2. Update `last_sync: <commit>` in `codebase/INDEX.md` if it exists.
3. Commit the map if the project tracks `.w1mer/`.

## Mapper rules

- **Read-only against code.** Mappers explore and write only their assigned
  document — never touch source, never compile, never commit.
- **Never read forbidden files** (`.env`, credentials, keys, certs). Note
  existence only, never contents — output is committed to git.
- **Write current state only.** No temporal language, no speculation.
- **Always include file paths** with backticks — the docs guide navigation.
- **Be prescriptive** (used by future implementers): "use pattern X" beats
  "pattern X is used".
- **Return confirmation only** (~10 lines): focus, documents written, counts.

## Focus areas

| Focus | Explore | Docs |
|-------|---------|------|
| tech | manifests (Cargo.toml/package.json/...), config files, build & test tooling, external SDKs | STACK, INTEGRATIONS |
| arch | directory layout, entry points, import/graph structure, data flows, abstractions | ARCHITECTURE, STRUCTURE |
| conv | lint/format config, sample source, naming patterns, error/logging conventions | CONVENTIONS |
