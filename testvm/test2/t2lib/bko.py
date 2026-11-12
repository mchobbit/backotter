"""BkoTest2 and SweepTest2: the conductor (contract 2.3, 2.4; package K6).

    conduct(word, hosts, checkout, out=print)    t2 BkoTest2 [ROUND] [stale] [plan], t2 SweepTest2 [ROUND] [plan]
    resume(run, hosts, out=print)                t2 resume t2b-X | t2w-X

No new scheduler: every family's VM work already waits in cmp-run.sh's first-come line on each
host.  The conductor runs the family words as children of one run, with one stamp (t2s-S, t2h-S,
t2v-S, t2g-S, t2i-S under t2b-S or t2w-S), one copy, one set of locks and one RunGuards:

  1. .lock-bko and every family's lock, once (the children's lifecycles take none: run.Shared).
  2. One pull, one facts probe (every family's wants); guest.preflight (what a killed job left);
     ctx.classes, specs, src and drive primed.  Each child plans and runs on a copy.copy() of
     that ctx with its own options, facts and rows.  One private copy, when first needed: before
     an identity row is written or a child with due cells starts (a composite with nothing due
     makes none).
  3. Provisional plans of every child.  `plan` prints them and one combined total
     (estimates.pack over every due cell), and stops: nothing is written, nothing runs.
  4. guest.pre_plan (a guest its identity check failed, by its lines, is identity_bad too); then
     guest.identity_check (no hash unless a record differs) for the guests of due stic and holt
     cells and of BkoTest2's carried canaries.  A guest that fails gets its guest:G FAIL row (rule guest:identity,
     under this run's id) and options.identity_bad: its stic and holt cells are not-runnable.
     Facts again when anything was backfilled.
  5. Final plans (the provisional ones when nothing they read changed).  BkoTest2 feeds the
     carried canaries of the img plan to the stic and holt children (options.extra_due), only
     where the child's planned inputs for that cell are the image's digest; otherwise a note.
  6. stic, holt and guest in parallel threads, each run.lifecycle(..., shared=).  Each builds,
     gates and re-plans on its own.  A run-wide stop (harness count, swap-out, write budget)
     trips the shared RunGuards and every runner stops starting; per-family stops stay theirs.
  7. Unless the guards tripped: facts and rows again; the gate child for what is still due,
     less every gate cell a run of this stamp already ran (its universe is the rest; nothing
     left, no child).  A layer a sibling's gate ran, failed or not, doesn't run twice.
  8. BkoTest2 only, unless the guards tripped: the img child (bare), on the rows just written.
  9. The composite report (report.render_composite), the highest exit of the children, and at
     least 1 for an identity failure, a to-class cell or a step a guard skipped.

SweepTest2 is the same with the All forms (GateTest2All, SticTest2All, HoltTest2All,
GuestTest2All), each word.sweep (rule fresh:Sweep, the sweep's budget), and options.gate_fresh:
stic's and holt's gate steps run their layers fresh, and the GateTest2All child takes only the
layers no child ran.  After the last pull: report.verdict_flips over the sweep's rows (a guest
soft input that differs gets tool_guest pkgdiff's package difference) and sweep.json, complete
only when every child ended with no stop of any kind, none was refused, every due cell has a
decisive row and every guest's identity held.  The coverage lines name the guests with no
SticTest check, those in no spec, what wasn't runnable, and (SweepTest2) the soft-stale images
and the age of each image's panel.

MAC_ROOT/runs/t2b-S/bko.json (t2w-S/bko.json) holds the phases, the children, identity_bad,
extra_due, the guards (saved whenever they change, with each family's share of the harness
count) and the copy, for `t2 resume`: children done are skipped; one whose report.txt is there
takes its stops and plan.json's due cells, and its exit is judged from its rows (never 0 by
default); those that began go through run.resume(..., shared=) (SticTest2's share of the
harness count is taken off first: its resume counts its rows again), or, for a family with no
resume() (GateTest2), are set aside with a report.txt once no PID they recorded runs; the
others start.  Every run of the stamp (the img child's panel t2h-S-img too) is the composite's
own for the unfinished-run check, and a fresh run that finds a composite's unfinished child
names `t2 resume` of the composite.

Ctrl-C (SIGTERM, SIGHUP) in steps 6-9 trips the guards (every runner stops starting), ignores
further signals and waits for the children before the locks go; bko.json stays unfinished
(exit 130) and the resume drops that guard.  A child thread's BaseException is its exit 3.

--universe (test only, refused in the default MAC_ROOT; sweep.json records it) is split by
family: a child whose family it doesn't name doesn't run, and its gate cells cut the stic and
holt children's gate steps too (options.gate_universe).
Every I/O goes through an Env (Env is the real one; tests give their own).
"""

import copy as copymod
import json
import os
import re
import signal
import threading
import time
import traceback

from . import DEFAULT_MAC_ROOT, estimates, expand, keys, parse_utc, utc_now, words
from . import mac_root as default_mac_root
from . import plan as planmod
from . import report
from . import run as runmod

PARALLEL = ("stic", "holt", "guest")
PLAN_ORDER = {"bko": ("gate", "stic", "holt", "guest", "img"), "sweep": ("gate", "stic", "holt", "guest")}
REPORT_ORDER = {"bko": ("stic", "holt", "guest", "gate", "img"), "sweep": ("stic", "holt", "guest", "gate")}
ROUND_FAMILIES = ("stic", "gate")
DECISIVE = ("PASS", "FAIL", "FLAKY-FAIL")
PHASES = ("start", "plans", "identity", "children", "gate", "img", "report", "done")
JOB_GIB = 4          # estimates.pack's job size where a plan gives none (the golden total's)
KEEP_LINES = 60      # a child's last output lines kept in bko.json
CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_INTERNAL = runmod.EXIT_OK, runmod.EXIT_FAIL, runmod.EXIT_REFUSED, runmod.EXIT_INTERNAL
EXIT_INTERRUPTED = 130     # (t2's own exit for an interrupt)
INTERRUPTED = "interrupted (Ctrl-C or a signal): nothing more starts"
GUARD_SAVE_S = 30          # bko.json again for a guard's bytes at most this often (a stop at once)
IDENTITY_LINE = re.compile(r"guest:([^\s:]+): identity (FAIL|unknown): (.*)\Z")


class Refused(runmod.Refused):
    """The composite can't start (exit 2)."""


class Guards(runmod.RunGuards):
    """The composite's RunGuards: also each family's share of the harness count (by the cell's
    family), and a call (on_change) whenever they change, so that bko.json always holds them.
    On a resume a child that refeeds its first part's rows (SticTest2's refeed) has its share
    taken off first (forget), so that no result is counted twice."""

    def __init__(self, policy=None, sweep=False, update=False, now=None):
        super().__init__(policy, sweep, update, now)
        self.by_family = {}
        self.on_change = None
        self._saved_t = 0.0

    def _changed(self, urgent=True):
        cb = self.on_change
        if cb is None:
            return
        t = time.time()
        if not urgent and t - self._saved_t < GUARD_SAVE_S:
            return
        self._saved_t = t
        try:
            cb()
        except Exception:   # noqa: BLE001 - a record that can't be written stops nothing
            pass

    def add_harness(self, cell=None):
        n = super().add_harness(cell)
        with self._mu:
            f = str(cell or "?").split(":", 1)[0]
            self.by_family[f] = self.by_family.get(f, 0) + 1
        self._changed()
        return n

    def trip(self, why):
        super().trip(why)
        self._changed()

    def feed_host(self, family, host, swap_out_bytes=None, ours_bytes=None):
        why = super().feed_host(family, host, swap_out_bytes, ours_bytes)
        self._changed(urgent=bool(why))
        return why

    def forget(self, family):
        """Take FAMILY's share off the harness count (its runner counts its rows again)."""
        with self._mu:
            n = int(self.by_family.pop(family, 0) or 0)
            self.harness = max(0, self.harness - n)
            return n

    def untrip(self, why):
        with self._mu:
            self.reasons = [r for r in self.reasons if r != why]

    def to_json(self):
        d = super().to_json()
        with self._mu:
            d["by_family"] = dict(self.by_family)
        return d

    @classmethod
    def from_json(cls, doc, policy=None, sweep=False, update=False, now=None):
        g = super(Guards, cls).from_json(doc, policy, sweep, update, now)
        g.by_family = {str(k): int(v or 0) for k, v in ((doc or {}).get("by_family") or {}).items()}
        return g


def default_root(mac_root):
    """True when MAC_ROOT (None: T2_MAC_ROOT's, else the default) is the default root."""
    try:
        return os.path.realpath(mac_root or default_mac_root()) == os.path.realpath(expand(DEFAULT_MAC_ROOT))
    except (OSError, TypeError, ValueError):
        return True


def stamp_of(run):
    """YYYYMMDDTHHMM-XXXX of a run id (any family, attempt or -img suffix), or None."""
    m = re.match(r"t2[a-z]-(\d{8}T\d{4}-[0-9a-f]{4})(?:-img)?(?:-r\d)?\Z", run or "")
    return m.group(1) if m else None


def of_stamp(run, stamp):
    return bool(stamp) and stamp_of(run) == stamp


def cut(text, n=160):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 3] + "..."


def guest_of(cell):
    """The vms/ guest a stic or holt cell runs on."""
    return keys.Cell(cell).name


def facts_digest(facts):
    try:
        return json.dumps(facts or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return None


# --- the world, for the conductor ---------------------------------------------------------------

class Env:
    """What the conductor does to the world: the pull, the probe, plans, the copy, locks and
    the children's lifecycles.  Tests give their own."""

    def __init__(self, checkout, hosts, mac_root=None, host_root=None, log=None):
        self.checkout, self.hosts = checkout, list(hosts)
        self.mac_root, self.host_root = mac_root, host_root
        self.log = log      # (the conductor sets it: a failed pull and the ledger's warnings are said)

    def now(self):
        return time.time()

    def light_ctx(self):
        return planmod.Ctx(self.checkout, hosts=self.hosts, options={})

    def known(self, ctx):
        return planmod.known_names(ctx)

    # pull and facts (t2's full_ctx, for every family at once)
    def mirror_hosts(self):
        from . import ledger
        try:
            names = os.listdir(ledger.ledger_dir())
        except OSError:
            return []
        return sorted(n[:-len(".jsonl")] for n in names
                      if n.endswith(".jsonl") and not n.endswith(".lost.jsonl") and n != "mac.jsonl")

    def pull_rows(self, do_pull=True, log=None):
        """(rows, pulled): every host with a mirror pulled (a stale one would hide its FAILs)."""
        from . import ledger
        recs = {}
        every = list(dict.fromkeys(self.hosts + self.mirror_hosts()))
        if do_pull:
            try:
                recs = ledger.pull(every)
            except Exception as e:   # noqa: BLE001 - a pull never stops a plan
                if log:
                    log(f"t2: pull failed: {e}")
        v = ledger.view()
        if log:
            for w in v.warnings:
                log("t2: ledger: " + w)
        pulled = {}
        for h in every:
            p = (v.pull or {}).get(h) or recs.get(h) or {}
            pulled[h] = {"t": p.get("t"), "rows": p.get("rows"), "ok": p.get("ok"), "stale": not (recs.get(h) or {}).get("ok")}
        return list(v.rows), pulled

    def pull(self):
        return self.pull_rows(log=self.log)[0]

    def want(self, ctx, word):
        sets = ["bko-bin"] + (["round" + word.round] if word.round else [])
        fams = ["stic", "holt", "gate", "guest"] + (["img"] if word.family == "bko" else [])
        want = keys.wants(ctx.specs, fams, sets)
        want["images"] = True      # the img child, the refusal's binaries, the sweep's coverage
        return want

    def probe(self, want, save=True, log=None):
        from . import facts as factsmod
        got = factsmod.probe_all(self.hosts, want, save=save)
        out = {}
        for h in self.hosts:
            f = got.get(h) or {}
            if f.get("ok") is False or not f:
                if log:
                    log(f"t2: {h}: no facts: {f.get('error') or 'no answer'}")
            else:
                out[h] = f
        return out

    def make_ctx(self, word, log=None):
        """Steps 1-2's pull and facts, in parallel; then the fork heads' round's set when no
        ROUND was given (as t2's full_ctx does for a SticTest2 or GateTest2 word)."""
        from . import copies
        lc = self.light_ctx()
        want = self.want(lc, word)
        box = {}

        def p():
            try:
                box["rows"], box["pulled"] = self.pull_rows(log=log)
            except BaseException as e:   # noqa: BLE001 - re-raised below
                box["error"] = e

        th = threading.Thread(target=p)
        th.start()
        facts = self.probe(want, save=not word.plan, log=log)
        th.join()
        if "error" in box:
            e = box["error"]
            raise Refused(f"the ledger can't be read ({type(e).__name__}: {e}): no plan from an empty ledger")
        pv = copies.preview(self.checkout)
        opts = {"pulled": box.get("pulled"), "running": {}, "last_sweep": runmod.last_sweep(self.mac_root)}
        ctx = planmod.Ctx(self.checkout, head=pv.commit, hosts=self.hosts, facts=facts, ledger=box.get("rows") or [],
                          copy_preview={"id": pv.id, "dirty": pv.dirty, "commit": pv.commit}, options=opts)
        if not word.round:
            sw = words.Word("stic", "SticTest2")
            heads = planmod.Planner(sw, ctx).fork_heads()
            names = set()
            for f in facts.values():
                for n, e in (f.get("rounds") or {}).items():
                    if planmod.round_commits(e) == heads:
                        names.add("round" + n)
            need = sorted(n for n in names if any(n not in (f.get("sets") or {}) for f in facts.values()))
            if need:
                want["sets"] = sorted(set(want["sets"]) | set(need))
                ctx.facts.update(self.probe(want, save=not word.plan, log=log))
        ctx.options["want"] = want
        return ctx

    def refresh_facts(self, ctx, log=None):
        """Facts again, with the run's wants; a host that doesn't answer keeps its last facts."""
        got = self.probe(ctx.options.get("want") or self.want(ctx, words.Word("bko", "BkoTest2")), save=True, log=log)
        return dict(ctx.facts, **got)

    def plan(self, word, ctx):
        return planmod.make(word, ctx)

    def preview(self):
        from . import copies
        pv = copies.preview(self.checkout)
        return {"id": pv.id, "dirty": pv.dirty, "commit": pv.commit}

    def make_copy(self, hosts, preview, log=None):
        """{host: {id, path, commit, dirty}}: the one private copy every child uses."""
        from . import copies
        made = copies.make(self.checkout, hosts, log=log)
        return {h: {"id": c.id, "path": c.path, "commit": preview.get("commit"), "dirty": preview.get("dirty")}
                for h, c in made.items()}

    # the run's guard rails, as lifecycle's
    def locks(self, names, run):
        return runmod.acquire_locks(names, run, self.mac_root)

    def unfinished(self, family, exclude=()):
        return runmod.unfinished_runs(family, self.mac_root, exclude=exclude)

    def leftovers(self, live, out):
        lefts = runmod.our_leftovers(self.mac_root, live)
        if lefts:
            runmod.remove_leftovers(lefts, out)

    def held_runs(self):
        return runmod.held_runs(self.mac_root)

    def running(self, family):
        return runmod.running_cells(family, self.mac_root)

    def preflight(self, facts, hosts):
        return runmod.preflight(facts, hosts)

    def runner(self, family):
        return runmod.runner(family)

    def set_aside_leftovers(self, run, out):
        """Before a report.txt sets dead RUN aside: (host, pid) of a PID its made.json recorded that
        still runs or can't be checked (then nothing is removed), else None, and what it made is
        removed (run.py's PID-checked removal)."""
        lefts = [x for x in runmod.our_leftovers(self.mac_root, live=()) if x[0] == run]
        live = next(((h, pid) for _, h, _, pid in lefts if pid and not runmod.pid_dead(h, pid)), None)
        if live:
            return live
        if lefts:
            runmod.remove_leftovers(lefts, out, dead=lambda h, p: True)    # (just checked)
        return None

    # the children
    def lifecycle(self, word, ctx, shared, out):
        return runmod.lifecycle(word, ctx, mac_root=self.mac_root, host_root=self.host_root, out=out,
                                pull=self.pull, shared=shared)

    def resume(self, run, ctx, shared, out):
        return runmod.resume(run, ctx, mac_root=self.mac_root, out=out, pull=self.pull, shared=shared)

    def identity(self, ctx, guests, run, log):
        g = self.runner("guest")
        if g is None or not hasattr(g, "identity_check"):
            return {}
        return g.identity_check(ctx, guests, sha256=False, run=run, log=log)

    def pkgdiff(self, guest, gen_a, gen_b):
        """tool_guest pkgdiff on Chopin (the dumps never leave it): {added, removed, changed} or None."""
        try:
            from . import guest as gmod, hostjob
            if gmod.FIRST_HOST not in self.hosts:
                return None
            rc, text = gmod.remote_tool(hostjob.HostIO(), gmod.FIRST_HOST, ["pkgdiff", guest, gen_a, gen_b], timeout=120)
            return json.loads((text or "").strip().splitlines()[-1]) if rc == 0 else None
        except Exception:   # noqa: BLE001 - the flip's line stands without it
            return None


class Out:
    """A child's out(): every line kept; single lines also said, tagged (a multi-line text is
    the child's report, which the composite prints)."""

    def __init__(self, say, tag, live=True):
        self.say, self.tag, self.live = say, tag, live
        self.lines = []
        self.mu = threading.Lock()

    def __call__(self, text=""):
        text = str(text)
        with self.mu:
            self.lines.append(text)
        if self.live and "\n" not in text:
            self.say(f"[{self.tag}] {text}")


# --- small pieces, testable alone ---------------------------------------------------------------

def child_text(word, fam):
    """The child word's text: the All forms for a sweep; ROUND for stic and gate; stale passed on."""
    bits = [words.FAMILY_WORD[fam] + ("All" if word.sweep else "")]
    if word.round and fam in ROUND_FAMILIES:
        bits.append(word.round)
    if word.stale and not word.sweep:
        bits.append("stale")
    return " ".join(bits)


def child_word(word, fam, known):
    w = words.parse(child_text(word, fam).split(), known)
    w.sweep = bool(word.sweep)
    return w


def split_universe(universe, family):
    """{fam: [cell]} of a test --universe, or None.  A cell of no child family refuses."""
    if not universe:
        return None
    fams = PLAN_ORDER[family]
    out = {f: [] for f in fams}
    bad = []
    for c in universe:
        f = str(c).split(":", 1)[0]
        if f in out:
            if c not in out[f]:
                out[f].append(c)
        else:
            bad.append(c)
    if bad:
        raise Refused(f"--universe names no cell of {words.COMPOSITE_WORD[family]}'s children: {', '.join(bad)}"
                      + (" (SweepTest2 has no ImgTest2 child)" if family == "sweep" and any(str(b).startswith("img:") for b in bad) else ""))
    return out


def combined_line(plans, hosts):
    """One total over every child's due cells: estimates.pack places them on the hosts.  The gm
    is the sum of the plans' own totals (what each prints above it)."""
    jobs, gm, gbs = [], 0.0, 0.0
    for p in plans:
        gm += float((p.totals or {}).get("gm") or 0)
        for c in p.due():
            est = c.est or {}
            allowed = sorted(h for h in (c.keys or {}) if h != "mac") or None   # (the Mac's cells take no host)
            jobs.append((c.cell, int(est.get("wall_s") or 0), float(est.get("d_gib") or JOB_GIB), allowed))
            gbs += float(est.get("gb") or 0)
    wall, _, left = estimates.pack(sorted(jobs), list(hosts))
    return (f"combined: {len(jobs)} due, {gm:.1f} gm, {report.fmt_min(wall)} wall, {gbs:.1f} GB"
            + (f"; no host takes {', '.join(left)}" if left else ""))


def planned_values(pc):
    """{host: inputs.bin} a plan cell would run with (predicted for a set not built yet)."""
    out = {}
    for h, inp in (getattr(pc, "inp", None) or {}).items():
        out[h] = dict((inp or {}).get("bin") or {})
    for h, res in (getattr(pc, "res", None) or {}).items():
        if h not in out and getattr(res, "inputs", None):
            out[h] = dict((res.inputs or {}).get("bin") or {})
    return out


def carried_canaries(img_plan, child_plans, holt_code_of=None):
    """BkoTest2 step 5: ({fam: {cell: why}}, [note]).  For each image a binary keeps from running
    with only a carried PASS (carried-other) whose canary is a stic or holt cell: that cell is due
    (canary:carried) only when the child's planned inputs for it show the image's digest (for
    bko-holt: its code at the image's bko-holt.commit, or the same sha256).  Otherwise the image
    needs a rebuild, and a note says so."""
    extra, notes = {}, []
    if img_plan is None:
        return extra, notes
    for pc in img_plan.cells:
        plat = pc.cell.split(":", 1)[1]
        for name, b in sorted((getattr(pc, "binaries", None) or {}).items()):
            if not isinstance(b, dict) or b.get("state") != "carried-other" or not b.get("canary"):
                continue
            cell, hexd = b["canary"], b.get("digest") or ""
            fam = cell.split(":", 1)[0]
            p = child_plans.get(fam)
            cpc = p.cell(cell) if p is not None else None
            if cpc is None:
                notes.append(f"{pc.cell}: its {name}'s canary {cell} isn't in this run")
                continue
            osn = "omnios" if plat == "illumos" and fam == "stic" else plat
            bname = f"{osn}/{name}"
            want = {"sha256:" + hexd}
            if name == "bko-holt" and holt_code_of is not None:
                code = holt_code_of(plat)
                if code:
                    want.add("code:" + code)
            seen = {h: v.get(bname) for h, v in planned_values(cpc).items()}
            if hexd and any(v in want for v in seen.values()):
                extra.setdefault(fam, {})[cell] = f"the canary of {pc.cell}'s {name} {hexd[:12]} (only a carried PASS)"
            else:
                notes.append(f"{pc.cell} needs a rebuild: its {name} isn't what this run tests")
    return extra, notes


# plan.place's reasons for a cell that a host's absence keeps from running (not in --hosts, no facts)
HOST_GONE = re.compile(r"not in --hosts|unreachable|no host in --hosts is reachable|on no reachable host")


def run_rows(rows, runs):
    """The rows of RUNS (their attempts too)."""
    base = set(runs)
    return [r for r in rows or () if report._base_run(r.get("run")) in base]


def decisive_cells(rows, stamp):
    """{cell} with a decisive row from a run of STAMP: a valid run row PASS, FAIL or FLAKY-FAIL,
    or a valid derived row (an invalid row is one the planner won't count either)."""
    out = set()
    for r in rows or ():
        if not of_stamp(r.get("run"), stamp) or r.get("valid") is not True:
            continue
        if (r.get("kind") == "run" and r.get("verdict") in DECISIVE) or r.get("kind") in ("carried", "class"):
            out.add(r.get("cell"))
    return out


def judged_exit(st, mine):
    """The exit of a child whose own wasn't recorded (it ended, or was finished by hand, before
    the composite could record it), from its record and MINE (its runs' rows), as run.finish
    judges: 1 for any stop, any run row that isn't PASS, or a due cell whose newest decisive row
    isn't a valid PASS (or a valid derived row); due cells not known: 1.  Never 0 by default."""
    if st.get("stops"):
        return EXIT_FAIL
    if any(r.get("kind") == "run" and r.get("verdict") != "PASS" for r in mine):
        return EXIT_FAIL
    due = st.get("due_cells")
    if due is None:
        return EXIT_FAIL
    for c in due:
        cand = [r for r in mine if r.get("cell") == c and
                ((r.get("kind") == "run" and r.get("verdict") in DECISIVE) or r.get("kind") in ("carried", "class"))]
        if not cand:
            return EXIT_FAIL
        r = max(cand, key=lambda x: (x.get("t") or "", x.get("id") or ""))
        if r.get("valid") is not True or (r.get("kind") == "run" and r.get("verdict") != "PASS"):
            return EXIT_FAIL
    return EXIT_OK


def ran_cells(rows, stamp, prefix=""):
    """{cell} a run of STAMP has a run row for (any verdict)."""
    return {r.get("cell") for r in rows or () if r.get("kind") == "run" and of_stamp(r.get("run"), stamp)
            and str(r.get("cell") or "").startswith(prefix)}


def invalid_passes(rows):
    """ROWS' run rows that say PASS but are invalid (a PASS nothing measured is no PASS)."""
    return [r for r in rows or () if r.get("kind") == "run" and r.get("verdict") == "PASS" and r.get("valid") is not True]


def done_cells(rows, stamp, prefix=""):
    """{cell} this stamp needn't run again: a valid decisive row of STAMP, or a run row of STAMP
    that didn't pass (a layer that failed in a sibling's gate isn't run twice).  A cell whose
    only rows of STAMP are invalid PASSes isn't done: nothing measured it."""
    mine = [r for r in rows or () if str(r.get("cell") or "").startswith(prefix)]
    out = {c for c in decisive_cells(mine, stamp)}
    out |= {r.get("cell") for r in mine if r.get("kind") == "run" and of_stamp(r.get("run"), stamp) and r.get("verdict") != "PASS"}
    return out


def report_stops(path):
    """The stops a child's report.txt names ("stops: a; b"), or []."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("stops: "):
                    s = line[len("stops: "):].strip()
                    return [] if s == "none" else [x.strip() for x in s.split("; ") if x.strip()]
    except OSError:
        pass
    return []


def pkg_text(guest, a, b, d):
    if not isinstance(d, dict):
        return f"packages of {guest} {a[:12]} -> {b[:12]}: not known (tool_guest pkgdiff gave nothing: no dumps of both on chopin)"
    bits = []
    for k in ("added", "removed", "changed"):
        xs = d.get(k) or []
        if xs:
            bits.append(f"{k} {len(xs)} ({', '.join(str(x) for x in xs[:6])}{', ...' if len(xs) > 6 else ''})")
    return f"packages of {guest} {a[:12]} -> {b[:12]}: " + ("; ".join(bits) or "no difference")


# --- the conductor --------------------------------------------------------------------------------

class Conductor:
    def __init__(self, word, hosts, checkout=None, out=print, env=None):
        self.word, self.say = word, out
        self.fam = word.family          # bko or sweep
        if self.fam not in PLAN_ORDER:
            raise Refused(f"{word.word} isn't BkoTest2 or SweepTest2")
        self.checkout = checkout or CHECKOUT
        self.env = env or Env(self.checkout, hosts)
        self.hosts = list(self.env.hosts)
        self.mu = threading.RLock()
        self.copy_mu = threading.Lock()
        self.doc = {}
        self.locks = []
        self.ctx = None
        self.guards = None
        self.universe = split_universe(getattr(word, "universe", None), self.fam)
        self.words = {}
        self.cctx = {}      # {fam: the child's ctx, after it ran}
        self.final = {}     # {fam: the plan the child got}
        self.notes = []
        self.resumed = self._copy_ok = False
        self.threads = []
        self._sigs = {}
        if getattr(self.env, "log", "unset") is None:
            self.env.log = self.envlog

    # -- records --
    @property
    def mac_root(self):
        return self.env.mac_root

    def rdir(self, run=None):
        return runmod.run_dir(run or self.run, self.mac_root)

    def save(self):
        with self.mu:
            self.doc["guards"] = self.guards.to_json() if self.guards is not None else None
            runmod.write_json(os.path.join(self.rdir(), "bko.json"), self.doc)

    def phase(self, name):
        with self.mu:
            self.doc["phase"] = name
            self.doc.setdefault("phases", {})[name] = utc_now()
        self.save()

    def past(self, name):
        return PHASES.index(self.doc.get("phase") or "start") > PHASES.index(name)

    def child(self, fam):
        with self.mu:
            return self.doc.setdefault("children", {}).setdefault(fam, {"state": "unstarted"})

    def set_child(self, fam, **kw):
        with self.mu:
            self.child(fam).update(kw)
        self.save()

    def envlog(self, text):
        """The Env's log (a pull that failed, the ledger's warnings): run.log once there is a run."""
        if getattr(self, "run", None):
            self.log(text)
        else:
            self.say(text)

    def log(self, text):
        self.say(text)
        try:
            with open(os.path.join(self.rdir(), "run.log"), "a", encoding="utf-8") as f:
                f.write(f"{utc_now()} {text}\n")
        except OSError:
            pass

    # -- the children's words and ctxs --
    def families(self):
        return [f for f in PLAN_ORDER[self.fam] if self.universe is None or self.universe.get(f)]

    def child_ctx(self, fam, plan_cells=None):
        """A copy of the run's ctx for FAM: its own options, facts and rows."""
        c = copymod.copy(self.ctx)
        c.options = dict(self.ctx.options)
        c.facts = dict(self.ctx.facts)
        c.ledger = list(self.ctx.ledger)
        c.run = None
        for k in ("universe", "gate_universe", "extra_due", "identity_bad", "gate_fresh", "pull", "run"):
            c.options.pop(k, None)
        c.options["running"] = self.env.running(fam) if self.word.plan else {}   # (a run holds the locks itself)
        uni = plan_cells if plan_cells is not None else (self.universe or {}).get(fam)
        if uni:
            c.options["universe"] = list(uni)
        if self.universe is not None and fam in ("stic", "holt"):
            c.options["gate_universe"] = list(self.universe.get("gate") or [])
        if self.word.sweep and fam in ("stic", "holt"):
            c.options["gate_fresh"] = True
        bad = self.doc.get("identity_bad") or {}
        if bad and fam in ("stic", "holt"):
            c.options["identity_bad"] = dict(bad)
        ed = (self.doc.get("extra_due") or {}).get(fam)
        if ed:
            c.options["extra_due"] = dict(ed)
        return c

    def shared(self, plan=None):
        return runmod.Shared(self.stamp, self.doc.get("copy") or {}, self.guards, plan=plan)

    def set_lock(self, fam, run):
        """The family's lock names the child that runs now (other sessions' plans read it)."""
        for lk in self.locks:
            if lk.name == fam and lk.fd is not None:
                runmod.write_json(lk.info, {"run": run, "pid": os.getpid(), "start": utc_now(), "composite": self.run})

    # -- the whole --
    def conduct(self):
        w = self.word
        try:
            known = self.env.known(self.env.light_ctx())
            for f in self.families():
                self.words[f] = child_word(w, f, known)
        except (words.Refused, Refused) as e:
            self.say(f"t2: {e}")
            return EXIT_REFUSED
        if w.plan:
            return self.plan_only()
        if self.universe is not None and default_root(self.mac_root):
            # (a cut composite would write rows, and a cut sweep a sweep.json, into the real records)
            self.say(f"t2: --universe is test-only: {w.word} takes it only with T2_MAC_ROOT set to a test root "
                     "(not the default)")
            return EXIT_REFUSED
        for _ in range(20):     # a stamp no run of this root has (a minute and 2 random bytes)
            self.stamp = runmod.new_stamp()
            if not self.stamp_runs(self.stamp):
                break
        else:
            self.say("t2: no free run stamp this minute: try again")
            return EXIT_REFUSED
        self.run = runmod.new_run_id(self.fam, stamp=self.stamp)
        return self.go()

    def stamp_runs(self, stamp):
        """[run] of this root whose id carries STAMP (any family, attempt or panel)."""
        try:
            names = os.listdir(runmod.runs_dir(self.mac_root))
        except OSError:
            return []
        return sorted(n for n in names if runmod.RUN_RE.match(n) and of_stamp(n, stamp))

    def plan_only(self):
        """Step 3's `plan`: every child's provisional plan, then one total.  Writes nothing."""
        try:
            self.ctx = self.env.make_ctx(self.word, log=self.say)
            plans = self.provisional()
        except (planmod.PlanError, Refused) as e:
            self.say(f"t2: {e}")
            return EXIT_REFUSED
        for f in self.families():   # (each without its own sweep line: one, last)
            self.say("\n".join(x for x in plans[f].render().rstrip().split("\n") if not x.startswith("sweep: SweepTest2")))
        self.say(combined_line([plans[f] for f in self.families()], self.hosts))
        self.say(report.sweep_line(self.ctx.classes.get("policy"), (self.ctx.options or {}).get("last_sweep"), self.ctx.now))
        return EXIT_OK

    def provisional(self):
        plans = {}
        for f in self.families():
            plans[f] = self.env.plan(self.words[f], self.child_ctx(f))
        return plans

    def start_guard_rails(self, own=()):
        """Locks, unfinished runs, leftovers (lifecycle's, for every family at once).  A refusal text or None."""
        try:
            self.locks = self.env.locks(runmod.family_locks(self.word), self.run)
        except runmod.Busy as e:
            return str(e)
        for f in runmod.RUN_PREFIX:
            busy = self.env.unfinished(f, exclude=tuple(own))
            if busy:
                b = busy[-1]
                comp = self.composite_of(b)
                if comp:    # a composite's child: the composite finishes it, under its locks and guards
                    return (f"run {b} of {f} is unfinished: it is a child of {comp}, which didn't finish (it began "
                            f"host work and has no report): its checks or queue runners may still run on the hosts. "
                            f"`t2 resume {comp}` finishes it (not `t2 resume {b}`, which would run outside "
                            f"{comp}'s locks and run-wide stop rules)")
                return (f"run {b} of {f} is unfinished (it began host work and has no report): its checks or "
                        f"queue runners may still run on the hosts. `t2 resume {b}` finishes it; once nothing of it "
                        f"runs, a report.txt in {runmod.run_dir(b, self.mac_root)} sets it aside")
        live = set(self.env.held_runs()) | {self.run} | set(own)
        live |= set(self.env.unfinished(None, exclude=()))
        self.env.leftovers(live, self.say)
        return None

    def composite_of(self, run):
        """The unfinished BkoTest2 or SweepTest2 run (t2b-S, t2w-S) RUN is a child of, or None."""
        st = stamp_of(run)
        if not st or run.startswith(("t2b-", "t2w-")):
            return None
        for fam in ("bko", "sweep"):
            c = runmod.new_run_id(fam, stamp=st)
            d = runmod.run_dir(c, self.mac_root)
            if os.path.exists(os.path.join(d, "bko.json")) and not os.path.exists(os.path.join(d, "report.txt")):
                return c
        return None

    def release(self):
        for lk in reversed(self.locks):
            lk.release()
        self.locks = []

    # -- signals: an interrupt stops the starts, and the locks stay until every child has ended --
    def catch_signals(self):
        """SIGTERM and SIGHUP as Ctrl-C (KeyboardInterrupt in the main thread), for the run's life."""
        def handler(signum, frame):
            raise KeyboardInterrupt(signal.Signals(signum).name)
        for s in (signal.SIGTERM, signal.SIGHUP):
            try:
                self._sigs.setdefault(s, signal.getsignal(s))
                signal.signal(s, handler)
            except (ValueError, OSError, AttributeError):   # (not the main thread)
                self._sigs.pop(s, None)

    def ignore_signals(self):
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                self._sigs.setdefault(s, signal.getsignal(s))
                signal.signal(s, signal.SIG_IGN)
            except (ValueError, OSError, AttributeError):
                self._sigs.pop(s, None)

    def restore_signals(self):
        for s, h in list(self._sigs.items()):
            try:
                signal.signal(s, h if h is not None else signal.SIG_DFL)
            except (ValueError, OSError, TypeError):
                pass
        self._sigs = {}

    def interrupted(self, why="interrupted"):
        """Ctrl-C (or SIGTERM, SIGHUP) in steps 6-9: every runner stops starting (the guards),
        further signals are ignored, and the composite waits for its children before its locks
        go; bko.json stays unfinished, for `t2 resume`."""
        self.ignore_signals()
        if self.guards is not None:
            self.guards.trip(INTERRUPTED)
        alive = [th for th in self.threads if th.is_alive()]
        self.log(f"t2: {why}: nothing more starts" + (f"; waiting for {', '.join(th.name[4:] for th in alive)} to end "
                                                       "(further signals are ignored; the locks stay held until then)"
                                                       if alive else ""))
        for th in alive:
            th.join()
        with self.mu:
            self.doc["interrupted"] = utc_now()
        self.save()
        self.log(f"t2: {self.run} stopped: `t2 resume {self.run}` goes on from here")
        return EXIT_INTERRUPTED

    def go(self, saved=None):
        """Steps 1-9; SAVED: a bko.json to go on from (t2 resume)."""
        # every run of this stamp is ours: the children, their attempts and the img child's panel t2h-S-img
        own = sorted({self.run} | set(self.stamp_runs(self.stamp))
                     | {c.get("run") for c in ((saved or {}).get("children") or {}).values() if c.get("run")})
        if saved is None:
            self.doc = {"v": 1, "run": self.run, "stamp": self.stamp, "family": self.fam, "word": self.word.to_json(),
                        "text": self.word.text, "hosts": self.hosts, "universe": getattr(self.word, "universe", None),
                        "phase": "start", "phases": {"start": utc_now()}, "children": {}, "identity_bad": {},
                        "extra_due": {}, "copy": None, "notes": [], "start": utc_now(), "t0": self.env.now()}
        else:
            self.doc = saved
        why = self.start_guard_rails(own)
        if why:
            self.release()
            self.say(f"t2: {why}")
            return EXIT_REFUSED
        self.catch_signals()
        try:
            ex = self.under_locks(saved is not None)
            if ex == EXIT_REFUSED:
                self.ended_early("refused before any child began" + (f": {self.doc['error']}" if self.doc.get("error") else ""),
                                 ex, saved)
            return ex
        except BaseException as e:
            # (an error or an interrupt before any child began: finished, so that no later run waits 12 h on it)
            self.ended_early(f"stopped before any child began: {type(e).__name__}: {cut(e, 200)}", EXIT_INTERNAL, saved)
            raise
        finally:
            self.release()
            self.restore_signals()

    def ended_early(self, why, ex, saved):
        """A fresh composite that ended before any child began gets a short report.txt."""
        if saved is not None or self.doc.get("children") or not os.path.exists(os.path.join(self.rdir(), "bko.json")):
            return
        try:
            runmod.write_text(os.path.join(self.rdir(), "report.txt"), f"{self.word.word} report: run {self.run}: {why}\n")
            with self.mu:
                self.doc["exit"] = ex
            self.phase("done")
        except Exception:   # noqa: BLE001 - the error that brought us here is the one to see
            pass

    def under_locks(self, resumed):
        w = self.word
        try:
            self.ctx = self.env.make_ctx(w, log=self.say)
        except Refused as e:
            self.say(f"t2: {e}")
            return EXIT_REFUSED
        bad = self.env.preflight(self.ctx.facts, self.hosts)
        if bad:
            self.say("t2: preflight refuses: " + "; ".join(bad))
            return EXIT_REFUSED
        pol = self.ctx.classes.get("policy")
        self.guards = Guards.from_json(self.doc.get("guards"), pol, sweep=w.sweep, now=self.ctx.now) \
            if resumed and self.doc.get("guards") else Guards(pol, sweep=w.sweep, now=self.ctx.now)
        if resumed:
            self.guards.untrip(INTERRUPTED)     # (the interrupt was the last process's, not a stop rule)
            for f in self.families():
                if f == "stic" and self.will_resume(f):
                    n = self.guards.forget(f)   # its run.resume counts its first part's rows again (refeed)
                    if n:
                        self.log(f"resume: {words.FAMILY_WORD[f]}'s {n} harness-class result(s) are counted again by its resume")
        self.guards.on_change = self.save
        self.resumed = resumed
        self.save()
        if not resumed:     # step 2's guest.preflight (run.resume runs none): what a killed job left
            for f in PLAN_ORDER[self.fam]:
                mod = self.env.runner(f)
                if mod is not None and hasattr(mod, "preflight"):
                    try:
                        mod.preflight(self.ctx, self.say)
                    except Exception as e:   # noqa: BLE001 - housekeeping
                        self.note(f"{f} preflight: {type(e).__name__}: {cut(e, 160)}")
        # prime what the children share (load_drive must not race)
        for attr in ("classes", "src", "specs", "drive"):
            try:
                getattr(self.ctx, attr)
            except Exception as e:   # noqa: BLE001 - a family that needs it says so itself
                self.note(f"ctx.{attr}: {type(e).__name__}: {cut(e, 120)}")
        self.log(f"{w.word} run {self.run}: children {', '.join(self.words[f].word for f in self.families())}")
        try:
            if not self.past("identity"):
                ex = self.plans_and_identity()
                if ex is not None:
                    return ex
            return self.rest(resumed)
        except (planmod.PlanError, Refused) as e:
            self.say(f"t2: {e}")
            self.doc["error"] = str(e)
            self.save()
            return EXIT_REFUSED

    def ensure_copy(self):
        """The one private copy, made when first needed (an identity row, a child with due cells),
        and the runners' preflights with it: a composite with nothing due makes no copy and
        touches no host beyond its probe.  A resume keeps its own copy while the checkout is the
        same tree.  None, or why there is none (a refusal)."""
        with self.copy_mu:
            return self._ensure_copy()

    def _ensure_copy(self):
        if self.doc.get("copy") and self._copy_ok:
            return None
        pv = self.ctx.copy_preview or {}
        saved = self.doc.get("copy") or {}
        if not saved or any((c or {}).get("id") == pv.get("id") for c in saved.values()):
            try:
                self.doc["copy"] = self.env.make_copy([h for h in self.hosts if h in self.ctx.facts], pv, log=self.say)
            except Exception as e:   # noqa: BLE001 - copies.CopyError and the hosts' errors
                if not saved:
                    return f"no private copy: {e}"
                self.note(f"the copy couldn't be made again ({e}): the run goes on with its own")
        else:
            self.note(f"the checkout changed since {self.run} began: it goes on with its own copy "
                      f"{next(iter(saved.values())).get('id')}")
        self._copy_ok = True
        self.save()
        return None

    def note(self, text):
        with self.mu:
            if text not in self.notes:
                self.notes.append(text)
                self.doc.setdefault("notes", []).append(text)

    def plans_and_identity(self):
        """Steps 3-5.  None to go on, else an exit."""
        self.phase("plans")
        prov = self.provisional()
        before = facts_digest(self.ctx.facts)
        nrows = len(self.ctx.ledger)
        self.doc["provisional"] = {f: {"due": p.totals.get("due", 0), "gm": p.totals.get("gm", 0)} for f, p in prov.items()}
        # 4: image sums and moved disks; then identity for the guests of due stic and holt cells
        self.phase("identity")
        g = self.env.runner("guest")
        bad, wrote, failed = {}, False, []
        if g is not None and hasattr(g, "pre_plan"):
            pctx = copymod.copy(self.ctx)          # (the same facts: pre_plan's sums and stats land in them)
            pctx.options = dict(self.ctx.options)
            if self.universe is not None:
                gs = sorted({guest_of(c) for f in ("stic", "holt", "guest") for c in self.universe.get(f) or ()})
                pctx.options["universe"] = [f"guest:{x}" for x in gs]

            def heard(text):
                # pre_plan's identity results reach us only as its lines (guest.identity_check's,
                # and pre_plan's own per guest): a FAIL (its row written) or unknown is identity_bad
                m = IDENTITY_LINE.match(str(text))
                if m:
                    bad.setdefault(m.group(1), ("identity unknown: " if m.group(2) == "unknown" else "") + m.group(3))
                    if m.group(2) == "FAIL":
                        failed.append(m.group(1))
                self.say(text)

            try:
                g.pre_plan(pctx, self.word, heard)
            except Exception as e:   # noqa: BLE001 - the plans say what isn't known
                self.note(f"guest pre_plan: {type(e).__name__}: {cut(e, 160)}")
            wrote = bool(failed)     # (its FAIL rows: the rows again, so that the final plans see them)
        mid = facts_digest(self.ctx.facts)       # pre_plan's sums and stats are in the facts now
        have = {x for f in self.ctx.facts.values() for x in (f.get("guests") or {})}
        # 5's carried canaries first: their guests' identity is checked too (a canary's PASS is what
        # lets ImgTest2 take a carried binary)
        extra = {}
        if self.fam == "bko" and "img" in prov:
            extra, notes = carried_canaries(prov["img"], prov, self.holt_code_of)
            for n in notes:
                self.note(n)
        due_guests = sorted(({guest_of(c.cell) for f in ("stic", "holt") if f in prov for c in prov[f].due()}
                             | {guest_of(c) for f in ("stic", "holt") for c in extra.get(f) or ()}) & have - set(bad))
        if due_guests:
            why = self.ensure_copy()    # (its FAIL rows go through the copy's t2-host append)
            if why:
                self.say(f"t2: {why}")
                return EXIT_REFUSED
            ictx = copymod.copy(self.ctx)
            ictx.run = _IdentityRun(self.word, self.doc.get("copy") or {})
            res = self.env.identity(ictx, due_guests, self.run, self.say) or {}
            for gname, r in sorted(res.items()):
                if not r.get("ok"):
                    bad[gname] = ("identity unknown: " if r.get("harness") else "") + str(r.get("why") or "identity failed")
                    wrote = wrote or bool(r.get("row"))
        self.doc["identity_bad"] = bad
        for gname, why in sorted(bad.items()):
            self.log(f"guest:{gname}: identity: {why}: its stic and holt cells don't run")
        if due_guests and facts_digest(self.ctx.facts) != mid:     # identity backfilled a stat record
            self.ctx.facts = self.env.refresh_facts(self.ctx, log=self.say)
        if wrote:
            self.ctx.ledger = list(self.env.pull())
        # 5: final plans, with BkoTest2's carried canaries
        self.doc["extra_due"] = extra
        same = facts_digest(self.ctx.facts) == before and len(self.ctx.ledger) == nrows
        for f in self.families():
            if same and not (f in ("stic", "holt") and (bad or extra.get(f))):
                self.final[f] = prov[f]
            else:
                self.final[f] = self.env.plan(self.words[f], self.child_ctx(f))
        self.doc["plans"] = {f: {"due": p.totals.get("due", 0), "gm": p.totals.get("gm", 0)} for f, p in self.final.items()}
        self.phase("children")
        return None

    def holt_code_of(self, plat):
        """bko-holt's code at the image's bko-holt.commit, or None."""
        for f in self.ctx.facts.values():
            commit = ((f.get("images") or {}).get(plat) or {}).get("holt_commit")
            if commit:
                try:
                    return keys.holt_code(keys.GitSource(self.checkout, commit, scope=("holt",)))
                except Exception:   # noqa: BLE001 - not in this repository: no match
                    return None
        return None

    # -- steps 6-9 --
    def rest(self, resumed=False):
        try:
            return self._rest(resumed)
        except KeyboardInterrupt as e:
            return self.interrupted(f"interrupted ({e})" if str(e) else "interrupted")

    def _rest(self, resumed=False):
        t0 = self.doc.get("t0") or self.env.now()
        if resumed or any((p.totals or {}).get("due") for p in self.final.values()):
            why = self.ensure_copy()
            if why:
                self.say(f"t2: {why}")
                return EXIT_REFUSED
        par = [f for f in PARALLEL if f in self.families()]
        if resumed and self.past("children"):   # a child that hadn't ended when the composite died
            par = [f for f in par if self.child(f).get("state") not in ("done", "skipped", "not-run")]
            if par:
                self.log(f"resume: {', '.join(words.FAMILY_WORD[f] for f in par)} hadn't ended")
        if self.doc.get("phase") == "children" or (resumed and par):
            ths = [threading.Thread(target=self.run_child_safe, args=(f,), name=f"bko-{f}") for f in par]
            self.threads = ths
            for th in ths:
                th.start()
            for th in ths:
                th.join()       # (a Ctrl-C here: rest() trips the guards and waits for them)
            self.phase("gate")
        if self.doc.get("phase") == "gate":
            if "gate" in self.families():
                self.run_child_safe("gate", self.gate_step)
            self.phase("img" if self.fam == "bko" else "report")
        if self.doc.get("phase") == "img":
            if "img" in self.families():
                self.run_child_safe("img", self.img_step)
            self.phase("report")
        return self.finish(t0)

    def anything_ran(self, fams=PARALLEL):
        """True when a child of FAMS had due cells (it ran something)."""
        return any(((self.doc.get("children") or {}).get(f) or {}).get("due") for f in fams)

    def run_child_safe(self, fam, step=None):
        """STEP (default run_child(FAM)) with its errors made FAM's: a plan that can't be made
        refuses that child (exit 2), anything else is an internal error (3); the others go on."""
        try:
            (step or (lambda: self.run_child(fam)))()
        except KeyboardInterrupt:
            raise       # (the main thread's gate or img step: rest() handles it)
        except (planmod.PlanError, runmod.Refused) as e:
            self.note(f"{words.FAMILY_WORD[fam]}: refused: {cut(e, 200)}")
            self.set_child(fam, state="done", exit=EXIT_REFUSED, end=utc_now(), why=f"refused: {cut(e, 200)}")
        except BaseException as e:   # noqa: BLE001 - the conductor's own error around a child; a SystemExit too
            self.note(f"{words.FAMILY_WORD[fam]}: {type(e).__name__}: {cut(e, 200)}")
            try:
                with open(os.path.join(self.rdir(), "run.log"), "a", encoding="utf-8") as f:
                    f.write(traceback.format_exc())
            except OSError:
                pass
            self.set_child(fam, state="done", exit=EXIT_INTERNAL, end=utc_now(), why=f"{type(e).__name__}: {cut(e, 200)}")

    def run_child(self, fam, plan=None, plan_cells=None):
        """One child: lifecycle (or run.resume, when it began before a crash) with shared=."""
        st = self.child(fam)
        if st.get("state") in ("done", "skipped", "not-run"):
            return
        run = st.get("run") or runmod.new_run_id(fam, stamp=self.stamp)
        began = os.path.exists(os.path.join(self.rdir(run), "plan.json"))
        if os.path.exists(os.path.join(self.rdir(run), "report.txt")):
            # it ended before the composite could record its exit (or was finished by hand): its
            # stops and due cells from its report.txt and plan.json; finish() judges its exit
            # from them and its rows, as run.finish would (never 0 by default)
            self.set_child(fam, state="done", run=run, exit=st.get("exit"),
                           stops=list(st.get("stops") or report_stops(os.path.join(self.rdir(run), "report.txt"))),
                           due_cells=st.get("due_cells") if st.get("due_cells") is not None else self.planned_due(run),
                           note="its report.txt was there")
            return
        if began and st.get("state") == "running":
            mod = self.env.runner(fam)
            if mod is None or not hasattr(mod, "resume"):
                self.set_aside(fam, run)
                return
        cctx = self.child_ctx(fam, plan_cells)
        if began and st.get("state") == "running":
            try:
                pj = runmod.read_json(os.path.join(self.rdir(run), "plan.json"), {}) or {}
                cells = [c["cell"] for c in pj.get("cells") or [] if c.get("cell")]
                if cells:
                    cctx.options["universe"] = cells
            except (OSError, ValueError, KeyError):
                pass
            plan = None
        elif plan is None:
            plan = self.final.get(fam) or self.env.plan(self.words[fam], cctx)
        due = (plan.totals.get("due") if plan is not None else None)
        live = bool(due) or plan is None
        if live:
            why = self.ensure_copy()
            if why:
                raise Refused(why)
        out = Out(self.say, words.FAMILY_WORD[fam], live=live)
        self.set_child(fam, state="running", run=run, word=self.words[fam].text, start=utc_now(),
                       due=due if due is not None else st.get("due"))
        self.set_lock(fam, run)
        if live:
            self.log(f"{self.words[fam].word} {run}: " + (f"{due} due" if due is not None else "resumed"))
        t = self.env.now()
        try:
            if plan is None:
                ex = self.env.resume(run, cctx, self.shared(), out)
            else:
                ex = self.env.lifecycle(self.words[fam], cctx, self.shared(plan), out)
        except Exception as e:   # noqa: BLE001 - one child's crash doesn't take the others down
            out(f"t2: {self.words[fam].word} {run}: {type(e).__name__}: {e}")
            self.note(f"{self.words[fam].word} {run}: {type(e).__name__}: {cut(e, 200)}")
            try:
                with open(os.path.join(self.rdir(), "run.log"), "a", encoding="utf-8") as f:
                    f.write(traceback.format_exc())
            except OSError:
                pass
            ex = EXIT_INTERNAL
        rs = getattr(cctx, "run", None)
        stops = list(getattr(getattr(rs, "stops", None), "stops", None) or [])
        for s in report_stops(os.path.join(self.rdir(run), "report.txt")):
            if s not in stops:
                stops.append(s)
        fp = getattr(rs, "plan", None) or plan
        with self.mu:
            self.cctx[fam] = cctx
            if fp is not None:
                self.final[fam] = fp
        self.set_child(fam, state="done", exit=int(ex if ex is not None else EXIT_INTERNAL), end=utc_now(),
                       wall_s=int(self.env.now() - t), stops=stops, lines=out.lines[-KEEP_LINES:],
                       due_cells=[c.cell for c in fp.due()] if fp is not None else st.get("due_cells") or [],
                       to_class=[c.cell for c in fp.by_status("to-class")] if fp is not None else [])

    def planned_due(self, run):
        """The due cells of RUN's plan.json, or None (no plan.json)."""
        pj = runmod.read_json(os.path.join(self.rdir(run), "plan.json"), None)
        if not isinstance(pj, dict):
            return None
        return [c["cell"] for c in pj.get("cells") or [] if isinstance(c, dict) and c.get("cell") and c.get("status") == "due"]

    def set_aside(self, fam, run):
        """A child that began before the composite died, of a family whose runner has no resume()
        (GateTest2): what it left goes (PID-checked), a report.txt sets it aside, and its due cells
        with no row of this stamp count as not run (the exit is at least 1, the sweep incomplete)."""
        rt = f"t2lib/{runmod.RUNNERS.get(fam, fam)}.py"
        why = f"set aside by `t2 resume {self.run}`: {rt} has no resume(), so the cells it didn't finish don't run in this run"
        try:
            live = self.env.set_aside_leftovers(run, self.say)
        except Exception as e:   # noqa: BLE001 - not known: not set aside
            live = ("?", f"? ({type(e).__name__}: {cut(e, 80)})")
        if live:    # (its host work may go on: it stays unfinished, and this child is refused)
            raise Refused(f"{run} began before {self.run} died and {rt} has no resume(); pid {live[1]} on {live[0]} "
                          f"that it recorded still runs or can't be checked, so it isn't set aside. Once that ends, "
                          f"a report.txt in {self.rdir(run)} sets it aside")
        due = self.planned_due(run)
        runmod.write_text(os.path.join(self.rdir(run), "report.txt"),
                          f"{words.FAMILY_WORD[fam]} report: run {run}: {why} (no PID it recorded still ran)\n"
                          f"stops: {why}\n")
        self.note(f"{words.FAMILY_WORD[fam]} {run}: {why}")
        self.set_child(fam, state="done", run=run, exit=None, stops=[why], due_cells=due, end=utc_now(),
                       note="set aside: no resume()")
        self.log(f"{words.FAMILY_WORD[fam]} {run}: {why}")

    def will_resume(self, fam):
        """True when FAM's child began before the composite died and run.resume will go on with it."""
        st = (self.doc.get("children") or {}).get(fam) or {}
        if not self.began(st) or os.path.exists(os.path.join(self.rdir(st["run"]), "report.txt")):
            return False
        mod = self.env.runner(fam)
        return mod is not None and hasattr(mod, "resume")

    def began(self, st):
        """A child the composite started before it died, that got as far as its plan.json."""
        return st.get("state") == "running" and bool(st.get("run")) and \
            os.path.exists(os.path.join(self.rdir(st["run"]), "plan.json"))

    def skip_guarded(self, fam):
        why = "; ".join(self.guards.why()) or "a run-wide stop rule"
        self.set_child(fam, state="not-run", exit=EXIT_FAIL, why=f"not run: a run-wide stop rule tripped ({why})")
        self.log(f"{self.words[fam].word}: not run: a run-wide stop rule tripped ({why})")

    def refresh(self):
        """Facts and rows again, after the parallel children (skipped when none of them ran)."""
        if not self.anything_ran():
            return
        self.ctx.ledger = list(self.env.pull())
        self.ctx.facts = self.env.refresh_facts(self.ctx, log=self.say)

    def gate_step(self):
        """Step 7: the gate child for what is still due, less what this stamp ran."""
        st = self.child("gate")
        if st.get("state") in ("done", "skipped", "not-run"):
            return
        if self.guards.tripped():
            self.skip_guarded("gate")
            return
        if self.began(st):     # (a resume: it began)
            self.run_child("gate")
            return
        if self.anything_ran() or "gate" not in self.final:
            self.refresh()
            full = self.env.plan(self.words["gate"], self.child_ctx("gate"))
        else:
            full = self.final["gate"]
        due = [c.cell for c in full.due()]
        ran = done_cells(self.ctx.ledger, self.stamp, "gate:")
        rest = [c for c in due if c not in ran]
        again = sorted(c for c in set(due) & ran_cells(self.ctx.ledger, self.stamp, "gate:") if c not in ran)
        if again:
            self.log(f"{self.words['gate'].word}: ran in this run, but no valid row: again: {', '.join(again)}")
        with self.mu:
            self.doc["gate_due"] = due
        if due and not rest:
            self.set_child("gate", state="skipped", exit=EXIT_OK,
                           why=f"every due layer ran in this run's gate steps ({', '.join(sorted(ran & set(due))[:8])})")
            self.log(f"{self.words['gate'].word}: nothing left: every due layer ran in the children's gate steps")
            return
        if ran & set(due):
            self.log(f"{self.words['gate'].word}: left out, ran in this run already: {', '.join(sorted(ran & set(due)))}")
        plan = full if not (ran & set(due)) else self.env.plan(self.words["gate"], self.child_ctx("gate", rest))
        self.run_child("gate", plan=plan, plan_cells=rest if ran & set(due) else None)

    def img_step(self):
        """Step 8 (BkoTest2): the img child, bare, on the rows just written."""
        st = self.child("img")
        if st.get("state") in ("done", "skipped", "not-run"):
            return
        if self.guards.tripped():
            self.skip_guarded("img")
            return
        if self.began(st):
            self.run_child("img")
            return
        if self.anything_ran() or "img" not in self.final:
            if self.anything_ran():
                self.ctx.ledger = list(self.env.pull())
            plan = self.env.plan(self.words["img"], self.child_ctx("img"))
        else:
            plan = self.final["img"]
        self.run_child("img", plan=plan)

    # -- step 9 --
    def child_runs(self, fam):
        st = (self.doc.get("children") or {}).get(fam) or {}
        out = {st["run"]} if st.get("run") else set()
        if fam == "img" and st.get("run"):
            out.add(runmod.new_run_id("holt", stamp=self.stamp, suffix="img"))   # its panel
        return out

    def child_entry(self, fam, rows):
        st = (self.doc.get("children") or {}).get(fam) or {}
        w = self.words[fam].word if fam in self.words else words.FAMILY_WORD[fam]
        run = st.get("run")
        text = None
        if run:
            try:
                with open(os.path.join(self.rdir(run), "report.txt"), encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                text = None
        if text is None:
            if st.get("state") in ("skipped", "not-run"):
                text = st.get("why") or st.get("state")
            else:
                text = "\n".join(x for x in (st.get("lines") or []) if x) or (st.get("why") or "not run")
        mine = run_rows(rows, self.child_runs(fam))
        gm = sum(float(r.get("gm") or 0) for r in mine if r.get("kind") == "run")
        gb = {}
        for r in mine:
            x = estimates.wear_gb(r)
            if x:
                gb[r.get("host") or "?"] = gb.get(r.get("host") or "?", 0.0) + x
        return {"word": w, "run": run, "exit": st.get("exit", "-"), "text": text, "gm": round(gm, 2),
                "wall_s": int(st.get("wall_s") or 0), "gb": gb}

    def missing(self, rows):
        """sweep.json's missing: what didn't run, a stop of any kind, a refusal, a due cell with no
        decisive row of this stamp, a guest whose identity failed."""
        out = []
        have = decisive_cells(rows, self.stamp)
        kids = self.doc.get("children") or {}
        for f in PLAN_ORDER[self.fam]:
            w = words.FAMILY_WORD[f] + "All"
            if self.universe is not None and not self.universe.get(f):
                out.append(f"{w}: not in --universe")
                continue
            st = kids.get(f) or {}
            if st.get("state") == "not-run" or st.get("state") in (None, "unstarted", "running"):
                out.append(f"{w}: {st.get('why') or 'not run'}")
                continue
            if st.get("exit") == EXIT_REFUSED:
                out.append(f"{w}: refused")
            elif st.get("exit") == EXIT_INTERNAL:
                out.append(f"{w}: an internal error")
            for s in st.get("stops") or ():
                out.append(f"{w}: stopped: {cut(s, 160)}")
        due = set()
        for f in PARALLEL:
            due |= set(((kids.get(f) or {}).get("due_cells")) or [])
        due |= set(self.doc.get("gate_due") or [])
        due |= set(((kids.get("gate") or {}).get("due_cells")) or [])
        left = sorted(c for c in due if c not in have)
        if left:
            out.append(f"{len(left)} due cell(s) with no decisive row: {', '.join(left[:12])}"
                       + (f" and {len(left) - 12} more" if len(left) > 12 else ""))
        for g, why in sorted((self.doc.get("identity_bad") or {}).items()):
            out.append(f"guest:{g}: identity: {cut(why, 120)}")
        out += self.hosts_missing()
        return out

    def hosts_missing(self):
        """A sweep is everything, fresh: on every default host (DEFAULT_HOSTS), each with facts.  A --hosts
        subset, or a host with no facts, leaves its pinned cells not runnable rather than due, so
        "every due cell has a decisive row" alone would call it complete.  Those cells, and every
        other cell a host's absence kept from running, are named too (a cell that is not runnable
        for a reason of its own, such as a blocked panel or missing binaries, is the coverage
        lines' business, not the sweep's)."""
        out = []
        facts = getattr(self.ctx, "facts", None) or {}
        absent = [h for h in planmod.DEFAULT_HOSTS if h not in self.hosts]
        if absent:
            out.append(f"ran on {', '.join(self.hosts)} only: {', '.join(absent)} not swept (--hosts)")
        blind = [h for h in self.hosts if h not in facts]
        if blind:
            out.append(f"no facts from {', '.join(blind)}: not swept")
        cells = []
        for f in PLAN_ORDER[self.fam]:
            p = self.final.get(f)
            if p is None:
                continue
            for c in p.by_status("not-runnable"):
                if any(HOST_GONE.search(r or "") for r in c.reasons or ()):
                    cells.append(c.cell)
        cells = sorted(set(cells))
        if cells:
            out.append(f"{len(cells)} cell(s) not runnable for want of a host: {', '.join(cells[:12])}"
                       + (f" and {len(cells) - 12} more" if len(cells) > 12 else ""))
        return out

    def coverage(self):
        """The coverage: lines (contract 1.8)."""
        out = []
        try:
            known = self.env.known(self.ctx)
            stic = set(self.ctx.drive.all_guests())
            nocheck = sorted(set(known.get("guest") or ()) - stic)
            if nocheck:
                out.append(f"stic: no check for {', '.join(nocheck)}")
        except Exception as e:   # noqa: BLE001 - a coverage line can't be made; say it
            out.append(f"stic: the checks can't be listed ({type(e).__name__})")
        seen = set()
        for f in PLAN_ORDER[self.fam]:
            p = self.final.get(f)
            if p is None:
                continue
            for n in p.notes:
                if n.startswith("not in any spec") and n not in seen:
                    seen.add(n)
                    out.append(n)
            nr = p.by_status("not-runnable")
            if nr:
                by = {}
                for c in nr:
                    by.setdefault(cut("; ".join(c.reasons) or "no host can run it", 120), []).append(c.cell)
                groups = sorted(by.items(), key=lambda x: (-len(x[1]), x[0]))
                for why, cs in groups[:6]:
                    out.append(f"not runnable: {', '.join(cs[:8])}{' ...' if len(cs) > 8 else ''} ({why})")
                more = [c for _, cs in groups[6:] for c in cs]
                if more:
                    out.append(f"not runnable: {', '.join(more[:12])}{' ...' if len(more) > 12 else ''} (`t2 plan` says why)")
        if self.fam == "sweep":
            out += self.img_coverage()
        return out

    def img_coverage(self):
        """A sweep doesn't test images: the soft-stale ones and each panel's age, from an img plan."""
        out = []
        try:
            known = self.env.known(self.ctx)
            iw = words.parse(["ImgTest2", "plan"], known)
            ictx = copymod.copy(self.ctx)
            ictx.options = {k: v for k, v in self.ctx.options.items() if k not in ("universe", "gate_fresh")}
            p = self.env.plan(iw, ictx)
        except Exception as e:   # noqa: BLE001
            return [f"img: no plan ({type(e).__name__}: {cut(e, 120)})"]
        soft = [c for c in p.by_status("soft-stale")]
        if soft:
            out.append("img: soft-stale " + ", ".join(f"{c.cell} ({cut('; '.join(c.reasons), 60)})" for c in soft))
        now = self.ctx.now
        panel = ((self.ctx.classes.get("holt") or {}).get("img_panel")) or {}
        never, ages = [], []
        for plat in words.PLATS:
            cell = f"img:{plat}"
            if (panel.get(plat) or {}).get("blocked"):
                out.append(f"img: {cell} panel blocked: {cut(panel[plat]['blocked'], 120)}")
                continue
            best = None
            for r in self.ctx.ledger:
                if r.get("cell") == cell and ((r.get("extra") or {}).get("panel") or {}).get("result") == "pass" \
                        and r.get("valid") is True:
                    if best is None or (r.get("t") or "") > (best.get("t") or ""):
                        best = r
            if best is None:
                never.append(plat)
                continue
            try:
                ages.append(f"{plat} {int((now - parse_utc(best['t'])) // 86400)} days ago")
            except (KeyError, TypeError, ValueError):
                ages.append(f"{plat} ({best.get('id')})")
        if ages:
            out.append("img: panels passed: " + ", ".join(ages))
        if never:
            out.append("img: panels never passed: " + ", ".join(never))
        return out

    def finish(self, t0):
        w = self.word
        ran_any = self.anything_ran(PLAN_ORDER[self.fam])
        rows = list(self.env.pull()) if ran_any or self.doc.get("identity_bad") else list(self.ctx.ledger)
        kids = self.doc.get("children") or {}
        order = [f for f in REPORT_ORDER[self.fam] if f in self.families()]
        flips = []
        if w.sweep:
            runs = [r for f in order for r in self.child_runs(f)]
            flips = report.verdict_flips(rows, runs)
            for fl in flips:
                for g, a, b in fl.get("guest_gens") or ():
                    if a and b and a != b:
                        fl["pkgdiff"] = pkg_text(g, a, b, self.env.pkgdiff(g, a, b))
        stops = list(self.guards.why())
        for f in order:
            for s in (kids.get(f) or {}).get("stops") or ():
                t = f"{words.FAMILY_WORD[f]}: {s}"
                if t not in stops:
                    stops.append(t)
        # the exit: the children's highest; 1 for identity, a to-class cell, a step a guard skipped
        ex = EXIT_OK
        for f in order:
            st = kids.get(f)
            if st is None or st.get("state") not in ("done", "skipped", "not-run"):
                # (never ended: its thread died, or it never began although its step ran)
                st = self.child(f)
                st.update(state="done", exit=EXIT_INTERNAL, why=st.get("why") or f"it never ended (state {st.get('state')})")
                self.note(f"{words.FAMILY_WORD[f]}: {st['why']}")
            e = st.get("exit")
            if e is None and st.get("state") == "done":     # its exit wasn't recorded (a resume)
                e = judged_exit(st, run_rows(rows, self.child_runs(f)))
                st["exit"] = e
            inv = invalid_passes(run_rows(rows, self.child_runs(f)))
            if inv and st.get("state") == "done":   # (its own exit may count only its plan's cells: a gate step's
                for r in inv[:6]:                    #  layers aren't in a SticTest2 plan)
                    t = f"{words.FAMILY_WORD[f]}: {r.get('cell')} PASS, but its row {r.get('id')} is invalid: {cut(r.get('note'), 120)}"
                    if t not in self.notes:
                        self.note(t)
                if not isinstance(e, int) or e < EXIT_FAIL:
                    e = st["exit"] = EXIT_FAIL
            if isinstance(e, int):
                ex = max(ex, e)
        if self.doc.get("identity_bad"):
            ex = max(ex, EXIT_FAIL)
        if any((kids.get(f) or {}).get("to_class") for f in order):
            ex = max(ex, EXIT_FAIL)
        now = self.env.now()
        if w.sweep:
            miss = self.missing(rows)
            sw = {"stamp": self.stamp, "run": self.run, "children": {f: (kids.get(f) or {}).get("run") for f in order},
                  "complete": not miss, "missing": miss, "t": utc_now(), "hosts": list(self.hosts),
                  "hosts_with_facts": [h for h in self.hosts if h in (getattr(self.ctx, "facts", None) or {})]}
            if self.universe is not None:   # (test only, never in the default root: conduct refuses it there)
                sw["universe"] = getattr(self.word, "universe", None) or sorted(c for v in self.universe.values() for c in v)
            runmod.write_json(os.path.join(self.rdir(), "sweep.json"), sw)
            self.doc["sweep"] = sw
            if miss:
                ex = max(ex, EXIT_FAIL)
        last = runmod.last_sweep(self.mac_root)
        policy = self.ctx.classes.get("policy")
        if not ran_any and not w.sweep:
            # nothing due anywhere: each child's line, then the sweep line
            lines = []
            for f in order:
                st = kids.get(f) or {}
                lines += [x for x in (st.get("lines") or []) if x and "\n" not in x] or [st.get("why") or f"{words.FAMILY_WORD[f]}: not run"]
            lines += [f"note: {n}" for n in self.notes[:10]]
            for g, why in sorted((self.doc.get("identity_bad") or {}).items()):
                lines.append(f"guest:{g}: identity: {why}")
            lines.append(report.sweep_line(policy, last, now))
            text = "\n".join(lines) + "\n"
        else:
            children = [self.child_entry(f, rows) for f in order]
            totals = {"word": w.word, "run": self.run, "stops": stops, "last_sweep": last, "now": now,
                      "wall_s": int(now - t0)}
            cov = self.coverage()
            text = report.render_composite(children, flips, totals, cov, policy)
            extra = [f"note: {n}" for n in self.notes]
            extra += [f"guest:{g}: identity: {why}: its stic and holt cells didn't run"
                      for g, why in sorted((self.doc.get("identity_bad") or {}).items())]
            if w.sweep:
                sw = self.doc.get("sweep") or {}
                extra.append("sweep: complete" if sw.get("complete") else
                             "sweep: INCOMPLETE (the days until due stay as they were): " + "; ".join(sw.get("missing") or [])[:600])
            if extra:   # before the sweep line, which stays last
                lines = text.rstrip("\n").split("\n")
                text = "\n".join(lines[:-1] + extra + lines[-1:]) + "\n"
        runmod.write_text(os.path.join(self.rdir(), "report.txt"), text)
        self.say(text.rstrip())
        with self.mu:
            self.doc["exit"] = ex
            self.doc["end"] = utc_now()
        self.phase("done")
        return ex


class _IdentityRun:
    """What guest.identity_check reads of ctx.run: the word its FAIL rows name, and the copy
    whose t2-host appends them."""

    def __init__(self, word, copy):
        self.word, self.copy = word, copy


# --- the CLI's entry points -----------------------------------------------------------------------

def conduct(word, hosts, checkout, out=print, env=None):
    """t2 BkoTest2 ... | SweepTest2 ...: the exit code."""
    try:
        c = Conductor(word, hosts, checkout, out=out, env=env)
    except Refused as e:
        out(f"t2: {e}")
        return EXIT_REFUSED
    return c.conduct()


def resume(run, hosts=None, out=print, env=None, checkout=None):
    """t2 resume t2b-X | t2w-X: the composite again, from its bko.json, on its own hosts."""
    if not runmod.RUN_RE.match(run or "") or not run.startswith(("t2b-", "t2w-")):
        out(f"t2: not a BkoTest2 or SweepTest2 run: {run}")
        return EXIT_REFUSED
    mroot = env.mac_root if env is not None else None
    doc = runmod.read_json(os.path.join(runmod.run_dir(run, mroot), "bko.json"))
    if not doc:
        out(f"t2: no {runmod.run_dir(run, mroot)}/bko.json")
        return EXIT_REFUSED
    saved_hosts = list(doc.get("hosts") or [])
    if hosts and saved_hosts and list(hosts) != saved_hosts:
        out(f"t2: note: {run} ran on {','.join(saved_hosts)}: it resumes there")
    hosts = saved_hosts or list(hosts or ())
    if doc.get("phase") == "done":
        try:
            with open(os.path.join(runmod.run_dir(run, mroot), "report.txt"), encoding="utf-8") as f:
                out(f.read().rstrip())
        except OSError:
            pass
        out(f"t2: {run} had finished")
        return int(doc.get("exit") or 0)
    checkout = checkout or (env.checkout if env is not None else CHECKOUT)
    env = env or Env(checkout, hosts)
    try:
        known = env.known(env.light_ctx())
        word = words.parse(str(doc.get("text") or "").split(), known)
    except words.Refused as e:
        out(f"t2: {run}'s word doesn't parse now: {e}")
        return EXIT_REFUSED
    word.universe = doc.get("universe")
    try:
        c = Conductor(word, hosts, checkout, out=out, env=env)
        for f in c.families():
            c.words[f] = child_word(word, f, known)
    except (Refused, words.Refused) as e:
        out(f"t2: {e}")
        return EXIT_REFUSED
    c.run, c.stamp = doc["run"], doc["stamp"]
    c.notes = list(doc.get("notes") or [])
    out(f"t2: resuming {run} from its phase {doc.get('phase')}")
    return c.go(saved=doc)

