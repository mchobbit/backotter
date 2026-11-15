#!/usr/bin/env python3
"""A stand-in for drive.py (T2_BATCH_DRIVE, tests): [--private-copy] --hosts H,H --out DIR
--logs-mark M ROUND [GUEST...].  It writes DIR/results.tsv (guest host exit pass fail limit
seconds logdir), each guest on the hosts in turn; a guest in FAKE_STIC_FAIL fails.  With
FAKE_DRIVE_BUSY=MARKER, the first call (while MARKER isn't there) refuses as drive.py does while
a check runs from the private copy.  Logs each call to MAC_ROOT/stub-calls.log."""
import os, sys, time
a = sys.argv[1:]
def opt(n):
    i = a.index(n)
    v = a[i + 1]
    del a[i:i + 2]
    return v
pc = "--private-copy" in a
if pc:
    a.remove("--private-copy")
hosts, out, mark = opt("--hosts").split(","), opt("--out"), opt("--logs-mark")
rnd, guests = a[0], a[1:] or ["alpine324"]
with open(os.path.join(os.environ["T2_MAC_ROOT"], "stub-calls.log"), "a") as f:
    f.write(f"{time.time():.3f} {os.getpid()} DRIVE {mark} {rnd} {','.join(hosts)} {' '.join(guests)} pc={pc}\n")
busy = os.environ.get("FAKE_DRIVE_BUSY")
if busy and not os.path.exists(busy):
    open(busy, "w").close()
    sys.exit("drive.py: --private-copy: a check runs from ~/backotter-work/st1-wt/testvm/stictest1 on cage")
time.sleep(float(os.environ.get("FAKE_DRIVE_S") or 0.3))
fail = (os.environ.get("FAKE_STIC_FAIL") or "").split()
os.makedirs(out, exist_ok=True)
rows = []
for i, g in enumerate(guests):
    h = hosts[i % len(hosts)]
    bad = g in fail
    rows.append(f"{g}\t{h}\t{1 if bad else 0}\t10\t{1 if bad else 0}\t0\t42\t/tmp/{mark}/{g}")
with open(os.path.join(out, "results.tsv"), "w") as f:
    f.write("guest\thost\texit\tpass\tfail\tlimit\tseconds\tlogdir\n" + "".join(r + "\n" for r in rows))
print(f"stub drive: {len(guests)} guests, {sum(g in fail for g in guests)} failed")
sys.exit(1 if any(g in fail for g in guests) else 0)
