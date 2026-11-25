"""BackShot1 on TrueNAS Community Edition 25.10 (docs/design/truenas.md): the
ESP on the boot-pool disk is mounted by nothing, so bs1-common's
check_coverage passes over it on TrueNAS alone, and bs1-backup's
truenas_check then needs bko-holt dump's boot.truenas to list it with its
volume ID (load formats it so and its grub-install makes it again), of
TrueNAS 25.10, holding only what that grub-install writes.  The Thinkpad's
first preflight on TrueNAS SCALE 25.10.8 stopped at "/dev/nvme0n1p2 (vfat)
isn't mounted, so it wouldn't be backed up" (2026-11-25).  Also
internal_mounts' one copy of a ZFS dataset mounted twice (.system/cores at
/var/db/system/cores and /var/lib/systemd/coredump, as the dump has it) and
unhinted_check's stop at a source nothing mounts (an inactive boot
environment).  Fake lsblk, blkid, findmnt, zpool and midclt; no root."""

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

if os.geteuid() == 0:
    raise unittest.SkipTest("BackShot1's tests don't run as root")

TESTS = Path(__file__).resolve().parent
BS1 = TESTS.parent.parent / "realworld" / "backshot1"
ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BIOS = "21686148-6449-6e6f-744e-656564454649"
ZFS = "6a898cc3-1dd2-11b2-99a6-080020736631"


def newest_bash():
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
    """Each function's text, a one-line definition or a block."""
    text = Path(path).read_text()
    out = []
    for n in names:
        m = re.search(rf"^{re.escape(n)}\(\) {{\n.*?^}}\n", text, re.M | re.S) or re.search(rf"^{re.escape(n)}\(\) {{ .*}}$", text, re.M)
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


# The Thinkpad's, 2026-11-25: TrueNAS's installer's three partitions.
PARTS = [
    ("nvme0n1p1", "259:1", "", BIOS, ""),
    ("nvme0n1p2", "259:2", "vfat", ESP, "F717-4973"),
    ("nvme0n1p3", "259:3", "zfs_member", ZFS, "74288661024722887"),
]
# What its ESP held, as bko-holt dump read it.
ESP_FILES = ["EFI/boot/bootx64.efi", "EFI/debian/BOOTX64.CSV", "EFI/debian/fbx64.efi", "EFI/debian/grub.cfg",
             "EFI/debian/grubx64.efi", "EFI/debian/mmx64.efi", "EFI/debian/shimx64.efi"]


def layout(product="TrueNAS 25.10.8", esps=(("nvme0n1p2", "F717-4973"),), files=ESP_FILES, read=True, truenas=True, sources=None):
    boot = {"esps": [{"device": "nvme0n1p2", "mounted_at": None,
                      "contents": {"files": [{"path": f, "size": 1} for f in files]} if read else None}]}
    if truenas:
        boot["truenas"] = {"pool": "boot-pool", "version": "25.10.8", "bes": [],
                           "esps": [{"device": d, "disk": "nvme0n1", "uuid": u, "label": "EFI", "read": read} for d, u in esps]}
    os_ = {"id": "debian", "version_id": "12"}
    if product:
        os_["product"] = product
    return {"os": os_, "boot": boot, "storage": {"backup_sources": sources if sources is not None else [
        {"path": "/", "from": "/", "fstype": "zfs", "source": "boot-pool/ROOT/25.10.8"}]}}


@unittest.skipUnless(BASH, "needs bash")
class TrueNas(unittest.TestCase):
    def setUp(self):
        self.t = t = Path(tempfile.mkdtemp(prefix="bs1-truenas-"))
        self.addCleanup(shutil.rmtree, t, True)
        b = self.bin = t / "bin"
        b.mkdir()
        (t / "version").write_text("25.10.8")
        self.parts(PARTS)
        self.mounts([("/", "boot-pool/ROOT/25.10.8", "zfs", "/", "0:30")])
        exe(b / "lsblk", f"""#!/bin/sh
case "$*" in
"-lnpo NAME,KNAME,MAJ:MIN,FSTYPE /dev/nvme0n1") cat {t}/lsblk-cov ;;
"-lnpo NAME,FSTYPE /dev/nvme0n1") cat {t}/lsblk-fs ;;
"-dno PARTTYPE /dev/"*) f={t}/ptype-${{3##*/}}; [ -e "$f" ] && cat "$f" ;;
"-snlo NAME /dev/nvme0n1p3") printf 'nvme0n1p3\\nnvme0n1\\n' ;;
*) echo "lsblk $*: not faked" >&2; exit 1 ;;
esac
""")
        exe(b / "blkid", f"""#!/bin/sh
case "$*" in
"-p -o value -s UUID /dev/"*) f={t}/uuid-${{6##*/}}; [ -s "$f" ] || exit 2; cat "$f" ;;
*) echo "blkid $*: not faked" >&2; exit 1 ;;
esac
""")
        exe(b / "findmnt", f"""#!/bin/sh
case "$*" in
"-rno SOURCE") cut -f2 {t}/mounts ;;
"-rno MAJ:MIN") cut -f5 {t}/mounts ;;
"-rno TARGET,SOURCE,FSTYPE,FSROOT") awk -F'\\t' '{{ print $1, $2, $3, $4 }}' {t}/mounts ;;
*) echo "findmnt $*: not faked" >&2; exit 1 ;;
esac
""")
        exe(b / "zpool", "#!/bin/sh\n[ \"$1 $2\" = \"list -vHPL\" ] || exit 1\n[ \"$3\" = boot-pool ] || exit 1\n"
                         "printf 'boot-pool\\t476G\\n\\t/dev/nvme0n1p3\\t476G\\n'\n")
        self.midclt(True)
        common = BS1 / "bs1-common"
        self.lib = "\n".join([
            "set -uo pipefail",
            'say() { echo "$*"; }',
            'die() { echo "STOPPED: $*"; exit 1; }',
            "is_ostree() { false; }",
            'is_abroot() { [[ -e "$ABROOT" ]]; }',
            f"ABROOT={t}/abroot.json",
            assignment(common, "REAL_FS"),
            assignment(common, "ESP_TYPE"),
            f"PROXMOX_ESPS={t}/no-proxmox-boot-uuids",
            f"TRUENAS_VERSION={t}/version",
            functions(common, "is_truenas", "truenas_esp", "truenas_esps", "check_coverage", "on_disk", "internal_mounts"),
            functions(BS1 / "bs1-backup", "truenas_check", "unhinted_check"),
            "disk=nvme0n1",
        ])

    def midclt(self, there):
        f = self.bin / "midclt"
        if there:
            exe(f, "#!/bin/sh\nexit 0\n")
        elif f.exists():
            f.unlink()

    def parts(self, parts):
        t = self.t
        cov, fs = ["/dev/nvme0n1 nvme0n1 259:0\n"], ["/dev/nvme0n1\n"]
        for name, devno, fstype, ptype, uuid in parts:
            cov.append(f"/dev/{name} {name} {devno} {fstype}".rstrip() + "\n")
            fs.append(f"/dev/{name} {fstype}".rstrip() + "\n")
            (t / f"ptype-{name}").write_text(f"{ptype}\n")
            (t / f"uuid-{name}").write_text(f"{uuid}\n" if uuid else "")
        (t / "lsblk-cov").write_text("".join(cov))
        (t / "lsblk-fs").write_text("".join(fs))

    def mounts(self, rows):
        """(target, source, fstype, fsroot, maj:min) each."""
        (self.t / "mounts").write_text("".join("\t".join(r) + "\n" for r in rows))

    def sh(self, script):
        return subprocess.run([BASH, "-c", f"{self.lib}\n{script}"], capture_output=True, text=True, timeout=60,
                              env={"PATH": f"{self.bin}:/usr/bin:/bin", "LC_ALL": "C", "TMPDIR": str(self.t)})

    def dump(self, **kw):
        p = self.t / "layout.json"
        p.write_text(json.dumps(layout(**kw)))
        return p

    # ---- check_coverage

    def test_coverage_passes_over_truenass_esp_and_names_it(self):
        r = self.sh("check_coverage nvme0n1; echo rc $?")
        self.assertIn("rc 0", r.stdout, r.stdout + r.stderr)
        self.assertIn("/dev/nvme0n1p2 (vfat F717-4973) isn't mounted: it is TrueNAS's ESP", r.stdout)
        self.assertNotIn("STOPPED", r.stdout)

    def test_coverage_stops_at_the_esp_without_midclt(self):
        self.midclt(False)
        r = self.sh("check_coverage nvme0n1; echo rc $?")
        self.assertIn("STOPPED: /dev/nvme0n1p2 (vfat) isn't mounted, so it wouldn't be backed up", r.stdout)
        self.assertNotIn("rc 0", r.stdout)

    def test_coverage_stops_at_the_esp_without_etc_version(self):
        (self.t / "version").unlink()
        r = self.sh("check_coverage nvme0n1; echo rc $?")
        self.assertIn("STOPPED: /dev/nvme0n1p2 (vfat) isn't mounted", r.stdout)

    def test_coverage_stops_at_a_vfat_that_isnt_of_the_esp_type(self):
        self.parts([PARTS[0], ("nvme0n1p2", "259:2", "vfat", "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7", "F717-4973"), PARTS[2]])
        r = self.sh("check_coverage nvme0n1; echo rc $?")
        self.assertIn("STOPPED: /dev/nvme0n1p2 (vfat) isn't mounted", r.stdout)

    def test_coverage_stops_at_other_unmounted_filesystems_on_truenas(self):
        self.parts(PARTS + [("nvme0n1p4", "259:4", "ext4", "0fc63daf-8483-4772-8e79-3d69d8477de4", "")])
        r = self.sh("check_coverage nvme0n1; echo rc $?")
        self.assertIn("TrueNAS's ESP", r.stdout)
        self.assertIn("STOPPED: /dev/nvme0n1p4 (ext4) isn't mounted", r.stdout)

    def test_coverage_counts_a_mounted_esp_as_mounted(self):
        self.mounts([("/", "boot-pool/ROOT/25.10.8", "zfs", "/", "0:30"), ("/boot/efi", "/dev/nvme0n1p2", "vfat", "/", "259:2")])
        r = self.sh("check_coverage nvme0n1; echo rc $?")
        self.assertIn("rc 0", r.stdout)
        self.assertNotIn("TrueNAS's ESP", r.stdout)

    # ---- truenas_check

    def check(self, **kw):
        return self.sh(f"truenas_check {self.dump(**kw)}; echo rc $?")

    def test_truenas_check_passes_the_thinkpads_dump(self):
        r = self.check()
        self.assertIn("rc 0", r.stdout, r.stdout + r.stderr)
        self.assertIn("TrueNAS 25.10.8's ESPs, not in the backup, which the restore formats with their volume IDs "
                      "and its grub-install makes again: /dev/nvme0n1p2 (F717-4973, 7 files)", r.stdout)

    def test_truenas_check_wants_boot_truenas(self):
        r = self.check(truenas=False)
        self.assertIn("rc 1", r.stdout)
        self.assertIn("bko-holt dump has no boot.truenas", r.stdout)

    def test_truenas_check_wants_25_10(self):
        for product in ("TrueNAS 26.0.0", "TrueNAS 25.04.2", None):
            r = self.check(product=product)
            self.assertIn("rc 1", r.stdout, product)
            self.assertIn("bko-holt load restores TrueNAS 25.10 only", r.stdout)

    def test_truenas_check_wants_each_esp_listed(self):
        r = self.check(esps=())
        self.assertIn("rc 1", r.stdout)
        self.assertIn("/dev/nvme0n1p2 isn't in its boot.truenas.esps", r.stdout)

    def test_truenas_check_wants_the_same_volume_id(self):
        r = self.check(esps=(("nvme0n1p2", "1234-ABCD"),))
        self.assertIn("rc 1", r.stdout)
        self.assertIn("/dev/nvme0n1p2's volume ID is F717-4973, but boot.truenas.esps says 1234-ABCD", r.stdout)
        # Case doesn't count.
        r = self.check(esps=(("nvme0n1p2", "f717-4973"),))
        self.assertIn("rc 0", r.stdout)

    def test_truenas_check_stops_at_files_the_remake_wouldnt_write(self):
        r = self.check(files=ESP_FILES + ["EFI/memtest/memtest.efi"])
        self.assertIn("rc 1", r.stdout)
        self.assertIn("/dev/nvme0n1p2 holds EFI/memtest/memtest.efi, which the restore's grub-install wouldn't write again", r.stdout)

    def test_truenas_check_stops_at_an_esp_the_dump_couldnt_read(self):
        r = self.check(read=False)
        self.assertIn("rc 1", r.stdout)
        self.assertIn("bko-holt dump couldn't read /dev/nvme0n1p2", r.stdout)

    def test_truenas_check_takes_a_grub_only_esp(self):
        r = self.check(files=["EFI/debian/grubx64.efi", "EFI/BOOT/BOOTX64.EFI"])
        self.assertIn("rc 0", r.stdout, r.stdout + r.stderr)
        self.assertIn("(F717-4973, 2 files)", r.stdout)

    def test_truenas_check_says_nothing_elsewhere(self):
        self.midclt(False)
        r = self.check(truenas=False, product=None)
        self.assertEqual(r.stdout, "rc 0\n", r.stderr)

    # ---- unhinted_check

    def test_unhinted_check_stops_at_an_inactive_boot_environment(self):
        sources = [{"path": "/", "from": "/", "fstype": "zfs", "source": "boot-pool/ROOT/25.10.8"},
                   {"path": "/run/bko-be/25.10.7", "from": None, "fstype": "zfs", "source": "boot-pool/ROOT/25.10.7",
                    "mount": {"device": "boot-pool/ROOT/25.10.7", "fstype": "zfs", "options": "ro"}}]
        r = self.sh(f"unhinted_check {self.dump(sources=sources)}; echo rc $?")
        self.assertIn("rc 1", r.stdout)
        self.assertIn("they'd come back empty: /run/bko-be/25.10.7", r.stdout)
        (self.t / "abroot.json").write_text("{}")
        r = self.sh(f"unhinted_check {self.dump(sources=sources)}; echo rc $?")
        self.assertEqual(r.stdout, "rc 0\n")

    def test_unhinted_check_passes_mounted_sources(self):
        r = self.sh(f"unhinted_check {self.dump()}; echo rc $?")
        self.assertEqual(r.stdout, "rc 0\n", r.stderr)

    # ---- internal_mounts

    def test_a_dataset_mounted_twice_is_in_the_tree_once_at_its_first_path(self):
        self.mounts([
            ("/", "boot-pool/ROOT/25.10.8", "zfs", "/", "0:30"),
            ("/var/lib", "boot-pool/ROOT/25.10.8/var/lib", "zfs", "/", "0:41"),
            ("/var/db/system", "boot-pool/.system", "zfs", "/", "0:50"),
            ("/var/db/system/cores", "boot-pool/.system/cores", "zfs", "/", "0:56"),
            ("/var/lib/systemd/coredump", "boot-pool/.system/cores", "zfs", "/", "0:56"),
            ("/boot/grub", "boot-pool/grub", "zfs", "/", "0:60"),
            ("/tmp/a", "/dev/sdb1", "xfs", "/", "8:17"),
        ])
        r = self.sh("internal_mounts nvme0n1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([line.split("\t")[0] for line in r.stdout.splitlines()],
                         ["/", "/boot/grub", "/var/db/system", "/var/db/system/cores", "/var/lib"])
        self.assertTrue(all(len(line.split("\t")) == 3 for line in r.stdout.splitlines()), r.stdout)


if __name__ == "__main__":
    unittest.main()
