#!/usr/bin/env python3
"""w1mer — metadata-driven archive CLI.

Single-file, stdlib-only. Operates the .w1mer/ planning archive defined by
the w1mer.yaml type registry. Content files are the source of truth; INDEX
files are build artifacts (single-direction sync).

Commands:
  init                    scaffold .w1mer/ from templates + copy w1mer.yaml
  install                 install host agents + CLI launcher (--host, --link)
  batch-start <task>      record the batch boundary commit (gates completeness)
  batch-end <task>        explicitly close the current task (end = HEAD)
  ensure <task>           idempotently make this task current (subagent startup)
  role-join <task> <role> mark a role as launched (subagent startup)
  status                  show current batch state + completeness
  new <type> [args]       create an entry (auto-increments the id)
  set <type> <id> --state <state>   update an entry's state
  list [--type <type>]    list entries (tree order for ids)
  build                   regenerate all INDEX files
"""

import argparse
import contextlib
import datetime
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_ROOT / "templates"
CONFIG_NAME = "w1mer.yaml"

HOSTS = {
    "opencode": {
        "agents": SKILL_ROOT / "hosts" / "opencode" / "agent",
        "dest": Path.home() / ".config" / "opencode" / "agents",
    },
    "claude-code": {
        "agents": SKILL_ROOT / "hosts" / "claude-code" / "agents",
        "dest": Path.home() / ".claude" / "agents",
    },
    "codex": {
        "agents": SKILL_ROOT / "hosts" / "codex" / "agents",
        "dest": Path.home() / ".codex" / "agents",
    },
}

# ---------------------------------------------------------------------------
# minimal YAML subset (key: value, nested 2-space blocks, dash lists, # comments)
# ---------------------------------------------------------------------------


def parse_yaml(text):
    root = {}
    stack = []  # (indent, dict)
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        line = line.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        container = stack[-1][1] if stack else root
        if line.startswith("- "):
            raise ValueError("top-level dash lists not supported")
        if ": " in line:
            key, _, value = line.partition(": ")
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
            elif value:
                value = value.strip("\"'")
            container[key] = value
        elif line.endswith(":"):
            key = line[:-1].strip()
            child = {}
            container[key] = child
            stack.append((indent, child))
        else:
            raise ValueError(f"cannot parse line: {raw!r}")
    return root


def load_config(cwd):
    path = Path(cwd) / CONFIG_NAME
    if not path.exists():
        sys.exit(f"error: {CONFIG_NAME} not found (run 'w1mer init' first)")
    return parse_yaml(path.read_text(encoding="utf-8"))


def get_type(cfg, name):
    types = cfg.get("types", {})
    if name not in types:
        sys.exit(f"error: unknown type '{name}'. Known: {', '.join(types)}")
    return types[name]


# ---------------------------------------------------------------------------
# frontmatter
# ---------------------------------------------------------------------------


def read_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ": " in line:
            k, _, v = line.partition(": ")
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def write_frontmatter(path, meta, body):
    fm = "---\n" + "".join(f"{k}: {v}\n" for k, v in meta.items()) + "---\n"
    path.write_text(fm + body, encoding="utf-8")


# ---------------------------------------------------------------------------
# table cells (Markdown rows)
# ---------------------------------------------------------------------------


def esc_cell(s):
    """Escape a value for a Markdown table cell: '\\' -> '\\\\', '|' -> '\\|'."""
    return s.replace("\\", "\\\\").replace("|", "\\|")


def split_cells(s):
    """Split a table row's inner text on unescaped '|' and unescape the cells.

    Inverse of joining esc_cell()ed values with ' | '.
    """
    cells, cur, i, n = [], [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] in "|\\":
            cur.append("|" if s[i + 1] == "|" else "\\")
            i += 2
            continue
        if c == "|":
            cells.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    cells.append("".join(cur))
    return cells


# ---------------------------------------------------------------------------
# ids
# ---------------------------------------------------------------------------


def next_id(tdef, parent=None, domain=None, existing=()):
    """Compute the next id for a type.

    parent    → derived id (R_{roadmap} style: dots become underscores)
    domain    → substituted into {domain} placeholders (perf)
    existing  → iterable of existing ids, for {seq:N} auto-increment
    """
    pattern = tdef.get("id", "")
    if parent is not None:
        return pattern.replace("{roadmap}", str(parent).replace(".", "_"))
    if domain is not None:
        pattern = pattern.replace("{domain}", domain)
    m = re.search(r"\{seq:(\d+)\}", pattern)
    if m:
        width = int(m.group(1))
        prefix = pattern[: m.start()]
        nums = []
        for e in existing:
            if str(e).startswith(prefix):
                num = re.search(r"(\d+)$", str(e))
                if num:
                    nums.append(int(num.group(1)))
        n = max(nums) + 1 if nums else 1
        return prefix + str(n).zfill(width)
    return pattern


def task_existing_ids(text):
    """Collect existing task ids from ROADMAP rows: | 05 |, | 05.1 |, ..."""
    return re.findall(r"^\| (\d+(?:\.\d+)*) ", text, re.M)


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------

BODY_TEMPLATES = {
    "review": "# Review {id}\n\nStatus: {state}\n\n## Conclusion\n\n(one paragraph)\n\n## Findings\n\n- \n",
    "bug": "# Bug {id}\n\nStatus: {state}\n\n## Symptom\n\n(what fails / error message)\n\n## Root cause\n\n(diagnosis)\n\n## Fix\n\n(approach)\n",
    "detail": "# Detail {id}\n\nStatus: {state}\n\n(body)\n",
    "perf": "# PERF {id}\n\nStatus: {state}\n\n## Bottleneck\n\n## Approach\n\n## Expected value\n\n",
}


# ---------------------------------------------------------------------------
# state (STATE.json) — batch ledger + role liveness
# ---------------------------------------------------------------------------
#
# Single source of truth for execution state (commits, roles, timing), kept
# separate from the planning docs (ROADMAP.md). Machine-maintained: agents
# only touch it through the CLI, never by hand.
#
#   .w1mer/STATE.json  the state
#   .w1mer/.lock       advisory lock (flock) serializing read-modify-write
#
# Concurrency model:
#   - writes hold the lock across the whole load -> modify -> save cycle
#   - save is temp-file + os.replace (atomic on POSIX), so a reader never sees
#     a torn file; pure reads therefore need no lock
#   - base/end are first-write-wins: once non-null they are never rewritten,
#     which is what makes re-running a command idempotent ("do it for the
#     main agent, but don't duplicate")

STATE_NAME = "STATE.json"
LOCK_NAME = ".lock"
EXPECTED_ROLES = {
    "main": ["reviewer", "implementer", "explorer"],
    "sub": ["reviewer", "fixer"],
}


def missing_roles(entry):
    """Expected roles for a task entry that have not joined (derived, not stored)."""
    expected = EXPECTED_ROLES.get(entry.get("type", "main"), [])
    return [r for r in expected if not entry.get("roles", {}).get(r, {}).get("joined")]


def state_path(cwd):
    return Path(cwd) / ".w1mer" / STATE_NAME


def lock_path(cwd):
    return Path(cwd) / ".w1mer" / LOCK_NAME


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_state():
    return {"version": 1, "updated": None, "current": None, "tasks": {}}


def load_state(cwd):
    p = state_path(cwd)
    if not p.exists():
        return empty_state()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"error: {p} is unreadable/corrupt ({e}); remove it and re-run")


@contextlib.contextmanager
def locked(cwd):
    """Hold an exclusive advisory lock for the duration of a state mutation."""
    try:
        import fcntl
    except ImportError:  # non-POSIX: best-effort, no lock
        yield
        return
    lp = lock_path(cwd)
    lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def save_state(cwd, state):
    """Write state atomically (temp + rename). Caller must hold locked()."""
    state["updated"] = now_iso()
    p = state_path(cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".STATE.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def git_head(cwd):
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                             capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        sys.exit(f"error: not a git repo (git rev-parse HEAD failed): {e}")
    return out.stdout.strip()


def task_entry(ttype):
    """Fresh entry for a task, with all expected roles un-joined."""
    return {
        "type": ttype,
        "base": None,
        "end": None,
        "roles": {r: {"joined": False, "at": None} for r in EXPECTED_ROLES[ttype]},
        "started": now_iso(),
        "ended": None,
    }


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_init(cwd):
    dst = Path(cwd) / ".w1mer"
    if dst.exists() and any(dst.iterdir()):
        sys.exit("error: .w1mer/ already exists and is not empty")
    dst.mkdir(parents=True, exist_ok=True)
    src = TEMPLATES / "w1mer"
    shutil.copytree(src, dst, dirs_exist_ok=True)
    cfg_dst = Path(cwd) / CONFIG_NAME
    if not cfg_dst.exists():
        shutil.copy(TEMPLATES / CONFIG_NAME, cfg_dst)
        print(f"created {CONFIG_NAME}")
    print(f"scaffolded .w1mer/ from templates")


def pick_bin_dir():
    """First writable dir on PATH, preferring ~/.local/bin (POSIX only)."""
    home_local = Path.home() / ".local" / "bin"
    if os.name != "nt" and home_local in [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]:
        return home_local
    for p in os.environ.get("PATH", "").split(os.pathsep):
        d = Path(p)
        if d.is_dir() and os.access(d, os.W_OK):
            return d
    return None


def install_cli(bin_dir, link=False):
    is_windows = os.name == "nt"
    name = "w1mer.bat" if is_windows else "w1mer"
    dst = bin_dir / name
    src = SKILL_ROOT / "scripts" / "w1mer.py"
    if link and not is_windows:
        src.chmod(src.stat().st_mode | stat.S_IEXEC)
        try:
            dst.unlink(missing_ok=True)
            dst.symlink_to(src)
            print(f"linked CLI -> {dst}")
            return True
        except OSError as e:
            print(f"warning: symlink failed ({e}); falling back to launcher")
    if is_windows:
        launcher = (
            "@echo off\r\n"
            f'python "{src}" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n"
        )
    else:
        launcher = (
            "#!/bin/sh\n"
            f"exec python3 {shlex.quote(str(src))} \"$@\"\n"
        )
    try:
        dst.unlink(missing_ok=True)
    except OSError:
        pass
    dst.write_text(launcher, encoding="utf-8")
    if not is_windows:
        dst.chmod(dst.stat().st_mode | stat.S_IEXEC)
    print(f"installed launcher -> {dst}")
    return True


def cmd_install(args):
    host = args.host
    hosts = [host] if host != "all" else list(HOSTS)
    for h in hosts:
        if h not in HOSTS:
            sys.exit(f"error: unknown host '{h}'. Known: {', '.join(HOSTS)} or 'all'")
    if args.list_only:
        for h in hosts:
            spec = HOSTS[h]
            print(f"[{h}] {spec['dest']} <- {spec['agents']}")
        if not args.no_cli:
            print(f"[cli] {pick_bin_dir() or 'NO WRITABLE PATH DIR'} <- {SKILL_ROOT / 'scripts' / 'w1mer.py'}")
        return
    for h in hosts:
        spec = HOSTS[h]
        src, dest = spec["agents"], spec["dest"]
        if not src.is_dir():
            print(f"warning: no agent definitions at {src}")
            continue
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in sorted(list(src.glob("*.md")) + list(src.glob("*.toml"))):
            shutil.copy2(f, dest / f.name)
            n += 1
        print(f"installed {n} agent(s) -> {dest}")
    if not args.no_cli:
        bin_dir = Path(args.bin_dir) if args.bin_dir else pick_bin_dir()
        if bin_dir is None:
            print("warning: no writable directory on PATH; run with --bin-dir <dir>")
        else:
            install_cli(bin_dir, link=args.link)


def _start_task(state, task, ttype, head, gate):
    """Shared core of batch-start / ensure: close the previous task, open this
    one, set current. Returns (ok, msg); ok=False means the completeness gate
    blocked (msg is the reason). The gate is the only difference between the
    two commands — ensure defers it to the main agent's next batch-start."""
    tasks = state["tasks"]
    prev = state.get("current")
    if prev and prev != task and prev in tasks:
        if tasks[prev].get("end") is None:
            tasks[prev]["end"] = head
            tasks[prev]["ended"] = now_iso()
        if gate:
            missing = missing_roles(tasks[prev])
            if missing and not task.startswith(prev + "."):
                return False, (f"previous task {prev} INCOMPLETE (missing roles: "
                               f"{', '.join(missing)}); repair it via sub-batch "
                               f"{prev}.1 before starting {task}")
    if task in tasks:
        entry = tasks[task]
        if entry.get("base") is None:
            entry["base"] = head
    else:
        tasks[task] = task_entry(ttype)
        tasks[task]["base"] = head
    state["current"] = task
    return True, None


def cmd_batch_start(cwd, args):
    """Record the batch boundary: close the previous task, open the current one.

    Idempotent (first-write-wins on base/role-joins). Completeness gate:
    leaving a task requires all its expected roles to have joined, EXCEPT when
    the new task is a sub-id of it (05 -> 05.1) — the repair sub-batch, which
    exists precisely because the parent is incomplete."""
    ttype = args.type
    head = git_head(cwd)
    with locked(cwd):
        state = load_state(cwd)
        ok, msg = _start_task(state, args.task, ttype, head, gate=True)
        save_state(cwd, state)
        if not ok:
            sys.exit(f"error: {msg}")
    print(f"batch-start {args.task}  base={head[:7]}  type={ttype}")


def cmd_ensure(cwd, args):
    """Idempotent 'make this task the current, in-progress task'.

    This is what a subagent runs on startup: if the main agent already ran
    batch-start it is a no-op; if the main agent was compacted and never ran
    it, this does it on the main agent's behalf. No completeness gate — a
    subagent joining its own task is not 'moving on', so the gate (which
    guards the main agent's progress) is deferred to the next batch-start."""
    ttype = args.type
    head = git_head(cwd)
    with locked(cwd):
        state = load_state(cwd)
        _start_task(state, args.task, ttype, head, gate=False)
        save_state(cwd, state)
    print(f"ensure {args.task}  base={head[:7]}  type={ttype}")


def cmd_batch_end(cwd, args):
    """Explicitly close the current task: end = HEAD. First-write-wins."""
    head = git_head(cwd)
    with locked(cwd):
        state = load_state(cwd)
        if state.get("current") != args.task:
            sys.exit(f"error: task {args.task} is not the current task (current: {state.get('current')})")
        entry = state["tasks"].get(args.task)
        if entry is None:
            sys.exit(f"error: task {args.task} not in STATE.json")
        if entry.get("end") is not None:
            print(f"task {args.task} already closed (no-op)")
            return
        entry["end"] = head
        entry["ended"] = now_iso()
        save_state(cwd, state)
    print(f"batch-end {args.task}  end={head[:7]}")


def cmd_role_join(cwd, args):
    """Mark a role as launched for the current task (subagent startup ritual).

    Idempotent: re-joining keeps the original 'at' timestamp (first-write-wins),
    so 'at' stays the true first-join time."""
    with locked(cwd):
        state = load_state(cwd)
        entry = state["tasks"].get(args.task)
        if entry is None:
            sys.exit(f"error: task {args.task} not in STATE.json (run 'batch-start' or 'ensure' first)")
        expected = EXPECTED_ROLES.get(entry.get("type", "main"), [])
        if args.role not in expected:
            sys.exit(f"error: role '{args.role}' not expected for {entry['type']} batch (expected: {', '.join(expected)})")
        if entry.get("end") is not None:
            sys.exit(f"error: task {args.task} already closed (end set); late join is a protocol violation")
        info = entry["roles"].setdefault(args.role, {"joined": False, "at": None})
        if info["joined"]:
            print(f"role {args.role} already joined for {args.task} (no-op)")
            return
        info["joined"] = True
        info["at"] = now_iso()
        save_state(cwd, state)
    print(f"role {args.role} joined for {args.task}")


def cmd_status(cwd, args):
    """Show the current batch state and its derived completeness."""
    state = load_state(cwd)
    cur = state.get("current")
    if not cur:
        print("no active batch (STATE.json empty)")
        return
    entry = state["tasks"].get(cur)
    if not entry:
        print(f"current={cur} but no record in STATE.json")
        return
    ttype = entry.get("type", "main")
    expected = EXPECTED_ROLES.get(ttype, [])
    print(f"current: {cur}  (type={ttype})")
    print(f"  base: {entry.get('base') or '-'}")
    print(f"  end:  {entry.get('end') or '-'}")
    for r in expected:
        info = entry.get("roles", {}).get(r, {})
        mark = "ok     " if info.get("joined") else "MISSING"
        print(f"  role {r:<12} {mark}  {info.get('at') or ''}")
    missing = missing_roles(entry)
    if entry.get("end") is None:
        print("  status: IN PROGRESS (end not set)")
    elif missing:
        print(f"  status: INCOMPLETE (missing roles: {', '.join(missing)})")
    else:
        print("  status: COMPLETE")


def collect_files(tdef, cfg):
    """Return dict {id: path} for all content files of a type (excluding INDEX)."""
    root = Path.cwd() / cfg.get("planning_dir", ".w1mer")
    d = root / tdef["dir"]
    out = {}
    if not d.exists():
        return out
    for p in sorted(d.glob("*.md")):
        if p.name in ("INDEX.md", "CHANGES.md") or p.name.startswith("."):
            continue
        meta, _ = read_frontmatter(p)
        if "id" in meta:
            out[meta["id"]] = p
    return out


def slugify(title):
    # \w keeps unicode letters (incl. CJK) so Chinese titles survive
    s = re.sub(r"[^\w]+", "_", title).strip("_").lower()
    return s or "untitled"


def cmd_new(cwd, cfg, args):
    tdef = get_type(cfg, args.type)
    files = collect_files(tdef, cfg)
    if args.type == "task":
        add_roadmap_task(cwd, args)
        return
    states = tdef.get("states", [])
    state = args.state or (states[0] if states else "todo")
    if states and state not in states:
        sys.exit(f"error: state '{state}' not in {states}")
    existing = set(files) | set(tdef.get("static", []))
    nid = next_id(tdef, parent=args.parent, domain=args.domain, existing=existing)
    if nid in files:
        sys.exit(f"error: id {nid} already exists")
    meta = {"id": nid, "title": args.title or f"{args.type} {nid}", "state": state}
    if args.domain:
        meta["domain"] = args.domain
    # build file name
    slug = args.slug or slugify(args.title or nid)
    fname = tdef["file"].replace("{id}", nid).replace("{slug}", slug)
    d = Path(cwd) / cfg.get("planning_dir", ".w1mer") / tdef["dir"]
    d.mkdir(parents=True, exist_ok=True)
    path = d / fname
    body = BODY_TEMPLATES.get(args.type, "# {id}\n\n").format(id=nid, state=state, title=meta["title"])
    write_frontmatter(path, meta, body)
    rel = path.resolve().relative_to(Path(cwd).resolve())
    print(f"created {rel}  (id={nid})")


def add_roadmap_task(cwd, args):
    road = Path(cwd) / ".w1mer" / "ROADMAP.md"
    if not road.exists():
        sys.exit("error: .w1mer/ROADMAP.md missing (run 'w1mer init')")
    text = road.read_text(encoding="utf-8")
    ids = task_existing_ids(text)
    if args.parent:
        parent = str(args.parent)
        children = [i for i in ids if i.startswith(f"{parent}.")]
        n = 1
        for c in children:
            m = re.match(rf"^{re.escape(parent)}\.(\d+)$", c)
            if m:
                n = max(n, int(m.group(1)) + 1)
        nid = f"{parent}.{n}"
    else:
        nums = [int(i.split(".")[0]) for i in ids]
        nid = f"{max(nums) + 1:02d}" if nums else "01"
    if nid in ids:
        sys.exit(f"error: task {nid} already exists")
    section = args.section or "perf"
    title = args.title or f"task {nid}"
    doc = args.doc or "—"
    insert_roadmap_row(cwd, nid, title, doc, section)
    print(f"added task {nid}: {title}  [{section}]")


def insert_roadmap_row(cwd, nid, title, doc, section):
    """Insert a task row with an explicit id into the section, pre-order sorted."""
    road = Path(cwd) / ".w1mer" / "ROADMAP.md"
    text = road.read_text(encoding="utf-8")
    if nid in task_existing_ids(text):
        sys.exit(f"error: task {nid} already exists")
    marker = f"<!-- w1mer:task:{section} -->"
    if marker not in text:
        sys.exit(f"error: ROADMAP.md missing marker {marker} (sections: perf/bug/feature/infra/backlog)")
    line = f"| {nid} | {esc_cell(title)} | {esc_cell(doc)} | todo | |"
    pos = text.index(marker) + len(marker)
    lines = text[pos:].split("\n")
    # skip leading blank lines after the marker
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    lines = lines[i:]
    # collect contiguous task-row lines
    region_end = 0
    while region_end < len(lines) and re.match(r"^\| \d", lines[region_end]):
        region_end += 1
    task_rows = lines[:region_end]
    tail = lines[region_end:]
    task_rows.append(line)
    # pre-order sort by ID column only: (1,) < (1,1) < (1,1,1) < (1,2) < (2,)
    task_rows.sort(key=task_row_key)
    block = "\n".join(task_rows) + "\n\n" + "\n".join(tail).rstrip() + "\n"
    text = text[:pos] + "\n" + block
    road.write_text(text, encoding="utf-8")


def task_row_key(row):
    """Sort key from the task row's ID column only — never doc/effect numbers."""
    m = re.match(r"^\| (\d+(?:\.\d+)*) ", row)
    return tuple(int(p) for p in m.group(1).split(".")) if m else (0,)


def find_file(tdef, cfg, nid):
    files = collect_files(tdef, cfg)
    if nid in files:
        return files[nid]
    sys.exit(f"error: {nid} not found")


def cmd_set(cwd, cfg, args):
    tdef = get_type(cfg, args.type)
    if args.type == "task":
        if not args.state and not args.effect and not args.register_if_missing:
            sys.exit("error: provide --state and/or --effect for task rows")
        set_roadmap_task(cwd, args)
        return
    if not args.state:
        sys.exit("error: --state required for non-task types")
    path = find_file(tdef, cfg, args.id)
    states = tdef.get("states", [])
    if states and args.state not in states:
        sys.exit(f"error: state '{args.state}' not in {states}")
    meta, body = read_frontmatter(path)
    meta["state"] = args.state
    write_frontmatter(path, meta, body)
    rel = path.resolve().relative_to(Path(cwd).resolve())
    print(f"{rel}: state -> {args.state}")


def set_roadmap_task(cwd, args):
    """Update a task row in ROADMAP.md: status (--state) and/or effect (--effect).
    Row format: | id | task | doc | status | effect |
    With --register-if-missing, a missing row is registered first (state todo)."""
    road = Path(cwd) / ".w1mer" / "ROADMAP.md"
    if not road.exists():
        sys.exit("error: .w1mer/ROADMAP.md missing (run 'w1mer init')")
    text = road.read_text(encoding="utf-8")
    pattern = re.compile(rf"^(\| {re.escape(str(args.id))} \|)([^\n]*?)(\|)$", re.M)
    m = pattern.search(text)
    if not m:
        if not args.register_if_missing:
            sys.exit(f"error: task {args.id} not found in ROADMAP.md")
        nid = str(args.id)
        if not re.fullmatch(r"\d+(?:\.\d+)*", nid):
            sys.exit(f"error: task id '{nid}' is not a valid hierarchical id (e.g. 05, 05.1)")
        section = args.section or "perf"
        insert_roadmap_row(cwd, nid, args.title or f"task {nid}", args.doc or "—", section)
        print(f"registered task {nid} in ROADMAP.md  [{section}]")
        text = road.read_text(encoding="utf-8")
        m = pattern.search(text)
        if not m:
            sys.exit(f"error: task {args.id} not found in ROADMAP.md")
    cells = [c.strip() for c in split_cells(m.group(2))]
    # cells: [task, doc, status, effect?]; pad to 4
    while cells and cells[0] == "":
        cells.pop(0)
    while len(cells) < 4:
        cells.append("")
    if args.state:
        cells[2] = args.state
    if args.effect:
        cells[3] = args.effect
    new_row = "| " + str(args.id) + " | " + " | ".join(esc_cell(c) for c in cells) + " |"
    text = text[: m.start()] + new_row + text[m.end():]
    road.write_text(text, encoding="utf-8")
    print(f"task {args.id}: state={cells[2]} effect={cells[3]}")


def preorder_sort(ids, domain_order=None):
    def key(i):
        s = str(i)
        if domain_order:
            m = re.match(r"^(?:PERF_)?(\w+?)(?:_\d+)?$", s)
            # PERF_string_01 → domain "string", seq 01
            dm = re.match(r"PERF_(\w+)_(\d+)$", s)
            if dm:
                dom, seq = dm.group(1), int(dm.group(2))
                dom_idx = domain_order.index(dom) if dom in domain_order else len(domain_order)
                return (dom_idx, seq)
        prefix = re.split(r"\d", s, 1)[0]          # leading non-digit prefix
        nums = tuple(int(m) for m in re.findall(r"\d+", s))
        return (prefix, nums, s)

    return sorted(ids, key=key)


def cmd_list(cwd, cfg, args):
    if args.type:
        types = {args.type: get_type(cfg, args.type)}
    else:
        types = cfg.get("types", {})
    root = Path(cwd) / cfg.get("planning_dir", ".w1mer")
    for tname, tdef in types.items():
        if tname == "task":
            list_roadmap_tasks(cwd)
            continue
        files = collect_files(tdef, cfg)
        if not files:
            continue
        print(f"\n[{tname}]")
        for nid in preorder_sort(files, tdef.get("domains")):
            meta, _ = read_frontmatter(files[nid])
            print(f"  {nid:<8} {meta.get('state','?'):<14} {meta.get('title','')}")


def list_roadmap_tasks(cwd):
    road = Path(cwd) / ".w1mer" / "ROADMAP.md"
    if not road.exists():
        return
    rows = []
    for line in road.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| (\d+(?:\.\d+)*) \|(.*)\|$", line)
        if m:
            cells = [c.strip() for c in split_cells(m.group(2))]
            # cells: [task, doc, status, effect?]
            if len(cells) >= 3:
                rows.append((m.group(1), cells[2], cells[0]))
    if rows:
        print("\n[task]")
    for nid, status, title in sorted(rows, key=lambda r: tuple(int(p) for p in r[0].split("."))):
        print(f"  {nid:<8} {status:<14} {title}")


def cmd_build(cwd, cfg):
    root = Path(cwd) / cfg.get("planning_dir", ".w1mer")
    for tname, tdef in cfg.get("types", {}).items():
        d = root / tdef["dir"]
        idx = d / "INDEX.md"
        if not idx.exists():
            continue
        files = collect_files(tdef, cfg)
        rows = []
        cols = tdef.get("index", ["id", "title", "state", "doc"])
        for nid in preorder_sort(files, tdef.get("domains")):
            meta, _ = read_frontmatter(files[nid])
            vals = []
            for c in cols:
                if c == "doc":
                    vals.append(f"[{files[nid].name}]({files[nid].name})")
                else:
                    vals.append(esc_cell(meta.get(c, "")))
            rows.append("| " + " | ".join(vals) + " |")
        text = idx.read_text(encoding="utf-8")
        if "<!-- w1mer:rows -->" not in text:
            continue
        start = text.index("<!-- w1mer:rows -->") + len("<!-- w1mer:rows -->")
        end = text.index("<!-- /w1mer:rows -->")
        text = text[:start] + "\n" + "\n".join(rows) + "\n" + text[end:]
        idx.write_text(text, encoding="utf-8")
        rel = idx.resolve().relative_to(Path(cwd).resolve())
        print(f"built {rel}  ({len(rows)} rows)")


STABLE_DOCS = ("STACK", "STRUCTURE", "ARCHITECTURE", "INTEGRATIONS", "CONVENTIONS")


def cmd_sync(cwd, cfg, args):
    """Compact: merge architecture-impact deltas from detail/CHANGES.md into
    the stable codebase docs. Each delta line is tagged:
      - [ARCHITECTURE] contract X changed
    Untagged lines are listed as 'unassigned'. --apply writes the deltas
    under a dated heading in each target doc and clears CHANGES.md."""
    root = Path(cwd) / cfg.get("planning_dir", ".w1mer")
    changes = root / "detail" / "CHANGES.md"
    if not changes.exists():
        sys.exit("error: .w1mer/detail/CHANGES.md missing (run 'w1mer init')")
    meta, body = read_frontmatter(changes)
    deltas = []
    in_deltas = False
    for line in body.splitlines():
        if line.strip() == "## Deltas":
            in_deltas = True
            continue
        if in_deltas and line.strip().startswith("- "):
            item = line.strip()[2:].strip()
            if item and item != "(empty)":
                deltas.append(item)
    if not deltas:
        print("no deltas in detail/CHANGES.md")
        return
    tagged = {d: [] for d in STABLE_DOCS}
    unassigned = []
    for d in deltas:
        m = re.match(r"^\[(\w+)\]\s*(.*)$", d)
        if m and m.group(1) in tagged:
            tagged[m.group(1)].append(m.group(2))
        else:
            unassigned.append(d)
    if not args.apply:
        for doc in STABLE_DOCS:
            if tagged[doc]:
                print(f"-> {doc}.md")
                for t in tagged[doc]:
                    print(f"    {t}")
        if unassigned:
            print("-> unassigned")
            for t in unassigned:
                print(f"    {t}")
        print("(dry run; use --apply to write)")
        return
    # apply: append dated heading + deltas to each stable doc, then clear CHANGES
    today = datetime.date.today().isoformat()
    for doc in STABLE_DOCS:
        if not tagged[doc]:
            continue
        path = root / "codebase" / f"{doc}.md"
        if not path.exists():
            sys.exit(f"error: {path} missing")
        block = "\n".join(f"- {t}" for t in tagged[doc])
        text = path.read_text(encoding="utf-8").rstrip() + f"\n\n## Compact {today}\n\n{block}\n"
        path.write_text(text, encoding="utf-8")
        print(f"updated {doc}.md  (+{len(tagged[doc])} deltas)")
    new_body = body
    new_body = re.sub(r"(?ms)^## Deltas\n\n- .*$", "## Deltas\n\n- (empty)", new_body)
    write_frontmatter(changes, meta, new_body)
    print("cleared detail/CHANGES.md")


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(prog="w1mer", description="metadata-driven archive CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="scaffold .w1mer/ from templates")

    p_bs = sub.add_parser("batch-start", help="record the batch boundary commit (gates completeness)")
    p_bs.add_argument("task", help="task id being started")
    p_bs.add_argument("--type", default="main", choices=["main", "sub"],
                     help="batch type (main|sub); picks the expected roles")

    p_be = sub.add_parser("batch-end", help="explicitly close the current task (end = HEAD)")
    p_be.add_argument("task")

    p_ens = sub.add_parser("ensure", help="idempotently make this task current (subagent startup)")
    p_ens.add_argument("task")
    p_ens.add_argument("--type", default="main", choices=["main", "sub"],
                      help="batch type (main|sub); picks the expected roles")

    sub.add_parser("status", help="show current batch state + completeness")

    p_rj = sub.add_parser("role-join", help="mark a role as launched (subagent startup)")
    p_rj.add_argument("task")
    p_rj.add_argument("role")

    p_install = sub.add_parser("install", help="install host agents + CLI launcher")
    p_install.add_argument("--host", default="opencode", help="opencode | claude-code | codex | all (default: opencode)")
    p_install.add_argument("--no-cli", action="store_true", help="skip installing the w1mer CLI launcher")
    p_install.add_argument("--link", action="store_true", help="symlink the CLI to the skill instead of a launcher script")
    p_install.add_argument("--bin-dir", default=None, help="directory to install the CLI launcher (default: first writable PATH dir)")
    p_install.add_argument("--list", dest="list_only", action="store_true", help="show where things would install, do nothing")

    p_new = sub.add_parser("new", help="create an entry")
    p_new.add_argument("type")
    p_new.add_argument("--parent", default=None, help="derive child id from parent (e.g. 05 -> 05.1)")
    p_new.add_argument("--title", default=None)
    p_new.add_argument("--doc", default=None, help="doc column (task rows)")
    p_new.add_argument("--slug", default=None, help="file slug (overrides auto from title)")
    p_new.add_argument("--domain", default=None, help="perf domain")
    p_new.add_argument("--state", default=None, help="initial state (default: type's first state)")
    p_new.add_argument("--section", default=None, help="task section: perf/bug/feature/infra/backlog")

    p_set = sub.add_parser("set", help="update an entry's state (task also accepts --effect)")
    p_set.add_argument("type")
    p_set.add_argument("id")
    p_set.add_argument("--state", default=None)
    p_set.add_argument("--effect", default=None, help="effect summary (task rows only)")
    p_set.add_argument("--register-if-missing", dest="register_if_missing", action="store_true",
                      help="task: register the row in ROADMAP.md first if it is missing")
    p_set.add_argument("--title", default=None, help="title (register-if-missing only)")
    p_set.add_argument("--doc", default=None, help="doc column (register-if-missing only)")
    p_set.add_argument("--section", default=None, help="task section: perf/bug/feature/infra/backlog (register-if-missing only)")

    p_list = sub.add_parser("list", help="list entries")
    p_list.add_argument("--type", default=None)

    sub.add_parser("build", help="regenerate INDEX files")

    p_sync = sub.add_parser("sync", help="compact architecture deltas into stable codebase docs")
    p_sync.add_argument("--apply", action="store_true", help="write deltas + clear CHANGES (default: dry run)")

    args = p.parse_args()
    if args.cmd == "init":
        cmd_init(Path.cwd())
        return
    if args.cmd == "install":
        cmd_install(args)
        return
    if args.cmd == "batch-start":
        cmd_batch_start(Path.cwd(), args)
        return
    if args.cmd == "batch-end":
        cmd_batch_end(Path.cwd(), args)
        return
    if args.cmd == "ensure":
        cmd_ensure(Path.cwd(), args)
        return
    if args.cmd == "status":
        cmd_status(Path.cwd(), args)
        return
    if args.cmd == "role-join":
        cmd_role_join(Path.cwd(), args)
        return
    cfg = load_config(Path.cwd())
    if args.cmd == "new":
        cmd_new(Path.cwd(), cfg, args)
    elif args.cmd == "set":
        cmd_set(Path.cwd(), cfg, args)
    elif args.cmd == "list":
        cmd_list(Path.cwd(), cfg, args)
    elif args.cmd == "build":
        cmd_build(Path.cwd(), cfg)
    elif args.cmd == "sync":
        cmd_sync(Path.cwd(), cfg, args)


if __name__ == "__main__":
    main()
