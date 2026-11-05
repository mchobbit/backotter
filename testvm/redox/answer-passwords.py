#!/usr/bin/env python3
"""Run redox_installer on a pseudo-terminal and type the password at its
password prompts, so that the password stays out of its config: the
installer copies its whole config into the image (/filesystem.toml) before
the packages, and an overwrite after them leaves those old blocks, in the
clear, in RedoxFS's free space.

Usage: answer-passwords.py PASSWORD_FILE SECONDS COMMAND [ARG...]

The installer asks twice (enter, confirm) for each [users.X] without a
password, as general.prompt is on.  Its prompts aren't flushed before it
reads (print! without a newline), so this goes by the terminal instead:
termion's read_passwd puts it into raw mode (no echo, not canonical) while
it reads, and each time it does, after new output, this types the first
line of PASSWORD_FILE and a return.  Never in cooked mode, where the
terminal would echo it into the log.  What COMMAND prints goes to stdout,
then a line saying how many answers were typed; the exit status is
COMMAND's, or 124 after SECONDS.
"""

import os
import pty
import select
import signal
import sys
import termios
import time


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    password = open(sys.argv[1], "rb").read().split(b"\n")[0]
    if not password:
        print("answer-passwords.py: the password file is empty", file=sys.stderr)
        return 2
    deadline = time.monotonic() + float(sys.argv[2])
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(sys.argv[3], sys.argv[3:])
    out = sys.stdout.buffer
    answers, fresh = 0, True        # fresh: output since the last answer
    while True:
        if time.monotonic() > deadline:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
            print(f"\nanswer-passwords.py: timed out ({answers} answers typed)", flush=True)
            return 124
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:         # EIO: the child closed the terminal
                d = b""
            if not d:
                break
            out.write(d)
            out.flush()
            fresh = True
        try:
            lflag = termios.tcgetattr(fd)[3]
        except termios.error:
            continue
        if fresh and not lflag & (termios.ICANON | termios.ECHO):
            os.write(fd, password + b"\r")
            answers, fresh = answers + 1, False
    _, status = os.waitpid(pid, 0)
    print(f"answer-passwords.py: {answers} answers typed", flush=True)
    code = os.waitstatus_to_exitcode(status)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
