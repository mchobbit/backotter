"""Tiers 1 and 2 (backshot2.md, "Tiers"): one case through HoltTest1's
driver, ht1.sh, with HT_VARIANT=bs2 (testvm/backshot2/ht1-bs2.sh).  Run as
root under a cmp-run.sh --reserve (bs2-host tier1 does it from the queue).

ht1.sh's work goes in /mnt/bko-ram/backshot2/RUN/CASE and ends in
/bkoscratch/backshot2/RUN/CASE (HT_WORK and HT_RECORDS, a pair): a pass
keeps its records only, a failure everything.  The verdict comes from its
report: PASS; HARNESS when what failed is the rig's (no SSH, the rescuer,
the host), rerun once; FAIL otherwise, with the first FAIL line as the
failing check (the shrinker keeps a case failing at that check).

A RESULT: PASS is a PASS only when every phase the report's header names
("phases: source backup rebuild verify") began (its "== P" line) and ended
(its row in logs/phases.tsv): bash abandons ht1.sh's whole phase loop on an
expansion error in a hook, and ht1.sh then says RESULT: PASS for the phases
it never ran (bs2a-20261114-0655).  Short of that: HARNESS, "phase P never
ran (log ends: ...)".
"""

import json
import os
import re
import subprocess
import time

from . import case as C, ledger as L

HARNESS = [r"^FAIL source boots", r"^FAIL rescuer boots", r"^FAIL rescuer setup", r"rescue image:", r"over its reservation",
           r"^FAIL mount the source", r"^FAIL rustic-bko init", r"the pin changed", r"^FAIL fsmeta record", r"bs2-host apply",
           r"^FAIL the rescuer's QEMU didn't quit"]


def check_of(line):
    """A FAIL line's check, without its numbers: what the shrinker keeps failing."""
    t = re.sub(r"^FAIL ", "", line)
    t = re.split(r"[:(]", t, maxsplit=1)[0].strip()
    return re.sub(r"\d+", "N", t)[:80]


SUMMARY = re.compile(r"^(RAM disk: |disk \()")   # ht1-lib's ht_summary: the line before RESULT


def log_end(text):
    """The last line before ht_summary's (else the last line): where a run stopped."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    for i, l in enumerate(lines):
        if SUMMARY.match(l):
            lines = lines[:i]
            break
    return lines[-1][-200:] if lines else "nothing"


def phase_gaps(report, phases_tsv, log=None):
    """Why a RESULT: PASS isn't one: the header's phases that never began or
    never ended, with where the log (else the report) ends; None when all did."""
    lines = report.splitlines()
    head = next((l for l in lines if l.startswith("HoltTest1 ")), "")
    m = re.search(r", phases: (.+)$", head)
    want = m.group(1).split() if m else []
    if not want:
        return f"no phases in the HoltTest1 header (log ends: {log_end(log or report)})"
    began = {m.group(1) for m in (re.match(r"^== (\w+)", l) for l in lines) if m}
    ended = {l.split("\t", 1)[0] for l in (phases_tsv or "").splitlines()[1:] if l.strip()}
    gaps = [f"phase {p} never {'ran' if p not in began else 'ended'}" for p in want if p not in began or p not in ended]
    if not gaps:
        return None
    return f"{'; '.join(gaps)} (log ends: {log_end(log or report)})"


def verdict_of(report, phases_tsv=None, log=None):
    """REPORT (report.txt), PHASES_TSV (logs/phases.tsv, None: not read) and
    LOG (ht1.sh's stdout and stderr): (verdict, check, why)."""
    fails = [l for l in report.splitlines() if l.startswith("FAIL ")]
    res = next((l for l in report.splitlines() if l.startswith("RESULT: ")), "")
    if res.startswith("RESULT: PASS") and not fails:
        gap = phase_gaps(report, phases_tsv, log)
        if gap:
            return "HARNESS", gap, [gap]
        return "PASS", None, []
    if not res:
        return "HARNESS", "no RESULT line", fails
    if not fails:
        return "HARNESS", res, []
    first = fails[0]
    if any(re.search(p, first) for p in HARNESS):
        return "HARNESS", check_of(first), fails
    return "FAIL", check_of(first), fails


def read_or(path, default=None):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return default


def run_once(case, name, source, tier, run, port, here, testvm, root, holt):
    cdir = f"{root}/runs/{run}/cases"
    os.makedirs(cdir, exist_ok=True)
    cpath = f"{cdir}/{name}.case"
    C.write(cpath, dict(case, name=name))
    env = dict(os.environ, HT_VARIANT="bs2", BS2_CASE=cpath, BS2_NAME=name, BS2_TIER=str(tier),
               BS2_HOST=os.path.join(here, "bs2-host"), HT_WORK="/mnt/bko-ram/backshot2", HT_RECORDS=root,
               HT_HOLT=holt, HT_PORT=str(port), HT_QUICK="1")
    log = f"{root}/runs/{run}/logs/{name}.t{tier}.log"
    os.makedirs(os.path.dirname(log), exist_ok=True)
    t0 = time.time()
    with open(log, "w") as f:
        r = subprocess.run([os.path.join(testvm, "holttest1d", "ht1.sh"), source, f"{run}-t{tier}"], env=env,
                           stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
    k = f"{root}/{run}-t{tier}/{name}"
    rep, tsv, out = (read_or(p) for p in (f"{k}/report.txt", f"{k}/logs/phases.tsv", log))
    v, check, fails = verdict_of(rep or "", tsv, out)
    if r.returncode == 2 and not rep:
        v, check = "HARNESS", "ht1.sh refused to start"
        try:
            with open(log) as f:
                fails = [f.read()[-300:]]
        except OSError:
            pass
    return v, check, fails, time.time() - t0, k


def run(case, name, source, gen, dump, tier, run_name, port, here, testvm, root, holt, bins):
    v, check, fails, wall, k = run_once(case, name, source, tier, run_name, port, here, testvm, root, holt)
    attempt = 1
    if v == "HARNESS":
        # Test2's flake rule: a harness failure reruns once (its K goes to K.1).
        attempt = 2
        v, check, fails, wall2, k = run_once(case, name, source, tier, run_name, port, here, testvm, root, holt)
        wall += wall2
    line = {"case": name, "hash": C.case_hash(case), "source": source, "gen": gen, "tier": tier, "verdict": v,
            "check": check, "why": fails[:5], "dir": k, "attempt": attempt, "wall_s": round(wall),
            "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(f"{root}/runs/{run_name}/tier{tier}.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")
    L.write_rows(run_name, source, gen, [(name, case, v, fails or [check or ""])], tier=tier, wall=wall, bins=bins)
    print(json.dumps(line))
    return 0 if v == "PASS" else 1
