#!/usr/bin/env python3
"""Drive bko-rescue on a pty of a set size (RT8): scripted keys in, the
screen out through screen.py's strict interpreter, waits on screen text.

As a library (demo-test.py uses it):

    d = Drive("target/debug/bko-rescue", ["--demo"], rows=24, cols=80)
    d.wait("Which machine is this?")
    d.keys("Down", "Enter")
    d.type("sftp://nas/x")
    print(d.dump())
    code = d.exit()

Before it starts the program, it runs `bko-rescue --profile` with the same
options, environment and pty size, and holds the screen to exactly that
profile: its line style, colour and caps.  A profile in plain mode, or
`to_plain()` after `P`, reads lines instead, and then any escape byte at
all is a fault.

As a command, a script of steps (one a line, `#` comments):

    drive.py [--rows 24] [--cols 80] [--env K=V ...] [--dumps DIR]
             --script FILE -- PATH/TO/bko-rescue ARGS...

    wait TEXT          until TEXT is on the screen (5 s, `wait 10 TEXT` longer)
    gone TEXT          until it isn't
    keys K ...         named keys: Up Down Left Right Tab Enter Esc Space BS,
                       or C-x for a control key
    type TEXT          the characters of TEXT, one write each
    dump NAME          the screen in the golden form to DIR/NAME.txt
    focus TEXT         TEXT is in focus (reverse and bold)
    resize ROWS COLS
    plain              the program goes on in plain mode (after P or p)
    signal TERM|HUP
    exit N             wait for it to end with N (a signal as -N)

Exit 0 when every step passes; else the step, the fault and the screen.
Python's stdlib only.
"""

import argparse
import fcntl
import os
import re
import select
import shlex
import signal
import struct
import subprocess
import sys
import termios
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screen  # noqa: E402

KEYS = {
    "Up": b"\x1b[A", "Down": b"\x1b[B", "Right": b"\x1b[C", "Left": b"\x1b[D",
    "Tab": b"\t", "Enter": b"\r", "Esc": b"\x1b", "Space": b" ", "BS": b"\x7f",
}

PLAIN_HEAD = b"BackOtter rescue (plain mode)"

# The options that pick what bko-rescue does, left out of the --profile run.
MODES = {"--demo", "--spike", "--abi", "--check-tty", "--profile", "--version", "--help"}


class Failed(Exception):
    pass


def base_env(extra=None):
    e = {"PATH": "/usr/bin:/bin", "TERM": "xterm", "LANG": "C.UTF-8", "HOME": os.environ.get("HOME", "/")}
    e.update(extra or {})
    return e


class Pty:
    """A program on a pty of a set size, its stderr to a pipe."""

    def __init__(self, argv, rows, cols, env):
        self.master, self.slave = os.openpty()
        self.set_size(rows, cols)
        self.before = termios.tcgetattr(self.slave)
        self.p = subprocess.Popen(argv, stdin=self.slave, stdout=self.slave, stderr=subprocess.PIPE,
                                  env=env, start_new_session=True)

    def set_size(self, rows, cols):
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def read(self, secs):
        """Bytes that came within `secs`; b"" at none, None at hang-up."""
        r, _, _ = select.select([self.master], [], [], max(secs, 0))
        if not r:
            return b""
        try:
            return os.read(self.master, 65536)
        except OSError:
            return None

    def close(self):
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


def profile(binary, args, rows, cols, env):
    """`bko-rescue --profile` with these options on a pty like the run's:
    {key: value} of its lines."""
    keep, skip = [], False
    for a in args:
        if skip:
            skip = False
            if a in ("panic", "linger"):
                continue
        if a.split("=", 1)[0] in MODES:
            skip = a == "--spike"
            continue
        keep.append(a)
    t = Pty([binary, "--profile"] + keep, rows, cols, env)
    out, end = b"", time.time() + 5
    while t.p.poll() is None and time.time() < end:
        b = t.read(0.05)
        if b:
            out += b
    while True:
        b = t.read(0.05)
        if not b:
            break
        out += b
    t.p.wait()
    t.close()
    text = out.decode(errors="replace").replace("\r", "")
    return dict(l.split(" ", 1) for l in text.splitlines() if " " in l)


class Drive:
    def __init__(self, binary, args, rows=24, cols=80, env=None, timeout=5):
        self.binary = os.path.abspath(binary)
        self.args = list(args)
        self.env = base_env(env)
        self.timeout = timeout
        self.prof = profile(self.binary, self.args, rows, cols, self.env)
        mode = self.prof.get("mode", "")
        caps = rules_from(self.prof)
        self.esc_s = int(self.prof.get("esc", "250ms").rstrip("ms") or 250) / 1000
        self.rules = caps
        self.screen = screen.Screen(rows, cols, caps)
        self.plain = mode.startswith("plain")
        self.to_plain_pending = False
        self.text = b""                 # what came in plain mode
        self.out = b""                  # every byte
        self.t = Pty([self.binary] + self.args, rows, cols, self.env)
        self.hung_up = False
        self.code = None
        self.err = ""

    # ---- reading ----

    def take(self, data):
        self.out += data
        if self.to_plain_pending:
            i = data.find(PLAIN_HEAD)
            if i < 0:
                return self.screen.feed(data)
            self.screen.feed(data[:i])
            self.screen.end()
            self.to_plain_pending = False
            self.plain = True
            data = data[i:]
        if self.plain:
            self.check_plain(data)
            self.text += data
        else:
            self.screen.feed(data)

    def check_plain(self, data):
        """Plain mode sends no escape at all and no control byte but CR
        and LF (and a tab); outside UTF-8, only ASCII."""
        for i, b in enumerate(data):
            if b == 0x1b or (b < 0x20 and b not in (0x09, 0x0a, 0x0d)) or b == 0x7f:
                raise screen.Fault("plain mode, byte %d: 0x%02x" % (len(self.text) + i, b))
            if b >= 0x80 and self.rules.lines != "utf8":
                raise screen.Fault("plain mode, byte %d: 0x%02x outside UTF-8" % (len(self.text) + i, b))

    def pump(self, secs):
        """Read for `secs`, or until the program hangs up."""
        end = time.time() + secs
        while not self.hung_up:
            left = end - time.time()
            if left <= 0:
                return
            b = self.t.read(left)
            if b is None:
                self.hung_up = True
            elif b:
                self.take(b)

    def quiet(self, secs=0.15, most=3):
        """Read until nothing has come for `secs` (at most `most` s)."""
        end = time.time() + most
        while time.time() < end and not self.hung_up:
            b = self.t.read(secs)
            if b is None:
                self.hung_up = True
            elif not b:
                return
            else:
                self.take(b)

    def shows(self, s):
        if self.plain:
            return s.encode() in self.text.replace(b"\r", b"")
        return s in self.screen.text()

    def wait(self, s, secs=None, since=None):
        """Until `s` shows on the screen (in plain mode: in the text,
        after `since` bytes of it)."""
        end = time.time() + (secs or self.timeout)
        while True:
            if self.plain and since is not None:
                found = s.encode() in self.text[since:].replace(b"\r", b"")
            else:
                found = self.shows(s)
            if found:
                return
            if time.time() > end or self.hung_up:
                raise Failed("waited %ss for %r" % (secs or self.timeout, s))
            self.pump(0.05)

    def gone(self, s, secs=None):
        end = time.time() + (secs or self.timeout)
        while self.shows(s):
            if time.time() > end or self.hung_up:
                raise Failed("waited for %r to go" % s)
            self.pump(0.05)

    def mark(self):
        """Where plain mode's text is now, for `wait(..., since=)`."""
        return len(self.text)

    # ---- writing ----

    def send(self, b, settle=True):
        os.write(self.t.master, b)
        if settle:
            self.quiet()

    def keys(self, *names):
        """Named keys, each its own write; Esc waits out the Esc timeout so
        it isn't read with the next key."""
        for k in names:
            if k in KEYS:
                b = KEYS[k]
            elif re.fullmatch(r"C-[a-z]", k):
                b = bytes([ord(k[2]) - 0x60])
            elif len(k) == 1:
                b = k.encode()
            else:
                raise Failed("no key %r" % k)
            self.send(b, settle=False)
            time.sleep(self.esc_s + 0.1 if k == "Esc" else 0.03)
            self.quiet()

    def type(self, s, line=False):
        """Characters, one write each (in plain mode the line and Enter at
        once when `line`)."""
        if line:
            return self.send(s.encode() + b"\n")
        for ch in s:
            self.send(ch.encode(), settle=False)
            time.sleep(0.01)
        self.quiet()

    def resize(self, rows, cols):
        self.quiet()
        self.t.set_size(rows, cols)
        self.screen.resize(rows, cols)
        self.quiet(0.6)

    def to_plain(self):
        """The next bytes put the terminal back, then plain mode starts."""
        self.to_plain_pending = True

    def set_profile(self, lines=None, colour=None):
        """After `L` or `C`: the screen is held to the new style.  Call it
        after quiet() and before the key."""
        self.quiet()
        if lines is not None:
            self.rules.lines = lines
        if colour is not None:
            self.rules.colour = colour

    def signal(self, sig):
        self.t.p.send_signal(sig)

    # ---- the end ----

    def exit(self, secs=5):
        """Wait for it to end: its exit code (a signal as -N)."""
        end = time.time() + secs
        while self.t.p.poll() is None and time.time() < end:
            self.pump(0.05)
        self.quiet(0.1, 1)
        if self.t.p.poll() is None:
            self.t.p.kill()
        self.code = self.t.p.wait()
        self.err = self.t.p.stderr.read().decode(errors="replace")
        if not self.plain and not self.to_plain_pending:
            self.screen.end()
        return self.code

    def attrs(self):
        return termios.tcgetattr(self.t.slave)

    def settings_back(self):
        return self.attrs() == self.t.before

    def dump(self):
        if self.plain:
            return self.text.decode(errors="replace").replace("\r", "")
        return self.screen.dump()

    def close(self):
        if self.t.p.poll() is None:
            self.t.p.kill()
            self.t.p.wait()
        self.t.close()


def rules_from(prof):
    """screen.Rules from --profile's lines."""
    lines = prof.get("lines", "ascii").split()[0]
    colour = prof.get("colour", "off").startswith("on")
    j, h, c = screen.Rules.parse_caps(prof.get("caps", "none"))
    return screen.Rules(lines, colour, j, h, c)


# ---- the script runner ----

def run_script(d, steps, dumps):
    for n, raw in steps:
        try:
            step(d, n, raw, dumps)
        except (Failed, screen.Fault) as e:
            raise Failed("line %d, %s: %s" % (n, raw, e))


def step(d, n, raw, dumps):
    w = shlex.split(raw)
    cmd, rest = w[0], w[1:]
    if cmd == "wait" or cmd == "gone":
        secs = None
        if len(rest) > 1 and re.fullmatch(r"\d+(\.\d+)?", rest[0]):
            secs, rest = float(rest[0]), rest[1:]
        (d.wait if cmd == "wait" else d.gone)(" ".join(rest), secs)
    elif cmd == "keys":
        d.keys(*rest)
    elif cmd == "type":
        d.type(" ".join(rest))
    elif cmd == "dump":
        if not dumps:
            raise Failed("dump without --dumps")
        d.quiet()
        with open(os.path.join(dumps, rest[0] + ".txt"), "w") as f:
            f.write(d.dump())
    elif cmd == "focus":
        want = " ".join(rest)
        d.quiet()
        if not any(want in x for x in d.screen.focused()):
            raise Failed("%r isn't in focus: %r is" % (want, d.screen.focused()))
    elif cmd == "resize":
        d.resize(int(rest[0]), int(rest[1]))
    elif cmd == "plain":
        d.to_plain()
    elif cmd == "signal":
        d.signal(getattr(signal, "SIG" + rest[0]))
    elif cmd == "exit":
        code = d.exit()
        if code != int(rest[0]):
            raise Failed("exit %d, not %s: %s" % (code, rest[0], d.err.strip()))
        if not d.settings_back():
            raise Failed("the terminal's settings weren't put back")
    else:
        raise Failed("line %d: no step %r" % (n, cmd))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rows", type=int, default=24)
    ap.add_argument("--cols", type=int, default=80)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--dumps")
    ap.add_argument("--script", required=True)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        ap.error("give the program after --")
    steps = []
    with open(a.script) as f:
        for n, l in enumerate(f, 1):
            l = l.strip()
            if l and not l.startswith("#"):
                steps.append((n, l))
    if a.dumps:
        os.makedirs(a.dumps, exist_ok=True)
    d = Drive(cmd[0], cmd[1:], a.rows, a.cols, dict(e.split("=", 1) for e in a.env))
    try:
        run_script(d, steps, a.dumps)
    except (Failed, screen.Fault) as e:
        print("drive.py: %s\n%s" % (e, d.dump()), file=sys.stderr)
        d.close()
        return 1
    d.close()
    print("drive.py: %d steps passed" % len(steps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
