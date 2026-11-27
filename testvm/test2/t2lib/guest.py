"""GuestTest2: every test guest's health, read only but for `GuestTest2 update` (contract 2.1, K3).

    run(ctx, plan), resume(ctx, plan)       run.py's runner API
    pre_plan(ctx, word, out)                image sums, identity of disks that moved; once, before the plan
    preflight(ctx, out)                     what a killed run left (t2-host tool guest sweep)
    identity_check(ctx, guests, sha256=False, run=None) -> {G: {ok, why}}
                                            step 1; a failing guest gets its FAIL row, under RUN
    cmd_lines(argv), cmd_fingerprints(argv) t2 guest-lines, t2 guest-fingerprints

A pool per host on the Mac, one shared list, longest estimated wall first; each job is guest/job.sh through hostjob.host_job,
under cmp-run.sh --reserve D M, WEAR_TAG=guest:RUN:G.  The pool's rules: at most 3 jobs a host (2 in an
update round), none started while one of that host's is still in cmp-run.sh's line, and jobs of D >=
big_gib one at a time, and none on a host the swap rule or the host rule holds (run.py's HostHolds and
HostWatch: the job waits for another host it may run on, or for the hold to end), nor a job that downloads (NET_MODES) anywhere
while the network rule holds (NetHold: a probe of the farm's internet under net_min_kibs).  An overrun below the cap reruns at rerun_d (2 x D or 2 x its peak) on a host that fits it, and feeds no stop rule.  A FAIL that
isn't to_class_only reruns once (try and probe), never in an update.  A HARNESS or LOST attempt whose host went
away under it (hostjob.host_lost: the host rule) is host-lost: requeued once, to a host it may run on (not that
one while its job may still run there), never in an update, and no stop rule counts it; lost again on another
host, it counts once as a harness result (StopEngine's lost_again).  A guest whose log baseline wasn't
written on a host runs on the others that may run it (placed_by_baseline); one that ran there anyway says so
in its cell (no_baseline) and a note.  MAC_ROOT/runs/RUN/guest.json
records what started and what ended: resume adopts an attempt that was started (hostjob.reattach_job)
and starts another only once that one is proven gone with no row, never in an update round.
"""

import collections
import json
import math
import os
import re
import shlex
import subprocess
import threading
import time

from . import facts as factsmod
from . import guest_judge as J
from . import DEFAULT_HOSTS, hostjob, hostrun, mac_root, rows_guest, tool_guest, utc_now

POLL_S = 15
MAX_ATTEMPTS = 3
PUT_TRIES = 3     # put_file: a host file's write, tried this often (an ssh that failed, 2026-11-20)
PUT_WAIT_S = 5    # and this long times the try between (io.sleep)
JOBS_PER_HOST = 3
UPDATE_JOBS = 2
UPDATE_MIN_D = 6
RUN_DEFAULTS = {"try_days": 7, "ram_gib_first": 8, "ram_gib_max": 16, "big_gib": 13, "failed_overlay_mib": 1024,
                "failed_keep_days": 14}
FAILING = ("FAIL", "FLAKY-FAIL", "HARNESS", "LOST")


def first_host():
    """The host a guest update and vm-update's fleet lock run on: the first with the fleet
    role in the host table (Chopin since 2026-11-13; vm-update's own ALL_HOSTS[0] is the
    same row), else the first default host when the table names no fleet host.  Not a name
    here: a run's --hosts may hold hosts that have no fleet.  A table that can't be read or
    doesn't check raises hosts.HostsError rather than quietly moving the update and the fleet
    lock to the first default host (t2lib's own import reads the table too, so this is the
    same refusal, not a new one).  Read at call time, never frozen at import: a test that
    repoints BKO_HOSTS_JSON gets the table it set."""
    from . import hosts as hosts_table
    fleet = hosts_table.with_role("fleet")
    return fleet[0] if fleet else DEFAULT_HOSTS[0]


def __getattr__(name):
    """guest.FIRST_HOST, the name this module had for first_host()'s answer while it was a
    constant (bko.py still reads it that way): the attribute hook (PEP 562) asks the table
    now.  A use inside this module calls first_host(), which the hook never sees."""
    if name == "FIRST_HOST":
        return first_host()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


KIND_MODE = {"ssh": "try", "probe": "probe", "redox": "console", "sculpt": "console"}
# An update round's job: vm-update's real form, but for Sculpt (no SSH) testvm/sculpt-update
# (guest/update-sculpt.sh), which boots its update before it merges.
KIND_UPDATE_MODE = {"sculpt": "console-update"}


def run_policy(classes):
    p = ((classes or {}).get("policy") or {}).get("guest") or {}
    return dict(RUN_DEFAULTS, **(p.get("run") or {}))


def mode_of(entry, update=False):
    if update:
        return KIND_UPDATE_MODE.get(entry.get("kind"), "update")
    return KIND_MODE.get(entry.get("kind"), "try")


# vm-update's phases from the update's end on (result.json's "phase" where it stopped): a job that
# failed in one of these had run its whole update, so its RAM peak measures what the update needs.
UPDATE_ENDED = ("reboot", "snapd", "after-hook", "gate", "dump", "trim", "poweroff", "ship", "done")
# ... and the phases of a job that got as far as running its update (reserve_for's HARNESS floors).
UPDATE_REACHED = ("update", "reboot-first") + UPDATE_ENDED
_PHASE_RE = re.compile(r"\bin phase (\S+)")


def _cut_short(r):
    """Whether row R's job stopped before its update ended, so its RAM peak is only a floor of
    what the update needs: an overrun (vm-update stopped it at its limit), or a try that failed
    in or before the update (a download that failed for good: endeavouros's 1.4 GiB rows of
    2026-11-17, whose mirror stalled two thirds into a 2.2 GiB download, sized the next run's D
    at 1.9 GiB for an update that needs 6 and more).  The phase is extra.phase, or (rows before
    it was recorded) the "in phase P" of the try's fail line; none known is a floor too."""
    x = r.get("extra") or {}
    if x.get("overrun"):
        return True
    if (r.get("needs") or {}).get("try") != "FAIL":
        return False
    phase = x.get("phase")
    if not phase:
        m = next((m for m in (_PHASE_RE.search(str(f)) for f in r.get("fails") or ()) if m), None)
        phase = m.group(1) if m else None
    return phase not in UPDATE_ENDED


def reserve_for(rows, cell, entry, policy, update=False):
    """(D, M) of a job's --reserve (contract 2.1): D from the last 3 rows' RAM peaks (1.1 x the
    largest + 0.25 GiB), else the spec's ram_gib, else ram_gib_first, never over ram_gib_max; M
    the guest's RAM + 1 GiB.  A row whose job was cut short (_cut_short: an overrun, or an update
    that failed part way) measures nothing: its peak is a floor D never goes under, and it counts
    only while no row measured since says what the whole update took, a rerun in the same run
    aside: that is the same day's update again, whose peak varies (zorin18's NILFS2 root grows
    its overlay with the run's length, not the update's size: 4.54 GiB at the overrun of a slow-
    mirror attempt, 3.5 at its rerun's end, t2v-20261118T1426-76be), so the larger counts.
    A HARNESS row that got as far as the update (extra.phase "update" or later: t2 stopped it
    at its timeout or on a net stall) is a floor the same way, from its own last 3 (guix15's
    5.03 GiB at a 7200 s stop on czerny was left out, and its next run, still at 4.6, overran
    at 5.79 GiB, t2v-20261121T1624-bd34).  Other HARNESS rows and LOST rows count not at all
    (the job never got far)."""
    mine = sorted((r for r in rows or () if r.get("cell") == cell and r.get("kind") == "run"
                   and (r.get("verdict") not in ("HARNESS", "LOST") or (r.get("extra") or {}).get("overrun"))
                   and isinstance(r.get("ram_peak_kib"), int) and r["ram_peak_kib"] > 0),
                  key=lambda r: (r.get("t") or "", r.get("id") or ""), reverse=True)[:3]
    cap = float(policy.get("ram_gib_max", 16))
    measured = [r for r in mine if not _cut_short(r)]
    newest = (measured[0].get("t") or "", measured[0].get("id") or "") if measured else None
    same_run = measured[0].get("run") if measured else None
    stopped = sorted((r for r in rows or () if r.get("cell") == cell and r.get("kind") == "run"
                      and r.get("verdict") == "HARNESS" and not (r.get("extra") or {}).get("overrun")
                      and (r.get("extra") or {}).get("phase") in UPDATE_REACHED
                      and isinstance(r.get("ram_peak_kib"), int) and r["ram_peak_kib"] > 0),
                     key=lambda r: (r.get("t") or "", r.get("id") or ""), reverse=True)[:3]
    floors = [r for r in [r for r in mine if _cut_short(r)] + stopped
              if newest is None or (r.get("t") or "", r.get("id") or "") > newest
              or (same_run and r.get("run") == same_run)]
    if measured:
        d = round(1.1 * max(r["ram_peak_kib"] for r in measured) / 1048576 + 0.25, 1)
    elif entry.get("ram_gib"):
        d = float(entry["ram_gib"])
    else:
        d = float(policy.get("ram_gib_first", 8))
    if floors:
        d = max(d, round(1.1 * max(r["ram_peak_kib"] for r in floors) / 1048576 + 0.25, 1))
    d = max(min(d, cap), 1.0)
    if update:
        d = max(d, min(float(UPDATE_MIN_D), cap))
    mem = int(entry.get("memory") or 2048)
    return d, int(math.ceil(mem / 1024.0)) + 1


def rerun_d(d, peak_kib, cap):
    """An overrun's rerun D: 2 x D, or twice what the overrun had reached when it was stopped (its
    peak, a floor: the update was still writing), whichever is larger, never over CAP.  The
    watcher stops a job just past D + max(1, D/10), so at a small D 2 x D is about the peak itself,
    and an update over twice its D overran again: manjaro's 1.0 -> 2.0 -> 4.0 for 3.87 GiB, and
    pop24's 1.3 (stopped at 2.44 GiB) rerun at 2.6 peaked at 3.57 of its 3.6 limit, Pop!_OS's
    kernel build with Firefox and COSMIC (t2v-20261118T0027-d7c8, t2v-20261118T2305-edbb)."""
    nd = round(d * 2, 1)
    if isinstance(peak_kib, (int, float)) and peak_kib > 0:
        nd = max(nd, round(2 * peak_kib / 1048576, 1))
    return min(cap, nd)


def fitting_hosts(hosts, need, facts):
    """HOSTS whose ceiling (facts.ceiling: the most one cmp-run.sh --reserve can ever get there)
    NEED (D, M) fits; a host whose facts give none counts as fitting.  An overrun's rerun is
    larger than the plan placed: on a host it can never fit, cmp-run.sh refuses it at once."""
    return [h for h in hosts if not factsmod.over_ceiling(need, factsmod.ceiling((facts or {}).get(h)))]


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


STALE = "stale:"       # identity_compare's dropped reason for a cache at another generation


def is_stale(how):
    """True when HOW, a dropped reason of identity_compare, is a stale cache's."""
    return str(how or "").startswith(STALE)


def identity_compare(per_host, hashed=False, optional=()):
    """Step 1 on what the hosts answered ({host: tool_guest.identity_of's dict}).
    {"ok": True|False|None, "why", "rehash": [hosts]}; ok None: hash on the hosts in rehash first.
    A host marked no_answer vouches for nothing and condemns nothing: ok False, harness True.
    A host in OPTIONAL (may_lack_guests: an image cache) that has no such guest is left out
    while another host has it: only the hosts that hold the guest are compared, and the result's
    "dropped" ({host: "dangles"|"gone"|"stale:GENERATION"}) names the hosts left out so.  An
    optional host at another image-state generation than the fleet's is left out the same way
    (_stale_caches), with "stale:" and the generation it has (nothing after "stale:" when it has
    no image-state of its own, which differs from the fleet's generation just the same).
    A disk with a backing file fails, but for a world's thin disk over the fleet's same disk
    (contract 4), whose backing disk passes the fleet's checks, at one generation on every host."""
    dropped = {}
    if optional:
        lacks = {h for h, i in per_host.items() if h in optional and (not i or i.get("missing"))}
        if lacks and any(i and not i.get("missing") for h, i in per_host.items() if h not in lacks):
            dropped = {h: "dangles" if (per_host[h] or {}).get("dangling") else "gone" for h in sorted(lacks)}
            per_host = {h: i for h, i in per_host.items() if h not in lacks}
        stale = _stale_caches(per_host, optional)
        if stale:
            dropped.update({h: STALE + (str(gen) if gen else "") for h, gen in sorted(stale.items())})
            per_host = {h: i for h, i in per_host.items() if h not in stale}
    res = _identity_compare(per_host, hashed)
    if dropped:
        res["dropped"] = dropped
    return res


def _stale_caches(per_host, optional):
    """{host: generation} for each host of OPTIONAL (may_lack_guests: an image cache) whose
    guest is at another image-state generation than the one the hosts outside OPTIONAL hold: its
    vms/GUEST is linked to an older vm-store version (Cage's void-musl was, 2026-11-14) while
    the fleet hosts agree, which is that cache's link to mend, not a fault of the guest's
    disks.  Such a host is dropped like one that lacks the guest, and the guest's cells run on
    the hosts that remain, rather than every host's cells failing on "the hosts' generations
    differ".  Nothing is dropped, and that failure stands, unless the hosts that hold the guest
    and are not optional (the fleet hosts) all hold one generation, and it is a generation: a
    fleet that disagrees with itself, a guest no fleet host of the run holds, and a fleet host
    with no image-state at all are each a fault to answer for, not a cache to mend.  A cache
    with no image-state of its own differs from the fleet's generation as plainly as one at an
    older version, and the same `vm-store link` mends it, so it is dropped too, with no
    generation to name after "stale:".  A host that didn't answer or lacks the guest is
    _identity_compare's business, so none is dropped here while one of those is in hand."""
    if any(not i or i.get("missing") or i.get("no_answer") for i in per_host.values()):
        return {}
    gens = {h: (i.get("image_state") or {}).get("generation") for h, i in per_host.items()}
    if len(set(gens.values())) < 2:
        return {}
    fleet = {g for h, g in gens.items() if h not in optional}
    if len(fleet) != 1:
        return {}
    want = fleet.pop()
    if not want:
        return {}
    return {h: g for h, g in sorted(gens.items()) if h in optional and g != want}


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
    its FAIL row is written, under RUN; RUN None: a new t2v- id).  A guest that passes on the hosts
    that hold it, while an image cache was left out (identity_compare's dropped: its entry gone, or
    at another generation than the fleet's), also has "lacking" {host: why} and "dropped" {host:
    identity_compare's reason}, and G leaves that host's facts so its cells go elsewhere
    (_gone_since_facts).  The caller keeps "dropped" for as long as it plans with these facts:
    a facts refresh after this step answers with the guest again (a stale cache's vms/GUEST is
    there, only linked to an older version), and apply_drops puts the drop back.  sha256: always
    hash.  A host that didn't answer, twice, isn't a guest's fault: ok false, harness true, and
    no FAIL row.

    The hosts are asked at once (ask_each), each hashing its own disks: one after another, a
    GuestTest2All's hashing took an hour on 2026-11-18 (Chopin 26 min, Cinqcent 11, Copland and
    Cage 6 each, then Choubox) before any cell ran.  A rehash (a guest whose disks differ from
    their merge record, or one with no image-state, which has none) asks each host once for all
    its guests, with --cached: a disk t2-host imgsum's cache holds at the same stat isn't read
    again.  sha256's hashing never uses the cache."""
    from . import run as runmod
    io = io or hostjob.HostIO()
    log = log or (lambda t: None)
    hosts = [h for h in ctx.hosts if h in (ctx.facts or {})]
    guests = list(guests)
    if not guests or not hosts:
        return {}
    info = {}

    def ask(host, names, sha, backfill, cached=False):
        """{guest: its identity}, or None when HOST gave no answer, twice."""
        a = ["identity"] + list(names) + (["--sha256"] if sha else []) + (["--backfill"] if backfill else []) \
            + (["--cached"] if cached else [])
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

    def ask_each(jobs):
        """ask(HOST, *ARGS) for each HOST: ARGS of JOBS, all at once: {host: its answer or None}."""
        got = {}

        def one(h, a):
            try:
                got[h] = ask(h, *a)
            except Exception as e:   # noqa: BLE001 - ask's own catch covers the ssh; this, the rest
                log(f"{h}: identity: {type(e).__name__}: {e}")
                got[h] = None
        if len(jobs) == 1:
            for h, a in jobs.items():
                one(h, a)
            return got
        ts = [threading.Thread(target=one, args=(h, a), name=f"identity-{h}", daemon=True) for h, a in sorted(jobs.items())]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return got

    optional = {h for h in hosts if may_lack_guests(h)}

    def holders(g):
        """The hosts asked about G: all but an image cache whose facts say it hasn't G (a cache
        that fetches it later is checked then); every host when none has it (no such guest)."""
        def has(h):
            gs = (ctx.facts.get(h) or {}).get("guests")
            return not isinstance(gs, dict) or g in gs     # facts with no guests part: asked, as before
        return [h for h in hosts if h not in optional or has(h)] or hosts

    asked = {g: holders(g) for g in guests}
    first = ask_each({h: ([g for g in guests if h in asked[g]], sha256, sha256) for h in hosts
                      if any(h in asked[g] for g in guests)})
    for h in hosts:
        for g, v in (first.get(h) or {}).items():
            info.setdefault(g, {})[h] = v
    seen = {}
    for g in guests:
        per = info.setdefault(g, {})
        for h in asked[g]:
            per.setdefault(h, {"no_answer": True})
        seen[g] = identity_compare(per, hashed=sha256, optional=optional)
    rehash = {}
    for g in guests:
        if seen[g]["ok"] is None:
            for h in seen[g]["rehash"]:
                rehash.setdefault(h, []).append(g)
    again = ask_each({h: (gs, True, True, not sha256) for h, gs in rehash.items()})
    out, rid = {}, None
    for g in guests:
        per, res = info[g], seen[g]
        dropped = dict(res.get("dropped") or {})
        if res["ok"] is None:
            for h in res["rehash"]:
                got = (again.get(h) or {}).get(g)
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
        if dropped:
            out[g]["dropped"] = dict(dropped)
        gone = _gone_since_facts(ctx, g, dropped)
        if gone:
            out[g]["lacking"] = gone
            for h, w in sorted(gone.items()):
                what = w if is_stale(dropped.get(h)) else f"no {g} in its vms/ ({w})"
                log(f"guest:{g}: {h}: {what}: its stic and holt cells stay off {h}")
    return out


GONE_WORDS = {"dangles": "its vms/ link dangles, gone since facts", "gone": "gone since facts"}


def dropped_why(g, how):
    """The phrase a DROPPED host's facts carry for G: why G's cells stay off that image cache.
    A stale cache's names the generation it has and what mends it, since the entry is there and
    only its link is old; one with no image-state of its own has no version to name, and the
    same `vm-store link` mends it."""
    if is_stale(how):
        gen = str(how)[len(STALE):]
        what = f"stale generation {gen[:12]}" if gen else "no image-state of its own"
        return f"{what} (run vm-store link {g} there)"
    return GONE_WORDS.get(how, GONE_WORDS["gone"])


def drop_in_facts(facts, g, host, how):
    """G out of HOST's facts' guests, with HOW's why (identity_compare's dropped reason): the
    why, or None when there is nothing to drop (no facts of HOST, no guests part -- that cache
    was asked without knowing -- or G already out of them).  Placement, Planner's
    fit_capabilities, reads a cache's guests, so this is what keeps G's stic and holt cells off
    the host, as the host's own next facts would; "guests_gone" says why, and a stale cache's
    "guests_stale" holds it too, since that one is the whole reason a plan's "kept off HOST"
    needs ("stale generation G (run vm-store link G there)"), where a gone cache's reads "no G
    in its vms/ (WHY)"."""
    f = (facts or {}).get(host)
    gs = (f or {}).get("guests")
    if not isinstance(gs, dict) or g not in gs:
        return None
    gs.pop(g, None)
    why = dropped_why(g, how)
    f.setdefault("guests_gone", {})[g] = why
    if is_stale(how):
        f.setdefault("guests_stale", {})[g] = why
    return why


def apply_drops(facts, drops):
    """DROPS, {guest: {host: identity_compare's dropped reason}} as identity_check's results
    carry them, into FACTS again: {guest: {host: why}} for what this call took out.  The step
    itself drops the guest when it runs, but a facts refresh after it (one backfilled stat
    record makes the conductor probe again) answers for each host wholesale, and a stale cache
    still holds its vms/GUEST -- only linked to an older vm-store version -- so the fresh facts
    put the guest back and the cells would run there against the stale image.  Every caller that
    plans with refreshed facts calls this on them, bko.Conductor at each refresh and for each
    child's ctx, so the drop outlives the probe.  Idempotent: a host whose facts already lack
    the guest is left as it is, so calling it twice says nothing twice."""
    out = {}
    for g, per in sorted((drops or {}).items()):
        for h, how in sorted((per or {}).items()):
            why = drop_in_facts(facts, g, h, how)
            if why:
                out.setdefault(g, {})[h] = why
    return out


def _gone_since_facts(ctx, g, dropped):
    """{host: why} for each image cache DROPPED (identity_compare) whose facts said it held G,
    out of ctx.facts (drop_in_facts)."""
    return (apply_drops(ctx.facts, {g: dropped})).get(g) or {}


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
    (hash, then backfill, or a FAIL row).  ctx.facts get what changed.  The identity results of
    the guests it checked, {G: identity_check's answer}, so that the caller keeps their "dropped"
    image caches across its own facts refresh (apply_drops); {} when it checked none."""
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
    if not moved:
        return {}
    out("disks differ from their merge records: " + ", ".join(moved))
    res = identity_check(ctx, moved, io=io, log=out)
    for g, r in sorted(res.items()):
        out(f"guest:{g}: identity {'ok' if r['ok'] else ('unknown: ' if r.get('harness') else 'FAIL: ') + str(r['why'])}")
    return res


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
              "testvm/test2/t2-host", "testvm/test2/t2lib", "testvm/test2/classes.json", "testvm/hosts.json",
              "testvm/sculpt-update", "testvm/sculpt-check.sh")


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


# --- the network rule ------------------------------------------------------------------------------

# The modes whose job downloads a guest's updates: a probe or a console boot (redox, sculpt2610's
# try) reads no mirror, and starts whatever the internet does.
NET_MODES = ("try", "update", "console-update")
NET_DEFAULTS = {"net_probe_urls": ["https://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.xz",
                                   "https://mirrors.edge.kernel.org/debian/dists/trixie/main/binary-amd64/Packages.xz"],
                "net_probe_bytes": 2 * 1024 * 1024, "net_probe_s": 15, "net_min_kibs": 256, "net_clear_kibs": 1024,
                "net_check_min": 5, "net_hold_min": 10, "net_hold_max_min": 240}


def net_probe_cmd(url, nbytes, secs):
    """The host's shell line that reads the first NBYTES of URL (redirects followed) for at most
    SECS seconds and prints "t2-net SPEED SIZE CODE" (curl's bytes/s, bytes, HTTP status), whatever curl's exit."""
    return (f"curl -sSL -o /dev/null -r 0-{int(nbytes) - 1} --max-time {int(secs)} "
            f"-w 't2-net %{{speed_download}} %{{size_download}} %{{http_code}}\\n' {shlex.quote(url)} 2>/dev/null; true")


def parse_net_probe(text):
    """net_probe_cmd's output -> bytes/s (0.0 for a fetch that got nothing), or None for an
    answer without the line (curl missing, the host's shell not reached)."""
    for line in reversed(str(text or "").splitlines()):
        m = re.match(r"\s*t2-net (\d+(?:\.\d+)?) (\d+) (\d{3})\s*$", line)
        if m:
            return float(m[1]) if 200 <= int(m[3]) < 300 and int(m[2]) > 0 else 0.0
    return None


class NetHold:
    """The network rule (GuestTest2 t2v-20261119T0157-c8c7: the farm's internet ran at 5 to 100
    KiB/s for hours, and ten try jobs each downloaded for 5400 s before the harness stopped them,
    attempts 2 included).  Every net_check_min minutes while a job that downloads waits, one
    host reads the first net_probe_bytes of each of net_probe_urls in turn, net_probe_s seconds
    at most each, and the best rate counts (the first that is fast enough ends the probe).  A
    rate under net_min_kibs KiB/s holds the farm, since its hosts share one internet: no job of
    NET_MODES starts anywhere; those running go on.  It starts again once held net_hold_min
    minutes and a probe reads at least net_clear_kibs (the hysteresis).  A hold that lasts
    net_hold_max_min minutes gives up: the waiting jobs are skipped, so the run ends rather than
    wait for ever.  A probe that can't be read (no host answered) changes nothing.
    net_min_kibs 0 turns the rule off.

        check(probe) -> None     PROBE(url, nbytes, secs) -> bytes/s or None; rate-limited
        held() -> why or None;   gave_up() -> why or None;   notes() -> [what happened]"""

    def __init__(self, policy=None, clock=None):
        p = dict(NET_DEFAULTS, **{k: v for k, v in (policy or {}).items() if k in NET_DEFAULTS})
        self.urls = [u for u in (p["net_probe_urls"] or []) if isinstance(u, str) and u]
        self.nbytes, self.secs = int(p["net_probe_bytes"]), float(p["net_probe_s"])
        self.min_b, self.clear_b = float(p["net_min_kibs"]) * 1024, float(p["net_clear_kibs"]) * 1024
        self.every_s, self.min_s, self.max_s = float(p["net_check_min"]) * 60, float(p["net_hold_min"]) * 60, float(p["net_hold_max_min"]) * 60
        self.clock = clock or time.time
        self.last = None
        self.hold = None          # {"since": t, "why": text}
        self.given = None
        self.said = []

    @property
    def on(self):
        return self.min_b > 0 and bool(self.urls)

    def rate(self, probe):
        """The best rate the probe URLs give (the first fast enough ends it), and its URL; (None, None) when none could be read."""
        best = (None, None)
        for u in self.urls:
            r = probe(u, self.nbytes, self.secs)
            if r is not None and (best[0] is None or r > best[0]):
                best = (r, u)
            if r is not None and r >= self.clear_b:
                break
        return best

    def check(self, probe):
        if not self.on or self.given:
            return None
        now = self.clock()
        if self.last is not None and now - self.last < self.every_s:
            return None
        self.last = now
        r, url = self.rate(probe)
        if r is None:
            return None
        kib = r / 1024
        site = re.sub(r"^\w+://([^/]+).*", r"\1", url or "")
        if self.hold is None:
            if r < self.min_b:
                why = f"the internet ran at {kib:.0f} KiB/s ({site}), under {self.min_b / 1024:g}"
                self.hold = {"since": now, "why": why}
                self.said.append(f"held every guest update: {why} ({utc_now()})")
            return None
        held_s = now - self.hold["since"]
        if held_s >= self.min_s and r >= self.clear_b:
            self.said.append(f"guest updates started again after {held_s / 60:.0f} min held (the internet at {kib:.0f} KiB/s; {utc_now()})")
            self.hold = None
            return None
        if held_s >= self.max_s:
            self.given = (f"the internet ran under {self.clear_b / 1024:g} KiB/s for {held_s / 60:.0f} min "
                          f"(last {kib:.0f} KiB/s, {site}): the guest updates not yet started weren't run")
            self.said.append(self.given)
        return None

    def held(self):
        return None if self.hold is None else self.hold["why"]

    def gave_up(self):
        return self.given

    def notes(self):
        return list(self.said)


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
        self.host_requeued = None   # why its host was lost under an attempt that was requeued (once)
        self.lost_host = None       # ... and which host that was (lost again on another: it counts)
        self.no_baseline = set()    # the hosts its log baseline wasn't written on (upload_baselines)
        self.started_t = None       # when its attempt's job started (the host rule's window)
        self.resumed = None       # guest.json's record of an attempt an earlier process started

    @property
    def cell(self):
        return f"guest:{self.guest}"


class Runner:
    def __init__(self, ctx, plan, io=None, log=None, job=None, fleet=None, poll=POLL_S, clock=None, reattach=None, net_probe=None):
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
        self.unsure = []          # attempts waiting for their host's confirm (finish_job, take_unsure)
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
        self.net = NetHold(self.policy, self.clock)     # the network rule
        self.net_probe = net_probe or self.probe_net
        self._net_said = None

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
        allowed = self.update_hosts(pc, g, e) if self.update else self.allowed_hosts(pc)
        return Item(g, pc, e, d, m, allowed)

    def update_hosts(self, pc, g, entry):
        """Where an update round may update guest G (img-speed F1, FD1): the fleet's first host,
        and every host of the run that caches G's base: its facts' copy at that host's generation
        with the same disk SHA-256s in its image-state (one generation can be other bytes: a cache
        holds what was published, and the fleet's image can have been rewritten since) and, where
        both are known, the same disks' sum; vm-update --ship checks the bytes again before it boots.  Those update in a --keep overlay and ship only the overlay to the
        first host, which merges it.  sculpt2610 (sculpt-update) and a host the cell may not run on
        (allowed_hosts) stay off; a host whose synced tree differs is dropped later (run_update)."""
        fh = first_host()
        if fh not in self.hosts:
            return [self.hosts[0]]
        if mode_of(entry, True) != "update":
            return [fh]
        def copy_of(h):
            return (((self.ctx.facts or {}).get(h) or {}).get("guests") or {}).get(g) or {}
        base = copy_of(fh)
        if not base.get("generation"):
            return [fh]
        may = set(self.allowed_hosts(pc))
        out = [fh]
        for h in self.hosts:
            c = copy_of(h)
            if h == fh or h not in may or c.get("generation") != base["generation"]:
                continue
            if not base.get("state_sha256") or c.get("state_sha256") != base["state_sha256"]:
                continue
            if base.get("imgsum") and c.get("imgsum") and c["imgsum"] != base["imgsum"]:
                continue
            out.append(h)
        return out

    def pm_of(self, it):
        return (it.entry.get("checks") or {}).get("pm")

    # --- baselines and cells ---------------------------------------------------------------------
    def upload_baselines(self, guests):
        """Each guest's log baseline onto every host; {guest: {host}} of those it wasn't written on
        (noted: placed_by_baseline keeps the guest off them when another host may run it)."""
        root = os.path.join(getattr(self.rs, "mac_root", None) or mac_root(), "guest-baselines")
        missed = {}
        for g in guests:
            p = os.path.join(root, f"{g}.json")
            try:
                with open(p, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            d = f"{self.host_root.rstrip('/')}/runs/{self.rs.run}/baselines"
            for h in self.hosts:
                why = self.put_file(h, f"{d}/{g}.json", data)
                if why:   # the run goes on: the guest's rows there judge its lines with no baseline
                    self.log(f"{g}: baseline not written on {h}: {why}")
                    self.notes.append(f"guest:{g}: baseline not written on {h} ({why}): its lines there are judged as with none")
                    missed.setdefault(g, set()).add(h)
        return missed

    def placed_by_baseline(self, items, missed):
        """Items whose baseline missed a host (upload_baselines' MISSED): run on the hosts that have
        it, when one of them may run it; else marked (no_baseline), and its cell says so (finalize)."""
        for it in items:
            miss = set(missed.get(it.guest) or ())
            if not miss:
                continue
            if it.resumed:   # (an attempt already running where it runs: only marked)
                it.no_baseline = miss
                continue
            keep = [h for h in it.allowed if h not in miss]
            if keep and keep != it.allowed:
                self.log(f"{it.cell}: not on {', '.join(sorted(set(it.allowed) & miss))}: its baseline isn't there")
                it.allowed = keep
            elif not keep:
                it.no_baseline = miss

    def put_file(self, host, path, data):
        """DATA as HOST's PATH: written beside it, moved into place and seen there, not empty.  A
        write that failed is tried again (PUT_TRIES, waits of PUT_WAIT_S times the try).  None, or
        why not: one ssh's write of the cells file failed, unseen, and elementary8-luks and neon24
        passed on chopin into invalid rows, with no cell entry (the freeze run, 2026-11-20)."""
        tmp = path + ".tmp"
        cmd = (f"mkdir -p {hostrun.rq(os.path.dirname(path))} && cat > {hostrun.rq(tmp)} && mv -f {hostrun.rq(tmp)} {hostrun.rq(path)}"
               f" && test -s {hostrun.rq(path)}")
        why = None
        for n in range(PUT_TRIES):
            try:
                rc, _out, err = self.io.sh(host, cmd, input=data, timeout=60)
                why = None if rc == 0 else f"exit {rc}" + (f": {str(err).strip()[-200:]}" if str(err or "").strip() else "")
            except Exception as e:   # noqa: BLE001 - an ssh that raised is a write that failed: tried again
                why = f"{type(e).__name__}: {e}"
            if why is None:
                return None
            if n < PUT_TRIES - 1:
                self.io.sleep(PUT_WAIT_S * (n + 1))
        return f"{why} ({PUT_TRIES} tries)"

    def write_job_cells(self, it, host):
        """The attempt's own cells file (reruns: rule, attempt, what it reruns); one not written
        (put_file) raises, which job_thread makes the attempt's harness result."""
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
        why = self.put_file(host, path, json.dumps(doc, sort_keys=True).encode())
        if why:   # job_thread: a harness result, and no job run against a missing file
            raise RuntimeError(f"the cells file {path} wasn't written on {host}: {why}")
        return path

    def job_env(self, it, cells, mode, host=None):
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
        if mode not in ("console", "console-update"):
            e["VM_UPDATE_RAM"] = f"{it.d:g}"
        if self.update:
            e["VM_UPDATE_ROUND"] = self.rs.run
            # The run's hosts are where its updates run (update_hosts); they merge on the fleet only,
            # never into a cache host's copy (vm-store's, read only): a word's --hosts that leaves out
            # some of the fleet merges on the fleet hosts it names.
            from . import hosts as hosts_table
            fleet = hosts_table.with_role("fleet")
            named = [h for h in self.hosts if h in fleet]
            if mode == "update" and host is not None and host != first_host():
                e["T2_GUEST_SHIP"] = "1"   # vm-update --ship: the overlay goes to the fleet, which merges it
            elif named and set(named) != set(fleet):
                e["T2_GUEST_HOSTS"] = ",".join(f"{h}.local" for h in named)
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
        from . import parse_utc   # (a resumed attempt began when the earlier process started it)
        it.started_t = (parse_utc((it.resumed or {}).get("t")) if isinstance(it.resumed, dict) else None) or self.clock()

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
                                                       "flake": it.flake, "rerun_done": it.rerun_done, "rows": list(it.rows),
                                                       "host_requeued": it.host_requeued, "lost_host": it.lost_host,
                                                       "allowed": list(it.allowed)}
                self.save()
                extra = self.job_env(it, cells, mode, host)
                rc, odir, t, why = self.job(self.env, host, it.guest, it.attempt, cmd, (it.d, it.m), tag, timeout, 4 * 3600, extra, None, "guest",
                                            on_began=on_began)
            # a job the runner's TERM ended (its timeout) wrote its row as it ended: that row is the result
            ended = why is None or getattr(why, "gone", False) or getattr(why, "stopped", False)
            files = self.fetch_row(host, odir) if ended else {}
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
        if self.net.gave_up() and self.needs_net(it):
            return self.net.gave_up()
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
        stopped = bool(getattr(why, "stopped", False))   # host_job's TERM ended it: job.sh wrote its row
        if (why is None or gone or stopped) and files.get("row-summary.json"):
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
        # its host went away under it (the host rule: no answer, a reboot): host-lost, which no stop
        # rule counts, and requeued once, to another host it may run on or to the same one once it
        # is back (the hold keeps it off until then); never beside a job that may still run there
        # (no answer, no reboot since it began), never a second real update
        lost = None
        if v in ("HARNESS", "LOST") and not summ.get("overrun") and not summ.get("net_stall"):
            lost = hostjob.host_lost(self.rs.stops, host, it.started_t, self.clock(),
                                     seen=lambda h: hostjob.host_seen(self.io, h),
                                     text=" ".join([str(why or ""), str(summ.get("harness") or "")]))
            if not lost and self.may_wait_host(it, host):
                # its host may be going away (a job TERMed as its host shut down ends before the host
                # stops answering): it waits, unsure, for the host to be seen answering a while after
                self.unsure.append((it, host, rc, t, summ, still, self.clock()))
                return
        if lost and self.requeue_lost(it, host, v, lost, still, summ):
            return
        self.after_attempt(it, host, rc, t, summ, still)

    def may_wait_host(self, it, host):
        """Whether IT's HARNESS or LOST attempt on HOST may wait for the host rule's confirm (unsure):
        one that may be requeued, or one requeued once already on another host's loss (lost here
        too, it counts: requeue_lost's lost_again)."""
        st = self.rs.stops
        known = getattr(st, "host_known", None)
        again = bool(it.host_requeued and it.lost_host and it.lost_host != host)
        return bool(not self.update and (again or (not it.host_requeued and it.attempt < MAX_ATTEMPTS))
                    and hasattr(st, "host_confirmed") and known is not None and known(host))

    def requeue_lost(self, it, host, v, lost, still, summ):
        """IT's attempt on HOST is host-lost (LOST: why): requeued once (True), else said why not."""
        summ["host_lost"] = lost
        rb = getattr(self.rs.stops, "rebooted", None)
        gone_there = bool(not still or (rb is not None and it.started_t is not None and rb(host, it.started_t)))
        where = list(it.allowed) if gone_there else [h for h in it.allowed if h != host]
        if self.update or it.host_requeued or it.attempt >= MAX_ATTEMPTS or not where:
            if it.host_requeued and it.lost_host and it.lost_host != host:
                # lost on a second host: no longer two hosts' bad luck, so a harness result (feed)
                summ["lost_again"] = f"{lost}; before on {it.lost_host}: {it.host_requeued}"
            self.notes.append(f"{it.cell}: attempt {it.attempt} {v} on {host}: host lost ({lost}); not requeued"
                              + (" (an update round)" if self.update else " (requeued once already)" if it.host_requeued
                                 else "" if where else f" (its job may still run on {host}, and no other host may run it)"))
            return False
        it.host_requeued, it.lost_host = lost, host
        it.allowed = where
        it.attempt += 1
        it.prev_verdict = "FAIL" if it.prev_verdict == "FAIL" else "HARNESS"   # a FAIL before it still counts
        self.notes.append(f"{it.cell}: attempt {it.attempt - 1} {v} on {host}: host lost ({lost}); "
                          f"requeued as attempt {it.attempt}" + ("" if host in where else f", not on {host}"))
        self.log(f"{it.cell}: host lost on {host} ({lost}): attempt {it.attempt}"
                 + ("" if host in where else f", not on {host} (its job may still run there)"))
        self.pending.appendleft(it)
        return True

    def take_unsure(self, final=False):
        """The attempts waiting for their host's confirm (finish_job): host-lost now (its host went
        away soon after: requeued once), or confirmed, waited host_confirm_max_min or FINAL (then
        they go on as any attempt that ended)."""
        st = self.rs.stops
        conf = getattr(st, "host_confirm", None)
        grace, most = conf() if conf is not None else (0, 0)
        left = []
        for rec in self.unsure:
            it, host, rc, t, summ, still, at = rec
            lost = hostjob.host_lost(st, host, it.started_t, at + grace)
            if lost and self.requeue_lost(it, host, summ.get("verdict"), lost, still, summ):
                continue
            if lost or final or st.host_confirmed(host, at) or self.clock() - at > most:
                self.after_attempt(it, host, rc, t, summ, still)
                continue
            left.append(rec)
        self.unsure = left

    def after_attempt(self, it, host, rc, t, summ, still):
        """An attempt that ended, not requeued for its host: an overrun's or a FAIL's rerun, else its end."""
        v = summ.get("verdict")
        # an overrun below the cap: again, larger; it feeds no stop rule
        if not self.update and summ.get("overrun") and it.d < self.cap and it.attempt < MAX_ATTEMPTS and not still:
            d = rerun_d(it.d, summ.get("peak"), self.cap)
            fit = fitting_hosts(it.allowed, (d, it.m), getattr(self.ctx, "facts", None))
            if not fit:   # none takes that much: the most the largest host takes, if more than before
                ceils = [c[0] for c in (factsmod.ceiling((self.ctx.facts or {}).get(h)) for h in it.allowed) if c and c[1] >= it.m]
                d = max([c for c in ceils if c > it.d], default=d)
                fit = fitting_hosts(it.allowed, (d, it.m), self.ctx.facts) or it.allowed
            it.d, it.allowed = d, fit
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
        if host in it.no_baseline:   # (upload_baselines: its lines were judged as with no baseline)
            entry["no_baseline"] = True
            self.notes.append(f"{it.cell}: ran on {host}, where its baseline wasn't written: its new lines are judged "
                              f"as with none (attempt {it.attempt} {v}); don't accept them from this run")
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
               "flake": bool(it.flake) and summ.get("verdict") == "PASS",
               # an attempt stopped while it waited on the network (rows_guest.net_stall): GuestTest2's
               # own stop, never the run-wide harness count, which would stop SticTest2 and HoltTest2 too
               "net": bool(summ.get("net_stall")) and summ.get("verdict") == "HARNESS",
               # its host went away under it (finish_job: the host rule): no stop rule counts it
               "host_lost": summ.get("host_lost"), "lost_again": summ.get("lost_again"), "start": it.started_t, "end": self.clock()}
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
        held, was = self.held(), getattr(self, "_held_said", {})
        for h in sorted(set(held) - set(was)):
            self.log(f"HOLD {h}: {held[h]}: no job starts there; its jobs go to the other hosts they may run on")
        for h in sorted(set(was) - set(held)):
            self.log(f"{h}: the hold is over: jobs start there again")
        self._held_said = held

    # --- the network rule (NetHold) ------------------------------------------------------------------
    def needs_net(self, it):
        """IT's job downloads (NET_MODES), and is no earlier process's attempt to adopt."""
        return not it.resumed and mode_of(it.entry, self.update) in NET_MODES

    def probe_net(self, url, nbytes, secs):
        """NetHold's probe: the first host that answers (one the swap rule holds last) reads URL."""
        held = self.held()
        for h in sorted(self.hosts, key=lambda x: x in held):
            try:
                rc, out, _ = self.io.sh(h, net_probe_cmd(url, nbytes, secs), timeout=secs + 45)
            except Exception:   # noqa: BLE001 - a host that can't be asked: the next
                continue
            r = parse_net_probe(out) if rc == 0 else None
            if r is not None:
                return r
        return None

    def net_check(self):
        """The network rule's probe, when a job that downloads waits (NetHold rate-limits it); its holds logged once."""
        if not any(self.needs_net(it) for it in self.pending):
            return
        try:
            self.net.check(self.net_probe)
        except Exception as e:   # noqa: BLE001 - a probe that breaks holds nothing
            self.log(f"network rule: {type(e).__name__}: {e}")
            return
        why = self.net.held()
        if why and why != self._net_said:
            self.log(f"HOLD network: {why}: no guest update starts on any host; those running go on")
        elif not why and self._net_said:
            self.log("network: the hold is over: guest updates start again")
        self._net_said = why

    def held(self):
        """{host: why}: the hosts the swap rule or the host rule holds now (no job starts there)."""
        fn = getattr(self.rs.stops, "held", None)
        try:
            return dict(fn() or {}) if fn is not None else {}
        except Exception:   # noqa: BLE001 - holds that can't be read hold nothing
            return {}

    def drive(self):
        while self.pending or self.active or self.done_q or self.unsure:
            while self.done_q:
                rec = self.done_q.popleft()
                self.finish_job(*rec)
            if self.unsure:
                self.take_unsure()
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
            self.net_check()
            held, net = self.held(), self.net.held()
            for host in self.hosts:
                if host in held:
                    continue
                for it in list(self.pending):
                    if net and self.needs_net(it):
                        continue
                    if host in it.allowed and self.can_start(it, host):
                        self.pending.remove(it)
                        self.start(it, host)
                        break
            self.host_checks()
            if self.pending or self.active or self.done_q or self.unsure:
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

    def drop_unsynced(self, fh, items):
        """The other hosts an update may run on (update_hosts) run it from their synced tree too,
        so each must match this checkout as FH's does (tree_matches); one that doesn't is noted and
        left out of every item, which then updates on the hosts left."""
        others = sorted({h for it in items for h in it.allowed if h != fh})
        bad = {}
        for h in others:
            why = self.tree_matches(h)
            if why:
                bad[h] = why
                self.notes.append(f"GuestTest2 update: {h} left out, its guests update on {fh}: {why}")
        for it in items:
            if bad and not it.resumed:
                it.allowed = [h for h in it.allowed if h not in bad] or [fh]

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
        fh = first_host()
        host = fh if fh in self.hosts else self.hosts[0]
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
            self.drop_unsynced(host, items)
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
        if mode_of(self.sf.entry(guest_name), True) != "update":   # sculpt-update: no package lists; its steps say which image
            return ""
        try:
            # On the fleet's first host, where every update's dumps are kept (vm-update --ship sends them there)
            rc, text = remote_tool(self.io, first_host(), ["pkgdiff", guest_name, a, b], timeout=60)
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
                if n in ("image:free", "logs", "image:current"):   # (the others only say which version)
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
        out = {"run": rs.run, "word": self.word.word, "family": "guest", "start": start, "end": utc_now(), "cells": cells,
               "stops": list(rs.stops.stops), "notes": self.notes + ["network rule: " + n for n in self.net.notes()], "wear": {}, "hosts": sorted({c["host"] for c in self.cells if c.get("host")}),
               "copy": next((c.get("id") for c in (getattr(rs, "copy", None) or {}).values() if isinstance(c, dict) and c.get("id")), None),
               "blocks": self.blocks, "exit": ex}
        kept = getattr(self.env, "kept", None)    # (hostjob.host_checks: failed runs' kept state, by host)
        if isinstance(kept, dict) and any(kept.values()):
            out["write_kept"] = {h: v for h, v in sorted(kept.items()) if v}
        return out

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

    @staticmethod
    def wall_of(it):
        """IT's estimated wall in seconds: the plan's (pc.est: the median of its last PASS-like
        rows, else estimates.tsv), its gm when it has no wall, else 0."""
        est = getattr(it.pc, "est", None) or {}
        try:
            return float(est.get("wall_s") or 0) or float(est.get("gm") or 0) * 60
        except (TypeError, ValueError):
            return 0.0

    def longest_first(self, items):
        """The pool's queue, longest estimated wall first (ties in the plan's order): in the
        plan's (alphabetical) order the longest (elementary8-luks 38 min, elementary8 36,
        dragonfly6 32) started mid-run and ended last."""
        return sorted(items, key=lambda it: -self.wall_of(it))

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

    @staticmethod
    def restore(it, st):
        """IT as guest.json's started record ST left it (resume: adopt waits for that attempt)."""
        it.attempt = int(st.get("attempt") or 1)
        it.d = float(st.get("d") or it.d)
        it.prev_verdict, it.flake, it.rerun_done = st.get("prev_verdict"), st.get("flake"), bool(st.get("rerun_done"))
        it.rows = list(st.get("rows") or [])
        # (requeued once for its host already: a resume doesn't requeue it twice, nor put it back
        # where its requeue or an overrun's rerun took it off)
        it.host_requeued, it.lost_host = st.get("host_requeued"), st.get("lost_host")
        it.allowed = [h for h in st.get("allowed") or () if h in it.allowed] or it.allowed
        it.resumed = st

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
                    self.restore(it, st)
        self.placed_by_baseline(items, self.upload_baselines([it.guest for it in items]))
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
            self.pending.extend(self.longest_first(self.adopt(items)))
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
    have = set(cur.get("lines") or [])    # a changed builder keeps them (rows_guest.baseline_of)
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
    host = first_host()
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
    """The default hosts: the hosts whose ledgers a guest round pulls, and whose --hosts
    (when a word names others) vm-update merges on.  vm-update's own fleet (its ALL_HOSTS)
    is the hosts with the fleet role: chopin alone since 2026-11-13."""
    return DEFAULT_HOSTS
