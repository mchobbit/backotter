"""P1's key tests: U1.4 golden keys (and R2's two cases), U1.5 plumbing and soft
change no key, U1.6 GitSource = TreeSource = copy, and the value forms, globs and
spec layouts keys.resolve reads.  No host needed."""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from t2lib import GROUPS, canonical, keys  # noqa: E402

A, B, C, D = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
ENV = ("T2_MAC_ROOT", "T2_HOST_ROOT", "T2_COPY_ROOT", "T2_LEDGER", "T2_WORK", "BKO_GIT")
_saved, _tmp = {}, None


def setUpModule():
    global _tmp
    _tmp = tempfile.mkdtemp(prefix="t2-test-keys-")
    for k in ENV:
        _saved[k] = os.environ.get(k)
    os.environ.update(T2_MAC_ROOT=os.path.join(_tmp, "mac"), T2_HOST_ROOT=os.path.join(_tmp, "host"),
                      T2_COPY_ROOT=os.path.join(_tmp, "copy"), BKO_GIT=os.path.join(_tmp, "git"))
    os.environ.pop("T2_LEDGER", None)


def tearDownModule():
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(_tmp, ignore_errors=True)


def sha(s):
    return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t2", GIT_AUTHOR_EMAIL="t2", GIT_COMMITTER_NAME="t2",
               GIT_COMMITTER_EMAIL="t2", GIT_CONFIG_NOSYSTEM="1", HOME=_tmp)
    return subprocess.run(["git", "-C", repo] + list(args), check=True, capture_output=True, env=env).stdout


def make_repo(root, files, links=()):
    os.makedirs(root)
    git(root, "init", "-q")
    for rel, content in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(content.encode() if isinstance(content, str) else content)
    for rel, target in links:
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        os.symlink(target, p)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root


# A small world: specs in the three formats, one host's facts.
STIC_SPEC = {
    "v": 1, "family": "stic",
    "plumbing": ["testvm/vm-run", "shared:cmp-run.sh"],
    "soft": ["kernel", "zfs", "qemu", "ovmf"],
    "entries": {
        "debian13": {"bins": ["linux/restic-bko", "linux/rustic-bko"], "harness": ["testvm/stictest1/GUEST/**"],
                     "judge": ["entry:testvm/test2/stic/limits.json#debian13", "?testvm/stictest1/debian13/none.py"]},
        "haiku6": {"bins": ["haiku", "linux/restic-bko"], "harness": ["testvm/stictest1/haiku6/*.sh"],
                   "host": ["kernel", "pkg:e2fsprogs"]},
        "alpinebsd": {"bins": ["netbsd"], "xguest": ["netbsd11", "host:~/backotter-work/netbsd-tools"],
                      "harness": ["testvm/stictest1/alpinebsd/check.sh"]},
        "missingone": {"harness": ["testvm/stictest1/nothere/**"], "guest": "debian13"},
    },
}
HOLT_SPEC = {
    "v": 1, "family": "holt", "plumbing": ["testvm/holttest1d/ht1-lib.sh", "shared:cmp-run.sh"],
    "kinds": {"linux": {"plat": "linux", "bins": ["PLAT/bko-holt", "PLAT/rustic-bko"],
                        "harness": ["testvm/holttest1d/ht1.sh"], "rescuer": ["@firmware", "bin/*.deb", "bin/bcachefs"]},
              "bsd": {"bins": ["PLAT/bko-holt", "PLAT/rustic-bko"], "harness": ["testvm/holttest1d/ht1-bsd.sh"]}},
    "entries": {"debian13": {"kind": "linux"}, "debian13-stack": {"kind": "linux", "harness": ["testvm/console-boot.py"]},
                "freebsd15": {"kind": "bsd", "plat": "freebsd", "rescuer": ["freebsd15-ufs"]},
                "dragonfly6": {"kind": "bsd", "plat": "dragonfly", "rescuer": ["dragonfly6-newfsid.qcow2"], "pin": "cinqcent"}},
}
GATE_SPEC = {
    "v": 1, "family": "gate",
    "layers": {"loop:linux": {"harness": ["testvm/compare/*.py"], "judge": ["testvm/compare/expected-coverage.json"],
                              "host": ["kernel", "pkg:e2fsprogs", "pkg:xfsprogs"], "bins": ["linux/*-bko"]},
               "bin:version": {"bins": ["*/*-bko", "linux/bko-holt", "freebsd/bko-holt"]},
               "fork:restic": {"gitrepos": ["restic"], "harness": ["testvm/audit/fork-tests.sh"]}},
}
REPO_FILES = {
    "testvm/vm-run": "#!/bin/sh\n# vm-run\n",
    "testvm/stictest1/debian13/check.sh": "#!/bin/sh\necho debian13\n",
    "testvm/stictest1/debian13/inner.sh": "#!/bin/sh\necho inner\n",
    "testvm/stictest1/haiku6/check.sh": "#!/bin/sh\necho haiku\n",
    "testvm/stictest1/haiku6/guest-lib.sh": "# lib\n",
    "testvm/stictest1/haiku6/readme.txt": "not a .sh\n",
    "testvm/stictest1/alpinebsd/check.sh": "#!/bin/sh\necho alpinebsd\n",
    "testvm/holttest1d/ht1.sh": "#!/bin/sh\n# ht1\n",
    "testvm/holttest1d/ht1-bsd.sh": "#!/bin/sh\n# ht1-bsd\n",
    "testvm/holttest1d/ht1-lib.sh": "# lib\n",
    "testvm/console-boot.py": "print('boot')\n",
    "testvm/compare/fsmeta.py": "# fsmeta\n",
    "testvm/compare/compare.py": "# compare\n",
    "testvm/compare/expected-coverage.json": "{}\n",
    "testvm/audit/fork-tests.sh": "#!/bin/sh\n",
    "testvm/test2/stic/manifests.json": json.dumps(STIC_SPEC, indent=1),
    "testvm/test2/holt/spec.json": json.dumps(HOLT_SPEC, indent=1),
    "testvm/test2/gate/layers.json": json.dumps(GATE_SPEC, indent=1),
    "testvm/test2/stic/limits.json": json.dumps({"debian13": {"acl": "why"}, "haiku6": {"xattr": "why"}}),
    "stick/build-round.sh": "#!/bin/sh\n",
    "holt/Cargo.toml": "[package]\nname = \"bko-holt\"\nversion = \"0.2.4\"\n",
    "holt/LICENSE": "license text\n",
    "holt/README.md": "# holt\n",
    "holt/src/main.rs": "fn main() {}\n",
    "holt/tests/t.rs": "#[test] fn t() {}\n",
    "docs/outside.md": "not in a copy\n",
}


def facts(**over):
    f = {
        "host": "chopin",
        "versions": {"kernel": "6.12.115+deb13.1-amd64", "zfs": "2.3.9-1", "qemu": "QEMU emulator version 10.0.13",
                     "ovmf": "2025.02-8", "pkgs": {"e2fsprogs": "1.47.2-3+b12", "xfsprogs": "6.13.0-2"},
                     "rustc": "rustc 1.94.1", "go": None},
        "sets": {"roundrel4": {"sums": {"linux/restic-bko": A, "linux/rustic-bko": B, "netbsd/restic-bko": C,
                                        "netbsd/rustic-bko": D, "haiku/rustic-bko": A[:63] + "1",
                                        "linux/restic-commit": "e" * 64, "linux/restic-bko.digest": "f" * 64},
                               "commits": {"linux/restic-bko": "5" * 40},
                               "digests": {"linux/restic-bko": "1" * 64, "linux/rustic-bko": "2" * 64},
                               "digest_texts": {"linux/restic-bko": "restic-bko linux/amd64\ndigest " + "1" * 64 + "\n"},
                               "build_info": "round rel4"},
                 "bko-bin": {"sums": {"linux/rustic-bko": B, "freebsd/rustic-bko": C, "dragonfly/rustic-bko": D},
                             "digests": {}}},
        "holt_bins": {},
        "guests": {"debian13": {"generation": "9" * 64, "files": {"vm-run-options": "absent", "luks-passphrase": A}},
                   "debian13-stack": {"generation": "8" * 64, "files": {"vm-run-options": B}, "firmware": "uefi"},
                   "haiku6": {"generation": "7" * 64, "files": {}},
                   "alpinebsd": {"generation": "6" * 64, "files": {}},
                   "netbsd11": {"generation": "5" * 64, "files": {}},
                   "freebsd15": {"generation": "4" * 64, "files": {}},
                   "freebsd15-ufs": {"generation": "3" * 64, "files": {}},
                   "dragonfly6": {"generation": None, "imgsum": None, "files": {}}},
        "rescuers": {"images": {"rescuer.qcow2": {"sha256": C}, "rescuer-uefi.qcow2": {"sha256": D},
                                "dragonfly6-newfsid.qcow2": {"sha256": None, "imgsum": None}},
                     "bin": {"f2fs.deb": A, "jfs.deb": B, "bcachefs": C}},
        "plumbing": {"shared:cmp-run.sh": "e" * 64},
        "host_inputs": {"~/backotter-work/netbsd-tools": "dir:" + "f" * 64},
    }
    f.update(over)
    return f


class Golden(unittest.TestCase):
    """U1.4: three fixed input sets give these keys on every Python and host."""

    def test_canonical_form(self):
        self.assertEqual(canonical({"b": 1, "a": [1, "é"]}), '{"a":[1,"\\u00e9"],"b":1}')

    def test_golden_1(self):
        inp = {"bin": {"linux/restic-bko": "sha256:" + A},
               "harness": {"testvm/stictest1/debian13/check.sh": "sha256:" + B},
               "soft": {"kernel": "ver:6.12.48+deb13-amd64"}}
        want = "600b25864fd3ee85a6389899bc8dd97c51bcee1e5631fd3b16f2aa067c0ede07"
        self.assertEqual(keys.key("stic:debian13", inp), want)
        # the same, by the contract's formula written out (soft isn't keyed)
        text = ('{"cell":"stic:debian13","hard":{"bin":{"linux/restic-bko":"sha256:' + A + '"},'
                '"harness":{"testvm/stictest1/debian13/check.sh":"sha256:' + B + '"}},"v":1}')
        self.assertEqual(sha(text), want)
        self.assertEqual(keys.groups(inp), {
            "bin": "74250d68999ae202", "harness": "f6f9828ec8e1c57d", "judge": "44136fa355b3678a",
            "guest": "44136fa355b3678a", "xguest": "44136fa355b3678a", "rescuer": "44136fa355b3678a",
            "host": "44136fa355b3678a", "plumbing": "44136fa355b3678a", "soft": "7aa612c478298423"})

    def test_golden_2(self):
        inp = {"bin": {"linux/bko-holt": "code:" + C, "linux/rustic-bko": "sha256:" + B},
               "guest": {"debian13:generation": "gen:" + D, "debian13:vm-run-options": "absent"},
               "rescuer": {"rescuer/rescuer.qcow2": "sha256:" + A, "bin/bcachefs": "sha256:" + C},
               "plumbing": {"shared:cmp-run.sh": "sha256:" + A}}
        self.assertEqual(keys.key("holt:debian13+oneshot", inp),
                         "a501dee6bc63cc64711c4cf016e3652d44eedcabd19ebf81cba69618a89617fe")
        self.assertNotEqual(keys.key("holt:debian13", inp), keys.key("holt:debian13+oneshot", inp))

    def test_golden_3(self):
        inp = {"bin": {"linux/restic-bko": "sha256:" + A, "linux/restic-bko#digest": "ver:" + "1" * 64},
               "harness": {"git:restic": "git:" + "5" * 40, "testvm/compare/fs_roundtrip.py": "sha256:" + B},
               "judge": {"testvm/test2/stic/limits.json#x": "entry:" + C},
               "host": {"kernel": "ver:6.12.115+deb13.1-amd64", "pkg:xfsprogs": "ver:6.13.0-2"},
               "xguest": {"host:~/backotter-work/netbsd-tools": "dir:" + D}}
        self.assertEqual(keys.key("gate:loop:linux@chopin", inp),
                         "da312318371e7bf960f0c3e6f32900fc1cdf220048fc8aa211b6e112598d6c96")

    def test_r2_same_binaries_new_commit_digest_build_info(self):
        src = keys.TreeSource(self.repo)
        f1 = facts()
        f2 = copy.deepcopy(f1)
        s = f2["sets"]["roundrel4"]
        s["sums"]["linux/restic-commit"] = "0" * 64        # a rewritten commit file ("reused by ...")
        s["sums"]["linux/restic-bko.digest"] = "1" * 64    # a rewritten .digest ("built on" line)
        s["commits"]["linux/restic-bko"] = "6" * 40
        s["digest_texts"]["linux/restic-bko"] = "other text\n"
        s["build_info"] = "round rel5, another day"
        o = {"set": "roundrel4"}
        self.assertEqual(keys.resolve("stic:debian13", src, f1, None, o).key,
                         keys.resolve("stic:debian13", src, f2, None, o).key)

    def test_r2_changed_digest_line(self):
        src = keys.TreeSource(self.repo)
        f1 = facts()
        f2 = copy.deepcopy(f1)
        f2["sets"]["roundrel4"]["digests"]["linux/restic-bko"] = "3" * 64
        o = {"set": "roundrel4"}
        r1, r2 = keys.resolve("stic:debian13", src, f1, None, o), keys.resolve("stic:debian13", src, f2, None, o)
        self.assertNotEqual(r1.key, r2.key)
        self.assertEqual(keys.changed_groups(r1.groups, r2.groups), ["bin"])

    @classmethod
    def setUpClass(cls):
        cls.repo = make_repo(os.path.join(_tmp, "golden-repo"), REPO_FILES)


class NoKeyChange(unittest.TestCase):
    """U1.5: plumbing and soft changes move their group digests, never the key."""

    def setUp(self):
        self.repo = os.path.join(_tmp, "u15-repo")
        if not os.path.isdir(self.repo):
            make_repo(self.repo, REPO_FILES)
        self.src = keys.TreeSource(self.repo)

    def test_soft(self):
        f1, f2 = facts(), facts()
        f2["versions"] = dict(f1["versions"], kernel="6.12.116", qemu="QEMU 10.1", zfs="2.4.0", ovmf="2026")
        for cell in ("stic:debian13", "holt:debian13"):
            r1 = keys.resolve(cell, self.src, f1, None, {"set": "roundrel4"})
            r2 = keys.resolve(cell, self.src, f2, None, {"set": "roundrel4"})
            self.assertEqual(r1.key, r2.key, cell)
            self.assertEqual(keys.changed_groups(r1.groups, r2.groups), ["soft"], cell)

    def test_plumbing(self):
        f1, f2 = facts(), facts()
        f2["plumbing"] = {"shared:cmp-run.sh": "0" * 64}
        r1 = keys.resolve("stic:debian13", self.src, f1, None, {"set": "roundrel4"})
        r2 = keys.resolve("stic:debian13", self.src, f2, None, {"set": "roundrel4"})
        self.assertEqual(r1.key, r2.key)
        self.assertEqual(keys.changed_groups(r1.groups, r2.groups), ["plumbing"])
        # and a repo plumbing file: an edited testvm/vm-run
        d = os.path.join(_tmp, "u15-edit")
        shutil.copytree(self.repo, d, symlinks=True)
        with open(os.path.join(d, "testvm/vm-run"), "a") as f:
            f.write("# a comment\n")
        r3 = keys.resolve("stic:debian13", keys.TreeSource(d), f1, None, {"set": "roundrel4"})
        self.assertEqual(r1.key, r3.key)
        self.assertEqual(keys.changed_groups(r1.groups, r3.groups), ["plumbing"])
        shutil.rmtree(d)

    def test_host_versions_are_hard_where_listed(self):
        f2 = facts()
        f2["versions"] = dict(f2["versions"], kernel="6.12.116")
        r1 = keys.resolve("stic:haiku6", self.src, facts(), None, {"set": "roundrel4"})
        r2 = keys.resolve("stic:haiku6", self.src, f2, None, {"set": "roundrel4"})
        self.assertNotEqual(r1.key, r2.key)
        self.assertEqual(keys.changed_groups(r1.groups, r2.groups), ["host", "soft"])


class Sources(unittest.TestCase):
    """U1.6: a commit, its working tree and a private copy give the same values."""

    @classmethod
    def setUpClass(cls):
        files = dict(REPO_FILES)
        files["testvm/stictest1/debian13/bin.dat"] = bytes(range(256)) * 4
        cls.repo = make_repo(os.path.join(_tmp, "u16-repo"), files,
                             links=[("testvm/stictest1/debian13/boot.py", "../../console-boot.py")])
        # never copied, never keyed: excluded names (left untracked)
        for rel in ("testvm/__pycache__/x.cpython-314.pyc", "testvm/compare/y.pyc", "testvm/.DS_Store",
                    "holt/target/release/bko-holt"):
            p = os.path.join(cls.repo, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("junk\n")
        from t2lib import copies
        made = copies.make(cls.repo, ["local"], root=os.path.join(_tmp, "u16-copies"))
        cls.copy = made["local"].path

    def sources(self):
        return {"git": keys.GitSource(self.repo, "HEAD"), "tree": keys.TreeSource(self.repo),
                "copy": keys.TreeSource(self.copy)}

    def test_same_files_and_hashes(self):
        s = self.sources()
        names = {k: v.files() for k, v in s.items()}
        self.assertEqual(names["git"], names["tree"])
        self.assertEqual(names["git"], names["copy"])
        self.assertNotIn("docs/outside.md", names["tree"])
        self.assertFalse([n for n in names["tree"] if "__pycache__" in n or n.endswith((".pyc", ".DS_Store"))
                          or n.startswith("holt/target/")])
        for n in names["git"]:
            self.assertEqual(s["git"].sha256(n), s["tree"].sha256(n), n)
            self.assertEqual(s["git"].sha256(n), s["copy"].sha256(n), n)
        link = "testvm/stictest1/debian13/boot.py"
        self.assertEqual(s["tree"].read(link), b"../../console-boot.py")
        self.assertEqual(s["git"].mode(link), "120000")

    def test_same_inputs_and_keys(self):
        s = self.sources()
        for cell, opts in (("stic:debian13", {"set": "roundrel4"}), ("stic:haiku6", {"set": "roundrel4"}),
                           ("holt:debian13", {}), ("holt:freebsd15", {}), ("gate:loop:linux@chopin", {"set": "roundrel4"})):
            got = {k: keys.resolve(cell, v, facts(), None, opts) for k, v in s.items()}
            self.assertEqual(got["git"].inputs, got["tree"].inputs, cell)
            self.assertEqual(got["git"].inputs, got["copy"].inputs, cell)
            self.assertEqual(got["git"].key, got["copy"].key, cell)
            self.assertEqual(got["git"].spec, got["copy"].spec, cell)
        self.assertEqual(keys.holt_code(s["git"]), keys.holt_code(s["copy"]))

    def test_mapsource(self):
        other = os.path.join(_tmp, "u16-other")
        os.makedirs(os.path.join(other, "debian13"))
        with open(os.path.join(other, "debian13", "check.sh"), "w") as f:
            f.write("#!/bin/sh\necho from the other tree\n")
        m = keys.MapSource([keys.TreeSource(other, mount="testvm/stictest1"), keys.TreeSource(self.repo)])
        self.assertEqual(m.sha256("testvm/stictest1/debian13/check.sh"), sha("#!/bin/sh\necho from the other tree\n"))
        self.assertEqual(m.sha256("testvm/vm-run"), sha(REPO_FILES["testvm/vm-run"]))
        self.assertIsNone(m.sha256("testvm/stictest1/haiku6/check.sh"))   # the mount shadows the rest
        self.assertIn("testvm/stictest1/debian13/check.sh", m.files())
        self.assertNotIn("testvm/stictest1/haiku6/check.sh", m.files())


class Values(unittest.TestCase):
    """The value forms, globs and spec fields of contract 1.3."""

    @classmethod
    def setUpClass(cls):
        cls.repo = make_repo(os.path.join(_tmp, "values-repo"), REPO_FILES)
        cls.src = keys.TreeSource(cls.repo)

    def r(self, cell, f=None, opts=None):
        return keys.resolve(cell, self.src, f if f is not None else facts(), None, opts or {"set": "roundrel4"})

    def test_stic_entry(self):
        r = self.r("stic:debian13")
        i = r.inputs
        self.assertEqual(i["bin"], {"linux/restic-bko": "sha256:" + A, "linux/restic-bko#digest": "ver:" + "1" * 64,
                                    "linux/rustic-bko": "sha256:" + B, "linux/rustic-bko#digest": "ver:" + "2" * 64})
        self.assertEqual(sorted(i["harness"]), ["testvm/stictest1/debian13/check.sh", "testvm/stictest1/debian13/inner.sh"])
        self.assertEqual(i["judge"]["testvm/test2/stic/limits.json#debian13"],
                         "entry:" + sha(canonical({"acl": "why"})))
        self.assertEqual(i["judge"]["testvm/stictest1/debian13/none.py"], "absent")
        self.assertEqual(i["guest"]["debian13:generation"], "gen:" + "9" * 64)
        self.assertEqual(i["guest"]["debian13:luks-passphrase"], "sha256:" + A)
        self.assertEqual(i["guest"]["debian13:vm-run-options"], "absent")
        self.assertEqual(i["guest"]["debian13:ssh-user"], "absent")
        self.assertEqual(i["plumbing"], {"shared:cmp-run.sh": "sha256:" + "e" * 64,
                                         "testvm/vm-run": "sha256:" + sha(REPO_FILES["testvm/vm-run"])})
        self.assertEqual(i["soft"]["kernel"], "ver:6.12.115+deb13.1-amd64")
        self.assertEqual(r.spec["path"], "testvm/test2/stic/manifests.json#debian13")
        self.assertEqual(r.spec["sha256"], sha(REPO_FILES["testvm/test2/stic/manifests.json"]))
        self.assertTrue(all(keys.valid_value(v) for g in i.values() for v in g.values()))
        self.assertEqual(set(r.groups), set(GROUPS))

    def test_limits_entry_changes_only_its_cell(self):
        d = os.path.join(_tmp, "limits-edit")
        shutil.copytree(self.repo, d, symlinks=True)
        with open(os.path.join(d, "testvm/test2/stic/limits.json"), "w") as f:
            json.dump({"debian13": {"acl": "why", "xattr": "new"}, "haiku6": {"xattr": "why"}}, f)
        src2 = keys.TreeSource(d)
        a = self.r("stic:debian13")
        b = keys.resolve("stic:debian13", src2, facts(), None, {"set": "roundrel4"})
        self.assertEqual(keys.changed_groups(a.groups, b.groups), ["judge"])
        h1 = self.r("stic:haiku6")
        h2 = keys.resolve("stic:haiku6", src2, facts(), None, {"set": "roundrel4"})
        self.assertEqual(h1.key, h2.key)
        shutil.rmtree(d)

    def test_os_items_and_globs(self):
        i = self.r("stic:haiku6").inputs
        self.assertEqual(sorted(i["bin"]), ["haiku/rustic-bko", "haiku/rustic-bko#digest",
                                            "linux/restic-bko", "linux/restic-bko#digest"])
        self.assertEqual(i["bin"]["haiku/rustic-bko#digest"], "absent")
        self.assertEqual(sorted(i["harness"]), ["testvm/stictest1/haiku6/check.sh", "testvm/stictest1/haiku6/guest-lib.sh"])
        self.assertEqual(i["host"], {"kernel": "ver:6.12.115+deb13.1-amd64", "pkg:e2fsprogs": "ver:1.47.2-3+b12"})
        g = self.r("gate:loop:linux@chopin").inputs
        self.assertEqual(sorted(g["harness"]), ["testvm/compare/compare.py", "testvm/compare/fsmeta.py"])
        self.assertEqual(sorted(g["bin"]), ["linux/restic-bko", "linux/restic-bko#digest", "linux/rustic-bko",
                                            "linux/rustic-bko#digest"])
        self.assertNotIn("soft", g)   # gate: no soft facts
        self.assertTrue(keys.glob_re("testvm/**/x.py").match("testvm/x.py"))
        self.assertTrue(keys.glob_re("testvm/**/x.py").match("testvm/a/b/x.py"))
        self.assertFalse(keys.glob_re("testvm/*.py").match("testvm/a/x.py"))

    def test_xguest_and_missing(self):
        i = self.r("stic:alpinebsd").inputs
        self.assertEqual(i["xguest"], {"netbsd11:generation": "gen:" + "5" * 64,
                                       "host:~/backotter-work/netbsd-tools": "dir:" + "f" * 64})
        self.assertEqual(sorted(i["bin"]), ["netbsd/restic-bko", "netbsd/restic-bko#digest", "netbsd/rustic-bko",
                                            "netbsd/rustic-bko#digest"])
        r = self.r("stic:missingone")
        self.assertEqual(r.inputs["harness"], {"testvm/stictest1/nothere/**": "missing"})
        self.assertIn("harness/testvm/stictest1/nothere/**", r.unknown)   # a required input not there: unmeasured
        self.assertIn("debian13:generation", r.inputs["guest"])   # its own "guest" field
        with self.assertRaises(KeyError):
            self.r("stic:nosuchcheck")

    def test_guest_identity(self):
        f = facts()
        f["guests"]["debian13"] = {"generation": None, "imgsum": "img:" + C, "files": {}}
        self.assertEqual(self.r("stic:debian13", f).inputs["guest"]["debian13:generation"], "img:" + C)
        f["guests"]["debian13"] = {"generation": None, "imgsum": None, "files": {}}
        r = self.r("stic:debian13", f)
        self.assertEqual(r.inputs["guest"]["debian13:generation"], "img:unknown")
        self.assertIn("guest identity unknown: treated as changed (debian13)", r.notes)
        self.assertIn("guest/debian13:generation", r.unknown)
        del f["guests"]["debian13"]
        r = self.r("stic:debian13", f)
        self.assertEqual(r.inputs["guest"]["debian13:generation"], "missing")
        self.assertTrue(any("not on chopin" in n for n in r.notes))

    def test_holt(self):
        code = keys.holt_code(self.src)
        i = self.r("holt:debian13", opts={}).inputs
        self.assertEqual(i["bin"], {"linux/bko-holt": "code:" + code, "linux/rustic-bko": "sha256:" + B,
                                    "linux/rustic-bko#digest": "absent"})   # bko-bin has no .digest files
        self.assertEqual(i["rescuer"], {"rescuer/rescuer.qcow2": "sha256:" + C, "bin/f2fs.deb": "sha256:" + A,
                                        "bin/jfs.deb": "sha256:" + B, "bin/bcachefs": "sha256:" + C})
        s = self.r("holt:debian13-stack", opts={}).inputs
        self.assertEqual(s["rescuer"]["rescuer/rescuer-uefi.qcow2"], "sha256:" + D)   # @firmware: UEFI source
        self.assertIn("testvm/console-boot.py", s["harness"])
        f = facts()
        del f["rescuers"]["images"]["rescuer-uefi.qcow2"]
        r = self.r("holt:debian13-stack", f, opts={})
        self.assertIn("rescuer/rescuer.qcow2", r.inputs["rescuer"])
        self.assertIn("BIOS rescuer for a UEFI source", r.notes)
        b = self.r("holt:freebsd15", opts={}).inputs
        self.assertEqual(b["bin"], {"freebsd/bko-holt": "code:" + code, "freebsd/rustic-bko": "sha256:" + C,
                                    "freebsd/rustic-bko#digest": "absent"})
        self.assertEqual(b["rescuer"], {"freebsd15-ufs:generation": "gen:" + "3" * 64})
        dr = self.r("holt:dragonfly6", opts={})
        self.assertEqual(dr.pin, "cinqcent")
        self.assertEqual(dr.inputs["rescuer"]["rescuer/dragonfly6-newfsid.qcow2"], "img:unknown")
        # a HoltTest1 run (no CODE): the binaries it used, by sha256 (R14)
        t1 = self.r("holt:debian13", opts={"holt_sha256": {"linux": A}}).inputs
        self.assertEqual(t1["bin"]["linux/bko-holt"], "sha256:" + A)
        # a variant: the same inputs, another key
        v = self.r("holt:debian13+oneshot", opts={})
        self.assertEqual(v.inputs, self.r("holt:debian13", opts={}).inputs)
        self.assertNotEqual(v.key, self.r("holt:debian13", opts={}).key)

    def test_holt_code_covers_code_only(self):
        code = keys.holt_code(self.src)
        d = os.path.join(_tmp, "code-edit")
        shutil.copytree(self.repo, d, symlinks=True)
        for rel in ("holt/README.md", "holt/tests/t.rs"):
            with open(os.path.join(d, rel), "a") as f:
                f.write("more\n")
        self.assertEqual(keys.holt_code(keys.TreeSource(d)), code)
        with open(os.path.join(d, "holt/LICENSE"), "a") as f:
            f.write("more\n")
        self.assertNotEqual(keys.holt_code(keys.TreeSource(d)), code)
        lines = "".join(f"{p}\t{sha(REPO_FILES[p])}\n" for p in sorted(["holt/Cargo.toml", "holt/LICENSE", "holt/src/main.rs"]))
        self.assertEqual(code, sha(lines))
        shutil.rmtree(d)

    def test_gate_bko_holt_and_gitrepos(self):
        code = keys.holt_code(self.src)
        f = facts(holt_bins={code: {"sums": {"linux": A, "freebsd": None}}})
        g = self.r("gate:bin:version", f).inputs
        self.assertEqual(g["bin"]["linux/bko-holt"], "sha256:" + A)
        self.assertEqual(g["bin"]["freebsd/bko-holt"], "absent")
        self.assertIn("netbsd/rustic-bko", g["bin"])
        r = self.r("gate:fork:restic", f, {"gitrepos": {"restic": "5" * 40}})
        self.assertEqual(r.inputs["harness"]["git:restic"], "git:" + "5" * 40)
        r = self.r("gate:fork:restic", f, {})
        self.assertEqual(r.inputs["harness"]["git:restic"], "missing")   # no BKO_GIT checkout here

    def test_pending(self):
        r = self.r("stic:debian13", opts={"set": "roundt2r", "pending": "R5253b20-C96596bb-U6ccec58"})
        self.assertTrue(all(v == "pending:R5253b20-C96596bb-U6ccec58" for v in r.inputs["bin"].values()))
        self.assertIn("not built: R5253b20-C96596bb-U6ccec58", r.notes)
        h = self.r("holt:freebsd15", opts={"pending": "x"}).inputs["bin"]
        self.assertTrue(h["freebsd/rustic-bko"].startswith("sha256:"))   # holt's rustic-bko is bko-bin's

    def test_missing_set_and_shared(self):
        f = facts()
        f["sets"] = {}
        f["plumbing"] = {}
        r = self.r("stic:debian13", f)
        self.assertEqual(r.inputs["bin"]["linux/restic-bko"], "missing")
        self.assertEqual(r.inputs["plumbing"]["shared:cmp-run.sh"], "missing")
        self.assertIn("bin/linux/restic-bko", r.unknown)

    def test_unmeasured_inputs_are_unknown(self):
        # a binary the spec names that the set lacks, and a facts part that failed,
        # can't be measured: missing, and in unknown (a writer's row is then valid: false)
        f = facts()
        del f["sets"]["roundrel4"]["sums"]["linux/restic-bko"]
        r = self.r("stic:debian13", f)
        self.assertEqual(r.inputs["bin"]["linux/restic-bko"], "missing")
        self.assertIn("bin/linux/restic-bko", r.unknown)
        f = facts(versions=None, errors=["versions: OSError: boom"])
        r = self.r("stic:debian13", f)
        self.assertEqual(set(r.inputs["soft"].values()), {"missing"})
        self.assertTrue(all(f"soft/{n}" in r.unknown for n in r.inputs["soft"]))
        ok = self.r("stic:debian13")
        self.assertEqual(ok.unknown, [])
        self.assertEqual(self.r("stic:debian13", facts(versions={})).inputs["soft"]["kernel"], "absent")  # no such fact

    def test_wants_and_diff(self):
        w = keys.wants(keys.Specs(self.src), sets=["roundrel4"])
        self.assertEqual(w["plumbing"], ["shared:cmp-run.sh"])
        self.assertEqual(w["host_inputs"], ["~/backotter-work/netbsd-tools"])
        self.assertEqual(w["sets"], ["bko-bin", "roundrel4"])
        a, b = self.r("stic:debian13").inputs, self.r("stic:debian12" if False else "stic:debian13").inputs
        self.assertEqual(keys.diff_inputs(a, b), [])
        b = copy.deepcopy(a)
        b["harness"]["testvm/stictest1/debian13/inner.sh"] = "sha256:" + D
        self.assertEqual(keys.diff_inputs(a, b), [("harness", "testvm/stictest1/debian13/inner.sh",
                                                   a["harness"]["testvm/stictest1/debian13/inner.sh"], "sha256:" + D)])

    def test_spec_layouts(self):
        top = {"plumbing": ["testvm/vm-run"], "debian13": {"harness": ["testvm/vm-run"]}}
        sp = keys.Specs(self.src, overrides={"stic": top})
        r = keys.resolve("stic:debian13", self.src, facts(), sp, {"set": "roundrel4"})
        self.assertIn("testvm/vm-run", r.inputs["harness"])
        self.assertEqual(sp.get("stic").names(), ["debian13"])
        kinds = {"defaults": {"judge": ["testvm/compare/fsmeta.py"]},
                 "kinds": {"k": {"harness": ["testvm/vm-run"], "variants": {"big": {"harness": ["testvm/console-boot.py"]}}}},
                 "entries": {"g": {"kind": "k", "harness": ["testvm/holttest1d/ht1.sh"]}}}
        sp = keys.Specs(self.src, overrides={"holt": kinds})
        e = sp.get("holt").entry("g")
        self.assertEqual(e["harness"], ["testvm/vm-run", "testvm/holttest1d/ht1.sh"])
        self.assertEqual(e["judge"], ["testvm/compare/fsmeta.py"])
        self.assertIn("testvm/console-boot.py", sp.get("holt").entry("g", "big")["harness"])
        c = keys.Cell("gate:loop:linux@chopin")
        self.assertEqual((c.family, c.name, c.host, c.variant), ("gate", "loop:linux", "chopin", None))
        c = keys.Cell("holt:debian13-stack+bigger")
        self.assertEqual((c.family, c.name, c.variant), ("holt", "debian13-stack", "bigger"))

    def test_value_forms(self):
        for v in ("sha256:" + A, "gen:" + A, "img:" + A, "img:unknown", "ver:x", "git:" + "1" * 40, "dir:" + A,
                  "entry:" + A, "code:" + A, "pending:R1-C2-U3", "absent", "missing"):
            self.assertTrue(keys.valid_value(v), v)
        for v in ("sha256:abc", "git:" + A, "ver:" + "x" * 121, "nope", "", None, "absent "):
            self.assertFalse(keys.valid_value(v), v)
        self.assertEqual(keys.ver("x" * 200), "ver:" + "x" * 120)
        self.assertEqual(keys.ver(None), "absent")
        self.assertEqual(keys.ver(" QEMU  emulator\n10 "), "ver:QEMU emulator 10")


class NewForms(unittest.TestCase):
    """contract 1.4: fp:, projections, #SECTION, nested entry:, replace, bins_from_image, @image."""
    FP = {"generation": "e" * 64, "holt": A, "stic": B, "fine": C, "holt_was": [], "stic_was": [], "rules": "r"}

    @classmethod
    def setUpClass(cls):
        cls.root = os.path.join(_tmp, "newforms")
        files = {"testvm/update/in-guest.sh": "head\n# t2-section: prologue\np1\n# t2-section: Linux.apt\napt1\n"
                                              "  # t2-section: Linux.dnf\ndnf1\n# t2-section: Linux.apt\napt2\n",
                 "testvm/plain.sh": "no sections\n",
                 "testvm/test2/nest.json": json.dumps({"entries": {"g1": {"checks": {"a": 1}}}, "flat.key": 7}),
                 "testvm/x.py": "x\n", "testvm/holttest1d/ht1.sh": "h\n"}
        make_repo(cls.root, files)
        cls.src = keys.TreeSource(cls.root)
        cls.specs = keys.Specs(cls.src, overrides={
            "guest": {"v": 1, "family": "guest", "entries": {
                "g1": {"harness": ["testvm/update/in-guest.sh#Linux.apt", "testvm/plain.sh#Nope", "testvm/x.py"],
                       "judge": ["entry:testvm/test2/nest.json#entries.{guest}.checks",
                                 "entry:testvm/test2/nest.json#flat.key", "entry:testvm/test2/nest.json#entries.no.such"]}}},
            "holt": {"v": 1, "family": "holt", "kinds": {
                "linux": {"plat": "linux", "bins": ["PLAT/bko-holt", "PLAT/rustic-bko"], "rescuer": ["rescuer.qcow2"],
                          "harness": ["testvm/x.py"],
                          "variants": {"img": {"replace": ["rescuer", "bins"], "bins_from_image": True,
                                               "bins": ["PLAT/bko-holt", "PLAT/rustic-bko"], "rescuer": ["@image"],
                                               "harness": ["testvm/holttest1d/ht1.sh"]}}},
                "bsd": {"plat": "netbsd", "bins": ["PLAT/bko-holt"], "rescuer": ["g2"],
                        "variants": {"img": {"replace": ["bins"], "bins_from_image": True, "bins": ["PLAT/bko-holt"],
                                             "rescuer": ["@image"]}}}},
                "entries": {"g1": {"kind": "linux"}, "g2": {"kind": "bsd"}}},
            "img": {"v": 1, "family": "img", "entries": {
                "linux": {"bins_from_image": True, "bins": ["PLAT/restic-bko", "PLAT/test-image", "PLAT/release-image"],
                          "harness": ["testvm/x.py"]}}}})

    def facts(self, fp=None, images=True):
        g = {"generation": "e" * 64, "files": {}, "firmware": "bios", "fingerprint": fp}
        f = {"host": "h", "versions": {"kernel": "6", "zfs": "2", "qemu": "q", "ovmf": "o"},
             "guests": {"g1": g, "g2": {"generation": "f" * 64, "files": {}}},
             "rescuers": {"images": {"rescuer.qcow2": {"sha256": D}}, "bin": {}}}
        if images:
            f["images"] = {"linux": {"bins": {"restic-bko": A, "rustic-bko": B, "bko-holt": C},
                                     "release": {"name": "r.img", "sha256": D},
                                     "pin": {"sha256": "1" * 64, "current": True}},
                           "netbsd": {"bins": {"bko-holt": C}, "pin": {"sha256": "2" * 64, "current": False}}}
        return f

    def test_section(self):
        r = keys.resolve("guest:g1", self.src, self.facts(), self.specs)
        self.assertEqual(r.inputs["harness"]["testvm/update/in-guest.sh#Linux.apt"], "sha256:" + sha("apt1\napt2\n"))

    def test_section_fallback_is_the_whole_file_and_a_note(self):
        r = keys.resolve("guest:g1", self.src, self.facts(), self.specs)
        self.assertEqual(r.inputs["harness"]["testvm/plain.sh#Nope"], "sha256:" + sha("no sections\n"))
        self.assertTrue(any("no section Nope" in n for n in r.notes))

    def test_nested_entry(self):
        r = keys.resolve("guest:g1", self.src, self.facts(), self.specs)
        j = r.inputs["judge"]
        self.assertEqual(j["testvm/test2/nest.json#entries.g1.checks"], "entry:" + sha(canonical({"a": 1})))
        self.assertEqual(j["testvm/test2/nest.json#flat.key"], "entry:" + sha(canonical(7)))
        self.assertEqual(j["testvm/test2/nest.json#entries.no.such"], "absent")

    def test_guest_cell_keys_the_generation_never_the_fingerprint(self):
        r = keys.resolve("guest:g1", self.src, self.facts(self.FP), self.specs)
        self.assertEqual(r.inputs["guest"]["g1:generation"], "gen:" + "e" * 64)
        self.assertNotIn("g1:fingerprint", r.inputs["guest"])

    def test_projections(self):
        specs = keys.Specs(self.src, overrides={
            "holt": {"entries": {"g1": {}}}, "stic": {"entries": {"g1": {}}}})
        h = keys.resolve("holt:g1", self.src, self.facts(self.FP), specs)
        self.assertEqual(h.inputs["guest"]["g1:fingerprint"], "fp:" + A)
        self.assertNotIn("g1:generation", h.inputs["guest"])
        self.assertEqual(h.inputs["soft"]["g1:generation"], "gen:" + "e" * 64)
        self.assertNotIn("g1:fp-fine", h.inputs["soft"])
        s = keys.resolve("stic:g1", self.src, self.facts(self.FP), specs)
        self.assertEqual(s.inputs["guest"]["g1:fingerprint"], "fp:" + B)
        self.assertEqual(s.inputs["soft"]["g1:fp-fine"], "fp:" + C)
        self.assertTrue(keys.valid_value("fp:" + A))
        self.assertFalse(keys.valid_value("fp:abc"))

    def test_a_fingerprint_of_another_generation_or_none_is_todays_input(self):
        specs = keys.Specs(self.src, overrides={"holt": {"entries": {"g1": {}}}})
        for fp in (None, dict(self.FP, generation="0" * 64), dict(self.FP, holt="short")):
            r = keys.resolve("holt:g1", self.src, self.facts(fp), specs)
            self.assertEqual(r.inputs["guest"]["g1:generation"], "gen:" + "e" * 64)
            self.assertNotIn("g1:generation", r.inputs.get("soft", {}))

    def test_replace(self):
        e = keys.Specs(self.src, overrides=self.specs.overrides).get("holt").entry("g1", "img")
        self.assertEqual(e["rescuer"], ["@image"])
        self.assertEqual(e["bins"], ["PLAT/bko-holt", "PLAT/rustic-bko"])
        self.assertIn("testvm/x.py", e["harness"])         # not replaced: adds
        self.assertNotIn("replace", e)
        e = self.specs.get("holt").entry("g2", "img")
        self.assertEqual(e["rescuer"], ["g2", "@image"])   # BSD: the guest rescuer stays

    def test_bins_from_image_and_at_image(self):
        r = keys.resolve("holt:g1+img", self.src, self.facts(), self.specs)
        self.assertEqual(r.inputs["bin"], {"linux/bko-holt": "sha256:" + C, "linux/rustic-bko": "sha256:" + B})
        self.assertEqual(r.inputs["rescuer"], {"rescuer/img-linux": "sha256:" + "1" * 64})

    def test_a_stale_pin_is_unknown(self):
        r = keys.resolve("holt:g2+img", self.src, self.facts(), self.specs)
        self.assertEqual(r.inputs["rescuer"]["rescuer/img-netbsd"], keys.UNKNOWN_IMG)
        self.assertIn("rescuer/rescuer/img-netbsd", r.unknown)

    def test_img_cell(self):
        r = keys.resolve("img:linux", self.src, self.facts(), self.specs)
        self.assertEqual(r.inputs["bin"], {"linux/restic-bko": "sha256:" + A, "linux/test-image": "sha256:" + "1" * 64,
                                           "linux/release-image": "sha256:" + D})
        self.assertEqual(sorted(r.inputs["soft"]), ["ovmf", "qemu"])

    def test_empty_facts_resolve(self):
        for c in ("holt:g1+img", "img:linux"):
            r = keys.resolve(c, self.src, {}, self.specs, {"set": "x", "rustic_set": "x"})
            self.assertTrue(r.inputs["bin"])
        r = keys.resolve("img:linux", self.src, {}, self.specs)
        self.assertEqual(r.inputs["bin"]["linux/test-image"], keys.UNKNOWN_IMG)

    def test_wants_images(self):
        self.assertTrue(keys.wants(self.specs, ("img",))["images"])
        self.assertNotIn("images", keys.wants(self.specs, ("holt",)))


if __name__ == "__main__":
    unittest.main()
