#!/usr/bin/env python3
"""Run a QEMU command whose serial console is its stdin and stdout
(-serial stdio), log the console to LOG, and the first time PATTERN (a
Python regular expression, matched against the console's last 8 KiB with
ANSI escapes removed) shows, type KEYS: chunks separated by "|", each one
written at once (so an escape sequence such as \\x1b[A, the Up arrow, arrives
whole), half a second apart.  KEYS takes Python's string escapes (\\r, \\x1b).
Exits with the command's status.

make-vanilla.sh uses it to pick "Previous State" on GRUB's serial menu:
the master grub.cfg reads its default literally, so grub-reboot would do
nothing.

Usage: console-keys.py LOG PATTERN KEYS -- COMMAND ARGS...
"""
import codecs
import os
import re
import signal
import subprocess
import sys
import threading
import time

ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]")


def main() -> int:
    if len(sys.argv) < 6 or sys.argv[4] != "--":
        sys.exit(__doc__.strip().splitlines()[-1])
    log_path, pattern, keys, cmd = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[5:]
    want = re.compile(pattern.encode())
    chunks = [codecs.decode(k, "unicode_escape").encode("latin-1") for k in keys.split("|")]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    # Stopped (timeout(1), a TERM): the command too, as console-boot.py does.
    stopped = []
    def stop(*_):
        stopped.append(True)
        proc.terminate()
        threading.Timer(60, proc.kill).start()
    signal.signal(signal.SIGTERM, stop)

    def console() -> None:
        buf, typed = b"", False
        with open(log_path, "wb") as log:
            while chunk := proc.stdout.read1(4096):
                log.write(chunk)
                log.flush()
                if typed:
                    continue
                buf = (buf + ANSI.sub(b"", chunk))[-8192:]
                if want.search(buf):
                    typed = True
                    time.sleep(0.5)
                    try:
                        for k in chunks:
                            proc.stdin.write(k)
                            proc.stdin.flush()
                            time.sleep(0.5)
                    except OSError:
                        return
                    log.write(b"\n[console-keys.py: typed " + repr(keys).encode() + b"]\n")

    t = threading.Thread(target=console, daemon=True)
    t.start()
    rc = proc.wait()
    t.join(5)
    if stopped:
        os._exit(124)
    return rc


if __name__ == "__main__":
    sys.exit(main())
