#!/usr/bin/env python3
"""Keep only the diagnostics on BackOtter's own lines (RustAudit1, RA0).

Reads what cargo's tools print and a scope file (rust-scope.sh's
scope-NAME.txt), keeps the diagnostics whose line is one of ours, and prints
them with counts by lint, by lint group and by file.  rust-lint.sh runs it
on the Mac over each host run's output; it is also usable by hand.

Usage:
  rust-filter.py --scope FILE [--root DIR ...] [--tool NAME] [--files GLOB ...]
                 [--lints LINT ...] [--groups FILE] [--json OUT] [--list N]
                 [--no-list] INPUT ...
  rust-filter.py --compare OLD NEW
  rust-filter.py --summarize RUNDIR

Inputs, told apart line by line, in any mix:
  - cargo --message-format=json (clippy, check, doc, build): its
    compiler-message records, with the lint's name (the code field);
  - cargo --message-format=short: PATH:LINE:COL: LEVEL[CODE]: MESSAGE (no
    lint name there: the message stands in for it);
  - cargo fmt --check: "Diff in PATH:LINE:" (or the older "Diff in PATH at
    line LINE:") and the hunk after it; a hunk counts as one diagnostic,
    rustfmt, ours when any line it removes (or the line it inserts at) is.

Paths: relative ones are taken against the workspace root (crates/core/src/
x.rs); "../" ones are outside the tree (a path dependency); absolute ones
lose a --root DIR prefix, and without one matching are outside.  With no
--root at all an absolute path is matched by its longest suffix that is a
file in the scope.

--files GLOB (fnmatch, on the relative path) and --lints keep only those
files and lints (holt's parser-module pass).  --groups FILE is the output of
`clippy-driver -W help`, which names each clippy lint's group.  --json OUT
writes the counts, the in-scope items and a key per (file, lint, message
with its numbers blanked) for --compare.

--compare OLD NEW: two such JSON files, or two directories of them (paired
by file name): prints every key whose count grew, and exits 1 if any did.

--summarize RUNDIR: rust-lint.sh's summary of its run directory (raw/
info.txt and steps.tsv, filtered/NAME.TARGET.TOOL.json, scope/, holt's test
logs): the text of testvm/audit/rust/baseline.txt.

Exit: 0; 1 for --compare with a new in-scope diagnostic; 2 on bad input.
"""

import argparse
import collections
import fnmatch
import json
import os
import re
import sys

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SHORT = re.compile(
    r"^(?P<path>[^\s:][^:]*):(?P<line>\d+):(?P<col>\d+): "
    r"(?P<level>warning|error)(?:\[(?P<code>[^\]]+)\])?: (?P<msg>.*)$")
FMT_HEAD = re.compile(r"^Diff in (?P<path>.+?)(?: at line (?P<l1>\d+)|:(?P<l2>\d+)):\s*$")
LEVELS = ("warning", "error")
CLIPPY_GROUPS = ("correctness", "suspicious", "style", "complexity", "perf",
                 "pedantic", "nursery", "restriction", "cargo")


class Scope:
    """Our lines: path -> sorted [(start, end)], and the files listed."""

    def __init__(self, path):
        self.ranges = collections.defaultdict(list)
        self.files = set()
        self.info = {}
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if line.startswith("# name="):
                    for kv in line[2:].split():
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            self.info[k] = v
                    continue
                if line.startswith("# lines "):
                    parts = line.split(None, 5)
                    if len(parts) == 6:
                        self.files.add(parts[5])
                    continue
                if not line.strip() or line.startswith("#"):
                    continue
                p, _, r = line.rpartition(" ")
                a, _, b = r.partition("-")
                try:
                    self.ranges[p].append((int(a), int(b or a)))
                except ValueError:
                    raise SystemExit(f"rust-filter: bad scope line: {line!r}")
                self.files.add(p)
        for p in self.ranges:
            self.ranges[p].sort()

    def ours(self, path, line):
        for a, b in self.ranges.get(path, ()):
            if a <= line <= b:
                return True
            if a > line:
                break
        return False


def blank_numbers(msg):
    return re.sub(r"\d+", "N", msg)


class Item:
    __slots__ = ("tool", "path", "line", "col", "level", "lint", "msg",
                 "lines", "minus", "plus", "where")

    def __init__(self, tool, path, line, col, level, lint, msg, lines=None):
        self.tool, self.path, self.line, self.col = tool, path, line, col
        self.level, self.lint, self.msg = level, lint, msg
        self.lines = [line] if lines is None else lines   # the lines that decide "ours"
        self.minus = self.plus = 0
        self.where = None              # ours, upstream, other-file, outside

    def short(self):
        return f"{self.path}:{self.line}:{self.col}: {self.level}[{self.lint}]: {self.msg}"

    def key(self):
        return (self.path, self.line, self.col, self.level, self.lint, self.msg)


def real_span(msg):
    """The primary span in a real file, following macro expansions out."""
    spans = msg.get("spans") or []
    prim = [s for s in spans if s.get("is_primary")] or spans
    if not prim:
        return None
    s = prim[0]
    seen = 0
    while s and (s.get("file_name", "").startswith("<") or s.get("file_name", "") == "") and seen < 50:
        exp = s.get("expansion") or {}
        s = exp.get("span")
        seen += 1
    return s


def parse(lines, tool):
    """Items from one input's lines."""
    items = []
    fmt = None   # the fmt hunk being read: [Item, orig line number]

    def end_fmt():
        nonlocal fmt
        if fmt:
            it = fmt[0]
            if not it.lines:
                it.lines = [it.line]
            items.append(it)
        fmt = None

    for raw in lines:
        line = ANSI.sub("", raw.rstrip("\n").rstrip("\r"))
        m = FMT_HEAD.match(line)
        if m:
            end_fmt()
            n = int(m.group("l1") or m.group("l2"))
            fmt = [Item(tool or "fmt", m.group("path"), n, 1, "warning", "rustfmt", "not formatted", []), n]
            continue
        if fmt is not None:
            if line == "":
                continue   # between hunks
            if line[:1] in (" ", "-", "+"):
                it, n = fmt
                if line.startswith("-"):
                    it.lines.append(n)
                    it.minus += 1
                    fmt[1] = n + 1
                elif line.startswith("+"):
                    it.plus += 1
                    if not it.lines:
                        # an insertion: the line it goes before, and the one above
                        it.lines.extend([max(n - 1, 1), n])
                else:
                    fmt[1] = n + 1
                continue
            end_fmt()
        if line.startswith("{"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("reason") != "compiler-message":
                continue
            msg = rec.get("message") or {}
            level = msg.get("level", "")
            if level not in LEVELS:
                continue
            s = real_span(msg)
            if not s:
                continue   # "N warnings emitted" and the like
            code = (msg.get("code") or {}).get("code") or level
            items.append(Item(tool or "cargo", s["file_name"], int(s["line_start"]),
                              int(s.get("column_start", 1)), level, code, msg.get("message", "")))
            continue
        m = SHORT.match(line)
        if m:
            items.append(Item(tool or "cargo", m.group("path"), int(m.group("line")), int(m.group("col")),
                              m.group("level"), m.group("code") or "msg", m.group("msg")))
    end_fmt()
    return items


def rel_path(path, roots, scope):
    """(relative path, inside the tree?)"""
    if os.path.isabs(path):
        for r in roots:
            r = r.rstrip("/")
            if path.startswith(r + "/"):
                return os.path.normpath(path[len(r) + 1:]), True
        if roots:
            return path, False
        parts = path.strip("/").split("/")
        for i in range(len(parts)):
            cand = "/".join(parts[i:])
            if cand in scope.files:
                return cand, True
        return path, False
    p = os.path.normpath(path)
    if p.startswith("../") or p == "..":
        return path, False
    return p, True


def load_groups(path):
    """lint name -> group, from `clippy-driver -W help`."""
    groups = {}
    if not path:
        return groups
    sect = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("Lint groups"):
                sect = "groups"
                continue
            if line.startswith("Lint checks") or line.startswith("Lint tools"):
                sect = None
                continue
            if sect != "groups":
                continue
            m = re.match(r"^\s*(\S+)\s{2,}(\S.*)$", line)
            if not m or m.group(1) == "name":
                continue
            g = m.group(1).replace("-", "_")
            if g in ("clippy::all", "warnings") or g.startswith("-"):
                continue
            base = g.split("::")[-1]
            if g.startswith("clippy::") and base not in CLIPPY_GROUPS:
                continue
            for member in m.group(2).split(","):
                member = member.strip().replace("-", "_")
                if member and (member not in groups or g.startswith("clippy::")):
                    groups[member] = g
    return groups


def group_of(lint, groups):
    if lint in groups:
        return groups[lint]
    if lint == "rustfmt":
        return "rustfmt"
    if lint.startswith("rustdoc::"):
        return "rustdoc"
    if lint.startswith("clippy::"):
        return "clippy::?"
    if re.match(r"^E\d{4}$", lint) or lint in LEVELS:
        return "compiler"
    if lint == "msg":
        return "?"
    return "rustc"


def run_filter(a):
    scope = Scope(a.scope)
    groups = load_groups(a.groups)
    items = []
    for inp in a.inputs:
        try:
            with open(inp, encoding="utf-8", errors="replace") as f:
                items.extend(parse(f, a.tool))
        except OSError as e:
            print(f"rust-filter: {e}", file=sys.stderr)
            return 2
    seen, uniq, dups = set(), [], 0
    for it in items:
        it.path, inside = rel_path(it.path, a.root, scope)
        k = it.key()
        if k in seen:
            dups += 1
            continue
        seen.add(k)
        uniq.append(it)
        if not inside:
            it.where = "outside"
        elif it.path not in scope.files:
            it.where = "other-file"
        elif any(scope.ours(it.path, n) for n in it.lines):
            it.where = "ours"
        else:
            it.where = "upstream"
    if a.files:
        uniq = [it for it in uniq if any(fnmatch.fnmatch(it.path, g) for g in a.files)]
    if a.lints:
        want = {x.replace("-", "_") for x in a.lints}
        uniq = [it for it in uniq if it.lint in want]
    ours = [it for it in uniq if it.where == "ours"]
    ours.sort(key=lambda it: (it.path, it.line, it.col, it.lint))
    where = collections.Counter(it.where for it in uniq)
    by_lint = collections.Counter(it.lint for it in ours)
    by_file = collections.Counter(it.path for it in ours)
    by_group = collections.Counter(group_of(it.lint, groups) for it in ours)
    levels = collections.Counter(it.level for it in ours)
    keys = collections.Counter()
    fmt_files = {}
    for it in ours:
        if it.lint == "rustfmt":
            h = fmt_files.setdefault(it.path, [0, 0, 0])
            h[0] += 1
            h[1] += it.minus
            h[2] += it.plus
            keys[f"{it.path}\trustfmt\t"] += 1
        else:
            keys[f"{it.path}\t{it.lint}\t{blank_numbers(it.msg)}"] += 1

    label = a.tool or "diagnostics"
    out = sys.stdout
    print(f"== {label}: {len(ours)} on our lines ({levels.get('error', 0)} errors), of {len(uniq)}: "
          f"{where.get('upstream', 0)} on upstream lines of files we touched, "
          f"{where.get('other-file', 0)} in files we never touched, {where.get('outside', 0)} outside the tree; "
          f"{dups} duplicates dropped", file=out)
    if scope.info.get("commit"):
        print(f"   scope: {scope.info.get('name', '?')} {scope.info['commit'][:12]}", file=out)
    if not a.no_list:
        shown = ours if a.list is None else ours[:a.list]
        for it in shown:
            print(it.short(), file=out)
        if len(shown) < len(ours):
            print(f"... and {len(ours) - len(shown)} more", file=out)
    if ours:
        print("-- by group", file=out)
        for g, n in by_group.most_common():
            print(f"{n:7d} {g}", file=out)
        print("-- by lint", file=out)
        for lint, n in by_lint.most_common():
            print(f"{n:7d} {lint} [{group_of(lint, groups)}]", file=out)
        print("-- by file", file=out)
        for p, n in by_file.most_common():
            print(f"{n:7d} {p}", file=out)
    if fmt_files:
        print("-- rustfmt: hunks, lines removed, lines added, by file", file=out)
        for p, (h, mi, pl) in sorted(fmt_files.items(), key=lambda kv: -kv[1][0]):
            print(f"{h:7d} {mi:6d} {pl:6d} {p}", file=out)
    if a.json:
        doc = {
            "tool": label,
            "scope": scope.info,
            "inputs": a.inputs,
            "total": len(uniq),
            "ours": len(ours),
            "errors": levels.get("error", 0),
            "upstream": where.get("upstream", 0),
            "other_file": where.get("other-file", 0),
            "outside": where.get("outside", 0),
            "duplicates": dups,
            "by_group": dict(by_group.most_common()),
            "by_lint": dict(by_lint.most_common()),
            "by_file": dict(by_file.most_common()),
            "fmt": {"hunks": sum(v[0] for v in fmt_files.values()),
                    "minus": sum(v[1] for v in fmt_files.values()),
                    "plus": sum(v[2] for v in fmt_files.values()),
                    "by_file": fmt_files},
            "keys": dict(sorted(keys.items())),
            "items": [it.short() for it in ours],
        }
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=False)
            f.write("\n")
    return 0


def compare_pair(old, new, label, out):
    """Keys whose count grew from old to new; prints them, returns how many."""
    ok, nk = old.get("keys", {}), new.get("keys", {})
    grew = 0
    for k, n in sorted(nk.items()):
        o = ok.get(k, 0)
        if n > o:
            path, lint, msg = (k.split("\t") + ["", ""])[:3]
            print(f"new {label}: {path}: {lint}: {msg} ({o} -> {n})", file=out)
            grew += n - o
    return grew


def run_compare(old, new):
    pairs = []
    if os.path.isdir(old) and os.path.isdir(new):
        for name in sorted(os.listdir(new)):
            if not name.endswith(".json"):
                continue
            if os.path.exists(os.path.join(old, name)):
                pairs.append((os.path.join(old, name), os.path.join(new, name), name[:-5]))
            else:
                print(f"note: {name} has no counterpart in {old}: not compared")
        only_old = [n for n in sorted(os.listdir(old)) if n.endswith(".json") and not os.path.exists(os.path.join(new, n))]
        if only_old:
            print(f"note: {len(only_old)} outputs of {old} not in this run, not compared: "
                  + ", ".join(n[:-5] for n in only_old))
    elif os.path.isfile(old) and os.path.isfile(new):
        pairs.append((old, new, os.path.basename(new)[:-5] if new.endswith(".json") else new))
    else:
        print("rust-filter: --compare takes two JSON files or two directories", file=sys.stderr)
        return 2
    total = 0
    for o, n, label in pairs:
        try:
            with open(o, encoding="utf-8") as f:
                od = json.load(f)
            with open(n, encoding="utf-8") as f:
                nd = json.load(f)
        except (OSError, ValueError) as e:
            print(f"rust-filter: {e}", file=sys.stderr)
            return 2
        total += compare_pair(od, nd, label, sys.stdout)
    print(f"compare: {total} new on our lines in {len(pairs)} outputs")
    return 1 if total else 0


SHORT_TARGETS = {
    "x86_64-unknown-linux-gnu": "linux", "x86_64-unknown-linux-musl": "musl",
    "aarch64-unknown-linux-musl": "aarch64-musl", "x86_64-unknown-freebsd": "freebsd",
    "x86_64-unknown-netbsd": "netbsd", "x86_64-unknown-illumos": "illumos",
    "x86_64-unknown-openbsd": "openbsd", "x86_64-unknown-dragonfly": "dragonfly",
    "x86_64-unknown-haiku": "haiku", "any": "any",
}
TOOL_ORDER = ("fmt", "clippy", "clippy-parser", "doc")


def read_kv(path):
    kv = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                k, sep, v = line.rstrip("\n").partition("=")
                if sep:
                    kv[k] = v
    except OSError:
        pass
    return kv


def top(counter, n):
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    s = ", ".join(f"{k} {v}" for k, v in items[:n])
    return s + (f", +{len(items) - n} more" if len(items) > n else "")


def test_counts(path):
    """(passed, failed, ignored, binaries) from cargo test's output."""
    p = f = i = b = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"^test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored", line)
                if m:
                    p, f, i, b = p + int(m.group(1)), f + int(m.group(2)), i + int(m.group(3)), b + 1
    except OSError:
        return None
    return p, f, i, b


def run_summarize(d):
    """The baseline text of a rust-lint.sh run directory."""
    raw, filt = os.path.join(d, "raw"), os.path.join(d, "filtered")
    info = read_kv(os.path.join(raw, "info.txt"))
    out = []
    w = out.append
    w("# RustAudit1's mechanical baseline (testvm/audit/rust-lint.sh; docs/design/rust-audit.md, RA2): what")
    w("# rustfmt, clippy, rustdoc and the compiler say on BackOtter's own lines only (rust-scope.sh's")
    w("# scope-NAME.txt beside this file), per crate, target and tool.  Written by rust-lint.sh --baseline;")
    w("# the raw and filtered output stay on the Mac that ran it, in BKO_AUDIT_LOCAL/rust/RUN.")
    w("")
    w(f"run {os.path.basename(os.path.normpath(d))} on {info.get('host', '?')}, {info.get('date', '?')}, "
      f"{info.get('seconds', '?')} s on the host")
    names = []
    for name in ("holt", "rustic_core", "rustic"):
        c = info.get(f"commit_{name}")
        if c is None:
            continue
        names.append(name)
        sc = os.path.join(d, "scope", f"scope-{name}.txt")
        tot = ""
        try:
            with open(sc, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("# total: "):
                        tot = line[len("# total: "):].strip()
        except OSError:
            pass
        extra = ""
        if name == "rustic" and info.get("rustic_core_tree"):
            extra = f", built against {os.path.basename(info['rustic_core_tree'])}"
        w(f"  {name:12s} {c[:12]}{extra}; scope: {tot or '?'}")
    tc = info.get("toolchain", "1.94.1")
    w(f"toolchain {tc}: {info.get('rustc_' + tc, '?')}; {info.get('clippy_' + tc, '?')}; {info.get('rustfmt_' + tc, '?')}")
    for k in sorted(info):
        if k.startswith("rustc_") and k != "rustc_" + tc:
            w(f"  also {k[6:]}: {info[k]}; {info.get('clippy_' + k[6:], '')}".rstrip("; "))
    w("lints: holt testvm/audit/rust/holt-lints.txt (RA3's set, default groups as warnings); "
      f"forks upstream's own plus {info.get('fork_flags', '?')}")
    w("")
    docs = {}
    try:
        names_f = sorted(os.listdir(filt))
    except OSError:
        names_f = []
    for fn in names_f:
        if fn.endswith(".json"):
            parts = fn[:-5].split(".", 2)
            if len(parts) == 3:
                with open(os.path.join(filt, fn), encoding="utf-8") as f:
                    docs[tuple(parts)] = json.load(f)

    def order(k):
        n, t, tool = k
        tl = tool.split("-")[0] if not tool.startswith("clippy-") else tool
        ti = TOOL_ORDER.index(tl) if tl in TOOL_ORDER else len(TOOL_ORDER)
        tg = list(SHORT_TARGETS).index(t) if t in SHORT_TARGETS else 99
        return (names.index(n) if n in names else 9, ti, tg, tool)

    w(f"{'crate':12s} {'target':12s} {'tool':14s} {'ours':>6s} {'errors':>6s} {'upstream':>8s} "
      f"{'untouched':>9s} {'outside':>7s}   (counts on our lines; upstream = upstream lines of files we touched)")
    for k in sorted(docs, key=order):
        doc = docs[k]
        n, t, tool = k
        if tool == "fmt":
            fm = doc.get("fmt", {})
            ours = f"{doc['ours']}"
            tail = f"   hunks; -{fm.get('minus', 0)} +{fm.get('plus', 0)} lines"
        else:
            ours, tail = f"{doc['ours']}", ""
        w(f"{n:12s} {SHORT_TARGETS.get(t, t):12s} {tool:14s} {ours:>6s} {doc.get('errors', 0):>6d} "
          f"{doc.get('upstream', 0):>8d} {doc.get('other_file', 0):>9d} {doc.get('outside', 0):>7d}{tail}")
    w("")
    for k in sorted(docs, key=order):
        doc = docs[k]
        if not doc.get("ours"):
            continue
        n, t, tool = k
        w(f"== {n} {SHORT_TARGETS.get(t, t)} {tool}: {doc['ours']} on our lines")
        if tool == "fmt":
            byf = doc.get("fmt", {}).get("by_file", {})
            w("   files (hunks): " + top({p: v[0] for p, v in byf.items()}, 8))
            continue
        w("   groups: " + top(doc.get("by_group", {}), 9))
        w("   lints:  " + top(doc.get("by_lint", {}), 10))
        w("   files:  " + top(doc.get("by_file", {}), 6))
    tests = []
    for name in names:
        for fn in sorted(os.listdir(os.path.join(raw, name))) if os.path.isdir(os.path.join(raw, name)) else []:
            m = re.match(r"^(.*)\.test-(.+)\.txt$", fn)
            if m:
                c = test_counts(os.path.join(raw, name, fn))
                if c:
                    tests.append(f"  {name} on {m.group(2)}: {c[0]} passed, {c[1]} failed, {c[2]} ignored "
                                 f"({c[3]} test binaries)")
    if tests:
        w("")
        w("tests:")
        out.extend(tests)
    steps = os.path.join(raw, "steps.tsv")
    if os.path.exists(steps):
        w("")
        w("steps: crate target tool exit seconds max-RSS-MiB ran-to-its-end")
        with open(steps, encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) >= 7:
                    rss = f"{int(p[5]) // 1024}" if p[5].isdigit() else p[5]
                    w(f"  {p[0]:12s} {SHORT_TARGETS.get(p[1], p[1]):12s} {p[2]:14s} {p[3]:>4s} {p[4]:>5s} {rss:>6s} {p[6]}")
    print("\n".join(out))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Keep the diagnostics on BackOtter's own lines (see the module doc).")
    p.add_argument("--scope")
    p.add_argument("--root", action="append", default=[])
    p.add_argument("--tool")
    p.add_argument("--files", action="append", default=[], help="GLOB[,GLOB...], repeatable")
    p.add_argument("--lints", action="append", default=[], help="LINT[,LINT...], repeatable")
    p.add_argument("--groups")
    p.add_argument("--json")
    p.add_argument("--list", type=int)
    p.add_argument("--no-list", action="store_true")
    p.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"))
    p.add_argument("--summarize", metavar="RUNDIR")
    p.add_argument("inputs", nargs="*")
    a = p.parse_args(argv)
    a.files = [x for v in a.files for x in v.split(",") if x]
    a.lints = [x for v in a.lints for x in v.split(",") if x]
    if a.compare:
        return run_compare(*a.compare)
    if a.summarize:
        return run_summarize(a.summarize)
    if not a.scope or not a.inputs:
        p.error("--scope and at least one INPUT are needed (or --compare OLD NEW)")
    return run_filter(a)


if __name__ == "__main__":
    sys.exit(main())
