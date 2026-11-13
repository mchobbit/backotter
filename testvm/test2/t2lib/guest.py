"""GuestTest2: every test guest's health, read only but for `GuestTest2 update` (contract 2.1, K3).

    run(ctx, plan), resume(ctx, plan)       run.py's runner API
    pre_plan(ctx, word, out)                image sums, identity of disks that moved; once, before the plan
    preflight(ctx, out)                     what a killed run left (t2-host tool guest sweep)
    identity_check(ctx, guests, sha256=False, run=None) -> {G: {ok, why}}
                                            step 1; a failing guest gets its FAIL row, under RUN
    cmd_lines(argv), cmd_fingerprints(argv) t2 guest-lines, t2 guest-fingerprints

A pool per host on the Mac, one shared list; each job is guest/job.sh through hostjob.host_job,
under cmp-run.sh --reserve D M, WEAR_TAG=guest:RUN:G.  The pool's rules: at most 3 jobs a host (2 in an
update round), none started while one of that host's is still in cmp-run.sh's line, and jobs of D >=
big_gib one at a time.  An overrun below the cap reruns at 2 x D and feeds no stop rule.  A FAIL that
isn't to_class_only reruns once (try and probe), never in an update.  MAC_ROOT/runs/RUN/guest.json
records what started and what ended: resume adopts an attempt that was started (hostjob.reattach_job)
and starts another only once that one is proven gone with no row, never in an update round.
"""

import collections
import json
import math
import os
import shlex
import subprocess
import threading
import time

from . import guest_judge as J
from . import DEFAULT_HOSTS, hostjob, hostrun, mac_root, rows_guest, tool_guest, utc_now

POLL_S = 15
MAX_ATTEMPTS = 3
JOBS_PER_HOST = 3
UPDATE_JOBS = 2
UPDATE_MIN_D = 6
RUN_DEFAULTS = {"try_days": 7, "ram_gib_first": 8, "ram_gib_max": 16, "big_gib": 13, "failed_overlay_mib": 1024,
                "failed_keep_days": 14}
FAILING = ("FAIL", "FLAKY-FAIL", "HARNESS", "LOST")
FIRST_HOST = "chopin"
KIND_MODE = {"ssh": "try", "probe": "probe", "redox": "console", "sculpt": "console"}


def run_policy(classes):
    p = ((classes or {}).get("policy") or {}).get("guest") or {}
    return dict(RUN_DEFAULTS, **(p.get("run") or {}))


def mode_of(entry, update=False):
    if update:
        return "update"
    return KIND_MODE.get(entry.get("kind"), "try")


def reserve_for(rows, cell, entry, policy, update=False):
    """(D, M) of a job's --reserve (contract 2.1): D from the last rows' RAM peaks, else the
    spec's ram_gib, else ram_gib_first, never over ram_gib_max; M the guest's RAM + 1 GiB."""
    mine = sorted((r for r in rows or () if r.get("cell") == cell and r.get("kind") == "run"
                   and r.get("verdict") not in ("HARNESS", "LOST") and isinstance(r.get("ram_peak_kib"), int) and r["ram_peak_kib"] > 0),
                  key=lambda r: (r.get("t") or "", r.get("id") or ""), reverse=True)[:3]
    cap = float(policy.get("ram_gib_max", 16))
    if mine:
        d = round(1.1 * max(r["ram_peak_kib"] for r in mine) / 1048576 + 0.25, 1)
    elif entry.get("ram_gib"):
        d = float(entry["ram_gib"])
    else:
        d = float(policy.get("ram_gib_first", 8))
    d = max(min(d, cap), 1.0)
    if update:
        d = max(d, min(float(UPDATE_MIN_D), cap))
    mem = int(entry.get("memory") or 2048)
    return d, int(math.ceil(mem / 1024.0)) + 1


# --- the identity step --------------------------------------------------------------------------

REMOTE_DRIVER = r'''
import contextlib as _c, io as _io
_d = json.loads(_T2_DATA)
os.environ.update(_d.get("env") or {})
_b = _io.StringIO()
with _c.redirect_stdout(_b):
    _rc = main(_d["args"])
sys.stdout.write(json.dumps({"rc": _rc, "out": _b.getvalue()}))
'''


def host_env():
    """The Test2 roots a host-side command needs, in the hosts' terms."""
    e = {}
    for k in ("T2_HOST_ROOT", "T2_WORK", "T2_LEDGER", "T2_COPY_ROOT", "T2_IMAGES"):
        if os.environ.get(k):
            e[k] = hostrun.host_path(os.environ[k])
    return e


def vm_update_env():
    """host_env() and what vm-update itself reads: a world's VM_UPDATE_REMOTE_VMS (vm-update refuses a
    world without it) and VM_UPDATE_HOLT_BIN, passed as they are (relative to the other host's home)."""
    e = host_env()
    for k in ("VM_UPDATE_REMOTE_VMS", "VM_UPDATE_HOLT_BIN"):
        if os.environ.get(k):
            e[k] = os.environ[k]
    return e


def remote_tool(io, host, args, timeout=3600):
    """tool_guest.main(ARGS) on HOST, its source over ssh's stdin: (rc, stdout).  No copy needed."""
    with open(tool_guest.__file__, encoding="utf-8") as f:
        src = f.read()
    src = src.replace('if __name__ == "__main__":', "if False:") + REMOTE_DRIVER
    r = io.script(host, src, {"args": list(args), "env": host_env()}, timeout=timeout)
    return r.get("rc", 1), r.get("out", "")


def _json_out(text):
    try:
        return json.loads((text or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def may_lack_guests(host):
    """True when HOST is a known host that isn't a fleet host (hosts.json: no "fleet" role, an
    image cache such as copland's or cage's): its vms/ holds the guests it has fetched, so a guest
    it lacks is neither vouched for nor condemned there.  A fleet host holds every guest, and an
    unknown name is taken for one (as before the table had roles)."""
    from . import hosts as hosts_table
    try:
        r = hosts_table.find(host)
    except hosts_table.HostsError:
        return False
    return r is not None and "fleet" not in (r.get("roles") or ())


def identity_compare(per_host, hashed=False, optional=()):
    """Step 1 on what the hosts answered ({host: tool_guest.identity_of's dict}).
    {"ok": True|False|None, "why", "rehash": [hosts]}; ok None: hash on the hosts in rehash first.
    A host marked no_answer vouches for nothing and condemns nothing: ok False, harness True.
    A host in OPTIONAL (may_lack_guests: an image cache) that has no such guest is left out
    while another host has it: only the hosts that hold the guest are compared, and the result's
    "dropped" ({host: "dangles"|"gone"}) names the hosts left out so.
    A disk with a backing file fails, but for a world's thin disk over the fleet's same disk
    (contract 4), whose backing disk passes the fleet's checks, at one generation on every host."""
    dropped = {}
    if optional:
        lacks = {h for h, i in per_host.items() if h in optional and (not i or i.get("missing"))}
        if lacks and any(i and not i.get("missing") for h, i in per_host.items() if h not in lacks):
            dropped = {h: "dangles" if (per_host[h] or {}).get("dangling") else "gone" for h in sorted(lacks)}
            per_host = {h: i for h, i in per_host.items() if h not in lacks}
    res = _identity_compare(per_host, hashed)
    if dropped:
        res["dropped"] = dropped
    return res


def _identity_compare(per_host, hashed):
    silent = sorted(h for h, i in per_host.items() if i and i.get("no_answer"))
    if silent:
        return {"ok": False, "harness": True, "why": ", ".join(silent) + ": no answer to the identity step", "rehash": []}
    bad = [f"{h}: no such guest" + (" (its vms/ link dangles)" if (i or {}).get("dangling") else "")
           for h, i in per_host.items() if not i or i.get("missing")]
    if bad:
        return {"ok": False, "why": "; ".join(bad), "rehash": []}
    gens = {h: (i.get("image_state") or {}).get("generation") for h, i in per_host.items()}
    states = set(gens.values())
    has_state = any(states)
    if has_state and len(states) > 1:
        return {"ok": False, "why": "the hosts' generations differ: " + ", ".join(f"{h} {str(g)[:12]}" for h, g in sorted(gens.items())),
                "rehash": []}
    bases = {}
    for h, i in sorted(per_host.items()):
        for n, q in sorted((i.get("qemu") or {}).items()):
            why, snaps = None, q.get("snapshots") or []
            if q.get("ok") and q.get("format") == "qcow2" and q.get("backing"):
                # only a world's thin disk over the fleet's same disk (tool_guest.world_base), and that
                # disk is judged as the fleet's are: the snapshot lives there, not in the thin one
                b = q.get("base")
                bq = (b or {}).get("qemu") or {}
                if not b:
                    why = "it has a backing file"
                elif not bq.get("ok"):
                    why = "its backing file: " + (bq.get("why") or "qemu-img failed")
                elif bq.get("format") != "qcow2" or bq.get("backing") or bq.get("dirty") or bq.get("corrupt"):
                    why = "its backing file isn't a sound fleet image"
                elif not b.get("generation"):
                    why = "its backing file's guest has no image-state"
                snaps = bq.get("snapshots") or []
                bases.setdefault(n, {})[h] = (b or {}).get("generation")
            if why is None:
                if not q.get("ok"):
                    why = q.get("why") or "qemu-img failed"
                elif q.get("format") != "qcow2":
                    why = f"format {q.get('format')}"
                elif q.get("dirty"):
                    why = "dirty flag set"
                elif q.get("corrupt"):
                    why = "marked corrupt"
                elif (has_state or q.get("backing")) and "fresh-install" not in snaps:
                    why = "no fresh-install snapshot"
            if why:
                return {"ok": False, "why": f"{h}: {n}: {why}", "rehash": []}
    for n, g in sorted(bases.items()):
        if len(g) != len(per_host) or len(set(g.values())) > 1:
            return {"ok": False, "why": f"{n}: the backing files' generations differ: " +
                    ", ".join(f"{h} {str(x)[:12]}" for h, x in sorted(g.items())), "rehash": []}
    if not has_state:
        sums = {h: i.get("sha256") for h, i in per_host.items()}
        if not hashed:
            return {"ok": None, "why": "no image-state: the hosts' sums compare", "rehash": sorted(per_host)}
        for h, i in sorted(per_host.items()):   # a hash nobody produced vouches for nothing
            miss = sorted(n for n in (i.get("disks") or {}) if not (sums[h] or {}).get(n))
            if not sums[h] or miss:
                return {"ok": False, "why": f"{h}: no sha256 of " + (", ".join(miss) or "its disks"), "rehash": []}
        if len(sums) > 1 and len({json.dumps(v, sort_keys=True) for v in sums.values()}) > 1:
            return {"ok": False, "why": "no image-state, and the hosts' sums differ", "rehash": []}
        return {"ok": True, "why": None, "rehash": []}
    rehash, wrong = [], []
    for h, i in sorted(per_host.items()):
        st, disks = i.get("stat") or {}, i.get("disks") or {}
        want = (i.get("image_state") or {}).get("sha256") or {}
        if hashed:
            sh = i.get("sha256") or {}
            if not want or any(not sh.get(n) or sh.get(n) != want.get(n) for n in disks):
                wrong.append(h)
            continue
        recs = st.get("disks") or {}
        if st.get("generation") != gens[h] or set(recs) != set(disks) or \
                any((disks[n] or {}) != {k: (recs.get(n) or {}).get(k) for k in ("size", "mtime_ns", "inode")} for n in disks):
            rehash.append(h)
    if wrong:
        return {"ok": False, "why": "sha256 differs from image-state on " + ", ".join(wrong), "rehash": []}
    if rehash:
        return {"ok": None, "why": "disks differ from their merge record", "rehash": rehash}
    return {"ok": True, "why": None, "rehash": []}


def _src_of(ctx):
    from . import keys
    return getattr(ctx, "src", None) or keys.TreeSource(ctx.checkout)


def harness_row(ctx, guest, host, run, attempt, why, rule, word=None, needs=None, verdict="HARNESS", planned=None, reasons=None):
    """A row the Mac writes for what no job reported (a job that didn't start, identity): common
    fields from the checkout, inputs measured with the host's facts."""
    from . import copies, keys
    cell = f"guest:{guest}"
    try:
        specs = getattr(ctx, "specs", None) or keys.Specs(keys.TreeSource(ctx.checkout))
        m = keys.measure(cell, _src_of(ctx), (ctx.facts or {}).get(host) or {}, specs, {})
    except Exception as e:   # noqa: BLE001 - an unmeasured row is invalid, never a crash
        m = {"inputs": {}, "unknown": [f"{type(e).__name__}: {e}"], "spec": None}
    try:
        pv = copies.preview(ctx.checkout)
        common = {"commit": pv.commit, "committed": not pv.dirty, "dirty": dict(pv.dirty), "copy": pv.id}
    except Exception:   # noqa: BLE001
        common = {"commit": None, "committed": False, "dirty": None, "copy": "unknown"}
    needs = dict(needs or {"harness": "FAIL"})
    unknown = m.get("unknown") or []
    valid = not unknown and (planned is None or planned == m.get("key"))
    klass = {"HARNESS": "harness", "FAIL": "fail"}.get(verdict, "harness")
    row = dict(common, origin=f"guest:{run}:{guest}:{attempt}", family="guest", cell=cell, kind="run", run=run,
               word=word or "GuestTest2", rule=rule, reasons=[str(x)[:200] for x in (reasons or [why])][:20], attempt=attempt,
               start=utc_now(), inputs=m.get("inputs") or {}, spec=m.get("spec"), planned_key=planned, valid=bool(valid),
               verdict=verdict, needs=needs, counts={}, fails=[f"{verdict.lower()}: {why}"[:200]], gm=0.0, wall_s=0, waits_s=0,
               wear={}, ram_peak_kib=None, logs=None, meta={"run_family": "guest"},
               extra={"mode": None, "try_ran": False, "to_class_only": False}, note=str(why)[:1000])
    row["class"] = klass
    return row


def append_row(ctx, io, host, row, log=None):
    """ROW into HOST's ledger through t2-host append (the run's copy, else the synced tree);
    if that fails, into mac.jsonl: a row is never lost.  Its id, or None."""
    from . import ledger, mac_ledger_file
    rs = getattr(ctx, "run", None)
    cp = ((getattr(rs, "copy", None) or {}).get(host) or {}) if rs is not None else {}
    base = (cp.get("path") if isinstance(cp, dict) else None) or hostrun.host_path(os.environ.get("T2_WORK") or "~/backotter-work")
    r = ledger.complete(row, host=host)
    envs = " ".join(f"{k}={hostrun.rq(v)}" for k, v in sorted(host_env().items()))
    tool = hostrun.rq(base.rstrip("/") + "/testvm/test2/t2-host")
    rc, text, err = io.sh(host, f"env {envs} python3 -B {tool} append", input=json.dumps(r, sort_keys=True).encode(), timeout=120)
    if rc == 0 and (text or "").strip():
        return text.strip().splitlines()[-1]
    if log:
        log(f"{host}: t2-host append failed ({str(err).strip()[-120:]}): into mac.jsonl")
    try:
        r = ledger.complete(dict(row, host="mac"), host="mac")
        ledger.check(r)
        return ledger.append(mac_ledger_file(), [r])[0][0]
    except Exception as e:   # noqa: BLE001
        if log:
            log(f"guest row unwritten: {e}")
        return None


def identity_check(ctx, guests, sha256=False, run=None, io=None, log=None):
    """Step 1 for GUESTS: {G: {ok, why}} (a guest whose disks can't be vouched for has ok false, and
    its FAIL row is written, under RUN; RUN None: a new t2v- id).  sha256: always hash.  A host
    that didn't answer, twice, isn't a guest's fault: ok false, harness true, and no FAIL row."""
    from . import run as runmod
    io = io or hostjob.HostIO()
    log = log or (lambda t: None)
    hosts = [h for h in ctx.hosts if h in (ctx.facts or {})]
    guests = list(guests)
    if not guests or not hosts:
        return {}
    info = {}

    def ask(host, names, sha, backfill):
        """{guest: its identity}, or None when HOST gave no answer, twice."""
        a = ["identity"] + list(names) + (["--sha256"] if sha else []) + (["--backfill"] if backfill else [])
        for n in range(2):
            try:
                rc, out = remote_tool(io, host, a)
                got = _json_out(out) if rc == 0 else None
                if isinstance(got, dict):
                    return got
                log(f"{host}: identity: exit {rc}, no answer")
            except Exception as e:   # noqa: BLE001 - a host that can't answer makes no guest pass
                log(f"{host}: identity: {e}")
            if n == 0:
                io.sleep(5)
        return None

    optional = {h for h in hosts if may_lack_guests(h)}

    def holders(g):
        """The hosts asked about G: all but an image cache whose facts say it hasn't G (a cache
        that fetches it later is checked then); every host when none has it (no such guest)."""
        def has(h):
            gs = (ctx.facts.get(h) or {}).get("guests")
            return not isinstance(gs, dict) or g in gs     # facts with no guests part: asked, as before
        return [h for h in hosts if h not in optional or has(h)] or hosts

    asked = {g: holders(g) for g in guests}
    for h in hosts:
        names = [g for g in guests if h in asked[g]]
        if not names:
            continue
        for g, v in (ask(h, names, sha256, sha256) or {}).items():
            info.setdefault(g, {})[h] = v
    out, rid = {}, None
    for g in guests:
        per = info.get(g) or {}
        for h in asked[g]:
            per.setdefault(h, {"no_answer": True})
        res = identity_compare(per, hashed=sha256, optional=optional)
        dropped = dict(res.get("dropped") or {})
        if res["ok"] is None:
            for h in res["rehash"]:
                got = (ask(h, [g], True, True) or {}).get(g)
                if got:
                    per[h] = dict(per[h], **got)
                    if got.get("backfilled"):
                        _patch_stat(ctx, h, g, got.get("stat"))
                else:
                    per[h] = {"no_answer": True}
            only = {h: per[h] for h in res["rehash"]} if len(res["rehash"]) < len(per) else per
            res = identity_compare(only, hashed=True, optional=optional)
            dropped.update(res.get("dropped") or {})
        if res.get("harness"):
            log(f"guest:{g}: identity unknown: {res.get('why')}")
            out[g] = {"ok": False, "why": res.get("why"), "harness": True, "host": hosts[0]}
            continue
        if not res.get("ok"):
            why = res.get("why") or "identity unknown"
            rid = run or rid or runmod.new_run_id("guest")
            word = getattr(getattr(getattr(ctx, "run", None), "word", None), "text", None)
            row = harness_row(ctx, g, hosts[0], rid, 1, why, "guest:identity", needs={"identity": "FAIL"}, verdict="FAIL", word=word, reasons=[why])
            row_id = append_row(ctx, io, hosts[0], row, log)
            log(f"guest:{g}: identity FAIL: {why}")
            out[g] = {"ok": False, "why": why, "row": row_id, "host": hosts[0]}
            continue
        out[g] = {"ok": True, "why": res.get("why")}
        gone = _gone_since_facts(ctx, g, dropped)
        if gone:
            out[g]["lacking"] = gone
            for h, w in sorted(gone.items()):
                log(f"guest:{g}: {h}: no {g} in its vms/ ({w}): its stic and holt cells stay off {h}")
    return out


GONE_WORDS = {"dangles": "its vms/ link dangles, gone since facts", "gone": "gone since facts"}


def _gone_since_facts(ctx, g, dropped):
    """{host: why} for each image cache DROPPED (identity_compare) whose facts said it held G: G
    leaves that host's facts' guests (so placement, Planner.fit_capabilities, keeps G's cells off
    it, as the host's next facts would) and its facts' "guests_gone" says why.  A cache whose
    facts have no guests part was asked without knowing: nothing to correct."""
    gone = {}
    for h, how in sorted((dropped or {}).items()):
        f = (ctx.facts or {}).get(h)
        gs = (f or {}).get("guests")
        if not isinstance(gs, dict) or g not in gs:
            continue
        gs.pop(g, None)
        gone[h] = GONE_WORDS.get(how, GONE_WORDS["gone"])
        f.setdefault("guests_gone", {})[g] = gone[h]
    return gone


def _patch_stat(ctx, host, guest, stat):
    g = ((ctx.facts.get(host) or {}).get("guests") or {}).get(guest)
    if isinstance(g, dict) and stat:
        g["stat"] = stat


def _disk_differs(g):
    """Facts' disks[] against the guest's image-stat.json at the same generation."""
    st, gen = g.get("stat") or {}, g.get("generation")
    if not gen or st.get("generation") != gen:
        return False
    for d in g.get("disks") or ():
        rec = (st.get("disks") or {}).get(os.path.basename(d.get("path") or ""))
        if rec is None:
            return True
        if any(d.get(k) != rec.get(k) for k in ("size", "mtime_ns", "inode")):
            return True
    return False


def _word_guests(ctx, word, sf):
    names = [n for n in sf.names()]
    uni = (getattr(ctx, "options", None) or {}).get("universe")
    if uni:
        keep = {c[6:] for c in uni if str(c).startswith("guest:")}
        return [n for n in names if n in keep]
    fam = getattr(word, "family", None)
    if fam == "guest" and getattr(word, "names", None):
        keep = {str(c).split(":", 1)[-1] for c in word.names}
        return [n for n in names if n in keep]
    return names


def pre_plan(ctx, word, out=print, io=None):
    """Before the plan, once: the image sums the keys need for guests with no image-state, and the
    identity of every guest whose disks differ from its image-stat.json at the same generation
    (hash, then backfill, or a FAIL row).  ctx.facts get what changed."""
    io = io or hostjob.HostIO()
    sf = rows_guest.load_spec(ctx.checkout)
    names = _word_guests(ctx, word, sf)
    hosts = [h for h in ctx.hosts if h in (ctx.facts or {})]
    work = hostrun.host_path(os.environ.get("T2_WORK") or "~/backotter-work")
    from . import facts_host
    for h in hosts:
        gs = (ctx.facts[h].get("guests") or {})
        want = {n: [d.get("path") for d in gs[n].get("disks") or () if d.get("path")] for n in names
                if n in gs and not gs[n].get("generation") and not gs[n].get("imgsum")}
        paths = sorted({p for v in want.values() for p in v})
        if not paths:
            continue
        out(f"{h}: image sums of {', '.join(sorted(want))}")
        envs = " ".join(f"{k}={hostrun.rq(v)}" for k, v in sorted(host_env().items()))
        rc, text, _ = io.sh(h, f"env {envs} python3 -B {hostrun.rq(work.rstrip('/') + '/testvm/test2/t2-host')} imgsum "
                               + " ".join(hostrun.rq(p) for p in paths) + " </dev/null", timeout=3600)
        got = _json_out(text) if rc == 0 else None
        if not isinstance(got, dict):
            out(f"{h}: no image sums (t2-host imgsum exit {rc}): those rows won't count")
            continue
        for n, ds in want.items():
            sums = {os.path.basename(p): got.get(p) for p in ds}
            if sums and all(sums.values()):
                gs[n]["imgsum"] = facts_host.combine(sums)
    moved = sorted({n for h in hosts for n in names if _disk_differs((ctx.facts[h].get("guests") or {}).get(n) or {})})
    if moved:
        out("disks differ from their merge records: " + ", ".join(moved))
        res = identity_check(ctx, moved, io=io, log=out)
        for g, r in sorted(res.items()):
            out(f"guest:{g}: identity {'ok' if r['ok'] else ('unknown: ' if r.get('harness') else 'FAIL: ') + str(r['why'])}")


def preflight(ctx, out=print, io=None):
    """What a killed run of this world left, on each host; old failed/ records."""
    io = io or hostjob.HostIO()
    pol = run_policy(ctx.classes)
    for h in [x for x in ctx.hosts if x in (ctx.facts or {})]:
        try:
            rc, text = remote_tool(io, h, ["sweep", "--failed-keep-days", str(pol["failed_keep_days"])], timeout=300)
        except Exception as e:   # noqa: BLE001 - the sweep is housekeeping
            out(f"{h}: guest sweep: {e}")
            continue
        for line in (text or "").splitlines():
            if line and not line.startswith("sweep: "):
                out(f"{h}: {line}")
        if rc != 0:
            out(f"{h}: guest sweep exit {rc}")


# --- the fleet lock an update round holds ----------------------------------------------------------

# What an update round runs from the synced tree, and so must be this checkout's (contract 2.1 step 1);
# testvm/hosts.json: vm-update's fleet.
TREE_PATHS = ("testvm/vm-update", "testvm/update", "testvm/test2/guest",
              "testvm/test2/t2-host", "testvm/test2/t2lib", "testvm/test2/classes.json", "testvm/hosts.json")


class FleetLock:
    """ssh HOST vm-update --hold-fleet-lock RUN: held once it says so, a heartbeat line every 30 s."""

    def __init__(self, host, work, run, beat_s=30):
        self.host, self.work, self.run, self.beat_s = host, work, run, beat_s
        self.p = None
        self.stop_ev = threading.Event()
        self.dead = None          # why the holder is gone, once it is: its jobs then start no more

    def take(self, timeout=120):
        envs = " ".join(f"{k}={hostrun.rq(v)}" for k, v in sorted(vm_update_env().items()))
        remote = f"{hostrun.NOBYTECODE}env {envs} BKO_WORK={hostrun.rq(self.work)} {hostrun.rq(self.work.rstrip('/') + '/testvm/vm-update')} --hold-fleet-lock {shlex.quote(self.run)}"
        self.p = subprocess.Popen(hostrun.ssh_base() + [hostrun.dest(self.host), remote], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        line = _readline_timeout(self.p.stdout, timeout)
        if not line or line.strip() != b"held":
            self.release()
            return False
        threading.Thread(target=self._beat, daemon=True).start()
        threading.Thread(target=self._watch, args=(self.p,), daemon=True).start()
        return True

    def _gone(self, why):
        if self.dead is None and not self.stop_ev.is_set():
            self.dead = why

    def _beat(self):
        while not self.stop_ev.wait(self.beat_s):
            try:
                self.p.stdin.write(b"\n")
                self.p.stdin.flush()
            except (OSError, ValueError, AttributeError) as e:
                self._gone(f"a heartbeat couldn't be sent ({e.__class__.__name__})")
                return

    def _watch(self, p):
        """The holder prints nothing after "held": its stdout's end is its end."""
        try:
            while p.stdout.readline() and not self.stop_ev.is_set():
                pass
        except (OSError, ValueError):
            pass
        self._gone("its ssh ended")

    def lost(self):
        """Why the holder is gone (its ssh ended, a heartbeat failed, it exited), or None.
        Once it is, vm-update has removed the round's name: no job of this round may start."""
        if self.dead is None and self.p is not None and not self.stop_ev.is_set():
            try:
                rc = self.p.poll()
            except (OSError, AttributeError):
                rc = None
            if isinstance(rc, int):
                self._gone(f"it exited ({rc})")
        return self.dead

    def release(self):
        self.stop_ev.set()
        if self.p is not None:
            try:
                self.p.stdin.close()
            except OSError:
                pass
            try:
                self.p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.p.terminate()
            self.p = None


def _readline_timeout(stream, timeout):
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("l", stream.readline()), daemon=True)
    t.start()
    t.join(timeout)
    return box.get("l")


# --- the pool --------------------------------------------------------------------------------------

class UpdateEnv(hostjob.Env):
    """An update round runs from the synced tree (contract 2.1: real updates only from it)."""

    def __init__(self, rs, host_root, work, io=None, log=None):
        super().__init__(rs, host_root, io, log)
        self.work = work

    def copy_path(self, host):
        return self.work


class Item:
    def __init__(self, guest, pc, entry, d, m, allowed, attempt=1):
        self.guest, self.pc, self.entry, self.d, self.m, self.allowed, self.attempt = guest, pc, entry, d, m, allowed, attempt
        self.rows, self.rerun_done = [], False
        self.prev_verdict, self.flake = None, None
        self.resumed = None       # guest.json's record of an attempt an earlier process started

    @property
    def cell(self):
        return f"guest:{self.guest}"


class Runner:
    def __init__(self, ctx, plan, io=None, log=None, job=None, fleet=None, poll=POLL_S, clock=None, reattach=None):
        self.ctx, self.plan, self.rs = ctx, plan, ctx.run
        rs = self.rs
        self.word = rs.word
        self.update = bool(getattr(self.word, "update", False))
        hr = hostrun.host_path(rs.host_root)
        self.work = hostrun.host_path(os.environ.get("T2_WORK") or "~/backotter-work")
        self.env = UpdateEnv(rs, hr, self.work, io, log) if self.update else hostjob.Env(rs, hr, io, log)
        self.io = self.env.io
        self.host_root = hr
        self.job = job or hostjob.host_job
        self.reattach = reattach or hostjob.reattach_job
        self.fleet = fleet
        self.held_fleet, self.holder_noted = None, False   # an update round's holder, while it holds
        self.poll, self.clock = poll, clock or time.time
        self.t0 = self.clock()
        self.sf = rows_guest.load_spec(ctx.checkout)
        self.policy = run_policy(ctx.classes)
        self.flakes = rows_guest.flakes_of(ctx.checkout)
        self.hosts = list(rs.hosts)
        self.max_jobs = UPDATE_JOBS if self.update else JOBS_PER_HOST
        self.big = float(self.policy.get("big_gib", 13))
        self.cap = float(self.policy.get("ram_gib_max", 16))
        self.pending = collections.deque()
        self.active = {}          # thread name -> (item, host)
        self.line = {h: 0 for h in self.hosts}
        self.count = {h: 0 for h in self.hosts}
        self.bigs = {h: 0 for h in self.hosts}
        self.done_q = collections.deque()
        self.mu = threading.Lock()
        self.cells, self.notes, self.blocks = [], [], []
        self.skipped = []
        self.state_path = os.path.join(rs.mac_dir, "guest.json")
        self.state = self._load_state()
        self.last_checks = 0.0

    # --- state ------------------------------------------------------------------------------------
    def _load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d.setdefault("v", 1)
        d.setdefault("started", {})
        d.setdefault("ended", {})
        return d

    def save(self):
        from . import run as runmod
        with self.mu:
            runmod.write_json(self.state_path, self.state)

    def log(self, text):
        self.env.log("guest: " + text)

    # --- the plan's cells -----------------------------------------------------------------------
    def due(self):
        return [pc for pc in self.plan.cells if pc.status == "due" and str(pc.cell).startswith("guest:")]

    def allowed_hosts(self, pc):
        h = getattr(pc, "host", None)
        if h in self.hosts:
            return [h]
        ks = [x for x in (getattr(pc, "keys", None) or {}) if x in self.hosts]
        return ks or list(self.hosts)

    def item_of(self, pc):
        g = pc.cell.split(":", 1)[1]
        e = self.sf.entry(g)
        d, m = reserve_for(self.ctx.ledger, pc.cell, e, self.policy, self.update)
        allowed = [FIRST_HOST if FIRST_HOST in self.hosts else self.hosts[0]] if self.update else self.allowed_hosts(pc)
        return Item(g, pc, e, d, m, allowed)

    def pm_of(self, it):
        return (it.entry.get("checks") or {}).get("pm")

    # --- baselines and cells ---------------------------------------------------------------------
    def upload_baselines(self, guests):
        root = os.path.join(getattr(self.rs, "mac_root", None) or mac_root(), "guest-baselines")
        for g in guests:
            p = os.path.join(root, f"{g}.json")
            try:
                with open(p, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            d = f"{self.host_root.rstrip('/')}/runs/{self.rs.run}/baselines"
            for h in self.hosts:
                self.io.sh(h, f"mkdir -p {hostrun.rq(d)} && cat > {hostrun.rq(d + '/' + g + '.json')}", input=data, timeout=60)

    def write_job_cells(self, it, host):
        """The attempt's own cells file (reruns: rule, attempt, what it reruns)."""
        path = f"{self.host_root.rstrip('/')}/runs/{self.rs.run}/cells-{it.guest}-{it.attempt}.json"
        cp = (getattr(self.rs, "copy", None) or {}).get(host) or {}
        pc = it.pc
        ent = {"cell": it.cell, "rule": "rerun" if it.prev_verdict == "FAIL" else getattr(pc, "rule", None),
               "reasons": list(getattr(pc, "reasons", None) or [])[:20], "attempt": it.attempt,
               "planned_key": None if self.update else (getattr(pc, "keys", None) or {}).get(host),
               "prev_verdict": it.prev_verdict, "flake": it.flake, "rerun_of": it.rows[-1] if it.rows else None}
        doc = {"run": self.rs.run, "word": self.word.text, "attempt": it.attempt,
               "copy": {k: cp.get(k) for k in ("id", "path", "commit", "dirty")} if isinstance(cp, dict) else {},
               "cells": {it.guest: ent}}
        self.io.sh(host, f"mkdir -p {hostrun.rq(os.path.dirname(path))} && cat > {hostrun.rq(path)}", input=json.dumps(doc, sort_keys=True).encode(),
                   timeout=60)
        return path

    def job_env(self, it, cells, mode):
        c = it.entry.get("checks") or {}
        rules = " ".join(v for v in (c.get("rules") or {}).values() if v)
        e = {"T2_GUEST_RULES": rules, "T2_GUEST_PLAT": it.entry.get("plat") or "linux", "T2_CELLS": cells,
             "T2_GUEST_NEW": "0" if c.get("established", True) else "1"}
        e.update(host_env())
        if it.entry.get("cpus"):
            e["T2_GUEST_CPUS"] = str(it.entry["cpus"])
        if it.entry.get("memory"):
            e["T2_GUEST_MEMORY"] = str(it.entry["memory"])
        if os.environ.get("T2_WORK"):
            e["BKO_WORK"] = hostrun.host_path(os.environ["T2_WORK"])
        for k in ("VM_UPDATE_REMOTE_VMS", "VM_UPDATE_HOLT_BIN"):
            if os.environ.get(k):
                e[k] = os.environ[k]
        if mode != "console":
            e["VM_UPDATE_RAM"] = f"{it.d:g}"
        if self.update:
            e["VM_UPDATE_ROUND"] = self.rs.run
            if set(self.hosts) != set(hostjob_hosts()):   # --hosts of the word: merge on those only
                e["T2_GUEST_HOSTS"] = ",".join(f"{h}.local" for h in self.hosts)
        return e

    # --- one attempt ------------------------------------------------------------------------------
    def job_thread(self, it, host):
        try:
            self._job(it, host)
        except Exception as e:   # noqa: BLE001 - the runner's own failure is a harness result, never a lost cell
            self.log(f"{it.cell}: {type(e).__name__}: {e}")
            self.done_q.append((it, host, None, None, {}, hostjob.JobFailure(f"{type(e).__name__}: {e}"), {}))

    def _job(self, it, host):
        began = {"v": False}

        def on_began():
            with self.mu:
                if not began["v"]:
                    began["v"] = True
                    self.line[host] -= 1
        try:
            timeout = int(it.entry.get("timeout_s") or 5400)
            if it.resumed:   # an attempt an earlier process started: wait for its end, where it runs
                it.resumed = None
                rc, odir, t, why = self.reattach(self.env, host, it.guest, it.attempt, timeout, 4 * 3600, "guest", on_began=on_began)
            else:
                mode = mode_of(it.entry, self.update)
                out = hostjob.job_dir(self.env, it.guest, it.attempt, "guest")
                cells = self.write_job_cells(it, host)
                script = f"{self.env.copy_path(host).rstrip('/')}/testvm/test2/guest/job.sh"
                cmd = (f"bash {hostjob._q(script)} {mode} {shlex.quote(it.guest)} {shlex.quote(self.rs.run)} {it.attempt} {hostjob._q(out)}")
                tag = f"guest:{self.rs.run}:{it.guest}"
                with self.mu:
                    self.state["started"][it.guest] = {"host": host, "attempt": it.attempt, "d": it.d, "t": utc_now(), "prev_verdict": it.prev_verdict,
                                                       "flake": it.flake, "rerun_done": it.rerun_done, "rows": list(it.rows)}
                self.save()
                extra = self.job_env(it, cells, mode)
                rc, odir, t, why = self.job(self.env, host, it.guest, it.attempt, cmd, (it.d, it.m), tag, timeout, 4 * 3600, extra, None, "guest",
                                            on_began=on_began)
            files = self.fetch_row(host, odir) if why is None or getattr(why, "gone", False) else {}
            self.done_q.append((it, host, rc, odir, t, why, files))
        finally:
            on_began()   # whatever happened, the job isn't in cmp-run.sh's line any more

    def fetch_row(self, host, odir):
        """The job's row records; a fetch that fails reads as nothing, so try again before saying none."""
        files = {}
        for n in range(3):
            files = self.io.fetch(host, odir, ["row-summary.json", "row.id", "new-lines.json"])
            if files.get("row-summary.json"):
                break
            if n < 2:
                self.io.sleep(self.poll)
        return files

    def start(self, it, host):
        with self.mu:
            for d in (self.line, self.count, self.bigs):   # (a resumed job's host may not be this word's)
                d.setdefault(host, 0)
            self.count[host] += 1
            self.line[host] += 1
            if it.d >= self.big:
                self.bigs[host] += 1
        th = threading.Thread(target=self.job_thread, args=(it, host), daemon=True)
        self.active[th.name] = (it, host, th)
        th.start()

    def can_start(self, it, host):
        with self.mu:
            if self.count[host] >= self.max_jobs or self.line[host] > 0:
                return False
            if it.d >= self.big and self.bigs[host] > 0:
                return False
        return True

    def stopped(self, it):
        """Why IT mustn't start now, or None."""
        lost = self.holder_lost()
        if lost:
            return f"the round's fleet-lock holder is gone ({lost}): no job starts without it"
        if hostjob.tripped(self.rs):
            return "a run-wide stop rule fired first"
        if not self.rs.stops.may_start(self.pm_of(it)):
            return "a stop rule stopped the run first"
        return None

    def holder_lost(self):
        """An update round's: why its --hold-fleet-lock holder is gone, or None.  Noted once."""
        fl = getattr(self, "held_fleet", None)
        lost = getattr(fl, "lost", None) if fl is not None else None
        why = lost() if callable(lost) else None
        if not isinstance(why, str) or not why:
            return None
        if not self.holder_noted:
            self.holder_noted = True
            self.notes.append(f"the fleet-lock holder was lost mid-round ({why}); the jobs not yet started weren't run, "
                              "and nothing kept a by-hand driver out after it")
            self.log(f"fleet lock: the holder is gone ({why})")
        return why

    # --- the end of an attempt ----------------------------------------------------------------------
    def finish_job(self, it, host, rc, odir, t, why, files):
        with self.mu:
            self.count[host] -= 1
            if it.d >= self.big:
                self.bigs[host] -= 1
        summ = None
        gone = bool(getattr(why, "gone", False))   # resume: the attempt is proven gone
        if (why is None or gone) and files.get("row-summary.json"):
            try:
                summ = json.loads(files["row-summary.json"])
            except ValueError:
                summ = None
        rid = (files.get("row.id") or "").strip().splitlines()[-1:] if files else []
        rid = rid[0] if rid else None
        if summ is not None and rc not in (0, None) and not rid:
            summ = None   # job.sh's non-zero exit: the summary was written, the row wasn't
        if summ is not None:
            gone = False   # it did end, and left its row: as any job that ended
        if summ is None:
            why2 = str(why) if why is not None else f"the job ended without a row (exit {rc})"
            planned = None if self.update else (getattr(it.pc, "keys", None) or {}).get(host)
            row = harness_row(self.ctx, it.guest, host, self.rs.run, it.attempt, why2, getattr(it.pc, "rule", None) or "fresh:names",
                              word=self.word.text, planned=planned)
            rid = append_row(self.ctx, self.io, host, row, self.log)
            summ = {"verdict": "HARNESS", "class": "harness", "needs": dict(row["needs"]), "fails": list(row["fails"]), "to_class_only": False,
                    "overrun": False, "harness": why2, "tail": None}
        if rid:
            it.rows.append(rid)
        v = summ.get("verdict")
        still = bool(getattr(why, "still_running", False))
        # resume: the attempt is gone and left no row.  A new attempt, but never a second real update.
        if gone and not self.update and it.attempt < MAX_ATTEMPTS:
            it.attempt += 1
            self.log(f"{it.cell}: its job is gone and left no row: attempt {it.attempt}")
            self.pending.appendleft(it)
            return
        self.log(f"{it.cell} on {host}: attempt {it.attempt} {v}" + (f": {(summ.get('fails') or [''])[0]}" if summ.get("fails") else ""))
        # an overrun below the cap: again, larger; it feeds no stop rule
        if not self.update and summ.get("overrun") and it.d < self.cap and it.attempt < MAX_ATTEMPTS and not still:
            it.d = min(self.cap, round(it.d * 2, 1))
            it.attempt += 1
            it.prev_verdict = "FAIL" if it.prev_verdict == "FAIL" else "HARNESS"   # a FAIL before it still counts
            self.log(f"{it.cell}: overrun: attempt {it.attempt} with D {it.d:g}")
            self.pending.appendleft(it)
            return
        # one FAIL rerun, try and probe modes only
        if mode_of(it.entry, self.update) in ("try", "probe") and v == "FAIL" and not summ.get("to_class_only") and not it.rerun_done \
                and it.attempt < MAX_ATTEMPTS and not still:
            it.rerun_done = True
            it.prev_verdict = "FAIL"
            it.flake = J.match_flake(it.guest, summ.get("tail") or [], self.flakes)
            it.attempt += 1
            self.pending.appendleft(it)
            return
        self.finalize(it, host, summ, t)

    def finalize(self, it, host, summ, t):
        pc = it.pc
        v, klass = summ.get("verdict"), summ.get("class")
        entry = {"cell": it.cell, "outcome": "ran", "rule": getattr(pc, "rule", None), "reasons": list(getattr(pc, "reasons", None) or []),
                 "rows": list(it.rows), "verdict": v, "class": klass, "gm": round((summ.get("wall_s") or 0) / 60, 2),
                 "wall_s": summ.get("wall_s") or 0, "gb": None, "host": host, "needs": summ.get("needs") or {}, "fails": summ.get("fails") or [],
                 "note": summ.get("harness"), "to_class_only": bool(summ.get("to_class_only")), "merged": bool(summ.get("merged")), "attempt": it.attempt,
                 "merge_why": summ.get("merge_why"), "steps": summ.get("steps"), "fingerprint": summ.get("fingerprint"),
                 "pm": self.pm_of(it), "skips": summ.get("skips") or {}, "need_notes": summ.get("notes") or {},
                 "generation_before": summ.get("generation_before"), "generation_after": summ.get("generation_after")}
        self.cells.append(entry)
        with self.mu:
            self.state["ended"][it.guest] = entry
            self.state["started"].pop(it.guest, None)
        self.save()
        self.feed(it, host, summ)

    def feed(self, it, host, summ):
        """The stop rules: never a to-be-classed row, nor an overrun below the cap (only one at
        the cap counts); only FAIL needs, so a standing LIMIT-NEW doesn't stop a family."""
        if summ.get("to_class_only") or (summ.get("overrun") and it.d < self.cap):
            return
        res = {"cell": it.cell, "family": "guest", "part": None, "verdict": summ.get("verdict"), "class": summ.get("class"),
               "needs": J.only_fail_needs(summ.get("needs")), "os": self.pm_of(it), "host": host,
               "flake": bool(it.flake) and summ.get("verdict") == "PASS"}
        try:
            self.rs.stops.feed(res)
        except Exception as e:   # noqa: BLE001
            self.log(f"stop rules: {e}")

    def take_lines(self):
        """Each guest's new lines onto the Mac: MAC_ROOT/runs/RUN/guest-lines/G.{json,txt}."""
        from . import run as runmod
        d = os.path.join(self.rs.mac_dir, "guest-lines")
        for c in self.cells:
            g = c["cell"].split(":", 1)[1]
            if not c.get("host") or not c.get("attempt"):
                continue
            f = self.io.fetch(c["host"], hostjob.job_dir(self.env, g, c["attempt"], "guest"), ["new-lines.json"])
            if not f.get("new-lines.json"):
                continue
            try:
                doc = json.loads(f["new-lines.json"])
            except ValueError:
                continue
            runmod.write_json(os.path.join(d, f"{g}.json"), doc)
            lines = [("[seen before] " if x.get("seen_before") else "") + f"{x.get('source')}: {x.get('line')}" for x in doc.get("lines") or []]
            runmod.write_text(os.path.join(d, f"{g}.txt"), "\n".join(lines) + ("\n" if lines else ""))

    # --- the main loop -----------------------------------------------------------------------------
    def host_checks(self):
        now = self.clock()
        if now - self.last_checks < self.poll:
            return
        self.last_checks = now
        self.env.swap0 = self.state.setdefault("swap0", self.env.swap0 or {})
        hostjob.host_checks(self.env, self.rs, f"guest:{self.rs.run}", self.t0)

    def drive(self):
        while self.pending or self.active or self.done_q:
            while self.done_q:
                rec = self.done_q.popleft()
                self.finish_job(*rec)
            for th_name in [n for n, (_i, _h, th) in self.active.items() if not th.is_alive()]:
                self.active.pop(th_name, None)
            # the queue: cells a stop rule took out are reported as not run
            still = collections.deque()
            while self.pending:
                it = self.pending.popleft()
                why = self.stopped(it)
                if why:
                    self.skip(it, why)
                else:
                    still.append(it)
            self.pending = still
            for host in self.hosts:
                for it in list(self.pending):
                    if host in it.allowed and self.can_start(it, host):
                        self.pending.remove(it)
                        self.start(it, host)
                        break
            self.host_checks()
            if self.pending or self.active or self.done_q:
                if not self.done_q:
                    self.env.io.sleep(self.poll)

    def skip(self, it, why):
        pc = it.pc
        self.skipped.append({"cell": it.cell, "outcome": "skipped", "rule": getattr(pc, "rule", None), "reasons": [why], "rows": [], "verdict": None,
                             "class": None, "gm": 0.0, "wall_s": 0, "gb": None, "host": None, "needs": {}, "fails": [], "note": why})
        with self.mu:
            self.state.setdefault("skipped", {})[it.guest] = why
        self.save()

    # --- update round ------------------------------------------------------------------------------
    def tree_matches(self, host):
        """Contract 2.1 update step 1: the synced tree's vm-update, update/* and test2/guest/* are this checkout's,
        and so are t2-host, t2lib/* and classes.json, which the merge gate and the rows run from that tree
        (job.sh's --merge-gate: t2-host tool guest gate, guest_judge, rows_guest)."""
        from . import copies
        paths = list(TREE_PATHS)
        local = copies.tree_hashes(self.ctx.checkout, paths)
        envs = " ".join(f"{k}={hostrun.rq(v)}" for k, v in sorted(host_env().items()))
        tool = hostrun.rq(self.work.rstrip("/") + "/testvm/test2/t2-host")
        rc, text, _ = self.io.sh(host, f"env {envs} python3 -B {tool} hash --root {hostrun.rq(self.work)} " + " ".join(paths) + " </dev/null", timeout=120)
        got = _json_out(text) if rc == 0 else None
        if not isinstance(got, dict):
            return f"can't read the synced tree's hashes on {host} (t2-host hash exit {rc})"
        if got.get("files") != local:
            bad = sorted(set(local.items()) ^ set((got.get("files") or {}).items()))
            return "the synced tree differs from this checkout: " + ", ".join(sorted({k for k, _ in bad})[:6])
        return None

    def reproject_if_needed(self, host, items):
        rules_now = fingerprint_rules(self.ctx.checkout)
        stale = sorted({it.guest for it in items for h in self.hosts
                        if ((((self.ctx.facts.get(h) or {}).get("guests") or {}).get(it.guest) or {}).get("fingerprint") or {}).get("rules")
                        not in (None, rules_now)})
        if not stale or rules_now is None:
            return
        self.log("fingerprint rules changed: reprojecting " + ", ".join(stale))
        envs = " ".join(f"{k}={hostrun.rq(v)}" for k, v in sorted(vm_update_env().items()))
        tool = hostrun.rq(self.work.rstrip("/") + "/testvm/vm-update")
        rc, _, err = self.io.sh(host, f"env {envs} BKO_WORK={hostrun.rq(self.work)} {tool} --reproject " + " ".join(stale), timeout=1800)
        if rc != 0:
            self.notes.append(f"vm-update --reproject exit {rc}: {str(err).strip()[-200:]}")

    def waves(self, items):
        """Wave 1: the cheapest guest of each package manager; wave 2: the rest."""
        best = {}
        for it in items:
            gm = float((getattr(it.pc, "est", None) or {}).get("gm") or 0.0)
            p = self.pm_of(it)
            if p not in best or (gm, it.guest) < best[p][0]:
                best[p] = ((gm, it.guest), it)
        first = [v[1] for _k, v in sorted(best.items())]
        return first, [it for it in items if it not in first]

    def run_update(self, items):
        host = FIRST_HOST if FIRST_HOST in self.hosts else self.hosts[0]
        bad = self.tree_matches(host)
        if bad:
            self.notes.append("GuestTest2 update refused: " + bad)
            return 2
        self.reproject_if_needed(host, items)
        fleet = self.fleet or FleetLock(host, self.work, self.rs.run)
        if not fleet.take():
            self.notes.append("GuestTest2 update refused: the fleet lock is held (vm-update --hold-fleet-lock)")
            return 2
        self.held_fleet = fleet
        try:
            items = self.adopt(items)
            w1, w2 = self.waves(items)
            first = self.state.get("wave1")
            if first is None:   # (a resume keeps the first process's waves)
                first = self.state["wave1"] = [it.guest for it in w1]
                self.save()
            else:
                w1, w2 = [it for it in items if it.guest in first], [it for it in items if it.guest not in first]
            self.pending.extend(w1)
            self.drive()
            # Only wave 1's guests decide; one that never ran proves nothing, so it blocks.
            done = {c.get("cell"): c for c in self.cells}
            blocked = {(self.sf.entry(g).get("checks") or {}).get("pm") for g in first
                       if done.get(f"guest:{g}", {}).get("verdict") != "PASS" and not done.get(f"guest:{g}", {}).get("to_class_only")}
            for it in w2:
                if self.pm_of(it) in blocked:
                    self.skip(it, f"its package manager's wave-1 guest failed or was blocked ({self.pm_of(it)})")
                else:
                    self.pending.append(it)
            self.drive()
        finally:
            self.held_fleet = None
            fleet.release()
        return 1 if self.holder_noted else 0

    def update_block(self):
        lines = ["update round:"]
        before = {}
        for h in self.hosts:
            for g, v in ((self.ctx.facts.get(h) or {}).get("guests") or {}).items():
                before[g] = ((v.get("fingerprint") or {}).get("holt"))
        for c in sorted(self.cells, key=lambda c: c["cell"]):
            g = c["cell"].split(":", 1)[1]
            fp = c.get("fingerprint")
            fps = "fingerprint unchanged" if fp and fp == before.get(g) else ("fingerprint moved" if fp else "no fingerprint")
            steps = c.get("steps") or []
            lines.append(f"  {g}: " + ("merged" if c.get("merged") else "not merged" + (f" ({c.get('merge_why')})" if c.get("merge_why") else ""))
                         + (f"; steps: {', '.join(map(str, steps))}" if steps else "") + f"; {fps}" + self.changed_text(g, c))
        for s in self.skipped:
            lines.append(f"  {s['cell'].split(':', 1)[1]}: not run: {s['reasons'][0]}")
        return "\n".join(lines)

    def changed_text(self, guest_name, c):
        """What a merged update changed, from the two generations' package lists (they stay on the host)."""
        a, b = c.get("generation_before"), c.get("generation_after")
        if not c.get("merged") or not a or not b or a == b:
            return ""
        try:
            rc, text = remote_tool(self.io, c.get("host"), ["pkgdiff", guest_name, a, b], timeout=60)
        except Exception as e:   # noqa: BLE001 - the report only loses a detail
            return f"; changed: unknown ({e})"
        d = _json_out(text) if rc == 0 else None
        if not isinstance(d, dict):
            return "; changed: no earlier package list"
        names = d.get("changed", []) + d.get("added", []) + d.get("removed", [])
        return (f"; changed {len(d.get('changed', []))}, added {len(d.get('added', []))}, removed {len(d.get('removed', []))}"
                + (": " + ", ".join(names[:8]) + (", ..." if len(names) > 8 else "") if names else ""))

    def skips_block(self):
        """Why needs were SKIP, and the PASS notes (contract 1.3, 2.1), by reason."""
        by = collections.defaultdict(list)
        for c in self.cells:
            g = c["cell"].split(":", 1)[1]
            for n, why in sorted((c.get("skips") or {}).items()):
                by[f"{n} SKIP: {why}"].append(g)
            for n, why in sorted((c.get("need_notes") or {}).items()):
                if n in ("image:free", "logs"):   # (the others only say which version)
                    by[f"{n}: {why}"].append(g)
        if not by:
            return None
        return "\n".join(["skips and notes:"] + [f"  {k}: {', '.join(sorted(v))}" for k, v in sorted(by.items())])

    # --- grades ------------------------------------------------------------------------------------
    def grades_block(self, rows):
        names = [pc.cell.split(":", 1)[1] for pc in self.plan.cells if str(pc.cell).startswith("guest:")]
        stic_st, holt_st = self.other_statuses()
        pc_of = {pc.cell: pc for pc in self.plan.cells}
        lines = ["grades:"]
        checks_dir = os.path.join(self.ctx.checkout, "testvm", "stictest1")
        for g in sorted(names):
            cell = f"guest:{g}"
            pc = pc_of.get(cell)
            keys_now = set((getattr(pc, "keys", None) or {}).values())
            mine = [r for r in rows if r.get("cell") == cell and r.get("verdict") in ("PASS", "FAIL", "FLAKY-FAIL")
                    and r.get("valid") is True and not (r.get("extra") or {}).get("merged")]
            at_key = [r for r in mine if not keys_now or r.get("key") in keys_now]
            row = max(at_key, key=lambda r: (r.get("t") or "", r.get("id") or ""), default=None)
            has_check = os.path.exists(os.path.join(checks_dir, g, "check.sh"))
            gen = self.generation(g)
            gen_pass = any(r.get("verdict") == "PASS" and (gen is None or gen in json.dumps((r.get("inputs") or {}).get("guest") or {})) for r in mine)
            sp, hp = stic_st.get(g), holt_st.get(g)
            backup_ok = sp in ("current", "soft-stale", "canary-verified") and hp in ("current", "soft-stale", "canary-verified")
            lines.append(f"  {g:<20} {J.grade(g, row, has_check, backup_ok, gen_pass):<13} {self.backup_col(rows, g)}")
        return "\n".join(lines)

    def backup_col(self, rows, g):
        out = []
        for fam, cell in (("stic", f"stic:{g}"), ("holt", f"holt:{g}")):
            rs = [r for r in rows if r.get("cell") == cell and r.get("verdict") in ("PASS", "FAIL", "FLAKY-FAIL") and r.get("valid") is True]
            r = max(rs, key=lambda x: (x.get("t") or "", x.get("id") or ""), default=None)
            out.append(f"{fam} {r['verdict']} {str(r.get('t'))[:10]}" if r else f"{fam} none")
        return "  ".join(out)

    def generation(self, g):
        for h in self.hosts:
            v = ((self.ctx.facts.get(h) or {}).get("guests") or {}).get(g) or {}
            if v.get("generation"):
                return v["generation"]
        return None

    def grade_facts(self):
        """Facts a stic and a holt plan can be made from: a guest word's hold only what its own
        keys need.  As t2's full_ctx: those families' wants, then the round at the fork heads."""
        probe = getattr(self.io, "facts", None)
        if probe is None:
            return self.ctx.facts
        import copy
        from . import keys, plan as planmod, words
        specs = getattr(self.ctx, "specs", None) or keys.Specs(keys.TreeSource(self.ctx.checkout))
        want = keys.wants(specs, ("stic", "holt", "gate"))

        def ask():
            return {h: f for h, f in (probe(self.hosts, want) or {}).items() if f and f.get("ok") is not False}
        facts = ask()
        c = copy.copy(self.ctx)
        c.facts = facts
        heads = planmod.Planner(words.Word("stic", "SticTest2"), c).fork_heads()
        names = {"round" + n for f in facts.values() for n, e in (f.get("rounds") or {}).items() if planmod.round_commits(e) == heads}
        if any(n not in (f.get("sets") or {}) for n in names for f in facts.values()):
            want["sets"] = sorted(set(want["sets"]) | names)
            facts.update(ask())
        return facts

    def other_statuses(self):
        """{guest: status} of the stic and holt plans; empty on any failure (backup then not all PASS, and a note says why)."""
        import copy
        from . import plan as planmod, words
        out = []
        try:
            facts = self.grade_facts()
        except Exception as e:   # noqa: BLE001
            self.notes.append(f"grades: no facts for the stic and holt plans ({type(e).__name__}: {e})")
            return {}, {}
        for fam, word in (("stic", "SticTest2"), ("holt", "HoltTest2")):
            st = {}
            try:
                c = copy.copy(self.ctx)
                c.facts = facts
                c.options = {k: v for k, v in (getattr(self.ctx, "options", None) or {}).items() if k not in ("universe", "extra_due")}
                p = planmod.make(words.Word(fam, word), c)
                for pc in p.cells:
                    name = pc.cell.split(":", 1)[1]
                    if "+" not in name:
                        st[name] = pc.status
                if st and all(v == "not-runnable" for v in st.values()):
                    self.notes.append(f"grades: every {fam} cell is not-runnable: backup can't be judged")
            except Exception as e:   # noqa: BLE001 - grades then say usable, and why
                self.notes.append(f"grades: no {fam} plan ({type(e).__name__}: {e})")
            out.append(st)
        return out

    # --- the whole run -----------------------------------------------------------------------------
    def result(self, start, ex=None):
        rs = self.rs
        cells = sorted(self.cells, key=lambda c: c["cell"]) + self.skipped
        if ex is None:
            bad = any(c.get("verdict") in FAILING or c.get("class") == "lost" for c in cells)
            ex = 1 if bad or rs.stops.stops else 0
        return {"run": rs.run, "word": self.word.word, "family": "guest", "start": start, "end": utc_now(), "cells": cells,
                "stops": list(rs.stops.stops), "notes": self.notes, "wear": {}, "hosts": sorted({c["host"] for c in self.cells if c.get("host")}),
                "copy": next((c.get("id") for c in (getattr(rs, "copy", None) or {}).values() if isinstance(c, dict) and c.get("id")), None),
                "blocks": self.blocks, "exit": ex}

    def pull(self):
        pull = (getattr(self.ctx, "options", None) or {}).get("pull")
        if pull:
            try:
                return pull() or self.ctx.ledger
            except Exception as e:   # noqa: BLE001
                self.notes.append(f"pull after the run failed: {e}")
        return self.ctx.ledger

    def identity_entries(self, items):
        """Step 1.  Failing guests: an entry, no job.  Returns the items that may run."""
        guests = [it.guest for it in items]
        res = identity_check(self.ctx, guests, sha256=bool(getattr(self.word, "all", False)), run=self.rs.run, io=self.io, log=self.log)
        text = json.dumps(res, indent=1, sort_keys=True).encode()
        for h in self.hosts:   # HOST_ROOT/runs/RUN/identity.json
            d = f"{self.host_root.rstrip('/')}/runs/{self.rs.run}"
            self.io.sh(h, f"mkdir -p {hostrun.rq(d)} && cat > {hostrun.rq(d + '/identity.json')}", input=text, timeout=60)
        out = []
        for it in items:
            r = res.get(it.guest) or {"ok": False, "why": "identity not checked", "harness": True}
            if r["ok"]:
                out.append(it)
                continue
            why = str(r["why"])
            host = r.get("host") or it.allowed[0]
            if r.get("harness"):   # a host that didn't answer: the harness's, and no job either
                row = harness_row(self.ctx, it.guest, host, self.rs.run, 1, why, "guest:identity", word=self.word.text, reasons=[why])
                rid = append_row(self.ctx, self.io, host, row, self.log)
                v, klass, needs, fails = "HARNESS", "harness", {"harness": "FAIL"}, [f"harness: {why}"]
            else:
                rid, v, klass, needs, fails = r.get("row"), "FAIL", "fail", {"identity": "FAIL"}, [f"identity: {why}"]
            entry = {"cell": it.cell, "outcome": "ran", "rule": "guest:identity", "reasons": [why[:200]], "rows": [rid] if rid else [], "verdict": v,
                     "class": klass, "gm": 0.0, "wall_s": 0, "gb": None, "host": host, "needs": needs, "fails": fails, "note": why, "pm": self.pm_of(it)}
            self.cells.append(entry)
            with self.mu:   # a resume must not run it, and reports it
                self.state["ended"][it.guest] = entry
        self.save()
        return out

    def adopt(self, items):
        """Resume: an attempt an earlier process started is waited for on its own host, before
        anything new starts.  The items left to start."""
        rest = []
        for it in items:
            if it.resumed:
                self.start(it, it.resumed.get("host") or it.allowed[0])
            else:
                rest.append(it)
        return rest

    def run(self, resume=False):
        start = utc_now()
        if not resume:
            try:
                self.rs.write_cells(self.plan)
            except Exception as e:   # noqa: BLE001 - job cells carry what the rows need
                self.notes.append(f"cells.json not written: {e}")
        items = [self.item_of(pc) for pc in self.due()]
        ended = self.state.get("ended") or {}
        if resume:
            for g, c in ended.items():
                self.cells.append(c)
            for it in items:
                why = (self.state.get("skipped") or {}).get(it.guest)
                if why and it.guest not in ended:   # (still reported)
                    self.skip(it, why)
            items = [it for it in items if it.guest not in ended and it.guest not in (self.state.get("skipped") or {})]
            for it in items:
                st = (self.state.get("started") or {}).get(it.guest)
                if st:   # a job that started and whose end wasn't seen: adopt it (adopt, hostjob.reattach_job)
                    it.attempt = int(st.get("attempt") or 1)
                    it.d = float(st.get("d") or it.d)
                    it.prev_verdict, it.flake, it.rerun_done = st.get("prev_verdict"), st.get("flake"), bool(st.get("rerun_done"))
                    it.rows = list(st.get("rows") or [])
                    it.resumed = st
        self.upload_baselines([it.guest for it in items])
        ex = None
        if not resume or not self.state.get("identity_done"):
            items = self.identity_entries(items)
            with self.mu:
                self.state["identity_done"] = True
            self.save()
        if self.update:
            ex = self.run_update(items)
            ex = ex if ex else None
        else:
            self.pending.extend(self.adopt(items))
            self.drive()
        self.take_lines()
        rows = self.pull()
        try:
            if self.update:
                self.blocks.append(self.update_block())
            self.blocks.append(self.grades_block(rows))
            sk = self.skips_block()
            if sk:
                self.blocks.append(sk)
        except Exception as e:   # noqa: BLE001 - the report says it
            self.notes.append(f"no grades: {type(e).__name__}: {e}")
        return self.result(start, ex)


def fingerprint_rules(checkout):
    """The fingerprint rules' sha256: of the two files' sha256 hex, fingerprint.py's first."""
    from . import file_sha256, sha256_hex
    a = file_sha256(os.path.join(checkout, "testvm", "update", "fingerprint.py"))
    b = file_sha256(os.path.join(checkout, "testvm", "holttest1d", "judge.py"))
    return sha256_hex(a + b) if a and b else None


def run(ctx, plan, io=None, log=None, **kw):
    return Runner(ctx, plan, io=io, log=log, **kw).run()


def resume(ctx, plan, io=None, log=None, **kw):
    return Runner(ctx, plan, io=io, log=log, **kw).run(resume=True)


# --- t2 guest-lines -----------------------------------------------------------------------------------

def _rows():
    from . import ledger
    try:
        ledger.pull(list(hostjob_hosts()))
    except Exception as e:   # noqa: BLE001 - the mirrors then are what they are
        print(f"t2: pull failed: {e}")
    return ledger.view().rows


def newest_row(rows, run, guest):
    """The newest FAIL-or-other run row of RUN for GUEST."""
    mine = [r for r in rows if r.get("cell") == f"guest:{guest}" and r.get("kind") == "run" and r.get("run") == run]
    return max(mine, key=lambda r: (r.get("attempt") or 0, r.get("t") or ""), default=None)


def unmet(row):
    """(names of needs other than logs not met, whether logs is the row's only unmet need)."""
    needs = row.get("needs") or {}
    others = [n for n, v in needs.items() if n != "logs" and v not in ("PASS", "SKIP", "LIMIT")]
    return others, needs.get("logs") == "DIFF"


def class_row(row, mac=None):
    """The kind: class row an accepted row's lines make: verdict PASS, logs: PASS."""
    from . import ledger
    needs = dict(row.get("needs") or {}, logs="PASS")
    r = dict(row)
    for k in ("id", "c", "t", "host"):
        r.pop(k, None)
    r.update(origin=f"class:{row.get('id')}", kind="class", rule="guest:lines-classed", via=row.get("id"), verdict="PASS", needs=needs,
             counts={}, fails=[], reasons=["its new log lines were read and accepted"], attempt=row.get("attempt") or 1, start=None,
             planned_key=None, valid=True, note=None, extra=dict(row.get("extra") or {}, to_class_only=False))
    r["class"] = "pass"
    return ledger.complete(r, host="mac")


def accept(rows, run, guest, lines_doc, mac, out=print):
    """--accept GUEST: (True, class row) or (False, why).  Refused unless logs is the only unmet need
    of a valid FAIL that is to_class_only: a HARNESS or an invalid row never becomes a PASS."""
    row = newest_row(rows, run, guest)
    if row is None:
        return False, f"no row of {guest} in {run}"
    others, diff = unmet(row)
    if not diff:
        return False, f"{guest}: its row has logs: {(row.get('needs') or {}).get('logs')}, nothing to accept"
    if others:
        return False, f"{guest}: other needs aren't met: {', '.join(sorted(others)[:6])}"
    if row.get("verdict") != "FAIL" or row.get("valid") is not True or not (row.get("extra") or {}).get("to_class_only"):
        return False, (f"{guest}: its row is {row.get('verdict')}{'' if row.get('valid') is True else ', invalid'}: "
                       "only a valid FAIL with nothing but lines to class can be accepted")
    if not isinstance(lines_doc, dict) or lines_doc.get("lines") is None:
        return False, f"{guest}: no guest-lines/{guest}.json in {run}"
    from . import run as runmod
    p = os.path.join(mac, "guest-baselines", f"{guest}.json")
    cur = runmod.read_json(p, {}) or {}
    have = set(cur.get("lines") or [])
    if cur.get("builder") and lines_doc.get("builder") and cur["builder"] != lines_doc["builder"]:
        have = set()
    have |= {x.get("line") for x in lines_doc.get("lines") or [] if x.get("line")}
    runmod.write_json(p, {"v": 1, "guest": guest, "builder": lines_doc.get("builder"), "lines": sorted(have)})
    return True, class_row(row, mac)


def cmd_lines(argv, out=print):
    """t2 guest-lines RUN [--show [GUEST]] [--accept GUEST...|--accept-all]."""
    from . import ledger, mac_ledger_file
    from . import run as runmod
    args = list(argv)
    if not args or args[0].startswith("--"):
        out("usage: t2 guest-lines RUN [--show [GUEST]] [--accept GUEST...|--accept-all]")
        return 2
    run_id, args = args[0], args[1:]
    mac = mac_root()
    d = os.path.join(runmod.run_dir(run_id, mac), "guest-lines")
    if not args or args[0] == "--show":
        names = args[1:] if len(args) > 1 else sorted(n[:-5] for n in os.listdir(d) if n.endswith(".json")) if os.path.isdir(d) else []
        for g in names:
            doc = runmod.read_json(os.path.join(d, f"{g}.json"), {}) or {}
            for x in doc.get("lines") or []:
                out(f"{g}: {'[seen before] ' if x.get('seen_before') else ''}{x.get('source')}: {x.get('line')}")
        return 0
    rows = _rows()
    names, all_ = [], False
    if args[0] == "--accept-all":
        all_ = True
        names = sorted(n[:-5] for n in os.listdir(d) if n.endswith(".json")) if os.path.isdir(d) else []
    elif args[0] == "--accept" and len(args) > 1:
        names = args[1:]
    else:
        out("usage: t2 guest-lines RUN [--show [GUEST]] [--accept GUEST...|--accept-all]")
        return 2
    rc, made = 0, []
    for g in names:
        ok, what = accept(rows, run_id, g, runmod.read_json(os.path.join(d, f"{g}.json")), mac, out)
        if ok:
            made.append(what)
            out(f"accepted: {g}")
        elif all_:
            out(f"skipped: {what}")
        else:
            out(f"refused: {what}")
            rc = 2
    if made:
        for r in made:
            ledger.check(r)
        ledger.append(mac_ledger_file(), made)
    return rc


# --- t2 guest-fingerprints -----------------------------------------------------------------------------

def cmd_fingerprints(argv, out=print, io=None):
    """t2 guest-fingerprints [GUEST...] [--reproject]: serial vm-update --probe --write-fingerprint on
    Chopin for guests with image-state and no fingerprint at their generation (--reproject: for those
    with a sidecar).  Never inside BkoTest2: a write during a run invalidates rows."""
    from . import facts as factsmod
    from . import run as runmod
    args = list(argv)
    reproject = "--reproject" in args
    args = [a for a in args if a != "--reproject"]
    io = io or hostjob.HostIO()
    host = FIRST_HOST
    f = factsmod.probe(host, {}, save=False)
    if not f.get("ok"):
        out(f"{host}: no facts: {f.get('error')}")
        return 2
    gs = f.get("guests") or {}
    names = args or sorted(gs)
    work = hostrun.host_path(os.environ.get("T2_WORK") or "~/backotter-work")
    envs = " ".join(f"{k}={hostrun.rq(v)}" for k, v in sorted(vm_update_env().items()))
    tool = hostrun.rq(work.rstrip("/") + "/testvm/vm-update")
    if reproject:
        which = [g for g in names if (gs.get(g) or {}).get("fingerprint")]
        if not which:
            out("no guest has a sidecar")
            return 0
        rc, text, err = io.sh(host, f"env {envs} BKO_WORK={hostrun.rq(work)} {tool} --reproject " + " ".join(shlex.quote(g) for g in which), timeout=3600)
        out((text or "").rstrip())
        if rc:
            out(f"--reproject exit {rc}: {str(err).strip()[-300:]}")
        return 1 if rc else 0
    which = [g for g in names if (gs.get(g) or {}).get("generation") and not (gs.get(g) or {}).get("fingerprint")]
    run_id = runmod.new_run_id("guest")
    pol = run_policy(None)
    sf = None
    try:
        from .plan import load_json
        pol = run_policy(load_json(os.path.join(hostjob_checkout(), "testvm", "test2", "classes.json")))
        sf = rows_guest.load_spec(hostjob_checkout())
    except Exception:   # noqa: BLE001
        pass
    bad = 0
    for g in which:
        entry = sf.entry(g) if sf is not None and g in sf.names() else {}
        _d, m = reserve_for([], f"guest:{g}", entry, pol)
        mem = entry.get("memory")
        cpus = entry.get("cpus")
        cmd = (f"cd {hostrun.rq(work)} && WEAR_TAG=guest:{run_id}:{g} ~/backotter-work/cmp-run.sh --reserve 2 {m} env {envs} "
               f"VM_UPDATE_RAM=2 BKO_WORK={hostrun.rq(work)} {tool} --probe --write-fingerprint"
               + (f" --memory {mem}" if mem else "") + (f" --cpus {cpus}" if cpus else "") + f" {shlex.quote(g)}")
        out(f"{g}: probing")
        rc, text, err = io.sh(host, cmd, timeout=int(entry.get("timeout_s") or 3600))
        if rc != 0:
            bad += 1
            out(f"{g}: exit {rc}: {(str(text) + str(err)).strip()[-300:]}")
    try:
        rc2, _ = remote_tool(io, host, ["prune-dumps"], timeout=120)
    except Exception as e:   # noqa: BLE001 - housekeeping
        out(f"prune-dumps: {e}")
    out(f"{len(which) - bad} of {len(which)} guests have a fingerprint now")
    return 1 if bad else 0


def hostjob_checkout():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def hostjob_hosts():
    """The fleet vm-update keeps alike (its ALL_HOSTS): the default hosts."""
    return DEFAULT_HOSTS
