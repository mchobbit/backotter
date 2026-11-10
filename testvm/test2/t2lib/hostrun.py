"""Running things on the KVM test hosts, and the context t2-host gives row builders
(contract P1 R1.8, rulings R10, R11, R12).

    ssh(host, cmd|script, args, timeout)   one command or one stdin Python script
    session(host, script, args)             a stdin script that keeps talking (JSON lines)
    detached(host, cmd, log, pidfile)       a detached start; returns the PID it launched
    alive(host, pid, needle) / signal(host, pid, sig, needle)
                                            only for PIDs we recorded: no pkill, and no
                                            pgrep by name or user
    rsync(srcs, host, dest, ...)            rsync -rlp --checksum over the same ssh
    HostCtx                                 what t2-host row KIND gives rows_KIND.build

Every ssh is: ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 m@HOST.local
(T2_SSH names another ssh program, for tests).  The host "local" runs on this
machine, without ssh (tests, and t2-host on a host itself).  A stdin script runs
as python3 -I -B -: isolated, and writing no bytecode; every remote command,
session and detached start also exports PYTHONDONTWRITEBYTECODE=1 (R12: a root
run's __pycache__ would be root-owned in a copy).  DATA given to ssh(script=...)
is put in front of the script as _T2_DATA, a JSON text, since stdin carries the
script itself.  host_path() turns the Mac's own home into ~ for the hosts, so
T2_HOST_ROOT=$HOME/backotter-work/... set on the Mac names /home/m/backotter-work/... there.
"""

import json
import os
import re
import select
import shlex
import signal as _signal
import subprocess
import sys
import time

from . import (copy_root, expand, host_ledger_file, host_root, is_root, short_host, utc_now, work_dir)

SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15")
LOCAL = "local"
USER = "m"
STAMP_RE = re.compile(r"[0-9a-f]{7,40}(\.dirty)?\Z")
EXCLUDES = ("__pycache__", "*.pyc", ".DS_Store", "holt/target")
BOOT = ("import sys;n=int(sys.stdin.buffer.readline());s=sys.stdin.buffer.read(n);"
        "sys.argv=['t2-session']+sys.argv[1:];g={'__name__':'__main__'};exec(compile(s,'t2-session','exec'),g)")
# R12: whatever t2 starts on a host writes no bytecode into a copy (a root run's
# __pycache__ would be root-owned there).
NOBYTECODE = "export PYTHONDONTWRITEBYTECODE=1; "


class Result:
    def __init__(self, rc, out, err):
        self.rc, self.out, self.err = rc, out, err

    @property
    def text(self):
        return self.out.decode("utf-8", "replace")

    @property
    def errtext(self):
        return self.err.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.out.decode("utf-8"))

    def __repr__(self):
        return f"Result(rc={self.rc}, out={len(self.out)} B, err={self.errtext[-120:]!r})"


def dest(host):
    """m@HOST.local for a bare HOST, m@HOST when it names a domain, HOST as given
    when it names a user."""
    if "@" in host:
        return host
    if "." in host:
        return f"{USER}@{host}"
    return f"{USER}@{host}.local"


def ssh_base():
    prog = os.environ.get("T2_SSH") or "ssh"
    return shlex.split(prog) + list(SSH_OPTS)


def host_path(p):
    """A path the way a host should read it: the Mac's own home becomes ~ (the
    hosts' home is /home/m, the Mac's isn't)."""
    if p is None:
        return None
    home = os.path.expanduser("~")
    if not is_root() and (p == home or p.startswith(home + "/")) and sys.platform == "darwin":
        return "~" + p[len(home):]
    return p


def rq(p):
    """P quoted for a remote shell, a leading ~/ left for the shell to expand."""
    if p == "~":
        return '"$HOME"'
    if p.startswith("~/"):
        return '"$HOME"/' + shlex.quote(p[2:])
    return shlex.quote(p)


def _script_input(script, data):
    """The stdin of a script run: SCRIPT (source, or a .py file's path), DATA in front as _T2_DATA."""
    if isinstance(script, str) and "\n" not in script and script.endswith(".py") and os.path.isfile(script):
        with open(script, encoding="utf-8") as f:
            script = f.read()
    pre = ""
    if data is not None:
        pre = "_T2_DATA = " + json.dumps(json.dumps(data, ensure_ascii=True)) + "\n"
    return (pre + script).encode("utf-8")


def _python(host):
    return sys.executable if host == LOCAL else "python3"


def ssh(host, cmd=None, script=None, args=(), timeout=60, input=None, data=None, env=None):
    """Run CMD (a shell string, or a list to quote) or SCRIPT (Python source, or a
    .py path) with ARGS on HOST.  Returns Result(rc, out, err); rc 255 or 124
    (timeout) when ssh itself failed."""
    if script is not None:
        input = _script_input(script, data)
        remote = " ".join([shlex.quote(_python(host)), "-I", "-B", "-"] + [shlex.quote(str(a)) for a in args])
    elif isinstance(cmd, (list, tuple)):
        remote = " ".join(shlex.quote(str(c)) for c in cmd)
    elif isinstance(cmd, str):
        remote = cmd
    else:
        raise ValueError("ssh: give cmd or script")
    remote = NOBYTECODE + remote
    if host == LOCAL:
        argv = ["/bin/sh", "-c", remote]
    else:
        argv = ssh_base() + [dest(host), remote]
    try:
        p = subprocess.run(argv, input=input if input is not None else b"", capture_output=True, timeout=timeout,
                           env=env)
        return Result(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired as e:
        return Result(124, e.stdout or b"", (e.stderr or b"") + f"\ntimed out after {timeout} s".encode())
    except OSError as e:
        return Result(255, b"", str(e).encode())


class Session:
    """A Python script on HOST that reads JSON lines from us and answers in JSON
    lines (the copy lock holder).  Closing our end ends it."""

    def __init__(self, host, script, args=(), timeout=60):
        self.host, self.timeout = host, timeout
        remote = NOBYTECODE + " ".join([shlex.quote(_python(host)), "-I", "-B", "-c", shlex.quote(BOOT)]
                                       + [shlex.quote(str(a)) for a in args])
        argv = ["/bin/sh", "-c", remote] if host == LOCAL else ssh_base() + [dest(host), remote]
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        payload = _script_input(script, None)
        self.p.stdin.write(b"%d\n" % len(payload) + payload)
        self.p.stdin.flush()
        self._buf = b""

    def send(self, obj):
        self.p.stdin.write((json.dumps(obj, sort_keys=True) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def recv(self, timeout=None):
        """The next JSON line, or None when the script ended."""
        fd = self.p.stdout.fileno()
        wait = self.timeout if timeout is None else timeout
        end = time.monotonic() + wait
        while b"\n" not in self._buf:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"{self.host}: no answer in {wait} s")
            r, _, _ = select.select([fd], [], [], left)
            if not r:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    def ask(self, obj, timeout=None):
        self.send(obj)
        return self.recv(timeout)

    def close(self, timeout=30):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.p.kill()   # our own child (the ssh, or the local script)
            self.p.wait()
        err = self.p.stderr.read() or b""
        for f in (self.p.stdout, self.p.stderr):
            try:
                f.close()
            except OSError:
                pass
        return self.p.returncode, err.decode("utf-8", "replace")


def session(host, script, args=(), timeout=60):
    return Session(host, script, args, timeout)


def detached(host, cmd, log, pidfile):
    """Start shell command CMD on HOST detached (setsid -f, stdin from /dev/null,
    output to LOG); it writes its own PID to PIDFILE first.  Returns that PID, or
    None when it didn't start."""
    if host == LOCAL:
        with open(expand(log), "ab") as out:
            p = subprocess.Popen(["/bin/sh", "-c", f"{NOBYTECODE}echo $$ > {rq(expand(pidfile))}; exec {cmd}"],
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
        return p.pid
    inner = f"{NOBYTECODE}echo $$ > {rq(pidfile)}; exec {cmd}"
    remote = (f"rm -f {rq(pidfile)}; setsid -f sh -c {shlex.quote(inner)} > {rq(log)} 2>&1 < /dev/null; "
              f"i=0; while [ ! -s {rq(pidfile)} ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done; cat {rq(pidfile)}")
    r = ssh(host, remote, timeout=30)
    pid = r.text.strip()
    return int(pid) if r.rc == 0 and pid.isdigit() else None


def alive(host, pid, needle=None):
    """True while PID runs on HOST (and its command line holds NEEDLE, when given:
    against a reused PID)."""
    pid = int(pid)
    if needle is None:
        cmd = f"test -d /proc/{pid}"
    else:
        cmd = f"test -r /proc/{pid}/cmdline && tr '\\0' ' ' < /proc/{pid}/cmdline | grep -qF -- {shlex.quote(needle)}"
    if host == LOCAL and not os.path.isdir("/proc"):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        if needle is None:
            return True
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
        return needle in r.stdout
    return ssh(host, cmd, timeout=30).rc == 0


def signal(host, pid, sig="TERM", needle=None):
    """kill -SIG PID on HOST: only a PID we recorded, and only while it is still
    ours (NEEDLE, required, in its command line: never a reused PID)."""
    pid = int(pid)
    if pid <= 1:
        raise ValueError("no such PID to signal")
    if not needle:
        raise ValueError("signal() needs a needle from the recorded command line")
    if not alive(host, pid, needle):
        return False
    if host == LOCAL:
        try:
            os.kill(pid, getattr(_signal, "SIG" + sig))
            return True
        except ProcessLookupError:
            return False
    return ssh(host, f"kill -{shlex.quote(sig)} {pid}", timeout=30).rc == 0


def rsync(srcs, host, dest_dir, excludes=EXCLUDES, link_dest=None, delete=True, timeout=900):
    """rsync -rlp --checksum [--delete] SRCS to DEST_DIR on HOST (an absolute path),
    with the copy's exclusions (and --link-dest)."""
    cmd = ["rsync", "-rlp", "--checksum"] + (["--delete"] if delete else [])
    for e in excludes:
        cmd += ["--exclude", e]
    if link_dest:
        cmd.append(f"--link-dest={link_dest}")
    if host == LOCAL:
        target = dest_dir.rstrip("/") + "/"
    else:
        cmd += ["-e", " ".join(shlex.quote(x) for x in ssh_base())]
        target = f"{dest(host)}:{dest_dir.rstrip('/')}/"
    cmd += [s.rstrip("/") for s in srcs] + [target]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return Result(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired as e:
        return Result(124, e.stdout or b"", (e.stderr or b"") + b"\nrsync timed out")


# --- the host context (t2-host row KIND) -------------------------------------------------

class HostCtx:
    """What t2-host gives t2lib.rows_KIND.build(argv, hostctx):

        host, work, host_root, copy_root, ledger_path   this host, its roots (R1.1)
        tool, test2, testvm, base     t2-host, its testvm/test2, testvm and the tree
                                      above it (a private copy ID/, or ~/backotter-work)
        env                           the environment
        tree_info(base)               {commit, committed, dirty, copy, path} of a tree
        source(base)                  a keys.TreeSource of it
        facts(want)                   this host's facts (facts_host.py, in-process)
        measure(cell, opts, src)      keys.measure() with this host's facts and that source
        now()                         UTC ISO time
        serials()                     this host's disk serials, for scrubbing only
        row(**fields)                 a row's common fields filled: host, commit, committed, dirty, copy
        append(row)                   complete, scrub, validate and append it: (id, appended)
    """

    def __init__(self, work=None, tool=None, env=None):
        self.env = dict(os.environ if env is None else env)
        self.host = short_host()
        self.work = work_dir(work)
        self.host_root = host_root()
        self.copy_root = copy_root()
        self.ledger_path = host_ledger_file(self.host)
        self.tool = os.path.abspath(tool or os.path.join(os.path.dirname(os.path.dirname(__file__)), "t2-host"))
        self.test2 = os.path.dirname(self.tool)
        self.testvm = os.path.dirname(self.test2)
        self.base = os.path.dirname(self.testvm)
        self._facts = {}

    def now(self):
        return utc_now()

    def tree_info(self, base=None):
        """The tree's commit: a private copy's T2-COPY.json, else the synced tree's
        .backotter-commit stamps (testvm/ and stick/; sync-hosts.sh writes them)."""
        base = base or self.base
        info = None
        try:
            with open(os.path.join(base, "T2-COPY.json"), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            pass
        if info and info.get("commit"):
            dirty = info.get("dirty")
            return {"commit": info["commit"], "committed": dirty == {}, "dirty": dirty,
                    "copy": info.get("id") or os.path.basename(base), "path": base}
        stamps = []
        for d in ("testvm", "stick"):
            try:
                with open(os.path.join(base, d, ".backotter-commit"), encoding="utf-8") as f:
                    stamps.append(f.readline().strip())
            except OSError:
                stamps.append(None)
        if any(x is not None for x in stamps):
            # Clean only when both stamps are there, name a commit, agree and carry no
            # .dirty (sync-hosts.sh writes an empty stamp when git rev-parse fails).
            ok = [x for x in stamps if x is not None and STAMP_RE.match(x)]
            bases = {x.split(".")[0] for x in ok}
            commit = bases.pop() if len(bases) == 1 else None
            clean = len(ok) == 2 and commit is not None and all(x == commit for x in ok)
            return {"commit": commit, "committed": clean, "dirty": {} if clean else None,
                    "copy": "synced", "path": base}
        return {"commit": None, "committed": False, "dirty": None, "copy": "unknown", "path": base}

    def source(self, base=None):
        from . import keys
        return keys.TreeSource(base or self.base)

    def facts(self, want=None):
        k = json.dumps(want or {}, sort_keys=True)
        if k not in self._facts:
            from . import facts_host
            self._facts[k] = facts_host.collect(want=want or {}, work=self.work)
        return self._facts[k]

    def measure(self, cell, opts=None, src=None, facts=None, specs=None, want=None):
        from . import keys
        src = src or self.source()
        return keys.measure(cell, src, facts if facts is not None else self.facts(want), specs, opts)

    def serials(self):
        from . import ledger
        return ledger.local_serials()

    def row(self, **fields):
        ti = self.tree_info()
        r = {"host": self.host, "commit": ti["commit"], "committed": ti["committed"], "dirty": ti["dirty"],
             "copy": ti["copy"]}
        r.update(fields)
        return r

    def append(self, row):
        from . import ledger
        r = ledger.complete(row, host=self.host, serials=self.serials())
        ledger.check(r)
        return ledger.append(self.ledger_path, [r])[0]
