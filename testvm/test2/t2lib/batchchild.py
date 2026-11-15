"""A batch entry's child, wrapped (docs/design/batch.md §4.2, §6.2): run as a script by
t2lib/batch.py's spawn(), never imported.

    python3 -B batchchild.py ENTRY_DIR -- COMMAND...

It starts COMMAND (stdin /dev/null; stdout and stderr are the wrapper's, the entry's entry.log),
writes ENTRY_DIR/inner (the command's PID, for run discovery), passes each SIGINT, SIGTERM and
SIGHUP it gets on to the command (never a kill of its own), and when the command ends writes
ENTRY_DIR/exit ("RC EPOCH"; RC 128+N for a command a signal ended), created exclusively, so a
batch driver that was away when it ended still learns how it ended.  It exits with RC.
"""

import os
import signal
import subprocess
import sys
import time


def write_excl(path, text):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return True


def main(argv):
    if len(argv) < 3 or argv[1] != "--":
        sys.stderr.write("usage: batchchild.py ENTRY_DIR -- COMMAND...\n")
        return 2
    edir, cmd = argv[0], argv[2:]
    box = {}

    def fwd(signum, _frame):
        p = box.get("p")
        if p is not None and p.poll() is None:
            try:
                os.kill(p.pid, signum)
            except OSError:
                pass

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, fwd)
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
    except OSError as e:
        sys.stderr.write(f"batch: can't start {cmd[0]}: {e}\n")
        sys.stdout.flush()
        write_excl(os.path.join(edir, "exit"), f"127 {int(time.time())}\n")
        return 127
    box["p"] = p
    tmp = os.path.join(edir, f"inner.tmp.{os.getpid()}")
    with open(tmp, "w") as f:
        f.write(f"{p.pid}\n")
    os.replace(tmp, os.path.join(edir, "inner"))
    while True:
        try:
            rc = p.wait()
            break
        except InterruptedError:
            continue
    if rc < 0:
        rc = 128 - rc
    sys.stdout.flush()
    write_excl(os.path.join(edir, "exit"), f"{rc} {int(time.time())}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
