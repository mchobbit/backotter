"""Unit tests for what a tier 1 or 2 case reuses (bs2lib/reuse.py; README "What a case
reuses"): which cases may take the prepared backup, when a preparation is stale, the
rescuer state's key, bs2-host's "prepared", "rescuer-key" and apply's BS2_SHARE_BACKUP,
ht1-bs2.sh's QEMU args and NVRAM handling (bash, no VM), and bs2's switches.  Nothing
here boots anything:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BS2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(BS2)
HOST = os.path.join(BS2, "bs2-host")
sys.path.insert(0, BS2)
sys.path.insert(0, HERE)

from bs2lib import case as C, dumpx, reuse as RU  # noqa: E402
from test_bs2_hosts import B  # noqa: E402  (bs2, loaded by path)

ALPINE = dumpx.load(os.path.join(BS2, "..", "..", "holt", "tests", "corpus", "alpine324", "src.json"))


class TestBackupShareable(unittest.TestCase):
    def test_only_cases_that_back_up_the_tree_as_it_stands(self):
        plain = {"source": "alpine324", "edits": [{"op": "x"}], "load_options": ["--fit-largest", "10"],
                 "target": {"vda": {"size_bytes": 1}}}
        self.assertEqual(RU.backup_shareable(plain), (True, ""))
        # Edits, targets, options, the tier and the leftovers chain don't reach the backup.
        self.assertTrue(RU.backup_shareable(dict(plain, chain={"set": "leftovers"}, layer_seed={"kind": "md12"}))[0])
        self.assertTrue(RU.backup_shareable(dict(plain, snapshots={"count": 1, "restore": 0}))[0])
        ok, why = RU.backup_shareable(dict(plain, content={"recipe": "alpine-small-2", "seed": 1}))
        self.assertFalse(ok)
        self.assertIn("content", why)
        ok, why = RU.backup_shareable(dict(plain, snapshots={"count": 2, "restore": 0},
                                           load_options=["--snapshot", "@0"]))
        self.assertFalse(ok)
        self.assertIn("2 snapshots", why)


class TestSourceStale(unittest.TestCase):
    def fields(self, **kw):
        f = {"version": RU.PREP_VERSION, "source": "alpine324-uefi", "gen": "gen:abc", "holt": "h1",
             "drivers": {p: "s-" + p for p in RU.DRIVERS}, "options": ["--uefi", "--no-secure-boot"],
             "images": {"/v/alpine324-uefi.qcow2": "1:2:3"}, "disks": 1}
        f.update(kw)
        return f

    def test_the_same_serves(self):
        self.assertEqual(RU.source_stale(self.fields(), self.fields()), [])
        self.assertEqual(RU.source_stale(self.fields(), self.fields(), tier=2), [])

    def test_each_change_is_named(self):
        saved = self.fields()
        for k, v, word in (("gen", "gen:def", "generation"), ("holt", "h2", "bko-holt"),
                           ("options", ["--uefi"], "vm-run-options"), ("disks", 2, "disk count"),
                           ("images", {"/v/alpine324-uefi.qcow2": "1:2:4"}, "alpine324-uefi.qcow2 changed")):
            why = RU.source_stale(saved, self.fields(**{k: v}))
            self.assertEqual(len(why), 1, (k, why))
            self.assertIn(word, why[0])
        d = dict(saved["drivers"], **{"holttest1d/ht1.sh": "other"})
        self.assertEqual(RU.source_stale(saved, self.fields(drivers=d)), ["holttest1d/ht1.sh changed since the preparation"])

    def test_old_format_and_none(self):
        self.assertEqual(RU.source_stale(None, self.fields()), ["no preparation"])
        old = {k: v for k, v in self.fields().items() if k != "version"}
        self.assertTrue(any("format 1" in w for w in RU.source_stale(old, self.fields())))

    def test_tier2_needs_the_preparation_booted_as_it_boots(self):
        sb = self.fields(options=["--uefi"])
        self.assertEqual(RU.source_stale(sb, sb, tier=1), [])
        self.assertTrue(any("--no-secure-boot" in w for w in RU.source_stale(sb, sb, tier=2)))
        bios = self.fields(options=[])
        self.assertEqual(RU.source_stale(bios, bios, tier=2), [])

    def test_missing_files(self):
        with tempfile.TemporaryDirectory() as t:
            why = RU.source_stale(self.fields(), self.fields(), prep_dir=t)
            self.assertIn("the preparation has no dumps/src.json", why)
            self.assertIn("the preparation has no src-0.qcow2", why)


class TestBackupStale(unittest.TestCase):
    def test_rustic_and_files(self):
        saved = {"backup": {"snapshot_path": "/mnt/ht1-src-p-1", "rustic": "r1"}}
        self.assertEqual(RU.backup_stale(saved, "r1"), [])
        self.assertIn("rustic-bko", RU.backup_stale(saved, "r2")[0])
        self.assertEqual(RU.backup_stale({}, "r1")[0], "the preparation made no backup")
        with tempfile.TemporaryDirectory() as t:
            self.assertIn("the preparation has no backup/repo/config", RU.backup_stale(saved, "r1", t))


class TestVmOptions(unittest.TestCase):
    def test_as_ht1_reads_them(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(f"{t}/g")
            with open(f"{t}/g/vm-run-options", "w") as f:
                f.write("--uefi --disk g-1.qcow2 --no-secure-boot --disk /abs/x.qcow2\n--ignored\n")
            fw, imgs = RU.vm_options(t, "g")
            self.assertEqual(fw, ["--uefi", "--no-secure-boot"])
            self.assertEqual(imgs, [f"{t}/g/g.qcow2", f"{t}/g/g-1.qcow2", "/abs/x.qcow2"])
            self.assertEqual(RU.vm_options(t, "none"), ([], [f"{t}/none/none.qcow2"]))


class TestRescuerKey(unittest.TestCase):
    F = {"image": "/r/rescuer-uefi.qcow2", "image_stamp": "1:2:3", "image_sha": "abc", "rfw": ["--uefi", "--no-secure-boot",
         "--uefi-vars", "/mnt/bko-ram/x/vars.fd"], "memory": "3072", "qemu": "QEMU 10.0.13", "qemu_stamp": "4:5:6",
         "kernel": "6.12", "cpu": "cpu microcode 0x1", "extra": "-virtfs local,path=SHARE", "ovmf": {"/o/CODE": "7:8:9"},
         "drivers": {"vm-run": "d1"}}

    def test_the_cases_nvram_file_isnt_part_of_it(self):
        self.assertEqual(RU.normalize_rfw(["--uefi", "--uefi-vars", "/a", "--no-secure-boot"]),
                         ["--uefi", "--uefi-vars", "(the case's copy)", "--no-secure-boot"])
        other = dict(self.F, rfw=["--uefi", "--no-secure-boot", "--uefi-vars", "/mnt/bko-ram/y/vars.fd"])
        self.assertEqual(RU.rescuer_key(self.F), RU.rescuer_key(other))
        self.assertEqual(RU.rescuer_kind(self.F), RU.rescuer_kind(other))

    def test_anything_else_is_another_state(self):
        k = RU.key_hash(RU.rescuer_key(self.F))
        for field, v in (("image_stamp", "1:2:4"), ("image_sha", "abd"), ("memory", "4096"), ("qemu", "QEMU 10.1"),
                         ("qemu_stamp", "x"), ("kernel", "6.13"), ("cpu", "cpu microcode 0x2"), ("extra", "-device x"),
                         ("ovmf", {"/o/CODE": "x"}), ("drivers", {"vm-run": "d2"}), ("rfw", ["--uefi"])):
            self.assertNotEqual(RU.key_hash(RU.rescuer_key(dict(self.F, **{field: v}))), k, field)
        # Tier 1's Secure Boot rescuer and tier 2's are different kinds; a newer QEMU isn't.
        self.assertNotEqual(RU.rescuer_kind(dict(self.F, rfw=["--uefi"])), RU.rescuer_kind(self.F))
        self.assertEqual(RU.rescuer_kind(dict(self.F, qemu="QEMU 11")), RU.rescuer_kind(self.F))

    def test_bs2_host_rescuer_key(self):
        with tempfile.TemporaryDirectory() as t:
            img = f"{t}/rescuer.qcow2"
            with open(img, "w") as f:
                f.write("x")
            with open(img + ".sha256", "w") as f:
                f.write("feed  rescuer.qcow2\n")

            def key(*args):
                r = subprocess.run([sys.executable, HOST, "rescuer-key", "--image", img, *args], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                head, text = r.stdout.split("\n", 1)
                return head.split(), text
            h1 = key("--rfw=--uefi --uefi-vars /a/vars.fd", "--extra=-qmp unix:QMP ")[0][0]
            h2 = key("--rfw=--uefi --uefi-vars /b/vars.fd", "--extra=-qmp unix:QMP")[0][0]
            h3 = key("--rfw=--uefi --uefi-vars /b/vars.fd", "--extra=-qmp unix:QMP", "--memory", "4096")[0][0]
            self.assertEqual(h1, h2)
            self.assertNotEqual(h1, h3)
            self.assertRegex(h1, r"^[0-9a-f]{16}$")
            self.assertIn("image " + os.path.realpath(img), key("--rfw=")[1])
            self.assertIn("sha256-file feed", key("--rfw=")[1])


class PrepFixture(unittest.TestCase):
    """A preparation laid out as bs2-host source leaves it, in a temporary BS2_ROOT."""

    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t)
        self.vms, self.root = f"{self.t}/vms", f"{self.t}/root"
        os.makedirs(f"{self.vms}/alpine324")
        with open(f"{self.vms}/alpine324/alpine324.qcow2", "w") as f:
            f.write("image")
        with open(f"{self.vms}/alpine324/image-state", "w") as f:
            json.dump({"generation": "abc123"}, f)
        self.holt = f"{self.t}/bko-holt"
        with open(self.holt, "w") as f:
            f.write("holt 1")
        name = "p-20261113-120000-1"
        self.k = k = f"{self.root}/sources/alpine324/preps/{name}"
        for p in ("dumps/src.json", "logs/source-bootstate.txt", "logs/source-health.txt", "logs/source-lvmid.txt",
                  "src-0.qcow2", "backup/repo/config", "backup/repo-password", "src-meta.json", "bs2-snapshots.json"):
            os.makedirs(os.path.dirname(f"{k}/{p}"), exist_ok=True)
            with open(f"{k}/{p}", "w") as f:
                f.write("{}")
        saved = RU.fields_now(TESTVM, self.vms, "alpine324", "gen:abc123", RU.sha256_file(self.holt))
        saved.update(name=name, backup={"snapshot_path": f"/mnt/ht1-src-{name}", "rustic": "r1"})
        with open(f"{k}/prep.json", "w") as f:
            json.dump(saved, f)
        os.symlink(f"preps/{name}", f"{self.root}/sources/alpine324/prep")

    def prepared(self, *args, holt=None):
        env = dict(os.environ, BS2_ROOT=self.root, BS2_VMS=self.vms)
        r = subprocess.run([sys.executable, HOST, "prepared", "alpine324", "--holt", holt or self.holt, *args],
                           capture_output=True, text=True, env=env)
        out = {}
        for line in r.stdout.splitlines():
            n, _, v = line.partition("=")
            out[n] = subprocess.run(["sh", "-c", f'{line}; printf %s "${n}"'], capture_output=True, text=True).stdout
        return r.returncode, out, r.stderr


class TestBs2HostPrepared(PrepFixture):
    """bs2-host prepared against that preparation."""

    def test_fresh_serves_the_source_and_backup(self):
        rc, out, err = self.prepared("--tier", "1", "--rustic-sha", "r1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out["BS2_PREP_DIR"], os.path.realpath(self.k))
        self.assertEqual(out["BS2_PREP_SNAP"], "/mnt/ht1-src-p-20261113-120000-1")
        self.assertEqual(out["BS2_PREP_BACKUP_WHY"], "")

    def test_another_rustic_keeps_the_source_only(self):
        rc, out, _ = self.prepared("--rustic-sha", "r2")
        self.assertEqual(rc, 0)
        self.assertEqual(out["BS2_PREP_SNAP"], "")
        self.assertIn("rustic-bko", out["BS2_PREP_BACKUP_WHY"])

    def test_another_bko_holt_or_generation_serves_nothing(self):
        other = f"{self.t}/bko-holt-2"
        with open(other, "w") as f:
            f.write("holt 2")
        rc, out, _ = self.prepared("--rustic-sha", "r1", holt=other)
        self.assertEqual(rc, 1)
        self.assertIn("bko-holt", out["BS2_PREP_WHY"])
        self.assertNotIn("BS2_PREP_DIR", out)
        with open(f"{self.vms}/alpine324/image-state", "w") as f:
            json.dump({"generation": "def456"}, f)
        rc, out, _ = self.prepared("--rustic-sha", "r1")
        self.assertEqual(rc, 1)
        self.assertIn("generation", out["BS2_PREP_WHY"])

    def test_a_missing_file_serves_nothing(self):
        os.remove(f"{self.k}/logs/source-health.txt")
        rc, out, _ = self.prepared("--rustic-sha", "r1")
        self.assertEqual(rc, 1)
        self.assertIn("logs/source-health.txt", out["BS2_PREP_WHY"])

    def test_no_preparation(self):
        os.remove(f"{self.root}/sources/alpine324/prep")
        rc, out, _ = self.prepared()
        self.assertEqual(rc, 1)
        self.assertIn("no preparation", out["BS2_PREP_WHY"])


class TestApplyShareBackup(unittest.TestCase):
    def test_apply_says_whether_the_backup_is_shared(self):
        with tempfile.TemporaryDirectory() as t:
            src = f"{t}/src.json"
            with open(src, "w") as f:
                json.dump(ALPINE, f)

            def apply(case):
                p = f"{t}/c.case"
                C.write(p, dict({"source": "alpine324", "edits": [], "load_options": []}, **case))
                r = subprocess.run([sys.executable, HOST, "apply", p, src, f"{t}/out.json"], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                return [l for l in r.stdout.splitlines() if l.startswith("BS2_SHARE_BACKUP=")]
            self.assertEqual(apply({}), ["BS2_SHARE_BACKUP=1"])
            self.assertEqual(apply({"content": {"recipe": "alpine-small-2", "seed": 3}}), ["BS2_SHARE_BACKUP=0"])
            self.assertEqual(apply({"snapshots": {"count": 3, "restore": 1}, "load_options": ["--snapshot", "@1"]}),
                             ["BS2_SHARE_BACKUP=0"])


def bash(script, **env):
    """SCRIPT in bash with ht1-bs2.sh sourced (as ht1.sh sources it, for a case)."""
    e = dict(os.environ, BS2_CASE="/nonexistent.case", BS2_NAME="c1", BS2_HOST=HOST, **env)
    return subprocess.run(["bash", "-c", f'. "{BS2}/ht1-bs2.sh"\n{script}'], capture_output=True, text=True, env=e)


class TestHt1Bs2(unittest.TestCase):
    def test_syntax(self):
        for f in (f"{BS2}/ht1-bs2.sh", f"{TESTVM}/holttest1d/ht1.sh"):
            r = subprocess.run(["bash", "-n", f], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_extra_args_are_the_same_at_the_save_and_every_resume(self):
        r = bash('rfw=(--uefi --no-secure-boot --uefi-vars /r/vars.fd); bs2_rs_extra SHARE QMP; echo ==; '
                 'rfw=(); bs2_rs_extra /s/share /run/bs2/q.sock')
        self.assertEqual(r.returncode, 0, r.stderr)
        uefi, bios = r.stdout.split("==\n")
        u = uefi.split("\n")
        self.assertEqual(u[:4], ["-virtfs", "local,path=SHARE,mount_tag=bkoshare,security_model=none,readonly=on",
                                 "-device", "vmgenid,guid=auto"])
        ports = [x for x in u if x.startswith("pcie-root-port")]
        self.assertEqual(len(ports), 6)
        self.assertIn("id=bs2rp0,bus=pcie.0,chassis=40,addr=0x10", ports[0])
        self.assertIn("id=bs2rp5,bus=pcie.0,chassis=45,addr=0x15", ports[5])
        self.assertEqual(u[-3:], ["-qmp", "unix:QMP,server=on,wait=off", ""])
        self.assertNotIn("pcie-root-port", bios)
        self.assertIn("local,path=/s/share,", bios)

    def test_nvram(self):
        # Tier 2's NVRAM is rfw's (the restored boot keeps it); tier 1's UEFI rescuer gets its own copy.
        r = bash('R=/w; rfw=(--uefi --no-secure-boot --uefi-vars /w/vars.fd); bs2_rs_vars; bs2_rs_rfw /w/vars.fd | tr "\\n" " "; echo; '
                 'rfw=(--uefi); bs2_rs_vars; bs2_rs_rfw /w/rescuer-vars.fd | tr "\\n" " "; echo; '
                 'rfw=(); bs2_rs_rfw /x | wc -l')
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.split("\n")
        self.assertEqual(lines[:4], ["/w/vars.fd", "--uefi --no-secure-boot --uefi-vars /w/vars.fd ",
                                     "/w/rescuer-vars.fd", "--uefi --uefi-vars /w/rescuer-vars.fd "])
        self.assertEqual(lines[4].strip(), "0")   # a BIOS rescuer: no option at all, not an empty one

    def test_the_snapshot_load_restores(self):
        with tempfile.TemporaryDirectory() as t:
            r = bash(f'R={t}; SRC_MP=/mnt/ht1-src-c1; BS2_SNAPS=1; bs2_snapshot; echo /mnt/ht1-src-p-1 > {t}/bs2-snap-path; bs2_snapshot')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), ["latest:/mnt/ht1-src-c1", "latest:/mnt/ht1-src-p-1"])

    def test_prepare_mode_needs_no_case(self):
        r = subprocess.run(["bash", "-c", f'. "{BS2}/ht1-bs2.sh"; echo "GV=$GV CASE=[$BS2_CASE]"'], capture_output=True, text=True,
                           env=dict(os.environ, BS2_PREPARE="1", BS2_NAME="p-1", BS2_HOST=HOST))
        self.assertEqual(r.stdout.strip(), "GV=p-1 CASE=[]", r.stderr)
        r = subprocess.run(["bash", "-c", f'. "{BS2}/ht1-bs2.sh"'], capture_output=True, text=True,
                           env={k: v for k, v in os.environ.items() if not k.startswith("BS2_")})
        self.assertEqual(r.returncode, 2)


class TestReuseGlue(PrepFixture):
    """ht1-bs2.sh's source and backup hooks against that preparation, in bash, with
    qemu-img stood in for (it records the overlays it would make) and ht1.sh's say,
    ok and bad as lines."""

    def run_hooks(self, script, **env):
        shim = f"{self.t}/shim"
        os.makedirs(shim, exist_ok=True)
        with open(f"{shim}/qemu-img", "w") as f:
            f.write('#!/bin/sh\necho "qemu-img $*" >> "$SHIMLOG"; for a; do last=$a; done; : > "$last"\n')
        with open(f"{shim}/cp", "w") as f:   # (the Mac's cp has no --reflink)
            f.write('#!/bin/sh\nfor a; do shift; [ "$a" = --reflink=auto ] || set -- "$@" "$a"; done; exec /bin/cp "$@"\n')
        for x in ("qemu-img", "cp"):
            os.chmod(f"{shim}/{x}", 0o755)
        r_ = f"{self.t}/R"
        for d in ("dumps", "logs", "share"):
            os.makedirs(f"{r_}/{d}", exist_ok=True)
        with open(f"{r_}/share/bko-holt", "w") as f:
            f.write("holt 1")
        pre = (f'say() {{ echo "$*"; }}; ok() {{ say "PASS $*"; }}; bad() {{ say "FAIL $*"; }}\n'
               f'R={r_}; S=$R/share; G=alpine324; n=1; src=("$R/src-0.qcow2"); SRC_MP=/mnt/ht1-src-c1; HT_RUSTIC_SHA=r1\n')
        e = dict(BS2_ROOT=self.root, BS2_VMS=self.vms, SHIMLOG=f"{self.t}/shim.log",
                 PATH=f"{shim}:{os.environ['PATH']}", **env)
        return bash(pre + script, **e), r_

    def test_a_shareable_case_takes_the_source_and_the_backup(self):
        r, R = self.run_hooks("bs2_source_reuse && echo REUSED; BS2_SHARE_BACKUP=1; bs2_backup_reuse && echo BACKUP; bs2_snapshot")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.splitlines()
        self.assertIn("REUSED", out)
        self.assertIn("BACKUP", out)
        self.assertEqual(out[-1], "latest:/mnt/ht1-src-p-20261113-120000-1")
        with open(f"{self.t}/shim.log") as f:
            self.assertEqual(f.read().split(), ["qemu-img", "create", "-q", "-f", "qcow2", "-b",
                                                os.path.realpath(self.k) + "/src-0.qcow2", "-F", "qcow2", f"{R}/src-0.qcow2"])
        for f in ("dumps/src.json", "logs/source-health.txt", "logs/source-bootstate.txt", "logs/source-lvmid.txt",
                  "src-meta.json", "bs2-snapshots.json", "share/repo/config", "share/repo-password", "bs2-snap-path"):
            self.assertTrue(os.path.exists(f"{R}/{f}"), f)
        with open(f"{R}/logs/manifest.txt") as f:
            self.assertTrue(f.read().startswith("prepared " + os.path.realpath(self.k) + " prep.json "))

    def test_a_case_that_plants_backs_up_its_own(self):
        r, R = self.run_hooks("bs2_source_reuse && echo REUSED; BS2_SHARE_BACKUP=0; bs2_backup_reuse || echo OWN; bs2_snapshot")
        self.assertEqual(r.stdout.splitlines()[-2:], ["OWN", "latest:/mnt/ht1-src-c1"], r.stderr)
        self.assertFalse(os.path.exists(f"{R}/share/repo"))

    def test_switches_and_stale_fall_back_with_a_note(self):
        r, R = self.run_hooks("bs2_source_reuse || echo BOOT; BS2_SHARE_BACKUP=1; bs2_backup_reuse || echo OWN",
                              BS2_REUSE_SOURCE="0")
        self.assertEqual(r.stdout.splitlines(), ["note: source: a boot of its own (BS2_REUSE_SOURCE=0)", "BOOT",
                                                 "note: backup: one of its own (its source isn't the prepared one)", "OWN"])
        r, R = self.run_hooks("bs2_source_reuse || echo BOOT; BS2_SHARE_BACKUP=1; bs2_backup_reuse || echo OWN",
                              BS2_REUSE_BACKUP="0")
        self.assertEqual(r.stdout.splitlines()[-1], "OWN")
        r, R = self.run_hooks("HT_RUSTIC_SHA=r9; bs2_source_reuse && echo REUSED; BS2_SHARE_BACKUP=1; bs2_backup_reuse || echo OWN")
        self.assertIn("REUSED", r.stdout.splitlines())
        self.assertIn("note: backup: one of its own (rustic-bko", r.stdout)
        os.remove(f"{self.k}/dumps/src.json")
        r, R = self.run_hooks("bs2_source_reuse || echo BOOT")
        self.assertEqual(r.stdout.splitlines()[-1], "BOOT")
        self.assertIn("a boot of its own, not the prepared one (the preparation has no dumps/src.json)", r.stdout)


class TestBs2Switches(unittest.TestCase):
    def test_reuse_switches_reach_the_hosts(self):
        with mock.patch.dict(os.environ, {"BS2_RESCUER_STATE": "0", "BS2_REUSE_SOURCE": "0"}):
            os.environ.pop("BS2_REUSE_BACKUP", None)
            os.environ.pop("BS2_FAST_DOWN", None)
            cmd = B.root("x tier1 c")
            self.assertIn("BS2_REUSE_SOURCE=0 BS2_RESCUER_STATE=0 ./cmp-run.sh", cmd)
        with mock.patch.dict(os.environ, {}, clear=False):
            for v in B.REUSE_VARS:
                os.environ.pop(v, None)
            self.assertNotIn("BS2_", B.root("x"))

    def test_estimates_are_labelled(self):
        self.assertLess(B.SECS[1], 110)
        self.assertLess(B.SECS[1], B.SECS[2])


if __name__ == "__main__":
    unittest.main()
