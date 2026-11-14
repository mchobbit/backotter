#!/usr/bin/env python3
"""Drive OpenIndiana's text installer, or the installed system's first
boot, over a QEMU guest's serial console and its monitor (for
make-openindiana.sh).

Usage:
  install.py install    DIR NAME PASSWORD_FILE
  install.py first-boot DIR NAME PASSWORD_FILE PUBKEY_FILE

DIR holds QEMU's sockets, con.sock (-serial unix:DIR/con.sock,server) and
mon.sock (-monitor unix:DIR/mon.sock,server), and gets console.log, all the
console said.  The guest must have been started with both, and not have
reached its loader menu yet.

What it does, as found on the 2026.10 text ISO (docs/design/openindiana.md,
"What O1 found"):
- The loader (both modes) reads only the firmware's keyboard, not the
  serial port, and puts the kernel's console on the screen: keys typed with
  the monitor's sendkey stop the countdown ("3", the loader prompt) and set
  console=ttya and os_console=ttya before booting.  After that, the serial
  port is the console, both ways.
- install: the keyboard (US-English) and language (English) prompts, the
  menu's terminal type (xterm), then the curses installer: the whole disk
  (GPT, the installer's own layout), the host name NAME, the network
  "Automatically" (NWAM), UTC, root's password and a user "tester" with the
  same password, then Install.  The installer's keys: real function keys
  (xterm's F2 is ESC O Q) wherever an edit field may have the focus: its
  "Esc-2" form crashes the installer there (a TypeError in curses.ascii);
  arrows as the application-cursor keys (ESC O A/B) it switches to.  Then
  the menu's shell powers the guest off.
- first-boot: root is a role, so tester logs in on the console and su's;
  root becomes a normal account with PUBKEY_FILE's key, sshd lets root in
  by key, and the console stays on the serial port (/boot/conf.d/serial).
  Then it powers off.
Exits 0 when done, 1 with the reason on stderr.
"""
import os
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from term import Term  # noqa: E402

F2, F9 = b"\x1bOQ", b"\x1b[20~"
DOWN = b"\x1bOB"
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0B]")
KEYS = {" ": "spc", "\n": "ret", "=": "equal", "-": "minus", "_": "shift-minus"}


class Fail(Exception):
    pass


class Guest:
    def __init__(self, d):
        self.buf = bytearray()
        self.term = Term()
        self.lock = threading.Lock()
        self.log = open(os.path.join(d, "console.log"), "ab", buffering=0)
        self.con = self._connect(os.path.join(d, "con.sock"))
        self.mon = self._connect(os.path.join(d, "mon.sock"))
        self.eof = False
        threading.Thread(target=self._read, daemon=True).start()

    @staticmethod
    def _connect(path):
        for _ in range(100):
            try:
                s = socket.socket(socket.AF_UNIX)
                s.connect(path)
                return s
            except OSError:
                time.sleep(0.2)
        raise Fail(f"can't connect to {path}")

    def _read(self):
        while True:
            try:
                b = self.con.recv(4096)
            except OSError:
                b = b""
            if not b:
                self.eof = True
                return
            self.log.write(b)
            with self.lock:
                self.buf += b
                self.term.feed(b)

    def mark(self):
        with self.lock:
            return len(self.buf)

    def since(self, pos):
        with self.lock:
            return ANSI.sub(b"", bytes(self.buf[pos:])).decode(errors="replace")

    def screen(self):
        with self.lock:
            return self.term.text()

    def wait(self, what, pattern, timeout, pos=None, screen=False):
        """Until PATTERN (a regex) is in the output since POS (or on the
        screen); the installer's crash is a failure at once."""
        rx = re.compile(pattern, re.I | re.S)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            text = self.screen() if screen else self.since(pos or 0)
            if rx.search(text):
                return text
            seen = self.since(pos or 0)
            if "An unhandled exception occurred" in seen or "Installation Failed" in self.screen():
                raise Fail(f"the installer failed while waiting for {what}:\n{seen[-1500:]}")
            if self.eof:
                raise Fail(f"the guest stopped while waiting for {what}")
            time.sleep(0.5)
        raise Fail(f"no {what} in {timeout} s; the screen:\n{self.screen()}\nthe last output:\n{self.since(0)[-1500:]}")

    def send(self, data, pause=1.0):
        self.con.sendall(data if isinstance(data, bytes) else data.encode())
        time.sleep(pause)

    def sendkeys(self, text):
        self.mon.recv(65536) if self._ready(self.mon) else None
        for c in text:
            k = KEYS.get(c) or (f"shift-{c.lower()}" if c.isupper() else c)
            self.mon.sendall(f"sendkey {k}\n".encode())
            time.sleep(0.1)

    @staticmethod
    def _ready(s):
        import select
        return bool(select.select([s], [], [], 0)[0])


def loader(g):
    """Stop the loader's countdown and boot with the console on ttya."""
    g.wait("the loader's menu", r"Autoboot in \d", 300)
    g.sendkeys("3")
    g.wait("the loader's prompt", r"Type '\?' for a list of commands.*ok\s*$", 30)
    g.sendkeys("set console=ttya\nset os_console=ttya\n")
    time.sleep(1)
    pos = g.mark()
    g.sendkeys("boot\n")
    g.wait("the kernel's banner on the serial port", r"OpenIndiana Hipster .* 64-bit", 300, pos)


def on_screen(g, what, pattern, timeout=120):
    """PATTERN on the screen, which then settles a moment (curses draws in
    pieces) before a key goes in."""
    text = g.wait(what, pattern, timeout, screen=True)
    time.sleep(2)
    return text


def press(g, key, what, pattern, timeout=120):
    """KEY, then PATTERN on the screen.  A key the installer missed (it
    sometimes drops the first one a screen gets) leaves the screen as it
    was: sent again then, up to three times, but never once the screen has
    changed."""
    before = g.screen()
    end = time.monotonic() + timeout
    for attempt in range(4):
        g.send(key, 1)
        try:
            return on_screen(g, what, pattern, 20 if attempt < 3 else max(20, end - time.monotonic()))
        except Fail:
            if g.screen() != before:
                return on_screen(g, what, pattern, max(20, end - time.monotonic()))
    raise Fail(f"no {what}")


def install(g, name, password):
    loader(g)
    pos = g.mark()
    g.wait("the keyboard prompt", r"keyboard layout, enter a number", 600, pos)
    g.send("\r")
    g.wait("the language prompt", r"language you wish to use", 120, pos)
    pos = g.mark()
    g.send("\r")
    g.wait("the installation menu", r"Please enter a number \[1\]:", 300, pos)
    pos = g.mark()
    g.send("4\r")
    g.wait("the terminal type prompt", r"Enter terminal type", 30, pos)
    pos = g.mark()
    g.send("xterm\r")
    g.wait("the installation menu", r"currently xterm.*Please enter a number \[1\]:", 30, pos)
    g.send("1\r")
    # (Not the key legend: a console message (nwamd's) can scroll it away.)
    on_screen(g, "the welcome screen", r"How to navigate through this installer")
    press(g, F2, "the disks screen", r"Where should OpenIndiana be installed")
    press(g, F2, "the disk warning", r"Installing onto a.*Continue")
    g.send("\t")                    # Cancel -> Continue
    g.send("\r", 3)
    on_screen(g, "the partitions screen", r"Use the whole disk \(EFI\)")
    press(g, F2, "the network screen", r"Computer Name: openindiana")
    g.send(b"\x08" * len("openindiana"))
    g.send(name)
    on_screen(g, f"the host name {name}", rf"Computer Name: {re.escape(name)}\n", 20)
    g.send(DOWN)                    # the field -> "Automatically"
    press(g, F2, "the time zone screen", r"Time Zone: Regions.*UTC/GMT")
    press(g, F2, "the date and time screen", r"Date and Time")
    press(g, F2, "the users screen", r"Root password:")
    for field in (password, password, "Tester", "tester", password, password):
        g.send(field, 0.5)
        g.send(DOWN, 0.5)
    on_screen(g, "the users filled in", r"Username: +tester", 20)
    summary = press(g, F2, "the installation summary", r"Installation Summary.*Username: tester")
    for want in ("ZFS Pool name: rpool", "Time Zone: UTC", "Username: tester"):
        if want not in summary:
            raise Fail(f"the summary doesn't say {want!r}:\n{summary}")
    print(summary, file=sys.stderr)
    press(g, F2, "the installation under way", r"\A(?!.*Installation Summary)")  # any other screen
    on_screen(g, "the end of the installation", r"Installation Complete", 3600)
    pos = g.mark()
    g.send(b"\x1b9", 3)             # Quit (no edit field here: Esc-9 is safe)
    g.wait("the installation menu", r"Please enter a number \[1\]:", 60, pos)
    pos = g.mark()
    g.send("3\r", 3)
    g.wait("the shell", r"root@openindiana:.*#", 60, pos)
    g.send("grep -c 'Transfer completed' /tmp/install_log; sync; /usr/sbin/init 5\r")


def first_boot(g, name, password, key):
    loader(g)
    pos = g.mark()
    g.wait("the console login", r"console login:", 600, pos)
    g.send("tester\r", 2)
    g.wait("the password prompt", r"Password:", 30, pos)
    pos = g.mark()
    g.send(password + "\r", 4)
    g.wait("tester's shell", r"tester@\S+:~\$", 60, pos)
    pos = g.mark()
    g.send("su - root\r", 2)
    g.wait("su's password prompt", r"Password:", 30, pos)
    pos = g.mark()
    g.send(password + "\r", 4)
    g.wait("root's shell", r"root@\S+:~#", 60, pos)
    # Short lines: the console's line editing.
    steps = [
        "PATH=/usr/gnu/bin:/usr/sbin:/usr/bin:/sbin; export PATH",
        "rolemod -K type=normal root || usermod -K type=normal root",
        "mkdir -p -m 700 /root/.ssh",
        f"echo '{key}' >/root/.ssh/authorized_keys",
        "chmod 600 /root/.ssh/authorized_keys",
        "c=/etc/ssh/sshd_config",
        "sed -i '/^PermitRootLogin/d' $c; echo 'PermitRootLogin without-password' >>$c",
        "svcadm restart ssh",
        "mkdir -p /boot/conf.d; printf 'console=\"ttya,text\"\\nos_console=\"ttya\"\\n' >/boot/conf.d/serial",
        "echo FIRSTBOOT-$(hostname)-$(grep -c ssh-ed25519 /root/.ssh/authorized_keys)-OK",
    ]
    for s in steps:
        g.send(s + "\r", 1.5)
    g.wait("the first boot's marker", rf"FIRSTBOOT-{re.escape(name)}-1-OK", 60, pos)
    g.send("sync; /usr/sbin/init 5\r")


def main():
    if len(sys.argv) < 5 or sys.argv[1] not in ("install", "first-boot"):
        sys.exit(__doc__.split("Usage:")[1].split("\n\n")[0])
    mode, d, name, pwfile = sys.argv[1:5]
    password = open(pwfile).read().strip()
    g = Guest(d)
    try:
        if mode == "install":
            install(g, name, password)
        else:
            first_boot(g, name, password, open(sys.argv[5]).read().strip())
        # Powered off: QEMU closes the console.
        end = time.monotonic() + 300
        while not g.eof and time.monotonic() < end:
            time.sleep(1)
        if not g.eof:
            raise Fail("the guest didn't power off")
    except Fail as e:
        print(f"install.py {mode}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
