"""BackShot1 finds the restored disk's ESPs, and the boot entry naming one,
on a dos (MBR) disk as well as on GPT: bs1-common's disk_esps,
boot_entry_for and removable_loader (bs1-restore's "booting the restored
system next") and bs1-verify's booted_the_restored_esp.  Artix by Calamares
on the X13 has a dos disk whose ESP is type c, and the firmware's entry
HD(1,MBR,0xe30bd8c7,0x1000,0x96000): bs1-restore looked only for the GPT ESP
type and stopped, "no boot entry names a restored ESP, and nvme0n1 has none"
(artix-20261102-20261123-092557).  No root, no firmware: the functions run
against fake lsblk, blkid, mount, umount and findmnt, /sys under a temporary
directory and a plain directory for efivarfs."""

import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

if os.geteuid() == 0:
    # These run BackShot1's scripts against fakes.  As root, on a real
    # machine, one slip reaches its disks, its firmware or its reboot: a
    # background reboot outlived test_bs1_to_stick's fakes and rebooted the
    # Thinkpad (2026-11-22).
    raise unittest.SkipTest("BackShot1's tests don't run as root")

TESTS = Path(__file__).resolve().parent
BS1 = TESTS.parent.parent / "realworld" / "backshot1"
GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"


def newest_bash():
    """The newest bash here: the laptops run 5, the Mac's 3.2 will do."""
    best = None
    for b in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", "bash", "/bin/bash"):
        try:
            out = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        v = out.stdout.strip()
        if out.returncode == 0 and v.isdigit() and (best is None or int(v) > best[0]):
            best = (int(v), b)
    return best and best[1]


BASH = newest_bash()


def functions(path, *names):
    text = Path(path).read_text()
    out = []
    for n in names:
        m = re.search(rf"^{re.escape(n)}\(\) {{\n.*?^}}\n", text, re.M | re.S)
        assert m, f"no function {n} in {path}"
        out.append(m.group(0))
    return "\n".join(out)


def assignment(path, name):
    m = re.search(rf"^{re.escape(name)}=.*$", Path(path).read_text(), re.M)
    assert m, f"no {name}= in {path}"
    return m.group(0)


def exe(path, text):
    Path(path).write_text(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# The X13's, as Artix left them (efibootmgr -v, 2026-11-23), and two of the
# firmware's own entries and the stick's around them.
ARTIX_NV = """BootCurrent: 0010
Timeout: 0 seconds
BootOrder: 0007,0000,0001
Boot0000  Setup\tFvFile(721c8b66-426c-4e86-8e99-3457c46ab0b9)
Boot0001  Boot Menu\tFvFile(126a762d-5758-4fca-8531-201a7f57f850)
Boot0007* Artix\tHD(1,MBR,0xe30bd8c7,0x1000,0x96000)/\\EFI\\Artix\\grubx64.efi
Boot0010* BKO rescue\tPciRoot(0x0)/Pci(0x14,0x0)/USB(1,0)/HD(2,GPT,0b5e4c7e-2f6a-4c0e-9d0e-1c7f3b9f2a11,0x800,0x10000)/\\EFI\\BOOT\\BOOTX64.EFI
"""

ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"


@unittest.skipUnless(BASH, "needs bash")
class Esps(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-esp-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        t = self.t
        b = self.bin = t / "bin"
        b.mkdir()
        (t / "tmp").mkdir()
        exe(b / "lsblk", f"""#!/bin/sh
case "$*" in
"-dno PTTYPE,PTUUID /dev/nvme0n1") cat {t}/pt ;;
"-lnpo NAME,PARTTYPE,FSTYPE /dev/nvme0n1") cat {t}/parts-fs ;;
"-lnpo NAME,PARTTYPE,PARTUUID /dev/nvme0n1") cat {t}/parts-uuid ;;
*) echo "lsblk $*: not faked" >&2; exit 1 ;;
esac
""")
        exe(b / "blkid", f"""#!/bin/sh
case "$*" in
"-p -o value -s PART_ENTRY_UUID /dev/"*) f={t}/blkid-${{6##*/}}; [ -e "$f" ] || exit 2; cat "$f" ;;
*) echo "blkid $*: not faked" >&2; exit 1 ;;
esac
""")
        # Each partition's files are in esp-NAME; mounting one copies them in.
        exe(b / "mount", f"""#!/bin/sh
echo "mount $*" >>{t}/mounts
for a; do dev=$tgt; tgt=$a; done
src={t}/esp-${{dev##*/}}
[ -d "$src" ] || exit 32
cp -R "$src"/. "$tgt"/
""")
        exe(b / "umount", f'#!/bin/sh\necho "umount $*" >>{t}/mounts\nfind "$1" -mindepth 1 -delete\n')
        exe(b / "findmnt", "#!/bin/sh\nexit 0\n")
        self.lib = "\n".join([
            "set -uo pipefail",
            'say() { echo "$*"; }',
            assignment(BS1 / "bs1-common", "ESP_TYPE"),
            functions(BS1 / "bs1-common", "disk_esps", "boot_entry_for", "removable_loader"),
            functions(BS1 / "bs1-verify", "booted_the_restored_esp"),
        ]).replace("/sys/", f"{t}/sys/")

    def disk(self, pttype, ptuuid, parts, lbs=512):
        """PARTS: (name, type, fstype, partuuid, start) each."""
        t = self.t
        (t / "pt").write_text(f"{pttype} {ptuuid}\n")
        (t / "sys/class/block/nvme0n1/queue").mkdir(parents=True, exist_ok=True)
        (t / "sys/class/block/nvme0n1/queue/logical_block_size").write_text(f"{lbs}\n")
        fs, uu = ["/dev/nvme0n1\n"], ["/dev/nvme0n1\n"]
        for name, typ, fstype, partuuid, start in parts:
            fs.append(f"/dev/{name} {typ} {fstype}".rstrip() + "\n")
            uu.append(f"/dev/{name} {typ} {partuuid}\n")
            (t / f"sys/class/block/{name}").mkdir(parents=True, exist_ok=True)
            (t / f"sys/class/block/{name}/start").write_text(f"{start}\n")
        (t / "parts-fs").write_text("".join(fs))
        (t / "parts-uuid").write_text("".join(uu))

    def artix(self):
        # sfdisk -d: label: dos, label-id: 0xe30bd8c7; p1 start=4096, type=c, vfat.
        self.disk("dos", "e30bd8c7", [("nvme0n1p1", "0xc", "vfat", "e30bd8c7-01", 4096),
                                      ("nvme0n1p2", "0x83", "ext4", "e30bd8c7-02", 618496)])

    def sh(self, script, stdin=None):
        r = subprocess.run([BASH, "-c", f"{self.lib}\n{script}"], input=stdin, capture_output=True, text=True,
                           timeout=30, env={"PATH": f"{self.bin}:/usr/bin:/bin", "LC_ALL": "C",
                                            "TMPDIR": str(self.t / "tmp")})
        return r

    def esps(self):
        r = self.sh('disk_esps nvme0n1; echo "rc $?"')
        return r.stdout.splitlines()

    def entry(self, key, nv):
        r = self.sh(f'boot_entry_for "{key}"', stdin=nv)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    # ---- disk_esps and boot_entry_for

    def test_artix_on_the_x13s_dos_disk(self):
        self.artix()
        self.assertEqual(self.esps(), ["/dev/nvme0n1p1 mbr,0xe30bd8c7,0x1000", "rc 0"])
        self.assertEqual(self.entry("mbr,0xe30bd8c7,0x1000", ARTIX_NV), "0007")

    def test_dos_takes_0xef_first_then_fat_types_and_only_vfat(self):
        self.disk("dos", "e30bd8c7", [
            ("nvme0n1p1", "0xc", "vfat", "", 2048),
            ("nvme0n1p2", "0xef", "vfat", "", 4096),
            ("nvme0n1p3", "0x83", "ext4", "", 8192),
            ("nvme0n1p4", "0x7", "ntfs", "", 16384),
            ("nvme0n1p5", "0xc", "", "", 32768),        # unformatted
            ("nvme0n1p6", "0xe", "vfat", "", 65536),
            ("nvme0n1p7", "0x83", "vfat", "", 131072),  # vfat, but a Linux partition
        ])
        self.assertEqual(self.esps(), ["/dev/nvme0n1p2 mbr,0xe30bd8c7,0x1000",
                                       "/dev/nvme0n1p1 mbr,0xe30bd8c7,0x800",
                                       "/dev/nvme0n1p6 mbr,0xe30bd8c7,0x10000", "rc 0"])

    def test_dos_signature_and_start_without_leading_zeros_in_logical_blocks(self):
        # lsblk pads PTUUID to eight digits; efibootmgr prints %x.  A 4Kn disk:
        # sysfs's 512-byte sectors are the HD() node's blocks / 8.
        self.disk("dos", "0abc1234", [("nvme0n1p1", "0xef", "vfat", "0abc1234-01", 2048)], lbs=4096)
        self.assertEqual(self.esps(), ["/dev/nvme0n1p1 mbr,0xabc1234,0x100", "rc 0"])
        nv = ("BootOrder: 0002,0003,0004\n"
              "Boot0002* other\tHD(1,MBR,0xabc1234,0x1000,0x96000)/\\EFI\\x.efi\n"   # 0x100 isn't 0x1000
              "Boot0003* Artix\tHD(1,MBR,0x0abc1234,0x0100,0x96000)/\\EFI\\Artix\\grubx64.efi\n"
              "Boot0004* again\tHD(1,MBR,0xABC1234,0x100,0x96000)/\\EFI\\BOOT\\BOOTX64.EFI\n")
        self.assertEqual(self.entry("mbr,0xabc1234,0x100", nv), "0003")

    def test_dos_entry_for_another_partition_or_disk_isnt_the_esps(self):
        self.artix()
        nv = ("BootOrder: 0001,0002\n"
              "Boot0001* p2\tHD(2,MBR,0xe30bd8c7,0x10000,0x96000)/\\EFI\\x.efi\n"
              "Boot0002* other disk\tHD(1,MBR,0xe30bd8c8,0x1000,0x96000)/\\EFI\\x.efi\n")
        self.assertEqual(self.entry("mbr,0xe30bd8c7,0x1000", nv), "")

    def test_dos_without_a_signature_is_an_error(self):
        self.disk("dos", "", [("nvme0n1p1", "0xef", "vfat", "", 2048)])
        self.assertEqual(self.esps(), ["rc 1"])

    def test_gpt_by_type_and_the_tables_own_guid(self):
        g1, g2 = "5F1D4C3A-8E2B-4A6C-9D1E-0F2A3B4C5D6E", "1b2c3d4e-0000-4000-8000-000000000001"
        self.disk("gpt", "9a8b7c6d-0000-4000-8000-00000000000a", [
            ("nvme0n1p1", ESP.upper(), "vfat", "stale-udev-guid", 2048),
            ("nvme0n1p2", "0fc63daf-8483-4772-8e79-3d69d8477de4", "ext4", "x", 4096),
            ("nvme0n1p3", ESP, "vfat", g2, 8192),
        ])
        (self.t / "blkid-nvme0n1p1").write_text(g1 + "\n")    # p3: blkid fails, lsblk's then
        self.assertEqual(self.esps(), [f"/dev/nvme0n1p1 {g1.lower()}", f"/dev/nvme0n1p3 {g2}", "rc 0"])
        # Two on one ESP (ZFSBootMenu's): BootOrder's first, not the lowest number.
        nv = (f"BootOrder: 0002,0001\n"
              f"Boot0001* ZFSBootMenu (Backup)\tHD(1,GPT,{g1},0x800,0x100000)/\\EFI\\ZBM\\VMLINUZ-BACKUP.EFI\n"
              f"Boot0002* ZFSBootMenu\tHD(1,GPT,{g1},0x800,0x100000)/\\EFI\\ZBM\\VMLINUZ.EFI\n")
        self.assertEqual(self.entry(g1.lower(), nv), "0002")
        # In no BootOrder: the first listed.
        self.assertEqual(self.entry(g1.lower(), nv.replace("BootOrder: 0002,0001", "BootOrder: 0009")), "0001")

    # ---- removable_loader: bs1-restore's one-shot fallback

    def test_removable_loader_on_dos_takes_the_first_esp_with_one(self):
        self.disk("dos", "e30bd8c7", [("nvme0n1p1", "0xc", "vfat", "", 2048),
                                      ("nvme0n1p2", "0xef", "vfat", "", 4096)])
        (self.t / "esp-nvme0n1p2/EFI/Artix").mkdir(parents=True)     # 0xef, but no BOOTX64.EFI
        (self.t / "esp-nvme0n1p2/EFI/Artix/grubx64.efi").write_text("")
        (self.t / "esp-nvme0n1p1/EFI/Boot").mkdir(parents=True)
        (self.t / "esp-nvme0n1p1/EFI/Boot/bootx64.efi").write_text("")
        r = self.sh('removable_loader nvme0n1; echo "rc $?"')
        self.assertEqual(r.stdout.splitlines(), ["/dev/nvme0n1p1 /EFI/Boot/bootx64.efi", "rc 0"], r.stderr)
        mounts = (self.t / "mounts").read_text()
        self.assertIn("mount -o ro /dev/nvme0n1p2 ", mounts)
        self.assertEqual(mounts.count("umount"), 2)
        self.assertEqual(list((self.t / "tmp").iterdir()), [], "a mount point was left")

    def test_removable_loader_with_none(self):
        self.artix()
        r = self.sh('removable_loader nvme0n1; echo "rc $?"')     # esp-nvme0n1p1 missing: mount fails
        self.assertEqual(r.stdout.splitlines(), ["rc 1"])
        self.assertIn("can't mount /dev/nvme0n1p1", r.stderr)

    # ---- bs1-verify's booted_the_restored_esp, from efivarfs

    def boot_current(self, hd):
        """BootCurrent 0007, an entry whose device path is HD (or a
        whole-disk path when None), as efibootmgr --create would write it."""
        v = self.t / "sys/firmware/efi/efivars"
        v.mkdir(parents=True, exist_ok=True)
        dp = hd if hd is not None else struct.pack("<BBHBB", 1, 1, 6, 0, 0x14)    # Pci() alone
        p = ("\\EFI\\Artix\\grubx64.efi\0").encode("utf-16-le")
        dp += struct.pack("<BBH", 4, 4, 4 + len(p)) + p + b"\x7f\xff\x04\x00"
        opt = struct.pack("<IH", 1, len(dp)) + "Artix\0".encode("utf-16-le") + dp
        (v / f"BootCurrent-{GLOBAL}").write_bytes(struct.pack("<IH", 6, 7))
        (v / f"Boot0007-{GLOBAL}").write_bytes(struct.pack("<I", 7) + opt)

    @staticmethod
    def hd_mbr(sig, start, num=1, size=0x96000):
        return struct.pack("<BBHIQQ16sBB", 4, 1, 42, num, start, size, struct.pack("<I", sig) + bytes(12), 1, 1)

    @staticmethod
    def hd_gpt(guid, start=0x800, size=0x100000):
        return struct.pack("<BBHIQQ16sBB", 4, 1, 42, 1, start, size, uuid.UUID(guid).bytes_le, 2, 2)

    def booted(self):
        r = self.sh('disk=nvme0n1; booted_the_restored_esp; echo "rc $?"')
        return r.stdout.splitlines()[-1], r

    def test_verify_dos_entry_is_the_restored_esps(self):
        if not shutil.which("python3"):
            self.skipTest("needs python3")
        self.artix()
        self.boot_current(self.hd_mbr(0xE30BD8C7, 0x1000))
        rc, r = self.booted()
        self.assertEqual(rc, "rc 0", r.stdout + r.stderr)

    def test_verify_dos_entry_for_another_partition_isnt(self):
        if not shutil.which("python3"):
            self.skipTest("needs python3")
        self.artix()
        self.boot_current(self.hd_mbr(0xE30BD8C7, 0x10000))
        self.assertEqual(self.booted()[0], "rc 1")
        self.boot_current(self.hd_mbr(0xE30BD8C8, 0x1000))
        self.assertEqual(self.booted()[0], "rc 1")

    def test_verify_gpt_and_the_whole_disk_entry(self):
        if not shutil.which("python3"):
            self.skipTest("needs python3")
        g = "5f1d4c3a-8e2b-4a6c-9d1e-0f2a3b4c5d6e"
        self.disk("gpt", "9a8b7c6d-0000-4000-8000-00000000000a", [("nvme0n1p1", ESP, "vfat", g, 2048)])
        self.boot_current(self.hd_gpt(g))
        self.assertEqual(self.booted()[0], "rc 0")
        self.boot_current(self.hd_gpt("00000000-1111-4222-8333-444444444444"))
        self.assertEqual(self.booted()[0], "rc 1")
        # AerynOS: the firmware's own entry for the disk, no partition in it.
        self.boot_current(None)
        self.assertEqual(self.booted()[0], "rc 0")

    # ---- bs1-restore uses them

    def test_restore_finds_esps_through_disk_esps(self):
        text = (BS1 / "bs1-restore").read_text()
        self.assertIn('disk_esps "$disk"', text)
        self.assertIn("boot_entry_for", text)
        self.assertIn('removable_loader "$disk"', text)
        self.assertNotIn("ESP_TYPE", text)
        self.assertNotIn("PART_ENTRY_UUID", text)


# FreeBSD's efibootmgr (base, 15.1), faked with its state in files: the
# list as it prints it ("+Boot0004* LABEL" for BootCurrent, " Boot0000*
# LABEL"; -v adds the device path), and -B -b, -a -c -l -L (a new entry
# goes first in BootOrder), -o and -n -b.
FAKE_BSD_EFIBOOTMGR = r"""#!/bin/sh
s=@STATE@
echo "efibootmgr $*" >>$s/calls
case "$1" in
""|-v)
    echo "BootCurrent: $(cat $s/current)"
    echo "Timeout    : 1 seconds"
    echo "BootOrder  : $(sed 's/,/, /g' $s/order)"
    while read -r n label; do
        m=" "; [ "$n" = "$(cat $s/current)" ] && m=+
        if [ "$1" = -v ]; then echo "${m}Boot$n* $label $(cat $s/path-$n 2>/dev/null)"; else echo "${m}Boot$n* $label"; fi
    done <$s/entries
    ;;
-B) n=$3; grep -v "^$n " $s/entries >$s/e; mv $s/e $s/entries
    sed -e "s/$n,\{0,1\}//" -e 's/,$//' $s/order >$s/o; mv $s/o $s/order ;;
-a) n=$(awk '{ print $1 }' $s/entries | sort | tail -n 1); n=$(printf '%04X' $((0x$n + 1)))
    echo "$n $6" >>$s/entries; echo "$4" >$s/loader-$n
    o=$(cat $s/order); echo "$n${o:+,$o}" >$s/order ;;
-o) echo "$2" >$s/order ;;
-n) echo "$3" >$s/next ;;
*) echo "efibootmgr $*: not faked" >&2; exit 1 ;;
esac
"""


@unittest.skipUnless(BASH, "needs bash")
class FreeBsdEfi(unittest.TestCase):
    """bs1-restore's FreeBSD BootNext (docs/design/freebsd-bs1.md): base's
    efibootmgr in place of bs1-efi, a one-shot "BKO restored" left out of
    BootOrder; and bs1-verify's BootCurrent check from efibootmgr -v."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-bsdefi-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        (self.t / "bin").mkdir()
        exe(self.t / "bin" / "efibootmgr", FAKE_BSD_EFIBOOTMGR.replace("@STATE@", str(self.t)))
        self.state(current="0004", order="0004,0000",
                   entries={"0000": "UiApp", "0002": "BKO restored", "0003": "BKO restored 2",
                            "0004": "FreeBSD", "0005": "BKO restored"})
        self.lib = "\n".join([
            "set -uo pipefail",
            'say() { echo "$*"; }',
            'die() { echo "STOPPED: $*"; exit 1; }',
            'run() { say "$ $*"; "$@"; }',
            'must() { run "$@" || die "this failed: $*"; }',
            functions(BS1 / "bs1-restore", "bsd_entries_named", "bsd_efi_delete", "bsd_efi_oneshot"),
            functions(BS1 / "bs1-verify", "bsd_booted_the_restored_esp"),
        ])

    def state(self, current, order, entries, paths=None):
        (self.t / "current").write_text(current + "\n")
        (self.t / "order").write_text(order + "\n")
        (self.t / "entries").write_text("".join(f"{n} {label}\n" for n, label in entries.items()))
        for n, p in (paths or {}).items():
            (self.t / f"path-{n}").write_text(p + "\n")

    def bash(self, script, esps=""):
        stub = f"disk=nda0\ndisk_esps() {{ printf '%b' {esps!r}; }}\n"
        env = dict(os.environ, PATH=f"{self.t / 'bin'}:{os.environ['PATH']}")
        return subprocess.run([BASH, "-c", f"{self.lib}\n{stub}\n{script}"], capture_output=True, text=True, env=env, timeout=30)

    def entries(self):
        return dict(line.split(" ", 1) for line in (self.t / "entries").read_text().splitlines())

    def test_delete_takes_every_entry_of_that_label_alone(self):
        r = self.bash('bsd_efi_delete "BKO restored"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.entries(), {"0000": "UiApp", "0003": "BKO restored 2", "0004": "FreeBSD"})

    def test_oneshot_is_bootnext_and_not_in_bootorder(self):
        r = self.bash('bsd_efi_oneshot "BKO restored" /dev/nda0p1 /EFI/BOOT/BOOTX64.EFI')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.entries()["0006"], "BKO restored")
        self.assertEqual((self.t / "loader-0006").read_text().strip(), "nda0p1:/EFI/BOOT/BOOTX64.EFI")
        self.assertEqual((self.t / "order").read_text().strip(), "0004,0000")
        self.assertEqual((self.t / "next").read_text().strip(), "0006")
        self.assertIn("efibootmgr -a -c -l nda0p1:/EFI/BOOT/BOOTX64.EFI -L BKO restored", (self.t / "calls").read_text())

    def test_oneshot_with_no_bootorder(self):
        (self.t / "order").write_text("\n")
        r = self.bash('bsd_efi_oneshot "BKO restored" /dev/nda0p1 /EFI/BOOT/BOOTX64.EFI')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.t / "next").read_text().strip(), "0006")
        self.assertNotIn("efibootmgr -o", (self.t / "calls").read_text())

    GUID = "f859c46d-19ee-11f1-b4ca-002590ec6166"

    def booted(self, path, esps):
        self.state(current="0004", order="0004", entries={"0004": "FreeBSD"}, paths={"0004": path})
        return self.bash("bsd_booted_the_restored_esp; echo rc $?", esps)

    def test_booted_the_restored_esp_on_gpt(self):
        r = self.booted(f"HD(1,GPT,{self.GUID.upper()},0x28,0x82000)/File(\\EFI\\freebsd\\loader.efi)",
                        f"/dev/nda0p1 {self.GUID}\n")
        self.assertIn("rc 0", r.stdout, r.stdout + r.stderr)

    def test_another_esp_fails(self):
        r = self.booted("HD(1,GPT,00000000-1111-4222-8333-444444444444,0x28,0x82000)/File(\\EFI\\freebsd\\loader.efi)",
                        f"/dev/nda0p1 {self.GUID}\n")
        self.assertIn("rc 1", r.stdout)

    def test_mbr_key_without_leading_zeros(self):
        r = self.booted("HD(1,MBR,0x0e30bd8c7,0x1000,0x96000)/File(\\EFI\\BOOT\\BOOTX64.EFI)",
                        "/dev/nda0s1 mbr,0xe30bd8c7,0x1000\n")
        self.assertIn("rc 0", r.stdout, r.stdout + r.stderr)

    def test_firmware_entry_for_a_whole_disk_passes(self):
        r = self.booted("PciRoot(0x0)/Pci(0x1d,0x0)/NVMe(0x1,00-00-00-00-00-00-00-00)", f"/dev/nda0p1 {self.GUID}\n")
        self.assertIn("rc 0", r.stdout)

    def test_no_esp_fails(self):
        r = self.booted(f"HD(1,GPT,{self.GUID},0x28,0x82000)", "")
        self.assertIn("rc 1", r.stdout)


if __name__ == "__main__":
    unittest.main()
