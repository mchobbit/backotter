"""Tiers 1 and 2 (backshot2.md, "Tiers"): one case through HoltTest1's
driver, ht1.sh, with HT_VARIANT=bs2 (testvm/backshot2/ht1-bs2.sh).  Run as
root under a cmp-run.sh --reserve (bs2-host tier1 does it from the queue).

ht1.sh's work goes in /mnt/bko-ram/backshot2/RUN/CASE and ends in
/bkoscratch/backshot2/RUN/CASE (HT_WORK and HT_RECORDS, a pair): a pass
keeps its records only, a failure everything.  The verdict comes from its
report: PASS; HARNESS when what failed is the rig's (no SSH, the rescuer,
the host), rerun once; FAIL otherwise, with the first FAIL line as the
failing check (the shrinker keeps a case failing at that check).
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
    t = re.split(r"[:(]", t, 1)[0].strip()
    return re.sub(r"\d+", "N", t)[:80]


def verdict_of(report):
    fails = [l for l in report.splitlines() if l.startswith("FAIL ")]
    res = next((l for l in report.splitlines() if l.startswith("RESULT: ")), "")
    if res.startswith("RESULT: PASS") and not fails:
        return "PASS", None, []
    if not res:
        return "HARNESS", "no RESULT line", fails
    if not fails:
        return "HARNESS", res, []
    first = fails[0]
    if any(re.search(p, first) for p in HARNESS):
        return "HARNESS", check_of(first), fails
    return "FAIL", check_of(first), fails


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
    try:
        with open(f"{k}/report.txt") as f:
            rep = f.read()
    except OSError:
        rep = ""
    v, check, fails = verdict_of(rep)
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
