"""ARM64 A6 (docs/design/arm64.md, "What A6 built"): the arm64 guests' registrations and the
heavy-test driver.

- Test2: classes.json's arm64 block is merged (plan.arch_classes) and the -arm64 cells are in a
  word's universe only for a run whose --hosts has an arm64 host, so an amd64 run's cells are
  what they were; HoltTest2ARM; the arm64 sources' rescuer cap; the specs' entries.
- SticTest1: the five checks (their in-guest parts are their siblings'), drive.py's bins and its
  unnamed round without them.
- HoltTest1: ht1.sh's PLAT, rescuer and debs by the guest's architecture (its source), and
  make-rescuer.sh's --arch refusals.
- HostTest2: run-all.sh's arm64 profile and gate/loop.sh's linux-arm64 commits.
- The driver: a6-run.sh (the Mac's, bash 3.2) against a fake ssh that runs the "remote" side in
  a temporary home, and a6-stages.sh's stage selection and dry run (bash 4: skipped on the Mac).

No VM, no network, no host."""

import json
import os
import re
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

from t2lib import hosts as H  # noqa: E402
from t2lib import plan, words  # noqa: E402

ARM = ["alpine324-arm64", "debian13-arm64", "debian13-zfs-arm64", "fedora44-arm64", "ubuntu26-arm64"]


def rd(*p):
    with open(os.path.join(*p), errors="replace") as f:
        return f.read()


def bash_major(bash="bash"):
    try:
        out = subprocess.run([bash, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


BASH4 = bash_major() >= 4


def ctx(hosts):
    return plan.Ctx(REPO, hosts=hosts, facts={}, options={})


def universe(fam, hosts):
    c = ctx(hosts)
    return plan.Planner(words.Word(fam, ""), c).universe()


# --- Test2 ------------------------------------------------------------------------------------

class Registrations(unittest.TestCase):
    def test_amd64_runs_have_no_arm64_cell(self):
        for fam in ("stic", "holt", "guest"):
            u = universe(fam, ["chopin", "cinqcent", "copland", "cage"])
            self.assertEqual([c for c in u if c.endswith("-arm64")], [], fam)

    def test_a_run_with_bigfoot_has_the_five(self):
        for fam in ("stic", "holt", "guest"):
            u = universe(fam, ["chopin", "bigfoot"])
            self.assertEqual(sorted(c.split(":", 1)[1] for c in u if c.endswith("-arm64")), ARM, fam)
            amd = universe(fam, ["chopin"])
            self.assertEqual([c for c in u if not c.endswith("-arm64")], amd, f"{fam}: the amd64 cells as without bigfoot")

    def test_bigfoot_alone_keeps_the_amd64_cells(self):
        # (A1's: an amd64 cell in a run of bigfoot alone is there, and not runnable)
        u = universe("stic", ["bigfoot"])
        self.assertIn("stic:debian13", u)
        self.assertIn("stic:debian13-arm64", u)

    def test_the_block_merges_only_with_an_arm64_host(self):
        raw = plan.load_json(os.path.join(T2, "classes.json"))
        self.assertIs(plan.arch_classes(raw, ["chopin", "cinqcent"]), raw)
        m = plan.arch_classes(raw, ["chopin", "bigfoot"])
        self.assertEqual(m["holt"]["families"]["ARM"], raw["arm64"]["holt"]["families"]["ARM"])
        self.assertEqual(set(m["holt"]["uefi"]) - set(raw["holt"]["uefi"]), set(ARM))
        self.assertEqual(set(m["holt"]["needs_host_zfs"]) - set(raw["holt"]["needs_host_zfs"]), {"debian13-zfs-arm64"})
        self.assertNotIn("ARM", raw["holt"]["families"])   # (the file itself is unchanged by the merge)
        self.assertEqual(sorted(plan.arch_block_names(raw)), ARM)

    def test_known_names(self):
        k = plan.known_names(ctx(["chopin"]))
        self.assertFalse([g for g in k["stic"] + k["guest"] + k["holt"]["sources"] if g.endswith("-arm64")])
        k = plan.known_names(ctx(["chopin", "bigfoot"]))
        self.assertEqual(sorted(g for g in k["stic"] if g.endswith("-arm64")), ARM)
        self.assertEqual(sorted(k["holt"]["families"]["ARM"]), ARM)

    def test_check_classes_names_no_arm64_error(self):
        for hs in (["chopin"], ["chopin", "bigfoot"]):
            errs = plan.check_classes(ctx(hs))
            self.assertEqual([e for e in errs if "arm64" in e or "77" in e], [], hs)

    def test_holt_arm_word(self):
        self.assertIn("ARM", words.HOLT_LETTERS)
        w = words.parse(["HoltTest2ARM", "plan"], plan.known_names(ctx(["chopin", "bigfoot"])))
        self.assertEqual(w.letters, ["ARM"])

    def test_the_arm64_sources_need_their_rescuer(self):
        c = ctx(["bigfoot"])
        p = plan.Planner(words.Word("holt", ""), c)
        v = plan.View("holt:debian13-arm64", "holt")
        self.assertEqual(p.holt_inputs(v, "bigfoot"),
                         [("holt_rescuer_arm64", "no /bkoscratch/holttest1d/rescuer/rescuer-arm64.qcow2")])
        v = plan.View("holt:debian13-arm64+img", "holt")
        self.assertEqual(p.holt_inputs(v, "bigfoot"), [])
        v = plan.View("holt:debian13", "holt")
        self.assertEqual([k for k, _ in p.holt_inputs(v, "chopin")], ["holt_rescuer", "holt_debs"])

    def test_spec_entries(self):
        hs = json.loads(rd(T2, "holt", "spec.json"))["entries"]
        ms = json.loads(rd(T2, "stic", "manifests.json"))["entries"]
        lj = json.loads(rd(T2, "stic", "limits.json"))
        gs = json.loads(rd(T2, "guest", "spec.json"))["entries"]
        for g in ARM:
            self.assertEqual((hs[g]["kind"], hs[g]["plat"], hs[g]["rescuer"], hs[g]["replace"]),
                             ("linux", "linux-arm64", ["rescuer-arm64.qcow2"], ["rescuer"]), g)
            self.assertEqual(ms[g]["os"], "linux-arm64", g)
            self.assertEqual(ms[g]["bins"], ["linux-arm64/restic-bko", "linux-arm64/rustic-bko"], g)
            self.assertIn(f"testvm/stictest1/{g}/**", ms[g]["harness"], g)
            self.assertIn(g, lj)
            self.assertEqual((gs[g]["plat"], gs[g]["checks"]["established"]), ("linux-arm64", False), g)
        self.assertEqual(gs["ubuntu26-arm64"]["checks"]["fw"], "uefi-sb")
        self.assertEqual({gs[g]["checks"]["fw"] for g in ARM if g != "ubuntu26-arm64"}, {"uefi"})
        est = rd(T2, "estimates.tsv")
        for g in ARM:
            for f in ("stic", "holt", "guest"):
                self.assertRegex(est, rf"(?m)^{f}\t{f}:{re.escape(g)}\t", f"{f}:{g}")

    def test_facts_caps_arm64_only_on_aarch64(self):
        src = rd(T2, "t2lib", "facts_host.py")
        self.assertIn('if platform.machine() == "aarch64" and not ROOT:', src)


# --- SticTest1 --------------------------------------------------------------------------------

def load_drive():
    import importlib.util
    spec = importlib.util.spec_from_file_location("drive_a6", os.path.join(TESTVM, "stictest1", "drive.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class SticTest1(unittest.TestCase):
    SIBLING = {"debian13-arm64": "ubuntu26-uefi", "ubuntu26-arm64": "ubuntu26-uefi", "fedora44-arm64": "fedora44-uefi",
               "alpine324-arm64": "alpine324", "debian13-zfs-arm64": "debian13-zfs"}

    def test_the_five_checks(self):
        d = load_drive()
        spec = d.load_specs(ARM)
        for g in ARM:
            self.assertEqual(spec[g].os, "linux-arm64", g)
            t = rd(TESTVM, "stictest1", g, "check.sh")
            self.assertIn("roundN/bin/linux-arm64", t, g)
            self.assertIn(f"../{self.SIBLING[g]}", t, g)
            self.assertIn("aarch64", t, g)   # refuses another host, and checks the guest's machine
            self.assertIn('${BKO_TESTVM:-$WORK/testvm}', t, g)
            self.assertNotIn("/mnt/bko-ram/$PREFIX/$GUEST", t, g)
        # Bigfoot's RAM disk: every reserve fits its 9.0 ceiling; debian13-zfs-arm64's overlay is on disk
        for g in ARM:
            self.assertLessEqual(spec[g].d, 9.0, g)
        self.assertIn("ST1_SCRATCH:-/bkoscratch/st1-scratch", rd(TESTVM, "stictest1", "debian13-zfs-arm64", "check.sh"))

    def test_unnamed_rounds_leave_them_out(self):
        d = load_drive()
        left = [g for g in d.all_guests() if not d.no_arch_host([g], ["chopin", "cinqcent"])]
        self.assertFalse([g for g in left if g.endswith("-arm64")])
        self.assertEqual([g for g in d.all_guests() if not d.no_arch_host([g], ["bigfoot"]) and g.endswith("-arm64")], ARM)
        self.assertIn("if not a.guests:", rd(TESTVM, "stictest1", "drive.py"))

    def test_readme_estimates(self):
        e = load_drive().readme_estimates()
        self.assertEqual({g: e.get(g) for g in ARM},
                         {"alpine324-arm64": 153, "debian13-arm64": 374, "debian13-zfs-arm64": 856,
                          "fedora44-arm64": 268, "ubuntu26-arm64": 374})


# --- HoltTest1 --------------------------------------------------------------------------------

class HoltTest1(unittest.TestCase):
    def test_ht1_by_architecture(self):
        t = rd(TESTVM, "holttest1d", "ht1.sh")
        self.assertIn("[[ $G != *-arm64 ]] || GARCH=arm64 PLAT=linux-arm64", t)
        self.assertIn('ht_holt_pick "$PLAT"', t)
        self.assertIn('ht_rustic_take "$PLAT"', t)
        self.assertIn("IMG_PIN=${T2_HOST_ROOT:-/bkoscratch/test2}/img-pin/$PLAT", t)
        self.assertIn("rescuer/rescuer-arm64.qcow2", t)
        self.assertNotIn("ht_holt_pick linux", t)
        self.assertNotIn("ht_rustic_take linux", t)
        # the debs: amd64's only (their copies into the share and the check before a run)
        self.assertEqual(len(re.findall(r'\[\[ \$GARCH != amd64 \]\] \|\| cp "\$BIN"/\*\.deb', t)), 2)
        self.assertIn('if [[ $GARCH == amd64 ]] && [[ " ${PHASES[*]} " == *" backup "*', t)

    def test_ht1_refuses_another_architecture(self):
        # Only the top of ht1.sh, up to its architecture check, with uname stubbed.
        t = rd(TESTVM, "holttest1d", "ht1.sh")
        top = t[t.index("GARCH=amd64 PLAT=linux"):t.index('HOLT=""; [[ ${HT_VARIANT:-} == img ]]')]
        for g, m, rc in (("debian13-arm64", "x86_64", 2), ("debian13", "aarch64", 2), ("debian13-arm64", "aarch64", 0),
                         ("debian13", "x86_64", 0), ("debian13", "arm64", 0)):   # (arm64: the Mac's uname)
            p = subprocess.run(["bash", "-c", f'uname() {{ echo {m}; }}; hostname() {{ echo h; }}; G={g}\n{top}\necho "$GARCH $PLAT"'],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, rc, (g, m, p.stderr))
            if rc == 0:
                self.assertEqual(p.stdout.split(), ["arm64", "linux-arm64"] if g.endswith("-arm64") else ["amd64", "linux"])

    def test_make_rescuer_refusals(self):
        with tempfile.TemporaryDirectory() as d:
            u = os.path.join(d, "uname")
            with open(u, "w") as f:
                f.write("#!/bin/sh\necho x86_64\n")
            os.chmod(u, 0o755)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"])
            mr = os.path.join(TESTVM, "holttest1d", "make-rescuer.sh")
            p = subprocess.run(["bash", mr, "--arch", "arm64"], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(p.returncode, 2)
            self.assertIn("run it on an aarch64 host", p.stderr)
            p = subprocess.run(["bash", mr, "--arch", "riscv64"], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(p.returncode, 2)
            self.assertIn("--arch amd64 or arm64", p.stderr)
        t = rd(TESTVM, "holttest1d", "make-rescuer.sh")
        self.assertIn("[[ $ARCH != arm64 ]] || BASE=ubuntu26-arm64 NAME=rescuer-arm64", t)
        self.assertIn('QPID=(-- -pidfile "$D/$NAME.qemu.pid")', t)
        # amd64's QEMU line takes nothing more: QPID is empty there
        self.assertIn('"$D/$NAME.new.qcow2" "${QPID[@]}" </dev/null', t)

    def test_queue_and_sizes(self):
        q = rd(TESTVM, "holttest1d", "ht1-queue.sh")
        self.assertIn("ri=$L/rescuer/rescuer-arm64.qcow2", q)
        self.assertIn("$g != *-arm64 ]] && ! has_debs", q)
        rows = [l.split("\t") for l in rd(TESTVM, "holttest1d", "ram-sizes.tsv").splitlines() if l and not l.startswith("#")]
        got = {r[0]: (r[2], r[4], r[5]) for r in rows if r[0].endswith("-arm64")}
        self.assertEqual(got, {"alpine324-arm64": ("bigfoot", "2", "4"), "debian13-arm64": ("bigfoot", "3", "4"),
                               "debian13-zfs-arm64": ("bigfoot", "4", "4"), "fedora44-arm64": ("bigfoot", "6", "4"),
                               "ubuntu26-arm64": ("bigfoot", "10", "4")})
        self.assertEqual(rd(TESTVM, "holttest1d", "img-boot.d", "linux-arm64.json"),
                         rd(TESTVM, "holttest1d", "img-boot.d", "linux.json"))


# --- HostTest2 --------------------------------------------------------------------------------

@unittest.skipUnless(BASH4, "run-all.sh needs bash 4 (run on a Linux host)")
class HostTest2(unittest.TestCase):
    def run_all(self, host, oses=("linux", "linux-arm64")):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "work", "vms"))
            for o in oses:
                os.makedirs(os.path.join(d, "bin", o))
                for t in ("restic", "rustic"):
                    with open(os.path.join(d, "bin", o, f"{t}-bko"), "w") as f:
                        f.write("x 0.19.1+backotter.7\n")
                    with open(os.path.join(d, "bin", o, f"{t}-commit"), "w") as f:
                        f.write("a" * 40 + "\n" + ("source digest x\n" if t == "restic" else "rustic_core " + "b" * 40 + "\n"))
            e = {k: v for k, v in os.environ.items()
                 if k not in ("LINUX_FS", "SPARSE_FS", "PORT_BASE", "MAX_GUESTS", "ANY_HOST", "JOBS_CAP", H.ENV)}
            e.update(BKO_WORK=os.path.join(d, "work"), OLD_BIN="none", UNCHECKED_BINARIES="1")
            r = subprocess.run(["bash", os.path.join(TESTVM, "compare", "run-all.sh"), "--host", host, "--dry-run",
                                os.path.join(d, "bin"), os.path.join(d, "log")], capture_output=True, text=True, env=e, timeout=120)
            return r.returncode, r.stdout.replace(d, "D"), r.stderr.replace(d, "D")

    def test_bigfoots_profile(self):
        rc, out, err = self.run_all("bigfoot")
        self.assertIn("linux: zfs tmpfs; sparse: zfs zfs-nocomp", out)
        self.assertIn("D/bin/linux-arm64/restic-bko", out)
        self.assertNotIn("D/bin/linux/restic-bko", out)
        self.assertNotIn("nonroot", re.sub(r"(?m)^== .*$", "", out).split("== binaries")[-1].split("linux:")[0])

    def test_amd64_profiles_take_linux(self):
        for h in ("chopin", "cinqcent", "copland"):
            rc, out, err = self.run_all(h)
            self.assertIn("D/bin/linux/restic-bko", out, h)
            self.assertNotIn("linux-arm64", out, h)

    def test_sparse_disk_work_on_bigfoot_only(self):
        # Bigfoot's sparse-rt.sh keeps its lock and out/ in the run's log, never /var/tmp.
        rc, out, err = self.run_all("bigfoot")
        self.assertIn("SPARSE_DISK_WORK=D/log/sparse/zfs.work ", out)
        self.assertIn("SPARSE_DISK_WORK=D/log/sparse/zfs-nocomp.work ", out)
        for h in ("chopin", "cinqcent", "copland"):
            self.assertNotIn("SPARSE_DISK_WORK", self.run_all(h)[1], h)
        t = rd(TESTVM, "compare", "sparse-rt.sh")
        self.assertIn('[ -z "${SPARSE_DISK_WORK:-}" ] || DISK_W=$SPARSE_DISK_WORK', t)

    def test_loop_reads_linux_arm64_on_aarch64(self):
        t = rd(T2, "gate", "loop.sh")
        self.assertIn('los=linux; [[ $(uname -m) != aarch64 ]] || los=linux-arm64', t)
        self.assertIn('"$bin/$los/restic-commit"', t)


# --- the driver -------------------------------------------------------------------------------

FAKE_SSH = r'''#!/bin/bash
# a fake ssh: the "remote" is this machine, its HOME the test's; the command is logged.
while [[ $1 == -o ]]; do shift 2; done
host=$1; shift
printf '%s\n' "$*" >>"$A6T/ssh.log"
export HOME=$A6T/home PATH=$A6T/bin:$PATH
case "$*" in
    *"a6-stages.sh --dry-run"*) echo "DRY-RUN-PLAN $*"; exit 0 ;;
esac
exec bash -c "$*"
'''
STUBS = {
    "uname": '#!/bin/sh\n[ "$1" = -m ] && echo aarch64 || echo Linux\n',
    "sudo": '#!/bin/sh\n[ "$1" = -n ] && shift\nprintf "%s\\n" "$*" >>"$A6T/sudo.log"\ncase "$1" in systemd-run) mkdir -p "$HOME/backotter-work/a6-run"; for a; do case $a in *a6-run/*/tree/*) r=${a#*a6-run/}; r=${r%%/*}; echo running >"$HOME/backotter-work/a6-run/$r/state" 2>/dev/null;; esac; done; exit 0;; journalctl) exit 0;; esac\nexit 0\n',
    "systemctl": '#!/bin/sh\ncase "$1" in is-active) echo active;; esac\nexit 0\n',
}


class Driver(unittest.TestCase):
    """a6-run.sh with a fake ssh: what it ships, what it starts, and its arguments."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="a6-run-")
        self.addCleanup(shutil.rmtree, self.t, True)
        os.makedirs(os.path.join(self.t, "home", "backotter-work"))
        b = os.path.join(self.t, "bin")
        os.makedirs(b)
        for n, s in dict(STUBS, ssh=FAKE_SSH).items():
            with open(os.path.join(b, n), "w") as f:
                f.write(s)
            os.chmod(os.path.join(b, n), 0o755)
        # A checkout of its own: a6-run.sh ships its committed HEAD (git archive).
        self.repo = os.path.join(self.t, "repo")
        os.makedirs(os.path.join(self.repo, "testvm", "arm64"))
        for n in ("a6-run.sh", "a6-stages.sh"):
            shutil.copy(os.path.join(TESTVM, "arm64", n), os.path.join(self.repo, "testvm", "arm64", n))
        shutil.copy(os.path.join(TESTVM, "hosts.json"), os.path.join(self.repo, "testvm", "hosts.json"))
        with open(os.path.join(self.repo, "file.txt"), "w") as f:
            f.write("committed\n")
        g = lambda *a: subprocess.run(["git", "-C", self.repo] + list(a), check=True, capture_output=True)   # noqa: E731
        g("init", "-q")
        g("add", "-A")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
        with open(os.path.join(self.repo, "file.txt"), "w") as f:
            f.write("another session's uncommitted edit\n")

    def run_(self, *args):
        env = dict(os.environ, A6T=self.t, PATH=os.path.join(self.t, "bin") + os.pathsep + os.environ["PATH"])
        return subprocess.run(["/bin/bash", os.path.join(self.repo, "testvm", "arm64", "a6-run.sh")] + list(args),
                              capture_output=True, text=True, env=env, timeout=120, cwd=self.repo)

    def log(self, n):
        p = os.path.join(self.t, n)
        return rd(p) if os.path.exists(p) else ""

    def test_help_and_bad_arguments(self):
        p = self.run_("--help")
        self.assertEqual(p.returncode, 0)
        self.assertIn("--dry-run", p.stdout)
        self.assertIn("--fetch", p.stdout)
        for bad in (["--bogus"], ["--only"], ["--only", "stic;rm"], ["--stamp", "-x"], ["--host", "a b"]):
            self.assertEqual(self.run_(*bad).returncode, 2, bad)
        self.assertEqual(self.log("ssh.log"), "", "nothing reaches the host on a usage error")

    def test_a_run_ships_head_and_starts_a_service_in_the_slice(self):
        p = self.run_("--stamp", "s1", "--no-batch-check", "--only", "stic:alpine324-arm64")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        tree = os.path.join(self.t, "home", "backotter-work", "a6-run", "s1", "tree")
        self.assertEqual(rd(tree, "file.txt"), "committed\n", "HEAD's file, not the working tree's")
        head = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(rd(tree, "COMMIT").strip(), head)
        su = self.log("sudo.log")
        self.assertRegex(su, r"systemd-run --quiet --unit=bko-a6-s1 --slice=bko\.slice --collect")
        self.assertIn("setpriv --reuid=m --regid=m --init-groups --reset-env --", su)
        self.assertIn("a6-run/s1/tree/testvm/arm64/a6-stages.sh --run a6-run/s1 --only stic:alpine324-arm64", su)
        self.assertIn("--status s1", p.stdout)
        # a second start with the same stamp is refused
        p = self.run_("--stamp", "s1", "--no-batch-check")
        self.assertEqual(p.returncode, 1)
        self.assertIn("there already", p.stderr)

    def test_dry_run_ships_a_scratch_copy_and_removes_it(self):
        p = self.run_("--dry-run", "--stamp", "d1", "--skip", "t2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("DRY-RUN-PLAN", p.stdout)
        self.assertIn("a6-stages.sh --dry-run --run ~/backotter-work/a6-run/dry-d1 --skip t2", self.log("ssh.log"))
        self.assertFalse(os.path.exists(os.path.join(self.t, "home", "backotter-work", "a6-run", "dry-d1")))
        self.assertEqual(self.log("sudo.log"), "", "a dry run starts nothing")

    def test_tree_ships_that_trees_files(self):
        p = self.run_("--dry-run", "--stamp", "d2", "--tree", self.repo)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("+tree:", p.stdout)
        self.assertIn("(dirty)", p.stdout)

    def test_status_fetch_and_stop_name_a_run(self):
        p = self.run_("--status")
        self.assertEqual(p.returncode, 1)
        self.assertIn("no a6 run", p.stderr)
        p = self.run_("--stop", "nosuch")
        self.assertEqual(p.returncode, 1)

    def test_a_value_option_last_is_a_usage_error(self):
        # (bash's shift 2 with one argument left doesn't shift: it used to loop for ever)
        for bad in (["--stamp"], ["--list", "--tree"], ["--host"], ["--dry-run", "--tree"], ["--bins"], ["--img-guests"]):
            p = self.run_(*bad)
            self.assertEqual(p.returncode, 2, bad)
            self.assertIn("needs a value", p.stderr, bad)

    def test_latest_is_the_last_started_not_the_last_by_name(self):
        base = os.path.join(self.t, "home", "backotter-work", "a6-run")
        for n, t in (("a6stop2", 1000), ("20261116-150000", 2000), ("dry-x", 3000)):
            os.makedirs(os.path.join(base, n))
            with open(os.path.join(base, n, "state"), "w") as f:
                f.write("done\n")
            os.utime(os.path.join(base, n), (t, t))
        p = self.run_("--status")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("run 20261116-150000:", p.stdout, "the newest by mtime, not a6stop2 by name")
        with open(os.path.join(base, ".latest"), "w") as f:
            f.write("a6stop2\n")
        self.assertIn("run a6stop2:", self.run_("--status").stdout, ".latest wins")
        with open(os.path.join(base, ".latest"), "w") as f:
            f.write("gone\n")
        self.assertIn("run 20261116-150000:", self.run_("--status").stdout, "a .latest with no run is passed over")

    def test_a_start_records_the_latest(self):
        p = self.run_("--stamp", "zz9", "--no-batch-check", "--only", "stic:alpine324-arm64")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(rd(self.t, "home", "backotter-work", "a6-run", ".latest").strip(), "zz9")


def fake_forks(git, versions=("0.19.1+backotter.7", "0.11.4+backotter.13", "0.13.0+backotter.12")):
    """BKO_GIT with restic, rustic and rustic_core repos of one commit each: {repo: commit}."""
    files = {"restic": ("internal/global/global.go", f'package global\nconst Version = "{versions[0]}"\n'),
             "rustic": ("Cargo.toml", f'[package]\nname = "rustic-rs"\nversion = "{versions[1]}"\n'),
             "rustic_core": ("crates/core/Cargo.toml", f'[package]\nname = "rustic_core"\nversion = "{versions[2]}"\n')}
    out = {}
    for repo, (path, text) in files.items():
        d = os.path.join(git, repo)
        os.makedirs(os.path.dirname(os.path.join(d, path)), exist_ok=True)
        with open(os.path.join(d, path), "w") as f:
            f.write(text)
        g = lambda *a: subprocess.run(["git", "-C", d] + list(a), check=True, capture_output=True, text=True)   # noqa: E731
        g("init", "-q")
        g("add", "-A")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v")
        out[repo] = g("rev-parse", "HEAD").stdout.strip()
    return out


def write_commit_files(d, c):
    os.makedirs(d, exist_ok=True)
    for t in ("restic", "rustic"):
        with open(os.path.join(d, f"{t}-bko"), "w") as f:
            f.write("x\n")
    with open(os.path.join(d, "restic-commit"), "w") as f:
        f.write(f"{c['restic']} (from somewhere)\nsource digest x\n")
    with open(os.path.join(d, "rustic-commit"), "w") as f:
        f.write(f"{c['rustic']} (local)\nrustic_core {c['rustic_core']} (local)\n")


class DriverExpectations(Driver):
    """a6-run.sh makes hosttest2:bin's expect.tsv on the Mac, from BKO_GIT, for the set's commits."""

    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(TESTVM, "arm64", "a6-bin.py"), os.path.join(self.repo, "testvm", "arm64", "a6-bin.py"))
        os.symlink(T2, os.path.join(self.repo, "testvm", "test2"))
        self.git = os.path.join(self.t, "git")
        self.c = fake_forks(self.git)

    def run_(self, *args):
        env = dict(os.environ, A6T=self.t, BKO_GIT=self.git, PATH=os.path.join(self.t, "bin") + os.pathsep + os.environ["PATH"])
        return subprocess.run(["/bin/bash", os.path.join(self.repo, "testvm", "arm64", "a6-run.sh")] + list(args),
                              capture_output=True, text=True, env=env, timeout=120, cwd=self.repo)

    def test_expectations_shipped(self):
        write_commit_files(os.path.join(self.t, "home", "backotter-work", "bko-bin", "linux-arm64"), self.c)
        p = self.run_("--stamp", "e1", "--no-batch-check", "--only", "hosttest2:bin")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        e = os.path.join(self.t, "home", "backotter-work", "a6-run", "e1", "expect")
        self.assertEqual(rd(e, "expect.tsv"), "linux-arm64/restic-bko\t0.19.1+backotter.7\t\n"
                                              "linux-arm64/rustic-bko\t0.11.4+backotter.13\t0.13.0+backotter.12\n")
        self.assertEqual(json.loads(rd(e, "commits.json"))["linux-arm64/rustic-bko"]["rustic_core"], self.c["rustic_core"])
        self.assertIn("hosttest2:bin will expect linux-arm64/restic-bko 0.19.1+backotter.7", p.stdout)

    def test_bins_option_names_the_set(self):
        write_commit_files(os.path.join(self.t, "home", "other", "linux-arm64"), self.c)
        p = self.run_("--stamp", "e2", "--no-batch-check", "--bins", "~/other", "--only", "hosttest2:bin")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.t, "home", "backotter-work", "a6-run", "e2", "expect", "expect.tsv")))

    def test_a_commit_not_in_bko_git_is_a_note(self):
        write_commit_files(os.path.join(self.t, "home", "backotter-work", "bko-bin", "linux-arm64"),
                           dict(self.c, rustic="f" * 40))
        p = self.run_("--stamp", "e3", "--no-batch-check", "--only", "hosttest2:bin")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("no expectations for hosttest2:bin", p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.t, "home", "backotter-work", "a6-run", "e3", "expect", "expect.tsv")))


class BinJudge(unittest.TestCase):
    """a6-bin.py judge: gate:bin:static and gate:bin:version as gate.py judges them."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="a6-bin-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.c = fake_forks(os.path.join(self.t, "git"))
        self.set = os.path.join(self.t, "bins")
        write_commit_files(os.path.join(self.set, "linux-arm64"), self.c)
        self.exp = os.path.join(self.t, "expect")
        r = subprocess.run([sys.executable, "-I", "-B", os.path.join(TESTVM, "arm64", "a6-bin.py"), "expect", "--git",
                            os.path.join(self.t, "git"), os.path.join(self.set, "linux-arm64", "restic-commit"),
                            os.path.join(self.set, "linux-arm64", "rustic-commit"), self.exp], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def judge(self, static, version, holt):
        fs = []
        for n, t in (("s", static), ("v", version), ("h", holt)):
            fs.append(os.path.join(self.t, n + ".out"))
            with open(fs[-1], "w") as f:
                f.write(t)
        r = subprocess.run([sys.executable, "-I", "-B", os.path.join(TESTVM, "arm64", "a6-bin.py"), "judge", "--set", self.set,
                            "--expect", self.exp] + fs, capture_output=True, text=True)
        return r.returncode, r.stdout

    STATIC = "PASS static /b/restic-bko: static\nPASS static /b/rustic-bko: static\n"
    VERSION = ("PASS version linux-arm64/restic-bko [0.19.1+backotter.7]: holds 0.19.1+backotter.7\n"
               "PASS version linux-arm64/rustic-bko [0.11.4+backotter.13]: holds 0.11.4+backotter.13, bko_core 0.13.0+backotter.12\n")
    HOLT = "PASS version /b/linux-arm64/bko-holt [0.2.6]: prints bko-holt 0.2.6\n"

    def test_pass(self):
        rc, out = self.judge(self.STATIC, self.VERSION, self.HOLT)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(re.findall(r"(?m)^PASS ", out)), 4)

    def test_an_unchecked_binary_fails(self):
        v = self.VERSION.replace("PASS version linux-arm64/rustic-bko [0.11.4+backotter.13]: holds 0.11.4+backotter.13, bko_core 0.13.0+backotter.12",
                                 "SKIP version linux-arm64/rustic-bko [0.11.4+backotter.13]: holds 0.11.4+backotter.13; no expected version")
        rc, out = self.judge(self.STATIC, v, self.HOLT)
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"(?m)^FAIL version: .*rustic-bko")
        rc, out = self.judge(self.STATIC, self.VERSION.splitlines()[0] + "\n", self.HOLT)
        self.assertEqual(rc, 1, "a set binary with no line fails")

    def test_holt_must_pass(self):
        rc, out = self.judge(self.STATIC, self.VERSION, "SKIP version /b/linux-arm64/bko-holt [0.2.6]: prints bko-holt 0.2.6; no expected version\n")
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"(?m)^FAIL holt: ")
        self.assertEqual(self.judge(self.STATIC, self.VERSION, "")[0], 1)

    def test_static_fail_and_wrong_commits(self):
        self.assertEqual(self.judge("FAIL static /b/rustic-bko: NT_GNU_ABI_TAG\n", self.VERSION, self.HOLT)[0], 1)
        write_commit_files(os.path.join(self.set, "linux-arm64"), dict(self.c, restic="e" * 40))
        rc, out = self.judge(self.STATIC, self.VERSION, self.HOLT)
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"(?m)^FAIL expect: expect.tsv is for ")
        shutil.rmtree(self.exp)
        self.assertRegex(self.judge(self.STATIC, self.VERSION, self.HOLT)[1], r"(?m)^FAIL expect: no ")


def shell_function(path, name):
    """A bash function's text from a script (NAME() { ... } to the first line that is '}')."""
    m = re.search(r"(?ms)^" + re.escape(name) + r"\(\) \{[^\n]*\n.*?^\}\n", rd(path))
    return m.group(0)


class DantePoolCheck(unittest.TestCase):
    """a6-stages.sh's dante_ok: only ONLINE or a plain 'no such pool' is OK; a hang or anything else isn't."""

    def run_case(self, zpool_out, zpool_rc, timeout_rc=None):
        with tempfile.TemporaryDirectory() as d:
            for n, body in (("zpool", f"#!/bin/sh\nprintf '%s\\n' {json.dumps(zpool_out)}\nexit {zpool_rc}\n"),
                            ("timeout", "#!/bin/sh\nshift\nexec \"$@\"\n" if timeout_rc is None else f"#!/bin/sh\nexit {timeout_rc}\n")):
                with open(os.path.join(d, n), "w") as f:
                    f.write(body)
                os.chmod(os.path.join(d, n), 0o755)
            fn = shell_function(os.path.join(TESTVM, "arm64", "a6-stages.sh"), "dante_ok")
            r = subprocess.run(["bash", "-c", fn + 'dante_ok; echo "rc=$? $DANTE_WHY"'], capture_output=True, text=True,
                               env=dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"]))
            return r.stdout.strip()

    def test_cases(self):
        self.assertEqual(self.run_case("ONLINE", 0), "rc=0 ONLINE")
        self.assertEqual(self.run_case("cannot open 'DantePool': no such pool", 1), "rc=0 not imported")
        self.assertTrue(self.run_case("SUSPENDED", 0).startswith("rc=1 health SUSPENDED"))
        self.assertTrue(self.run_case("DEGRADED", 0).startswith("rc=1 "))
        self.assertTrue(self.run_case("", 0, timeout_rc=124).startswith("rc=1 zpool list didn't answer"))
        self.assertTrue(self.run_case("/dev/zfs and /proc/self/mounts are required.", 1).startswith("rc=1 zpool list exit 1"))


class RescuerCleanup(unittest.TestCase):
    """make-rescuer.sh's arm64 VM goes however the script ends; a6-stages.sh stops and reports it."""

    def test_make_rescuer_traps_on_arm64_only(self):
        t = rd(TESTVM, "holttest1d", "make-rescuer.sh")
        m = re.search(r"(?ms)^if \[\[ \$ARCH == arm64 \]\]; then\n    trap 'down' EXIT\n    trap '[^\n]*QUICK=1;[^\n]*exit 143' TERM INT HUP\nfi$", t)
        self.assertIsNotNone(m, "the traps, under the arm64 test")
        self.assertEqual(len(re.findall(r"(?m)^\s*trap ", t)), 2, "no other trap (amd64 as before)")
        # The PID checked against its command line, by the copy's file name: vm-run resolves
        # /bkoscratch to /home/m/bko-arm64/scratch, so D's spelling isn't in it.
        self.assertIn('grep -qF "/$NAME.new.qcow2,"', t)
        # arm64's ssh runs in the background, waited for, so a stop's TERM isn't held by it.
        self.assertIn('SSH_PID=$!\n        wait "$SSH_PID" || rc=$?', t)
        self.assertNotIn('grep -qF "$D/$NAME.new.qcow2"', t)

    @unittest.skipUnless(os.path.exists("/proc/self/cmdline"), "needs /proc (run on Bigfoot)")
    def test_the_pid_is_matched_by_the_copys_name(self):
        # A stand-in "QEMU" whose command line names the copy by another spelling of its
        # directory (vm-run's resolved path), as on Bigfoot; and one that names another file.
        with tempfile.TemporaryDirectory() as d:
            pidf = os.path.join(d, "rescuer-arm64.qemu.pid")
            for arg, want in (("file=/home/m/bko-arm64/scratch/r/rescuer-arm64.new.qcow2,format=qcow2", True),
                              ("file=/elsewhere/other.qcow2,format=qcow2", False)):
                q = subprocess.Popen(["bash", "-c", "sleep 30; true", arg])   # (no exec: its argv stays)
                try:
                    with open(pidf, "w") as f:
                        f.write(f"{q.pid}\n")
                    mr = shell_function(os.path.join(TESTVM, "holttest1d", "make-rescuer.sh"), "qemu_pid")
                    st = shell_function(os.path.join(TESTVM, "arm64", "a6-stages.sh"), "rescuer_qemu")
                    a = subprocess.run(["bash", "-c", mr + f'D={d} NAME=rescuer-arm64; qemu_pid'], capture_output=True, text=True).stdout.strip()
                    b = subprocess.run(["bash", "-c", st + f'RESCUER_PIDF={pidf} RESCUER_NEW=/bkoscratch/r/rescuer-arm64.new.qcow2; rescuer_qemu'],
                                       capture_output=True, text=True).stdout.strip()
                    self.assertEqual((a, b), (str(q.pid), str(q.pid)) if want else ("", ""), arg)
                finally:
                    q.kill()
                    q.wait()

    def test_stages_know_the_rescuer_qemu(self):
        t = rd(TESTVM, "arm64", "a6-stages.sh")
        self.assertIn("RESCUER_PIDF=/bkoscratch/holttest1d/rescuer/rescuer-arm64.qemu.pid", t)
        self.assertIn('rescuer-arm64\\.new\\.qcow2" && bad+=', t)
        self.assertIn('p=$(rescuer_qemu); [[ -z $p ]] || bad+=', t)
        self.assertIn('if [[ -n $p && ${STAGE_NAME:-} == rescuer ]]; then', t)


@unittest.skipUnless(BASH4, "a6-stages.sh needs bash 4 (run on Bigfoot)")
class Stages(unittest.TestCase):
    def sel(self, *args):
        p = subprocess.run(["bash", os.path.join(TESTVM, "arm64", "a6-stages.sh"), "--dry-run", "--run", "/nonexistent/x1"] + list(args),
                           capture_output=True, text=True, timeout=120, env=dict(os.environ, HOME=tempfile.gettempdir()))
        on = re.findall(r"(?m)^-- (\S+)$", p.stdout)
        return p.returncode, on, p.stderr

    def test_everything_by_default(self):
        rc, on, _ = self.sel()
        self.assertEqual(rc, 0)
        listed = subprocess.run(["bash", os.path.join(TESTVM, "arm64", "a6-stages.sh"), "--list"], capture_output=True,
                                text=True).stdout.split()
        self.assertEqual(on, listed)
        self.assertEqual(on[0], "preflight")
        self.assertEqual(on[-1], "final")

    def test_groups_bring_what_they_need(self):
        self.assertEqual(self.sel("--only", "stic")[1], ["preflight"] + [f"stic:{g}" for g in
                         ("debian13-arm64", "ubuntu26-arm64", "fedora44-arm64", "alpine324-arm64", "debian13-zfs-arm64")] + ["final"])
        self.assertEqual(self.sel("--only", "holt:debian13-arm64")[1], ["preflight", "rescuer", "holt:debian13-arm64", "final"])
        self.assertEqual(self.sel("--only", "img")[1],
                         ["preflight", "imgpin", "holt:debian13-arm64+img", "holt:debian13-zfs-arm64+img", "final"])
        self.assertEqual(self.sel("--only", "holt:debian13-arm64", "--skip", "rescuer")[1], ["preflight", "holt:debian13-arm64", "final"])
        rc, on, _ = self.sel("--skip", "stic,1ARM,img,t2")
        self.assertEqual(on, ["preflight", "hosttest2:bin", "hosttest2:loop-linux", "hosttest2:loop-sparse", "hosttest2:facts", "final"])
        self.assertEqual(self.sel("--img-guests", "fedora44-arm64", "--only", "img")[1],
                         ["preflight", "imgpin", "holt:fedora44-arm64+img", "final"])

    def test_refusals(self):
        for a in (["--skip", "preflight"], ["--skip", "final"], ["--only", "nosuch"], ["--img-guests", "debian13"], ["--bogus"]):
            self.assertEqual(self.sel(*a)[0], 2, a)

    def test_a_value_option_last_is_a_usage_error(self):
        for o in ("--run", "--only", "--skip", "--bins", "--img", "--img-guests"):
            p = subprocess.run(["bash", os.path.join(TESTVM, "arm64", "a6-stages.sh"), "--list", o],
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(p.returncode, 2, o)
            self.assertIn("needs a value", p.stderr, o)
