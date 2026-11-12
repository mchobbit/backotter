"""The Test2 planner: which cells a word runs, and why (contract 1.6-1.8; rulings R4-R6,
R14, R15, R17, R21, R27).

    make(word, ctx) -> Plan     pure given ctx: it reads the checkout, BKO_GIT and affected.sh,
                                and writes nothing anywhere

Each cell of the word's universe gets a status: due, current, canary-verified,
awaiting-canaries, soft-stale, carried, class-pending, to-class or not-runnable, with the selecting
rule (1.6.1's vocabulary) and its reasons.  Family scope is decided here and nowhere else;
the runners only execute plans.
"""

import importlib
import importlib.util
import json
import os
import re
import time

from . import DEFAULT_HOSTS, HARD, canonical, parse_utc, sha256_hex, utc_now
from . import bko_git as default_bko_git
from . import carry, copies, estimates, keys, words

STATUSES = ("due", "current", "canary-verified", "awaiting-canaries", "soft-stale", "carried",
            "class-pending", "to-class", "not-runnable")
DECISIVE_KINDS = ("run", "carried", "class")
DECISIVE_VERDICTS = ("PASS", "FAIL", "FLAKY-FAIL")
WATCH_ROOTS = ("testvm", "stick", "holt")
GATE_FALLBACK = ("holt", "fork:restic", "fork:rustic_core", "fork:rustic", "fork-root:restic", "bin:abi",
                 "bin:static", "bin:version", "loop:linux@chopin", "loop:linux@cinqcent", "loop:sparse@chopin",
                 "loop:sparse@cinqcent", "selftest")
_HEX40 = re.compile(r"\b[0-9a-f]{40}\b")


class PlanError(Exception):
    """The plan can't be made (a bad round, no spec): the CLI exits 2."""


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_drive(checkout):
    """testvm/stictest1/drive.py of the checkout, as a module (P4 keeps all_guests,
    load_specs, simulate and readme_estimates)."""
    path = os.path.join(checkout, "testvm", "stictest1", "drive.py")
    spec = importlib.util.spec_from_file_location("t2_drive", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def optional_module(name):
    try:
        return importlib.import_module("t2lib." + name)
    except ImportError:
        return None


def short(v, n=4):
    """A value as the plan prints it: sha256:4be1... -> 4be1.., gen:..., ver:6.12.115 -> 6.12.115."""
    if v is None:
        return "-"
    if not isinstance(v, str):
        return str(v)
    for p in ("sha256:", "gen:", "img:", "dir:", "entry:", "code:", "git:", "fp:"):
        if v.startswith(p):
            return v[len(p):len(p) + n] + ".."
    if v.startswith("ver:"):
        t = v[4:]
        return t[:n] + ".." if re.fullmatch(r"[0-9a-f]{40,64}", t) else t
    if v.startswith("pending:"):
        return "pending"
    return v


def hhmm(t):
    if isinstance(t, (int, float)):
        return time.strftime("%H:%MZ", time.gmtime(t))
    e = parse_utc(t) if isinstance(t, str) else None
    return time.strftime("%H:%MZ", time.gmtime(e)) if e else "?"


# --- context ----------------------------------------------------------------------------

class Ctx:
    """What a plan is made from (contract 1.7).

    checkout       the Mac checkout (the future copy's tree)
    head           its HEAD commit (40 hex)
    hosts          the hosts in use (--hosts)
    facts          {host: facts_host.py output}; a host missing is unreachable
    ledger         the merged rows, ordered by (t, id)
    copy_preview   {"id", "dirty", "commit"} of the copy a run would make
    options        universe [cells] (test only), classes (dict), bko_git, now (epoch), pulled
                   {host: {t, rows, stale}}, running {cell: run}, inputs_fn(cell, host, opts) for
                   tests, heads {repo: commit}, drive (module), plandiff (module), last_sweep (sweep.json's),
                   extra_due (cells or {cell: why}, rule canary:carried), identity_bad {guest: why}
    """

    def __init__(self, checkout, head=None, hosts=DEFAULT_HOSTS, facts=None, ledger=None, copy_preview=None, options=None):
        self.checkout = os.path.abspath(checkout)
        self.options = dict(options or {})
        self.git = carry.Git(self.checkout)
        self.head = head or self.git.rev("HEAD")
        self.hosts = list(hosts)
        self.facts = dict(facts or {})
        self.ledger = list(ledger or [])
        self.copy_preview = copy_preview or {"id": None, "dirty": {}, "commit": self.head}
        self._classes = self.options.get("classes")
        self._src = self._specs = self._drive = self._head_src = None

    @property
    def classes(self):
        if self._classes is None:
            self._classes = load_json(os.path.join(self.checkout, "testvm", "test2", "classes.json"))
        return self._classes

    @property
    def src(self):
        if self._src is None:
            self._src = self.options.get("src") or keys.TreeSource(self.checkout)
        return self._src

    @property
    def specs(self):
        if self._specs is None:
            self._specs = self.options.get("specs") or keys.Specs(self.src)
        return self._specs

    @property
    def drive(self):
        if self._drive is None:
            self._drive = self.options.get("drive") or load_drive(self.checkout)
        return self._drive

    @property
    def bko_git(self):
        return self.options.get("bko_git") or default_bko_git()

    @property
    def now(self):
        return self.options.get("now") or time.time()

    def head_sha256(self, path):
        """sha256 of PATH at HEAD, or None when it isn't there (the committed rule)."""
        if self._head_src is None:
            self._head_src = self.options.get("head_src") or keys.GitSource(self.checkout, self.head)
        return self._head_src.sha256(path)


# --- the plan ------------------------------------------------------------------------------

class PlanCell:
    def __init__(self, cell, family):
        self.cell, self.family = cell, family
        self.status, self.rule, self.reasons = None, None, []
        self.host = "any"
        self.keys = {}                 # {host: K}
        self.last_pass = None          # {id, t, key, host}
        self.est = {}
        self.rep = None                # class-pending: the representative cell
        self.klass = None
        self.running = None
        self.via = None                # carried: the PASS row carried from
        self.differ = []               # inputs that differ between hosts
        self.res = {}                  # {host: keys.Resolved} (not serialized)
        self.inp = {}                  # {host: inputs}: res's, or predicted for binaries not built (not serialized)
        self.change = None             # changed cells: {(group, name, new)} on the judged host (not serialized)
        self.part = None               # gate: fast or slow
        self.placed = None             # the host the estimate placed it on
        self.refused = False           # not-runnable and the word's exit says so: an image a binary keeps from
                                       # running, a named image that can't run, a bare update's stale --try
        self.binaries = None           # img: {NAME: {state, digest, why, canary}} (real, carried, carried-other, none)
        self.fp_carry = False          # its guest change is only the fingerprint's transition (not serialized)

    @property
    def key(self):
        vals = set(self.keys.values())
        return vals.pop() if len(vals) == 1 else None

    def to_json(self):
        out = dict(cell=self.cell, status=self.status, rule=self.rule, reasons=list(self.reasons), host=self.host,
                   key=self.key, keys=dict(self.keys), last_pass=self.last_pass, est=dict(self.est), rep=self.rep,
                   klass=self.klass, running=self.running, via=self.via, differ=list(self.differ), part=self.part,
                   placed=self.placed)
        if self.refused:
            out["refused"] = True
        if self.binaries:
            out["binaries"] = self.binaries
        return out


class Plan:
    def __init__(self, word, family):
        self.word, self.family = word, family
        self.cells = []
        self.totals = {"due": 0, "gm": 0.0, "wall_s": 0, "gb": {}}
        self.notes = []
        self.header = {}
        self.classes = []       # [{class, rep, members}]
        self.unmapped = []
        self.set = None         # the binaries' set: {name, dir, pending, round}
        self.options = {}       # what resume needs back: extra_due, identity_bad, gate_fresh
        self.made = utc_now()

    def by_status(self, status):
        return [c for c in self.cells if c.status == status]

    def cell(self, name):
        for c in self.cells:
            if c.cell == name:
                return c
        return None

    def due(self):
        return self.by_status("due")

    def to_json(self):
        out = dict(v=1, word=self.word.to_json(), family=self.family, made=self.made, header=self.header,
                   set=self.set, totals=self.totals, notes=list(self.notes), classes=self.classes,
                   unmapped=self.unmapped, cells=[c.to_json() for c in self.cells])
        if self.options:
            out["options"] = self.options
        return out

    def render(self):
        from . import report
        return report.render_plan(self)


# --- helpers over rows ---------------------------------------------------------------------

def pass_like(r):
    return r.get("verdict") == "PASS"


def row_t(r):
    return r.get("t") or ""


def needs_set(r):
    return frozenset((r.get("needs") or {}).keys())


def bin_commits(binmeta, name):
    """{repo: 40-hex} for binary NAME (OS/restic-bko, OS/rustic-bko) from a row's meta.bin or a
    facts set: {"commits": {"OS/restic-bko": HEX, "OS/rustic-bko": {"commit", "rustic_core"}}}."""
    if not isinstance(binmeta, dict):
        return {}
    c = (binmeta.get("commits") or {}).get(name)
    if c is None and isinstance(binmeta.get(name), dict):
        c = binmeta[name].get("commit") if "rustic_core" not in binmeta[name] else binmeta[name]
    out = {}
    if name.endswith("restic-bko"):
        h = _HEX40.search(c if isinstance(c, str) else json.dumps(c or ""))
        if h:
            out["restic"] = h.group(0)
    else:
        if isinstance(c, dict):
            h, k = _HEX40.search(str(c.get("commit") or "")), _HEX40.search(str(c.get("rustic_core") or ""))
        else:
            hs = _HEX40.findall(c or "") if isinstance(c, str) else []
            h = k = None
            if hs:
                out["rustic"] = hs[0]
            if len(hs) > 1:
                out["rustic_core"] = hs[1]
            return out
        if h:
            out["rustic"] = h.group(0)
        if k:
            out["rustic_core"] = k.group(0)
    return out


def digest_text(binmeta, name):
    """The .digest text (less built/reused lines) of NAME, from meta.bin or a facts set, or None."""
    if not isinstance(binmeta, dict):
        return None
    for k in ("digest_text", "digest_texts", "digests"):
        d = binmeta.get(k)
        if isinstance(d, dict) and isinstance(d.get(name), str) and "\n" in d[name]:
            return d[name]
    return None


def round_commits(entry):
    """A facts `rounds` entry -> {repo: 40-hex} of its linux commit files."""
    out = {}
    if isinstance(entry, str):
        hs = _HEX40.findall(entry)
        return dict(zip(("restic", "rustic", "rustic_core"), hs))
    if not isinstance(entry, dict):
        return out
    for k, v in entry.items():
        kl = k.lower()
        if isinstance(v, dict):
            sub = round_commits(v)
            if "rustic" in kl and "core" not in kl:
                c = _HEX40.search(str(v.get("commit") or ""))
                if c:
                    out["rustic"] = c.group(0)
                c = _HEX40.search(str(v.get("rustic_core") or ""))
                if c:
                    out["rustic_core"] = c.group(0)
            else:
                for r, h in sub.items():
                    out.setdefault(r, h)
            continue
        hs = _HEX40.findall(str(v))
        if not hs:
            continue
        if "core" in kl:
            out["rustic_core"] = hs[0]
        elif "rustic" in kl:
            out["rustic"] = hs[0]
            if len(hs) > 1:
                out.setdefault("rustic_core", hs[1])
        elif "restic" in kl:
            out["restic"] = hs[0]
    return out


def globmatch(path, patterns):
    return any(keys.glob_re(p).match(path) for p in patterns)


# --- the planner ---------------------------------------------------------------------------

class View:
    """One cell while it is being planned."""

    def __init__(self, cell, family):
        self.pc = PlanCell(cell, family)
        self.cell, self.family = cell, family
        c = keys.Cell(cell)
        self.name, self.variant, self.chost = c.name, c.variant, c.host
        self.hosts = []            # hosts that may run it
        self.why_not = None        # no host can: why
        self.res = {}              # {host: Resolved}
        self.inp = {}              # {host: inputs} (res's, or predicted for binaries not built)
        self.grp = {}              # {host: groups}
        self.rows = []             # valid rows, newest first
        self.unc = []              # PASS rows valid but for the committed rule, newest first
        self.allrows = []          # every row of the cell, newest first
        self.P = None
        self.G, self.D = set(), []
        self.change_set = None
        self.rescuer_names = []
        self.plan_stages = None


class Planner:
    def __init__(self, word, ctx):
        self.w, self.ctx = word, ctx
        self.fam = word.family
        self.cls = ctx.classes
        self.policy = self.cls.get("policy", {})
        self.plan = Plan(word, self.fam)
        self.plan.options = {k: ctx.options[k] for k in ("extra_due", "identity_bad", "gate_fresh") if ctx.options.get(k)}
        self.notes = self.plan.notes
        self.carry = ctx.options.get("carry") or carry.ArchiverCarry(ctx.checkout, ctx.bko_git)
        self.plandiff = ctx.options.get("plandiff", optional_module("plandiff"))
        by = {}
        for r in ctx.ledger:
            if isinstance(r, dict) and r.get("cell"):
                by.setdefault(r["cell"], []).append(r)
        self.rows_by_cell = by
        self.rows_by_id = {r.get("id"): r for rs in by.values() for r in rs}
        self._valid = {}
        self._views = {}
        self._named = None
        self._unmapped = {}
        self._golden = {}
        self._changed = {}
        self._wt = {}
        self._gate_holt = None
        self._binrows = None
        self._holt_codes = {}
        self._estimator = None
        self.heads = ctx.options.get("heads")
        self.opts = {}
        self.set_name = None
        self.pending = None

    # --- git, cached per plan ---

    def changed_paths(self, old_commit, old_dirty, roots):
        """carry.changed_since, once per (commit, dirty map, roots) in a plan."""
        k = (old_commit, canonical(old_dirty or {}), tuple(roots))
        if k not in self._changed:
            self._changed[k] = carry.changed_since(self.ctx.git, self.ctx.head, old_commit, old_dirty or {}, roots,
                                                   wt=self.wt_names(roots))
        return self._changed[k]

    def wt_names(self, roots):
        """HEAD against the working tree (untracked files too) under ROOTS, once per plan."""
        k = tuple(roots)
        if k not in self._wt:
            self._wt[k] = self.ctx.git.names(self.ctx.head, None, roots)
        return self._wt[k]

    # --- rows ---

    def committed(self, r):
        """The committed rule (contract 1.5.6), P1's copies.committed_ok."""
        if r.get("committed") is True:
            return True
        if not isinstance(r.get("dirty"), dict):
            return False
        return copies.committed_ok(r, self.ctx.head_sha256)

    def valid(self, r):
        k = r.get("id"), r.get("c")
        if k not in self._valid:
            self._valid[k] = (r.get("valid") is True, r.get("valid") is True and self.committed(r))
        return self._valid[k]

    # --- the binaries' set ---

    def gitrepos(self):
        """keys.resolve's gitrepos: the forks' backotter HEADs in ctx's BKO_GIT (fork_heads), so
        that gate's git: inputs and carry read the same repositories (never the env's BKO_GIT)."""
        heads = self.fork_heads()
        return {r: heads.get(r) for r in carry.FORKS}

    def fork_heads(self):
        if self.heads is None:
            self.heads = {}
            for repo in carry.FORKS:
                h = carry.Git(os.path.join(self.ctx.bko_git, repo)).rev("backotter") \
                    if os.path.isdir(os.path.join(self.ctx.bko_git, repo)) else None
                if h:
                    self.heads[repo] = h
        return self.heads

    def setup_set(self):
        """The set a stic or gate word tests: ROUND's, the round at the fork heads, else (stic)
        one to build (pending) or (gate) bko-bin.  Guest and img cells name no set."""
        if self.fam in ("guest", "img"):
            self.opts = {}
            return
        if self.fam == "holt":
            self.opts = {"rustic_set": "bko-bin"}
            code = keys.holt_code(self.ctx.src)
            hb = [h for h in self.ctx.hosts if code in ((self.ctx.facts.get(h) or {}).get("holt_bins") or {})]
            self.plan.set = {"name": "bko-bin", "holt_code": code, "holt_built": hb}
            ready = self.ctx.options.get("holt_bin")
            if ready:   # --holt-bin DIR: that set's binaries as they are (R14: rows count only where equal)
                sets = {h: (((self.ctx.facts.get(h) or {}).get("holt_bins") or {}).get(ready) or {}).get("sums")
                        for h in self.ctx.hosts if h in self.ctx.facts}
                missing = sorted(h for h, v in sets.items() if not v)
                if missing:
                    raise PlanError(f"--holt-bin {ready}: no SHA256SUMS there on {', '.join(missing)}")
                sums = next(iter(sets.values()))
                differ = sorted(h for h, v in sets.items() if v != sums)
                if differ:
                    raise PlanError(f"--holt-bin {ready} differs between hosts ({', '.join(sorted(sets))})")
                self.opts["holt_sha256"] = dict(sums)
                self.plan.set.update(holt_bin=ready, holt_built=sorted(sets))
                self.notes.append(f"--holt-bin {ready}: bko-holt from that set (sha256 keys; rows count only where equal)")
            return
        reach = [h for h in self.ctx.hosts if h in self.ctx.facts]
        if self.w.round:
            name = "round" + self.w.round
            # (facts_host.py names every set asked for: a set it couldn't find is null there)
            missing = [h for h in reach if not ((self.ctx.facts[h].get("sets") or {}).get(name))]
            self.set_name = name
            self.plan.set = {"name": name, "round": self.w.round, "dir": f"~/backotter-work/{name}/bin",
                             "pending": None, "missing_on": missing}
            if missing:
                self.notes.append(f"no {name} on {', '.join(missing)} (~/backotter-work/{name}/bin)")
        else:
            heads = self.fork_heads()
            found = None
            if reach and len(heads) == 3:
                per = []
                for h in reach:
                    rs = (self.ctx.facts[h].get("rounds") or {})
                    items = rs.items() if isinstance(rs, dict) else ((x.get("name"), x) for x in rs if isinstance(x, dict))
                    per.append({n for n, e in items if n and round_commits(e) == heads})
                both = set.intersection(*per) if per else set()
                if both:
                    found = sorted(both)[-1]
            if found:
                nm = found if found.startswith("round") else "round" + found
                self.set_name = nm
                self.plan.set = {"name": nm, "round": nm[5:], "dir": f"~/backotter-work/{nm}/bin", "pending": None,
                                 "at_heads": True}
            elif self.fam == "gate":
                self.set_name = "bko-bin"
                self.plan.set = {"name": "bko-bin", "dir": "~/backotter-work/bko-bin", "pending": None,
                                 "why": "no round at the fork heads on every host"}
            else:
                if len(heads) < 3:
                    raise PlanError(f"no backotter HEADs in {self.ctx.bko_git} (restic, rustic_core, rustic) "
                                    "to name the default round: give ROUND")
                rid = f"t2r-{heads['restic'][:7]}-{heads['rustic_core'][:7]}-{heads['rustic'][:7]}"
                self.pending = f"restic={heads['restic']},rustic_core={heads['rustic_core']},rustic={heads['rustic']}"
                self.set_name = "round" + rid
                self.plan.set = {"name": self.set_name, "round": rid, "dir": f"~/backotter-work/round{rid}/bin",
                                 "pending": self.pending, "heads": heads}
                self.notes.append(f"the fork heads' round isn't built: round{rid} would be built "
                                  f"(stick/build-round.sh, release profiles)")
        self.opts = {"set": self.set_name}
        if self.pending:
            self.opts["pending"] = self.pending

    # --- the universe ---

    def gate_entries(self):
        sf = self.ctx.specs.get("gate")
        out = []
        if sf is None:
            return [(n, {}) for n in GATE_FALLBACK], False
        for n in sf.names():
            try:
                e = sf.entry(n)
            except KeyError:
                e = {}
            out.append((n, e))
        return out, True

    @staticmethod
    def gate_per_host(name, e):
        return bool(e.get("per_host") or e.get("hosts") == "each" or e.get("where") in ("each", "each host")
                    or (not e and name.startswith("loop:")) or (name.startswith("loop:") and "pin" not in e))

    def universe(self):
        u = self.ctx.options.get("universe")
        if self.fam == "stic":
            cells = [f"stic:{g}" for g in self.ctx.drive.all_guests()]
        elif self.fam == "holt":
            h = self.cls["holt"]
            src = [g for f in h["families"].values() for g in f] + list(h.get("no_family", []))
            cells = [f"holt:{g}" for g in sorted(set(src))]
            for v, gs in h["variants"].items():
                cells += [f"holt:{g}+{v}" for g in gs]
            if self.img_asked(u):
                cells += self.img_cells()
        elif self.fam == "guest":
            cells = self.guest_universe()
        elif self.fam == "img":
            cells = [f"img:{p}" for p in words.PLATS]
        else:
            cells = []
            ents, have = self.gate_entries()
            if not have:
                self.notes.append("testvm/test2/gate/layers.json isn't there: GateTest2's layers can't be keyed")
            for n, e in ents:
                if "@" in n:
                    cells.append(f"gate:{n}")
                elif self.gate_per_host(n, e):
                    cells += [f"gate:{n}@{h}" for h in DEFAULT_HOSTS]
                else:
                    cells.append(f"gate:{n}")
        if u:
            want = set(u)
            unknown = sorted(want - set(cells))
            if unknown:
                raise PlanError(f"--universe names no cell of {words.FAMILY_WORD[self.fam]}: {', '.join(unknown)}")
            cells = [c for c in cells if c in want]
        return cells

    def img_panel(self):
        return self.cls.get("holt", {}).get("img_panel") or {}

    def img_cells(self):
        """Every holt:GUEST+img cell of holt.img_panel."""
        return [f"holt:{g}+img" for p in self.img_panel().values() for g in p.get("guests", ())]

    def img_asked(self, universe):
        """+img cells are in a word's universe only when it names them, or ImgTest2 asks."""
        return "img" in self.w.variants or any(n.endswith("+img") for n in self.w.names) \
            or any(c.endswith("+img") for c in universe or ())

    def panel_of(self, guest):
        """(PLAT, its img_panel entry) of GUEST, or (None, {})."""
        for p, e in self.img_panel().items():
            if guest in e.get("guests", ()):
                return p, e
        return None, {}

    def guest_spec(self):
        sf = self.ctx.specs.get("guest")
        if sf is None:
            raise PlanError(f"{keys.SPEC_PATHS['guest']} isn't there: GuestTest2 isn't built")
        return sf

    def guest_entry(self, name):
        sf = self.ctx.specs.get("guest")
        try:
            return sf.entry(name) if sf is not None else {}
        except KeyError:
            return {}

    def guest_universe(self):
        sf = self.guest_spec()
        excl = sf.data.get("excluded") or {}
        names = [n for n in sf.names() if n not in excl]
        have = {g for f in self.ctx.facts.values() for g in (f.get("guests") or {})}
        missing = sorted(have - set(names) - set(excl))
        if missing:
            self.notes.append("not in any spec: " + ", ".join(missing))
        if self.w.update and self.w.form == "bare":
            left = [n for n in names if self.guest_entry(n).get("update", True) is not True]
            if left:
                self.notes.append("not in a bare update round: " + ", ".join(left))
            names = [n for n in names if n not in left]
        return [f"guest:{n}" for n in names]

    # --- per cell ---

    def view(self, cell):
        if cell in self._views:
            return self._views[cell]
        v = View(cell, self.fam if cell.startswith(self.fam + ":") else cell.split(":", 1)[0])
        self._views[cell] = v
        allrows = sorted(self.rows_by_cell.get(cell, ()), key=lambda r: (row_t(r), r.get("id", "")), reverse=True)
        v.allrows = allrows
        cleared = set()     # keys with an uncommitted PASS newer than the rows still to come
        for r in allrows:
            if v.family == "guest" and (r.get("extra") or {}).get("merged"):
                continue    # a merged update's row is no PASS: the merged image hasn't booted yet
            ok_flag, ok = self.valid(r)
            if ok:
                v.rows.append(r)
            elif ok_flag and pass_like(r):
                v.unc.append(r)
                cleared.add(r.get("key"))
            elif ok_flag and r.get("verdict") in ("FAIL", "FLAKY-FAIL") and r.get("key") not in cleared:
                v.rows.append(r)    # the committed rule says what counts as a PASS; a FAIL at K is a FAIL
        v.P = next((r for r in v.rows if pass_like(r)), None)
        self.place(v)
        v.pc.inp = v.inp
        for h in v.hosts:
            res = self.resolve(v, h)
            if res is not None:
                v.res[h] = res
                v.inp[h], v.grp[h] = res.inputs, res.groups
                v.pc.keys[h] = res.key
                v.pc.res[h] = res
        if v.hosts and not v.res and not v.why_not:
            v.why_not = "its inputs can't be read (no spec entry?)"
        if len(set(v.pc.keys.values())) > 1:
            hs = list(v.res)
            a, b = v.res[hs[0]].inputs, v.res[hs[1]].inputs
            v.pc.differ = sorted({f"{g}:{n}" for g, n, x, y in keys.diff_inputs(a, b) if g in HARD})[:20]
        return v

    def facts(self, h):
        if h == "mac":
            return {}
        f = self.ctx.facts.get(h)
        if f is not None and self.pending and self.set_name not in (f.get("sets") or {}):
            # a round not built yet: its binaries' names, as bko-bin's are, so that keys.resolve
            # expands an OS to the same OS/NAME it will have (their values are pending:ID)
            proto = ((f.get("sets") or {}).get("bko-bin") or {}).get("sums") or {}
            f = dict(f, sets=dict(f.get("sets") or {}, **{self.set_name: {"sums": {n: None for n in proto},
                                                                         "commits": {}, "digests": {},
                                                                         "pending": self.pending}}))
        return f

    def resolve(self, v, h, opts=None):
        f = self.facts(h)
        if f is None:
            return None
        fn = self.ctx.options.get("inputs_fn")
        o = dict(self.opts if v.family == self.fam else {}, **(opts or {}))
        o.setdefault("gitrepos", self.gitrepos())
        try:
            if fn:
                return fn(v.cell, h, o)
            return keys.resolve(v.cell, self.ctx.src, f, self.ctx.specs, o)
        except (KeyError, ValueError) as e:
            v.why_not = v.why_not or f"no inputs: {e}"
            return None

    def place(self, v):
        """The hosts that may run the cell (pins, R17's UEFI rescuer) and, if none, why."""
        reach = [h for h in self.ctx.hosts if h in self.ctx.facts]
        bad = (self.ctx.options.get("identity_bad") or {}).get(v.name)
        if bad is not None and v.family in ("stic", "holt"):
            v.why_not = f"its guest's identity failed: {bad}"
            return
        if v.family == "guest":
            self.place_guest(v, reach)
            return
        if v.family == "img":
            self.place_img(v, reach)
            return
        if v.family == "gate":
            if v.name == "selftest" or (v.chost == "mac"):
                v.hosts, v.pc.host = ["mac"], "mac"
                return
            pin = v.chost
            if pin is None:
                sf = self.ctx.specs.get("gate")
                try:
                    pin = (sf.entry(v.name) if sf else {}).get("pin")
                except KeyError:
                    pin = None
            if pin:
                v.pc.host = pin
                if pin not in self.ctx.hosts:
                    v.why_not = f"{pin} is not in --hosts"
                elif pin not in self.ctx.facts:
                    v.why_not = f"{pin} is unreachable"
                else:
                    v.hosts = [pin]
                return
            v.hosts = reach
        elif v.family == "holt":
            pin = self.cls["holt"].get("pins", {}).get(v.name)
            if pin:
                v.pc.host = pin
                if pin not in self.ctx.hosts:
                    v.why_not = f"{v.name} runs on {pin} only, which is not in --hosts"
                    return
                if pin not in self.ctx.facts:
                    v.why_not = f"{v.name} runs on {pin} only, which is unreachable"
                    return
                v.hosts = [pin]
            else:
                v.hosts = list(reach)
            if v.variant == "img":
                self.place_img_panel(v)
            elif self.holt_uefi(v.name) and v.hosts:
                with_u = [h for h in v.hosts if self.has_uefi_rescuer(h)]
                if with_u:
                    if len(with_u) < len(v.hosts):
                        v.pc.reasons_extra = [f"UEFI source: only {', '.join(with_u)} have rescuer-uefi.qcow2"]
                    v.hosts = with_u
                    if len(with_u) == 1:
                        v.pc.host = with_u[0]
                else:
                    note = "BIOS rescuer for a UEFI source: no host has rescuer-uefi.qcow2"
                    if note not in self.notes:
                        self.notes.append(note)
        else:
            v.hosts = list(reach)
            if self.set_name and not self.pending:
                spec = self.stic_spec(v.name)
                ok = [h for h in v.hosts if self.has_bins(h, spec)]
                if not ok and v.hosts:
                    s = self.ctx.drive.RESTIC_FROM.get(spec.os, spec.os) if spec else "?"
                    v.why_not = (f"no {spec.os}/rustic-bko and {s}/restic-bko in {self.set_name}"
                                 if spec else f"no binaries in {self.set_name}")
                v.hosts = ok
        if not v.hosts and not v.why_not:
            v.why_not = "no host in --hosts is reachable"

    def place_guest(self, v, reach):
        e = self.guest_entry(v.name)
        have = [h for h in reach if v.name in ((self.ctx.facts[h].get("guests")) or {})]
        pin = e.get("pin")
        if self.w.update and e.get("update", True) is False:
            v.why_not = "vm-update can't update it (guest/spec.json: update false)"
            return
        if pin:
            v.pc.host = pin
            if pin not in self.ctx.hosts:
                v.why_not = f"{v.name} runs on {pin} only, which is not in --hosts"
            elif pin not in have:
                v.why_not = f"{v.name} runs on {pin} only, which is unreachable or doesn't have it"
            else:
                v.hosts = [pin]
            return
        v.hosts = have
        if not have:
            v.why_not = f"{v.name} is on no reachable host"

    def place_img(self, v, reach):
        ims = {h: ((self.ctx.facts[h].get("images") or {}).get(v.name)) for h in reach}
        v.hosts = [h for h, im in ims.items() if im and im.get("done_exit") is not None]
        if not v.hosts:
            building = any(im and im.get("done_exit") is None for im in ims.values())
            v.why_not = (f"images/{v.name}/done.exit is missing: a build is under way, or stopped before its end"
                         if building else f"no current image of {v.name} on a reachable host")

    def place_img_panel(self, v):
        plat, e = self.panel_of(v.name)
        if e.get("blocked"):
            v.hosts, v.why_not = [], f"blocked: {e['blocked']}"
            return
        ims = {h: ((self.ctx.facts.get(h) or {}).get("images") or {}).get(plat) for h in v.hosts}
        ok = [h for h, im in ims.items() if im and not (im.get("pin") or {}).get("bins_stale")]
        if not ok and v.hosts:
            stale = [h for h, im in ims.items() if im]
            v.why_not = (f"the {plat} image's binaries no longer match bko-bin.sha256 (bins-stale on {', '.join(stale)})"
                         if stale else f"no current image of {plat} on a reachable host")
        v.hosts = ok

    def holt_uefi(self, guest):
        """R17: a Linux-kind source with --uefi (classes.json holt.uefi, or holt/spec.json's
        firmware for a linux or offline entry)."""
        if guest in self.cls["holt"].get("uefi", ()):
            return True
        sf = self.ctx.specs.get("holt")
        try:
            e = sf.entry(guest) if sf is not None else {}
        except KeyError:
            return False
        return e.get("firmware") == "uefi" and e.get("kind") in ("linux", "offline")

    def has_uefi_rescuer(self, h):
        imgs = (((self.ctx.facts.get(h) or {}).get("rescuers") or {}).get("images") or {})
        return any(str(n).endswith("rescuer-uefi.qcow2") for n in imgs)

    def stic_spec(self, guest):
        if not hasattr(self, "_stic_specs"):
            try:
                self._stic_specs = self.ctx.drive.load_specs(self.ctx.drive.all_guests())
            except SystemExit as e:
                raise PlanError(str(e))
        return self._stic_specs.get(guest)

    def have(self, h):
        sf = (((self.ctx.facts.get(h) or {}).get("sets") or {}).get(self.set_name) or {})
        return set((sf.get("sums") or {}).keys())

    def has_bins(self, h, spec):
        if spec is None:
            return False
        return self.ctx.drive.has_bins(spec, self.have(h))

    # --- the rules ---

    def match_host(self, v, r):
        """The host whose K a row matches (else its own host, else the first)."""
        if r.get("host") in v.res and v.pc.keys.get(r["host"]) == r.get("key"):
            return r["host"]
        for h, k in v.pc.keys.items():
            if k == r.get("key"):
                return h
        if r.get("host") in v.res:
            return r["host"]
        return next(iter(v.res), None)

    def groups_on(self, v, h):
        if h in v.grp:
            return v.grp[h]
        res = self.resolve(v, h)
        return res.groups if res is not None else None

    def inputs_on(self, v, h):
        if h in v.inp:
            return v.inp[h]
        res = self.resolve(v, h)
        return res.inputs if res is not None else {}

    def canaries(self, fam):
        c = self.cls.get(fam, {}).get("canaries")
        if isinstance(c, dict):
            return [f"{fam}:{g}" for g in c]
        return [f"{fam}:{g}" for g in (c or ())]

    def canary_rows_ok(self, fam, digest):
        """(True, []) when every runnable canary of FAM has a PASS-like valid row with this
        plumbing digest; else (False, the canaries still to pass).  Canaries no host can run
        are left out (and noted); with none runnable it isn't verified."""
        runnable, waiting, left_out = [], [], []
        for c in self.canaries(fam):
            cv = self.view(c)
            if not cv.hosts:
                left_out.append(c)
                continue
            runnable.append(c)
            newest = next((r for r in cv.rows if (r.get("groups") or {}).get("plumbing") == digest
                           and r.get("kind") in DECISIVE_KINDS and r.get("verdict") in DECISIVE_VERDICTS), None)
            if newest is None or not pass_like(newest):
                waiting.append(c)
        if left_out:
            n = f"canaries no host can run, left out of canary-verified: {', '.join(left_out)}"
            if n not in self.notes:
                self.notes.append(n)
        if not runnable:
            return False, ["no runnable canary"]
        return (not waiting), waiting

    def name_diffs(self, old, new, groups):
        out = []
        for g, n, a, b in keys.diff_inputs(old or {}, new or {}):
            if g in groups:
                out.append((g, n, a, b))
        return out

    def fmt_diffs(self, diffs, limit=4, v=None):
        """Per-name differences as the plan prints them (a binary's #digest left out beside the
        binary itself), with the binaries' source commits when V's last PASS names them."""
        names = {n for g, n, a, b in diffs}
        diffs = [d for d in diffs if not (d[1].endswith("#digest") and d[1][:-len("#digest")] in names)]
        parts = []
        for g, n, a, b in diffs[:limit]:
            parts.append(f"{g}:{n} {short(a)}->{short(b)}" + (self.commit_move(v, n) if g == "bin" else ""))
        if len(diffs) > limit:
            parts.append(f"(+{len(diffs) - limit} more)")
        return parts

    def commit_move(self, v, name):
        """ (rustic b518f42->6ccec58) for an archiver binary whose source commits moved."""
        if v is None or v.P is None or not name.endswith(("restic-bko", "rustic-bko")):
            return ""
        old = bin_commits((v.P.get("meta") or {}).get("bin"), name)
        if self.pending:
            new = self.fork_heads()
        else:
            h = self.match_host(v, v.P)
            new = bin_commits((((self.ctx.facts.get(h) or {}).get("sets") or {}).get(self.set_name)), name)
        mv = [f"{r} {old[r][:7]}->{new[r][:7]}" for r in carry.binary_repos(name)
              if old.get(r) and new.get(r) and old[r] != new[r]]
        return f" ({', '.join(mv)})" if mv else ""

    def classify(self, v):
        pc = v.pc
        pc.reasons = list(getattr(pc, "reasons_extra", []))
        fresh = self.fresh.get(v.cell)
        if v.P is not None:
            pc.last_pass = {"id": v.P.get("id"), "t": v.P.get("t"), "key": v.P.get("key"), "host": v.P.get("host")}
        if not v.res:
            pc.status, pc.rule = "not-runnable", fresh[0] if fresh else None
            pc.reasons.append(v.why_not or "no host can run it")
            return
        if self.pending and v.family == self.fam:
            self.predict_unbuilt(v)
        if fresh:
            pc.status, pc.rule = "due", fresh[0]
            pc.reasons += fresh[1]
            self.explain_current(v)
            return
        ks = set(pc.keys.values())
        at_k = [r for r in v.rows if r.get("key") in ks and r.get("kind") in DECISIVE_KINDS
                and r.get("verdict") in DECISIVE_VERDICTS]
        if at_k:
            top = at_k[0]
            failing = []
            for k in sorted(ks):    # R4 is per key: a newer PASS at the other host's key doesn't answer a FAIL at this one's
                here = next((r for r in at_k if r.get("key") == k), None)
                if here is not None and here.get("verdict") in ("FAIL", "FLAKY-FAIL"):
                    failing.append(here)
            if failing:             # to-class only when every failing key is: a real FAIL at one key still reruns
                top = next((r for r in failing if not (r.get("extra") or {}).get("to_class_only")), failing[0])
            if v.family == "img" and self.panel_owed(v, at_k, ks):
                return
            if top.get("verdict") in ("FAIL", "FLAKY-FAIL"):
                if (top.get("extra") or {}).get("to_class_only"):
                    pc.status, pc.rule = "to-class", None
                    pc.reasons.append(f"to be classed: {top.get('id')} (t2 guest-lines {top.get('run')}, "
                                      "or a guest/limits.json entry)")
                    return
                pc.status, pc.rule = "due", "failed"
                pc.reasons.append(f"{top.get('verdict')} at this key: {top.get('id')} ({(top.get('t') or '')[:10]})")
                if any(pass_like(r) and r.get("key") == top.get("key") for r in at_k):
                    pc.reasons.append("verdict changed at an unchanged key")
                    self.verdict_changed.append(v.cell)
                return
            if v.family in ("guest", "img"):
                lacking = [h for h, k in pc.keys.items()
                           if not any(r.get("key") == k and pass_like(r) for r in at_k)]
                if lacking:     # a QEMU moved, or an image rebuilt, on one host: every reachable host's key needs a PASS
                    v.hosts = lacking
                    pc.host = lacking[0]
                    self.changed(v, lacking[0])
                    return
            self.at_pass(v, top)
            return
        # R4 at the last PASS's own key too: a FAIL after it there isn't hidden by a change since
        if v.P is not None:
            later = [r for r in v.rows if r.get("key") == v.P.get("key") and r.get("kind") in DECISIVE_KINDS
                     and r.get("verdict") in ("FAIL", "FLAKY-FAIL") and (row_t(r), r.get("id", "")) >
                     (row_t(v.P), v.P.get("id", ""))]
            if later:
                pc.status, pc.rule = "due", "failed"
                pc.reasons.append(f"{later[0].get('verdict')} after its last PASS at that key: {later[0].get('id')}; "
                                  "inputs changed since")
                return
        if v.P is None:
            pc.status = "due"
            pc.rule = {"holt": "img:panel" if v.variant == "img" else "holt:new", "img": "img:new"}.get(v.family, "new")
            unc = [r for r in v.unc if r.get("key") in ks]
            if unc:
                pc.rule = "pass-uncommitted"
                pc.reasons.append(f"PASS from an uncommitted copy ({unc[0].get('id')}): counts once committed")
            else:
                pc.reasons.append("no PASS yet")
            return
        self.changed(v)

    def panel_owed(self, v, at_k, ks):
        """img:P whose newest decisive row at its key leaves the panel owed, failed, or blocked
        by something that's gone: due, and the runner re-runs only the panel.  `release` requires
        the panel: a boot PASS whose panel didn't pass is owed too (never run, or still blocked:
        the runner's release check then ends the word nonzero)."""
        panel = self.img_panel().get(v.name) or {}
        blocked = panel.get("blocked")
        for k in sorted(ks):
            r = next((x for x in at_k if x.get("key") == k), None)
            if r is not None and r.get("origin"):
                # (a boot row's own .panel row supersedes it, whatever their times and ids say)
                r = next((x for x in at_k if x.get("key") == k and x.get("origin") == r["origin"] + ".panel"), r)
            res = ((r or {}).get("extra") or {}).get("panel") or {}
            result = res.get("result")
            if result in ("owed", "fail") or (result == "blocked" and not blocked):
                v.pc.status, v.pc.rule = "due", "img:panel-owed"
                v.pc.reasons.append(f"its panel is {result}: {r.get('id')}")
                return True
            if self.w.release and r is not None and pass_like(r) and result != "pass" and panel.get("guests"):
                v.pc.status, v.pc.rule = "due", "img:panel-owed"
                v.pc.reasons.append(f"release requires the panel, and its panel is {result or 'not recorded'}: {r.get('id')}")
                return True
        return False

    def explain_current(self, v):
        """A fresh cell's reasons say how it stands in the ledger."""
        ks = set(v.pc.keys.values())
        if any(r.get("key") in ks and pass_like(r) for r in v.rows):
            v.pc.reasons.append("PASS at this key already")
        elif v.P is None:
            v.pc.reasons.append("no PASS yet")

    def at_pass(self, v, R):
        pc = v.pc
        unm = self.unmapped_for(v, R)
        if unm:
            # R5 (a): a file no spec names changes no key, so rule 4a looks past a PASS at K too
            pc.status, pc.rule = "due", "changed:unmapped"
            pc.reasons += unm[:20]
            return
        via = self.rows_by_id.get(R.get("via")) if R.get("kind") == "class" else None
        if via is not None:
            top = next((r for r in self.view(via["cell"]).rows if r.get("key") == via.get("key")
                        and r.get("kind") in DECISIVE_KINDS and r.get("verdict") in DECISIVE_VERDICTS), None)
            if top is not None and top.get("verdict") in ("FAIL", "FLAKY-FAIL"):
                pc.status, pc.rule = "due", "fanout:" + str(R.get("rule") or "class:?").split(":", 1)[-1]
                pc.reasons.append(f"its class row stands on {via['cell']}, which {top.get('verdict')}ed at that key since: "
                                  f"{top.get('id')}")
                return
        h = self.match_host(v, R)
        g_now = self.groups_on(v, h) or {}
        rg = R.get("groups") or {}
        pc.last_pass = {"id": R.get("id"), "t": R.get("t"), "key": R.get("key"), "host": R.get("host")}
        canary = v.cell in self.canaries(v.family)
        if g_now.get("plumbing") != rg.get("plumbing"):
            diffs = self.name_diffs(R.get("inputs"), self.inputs_on(v, h), ("plumbing",))
            self.plumbing_changed = True
            if v.family == "img":   # no canaries: ImgTest2 tests the images built since their PASS
                pc.status = "soft-stale"
                pc.reasons.append("plumbing changed" + (": " + "; ".join(self.fmt_diffs(diffs, 2)) if diffs else ""))
                return
            if canary:
                pc.status, pc.rule = "due", "canary:plumbing"
                pc.reasons += self.fmt_diffs(diffs) or ["plumbing changed"]
                return
            ok, waiting = self.canary_rows_ok(v.family, g_now.get("plumbing"))
            if ok:
                pc.status, pc.rule = "canary-verified", None
                pc.reasons.append("plumbing changed; every canary passed with it")
            else:
                pc.status, pc.rule = "awaiting-canaries", None
                pc.reasons.append("plumbing changed: waiting for " + ", ".join(waiting))
            return
        if g_now.get("soft") != rg.get("soft"):
            diffs = self.name_diffs(R.get("inputs"), self.inputs_on(v, h), ("soft",))
            guest_only = bool(diffs) and all(":" in n for _, n, _, _ in diffs)   # a guest's generation or fine fingerprint
            if canary and not guest_only:
                pc.status, pc.rule = "due", "canary:host"
                pc.reasons += self.fmt_diffs(diffs) or ["host versions changed"]
                return
            pc.status = "soft-stale"
            if guest_only:
                who = sorted({n.split(":", 1)[0] for _, n, _, _ in diffs})
                pc.reasons.append(f"{', '.join(who)} soft inputs changed: " + "; ".join(self.fmt_diffs(diffs, 2)))
            else:
                pc.reasons.append("host versions changed" + (": " + "; ".join(self.fmt_diffs(diffs, 2)) if diffs else ""))
            return
        unk = sorted({f"{g}:{n}" for h in v.res for g, x in (v.inp.get(h) or {}).items() if g in ("guest", "xguest")
                      for n, val in (x or {}).items() if val == keys.UNKNOWN_IMG})
        if unk:
            # an identity not summed yet is treated as changed (keys.py): a PASS at it proves nothing
            groups = [g for g in HARD if any(u.startswith(g + ":") for u in unk)]
            pc.status, pc.rule = "due", "changed:" + "+".join(groups)
            pc.reasons.append("identity unknown, treated as changed: " + ", ".join(unk[:4]))
            return
        pc.status = "current"

    def changed(self, v, host=None):
        pc, P = v.pc, v.P
        h = host or self.match_host(v, P)
        if h not in v.inp:
            h = next(iter(v.inp))
        now_g, now_i = v.grp[h], v.inp[h]
        pg = P.get("groups") or {}
        empty = sha256_hex(canonical({}))[:16]
        v.G = {g for g in HARD if now_g.get(g, empty) != pg.get(g, empty)}
        v.D = self.name_diffs(P.get("inputs"), now_i, HARD)
        v.change_set = frozenset((g, n, b) for g, n, a, b in v.D)
        pc.change = v.change_set
        unm = self.unmapped_for(v, P)
        if unm:
            pc.status, pc.rule = "due", "changed:unmapped"
            pc.reasons += unm[:20]
            return
        ks = set(pc.keys.values())
        unc = [r for r in v.unc if r.get("key") in ks]
        if unc:
            pc.reasons.append(f"pass-uncommitted: {unc[0].get('id')} passed at this key from an uncommitted copy "
                              "(counts once committed)")
        if not v.G:
            # the hard inputs are the PASS's, under another key (a cell name or spec change)
            pc.status, pc.rule = "due", "changed:key"
            pc.reasons.append("its key changed with no hard input changed")
            return
        if v.family in ("stic", "holt") and "guest" in v.G and self.fingerprint_change(v, h):
            pc.fp_carry = True
            if v.G == {"guest"}:
                pc.status, pc.rule, pc.via = "carried", "carry:fingerprint", P.get("id")
                pc.reasons.append("the guest's identity moved to its fingerprint: the same image")
                return
            v.G = v.G - {"guest"}
            v.D = [d for d in v.D if d[0] != "guest"]
        fn = {"stic": self.stic_rule, "holt": self.holt_rule, "gate": self.gate_rule, "guest": self.guest_rule,
              "img": self.img_rule}[v.family]
        fn(v)

    def fingerprint_change(self, v, h):
        """True when the guest group's only change against P is one of carry:fingerprint's two
        (G:generation gone and G:fingerprint added, the sidecar at P's generation; or
        G:fingerprint changed, P's value in the sidecar's "was")."""
        by = {}
        for g, n, a, b in v.D:
            if g != "guest":
                continue
            gname, _, what = n.rpartition(":")
            if what not in ("generation", "fingerprint"):
                return False
            by.setdefault(gname, {})[what] = (a, b)
        if not by:
            return False
        guests = (self.ctx.facts.get(h) or {}).get("guests") or {}
        for gname, d in by.items():
            rec = keys.fingerprint_of(guests.get(gname))
            fp = d.get("fingerprint")
            if rec is None or fp is None:
                return False
            if "generation" in d:
                if d["generation"] != ("gen:" + rec["generation"], None) or fp[0] is not None:
                    return False
            elif not fp[0] or not fp[0].startswith("fp:") or fp[0][3:] not in (rec.get(f"{v.family}_was") or ()):
                return False
        return True

    def predict_unbuilt(self, v):
        """Binaries not built yet (contract 1.6), binary by binary: one whose own source repos
        (carry.binary_repos) are at the fork heads in the last PASS's meta.bin is predicted
        unchanged (the PASS's value and #digest stand in); the others stay pending:."""
        if v.P is None:
            return
        heads = self.fork_heads()
        meta = (v.P.get("meta") or {}).get("bin")
        pbin = (v.P.get("inputs") or {}).get("bin") or {}
        for h in list(v.inp):
            names = sorted({n.split("#")[0] for n in v.inp[h].get("bin", {})})
            same = {n for n in names if bin_commits(meta, n)
                    and all(bin_commits(meta, n).get(r) == heads.get(r) for r in carry.binary_repos(n))}
            if not same:
                continue
            inputs = {g: dict(x) for g, x in v.inp[h].items()}
            inputs["bin"] = {n: (pbin.get(n, val) if n.split("#")[0] in same else val)
                             for n, val in inputs["bin"].items()}
            v.inp[h], v.grp[h] = inputs, keys.groups(inputs)
            v.pc.keys[h] = keys.key(v.cell, inputs)
            note = ("not built: assumed reused (its last PASS ran the fork heads' commits)" if same == set(names) else
                    f"not built: {', '.join(sorted(same))} assumed reused (the fork heads' commits)")
            if note not in v.pc.reasons:
                v.pc.reasons.append(note)

    # --- unmapped paths (R5 (a)) ---

    def named_paths(self):
        if self._named is None:
            named = set()
            for fam in ("stic", "holt", "gate", "guest", "img"):
                sf = self.ctx.specs.get(fam)
                if sf is None:
                    continue
                cells = [f"{fam}:{n}" for n in sf.names()]
                if fam == "holt":
                    for var, gs in self.cls["holt"]["variants"].items():
                        cells += [f"holt:{g}+{var}" for g in gs]
                    cells += self.img_cells()
                for c in cells:
                    try:
                        r = keys.resolve(c, self.ctx.src, {}, self.ctx.specs,
                                         {"set": "x", "rustic_set": "x", "gitrepos": self.gitrepos()})
                    except (KeyError, ValueError):
                        continue
                    for g in ("harness", "judge", "plumbing", "xguest", "host"):
                        for n in r.inputs.get(g, {}):
                            p = n.split("#", 1)[0] if g in ("judge", "harness") else n
                            if p.startswith("shared:"):
                                p = p[len("shared:"):]
                                p = "testvm/compare/cmp-run.sh" if p == "cmp-run.sh" else p
                            if "/" in p and not p.startswith(("host:", "git:")):
                                named.add(p)
            self._named = named
        return self._named

    def unmapped_for(self, v, P):
        commit = P.get("commit")
        if not commit or not re.fullmatch(r"[0-9a-f]{7,40}", commit or ""):
            return []
        k = (commit, canonical(P.get("dirty") or {}), v.family)
        if k not in self._unmapped:
            full = self.ctx.git.rev(commit)
            if not full:
                self._unmapped[k] = []
            else:
                watch = (self.policy.get("watch") or {}).get(v.family, [])
                ign = self.policy.get("unmapped_ignore", [])
                named = self.named_paths()

                def keep(p):
                    # filtered before any blob is read: only watched paths no spec names are hashed
                    return p not in named and globmatch(p, watch) and not globmatch(p, ign)
                changed = carry.changed_since(self.ctx.git, self.ctx.head, full, P.get("dirty") or {}, WATCH_ROOTS,
                                              keep=keep, wt=self.wt_names(WATCH_ROOTS))
                self._unmapped[k] = sorted(changed)
                for p in self._unmapped[k]:
                    if p not in self.plan.unmapped:
                        self.plan.unmapped.append(p)
        return self._unmapped[k]

    # --- family rules ---

    def judge_policy(self):
        return self.policy.get("judge_change", "due")

    def stic_rule(self, v):
        pc, G = v.pc, v.G
        if G == {"bin"}:
            ok, rule, why = self.archiver_carry(v)
            if ok:
                pc.status, pc.rule, pc.via = "carried", rule, v.P.get("id")
                pc.reasons += why
                return
            pc.reasons += why[:2]
        if G == {"judge"} and self.judge_policy() == "soft-stale":
            pc.status = "soft-stale"
            pc.reasons.append("judge changed; re-judging waits for G2 (policy.judge_change soft-stale)")
            return
        pc.status, pc.rule = "due", "changed:" + "+".join(g for g in HARD if g in G)
        pc.reasons[:0] = self.fmt_diffs(v.D, v=v)

    def archiver_carry(self, v):
        """(ok, rule, reasons) for a bin-only change of an archiver cell."""
        meta = (v.P.get("meta") or {}).get("bin") or {}
        h = self.match_host(v, v.P)
        sf = (((self.ctx.facts.get(h) or {}).get("sets") or {}).get(self.set_name)) or {}
        names = sorted({n.split("#")[0] for g, n, a, b in v.D if g == "bin"})
        allbins = sorted({n.split("#")[0] for n in (v.P.get("inputs") or {}).get("bin", {})} | set(names))
        old_all, new_all = {}, {}
        for n in allbins:
            old_all.update(bin_commits(meta, n))
            new_all.update(dict(self.fork_heads()) if self.pending else bin_commits(sf, n))
        rules, why = [], []
        for n in names:
            if n.endswith("bko-holt"):
                return False, None, [f"{n} changed"]
            old = bin_commits(meta, n)
            new = dict(self.fork_heads()) if self.pending else bin_commits(sf, n)
            rule, w = self.carry.binary(n, old, new, digest_text(meta, n), None if self.pending else digest_text(sf, n),
                                        old_all, new_all)
            if rule is None:
                return False, None, [w]
            rules.append(rule)
            why.append(w)
        if not rules:
            return False, None, ["no binary changed"]
        order = ("carry:version-only", "carry:dfly-image", "carry:affected-v1")
        rule = min(rules, key=lambda r: order.index(r) if r in order else 9)
        return True, rule, sorted(set(why))

    def gate_rule(self, v):
        pc, G = v.pc, v.G
        if G == {"judge"} and self.judge_policy() == "soft-stale":
            pc.status = "soft-stale"
            pc.reasons.append("judge changed; re-judging waits for G2 (policy.judge_change soft-stale)")
            return
        pc.status, pc.rule = "due", "changed:" + "+".join(g for g in HARD if g in G)
        pc.reasons[:0] = self.fmt_diffs(v.D, v=v)

    def guest_rule(self, v):
        self.gate_rule(v)

    def img_rule(self, v):
        pc, G = v.pc, v.G
        if "bin" in G:
            pc.status, pc.rule = "due", "img:new"
            pc.reasons[:0] = self.fmt_diffs(v.D, v=v)
            return
        pc.status = "soft-stale"
        pc.reasons.append(f"changed ({'+'.join(g for g in HARD if g in G)}): ImgTest2 tests the images built since "
                          "their PASS")
        result = (((v.P or {}).get("extra") or {}).get("panel") or {}).get("result")
        if self.w.release and result != "pass" and (self.img_panel().get(v.name) or {}).get("guests"):
            pc.status, pc.rule = "due", "stale"     # the owed-panel path reads rows at the key, and this key has none
            pc.reasons.insert(0, f"release requires the panel, and its last PASS's panel is {result or 'not recorded'}")

    # --- GuestTest2's 7 days, the update round's --try ---

    def try_days(self):
        return float(((self.policy.get("guest") or {}).get("run") or {}).get("try_days", 7))

    def try_when(self, r):
        """When R's --try ran.  A class row only accepted that --try's log lines, later: it is dated
        by the row it classes, or the 7 days would restart with no look at the upstream repositories."""
        if r.get("kind") == "class" and r.get("via"):
            via = self.rows_by_id.get(r["via"])
            return row_t(via) if via is not None else ""
        return row_t(r)

    def last_try(self, v, passed=False):
        """The time of the newest --try: rows with valid: true and extra.try_ran (any row, committed
        or not: upstream repositories are in no key); PASSED: only a PASS, or the class row that
        made one.  None when there is none."""
        best = ""
        for r in v.allrows:
            if r.get("valid") is not True:
                continue
            ran = (r.get("extra") or {}).get("try_ran")
            if r.get("kind") == "class" and r.get("via"):
                via = self.rows_by_id.get(r["via"]) or {}
                ran = ran or (via.get("extra") or {}).get("try_ran")
            if ran and (not passed or pass_like(r)):
                best = max(best, self.try_when(r))
        return best or None

    def older_than_days(self, when):
        t = parse_utc(when) if when else None
        return t is None or self.ctx.now - t > self.try_days() * 86400

    def guest_try_pass(self, views):
        """guest:7day: a --try cell not due, whose last --try is older than try_days."""
        for v in views:
            if v.pc.status not in ("current", "soft-stale", "canary-verified", "awaiting-canaries", "to-class"):
                continue
            if not self.guest_entry(v.name).get("try"):
                continue
            when = self.last_try(v)
            if self.older_than_days(when):
                v.pc.status, v.pc.rule = "due", "guest:7day"
                v.pc.reasons.insert(0, "its last --try is older than " + f"{self.try_days():g} days"
                                    + (f" ({when[:10]})" if when else " (none on record)"))

    def guest_update_pass(self, views):
        """A bare GuestTest2 update: every guest needs a --try PASS within try_days."""
        if not (self.w.update and self.w.form == "bare"):
            return
        for v in views:
            if v.pc.status == "due" and self.older_than_days(self.last_try(v, passed=True)):
                v.pc.status, v.pc.rule, v.pc.refused = "not-runnable", None, True
                v.pc.reasons = [f"no --try PASS within {self.try_days():g} days: run t2 GuestTest2 {v.name} first"]

    # --- ImgTest2's refusal ---

    def estimator(self):
        if self._estimator is None:
            rows = {c: sorted(rs, key=lambda r: (row_t(r), r.get("id", ""))) for c, rs in self.rows_by_cell.items()}
            self._estimator = estimates.Estimator(rows, estimates.load_tsv(estimates.default_tsv_path(self.ctx.checkout)),
                                                  estimates.readme_estimates(self.ctx.drive) if self.fam == "stic" else {})
        return self._estimator

    def bin_rows(self):
        """{(family, name, value): {cell: [row, newest first]}}: every valid, committed, decisive
        stic and holt row, by the binaries it names.  Never a +img cell's: a panel tests the image
        itself, it is no evidence for letting that image be tested, and its FAIL is the
        img:panel-owed rule's to answer."""
        if self._binrows is None:
            out = {}
            for cell, rs in self.rows_by_cell.items():
                fam = cell.split(":", 1)[0]
                if fam not in ("stic", "holt") or cell.endswith("+img"):
                    continue
                for r in sorted(rs, key=lambda x: (row_t(x), x.get("id", "")), reverse=True):
                    if r.get("kind") in DECISIVE_KINDS and r.get("verdict") in DECISIVE_VERDICTS and self.valid(r)[1]:
                        for n, val in ((r.get("inputs") or {}).get("bin") or {}).items():
                            if "#" not in n:
                                out.setdefault((fam, n, val), {}).setdefault(cell, []).append(r)
            self._binrows = out
        return self._binrows

    def carried_chain_ok(self, r):
        """Walk a carried PASS through via to a run row: every link must be carry:version-only."""
        seen = set()
        while r is not None and r.get("kind") == "carried":
            if r.get("rule") != "carry:version-only" or r.get("id") in seen:
                return False
            seen.add(r.get("id"))
            r = self.rows_by_id.get(r.get("via"))
        return r is not None and r.get("kind") == "run" and pass_like(r)

    def holt_code_at(self, commit):
        """CODE of the holt tree at COMMIT, or None when it isn't in this repository."""
        if commit not in self._holt_codes:
            try:
                self._holt_codes[commit] = keys.holt_code(keys.GitSource(self.ctx.checkout, commit, scope=("holt",)))
            except (ValueError, OSError):
                self._holt_codes[commit] = None
        return self._holt_codes[commit]

    def binary_state(self, pairs):
        """(state, why, failing cell or None) of a binary from PAIRS [(family, name, value)]: none
        when no row or a cell's newest decisive row at that digest failed; else real, carried or
        carried-other."""
        per = {}
        for fam, n, val in pairs:
            for cell, rs in (self.bin_rows().get((fam, n, val)) or {}).items():
                per.setdefault(cell, []).extend(rs)
        best = None
        for cell, rs in sorted(per.items()):
            rs.sort(key=lambda x: (row_t(x), x.get("id", "")), reverse=True)
            if rs[0].get("verdict") in ("FAIL", "FLAKY-FAIL"):
                return "none", f"{cell}'s newest row at that digest is {rs[0].get('verdict')} ({rs[0].get('id')})", cell
            atk = {}
            for r in rs:
                atk.setdefault(r.get("key"), r)
            for r in atk.values():
                if not pass_like(r):
                    continue
                st = "real" if r.get("kind") == "run" else \
                    "carried" if r.get("kind") == "carried" and self.carried_chain_ok(r) else "carried-other"
                rank = ("carried-other", "carried", "real").index(st)
                if best is None or rank > best[0]:
                    best = (rank, st, r)
        if best is None:
            return "none", "no PASS at its digest", None
        return best[1], best[2].get("id"), None

    @staticmethod
    def cell_command(cell):
        """The word that runs a stic or holt CELL by name."""
        fam, name = cell.split(":", 1)
        return f"t2 SticTest2 ROUND {name}" if fam == "stic" else f"t2 HoltTest2 {name}"

    def canary_cell(self, plat, name):
        """The platform's cheapest cell (by the Estimator) that uses binary NAME."""
        est = self.estimator()
        if name == "bko-holt":
            sf = self.ctx.specs.get("holt")
            cells = []
            for g in (sf.names() if sf is not None else ()):
                try:
                    if (sf.entry(g).get("plat") or "linux") == plat:
                        cells.append(f"holt:{g}")
                except KeyError:
                    continue
        else:
            osn = "omnios" if plat == "illumos" else plat
            try:
                cells = [f"stic:{g}" for g in self.ctx.drive.all_guests() if getattr(self.stic_spec(g), "os", None) == osn]
            except PlanError:
                cells = []
        if not cells:
            return None, None
        c = min(cells, key=lambda c: (est.cell(c)["gm"], c))
        return c, self.cell_command(c)

    def judge_binary(self, plat, name, hexd, im):
        """{state, digest, why, canary} of one of an image's binaries (contract 1.7's four steps)."""
        out = {"state": "none", "digest": hexd, "why": None, "canary": None}
        val = "sha256:" + hexd
        pairs, failing = None, None
        if name == "bko-holt":
            commit = im.get("holt_commit")
            code = self.holt_code_at(commit) if commit else None
            if not commit:
                out["why"] = "no bko-holt.commit beside the image: rebuild it (stick/img-refresh-holt.sh, stick/img-lanes.sh)"
            elif code is None:
                out["why"] = f"bko-holt.commit {commit[:12]} isn't in this repository: rebuild the image from a commit that is"
            else:
                pairs = [("holt", f"{plat}/bko-holt", "code:" + code), ("holt", f"{plat}/bko-holt", val)]
        else:
            osn = "omnios" if plat == "illumos" else plat
            pairs = [("stic", f"{osn}/{name}", val), ("holt", f"{plat}/{name}", val)]
        if pairs is not None:
            out["state"], w, failing = self.binary_state(pairs)
            if out["state"] != "real":
                out["why"] = w
        if pairs is None or out["state"] not in ("none", "carried-other"):
            return out      # (with no commit to go on no test can give it a PASS: only the rebuild can)
        out["canary"], out["command"] = (failing, self.cell_command(failing)) if failing else self.canary_cell(plat, name)
        return out

    def listed_bins(self, plat):
        """The binaries img/spec.json says PLAT's image holds (restic-bko isn't on Haiku)."""
        sf = self.ctx.specs.get("img")
        try:
            bins = sf.entry(plat).get("bins") or () if sf is not None else ()
        except KeyError:
            return ()
        return [b.split("/", 1)[1] for b in bins if b.endswith(("/restic-bko", "/rustic-bko", "/bko-holt"))]

    def bin_changed(self, v):
        """True when img:P has no PASS, or its bin group differs from the PASS's on any host."""
        if v.P is None:
            return True
        then = (v.P.get("groups") or {}).get("bin")
        return any(g.get("bin") != then for g in v.grp.values())

    def img_refuse_pass(self, views):
        """Per img:P that would run: every binary needs a PASS at its digest (contract 1.7)."""
        for v in views:
            if v.pc.status != "due":
                continue
            bins, bad = {}, []
            for h in v.hosts:
                im = ((self.ctx.facts.get(h) or {}).get("images") or {}).get(v.name) or {}
                for name, hexd in sorted((im.get("bins") or {}).items()):
                    if (name, hexd) in bins:
                        continue
                    res = bins[(name, hexd)] = self.judge_binary(v.name, name, hexd, im)
                    if res["state"] in ("none", "carried-other"):
                        bad.append((name, res))
                for name in self.listed_bins(v.name):   # one bko-bin.sha256 doesn't list can't be vouched for
                    if name not in (im.get("bins") or {}) and (name, "") not in bins:
                        res = bins[(name, "")] = {"state": "none", "digest": "", "canary": None,
                                                  "why": f"not in images/{v.name}/bko-bin.sha256: rebuild the image"}
                        bad.append((name, res))
            v.pc.binaries = {n: r for (n, _), r in sorted(bins.items())}
            if not bad:
                continue
            why = []
            for name, res in bad:
                what = "only a carried PASS, not by version bumps alone" if res["state"] == "carried-other" else "no PASS at its digest"
                t = f"{v.name}/{name} {res['digest'][:12]}".rstrip() + f": {what}" + (f" ({res['why']})" if res.get("why") and res["why"] != what else "")
                if res.get("command"):
                    t += f"; run {res['command']}"
                why.append(t)
            # contract 1.7's exit: bin group changed since its PASS, or none; and what a word named. An
            # owed panel's image can't be let off either ("a failed or owed panel exits nonzero")
            v.pc.refused = v.cell in self.fresh or self.w.release or v.pc.rule == "img:panel-owed" or self.bin_changed(v)
            v.pc.status, v.pc.rule = "not-runnable", None
            v.pc.reasons = why

    # --- notes over the guests' sidecars ---

    def fingerprint_rules(self):
        """sha256 of the two files' sha256 hex, fingerprint.py's first (as fingerprint.py writes it)."""
        a, b = self.ctx.src.sha256("testvm/update/fingerprint.py"), self.ctx.src.sha256("testvm/holttest1d/judge.py")
        return sha256_hex((a or "") + (b or "")) if a and b else None

    def guest_notes(self, views):
        """disk differs from its merge record; fingerprint rules changed."""
        differ, rules_old = [], False
        now = self.fingerprint_rules()
        for name in sorted({v.name for v in views}):
            for h in self.ctx.hosts:
                g = ((self.ctx.facts.get(h) or {}).get("guests") or {}).get(name) or {}
                st = g.get("stat") or {}
                if st and g.get("generation") and st.get("generation") == g.get("generation") and name not in differ:
                    rec = st.get("disks") or {}
                    for d in g.get("disks") or ():
                        want = rec.get(os.path.basename(d.get("path") or ""))
                        if want is None:        # (a disk the record never saw differs, as guest.py has it)
                            differ.append(name)
                            break
                        if d.get("mtime_ns") is None:
                            continue
                        if (d.get("size"), d.get("mtime_ns"), d.get("inode")) != \
                                (want.get("size"), want.get("mtime_ns"), want.get("inode")):
                            differ.append(name)
                            break
                rec = keys.fingerprint_of(g)
                if now and rec and rec.get("rules") and rec["rules"] != now:
                    rules_old = True
        if differ:
            self.notes.append("disk differs from its merge record: " + ", ".join(differ))
        if rules_old:
            self.notes.append("fingerprint rules changed: run t2 guest-fingerprints --reproject")

    # --- holt ---

    def holt_plat(self, name):
        p = name.split("/", 1)[0] if "/" in name else "linux"
        return "illumos" if p == "omnios" else p

    def holt_rule(self, v):
        pc, G, D, P = v.pc, v.G, v.D, v.P
        if v.variant == "img":
            pc.status, pc.rule = "due", "img:panel"
            pc.reasons[:0] = self.fmt_diffs(D, v=v)
            return
        sel, carried, soft = [], [], []
        bins = sorted({n.split("#")[0] for g, n, a, b in D if g == "bin"})
        hb = [n for n in bins if n.endswith("bko-holt")]
        rb = [n for n in bins if n.endswith("rustic-bko")]
        ob = [n for n in bins if n not in hb and n not in rb]
        hpanel = self.cls["holt"].get("rustic_panel", {})
        if hb:
            st, rule, why = self.holt_code_rule(v)
            if st == "due":
                sel.append((rule, why))
            elif st == "carry":
                carried.append((rule, why))
            else:
                soft.append(why)
        if rb:
            plats = sorted({self.holt_plat(n) for n in rb})
            panel = {g for p in plats for g in hpanel.get(p, ())}
            if v.variant is None and v.name in panel:
                sel.append(("holt:rustic", f"rustic-bko changed ({', '.join(rb)}): {v.name} is in the "
                                           f"{'/'.join(plats)} rustic panel"))
            elif not hb:
                carried.append(("carry:rustic-panel", f"rustic-bko changed ({', '.join(rb)}); "
                                                      f"the {'/'.join(plats)} rustic panel covers it"))
        if ob:
            sel.append(("changed:bin", "binaries changed: " + ", ".join(ob)))
        if "guest" in G:
            sel.append(("holt:guest-update", "; ".join(self.fmt_diffs([d for d in D if d[0] == "guest"], 3))))
        if "harness" in G:
            sel.append(("holt:harness", "; ".join(self.fmt_diffs([d for d in D if d[0] == "harness"], 3))))
        if "judge" in G:
            if self.judge_policy() == "soft-stale":
                soft.append("judge changed; re-judging waits for G2")
            else:
                sel.append(("holt:judge", "judge changed; re-judging waits for G2: " +
                            "; ".join(self.fmt_diffs([d for d in D if d[0] == "judge"], 2))))
        for g in ("xguest", "host"):
            if g in G:
                sel.append((f"changed:{g}", "; ".join(self.fmt_diffs([d for d in D if d[0] == g], 2))))
        if "rescuer" in G:
            v.rescuer_names = [n for g, n, a, b in D if g == "rescuer"]
        if sel:
            pc.status, pc.rule = "due", sel[0][0]
            pc.reasons += [w for _, w in sel if w]
            return
        if "rescuer" in G:
            pc.status = "rescuer?"          # decided by the rescuer panel, after the pass
            pc.reasons += [w for _, w in carried] + soft
            return
        if G == {"bin"} and carried and not soft:
            order = ("carry:version-only", "carry:plan-diff", "carry:rustic-panel")
            rule = min((r for r, _ in carried), key=lambda r: order.index(r) if r in order else 9)
            pc.status, pc.rule, pc.via = "carried", rule, P.get("id")
            pc.reasons += [w for _, w in carried]
            return
        pc.status = "soft-stale"
        pc.reasons.append(f"changed ({'+'.join(g for g in HARD if g in G)}); no rule selects it")
        pc.reasons += soft[:2]

    def holt_code_rule(self, v):
        """bko-holt's code changed: version-only (carried), plan-visible (R15) or all 53 (holt:code)."""
        P = v.P
        # R14: rows of a build that isn't CODE's (HoltTest1 runs, --holt-bin sets) count only
        # where equal, whatever the tree's diff says; so does any row when the set is a --holt-bin one.
        if self.opts.get("holt_sha256"):
            return self.holt_all(v, "--holt-bin: a ready set counts only where its binaries are equal (R14)")
        pbin = (P.get("inputs") or {}).get("bin") or {}
        if any(n.endswith("bko-holt") and not str(x).startswith("code:") for n, x in pbin.items()):
            return self.holt_all(v, "its last PASS used a bko-holt that isn't a CODE build (R14)")
        old = (P.get("meta") or {}).get("holt", {}).get("commit") or P.get("commit")
        full = self.ctx.git.rev(old) if old else None
        if not full:
            return self.holt_all(v, f"holt code diff unknown (no commit {old or '?'} here)")
        dirty = P.get("dirty") if (P.get("meta") or {}).get("holt", {}).get("commit") in (None, P.get("commit")) else {}
        ch = self.changed_paths(full, dirty or {}, ("holt",))
        paths = carry.holt_code_paths(ch)
        if not paths:
            return self.holt_all(v, "holt's code digest changed, but no code path differs from its last PASS's tree")
        ok, bad = carry.version_only_files(
            paths, lambda p: carry.old_content(self.ctx.git, self.ctx.head, full, dirty or {}, p),
            self.ctx.git.worktree)
        if ok:
            return "carry", "carry:version-only", f"holt version lines only ({', '.join(paths)})"
        visible = self.cls["holt"].get("plan_visible", [])
        # a release bump beside a fix (Cargo.toml's and Cargo.lock's version lines) is
        # version-only on its own: only the other paths decide plan-visible
        vis_paths = [b[:-len(" added")] if b.endswith(" added") else b[:-len(" deleted")] if b.endswith(" deleted")
                     else b for b in bad]
        golden, known = self.golden_changes(full, dirty or {})
        mine = golden.get(v.name if v.variant is None else f"{v.name}+{v.variant}")
        base = golden.get(v.name)
        vst = set(self.cls["holt"].get("variant_stages", ()))
        if vis_paths and all(p in visible for p in vis_paths):
            if not known:
                return self.holt_all(v, "plan diff not known: the fast holt gate runs first")
            if v.variant is None:
                if mine is not None:
                    return "due", "holt:plan-diff", f"golden plan changed ({', '.join(mine) or 'stages unknown'})"
            else:
                if v.variant == "degraded":
                    return "due", "holt:variants", "plan-visible change; its degraded source dump isn't in the corpus"
                if mine is not None:
                    return "due", "holt:plan-diff", f"golden plan {v.name}+{v.variant} changed ({', '.join(mine)})"
                if base is not None and (not base or set(base) & vst):
                    return "due", "holt:variants", (f"{v.name}'s golden plan changed in "
                                                    f"{', '.join(sorted(set(base) & vst)) or 'stages unknown'}")
            return "carry", "carry:plan-diff", "plan-visible change; its golden plans are unchanged"
        why = "holt code changed: " + ", ".join(paths[:4]) + (" ..." if len(paths) > 4 else "")
        if v.variant is None:
            r = self.holt_all(v, why)
            if mine is not None:
                r = (r[0], r[1], r[2] + f"; holt:plan-diff ({', '.join(mine) or 'stages unknown'})")
                v.plan_stages = mine
            return r
        if mine is not None:
            return "due", "holt:plan-diff", f"golden plan {v.name}+{v.variant} changed ({', '.join(mine)})"
        if base is not None and (not base or set(base) & vst):
            return "due", "holt:variants", f"{v.name}'s golden plan changed in {', '.join(sorted(set(base) & vst)) or 'stages unknown'}"
        return "none", None, why + "; the variant's plans are unchanged"

    def holt_all(self, v, why):
        if v.variant is None:
            return "due", "holt:code", why
        return "none", None, why + " (a variant runs on its plan diff)"

    def golden_changes(self, old_commit, old_dirty):
        """{GUEST or GUEST+bigger: [stage...]} whose golden plan changed, from (a) the corpus's
        plan.txt files since OLD_COMMIT and (b) the newest gate:holt row's plan_diff at the
        current holt tree key; and whether the diff is known at all."""
        k = (old_commit, canonical(old_dirty))
        if k in self._golden:
            return self._golden[k]
        out = {}
        ch = self.changed_paths(old_commit, old_dirty, ("holt/tests/corpus",))
        blessed = False
        for p in ch:
            m = re.match(r"holt/tests/corpus/([^/]+)/plan(\+bigger)?\.txt\Z", p)
            if not m:
                continue
            blessed = True
            name = m.group(1) + (m.group(2) or "")
            oldt = carry.old_content(self.ctx.git, self.ctx.head, old_commit, old_dirty, p)
            newt = self.ctx.git.worktree(p)
            out[name] = self.stages(oldt, newt)
        row = self.gate_holt_row()
        if row is not None:
            pdf = (row.get("extra") or {}).get("plan_diff") or {}
            differ = pdf.get("differ") if isinstance(pdf.get("differ"), dict) else \
                {g: x for g, x in pdf.items() if g not in ("v", "corpus", "invariants", "invariants_skipped")}
            for g, x in (differ or {}).items():
                st = list((x or {}).get("stages") or []) if isinstance(x, dict) else []
                out[g] = sorted(set(out.get(g, [])) | set(st))
        r = (out, blessed or row is not None)
        self._golden[k] = r
        return r

    def stages(self, old, new):
        if self.plandiff is None or old is None or new is None:
            return []
        try:
            return list(self.plandiff.stages(old.decode("utf-8", "replace"), new.decode("utf-8", "replace")))
        except Exception:   # noqa: BLE001 - unknown stages: every variant runs
            return []

    def gate_holt_row(self):
        if self._gate_holt is None:
            self._gate_holt = False
            gv = View("gate:holt", "gate")
            saved = (self.opts, self.fam)
            try:
                self.opts = {"set": "bko-bin"}
                self.place(gv)
                ks = set()
                for h in gv.hosts:
                    res = self.resolve(gv, h, {"set": "bko-bin"})
                    if res is not None:
                        ks.add(res.key)
            finally:
                self.opts, self.fam = saved
            rows = sorted((r for r in self.rows_by_cell.get("gate:holt", ()) if r.get("key") in ks
                           and self.valid(r)[0] and (r.get("extra") or {}).get("plan_diff") is not None),
                          key=lambda r: (row_t(r), r.get("id", "")), reverse=True)
            self._gate_holt = rows[0] if rows else False
        return self._gate_holt or None

    def holt_rescuer_pass(self, views):
        panels = self.cls["holt"].get("rescuer_panel", {})
        cand = {}
        for v in views:
            if v.pc.status != "rescuer?" and not (v.pc.status == "due" and v.rescuer_names):
                continue
            for n in v.rescuer_names:
                for pk, p in panels.items():
                    if any(m in n for m in p.get("match", ())):
                        cand.setdefault(pk, []).append(v)
        chosen = {}
        for pk, vs in cand.items():
            p = panels[pk]
            base = [v for v in vs if v.variant is None]
            if "guests" in p:
                for v in base:
                    if v.name in p["guests"]:
                        chosen.setdefault(v.cell, []).append(f"rescuer {pk} changed: in its panel")
            if "stalest_of" in p:
                pool = [v for v in base if v.name in p["stalest_of"]]
                if pool:
                    st = min(pool, key=lambda v: (self.last_run_t(v), v.cell))
                    chosen.setdefault(st.cell, []).append(f"rescuer {pk} changed: the stalest of its panel")
                for v in base:
                    if v.name in p.get("plus", ()):
                        chosen.setdefault(v.cell, []).append(f"rescuer {pk} changed: in its panel")
        for v in views:
            if v.pc.status == "due" and v.cell in chosen:
                v.pc.reasons += chosen[v.cell]
            elif v.pc.status == "rescuer?":
                if v.cell in chosen:
                    v.pc.status, v.pc.rule = "due", "holt:rescuer"
                    v.pc.reasons[:0] = chosen[v.cell]
                else:
                    v.pc.status = "soft-stale"
                    v.pc.reasons.insert(0, "rescuer changed (" + ", ".join(v.rescuer_names[:2]) + "); not in its panel")

    def holt_new_pass(self, views):
        new = {v.name for v in views if v.variant is None and v.pc.rule == "holt:new" and v.pc.status == "due"}
        for v in views:
            if v.variant and v.variant != "img" and v.name in new and v.pc.status not in ("due", "not-runnable"):
                v.pc.status, v.pc.rule = "due", "holt:new"
                v.pc.reasons.insert(0, f"its source {v.name} is new: every applicable variant runs")

    # --- stic classes (1.6.2, R6) ---

    def last_run(self, v):
        return next((r for r in v.rows if r.get("kind") == "run" and pass_like(r)), None)

    def last_run_t(self, v):
        r = next((r for r in v.allrows if r.get("kind") == "run"), None)
        return row_t(r) if r else ""

    def stic_class_pass(self, views):
        by = {v.name: v for v in views}
        for cname, members in self.cls["stic"].get("classes", {}).items():
            mv = [by[m] for m in members if m in by]
            lastrun = {v.name: self.last_run(v) for v in mv}
            need_sets = {}
            for v in mv:
                if lastrun[v.name] is not None:
                    need_sets.setdefault(needs_set(lastrun[v.name]), []).append(v.name)
            split = len(need_sets) > 1
            label = {}
            for i, (ns, names) in enumerate(sorted(need_sets.items(), key=lambda x: sorted(x[1])), 1):
                label[ns] = f"{cname}.{i}" if split else cname
            if split:
                self.notes.append(f"class {cname} splits by need set: " +
                                  "; ".join(f"{label[ns]} {', '.join(sorted(n))}" for ns, n in need_sets.items()))
            cand = [v for v in mv if v.pc.status == "due" and (v.pc.rule or "").startswith("changed:")
                    and v.pc.rule != "changed:unmapped" and "guest" not in v.G and lastrun[v.name] is not None
                    and v.change_set]
            parts = {}
            for v in cand:
                parts.setdefault((v.change_set, needs_set(lastrun[v.name])), []).append(v)
            for (cs, ns), vs in parts.items():
                if len(vs) < 2:
                    continue
                lab = label.get(ns, cname)
                rep = min(vs, key=lambda v: (self.last_run_t(v), v.cell))
                others = [v for v in vs if v is not rep]
                rep.pc.rule, rep.pc.klass = f"class:{lab}", lab
                rep.pc.reasons.append(f"stalest of {lab} ({', '.join(v.name for v in vs)})")
                for v in others:
                    v.pc.status, v.pc.rule, v.pc.klass, v.pc.rep = "class-pending", f"class:{lab}", lab, rep.cell
                    v.pc.reasons = [f"{lab}, covered if {rep.cell} passes"] + v.pc.reasons[:2]
                self.plan.classes.append({"class": lab, "rep": rep.cell, "members": [v.cell for v in others],
                                          "change": sorted(f"{g}:{n}" for g, n, _ in cs)[:20]})

    # --- the word ---

    def fresh_selection(self, cells):
        """{cell: (rule, reasons)} for names, family letters, variant words and All."""
        w, out = self.w, {}
        cs = set(cells)
        if w.all:
            rule = "fresh:Sweep" if w.sweep else "fresh:All"
            return {c: (rule, [f"{w.word}: everything, fresh"]) for c in cells}
        if w.update and w.form == "bare":
            return {c: ("fresh:update", ["GuestTest2 update"]) for c in cells}
        for c in w.names:
            if c in cs:
                out[c] = ("fresh:update", ["GuestTest2 update, named"]) if w.update else ("fresh:names", ["named"])
        ed = self.ctx.options.get("extra_due") or ()
        for c in ed:
            if c in cs:
                why = ed[c] if isinstance(ed, dict) else "a carried binary's canary"
                out.setdefault(c, ("canary:carried", [why]))
        if self.fam == "holt":
            h = self.cls["holt"]
            for x in w.letters:
                for g in h["families"].get(x, ()):
                    c = f"holt:{g}"
                    if c in cs:
                        out.setdefault(c, (f"fresh:family:{x}", [f"HoltTest2{x}"]))
                for c in (f"holt:{n}" for n in h.get("family_variants", {}).get(x, ())):
                    if c in cs:
                        out.setdefault(c, (f"fresh:family:{x}", [f"HoltTest2{x}'s variant"]))
                if x == "D" and "holt:debian13+oneshot" in cs:
                    self.notes.append("debian13+oneshot runs once (HoltTest1D ran it on each host)")
            variants = dict(h["variants"], img=[c[5:].split("+")[0] for c in self.img_cells()])
            for var in w.variants:
                gs = variants.get(var, ())
                if w.names or w.letters:
                    base = {}   # a guest is selected by any of its cells, variant cells too
                    for c, (r, _) in out.items():
                        base.setdefault(c[5:].split("+")[0], r)
                    for g in gs:
                        if g in base and f"holt:{g}+{var}" in cs:
                            out.setdefault(f"holt:{g}+{var}", (base[g], [f"+{var}"]))
                else:
                    for g in gs:
                        if f"holt:{g}+{var}" in cs:
                            out.setdefault(f"holt:{g}+{var}", (f"fresh:variant:{var}", [f"+{var}"]))
        return out

    def build(self):
        self.verdict_changed = []
        self.plumbing_changed = False
        self.setup_set()
        cells = self.universe()
        self.fresh = self.fresh_selection(cells)
        self.ncells = len(cells)
        if self.w.form == "names" and not self.w.stale:
            # names and family letters run those cells, fresh, and nothing else (the table in
            # "How every Test2 decides"); a variant word alone adds to the bare word (R27)
            cells = [c for c in cells if c in self.fresh]
        views = [self.view(c) for c in cells]
        for v in views:
            self.classify(v)
        if self.fam == "holt":
            self.holt_rescuer_pass(views)
            self.holt_new_pass(views)
        if self.fam == "stic":
            self.stic_class_pass(views)
        if self.fam == "guest":
            self.guest_try_pass(views)
            self.guest_update_pass(views)
        if self.fam in ("guest", "stic", "holt"):
            self.guest_notes(views)
        if self.fam == "img":
            self.img_refuse_pass(views)     # a refused image is no reason to run a soft-stale one beside it
        self.stale_pass(views)
        if self.fam == "img":
            self.img_refuse_pass(views)
        if self.fam == "img" and (self.w.release or self.w.form == "names"):
            for v in views:     # an image the word names, or that release must vouch for, and can't run
                if v.pc.status == "not-runnable":
                    v.pc.refused = True
        if self.w.form == "names" and self.w.stale:   # the named cells, fresh, and every soft-stale cell
            views = [v for v in views if v.cell in self.fresh or v.pc.rule == "stale"]
        for v in views:
            if v.pc.status in ("due",) and not v.res:
                v.pc.status = "not-runnable"
            if v.pc.status == "rescuer?":
                v.pc.status = "soft-stale"
        running = self.ctx.options.get("running") or {}
        for v in views:
            if v.cell in running:
                v.pc.running = running[v.cell]
        if self.verdict_changed:
            self.notes.append("verdict changed at an unchanged key: " + ", ".join(self.verdict_changed))
        differ = []
        for v in views:
            for res in v.res.values():
                for n in getattr(res, "notes", ()):
                    if (n.startswith(("guest identity unknown", "BIOS rescuer", "rescuer identity unknown", "no section "))
                            or n.endswith("test image isn't pinned yet: its identity is unknown")) and n not in self.notes:
                        self.notes.append(n)
            if v.pc.differ and v.pc.host == "any":
                differ.append(f"{v.cell} ({', '.join(v.pc.differ[:4])})")
        if differ:
            self.notes.append("inputs differ between hosts (a row counts only for the host whose key it matches): "
                              + "; ".join(differ[:10]) + (f"; +{len(differ) - 10} more" if len(differ) > 10 else ""))
        self.plan.cells = [v.pc for v in views]
        self.estimate(views)
        self.header()
        return self.plan

    def stale_pass(self, views):
        w = self.w
        if w.stale:
            for v in views:
                if v.pc.status == "soft-stale":
                    v.pc.status, v.pc.rule = "due", "stale"
        elif w.form == "bare" and any(v.pc.status == "due" for v in views):
            pool = [v for v in views if v.pc.status in ("soft-stale", "canary-verified") and v.res]
            pool.sort(key=lambda v: (self.last_run_t(v), v.cell))
            for v in pool[:int(self.policy.get("beside", 2))]:
                v.pc.status, v.pc.rule = "due", "stale:beside"
                v.pc.reasons.insert(0, "the stalest soft-stale cells run beside a bare run")

    # --- estimates and the header ---

    def estimate(self, views):
        ctx = self.ctx
        rows = {c: sorted(rs, key=lambda r: (row_t(r), r.get("id", ""))) for c, rs in self.rows_by_cell.items()}
        tsv = estimates.load_tsv(estimates.default_tsv_path(ctx.checkout))
        readme = estimates.readme_estimates(ctx.drive) if self.fam == "stic" else {}
        est = estimates.Estimator(rows, tsv, readme)
        for v in views:
            v.pc.est = est.cell(v.cell)
        due = [v for v in views if v.pc.status == "due"]
        gb = {h: 0.0 for h in ctx.hosts}
        wall = 0
        if self.fam == "stic":
            have = {h: self.have(h) for h in ctx.hosts if h in ctx.facts}
            if self.pending:
                have = {h: {f"{o}/{n}" for o in ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "omnios", "haiku")
                            for n in ("restic-bko", "rustic-bko")} for h in have}
            first = [c[5:] for c in self.canaries("stic")] if self.plumbing_changed else ()
            wall, place, left = estimates.stic_wall(ctx.drive, [v.name for v in due],
                                                    {v.name: v.pc.est["wall_s"] for v in due}, have, first)
            for v in due:
                v.pc.placed = place.get(v.name)
        elif self.fam in ("guest", "img"):
            reach = [h for h in ctx.hosts if h in ctx.facts]
            first = reach[:1] if self.w.update else reach
            jobs = [(v.cell, v.pc.est["wall_s"], self.job_gib(v), [x for x in v.hosts if x in first] or None) for v in due]
            wall, place, left = estimates.pack(jobs, first, slots=2 if self.w.update else estimates.HOLT_JOBS)
            for v in due:
                v.pc.placed = place.get(v.cell)
        elif self.fam == "holt":
            sizes = estimates.load_ram_sizes(os.path.join(ctx.checkout, "testvm", "holttest1d", "ram-sizes.tsv"))
            jobs = [(v.cell, v.pc.est["wall_s"], sizes.get(v.cell[5:], sizes.get(v.name, 6)),
                     v.hosts or None) for v in due]
            wall, place, left = estimates.pack(jobs, [h for h in ctx.hosts if h in ctx.facts])
            for v in due:
                v.pc.placed = place.get(v.cell)
        else:
            part, host_of = {}, {}
            sf = ctx.specs.get("gate")
            for v in views:
                e = {}
                if sf is not None:
                    try:
                        e = sf.entry(v.name)
                    except KeyError:
                        e = {}
                v.pc.part = e.get("part") or ("slow" if v.name.startswith("loop:") else "fast")
                part[v.cell] = v.pc.part
                host_of[v.cell] = v.hosts[0] if v.hosts else v.pc.host
                v.pc.placed = host_of[v.cell] if v.pc.status == "due" else None
            wall = estimates.gate_wall([v.cell for v in due], {v.cell: v.pc.est for v in due}, part, host_of)
        for v in due:
            h = v.pc.placed or (v.hosts[0] if v.hosts else None)
            if h in gb:
                gb[h] += v.pc.est.get("gb", 0.0)
        self.plan.totals = {"due": len(due), "gm": round(sum(v.pc.est["gm"] for v in due), 1), "wall_s": int(wall),
                            "gb": {h: round(x, 2) for h, x in gb.items()},
                            "counts": {s: sum(1 for v in views if v.pc.status == s) for s in STATUSES},
                            "cells": getattr(self, "ncells", len(views))}

    def job_gib(self, v):
        if v.family == "img":
            return 1
        run = (self.policy.get("guest") or {}).get("run") or {}
        return float(self.guest_entry(v.name).get("ram_gib") or run.get("ram_gib_first", 8))

    def header(self):
        from . import report
        ctx = self.ctx
        dirty = (ctx.copy_preview or {}).get("dirty") or {}
        pulled = ctx.options.get("pulled") or {}
        self.plan.header = {
            "base": (ctx.head or "")[:7], "uncommitted": len(dirty), "copy": (ctx.copy_preview or {}).get("id"),
            "ledger_rows": len(ctx.ledger),
            "pulled": {h: pulled.get(h) for h in list(ctx.hosts) + [x for x in pulled if x not in ctx.hosts]},
            "facts": {h: (ctx.facts.get(h) or {}).get("t") for h in ctx.hosts if h in ctx.facts},
            "unreachable": [h for h in ctx.hosts if h not in ctx.facts],
            "sweep": report.sweep_line(self.policy, ctx.options.get("last_sweep"), ctx.now),
        }
        if self.fam == "img" and self.plan.due():
            est = self.estimator()
            cells = [f"holt:{g}+img" for c in self.plan.due() for g in self.img_panel().get(c.cell[4:], {}).get("guests", ())]
            gm = sum(est.cell(c)["gm"] for c in cells)
            must = self.w.release or self.w.all
            self.plan.header["panel"] = (f"panel: {len(cells)} cells, {gm:.1f} gm" if must else
                                         f"panel: decided after the boot tests' manifests (at most {gm:.1f} gm)")
        for h in self.plan.header["unreachable"]:
            self.notes.append(f"{h} is unreachable: its cells use its last ledger mirror; nothing runs there")
        for h, p in sorted(pulled.items()):
            if h not in ctx.hosts and isinstance(p, dict) and p.get("stale"):
                self.notes.append(f"{h}'s ledger mirror wasn't refreshed (its pull failed): its rows are "
                                  f"as of {p.get('t') or 'its last pull'}")


def make(word, ctx):
    """The plan for WORD (a words.Word) in CTX.  Writes nothing."""
    return Planner(word, ctx).build()


def known_names(ctx):
    """What words.parse checks names against."""
    h = ctx.classes["holt"]
    gate, _ = Planner(words.Word("gate", ""), ctx).gate_entries()
    gl = []
    for n, e in gate:
        if "@" in n:
            gl.append(n)
        elif Planner.gate_per_host(n, e):
            gl += [f"{n}@{x}" for x in DEFAULT_HOSTS]
        else:
            gl.append(n)
    try:
        stic = ctx.drive.all_guests()
    except Exception:   # noqa: BLE001 - a missing drive.py only loses stic names
        stic = []
    variants = dict(h["variants"], img=[g for p in (h.get("img_panel") or {}).values() for g in p.get("guests", ())])
    gs = ctx.specs.get("guest")
    excl = (gs.data.get("excluded") or {}) if gs is not None else {}
    return {"stic": stic,
            "holt": {"sources": sorted({g for f in h["families"].values() for g in f} | set(h.get("no_family", []))),
                     "variants": variants, "families": h["families"], "refused": h.get("excluded", {})},
            "gate": gl,
            "guest": [n for n in gs.names() if n not in excl] if gs is not None else [],
            "guest_refused": excl,
            "img": list(words.PLATS)}


def check_classes(ctx, holt_names=None, notes=None):
    """The class table's check (contract 1.8): every name exists (stic: a check directory; holt:
    a guest in the facts, else in holt/spec.json, else HOLT_NAMES for VM-less tests); the 53
    sources are exactly the families plus devuan6; every variant's guest is a source; the
    canaries, pins, panels and classes name sources or checks; the guest and img tables name
    what their specs have (a spec that isn't there yet is skipped, with a line in NOTES).
    Returns the errors."""
    c = ctx.classes
    errs = []
    try:
        stic = set(ctx.drive.all_guests())
    except Exception as e:   # noqa: BLE001
        return [f"no stic checks: {e}"]
    st = c.get("stic", {})
    members = [g for gs in st.get("classes", {}).values() for g in gs]
    for g in members + list(st.get("alone", [])) + list(st.get("canaries", {})) + list(st.get("non_linux", [])):
        if g not in stic:
            errs.append(f"stic: no check {g}")
    if len(members) != len(set(members)):
        errs.append("stic: a guest in two classes")
    if set(members) & set(st.get("alone", [])):
        errs.append("stic: a guest both in a class and alone")
    nl = set(st.get("non_linux", []))
    if set(members) - nl:
        errs.append(f"stic: Linux guests in a class (decision 1): {', '.join(sorted(set(members) - nl))}")
    h = c.get("holt", {})
    fam = [g for gs in h.get("families", {}).values() for g in gs]
    src = set(fam) | set(h.get("no_family", []))
    if len(fam) != len(set(fam)):
        errs.append("holt: a guest in two families")
    if len(src) != 53:
        errs.append(f"holt: {len(src)} sources, not 53")
    known = set(holt_names or ())
    if not known:
        for f in ctx.facts.values():
            known |= set((f.get("guests") or {}).keys())
    if not known:
        sf = ctx.specs.get("holt")
        if sf is not None:
            known = set(sf.names())
    if known:
        for g in sorted(src - known):
            errs.append(f"holt: source {g} is no guest (facts or holt/spec.json)")
    for g in h.get("excluded", {}):
        if g in src:
            errs.append(f"holt: {g} is excluded and a source")
    for var, gs in h.get("variants", {}).items():
        for g in gs:
            if g not in src:
                errs.append(f"holt: {var} variant of {g}, which is no source")
    for x, cells in h.get("family_variants", {}).items():
        for cell in cells:
            g, _, var = cell.partition("+")
            if g not in h.get("families", {}).get(x, ()) or g not in h.get("variants", {}).get(var, ()):
                errs.append(f"holt: family_variants {x}: {cell} isn't a variant of that family's guest")
    for g in list(h.get("canaries", [])) + list(h.get("pins", {})) + list(h.get("uefi", [])):
        if g not in src:
            errs.append(f"holt: {g} (canary, pin or uefi) is no source")
    for pk, p in h.get("rescuer_panel", {}).items():
        for g in list(p.get("guests", [])) + list(p.get("stalest_of", [])) + list(p.get("plus", [])):
            if g not in src:
                errs.append(f"holt: rescuer panel {pk}: {g} is no source")
    for pk, gs in h.get("rustic_panel", {}).items():
        for g in gs:
            if g not in src:
                errs.append(f"holt: rustic panel {pk}: {g} is no source")
    hsf = ctx.specs.get("holt")
    for pk, e in h.get("img_panel", {}).items():
        if pk not in words.PLATS:
            errs.append(f"holt: img_panel {pk}: no such platform")
        for g in e.get("guests", ()):
            if g not in src:
                errs.append(f"holt: img_panel {pk}: {g} is no source")
                continue
            try:
                plat = (hsf.entry(g).get("plat") or "linux") if hsf is not None else pk
            except KeyError:
                continue
            if plat != pk:
                errs.append(f"holt: img_panel {pk}: {g}'s platform is {plat}")
    sf = ctx.specs.get("holt")
    if sf is not None:
        spec_uefi = set()
        for g in sf.names():
            try:
                e = sf.entry(g)
            except KeyError:
                continue
            if e.get("firmware") == "uefi" and e.get("kind") in ("linux", "offline"):
                spec_uefi.add(g)
        if spec_uefi and spec_uefi != set(h.get("uefi", ())):
            errs.append(f"holt.uefi differs from holt/spec.json's UEFI Linux sources: "
                        f"{', '.join(sorted(spec_uefi ^ set(h.get('uefi', ()))))}")
    pol = c.get("policy", {})
    if pol.get("judge_change") not in ("due", "soft-stale"):
        errs.append("policy.judge_change: due or soft-stale")
    for fam_ in ("stic", "holt", "gate", "guest", "img"):
        if fam_ not in (pol.get("watch") or {}):
            errs.append(f"policy.watch has no {fam_}")
    gsf = ctx.specs.get("guest")
    if gsf is None:
        if notes is not None:
            notes.append(f"{keys.SPEC_PATHS['guest']} isn't there yet: guest.canaries not checked")
    else:
        excl = gsf.data.get("excluded") or {}
        for g in c.get("guest", {}).get("canaries", ()):
            if g not in gsf.names() or g in excl:
                errs.append(f"guest: canary {g} is no guest of guest/spec.json")
    if ctx.specs.get("img") is None and notes is not None:
        notes.append(f"{keys.SPEC_PATHS['img']} isn't there yet: not checked")
    return errs
