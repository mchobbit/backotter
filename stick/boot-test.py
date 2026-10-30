#!/usr/bin/env python3
"""Boot a BackOtter ISO in QEMU and check the live system from inside.

Usage: boot-test.py ISO {uefi-secureboot,uefi,bios} [--media {usb,cdrom}]
                     [--log FILE] [--timeout S]

Watches the first serial port (an ISO from "make-iso.sh --test" boots with a
console there), picks "Shell" in BackOtter's console menu, runs the checks,
and powers off.  Exits 0 if the system came up and every check passed.  It
can also log in at a plain login prompt, for ISOs without BackOtter's layer.

By default the ISO is a 16 GB USB stick, as if written to one raw: writes go
to a throwaway copy-on-write overlay, and the config partition checks run.
--media cdrom attaches it as a CD instead, which has no partitions.

Needs: qemu-system-x86_64 and qemu-img, with KVM; for the UEFI modes, OVMF (Debian/Ubuntu
package "ovmf").  uefi-secureboot uses OVMF's Secure Boot build with
Microsoft's keys enrolled, so it boots the ISO's signed shim and kernel the
way a stock PC with Secure Boot on would.
"""

from __future__ import annotations

import argparse
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time

OVMF = "/usr/share/OVMF"
FIRMWARE = {
    "uefi-secureboot": ("OVMF_CODE_4M.secboot.fd", "OVMF_VARS_4M.ms.fd"),
    "uefi": ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"),
}
# Set by the casper.conf inside the server ISO's initrd (the installer layer's
# /etc/casper.conf says "ubuntu", but that copy isn't the one casper reads).
LIVE_USER = "ubuntu-server"
PROMPT = re.compile(rb"[$#] $")
MENU = re.compile(rb"Choose \[1-4\]: ")
LOGIN = re.compile(rb"login: ")
FAILURE = re.compile(rb"Kernel panic|\(initramfs\) |BusyBox v[0-9]")
# Computed by the guest, so the command's own echo can't match it.
DONE = re.compile(rb"BO-42-DONE\r?\n")

# Each check prints one line: "BO-CHECK <name> <ok|FAIL> <detail>".
CHECKS = r"""
check() { n=$1; shift; if out=$(sh -c "$*" 2>&1); then r=ok; else r=FAIL; fi
          echo "BO-CHECK $n $r $(echo "$out" | tr '\n' ' ' | cut -c1-200)"; }
check kernel 'uname -r'
check layers 'grep -o "layerfs-path=[^ ]*" /proc/cmdline'
check hostname 'test "$(hostname)" = backotter && hostname'
check rootfs 'findmnt -n -o FSTYPE /'
check secureboot 'od -An -t u1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c 2>/dev/null | awk "{print (\$5==1 ? \"enabled\" : \"disabled\")}" | grep . || echo "no EFI (BIOS boot)"'
check snapd-gone '! dpkg-query -W -f="\${db:Status-Status}" snapd 2>/dev/null | grep -q "^installed"'
check llvm-gone '! ls /usr/lib/x86_64-linux-gnu/libLLVM* >/dev/null 2>&1'
check arm-firmware-gone 'test ! -e /usr/lib/firmware/qcom/x1e80100 && test ! -e /usr/lib/firmware/nvidia/tegra194'
check modem-firmware-kept 'ls -d /usr/lib/firmware/qcom/sdx61'
check switch-firmware 'ls /usr/lib/firmware/mellanox 2>/dev/null | wc -l | sed "s/$/ mellanox files/"'
check tools 'for c in python3 lvm mdadm cryptsetup sgdisk mkfs.ext4 mkfs.xfs mkfs.btrfs ssh; do command -v $c >/dev/null || { echo missing $c; exit 1; }; done; echo all present'
check python 'python3 -c "import sys; print(sys.version.split()[0])"'
check layer-tools 'for c in wpa_supplicant efibootmgr zpool zfs sshd; do command -v $c >/dev/null || { echo missing $c; exit 1; }; done; echo all present'
check dpkg-audit 'test -z "$(dpkg --audit)"'
check zfs-no-autoimport 'for u in zfs-import-cache zfs-import-scan zfs-mount; do s=$(systemctl is-enabled $u.service 2>/dev/null); test "$s" = masked || { echo "$u: $s"; exit 1; }; done; echo masked'
check wait-online-masked 'test "$(systemctl is-enabled systemd-networkd-wait-online.service 2>/dev/null)" = masked'
check cloud-init-off 'test -e /etc/cloud/cloud-init.disabled'
check cdrom-source-gone 'test ! -e /etc/apt/sources.list.d/cdrom.sources'
check network 'ip -brief -4 address show scope global | grep .'
check tty1-console 'systemctl cat getty@tty1.service | grep -q "login-program /usr/lib/backotter/console"'
check tty2-root-shell 'systemctl cat autovt@tty2.service | grep -q "autologin root"'
check sshd-off-at-boot '! systemctl is-active -q ssh.service && ! systemctl is-active -q ssh.socket && ! ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1'
check remote-access '/usr/lib/backotter/remote-access >/run/ra.out 2>&1 || { tail -n 3 /run/ra.out; exit 1; }; pw=$(sed -n "s/^Password: //p" /run/backotter/remote-access); printf "#!/bin/sh\necho %s\n" "$pw" >/root/.askpass; chmod 700 /root/.askpass; who=$(SSH_ASKPASS=/root/.askpass SSH_ASKPASS_REQUIRE=force setsid -w ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null backotter@127.0.0.1 whoami </dev/null 2>/run/ssh.err); rm -f /root/.askpass; if test "$who" = backotter; then echo "password login over SSH works"; else tail -n 3 /run/ssh.err; journalctl -u ssh -n 4 --no-pager -o cat; exit 1; fi'
check root-ssh-refused '! SSH_ASKPASS=/bin/false SSH_ASKPASS_REQUIRE=force setsid -w ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o NumberOfPasswordPrompts=1 root@127.0.0.1 true </dev/null 2>/dev/null'
check failed-units 'n=$(systemctl --failed --no-legend | wc -l); systemctl --failed --no-legend | cut -c1-60; test $n -eq 0'
check marker 'cd /cdrom && grep "  ./.disk/backotter.json$" md5sum.txt | md5sum -c --quiet && python3 -c "import json; m = json.load(open(\".disk/backotter.json\")); print(m[\"image_id\"], m[\"platform\"], \"zfs\", m[\"zfs\"][\"version\"], len(m[\"zfs\"][\"features\"]), \"features\")"'
check memory 'free -m | awk "/Mem:/{print \$3 \" MB used of \" \$2}"'
"""

# Finds the config partition the way docs/design/stick-config.md says: on the
# disk casper mounted at /cdrom only, as the marker describes it.  "rw" also
# checks stick.json, writes a file and reads it back after remounting.
CONFIG_CHECKS = r"""
cat >/run/bo-config.sh <<'EOF'
src=$(findmnt -n -o SOURCE /cdrom) || { echo "nothing mounted on /cdrom"; exit 1; }
parent=$(lsblk -no PKNAME "$src" | head -n 1)
disk=/dev/${parent:-${src#/dev/}}
eval "$(python3 -c 'import json; m = json.load(open("/cdrom/.disk/backotter.json")); p = m["config_partition"]; print("num=%s sectors=%s label=%s serial=%s image_id=%s" % (p["number"], p["sectors"], p["label"], p["fat_serial"], m["image_id"]))')"
part=
for f in /sys/class/block/${disk#/dev/}/*/partition; do
  test "$(cat "$f")" = "$num" && part=/dev/$(basename "$(dirname "$f")")
done
test -n "$part" || { echo "no partition $num on $disk (/cdrom is $src)"; exit 1; }
test "$(cat /sys/class/block/${part#/dev/}/size)" = "$sectors" || { echo "$part: not $sectors sectors"; exit 1; }
test "$(blkid -o value -s TYPE "$part")" = vfat || { echo "$part: not FAT"; exit 1; }
test "$(blkid -o value -s LABEL "$part")" = "$label" || { echo "$part: label is not $label"; exit 1; }
test "$(blkid -o value -s UUID "$part")" = "$serial" || { echo "$part: serial is not $serial"; exit 1; }
case ${1:-find} in
find) echo "/cdrom is $src; config is $part, $label $serial" ;;
rw)
  mnt=/run/bo-cfg; mkdir -p $mnt
  mount -o flush,nosuid,nodev,noexec "$part" $mnt || exit 1
  got=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["image_id"])' $mnt/backotter/stick.json)
  test "$got" = "$image_id" || { umount $mnt; echo "stick.json is for image $got"; exit 1; }
  stamp=$(date -u +%FT%TZ)
  echo "$stamp" >$mnt/backotter/boot-test.txt && sync && umount $mnt || exit 1
  mount -o ro "$part" $mnt || exit 1
  test "$(cat $mnt/backotter/boot-test.txt)" = "$stamp"; r=$?
  ls $mnt/backotter | tr "\n" " "; umount $mnt; exit $r ;;
settings)
  mnt=/run/bo-cfg; mkdir -p $mnt
  mount -o ro "$part" $mnt || exit 1
  python3 - $mnt/backotter/settings.toml <<'PY'; r=$?
import sys, tomllib
raw = open(sys.argv[1], "rb").read().decode()
s = tomllib.loads(raw)
assert (s["format"], s["schema"]) == ("backotter-settings", 1), s
# What the comments tell a user to do, in an editor that adds a byte-order mark.
edited = "\n".join(l[2:] if l.startswith(("# name", "# [machines", "# repository_url")) else l
                   for l in raw.splitlines())
e = tomllib.loads(("﻿" + edited).encode().decode("utf-8-sig"))
assert e["name"] == "drawer stick" and len(e["machines"]) == 1, e
print("parses as shipped and as edited per its comments")
PY
  umount $mnt; exit $r ;;
esac
EOF
check config-partition 'sh /run/bo-config.sh find'
check config-rw 'sh /run/bo-config.sh rw'
check config-settings 'sh /run/bo-config.sh settings'
"""
DONE_LINE = "echo BO-$((6 * 7))-DONE"


class Console:
    """The guest's serial port, read through QEMU's stdout."""

    def __init__(self, proc: subprocess.Popen[bytes], log) -> None:
        self.proc = proc
        self.log = log
        self.buf = b""

    def expect(self, *patterns: re.Pattern[bytes], timeout: float) -> int:
        """Wait until one of the patterns matches; return its index."""
        deadline = time.monotonic() + timeout
        assert self.proc.stdout is not None
        while True:
            for i, pat in enumerate(patterns):
                m = pat.search(self.buf)
                if m:
                    self.buf = self.buf[m.end():]
                    return i
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no match for {[p.pattern for p in patterns]}")
            ready, _, _ = select.select([self.proc.stdout], [], [], min(left, 1.0))
            if ready:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise EOFError("QEMU exited")
                self.log.write(chunk)
                self.log.flush()
                self.buf = (self.buf + chunk)[-65536:]

    def send(self, text: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(text.encode())
        self.proc.stdin.flush()


def qemu_command(iso: str, mode: str, media: str, tmp: str) -> list[str]:
    cmd = [
        "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "4096",
        "-display", "none", "-serial", "stdio", "-monitor", "none",
    ]
    if media == "cdrom":
        cmd += ["-drive", f"file={iso},media=cdrom,readonly=on"]
    else:
        # A stick bigger than the image, like a real one: the image's backup
        # GPT header then isn't at the end of the disk.
        overlay = os.path.join(tmp, "stick.qcow2")
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "raw",
                        "-b", os.path.abspath(iso), overlay, "16G"], check=True)
        cmd += [
            "-device", "qemu-xhci,id=xhci",
            "-drive", f"if=none,id=stick,format=qcow2,file={overlay}",
            "-device", "usb-storage,bus=xhci.0,drive=stick,bootindex=1",
        ]
    if mode == "bios":
        return cmd + ["-machine", "q35"]
    code, vars_template = FIRMWARE[mode]
    vars_copy = os.path.join(tmp, "OVMF_VARS.fd")
    shutil.copy(os.path.join(OVMF, vars_template), vars_copy)
    cmd += [
        "-machine", "q35,smm=on",
        "-global", "driver=cfi.pflash01,property=secure,value=on",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={os.path.join(OVMF, code)}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_copy}",
    ]
    return cmd


def run(iso: str, mode: str, media: str, log_path: str, timeout: float, user: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp, open(log_path, "wb") as log:
        proc = subprocess.Popen(qemu_command(iso, mode, media, tmp),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        con = Console(proc, log)
        start = time.monotonic()
        try:
            got = con.expect(MENU, LOGIN, FAILURE, timeout=timeout)
            if got == 2:
                print(f"{mode}: boot FAILED (see {log_path})")
                return False
            boot_s = time.monotonic() - start
            if got == 1:
                # Either agetty's "login: root (automatic login)" on the way
                # to BackOtter's menu or a root shell, or a real login prompt.
                try:
                    got = con.expect(MENU, PROMPT, timeout=20)
                except TimeoutError:
                    got = None
            as_root = True
            if got == 0:
                con.send("1\n")   # BackOtter's console: "Shell"
                con.expect(PROMPT, timeout=30)
            elif got is None:
                con.send(user + "\n")
                if con.expect(re.compile(rb"Password: $"), PROMPT, timeout=30) == 0:
                    con.send("\n")   # casper's live user has an empty password
                    con.expect(PROMPT, timeout=30)
                as_root = False
            con.send("export TERM=dumb; stty -echo" + ("" if as_root else "; sudo -i") + "\n")
            con.expect(PROMPT, timeout=30)
            # A line at a time: the guest's tty buffers only 4 KB of input.
            script = CHECKS + (CONFIG_CHECKS if media == "usb" else "") + DONE_LINE
            for line in script.strip().splitlines():
                con.send(line + "\n")
                time.sleep(0.1)
            con.expect(DONE, timeout=120)
            results = re.findall(rb"BO-CHECK (\S+) (ok|FAIL) ?(.*?)\r?\n",
                                 log_contents(log_path))
            print(f"{mode} ({media}): reached login in {boot_s:.0f} s")
            all_ok = bool(results)
            for name, status, detail in results:
                all_ok &= status == b"ok"
                print(f"  {status.decode():4} {name.decode():20} {detail.decode(errors='replace').strip()}")
            con.send("poweroff\n")
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                pass
            return all_ok
        except (TimeoutError, EOFError) as e:
            print(f"{mode}: {e} (see {log_path})")
            return False
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def log_contents(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("iso")
    ap.add_argument("mode", choices=["uefi-secureboot", "uefi", "bios"])
    ap.add_argument("--media", choices=["usb", "cdrom"], default="usb",
                    help="attach the ISO as a USB stick (default) or a CD")
    ap.add_argument("--log", help="serial log file (default: boot-test-MODE-MEDIA.log)")
    ap.add_argument("--timeout", type=float, default=300, help="seconds to reach login")
    ap.add_argument("--user", default=LIVE_USER, help=f"live user (default: {LIVE_USER})")
    args = ap.parse_args()
    ok = run(args.iso, args.mode, args.media,
             args.log or f"boot-test-{args.mode}-{args.media}.log", args.timeout, args.user)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
