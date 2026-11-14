"""vm-store's unit tests (docs/design/image-store.md section 6.1): temp-dir roots (a
mirror, a master, caches) with local-path sources and a fake rsync on PATH.  No VM,
no ssh, no host.  Where there is qemu-img (the hosts) the guests' disks are real
qcow2 images; elsewhere (the Mac) hand-built headers stand in.

    cd testvm/store && python3 -B -m unittest tests.test_vmstore
"""

import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
STORE_DIR = TESTS.parent
TESTVM = STORE_DIR.parent
sys.path.insert(0, str(STORE_DIR))
import vmstore as vs  # noqa: E402

HAVE_QEMU = shutil.which("qemu-img") is not None
LINUX = sys.platform.startswith("linux")
IS_ROOT = os.geteuid() == 0
MiB = 1 << 20

FAKE_RSYNC = r'''#!/usr/bin/env python3
"""rsync's local-path subset vm-store uses, logging each call (FAKE_RSYNC_LOG), the
bytes it sent per file (FAKE_RSYNC_BYTES), what objects/ held when it ran
(FAKE_RSYNC_SNOOP); it resumes from --partial-dir, can stop partway once
(FAKE_RSYNC_STOP_ONCE: a file holding N bytes; SIGTERM to its own process group),
sleep (FAKE_RSYNC_SLEEP) or fail on a source (FAKE_RSYNC_FAIL)."""
import json, os, signal, sys, time
args, env = sys.argv[1:], os.environ
pos, opts, i = [], {}, 0
while i < len(args):
    a = args[i]
    if a == "-e":
        i += 2
        continue
    if a.startswith("--") and "=" in a:
        k, v = a[2:].split("=", 1)
        opts[k] = v
    elif a.startswith("-"):
        opts[a.lstrip("-")] = True
    else:
        pos.append(a)
    i += 1
src, dst = pos[-2], pos[-1]
entry = {"argv": args, "src": src, "dst": dst}
if env.get("FAKE_RSYNC_SNOOP"):
    entry["snoop"] = sorted(os.listdir(env["FAKE_RSYNC_SNOOP"]))
if env.get("FAKE_RSYNC_LOG"):
    with open(env["FAKE_RSYNC_LOG"], "a") as f:
        f.write(json.dumps(entry) + "\n")
def count(name, n):
    if env.get("FAKE_RSYNC_BYTES"):
        with open(env["FAKE_RSYNC_BYTES"], "a") as f:
            f.write(f"{name} {n}\n")
if env.get("FAKE_RSYNC_FAIL") and env["FAKE_RSYNC_FAIL"] in src:
    sys.exit(23)
if "files-from" in opts:
    names = [l.strip() for l in open(opts["files-from"]) if l.strip()]
    pairs = [(os.path.join(src, n), os.path.join(dst, n)) for n in names]
else:
    pairs = [(src, os.path.join(dst, os.path.basename(src)) if dst.endswith("/") else dst)]
rc = 0
for s, d in pairs:
    if not os.path.isfile(s):
        sys.stderr.write(f"fake rsync: no {s}\n")
        rc = 23
        continue
    os.makedirs(os.path.dirname(d), exist_ok=True)
    pdir = os.path.join(os.path.dirname(d), opts.get("partial-dir", ".partial"))
    ppath = os.path.join(pdir, os.path.basename(d))
    data = open(s, "rb").read()
    have = open(ppath, "rb").read() if os.path.exists(ppath) else b""
    if not data.startswith(have):
        have = b""
    todo = data[len(have):]
    stop = env.get("FAKE_RSYNC_STOP_ONCE")
    if stop and os.path.exists(stop):
        n = int(open(stop).read())
        os.unlink(stop)
        os.makedirs(pdir, exist_ok=True)
        with open(ppath, "wb") as f:
            f.write(have + todo[:n])
        count(os.path.basename(s), n)
        os.killpg(0, signal.SIGTERM)
        time.sleep(5)
        sys.exit(20)
    if env.get("FAKE_RSYNC_SLEEP"):
        time.sleep(float(env["FAKE_RSYNC_SLEEP"]))
    tmp = d + ".fake-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    if "t" in opts:
        st = os.stat(s)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    if opts.get("chmod") == "Fa-w":
        os.chmod(tmp, 0o444)
    os.rename(tmp, d)
    if os.path.exists(ppath):
        os.unlink(ppath)
    count(os.path.basename(s), len(todo))
sys.exit(rc)
'''

# A child holding an OFD read lock on byte START of PATH, as a QEMU program would.
HOLD = textwrap.dedent("""
    import fcntl, os, sys
    sys.path.insert(0, sys.argv[1])
    import vmstore
    fd = os.open(sys.argv[2], os.O_RDONLY)
    fcntl.fcntl(fd, fcntl.F_OFD_SETLK, vmstore._flock(fcntl.F_RDLCK, int(sys.argv[3]), 1))
    print("held", flush=True)
    sys.stdin.read()
""")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def make_qcow2(path, vsize=64 * MiB, backing=None, version=3, snapshots=(), payload=b""):
    """A qcow2 header by hand: enough for vm-store's parser (not for QEMU)."""
    b = backing.encode() if backing else b""
    hdr = struct.pack(">4sIQIIQIIQQIIQ", b"QFI\xfb", version, 1024 if b else 0, len(b), 16, vsize, 0, 0, 0, 0, 0,
                      len(snapshots), 4096 if snapshots else 0)
    if version >= 3:
        hdr += struct.pack(">QQQII", 0, 0, 0, 4, 104)
    out = bytearray(hdr.ljust(1024, b"\0") + b)
    if snapshots:
        out = out.ljust(4096, b"\0")
        for i, name in enumerate(snapshots):
            sid, nm = str(i + 1).encode(), name.encode()
            ent = struct.pack(">QIHHIIQII", 0, 0, len(sid), len(nm), 0, 0, 0, 0, 0) + sid + nm
            out += ent + b"\0" * ((8 - len(ent) % 8) % 8)
    out = bytes(out.ljust(65536, b"\0")) + payload
    Path(path).write_bytes(out)


def make_disk(path, seed, backing=None, snapshot=True):
    """A disk unlike any other seed's: a real qcow2 where there is qemu-img."""
    if HAVE_QEMU:
        cmd = ["qemu-img", "create", "-q", "-f", "qcow2"]
        if backing:
            cmd += ["-b", str(backing), "-F", "qcow2"]
        subprocess.run(cmd + [str(path), str(64 * MiB + seed * 65536)], check=True)
        if snapshot and not backing:
            subprocess.run(["qemu-img", "snapshot", "-c", "fresh-install", str(path)], check=True)
    else:
        make_qcow2(path, 64 * MiB + seed * 65536, backing=str(backing) if backing else None,
                   snapshots=["fresh-install"] if snapshot else (), payload=os.urandom(8192) + bytes([seed % 256]) * 100)


def write_state(gdir, disks, updates=0, gen=None):
    sums = {d: sha(gdir / d) for d in disks}
    gen = gen or hashlib.sha256(json.dumps([sums, updates], sort_keys=True).encode()).hexdigest()
    (gdir / "image-state").write_text(json.dumps({"generation": gen, "updates": updates, "sha256": sums}, indent=1))
    return gen


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="vmstore-")))
        self.addCleanup(self._rmtree)
        self.work = self.tmp / "work"
        self.vms = self.work / "vms"
        self.vms.mkdir(parents=True)
        self.mirror = self.tmp / "mirror"
        self.cache = self.tmp / "cache"
        self.bf_root = self.tmp / "bf" / "root"
        self.bf_stage = self.tmp / "bf" / "stage"
        self.scan = self.tmp / "scan"
        self.scan.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in (("rsync", FAKE_RSYNC), ("zpool", FAKE_ZPOOL), ("sudo", FAKE_SUDO), ("flock", FAKE_FLOCK)):
            (self.bin / name).write_text(text)          # never this host's zpool, kernel log or flock
            (self.bin / name).chmod(0o755)
        self.log = self.tmp / "rsync.log"
        self.bytes_log = self.tmp / "rsync.bytes"
        self.env = {"BKO_WORK": str(self.work), "VM_STORE_ROOT": str(self.mirror),
                    "VM_STORE_SOURCES": f"chopin={self.mirror}", "VM_STORE_BIGFOOT": str(self.bf_root),
                    "VM_STORE_BIGFOOT_STAGE": str(self.bf_stage), "VM_STORE_BIGFOOT_HOST": "",
                    "VM_STORE_SCAN": str(self.scan), "VM_STORE_FLOOR": "0", "VM_STORE_MIN_AGE": "24h",
                    "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_RSYNC_LOG": str(self.log),
                    "FAKE_RSYNC_BYTES": str(self.bytes_log)}
        for k in ("VM_STORE_CAP", "VM_STORE_WINDOW", "VM_STORE_COPY", "VM_STORE_FLOOR_REFLINK",
                  "VM_STORE_TEST_CRASH", "FAKE_RSYNC_SLEEP",
                  "FAKE_RSYNC_STOP_ONCE", "FAKE_RSYNC_FAIL", "FAKE_RSYNC_SNOOP", *FAKE_BF_ENV):
            self.env[k] = ""
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.assertEqual(self.run_vs("init", str(self.mirror), "--role", "mirror")[0], 0)

    def _rmtree(self):
        for dp, dns, _ in os.walk(self.tmp):
            for d in dns:
                with contextlib.suppress(OSError):
                    os.chmod(os.path.join(dp, d), 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers ---

    def run_vs(self, *argv, root=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"VM_STORE_ROOT": str(root)} if root else {}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = vs.main(list(argv))
        self.last_err = err.getvalue()
        return rc, out.getvalue(), err.getvalue()

    def cli(self, *argv, root=None, extra=None, wait=True):
        env = dict(os.environ, **({"VM_STORE_ROOT": str(root)} if root else {}), **(extra or {}))
        cmd = [sys.executable, "-B", str(TESTVM / "vm-store"), *argv]
        if not wait:
            return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return subprocess.run(cmd, env=env, capture_output=True, text=True)

    def guest(self, name, disks=None, state=True, seed=0, updates=0, files=True):
        g = self.vms / name
        g.mkdir(exist_ok=True)
        disks = disks or [f"{name}.qcow2"]
        for i, d in enumerate(disks):
            make_disk(g / d, seed * 10 + i + (hash(name) % 1000) * 100)
        if len(disks) > 1:
            (g / "vm-run-options").write_text("--uefi " + " ".join(f"--disk {d}" for d in disks[1:]) + "\n")
        elif files:
            (g / "vm-run-options").write_text("--uefi\n")
        if files:
            (g / "id_ed25519").write_text(f"KEY {name}\n")
            (g / "id_ed25519").chmod(0o600)
            (g / "vm-run-options").chmod(0o644)
        if state:
            write_state(g, disks, updates)
        return g

    def init_cache(self, root=None, cap="1G", floor="0"):
        root = root or self.cache
        self.assertEqual(self.run_vs("init", str(root), "--role", "cache", "--cap", cap, "--floor", floor)[0], 0)
        return root

    def manifest(self, root, guest, vid=None):
        vid = vid or (root / "guests" / guest / "latest").read_text().split()[0]
        return json.loads((root / "guests" / guest / "versions" / f"{vid}.json").read_text())

    def latest(self, root, guest):
        p = root / "guests" / guest / "latest"
        return p.read_text().split()[0] if p.exists() else None

    def rsync_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def hold(self, path, start):
        p = subprocess.Popen([sys.executable, "-c", HOLD, str(STORE_DIR), str(path), str(start)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "held")

        def release():
            p.stdin.close()
            p.wait()
            p.stdout.close()
        self.addCleanup(release)
        return release


# --- 1: hashing, naming, dedupe, modes, VIDs ---------------------------------------

class TestPublish(StoreTest):
    def test_objects_modes_manifest_latest(self):
        g = self.guest("debian13", disks=["debian13.qcow2", "zroot-b.qcow2"])
        rc, out, err = self.run_vs("publish", "debian13")
        self.assertEqual(rc, 0, err)
        st = json.loads((g / "image-state").read_text())
        vid = "g" + st["generation"][:16]
        self.assertEqual(out.strip(), f"debian13 {vid}")
        m = self.manifest(self.mirror, "debian13")
        self.assertEqual((m["version"], m["generation"], m["updates"], m["parent"]), (vid, st["generation"], 0, None))
        self.assertEqual([d["name"] for d in m["disks"]], ["debian13.qcow2", "zroot-b.qcow2"])
        self.assertEqual([d["role"] for d in m["disks"]], ["boot", "disk"])
        for d in m["disks"]:
            self.assertEqual(d["chain"], [f"sha256-{st['sha256'][d['name']]}.qcow2"])
            obj = self.mirror / "objects" / d["chain"][0]
            self.assertEqual(sha(obj), st["sha256"][d["name"]])
            self.assertEqual(obj.stat().st_mode & 0o7777, 0o444)
            self.assertEqual(d["file_size"], obj.stat().st_size)
            self.assertEqual(d["snapshots"], ["fresh-install"])
        self.assertEqual(sorted(m["files"]), ["id_ed25519", "vm-run-options"])
        self.assertEqual(m["files"]["id_ed25519"]["mode"], "0600")
        self.assertEqual(m["files"]["vm-run-options"]["mode"], "0644")
        for f in m["files"].values():
            obj = self.mirror / "objects" / f["object"]
            self.assertEqual(obj.stat().st_mode & 0o7777, 0o400)
            self.assertEqual("sha256-" + sha(obj) + ".file", f["object"])
        lat = (self.mirror / "guests" / "debian13" / "latest").read_text()
        msha = sha(self.mirror / "guests" / "debian13" / "versions" / f"{vid}.json")
        self.assertEqual(lat, f"{vid} {msha}\n")
        self.assertEqual((self.mirror / "guests/debian13/versions" / f"{vid}.json.sha256").read_text().split()[0], msha)
        self.assertEqual(m["publisher"]["tool"], "vm-store/1")
        self.assertIn(m["publisher"]["copy"], ("reflink", "plain"))
        self.assertEqual(list((self.mirror / "objects" / ".incoming").iterdir()), [])
        # A second publish is a no-op.
        before = (self.mirror / "guests/debian13/versions" / f"{vid}.json").stat().st_mtime_ns
        rc, out, err = self.run_vs("publish", "debian13")
        self.assertEqual(rc, 0, err)
        self.assertIn("already published", err)
        self.assertEqual((self.mirror / "guests/debian13/versions" / f"{vid}.json").stat().st_mtime_ns, before)
        self.assertEqual(self.run_vs("verify")[0], 0, self.last_err)

    def test_dedupe(self):
        a, b = self.guest("a1"), self.guest("b1")
        (b / "vm-run-options").write_text((a / "vm-run-options").read_text())
        self.assertEqual(self.run_vs("publish", "a1", "b1")[0], 0, self.last_err)
        ma, mb = self.manifest(self.mirror, "a1"), self.manifest(self.mirror, "b1")
        self.assertEqual(ma["files"]["vm-run-options"]["object"], mb["files"]["vm-run-options"]["object"])
        files = [p for p in (self.mirror / "objects").iterdir() if p.name.endswith(".file")]
        self.assertEqual(len(files), 3)                    # one vm-run-options, two keys

    def test_i_vid_is_t2s_img_rule(self):
        sys.path.insert(0, str(TESTVM / "test2" / "t2lib"))
        import facts_host
        g = self.guest("chimera", disks=["chimera.qcow2", "b.qcow2"], state=False)
        rc, out, _ = self.run_vs("publish", "chimera")
        self.assertEqual(rc, 0, self.last_err)
        sums = {d: sha(g / d) for d in ("chimera.qcow2", "b.qcow2")}
        want = "i" + facts_host.combine(sums)[len("img:"):][:16]
        self.assertEqual(out.split(), ["chimera", want])
        self.assertEqual(vs.vid_of_sums(sums), want)
        m = self.manifest(self.mirror, "chimera")
        self.assertEqual((m["generation"], m["updates"]), (None, None))
        # Again: the same i VID, a no-op.
        self.assertEqual(self.run_vs("publish", "chimera")[1].split(), ["chimera", want])
        self.assertIn("already published", self.last_err)

    def test_sidecar_denylist(self):
        g = self.guest("side")
        for name in ("update.log", "build.exit", "shot.png", ".vm-update.lock", "x.lock", "image-stat.json",
                     "image-fingerprint.json", "rebuild-boot.sh", "inst.iso", "inst.iso.part", "a.img", "a.img.xz",
                     "disk.raw", "b.tar.gz", "c.tgz", "d.xz", "e.bz2", "f.zst"):
            (g / name).write_text("x")
        (g / ".vm-update-last").mkdir()
        (g / ".vm-update-last" / "result.json").write_text("{}")
        (g / "sub").mkdir()
        (g / "sub" / "notes.txt").write_text("keep me")
        (g / "sub" / "notes.txt").chmod(0o640)
        with open(g / "big.bin", "wb") as f:
            f.truncate(65 * MiB)
        (g / "link").symlink_to("vm-run-options")
        (g / "vm-run.side.abcd.qcow2").write_bytes(b"QFI\xfb")      # a stray overlay, not a disk
        self.assertEqual(self.run_vs("publish", "side")[0], 0, self.last_err)
        m = self.manifest(self.mirror, "side")
        self.assertEqual(sorted(m["files"]), ["id_ed25519", "sub/notes.txt", "vm-run-options"])
        self.assertEqual(m["files"]["sub/notes.txt"]["mode"], "0640")
        for skipped in ("update.log", "big.bin: over 64 MiB", "link: a symlink", ".vm-update-last/",
                        "vm-run.side.abcd.qcow2: not one of the guest's disks"):
            self.assertIn(f"skip {skipped}", self.last_err)
        self.assertEqual([d["name"] for d in m["disks"]], ["side.qcow2"])

    def test_dry_run_changes_nothing(self):
        self.guest("dry")
        rc, _, err = self.run_vs("publish", "dry", "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("would publish", err)
        self.assertEqual(vs.Store(self.mirror, vs.Config()).object_names(), [])

    def test_changed_smallest_first_and_skips_published(self):
        self.guest("big", disks=["big.qcow2", "big2.qcow2", "big3.qcow2"])
        self.guest("small")
        self.guest("nostate", state=False)
        rc, out, err = self.run_vs("publish", "--changed")
        self.assertEqual(rc, 0, err)
        self.assertEqual([l.split()[0] for l in out.splitlines()], ["small", "big"])
        rc, out, _ = self.run_vs("publish", "--changed")
        self.assertEqual((rc, out), (0, ""))

    def test_refuses_on_a_cache(self):
        self.guest("g1")
        self.init_cache()
        rc, _, err = self.run_vs("publish", "g1", root=self.cache)
        self.assertEqual(rc, 1)
        self.assertIn("runs on the mirror", err)


    def test_changed_fails_on_a_guest_it_cant_judge(self):
        self.guest("a1")
        (self.guest("a2") / "image-state").write_text("{broken")
        rc, out, err = self.run_vs("publish", "--changed")
        self.assertEqual(rc, 1, err)                        # a1 published, a2 never: not a success
        self.assertIn("a2:", err)
        self.assertEqual([l.split()[0] for l in out.splitlines()], ["a1"])

    def test_changed_stateless_is_opt_in_and_follows_vms(self):
        self.guest("st")
        g = self.guest("dev", state=False)
        self.guest("other", state=False)
        rc, out, err = self.run_vs("publish", "--changed")
        self.assertEqual((rc, [l.split()[0] for l in out.splitlines()]), (0, ["st"]), err)
        rc, out, err = self.run_vs("publish", "--changed", "--stateless", "dev")
        self.assertEqual(rc, 0, err)
        self.assertEqual([l.split()[0] for l in out.splitlines()], ["dev"])
        v1 = out.split()[1]
        self.assertTrue(v1.startswith("i"))
        self.assertEqual(self.latest(self.mirror, "dev"), v1)
        self.assertIsNone(self.latest(self.mirror, "other"))
        n = len(vs.Store(self.mirror, vs.Config()).object_names())
        rc, out, err = self.run_vs("publish", "--changed", "--stateless", "dev")   # unchanged: hashed, dropped
        self.assertEqual((rc, out.split()), (0, ["dev", v1]), err)
        self.assertIn("already published", err)
        self.assertEqual(len(vs.Store(self.mirror, vs.Config()).object_names()), n)
        # A rebuild: by name latest stays (section 1.6); opted in, latest follows vms/dev.
        (g / "dev.qcow2").unlink()
        make_disk(g / "dev.qcow2", 4242)
        rc, out, err = self.run_vs("publish", "dev")
        v2 = out.split()[1]
        self.assertEqual((rc, self.latest(self.mirror, "dev")), (0, v1), err)
        (g / "dev.qcow2").unlink()
        make_disk(g / "dev.qcow2", 4343)
        rc, out, err = self.run_vs("publish", "--changed", "--stateless", "dev,other")
        self.assertEqual(rc, 0, err)
        v3 = dict(l.split() for l in out.splitlines())["dev"]
        self.assertNotIn(v3, (v1, v2))
        self.assertEqual(self.latest(self.mirror, "dev"), v3)
        self.assertIsNotNone(self.latest(self.mirror, "other"))
        self.assertEqual(self.manifest(self.mirror, "dev", v3)["parent"], v1)

    def test_stateless_refusals(self):
        self.guest("ok")
        self.assertEqual(self.run_vs("publish", "ok", "--stateless", "ok")[0], 2)
        self.assertEqual(self.run_vs("publish", "--changed", "--stateless", "../x")[0], 2)
        rc, out, err = self.run_vs("publish", "--changed", "--stateless", "missing")
        self.assertEqual(rc, 1, err)                        # named but absent: a failure; the rest published
        self.assertIn("no ", err)
        self.assertEqual([l.split()[0] for l in out.splitlines()], ["ok"])

    def test_i_republish_after_a_sidecar_edit_installs_nothing(self):
        g = self.guest("q1", state=False)
        self.assertEqual(self.run_vs("publish", "q1")[0], 0, self.last_err)
        before = sorted(os.listdir(self.mirror / "objects"))
        (g / "vm-run-options").write_text("--uefi --q35\n")  # the i VID names only the disks
        rc, _, err = self.run_vs("publish", "q1")
        self.assertEqual(rc, 0, err)
        self.assertIn("already published", err)
        self.assertEqual(sorted(os.listdir(self.mirror / "objects")), before)
        self.assertEqual(os.listdir(self.mirror / "objects" / ".incoming"), [])

    def test_staged_files_are_fsynced_before_they_are_named(self):
        self.guest("fs")
        synced = []
        real = vs.fsync_file
        with mock.patch.object(vs, "fsync_file", side_effect=lambda p: (synced.append(str(p)), real(p))):
            self.assertEqual(self.run_vs("publish", "fs")[0], 0, self.last_err)
            m = self.manifest(self.mirror, "fs")
            n_objs = len(vs.manifest_objects(m))
            self.assertEqual(len([p for p in synced if "/objects/.incoming/tmp." in p]), n_objs)
            self.init_cache()
            synced.clear()
            self.assertEqual(self.run_vs("ensure", "fs", root=self.cache)[0], 0, self.last_err)
        names = {os.path.basename(p) for p in synced}
        self.assertTrue({"image-state", ".vm-store.json", "vm-run-options", "id_ed25519"} <= names, names)

# --- 2: refusals -----------------------------------------------------------------

class TestRefusals(StoreTest):
    def assert_nothing_stored(self):
        self.assertEqual(vs.Store(self.mirror, vs.Config()).object_names(), [])
        self.assertEqual([p.name for p in (self.mirror / "objects" / ".incoming").iterdir()], [])

    def test_fleet_lock_held(self):
        self.guest("g1")
        with open(self.vms / ".vm-update.fleet.lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            rc, _, err = self.run_vs("publish", "g1")
        self.assertEqual(rc, 3)
        self.assertIn("fleet lock", err)
        self.assert_nothing_stored()

    def test_fleet_lock_shared_by_a_round_also_blocks(self):
        self.guest("g1")
        with open(self.vms / ".vm-update.fleet.lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            self.assertEqual(self.run_vs("publish", "g1")[0], 3)

    def test_unfinished_update(self):
        for name in (".vm-update.pending", ".vm-update.g1.qcow2", "g1.qcow2.merging"):
            g = self.guest("g1")
            (g / name).write_text("x")
            rc, _, err = self.run_vs("publish", "g1")
            self.assertEqual(rc, 1, name)
            self.assertIn("unfinished", err)
            (g / name).unlink()
        self.assert_nothing_stored()

    def test_image_state_mismatch(self):
        g = self.guest("g1")
        st = json.loads((g / "image-state").read_text())
        st["sha256"]["g1.qcow2"] = "0" * 64
        (g / "image-state").write_text(json.dumps(st))
        rc, _, err = self.run_vs("publish", "g1")
        self.assertEqual(rc, 1)
        self.assertIn("changed since vm-update recorded it", err)
        self.assert_nothing_stored()

    def test_expect_generation(self):
        g = self.guest("g1")
        gen = json.loads((g / "image-state").read_text())["generation"]
        wrong = "0" * 16 if not gen.startswith("0" * 16) else "1" * 16
        self.assertEqual(self.run_vs("publish", "g1", "--expect-generation", wrong)[0], 1)
        self.assert_nothing_stored()
        self.assertEqual(self.run_vs("publish", "g1", "--expect-generation", gen[:16])[0], 0, self.last_err)

    def test_backing_file(self):
        g = self.guest("g1", state=False)
        base = self.tmp / "base.qcow2"
        make_disk(base, 99)
        (g / "g1.qcow2").unlink()
        make_disk(g / "g1.qcow2", 1, backing=base)
        rc, _, err = self.run_vs("publish", "g1")
        self.assertEqual(rc, 1)
        self.assertIn("backing file", err)
        self.assert_nothing_stored()

    def test_writer_on_byte_101_is_busy(self):
        g = self.guest("g1", disks=["g1.qcow2", "two.qcow2"])
        self.hold(g / "two.qcow2", 101)
        rc, _, err = self.run_vs("publish", "g1")
        self.assertEqual(rc, 3, err)
        self.assertIn("open for writing", err)
        self.assert_nothing_stored()

    def test_reader_on_byte_201_does_not_block(self):
        g = self.guest("g1")
        self.hold(g / "g1.qcow2", 201)
        self.assertEqual(self.run_vs("publish", "g1")[0], 0, self.last_err)

    def test_fence_blocks_a_new_writer_meanwhile(self):
        g = self.guest("g1")
        seen = []
        real = vs.copy_file

        def copy(src, dst, how):
            fd = os.open(src, os.O_RDONLY)     # what a new QEMU writer checks: byte 201 free?
            try:
                seen.append(vs.ofd_conflict(fd, 201, 1))
            finally:
                os.close(fd)
            return real(src, dst, how)
        with mock.patch.object(vs, "copy_file", copy):
            self.assertEqual(self.run_vs("publish", "g1")[0], 0, self.last_err)
        self.assertEqual(seen, [True])
        fd = os.open(g / "g1.qcow2", os.O_RDONLY)
        self.addCleanup(os.close, fd)
        self.assertFalse(vs.ofd_conflict(fd, 100, 200))   # let go after

    def test_source_changed_during_copy(self):
        g = self.guest("g1", state=False)
        real = vs.copy_file

        def copy(src, dst, how):
            r = real(src, dst, how)
            with open(src, "ab") as f:
                f.write(b"late write")
            return r
        with mock.patch.object(vs, "copy_file", copy):
            rc, _, err = self.run_vs("publish", "g1")
        self.assertEqual(rc, 1)
        self.assertIn("changed during the copy", err)
        self.assert_nothing_stored()

    def test_floor(self):
        self.guest("g1")
        with mock.patch.dict(os.environ, {"VM_STORE_FLOOR": "1000000G"}):
            rc, _, err = self.run_vs("publish", "g1")
        self.assertEqual(rc, 4)
        self.assertIn("floor", err)

    def test_floor_by_default_even_where_reflink_works(self):
        """The default (VM_STORE_FLOOR_REFLINK unset) counts every byte, reflink or not."""
        self.guest("g1")
        calls = []

        def copy(src, dst, how):
            calls.append(how)
            shutil.copyfile(src, dst)
            return "plain"
        with mock.patch.object(vs, "reflink_ok", return_value=True), mock.patch.object(vs, "copy_file", copy), \
                mock.patch.dict(os.environ, {"VM_STORE_FLOOR": "1000000G"}):
            rc, _, err = self.run_vs("publish", "g1")
            self.assertEqual(rc, 4, err)
            self.assertIn("below the floor of", err)
            self.assertNotIn("reflinked", err)
            rc, _, err = self.run_vs("publish", "g1", "--dry-run")
            self.assertEqual(rc, 4, err)
        self.assertEqual(calls, [])
        self.assert_nothing_stored()
        with mock.patch.object(vs, "reflink_ok", return_value=True), mock.patch.object(vs, "copy_file", copy):
            rc, _, err = self.run_vs("publish", "g1")       # floor 0: copied the way it always was
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls, ["auto"])

    def test_floor_reflink_takes_0_or_1(self):
        self.guest("g1")
        with mock.patch.dict(os.environ, {"VM_STORE_FLOOR_REFLINK": "yes"}):
            rc, _, err = self.run_vs("publish", "g1")
        self.assertEqual(rc, 2, err)
        self.assertIn("VM_STORE_FLOOR_REFLINK", err)


class TestReflinkFloor(StoreTest):
    """Section 2.4's floor, opted in with VM_STORE_FLOOR_REFLINK=1: it counts what the
    copies write now, nothing for a reflink."""

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {"VM_STORE_FLOOR_REFLINK": "1"})
        p.start()
        self.addCleanup(p.stop)

    def fake_copy(self, reflink_works=True):
        calls = []

        def copy(src, dst, how):
            calls.append(how)
            if how == "reflink":
                if not reflink_works:
                    raise vs.ReflinkFailed(f"cp --reflink=always {src}: not supported")
                shutil.copyfile(src, dst)
                return "reflink"
            shutil.copyfile(src, dst)
            return "plain"
        return copy, calls

    def test_reflink_needs_no_room_even_below_the_floor(self):
        self.guest("g1")
        copy, calls = self.fake_copy()
        with mock.patch.object(vs, "reflink_ok", return_value=True), mock.patch.object(vs, "copy_file", copy), \
                mock.patch.dict(os.environ, {"VM_STORE_FLOOR": "1000000G"}):
            rc, out, err = self.run_vs("publish", "g1")
        self.assertEqual(rc, 0, err)
        self.assertIn("below the floor", err)
        self.assertEqual(calls, ["reflink"])
        self.assertEqual(self.manifest(self.mirror, "g1")["publisher"]["copy"], "reflink")

    def test_dry_run_reports_no_new_bytes(self):
        self.guest("g1")
        with mock.patch.object(vs, "reflink_ok", return_value=True), \
                mock.patch.dict(os.environ, {"VM_STORE_FLOOR": "1000000G"}):
            rc, _, err = self.run_vs("publish", "g1", "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("0.00 GiB new, reflinked", err)

    def test_failed_reflink_is_checked_again_as_a_copy(self):
        self.guest("g1")
        copy, calls = self.fake_copy(reflink_works=False)
        with mock.patch.object(vs, "reflink_ok", return_value=True), mock.patch.object(vs, "copy_file", copy), \
                mock.patch.dict(os.environ, {"VM_STORE_FLOOR": "1000000G"}):
            rc, _, err = self.run_vs("publish", "g1")
        self.assertEqual(rc, 4, err)
        self.assertEqual(calls, ["reflink"])                  # refused before any plain copy
        self.assertEqual(vs.Store(self.mirror, vs.Config()).object_names(), [])
        self.assertEqual(os.listdir(self.mirror / "objects" / ".incoming"), [])
        with mock.patch.object(vs, "reflink_ok", return_value=True), mock.patch.object(vs, "copy_file", copy):
            rc, _, err = self.run_vs("publish", "g1")       # floor 0: the copy goes ahead
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls[-2:], ["reflink", "plain"])
        self.assertEqual(self.manifest(self.mirror, "g1")["publisher"]["copy"], "plain")

    def test_copy_plain_or_no_reflink_counts_every_byte(self):
        self.guest("g1")
        for env, ok in (({"VM_STORE_COPY": "plain"}, True), ({}, False)):
            with mock.patch.object(vs, "reflink_ok", return_value=ok), \
                    mock.patch.dict(os.environ, dict(env, VM_STORE_FLOOR="1000000G")):
                rc, _, err = self.run_vs("publish", "g1")
            self.assertEqual(rc, 4, (env, err))

    def test_reflink_ok_without_gnu_cp_or_across_file_systems(self):
        self.guest("g1")
        store = vs.Store(self.mirror, vs.Config())
        with mock.patch.object(vs, "gnu", return_value=False):
            self.assertFalse(vs.reflink_ok(self.vms / "g1" / "g1.qcow2", store.incoming))
        src = self.vms / "g1" / "g1.qcow2"
        cp = self.bin / "cp"                                 # a GNU cp whose --reflink=always works, or not
        for works in (True, False):
            cp.write_text("#!/bin/sh\n" + ("exec /bin/cp \"$3\" \"$4\"\n" if works else "exit 1\n"))
            cp.chmod(0o755)
            with mock.patch.object(vs, "gnu", return_value=True), mock.patch.dict(vs._REFLINK, clear=True):
                self.assertEqual(vs.reflink_ok(src, store.incoming), works)
                self.assertFalse(vs.reflink_ok(self.vms / "g1" / "nothing.qcow2", store.incoming))
                real_stat = os.stat
                with mock.patch.object(vs.os, "stat", lambda p, *a, **k: os.stat_result(
                        (0,) * 2 + (12345 if str(p) == str(src) else real_stat(p).st_dev,) + (0,) * 7)):
                    self.assertFalse(vs.reflink_ok(src, store.incoming))       # another file system
            self.assertEqual([n for n in os.listdir(store.incoming) if n.startswith("probe.")], [])

    def test_copy_file_reflink_never_copies(self):
        src = self.tmp / "src"
        src.write_bytes(b"x" * 4096)
        dst = self.tmp / "dst"
        with mock.patch.object(vs, "gnu", return_value=False):
            with self.assertRaises(vs.ReflinkFailed):
                vs.copy_file(src, dst, "reflink")
        self.assertFalse(dst.exists())


# --- 3: the latest rule ----------------------------------------------------------

def man(vid, updates=None, shas=("a",)):
    return {"version": vid, "updates": updates,
            "disks": [{"name": f"d{i}.qcow2", "chain": [f"sha256-{s * 64}.qcow2"]} for i, s in enumerate(shas)]}


class TestLatestRule(StoreTest):
    def test_table(self):
        g0, g1, g2 = man("g" + "0" * 16, 0), man("g" + "1" * 16, 1), man("g" + "2" * 16, 2)
        i1, i2 = man("i" + "1" * 16, shas=("a",)), man("i" + "2" * 16, shas=("b",))
        r = vs.latest_rule
        self.assertTrue(r(None, g0, False)[0])
        self.assertTrue(r(None, i1, False)[0])
        self.assertTrue(r((g1["version"], g1), g2, False)[0])
        self.assertFalse(r((g2["version"], g2), g1, False)[0])
        self.assertFalse(r((g1["version"], g1), dict(g1, version="g" + "9" * 16), False)[0])   # equal updates
        self.assertTrue(r((i1["version"], i1), dict(g0, disks=i1["disks"]), False)[0])        # g over i, same shas
        self.assertFalse(r((i2["version"], i2), dict(g0, disks=i1["disks"]), False)[0])
        self.assertFalse(r((g0["version"], g0), i1, False)[0])                                 # i: a rebuild
        self.assertFalse(r((i1["version"], i1), i2, False)[0])
        self.assertFalse(r((g0["version"], None), g1, False)[0])                               # latest's manifest gone
        self.assertTrue(r((g2["version"], g2), i1, True)[0])                                   # --set-latest
        self.assertTrue(r((g2["version"], g2), g0, True)[0])

    def test_g_over_i_then_rebuild_then_set_latest(self):
        g = self.guest("lx", state=False)
        _, out, _ = self.run_vs("publish", "lx")
        ivid = out.split()[1]
        self.assertEqual(self.latest(self.mirror, "lx"), ivid)
        write_state(g, ["lx.qcow2"], updates=0)           # vm-update's first look at it
        _, out, _ = self.run_vs("publish", "lx")
        gvid = out.split()[1]
        self.assertEqual(self.latest(self.mirror, "lx"), gvid)
        self.assertEqual(self.manifest(self.mirror, "lx", gvid)["parent"], ivid)
        # A rebuild: new disks, no image-state.
        (g / "image-state").unlink()
        (g / "lx.qcow2").unlink()
        make_disk(g / "lx.qcow2", 777)
        _, out, err = self.run_vs("publish", "lx")
        rvid = out.split()[1]
        self.assertIn("rebuild", err)
        self.assertEqual(self.latest(self.mirror, "lx"), gvid)
        self.assertIsNone(self.manifest(self.mirror, "lx", rvid)["parent"])
        # --set-latest on what is published already moves it too.
        rc, _, err = self.run_vs("publish", "lx", "--set-latest")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.latest(self.mirror, "lx"), rvid)

    def test_older_update_is_recorded_not_latest(self):
        g = self.guest("up", updates=3)
        _, out, _ = self.run_vs("publish", "up")
        v3 = out.split()[1]
        write_state(g, ["up.qcow2"], updates=2)
        _, out, err = self.run_vs("publish", "up")
        v2 = out.split()[1]
        self.assertNotEqual(v2, v3)
        self.assertEqual(self.latest(self.mirror, "up"), v3)
        self.assertIn("latest stays", err)
        write_state(g, ["up.qcow2"], updates=4)
        _, out, _ = self.run_vs("publish", "up")
        self.assertEqual(self.latest(self.mirror, "up"), out.split()[1])
        self.assertEqual(self.manifest(self.mirror, "up", out.split()[1])["parent"], v3)


# --- 4: ordering and crashes -----------------------------------------------------

class TestOrdering(StoreTest):
    def test_crash_after_objects_and_after_manifest(self):
        g = self.guest("cr")
        _, out, _ = self.run_vs("publish", "cr")
        v0 = out.split()[1]
        for point, updates in (("after-objects", 1), ("after-manifest", 2)):
            write_state(g, ["cr.qcow2"], updates=updates)
            r = self.cli("publish", "cr", extra={"VM_STORE_TEST_CRASH": point})
            self.assertEqual(r.returncode, 70, r.stderr)
            self.assertEqual(self.latest(self.mirror, "cr"), v0, point)
            r = self.cli("verify")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        vid = "g" + json.loads((g / "image-state").read_text())["generation"][:16]
        self.assertTrue((self.mirror / "guests/cr/versions" / f"{vid}.json.sha256").exists())
        # Publishing again finishes what the crash left: latest moves.
        rc, _, err = self.run_vs("publish", "cr")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.latest(self.mirror, "cr"), vid)

    def test_verify_finds_damage(self):
        self.guest("dm")
        self.run_vs("publish", "dm")
        m = self.manifest(self.mirror, "dm")
        obj = self.mirror / "objects" / m["files"]["vm-run-options"]["object"]
        obj.chmod(0o600)
        obj.write_text("rot")
        obj.chmod(0o400)
        rc, out, _ = self.run_vs("verify")
        self.assertEqual(rc, 1)
        self.assertIn("don't match its name", out)
        self.assertIn("bytes, the manifest says", out)


# --- 5: ensure -------------------------------------------------------------------

def load_compare():
    spec = importlib.util.spec_from_file_location("compare_under_test", TESTVM / "compare" / "compare.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestEnsure(StoreTest):
    def test_leaf_dir(self):
        g = self.guest("debian13", disks=["debian13.qcow2", "zroot-b.qcow2"])
        self.run_vs("publish", "debian13")
        self.init_cache()
        rc, out, err = self.run_vs("ensure", "debian13", root=self.cache)
        self.assertEqual(rc, 0, err)
        leaf = Path(out.strip())
        self.assertEqual(leaf.name, "debian13")
        self.assertEqual(leaf.parent.parent.parent, self.cache / "live")
        cfg = load_compare().GuestConfig(leaf)
        self.assertTrue(cfg.base.exists())
        self.assertEqual(cfg.disks, [leaf / "zroot-b.qcow2"])
        self.assertTrue(cfg.disks[0].exists())
        self.assertEqual(cfg.options, ["--uefi"])
        for d in ("debian13.qcow2", "zroot-b.qcow2"):
            self.assertTrue((leaf / d).is_symlink())
            self.assertFalse(os.path.isabs(os.readlink(leaf / d)))
            self.assertEqual(os.path.dirname(os.path.realpath(leaf / d)), str(self.cache / "objects"))
            self.assertEqual(sha(leaf / d), sha(g / d))
            self.assertTrue(vs.in_store(leaf / d))
        self.assertEqual((leaf / "id_ed25519").stat().st_mode & 0o7777, 0o600)
        self.assertEqual((leaf / "vm-run-options").stat().st_mode & 0o7777, 0o644)
        self.assertFalse((leaf / "id_ed25519").is_symlink())
        st = json.loads((leaf / "image-state").read_text())
        want = json.loads((g / "image-state").read_text())
        self.assertEqual(st, want)
        marker = json.loads((leaf / ".vm-store.json").read_text())
        self.assertEqual(marker["version"], "g" + want["generation"][:16])
        self.assertEqual(sorted(p.name for p in leaf.iterdir() if p.name.endswith(".qcow2")),
                         ["debian13.qcow2", "zroot-b.qcow2"])
        # Again: the fast path, no rsync.
        n = len(self.rsync_calls())
        rc, out2, _ = self.run_vs("ensure", "debian13@latest", "--json", root=self.cache)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out2)["dir"], str(leaf))
        self.assertEqual(len(self.rsync_calls()), n)
        self.assertFalse(vs.in_store(g / "debian13.qcow2"))

    def test_disk_named_otherwise(self):
        self.guest("freebsd15-ufs", disks=["freebsd15.qcow2"])
        self.run_vs("publish", "freebsd15-ufs")
        self.init_cache()
        rc, out, err = self.run_vs("ensure", "freebsd15-ufs", root=self.cache)
        self.assertEqual(rc, 0, err)
        leaf = Path(out.strip())
        self.assertEqual(leaf.name, "freebsd15-ufs")
        self.assertTrue((leaf / "freebsd15.qcow2").exists())

    def test_wrong_marker_fails(self):
        self.guest("g1")
        self.run_vs("publish", "g1")
        self.init_cache()
        leaf = Path(self.run_vs("ensure", "g1", root=self.cache)[1].strip())
        mk = json.loads((leaf / ".vm-store.json").read_text())
        mk["manifest_sha256"] = "0" * 64
        (leaf / ".vm-store.json").write_text(json.dumps(mk))
        rc, _, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 1)
        self.assertIn("store inconsistency", err)

    def test_on_the_mirror_itself(self):
        self.guest("g1")
        self.run_vs("publish", "g1")
        rc, out, err = self.run_vs("ensure", "g1")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.strip().startswith(str(self.mirror / "live" / "g1")))
        self.assertEqual(self.rsync_calls(), [])
        m = self.manifest(self.mirror, "g1")
        o = self.mirror / "objects" / m["disks"][0]["chain"][0]
        o.chmod(0o644)
        o.unlink()
        rc, _, err = self.run_vs("ensure", "g1")
        self.assertEqual(rc, 1)
        self.assertIn("damage", err)

    def test_explicit_vid_and_list(self):
        g = self.guest("lv")
        _, out, _ = self.run_vs("publish", "lv")
        v1 = out.split()[1]
        write_state(g, ["lv.qcow2"], updates=1)
        _, out, _ = self.run_vs("publish", "lv", "--note", "second")
        v2 = out.split()[1]
        self.init_cache()
        rc, out, err = self.run_vs("ensure", f"lv@{v1}", root=self.cache)
        self.assertEqual(rc, 0, err)
        self.assertIn(f"/live/lv/{v1}/lv", out)
        rc, out, _ = self.run_vs("list", "--json")
        rows = json.loads(out)["lv"]
        self.assertEqual([r["version"] for r in rows], [v1, v2])
        self.assertEqual([r["latest"] for r in rows], [False, True])
        rc, out, _ = self.run_vs("list", "lv", "--source", "chopin", "--json", root=self.cache)
        self.assertEqual([r["version"] for r in json.loads(out)["lv"]], [v1, v2])
        rc, out, _ = self.run_vs("status", root=self.cache)
        self.assertEqual(rc, 0)
        self.assertIn("role cache", out)


# --- 6: fetch --------------------------------------------------------------------

class TestFetch(StoreTest):
    def setUp(self):
        super().setUp()
        self.g = self.guest("g1")
        self.run_vs("publish", "g1")
        self.m = self.manifest(self.mirror, "g1")
        self.disk_obj = self.m["disks"][0]["chain"][0]
        self.init_cache()

    def bad_copy(self):
        bad = self.tmp / "bad"
        shutil.copytree(self.mirror, bad, symlinks=True)
        o = bad / "objects" / self.disk_obj
        o.chmod(0o644)
        data = bytearray(o.read_bytes())
        data[-1] ^= 0xFF
        o.write_bytes(bytes(data))
        return bad

    def test_nothing_visible_before_its_sha_passes(self):
        bad = self.bad_copy()
        with mock.patch.dict(os.environ, {"VM_STORE_SOURCES": f"bad={bad}",
                                          "FAKE_RSYNC_SNOOP": str(self.cache / "objects")}):
            rc, _, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 1)
        self.assertIn("no source gave good bytes", err)
        self.assertIn("vm-store verify", err)
        self.assertFalse((self.cache / "objects" / self.disk_obj).exists())
        self.assertFalse((self.cache / "objects" / ".incoming" / self.disk_obj).exists())
        for call in self.rsync_calls():
            self.assertNotIn(os.path.basename(call["src"]), call["snoop"])

    def test_corrupt_source_falls_back(self):
        bad = self.bad_copy()
        with mock.patch.dict(os.environ, {"VM_STORE_SOURCES": f"bad={bad} chopin={self.mirror}"}):
            rc, _, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 0, err)
        self.assertIn("other bytes", err)
        srcs = [c["src"] for c in self.rsync_calls() if c["src"].endswith(self.disk_obj)]
        self.assertEqual(srcs, [f"{bad}/objects/{self.disk_obj}", f"{self.mirror}/objects/{self.disk_obj}"])
        self.assertEqual(sha(self.cache / "objects" / self.disk_obj), self.disk_obj[7:71])
        self.assertEqual((self.cache / "objects" / self.disk_obj).stat().st_mode & 0o7777, 0o444)

    def test_unreachable_first_source(self):
        with mock.patch.dict(os.environ, {"VM_STORE_SOURCES": f"gone={self.tmp}/nowhere chopin={self.mirror}"}):
            rc, _, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 0, err)
        self.assertIn("source gone", err)

    def test_interrupted_fetch_resumes(self):
        stop = self.tmp / "stop"
        stop.write_text("4096")
        with mock.patch.dict(os.environ, {"FAKE_RSYNC_STOP_ONCE": str(stop)}):
            rc, _, err = self.run_vs("ensure", "g1", root=self.cache)
            self.assertEqual(rc, 1, err)
            partial = self.cache / "objects" / ".incoming" / ".partial" / self.disk_obj
            self.assertEqual(partial.stat().st_size, 4096)
            self.assertFalse((self.cache / "objects" / self.disk_obj).exists())
            rc, _, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 0, err)
        sent = sum(int(l.split()[1]) for l in self.bytes_log.read_text().splitlines() if l.split()[0] == self.disk_obj)
        self.assertEqual(sent, (self.mirror / "objects" / self.disk_obj).stat().st_size)
        self.assertFalse(partial.exists())

    def test_offline_uses_cached_latest(self):
        v1 = self.m["version"]
        self.assertEqual(self.run_vs("ensure", "g1", root=self.cache)[0], 0)
        write_state(self.g, ["g1.qcow2"], updates=1)
        _, out, _ = self.run_vs("publish", "g1")
        v2 = out.split()[1]
        rc, out, err = self.run_vs("ensure", "g1", "--offline", root=self.cache)
        self.assertEqual(rc, 0, err)
        self.assertIn(f"/{v1}/", out)
        rc, out, _ = self.run_vs("ensure", "g1", root=self.cache)
        self.assertIn(f"/{v2}/", out)
        # No source answers: the cached latest, with a warning.
        with mock.patch.dict(os.environ, {"VM_STORE_SOURCES": f"gone={self.tmp}/nowhere"}):
            rc, out, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 0, err)
        self.assertIn("using the cached latest", err)
        # --offline never fetches.
        cache2 = self.init_cache(self.tmp / "cache2")
        (cache2 / "guests" / "g1").mkdir(parents=True)
        shutil.copy(self.cache / "guests/g1/latest", cache2 / "guests/g1/latest")
        shutil.copytree(self.cache / "guests/g1/versions", cache2 / "guests/g1/versions")
        rc, _, err = self.run_vs("ensure", "g1", "--offline", root=cache2)
        self.assertEqual(rc, 1)
        self.assertIn("--offline fetches nothing", err)

    def test_failed_fetch_keeps_the_cached_latest(self):
        self.assertEqual(self.run_vs("ensure", "g1", root=self.cache)[0], 0, self.last_err)
        v1 = self.m["version"]
        with open(self.g / "g1.qcow2", "ab") as f:            # v2 has a disk the cache lacks
            f.write(b"more")
        write_state(self.g, ["g1.qcow2"], updates=1)
        _, out, _ = self.run_vs("publish", "g1")
        v2 = out.split()[1]
        self.assertNotEqual(v1, v2)
        with mock.patch.dict(os.environ, {"FAKE_RSYNC_FAIL": "/objects"}):
            rc, _, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 1, err)
        self.assertEqual(self.latest(self.cache, "g1"), v1)     # still the last whole version
        rc, out, err = self.run_vs("ensure", "g1", "--offline", root=self.cache)
        self.assertEqual(rc, 0, err)
        self.assertIn(f"/{v1}/", out)
        rc, out, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 0, err)
        self.assertIn(f"/{v2}/", out)
        self.assertEqual(self.latest(self.cache, "g1"), v2)

    def test_verify_on_a_cache_after_gc(self):
        self.assertEqual(self.run_vs("ensure", "g1", root=self.cache)[0], 0, self.last_err)
        for st in (self.cache / "used").iterdir():
            os.utime(st, (1, 1))
        with mock.patch.dict(os.environ, {"VM_STORE_CAP": "0"}):
            self.assertEqual(self.run_vs("gc", root=self.cache)[0], 0, self.last_err)
        self.assertEqual(vs.Store(self.cache, vs.Config()).object_names(), [])
        rc, out, _ = self.run_vs("verify", root=self.cache)
        self.assertEqual(rc, 0, out)                        # evicted is "not cached", not a problem
        self.assertIn("'absent': 3", out)
        # A present object is still checked.
        self.assertEqual(self.run_vs("ensure", "g1", root=self.cache)[0], 0, self.last_err)
        o = self.cache / "objects" / self.disk_obj
        o.chmod(0o644)
        rc, out, _ = self.run_vs("verify", root=self.cache)
        self.assertEqual(rc, 1)
        self.assertIn("mode 0644", out)
        # The mirror still counts a missing object as damage.
        mo = self.mirror / "objects" / self.disk_obj
        mo.chmod(0o644)
        mo.unlink()
        rc, out, _ = self.run_vs("verify")
        self.assertEqual(rc, 1)
        self.assertIn("is missing", out)

    def test_manifest_sha_mismatch_fails(self):
        mp = self.mirror / "guests/g1/versions" / f"{self.m['version']}.json"
        mp.chmod(0o644)
        mp.write_text(mp.read_text() + " ")
        rc, _, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 1)
        self.assertIn("sha mismatch", err)
        self.assertEqual(self.rsync_calls(), [])

    def test_divergence(self):
        self.assertEqual(self.run_vs("ensure", "g1", root=self.cache)[0], 0)
        lp = self.mirror / "guests/g1/latest"
        lp.write_text(f"{self.m['version']} {'0' * 64}\n")
        rc, _, err = self.run_vs("ensure", "g1", root=self.cache)
        self.assertEqual(rc, 1)
        self.assertIn("store divergence", err)

    def test_fetch_prints_nothing(self):
        rc, out, err = self.run_vs("fetch", "g1", root=self.cache)
        self.assertEqual((rc, out), (0, ""), err)
        self.assertTrue((self.cache / "objects" / self.disk_obj).exists())


# --- 7: gc -----------------------------------------------------------------------

class TestGc(StoreTest):
    def setUp(self):
        super().setUp()
        self.init_cache(cap="1G")
        self.store = vs.Store(self.cache, vs.Config())
        self.n = 0

    def obj(self, age_h=48, size=MiB, kind="qcow2"):
        """An object of SIZE bytes, last ensured AGE_H hours ago."""
        self.n += 1
        data = bytearray(os.urandom(size))
        if kind == "qcow2":
            make_qcow2(self.tmp / "h", payload=b"")
            data[:104] = (self.tmp / "h").read_bytes()[:104]
        data = bytes(data)
        name = f"sha256-{hashlib.sha256(data).hexdigest()}.{kind}"
        p = self.cache / "objects" / name
        p.write_bytes(data)
        p.chmod(0o444)
        st = self.cache / "used" / name
        st.touch()
        t = time.time() - age_h * 3600
        os.utime(st, (t, t))
        return name

    def version(self, guest, vid, disks, files=()):
        m = {"schema": 1, "guest": guest, "version": vid, "generation": None, "updates": None, "parent": None,
             "disks": [{"name": f"d{i}.qcow2", "chain": [d], "file_size": MiB} for i, d in enumerate(disks)],
             "files": {f"f{i}": {"object": f, "mode": "0644", "size": MiB} for i, f in enumerate(files)}}
        vd = self.cache / "guests" / guest / "versions"
        vd.mkdir(parents=True, exist_ok=True)
        data = vs.manifest_bytes(m)
        (vd / f"{vid}.json").write_bytes(data)
        (vd / f"{vid}.json.sha256").write_text(hashlib.sha256(data).hexdigest() + "\n")

    def gc(self, cap, *extra, dry=False):
        with mock.patch.dict(os.environ, {"VM_STORE_CAP": str(cap), "VM_STORE_ROOT": str(self.cache)}):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = vs.main(["gc", "--json", *(["--dry-run"] if dry else []), *extra])
        plan = json.loads(out.getvalue()) if out.getvalue().strip() else {}
        return rc, plan, err.getvalue()

    def left(self):
        return set(self.store.object_names())

    def test_lru_order(self):
        a, b, c = self.obj(72), self.obj(48), self.obj(30)
        use = self.store.use()
        rc, plan, err = self.gc(use - MiB - MiB // 2)        # two of the three must go
        self.assertEqual(rc, 0, err)
        self.assertEqual(plan["evicted"], [a, b])
        self.assertEqual(self.left(), {c})
        self.assertFalse((self.cache / "used" / a).exists())

    def test_dry_run_deletes_nothing(self):
        a, b = self.obj(72), self.obj(48)
        rc, plan, _ = self.gc(0, dry=True)
        self.assertEqual(rc, 0)
        self.assertEqual(plan["evicted"], [a, b])
        self.assertEqual(self.left(), {a, b})

    def test_kept_by_stamp_lock_and_overlays(self):
        fresh, locked, absb, relb, viab, gone = (self.obj(1), self.obj(), self.obj(), self.obj(), self.obj(),
                                                 self.obj())
        self.hold(self.cache / "objects" / locked, 100)
        make_qcow2(self.scan / "abs.qcow2", backing=str(self.cache / "objects" / absb))
        (self.scan / "deep" / "er").mkdir(parents=True)
        make_qcow2(self.scan / "deep" / "er" / "src-0.qcow2",
                   backing=os.path.relpath(self.cache / "objects" / relb, self.scan / "deep" / "er"))
        leaf = self.cache / "live" / "gv" / "i0000000000000000" / "gv"
        leaf.mkdir(parents=True)
        (leaf.parent / "objects").symlink_to("../../../objects")
        (leaf / "gv.qcow2").symlink_to(f"../objects/{viab}")
        make_qcow2(self.scan / "via-live.qcow2", backing=str(leaf / "gv.qcow2"))
        rc, plan, err = self.gc(0)
        self.assertEqual(rc, 4, err)                         # cap 0 can't be met: all the rest are in use
        self.assertEqual(self.left(), {fresh, locked, absb, relb, viab})
        self.assertIn("in use", plan["error"])
        why, _ = vs.in_use(vs.Config(), self.store)
        self.assertTrue(why[fresh].startswith("used "))
        self.assertEqual(why[locked], "qemu lock")
        self.assertEqual(why[absb], f"overlay {self.scan}/abs.qcow2")
        self.assertEqual(why[relb], f"overlay {self.scan}/deep/er/src-0.qcow2")
        self.assertEqual(why[viab], f"overlay {self.scan}/via-live.qcow2")
        self.assertNotIn(gone, self.left())

    @unittest.skipIf(IS_ROOT, "root reads every directory")
    def test_unreadable_scan_dir_raises_the_age(self):
        two_days, four_days, ten_days = self.obj(48), self.obj(96), self.obj(240)
        blind = self.scan / "root-mktemp"
        blind.mkdir()
        blind.chmod(0)
        rc, plan, err = self.gc(0)
        self.assertEqual(rc, 4)
        self.assertIn("can't read", err)
        self.assertEqual(self.left(), {two_days, four_days})
        blind.chmod(0o755)
        rc, plan, err = self.gc(0)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.left(), set())

    @unittest.skipIf(IS_ROOT, "root reads every file")
    def test_unreadable_overlay_raises_the_age(self):
        two_days, ten_days = self.obj(48), self.obj(240)
        ov = self.scan / "vm-run.x.qcow2"                   # a root vm-run's 0600 mktemp overlay, to m
        make_qcow2(ov, backing=str(self.cache / "objects" / two_days))
        ov.chmod(0)
        self.addCleanup(ov.chmod, 0o644)
        rc, plan, err = self.gc(0)
        self.assertEqual(rc, 4, err)
        self.assertIn(str(ov), plan["blind"] if "blind" in plan else err)
        self.assertIn("can't read", err)
        self.assertEqual(self.left(), {two_days})
        ov.chmod(0o644)
        rc, plan, err = self.gc(0)
        self.assertEqual(rc, 4)                             # readable now: kept by rule b
        self.assertEqual(self.left(), {two_days})
        why, blind = vs.in_use(vs.Config(), self.store)
        self.assertEqual((why[two_days], blind), (f"overlay {ov}", []))

    def test_version_kept_whole(self):
        a, b, c = self.obj(1), self.obj(100, kind="file"), self.obj(100)
        other = self.obj(100)
        self.version("v", "g" + "1" * 16, [a], [b])
        self.version("w", "g" + "2" * 16, [c])
        rc, plan, err = self.gc(0)
        self.assertEqual(rc, 4)
        self.assertEqual(self.left(), {a, b})
        why, _ = vs.in_use(vs.Config(), self.store)
        self.assertIn("version v@g1111111111111111", why[b])
        self.assertNotIn(other, self.left())

    def test_floor_counts(self):
        a, b, c = self.obj(72), self.obj(48), self.obj(30)
        use0 = self.store.use()
        base = 10 * MiB

        def free(_path):
            return base + (use0 - self.store.use())       # what eviction gives back
        with mock.patch.object(vs, "free_bytes", free), \
                mock.patch.dict(os.environ, {"VM_STORE_FLOOR": str(base + MiB + MiB // 2)}):
            rc, plan, err = self.gc(10 ** 12)
        self.assertEqual(rc, 0, err)
        self.assertEqual(plan["evicted"], [a, b])
        self.assertEqual(self.left(), {c})

    def test_refuses_on_mirror_and_master(self):
        rc, _, err = self.run_vs("gc")
        self.assertEqual(rc, 1)
        self.assertIn("caches only", err)
        master = self.tmp / "master"
        (master / "objects").mkdir(parents=True)
        (master / "store.json").write_text('{"schema": 1, "role": "master"}')
        rc, _, err = self.run_vs("gc", root=master)
        self.assertEqual(rc, 1)
        self.assertIn("caches only", err)

    def test_everything_in_use_is_no_room(self):
        self.obj(1), self.obj(2)
        rc, _, err = self.gc(MiB)
        self.assertEqual(rc, 4)
        self.assertIn("in use", err)


    def link_version(self, guest, vid, objs, dangling=False):
        """vms/GUEST -> live/GUEST/VID/GUEST, as link (or a hand-made link) leaves it."""
        self.version(guest, vid, objs)
        leaf = self.cache / "live" / guest / vid / guest
        if not dangling:
            leaf.mkdir(parents=True)
            (leaf.parent / "objects").symlink_to("../../../objects")
            for i, o in enumerate(objs):
                (leaf / f"d{i}.qcow2").symlink_to(f"../objects/{o}")
        (self.vms / guest).symlink_to(leaf)
        return leaf

    def test_rule_f_keeps_what_vms_links_to(self):
        a, b, c, d = self.obj(100), self.obj(100), self.obj(100), self.obj(100)
        leaf = self.link_version("lk", "g" + "3" * 16, [a, b])
        (self.vms / "full").mkdir()                          # a real folder with a disk linked by hand
        (self.vms / "full" / "full.qcow2").symlink_to(self.cache / "objects" / c)
        e = self.obj(100)
        sub = self.link_version("sv", "g" + "6" * 16, [e])
        (self.vms / "sv").unlink()
        (self.vms / "full" / "sub").symlink_to(sub)           # a folder linked inside a real one
        rc, plan, err = self.gc(0)
        self.assertEqual(rc, 4, err)
        self.assertEqual(self.left(), {a, b, c, e})
        why, _ = vs.in_use(vs.Config(), self.store)
        self.assertEqual(why[a], f"linked from {self.vms / 'lk'}")
        self.assertEqual(why[c], f"linked from {self.vms / 'full' / 'full.qcow2'}")
        self.assertTrue(leaf.is_dir())
        self.assertNotIn(d, self.left())
        # Linked elsewhere (link re-pointed it): the old version goes like any other.
        (self.vms / "lk").unlink()
        (self.vms / "full" / "full.qcow2").unlink()
        (self.vms / "full" / "sub").unlink()
        rc, plan, err = self.gc(0)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.left(), set())
        self.assertFalse(leaf.parent.exists())

    @unittest.skipIf(IS_ROOT, "root reads every directory")
    def test_unreadable_vms_dir_raises_the_age(self):
        """A link in a vms/G dir gc can't read (a sudo compare.py's 0700 one): rule d."""
        two_days, ten_days = self.obj(48), self.obj(240)
        (self.vms / "full").mkdir()
        for mode in (0, 0o400):                              # unlistable, then listed but not searchable
            hidden = self.vms / "full" / "root-0700"
            hidden.mkdir()
            (hidden / "full.qcow2").symlink_to(self.cache / "objects" / two_days)
            hidden.chmod(mode)
            rc, plan, err = self.gc(0)
            self.assertEqual(rc, 4, (mode, err))
            self.assertIn("can't read", err)
            self.assertIn(f"can't read {hidden}", err)
            self.assertEqual(self.left(), {two_days}, mode)  # kept by the 7-day age
            why, blind = vs.in_use(vs.Config(), self.store)
            self.assertIn("7-day age", why[two_days])
            hidden.chmod(0o755)
            why, blind = vs.in_use(vs.Config(), self.store)  # readable: kept by rule f
            self.assertEqual((why[two_days], blind), (f"linked from {hidden / 'full.qcow2'}", []))
            shutil.rmtree(hidden)
        self.assertNotIn(ten_days, self.left())
        self.vms.chmod(0)                                    # vms/ itself
        _, blind = vs.in_use(vs.Config(), self.store)
        self.assertEqual(blind, [str(self.vms)])
        self.vms.chmod(0o755)

    def test_linked_live_dir_is_never_removed(self):
        a, b = self.obj(100), self.obj(100)
        leaf = self.link_version("lk", "g" + "4" * 16, [a, b])
        (self.cache / "objects" / b).unlink()                 # half evicted before rule f existed
        rc, plan, err = self.gc(10 ** 12)
        self.assertEqual(rc, 0, err)
        self.assertTrue(leaf.is_dir())                       # vms/lk doesn't dangle
        self.assertEqual(self.left(), {a})
        dl = self.obj(100)
        self.link_version("gone", "g" + "5" * 16, [dl], dangling=True)
        rc, plan, err = self.gc(0)
        self.assertEqual(self.left(), {a, dl}, err)
        why, _ = vs.in_use(vs.Config(), self.store)
        self.assertEqual(why[dl], f"linked from {self.vms / 'gone'}")


# --- 7b: link --------------------------------------------------------------------

class TestLink(StoreTest):
    """vm-store link on a cache host: its own BKO_WORK, whose vms/ holds links only."""

    def setUp(self):
        super().setUp()
        self.init_cache()
        self.cwork = self.tmp / "cwork"
        self.cvms = self.cwork / "vms"
        self.cvms.mkdir(parents=True)

    def link(self, *argv, root=None):
        with mock.patch.dict(os.environ, {"BKO_WORK": str(self.cwork)}):
            return self.run_vs("link", *argv, root=root or self.cache)

    def update(self, name, updates):
        g = self.vms / name
        (g / f"{name}.qcow2").unlink()
        make_disk(g / f"{name}.qcow2", 900 + updates)
        write_state(g, [f"{name}.qcow2"], updates=updates)
        rc, out, err = self.run_vs("publish", name)
        self.assertEqual(rc, 0, err)
        return out.split()[1]

    def test_link_and_relink(self):
        self.guest("g1")
        _, out, _ = self.run_vs("publish", "g1")
        v1 = out.split()[1]
        rc, out, err = self.link("g1")
        self.assertEqual((rc, out), (0, f"g1 {v1}\n"), err)
        p = self.cvms / "g1"
        self.assertTrue(p.is_symlink())
        self.assertEqual(os.readlink(p), str(self.cache / "live" / "g1" / v1 / "g1"))
        self.assertEqual(json.loads((p / ".vm-store.json").read_text())["version"], v1)
        self.assertEqual(sha(p / "g1.qcow2"), sha(self.vms / "g1" / "g1.qcow2"))
        # Up to date: nothing changes, not even the link's inode.
        ino = os.lstat(p).st_ino
        self.assertEqual(self.link("g1")[:2], (0, f"g1 {v1}\n"))
        self.assertEqual(os.lstat(p).st_ino, ino)
        # A new version on the mirror: --all re-points every link of ours.
        v2 = self.update("g1", 1)
        rc, out, err = self.link("--all")
        self.assertEqual((rc, out), (0, f"g1 {v2}\n"), err)
        self.assertEqual(os.readlink(p), str(self.cache / "live" / "g1" / v2 / "g1"))
        self.assertIn(f"(was {v1})", err)
        self.assertEqual([n for n in os.listdir(self.cvms) if n.startswith(".")], [])
        # An explicit version (a rollback) too.
        rc, out, err = self.link(f"g1@{v1}")
        self.assertEqual((rc, os.readlink(p)), (0, str(self.cache / "live" / "g1" / v1 / "g1")), err)

    def test_gc_keeps_the_linked_version_only(self):
        self.guest("g1")
        self.run_vs("publish", "g1")
        self.assertEqual(self.link("g1")[0], 0, self.last_err)
        v2 = self.update("g1", 1)
        self.assertEqual(self.link("g1")[0], 0, self.last_err)
        for o in os.listdir(self.cache / "used"):          # all long unused
            t = time.time() - 30 * 86400
            os.utime(self.cache / "used" / o, (t, t))
        with mock.patch.dict(os.environ, {"BKO_WORK": str(self.cwork), "VM_STORE_CAP": "0"}):
            rc, out, err = self.run_vs("gc", root=self.cache)
        self.assertEqual(rc, 4, err)                        # what is linked can't go
        # ...and only that: v1 shares its sidecar objects with v2, yet its disk went (rule f after e).
        m = self.manifest(self.cache, "g1", v2)
        self.assertEqual(set(vs.Store(self.cache, vs.Config()).object_names()), set(vs.manifest_objects(m)))
        self.assertTrue((self.cvms / "g1" / "g1.qcow2").is_file())

    def test_hand_made_and_dangling_links_are_replaced(self):
        self.guest("g1")
        _, out, _ = self.run_vs("publish", "g1")
        v1 = out.split()[1]
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                              text=True).stdout.strip()
        stale = self.cvms / f".g1.vm-store-link.{dead}"     # a killed link's temp link
        stale.symlink_to(self.cache / "live" / "g1" / v1 / "g1")
        (self.cvms / "g1").symlink_to(self.cache / "live" / "g1" / ("g" + "0" * 16) / "g1")   # a VID long gone
        self.assertEqual(self.link("--all"), (0, f"g1 {v1}\n", self.last_err))
        self.assertTrue((self.cvms / "g1" / "g1.qcow2").is_file())
        self.assertFalse(os.path.lexists(stale))

    def test_refusals(self):
        self.guest("g1")
        self.guest("g2")
        self.run_vs("publish", "g1")
        self.run_vs("publish", "g2")
        (self.cvms / "g1").mkdir()                            # a full guest folder
        (self.cvms / "g1" / "g1.qcow2").write_bytes(b"mine")
        (self.cvms / "g2").symlink_to(self.vms / "g2")        # a link of someone else's
        n = len(self.rsync_calls())
        rc, out, err = self.link("g1", "g2")
        self.assertEqual((rc, out), (1, ""), err)
        self.assertIn("not a link", err)
        self.assertIn("remove it by hand", err)
        self.assertEqual((self.cvms / "g1" / "g1.qcow2").read_bytes(), b"mine")
        self.assertEqual(os.readlink(self.cvms / "g2"), str(self.vms / "g2"))
        self.assertEqual(len(self.rsync_calls()), n)          # refused before fetching anything
        self.assertEqual(self.link("--all")[:2], (0, ""))     # neither is one of ours
        self.assertEqual(self.link()[0], 2)
        self.assertEqual(self.link("g1", "--all")[0], 2)
        rc, _, err = self.link("g1", root=self.mirror)
        self.assertEqual(rc, 1)
        self.assertIn("runs on a cache", err)

    def test_refused_while_a_vm_of_it_runs(self):
        self.guest("g1")
        self.run_vs("publish", "g1")
        self.assertEqual(self.link("g1")[0], 0, self.last_err)
        p = self.cvms / "g1"
        old = os.readlink(p)
        v2 = self.update("g1", 1)
        release = self.hold(os.path.realpath(p / "g1.qcow2"), 100)   # vm-run --ram on the old version
        rc, out, err = self.link("g1")
        self.assertEqual((rc, out), (3, ""), err)
        self.assertIn("a QEMU program has g1.qcow2 open", err)
        self.assertEqual(os.readlink(p), old)
        self.assertTrue((self.cache / "live" / "g1" / v2 / "g1").is_dir())   # fetched all the same
        release()
        self.assertEqual(self.link("g1")[:2], (0, f"g1 {v2}\n"), self.last_err)

    def test_refused_while_a_comparison_of_it_runs(self):
        self.guest("g1")
        self.run_vs("publish", "g1")
        self.assertEqual(self.link("g1")[0], 0, self.last_err)
        self.update("g1", 1)
        proc = self.tmp / "proc" / "4242"
        proc.mkdir(parents=True)
        (proc / "cmdline").write_bytes(b"python3\0/x/testvm/compare/compare.py\0--keep\0g1\0")
        with mock.patch.object(vs, "glob_proc", return_value=[str(proc)]):
            rc, _, err = self.link("g1")
        self.assertEqual(rc, 3, err)
        self.assertIn("a comparison of g1 runs (pid 4242)", err)
        (proc / "cmdline").write_bytes(b"python3\0/x/testvm/compare/compare.py\0g2\0")
        with mock.patch.object(vs, "glob_proc", return_value=[str(proc)]):
            self.assertEqual(self.link("g1")[0], 0, self.last_err)

    def test_dry_run_and_offline(self):
        self.guest("g1")
        _, out, _ = self.run_vs("publish", "g1")
        v1 = out.split()[1]
        rc, out, err = self.link("g1", "--dry-run")
        self.assertEqual((rc, out), (0, f"g1 {v1}\n"), err)
        self.assertIn("would link it to", err)
        self.assertFalse(os.path.lexists(self.cvms / "g1"))
        self.assertEqual(os.listdir(self.cache / "live"), [])
        rc, _, err = self.link("g1", "--offline")              # nothing cached yet
        self.assertEqual(rc, 1, err)
        self.assertFalse(os.path.lexists(self.cvms / "g1"))
        self.assertEqual(self.link("g1")[0], 0)
        self.assertEqual(self.link("g1", "--offline")[:2], (0, f"g1 {v1}\n"))


# --- 8: concurrency --------------------------------------------------------------

class TestConcurrency(StoreTest):
    def test_two_ensures_one_rsync(self):
        self.guest("g1", files=False)
        self.run_vs("publish", "g1")
        self.init_cache()
        obj = self.manifest(self.mirror, "g1")["disks"][0]["chain"][0]
        ps = [self.cli("ensure", "g1", root=self.cache, extra={"FAKE_RSYNC_SLEEP": "1"}, wait=False) for _ in range(2)]
        outs = [p.communicate(timeout=60) for p in ps]
        self.assertEqual([p.returncode for p in ps], [0, 0], outs)
        self.assertEqual(outs[0][0], outs[1][0])
        self.assertEqual(len([c for c in self.rsync_calls() if c["src"].endswith(obj)]), 1)

    def test_gc_inside_ensure_spares_its_own_version(self):
        self.guest("g1")
        self.run_vs("publish", "g1")
        self.init_cache()
        self.assertEqual(self.run_vs("ensure", "g1", root=self.cache)[0], 0)
        m = self.manifest(self.cache, "g1")
        disk, key = m["disks"][0]["chain"][0], m["files"]["id_ed25519"]["object"]
        for o in os.listdir(self.cache / "used"):           # all long unused
            t = time.time() - 30 * 86400
            os.utime(self.cache / "used" / o, (t, t))
        (self.cache / "objects" / key).unlink()               # one object to fetch again
        stranger = self.cache / "objects" / ("sha256-" + "e" * 64 + ".qcow2")
        stranger.write_bytes(os.urandom(MiB))
        st = self.cache / "used" / stranger.name
        st.touch()
        os.utime(st, (time.time() - 40 * 86400,) * 2)
        store = vs.Store(self.cache, vs.Config())
        cap = store.use() - allocated_of(stranger) + 2 * 4096 + 4096
        rc, _, err = self.run_vs_cap("ensure", "g1", cap=cap)
        self.assertEqual(rc, 0, err)
        self.assertFalse(stranger.exists())
        self.assertTrue((self.cache / "objects" / disk).exists())
        self.assertTrue((self.cache / "objects" / key).exists())
        # Too small even so: no room, and its own version stays.
        (self.cache / "objects" / key).unlink()
        rc, _, err = self.run_vs_cap("ensure", "g1", cap=1)
        self.assertEqual(rc, 4, err)
        self.assertTrue((self.cache / "objects" / disk).exists())

    def run_vs_cap(self, *argv, cap):
        with mock.patch.dict(os.environ, {"VM_STORE_CAP": str(cap)}):
            return self.run_vs(*argv, root=self.cache)


def allocated_of(p):
    return os.lstat(p).st_blocks * 512


# --- 9: qcow2 headers ------------------------------------------------------------

class TestQcow2(StoreTest):
    def test_hand_built_headers(self):
        for version in (2, 3):
            p = self.tmp / f"v{version}.qcow2"
            make_qcow2(p, version=version)
            self.assertIsNone(vs.qcow2_backing(p))
            make_qcow2(p, version=version, backing="../objects/sha256-" + "a" * 64 + ".qcow2", snapshots=["fresh-install", "x"])
            self.assertEqual(vs.qcow2_backing(p), "../objects/sha256-" + "a" * 64 + ".qcow2")
            h = vs.qcow2_header(p)
            self.assertEqual((h["version"], h["virtual_size"], h["snapshots"]), (version, 64 * MiB, ["fresh-install", "x"]))
            make_qcow2(p, version=version, backing="/abs/base.qcow2")
            self.assertEqual(vs.qcow2_backing(p), "/abs/base.qcow2")
        raw = self.tmp / "raw.img"
        raw.write_bytes(b"\0" * 4096)
        self.assertIsNone(vs.qcow2_header(raw))
        self.assertIsNone(vs.qcow2_backing(self.tmp / "missing"))

    @unittest.skipUnless(HAVE_QEMU, "no qemu-img here")
    def test_real_images(self):
        base = self.tmp / "base.qcow2"
        make_disk(base, 1)
        top = self.tmp / "sub" / "top.qcow2"
        top.parent.mkdir()
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-b", "../base.qcow2", "-F", "qcow2", str(top)], check=True)
        self.assertIsNone(vs.qcow2_backing(base))
        self.assertEqual(vs.qcow2_backing(top), "../base.qcow2")
        for compat in ("0.10", "1.1"):
            p = self.tmp / f"c{compat}.qcow2"
            subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-o", f"compat={compat}", "-b", str(base),
                            "-F", "qcow2", str(p)], check=True)
            self.assertEqual(vs.qcow2_backing(p), str(base))
        self.assertEqual(vs.qcow2_header(base)["snapshots"], ["fresh-install"])
        self.assertEqual(vs.disk_info(top)["backing"], "../base.qcow2")


# Bigfoot's tools for the health probe (bigfoot_health), which runs here when
# VM_STORE_BIGFOOT_HOST is empty (StoreTest puts them on PATH, their knobs cleared):
# zpool answers FAKE_ZPOOL_HEALTH (ONLINE) and FAKE_ZPOOL_SCAN, or no pool
# (FAKE_ZPOOL_MISSING), after FAKE_ZPOOL_SLEEP (status: also FAKE_ZPOOL_STATUS_SLEEP);
# sudo runs only journalctl, which prints the file FAKE_JOURNAL and logs its args to
# FAKE_JOURNAL_ARGS, or fails as without a password (FAKE_SUDO_FAIL); flock finds the
# lock free, or busy (FAKE_FLOCK_BUSY).  Never this host's own zpool, log or flock.
FAKE_ZPOOL = r'''#!/bin/sh
[ -n "$FAKE_ZPOOL_SLEEP" ] && sleep "$FAKE_ZPOOL_SLEEP"
case "$1" in
list)
    if [ -n "$FAKE_ZPOOL_MISSING" ]; then echo "cannot open '$5': no such pool"; exit 1; fi
    echo "${FAKE_ZPOOL_HEALTH:-ONLINE}" ;;
status)
    [ -n "$FAKE_ZPOOL_STATUS_SLEEP" ] && sleep "$FAKE_ZPOOL_STATUS_SLEEP"
    printf '  pool: %s\n state: %s\n  scan: %s\nconfig:\n\nerrors: No known data errors\n' "$2" \
        "${FAKE_ZPOOL_HEALTH:-ONLINE}" "${FAKE_ZPOOL_SCAN:-scrub repaired 0B in 1 days with 0 errors on Sun Nov  9 2026}" ;;
*) exit 2 ;;
esac
'''
FAKE_SUDO = r'''#!/bin/sh
if [ -n "$FAKE_SUDO_FAIL" ]; then echo "sudo: a password is required" >&2; exit 1; fi
[ "$1" = -n ] && shift
[ "$1" = journalctl ] || { echo "fake sudo: journalctl only" >&2; exit 1; }
[ -n "$FAKE_JOURNAL_ARGS" ] && echo "$*" > "$FAKE_JOURNAL_ARGS"
[ -n "$FAKE_JOURNAL" ] && cat "$FAKE_JOURNAL"
exit 0
'''
FAKE_FLOCK = r'''#!/bin/sh
[ -n "$FAKE_FLOCK_BUSY" ] && exit 99
exit 0
'''
FAKE_BF_ENV = ("FAKE_ZPOOL_HEALTH", "FAKE_ZPOOL_SCAN", "FAKE_ZPOOL_MISSING", "FAKE_ZPOOL_SLEEP",
               "FAKE_ZPOOL_STATUS_SLEEP", "FAKE_JOURNAL", "FAKE_JOURNAL_ARGS", "FAKE_SUDO_FAIL", "FAKE_FLOCK_BUSY",
               "VM_STORE_BF_POOL", "VM_STORE_BF_RESET_WINDOW", "VM_STORE_BF_GATE", "VM_STORE_RSYNC_TIMEOUT",
               "VM_STORE_BWLIMIT_BIGFOOT")


# --- 10: push --------------------------------------------------------------------

class TestPush(StoreTest):
    def setUp(self):
        super().setUp()                                     # a healthy pool (the fake zpool)
        self.bf_stage.mkdir(parents=True)
        rc, _, err = self.run_vs("init", "--bigfoot")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads((self.bf_root / "store.json").read_text())["role"], "master")
        self.guest("p1")
        self.guest("p2", disks=["p2.qcow2", "p2-b.qcow2"])
        self.assertEqual(self.run_vs("publish", "p1", "p2")[0], 0, self.last_err)

    def stage_files(self):
        return [os.path.join(dp, f) for dp, _, fns in os.walk(self.bf_stage / "incoming") for f in fns]

    def test_push_command_lines_and_result(self):
        rc, _, err = self.run_vs("push", "--now")
        self.assertEqual(rc, 0, err)
        calls = self.rsync_calls()
        self.assertEqual(len(calls), 3)                     # objects, manifests, latest
        for c in calls:
            a = c["argv"]
            self.assertIn(f"--rsync-path=flock -w 270 -E 99 {self.bf_stage}/xfer.lock nice -n19 rsync", a)
            for flag in ("--bwlimit=40000", "--compress", "--compress-choice=zstd", "--compress-level=1",
                         "--no-whole-file", "--fsync", "--partial-dir=.partial", "--chmod=Fa-w", "-t"):
                self.assertIn(flag, a)
            self.assertTrue(c["dst"].startswith(f"{self.bf_stage}/incoming/"), c["dst"])   # never into the root
            self.assertTrue(any(x.startswith("--files-from=") for x in a))
        here = vs.local_listing(vs.Store(self.mirror, vs.Config()))
        lst = vs.bigfoot_listing(vs.Config())
        self.assertEqual(lst["objects"], here["objects"])
        self.assertEqual(lst["sha256"], here["sha256"])
        self.assertEqual(lst["latest"], here["latest"])
        self.assertEqual(self.stage_files(), [])
        for n in here["objects"]:
            self.assertEqual((self.bf_root / "objects" / n).stat().st_mode & 0o222, 0)
        self.assertEqual(self.run_vs("verify", "--where", "bigfoot")[0], 0, self.last_err)
        # Nothing left to send.
        n = len(self.rsync_calls())
        self.assertEqual(self.run_vs("push", "--now")[0], 0)
        self.assertEqual(len(self.rsync_calls()), n)

    def test_failed_objects_hold_back_manifests_and_latest(self):
        with mock.patch.dict(os.environ, {"FAKE_RSYNC_FAIL": "/objects"}):
            rc, _, err = self.run_vs("push", "--now")
        self.assertEqual(rc, 1)
        self.assertIn("skipped", err)
        self.assertEqual(list((self.bf_root / "objects").iterdir()), [])
        self.assertEqual(list((self.bf_root / "guests").iterdir()), [])
        rc, out, _ = self.run_vs("verify", "--where", "bigfoot")
        self.assertEqual(rc, 0)
        self.assertIn("unpushed", out)
        # The next push sends the rest and takes what was staged.
        self.assertEqual(self.run_vs("push", "--now")[0], 0, self.last_err)
        self.assertEqual(sorted(os.listdir(self.bf_root / "guests")), ["p1", "p2"])
        self.assertEqual(self.stage_files(), [])

    def test_window_and_dry_run(self):
        hour = time.localtime().tm_hour
        away = f"{(hour + 2) % 24:02d}:00-{(hour + 3) % 24:02d}:00"
        with mock.patch.dict(os.environ, {"VM_STORE_WINDOW": away}):
            rc, _, err = self.run_vs("push")
        self.assertEqual(rc, 3)
        self.assertIn("outside VM_STORE_WINDOW", err)
        rc, out, _ = self.run_vs("push", "--now", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("objects sha256-", out)
        self.assertEqual(self.rsync_calls(), [])
        import datetime as dt
        self.assertTrue(vs.in_window("00:30-07:30", dt.datetime(2026, 1, 1, 3, 0)))
        self.assertFalse(vs.in_window("00:30-07:30", dt.datetime(2026, 1, 1, 7, 30)))
        self.assertTrue(vs.in_window("22:00-02:00", dt.datetime(2026, 1, 1, 23, 0)))
        self.assertTrue(vs.in_window("22:00-02:00", dt.datetime(2026, 1, 1, 1, 0)))
        self.assertFalse(vs.in_window("22:00-02:00", dt.datetime(2026, 1, 1, 12, 0)))

    def test_status_shows_unpushed(self):
        rc, out, _ = self.run_vs("status", "--json")
        self.assertEqual(rc, 0)
        st = json.loads(out)
        self.assertEqual(st["role"], "mirror")
        self.assertEqual(len(st["unpushed"]["latest"]), 2)
        self.run_vs("push", "--now")
        st = json.loads(self.run_vs("status", "--json")[1])
        self.assertEqual(st["unpushed"], {"objects": [], "meta": [], "latest": []})

    def test_fetch_from_bigfoot_source(self):
        self.run_vs("push", "--now")
        self.init_cache()
        with mock.patch.dict(os.environ, {"VM_STORE_SOURCES": f"chopin={self.tmp}/down bigfoot={self.bf_root}"}):
            rc, _, err = self.run_vs("ensure", "p1", root=self.cache)
        self.assertEqual(rc, 0, err)
        fetches = [c for c in self.rsync_calls() if c["dst"].startswith(str(self.cache))]
        self.assertTrue(fetches)
        self.assertTrue(all(c["src"].startswith(str(self.bf_root)) for c in fetches), fetches)
        for c in fetches:
            self.assertIn("--bwlimit=40000", c["argv"])
            self.assertIn("--compress-choice=zstd", c["argv"])
            self.assertIn(f"--rsync-path=flock -w 270 -E 99 {self.bf_stage}/xfer.lock nice -n19 rsync", c["argv"])

    def test_defaults_follow_d1_and_d2(self):
        # D1: the root is bt's top level; D2: a cache's default source is Chopin alone.
        cfg = vs.Config({})
        self.assertEqual(cfg.bf_root, "/media/DantePool/bt/BkoStore")
        self.assertEqual(cfg.bf_stage, "/media/DantePool/bt/.bko-staging")
        self.assertEqual([(s.name, s.spec) for s in cfg.sources],
                         [("chopin", "m@chopin.local:/bkoscratch/bko-store")])

    def test_verify_bigfoot_only_from_the_mirror(self):
        self.init_cache()
        rc, _, err = self.run_vs("verify", "--where", "bigfoot", root=self.cache)
        self.assertEqual(rc, 1)
        self.assertIn("runs on the mirror", err)


    def bf(self, rel):
        return self.bf_root / "guests" / rel

    def test_unfinished_manifest_is_not_pushed(self):
        g = self.guest("p3")
        self.assertEqual(self.run_vs("publish", "p3")[0], 0, self.last_err)
        vd = self.mirror / "guests/p3/versions"
        vid = self.latest(self.mirror, "p3")
        (vd / f"{vid}.json.sha256").unlink()                # as a publish that died between the two
        (self.mirror / "guests/p3/latest").unlink()
        self.assertEqual(self.run_vs("push", "--now")[0], 0, self.last_err)
        self.assertFalse(self.bf(f"p3/versions/{vid}.json").exists())
        time.sleep(1.1)                                     # the re-publish's manifest gets other bytes
        self.assertEqual(self.run_vs("publish", "p3")[0], 0, self.last_err)
        self.assertEqual(self.run_vs("push", "--now")[0], 0, self.last_err)
        data = self.bf(f"p3/versions/{vid}.json").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         self.bf(f"p3/versions/{vid}.json.sha256").read_text().split()[0])
        self.assertEqual(data, (vd / f"{vid}.json").read_bytes())
        self.assertEqual(self.run_vs("verify", "--where", "bigfoot")[0], 0, self.last_err)
        self.init_cache()
        with mock.patch.dict(os.environ, {"VM_STORE_SOURCES": f"bigfoot={self.bf_root}"}):
            self.assertEqual(self.run_vs("ensure", "p3", root=self.cache)[0], 0, self.last_err)

    def test_a_manifest_that_differs_on_bigfoot_is_reported(self):
        self.assertEqual(self.run_vs("push", "--now")[0], 0, self.last_err)
        vid = self.latest(self.mirror, "p1")
        bj = self.bf(f"p1/versions/{vid}.json")
        data = bytearray(bj.read_bytes())
        data[-2:-1] = b" "                                  # same size, other bytes
        bj.chmod(0o644)
        bj.write_bytes(bytes(data))
        rc, out, _ = self.run_vs("verify", "--where", "bigfoot")
        self.assertEqual(rc, 1)
        self.assertIn(f"guests/p1/versions/{vid}.json: differs on bigfoot", out)
        n = len(self.rsync_calls())
        rc, _, err = self.run_vs("push", "--now")
        self.assertEqual(rc, 1)
        self.assertIn("differ on bigfoot", err)
        self.assertEqual(len(self.rsync_calls()), n)        # not sent again, never replaced
        self.assertEqual(bj.read_bytes(), bytes(data))

    def test_an_object_that_differs_on_bigfoot_is_reported_not_resent(self):
        self.assertEqual(self.run_vs("push", "--now")[0], 0, self.last_err)
        o = sorted((self.bf_root / "objects").iterdir())[0]
        o.chmod(0o644)
        with open(o, "ab") as f:
            f.write(b"x")
        n = len(self.rsync_calls())
        rc, _, err = self.run_vs("push", "--now")
        self.assertEqual(rc, 1)
        self.assertIn(f"objects/{o.name}", err)
        self.assertIn("differ on bigfoot", err)
        self.assertEqual(len(self.rsync_calls()), n)
        self.assertEqual(self.stage_files(), [])
        rc, out, _ = self.run_vs("verify", "--where", "bigfoot")
        self.assertEqual(rc, 1)
        self.assertIn("bytes on bigfoot", out)
        st = json.loads(self.run_vs("status", "--json")[1])
        self.assertEqual(st["unpushed"]["objects"], [])
        self.assertEqual(len(st["differs"]), 1)

    def test_accept_checks_each_manifest_against_its_sha256(self):
        inc = self.bf_stage / "incoming"
        here = vs.local_listing(vs.Store(self.mirror, vs.Config()))
        objs = sorted(here["objects"])
        for n in objs:
            shutil.copy(self.mirror / "objects" / n, inc / n)
        vid = self.latest(self.mirror, "p1")
        jr = f"p1/versions/{vid}.json"
        (inc / "meta" / "p1" / "versions").mkdir(parents=True)
        shutil.copy(self.mirror / "guests" / jr, inc / "meta" / jr)
        (inc / "meta" / (jr + ".sha256")).write_text("0" * 64 + f"  {vid}.json\n")
        (inc / "latest" / "p1").mkdir(parents=True)
        shutil.copy(self.mirror / "guests/p1/latest", inc / "latest/p1/latest")
        done = vs.remote_py(None, vs.ACCEPT_PY, {"root": str(self.bf_root), "stage": str(self.bf_stage),
                                                 "objects": objs, "meta": [jr, jr + ".sha256"], "latest": ["p1"]})
        self.assertEqual(done["meta"], [])
        self.assertEqual(done["latest"], [])
        self.assertTrue(any("doesn't match its .sha256" in x for x in done["skipped"]), done)
        self.assertFalse(self.bf(jr).exists())
        # An object already there with another size is reported, not replaced.
        o = self.bf_root / "objects" / objs[0]
        before = o.read_bytes()
        (inc / objs[0]).write_bytes(before + b"x")
        done = vs.remote_py(None, vs.ACCEPT_PY, {"root": str(self.bf_root), "stage": str(self.bf_stage),
                                                 "objects": [objs[0]], "meta": [], "latest": []})
        self.assertEqual(done["objects"], [])
        self.assertTrue(any("differs on bigfoot" in x for x in done["skipped"]), done)
        self.assertEqual(o.read_bytes(), before)
        self.assertFalse((inc / objs[0]).exists())

# --- 10b: Bigfoot's health gate --------------------------------------------------

def probe_out(health="ONLINE", scan="scrub repaired 0B in 1 days with 0 errors on Sun Nov  9 2026",
              journal=None, journal_rc=0, end=True):
    """BF_HEALTH_SH's output, by hand."""
    out = f"@@ list\n{health}\n@@ rc 0\n@@ status\n  pool: DantePool\n state: {health}\n  scan: {scan}\n@@ rc 0\n"
    if journal is not None:
        out += "@@ journal\n" + "".join(x + "\n" for x in journal) + f"@@ rc {journal_rc}\n"
    return out + ("@@ end\n" if end else "")


RESET = "Nov 14 07:46:25 bigfoot kernel: usb 4-1.4: reset SuperSpeed USB device number 3 using xhci_hcd"
DROP = "Nov 14 07:46:27 bigfoot kernel: usb 4-1.4.2: USB disconnect, device number 5"
READ71 = "Nov 14 07:46:29 bigfoot kernel: usb 4-1.4: device descriptor read/64, error -71"


class TestBigfootHealth(StoreTest):
    def setUp(self):
        super().setUp()
        self.jargs = self.tmp / "journal.args"
        os.environ["FAKE_JOURNAL_ARGS"] = str(self.jargs)

    def journal(self, *lines):
        j = self.tmp / "journal"
        j.write_text("".join(x + "\n" for x in lines))
        os.environ["FAKE_JOURNAL"] = str(j)

    def test_parse_online_is_healthy(self):
        ok, reasons, d = vs.parse_health(probe_out(journal=[]), "DantePool", "6h")
        self.assertEqual((ok, reasons), (True, []))
        self.assertEqual((d["health"], d["resets"], d["warnings"]), ("ONLINE", 0, []))

    def test_parse_suspended_and_degraded(self):
        for h in ("SUSPENDED", "DEGRADED", "FAULTED"):
            ok, reasons, d = vs.parse_health(probe_out(h, journal=[]), "DantePool", "6h")
            self.assertFalse(ok)
            self.assertEqual(reasons, [f"pool DantePool is {h}"])
            self.assertEqual(d["health"], h)
            self.assertFalse(vs.parse_health(probe_out(h), "DantePool", None, pool_only=True)[0])

    def test_parse_scrub_and_resilver(self):
        for kind in ("scrub", "resilver"):
            out = probe_out(scan=f"{kind} in progress since Fri Nov 14 01:00:00 2026", journal=[])
            ok, reasons, d = vs.parse_health(out, "DantePool", "6h")
            self.assertFalse(ok)
            self.assertEqual(reasons, [f"a {kind} is in progress on DantePool"])
            self.assertTrue(d["scan"].startswith(f"{kind} in progress"))
            self.assertEqual(vs.parse_health(out, "DantePool", None, pool_only=True)[:2], (True, []))

    def test_parse_usb_resets_in_the_window(self):
        noise = "Nov 14 07:40:00 bigfoot kernel: usb 3-1: new high-speed USB device number 2 using xhci_hcd"
        ok, reasons, d = vs.parse_health(probe_out(journal=[noise, RESET, DROP, READ71]), "DantePool", "6h")
        self.assertFalse(ok)
        self.assertEqual(d["resets"], 3)
        self.assertEqual(d["last_reset"], READ71)
        self.assertEqual(len(reasons), 1)
        self.assertIn("3 USB reset/disconnect line(s)", reasons[0])
        self.assertIn("last 6h", reasons[0])
        self.assertIn(READ71, reasons[0])
        self.assertTrue(vs.parse_health(probe_out(journal=[noise]), "DantePool", "6h")[0])

    def test_parse_sudo_unavailable_is_skipped_not_failed(self):
        out = probe_out(journal=["sudo: a password is required"], journal_rc=1)
        ok, reasons, d = vs.parse_health(out, "DantePool", "6h")
        self.assertEqual((ok, reasons, d["resets"]), (True, [], None))
        self.assertEqual(len(d["warnings"]), 1)
        self.assertIn("USB resets not checked", d["warnings"][0])
        self.assertIn("a password is required", d["warnings"][0])

    def test_parse_no_pool_or_truncated(self):
        ok, reasons, _ = vs.parse_health("@@ list\ncannot open 'DantePool': no such pool\n@@ rc 1\n@@ end\n",
                                         "DantePool", None)
        self.assertFalse(ok)
        self.assertIn("zpool list DantePool failed: cannot open", reasons[0])
        ok, reasons, d = vs.parse_health(probe_out(end=False), "DantePool", None)
        self.assertTrue(ok)
        self.assertIn("the probe didn't finish", d["warnings"])
        self.assertFalse(vs.parse_health("", "DantePool", None)[0])

    def test_probe_through_the_fakes(self):
        cfg = vs.Config()
        self.assertEqual(cfg.bf_pool, "DantePool")          # the tests' stage isn't under /media
        ok, reasons, d = vs.bigfoot_health(cfg)
        self.assertEqual((ok, reasons, d["health"], d["resets"]), (True, [], "ONLINE", 0), d)
        a = self.jargs.read_text().split()
        self.assertEqual(a[:2], ["journalctl", "_TRANSPORT=kernel"])   # every boot in the window, not -k's last
        self.assertIn("--since=-21600s", a)
        self.journal(RESET)
        os.environ["FAKE_ZPOOL_SCAN"] = "resilver in progress since Fri Nov 14 01:00:00 2026"
        ok, reasons, d = vs.bigfoot_health(vs.Config())
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 2, reasons)
        self.assertEqual(d["last_reset"], RESET)
        self.jargs.unlink()
        self.assertEqual(vs.bigfoot_health(vs.Config(), pool_only=True)[:2], (True, []))
        self.assertFalse(self.jargs.exists())               # a reader's probe reads no log
        with mock.patch.dict(os.environ, {"VM_STORE_BF_RESET_WINDOW": "0", "FAKE_ZPOOL_SCAN": ""}):
            ok, reasons, d = vs.bigfoot_health(vs.Config())
        self.assertEqual((ok, d["window"]), (True, None))
        self.assertFalse(self.jargs.exists())
        with mock.patch.dict(os.environ, {"FAKE_SUDO_FAIL": "1", "FAKE_ZPOOL_SCAN": ""}):
            ok, reasons, d = vs.bigfoot_health(vs.Config())
        self.assertEqual((ok, reasons), (True, []))
        self.assertIn("a password is required", d["warnings"][0])
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_HEALTH": "SUSPENDED", "FAKE_ZPOOL_SCAN": ""}):
            ok, reasons, _ = vs.bigfoot_health(vs.Config(), pool_only=True)
        self.assertEqual((ok, reasons), (False, ["pool DantePool is SUSPENDED"]))

    def test_no_answer_in_time_is_unhealthy(self):
        os.environ["FAKE_ZPOOL_SLEEP"] = "20"
        t0 = time.monotonic()
        with mock.patch.object(vs, "BF_PROBE_TIMEOUT", 1):
            ok, reasons, _ = vs.bigfoot_health(vs.Config())
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual((ok, reasons), (False, ["Bigfoot did not answer within 1s"]))
        with mock.patch.object(vs.subprocess, "Popen", side_effect=OSError("no ssh")):
            ok, reasons, _ = vs.bigfoot_health(vs.Config())
        self.assertFalse(ok)
        self.assertIn("no ssh", reasons[0])

    def test_ssh_command_line(self):
        seen = {}

        class Stdin(io.BytesIO):
            def close(self):
                seen["script"] = self.getvalue().decode()
                super().close()

        class P:
            pid, returncode = 0, 0

            def __init__(self, cmd, **kw):
                seen["cmd"], seen["kw"] = cmd, kw
                self.stdin, self.stdout, self.stderr = Stdin(), io.BytesIO(probe_out(journal=[]).encode()), io.BytesIO()

            def wait(self, timeout=None):
                return 0
        cfg = vs.Config({"VM_STORE_BIGFOOT_HOST": "m@bf", "VM_STORE_BF_POOL": "Tank"})
        with mock.patch.object(vs.subprocess, "Popen", P):
            ok, _, d = vs.bigfoot_health(cfg)
            self.assertEqual(seen["cmd"][-1].split()[-1], "-")              # no lock check
            vs.bigfoot_health(cfg, lock=True)
        self.assertTrue(ok)
        self.assertEqual(seen["cmd"][:-2], ["ssh", *vs.SSH_OPTS])
        self.assertEqual(seen["cmd"][-2], "m@bf")
        self.assertEqual(seen["cmd"][-1].split()[:4], ["sh", "-s", "--", "Tank"])
        self.assertEqual(seen["cmd"][-1].split()[-1], f"{vs.DEFAULT_BF_STAGE}/xfer.lock")
        self.assertTrue(seen["kw"].get("start_new_session"))
        self.assertIn(f"timeout -k 2 {vs.BF_STEP_TIMEOUT}", seen["script"])
        self.assertIn("flock -n -E 99", seen["script"])
        # Four steps, each with timeout's 2 s grace, and ssh's connect fit in the whole probe.
        self.assertLess(4 * (vs.BF_STEP_TIMEOUT + 2) + 5, vs.BF_PROBE_TIMEOUT)

    def test_timeout_keeps_what_the_probe_said(self):
        # zpool list says SUSPENDED, then zpool status hangs: the verdict names both.
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_HEALTH": "SUSPENDED", "FAKE_ZPOOL_STATUS_SLEEP": "20"}), \
                mock.patch.object(vs, "BF_PROBE_TIMEOUT", 1):
            ok, reasons, d = vs.bigfoot_health(vs.Config())
        self.assertFalse(ok)
        self.assertEqual(reasons, ["Bigfoot did not answer within 1s", "pool DantePool is SUSPENDED"])
        self.assertEqual((d["health"], d["warnings"]), ("SUSPENDED", []))
        # A step's own timeout is said as one.
        ok, reasons, _ = vs.parse_health("@@ list\n@@ rc 124\n@@ end\n", "DantePool", None)
        self.assertEqual(reasons, ["zpool list DantePool failed: timed out"])
        out = probe_out().replace("@@ rc 0\n@@ end", "@@ rc 137\n@@ end")
        self.assertIn("zpool status DantePool: timed out", vs.parse_health(out, "DantePool", None)[2]["warnings"])

    def test_interrupt_kills_the_probe(self):
        os.environ["FAKE_ZPOOL_SLEEP"] = "20"
        made = []
        real_popen, real_join = vs.subprocess.Popen, vs.threading.Thread.join

        def popen(*a, **kw):
            made.append(real_popen(*a, **kw))
            return made[-1]

        def join(self, timeout=None):                       # ^C while waiting for the answer
            if not made[0].poll() and not getattr(join, "hit", False):
                join.hit = True
                raise KeyboardInterrupt
            return real_join(self, timeout)
        with mock.patch.object(vs.subprocess, "Popen", popen), \
                mock.patch.object(vs.threading.Thread, "join", join), self.assertRaises(KeyboardInterrupt):
            vs.bigfoot_health(vs.Config())
        self.assertIsNotNone(made[0].poll())                # gone, not left behind

    def test_lock_state_after_a_failure(self):
        self.assertEqual(vs.bigfoot_health(vs.Config(), lock=True)[2]["xfer_lock"], "free")
        self.assertIsNone(vs.bigfoot_health(vs.Config())[2]["xfer_lock"])
        with mock.patch.dict(os.environ, {"FAKE_FLOCK_BUSY": "1"}):
            ok, text, d = vs.bigfoot_verdict(vs.Config(), lock=True)
        self.assertTrue(ok)
        self.assertEqual(d["xfer_lock"], "busy")
        self.assertIn("xfer.lock is still held", text)
        with mock.patch.dict(os.environ, {"FAKE_FLOCK_BUSY": "1", "FAKE_ZPOOL_HEALTH": "SUSPENDED"}):
            self.assertIsNone(vs.bigfoot_health(vs.Config(), lock=True)[2]["xfer_lock"])   # not tried

    def test_config(self):
        self.assertEqual(vs.Config({}).bf_pool, "DantePool")
        self.assertEqual(vs.Config({"VM_STORE_BIGFOOT_STAGE": "/media/Other/x/.st"}).bf_pool, "Other")
        self.assertEqual(vs.Config({}).rsync_timeout, "300")
        for k, v in (("VM_STORE_BF_GATE", "2"), ("VM_STORE_RSYNC_TIMEOUT", "5m"), ("VM_STORE_RSYNC_TIMEOUT", "0"),
                     ("VM_STORE_BF_RESET_WINDOW", "6 hours"), ("VM_STORE_BF_POOL", "a b")):
            with self.assertRaises(vs.Usage, msg=(k, v)):
                vs.Config({k: v})

    def test_bigfoot_health_command(self):
        rc, out, _ = self.run_vs("bigfoot-health")
        self.assertEqual(rc, 0, self.last_err)
        self.assertTrue(out.startswith("healthy: pool DantePool ONLINE"), out)
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_HEALTH": "SUSPENDED"}):
            rc, out, _ = self.run_vs("bigfoot-health")
            self.assertEqual((rc, out), (1, "unhealthy: pool DantePool is SUSPENDED\n"))
            rc, out, _ = self.run_vs("bigfoot-health", "--json")
            self.assertEqual(rc, 1)
            j = json.loads(out)
            self.assertEqual((j["ok"], j["health"], j["reasons"]),
                             (False, "SUSPENDED", ["pool DantePool is SUSPENDED"]))
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_SCAN": "scrub in progress since today"}):
            self.assertEqual(self.run_vs("bigfoot-health")[0], 1)
            self.assertEqual(self.run_vs("bigfoot-health", "--pool-only")[0], 0)
        self.assertEqual(self.run_vs("bigfoot-health", "--bogus")[0], 2)
        self.init_cache()
        rc, _, err = self.run_vs("bigfoot-health", root=self.cache)
        self.assertEqual(rc, 2)
        self.assertIn("runs on the mirror", err)
        r = self.cli("bigfoot-health")                      # the entry point's exit status
        self.assertEqual(r.returncode, 0, r.stderr)


class TestBigfootGate(StoreTest):
    def setUp(self):
        super().setUp()
        self.bf_stage.mkdir(parents=True)
        self.assertEqual(self.run_vs("init", "--bigfoot")[0], 0, self.last_err)
        self.guest("p1")
        self.assertEqual(self.run_vs("publish", "p1")[0], 0, self.last_err)

    def test_push_refuses_when_unhealthy_and_now_does_not_bypass(self):
        for env, why in (({"FAKE_ZPOOL_HEALTH": "SUSPENDED"}, "pool DantePool is SUSPENDED"),
                         ({"FAKE_ZPOOL_SCAN": "scrub in progress since today"}, "a scrub is in progress"),
                         ({"FAKE_ZPOOL_SLEEP": "20"}, "did not answer within 1s")):
            with mock.patch.dict(os.environ, env), mock.patch.object(vs, "BF_PROBE_TIMEOUT", 1), \
                    mock.patch.object(vs, "bigfoot_listing", side_effect=AssertionError("listed")) as bl:
                rc, _, err = self.run_vs("push", "--now")
            self.assertEqual(rc, 1, err)
            bl.assert_not_called()                          # the gate comes before any read of Bigfoot
            self.assertIn("push: Bigfoot is not healthy: ", err)
            self.assertIn(why, err)
            self.assertIn("; not pushing", err)
            self.assertEqual(self.rsync_calls(), [])
        self.assertEqual(list((self.bf_root / "objects").iterdir()), [])
        # Outside the window, the window says no first (exit 3), as before.
        hour = time.localtime().tm_hour
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_HEALTH": "SUSPENDED",
                                          "VM_STORE_WINDOW": f"{(hour + 2) % 24:02d}:00-{(hour + 3) % 24:02d}:00"}):
            self.assertEqual(self.run_vs("push")[0], 3)
        # publish --push: the publish stands, the push fails.
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_HEALTH": "SUSPENDED", "VM_STORE_WINDOW": "00:00-23:59"}):
            self.guest("p2")
            rc, _, err = self.run_vs("publish", "p2", "--push")
        self.assertEqual(rc, 1)
        self.assertIn("push failed (the publish stands", err)
        self.assertIsNotNone(self.latest(self.mirror, "p2"))

    def test_dry_run_reports_but_does_not_fail(self):
        # Not ONLINE: the verdict, and no listing (a suspended pool would hang it there).
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_HEALTH": "SUSPENDED"}), \
                mock.patch.object(vs, "bigfoot_listing", side_effect=AssertionError("listed")) as bl:
            rc, out, err = self.run_vs("push", "--now", "--dry-run")
        self.assertEqual(rc, 0, err)
        bl.assert_not_called()
        self.assertIn("push: Bigfoot is not healthy: pool DantePool is SUSPENDED; a real run would refuse", err)
        self.assertIn("push: not listing Bigfoot: pool DantePool is SUSPENDED (--dry-run)", err)
        self.assertEqual(out, "")
        # ONLINE but scrubbing: the verdict, and what a real run (once healthy) would send.
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_SCAN": "scrub in progress since today"}):
            rc, out, err = self.run_vs("push", "--now", "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("a scrub is in progress on DantePool; a real run would refuse", err)
        self.assertIn("objects sha256-", out)
        self.assertEqual(self.rsync_calls(), [])
        rc, _, err = self.run_vs("push", "--now", "--dry-run")
        self.assertIn("push: Bigfoot is healthy (pool DantePool ONLINE)", err)

    def test_gate_0_bypasses_loudly(self):
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_HEALTH": "SUSPENDED", "VM_STORE_BF_GATE": "0"}):
            rc, _, err = self.run_vs("push", "--now")
        self.assertEqual(rc, 0, err)
        self.assertIn("WARNING: VM_STORE_BF_GATE=0: push without checking Bigfoot's health", err)
        self.assertEqual(len(self.rsync_calls()), 3)

    def test_timeout_in_every_bigfoot_rsync(self):
        with mock.patch.dict(os.environ, {"VM_STORE_RSYNC_TIMEOUT": "60"}):
            self.assertEqual(self.run_vs("push", "--now")[0], 0, self.last_err)
        calls = self.rsync_calls()
        self.assertEqual(len(calls), 3)
        for c in calls:
            self.assertIn("--timeout=60", c["argv"])
        self.init_cache()
        with mock.patch.dict(os.environ, {"VM_STORE_SOURCES": f"bigfoot={self.bf_root}"}):
            self.assertEqual(self.run_vs("ensure", "p1", root=self.cache)[0], 0, self.last_err)
        fetches = self.rsync_calls()[3:]
        self.assertTrue(fetches)
        for c in fetches:
            self.assertIn("--timeout=300", c["argv"])
        # Chopin as a source isn't Bigfoot: no gate, no --timeout.  Bigfoot's host is, by any name.
        cfg = vs.Config({"VM_STORE_BIGFOOT_HOST": "m@bigfoot.local"})
        self.assertNotIn("--timeout=300", vs.Source("chopin", "m@c:/s", cfg).rsync_cmd("objects/x", "/d/"))
        for spec in ("m@bigfoot.local:/media/DantePool/bt", "bigfoot.local:/x"):
            src = vs.Source("bf", spec, cfg)
            self.assertTrue(src.bigfoot, spec)
            self.assertIn("--timeout=300", src.rsync_cmd("objects/x", "/d/"))
        self.assertFalse(vs.Source("bf", "/local/bt", vs.Config({"VM_STORE_BIGFOOT_HOST": ""})).bigfoot)

    def test_lock_wait_fits_in_the_timeout_and_is_retried(self):
        self.assertEqual(vs.xfer_rsync_path(vs.Config({"VM_STORE_RSYNC_TIMEOUT": "300"})),
                         f"--rsync-path=flock -w 270 -E 99 {vs.DEFAULT_BF_STAGE}/xfer.lock nice -n19 rsync")
        self.assertIn("flock -w 10 ", vs.xfer_rsync_path(vs.Config({"VM_STORE_RSYNC_TIMEOUT": "20"})))
        self.assertIn("flock -w 1 ", vs.xfer_rsync_path(vs.Config({"VM_STORE_RSYNC_TIMEOUT": "1"})))
        err = io.StringIO()
        with mock.patch.object(vs, "run_child", side_effect=[vs.XFER_BUSY, vs.XFER_BUSY, 0]) as rc_, \
                contextlib.redirect_stderr(err):
            self.assertEqual(vs.run_xfer(["rsync"]), 0)
        self.assertEqual(rc_.call_count, 3)
        self.assertEqual(err.getvalue().count("xfer.lock is busy"), 2)
        with mock.patch.object(vs, "run_child", return_value=vs.XFER_BUSY) as rc_, \
                mock.patch.object(vs, "XFER_LOCK_WAIT", 0):
            self.assertEqual(vs.run_xfer(["rsync"]), vs.XFER_BUSY)     # given up: its caller probes
        self.assertEqual(rc_.call_count, 1)

    def test_rsync_failure_runs_the_probe(self):
        def suspend(cfg, src, names, dest):                 # the pool goes as the rsync starts
            os.environ["FAKE_ZPOOL_HEALTH"] = "SUSPENDED"
            return 30
        with mock.patch.dict(os.environ, {}), mock.patch.object(vs, "push_rsync", side_effect=suspend) as pr:
            rc, _, err = self.run_vs("push", "--now")
        self.assertEqual(rc, 1)
        self.assertEqual(pr.call_count, 1)                  # stopped: no manifests, no latest, no accept
        self.assertIn("push: rsync of the objects failed (exit status 30); Bigfoot is not healthy: "
                      "pool DantePool is SUSPENDED; stopped", err)
        self.assertEqual(err.count("rsync of the objects failed"), 1)   # said once
        # A busy xfer.lock (another transfer past this one's lock wait) is named.
        with mock.patch.dict(os.environ, {"FAKE_FLOCK_BUSY": "1"}), \
                mock.patch.object(vs, "push_rsync", return_value=30):
            rc, _, err = self.run_vs("push", "--now")
        self.assertEqual(rc, 1)
        self.assertIn("Bigfoot looks healthy (pool DantePool ONLINE); Bigfoot's xfer.lock is still held", err)
        # A failure on a healthy pool says so, and goes on as before.
        with mock.patch.dict(os.environ, {"FAKE_RSYNC_FAIL": "/objects"}):
            rc, _, err = self.run_vs("push", "--now")
        self.assertEqual(rc, 1)
        self.assertIn("rsync of the objects (exit status 23) failed (Bigfoot looks healthy (pool DantePool ONLINE))",
                      err)
        self.assertIn("bigfoot skipped", err)

    def test_accept_failure_carries_the_verdict(self):
        def accept_fails(host, script, args, timeout=vs.REMOTE_TIMEOUT):
            if script is vs.ACCEPT_PY:
                os.environ["FAKE_ZPOOL_HEALTH"] = "SUSPENDED"
                raise vs.Unreachable("bigfoot timed out")
            return real(host, script, args, timeout)
        real = vs.remote_py
        with mock.patch.dict(os.environ, {}), mock.patch.object(vs, "remote_py", side_effect=accept_fails):
            rc, _, err = self.run_vs("push", "--now")
        self.assertEqual(rc, 1)
        self.assertIn("push: bigfoot's accept failed: bigfoot timed out; Bigfoot is not healthy: "
                      "pool DantePool is SUSPENDED", err)

    def test_init_bigfoot_gates_on_the_pool(self):
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_HEALTH": "SUSPENDED"}), \
                mock.patch.object(vs, "remote_py", side_effect=AssertionError("wrote")) as rp:
            rc, _, err = self.run_vs("init", "--bigfoot")
        self.assertNotEqual(rc, 0)
        rp.assert_not_called()
        self.assertIn("init --bigfoot: Bigfoot is not healthy: pool DantePool is SUSPENDED; not writing to it", err)
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_SCAN": "scrub in progress since today"}):
            self.assertEqual(self.run_vs("init", "--bigfoot")[0], 0, self.last_err)   # the pool only

    def test_readers_gate_on_the_pool_only(self):
        self.assertEqual(self.run_vs("push", "--now")[0], 0, self.last_err)
        self.init_cache()
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_SCAN": "scrub in progress since today"}):
            self.assertEqual(self.run_vs("verify", "--where", "bigfoot")[0], 0, self.last_err)
        with mock.patch.dict(os.environ, {"FAKE_ZPOOL_HEALTH": "SUSPENDED"}):
            rc, _, err = self.run_vs("verify", "--where", "bigfoot")
            self.assertEqual(rc, 1)
            self.assertIn("verify: Bigfoot is not healthy: pool DantePool is SUSPENDED; not reading from it", err)
            st = json.loads(self.run_vs("status", "--json")[1])
            self.assertIsNone(st["unpushed"])
            self.assertIn("SUSPENDED", st["bigfoot"])
            n = len(self.rsync_calls())
            with mock.patch.dict(os.environ, {"VM_STORE_SOURCES": f"bigfoot={self.bf_root}"}):
                rc, _, err = self.run_vs("ensure", "p1", root=self.cache)
                self.assertEqual(rc, 1)
                self.assertIn("source bigfoot: Bigfoot is not healthy: pool DantePool is SUSPENDED", err)
                self.assertEqual(self.run_vs("list", "--source", "bigfoot")[0], 1)
            self.assertEqual(len(self.rsync_calls()), n)
            # With Chopin first, ensure falls back past an unhealthy Bigfoot (here Chopin answers).
            with mock.patch.dict(os.environ, {"VM_STORE_SOURCES": f"bigfoot={self.bf_root} chopin={self.mirror}"}):
                rc, _, err = self.run_vs("ensure", "p1", root=self.cache)
            self.assertEqual(rc, 0, err)
            self.assertTrue(all(c["src"].startswith(str(self.mirror)) for c in self.rsync_calls()[n:]))

    def test_failed_fetch_from_bigfoot_runs_the_probe(self):
        self.assertEqual(self.run_vs("push", "--now")[0], 0, self.last_err)
        self.init_cache()
        cfg = vs.Config({**os.environ, "VM_STORE_SOURCES": f"bigfoot={self.bf_root}", "VM_STORE_ROOT": str(self.cache)})
        store = vs.Store(self.cache, cfg)
        name = sorted(os.listdir(self.bf_root / "objects"))[0]
        src = cfg.source("bigfoot")
        self.assertTrue(src.check() is None and src.gated == "")
        with mock.patch.dict(os.environ, {"FAKE_RSYNC_FAIL": str(self.bf_root), "FAKE_ZPOOL_HEALTH": "SUSPENDED"}):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(vs.StoreError):
                vs.fetch_object(store, name, [src])
        self.assertIn("bigfoot: rsync of", err.getvalue())
        self.assertIn("Bigfoot is not healthy: pool DantePool is SUSPENDED", err.getvalue())
        self.assertTrue(src.down)

# --- 11: the write guards --------------------------------------------------------

class TestGuards(StoreTest):
    def test_vm_run_guard_script(self):
        r = subprocess.run(["bash", str(TESTS / "vm-run-guard.sh")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("FAIL", r.stdout)

    def test_in_store(self):
        root = self.tmp / "s"
        (root / "objects").mkdir(parents=True)
        o = root / "objects" / ("sha256-" + "0" * 64 + ".qcow2")
        o.write_bytes(b"x")
        self.assertFalse(vs.in_store(o))                    # no store.json yet
        (root / "store.json").write_text("{}")
        self.assertTrue(vs.in_store(o))
        (self.tmp / "l.qcow2").symlink_to(o)
        self.assertTrue(vs.in_store(self.tmp / "l.qcow2"))
        self.assertFalse(vs.in_store(self.tmp / "l2.qcow2"))
        (root / "synth").mkdir()                             # H2's scratch guest inside a test root
        (root / "synth" / "synth.qcow2").write_bytes(b"x")
        self.assertFalse(vs.in_store(root / "synth" / "synth.qcow2"))
        self.assertFalse(vs.in_store(root / "store.json"))
        (root / "objects" / ".incoming").mkdir()
        self.assertTrue(vs.in_store(root / "objects" / ".incoming" / "tmp.x"))
        self.assertFalse(vs.in_store("/"))

    @unittest.skipUnless(LINUX, "qcow2-punch.py needs Linux's fallocate")
    def test_qcow2_punch_refuses_objects(self):
        root = self.tmp / "s"
        (root / "objects").mkdir(parents=True)
        (root / "store.json").write_text("{}")
        o = root / "objects" / ("sha256-" + "0" * 64 + ".qcow2")
        make_qcow2(o)
        o.chmod(0o644)                                      # writable, as root would find it
        before = sha(o)
        r = subprocess.run([sys.executable, str(TESTVM / "qcow2-punch.py"), str(o)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is a vm-store object", r.stderr)
        self.assertEqual(sha(o), before)


if __name__ == "__main__":
    unittest.main()
