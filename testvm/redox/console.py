#!/usr/bin/env python3
"""Drive the Redox guest's serial console, a QEMU chardev socket (server=on),
as root: Redox's sshd serves no logins yet, so this is the way in.

Usage: console.py SOCKET PASSWORD_FILE login [SECONDS]
       console.py SOCKET PASSWORD_FILE run [--timeout SECONDS] COMMAND
       console.py SOCKET PASSWORD_FILE send COMMAND

  login  waits up to SECONDS (default 300) for the login prompt, then logs in
         as root with the password in PASSWORD_FILE (make-redox.sh's
         root-password); exits 0 once root's shell answers
  run    runs COMMAND in root's shell (logging in first if it isn't), its
         stdin /dev/null; prints what it printed and exits with its status,
         or 124 if it takes more than SECONDS (default 120)
  send   types COMMAND and returns at once, e.g. poweroff

The shell stays logged in between calls (the socket is QEMU's, the session
the guest's).  Markers around each command tell its output from the
console's echo and from what the guest logs to the console meanwhile (sshd
does, at every connection), so that noise can land inside the output; \\r and
terminal escapes are taken out.  BKO_CONSOLE_TRACE=1 copies everything read
to stderr.
"""

import os
import re
import socket
import sys
import time

ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]|\x1b[78=>]")
TRACE = os.environ.get("BKO_CONSOLE_TRACE") == "1"


class Console:
    def __init__(self, path: str):
        self.s = socket.socket(socket.AF_UNIX)
        end = time.monotonic() + 60     # QEMU may not have made it yet
        while True:
            try:
                self.s.connect(path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= end:
                    raise SystemExit(f"console.py: no console at {path}")
                time.sleep(1)
        self.s.settimeout(0.3)
        self.buf = b""

    def read(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                continue
            if not d:
                raise SystemExit("console.py: the console closed (QEMU gone?)")
            if TRACE:
                sys.stderr.buffer.write(d)
                sys.stderr.flush()
            self.buf += d

    def text(self) -> str:
        return ANSI.sub(b"", self.buf).replace(b"\r", b"").decode(errors="replace")

    def until(self, pattern: str, seconds: float):
        end = time.monotonic() + seconds
        while True:
            m = re.search(pattern, self.text())
            if m or time.monotonic() >= end:
                return m
            self.read(0.5)

    def type(self, line: str) -> None:
        self.s.sendall(line.encode() + b"\r")

    def last_line(self) -> str:
        lines = [l for l in self.text().split("\n") if l.strip()]
        return lines[-1] if lines else ""

    def state(self, seconds: float) -> str:
        """'login' at the login prompt, 'shell' at a shell's, after up to
        SECONDS of pressing return."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.buf = b""
            self.type("")
            self.read(2)
            last = self.last_line()
            if re.search(r"login:\s*$", last):
                return "login"
            if re.search(r"[#$]\s*$", last):
                return "shell"
        return "none"

    def login(self, password: str, seconds: float) -> None:
        state = self.state(seconds)
        if state == "none":
            raise SystemExit(f"console.py: no login prompt or shell within {seconds:.0f} s")
        if state == "login":
            self.buf = b""
            self.type("root")
            if not self.until(r"[Pp]assword:?\s*$", 30):
                raise SystemExit("console.py: no password prompt after root")
            self.buf = b""
            self.type(password)
            # What's typed before the shell is up gets lost: its prompt
            # first (or 20 s, should what the guest logs come after it).
            self.until(r"[#$]\s*$", 20)
            if re.search(r"[Ll]ogin incorrect|login:\s*$", self.text()):
                raise SystemExit("console.py: root's login failed: " + self.last_line()[-200:])
        # Whatever the prompt looks like: root's shell must run this.
        for _ in range(3):
            self.buf = b""
            self.type("printf '@@B%s\\n' KO-READY")
            if self.until(r"@@BKO-READY\n", 10):
                self.read(0.5)
                return
        raise SystemExit("console.py: the login didn't give a shell: " + self.last_line()[-200:])

    def run(self, command: str, seconds: float) -> int:
        self.buf = b""
        # One line: the console echoes it, and printf's %s keeps the
        # markers out of the echo.
        self.type(f"printf '@@B%s\\n' KO-START; ( {command} ) </dev/null; printf '@@B%s %s\\n' KO-END $?")
        m = self.until(r"(?s)@@BKO-START\n(.*?)@@BKO-END (\d+)\n", seconds)
        if not m:
            sys.stdout.write(self.text().split("@@BKO-START\n", 1)[-1])
            print(f"console.py: no end of the command within {seconds:.0f} s", file=sys.stderr)
            return 124
        sys.stdout.write(m.group(1))
        return int(m.group(2))


def main() -> int:
    a = sys.argv[1:]
    if len(a) < 3 or a[2] not in ("login", "run", "send"):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    sock, pw_file, action, rest = a[0], a[1], a[2], a[3:]
    password = open(pw_file).read().strip()
    c = Console(sock)
    if action == "login":
        c.login(password, float(rest[0]) if rest else 300)
        return 0
    timeout = 120.0
    if action == "run" and rest[:1] == ["--timeout"]:
        timeout, rest = float(rest[1]), rest[2:]
    if len(rest) != 1:
        print("console.py: one COMMAND", file=sys.stderr)
        return 2
    c.login(password, 60)
    if action == "send":
        c.type(rest[0])
        try:
            c.read(1)
        except SystemExit:      # gone at once: poweroff can be that quick
            pass
        return 0
    return c.run(rest[0], timeout)


if __name__ == "__main__":
    sys.exit(main())
