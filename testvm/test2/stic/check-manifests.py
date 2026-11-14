#!/usr/bin/env python3
"""Static checks of Test2's input specs, without any host.

Usage: check-manifests.py [--spec FILE]... [--lint] [--suggest GUEST] [--json] [-q]

With no --spec it checks SticTest's testvm/test2/stic/manifests.json against
the 52 checks in testvm/stictest1 (contract R4.1, rulings R5 (b)-(d)):
  - every repo path a check's scripts reference is in its manifest: the
    references of check.sh and of every repo script it reaches, by
    $HERE/, $here/, $H/, $C/, $CMP/, ${BKO_TESTVM...}/ and the like, ../GUEST/,
    literal testvm/ paths, `source` and `.` chains, the words of a
    `for f in A B; do ... $X/$f` loop, and Python imports (import X, from X
    import ...) resolved against the script's directory and testvm/compare;
  - every listed path exists (a glob matches at least one file);
  - the manifest's bins are drive.py's: parse_check()'s OS, with RESTIC_FROM's
    restic (haiku6 reads its repository with Linux's restic-bko);
  - each check's harness holds testvm/stictest1/GUEST/** (R5 (b)), and its
    judge the per-guest judge.py, verdict.py and expected-coverage.json it uses
    and its limits.json entry (R3, R5 (d));
  - the plumbing files (the family's list) are not repeated as harness;
  - the BKO_TESTVM lint (U4.2): no check or helper in testvm/stictest1 names
    the synced tree (~/backotter-work/testvm, $WORK/testvm, $W/testvm, ...)
    other than through ${BKO_TESTVM:-...}.
It also reports (but doesn't fail on) the files under classes.json's
policy.watch that no spec or plumbing list names and no policy.unmapped_ignore
glob covers (R5 (a)), so the lists converge; --strict-watch makes each of
them an error (for t2 self-test, once the lists have converged).

--spec FILE (testvm/test2/holt/spec.json, testvm/test2/gate/layers.json,
testvm/test2/guest/spec.json, testvm/test2/img/spec.json) checks that spec the
same way (R5 (c)): every $HERE/, $T/, $C/ and literal testvm/ reference of a
listed driver or script is in its entry, its kind's or the family's plumbing,
and every listed path exists (PATH#SECTION: PATH does, and a section named
with no marker in it is a note; entry:PATH#a.b.c walks objects, {guest} first).
For holt it also checks each guest's kind against ht1.sh's dispatch (its
`case $G in` at the top) and reports a kind's variant file that isn't there.
--spec stic is manifests.json itself.

--lint runs the lint only; --suggest GUEST prints the references found for one
check (to write its entry).  Exit 0: no errors (reports allowed); 1: errors;
2: usage.  Stdlib only, Python 3.9+; it reads the files and writes nothing.
"""

import argparse
import fnmatch
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STIC = "testvm/stictest1"
MANIFESTS = "testvm/test2/stic/manifests.json"
LIMITS = "testvm/test2/stic/limits.json"
CLASSES = "testvm/test2/classes.json"
COMPARE = "testvm/compare"
LIMITS_ENTRY = "entry:" + LIMITS + "#{guest}"
JUDGE_NAMES = ("judge.py", "verdict.py", "expected-coverage.json")   # R5 (d): what reads records
WATCH_DEFAULT = {
    "stic": ["testvm/stictest1/**", "testvm/compare/**", "testvm/vm-*", "testvm/console-boot.py", "testvm/omarchy/**"],
    "holt": ["testvm/holttest1d/**", "testvm/vm-*", "testvm/compare/fsmeta.py", "testvm/redox/**",
             "testvm/sculpt-check.sh", "testvm/console-boot.py", "testvm/omarchy/**"],
    "gate": ["testvm/audit/**", "testvm/compare/**", "testvm/test2/gate/**"],
}
IGNORE_DEFAULT = ["**/*.md", "testvm/test2/**", "testvm/compare/affected.sh", "testvm/compare/wear-report.py",
                  "testvm/holttest1d/make-*.sh", "testvm/holttest1d/build-holt.sh", "testvm/holttest1d/ht1-debug.sh",
                  "testvm/holttest1d/ht1-lib-test.sh"]
SKIP_NAMES = ("__pycache__", ".DS_Store")

sys.path.insert(0, os.path.dirname(HERE))   # testvm/test2: t2lib
try:
    from t2lib import keys as _keys   # P1's spec merging (defaults, kinds), when it is there
except Exception:   # pragma: no cover - P1 not there yet
    _keys = None


# --- the repository ---------------------------------------------------------------

def repo_files(root=None):
    """Every file under testvm/, stick/ and holt/ (repo-relative), the copies' exclusions left out."""
    root = root or REPO
    out = []
    for top in ("testvm", "stick", "holt"):
        for dp, dns, fns in os.walk(os.path.join(root, top)):
            rel = os.path.relpath(dp, root).replace(os.sep, "/")
            dns[:] = sorted(d for d in dns if d not in SKIP_NAMES and not (rel + "/" + d).startswith("holt/target"))
            for f in fns:
                if f in SKIP_NAMES or f.endswith(".pyc"):
                    continue
                out.append(rel + "/" + f)
    return sorted(out)


def glob_rx(pattern):
    """A path glob: * and ? within a segment, ** across segments (P1's keys.glob_re)."""
    if _keys is not None:
        return _keys.glob_re(pattern)
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif pattern.startswith("**", i):
            out.append(".*"); i += 2
        elif pattern[i] == "*":
            out.append("[^/]*"); i += 1
        elif pattern[i] == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(pattern[i])); i += 1
    return re.compile("".join(out) + r"\Z")


def is_glob(p):
    return any(c in p for c in "*?[")


def norm(path):
    """a/b/../c -> a/c; None when it climbs out of the repository."""
    parts = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts)


# --- references in shell and Python ---------------------------------------------------

WORK = "@WORK@"           # ~/backotter-work on a host: its testvm/ is the synced repo tree
TESTVM = "@TESTVM@"       # ${BKO_TESTVM:-...}: testvm/ (the copy's or the synced one)
SEG = r"(?:[A-Za-z0-9_.+%@-]+|\$\{?[A-Za-z_]\w*\}?|\$\([^()]*\))"
REF_RX = re.compile(r"(@TESTVM@|@WORK@|\$\{?([A-Za-z_]\w*)\}?)[\"']{0,3}((?:/" + SEG + r")+)")
LIT_RX = re.compile(r"(?<![\w$/.@-])(testvm/[A-Za-z0-9_./-]+)")
ASSIGN_RX = re.compile(r"(?:^|[\s;&|(])(?:local\s+|export\s+|declare\s+|readonly\s+)?([A-Za-z_]\w*)="
                       r"(\"(?:[^\"\\]|\\.)*\"|'[^']*'|\$\((?:[^()]|\([^()]*\))*\)|\$\{(?:[^{}]|\{[^{}]*\})*\}[^\s;]*|[^\s;]+)")
SOURCE_RX = re.compile(r"(?:^|[\s;&|({])(?:source|\.)\s+(\S+)")
FOR_RX = re.compile(r"^\s*for\s+([A-Za-z_]\w*)\s+in\s+([^;]*?)\s*;\s*do\b(.*)$")
PY_SELFDIR_RX = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(?:os\.path\.dirname\(os\.path\.(?:abspath|realpath)\(__file__\)\)|"
                           r"Path\(__file__\)\.(?:resolve\(\)\.)?parent)\s*$")
PY_JOIN_RX = re.compile(r"(?:\b([A-Za-z_]\w*)\s*/\s*[\"']([A-Za-z0-9_.+-]+)[\"']|os\.path\.join\(\s*([A-Za-z_]\w*)\s*,\s*[\"']([A-Za-z0-9_./+-]+)[\"'])")
PY_IMPORT_RX = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\b|import\s+([A-Za-z_][\w.,\s]*?)(?:\s+as\s+\w+)?\s*(?:#.*)?$)")
HEREDOC_RX = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def strip_bko_testvm(line):
    """${BKO_TESTVM:-...} (braces balanced) -> @TESTVM@; ${BKO_WORK:-...} -> @WORK@;
    ~/backotter-work and $HOME/backotter-work -> @WORK@."""
    out, i = [], 0
    while i < len(line):
        m = re.match(r"\$\{(BKO_TESTVM|BKO_WORK):-", line[i:])
        if m:
            depth, j = 1, i + len(m.group(0))
            while j < len(line) and depth:
                depth += {"{": 1, "}": -1}.get(line[j], 0)
                j += 1
            out.append(TESTVM if m.group(1) == "BKO_TESTVM" else WORK)
            i = j
            continue
        out.append(line[i])
        i += 1
    s = "".join(out)
    s = re.sub(r"(?:\$HOME|\$\{HOME\}|~)/backotter-work(?=[/\"'\s;]|$)", WORK, s)
    return s


class Script:
    """One file's references: (repo path or glob, how, line number)."""

    def __init__(self, path, files):
        self.path = path
        self.files = files
        self.fileset = set(files)
        self.dir = os.path.dirname(path)
        self.vars = {}
        self.refs = []
        self.follow = []      # scripts it runs or sources (repo paths)
        try:
            with open(os.path.join(REPO, path), encoding="utf-8", errors="replace") as f:
                self.text = f.read()
        except OSError:
            self.text = ""
        if path.endswith(".py") or self.text.startswith("#!/usr/bin/env python"):
            self.python()
        elif self.text.startswith("#!") and "sh" in self.text.split("\n", 1)[0] or path.endswith(".sh") \
                or "/" in path and "." not in os.path.basename(path):
            self.shell()

    # values -------------------------------------------------------------------------
    def value(self, expr):
        """A shell word's repo meaning: ("repo", path) | ("work", path) | None."""
        e = expr.strip()
        if len(e) >= 2 and e[0] == e[-1] and e[0] in "\"'":
            e = e[1:-1]
        e = strip_bko_testvm(e)
        me = r"\"?\$(?:0|\{0\}|\{BASH_SOURCE\[0\]\}|BASH_SOURCE)\"?"
        real = r"\$\((?:realpath|readlink\s+-f)\s+" + me + r"\)"
        if re.fullmatch(real, e):
            return ("repo", self.path)
        if re.fullmatch(r"\$\((?:cd\s+\"?\$\(dirname\s+" + me + r"\)\"?\s*&&\s*pwd|dirname\s+\"?" + real
                        + r"\"?|dirname\s+" + me + r")\)", e):
            return ("repo", self.dir)
        m = re.fullmatch(r"\$\(dirname\s+\"?\$\{?([A-Za-z_]\w*)\}?\"?\)", e)
        if m:
            v = self.vars.get(m.group(1))
            return (v[0], os.path.dirname(v[1])) if v else None
        m = re.fullmatch(r"\$\(cd\s+\"?(.*?)\"?\s*&&\s*pwd\)", e)
        if m:
            return self.value(m.group(1))
        m = re.match(r"(@TESTVM@|@WORK@|\$\{?([A-Za-z_]\w*)\}?)(.*)\Z", e)
        if not m:
            return None
        rest = m.group(3)
        if m.group(1) == TESTVM:
            base = ("repo", "testvm")
        elif m.group(1) == WORK:
            base = ("work", "")
        else:
            base = self.vars.get(m.group(2))
        if base is None or (rest and not rest.startswith("/")) or re.search(r"[$`(){}*?\[\s]", rest):
            return None
        p = norm((base[1] + rest) if base[1] else rest.lstrip("/"))
        if p is None:
            return None
        if base[0] == "work":
            return ("repo", p) if p == "testvm" or p.startswith("testvm/") else ("work", p)
        return ("repo", p)

    def add(self, path, how, n):
        if path is None:
            return
        if path.endswith(".md"):
            return   # documentation named in a message
        self.refs.append((path, how, n))

    def resolve_ref(self, base_tok, var, tail):
        """A reference BASE/TAIL: a repo path, a glob for its dynamic parts, or None."""
        if base_tok == TESTVM:
            base = ("repo", "testvm")
        elif base_tok == WORK:
            base = ("work", "")
        else:
            base = self.vars.get(var)
        if base is None:
            return None
        segs = [s for s in tail.split("/") if s]
        dyn = False
        out = []
        for s in segs:
            if s.startswith("$"):
                out.append("*")
                dyn = True
            else:
                out.append(s)
        p = norm(((base[1] + "/") if base[1] else "") + "/".join(out))
        if p is None:
            return None
        if base[0] == "work" and not (p == "testvm" or p.startswith("testvm/")):
            return None
        if base[0] != "work" and not p.startswith(("testvm/", "stick/", "holt/")):
            return None
        return ("dyn" if dyn else "ref", p)

    # shell ----------------------------------------------------------------------------
    def shell(self):
        loops = []          # (var, words, active)
        heredoc = None
        for n, raw in enumerate(self.text.split("\n"), 1):
            if heredoc:
                if raw.strip() == heredoc:
                    heredoc = None
                continue
            if raw.lstrip().startswith("#"):
                continue
            hd = HEREDOC_RX.search(raw)
            if hd and hd.group(1):            # a quoted heredoc's body is text (a script for the guest, Python)
                heredoc = hd.group(2)
            line = strip_bko_testvm(raw)
            if re.match(r"^\s*done\b", line) and loops:
                loops.pop()
            fm = FOR_RX.match(line)
            if fm:
                words = fm.group(2).split()
                static = [w.strip("\"'") for w in words if "$" not in w and "*" not in w and "`" not in w]
                if static and len(static) == len(words):
                    loops.append((fm.group(1), static))
                else:
                    loops.append((fm.group(1), None))
                body = fm.group(3)
                if re.search(r"\bdone\b", body):
                    self.scan(line, n, loops)
                    loops.pop()
                    continue
            for am in ASSIGN_RX.finditer(line):
                v = self.value(am.group(2))
                if v is not None:
                    self.vars[am.group(1)] = v
                elif am.group(1) in self.vars and not am.group(2).startswith("${" + am.group(1)):
                    del self.vars[am.group(1)]
            self.scan(line, n, loops)

    def scan(self, line, n, loops):
        for var, words in loops:
            if words is None:
                continue
            if re.search(r"\$\{?" + var + r"\b\}?", line):
                for w in words:
                    self.scan_one(re.sub(r"\$\{?" + var + r"\b\}?", w, line), n)
                return
        self.scan_one(line, n)

    def scan_one(self, line, n):
        for m in REF_RX.finditer(line):
            r = self.resolve_ref(m.group(1), m.group(2), m.group(3))
            if r:
                self.add(r[1], r[0], n)
        for m in LIT_RX.finditer(line):
            p = norm(m.group(1).rstrip("."))
            if p and not p.endswith("/"):
                self.add(p, "lit", n)
        for m in SOURCE_RX.finditer(line):
            v = self.value(m.group(1).rstrip(";&|)"))
            if v and v[0] == "repo":
                self.follow.append(v[1])

    # Python ----------------------------------------------------------------------------
    def python(self):
        for n, line in enumerate(self.text.split("\n"), 1):
            if line.lstrip().startswith("#"):
                continue
            m = PY_SELFDIR_RX.match(line)
            if m:
                self.vars[m.group(1)] = ("repo", self.dir)
            for jm in PY_JOIN_RX.finditer(line):
                var, name = (jm.group(1), jm.group(2)) if jm.group(1) else (jm.group(3), jm.group(4))
                if var in self.vars and self.vars[var][0] == "repo":
                    self.add(norm(self.vars[var][1] + "/" + name), "ref", n)
            im = PY_IMPORT_RX.match(line)
            if im:
                mods = [im.group(1)] if im.group(1) else [x.strip().split()[0] for x in im.group(2).split(",") if x.strip()]
                for mod in mods:
                    top = mod.split(".")[0]
                    for d in (self.dir, COMPARE):
                        cand = f"{d}/{top}.py"
                        if cand in self.fileset:
                            self.add(cand, "import", n)
                            self.follow.append(cand)
                            break


def closure(start, files, stop=()):
    """Every reference reachable from START, following the repo scripts it names
    (source chains, scripts it runs or copies, Python imports), the files in STOP
    (plumbing) left unfollowed.  {path: [(from, line, how)]}, and the dynamic ones.
    A directory named on its own (C=.../compare) is no reference."""
    fileset = set(files)
    dirs = {f.rsplit("/", i)[0] for f in files for i in range(1, f.count("/") + 1)}
    seen, todo, refs, dyn = set(), [start], {}, {}
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        s = Script(p, files)
        for path, how, n in s.refs:
            if how == "dyn":
                dyn.setdefault(path, []).append((p, n))
                continue
            if path in dirs:
                continue
            refs.setdefault(path, []).append((p, n, how))
            if path in fileset and path not in stop and is_script(path):
                todo.append(path)
        for f in s.follow:
            if f in fileset and f not in stop:
                todo.append(f)
    return refs, dyn


def is_script(path):
    base = os.path.basename(path)
    if base.endswith((".sh", ".py")):
        return True
    if "." in base:
        return False
    try:
        with open(os.path.join(REPO, path), "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    return head.startswith(b"#!") and (b"sh" in head.split(b"\n")[0] or b"python" in head.split(b"\n")[0])


# --- the BKO_TESTVM lint (U4.2) ------------------------------------------------------------

LINT_RX = re.compile(r"(\$\{?(?:WORK|W)\}?[\"']{0,3}/testvm\b|backotter-work/testvm\b)")


def lint(root=None):
    """Lines in testvm/stictest1/*/* that name the synced tree other than through
    ${BKO_TESTVM:-...}: [(path, line number, text)]."""
    root = root or REPO
    bad = []
    base = os.path.join(root, STIC)
    for g in sorted(os.listdir(base)):
        gd = os.path.join(base, g)
        if not os.path.isdir(gd):
            continue
        for dp, dns, fns in os.walk(gd):
            dns[:] = [d for d in dns if d not in SKIP_NAMES]
            for fn in sorted(fns):
                if fn in SKIP_NAMES or fn.endswith(".pyc"):
                    continue
                p = os.path.join(dp, fn)
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        lines = fh.read().split("\n")
                except OSError:
                    continue
                for n, line in enumerate(lines, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    stripped = strip_bko_testvm(line).replace(WORK + "/testvm", "@SYNCED@")
                    if LINT_RX.search(stripped) or "@SYNCED@" in stripped:
                        bad.append((os.path.relpath(p, root).replace(os.sep, "/"), n, line.strip()[:160]))
    return bad


# --- specs --------------------------------------------------------------------------------

def load_json(path):
    with open(os.path.join(REPO, path) if not os.path.isabs(path) else path) as f:
        return json.load(f)


def spec_entries(data):
    """{name: raw entry} of a spec file (P1's layouts: entries, cells, checks, guests, layers, or the top level)."""
    for k in ("entries", "cells", "checks", "guests", "layers"):
        c = data.get(k)
        if isinstance(c, dict):
            return {n: e for n, e in c.items() if isinstance(e, dict)}
        if isinstance(c, list):
            return {e["name"]: e for e in c if isinstance(e, dict) and "name" in e}
    reserved = {"v", "family", "plumbing", "soft", "notes", "kinds", "defaults", "variants", "policy", "comment",
                "_comment", "description", "doc", "version", "about"}
    return {k: v for k, v in data.items() if isinstance(v, dict) and k not in reserved}


def merged_entry(data, name, path):
    """The entry merged over the file's defaults and its kind, as keys.resolve sees it."""
    if _keys is not None:
        sf = _keys.SpecFile("x", path, data, "")
        try:
            return sf.entry(name)
        except KeyError:
            pass
    own = spec_entries(data)[name]
    merged = {}
    parts = [data.get("defaults") or {}]
    if own.get("kind") and isinstance(data.get("kinds"), dict):
        parts.append(data["kinds"].get(own["kind"]) or {})
    parts.append(own)
    for p in parts:
        for k, v in p.items():
            if isinstance(v, list) and isinstance(merged.get(k), list):
                merged[k] = merged[k] + [x for x in v if x not in merged[k]]
            elif isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = dict(merged[k], **v)
            else:
                merged[k] = v
    return merged


def subst(item, guest, plat=None, kind=None):
    for word, val in (("guest", guest), ("plat", plat), ("kind", kind)):
        if val is None or not isinstance(item, str):
            continue
        item = item.replace("{" + word + "}", val)
        item = "/".join(val if seg == word.upper() else seg for seg in item.split("/"))
    return item


def listed_paths(items, files):
    """The repo paths a spec list covers: (set of files, [paths or globs that match nothing]).
    A "?PATH" that isn't there is no error; PATH#SECTION is PATH."""
    have, dead = set(), []
    for it in items or []:
        if not isinstance(it, str):
            it = it.get("path") if isinstance(it, dict) else None
            if not it:
                continue
        optional = it.startswith("?")
        it = it[1:] if optional else it
        if "#" in it and not it.startswith(("entry:", "shared:", "host:", "git:")):
            it = it.split("#", 1)[0]
        if optional and not is_glob(it) and it not in files:
            continue
        if it.startswith(("entry:", "shared:", "host:", "git:")):
            if it.startswith("entry:"):
                f = it[len("entry:"):].split("#", 1)[0]
                if f not in files:
                    dead.append(it)
            continue
        if is_glob(it):
            rx = glob_rx(it)
            hits = [f for f in files if rx.match(f)]
            if not hits:
                dead.append(it)
            have.update(hits)
        elif it in files:
            have.add(it)
        else:
            dead.append(it)
    return have, dead


def covered(path, have, globs):
    return path in have or any(glob_rx(g).match(path) for g in globs)


def drive_specs():
    """drive.py's parse_check OS and RESTIC_FROM, by importing it (it reads the checks here)."""
    sys.path.insert(0, os.path.join(REPO, STIC))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("st1_drive", os.path.join(REPO, STIC, "drive.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        guests = m.all_guests()
        return m, guests, m.load_specs(guests)
    finally:
        sys.path.pop(0)


def check_stic(files, path=MANIFESTS, out=None):
    """Errors and reports for manifests.json against the checks."""
    errs, reps = [], []
    data = load_json(path)
    entries = spec_entries(data)
    drive, guests, specs = drive_specs()
    plumbing, pdead = listed_paths(data.get("plumbing"), set(files))
    errs += [f"{path}: plumbing {p}: no such file" for p in pdead]
    stop = set(plumbing)
    for g in guests:
        if g not in entries:
            errs.append(f"{path}: no entry for {g} (testvm/stictest1/{g}/check.sh)")
    for g in sorted(entries):
        if g not in guests:
            errs.append(f"{path}: entry {g} has no testvm/stictest1/{g}/check.sh")
    limits = {}
    try:
        limits = load_json(LIMITS)
    except (OSError, ValueError) as e:
        errs.append(f"{LIMITS}: {e}")
    for g in guests:
        if g not in entries:
            continue
        e = merged_entry(data, g, path)
        sub = lambda xs: [subst(x, g) for x in (xs or [])]
        harness, judge = sub(e.get("harness")), sub(e.get("judge"))
        hset, hdead = listed_paths(harness, set(files))
        jset, jdead = listed_paths(judge, set(files))
        errs += [f"{g}: harness {p}: matches no file" for p in hdead]
        errs += [f"{g}: judge {p}: matches no file" for p in jdead]
        globs = [x for x in harness + judge if isinstance(x, str) and is_glob(x)]
        own = f"{STIC}/{g}/**"
        if own not in harness:
            errs.append(f"{g}: harness lacks {own} (R5 (b))")
        if LIMITS_ENTRY.replace("{guest}", g) not in judge:
            errs.append(f"{g}: judge lacks entry:{LIMITS}#{g} (R3)")
        elif g not in limits:
            reps.append(f"{g}: no limits.json entry (no LIMIT classed yet)")
        for p in sorted(hset & stop):
            errs.append(f"{g}: harness {p} is family plumbing: list it only there (decision 4's canaries)")
        # bins against drive.py
        s = specs[g]
        want = {f"{s.os}/rustic-bko", f"{drive.RESTIC_FROM.get(s.os, s.os)}/restic-bko"}
        got = set(sub(e.get("bins")))
        if got != want:
            errs.append(f"{g}: bins {sorted(got)}, drive.py hands the check {sorted(want)} (parse_check, RESTIC_FROM)")
        if e.get("os") and e["os"] != s.os:
            errs.append(f"{g}: os {e['os']}, drive.py's parse_check says {s.os}")
        # references
        refs, dyn = closure(f"{STIC}/{g}/check.sh", files, stop)
        for p, where in sorted(refs.items()):
            if p in stop:
                continue
            if p not in set(files):
                # A script that runs in the guest names its own copies there
                # (extras.sh's $here/rt-common.sh is /root/rt-common.sh): a note.
                if not p.startswith(f"{STIC}/{g}/"):
                    reps.append(f"{g}: {where[0][0]}:{where[0][1]} names {p}, which isn't in the repository")
                continue
            if not covered(p, hset | jset, globs):
                errs.append(f"{g}: {where[0][0]}:{where[0][1]} uses {p}: not in its manifest")
            if os.path.basename(p) in JUDGE_NAMES and not covered(p, jset, [x for x in judge if is_glob(x)]):
                errs.append(f"{g}: {p} reads records: list it under judge too (R5 (d))")
        for pat, where in sorted(dyn.items()):
            rx = glob_rx(pat)
            hits = [f for f in files if rx.match(f)]
            if not hits:
                continue
            if not any(covered(h, hset | jset, globs) or h in stop for h in hits):
                errs.append(f"{g}: {where[0][0]}:{where[0][1]} uses {pat}: none of its {len(hits)} matches is in its manifest")
        if out is not None:
            out[g] = {"refs": sorted(refs), "dynamic": sorted(dyn)}
    return errs, reps, data


# --- the other families' specs (R5 (c)) ------------------------------------------------------------

def ht1_dispatch(guest):
    """The driver ht1.sh's `case $G in` sends GUEST to: ht1-bsd.sh, ht1-offline.sh or ht1.sh."""
    try:
        with open(os.path.join(REPO, "testvm/holttest1d/ht1.sh")) as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"\bcase \$G in\n(.*?)\nesac", text, re.S)
    if not m:
        return None
    for pats, target in re.findall(r"^\s*([^)\n#]+)\)\s*\n\s*exec [^\n]*?/(ht1-[a-z]+\.sh)", m.group(1), re.M):
        if any(fnmatch.fnmatchcase(guest, p.strip()) for p in pats.split("|")):
            return target
    return "ht1.sh"


def kind_driver(kind, entry):
    """The driver a holt entry runs: its "driver", else the most specific ht1 driver its
    harness lists, else by the kind's name."""
    if entry.get("driver"):
        return os.path.basename(entry["driver"])
    listed = [os.path.basename(x) for x in entry.get("harness") or []
              if isinstance(x, str) and re.fullmatch(r"testvm/holttest1d/ht1(-bsd|-offline)?\.sh", x)]
    for d in ("ht1-bsd.sh", "ht1-offline.sh", "ht1.sh"):
        if d in listed:
            return d
    return {"linux": "ht1.sh", "bsd": "ht1-bsd.sh", "offline": "ht1-offline.sh"}.get(kind)


def section_notes(items, name, path):
    """Notes for each PATH#SECTION of ITEMS with no "# t2-section: SECTION" marker in PATH (keys.py
    keys the whole file then), and each entry:PATH#KEY whose KEY isn't there."""
    out = []
    for it in items or ():
        if not isinstance(it, str):
            continue
        it = it.lstrip("?")
        if it.startswith("entry:"):
            p, _, k = it[len("entry:"):].partition("#")
            try:
                cur = load_json(p)
            except (OSError, ValueError):
                continue
            found = isinstance(cur, dict) and k in cur
            if not found:
                found = True
                for part in k.split("."):
                    if not isinstance(cur, dict) or part not in cur:
                        found = False
                        break
                    cur = cur[part]
            if not found and not p.endswith("limits.json"):     # (limits.json starts empty: R3)
                out.append(f"{path}#{name}: {it}: no such entry (it keys as absent)")
        elif "#" in it and not it.startswith(("shared:", "host:", "git:")):
            p, _, sec = it.partition("#")
            try:
                with open(os.path.join(REPO, p), "rb") as f:
                    data = f.read()
            except OSError:
                continue
            if _keys is not None and _keys.section_text(data, sec) is None:
                out.append(f"{path}#{name}: {it}: no such section (the whole file keys it)")
    return out


def check_spec(path, files):
    """R5 (c) for holt/spec.json or gate/layers.json: every reference of the scripts
    an entry runs is in the entry, its kind or the family's plumbing.  For holt the
    scripts are the guest's driver (by ht1.sh's dispatch: ht1.sh listed as the
    bsd and offline kinds' dispatcher isn't read past it) and what it sources or
    runs, plus the entry's other listed scripts; a file the kind's "per_guest"
    names is used by some guests only, so a reference to it is no error."""
    errs, reps = [], []
    data = load_json(path)
    fam = data.get("family") or ("holt" if "holt" in path else "gate" if "gate" in path else
                                 "guest" if "guest/" in path else "img" if "img/" in path else "stic")
    if fam == "stic":
        e, r, _ = check_stic(files, path)
        return e, r
    fileset = set(files)
    try:
        ignore = [glob_rx(g) for g in (load_json(CLASSES).get("policy") or {}).get("unmapped_ignore") or []]
    except (OSError, ValueError):
        ignore = []
    plumbing, pdead = listed_paths(data.get("plumbing"), fileset)
    errs += [f"{path}: plumbing {p}: no such file" for p in pdead]
    entries = spec_entries(data)
    done = set()
    for kn, kd in sorted((data.get("kinds") or {}).items()):
        plats = sorted({(lambda m: m.get("plat") or m.get("platform") or "linux")(merged_entry(data, n, path))
                        for n, own in entries.items() if own.get("kind") == kn})
        for vn, v in sorted((kd.get("variants") or {}).items()):
            items = [y for k in ("harness", "judge", "plumbing") for x in (v.get(k) or []) if isinstance(x, str)
                     for y in ([x.replace("{plat}", p) for p in plats] if "{plat}" in x else [x])]
            _, vdead = listed_paths(items, fileset)
            reps += [f"{path}: kinds.{kn}.variants.{vn}: {p}: matches no file" for p in vdead]
    somewhere, listed, mates = set(), {}, {}    # the guest family's shared job.sh names what only some entries list
    if fam == "guest":
        for name in entries:
            e = merged_entry(data, name, path)
            listed[name] = {x.lstrip("?") for x in (e.get("harness") or []) if isinstance(x, str)}
            somewhere |= {x.split("#", 1)[0] for x in listed[name]}
            mates.setdefault((e.get("plat"), (e.get("checks") or {}).get("pm")), []).append(name)
        for names_ in mates.values():       # a package manager's or OS's guests share their in-guest.sh sections
            for name in names_:
                others = [listed[n] for n in names_ if n != name]
                for sec in sorted(set.intersection(*others) - listed[name]) if others else ():
                    if "#" in sec:
                        errs.append(f"{path}#{name}: {sec}: every other guest of its package manager lists it")

    def guest_exempt(entry, p):
        """A path only some guests list: true unless it's a shared one this guest's peers all list."""
        if p not in somewhere:
            return False
        if re.fullmatch(r"testvm/test2/guest/(new-guest|probe-[a-z]+)\.sh", p):
            return True     # (per-guest by design: established: false, redox, sculpt2610)
        peers = next(ns for ns in mates.values() if entry in ns)
        others = [{x.split("#", 1)[0] for x in listed[n]} for n in peers if n != entry]
        return not (others and all(p in o for o in others))

    for name in sorted(entries):
        e = merged_entry(data, name, path)
        guest = e.get("guest") or (name if fam in ("holt", "guest") else None)
        plat, kind = e.get("plat") or e.get("platform") or (name if fam == "img" else None), e.get("kind")
        lists = [subst(x, guest, plat, kind) for k in ("harness", "judge", "plumbing") for x in (e.get(k) or [])]
        reps += section_notes(lists, name, path)
        if e.get("driver"):
            lists.append(e["driver"])
        have, dead = listed_paths(lists, fileset)
        errs += [f"{path}#{name}: {p}: matches no file" for p in dead]
        globs = [x for x in lists if isinstance(x, str) and is_glob(x)]
        cond = set(e.get("per_guest") or {}) if isinstance(e.get("per_guest"), dict) else set(e.get("per_guest") or [])
        for v in (((data.get("kinds") or {}).get(kind) or {}).get("variants") or {}).values():
            # a variant's own files: the driver's branch for it uses them, the base cell doesn't
            cond |= {subst(x, guest, plat, kind) for x in (v.get("harness") or []) if isinstance(x, str)}
        starts = []
        if fam == "holt":
            drv = kind_driver(kind, e)
            want = ht1_dispatch(guest)
            if want and drv and want != drv:
                errs.append(f"{path}#{name}: kind {kind} runs {drv}; ht1.sh sends {guest} to {want}")
            if drv:
                starts.append("testvm/holttest1d/" + drv)
            starts += [p for p in sorted(have) if is_script(p) and not re.fullmatch(r"testvm/holttest1d/ht1(-bsd|-offline)?\.sh", p)]
        else:
            starts = sorted(p for p in have if is_script(p))
        for st in starts:
            refs, _dyn = closure(st, files, plumbing)
            for p, where in sorted(refs.items()):
                if p in plumbing or p in cond:
                    continue
                if guest_exempt(name, p) or (fam in ("guest", "img") and any(r.match(p) for r in ignore)):
                    continue    # (D2: what decides no need, and the family's own runner, aren't keyed)
                key = (name, p)
                if p not in fileset:
                    if key not in done:
                        reps.append(f"{path}#{name}: {where[0][0]}:{where[0][1]} names {p}, which isn't in the repository")
                    done.add(key)
                    continue
                if not covered(p, have | plumbing, globs) and key not in done:
                    errs.append(f"{path}#{name}: {where[0][0]}:{where[0][1]} uses {p}: not in the entry, its kind or the plumbing")
                done.add(key)
    return errs, reps


def watch_report(files, spec_paths):
    """Files under policy.watch that no spec names (R5 (a)): reported, never an error."""
    try:
        classes = load_json(CLASSES)
    except (OSError, ValueError):
        classes = {}
    pol = classes.get("policy") or {}
    watch = pol.get("watch") or WATCH_DEFAULT
    ignore = pol.get("unmapped_ignore") or IGNORE_DEFAULT
    reps = []
    named, named_globs = set(), []
    for sp in spec_paths:
        try:
            data = load_json(sp)
        except (OSError, ValueError):
            continue
        items = list(data.get("plumbing") or [])
        for k in ("defaults",):
            for v in (data.get(k) or {}).values():
                items += v if isinstance(v, list) else []
        for kd in (data.get("kinds") or {}).values():
            for k in ("harness", "judge", "plumbing"):
                items += kd.get(k) or []
            for var in (kd.get("variants") or {}).values():
                for k in ("harness", "judge", "plumbing"):
                    items += var.get(k) or []
        for name, e in spec_entries(data).items():
            for k in ("harness", "judge", "plumbing"):
                plat = e.get("plat") or (name if data.get("family") == "img" else None)
                items += [subst(x, e.get("guest") or name, plat, e.get("kind")) for x in (e.get(k) or [])]
        for it in items:
            if not isinstance(it, str):
                continue
            it = it.lstrip("?")
            if it.startswith(("entry:", "host:", "git:")):
                continue
            it = it.split("#", 1)[0]
            if it.startswith("shared:"):
                it = it[len("shared:"):]
                # ~/backotter-work/cmp-run.sh links into the synced testvm/compare (contract 1.3)
                it = {"cmp-run.sh": "testvm/compare/cmp-run.sh"}.get(it, it)
            if is_glob(it):
                named_globs.append(it)
            else:
                named.add(it)
    rx_named = [glob_rx(g.replace("{plat}", "*").replace("{guest}", "*").replace("PLAT", "*"))
                for g in named_globs if "GUEST" not in g]
    plats = "linux freebsd netbsd openbsd dragonfly illumos haiku".split()
    for it in [x for x in named if "{plat}" in x or "/PLAT/" in x]:     # a kind's or variant's per-platform file
        for p in plats:
            named.add(it.replace("{plat}", p).replace("/PLAT/", f"/{p}/"))
    rx_ignore = [glob_rx(g) for g in ignore]
    for fam, globs in sorted(watch.items()):
        if fam in ("guest", "img") and not os.path.exists(os.path.join(REPO, f"testvm/test2/{fam}/spec.json")):
            reps.append(f"watch {fam}: testvm/test2/{fam}/spec.json isn't there yet: skipped")
            continue
        rx_watch = [glob_rx(g) for g in globs]
        for f in files:
            if not any(r.match(f) for r in rx_watch):
                continue
            if f in named or any(r.match(f) for r in rx_named) or any(r.match(f) for r in rx_ignore):
                continue
            reps.append(f"watch {fam}: {f}: no spec names it (name it in a spec or in policy.unmapped_ignore)")
    return reps


# --- main ----------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(usage="check-manifests.py [--spec FILE]... [--lint] [--strict-watch] [--suggest GUEST] [--json] [-q]")
    ap.add_argument("--spec", action="append", default=[], metavar="FILE",
                    help="a spec to check (stic, holt/spec.json, gate/layers.json); default: manifests.json")
    ap.add_argument("--lint", action="store_true", help="the BKO_TESTVM lint only")
    ap.add_argument("--suggest", metavar="GUEST", help="print the references found for one check")
    ap.add_argument("--strict-watch", action="store_true",
                    help="a policy.watch file no spec names is an error (R5 (a): each makes its family's cells due)")
    ap.add_argument("--json", action="store_true", help="the result as JSON")
    ap.add_argument("-q", "--quiet", action="store_true", help="errors only")
    a = ap.parse_args(argv)
    files = repo_files()
    if a.suggest:
        stop = set()
        try:
            stop, _ = listed_paths(load_json(MANIFESTS).get("plumbing"), set(files))
        except (OSError, ValueError):
            pass
        refs, dyn = closure(f"{STIC}/{a.suggest}/check.sh", files, stop)
        for p, where in sorted(refs.items()):
            tag = "plumbing" if p in stop else "missing" if p not in set(files) else ""
            print(f"{p}\t{where[0][0]}:{where[0][1]}\t{tag}")
        for p, where in sorted(dyn.items()):
            print(f"{p}\t{where[0][0]}:{where[0][1]}\tdynamic")
        return 0
    errs, reps = [], []
    bad = lint()
    errs += [f"lint: {p}:{n}: names the synced tree, not ${{BKO_TESTVM:-...}}: {t}" for p, n, t in bad]
    if not a.lint:
        specs = a.spec or [MANIFESTS]
        for sp in specs:
            sp = {"stic": MANIFESTS, "holt": "testvm/test2/holt/spec.json", "gate": "testvm/test2/gate/layers.json",
                  "guest": "testvm/test2/guest/spec.json", "img": "testvm/test2/img/spec.json"}.get(sp, sp)
            if not os.path.exists(os.path.join(REPO, sp)):
                errs.append(f"{sp}: no such spec")
                continue
            try:
                e, r = check_spec(sp, files)
            except (ValueError, KeyError) as x:
                e, r = [f"{sp}: {x}"], []
            errs += e
            reps += r
        wr = watch_report(files, [MANIFESTS, "testvm/test2/holt/spec.json", "testvm/test2/gate/layers.json",
                                  "testvm/test2/guest/spec.json", "testvm/test2/img/spec.json"])
        if a.strict_watch:
            errs += [x for x in wr if "isn't there yet" not in x]
            reps += [x for x in wr if "isn't there yet" in x]
        else:
            reps += wr
    if a.json:
        print(json.dumps({"errors": errs, "reports": reps}, indent=1))
    else:
        for x in errs:
            print("ERROR " + x)
        if not a.quiet:
            for x in reps:
                print("note  " + x)
        print(f"check-manifests: {len(errs)} errors, {len(reps)} notes"
              + ("" if a.lint else f" ({', '.join(a.spec or ['stic'])})"))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
