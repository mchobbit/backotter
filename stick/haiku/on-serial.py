#!/bin/python3
"""BackOtter rescue stick, Haiku: the console on a serial port.

Usage: on-serial.py TTY COMMAND...

Haiku's serial ports stay raw: tcsetattr succeeds on them but changes
nothing (no echo, no line editing, no ^C, no CR/LF translation), and they can
be open only once at a time.  So COMMAND runs on a pseudo-terminal, which
has all of that (and is its controlling terminal, so ^C and job control
work), and this copies bytes between the pseudo-terminal and TTY, opened
once.  COMMAND starts again a second after it exits, as getty would start
the console again.  haiku/boot runs this at every boot.
"""

import os
import pty
import select
import sys
import time


def serve(port: int, argv: list[str]) -> None:
    """Run argv on a pseudo-terminal, bridged to port, until it exits."""
    pid, master = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "vt100"
        os.execv(argv[0], argv)
    try:
        while True:
            ready, _, _ = select.select([port, master], [], [])
            if port in ready:
                data = os.read(port, 4096)
                if data:
                    os.write(master, data)
            if master in ready:
                try:
                    data = os.read(master, 4096)
                except OSError:      # the child's side is closed
                    break
                if not data:
                    break
                os.write(port, data)
    finally:
        os.close(master)
        os.waitpid(pid, 0)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    port = os.open(sys.argv[1], os.O_RDWR | os.O_NOCTTY)
    while True:
        serve(port, sys.argv[2:])
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
