#!/usr/bin/env python3
"""A stand-in for testvm/backshot2/bs2 (T2_BATCH_BS2, tests): --hosts H,H WORD [ARG...].  It
makes the run's queue (FAKE_BS2_CASES cases, default 4) under T2_BATCH_BS2_ROOT, starts the fake
bs2-host queue beside this script detached, prints `run RUN: bs2 status RUN` and exits, as bs2
does.  FAKE_BS2_REFUSE: it prints "bs2: not started" and exits 1.  Logs to MAC_ROOT/stub-calls.log."""
import os, subprocess, sys, time
a = sys.argv[1:]
i = a.index("--hosts")
hosts = a[i + 1].split(",")
del a[i:i + 2]
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(os.environ["T2_MAC_ROOT"], "stub-calls.log"), "a") as f:
    f.write(f"{time.time():.3f} {os.getpid()} BS2 {' '.join(a)} {','.join(hosts)}\n")
if os.environ.get("FAKE_BS2_REFUSE"):
    sys.exit("bs2: not started:\n  cage: no vms/alpine324")
run = time.strftime("bs2a-%Y%m%d-%H%M%S") + f"-{os.getpid() % 10000}"
rd = os.path.join(os.environ["T2_BATCH_BS2_ROOT"], "runs", run)
os.makedirs(rd)
with open(os.path.join(rd, "queue.txt"), "w") as f:
    f.write("".join(f"1 cases/c{n}.json\n" for n in range(int(os.environ.get("FAKE_BS2_CASES") or 4))))
subprocess.Popen(["bash", os.path.join(here, "bs2-host"), "queue", run, "--slots", "2"], stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
print(f"{hosts[0]}: started 4 cases on 2 slots")
print(f"run {run}: bs2 status {run}")
