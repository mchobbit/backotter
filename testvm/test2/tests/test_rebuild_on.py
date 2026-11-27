"""F12's tests (docs/design/img-speed.md §12 F12): testvm/rebuild-on, a guest's make-*.sh build on
any build host (its inputs copied from Chopin's /bkoscratch/rebuild-inputs, the host chosen by
pick-host), and its ship to Chopin for the vm-store publish; testvm/rebuild_on_host.py, its host
half; and rebuild-one.sh's two read-only modes, --inputs and --input-files.

No real host: a fixture farm, each host a directory reached by a fake ssh that runs the command
there (the nested host-to-host ssh too); pick-host, cmp-run.sh, rsync and vm-store are stand-ins.
Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTVM = os.path.dirname(os.path.dirname(HERE))
MAC = os.path.join(TESTVM, "rebuild-on")
ONE = os.path.join(TESTVM, "rebuild-one.sh")

FAKE_SSH = r"""#!/usr/bin/env bash
while [[ $# -gt 0 && $1 == -* ]]; do case $1 in -o) shift 2 ;; *) shift ;; esac; done
dest=$1; shift
name=${dest#*@}; name=${name%%.*}
root=$RB_FARM/$name
[[ -d $root ]] || { echo "ssh: no host $dest" >&2; exit 255; }
echo "$name $*" >>"$RB_FARM/ssh.log"
cd "$root/home" || exit 255
exec env HOME="$root/home" BKO_REBUILD_INPUTS="$root/inputs" BKO_REBUILD_LINK_MIN=1000 \
    BKO_CMP_RUN="$RB_BIN/cmp-run" RB_HOST=$name bash -c "$*"
"""
# cmp-run.sh TAG SLOTS GUEST=D%M with CMP_CMD="env REBUILD_DIR=DIR DIR/testvm/rebuild-one.sh": the
# job's outputs as rebuild-one.sh leaves them, rc from $HOME/job-rc (default 0).
FAKE_CMP = r"""#!/usr/bin/env bash
tag=$1 g=${3%%=*}
d=${CMP_CMD#env REBUILD_DIR=}; d=${d%% *}
echo "$WEAR_TAG|$CMP_CMD|$*" >>"$HOME/cmp-run.log"
rc=0; [[ -f $HOME/job-rc ]] && rc=$(cat "$HOME/job-rc")
w=$d/vms/$g; mkdir -p "$w" cmp-logs/$tag
[[ -f $d/testvm/rebuild-one.sh ]] || rc=98
echo "disk of $g on $RB_HOST" >"$w/$g.qcow2"; echo $rc >"$w/build.exit"
if [[ $rc = 0 ]]; then echo '{"guest": "'$g'", "boot": {"ssh": true}}' >"$w/rebuild-boot.json"; fi
ln -s /bkoscratch/rebuild-inputs/$g/big.iso "$w/big.iso"
echo "$g $rc $RB_HOST" >>cmp-logs/$tag/done
"""
FAKE_PICK = """#!/bin/sh
echo "$*" >>"$RB_FARM/pick.log"
echo m@cage.local
"""
# rsync -rlp ... -e SSH SRC/ HOST:DEST/ -> a copy into HOST's root.
FAKE_RSYNC = r"""#!/usr/bin/env bash
src=${@: -2:1} dst=${@: -1}
name=${dst%%:*}; name=${name#*@}; name=${name%%.*}; path=${dst#*:}
[[ $path == /* ]] || path=/home/$path
mkdir -p "$RB_FARM/$name$path" && cp -R "$src". "$RB_FARM/$name$path"
"""
FAKE_VMSTORE = """#!/bin/sh
echo "${VM_STORE_ROOT:-} $*" >>"$HOME/vm-store.log"
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
        self.d = tempfile.mkdtemp(prefix="t2-rebuild-on-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.farm = os.path.join(self.d, "farm")
        self.bin = os.path.join(self.d, "bin")
        for n, t in (("ssh", FAKE_SSH), ("cmp-run", FAKE_CMP), ("pick-host", FAKE_PICK), ("rsync", FAKE_RSYNC),
                     ("vm-store", FAKE_VMSTORE)):
            wr(os.path.join(self.bin, n), t, 0o755)
        for h in ("chopin", "cinqcent", "copland", "cage", "czerny"):
            os.makedirs(os.path.join(self.farm, h, "home", "backotter-work"))
        self.env = dict(os.environ, RB_FARM=self.farm, RB_BIN=self.bin, BKO_REBUILD_SSH=os.path.join(self.bin, "ssh"),
                        BKO_PICK_HOST=os.path.join(self.bin, "pick-host"), BKO_REBUILD_POLL="0.05",
                        BKO_REBUILD_VM_STORE=os.path.join(self.bin, "vm-store"), PYTHONDONTWRITEBYTECODE="1",
                        PATH=self.bin + os.pathsep + os.environ["PATH"])
        for k in ("BKO_REBUILD_SOURCE", "BKO_REBUILD_FLOOR_GIB", "BKO_REBUILD_STORE_ROOT"):
            self.env.pop(k, None)
        self.env["BKO_REBUILD_FLOOR_GIB"] = "0"

    def p(self, host, *rel):
        return os.path.join(self.farm, host, *rel)

    def run_(self, *args, env=None):
        return subprocess.run([MAC, *args], capture_output=True, text=True, timeout=120, env=env or self.env)

    def inputs(self, host, name, files):
        for rel, text in files.items():
            wr(self.p(host, "inputs", name, rel), text)

    def ssh_log(self):
        try:
            return rd(os.path.join(self.farm, "ssh.log"))
        except OSError:
            return ""


class RebuildOneModes(unittest.TestCase):
    """rebuild-one.sh --inputs and --input-files: read-only, from anywhere."""

    def inputs(self, g):
        r = subprocess.run(["bash", ONE, "--inputs", g], capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout.split("\n")[:-1], r.stderr

    def test_own_shared_and_used_folders(self):
        self.assertEqual(self.inputs("alpine324")[:2], (0, ["own alpine324"]))
        self.assertEqual(self.inputs("elementary8-luks")[1], ["own elementary8-luks", "own elementary8"])
        self.assertEqual(self.inputs("pikaos4-luks")[1], ["own pikaos4-luks", "uses pikaos4"])
        self.assertEqual(self.inputs("uc24-fde")[1], ["own uc24-fde", "uses ubuntu-core24"])
        self.assertEqual(self.inputs("proxmox9-mirror")[1], ["own proxmox9-mirror", "uses proxmox9"])
        self.assertEqual(self.inputs("slackware15-uefi")[1], ["own slackware15-uefi", "own slackware15"])

    def test_no_recipe(self):
        rc, out, err = self.inputs("no-such-guest")
        self.assertEqual(rc, 2)
        self.assertIn("no recipe for no-such-guest", err)

    def test_every_recipe_answers(self):
        text = rd(ONE)
        names = set()
        for m in re.finditer(r"^([a-z0-9][a-z0-9|.-]*)\)\s+cmd=", text, re.M):
            names.update(m.group(1).split("|"))
        self.assertGreater(len(names), 80)
        for g in sorted(names):
            rc, out, err = self.inputs(g)
            self.assertEqual(rc, 0, f"{g}: {err}")
            self.assertEqual(out[0], f"own {g}", g)
            for line in out:
                self.assertRegex(line, r"^(own|uses) [A-Za-z0-9._-]+$", g)
            # every rebuild-inputs folder the guest's lines name is listed
            for line in text.splitlines():
                if re.match(rf"^(\S+\|)*{re.escape(g)}(\|\S+)*\)", line):
                    for n in re.findall(r"/bkoscratch/rebuild-inputs/([A-Za-z0-9._-]+)", line):
                        self.assertTrue(f"own {n}" in out or f"uses {n}" in out, f"{g}: {n} not in {out}")

    def test_input_files_filter(self):
        d = tempfile.mkdtemp(prefix="t2-rebuild-one-")
        self.addCleanup(shutil.rmtree, d, True)
        for n in ("a.iso", "a.iso.sig", "key.gpg", "g.qcow2", "root-password", "id_ed25519", "build.log",
                  "image-state", "rebuild-build.json", "update.log", "openSUSE-MicroOS.x86_64.qcow2",
                  "half.tar", "half.tar.part"):
            wr(os.path.join(d, n), "x")
        os.makedirs(os.path.join(d, "sub"))
        os.symlink("a.iso", os.path.join(d, "link.iso"))
        r = subprocess.run(["bash", ONE, "--input-files", d], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(r.stdout.split()), ["a.iso", "a.iso.sig", "key.gpg", "link.iso",
                                                    "openSUSE-MicroOS.x86_64.qcow2"])
        r = subprocess.run(["bash", ONE, "--input-files", os.path.join(d, "none")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_build_mode_still_guarded(self):
        r = subprocess.run(["bash", ONE, "alpine324"], capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, REBUILD_DIR="/nonexistent-rebuild"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("run /nonexistent-rebuild/testvm/rebuild-one.sh", r.stderr)


class Inputs(Farm):
    """rebuild-on inputs GUEST --host H: Chopin's folders on H, checked by SHA-256."""

    def test_copies_own_folder_then_holds_it(self):
        self.inputs("chopin", "alpine324", {"root.tar.gz": "r" * 2000, "key.gpg": "k", "sub/x": "deep"})
        r = self.run_("inputs", "alpine324", "--host", "copland")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        for rel in ("root.tar.gz", "key.gpg", "sub/x"):
            self.assertEqual(rd(self.p("copland", "inputs", "alpine324", rel)),
                             rd(self.p("chopin", "inputs", "alpine324", rel)))
        self.assertIn("alpine324's inputs are m@chopin.local's (3 files, SHA-256 equal)", r.stdout)
        self.assertFalse([n for n in os.listdir(self.p("copland", "inputs")) if n.startswith(".")])
        before = self.ssh_log().count(" copy ")
        r = self.run_("inputs", "alpine324", "--host", "copland")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("held already, the same as m@chopin.local's (3 files)", r.stdout)
        self.assertEqual(self.ssh_log().count(" copy "), before)

    def test_big_twin_becomes_a_hard_link(self):
        iso = "i" * 5000
        self.inputs("chopin", "arch", {"arch.iso": iso, "sum": "a"})
        self.inputs("copland", "alpine324", {"arch.iso": iso, "small": "s"})
        r = self.run_("inputs", "arch", "--host", "copland")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.stat(self.p("copland", "inputs", "arch", "arch.iso")).st_ino,
                         os.stat(self.p("copland", "inputs", "alpine324", "arch.iso")).st_ino)
        self.assertIn("linked 1 file(s)", r.stdout)

    def test_a_folder_that_differs_is_refused_and_nothing_copied(self):
        self.inputs("chopin", "pikaos4", {"p.iso": "new", "p.sha": "1"})
        self.inputs("chopin", "pikaos4-luks", {"k": "1"})
        self.inputs("cage", "pikaos4", {"p.iso": "old", "extra": "e"})
        r = self.run_("inputs", "pikaos4-luks", "--host", "cage")
        self.assertEqual(r.returncode, 1)
        self.assertIn("other bytes: p.iso; missing: p.sha; only there: extra): move it aside", r.stderr)
        self.assertFalse(os.path.exists(self.p("cage", "inputs", "pikaos4-luks")))

    def test_own_folder_on_the_host_that_would_shadow_the_shared_one_is_refused(self):
        # Chopin builds elementary8-luks from elementary8 (it has no elementary8-luks); a host
        # holding an elementary8-luks would build from that instead, so it is refused.
        self.inputs("chopin", "elementary8", {"e.iso": "E"})
        self.inputs("cage", "elementary8-luks", {"e.iso": "stale"})
        r = self.run_("inputs", "elementary8-luks", "--host", "cage")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("/elementary8-luks would be elementary8-luks's inputs there, but m@chopin.local builds it "
                      "from elementary8 (it has no elementary8-luks): move it aside", r.stderr)
        self.assertFalse(os.path.exists(self.p("cage", "inputs", "elementary8")))
        r = self.run_("inputs", "elementary8-luks", "--host", "copland")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(self.p("copland", "inputs", "elementary8", "e.iso")), "E")

    def test_vms_folder_when_chopin_has_no_inputs_folder(self):
        v = self.p("chopin", "home", "backotter-work", "vms", "pikaos4-luks")
        for n, t in (("live.iso", "L" * 3000), ("key.asc", "k"), ("pikaos4-luks.qcow2", "disk"),
                     ("root-password", "pw"), ("id_ed25519", "key"), ("build.log", "log")):
            wr(os.path.join(v, n), t)
        wr(self.p("chopin", "inputs", "elsewhere", "real.iso"), "R" * 3000)
        os.symlink(self.p("chopin", "inputs", "elsewhere", "real.iso"), os.path.join(v, "linked.iso"))
        self.inputs("chopin", "pikaos4", {"p.iso": "P"})
        r = self.run_("inputs", "pikaos4-luks", "--host", "czerny")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = sorted(os.listdir(self.p("czerny", "inputs", "pikaos4-luks")))
        self.assertEqual(got, ["key.asc", "linked.iso", "live.iso"])
        self.assertFalse(os.path.islink(self.p("czerny", "inputs", "pikaos4-luks", "linked.iso")))
        self.assertEqual(rd(self.p("czerny", "inputs", "pikaos4-luks", "linked.iso")), "R" * 3000)
        self.assertEqual(rd(self.p("czerny", "inputs", "pikaos4", "p.iso")), "P")
        self.assertIn("(3 input files)", r.stdout)

    def test_missing_used_folder_on_chopin(self):
        self.inputs("chopin", "pikaos4-luks", {"k": "1"})
        r = self.run_("inputs", "pikaos4-luks", "--host", "cage")
        self.assertEqual(r.returncode, 1)
        self.assertIn("/inputs/pikaos4, which pikaos4-luks's recipe names", r.stderr)

    def test_neither_folder(self):
        r = self.run_("inputs", "alpine324", "--host", "cage")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no rebuild-inputs folder for alpine324 (alpine324) and no backotter-work/vms/alpine324",
                      r.stderr)

    def test_floor_refuses(self):
        self.inputs("chopin", "alpine324", {"a": "x"})
        r = self.run_("inputs", "alpine324", "--host", "cage", env=dict(self.env, BKO_REBUILD_FLOOR_GIB="1e12"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("under its floor", r.stderr)
        self.assertFalse(os.path.exists(self.p("cage", "inputs", "alpine324")))

    def test_dry_run_and_the_source_itself(self):
        self.inputs("chopin", "alpine324", {"a": "x"})
        r = self.run_("inputs", "alpine324", "--host", "cage", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would copy m@chopin.local:", r.stdout)
        self.assertFalse(os.path.exists(self.p("cage", "inputs", "alpine324")))
        r = self.run_("inputs", "alpine324", "--host", "chopin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("is the inputs' source: nothing to copy", r.stdout)


class Build(Farm):
    """rebuild-on build: the host (given or pick-host's), the inputs, the detached job, its exit."""

    def setUp(self):
        super().setUp()
        self.inputs("chopin", "alpine324", {"a": "x"})
        wr(self.p("cage", "home", "backotter-work", "testvm", "rebuild-one.sh"), "# the synced copy\n")

    def build(self, *args, env=None):
        return self.run_("build", "alpine324", "--reserve", "4", "4", "--dir", "rb", *args, env=env)

    def test_picked_host_builds_and_waits(self):
        r = self.build("--tag", "t1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        pick = rd(os.path.join(self.farm, "pick.log"))
        self.assertIn("--reserve 4 4 --hosts chopin,cinqcent,copland,cage,czerny", pick)
        self.assertNotIn("choubox", pick)
        self.assertIn("building on m@cage.local", r.stdout)
        self.assertIn("t1 on m@cage.local: exit 0", r.stdout)
        log = rd(self.p("cage", "home", "cmp-run.log"))
        d = self.p("cage", "home", "rb")
        self.assertEqual(log.strip(), f"rebuild:t1|env REBUILD_DIR={d} {d}/testvm/rebuild-one.sh|t1 1 alpine324=4%4")
        self.assertEqual(rd(os.path.join(d, "testvm", "rebuild-one.sh")), "# the synced copy\n")
        self.assertEqual(rd(os.path.join(d, "rebuild-on", "t1.exit")).strip(), "0")
        self.assertTrue(os.path.isfile(self.p("cage", "inputs", "alpine324", "a")))
        self.assertIn('"ok": true', r.stdout)

    def test_given_host_and_testvm_from_here(self):
        tv = os.path.join(self.d, "mytestvm")
        # The inputs are the ones that copy's rebuild-one.sh --inputs names (here one more
        # folder than the checkout's recipe), not the checkout's.
        mine = '# mine\n[ "$1" = --inputs ] && { echo "own $2"; echo "uses arch"; exit 0; }\n'
        wr(os.path.join(tv, "rebuild-one.sh"), mine)
        self.inputs("chopin", "arch", {"arch.iso": "A"})
        r = self.build("--host", "czerny", "--testvm", tv, "--tag", "t2")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.farm, "pick.log")))
        self.assertEqual(rd(self.p("czerny", "home", "rb", "testvm", "rebuild-one.sh")), mine)
        self.assertEqual(rd(self.p("czerny", "inputs", "arch", "arch.iso")), "A")
        self.assertTrue(os.path.isfile(self.p("czerny", "inputs", "alpine324", "a")))

    def test_failed_job_and_no_wait(self):
        wr(self.p("cage", "home", "job-rc"), "1\n")
        r = self.build("--tag", "t3", "--no-wait")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wait with: testvm/rebuild-on wait t3 --host m@cage.local --dir rb", r.stdout)
        r = self.run_("wait", "t3", "--host", "cage", "--dir", "rb")
        self.assertEqual(r.returncode, 1)
        self.assertIn("t3 on m@cage.local: exit 1", r.stdout)
        r = self.build("--tag", "t3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tag t3 has run in", r.stderr)

    def test_refused_while_a_rebuild_runs_from_dir(self):
        d = self.p("cage", "home", "rb")
        os.makedirs(d)
        fd = os.open(os.path.join(d, ".testvm.lock"), os.O_RDWR | os.O_CREAT)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_SH)
        r = self.build("--tag", "t4")
        self.assertEqual(r.returncode, 1)
        self.assertIn("a rebuild is running from", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(d, "testvm")))

    def test_usage(self):
        r = self.run_("build", "alpine324", "--reserve", "4", "x")
        self.assertEqual(r.returncode, 2)
        r = self.run_("build", "no-such", "--reserve", "4", "4", "--host", "cage")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no recipe for no-such", r.stderr)


class Ship(Farm):
    """rebuild-on ship: only a finished rebuild, to Chopin, disks compared; --publish there."""

    def setUp(self):
        super().setUp()
        self.w = self.p("copland", "home", "rb", "vms", "alpine324")
        wr(os.path.join(self.w, "alpine324.qcow2"), "the disk")
        wr(os.path.join(self.w, "build.exit"), "0\n")
        wr(os.path.join(self.w, "rebuild-boot.json"), '{"boot": {"ssh": true}}')
        wr(os.path.join(self.w, "key.gpg"), "k")
        os.symlink("/bkoscratch/rebuild-inputs/alpine324/big.iso", os.path.join(self.w, "big.iso"))
        self.to = "rbvms"

    def ship(self, *args, env=None):
        return self.run_("ship", "alpine324", "--host", "copland", "--dir", "rb", "--to", self.to, *args, env=env)

    def test_ship_and_publish(self):
        r = self.ship("--publish", "--no-relink", env=dict(self.env, BKO_REBUILD_STORE_ROOT="/priv/store"))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        dest = self.p("chopin", "home", "rbvms", "alpine324")
        self.assertEqual(rd(os.path.join(dest, "alpine324.qcow2")), "the disk")
        self.assertEqual(os.readlink(os.path.join(dest, "big.iso")), "/bkoscratch/rebuild-inputs/alpine324/big.iso")
        self.assertRegex(r.stdout, r"alpine324.qcow2: 0.0 GiB, sha256 [0-9a-f]{64} on m@copland.local and m@chopin.local")
        self.assertEqual(rd(self.p("chopin", "home", "vm-store.log")).strip(),
                         "/priv/store publish --from rbvms/alpine324 alpine324 --no-relink")
        r = self.ship()
        self.assertEqual(r.returncode, 1)
        self.assertIn("is there already: move it aside first", r.stderr)

    def test_without_publish_says_how(self):
        r = self.ship()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("publish with: testvm/vm-store publish --from", r.stdout)
        self.assertFalse(os.path.exists(self.p("chopin", "home", "vm-store.log")))

    def test_an_unfinished_rebuild_is_refused(self):
        wr(os.path.join(self.w, "build.exit"), "1\n")
        os.remove(os.path.join(self.w, "rebuild-boot.json"))
        r = self.ship("--publish")
        self.assertEqual(r.returncode, 1)
        self.assertIn("is no finished rebuild: build.exit is '1', not 0; no rebuild-boot.json", r.stderr)
        self.assertFalse(os.path.exists(self.p("chopin", "home", "rbvms")))

    def test_a_failed_boot_is_refused(self):
        wr(os.path.join(self.w, "rebuild-boot.json"), '{"boot": {"ssh": false, "error": "timeout"}}')
        r = self.ship()
        self.assertEqual(r.returncode, 1)
        self.assertIn("its boot check failed", r.stderr)

    def test_usage(self):
        r = self.run_("ship", "alpine324", "--host", "copland", "--set-latest")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
