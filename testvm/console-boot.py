#!/usr/bin/env python3
"""Run a QEMU command whose serial console is its stdin and stdout
(-serial stdio), log the console to LOG, and type the passphrase in
PASSPHRASE_FILE (without its final newline) at each prompt for it: Debian's
"Please unlock disk NAME:" from cryptsetup-initramfs, FreeBSD's loader's
"GELI Passphrase for DEVICE ", OpenBSD's boot loader's "Passphrase: "
(softraid CRYPTO) at the start of a line, or "Enter passphrase".  It types
one key at a time, and one Enter more when typing got no output for 30 s.
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

PROMPT = re.compile(rb"(?i)(please unlock disk [^\n]*:|geli passphrase for \S+|(?:^|\n)passphrase:|enter passphrase[^\n]*)\s*$")
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
NUDGE_S = 30


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

    # Keys typed into a BIOS loader can still be lost (the keyboard buffer
    # takes 15; a host too busy to run the guest for a while hands it a
    # burst): when typing got no output at all for NUDGE_S, one Enter more,
    # so that a loader still waiting at the prompt takes what it has, finds
    # it wrong and asks again, and the passphrase is typed again.  (FreeBSD's
    # loader prints "Calculating GELI Decryption Key" at once.)
    lock = threading.Lock()
    # at: when the passphrase was last typed with no output since; last:
    # when it was last typed at all (a prompt within 5 s of that is the same
    # one, still on its way out), forgotten by a nudge, whose prompt is new.
    typed = {"at": None, "last": 0.0, "nudges": 0}

    def send(data: bytes, gap: float) -> bool:
        try:
            with lock:
                for b in data:
                    proc.stdin.write(bytes([b]))
                    proc.stdin.flush()
                    time.sleep(gap)
        except OSError:
            return False
        return True

    def nudge() -> None:
        while proc.poll() is None:
            time.sleep(1)
            at = typed["at"]
            if at is not None and time.monotonic() - at > NUDGE_S and typed["nudges"] < 3:
                typed["at"], typed["last"] = None, 0.0
                typed["nudges"] += 1
                if not send(b"\r", 0):
                    return

    def console() -> None:
        buf = b""
        with open(log_path, "wb") as log:
            while chunk := proc.stdout.read1(4096):
                typed["at"] = None
                log.write(chunk)
                log.flush()
                buf = (buf + ANSI.sub(b"", chunk))[-4096:]
                if PROMPT.search(buf) and time.monotonic() - typed["last"] > 5:
                    time.sleep(1)
                    # One key at a time: OpenBSD's /boot and FreeBSD's BIOS
                    # loader read the BIOS keyboard buffer, which holds only
                    # 15 keys (all at once, the Enter was lost there).
                    if not send(key + b"\r", 0.05):
                        return
                    buf = b""
                    typed["at"] = typed["last"] = time.monotonic()

    threading.Thread(target=nudge, daemon=True).start()
    t = threading.Thread(target=console, daemon=True)
    t.start()
    rc = proc.wait()
    t.join(5)
    if stopped:
        os._exit(124)       # not waiting for the KILL timer
    return rc


if __name__ == "__main__":
    sys.exit(main())
