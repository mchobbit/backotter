#!/usr/bin/env python3
"""Run a QEMU command whose serial console is its stdin and stdout
(vm-run without --serial-log: -nographic), log the console to LOG, and type
the Ubuntu Core recovery key in KEY_FILE (the dashed form, without its
final newline) at each "Please enter the recovery key for disk ..." prompt
(snap-bootstrap's, through systemd-ask-password).  It types one key at a
time.  It never writes the key to LOG: each typing is logged as
"[type-recovery-key.py: typed the key, N]".  Exits with the command's
status.  As console-boot.py, which knows the other systems' prompts.

Usage: type-recovery-key.py LOG KEY_FILE -- COMMAND ARGS...
"""
import os
import re
import signal
import subprocess
import sys
import threading
import time

PROMPT = re.compile(rb"(?i)please enter the recovery key for[^\n]*$")
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]")


def main() -> int:
    if len(sys.argv) < 5 or sys.argv[3] != "--":
        sys.exit(__doc__.strip().splitlines()[-1])
    log_path, key_path, cmd = sys.argv[1], sys.argv[2], sys.argv[4:]
    with open(key_path, "rb") as f:
        key = f.read().strip()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    stopped = []

    def stop(*_):
        stopped.append(True)
        proc.terminate()
        threading.Timer(60, proc.kill).start()
    signal.signal(signal.SIGTERM, stop)

    def console() -> None:
        buf, count, last = b"", 0, 0.0
        with open(log_path, "wb") as log:
            while chunk := proc.stdout.read1(4096):
                log.write(chunk)
                log.flush()
                buf = (buf + ANSI.sub(b"", chunk))[-4096:]
                if PROMPT.search(buf.rstrip()):
                    if time.monotonic() - last < 5:
                        continue
                    time.sleep(1)
                    try:
                        for b in key + b"\r":
                            proc.stdin.write(bytes([b]))
                            proc.stdin.flush()
                            time.sleep(0.03)
                    except OSError:
                        return
                    count += 1
                    last = time.monotonic()
                    buf = b""
                    log.write(f"\n[type-recovery-key.py: typed the key, {count}]\n".encode())
                    log.flush()

    t = threading.Thread(target=console, daemon=True)
    t.start()
    rc = proc.wait()
    t.join(5)
    if stopped:
        os._exit(124)
    return rc


if __name__ == "__main__":
    sys.exit(main())
