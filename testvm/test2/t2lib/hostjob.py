"""One job on a test host, for the runners on the Mac (gate.py's, lifted out; contract 3).

    host_job(env, host, name, attempt, cmd, reserve, wear_tag, timeout_s, wait_s, extra_env,
             ram, subdir, on_began=None)
                        CMD on HOST, detached, under cmp-run.sh --reserve; waits for its end
    job_times(t)        (wall_s, waits_s) of host_job's times
    reattach_job(env, host, name, attempt, timeout_s, wait_s, subdir, on_began=None)
                        a job an earlier process started: host_job's result, without starting it
    JobFailure          host_job's why
    HostIO              a runner's operations on hosts and on the Mac
    Env(rs, host_root, io=None, log=None)
                        what host_job needs, for a runner with no RunEnv of its own
    host_checks(env, rs, tag_prefix, t0)
                        per host: a look (host_state: did it answer, its boot id and uptime)
                        into rs.stops.feed_seen (the host rule, run.HostWatch), swap-out since
                        the first check, and the bytes this run's wear tags wrote (and kept,
                        keep=), into rs.stops.feed_host (and so RunGuards)
    host_seen(io, host), host_state(io, host)
                        a look at HOST: None (no answer) or {boot, up_s}; with its pswpout
    host_lost(stops, host, start, end, seen=None, text=None)
                        why a harness-class result on HOST is the host's going away, or None
    wait_back(stops, host, seen, sleep, max_s, log=None, cancelled=None)
                        until HOST is back: the host rule doesn't hold it, and it has
                        answered and been up host_settle_min
    guards_of(rs)       the composite's RunGuards, or None

host_job's env is anything with .host_root, .attempt_run(a), .copy_path(host), .io, .rs (a
RunState or None) and .log(text).  The job's records are
HOST_ROOT/runs/{attempt_run}/{subdir}/{layer_dir(name)}.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time

CMP_RUN = "~/backotter-work/cmp-run.sh"
POLL_S = 15


def layer_dir(name):
    """loop:linux@chopin -> loop-linux-chopin: a layer's directory and lock name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name)


# --- host I/O (t2lib.hostrun underneath; the unit tests give a fake) ------------------------

FETCH_SCRIPT = r'''
import json, os, sys
d = json.loads(_T2_DATA)
out, total = {}, 0
base = os.path.expanduser(d["dir"])   # a HOST_ROOT under the Mac's home reaches the host as ~/...
for n in d["names"]:
    try:
        with open(os.path.join(base, n), "rb") as f:
            b = f.read(d.get("max", 4 << 20))
    except OSError:
        continue
    total += len(b)
    if total > d.get("total", 16 << 20):
        break
    out[n] = b.decode("utf-8", "replace")
sys.stdout.write(json.dumps(out))
'''


class HostIO:
    """The gate's operations on hosts and on the Mac."""

    def __init__(self):
        from . import hostrun
        self.h = hostrun
        self.unknown_max = None   # T2_UNKNOWN_MAX's patience for this IO (unknown_max()); None: the env's

    def sh(self, host, cmd, timeout=60, input=None):
        r = self.h.ssh(host, cmd, timeout=timeout, input=input)
        return r.rc, r.text, r.errtext

    def script(self, host, script, data, timeout=120):
        r = self.h.ssh(host, script=script, data=data, timeout=timeout)
        if r.rc != 0:
            raise RuntimeError(f"{host}: script exit {r.rc}: {r.errtext.strip()[-300:]}")
        return json.loads(r.text or "{}")

    def start(self, host, cmd, log, pidfile):
        return self.h.detached(host, cmd, log, pidfile)

    def alive(self, host, pid, needle=None):
        """True while PID runs on HOST with NEEDLE in its command line, False when the host
        says it doesn't, None when the host didn't answer (an ssh that failed is no answer)."""
        pid = int(pid)
        test = (f"test -r /proc/{pid}/cmdline && tr '\\0' ' ' < /proc/{pid}/cmdline | grep -qF -- {shlex.quote(needle)}"
                if needle else f"test -d /proc/{pid}")
        rc, text, _ = self.sh(host, f"if {test}; then echo alive; else echo gone; fi", timeout=30)
        ans = (text or "").strip()
        return True if rc == 0 and ans == "alive" else False if rc == 0 and ans == "gone" else None

    def stop(self, host, pid, needle, wait_s=300):
        """TERM the process group of a job this run started (PID leads it: setsid), only
        while PID is still that job (NEEDLE in its command line); then wait up to WAIT_S for
        the group to be gone.  True once nothing of it runs, False when it may still run.
        Never a KILL: the scripts' TERM traps clean up their pools, mounts and RAM."""
        pid = int(pid)
        if pid <= 1:
            return False
        state = self.alive(host, pid, needle)
        if state is None:
            return False
        if state:
            rc, text, _ = self.sh(host, f"[ \"$(ps -o pgid= -p {pid} | tr -d ' ')\" = {pid} ] && kill -TERM -- -{pid}"
                                        f" && echo sent", timeout=30)
            if rc != 0 or "sent" not in (text or ""):
                return False
        else:   # the leader is gone; what is left of its group is still ours (Linux reuses no
            # PID while a process group of that number lives)
            self.sh(host, f"kill -TERM -- -{pid} 2>/dev/null", timeout=30)
        t0 = time.time()
        while True:   # the group: kill -0 says "No such process" once none of it is left
            rc, text, err = self.sh(host, f"kill -0 -- -{pid} 2>&1; echo rc=$?", timeout=30)
            if rc == 0 and "rc=1" in text and "No such process" in text:
                return True
            if time.time() - t0 > wait_s:
                return False
            self.sleep(POLL_S)

    def fetch(self, host, d, names):
        """{name: text} of the files of D on HOST that are there."""
        try:
            return self.script(host, FETCH_SCRIPT, {"dir": d, "names": list(names)})
        except (RuntimeError, ValueError):
            return {}

    def local(self, argv, cwd=None, env=None, timeout=None):
        """(rc, output) of ARGV on the Mac, in a process group of its own: past TIMEOUT the
        whole group gets a TERM (fork-tests.sh's background waiters too), rc 124."""
        import signal as _sig
        try:
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True)
        except OSError as e:
            return 127, str(e)
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, _sig.SIGTERM)
            except OSError:
                pass
            try:
                out, err = p.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, _sig.SIGKILL)
                except OSError:
                    pass
                out, err = p.communicate()
            return 124, f"timed out after {timeout} s\n" + (out or b"").decode("utf-8", "replace")[-2000:]
        return p.returncode, out.decode("utf-8", "replace") + err.decode("utf-8", "replace")

    def facts(self, hosts, want):
        """{host: facts} of what WANT names (facts.probe_all, nothing saved)."""
        from . import facts as factsmod
        return factsmod.probe_all(list(hosts), want, save=False)

    def sleep(self, s):
        time.sleep(s)


def _q(p):
    """P for a remote shell; a leading ~/ stays for it to expand."""
    if p == "~":
        return '"$HOME"'
    if p.startswith("~/"):
        return '"$HOME"/' + shlex.quote(p[2:])
    return shlex.quote(p)


# --- one job on a host ----------------------------------------------------------------------

class JobFailure(str):
    """host_job's why: the text, whether the job may still run on the host (then the layer
    isn't run again beside it), and (reattach_job) whether it's proven gone, with no exit status."""

    def __new__(cls, text, still_running=False, gone=False):
        o = str.__new__(cls, text)
        o.still_running, o.gone = still_running, gone
        o.stopped = False   # host_job gave up on it and its TERM ended it
        return o


STOP_WHY = ".stop-why"   # in a job's records: why host_job stopped it (guest rows say so)


UNKNOWN_MAX = 20   # polls in a row with no answer from the host before host_job gives up


def unknown_max(io=None):
    """The polls in a row with no answer before a job is given up on: IO's unknown_max, else
    T2_UNKNOWN_MAX (t2 batch sets it from its patience, docs/design/batch.md §6.4), else
    UNKNOWN_MAX; never fewer than UNKNOWN_MAX, so a session's default doesn't change."""
    v = getattr(io, "unknown_max", None)
    if v is None:
        v = os.environ.get("T2_UNKNOWN_MAX")
    try:
        return max(UNKNOWN_MAX, int(v)) if v not in (None, "") else UNKNOWN_MAX
    except (TypeError, ValueError):
        return UNKNOWN_MAX


def job_poll_cmd(out):
    """host_job's poll: .exit, a blank line, .began.  Exit 0 whether or not they are there yet
    (exit 1 only when the job's directory is gone): a job waiting in cmp-run.sh's line has
    neither, and that isn't 'no answer'."""
    return f"cd {_q(out)} || exit 1; cat .exit 2>/dev/null; echo; cat .began 2>/dev/null; exit 0"


def job_dir(env, name, attempt, subdir="gate"):
    """HOST_ROOT/runs/{attempt_run}/{subdir}/{layer_dir(name)}: a job's records."""
    return f"{env.host_root.rstrip('/')}/runs/{env.attempt_run(attempt)}/{subdir}/{layer_dir(name)}"


def host_job(env, host, name, attempt, script_cmd, reserve=None, wear_tag=None, timeout_s=3600,
             wait_s=4 * 3600, extra_env=None, ram=None, subdir="gate", on_began=None):
    """SCRIPT_CMD (a shell command, run in the copy's root) on HOST, detached, under
    ~/backotter-work/cmp-run.sh --reserve when RESERVE is (DISK, MEM), with WEAR_TAG for
    that command alone; waits for its end.  (rc or None, out_dir, times, why): why (a
    JobFailure) says what went wrong when rc is None, never empty.  TIMEOUT_S counts from
    the job's start, after the wait for room (at most WAIT_S); a job past either is stopped
    (its process group gets a TERM), and when it can't be seen gone, why.still_running.
    ON_BEGAN() is called once, when the job has left cmp-run.sh's line (or has ended).
    ENV.on_submitted(HOST, NAME), when ENV has one, is called once the job's start was tried
    (it is in cmp-run.sh's line, or it couldn't start): SticTest2 starts drive.py after its slow
    gate's layers are in the line (stic.py, gate.run_layers)."""
    out = job_dir(env, name, attempt, subdir)
    copy = env.copy_path(host)
    # In the job's command line as written ("$HOME"/'...' for a ~/ root): never a reused PID.
    needle = f"/runs/{env.attempt_run(attempt)}/{subdir}/{layer_dir(name)}/"
    ev = dict({"PYTHONDONTWRITEBYTECODE": "1"}, **(extra_env or {}))
    exports = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in sorted(ev.items()))
    # cmp-run.sh runs its command in ~/backotter-work (the synced tree's root): back to the copy.
    inner = f"cd {_q(copy)} || exit 2; date +%s > {_q(out + '/.began')}; exec {script_cmd}"
    if reserve:
        wt = f"WEAR_TAG={shlex.quote(wear_tag)} " if wear_tag else ""
        run = f"{wt}{CMP_RUN} --reserve {reserve[0]:g} {reserve[1]:g} bash -c {shlex.quote(inner)}"
    else:
        run = f"bash -c {shlex.quote(inner)}"
    body = (f"cd {_q(copy)} || {{ echo 'no copy here' >&2; echo 2 > {_q(out + '/.exit')}; exit 2; }}; "
            f"export {exports}; date +%s > {_q(out + '/.submitted')}; "
            f"{run}; rc=$?; date +%s > {_q(out + '/.ended')}; echo $rc > {_q(out + '/.exit')}")
    def submitted():
        fn = getattr(env, "on_submitted", None)
        if fn is not None:
            try:
                fn(host, name)
            except Exception:   # noqa: BLE001 - only the caller's ordering
                pass

    rc, _, err = env.io.sh(host, f"mkdir -p {_q(out)} && cd {_q(out)} && rm -f .exit .began .submitted .ended .pid {STOP_WHY}")
    if rc != 0:
        submitted()
        return None, out, {}, JobFailure(f"can't make {out} on {host}: {err.strip()[-150:]}")
    pid = env.io.start(host, f"bash -c {shlex.quote(body)}", out + "/job.log", out + "/.pid")
    submitted()
    if not pid:
        return None, out, {}, JobFailure(f"the job didn't start on {host}")
    if ram and env.rs is not None and hasattr(env.rs, "record_made"):
        try:   # for preflight's leftovers of a run that died (the job removes it itself)
            env.rs.record_made(host, ram, pid)
        except Exception:   # noqa: BLE001
            pass
    env.log(f"{host}: {subdir}:{name} attempt {attempt} started, pid {pid}, records {out}")

    def began_now():
        nonlocal told
        if on_began is not None and not told:
            told = True
            try:
                on_began()
            except Exception:   # noqa: BLE001 - only the pool's bookkeeping
                pass

    told = False

    def give_up(why):
        began_now()
        gone = False
        # Why it is stopped, in its records first: the job's row then says the runner stopped it
        # and why (guest/job.sh's TERM alone reads as "stopped from outside", rows_guest).
        try:
            env.io.sh(host, f"printf '%s\\n' {shlex.quote(str(why))} > {_q(out + '/' + STOP_WHY)}", timeout=20)
        except Exception:   # noqa: BLE001 - only the words of its row
            pass
        if hasattr(env.io, "stop"):
            try:
                gone = env.io.stop(host, pid, needle)
            except Exception:   # noqa: BLE001
                gone = False
        env.log(f"{host}: {subdir}:{name} attempt {attempt}: {why}; "
                + ("stopped" if gone else f"pid {pid} may still run: no rerun beside it"))
        o = JobFailure(f"{why}; " + ("stopped" if gone else f"pid {pid} may still run"), still_running=not gone)
        o.stopped = bool(gone)   # it ended under the runner's TERM: what it wrote as it ended (a guest's row) is there
        return None, out, {}, o

    t0, began, said, unknown, ex = time.time(), None, False, 0, None
    while True:
        rc, text, _ = env.io.sh(host, job_poll_cmd(out))
        e1, _, b = (text or "").partition("\n")
        if rc == 0 and e1.strip().lstrip("-").isdigit():
            ex = int(e1.strip())
            break
        if rc == 0 and b.strip().isdigit() and began is None:
            began = time.time()
            began_now()
        state = env.io.alive(host, pid, needle) if rc == 0 else None
        if state is False:
            rc2, text2, _ = env.io.sh(host, f"cat {_q(out + '/.exit')} 2>/dev/null")
            if rc2 == 0 and text2.strip().lstrip("-").isdigit():
                ex = int(text2.strip())
                break
            if rc2 == 0:
                return give_up(f"the job on {host} ended without an exit status (pid {pid}; {out}/job.log)")
            state = None
        unknown = unknown + 1 if state is None else 0
        now = time.time()
        if unknown >= unknown_max(env.io):   # (T2_UNKNOWN_MAX: a batch's patience)
            return give_up(f"no answer from {host} in {unknown} polls")
        if began is None and now - t0 > wait_s:
            return give_up(f"no room for it on {host} after {wait_s} s")
        if began is not None and now - began > timeout_s:
            return give_up(f"no end after {timeout_s} s ({out}/job.log)")
        if began is None and not said and now - t0 > 120:
            env.log(f"{host}: {subdir}:{name} waits for room in cmp-run.sh's ledger")
            said = True
        env.io.sleep(POLL_S)
    began_now()
    f = env.io.fetch(host, out, [".submitted", ".began", ".ended", ".exit"])
    t = {k[1:]: int(v.strip()) for k, v in f.items() if v.strip().lstrip("-").isdigit()}
    t["exit"] = ex   # the polled exit: a fetch that failed afterwards loses only the times
    return ex, out, t, None


REATTACH_POLL = ("cd {d} 2>/dev/null || {{ echo nodir; exit 0; }}; "
                 "for f in .exit .began .pid; do printf '%s=' \"$f\"; head -n 1 \"$f\" 2>/dev/null; echo; done; exit 0")


def reattach_job(env, host, name, attempt, timeout_s=3600, wait_s=4 * 3600, subdir="gate", on_began=None):
    """A job an earlier process of this runner started on HOST: wait for its end.  host_job's
    result.  why.gone: it's proven gone with no exit status (or it never got as far as its
    directory); why.still_running: it may still run.  A host that doesn't answer isn't a job
    that ended.  Past TIMEOUT_S + WAIT_S from here the job is stopped, as host_job's would be."""
    out = job_dir(env, name, attempt, subdir)
    needle = f"/runs/{env.attempt_run(attempt)}/{subdir}/{layer_dir(name)}/"
    told = False

    def began_now():
        nonlocal told
        if on_began is not None and not told:
            told = True
            try:
                on_began()
            except Exception:   # noqa: BLE001 - only the pool's bookkeeping
                pass

    def poll():
        rc, text, _ = env.io.sh(host, REATTACH_POLL.format(d=_q(out)))
        if rc != 0:
            return None
        if (text or "").strip() == "nodir":
            return {}
        v = {}
        for line in (text or "").splitlines():
            k, eq, x = line.partition("=")
            if eq:
                v[k] = x.strip()
        return v

    env.log(f"{host}: {subdir}:{name} attempt {attempt}: adopting the job an earlier process started; records {out}")
    t0, unknown, pid = time.time(), 0, ""
    while True:
        v = poll()
        state = None
        if v is not None:
            if not v:   # no directory: it never started
                began_now()
                return None, out, {}, JobFailure(f"attempt {attempt}'s job on {host} never started (resume)", gone=True)
            if v.get(".exit", "").lstrip("-").isdigit():
                ex = int(v[".exit"])
                break
            if v.get(".began", "").isdigit():
                began_now()
            pid = v.get(".pid", "")
            state = env.io.alive(host, int(pid), needle) if pid.isdigit() else False
            if state is False:   # (it may have ended since the look at .exit)
                v2 = poll()
                if v2 and v2.get(".exit", "").lstrip("-").isdigit():
                    ex = int(v2[".exit"])
                    break
                if v2 is not None:
                    began_now()
                    return None, out, {}, JobFailure(f"attempt {attempt}'s job on {host} ended with no exit status (resume)", gone=True)
                state = None
        unknown = unknown + 1 if state is None else 0
        if unknown >= unknown_max(env.io):   # (T2_UNKNOWN_MAX, on a resume too)
            began_now()
            return None, out, {}, JobFailure(f"no answer from {host} in {unknown} polls (resume)", still_running=True)
        if time.time() - t0 > timeout_s + wait_s:
            began_now()
            stopped = False
            try:   # as host_job's give_up: the job's row says the runner stopped it, and why
                env.io.sh(host, f"printf '%s\\n' {shlex.quote(f'no end after {timeout_s + wait_s} s (resume)')} "
                                f"> {_q(out + '/' + STOP_WHY)}", timeout=20)
            except Exception:   # noqa: BLE001
                pass
            if pid.isdigit() and hasattr(env.io, "stop"):
                try:
                    stopped = env.io.stop(host, int(pid), needle)
                except Exception:   # noqa: BLE001
                    stopped = False
            o = JobFailure(f"attempt {attempt}'s job on {host} has no end after {timeout_s + wait_s} s (resume)",
                           still_running=not stopped)
            o.stopped = bool(stopped)
            return None, out, {}, o
        env.io.sleep(POLL_S)
    began_now()
    f = env.io.fetch(host, out, [".submitted", ".began", ".ended", ".exit"])
    t = {k[1:]: int(x.strip()) for k, x in f.items() if x.strip().lstrip("-").isdigit()}
    t["exit"] = ex
    return ex, out, t, None


def job_times(t):
    """(wall_s, waits_s) of host_job's times: the waits are the time in cmp-run.sh's line."""
    sub, began, ended = t.get("submitted"), t.get("began"), t.get("ended")
    if began is None:
        return (max(ended - sub, 0) if ended and sub else 0), 0
    return max((ended or began) - began, 0), max(began - (sub or began), 0)


# --- a runner with no environment of its own ------------------------------------------------

class Env:
    """host_job's env for a runner on run.py's RunState: Env(rs, host_root, io=None, log=None)."""

    def __init__(self, rs, host_root, io=None, log=None):
        self.rs, self.host_root = rs, host_root
        self.io = io or HostIO()
        self._log = log or getattr(rs, "log", None) or _stderr
        self.hosts = list(getattr(rs, "hosts", None) or [])
        self.swap0 = {}

    def log(self, text):
        self._log(text)

    def attempt_run(self, attempt):
        return self.rs.run if attempt == 1 else f"{self.rs.run}-r{attempt}"

    def copy_path(self, host):
        c = (getattr(self.rs, "copy", None) or {}).get(host)
        p = c.get("path") if isinstance(c, dict) else getattr(c, "path", c)
        if not p:
            raise RuntimeError(f"no private copy on {host} (run.py's lifecycle makes it before the runner)")
        return p


def _stderr(msg):
    sys.stderr.write(time.strftime("%H:%M:%S ") + msg + "\n")
    sys.stderr.flush()


# --- the host-wide stop rules ------------------------------------------------------------------

TAG_SCRIPT = r'''
import calendar, re, sys, time
prefix, since = sys.argv[1], float(sys.argv[2])
virt = re.compile(r"((zram|loop|nbd|md|ram|sr|zd)\d+|dm-\d+)\Z")
retry = re.compile(re.escape(prefix) + r"-r\d+:")
total = kept = 0
try:
    with open("/bkoscratch/wear/jobs.log", errors="replace") as f:
        lines = f.read().splitlines()
except OSError:
    lines = []
# TIME HOST SCOPE RC SECS DISK=BYTES[,DISK=BYTES] WHAT tag=TAG serial=... keep=DISK=BYTES,... (tab-separated)
for l in lines:
    f = l.split("\t")
    tag = next((x[4:] for x in f if x.startswith("tag=")), "")
    if len(f) < 6 or not (tag == prefix or tag.startswith(prefix + ":") or retry.match(tag)):
        continue
    try:
        t = calendar.timegm(time.strptime(f[0][:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        t = since
    if t < since:
        continue
    disks = {}
    for part in f[5].split(","):
        k, _, v = part.partition("=")
        if v.isdigit() and k and not virt.match(k):
            disks[k] = disks.get(k, 0) + int(v)
    total += sum(disks.values())
    # keep=: the part a failed run's state wrote as it was kept (iolog.sh --keep-since)
    for part in next((x[5:] for x in f[6:] if x.startswith("keep=")), "").split(","):
        k, _, v = part.partition("=")
        if v.isdigit() and disks.get(k):
            kept += min(int(v), disks[k])
print(total, kept)
'''


# A look at a host for the host rule (run.HostWatch): it answered (the ssh ran: this always
# exits 0), its boot id and its uptime; and its pswpout for the swap rule, in the same ssh.
STATE_CMD = ("printf 'pswpout '; awk '$1 == \"pswpout\" { print $2 }' /proc/vmstat 2>/dev/null; echo; "
             "printf 'boot '; cat /proc/sys/kernel/random/boot_id 2>/dev/null; echo; "
             "printf 'up '; cut -d' ' -f1 /proc/uptime 2>/dev/null; echo; exit 0")
NO_ANSWER_RX = re.compile(r"\bno answer from (\S+) in \d+ polls")


def parse_state(text):
    """STATE_CMD's output -> ({boot, up_s}, pswpout bytes or None).  An answer without its
    lines (a fake host, a host with no /proc) is still an answer: boot and up_s None."""
    seen, sw, first = {"boot": None, "up_s": None}, None, None
    for line in str(text or "").splitlines():
        k, _, v = line.strip().partition(" ")
        v = v.strip()
        if first is None and line.strip():
            first = line.strip()
        if k == "pswpout" and v.isdigit():
            sw = int(v) * 4096
        elif k == "boot" and re.fullmatch(r"[0-9a-fA-F-]{8,64}", v):
            seen["boot"] = v
        elif k == "up":
            try:
                seen["up_s"] = float(v)
            except ValueError:
                pass
    if sw is None and first and first.isdigit():   # a bare pswpout (pswpout()'s own form)
        sw = int(first) * 4096
    return seen, sw


def host_state(io, host):
    """(seen, pswpout bytes): SEEN None when HOST didn't answer (the ssh failed or timed out),
    else {boot, up_s} (run.HostWatch.feed's); the bytes swapped out since its boot, or None."""
    try:
        rc, out, _ = io.sh(host, STATE_CMD, timeout=30)
    except Exception:   # noqa: BLE001 - an ssh that can't be made is no answer
        return None, None
    if rc != 0:
        return None, None
    return parse_state(out)


def host_seen(io, host):
    """host_state's look alone."""
    return host_state(io, host)[0]


def feed_seen(stops, host, seen):
    """SEEN into STOPS' host rule (an engine without one: "ok")."""
    fn = getattr(stops, "feed_seen", None)
    if fn is None:
        return "ok"
    try:
        return fn(host, seen) or "ok"
    except Exception:   # noqa: BLE001 - a look that can't be fed stops nothing
        return "ok"


def host_lost(stops, host, start=None, end=None, seen=None, text=None):
    """Why a harness-class result of a job on HOST from START to END (epoch or UTC times) is the
    host's going away, not the harness's (run.HostWatch.lost), or None.  TEXT (the result's
    why) saying host_job got no answer from HOST is that by itself.  When the host rule knows
    of nothing, SEEN(host) (a look: host_seen) is asked once and fed to it first, so a host
    that rebooted before any look saw it gone still counts (its uptime)."""
    fn = getattr(stops, "host_lost", None)
    if fn is None or not host:
        return None
    m = NO_ANSWER_RX.search(str(text or ""))
    try:
        why = fn(host, start, end)
        if why:
            return why
        if m and m.group(1) == host:
            return m.group(0)
        if seen is None:
            return None
        feed_seen(stops, host, seen(host))
        return fn(host, start, end)
    except Exception:   # noqa: BLE001 - a rule that can't be asked: the result counts as it would
        return None


def wait_back(stops, host, seen, sleep, max_s, log=None, cancelled=None, poll=POLL_S, clock=None):
    """Until HOST is back (each poll a look, SEEN(host), fed to the host rule): the host rule
    doesn't hold it (its own hold, not the swap rule's: a swap hold isn't the host's going away,
    and nothing here would lift it), and it is settled: its hold lifted while this waited (the
    lift is host_settle_min of answers and uptime), or it has answered here host_settle_min in
    a row and been up that long (no hold yet: a host the rule hadn't looked at before, back from
    a quick reboot, isn't ready for a rerun at its first answer).  At most MAX_S seconds by
    CLOCK (time.time), or until CANCELLED() (a run-wide stop): True once HOST is back."""
    clock = clock or time.time
    settle = _settle_s(stops)
    t0, said, ok_from, was_held = clock(), False, None, False
    while True:
        look = seen(host)
        feed_seen(stops, host, look)
        now = clock()
        try:
            held = (getattr(stops, "host_held", None) or getattr(stops, "held", None) or dict)() or {}
        except Exception:   # noqa: BLE001
            held = {}
        if look is None:
            ok_from = None
        elif ok_from is None:
            ok_from = now
        if host in held:
            was_held = True
        elif look is not None:
            up = look.get("up_s") if isinstance(look, dict) else None
            up_ok = not isinstance(up, (int, float)) or isinstance(up, bool) or up >= settle
            if up_ok and (was_held or now - ok_from >= settle):
                return True
        if not said and log is not None:
            log(f"{host}: waiting for it to be back ("
                + (f"the host rule's hold: {held[host]}" if host in held else
                   f"answering and up {settle / 60:g} min") + ")")
            said = True
        if clock() - t0 > max_s or (cancelled is not None and cancelled()):
            return False
        sleep(poll)


def _settle_s(stops):
    """The host rule's host_settle_min in seconds (STOPS' host_settle_s, else its watch's), else 300."""
    for get in (lambda: stops.host_settle_s(), lambda: stops.watch.settle_s):
        try:
            v = get()
        except Exception:   # noqa: BLE001
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return 300.0


def pswpout(io, host):
    """Bytes swapped out on HOST since its boot, or None."""
    rc, out, _ = io.sh(host, "awk '$1 == \"pswpout\" { print $2 }' /proc/vmstat", timeout=30)
    try:
        return int(out.strip()) * 4096 if rc == 0 else None
    except (ValueError, AttributeError):
        return None


def tag_bytes_kept(io, host, prefix, since):
    """(bytes, kept): the bytes the jobs tagged PREFIX (and PREFIX-rN) wrote to HOST's physical
    disks, from the jobs.log lines ending at or after SINCE (R22: never the whole disk's), and
    the part of them a failed run's state wrote as it was kept (the lines' keep=, 0 from a
    script that gives none); (None, None) when they can't be read."""
    rc, out, _ = io.sh(host, f"python3 -I -B - {shlex.quote(prefix)} {since:.0f}", input=TAG_SCRIPT.encode(), timeout=60)
    try:
        f = out.split() if rc == 0 else []
        return (int(f[0]), int(f[1]) if len(f) > 1 else 0) if f else (None, None)
    except (ValueError, AttributeError):
        return None, None


def tag_bytes(io, host, prefix, since):
    """tag_bytes_kept's bytes alone, or None."""
    return tag_bytes_kept(io, host, prefix, since)[0]


def guards_of(rs):
    """The composite's RunGuards (run.py's Shared), or None for a plain word."""
    if rs is None:
        return None
    for holder in (rs, getattr(rs, "shared", None), getattr(rs, "stops", None)):
        g = getattr(holder, "guards", None) if holder is not None else None
        if g is not None and hasattr(g, "tripped"):
            return g
    return None


def tripped(rs):
    """True once a run-wide stop rule (RunGuards) has fired."""
    g = guards_of(rs)
    try:
        return bool(g is not None and g.tripped())
    except Exception:   # noqa: BLE001 - a guard that can't be read stops nothing
        return False


def host_checks(env, rs, tag_prefix, t0):
    """Per host: swap-out since this run's first check, and the bytes this run's jobs wrote
    (TAG_PREFIX: guest:RUN), fed to rs.stops.feed_host, with KEPT the part of them failed
    runs' kept state wrote (tag_bytes_kept), which the write rule doesn't count.
    [what feed_host said, other than ok]."""
    said = []
    swap0 = getattr(env, "swap0", None)
    if swap0 is None:
        swap0 = env.swap0 = {}
    hosts = getattr(env, "hosts", None) or getattr(rs, "hosts", None) or []
    for h in hosts:
        seen, sw = host_state(env.io, h)
        what = feed_seen(rs.stops, h, seen)   # the host rule (run.HostWatch): no answer, a reboot
        if what and what != "ok":
            said.append(what)
        if seen is None:   # no answer: nothing more to read there this time
            continue
        if sw is not None and h not in swap0:
            swap0[h] = sw
        ours, kept = tag_bytes_kept(env.io, h, tag_prefix, t0 - 60)
        if ours is not None:    # env.kept {host: bytes}: the report's budget line says it
            km = getattr(env, "kept", None)
            if not isinstance(km, dict):
                km = env.kept = {}
            if kept:
                km[h] = kept
            else:
                km.pop(h, None)
        kw = {"kept": kept} if kept else {}    # (an engine of the older signature takes no KEPT)
        try:
            what = rs.stops.feed_host(h, swap_out_bytes=(sw - swap0[h]) if sw is not None else None, ours_bytes=ours,
                                      **kw)
        except Exception as e:   # noqa: BLE001 - a stop rule that can't be fed doesn't end the run
            env.log(f"{h}: host checks: {type(e).__name__}: {e}")
            continue
        if what and what != "ok":
            said.append(what)
    return said
