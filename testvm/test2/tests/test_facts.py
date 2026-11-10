"""P1's facts and wear tests: U1.9 facts_host.py against a fake root (T2_FACTS_ROOT),
read only, from stdin, with no serial in its output; t2-host imgsum and its cache;
U1.10 the wear join on a synthetic jobs.log; diskstats windows and SMART day deltas.
No host needed."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(HERE)
sys.path.insert(0, TEST2)

from t2lib import wear  # noqa: E402

FX = os.path.join(HERE, "fixtures", "p1")
FACTS = os.path.join(TEST2, "t2lib", "facts_host.py")
T2HOST = os.path.join(TEST2, "t2-host")
SERIALS = ("FAKESER-NOT-REAL-01", "FAKESER-NOT-REAL-02", "FAKESER-NOT-REAL-03")
_tmp = None


def setUpModule():
    global _tmp
    _tmp = tempfile.mkdtemp(prefix="t2-test-facts-")


def tearDownModule():
    shutil.rmtree(_tmp, ignore_errors=True)


def sha(b):
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def tree_state(root):
    """(relpath, size, mtime_ns) of every entry: to show a run wrote nothing."""
    out = []
    for dp, dns, fns in os.walk(root):
        for n in sorted(dns + fns):
            p = os.path.join(dp, n)
            st = os.lstat(p)
            out.append((os.path.relpath(p, root), st.st_size, st.st_mtime_ns))
    return sorted(out)


class FactsHost(unittest.TestCase):
    """U1.9."""

    @classmethod
    def setUpClass(cls):
        cls.root = os.path.join(_tmp, "factsroot")
        shutil.copytree(os.path.join(FX, "factsroot"), cls.root, symlinks=True)
        cls.work = os.path.join(cls.root, "home", "m", "backotter-work")

    def probe(self, want=None, extra=()):
        args = [sys.executable, "-I", "-", "--work", self.work, "--user", "m", "--home", os.path.dirname(self.work)] + list(extra)
        if want is not None:
            args += ["--want", json.dumps(want)]
        env = dict(os.environ, T2_FACTS_ROOT=self.root)
        env.pop("T2_HOST_ROOT", None)
        env.pop("T2_COPY_ROOT", None)
        with open(FACTS, "rb") as f:
            p = subprocess.run(args, stdin=f, capture_output=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.decode(), json.loads(p.stdout)

    def test_home_and_user_never_come_from_work(self):
        """A test world two levels below home: rustc's version is still read from home's rustup."""
        deep = os.path.join(self.root, "home", "m", "backotter-work", "t2rest", "world")
        shutil.rmtree(deep, ignore_errors=True)
        os.makedirs(os.path.join(deep, "vms"))
        try:
            args = [sys.executable, "-I", "-", "--work", deep, "--user", "m", "--home", os.path.join(self.root, "home", "m")]
            env = dict(os.environ, T2_FACTS_ROOT=self.root)
            with open(FACTS, "rb") as f:
                p = subprocess.run(args, stdin=f, capture_output=True, env=env, timeout=60)
            j = json.loads(p.stdout)
            self.assertEqual(j["versions"]["rustc"], "rustc 1.94.1 (e408947bf 2026-03-25)")
            self.assertTrue(j["preflight"]["kvm"])
            self.assertEqual(j["work"], deep)
        finally:
            shutil.rmtree(deep, ignore_errors=True)

    def test_the_process_user_when_no_user_is_given(self):
        sys.path.insert(0, TEST2)
        from t2lib import facts_host
        import getpass
        self.assertEqual(facts_host.default_user(), getpass.getuser())
        self.assertEqual(facts_host.default_home(), os.path.expanduser("~"))

    def test_fw(self):
        sys.path.insert(0, TEST2)
        from t2lib import facts_host as fh
        self.assertEqual(fh.fw_of([]), "bios")
        self.assertEqual(fh.fw_of(["--uefi"]), "uefi-sb")
        self.assertEqual(fh.fw_of(["--uefi", "--no-secure-boot"]), "uefi")
        self.assertEqual(fh.fw_of(["--q35"]), "q35")

    def _guest_dir(self, name="fpguest", gen="a" * 64):
        g = os.path.join(self.work, "vms", name)
        os.makedirs(g, exist_ok=True)
        with open(os.path.join(g, name + ".qcow2"), "w") as f:
            f.write("disk")
        with open(os.path.join(g, "image-state"), "w") as f:
            json.dump({"generation": gen, "updates": 1}, f)
        return g

    def test_sidecars(self):
        g = self._guest_dir()
        try:
            doc = {"v": 1, "generation": "a" * 64, "rules": "r" * 64,
                   "holt": {"digest": "1" * 64, "was": ["2" * 64]},
                   "stic": {"digest": "3" * 64, "fine": "4" * 64, "was": ["5" * 64]}}
            with open(os.path.join(g, "image-fingerprint.json"), "w") as f:
                json.dump(doc, f)
            with open(os.path.join(g, "image-stat.json"), "w") as f:
                json.dump({"generation": "a" * 64, "disks": {"fpguest.qcow2": {"size": 4, "mtime_ns": 1, "inode": 2}}}, f)
            with open(os.path.join(g, "vm-run-options"), "w") as f:
                f.write("--uefi --no-secure-boot\n")
            _, j = self.probe({"guests": ["fpguest"]})
            e = j["guests"]["fpguest"]
            self.assertEqual(e["fingerprint"], {"generation": "a" * 64, "holt": "1" * 64, "stic": "3" * 64, "fine": "4" * 64,
                                                "holt_was": ["2" * 64], "stic_was": ["5" * 64], "rules": "r" * 64})
            self.assertEqual(e["stat"]["disks"]["fpguest.qcow2"]["size"], 4)
            self.assertEqual(e["fw"], "uefi")
            d = e["disks"][0]
            self.assertTrue(isinstance(d["inode"], int) and isinstance(d["mtime_ns"], int))
            # a sidecar of another generation is no fingerprint
            doc["generation"] = "b" * 64
            with open(os.path.join(g, "image-fingerprint.json"), "w") as f:
                json.dump(doc, f)
            _, j = self.probe({"guests": ["fpguest"]})
            self.assertIsNone(j["guests"]["fpguest"]["fingerprint"])
        finally:
            shutil.rmtree(g, ignore_errors=True)

    def test_rows_holt_agrees_with_the_probe(self):
        g = self._guest_dir("fpguest2")
        sys.path.insert(0, TEST2)
        from t2lib import rows_holt
        try:
            doc = {"generation": "a" * 64, "holt": {"digest": "1" * 64}, "stic": {"digest": "3" * 64, "fine": "4" * 64}}
            with open(os.path.join(g, "image-fingerprint.json"), "w") as f:
                json.dump(doc, f)
            _, j = self.probe({"guests": ["fpguest2"]})
            mine = rows_holt.guest_facts(self.work, "fpguest2")
            for k in ("fingerprint", "stat", "fw", "generation", "firmware"):
                self.assertEqual(mine[k], j["guests"]["fpguest2"][k], k)
        finally:
            shutil.rmtree(g, ignore_errors=True)

    def _image(self, images, plat, hostroot):
        d = os.path.join(images, plat)
        os.makedirs(os.path.join(d, "test"))
        tp = os.path.join(d, "test", "bko.img")
        with open(tp, "w") as f:
            f.write("image")
        with open(os.path.join(d, "bko-bin.sha256"), "w") as f:
            f.write("%s  ./restic-bko\n%s  ./bko-holt\n" % ("1" * 64, "2" * 64))
        with open(os.path.join(d, "SHA256SUMS"), "w") as f:
            f.write("%s  bko.img\n%s  bko.img.xz\n" % ("3" * 64, "4" * 64))
        with open(os.path.join(d, "done.exit"), "w") as f:
            f.write("0\n")
        with open(os.path.join(d, "bko-holt.commit"), "w") as f:
            f.write("a" * 40 + "\n")
        with open(os.path.join(d, "result.txt"), "w") as f:
            f.write("bko-bin set from round rel4\nmore\n")
        return tp

    def test_images_part(self):
        images = os.path.join(_tmp, "imgs")
        hr = os.path.join(_tmp, "hr-img")
        tp = self._image(images, "netbsd", hr)
        os.makedirs(os.path.join(images, "linux", "prev-1"))        # no current image: not listed
        s = os.stat(tp)
        pd = os.path.join(hr, "img-pin", "netbsd")
        os.makedirs(pd)
        with open(os.path.join(pd, "rescuer.img.sha256"), "w") as f:
            f.write("5" * 64 + "  rescuer.img\n")
        with open(os.path.join(pd, "source.json"), "w") as f:
            json.dump({"path": tp, "size": s.st_size, "mtime_ns": s.st_mtime_ns, "inode": s.st_ino, "sha256": "5" * 64}, f)
        _, j = self.probe({"images": True, "guests": []}, extra=["--images", images, "--host-root", hr])
        self.assertEqual(sorted(j["images"]), ["netbsd"])
        im = j["images"]["netbsd"]
        self.assertEqual(im["bins"], {"restic-bko": "1" * 64, "bko-holt": "2" * 64})
        self.assertEqual(im["release"], {"name": "bko.img", "sha256": "3" * 64})
        self.assertEqual((im["test"]["name"], im["test"]["size"]), ("bko.img", 5))
        self.assertEqual(im["pin"], {"sha256": "5" * 64, "current": True, "bins_stale": False})
        self.assertEqual((im["done_exit"], im["holt_commit"]), (0, "a" * 40))
        self.assertEqual(im["bko_bin_info"], "bko-bin set from round rel4")
        # the image moved: the pin isn't current
        os.utime(tp, ns=(1, 1))
        _, j = self.probe({"images": True, "guests": []}, extra=["--images", images, "--host-root", hr])
        self.assertFalse(j["images"]["netbsd"]["pin"]["current"])
        # no commit file, no pin, no done.exit
        os.remove(os.path.join(images, "netbsd", "bko-holt.commit"))
        os.remove(os.path.join(images, "netbsd", "done.exit"))
        shutil.rmtree(os.path.join(hr, "img-pin"))
        _, j = self.probe({"images": True, "guests": []}, extra=["--images", images, "--host-root", hr])
        im = j["images"]["netbsd"]
        self.assertEqual((im["holt_commit"], im["done_exit"]), (None, None))
        self.assertEqual(im["pin"], {"sha256": None, "current": False, "bins_stale": False})
        # not asked for
        _, j = self.probe({"guests": []}, extra=["--images", images, "--host-root", hr])
        self.assertNotIn("images", j)

    def test_an_image_that_cannot_be_read_costs_only_its_platform(self):
        images = os.path.join(_tmp, "imgs-bad")
        hr = os.path.join(_tmp, "hr-img-bad")
        self._image(images, "linux", hr)
        tp = self._image(images, "netbsd", hr)
        os.remove(tp)
        os.symlink("/nonexistent", tp)
        _, j = self.probe({"images": True, "guests": []}, extra=["--images", images, "--host-root", hr])
        self.assertEqual(sorted(j["images"]), ["linux"])
        self.assertEqual(len([e for e in j["errors"] if e.startswith("images/netbsd: ")]), 1)

    def test_facts(self):
        before = tree_state(self.root)
        t0 = time.monotonic()
        text, f = self.probe({"sets": ["bko-bin", "roundrel4", "roundnone"],
                              "plumbing": ["shared:cmp-run.sh", "shared:testvm/compare/iolog.sh", "shared:nothere"],
                              "host_inputs": ["~/backotter-work/netbsd-tools", "~/backotter-work/nothere"]})
        took = time.monotonic() - t0
        self.assertEqual(tree_state(self.root), before)     # read only
        # The 3 s budget is a host's (0.13 s on chopin, 2026-11-08): here only a gross
        # slowdown fails, not a Mac busy with other test runs.
        self.assertLess(took, 20.0)
        self.assertLess(f["took_s"], 15.0)
        for s in SERIALS + ("not-a-real-passphrase",):
            self.assertNotIn(s, text)
        self.assertEqual(f["errors"], [])
        v = f["versions"]
        self.assertEqual(v["kernel"], "6.12.48+deb13-amd64")
        self.assertEqual(v["zfs"], "2.3.2-1")
        self.assertEqual(v["qemu"], "QEMU emulator version 10.0.2 (Debian 1:10.0.2+ds-1)")
        self.assertEqual(v["ovmf"], "2025.02-8")
        self.assertEqual(v["rustc"], "rustc 1.94.1 (e408947bf 2026-03-25)")
        self.assertEqual(v["pkgs"]["e2fsprogs"], "1.47.2-3+b3")
        self.assertEqual(v["pkgs"]["fdisk"], "2.41-5")
        self.assertIsNone(v["pkgs"]["gdisk"])                 # deinstalled: config files only
        self.assertIsNone(v["pkgs"]["mdadm"])
        p = f["preflight"]
        self.assertEqual((p["tctl_c"], p["nvme_c"]), (47.75, {"nvme0": 32.85}))
        self.assertEqual((p["ram"]["mounted"], p["ram"]["reserve_rows"], p["ram"]["wait_rows"]), (True, 2, 0))
        self.assertEqual((p["mem_available_kib"], p["swap_used_kib"], p["pswpout"]), (57495328, 2244, 18062))
        self.assertTrue(p["kvm"])
        self.assertEqual(p["diskstats"], {"nvme0n1": {"model": "FAKE_NVME_2TB", "sectors_written": 568163601},
                                          "sda": {"model": "FAKE_SATA_1TB", "sectors_written": 4000}})
        bb = f["sets"]["bko-bin"]
        self.assertEqual(bb["sums"]["linux/restic-bko"], sha("fake restic linux\n"))
        self.assertEqual(bb["commits"], {"linux/restic-bko": "5" * 40,
                                         "linux/rustic-bko": {"commit": "b" * 40, "rustic_core": "4" * 40}})
        self.assertEqual(bb["digests"], {})
        self.assertEqual(bb["build_info"], "bko-bin set from round rel4 (synthetic)")
        rr = f["sets"]["roundrel4"]
        with open(os.path.join(self.work, "roundrel4/bin/linux/rustic-bko.digest"), "rb") as fh:
            self.assertEqual(rr["sums"]["linux/rustic-bko.digest"], sha(fh.read()))
        self.assertEqual(rr["digests"], {"linux/restic-bko": "d" * 64, "linux/rustic-bko": "e" * 64})
        self.assertNotIn("built on", rr["digest_texts"]["linux/restic-bko"])
        self.assertNotIn("reused from", rr["digest_texts"]["linux/rustic-bko"])
        self.assertIn("sources " + "8" * 64, rr["digest_texts"]["linux/rustic-bko"])
        self.assertIsNone(f["sets"]["roundnone"])
        self.assertEqual(f["rounds"]["rel4"]["restic"], "5" * 40)
        self.assertEqual(f["rounds"]["rel4"]["rustic_core"], "4" * 40)
        hb = f["holt_bins"]
        self.assertEqual(hb["holttest1d"]["sums"], {"linux": sha("fake bko-holt linux 0.2.4\n"),
                                                    "freebsd": sha("fake bko-holt freebsd 0.2.4\n")})
        code = sha("synthetic holt code")
        self.assertEqual(hb[code]["sums"], {"linux": sha("fake built bko-holt linux\n"),
                                            "freebsd": sha("fake built bko-holt freebsd\n")})
        g = f["guests"]
        self.assertEqual(sorted(g), ["debian13", "fedora44-raid"])          # notaguest has no qcow2
        self.assertEqual(g["debian13"]["generation"], sha("gen debian13 u3"))
        self.assertEqual(g["debian13"]["updates"], 3)
        self.assertEqual(g["debian13"]["files"]["luks-passphrase"], sha("not-a-real-passphrase\n"))
        self.assertEqual(g["debian13"]["files"]["vm-run-options"], "absent")
        self.assertEqual(g["debian13"]["firmware"], "bios")
        fr = g["fedora44-raid"]
        self.assertEqual((fr["generation"], fr["imgsum"], fr["firmware"]), (None, None, "uefi"))
        self.assertEqual([os.path.basename(d["path"]) for d in fr["disks"]], ["fedora44-raid-b.qcow2", "fedora44-raid.qcow2"])
        rs = f["rescuers"]
        self.assertEqual(rs["images"]["rescuer.qcow2"]["sha256"], sha("fake rescuer image\n"))
        self.assertIsNone(rs["images"]["dragonfly6-newfsid.qcow2"]["sha256"])
        self.assertEqual(rs["bin"], {"bcachefs": sha("fake bcachefs\n"), "f2fs-tools_1.16.0-1.1+b1_amd64.deb": sha("fake deb\n")})
        self.assertEqual(f["plumbing"], {"shared:cmp-run.sh": sha("#!/bin/sh\n# fake queue\n"),
                                         "shared:testvm/compare/iolog.sh": sha("#!/bin/sh\n# fake iolog\n"),
                                         "shared:nothere": None})
        hi = f["host_inputs"]
        self.assertEqual(hi["~/backotter-work/nothere"], "absent")
        nb = os.path.join(self.work, "netbsd-tools")
        lines = sorted([f"bin/nbmake\t{sha('fake tool' + chr(10))}", f"share/readme\t{sha('fake' + chr(10))}",
                        "current\t->bin"])
        self.assertEqual(hi["~/backotter-work/netbsd-tools"], "dir:" + sha("".join(l + "\n" for l in lines)))
        self.assertTrue(os.path.isdir(nb))
        self.assertEqual(f["synced"], {"testvm": "6ad28d7", "stick": "6ad28d7.dirty"})
        self.assertEqual(f["copies"], ["6fff54b"])          # not the .part directory

    def test_go_and_rustc_versions_read_not_run(self):
        # go is never run (it writes telemetry into the caller's home); rustc is the
        # rustup default toolchain's own binary, not the proxy
        from t2lib import facts_host
        home = os.path.join(_tmp, "verhome")
        shutil.rmtree(home, ignore_errors=True)
        gob = os.path.join(home, "sdk", "go1.25.10", "bin")
        os.makedirs(gob)
        with open(os.path.join(home, "sdk", "go1.25.10", "VERSION"), "w") as f:
            f.write("go1.25.10\ntime 2026-05-04T20:36:18Z\n")
        mark = os.path.join(home, "ran")
        for p, body in ((os.path.join(gob, "go"), f"touch {mark}; echo go version wrong"),
                        (os.path.join(home, ".cargo", "bin", "rustc"), f"touch {mark}; echo rustc proxy"),
                        (os.path.join(home, ".rustup", "toolchains", "stable-x86_64-unknown-linux-gnu", "bin", "rustc"),
                         "echo 'rustc 1.94.1 (e408947bf 2026-03-25)'")):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("#!/bin/sh\n" + body + "\n")
            os.chmod(p, 0o755)
        with open(os.path.join(home, ".rustup", "settings.toml"), "w") as f:
            f.write('version = "12"\ndefault_toolchain = "stable"\nprofile = "default"\n')
        self.assertEqual(facts_host.go_version(os.path.join(gob, "go")), "go1.25.10")
        self.assertEqual(facts_host.rustc_version(home), "rustc 1.94.1 (e408947bf 2026-03-25)")
        self.assertFalse(os.path.exists(mark))
        os.unlink(os.path.join(home, ".rustup", "settings.toml"))
        self.assertEqual(facts_host.rustc_version(home), "rustc proxy")   # no rustup default: the proxy, as a user
        self.assertIsNone(facts_host.go_version(None))

    def test_holt_bin_dirs(self):
        # --holt-bin DIR: the set's sums from DIR/SHA256SUMS, named as asked (~/ or absolute);
        # a directory without SHA256SUMS isn't listed.
        ready = os.path.join(self.root, "home/m/ready")
        os.makedirs(os.path.join(ready, "freebsd"), exist_ok=True)
        with open(os.path.join(ready, "SHA256SUMS"), "w") as f:
            f.write("%s *bko-holt\n%s *freebsd/bko-holt\n" % ("a" * 64, "b" * 64))
        _, f = self.probe({"holt_bin_dirs": ["/home/m/ready", "~/nowhere"]})
        self.assertEqual(f["holt_bins"]["/home/m/ready"]["sums"], {"linux": "a" * 64, "freebsd": "b" * 64})
        self.assertNotIn("~/nowhere", f["holt_bins"])

    def test_guests_filter_and_defaults(self):
        _, f = self.probe({"guests": ["fedora44-raid"]})
        self.assertEqual(sorted(f["guests"]), ["fedora44-raid"])
        self.assertEqual(sorted(f["sets"]), ["bko-bin"])
        self.assertEqual(sorted(f["plumbing"]), ["shared:cmp-run.sh", "shared:testvm/compare/iolog.sh"])

    def test_imgsum_cache(self):
        hroot = os.path.join(_tmp, "imgsum-host")
        g = os.path.join(self.work, "vms", "fedora44-raid")
        disks = [os.path.join(g, "fedora44-raid.qcow2"), os.path.join(g, "fedora44-raid-b.qcow2"),
                 os.path.join(self.root, "bkoscratch/holttest1d/rescuer/dragonfly6-newfsid.qcow2")]
        env = dict(os.environ, T2_HOST_ROOT=hroot)
        p = subprocess.run([sys.executable, "-B", T2HOST, "imgsum"] + disks, capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        got = json.loads(p.stdout)
        self.assertEqual(got[disks[0]], sha("fake raid disk a\n"))
        with open(os.path.join(hroot, "cache", "imgsum.json")) as f:
            cache = json.load(f)
        self.assertEqual(sorted(cache["files"]), sorted(disks))
        _, f = self.probe(extra=["--host-root", hroot])
        want = "img:" + sha("".join(sorted([f"fedora44-raid-b.qcow2\t{sha('fake raid disk b' + chr(10))}\n",
                                            f"fedora44-raid.qcow2\t{sha('fake raid disk a' + chr(10))}\n"])))
        self.assertEqual(f["guests"]["fedora44-raid"]["imgsum"], want)
        self.assertEqual(f["rescuers"]["images"]["dragonfly6-newfsid.qcow2"]["imgsum"],
                         "img:" + sha("fake one-time rescuer\n"))
        # a changed disk: the cached sum no longer counts
        time.sleep(1.1)
        with open(disks[1], "a") as fh:
            fh.write("more\n")
        _, f = self.probe(extra=["--host-root", hroot])
        self.assertIsNone(f["guests"]["fedora44-raid"]["imgsum"])
        p = subprocess.run([sys.executable, "-B", T2HOST, "imgsum", os.path.join(g, "nosuch.qcow2")],
                           capture_output=True, env=env)
        self.assertEqual(p.returncode, 1)
        with open(disks[1], "w") as fh:
            fh.write("fake raid disk b\n")

    def test_t2host_facts(self):
        env = dict(os.environ, T2_FACTS_ROOT=self.root)
        p = subprocess.run([sys.executable, "-B", T2HOST, "--work", self.work, "facts", "--want",
                            json.dumps({"sets": ["roundrel4"]})], capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        f = json.loads(p.stdout)
        self.assertEqual(sorted(f["sets"]), ["roundrel4"])
        self.assertEqual(f["work"], self.work)


class Wear(unittest.TestCase):
    """U1.10, and the diskstats and SMART parts of the wear line."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FX, "jobs.log")) as f:
            cls.jobs = wear.parse_jobs(f.read())
        with open(os.path.join(FX, "smart.log")) as f:
            cls.smart = f.read()

    def test_parse(self):
        self.assertEqual(len(self.jobs), 7)   # the two broken lines skipped
        self.assertFalse(any("serial" in j.x for j in self.jobs))
        self.assertEqual(self.jobs[1].physical_bytes(), 1862656)              # zram0 and loop1 aren't disks
        self.assertEqual(self.jobs[2].physical_bytes(), 24576 + 3066368)      # dm-0 and md127 neither
        self.assertEqual(self.jobs[6].physical_bytes(), 8192)                 # one disk named twice: summed

    def test_join(self):
        q = [{"id": "r1", "tag": "ht1:r4all:pop24", "start": "2026-11-08T02:09:50Z", "t": "2026-11-08T02:16:58Z"},
             {"id": "r2", "tag": "ht1:r4all:pop24", "start": "2026-11-08T04:58:00Z", "t": "2026-11-08T04:59:00Z"},
             {"id": "r3", "tag": "ht1:r4all:redox", "start": "2026-11-08T02:52:00Z", "t": "2026-11-08T02:55:00Z"},
             {"id": "r4", "tag": "ht1:r4all:pop24", "start": "2026-11-08T06:00:00Z", "t": "2026-11-08T06:10:00Z"},
             {"id": "r5", "tag": "ht1:r4all:nothing", "start": "2026-11-08T02:00:00Z", "t": "2026-11-08T03:00:00Z"},
             {"id": "r6", "tag": "ht1:r4all:pop24", "start": "2026-11-08T02:17:30Z", "t": "2026-11-08T02:20:00Z"}]
        got = wear.join(self.jobs, q)
        self.assertEqual(got["r1"], {"ht1:r4all:pop24": 22504448})   # T1.4's line
        self.assertEqual(got["r2"], {"ht1:r4all:pop24": 1000})       # the rerun's own line, not the first run's
        self.assertEqual(got["r3"], {"ht1:r4all:redox": 1862656})
        self.assertEqual(got["r4"], {"ht1:r4all:pop24": None})       # nothing in its window
        self.assertEqual(got["r5"], {"ht1:r4all:nothing": None})
        self.assertEqual(got["r6"], {"ht1:r4all:pop24": 22504448})   # ended 19 s before start: inside the 60 s
        self.assertEqual(wear.tag_sums(self.jobs, ["ht1:r4all:pop24"]), {"ht1:r4all:pop24": 22505448})
        self.assertEqual(wear.tag_disks(self.jobs, {"ht1:r4all:debian13-stack-degraded"}),
                         {"ht1:r4all:debian13-stack-degraded": {"sda": 24576, "nvme0n1": 3066368}})

    def test_stdin_script(self):
        data = {"log": os.path.join(FX, "jobs.log"),
                "queries": [{"id": "x", "tag": "ht1:r4all:pop24", "start": "2026-11-08T02:10:00Z",
                             "t": "2026-11-08T02:16:00Z"}]}
        with open(os.path.join(TEST2, "t2lib", "wear.py")) as f:
            src = "_T2_DATA = " + json.dumps(json.dumps(data)) + "\n" + f.read()
        p = subprocess.run([sys.executable, "-I", "-B", "-", "join"], input=src.encode(), capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["results"], {"x": {"ht1:r4all:pop24": 22504448}})
        self.assertNotIn(b"FAKESER", p.stdout)

    def test_diskstats_delta(self):
        before = {"nvme0n1": {"model": "M", "sectors_written": 1000}, "zram0": {"model": "-", "sectors_written": 1},
                  "sda": {"model": "S", "sectors_written": 50}}
        after = {"nvme0n1": {"model": "M", "sectors_written": 3000}, "zram0": {"model": "-", "sectors_written": 99},
                 "sda": {"model": "S", "sectors_written": 10}, "sdb": {"model": "N", "sectors_written": 7}}
        self.assertEqual(wear.diskstats_delta(before, after), {
            "nvme0n1": {"model": "M", "bytes": 2000 * 512}, "sda": {"model": "S", "bytes": None},
            "sdb": {"model": "N", "bytes": None}})

    def test_smart_days(self):
        d = wear.smart_days(self.smart)
        self.assertNotIn("FAKESER", json.dumps(sorted(d.items(), key=str)))
        # the 2 TB disk by its serial, though its name moved from nvme0n1 to nvme1n1 at the reboot
        self.assertEqual(d[("hostA", "nvme1n1", "FAKE_NVME_2TB")],
                         {"2026-11-07": 10000000000, "2026-11-08": 27200000000})
        self.assertEqual(d[("hostA", "nvme0n1", "FAKE_NVME_1TB")], {"2026-11-07": 0, "2026-11-08": 500000000})
        self.assertEqual(d[("hostA", "sda", "FAKE_SATA_1TB")], {"2026-11-08": 0})
        today = wear.smart_today(self.smart, "2026-11-08")
        self.assertIn({"host": "hostA", "disk": "nvme1n1", "model": "FAKE_NVME_2TB", "bytes": 27200000000}, today)


if __name__ == "__main__":
    unittest.main()
