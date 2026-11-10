"""K4's unit tests of HoltTest2's +img cells: holt.py (pins, no CODE build, no fast gate, the
guards), ht1.sh's branch, ram-size.sh and ht1-queue.sh's variant.  No host, no VM."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
WT = os.path.dirname(os.path.dirname(T2))
sys.path.insert(0, T2)
sys.path.insert(0, HERE)

from t2lib import holt  # noqa: E402
import test_holt as th  # noqa: E402

HT = os.path.join(WT, "testvm", "holttest1d")


def setUpModule():
    th.setUpModule()


def tearDownModule():
    th.tearDownModule()


def src(name):
    with open(os.path.join(HT, name), encoding="utf-8") as f:
        return f.read()


class Remote(th.FakeRemote):
    def __init__(self, outcomes, pins=None):
        super().__init__(outcomes)
        self.pins_asked, self.pins = [], pins

    def pin_images(self, host, tool, plats, env):
        self.pins_asked.append((host, list(plats), dict(env)))
        if self.pins and host in self.pins:
            return self.pins[host]
        return {p: {"ok": True, "sha256": "d" * 64, "how": "current", "stale": []} for p in plats}


class Flow(unittest.TestCase):
    PC = th.U52Todo.PC

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-k4-holt-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def ctx(self, cells):
        self.plan = th.FakePlan(cells)
        self.rs = th.FakeRS(self.tmp, self.plan)
        s = th.keys.TreeSource(WT)
        return type("Ctx", (), {"run": self.rs, "specs": th.keys.Specs(s), "src": s, "head": "6" * 40,
                                "classes": th.jload(os.path.join(T2, "classes.json")),
                                "copy_preview": {"commit": "6" * 40, "dirty": {}},
                                "options": {"pull": lambda: []}, "facts": {}})()

    def go(self, cells, remote=None, options=None):
        c = self.ctx(cells)
        if options:
            c.options.update(options)
        self.gated = 0

        def gate(ctx):
            self.gated += 1
            return None

        self.remote = remote or Remote({})
        return holt.run(c, self.plan, remote=self.remote, sleep=lambda s: None, poll=0, gate=gate)

    def both(self):
        return {"chopin": "1" * 64, "cinqcent": "1" * 64}

    def test_all_img_needs_no_build_and_no_gate(self):
        res = self.go([self.PC("holt:debian12+img", "img:panel", keys_=self.both()),
                       self.PC("holt:aerynos+img", "img:panel", keys_=self.both())])
        self.assertEqual(self.gated, 0)
        starts = [c[0] for h, op, *c in self.remote.calls if op == "start"]
        self.assertFalse([c for c in starts if "build-holt.sh" in c], starts)
        qs = [c for c in starts if "ht1-queue.sh" in c]
        self.assertEqual(len(qs), 2)
        self.assertTrue(all("HT_HOLT_BIN" not in c for c in qs), qs)
        self.assertEqual(sorted(self.remote.written[("chopin", holt.QUEUE + "/t2h-20261108T0300-abcd/todo")].split()),
                         ["aerynos+img", "debian12+img"])
        self.assertEqual(res["exit"], 0, res)

    def test_the_pin_comes_first_on_each_host(self):
        self.go([self.PC("holt:debian12+img", "img:panel", keys_=self.both()),
                 self.PC("holt:freebsd14+img", "img:panel", keys_={"chopin": "2" * 64})])
        asked = {h: p for h, p, _ in self.remote.pins_asked}
        self.assertEqual(asked, {"chopin": ["freebsd", "linux"], "cinqcent": ["linux"]})
        self.assertTrue(all(e["T2_HOST_ROOT"] == "/bkoscratch/t2test" for _, _, e in self.remote.pins_asked))

    def test_a_refused_pin_keeps_the_guest_off_that_host(self):
        r = Remote({}, pins={"cinqcent": {"linux": {"ok": False, "why": "a build is under way (no done.exit)"}}})
        res = self.go([self.PC("holt:debian12+img", "img:panel", keys_=self.both())], remote=r)
        qs = [c[0] for h, op, *c in r.calls if op == "start" and "ht1-queue.sh" in c[0]]
        self.assertIn("ONLY_ELSEWHERE=debian12", qs[1])
        self.assertTrue(any("a build is under way" in n for n in res["notes"]))

    def test_a_mixed_run_builds_for_the_plain_cells_and_gates(self):
        self.go([self.PC("holt:debian13", "holt:code", keys_=self.both()),
                 self.PC("holt:debian12+img", "img:panel", keys_=self.both())])
        self.assertEqual(self.gated, 1)
        starts = [c[0] for h, op, *c in self.remote.calls if op == "start"]
        self.assertEqual(len([c for c in starts if "build-holt.sh" in c]), 1)
        self.assertTrue(all("HT_HOLT_BIN=" in c for c in starts if "ht1-queue.sh" in c))

    def real_gate(self, options, ledger=()):
        from unittest import mock
        from t2lib import gate as gmod
        c = self.ctx([self.PC("holt:debian12+img", "img:panel", keys_=self.both())])
        c.options.update(options)
        c.ledger = list(ledger)
        r = holt.Runner(c, self.plan, remote=Remote({}), sleep=lambda s: None, poll=0)
        ran = []
        gp = type("GP", (), {"cells": [type("C", (), {"cell": "gate:holt", "status": "due"})()]})()
        with mock.patch.object(gmod, "family_layers", lambda f: {"fast": ["holt"]}), \
                mock.patch.object(gmod, "gate_plan", lambda ctx: gp), \
                mock.patch.object(gmod, "run_layers", lambda ctx, names, part, plan=None: ran.append(list(names)) or {"cells": []}):
            why = r.gate()
        return why, ran, r.notes

    def test_the_holt_gate_step_takes_the_composites_universe_and_runs_once_in_a_sweep(self):
        why, ran, notes = self.real_gate({"gate_universe": ["gate:bin:version"]})
        self.assertEqual((why, ran), (None, []))
        self.assertTrue(any("--universe" in n for n in notes), notes)
        self.assertEqual(self.real_gate({"gate_universe": ["gate:holt"]})[:2], (None, [["holt"]]))
        stamp = self.rs.run[len("t2h-"):]
        row = {"cell": "gate:holt", "kind": "run", "run": "t2h-" + stamp, "verdict": "PASS", "t": "1", "id": "a"}
        self.assertEqual(self.real_gate({"gate_fresh": True}, [row])[:2], (None, []))
        self.assertEqual(self.real_gate({"gate_fresh": True}, [dict(row, run="t2h-20260101T0000-0000")])[:2], (None, [["holt"]]))
        why, ran, _ = self.real_gate({"gate_fresh": True}, [row, dict(row, verdict="FAIL", t="2", id="b")])
        self.assertEqual(ran, [])
        self.assertIn("gate:holt already ran in this sweep and didn't pass", why)

    def test_gate_fresh_gates_all_img_too(self):
        self.go([self.PC("holt:debian12+img", "img:panel", keys_=self.both())], options={"gate_fresh": True})
        self.assertEqual(self.gated, 1)

    def test_the_guards_stop_the_queue_once(self):
        class G:
            def tripped(self):
                return True

        class R(Remote):
            pass

        r = R({})
        c = self.ctx([self.PC("holt:debian12+img", "img:panel", keys_=self.both())])
        self.rs.stops.guards = G()
        self.gated = 0
        res = holt.run(c, self.plan, remote=r, sleep=lambda s: None, poll=0, gate=lambda ctx: None)
        touched = [x for x in r.calls if x[1] == "touch" and x[2].endswith("/stop")]
        self.assertEqual(sorted(h for h, _, _ in touched), ["chopin", "cinqcent"])
        self.assertTrue(any("run-wide stop" in s for s in res["stops"]))


class Driver(unittest.TestCase):
    def test_img_key_is_bulk_and_released(self):
        ht = src("ht1.sh")
        bulk = re.search(r"^HT_BULK=\((.*)\)$", ht, re.M).group(1)
        self.assertIn("img-key", bulk.split())
        self.assertIn("img-key.pub", bulk.split())
        rel = re.search(r"^ht_release\(\) \{(.*)\}$", ht, re.M).group(1)
        self.assertIn('rm -f "$R/img-key"', rel)

    def test_the_key_goes_at_the_rebuilds_end(self):
        ht = src("ht1.sh")
        self.assertRegex(ht, r'vm_down "\$rkey"\n    rm -f "\$R/rescuer\.qcow2" "\$R/img-key" "\$R/img-key\.pub"')

    def test_no_key_survives_to_k(self):
        # ht_end calls ht_release before R goes to K: the key can't be moved there.
        lib = src("ht1-lib.sh")
        rel = lib.index("declare -F ht_release >/dev/null && ht_release")
        self.assertLess(rel, lib.index("R to K: copied into K.part"))
        self.assertIn('--exclude=/.ht/', lib)

    def test_a_variant_with_a_debug_keep_is_refused(self):
        self.assertRegex(src("ht1.sh"), r"no HT_KEEP or HT_CLEAN=0")

    def test_both_drivers_run_img_in_ram_only_and_check_the_pin_at_the_rebuild(self):
        # (on disk R is K, where a killed run would leave the key; a re-pin during the run would
        # credit the row to an image that didn't boot)
        for name in ("ht1.sh", "ht1-bsd.sh"):
            ht = src(name)
            self.assertIn("img-key", re.search(r"^HT_BULK=\((.*)\)$", ht, re.M).group(1).split(), name)
            self.assertRegex(ht, r"HT_MODE == ram \]\] \|\| \{ echo \"ht1(-bsd)?: \+img: only in the RAM disk", name)
            self.assertLess(ht.index("\nht_paths"), ht.index("only in the RAM disk"), name)
            self.assertIn("IMG_ID=$(pin_id)", ht, name)
            self.assertIn('IMG_HARNESS="the pin changed during the run"', ht, name)
            self.assertIn("${IMG_HARNESS:+; harness: $IMG_HARNESS}", ht, name)

    def test_rebuild_asserts_rather_than_installs(self):
        ht = src("ht1.sh")
        branch = ht[ht.index('if [[ $VARIANT == img ]]; then\n        # The image\'s own tools'):]
        branch = branch[:branch.index("\n    else\n")]
        for tool in ("mkfs.ext4", "mkfs.xfs", "mkfs.btrfs", "mkfs.vfat", "mkfs.f2fs", "mkfs.nilfs2", "mkfs.jfs", "mkfs.bcachefs",
                     "zpool", "mdadm", "cryptsetup", "lvm", "sgdisk"):
            self.assertIn(tool, branch, tool)
        self.assertNotIn("apt-get", branch)
        self.assertNotIn("inst ", branch)
        self.assertIn("command -v $c", branch)

    def test_the_image_is_a_raw_backed_16g_overlay_and_its_own_binaries_run(self):
        ht = src("ht1.sh")
        self.assertIn('qemu-img create -q -f qcow2 -b "$RESCUER_IMG" -F raw "$R/rescuer.qcow2" 16G', ht)
        self.assertIn("RHOLT=/usr/local/bin/bko-holt RARCH=/usr/local/bin/rustic-bko", ht)
        self.assertIn('"$RHOLT load /mnt/share/layout.json $*"', ht)
        self.assertIn('--archiver "$RARCH"', ht)
        self.assertIn("HT_RUSTIC=$IMG_PIN/rustic-bko HT_HOLT=$IMG_PIN/bko-holt", ht)

    @unittest.skipUnless(shutil.which("bash"), "no bash")
    def test_ram_size_accepts_img(self):
        with tempfile.TemporaryDirectory() as t:
            table = os.path.join(t, "table.tsv")
            with open(table, "w") as f:
                f.write("guest\tvariant\thost\tleft\tD\tM\tsource\ndebian12\t-\tx\t1\t5\t4\ts\n")
            r = subprocess.run(["bash", os.path.join(HT, "ram-size.sh"), "--table", table, "--peaks", "/dev/null", "debian12+img"],
                               capture_output=True, text=True, env=dict(os.environ, PATH=os.environ["PATH"]))
            if r.returncode == 127:
                self.skipTest("no realpath or awk")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), ["debian12+img", "5", "4", "table"])
            r = subprocess.run(["bash", os.path.join(HT, "ram-size.sh"), "--table", table, "--peaks", "/dev/null", "debian12+nope"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)

    def test_the_queue_takes_the_variant(self):
        q = src("ht1-queue.sh")
        self.assertRegex(q, r"LINE_RE=.*\|img\)")
        self.assertRegex(q, r"\(bigger\|oneshot\|degraded\|img\)\$ \]\] && gv=")

    def test_img_boot_shell_lines(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("img_boot", os.path.join(HT, "img-boot.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        cfg = m.config("linux")
        self.assertEqual(cfg["user"], "root")
        lines = m.shell_lines("ssh-" + "ed25519 AAAA t2", cfg)
        self.assertEqual(lines[0], "export TERM=dumb; stty -echo")
        self.assertIn("mkdir -p /run/sshd", lines)
        self.assertLess(lines.index("mkdir -p /run/sshd"), next(i for i, x in enumerate(lines) if "AuthorizedKeysFile" in x))
        self.assertTrue(any("/tmp/bko-k" in x and "AAAA" in x for x in lines))
        sshd = next(x for x in lines if "AuthorizedKeysFile" in x)
        for o in ("AuthorizedKeysFile=/tmp/bko-k", "StrictModes=no", "PermitRootLogin=prohibit-password", "AllowUsers=root",
                  "PasswordAuthentication=no"):
            self.assertIn(o, sshd)
        self.assertTrue(all(len(x) < 300 for x in lines))

    def test_img_boot_uses_boot_tests_own_prompts(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("img_boot", os.path.join(HT, "img-boot.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        bt = m.load_boot_test()
        for name in ("Console", "MENU", "PROMPT", "TYPING_RATE"):
            self.assertTrue(hasattr(bt, name), name)

    def test_the_menu_is_answered_each_time_it_appears(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("img_boot", os.path.join(HT, "img-boot.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        script = ("import sys\nfor i in range(2):\n print('Choose [1-4]: ',end='',flush=True)\n sys.stdin.readline()\n"
                  " print('# ',end='',flush=True)\n while 'BKO-IMG-SSHD' not in sys.stdin.readline(): pass\n print('rebooted',flush=True)\n")
        with tempfile.TemporaryDirectory() as t:
            key = os.path.join(t, "k.pub")
            with open(key, "w") as f:
                f.write("ssh-" + "ed25519 AAAA t2\n")
            log = os.path.join(t, "console.log")
            r = subprocess.run([sys.executable, os.path.join(HT, "img-boot.py"), log, "linux", key, "--", sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(log) as f:
                self.assertEqual(f.read().count("Choose [1-4]: "), 2)


if __name__ == "__main__":
    unittest.main()
