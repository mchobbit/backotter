"""Unit tests for a case's read of its preparation's repository where it is (2026-11-25:
a shared-backup case copied 1.6-3.4 GiB into its RAM-disk share): ht1-bs2.sh's read-only
bind at share/repo, its release, the guards on every rm, the fallback to a copy, the
rebind for a run from rebuild on (bash, with mount, umount, mountpoint and findmnt stood
in for: no root, no VM); bs2-host's references (mount_refs(), prep_refs(), prune_preps()),
the reservation without the repository (repo_shared(), case_disk(), room.py shared_of(),
slim-sizes.tsv's d_shared); and ht1.sh's glue.  Nothing here reaches a host:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import json
import os
import re
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BS2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(BS2)
sys.path.insert(0, BS2)
sys.path.insert(0, HERE)

from bs2lib import case as C, room as RM, slim as SL  # noqa: E402
from test_bs2_hosts import load_host  # noqa: E402
import test_bs2_reuse as TR  # noqa: E402  (its fixture and hooks; a module, so its tests aren't found here again)

H = load_host()
GiB = 1 << 30

# Stand-ins: a "bind" is a symlink in place of the mount point's empty directory, listed in
# MNTTAB ("TARGET SOURCE OPTIONS"); umount puts the empty directory back.  So -ef, du and
# reads see the preparation's files through it, as through a real bind.
SHIMS = {
    "mount": r'''#!/bin/sh
echo "mount $*" >> "$SHIMLOG"
[ -z "$FAKE_MOUNT_FAIL" ] || { echo "mount: only root can use --bind" >&2; exit 32; }
from= to= opts=
while [ $# -gt 0 ]; do
    case $1 in --bind) ;; -o) shift; opts=$1 ;; *) if [ -z "$from" ]; then from=$1; else to=$1; fi ;; esac
    shift
done
rmdir "$to" && ln -s "$from" "$to" && echo "$to $from ${FAKE_OPTS:-$opts}" >> "$MNTTAB"
''',
    "umount": r'''#!/bin/sh
echo "umount $*" >> "$SHIMLOG"
lazy=; [ "$1" = -l ] && { lazy=1; shift; }
[ -z "$FAKE_UMOUNT_BUSY" ] || [ -n "$lazy" ] || { echo "umount: $1: target is busy" >&2; exit 32; }
grep -q "^$1 " "$MNTTAB" 2>/dev/null || { echo "umount: $1: not mounted" >&2; exit 32; }
grep -v "^$1 " "$MNTTAB" > "$MNTTAB.n"; mv "$MNTTAB.n" "$MNTTAB"
rm "$1" && mkdir "$1"
''',
    "mountpoint": r'''#!/bin/sh
[ "$1" = -q ] && shift
grep -q "^$1 " "$MNTTAB" 2>/dev/null
''',
    "findmnt": r'''#!/bin/sh
for a; do m=$a; done
awk -v m="$m" '$1 == m { print $3 }' "$MNTTAB" 2>/dev/null
''',
    # (The Mac's rm: -x is GNU's --one-file-system.)
    "rm": r'''#!/bin/sh
for a; do shift; if [ "$a" = --one-file-system ]; then set -- "$@" -x; else set -- "$@" "$a"; fi; done
exec /bin/rm "$@"
''',
    "sleep": "#!/bin/sh\n:\n",
}


class RepoBind(TR.PrepFixture):
    """ht1-bs2.sh's bind of the preparation's repository, against PrepFixture's preparation."""

    run_hooks = TR.TestReuseGlue.run_hooks

    def hooks(self, script, **env):
        shim = f"{self.t}/shim"
        os.makedirs(shim, exist_ok=True)
        for name, text in SHIMS.items():
            with open(f"{shim}/{name}", "w") as f:
                f.write(text)
            os.chmod(f"{shim}/{name}", 0o755)
        e = dict(MNTTAB=f"{self.t}/mnttab", HT_MODE="ram")
        e.update(env)
        return self.run_hooks(script, **e)

    def repo(self):
        return os.path.realpath(self.k) + "/backup/repo"

    def mnttab(self):
        try:
            with open(f"{self.t}/mnttab") as f:
                return f.read()
        except OSError:
            return ""

    def shimlog(self):
        with open(f"{self.t}/shim.log") as f:
            return f.read()

    def assert_prep_intact(self):
        with open(f"{self.k}/backup/repo/config") as f:
            self.assertEqual(f.read(), "{}")
        self.assertTrue(os.path.isdir(f"{self.k}/backup/repo"))

    def test_bound_read_only_where_it_is(self):
        os.chmod(f"{self.k}/backup/repo/config", 0o600)
        os.chmod(f"{self.k}/backup/repo-password", 0o600)
        r, R = self.hooks("bs2_source_reuse >/dev/null; BS2_SHARE_BACKUP=1; bs2_backup_reuse && echo BACKUP; "
                          'echo "FROM $(cat $R/bs2-repo-from)"; mountpoint -q $S/repo && echo MOUNTED; '
                          '[[ $S/repo/config -ef ' + self.repo() + '/config ]] && echo SAME; '
                          'bs2_share_readable; ls -l $S/repo-password | cut -c1-10; bs2_snapshot; '
                          'bs2_repo_release && echo RELEASED; [[ -e $S/repo ]] || echo GONE')
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.splitlines()
        self.assertIn("BACKUP", out)
        self.assertTrue(any(l.startswith("PASS the prepared backup, this case's tree as it stands (bound read-only where it is")
                            and self.repo() in l for l in out), out)
        self.assertFalse([l for l in out if l.startswith("note: backup")], out)
        self.assertIn(f"FROM {self.repo()}", out)
        for w in ("MOUNTED", "SAME", "RELEASED", "GONE", "latest:/mnt/ht1-src-p-20261113-120000-1"):
            self.assertIn(w, out)
        self.assertIn(f"mount --bind -o ro,nosuid,nodev,noexec {self.repo()} {R}/share/repo", self.shimlog())
        self.assertEqual(self.mnttab(), "")
        # The share readable by all, the preparation's repository never chmod'ed (read-only).
        self.assertIn("-rw-r--r--", out)
        self.assertEqual(stat.S_IMODE(os.stat(f"{self.k}/backup/repo/config").st_mode), 0o600)
        self.assert_prep_intact()
        for f in ("share/repo-password", "src-meta.json", "bs2-snapshots.json", "bs2-snap-path"):
            self.assertTrue(os.path.exists(f"{R}/{f}"), f)
        with open(f"{R}/logs/manifest.txt") as f:
            self.assertIn(f"repo-bind {self.repo()} config ", f.read())
        with open(f"{R}/logs/bs2-repo.log") as f:
            log = f.read()
        self.assertIn(f"bound {self.repo()} read-only at {R}/share/repo", log)
        self.assertIn(f"released {R}/share/repo", log)

    def test_falls_back_to_a_copy_with_a_note(self):
        for env, why in (({"BS2_REPO_BIND": "0"}, "BS2_REPO_BIND=0"),
                         ({"HT_MODE": "disk"}, "the case works on /bkoscratch (HT_RAM=0)"),
                         ({"FAKE_MOUNT_FAIL": "1"}, "mount --bind: mount: only root can use --bind"),
                         ({"FAKE_OPTS": "rw,nosuid"}, "wasn't read-only"),
                         ({"FAKE_MOUNT_FAIL": "1", "BS2_REPO_ROOM": "0"}, "the copy may outgrow it")):
            with self.subTest(env=env):
                r, R = self.hooks("bs2_source_reuse >/dev/null; BS2_SHARE_BACKUP=1; bs2_backup_reuse && echo BACKUP", **env)
                self.assertEqual(r.returncode, 0, r.stderr)
                out = r.stdout.splitlines()
                self.assertIn("BACKUP", out)
                note = [l for l in out if l.startswith("note: backup: the preparation's repository copied into the share")]
                self.assertTrue(note and why in note[0], out)
                self.assertTrue(any(l.startswith("PASS the prepared backup, this case's tree as it stands (copied, ") for l in out))
                # A copy of its own: a directory in the share, not the preparation's files.
                self.assertFalse(os.path.islink(f"{R}/share/repo"))
                self.assertTrue(os.path.isfile(f"{R}/share/repo/config"))
                self.assertFalse(os.path.samefile(f"{R}/share/repo/config", f"{self.k}/backup/repo/config"))
                self.assertFalse(os.path.exists(f"{R}/bs2-repo-from"))
                self.assertEqual(self.mnttab(), "")
                self.assert_prep_intact()

    def test_never_removes_through_a_mount(self):
        # A mount at share/repo this run didn't make (a dead run's, say): never removed through.
        other = f"{self.t}/someone-else"
        os.makedirs(other)
        with open(f"{other}/keep", "w") as f:
            f.write("x")
        r, R = self.hooks(f'mkdir -p $S; ln -s {other} $S/repo; echo "$S/repo {other} ro" > $MNTTAB; '
                          'bs2_repo_clear || echo REFUSED; bs2_source_reuse >/dev/null; BS2_SHARE_BACKUP=1; '
                          'bs2_backup_reuse || echo OWN; bs2_backup_own || echo NOTOWN')
        out = r.stdout.splitlines()
        self.assertIn("REFUSED", out)
        self.assertIn("note: backup: one of its own (share/repo is a mount it can't release: logs/bs2-repo.log)", out)
        self.assertIn("FAIL share/repo is a mount it can't release (logs/bs2-repo.log)", out)
        self.assertIn("NOTOWN", out)
        self.assertTrue(os.path.exists(f"{other}/keep"))
        # Its own bind: released first, then the share's repository goes, the preparation's stays.
        os.remove(f"{self.t}/mnttab")
        os.remove(f"{R}/share/repo")
        r, R = self.hooks('bs2_source_reuse >/dev/null; BS2_SHARE_BACKUP=1; bs2_backup_reuse >/dev/null; '
                          'bs2_repo_clear && echo CLEARED; [[ -e $S/repo ]] || echo GONE; '
                          f'bs2_repo_bind {self.repo()} && rm -rf --one-file-system "$R" && echo RM')
        self.assertIn("CLEARED", r.stdout.splitlines())
        self.assertIn("GONE", r.stdout.splitlines())
        self.assert_prep_intact()

    def test_a_busy_bind_is_released_lazily(self):
        r, R = self.hooks("bs2_source_reuse >/dev/null; BS2_SHARE_BACKUP=1; bs2_backup_reuse >/dev/null; "
                          "bs2_repo_release && echo RELEASED", FAKE_UMOUNT_BUSY="1")
        self.assertIn("RELEASED", r.stdout.splitlines(), r.stderr)
        self.assertEqual([l for l in self.shimlog().splitlines() if l.startswith("umount")],
                         [f"umount {R}/share/repo"] * 3 + [f"umount -l {R}/share/repo"])
        self.assertEqual(self.mnttab(), "")

    def test_bound_again_for_a_run_from_rebuild(self):
        r, R = self.hooks("bs2_source_reuse >/dev/null; BS2_SHARE_BACKUP=1; bs2_backup_reuse >/dev/null; bs2_repo_release")
        self.assertFalse(os.path.exists(f"{R}/share/repo"))   # (as ht_end leaves it for K)
        # A run from rebuild on: no backup phase, the record says which repository.
        r, R = self.hooks("bs2_repo_again && echo AGAIN; mountpoint -q $S/repo && echo MOUNTED; bs2_repo_again; "
                          "bs2_repo_release")
        out = r.stdout.splitlines()
        self.assertEqual(out, [f"PASS the prepared backup bound again, read-only ({self.repo()})", "AGAIN", "MOUNTED"])
        # It can't be bound: a copy, with a note; the preparation gone: a FAIL, never a run without it.
        r, R = self.hooks("bs2_repo_again && echo AGAIN; [[ -L $S/repo ]] || echo COPY", FAKE_MOUNT_FAIL="1")
        self.assertIn("AGAIN", r.stdout.splitlines())
        self.assertIn("COPY", r.stdout.splitlines())
        self.assertTrue(r.stdout.startswith("note: backup: the preparation's repository copied into the share, not bound again"))
        os.system(f"/bin/rm -rf {R}/share/repo")
        with open(f"{R}/bs2-repo-from", "w") as f:
            f.write(self.repo() + "\n")
        os.system(f"/bin/rm -rf {self.k}/backup/repo")
        r, R = self.hooks("bs2_repo_again || echo STOP")
        self.assertEqual(r.stdout.splitlines()[-2:], [f"FAIL the preparation's repository: neither bound nor copied ({self.repo()})",
                                                      "STOP"])

    def test_only_a_preparations_repository(self):
        os.makedirs(f"{self.t}/elsewhere")
        with open(f"{self.t}/elsewhere/config", "w") as f:
            f.write("{}")
        r, R = self.hooks(f'mkdir -p $S; bs2_repo_bind {self.t}/elsewhere || echo "NO $BS2_REPO_WHY"')
        self.assertEqual(r.stdout.splitlines(), [f"NO {os.path.realpath(self.t)}/elsewhere isn't a preparation's repository"])
        self.assertEqual(self.mnttab(), "")


class Ht1Glue(unittest.TestCase):
    """ht1.sh's side: the bind is released on every end that releases anything, the 9p
    export remaps a bound filesystem's inode numbers, a run from rebuild on binds again."""

    def setUp(self):
        with open(os.path.join(TESTVM, "holttest1d", "ht1.sh")) as f:
            self.ht1 = f.read()
        with open(os.path.join(TESTVM, "holttest1d", "ht1-lib.sh")) as f:
            self.lib = f.read()

    def test_release_and_rebind(self):
        rel = re.search(r"^ht_release\(\) \{(.*)\}$", self.ht1, re.M).group(1)
        self.assertTrue(rel.strip().startswith("[[ $VARIANT != bs2 ]] || bs2_repo_release;"), rel)
        reb = self.ht1[self.ht1.index("phase_rebuild() {"):]
        reb = reb[:reb.index("\n}\n")]
        self.assertIn("bs2_repo_again || return; bs2_share_readable", reb)
        self.assertIn('readonly=on${BS2_VIRTFS_MORE:-}"', reb)
        self.assertNotIn('\n    chmod -R a+rX "$S"\n', reb)
        # ht_end: ht_release, then ht_free (which unmounts what is left under R in RAM),
        # before the pass's deletes and the move to K.
        end = self.lib[self.lib.index("ht_end() {"):]
        i, j = end.index("declare -F ht_release >/dev/null && ht_release"), end.index('ht_free "$R" "$GV" "$HT_MODE"')
        self.assertLess(i, j)
        self.assertLess(j, end.index('rm -rf --one-file-system -- "$f"'))
        self.assertLess(j, end.index("ht_rsync -aHx"))

    def test_the_export_remaps_at_the_save_and_every_resume(self):
        r = TR.bash("rfw=(); bs2_rs_extra /s/share Q")
        self.assertIn("local,path=/s/share,mount_tag=bkoshare,security_model=none,readonly=on,multidevs=remap", r.stdout)


class MountRefs(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(os.system, f"/bin/rm -rf {self.t}")

    def mountinfo(self, text):
        p = f"{self.t}/mountinfo"
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_a_bind_names_its_source(self):
        p = self.mountinfo(
            "22 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw\n"
            "30 22 0:40 / /mnt/bko-ram rw,relatime shared:20 - tmpfs tmpfs rw\n"
            "99 30 259:2 /bkoscratch/backshot2/sources/pop24/preps/p-1/backup/repo /mnt/bko-ram/backshot2/r-t1/c/share/repo "
            "ro,nosuid,nodev,noexec shared:1 - ext4 /dev/nvme0n1p2 rw\n"
            # btrfs: the filesystem mounted from a subvolume, and a bind of a directory in it
            "40 22 0:50 /@ /srv rw shared:5 - btrfs /dev/sdb rw\n"
            "41 30 0:50 /@/a\\040b /mnt/bko-ram/x rw shared:5 - btrfs /dev/sdb rw\n")
        refs = H.mount_refs(p)
        self.assertIn("/bkoscratch/backshot2/sources/pop24/preps/p-1/backup/repo", refs)
        self.assertIn("/srv/a b", refs)
        self.assertNotIn("/", refs)
        self.assertEqual(H.mount_refs(f"{self.t}/none"), set())

    def test_prune_keeps_a_preparation_a_case_has_bound(self):
        d = f"{self.t}/sources/pop24"
        for n in ("p-1", "p-2"):
            os.makedirs(f"{d}/preps/{n}/backup/repo")
        os.symlink("preps/p-2", f"{d}/prep")
        repo = os.path.realpath(f"{d}/preps/p-1/backup/repo")
        proc = f"{self.t}/proc"
        os.makedirs(f"{proc}/self")
        with open(f"{proc}/self/mountinfo", "w") as f:
            f.write("22 1 259:2 / / rw - ext4 /dev/x rw\n"
                    f"99 22 259:2 {repo} {self.t}/ram/c/share/repo ro - ext4 /dev/x rw\n")
        refs = H.prep_refs([], proc=proc)
        self.assertIn(repo, refs)
        self.assertEqual(H.prune_preps(d, refs), [])
        self.assertTrue(os.path.isdir(f"{d}/preps/p-1/backup/repo"))
        self.assertEqual([n for n, _ in H.prune_preps(d, set())], ["p-1"])


ROWS = ("source\trecipe\td\tmem\tt1_s\tused_before_mib\tused_after_mib\tprep_mib\ttools_mib\tmeasured\td_shared\n"
        f"pop24\t{SL.RECIPE}\t14\t4\t358\t-\t-\t-\t-\tx\t11\n"
        f"mint22\t{SL.RECIPE}\t11\t4\t246\t-\t-\t-\t-\tno d_shared yet\n")


class SharedRoom(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(os.system, f"/bin/rm -rf {self.t}")
        p = f"{self.t}/slim-sizes.tsv"
        with open(p, "w") as f:
            f.write(ROWS)
        patch = mock.patch.object(RM, "SLIM_SIZES", p)
        patch.start()
        self.addCleanup(patch.stop)

    def test_shared_of(self):
        self.assertEqual(RM.slim_table()["pop24"]["d_shared"], 11)
        self.assertIsNone(RM.slim_table()["mint22"]["d_shared"])
        self.assertEqual(RM.shared_of("pop24", 3 * GiB), 11)              # measured
        self.assertEqual(RM.shared_of("mint22", int(2.9 * GiB)), 11 - 2)  # D less the repository's whole GiB
        self.assertEqual(RM.shared_of("mint22", GiB - 1), 11)
        self.assertEqual(RM.shared_of("alpine324", 5 * GiB), C.DISK_GIB)  # never under bs2's own
        # Placement stays the source's room: its own-backup cases go anywhere it goes.
        self.assertEqual(RM.need("pop24"), (14, 4))

    def test_the_repository_table(self):
        with mock.patch.object(RM, "SLIM_SIZES", os.path.join(BS2, "slim-sizes.tsv")):
            for s, row in RM.slim_table().items():
                if row["d_shared"] is not None:
                    self.assertGreaterEqual(row["d_shared"], C.DISK_GIB, s)
                    self.assertLessEqual(row["d_shared"], row["d"], s)


class SharedReservation(TR.PrepFixture):
    """bs2-host case_disk(): a case that will read its preparation's repository where it is
    reserves the source's room without it; any doubt, the room with it."""

    def setUp(self):
        super().setUp()
        with open(f"{self.root}/sources/alpine324/src.json", "w") as f:
            json.dump(TR.ALPINE, f)
        work = f"{self.t}/work"
        os.makedirs(f"{work}/bko-bin/linux")
        with open(f"{work}/bko-bin/linux/rustic-bko", "w") as f:
            f.write("rustic 1")
        from bs2lib import reuse as RU
        with open(f"{self.k}/prep.json") as f:
            saved = json.load(f)
        saved["backup"]["rustic"] = RU.sha256_file(f"{work}/bko-bin/linux/rustic-bko")
        with open(f"{self.k}/prep.json", "w") as f:
            json.dump(saved, f)
        self.case = f"{self.t}/c.case"
        C.write(self.case, {"source": "alpine324", "edits": [], "load_options": []})
        for p in (mock.patch.object(H, "ROOT", self.root), mock.patch.object(H, "VMS", self.vms),
                  mock.patch.object(H, "WORK", work), mock.patch.object(H, "holt_bin", lambda: self.holt),
                  mock.patch.object(H, "ceiling", lambda: (29.0, 23.2)), mock.patch.object(H, "du_bytes", lambda p: 3 * GiB + 5),
                  mock.patch.object(RM, "of", lambda s, *a, **k: (12, 4)), mock.patch.object(RM, "SLIM_SIZES", os.devnull),
                  mock.patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        for v in ("BS2_REUSE_SOURCE", "BS2_REUSE_BACKUP", "BS2_REPO_BIND") + H.OTHER_INPUTS:
            os.environ.pop(v, None)

    def test_a_shared_backup_reserves_without_its_repository(self):
        self.assertEqual(H.repo_shared(self.case, 1)[0], os.path.realpath(self.k) + "/backup/repo")
        self.assertEqual(H.case_disk(self.case, tier=1), 12 - 3)
        self.assertEqual(H.case_disk(self.case), 12 - 3)                 # both tiers serve (no Secure Boot here)
        self.assertEqual(H.case_disk(self.case, shared=False), 12)      # (the leftovers set's)
        self.assertEqual(H.case_disk(self.case, ssd=True), 12)

    def test_any_doubt_reserves_the_repository_too(self):
        own = f"{self.t}/own.case"
        C.write(own, {"source": "alpine324", "edits": [], "load_options": [], "content": {"recipe": "alpine-small-2", "seed": 3}})
        self.assertIsNone(H.repo_shared(own, 1))
        self.assertEqual(H.case_disk(own, tier=1), 12)
        for v in ("BS2_REUSE_SOURCE", "BS2_REUSE_BACKUP", "BS2_REPO_BIND"):
            with self.subTest(v=v), mock.patch.dict(os.environ, {v: "0"}):
                self.assertEqual(H.case_disk(self.case, tier=1), 12)
        for v in H.OTHER_INPUTS:
            with self.subTest(v=v), mock.patch.dict(os.environ, {v: "/x"}):
                self.assertEqual(H.case_disk(self.case, tier=1), 12)
        with open(f"{self.t}/work/bko-bin/linux/rustic-bko", "w") as f:
            f.write("rustic 2")   # (another rustic-bko: the case backs up its own)
        self.assertIsNone(H.repo_shared(self.case, 1))
        self.assertEqual(H.case_disk(self.case, tier=1), 12)
        with mock.patch.object(H, "repo_shared", mock.Mock(side_effect=OSError("boom"))):
            self.assertEqual(H.case_disk(self.case, tier=1), 12)

    def test_a_stale_preparation_reserves_the_repository_too(self):
        os.remove(f"{self.k}/logs/source-health.txt")
        self.assertIsNone(H.repo_shared(self.case, 1))
        self.assertEqual(H.case_disk(self.case, tier=1), 12)


if __name__ == "__main__":
    unittest.main()
