#!/usr/bin/env python3
"""Rehearse BackShot1 in QEMU: the real scripts, the way the laptop will run them.

Usage: bs1-qemu.py TEST_IMG GUEST_DIR TOOLS_DIR WORK_DIR [--port N] [--reformat MOUNT=TYPE]...

TEST_IMG is an image from "make-img.sh --test".  GUEST_DIR is a UEFI test guest
(vms/ubuntu26-uefi), attached as NVMe with its writes in WORK_DIR.  TOOLS_DIR has
bko-holt and rustic-bko.  Three boots, all over SSH with the guest's key:

  source    the running guest: format the USB drive, stage, bs1-backup
  rescue    the stick, its key added by add-ssh-key.sh: wrong serial refused,
            --plan-only changes nothing, then bs1-restore
  restored  the internal disk and the drive, with the NVRAM the run left: bs1-verify
            (which checks GRUB's embedded lvmid where it has one: gentoo)

The three share one NVRAM, as a laptop's: WORK_DIR/vars.fd, a fresh store, or a
copy of GUEST_DIR/uefi-vars.fd when the guest has one and boots with Secure Boot
off (its install's: cachyos-zfs's has Limine's entry).  The source then boots
through its boot loader's own entry, as the Thinkpad does, and bs1-restore's
load recreates that entry for the restored boot.  A source that booted
through the firmware's own entry for the disk instead (a fresh store, and
no entry of its own on a partition: manjaro) has none to recreate, so its
restored boot is given the disk first as the source's was (bootindex): it
then goes through the ESP's removable-media loader, which load renews.
Without that, OVMF's shell, ahead of the disk's entry it makes anew at
that boot, would take the boot.

The internal disk is the guest's own size.  The backup is checked to be named
/mnt/bs1-src (the tree is mounted elsewhere: --as-path) and to keep a file's
real access time.  An ostree guest (kinoite44: docs/design/bazzite.md) then
boots its restored rollback and its default again, each checked by
"bs1-verify --boot": GRUB is pointed at the rollback by a `default` in
grubenv, since bootupd's grub.cfg never reads next_entry (grub2-reboot), and
grubenv is put back as it was.

A LUKS guest's passphrase (the first of GUEST_DIR's luks-keyfile,
console-passphrase and luks-passphrase, without a final newline) goes into
keys/disk-passphrase.  When bs1-adopt sets the guest to unlock from the drive
(docs/design/bs1-luks-unattended.md: families S, D and E), the source also:
  (a) runs bs1-adopt again, which must change nothing and remake nothing;
  (b) boots with the drive and nothing typed: "bs1-verify --unlock" must say
      UNLOCK=drive;
  (c) boots without the drive: the prompt must come (after the wait), and,
      answered, --unlock must say typed;
  (d) then boots with the drive again for the backup, whose preflight must say
      UNLOCK=drive.
The restored system boots with the drive and nothing typed, and bs1-verify
checks the unlock too; after the last restore it boots once without the
drive, which must again reach the prompt.  --luks-typed answers the source's
own first boot (and an attended run's restored boots) on the serial console
(console-boot.py), or with --luks-monitor through QEMU's monitor
(omarchy/type-luks-passphrase.py: Plymouth asks on the screen).
--strip-embedded-key first takes a guest's own key file out of crypttab and
the initramfs (gentoo-systemd, chimera-uefi), so it asks like the Thinkpad.
--luks-off runs "bs1-adopt --luks-off" once after (a): the files must be as
before bs1-adopt, then it adopts again.

--reformat goes to bs1-restore, and so to bko-holt load: the restore makes
that filesystem as TYPE, and bs1-verify checks it is.

Exits 0 if all of that held.  Nothing outside WORK_DIR is written.
"""

import argparse
import base64
import datetime
import glob
import json
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rw1a", os.path.join(HERE, "..", "rw1a-qemu.py"))
rw1a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rw1a)

SERIAL, LABEL = "BKOINTERNAL", "BKOBACKUPA"
# Guix System (Shepherd) has neither systemctl nor poweroff: its own reboot,
# and halt, which powers off.
REBOOT = "if command -v systemctl >/dev/null; then systemctl reboot; else reboot; fi"
POWEROFF = "if command -v poweroff >/dev/null; then poweroff; else halt; fi"
# A file whose access time the backup must keep (rustic-bko's --set-atime yes,
# with --as-path): written before bs1-backup, found in the snapshot after.
PROBE, PROBE_ATIME = "var/tmp/bs1-atime-probe", "2001-02-03 04:05:06"     # UTC
# Points GRUB at the one BLS entry whose ostree= leads to a deployment other
# than the booted one, by grubenv's `default`, and prints the entry's ID
# (ht1.sh's OSTREE_OTHER, docs/design/bazzite.md B6).
OSTREE_OTHER = r'''b=$(readlink -f "$(tr " " "\n" </proc/cmdline | sed -n "s/^ostree=//p")"); n=0; id=""
for e in /boot/loader/entries/ostree-*.conf; do
    [ -f "$e" ] || continue
    o=$(sed -n "s/^options[ \t]*//p" "$e" | tr " " "\n" | sed -n "s/^ostree=//p")
    [ -n "$o" ] && [ "$(readlink -f "$o")" != "$b" ] && { id=$(basename "$e" .conf); n=$((n + 1)); }
done
[ "$n" = 1 ] || { echo "$n BLS entries lead to a deployment other than the booted $b" >&2; exit 1; }
grub2-editenv /boot/grub2/grubenv set default="$id" && echo "$id"'''
SSH = rw1a.SSH + ["-o", "ServerAliveInterval=30"]
CONSOLE_BOOT = os.path.join(HERE, "..", "..", "console-boot.py")
# The entry the firmware booted through, from efivarfs (no efibootmgr needed).
NVBOOT = os.path.join(HERE, "..", "..", "holttest1d", "nvboot-efivars.sh")
TYPE_LUKS = os.path.join(HERE, "..", "..", "omarchy", "type-luks-passphrase.py")
# --strip-embedded-key: crypttab's key files become none, and leave the
# initramfs (dracut's install_items, cryptsetup-initramfs's KEYFILE_PATTERN)
# and the disk; the initramfs is remade as bko-holt load would.  Plain sh, and
# no sed -i: Chimera's is FreeBSD's.
STRIP_KEY = r'''set -e
t=/etc/crypttab
keys=$(awk '!/^[[:space:]]*#/ && NF >= 3 && $3 != "none" && $3 != "-" && $3 !~ /:/ { print $3 }' $t)
[ -n "$keys" ] || { echo "no key file in $t"; exit 1; }
awk 'BEGIN { OFS = "\t" } !/^[[:space:]]*#/ && NF >= 3 && $3 != "none" && $3 != "-" && $3 !~ /:/ { $3 = "none" } { print }' $t >$t.bs1
cat $t.bs1 >$t; rm $t.bs1
for k in $keys; do
    for f in /etc/dracut.conf.d/*.conf; do
        [ -f "$f" ] || continue
        grep -v -F "$k" "$f" >"$f.bs1" || true
        cat "$f.bs1" >"$f"; rm "$f.bs1"
    done
    rm -f "$k"
done
f=/etc/cryptsetup-initramfs/conf-hook
if [ -f $f ]; then grep -v '^KEYFILE_PATTERN=' $f >$f.bs1 || true; cat $f.bs1 >$f; rm $f.bs1; fi
if command -v mkinitcpio >/dev/null; then mkinitcpio -P
elif command -v dracut >/dev/null; then
    if [ -e /etc/gentoo-release ]; then
        for i in /boot/initramfs-*.img; do k=${i#/boot/initramfs-}; k=${k%.img}; [ ! -d /lib/modules/$k ] || dracut --force $i $k; done
    else
        for k in /lib/modules/*; do [ ! -d $k/kernel ] || dracut --force --kver ${k##*/}; done
    fi
elif command -v update-initramfs >/dev/null; then update-initramfs -u -k all
fi
cat $t'''
# What bs1-adopt's LUKS step writes, for "a second run changes nothing" and
# "--luks-off puts it back".
LUKS_FILES = ("find /etc/backshot1 /etc/crypttab /etc/crypttab.initramfs /etc/dracut.conf.d /etc/mkinitcpio.conf.d "
              "/etc/initcpio /etc/initramfs-tools/hooks /usr/local/lib/backshot1 -type f 2>/dev/null | LC_ALL=C sort | "
              "while read -r f; do echo \"$f $(sha256sum <\"$f\" | cut -c1-16)\"; done")
fails = []


def check(ok, what):
    rw1a.check(ok, what, fails)


class Vm:
    def __init__(self, cmd, log, typer=None):
        self.out = open(log, "ab")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=self.out, stderr=subprocess.STDOUT)
        self.typer = subprocess.Popen(typer) if typer else None

    def stop(self):
        """TERM first: console-boot.py passes it on to its QEMU, which a KILL
        of console-boot.py would leave running."""
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.typer and self.typer.poll() is None:
            self.typer.kill()
            self.typer.wait()

    def wait_off(self, timeout=180):
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print("  (had to stop QEMU)", flush=True)
        self.stop()
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

    def reboot(self, proc, timeout=420):
        """Reboots the guest and waits for its next boot: a new boot ID, since
        sshd answers for a while after `systemctl reboot`."""
        rc, old = self.run("cat /proc/sys/kernel/random/boot_id", 30)
        if rc != 0:
            return False
        self.run(REBOOT, 30)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if proc.poll() is not None:
                return False
            rc, now = self.run("cat /proc/sys/kernel/random/boot_id", 15)
            if rc == 0 and now.strip() and now.strip() != old.strip():
                return True
            time.sleep(5)
        return False


def rollback_boots(root, vm, run, restores, host_key_is_pinned):
    """The restored ostree system's rollback booted once, then its default
    again, each checked by bs1-verify --boot against the baseline.  False if
    the guest stopped answering."""
    drive = f"mkdir -p /mnt/bs1-backup && mount LABEL={LABEL} /mnt/bs1-backup"
    _, grubenv = root.run("sha256sum </boot/grub2/grubenv", 30)
    rc, out = root.run(OSTREE_OTHER, 60)
    entry = out.strip().splitlines()[-1] if out.strip() else ""
    check(rc == 0, f"GRUB is pointed at the restored rollback, entry {entry or out.strip()}")
    if rc != 0:
        return True
    root.run("umount /mnt/bs1-backup", 60)
    up = root.reboot(vm.proc)
    check(up, "the restored rollback boots and answers")
    if not up:
        return False
    # Informational: bs1-adopt writes the fixed host key into the booted
    # deployment's etc only, so the rollback may present the old one.
    print(f"  (the rollback presents the fixed host key: {'yes' if host_key_is_pinned() else 'no'})", flush=True)
    rc, out = root.show(f"{drive} && bash /mnt/bs1-backup/backshot1/bs1-verify --boot {run}")
    check(rc == 0 and "BOOT: rollback: PASS" in out, "bs1-verify --boot: the rollback is the source's")
    rc, _ = root.run("grub2-editenv /boot/grub2/grubenv unset default", 30)
    _, after = root.run("sha256sum </boot/grub2/grubenv", 30)
    check(rc == 0 and after == grubenv, "grubenv is as it was again")
    root.run("umount /mnt/bs1-backup", 60)
    up = root.reboot(vm.proc)
    check(up, "the restored default boots again")
    if not up:
        return False
    rc, out = root.show(f"{drive} && bash /mnt/bs1-backup/backshot1/bs1-verify --boot {run}; cat $(ls -d {restores}/*[0-9] | tail -n 1)/BOOTS")
    check(rc == 0 and "BOOT: default: PASS" in out, "bs1-verify --boot: then the default is the source's again")
    return True


def own_nvram(guest_dir):
    """The store the source starts on: the guest's own (uefi-vars.fd) when it
    has one and boots with Secure Boot off (vm-run-options' --no-secure-boot:
    the store is OVMF_CODE_4M.fd's, as this one boots), else a fresh one."""
    own, opts = os.path.join(guest_dir, "uefi-vars.fd"), os.path.join(guest_dir, "vm-run-options")
    try:
        with open(opts) as f:
            insecure = "--no-secure-boot" in f.read().split()
    except OSError:
        insecure = False
    return own if insecure and os.path.isfile(own) else f"{rw1a.OVMF}/{rw1a.VARS}"


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
    ap.add_argument("--luks-typed", action="store_true", help="the guest asks for its LUKS passphrase at boot: answer its first boot")
    ap.add_argument("--luks-monitor", action="store_true", help="with --luks-typed: type it on the keyboard through QEMU's monitor (Plymouth: omarchy)")
    ap.add_argument("--strip-embedded-key", action="store_true", help="first take the guest's own LUKS key file out of crypttab and its initramfs, so it asks")
    ap.add_argument("--luks-off", action="store_true", help="also check bs1-adopt --luks-off, once")
    ap.add_argument("--reformat", action="append", default=[], metavar="MOUNT=TYPE", help="restore the filesystem at MOUNT as TYPE (bko-holt load --reformat)")
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
    shutil.copy(own_nvram(a.guest_dir) if not a.bios else f"{rw1a.OVMF}/{rw1a.VARS}", vars_file)
    # The source's real size: the dump and the restore see the disk the guest
    # has (and with --smaller, losing 1 GiB takes more than slack).
    guest_size = json.loads(subprocess.run(["qemu-img", "info", "--output=json", guest[0]], capture_output=True, text=True, check=True).stdout)["virtual-size"]
    rw1a.overlay(internal, guest[0], str(guest_size))
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

    # The guest's LUKS passphrase, as the person writes theirs: no final newline.
    passfile = None
    for n in ("luks-keyfile", "console-passphrase", "luks-passphrase"):
        src = os.path.join(a.guest_dir, n)
        if os.path.isfile(src):
            passfile = f"{w}/disk-passphrase"
            with open(src, "rb") as f, open(passfile, "wb") as out:
                out.write(f.read().rstrip(b"\n"))
            os.chmod(passfile, 0o600)
            break
    if (a.luks_typed or a.strip_embedded_key) and not passfile:
        sys.exit(f"{a.guest_dir} has no luks-keyfile, console-passphrase or luks-passphrase")
    mount_drive = f"mkdir -p /mnt/bs1-backup; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do mount LABEL={LABEL} /mnt/bs1-backup 2>/dev/null && break; sleep 2; done; mountpoint -q /mnt/bs1-backup"

    def boot(name, disks, typed=False, delays=(25, 50, 80)):
        """A QEMU of the guest; typed: its LUKS prompt is answered, on the
        serial console or (--luks-monitor) on the keyboard after DELAYS s."""
        cmd, log = machine(name, disks), f"{w}/console-{name}.log"
        if not typed:
            return Vm(cmd, log)
        print(f"  ({name}: the passphrase is typed when asked)", flush=True)
        if a.luks_monitor:
            sock = f"{w}/monitor-{name}.sock"
            i = cmd.index("-monitor")
            cmd[i + 1] = f"unix:{sock},server,nowait"
            return Vm(cmd, log, [sys.executable, TYPE_LUKS, sock, passfile, *map(str, delays)])
        return Vm([sys.executable, CONSOLE_BOOT, log, passfile, "--", *cmd], f"{w}/console-{name}.wrapper.log")

    def off(vm, sh):
        sh.run("umount /mnt/bs1-backup 2>/dev/null; " + POWEROFF, 60)
        vm.wait_off()

    def failed(vm):
        vm.stop()
        print("FAILED: " + "; ".join(fails), flush=True)
        return 1

    print("== source: the running guest", flush=True)
    vm = boot("source", [internal_disk(internal, True), usb_drive], a.luks_typed)
    root = Ssh(key, a.port, "root")
    if not root.up(vm.proc):
        vm.stop()
        sys.exit("the source guest never answered")
    if a.strip_embedded_key:
        print("== the guest's own LUKS key file out, so it asks as the Thinkpad does", flush=True)
        rc, _ = root.show(STRIP_KEY, 1800)
        check(rc == 0, "the guest's key file is out of crypttab and the initramfs")
        if fails:
            return failed(vm)
        off(vm, root)
        vm = boot("source-typed", [internal_disk(internal, True), usb_drive], True)
        if not root.up(vm.proc):
            vm.stop()
            sys.exit("the source guest, its key file out, never answered with the passphrase typed")
    _, host = root.run("cat /proc/sys/kernel/hostname")
    host = host.strip().splitlines()[-1]
    rc, _ = root.show(f"{mount_drive} && mkdir -p /mnt/bs1-backup/backshot1/bin /mnt/bs1-backup/backshot1/runs; rc=$?; . /etc/os-release; echo $PRETTY_NAME; exit $rc")
    check(rc == 0, "the source guest mounts the backup drive")
    if fails:
        return failed(vm)
    conf = f"{w}/bs1.conf"
    with open(conf, "w") as f:
        f.write(f"SOURCE_HOST={host}\nINTERNAL_SERIAL={SERIAL}\nBACKUP_LABEL={LABEL}\n")
    scp = ["scp", "-q", *SSH, "-i", key, "-P", str(a.port)]
    names = [os.path.join(HERE, f"bs1-{n}") for n in ("common", "backup", "restore", "verify", "manifest", "ostree", "adopt", "luks", "unlock-key", "list")]
    names.append(os.path.join(HERE, "..", "..", "ostree_paths.py"))      # bs1-manifest's and bs1-ostree's
    subprocess.run([*scp, *names, conf, f"root@127.0.0.1:/mnt/bs1-backup/backshot1/"], check=True)
    subprocess.run([*scp, f"{a.tools}/bko-holt", f"{a.tools}/rustic-bko", "root@127.0.0.1:/mnt/bs1-backup/backshot1/bin/"], check=True)
    root.run("mkdir -m 700 /mnt/bs1-backup/backshot1/keys; chmod 755 /mnt/bs1-backup/backshot1/bs1-* /mnt/bs1-backup/backshot1/bin/*")
    subprocess.run([*scp, f"{client}.pub", "root@127.0.0.1:/mnt/bs1-backup/backshot1/keys/authorized_keys"], check=True)
    subprocess.run([*scp, host_key, f"{host_key}.pub", "root@127.0.0.1:/mnt/bs1-backup/backshot1/keys/"], check=True)
    # A LUKS guest: its passphrase is what load formats with, and what the
    # restored system unlocks with from the drive.
    if passfile:
        subprocess.run([*scp, passfile, "root@127.0.0.1:/mnt/bs1-backup/backshot1/keys/disk-passphrase"], check=True)
    _, luks_before = root.run(LUKS_FILES, 60)
    rc, _ = root.show("sh /mnt/bs1-backup/backshot1/bs1-adopt", 3600)
    check(rc == 0, "bs1-adopt passes")
    check(host_key_is_pinned(), "the installed system presents the fixed host key")
    root = Ssh(client, a.port, "root")
    check(root.run("true", 30)[0] == 0, "the BackShot1 key logs in as root")
    _, family = root.run(". /etc/backshot1/luks-unlock 2>/dev/null && echo \"$family\"", 30)
    family = family.strip()
    print(f"  (LUKS: {family or 'not adopted, or no LUKS'})", flush=True)

    def volumes(out):
        """bs1-verify --unlock's volume lines: {NAME: HOW}."""
        return dict(m.groups() for m in re.finditer(r"^  (\S+) (\S+)$", out, re.M))

    def unlock(sh, local=False):
        """bs1-verify --unlock's output; local: from a copy of the scripts in
        /run, for a boot without the drive."""
        if local:
            sh.run("mkdir -p /run/bs1-check", 30)
            subprocess.run(["scp", "-q", *SSH, "-i", client, "-P", str(a.port), *[os.path.join(HERE, f"bs1-{n}") for n in ("common", "verify")],
                            conf, "root@127.0.0.1:/run/bs1-check/"], check=True)
            return sh.show("bash /run/bs1-check/bs1-verify --unlock", 120)
        return sh.show(f"{mount_drive} && bash /mnt/bs1-backup/backshot1/bs1-verify --unlock", 120)

    if family in ("S", "D", "E"):
        print("== LUKS (a): bs1-adopt again changes nothing", flush=True)
        _, sums = root.run(LUKS_FILES, 60)
        rc, out = root.show("sh /mnt/bs1-backup/backshot1/bs1-adopt", 3600)
        _, sums2 = root.run(LUKS_FILES, 60)
        check(rc == 0 and "unchanged; the initramfs isn't remade" in out and sums == sums2, "a second bs1-adopt changes nothing and remakes nothing")
        if a.luks_off:
            rc, _ = root.show("sh /mnt/bs1-backup/backshot1/bs1-adopt --luks-off", 3600)
            _, now = root.run(LUKS_FILES, 60)
            check(rc == 0 and now == luks_before, "bs1-adopt --luks-off puts back the files as they were before bs1-adopt")
            if now != luks_before:
                print(f"  before:\n{luks_before}\n  after --luks-off:\n{now}", flush=True)
            rc, _ = root.show("sh /mnt/bs1-backup/backshot1/bs1-adopt", 3600)
            check(rc == 0, "bs1-adopt again, after --luks-off")
        if fails:
            return failed(vm)
        off(vm, root)
        print("== LUKS (b): a boot with the drive, nothing typed", flush=True)
        vm = boot("source-drive", [internal_disk(internal, True), usb_drive])
        up = root.up(vm.proc, timeout=300)
        check(up, "the source boots with the drive, unlocked by its key, and answers within 5 min")
        if up:
            rc, out = unlock(root)
            check(rc == 0 and "UNLOCK=drive" in out, "bs1-verify --unlock: UNLOCK=drive")
        if fails:
            return failed(vm)
        off(vm, root)
        print("== LUKS (c): a boot without the drive asks, after the wait", flush=True)
        vm = boot("source-nodrive", [internal_disk(internal, True)], True, (75, 105, 135))
        up = root.up(vm.proc, timeout=600)
        check(up, "the source boots without the drive, its passphrase typed at the prompt")
        if up:
            rc, out = unlock(root, local=True)
            v = volumes(out)
            check("UNLOCK=attended" in out and v and "drive" not in v.values(), f"bs1-verify --unlock: typed, not the drive: {v}")
        if fails:
            return failed(vm)
        root.run(POWEROFF, 60)
        vm.wait_off()
        print("== LUKS (d): with the drive again, for the backup", flush=True)
        vm = boot("source-backup", [internal_disk(internal, True), usb_drive])
        up = root.up(vm.proc, timeout=300)
        check(up, "the source boots with the drive again")
        if up:
            rc, _ = root.show(mount_drive, 60)
            check(rc == 0, "the source guest mounts the backup drive again")
        if fails:
            return failed(vm)

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
        return failed(vm)
    # Whether the source booted through an entry of its own (one on a
    # partition: HD()), which the restore recreates, or the firmware's.
    by_firmware = False
    if not a.bios:
        with open(NVBOOT, "rb") as f:
            script = base64.b64encode(f.read()).decode()
        _, nv = root.run(f"echo {script} | base64 -d | sh", 60)
        nv = nv.strip().splitlines()[-1] if nv.strip() else ""
        by_firmware = nv.startswith("Boot") and "HD(" not in nv
        print(f"the source booted through {nv or '(unknown)'}" + ("; the firmware's own entry, so the restored boot gets the disk first too" if by_firmware else ""), flush=True)
    if a.earlier_restore:
        root.run("mkdir -p /var/log/bko-holt; echo '==== stage boot done' >/var/log/bko-holt/load.log")
    _, ostree = root.run("test -e /run/ostree-booted && echo ostree-booted", 30)
    ostree = "ostree-booted" in ostree
    # Not on a /var/tmp in RAM, which isn't in the backup.
    rc, _ = root.run(f"findmnt -no FSTYPE --target /var/tmp | grep -qvx tmpfs && echo probe >/{PROBE} && TZ=UTC touch -a -d '{PROBE_ATIME}' /{PROBE}", 30)
    probe = rc == 0
    rc, out = root.show("bash /mnt/bs1-backup/backshot1/bs1-backup --preflight")
    check(rc == 0, "bs1-backup --preflight passes")
    mode = next((ln[7:].strip() for ln in out.splitlines() if ln.startswith("UNLOCK=")), None)
    if family in ("S", "D", "E"):
        check(mode == "drive", f"the preflight says UNLOCK=drive: {mode}")
    elif family == "own":
        check(mode == "own", f"the preflight says UNLOCK=own: {mode}")
    # An attended run's restored boots need the passphrase typed.
    typed_restore = mode == "attended" and a.luks_typed
    rc, out = root.show("bash /mnt/bs1-backup/backshot1/bs1-backup")
    check(rc == 0, "bs1-backup passes")
    run = next((ln[4:].strip() for ln in out.splitlines() if ln.startswith("RUN=")), None)
    check(run is not None, "bs1-backup names its run")
    if run:
        r = f"/mnt/bs1-backup/backshot1/runs/{run}"
        rustic = f"/mnt/bs1-backup/backshot1/bin/rustic-bko -r {r}/repo --password-file {r}/repo-password --no-progress --no-cache"
        rc, out = root.run(f"{rustic} snapshots --json 2>/dev/null", 300)
        try:
            # Grouped ({"group_key", "snapshots"}) unless nothing groups them.
            items = json.loads(out)
            paths = [p for g in items for sn in (g["snapshots"] if "snapshots" in g else [g]) for p in sn.get("paths", [])]
        except (ValueError, TypeError, AttributeError, IndexError, KeyError):
            paths = out.strip().splitlines()[-3:]
        check(paths == ["/mnt/bs1-src"], f"the snapshot is named /mnt/bs1-src, not where the tree is mounted: {paths}")
        if probe:
            rc, out = root.run(f"{rustic} cat tree latest:/mnt/bs1-src/{os.path.dirname(PROBE)} 2>/dev/null", 300)
            try:
                node = next(n for n in json.loads(out)["nodes"] if n["name"] == os.path.basename(PROBE))
                atime = node.get("atime") or ""
            except (ValueError, StopIteration, KeyError, TypeError):
                atime = f"(no node: {out.strip()[-200:]})"
            want = datetime.datetime.fromisoformat(PROBE_ATIME).replace(tzinfo=datetime.timezone.utc)
            try:
                # Whole seconds: the fraction may have more digits than Python reads.
                kept = datetime.datetime.fromisoformat(re.sub(r"\.[0-9]+", "", atime).replace("Z", "+00:00")) == want
            except ValueError:
                kept = False
            check(kept, f"the backup keeps a file's real access time (--set-atime yes): {atime}")
        else:
            print("  (no access-time probe: /var/tmp is in RAM)", flush=True)
    root.run("umount /mnt/bs1-backup; " + POWEROFF)
    vm.wait_off()
    if fails:
        print("FAILED: " + "; ".join(fails), flush=True)
        return 1

    fp = f"sudo -n sh -c 'sfdisk -d /dev/{dev} | sha256sum; blkid -p {parts} | sha256sum'"
    bs1 = "sudo -n bash /mnt/bs1-backup/backshot1/"
    restores = f"/mnt/bs1-backup/backshot1/runs/{run}/restores"
    distro = run.rsplit("-", 2)[0]
    # bs1-restore's own options: --fit-largest for --smaller, and --reformat.
    restore_opts = (" --fit-largest 10" if a.smaller else "") + "".join(f" --reformat {r}" for r in a.reformat)
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
            vm.stop()
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
            rc, out = rescue.show(f"{bs1}bs1-restore --erase-serial {SERIAL} --plan-only{restore_opts} {run}")
            check(rc == 0, "--plan-only passes")
            check(rescue.run(fp)[1] == before, "--plan-only changes nothing")
        rc, _ = rescue.show(f"{bs1}bs1-restore --erase-serial {SERIAL} --detach{restore_opts} {run}")
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

        print(f"== restored {attempt}: the internal disk, " + ("first (BIOS)" if a.bios else "first, as the source booted it" if by_firmware else "by the NVRAM the run left"), flush=True)
        # BIOS has no NVRAM to go by; nor had a source the firmware booted by its own entry.
        vm = boot(f"restored{attempt}", [internal_disk(internal, a.bios or by_firmware), usb_drive], typed_restore)
        # A ZFS root is a dataset, which lsblk can't follow; bs1-verify checks its
        # pool.  On ostree / is a composefs overlay, and the disk is /sysroot's.
        up = root.up(vm.proc, f"r=/; [ -e /run/ostree-booted ] && r=/sysroot; findmnt -no FSTYPE $r | grep -qx zfs || lsblk -snlo NAME \"$(findmnt -n -o SOURCE $r | sed 's/\\[.*//')\" | grep -qx {dev}")
        check(up, "the restored system boots " + ("by itself" if a.bios else "from the disk's removable-media loader, as the source did," if by_firmware else "from NVRAM") + " and answers")
        if up:
            check(host_key_is_pinned(), "the restored system presents the fixed host key")
            # Started by its boot this time, so it's enabled, not just running.
            check(mdns_up(), "avahi-daemon comes up at boot")
            rc, out = root.show(f"mkdir -p /mnt/bs1-backup && mount LABEL={LABEL} /mnt/bs1-backup && bash /mnt/bs1-backup/backshot1/bs1-verify {run}; rc=$?; a=$(ls -d {restores}/*[0-9] | tail -n 1); head -n 40 $a/compare.txt; grep -h . $a/lvmid-after.txt 2>/dev/null; exit $rc")
            check(rc == 0, "bs1-verify passes")
            if rc != 0:
                root.show(f"tail -n 60 $(ls -d {restores}/*[0-9] | tail -n 1)/verify.log")
            if mode == "drive":
                rc, out = unlock(root)
                check(rc == 0 and "UNLOCK=drive" in out, "the restored system was unlocked by the drive's key, nothing typed: UNLOCK=drive")
            if rc == 0 and ostree and attempt == a.restores:
                up = rollback_boots(root, vm, run, restores, host_key_is_pinned)
            if up and attempt == a.restores:
                _, out = root.run(f"ls -d {restores}/*[0-9] | wc -l; cat {restores}/*[0-9]/RESULT | grep -c PASS")
                check(out.split() == [str(a.restores), str(a.restores)], f"each of the {a.restores} restores kept its own PASS")
                root.run(f"mkdir {restores}/99990101-000000")
                rc, out = root.run(f"bash /mnt/bs1-backup/backshot1/bs1-verify {run}")
                check(rc == 2 and "didn't finish" in out, "bs1-verify refuses when the newest restore didn't finish")
                root.run(f"rmdir {restores}/99990101-000000")
                root.show("bash /mnt/bs1-backup/backshot1/bs1-list")
            root.run("umount /mnt/bs1-backup; " + POWEROFF)
        vm.wait_off()
        if fails:
            break
        if mode == "drive" and attempt == a.restores:
            print("== LUKS (f): the restored system without the drive asks, after the wait", flush=True)
            vm = boot(f"restored{attempt}-nodrive", [internal_disk(internal, a.bios or by_firmware)], True, (75, 105, 135))
            up = root.up(vm.proc, timeout=600)
            check(up, "the restored system boots without the drive, its passphrase typed at the prompt")
            if up:
                rc, out = unlock(root, local=True)
                v = volumes(out)
                check("UNLOCK=attended" in out and v and "drive" not in v.values(), f"bs1-verify --unlock: typed, not the drive: {v}")
                root.run(POWEROFF, 60)
            vm.wait_off()
    print("FAILED: " + "; ".join(fails) if fails else "PASS", flush=True)
    if a.discard_on_pass and not fails:
        for f in (internal, drive, drive_raw, stick, stick_raw, vars_file):
            os.remove(f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
