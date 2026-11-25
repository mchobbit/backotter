"""The other kinds of batch entry, as children (docs/design/batch.md §3.1, §6.2; piece B6): run as
a script by t2lib/batch.py's spawn() (inside batchchild.py), never imported by the driver's loop.

    batchkinds.py ht1 EDIR --run RUN --queue-host Q [--resume] -- LINE...
    batchkinds.py stic1 EDIR -- DRIVE_ARGV...
    batchkinds.py bs2 EDIR --hosts H,H [--resume] -- WORD [ARG...]

Each starts its tool on the hosts the scheduler gave the entry (EDIR/placed: the claim and its
grow lines), waits for the tool's host work to end, writes the tool's own record into EDIR and
exits 0 (every cell passed), 1 (a cell did not), 2 (the tool refused to start) or 3 (it broke).
The work they start is host-autonomous (setsid on the hosts): a TERM or INT ends this watcher,
never the hosts' work, and `t2 batch resume` starts it again with --resume.

ht1    testvm/holttest1d/ht1-farm.sh RUN --hosts H,H --queue-host Q LINE... (the todo on Q, one
       runner a host), then every POLL: each host's runners of RUN (pgrep), Q's todo, the done
       rows.  A host a grow line adds gets `ht1-farm.sh RUN --join --queue-host Q --hosts H`; a
       resume joins every host of the claim (--join leaves a host whose runner still runs alone).
       Ends when no runner of RUN runs anywhere (two polls in a row).  EDIR/ht1-done.tsv: every
       host's done rows; EDIR/ht1-left.txt: the lines still in the todo.
stic1  testvm/stictest1/drive.py (its argv as given: --private-copy, --hosts, --out EDIR/stic1,
       --logs-mark); drive.py is the Mac-side driver and adopts the checks that ran when run again
       with the same mark.  Its --private-copy refuses while a check runs from the copy: a resume
       then waits and tries again.  The verdicts: EDIR/stic1/results.tsv.
bs2    testvm/backshot2/bs2 --hosts H,H WORD ARG... (it starts the queues detached and prints its
       run, RUN), then every POLL: each host's processes of RUN and its queue.  A resume restarts a
       host's queue (bs2-host queue RUN --slots N, from its queue.next, with the entry's WORD as
       BS2_WORD: its rows' word) when nothing of RUN runs there and the queue isn't through.  EDIR/run: RUN; EDIR/bs2-status.json: the verdicts.

Environment: T2_BATCH_HT1_FARM, T2_BATCH_DRIVE, T2_BATCH_BS2 (the tools, split like a shell
line; tests), T2_BATCH_KIND_POLL (s, default 30), T2_BATCH_PATIENCE_S (a host silent this long
counts as gone; default 7200), T2_SSH (hostrun's), T2_BATCH_HT1_ROOT and T2_BATCH_BS2_ROOT (the
hosts' /bkoscratch/holttest1d and /bkoscratch/backshot2; tests), T2_BATCH_BS2_HOST (bs2-host there).  On a test MAC_ROOT the real tools
are never run (they reach the farm): without its stub an entry is refused (exit 2).
"""

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
T2_DIR = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2_DIR)
if T2_DIR not in sys.path:
    sys.path.insert(0, T2_DIR)

from t2lib import hostrun          # noqa: E402
from t2lib import hosts as H       # noqa: E402

HT1_ROOT = os.environ.get("T2_BATCH_HT1_ROOT") or "/bkoscratch/holttest1d"   # (the env: tests' fake hosts)
HT1_BASEPORT = 2600
BS2_ROOT = os.environ.get("T2_BATCH_BS2_ROOT") or "/bkoscratch/backshot2"
BS2_HOST = os.environ.get("T2_BATCH_BS2_HOST") or "~/backotter-work/bs2-wt/testvm/backshot2/bs2-host"
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
QUIET_POLLS = 2


def say(*a):
    print("batch:", *a, flush=True)


def env_f(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


def write_json(path, obj):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def write_text(path, text):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def placed_hosts(edir):
    """The entry's hosts: its claim's (placed's first line), then each grow line's, in order."""
    try:
        with open(os.path.join(edir, "placed"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for i, ln in enumerate(lines):
        try:
            if i == 0:
                hs = (json.loads(ln) or {}).get("hosts") or []
            elif ln.startswith("grow "):
                hs = [(json.loads(ln[5:]) or {}).get("host")]
            else:
                continue
        except ValueError:
            continue
        out += [h for h in hs if h and h not in out]
    return out


class NoRealTool(Exception):
    pass


def real_root():
    from t2lib import DEFAULT_MAC_ROOT, expand, mac_root
    return os.path.realpath(mac_root()) == os.path.realpath(expand(DEFAULT_MAC_ROOT))


def tool(name, default):
    """The tool NAME names (a stub, tests), else DEFAULT: the real one, which reaches the farm,
    only ever on the real MAC_ROOT.  A test root without the stub raises NoRealTool."""
    o = os.environ.get(name)
    if o:
        return shlex.split(o)
    if not real_root():
        raise NoRealTool(f"{name} is not set and MAC_ROOT is a test root: the real tool reaches the farm, "
                         "and a test never does")
    return list(default)


def run_tool(argv, keep=60):
    """ARGV with stdin /dev/null, its output passed through to ours: (rc, its last KEEP lines)."""
    say("runs: " + " ".join(shlex.quote(a) for a in argv))
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             cwd=os.path.dirname(TESTVM))
    except OSError as e:
        say(f"can't start {argv[0]}: {e}")
        return 127, [str(e)]
    tail = []
    for b in iter(p.stdout.readline, b""):
        s = b.decode("utf-8", "replace")
        sys.stdout.write(s)
        sys.stdout.flush()
        tail = (tail + [s.rstrip("\n")])[-keep:]
    return p.wait(), tail


class Silence:
    """Per host: since when it hasn't answered.  A host silent past PATIENCE counts as gone.
    Measured on the monotonic clock, which stands still while the Mac sleeps (macOS's
    mach_absolute_time, Linux's CLOCK_MONOTONIC): a host that fails one poll before the lid
    closes and one after it wakes (Wi-Fi not back yet) has been silent minutes, not the night."""

    def __init__(self, patience):
        self.patience, self.since = patience, {}

    def answered(self, h):
        self.since.pop(h, None)

    def silent(self, h, now=None):
        now = time.monotonic() if now is None else now
        self.since.setdefault(h, now)
        return now - self.since[h] >= self.patience


def host_script(host, script, args, timeout=60):
    """SCRIPT (Python) with ARGS on HOST over hostrun.ssh (T2_SSH), its table destination."""
    try:
        d = H.ssh(host)
    except Exception:   # noqa: BLE001 - the bare name then
        d = host
    return hostrun.ssh(d, script=script, args=args, timeout=timeout)


def host_cmd(host, cmd, timeout=60):
    try:
        d = H.ssh(host)
    except Exception:   # noqa: BLE001
        d = host
    return hostrun.ssh(d, cmd=cmd, timeout=timeout)


# --- ht1 ------------------------------------------------------------------------------------------

HT1_STATUS = r'''
import os, re, subprocess, sys
root, run, port, qhost = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
q = os.path.join(root, "queue", run)
pat = "[h]t1-queue\\.sh " + run.replace(".", "\\.") + " " + port
try:
    alive = len([x for x in subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True).stdout.split() if x])
except OSError:
    alive = 0
print("@alive %d" % alive)
if qhost:
    try:
        with open(os.path.join(q, "todo")) as f:
            lines = [l.strip() for l in f if l.strip()]
        print("@todo %d" % len(lines))
        for l in lines:
            print("L\t" + l)
    except OSError:
        print("@todo -")
try:
    with open(os.path.join(q, "done")) as f:
        for l in f:
            if l.strip():
                print("D\t" + l.rstrip("\n"))
except OSError:
    pass
print("@end")
'''


def ht1_poll(host, run, qhost):
    """{alive, todo (None: unknown), left [lines], done [rows]} of HOST, or None: no answer."""
    r = host_script(host, HT1_STATUS, [HT1_ROOT, run, HT1_BASEPORT, "1" if qhost else "0"])
    if r.rc != 0 or "@end" not in r.text:
        return None
    out = {"alive": 0, "todo": None, "left": [], "done": []}
    for ln in r.text.splitlines():
        if ln.startswith("@alive "):
            out["alive"] = int(ln.split()[1])
        elif ln.startswith("@todo "):
            v = ln.split()[1]
            out["todo"] = int(v) if v.isdigit() else None
        elif ln.startswith("L\t"):
            out["left"].append(ln[2:])
        elif ln.startswith("D\t"):
            out["done"].append(ln[2:])
    return out


def ht1(edir, run, qhost, lines, resume=False):
    if not NAME_RE.match(run):
        say(f"bad RUN {run!r}")
        return 2
    farm = tool("T2_BATCH_HT1_FARM", ["bash", os.path.join(TESTVM, "holttest1d", "ht1-farm.sh")])
    kj = os.path.join(edir, "kind.json")
    st = read_json(kj, {}) or {}
    st.update(run=run, queue_host=qhost)
    joined = set(st.get("joined") or [])
    hosts = placed_hosts(edir) or [qhost]
    if resume:
        rc, tail = run_tool(farm + [run, "--join", "--queue-host", qhost, "--hosts", ",".join(hosts)])
        if rc == 2:
            say(f"ht1-farm.sh --join refused (exit 2): {tail[-1] if tail else ''}")
            return 2 if not st.get("started") else 3
        joined |= set(hosts)
    elif not st.get("started"):
        rc, tail = run_tool(farm + [run, "--hosts", ",".join(hosts), "--queue-host", qhost] + list(lines))
        if rc == 2:
            say(f"ht1-farm.sh refused (exit 2): {tail[-1] if tail else ''}")
            return 2
        if rc not in (0, 1):
            return 3
        joined |= set(hosts)
    st.update(started=True, joined=sorted(joined))
    write_json(kj, st)
    poll = env_f("T2_BATCH_KIND_POLL", 30)
    quiet, sil = 0, Silence(env_f("T2_BATCH_PATIENCE_S", 7200))
    last = {}
    while True:
        hosts = placed_hosts(edir) or [qhost]
        new = [h for h in hosts if h not in joined]
        if new:
            rc, tail = run_tool(farm + [run, "--join", "--queue-host", qhost, "--hosts", ",".join(new)])
            say(f"grew onto {','.join(new)}: ht1-farm.sh --join exit {rc}")
            joined |= set(new)
            st["joined"] = sorted(joined)
            write_json(kj, st)
        alive, gone, todo = 0, [], None
        for h in sorted(set(hosts) | {qhost}):
            got = ht1_poll(h, run, h == qhost)
            if got is None:
                if sil.silent(h):
                    gone.append(h)
                else:
                    alive += 1          # no answer is not "ended" (audit-lib.sh's rule)
                continue
            sil.answered(h)
            last[h] = got
            alive += got["alive"]
            if h == qhost:
                todo = got["todo"]
        if alive == 0:
            quiet += 1
        else:
            quiet = 0
        if quiet >= QUIET_POLLS:
            break
        time.sleep(poll)
    rows, header = [], None
    for h in sorted(last):
        for ln in last[h]["done"]:
            if ln.startswith("end_utc\t"):
                header = ln
            elif ln not in rows:        # (each row names its host: a repeat is the same row)
                rows.append(ln)
    write_text(os.path.join(edir, "ht1-done.tsv"), "\n".join([header or "end_utc\thost\tgv\trc\td_gib\tm_gib\tport\t"
                                                                         "wait_s\trun_s"] + rows) + "\n")
    left = (last.get(qhost) or {}).get("left") or []
    write_text(os.path.join(edir, "ht1-left.txt"), "".join(x + "\n" for x in left))
    rc_of = {}
    for ln in rows:
        f = ln.split("\t")
        if len(f) >= 4:
            rc_of[f[2]] = f[3]
    want = [re.split(r"[=%]", x)[0].replace("+", "-") for x in lines] if lines else list(rc_of)
    bad = [g for g in want if rc_of.get(g) != "0"]
    st.update(ended=True, todo_left=len(left), gone=gone, done=len(rows))
    write_json(kj, st)
    say(f"ht1 {run}: {len(rows)} done rows, {len(left)} left in the todo, {len(bad)} not passed"
        + (f"; no answer from {', '.join(gone)} past the patience" if gone else ""))
    return 0 if not bad and not left else 1


# --- stic1 -----------------------------------------------------------------------------------------

def stic1(edir, argv):
    if not os.environ.get("T2_BATCH_DRIVE"):
        tool("T2_BATCH_DRIVE", [])          # (refuses on a test root: drive.py reaches the farm)
    poll = env_f("T2_BATCH_KIND_POLL", 30)
    waited = False
    while True:
        rc, tail = run_tool(argv)
        text = "\n".join(tail)
        if rc == 130:
            return 130
        if rc != 0 and "--private-copy" in text and "a check runs from" in text:
            if not waited:
                say("drive.py's --private-copy waits: a check of the round still runs from the copy; "
                    f"trying again every {poll:.0f} s")
                waited = True
            time.sleep(poll)
            continue
        break
    res = os.path.join(edir, "stic1", "results.tsv")
    if not os.path.exists(res):
        if "uncommitted changes" in text or "refuse" in text:
            return 2
        return 3 if rc != 0 else 1
    bad = 0
    with open(res, encoding="utf-8") as f:
        for ln in f.read().splitlines()[1:]:
            x = ln.split("\t")
            if len(x) >= 5 and not (x[2] == "0" and x[4] == "0"):
                bad += 1
    return 0 if rc == 0 and bad == 0 else 1


# --- bs2 -------------------------------------------------------------------------------------------

BS2_STATUS = r'''
import glob, json, os, re, subprocess, sys
root, run = sys.argv[1], sys.argv[2]
rd = os.path.join(root, "runs", run)
pat = "[b]s2-host.*" + run.replace(".", "\\.")
try:
    alive = len([x for x in subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True).stdout.split() if x])
except OSError:
    alive = 0
def rd_(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return ""
nxt = rd_(os.path.join(rd, "queue.next")).strip()
lines = len([l for l in rd_(os.path.join(rd, "queue.txt")).splitlines() if l.strip()])
v = {}
for p in glob.glob(os.path.join(root, "runs", run + "*", "tier*.jsonl")):
    for l in rd_(p).splitlines():
        try:
            j = json.loads(l)
        except ValueError:
            continue
        v[j.get("verdict")] = v.get(j.get("verdict"), 0) + 1
print(json.dumps({"alive": alive, "next": int(nxt) if nxt.isdigit() else 0, "lines": lines, "verdicts": v}))
'''


def bs2_poll(host, run):
    r = host_script(host, BS2_STATUS, [BS2_ROOT, run])
    if r.rc != 0:
        return None
    try:
        return json.loads(r.text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


RUN_LINE = re.compile(r"\brun (\S+): bs2 status\b")
# bs2's words (testvm/backshot2/bs2lib/ledger.py WORDS): a resume restarts a queue with the entry's
# as BS2_WORD, as bs2 started it, so its cases' ledger rows are still written (bs2 writes none
# without a word).
BS2_WORDS = ("BackShot2Alpine", "BackShot2", "BackShot2All")


def bs2(edir, hosts, args, resume=False):
    kj = os.path.join(edir, "kind.json")
    st = read_json(kj, {}) or {}
    bs2_ = tool("T2_BATCH_BS2", [sys.executable, "-B", os.path.join(TESTVM, "backshot2", "bs2")])
    run = st.get("run")
    if not run:
        if resume:
            say("resume: no bs2 run was recorded, so none started: the word again")
        rc, tail = run_tool(bs2_ + ["--hosts", ",".join(hosts)] + list(args))
        run = next((m.group(1) for m in (RUN_LINE.search(x) for x in reversed(tail)) if m), None)
        if not run:
            return 2 if any("not started" in x or "no host" in x for x in tail) or rc == 2 else 3
        st.update(run=run, hosts=list(hosts))
        write_json(kj, st)
        write_text(os.path.join(edir, "run"), run + "\n")
        say(f"bs2 run {run} on {','.join(hosts)} (bs2 exit {rc})")
        resume = False
    poll = env_f("T2_BATCH_KIND_POLL", 30)
    quiet, sil, last, first = 0, Silence(env_f("T2_BATCH_PATIENCE_S", 7200)), {}, True
    while True:
        alive = 0
        for h in hosts:
            got = bs2_poll(h, run)
            if got is None:
                alive += 0 if sil.silent(h) else 1
                continue
            sil.answered(h)
            last[h] = got
            if resume and first and got["alive"] == 0 and got["lines"] and got["next"] < got["lines"] \
                    and (real_root() or os.environ.get("T2_BATCH_BS2_HOST")):
                n = H.limit(h, "bs2_slots") or 2
                log = f"{BS2_ROOT}/logs/{run}-resume-{h}.log"
                word = next((x for x in args if x in BS2_WORDS), None)
                wenv = f"BS2_WORD={word} " if word else ""
                r = host_cmd(h, f"mkdir -p {BS2_ROOT}/logs && cd ~/backotter-work && setsid -f bash -c {shlex.quote(f'{wenv}{BS2_HOST} queue {run} --slots {n}')}"
                                f" > {log} 2>&1 < /dev/null")
                say(f"{h}: its queue had stopped at {got['next']} of {got['lines']}: restarted (exit {r.rc}, log {log})")
                alive += 1
                continue
            alive += got["alive"]
        first = False
        quiet = quiet + 1 if alive == 0 else 0
        if quiet >= QUIET_POLLS:
            break
        time.sleep(poll)
    v = {}
    for got in last.values():
        for k, n in (got.get("verdicts") or {}).items():
            v[k] = v.get(k, 0) + n
    st.update(ended=True, verdicts=v)
    write_json(kj, st)
    write_json(os.path.join(edir, "bs2-status.json"), {"run": run, "verdicts": v, "hosts": last})
    say(f"bs2 {run}: " + (", ".join(f"{k} {n}" for k, n in sorted(v.items())) or "no verdicts"))
    return 0 if v and set(v) == {"PASS"} else 1


# --- main -------------------------------------------------------------------------------------------

def main(argv):
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, lambda n, f: sys.exit(128 + n))     # (the hosts' work goes on)
    if len(argv) < 3 or "--" not in argv:
        sys.stderr.write(__doc__)
        return 2
    i = argv.index("--")
    head, rest = argv[:i], argv[i + 1:]
    kind, edir, opts = head[0], head[1], head[2:]

    def opt(name):
        return opts[opts.index(name) + 1] if name in opts and opts.index(name) + 1 < len(opts) else None

    try:
        if kind == "ht1":
            return ht1(edir, opt("--run"), opt("--queue-host"), rest, "--resume" in opts)
        if kind == "stic1":
            return stic1(edir, rest)
        if kind == "bs2":
            return bs2(edir, [h for h in (opt("--hosts") or "").split(",") if h], rest, "--resume" in opts)
    except KeyboardInterrupt:
        say("interrupted: the hosts' work goes on; `t2 batch resume` picks it up")
        return 130
    except NoRealTool as e:
        say(f"refused: {e}")
        return 2
    sys.stderr.write(f"batchkinds.py: no kind {kind}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
