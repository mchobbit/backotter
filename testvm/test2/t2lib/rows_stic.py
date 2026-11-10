"""SticTest ledger rows: what one check.sh run did, built on the host that ran it
(contract 2.2 and R4.4; rulings R1, R3, R10, R26, R28).

drive.py runs, as soon as it sees a guest's exit file (or marks it LOST), and
once more at the end for any row still missing:

    python3 -B TOOL row stic --mark MARK --guest GUEST --bin BIN --load N --waits S
        --attempt A --checks DIR [--run RUN --cells CELLS] [--lost]

and t2-host calls build(argv, hostctx), appends the row and prints its id
(origin stic:MARK:GUEST:A: a repeat appends nothing).  build() reads, and
writes nothing:
  - ~/backotter-work/stictest1/GUEST/logs/MARK/run.out and .start, .end, .exit
    (the check's PASS/FAIL/LIMIT lines are what it printed: drive.py's
    results.tsv counts the same lines; the log dir's results.json or lines.txt
    only when run.out has none);
  - CELLS (the run's host cells.json, Test2 runs): the cell's word, rule,
    reasons, planned key, attempt, rerun_of, flake and limits_classed, and the
    copy (id, path);
  - the tree the checks ran from: a Test2 run's private copy (its T2-COPY.json),
    else SticTest1's map, testvm/stictest1 from DIR and testvm from the synced
    ~/backotter-work/testvm (R28);
  - this host's facts (facts_host.py, in-process) for the cell's inputs
    (keys.measure over testvm/test2/stic/manifests.json), and the binaries the
    check got, hashed (R26).

The inputs are measured when the row is written, which can be long after the
check began (a resumed or re-run mark adopts checks that ended days ago).  So
every file an input stands for is looked at: the tree's (harness, judge,
plumbing, shared:), the binaries, their .digest files and the set's
SHA256SUMS, the guests' image-state and guest files (a guest keyed by its
img: sum: its disks) and the host inputs.  One modified after run.out.start
(sync-hosts.sh, the copies and --private-copy write with rsync -rlp, which
leaves a changed file the time of the copy) means the check may have run
other inputs than the row names: the row is valid: false.  A Test2 row is
also invalid without its cells.json entry or a planned key.

Verdicts and classes (the stop rules count the classes):
    PASS, pass          nothing FAILs, every LIMIT classed, exit 0
    FAIL, fail          a FAIL line, or a LIMIT not classed (LIMIT-NEW)
    HARNESS, harness    only harness FAILs (boot, copy, binaries, check ran,
                        harness, guest boots), exit 0 with no FAIL line missing,
                        a non-zero exit with no FAIL line, or no verdict lines
    LOST, harness       no exit file (drive.py: --lost)
    FLAKY-FAIL, fail    a PASS on attempt 2 (the rerun after a FAIL)
    PASS, known-flake   the same when cells.json marks attempt 1's FAILs a
                        known flake (flakes.json)
gm = (wall - waits) / 60, wall from run.out.start to .end.
"""

import argparse
import json
import os
import re
import sys
import time

from . import file_sha256, parse_utc, utc_now

SPEC = "testvm/test2/stic/manifests.json"
LIMITS = "testvm/test2/stic/limits.json"
FLAKES = "testvm/test2/stic/flakes.json"
VERDICT_RX = re.compile(r"^(PASS|FAIL|LIMIT) (.*)$")
HARNESS_NEEDS = ("boot", "copy", "binaries", "check ran", "harness", "harness-tools", "harness-copy", "guest boots")
RANK = {"PASS": 0, "SKIP": 0, "LIMIT": 1, "DIFF": 2, "LIMIT-NEW": 3, "FAIL": 4}
OS_RESTIC = {"haiku": "linux"}   # drive.py's RESTIC_FROM: haiku6 reads its repository with Linux's restic-bko
MAX_FAILS, MAX_LEN = 20, 200


# --- the check's lines ----------------------------------------------------------------

def parse_lines(text):
    """[(verdict, need, detail)] of a check's PASS/FAIL/LIMIT lines ("VERDICT need: detail")."""
    out = []
    for line in (text or "").splitlines():
        m = VERDICT_RX.match(line.rstrip("\r"))
        if not m:
            continue
        need, _, detail = m.group(2).partition(": ")
        out.append((m.group(1), need.strip(), detail))
    return out


def results_json_lines(data):
    """The (verdict, need, detail) of a results.json, whatever its check's layout
    ({"results": [...]}, {"needs": [...]}, {"items": [...]}, or {need: {verdict, detail}})."""
    rows = None
    if isinstance(data, dict):
        for k in ("results", "needs", "items"):
            if isinstance(data.get(k), list):
                rows = data[k]
                break
        if rows is None and data and all(isinstance(v, dict) for v in data.values()):
            rows = [dict(v, need=k) for k, v in data.items()]
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        v = r.get("verdict") or r.get("result") or r.get("status")
        if v not in ("PASS", "FAIL", "LIMIT") or not r.get("need"):
            continue
        d = r.get("detail") if r.get("detail") is not None else r.get("details")
        if isinstance(d, list):
            d = "; ".join(str(x) for x in d[:4])
        out.append((v, str(r["need"]), "" if d is None else str(d)))
    return out


def worst(a, b):
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b


def flake_match(entry, fail_lines, run_out=""):
    """Whether a flakes.json entry explains these FAIL lines: its pattern found in
    them (or in run.out, "over": "run.out"), and every FAIL line matching its
    "fails" regex when it has one."""
    try:
        text = run_out if entry.get("over") == "run.out" else "\n".join(fail_lines)
        if not fail_lines or not re.search(entry["pattern"], text, re.M):
            return False
        every = entry.get("fails")
        return not every or all(re.search(every, l) for l in fail_lines)
    except (re.error, KeyError, TypeError):
        return False


def known_flake(flakes, guest, fail_lines, run_out=""):
    """The first flakes.json entry for GUEST that explains these FAIL lines, or None."""
    for e in flakes or []:
        if isinstance(e, dict) and e.get("guest") == guest and flake_match(e, fail_lines, run_out):
            return e
    return None


def judge(lines, exit_code, lost=False, limits_classed=None, attempt=1, flake=None, prev_needs=None):
    """(verdict, class, needs, counts, fails, notes) of one run's lines.  EXIT_CODE
    is run.out.exit's number (None: no exit file).  LIMITS_CLASSED: the LIMIT
    needs already classed (None: every LIMIT counts as classed).  FLAKE: cells.json's
    flake for attempt 2 (attempt 1's FAILs were a known flake)."""
    needs, counts, fails, notes = {}, {"PASS": 0, "FAIL": 0, "LIMIT": 0}, [], []
    classed = None if limits_classed is None else set(limits_classed)
    new_limits = []
    for v, need, detail in lines:
        counts[v] += 1
        nv = v
        if v == "LIMIT" and classed is not None and need not in classed:
            nv = "LIMIT-NEW"
            if need not in new_limits:
                new_limits.append(need)
        needs[need] = worst(needs[need], nv) if need in needs else nv
        if v == "FAIL":
            fails.append(f"FAIL {need}: {detail}"[:MAX_LEN])
    if lines and exit_code == 0 and not lost:
        for n in sorted(set(prev_needs or ()) - set(needs)):
            needs[n] = "FAIL"
            fails.append(f"FAIL {n}: the check printed no verdict for this need (it did in its last pass)"[:MAX_LEN])
    real_fails = [n for n, v in needs.items() if v == "FAIL" and n not in HARNESS_NEEDS]
    harness_fails = [n for n, v in needs.items() if v == "FAIL" and n in HARNESS_NEEDS]
    for n in new_limits[:5]:
        notes.append(f"LIMIT-NEW {n}: not in limits.json or the last pass")
    if lost or exit_code is None:
        notes.append("lost: no exit file (the host rebooted, or the check was killed)" if lost
                     else "no exit file")
        verdict, cls = "LOST", "harness"
    elif real_fails or new_limits:
        verdict, cls = "FAIL", "fail"
        if exit_code == 0 and real_fails:
            notes.append("exit 0 with FAIL lines")
    elif harness_fails:
        verdict, cls = "HARNESS", "harness"
    elif not lines:
        verdict, cls = "HARNESS", "harness"
        notes.append(f"no PASS/FAIL/LIMIT lines (exit {exit_code})")
    elif exit_code != 0:
        verdict, cls = "HARNESS", "harness"
        notes.append(f"exit {exit_code} without a FAIL line")
    else:
        verdict, cls = "PASS", "pass"
    if attempt >= 2 and verdict == "PASS":
        if flake:
            cls = "known-flake"
            why = flake.get("why") if isinstance(flake, dict) else str(flake)
            notes.append(f"attempt 1's FAILs are a known flake: {why}"[:MAX_LEN])
        else:
            verdict, cls = "FLAKY-FAIL", "fail"
            notes.append("passed on attempt 2: FLAKY-FAIL")
    return verdict, cls, needs, counts, fails[:MAX_FAILS], notes


def gm_of(load, start, end, waits):
    """Guest-minutes without the waits for slots and room."""
    if start is None or end is None or end < start:
        return 0.0
    return round(max(0.0, (end - start) - (waits or 0)) / 60.0, 3)


# --- files ---------------------------------------------------------------------------------

def _read(path, limit=8 << 20):
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def _num(path):
    t = _read(path, 64)
    t = (t or "").strip()
    return int(t) if t.isdigit() else None


def _json(path):
    t = _read(path)
    if t is None:
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


def log_dir(mark_dir, run_out, start):
    """The run's own log dir: the last "logs: DIR" (or "(wall N s; logs DIR)") line
    of run.out, else the newest directory under MARK_DIR made since the start."""
    found = None
    for line in (run_out or "").splitlines():
        m = re.match(r"^(?:logs:? |\(wall [0-9]+ s; logs )(\S+?)\)?$", line.strip())
        if m:
            found = m.group(1)
    if found and os.path.isdir(found):
        return found
    best = None
    try:
        for n in os.listdir(mark_dir):
            p = os.path.join(mark_dir, n)
            if os.path.isdir(p):
                t = os.stat(p).st_mtime
                if (start is None or t >= start - 2) and (best is None or t > best[0]):
                    best = (t, p)
    except OSError:
        pass
    return best[1] if best else None


def set_name(bin_dir, work):
    """The facts' set name for a BIN dir: roundNAME for WORK/roundNAME/bin, bko-bin, else the path."""
    b = os.path.normpath(bin_dir)
    w = os.path.normpath(work)
    if os.path.dirname(b) == w:
        return os.path.basename(b)
    if os.path.basename(b) == "bin" and os.path.dirname(os.path.dirname(b)) == w:
        return os.path.basename(os.path.dirname(b))
    return b


def expand_host(p, home):
    if p is None:
        return None
    if p == "~" or p.startswith("~/"):
        return home + p[1:]
    return p if os.path.isabs(p) else os.path.join(home, p)


def tree_path(src, path):
    """The file a repo PATH is in SRC (a keys.TreeSource, or a MapSource of them), or None."""
    pick = getattr(src, "_pick", None)
    s = pick(path) if pick is not None else src
    fn = getattr(s, "_abs", None) if s is not None else None
    try:
        return fn(path) if fn is not None else None
    except (TypeError, ValueError):
        return None


def input_files(m, src, work, bdir, facts, home):
    """[(input name, host path)] of the files the measured inputs M stand for."""
    out = []
    inp = m.get("inputs") or {}
    for grp in ("harness", "judge", "plumbing"):
        for name in inp.get(grp) or {}:
            if name.startswith("shared:"):
                out.append((name, os.path.join(work, name[len("shared:"):])))
            elif name.startswith("host:"):
                out.append((name, expand_host(name[len("host:"):], home)))
            elif not name.startswith("git:"):
                p = tree_path(src, name.split("#", 1)[0])
                if p:
                    out.append((name, p))
    names = list(inp.get("bin") or {})
    for name in names:
        out.append((name, os.path.join(bdir, name[:-len("#digest")] + ".digest" if name.endswith("#digest") else name)))
    if names:
        out.append(("SHA256SUMS", os.path.join(bdir, "SHA256SUMS")))
    guests = (facts or {}).get("guests") or {}
    for grp in ("guest", "xguest", "host"):
        for name in inp.get(grp) or {}:
            if name.startswith("host:"):
                out.append((name, expand_host(name[len("host:"):], home)))
                continue
            g, sep, f = name.partition(":")
            if not sep or grp == "host":
                continue
            gdir = os.path.join(work, "vms", g)
            if f == "generation":
                out.append((f"{g}:image-state", os.path.join(gdir, "image-state")))
                gf = guests.get(g) or {}
                if not gf.get("generation"):   # keyed by its disks' sum
                    out += [(f"{g}:{os.path.basename(d['path'])}", d["path"]) for d in gf.get("disks") or []
                            if isinstance(d, dict) and d.get("path")]
            elif f == "fingerprint":
                out.append((f"{g}:image-fingerprint", os.path.join(gdir, "image-fingerprint.json")))
            else:
                out.append((name, os.path.join(gdir, f)))
    return out


def changed_after(start, items, limit=4000):
    """The (name, path) ITEMS whose file was modified after START (epoch seconds; a
    directory: any file under it, up to LIMIT files), in order.  start is date +%s of
    the check's start, so a change in its first second counts as before it."""
    out = []
    for name, p in items:
        if not p:
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        m = st.st_mtime
        if os.path.isdir(p):
            n = 0
            for dp, dns, fns in os.walk(p):
                for fn in fns:
                    n += 1
                    try:
                        m = max(m, os.stat(os.path.join(dp, fn)).st_mtime)
                    except OSError:
                        pass
                if n >= limit:
                    break
        if m >= start + 1:
            out.append((name, p))
    return out


# --- build ------------------------------------------------------------------------------------

def _args(argv):
    ap = argparse.ArgumentParser(prog="t2-host row stic", add_help=False)
    ap.add_argument("--mark", required=True)
    ap.add_argument("--guest", required=True)
    ap.add_argument("--bin", required=True, help="the round's binaries (the check got BIN/OS)")
    ap.add_argument("--load", type=float, default=1)
    ap.add_argument("--waits", type=float, default=0)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--checks", default=None, help="the testvm/stictest1 the checks ran from")
    ap.add_argument("--run", default=None)
    ap.add_argument("--cells", default=None)
    ap.add_argument("--lost", action="store_true")
    ap.add_argument("--work", default=None)
    ap.add_argument("--now", default=None, help="UTC ISO time (tests)")
    ap.add_argument("--facts", default=None, help="a facts JSON file instead of the probe (tests)")
    ap.add_argument("--host", default=None, help="the host name (tests)")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", a.mark) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", a.guest):
        raise SystemExit("bad --mark or --guest")
    return a


def load_cells(path, guest):
    """(cells.json, the guest's entry or None)."""
    data = _json(path) if path else None
    if not isinstance(data, dict):
        return None, None
    ent = (data.get("cells") or {}).get(guest)
    return data, ent if isinstance(ent, dict) else None


def last_pass_limits(ledger_path, cell):
    """The LIMIT needs of the cell's newest PASS-like run row in this host's ledger."""
    best = None
    try:
        with open(ledger_path, "rb") as f:
            for raw in f:
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                if (isinstance(r, dict) and r.get("cell") == cell and r.get("kind") == "run"
                        and r.get("verdict") == "PASS" and r.get("valid")):
                    if best is None or (r.get("t") or "") >= (best.get("t") or ""):
                        best = r
    except OSError:
        return set()
    return {n for n, v in ((best or {}).get("needs") or {}).items() if v in ("LIMIT", "LIMIT-NEW")}


def test1_tree(hostctx, work, checks, home):
    """SticTest1's tree map (R28): (source, base, commit info, notes)."""
    from . import keys
    notes = []
    synced = os.path.join(work, "testvm", "stictest1")
    checks = os.path.normpath(checks or synced)
    ti = hostctx.tree_info(work) if hostctx is not None else {"commit": None, "committed": False, "dirty": None,
                                                              "copy": "synced"}
    stamp = ti.get("commit")
    if checks == os.path.normpath(synced):
        src = keys.TreeSource(work)
        copy = "synced"
        committed = bool(ti.get("committed"))
    else:
        src = keys.MapSource([keys.TreeSource(checks, mount="testvm/stictest1"), keys.TreeSource(work)])
        copy = "st1-wt" if "/st1-wt/" in checks + "/" else "tree:" + checks
        wt_commit = (_read(os.path.join(os.path.dirname(os.path.dirname(checks)), "COMMIT"), 128) or "").strip()
        committed = bool(ti.get("committed")) and bool(stamp) and wt_commit[:len(stamp)] == stamp and len(wt_commit) == 40
        if not committed:
            notes.append(f"checks from {copy} at {wt_commit[:7] or 'an unknown commit'}, testvm synced at "
                         f"{stamp or 'unknown'}: never counts (R28)")
    if not committed:
        return src, {"commit": stamp, "committed": False, "dirty": None, "copy": copy}, notes
    return src, {"commit": stamp, "committed": True, "dirty": {}, "copy": copy}, notes


def build(argv, hostctx=None):
    """The row of one check run (see the module docstring)."""
    from . import keys
    a = _args(argv)
    host = a.host or getattr(hostctx, "host", None) or os.uname().nodename.split(".")[0]
    work = os.path.abspath(os.path.expanduser(a.work or getattr(hostctx, "work", None) or "~/backotter-work"))
    home = os.path.dirname(work)
    now = a.now or utc_now()
    notes, unmeasured = [], []
    g, mark = a.guest, a.mark
    cell = f"stic:{g}"

    # The run's own files.
    mdir = os.path.join(work, "stictest1", g, "logs", mark)
    out = os.path.join(mdir, "run.out")
    start, end, code = _num(out + ".start"), _num(out + ".end"), _num(out + ".exit")
    if start is None:
        raise SystemExit(f"no {out}.start: {g} never started under mark {mark}")
    if code is None and not a.lost:
        raise SystemExit(f"no {out}.exit: {g} still runs (drive.py passes --lost for a lost one)")
    run_out = _read(out) or ""
    ldir = log_dir(mdir, run_out, start)
    lines, source = parse_lines(run_out), "run.out"
    if not lines and ldir:
        rj = _json(os.path.join(ldir, "results.json"))
        lines, source = results_json_lines(rj), "results.json"
        if not lines:
            lines, source = parse_lines(_read(os.path.join(ldir, "lines.txt"))), "lines.txt"
    if a.lost and end is None:
        end = int(parse_utc(now) or time.time())

    # Test2's cells file (a Test2 row even when it can't be read: then it never counts), or SticTest1.
    t2 = bool(a.cells)
    cells, ce = load_cells(expand_host(a.cells, home), g) if t2 else (None, None)
    if t2 and cells is None:
        notes.append(f"no readable cells file {a.cells}: never counts")
    elif t2 and ce is None:
        notes.append(f"{g} not in {a.cells}: never counts")
    attempt = int((ce or {}).get("attempt") or a.attempt or 1)
    run = a.run or (cells or {}).get("run") or mark
    word = (cells or {}).get("word") or (f"SticTest2 run {run}" if t2 else f"SticTest1 drive.py {mark}")
    rule = (ce or {}).get("rule") or ("test1" if not t2 else "unplanned")
    reasons = list((ce or {}).get("reasons") or [])
    planned = (ce or {}).get("planned_key") if t2 else None

    # The tree, and its commit.
    if t2:
        cp = ((cells or {}).get("copy") or {})
        base = expand_host(cp.get("path"), home) if cp.get("path") else None
        if not base and a.checks:
            base = os.path.dirname(os.path.dirname(expand_host(a.checks, home)))
        if not base or not os.path.isdir(base):
            raise SystemExit(f"no private copy for run {run} ({cp.get('path') or a.checks})")
        src = keys.TreeSource(base)
        ti = hostctx.tree_info(base) if hostctx is not None else {}
        tinfo = {"commit": ti.get("commit") or cp.get("commit"), "committed": bool(ti.get("committed")),
                 "dirty": ti.get("dirty") if ti else cp.get("dirty"), "copy": ti.get("copy") or cp.get("id") or "copy"}
    else:
        src, tinfo, tn = test1_tree(hostctx, work, expand_host(a.checks, home) if a.checks else None, home)
        notes += tn

    # The cell's inputs, measured here.
    specs = keys.Specs(src)
    sname = set_name(expand_host(a.bin, home), work)
    if a.facts:
        facts = _json(a.facts) or {}
    else:
        want = keys.wants(specs, ("stic",), sets=[sname])
        facts = hostctx.facts(want) if hostctx is not None else {}
    try:
        m = keys.measure(cell, src, facts, specs, {"set": sname})
    except KeyError as e:
        raise SystemExit(f"{cell}: {e}")
    unmeasured += m.get("unknown") or []
    notes += [n for n in (m.get("notes") or []) if n not in notes]

    # The binaries the check got (R26): hashed, against the set's SHA256SUMS.
    bdir = expand_host(a.bin, home)
    sset = ((facts.get("sets") or {}).get(sname) or {})
    bad_bins = []
    entry = specs.get("stic").entry(g) if specs.get("stic") else {}
    for name in entry.get("bins") or []:
        if "/" not in name:
            continue
        have = file_sha256(os.path.join(bdir, name))
        claim = (sset.get("sums") or {}).get(name)
        if have is None or claim is None or have != claim:
            bad_bins.append(name)
    if bad_bins:
        notes.append("binaries differ from SHA256SUMS: " + ", ".join(bad_bins))

    # LIMITs classed: cells.json's, else limits.json's and the last pass's.
    lc = (ce or {}).get("limits_classed")
    if lc is None:
        data, _ = specs.json(LIMITS)
        lc = set(((data or {}).get(g) or {}).keys()) | last_pass_limits(getattr(hostctx, "ledger_path", None) or "", cell)
    flakes, _ = specs.json(FLAKES)
    verdict, cls, needs, counts, fails, jn = judge(lines, code, a.lost, lc, attempt, (ce or {}).get("flake"),
                                                   (ce or {}).get("prev_needs"))
    notes += jn
    fl = None
    if verdict in ("FAIL", "HARNESS", "LOST") and attempt == 1:
        full = [f"FAIL {n}: {d}" for v, n, d in lines if v == "FAIL"]
        fl = known_flake(flakes if isinstance(flakes, list) else [], g, full, run_out)
        if fl:
            notes.append((f"matches a known flake: {fl.get('why', '')}"
                          + (" (a pattern derived from the check, not yet seen in a run.out)"
                             if fl.get("evidence") == "derived" else ""))[:MAX_LEN])

    # What the check ran with is what the row names only if nothing changed since it began.
    moved = changed_after(start, input_files(m, src, work, bdir, facts, home))

    valid = not unmeasured and not bad_bins and not moved
    if planned is not None and planned != m["key"]:
        valid = False
        notes.append(f"key {m['key'][:12]} isn't the planned {str(planned)[:12]}: inputs changed")
    if moved:
        notes.append(f"inputs changed after the check began ({len(moved)}: "
                     + ", ".join(n for n, _ in moved[:4]) + "): never counts")
    if unmeasured:
        notes.append("unmeasured: " + ", ".join(unmeasured[:6]))
    if t2 and ce is None:
        valid = False
    elif t2 and planned is None:
        valid = False
        notes.append("no planned key in cells.json: never counts")

    meta_bin = {"set": sname, "dir": a.bin}
    names = [n for n in entry.get("bins") or [] if "/" in n]
    meta_bin["commits"] = {n: (sset.get("commits") or {}).get(n) for n in names}
    meta_bin["digests"] = {n: ((sset.get("digest_texts") or {}).get(n) or "")[:2000] for n in names}
    meta_bin["build_info"] = sset.get("build_info")
    peak = _num(os.path.join(ldir, "ramdisk-peak-kib")) if ldir else None
    row = {
        "origin": f"stic:{mark}:{g}:{attempt}",
        "family": "stic", "cell": cell, "kind": "run",
        "start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start)),
        "host": host, "run": run, "word": word, "rule": rule, "reasons": reasons, "attempt": attempt,
        "inputs": m["inputs"], "key": m["key"], "groups": m["groups"], "spec": m["spec"],
        "planned_key": planned, "valid": bool(valid),
        "verdict": verdict, "class": cls, "needs": needs, "counts": counts, "fails": fails,
        "gm": gm_of(a.load, start, end, a.waits), "wall_s": max(0, (end or start) - start),
        "waits_s": int(round(a.waits or 0)),
        "wear": {f"stic:{mark}:{g}": None}, "ram_peak_kib": peak, "seed": None,
        "commit": tinfo.get("commit"), "committed": bool(tinfo.get("committed")), "dirty": tinfo.get("dirty"),
        "copy": tinfo.get("copy") or "unknown",
        "logs": f"{host}:{ldir or mdir}",
        "meta": {"bin": meta_bin, "round": sname, "mark": mark, "load": a.load,
                 "checks": a.checks, "exit": code},
        "extra": {"source": source, "rerun_of": (ce or {}).get("rerun_of")},
        "via": None,
    }
    if moved:
        row["extra"]["changed"] = [n[:MAX_LEN] for n, _ in moved[:MAX_FAILS]]
    if fl:
        row["extra"]["flake"] = {k: fl.get(k) for k in ("guest", "pattern", "why", "ref", "evidence") if k in fl}
    if (ce or {}).get("flake"):
        row["extra"]["flake_attempt1"] = (ce or {}).get("flake")
    if notes:
        row["note"] = "; ".join(dict.fromkeys(notes))[:600]
    return row


def main(argv=None):   # pragma: no cover - t2-host is the entry point; this prints a row by hand
    argv = sys.argv[1:] if argv is None else argv
    print(json.dumps(build(argv), indent=1, sort_keys=True))


if __name__ == "__main__":   # pragma: no cover
    main()
