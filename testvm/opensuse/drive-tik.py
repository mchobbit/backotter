#!/usr/bin/env python3
"""Run tik, Aeon's installer, over SSH and answer its dialogs.

Usage: drive-tik.py LOG KEY_FILE TIMEOUT -- SSH_COMMAND...

With no Wayland or X session, tik asks through cenity on its terminal
(usr/lib/tik/lib/cenity): an info or warning waits for "Press any key to
...", a question is bash's `select` ("1) Yes", "2) No", then "#? "), a list
"Please select a item number", a password `read -s`.  This runs
SSH_COMMAND (ssh -tt ... tik: a terminal, which cenity's progress dialogs
need for their /dev/tty) on a pty of its own and answers: any key with
Enter, every question with 1 (Yes: "Begin Installation?"), except "Do you
really want to quit?", which a cancelled dialog asks, with 2; and after 300 s
with nothing written, Enter (up to three times), for a dialog shown inside a
command substitution, whose prompt nobody sees.  A password
or a disk list means tik isn't doing what make-opensuse.sh set up (Fallback
mode: no TPM 2.0; more than one disk), and fails the run.

The recovery key that tik shows once (modhex, eight groups of eight) goes
to KEY_FILE (mode 600) and never into LOG, which gets everything tik wrote
with the key, and the QR code of it, replaced.  Exit status 0 once tik said
"Installation Complete!" and the key was read (tik then reboots, which ends
the SSH session), 1 otherwise, 2 on TIMEOUT seconds.
"""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import sys
import time

KEY_RE = re.compile(rb"\b(?:[cbdefghijklnrtuv]{8}-){7}[cbdefghijklnrtuv]{8}\b")
ANSI_RE = re.compile(rb"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|[@-Z\\-_]|c)")
# qrencode -t UTF8i: lines of block elements (U+2580-U+259F) and spaces.
IDLE = 300
QR_RE = re.compile("^[▀-▟ ]*[▀-▟][▀-▟ ]*$", re.M)


def redact(text: bytes, key: bytes | None) -> bytes:
    if key:
        text = text.replace(key, b"<recovery key: in recovery-key>")
    t = text.decode("utf-8", "replace")
    return QR_RE.sub("<QR code of the recovery key>", t).encode()


def main() -> int:
    log, key_file, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
    if sys.argv[4] != "--":
        sys.exit("usage: drive-tik.py LOG KEY_FILE TIMEOUT -- SSH_COMMAND...")
    cmd = sys.argv[5:]
    master, slave = pty.openpty()
    proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    raw = b""
    key: bytes | None = None
    answered = {"any": 0, "question": 0, "quit": 0}
    failure = ""
    deadline = time.monotonic() + timeout
    last_write = 0.0
    last_output = time.monotonic()
    nudges = 0

    def save() -> None:
        with open(log, "wb") as f:
            f.write(redact(raw, key))

    def send(what: bytes, why: str) -> None:
        print(f"drive-tik: {why}: sending {what!r}", file=sys.stderr)
        try:
            os.write(master, what)
        except OSError:
            pass

    while True:
        if time.monotonic() > deadline:
            failure = f"timed out after {timeout:.0f} s"
            proc.kill()
            break
        ready, _, _ = select.select([master], [], [], 1.0)
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            raw += chunk
            last_output = time.monotonic()
        elif proc.poll() is not None:
            break
        text = ANSI_RE.sub(b"", raw).replace(b"\r", b"")
        if key is None:
            m = KEY_RE.search(text)
            if m:
                key = m.group(0)
                fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.write(fd, key + b"\n")
                os.close(fd)
                os.chmod(key_file, 0o600)
                print(f"drive-tik: the recovery key is in {key_file}", file=sys.stderr)
        if b"--PASSWORD--" in text:
            failure = "tik asked for a passphrase (Fallback mode: did it find no TPM 2.0?)"
            proc.kill()
            break
        if b"Please select a item number" in text:
            failure = "tik asked which disk or image to use"
            proc.kill()
            break
        # Each new prompt once: a key sent before cenity's read starts waits
        # in the pty's input, which the read then takes.
        n = text.count(b"Press any key to")
        if n > answered["any"]:
            answered["any"] = n
            send(b"\r", "any key")
        n = text.count(b"#?")
        if n > answered["question"] + answered["quit"]:
            if b"Do you really want to quit?" in text[text.rfind(b"--QUESTION--"):]:
                answered["quit"] += 1
                send(b"2\r", "question: really quit? no")
            else:
                answered["question"] += 1
                send(b"1\r", "question: yes")
        # A dialog tik shows inside a $(...) (warn() in a function whose
        # output is captured) prints its prompt where nobody sees it, and
        # waits: after IDLE seconds with nothing written, Enter, a few times.
        if time.monotonic() - last_output > IDLE and nudges < 3:
            nudges += 1
            last_output = time.monotonic()
            send(b"\r", f"nothing written for {IDLE} s (a hidden dialog?)")
        if time.monotonic() - last_write > 5:
            save()
            last_write = time.monotonic()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    os.close(master)
    save()
    text = ANSI_RE.sub(b"", raw)
    if failure:
        print(f"drive-tik: {failure}; see {log}", file=sys.stderr)
        return 2 if failure.startswith("timed out") else 1
    if b"--ERROR--" in text:
        print(f"drive-tik: tik reported an error; see {log}", file=sys.stderr)
        return 1
    if b"Installation Complete!" not in text:
        print(f"drive-tik: tik ended without \"Installation Complete!\"; see {log}", file=sys.stderr)
        return 1
    if key is None:
        print(f"drive-tik: tik showed no recovery key; see {log}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
