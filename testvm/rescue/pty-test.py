#!/usr/bin/env python3
"""bko-rescue's terminal layer on a real pty (RT2, RT4): raw mode on and back,
--check-tty, --profile, and the spike driven by keys in each line style,
with every byte it sends checked against the sequences it may send.

    testvm/rescue/pty-test.py PATH/TO/bko-rescue

Python's stdlib only.  Exit 0 when every check passes.
"""

import fcntl
import os
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

BIN = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "bko-rescue"
FAILS = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


class Pty:
    """bko-rescue on a pty of a set size, its stderr to a pipe."""

    def __init__(self, args, rows=24, cols=80, env=None):
        self.master, self.slave = os.openpty()
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        e = {"PATH": "/usr/bin:/bin", "TERM": "xterm", "LANG": "C.UTF-8"}
        e.update(env or {})
        self.before = termios.tcgetattr(self.slave)
        self.p = subprocess.Popen(
            [BIN] + args, stdin=self.slave, stdout=self.slave, stderr=subprocess.PIPE,
            env=e, start_new_session=True)
        self.out = b""

    def read(self, secs):
        end = time.time() + secs
        while True:
            left = end - time.time()
            if left <= 0:
                return
            r, _, _ = select.select([self.master], [], [], left)
            if r:
                try:
                    self.out += os.read(self.master, 65536)
                except OSError:
                    return

    def send(self, b, wait=0.15):
        os.write(self.master, b)
        self.read(wait)

    def wait(self, secs=5):
        end = time.time() + secs
        while self.p.poll() is None and time.time() < end:
            self.read(0.05)
        self.read(0.1)
        if self.p.poll() is None:
            self.p.kill()
        code = self.p.wait()
        err = self.p.stderr.read().decode(errors="replace")
        return code, err

    def attrs(self):
        return termios.tcgetattr(self.slave)

    def close(self):
        os.close(self.master)
        os.close(self.slave)


# Exactly what out.rs may send: move, home, clear, SGR with 0 1 7 30-37
# 40-47, cursor hide and show, DEC on and off.
SGR_OK = {0, 1, 7} | set(range(30, 38)) | set(range(40, 48))
SEQ = re.compile(rb"\x1b(\[[0-9;?]*[ -/]*[@-~]|\(.|.)", re.S)


def bad_sequences(out, utf8, dec=False, hide=True, clear=True, joined=True):
    """What in `out` a profile may not send: DEC on and off only in the
    DEC line style, cursor hiding only when it hides, ESC [ 2 J only when
    it clears that way, one SGR parameter a sequence when not joined."""
    bad = []
    for m in SEQ.finditer(out):
        s = m.group(0)
        if re.fullmatch(rb"\x1b\[\d+;\d+H|\x1b\[H", s):
            continue
        if dec and re.fullmatch(rb"\x1b\(0|\x1b\(B", s):
            continue
        if hide and re.fullmatch(rb"\x1b\[\?25[lh]", s):
            continue
        if clear and s == b"\x1b[2J":
            continue
        g = re.fullmatch(rb"\x1b\[([0-9;]+)m", s)
        if g:
            ps = g.group(1).split(b";")
            if all(p and int(p) in SGR_OK for p in ps) and (joined or len(ps) == 1):
                continue
        bad.append(s)
    rest = SEQ.sub(b"", out)
    for i, b in enumerate(rest):
        if b < 0x20 and b not in (0x0a, 0x0d):
            bad.append(rest[i:i + 1])
    if not utf8 and any(b >= 0x80 for b in rest):
        bad.append(b"a byte >= 0x80")
    if utf8:
        try:
            rest.decode("utf-8")
        except UnicodeDecodeError:
            bad.append(b"invalid UTF-8")
    return bad


def raw_flags_off(a):
    iflag, lflag = a[0], a[3]
    return (lflag & (termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN) == 0
            and iflag & (termios.ICRNL | termios.IXON) == 0)


def test_check_tty():
    t = Pty(["--check-tty"])
    code, err = t.wait()
    check(code == 0 and b"check-tty: ok" in t.out, "--check-tty passes on a pty (%d %r)" % (code, t.out[-120:]))
    check(t.attrs() == t.before, "--check-tty leaves the settings as they were")
    t.close()
    p = subprocess.run([BIN, "--check-tty"], stdin=subprocess.DEVNULL, capture_output=True)
    check(p.returncode == 1 and b"not a terminal" in p.stdout, "--check-tty fails without a terminal")


def profile(env, rows=24, cols=80, args=()):
    t = Pty(["--profile"] + list(args), rows, cols, env)
    code, _ = t.wait()
    t.close()
    text = t.out.decode(errors="replace").replace("\r", "")
    return code, dict(l.split(" ", 1) for l in text.splitlines() if " " in l)


def test_profile():
    code, d = profile({})
    check(code == 0 and d.get("tty", "").startswith("pts/"), "--profile names the pty: %r" % d.get("tty"))
    check(d.get("lines", "").startswith("utf8") and d.get("colour", "").startswith("on"),
          "ssh-style pty, C.UTF-8: utf8, colour")
    check(d.get("size") == "80x24", "size 80x24: %r" % d.get("size"))
    _, d = profile({"LANG": "C", "TERM": "vt220"})
    check(d.get("lines", "").startswith("ascii") and d.get("colour", "").startswith("off"),
          "LANG=C, TERM=vt220: ascii, no colour")
    _, d = profile({}, 0, 0)
    check(d.get("size", "").startswith("80x24 (0x0"), "0x0 taken as 80x24: %r" % d.get("size"))
    _, d = profile({"BKO_RESCUE_LINES": "dec"}, args=["--colour", "off"])
    check(d.get("lines") == "dec (BKO_RESCUE_LINES)" and d.get("colour") == "off (--colour)",
          "overrides from the environment and the flag")
    _, d = profile({"TERM": "dumb"})
    check(d.get("mode", "").startswith("plain"), "TERM=dumb is plain mode")


def spike_run(name, args, env, utf8, dec=False):
    t = Pty(["--spike"] + args, env=env)
    t.read(0.8)
    check(t.p.poll() is None and raw_flags_off(t.attrs()), name + ": raw mode on while running")
    first = len(t.out)
    check(first < 6000, name + ": first frame %d bytes" % first)
    t.send(b"2")
    t.send(b"\x1b[B")                     # Down
    t.send(b"\x1bOB")                     # Down, SS3 form
    t.send(b"\r")
    check(b"chose item 3" in t.out, name + ": list moved twice, Enter chose item 3")
    t.send(b"3", 1.3)                      # the gauge for a while
    t.send(b"4")
    t.send(b"ab\xc3\xa9 \x7f")             # four typed, then Backspace
    t.send(b"\r")
    check(b"count typed: 3" in t.out, name + ": password count 3")
    check(b"4 * * * * BS Enter" in t.out and b"\xc3\xa9" not in t.out,
          name + ": the key log shows * for typed characters")
    t.send(b"\x1bq")                      # Esc and q in one read (Alt+q)
    check(t.p.poll() is None and b"Enter Esc *" in t.out,
          name + ": Esc then q in one read: q logged as * and not run")
    t.send(b"\x1b", 0.35)                  # Esc alone leaves the field
    t.send(b"1")
    t.send(b"\t")
    t.send(b"\r")
    check(b"pressed Back" in t.out, name + ": Tab moved the focus, Enter pressed Back")
    t.send(b"q")
    code, err = t.wait()
    check(code == 0, name + ": exit 0 (%d %s)" % (code, err.strip()))
    check(t.attrs() == t.before, name + ": settings put back")
    bad = bad_sequences(t.out, utf8, dec)
    check(not bad, name + ": only allowed sequences (%r)" % bad[:5])
    t.close()
    return t.out


def test_spike_fallbacks():
    """The fallback profile DragonFly's syscons and illumos's tem get: one
    SGR parameter each, the cursor parked, clearing with spaces, ASCII.
    The keys turn each on, then only the bytes after are checked, the
    exit's blanking included."""
    name = "fallbacks"
    t = Pty(["--spike"])
    t.read(0.8)
    for k in b"llj":                       # utf8 -> dec -> ascii; sgr one-each
        t.send(bytes([k]), 0.3)
    t.send(b"h", 0.3)                      # the cursor parked (shows it first)
    mark = len(t.out)
    t.send(b"e", 0.4)                      # clear with spaces
    check(b"sgr one-each | cursor park | clear spaces" in t.out[mark:],
          name + ": the card says the fallback profile")
    t.send(b"2")
    t.send(b"\x1b[B")
    t.send(b"\r")
    t.send(b"3", 1.3)
    t.send(b"4")
    t.send(b"ab\r")
    t.send(b"\x1b", 0.35)
    t.send(b"1")
    t.send(b"\t")
    t.send(b"\r")
    check(b"chose item 2" in t.out and b"count typed: 2" in t.out and b"pressed Back" in t.out,
          name + ": the cards still work")
    t.send(b"q")
    code, err = t.wait()
    check(code == 0 and t.attrs() == t.before, name + ": exit 0, settings put back (%d %s)" % (code, err.strip()))
    tail = t.out[mark:]
    bad = bad_sequences(tail, False, dec=False, hide=False, clear=False, joined=False)
    check(not bad, name + ": only the fallback's sequences after the keys (%r)" % bad[:5])
    check(b"\x1b[24;1H" + b" " * 79 + b"\x1b[H" in tail and tail.rstrip(b"\r\n").endswith(b"done"),
          name + ": the exit blanks with spaces, the last cell left alone")
    t.close()


def test_spike():
    out = spike_run("utf8 colour", [], {}, True)
    # White on blue, in full or as a change after a reset (out.rs sends
    # only what changes).
    check("┌".encode() in out and re.search(rb"\x1b\[(0;)?37;44m", out), "utf8 colour: box glyphs and white on blue")
    check(b"Up" in out or b"Down" in out, "utf8 colour: key log shows Down")
    out = spike_run("dec mono", ["--lines", "dec", "--colour", "off"], {}, False, dec=True)
    check(b"\x1b(0lq" in out and b"\x1b(B" in out, "dec mono: DEC glyphs")
    check(all(not re.search(rb"(^|;)[34]\d", m) for m in re.findall(rb"\x1b\[([0-9;]*)m", out)),
          "dec mono: no colour parameter anywhere")
    out = spike_run("ascii", [], {"LANG": "C", "TERM": "vt220"}, False)
    check(b"+---" in out and b"\x1b(0" not in out, "ascii: + and - lines, no DEC")
    # The gauge's own updates, as the card reports them.
    m = re.findall(rb"the most (\d+) bytes", out)
    check(m and int(m[-1]) < 40, "gauge updates under 40 bytes (most %s)" % (m[-1] if m else None))


def test_spike_panic_and_signals():
    t = Pty(["--spike", "panic"])
    code, err = t.wait(5)
    check(code == 101 and "on purpose" in err, "--spike panic: exit 101 with the message (%d)" % code)
    check(t.attrs() == t.before, "--spike panic: settings put back")
    check(t.out.endswith(b"\x1b[0m\x1b[?25h\x1b[2J\x1b[H"), "--spike panic: screen reset before the message")
    t.close()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        t = Pty(["--spike"])
        t.read(0.5)
        t.p.send_signal(sig)
        code, err = t.wait(3)
        check(code == 128 + sig, "%s: exit %d (%d)" % (sig.name, 128 + sig, code))
        check(t.attrs() == t.before, "%s: settings put back" % sig.name)
        t.close()
    # Shutdown may send TERM twice: the second, in the cleanup, mustn't
    # kill it with the default action (illumos's SysV signal()).
    t = Pty(["--spike"])
    t.read(0.5)
    t.p.send_signal(signal.SIGTERM)
    time.sleep(0.01)
    t.p.send_signal(signal.SIGTERM)
    code, err = t.wait(3)
    check(code == 128 + signal.SIGTERM, "SIGTERM twice: exit %d (%d)" % (128 + signal.SIGTERM, code))
    check(t.attrs() == t.before, "SIGTERM twice: settings put back")
    t.close()


def test_resize_and_small():
    t = Pty(["--spike"], rows=30, cols=100)
    t.read(0.6)
    check(b"\x1b[4;11H" in t.out, "30x100: the canvas centred (row 1 at 4, column 1 at 11)")
    fcntl.ioctl(t.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 20, 60, 0, 0))
    t.read(0.6)
    check(b"needs 80x24; this terminal is 60x20" in t.out, "a resize to 60x20 noticed within 0.6 s")
    t.send(b"x", 0.3)                      # one redraw for the key, then quiet
    mark = len(t.out)
    t.read(1.0)
    check(len(t.out) == mark, "too small: no redraw on each tick after a key (%d bytes)" % (len(t.out) - mark))
    t.send(b"q")
    code, _ = t.wait()
    check(code == 0 and t.attrs() == t.before, "quit from the too-small screen")
    t.close()


def test_no_tty():
    p = subprocess.run([BIN, "--spike"], stdin=subprocess.DEVNULL, capture_output=True)
    check(p.returncode == 2 and b"plain mode" in p.stderr, "--spike without a terminal refuses")


def test_abi_and_options():
    p = subprocess.run([BIN, "--abi"], capture_output=True)
    check(p.returncode == 0 and b"size termios" in p.stdout, "--abi prints")
    p = subprocess.run([BIN, "--lines", "fancy"], capture_output=True)
    check(p.returncode == 2 and b"usage" in p.stderr, "a bad option is exit 2 with the usage")
    p = subprocess.run([BIN, "--plain=no"], capture_output=True)
    check(p.returncode == 2 and b"takes no value" in p.stderr, "--plain=no is an error")
    p = subprocess.run([BIN, "--x\x1b[2J"], capture_output=True)
    check(p.returncode == 2 and b"\x1b" not in p.stderr, "an option's escape isn't echoed")


for f in (test_check_tty, test_profile, test_spike, test_spike_fallbacks, test_spike_panic_and_signals,
          test_resize_and_small, test_no_tty, test_abi_and_options):
    print("==", f.__name__)
    f()
print("%d failed" % len(FAILS))
sys.exit(1 if FAILS else 0)
