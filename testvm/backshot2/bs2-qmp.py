#!/usr/bin/env python3
"""QMP for ht1-bs2.sh's saved rescuer (README "What a case reuses"): stdlib only.

  bs2-qmp.py SOCK save FILE [TIMEOUT]       stop the VM and write its state to FILE
                                            (migrate file:), waiting till that is done
  bs2-qmp.py SOCK resume [TIMEOUT]          wait for -incoming's state to load, then cont
  bs2-qmp.py SOCK hotplug NODE FILE DEV [BUS]
                                            FILE (qcow2) as block node NODE, then a
                                            virtio-blk-pci DEV on it (on BUS, a PCIe root
                                            port; none: the PC machine's bus)
  bs2-qmp.py SOCK unplug TIMEOUT DEV...     device_del each DEV, waiting till QEMU
                                            reports each gone (the guest let go)
  bs2-qmp.py SOCK quit

Exit 0 done, 1 not (why on stderr).  SOCK: QEMU's -qmp unix:SOCK,server=on,wait=off.
"""

import json
import socket
import sys
import time


class Fail(Exception):
    pass


class QMP:
    def __init__(self, path, wait=30):
        end = time.time() + wait
        while True:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(path)
                break
            except OSError as e:
                s.close()
                if time.time() > end:
                    raise Fail(f"no QMP at {path}: {e}")
                time.sleep(0.1)
        # (Loading a state from a file keeps QEMU's main loop busy: QMP answers after.)
        s.settimeout(180)
        self.s, self.r = s, s.makefile("rb")
        self.read()   # the greeting
        self.cmd("qmp_capabilities")

    def read(self):
        line = self.r.readline()
        if not line:
            raise Fail("QEMU closed QMP (it exited: see the serial log and its .vm-run)")
        return json.loads(line)

    def cmd(self, name, **args):
        msg = {"execute": name}
        if args:
            msg["arguments"] = args
        self.s.sendall((json.dumps(msg) + "\n").encode())
        while True:
            r = self.read()
            if "return" in r:
                return r["return"]
            if "error" in r:
                raise Fail(f"{name}: {r['error'].get('desc', r['error'])}")
            # (an event: not the answer)


def save(q, path, timeout):
    q.cmd("stop")
    # (Migration's default bandwidth cap, 128 MiB/s, would make a GiB a matter of seconds.)
    q.cmd("migrate-set-parameters", **{"max-bandwidth": 100 << 30})
    q.cmd("migrate", uri=f"file:{path}")
    end = time.time() + timeout
    while time.time() < end:
        st = q.cmd("query-migrate")
        s = st.get("status")
        if s == "completed":
            print(f"saved: {st.get('ram', {}).get('transferred', '?')} bytes of RAM in {st.get('total-time', '?')} ms")
            return
        if s in ("failed", "cancelled"):
            raise Fail(f"migrate: {s}: {st.get('error-desc', '')}")
        time.sleep(0.2)
    raise Fail(f"migrate: not done in {timeout} s")


def resume(q, timeout):
    end = time.time() + timeout
    while time.time() < end:
        st = q.cmd("query-status").get("status")
        if st == "running":
            print("running")
            return
        if st in ("paused", "postmigrate"):
            m = q.cmd("query-migrate").get("status")
            if m in ("completed", None):
                q.cmd("cont")
                continue
        elif st not in ("inmigrate", "prelaunch"):
            raise Fail(f"the VM is {st}, not loading its state")
        time.sleep(0.05)
    raise Fail(f"the state didn't load in {timeout} s")


def hotplug(q, node, path, dev, bus=None):
    q.cmd("blockdev-add", **{"driver": "qcow2", "node-name": node, "file": {"driver": "file", "filename": path}})
    a = {"driver": "virtio-blk-pci", "drive": node, "id": dev}
    if bus:
        a["bus"] = bus
    q.cmd("device_add", **a)
    print(f"{dev}: {path}{' on ' + bus if bus else ''}")


def unplug(q, timeout, devs):
    for d in devs:
        q.cmd("device_del", id=d)
    # (A device QEMU let go of is no longer among its peripherals.)
    end = time.time() + timeout
    while True:
        left = set(devs) & {p["name"] for p in q.cmd("qom-list", path="/machine/peripheral")}
        if not left:
            break
        if time.time() > end:
            raise Fail(f"not gone in {timeout:g} s: {' '.join(sorted(left))}")
        time.sleep(0.2)
    print(f"unplugged: {' '.join(devs)}")


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    sock, what, rest = argv[0], argv[1], argv[2:]
    try:
        q = QMP(sock)
        if what == "save" and rest:
            save(q, rest[0], float(rest[1]) if len(rest) > 1 else 300)
        elif what == "resume":
            resume(q, float(rest[0]) if rest else 120)
        elif what == "hotplug" and len(rest) in (3, 4):
            hotplug(q, rest[0], rest[1], rest[2], rest[3] if len(rest) == 4 else None)
        elif what == "unplug" and len(rest) >= 2:
            unplug(q, float(rest[0]), rest[1:])
        elif what == "quit":
            try:
                q.cmd("quit")
            except (Fail, OSError):
                pass   # (it may go before it answers)
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (Fail, OSError, ValueError) as e:
        print(f"bs2-qmp: {what}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
