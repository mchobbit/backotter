"""ARM64 A5 (docs/design/arm64.md): the guest builders' --arch arm64, with amd64's kept as it was,
and the vm-* tools' arm64 paths.

The builders (make-debian.sh, make-ubuntu.sh, make-fedora.sh, make-bootstrap.sh) run in a
temporary tree with every outside program they call stubbed (wget, gpg, gpgv, sha256sum,
xorriso, qemu-img, openssl, ssh-keygen, uname, QEMU, and vm-run, which on arm64 does the
install): what each would download, the install's command line, the answers it hands the
installer (the preseed, user-data, kickstart, install script and Alpine's overlay) and the
vm-run-options it writes.  amd64's QEMU command lines are what each builder ran before A5.
make-debian-zfs.sh needs root, so only its refusals are tested.  vm-update's dump_plat (which
bko-holt an arm64 guest's dump takes), and vm-chroot's refusal of a guest of another
architecture (its root check taken out, chroot and mount stubbed).

The builders need bash 4, GNU timeout, cpio and gzip: under the Mac's tools these are skipped
(run them on a Linux host, e.g. Bigfoot or a farm host).  No VM, no network."""

import gzip
import importlib.machinery
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)


def bash_major():
    try:
        out = subprocess.run(["bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def gnu_tools():
    if bash_major() < 4:
        return False
    for t in ("timeout", "cpio", "gzip"):
        if not shutil.which(t):
            return False
    r = subprocess.run(["timeout", "--version"], capture_output=True, text=True)
    return r.returncode == 0 and "GNU" in r.stdout


def has_yaml():
    return subprocess.run([sys.executable, "-c", "import yaml"], capture_output=True).returncode == 0


# QEMU and vm-run: their words to W/run.N (one file a run), the folder of their -initrd (the
# builder's work folder, with the answers in it) copied to W/work.N, and BACKOTTER-INSTALL-OK
# in the serial log, as make-bootstrap.sh's install script prints it.
RECORD = r'''#!/bin/bash
n=$(ls "$W" | grep -c '^run\.')
{ echo "$(basename "$0")"; echo "SSH_PORT=${SSH_PORT:-}"; printf '%s\n' "$@"; } >"$W/run.$n"
prev="" log=""
for a; do
    [[ $prev == -initrd ]] && cp -a "$(dirname "$a")" "$W/work.$n"
    [[ $prev == --serial-log ]] && log=$a
    [[ $prev == -serial && $a == file:* ]] && log=${a#file:}
    prev=$a
done
[[ -z $log ]] || echo BACKOTTER-INSTALL-OK >>"$log"
'''

STUBS = {
    "uname": '#!/bin/bash\n[[ ${1:-} == -m ]] && { echo "$FAKE_M"; exit 0; }\nexec /bin/uname "$@"\n',
    # wget: each URL to W/wget.log; -O - and a checksum list print W/sums.txt; -O FILE
    # writes a placeholder.
    "wget": r'''#!/bin/bash
out="" url=""
while [[ $# -gt 0 ]]; do
    case $1 in -O) out=$2; shift 2 ;; -*) shift ;; *) url=$1; shift ;; esac
done
echo "$url" >>"$W/wget.log"
case ${url##*/} in *SUMS*|*sums*|*CHECKSUM*) sums=yes ;; *) sums=no ;; esac
if [[ $out == - ]]; then cat "$W/sums.txt"
elif [[ -n $out && $sums == yes ]]; then cat "$W/sums.txt" >"$out"
elif [[ -n $out ]]; then echo "fetched $url" >"$out"; fi
''',
    "gpg": r'''#!/bin/bash
for a; do
    [[ $a == --export ]] && { echo key; exit 0; }
    [[ $a == --show-keys ]] && { echo "fpr:::::::::$FAKE_FPR:"; exit 0; }
done
exit 0
''',
    "gpgv": r'''#!/bin/bash
out=""
while [[ $# -gt 1 ]]; do [[ $1 == --output ]] && { out=$2; shift; }; shift; done
[[ -z $out ]] || cp "$1" "$out"
''',
    "sha256sum": '#!/bin/bash\ncat >/dev/null\n',
    "openssl": r'''#!/bin/bash
case $1 in
    rand) echo c2VjcmV0c2VjcmV0c2VjcmV0 ;;
    passwd) cat >/dev/null; echo '$6$salt$hash' ;;
esac
''',
    "ssh-keygen": r'''#!/bin/bash
while [[ $# -gt 0 ]]; do [[ $1 == -f ]] && f=$2; shift; done
echo private >"$f"; echo "ssh-ed25519 AAAAtest root@test" >"$f.pub"
''',
    "qemu-img": r'''#!/bin/bash
case $1 in
    create) shift; while [[ $1 == -* ]]; do [[ $1 == -f ]] && shift; shift; done; : >"$1" ;;
    info) echo '{"format": "qcow2", "actual-size": 600000000}' ;;
esac
exit 0
''',
    # xorriso: -extract SRC DST pairs, with what each builder reads from its ISO.
    "xorriso": r'''#!/bin/bash
prev=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    a=${args[$i]}
    if [[ $a == -o ]]; then : >"${args[$((i + 1))]}"; fi
    if [[ $a == -extract ]]; then
        src=${args[$((i + 1))]} dst=${args[$((i + 2))]}
        mkdir -p "$(dirname "$dst")"
        case $src in
            /loader/entries) mkdir -p "$dst"
                printf 'linux /arch/boot/x86_64/vmlinuz-linux\ninitrd /arch/boot/x86_64/initramfs-linux.img\noptions archisobasedir=arch archisosearchuuid=X\n' >"$dst/01-archiso.conf" ;;
            /EFI/BOOT/grub.cfg) echo "linux /images/pxeboot/vmlinuz inst.stage2=hd:LABEL=Fedora-S-dvd-$FAKE_FEDARCH-44 quiet" >"$dst" ;;
            /.disk/info) echo 'Ubuntu-Server 26.04.1 LTS "Resolute" - Release arm64 (20260820)' >"$dst" ;;
            /boot/grub/grub.cfg) printf 'set timeout=1\n\nmenuentry "Linux virt" {\nlinux\t/boot/vmlinuz-virt modules=loop,squashfs,sd-mod,usb-storage quiet console=tty0 console=ttyAMA0\ninitrd\t/boot/initramfs-virt\n}\n' >"$dst" ;;
            *) echo "from $src" | gzip >"$dst" ;;
        esac
    fi
done
exit 0
''',
    "qemu-system-x86_64": RECORD,
    "qemu-system-aarch64": RECORD,
}


@unittest.skipUnless(gnu_tools(), "the builders need bash 4, GNU timeout, cpio and gzip (a Linux host)")
class Builders(unittest.TestCase):
    def setUp(self):
        self.w = tempfile.mkdtemp(prefix="t2-a5-")
        self.addCleanup(shutil.rmtree, self.w, True)
        w = self.w
        self.tv = os.path.join(w, "testvm")
        os.makedirs(self.tv)
        # OVMF (amd64's --uefi) from a folder of this test's.
        os.makedirs(os.path.join(w, "fw", "OVMF"))
        for f in ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"):
            self.put(os.path.join(w, "fw", "OVMF", f), "fw\n")
        for f in ("make-debian.sh", "make-ubuntu.sh", "make-fedora.sh", "make-bootstrap.sh", "make-debian-zfs.sh"):
            with open(os.path.join(TESTVM, f)) as src:
                self.put(os.path.join(self.tv, f), src.read().replace("/usr/share/OVMF", f"{w}/fw/OVMF"), 0o755)
        for d in ("debian", "ubuntu", "fedora", "bootstrap"):
            shutil.copytree(os.path.join(TESTVM, d), os.path.join(self.tv, d))
        self.put(os.path.join(self.tv, "vm-run"), RECORD, 0o755)
        os.makedirs(os.path.join(w, "stub"))
        os.makedirs(os.path.join(w, "tmp"))
        for n, text in STUBS.items():
            self.put(os.path.join(w, "stub", n), text, 0o755)

    def put(self, path, text, mode=0o644):
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, mode)

    def read(self, *rel):
        with open(os.path.join(self.w, *rel)) as f:
            return f.read()

    # Every checksum list's lines for every ISO the builders fetch, in each list's format.
    SUMS = "".join(f"{'0' * 64}  {i}\n" for i in (
        "debian-13.0.0-amd64-DVD-1.iso", "debian-13.0.0-arm64-DVD-1.iso", "archlinux-2026.10.01-x86_64.iso")) + \
        "".join(f"{'0' * 64} *ubuntu-26.04.1-live-server-{a}.iso\n" for a in ("amd64", "arm64")) + \
        "".join(f"SHA256 (Fedora-Server-dvd-{a}-44-1.7.iso) = {'0' * 64}\n" for a in ("x86_64", "aarch64"))

    def build(self, script, *args, m="aarch64", sums=SUMS, fpr="", fedarch="aarch64"):
        self.put(os.path.join(self.w, "sums.txt"), sums)
        e = {k: v for k, v in os.environ.items() if k not in ("SSH_PORT",)}
        e.update(PATH=os.path.join(self.w, "stub") + os.pathsep + e["PATH"], W=self.w, FAKE_M=m, FAKE_FPR=fpr,
                 FAKE_FEDARCH=fedarch, TMPDIR=os.path.join(self.w, "tmp"))
        return subprocess.run(["bash", os.path.join(self.tv, script), *args], capture_output=True, text=True, env=e,
                              cwd=self.w, stdin=subprocess.DEVNULL, timeout=120)

    def runs(self):
        """Each recorded run: (program, SSH_PORT, words), normalized: the tree as W, the work
        folder as TMP, ports as PORT."""
        out = []
        for n in range(len([f for f in os.listdir(self.w) if f.startswith("run.")])):
            lines = self.read(f"run.{n}").splitlines()
            words = [self.norm(x) for x in lines[2:]]
            out.append((lines[0], lines[1], words))
        return out

    def norm(self, s):
        s = re.sub(re.escape(os.path.join(self.w, "tmp")) + r"/tmp\.\w+", "TMP", s)
        s = s.replace(self.w, "W")
        return re.sub(r"(10\.0\.2\.2|127\.0\.0\.1):\d+", r"\1:PORT", s)

    def options(self, guest_dir):
        p = os.path.join(self.w, guest_dir, "vm-run-options")
        return self.read(guest_dir, "vm-run-options") if os.path.exists(p) else None

    # --- make-debian.sh --------------------------------------------------------------

    def preseed(self, n=0):
        """The preseed appended to the installer's initrd (its last gzip member, a newc cpio)."""
        with open(os.path.join(self.w, f"work.{n}", "initrd.gz"), "rb") as f:
            data = f.read()
        members, rest = [], data
        while rest:
            d = __import__("zlib").decompressobj(31)
            members.append(d.decompress(rest))
            rest = d.unused_data
        cpio = members[-1]
        i = cpio.index(b"preseed.cfg\0")
        start = (i + len("preseed.cfg") + 1 + 3) & ~3
        size = int(cpio[i - 110 + 54:i - 110 + 62], 16)
        return cpio[start:start + size].decode()

    def test_debian_amd64_as_before(self):
        r = self.build("make-debian.sh", "13.0.0", "btrfs", "vms/debian13", m="x86_64")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.runs(), [("qemu-system-x86_64", "SSH_PORT=", [
            "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "2048", "-machine", "pc",
            "-drive", "file=debian13.qcow2,if=virtio,format=qcow2",
            "-drive", "file=debian-13.0.0-amd64-DVD-1.iso,media=cdrom,readonly=on",
            "-nic", "user,model=virtio-net-pci", "-kernel", "TMP/vmlinuz", "-initrd", "TMP/initrd.gz",
            "-append", "auto=true priority=critical DEBIAN_FRONTEND=text console=ttyS0,115200n8 --- console=tty0 "
                       "console=ttyS0,115200n8",
            "-display", "none", "-serial", "file:install-serial.log", "-monitor", "none", "-no-reboot"])])
        self.assertIn("https://cdimage.debian.org/cdimage/archive/13.0.0/amd64/iso-dvd/debian-13.0.0-amd64-DVD-1.iso",
                      self.read("wget.log"))
        p = self.preseed()
        self.assertIn("d-i base-installer/kernel/image string linux-image-amd64", p)
        self.assertIn("d-i partman-partitioning/default_label string msdos", p)
        self.assertNotIn("method{ efi }", p)
        self.assertIsNone(self.options("vms/debian13"))

    def test_debian_arm64(self):
        r = self.build("make-debian.sh", "--arch", "arm64", "13.0.0", "ext4", "vms/debian13-arm64")
        self.assertEqual(r.returncode, 0, r.stderr)
        (prog, port, words), = self.runs()
        self.assertEqual(prog, "vm-run")
        self.assertRegex(port, r"^SSH_PORT=\d+$")
        self.assertEqual(words, [
            "--uefi", "--no-secure-boot", "--memory", "2048", "--serial-log", "install-serial.log",
            "--cdrom", "debian-13.0.0-arm64-DVD-1.iso", "debian13-arm64.qcow2", "--",
            "-kernel", "TMP/vmlinuz", "-initrd", "TMP/initrd.gz",
            "-append", "auto=true priority=critical DEBIAN_FRONTEND=text console=ttyAMA0,115200n8 --- "
                       "console=ttyAMA0,115200n8", "-no-reboot"])
        wl = self.read("wget.log")
        self.assertIn("https://cdimage.debian.org/cdimage/archive/13.0.0/arm64/iso-dvd/debian-13.0.0-arm64-DVD-1.iso", wl)
        self.assertNotIn("amd64", wl)
        p = self.preseed()
        self.assertIn("d-i base-installer/kernel/image string linux-image-arm64", p)
        self.assertNotIn("amd64", p)
        self.assertIn("d-i partman-partitioning/default_label string gpt", p)
        self.assertIn("method{ efi } format{ }", p)
        self.assertIn("d-i grub-installer/force-efi-extra-removable boolean true", p)
        self.assertEqual(p.count("partman-auto/expert_recipe"), 1)
        self.assertIn("filesystem{ ext4 } \\\n            mountpoint{ / }", p)
        self.assertNotIn("$primary{ }", p)
        self.assertIn("d-i netcfg/get_hostname string deb13-arm64", p)
        self.assertNotRegex(p, r"@[A-Z_]+@")
        self.assertEqual(self.options("vms/debian13-arm64"), "--uefi --no-secure-boot\n")

    def test_debian_refusals(self):
        for args, m, msg in (
                (["--arch", "arm64", "13.0.0", "ext4", "vms/x"], "x86_64", "builds on an aarch64 host"),
                (["--arch", "arm64", "--kali", "2026.3", "ext4", "vms/x"], "aarch64", "Debian only"),
                (["--arch", "arm64", "--devuan", "6.1.1", "ext3", "vms/x"], "aarch64", "Debian only"),
                (["--arch", "riscv64", "13.0.0", "ext4", "vms/x"], "aarch64", "takes amd64 or arm64")):
            r = self.build("make-debian.sh", *args, m=m)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn(msg, r.stderr, args)
            self.assertFalse(os.path.exists(os.path.join(self.w, "wget.log")), args)

    # --- make-ubuntu.sh --------------------------------------------------------------

    @unittest.skipUnless(has_yaml(), "make-ubuntu.sh checks user-data with python3's yaml")
    def test_ubuntu_amd64_as_before(self):
        r = self.build("make-ubuntu.sh", "--uefi", "--name", "ubuntu26-uefi", "26.04.1", "vms/u", m="x86_64")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.runs(), [("qemu-system-x86_64", "SSH_PORT=", [
            "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "2048", "-machine", "q35",
            "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=W/fw/OVMF/OVMF_CODE_4M.fd",
            "-drive", "if=pflash,format=raw,unit=1,file=TMP/vars.fd",
            "-drive", "file=ubuntu26-uefi.qcow2,if=virtio,format=qcow2",
            "-drive", "file=ubuntu-26.04.1-live-server-amd64.iso,media=cdrom,readonly=on",
            "-drive", "file=TMP/cidata.iso,media=cdrom,readonly=on", "-nic", "none",
            "-kernel", "TMP/vmlinuz", "-initrd", "TMP/initrd",
            "-append", "autoinstall console=tty0 console=ttyS0,115200n8",
            "-display", "none", "-serial", "file:install-serial.log", "-monitor", "none", "-no-reboot"])])
        self.assertIn("https://releases.ubuntu.com/26.04/SHA256SUMS", self.read("wget.log"))
        self.assertIn("console=ttyS0,115200n8", self.read("work.0", "cidata", "user-data"))
        self.assertEqual(self.options("vms/u"), "--uefi\n")

    @unittest.skipUnless(has_yaml(), "make-ubuntu.sh checks user-data with python3's yaml")
    def test_ubuntu_amd64_bios_as_before(self):
        r = self.build("make-ubuntu.sh", "26.04.1", "vms/u", m="x86_64")
        self.assertEqual(r.returncode, 0, r.stderr)
        (prog, _, words), = self.runs()
        self.assertEqual((prog, words[:9]), ("qemu-system-x86_64", [
            "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "2048", "-machine", "pc"]))
        self.assertIn("file=ubuntu26.qcow2,if=virtio,format=qcow2", words)
        self.assertIn("rpool", self.read("work.0", "cidata", "user-data"))
        self.assertIsNone(self.options("vms/u"))

    @unittest.skipUnless(has_yaml(), "make-ubuntu.sh checks user-data with python3's yaml")
    def test_ubuntu_arm64(self):
        r = self.build("make-ubuntu.sh", "--arch", "arm64", "--uefi", "--name", "ubuntu26-arm64", "26.04.1", "vms/u")
        self.assertEqual(r.returncode, 0, r.stderr)
        (prog, port, words), = self.runs()
        self.assertEqual(prog, "vm-run")
        self.assertEqual(port, "SSH_PORT=")          # no network card at all
        self.assertEqual(words, [
            "--uefi", "--no-secure-boot", "--memory", "2048", "--qemu-options", "TMP/qemu-options",
            "--serial-log", "install-serial.log", "--cdrom", "ubuntu-26.04.1-live-server-arm64.iso",
            "ubuntu26-arm64.qcow2", "--",
            "-drive", "if=none,id=cidata,file=TMP/cidata.iso,media=cdrom,readonly=on",
            "-device", "virtio-scsi-pci,id=cidatascsi", "-device", "scsi-cd,drive=cidata,bus=cidatascsi.0",
            "-kernel", "TMP/vmlinuz", "-initrd", "TMP/initrd",
            "-append", "autoinstall console=tty0 console=ttyAMA0,115200n8", "-no-reboot"])
        self.assertEqual(self.read("work.0", "qemu-options"), "-nic none\n")
        wl = self.read("wget.log")
        self.assertIn("https://cdimage.ubuntu.com/releases/26.04.1/release/SHA256SUMS", wl)
        self.assertIn("https://cdimage.ubuntu.com/releases/26.04.1/release/ubuntu-26.04.1-live-server-arm64.iso", wl)
        self.assertNotIn("releases.ubuntu.com", wl.replace("cdimage.ubuntu.com/releases", ""))
        ud = self.read("work.0", "cidata", "user-data")
        self.assertIn('GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyAMA0,115200n8"', ud)
        self.assertNotIn("ttyS0", ud)
        self.assertIn("hostname: ubuntu26-arm64", ud)
        self.assertIn("name: direct", ud)
        # Secure Boot on, with AAVMF's Microsoft keys (Decision 10).
        self.assertEqual(self.options("vms/u"), "--uefi\n")
        # --uefi is implied, and the name defaults to -arm64.
        r = self.build("make-ubuntu.sh", "--arch", "arm64", "26.04.1", "vms/u2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.w, "vms", "u2", "ubuntu26-arm64.qcow2")))

    def test_ubuntu_refusals(self):
        for args, m, msg in (
                (["--arch", "arm64", "26.04.1", "vms/x"], "x86_64", "builds on an aarch64 host"),
                (["--arch", "arm64", "--zfs-mirror", "26.04.1", "vms/x"], "aarch64", "BIOS only"),
                (["--arch", "arm64", "--uefi", "--luks", "26.04.1", "vms/x"], "aarch64", "direct layout only"),
                (["--arch", "arm64", "--uefi", "--zfs-guided", "26.04.1", "vms/x"], "aarch64", "direct layout only"),
                (["--arch", "arm64", "--uefi", "--raid", "26.04.1", "vms/x"], "aarch64", "direct layout only"),
                (["--arch", "arm64", "--name", "ubuntu26", "26.04.1", "vms/x"], "aarch64", "ends in -arm64")):
            r = self.build("make-ubuntu.sh", *args, m=m)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn(msg, r.stderr, args)
            self.assertFalse(os.path.exists(os.path.join(self.w, "wget.log")), args)

    # --- make-fedora.sh --------------------------------------------------------------

    FEDORA_KEY = "36F612DCF27F7D1A48A835E4DBFCF71C6D9F90A6"

    def test_fedora_amd64_as_before(self):
        r = self.build("make-fedora.sh", "44-1.7", "vms/f", m="x86_64", fpr=self.FEDORA_KEY, fedarch="x86_64")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.runs(), [("qemu-system-x86_64", "SSH_PORT=", [
            "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "2048", "-machine", "pc",
            "-drive", "file=fedora44.qcow2,if=virtio,format=qcow2",
            "-drive", "file=Fedora-Server-dvd-x86_64-44-1.7.iso,media=cdrom,readonly=on",
            "-nic", "user,model=virtio-net-pci", "-kernel", "TMP/vmlinuz", "-initrd", "TMP/initrd.img",
            "-append", "inst.stage2=hd:LABEL=Fedora-S-dvd-x86_64-44 inst.ks=http://10.0.2.2:PORT/ks.cfg inst.text "
                       "console=tty0 console=ttyS0,115200n8",
            "-display", "none", "-serial", "file:install-serial.log", "-monitor", "none", "-no-reboot"])])
        wl = self.read("wget.log")
        self.assertIn("https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Server/x86_64/iso/"
                      "Fedora-Server-44-1.7-x86_64-CHECKSUM", wl)
        ks = self.read("work.0", "www", "ks.cfg")
        self.assertIn("\n-mcelog\n", ks)
        self.assertIn("console=ttyS0,115200n8", ks)
        self.assertIsNone(self.options("vms/f"))

    def test_fedora_arm64(self):
        r = self.build("make-fedora.sh", "--arch", "arm64", "--uefi", "--name", "fedora44-arm64", "44-1.7", "vms/f",
                       fpr=self.FEDORA_KEY)
        self.assertEqual(r.returncode, 0, r.stderr)
        (prog, port, words), = self.runs()
        self.assertEqual(prog, "vm-run")
        self.assertRegex(port, r"^SSH_PORT=\d+$")
        self.assertEqual(words, [
            "--uefi", "--no-secure-boot", "--memory", "2048", "--serial-log", "install-serial.log",
            "--cdrom", "Fedora-Server-dvd-aarch64-44-1.7.iso", "fedora44-arm64.qcow2", "--",
            "-kernel", "TMP/vmlinuz", "-initrd", "TMP/initrd.img",
            "-append", "inst.stage2=hd:LABEL=Fedora-S-dvd-aarch64-44 inst.ks=http://10.0.2.2:PORT/ks.cfg inst.text "
                       "console=tty0 console=ttyAMA0,115200n8", "-no-reboot"])
        wl = self.read("wget.log")
        self.assertIn("https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Server/aarch64/iso/"
                      "Fedora-Server-44-1.7-aarch64-CHECKSUM", wl)
        self.assertIn("/Fedora-Server-dvd-aarch64-44-1.7.iso", wl)
        self.assertNotIn("x86_64", wl)
        ks = self.read("work.0", "www", "ks.cfg")
        self.assertNotIn("-mcelog", ks)
        self.assertNotIn("ttyS0", ks)
        self.assertIn('bootloader --boot-drive=vda --append="console=tty0 console=ttyAMA0,115200n8"', ks)
        self.assertIn("autopart --type=btrfs --noswap", ks)
        self.assertIn("--hostname=fedora44-arm64", ks)
        self.assertEqual(self.options("vms/f"), "--uefi --no-secure-boot\n")

    def test_fedora_refusals(self):
        for args, m, msg in (
                (["--arch", "arm64", "44-1.7", "vms/x"], "x86_64", "builds on an aarch64 host"),
                (["--arch", "arm64", "--uefi", "--raid", "44-1.7", "vms/x"], "aarch64", "--uefi's layout only"),
                (["--arch", "arm64", "--ostree", "quay.io/fedora/fedora-kinoite:44", "44-1.7", "vms/x"], "aarch64",
                 "--uefi's layout only"),
                (["--arch", "arm64", "--name", "fedora44", "44-1.7", "vms/x"], "aarch64", "ends in -arm64")):
            r = self.build("make-fedora.sh", *args, m=m, fpr=self.FEDORA_KEY)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn(msg, r.stderr, args)
            self.assertFalse(os.path.exists(os.path.join(self.w, "wget.log")), args)

    # --- make-bootstrap.sh -----------------------------------------------------------

    def test_bootstrap_amd64_as_before(self):
        r = self.build("make-bootstrap.sh", "alpine", "vms/a", m="x86_64")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.runs(), [("qemu-system-x86_64", "SSH_PORT=", [
            "-enable-kvm", "-cpu", "host", "-smp", "4", "-m", "4096", "-machine", "pc",
            "-drive", "file=alpine324.qcow2,if=virtio,format=qcow2",
            "-drive", "file=archlinux-2026.10.01-x86_64.iso,media=cdrom,readonly=on",
            "-nic", "user,model=virtio-net-pci", "-kernel", "TMP/vmlinuz", "-initrd", "TMP/initramfs.img",
            "-append", "archisobasedir=arch archisosearchuuid=X cow_spacesize=2048M console=tty0 console=ttyS0,115200n8 "
                       "script=http://10.0.2.2:PORT/install.sh",
            "-display", "none", "-serial", "file:install-serial.log", "-monitor", "none", "-no-reboot"])])
        wl = self.read("wget.log")
        self.assertIn("/releases/x86_64/alpine-minirootfs-3.24.3-x86_64.tar.gz", wl)
        self.assertIn("archlinux-2026.10.01-x86_64.iso", wl)
        sh = self.read("work.0", "www", "install.sh")
        self.assertIn("exec > >(tee /dev/ttyS0) 2>&1", sh)
        self.assertIn('SERIAL_CMDLINE="console=tty0 console=$SERIAL_TTY,115200n8"', sh)
        self.assertIn("SERIAL_TTY='ttyS0'", sh)
        self.assertIn("ARCH='amd64'", sh)
        self.assertIn("until systemctl is-active -q pacman-init.service", sh)
        self.assertNotRegex(sh, r"@[A-Z_]+@")
        self.assertFalse(os.path.exists(os.path.join(self.w, "work.0", "www", "bko.apkovl.tar.gz")))
        self.assertIsNone(self.options("vms/a"))
        # The install script's amd64 meaning: under bash, what its settings come to.
        out = subprocess.run(["bash", "-c", sh.split("echo \"BACKOTTER-INSTALL-START")[0].replace(
            "exec > >(tee /dev/ttyS0) 2>&1", "").replace("trap ", ": ") + 'echo "$SERIAL_CMDLINE"'],
            capture_output=True, text=True)
        self.assertEqual(out.stdout, "console=tty0 console=ttyS0,115200n8\n")

    def test_bootstrap_arm64(self):
        r = self.build("make-bootstrap.sh", "--arch", "arm64", "alpine", "vms/a")
        self.assertEqual(r.returncode, 0, r.stderr)
        (prog, port, words), = self.runs()
        self.assertEqual(prog, "vm-run")
        self.assertRegex(port, r"^SSH_PORT=\d+$")
        self.assertEqual(words, [
            "--uefi", "--no-secure-boot", "--cpus", "4", "--memory", "4096", "--serial-log", "install-serial.log",
            "--cdrom", "alpine-virt-3.24.3-aarch64.iso", "alpine324-arm64.qcow2", "--",
            "-kernel", "TMP/vmlinuz", "-initrd", "TMP/initramfs.img",
            "-append", "modules=loop,squashfs,sd-mod,usb-storage quiet ip=dhcp "
                       "apkovl=http://10.0.2.2:PORT/bko.apkovl.tar.gz console=tty0 console=ttyAMA0,115200n8",
            "-no-reboot"])
        wl = self.read("wget.log").splitlines()
        self.assertEqual(sorted(wl), sorted([
            "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/aarch64/alpine-virt-3.24.3-aarch64.iso.asc",
            "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/aarch64/alpine-virt-3.24.3-aarch64.iso",
            "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/aarch64/alpine-minirootfs-3.24.3-aarch64.tar.gz.asc",
            "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/aarch64/alpine-minirootfs-3.24.3-aarch64.tar.gz"]))
        sh = self.read("work.0", "www", "install.sh")
        for s in ("exec > >(tee /dev/ttyAMA0) 2>&1", "ARCH='arm64'", "SERIAL_TTY='ttyAMA0'", "FIRMWARE='uefi'",
                  "LAYOUT='uefi'", "ROOT_FS='xfs'", "GUEST_NAME='alpine324-arm64'"):
            self.assertIn(s, sh)
        self.assertNotRegex(sh, r"@[A-Z_]+@")
        self.assertEqual(subprocess.run(["bash", "-n"], input=sh, text=True).returncode, 0)
        # Alpine's part, under arm64: GRUB arm64-efi, the getty on ttyAMA0.
        self.assertIn("[[ $ARCH == amd64 ]] || efi_target=arm64-efi", sh)
        # The overlay: its files and links, and the local.d script.
        with tarfile.open(os.path.join(self.w, "work.0", "www", "bko.apkovl.tar.gz")) as t:
            m = {i.name: i for i in t.getmembers()}
            self.assertEqual({n for n, i in m.items() if i.isfile()}, {
                "etc/.default_boot_services", "etc/hostname", "etc/apk/world", "etc/network/interfaces",
                "etc/local.d/bko-install.start"})
            self.assertEqual(m["etc/runlevels/default/local"].linkname, "/etc/init.d/local")
            self.assertEqual(m["etc/runlevels/default/networking"].linkname, "/etc/init.d/networking")
            self.assertEqual(m["etc/local.d/bko-install.start"].mode, 0o755)
            self.assertTrue(all(i.uid == 0 and i.gid == 0 for i in m.values()))
            start = t.extractfile("etc/local.d/bko-install.start").read().decode()
            self.assertIn("iface eth0 inet dhcp", t.extractfile("etc/network/interfaces").read().decode())
        self.assertEqual(subprocess.run(["sh", "-n"], input=start, text=True).returncode, 0)
        self.assertIn("printf '%s\\n' https://dl-cdn.alpinelinux.org/alpine/v3.24/main "
                      "https://dl-cdn.alpinelinux.org/alpine/v3.24/community >/etc/apk/repositories", start)
        self.assertNotIn("arch-install-scripts", start.split("# arch-chroot:")[0])   # its genfstab conflicts with alpine-conf's
        self.assertIn('chroot "$r" "$@"; rc=$?', start)
        self.assertIn("swap\tdefaults", sh)
        self.assertRegex(start, r"wget -q -O /tmp/install\.sh http://10\.0\.2\.2:\d+/install\.sh")
        self.assertIn("exec bash /tmp/install.sh >/dev/null 2>&1", start)
        self.assertEqual(self.options("vms/a"), "--uefi --no-secure-boot\n")

    def test_bootstrap_refusals(self):
        for args, m, msg in (
                (["--arch", "arm64", "alpine", "vms/x"], "x86_64", "builds on an aarch64 host"),
                (["--arch", "arm64", "void", "vms/x"], "aarch64", "DISTRO alpine only"),
                (["--arch", "arm64", "alpine-uefi", "vms/x"], "aarch64", "DISTRO alpine only"),
                (["--arch", "s390x", "alpine", "vms/x"], "aarch64", "takes amd64 or arm64")):
            r = self.build("make-bootstrap.sh", *args, m=m)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn(msg, r.stderr, args)
            self.assertFalse(os.path.exists(os.path.join(self.w, "wget.log")), args)

    # --- make-debian-zfs.sh (root: its refusals only) ----------------------------------

    def test_debian_zfs_refusals(self):
        for args, m, msg in (
                (["--arch", "arm64", "vms/x", "debian13-zfs-arm64"], "x86_64", "builds on an aarch64 host"),
                (["--arch", "arm64", "vms/x", "debian13-zfs"], "aarch64", "ends in -arm64"),
                (["--arch", "mips", "vms/x", "debian13-zfs-arm64"], "aarch64", "takes amd64 or arm64"),
                # Past the architecture's checks, a user is still refused.
                (["--arch", "arm64", "vms/x", "debian13-zfs-arm64"], "aarch64", "must run as root"),
                (["vms/x", "debian13-zfs"], "x86_64", "must run as root")):
            if os.geteuid() == 0:
                self.skipTest("run as root")
            r = self.build("make-debian-zfs.sh", *args, m=m)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn(msg, r.stderr, args)
        self.assertFalse(os.path.exists(os.path.join(self.w, "vms", "x")))


# --- vm-update: which bko-holt an arm64 guest's dump takes ---------------------------

def load_vm_update():
    loader = importlib.machinery.SourceFileLoader("vm_update_a5", os.path.join(TESTVM, "vm-update"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class VmUpdateDumpPlat(unittest.TestCase):
    def test_dump_plat(self):
        vu = load_vm_update()
        # amd64's as before: DUMP_PLATS' folders.
        for system, plat in vu.DUMP_PLATS.items():
            self.assertEqual(vu.dump_plat(system, "debian13"), plat)
        self.assertIsNone(vu.dump_plat("Minix", "debian13"))
        # arm64's beside them (bko-holt's bin set: linux-arm64/).
        for g in ("debian13-arm64", "ubuntu26-arm64", "fedora44-arm64", "alpine324-arm64", "debian13-zfs-arm64"):
            self.assertEqual(vu.dump_plat("Linux", g), "linux-arm64")
        self.assertEqual(vu.dump_plat("FreeBSD", "freebsd15-arm64"), "freebsd-arm64")
        self.assertIsNone(vu.dump_plat("Minix", "debian13-arm64"))

    def test_dump_takes_the_arm64_binary(self):
        vu = load_vm_update()
        d = tempfile.mkdtemp(prefix="t2-a5-holt-")
        self.addCleanup(shutil.rmtree, d, True)
        for sub in ("", "linux-arm64"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
            with open(os.path.join(d, sub, "bko-holt"), "w") as f:
                f.write(sub or "amd64")
        put = []

        class G:
            name = "debian13-arm64"

        vm = vu.VM.__new__(vu.VM)
        vm.g = G()
        vm.put = lambda local, remote: put.append(str(local)) or 1     # stop after the copy
        old = os.environ.get("VM_UPDATE_HOLT_BIN")
        os.environ["VM_UPDATE_HOLT_BIN"] = d
        try:
            self.assertEqual(vm.dump("Linux 6.12.48+deb13-arm64"), (None, "copying bko-holt in failed (exit status 1)"))
            G.name = "debian13"
            vm.dump("Linux 6.12.48+deb13-amd64")
        finally:
            if old is None:
                os.environ.pop("VM_UPDATE_HOLT_BIN")
            else:
                os.environ["VM_UPDATE_HOLT_BIN"] = old
        self.assertEqual(put, [os.path.join(d, "linux-arm64", "bko-holt"), os.path.join(d, "bko-holt")])


# --- vm-chroot: a guest of another architecture is refused ----------------------------

ELF = {"x86_64": b"\x7fELF\x02\x01\x01" + b"\0" * 9 + b"\x03\x00\x3e\x00",
       "aarch64": b"\x7fELF\x02\x01\x01" + b"\0" * 9 + b"\x03\x00\xb7\x00"}


@unittest.skipUnless(bash_major() >= 4, "vm-chroot needs bash 4 or newer")
class VmChroot(unittest.TestCase):
    def setUp(self):
        self.w = tempfile.mkdtemp(prefix="t2-a5-chroot-")
        self.addCleanup(shutil.rmtree, self.w, True)
        w = self.w
        os.makedirs(os.path.join(w, "tv"))
        os.makedirs(os.path.join(w, "stub"))
        with open(os.path.join(TESTVM, "vm-chroot")) as f:
            code = f.read()
        root_check = '[[ $EUID -eq 0 ]] || die "must run as root"\n'
        self.assertIn(root_check, code)
        with open(os.path.join(w, "tv", "vm-chroot"), "w") as f:
            f.write(code.replace(root_check, ""))
        os.chmod(os.path.join(w, "tv", "vm-chroot"), 0o755)
        shutil.copy(os.path.join(TESTVM, "ostree_paths.py"), os.path.join(w, "tv"))
        stubs = {"uname": '#!/bin/bash\n[[ ${1:-} == -m ]] && { echo "$FAKE_M"; exit 0; }\nexec /bin/uname "$@"\n',
                 "mount": "#!/bin/bash\nexit 0\n", "umount": "#!/bin/bash\nexit 0\n",
                 "chroot": '#!/bin/bash\necho "chroot $*" >"$W/chroot"\n'}
        for n, text in stubs.items():
            with open(os.path.join(w, "stub", n), "w") as f:
                f.write(text)
            os.chmod(os.path.join(w, "stub", n), 0o755)

    def guest(self, machine, usrmerge=True):
        g = tempfile.mkdtemp(prefix="g", dir=self.w)
        os.makedirs(os.path.join(g, "etc"))
        open(os.path.join(g, "etc", "fstab"), "w").close()
        os.makedirs(os.path.join(g, "usr", "bin"))
        os.makedirs(os.path.join(g, "usr", "sbin"))
        for d in ("proc", "sys", "dev/pts"):
            os.makedirs(os.path.join(g, d))
        with open(os.path.join(g, "usr", "bin", "dash"), "wb") as f:
            f.write(ELF[machine] + b"\0" * 100)
        if usrmerge:
            os.symlink("usr/bin", os.path.join(g, "bin"))     # bin -> usr/bin, sh -> dash: links inside the guest
        else:
            os.makedirs(os.path.join(g, "bin"))
            shutil.copy(os.path.join(g, "usr", "bin", "dash"), os.path.join(g, "bin", "dash"))
        os.symlink("dash", os.path.join(g, "usr", "bin", "sh"))
        if not usrmerge:
            os.symlink("/usr/bin/dash", os.path.join(g, "bin", "sh"))
        return g

    def chroot(self, g, m):
        e = dict(os.environ, PATH=os.path.join(self.w, "stub") + os.pathsep + os.environ["PATH"], FAKE_M=m, W=self.w)
        if os.path.exists(os.path.join(self.w, "chroot")):
            os.remove(os.path.join(self.w, "chroot"))
        return subprocess.run([os.path.join(self.w, "tv", "vm-chroot"), g, "true"], capture_output=True, text=True,
                              env=e, timeout=60)

    def test_same_architecture_runs(self):
        for m, usrmerge in (("aarch64", True), ("x86_64", True), ("aarch64", False)):
            g = self.guest(m, usrmerge)
            r = self.chroot(g, m)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(self.w, "chroot")) as f:
                self.assertEqual(f.read(), f"chroot {os.path.realpath(g)} true\n")

    def test_other_architecture_refused(self):
        for guest, host in (("aarch64", "x86_64"), ("x86_64", "aarch64")):
            g = self.guest(guest)
            r = self.chroot(g, host)
            self.assertEqual(r.returncode, 1)
            self.assertIn(f"is a {guest} guest (its /bin/sh), and this host is {host}", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(self.w, "chroot")))

    def test_no_shell_is_not_refused(self):
        g = self.guest("aarch64")
        os.remove(os.path.join(g, "usr", "bin", "sh"))
        r = self.chroot(g, "x86_64")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
