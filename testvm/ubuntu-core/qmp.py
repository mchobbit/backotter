#!/usr/bin/env python3
"""A few QMP commands on a QEMU's QMP socket (QEMU started with
-qmp unix:SOCKET,server=on,wait=off), for what the serial console can't
show: Ubuntu Core asks for its recovery key on /dev/console, which is tty1
(the screen) because the gadget's command line names console=tty1 last.

Usage: qmp.py SOCKET screendump FILE.png
       qmp.py SOCKET type KEY_FILE      the file's first line, then Enter, on
                                        the keyboard (send-key), never printed
       qmp.py SOCKET key QCODE...       these keys, one after another (esc, down, ret)
"""
import json
import socket
import sys
import time

KEYS = {"-": "minus", " ": "spc", ".": "dot", "\n": "ret"}


def session(path: str):
    s = socket.socket(socket.AF_UNIX)
    s.connect(path)
    f = s.makefile("rw")
    json.loads(f.readline())                       # the greeting

    def cmd(name: str, **args) -> dict:
        f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        f.flush()
        while True:
            r = json.loads(f.readline())
            if "return" in r or "error" in r:
                if "error" in r:
                    raise SystemExit(f"qmp.py: {name}: {r['error'].get('desc')}")
                return r["return"]
    cmd("qmp_capabilities")
    return cmd


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[2] not in ("screendump", "type", "key"):
        sys.exit(__doc__.strip().split("\n\n")[-1])
    cmd = session(sys.argv[1])
    if sys.argv[2] == "key":
        for key in sys.argv[3:]:
            cmd("send-key", keys=[{"type": "qcode", "data": key}])
            time.sleep(0.1)
        return 0
    if sys.argv[2] == "screendump":
        cmd("screendump", filename=sys.argv[3], format="png")
        return 0
    with open(sys.argv[3]) as f:
        text = f.readline().strip()
    for ch in text + "\n":
        key = KEYS.get(ch, ch)
        if not (len(key) > 1 or key.isalnum()):
            raise SystemExit("qmp.py: a character it has no key for")
        cmd("send-key", keys=[{"type": "qcode", "data": key}])
        time.sleep(0.05)
    print("qmp.py: typed", len(text), "keys and Enter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
