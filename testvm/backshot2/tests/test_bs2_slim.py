"""Unit tests for the slim step (bs2lib/slim.py, README "Slimming"): which sources it
slims (the small hosts' ceiling rule, the three full-fat ones), its script (syntax, the
guard that never lets it touch the layout, the boot chain, /etc or what content
recipes plant), reading its output back (slim.json), the slimmed room and seconds
(room.py's slim-sizes.tsv rows, sources.secs()) and the generation's recipe suffix.
Nothing here reaches a host or runs the script on this machine's root:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BS2 = os.path.dirname(HERE)
sys.path.insert(0, BS2)
sys.path.insert(0, HERE)
from bs2lib import room as RM, slim as SL, sources as S, space as SP  # noqa: E402

# The user's list of 2026-11-25 (every source over the small hosts' ceiling but the three
# full-fat ones), plus slackware15 and slackware15-uefi, which the same rule takes (D 16).
WANT = {"vanilla3", "anduinos2", "biglinux", "elementary8", "elementary8-luks", "endeavouros", "endeavouros-grub",
        "manjaro", "mint22", "mx25", "neon24", "omarchy", "pikaos4", "pikaos4-luks", "pop24", "solus49",
        "ubuntu26-uefi", "zorin18", "slackware15", "slackware15-uefi"}


class Which(unittest.TestCase):
    def test_the_ceiling_rule_less_the_full_fat(self):
        built = S.built()
        got = {s for s in built if SL.slimmed(s)}
        self.assertEqual(got, WANT & set(built))
        self.assertEqual(SL.FULL_FAT, ("ubuntu26-luks", "kinoite44", "garuda"))
        for s in SL.FULL_FAT:
            self.assertTrue(SL.full_over_small(s), s)   # over the ceiling, kept full by choice
            self.assertFalse(SL.slimmed(s), s)
        for s in ("alpine324", "alpine324-luks", "guix15", "debian13", "ubuntu24-luks", "nixos2605", "run:x"):
            self.assertFalse(SL.slimmed(s), s)
        # Every built source room.py keeps off a small host is slimmed or full fat.
        small = SL.SMALL_CEILING
        for s in built:
            d, m = RM.full_of(s)
            self.assertEqual(d > small[0] or m > small[1], SL.slimmed(s) or s in SL.FULL_FAT, s)

    def test_a_row_of_its_own(self):
        with tempfile.TemporaryDirectory() as t:
            p = f"{t}/ram-sizes.tsv"
            with open(p, "w") as f:
                f.write("tiny\t-\tchopin\t1\t9\t4\tx\nbig\t-\tchopin\t1\t10\t4\tx\nhungry\t-\tchopin\t1\t3\t11\tx\n")
            self.assertFalse(SL.slimmed("tiny", p))      # 9 fits the small hosts' 9.0
            self.assertTrue(SL.slimmed("big", p))
            self.assertTrue(SL.slimmed("hungry", p))     # its memory is over 10.1
            self.assertFalse(SL.slimmed("no-row", p))    # bs2's own 2 and 3

    def test_suffix(self):
        self.assertEqual(SL.suffix("vanilla3"), f"+slim{SL.RECIPE}")
        self.assertEqual(SL.suffix("garuda"), "")
        self.assertEqual(SL.suffix("debian13"), "")


def function(text, name):
    """The sh function NAME of TEXT, as written."""
    m = re.search(rf"^{name}\(\) \{{.*?^}}", text, re.S | re.M)
    assert m, name
    return m.group(0)


class Script(unittest.TestCase):
    def setUp(self):
        self.text = SL.script("vanilla3")

    def test_syntax_and_its_end(self):
        for shell in ("sh", "bash", "dash"):
            if shutil.which(shell):
                r = subprocess.run([shell, "-n"], input=self.text, text=True, capture_output=True)
                self.assertEqual(r.returncode, 0, (shell, r.stderr))
        self.assertIn(f"recipe {SL.RECIPE} vanilla3", self.text)
        lines = [l for l in self.text.splitlines() if l.strip()]
        self.assertEqual(lines[-1], "echo REBOOT")    # ht1.sh boots it again, from cold
        for k in ("@RECIPE@", "@SOURCE@", "@NEVER_CASE@", "@SNAP_ETC_UNITS@", "@DU_FS@"):
            self.assertNotIn(k, self.text)

    def test_no_step_names_a_path_it_must_never_touch(self):
        # Every path a step removes sits under /usr/share, /usr/lib/{firmware,locale},
        # /lib/firmware, /var/cache, /var/lib/{apt/lists,abroot/storage,systemd/coredump,
        # snapd/cache}, /var/log or /var/crash, or is a distro tool's.
        for name in ("pkgcache", "logs", "docs", "locale", "themes", "fonts", "firmware", "abroot_"):
            body = function(self.text, name)
            for p in re.findall(r"(?<![\w$}])/(?:etc|boot|efi|home|root|srv)\b[\w/.*-]*", body):
                self.fail(f"{name} names {p}")
        self.assertNotIn("mkfs", self.text)
        self.assertNotIn("tune2fs", self.text)
        self.assertNotIn("sfdisk", self.text)
        self.assertNotIn("update-grub", self.text)
        self.assertNotIn("update-initramfs", self.text)
        self.assertNotIn("/lib/modules", self.text.replace(function(self.text, "never"), ""))   # only in the guard

    @unittest.skipUnless(shutil.which("sh"), "no sh")
    def test_the_guard(self):
        """never() refuses everything under NEVER (by real path, a link's target too, and
        relative to the other ABRoot root R), and gone()/emptied() remove the rest."""
        with tempfile.TemporaryDirectory() as t:
            r = f"{os.path.realpath(t)}/other"
            for p in ("usr/share/doc/a", "usr/share/locale/de/LC_MESSAGES", "usr/share/locale/en_GB", "etc/x",
                      "usr/lib/firmware/amd-ucode", "usr/lib/firmware/iwlwifi", "srv/bs2/b"):
                os.makedirs(f"{r}/{p}")
            open(f"{r}/usr/share/locale/locale.alias", "w").close()
            open(f"{r}/usr/lib/firmware/regulatory.db", "w").close()
            os.symlink(f"{r}/etc/x", f"{r}/usr/share/doc/to-etc")
            prelude = "\n".join(l for l in self.text.splitlines()
                                if l.startswith(("say()", "err()", "RMOPT=")))
            funcs = "\n".join(function(self.text, n) for n in ("never", "gone", "emptied"))
            prog = f"""set -u
ERR=0
{prelude}
{funcs}
R={r}
LANGS="C C.* POSIX en en_* en@* locale.alias"
for p in /etc /etc/fstab /boot/grub/grub.cfg /efi/EFI /home/u /srv/bs2/x /root/.ssh /var/srv/bs2 \
    /var/lib/abroot/etc/vos-a /usr/lib/modules/6.1 /usr/share/grub/themes /usr/lib/firmware/amd-ucode; do
    never "$R$p" && echo "never $p" || echo "free $p"
done
never "$R/usr/share/doc/a" && echo "never doc" || echo "free doc"
gone "$R/srv/bs2" "$R/usr/share/doc/to-etc" "$R/usr/share/doc/a"
emptied "$R/usr/share/locale" $LANGS
emptied "$R/usr/lib/firmware" 'amd-ucode*' 'intel-ucode*' 'regulatory.db*'
emptied "$R/etc"
echo "ERR=$ERR"
"""
            out = subprocess.run(["sh", "-c", prog], text=True, capture_output=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            o = out.stdout
            for p in ("/etc", "/etc/fstab", "/boot/grub/grub.cfg", "/efi/EFI", "/home/u", "/srv/bs2/x", "/root/.ssh",
                      "/var/srv/bs2", "/var/lib/abroot/etc/vos-a", "/usr/lib/modules/6.1", "/usr/share/grub/themes",
                      "/usr/lib/firmware/amd-ucode"):
                self.assertIn(f"never {p}\n", o)
            self.assertIn("free doc\n", o)
            self.assertIn("SLIM refused", o)
            self.assertTrue(os.path.isdir(f"{r}/srv/bs2/b"))           # content's: refused
            self.assertTrue(os.path.islink(f"{r}/usr/share/doc/to-etc"))  # its target is /etc's: refused
            self.assertFalse(os.path.exists(f"{r}/usr/share/doc/a"))
            self.assertEqual(sorted(os.listdir(f"{r}/usr/share/locale")), ["en_GB", "locale.alias"])
            self.assertEqual(sorted(os.listdir(f"{r}/usr/lib/firmware")), ["amd-ucode", "regulatory.db"])
            self.assertTrue(os.path.isdir(f"{r}/etc/x"))
            self.assertIn("ERR=0", o)

    @unittest.skipUnless(shutil.which("sh"), "no sh")
    def test_used_counts_nilfs2_by_du(self):
        """used() sums df's used per /dev device once, but a DU_FS filesystem's (nilfs2's)
        is du -sxk of its first mount: its df counts what its checkpoints still hold."""
        self.assertEqual(list(SL.DU_FS), ["nilfs2"])
        self.assertIn("-t nilfs2 ", function(self.text, "dufs"))
        with tempfile.TemporaryDirectory() as t:
            stubs = {
                "findmnt": 'case "$*" in *nilfs2*) [ -n "${NILFS:-}" ] && printf \'%s\\n\' '
                           '"/dev/vda3 nilfs2 /" "/dev/vda3[/var/x] nilfs2 /var/y" "/dev/vda3 nilfs2 /snap\\x20cp" '
                           '"/dev/vdb1 nilfs2 /data\\x20two" ;; esac; exit 0',
                "df": "echo 'Filesystem 1024-blocks Used Available Capacity Mounted on'; "
                      "echo '/dev/vda3 9000000 8296000 700000 93% /'; echo '/dev/vda2 1000000 100000 900000 10% /boot'; "
                      "echo '/dev/vda3 9000000 8296000 700000 93% /var/y'; echo 'tmpfs 100 50 50 50% /run'; "
                      "echo '/dev/vdb1 5000 4000 1000 80% /data two'",
                "du": 'eval "last=\\${$#}"; echo "$last" >> "$DULOG"; case $last in /) n=4700000 ;; "/data two") n=7 ;; '
                      '*) n=999999 ;; esac; printf \'%s\\t%s\\n%s\\ttotal\\n\' $n "$last" $n',
            }
            for n, body in stubs.items():
                with open(f"{t}/{n}", "w") as f:
                    f.write(f"#!/bin/sh\n{body}\n")
                os.chmod(f"{t}/{n}", 0o755)
            funcs = "\n".join(l for l in self.text.splitlines() if l.startswith("kib()"))
            funcs += "\n" + "\n".join(function(self.text, n) for n in ("dufs", "used"))
            prog = f"""set -u
{funcs}
echo "used $(used)"
dufs | while read -r ud uf ut; do printf 'usedby du %s %s %s\\n' "$ud" "$uf" "$ut"; done
"""
            env = dict(os.environ, PATH=f"{t}:{os.environ['PATH']}", DULOG=f"{t}/du.log", NILFS="1")
            out = subprocess.run(["sh", "-c", prog], text=True, capture_output=True, env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.splitlines(), ["used 4800007", "usedby du /dev/vda3 nilfs2 /",
                                                       "usedby du /dev/vdb1 nilfs2 /data two"])
            with open(f"{t}/du.log") as f:
                self.assertEqual(f.read().splitlines(), ["/", "/data two"])   # each device once, its first mount
            # No nilfs2: df's used, each /dev device once, as before.
            out = subprocess.run(["sh", "-c", prog], text=True, capture_output=True, env=dict(env, NILFS=""))
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.splitlines(), ["used 8400000"])


OUT = """PASS source boots
SLIM note configured LANG en_US.UTF-8: its locales kept
SLIM recipe {r} anduinos2
SLIM state before running
SLIM failed before
SLIM used before 4153788
SLIM step pkgcache / 172252 apt-get clean; /var/lib/apt/lists/*
SLIM step logs / 60 journalctl --rotate --vacuum-time=1s
SLIM step flatpak / 0 nothing to do
SLIM step locale / 418160 /usr/share/locale/*
SLIM step firmware /run/bko-slim/other 1000 /usr/lib/firmware/*
SLIM refused /usr/share/doc/x
SLIM used after 4065200
REBOOT
"""


class Output(unittest.TestCase):
    def test_parse_and_record(self):
        r = SL.record(OUT.format(r=SL.RECIPE), "running\n", "anduinos2")
        self.assertEqual(r["recipe"], SL.RECIPE)
        self.assertEqual(r["used_before"], 4153788 * 1024)
        self.assertEqual(r["used_after"], 4065200 * 1024)
        self.assertEqual([s["step"] for s in r["steps"]], ["pkgcache", "logs", "flatpak", "locale", "firmware"])
        self.assertEqual(r["steps"][-1]["root"], "/run/bko-slim/other")
        self.assertEqual(r["freed"], (172252 + 60 + 418160 + 1000) * 1024)
        self.assertEqual(r["state_after"], "running")
        self.assertEqual(r["refused"], ["/usr/share/doc/x"])
        self.assertEqual(r["never"], list(SL.NEVER))
        self.assertIn("grub.cfg", r["old_kernels"])

    def test_used_by_du_is_recorded(self):
        r = SL.record(OUT.format(r=SL.RECIPE), "running\n", "anduinos2")
        self.assertEqual(r["used_du"], [])
        self.assertFalse([n for n in r["notes"] if n.startswith("used:")])
        text = OUT.format(r=SL.RECIPE).replace("SLIM used before", "SLIM usedby du /dev/vda3 nilfs2 /\n"
                                               "SLIM usedby du /dev/vdb1 nilfs2 /data two\nSLIM used before")
        r = SL.record(text, "running\n", "anduinos2")
        self.assertEqual(r["used_du"], [{"device": "/dev/vda3", "fstype": "nilfs2", "mount": "/"},
                                        {"device": "/dev/vdb1", "fstype": "nilfs2", "mount": "/data two"}])
        self.assertEqual(r["used_before"], 4153788 * 1024)
        notes = [n for n in r["notes"] if n.startswith("used:")]
        self.assertEqual(len(notes), 2)
        self.assertTrue(notes[0].startswith("used: nilfs2 /dev/vda3 (/) measured by du -sxk"), notes[0])
        self.assertIn("checkpoints", notes[0])

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, "not running"):
            SL.record(OUT.format(r=SL.RECIPE), "degraded\n", "anduinos2")
        with self.assertRaisesRegex(ValueError, "recipe"):
            SL.record(OUT.format(r=SL.RECIPE + 1), "running\n", "anduinos2")
        with self.assertRaisesRegex(ValueError, "recipe"):
            SL.record(OUT.format(r=SL.RECIPE), "running\n", "biglinux")
        with self.assertRaisesRegex(ValueError, "failed"):
            SL.record(OUT.format(r=SL.RECIPE) + "SLIM error snap: remove snapd\n", "running\n", "anduinos2")
        # A source degraded before it was slimmed is held to nothing more.
        r = SL.record(OUT.format(r=SL.RECIPE).replace("before running", "before degraded"), "degraded\n", "anduinos2")
        self.assertEqual(r["state_after"], "degraded")


ROWS = ("source\trecipe\td\tmem\tt1_s\tused_before_mib\tused_after_mib\tprep_mib\ttools_mib\tmeasured\n"
        f"vanilla3\t{SL.RECIPE}\t12\t4\t400\t20000\t9000\t2000\t2000\tx\n"
        f"anduinos2\t{SL.RECIPE + 1}\t5\t4\t200\t-\t-\t-\t-\tanother recipe\n"
        f"garuda\t{SL.RECIPE}\t5\t4\t200\t-\t-\t-\t-\tfull fat: never taken\n")


class Room(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.p = f"{self.t}/slim-sizes.tsv"
        with open(self.p, "w") as f:
            f.write(ROWS)
        self.patch = mock.patch.object(RM, "SLIM_SIZES", self.p)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.t)

    def test_the_slimmed_room_or_the_full(self):
        self.assertEqual(RM.of("vanilla3"), (12, 4))           # its measured, slimmed room
        self.assertEqual(RM.full_of("vanilla3"), (30, 4))      # HoltTest1's
        self.assertEqual(RM.of("anduinos2"), RM.full_of("anduinos2"))   # measured on another recipe
        self.assertEqual(RM.of("garuda"), (20, 4))             # full fat: ram-sizes.tsv's
        self.assertEqual(RM.of("mint22"), (17, 4))             # not measured yet: the full guest's
        self.assertEqual(RM.need("vanilla3"), (12, 4))
        # vanilla3 leaves the SSD lane: the big hosts' RAM disks hold it, the small ones don't.
        self.assertEqual(RM.scratch("vanilla3", (29.0, 23.2), True, 29.0), "ram")
        self.assertIsNone(RM.scratch("vanilla3", (9.0, 10.1), False, 29.0))

    def test_seconds(self):
        self.assertEqual(S.secs("vanilla3", 1), (400, "measured"))
        self.assertEqual(S.secs("vanilla3", 2), (400, "est."))
        self.assertEqual(S.secs("anduinos2", 1)[1], "est.")

    def test_the_repository_table_reads(self):
        with mock.patch.object(RM, "SLIM_SIZES", os.path.join(BS2, "slim-sizes.tsv")):
            rows = RM.slim_table()
            for s, row in rows.items():
                self.assertTrue(SL.slimmed(s), s)
                self.assertEqual(row["recipe"], SL.RECIPE, s)
                self.assertGreaterEqual(row["d"], 2, s)
                self.assertGreaterEqual(row["mem"], 4, s)
                self.assertTrue(row["measured"], s)

    def test_space_ratios_leave_the_slimmed_out(self):
        tsv = f"{self.t}/space-sizes.tsv"
        with open(tsv, "w") as f:
            f.write("source\ttools_mib\tprep_mib\tmeasured\ndebian12\t328\t511\tx\nvanilla3\t1\t1\tslimmed\n")
        with open(f"{self.t}/only.tsv", "w") as f:
            f.write("source\ttools_mib\tprep_mib\tmeasured\ndebian12\t328\t511\tx\n")
        self.assertEqual(SP.ratios(tsv), SP.ratios(f"{self.t}/only.tsv"))


class Generation(unittest.TestCase):
    def test_a_slimmed_source_names_its_recipe(self):
        from test_bs2_hosts import load_host
        H = load_host()
        with tempfile.TemporaryDirectory() as t:
            for s in ("vanilla3", "garuda"):
                os.makedirs(f"{t}/{s}")
                with open(f"{t}/{s}/image-state", "w") as f:
                    f.write('{"generation": "abc"}')
            with mock.patch.object(H, "VMS", t):
                self.assertEqual(H.generation("vanilla3"), f"gen:abc+slim{SL.RECIPE}")
                self.assertEqual(H.base_generation("vanilla3"), "gen:abc")
                self.assertEqual(H.generation("garuda"), "gen:abc")
                self.assertEqual(H.generation("run:x"), "run:x")


class OwnBoot(unittest.TestCase):
    """A case of a slimmed source that boots the source itself runs the slim step too."""

    def setUp(self):
        from test_bs2_hosts import load_host
        self.H = load_host()
        self.t = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("HT_SOURCE_PREP", None)
        os.environ.pop("BS2_REUSE_SOURCE", None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.t)

    def own(self, source, why):
        with mock.patch.object(self.H, "prep_reasons", lambda s, h, t=1: (None, None, why)), \
                mock.patch.object(self.H, "run_dir", lambda run: self.t), \
                mock.patch.object(self.H, "holt_bin", lambda: "/nonexistent"), \
                mock.patch("sys.stderr"):
            self.H.slim_own_boot(source, 1, "r1")
        return os.environ.get("HT_SOURCE_PREP")

    def test_stale_preparation(self):
        p = self.own("vanilla3", ["generation: prepared x, now y"])
        self.assertEqual(p, f"{self.t}/logs/slim-vanilla3.sh")
        with open(p) as f:
            self.assertEqual(f.read(), SL.script("vanilla3"))

    def test_current_preparation_or_not_slimmed(self):
        self.assertIsNone(self.own("vanilla3", []))
        self.assertIsNone(self.own("debian13", ["no preparation"]))
        self.assertIsNone(self.own("garuda", ["no preparation"]))

    def test_reuse_off(self):
        os.environ["BS2_REUSE_SOURCE"] = "0"
        self.assertIsNotNone(self.own("mint22", []))


if __name__ == "__main__":
    unittest.main()
