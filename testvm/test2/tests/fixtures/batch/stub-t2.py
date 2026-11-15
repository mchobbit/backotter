#!/usr/bin/env python3
"""A stub t2 for tests/test_batch_e2e.py (T2_BATCH_T2): it takes its family lock as a real run
does (run.acquire_locks, "pending" then the run id), makes a run dir, sleeps and ends.

    stub-t2.py WORD [ARG...] --hosts H,H        stub-t2.py resume RUN

T2_STUB_CONF (a JSON file) sets each word's behaviour, by its first argument:
    {"HoltTest2": {"sleep": 2, "exit": 0, "ignore_int": false, "busy_once": "/path/marker"}, ...}
busy_once: the first start (while MARKER isn't there) prints run.Busy's refusal and exits 2.
cells: {CELL: [VERDICT, REASON, LOGS]}: the plan's due cells, and a ledger row for each in
MAC_ROOT/ledger/mac.jsonl (VERDICT null: no row, as a cell whose host went away).
Every start and end is appended to MAC_ROOT/stub-calls.log.
"""

import json
import os
import signal
import sys
import time

T2 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, T2)

from t2lib import mac_root, utc_now          # noqa: E402
from t2lib import batchfile as bf             # noqa: E402
from t2lib import run as runmod               # noqa: E402


def note(text):
    with open(os.path.join(mac_root(), "stub-calls.log"), "a") as f:
        f.write(f"{time.time():.3f} {os.getpid()} {text}\n")


def main(argv):
    conf = {}
    if os.environ.get("T2_STUB_CONF"):
        with open(os.environ["T2_STUB_CONF"]) as f:
            conf = json.load(f)
    hosts = []
    if "--hosts" in argv:
        i = argv.index("--hosts")
        hosts = argv[i + 1].split(",")
        argv = argv[:i] + argv[i + 2:]
    if argv[0] == "resume":
        run = argv[1]
        fam = next(f for f, p in runmod.RUN_PREFIX.items() if run.startswith(p + "-"))
        c = conf.get("resume") or {}
        e = bf.Entry("t2", [{"gate": "GateTest2", "stic": "SticTest2", "holt": "HoltTest2", "guest": "GuestTest2",
                             "img": "ImgTest2", "bko": "BkoTest2", "sweep": "SweepTest2"}[fam]])
        try:
            locks = runmod.acquire_locks(bf.lock_list(e), run)
        except runmod.Busy as ex:
            print(f"t2: {ex}", flush=True)
            return 2
        note(f"RESUME {run}")
        time.sleep(float(c.get("sleep", 0.3)))
        runmod.write_text(os.path.join(runmod.run_dir(run), "report.txt"), f"stub report: {run} resumed\n")
        for lk in locks:
            lk.release()
        note(f"END {run}")
        return int(c.get("exit", 0))
    word = argv[0]
    c = conf.get(word) or {}
    e = bf.Entry("t2", argv)
    if c.get("busy_once") and not os.path.exists(c["busy_once"]):
        open(c["busy_once"], "w").close()
        print(f"t2: another {bf.lock_list(e)[0]} run holds its lock: run t2h-20261115T0000-beef, pid 1, "
              f"started {utc_now()}", flush=True)
        note(f"BUSY {word}")
        return 2
    try:
        locks = runmod.acquire_locks(bf.lock_list(e), "pending")
    except runmod.Busy as ex:
        print(f"t2: {ex}", flush=True)
        note(f"BUSY {word}")
        return 2
    if c.get("ignore_int"):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    fam = e.family
    run = runmod.new_run_id(fam)
    time.sleep(float(c.get("plan_s", 0.2)))
    for lk in locks:
        runmod.write_json(lk.info, {"run": run, "pid": os.getpid(), "start": utc_now()})
    rd = runmod.run_dir(run)
    cells = c.get("cells") or {}
    runmod.write_json(os.path.join(rd, "plan.json"), {"family": fam, "word": {"text": " ".join(argv)},
                                                      "header": {"facts": {h: {} for h in hosts}},
                                                      "cells": [{"cell": x, "status": "due"} for x in cells]})
    rows = []
    for cell, (verdict, reason, logs) in cells.items():
        if not verdict:
            continue
        r = {"v": 1, "id": f"mac-{run}-{len(rows):04d}", "cell": cell, "run": run, "kind": "run", "attempt": 1,
             "verdict": verdict, "class": {"PASS": "pass", "FAIL": "fail", "HARNESS": "harness", "LOST": "lost"}[verdict],
             "host": (hosts or ["mac"])[0], "fails": [reason] if reason else [], "t": utc_now(), "logs": logs,
             "valid": True, "committed": True, "gm": 1.0, "wall_s": 10, "origin": f"stub:{run}:{cell}"}
        from t2lib import ledger
        r["c"] = ledger.row_c(r)
        rows.append(json.dumps(r, sort_keys=True))
    if rows:
        os.makedirs(os.path.join(mac_root(), "ledger"), exist_ok=True)
        with open(os.path.join(mac_root(), "ledger", "mac.jsonl"), "a") as f:
            f.write("".join(x + "\n" for x in rows))
    for h in hosts:
        runmod.write_json(os.path.join(rd, f"cells-{h}.json"), {"host": h})
    note(f"START {word} {run} {','.join(hosts)} UNKNOWN_MAX={os.environ.get('T2_UNKNOWN_MAX')} "
         f"T2_SSH={os.environ.get('T2_SSH')}")
    print(f"stub: {word} as {run} on {','.join(hosts)}", flush=True)
    try:
        time.sleep(float(c.get("sleep", 0.5)))
    except KeyboardInterrupt:
        note(f"INT {run}")
        return 130
    finally:
        pass
    runmod.write_text(os.path.join(rd, "report.txt"), f"stub report: {run}\n")
    for lk in locks:
        lk.release()
    note(f"END {run}")
    return int(c.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
