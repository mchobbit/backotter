"""BackShot2's rows in Test2's ledger (backshot2.md, "Words"; test2.md, L1).

A row per case and tier, family "bs2", cell "bs2:SOURCE/CASEHASH@tTIER", on
this host's ledger (HOST_ROOT/ledger/HOST.jsonl).  Its key is Test2's,
from the hard inputs: the bko-holt and rustic-bko under test (bin), the
source's generation (guest), and BackShot2's own scripts (harness), so an
unchanged case at an unchanged key needn't run again (done() says).
Verdicts map onto the ledger's: PASS, FAIL and HARNESS as they are; INVALID
(the generator's fault) and UNSUPPORTED (a flag the build hasn't got) are
HARNESS rows with extra.bs2 saying which, never a FAIL.
"""

import glob
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTVM = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(TESTVM, "test2"))

HARNESS_FILES = ("backshot2/bs2lib/*.py", "backshot2/bs2-host", "holttest1d/ht1.sh", "holttest1d/ht1-lib.sh",
                 "holttest1d/judge.py", "compare/fsmeta.py")


def harness():
    out = {}
    for pat in HARNESS_FILES:
        for p in sorted(glob.glob(os.path.join(TESTVM, pat))):
            with open(p, "rb") as f:
                out["testvm/" + os.path.relpath(p, TESTVM)] = "sha256:" + hashlib.sha256(f.read()).hexdigest()
    return out


def cell(source, case_hash, tier):
    return f"bs2:{source.replace('run:', 'run-')}/{case_hash}@t{tier}"


def inputs(source, gen, bins):
    g = gen if gen and (gen.startswith("gen:") or gen.startswith("img:")) else f"ver:{gen or source}"
    return {"bin": dict(bins), "guest": {source: g}, "harness": harness()}


def rows(run, source, gen, results, tier, wall, bins, word="BackShot2Alpine"):
    from t2lib import ledger, utc_now
    from bs2lib import case as C
    inp = inputs(source, gen, bins)
    out = []
    per = int(wall / max(1, len(results)))
    for name, case, verdict, why in results:
        v, extra = verdict, {}
        if verdict in ("INVALID", "UNSUPPORTED"):
            v, extra = "HARNESS", {"bs2": verdict}
        cls = {"PASS": "pass", "FAIL": "fail", "HARNESS": "harness"}[v]
        h = C.case_hash(case)
        out.append(ledger.complete({
            "origin": f"bs2:{run}:{name}:t{tier}", "family": "bs2", "cell": cell(source, h, tier), "kind": "run",
            "run": run, "word": word, "rule": "bs2", "attempt": 1, "inputs": inp, "valid": True, "verdict": v,
            "class": cls, "commit": None, "committed": False, "copy": "bs2-wt", "start": utc_now(),
            "fails": [str(x)[:200] for x in why[:5]] if v != "PASS" else [], "wall_s": per,
            "extra": dict(extra, case=name, tier=tier)}))
    return out


def write_rows(run, source, gen, results, tier, wall, bins, word="BackShot2Alpine"):
    """Appends the rows; best effort (a ledger problem is noted, never fatal)."""
    if os.environ.get("BS2_NO_LEDGER"):
        return
    try:
        from t2lib import ledger, host_ledger_file as ledger_path
        rs = rows(run, source, gen, results, tier, wall, bins, word)
        bad = [e for r in rs for e in ledger.validate(r)]
        if bad:
            print(f"bs2: ledger rows not written: {bad[:3]}", file=sys.stderr)
            return
        ledger.Ledger(ledger_path()).append_rows(rs)
    except Exception as e:  # noqa: BLE001 - the ledger is a record, not the test
        print(f"bs2: ledger: {e}", file=sys.stderr)


def done(source, case_hash, tier, gen, bins, path=None):
    """Whether this host's ledger has a PASS at this case's key."""
    try:
        from t2lib import keys, ledger, host_ledger_file as ledger_path
        k = keys.key(cell(source, case_hash, tier), inputs(source, gen, bins))
        for r in ledger.read(path or ledger_path()).rows:
            if r.get("key") == k and r.get("verdict") == "PASS":
                return True
    except Exception:  # noqa: BLE001
        return False
    return False
