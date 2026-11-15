"""A batch file, parsed (docs/design/batch.md §3, §4.4; piece B1).  Pure: no I/O, an include
is read through the READ the caller passes.

    parse(text, path, read) -> Batch        BatchError (every syntax error, FILE:LINE) when it can't
    refusals(batch, table, known=None, allow_update=False) -> [str]   §4.4's table, the hosts, the words
    locks_of(entry) -> set                  the t2 family locks the entry's run takes (run.family_locks)
    lock_list(entry) -> [str]               the same, in the order the run takes them
    pools_of(entry) -> set                  the host pools it owns on each host it uses (§4.5)
    lock_matrix(entries, builds=None) -> {(id, id): reason}   pairs that may not run at once
    parse_duration("90m") -> 5400           fmt_hm(5400) -> "1:30"

The grammar (§3.1):

    line      := comment | blank | directive | manual | entry
    directive := "set" KEY VALUE | "include" PATH        (PATH relative to the file, depth <= 2)
    manual    := "manual" TEXT                           (printed, never run)
    entry     := ["serial"] KIND ARG... [OPT...]          KIND: t2 ht1 stic1 bs2 cmd
    OPT       := KEY=VALUE                               id hosts after est retry rerun why

Items (entries and manual lines) are numbered 1, 2, ... in reading order, includes inlined; an
entry's id is NNN-KIND-SLUG (its number, its kind, its arguments slugified, the slug cut at 40),
or its id=.  Settings: the including file's `set` wins over an included file's.
"""

import hashlib
import json
import posixpath
import re
import shlex
import types

KINDS = ("t2", "ht1", "stic1", "bs2", "cmd")
OPT_KEYS = ("id", "hosts", "after", "est", "retry", "rerun", "why")
SETTINGS = {"mode": "parallel", "round": None, "hosts": None, "retry": 0, "lock-wait": 4 * 3600,
            "patience": 2 * 3600, "log-fetch": "non-pass", "prev": None}
MODES = ("parallel", "serial")
LOG_FETCH = ("non-pass", "all", "none")
POOLS = ("stic", "holt", "bs2", "gate")
INCLUDE_DEPTH = 2
SLUG_MAX = 40
FORBIDDEN_HOSTS = ("bigfoot",)
DEFAULT_WALL = {"t2": 3600, "ht1": 4 * 3600, "stic1": 2 * 3600, "bs2": 3 * 3600, "cmd": 300}

_OPT_RE = re.compile(r"([a-z][a-z_-]*)=(.*)\Z", re.S)
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]*\Z")
_DUR_RE = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?\Z")
_ROUND_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
# the Test2 words (words.py's), read here without its name table
_WORD_RE = re.compile(r"(gatetest2|stictest2|holttest2|guesttest2|imgtest2|bkotest2|sweeptest2)(all)?(.*)\Z", re.I)
_FAMILY = {"gatetest2": "gate", "stictest2": "stic", "holttest2": "holt", "guesttest2": "guest", "imgtest2": "img",
           "bkotest2": "bko", "sweeptest2": "sweep"}
FAMILY_POOLS = {"stic": {"stic"}, "holt": {"holt"}, "guest": {"holt"}, "img": {"holt"}, "gate": {"gate"},
                "bko": {"stic", "holt", "gate"}, "sweep": {"stic", "holt", "gate"}}
ROUND_FAMILIES = ("stic", "gate", "bko", "sweep")   # the words that take a ROUND
KIND_POOLS = {"ht1": {"holt"}, "stic1": {"stic"}, "bs2": {"bs2"}}
TTY_WORDS = ("read", "passwd", "su", "login", "vipw", "vigr", "visudo", "select")
ALL_LOCKS = ("bko", "gate", "stic", "holt", "guest", "img")


class BatchError(Exception):
    """A batch file that doesn't parse: every error, each FILE:LINE: what."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class Entry:
    """One entry.  kind: t2 ht1 stic1 bs2 cmd; args: its arguments (for cmd: [NAME]); cmd: the
    command (cmd only); opts: its KEY=VALUE options, as written; pos: its item number."""

    def __init__(self, kind, args, opts=None, serial=False, pos=0, line=0, file="", text="", cmd=None):
        self.kind, self.args, self.opts = kind, list(args), dict(opts or {})
        self.serial, self.pos, self.line, self.file, self.text = bool(serial), pos, line, file, text
        self.cmd = list(cmd) if cmd is not None else None
        self.id = None
        self.after = []
        self.hosts = None          # hosts=: the entry's own hosts, never split
        self.est_s = None
        self.retry = None          # None: the batch's
        self.rerun = None          # cmd's rerun=; None: the kind's default (no)
        self.why = self.opts.get("why")
        self.implied_serial = False  # GuestTest2 update: always serial (§4.4)

    @property
    def is_serial(self):
        return self.serial or self.implied_serial

    @property
    def slug(self):
        return slugify(self.args)

    @property
    def family(self):
        """The t2 family (gate stic holt guest img bko sweep) of a t2 entry, else None."""
        if self.kind != "t2" or not self.args:
            return None
        m = _WORD_RE.match(self.args[0])
        return _FAMILY[m.group(1).lower()] if m else None

    @property
    def update(self):
        return self.family == "guest" and any(a.lower() == "update" for a in self.args[1:])

    def where(self):
        return f"{self.file}:{self.line}"

    def to_json(self):
        return {"id": self.id, "kind": self.kind, "args": list(self.args), "cmd": self.cmd, "opts": dict(self.opts),
                "serial": self.serial, "implied_serial": self.implied_serial, "after": list(self.after),
                "hosts": list(self.hosts) if self.hosts is not None else None, "est_s": self.est_s,
                "retry": self.retry, "rerun": self.rerun, "why": self.why, "pos": self.pos, "line": self.line,
                "file": self.file, "text": self.text}

    @classmethod
    def from_json(cls, d):
        e = cls(d["kind"], d.get("args") or [], d.get("opts"), d.get("serial"), d.get("pos", 0), d.get("line", 0),
                d.get("file", ""), d.get("text", ""), d.get("cmd"))
        e.id = d.get("id")
        e.after = list(d.get("after") or [])
        e.hosts = list(d["hosts"]) if d.get("hosts") is not None else None
        e.est_s, e.retry, e.rerun, e.why = d.get("est_s"), d.get("retry"), d.get("rerun"), d.get("why")
        e.implied_serial = bool(d.get("implied_serial"))
        return e

    def same_as(self, other):
        """True when OTHER is this entry with the same id, kind, arguments and options (--reparse)."""
        a, b = self.to_json(), other.to_json()
        for k in ("pos", "line", "file", "text"):
            a.pop(k, None)
            b.pop(k, None)
        return a == b

    def __repr__(self):
        return f"Entry({self.id!r})"


class Batch:
    """A parsed file.  settings: every key of SETTINGS, resolved; items: entries and manual
    lines in reading order; sources: {path: sha256} of every file read; lines: [[path, n, text]]."""

    def __init__(self, path):
        self.path = path
        self.settings = dict(SETTINGS)
        self.set_at = {}           # key -> FILE:LINE
        self.items = []
        self.sources = {}
        self.lines = []

    @property
    def entries(self):
        return [x for x in self.items if isinstance(x, Entry)]

    @property
    def manual(self):
        return [x for x in self.items if not isinstance(x, Entry)]

    @property
    def name(self):
        b = posixpath.basename(self.path)
        return b[:-len(".batch")] if b.endswith(".batch") else b

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(sorted(self.sources.items()), sort_keys=True).encode()).hexdigest()

    def entry(self, eid):
        for e in self.entries:
            if e.id == eid:
                return e
        return None

    def to_json(self):
        return {"path": self.path, "name": self.name, "settings": dict(self.settings), "set_at": dict(self.set_at),
                "entries": [e.to_json() for e in self.entries],
                "manual": [dict(m) for m in self.manual], "items": [x.id if isinstance(x, Entry) else
                                                                    {"manual": x["text"]} for x in self.items],
                "sources": dict(self.sources), "digest": self.digest, "lines": [list(x) for x in self.lines]}

    @classmethod
    def from_json(cls, d):
        b = cls(d["path"])
        b.settings = dict(SETTINGS, **(d.get("settings") or {}))
        b.set_at = dict(d.get("set_at") or {})
        es = {e["id"]: Entry.from_json(e) for e in d.get("entries") or []}
        ms = list(d.get("manual") or [])
        for it in d.get("items") or []:
            if isinstance(it, dict):
                b.items.append(ms.pop(0) if ms else {"text": it.get("manual"), "pos": 0})
            elif it in es:
                b.items.append(es[it])
        b.sources = dict(d.get("sources") or {})
        b.lines = [list(x) for x in d.get("lines") or []]
        return b


# --- small parsers ----------------------------------------------------------------------------

def parse_duration(s):
    """Seconds of "90m", "2h", "1h30m", "45s", "1d", or a bare number of seconds; ValueError."""
    t = str(s).strip().lower()
    if t.isdigit():
        return int(t)
    m = _DUR_RE.match(t)
    if not t or not m or not any(m.groups()):
        raise ValueError(f"not a duration: {s!r} (90m, 2h, 1h30m, 45s)")
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def fmt_hm(s):
    """5400 -> "1:30" (hours:minutes, rounded to the minute)."""
    if s is None:
        return "-"
    m = int(round(float(s) / 60.0))
    return f"{m // 60}:{m % 60:02d}"


def slugify(args):
    s = "-".join(str(a) for a in args).lower()
    s = re.sub(r"[^a-z0-9+._-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:SLUG_MAX].rstrip("-") or "x"


def host_list(s):
    return list(dict.fromkeys(h.strip() for h in str(s).split(",") if h.strip()))


# --- parse ------------------------------------------------------------------------------------

def parse(text, path, read=None):
    """The Batch of TEXT (the file at PATH).  READ(path) -> text reads an include (None: includes
    are refused).  Raises BatchError with every error found."""
    b = Batch(path)
    errs = []
    seen = []
    items = []
    _parse_into(b, text, path, read, 0, errs, items, seen, set())
    # number the items, make the ids
    for n, it in enumerate(items, 1):
        if isinstance(it, Entry):
            it.pos = n
        else:
            it["pos"] = n
    b.items = items
    by_slug, ids = {}, {}
    for e in b.entries:
        if "id" in e.opts:
            if not _ID_RE.match(e.opts["id"]):
                errs.append(f"{e.where()}: bad id={e.opts['id']!r}: letters, digits, '+', '.', '_', '-'")
            e.id = e.opts["id"]
        else:
            key = (e.kind, e.slug)
            if key in by_slug:
                errs.append(f"{e.where()}: this entry slugifies as {by_slug[key].where()}'s ({e.kind}-{e.slug}): "
                            "give one an id=NAME")
            by_slug.setdefault(key, e)
            e.id = f"{e.pos:03d}-{e.kind}-{e.slug}"
        if e.id in ids:
            errs.append(f"{e.where()}: id {e.id} is {ids[e.id].where()}'s too: ids are unique")
        ids.setdefault(e.id, e)
    # after=: known ids, no cycle
    for e in b.entries:
        if "after" in e.opts:
            e.after = host_list(e.opts["after"])
            for a in e.after:
                if a not in ids:
                    errs.append(f"{e.where()}: after={a}: no entry has that id")
                elif a == e.id:
                    errs.append(f"{e.where()}: after={a}: an entry can't wait for itself")
    cyc = _cycle(b.entries)
    if cyc:
        errs.append(f"{ids[cyc[0]].where()}: after= makes a cycle: {' -> '.join(cyc)}")
    if errs:
        raise BatchError(errs)
    return b


def _cycle(entries):
    """A cycle of after= (as a list of ids, first repeated last), or None."""
    graph = {e.id: [a for a in e.after if a != e.id] for e in entries}
    state = {}

    def visit(n, stack):
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, ()):
            if m not in graph:
                continue
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if not state.get(m):
                r = visit(m, stack)
                if r:
                    return r
        stack.pop()
        state[n] = 2
        return None

    for e in entries:
        if not state.get(e.id):
            r = visit(e.id, [])
            if r:
                return r
    return None


def _parse_into(b, text, path, read, depth, errs, items, seen, chain):
    b.sources[path] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    here_set = set()
    pending_sets = []
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        b.lines.append([path, n, line])
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        where = f"{path}:{n}"
        head = s.split(None, 1)[0]
        if head == "manual":
            t = s[len("manual"):].strip()
            if not t:
                errs.append(f"{where}: manual needs a TEXT")
            items.append({"text": t, "file": path, "line": n, "pos": 0})
            continue
        try:
            toks = shlex.split(s, comments=True, posix=True)
        except ValueError as e:
            errs.append(f"{where}: {e}")
            continue
        if not toks:
            continue
        if toks[0] == "set":
            if len(toks) != 3:
                errs.append(f"{where}: set KEY VALUE (one value; quote it if it has spaces)")
                continue
            k, v = toks[1], toks[2]
            if k in here_set:
                errs.append(f"{where}: set {k} twice in this file")
                continue
            here_set.add(k)
            try:
                pending_sets.append((k, _setting(k, v), where))
            except ValueError as e:
                errs.append(f"{where}: {e}")
            continue
        if toks[0] == "include":
            if len(toks) != 2:
                errs.append(f"{where}: include PATH")
                continue
            if depth + 1 > INCLUDE_DEPTH:
                errs.append(f"{where}: include {toks[1]}: includes nest at most {INCLUDE_DEPTH} deep")
                continue
            if read is None:
                errs.append(f"{where}: include {toks[1]}: no includes here")
                continue
            p = posixpath.normpath(posixpath.join(posixpath.dirname(path), toks[1]))
            if p in chain or p == path:
                errs.append(f"{where}: include {toks[1]}: it includes itself")
                continue
            try:
                t = read(p)
            except (OSError, UnicodeDecodeError) as e:
                errs.append(f"{where}: include {toks[1]}: {getattr(e, 'strerror', None) or e}")
                continue
            sub = Batch(p)
            _parse_into(sub, t, p, read, depth + 1, errs, items, seen, chain | {path})
            b.sources.update(sub.sources)
            b.lines.extend(sub.lines)
            for k, v in sub.settings.items():
                if k in sub.set_at and k not in b.set_at:
                    b.settings[k], b.set_at[k] = v, sub.set_at[k]
            continue
        e = _entry(toks, path, n, s, errs)
        if e is not None:
            items.append(e)
    for k, v, where in pending_sets:   # this file's own: over any include's
        b.settings[k], b.set_at[k] = v, where


def _setting(k, v):
    if k not in SETTINGS:
        raise ValueError(f"no setting {k!r} (settings: {', '.join(SETTINGS)})")
    if k == "mode":
        if v not in MODES:
            raise ValueError(f"set mode: parallel or serial, not {v!r}")
        return v
    if k == "round":
        if not _ROUND_RE.match(v):
            raise ValueError(f"set round: bad ROUND {v!r}")
        return v
    if k == "hosts":
        hs = host_list(v)
        if not hs:
            raise ValueError("set hosts names no host")
        return hs
    if k == "retry":
        if not v.isdigit():
            raise ValueError(f"set retry: a number, 0 or more, not {v!r}")
        return int(v)
    if k in ("lock-wait", "patience"):
        return parse_duration(v)
    if k == "log-fetch":
        if v not in LOG_FETCH:
            raise ValueError(f"set log-fetch: {', '.join(LOG_FETCH)}, not {v!r}")
        return v
    return v


def _entry(toks, path, n, text, errs):
    where = f"{path}:{n}"
    serial = False
    if toks[0] == "serial":
        serial = True
        toks = toks[1:]
        if not toks:
            errs.append(f"{where}: serial needs an entry")
            return None
    kind = toks[0]
    if kind not in KINDS:
        errs.append(f"{where}: unknown kind {kind!r} (kinds: {', '.join(KINDS)}; directives: set, include, manual)")
        return None
    rest = toks[1:]
    cmd = None
    if kind == "cmd":
        if "--" not in rest:
            errs.append(f"{where}: cmd NAME -- COMMAND...")
            return None
        i = rest.index("--")
        rest, cmd = rest[:i], rest[i + 1:]
        if not cmd:
            errs.append(f"{where}: cmd {' '.join(rest)} --: no COMMAND")
            return None
    args, opts = [], {}
    for t in rest:
        m = _OPT_RE.match(t)
        if m and not t.startswith("-"):
            k, v = m.group(1), m.group(2)
            if k not in OPT_KEYS:
                errs.append(f"{where}: unknown option {k}= (options: {', '.join(OPT_KEYS)})")
                continue
            if k in opts:
                errs.append(f"{where}: {k}= twice")
                continue
            opts[k] = v
        else:
            if opts:
                errs.append(f"{where}: {t!r} after the options: options come last")
            args.append(t)
    if kind == "cmd" and len(args) != 1:
        errs.append(f"{where}: cmd takes one NAME before --, not {len(args)}")
        return None
    if not args:
        errs.append(f"{where}: {kind} needs its arguments")
        return None
    e = Entry(kind, args, opts, serial, 0, n, path, text, cmd)
    if "hosts" in opts:
        e.hosts = host_list(opts["hosts"])
        if not e.hosts:
            errs.append(f"{where}: hosts= names no host")
    if "est" in opts:
        try:
            e.est_s = parse_duration(opts["est"])
        except ValueError as ex:
            errs.append(f"{where}: est=: {ex}")
    if "retry" in opts:
        if opts["retry"].isdigit():
            e.retry = int(opts["retry"])
        else:
            errs.append(f"{where}: retry=: a number, 0 or more")
    if "rerun" in opts:
        if opts["rerun"] in ("yes", "no"):
            e.rerun = opts["rerun"] == "yes"
        else:
            errs.append(f"{where}: rerun=: yes or no")
        if kind != "cmd":
            errs.append(f"{where}: rerun= goes with cmd entries (the other kinds resume)")
    e.implied_serial = e.update
    return e


# --- what an entry holds ----------------------------------------------------------------------

def lock_list(entry):
    """The t2 family locks ENTRY's run takes, in order (run.family_locks), [] for the other kinds."""
    fam = entry.family
    if fam is None:
        return []
    from . import run   # (import-time pure; the lock matrix is the run's own)
    return list(run.family_locks(types.SimpleNamespace(family=fam, update=entry.update)))


def locks_of(entry):
    return set(lock_list(entry))


def pools_of(entry):
    """The pools ENTRY owns on each host it uses (§4.5): a serial entry and a cmd with hosts= every
    pool; a cmd without hosts= none."""
    if entry.is_serial and (entry.kind != "cmd" or entry.hosts):
        return set(POOLS)
    if entry.kind == "cmd":
        return set(POOLS) if entry.hosts else set()
    if entry.kind == "t2":
        if entry.update:
            return set(POOLS)
        return set(FAMILY_POOLS.get(entry.family) or ())
    return set(KIND_POOLS.get(entry.kind) or ())


def lock_matrix(entries, builds=None):
    """{(a, b): reason} for every ordered pair of entries that may not run at once: shared family
    locks (img -> img, holt; GuestTest2 update -> every lock), and two t2 entries whose plans
    would build the same round (BUILDS: {id: round}, from the profiles; None: not known yet)."""
    out = {}
    es = list(entries)
    for i, a in enumerate(es):
        la = locks_of(a)
        for b in es[i + 1:]:
            why = []
            both = sorted(la & locks_of(b))
            if both:
                why.append("lock" + ("s " if len(both) > 1 else " ") + ",".join(both))
            if builds and builds.get(a.id) and builds.get(a.id) == builds.get(b.id):
                why.append(f"both build round {builds[a.id]}")
            if why:
                out[(a.id, b.id)] = out[(b.id, a.id)] = "; ".join(why)
    return out


# --- refusals (§4.4, §6.1 item 7) ------------------------------------------------------------

def refusals(batch, table, known=None, allow_update=False, hosts=None):
    """[str]: every reason BATCH can't run.  TABLE: the host table (hosts.load()); KNOWN: the
    names words.parse checks against (plan.known_names), None to check the words' shape only;
    HOSTS: the batch's hosts (--hosts, else set hosts, else the table's default hosts)."""
    from . import hosts as H
    from . import words
    out = []
    every = {r["name"]: r for r in H.rows(True, table)}
    bhosts = list(hosts) if hosts else (batch.settings.get("hosts") or list(H.defaults(table)))

    def host_problems(hs, what):
        bad = []
        for h in hs:
            r = every.get(h)
            if h in FORBIDDEN_HOSTS:
                bad.append(f"{what} names {h}: Bigfoot is the user's machine, never a batch's")
            elif r is None:
                bad.append(f"{what} names {h}: no such host (known: {', '.join(H.known(table))})")
            elif not r.get("enabled"):
                bad.append(f"{what} names {h}: a reserved row (enabled false)")
            elif H.row_arch(r) != H.DEFAULT_ARCH:
                bad.append(f"{what} names {h}: an {H.row_arch(r)} host (the batch's hosts are amd64)")
        return bad

    out += host_problems(bhosts, "hosts")
    ht1_runs = {}
    for e in batch.entries:
        w = f"{e.id} ({e.where()})"
        low = [a.lower() for a in (e.args + (e.cmd or []))]
        if any("backshot1" in a for a in low):
            out.append(f"{w}: BackShot1 is real hardware, a person at the ThinkPad: a manual line")
        if e.hosts is not None:
            out += [f"{w}: {x}" for x in host_problems(e.hosts, "hosts=")]
            stray = [h for h in e.hosts if h not in bhosts and h in every and h not in FORBIDDEN_HOSTS]
            if stray:
                out.append(f"{w}: hosts= names {', '.join(stray)}, not among the batch's hosts ({' '.join(bhosts)})")
        if e.kind == "t2":
            out += [f"{w}: {x}" for x in _t2_refusals(e, words, known, allow_update)]
        elif e.kind == "bs2":
            for i, a in enumerate(low):
                if a in ("--tier=3", "tier3") or (a == "--tier" and i + 1 < len(low) and low[i + 1] == "3"):
                    out.append(f"{w}: bs2 tier 3 is the ThinkPad's cases (docs/design/bs2-all.md): a manual line")
        elif e.kind == "ht1":
            run = e.args[0]
            if run in ht1_runs:
                out.append(f"{w}: ht1 run {run} is {ht1_runs[run]}'s too: one queue would serve both (rename one)")
            ht1_runs.setdefault(run, e.id)
        elif e.kind == "cmd":
            out += [f"{w}: {x}" for x in _cmd_refusals(e.cmd)]
        if "--universe" in low or any(a.startswith("--universe=") for a in low):
            out.append(f"{w}: --universe is test-only, refused in a batch")
    return out


def _t2_refusals(e, words, known, allow_update):
    out = []
    a0 = e.args[0]
    low = [a.lower() for a in e.args]
    if a0 == "guest-lines":
        if any(x.startswith("--accept") for x in low):
            return ["t2 guest-lines --accept: classing log lines is the person's judgement (by hand, after)"]
        return ["t2 guest-lines is a person's tool, not a batch entry"]
    if not words.is_word(a0):
        return [f"t2 {a0}: not a Test2 word (a t2 entry is a word: GateTest2, SticTest2, HoltTest2, GuestTest2, "
                "ImgTest2, BkoTest2, SweepTest2 and their forms)"]
    if "plan" in low[1:]:
        out.append("'plan' in an entry: `t2 batch FILE plan` plans the whole batch")
    for x in e.args[1:]:
        if x == "--hosts" or x.startswith("--hosts="):
            out.append("--hosts in an entry: hosts=H,H is the entry's claim (and the batch divides the rest)")
        elif x.startswith("--") and not (x == "--holt-bin" or x.startswith("--holt-bin=")) \
                and not x.startswith("--universe"):
            out.append(f"{x}: no such t2 option in a batch entry")
    if e.update and not allow_update:
        out.append("t2 GuestTest2 update holds vm-update's fleet lock through a 120 s heartbeat a Mac sleep loses: "
                   "refused unless `t2 batch FILE --allow-update` (lid open)")
    if known is not None:
        argv = [x for x in e.args if not x.startswith("--")]
        try:
            words.parse(argv, known)
        except words.Refused as ex:
            out.append(f"t2 {' '.join(e.args)}: {ex}")
    return out


def _cmd_refusals(cmd):
    out = []
    toks = list(cmd or [])
    flat = " ".join(toks)
    words_ = re.split(r"[\s;&|()`]+", flat)
    if "/dev/tty" in flat:
        out.append("the command reads /dev/tty: it would prompt (stdin is /dev/null in a batch)")
    for i, w in enumerate(words_):
        if w == "sudo":
            nxt = words_[i + 1] if i + 1 < len(words_) else ""
            if not (nxt.startswith("-") and "n" in nxt.lstrip("-") and not nxt.startswith("--")) and nxt != "--non-interactive":
                out.append("the command has sudo without -n: it would prompt")
                break
    if any(w in TTY_WORDS for w in words_):
        out.append(f"the command runs {', '.join(sorted({w for w in words_ if w in TTY_WORDS}))}: it reads a tty")
    for i, w in enumerate(words_):
        if w == "ssh" and any(x in ("-t", "-tt") for x in words_[i + 1:i + 6]):
            out.append("the command runs ssh -t: it wants a tty")
            break
    return out


def effective_args(entry, settings, known=None):
    """ENTRY's t2 arguments with the batch's `set round` added when its word takes a round and
    names none (KNOWN: words.parse's names; without them only BkoTest2/SweepTest2 can be read)."""
    if entry.kind != "t2" or not settings.get("round") or entry.family not in ROUND_FAMILIES:
        return list(entry.args)
    argv = [x for x in entry.args if not x.startswith("--")]
    rnd = None
    if known is not None:
        from . import words
        try:
            rnd = words.parse(argv, known).round
        except words.Refused:
            return list(entry.args)
    elif entry.family in ("bko", "sweep"):
        rnd = next((x for x in argv[1:] if x.lower() not in ("plan", "stale")), None)
    else:
        return list(entry.args)
    if rnd:
        return list(entry.args)
    return [entry.args[0], settings["round"]] + list(entry.args[1:])


def diff_lines(old, new):
    """The first place two batches' LINES ([[path, n, text]]) differ, as a sentence, or None."""
    a = [tuple(x) for x in old or []]
    b = [tuple(x) for x in new or []]
    for x, y in zip(a, b):
        if x != y:
            return f"{y[0]}:{y[1]} was {x[2]!r}, is {y[2]!r}" if x[:2] == y[:2] else \
                f"{x[0]}:{x[1]} ({x[2]!r}) is now {y[0]}:{y[1]} ({y[2]!r})"
    if len(a) != len(b):
        if len(a) > len(b):
            x = a[len(b)]
            return f"{x[0]}:{x[1]} ({x[2]!r}) is gone"
        y = b[len(a)]
        return f"{y[0]}:{y[1]} ({y[2]!r}) is new"
    return None
