"""ARM64 A4 (docs/design/arm64.md): the arm64 rescue image's build, with amd64's kept as it was.
stick/boot-test.py's --arch (arm64's QEMU: virt, AAVMF, xHCI or a SCSI CD, a NIC without a
ROM; no BIOS) and its "[ARCH] " lines in PLATFORM/checks.sh; slim-base-layer.sh --settings
and build-backotter-layer.sh --settings (tier 2's lists, mksquashfs's options, the bin
directory and DKMS's machine); make-img.sh's menu, against a small fake ISO.

The amd64 expectations are what each script did before A4.  The make-img.sh case needs
Linux's xorriso, mksquashfs and bash 4 (it runs on Bigfoot and the farm hosts, and is
skipped elsewhere).  No VM."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STICK = os.path.join(REPO, "stick")
SLIM = os.path.join(STICK, "slim-base-layer.sh")
LAYER = os.path.join(STICK, "build-backotter-layer.sh")
MAKE_IMG = os.path.join(STICK, "make-img.sh")


def load_boot_test():
    spec = importlib.util.spec_from_file_location("boot_test_a4", os.path.join(STICK, "boot-test.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BT = load_boot_test()
X86_HEAD = ["qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "4096",
            "-display", "none", "-serial", "stdio", "-monitor", "none"]
X86_STICK = ["-device", "qemu-xhci,id=usb", "-drive", "if=none,id=stick,format=qcow2,file=/t/stick.qcow2",
             "-device", "usb-storage,bus=usb.0,drive=stick,bootindex=1"]


def command(*args, **kw):
    with mock.patch.object(BT.subprocess, "run") as run, mock.patch.object(BT.shutil, "copy") as copy:
        cmd = BT.qemu_command("/x/i.img", *args, tmp="/t", **kw)
    return cmd, run, copy


class BootTestQemu(unittest.TestCase):
    def test_amd64_is_as_before(self):
        # What boot-test.py built before A4, for one case of each kind.
        self.assertEqual(command("linux", "uefi-secureboot", "usb")[0], X86_HEAD + X86_STICK + [
            "-machine", "q35,smm=on", "-global", "driver=cfi.pflash01,property=secure,value=on",
            "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd",
            "-drive", "if=pflash,format=raw,unit=1,file=/t/OVMF_VARS.fd"])
        self.assertEqual(command("linux", "bios", "cdrom")[0], X86_HEAD + [
            "-drive", "file=/x/i.img,media=cdrom,readonly=on", "-machine", "q35"])
        self.assertEqual(command("dragonfly", "bios", "usb")[0], X86_HEAD + [
            "-device", "usb-ehci,id=usb", "-drive", "if=none,id=stick,format=qcow2,file=/t/stick.qcow2",
            "-device", "usb-storage,bus=usb.0,drive=stick,bootindex=1", "-machine", "pc"])
        # arch's default is amd64.
        self.assertEqual(command("linux", "uefi", "usb")[0], command("linux", "uefi", "usb", arch="amd64")[0])

    def test_arm64_stick(self):
        cmd, run, copy = command("linux", "uefi", "usb", arch="arm64")
        self.assertEqual(cmd, [
            "qemu-system-aarch64", "-enable-kvm", "-machine", "virt,gic-version=host", "-cpu", "host",
            "-smp", "2", "-m", "4096", "-display", "none", "-serial", "stdio", "-monitor", "none",
            "-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0,romfile=",
            "-device", "qemu-xhci,id=usb", "-drive", "if=none,id=stick,format=qcow2,file=/t/stick.qcow2",
            "-device", "usb-storage,bus=usb.0,drive=stick,bootindex=1",
            "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd",
            "-drive", "if=pflash,format=raw,unit=1,file=/t/AAVMF_VARS.fd"])
        # The stick is an overlay on the image; the variables a copy of AAVMF's.
        self.assertIn("16G", run.call_args[0][0])
        copy.assert_called_once_with("/usr/share/AAVMF/AAVMF_VARS.fd", "/t/AAVMF_VARS.fd")

    def test_arm64_secure_boot_and_cd(self):
        cmd, _, copy = command("linux", "uefi-secureboot", "cdrom", arch="arm64")
        self.assertIn("if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.ms.fd", cmd)
        copy.assert_called_once_with("/usr/share/AAVMF/AAVMF_VARS.ms.fd", "/t/AAVMF_VARS.fd")
        # No SMM on arm64, and the CD is on virtio-scsi (virt has no IDE).
        self.assertFalse([a for a in cmd if "smm" in a or "q35" in a])
        i = cmd.index("virtio-scsi-pci,id=scsi0")
        self.assertEqual(cmd[i + 1:i + 5], [
            "-drive", "if=none,id=cd,file=/x/i.img,format=raw,media=cdrom,readonly=on",
            "-device", "scsi-cd,bus=scsi0.0,drive=cd,bootindex=1"])
        self.assertNotIn("qemu-xhci,id=usb", cmd)

    def test_arm64_refusals(self):
        with self.assertRaisesRegex(ValueError, "no bios mode"):
            command("linux", "bios", "usb", arch="arm64")
        with self.assertRaisesRegex(ValueError, "no freebsd image"):
            command("freebsd", "uefi", "usb", arch="arm64")
        for argv in (["i.img", "bios", "--arch", "arm64"],
                     ["i.img", "uefi", "--arch", "arm64", "--platform", "openbsd"]):
            with mock.patch.object(sys, "argv", ["boot-test.py"] + argv), \
                    mock.patch.object(BT, "run") as run, \
                    mock.patch("sys.stderr"), self.assertRaises(SystemExit) as e:
                BT.main()
            self.assertEqual(e.exception.code, 2)
            run.assert_not_called()

    def test_the_default_arch_is_the_hosts(self):
        for machine, arch in (("aarch64", "arm64"), ("arm64", "arm64"), ("x86_64", "amd64")):
            with mock.patch.object(BT.os, "uname") as uname:
                uname.return_value = mock.Mock(machine=machine)
                self.assertEqual(BT.host_arch(), arch)

    def test_main_passes_the_arch(self):
        with mock.patch.object(sys, "argv", ["boot-test.py", "i.img", "uefi", "--arch", "arm64"]), \
                mock.patch.object(BT, "run", return_value=True) as run:
            self.assertEqual(BT.main(), 0)
        self.assertEqual(run.call_args[0][-1], "arm64")


class CheckScript(unittest.TestCase):
    def lines(self, arch, media="usb"):
        return BT.check_script("linux", media, arch).splitlines()

    def test_arch_lines(self):
        amd64, arm64 = self.lines("amd64"), self.lines("arm64")
        self.assertIn("check llvm-gone '! ls /usr/lib/x86_64-linux-gnu/libLLVM* >/dev/null 2>&1'", amd64)
        self.assertIn("check llvm-gone '! ls /usr/lib/aarch64-linux-gnu/libLLVM* >/dev/null 2>&1'", arm64)
        names = lambda ls: [l.split()[1] for l in ls if l.startswith("check ")]
        self.assertIn("arm-firmware-gone", names(amd64))
        self.assertNotIn("x86-firmware-gone", names(amd64))
        self.assertNotIn("arm-firmware-kept", names(amd64))
        self.assertIn("x86-firmware-gone", names(arm64))
        self.assertIn("arm-firmware-kept", names(arm64))
        self.assertNotIn("arm-firmware-gone", names(arm64))
        for ls in (amd64, arm64):
            # No tag is typed, and each check is there once.
            self.assertFalse([l for l in ls if BT.ARCH_LINE.match(l)])
            self.assertEqual(len(names(ls)), len(set(names(ls))))
        # The rest is the same for both, in the same order.
        common = lambda ls: [l for l in ls if "firmware-" not in l and "llvm-gone" not in l]
        self.assertEqual(common(amd64), common(arm64))
        self.assertIn("check modem-firmware-kept 'ls -d /usr/lib/firmware/qcom/sdx61'", arm64)

    def test_the_filter(self):
        tmp = tempfile.mkdtemp(prefix="a4-checks-")
        self.addCleanup(shutil.rmtree, tmp)
        os.makedirs(os.path.join(tmp, "p"))
        with open(os.path.join(tmp, "p", "checks.sh"), "w") as f:
            f.write("# a comment\n\ncheck a 'x'\n[amd64] check b 'y'\n[arm64] check c 'z'\n"
                    "[riscv64] check d 'w'\n [arm64] check e 'v'\n")
        with mock.patch.object(BT, "HERE", tmp):
            self.assertEqual(BT.check_script("p", "cdrom"),
                             "check a 'x'\ncheck b 'y'\n[riscv64] check d 'w'\n [arm64] check e 'v'\n")
            self.assertEqual(BT.check_script("p", "cdrom", "arm64"),
                             "check a 'x'\ncheck c 'z'\n[riscv64] check d 'w'\n [arm64] check e 'v'\n")


def settings(script, *args, env=None):
    """The arrays and words a --settings run declares, as {name: value or [values]}."""
    sh = ('eval "$("$@")" || exit 1; for n in $(compgen -A variable); do '
          'case $n in TIER2_*|MKSQUASHFS_OPTS|BKO_ARCH|BIN_OS|ELF_MACHINE|KERNEL_MACHINE) '
          'eval "printf \'%s\\x1f\' $n \\"\\${$n[@]}\\""; echo;; esac; done')
    e = dict(os.environ, BKO_BIN="/nonexistent/bko-bin", BKO_HOLT_BIN="/nonexistent/holt-bin")
    e.pop("BKO_ARCH", None)
    e.update(env or {})
    r = subprocess.run(["bash", "-c", sh, "x", "bash", script] + list(args),
                       capture_output=True, text=True, env=e)
    if r.returncode:
        raise AssertionError(r.stderr)
    out = {}
    for line in r.stdout.splitlines():
        name, *vals = line.split("\x1f")[:-1]
        out[name] = vals
    return out


OLD_TIER2_EXCLUDE = [
    "qcom/*", "a300_p*.fw.zst", "nvidia/tegra*", "nvidia/gk20a/*", "nvidia/gm20b/*", "nvidia/gp10b/*",
    "dpaa2/*", "mediatek/mt81*", "mediatek/sof/*", "mediatek/sof-tplg/*", "vpu_[dp].bin.zst",
    "mediatek/mt7622*", "mediatek/mt798*", "amlogic/*", "meson/*", "rockchip/*", "imx/*", "amphion/*",
    "cnm/*", "arm/*", "powervr/*", "s5p-mfc*", "rcar_gen4_pcie*", "r8a779x_usb3_*"]
OLD_TIER2_INCLUDE = ["qcom/sdx35/*", "qcom/sdx61/*", "qcom/aic100/*", "qcom/qdu100/*"]
OLD_SQUASHFS = ["-comp", "xz", "-Xbcj", "x86", "-b", "1M", "-Xdict-size", "1M", "-noappend", "-no-progress"]
ARM64_SQUASHFS = ["-comp", "xz", "-b", "1M", "-Xdict-size", "1M", "-noappend", "-no-progress"]


class SlimSettings(unittest.TestCase):
    def test_amd64_is_as_before(self):
        s = settings(SLIM, "--settings", "amd64")
        self.assertEqual(s["TIER2_EXCLUDE"], OLD_TIER2_EXCLUDE)
        self.assertEqual(s["TIER2_INCLUDE"], OLD_TIER2_INCLUDE)
        self.assertEqual(s["MKSQUASHFS_OPTS"], OLD_SQUASHFS)

    def test_arm64(self):
        s = settings(SLIM, "--settings", "arm64")
        self.assertEqual(s["MKSQUASHFS_OPTS"], ARM64_SQUASHFS)
        self.assertEqual(s["TIER2_INCLUDE"], [])
        # x86 SoCs' firmware goes, and none of the ARM firmware amd64's image drops.
        for pat in ("intel/sof*", "intel/ish/*", "intel/ipu/*", "intel/vpu/*", "amdnpu/*"):
            self.assertIn(pat, s["TIER2_EXCLUDE"])
        self.assertFalse(set(s["TIER2_EXCLUDE"]) & set(OLD_TIER2_EXCLUDE))
        # What the arm64 kernel's PCIe and USB drivers load stays.
        for kept in ("iwlwifi-", "intel/ibt-", "intel/ice/", "intel/qat/", "i915/", "xe/", "amdgpu/",
                     "intel/vsc/", "qcom/x1e80100/", "nvidia/tegra194/"):
            self.assertFalse([p for p in s["TIER2_EXCLUDE"] if p.startswith(kept)], kept)

    def test_an_unknown_arch(self):
        r = subprocess.run(["bash", SLIM, "--settings", "riscv64"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("amd64 or arm64 only", r.stderr)


class LayerSettings(unittest.TestCase):
    def test_amd64_is_as_before(self):
        for env in ({}, {"BKO_ARCH": "amd64"}):
            s = settings(LAYER, "--settings", env=env)
            self.assertEqual(s["MKSQUASHFS_OPTS"], OLD_SQUASHFS)
            self.assertEqual((s["BKO_ARCH"], s["BIN_OS"], s["ELF_MACHINE"], s["KERNEL_MACHINE"]),
                             (["amd64"], ["linux"], ["3e00"], ["x86_64"]))

    def test_arm64(self):
        s = settings(LAYER, "--settings", env={"BKO_ARCH": "arm64"})
        self.assertEqual(s["MKSQUASHFS_OPTS"], ARM64_SQUASHFS)
        self.assertEqual((s["BIN_OS"], s["ELF_MACHINE"], s["KERNEL_MACHINE"]),
                         (["linux-arm64"], ["b700"], ["aarch64"]))

    def test_the_dkms_path_reads_the_machine(self):
        with open(LAYER, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("kb_dkms=/var/lib/dkms/bcachefs/$BCACHEFS_VERSION/$KVER/$KERNEL_MACHINE\n", text)
        self.assertNotIn("/x86_64\n", text)


# --- make-img.sh against a fake ISO ------------------------------------------------------------

STOCK_GRUB = {
    "amd64": """set timeout=30

loadfont unicode

set menu_color_normal=white/black
set menu_color_highlight=black/light-gray

menuentry "Try or Install Ubuntu Server" {
    set gfxpayload=keep
    linux  /casper/vmlinuz  ---
    initrd /casper/initrd
}
grub_platform
if [ "$grub_platform" = "efi" ]; then
menuentry 'Boot from next volume' {
    exit 1
}
menuentry 'UEFI Firmware Settings' {
    fwsetup
}
fi
""",
    "arm64": """set timeout=30

loadfont unicode

set menu_color_normal=white/black
set menu_color_highlight=black/light-gray

set cmdline=
smbios --type 4 --get-string 5 --set proc_version
regexp "Snapdragon.*" "$proc_version"
if [ $? = 0 ]; then
  # Work around Snapdragon X firmware bug. cutmem is not allowed in lockdown mode.
  if [ $lockdown != "y" ]; then
    cutmem 0x8800000000 0x8fffffffff
  fi
  # arm64.nopauth works around 8cx Gen 3 firmware bug
  cmdline="clk_ignore_unused pd_ignore_unused arm64.nopauth"
fi

menuentry "Try or Install Ubuntu Server" {
    set gfxpayload=keep
    linux  /casper/vmlinuz $cmdline  ---  console=tty0
    initrd /casper/initrd
}
menuentry 'Boot from next volume' {
    exit 1
}
menuentry 'UEFI Firmware Settings' {
    fwsetup
}
""",
}


def menu(arch, timeout=10, default=0):
    """The menu make-img.sh writes; amd64's is what it wrote before A4."""
    serial = {"amd64": "ttyS0", "arm64": "ttyAMA0"}[arch]
    pre = "$cmdline " if arch == "arm64" else ""

    def entry(title, args):
        return (f'menuentry "{title}" {{\n    set gfxpayload=keep\n'
                f"    linux  /casper/vmlinuz {pre}layerfs-path=ubuntu-server-minimal.backotter.squashfs "
                f"hostname=backotter nopersistent {args} ---\n    initrd /casper/initrd\n}}\n")
    stock = STOCK_GRUB[arch]
    head = (f"set timeout={timeout}\nset default={default}\n\nloadfont unicode\n\n"
            "set menu_color_normal=white/black\nset menu_color_highlight=black/light-gray\n\n")
    if arch == "arm64":
        head += stock[stock.index("set cmdline="):stock.index("\nfi\n") + 4] + "\n"
    firmware = stock[stock.index("grub_platform"):] if arch == "amd64" else \
        stock[stock.index("menuentry 'Boot from next volume'"):]
    return (head + entry("BackOtter rescue", "") + entry("BackOtter rescue (safe graphics)", "nomodeset")
            + entry(f"BackOtter rescue (serial console {serial})", f"console=tty0 console={serial},115200n8")
            + firmware)


def wr(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)
    os.chmod(path, mode)


def fake_inputs(tmp, arch):
    """A stock ISO with the files make-img.sh reads and drops, and slim and layer
    directories as slim-base-layer.sh and build-backotter-layer.sh would leave them."""
    iso = os.path.join(tmp, "iso")
    base = "ubuntu-server-minimal"
    files = {
        "boot/grub/grub.cfg": STOCK_GRUB[arch],
        ".disk/info": f'Ubuntu-Server 26.04.1 LTS "Resolute Raccoon" - Release {arch} (20260826)\n',
        f"casper/{base}.squashfs": "stock base\n", f"casper/{base}.manifest.full": "x\n",
        f"casper/{base}.ubuntu-server.squashfs": "server\n", "casper/install-sources.yaml": "-\n",
        "casper/vmlinuz": "old kernel\n", "casper/initrd": "old initrd\n",
        "pool/main/z/zfs.deb": "deb\n", "dists/resolute/Release": "release\n",
    }
    import hashlib
    for p, d in files.items():
        wr(os.path.join(iso, p), d)
    wr(os.path.join(iso, "md5sum.txt"), "".join(
        f"{hashlib.md5(d.encode()).hexdigest()}  ./{p}\n" for p, d in sorted(files.items())))
    subprocess.run(["xorriso", "-as", "mkisofs", "-quiet", "-R", "-V", "Ubuntu-Server 26.04.1 LTS",
                    "-o", os.path.join(tmp, "stock.iso"), iso], check=True, capture_output=True)
    slim, layer = os.path.join(tmp, "slim"), os.path.join(tmp, "layer")
    for p in (f"{base}.squashfs", f"{base}.manifest", f"{base}.size", "vmlinuz", "initrd"):
        wr(os.path.join(slim, p), f"slim {p}\n")
    tree = os.path.join(tmp, "layer-tree")
    wr(os.path.join(tree, "usr/local/bin/bko-holt"), "#!/bin/sh\n", 0o755)
    wr(os.path.join(tree, "usr/local/bin/rustic-bko"), "#!/bin/sh\n", 0o755)
    wr(os.path.join(tree, "usr/share/zfs/compatibility.d/openzfs-2.4"), "# features\nasync_destroy\nzilsaxattr\n")
    os.makedirs(layer)
    subprocess.run(["mksquashfs", tree, os.path.join(layer, f"{base}.backotter.squashfs"), "-quiet",
                    "-noappend", "-no-progress"], check=True, capture_output=True)
    wr(os.path.join(layer, f"{base}.backotter.manifest"),
       f"linux-image-7.0.0-43-generic:{arch}\t7.0.0-43.43\nzfsutils-linux\t2.4.1-1ubuntu5\n")
    wr(os.path.join(layer, f"{base}.backotter.size"), "1234\n")
    # The config partition's FAT tools: stand-ins (marker.py only runs them).
    stubs = os.path.join(tmp, "stubs")
    for c in ("mkfs.vfat", "mmd", "mcopy"):
        wr(os.path.join(stubs, c), "#!/bin/sh\nexit 0\n", 0o755)
    return os.path.join(tmp, "stock.iso"), slim, layer, stubs


def bash_major():
    try:
        r = subprocess.run(["bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True)
        return int(r.stdout.strip() or 0)
    except (OSError, ValueError):
        return 0


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("xorriso") and shutil.which("mksquashfs")
                     and bash_major() >= 4, "needs Linux's xorriso, mksquashfs and bash 4")
class MakeImgMenu(unittest.TestCase):
    def build(self, arch, *opts):
        tmp = tempfile.mkdtemp(prefix=f"a4-img-{arch}-")
        self.addCleanup(shutil.rmtree, tmp)
        iso, slim, layer, stubs = fake_inputs(tmp, arch)
        out = os.path.join(tmp, "out")
        os.mkdir(out)
        env = dict(os.environ, PATH=stubs + ":" + os.environ["PATH"])
        r = subprocess.run(["bash", MAKE_IMG, *opts, iso, slim, layer, out], capture_output=True, text=True,
                           env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        (img,) = os.listdir(out)
        got = os.path.join(tmp, "got")
        subprocess.run(["xorriso", "-osirrox", "on", "-indev", os.path.join(out, img),
                        "-extract", "/boot/grub/grub.cfg", got + "-grub.cfg",
                        "-extract", "/md5sum.txt", got + "-md5sum.txt"], check=True, capture_output=True)
        r = subprocess.run(["xorriso", "-indev", os.path.join(out, img), "-report_el_torito", "as_mkisofs"],
                           capture_output=True, text=True)
        self.layout = r.stdout
        with open(got + "-grub.cfg") as f, open(got + "-md5sum.txt") as g:
            return img, f.read(), g.read()

    def test_amd64_is_as_before(self):
        img, grub, md5 = self.build("amd64")
        self.assertTrue(img.startswith("backotter-live-linux-dev.") and img.endswith("-amd64.img"), img)
        self.assertEqual(grub, menu("amd64"))
        self.assertNotIn("-partition_offset 16", self.layout)
        self.assertIn("  ./boot/grub/grub.cfg\n", md5)
        self.assertNotIn("./pool/", md5)

    def test_amd64_test_menu(self):
        self.assertEqual(self.build("amd64", "--test")[1], menu("amd64", 3, 2))

    def test_arm64(self):
        img, grub, md5 = self.build("arm64", "--test")
        self.assertTrue(img.endswith("-arm64.img"), img)
        self.assertEqual(grub, menu("arm64", 3, 2))
        self.assertIn("console=tty0 console=ttyAMA0,115200n8 ---", grub)
        self.assertNotIn("ttyS0", grub)
        self.assertNotIn("grub_platform", grub)
        # The ISO 9660 tree again at partition 1's start, so casper mounts that, not the stick.
        self.assertIn("-partition_offset 16", self.layout)
