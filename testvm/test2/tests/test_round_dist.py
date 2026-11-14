"""A round's binaries and the shared bin sets reach every host, not only the build hosts
(gaps P7, P17, P22): stick/build-round.sh --dist-hosts and --dist-only (a round's bin from
Chopin to hosts that build nothing, checked there by sha256), testvm/promote-bins.sh
(bko-bin, cmp-bin and holt-bin from a build host), stic.py's round_step (built on the
table's build hosts whatever --hosts names, then copied to the rest) and holt.py's
ensure_holt (bko-holt built on build hosts only, shipped to every host when asked).

The shell scripts run against fake hosts: ssh runs the command here, in a directory that
is the host's home; rsync maps HOST:PATH there and runs this Mac's rsync; flock refuses
while the world's lock-held file exists."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
REPO = os.path.dirname(TESTVM)
sys.path.insert(0, T2)
sys.path.insert(0, HERE)

from t2lib import holt, stic  # noqa: E402
from t2lib import hosts as H  # noqa: E402
import test_holt  # noqa: E402
import test_stic  # noqa: E402

BUILD_ROUND = os.path.join(REPO, "stick", "build-round.sh")
PROMOTE = os.path.join(TESTVM, "promote-bins.sh")
REAL_RSYNC = shutil.which("rsync")

FAKE_SSH = r'''#!/usr/bin/env python3
import os, subprocess, sys
root = os.environ["FAKE_ROOT"]
a = sys.argv[1:]
while a and a[0].startswith("-"):
    a = a[2:] if a[0] in ("-o", "-p", "-i", "-l") else a[1:]
dest, cmd = a[0], " ".join(a[1:])
host = dest.split("@")[-1].split(".")[0]
with open(os.path.join(root, "log"), "a") as f:
    f.write(f"ssh {host} {cmd}\n")
if os.path.exists(os.path.join(root, "down-" + host)):
    sys.stderr.write(f"ssh: connect to host {dest}: No route to host\n")
    sys.exit(255)
home = os.path.join(root, "hosts", host)
os.makedirs(home, exist_ok=True)
env = dict(os.environ, HOME=home, FAKE_ON=host)
if os.path.isdir(os.path.join(home, "bin")):   # the host's own tools first
    env["PATH"] = os.path.join(home, "bin") + os.pathsep + env["PATH"]
sys.exit(subprocess.run(["bash", "-c", cmd], cwd=home, env=env).returncode)
'''

FAKE_RSYNC = r'''#!/usr/bin/env python3
import os, re, sys
root = os.environ["FAKE_ROOT"]
on = os.environ.get("FAKE_ON")
out, remote, a = [], False, sys.argv[1:]
i = 0
while i < len(a):
    x = a[i]
    if x == "-e":
        i += 2
        continue
    m = re.match(r"(?:[\w.-]+@)?([\w-]+)(?:\.[\w.-]+)?:(.*)$", x)
    if m and not x.startswith("-"):
        remote = True
        host = m.group(1)
        if os.path.exists(os.path.join(root, "down-" + host)):
            sys.exit(255)
        p = m.group(2)
        x = p if p.startswith("/") else os.path.join(root, "hosts", host, p)
    out.append(x)
    i += 1
with open(os.path.join(root, "log"), "a") as f:
    f.write(f"rsync on={on or 'mac'} {' '.join(sys.argv[1:])}\n")
if on and remote and os.path.exists(os.path.join(root, "noh2h")):
    sys.stderr.write("ssh: Host key verification failed.\n")
    sys.exit(12)
os.execv(os.environ["FAKE_REAL_RSYNC"], ["rsync"] + out)
'''

FAKE_FLOCK = '#!/bin/sh\n[ -e "$FAKE_ROOT/lock-held" ] && exit 1\nexit 0\n'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class World:
    """Fake hosts under a temporary directory: ROOT/hosts/NAME is NAME's home."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="t2-dist-")
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin)
        for name, text in (("ssh", FAKE_SSH), ("rsync", FAKE_RSYNC), ("flock", FAKE_FLOCK)):
            p = os.path.join(self.bin, name)
            with open(p, "w") as f:
                f.write(text)
            os.chmod(p, 0o755)
        if not shutil.which("sha256sum"):   # (an older Mac: shasum does what the hosts' sha256sum does)
            p = os.path.join(self.bin, "sha256sum")
            with open(p, "w") as f:
                f.write('#!/bin/sh\nexec shasum -a 256 "$@"\n')
            os.chmod(p, 0o755)
        self.tmp = os.path.join(self.root, "tmp")
        os.makedirs(self.tmp)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def home(self, host):
        return os.path.join(self.root, "hosts", host)

    def put(self, host, rel, text, mode=0o644):
        p = os.path.join(self.home(host), rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        os.chmod(p, mode)
        return p

    def read(self, host, rel):
        try:
            with open(os.path.join(self.home(host), rel)) as f:
                return f.read()
        except OSError:
            return None

    def files(self, host, rel):
        top = os.path.join(self.home(host), rel)
        out = {}
        for dp, dns, fns in os.walk(top):
            for n in fns:
                p = os.path.join(dp, n)
                with open(p) as f:
                    out[os.path.relpath(p, top)] = f.read()
        return out

    def sums(self, host, rel, skip=("SHA256SUMS",)):
        """SHA256SUMS of HOST:REL, as build-round.sh's shasums makes it."""
        top = os.path.join(self.home(host), rel)
        lines = []
        for r in sorted(self.files(host, rel)):
            if os.path.basename(r) in skip or os.path.basename(r).startswith("build-"):
                continue
            with open(os.path.join(top, r), "rb") as f:
                lines.append(f"{hashlib.sha256(f.read()).hexdigest()}  ./{r}\n")
        return "".join(lines)

    def flag(self, name, on=True):
        p = os.path.join(self.root, name)
        if on:
            open(p, "w").close()
        elif os.path.exists(p):
            os.unlink(p)

    def log(self):
        try:
            with open(os.path.join(self.root, "log")) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def run(self, argv, env=None):
        e = dict(os.environ)
        for k in (H.ENV, "CHOPIN", "CINQCENT", "BKO_ROUNDS", "BKO_REMOTE_DIR", "BKO_WORK_REMOTE"):
            e.pop(k, None)
        e.update(PATH=self.bin + os.pathsep + e["PATH"], FAKE_ROOT=self.root, FAKE_REAL_RSYNC=REAL_RSYNC,
                 TMPDIR=self.tmp, **(env or {}))
        return subprocess.run(["bash"] + list(argv), capture_output=True, text=True, env=e, timeout=120,
                              cwd=self.root)


def round_bin(w, host="chopin", rnd="r1", extra=None):
    """A built round's bin on HOST, with its SHA256SUMS."""
    b = f"backotter-work/round{rnd}/bin"
    w.put(host, f"{b}/linux/restic-bko", "restic-bko linux\n", 0o755)
    w.put(host, f"{b}/linux/rustic-bko", "rustic-bko linux\n", 0o755)
    w.put(host, f"{b}/freebsd/rustic-bko", "rustic-bko freebsd\n", 0o755)
    w.put(host, f"{b}/restic-commit", "abc\n")
    w.put(host, f"{b}/build-chopin.files", "linux/restic-bko\nlinux/rustic-bko\nrestic-commit\n")
    w.put(host, f"{b}/build-chopin.tsv", "chopin\trestic-bko\tlinux\tbuilt\t1\tx\ty\n")
    w.put(host, f"{b}/build-cinqcent.files", "freebsd/rustic-bko\n")
    for k, v in (extra or {}).items():
        w.put(host, f"{b}/{k}", v)
    w.put(host, f"{b}/SHA256SUMS", w.sums(host, b))
    return b


@unittest.skipUnless(REAL_RSYNC, "no rsync here")
class BuildRoundDist(unittest.TestCase):
    """build-round.sh --dist-hosts / --dist-only: the round's bin, whole, from the source build
    host to hosts that build nothing; checked there by sha256sum -c and SHA256SUMS."""

    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)

    def br(self, *args, env=None):
        return self.w.run([BUILD_ROUND] + list(args), env)

    def test_dist_only_copies_and_checks(self):
        b = round_bin(self.w)
        r = self.br("--dist-only", "--dist-hosts", "cage,copland", "r1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for h in ("cage", "copland"):
            self.assertEqual(self.w.files(h, b), self.w.files("chopin", b), h)
            self.assertIn(f"{h}: {b} copied from chopin, 4 sums checked there", r.stdout)
        self.assertFalse(os.path.exists(self.w.home("cinqcent")))   # a build host not named: untouched
        # host to host: rsync ran on chopin
        self.assertTrue(any(x.startswith("rsync on=chopin ") and "m@cage.local:" in x for x in self.w.log()))

    def test_dist_through_the_mac_when_hosts_cant_reach_each_other(self):
        b = round_bin(self.w)
        self.w.flag("noh2h")
        r = self.br("--dist-only", "--dist-hosts", "cage", "r1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cage: chopin can't copy to m@cage.local: through the Mac", r.stdout)
        self.assertEqual(self.w.files("cage", b), self.w.files("chopin", b))
        self.assertEqual(os.listdir(self.w.tmp), [])   # the Mac's copy is gone

    def test_dist_all_means_every_enabled_host_that_doesnt_build(self):
        round_bin(self.w)
        r = self.br("--dist-only", "--dry-run", "--dist-hosts", "all", "r1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        named = [x.split()[1].rstrip(":") for x in r.stdout.splitlines() if "would copy" in x]
        self.assertEqual(named, ["copland", "cage"])   # the table's order; czerny reserved; nothing written
        self.assertFalse(os.path.exists(self.w.home("cage")))
        r = self.br("--dist-only", "--dry-run", "--hosts", "chopin", "--dist-hosts", "all", "r1")
        named = [x.split()[1].rstrip(":") for x in r.stdout.splitlines() if "would copy" in x]
        self.assertEqual(named, ["cinqcent", "copland", "cage"])

    def test_a_source_that_doesnt_check_is_copied_nowhere(self):
        b = round_bin(self.w)
        self.w.put("chopin", f"{b}/linux/rustic-bko", "changed after SHA256SUMS\n")
        r = self.br("--dist-only", "--dist-hosts", "cage", "r1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAILED: chopin:" + b + " has no SHA256SUMS that checks: copied to no host", r.stdout)
        self.assertFalse(os.path.exists(self.w.home("cage")))

    def test_a_host_that_is_down_fails_alone(self):
        b = round_bin(self.w)
        self.w.flag("down-copland")
        r = self.br("--dist-only", "--dist-hosts", "copland,cage", "r1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("copland: FAILED: can't reach m@copland.local", r.stdout)
        self.assertEqual(self.w.files("cage", b), self.w.files("chopin", b))

    def test_a_copy_that_differs_there_fails(self):
        b = round_bin(self.w)

        # cage's sha256sum: a disk that returns other bytes
        self.w.put("cage", "bin/sha256sum", '#!/bin/sh\necho "x: FAILED" >&2; exit 1\n', 0o755)
        r = self.br("--dist-only", "--dist-hosts", "cage", "r1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"cage: FAILED: m@cage.local:{b} doesn't check against its SHA256SUMS", r.stdout)

    def test_the_source_is_the_one_build_host(self):
        b = round_bin(self.w, "cinqcent")
        r = self.br("--dist-only", "--hosts", "cinqcent", "--dist-hosts", "cage", "r1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.w.files("cage", b), self.w.files("cinqcent", b))
        self.assertFalse(os.path.exists(self.w.home("chopin")))

    def test_refusals(self):
        def no(args, msg):
            r = self.br(*args)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertIn(msg, r.stderr, args)
        no(["--dist-only", "r1"], "--dist-only needs --dist-hosts")
        no(["--dist-only", "--dist-hosts", "czerny", "r1"], "czerny isn't an enabled host of testvm/hosts.json")
        no(["--dist-only", "--dist-hosts", "bigfoot2", "r1"], "bigfoot2 isn't an enabled host of testvm/hosts.json")
        no(["--dist-only", "--dist-hosts", "bigfoot", "r1"],
           "--dist-hosts: bigfoot is an arm64 host (testvm/hosts.json's arch), and a round's binaries are amd64")
        no(["--dist-only", "--dist-hosts", "chopin", "r1"], "--dist-hosts: chopin builds this round")
        no(["--hosts", "cage", "--dist-only", "--dist-hosts", "copland", "r1"],
           "--hosts: both, chopin, cinqcent or chopin,cinqcent")
        no(["--host", "chopin", "--dist-hosts", "cage", "r1"], "--dist-hosts and --dist-only are the Mac part's")
        self.assertEqual(self.w.log(), [])

    def git_trees(self):
        """Three clean checkouts for the Mac part; restic's build-backotter.sh gives a digest."""
        g = os.path.join(self.w.root, "git")
        for r in ("restic", "rustic_core", "rustic"):
            d = os.path.join(g, r)
            os.makedirs(d)
            with open(os.path.join(d, "README"), "w") as f:
                f.write(r + "\n")
            if r == "restic":
                p = os.path.join(d, "build-backotter.sh")
                with open(p, "w") as f:
                    f.write("#!/bin/sh\necho digest-of-restic\n")
                os.chmod(p, 0o755)
            for cmd in (["init", "-q"], ["add", "-A"],
                        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
                subprocess.run(["git", "-C", d] + cmd, check=True, capture_output=True)
        # The hosts' build-round.sh (BKO_REMOTE_DIR=w): a stand-in that "builds" its part.
        fake = r'''#!/usr/bin/env bash
mode=$2; [[ $1 == --pull ]] && mode=pull
b=$BKO_ROUNDS/round${@: -1}/bin
mkdir -p "$b"
case $mode in
  chopin) mkdir -p "$b/linux" && echo "restic-bko linux" > "$b/linux/restic-bko"
          printf 'chopin\trestic-bko\tlinux\tbuilt\t1\tx\ty\n' > "$b/build-chopin.tsv" ;;
  cinqcent) mkdir -p "$b/freebsd" && echo "rustic-bko freebsd" > "$b/freebsd/rustic-bko"
          printf 'cinqcent\trustic-bko\tfreebsd\tbuilt\t1\tx\ty\n' > "$b/build-cinqcent.tsv" ;;
  pull) ;;
esac
(cd "$b" && find . -type f ! -name 'SHA256SUMS*' ! -name 'build-*' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS)
'''
        for h in ("chopin", "cinqcent"):
            self.w.put(h, "w/stick/build-round.sh", fake, 0o755)
        return {"BKO_GIT": g, "BKO_REMOTE_DIR": "w"}

    def test_a_build_then_dist(self):
        env = self.git_trees()
        r = self.br("--dist-hosts", "cage", "r2", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        b = "backotter-work/roundr2/bin"
        self.assertEqual(self.w.files("cage", b), self.w.files("chopin", b))
        self.assertIn("cage: " + b + " copied from chopin", r.stdout)
        self.assertLess(r.stdout.index("HOST\tBINARY"), r.stdout.index("cage: " + b))   # after the table
        self.assertIn("EXPECT_RESTIC_COMMIT=", r.stdout)

    def test_a_build_without_dist_hosts_touches_no_other_host(self):
        env = self.git_trees()
        r = self.br("r2", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted({x.split()[1] for x in self.w.log() if x.startswith("ssh ")}), ["chopin", "cinqcent"])
        self.assertNotIn("copied from", r.stdout)

    def test_a_failed_build_is_not_distributed(self):
        env = self.git_trees()
        self.w.put("cinqcent", "w/stick/build-round.sh", "#!/bin/sh\nexit 1\n", 0o755)
        r = self.br("--dist-hosts", "cage", "r2", env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("roundr2 not copied to cage (the build failed)", r.stdout)
        self.assertFalse(os.path.exists(self.w.home("cage")))


@unittest.skipUnless(REAL_RSYNC, "no rsync here")
class PromoteBins(unittest.TestCase):
    """testvm/promote-bins.sh: bko-bin, cmp-bin and holt-bin from a build host, checked by a
    manifest of sha256s on the source (its own SHA256SUMS checked first)."""

    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)
        w = "backotter-work"
        self.w.put("chopin", f"{w}/bko-bin/linux/restic-bko", "restic linux\n", 0o755)
        self.w.put("chopin", f"{w}/bko-bin/freebsd/rustic-bko", "rustic freebsd\n", 0o755)
        self.w.put("chopin", f"{w}/bko-bin/BUILD-INFO", "assembled on chopin\n")
        self.w.put("chopin", f"{w}/bko-bin/SHA256SUMS", self.w.sums("chopin", f"{w}/bko-bin"))
        self.w.put("chopin", f"{w}/cmp-bin/restic-bko", "cmp restic\n", 0o755)
        self.w.put("chopin", f"{w}/cmp-bin/restic-bko.digest", "d1\n")
        self.w.put("chopin", f"{w}/holt-bin/linux/bko-holt", "holt linux\n", 0o755)
        self.w.put("chopin", f"{w}/holt-bin/linux/COMMIT", "c" * 40 + "\n")
        os.makedirs(os.path.join(self.w.home("chopin"), w, "images"))
        os.makedirs(os.path.join(self.w.home("cage"), w))

    def pb(self, *args):
        return self.w.run([PROMOTE] + list(args))

    def test_dry_run_writes_nothing(self):
        r = self.pb("--dry-run", "cage")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cage bko-bin: would copy 4 of 4 files", r.stdout)
        self.assertIn("cage cmp-bin: would copy 2 of 2 files", r.stdout)
        self.assertIn("cage holt-bin: would copy 2 of 2 files", r.stdout)
        self.assertEqual(os.listdir(os.path.join(self.w.home("cage"), "backotter-work")), [])

    def test_promote_then_up_to_date(self):
        r = self.pb("--sets", "bko-bin,cmp-bin,holt-bin", "cage")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for s in ("bko-bin", "cmp-bin", "holt-bin"):
            self.assertEqual(self.w.files("cage", "backotter-work/" + s), self.w.files("chopin", "backotter-work/" + s))
        self.assertIn("cage bko-bin: 4 of 4 files copied from chopin, all 4 checked by sha256", r.stdout)
        self.assertEqual([n for n in os.listdir(os.path.join(self.w.home("cage"), "backotter-work"))
                          if n.startswith(".promote-")], [])
        # the SHA256SUMS (and BUILD-INFO) are the source's: the same on every host
        self.assertEqual(self.w.read("cage", "backotter-work/bko-bin/SHA256SUMS"),
                         self.w.read("chopin", "backotter-work/bko-bin/SHA256SUMS"))
        r = self.pb("cage")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cage bko-bin: up to date (4 files)", r.stdout)
        self.assertNotIn("copied", r.stdout)

    def test_extra_files_stay_unless_delete(self):
        self.pb("--sets", "cmp-bin", "cage")
        self.w.put("cage", "backotter-work/cmp-bin/old-bko", "stale\n")
        r = self.pb("--sets", "cmp-bin", "cage")
        self.assertIn("cage cmp-bin: up to date (2 files; 1 there that chopin hasn't (./old-bko), kept)", r.stdout)
        r = self.pb("--delete", "--sets", "cmp-bin", "cage")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNone(self.w.read("cage", "backotter-work/cmp-bin/old-bko"))

    def test_a_source_whose_sums_dont_check_goes_nowhere(self):
        self.w.put("chopin", "backotter-work/bko-bin/linux/restic-bko", "changed\n")
        r = self.pb("--sets", "bko-bin,cmp-bin", "cage")
        self.assertEqual(r.returncode, 1)
        self.assertIn("bko-bin: FAILED: a SHA256SUMS in chopin:backotter-work/bko-bin doesn't check", r.stdout)
        self.assertIsNone(self.w.read("cage", "backotter-work/bko-bin/linux/restic-bko"))
        self.assertIsNotNone(self.w.read("cage", "backotter-work/cmp-bin/restic-bko"))   # the others still go

    def test_a_held_image_lock_refuses_that_host(self):
        os.makedirs(os.path.join(self.w.home("cage"), "backotter-work", "images"))
        self.w.flag("lock-held")
        r = self.pb("--sets", "holt-bin", "cage")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("images/.lock-linux is held", r.stderr)
        self.assertIsNone(self.w.read("cage", "backotter-work/holt-bin/linux/bko-holt"))
        self.assertEqual([n for n in os.listdir(os.path.join(self.w.home("cage"), "backotter-work"))
                          if n.startswith(".promote-")], [])

    def test_through_the_mac(self):
        self.w.flag("noh2h")
        os.makedirs(os.path.join(self.w.home("copland"), "backotter-work"))
        r = self.pb("--sets", "holt-bin", "cage", "copland")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cage holt-bin: chopin can't copy to m@cage.local: through the Mac", r.stdout)
        for h in ("cage", "copland"):
            self.assertEqual(self.w.files(h, "backotter-work/holt-bin"), self.w.files("chopin", "backotter-work/holt-bin"))
        pulls = [x for x in self.w.log() if x.startswith("rsync on=mac ") and "m@chopin.local:" in x]
        self.assertEqual(len(pulls), 1)   # one pull from the source for both hosts

    def test_all_and_refusals(self):
        r = self.pb("--dry-run", "all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        hosts = sorted({x.split()[1] for x in r.stdout.splitlines() if " bko-bin: " in x})
        self.assertEqual(hosts, ["cage", "cinqcent", "copland"])   # not bigfoot: an arm64 host
        for args, msg in ((["--from", "cage", "chopin"], "--from cage: not a build host"),
                          (["bigfoot"], "bigfoot is an arm64 host (testvm/hosts.json's arch): the sets are amd64"),
                          (["chopin"], "chopin is the source"),
                          (["czerny"], "no such enabled host czerny"),
                          (["--sets", "images", "cage"], "--sets: of bko-bin cmp-bin holt-bin"),
                          ([], "Usage: promote-bins.sh")):
            r = self.pb(*args)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertIn(msg, r.stderr, args)


# --- stic.py: round_step -------------------------------------------------------------------------

class SticRoundStep(unittest.TestCase):
    """A pending round is built on the table's build hosts whatever --hosts names, then its bin
    copied to the --hosts hosts that build nothing; a built round is left as it is (run.py
    refuses one that a --hosts host lacks)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-dist-stic-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ctx = test_stic.RunCtx(self.tmp)
        self.copy = self.ctx.options["run"]["copy"]["path"]

    def step(self, hosts, st, copies=None, rc=0):
        self.ctx.hosts = list(hosts)
        self.ctx.options["run"]["hosts"] = list(hosts)
        plan = test_stic.Plan([test_stic.PC("stic:debian12", "due", "changed:bin", key="1" * 64)])
        plan.set = st
        after = test_stic.Plan([test_stic.PC("stic:debian12", "due", "changed:bin", key="2" * 64)])
        after.set = dict(st, pending=None, missing_on=[])
        env = test_stic.FakeEnv({})
        self.built, self.copied, self.probed = [], [], []
        env.run = lambda argv, env=None, timeout=None: (self.built.append((argv, env)),
                                                        type("R", (), {"returncode": rc})())[1]
        env.refresh_facts = lambda ctx, sets: (self.probed.append((list(ctx.hosts), sets)), {})[1]
        env.replan = lambda word, ctx: after
        if copies is not False:
            env.make_copy = lambda checkout, hs: (self.copied.append(list(hs)),
                                                  {h: (copies or {}).get(h, self.copy) for h in hs})[1]
        r = stic.SticRun(self.ctx, plan, env)
        r.setup()
        r.round_step()
        return r, env

    PENDING = {"name": "roundt2r-x", "round": "t2r-x", "dir": "~/backotter-work/roundt2r-x/bin", "pending": "restic=x"}

    def argv(self):
        return [a for a in self.built[0][0][2:]]

    def test_default_hosts_build_as_ever(self):
        self.step(["chopin", "cinqcent"], dict(self.PENDING))
        self.assertEqual(self.argv(), ["t2r-x"])   # build-round.sh ROUND: no --hosts, no --dist-hosts
        self.assertEqual(self.copied, [])
        e = self.built[0][1]
        self.assertEqual((e["BKO_PROFILE"], e["BKO_GUEST_PROFILE"], e["BKO_REMOTE_DIR"]),
                         ("release", "release", self.copy))
        self.assertEqual(self.probed, [(["chopin", "cinqcent"], ["roundt2r-x"])])

    def test_small_hosts_get_the_round_copied(self):
        self.step(["chopin", "cinqcent", "copland", "cage"], dict(self.PENDING))
        self.assertEqual(self.argv(), ["--dist-hosts", "copland,cage", "t2r-x"])
        self.assertEqual(self.copied, [])
        self.assertEqual(self.probed[0][0], ["chopin", "cinqcent", "copland", "cage"])   # facts of every --hosts host

    def test_hosts_without_build_hosts_still_build_there(self):
        _, env = self.step(["cage"], dict(self.PENDING))
        self.assertEqual(self.copied, [["chopin", "cinqcent"]])   # the copy on both build hosts first
        self.assertEqual(self.argv(), ["--dist-hosts", "cage", "t2r-x"])
        self.assertTrue(any("building round t2r-x on chopin and cinqcent" in m and "then its bin to cage" in m
                            for m in env.logs), env.logs)

    def test_one_build_host_named(self):
        self.step(["chopin", "copland"], dict(self.PENDING))
        self.assertEqual(self.copied, [["cinqcent"]])
        self.assertEqual(self.argv(), ["--dist-hosts", "copland", "t2r-x"])

    def test_a_copy_elsewhere_refuses(self):
        with self.assertRaisesRegex(stic.SticError, "the copy on cinqcent is at /elsewhere, not "):
            self.step(["chopin"], dict(self.PENDING), copies={"cinqcent": "/elsewhere"})
        self.assertEqual(self.built, [])
        with self.assertRaisesRegex(stic.SticError, "no private copy on cinqcent"):
            self.step(["chopin"], dict(self.PENDING), copies=False)

    def test_a_failed_build_refuses(self):
        with self.assertRaisesRegex(stic.SticError, r"build-round.sh t2r-x failed \(exit 1\)"):
            self.step(["chopin", "cinqcent", "cage"], dict(self.PENDING), rc=1)

    def test_a_built_round_is_left_as_it_is(self):
        """round_step builds a pending round only: a built one that a --hosts host lacks is
        run.py's refusal (below), copied by hand with build-round.sh --dist-only."""
        for hosts, miss in ((["chopin", "cinqcent", "copland", "cage"], ["cage"]),
                            (["chopin", "copland"], ["chopin", "copland"]),
                            (["chopin", "cinqcent"], ["cinqcent"]),
                            (["chopin", "cinqcent"], [])):
            st = {"name": "roundrel6", "round": "rel6", "dir": "~/backotter-work/roundrel6/bin",
                  "pending": None, "missing_on": miss}
            self.step(hosts, st)
            self.assertEqual((self.built, self.copied, self.probed), ([], [], []), (hosts, miss))

    def test_run_refuses_a_round_missing_on_a_small_host(self):
        """run.py refuses a plan whose ROUND some --hosts host lacks, before any runner: no
        run id, no host work (copy it there with build-round.sh --dist-only first)."""
        import types
        from t2lib import run
        mac = os.path.join(self.tmp, "mac")
        good = {"preflight": {"ram": {"mounted": True}, "kvm": True, "scratch_free_gb": 500}}
        self.ctx.hosts = ["chopin", "cage"]
        self.ctx.facts = {h: dict(good) for h in self.ctx.hosts}
        self.assertEqual(run.preflight(self.ctx.facts, self.ctx.hosts), [])   # (this test is about the ROUND)
        plan = test_stic.Plan([test_stic.PC("stic:debian12", "due", "changed:bin", key="1" * 64)])
        plan.set = {"name": "roundrel6", "round": "rel6", "dir": "~/backotter-work/roundrel6/bin",
                    "pending": None, "missing_on": ["cage"]}
        word = types.SimpleNamespace(family="stic", word="SticTest2", form="bare", round="rel6")
        shared = types.SimpleNamespace(plan=plan, stamp=None, suffix=None)
        out = []
        code = run.lifecycle(word, self.ctx, mac_root=mac, out=out.append, shared=shared)
        self.assertEqual(code, run.EXIT_REFUSED, out)
        self.assertTrue(any("isn't on cage: a ROUND must be on every host in use" in m for m in out), out)
        self.assertFalse(os.path.isdir(os.path.join(mac, "runs")) and os.listdir(os.path.join(mac, "runs")), out)


# --- holt.py: ensure_holt ------------------------------------------------------------------------

class Remote4(test_holt.FakeRemote):
    """FakeRemote with copland and cage too."""

    def __init__(self, outcomes):
        super().__init__(outcomes)
        for h in ("copland", "cage"):
            self.files[h], self.ledger[h] = {}, []


class HoltBuildHosts(unittest.TestCase):
    """bko-holt builds go to the table's build hosts only; the builds go to every --hosts host,
    and (holt_ship known) to every known host."""
    PC = test_holt.U53HoltRun.PC
    go = test_holt.U53HoltRun.go

    def setUp(self):
        """The fake remote expects MAXJOBS 3 on every host: a copy of the table without the
        small class's limits (holt_jobs 1 there since 2026-11-13)."""
        from unittest import mock
        with open(H.path()) as f:
            t = json.load(f)
        t["class_limits"] = {"big": {}, "small": {}}
        d = tempfile.mkdtemp(prefix="t2-rd-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "hosts.json")
        with open(p, "w") as f:
            json.dump(t, f)
        patcher = mock.patch.dict(os.environ, {H.ENV: p})
        patcher.start()
        self.addCleanup(patcher.stop)
        test_holt.U53HoltRun.setUp(self)
    gate = test_holt.U53HoltRun.gate
    HB = "/bkoscratch/t2test/holt-bin/" + "c" * 64

    def hosts(self, hs, copies=None):
        self.rs.hosts = list(hs)
        self.rs.copy = {h: {"id": "6fff54b", "path": "/home/m/backotter-work/t2/copy/6fff54b", "commit": "6fff54b"}
                        for h in (copies or hs)}
        for pc in self.cells:
            pc.keys = {h: "1" * 64 for h in hs}

    def builds(self, r):
        import re
        return [(h, re.search(r"--reserved \S+ (\S+)", c[0]).group(1))
                for h, op, *c in r.calls if op == "start" and "--reserved" in c[0]]

    def test_a_small_host_in_hosts_builds_nothing(self):
        self.hosts(["copland", "chopin"])
        r = Remote4({})
        res = self.go({}, r)
        self.assertEqual(res["exit"], 0, res)
        self.assertEqual({h for h, _ in self.builds(r)}, {"chopin"})
        for p in ("bko-holt", "freebsd/bko-holt", "openbsd/bko-holt"):
            self.assertEqual(r.files["copland"][f"{self.HB}/{p}"], r.files["chopin"][f"{self.HB}/{p}"])
        self.assertEqual(r.files["copland"][self.HB + "/SHA256SUMS"], r.files["chopin"][self.HB + "/SHA256SUMS"])

    def test_the_default_hosts_build_as_ever(self):
        r = Remote4({})
        res = self.go({}, r)
        self.assertEqual(res["exit"], 0, res)
        self.assertEqual(self.builds(r), [("chopin", "linux"), ("cinqcent", "freebsd"), ("chopin", "openbsd")])
        self.assertEqual(r.files["copland"], {})   # nothing shipped to the small hosts by default
        self.assertEqual(r.files["cage"], {})

    def test_holt_ship_known_ships_to_every_known_host(self):
        self.ctx.options["holt_ship"] = "known"
        r = Remote4({})
        res = self.go({}, r)
        self.assertEqual(res["exit"], 0, res)
        for h in ("copland", "cage"):
            self.assertEqual(r.files[h][self.HB + "/SHA256SUMS"], r.files["chopin"][self.HB + "/SHA256SUMS"], h)
            self.assertIn(self.HB + "/openbsd/bko-holt", r.files[h])
        self.assertEqual({h for h, _ in self.builds(r)}, {"chopin", "cinqcent"})

    def test_no_build_host_refuses_the_build(self):
        self.hosts(["cage"])
        r = Remote4({})
        res = self.go({}, r)
        self.assertEqual(res["exit"], 1, res)
        self.assertEqual(self.builds(r), [])
        self.assertTrue(any("no build host (role build in testvm/hosts.json) in --hosts cage" in s
                            for s in res["stops"]), res["stops"])

    def test_a_build_host_holding_the_copy_builds_for_small_hosts(self):
        self.cells[:] = [pc for pc in self.cells if pc.cell in ("holt:debian13", "holt:freebsd15", "holt:mint22")]
        self.hosts(["cage"], copies=["cage", "cinqcent"])
        r = Remote4({})
        res = self.go({}, r)
        self.assertEqual(res["exit"], 0, res)
        self.assertEqual({h for h, _ in self.builds(r)}, {"cinqcent"})
        self.assertEqual(r.files["cage"][self.HB + "/SHA256SUMS"], r.files["cinqcent"][self.HB + "/SHA256SUMS"])

    def test_a_platform_a_host_has_needs_no_build_host(self):
        import hashlib as hl
        old = "bko-holt linux, an earlier run's"
        info = {"platform": "linux", "sha256": hl.sha256(old.encode()).hexdigest(), "host": "chopin"}
        r = Remote4({})
        r.files["chopin"][self.HB + "/bko-holt"] = old
        r.files["chopin"][self.HB + "/build-linux.json"] = json.dumps(info, sort_keys=True) + "\n"
        self.hosts(["cage"])
        self.cells[:] = [pc for pc in self.cells if pc.cell in ("holt:debian13", "holt:mint22")]
        for pc in self.cells:
            pc.keys = {"cage": "1" * 64}
        res = self.go({}, r)
        self.assertEqual(res["exit"], 0, res)
        self.assertEqual(self.builds(r), [])
        self.assertEqual(r.files["cage"][self.HB + "/bko-holt"], old)


if __name__ == "__main__":
    unittest.main()
