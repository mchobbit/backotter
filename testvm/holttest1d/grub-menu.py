#!/usr/bin/env python3
"""Run a QEMU command whose serial console is its stdin and stdout
(-serial stdio), log the console to LOG, and walk GRUB's serial menu
through STEPs, one after another:

  pick:REGEX   move the highlight (Down, then Up) to the entry whose title
               matches REGEX (a Python regular expression) and press Enter
  down:K       press Down K times from where the highlight is (a submenu
               opens on its first entry), then Enter
  wait:REGEX   wait until REGEX shows on the console

Each step waits for its own output, at most 120 s; console text is matched
with ANSI escapes removed, the highlighted entry from GRUB's own drawing of
it (serial terminfo's reverse video, ESC[7m, or OVMF's console's black on
white).  It writes "[grub-menu.py: step N STEP done]" into LOG after each
step, "[grub-menu.py: all steps done]" after the last, or "[grub-menu.py:
step N STEP failed: WHY]" and types nothing more (GRUB then boots its
default when its timeout runs out).  Exits with the command's status.

ht1-tu.sh boots a transactional guest's older slots with it: "Start
bootloader from a read-only snapshot", the slot's place in that submenu,
and a wait for "bootable snapshot #N" in the slot's own menu, whose
default then boots (docs/design/aeon.md, M5).

Usage: grub-menu.py LOG STEP... -- COMMAND ARGS...
"""
import os
import re
import signal
import subprocess
import sys
import threading
import time

ANSI = re.compile(rb"\x1b\[[0-9;?=:]*[A-Za-z]|\x1b[()][A-Za-z0-9]|\x1b[78]|\x1b\][^\x1b\x07]*(\x07|\x1b\\)|\x1bP[^\x1b]*\x1b\\")
# The highlighted entry as GRUB draws it: serial terminfo's ESC[7m then the
# cursor to the entry's row, column 3; OVMF's text console's black on white.
HIGHLIGHT = re.compile(rb"(?:\x1b\[7m|\x1b\[30m\x1b\[47m)\x1b\[\d+;0?3H([^\x1b]*)")
# GRUB's menu takes v and ^ as Down and Up (its own help line says so):
# one byte each, where an arrow's escape sequence split by a busy host is
# read as Escape, which leaves a submenu (seen on Cinqcent, run aeon1c).
DOWN, UP, ENTER = b"v", b"^", b"\r"
STEP_S = 120


def main() -> int:
    args = sys.argv[1:]
    if "--" not in args or len(args) < 3:
        sys.exit(__doc__.strip().splitlines()[-1])
    i = args.index("--")
    log_path, steps, cmd = args[0], args[1:i], args[i + 1:]
    for s in steps:
        if not re.fullmatch(r"(pick|wait):.+|down:\d+", s):
            sys.exit(f"grub-menu.py: a STEP is pick:REGEX, down:K or wait:REGEX, not {s!r}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    stopped = []

    def stop(*_):
        stopped.append(True)
        proc.terminate()
        threading.Timer(60, proc.kill).start()
    signal.signal(signal.SIGTERM, stop)

    lock = threading.Condition()
    state = {"raw": b"", "text": b"", "hl": None, "seq": 0}
    log = open(log_path, "wb")

    def note(msg: str) -> None:
        with lock:
            log.write(f"\n[grub-menu.py: {msg}]\n".encode())
            log.flush()

    def console() -> None:
        while chunk := proc.stdout.read1(4096):
            with lock:
                log.write(chunk)
                log.flush()
                state["raw"] = (state["raw"] + chunk)[-65536:]
                state["text"] += ANSI.sub(b"", chunk)
                m = None
                for m in HIGHLIGHT.finditer(state["raw"]):
                    pass
                if m:
                    state["hl"] = m.group(1).decode("utf-8", "replace").strip().lstrip("*").strip()
                state["seq"] += 1
                lock.notify_all()
        with lock:
            state["seq"] = -1
            lock.notify_all()

    def until(pred, secs) -> bool:
        end = time.monotonic() + secs
        with lock:
            while not pred():
                left = end - time.monotonic()
                if left <= 0 or state["seq"] < 0:
                    return False
                lock.wait(left)
        return True

    def key(k: bytes) -> bool:
        try:
            proc.stdin.write(k)
            proc.stdin.flush()
        except OSError:
            return False
        time.sleep(0.4)
        return True

    def settle() -> None:
        """Until the console has been quiet for half a second (a redraw done)."""
        while True:
            with lock:
                s = state["seq"]
            time.sleep(0.5)
            with lock:
                if state["seq"] == s:
                    return

    def enter() -> bool:
        """Enter, with what was drawn so far forgotten: the next menu's own."""
        nonlocal mark
        with lock:
            state["hl"], state["raw"], mark = None, b"", len(state["text"])
        return key(ENTER)

    mark = 0

    def walk() -> None:
        for n, step in enumerate(steps, 1):
            kind, arg = step.split(":", 1)
            if kind == "wait":
                want = re.compile(arg.encode())
                if not until(lambda: want.search(state["text"][mark:]), STEP_S):
                    note(f"step {n} {step} failed: not shown in {STEP_S} s")
                    return
            elif kind == "down":
                if not until(lambda: state["hl"] is not None, STEP_S):
                    note(f"step {n} {step} failed: no menu in {STEP_S} s")
                    return
                settle()
                for _ in range(int(arg)):
                    if not key(DOWN):
                        return
                settle()
                if not enter():
                    return
            else:
                want = re.compile(arg)
                # The menu that has the entry, drawn (since the last step):
                # REGEX without its anchors, the menu's text being one run
                # of every entry (the cursor moves between them are gone).
                shown = re.compile(arg.removeprefix("^").removesuffix("$"))
                if not until(lambda: shown.search(state["text"][mark:].decode("utf-8", "replace")) and state["hl"] is not None, STEP_S):
                    note(f"step {n} {step} failed: no entry like it in {STEP_S} s")
                    return
                settle()
                found = False
                for k in [DOWN] * 40 + [UP] * 80:
                    with lock:
                        hl = state["hl"]
                    if hl is not None and want.search(hl):
                        found = True
                        break
                    if not key(k):
                        return
                    settle()
                if not found:
                    note(f"step {n} {step} failed: the highlight never reached it (last {state['hl']!r})")
                    return
                if not enter():
                    return
            note(f"step {n} {step} done")
        note("all steps done")

    threading.Thread(target=walk, daemon=True).start()
    t = threading.Thread(target=console, daemon=True)
    t.start()
    rc = proc.wait()
    t.join(5)
    if stopped:
        os._exit(124)
    return rc


if __name__ == "__main__":
    sys.exit(main())
