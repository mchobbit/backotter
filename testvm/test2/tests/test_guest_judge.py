"""K3's tests: the judge (no host, no VM).  Each rule family has a positive and a negative
case; #18, #19 and #20 are replays of what those defects looked like.  Mount lists come from the
corpus (holt/tests/corpus/*/src.json), the rest from tests/fixtures/guest/ and hand-made hook output in
the scripts' format."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from t2lib import guest_judge as J  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
CORPUS = os.path.join(REPO, "holt", "tests", "corpus")
FX = os.path.join(HERE, "fixtures", "guest")
POLICY = {"free_note_pct": 80, "free_fail_pct": 90, "boot_free_min_mib": 200}


def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def sec(*parts):
    """Hook output from ("file PATH", "lines") and ("cmd NAME", "lines") pairs, with === end."""
    out = []
    for head, body in parts:
        out.append(f"=== {head}")
        out.append(body)
    out.append(J.END)
    return J.parse_sections("\n".join(out) + "\n")


def checks(**rules):
    return {"pm": "apt", "fw": "bios", "established": True, "rules": dict({"source": "S-apt", "loader": "L-grub", "kernel": "K-deb", "boot": "B-lin", "mounts": "M-lin"}, **rules)}


def src(rule, s):
    return J.judge_image(s, checks(source=rule), POLICY, updates=1)["image:sources"]


def ld(rule, s):
    return J.judge_image(s, checks(loader=rule), POLICY, updates=1)["image:loader"]


class Sections(unittest.TestCase):
    def test_parse(self):
        s = J.parse_sections("=== file /etc/x\na\nb\n=== cmd y\nc\n=== end\n")
        self.assertEqual(s.file("/etc/x"), ["a", "b"])
        self.assertEqual(s.cmd("y"), ["c"])
        self.assertTrue(s.ended)

    def test_empty_and_cut_off_hooks(self):
        self.assertEqual(J.check_hook("", ("x",)), (False, "empty output"))
        ok, why = J.check_hook("=== cmd x\na\n", ("x",))
        self.assertFalse(ok)
        self.assertIn("cut off", why)
        ok, why = J.check_hook("=== cmd x\na\n=== end\n", ("y",))
        self.assertFalse(ok)
        self.assertIn("no section y", why)
        self.assertEqual(J.check_hook("=== cmd x\na\n=== end\n", ("x",)), (True, None))

    def test_output_after_the_end_is_cut_off(self):
        self.assertFalse(J.parse_sections("=== end\n=== cmd x\n").ended)


class Sources(unittest.TestCase):
    """#19: a guest's package sources, rule by rule."""

    def test_apt_19_devuan_without_security_is_a_fail(self):
        s = sec(("file /etc/apt/sources.list", "deb http://deb.devuan.org/merged excalibur main"))
        n = src("S-apt", s)
        self.assertEqual(n.v, "FAIL")
        self.assertEqual(n.lines, ["no -security suite in apt's sources"])

    def test_apt_with_security(self):
        s = sec(("file /etc/apt/sources.list.d/d.list", "deb http://x/merged excalibur main\ndeb http://x/merged excalibur-security main"))
        self.assertEqual(src("S-apt", s).v, "PASS")

    def test_apt_deb822_and_disabled_stanzas(self):
        body = "Types: deb\nURIs: http://deb.debian.org/debian\nSuites: bookworm bookworm-updates\n\nEnabled: no\nTypes: deb\nURIs: http://security\nSuites: bookworm-security\n"
        self.assertEqual(src("S-apt", sec(("file /etc/apt/sources.list.d/d.sources", body))).v, "FAIL")
        body2 = "Types: deb\nURIs: http://deb.debian.org/debian\nSuites: bookworm\n\nTypes: deb\nURIs: http://security\nSuites: bookworm-security\n"
        self.assertEqual(src("S-apt", sec(("file /etc/apt/sources.list.d/d.sources", body2))).v, "PASS")

    def test_apt_snapshot_and_dvd(self):
        body = "Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/20260101T000000Z/\nSuites: noble noble-security\n"
        self.assertIn("snapshot.ubuntu.com", src("S-apt", sec(("file /etc/apt/sources.list.d/ubuntu.sources", body))).lines[0])
        self.assertIn("install DVD", src("S-apt", sec(("file /etc/apt/sources.list", "deb cdrom:[x]/ bookworm main"))).lines[0])

    def test_updates_zero_is_skip(self):
        s = sec(("file /etc/apt/sources.list", "deb cdrom:[x]/ bookworm main"))
        for u in (0, None):
            n = J.judge_image(s, checks(), POLICY, updates=u)["image:sources"]
            self.assertEqual(n.v, "SKIP")
            self.assertEqual(n.why, J.U0)

    def test_dnf(self):
        ok = sec(("file /etc/yum.repos.d/fedora-updates.repo", "[updates]\nenabled=1\n[updates-debuginfo]\nenabled=0"))
        bad = sec(("file /etc/yum.repos.d/fedora-updates.repo", "[updates]\nenabled=0"))
        self.assertEqual(src("S-dnf", ok).v, "PASS")
        self.assertEqual(src("S-dnf", bad).v, "FAIL")
        vault = sec(("file /etc/yum.repos.d/rocky.repo", "[baseos]\nenabled=1\nbaseurl=https://dl.rockylinux.org/vault/rocky/10.0/BaseOS"))
        live = sec(("file /etc/yum.repos.d/rocky.repo", "[baseos]\nenabled=1\nmirrorlist=https://mirrors.rockylinux.org/x"))
        self.assertEqual(src("S-dnf", vault).v, "FAIL")
        self.assertEqual(src("S-dnf", live).v, "PASS")

    def test_pacman(self):
        ok = sec(("file /etc/pacman.d/mirrorlist", "Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch"))
        arch = sec(("file /etc/pacman.d/mirrorlist", "Server = https://archive.archlinux.org/repos/2026/10/01/$repo/os/$arch"))
        self.assertEqual(src("S-pac", ok).v, "PASS")
        self.assertEqual(src("S-pac", arch).v, "FAIL")
        self.assertEqual(src("S-pac", sec(("file /etc/pacman.d/mirrorlist", "#Server = x"))).v, "FAIL")

    def test_pacman_artix_endeavour_cachy(self):
        art = sec(("file /etc/os-release", 'ID=artix'), ("file /etc/pacman.d/mirrorlist", "Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch"))
        self.assertIn("no Artix mirror", src("S-pac", art).lines[0])
        eos = sec(("file /etc/os-release", 'ID=endeavouros'), ("file /etc/pacman.d/mirrorlist", "Server = https://x"))
        self.assertIn("endeavouros-mirrorlist", src("S-pac", eos).lines[0])
        eos2 = sec(("file /etc/os-release", 'ID=endeavouros'), ("file /etc/pacman.d/mirrorlist", "Server = https://x"),
                   ("file /etc/pacman.d/endeavouros-mirrorlist", "Server = https://y"))
        self.assertEqual(src("S-pac", eos2).v, "PASS")
        c = sec(("file /etc/os-release", 'ID=cachyos'), ("file /etc/pacman.d/mirrorlist", "Server = https://x"))
        self.assertIn("cachyos-mirrorlist", src("S-pac", c).lines[0])

    def test_zypper(self):
        ok = sec(("file /etc/zypp/repos.d/o.repo", "[o]\nenabled=1\nbaseurl=http://download.opensuse.org/tumbleweed/repo/oss/"))
        hist = sec(("file /etc/zypp/repos.d/o.repo", "[o]\nenabled=1\nbaseurl=http://download.opensuse.org/history/20261030/tumbleweed/repo/oss/"))
        dvd = sec(("file /etc/zypp/repos.d/o.repo", "[o]\nenabled=1\nbaseurl=dvd:/?devices=/dev/sr0"))
        self.assertEqual(src("S-zyp", ok).v, "PASS")
        self.assertEqual(src("S-zyp", hist).v, "FAIL")
        self.assertEqual(src("S-zyp", dvd).v, "FAIL")

    def test_moss(self):
        self.assertEqual(src("S-moss", sec(("file /etc/moss/repo.d/a.kdl", 'version "stream/unstable"'))).v, "PASS")
        self.assertEqual(src("S-moss", sec(("file /etc/moss/repo.d/a.kdl", '  version "tag/2026-11-02-unstable-stream"'))).v, "FAIL")

    def test_apk(self):
        osr = ("file /etc/os-release", "ID=alpine\nVERSION_ID=3.24.1")
        self.assertEqual(src("S-apk", sec(osr, ("file /etc/apk/repositories", "https://dl-cdn.alpinelinux.org/alpine/v3.24/main"))).v, "PASS")
        self.assertEqual(src("S-apk", sec(osr, ("file /etc/apk/repositories", "https://dl-cdn.alpinelinux.org/alpine/v3.22/main"))).v, "FAIL")
        self.assertEqual(src("S-apk", sec(("file /etc/apk/repositories", "#nothing"))).v, "FAIL")

    def test_xbps(self):
        self.assertEqual(src("S-xbps", sec(("file /usr/share/xbps.d/00-repository-main.conf", "repository=https://repo-default.voidlinux.org/current/musl"))).v, "PASS")
        self.assertEqual(src("S-xbps", sec(("file /usr/share/xbps.d/x.conf", "# nothing"))).v, "FAIL")

    def test_portage(self):
        """gentoo.sh's guests: the binhost, make.conf's getbinpkg, the gentoo repository's sync-uri."""
        bins = ("file /etc/portage/binrepos.conf/gentoo.conf",
                "[gentoo]\npriority = 1\nsync-uri = https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64")
        make = ("file /etc/portage/make.conf", 'COMMON_FLAGS="-O2 -pipe"\nFEATURES="${FEATURES} getbinpkg binpkg-request-signature"')
        repo = ("file /usr/share/portage/config/repos.conf",
                "[DEFAULT]\nmain-repo = gentoo\n\n[gentoo]\nlocation = /var/db/repos/gentoo\nsync-uri = rsync://rsync.gentoo.org/gentoo-portage")
        self.assertEqual(src("S-portage", sec(bins, make, repo)).v, "PASS")
        self.assertEqual(src("S-portage", sec(make, repo)).lines, ["no binary package host in /etc/portage/binrepos.conf"])
        nobin = ("file /etc/portage/make.conf", 'FEATURES="${FEATURES} -getbinpkg binpkg-request-signature"')
        self.assertEqual(src("S-portage", sec(bins, nobin, repo)).lines, ["make.conf's FEATURES has no getbinpkg"])
        self.assertEqual(src("S-portage", sec(bins, make)).lines, ["no sync-uri for the gentoo repository"])
        # /etc/portage/repos.conf over the default: a gentoo section there without a sync-uri
        # leaves the default's.
        self.assertEqual(src("S-portage", sec(bins, make, repo, ("file /etc/portage/repos.conf/gentoo.conf", "[gentoo]\nauto-sync = no"))).v, "PASS")

    def test_nix(self):
        self.assertEqual(src("S-nix", sec(("file /etc/nixos/configuration.nix", 'nix.nixPath = [ "nixpkgs=https://channels.nixos.org/nixos-26.05/nixexprs.tar.xz" ];'))).v, "PASS")
        self.assertEqual(src("S-nix", sec(("file /etc/nixos/configuration.nix", 'nix.nixPath = [ "nixpkgs=https://releases.nixos.org/x" ];'))).v, "FAIL")

    def test_freebsd(self):
        repo = 'FreeBSD-ports: {\n  url: "pkg+https://pkg.FreeBSD.org/${ABI}/quarterly",\n  enabled: yes\n}\n'
        ok = sec(("file /etc/pkg/FreeBSD.conf", repo), ("cmd pkgbase", "no"))
        self.assertEqual(src("S-fbsd", ok).v, "PASS")
        off = sec(("file /etc/pkg/FreeBSD.conf", repo), ("file /usr/local/etc/pkg/repos/FreeBSD.conf", "FreeBSD-ports: { enabled: no }"), ("cmd pkgbase", "no"))
        self.assertEqual(src("S-fbsd", off).v, "FAIL")
        pb = sec(("file /etc/pkg/FreeBSD.conf", repo), ("cmd pkgbase", "yes"))
        self.assertIn("base repository", src("S-fbsd", pb).lines[0])
        pb2 = sec(("file /etc/pkg/FreeBSD.conf", repo + 'FreeBSD-base: {\n url: "x",\n enabled: yes\n}\n'), ("cmd pkgbase", "yes"))
        self.assertEqual(src("S-fbsd", pb2).v, "PASS")

    def test_ghostbsd(self):
        ok = sec(("file /etc/pkg/GhostBSD.conf", 'GhostBSD_base: {\n url: "https://pkg.ghostbsd.org/x",\n enabled: yes\n}\n'))
        self.assertEqual(src("S-gbsd", ok).v, "PASS")
        self.assertEqual(src("S-gbsd", sec(("file /etc/pkg/FreeBSD.conf", 'FreeBSD: {\n url: "x",\n enabled: yes\n}\n'))).v, "FAIL")

    def test_the_other_bsds_ips_haiku(self):
        self.assertEqual(src("S-hbsd", sec(("file /etc/hbsd-update.conf", "dnsrec=x.y"))).v, "PASS")
        self.assertEqual(src("S-hbsd", sec(("file /etc/hbsd-update.conf", "# none"))).v, "FAIL")
        nb = sec(("file /usr/pkg/etc/pkgin/repositories.conf", "https://cdn.netbsd.org/x"), ("file /usr/pkg/etc/sysupgrade.conf", 'RELEASEDIR="x"'))
        self.assertEqual(src("S-nbsd", nb).v, "PASS")
        self.assertEqual(len(src("S-nbsd", sec(("file /usr/pkg/etc/pkgin/repositories.conf", "# none"))).lines), 2)
        self.assertEqual(src("S-obsd", sec(("file /etc/installurl", "https://cdn.openbsd.org/pub/OpenBSD"))).v, "PASS")
        self.assertEqual(src("S-obsd", sec(("file /etc/installurl", ""))).v, "FAIL")
        dr = 'Avalon: {\n url: "https://x",\n enabled: yes\n}\n'
        self.assertEqual(src("S-dfly", sec(("file /usr/local/etc/pkg/repos/df-latest.conf", dr))).v, "PASS")
        self.assertEqual(src("S-dfly", sec(("cmd x", ""))).v, "FAIL")
        # dragonfly6's own df-latest.conf (T3.8): bare urls, one block enabled
        bare = ('Avalon: {\n\turl\t\t: https://mirror-master.dragonflybsd.org/dports/${ABI}/LATEST,\n'
                '\tmirror_type\t: NONE,\n\tpriority\t: 0,\n\tenabled\t\t: yes\n}\n'
                'MUUG: {\n\turl\t\t: https://muug.ca/mirror/dragonflybsd/dports/${ABI}/LATEST,\n\tenabled\t\t: no\n}\n')
        self.assertEqual(src("S-dfly", sec(("file /usr/local/etc/pkg/repos/df-latest.conf", bare))).v, "PASS")
        self.assertEqual(J._enabled_repos(J._pkg_repos(sec(("file /usr/local/etc/pkg/repos/df-latest.conf", bare)))),
                         {"Avalon": {"url": "https://mirror-master.dragonflybsd.org/dports/X/LATEST", "enabled": True}})
        self.assertEqual(src("S-dfly", sec(("file /usr/local/etc/pkg/repos/df-latest.conf", bare.replace("yes", "no")))).v, "FAIL")
        self.assertEqual(src("S-ips", sec(("cmd pkg-publisher", "omnios origin online F https://pkg.omnios.org/r151058/core/"))).v, "PASS")
        self.assertEqual(src("S-ips", sec(("cmd pkg-publisher", ""))).v, "FAIL")
        self.assertEqual(src("S-haiku", sec(("cmd pkgman-repos", "HaikuPorts"))).v, "PASS")
        self.assertEqual(src("S-haiku", sec(("cmd pkgman-repos", ""))).v, "FAIL")

    def test_no_rule(self):
        n = J.judge_image(sec(("cmd x", "")), checks(source="S-none"), POLICY, updates=1)["image:sources"]
        self.assertEqual((n.v, n.why), ("SKIP", J.NO_RULE))


GRUB = """set default="0"
menuentry 'Debian' {
\tlinux\t/vmlinuz-6.1.0-54-amd64 root=UUID=x ro
}
submenu 'Advanced' {
  menuentry 'x' {
\tlinux\t/vmlinuz-6.1.0-9-amd64 root=UUID=x ro
  }
}
"""


class Loader(unittest.TestCase):
    def test_grub_first_is_newest(self):
        self.assertEqual(ld("L-grub", sec(("file /boot/grub/grub.cfg", GRUB))).v, "PASS")

    def test_grub_first_is_old(self):
        bad = GRUB.replace("6.1.0-54", "X").replace("6.1.0-9", "6.1.0-54").replace("X", "6.1.0-9")
        self.assertEqual(ld("L-grub", sec(("file /boot/grub/grub.cfg", bad))).v, "FAIL")

    def test_grub_default_points_elsewhere(self):
        self.assertEqual(ld("L-grub", sec(("file /boot/grub/grub.cfg", GRUB.replace('"0"', '"1"')))).v, "FAIL")

    def test_grub_saved_entry(self):
        cfg = GRUB.replace('set default="0"', 'set default="${saved_entry}"')
        self.assertEqual(ld("L-grub", sec(("file /boot/grub/grub.cfg", cfg), ("file /boot/grub/grubenv", "saved_entry=0"))).v, "PASS")
        env = "saved_entry=Advanced>Debian with Linux 6.1.0-9-amd64"
        self.assertEqual(ld("L-grub", sec(("file /boot/grub/grub.cfg", cfg), ("file /boot/grub/grubenv", env))).v, "FAIL")

    def test_grub_named_kernels_have_no_version(self):
        cfg = 'set default="0"\nmenuentry a {\n linux /vmlinuz-linux root=x\n}\n'
        self.assertEqual(ld("L-grub", sec(("file /boot/grub/grub.cfg", cfg))).v, "PASS")
        self.assertEqual(ld("L-grub", sec(("cmd x", ""))).v, "FAIL")

    def test_bls(self):
        ent = "abc-6.17.1-200.fc44.x86_64.conf\nabc-7.0.4-200.fc44.x86_64.conf\nabc-0-rescue-1.conf"
        ok = sec(("cmd bls-entries", ent), ("file /boot/grub2/grubenv", "saved_entry=abc-7.0.4-200.fc44.x86_64"))
        bad = sec(("cmd bls-entries", ent), ("file /boot/grub2/grubenv", "saved_entry=abc-6.17.1-200.fc44.x86_64"))
        self.assertEqual(ld("L-bls", ok).v, "PASS")
        self.assertEqual(ld("L-bls", bad).v, "FAIL")
        self.assertEqual(ld("L-bls", sec(("cmd bls-entries", ""))).v, "FAIL")

    def test_sd_18_pop_without_default(self):
        s = sec(("cmd sd-entries", "Pop_OS-current.conf\nPop_OS-oldkern.conf"), ("file /boot/efi/loader/loader.conf", "timeout 3"))
        n = ld("L-sd", s)
        self.assertEqual(n.v, "FAIL")
        self.assertIn("old entry", n.lines[0])

    def test_sd_with_default(self):
        s = sec(("cmd sd-entries", "Pop_OS-current.conf\nPop_OS-oldkern.conf"), ("file /boot/efi/loader/loader.conf", "default Pop_OS-current"))
        self.assertEqual(ld("L-sd", s).v, "PASS")

    def test_sd_glob_and_one_entry(self):
        s = sec(("cmd sd-entries", "aerynos-6.17.conf"), ("file /efi/loader/loader.conf", 'default "aerynos*"'))
        self.assertEqual(ld("L-sd", s).v, "PASS")
        # endeavouros: no default at all; one entry is harmless
        self.assertEqual(ld("L-sd", sec(("cmd sd-entries", "x.conf"), ("file /efi/loader/loader.conf", "timeout 3"))).v, "PASS")
        s = sec(("cmd sd-entries", "aerynos-6.17.conf"), ("file /efi/loader/loader.conf", "default nothing*"))
        self.assertEqual(ld("L-sd", s).v, "FAIL")

    def test_limine(self):
        ok = "default_entry: 1\n/Omarchy\n  protocol: linux\n"
        self.assertEqual(ld("L-limine", sec(("file /boot/limine.conf", ok))).v, "PASS")
        self.assertEqual(ld("L-limine", sec(("file /boot/limine.conf", "default_entry: 3\n/One\n"))).v, "FAIL")

    def test_fbsd(self):
        self.assertEqual(ld("L-fbsd", sec(("file /boot/loader.conf", 'zfs_load="YES"'), ("cmd zfs-boot", "root zroot/ROOT/default\nbootfs zroot/ROOT/default"))).v, "PASS")
        self.assertEqual(ld("L-fbsd", sec(("file /boot/loader.conf", 'kernel="kernel.old"'))).v, "FAIL")
        self.assertEqual(ld("L-fbsd", sec(("cmd zfs-boot", "root zroot/ROOT/default\nbootfs zroot/ROOT/old"))).v, "FAIL")

    def test_nbsd(self):
        cfg = "menu=Boot normally:boot\nmenu=Boot old:boot onetbsd\ndefault=1"
        self.assertEqual(ld("L-nbsd", sec(("file /boot.cfg", cfg))).v, "PASS")
        self.assertEqual(ld("L-nbsd", sec(("file /boot.cfg", cfg.replace("default=1", "default=2")))).v, "FAIL")
        self.assertEqual(ld("L-nbsd", sec(("file /boot.cfg", cfg.replace("default=1", "default=9")))).v, "FAIL")
        self.assertEqual(ld("L-nbsd", sec(("cmd x", ""))).v, "PASS")   # no /boot.cfg: the loader's own default

    def test_obsd_dfly(self):
        self.assertEqual(ld("L-obsd", sec(("cmd x", ""))).v, "PASS")
        self.assertEqual(ld("L-obsd", sec(("file /etc/boot.conf", "boot /bsd.sp"))).v, "FAIL")
        self.assertEqual(ld("L-dfly", sec(("file /boot/loader.conf", "x=1"))).v, "PASS")
        self.assertEqual(ld("L-dfly", sec(("file /boot/loader.conf", 'kernel="old"'))).v, "FAIL")

    def test_ill(self):
        good = "a;u;;;1;static;100\nb;u;NR;/;1;static;200"
        bad = "a;u;NR;/;1;static;100\nb;u;;;1;static;200"
        self.assertEqual(ld("L-ill", sec(("cmd beadm", good))).v, "PASS")
        self.assertEqual(ld("L-ill", sec(("cmd beadm", bad))).v, "FAIL")

    def test_none(self):
        self.assertEqual(ld("L-none", sec(("cmd x", ""))).v, "SKIP")


class Free(unittest.TestCase):
    DF = ("Filesystem     1024-blocks      Used Available Capacity Mounted on\n"
          "/dev/vda3         13453016   1448976  11298880      12% /\n"
          "tmpfs              1000000   1000000         0     100% /run/foo\n"
          "/dev/loop3           10000     10000         0     100% /snap/core/1\n"
          "/dev/vda1           964900     89644    808888      10% /boot\n")

    def free(self, df):
        return J.judge_image(sec(("cmd df", df)), checks(), POLICY, 1)["image:free"]

    def test_pseudo_filesystems_dont_count(self):
        self.assertEqual(self.free(self.DF).v, "PASS")

    def test_full_and_note(self):
        n = self.free(self.DF.replace("      12% /", "      91% /", 1))
        self.assertEqual(n.v, "FAIL")
        self.assertIn("/ is at least 90% full", n.lines[0])
        n = self.free(self.DF.replace("      12% /", "      85% /", 1))
        self.assertEqual(n.v, "PASS")
        self.assertIn("85%", n.why)

    def test_boot_room(self):
        n = self.free(self.DF.replace("964900     89644    808888", "964900    800000    100000"))
        self.assertEqual(n.v, "FAIL")
        self.assertIn("/boot has under", n.lines[0])

    def test_haiku_two_line_df(self):
        t = ("\x1b[1mVolume\x1b[0m\n Type        Total      Free      Flags   Used%  Device\n"
             "\x1b[1m/boot             \x1b[0m\n        bfs   16.0 GiB  13.4 GiB QAM-P-W    16% /dev/disk/scsi/2/0/0/0  \n"
             "\x1b[1m/boot/system      \x1b[0m\n  packagefs          0         0 QAM-P--   100%     \n")
        rows = J.parse_df(t.splitlines())
        self.assertEqual(rows[0][:2], ("/boot", 16))
        self.assertEqual(len(rows), 1)

    def test_nothing_parsable(self):
        self.assertEqual(self.free("Filesystem\n").v, "FAIL")


class Kernels(unittest.TestCase):
    def k(self, rule, listing, **kw):
        c = checks(kernel=rule)
        c.update(kw)
        return J.judge_image(sec(("cmd kernels", listing)), c, POLICY, 1)["image:kernels"]

    def test_each_rules_limit(self):
        for rule, limit in J.KERNELS_MAX.items():
            self.assertEqual(self.k(rule, "\n".join(f"k{i}" for i in range(limit))).v, "PASS", rule)
            self.assertEqual(self.k(rule, "\n".join(f"k{i}" for i in range(limit + 1))).v, "FAIL", rule)

    def test_no_kernel(self):
        self.assertEqual(self.k("K-deb", "").v, "FAIL")

    def test_an_entry_overrides(self):
        self.assertEqual(self.k("K-pac", "a\nb\nc", kernels_max=3).v, "PASS")


class ZfsCache(unittest.TestCase):
    FS = "UUID=x /boot/efi vfat umask=0 0 1\n"
    LIST = "bpool/BOOT/ubuntu /boot\nrpool/ROOT/ubuntu /\nbpool none"

    def z(self, *extra):
        s = sec(("file /etc/fstab", self.FS), ("cmd zfs-list", self.LIST), *extra)
        return J.judge_image(s, checks(mounts="M-zfs"), POLICY, 1)["image:zfs-cache"]

    def test_20_ubuntu26_zfs_without_the_cache(self):
        n = self.z(("cmd zfs-list-cache", ""))
        self.assertEqual((n.v, n.lines), ("FAIL", ["no /etc/zfs/zfs-list.cache/bpool"]))

    def test_with_the_cache(self):
        self.assertEqual(self.z(("cmd zfs-list-cache", "bpool\nrpool")).v, "PASS")

    def test_no_fstab_mount_under_zfs(self):
        s = sec(("file /etc/fstab", "UUID=x /boot ext4 d 0 2"), ("cmd zfs-list", "rpool/ROOT/d /"), ("cmd zfs-list-cache", ""))
        self.assertEqual(J.judge_image(s, checks(mounts="M-zfs"), POLICY, 1)["image:zfs-cache"].v, "PASS")

    def test_only_on_zfs_guests(self):
        self.assertNotIn("image:zfs-cache", J.judge_image(sec(("cmd x", "")), checks(), POLICY, 1))


def reading(boot="", fstab="ok 1", mounts="format mountinfo", kernel=""):
    return sec(("cmd boot-state", boot), ("cmd fstab-check", fstab), ("cmd mounts", mounts), ("cmd kernel", kernel))


MI_OK = ("format mountinfo\n"
         "22 1 8:3 / / rw - ext4 /dev/vda3 rw\n"
         "23 22 0:5 / /proc rw - proc proc rw\n"
         "24 22 8:1 / /boot rw - ext4 /dev/vda1 rw\n")
RUN = "running 6.1.0-54-amd64\ninstalled 6.1.0-54-amd64\ninstalled 6.1.0-9-amd64"


class Standing(unittest.TestCase):
    """A line in both boots is standing (LIMIT); new after the update, FAIL."""

    def after(self, a, b, **rules):
        return J.judge_after(b, a, checks(**rules))

    def test_clean(self):
        r = self.after(reading("init systemd\nstate running", kernel=RUN, mounts=MI_OK), reading("init systemd\nstate running", kernel=RUN, mounts=MI_OK))
        self.assertTrue(all(r[n].v == "PASS" for n in J.AFTER_NEEDS))

    def test_new_failed_unit_is_fail(self):
        r = self.after(reading("init systemd\nstate degraded\nfailed foo.service", kernel=RUN), reading("init systemd\nstate running", kernel=RUN))
        self.assertEqual(r["after:failed-units"].v, "FAIL")
        self.assertEqual(r["after:failed-units"].new, ["failed foo.service"])
        self.assertEqual(r["after:boot-state"].v, "FAIL")

    def test_standing_unit_is_limit(self):
        a = reading("init systemd\nstate degraded\nfailed foo.service", kernel=RUN)
        r = self.after(a, a)
        self.assertEqual(r["after:failed-units"].v, "LIMIT")
        self.assertEqual(r["after:failed-units"].standing, ["failed foo.service"])

    def test_gone_after_the_update_is_nothing(self):
        r = self.after(reading("init systemd\nstate running", kernel=RUN), reading("init systemd\nstate degraded\nfailed x", kernel=RUN))
        self.assertEqual(r["after:failed-units"].v, "PASS")

    def test_bsd_down_newsyslog(self):
        c = checks(boot="B-bsd", mounts="M-bsd", kernel="K-fbsd")
        b = reading("down newsyslog\nok 12 services checked", kernel="k 15.1-RELEASE\nr 15.1-RELEASE", mounts="format mount-p\nzroot/ROOT/default / zfs rw 0 0")
        r = J.judge_after(b, b, c)
        self.assertEqual(r["after:boot-state"].v, "LIMIT")
        self.assertEqual(r["after:boot-state"].standing, ["down newsyslog"])
        self.assertEqual(r["after:failed-units"].v, "SKIP")
        a = reading("down newsyslog\ndown sshd\nok 12 services checked", kernel="k 15.1-RELEASE\nr 15.1-RELEASE", mounts="format mount-p\nzroot/ROOT/default / zfs rw 0 0")
        r = J.judge_after(b, a, c)
        self.assertEqual((r["after:boot-state"].v, r["after:boot-state"].new), ("FAIL", ["down sshd"]))

    def test_illumos_smf_lines(self):
        c = checks(boot="B-ill", mounts="M-ill", kernel="K-ill")
        body = "down lrc:/etc/rc2_d/S20sysetup\ndown svc:/network/cloud-init:default\nok 90 services checked"
        s = sec(("cmd boot-state", body), ("cmd fstab-check", "ok 0"), ("cmd mounts", "format vfstab-p\nrpool/ROOT/o - / zfs - no rw"),
                ("cmd kernel", "see beadm-after"), ("cmd beadm-after", "a;u;NR;/;1;static;200"))
        r = J.judge_after(s, s, c)
        self.assertEqual(r["after:boot-state"].v, "LIMIT")
        self.assertEqual(len(r["after:boot-state"].standing), 2)

    def test_artix_s6(self):
        s = reading("init s6-rc\nstate degraded\nfailed dbus-log\nfailed dbus-srv\nfailed elogind", kernel=RUN)
        r = J.judge_after(s, s, checks())
        self.assertEqual(r["after:failed-units"].v, "LIMIT")
        self.assertEqual(r["after:failed-units"].standing, ["failed dbus-log", "failed dbus-srv", "failed elogind"])

    def test_openbsd_and_alpinebsd_formats(self):
        c = checks(boot="B-obsd", mounts="M-bsd", kernel="K-obsd")
        s = reading("down sndiod\nok 5 daemons checked", mounts="format mount\n/dev/sd0a on / type ffs (local)", kernel="match yes")
        self.assertEqual(J.judge_after(s, s, c)["after:boot-state"].standing, ["down sndiod"])
        c = checks(boot="B-alpbsd", mounts="M-bsd", kernel="K-alpbsd")
        s = reading("down dropbear-log\nok 7 services checked", mounts="format mount\n/dev/dk1 on / type ffs (local)", kernel="match yes")
        self.assertEqual(J.judge_after(s, s, c)["after:boot-state"].standing, ["down dropbear-log"])

    def test_a_check_that_printed_nothing_is_no_all_clear(self):
        bsd = checks(boot="B-bsd", mounts="M-bsd", kernel="K-fbsd")
        good = reading("down newsyslog\nok 12 services checked", kernel="k 15.1\nr 15.1", mounts="format mount-p\nzroot/ROOT/default / zfs rw 0 0")
        for boot, c in (("B-bsd", bsd), ("B-obsd", checks(boot="B-obsd", mounts="M-bsd", kernel="K-obsd")), ("B-ill", checks(boot="B-ill", mounts="M-ill", kernel="K-ill"))):
            s = reading("", kernel="match yes", mounts="format mount\n/dev/sd0a on / type ffs (local)")
            self.assertEqual(J.after_lines(s, c)["after:boot-state"], [J.NO_SERVICE_LIST], boot)
        lin = checks()
        self.assertIn(J.NO_STATE, J.after_lines(reading("init openrc", kernel=RUN), lin)["after:boot-state"])
        self.assertEqual(J.after_lines(reading(kernel=RUN, mounts="format mountinfo"), lin)["after:shadow"], [J.NO_MOUNTS])
        self.assertEqual(J.after_lines(reading(kernel=RUN, fstab=""), lin)["after:fstab"], [J.NO_FSTAB_OK])

    def test_an_error_line_says_it_all(self):
        s = reading("error service -e listed nothing", kernel="k 15\nr 15", mounts="format mount-p\nz / zfs rw 0 0")
        self.assertEqual(J.after_lines(s, checks(boot="B-bsd", mounts="M-bsd", kernel="K-fbsd"))["after:boot-state"], ["error service -e listed nothing"])

    def test_the_merge_gate_stops_tools_that_went_silent(self):
        c = checks(boot="B-bsd", mounts="M-bsd", kernel="K-fbsd")
        b = reading("down newsyslog\nok 12 services checked", kernel="k 15\nr 15", mounts="format mount-p\nz / zfs rw 0 0")
        a = reading("", kernel="k 15\nr 15", mounts="format mount-p")
        ok, why, new = J.merge_gate(b, a, c)
        self.assertFalse(ok)
        self.assertTrue(any(J.NO_SERVICE_LIST in x for x in new))
        self.assertTrue(any(J.NO_MOUNTS in x for x in new))

    def test_b_none_is_skip(self):
        s = reading("", kernel="", mounts="format none", fstab="ok boot volume mounted")
        r = J.judge_after(s, s, checks(boot="B-none", mounts="M-haiku", kernel="K-haiku"))
        for n in ("after:boot-state", "after:failed-units", "after:shadow", "after:newest-kernel"):
            self.assertEqual(r[n].v, "SKIP", n)
        self.assertEqual(r["after:fstab"].v, "PASS")

    def test_a_probes_one_reading_is_all_standing(self):
        s = reading("init systemd\nstate degraded\nfailed x", kernel=RUN)
        r = J.judge_after(None, s, checks())
        self.assertEqual(r["after:failed-units"].v, "LIMIT")

    def test_fstab_unmounted(self):
        r = J.judge_after(reading(kernel=RUN), reading(kernel=RUN, fstab="ok 0\nfstab mount /srv not mounted"), checks())
        self.assertEqual(r["after:fstab"].v, "FAIL")

    def test_classed_new_line_is_limit(self):
        cl = {"after:failed-units": {"lines": ["failed x"], "why": "w"}}
        r = J.judge_after(reading(kernel=RUN), reading("failed x\ninit systemd", kernel=RUN), checks(), cl)
        self.assertEqual(r["after:failed-units"].v, "LIMIT")


class RealLogLines(unittest.TestCase):
    """known-lines.json against lines from real update.logs on the hosts, numbers as normalize leaves them."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "testvm/test2/guest/known-lines.json"), encoding="utf-8") as f:
            cls.known = json.load(f)

    def left(self, pm, raw):
        return J.sweep_lines({"update": raw + "\n"}, J.known_filter(self.known, pm), [])

    CHATTER = {
        "apt": ["Get:1 http://deb.debian.org/debian trixie/main amd64 bash amd64 5.2.37-2 [1,234 kB]", "Unpacking bash (5.2.37-2) over (5.2.37-1) ...",
                "Reading package lists... Done", "base-files is already the newest version (13.8).", "Found linux image: /boot/vmlinuz-6.12.57+deb13-amd64"],
        "dnf": ["[ 1/42] Upgrading NetworkManager-1:1.52.0 100% |   2.1 MiB/s |   3.4 MiB |  00m02s", "[ 3/9] Installing hunspell-en-AU-0:2.1 100% |   1.0 MiB/s |  10.0 KiB |  00m00s",
                "[12/42] Removing NetworkManager-1:1.50.0 100% |   0.0 B/s |   0.0 B |  00m00s", "Erasing          : kernel-6.12.0-55.el10_0.x86_64    4/9",
                " libmount   x86_64   2.40.2-10.el10   baseos   153 k", "replacing ethtool x86_64 2:6.15-1.fc44 anaconda 1.4 MiB", ">>> Finished %post scriptlet: audit-1:4.1.2-1.fc44.x86_64"],
        "pacman": ["upgrading brotli...", "( 3/12) upgrading brotli", "(10/12) installing zstd", "( 1/9) Applying kernel sysctl settings...",
                   "core/ca-certificates-mozilla 3.116-1 3.117-1 0.00 MiB 0.38 MiB"],
        "apk": ["( 1/9) Upgrading alpine-base (3.24.1-r0 -> 3.24.2-r0)", "(10/19) Installing openssl (3.5.0-r0)", "OK: 123.4 MiB in 42 packages"],
        "zypper": ["( 1/8) Installing: firewalld-2.3.1-1.1.noarch [.."],
        "xbps": ["gzip-1.14_1.x86_64-musl.xbps: [84KB 100%] 1234KB/s ETA: 00m00s", "gzip-1.14_1: verifying RSA signature...", "gzip-1.14_1: updated successfully."],
        "freebsd": ["[ 7/120] Extracting FreeBSD-acct-15.1p1: .......... done", "[3/9] Upgrading curl-8.15.0...", "Fetching meta.conf: . done", "/rescue/zdb"],
        "dragonfly": ["[1/3] Deinstalling bind-tools-9.20.1...", "in-guest: base system: make buildworld (12:01:02)"],
        "moss": ["Installed systemd-libs", "ngtcp2 1.15.0-1 -> 1.16.0-1"],
        # gentoo's (G1's vm-update --try, docs/design/gentoo.md)
        "portage": [">>> Emerging binary (2 of 9) net-misc/curl-8.17.0-r1::gentoo", ">>> Installing (2 of 9) net-misc/curl-8.17.0-r1::gentoo",
                    ">>> Jobs: 2 of 9 complete, 1 running                Load avg: 3.05, 2.10, 1.08",
                    "[binary   U  ] net-misc/curl-8.17.0-r1-1 [8.16.0] USE=\"http2* http3* quic*\"", "[ebuild     U  ] sys-boot/grub-2.16-r1 [2.14-r5]",
                    "Calculating dependencies  ... done!", "Packages installed:   312", "dev-build/meson-format-array",
                    " * IMPORTANT: 23 news items need reading for repository 'gentoo'.",
                    "--2026-11-12 11:52:01--  https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64/sys-devel/gettext/gettext-0.26-1.gpkg.tar",
                    "  3000K .......... .......... .......... .......... .......... 52% 1.21M 2s",
                    "  4050K .......... ..........100% 8.43M=0.3s",
                    "!!! The following binary packages have been ignored due to non matching USE:",
                    "    =net-libs/nghttp2-1.70.0 -systemd xml", "      Using --binpkg-respect-use=y will silence this warning."],
        "nix": ["copying path '/nix/store/abc-foo-1.0' from 'https://cache.nixos.org'...", "building '/nix/store/abc-foo.drv'..."],
        "netbsd": ["Removed obsolete file /usr/lib/libunbound.so.8.1"],
        "openbsd": ["Installing patch 002_aesgmac", "Get/Verify syspatch78-002_aesgmac.tgz"],
        "haiku": ["100% brotli-1.1.0-3-x86_64.hpkg [234.1 KiB]", "upgrade package brotli-1.1.0-2 to 1.1.0-3 from repository HaikuPorts"],
        # kinoite44's (B4's --try and its staged upgrade to :45, docs/design/bazzite.md)
        "rpm-ostree": ["Pulling manifest: ostree-unverified-registry:quay.io/fedora/fedora-kinoite:45",
                       "Importing: ostree-unverified-registry:quay.io/fedora/fedora-kinoite:45 (digest: sha256:"
                       "6942ebd1efac34b3e09b44406313048baf481e380de74f0c0d19d25d3a028826)",
                       "ostree chunk layers already present: 9", "ostree chunk layers needed: 56 (2.7 GB)",
                       "[1/56] Fetching ostree chunk a3c0f99e1253fe268a6 (121.1 MB)...done", "Staging deployment...done",
                       'Run "systemctl reboot" to start a reboot', "No upgrade available."],
    }

    def test_each_package_managers_chatter_goes(self):
        for pm, lines in self.CHATTER.items():
            for x in lines:
                self.assertEqual(self.left(pm, x), [], (pm, x))

    REAL_ERRORS = {
        "nix": ["error: builder for '/nix/store/abc-foo.drv' failed with exit code 1", "error: Cannot build '/nix/store/abc-foo.drv'.", "building '/nix/store/abc.drv' failed"],
        "apt": ["The following packages have unmet dependencies:", "Reading package lists... Error!"],
        "dragonfly": ["in-guest: base system: make buildworld failed"],
        "freebsd": ["Fetching foo-1.0.pkg: size mismatch, cannot continue"],
        "pacman": ["error: failed to commit transaction (conflicting files)"],
        "dnf": ["Error: Transaction test error:"],
        "portage": ["!!! Fetch failed for sys-devel/gettext-0.26-1, continuing...", ">>> Failed to emerge sys-boot/grub-2.16-r1",
                    " * ERROR: sys-boot/grub-2.16-r1::gentoo failed (compile phase):",
                    ">>> Jobs: 1 of 9 complete, 1 failed                Load avg: 3.05, 2.10, 1.08",
                    " * IMPORTANT: config file '/etc/default/grub' needs updating."],
        "zypper": ["Retrieving: foo-1.0.rpm [Error: not found]"],
        "rpm-ostree": ["error: Creating importer: failed to invoke method OpenImage: reading manifest 44 in quay.io/fedora/fedora-kinoite: manifest unknown",
                       "[3/56] Fetching ostree chunk 7ef9932682b330f4496 (70.1 MB)...failed",
                       "in-guest: step: ostree: deployment 7afcf66486b5600eac1bb112d6c054f57b4972189465e60d026c15f6551a4b71 staged, "
                       "in place of 10e6a37fe91449ab0256dc707f50023505f76d29b6d34844b63843d0950f7de6"],
    }

    def test_a_real_error_is_never_dropped(self):
        for pm, lines in self.REAL_ERRORS.items():
            for x in lines:
                self.assertEqual(len(self.left(pm, x)), 1, (pm, x))

    def test_a_wrapped_package_list_goes_with_its_header(self):
        raw = "The following packages will be upgraded:\n  base-files bash\n  busybox dpkg\n1 upgraded, 0 newly installed\n  indented after the list\n"
        self.assertEqual([x["line"] for x in self.left("apt", raw)], ["indented after the list"])

    def test_an_unmet_list_stays(self):
        raw = "The following packages have unmet dependencies:\n libfoo : Depends: bar but it is not installable\n"
        self.assertEqual(len(self.left("apt", raw)), 2)

    def test_rpm_ostrees_package_lists_go(self):
        raw = ("Upgraded:\n  7zip 26.04-1.fc44 -> 26.04-1.fc45\n  NetworkManager 1:1.56.1-2.fc44 -> 1:1.58.1-3.fc45\n"
               "Removed:\n  foo-1.0-1.fc44.x86_64\nAdded:\n  bar-2.0-1.fc45.x86_64\n")
        self.assertEqual(self.left("rpm-ostree", raw), [])

    def test_pkgs_list_too(self):
        raw = "Installed packages to be UPGRADED:\n\tcurl: 8.14 -> 8.15 [FreeBSD]\n\tzstd: 1.5.6 -> 1.5.7 [FreeBSD]\n"
        self.assertEqual(self.left("freebsd", raw), [])

    def test_the_run_folders_boot_line_goes(self):
        self.assertEqual(self.left("apt", "12:00:01 boot: SSH_PORT=40123 RAM_DIR=/mnt/bko-ram/vm-update.g/.vm-update-run.xyz /home/m/c/ab12/testvm/vm-run --ram --keep g.qcow2"), [])


class NewestKernel(unittest.TestCase):
    def k(self, rule, body):
        return J.newest_kernel_lines(body.splitlines(), rule, sec(("cmd beadm-after", body)))

    def test_gentoo_is_debians_rule(self):
        self.assertEqual(self.k("K-gentoo", "running 6.18.63-gentoo-dist-bin\ninstalled 6.18.63-gentoo-dist-bin"), [])
        self.assertEqual(self.k("K-gentoo", "running 6.18.63-gentoo-dist-bin\ninstalled 6.18.63-gentoo-dist-bin\ninstalled 6.18.64-gentoo-dist-bin"),
                         [J.NOT_NEWEST])
        self.assertEqual(self.k("K-gentoo", "running 6.18.63-gentoo-dist-bin\ninstalled 6.18.64-gentoo-dist-bin"), [J.NO_MODULES])

    def test_version_sort(self):
        self.assertEqual(self.k("K-deb", RUN), [])
        self.assertEqual(self.k("K-deb", "running 6.1.0-9-amd64\ninstalled 6.1.0-54-amd64\ninstalled 6.1.0-9-amd64"), [J.NOT_NEWEST])
        self.assertEqual(self.k("K-fed", "running 7.0.4\ninstalled 7.0.4\ninstalled 6.17.1"), [])

    def test_ostree_running_against_installed_says_nothing(self):
        # /usr/lib/modules is the booted deployment's alone: the rule is which deployment booted
        body = "running 7.3.3-200.fc44.x86_64\ninstalled 7.3.3-200.fc44.x86_64\nbooted ab12.0\ndefault ab12.0"
        self.assertEqual(self.k("K-ostree", body), [])
        self.assertEqual(self.k("K-ostree", body.replace("booted ab12.0", "booted cd34.1")), [J.OSTREE_NOT_DEFAULT])
        self.assertEqual(self.k("K-ostree", "running 7.3.3\ninstalled 7.3.3\nbooted ab12.0"),
                         ["kernel unverified: rpm-ostree status gave no booted or default deployment"])
        self.assertEqual(self.k("K-ostree", "running 7.3.3\ninstalled 7.2.9\nbooted ab12.0\ndefault ab12.0"), [J.NO_MODULES])

    def test_ostree_same_commit_pair_tells_by_serial(self):
        # kinoite44's fresh pair: one commit, two serials; booting .1 when .0 is the default is a finding
        self.assertEqual(self.k("K-ostree", "running 7.3.3\ninstalled 7.3.3\nbooted ab12.1\ndefault ab12.0"),
                         [J.OSTREE_NOT_DEFAULT])

    def test_ostree_the_staged_deployment_must_boot(self):
        steps = ["ostree: deployment ef56 staged, in place of ab12"]
        run = "running 7.3.3\ninstalled 7.3.3\n"
        k = lambda body: J.newest_kernel_lines((run + body).splitlines(), "K-ostree", None, steps)
        self.assertEqual(k("booted ef56.0\ndefault ef56.0"), [])
        # ostree-finalize-staged never wrote it: the old one is still the default and boots
        self.assertEqual(k("booted ab12.1\ndefault ab12.1"), [J.OSTREE_NOT_STAGED])
        # it was written, but the rollback booted
        self.assertEqual(k("booted ab12.1\ndefault ef56.0"), [J.OSTREE_NOT_DEFAULT, J.OSTREE_NOT_STAGED])
        # other steps say nothing about the deployment
        self.assertEqual(J.newest_kernel_lines((run + "booted ab12.1\ndefault ab12.1").splitlines(), "K-ostree", None,
                                               ["purging casper"]), [])

    def test_ostree_grubenv_keys(self):
        body = "running 7.3.3\ninstalled 7.3.3\nbooted ab12.0\ndefault ab12.0\n"
        self.assertEqual(self.k("K-ostree", body + "grubenv-key default"), [J.GRUBENV_KEY["default"]])
        self.assertEqual(self.k("K-ostree", body + "grubenv-key next_entry\ngrubenv-key default"),
                         [J.GRUBENV_KEY["default"], J.GRUBENV_KEY["next_entry"]])

    def test_ostree_after_an_update_where_the_old_deployment_booted(self):
        # The round B7's K-fed passed: the pre-update reading is clean, the update staged ef56, and the
        # reboot came up in the old deployment.  The line is new, so the merge gate blocks it.
        c = checks(source="S-none", loader="L-bls", kernel="K-ostree")
        before = reading("init systemd\nstate running", kernel="running 7.3.3\ninstalled 7.3.3\nbooted ab12.1\ndefault ab12.1",
                         mounts=MI_OK)
        steps = ["ostree: deployment ef56 staged, in place of ab12"]
        r = J.judge_after(before, before, c, steps=steps)
        self.assertEqual(r["after:newest-kernel"].v, "FAIL")
        self.assertEqual(r["after:newest-kernel"].new, [J.OSTREE_NOT_STAGED])
        ok, why, new = J.merge_gate(before, before, c, steps=steps)
        self.assertFalse(ok)
        self.assertEqual(new, ["after:newest-kernel: " + J.OSTREE_NOT_STAGED])
        # a probe (one reading) has no update to judge against
        self.assertEqual(J.judge_after(None, before, c)["after:newest-kernel"].v, "PASS")
        self.assertTrue(J.merge_gate(before, before, c)[0])

    def test_match_forms(self):
        for rule in ("K-pac", "K-apk", "K-nix", "K-obsd", "K-nbsd", "K-dfly", "K-alpbsd"):
            self.assertEqual(self.k(rule, "match yes"), [], rule)
            self.assertEqual(self.k(rule, "match no"), [J.NOT_NEWEST], rule)

    def test_freebsd(self):
        self.assertEqual(self.k("K-fbsd", "k 15.1-RELEASE-p3\nr 15.1-RELEASE-p3"), [])
        self.assertEqual(self.k("K-fbsd", "k 15.1-RELEASE-p4\nr 15.1-RELEASE-p3"), [J.NOT_NEWEST])

    def test_illumos(self):
        self.assertEqual(self.k("K-ill", "a;u;;;1;static;100\nb;u;NR;/;1;static;200"), [])
        self.assertEqual(self.k("K-ill", "a;u;NR;/;1;static;100\nb;u;;;1;static;200"), [J.NOT_NEWEST])

    def test_unverified_is_a_line_not_a_pass(self):
        self.assertEqual(self.k("K-nbsd", "unverified can't read /netbsd"), ["kernel unverified: can't read /netbsd"])

    def test_the_version_doesnt_decide_the_line(self):
        a = J.newest_kernel_lines("running 6.1.0-9\ninstalled 6.1.0-54\ninstalled 6.1.0-9".splitlines(), "K-deb")
        b = J.newest_kernel_lines("running 6.1.0-54\ninstalled 6.1.0-55\ninstalled 6.1.0-54".splitlines(), "K-deb")
        self.assertEqual(a, b)   # standing is by line


class Shadow(unittest.TestCase):
    def test_the_corpus_has_one_hit(self):
        hits = {}
        for g in sorted(os.listdir(CORPUS)):
            p = os.path.join(CORPUS, g, "src.json")
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            m = [(x.get("target"), x.get("source"), x.get("fstype")) for x in (d.get("storage") or {}).get("mounts") or []]
            r = J.shadowed(m)
            if r:
                hits[g] = r
        self.assertEqual(list(hits), ["ghostbsd26-zfs"])
        self.assertEqual(hits["ghostbsd26-zfs"], ["/tmp is shadowed by a later mount on /tmp"])

    def test_mountinfo_text(self):
        self.assertEqual(J.shadowed(J.parse_mounts(MI_OK.splitlines())), [])
        bad = MI_OK + "30 24 8:2 / /boot/efi rw - vfat /dev/vda2 rw\n31 22 0:40 / /boot rw - zfs bpool/BOOT/ubuntu rw\n"
        hit = J.shadowed(J.parse_mounts(bad.splitlines()))
        self.assertIn("/boot/efi is shadowed by a later mount on /boot", hit)

    def test_an_automount_isnt_shadowed(self):
        mi = MI_OK + "40 22 0:41 / /boot/efi rw - autofs systemd-1 rw\n41 40 8:2 / /boot/efi rw - vfat /dev/vda2 rw\n"
        self.assertEqual(J.shadowed(J.parse_mounts(mi.splitlines())), [])

    def test_other_formats(self):
        self.assertEqual(J.parse_mounts("format mount-p\nzroot/x /tmp zfs rw 0 0".splitlines()), [("/tmp", "zroot/x", "zfs")])
        self.assertEqual(J.parse_mounts("format vfstab-p\nrpool/x - /tmp zfs - no rw".splitlines()), [("/tmp", "rpool/x", "zfs")])
        self.assertEqual(J.parse_mounts("format mount\n/dev/sd0a on / type ffs (local)".splitlines()), [("/", "/dev/sd0a", "ffs")])
        self.assertEqual(J.parse_mounts("/dev/da0 on /x (ufs, local)".splitlines()), [("/x", "/dev/da0", "ufs")])


class MergeGate(unittest.TestCase):
    def test_new_line_blocks(self):
        ok, why, new = J.merge_gate(reading(kernel=RUN), reading("failed x", kernel=RUN), checks())
        self.assertFalse(ok)
        self.assertIn("failed x", why)
        self.assertEqual(new, ["after:failed-units: failed x"])

    def test_standing_doesnt_block(self):
        a = reading("failed x", kernel=RUN)
        self.assertTrue(J.merge_gate(a, a, checks())[0])

    def test_classed_new_line_doesnt_block(self):
        cl = {"after:failed-units": {"lines": ["failed x"], "why": "w"}}
        self.assertTrue(J.merge_gate(reading(kernel=RUN), reading("failed x", kernel=RUN), checks(), cl)[0])

    def test_a_failed_hook_blocks(self):
        a = reading(kernel=RUN)
        ok, why, _ = J.merge_gate(a, a, checks(), None, False, "cut off")
        self.assertFalse(ok)
        self.assertIn("a hook failed: cut off", why)


class NewGuest(unittest.TestCase):
    def ok(self):
        return sec(("cmd firmware", "uefi+secure-boot"), ("cmd restic-version", "restic 0.19"), ("cmd rustic-version", "rustic 0.11"),
                   ("cmd smoke", "ok restic\nok rustic"))

    def test_pass(self):
        r = J.new_guest_needs(self.ok(), {"fw": "uefi-sb"})
        self.assertTrue(all(r[n].v == "PASS" for n in J.NEW_NEEDS), r)

    def test_firmware_claim(self):
        r = J.new_guest_needs(self.ok(), {"fw": "bios"})
        self.assertEqual(r["new:firmware"].v, "FAIL")
        self.assertEqual(J.new_guest_needs(sec(("cmd firmware", "bios")), {"fw": "q35"})["new:firmware"].v, "PASS")

    def test_smoke_and_versions(self):
        s = sec(("cmd firmware", "bios"), ("cmd restic-version", "failed no /tmp/restic-bko"), ("cmd rustic-version", ""), ("cmd smoke", "ok restic\nfailed rustic"))
        r = J.new_guest_needs(s, {"fw": "bios"})
        self.assertEqual(r["new:restic"].v, "FAIL")
        self.assertEqual(r["new:rustic"].v, "FAIL")
        self.assertEqual((r["new:smoke"].v, r["new:smoke"].lines), ("FAIL", ["failed rustic"]))

    def test_probe(self):
        self.assertEqual(J.probe_need(sec(("cmd probe", "ok"))).v, "PASS")
        self.assertEqual(J.probe_need(sec(("cmd probe", "no root login on the console"))).lines, ["no root login on the console"])


class Normalizer(unittest.TestCase):
    SAMPLES = [
        "\x1b[0;32m  OK  \x1b[0m Started foo.service - something.",
        "\x1b]3008;start=abc;machineid=0123456789abcdef\x1b\\[   12.345] kernel: thing",
        "Nov 10 01:02:03 host sshd[123]: Accepted key",
        "2026-11-08T05:00:00.123Z error at 0xdeadbeef in 4f2a9c",
        "uuid 12345678-90ab-cdef-1234-567890abcdef and 42 bottles",
        "/mnt/bko-ram/vm-update.debian12/.vm-update-run.abc123/vm-run.x.qcow2 kept",
    ]

    def test_stable(self):
        for s in self.SAMPLES:
            n = J.normalize(s)
            self.assertEqual(J.normalize(n), n, s)

    def test_what_goes(self):
        self.assertEqual(J.normalize("[   12.345] x"), "x")
        self.assertEqual(J.normalize("\x1b]3008;x=1\x1b\\hello"), "hello")
        self.assertEqual(J.normalize("12:00:01 hello"), "TIME hello")
        self.assertEqual(J.normalize("id 12345678-90ab-cdef-1234-567890abcdef"), "id UUID")
        self.assertEqual(J.normalize("sum 0123abcd9f"), "sum HEX")
        self.assertEqual(J.normalize("got 12 of 345"), "got N of N")
        self.assertEqual(J.normalize("a /mnt/bko-ram/vm-update.x/.vm-update-run.q/f b"), "a RUN b")

    def test_two_runs_of_a_line_agree(self):
        a = "[    3.111] systemd[1]: run 2026-11-08 05:00:00 took 12ms abcdef0123"
        b = "[    9.222] systemd[1]: run 2026-11-09 06:11:00 took 7ms 0123456abc"
        self.assertEqual(J.normalize(a), J.normalize(b))


class LogSweep(unittest.TestCase):
    def test_no_baseline_means_every_line_is_new(self):
        r = J.sweep_lines({"update": "alpha 1\nbeta 2\n"}, [], None)
        self.assertEqual([x["line"] for x in r], ["alpha N", "beta N"])

    def test_seen_before_tag(self):
        r = J.sweep_lines({"update": "alpha 1\nbeta 2\n"}, [], None, older=["alpha 9\n"])
        self.assertEqual({x["line"]: x["seen_before"] for x in r}, {"alpha N": True, "beta N": False})

    def test_seen_before_is_for_the_reader_only(self):
        self.assertTrue(J.sweep_lines({"update": "alpha 1\n"}, [], None, older=["alpha 9\n"]))   # still new

    def test_baseline_and_known(self):
        r = J.sweep_lines({"update": "alpha 1\nbeta 2\nGet:1 http://x\n"}, J.known_filter({"all": [], "apt": ["^Get:N "]}, "apt"), ["alpha N"])
        self.assertEqual([x["line"] for x in r], ["beta N"])

    def test_nothing_is_added_without_accept(self):
        base = ["alpha N"]
        J.sweep_lines({"update": "beta 2\n"}, [], base)
        self.assertEqual(base, ["alpha N"])

    def test_known_lines_file(self):
        with open(os.path.join(REPO, "testvm/test2/guest/known-lines.json"), encoding="utf-8") as f:
            k = json.load(f)
        for pm, pats in k.items():
            for p in pats:
                if pm != "about":
                    __import__("re").compile(p)
        pats = J.known_filter(k, "apt")
        self.assertTrue(any(p.search(J.normalize("Get:1 http://deb.debian.org bookworm main")) for p in pats))
        for bad in ("E: Failed to fetch http://x", "error: failed to commit transaction", "[FAILED] Failed to start foo.service",
                    "dpkg: error processing package foo"):
            self.assertFalse(any(p.search(J.normalize(bad)) for p in pats), bad)


class Verdicts(unittest.TestCase):
    def test_limit_classed_and_new(self):
        d = {"after:boot-state": J.Need("LIMIT", None, ["down newsyslog"])}
        needs = {"after:boot-state": "LIMIT"}
        self.assertEqual(J.apply_limits(needs, d, {})["after:boot-state"], "LIMIT-NEW")
        cl = {"after:boot-state": {"lines": ["down newsyslog"], "why": "w"}}
        self.assertEqual(J.apply_limits(needs, d, cl)["after:boot-state"], "LIMIT")

    def test_a_different_standing_line_stays_new(self):
        d = {"after:boot-state": J.Need("LIMIT", None, ["down other"])}
        cl = {"after:boot-state": {"lines": ["down newsyslog"], "why": "w"}}
        self.assertEqual(J.apply_limits({"after:boot-state": "LIMIT"}, d, cl)["after:boot-state"], "LIMIT-NEW")

    def test_pass(self):
        self.assertEqual(J.verdict_of({"a": "PASS", "b": "LIMIT", "c": "SKIP"}), ("PASS", "pass", False))

    def test_to_class_only(self):
        self.assertEqual(J.verdict_of({"logs": "DIFF"}), ("FAIL", "fail", True))
        self.assertEqual(J.verdict_of({"logs": "DIFF", "after:x": "LIMIT-NEW"}), ("FAIL", "fail", True))
        self.assertEqual(J.verdict_of({"logs": "DIFF", "try": "FAIL"}), ("FAIL", "fail", False))

    def test_harness(self):
        self.assertEqual(J.verdict_of({"try": "FAIL"}, "no boot"), ("HARNESS", "harness", False))

    def test_feeds_only_fail_needs(self):
        self.assertEqual(J.only_fail_needs({"a": "FAIL", "b": "LIMIT-NEW", "c": "DIFF"}), {"a": "FAIL"})


class Flakes(unittest.TestCase):
    def flakes(self):
        with open(os.path.join(REPO, "testvm/test2/guest/flakes.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_tail_matches(self):
        self.assertIsNotNone(J.match_flake("debian12", ["E: Failed to fetch http://x  Connection failed"], self.flakes()))

    def test_only_the_tail(self):
        log = ["E: Failed to fetch http://x"] + ["ok"] * 100 + ["in-guest: dist-upgrade failed"]
        self.assertIsNone(J.match_flake("debian12", log[-8:], self.flakes()))

    def test_every_entry_says_why(self):
        for f in self.flakes():
            self.assertTrue(f.get("why") and f.get("ref") and f.get("pattern"))

    def test_no_real_failure_matches(self):
        for tail in ("dpkg: error processing package foo", "error: failed to commit transaction (conflicting files)",
                     "the update failed (exit status 1)", "kernel panic"):
            self.assertIsNone(J.match_flake("debian12", [tail], self.flakes()), tail)


class Grades(unittest.TestCase):
    PASS = {"verdict": "PASS", "needs": {"try": "PASS"}}

    def test_each(self):
        self.assertEqual(J.grade("debian12", self.PASS, True, True, True), "solid")
        self.assertEqual(J.grade("debian12", self.PASS, True, False, True), "usable")
        lim = {"verdict": "PASS", "needs": {"try": "PASS", "after:boot-state": "LIMIT"}}
        self.assertEqual(J.grade("debian12", lim, True, True, True), "usable")
        self.assertEqual(J.grade("debian12", {"verdict": "FAIL", "needs": {"try": "FAIL"}}, True, True, True), "unusable")
        self.assertEqual(J.grade("manjaro", self.PASS, False, True, True), "not covered")
        self.assertEqual(J.grade("redox", self.PASS, False, True, True), "offline-only")
        self.assertEqual(J.grade("sculpt2610", self.PASS, True, True, True), "offline-only")
        self.assertEqual(J.grade("debian12", None, True, True, False), "unverified")
        self.assertEqual(J.grade("debian12", self.PASS, True, True, False), "unverified")


if __name__ == "__main__":
    unittest.main()
