# {project} — Roadmap

> Lower number = higher priority. Priorities: performance > bug fixes > features.
> Each completed task: mark `[x]` + date + commit, add a one-line measured
> effect summary and `pending-review`; full data goes to the results archive.
> Insert new items with derived sub-ids (`05.1`, `05.1.2`); never batch-renumber.

## ID rules

- Unbounded rolling hierarchical IDs: `01`, `05`, `05.1`, `05.1.1`, ...
- Children branch deeper on demand (sub-batch fixes, review follow-ups).
- Ordering: pre-order traversal (parent first, children right behind).
- Cross-references stay stable; derived ids link review/results/bug docs.

## Status states

`todo` `doing` `done` `pending-review` `reviewed` `reviewed-issues` `fixed` `re-reviewed`

## 一、Performance

| # | Task | Doc | Status | Effect |
|---|------|-----|--------|--------|
<!-- w1mer:task:perf -->

## 二、Bug fixes

| # | Task | Doc | Status | Effect |
|---|------|-----|--------|--------|
<!-- w1mer:task:bug -->

## 三、Features

| # | Task | Doc | Status | Effect |
|---|------|-----|--------|--------|
<!-- w1mer:task:feature -->

## 四、Infrastructure

| # | Task | Doc | Status | Effect |
|---|------|-----|--------|--------|
<!-- w1mer:task:infra -->

## 五、Backlog

(Items awaiting re-prioritization)
<!-- w1mer:task:backlog -->
