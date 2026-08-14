#!/usr/bin/env python3
"""w1mer — metadata-driven archive CLI.

Single-file, stdlib-only. Operates the .w1mer/ planning archive defined by
the w1mer.yaml type registry. Content files are the source of truth; INDEX
files are build artifacts (single-direction sync).

Commands:
  init                    scaffold .w1mer/ from templates + copy w1mer.yaml
  install                 install host agents + CLI launcher (--host, --link)
  new <type> [args]       create an entry (auto-increments the id)
  set <type> <id> --state <state>   update an entry's state
  list [--type <type>]    list entries (tree order for ids)
  build                   regenerate all INDEX files
"""

import argparse
import datetime
import os
import re
import shlex
import shutil
import stat
import sys
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
        for f in sorted(src.glob("*.md")):
            shutil.copy2(f, dest / f.name)
            n += 1
        print(f"installed {n} agent(s) -> {dest}")
    if not args.no_cli:
        bin_dir = Path(args.bin_dir) if args.bin_dir else pick_bin_dir()
        if bin_dir is None:
            print("warning: no writable directory on PATH; run with --bin-dir <dir>")
        else:
            install_cli(bin_dir, link=args.link)


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
    marker = f"<!-- w1mer:task:{section} -->"
    if marker not in text:
        sys.exit(f"error: ROADMAP.md missing marker {marker} (sections: perf/bug/feature/infra/backlog)")
    title = args.title or f"task {nid}"
    doc = args.doc or "—"
    line = f"| {nid} | {title} | {doc} | todo | |"
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
    print(f"added task {nid}: {title}  [{section}]")


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
        if not args.state and not args.effect:
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
    Row format: | id | task | doc | status | effect |"""
    road = Path(cwd) / ".w1mer" / "ROADMAP.md"
    if not road.exists():
        sys.exit("error: .w1mer/ROADMAP.md missing (run 'w1mer init')")
    text = road.read_text(encoding="utf-8")
    pattern = re.compile(rf"^(\| {re.escape(str(args.id))} \|)([^\n]*?)(\|)$", re.M)
    m = pattern.search(text)
    if not m:
        sys.exit(f"error: task {args.id} not found in ROADMAP.md")
    cells = [p.strip() for p in m.group(2).split("|")]
    # cells: ['', task, doc, status, effect, ''] → drop empties but keep effect if blank
    while cells and cells[-1] == "":
        cells.pop()
    while cells and cells[0] == "":
        cells.pop(0)
    # cells now: [task, doc, status, effect?]; pad to 4
    while len(cells) < 4:
        cells.append("")
    if args.state:
        cells[2] = args.state
    if args.effect:
        cells[3] = args.effect
    new_row = "| " + str(args.id) + " | " + " | ".join(cells) + " |"
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
        m = re.match(r"^\| (\d+(?:\.\d+)*) \|([^|]+)\|([^|]+)\|([^|]+)\|", line)
        if m:
            rows.append((m.group(1), m.group(4).strip(), m.group(2).strip()))
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
                    vals.append(meta.get(c, ""))
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

    p_install = sub.add_parser("install", help="install host agents + CLI launcher")
    p_install.add_argument("--host", default="opencode", help="opencode | claude-code | all (default: opencode)")
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
