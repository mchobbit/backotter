#!/usr/bin/env python3
"""Rehearse BackShot1 in QEMU: the real scripts, the way the laptop will run them.

Usage: bs1-qemu.py TEST_IMG GUEST_DIR TOOLS_DIR WORK_DIR [--port N]

TEST_IMG is an image from "make-img.sh --test".  GUEST_DIR is a UEFI test guest
(vms/ubuntu26-uefi), attached as NVMe with its writes in WORK_DIR.  TOOLS_DIR has
bko-holt and rustic-bko.  Three boots, all over SSH with the guest's key:

  source    the running guest: format the USB drive, stage, bs1-backup
  rescue    the stick, its key added by add-ssh-key.sh: wrong serial refused,
            --plan-only changes nothing, then bs1-restore
  restored  the internal disk and the drive, with the NVRAM the run left: bs1-verify

Exits 0 if all of that held.  Nothing outside WORK_DIR is written.
"""

import argparse
import glob
import json
import importlib.util
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rw1a", os.path.join(HERE, "..", "rw1a-qemu.py"))
rw1a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rw1a)

SERIAL, LABEL = "BKOINTERNAL", "BKOBACKUPA"
SSH = rw1a.SSH + ["-o", "ServerAliveInterval=30"]
fails = []


def check(ok, what):
    rw1a.check(ok, what, fails)


class Vm:
    def __init__(self, cmd, log):
        self.out = open(log, "ab")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=self.out, stderr=subprocess.STDOUT)

    def wait_off(self, timeout=180):
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print("  (had to kill QEMU)", flush=True)
            self.proc.kill()
            self.proc.wait()
        self.out.close()


class Ssh:
    def __init__(self, key, port, user):
        self.base = ["ssh", *SSH, "-i", key, "-p", str(port), f"{user}@127.0.0.1"]

    def run(self, command, timeout=3600):
        r = subprocess.run(["timeout", str(timeout), *self.base, command], capture_output=True, text=True, errors="replace")
        return r.returncode, r.stdout + r.stderr

    def up(self, proc, probe="true", timeout=420):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if proc.poll() is not None:
                return False
            # QEMU's user networking accepts before the guest is up: the timeout matters.
            r = subprocess.run(["timeout", "15", *self.base, probe], capture_output=True, text=True)
            if r.returncode == 0:
                return True
            time.sleep(5)
        return False

    def show(self, command, timeout=3600):
        rc, out = self.run(command, timeout)
        print(out[-6000:], flush=True)
        return rc, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("img")
    ap.add_argument("guest_dir")
    ap.add_argument("tools")
    ap.add_argument("work")
    ap.add_argument("--port", type=int, default=2977)
    ap.add_argument("--virtio", action="store_true", help="the internal disk is virtio, for a guest whose initramfs has no NVMe driver")
    ap.add_argument("--bios", action="store_true", help="SeaBIOS, not OVMF")
    ap.add_argument("--smaller", action="store_true", help="restore onto a blank disk 1 GiB smaller, with --fit-largest 10%%")
    ap.add_argument("--discard-on-pass", action="store_true", help="delete the disk images if it passes")
    ap.add_argument("--restores", type=int, default=1, help="how many times to restore the one backup")
    ap.add_argument("--earlier-restore", action="store_true", help="back up a system that shows it was restored once before (its /var/log/bko-holt)")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    dev, parts = ("vda", "/dev/vda[0-9]*") if a.virtio else ("nvme0n1", "/dev/nvme0n1p*")

    def internal_disk(path, boot=False):
        if not a.virtio:
            return rw1a.nvme(path, boot)
        return ["-drive", f"if=none,id=internal,format=qcow2,file={path}",
                "-device", f"virtio-blk-pci,drive=internal,serial={SERIAL}" + (",bootindex=1" if boot else "")]

    def machine(name, disks):
        cmd = rw1a.qemu(a.work, name, f"{a.work}/vars.fd", disks, a.port)
        if not a.bios:
            return cmd
        out, i = [], 0
        while i < len(cmd):
            if i + 1 < len(cmd) and cmd[i] in ("-drive", "-global") and "pflash" in cmd[i + 1]:
                i += 2
                continue
            out.append(cmd[i])
            i += 1
        return out

    guest = [g for g in glob.glob(os.path.join(a.guest_dir, "*.qcow2")) if "overlay" not in os.path.basename(g)]
    if len(guest) != 1:
        sys.exit(f"want one qcow2 in {a.guest_dir}, found {guest}")
    key = os.path.join(a.guest_dir, "id_ed25519")
    w = a.work
    internal, drive_raw, drive, stick_raw, stick, vars_file = (f"{w}/internal.qcow2", f"{w}/drive.raw", f"{w}/drive.qcow2", f"{w}/stick.img", f"{w}/stick.qcow2", f"{w}/vars.fd")
    shutil.copy(f"{rw1a.OVMF}/{rw1a.VARS}", vars_file)
    # --smaller: the source's real size, so that losing 1 GiB takes more than slack.
    guest_size = json.loads(subprocess.run(["qemu-img", "info", "--output=json", guest[0]], capture_output=True, text=True, check=True).stdout)["virtual-size"]
    rw1a.overlay(internal, guest[0], str(guest_size) if a.smaller else "32G")
    # Partitioned and formatted here: a guest fresh from its installer may have
    # neither sfdisk (Alpine) nor mkfs.ext4 (AerynOS).
    env = {**os.environ, "PATH": os.environ["PATH"] + ":/usr/sbin:/sbin"}
    subprocess.run(["truncate", "-s", "40G", drive_raw], check=True)
    subprocess.run(["sfdisk", "-q", "--label", "gpt", drive_raw], input=",,L\n", text=True, check=True, env=env)
    subprocess.run(["mkfs.ext4", "-q", "-F", "-L", LABEL, "-E", "offset=1048576", drive_raw, "39G"], check=True, env=env)
    rw1a.overlay(drive, drive_raw, "40G", "raw")
    shutil.copy(a.img, stick_raw)
    keys = f"{w}/keys"
    os.makedirs(keys, exist_ok=True)
    subprocess.run([os.path.join(HERE, "make-keys")], check=True, env={**os.environ, "BS1_KEYS": keys})
    client, host_key = f"{keys}/id_ed25519", f"{keys}/ssh_host_ed25519_key"
    with open(f"{host_key}.pub") as f:
        host_blob = f.read().split()[1]
    subprocess.run([os.path.join(HERE, "..", "..", "..", "stick", "add-ssh-key.sh"), "--host-key", host_key, stick_raw, f"{client}.pub"], check=True)
    rw1a.overlay(stick, stick_raw, "16G", "raw")
    usb_drive = rw1a.usb("drive", drive, LABEL)

    def host_key_is_pinned():
        r = subprocess.run(["ssh-keyscan", "-T", "10", "-t", "ed25519", "-p", str(a.port), "127.0.0.1"], capture_output=True, text=True)
        return host_blob in r.stdout

    print("== source: the running guest", flush=True)
    vm = Vm(machine("source", [internal_disk(internal, True), usb_drive]), f"{w}/console-source.log")
    root = Ssh(key, a.port, "root")
    if not root.up(vm.proc):
        sys.exit("the source guest never answered")
    _, host = root.run("cat /proc/sys/kernel/hostname")
    host = host.strip().splitlines()[-1]
    rc, _ = root.show(f"mkdir -p /mnt/bs1-backup; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do mount LABEL={LABEL} /mnt/bs1-backup 2>/dev/null && break; sleep 2; done; mkdir -p /mnt/bs1-backup/backshot1/bin /mnt/bs1-backup/backshot1/runs && grep -q ' /mnt/bs1-backup ' /proc/mounts; rc=$?; . /etc/os-release; echo $PRETTY_NAME; exit $rc")
    check(rc == 0, "the source guest mounts the backup drive")
    if fails:
        vm.proc.kill()
        return 1
    conf = f"{w}/bs1.conf"
    with open(conf, "w") as f:
        f.write(f"SOURCE_HOST={host}\nINTERNAL_SERIAL={SERIAL}\nBACKUP_LABEL={LABEL}\n")
    scp = ["scp", "-q", *SSH, "-i", key, "-P", str(a.port)]
    names = [os.path.join(HERE, f"bs1-{n}") for n in ("common", "backup", "restore", "verify", "manifest", "adopt", "list")]
    names.append(os.path.join(HERE, "..", "..", "ostree_paths.py"))      # bs1-manifest's
    subprocess.run([*scp, *names, conf, f"root@127.0.0.1:/mnt/bs1-backup/backshot1/"], check=True)
    subprocess.run([*scp, f"{a.tools}/bko-holt", f"{a.tools}/rustic-bko", "root@127.0.0.1:/mnt/bs1-backup/backshot1/bin/"], check=True)
    root.run("mkdir -m 700 /mnt/bs1-backup/backshot1/keys; chmod 755 /mnt/bs1-backup/backshot1/bs1-* /mnt/bs1-backup/backshot1/bin/*")
    subprocess.run([*scp, f"{client}.pub", "root@127.0.0.1:/mnt/bs1-backup/backshot1/keys/authorized_keys"], check=True)
    subprocess.run([*scp, host_key, f"{host_key}.pub", "root@127.0.0.1:/mnt/bs1-backup/backshot1/keys/"], check=True)
    rc, _ = root.show("sh /mnt/bs1-backup/backshot1/bs1-adopt")
    check(rc == 0, "bs1-adopt passes")
    check(host_key_is_pinned(), "the installed system presents the fixed host key")
    root = Ssh(client, a.port, "root")
    check(root.run("true", 30)[0] == 0, "the BackShot1 key logs in as root")
    def mdns_up():
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if root.run("cat /proc/[0-9]*/comm 2>/dev/null | grep -q '^avahi-daemon'", 30)[0] == 0:
                return True
            time.sleep(3)
        print(root.run("for p in /proc/[0-9]*; do echo \"$(cat $p/comm 2>/dev/null): $(tr '\\0' ' ' <$p/cmdline 2>/dev/null)\"; done | grep -iE 'avahi|dbus'", 30)[1], flush=True)
        return False

    check(mdns_up(), "avahi-daemon is running")
    if fails:
        vm.proc.kill()
        print("FAILED: " + "; ".join(fails), flush=True)
        return 1
    if a.earlier_restore:
        root.run("mkdir -p /var/log/bko-holt; echo '==== stage boot done' >/var/log/bko-holt/load.log")
    rc, out = root.show("bash /mnt/bs1-backup/backshot1/bs1-backup")
    check(rc == 0, "bs1-backup passes")
    run = next((ln[4:].strip() for ln in out.splitlines() if ln.startswith("RUN=")), None)
    check(run is not None, "bs1-backup names its run")
    root.run("umount /mnt/bs1-backup; poweroff")
    vm.wait_off()
    if fails:
        print("FAILED: " + "; ".join(fails), flush=True)
        return 1

    fp = f"sudo -n sh -c 'sfdisk -d /dev/{dev} | sha256sum; blkid -p {parts} | sha256sum'"
    bs1 = "sudo -n bash /mnt/bs1-backup/backshot1/"
    restores = f"/mnt/bs1-backup/backshot1/runs/{run}/restores"
    distro = run.rsplit("-", 2)[0]
    fit = " --fit-largest 10" if a.smaller else ""
    if a.smaller:
        print("== the internal disk is replaced by a blank one 1 GiB smaller", flush=True)
        os.remove(internal)
        rw1a.overlay(internal, None, str(guest_size - (1 << 30)))
    for attempt in range(1, a.restores + 1):
        print(f"== rescue {attempt}: the stick", flush=True)
        vm = Vm(machine(f"rescue{attempt}", [rw1a.usb("stick", stick, "BKOSTICK", True), internal_disk(internal), usb_drive]), f"{w}/console-rescue{attempt}.log")
        rescue = Ssh(client, a.port, "backotter")
        up = rescue.up(vm.proc, "sudo -n true")
        check(up, "the stick starts SSH by itself and the key gets sudo")
        if not up:
            vm.proc.kill()
            return 1
        check(host_key_is_pinned(), "the stick presents the fixed host key")
        rescue.run(f"sudo -n sh -c 'mkdir -p /mnt/bs1-backup; mount LABEL={LABEL} /mnt/bs1-backup'")
        rc, out = rescue.run(f"{bs1}bs1-list --resolve {distro}")
        check(rc == 0 and out.strip() == run, f"\"{distro}\" finds its backup")
        rc, _ = rescue.run(f"{bs1}bs1-list --resolve nosuchdistro")
        check(rc != 0, "an unknown name is refused")
        if attempt == 1:
            _, before = rescue.run(fp)
            rc, out = rescue.run(f"{bs1}bs1-restore --erase-serial WRONGSERIAL {run}")
            check(rc != 0 and "isn't this machine's internal disk" in out, "a wrong serial is refused")
            rc, out = rescue.show(f"{bs1}bs1-restore --erase-serial {SERIAL} --plan-only{fit} {run}")
            check(rc == 0, "--plan-only passes")
            check(rescue.run(fp)[1] == before, "--plan-only changes nothing")
        rc, _ = rescue.show(f"{bs1}bs1-restore --erase-serial {SERIAL} --detach{fit} {run}")
        check(rc == 0, "the detached restore starts")
        status = ""
        end = time.monotonic() + 3000
        while not status and time.monotonic() < end:
            time.sleep(15)
            rc, out = rescue.run("sudo -n cat /run/backotter/restore.exit", 30)
            status = out.strip() if rc == 0 else ""
        rescue.show("sudo -n tail -n 12 /run/backotter/restore.out")
        # How long the boot stage took, and how many atimes it had to put back.
        rescue.show("sudo -n sh -c 'grep -A200 \"==== redoing the boot loader\" /run/backotter/restore.out | grep -m1 -E \"exit [0-9]+ after\"; "
                    "echo atimes to put back: $(LC_ALL=C comm -z -23 /run/bko-holt/atimes.before /run/bko-holt/atimes.after | tr -cd \"\\0\" | wc -c) "
                    "of $(tr -cd \"\\0\" </run/bko-holt/atimes.before | wc -c)'")
        check(status == "0", f"restore {attempt} passes")
        rescue.run("sudo -n sh -c 'umount /mnt/bs1-backup; systemctl poweroff'")
        vm.wait_off()
        if fails:
            print("FAILED: " + "; ".join(fails), flush=True)
            return 1

        print(f"== restored {attempt}: the internal disk, by the NVRAM the run left", flush=True)
        # BIOS has no NVRAM to go by.
        vm = Vm(machine(f"restored{attempt}", [internal_disk(internal, a.bios), usb_drive]), f"{w}/console-restored{attempt}.log")
        # A ZFS root is a dataset, which lsblk can't follow; bs1-verify checks its pool.
        up = root.up(vm.proc, f"findmnt -no FSTYPE / | grep -qx zfs || lsblk -snlo NAME \"$(findmnt -n -o SOURCE / | sed 's/\\[.*//')\" | grep -qx {dev}")
        check(up, "the restored system boots from NVRAM and answers")
        if up:
            check(host_key_is_pinned(), "the restored system presents the fixed host key")
            # Started by its boot this time, so it's enabled, not just running.
            check(mdns_up(), "avahi-daemon comes up at boot")
            rc, out = root.show(f"mkdir -p /mnt/bs1-backup && mount LABEL={LABEL} /mnt/bs1-backup && bash /mnt/bs1-backup/backshot1/bs1-verify {run}; rc=$?; head -n 40 $(ls -d {restores}/*[0-9] | tail -n 1)/compare.txt; exit $rc")
            check(rc == 0, "bs1-verify passes")
            if rc != 0:
                root.show(f"tail -n 60 $(ls -d {restores}/*[0-9] | tail -n 1)/verify.log")
            if attempt == a.restores:
                _, out = root.run(f"ls -d {restores}/*[0-9] | wc -l; cat {restores}/*[0-9]/RESULT | grep -c PASS")
                check(out.split() == [str(a.restores), str(a.restores)], f"each of the {a.restores} restores kept its own PASS")
                root.run(f"mkdir {restores}/99990101-000000")
                rc, out = root.run(f"bash /mnt/bs1-backup/backshot1/bs1-verify {run}")
                check(rc == 2 and "didn't finish" in out, "bs1-verify refuses when the newest restore didn't finish")
                root.run(f"rmdir {restores}/99990101-000000")
                root.show("bash /mnt/bs1-backup/backshot1/bs1-list")
            root.run("umount /mnt/bs1-backup; poweroff")
        vm.wait_off()
        if fails:
            break
    print("FAILED: " + "; ".join(fails) if fails else "PASS", flush=True)
    if a.discard_on_pass and not fails:
        for f in (internal, drive, drive_raw, stick, stick_raw, vars_file):
            os.remove(f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
