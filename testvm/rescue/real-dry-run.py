#!/usr/bin/env python3
"""bko-rescue's whole flow as a dry run against a REAL bko-holt and
rustic-bko (rescue-tui.md, RT12), in a throwaway HoltTest rescuer, and the
proof it wrote nothing: every target's and the decoy's whole contents, and
the stick's, the same before and after.

    real-dry-run.py --out DIR --stick-dir DIR [--reset] [--targets vdb,vdc]
                    [--decoy vdd] [--bko-rescue /root/bko-rescue] [--share /mnt/share]

Run as root inside the rescuer that `ht1-debug.sh GUEST again partition`
keeps up (testvm/holttest1d), with drive.py and screen.py beside this file.
--reset first wipes the targets' tables (wipefs) and bko-holt's RAM state
(/run/bko-holt), so they are as blank as a new rescuer's: only on a
throwaway rescuer.

The stick: no rescuer has one, so one is made here, a disk image on a loop
device (DIR/stick.img, its partitions read by the kernel): partition 1 a
FAT holding the image's marker in .disk/ and mounted at /cdrom read-only,
as casper mounts the stick's medium; partition 3 the config partition
--stick-dir holds, as `stick/marker.py --config-part 3` made it (its
backotter.json and config.img: no rescuer has mtools), with the machine's
system.json put on it: the share's layout.json as its `layout`, the
share's repository and the backup's hostname.  Every bko-holt call is the
real one's: `stick --json` finds that stick as on a real one, and `stick
--copy` (holt-json.md, open question 8) gives load its layout.  Before the
flow, a `stick --copy` of its own checks the copy is the share's layout.

The flow, in plain mode (the prompts parsed, nothing hard-wired but the
order): the one machine, the backup password (the share's), the newest
backup, each old disk onto its target (vda on vdb, vdb on vdc, as
HoltTest maps them), the passphrase (any: a dry run formats nothing) twice,
each disk's confirmation as its prompt asks, then r: the dry run, to
"The dry run of HOST is done", then Leave.  It checks: bko-rescue exits 0;
Done says nothing was written and how many commands would have run;
BKO_HOLT_NO_COMMIT's lock confirmed (`no_commit`); the targets, the decoy
and the stick image unchanged (md5 of each whole); no partition record or
lock of bko-holt's made; /cdrom unmounted and the loop device let go.  DIR gets the transcript (transcript.txt), the
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


def make_stick(a, system):
    """The stick: a GPT disk image on a loop device, partition 1 a FAT with
    the marker in .disk/ (mounted at /cdrom read-only), 2 a filler, 3 the
    config partition marker.py made, with SYSTEM as the machine's
    system.json (and its .sha256) put on it.  Its image and device."""
    marker_path = os.path.join(a.stick_dir, "backotter.json")
    marker = json.load(open(marker_path))
    cp = marker["config_partition"]
    if cp["number"] != 3:
        raise SystemExit("--stick-dir: the config partition is %s, not 3 (marker.py --config-part 3)" % cp["number"])
    img = os.path.join(a.out, "stick.img")
    if os.path.exists(img):
        sh("losetup -j %s | cut -d: -f1 | xargs -r -n1 losetup -d" % img, check_rc=False)
        os.remove(img)
    if sh("findmnt -n /cdrom", check_rc=False).strip():
        raise SystemExit("/cdrom is mounted already: not a rescuer this can use")
    p3 = 20480
    sh("truncate -s %d %s" % ((p3 + cp["sectors"] + 2048) * 512, img))
    fat = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
    table = "label: gpt\nstart=2048, size=16384, type=%s\nstart=18432, size=2048, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4\nstart=%d, size=%d, type=%s\n" % (fat, p3, cp["sectors"], fat)
    subprocess.run(["sfdisk", "-q", img], input=table.encode(), check=True)
    dev = sh("losetup -fP --show %s" % img).strip()
    sh("udevadm settle", check_rc=False)
    for _ in range(50):
        if os.path.exists(dev + "p3"):
            break
        time.sleep(0.1)
    sh("mkfs.vfat -n BKOMEDIUM %sp1 >/dev/null" % dev)
    sh("dd if=%s of=%sp3 bs=1M conv=fsync status=none" % (os.path.join(a.stick_dir, "config.img"), dev))
    mnt = os.path.join(a.out, "stick-mnt")
    os.makedirs(mnt, exist_ok=True)
    rel = "backotter/machines/%s" % INSTALL_ID
    body = (json.dumps(system, indent=2) + "\n").encode()
    sh("mount -t vfat %sp3 %s && mkdir -p %s/%s" % (dev, mnt, mnt, rel))
    with open(os.path.join(mnt, rel, "system.json"), "wb") as f:
        f.write(body)
    with open(os.path.join(mnt, rel, "system.json.sha256"), "w") as f:
        f.write("%s  system.json\n" % hashlib.sha256(body).hexdigest())
    sh("sync && umount %s" % mnt)
    sh("mount -t vfat %sp1 %s && mkdir -p %s/.disk && cp %s %s/.disk/backotter.json && sync && umount %s" % (dev, mnt, mnt, marker_path, mnt, mnt))
    sh("mkdir -p /cdrom && mount -t vfat -o ro %sp1 /cdrom" % dev)
    return img, dev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--stick-dir", required=True)
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

    layout_text = open(os.path.join(a.share, "layout.json"), "rb").read()
    layout = json.loads(layout_text)
    host = (layout.get("host") or {}).get("hostname") or "debian13-stack"
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
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    system = {
        "format": "backotter-system", "schema": 1, "sequence": 1, "written": now, "written_by": "real-dry-run.py",
        "machine": {"install_id": INSTALL_ID, "hostname": snap_host, "dmi": {"system_uuid": None}},
        "os": {"id": (layout.get("os") or {}).get("id"), "version_id": (layout.get("os") or {}).get("version_id")},
        "backup": {"engine": "rustic", "repository": {"kind": "local", "url": "%s/repo" % a.share},
                   "repository_id": repo_id, "credential": {"kind": "prompt"}},
        "requirements": {"zfs_features": [], "kernel_modules": []},
        "layout": layout,
    }
    img, dev = make_stick(a, system)

    # bko-holt's view of it, read only: the stick found, the machine ok,
    # and its copy the share's layout.
    st = json.loads(sh("BKO_HOLT_NO_COMMIT=1 %s stick --json" % holt))
    machines = st.get("machines") or []
    check(st["config"]["state"] == "ok" and st["config"]["dev"] == dev + "p3", "stick --json: the config partition %s found on %s (%s)" % (
        st["config"]["dev"], (st.get("boot_disk") or {}).get("node"), st["config"]["why"]))
    check(len(machines) == 1 and machines[0]["state"] == "ok" and machines[0]["install_id"] == INSTALL_ID,
          "stick --json: the machine is there and ok (%s)" % [(m["install_id"], m["state"], m["why"]) for m in machines])
    kdir = os.path.join(a.out, "copy-check")
    sh("rm -rf %s && mkdir -m 700 %s" % (kdir, kdir))
    r = subprocess.run([holt, "stick", "--copy", INSTALL_ID, os.path.join(kdir, "layout.json"), "--json"], capture_output=True,
                       env=dict(os.environ, BKO_HOLT_NO_COMMIT="1"))
    copied = open(os.path.join(kdir, "layout.json"), "rb").read() if os.path.exists(os.path.join(kdir, "layout.json")) else b""
    check(r.returncode == 0 and json.loads(r.stdout).get("format") == "bko-holt-stick-copy", "stick --copy: exit 0, its document (%s)" % r.stdout.decode(errors="replace")[:300])
    check(copied and json.loads(copied) == layout, "stick --copy: OUT is the share's layout (%d bytes, byte for byte: %s)" % (len(copied), copied == layout_text))
    summary_copy = {"bytes": len(copied), "same_bytes": copied == layout_text}
    sh("rm -rf %s" % kdir)

    # Before: every disk the run could touch, and the stick, whole.
    disks = ["/dev/%s" % t for t in targets + [a.decoy]]
    sh("sync")
    before = {d: md5(d) for d in disks}
    before[img] = md5(img)
    with open(os.path.join(a.out, "md5-before.json"), "w") as f:
        json.dump(before, f, indent=1)
    holt_state_before = sorted(os.listdir("/run/bko-holt")) if os.path.isdir("/run/bko-holt") else []

    env = {"TERM": "xterm", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    d = drive.Drive(a.bko_rescue, ["--dry-run", "--plain", "--holt", holt, "--rustic", rustic], 24, 80, env, timeout=120)
    summary = {"host": snap_host, "version": version, "stick_dev": dev, "copy": summary_copy}
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
        # Anchored after the "r": an earlier prompt never stands for Done's.
        m = d.mark()
        d.type("r", line=True)
        d.wait("is done", secs=600, since=m)
        d.wait("leave bko-rescue", since=m)
        at = d.text.find(b"leave bko-rescue", m)
        d.wait("Choose [", since=at)
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
    sh("umount /cdrom", check_rc=False)
    sh("losetup -d %s" % dev, check_rc=False)
    check(not sh("findmnt -n /cdrom", check_rc=False).strip(), "/cdrom unmounted")
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
