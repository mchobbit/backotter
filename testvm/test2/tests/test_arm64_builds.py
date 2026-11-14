"""ARM64 A2 (docs/design/arm64.md): the archivers and bko-holt built for linux-arm64, laid out
beside the amd64 ones.  hosts.json's rustup_home and cargo_home (hosts.py rust_env, hosts.sh
hosts_rust_env); stick/zig-tools.sh's pins and stick/zigbuild/cargo; stick/build-round.sh
--arch arm64 (the arm64 host's part in its job_slice, its builds brought to the build hosts,
the check builds compared, bin/linux-arm64/ sent back to it alone), its --sums and --pull with
an arm64 directory's own SHA256SUMS, --dist-hosts to an arm64 host; promote-bins.sh to an
arm64 host (its sets' arm64 directories only); build-holt.sh's linux-arm64.

The shell scripts run against test_round_dist's fake hosts (ssh runs the command here, in a
directory that is the host's home).  No host needed."""

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

from t2lib import hosts as H  # noqa: E402
import test_round_dist as RD  # noqa: E402

HOSTS_PY = os.path.join(T2, "t2lib", "hosts.py")
HOSTS_SH = os.path.join(TESTVM, "hosts.sh")
ZIG_TOOLS = os.path.join(REPO, "stick", "zig-tools.sh")
SHIM = os.path.join(REPO, "stick", "zigbuild", "cargo")
BUILD_HOLT = os.path.join(TESTVM, "holttest1d", "build-holt.sh")
BUILD_ROUND = RD.BUILD_ROUND
PROMOTE = RD.PROMOTE
W = "backotter-work"


def table():
    with open(os.path.join(TESTVM, "hosts.json"), encoding="utf-8") as f:
        return json.load(f)


def clean_env(**extra):
    e = dict(os.environ)
    for k in (H.ENV, "RUSTUP_HOME", "CARGO_HOME", "BKO_TOOLS"):
        e.pop(k, None)
    e.update(extra)
    return e


def sha(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


# --- the table: the toolchain homes ---------------------------------------------------------

class RustEnv(unittest.TestCase):
    def test_bigfoot_names_its_staging_toolchains(self):
        self.assertEqual(H.rust_env("bigfoot"), {"RUSTUP_HOME": "/home/m/bko-arm64/toolchains/rustup",
                                                 "CARGO_HOME": "/home/m/bko-arm64/toolchains/cargo"})
        self.assertEqual(H.rust_env("bigfoot.local"), H.rust_env("bigfoot"))
        for h in ("chopin", "cinqcent", "cage", "czerny", "nosuch"):
            self.assertEqual(H.rust_env(h), {}, h)

    def test_bad_homes(self):
        def errs(**f):
            t = table()
            next(r for r in t["hosts"] if r["name"] == "cage").update(f)
            return " | ".join(H.check_table(t))
        for v in ("relative/path", "/with space", "/a/../b", "/..", 3, ""):
            self.assertIn("an absolute path (no spaces, no ..) or null", errs(rustup_home=v), v)
        self.assertIn("cargo_home 'x'", errs(cargo_home="x"))
        self.assertEqual(errs(rustup_home="/opt/rustup", cargo_home=None), "")

    def test_command_line_and_shell(self):
        r = subprocess.run([sys.executable, "-I", "-B", HOSTS_PY, "rust-env", "bigfoot"], capture_output=True,
                           text=True, env=clean_env())
        self.assertEqual((r.returncode, r.stdout), (0, "RUSTUP_HOME=/home/m/bko-arm64/toolchains/rustup\n"
                                                       "CARGO_HOME=/home/m/bko-arm64/toolchains/cargo\n"))
        r = subprocess.run([sys.executable, "-I", "-B", HOSTS_PY, "rust-env", "chopin"], capture_output=True,
                           text=True, env=clean_env())
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        sh = ('. "$1"; hosts_rust_env bigfoot; echo "$RUSTUP_HOME|$CARGO_HOME"; '
              'unset RUSTUP_HOME; CARGO_HOME=/mine; hosts_rust_env bigfoot; echo "$RUSTUP_HOME|$CARGO_HOME"; '
              'unset RUSTUP_HOME CARGO_HOME; hosts_rust_env chopin; echo "${RUSTUP_HOME:-none}|${CARGO_HOME:-none}"; '
              'hosts_rust_env nosuch; echo $?')
        r = subprocess.run(["bash", "-c", sh, "x", HOSTS_SH], capture_output=True, text=True, env=clean_env())
        self.assertEqual(r.stdout, "/home/m/bko-arm64/toolchains/rustup|/home/m/bko-arm64/toolchains/cargo\n"
                                   "/home/m/bko-arm64/toolchains/rustup|/mine\n"   # a caller's own one stays
                                   "none|none\n0\n", r.stderr)


# --- zig-tools.sh, zigbuild/cargo -----------------------------------------------------------

class ZigTools(unittest.TestCase):
    def setUp(self):
        self.tools = tempfile.mkdtemp(prefix="t2-zig-")
        self.addCleanup(shutil.rmtree, self.tools, True)

    def run_zt(self, *args):
        return subprocess.run(["bash", ZIG_TOOLS] + list(args), capture_output=True, text=True,
                              env=clean_env(BKO_TOOLS=self.tools))

    def fake(self, rel, out):
        p = os.path.join(self.tools, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(f"#!/bin/sh\necho '{out}'\n")
        os.chmod(p, 0o755)

    def test_pins_and_paths(self):
        r = self.run_zt("--versions")
        self.assertEqual((r.returncode, r.stdout), (0, "zig 0.15.2 cargo-zigbuild 0.24.1\n"))
        r = self.run_zt("--path")
        self.assertEqual(r.stdout, f"{self.tools}/zig-0.15.2:{self.tools}/cargo-zigbuild-0.24.1/bin\n")
        with open(ZIG_TOOLS) as f:
            text = f.read()
        for m in ("x86_64", "aarch64"):   # a sha256 pinned for each machine the builds run on
            self.assertRegex(text, rf"\n\s+{m}\) echo [0-9a-f]{{64}} ;;")

    def test_check(self):
        r = self.run_zt("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"no zig 0.15.2 in {self.tools}/zig-0.15.2: ", r.stdout)
        self.assertIn("--install", r.stdout)
        self.fake("zig-0.15.2/zig", "0.15.2")
        r = self.run_zt("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no cargo-zigbuild 0.24.1", r.stdout)
        self.fake("cargo-zigbuild-0.24.1/bin/cargo-zigbuild", "cargo-zigbuild 0.24.0")   # another release
        self.assertEqual(self.run_zt("--check").returncode, 1)
        self.fake("cargo-zigbuild-0.24.1/bin/cargo-zigbuild", "cargo-zigbuild 0.24.1")
        self.assertEqual(self.run_zt("--check").returncode, 0)
        self.fake("zig-0.15.2/zig", "0.16.0")
        self.assertEqual(self.run_zt("--check").returncode, 1)
        self.assertEqual(self.run_zt().returncode, 2)

    def test_the_cargo_shim(self):
        log = os.path.join(self.tools, "log")
        real = os.path.join(self.tools, "real-cargo")
        with open(real, "w") as f:
            f.write(f'#!/bin/sh\necho "$*" >> {log}\n')
        os.chmod(real, 0o755)
        for args in (["build", "--release", "--locked", "--target", "aarch64-unknown-linux-musl"], ["metadata"], []):
            r = subprocess.run([SHIM] + args, capture_output=True, text=True, env=clean_env(BKO_CARGO=real))
            self.assertEqual(r.returncode, 0, r.stderr)
        with open(log) as f:
            self.assertEqual(f.read().splitlines(),
                             ["zigbuild --release --locked --target aarch64-unknown-linux-musl", "metadata", ""])
        r = subprocess.run([SHIM, "build"], capture_output=True, text=True, env=clean_env())
        self.assertEqual(r.returncode, 2)
        self.assertIn("BKO_CARGO names no cargo", r.stderr)


# --- build-round.sh: a host's SHA256SUMS ----------------------------------------------------

@unittest.skipUnless(RD.REAL_RSYNC, "no rsync here")
class RoundSums(unittest.TestCase):
    """--sums (the host part, run on the collector once the Mac has gathered every builder's
    files): the round's SHA256SUMS lists every file, an arm64 directory's too, and each arm64
    directory has its own."""

    def setUp(self):
        self.w = RD.World()
        self.addCleanup(self.w.close)
        self.b = f"{W}/roundr1/bin"

    def host_part(self, host, *args, env=None):
        e = {"HOME": self.w.home(host), "FAKE_ON": host}
        e.update(env or {})
        return self.w.run([BUILD_ROUND] + list(args), env=e)

    def put_round(self, host):
        b = self.b
        self.w.put(host, f"{b}/linux/restic-bko", "restic amd64\n", 0o755)
        self.w.put(host, f"{b}/linux-arm64/restic-bko", "restic arm64\n", 0o755)
        self.w.put(host, f"{b}/linux-arm64/restic-commit", "abc\n")
        self.w.put(host, f"{b}/linux-arm64/rustic-bko", "rustic arm64\n", 0o755)
        self.w.put(host, f"{b}/build-chopin.tsv", "x\n")

    def test_sums(self):
        self.put_round("chopin")
        r = self.host_part("chopin", "--sums", "r1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("and linux-arm64/SHA256SUMS", r.stdout)
        top = self.w.read("chopin", f"{self.b}/SHA256SUMS")
        self.assertEqual([l.split("  ")[1] for l in top.splitlines()],
                         ["./linux-arm64/restic-bko", "./linux-arm64/restic-commit", "./linux-arm64/rustic-bko",
                          "./linux/restic-bko"])
        arm = self.w.read("chopin", f"{self.b}/linux-arm64/SHA256SUMS")
        self.assertEqual(arm, f"{sha('restic arm64' + chr(10))}  ./restic-bko\n{sha('abc' + chr(10))}  ./restic-commit\n"
                              f"{sha('rustic arm64' + chr(10))}  ./rustic-bko\n")
        for d in (self.b, self.b + "/linux-arm64"):   # both check where they are
            c = subprocess.run(["sha256sum", "-c", "--quiet", "--strict", "SHA256SUMS"], capture_output=True,
                               cwd=os.path.join(self.w.home("chopin"), d), env=dict(os.environ, PATH=self.w.bin + os.pathsep + os.environ["PATH"]))
            self.assertEqual(c.returncode, 0, d)

    def test_the_gathered_files_are_summed_once(self):
        """What the Mac gathers from the other builders is in the collector's SHA256SUMS: --sums
        reads the directory, whoever built what is in it."""
        self.put_round("chopin")
        self.w.put("chopin", f"{self.b}/freebsd/rustic-bko", "rustic freebsd\n", 0o755)
        self.w.put("chopin", f"{self.b}/build-cinqcent.files", "freebsd/rustic-bko\n")
        self.w.put("chopin", f"{self.b}/build-cinqcent.tsv", "y\n")
        self.w.put("chopin", f"{self.b}/build-hosts", "chopin\ncinqcent\n")
        r = self.host_part("chopin", "--sums", "r1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        top = self.w.read("chopin", f"{self.b}/SHA256SUMS")
        self.assertIn("./freebsd/rustic-bko", top)
        self.assertIn("./linux-arm64/rustic-bko", top)
        for n in ("build-hosts", "build-cinqcent.files", "build-chopin.tsv"):   # build-* are never summed
            self.assertNotIn(n, top)
        self.assertIsNotNone(self.w.read("chopin", f"{self.b}/linux-arm64/SHA256SUMS"))

    def test_an_unknown_host_part_is_refused(self):
        os.makedirs(os.path.join(self.w.home("chopin"), W))
        r = self.host_part("chopin", "--host", "nohost", "--units", "linux", "r1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--host: a host with the build or build-arm64 role in testvm/hosts.json, not nohost",
                      r.stderr)
        r = self.host_part("chopin", "--host", "chopin", "r1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--host chopin: --units LIST says what to build here", r.stderr)
        r = self.host_part("chopin", "--host", "bigfoot", "--units", "linux-arm64", "--arch", "arm64", "r1")
        self.assertEqual(r.returncode, 2)   # this Mac isn't an aarch64 Linux
        self.assertIn("--host bigfoot: this is a", r.stderr)
        r = self.host_part("chopin", "--arch", "sparc", "--sums", "r1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--arch: amd64, arm64, amd64,arm64 or all, not sparc", r.stderr)


# --- build-round.sh: the Mac part with --arch arm64 -----------------------------------------

FAKE_PART = r'''#!/usr/bin/env bash
# A stand-in for a host's build-round.sh: "builds" its arm64 part (and, with amd64, linux's).
a=amd64 mode=""
args=("$@")
while [[ $# -gt 1 ]]; do
    case $1 in --host) mode=$2; shift ;; --sums) mode=sums ;; --arch) a=$2; shift ;; --units) shift ;; esac
    shift
done
echo "$FAKE_ON ${args[*]} sliced=${FAKE_SLICED:-no}" >> "$FAKE_ROOT/parts"
b=$BKO_ROUNDS/round$1/bin
mkdir -p "$b"
sums() {
    (cd "$b" && find . -type f ! -name 'SHA256SUMS*' ! -name 'build-*' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS)
    for d in "$b"/*-arm64; do
        [[ -d $d ]] && (cd "$d" && find . -type f ! -name 'SHA256SUMS*' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS)
    done
    true
}
[[ " ${args[*]} " == *" --dry-run "* ]] && exit 0
case $mode in
  chopin)
    : > "$b/build-chopin.tsv"; : > "$b/build-chopin.files"
    if [[ ,$a, == *,amd64,* ]]; then
        mkdir -p "$b/linux" && echo "restic-bko amd64" > "$b/linux/restic-bko"
        printf 'chopin\trestic-bko\tlinux\tbuilt\t1\t1111\td0\n' >> "$b/build-chopin.tsv"
        echo linux/restic-bko >> "$b/build-chopin.files"
    fi
    mkdir -p "$b/linux-arm64" && echo "restic-bko arm64" > "$b/linux-arm64/restic-bko"
    printf 'chopin\trestic-bko\tlinux-arm64\tbuilt\t1\taaaa\td1\nchopin\trustic-bko\tlinux-arm64\tcheck-built\t9\t%s\td2\n' \
        "${FAKE_RUSTIC_CHECK:-bbbb}" >> "$b/build-chopin.tsv"
    echo linux-arm64/restic-bko >> "$b/build-chopin.files"
    sums
    ;;
  cinqcent)
    printf 'cinqcent\trestic-bko\tlinux-arm64\tcheck-built\t1\t%s\td1\n' "${FAKE_RESTIC_CHECK:-aaaa}" > "$b/build-cinqcent.tsv"
    : > "$b/build-cinqcent.files"; sums ;;
  bigfoot)
    mkdir -p "$b/linux-arm64" && echo "rustic-bko arm64" > "$b/linux-arm64/rustic-bko" && echo c > "$b/linux-arm64/rustic-commit"
    printf 'bigfoot\trustic-bko\tlinux-arm64\tbuilt\t9\tbbbb\td3\n' > "$b/build-bigfoot.tsv"
    printf 'linux-arm64/rustic-bko\nlinux-arm64/rustic-commit\n' > "$b/build-bigfoot.files" ;;
  sums) sums ;;
esac
'''

# sudo -n systemd-run --scope --quiet --slice=S -- setpriv ... -- CMD: CMD, marked as in the slice.
FAKE_SUDO = r'''#!/bin/sh
while [ "$1" != "--" ]; do shift; done; shift
while [ "$1" != "--" ]; do shift; done; shift
FAKE_SLICED=yes exec "$@"
'''


@unittest.skipUnless(RD.REAL_RSYNC, "no rsync here")
class BuildRoundArm64(unittest.TestCase):
    def setUp(self):
        self.w = RD.World()
        self.addCleanup(self.w.close)
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
        for h in ("chopin", "cinqcent", "bigfoot"):
            self.w.put(h, "w/stick/build-round.sh", FAKE_PART, 0o755)
        self.w.put("bigfoot", "bin/sudo", FAKE_SUDO, 0o755)
        self.env = {"BKO_GIT": g, "BKO_REMOTE_DIR": "w"}
        self.b = f"{W}/roundr2/bin"

    def br(self, *args, env=None):
        """The Mac part with --hosts both --split legacy: the fixed two-way split, which is
        what FAKE_PART stands in for (the dealing over the build hosts, which is what any
        --hosts does without --split legacy, is tests/test_round_deal.py's)."""
        return self.w.run([BUILD_ROUND, "--hosts", "both", "--split", "legacy"] + list(args),
                          dict(self.env, **(env or {})))

    def parts(self):
        try:
            with open(os.path.join(self.w.root, "parts")) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def test_arm64_round(self):
        r = self.br("--arch", "arm64", "r2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = self.parts()
        self.assertIn("bigfoot --host bigfoot --units linux-arm64 --arch arm64 r2 sliced=yes", p)   # in its job_slice
        self.assertIn("chopin --host chopin --units restic-arm64,linux-arm64-check --arch arm64 r2 sliced=no", p)
        self.assertIn("cinqcent --host cinqcent --units restic-arm64-check --arch arm64 r2 sliced=no", p)
        self.assertIn("chopin --sums r2 sliced=no", p)
        # The arm64 host's builds are the build hosts' too; the sums are the same everywhere.
        for h in ("chopin", "cinqcent"):
            self.assertEqual(self.w.read(h, f"{self.b}/linux-arm64/rustic-bko"), "rustic-bko arm64\n", h)
            self.assertIsNotNone(self.w.read(h, f"{self.b}/build-bigfoot.tsv"), h)
        self.assertEqual(self.w.read("cinqcent", f"{self.b}/SHA256SUMS"), self.w.read("chopin", f"{self.b}/SHA256SUMS"))
        self.assertIn("./linux-arm64/rustic-bko", self.w.read("chopin", f"{self.b}/SHA256SUMS"))
        arm = self.w.read("chopin", f"{self.b}/linux-arm64/SHA256SUMS")
        self.assertEqual([l.split("  ")[1] for l in arm.splitlines()], ["./restic-bko", "./rustic-bko", "./rustic-commit"])
        for h in ("cinqcent", "bigfoot"):
            self.assertEqual(self.w.read(h, f"{self.b}/linux-arm64/SHA256SUMS"), arm, h)
        # bigfoot: bin/linux-arm64/ whole, and nothing else of the round's
        self.assertEqual(self.w.read("bigfoot", f"{self.b}/linux-arm64/restic-bko"), "restic-bko arm64\n")
        self.assertEqual(sorted(os.listdir(os.path.join(self.w.home("bigfoot"), self.b))),
                         ["build-bigfoot.files", "build-bigfoot.tsv", "linux-arm64"])
        self.assertIn("bigfoot: " + self.b + "/{linux-arm64} copied from chopin (its arm64 binaries only), 3 sums checked",
                      r.stdout)
        # the table has the three hosts' rows; the cross-checks match
        self.assertIn("bigfoot\trustic-bko\tlinux-arm64\tbuilt", r.stdout)
        self.assertIn("restic-bko linux-arm64: the same: chopin's aaaa, cinqcent's check aaaa", r.stdout)
        self.assertIn("rustic-bko linux-arm64: the same: bigfoot's bbbb, chopin's check bbbb", r.stdout)
        self.assertNotIn("not cross-checked", r.stdout)

    def test_both_archs_and_bigfoot_never_gets_amd64(self):
        r = self.br("--arch", "all", "--dist-hosts", "cage", "r2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("chopin --host chopin --units restic,linux,dragonfly,haiku,restic-arm64,linux-arm64-check "
                      "--arch amd64,arm64 r2 sliced=no", self.parts())
        self.assertEqual(self.w.read("chopin", f"{self.b}/linux/restic-bko"), "restic-bko amd64\n")
        self.assertFalse(os.path.exists(os.path.join(self.w.home("bigfoot"), self.b, "linux")))
        self.assertEqual(self.w.files("cage", self.b), self.w.files("chopin", self.b))   # an amd64 host: whole

    def test_a_check_that_differs_fails_the_round(self):
        r = self.br("--arch", "arm64", "--dist-hosts", "cage", "r2", env={"FAKE_RESTIC_CHECK": "cccc"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("restic-bko linux-arm64: DIFFERS: chopin's aaaa, cinqcent's check cccc", r.stdout)
        self.assertIn("roundr2's arm64 directories not copied back to bigfoot (the build failed)", r.stdout)
        self.assertIn("roundr2 not copied to cinqcent cage (the build failed)", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.w.home("bigfoot"), self.b, "linux-arm64", "restic-bko")))
        self.assertFalse(os.path.exists(self.w.home("cage")))

    def test_rustics_cross_arch_difference_is_said_and_fails_nothing(self):
        # Bigfoot's native build and Chopin's cross build can't be the same bytes (cargo hashes
        # the host's rustc -vV into build scripts and proc-macros): said, not a failure.
        r = self.br("--arch", "arm64", "r2", env={"FAKE_RUSTIC_CHECK": "cccc"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rustic-bko linux-arm64: differs, as builds on an arm64 and an amd64 host must "
                      "(docs/design/arm64.md, \"What A2 built\"): bigfoot's bbbb, chopin's check cccc", r.stdout)
        self.assertNotIn("DIFFERS", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.w.home("bigfoot"), self.b, "linux-arm64", "restic-bko")))

    def test_one_build_host(self):
        r = self.w.run([BUILD_ROUND, "--hosts", "chopin", "--split", "legacy", "--arch", "arm64", "r2"],
                       dict(self.env))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = self.parts()
        self.assertFalse(any(x.startswith("cinqcent ") for x in p), p)
        self.assertIn("chopin --sums r2 sliced=no", p)   # the arm64 host's builds counted in
        self.assertIn("./linux-arm64/rustic-bko", self.w.read("chopin", f"{self.b}/SHA256SUMS"))
        self.assertIn("restic-bko linux-arm64: chopin's aaaa, not cross-checked: no check build this round "
                      "(--hosts chopin)", r.stdout)

    def test_dry_run(self):
        r = self.br("--dry-run", "--arch", "arm64", "r2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("bigfoot --host bigfoot --units linux-arm64 --dry-run --arch arm64 r2 sliced=yes",
                      self.parts())
        self.assertNotIn("Cross-checks", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.w.home("chopin"), self.b, "linux-arm64")))

    def test_default_is_amd64_alone(self):
        r = self.br("r2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(any(x.startswith("bigfoot ") for x in self.parts()))
        self.assertIn("chopin --host chopin --units restic,linux,dragonfly,haiku r2 sliced=no",
                      self.parts())   # no --arch: as before A2
        self.assertNotIn("Cross-checks", r.stdout)
        self.assertFalse(os.path.exists(self.w.home("bigfoot") + "/" + W))

    def test_refusals(self):
        for args, msg in ((["--arch", "arm64", "--hosts", "cinqcent", "--split", "legacy", "r2"],
                           "--arch arm64: no host of --hosts cinqcent builds restic-bko linux-arm64"),
                          (["--arch", "x86", "r2"], "--arch: amd64, arm64, amd64,arm64 or all, not x86"),
                          (["--arch", "arm64", "--dist-hosts", "bigfoot", "r2"],
                           "--dist-hosts: bigfoot builds this round's arm64 rustic-bko (--arch arm64)")):
            r = self.w.run([BUILD_ROUND] + list(args), dict(self.env))
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertIn(msg, r.stderr, args)
        self.assertEqual(self.parts(), [])


# --- build-round.sh --dist-hosts an arm64 host ----------------------------------------------

@unittest.skipUnless(RD.REAL_RSYNC, "no rsync here")
class DistArm64(unittest.TestCase):
    def setUp(self):
        self.w = RD.World()
        self.addCleanup(self.w.close)

    def br(self, *args):
        return self.w.run([BUILD_ROUND] + list(args))

    def arm_round(self):
        b = f"{W}/roundr1/bin"
        self.w.put("chopin", f"{b}/linux-arm64/restic-bko", "restic arm64\n", 0o755)
        self.w.put("chopin", f"{b}/linux-arm64/rustic-bko", "rustic arm64\n", 0o755)
        self.w.put("chopin", f"{b}/linux-arm64/SHA256SUMS", self.w.sums("chopin", f"{b}/linux-arm64"))
        return RD.round_bin(self.w)   # the amd64 files, and the SHA256SUMS of all

    def test_an_arm64_host_gets_only_the_arm64_directories(self):
        b = self.arm_round()
        r = self.br("--dist-only", "--dist-hosts", "cage,bigfoot", "r1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.w.files("cage", b), self.w.files("chopin", b))
        self.assertEqual(sorted(self.w.files("bigfoot", b)),
                         ["linux-arm64/SHA256SUMS", "linux-arm64/restic-bko", "linux-arm64/rustic-bko"])
        self.assertIn(f"bigfoot: {b}/{{linux-arm64}} copied from chopin (its arm64 binaries only), 2 sums checked there",
                      r.stdout)
        self.assertTrue(os.access(os.path.join(self.w.home("bigfoot"), b, "linux-arm64", "restic-bko"), os.X_OK))

    def test_dry_run_and_all(self):
        self.arm_round()
        r = self.br("--dist-only", "--dry-run", "--dist-hosts", "bigfoot", "r1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("bigfoot: would copy backotter-work/roundr1/bin's arm64 directories from chopin", r.stdout)
        r = self.br("--dist-only", "--dry-run", "--dist-hosts", "all", "r1")
        self.assertNotIn("bigfoot", r.stdout)   # all: the amd64 hosts, as A1 made it
        self.assertFalse(os.path.exists(self.w.home("bigfoot")))

    def test_a_round_without_arm64(self):
        b = RD.round_bin(self.w)
        r = self.br("--dist-only", "--dist-hosts", "bigfoot,cage", "r1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"bigfoot: FAILED: chopin:{b} has no arm64 directory", r.stdout)
        self.assertEqual(self.w.files("cage", b), self.w.files("chopin", b))   # the others still go

    def test_an_arm64_copy_that_differs(self):
        self.arm_round()
        self.w.put("bigfoot", "bin/sha256sum", '#!/bin/sh\necho "x: FAILED" >&2; exit 1\n', 0o755)
        r = self.br("--dist-only", "--dist-hosts", "bigfoot", "r1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("doesn't check against its SHA256SUMS", r.stdout)


# --- promote-bins.sh to an arm64 host -------------------------------------------------------

@unittest.skipUnless(RD.REAL_RSYNC, "no rsync here")
class PromoteArm64(unittest.TestCase):
    def setUp(self):
        self.w = RD.World()
        self.addCleanup(self.w.close)
        w = self.w
        w.put("chopin", f"{W}/bko-bin/linux/restic-bko", "restic linux\n", 0o755)
        w.put("chopin", f"{W}/bko-bin/linux-arm64/restic-bko", "restic arm64\n", 0o755)
        w.put("chopin", f"{W}/bko-bin/linux-arm64/rustic-bko", "rustic arm64\n", 0o755)
        w.put("chopin", f"{W}/bko-bin/linux-arm64/SHA256SUMS", w.sums("chopin", f"{W}/bko-bin/linux-arm64"))
        w.put("chopin", f"{W}/bko-bin/BUILD-INFO", "assembled on chopin\n")
        w.put("chopin", f"{W}/bko-bin/SHA256SUMS", w.sums("chopin", f"{W}/bko-bin"))
        w.put("chopin", f"{W}/cmp-bin/restic-bko", "cmp restic\n", 0o755)
        w.put("chopin", f"{W}/holt-bin/linux/bko-holt", "holt linux\n", 0o755)
        w.put("chopin", f"{W}/holt-bin/linux-arm64/bko-holt", "holt arm64\n", 0o755)
        w.put("chopin", f"{W}/holt-bin/linux-arm64/COMMIT", "c" * 40 + "\n")
        w.put("chopin", f"{W}/holt-bin/linux-arm64/SHA256SUMS", w.sums("chopin", f"{W}/holt-bin/linux-arm64"))
        for h in ("cage", "bigfoot"):
            os.makedirs(os.path.join(w.home(h), W))

    def pb(self, *args):
        return self.w.run([PROMOTE] + list(args))

    def test_bigfoot_gets_the_arm64_directories_alone(self):
        r = self.pb("--dry-run", "bigfoot")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("bigfoot bko-bin: would copy 3 of 3 files", r.stdout)
        self.assertIn("bigfoot cmp-bin: nothing for an arm64 host (chopin:backotter-work/cmp-bin has no *-arm64/): left alone",
                      r.stdout)
        r = self.pb("bigfoot")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(self.w.files("bigfoot", f"{W}/bko-bin")),
                         ["linux-arm64/SHA256SUMS", "linux-arm64/restic-bko", "linux-arm64/rustic-bko"])
        self.assertEqual(sorted(self.w.files("bigfoot", f"{W}/holt-bin")),
                         ["linux-arm64/COMMIT", "linux-arm64/SHA256SUMS", "linux-arm64/bko-holt"])
        self.assertFalse(os.path.exists(os.path.join(self.w.home("bigfoot"), W, "cmp-bin")))
        self.assertIn("bigfoot bko-bin: 3 of 3 files copied from chopin (its arm64 directories: linux-arm64), "
                      "all 3 checked by sha256", r.stdout)
        r = self.pb("bigfoot")
        self.assertIn("bigfoot bko-bin: up to date (3 files)", r.stdout)

    def test_through_the_mac(self):
        self.w.flag("noh2h")
        r = self.pb("--sets", "holt-bin", "cage", "bigfoot")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("bigfoot holt-bin: chopin can't copy to m@bigfoot.local: through the Mac", r.stdout)
        self.assertEqual(sorted(self.w.files("bigfoot", f"{W}/holt-bin")),
                         ["linux-arm64/COMMIT", "linux-arm64/SHA256SUMS", "linux-arm64/bko-holt"])
        self.assertEqual(self.w.files("cage", f"{W}/holt-bin"), self.w.files("chopin", f"{W}/holt-bin"))   # whole

    def test_an_amd64_file_there_is_extra(self):
        self.w.put("bigfoot", f"{W}/bko-bin/linux/restic-bko", "an amd64 binary\n", 0o755)
        r = self.pb("--sets", "bko-bin", "bigfoot")
        self.assertIn("1 there that chopin hasn't (./linux/restic-bko), kept", r.stdout)
        r = self.pb("--delete", "--sets", "bko-bin", "bigfoot")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNone(self.w.read("bigfoot", f"{W}/bko-bin/linux/restic-bko"))
        self.assertEqual(self.w.read("bigfoot", f"{W}/bko-bin/linux-arm64/restic-bko"), "restic arm64\n")

    def test_all_is_still_amd64(self):
        r = self.pb("--dry-run", "all")
        self.assertNotIn("bigfoot", r.stdout)


# --- build-holt.sh linux-arm64 --------------------------------------------------------------

class BuildHoltArm64(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="t2-holt-arm-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def run_bh(self, *args, **env):
        e = clean_env(**dict(dict(HOLT_SRC=os.path.join(REPO, "holt"), HOLT_OUT=os.path.join(self.d, "out"),
                                  HOLT_LOGS=os.path.join(self.d, "logs"), TMPDIR=self.d), **env))
        e.pop("T2_HOST_ROOT", None)
        return subprocess.run(["bash", BUILD_HOLT] + list(args), capture_output=True, text=True, env=e, timeout=60)

    def test_refused_off_an_arm64_host(self):
        if subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip() == "aarch64":
            self.skipTest("an aarch64 machine would build")
        r = self.run_bh("linux-arm64")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("build-holt: linux-arm64 builds natively on an arm64 host (testvm/hosts.json's arch: bigfoot)",
                      r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.d, "out", "bko-holt")))

    def test_a_platform(self):
        r = self.run_bh("sparc")
        self.assertEqual(r.returncode, 2)
        self.assertIn("linux, linux-arm64, freebsd", r.stderr)
        r = self.run_bh("linux-arm64", TMPDIR="")
        self.assertEqual(r.returncode, 2)
        self.assertIn("linux-arm64: TMPDIR must name a directory", r.stderr)

    def test_its_default_output(self):
        with open(BUILD_HOLT) as f:
            text = f.read()
        # bin/PLATFORM/bko-holt (linux-arm64 among the BSDs'), linux's alone in bin/
        self.assertIn("OUT=${HOLT_OUT:-/bkoscratch/holttest1d/bin/$PLATFORM}\n[[ $PLATFORM != linux ]] ||", text)


if __name__ == "__main__":
    unittest.main()
