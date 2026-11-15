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

--passphrase FILE also types FILE's passphrase (without its final newline)
at each prompt for it, as console-boot.py does (its prompts; one key at a
time, a prompt within 5 s of the last typing being the same one), for a
menu that asks for one (ht1.sh's pick of another ZFSBootMenu boot
environment).  The prompt may come before PATTERN or after the first
chunk of KEYS: void-zbm-enc's ZFSBootMenu counts down first and asks for
the pool's passphrase only once its countdown ends, or once Escape opens
its menu.  So KEYS' first chunk goes when PATTERN shows (prompt or not),
and the rest only once the console has shown at least 256 bytes after
the last thing typed (that chunk or the passphrase: more than its echo)
and then been quiet for 3 s (the menu drawn), or after 60 s.  The log gets a line saying it was
typed, never the passphrase.  (The two tools can't be nested: each owns
the console.)

Usage: console-keys.py [--passphrase FILE] LOG PATTERN KEYS -- COMMAND ARGS...
"""
import codecs
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time

ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]")
# console-boot.py's PROMPT: the passphrase prompts it answers.
PROMPT = re.compile(rb"(?i)(please unlock disk [^\n]*:|geli passphrase for \S+|(?:^|\n)passphrase:|enter passphrase[^\n]*)\s*$")


def main() -> int:
    key = None
    if len(sys.argv) > 2 and sys.argv[1] == "--passphrase":
        with open(sys.argv[2], "rb") as f:
            key = f.read().rstrip(b"\n")
        del sys.argv[1:3]
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

    def console_passphrase() -> None:
        fd = proc.stdout.fileno()
        # buf: the console since the last typing (ANSI escapes removed), for
        # PATTERN and the prompts; last: when the passphrase was last typed;
        # stage: 0 before PATTERN, 1 once KEYS' first chunk went (the rest
        # waiting for the menu), 2 once all of KEYS went; acted: when the
        # last thing was typed; seen: how many bytes came after it; heard:
        # when the last did.
        buf, last, stage, acted, seen, heard = b"", 0.0, 0, 0.0, 0, 0.0
        with open(log_path, "wb") as log:
            def write(data: bytes, gap: float) -> bool:
                try:
                    if gap:
                        for b in data:
                            proc.stdin.write(bytes([b]))
                            proc.stdin.flush()
                            time.sleep(gap)
                    else:
                        proc.stdin.write(data)
                        proc.stdin.flush()
                except OSError:
                    return False
                return True
            while True:
                r, _, _ = select.select([fd], [], [], 0.2)
                now = time.monotonic()
                if r:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        return
                    log.write(chunk)
                    log.flush()
                    buf = (buf + ANSI.sub(b"", chunk))[-8192:]
                    seen, heard = seen + len(chunk), now
                    if PROMPT.search(buf) and now - last > 5:
                        time.sleep(1)
                        if not write(key + b"\r", 0.05):
                            return
                        buf, last = b"", time.monotonic()
                        acted, seen = last, 0
                        log.write(b"\n[console-keys.py: passphrase typed]\n")
                        continue
                    if stage == 0 and want.search(buf):
                        time.sleep(0.5)
                        if not write(chunks[0], 0):
                            return
                        stage, buf = 1, b""
                        acted, seen = time.monotonic(), 0
                        log.write(b"\n[console-keys.py: sent " + repr(keys.split("|")[0]).encode() + b", the rest once the menu is drawn]\n")
                        continue
                if stage == 1 and ((seen >= 256 and now - heard >= 3) or now - acted >= 60):
                    for k in chunks[1:]:
                        time.sleep(0.5)
                        if not write(k, 0):
                            return
                    stage = 2
                    log.write(b"\n[console-keys.py: typed " + repr(keys).encode() + b"]\n")
                    log.flush()

    t = threading.Thread(target=console if key is None else console_passphrase, daemon=True)
    t.start()
    rc = proc.wait()
    t.join(5)
    if stopped:
        os._exit(124)
    return rc


if __name__ == "__main__":
    sys.exit(main())
