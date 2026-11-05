#!/usr/bin/env python3
"""Run a QEMU command whose serial console is its stdin and stdout
(-serial stdio), log the console to LOG, and type the passphrase in
PASSPHRASE_FILE (without its final newline) at each prompt for it: Debian's
"Please unlock disk NAME:" from cryptsetup-initramfs, FreeBSD's loader's
"GELI Passphrase for DEVICE ", or "Enter passphrase".
Exits with the command's status.

Usage: console-boot.py LOG PASSPHRASE_FILE -- COMMAND ARGS...
"""
import os
import re
import signal
import subprocess
import sys
import threading
import time

PROMPT = re.compile(rb"(?i)(please unlock disk [^\n]*:|geli passphrase for \S+|enter passphrase[^\n]*)\s*$")
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")


def main() -> int:
    if len(sys.argv) < 5 or sys.argv[3] != "--":
        sys.exit(__doc__.strip().splitlines()[-1])
    log_path, key_path, cmd = sys.argv[1], sys.argv[2], sys.argv[4:]
    with open(key_path, "rb") as f:
        key = f.read().rstrip(b"\n")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    # Stopped (timeout(1), vm_halt): the command too, with TERM, which
    # vm-run passes on to its QEMU, and KILL if it is still there after
    # 60 s.  The main thread's wait below reaps it (a wait in the handler
    # would block on that one's lock).
    stopped = []
    def stop(*_):
        stopped.append(True)
        proc.terminate()
        threading.Timer(60, proc.kill).start()
    signal.signal(signal.SIGTERM, stop)

    def console() -> None:
        buf, last = b"", 0.0
        with open(log_path, "wb") as log:
            while chunk := proc.stdout.read1(4096):
                log.write(chunk)
                log.flush()
                buf = (buf + ANSI.sub(b"", chunk))[-4096:]
                if PROMPT.search(buf) and time.monotonic() - last > 5:
                    time.sleep(1)
                    try:
                        proc.stdin.write(key + b"\r")
                        proc.stdin.flush()
                    except OSError:
                        return
                    buf, last = b"", time.monotonic()

    t = threading.Thread(target=console, daemon=True)
    t.start()
    rc = proc.wait()
    t.join(5)
    if stopped:
        os._exit(124)       # not waiting for the KILL timer
    return rc


if __name__ == "__main__":
    sys.exit(main())
