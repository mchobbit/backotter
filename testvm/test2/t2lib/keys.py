"""Keys: what a cell's result depends on (contract 1.3, rulings R2, R3, R14).

A cell's inputs are {group: {name: value}}.  The hard groups (bin, harness,
judge, guest, xguest, rescuer, host) make its key:

    key = sha256(canonical({"v": 1, "cell": CELL, "hard": {g: inputs[g] for g in HARD if inputs.get(g)}}))

and every group, plumbing and soft included, gets a 16-hex digest:

    groups[g] = sha256(canonical(inputs.get(g, {})))[:16]

Values:
    sha256:HEX   a file's or binary's bytes (a repo symlink: its target's text, as git keeps it)
    gen:HEX      an image-state generation
    img:HEX      a content sum of disk images (t2-host imgsum's cache); img:unknown until one is made
    ver:TEXT     a version, at most 120 characters
    git:HEX40    an external repository's commit
    dir:HEX      a host directory's digest (sha256 over sorted "relpath<TAB>sha256" lines, links as "->target")
    entry:HEX    sha256 of the canonical JSON of one entry of a JSON file (R3: limits.json); "a.b.c" walks objects
    fp:HEX       a guest's fingerprint digest (image-fingerprint.json), the projection of the cell's family
    code:HEX     bko-holt by its code digest CODE (R14), built or not
    pending:ID   a binary not built yet, by its source identity (contract 1.6)
    absent       an optional file that isn't there; missing: a required one that isn't,
                 or anything else that couldn't be measured (a binary not in its set,
                 a facts part that failed).  Every missing input is in Resolved.unknown,
                 so a writer's row with one is valid: false

Sources give a repo path's bytes, the same whichever reads them (U1.6):
GitSource(repo, commit) through git cat-file --batch, TreeSource(dir) for a
working tree or a private copy, MapSource([...]) for Test1 rows whose
testvm/stictest1 came from one tree and testvm from another.  Sources see
only testvm/, stick/ and holt/, less __pycache__/, *.pyc, .DS_Store and
holt/target/: what a private copy holds.

Spec files (one per family; P3, P4 and P5 own them):
    stic  testvm/test2/stic/manifests.json   one entry per check (stic:GUEST)
    holt  testvm/test2/holt/spec.json        per guest, merged over its kind (holt:GUEST[+VARIANT])
    gate  testvm/test2/gate/layers.json      one entry per layer (gate:LAYER[@HOST])
    guest testvm/test2/guest/spec.json       per guest, merged over its kind (guest:GUEST)
    img   testvm/test2/img/spec.json         per platform (img:PLAT)
The canonical layout is

    {"v": 1, "plumbing": [...], "soft": [...], "defaults": {...},
     "kinds": {KIND: ENTRY}, "entries": {NAME: ENTRY}}

and an entry may also sit at the top level or under "cells", "checks",
"guests" or "layers".  An entry with "kind" is merged over kinds[KIND] (and
the file's "defaults"): lists add up, dicts merge, scalars are the entry's.
"variants": {VARIANT: ENTRY} adds a variant's own inputs; its "replace": [FIELD...]
makes those fields replace the kind's and the entry's instead of adding to them.  In
strings, the path segments GUEST, PLAT and KIND (or {guest}, {plat}, {kind}) are the
entry's guest, platform ("plat") and kind.  Every field is optional:

    bins         binaries: "OS/NAME" ("linux/restic-bko"), "OS" (that OS's *-bko
                 binaries in the set), globs ("freebsd/*-bko"), "PLAT/bko-holt".
                 Each archiver binary NAME also keys "NAME#digest": ver: of the
                 digest line of its .digest file (R2), or absent.  Holt keys
                 PLAT/bko-holt as code:CODE (R14); other families as the sha256 of
                 HOST_ROOT/holt-bin/CODE's build, or absent.
    bins_from_image  bins are the image's own: "PLAT/NAME" from facts.images[PLAT].bins,
                 "PLAT/test-image" the pin's sha256, "PLAT/release-image" SHA256SUMS' .img line
    harness      repo paths and globs (* within a segment, ** across them); PATH#SECTION:
                 the lines of PATH between "# t2-section: SECTION" markers (no marker: the whole file);
    judge        "?PATH" for an optional one; "entry:FILE#KEY" (R3); "shared:PATH"
    guest_files  the guest's files (default vm-run-options, ssh-user,
                 console-passphrase, luks-passphrase, zfs-passphrase, no-ssh): GUEST:FILE
    guests       more guests the cell boots (their generation and files)
    xguest       guest names (GUEST:generation) and "host:PATH" host inputs
    rescuer      rescuer images ("rescuer.qcow2", "@firmware": the UEFI one for
                 a UEFI guest where the host has it, "@image": rescuer/img-PLAT, the
                 pinned rescue image), "bin/*.deb", "bin/bcachefs",
                 guest names (a live rescuer's generation)
    host         host facts, hard: kernel, zfs, qemu, ovmf, rustc, go, pkg:NAME
    host_inputs  host paths (dir: digests), group host unless {"path", "group"} says
    gitrepos     restic, rustic_core, rustic: git: of branch backotter in BKO_GIT (harness)
    plumbing     more plumbing files for this entry (beside the family's list)
    soft         the soft facts (default kernel, zfs, qemu, ovmf for stic and holt; none for gate)
    pin, plat, kind, guest, notes   not inputs

A facts dict is one host's facts_host.py output.  Options (opts) a caller can give:
    set          the archivers' set: "bko-bin" (default) or "roundNAME" (a round's directory)
    rustic_set   the set holt's rustic-bko comes from (default "bko-bin")
    pending      the source identity of binaries not built yet: their values become pending:ID
    holt_code    CODE, when the caller has it (else computed from the source)
    holt_sha256  {PLAT: sha256} of the bko-holt binaries a HoltTest1 or --holt-bin run used:
                 holt then keys sha256: (R14), not code:
    gitrepos     {name: commit} instead of reading BKO_GIT
"""

import json
import os
import re
import subprocess
import threading

from . import (GROUPS, HARD, SCOPE, bko_git, canonical, excluded, sha256_hex, tree_files)

SPEC_PATHS = {
    "stic": "testvm/test2/stic/manifests.json",
    "holt": "testvm/test2/holt/spec.json",
    "gate": "testvm/test2/gate/layers.json",
    "guest": "testvm/test2/guest/spec.json",
    "img": "testvm/test2/img/spec.json",
}
GUEST_FILES = ("vm-run-options", "ssh-user", "console-passphrase", "luks-passphrase", "zfs-passphrase", "no-ssh")
SOFT_DEFAULT = {"stic": ("kernel", "zfs", "qemu", "ovmf"), "holt": ("kernel", "zfs", "qemu", "ovmf"), "gate": (),
                "guest": ("kernel", "zfs"), "img": ("qemu", "ovmf")}
CONTAINERS = ("entries", "cells", "checks", "guests", "layers")
RESERVED = {"v", "family", "plumbing", "soft", "notes", "kinds", "defaults", "variants", "policy",
            "comment", "_comment", "description", "doc", "version"} | set(CONTAINERS)
VALUE_RE = re.compile(r"(sha256:[0-9a-f]{64}|gen:[0-9a-f]{64}|img:([0-9a-f]{64}|unknown)|ver:.{0,120}|"
                      r"git:[0-9a-f]{40}|dir:[0-9a-f]{64}|entry:[0-9a-f]{64}|code:[0-9a-f]{64}|fp:[0-9a-f]{64}|"
                      r"pending:.{1,200}|"
                      r"absent|missing)\Z", re.S)
UNKNOWN_IMG = "img:unknown"
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
SECTION_RE = re.compile(rb"^[ \t]*#[ \t]*t2-section:[ \t]*(\S+)[ \t]*$")


# --- values ---------------------------------------------------------------------

def ver(text):
    """ver: of a version text, cut to 120 characters (None: absent)."""
    if text is None:
        return "absent"
    text = " ".join(str(text).split())
    return "ver:" + text[:120]


def sha(hexd, optional=False):
    if hexd:
        return "sha256:" + hexd
    return "absent" if optional else "missing"


def valid_value(v):
    return isinstance(v, str) and bool(VALUE_RE.match(v))


# --- digests ----------------------------------------------------------------------

def key(cell, inputs):
    """The cell's hard key (64 hex)."""
    hard = {g: inputs[g] for g in HARD if inputs.get(g)}
    return sha256_hex(canonical({"v": 1, "cell": cell, "hard": hard}))


def groups(inputs):
    """Every group's 16-hex digest, plumbing and soft included."""
    return {g: sha256_hex(canonical(inputs.get(g, {})))[:16] for g in GROUPS}


def changed_groups(groups_a, groups_b, which=GROUPS):
    """The groups whose digests differ."""
    return [g for g in which if (groups_a or {}).get(g) != (groups_b or {}).get(g)]


def diff_inputs(old, new):
    """[(group, name, old value, new value)] for every name that differs (None: not there)."""
    out = []
    for g in GROUPS:
        a, b = (old or {}).get(g, {}), (new or {}).get(g, {})
        for n in sorted(set(a) | set(b)):
            if a.get(n) != b.get(n):
                out.append((g, n, a.get(n), b.get(n)))
    return out


# --- sources ----------------------------------------------------------------------

def _in_scope(path, scope):
    return not scope or path.split("/", 1)[0] in scope


class TreeSource:
    """A directory's files as repo paths: a checkout's working tree or a private
    copy.  With MOUNT, repo path MOUNT/REST is ROOT/REST (MapSource's parts).
    Hashes are cached per instance: make a new one to see later changes."""

    def __init__(self, root, mount="", scope=SCOPE):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.mount = mount.strip("/")
        self.scope = None if self.mount else scope
        self._sha = {}
        self._files = None

    def __repr__(self):
        return f"TreeSource({self.root!r}" + (f", mount={self.mount!r})" if self.mount else ")")

    def owns(self, path):
        return not self.mount or path == self.mount or path.startswith(self.mount + "/")

    def _abs(self, path):
        if self.mount:
            if not self.owns(path):
                return None
            path = path[len(self.mount):].lstrip("/")
        elif not _in_scope(path, self.scope):
            return None
        if excluded(path) or path.startswith("/") or ".." in path.split("/"):
            return None
        return os.path.join(self.root, path) if path else self.root

    def files(self):
        if self._files is None:
            found = tree_files(self.root, self.scope)
            pre = self.mount + "/" if self.mount else ""
            self._files = sorted(pre + p for p in found)
        return list(self._files)

    def read(self, path):
        p = self._abs(path)
        if p is None:
            return None
        try:
            if os.path.islink(p):
                return os.readlink(p).encode("utf-8", "surrogateescape")
            if not os.path.isfile(p):
                return None
            with open(p, "rb") as f:
                return f.read()
        except OSError:
            return None

    def sha256(self, path):
        if path not in self._sha:
            data = self.read(path)
            self._sha[path] = None if data is None else sha256_hex(data)
        return self._sha[path]

    def exists(self, path):
        return self.sha256(path) is not None


class GitSource:
    """A commit's files, read with git cat-file --batch (on the Mac)."""

    def __init__(self, repo, commit="HEAD", scope=SCOPE):
        self.repo = repo
        self.scope = scope
        out = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "-q", commit + "^{commit}"],
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise ValueError(f"{repo}: no commit {commit}")
        self.commit = out.stdout.strip()
        self._tree = None
        self._cat = None
        self._lock = threading.Lock()
        self._sha = {}

    def __repr__(self):
        return f"GitSource({self.repo!r}, {self.commit[:12]})"

    def _load(self):
        if self._tree is not None:
            return
        cmd = ["git", "-C", self.repo, "ls-tree", "-r", "-z", "--full-tree", self.commit]
        if self.scope:
            cmd += ["--"] + list(self.scope)
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
        tree = {}
        for rec in out.split(b"\0"):
            if not rec:
                continue
            meta, _, path = rec.partition(b"\t")
            mode, typ, obj = meta.split(b" ")
            if typ != b"blob":
                continue   # a submodule's commit
            p = path.decode("utf-8", "surrogateescape")
            if not excluded(p):
                tree[p] = (mode.decode(), obj.decode())
        self._tree = tree

    def files(self):
        self._load()
        return sorted(self._tree)

    def mode(self, path):
        self._load()
        return self._tree.get(path, (None, None))[0]

    def read(self, path):
        self._load()
        ent = self._tree.get(path)
        if ent is None:
            return None
        with self._lock:
            if self._cat is None or self._cat.poll() is not None:
                self._cat = subprocess.Popen(["git", "-C", self.repo, "cat-file", "--batch"],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            self._cat.stdin.write(ent[1].encode() + b"\n")
            self._cat.stdin.flush()
            head = self._cat.stdout.readline().split()
            if len(head) != 3 or head[1] == b"missing":
                return None
            size = int(head[2])
            data = b""
            while len(data) < size:
                chunk = self._cat.stdout.read(size - len(data))
                if not chunk:
                    raise IOError("git cat-file ended early")
                data += chunk
            self._cat.stdout.read(1)   # the newline after the object
            return data

    def sha256(self, path):
        if path not in self._sha:
            data = self.read(path)
            self._sha[path] = None if data is None else sha256_hex(data)
        return self._sha[path]

    def exists(self, path):
        self._load()
        return path in self._tree

    def close(self):
        with self._lock:
            if self._cat is not None:
                try:
                    self._cat.stdin.close()
                    self._cat.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    self._cat.kill()   # our own git cat-file
                    self._cat.wait()
                self._cat.stdout.close()
                self._cat = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class MapSource:
    """Several sources by repo path prefix, the longest mount first: a Test1 row's
    testvm/stictest1 from the tree its checks ran from, testvm from the synced one."""

    def __init__(self, sources):
        self.sources = sorted(sources, key=lambda s: -len(getattr(s, "mount", "")))

    def __repr__(self):
        return f"MapSource({self.sources!r})"

    def _pick(self, path):
        for s in self.sources:
            if s.owns(path):
                return s
        return None

    def files(self):
        out = set()
        for s in self.sources:
            out.update(p for p in s.files() if self._pick(p) is s)
        return sorted(out)

    def read(self, path):
        s = self._pick(path)
        return None if s is None else s.read(path)

    def sha256(self, path):
        s = self._pick(path)
        return None if s is None else s.sha256(path)

    def exists(self, path):
        return self.sha256(path) is not None


# --- globs ---------------------------------------------------------------------------

def glob_re(pattern):
    """A path glob as a regex: * and ? within a segment, ** across segments."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def is_glob(p):
    return any(ch in p for ch in "*?[")


def expand_glob(pattern, names):
    rx = glob_re(pattern)
    return sorted(n for n in names if rx.match(n))


# --- specs ---------------------------------------------------------------------------

class SpecFile:
    def __init__(self, family, path, data, sha):
        self.family, self.path, self.data, self.sha256 = family, path, data, sha

    def __repr__(self):
        return f"SpecFile({self.family}, {self.path}, {self.sha256[:12] if self.sha256 else None})"

    def names(self):
        """Every entry name in the file."""
        d = self.data
        for k in CONTAINERS:
            if isinstance(d.get(k), dict):
                return sorted(d[k])
            if isinstance(d.get(k), list):
                return sorted(e["name"] for e in d[k] if isinstance(e, dict) and "name" in e)
        return sorted(k for k, v in d.items() if isinstance(v, dict) and k not in RESERVED)

    def raw_entry(self, name):
        d = self.data
        for k in CONTAINERS:
            c = d.get(k)
            if isinstance(c, dict) and isinstance(c.get(name), dict):
                return c[name]
            if isinstance(c, list):
                for e in c:
                    if isinstance(e, dict) and e.get("name") == name:
                        return e
        v = d.get(name)
        if isinstance(v, dict) and name not in RESERVED:
            return v
        raise KeyError(f"{self.path}: no entry {name}")

    def entry(self, name, variant=None):
        """The entry for NAME merged over the file's defaults and its kind (and VARIANT)."""
        own = self.raw_entry(name)
        parts = []
        if isinstance(self.data.get("defaults"), dict):
            parts.append(self.data["defaults"])
        kind = own.get("kind")
        kinds = self.data.get("kinds")
        if kind and isinstance(kinds, dict) and isinstance(kinds.get(kind), dict):
            parts.append(kinds[kind])
        parts.append(own)
        if variant:
            for src in (kinds.get(kind, {}) if kind and isinstance(kinds, dict) else {}, own):
                v = (src.get("variants") or {}).get(variant) if isinstance(src, dict) else None
                if isinstance(v, dict):
                    parts.append(v)
        merged = {}
        for p in parts:
            replace = set(p.get("replace") or ())
            for k, v in p.items():
                if k in ("variants", "replace"):
                    continue
                if k in replace:
                    merged[k] = v
                elif isinstance(v, list) and isinstance(merged.get(k), list):
                    merged[k] = merged[k] + [x for x in v if x not in merged[k]]
                elif isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k] = dict(merged[k], **v)
                else:
                    merged[k] = v
        return merged


class Specs:
    """The three spec files from a source (OVERRIDES: {family: dict} for tests)."""

    def __init__(self, src, paths=None, overrides=None):
        self.src = src
        self.paths = dict(SPEC_PATHS, **(paths or {}))
        self.overrides = overrides or {}
        self._files = {}
        self._json = {}

    def get(self, family):
        if family not in self._files:
            if family in self.overrides:
                data = self.overrides[family]
                self._files[family] = SpecFile(family, self.paths.get(family, f"<{family}>"), data,
                                               sha256_hex(canonical(data)))
            else:
                path = self.paths.get(family)
                raw = self.src.read(path) if path else None
                if raw is None:
                    self._files[family] = None
                else:
                    self._files[family] = SpecFile(family, path, json.loads(raw.decode("utf-8")), sha256_hex(raw))
        return self._files[family]

    def json(self, path):
        """(data, sha256) of a JSON file of the source, or (None, None)."""
        if path not in self._json:
            raw = self.src.read(path)
            self._json[path] = (None, None) if raw is None else (json.loads(raw.decode("utf-8")), sha256_hex(raw))
        return self._json[path]


# --- cells ----------------------------------------------------------------------------

class Cell:
    """family:NAME[+VARIANT][@HOST]: stic:debian13, holt:debian13+oneshot, gate:loop:linux@chopin."""

    def __init__(self, cell):
        fam, sep, rest = cell.partition(":")
        if not sep or not rest:
            raise ValueError(f"bad cell {cell!r}")
        self.cell, self.family = cell, fam
        self.host = None
        if fam == "gate" and "@" in rest:
            rest, self.host = rest.rsplit("@", 1)
        self.variant = None
        if fam == "holt" and "+" in rest:
            rest, self.variant = rest.split("+", 1)
        self.name = rest

    def __repr__(self):
        return f"Cell({self.cell})"


def parse_cell(cell):
    return Cell(cell)


# --- the holt code digest -----------------------------------------------------------

def holt_code_paths(paths):
    """The paths CODE covers (contract 2.3): holt/** less holt/target/, holt/tests/ and *.md."""
    return sorted(p for p in paths if p.startswith("holt/") and not p.startswith(("holt/target/", "holt/tests/"))
                  and not p.endswith(".md"))


def holt_code(src):
    """CODE: sha256 over sorted "path<TAB>sha256" lines of holt's code (LICENSE stays: main.rs includes it)."""
    return sha256_hex("".join(f"{p}\t{src.sha256(p)}\n" for p in holt_code_paths(src.files())))


# --- inputs ------------------------------------------------------------------------------

class Resolved:
    """A cell's inputs and what goes beside them: the spec entry used, notes for
    the plan, the inputs the writer couldn't measure, the pin."""

    def __init__(self, cell):
        self.cell = cell
        self.inputs = {g: {} for g in GROUPS}
        self.spec = None
        self.entry = {}
        self.notes = []
        self.unknown = []
        self.pin = None

    def note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    def put(self, group, name, value):
        if not valid_value(value):
            raise ValueError(f"{self.cell}: bad value for {group}/{name}: {value!r}")
        self.inputs[group][name] = value

    def done(self):
        # Every "missing" value is an input that couldn't be measured (a required file
        # or glob with nothing there, a binary not in its set's sums, a facts part
        # that failed): unknown, so a writer's row with it is valid: false.
        for g, x in self.inputs.items():
            for n, v in x.items():
                if v == "missing" and f"{g}/{n}" not in self.unknown:
                    self.unknown.append(f"{g}/{n}")
        self.unknown = list(dict.fromkeys(self.unknown))
        self.inputs = {g: dict(sorted(v.items())) for g, v in self.inputs.items() if v}
        return self

    @property
    def key(self):
        return key(self.cell, self.inputs)

    @property
    def groups(self):
        return groups(self.inputs)


def _subst(s, guest, plat, kind):
    if not isinstance(s, str):
        return s
    for word, val in (("GUEST", guest), ("PLAT", plat), ("KIND", kind)):
        if val is None:
            continue
        s = s.replace("{" + word.lower() + "}", val)
        s = "/".join(val if seg == word else seg for seg in s.split("/"))
    return s


def _list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _part_failed(facts, part):
    """True when FACTS has PART but its probe failed (facts_host: null, named in errors)."""
    if not isinstance(facts, dict) or part not in facts:
        return False
    return facts[part] is None or any(str(e).startswith(part + ":") for e in facts.get("errors") or [])


def _host_fact(facts, name):
    """A host or soft fact's value: ver:..., absent, or missing when the versions part failed."""
    if _part_failed(facts, "versions"):
        return "missing"
    return ver(_fact_version(facts, name))


def _fact_version(facts, name):
    v = (facts or {}).get("versions") or {}
    if name.startswith("pkg:"):
        return (v.get("pkgs") or {}).get(name[4:])
    return v.get(name)


def fingerprint_of(g):
    """The guest facts' fingerprint record when it speaks for the current generation, else None."""
    fp = (g or {}).get("fingerprint")
    if not isinstance(fp, dict) or not g.get("generation") or fp.get("generation") != g["generation"]:
        return None
    return fp


def _guest_identity(r, facts, guest, group, family=None):
    g = ((facts or {}).get("guests") or {}).get(guest)
    name = f"{guest}:generation"
    if g is None:
        r.put(group, name, "missing")
        r.note(f"guest {guest} not on {(facts or {}).get('host', 'this host')}")
        r.unknown.append(f"{group}/{name}")
        return None
    fp = fingerprint_of(g) if group == "guest" and family in ("stic", "holt") else None
    digest = (fp or {}).get(family)
    if isinstance(digest, str) and HEX64_RE.match(digest):
        # what the cell depends on of the image; the generation stays beside it, soft
        r.put(group, f"{guest}:fingerprint", "fp:" + digest)
        r.put("soft", name, "gen:" + g["generation"])
        fine = (fp or {}).get("fine")
        if family == "stic" and isinstance(fine, str) and HEX64_RE.match(fine):
            r.put("soft", f"{guest}:fp-fine", "fp:" + fine)
    elif g.get("generation"):
        r.put(group, name, "gen:" + g["generation"])
    elif g.get("imgsum"):
        r.put(group, name, g["imgsum"] if g["imgsum"].startswith("img:") else "img:" + g["imgsum"])
    else:
        r.put(group, name, UNKNOWN_IMG)
        r.note(f"guest identity unknown: treated as changed ({guest})")
        r.unknown.append(f"{group}/{name}")
    return g


def _file_input(r, group, src, specs, item, guest, plat, kind, facts):
    item = _subst(item, guest, plat, kind)
    optional = item.startswith("?")
    if optional:
        item = item[1:]
    if item.startswith("entry:"):
        path, _, k = item[len("entry:"):].partition("#")
        data, _ = specs.json(path)
        name = f"{path}#{k}"
        found, val = _walk_entry(data, k)
        if data is None:
            r.put(group, name, "absent" if optional else "missing")
        elif not found:
            r.put(group, name, "absent")
        else:
            r.put(group, name, "entry:" + sha256_hex(canonical(val)))
        return
    if item.startswith("shared:"):
        v = ((facts or {}).get("plumbing") or {}).get(item, None)
        if v is None and item not in ((facts or {}).get("plumbing") or {}):
            r.put(group, item, "missing")
            r.unknown.append(f"{group}/{item}")
            r.note(f"{item} not in the facts")
        else:
            r.put(group, item, sha(v, optional=True) if v else "absent")
        return
    if item.startswith("host:"):
        _host_input(r, group, item[len("host:"):], facts)
        return
    if "#" in item:
        _section_input(r, group, src, item, optional)
        return
    if is_glob(item):
        hits = expand_glob(item, src.files())
        if not hits:
            r.put(group, item, "absent" if optional else "missing")
        for p in hits:
            r.put(group, p, sha(src.sha256(p)))
        return
    r.put(group, item, sha(src.sha256(item), optional))


def _walk_entry(data, k):
    """(found, value) of K in DATA: the literal top-level key, else a dotted walk through objects."""
    if not isinstance(data, dict):
        return False, None
    if k in data:
        return True, data[k]
    cur = data
    for part in k.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def section_text(data, name):
    """The lines of DATA (bytes) in section NAME, joined (a name may appear more than once):
    each "# t2-section: X" marker opens X and closes what was open.  None: no such marker."""
    out, cur, seen = [], None, False
    for line in data.splitlines(keepends=True):
        m = SECTION_RE.match(line.rstrip(b"\r\n"))
        if m:
            cur = m.group(1).decode("utf-8", "replace")
            seen = seen or cur == name
            continue
        if cur == name:
            out.append(line)
    return b"".join(out) if seen else None


def _section_input(r, group, src, item, optional):
    path, _, section = item.partition("#")
    data = src.read(path)
    if data is None:
        r.put(group, item, "absent" if optional else "missing")
        return
    text = section_text(data, section)
    if text is None:
        r.note(f"no section {section} in {path}: the whole file keys it")
        text = data
    r.put(group, item, "sha256:" + sha256_hex(text))


def _host_input(r, group, path, facts):
    hi = (facts or {}).get("host_inputs") or {}
    name = "host:" + path
    if path not in hi:
        r.put(group, name, "missing")
        r.unknown.append(f"{group}/{name}")
        r.note(f"host input {path} not in the facts")
    else:
        r.put(group, name, hi[path] or "absent")


def _set_facts(facts, set_name):
    return ((facts or {}).get("sets") or {}).get(set_name)


def _image_of(facts, plat):
    return (((facts or {}).get("images") or {}).get(plat)) or {}


def _pin_sha(im):
    """The pin's sha256 while it is the image's (a stale pin says nothing about the image now)."""
    pin = (im or {}).get("pin") or {}
    return pin.get("sha256") if pin.get("current") else None


def _image_bin(r, facts, item, plat):
    """One of an image's own binaries, or one of the images (works on empty facts)."""
    im = _image_of(facts, plat)
    name = item.rsplit("/", 1)[-1]
    if not im:
        r.put("bin", item, UNKNOWN_IMG if name == "test-image" else "missing")
        if facts and "images" in facts:
            r.note(f"no current image of {plat} on {facts.get('host', 'this host')}")
    elif name == "test-image":
        s = _pin_sha(im)
        r.put("bin", item, "sha256:" + s if s else UNKNOWN_IMG)
        if not s:
            r.note(f"{plat}'s test image isn't pinned yet: its identity is unknown")
            r.unknown.append(f"bin/{item}")
    elif name == "release-image":
        r.put("bin", item, sha((im.get("release") or {}).get("sha256")))
    else:
        r.put("bin", item, sha((im.get("bins") or {}).get(name)))


def _bin_inputs(r, c, entry, facts, src, opts, plat, guest, kind):
    set_name = opts.get("set") or "bko-bin"
    for item in _list(entry.get("bins")):
        item = _subst(item, guest, plat, kind)
        if entry.get("bins_from_image"):
            _image_bin(r, facts, item, plat)
            continue
        if item.endswith("/bko-holt") or item == "bko-holt":
            p = item[:-len("/bko-holt")] if "/" in item else "linux"
            _holt_bin(r, c, p, facts, src, opts)
            continue
        if c.family == "holt" and item.endswith("rustic-bko"):
            sname, pending = opts.get("rustic_set") or "bko-bin", None   # a built set: never pending
        else:
            sname, pending = set_name, opts.get("pending")
        sf = _set_facts(facts, sname)
        sums = (sf or {}).get("sums") or {}
        if "/" not in item:
            names = expand_glob(item + "/*-bko", sums)
        elif is_glob(item):
            names = [n for n in expand_glob(item, sums) if n.endswith("-bko")]
        else:
            names = [item]
        if not names:
            names = [item if "/" in item else item + "/*-bko"]
        for n in names:
            if pending:
                r.put("bin", n, "pending:" + str(pending))
                r.put("bin", n + "#digest", "pending:" + str(pending))
                r.note("not built: " + str(pending))
                continue
            if sf is None:
                r.put("bin", n, "missing")
                r.unknown.append(f"bin/{n}")
                r.note(f"no set {sname} on {(facts or {}).get('host', 'this host')}")
                continue
            # a binary the spec names that its set doesn't have can't be measured
            r.put("bin", n, sha(sums.get(n)) if sums.get(n) else "missing")
            if n.endswith("-bko"):
                d = ((sf.get("digests") or {}).get(n))
                r.put("bin", n + "#digest", ver(d) if d else "absent")


def _holt_bin(r, c, plat, facts, src, opts):
    name = f"{plat}/bko-holt"
    shas = opts.get("holt_sha256")
    if c.family == "holt":
        if shas is not None:
            r.put("bin", name, sha(shas.get(plat)))
            return
        code = opts.get("holt_code") or holt_code(src)
        r.put("bin", name, "code:" + code)
        return
    code = opts.get("holt_code") or holt_code(src)
    if _part_failed(facts, "holt_bins"):
        r.put("bin", name, "missing")
        return
    hb = ((facts or {}).get("holt_bins") or {}).get(code)
    v = ((hb or {}).get("sums") or {}).get(plat)
    r.put("bin", name, sha(v) if v else "absent")


def _rescuer_inputs(r, entry, facts, guest, g):
    resc = (facts or {}).get("rescuers") or {}
    images = resc.get("images") or {}
    binf = resc.get("bin") or {}
    for item in _list(entry.get("rescuer")):
        item = _subst(item, guest, entry.get("plat"), entry.get("kind"))
        if item == "@firmware":
            uefi = (g or {}).get("firmware") == "uefi"
            item = "rescuer-uefi.qcow2" if uefi and "rescuer-uefi.qcow2" in images else "rescuer.qcow2"
            if uefi and item == "rescuer.qcow2":
                r.note("BIOS rescuer for a UEFI source")
        if item == "@image":
            plat = entry.get("plat")
            s = _pin_sha(_image_of(facts, plat))
            name = f"rescuer/img-{plat}"
            r.put("rescuer", name, "sha256:" + s if s else UNKNOWN_IMG)
            if not s:
                r.note(f"{plat}'s test image isn't pinned yet: its identity is unknown")
                r.unknown.append("rescuer/" + name)
            continue
        if item.startswith("bin/"):
            hits = expand_glob(item[4:], binf) if is_glob(item) else [item[4:]]
            if not hits:
                r.put("rescuer", item, "missing")
            for h in hits:
                r.put("rescuer", "bin/" + h, sha(binf.get(h)))
            continue
        if item.endswith((".qcow2", ".img", ".raw")):
            im = images.get(item)
            name = "rescuer/" + item
            if im is None:
                r.put("rescuer", name, "missing")
                r.unknown.append("rescuer/" + name)
                r.note(f"no rescuer {item} on {(facts or {}).get('host', 'this host')}")
            elif im.get("sha256"):
                r.put("rescuer", name, "sha256:" + im["sha256"])
            elif im.get("imgsum"):
                r.put("rescuer", name, im["imgsum"])
            else:
                r.put("rescuer", name, UNKNOWN_IMG)
                r.unknown.append("rescuer/" + name)
                r.note(f"rescuer identity unknown: treated as changed ({item})")
            continue
        _guest_identity(r, facts, item[len("guest:"):] if item.startswith("guest:") else item, "rescuer")


def _git_head(name, opts):
    given = (opts.get("gitrepos") or {})
    if name in given:
        return given[name]
    repo = os.path.join(bko_git(), name)
    out = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "-q", "backotter^{commit}"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def resolve(cell, src, facts, specs=None, opts=None):
    """The cell's inputs from source SRC (a working tree, a copy or a commit) and one
    host's FACTS, by its family's spec entry: a Resolved (.inputs, .spec, .notes,
    .unknown, .pin, .key, .groups).  KeyError when the spec has no entry for it."""
    c = Cell(cell)
    opts = dict(opts or {})
    specs = specs if specs is not None else Specs(src)
    sf = specs.get(c.family)
    if sf is None:
        raise KeyError(f"no spec file for {c.family} ({SPEC_PATHS.get(c.family)})")
    entry = sf.entry(c.name, c.variant)
    r = Resolved(cell)
    r.entry = entry
    r.spec = {"path": f"{sf.path}#{c.name}", "sha256": sf.sha256}
    r.pin = entry.get("pin")
    guest = entry.get("guest") or (c.name if c.family in ("stic", "holt", "guest") else None)
    plat = entry.get("plat") or entry.get("platform") or (c.name if c.family == "img" else None)
    kind = entry.get("kind")

    _bin_inputs(r, c, entry, facts, src, opts, plat, guest, kind)
    for group in ("harness", "judge"):
        for item in _list(entry.get(group)):
            _file_input(r, group, src, specs, item, guest, plat, kind, facts)
    for name in _list(entry.get("gitrepos")):
        head = _git_head(name, opts)
        r.put("harness", "git:" + name, ("git:" + head) if head else "missing")
        if not head:
            r.unknown.append("harness/git:" + name)
    g = None
    if c.family in ("stic", "holt", "guest") and guest:
        for gname in [guest] + [x for x in _list(entry.get("guests")) if x != guest]:
            gf = _guest_identity(r, facts, gname, "guest", c.family)
            if gname == guest:
                g = gf
            for fname in _list(entry.get("guest_files", GUEST_FILES)):
                if gf is None:
                    r.put("guest", f"{gname}:{fname}", "missing")
                    continue
                v = (gf.get("files") or {}).get(fname)
                r.put("guest", f"{gname}:{fname}", sha(v) if v and v != "absent" else "absent")
    for item in _list(entry.get("xguest")):
        if isinstance(item, str) and item.startswith("host:"):
            _host_input(r, "xguest", item[len("host:"):], facts)
        else:
            _guest_identity(r, facts, item, "xguest")
    _rescuer_inputs(r, entry, facts, guest, g)
    for name in _list(entry.get("host")):
        r.put("host", name, _host_fact(facts, name))
    for item in _list(entry.get("host_inputs")):
        if isinstance(item, dict):
            _host_input(r, item.get("group", "host"), item["path"], facts)
        else:
            _host_input(r, "host", item, facts)
    for item in _list(sf.data.get("plumbing")) + _list(entry.get("plumbing")):
        _file_input(r, "plumbing", src, specs, item, guest, plat, kind, facts)
    soft = entry.get("soft", sf.data.get("soft", SOFT_DEFAULT.get(c.family, ())))
    for name in _list(soft):
        r.put("soft", name, _host_fact(facts, name))
    return r.done()


def inputs(cell, src, facts, specs=None, opts=None):
    """{group: {name: value}} for CELL (resolve() for the rest)."""
    return resolve(cell, src, facts, specs, opts).inputs


def measure(cell, src, facts, specs=None, opts=None):
    """What a writer puts in its row: inputs, key, groups, spec, plus notes and
    the inputs it couldn't measure (unknown: a row with any is valid: false)."""
    r = resolve(cell, src, facts, specs, opts)
    return {"inputs": r.inputs, "key": r.key, "groups": r.groups, "spec": r.spec,
            "notes": r.notes, "unknown": r.unknown, "pin": r.pin}


def wants(specs, families=("stic", "holt", "gate"), sets=()):
    """What facts_host.py must be asked for (its --want) to resolve these
    families' cells: the shared: plumbing files, host inputs, sets, and images when
    the img family is asked for.  (t2 adds holt_bin_dirs for a HoltTest2 --holt-bin
    DIR, and images for a word with +img cells.)"""
    shared, host_inputs = set(), set()
    for fam in families:
        sf = specs.get(fam)
        if sf is None:
            continue
        items = list(_list(sf.data.get("plumbing")))
        for n in sf.names():
            try:
                e = sf.entry(n)
            except KeyError:
                continue
            for k in ("harness", "judge", "plumbing", "xguest"):
                items += [x for x in _list(e.get(k)) if isinstance(x, str)]
            for x in _list(e.get("host_inputs")):
                host_inputs.add(x["path"] if isinstance(x, dict) else x)
        for x in items:
            x = x.lstrip("?")
            if x.startswith("shared:"):
                shared.add(x)
            elif x.startswith("host:"):
                host_inputs.add(x[len("host:"):])
    out = {"sets": sorted(set(sets) | {"bko-bin"}), "plumbing": sorted(shared), "host_inputs": sorted(host_inputs)}
    if "img" in families:
        out["images"] = True
    return out
