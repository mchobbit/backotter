"""BackShot1 keeps the Thinkpad awake: bs1-common's hold_awake (a logind
block inhibitor for as long as the calling script runs) and bs1-adopt's
sleep.conf drop-in (no_sleep, and --sleep-on's sleep_on).  GNOME's idle
auto-suspend took the machine down during a phase 1 (Fedora 45,
2026-11-20).  No root, no logind: a fake systemd-inhibit, and the drop-in
in a temporary directory."""

import configparser
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
BS1 = TESTS.parent.parent / "realworld" / "backshot1"


# These functions need nothing of bash 4: macOS's 3.2 runs them too.
BASH = shutil.which("bash")


def functions(path, *names):
    """The named functions' text from a bash file (each from `NAME() {` to
    the first `}` alone on a line)."""
    text = Path(path).read_text()
    out = []
    for n in names:
        m = re.search(rf"^{re.escape(n)}\(\) {{\n.*?^}}\n", text, re.M | re.S)
        assert m, f"no function {n} in {path}"
        out.append(m.group(0))
    return "\n".join(out)


def assignment(path, name):
    """A top-level NAME=... line from a bash file."""
    m = re.search(rf"^{re.escape(name)}=.*$", Path(path).read_text(), re.M)
    assert m, f"no {name}= in {path}"
    return m.group(0)


def exe(path, text):
    Path(path).write_text(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie of ours would still answer: none here, the fake isn't our child.
    return True


@unittest.skipUnless(BASH, "needs bash")
class HoldAwake(unittest.TestCase):
    """hold_awake: an inhibitor for sleep, idle and the lid switch, held by a
    child that outlives nothing and holds none of the script's pipes."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-awake-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        # Only these tools: a real systemd-inhibit on the test host mustn't show.
        self.bin = self.t / "bin"
        self.bin.mkdir()
        for tool in ("sh", "grep", "sleep", "cat"):
            os.symlink(shutil.which(tool), self.bin / tool)

    def fake(self, name="systemd-inhibit", list_rc=0, take_rc=None):
        """A fake inhibitor: --list prints the PIDs that took one (its own
        PID, as logind lists the client); otherwise it records its argv and
        runs the command after the options, unless take_rc says it refuses."""
        refuse = f"exit {take_rc}" if take_rc is not None else ""
        exe(self.bin / name, f"""#!/bin/sh
if [ "$1" = --list ]; then
    [ {list_rc} -eq 0 ] || exit {list_rc}
    echo "WHO UID USER PID COMM WHAT WHY MODE"
    cat "{self.t}/held" 2>/dev/null
    exit 0
fi
printf '%s\\n' "$@" >"{self.t}/args"
{refuse}
echo "BackShot1 0 root $$ {name} sleep:idle:handle-lid-switch why block" >>"{self.t}/held"
echo $$ >"{self.t}/pid"
while [ $# -gt 0 ]; do case $1 in --*) shift ;; *) break ;; esac; done
exec "$@"
""")

    def run_hold(self, after="", timeout=8):
        lib = functions(BS1 / "bs1-common", "hold_awake")
        script = f'say() {{ echo "$*"; }}\n{lib}\nhold_awake "the why"\necho "rc $?"\n{after}\n'
        t0 = time.monotonic()
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=timeout,
                           env={"PATH": str(self.bin), "LC_ALL": "C"})
        return r, time.monotonic() - t0

    def test_takes_the_inhibitor_for_sleep_idle_and_the_lid(self):
        self.fake()
        r, took = self.run_hold(after=f'kill -0 "$(cat {self.t}/pid)" && echo "held while running"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rc 0", r.stdout)
        self.assertIn("holding sleep, idle and the lid switch off", r.stdout)
        self.assertIn("held while running", r.stdout)
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual(args[:4], ["--what=sleep:idle:handle-lid-switch", "--mode=block", "--who=BackShot1", "--why=the why"])
        # The holder has its own descriptors: the script's pipes closed at once.
        self.assertLess(took, 4, "the holder kept the script's output open")

    def test_the_inhibitor_goes_with_the_script(self):
        self.fake()
        # Long enough for the holder to be in its loop when the script ends.
        r, _ = self.run_hold(after=f'sleep 1; kill -0 "$(cat {self.t}/pid)" && echo "held while running"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("held while running", r.stdout)
        pid = int((self.t / "pid").read_text())
        end = time.monotonic() + 12
        while alive(pid) and time.monotonic() < end:
            time.sleep(0.2)
        self.assertFalse(alive(pid), "the inhibitor outlived the script")

    def test_elogind_inhibit(self):
        self.fake("elogind-inhibit")
        r, _ = self.run_hold()
        self.assertIn("holding sleep, idle and the lid switch off", r.stdout)
        self.assertIn("elogind-inhibit", r.stdout)

    def test_no_inhibitor_says_so_and_carries_on(self):
        r, _ = self.run_hold()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rc 0", r.stdout)
        self.assertIn("nothing holds sleep off", r.stdout)

    def test_no_logind_says_so_and_takes_nothing(self):
        self.fake(list_rc=1)
        r, _ = self.run_hold()
        self.assertIn("rc 0", r.stdout)
        self.assertIn("can't reach logind", r.stdout)
        self.assertFalse((self.t / "args").exists())

    def test_a_refused_inhibitor_is_a_warning(self):
        self.fake(take_rc=1)
        r, _ = self.run_hold()
        self.assertIn("rc 0", r.stdout)
        self.assertIn("WARNING", r.stdout)
        self.assertNotIn("holding", r.stdout)

    def test_backup_and_verify_take_it(self):
        for script in ("bs1-backup", "bs1-verify"):
            self.assertRegex((BS1 / script).read_text(), r"(?m)^hold_awake ", script)


@unittest.skipUnless(BASH, "needs bash")
class NoSleep(unittest.TestCase):
    """bs1-adopt's drop-in: sleep.conf's Allow*=no under systemd or elogind,
    once; --sleep-on removes only BackShot1's."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-nosleep-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.sd = self.t / "etc/systemd/sleep.conf.d/90-backshot1.conf"
        self.el = self.t / "etc/elogind/sleep.conf.d/90-backshot1.conf"

    def adopt(self, call, init="systemd"):
        lib = functions(BS1 / "bs1-adopt", "no_sleep", "sleep_on")
        drop = assignment(BS1 / "bs1-adopt", "SLEEP_DROPINS")
        head = assignment(BS1 / "bs1-adopt", "SLEEP_HEAD")
        # The real paths, under the test's root.
        real = drop.split("=", 1)[1].strip('"').split()
        self.assertEqual(real, ["/etc/systemd/sleep.conf.d/90-backshot1.conf", "/etc/elogind/sleep.conf.d/90-backshot1.conf"])
        script = f'set -euo pipefail\n{head}\nSLEEP_DROPINS="{self.sd} {self.el}"\ninit={init}\n{lib}\n{call}\n'
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=30)
        return r

    def sleep_section(self, f):
        c = configparser.ConfigParser()
        c.optionxform = str
        c.read_string(f.read_text())
        return dict(c["Sleep"])

    def test_systemd_disallows_every_sleep(self):
        r = self.adopt("no_sleep")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"wrote {self.sd}", r.stdout)
        self.assertEqual(self.sleep_section(self.sd), {"AllowSuspend": "no", "AllowHibernation": "no",
                                                       "AllowSuspendThenHibernate": "no", "AllowHybridSleep": "no"})
        self.assertTrue(self.sd.read_text().startswith("# BackShot1 (bs1-adopt)"))
        self.assertFalse(self.el.exists())

    def test_again_changes_nothing(self):
        self.adopt("no_sleep")
        before = self.sd.stat().st_mtime_ns, self.sd.read_text()
        r = self.adopt("no_sleep")
        self.assertIn("unchanged", r.stdout)
        self.assertEqual((self.sd.stat().st_mtime_ns, self.sd.read_text()), before)

    def test_elogind(self):
        (self.t / "etc/elogind").mkdir(parents=True)
        r = self.adopt("no_sleep", init="openrc")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sleep_section(self.el)["AllowSuspend"], "no")
        self.assertFalse(self.sd.exists())

    def test_no_logind_writes_nothing(self):
        r = self.adopt("no_sleep", init="runit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no logind", r.stdout)
        self.assertFalse((self.t / "etc").exists())

    def test_sleep_on_removes_it(self):
        self.adopt("no_sleep")
        r = self.adopt("sleep_on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"removed {self.sd}", r.stdout)
        self.assertFalse(self.sd.parent.exists(), "the empty sleep.conf.d stays")
        r = self.adopt("sleep_on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to remove", r.stdout)

    def test_sleep_on_keeps_a_file_not_ours(self):
        self.sd.parent.mkdir(parents=True)
        self.sd.write_text("[Sleep]\nAllowSuspend=no\n")
        r = self.adopt("sleep_on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("isn't BackShot1's", r.stderr)
        self.assertTrue(self.sd.exists())


if __name__ == "__main__":
    unittest.main()
