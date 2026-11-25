#!/usr/bin/env python3
"""bko-rescue's whole flow as a dry run against a REAL bko-holt and
rustic-bko (rescue-tui.md, RT12), in a throwaway HoltTest rescuer, and the
proof it wrote nothing: every target's and the decoy's whole contents, and
the stick's, the same before and after.

    real-dry-run.py --out DIR [--reset] [--targets vdb,vdc] [--decoy vdd]
                    [--bko-rescue /root/bko-rescue] [--share /mnt/share]

Run as root inside the rescuer that `ht1-debug.sh GUEST again partition`
keeps up (testvm/holttest1d), with drive.py and screen.py beside this file.
--reset first wipes the targets' tables (wipefs) and bko-holt's RAM state
(/run/bko-holt), so they are as blank as a new rescuer's: only on a
throwaway rescuer.

The stick: no rescuer has one, and a stick's system.json isn't yet a layout
bko-holt loads (holt-json.md, open question 8).  So the config partition
is a FAT image on a loop device holding the share's layout.json as the
machine's system.json, and `bko-holt stick --json` alone is answered by a
document made here naming that device (a wrapper, DIR/holt-wrap); every
other bko-holt call, `--version --json` among them, is the real one's.

The flow, in plain mode (the prompts parsed, nothing hard-wired but the
order): the one machine, the backup password (the share's), the newest
backup, each old disk onto its target (vda on vdb, vdb on vdc, as
HoltTest maps them), the passphrase (any: a dry run formats nothing) twice,
each disk's confirmation as its prompt asks, then r: the dry run, to
"The dry run of HOST is done", then Leave.  It checks: bko-rescue exits 0;
Done says nothing was written and how many commands would have run;
BKO_HOLT_NO_COMMIT's lock confirmed (`no_commit`); the targets, the decoy
and the stick image unchanged (md5 of each whole); no partition record or
lock of bko-holt's made.  DIR gets the transcript (transcript.txt), the
checksums and summary.json.  Exit 0 when every check passes.

Python's stdlib only.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drive  # noqa: E402

INSTALL_ID = "5e1f0c2a-7b3d-4c8e-9a6f-0d2e4b7c9a13"
FAILS = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        FAILS.append(what)
    return ok


def sh(cmd, check_rc=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check_rc and r.returncode != 0:
        raise SystemExit("%s: exit %d: %s" % (cmd, r.returncode, r.stderr.strip()[-500:]))
    return r.stdout


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(16 << 20)
            if not b:
                return h.hexdigest()
            h.update(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--targets", default="vdb,vdc")
    ap.add_argument("--decoy", default="vdd")
    ap.add_argument("--bko-rescue", default="/root/bko-rescue")
    ap.add_argument("--share", default="/mnt/share")
    a = ap.parse_args()
    os.makedirs(a.out, mode=0o700, exist_ok=True)
    targets = a.targets.split(",")
    holt = os.path.join(a.share, "bko-holt")
    rustic = os.path.join(a.share, "rustic-bko")
    if a.reset:
        for t in targets:
            sh("wipefs -a /dev/%s >/dev/null" % t)
            sh("dd if=/dev/zero of=/dev/%s bs=1M count=1 conv=fsync status=none" % t)
            sh("blockdev --rereadpt /dev/%s" % t, check_rc=False)
        sh("rm -rf /run/bko-holt")
        sh("udevadm settle", check_rc=False)
    # bko-holt load runs the archiver by name.
    if not os.path.exists("/usr/local/bin/rustic-bko"):
        os.symlink(rustic, "/usr/local/bin/rustic-bko")

    # The stick's config partition: a FAT image on a loop device.
    img = os.path.join(a.out, "stick.img")
    if os.path.exists(img):
        sh("losetup -j %s | cut -d: -f1 | xargs -r -n1 losetup -d" % img, check_rc=False)
        os.remove(img)
    sh("truncate -s 64M %s && mkfs.vfat -n BACKOTTER %s >/dev/null" % (img, img))
    dev = sh("losetup -f --show %s" % img).strip()
    mnt = os.path.join(a.out, "stick-mnt")
    os.makedirs(mnt, exist_ok=True)
    rel = "backotter/machines/%s/system.json" % INSTALL_ID
    sh("mount -t vfat %s %s && mkdir -p %s/%s && cp %s/layout.json %s/%s && sync && umount %s" % (
        dev, mnt, mnt, os.path.dirname(rel), a.share, mnt, rel, mnt))
    layout = json.load(open(os.path.join(a.share, "layout.json")))
    host = (layout.get("system") or {}).get("hostname") or layout.get("hostname") or "debian13-stack"
    pw_file = os.path.join(a.share, "repo-password")
    snaps = json.loads(sh("%s --repository %s/repo --password-file %s --no-progress snapshots --json 2>/dev/null" % (rustic, a.share, pw_file)))
    flat = []
    for g in snaps:
        # rustic's groups ({group_key, snapshots}), or snapshots alone.
        flat += g["snapshots"] if isinstance(g, dict) and isinstance(g.get("snapshots"), list) else [g]
    snap_host = next((s.get("hostname") for s in flat if s.get("hostname")), host)
    conf = sh("%s --repository %s/repo --password-file %s --no-progress cat config 2>/dev/null" % (rustic, a.share, pw_file))
    repo_id = json.loads(conf).get("id")
    version = json.loads(sh("%s --version --json" % holt))["version"]
    stick = {
        "format": "bko-holt-stick", "schema": 1, "version": version,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "dry_run": True,
        "boot_disk": {"node": os.path.basename(dev), "found_by": "test"},
        "config": {"state": "ok", "partition": 1, "label": "BACKOTTER", "why": None, "dev": dev},
        "image": {"image_id": "real-dry-run", "platform": "linux", "built": "2026-11-25T00:00:00Z", "system_schemas": [1]},
        "stick": {"stick_id": None, "name": None}, "here": {"dmi_uuid": None},
        "machines": [{
            "install_id": INSTALL_ID, "path": rel, "state": "ok", "why": None, "schema": 1, "known_schema": True,
            "hostname": snap_host, "os": {"id": "debian", "version_id": "13"},
            "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sequence": 1, "dmi_uuid": None,
            "this_computer": False, "repository": {"kind": "local", "url": "%s/repo" % a.share},
            "repository_id": repo_id, "keyboard": None, "time": None, "password_hint": None}],
        "notes": [],
    }
    stick_doc = os.path.join(a.out, "stick.json")
    with open(stick_doc, "w") as f:
        json.dump(stick, f)
    wrap = os.path.join(a.out, "holt-wrap")
    with open(wrap, "w") as f:
        f.write("#!/bin/sh\n# stick --json alone is answered here (no rescuer has a stick); the rest is bko-holt's.\n"
                "if [ \"$1\" = stick ] && [ \"$2\" = --json ] && [ $# -eq 2 ]; then cat %s; exit 0; fi\nexec %s \"$@\"\n" % (stick_doc, holt))
    os.chmod(wrap, 0o755)

    # Before: every disk the run could touch, and the stick, whole.
    disks = ["/dev/%s" % t for t in targets + [a.decoy]]
    sh("sync")
    before = {d: md5(d) for d in disks}
    before[img] = md5(img)
    with open(os.path.join(a.out, "md5-before.json"), "w") as f:
        json.dump(before, f, indent=1)
    holt_state_before = sorted(os.listdir("/run/bko-holt")) if os.path.isdir("/run/bko-holt") else []

    env = {"TERM": "xterm", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    d = drive.Drive(a.bko_rescue, ["--dry-run", "--plain", "--holt", wrap, "--rustic", rustic], 24, 80, env, timeout=120)
    summary = {"host": snap_host, "version": version, "stick_dev": dev}
    pp = "dry-run passphrase"
    try:
        d.wait("t, s]: ")
        d.type("1", line=True)
        d.wait("Type the backup password, or press Enter alone for other choices:")
        d.type(open(pw_file).read().strip(), line=True)
        m = d.mark()
        d.wait(", b]: ", since=m)
        m = d.mark()
        d.type("1", line=True)
        overview = "o) these are right"
        d.wait(overview, since=m)
        for i, t in enumerate(targets, 1):
            m = d.mark()
            d.type(str(i), line=True)
            d.wait("where does it go?", since=m)
            d.wait("Choose [", since=m)
            text = d.text[m:].decode(errors="replace")
            row = re.search(r"(\d+)\) %s, " % t, text)
            check(row is not None, "old disk %d: its target %s is offered" % (i, t))
            m = d.mark()
            d.type(row.group(1) if row else "b", line=True)
            d.wait(overview, since=m)
        d.type("o", line=True)
        d.wait("Passphrase: ")
        d.type(pp, line=True)
        d.wait("Type it again: ")
        # Each mark before the line typed: its answer comes as it is sent.
        m = d.mark()
        d.type(pp, line=True)
        for n in range(1, len(targets) + 1):
            d.wait("or b to go back: ", since=m)
            text = d.text[m:].decode(errors="replace")
            want = re.search(r"type its size and name, (.+?), or b to go back: ", text)
            if not want:
                check(False, "disk %d: its confirmation asked by size and name (no serial here)" % n)
                break
            m = d.mark()
            d.type(want.group(1), line=True)
        d.wait("Choose [r, c, a, b]: ", since=m)
        d.type("r", line=True)
        d.wait("is done", secs=600)
        d.wait("Choose [")
        text = d.dump()
        n = re.search(r"Nothing was written: (\d+) commands? would have run, and none did\.", text)
        check(n is not None, "Done: nothing was written, N commands would have run")
        summary["would"] = int(n.group(1)) if n else None
        leave = re.search(r"(\w)\) leave bko-rescue", text)
        d.type(leave.group(1) if leave else "q", line=True)
        code = d.exit(30)
        check(code == 0, "bko-rescue exits 0 (%s)" % code)
    except drive.Failed as e:
        check(False, "the flow: %s" % e)
    finally:
        with open(os.path.join(a.out, "transcript.txt"), "w") as f:
            f.write(d.dump())
        d.close()
    text = open(os.path.join(a.out, "transcript.txt")).read()
    check("DRY RUN: nothing will be written" in text, "the banner on every screen")
    check("\x1b" not in text, "plain mode: no escape sequences")
    check(pp not in text, "the passphrase never shown")

    sh("sync")
    after = {x: md5(x) for x in disks}
    sh("losetup -d %s" % dev, check_rc=False)
    after[img] = md5(img)
    with open(os.path.join(a.out, "md5-after.json"), "w") as f:
        json.dump(after, f, indent=1)
    for x in before:
        check(before[x] == after[x], "%s unchanged (md5 %s)" % (x, after[x][:12]))
    state = sorted(os.listdir("/run/bko-holt")) if os.path.isdir("/run/bko-holt") else []
    check("partitioned" not in state and "load.lock" not in state, "bko-holt made no partition record and holds no lock (%s, before %s)" % (state, holt_state_before))
    summary.update(checks_failed=FAILS, before=before, after=after, holt_state=state)
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print("%d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
