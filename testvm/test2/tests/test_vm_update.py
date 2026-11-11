"""V1 (package K2): vm-update's merge rule, gate, hooks, fleet lock, RAM rules and merges,
in-guest.sh's steps and sections, pkgset.sh under stub commands, and fingerprint.py over the
corpus dumps.  No VM, no ssh and no host: everything that would be one is a stand-in."""

import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
import warnings
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
T2 = TESTS.parent
TESTVM = T2.parent
REPO = TESTVM.parent
FIX = TESTS / "fixtures" / "v1"
CORPUS = REPO / "holt" / "tests" / "corpus"


def load_vm_update():
    loader = importlib.machinery.SourceFileLoader("vm_update_under_test", str(TESTVM / "vm-update"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def load_fingerprint():
    spec = importlib.util.spec_from_file_location("fingerprint_under_test", TESTVM / "update" / "fingerprint.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


VU = load_vm_update()
FP = load_fingerprint()
JUDGE = FP.load_judge()

GUESTS = {"debian12": "dpkg", "fedora44": "rpm", "arch": "pacman", "alpine324": "apk", "void-musl": "xbps",
          "aerynos": "moss", "nixos2605": "nixos", "freebsd15": "freebsd", "hardenedbsd15": "hardenedbsd",
          "ghostbsd26": "freebsd", "netbsd11": "netbsd", "alpinebsd": "alpinebsd", "openbsd8": "openbsd",
          "dragonfly6": "dragonfly", "omnios58": "illumos", "haiku6": "haiku"}


def dump_of(guest):
    with open(CORPUS / guest / "src.json", encoding="utf-8") as f:
        return json.load(f)


def pkgset_of(guest):
    return (FIX / "pkgset" / f"{guest}.txt").read_text()


def build(guest, dump=None, pkgset=None):
    return FP.build(dump if dump is not None else dump_of(guest), pkgset if pkgset is not None else pkgset_of(guest), JUDGE)


class TempDir(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)   # a Guest keeps its log and lock open for life
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="vmu-")))
        self.addCleanup(shutil.rmtree, self.tmp, True)


# --- the merge rule ----------------------------------------------------------------------------

A = "base pm dpkg\npkg e2fsprogs 1.47.0-2 amd64\npkg hello 2.10-3 amd64\n"


class TestMergeRule(unittest.TestCase):
    def test_same_sets_no_merge(self):
        d = VU.merge_decision(A, "pkg hello 2.10-3 amd64\nbase pm dpkg\n\npkg e2fsprogs 1.47.0-2 amd64\n", [])
        self.assertFalse(d["decided"])
        self.assertFalse(d["pkg_changed"])
        self.assertFalse(d["unknown"])
        self.assertEqual(d["why"], "nothing changed")

    def test_a_version_moved(self):
        d = VU.merge_decision(A, A.replace("2.10-3", "2.10-4"), [])
        self.assertTrue(d["decided"])
        self.assertTrue(d["pkg_changed"])
        self.assertEqual(d["packages"], 1)

    def test_a_package_gone(self):
        self.assertTrue(VU.merge_decision(A, "base pm dpkg\npkg e2fsprogs 1.47.0-2 amd64\n", [])["decided"])

    def test_a_step_alone_merges(self):
        d = VU.merge_decision(A, A, ["rc.conf: IPv4 first"])
        self.assertTrue(d["decided"])
        self.assertFalse(d["pkg_changed"])
        self.assertEqual(d["steps"], ["rc.conf: IPv4 first"])

    def test_unknown_either_side_merges(self):
        for before, after in ((A, "unknown no listing for Plan9\n"), ("unknown x\n", A), ("unknown\n", "unknown\n")):
            d = VU.merge_decision(before, after, [])
            self.assertTrue(d["decided"], (before, after))
            self.assertTrue(d["unknown"])
            self.assertFalse(d["pkg_changed"])

    def test_an_empty_or_garbled_listing_is_unknown(self):
        self.assertTrue(VU.merge_decision(A, "", [])["unknown"])
        self.assertTrue(VU.merge_decision(A, "   \n", [])["unknown"])
        self.assertTrue(VU.merge_decision(A, A + "bash: moss: not found\n", [])["unknown"])

    def test_the_gate_starts_unset(self):
        self.assertIsNone(VU.merge_decision(A, A, [])["gate"])


class TestSteps(unittest.TestCase):
    def test_parse(self):
        out = textwrap.dedent("""\
            in-guest: Linux 6.1.0 x86_64
            in-guest: step: unfreezing: /etc/x from a to b
            Get:1 http://deb.debian.org in-guest: step: not at a line's start
            in-guest: updated
            in-guest: step:   sysupgrade.conf: the daily
            """)
        self.assertEqual(VU.parse_steps(out), ["unfreezing: /etc/x from a to b", "sysupgrade.conf: the daily"])

    def test_no_steps(self):
        self.assertEqual(VU.parse_steps("in-guest: updated\nin-guest: NILFS: 3 MiB free\n"), [])


class TestInGuest(unittest.TestCase):
    TEXT = (TESTVM / "update" / "in-guest.sh").read_text()
    NAMES = {"prologue", "NixOS", "Linux.common", "Linux.apt", "Linux.dnf", "Linux.pacman", "Linux.zypper",
             "Linux.xbps", "Linux.apk", "Linux.moss", "FreeBSD", "DragonFly", "OpenBSD", "NetBSD", "SunOS",
             "Haiku", "Redox"}

    def sections(self):
        out, cur = {}, None
        for line in self.TEXT.splitlines():
            m = re.match(r"\s*# t2-section: (\S+)\s*$", line)
            if m:
                cur = m[1]
                out.setdefault(cur, [])
            elif cur:
                out[cur].append(line)
        return {k: "\n".join(v) for k, v in out.items()}

    def test_every_name_and_no_other(self):
        self.assertEqual(set(self.sections()), self.NAMES)

    def test_blocks_carry_their_package_manager(self):
        s = self.sections()
        for name, needle in (("Linux.apt", "snapshot.ubuntu.com"), ("Linux.apt", "Devuan's"), ("Linux.apt", "nilfs2"),
                             ("Linux.apt", "zfs-list.cache"), ("Linux.apt", "has apt-get"),
                             ("Linux.pacman", "geo.mirror.pkgbuild.com"), ("Linux.pacman", "Artix's mirrors"),
                             ("Linux.pacman", "@factory"), ("Linux.pacman", "has pacman"),
                             ("Linux.dnf", "fedora-updates.repo"), ("Linux.dnf", "vault"), ("Linux.dnf", "has dnf"),
                             ("Linux.zypper", "history"), ("Linux.zypper", "install DVD"), ("Linux.zypper", "has zypper"),
                             ("Linux.moss", "unstable stream"), ("Linux.moss", "has moss"),
                             ("Linux.xbps", "has xbps-install"), ("Linux.apk", "apk upgrade"),
                             ("Linux.common", "/etc/os-release"), ("Linux.common", "no package manager found"),
                             ("prologue", "step() {"), ("prologue", 'say "updated"'), ("prologue", "no updater for"),
                             ("NixOS", "nixos-rebuild"), ("FreeBSD", "freebsd-update"), ("DragonFly", "buildworld"),
                             ("OpenBSD", "syspatch"), ("NetBSD", "sysupgrade"), ("SunOS", "pkg update"),
                             ("Haiku", "pkgman"), ("Redox", "pkg --yes update")):
            self.assertIn(needle, s[name], name)

    def test_linux_common_holds_no_package_manager(self):
        common = self.sections()["Linux.common"]
        for word in ("apt-get", "dnf", "pacman", "zypper", "xbps", "moss"):
            self.assertNotIn(word, common)

    def test_what_is_not_a_fix_is_no_step(self):
        for text in ('"updated"', "NILFS: $((", "no packages at", "is the one installed", "holds NetBSD", "$(uname -srm)",
                     "base system: current", "base system: rebuilding"):
            for line in self.TEXT.splitlines():
                if text in line and line.lstrip().startswith("step "):
                    self.fail(line)

    def test_the_silent_fixes_are_guarded_steps(self):
        t = self.TEXT
        self.assertIn('if ! grep -qF "\\"nixpkgs=$url\\"" /etc/nixos/configuration.nix; then', t)
        self.assertIn("! apt-mark showhold | grep -qx snapd; then", t)
        self.assertIn('if [ -z "$(ls /etc/openssl/certs)" ]; then', t)
        self.assertIn('if [ "$(cat /usr/pkg/etc/pkgin/repositories.conf 2>/dev/null)" != "$pkgs" ]; then', t)
        self.assertIn("if cmp -s /usr/pkg/etc/sysupgrade.conf.new /usr/pkg/etc/sysupgrade.conf; then", t)

    def test_shell_parses(self):
        r = subprocess.run(["sh", "-n", str(TESTVM / "update" / "in-guest.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


# --- pkgset.sh under stub commands --------------------------------------------------------------

class TestPkgset(TempDir):
    def run_script(self, system, stubs, files=(), nixos=False):
        """pkgset.sh with its PATH line gone, absolute paths under the temp root, and STUBS
        {command: shell body} first on PATH."""
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        root = self.tmp / "root"
        all_stubs = dict(stubs, uname=f"echo {system}")
        for name, body in all_stubs.items():
            p = bindir / name
            p.write_text("#!/bin/sh\n" + body + "\n")
            p.chmod(0o755)
        for path, text in files:
            f = root / path.lstrip("/")
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        if nixos:
            (root / "etc").mkdir(parents=True, exist_ok=True)
            (root / "etc/NIXOS").write_text("")
        script = (TESTVM / "update" / "pkgset.sh").read_text()
        script = re.sub(r"^PATH=.*$", "", script, count=1, flags=re.M)
        for p in ("/etc/NIXOS", "/var/db/", "/boot/system/packages"):
            script = script.replace(p, str(root) + p)
        tools = self.tmp / "tools"                      # only these: a host's own apt-get would be found first
        tools.mkdir(exist_ok=True)
        for t in ("sed", "awk", "sort", "tr", "ls", "wc", "cat", "tail", "readlink"):
            if not (tools / t).exists():
                (tools / t).symlink_to(shutil.which(t))
        env = dict(os.environ, PATH=f"{bindir}:{tools}")
        r = subprocess.run([shutil.which("sh"), "-s"], input=script, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def check(self, out, want):
        for line in want:
            self.assertIn(line, out.splitlines(), out)
        self.assertIsNotNone(VU.listing_lines(out), out)

    def test_dpkg(self):
        out = self.run_script("Linux", {
            "apt-get": "true",
            "dpkg-query": "echo 'ii  e2fsprogs 1.47.0-2 amd64'; echo 'rc  gone 1.0 amd64'"})
        self.check(out, ["base pm dpkg", "pkg e2fsprogs 1.47.0-2 amd64"])
        self.assertNotIn("gone", out)

    def test_rpm_via_zypper(self):
        out = self.run_script("Linux", {"zypper": "true", "rpm": "echo 'e2fsprogs 0:1.47-1.x86_64'"})
        self.check(out, ["base pm rpm", "pkg e2fsprogs 0:1.47-1.x86_64"])

    def test_pacman(self):
        self.check(self.run_script("Linux", {"pacman": "echo 'linux 7.2.arch1-1'"}), ["base pm pacman", "pkg linux 7.2.arch1-1"])

    def test_xbps(self):
        out = self.run_script("Linux", {"xbps-install": "true", "xbps-query": "echo 'ii xbps-triggers-0.128_1 Triggers'"})
        self.check(out, ["base pm xbps", "pkg xbps-triggers 0.128_1"])

    def test_apk(self):
        out = self.run_script("Linux", {"apk": "echo linux-lts-6.18.60-r0; echo musl-1.2.5-r9"})
        self.check(out, ["base pm apk", "pkg linux-lts 6.18.60-r0", "pkg musl 1.2.5-r9"])

    # moss's own lines, as aerynos printed them over ssh on 2026-11-10 (moss v0.26.10, 64070b2).
    MOSS = ("acl                                     2.4.0-6 - Access control list utilities\n"
            "bash                                  5.3.20-34 - GNU Bourne-Again Shell\n"
            "dbus-broker                               37-13 - Linux D-Bus Message Broker\n"
            "dracut                                   112-15 - dracut is an event driven initramfs infrastructure.\n"
            "systemd-boot                          261.4-107 - \n")

    def moss(self, listing):
        """A moss that knows its October 2026 words: `pkg list --installed` prints LISTING, and the
        old `list installed` fails as it does there."""
        return {"moss": "if [ \"$*\" = 'pkg list --installed' ]; then\ncat <<'EOF'\n" + listing + "EOF\nexit 0\nfi\n"
                        "echo \"error: unrecognized subcommand '$1'\" >&2\nexit 2"}

    def test_moss(self):
        out = self.run_script("Linux", self.moss(self.MOSS))
        self.check(out, ["base pm moss", "pkg acl 2.4.0-6", "pkg bash 5.3.20-34", "pkg dbus-broker 37-13",
                         "pkg dracut 112-15", "pkg systemd-boot 261.4-107"])
        self.assertEqual(len(out.splitlines()), 6, out)

    def test_moss_that_fails_is_unknown_alone(self):
        out = self.run_script("Linux", {"moss": "echo \"error: unrecognized subcommand 'list'\" >&2; exit 2"})
        self.assertEqual(out, "unknown moss pkg list --installed failed (exit status 2)\n")

    def test_moss_another_line_is_unknown_alone(self):
        # A header, the shape the old parser took for moss's, and a sync line.
        for extra in ("Installed packages:\n", "bash 5.3-2\n", "acl   2.4.0-6 => 2.4.1-7 - Access control list utilities\n"):
            out = self.run_script("Linux", self.moss(self.MOSS + extra))
            self.assertEqual(out, f"unknown moss pkg list --installed printed: {extra.strip()[:60]}\n")
            self.assertIsNone(VU.listing_lines(out))

    def test_moss_listing_nothing_is_unknown(self):
        self.assertEqual(self.run_script("Linux", self.moss("")), "unknown moss pkg list --installed listed nothing\n")

    def test_nixos(self):
        sysp = "/nix/store/aaaa-nixos-system-26.05"
        out = self.run_script("Linux", {
            "readlink": f"echo {sysp}",
            "nix-store": "echo /nix/store/bbbb-e2fsprogs-1.47.3-bin; echo /nix/store/cccc-e2fsprogs-1.47.3;"
                         " echo /nix/store/dddd-util-linux-2.41-lib; echo /nix/store/eeee-source"}, nixos=True)
        self.check(out, ["base pm nixos", f"nix-system {sysp}", "nix-pkg e2fsprogs 1.47.3", "nix-pkg util-linux 2.41"])
        self.assertEqual(out.count("nix-pkg e2fsprogs"), 1)
        self.assertNotIn("source", out)

    def freebsd(self, **extra):
        return dict({"sha256": 'echo "abc$2"', "freebsd-version": 'echo "15.1-RELEASE-$1"',
                     "zfs": "echo zfs-2.3.4-FreeBSD_g12; echo zfs-kmod-2.3.4", "pkg": 'case $1 in -N) exit 0;; query) echo "FreeBSD-runtime 15.1";; esac'},
                    **extra)

    def test_freebsd(self):
        out = self.run_script("FreeBSD", self.freebsd())
        self.check(out, ["base pm freebsd", "base kernel abc/boot/kernel/kernel", "base libc abc/lib/libc.so.7",
                         "base freebsd-version-k 15.1-RELEASE--k", "base zfs 2.3.4-FreeBSD_g12", "pkg FreeBSD-runtime 15.1"])

    def test_hardenedbsd_no_pkg_runs_no_pkg(self):
        out = self.run_script("FreeBSD", self.freebsd(**{
            "hbsd-update": "true", "pkg": 'case $1 in -N) exit 1;; *) echo should-not-run; exit 1;; esac'}))
        self.check(out, ["base pm hardenedbsd"])
        self.assertNotIn("should-not-run", out)

    def test_a_failed_base_read_is_unknown_alone(self):
        out = self.run_script("FreeBSD", self.freebsd(sha256="exit 1"))
        self.assertEqual(out.strip(), "unknown no sha256 of the kernel")

    def test_dragonfly(self):
        out = self.run_script("DragonFly", {"pkg": 'case $1 in -N) exit 0;; query) echo "bash 5.3";; esac'},
                              files=[("/var/db/bko-base", "cafe\n")])
        self.check(out, ["base pm dragonfly", "base bko-base cafe", "pkg bash 5.3"])

    def test_dragonfly_before_its_first_rebuild_has_no_base_line(self):
        out = self.run_script("DragonFly", {"pkg": 'case $1 in -N) exit 0;; query) echo "bash 5.3";; esac'})
        self.assertNotIn("bko-base", out)

    def test_openbsd(self):
        out = self.run_script("OpenBSD", {"syspatch": "echo 001_nfs; echo 002_x", "pkg_info": "echo quirks-7.14; echo python-3.12.9p0"})
        self.check(out, ["base pm openbsd", "syspatch 001_nfs", "base syspatch-count 2", "pkg quirks 7.14", "pkg python 3.12.9p0"])

    def test_openbsd_no_patches_yet(self):
        out = self.run_script("OpenBSD", {"syspatch": "true", "pkg_info": "true"})
        self.check(out, ["base syspatch-count 0"])

    def test_netbsd(self):
        out = self.run_script("NetBSD", {"cksum": "echo 'SHA512 (x) = feed'", "pkg_info": "echo pkgin-25.10.0"},
                              files=[("/var/db/bko-netbsd-daily.sha512", "x")])
        self.check(out, ["base pm netbsd", "base netbsd-daily feed", "pkg pkgin 25.10.0"])

    def test_alpinebsd(self):
        self.check(self.run_script("NetBSD", {"apk": "echo netbsd-base-11.1-r0"}), ["base pm alpinebsd", "pkg netbsd-base 11.1-r0"])

    def test_illumos(self):
        out = self.run_script("SunOS", {"pkg": "echo 'system/kernel  0.5.11-151058.0 i--'; echo 'x/y (extra)  1.0 i--'"})
        self.check(out, ["base pm illumos", "pkg system/kernel 0.5.11-151058.0", "pkg x/y 1.0"])

    def test_haiku(self):
        out = self.run_script("Haiku", {}, files=[("/boot/system/packages/haiku-r1~beta6_hrev1-1-x86_64.hpkg", ""),
                                                  ("/boot/system/packages/bash-5.2-1-x86_64.hpkg", "")])
        self.check(out, ["base pm haiku", "pkg haiku r1~beta6_hrev1-1-x86_64", "pkg bash 5.2-1-x86_64"])

    def test_nothing_known_is_unknown(self):
        self.assertTrue(self.run_script("Plan9", {}).startswith("unknown"))
        self.assertTrue(self.run_script("Linux", {"apt-get": "true", "dpkg-query": "exit 2"}).startswith("unknown"))


# --- fingerprint.py ----------------------------------------------------------------------------

class TestFingerprint(unittest.TestCase):
    def test_every_os_kind_builds(self):
        for g, kind in GUESTS.items():
            r = build(g)
            self.assertEqual(r["facts"]["os_kind"], kind, g)
            for d in (r["holt"]["digest"], r["stic"]["digest"], r["stic"]["fine"], r["facts"]["layout"]):
                self.assertRegex(d, r"^[0-9a-f]{64}$")
            self.assertTrue(r["facts"]["kernel"], g)
            self.assertTrue(r["facts"]["kernel_line"], g)
            self.assertTrue(r["facts"]["boot"] or r["facts"]["storage"], g)

    def test_kernel_lines(self):
        want = {"debian12": "6.1", "freebsd15": "15.1", "netbsd11": "11.1", "openbsd8": "8.0", "dragonfly6": "6.4",
                "omnios58": "r151058", "haiku6": "R1~beta6", "nixos2605": "6.18"}
        for g, line in want.items():
            self.assertEqual(build(g)["facts"]["kernel_line"], line, g)

    def test_the_kernels_each_kind_reads(self):
        d = dump_of("hardenedbsd15")
        r = build("hardenedbsd15", d)
        self.assertEqual(r["facts"]["kernel"], "3" * 64)     # its version string doesn't move
        self.assertEqual(build("freebsd15")["facts"]["kernel"], dump_of("freebsd15")["os"]["kernel_version"])
        self.assertEqual(build("debian12")["facts"]["kernel"], dump_of("debian12")["os"]["kernel_release"])

    def test_user_at_host_is_left_out(self):
        for g in ("netbsd11", "dragonfly6"):
            d1, d2 = dump_of(g), dump_of(g)
            base = re.sub(r"\S+:/\S+$", "", d1["os"]["kernel_version"]).rstrip()
            at = chr(64)                                # no address-shaped text in the repository
            d1["os"]["kernel_version"] = f"{base}  builder{at}one:/usr/obj/GENERIC"
            d2["os"]["kernel_version"] = f"{base}  other{at}two:/usr/src/sys"
            a, b = build(g, d1), build(g, d2)
            self.assertEqual(a["facts"]["kernel"], base.rstrip(), g)
            self.assertEqual(a["holt"]["digest"], b["holt"]["digest"], g)

    def test_stable_under_the_volatile_fields(self):
        for g in GUESTS:
            d = dump_of(g)
            a = build(g, d)
            e = copy.deepcopy(d)
            e["generated"] = "2030-01-01T00:00:00Z"
            e["tool"]["version"] = "9.9.9"
            e["host"]["hostname"] = "elsewhere"
            e["warnings"] = ["a warning"]
            b = build(g, e)
            self.assertEqual(a["holt"]["digest"], b["holt"]["digest"], g)
            self.assertEqual(a["stic"], b["stic"], g)

    def test_stable_under_usage_and_boot_order(self):
        d = dump_of("tumbleweed")
        ps = "base pm rpm\npkg e2fsprogs 0:1.47-1\npkg grub2-common 1:2.16-4\n"
        a = FP.layout(d, JUDGE)
        e = copy.deepcopy(d)
        moved = 0
        def walk(v):
            nonlocal moved
            if isinstance(v, dict):
                for k, x in v.items():
                    if k in ("mounted_at", "mounted_subvolumes") and isinstance(x, list) and len(x) > 1:
                        x.reverse()
                        moved += 1
                    elif k in ("used_bytes", "available_bytes", "free_blocks") and isinstance(x, int):
                        v[k] = x + 4096
                        moved += 1
                    else:
                        walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
        walk(e)
        e["storage"]["mounts"].reverse()
        self.assertGreater(moved, 0)
        self.assertEqual(a, FP.layout(e, JUDGE))
        self.assertEqual(build("tumbleweed", d, ps)["facts"]["layout"], a)

    def test_stable_under_versions_in_the_dump(self):
        d = dump_of("debian12")
        a = FP.layout(d, JUDGE)
        e = copy.deepcopy(d)
        e["os"]["kernel_release"] = "9.9.9"
        e["boot"]["packages"]["grub-pc"] = "9"
        e["boot"]["kernels"]["installed"] = []
        self.assertEqual(a, FP.layout(e, JUDGE))

    def test_two_boot_only_rules(self):
        d = dump_of("fedora44-raid") if (CORPUS / "fedora44-raid" / "src.json").exists() else None
        if d is None or not d["storage"].get("md_arrays"):
            self.skipTest("no md guest in the corpus")
        a = FP.layout(d, JUDGE)
        e = copy.deepcopy(d)
        for arr in e["storage"]["md_arrays"]:
            arr["md"]["array_state"] = "active" if arr["md"].get("array_state") != "active" else "clean"
        self.assertEqual(a, FP.layout(e, JUDGE))

    def test_layout_moves_on_a_structural_edit(self):
        d = dump_of("debian12")
        a = build("debian12", d)
        e = copy.deepcopy(d)
        p = e["storage"]["disks"][0]["partitions"][0]
        p["size_bytes"] = (p.get("size_bytes") or 0) + 1048576
        b = build("debian12", e)
        self.assertNotEqual(a["facts"]["layout"], b["facts"]["layout"])
        self.assertNotEqual(a["holt"]["digest"], b["holt"]["digest"])
        self.assertEqual(a["stic"], b["stic"])

    def test_stic_moves_only_by_a_minor(self):
        ps = pkgset_of("debian12")
        a = build("debian12", pkgset=ps)
        patch = build("debian12", pkgset=ps.replace("e2fsprogs 1.47.0-2", "e2fsprogs 1.47.1-1"))
        self.assertEqual(a["stic"]["digest"], patch["stic"]["digest"])
        self.assertNotEqual(a["stic"]["fine"], patch["stic"]["fine"])
        self.assertNotEqual(a["holt"]["digest"], patch["holt"]["digest"])
        minor = build("debian12", pkgset=ps.replace("e2fsprogs 1.47.0-2", "e2fsprogs 1.48.0-1"))
        self.assertNotEqual(a["stic"]["digest"], minor["stic"]["digest"])

    def test_stic_moves_by_the_kernel_line_zfs_and_lsm(self):
        ps = pkgset_of("debian13-zfs")
        d = dump_of("debian13-zfs")
        a = build("debian13-zfs", d, ps)
        self.assertEqual(a["facts"]["zfs"], "2.3.2-2")
        self.assertEqual(build("debian13-zfs", d, ps.replace("zfsutils-linux 2.3.2-2", "zfsutils-linux 2.3.9-1"))["stic"]["digest"], a["stic"]["digest"])
        self.assertNotEqual(build("debian13-zfs", d, ps.replace("zfsutils-linux 2.3.2-2", "zfsutils-linux 2.4.0-1"))["stic"]["digest"], a["stic"]["digest"])
        e = copy.deepcopy(d)
        e["os"]["kernel_release"] = "6.12.99-amd64"
        kpatch = build("debian13-zfs", e, ps)
        e["os"]["kernel_release"] = "6.13.1-amd64"
        kminor = build("debian13-zfs", e, ps)
        if a["facts"]["kernel_line"] == "6.12":
            self.assertEqual(kpatch["stic"]["digest"], a["stic"]["digest"])
            self.assertNotEqual(kpatch["stic"]["fine"], a["stic"]["fine"])
        self.assertNotEqual(kminor["stic"]["digest"], a["stic"]["digest"])
        ps2 = pkgset_of("debian12")
        b = build("debian12", pkgset=ps2)
        self.assertEqual(b["facts"]["lsm"], {"apparmor": "3.0.8-3"})
        self.assertEqual(build("debian12", pkgset=ps2.replace("apparmor 3.0.8-3", "apparmor 3.0.13-1"))["stic"]["digest"], b["stic"]["digest"])
        self.assertNotEqual(build("debian12", pkgset=ps2.replace("apparmor 3.0.8-3", "apparmor 4.1.0-1"))["stic"]["digest"], b["stic"]["digest"])

    def test_a_package_in_no_digest_moves_none(self):
        ps = pkgset_of("debian12")
        a = build("debian12", pkgset=ps)
        b = build("debian12", pkgset=ps.replace("hello 2.10-3", "hello 2.12-1"))
        self.assertEqual(a["holt"], b["holt"])
        self.assertEqual(a["stic"], b["stic"])

    def moved(self, g, a, b, *names):
        got = {n for n, x, y in (("holt", a["holt"]["digest"], b["holt"]["digest"]),
                                 ("stic", a["stic"]["digest"], b["stic"]["digest"]),
                                 ("fine", a["stic"]["fine"], b["stic"]["fine"])) if x != y}
        self.assertEqual(got, set(names), g)

    def test_a_changed_package_moves_the_right_digest(self):
        # Per OS kind: (guest, old text, new text, which digests move).
        cases = [
            ("debian12", "grub-pc 2.06-13+deb12u2", "grub-pc 2.06-13+deb12u3", ("holt",)),
            ("fedora44", "dracut 0:108-8.fc44.x86_64", "dracut 0:109-1.fc44.x86_64", ("holt",)),
            ("fedora44", "xfsprogs 0:6.17.0-1", "xfsprogs 0:6.18.0-1", ("holt", "stic", "fine")),
            ("arch", "mkinitcpio 42.2-1", "mkinitcpio 42.3-1", ("holt",)),
            ("alpine324", "mkinitfs 3.14.1-r0", "mkinitfs 3.14.2-r0", ("holt",)),
            ("void-musl", "dracut 112_1", "dracut 113_1", ("holt",)),
            ("void-musl", "attr-progs 2.5.2_1", "attr-progs 2.6.0_1", ("stic", "fine")),
            ("aerynos", "systemd-boot 261.4-107", "systemd-boot 261.5-108", ("holt",)),
            ("aerynos", "f2fs-tools 1.17.0-6", "f2fs-tools 1.18.0-1", ("holt", "stic", "fine")),
            ("nixos2605", "nix-pkg grub 2.12", "nix-pkg grub 2.14", ("holt",)),
            ("nixos2605", "bcachefs-tools 1.25.3", "bcachefs-tools 1.26.0", ("holt", "stic", "fine")),
            ("nixos2605", "bcachefs-tools 1.25.3", "bcachefs-tools 1.25.4", ("holt", "fine")),
            ("freebsd15", "base libc 22", "base libc 99", ("holt", "fine")),
            ("freebsd15", "FreeBSD-runtime 15.1p6", "FreeBSD-runtime 15.1p7", ("holt", "fine")),
            ("freebsd15", "base zfs 2.3.4", "base zfs 2.4.0", ("stic", "fine")),
            ("hardenedbsd15", "base libc 44", "base libc 99", ("holt", "fine")),
            ("ghostbsd26", "GhostBSD-runtime 26.2", "GhostBSD-runtime 26.3", ("holt", "fine")),
            ("netbsd11", "base netbsd-daily aaaa", "base netbsd-daily bbbb", ("holt", "fine")),
            ("alpinebsd", "netbsd-base 11.1-r0", "netbsd-base 11.1-r1", ("holt", "fine")),
            ("openbsd8", "syspatch 002_tmux", "syspatch 003_ssh", ("holt", "fine")),
            ("dragonfly6", "bko-base 832c", "bko-base 999c", ("holt", "fine")),
            ("omnios58", "system/kernel 0.5.11-151058.0", "system/kernel 0.5.11-151058.1", ("holt", "fine")),
            ("haiku6", "haiku r1~beta6_hrev59866_92", "haiku r1~beta6_hrev59999_1", ("holt", "fine")),
        ]
        for g, old, new, want in cases:
            ps = pkgset_of(g)
            self.assertIn(old, ps, g)
            self.moved((g, old), build(g, pkgset=ps), build(g, pkgset=ps.replace(old, new)), *want)

    def test_a_changed_kernel_string_moves_the_right_digest(self):
        for g, field in (("debian12", "kernel_release"), ("fedora44", "kernel_release"), ("arch", "kernel_release"),
                         ("alpine324", "kernel_release"), ("void-musl", "kernel_release"), ("aerynos", "kernel_release"),
                         ("nixos2605", "kernel_release"), ("freebsd15", "kernel_version"), ("ghostbsd26", "kernel_version"),
                         ("netbsd11", "kernel_version"), ("alpinebsd", "kernel_version"), ("openbsd8", "kernel_version"),
                         ("dragonfly6", "kernel_version"), ("omnios58", "kernel_version"), ("haiku6", "kernel_version")):
            d = dump_of(g)
            a = build(g, d)
            e = copy.deepcopy(d)
            if field == "kernel_release":              # the same line, another patch
                m = re.match(r"(\d+\.\d+)(.*)", d["os"][field])
                e["os"][field] = m[1] + ".999" + m[2]
            else:
                e["os"][field] = "X" + d["os"][field]
            self.moved(g, a, build(g, e), "holt", "fine")
        d = dump_of("hardenedbsd15")                   # only its kernel's sha256 is its kernel
        ps = pkgset_of("hardenedbsd15")
        self.moved("hbsd", build("hardenedbsd15", d, ps), build("hardenedbsd15", d, ps.replace("base kernel 33", "base kernel 77")), "holt", "fine")
        e = copy.deepcopy(d)
        e["os"]["kernel_version"] = "X"
        self.moved("hbsd", build("hardenedbsd15", d, ps), build("hardenedbsd15", e, ps))

    def test_a_new_release_moves_stic(self):
        for g, field, old, new in (("freebsd15", "kernel_release", "15.1", "15.2"), ("netbsd11", "kernel_release", "11.1", "11.2"),
                                   ("openbsd8", "kernel_release", "8.0", "8.1"), ("dragonfly6", "kernel_release", "6.4", "6.6"),
                                   ("omnios58", "version_id", "r151058", "r151060"), ("haiku6", "kernel_release", "beta6", "beta7")):
            d = dump_of(g)
            e = copy.deepcopy(d)
            self.assertIn(old, d["os"][field], g)
            e["os"][field] = d["os"][field].replace(old, new)
            self.assertNotEqual(build(g, d)["stic"]["digest"], build(g, e)["stic"]["digest"], g)

    def test_illumos_point_release_is_no_new_line(self):
        d = dump_of("omnios58")
        e = copy.deepcopy(d)
        e["os"]["version_id"] = "r151058z"
        self.assertEqual(build("omnios58", d)["stic"]["digest"], build("omnios58", e)["stic"]["digest"])

    def test_unknown_writes_none(self):
        for g in GUESTS:
            for ps in ("unknown\n", "unknown no listing for X\n", pkgset_of(g) + "unknown late\n", "", pkgset_of(g) + "garbage line here\n"):
                with self.assertRaises(FP.NoFingerprint, msg=(g, ps[:20])):
                    build(g, pkgset=ps)

    def test_a_field_with_no_line_behind_it_writes_none(self):
        for g, drop in (("debian12", r"^pkg .*\n"), ("freebsd15", r"^base kernel .*\n"), ("freebsd15", r"^base libc .*\n"),
                        ("hardenedbsd15", r"^base kernel .*\n"), ("netbsd11", r"^base netbsd-daily .*\n"),
                        ("alpinebsd", r"^pkg netbsd-.*\n"), ("openbsd8", r"^base syspatch-count .*\n"),
                        ("dragonfly6", r"^base bko-base .*\n"), ("omnios58", r"^pkg (entire|system/).*\n"),
                        ("haiku6", r"^pkg haiku.*\n"), ("nixos2605", r"^nix-pkg .*\n")):
            ps = re.sub(drop, "", pkgset_of(g), flags=re.M)
            with self.assertRaises(FP.NoFingerprint, msg=(g, drop)):
                build(g, pkgset=ps)
        for g, field in (("debian12", "kernel_release"), ("freebsd15", "kernel_version"), ("freebsd15", "kernel_release")):
            d = dump_of(g)
            d["os"][field] = None
            with self.assertRaises(FP.NoFingerprint, msg=(g, field)):
                build(g, d)

    def test_zfs_pool_without_a_version_writes_none(self):
        with self.assertRaises(FP.NoFingerprint):
            build("debian13-zfs", pkgset="base pm dpkg\npkg e2fsprogs 1.47.2-3\npkg grub-pc 2.12-9\n")
        ps = re.sub(r"^base zfs .*\n", "", pkgset_of("freebsd15"), flags=re.M)
        if FP.uses_zfs(dump_of("freebsd15")):
            with self.assertRaises(FP.NoFingerprint):
                build("freebsd15", pkgset=ps)

    def test_tool_is_no_digest_input(self):
        r = build("debian12")
        self.assertEqual(r["tool"], dump_of("debian12")["tool"]["version"])

    def test_several_versions_of_one_package(self):
        self.assertIn(",", build("fedora44")["facts"]["boot"]["kernel-core"])

    def test_aliases(self):
        f = build("void-musl")["facts"]["fixture"]
        self.assertEqual(f["attr"], "2.5.2_1")
        self.assertEqual(f["acl"], "2.3.2_1")
        self.assertEqual(build("arch")["facts"]["fixture"]["nilfs-tools"], "2.2.11-1")

    def test_minor_and_major(self):
        self.assertEqual(FP.minor("1:1.47.2-3+b1"), "1.47")
        self.assertEqual(FP.minor("v2.3.4"), "2.3")
        self.assertEqual(FP.minor("6.1.0-1,6.2.0-1"), "6.1,6.2")
        self.assertEqual(FP.minor("20261030-1.1"), "20261030")
        self.assertIsNone(FP.minor(None))
        self.assertEqual(FP.major("42.1-1.fc44"), "42")
        self.assertEqual(FP.major("1:4.1.0"), "4")

    def test_packages_are_boot_rs(self):
        text = (REPO / "holt" / "src" / "boot.rs").read_text()
        body = re.search(r"pub const PACKAGES: &\[&str\] = &\[(.*?)\];", text, re.S)[1]
        body = re.sub(r"//[^\n]*", "", body)     # its comments quote names too (Chimera's)
        self.assertEqual(sorted(re.findall(r'"([^"]+)"', body)), sorted(FP.PACKAGES))
        self.assertEqual(len(FP.PACKAGES), len(set(FP.PACKAGES)))
        self.assertLessEqual(FP.STORAGE, set(FP.PACKAGES))

    def test_rules(self):
        a, b = FP.file_sha(TESTVM / "update" / "fingerprint.py"), FP.file_sha(TESTVM / "holttest1d" / "judge.py")
        self.assertEqual(FP.rules_hash(), hashlib.sha256((a + b).encode()).hexdigest())

    def test_judge_is_only_read(self):
        before = FP.file_sha(TESTVM / "holttest1d" / "judge.py")
        build("debian12")
        self.assertEqual(before, FP.file_sha(TESTVM / "holttest1d" / "judge.py"))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as t:
            d, p = os.path.join(t, "d.json"), os.path.join(t, "p")
            shutil.copy(CORPUS / "debian12" / "src.json", d)
            shutil.copy(FIX / "pkgset" / "debian12.txt", p)
            r = subprocess.run([sys.executable, "-B", str(TESTVM / "update" / "fingerprint.py"), d, p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["holt"], build("debian12")["holt"])
            Path(p).write_text("unknown\n")
            r = subprocess.run([sys.executable, "-B", str(TESTVM / "update" / "fingerprint.py"), d, p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 3)
            self.assertIn("no fingerprint", r.stderr)


class TestOpenSuseNames(unittest.TestCase):
    def test_btrfsprogs_is_the_fixture_tool(self):
        self.assertEqual(build("tumbleweed")["facts"]["fixture"]["btrfs-progs"], "0:6.17-1.1.x86_64")

    def test_its_minor_moves_stic_and_its_patch_only_fine(self):
        base = pkgset_of("tumbleweed")
        a = build("tumbleweed")
        minor = build("tumbleweed", pkgset=base.replace("btrfsprogs 0:6.17-1.1", "btrfsprogs 0:6.18-1.1"))
        self.assertNotEqual(a["stic"]["digest"], minor["stic"]["digest"])
        patch = build("tumbleweed", pkgset=base.replace("btrfsprogs 0:6.17-1.1", "btrfsprogs 0:6.17-2.1"))
        self.assertEqual(a["stic"]["digest"], patch["stic"]["digest"])
        self.assertNotEqual(a["stic"]["fine"], patch["stic"]["fine"])


class TestWasAndRules(unittest.TestCase):
    def rec(self, ps=None):
        return build("debian12", pkgset=ps)

    def test_sidecar_shape(self):
        s = FP.sidecar(self.rec(), "g" * 64, "update", "2026-11-10T00:00:00Z")
        self.assertEqual(s["v"], 1)
        self.assertEqual(s["generation"], "g" * 64)
        self.assertEqual(s["source"], "update")
        self.assertEqual(s["holt"]["was"], [])
        self.assertEqual(s["stic"]["was"], [])
        self.assertEqual(sorted(s["stic"]), ["digest", "fine", "was"])
        self.assertEqual(s["rules"], FP.rules_hash())

    def test_was_carries_over_at_an_equal_digest(self):
        s = FP.sidecar(self.rec(), "g", "update", "t")
        s["holt"]["was"] = ["old1"]
        s["stic"]["was"] = ["old2"]
        n = FP.sidecar(self.rec(), "h", "update", "t2", s)
        self.assertEqual(n["holt"]["was"], ["old1"])
        self.assertEqual(n["stic"]["was"], ["old2"])

    def test_was_is_emptied_by_a_new_digest(self):
        s = FP.sidecar(self.rec(), "g", "update", "t")
        s["holt"]["was"] = ["old1"]
        s["stic"]["was"] = ["old2"]
        ps = pkgset_of("debian12").replace("grub-pc 2.06-13+deb12u2", "grub-pc 2.06-14")
        n = FP.sidecar(self.rec(ps), "h", "update", "t2", s)
        self.assertEqual(n["holt"]["was"], [])
        self.assertEqual(n["stic"]["was"], ["old2"])      # no minor moved: its digest is the same

    def test_reproject_unchanged(self):
        d, ps = dump_of("debian12"), pkgset_of("debian12")
        s = FP.sidecar(build("debian12"), "g", "probe", "t")
        s["holt"]["was"] = ["x"]
        n = FP.reproject(s, d, ps, JUDGE)
        self.assertEqual(n["holt"], s["holt"])
        self.assertEqual(n["generation"], "g")
        self.assertEqual(n["source"], "probe")

    def test_reproject_a_moved_digest_goes_in_was(self):
        d, ps = dump_of("debian12"), pkgset_of("debian12")
        s = FP.sidecar(build("debian12"), "g", "probe", "t")
        s["rules"] = "old-rules"
        s["holt"] = {"digest": "e" * 64, "was": ["d" * 64]}
        s["stic"] = {"digest": "f" * 64, "fine": "a" * 64, "was": []}
        n = FP.reproject(s, d, ps, JUDGE)
        self.assertEqual(n["holt"]["was"], ["d" * 64, "e" * 64])
        self.assertEqual(n["stic"]["was"], ["f" * 64])
        self.assertEqual(n["holt"]["digest"], build("debian12")["holt"]["digest"])
        self.assertEqual(n["rules"], FP.rules_hash())


class FakeGuest:
    def __init__(self, name="g1", images=("g1.qcow2",), d=None):
        self.name, self.images, self.dir = name, list(images), Path(d or "/nonexistent")
        self.notes, self.log = [], io.StringIO()
        self.log.name = "update.log"
        self.serial_path = self.dir / "serial.log"

    def note(self, m):
        self.notes.append(m)

    def path(self, img):
        return f"/orig/{img}"

    def ssh_cmd(self, port, *remote):
        return ["true"]


class World(TempDir):
    """VU's globals pointed at a temp tree, on an imaginary first host."""

    def setUp(self):
        super().setUp()
        self.work = self.tmp / "work"
        self.vms = self.work / "vms"
        self.vms.mkdir(parents=True)
        self.root = self.tmp / "state"
        for name, value in (("WORK", self.work), ("VMS", self.vms), ("RAM_BASE", self.tmp / "ram"), ("say", lambda *a: None)):
            p = mock.patch.object(VU, name, value)
            p.start()
            self.addCleanup(p.stop)
        (self.tmp / "ram").mkdir()
        env = mock.patch.dict(os.environ, {"T2_HOST_ROOT": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        for var in ("VM_UPDATE_RAM", "VM_UPDATE_ROUND", "VM_UPDATE_FREE_KIB", "VM_UPDATE_LIMIT_KIB", "T2_WORK", "T2_CELLS", "WEAR_TAG", "T2_RUN"):
            os.environ.pop(var, None)

    def guest_dir(self, name="g1", image="g1.qcow2"):
        d = self.vms / name
        d.mkdir(parents=True, exist_ok=True)
        (d / image).write_text("image")
        return d


class TestSidecarsAndReproject(World):
    def test_a_sidecar_of_another_generation_counts_for_nothing(self):
        self.assertFalse(VU.sidecar_current({"generation": "a"}, "b"))
        self.assertFalse(VU.sidecar_current(None, "b"))
        self.assertFalse(VU.sidecar_current({"generation": "a"}, None))
        self.assertTrue(VU.sidecar_current({"generation": "b"}, "b"))

    def test_reproject_rewrites_on_all_hosts(self):
        g = FakeGuest(d=self.guest_dir())
        d, ps = dump_of("debian12"), pkgset_of("debian12")
        old = FP.sidecar(build("debian12"), "gen1", "update", "t")
        old["holt"]["digest"], old["rules"] = "e" * 64, "old"
        VU.write_json(None, "g1", VU.FP_FILE, old)
        VU.save_dump(g, "gen1", json.dumps(d).encode(), ps)
        msg = VU.reproject(g, [None])
        self.assertIn("holt", msg)
        new = json.loads((self.vms / "g1" / VU.FP_FILE).read_text())
        self.assertEqual(new["holt"]["was"], ["e" * 64])
        self.assertEqual(new["rules"], FP.rules_hash())

    def test_reproject_needs_the_dump(self):
        g = FakeGuest(d=self.guest_dir())
        VU.write_json(None, "g1", VU.FP_FILE, FP.sidecar(build("debian12"), "gen1", "update", "t"))
        with self.assertRaisesRegex(VU.Failed, "no stored dump"):
            VU.reproject(g, [None])

    def test_reproject_without_a_sidecar(self):
        self.assertIn("no fingerprint", VU.reproject(FakeGuest(d=self.guest_dir()), [None]))

    def test_reproject_with_no_fingerprint_by_todays_rules_removes_it(self):
        g = FakeGuest(d=self.guest_dir())
        VU.write_json(None, "g1", VU.FP_FILE, FP.sidecar(build("debian12"), "gen1", "update", "t"))
        VU.save_dump(g, "gen1", json.dumps(dump_of("debian12")).encode(), "unknown\n")
        self.assertIn("generation stands in", VU.reproject(g, [None]))
        self.assertFalse((self.vms / "g1" / VU.FP_FILE).exists())

    def test_dumps_are_private(self):
        g = FakeGuest(d=self.guest_dir())
        VU.save_dump(g, "gen1", b"{}", "base pm dpkg\n")
        d = self.root / "guest-dumps" / "g1"
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(d.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((d / "gen1.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((d / "gen1.pkgset").stat().st_mode), 0o600)


# --- the fleet lock ----------------------------------------------------------------------------

DRIVER = """
import importlib.machinery, importlib.util, os, sys, json
from pathlib import Path
loader = importlib.machinery.SourceFileLoader("vmu", {path!r})
spec = importlib.util.spec_from_loader(loader.name, loader)
VU = importlib.util.module_from_spec(spec)
loader.exec_module(VU)
VU.VMS = Path({vms!r})
VU.on_first_host = lambda: True
VU.HEARTBEAT_TIMEOUT = float(os.environ.get("HEARTBEAT", "30"))
what = sys.argv[1]
if what == "hold":
    sys.exit(VU.hold_fleet_lock(sys.argv[2]))
run = None if what == "ex" else sys.argv[2]
try:
    keep = VU.take_fleet_lock(run)
except VU.Failed as e:
    print("refused:", e, flush=True)
    sys.exit(e.rc)
print("got", flush=True)
sys.stdin.read()
"""


class TestFleetLock(World):
    def setUp(self):
        super().setUp()
        self.procs = []
        self.script = self.tmp / "driver.py"
        self.script.write_text(DRIVER.format(path=str(TESTVM / "vm-update"), vms=str(self.vms)))

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            for s in (p.stdin, p.stdout):
                if s:
                    s.close()

    def start(self, *argv, env=None):
        p = subprocess.Popen([sys.executable, "-B", str(self.script), *argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, env=dict(os.environ, **(env or {})))
        self.procs.append(p)
        return p

    def hold(self, run="t2v-1", env=None):
        p = self.start("hold", run, env=env)
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def job(self, run="t2v-1"):
        p = self.start("sh", run)
        self.assertEqual(p.stdout.readline().strip(), "got")
        return p

    def end(self, p):
        p.stdin.close()
        p.stdin = None
        p.wait(10)

    def refused(self, *argv):
        p = self.start(*argv)
        line = p.stdout.readline()
        self.assertTrue(line.startswith("refused:"), line)
        p.wait(10)
        return line

    def test_by_hand_alone(self):
        self.end(self._ok("ex"))

    def _ok(self, *argv):
        p = self.start(*argv)
        self.assertEqual(p.stdout.readline().strip(), "got")
        return p

    def test_by_hand_refused_under_a_held_shared_lock(self):
        h = self.hold()
        line = self.refused("ex")
        self.assertIn("t2v-1", line)
        self.assertIn("held", line)
        self.end(h)

    def test_the_round_file_names_the_run(self):
        h = self.hold("t2v-77")
        self.assertEqual((self.vms / VU.FLEET_ROUND).read_text().strip(), "t2v-77")
        self.end(h)

    def test_second_round_refused(self):
        h = self.hold()
        p = self.start("hold", "t2v-2")
        self.assertEqual(p.wait(10), 75)
        self.assertEqual((self.vms / VU.FLEET_ROUND).read_text().strip(), "t2v-1")
        self.end(h)

    def test_a_by_hand_driver_keeps_a_round_out(self):
        d = self._ok("ex")
        p = self.start("hold", "t2v-1")
        self.assertEqual(p.wait(10), 75)
        self.end(d)

    def test_two_jobs_share_it(self):
        h = self.hold()
        a, b = self.job(), self.job()
        self.end(a)
        self.end(b)
        self.end(h)

    def test_a_job_with_the_wrong_round_refused(self):
        h = self.hold("t2v-1")
        self.assertIn("t2v-1", self.refused("sh", "t2v-other"))
        self.end(h)

    def test_a_job_with_no_round_refused(self):
        self.assertIn("none", self.refused("sh", "t2v-1"))

    def test_the_holders_death_leaves_the_jobs_lock(self):
        h = self.hold()
        j = self.job()
        h.kill()
        h.wait()
        self.refused("ex")
        p = self.start("hold", "t2v-2")
        self.assertEqual(p.wait(10), 75)
        self.end(j)
        n = self.hold("t2v-2")
        self.end(n)

    def test_heartbeat_keeps_it_and_silence_lets_go(self):
        h = self.hold(env={"HEARTBEAT": "1.5"})
        for _ in range(3):
            time.sleep(0.8)
            h.stdin.write("beat\n")
            h.stdin.flush()
        self.assertIsNone(h.poll())
        self.assertEqual(h.wait(6), 75)
        n = self.hold("t2v-2")
        self.end(n)

    def test_end_of_input_lets_go(self):
        h = self.hold()
        h.stdin.close()
        h.stdin = None
        self.assertEqual(h.wait(10), 0)

    def assert_round_gone(self, run="t2v-1"):
        self.assertFalse((self.vms / VU.FLEET_ROUND).exists(), "the round's name outlived its holder")
        self.assertIn("none", self.refused("sh", run))   # a later job of the round fails its check

    def test_the_round_file_goes_at_end_of_input(self):
        h = self.hold()
        self.end(h)
        self.assertEqual(h.returncode, 0)
        self.assert_round_gone()

    def test_the_round_file_goes_on_silence(self):
        h = self.hold(env={"HEARTBEAT": "0.5"})
        self.assertEqual(h.wait(6), 75)
        self.assert_round_gone()

    def test_the_round_file_goes_on_term_and_hup(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            h = self.hold()
            h.send_signal(sig)
            self.assertEqual(h.wait(10), 75)
            self.assert_round_gone()

    def test_a_running_job_keeps_its_lock_when_the_round_file_goes(self):
        h = self.hold()
        j = self.job()
        self.end(h)
        self.assertFalse((self.vms / VU.FLEET_ROUND).exists())
        self.refused("ex")                          # the job still holds it shared
        self.end(j)
        self.end(self._ok("ex"))

    def test_another_rounds_file_is_left_alone(self):
        VU.clear_round(self.vms / VU.FLEET_ROUND, "t2v-1")   # no file: nothing to do
        (self.vms / VU.FLEET_ROUND).write_text("t2v-2\n")
        VU.clear_round(self.vms / VU.FLEET_ROUND, "t2v-1")
        self.assertEqual((self.vms / VU.FLEET_ROUND).read_text().strip(), "t2v-2")

    def test_a_bad_run_id(self):
        self.assertEqual(VU.hold_fleet_lock("a b"), 2)


# --- RAM -----------------------------------------------------------------------------------------

class TestRam(World):
    def reserve(self, *rows):
        (VU.RAM_BASE / ".reserve").write_text("".join(f"{p} {d} 3 - reserve x\n" for p, d in rows))

    def ismounted(self, yes=True):
        p = mock.patch.object(VU.os.path, "ismount", lambda path: yes)
        p.start()
        self.addCleanup(p.stop)

    def test_unset_is_beside_the_image(self):
        g = FakeGuest(d=self.guest_dir())
        self.assertEqual(VU.overlay_dir(g), g.dir)
        self.assertIsNone(VU.ram_gib())

    def test_bad_values(self):
        for v in ("x", "0", "-1"):
            with mock.patch.dict(os.environ, {"VM_UPDATE_RAM": v}):
                with self.assertRaises(VU.Failed):
                    VU.ram_gib()

    def test_requires_an_ancestors_reservation(self):
        self.ismounted()
        with self.assertRaisesRegex(VU.Failed, "RAM requires an ancestor's reservation"):
            VU.require_reservation(1)
        self.reserve((1, 8))                            # init: an ancestor, never counted
        with self.assertRaisesRegex(VU.Failed, "RAM requires an ancestor's reservation"):
            VU.require_reservation(1)

    def test_a_row_of_someone_elses_is_no_reservation(self):
        self.ismounted()
        other = subprocess.Popen(["sleep", "30"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        self.reserve((other.pid, 8))
        with self.assertRaisesRegex(VU.Failed, "RAM requires an ancestor's reservation"):
            VU.require_reservation(1)

    def test_a_parents_row_will_do(self):
        self.ismounted()
        self.reserve((os.getppid(), 4))
        self.assertEqual(VU.require_reservation(3), os.getppid())

    def test_ones_own_row_will_do(self):
        self.ismounted()
        self.reserve((os.getpid(), 4))
        self.assertEqual(VU.require_reservation(4), os.getpid())

    def test_a_dead_rows_no_reservation(self):
        self.ismounted()
        with mock.patch.object(VU, "pid_alive", lambda p: False):
            self.reserve((os.getpid(), 4))
            with self.assertRaises(VU.Failed):
                VU.require_reservation(1)

    def test_more_than_reserved(self):
        self.ismounted()
        self.reserve((os.getpid(), 2))
        with self.assertRaisesRegex(VU.Failed, "more than"):
            VU.require_reservation(3)

    def test_not_mounted(self):
        self.ismounted(False)
        self.reserve((os.getpid(), 4))
        with self.assertRaisesRegex(VU.Failed, "mounted"):
            VU.require_reservation(1)

    def test_non_default_ram_dir(self):
        tag = hashlib.sha256(os.path.realpath(self.vms).encode()).hexdigest()[:8]
        self.assertEqual(VU.ram_dir_of("g1"), VU.RAM_BASE / f"vm-update.{tag}.g1")

    def test_default_ram_dir(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            (self.tmp / "backotter-work").mkdir()
            with mock.patch.object(VU, "VMS", self.tmp / "backotter-work" / "vms"):
                self.assertEqual(VU.ram_dir_of("g1"), VU.RAM_BASE / "vm-update.g1")

    def test_run_folder_goes_in_the_ram_dir(self):
        with mock.patch.dict(os.environ, {"VM_UPDATE_RAM": "2"}):
            d = VU.overlay_dir(FakeGuest(d=self.guest_dir()))
        self.assertEqual(d, VU.ram_dir_of("g1"))
        self.assertTrue(d.is_dir())

    def test_busy_script_looks_in_both_ram_dirs(self):
        self.assertIn('/mnt/bko-ram/vm-update."$guest"/*.qcow2', VU.BUSY_SCRIPT)
        self.assertIn('/mnt/bko-ram/vm-update.????????."$guest"/*.qcow2', VU.BUSY_SCRIPT)


class TestOverrun(unittest.TestCase):
    GIB = 1048576

    def test_within_it(self):
        self.assertIsNone(VU.overrun_why(2 * self.GIB, 2, 20 * self.GIB, 0))

    def test_past_the_margin_of_one_gib(self):
        self.assertIsNone(VU.overrun_why(3 * self.GIB, 2, 20 * self.GIB, 0))
        self.assertIn("past the limit", VU.overrun_why(3 * self.GIB + 1, 2, 20 * self.GIB, 0))

    def test_past_the_margin_of_ten_percent(self):
        self.assertIsNone(VU.overrun_why(int(22 * self.GIB), 20, 20 * self.GIB, 0))
        self.assertIn("past the limit", VU.overrun_why(int(22 * self.GIB) + 1, 20, 20 * self.GIB, 0))

    def test_over_it_with_little_free(self):
        self.assertIn("under 1 GiB free", VU.overrun_why(2 * self.GIB + 1, 2, 1024, 0))

    def test_within_it_with_little_free_goes_on(self):
        self.assertIsNone(VU.overrun_why(2 * self.GIB, 2, 1024, 0))

    def test_no_live_row_twice(self):
        self.assertIsNone(VU.overrun_why(10, 2, 20 * self.GIB, 1))
        self.assertIn("reservation ended", VU.overrun_why(10, 2, 20 * self.GIB, 2))

    def test_a_test_limit(self):
        self.assertIn("past the limit", VU.overrun_why(11, 2, 20 * self.GIB, 0, limit_kib=10))

    def test_tenths_of_a_gib(self):
        self.assertIsNotNone(VU.overrun_why(int(0.06 * self.GIB), 0.05, 1024, 0))


class TestWatcher(World):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(VU, "WATCH_INTERVAL", 0.02)
        p.start()
        self.addCleanup(p.stop)

    def vm(self, reserved=1):
        v = VU.VM.__new__(VU.VM)
        v.g = FakeGuest()
        v.reserved, v.reserve_pid = reserved, os.getpid()
        v.ram_dir = str(self.tmp / "run")
        os.makedirs(v.ram_dir, exist_ok=True)
        v.peak_kib, v.overrun = 0, None
        v.watch_end = threading.Event()
        v.signalled = []
        v.signal = lambda: v.signalled.append(1)
        return v

    def watch(self, v):
        t = threading.Thread(target=v._watch, daemon=True)
        t.start()
        return t

    def stop(self, v, t):
        v.watch_end.set()
        t.join(5)

    def fill(self, v, kib):
        with open(os.path.join(v.ram_dir, "o.qcow2"), "wb") as f:
            f.write(b"x" * (kib * 1024))

    def live(self):
        (VU.RAM_BASE / ".reserve").write_text(f"{os.getpid()} 1 3 - reserve\n")

    def test_keeps_the_peak_and_leaves_a_run_in_bounds(self):
        v = self.vm()
        self.live()
        self.fill(v, 64)
        with mock.patch.object(VU, "free_kib", lambda: 20 << 20):
            t = self.watch(v)
            time.sleep(0.2)
            self.stop(v, t)
        self.assertGreaterEqual(v.peak_kib, 64)
        self.assertIsNone(v.overrun)
        self.assertEqual(v.signalled, [])

    def test_the_limit_stops_it(self):
        v = self.vm()
        self.live()
        self.fill(v, 64)
        with mock.patch.dict(os.environ, {"VM_UPDATE_LIMIT_KIB": "10"}), mock.patch.object(VU, "free_kib", lambda: 20 << 20):
            t = self.watch(v)
            t.join(5)
        self.assertIn("past the limit", v.overrun)
        self.assertEqual(v.signalled, [1])

    def test_low_free_stops_a_run_over_its_reservation(self):
        v = self.vm(reserved=0.00001)
        self.live()
        self.fill(v, 64)
        with mock.patch.dict(os.environ, {"VM_UPDATE_FREE_KIB": "1024"}):
            t = self.watch(v)
            t.join(5)
        self.assertIn("under 1 GiB free", v.overrun)

    def test_the_end_of_the_reservation_stops_it(self):
        v = self.vm()
        (VU.RAM_BASE / ".reserve").write_text("")
        with mock.patch.object(VU, "free_kib", lambda: 20 << 20):
            t = self.watch(v)
            t.join(5)
        self.assertIn("reservation ended", v.overrun)

    def test_no_reservation_only_measures(self):
        v = self.vm(reserved=None)
        self.fill(v, 64)
        t = self.watch(v)
        time.sleep(0.15)
        self.stop(v, t)
        self.assertGreaterEqual(v.peak_kib, 64)
        self.assertEqual(v.signalled, [])


# --- COMMIT_SCRIPT under a stub qemu-img ---------------------------------------------------------

class TestCommitScript(TempDir):
    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls"
        self.d = self.tmp / "guest"
        self.d.mkdir()
        self.img = self.d / "g.qcow2"
        self.img.write_text("image")
        self.ovl = self.d / ".vm-update.g.qcow2"
        self.ovl.write_text("overlay")
        self.snaps = self.tmp / "snaps"
        self.snaps.write_text("7\n")
        self.mark = self.d / ".vm-update.g.qcow2.merging"
        self.committed = Path(str(self.mark) + ".committed")
        self.stub("qemu-img", f'''
echo "$*" >>{self.calls}
case $1 in
snapshot)
    case $2 in
    -l) echo "Snapshot list:"; echo "ID TAG"; while read -r i; do echo "$i fresh-install 0 B"; done <{self.snaps} ;;
    -c) echo 8 >>{self.snaps} ;;
    -d) sed -i.bak 1d {self.snaps} ;;
    esac ;;
info) echo '{{"format": "qcow2"}}' ;;
esac
''')
        self.stub("sha256sum", '''
f=$2; [ "$1" = -- ] || f=$1
python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest() + "  " + sys.argv[1])' "$f"
''')
        self.stub("sync", f'echo "sync $*" >>{self.calls}')

    def stub(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/bash\n" + body)
        p.chmod(0o755)

    def sum(self, p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def run_script(self, want=None, osum=None):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", BKO_WORK=str(self.tmp / "none"))
        return subprocess.run(["bash", "-c", VU.COMMIT_SCRIPT, "bash", str(self.ovl), str(self.img), "fresh-install",
                               want or self.sum(self.img), self.sum(self.ovl) if osum is None and self.ovl.exists() else (osum or "")],
                              capture_output=True, text=True, env=env)

    def calls_made(self):
        return self.calls.read_text() if self.calls.exists() else ""

    def test_a_whole_merge(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        c = self.calls_made()
        self.assertIn("commit -q -d", c)
        self.assertIn("snapshot -c fresh-install", c)
        self.assertIn("snapshot -d fresh-install", c)
        self.assertFalse(self.ovl.exists())
        self.assertTrue(self.mark.exists())               # for merge_on to remove
        self.assertTrue(self.committed.exists())
        self.assertEqual(r.stdout.strip(), self.sum(self.img))

    def test_mark_and_committed_marker_without_the_overlay_does_the_snapshots(self):
        self.mark.write_text("7\nnot-looked-at -\n")
        self.committed.write_text("")
        self.ovl.unlink()
        r = self.run_script(want="not-looked-at")
        self.assertEqual(r.returncode, 0, r.stderr)
        c = self.calls_made()
        self.assertIn("snapshot -c fresh-install", c)
        self.assertNotIn("commit", c)

    def test_mark_without_overlay_or_marker_is_overlay_lost(self):
        self.mark.write_text("7\n")
        self.ovl.unlink()
        r = self.run_script(want="not-looked-at")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("OVERLAY-LOST", r.stderr)
        self.assertEqual(self.calls_made(), "")           # no snapshot command, nothing at all
        self.assertTrue(self.mark.exists())
        self.assertEqual(self.snaps.read_text(), "7\n")

    def test_a_wrong_overlay_sum_stops_before_the_mark(self):
        r = self.run_script(osum="0" * 64)
        self.assertEqual(r.returncode, 1)
        self.assertIn("isn't the overlay", r.stderr)
        self.assertFalse(self.mark.exists())
        self.assertNotIn("commit", self.calls_made())

    def test_a_changed_image_stops_before_the_mark(self):
        r = self.run_script(want="0" * 64)
        self.assertEqual(r.returncode, 1)
        self.assertIn("changed since", r.stderr)
        self.assertFalse(self.mark.exists())

    def test_no_mark_no_overlay(self):
        self.ovl.unlink()
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertIn("is missing", r.stderr)

    def test_a_cut_short_commit_repeats(self):
        self.mark.write_text("7\n")
        r = self.run_script(want="not-looked-at")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("commit -q -d", self.calls_made())

    def test_after_commit_it_is_not_committed_twice(self):
        self.mark.write_text(f"7\nnot-looked-at {self.sum(self.ovl)}\n")
        self.committed.write_text("")
        r = self.run_script(want="not-looked-at")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("commit -q", self.calls_made())
        self.assertFalse(self.ovl.exists())

    def test_the_mark_names_its_merge_and_is_on_disk_before_the_commit(self):
        want, osum = self.sum(self.img), self.sum(self.ovl)
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.mark.read_text().splitlines(), ["7", f"{want} {osum}"])
        c = self.calls_made().splitlines()
        synced = [i for i, l in enumerate(c) if l.startswith("sync") and l.endswith(f"{self.mark} {self.d}")]
        self.assertTrue(synced, c)
        self.assertLess(synced[0], [i for i, l in enumerate(c) if l.startswith("commit")][0])

    def test_marks_left_by_another_merge_are_refused(self):
        self.mark.write_text("7\nolder-image older-overlay\n")
        self.committed.write_text("")
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertIn("isn't this merge's", r.stderr)
        self.assertEqual(self.calls_made(), "")           # no commit, no snapshot
        self.assertTrue(self.ovl.exists())

    def test_a_mark_of_another_merge_is_refused_with_no_marker_too(self):
        self.mark.write_text("7\nolder-image older-overlay\n")
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("commit", self.calls_made())

    def test_a_mark_naming_no_merge_beside_a_marker_is_refused(self):
        self.mark.write_text("7\n")
        self.committed.write_text("")
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertIn("isn't this merge's", r.stderr)
        self.assertTrue(self.ovl.exists())

    def test_a_marker_with_no_mark_is_no_merges(self):
        self.committed.write_text("")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("commit -q -d", self.calls_made())
        self.assertFalse(self.ovl.exists())

    def test_the_same_merge_resumes_by_its_stamp(self):
        self.mark.write_text(f"7\n{self.sum(self.img)} {self.sum(self.ovl)}\n")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("commit -q -d", self.calls_made())

    def test_no_mark_no_overlay_says_what_to_do(self):
        self.ovl.unlink()
        r = self.run_script()
        self.assertIn(".vm-update.pending", r.stderr)
        self.assertIn("image-state", r.stderr)

    def test_an_image_without_a_snapshot(self):
        self.snaps.write_text("")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("snapshot -c", self.calls_made())


# --- hooks, ssh, the gate ------------------------------------------------------------------------

class TestHooks(TempDir):
    def test_status(self):
        self.assertEqual(VU.hook_status(0, "=== file x\nhello\n=== end\n"), {"rc": 0, "sentinel": True})

    def test_empty_output(self):
        self.assertEqual(VU.hook_status(0, ""), {"rc": 0, "sentinel": False})

    def test_cut_off_output(self):
        self.assertFalse(VU.hook_status(0, "=== file x\nhello\n")["sentinel"])
        self.assertFalse(VU.hook_status(0, "=== end\nthen more\n")["sentinel"])

    def test_problems(self):
        self.assertEqual(VU.hook_problems({"a": {"rc": 0, "sentinel": True}}), [])
        self.assertEqual(VU.hook_problems({"a": {"rc": 1, "sentinel": True}, "b": {"rc": 0, "sentinel": False}}), ["a", "b"])

    def test_names(self):
        a = types.SimpleNamespace(hook=["before=/x/before", "before=/x/new", "after=/x/after", "after=/y/new"])
        h = VU.hooks_of(a)
        self.assertEqual([n for n, _ in h["before"]], ["before", "new"])
        self.assertEqual([n for n, _ in h["after"]], ["after", "new-2"])

    def test_job_shs_hooks_are_named_as_the_row_reads_them(self):
        sys.path.insert(0, str(T2))
        self.addCleanup(sys.path.remove, str(T2))
        from t2lib import rows_guest
        specs = re.findall(r'--hook "((?:before|after)=\$H/\w+)"', (T2 / "guest" / "job.sh").read_text())
        h = VU.hooks_of(types.SimpleNamespace(hook=specs))
        names = {n for phase in h.values() for n, _ in phase}
        self.assertEqual(names, set(rows_guest.HOOK_FILE.values()))

    def test_run_hooks_records(self):
        f = self.tmp / "h"
        f.write_text("echo hi")
        vm = types.SimpleNamespace(g=FakeGuest(), ssh_checked=lambda s, t: subprocess.CompletedProcess([], 0, "hi\n", ""))
        res = VU.new_result("g1", "try")
        VU.run_hooks(vm, "before", [("before", f)], res, self.tmp)
        self.assertEqual((self.tmp / "hook-before.out").read_text(), "hi\n")
        self.assertEqual(res["hooks"]["before"], {"rc": 0, "sentinel": False})


class TestSshChecked(unittest.TestCase):
    def vm(self, *rcs):
        v = VU.VM.__new__(VU.VM)
        v.g = FakeGuest()
        seq = list(rcs)
        v.calls = []

        def ssh(script, timeout, log=True):
            v.calls.append(script)
            return subprocess.CompletedProcess([], seq.pop(0), "out\n", "")
        v.ssh = ssh
        return v

    def setUp(self):
        p = mock.patch.object(VU.stopping, "wait", lambda t: False)
        p.start()
        self.addCleanup(p.stop)

    def test_255_is_retried_once(self):
        v = self.vm(255, 0)
        self.assertEqual(v.ssh_checked("x", 5).returncode, 0)
        self.assertEqual(len(v.calls), 2)

    def test_255_twice_fails_the_run(self):
        with self.assertRaisesRegex(VU.Failed, "failed twice"):
            self.vm(255, 255).ssh_checked("x", 5)

    def test_another_status_is_the_callers(self):
        v = self.vm(3)
        self.assertEqual(v.ssh_checked("x", 5).returncode, 3)
        self.assertEqual(len(v.calls), 1)

    def test_a_listing_that_ssh_lost_is_never_unknown(self):
        with self.assertRaises(VU.Failed):
            self.vm(255, 255).listing()

    def test_a_listing_that_failed_is_unknown(self):
        self.assertTrue(self.vm(1).listing().startswith("unknown"))

    def test_an_empty_listing_is_unknown(self):
        v = self.vm(0)
        v.ssh_checked = lambda s, t: subprocess.CompletedProcess([], 0, "", "")
        self.assertTrue(v.listing().startswith("unknown"))

    def test_a_listings_error_output_and_why_it_is_unknown_are_logged(self):
        # T2.1: aerynos's listing was unknown, and nothing kept moss's own words.
        v = self.vm(0)
        said = "unknown moss pkg list --installed failed (exit status 2)\n"
        v.ssh_checked = lambda s, t: subprocess.CompletedProcess([], 0, said, "error: unrecognized subcommand 'list'\n")
        self.assertEqual(v.listing(), said)
        self.assertEqual(v.g.log.getvalue(), "pkgset.sh's error output:\nerror: unrecognized subcommand 'list'\n")
        self.assertEqual(v.g.notes, ["package listing: " + said.strip()])

    def test_a_listing_that_failed_says_why_in_the_log(self):
        v = self.vm(1)
        self.assertEqual(v.listing(), "unknown pkgset.sh: exit status 1\n")
        self.assertEqual(v.g.notes, ["package listing: unknown pkgset.sh: exit status 1"])

    def test_a_whole_listing_logs_nothing(self):
        v = self.vm(0)
        v.ssh_checked = lambda s, t: subprocess.CompletedProcess([], 0, A, "")
        self.assertEqual(v.listing(), A)
        self.assertEqual((v.g.log.getvalue(), v.g.notes), ("", []))

    def test_stop_during_the_wait(self):
        with mock.patch.object(VU.stopping, "wait", lambda t: True):
            with self.assertRaisesRegex(VU.Failed, "stopped"):
                self.vm(255, 0).ssh_checked("x", 5)


class TestGate(unittest.TestCase):
    def gate(self, cmd):
        m = VU.merge_decision(A, A + "pkg new 1 amd64\n", [])
        return m

    def test_0_merges(self):
        m = self.gate("")
        VU.run_gate("exit 0", m, FakeGuest())
        self.assertTrue(m["decided"])
        self.assertEqual(m["gate"], 0)

    def test_10_blocks(self):
        m = self.gate("")
        with self.assertRaises(VU.Blocked) as c:
            VU.run_gate("echo 'after:boot-state new: failed.service'; exit 10", m, FakeGuest())
        self.assertEqual(c.exception.rc, 10)
        self.assertFalse(m["decided"])
        self.assertEqual(m["gate"], 10)
        self.assertIn("failed.service", m["why"])

    def test_anything_else_fails_without_a_merge(self):
        m = self.gate("")
        with self.assertRaises(VU.Failed) as c:
            VU.run_gate("exit 3", m, FakeGuest())
        self.assertNotIsInstance(c.exception, VU.Blocked)
        self.assertFalse(m["decided"])
        self.assertEqual(m["gate"], 3)


    def test_a_stop_is_no_gate_verdict(self):
        m = self.gate("")
        VU.stopping.set()
        self.addCleanup(VU.stopping.clear)
        with self.assertRaisesRegex(VU.Failed, "^stopped$"):
            VU.run_gate("exit 3", m, FakeGuest())
        self.assertIsNone(m["gate"])


# --- who may merge -------------------------------------------------------------------------------

class TestRefusals(TempDir):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.real = self.home / "backotter-work"
        (self.real / "testvm").mkdir(parents=True)
        (self.real / "vms").mkdir()
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        for v in ("T2_HOST_ROOT", "T2_WORK"):
            os.environ.pop(v, None)

    def at(self, work, testvm):
        return mock.patch.multiple(VU, WORK=work, VMS=work / "vms", TESTVM=testvm)

    def test_the_real_tree_as_it_is(self):
        with self.at(self.real, self.real / "testvm"):
            VU.refuse_real_update()

    def test_the_default_root_named_is_no_refusal(self):
        with self.at(self.real, self.real / "testvm"), mock.patch.dict(os.environ, {"T2_HOST_ROOT": "/bkoscratch/test2"}):
            VU.refuse_real_update()

    def test_a_non_default_root_with_the_real_tree(self):
        with self.at(self.real, self.real / "testvm"), mock.patch.dict(os.environ, {"T2_HOST_ROOT": str(self.tmp / "s")}):
            with self.assertRaisesRegex(VU.Failed, "T2_HOST_ROOT"):
                VU.refuse_real_update()
            with self.assertRaisesRegex(VU.Failed, "T2_HOST_ROOT"):
                VU.refuse_on_real_tree()               # --finish's check

    def test_a_non_default_work_with_the_real_tree(self):
        with self.at(self.real, self.real / "testvm"), mock.patch.dict(os.environ, {"T2_WORK": str(self.tmp / "w")}):
            with self.assertRaisesRegex(VU.Failed, "T2_WORK"):
                VU.refuse_real_update()

    def test_t2_work_naming_the_real_tree_is_no_refusal(self):
        with self.at(self.real, self.real / "testvm"), mock.patch.dict(os.environ, {"T2_WORK": str(self.real)}):
            VU.refuse_real_update()

    def test_a_world_may(self):
        w = self.tmp / "world"
        (w / "testvm").mkdir(parents=True)
        (w / "vms").mkdir()
        with self.at(w, w / "testvm"), mock.patch.dict(os.environ, {"T2_HOST_ROOT": str(self.tmp / "s"), "T2_WORK": str(w)}):
            VU.refuse_real_update()

    def test_a_private_copy_is_refused(self):
        copy_ = self.tmp / "copy"
        (copy_ / "testvm").mkdir(parents=True)
        with self.at(self.real, copy_ / "testvm"):
            with self.assertRaisesRegex(VU.Failed, "synced tree"):
                VU.refuse_real_update()

    def test_a_copy_with_no_vms_is_refused(self):
        w = self.tmp / "copy"
        (w / "testvm").mkdir(parents=True)
        with self.at(w, w / "testvm"):
            with self.assertRaisesRegex(VU.Failed, "no .*vms"):
                VU.refuse_real_update()

    def world(self):
        w = self.tmp / "world"
        (w / "testvm").mkdir(parents=True)
        (w / "vms" / "g1").mkdir(parents=True)
        return w

    def test_a_world_that_doesnt_say_where_it_is_elsewhere(self):
        w = self.world()
        os.environ.pop("VM_UPDATE_REMOTE_VMS", None)
        with self.at(w, w / "testvm"):
            with self.assertRaisesRegex(VU.Failed, "VM_UPDATE_REMOTE_VMS"):
                VU.refuse_unaimed_world(["cinqcent.local"], False)
            with self.assertRaisesRegex(VU.Failed, "VM_UPDATE_REMOTE_VMS"):
                VU.refuse_unaimed_world([], True)              # the fleet lock is over there
            VU.refuse_unaimed_world([], False)                 # --hosts '' on the first host
            with mock.patch.dict(os.environ, {"VM_UPDATE_REMOTE_VMS": "backotter-work/t/world/vms"}):
                VU.refuse_unaimed_world(["cinqcent.local"], True)

    def test_the_real_tree_needs_no_remote_path(self):
        os.environ.pop("VM_UPDATE_REMOTE_VMS", None)
        with self.at(self.real, self.real / "testvm"):
            VU.refuse_unaimed_world(["cinqcent.local"], True)

    def main(self, *argv):
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["vm-update", *argv]), contextlib.redirect_stderr(err):
            return VU.main(), err.getvalue()

    def test_sidecar_writers_are_refused_in_an_unaimed_world(self):
        w = self.world()
        os.environ.pop("VM_UPDATE_REMOTE_VMS", None)
        with self.at(w, w / "testvm"):
            for argv in (["--reproject", "g1"], ["--probe", "--write-fingerprint", "g1"], ["--status", "g1"], ["--finish", "g1"], ["g1"]):
                rc, err = self.main("--hosts", "elsewhere.local", *argv)
                self.assertEqual(rc, 1, argv)
                self.assertIn("VM_UPDATE_REMOTE_VMS", err, argv)

    def test_sidecar_writers_are_refused_on_the_real_tree_in_a_test(self):
        (self.real / "vms" / "g1").mkdir()
        with self.at(self.real, self.real / "testvm"), mock.patch.dict(os.environ, {"T2_HOST_ROOT": str(self.tmp / "s")}):
            for argv in (["--reproject", "g1"], ["--probe", "--write-fingerprint", "g1"]):
                rc, err = self.main("--hosts", "", *argv)
                self.assertEqual(rc, 1, argv)
                self.assertIn("T2_HOST_ROOT", err, argv)

    def test_a_refused_probe_still_writes_its_result(self):
        (self.real / "vms" / "g1").mkdir()
        out = self.tmp / "out"
        with self.at(self.real, self.real / "testvm"), mock.patch.dict(os.environ, {"T2_HOST_ROOT": str(self.tmp / "s")}):
            rc, err = self.main("--hosts", "", "--probe", "--write-fingerprint", "--out", str(out), "g1")
        res = json.loads((out / "result.json").read_text())
        self.assertEqual((res["mode"], res["rc"]), ("probe", 1))

    def test_finish_with_no_vms_is_a_refusal(self):
        w = self.tmp / "copy"
        (w / "testvm").mkdir(parents=True)
        os.environ.pop("VM_UPDATE_REMOTE_VMS", None)
        with self.at(w, w / "testvm"):
            rc, err = self.main("--hosts", "", "--finish", "g1") if VU.on_first_host() else (1, "no vms")
        self.assertEqual(rc, 1)
        self.assertIn("vms", err)
        with self.at(w, w / "testvm"), self.assertRaisesRegex(VU.Failed, "no .*vms"):
            VU.refuse_no_vms()

    def test_the_real_tree_by_another_mount_is_the_real_tree(self):
        other = self.tmp / "bkoscratch-work"
        other.mkdir()
        with mock.patch.object(VU.os.path, "samefile", lambda a, b: True):
            self.assertTrue(VU.real_tree(other))
        self.assertFalse(VU.real_tree(other))

    def test_same_path_of_something_missing(self):
        self.assertFalse(VU.same_path(self.tmp / "no", self.tmp / "nor"))
        self.assertTrue(VU.same_path(self.tmp / "no", self.tmp / "no"))

    def test_a_link_for_vms_is_refused(self):
        w = self.tmp / "world"
        (w / "testvm").mkdir(parents=True)
        (w / "vms").symlink_to(self.real / "vms")
        with self.at(w, w / "testvm"):
            with self.assertRaisesRegex(VU.Failed, "link"):
                VU.refuse_real_update()


class TestArguments(unittest.TestCase):
    def main(self, *argv):
        with mock.patch.object(sys, "argv", ["vm-update", *argv]):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    rc = VU.main()
                except SystemExit as e:
                    rc = e.code
        return rc, out.getvalue() + err.getvalue()

    def test_help_names_every_option(self):
        rc, text = self.main("--help")
        self.assertEqual(rc, 0)
        for opt in ("--try", "--probe", "--hosts", "--jobs", "--cpus", "--memory", "--out", "--hook", "--push", "--write-fingerprint",
                    "--merge-gate", "--keep-failed", "--status", "--finish", "--reproject", "--hold-fleet-lock", "VM_UPDATE_RAM",
                    "VM_UPDATE_ROUND", "BKO_WORK", "VM_UPDATE_REMOTE_VMS", "VM_UPDATE_HOLT_BIN"):
            self.assertIn(opt, text, opt)

    def refused(self, *argv):
        rc, text = self.main(*argv)
        self.assertEqual(rc, 2, text)
        return text

    def test_modes_dont_combine(self):
        self.assertIn("don't combine", self.refused("--try", "--probe", "g"))

    def test_keep_failed_not_on_a_real_update(self):
        self.assertIn("--keep-failed", self.refused("--keep-failed", "g"))

    def test_merge_gate_only_real(self):
        self.assertIn("--merge-gate", self.refused("--try", "--merge-gate", "x", "g"))

    def test_try_writes_no_fingerprint(self):
        self.assertIn("--write-fingerprint", self.refused("--try", "--write-fingerprint", "g"))

    def test_a_hook_needs_out_and_a_file(self):
        self.assertIn("--hook", self.refused("--try", "--hook", "before=/nonexistent", "--out", "/tmp/x", "g"))
        self.assertIn("--out", self.refused("--try", "--hook", f"before={__file__}", "g"))
        self.assertIn("--hook", self.refused("--try", "--hook", f"during={__file__}", "--out", "/tmp/x", "g"))

    def test_hold_takes_no_guests(self):
        self.assertIn("no guests", self.refused("--hold-fleet-lock", "t2v-1", "g"))

    def test_no_guests(self):
        self.assertIn("no guests", self.refused("--try"))

    def test_a_reservation_is_one_guests(self):
        with mock.patch.dict(os.environ, {"VM_UPDATE_RAM": "4"}):
            self.assertIn("VM_UPDATE_RAM", self.refused("--try", "a", "b"))
            self.assertIn("VM_UPDATE_RAM", self.refused("--probe", "a", "b"))


# --- the flow, with a stand-in VM ----------------------------------------------------------------

BEFORE = "base pm dpkg\npkg a 1 amd64\n"
AFTER = "base pm dpkg\npkg a 2 amd64\n"


class FakeVM:
    last = None
    in_guest = "in-guest: updated\n"
    listings = (BEFORE, AFTER)
    hook_output = "=== end\n"
    fail_in = None

    def __init__(self, g, memory, cpus):
        FakeVM.last = self
        self.g, self.port, self.ram_dir, self.reserved = g, 2222, str(g.dir / ".vm-update-run.x"), None
        self.peak_kib, self.overrun = 123, None
        os.makedirs(self.ram_dir, exist_ok=True)
        self.calls, self.discarded, self.kept = [], False, False
        self.queue = list(self.listings)

    def wait_ssh(self, rebooting=False):
        self.calls.append("reboot-up" if rebooting else "up")

    def ssh_checked(self, script, timeout, log=False):
        if script.startswith("uname"):
            self.calls.append("uname")
            return subprocess.CompletedProcess([], 0, "Linux 6.1\n", "")
        self.calls.append("hook")
        return subprocess.CompletedProcess([], 0, self.hook_output, "")

    def ssh(self, script, timeout, log=True):
        if "t2-section: prologue" in script:
            self.calls.append("in-guest")
            if self.fail_in:
                return subprocess.CompletedProcess([], self.fail_in, "E: Failed to fetch x\n", "")
            return subprocess.CompletedProcess([], 0, self.in_guest, "")
        if "fstrim" in script:
            self.calls.append("trim")
            return subprocess.CompletedProcess([], 0, "", "")
        self.calls.append("ssh")
        return subprocess.CompletedProcess([], 0, "", "")

    def uptime(self):
        return None

    def type_passphrase(self):
        pass

    def listing(self):
        self.calls.append("pkgset")
        return self.queue.pop(0)

    def put(self, local, remote):
        self.calls.append(f"put {remote}")
        return 0

    def dump(self, uname):
        self.calls.append("dump")
        return json.dumps(dump_of("debian12")).encode(), ""

    def poweroff(self):
        self.calls.append("poweroff")
        return {self.g.path(self.g.images[0]): str(Path(self.ram_dir) / "o")}

    def discard(self, keep=False):
        self.discarded, self.kept = True, keep


class TestFlow(World):
    def setUp(self):
        super().setUp()
        self.d = self.guest_dir()
        self.state = {"generation": "a" * 64, "updates": 3, "sha256": {"g1.qcow2": "b" * 64}}
        (self.d / "image-state").write_text(json.dumps(self.state))
        FakeVM.in_guest, FakeVM.listings, FakeVM.hook_output, FakeVM.fail_in = "in-guest: updated\n", (BEFORE, AFTER), "=== end\n", None
        self.shipped = []
        self.out = self.tmp / "out"
        ps = (FIX / "pkgset" / "debian12.txt").read_text()
        FakeVM.listing_after = ps
        p = [mock.patch.object(VU, "VM", FakeVM),
             mock.patch.object(VU, "check_hosts", lambda g, h, start, exclude=None: {None: self.state}),
             mock.patch.object(VU, "ship_and_commit", self.ship),
             mock.patch.object(VU, "by_hand_row", lambda *a: None),
             mock.patch.object(VU, "fingerprint_mod", lambda: self.fp()),
             mock.patch.object(VU.os.path, "getsize", lambda p: 5 << 20)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def fp(self):
        """fingerprint.py, its build taking any listing as debian12's."""
        m = types.SimpleNamespace(**{k: getattr(FP, k) for k in ("NoFingerprint", "sidecar")})
        m.build = lambda dump, listing: build("debian12")
        return m

    def ship(self, g, hosts, overlays, states, rec, dump, listing):
        self.shipped.append(rec)
        FakeVM.last.calls.append("ship")
        return "c" * 64

    def args(self, **kw):
        base = dict(try_=False, probe=False, status=False, finish=False, reproject=False, out=str(self.out), hook=None, push=None,
                    write_fingerprint=False, merge_gate=None, keep_failed=False, memory=None, cpus=None, guests=["g1"])
        base.update(kw)
        return types.SimpleNamespace(**base)

    def hook_files(self, **names):
        out = []
        for phase, name in names.items():
            f = self.tmp / name
            f.write_text("echo x\n")
            out.append(f"{phase}={f}")
        return out

    def run_update(self, **kw):
        msg = VU.update("g1", self.args(**kw), [None])
        return msg, json.loads((self.out / "result.json").read_text())

    def fails(self, exc, **kw):
        with self.assertRaises(exc) as c:
            VU.update("g1", self.args(**kw), [None])
        return c.exception, json.loads((self.out / "result.json").read_text())

    def calls(self):
        return FakeVM.last.calls

    def test_real_order(self):
        msg, res = self.run_update(hook=self.hook_files(before="before", after="after"), merge_gate="exit 0")
        c = self.calls()
        self.assertEqual([x for x in c if x not in ("uname", "ssh", "reboot-up", "up")],
                         ["hook", "pkgset", "in-guest", "hook", "pkgset", "dump", "trim", "poweroff", "ship"])
        self.assertEqual(res["phase"], "done")
        self.assertEqual(res["rc"], 0)
        self.assertTrue(res["merged"])
        self.assertEqual(res["generation_before"], "a" * 64)
        self.assertEqual(res["generation_after"], "c" * 64)
        self.assertEqual(res["merge"]["gate"], 0)
        self.assertTrue(res["merge"]["decided"])
        self.assertEqual(res["trim_rc"], 0)
        self.assertIsNotNone(res["fingerprint"])
        self.assertIsNotNone(self.shipped[0])
        self.assertTrue(FakeVM.last.discarded)
        for key in ("guest", "mode", "uname", "overlay_mib", "overlay_dir", "ram_peak_kib", "reserved_gib", "overrun", "wall_s", "tail", "hooks"):
            self.assertIn(key, res)

    def test_nothing_changed(self):
        FakeVM.listings = (BEFORE, BEFORE)
        msg, res = self.run_update()
        self.assertIn("nothing changed; no merge", msg)
        c = self.calls()
        self.assertNotIn("trim", c)
        self.assertNotIn("ship", c)
        self.assertFalse(res["merged"])
        self.assertEqual(res["generation_after"], "a" * 64)
        self.assertIsNone(res["trim_rc"])
        side = json.loads((self.d / VU.FP_FILE).read_text())
        self.assertEqual(side["source"], "update-nochange")
        self.assertEqual(side["generation"], "a" * 64)

    def test_nothing_changed_with_a_current_fingerprint_dumps_nothing(self):
        FakeVM.listings = (BEFORE, BEFORE)
        (self.d / VU.FP_FILE).write_text(json.dumps({"generation": "a" * 64}))
        self.run_update()
        self.assertNotIn("dump", self.calls())

    def test_nothing_changed_with_an_old_generations_fingerprint(self):
        FakeVM.listings = (BEFORE, BEFORE)
        (self.d / VU.FP_FILE).write_text(json.dumps({"generation": "0" * 64}))
        self.run_update()
        self.assertIn("dump", self.calls())

    def test_a_step_alone_merges(self):
        FakeVM.listings = (BEFORE, BEFORE)
        FakeVM.in_guest = "in-guest: step: rc.conf: IPv4 first\nin-guest: updated\n"
        msg, res = self.run_update()
        self.assertTrue(res["merged"])
        self.assertEqual(res["merge"]["steps"], ["rc.conf: IPv4 first"])

    def test_unknown_merges(self):
        FakeVM.listings = ("unknown x\n", "unknown x\n")
        msg, res = self.run_update()
        self.assertTrue(res["merged"])
        self.assertTrue(res["merge"]["unknown"])

    def test_gate_blocks(self):
        e, res = self.fails(VU.Blocked, merge_gate="echo new line; exit 10")
        self.assertEqual(res["rc"], 10)
        self.assertFalse(res["merged"])
        self.assertEqual(res["merge"]["gate"], 10)
        self.assertIn("new line", res["merge"]["why"])
        self.assertNotIn("trim", self.calls())
        self.assertNotIn("ship", self.calls())
        self.assertEqual(res["generation_after"], "a" * 64)

    def test_the_gate_reads_the_hooks_results(self):
        seen = {}

        def gate(cmd, m, g):
            seen.update(json.loads((self.out / "result.json").read_text()))
        with mock.patch.object(VU, "run_gate", gate):
            self.run_update(hook=self.hook_files(before="before"), merge_gate="x")
        self.assertEqual(seen["phase"], "gate")
        self.assertEqual(seen["hooks"]["before"], {"rc": 0, "sentinel": True})

    def test_a_failed_hook_means_no_merge(self):
        FakeVM.hook_output = "cut off\n"
        e, res = self.fails(VU.Blocked, hook=self.hook_files(before="before"), merge_gate="exit 0")
        self.assertIn("before", res["merge"]["why"])
        self.assertFalse(res["merged"])
        self.assertIsNone(res["merge"]["gate"])          # the gate had no say
        self.assertNotIn("ship", self.calls())

    def test_try(self):
        msg, res = self.run_update(try_=True)
        c = self.calls()
        self.assertNotIn("trim", c)
        self.assertNotIn("dump", c)
        self.assertNotIn("ship", c)
        self.assertEqual(res["mode"], "try")
        self.assertEqual(res["phase"], "done")
        self.assertIn("would merge", msg)
        self.assertTrue(res["merge"]["decided"])
        self.assertFalse(res["merged"])
        self.assertEqual(res["generation_after"], "a" * 64)
        self.assertEqual(res["overlay_mib"], 5)

    def test_try_nothing(self):
        FakeVM.listings = (BEFORE, BEFORE)
        msg, res = self.run_update(try_=True)
        self.assertIn("nothing to merge", msg)
        self.assertFalse(res["merge"]["decided"])

    def test_try_failure_keeps_its_folder(self):
        FakeVM.fail_in = 1
        e, res = self.fails(VU.Failed, try_=True, keep_failed=True)
        self.assertTrue(FakeVM.last.kept)
        self.assertTrue(res["kept_failed"])
        self.assertTrue(res["overlay_dir"])
        self.assertEqual(res["rc"], 1)
        self.assertEqual(res["phase"], "update")
        self.assertIn("E: Failed to fetch x", res["tail"])
        self.assertLessEqual(len(res["tail"]), 8)

    def test_try_failure_without_keep(self):
        FakeVM.fail_in = 1
        e, res = self.fails(VU.Failed, try_=True)
        self.assertFalse(FakeVM.last.kept)
        self.assertFalse(res["kept_failed"])

    def test_a_stop_during_the_update_is_a_stop(self):
        class Stopped(FakeVM):
            def ssh(self, script, timeout, log=True):
                if "t2-section: prologue" in script:       # the stop kills the VM, and so this ssh
                    VU.stopping.set()
                    return subprocess.CompletedProcess([], 255, "apt says something\n", "")
                return FakeVM.ssh(self, script, timeout, log)
        self.addCleanup(VU.stopping.clear)
        with mock.patch.object(VU, "VM", Stopped):
            e, res = self.fails(VU.Failed, try_=True, keep_failed=True)
        self.assertEqual(res["tail"], ["stopped"])
        self.assertEqual(res["phase"], "update")
        self.assertFalse(FakeVM.last.kept)
        self.assertFalse(res["kept_failed"])

    def test_a_255_with_no_stop_is_the_updates_failure(self):
        FakeVM.fail_in = 255
        e, res = self.fails(VU.Failed, try_=True)
        self.assertIn("E: Failed to fetch x", res["tail"])

    def test_a_mistyped_guest_makes_no_folder(self):
        with self.assertRaisesRegex(VU.Failed, "no such guest"):
            VU.update("typo", self.args(out=None, try_=True), [None])
        self.assertFalse((VU.VMS / "typo").exists())

    def test_nothing_updates_it(self):
        FakeVM.fail_in = 3
        msg, res = self.run_update()
        self.assertIn("nothing updates", msg)
        self.assertEqual(res["rc"], 0)

    def test_probe_does_no_update(self):
        msg, res = self.run_update(probe=True, hook=self.hook_files(before="before"))
        c = self.calls()
        self.assertNotIn("in-guest", c)
        self.assertNotIn("reboot-up", c)
        self.assertNotIn("dump", c)
        self.assertIn("poweroff", c)
        self.assertEqual(res["mode"], "probe")
        self.assertEqual(res["phase"], "done")
        self.assertFalse((self.d / VU.FP_FILE).exists())

    def test_probe_writes_the_fingerprint(self):
        msg, res = self.run_update(probe=True, write_fingerprint=True)
        self.assertEqual([x for x in self.calls() if x in ("pkgset", "dump")], ["pkgset", "dump"])
        side = json.loads((self.d / VU.FP_FILE).read_text())
        self.assertEqual(side["source"], "probe")
        self.assertEqual(side["generation"], "a" * 64)
        self.assertTrue((self.root / "guest-dumps" / "g1" / ("a" * 64 + ".json")).exists())

    def test_probe_needs_an_image_state(self):
        (self.d / "image-state").unlink()
        e, res = self.fails(VU.Failed, probe=True, write_fingerprint=True)
        self.assertIn("no generation", str(e))

    def test_no_fingerprint_is_no_failure(self):
        def nope(dump, listing):
            raise FP.NoFingerprint("the listing is unknown")
        m = types.SimpleNamespace(NoFingerprint=FP.NoFingerprint, sidecar=FP.sidecar, build=nope)
        with mock.patch.object(VU, "fingerprint_mod", lambda: m):
            msg, res = self.run_update()
        self.assertTrue(res["merged"])
        self.assertIsNone(res["fingerprint"])
        self.assertIn("unknown", res["fingerprint_why"])
        self.assertIsNone(self.shipped[0])

    def test_push_comes_before_the_hooks(self):
        f = self.tmp / "bin"
        f.write_text("x")
        self.run_update(try_=True, push=[f"{f}=/tmp/x"], hook=self.hook_files(before="before"))
        c = self.calls()
        self.assertLess(c.index("put /tmp/x"), c.index("hook"))

    def test_the_listings_and_steps_are_kept(self):
        FakeVM.in_guest = "in-guest: step: one\nin-guest: updated\n"
        self.run_update(try_=True)
        self.assertEqual((self.out / "pkgset-before.txt").read_text(), BEFORE)
        self.assertEqual((self.out / "pkgset-after.txt").read_text(), AFTER)
        self.assertEqual((self.out / "steps.txt").read_text(), "one\n")

    def test_out_gets_the_log_and_the_shared_log_a_pointer(self):
        self.run_update(try_=True)
        self.assertIn("vm-update --try", (self.out / "update.log").read_text())
        shared = (self.d / "update.log").read_text().splitlines()
        self.assertEqual(len(shared), 1)
        self.assertIn(str(self.out), shared[0])

    def test_without_out_result_is_in_the_guest(self):
        VU.update("g1", self.args(out=None, try_=True), [None])
        self.assertTrue((self.d / ".vm-update-last" / "result.json").exists())

    def test_several_guests_out_per_guest(self):
        self.guest_dir("g2", "g2.qcow2")
        self.assertEqual(VU.out_dir("g2", self.args(guests=["g1", "g2"])), self.out / "g2")
        self.assertEqual(VU.out_dir("g1", self.args()), self.out)

    def test_a_lock_failure_still_writes_a_result(self):
        with mock.patch.object(VU, "Guest", side_effect=VU.Failed("another vm-update is working on it")):
            e, res = self.fails(VU.Failed, try_=True)
        self.assertEqual(res["phase"], "start")
        self.assertEqual(res["rc"], 1)
        self.assertIn("another vm-update", res["tail"][0])

    def test_an_overrun_is_recorded(self):
        class Over(FakeVM):
            def wait_ssh(self, rebooting=False):
                self.overrun = "over its reservation"
                raise VU.Failed("the VM stopped while booting")
        with mock.patch.object(VU, "VM", Over):
            e, res = self.fails(VU.Failed, try_=True)
        self.assertTrue(res["overrun"])
        self.assertEqual(res["overrun_why"], "over its reservation")
        self.assertEqual(res["phase"], "boot")


class TestMerges(World):
    def setUp(self):
        super().setUp()
        self.d = self.guest_dir()
        self.g = FakeGuest(d=self.d)
        self.overlay = self.d / "o"
        self.overlay.write_text("overlay")
        self.states = {None: {"generation": "a" * 64, "updates": 1, "sha256": {"g1.qcow2": "s"}},
                       "other": {"generation": "a" * 64, "updates": 1, "sha256": {"g1.qcow2": "t"}}}
        self.g.path = lambda img: "/orig"
        self.order = []
        self.pending = {}
        p = [mock.patch.object(VU, "on_host", self.on_host),
             mock.patch.object(VU, "sha256_on", lambda h, p: "sum"),
             mock.patch.object(VU, "check_hosts", lambda g, h, start, exclude=None: self.states),
             mock.patch.object(VU, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")),
             mock.patch.object(VU, "read_json", lambda h, n, name: self.pending.get((h, name))),
             mock.patch.object(VU, "write_json", self.write_json),
             mock.patch.object(VU, "merge_on", self.merge_on)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.fail_on = "no host"
        self.exc = VU.Failed("boom")
        self.moves = []

    def on_host(self, host, script, *args, input=None):
        if script.startswith("[[ ! -e $1 ]] || mv"):
            self.moves.append((host, args))
        return ""

    def write_json(self, h, n, name, data):
        if data is None:
            self.pending.pop((h, name), None)
        else:
            self.pending[(h, name)] = copy.deepcopy(data)

    def merge_on(self, g, host, pending):
        self.order.append(host)
        if host == self.fail_on:
            raise self.exc
        self.pending.pop((host, VU.PENDING_FILE), None)

    def ship(self, hosts=(None, "other"), rec=None):
        return VU.ship_and_commit(self.g, list(hosts), {"/orig": str(self.overlay)}, self.states, rec, None, None)

    def test_one_at_a_time_this_host_last(self):
        self.assertEqual(VU.merge_order([None, "a", "b"]), ["a", "b", None])
        gen = self.ship()
        self.assertEqual(self.order, ["other", None])
        self.assertRegex(gen, r"^[0-9a-f]{64}$")

    def test_a_failure_stops_before_this_host(self):
        self.fail_on = "other"
        with self.assertRaisesRegex(VU.Failed, "--finish g1"):
            self.ship()
        self.assertEqual(self.order, ["other"])
        self.assertIn(("other", VU.PENDING_FILE), self.pending)
        self.assertIn((None, VU.PENDING_FILE), self.pending)

    def test_overlay_lost_stays_itself(self):
        self.fail_on = "other"
        self.exc = VU.OverlayLost("OVERLAY-LOST on other")
        with self.assertRaises(VU.OverlayLost):
            self.ship()

    def test_the_fingerprint_rides_in_pending(self):
        rec = build("debian12")
        self.fail_on = "other"
        with mock.patch.object(VU, "fingerprint_mod", lambda: FP), self.assertRaises(VU.Failed):
            self.ship(rec=rec)
        side = self.pending[("other", VU.PENDING_FILE)]["fingerprint"]
        self.assertEqual(side["source"], "update")
        self.assertEqual(side["generation"], self.pending[("other", VU.PENDING_FILE)]["state"]["generation"])

    def test_a_failed_merge_moves_ram_overlays_to_disk(self):
        ram = f"{VU.RAM_BASE}/vm-update.x.g1/.vm-update.g1.qcow2"
        pend = {"overlays": {"g1.qcow2": ram}, "before": {}, "state": {}}
        VU.overlays_to_disk(self.g, "other", pend)
        self.assertEqual(self.moves[0][1], (ram, f"{VU.REMOTE_VMS}/g1/.vm-update.g1.qcow2"))
        self.assertEqual(pend["overlays"]["g1.qcow2"], f"{VU.REMOTE_VMS}/g1/.vm-update.g1.qcow2")
        self.assertIn(("other", VU.PENDING_FILE), self.pending)

    def test_a_disk_overlay_stays_put(self):
        pend = {"overlays": {"g1.qcow2": "backotter-work/vms/g1/.vm-update.g1.qcow2"}, "before": {}, "state": {}}
        VU.overlays_to_disk(self.g, "other", pend)
        self.assertEqual(self.moves, [])

    def test_a_failure_right_after_the_move_leaves_no_overlay(self):
        def bad(h, p):
            raise VU.Failed("no sum")
        with mock.patch.object(VU, "sha256_on", bad), self.assertRaisesRegex(VU.Failed, "no sum"):
            self.ship()
        self.assertEqual(sorted(p.name for p in self.d.glob(".vm-update.*.qcow2")), [])
        self.assertEqual(self.order, [])

    def test_a_bad_sidecar_read_leaves_no_overlay(self):
        def bad(h, n, name):
            raise VU.Failed("not JSON")
        with mock.patch.object(VU, "fingerprint_mod", lambda: FP), mock.patch.object(VU, "read_json", bad):
            with self.assertRaisesRegex(VU.Failed, "not JSON"):
                self.ship(rec=build("debian12"))
        self.assertEqual(sorted(p.name for p in self.d.glob(".vm-update.*.qcow2")), [])

    def test_no_room_elsewhere_means_no_merge(self):
        with mock.patch.dict(os.environ, {"VM_UPDATE_RAM": "2"}):
            with mock.patch.object(VU, "RoomElsewhere") as R:
                R.return_value.wait.side_effect = VU.Failed("no room for the overlay")
                (VU.ram_dir_of("g1")).mkdir()
                with self.assertRaisesRegex(VU.Failed, "no room"):
                    self.ship()
                self.assertEqual(self.order, [])
                R.return_value.release.assert_called()
        self.assertNotIn((None, VU.PENDING_FILE), self.pending)


class TestMergeOn(World):
    def test_overlay_lost_keeps_the_marks_and_says_what_to_do(self):
        g = FakeGuest(d=self.guest_dir())
        removed = []

        def counted(host, tag, what, script, *args):
            e = VU.Failed("OVERLAY-LOST")
            e.status = 4
            raise e
        pend = {"overlays": {"g1.qcow2": "/mnt/bko-ram/x"}, "before": {"g1.qcow2": "s"}, "state": {"generation": "g", "updates": 1}}
        with mock.patch.object(VU, "on_host_counted", counted), mock.patch.object(VU, "on_host", lambda *a, **k: removed.append(a)):
            with self.assertRaises(VU.OverlayLost) as c:
                VU.merge_on(g, None, pend)
        self.assertEqual(c.exception.rc, 4)
        self.assertEqual(removed, [])
        for needle in ("OVERLAY-LOST", "g1.qcow2", "image-state", "merging", ".vm-update.pending", "update g1 again"):
            self.assertIn(needle, str(c.exception))

    def marks(self, d):
        (d / "g1.qcow2").write_text("x") if not (d / "g1.qcow2").exists() else None
        for name in (".vm-update.g1.qcow2.merging", ".vm-update.g1.qcow2.merging.committed"):
            (d / name).write_text("")

    def test_a_cut_short_runs_marks_go_at_the_resume(self):
        d = self.guest_dir()
        g = FakeGuest(d=d)
        self.marks(d)
        pend = {"overlays": {"g1.qcow2": "/x"}, "before": {"g1.qcow2": "s"}, "state": {"generation": "g2", "updates": 2},
                "overlay_sums": {"g1.qcow2": "o"}, "wear_tag": "t", "done": {"g1.qcow2": "new"}}
        VU.write_json(None, "g1", VU.PENDING_FILE, pend)
        with mock.patch.object(VU, "stat_of", lambda h, gg: {}), \
                mock.patch.object(VU, "on_host_counted", side_effect=AssertionError("committed again")):
            VU.merge_on(g, None, pend)
        self.assertEqual([p.name for p in d.glob(".vm-update.*merging*")], [])
        self.assertFalse((d / VU.PENDING_FILE).exists())

    def test_marks_that_wont_go_leave_the_pending_for_finish(self):
        d = self.guest_dir()
        g = FakeGuest(d=d)
        pend = {"overlays": {"g1.qcow2": "/x"}, "before": {"g1.qcow2": "s"}, "state": {"generation": "g2", "updates": 2},
                "overlay_sums": {"g1.qcow2": "o"}, "wear_tag": "t"}
        VU.write_json(None, "g1", VU.PENDING_FILE, pend)
        real = VU.on_host

        def flaky(host, script, *a, **k):
            if script == VU.MARKS_RM_SCRIPT:
                raise VU.Failed("ssh dropped")
            return real(host, script, *a, **k)
        with mock.patch.object(VU, "on_host_counted", lambda *a: "new\n"), mock.patch.object(VU, "on_host", flaky):
            with self.assertRaisesRegex(VU.Failed, "ssh dropped"):
                VU.merge_on(g, None, pend)
        self.assertEqual(json.loads((d / VU.PENDING_FILE).read_text())["done"], {"g1.qcow2": "new"})

    def check(self, d, g):
        (d / VU.STATE_FILE).write_text(json.dumps({"generation": "a", "updates": 1, "sha256": {"g1.qcow2": "s"}}))
        with mock.patch.object(VU, "busy", lambda *a: []), mock.patch.object(VU, "sha256_on", lambda h, p: "s"), \
                mock.patch.object(VU, "machine_id", lambda h, n: "m"):
            return VU.check_hosts(g, [None], start=False)

    def test_marks_with_no_pending_are_refused(self):
        d = self.guest_dir()
        g = FakeGuest(d=d)
        self.assertEqual(self.check(d, g)[None]["generation"], "a")
        self.marks(d)
        with self.assertRaisesRegex(VU.Failed, r"merging.*no merge pending"):
            self.check(d, g)

    def test_the_other_hosts_commit_is_counted(self):
        seen = []
        with mock.patch.object(VU, "on_host", lambda host, script, *a, **k: seen.append((host, script, a)) or ""):
            VU.on_host_counted("other", "guest:r:g", "merge", "echo hi", "x")
        self.assertIn("systemd-run", seen[0][1])
        self.assertIn("iolog.sh", seen[0][1])
        self.assertEqual(seen[0][2][:2], ("guest:r:g", "merge"))

    def test_this_hosts_commit_runs_plain(self):
        seen = []
        with mock.patch.object(VU, "on_host", lambda host, script, *a, **k: seen.append((host, script)) or ""):
            VU.on_host_counted(None, "t", "w", "echo hi")
        self.assertEqual(seen, [(None, "echo hi")])

    def test_stat_and_sidecars_are_written(self):
        d = self.guest_dir()
        g = FakeGuest(d=d)
        pend = {"overlays": {"g1.qcow2": "/x"}, "before": {"g1.qcow2": "s"}, "state": {"generation": "g2", "updates": 2},
                "overlay_sums": {"g1.qcow2": "o"}, "wear_tag": "t", "fingerprint": {"generation": "g2"}, "done": {"g1.qcow2": "new"}}
        with mock.patch.object(VU, "stat_of", lambda h, gg: {"g1.qcow2": {"size": 1, "mtime_ns": 2, "inode": 3}}):
            VU.write_json(None, "g1", VU.PENDING_FILE, pend)
            VU.merge_on(g, None, pend)
        self.assertEqual(json.loads((d / VU.FP_FILE).read_text()), {"generation": "g2"})
        self.assertEqual(json.loads((d / VU.STAT_FILE).read_text()), {"generation": "g2", "disks": {"g1.qcow2": {"size": 1, "mtime_ns": 2, "inode": 3}}})
        self.assertEqual(json.loads((d / VU.STATE_FILE).read_text())["sha256"], {"g1.qcow2": "new"})
        self.assertFalse((d / VU.PENDING_FILE).exists())


# --- stopping, and the other host's reservation ---------------------------------------------------

STOP_DRIVER = """
import importlib.machinery, importlib.util, sys, time
loader = importlib.machinery.SourceFileLoader("vmu", sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
VU = importlib.util.module_from_spec(spec)
loader.exec_module(VU)
def update(name, args, hosts):
    print("started", flush=True)
    if not VU.stopping.wait(20):
        raise VU.Failed("never told to stop")
    time.sleep(1.5)                            # as a VM's stop takes a moment: room for later signals
    raise VU.Failed("stopped")
VU.update = update
sys.argv = ["vm-update", "--try", "--hosts", "", "g"]
sys.exit(VU.main())
"""


class TestStopSignals(unittest.TestCase):
    def go(self, *signals, gap=0.1):
        p = subprocess.Popen([sys.executable, "-c", STOP_DRIVER, str(TESTVM / "vm-update")], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=dict(os.environ, BKO_WORK=tempfile.gettempdir()))
        self.addCleanup(p.kill)
        self.assertEqual(p.stdout.readline().strip(), "started")
        for s in signals:
            p.send_signal(s)
            time.sleep(gap)
        out, err = p.communicate(timeout=30)
        p.stdout.close()
        p.stderr.close()
        return p.returncode, out, err

    def test_one_term_stops(self):
        rc, out, err = self.go(signal.SIGTERM)
        self.assertEqual(rc, 1, err)
        self.assertIn("FAILED g: stopped", out)

    def test_a_burst_of_terms_is_one_stop(self):
        for gap in (0.00002, 0.0001, 0.001):
            rc, out, err = self.go(*[signal.SIGTERM] * 5, gap=gap)
            self.assertEqual(rc, 1, err)
            self.assertIn("FAILED g: stopped", out)
            self.assertNotIn("stopped at once", err)

    def test_a_hangup_stops_with_its_output_gone(self):
        rc, out, err = self.go(signal.SIGHUP)
        self.assertEqual(rc, 1, err)

    def test_a_second_ctrl_c_stops_at_once(self):
        rc, out, err = self.go(signal.SIGINT, signal.SIGINT)
        self.assertEqual(rc, 130, err)
        self.assertIn("stopped at once", err)


@unittest.skipUnless(shutil.which("setsid") and shutil.which("flock"), "needs util-linux")
class TestRoomScript(TempDir):
    def fake(self, body):
        d = self.tmp / "backotter-work"
        d.mkdir(exist_ok=True)
        f = d / "cmp-run.sh"
        f.write_text("#!/bin/bash\n" + body)
        f.chmod(0o755)

    def start(self):
        self.lock = self.tmp / "lock"
        p = subprocess.Popen(["bash", "-c", VU.ROOM_SCRIPT, "bash", "1", str(self.lock)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, HOME=str(self.tmp)))
        self.addCleanup(p.kill)
        return p

    def pid_of(self, name="pid"):
        f = self.tmp / name
        for _ in range(100):
            if f.exists() and f.read_text().strip():
                return int(f.read_text())
            time.sleep(0.05)
        self.fail("no pid")

    def gone(self, pid):
        for _ in range(100):
            if not VU.pid_alive(pid):
                return True
            time.sleep(0.05)
        return False

    def test_a_waiter_goes_when_stdin_ends(self):
        self.fake(f"echo $$ >{self.tmp}/pid; while :; do sleep 30; done")
        p = self.start()
        pid = self.pid_of()
        p.stdin.close()
        self.assertEqual(p.wait(10), 0)
        self.assertTrue(self.gone(pid))
        self.assertEqual(p.stdout.read(), b"")             # nothing left holding ssh's channel open
        p.stdout.close()
        p.stderr.close()

    def test_a_refused_reservation_ends_with_its_reason(self):
        self.fake("echo 'can never fit' >&2; exit 3")
        p = self.start()
        self.assertEqual(p.wait(10), 3)
        self.assertIn(b"can never fit", p.stderr.read())
        p.stdin.close()
        p.stdout.close()
        p.stderr.close()

    def test_held_says_so_and_goes_when_stdin_ends(self):
        self.fake(f'echo $$ >{self.tmp}/pid; shift 3; exec "$@"')
        p = self.start()
        self.assertEqual(p.stdout.readline(), b"reserved\n")
        pid = self.pid_of()
        self.assertTrue(VU.pid_alive(pid))
        p.stdin.close()
        self.assertEqual(p.wait(10), 0)
        self.assertTrue(self.gone(pid))
        p.stdout.close()
        p.stderr.close()

    def test_the_held_command_is_one_line(self):
        held = re.search(r"sh -c '(.*?)' sh", VU.ROOM_SCRIPT, re.S)[1]
        self.assertNotIn("\n", held)


if __name__ == "__main__":
    unittest.main()
