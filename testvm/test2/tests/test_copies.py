"""P1's copy tests: U1.7 copy ids, the dirty map, untracked files and exclusions,
two copy runs of one ID at once, reuse with verification, --link-dest, gc; U1.8
the committed rule.  The "local" host stands in for a KVM test host."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(HERE)
sys.path.insert(0, TEST2)

from t2lib import canonical, copies  # noqa: E402

ENV = ("T2_MAC_ROOT", "T2_HOST_ROOT", "T2_COPY_ROOT", "T2_LEDGER", "T2_WORK")
_saved, _tmp = {}, None


def setUpModule():
    global _tmp
    _tmp = tempfile.mkdtemp(prefix="t2-test-copies-")
    for k in ENV:
        _saved[k] = os.environ.get(k)
        os.environ.pop(k, None)
    os.environ.update(T2_MAC_ROOT=os.path.join(_tmp, "mac"), T2_HOST_ROOT=os.path.join(_tmp, "host"),
                      T2_COPY_ROOT=os.path.join(_tmp, "copy-default"))


def tearDownModule():
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(_tmp, ignore_errors=True)


def sha(b):
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t2", GIT_AUTHOR_EMAIL="t2", GIT_COMMITTER_NAME="t2",
               GIT_COMMITTER_EMAIL="t2", GIT_CONFIG_NOSYSTEM="1", HOME=_tmp)
    return subprocess.run(["git", "-C", repo] + list(args), check=True, capture_output=True, env=env).stdout


FILES = {
    "testvm/vm-run": "#!/bin/sh\n",
    "testvm/stictest1/debian13/check.sh": "#!/bin/sh\necho 1\n",
    "testvm/stictest1/debian13/inner.sh": "#!/bin/sh\necho inner\n",
    "testvm/compare/fsmeta.py": "# fsmeta\n",
    "stick/build-round.sh": "#!/bin/sh\n",
    "holt/src/main.rs": "fn main() {}\n",
    "holt/Cargo.toml": "[package]\n",
    "docs/x.md": "outside the copy\n",
}


def write(root, rel, content):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)


def repo(name, files=FILES):
    r = os.path.join(_tmp, name)
    shutil.rmtree(r, ignore_errors=True)
    os.makedirs(r)
    git(r, "init", "-q")
    for rel, c in files.items():
        write(r, rel, c)
    os.symlink("../vm-run", os.path.join(r, "testvm/stictest1/vm-run-link"))
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    return r


def head(r):
    return git(r, "rev-parse", "HEAD").decode().strip()


class Ids(unittest.TestCase):
    """U1.7: ids and the dirty map."""

    def test_clean(self):
        r = repo("ids-clean")
        pv = copies.preview(r)
        self.assertEqual(pv.dirty, {})
        self.assertEqual(pv.id, head(r)[:7])
        self.assertNotIn("docs/x.md", pv.hashes)
        self.assertEqual(pv.hashes["testvm/stictest1/vm-run-link"], "->../vm-run")
        # excluded files, untracked: neither dirty nor hashed
        for rel in ("testvm/.DS_Store", "testvm/compare/__pycache__/fsmeta.cpython-314.pyc", "testvm/x.pyc",
                    "holt/target/release/bko-holt", "docs/new.md"):
            write(r, rel, "junk\n")
        pv = copies.preview(r)
        self.assertEqual((pv.dirty, pv.id), ({}, head(r)[:7]))
        self.assertFalse([p for p in pv.hashes if "__pycache__" in p or p.endswith((".pyc", ".DS_Store"))
                          or p.startswith("holt/target/")])

    def test_dirty(self):
        r = repo("ids-dirty")
        write(r, "testvm/stictest1/debian13/check.sh", "#!/bin/sh\necho 2\n")      # modified
        os.unlink(os.path.join(r, "testvm/vm-run"))                                 # deleted
        write(r, "testvm/stictest1/debian13/new.sh", "new\n")                       # untracked, deep
        write(r, "testvm/newdir/a/b.txt", "deeper\n")                               # untracked directory
        git(r, "mv", "stick/build-round.sh", "stick/build-round2.sh")               # renamed (staged)
        write(r, "testvm/.DS_Store", "junk\n")
        pv = copies.preview(r)
        want = {"testvm/stictest1/debian13/check.sh": sha("#!/bin/sh\necho 2\n"), "testvm/vm-run": None,
                "testvm/stictest1/debian13/new.sh": sha("new\n"), "testvm/newdir/a/b.txt": sha("deeper\n"),
                "stick/build-round2.sh": sha("#!/bin/sh\n"), "stick/build-round.sh": None}
        self.assertEqual(pv.dirty, want)
        self.assertEqual(pv.id, head(r)[:7] + ".d" + sha(canonical(want))[:8])
        self.assertEqual(copies.copy_id(head(r), want), pv.id)
        # a symlink's content is its target text, as git keeps it
        os.unlink(os.path.join(r, "testvm/stictest1/vm-run-link"))
        os.symlink("../other", os.path.join(r, "testvm/stictest1/vm-run-link"))
        self.assertEqual(copies.preview(r).dirty["testvm/stictest1/vm-run-link"], sha("../other"))


class Make(unittest.TestCase):
    """U1.7: make, reuse, verify, link-dest, two at once."""

    def setUp(self):
        self.root = os.path.join(_tmp, "copies-" + self._testMethodName)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_make_reuse_verify(self):
        r = repo("make-1")
        write(r, "testvm/stictest1/debian13/check.sh", "#!/bin/sh\necho changed\n")
        pv = copies.preview(r)
        out = copies.make(r, ["local"], root=self.root)
        c = out["local"]
        self.assertTrue(c.made)
        self.assertEqual(c.path, os.path.join(self.root, pv.id))
        self.assertTrue(os.path.isfile(os.path.join(c.path, ".t2-complete")))
        with open(os.path.join(c.path, "COMMIT")) as f:
            self.assertEqual(f.read(), head(r)[:7] + ".dirty\n")
        with open(os.path.join(c.path, "T2-COPY.json")) as f:
            info = json.load(f)
        self.assertEqual((info["id"], info["commit"], info["dirty"]), (pv.id, head(r), pv.dirty))
        self.assertTrue(info["made"].endswith("Z") and info["from"].endswith(":" + os.path.realpath(r)) or
                        info["from"].endswith(":" + r))
        self.assertEqual(copies.tree_hashes(c.path), pv.hashes)
        self.assertFalse(os.path.exists(os.path.join(c.path, "docs")))
        self.assertTrue(os.path.islink(os.path.join(c.path, "testvm/stictest1/vm-run-link")))
        self.assertEqual(sorted(os.listdir(self.root)), sorted([pv.id, f".{pv.id}.lock", f".{pv.id}.used"]))
        ok, diff = copies.verify("local", c.path, pv.hashes)
        self.assertTrue(ok, diff)
        used = os.path.join(self.root, f".{pv.id}.used")
        os.utime(used, (time.time() - 5 * 86400,) * 2)
        again = copies.make(r, ["local"], root=self.root)
        self.assertFalse(again["local"].made)
        self.assertGreater(os.stat(used).st_mtime, time.time() - 600)   # a reuse marks it taken
        # a copy that changed is never reused: it is set aside and a fresh one made
        write(c.path, "testvm/vm-run", "tampered\n")
        ok, diff = copies.verify("local", c.path, pv.hashes)
        self.assertEqual((ok, diff), (False, ["testvm/vm-run"]))
        msgs = []
        fresh = copies.make(r, ["local"], root=self.root, log=msgs.append)["local"]
        self.assertTrue(fresh.made)
        self.assertEqual(fresh.path, c.path)
        self.assertEqual(copies.verify("local", fresh.path, pv.hashes), (True, []))
        bad = [n for n in os.listdir(self.root) if n.startswith(f".{pv.id}.bad.")]
        self.assertEqual(len(bad), 1)
        with open(os.path.join(self.root, bad[0], "testvm/vm-run")) as f:
            self.assertEqual(f.read(), "tampered\n")
        self.assertTrue(any("set aside" in m for m in msgs), msgs)

    def test_clean_commit_file(self):
        r = repo("make-clean")
        c = copies.make(r, ["local"], root=self.root)["local"]
        with open(os.path.join(c.path, "COMMIT")) as f:
            self.assertEqual(f.read(), head(r)[:7] + "\n")
        self.assertEqual(os.path.basename(c.path), head(r)[:7])

    def test_link_dest(self):
        r = repo("make-link")
        first = copies.make(r, ["local"], root=self.root)["local"]
        write(r, "testvm/vm-run", "#!/bin/sh\n# edited\n")
        second = copies.make(r, ["local"], root=self.root)["local"]
        self.assertNotEqual(first.id, second.id)
        same = "testvm/stictest1/debian13/inner.sh"
        self.assertEqual(os.stat(os.path.join(first.path, same)).st_ino, os.stat(os.path.join(second.path, same)).st_ino)
        self.assertNotEqual(os.stat(os.path.join(first.path, "testvm/vm-run")).st_ino,
                            os.stat(os.path.join(second.path, "testvm/vm-run")).st_ino)
        with open(os.path.join(first.path, "testvm/vm-run")) as f:
            self.assertEqual(f.read(), "#!/bin/sh\n")   # the first copy never changes

    def test_two_at_once(self):
        r = repo("make-two")
        write(r, "testvm/stictest1/debian13/check.sh", "#!/bin/sh\necho two\n")
        res, errs = [], []

        def one():
            try:
                res.append(copies.make(r, ["local"], root=self.root)["local"])
            except Exception as e:
                errs.append(e)
        ths = [threading.Thread(target=one) for _ in range(3)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        self.assertEqual(errs, [])
        self.assertEqual(sorted(x.made for x in res), [False, False, True])
        pv = copies.preview(r)
        self.assertEqual(sorted(os.listdir(self.root)), sorted([pv.id, f".{pv.id}.lock", f".{pv.id}.used"]))
        self.assertFalse([d for d in os.listdir(os.path.join(self.root, pv.id)) if pv.id in d])   # nothing nested
        self.assertEqual(copies.tree_hashes(os.path.join(self.root, pv.id)), pv.hashes)

    def test_abort_leaves_nothing(self):
        from t2lib import hostrun
        s = hostrun.session("local", copies._self_source(), ["hold", self.root, "abcdef1"])
        st = s.recv()
        self.assertEqual((st["op"], st["complete"]), ("locked", False))
        os.makedirs(os.path.join(st["root"], st["part"], "testvm"))
        self.assertEqual(s.ask({"op": "abort"})["op"], "aborted")
        s.send({"op": "bye"})
        s.close()
        self.assertEqual(sorted(os.listdir(self.root)), [".abcdef1.lock"])
        # a holder whose maker went away removes its part too
        s = hostrun.session("local", copies._self_source(), ["hold", self.root, "abcdef1"])
        st = s.recv()
        os.makedirs(os.path.join(st["root"], st["part"]))
        s.close()
        self.assertEqual(sorted(os.listdir(self.root)), [".abcdef1.lock"])

    def test_gc(self):
        r = repo("gc")
        c1 = copies.make(r, ["local"], root=self.root)["local"]
        write(r, "testvm/vm-run", "v2\n")
        c2 = copies.make(r, ["local"], root=self.root)["local"]
        write(r, "testvm/vm-run", "v3\n")
        c3 = copies.make(r, ["local"], root=self.root)["local"]
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 10 * 86400))
        for c in (c1, c2, c3):
            p = os.path.join(c.path, "T2-COPY.json")
            with open(p) as f:
                info = json.load(f)
            info["made"] = old
            with open(p, "w") as f:
                json.dump(info, f)
        for c in (c1, c2):   # c3 was taken (reused) 2 days ago: kept though it is old
            os.utime(os.path.join(self.root, f".{c.id}.used"), (time.time() - 10 * 86400,) * 2)
        os.utime(os.path.join(self.root, f".{c3.id}.used"), (time.time() - 2 * 86400,) * 2)
        stale = os.path.join(self.root, f".{c3.id}.bad.{int(time.time() - 9 * 86400)}")
        recent = os.path.join(self.root, f".{c3.id}.bad.{int(time.time() - 3600)}")
        for d in (stale, recent):
            os.makedirs(os.path.join(d, "testvm"))
        hroot = os.path.join(_tmp, "gc-host")
        os.makedirs(os.path.join(hroot, "runs", "t2s-20261108T0315-4f2a"))
        with open(os.path.join(hroot, "runs", "t2s-20261108T0315-4f2a", "cells.json"), "w") as f:
            json.dump({"run": "t2s-20261108T0315-4f2a", "copy": {"id": c2.id}}, f)
        dry = copies.gc(["local"], root=self.root, host_root=hroot, dry_run=True)["local"]
        self.assertEqual(dry["deleted"], [c1.id])
        self.assertEqual(dry["set_aside"], [os.path.basename(stale)])
        self.assertTrue(os.path.isdir(c1.path) and os.path.isdir(stale))
        g = copies.gc(["local"], root=self.root, host_root=hroot)["local"]
        self.assertEqual(g["deleted"], [c1.id])
        self.assertEqual(g["set_aside"], [os.path.basename(stale)])
        self.assertEqual(sorted(g["kept"]), sorted([c2.id, c3.id]))
        self.assertEqual(g["referenced"], [c2.id])
        self.assertFalse(os.path.exists(c1.path) or os.path.exists(stale))
        self.assertTrue(os.path.isdir(c2.path) and os.path.isdir(c3.path) and os.path.isdir(recent))
        left = os.listdir(self.root)
        self.assertFalse({f".{c1.id}.lock", f".{c1.id}.used"} & set(left))   # its lock and stamp went with it
        self.assertIn(f".{c2.id}.lock", left)

    def test_bad_ids_refused(self):
        from t2lib import hostrun
        os.makedirs(self.root, exist_ok=True)
        keep = os.path.join(self.root, "abcdef1")
        os.makedirs(keep)
        for cid in ("", ".", "..", "abcdef1/x", "abcdef1.part.12", "ABCDEF1", "abc"):
            s = hostrun.session("local", copies._self_source(), ["hold", self.root, cid])
            st = s.recv()
            s.close()
            self.assertTrue(st is None or st.get("op") == "error", (cid, st))
        self.assertTrue(os.path.isdir(keep))
        import io
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(copies.host_main(["hold", self.root, ""]), 2)
        self.assertIn("bad copy id", err.getvalue())


class HostRun(unittest.TestCase):
    """hostrun (R1.8): what copies and pull run on a host, through the "local" host."""

    def test_script_session_and_env(self):
        from t2lib import hostrun
        r = hostrun.ssh("local", script="import os, sys, json\nprint(json.dumps([sys.argv[1:], json.loads(_T2_DATA),"
                                        " os.environ.get('PYTHONDONTWRITEBYTECODE')]))\n",
                        args=["a b", "c'd", "$HOME", "x;y"], data={"k": [1, "é"]})
        self.assertEqual(r.rc, 0, r.errtext)
        self.assertEqual(r.json(), [["a b", "c'd", "$HOME", "x;y"], {"k": [1, "é"]}, "1"])
        r = hostrun.ssh("local", "echo $PYTHONDONTWRITEBYTECODE; exit 3")
        self.assertEqual((r.rc, r.text.strip()), (3, "1"))
        src = ("import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}), flush=True)\n"
               "for line in sys.stdin.buffer:\n    r = json.loads(line)\n    if r.get('op') == 'bye':\n        break\n"
               "    print(json.dumps({'echo': r}), flush=True)\n")
        s = hostrun.session("local", src, ["p q", "it's"])
        self.assertEqual(s.recv(), {"argv": ["p q", "it's"]})
        self.assertEqual(s.ask({"op": "x", "s": 'a "b"'}), {"echo": {"op": "x", "s": 'a "b"'}})
        s.send({"op": "bye"})
        self.assertEqual(s.close()[0], 0)

    def test_dest_and_signal_needs_a_needle(self):
        from t2lib import hostrun
        self.assertEqual(hostrun.dest("chopin.local"), "m@chopin.local")   # as m, never the Mac's own user
        with self.assertRaises(ValueError):
            hostrun.signal("local", 12345, "TERM")

    def test_tree_info_stamps(self):
        from t2lib import hostrun
        ctx = hostrun.HostCtx.__new__(hostrun.HostCtx)
        base = os.path.join(_tmp, "stamps")

        def info(**stamps):
            shutil.rmtree(base, ignore_errors=True)
            for d in ("testvm", "stick"):
                os.makedirs(os.path.join(base, d))
                if stamps.get(d) is not None:
                    with open(os.path.join(base, d, ".backotter-commit"), "w") as f:
                        f.write(stamps[d] + "\n")
            t = ctx.tree_info(base)
            return t["commit"], t["committed"], t["dirty"]
        self.assertEqual(info(testvm="6fff54b", stick="6fff54b"), ("6fff54b", True, {}))
        self.assertEqual(info(testvm="6fff54b.dirty", stick="6fff54b"), ("6fff54b", False, None))
        self.assertEqual(info(testvm="", stick=""), (None, False, None))          # git rev-parse failed
        self.assertEqual(info(testvm="6fff54b"), ("6fff54b", False, None))        # one stamp: not enough
        self.assertEqual(info(testvm="6fff54b", stick="1234567"), (None, False, None))
        self.assertEqual(info(testvm="not a commit", stick="6fff54b"), ("6fff54b", False, None))
        self.assertEqual(info(), (None, False, None))

    def test_detached_alive_signal(self):
        from t2lib import hostrun
        d = os.path.join(_tmp, "detached")
        os.makedirs(d, exist_ok=True)
        pid = hostrun.detached("local", "sleep 60", os.path.join(d, "log"), os.path.join(d, "pid"))
        self.assertTrue(pid and hostrun.alive("local", pid))
        self.assertTrue(hostrun.alive("local", pid, "sleep 60"))
        self.assertFalse(hostrun.alive("local", pid, "no such command line"))
        self.assertFalse(hostrun.signal("local", pid, "TERM", needle="no such command line"))   # not ours: untouched
        self.assertTrue(hostrun.signal("local", pid, "TERM", needle="sleep 60"))
        try:
            os.waitpid(pid, 0)   # our own child (the local start): reap it, so it isn't a zombie
        except ChildProcessError:
            pass                 # subprocess's own cleanup reaped it first
        self.assertFalse(hostrun.alive("local", pid))
        with self.assertRaises(ValueError):
            hostrun.signal("local", 1)

    def test_paths(self):
        from t2lib import hostrun
        self.assertEqual(hostrun.dest("chopin"), "m@chopin.local")
        self.assertEqual(hostrun.dest("m@x.local"), "m@x.local")
        self.assertEqual(hostrun.rq("~/a b"), '"$HOME"/\'a b\'')
        self.assertEqual(hostrun.rq("/x/y"), "/x/y")
        if sys.platform == "darwin" and os.geteuid() != 0:
            self.assertEqual(hostrun.host_path(os.path.expanduser("~/backotter-work/x")), "~/backotter-work/x")
        self.assertEqual(hostrun.host_path("/bkoscratch/test2"), "/bkoscratch/test2")


class Committed(unittest.TestCase):
    """U1.8: the committed rule."""

    def test_rule(self):
        r = repo("committed")
        hh = copies.head_hashes(r)
        a = sha(FILES["testvm/stictest1/debian13/check.sh"])
        self.assertTrue(copies.committed_ok({"committed": True, "dirty": {}}, hh))
        self.assertTrue(copies.committed_ok({"committed": True, "dirty": None}, hh))
        self.assertFalse(copies.committed_ok({"committed": False, "dirty": None}, hh))   # a dirty synced tree
        self.assertFalse(copies.committed_ok({"committed": False}, hh))
        # an uncommitted copy whose diff HEAD now has
        self.assertTrue(copies.committed_ok({"committed": False, "dirty": {"testvm/stictest1/debian13/check.sh": a}}, hh))
        self.assertFalse(copies.committed_ok({"committed": False, "dirty": {"testvm/stictest1/debian13/check.sh": "0" * 64}}, hh))
        # a deletion counts once HEAD lacks the path too
        self.assertTrue(copies.committed_ok({"committed": False, "dirty": {"testvm/gone.sh": None}}, hh))
        self.assertFalse(copies.committed_ok({"committed": False, "dirty": {"testvm/vm-run": None}}, hh))
        # a symlink, by its target text
        self.assertTrue(copies.committed_ok({"committed": False, "dirty": {"testvm/stictest1/vm-run-link": sha("../vm-run")}}, hh))
        self.assertTrue(copies.committed_ok({"committed": False, "dirty": {"x": "1"}}, {"x": "1"}))

    def test_before_and_after_the_commit(self):
        r = repo("committed-2")
        write(r, "testvm/stictest1/debian13/check.sh", "#!/bin/sh\necho fixed\n")
        os.unlink(os.path.join(r, "testvm/vm-run"))
        pv = copies.preview(r)
        row = {"committed": False, "dirty": pv.dirty}
        self.assertFalse(copies.committed_ok(row, copies.head_hashes(r)))
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "the same diff, committed")
        self.assertTrue(copies.committed_ok(row, copies.head_hashes(r)))
        write(r, "testvm/stictest1/debian13/check.sh", "#!/bin/sh\necho later\n")
        git(r, "commit", "-q", "-am", "then changed again")
        self.assertFalse(copies.committed_ok(row, copies.head_hashes(r)))


if __name__ == "__main__":
    unittest.main()
