#!/usr/bin/env python3
"""Run a QEMU command whose serial console is its stdout (-serial stdio) and
whose human monitor is the unix socket MONITOR (-monitor
unix:MONITOR,server,nowait), log the console to LOG, and run COMMAND_FILE's
one line as root on the guest's virtual terminal tty3, typed with the
monitor's sendkey.

Guix's installation image has a root login with no password on tty3-tty6
(mingetty), the installer on tty1, and nothing on the serial console: no
getty there, and no way to run a command at boot.  So, once the serial
console shows READY (a regex) and SETTLE_S seconds more have passed: switch
to tty3, type "root" and Enter (then one Enter more, should it ask for the
empty password), and a check that echoes a marker to /dev/ttyS0.  If the
marker doesn't show on the serial console within 15 s, the getty wasn't up
yet: again, up to 8 times.  Then type the line, which should send its own
output to /dev/ttyS0.  After that, only the log.

Exits with the command's status, 3 if READY never showed within
READY_TIMEOUT seconds or the login never worked.

Usage: console-type.py LOG MONITOR COMMAND_FILE READY -- COMMAND ARGS...
"""
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
SETTLE_S = float(os.environ.get("SETTLE_S", "20"))
READY_TIMEOUT = float(os.environ.get("READY_TIMEOUT", "600"))

# The US keyboard: each character as sendkey's key name, with shift- when
# it takes Shift.
PLAIN = {" ": "spc", "\n": "ret", "\t": "tab", "-": "minus", "=": "equal", "/": "slash", ".": "dot",
         ",": "comma", ";": "semicolon", "'": "apostrophe", "\\": "backslash", "[": "bracket_left",
         "]": "bracket_right", "`": "grave_accent"}
SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9",
           ")": "0", "_": "minus", "+": "equal", "?": "slash", ">": "dot", "<": "comma", ":": "semicolon",
           '"': "apostrophe", "|": "backslash", "{": "bracket_left", "}": "bracket_right", "~": "grave_accent"}


def key_name(c: str) -> str:
    if c.isascii() and (c.islower() or c.isdigit()):
        return c
    if c.isascii() and c.isupper():
        return "shift-" + c.lower()
    if c in PLAIN:
        return PLAIN[c]
    if c in SHIFTED:
        return "shift-" + SHIFTED[c]
    raise ValueError(f"can't type {c!r}")


class Monitor:
    def __init__(self, path: str) -> None:
        end = time.monotonic() + 60
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX)
                self.s.connect(path)
                break
            except OSError:
                if time.monotonic() > end:
                    raise
                time.sleep(0.5)
        self.s.settimeout(0.2)
        self.drain()

    def drain(self) -> None:
        try:
            while self.s.recv(65536):
                pass
        except OSError:
            pass

    def cmd(self, line: str) -> None:
        self.s.sendall(line.encode() + b"\n")
        time.sleep(0.03)
        self.drain()

    def type(self, text: str) -> None:
        for c in text:
            self.cmd("sendkey " + key_name(c))


def main() -> int:
    if len(sys.argv) < 7 or sys.argv[5] != "--":
        sys.exit(__doc__.strip().splitlines()[-1])
    log_path, mon_path, cmd_path, ready = sys.argv[1], sys.argv[2], sys.argv[3], re.compile(sys.argv[4].encode())
    qemu = sys.argv[6:]
    with open(cmd_path) as f:
        line = f.read().strip("\n")
    for c in line:
        key_name(c)                 # fails now, not halfway through typing
    proc = subprocess.Popen(qemu, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)

    def stop(*_):
        proc.terminate()
        threading.Timer(60, proc.kill).start()
    signal.signal(signal.SIGTERM, stop)

    seen = {"buf": b"", "ready": None}
    lock = threading.Lock()

    def console() -> None:
        with open(log_path, "wb") as log:
            while chunk := proc.stdout.read1(4096):
                log.write(chunk)
                log.flush()
                with lock:
                    seen["buf"] = (seen["buf"] + ANSI.sub(b"", chunk))[-65536:]
                    if seen["ready"] is None and ready.search(seen["buf"]):
                        seen["ready"] = time.monotonic()

    def has(marker: bytes) -> bool:
        with lock:
            return marker in seen["buf"]

    failed = []

    def drive() -> None:
        end = time.monotonic() + READY_TIMEOUT
        while seen["ready"] is None:
            if proc.poll() is not None:
                return
            if time.monotonic() > end:
                sys.stderr.write("console-type: READY never showed\n")
                failed.append(1)
                stop()
                return
            time.sleep(1)
        time.sleep(SETTLE_S)
        mon = Monitor(mon_path)
        for attempt in range(1, 9):
            # kmscon holds tty1; either chord reaches the kernel's VT switch.
            mon.cmd("sendkey ctrl-alt-f3")
            mon.cmd("sendkey alt-f3")
            time.sleep(1)
            mon.type("root\n")
            time.sleep(3)
            mon.type("\n")
            time.sleep(1)
            # The marker is computed, so the typed line itself can't match.
            mon.type(f"echo BKO-TTY-OK-$(( {attempt} * 1000 + 7 )) >/dev/ttyS0\n")
            marker = f"BKO-TTY-OK-{attempt * 1000 + 7}".encode()
            for _ in range(15):
                if has(marker):
                    sys.stderr.write(f"console-type: logged in on tty3 (attempt {attempt})\n")
                    mon.type(line + "\n")
                    return
                time.sleep(1)
        sys.stderr.write("console-type: the root login on tty3 never answered\n")
        failed.append(1)
        stop()

    t = threading.Thread(target=console, daemon=True)
    t.start()
    threading.Thread(target=drive, daemon=True).start()
    rc = proc.wait()
    t.join(5)
    if failed:
        os._exit(3)
    return rc


if __name__ == "__main__":
    sys.exit(main())
