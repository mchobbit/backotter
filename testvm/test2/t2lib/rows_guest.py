"""GuestTest2's ledger rows: what one guest/job.sh job did, built on the host that ran it
(contract 1.3, 2.1).

guest/job.sh runs, as its job ends:

    python3 -B T/test2/t2-host row guest --guest G --run RUN --attempt A --out DIR --mode MODE \\
        --rc N --cells CELLS

and t2-host calls build(argv, hostctx), appends the row and prints its id.  build() reads DIR's
records (result.json, hook-*.out, update.log, serial.log), the cell's entry in CELLS (rule,
reasons, planned key, what attempt it is), the spec and guest/limits.json of the tree it runs
from, HOST_ROOT/runs/RUN/baselines/G.json, and the cell's inputs (keys.measure).  It writes in
DIR: new-lines.json, steps.txt, pkgset files are vm-update's, and row-summary.json, which the
Mac reads for the rerun and the stop rules.

    --by-hand   a plain vm-update (no T2_CELLS): rule test1, valid false, "by hand: no Test2
                checks ran".  It never counts as a PASS.

Kinds of result:
    PASS, pass         every need PASS, SKIP or a classed LIMIT
    PASS, known-flake  on a rerun whose first attempt's failure tail was a known flake
    FLAKY-FAIL, fail   PASS on the rerun of a FAIL
    FAIL, fail         a FAIL or LIMIT-NEW need, or logs: DIFF
    HARNESS, harness   no result.json, the VM never booted or SSH never up, an overrun, a job that
                       didn't start, a hook whose output is missing or cut
"""

import argparse
import json
import os
import re
import time

from . import guest_judge as J
from . import keys as _keys

SPEC = "testvm/test2/guest/spec.json"
HARNESS_PHASES = ("start", "boot", "push", "before-hook", "after-hook")   # vm-update's phases: never came up, or an ssh that failed
GATE_BLOCKED = 10   # vm-update's exit, and --merge-gate's, for a merge that mustn't happen
HOOK_FILE = {"before": "before", "new": "new", "after": "after"}   # job.sh's hooks as vm-update names them: after their files
NOTHING_UPDATES = "nothing updates this guest"   # vm-update's merge.why when in-guest.sh exits 3: no update, no reboot
MAX_FAILS = 20


def _args(argv):
    p = argparse.ArgumentParser(prog="t2-host row guest", add_help=False)
    p.add_argument("--guest", required=True)
    p.add_argument("--run")
    p.add_argument("--attempt", type=int, default=1)
    p.add_argument("--out", required=True)
    p.add_argument("--mode", default="try", choices=("try", "probe", "update", "console"))
    p.add_argument("--rc", type=int, default=0)
    p.add_argument("--cells")
    p.add_argument("--by-hand", action="store_true")
    p.add_argument("--now")
    return p.parse_args(argv)


def _read(path, limit=8 << 20):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            n = f.tell()
            f.seek(max(n - limit, 0))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return None


def _json(path):
    t = _read(path)
    try:
        return json.loads(t) if t is not None else None
    except ValueError:
        return None


def load_cell(path, guest):
    """(the doc, the guest's entry) of a cells.json."""
    d = _json(path) if path else None
    if not isinstance(d, dict):
        return None, None
    return d, (d.get("cells") or {}).get(guest) or (d.get("cells") or {}).get(f"guest:{guest}")


def load_spec(base):
    """The guest spec of the tree at BASE, as keys.SpecFile (kinds merged by .entry)."""
    p = os.path.join(base, SPEC)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return _keys.SpecFile("guest", SPEC, data, None)


def classed_of(base, guest):
    d = _json(os.path.join(base, "testvm/test2/guest/limits.json")) or {}
    e = d.get(guest)
    return e if isinstance(e, dict) else {}


def known_of(base):
    return _json(os.path.join(base, "testvm/test2/guest/known-lines.json")) or {}


def flakes_of(base):
    d = _json(os.path.join(base, "testvm/test2/guest/flakes.json"))
    if isinstance(d, dict):
        d = d.get("flakes")
    return d or []


def policy_of(base):
    d = _json(os.path.join(base, "testvm/test2/classes.json")) or {}
    return ((d.get("policy") or {}).get("guest")) or {}


BUILDER = re.compile(r"testvm/(make-[^/]*\.sh|[^/]+/\*\*)\Z")   # a guest's builder and its directory; never the harness beside them


def builder_digest(entry_raw, inputs):
    """A digest of the guest's builder files' inputs: a change resets its log baseline."""
    from . import canonical, sha256_hex
    want = [x for x in (entry_raw.get("harness") or []) if BUILDER.match(x)]
    h = (inputs or {}).get("harness") or {}
    got = {}
    for x in want:
        prefix = x.split("*", 1)[0]
        for k, v in h.items():
            if k == x or (("*" in x) and k.startswith(prefix)):
                got[k] = v
    return sha256_hex(canonical(got))


def hook_text(out, name):
    return _read(os.path.join(out, f"hook-{name}.out"))


def hook_status(result, name):
    """vm-update's record of hook NAME: {rc, sentinel}, or None when it never ran."""
    return ((result or {}).get("hooks") or {}).get(name)


def older_logs(work, guest):
    """This guest's earlier logs on this host, for the seen_before tag."""
    d = os.path.join(work, "vms", guest)
    return [t for t in (_read(os.path.join(d, n), 16 << 20) for n in ("update.log", "update-serial.log")) if t]


def baseline_of(host_root, run, guest, builder):
    """The Mac's accepted lines for GUEST, or None (none yet, or its builder changed)."""
    d = _json(os.path.join(host_root, "runs", run or "-", "baselines", f"{guest}.json")) if run else None
    if not isinstance(d, dict) or not isinstance(d.get("lines"), list):
        return None
    if d.get("builder") and builder and d["builder"] != builder:
        return None
    return d["lines"]


def _cut_fails(fails):
    return [str(x)[:200] for x in fails[:MAX_FAILS]]


def write_summary(out, doc):
    try:
        tmp = os.path.join(out, f".row-summary.{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, os.path.join(out, "row-summary.json"))
    except OSError:
        pass


def judge_job(guest, mode, result, out, entry, policy, classed, updates, rc, stopped_mark=False):
    """(needs {need: value}, details {need: Need}, harness_why, hook_failed [names]).  STOPPED_MARK:
    job.sh was told to stop (a TERM, a timeout)."""
    checks = entry.get("checks") or {}
    needs, details, hook_failed = {"identity": "PASS"}, {}, []
    harness_why = None
    result = result or {}
    phase = str(result.get("phase") or "")
    overrun = bool(result.get("overrun"))
    image_names = [n for n in J.IMAGE_NEEDS if n != "image:zfs-cache" or (checks.get("rules") or {}).get("mounts") == "M-zfs"]
    is_new = mode == "try" and not checks.get("established", True)

    def add(res):
        for n, d in res.items():
            needs[n], details[n] = d.v, d

    def fail_needs(names, why):
        for n in names:
            needs[n], details[n] = "FAIL", J.Need("FAIL", None, [why])

    def fail_hook_needs():
        """A hook that failed makes its needs FAIL, never SKIP (contract 1.3)."""
        if "before" in hook_failed:
            fail_needs(image_names, "hook before failed")
        if "before" in hook_failed or "after" in hook_failed:
            fail_needs(J.AFTER_NEEDS, "a hook failed")
        if "new" in hook_failed:
            fail_needs(J.NEW_NEEDS, "hook new failed")

    if mode == "console":
        needs["try"], details["try"] = "SKIP", J.skip("no SSH: a console probe")
        for n in J.IMAGE_NEEDS[:2] + J.IMAGE_NEEDS[3:] + J.AFTER_NEEDS:
            needs[n], details[n] = "SKIP", J.skip("no SSH")
        t = hook_text(out, "probe")
        ok, why = J.check_hook(t, ("probe",))
        if not ok:
            hook_failed.append("probe")
            harness_why = f"the console probe's output: {why}"
            fail_needs(["new:probe"], f"hook probe: {why}")
        else:
            add({"new:probe": J.probe_need(J.parse_sections(t))})
        return needs, details, harness_why, hook_failed

    if not result:
        return needs, details, f"no result.json in {out} (vm-update exit {rc})", hook_failed
    if overrun:
        return needs, details, "the run outgrew its reservation (overrun)", hook_failed
    # in-guest.sh exit 3: no update and no reboot; one reading, as a probe's
    nothing = rc == 0 and (result.get("merge") or {}).get("why") == NOTHING_UPDATES
    one_reading = mode == "probe" or nothing
    # The hooks the run reached.  With rc 0 or 10 each of job.sh's hooks must have left a record:
    # one that didn't is the harness's, never a SKIP.
    secs = {}
    for name, required in (("before", J.required_image(checks) + J.required_after(checks)),
                           ("new", ("firmware",)), ("after", J.required_after(checks))):
        if (name == "new" and not is_new) or (name == "after" and one_reading):
            continue
        st = hook_status(result, HOOK_FILE[name])
        t = hook_text(out, HOOK_FILE[name])
        if st is None and t is None:
            if rc in (0, GATE_BLOCKED):
                hook_failed.append(name)
                harness_why = harness_why or f"hook {name}: never ran, though vm-update exited {rc}"
            continue   # (else an earlier step failed: it never got there)
        ok, why = J.check_hook(t, required)
        if ok and st is not None and st.get("rc") not in (0, None):
            ok, why = False, f"exit {st.get('rc')}"
        if not ok:
            hook_failed.append(name)
            harness_why = harness_why or f"hook {name}: {why}"
        else:
            secs[name] = J.parse_sections(t)
    # A hook an ssh cut off for good: the first of that phase's with no record.
    if rc not in (0, GATE_BLOCKED) and phase in ("before-hook", "after-hook"):
        for name in ("before", "new") if phase == "before-hook" else ("after",):
            if (name != "new" or is_new) and name not in secs and name not in hook_failed:
                hook_failed.append(name)
                harness_why = harness_why or f"hook {name}: lost (vm-update exit {rc} in phase {phase})"
                break
    # A run that never booted, got no SSH, or was stopped from outside is the harness's.
    tail = result.get("tail") or []
    stopped = rc != 0 and (stopped_mark or (bool(tail) and str(tail[-1]).startswith("stopped")))
    if rc not in (0, GATE_BLOCKED) and (phase in HARNESS_PHASES or stopped or ("before" not in secs and "before" not in hook_failed)):
        why = "stopped from outside" if stopped else harness_why or f"the guest never came up, or ssh failed: vm-update exit {rc} in phase {phase or '?'}"
        fail_hook_needs()
        return needs, details, why, hook_failed
    if mode == "probe" and rc != 0:   # no need says what went wrong after the hooks (a guest that won't power off)
        harness_why = harness_why or f"the probe ended with vm-update exit {rc} in phase {phase or '?'}"
    if "before" in secs:
        add(J.judge_image(secs["before"], checks, policy.get("judge") or {}, updates))
    elif "before" not in hook_failed:
        for n in image_names:
            needs[n], details[n] = "SKIP", J.skip("the run stopped before the image checks")
    # try
    if one_reading:
        needs["try"], details["try"] = "SKIP", J.skip("a probe: no update" if mode == "probe" else NOTHING_UPDATES)
    else:
        needs["try"] = "PASS" if rc in (0, GATE_BLOCKED) else "FAIL"
        details["try"] = J.Need(needs["try"], None, [] if needs["try"] == "PASS" else [f"vm-update exit {rc} in phase {phase or '?'}"])
    # after
    if one_reading and "before" in secs:
        add(J.judge_after(None, secs["before"], checks, classed))
    elif "after" in secs and "before" in secs:
        add(J.judge_after(secs["before"], secs["after"], checks, classed))
    elif "after" not in hook_failed and "before" not in hook_failed:
        for n in J.AFTER_NEEDS:
            needs[n], details[n] = "SKIP", J.skip("the update failed before the reboot's checks")
    if rc == GATE_BLOCKED:   # the row of a blocked merge is a FAIL, whatever the needs above say
        why = (result.get("merge") or {}).get("why") or "the merge gate blocked it"
        needs["merge-gate"], details["merge-gate"] = "FAIL", J.Need("FAIL", None, [why])
    # new guests
    if is_new and "new" in secs:
        add(J.new_guest_needs(secs["new"], checks))
    elif is_new and "new" not in hook_failed:
        for n in J.NEW_NEEDS:
            needs[n], details[n] = "SKIP", J.skip("the run stopped before the new-guest checks")
    fail_hook_needs()
    return needs, details, harness_why, hook_failed


def sweep(out, guest, entry, baseline, known, older):
    """(new-line records, logs value, why it's a SKIP or None, a note or None)."""
    pm = (entry.get("checks") or {}).get("pm")
    sources = {}
    for name, f in (("update", "update.log"), ("serial", "serial.log")):
        t = _read(os.path.join(out, f), 32 << 20)
        if t:
            sources[name] = t
    if not sources:
        return [], "SKIP", "no log was read", None
    lines = J.sweep_lines(sources, J.known_filter(known, pm), baseline, older)
    cut = sorted(n for n, t in sources.items() if t.count("\n") >= J.LINES_MAX)
    note = f"only the last {J.LINES_MAX} lines of {' and '.join(cut)} were read" if cut else None
    return lines, ("DIFF" if lines else "PASS"), None, note


def build(argv, hostctx=None):
    a = _args(argv)
    base = getattr(hostctx, "base", None) or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    work = getattr(hostctx, "work", None) or os.path.expanduser("~/backotter-work")
    hroot = getattr(hostctx, "host_root", None) or "/bkoscratch/test2"
    now = a.now or (hostctx.now() if hostctx is not None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    guest, cell = a.guest, f"guest:{a.guest}"
    result = _json(os.path.join(a.out, "result.json"))
    notes, unmeasured = [], []
    try:
        sf = load_spec(base)
        entry = sf.entry(guest)
        raw = sf.raw_entry(guest)
    except (OSError, ValueError, KeyError) as e:
        sf, entry, raw = None, {}, {}
        notes.append(f"no spec entry for {guest}: {e}")
    cdoc, ce = load_cell(a.cells, guest) if a.cells else (None, None)
    # the inputs
    m = {"inputs": {}, "unknown": ["all"], "spec": None, "notes": [], "key": None, "groups": None}
    if hostctx is not None:
        try:
            src = hostctx.source()
            specs = _keys.Specs(src)
            m = hostctx.measure(cell, opts={}, src=src, specs=specs,
                                want=_keys.wants(specs, ("guest",)))
        except Exception as e:   # noqa: BLE001 - an unmeasured row is invalid, never a crash
            notes.append(f"inputs not measured: {type(e).__name__}: {e}")
    inputs = m.get("inputs") or {}
    unmeasured += m.get("unknown") or []
    notes += [n for n in m.get("notes") or [] if n not in notes]
    wall = int((result or {}).get("wall_s") or 0)
    times = {}
    for n in ("submitted", "began"):
        t = _read(os.path.join(a.out, "." + n))
        if t and t.strip().isdigit():
            times[n] = int(t)
    waits = max(times.get("began", 0) - times.get("submitted", 0), 0) if len(times) == 2 else 0
    host = getattr(hostctx, "host", None) or os.uname().nodename.split(".")[0]
    tag = os.environ.get("WEAR_TAG") or f"guest:{a.run or 'by-hand'}:{guest}"
    logs = f"{host}:{os.path.abspath(a.out)}"
    common = dict(family="guest", cell=cell, kind="run", word=(cdoc or {}).get("word") or "vm-update", attempt=a.attempt,
                  start=_start_of(result, now, wall), inputs=inputs, spec=m.get("spec"), gm=round(wall / 60, 3), wall_s=wall,
                  waits_s=waits, wear={tag: None}, logs=logs, meta={"run_family": "guest"})

    if a.by_hand:
        rc = a.rc
        needs = {"try": "PASS" if rc == 0 else "FAIL"}
        v, cl = ("PASS", "pass") if rc == 0 else ("FAIL", "fail")
        extra = {"mode": a.mode, "try_ran": False, "by_hand": True}
        row = dict(common, origin=f"guest:by-hand:{guest}:{re.sub(r'[-:]', '', now)}", run=f"by-hand-{re.sub(r'[-:]', '', now)}",
                   rule="test1", reasons=["by hand"], planned_key=None, valid=False, verdict=v, needs=needs,
                   counts=_counts(needs), fails=[], ram_peak_kib=(result or {}).get("ram_peak_kib"), extra=extra,
                   note="by hand: no Test2 checks ran", word="vm-update")
        row["class"] = cl
        return hostctx.row(**row) if hostctx is not None else row

    policy = policy_of(base)
    classed = classed_of(base, guest)
    updates = _updates(work, guest)
    if (result or {}).get("merged") and isinstance(updates, int):   # image-state was written by the merge: the boot judged had one less
        updates = max(updates - 1, 0)
    stopped_mark = os.path.exists(os.path.join(a.out, ".stopped"))
    needs, details, harness_why, hook_failed = judge_job(guest, a.mode, result, a.out, entry, policy, classed, updates, a.rc, stopped_mark)
    # the log sweep
    builder = builder_digest(raw, inputs)
    baseline = baseline_of(hroot, a.run, guest, builder)
    new_lines, logs_v, logs_skip, logs_note = sweep(a.out, guest, entry, baseline, known_of(base), older_logs(work, guest))
    needs["logs"] = logs_v
    if logs_skip or logs_note:
        details["logs"] = J.Need(logs_v, logs_skip or logs_note)
    try:
        with open(os.path.join(a.out, "new-lines.json"), "w", encoding="utf-8") as f:
            json.dump({"guest": guest, "run": a.run, "attempt": a.attempt, "builder": builder, "lines": new_lines}, f, indent=0)
            f.write("\n")
    except OSError as e:
        notes.append(f"new-lines.json not written: {e}")
    needs = J.apply_limits(needs, details, classed)
    verdict, klass, to_class = J.verdict_of(needs, harness_why)
    prev = (ce or {}).get("prev_verdict")
    flake = (ce or {}).get("flake")
    if prev == "FAIL" and verdict == "PASS":
        verdict, klass = ("PASS", "known-flake") if flake else ("FLAKY-FAIL", "fail")
    fails = []
    for n, d in details.items():
        if needs.get(n) in ("FAIL", "LIMIT-NEW"):
            fails += [f"{n}: {x}" for x in (d.lines or [d.why or needs[n]])]
    if needs.get("logs") == "DIFF":
        fails.append(f"logs: {len(new_lines)} lines not yet accepted")
    if harness_why:
        fails.insert(0, f"harness: {harness_why}")
    r = result or {}
    merge = r.get("merge") or {}
    merged = bool(r.get("merged"))
    # A real update that merged is recorded but isn't the cell's PASS; one that merged nothing is.
    try_ran = needs.get("try") == "PASS" and a.mode in ("try", "update") and not merged
    standing = {n: list(getattr(details[n], "standing", []) or []) for n in details if getattr(details[n], "standing", None)}
    skips = {n: str(d.why)[:200] for n, d in details.items() if needs.get(n) == "SKIP" and d.why}     # contract 1.3: SKIP names why
    need_notes = {n: str(d.why)[:200] for n, d in details.items() if needs.get(n) != "SKIP" and d.why}   # a PASS's note (image:free, ...)
    extra = {
        "mode": a.mode, "try_ran": bool(try_ran), "merged": merged, "merge_why": None if merged else merge.get("why"),
        "merge": {"decided": merge.get("decided"), "why": merge.get("why"), "gate": merge.get("gate")},
        "pkg_changed": merge.get("pkg_changed"), "steps": merge.get("steps"), "new_lines": len(new_lines),
        "fingerprint": _fingerprint(work, guest), "reserved_gib": r.get("reserved_gib"), "overrun": bool(r.get("overrun")),
        "generation_before": r.get("generation_before"), "generation_after": r.get("generation_after"),
        "trim_rc": r.get("trim_rc"), "to_class_only": bool(to_class), "standing": standing, "hook_failed": hook_failed,
        "skips": skips, "notes": need_notes,
    }
    if prev:
        extra["rerun_of"] = (ce or {}).get("rerun_of")
    planned = (ce or {}).get("planned_key")
    key_now = m.get("key") or (_keys.key(cell, inputs) if inputs else None)
    invalid = []
    if planned and planned != key_now:
        invalid.append("its inputs as run differ from the plan's")
    if unmeasured:
        invalid.append("not measured: " + ", ".join(unmeasured[:5]))
    if not ce:
        invalid.append("no cell entry (T2_CELLS)")
    valid = not invalid
    write_steps(a.out, merge.get("steps"))
    note = "; ".join(notes + invalid)[:1000] or None
    row = dict(common, origin=f"guest:{a.run}:{guest}:{a.attempt}", run=a.run, rule=(ce or {}).get("rule") or "fresh:names",
               reasons=[str(x)[:200] for x in ((ce or {}).get("reasons") or [])][:20], planned_key=planned, valid=valid, verdict=verdict,
               needs=needs, counts=_counts(needs), fails=_cut_fails(fails), ram_peak_kib=r.get("ram_peak_kib"), extra=extra, note=note)
    row["class"] = klass
    write_summary(a.out, {"verdict": verdict, "class": klass, "needs": needs, "fails": row["fails"], "to_class_only": bool(to_class),
                          "overrun": bool(r.get("overrun")), "harness": harness_why, "tail": r.get("tail"),
                          "peak": r.get("ram_peak_kib"), "merged": merged, "merge_why": extra["merge_why"], "steps": extra["steps"],
                          "wall_s": wall, "new_lines": len(new_lines), "hook_failed": hook_failed, "phase": r.get("phase"),
                          "pkg_changed": extra["pkg_changed"], "fingerprint": extra["fingerprint"], "skips": skips, "notes": need_notes,
                          "generation_before": r.get("generation_before"), "generation_after": r.get("generation_after")})
    return hostctx.row(**row) if hostctx is not None else row


def _updates(work, guest):
    d = _json(os.path.join(work, "vms", guest, "image-state"))
    return (d or {}).get("updates") if isinstance(d, dict) else None


def _fingerprint(work, guest):
    d = _json(os.path.join(work, "vms", guest, "image-fingerprint.json"))
    st = _json(os.path.join(work, "vms", guest, "image-state"))
    if isinstance(d, dict) and isinstance(st, dict) and d.get("generation") == st.get("generation"):
        return (d.get("holt") or {}).get("digest")
    return None


def _counts(needs):
    c = {"PASS": 0, "FAIL": 0, "LIMIT": 0}
    for v in needs.values():
        if v == "PASS":
            c["PASS"] += 1
        elif v in ("FAIL", "DIFF"):
            c["FAIL"] += 1
        elif v in ("LIMIT", "LIMIT-NEW"):
            c["LIMIT"] += 1
    return c


def _start_of(result, now, wall):
    from . import parse_utc
    t0 = parse_utc(now)
    if t0 is not None and wall:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0 - wall))
    return now


def write_steps(out, steps):
    if not steps:
        return
    try:
        with open(os.path.join(out, "steps.txt"), "w", encoding="utf-8") as f:
            for s in steps:
                f.write(str(s) + "\n")
    except OSError:
        pass
