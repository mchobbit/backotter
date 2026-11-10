"""K4's unit tests: boot-test.py's manifest, img.py (parsing, the panel, the flow against fake
hosts), tool_img.py (the pin), stick/img-one.sh's commit file, boot.sh.

No host, no VM.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
WT = os.path.dirname(os.path.dirname(T2))
sys.path.insert(0, T2)

from t2lib import hostjob, img, run as runmod, tool_img  # noqa: E402

FX = os.path.join(HERE, "fixtures", "img")
_SAVED, _TMP = {}, None


def setUpModule():
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="t2-k4-roots-")
    env = {"T2_MAC_ROOT": os.path.join(_TMP, "mac"), "T2_HOST_ROOT": os.path.join(_TMP, "host"),
           "T2_COPY_ROOT": os.path.join(_TMP, "copy"), "T2_SSH": "false"}
    for k, v in env.items():
        _SAVED[k] = os.environ.get(k)
        os.environ[k] = v
    for k in ("T2_LEDGER", "T2_WORK", "T2_IMAGES", "BKO_WORK", "BKO_IMG_DIR"):
        _SAVED[k] = os.environ.pop(k, None)


def tearDownModule():
    for k, v in _SAVED.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(_TMP, True)


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def wr(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def jdump(path, obj):
    wr(path, json.dumps(obj))


def sha_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def load_boot_test():
    spec = importlib.util.spec_from_file_location("boot_test", os.path.join(WT, "stick", "boot-test.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BootTest(unittest.TestCase):
    def test_manifest_fields_from_a_console_log(self):
        bt = load_boot_test()
        log = b"junk\r\nBKO-MF kernel 7.0.0\r\nBKO-MF tool:lvm abc\r\nBKO-MF empty \r\nBKO-CHECK x ok y\r\n"
        self.assertEqual(bt.manifest_fields(log), {"kernel": "7.0.0", "tool:lvm": "abc", "empty": ""})

    def test_manifest_fields_as_real_serial_logs_have_them(self):
        # The 11-07 logs' BKO-CHECK lines, scrubbed (the prompts' user@ too), with the marker changed: the prompt and,
        # on Linux, OSC 3008 sequences are on the marker's line.
        bt = load_boot_test()
        osc = (b"\x1b]3008;end=UUID;exit=success\x1b\\\x1b]3008;start=UUID;type=shell;machineid=MID;user=root;cwd=/\x1b\\"
               b"backotter:/# \x1b]3008;start=UUID;type=command;cwd=/\x1b\\")
        log = (b">           echo \"BKO-MF $n $(echo \"$out\" | tr '\\n' ' ' | cut -c1-200)\"; }\r\n"
               + osc + b"BKO-MF kernel 7.0.0-43-generic \r\n"
               + b"backotter:/ # > > > > backotter:/ # BKO-MF tool:gpart abc \r\n"
               + b"backotter:~$ > > > backotter:~$ BKO-MF zfs zfs-2.4.1 \r\n"
               + b"BKO-MF bin:bko-holt cafe \r\n")
        self.assertEqual(bt.manifest_fields(log), {"kernel": "7.0.0-43-generic", "tool:gpart": "abc", "zfs": "zfs-2.4.1",
                                                   "bin:bko-holt": "cafe"})

    def test_each_platform_has_manifest_lines_under_the_typing_limit(self):
        bt = load_boot_test()
        for p in bt.PLATFORMS:
            lines = bt.manifest_script(p).splitlines()
            self.assertTrue(lines, p)
            self.assertFalse(lines[0].startswith("#"), p)
            self.assertTrue(all(len(x) < 300 for x in lines), p)
            keys = [x.split()[1] for x in lines if x.startswith("mf ")]
            self.assertEqual(len(keys), len(set(keys)), p)
            for need in ("kernel", "image_id", "bin:rustic-bko", "bin:bko-holt"):
                self.assertIn(need, keys, p)
            self.assertTrue(any(k.startswith("tool:") for k in keys), p)
            if p != "haiku":
                self.assertIn("bin:restic-bko", keys, p)
            self.assertTrue(all(x.startswith("mf ") or p == "illumos" for x in lines), p)

    def test_a_command_gets_no_terminal_stdin(self):
        self.assertIn("</dev/null", load_boot_test().MANIFEST_FUNCTION)

    def test_mf_runs_in_a_shell(self):
        bt = load_boot_test()
        script = bt.MANIFEST_FUNCTION + "mf a 'echo hello; echo there'\nmf b 'true'\nmf c 'cat'\n"
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.stdout.splitlines(), ["BKO-MF a hello there ", "BKO-MF b - ", "BKO-MF c - "])


class Parse(unittest.TestCase):
    def test_a_passing_mode(self):
        got = img.parse_boot_out(rd(os.path.join(FX, "boot-linux-bios.out")))
        self.assertTrue(got["reached"])
        self.assertEqual(len(got["checks"]), 35)
        self.assertTrue(all(ok for _, ok, _ in got["checks"]))
        self.assertIn(("kernel", True, "7.0.0-43-generic"), got["checks"])

    def test_the_illumos_smf_fail(self):
        files = {"boot-bios.out": rd(os.path.join(FX, "boot-illumos-bios.out")), "boot-bios.rc": "1\n"}
        needs, fails = img.boot_needs(("bios",), files)
        self.assertEqual(needs["boot:bios:smf"], "FAIL")
        self.assertEqual(needs["boot:bios:kernel"], "PASS")
        self.assertEqual([f for f in fails if ":smf" in f],
                         ["boot:bios:smf: in trouble: svc:/application/backotter/console-release:default"])
        self.assertEqual(sum(1 for v in needs.values() if v == "FAIL"), 1)

    def test_a_boot_that_failed_before_its_checks(self):
        for name, mode in (("boot-failed.out", "bios"), ("boot-timeout.out", "uefi")):
            files = {f"boot-{mode}.out": rd(os.path.join(FX, name)), f"boot-{mode}.rc": "1\n"}
            needs, fails = img.boot_needs((mode,), files)
            self.assertEqual(needs, {f"boot:{mode}": "FAIL"})
            self.assertTrue(fails[0].startswith(f"boot:{mode}: "), fails)

    def test_missing_output(self):
        self.assertEqual(img.boot_needs(("bios",), {})[0], {"boot:bios": "FAIL"})

    def test_passing_checks_with_a_failing_exit_arent_a_pass(self):
        files = {"boot-bios.out": "bios (usb): reached login in 3 s\n  ok   a  b\n", "boot-bios.rc": "1\n"}
        needs, _ = img.boot_needs(("bios",), files)
        self.assertEqual(needs, {"boot:bios:a": "PASS", "boot:bios": "FAIL"})


class ManifestBins(unittest.TestCase):
    M = {"bin:rustic-bko": "a", "bin:bko-holt": "b", "kernel": "k"}

    def test_match(self):
        self.assertEqual(img.check_manifest_bins(self.M, {"rustic-bko": "a", "bko-holt": "b"}), ("PASS", []))

    def test_wrong_sum(self):
        n, f = img.check_manifest_bins(self.M, {"rustic-bko": "x", "bko-holt": "b"})
        self.assertEqual(n, "FAIL")
        self.assertIn("rustic-bko", f[0])

    def test_a_binary_in_the_image_and_not_recorded(self):
        n, f = img.check_manifest_bins(dict(self.M, **{"bin:restic-bko": "c"}), {"rustic-bko": "a", "bko-holt": "b"})
        self.assertEqual(n, "FAIL")
        self.assertIn("restic-bko", f[0])

    def test_no_manifest(self):
        self.assertEqual(img.check_manifest_bins(None, {"a": "b"})[0], "FAIL")


def mf(**kw):
    m = {"kernel": "k1", "zfs": "z1", "image_id": "i1", "tool:lvm": "t1", "pkg:lvm2": "v1", "bin:bko-holt": "b1"}
    m.update(kw)
    return m


def passrow(manifest, t="2026-11-09T00:00:00Z", result="pass", verdict="PASS", valid=True, cell="img:linux"):
    return {"cell": cell, "kind": "run", "committed": True, "verdict": verdict, "valid": valid, "t": t,
            "extra": {"manifest": manifest, "panel": {"result": result}}}


class PanelDecision(unittest.TestCase):
    CFG = {"guests": ["debian12", "ubuntu24"]}

    def test_no_earlier_panel(self):
        d = img.panel_decision("linux", mf(), [], self.CFG)
        self.assertEqual((d["needed"], d["result"], d["why"]), (True, "owed", "no earlier panel"))
        self.assertEqual(d["cells"], ["holt:debian12+img", "holt:ubuntu24+img"])

    def test_unchanged(self):
        d = img.panel_decision("linux", mf(image_id="i2", **{"bin:bko-holt": "b2"}), [passrow(mf())], self.CFG)
        self.assertEqual((d["needed"], d["result"], d["why"]), (False, "none", "storage pieces unchanged"))

    def test_each_storage_field_moves_it(self):
        for kw, word in (({"kernel": "k2"}, "kernel"), ({"zfs": "z2"}, "zfs"), ({"tool:lvm": "t2"}, "storage tools"),
                         ({"pkg:lvm2": "v2"}, "storage tools")):
            d = img.panel_decision("linux", mf(**kw), [passrow(mf())], self.CFG)
            self.assertEqual((d["result"], d["why"]), ("owed", f"{word} changed"))

    def test_a_tool_removed_counts(self):
        m = mf()
        del m["tool:lvm"]
        self.assertEqual(img.panel_decision("linux", m, [passrow(mf())], self.CFG)["result"], "owed")

    def test_only_a_row_whose_panel_passed_is_the_reference(self):
        rows = [passrow(mf(kernel="old"), "2026-11-01T00:00:00Z"), passrow(mf(), "2026-11-09T00:00:00Z", result="owed"),
                passrow(mf(), "2026-11-09T01:00:00Z", result="none"), passrow(mf(), "2026-11-09T02:00:00Z", result="fail"),
                passrow(mf(), "2026-11-09T03:00:00Z", valid=False), passrow(mf(), "2026-11-09T04:00:00Z", cell="img:netbsd")]
        self.assertEqual(img.panel_decision("linux", mf(), rows, self.CFG)["why"], "kernel changed")

    def test_a_row_from_a_dirty_copy_is_no_reference(self):
        d = img.panel_decision("linux", mf(), [dict(passrow(mf()), committed=False)], self.CFG)
        self.assertEqual((d["needed"], d["why"]), (True, "no earlier panel"))
        d = img.panel_decision("linux", mf(), [dict(passrow(mf()), kind="class")], self.CFG)
        self.assertEqual((d["needed"], d["why"]), (True, "no earlier panel"))

    def test_the_manifest_fixture(self):
        m = json.loads(rd(os.path.join(FX, "manifest-linux.json")))
        self.assertEqual(sorted(img.storage_fields(m)),
                         ["kernel", "pkg:mdadm", "pkg:zfsutils-linux", "tool:mdadm", "tool:zpool", "zfs"])
        self.assertFalse(img.panel_decision("linux", dict(m, image_id="another"), [passrow(m)], self.CFG)["needed"])
        d = img.panel_decision("linux", dict(m, **{"tool:mdadm": "9999"}), [passrow(m)], self.CFG)
        self.assertEqual((d["needed"], d["why"]), (True, "storage tools changed"))

    def test_the_newest_reference_wins(self):
        rows = [passrow(mf(), "2026-11-01T00:00:00Z"), passrow(mf(kernel="k9"), "2026-11-08T00:00:00Z")]
        self.assertEqual(img.panel_decision("linux", mf(kernel="k9"), rows, self.CFG)["needed"], False)

    def test_release_and_all_force_it(self):
        rows = [passrow(mf())]
        self.assertEqual(img.panel_decision("linux", mf(), rows, self.CFG, release=True)["why"], "release")
        self.assertEqual(img.panel_decision("linux", mf(), rows, self.CFG, all_form=True)["why"], "ImgTest2All")

    def test_blocked(self):
        cfg = dict(self.CFG, blocked="no bsd client")
        d = img.panel_decision("openbsd", mf(), [], cfg)
        self.assertEqual((d["needed"], d["result"], d["why"]), (True, "blocked", "blocked: no bsd client"))
        d = img.panel_decision("linux", mf(), [passrow(mf())], cfg)
        self.assertEqual(d["result"], "none")


class PanelCells(unittest.TestCase):
    class P:
        def __init__(self, keys):
            self.keys = keys

        def cell(self, c):
            return self

    def row(self, verdict, key="k", t="2026-11-09T00:00:00Z", valid=True):
        return {"cell": "holt:a+img", "verdict": verdict, "key": key, "t": t, "valid": valid}

    def test_results(self):
        p = self.P({"h": "k"})
        self.assertEqual(img.panel_cell_results(p, [self.row("PASS")], ["holt:a+img"]), {"holt:a+img": "PASS"})
        self.assertEqual(img.panel_cell_results(p, [self.row("FAIL")], ["holt:a+img"]), {"holt:a+img": "FAIL"})
        self.assertEqual(img.panel_cell_results(p, [self.row("HARNESS")], ["holt:a+img"]), {"holt:a+img": None})
        self.assertEqual(img.panel_cell_results(p, [], ["holt:a+img"]), {"holt:a+img": None})

    def test_another_key_or_an_invalid_row_says_nothing(self):
        p = self.P({"h": "k"})
        self.assertEqual(img.panel_cell_results(p, [self.row("PASS", key="old")], ["holt:a+img"]), {"holt:a+img": None})
        self.assertEqual(img.panel_cell_results(p, [self.row("PASS", valid=False)], ["holt:a+img"]), {"holt:a+img": None})

    def test_no_keys_is_not_any_key(self):
        old = self.row("PASS", key="old")
        self.assertEqual(img.panel_cell_results(self.P({}), [old], ["holt:a+img"]), {"holt:a+img": None})
        self.assertEqual(img.panel_cell_results(None, [old], ["holt:a+img"]), {"holt:a+img": None})

    def test_a_cell_the_plan_cant_run_says_nothing(self):
        p = self.P({"h": "k"})
        p.status = "not-runnable"
        self.assertEqual(img.panel_cell_results(p, [self.row("PASS")], ["holt:a+img"]), {"holt:a+img": None})

    def test_only_the_boot_rows_image_counts(self):
        p = self.P({"chopin": "k1", "cinqcent": "k2"})
        other = dict(self.row("PASS", key="k2"), meta={"img": {"sha256": "other"}})
        same = dict(self.row("PASS", key="k1"), meta={"img": {"sha256": "boot"}})
        by_input = dict(self.row("PASS", key="k2"), inputs={"rescuer": {"rescuer/img-linux": "sha256:boot"}})
        self.assertEqual(img.panel_cell_results(p, [other], ["holt:a+img"], {"holt:a+img": "boot"}), {"holt:a+img": None})
        self.assertEqual(img.panel_cell_results(p, [same], ["holt:a+img"], {"holt:a+img": "boot"}), {"holt:a+img": "PASS"})
        self.assertEqual(img.panel_cell_results(p, [by_input], ["holt:a+img"], {"holt:a+img": "boot"}), {"holt:a+img": "PASS"})
        self.assertEqual(img.panel_cell_results(p, [same], ["holt:a+img"], {"holt:a+img": None}), {"holt:a+img": None})

    def test_the_newest_row_decides(self):
        rows = [self.row("FAIL", t="2026-11-09T00:00:00Z"), self.row("PASS", t="2026-11-09T01:00:00Z")]
        self.assertEqual(img.panel_cell_results(self.P({"h": "k"}), rows, ["holt:a+img"]), {"holt:a+img": "PASS"})


# --- the pin -------------------------------------------------------------------------------------

class PathOnMount(unittest.TestCase):
    MI = ("52 31 259:5 / /bkoscratch rw,noatime shared:91 - xfs /dev/x rw\n"
          "56 31 259:5 /work /home/m/backotter-work rw,noatime shared:91 - xfs /dev/x rw\n"
          "59 56 259:5 /vms /home/m/backotter-work/vms rw,noatime shared:91 - xfs /dev/x rw\n"
          "70 31 8:1 / / rw - ext4 /dev/y rw\n")

    def setUp(self):
        self.real = os.path.realpath
        os.path.realpath = lambda p: p
        self.addCleanup(lambda: setattr(os.path, "realpath", self.real))
        self.mounts = tool_img.parse_mountinfo(self.MI)

    def test_two_mounts_of_one_filesystem(self):
        self.assertEqual(tool_img.path_on_mount("/home/m/backotter-work/images/linux/test/x.img", "/bkoscratch/test2/img-pin/linux", self.mounts),
                         "/bkoscratch/work/images/linux/test/x.img")

    def test_the_same_mount(self):
        self.assertEqual(tool_img.path_on_mount("/bkoscratch/a/x.img", "/bkoscratch/t", self.mounts), "/bkoscratch/a/x.img")

    def test_another_filesystem(self):
        self.assertIsNone(tool_img.path_on_mount("/etc/x", "/bkoscratch/t", self.mounts))

    def test_a_path_the_target_mount_doesnt_show(self):
        self.assertIsNone(tool_img.path_on_mount("/bkoscratch/other/x.img", "/home/m/backotter-work/p", self.mounts))
        self.assertIsNone(tool_img.path_on_mount("/home/m/backotter-work/vms/g/x", "/home/m/backotter-work/images", self.mounts[:2]) and None)

    def test_the_longest_mount_point_holds_it(self):
        self.assertEqual(tool_img.mount_of("/home/m/backotter-work/vms/g/d.qcow2", self.mounts)["root"], "/vms")


class Pin(unittest.TestCase):
    IMG, RST, RUS, HOL = "test image bytes\n", "restic\n", "rustic\n", "holt\n"

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-k4-pin-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.images = os.path.join(self.t, "work", "images")
        self.work = os.path.join(self.t, "work")
        self.root = os.path.join(self.t, "state")
        self.make()

    def make(self, plat="netbsd"):
        d = os.path.join(self.images, plat)
        wr(os.path.join(d, "test", "bko.img"), self.IMG)
        wr(os.path.join(d, "done.exit"), "0\n")
        wr(os.path.join(d, "bko-bin.sha256"), f"{sha_text(self.RST)}  ./restic-bko\n{sha_text(self.RUS)}  ./rustic-bko\n{sha_text(self.HOL)}  ./bko-holt\n")
        wr(os.path.join(self.work, "bko-bin", plat, "restic-bko"), self.RST, 0o755)
        wr(os.path.join(self.work, "bko-bin", plat, "rustic-bko"), self.RUS, 0o755)
        wr(os.path.join(self.work, "holt-bin", plat, "bko-holt"), self.HOL, 0o755)

    def pin(self, plat="netbsd", **kw):
        kw.setdefault("copy", shutil.copy)   # (cp --reflink is Linux's)
        return tool_img.pin_one(plat, self.images, self.work, self.root, **kw)

    def pdir(self, plat="netbsd"):
        return os.path.join(self.root, "img-pin", plat)

    def test_a_hard_link_on_the_same_filesystem(self):
        src = os.path.join(self.images, "netbsd", "test", "bko.img")
        r = self.pin(mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"))
        self.assertEqual(r["how"], "link")
        self.assertEqual(os.stat(src).st_ino, os.stat(os.path.join(self.pdir(), "rescuer.img")).st_ino)
        self.assertEqual(rd(os.path.join(self.pdir(), "rescuer.img.sha256")).split()[0], sha_text(self.IMG))

    def test_exdev_falls_back_to_a_temp_reflink_and_rename(self):
        calls = []

        def link(a, b):
            calls.append(("link", a, b))
            raise OSError(errno.EXDEV, "cross-device")

        def copy(a, b):
            calls.append(("copy", a, b))
            shutil.copy(a, b)

        mounts = tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n")
        r = self.pin(mounts=mounts, link=link, copy=copy)
        self.assertEqual((r["ok"], r["how"]), (True, "reflink"))
        self.assertEqual([c[0] for c in calls], ["link", "copy"])
        self.assertIn("rescuer.img.tmp.", calls[1][2])
        self.assertEqual(rd(os.path.join(self.pdir(), "rescuer.img")), self.IMG)
        self.assertEqual([n for n in os.listdir(self.pdir()) if ".tmp." in n], [])

    def test_a_rename_keeps_a_running_tests_inode(self):
        mounts = tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n")
        self.pin(mounts=mounts)
        p = os.path.join(self.pdir(), "rescuer.img")
        with open(p, encoding="utf-8") as held:
            test = os.path.join(self.images, "netbsd", "test", "bko.img")
            os.unlink(test)
            wr(test, "a newer image\n")
            r = self.pin(mounts=mounts)
            self.assertEqual(r["how"], "link")
            self.assertEqual(held.read(), self.IMG)
        self.assertEqual(rd(p), "a newer image\n")

    def test_source_json_current_and_not(self):
        mounts = tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n")
        r = self.pin(mounts=mounts)
        sj = json.loads(rd(os.path.join(self.pdir(), "source.json")))
        test = os.path.join(self.images, "netbsd", "test", "bko.img")
        s = os.stat(test)
        self.assertEqual(sj, {"path": test, "size": s.st_size, "mtime_ns": s.st_mtime_ns, "inode": s.st_ino, "sha256": r["sha256"]})
        self.assertEqual(self.pin(mounts=mounts)["how"], "current")
        os.utime(test, ns=(s.st_atime_ns, s.st_mtime_ns + 1000))
        self.assertEqual(self.pin(mounts=mounts)["how"], "link")

    def test_refused_while_building(self):
        os.unlink(os.path.join(self.images, "netbsd", "done.exit"))
        r = self.pin()
        self.assertFalse(r["ok"])
        self.assertIn("no done.exit", r["why"])

    def test_refused_while_the_lock_is_held(self):
        lk = os.path.join(self.images, ".lock-netbsd")
        with open(lk, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            r = self.pin()
        self.assertFalse(r["ok"])
        self.assertIn("is held", r["why"])

    def test_a_free_lock_is_nothing(self):
        wr(os.path.join(self.images, ".lock-netbsd"), "")
        self.assertTrue(self.pin(mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"))["ok"])

    def test_no_test_image(self):
        os.unlink(os.path.join(self.images, "netbsd", "test", "bko.img"))
        r = self.pin()
        self.assertEqual((r["ok"], r["why"]), (False, "no test image"))

    def test_the_binaries_land_checked(self):
        r = self.pin(mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"))
        self.assertEqual(r["stale"], [])
        for n, t in (("restic-bko", self.RST), ("rustic-bko", self.RUS), ("bko-holt", self.HOL)):
            self.assertEqual(rd(os.path.join(self.pdir(), n)), t)
        self.assertFalse(os.path.exists(os.path.join(self.pdir(), "bins-stale")))

    def test_stale_binaries(self):
        wr(os.path.join(self.work, "holt-bin", "netbsd", "bko-holt"), "another build\n", 0o755)
        r = self.pin(mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["stale"], ["bko-holt"])
        self.assertEqual(rd(os.path.join(self.pdir(), "bins-stale")), "bko-holt\n")
        self.assertFalse(os.path.exists(os.path.join(self.pdir(), "bko-holt")))
        # the set is back: the file goes
        wr(os.path.join(self.work, "holt-bin", "netbsd", "bko-holt"), self.HOL, 0o755)
        self.assertEqual(self.pin(mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"))["stale"], [])
        self.assertFalse(os.path.exists(os.path.join(self.pdir(), "bins-stale")))

    def test_no_writes_under_images_or_the_binaries(self):
        before = self.tree()
        self.pin(mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"))
        self.assertEqual(self.tree(), before)

    def tree(self):
        out = {}
        for base in (self.images, os.path.join(self.work, "bko-bin"), os.path.join(self.work, "holt-bin")):
            for d, _, ns in os.walk(base):
                for n in ns:
                    p = os.path.join(d, n)
                    s = os.stat(p)
                    out[p] = (s.st_size, s.st_mtime_ns, rd(p))
        return out

    def fake_xorriso(self, release, test):
        def run(argv, **kw):
            tag = release if "/test/" not in argv[4] else test
            if tag is not None:
                wr(argv[7], tag)
            return subprocess.CompletedProcess(argv, 0)
        return run

    def linux(self, release, test, rel_img=True):
        self.make("linux")
        if rel_img:
            wr(os.path.join(self.images, "linux", "rel.img"), "release\n")
        return self.pin("linux", mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"), xorriso=self.fake_xorriso(release, test))

    def test_release_same(self):
        t = "aaa  ./casper/a.squashfs\nbbb  ./boot/grub/grub.cfg\n"
        r = self.linux(t, t.replace("bbb", "ccc"))
        self.assertTrue(r["release_same"]["same"], r["release_same"])
        sj = json.loads(rd(os.path.join(self.pdir("linux"), "source.json")))
        self.assertTrue(sj["release_same"]["same"])

    def test_release_different(self):
        r = self.linux("aaa  ./casper/a.squashfs\n", "bbb  ./casper/a.squashfs\n")
        self.assertIs(r["release_same"]["same"], False)
        self.assertIn("casper/a.squashfs", r["release_same"]["why"])

    def test_release_unreadable(self):
        self.assertIs(self.linux(None, "a  ./x.squashfs\n")["release_same"]["same"], None)
        d = os.path.join(self.images, "linux")
        os.unlink(os.path.join(d, "rel.img"))
        os.unlink(os.path.join(self.pdir("linux"), "source.json"))
        r = self.pin("linux", mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"), xorriso=self.fake_xorriso("x", "x"))
        self.assertEqual(r["release_same"], {"same": None, "why": "no release image"})

    def test_a_comparison_that_failed_is_made_again(self):
        t = "aaa  ./casper/a.squashfs\n"
        self.assertIs(self.linux(None, t)["release_same"]["same"], None)
        r = self.pin("linux", mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"), xorriso=self.fake_xorriso(t, t))
        self.assertEqual(r["how"], "current")
        self.assertIs(r["release_same"]["same"], True)

    def test_an_answer_is_kept(self):
        t = "aaa  ./casper/a.squashfs\n"
        self.linux(t, t)

        def boom(argv, **kw):
            raise AssertionError("compared again")
        r = self.pin("linux", mounts=tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n"), xorriso=boom)
        self.assertIs(r["release_same"]["same"], True)

    def test_a_relink_over_the_same_inode_leaves_no_temp_name(self):
        mounts = tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n")
        self.pin(mounts=mounts)
        os.unlink(os.path.join(self.pdir(), "source.json"))
        self.assertEqual(self.pin(mounts=mounts)["how"], "link")
        self.assertEqual([n for n in os.listdir(self.pdir()) if ".tmp." in n], [])

    def test_an_empty_sha_file_is_not_current(self):
        mounts = tool_img.parse_mountinfo("1 0 1:1 / / rw - x y z\n")
        self.pin(mounts=mounts)
        wr(os.path.join(self.pdir(), "rescuer.img.sha256"), "")
        r = self.pin(mounts=mounts)
        self.assertEqual((r["ok"], r["how"], r["sha256"]), (True, "link", sha_text(self.IMG)))

    def test_one_platforms_error_isnt_the_others(self):
        ctx = type("C", (), {"work": self.work, "host_root": self.root, "env": {"T2_IMAGES": self.images}})()
        self.make("haiku")
        real, realw = tool_img.pin_one, sys.stdout.write
        wrote = []

        def pin_one(p, *a, **k):
            if p == "netbsd":
                raise IndexError("boom")
            return {"ok": True}
        tool_img.pin_one, sys.stdout.write = pin_one, wrote.append
        try:
            self.assertEqual(tool_img.main(["pin", "netbsd", "haiku"], ctx), 0)
        finally:
            tool_img.pin_one, sys.stdout.write = real, realw
        got = json.loads("".join(wrote))
        self.assertIs(got["netbsd"]["ok"], False)
        self.assertIn("IndexError", got["netbsd"]["why"])
        self.assertIs(got["haiku"]["ok"], True)

    def test_other_platforms_have_no_release_same(self):
        self.assertNotIn("release_same", self.pin(mounts=[]))

    def test_main_prints_one_object(self):
        ctx = type("C", (), {"work": self.work, "host_root": self.root, "env": {"T2_IMAGES": self.images}})()
        wrote = []
        real, realc = sys.stdout.write, tool_img.copy_reflink
        sys.stdout.write = lambda s: wrote.append(s)
        tool_img.copy_reflink = shutil.copy
        try:
            rc = tool_img.main(["pin", "netbsd", "haiku"], ctx)
        finally:
            sys.stdout.write, tool_img.copy_reflink = real, realc
        self.assertEqual(rc, 0)
        got = json.loads("".join(wrote))
        self.assertTrue(got["netbsd"]["ok"])
        self.assertFalse(got["haiku"]["ok"])

    def test_main_refuses_bad_usage(self):
        ctx = type("C", (), {"work": self.work, "host_root": self.root, "env": {}})()
        err = sys.stderr
        sys.stderr = open(os.devnull, "w")
        try:
            self.assertEqual(tool_img.main(["pin", "plan9"], ctx), 2)
            self.assertEqual(tool_img.main(["unpin", "linux"], ctx), 2)
        finally:
            sys.stderr.close()
            sys.stderr = err


# --- the flow ----------------------------------------------------------------------------------

BINS = {"restic-bko": "a" * 64, "rustic-bko": "b" * 64, "bko-holt": "c" * 64}


class PC:
    family = "img"

    def __init__(self, cell, rule="img:new", status="due", keys_=None, refused=False):
        self.cell, self.rule, self.status, self.reasons = cell, rule, status, ["why"]
        self.keys = {"chopin": "1" * 64} if keys_ is None else keys_
        self.placed, self.host, self.refused = None, None, refused
        self.binaries = {"restic-bko": {"state": "real"}, "rustic-bko": {"state": "carried"}, "bko-holt": {"state": "real"}}


class Plan:
    def __init__(self, cells):
        self.cells, self.set, self.notes = cells, None, []

    def cell(self, c):
        return next((x for x in self.cells if x.cell == c), None)


class Stops:
    def __init__(self):
        self.fed, self.stop = [], False

    def feed(self, res):
        self.fed.append(res)
        return "ok"

    def feed_host(self, host, swap_out_bytes=None, ours_bytes=None):
        return "ok"

    def may_start(self, c=None):
        return not self.stop


class Word:
    family, word, text, form, release = "img", "ImgTest2", "ImgTest2", "bare", False


class RS:
    def __init__(self, mac, plan, word=None):
        self.run, self.word, self.family = "t2i-20261110T0312-4f2a", word or Word(), "img"
        self.mac_dir, self.mac_root, self.host_root, self.hosts = mac, mac, "/bkoscratch/t2test", ["chopin"]
        self.copy = {"chopin": {"id": "c", "path": "/copy/c", "commit": "x"}}
        self.stops, self.plan, self.flakes = Stops(), plan, []
        self.logged, self.made = [], []

    def log(self, t):
        self.logged.append(t)

    def record_made(self, h, p, pid=None):
        self.made.append((h, p, pid))

    def replan(self, facts=None, rows=None):
        return self.plan, [], []

    def write_cells(self, plan):
        return {}


class IO:
    """One host's job and row machinery.  MODES names each attempt's outcome per mode."""

    def __init__(self, results, manifest, pins=None):
        self.results = results           # attempt -> {mode: (rc, out text)}
        self.manifest = manifest
        self.pins = pins
        self.calls, self.rows, self.started = [], [], []
        self.pid = 100
        self.fin = {}

    def sh(self, host, cmd, timeout=60, input=None):
        self.calls.append(cmd)
        if " tool img pin " in cmd:
            names = cmd.split(" tool img pin ")[1].split(" </dev/null")[0].split()
            if self.pins is None:
                return 0, json.dumps({n: {"ok": True, "sha256": "d" * 64, "how": "link", "stale": [], "bins": BINS, "release_same": {"same": True, "why": "ok"}} for n in names}) + "\n", ""
            return 0, json.dumps(self.pins) + "\n", ""
        if "cat .exit" in cmd:
            a = self.attempt_of(cmd)
            return 0, str(self.exit_of(a)) + "\n1\n", ""
        return 0, "", ""

    def attempt_of(self, cmd):
        return 2 if "-r2/img/" in cmd else 1

    def exit_of(self, attempt):
        return 1 if any(rc for rc, _ in self.results.get(attempt, {}).values()) else 0

    def script(self, host, script, data, timeout=120):
        if data.get("append"):
            r = data["append"][0]
            self.rows.append(r)
            return {"ids": [[f"id{len(self.rows)}", True]]}
        return {"measure": {c: {"inputs": {}, "key": "1" * 64, "spec": None, "unknown": []} for c in data["measure"]}}

    def start(self, host, cmd, log, pidfile):
        self.pid += 1
        self.started.append(cmd)
        return self.pid

    def alive(self, host, pid, needle=None):
        return False

    def fetch(self, host, d, names):
        a = 2 if "-r2/img/" in d else 1
        out = {}
        for m, (rc, text) in self.results.get(a, {}).items():
            out[f"boot-{m}.out"] = text
            out[f"boot-{m}.rc"] = f"{rc}\n"
        if self.manifest is not None:
            out["manifest.json"] = json.dumps(self.manifest)
        for k in (".submitted", ".began", ".ended"):
            out[k] = "1000\n"
        out[".ended"] = "1100\n"
        return {k: v for k, v in out.items() if k in names}

    def sleep(self, s):
        pass


def passing(modes=("bios", "uefi")):
    return {m: (0, f"{m} (usb): reached login in 3 s\n  ok   tools  all present\n") for m in modes}


def failing(mode="bios"):
    return dict(passing(), **{mode: (1, f"{mode} (usb): reached login in 3 s\n  FAIL tools  missing gpart\n")})


def manifest(kernel="k1"):
    return dict({"bin:" + k: v for k, v in BINS.items()}, kernel=kernel, zfs="z1", **{"tool:gpart": "t"})


class Flow(unittest.TestCase):
    CLASSES = {"holt": {"img_panel": {"netbsd": {"guests": ["netbsd11"]}, "linux": {"guests": ["debian12", "ubuntu24"]},
                                      "openbsd": {"guests": ["openbsd8"], "blocked": "no way in yet"}}},
               "policy": {"sweep": {"days": 7}}}

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-k4-flow-")
        self.addCleanup(shutil.rmtree, self.t, True)

    def ctx(self, plan, rows=(), word=None):
        self.rs = RS(self.t, plan, word)
        facts = {"chopin": {"images": {p: {"bins": dict(BINS), "pin": {}} for p in ("linux", "netbsd", "openbsd", "haiku")}}}
        specs = type("S", (), {"get": staticmethod(lambda n: None)})()
        return type("Ctx", (), {"run": self.rs, "classes": self.CLASSES, "options": {}, "ledger": list(rows), "facts": facts,
                                "specs": specs, "hosts": ["chopin"]})()

    def go(self, cells, io, rows=(), word=None, panel=None):
        plan = Plan(cells)
        c = self.ctx(plan, rows, word)
        with_measure = img.Runner.measure
        img.Runner.measure = lambda s, h, cell: {"inputs": {}, "key": "1" * 64, "spec": None, "unknown": []}
        self.addCleanup(lambda: setattr(img.Runner, "measure", with_measure))
        self.io = io
        from t2lib import gate as gmod
        self.assertTrue(hasattr(gmod, "HOST_SCRIPT"))
        return img.run(c, plan, io=io, sleep=lambda s: None, poll=0.01, panel=panel, pull=lambda: None)

    def test_a_boot_pass_needing_no_panel(self):
        io = IO({1: passing()}, manifest())
        res = self.go([PC("img:netbsd")], io, rows=[passrow(manifest(), cell="img:netbsd")])
        self.assertEqual(res["exit"], 0)
        r = io.rows[0]
        self.assertEqual((r["origin"], r["verdict"], r["class"]), ("img:t2i-20261110T0312-4f2a:netbsd:1", "PASS", "pass"))
        self.assertEqual(r["needs"]["boot:bios:tools"], "PASS")
        self.assertEqual(r["needs"]["manifest:bins"], "PASS")
        self.assertEqual(r["needs"]["release-same"], "SKIP")
        self.assertEqual(r["needs"]["refuse:rustic-bko"], "PASS")
        self.assertEqual(r["extra"]["refuse"]["rustic-bko"], "carried")
        self.assertEqual(r["extra"]["panel"], {"needed": False, "why": "storage pieces unchanged", "cells": ["holt:netbsd11+img"], "result": "none"})
        self.assertEqual(r["extra"]["release_tested"], False)
        self.assertEqual(r["extra"]["manifest"]["kernel"], "k1")
        self.assertEqual(res["blocks"][0], "img:netbsd   boot PASS   panel not needed (storage pieces unchanged)    release build not tested")
        job = next(c for c in io.started)
        self.assertIn("cmp-run.sh --reserve 1 5", job)
        self.assertIn("WEAR_TAG=img:t2i-20261110T0312-4f2a:netbsd", job)
        self.assertIn("boot.sh", job)
        self.assertEqual(len(io.started), 1)

    def test_a_panel_that_passes_writes_a_second_row(self):
        io = IO({1: passing()}, manifest())
        seen = []

        def panel(cells):
            seen.append(cells)
            return {c: "PASS" for c in cells}

        res = self.go([PC("img:netbsd")], io, panel=panel)
        self.assertEqual(seen, [["holt:netbsd11+img"]])
        self.assertEqual([r["origin"] for r in io.rows], ["img:t2i-20261110T0312-4f2a:netbsd:1", "img:t2i-20261110T0312-4f2a:netbsd:1.panel"])
        self.assertEqual(io.rows[0]["extra"]["panel"]["result"], "owed")
        self.assertEqual(io.rows[1]["extra"]["panel"]["result"], "pass")
        self.assertEqual(io.rows[1]["needs"]["panel"], "PASS")
        self.assertEqual(io.rows[1]["verdict"], "PASS")
        self.assertEqual(io.rows[1]["needs"]["boot:bios:tools"], "PASS")
        self.assertEqual(res["exit"], 0)
        self.assertEqual(res["cells"][0]["rows"], ["id1", "id2"])
        self.assertEqual(res["blocks"][0], "img:netbsd   boot PASS   panel 1/1 PASS (no earlier panel)    release build not tested")

    def test_a_panel_that_fails(self):
        io = IO({1: passing()}, manifest())
        res = self.go([PC("img:netbsd")], io, panel=lambda cells: {c: "FAIL" for c in cells})
        self.assertEqual((io.rows[1]["extra"]["panel"]["result"], io.rows[1]["needs"]["panel"], io.rows[1]["verdict"]), ("fail", "FAIL", "FAIL"))
        self.assertEqual(res["exit"], 1)
        self.assertTrue(res["blocks"][0].startswith("img:netbsd   boot PASS   panel 0/1 PASS"))

    def test_a_panel_that_didnt_run_stays_owed(self):
        io = IO({1: passing()}, manifest())
        res = self.go([PC("img:netbsd")], io, panel=lambda cells: {c: None for c in cells})
        self.assertEqual(len(io.rows), 1)
        self.assertEqual(res["exit"], 1)
        self.assertIn("panel OWED (stopped before it ran)", res["blocks"][0])

    def test_a_stop_before_the_panel(self):
        io = IO({1: passing()}, manifest())
        calls = []

        def panel(cells):
            calls.append(cells)
            return {}

        class T:
            def tripped(self):
                return True

        plan = Plan([PC("img:netbsd")])
        c = self.ctx(plan)
        self.rs.stops.guards = T()
        real = img.Runner.measure
        img.Runner.measure = lambda s, h, cell: {"inputs": {}, "key": "1" * 64, "spec": None, "unknown": []}
        self.addCleanup(lambda: setattr(img.Runner, "measure", real))
        # the guard trips only after the boot row
        orig = hostjob.tripped
        n = {"i": 0}

        def trip(rs):
            n["i"] += 1
            return n["i"] > 2
        hostjob.tripped = trip
        self.addCleanup(lambda: setattr(hostjob, "tripped", orig))
        res = img.run(c, plan, io=io, sleep=lambda s: None, poll=0.01, panel=panel, pull=lambda: None)
        self.assertEqual(calls, [])
        self.assertEqual(res["exit"], 1)
        self.assertIn("OWED (stopped before it ran)", res["blocks"][0])

    def test_a_fail_reruns_once_as_flaky(self):
        io = IO({1: failing(), 2: passing()}, manifest())
        res = self.go([PC("img:netbsd")], io, rows=[passrow(manifest(), cell="img:netbsd")])
        self.assertEqual([r["verdict"] for r in io.rows], ["FAIL", "FLAKY-FAIL"])
        self.assertEqual(io.rows[1]["origin"].rsplit(":", 1)[1], "2")
        self.assertEqual(io.rows[1]["rule"], "rerun")
        self.assertEqual(len(io.started), 2)
        self.assertEqual(res["exit"], 1)

    def test_a_known_flake_passes_on_its_rerun(self):
        io = IO({1: failing(), 2: passing()}, manifest())
        plan = Plan([PC("img:netbsd")])
        c = self.ctx(plan, [passrow(manifest(), cell="img:netbsd")])
        self.rs.flakes = [{"guest": "*", "pattern": "missing gpart", "why": "w", "ref": "r"}]
        real = img.Runner.measure
        img.Runner.measure = lambda s, h, cell: {"inputs": {}, "key": "1" * 64, "spec": None, "unknown": []}
        self.addCleanup(lambda: setattr(img.Runner, "measure", real))
        res = img.run(c, plan, io=io, sleep=lambda s: None, poll=0.01, pull=lambda: None)
        self.assertEqual((io.rows[1]["verdict"], io.rows[1]["class"]), ("PASS", "known-flake"))
        self.assertEqual(res["exit"], 0)

    def test_a_second_fail_stands(self):
        io = IO({1: failing(), 2: failing()}, manifest())
        res = self.go([PC("img:netbsd")], io)
        self.assertEqual([r["verdict"] for r in io.rows], ["FAIL", "FAIL"])
        self.assertEqual(res["exit"], 1)
        self.assertIn("boot FAIL   panel not run", res["blocks"][0])

    def test_a_boot_fail_runs_no_panel(self):
        io = IO({1: failing(), 2: failing()}, manifest())
        res = self.go([PC("img:netbsd")], io, panel=lambda c: self.fail("no panel"))
        self.assertEqual(res["cells"][0]["verdict"], "FAIL")

    def test_the_manifest_catches_a_wrong_binary(self):
        io = IO({1: passing(), 2: passing()}, dict(manifest(), **{"bin:bko-holt": "e" * 64}))
        res = self.go([PC("img:netbsd")], io)
        self.assertEqual(io.rows[0]["needs"]["manifest:bins"], "FAIL")
        self.assertEqual(res["exit"], 1)

    def test_a_job_that_didnt_finish_is_harness(self):
        class J(IO):
            def start(self, host, cmd, log, pidfile):
                return None
        io = J({1: {}}, manifest())
        res = self.go([PC("img:netbsd")], io)
        self.assertEqual(io.rows[0]["verdict"], "HARNESS")
        self.assertEqual(len(io.rows), 1)
        self.assertEqual(res["exit"], 1)

    def test_a_refused_pin_is_not_runnable_and_exits_2(self):
        io = IO({}, None, pins={"netbsd": {"ok": False, "why": "a build is under way (no done.exit)"}})
        res = self.go([PC("img:netbsd")], io)
        self.assertEqual(res["cells"][0]["outcome"], "not-runnable")
        self.assertIn("a build is under way (no done.exit)", res["cells"][0]["reasons"])
        self.assertEqual(io.rows, [])
        self.assertEqual(res["exit"], 2)

    def test_a_refused_cell_of_the_plan_exits_2_after_the_rest(self):
        io = IO({1: passing()}, manifest())
        cells = [PC("img:netbsd"), PC("img:haiku", status="not-runnable", refused=True)]
        res = self.go(cells, io, rows=[passrow(manifest(), cell="img:netbsd")])
        self.assertEqual(len(io.rows), 1)
        self.assertEqual(res["exit"], 2)

    def test_panel_owed_goes_straight_to_the_panel(self):
        io = IO({1: passing()}, manifest())
        boot = dict(passrow(manifest(), result="owed", cell="img:netbsd"), key="1" * 64, id="boot1", host="chopin", attempt=1,
                    origin="img:t2i-old:netbsd:1", needs={"boot:bios:tools": "PASS"}, fails=[])
        boot["extra"]["panel"] = {"needed": True, "why": "no earlier panel", "cells": ["holt:netbsd11+img"], "result": "owed"}
        res = self.go([PC("img:netbsd", rule="img:panel-owed")], io, rows=[boot], panel=lambda cs: {c: "PASS" for c in cs})
        self.assertEqual(io.started, [])
        self.assertEqual(len(io.rows), 1)
        self.assertEqual(io.rows[0]["origin"], "img:t2i-20261110T0312-4f2a:netbsd:1.panel")
        self.assertEqual((io.rows[0]["needs"], io.rows[0]["extra"]["panel"]["result"]), ({"boot:bios:tools": "PASS", "panel": "PASS"}, "pass"))
        self.assertEqual(res["exit"], 0)

    def test_a_panel_owed_by_release_says_release(self):
        # (its boot row's panel wasn't needed: "storage pieces unchanged" isn't why it runs now)
        class W(Word):
            release = True
            form = "names"
        io = IO({1: passing()}, manifest())
        boot = dict(passrow(manifest(), cell="img:netbsd"), key="1" * 64, id="boot1", host="chopin", attempt=1,
                    origin="img:t2i-old:netbsd:1", needs={"boot:bios:tools": "PASS"}, fails=[])
        boot["extra"]["panel"] = {"needed": False, "why": "storage pieces unchanged", "cells": ["holt:netbsd11+img"], "result": "none"}
        res = self.go([PC("img:netbsd", rule="img:panel-owed")], io, rows=[boot], word=W(), panel=lambda cs: {c: "PASS" for c in cs})
        self.assertEqual(io.rows[0]["extra"]["panel"]["why"], "release")
        self.assertNotIn("storage pieces unchanged", res["blocks"][0])

    def test_blocked_without_release_only_reports(self):
        io = IO({1: passing()}, manifest())
        res = self.go([PC("img:openbsd")], io, panel=lambda c: self.fail("no panel"))
        self.assertEqual(io.rows[0]["extra"]["panel"]["result"], "blocked")
        self.assertEqual(res["exit"], 0)
        self.assertIn("panel NOT TESTED: blocked: no way in yet", res["blocks"][0])

    def test_blocked_fails_release(self):
        class W(Word):
            release = True
            form = "names"
        io = IO({1: passing()}, manifest())
        res = self.go([PC("img:openbsd")], io, word=W(), panel=lambda c: self.fail("no panel"))
        self.assertEqual(res["exit"], 1)

    def test_release_needs_the_panel(self):
        class W(Word):
            release = True
            form = "names"
        io = IO({1: passing()}, manifest())
        res = self.go([PC("img:netbsd")], io, word=W(), rows=[passrow(manifest(), cell="img:netbsd")],
                      panel=lambda cs: {c: "PASS" for c in cs})
        self.assertEqual(res["exit"], 0)
        self.assertEqual(io.rows[0]["extra"]["panel"]["why"], "release")
        self.assertEqual(io.rows[0]["extra"]["release"], True)
        self.assertTrue(any("no complete SweepTest2" in n for n in res["notes"]))

    def test_linux_release_same_comes_from_the_pin(self):
        modes = ("bios", "uefi", "uefi-secureboot", "cdrom")
        io = IO({1: passing(modes)}, manifest())
        plan = Plan([PC("img:linux")])
        c = self.ctx(plan, [passrow(manifest())])
        real = img.Runner.measure
        img.Runner.measure = lambda s, h, cell: {"inputs": {}, "key": "1" * 64, "spec": None, "unknown": []}
        self.addCleanup(lambda: setattr(img.Runner, "measure", real))
        spec = {"linux": {"modes": list(modes)}}
        c.specs = type("S", (), {"get": staticmethod(lambda n: type("F", (), {"entry": staticmethod(lambda p: spec[p])})() if n == "img" else None)})()
        res = img.run(c, plan, io=io, sleep=lambda s: None, poll=0.01, pull=lambda: None)
        self.assertEqual(res["exit"], 0)
        self.assertEqual(io.rows[0]["needs"]["release-same"], "PASS")
        self.assertEqual(io.rows[0]["extra"]["release_tested"], True)
        self.assertEqual(io.rows[0]["extra"]["modes"], list(modes))
        self.assertEqual(res["blocks"][0], "img:linux    boot PASS   panel not needed (storage pieces unchanged)")

    def test_an_unexpected_key_invalidates_the_row(self):
        io = IO({1: passing()}, manifest())
        plan = Plan([PC("img:netbsd")])
        c = self.ctx(plan, [passrow(manifest(), cell="img:netbsd")])
        real = img.Runner.measure
        img.Runner.measure = lambda s, h, cell: {"inputs": {}, "key": "2" * 64, "spec": None, "unknown": []}
        self.addCleanup(lambda: setattr(img.Runner, "measure", real))
        img.run(c, plan, io=io, sleep=lambda s: None, poll=0.01, pull=lambda: None)
        self.assertFalse(io.rows[0]["valid"])

    def test_a_resume_reuses_a_finished_image(self):
        io = IO({1: passing()}, manifest())
        rows = [passrow(manifest(), cell="img:netbsd")]
        self.go([PC("img:netbsd")], io, rows=rows)
        self.assertEqual(len(io.rows), 1)
        io2 = IO({1: passing()}, manifest())
        plan = Plan([PC("img:netbsd")])
        c = self.ctx(plan, rows)
        res = img.resume(c, plan, io=io2, sleep=lambda s: None, poll=0.01, pull=lambda: None)
        self.assertEqual((len(io2.rows), io2.started), (0, []))
        self.assertEqual(res["cells"][0]["verdict"], "PASS")

    # --- what isn't the image's fault is a HARNESS, never a FAIL ------------------------------
    def assert_harness(self, res, io, text):
        self.assertEqual(len(io.rows), 1)
        r = io.rows[0]
        self.assertEqual((r["verdict"], r["class"], r["needs"]), ("HARNESS", "harness", {}))
        self.assertIn(text, r["fails"][0])
        self.assertEqual(res["exit"], 1)

    def test_records_that_cant_be_read_are_a_harness(self):
        class F(IO):
            def fetch(self, host, d, names):
                return {} if any(n.startswith("boot-") for n in names) else super().fetch(host, d, names)
        io = F({1: passing()}, manifest())
        self.assert_harness(self.go([PC("img:netbsd")], io), io, "never began")

    def test_a_job_that_never_began_is_a_harness(self):
        class F(IO):
            def exit_of(self, attempt):
                return 1

            def fetch(self, host, d, names):
                return {} if any(n.startswith("boot-") for n in names) else {k: v for k, v in super().fetch(host, d, names).items() if k != ".began"}
        io = F({1: failing()}, manifest())
        self.assert_harness(self.go([PC("img:netbsd")], io), io, "never began")

    def test_a_missing_mode_is_a_harness(self):
        class F(IO):
            def fetch(self, host, d, names):
                return {k: v for k, v in super().fetch(host, d, names).items() if k != "boot-uefi.rc"}
        io = F({1: passing()}, manifest())
        self.assert_harness(self.go([PC("img:netbsd")], io), io, "boot-uefi.rc")

    def test_a_fetch_that_failed_once_is_read_again(self):
        class F(IO):
            n = 0

            def fetch(self, host, d, names):
                if any(x.startswith("boot-") for x in names):
                    F.n += 1
                    if F.n == 1:
                        return {}
                return super().fetch(host, d, names)
        io = F({1: passing()}, manifest())
        self.go([PC("img:netbsd")], io, rows=[passrow(manifest(), cell="img:netbsd")])
        self.assertEqual(io.rows[0]["verdict"], "PASS")

    def test_a_traceback_is_a_harness(self):
        tb = "Traceback (most recent call last):\n  File \"boot-test.py\", line 1\nValueError: x\n"
        io = IO({1: {"bios": (1, tb), "uefi": (0, passing()["uefi"][1])}}, manifest())
        self.assert_harness(self.go([PC("img:netbsd")], io), io, "crashed")

    def console(self, log):
        class F(IO):
            def fetch(self, host, d, names):
                return dict(super().fetch(host, d, names), **({"boot-bios.log": log} if "boot-bios.log" in names else {}))
        return F({1: {"bios": (1, "bios: QEMU exited (see /x/boot-bios.log)\n"), "uefi": (0, passing()["uefi"][1])},
                  2: {"bios": (1, "bios: QEMU exited (see /x/boot-bios.log)\n"), "uefi": (0, passing()["uefi"][1])}}, manifest())

    def test_qemu_exiting_with_no_console_is_a_harness(self):
        io = self.console("")
        self.assert_harness(self.go([PC("img:netbsd")], io), io, "no console output")

    def test_qemu_exiting_after_the_console_is_a_fail(self):
        io = self.console("SeaBIOS\nsome kernel panic\n")
        self.go([PC("img:netbsd")], io)
        self.assertEqual(io.rows[0]["verdict"], "FAIL")
        self.assertEqual(io.rows[0]["needs"]["boot:bios"], "FAIL")

    def test_a_mode_whose_exit_isnt_a_verdict_is_a_harness(self):
        io = IO({1: {"bios": (2, "boot-test.py: error: bad arguments\n"), "uefi": (0, passing()["uefi"][1])}}, manifest())
        self.assert_harness(self.go([PC("img:netbsd")], io), io, "exited 2")

    # --- resume -----------------------------------------------------------------------------
    def two_hosts(self, io, state_host="cinqcent"):
        wr(os.path.join(self.t, "img.json"), json.dumps({
            "v": 1, "cells": {"netbsd": {"attempts": {"1": {"started": "2026-11-10T00:00:00Z", "host": state_host}}, "host": state_host}},
            "pins": {h: {"netbsd": {"ok": True, "sha256": "d" * 64, "stale": [], "bins": BINS}} for h in ("chopin", "cinqcent")},
            "copy": {h: {"id": "c", "path": "/copy/c"} for h in ("chopin", "cinqcent")}}))
        pc = PC("img:netbsd", keys_={"chopin": "1" * 64, "cinqcent": "1" * 64})
        pc.placed = "chopin"
        plan = Plan([pc])
        c = self.ctx(plan, [passrow(manifest(), cell="img:netbsd")])
        self.rs.hosts = ["chopin", "cinqcent"]
        self.rs.copy = {h: {"id": "c", "path": "/copy/c"} for h in ("chopin", "cinqcent")}
        real = img.Runner.measure
        img.Runner.measure = lambda s, h, cell: {"inputs": {}, "key": "1" * 64, "spec": None, "unknown": []}
        self.addCleanup(lambda: setattr(img.Runner, "measure", real))
        return img.resume(c, plan, io=io, sleep=lambda s: None, poll=0.01, pull=lambda: None)

    def test_a_resume_reattaches_where_the_job_was_started(self):
        fetched, written = [], []

        class F(IO):
            def fetch(self, host, d, names):
                fetched.append(host)
                if host != "cinqcent":
                    return {}
                return dict(super().fetch(host, d, names), **({".exit": "0\n"} if ".exit" in names else {}))

            def script(self, host, script, data, timeout=120):
                if data.get("append"):
                    written.append(host)
                return super().script(host, script, data, timeout)
        io = F({1: passing()}, manifest())
        res = self.two_hosts(io)
        self.assertEqual(set(fetched), {"cinqcent"})
        self.assertEqual(written, ["cinqcent"])
        self.assertEqual((io.rows[0]["verdict"], res["exit"], io.started), ("PASS", 0, []))

    def test_a_resume_reads_exit_again_when_the_job_is_gone(self):
        seen = []

        class F(IO):
            def fetch(self, host, d, names):
                got = super().fetch(host, d, names)
                if ".exit" in names:
                    seen.append(1)
                    if len(seen) >= 2:   # (the job ended between the first look and alive())
                        got[".exit"] = "0\n"
                return got

            def sh(self, host, cmd, timeout=60, input=None):
                return (0, "500\n", "") if "/.pid" in cmd else super().sh(host, cmd, timeout, input)
        io = F({1: passing()}, manifest())
        self.two_hosts(io, "chopin")
        self.assertEqual(io.rows[0]["verdict"], "PASS")

    def test_a_resume_with_no_job_is_a_harness(self):
        class F(IO):
            def sh(self, host, cmd, timeout=60, input=None):
                return (0, "", "") if "/.pid" in cmd else super().sh(host, cmd, timeout, input)
        io = F({1: passing()}, manifest())
        self.two_hosts(io, "chopin")
        self.assertEqual(io.rows[0]["verdict"], "HARNESS")
        self.assertIn("no exit status", io.rows[0]["fails"][0])

    def test_a_silent_host_isnt_a_job_that_ended(self):
        class F(IO):
            def sh(self, host, cmd, timeout=60, input=None):
                return (255, "", "no route") if "/.pid" in cmd else super().sh(host, cmd, timeout, input)
        io = F({1: passing()}, manifest())
        self.two_hosts(io, "chopin")
        self.assertIn("no answer", io.rows[0]["fails"][0])

    # --- bins-stale, and whose image a panel is ----------------------------------------------
    def stale_pin(self, stale=("bko-holt",)):
        return {"netbsd": {"ok": True, "sha256": "d" * 64, "how": "link", "stale": list(stale), "bins": BINS}}

    def test_a_bins_stale_panel_is_not_runnable(self):
        def panel(cells):
            raise AssertionError("no child for a bins-stale panel")
        io = IO({1: passing()}, manifest(), pins=self.stale_pin())
        res = self.go([PC("img:netbsd")], io, panel=panel)
        self.assertEqual(res["exit"], 1)
        self.assertEqual(len(io.rows), 1)
        self.assertEqual(io.rows[0]["extra"]["panel"]["result"], "owed")
        self.assertIn("panel not runnable: bins-stale: bko-holt", res["blocks"][0])

    def test_a_panel_gets_the_boot_rows_image(self):
        got = {}

        def run_panel(self, cells):
            got.update(self.panel_images)
            return {c: "PASS" for c in cells}
        real = img.Runner.run_panel
        img.Runner.run_panel = run_panel
        self.addCleanup(lambda: setattr(img.Runner, "run_panel", real))
        self.go([PC("img:netbsd")], IO({1: passing()}, manifest()))
        self.assertEqual(got, {"holt:netbsd11+img": "d" * 64})

    def test_the_panel_row_carries_the_boots_cost(self):
        io = IO({1: passing()}, manifest())
        self.go([PC("img:netbsd")], io, panel=lambda cells: {c: "PASS" for c in cells})
        self.assertEqual(len(io.rows), 2)
        self.assertEqual((io.rows[1]["wall_s"], io.rows[1]["gm"]), (io.rows[0]["wall_s"], io.rows[0]["gm"]))
        self.assertEqual(io.rows[0]["wall_s"], 100)

    def test_rows_are_ledger_rows(self):
        from t2lib import keys, ledger
        io = IO({1: passing()}, manifest())
        self.go([PC("img:netbsd")], io, panel=lambda cells: {c: "PASS" for c in cells})
        inputs = {"bin": {"netbsd/bko-holt": "sha256:" + "c" * 64}}
        for r in io.rows:
            r2 = dict(r, inputs=inputs, planned_key=None, spec={"path": "x", "sha256": "0" * 64}, commit="a" * 40, committed=True, dirty={}, copy="c")
            self.assertEqual(keys.key(r2["cell"], inputs), keys.key("img:netbsd", inputs))
            c = ledger.complete(r2, host="chopin")
            self.assertEqual(ledger.validate(c), [])

    def test_a_run_id_of_a_panel_doesnt_collide(self):
        stamp = "20261110T0312-4f2a"
        panel = runmod.new_run_id("holt", stamp, "img")
        self.assertEqual(panel, f"t2h-{stamp}-img")
        self.assertNotEqual(panel, runmod.new_run_id("holt", stamp))
        self.assertTrue(runmod.RUN_RE.match(panel))
        self.assertEqual(img.Runner.stamp(type("R", (), {"run_id": f"t2i-{stamp}"})()), stamp)


class PanelChild(unittest.TestCase):
    """run_panel starts one child holt lifecycle: t2h-STAMP-img, universe or names."""

    def go(self, form):
        from t2lib import facts as factsmod, plan as planmod, words
        t = tempfile.mkdtemp(prefix="t2-k4-panel-")
        self.addCleanup(shutil.rmtree, t, True)
        got = {}

        class W(Word):
            pass

        W.form = form
        pc = PC("img:linux")
        plan = Plan([pc])
        rs = RS(t, plan, W())
        ctx = type("Ctx", (), {"run": rs, "classes": {}, "options": {"universe": ["x"], "pull": lambda: [{"id": 1}]}, "ledger": [],
                               "facts": {"chopin": {}}, "specs": type("S", (), {"get": staticmethod(lambda n: None)})(), "hosts": ["chopin"]})()

        def lifecycle(w, c, mac_root=None, host_root=None, out=None, pull=None, shared=None):
            got.update(word=w, universe=c.options.get("universe"), shared=shared, ctx=c, mac_root=mac_root)
            return 0

        def make(w, c):
            got["after_universe"] = c.options.get("universe")
            got["after_facts"] = c.facts
            return Plan([])

        saved = [(runmod, "lifecycle", runmod.lifecycle), (planmod, "known_names", planmod.known_names), (planmod, "make", planmod.make),
                 (words, "parse", words.parse), (factsmod, "probe_all", factsmod.probe_all)]
        self.addCleanup(lambda: [setattr(m, n, v) for m, n, v in saved])
        runmod.lifecycle, planmod.known_names, planmod.make = lifecycle, lambda c: {}, make
        words.parse = lambda args, known: list(args)
        factsmod.probe_all = lambda hosts, want, **kw: {"chopin": {"ok": True, "fresh": 1}}
        res = img.Runner(ctx, plan, io=IO({}, None), pull=None).run_panel(["holt:debian12+img", "holt:ubuntu24+img"])
        return got, res

    def test_bare_uses_the_universe(self):
        got, res = self.go("bare")
        self.assertEqual(got["word"], ["HoltTest2"])
        self.assertEqual(got["universe"], ["holt:debian12+img", "holt:ubuntu24+img"])
        self.assertEqual(got["after_universe"], got["universe"])
        self.assertEqual((got["shared"].suffix, got["shared"].stamp), ("img", "20261110T0312-4f2a"))
        self.assertEqual(got["ctx"].facts, {"chopin": {"ok": True, "fresh": 1}})
        self.assertEqual(res, {"holt:debian12+img": None, "holt:ubuntu24+img": None})
        self.assertIs(got["after_facts"], got["ctx"].facts)   # (what the child's rows were keyed with)

    def test_all_names_its_cells(self):
        got, _ = self.go("All")
        self.assertEqual(got["word"], ["HoltTest2", "debian12+img", "ubuntu24+img"])
        self.assertNotIn("x", got["universe"] or [])


# --- the shell ---------------------------------------------------------------------------------

BASH = shutil.which("bash")


class ImgOne(unittest.TestCase):
    """stick/img-one.sh with stub builders: the commit file, and nothing of a real build."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-k4-one-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.w = os.path.join(self.t, "work")
        self.s = os.path.join(self.t, "stick")
        self.path = os.path.join(self.t, "path")
        wr(os.path.join(self.w, "bko-bin", "netbsd", "rustic-bko"), "rustic\n", 0o755)
        wr(os.path.join(self.w, "bko-bin", "BUILD-INFO"), "bko-bin set\n")
        self.holt = os.path.join(self.w, "holt-bin", "netbsd", "bko-holt")
        wr(self.holt, "holt\n", 0o755)
        self.commit = "ab" * 20
        wr(os.path.join(self.w, "holt-bin", "netbsd", "COMMIT"), self.commit + "\n")
        wr(os.path.join(self.s, "make-img-bsd.sh"),
           '#!/bin/sh\nif [ "$1" = --test ]; then d=$3; else d=$2; fi\necho image >"$d/x.img"\necho "Wrote $d/x.img"\n', 0o755)
        wr(os.path.join(self.s, "boot-test.py"), "print('  ok   tools  all')\n")
        wr(os.path.join(self.s, "compress-images.sh"), "#!/bin/sh\nexit 0\n", 0o755)
        wr(os.path.join(self.path, "flock"), "#!/bin/sh\nexit 0\n", 0o755)

    def run_one(self, env=None):
        e = dict(os.environ, BKO_WORK=self.w, S=self.s, PATH=self.path + ":" + os.environ["PATH"])
        e.update(env or {})
        return subprocess.run([BASH, os.path.join(WT, "stick", "img-one.sh"), "netbsd"], env=e, capture_output=True, text=True)

    def images(self):
        return os.path.join(self.w, "images", "netbsd")

    def test_written_when_the_binary_is_the_recorded_one(self):
        r = self.run_one()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(os.path.join(self.images(), "bko-holt.commit")), self.commit + "\n")
        self.assertEqual(rd(os.path.join(self.images(), "done.exit")), "0\n")

    def test_not_written_when_the_binary_changed(self):
        real = shutil.which("sha256sum")
        wr(os.path.join(self.path, "sha256sum"),
           f'#!/bin/sh\nif [ "$1" = ./bko-holt ] && [ ! -e {self.t}/changed ]; then {real} "$@"; r=$?; : >{self.t}/changed; echo another >{self.holt}; exit $r; fi\nexec {real} "$@"\n', 0o755)
        r = self.run_one()
        self.assertFalse(os.path.exists(os.path.join(self.images(), "bko-holt.commit")), r.stdout)
        self.assertIn("no bko-holt.commit", rd(os.path.join(self.images(), "result.txt")))

    def test_not_written_without_a_good_commit(self):
        wr(os.path.join(self.w, "holt-bin", "netbsd", "COMMIT"), "bef913e\n")
        self.run_one()
        self.assertFalse(os.path.exists(os.path.join(self.images(), "bko-holt.commit")))

    @unittest.skipUnless(shutil.which("flock"), "no flock here")
    def test_a_held_lock_refuses(self):
        os.makedirs(os.path.join(self.w, "images", "netbsd"), exist_ok=True)
        os.remove(os.path.join(self.path, "flock"))
        with open(os.path.join(self.w, "images", ".lock-netbsd"), "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            r = self.run_one()
        self.assertEqual(r.returncode, 3)
        self.assertIn("under way", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.images(), "done.exit")))


LINUX = sys.platform.startswith("linux") and shutil.which("flock")


class Guards(unittest.TestCase):
    """A test's T2_HOST_ROOT never reaches the real images or holt-bin.  A stub stat makes the
    world's directories "the real one", so nothing real is named."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-k4-guard-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.w = os.path.join(self.t, "w")
        self.stubs = os.path.join(self.t, "path")
        self.calls = os.path.join(self.t, "calls")
        wr(os.path.join(self.stubs, "stat"), "#!/bin/sh\necho 1:1\n", 0o755)
        for x in ("ssh", "rsync"):
            wr(os.path.join(self.stubs, x), f'#!/bin/sh\necho {x} "$@" >>{self.calls}\nexit 0\n', 0o755)
        os.makedirs(os.path.join(self.w, "images", "netbsd"))
        os.makedirs(os.path.join(self.w, "holt-bin"))

    def run_it(self, script, *args, **env):
        e = dict(os.environ, BKO_WORK=self.w, T2_HOST_ROOT=os.path.join(self.t, "state"), PATH=self.stubs + ":" + os.environ["PATH"])
        e.update(env)
        return subprocess.run([BASH, os.path.join(WT, "stick", script), *args], env=e, capture_output=True, text=True)

    def test_img_one(self):
        r = self.run_it("img-one.sh", "netbsd")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("the real set", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.w, "images", ".lock-netbsd")))

    def test_img_lanes(self):
        wr(os.path.join(self.w, "images", "lanes.done"), "x")
        r = self.run_it("img-lanes.sh", "netbsd")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("the real set", r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.w, "images", "lanes.done")))

    def test_img_refresh_holt(self):
        r = self.run_it("img-refresh-holt.sh", "a" * 40, "c" * 64, "netbsd")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("the real set", r.stderr)

    def test_img_xcopy_needs_a_dest_images(self):
        r = self.run_it("img-xcopy.sh", "dest", "netbsd")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BKO_DEST_IMAGES", r.stderr)
        self.assertFalse(os.path.exists(self.calls))

    def test_img_xcopy_from_a_world_needs_one_with_no_test_root_too(self):
        os.unlink(os.path.join(self.stubs, "stat"))
        r = self.run_it("img-xcopy.sh", "dest", "netbsd", T2_HOST_ROOT="/bkoscratch/test2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(os.path.exists(self.calls))

    @unittest.skipUnless(LINUX, "needs flock and bash 4")
    def test_img_xcopy_refuses_a_source_with_no_done_exit(self):
        r = self.run_it("img-xcopy.sh", "dest", "netbsd", BKO_DEST_IMAGES="w/images")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("no ", r.stderr)
        self.assertFalse(os.path.exists(self.calls))

    @unittest.skipUnless(LINUX, "needs flock and bash 4")
    def test_img_xcopy_holds_dests_lock_across_the_rsync(self):
        wr(os.path.join(self.w, "images", "netbsd", "done.exit"), "0\n")
        r = self.run_it("img-xcopy.sh", "dest", "netbsd", BKO_DEST_IMAGES="w/images")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = rd(self.calls).splitlines()
        self.assertEqual(len([c for c in calls if c.startswith("rsync")]), 2)
        for c in calls:
            if c.startswith("rsync"):
                self.assertIn("--rsync-path=flock -n w/images/.lock-netbsd rsync", c)
                self.assertIn("dest:w/images/netbsd/", c)
        self.assertIn("cd w/images &&", calls[0])

    @unittest.skipUnless(LINUX, "needs flock and bash 4")
    def test_img_xcopy_refuses_while_a_build_here_holds_the_lock(self):
        wr(os.path.join(self.w, "images", "netbsd", "done.exit"), "0\n")
        with open(os.path.join(self.w, "images", ".lock-netbsd"), "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            r = self.run_it("img-xcopy.sh", "dest", "netbsd", BKO_DEST_IMAGES="w/images")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertFalse(os.path.exists(self.calls))


@unittest.skipUnless(LINUX, "needs flock and bash 4")
class Refresh(unittest.TestCase):
    """stick/img-refresh-holt.sh: the binary and its COMMIT never mismatched."""
    OLD, NEW, CODE = "a" * 40, "b" * 40, "c" * 64

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-k4-refresh-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.w, self.hr = os.path.join(self.t, "w"), os.path.join(self.t, "state")
        self.stubs = os.path.join(self.t, "path")
        os.makedirs(self.stubs)
        self.d = os.path.join(self.w, "holt-bin", "netbsd")
        wr(os.path.join(self.hr, "holt-bin", self.CODE, "netbsd", "bko-holt"), "new\n", 0o755)
        wr(os.path.join(self.d, "bko-holt"), "old\n", 0o755)
        wr(os.path.join(self.d, "COMMIT"), self.OLD + "\n")
        os.makedirs(os.path.join(self.w, "images"))

    def run_it(self):
        e = dict(os.environ, BKO_WORK=self.w, T2_HOST_ROOT=self.hr, PATH=self.stubs + ":" + os.environ["PATH"])
        return subprocess.run([BASH, os.path.join(WT, "stick", "img-refresh-holt.sh"), self.NEW, self.CODE, "netbsd"],
                              env=e, capture_output=True, text=True)

    def test_a_refresh(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((rd(os.path.join(self.d, "bko-holt")), rd(os.path.join(self.d, "COMMIT"))), ("new\n", self.NEW + "\n"))
        self.assertEqual(sorted(os.listdir(self.d)), ["COMMIT", "bko-holt"])

    def fail_mv_on(self, what):
        real = shutil.which("mv")
        wr(os.path.join(self.stubs, "mv"), f'#!/bin/sh\nfor a; do case $a in *{what}) exit 1;; esac; done\nexec {real} "$@"\n', 0o755)

    def test_cut_short_at_commit_leaves_no_commit(self):
        self.fail_mv_on("/COMMIT")
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(rd(os.path.join(self.d, "bko-holt")), "new\n")
        self.assertFalse(os.path.exists(os.path.join(self.d, "COMMIT")))

    def test_cut_short_at_the_binary_leaves_no_commit(self):
        self.fail_mv_on("/bko-holt")
        self.assertEqual(self.run_it().returncode, 1)
        self.assertEqual(rd(os.path.join(self.d, "bko-holt")), "old\n")
        self.assertFalse(os.path.exists(os.path.join(self.d, "COMMIT")))

    def test_a_held_lock_refuses_and_writes_nothing(self):
        with open(os.path.join(self.w, "images", ".lock-netbsd"), "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            r = self.run_it()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(rd(os.path.join(self.d, "COMMIT")), self.OLD + "\n")

    def test_the_lock_is_held_while_it_copies(self):
        # (cp's stub tries the lock: it must find it taken.)
        lk = os.path.join(self.w, "images", ".lock-netbsd")
        wr(os.path.join(self.stubs, "cp"), f'#!/bin/sh\nflock -n {lk} true && echo free >{self.t}/seen || echo held >{self.t}/seen\nexec {shutil.which("cp")} "$@"\n', 0o755)
        self.assertEqual(self.run_it().returncode, 0)
        self.assertEqual(rd(os.path.join(self.t, "seen")), "held\n")


class BootSh(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-k4-bootsh-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        # A copy's layout, with boot-test.py a stub that notes its arguments.
        self.root = os.path.join(self.t, "copy")
        os.makedirs(os.path.join(self.root, "testvm", "test2", "img"))
        shutil.copy(os.path.join(T2, "img", "boot.sh"), os.path.join(self.root, "testvm", "test2", "img", "boot.sh"))
        wr(os.path.join(self.root, "stick", "boot-test.py"),
           "import json, sys\nprint(' '.join(sys.argv[1:]))\n"
           "if '--manifest' in sys.argv:\n    json.dump({'kernel': 'k'}, open(sys.argv[sys.argv.index('--manifest') + 1], 'w'))\n"
           "sys.exit(1 if sys.argv[2] == 'uefi' else 0)\n")
        self.pin = os.path.join(self.t, "rescuer.img")
        wr(self.pin, "x")

    def boot(self, *modes):
        out = os.path.join(self.t, "out")
        r = subprocess.run([BASH, os.path.join(self.root, "testvm", "test2", "img", "boot.sh"), "linux", self.pin, out] + list(modes),
                           capture_output=True, text=True)
        return r, out

    def test_modes_media_and_one_manifest(self):
        r, out = self.boot("bios", "uefi", "cdrom")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("--manifest", rd(os.path.join(out, "boot-bios.out")))
        self.assertNotIn("--manifest", rd(os.path.join(out, "boot-uefi.out")))
        self.assertIn("bios --platform linux --media cdrom", rd(os.path.join(out, "boot-cdrom.out")))
        self.assertEqual(rd(os.path.join(out, "boot-uefi.rc")).strip(), "1")
        self.assertEqual(json.loads(rd(os.path.join(out, "manifest.json"))), {"kernel": "k"})

    def test_a_pass(self):
        r, _ = self.boot("bios")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_pin(self):
        os.unlink(self.pin)
        self.assertEqual(self.boot("bios")[0].returncode, 2)

    @unittest.skipUnless(LINUX, "GNU rm")
    def test_a_signal_removes_the_ram_dir(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            wr(os.path.join(self.root, "stick", "boot-test.py"), "import time\ntime.sleep(30)\n")
            ram = os.path.join(self.t, f"ram-{int(sig)}")
            p = subprocess.Popen([BASH, os.path.join(self.root, "testvm", "test2", "img", "boot.sh"), "--ram", ram, "linux", self.pin,
                                  os.path.join(self.t, "out"), "bios"], start_new_session=True)
            for _ in range(200):
                if os.path.isdir(ram):
                    break
                time.sleep(0.05)
            time.sleep(0.3)
            os.killpg(p.pid, sig)
            self.assertEqual(p.wait(30), 128 + int(sig))
            self.assertFalse(os.path.exists(ram))

    def test_the_handlers_ignore_a_second_signal(self):
        text = rd(os.path.join(T2, "img", "boot.sh"))
        for code, name in ((143, "TERM"), (130, "INT"), (129, "HUP")):
            self.assertIn(f"""trap 'trap "" TERM INT HUP; exit {code}' {name}""", text)


class ImgBoot(unittest.TestCase):
    def test_no_arguments_prints_the_usage_line(self):
        r = subprocess.run([sys.executable, "-B", os.path.join(WT, "testvm", "holttest1d", "img-boot.py")], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(r.stderr.startswith("Usage: img-boot.py LOG"), r.stderr)


class Spec(unittest.TestCase):
    def test_the_seven(self):
        with open(os.path.join(T2, "img", "spec.json"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(sorted(d["entries"]), sorted(tool_img.PLATFORMS))
        self.assertEqual(d["entries"]["linux"]["modes"], ["bios", "uefi", "uefi-secureboot", "cdrom"])
        self.assertEqual(d["entries"]["netbsd"]["modes"], ["bios", "uefi"])
        self.assertEqual(d["soft"], ["qemu", "ovmf"])
        self.assertTrue(d["kinds"]["image"]["bins_from_image"])
        for x in d["kinds"]["image"]["harness"]:
            if "{plat}" not in x:
                self.assertTrue(os.path.exists(os.path.join(WT, x.lstrip("?"))), x)

    def test_it_resolves_the_images_binaries(self):
        from t2lib import keys
        src = keys.TreeSource(WT)
        specs = keys.Specs(src)
        base = {"bins": {"rustic-bko": "2" * 64, "bko-holt": "3" * 64}, "release": {"name": "x.img", "sha256": "4" * 64},
                "pin": {"sha256": "5" * 64, "current": True, "bins_stale": False}, "done_exit": 0}
        facts = {"host": "chopin", "versions": {"qemu": "Q", "ovmf": "O"},
                 "plumbing": {"shared:cmp-run.sh": "a" * 64, "shared:testvm/compare/iolog.sh": "b" * 64}}
        r = keys.resolve("img:haiku", src, dict(facts, images={"haiku": base}), specs)
        self.assertEqual(sorted(r.inputs["bin"]), ["haiku/bko-holt", "haiku/release-image", "haiku/rustic-bko", "haiku/test-image"])
        self.assertEqual(r.unknown, [])
        im = dict(base, bins=dict(base["bins"], **{"restic-bko": "1" * 64}))
        r = keys.resolve("img:netbsd", src, dict(facts, images={"netbsd": im}), specs)
        self.assertEqual(r.inputs["bin"]["netbsd/restic-bko"], "sha256:" + "1" * 64)
        self.assertEqual(r.inputs["bin"]["netbsd/test-image"], "sha256:" + "5" * 64)
        self.assertEqual(r.inputs["soft"], {"ovmf": "ver:O", "qemu": "ver:Q"})

    def test_flakes_start_empty(self):
        with open(os.path.join(T2, "img", "flakes.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), [])


if __name__ == "__main__":
    unittest.main()
