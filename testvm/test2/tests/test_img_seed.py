"""I7's tests (docs/design/img-speed.md §5.1, D5, D15, §14 row I7): stick/img-seed.sh and its host
half, stick/img-seed-host.sh, over a fixture farm: each host a directory (BKO_SEED_ROOT), reached
by a fake ssh that runs the command there, the nested host-to-host ssh too.  The digests are each
fixture host's own img_inputs.py digest linux (its stick/ is this worktree's).

No real host, no network, no sudo.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STICK = os.path.join(WT, "stick")
sys.path.insert(0, STICK)

import img_seed  # noqa: E402

SLIM = "slim-20261120"
ISO = "ubuntu-26.10-live-server-amd64.iso"
TOOLS = {"squashfs-tools": "1:4.6.1-1", "xorriso": "1.5.6-1.2+b1", "dosfstools": "4.2-1.2", "mtools": "4.0.48-1"}

# The fake ssh: [options] DEST CMD -> bash -c CMD with DEST's fixture root.  Per host, files in
# the root change what it says: host-tools.json (dpkg's versions), no-https (curl fails),
# locked (flock fails: the builder's lock is held).
FAKE_SSH = r"""#!/usr/bin/env bash
while [[ $# -gt 0 && $1 == -* ]]; do case $1 in -o) shift 2 ;; *) shift ;; esac; done
dest=$1; shift
name=${dest#*@}; name=${name%%.*}
root=$SEED_FARM/$name
[[ -d $root ]] || { echo "ssh: no host $dest" >&2; exit 255; }
echo "$name" >>"$SEED_FARM/ssh.log"
tools=$SEED_TOOLS; [[ -f $root/host-tools.json ]] && tools=$(cat "$root/host-tools.json")
curl=true; [[ -e $root/no-https ]] && curl=false
locked=; [[ -e $root/locked ]] && locked=1
exec env BKO_SEED_ROOT="$root" BKO_SEED_WORK="$root/work" BKO_SEED_SUDO= BKO_SEED_SPARSE= \
    BKO_SEED_CURL=$curl BKO_SEED_MARGIN_KIB=1024 BKO_IMG_HOST_TOOLS="$tools" BKO_DIGEST_CACHE="$root/cache.json" \
    FAKE_LOCKED=$locked PATH="$SEED_BIN:$PATH" bash -c "$*"
"""
FAKE_FLOCK = """#!/bin/sh
[ -z "$FAKE_LOCKED" ]
"""


def wr(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Farm(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="t2-img-seed-")
        self.farm = os.path.join(self.d, "farm")
        self.bin = os.path.join(self.d, "bin")
        wr(os.path.join(self.bin, "ssh"), FAKE_SSH, 0o755)
        if not shutil.which("flock"):
            wr(os.path.join(self.bin, "flock"), FAKE_FLOCK, 0o755)
        self.env = dict(os.environ, SEED_FARM=self.farm, SEED_BIN=self.bin, SEED_TOOLS=json.dumps(TOOLS),
                        BKO_SEED_SSH=os.path.join(self.bin, "ssh"), PYTHONDONTWRITEBYTECODE="1")
        for h in ("chopin", "cinqcent", "copland", "cage", "czerny"):
            self.host(h)
        self.source()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def root(self, h):
        return os.path.join(self.farm, h)

    def srv(self, h):
        return os.path.join(self.root(h), "bkoscratch", "imgbuild", "server")

    def host(self, h):
        w = os.path.join(self.root(h), "work")
        os.makedirs(os.path.join(w, "vms"))
        os.symlink(STICK, os.path.join(w, "stick"))
        ib = os.path.join(self.root(h), "bkoscratch", "imgbuild")
        os.makedirs(os.path.join(w, "img-media", "netbsd"))
        wr(os.path.join(w, "img-media", "netbsd", "MEDIA"), "11.1/base.tar.xz\n")
        for p in ("freebsd", "haiku"):     # links into imgbuild, as Chopin's are
            os.symlink(os.path.join(ib, "img-media", p), os.path.join(w, "img-media", p))
        os.symlink(os.path.join(ib, "vms", "freebsd15-builder"), os.path.join(w, "vms", "freebsd15-builder"))

    def slim(self, h, name, squash="squashfs bytes\n"):
        s = os.path.join(self.srv(h), name)
        for f, t in (("ubuntu-server-minimal.squashfs", squash), ("ubuntu-server-minimal.manifest", "pkg 1\n"),
                     ("ubuntu-server-minimal.size", "123\n"), ("vmlinuz", "kernel\n"), ("initrd", "initrd\n"),
                     ("report.txt", f"Source ISO: /bkoscratch/imgbuild/server/{ISO}\n")):
            wr(os.path.join(s, f), t)
        wr(os.path.join(s, "rootfs", "etc", "hostname"), "ubuntu\n")
        return s

    def point(self, h, name):
        link = os.path.join(self.srv(h), "slim")
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(name, link)

    def source(self):
        """Chopin: the server with slim -> SLIM and its ISO, real media and builder folders."""
        self.slim("chopin", SLIM)
        self.point("chopin", SLIM)
        wr(os.path.join(self.srv("chopin"), ISO), "iso bytes\n")
        w = os.path.join(self.root("chopin"), "work")
        os.symlink(self.srv("chopin"), os.path.join(w, "server"))
        ib = os.path.join(self.root("chopin"), "bkoscratch", "imgbuild")
        wr(os.path.join(ib, "img-media", "freebsd", "FreeBSD-memstick.img"), "memstick\n")
        wr(os.path.join(ib, "img-media", "freebsd", "MEDIA"), "FreeBSD-memstick.img\n")
        wr(os.path.join(ib, "img-media", "haiku", "haiku.iso"), "haiku\n")
        b = os.path.join(ib, "vms", "freebsd15-builder")
        wr(os.path.join(b, "freebsd15.qcow2"), "builder disk\n" * 1000)
        wr(os.path.join(b, "fingerprint"), "15.1-RELEASE\n")
        wr(os.path.join(b, "id_ed25519"), "key\n", 0o600)
        wr(os.path.join(b, ".vm-update.lock"), "")

    def seed(self, *args):
        r = subprocess.run([os.path.join(STICK, "img-seed.sh")] + list(args), env=self.env,
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr


class TestSeed(Farm):
    def test_seed_makes_a_host_ready(self):
        rc, out = self.seed("copland")
        self.assertEqual(rc, 0, out)
        self.assertIn("copland: ready", out)
        s = self.srv("copland")
        self.assertEqual(os.readlink(os.path.join(s, "slim")), SLIM)
        self.assertEqual(os.readlink(os.path.join(self.root("copland"), "work", "server")), s)
        for f in ("ubuntu-server-minimal.squashfs", "report.txt", "rootfs/etc/hostname"):
            self.assertEqual(rd(os.path.join(s, SLIM, f)), rd(os.path.join(self.srv("chopin"), SLIM, f)))
        self.assertEqual(rd(os.path.join(s, ISO)), "iso bytes\n")
        self.assertFalse(os.path.exists(os.path.join(s, ".seed-part")))
        # Seeded, it is ready by --check, and a second seed copies nothing.
        self.assertEqual(self.seed("--check", "copland")[0], 0)
        rc, out = self.seed("copland")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"{SLIM} is here already", out)

    def test_the_slim_manifests_match_the_planners(self):
        """The check is img_inputs.py's own: slim_entry and the ISO's SHA-256, as the base digest has them."""
        self.assertEqual(self.seed("copland")[0], 0)
        import img_inputs
        c = img_inputs.Cache(readonly=True)
        self.assertEqual(img_inputs.slim_entry(os.path.join(self.srv("copland"), "slim"), c),
                         img_inputs.slim_entry(os.path.join(self.srv("chopin"), "slim"), c))

    def test_check_changes_nothing(self):
        rc, out = self.seed("--check", "copland")
        self.assertEqual(rc, 1, out)
        self.assertIn("copland: NOT READY", out)
        self.assertIn(f"no {SLIM} or no {ISO}", out)
        self.assertFalse(os.path.exists(self.srv("copland")))
        self.assertFalse(os.path.lexists(os.path.join(self.root("copland"), "work", "server")))

    def test_a_slim_that_differs_is_not_pointed_at(self):
        self.slim("copland", SLIM, squash="other bytes\n")
        rc, out = self.seed("copland")
        self.assertEqual(rc, 1, out)
        self.assertIn("not repointed", out)
        self.assertIn(f"slim is {SLIM} (", out)
        self.assertFalse(os.path.lexists(os.path.join(self.srv("copland"), "slim")))

    def test_seeded_repoints_every_seeded_host_and_keeps_the_old_slim(self):
        for h in ("cinqcent", "copland"):
            self.slim(h, "slim-20261107")
            self.point(h, "slim-20261107")
            wr(os.path.join(self.srv(h), ISO), "iso bytes\n")
            os.symlink(self.srv(h), os.path.join(self.root(h), "work", "server"))
        rc, out = self.seed("--check", "--seeded")
        self.assertEqual(rc, 1, out)
        self.assertIn(f"server/slim is slim-20261107, the source's {SLIM}", out)
        rc, out = self.seed("--seeded")
        self.assertEqual(rc, 0, out)
        self.assertIn("seeded hosts: cinqcent, copland", out)
        for h in ("cinqcent", "copland"):
            self.assertEqual(os.readlink(os.path.join(self.srv(h), "slim")), SLIM)
            self.assertTrue(os.path.isdir(os.path.join(self.srv(h), "slim-20261107")))
        for h in ("cage", "czerny"):
            self.assertFalse(os.path.exists(self.srv(h)))     # not seeded: left alone

    def test_tools_older_or_unlike_the_sources(self):
        wr(os.path.join(self.root("copland"), "host-tools.json"),
           json.dumps(dict(TOOLS, **{"squashfs-tools": "1:4.5-3", "mtools": None})))
        wr(os.path.join(self.root("copland"), "no-https"), "")
        rc, out = self.seed("copland")
        self.assertEqual(rc, 1, out)
        self.assertIn("squashfs-tools 1:4.5-3 (needs 4.6 or later)", out)
        self.assertIn("the source's is 1:4.6.1-1 (D15", out)
        self.assertIn("no mtools", out)
        self.assertIn("no HTTPS to archive.ubuntu.com", out)

    def test_media_mends_dangling_links_only(self):
        rc, out = self.seed("--check", "--media", "copland")
        self.assertIn("img-media/freebsd dangles", out)
        rc, out = self.seed("--media", "copland")
        self.assertEqual(rc, 0, out)
        m = os.path.join(self.root("copland"), "work", "img-media")
        self.assertEqual(rd(os.path.join(m, "freebsd", "FreeBSD-memstick.img")), "memstick\n")
        self.assertFalse(os.path.exists(os.path.join(m, "freebsd", "MEDIA")))   # its own fetch writes it
        self.assertEqual(rd(os.path.join(m, "haiku", "haiku.iso")), "haiku\n")
        self.assertEqual(rd(os.path.join(m, "netbsd", "MEDIA")), "11.1/base.tar.xz\n")
        self.assertFalse(os.path.lexists(os.path.join(m, "netbsd", "11.1")))

    def test_builder_copied_and_checked(self):
        rc, out = self.seed("--builder", "copland")
        self.assertEqual(rc, 0, out)
        self.assertIn("builder copy checked: 4 files", out)
        b = os.path.join(self.root("copland"), "work", "vms", "freebsd15-builder")
        self.assertEqual(rd(os.path.join(b, "fingerprint")), "15.1-RELEASE\n")
        self.assertEqual(os.stat(os.path.join(b, "id_ed25519")).st_mode & 0o777, 0o600)
        # Once it is there, it is left alone.
        rc, out = self.seed("--builder", "copland")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("builder copy checked", out)

    def test_builder_refused_while_locked(self):
        wr(os.path.join(self.root("chopin"), "locked"), "")
        rc, out = self.seed("--builder", "copland")
        self.assertEqual(rc, 1, out)
        self.assertIn("is locked", out)
        vms = os.path.join(self.root("copland"), "bkoscratch", "imgbuild", "vms")
        self.assertEqual(os.listdir(vms), [])     # no part left behind

    def test_source_unusable(self):
        self.point("chopin", "slim-latest")
        rc, out = self.seed("copland")
        self.assertEqual(rc, 3, out)
        self.assertIn("not a slim-YYYYMMDD", out)

    def test_usage(self):
        self.assertEqual(self.seed()[0], 2)
        self.assertEqual(self.seed("chopin")[0], 2)
        self.assertEqual(self.seed("--seeded", "copland")[0], 2)
        self.assertEqual(self.seed("nosuchhost")[0], 2)


class TestRules(unittest.TestCase):
    def test_upstream(self):
        self.assertEqual(img_seed.upstream("1:4.6.1-1"), (4, 6, 1))
        self.assertEqual(img_seed.upstream("1.5.6-1.2+b1"), (1, 5, 6))
        self.assertEqual(img_seed.upstream("4.0.48-1"), (4, 0, 48))

    def test_facts_parse(self):
        f = img_seed.parse_facts("server_dir\tyes\nslims\tslim-1 slim-2\nmedia\tfreebsd\tdangling\t/x\n"
                                 "builder\tnone\t-\n")
        self.assertEqual(f["slims"], ["slim-1", "slim-2"])
        self.assertEqual(f["media"]["freebsd"], ("dangling", "/x"))
        self.assertEqual(f["builder"], ("none", "-"))


if __name__ == "__main__":
    unittest.main()
