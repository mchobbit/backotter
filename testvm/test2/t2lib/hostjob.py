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
                        per host: swap-out since the first check, and the bytes this run's
                        wear tags wrote, into rs.stops.feed_host (and so RunGuards)
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
        return o


UNKNOWN_MAX = 20   # polls in a row with no answer from the host before host_job gives up


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
    ON_BEGAN() is called once, when the job has left cmp-run.sh's line (or has ended)."""
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
    rc, _, err = env.io.sh(host, f"mkdir -p {_q(out)} && cd {_q(out)} && rm -f .exit .began .submitted .ended .pid")
    if rc != 0:
        return None, out, {}, JobFailure(f"can't make {out} on {host}: {err.strip()[-150:]}")
    pid = env.io.start(host, f"bash -c {shlex.quote(body)}", out + "/job.log", out + "/.pid")
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
        if hasattr(env.io, "stop"):
            try:
                gone = env.io.stop(host, pid, needle)
            except Exception:   # noqa: BLE001
                gone = False
        env.log(f"{host}: {subdir}:{name} attempt {attempt}: {why}; "
                + ("stopped" if gone else f"pid {pid} may still run: no rerun beside it"))
        return None, out, {}, JobFailure(f"{why}; " + ("stopped" if gone else f"pid {pid} may still run"),
                                         still_running=not gone)

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
        if unknown >= UNKNOWN_MAX:
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
        if unknown >= UNKNOWN_MAX:
            began_now()
            return None, out, {}, JobFailure(f"no answer from {host} in {unknown} polls (resume)", still_running=True)
        if time.time() - t0 > timeout_s + wait_s:
            began_now()
            stopped = False
            if pid.isdigit() and hasattr(env.io, "stop"):
                try:
                    stopped = env.io.stop(host, int(pid), needle)
                except Exception:   # noqa: BLE001
                    stopped = False
            return None, out, {}, JobFailure(f"attempt {attempt}'s job on {host} has no end after {timeout_s + wait_s} s (resume)",
                                             still_running=not stopped)
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
total = 0
try:
    with open("/bkoscratch/wear/jobs.log", errors="replace") as f:
        lines = f.read().splitlines()
except OSError:
    lines = []
# TIME HOST SCOPE RC SECS DISK=BYTES[,DISK=BYTES] WHAT tag=TAG serial=... (tab-separated)
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
    for part in f[5].split(","):
        k, _, v = part.partition("=")
        if v.isdigit() and k and not virt.match(k):
            total += int(v)
print(total)
'''


def pswpout(io, host):
    """Bytes swapped out on HOST since its boot, or None."""
    rc, out, _ = io.sh(host, "awk '$1 == \"pswpout\" { print $2 }' /proc/vmstat", timeout=30)
    try:
        return int(out.strip()) * 4096 if rc == 0 else None
    except (ValueError, AttributeError):
        return None


def tag_bytes(io, host, prefix, since):
    """Bytes the jobs tagged PREFIX (and PREFIX-rN) wrote to HOST's physical disks, from
    the jobs.log lines ending at or after SINCE (R22: never the whole disk's), or None."""
    rc, out, _ = io.sh(host, f"python3 -I -B - {shlex.quote(prefix)} {since:.0f}", input=TAG_SCRIPT.encode(), timeout=60)
    try:
        return int(out.strip()) if rc == 0 else None
    except (ValueError, AttributeError):
        return None


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
    (TAG_PREFIX: guest:RUN), fed to rs.stops.feed_host.  [what feed_host said, other than ok]."""
    said = []
    swap0 = getattr(env, "swap0", None)
    if swap0 is None:
        swap0 = env.swap0 = {}
    hosts = getattr(env, "hosts", None) or getattr(rs, "hosts", None) or []
    for h in hosts:
        sw = pswpout(env.io, h)
        if sw is not None and h not in swap0:
            swap0[h] = sw
        ours = tag_bytes(env.io, h, tag_prefix, t0 - 60)
        try:
            what = rs.stops.feed_host(h, swap_out_bytes=(sw - swap0[h]) if sw is not None else None, ours_bytes=ours)
        except Exception as e:   # noqa: BLE001 - a stop rule that can't be fed doesn't end the run
            env.log(f"{h}: host checks: {type(e).__name__}: {e}")
            continue
        if what and what != "ok":
            said.append(what)
    return said
