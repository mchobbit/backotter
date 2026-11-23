"""The runners on hosts that can't run everything (gaps P2, P5, P6, the runner side):
cmp-run.sh --ceiling (read-only), ht1-queue.sh's per-host skip set (a line over this host's
ceiling, a guest whose image is missing here, a ZFS source on a host without zpool, a Linux
line without the rescuer or the debs, a MOK guest on a host without sbverify), its --pop =LINE entries and --putback, a refused
--reserve that never counts as a quick failure, and ht1.sh refusing a missing rescuer or debs
instead of falling back to the debian13 guest.  drive.py's side (its probe's VM lines, pick's
novm) is drive.py --self-test's, run here too.  No host needed: the parts that need bash 4
and flock (the test hosts') are skipped without them."""

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(TEST2))
sys.path.insert(0, HERE)

from test_holt import modern_bash  # noqa: E402

TESTVM = os.path.join(REPO, "testvm")
HT = os.path.join(TESTVM, "holttest1d")
QUEUE = os.path.join(HT, "ht1-queue.sh")
CMP_RUN = os.path.join(TESTVM, "compare", "cmp-run.sh")
BASH = shutil.which("bash")


def rd(p):
    with open(p) as f:
        return f.read()


def wr(p, text, mode=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    if mode:
        os.chmod(p, mode)


def shell_funcs(path, *names):
    """The definitions of NAMES in a shell script, as text to eval: a one-line function by its
    line, else from its first line to the next line that is a lone '}'."""
    lines = rd(path).splitlines()
    out = []
    for n in names:
        i = next((i for i, l in enumerate(lines) if l.startswith(n + "() {") or l.startswith(n + "() {  #")), None)
        if i is None:
            raise AssertionError(f"no function {n} in {path}")
        if lines[i].count("{") == lines[i].count("}"):
            out.append(lines[i])
            continue
        j = next(j for j in range(i + 1, len(lines)) if lines[j] == "}")
        out.extend(lines[i:j + 1])
    return "\n".join(out) + "\n"


def shell_line(path, prefix):
    """The first line of PATH that starts with PREFIX (an assignment, say)."""
    return next(l for l in rd(path).splitlines() if l.startswith(prefix)) + "\n"


@unittest.skipUnless(BASH, "no bash")
class CmpRunCeiling(unittest.TestCase):
    """cmp-run.sh --ceiling prints ceiling()'s "DISK MEM" and writes nothing, not even the
    ledger's files; it is the very function facts_host.py and drive.py read out of the script."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-ceiling-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.bin = os.path.join(self.t, "bin")
        # A 30 GiB tmpfs at /mnt/bko-ram (df -Pk's KiB), and mountpoint saying it is one.
        wr(os.path.join(self.bin, "mountpoint"), "#!/bin/sh\nexit 0\n", 0o755)
        wr(os.path.join(self.bin, "df"), "#!/bin/sh\necho 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
                                        "echo 'tmpfs 31457280 0 31457280 0% /mnt/bko-ram'\n", 0o755)
        os.makedirs(os.path.join(self.t, "backotter-work"))
        self.ledger = os.path.join(self.t, "ledger")
        os.makedirs(self.ledger)
        self.env = dict(os.environ, HOME=self.t, PATH=self.bin + os.pathsep + os.environ["PATH"],
                        CMP_LEDGER_DIR=self.ledger)

    def run_cmp(self, *args):
        return subprocess.run([BASH, CMP_RUN] + list(args), capture_output=True, text=True, env=self.env, timeout=60)

    def test_prints_the_ceiling_and_writes_nothing(self):
        p = self.run_cmp("--ceiling")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertRegex(p.stdout, r"^[0-9]+\.[0-9] -?[0-9]+\.[0-9]\n$")
        self.assertEqual(p.stdout.split()[0], "29.0")   # the tmpfs less 1 GiB
        self.assertEqual(os.listdir(self.ledger), [], "--ceiling made the ledger's files")
        same = subprocess.run([BASH, "-c", 'eval "$(sed -n "/^ceiling()/,/^}/p" "$1")" && RAM=$2 && ceiling', "-",
                               CMP_RUN, "/mnt/bko-ram"], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(p.stdout, same.stdout, "not ceiling() itself (facts_host.py's CEILING_SH)")

    def test_usage(self):
        p = self.run_cmp("--ceiling", "extra")
        self.assertEqual(p.returncode, 2)
        self.assertIn("usage: cmp-run.sh --ceiling", p.stderr)
        self.assertEqual(os.listdir(self.ledger), [])

    def test_no_ram_disk(self):
        wr(os.path.join(self.bin, "mountpoint"), "#!/bin/sh\nexit 1\n", 0o755)
        p = self.run_cmp("--ceiling")
        self.assertEqual((p.returncode, p.stdout), (1, ""))

    def test_the_ceiling_function_is_still_one_sed_range(self):
        """facts_host.py and drive.py's status read it with sed /^ceiling()/,/^}/."""
        body = subprocess.run(["sed", "-n", "/^ceiling()/,/^}/p", CMP_RUN], capture_output=True, text=True).stdout
        self.assertTrue(body.startswith("ceiling() {") and body.rstrip().endswith("}") and "--ceiling" not in body)


@unittest.skipUnless(BASH, "no bash")
class QueueCantWhy(unittest.TestCase):
    """ht1-queue.sh's cant_why: why this host can never run a todo line (bash 3.2 does)."""

    FUNCS = ("guest_arch", "has_image", "needs_zfs", "has_debs", "has_mok", "ceil_now", "over_ceil", "fits_ceil", "cant_why")

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-cantwhy-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.w, self.l = os.path.join(self.t, "w"), os.path.join(self.t, "l")
        for g in ("debian13", "debian13-zfs", "freebsd15", "freebsd15-ufs", "freebsd14", "netbsd10", "redox", "mint22"):
            wr(os.path.join(self.w, "vms", g, g + ".qcow2"), "")
        os.makedirs(os.path.join(self.w, "vms", "gentoo"))           # a cache entry without its image
        os.symlink(os.path.join(self.t, "evicted.qcow2"), os.path.join(self.w, "vms", "gentoo", "gentoo.qcow2"))
        wr(os.path.join(self.t, "cached.qcow2"), "")
        os.makedirs(os.path.join(self.w, "vms", "kinoite44"))         # a link into the cache that resolves
        os.symlink(os.path.join(self.t, "cached.qcow2"), os.path.join(self.w, "vms", "kinoite44", "kinoite44.qcow2"))
        for g, opts in (("anduinos2", "--uefi --mok\n"), ("solus49", "--mok --uefi"), ("leap16", "--uefi\n"),
                        ("mokless", "--uefi --mokutil --disk x--mok.qcow2\n")):
            wr(os.path.join(self.w, "vms", g, g + ".qcow2"), "")
            wr(os.path.join(self.w, "vms", g, "vm-run-options"), opts)
        self.rescuer(True)
        self.debs(True)

    def rescuer(self, there):
        p = os.path.join(self.l, "rescuer", "rescuer.qcow2")
        if there:
            wr(p, "")
        elif os.path.exists(p):
            os.remove(p)

    def debs(self, there):
        for n in ("f2fs-tools_1.16_amd64.deb", "jfsutils_1.1_amd64.deb"):
            p = os.path.join(self.l, "bin", n)
            if there:
                wr(p, "")
            elif os.path.exists(p):
                os.remove(p)

    def why(self, nm, d="6", m="4", over=None, ceil="9.0 9.2", zfs=0, rimg="", arch="amd64", sbv=1):
        g = re.sub(r"[+=%].*", "", nm)
        v = nm.split("+", 1)[1] if "+" in nm else ""
        script = (f"W={self.w} L={self.l} CEIL='{ceil}' HOSTZFS={zfs} HOSTSBV={sbv} HOSTARCH={arch}; zfs_list=(debian13-zfs ubuntu24)\n"
                  + (f"HT_RESCUER_IMG={rimg}\n" if rimg else "unset HT_RESCUER_IMG\n")
                  + shell_funcs(QUEUE, *self.FUNCS)
                  + f"cant_why {nm} {g} '{v}' '{d}' '{m}' {over if over is not None else ''}; echo \"rc=$? $WHYNOT\"\n")
        p = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.stderr, "")
        rc, _, w = p.stdout.strip().partition(" ")
        return w if rc == "rc=0" else None

    def test_runnable(self):
        for nm in ("debian13", "freebsd15", "freebsd14", "freebsd14+img", "redox", "mint22+bigger", "kinoite44"):
            self.assertIsNone(self.why(nm), nm)

    def test_arch(self):
        """A guest of another architecture than the host's (uname -m) is never run there: an -arm64
        guest on an amd64 host, any other on an arm64 one (hosts.py's guest_arch), before its image."""
        wr(os.path.join(self.w, "vms", "debian13-arm64", "debian13-arm64.qcow2"), "")
        self.assertEqual(self.why("debian13-arm64"), "an arm64 guest, and this host is amd64")
        self.assertEqual(self.why("debian13-arm64+img"), "an arm64 guest, and this host is amd64")
        # (an arm64 guest's rescuer is rescuer-arm64.qcow2, make-rescuer.sh --arch arm64's, and it takes no debs: A6)
        self.assertEqual(self.why("debian13-arm64", arch="arm64"),
                         f"no rescuer {os.path.join(self.l, 'rescuer', 'rescuer-arm64.qcow2')} here")
        wr(os.path.join(self.l, "rescuer", "rescuer-arm64.qcow2"), "")
        self.debs(False)
        self.assertIsNone(self.why("debian13-arm64", arch="arm64"))
        self.debs(True)
        self.assertEqual(self.why("debian13", arch="arm64"), "an amd64 guest, and this host is arm64")
        self.assertEqual(self.why("chimera", arch="arm64"), "an amd64 guest, and this host is arm64")   # no image either
        q = rd(QUEUE)
        self.assertIn('HOSTARCH=amd64; [[ $(uname -m) == aarch64 ]] && HOSTARCH=arm64', q)

    def test_no_rescuer_guest(self):
        """A BSD, illumos or Haiku line needs its rescuer guest's image too (ht1-bsd.sh's RG, from
        the same vms/; +img's backup and verify boot it as well): else the whole guest is skipped."""
        os.remove(os.path.join(self.w, "vms", "freebsd15-ufs", "freebsd15-ufs.qcow2"))
        w = f"no {self.w}/vms/freebsd15-ufs/*.qcow2 here (rescuer of freebsd14)"
        self.assertEqual(self.why("freebsd14"), w)
        self.assertEqual(self.why("freebsd14+img"), w)
        self.assertEqual(self.why("netbsd10"), f"no {self.w}/vms/netbsd11/*.qcow2 here (rescuer of netbsd10)")
        self.assertEqual(self.why("haiku6"), f"no {self.w}/vms/haiku6/*.qcow2 here")   # its own image first
        self.assertIsNone(self.why("debian13"))   # a Linux line has none

    def test_the_rescuer_guests_are_ht1_bsds(self):
        """cant_why's rescuer guests are ht1-bsd.sh's RG, pattern by pattern."""
        bsd = set(re.findall(r"^    ([a-z0-9*|]+)\)\n\s+(?:#.*\n\s+)*PLAT=\S+ RG=(\S+)", rd(os.path.join(HT, "ht1-bsd.sh")), re.M))
        q = set(re.findall(r"^    ([a-z0-9*|]+)\) rg=(\S+) ;;", rd(QUEUE), re.M))
        self.assertEqual(len(bsd), 6, bsd)
        self.assertEqual(q, bsd)

    def test_ht_rescuer_img(self):
        """HT_RESCUER_IMG stands in for the rescuer, as in ht1.sh and ht1-offline.sh."""
        self.rescuer(False)
        other = os.path.join(self.t, "plain.qcow2")
        wr(other, "")
        self.assertIsNone(self.why("debian13", rimg=other))
        self.assertIsNone(self.why("redox", rimg=other))
        self.assertEqual(self.why("debian13", rimg=other + ".x"), f"no rescuer {other}.x here")
        self.rescuer(True)
        self.assertEqual(self.why("debian13", rimg=other + ".x"), f"no rescuer {other}.x here")   # it, not L's

    def test_no_image(self):
        self.assertEqual(self.why("gentoo"), f"no {self.w}/vms/gentoo/*.qcow2 here")   # a link that doesn't resolve
        self.assertEqual(self.why("chimera+img"), f"no {self.w}/vms/chimera/*.qcow2 here")

    def test_rescuer_and_debs(self):
        self.rescuer(False)
        self.assertEqual(self.why("debian13"), f"no rescuer {self.l}/rescuer/rescuer.qcow2 here")
        self.assertEqual(self.why("redox"), f"no rescuer {self.l}/rescuer/rescuer.qcow2 here")   # ht1-offline.sh's too
        self.assertIsNone(self.why("debian13+img"))   # the pinned image is its rescuer
        self.assertIsNone(self.why("freebsd15"))      # rescued by a BSD guest
        self.rescuer(True)
        self.debs(False)
        self.assertEqual(self.why("debian13"), f"no f2fs-tools and jfsutils debs in {self.l}/bin here")
        self.assertIsNone(self.why("redox"))          # ht1-offline.sh copies no debs
        self.assertIsNone(self.why("debian13+img"))

    def test_zfs(self):
        w = "needs OpenZFS here (NEEDS_ZFS), and this host has no zpool"
        self.assertEqual(self.why("debian13-zfs"), w)
        self.assertEqual(self.why("debian13-zfs+refuse"), w)   # every variant, as placement keeps them
        self.assertIsNone(self.why("debian13-zfs", zfs=1))
        self.assertIsNone(self.why("debian13"))

    def test_mok(self):
        """A guest whose vm-run-options enroll a MOK (--mok, ht1.sh's mok) is left for the others,
        every variant, on a host without sbverify (sbsigntool): ht1.sh's verify checks the restored
        GRUB's signature with it, and without it gave bko-holt a FAIL for the host's missing tool
        (choubox, t2h-20261123T1801-d49a, holt:anduinos2+reformat-ext4)."""
        w = ("boots with a MOK (vm-run-options --mok), and this host has no sbverify "
             "(apt-get install -y sbsigntool) to check the restored GRUB's signature with")
        for nm in ("anduinos2", "anduinos2+reformat-ext4", "anduinos2+img", "solus49"):   # (no newline at solus49's end)
            self.assertEqual(self.why(nm, sbv=0), w, nm)
            self.assertIsNone(self.why(nm), nm)
        for nm in ("leap16", "mokless", "debian13", "freebsd15", "redox"):   # Secure Boot without a MOK; a word like it; no options
            self.assertIsNone(self.why(nm, sbv=0), nm)
        self.rescuer(False)   # (the rescuer first, as for any Linux line)
        self.assertEqual(self.why("anduinos2", sbv=0), f"no rescuer {self.l}/rescuer/rescuer.qcow2 here")

    def test_mok_is_ht1s(self):
        """has_mok reads vm-run-options as ht1.sh does (its first line's words, --mok among the
        firmware options), HOSTSBV is sbverify where root's PATH has it, and the runner's start
        line says which."""
        q, h = rd(QUEUE), rd(os.path.join(HT, "ht1.sh"))
        self.assertIn('read -ra o < "$W/vms/$G/vm-run-options"', h)
        self.assertIn('mok=0; [[ " ${fw[*]} " == *" --mok "* ]] && mok=1', h)
        self.assertIn('sbverify --cert "$pem" "$f"', h)
        self.assertIn('read -ra o < "$W/vms/$1/vm-run-options"; [[ " ${o[*]-} " == *" --mok "* ]]', q)
        self.assertRegex(q, r"(?m)^HOSTSBV=0\nfor p in (/\S+/sbverify ?){6}; do \[\[ -f \$p && -x \$p \]\] && HOSTSBV=1; done$")
        self.assertIn('sbverify $( ((HOSTSBV)) && echo yes || echo "no (the MOK guests are left for others', q)

    def test_ceiling(self):
        self.assertEqual(self.why("mint22", "17", "4"), "--reserve 17 4 over this host's ceiling 9.0 9.2")
        self.assertEqual(self.why("mint22", "6", "9.5"), "--reserve 6 9.5 over this host's ceiling 9.0 9.2")
        self.assertIsNone(self.why("mint22", "9", "9.2"))       # at the ceiling fits
        self.assertIsNone(self.why("mint22", "17", "4", ceil=""))   # not known: not held to it
        self.assertIsNone(self.why("mint22", "17", "4", over=0))   # cant_scan's own answer counts
        self.assertIsNotNone(self.why("mint22", "6", "4", over=1))

    def test_the_drivers_are_ht1s(self):
        """cant_why's case is ht1.sh's: the same guests go to ht1-bsd.sh and ht1-offline.sh."""
        q, h = rd(QUEUE), rd(os.path.join(HT, "ht1.sh"))
        for pat in (r"freebsd\*\|hardenedbsd\*\|ghostbsd\*\|netbsd\*\|alpinebsd\*\|openbsd\*\|dragonfly\*\|omnios\*\|oi\*\|haiku\*\)",
                    r"redox\|sculpt\*\|qubes\*\)"):     # (Qubes dom0 read offline: docs/design/qubes.md)
            self.assertRegex(q, pat)
            self.assertRegex(h, pat)

    def test_needs_zfs_is_classes_json(self):
        """NEEDS_ZFS's default is Test2's classes.json holt.needs_host_zfs (placement's list)."""
        script = shell_line(QUEUE, "HERE=") + shell_funcs(QUEUE, "zfs_guests") + "zfs_guests\n"
        p = subprocess.run([BASH, "-c", script, QUEUE], capture_output=True, text=True, timeout=60)
        import json
        want = json.loads(rd(os.path.join(TEST2, "classes.json")))["holt"].get("needs_host_zfs") or []
        self.assertEqual(p.stdout.split(), want)


@unittest.skipUnless(BASH, "no bash")
class QueueLaunchBackstop(unittest.TestCase):
    """launch()'s backstop: a line cant_why says this host can never run goes back to the
    todo; one that can't go back gets a done row (rc 1, run_s -), as reap's refused one, so it
    is never lost silently (bash 3.2 does)."""

    def run_launch(self, back_rc, why):
        t = tempfile.mkdtemp(prefix="t2-launch-")
        self.addCleanup(shutil.rmtree, t, True)
        script = (f"Q={t} W=/w H=copland RAM=1 DISK_OVER=0 MODE=ram CEIL='9.0 9.2' NEWPORT=3081 BROKE=''; CANT=()\n"
                  "say() { echo \"SAY $*\"; }\n"
                  "cant_why() { WHYNOT=$T_WHY; return 0; }\n"
                  f"give_back() {{ echo \"BACK $1: $2\"; return {back_rc}; }}\n"
                  + shell_line(QUEUE, "NUM_RE=") + shell_funcs(QUEUE, "ram_room", "place_lines", "place", "done_row", "launch")
                  + "launch freebsd14+img=6%4; echo \"rc=$? cant=${CANT[*]} broke=$BROKE\"\n")
        p = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, T_WHY=why))
        self.assertEqual(p.stderr, "")
        done = os.path.join(t, "done")
        return p.stdout.splitlines(), (rd(done).splitlines() if os.path.exists(done) else [])

    def test_back_to_the_todo(self):
        out, done = self.run_launch(0, "no /w/vms/freebsd15-ufs/*.qcow2 here (rescuer of freebsd14)")
        self.assertIn("BACK freebsd14+img=6%4: not started here: no /w/vms/freebsd15-ufs/*.qcow2 here (rescuer of freebsd14)", out)
        self.assertEqual(out[-1], "rc=1 cant=freebsd14 broke=")   # the whole guest from now on
        self.assertEqual(done, [])

    def test_stays_taken_gets_a_done_row(self):
        out, done = self.run_launch(1, "--reserve 6 4 over this host's ceiling 9.0 9.2")
        self.assertEqual(out[-1], "rc=1 cant==freebsd14+img=6%4 broke=")   # that line only
        self.assertEqual(done[0].split("\t"), ["end_utc", "host", "gv", "rc", "d_gib", "m_gib", "port", "wait_s", "run_s"])
        self.assertEqual(done[1].split("\t")[1:], ["copland", "freebsd14-img", "1", "6", "4", "3081", "0", "-"])
        self.assertEqual(len(done), 2)


@unittest.skipUnless(BASH, "no bash")
class QueueLaunchPlace(unittest.TestCase):
    """launch() itself, to the job it starts: RAM first, the SSD for overflow (the user's rule,
    2026-11-23).  In mode overflow a line works in the RAM disk whenever its D fits there right
    now, whatever its size (--reserve D M, HT_RAM=1); on /bkoscratch (--reserve-ssd D M,
    HT_RAM=0) only when the RAM disk has no room for it now, or its D is over this host's
    RAM-disk ceiling.  cmp-run.sh is a stand-in that answers --ssd-free and --free as told and
    writes down what a job was started with; setsid one that runs its command."""

    def launch(self, line, mode="overflow", ceil="29.0 23.2", ssd="184.0", after="29.0 23.2", disk_over=None, back_rc=0,
               rs=1, ss=1):
        t = tempfile.mkdtemp(prefix="t2-place-")
        self.addCleanup(shutil.rmtree, t, True)
        os.makedirs(os.path.join(t, "bin"))
        os.makedirs(os.path.join(t, "l"))
        got = os.path.join(t, "job")
        for name, text in (("setsid", 'exec "$@"\n'),
                           ("cmp-run.sh", "case $1 in\n"
                                          f"--ssd-free) {'echo ' + ssd if ssd is not None else 'exit 1'} ;;\n"
                                          f"--free) echo '9.0 10.1'; echo 'waiting 0'; echo 'after {after}' ;;\n"
                                          f"*) echo \"HT_RAM=$HT_RAM WEAR_TAG=$WEAR_TAG HT_VARIANT=$HT_VARIANT $*\" > {got} ;;\n"
                                          "esac\n")):
            with open(os.path.join(t, "bin", name), "w") as f:
                f.write("#!/bin/sh\n" + text)
            os.chmod(os.path.join(t, "bin", name), 0o755)
        over = (0 if mode == "ram" else 1) if disk_over is None else disk_over
        script = (f"Q={t} L={t}/l W=/w H=cinqcent HERE=/copy/testvm/holttest1d RUN=r1 RAM=1 DISK_OVER={over} MODE={mode} "
                  f"CEIL='{ceil}' CMP_RUN={t}/bin/cmp-run.sh NEWPORT=2600 NEWLK='' BROKE='' t=40 f=200 RSFREE={rs} SSFREE={ss}; CANT=()\n"
                  "say() { echo \"SAY $*\"; }\n"
                  "close_locks() { :; }\n"
                  "cant_why() { [ \"$3\" = img ] && [ \"${4%.*}\" -gt \"${CEIL%%.*}\" ] && { WHYNOT=over; return 0; }; return 1; }\n"
                  f"give_back() {{ echo \"BACK $1: $2\"; return {back_rc}; }}\n"
                  + shell_line(QUEUE, "NUM_RE=") + shell_funcs(QUEUE, "ram_room", "place_lines", "place", "done_row", "launch")
                  + f"launch '{line}'; rc=$?; wait; echo \"rc=$rc d=${{JD[0]:-}} jr=${{JR[0]:-}} gv=${{GVS[0]:-}}\"\n")
        p = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, PATH=os.path.join(t, "bin") + os.pathsep + os.environ["PATH"]))
        self.assertEqual(p.stderr, "")
        out = p.stdout.splitlines()
        return (rd(got).strip() if os.path.exists(got) else None), out[-1], [x for x in out if not x.startswith("rc=")]

    JOB = "sudo -E /copy/testvm/holttest1d/ht1.sh"

    def test_it_fits_the_ram_disk_now_in_ram_though_over_12(self):
        """truenas25 (27 GiB) and pop24 (20) on a big host with an empty RAM disk: the lines
        t2h-20261123T1713-3cd9 put on the SSD for the cutoff of 12 alone."""
        job, last, said = self.launch("truenas25=27%4")
        self.assertEqual(job, f"HT_RAM=1 WEAR_TAG=ht1:r1:truenas25 HT_VARIANT= --reserve 27 4 {self.JOB} truenas25 r1")
        self.assertEqual(last, "rc=0 d=27 jr=1 gv=truenas25", "a RAM job: JR 1, which slots_now counts as a RAM slot's")
        self.assertEqual(said, ["SAY slot 0: truenas25=27%4 on port 2600 (pid " + said[0].split("(pid ")[1].split(")")[0]
                                + "), --reserve 27 4, a RAM slot, 40 C, 200 GB free"])
        job, last, _ = self.launch("pop24+bigger=20%4", after="20.0 23.2")
        self.assertEqual(job, f"HT_RAM=1 WEAR_TAG=ht1:r1:pop24-bigger HT_VARIANT=bigger --reserve 20 4 {self.JOB} pop24 r1")
        job, _, _ = self.launch("mint22=13%4", after="13.0 4.0")
        self.assertIn(" --reserve 13 4 ", job)

    def test_the_ram_disk_is_full_on_the_ssd(self):
        job, last, said = self.launch("neon24=15%4", after="2.0 19.2")   # (truenas25 holds 27 of 29)
        self.assertEqual(job, f"HT_RAM=0 WEAR_TAG=ht1:r1:neon24 HT_VARIANT= --reserve-ssd 15 4 {self.JOB} neon24 r1")
        self.assertEqual(last, "rc=0 d=0 jr=0 gv=neon24", "an SSD job: JR 0, an SSD slot's")
        self.assertIn("--reserve-ssd 15 4, on /bkoscratch (overflow: the RAM disk has room for 2.0 GiB now, not its 15), "
                      "an SSD slot, 40 C", said[0])
        job, _, _ = self.launch("debian13=4%4", after="2.0 19.2")
        self.assertIn("HT_RAM=0 ", job)
        self.assertIn(" --reserve-ssd 4 4 ", job)
        job, _, _ = self.launch("alpine324=2%4", after="2.0 19.2")
        self.assertIn(" --reserve 2 4 ", job, "what still fits stays in RAM")
        # the SSD short of room too: it waits for the RAM disk, as in mode ram
        job, _, _ = self.launch("neon24=15%4", after="2.0 19.2", ssd="14.0")
        self.assertIn("HT_RAM=1 ", job)
        self.assertIn(" --reserve 15 4 ", job)

    def test_over_the_ceiling_on_the_ssd(self):
        job, last, said = self.launch("vanilla3=30%4")
        self.assertEqual(job, f"HT_RAM=0 WEAR_TAG=ht1:r1:vanilla3 HT_VARIANT= --reserve-ssd 30 4 {self.JOB} vanilla3 r1")
        self.assertIn("on /bkoscratch (DISK_OVER: over the RAM-disk ceiling 29.0 23.2)", said[0])
        job, _, _ = self.launch("mint22=17%4", ceil="9.0 10.1", after="9.0 10.1", ssd="156.0")   # a small host
        self.assertIn(" --reserve-ssd 17 4 ", job)
        job, _, _ = self.launch("debian13=9%4", ceil="9.0 10.1", after="9.0 10.1", ssd="156.0")
        self.assertIn(" --reserve 9 4 ", job)
        # no room for it on the SSD now: back to the todo for the other hosts, nothing started
        job, last, said = self.launch("mint22=17%4", ceil="9.0 10.1", ssd="14.5")
        self.assertIsNone(job)
        self.assertEqual(last, "rc=1 d= jr= gv=")
        self.assertEqual(said, ["BACK mint22=17%4: not started here: its D 17 is over the RAM-disk ceiling 9.0 10.1, and "
                                "/bkoscratch has room for 14.5 GiB of scratch now"])

    def test_slots(self):
        """The slot model: the RAM slot taken, on the SSD though the RAM disk has room; the SSD
        slots full and a RAM slot free, in RAM when it fits; no slot it can take, back to the
        todo and nothing started."""
        job, last, said = self.launch("debian13=4%4", rs=0)
        self.assertEqual(job, f"HT_RAM=0 WEAR_TAG=ht1:r1:debian13 HT_VARIANT= --reserve-ssd 4 4 {self.JOB} debian13 r1")
        self.assertIn("--reserve-ssd 4 4, on /bkoscratch (no RAM slot free), an SSD slot, 40 C", said[0])
        job, last, _ = self.launch("truenas25=27%4", ss=0)
        self.assertIn(" --reserve 27 4 ", job)
        self.assertEqual(last, "rc=0 d=27 jr=1 gv=truenas25")
        job, last, said = self.launch("debian13=4%4", rs=0, ss=0)
        self.assertIsNone(job)
        self.assertEqual((last, said), ("rc=1 d= jr= gv=", ["BACK debian13=4%4: not started now: no slot free"]))
        job, last, said = self.launch("vanilla3=30%4", ss=0)
        self.assertIsNone(job, "over the ceiling: an SSD slot only")
        self.assertEqual(said, ["BACK vanilla3=30%4: not started now: no SSD slot free (its D is over the RAM-disk ceiling "
                                "29.0 23.2)"])
        job, last, said = self.launch("debian13+img=4%4", rs=0)
        self.assertIsNone(job, "+img: a RAM slot only")
        self.assertEqual(said, ["BACK debian13+img=4%4: not started now: no RAM slot free (+img works only in the RAM disk)"])
        # one that can't go back stays taken with its done row, as any line given back
        job, last, said = self.launch("debian13=4%4", rs=0, ss=0, back_rc=1)
        self.assertIsNone(job)
        self.assertEqual(last, "rc=1 d= jr= gv=")
        # modes ram and ssd count no slots
        job, _, said = self.launch("neon24=15%4", mode="ram", rs=0, ss=0)
        self.assertIn(" --reserve 15 4 ", job)
        self.assertNotIn("slot,", said[0])
        job, _, _ = self.launch("alpine324=2%4", mode="ssd", rs=0, ss=0)
        self.assertIn(" --reserve-ssd 2 4 ", job)

    def test_the_rest_is_kept(self):
        """+img only in the RAM disk; mode ssd every line; mode ram none, but holt_disk_mode's
        line over the ceiling under --reserve 0 M as before 2026-11-23."""
        job, _, _ = self.launch("debian13+img=15%4", after="2.0 19.2")
        self.assertEqual(job, f"HT_RAM=1 WEAR_TAG=ht1:r1:debian13-img HT_VARIANT=img --reserve 15 4 {self.JOB} debian13 r1")
        job, last, said = self.launch("vanilla3+img=30%4")
        self.assertIsNone(job, "cant_why's: back to the others")
        self.assertEqual(said, ["BACK vanilla3+img=30%4: not started here: over"])
        job, _, _ = self.launch("alpine324=2%4", mode="ssd")
        self.assertIn("HT_RAM=0 ", job)
        self.assertIn(" --reserve-ssd 2 4 ", job)
        job, _, _ = self.launch("neon24=15%4", mode="ram", after="2.0 19.2")
        self.assertIn(" --reserve 15 4 ", job)
        job, _, _ = self.launch("vanilla3=30%4", mode="ram", disk_over=1)
        self.assertEqual(job, f"HT_RAM=0 WEAR_TAG=ht1:r1:vanilla3 HT_VARIANT= --reserve 0 4 {self.JOB} vanilla3 r1")


@unittest.skipUnless(BASH, "no bash")
class QueueSlots(unittest.TestCase):
    """slots_now: this runner's free RAM and SSD slots, from its jobs' JR (1: a RAM job, 0: an
    SSD job), and IDLE.  Slots count in mode overflow only; holt_jobs (MAXJOBS) is the total."""

    def slots(self, jr, mode="overflow", rslots=2, sslots=2):
        script = (f"MODE={mode} RAM=1 RSLOTS={rslots} SSLOTS={sslots}; JOB=(); JR=()\n"
                  + "".join(f"JOB[{i}]=1{i}; JR[{i}]={r}\n" for i, r in enumerate(jr))
                  + shell_funcs(QUEUE, "slots_now") + 'slots_now; echo "$RSFREE $SSFREE $IDLE"\n')
        p = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.stderr, "")
        return p.stdout.strip()

    def test_a_big_host_s_two_and_two(self):
        self.assertEqual(self.slots([]), "1 1 1")
        self.assertEqual(self.slots([1]), "1 1 0")
        self.assertEqual(self.slots([1, 1]), "0 1 0", "both RAM slots taken")
        self.assertEqual(self.slots([1, 1, 0]), "0 1 0")
        self.assertEqual(self.slots([0, 0]), "1 0 0", "both SSD slots taken")
        self.assertEqual(self.slots([1, 0, 1, 0]), "0 0 0")

    def test_a_small_host_s_one_and_one(self):
        self.assertEqual(self.slots([1], rslots=1, sslots=1), "0 1 0")
        self.assertEqual(self.slots([0], rslots=1, sslots=1), "1 0 0")
        self.assertEqual(self.slots([1, 0], rslots=1, sslots=1), "0 0 0")

    def test_the_other_modes_count_none(self):
        self.assertEqual(self.slots([1, 1, 1], mode="ram", rslots=1, sslots=0), "1 1 0")
        self.assertEqual(self.slots([0, 0, 0], mode="ssd", rslots=0, sslots=1), "1 1 0")

    def test_the_loop_asks_them(self):
        q = rd(QUEUE)
        self.assertIn('elif slots_now; ((RSFREE + SSFREE == 0)); then waiting slots ""', q)
        self.assertIn("done | INLINE=$IDLE place_lines)", q, "the scan: a line with no place now is skipped by the pop")
        self.assertLess(q.index("elif ((${#JOB[@]} >= MAX)); then waiting full"), q.index("elif slots_now;"),
                        "holt_jobs stays the total")


@unittest.skipUnless(BASH, "no bash")
class QueueStopWhy(unittest.TestCase):
    """queue/RUN/stop's first line, when it has one (Test2 writes why it stopped), in the
    runner's log: its waiting line and its last."""

    def why(self, text):
        t = tempfile.mkdtemp(prefix="t2-stopwhy-")
        self.addCleanup(shutil.rmtree, t, True)
        if text is not None:
            wr(os.path.join(t, "stop"), text)
        script = f"Q={t}\n" + shell_funcs(QUEUE, "stop_why") + 'echo "stopped$(stop_why)"\n'
        p = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.stderr, "")
        return p.stdout

    def test_the_first_line_says_why(self):
        self.assertEqual(self.why("t2 on the Mac: SIGTERM: nothing more starts\nmore\n"),
                         "stopped (t2 on the Mac: SIGTERM: nothing more starts)\n")
        self.assertEqual(self.why(""), "stopped\n")      # (a touch: as before)
        self.assertEqual(self.why(None), "stopped\n")

    def test_the_loop_logs_it(self):
        q = rd(QUEUE)
        self.assertIn('waiting stop "stop: starting nothing more$(stop_why), ${#JOB[@]} running"', q)
        self.assertIn('then say "stopped$(stop_why)"; break', q)


class QueueRefusedReserve(unittest.TestCase):
    def test_the_refusal_is_cmp_runs(self):
        """reap's grep matches what cmp-run.sh says when a --reserve can never fit."""
        say = re.search(r'echo "(--reserve \$d \$m can never fit here[^"]*)"', rd(CMP_RUN)).group(1)
        line = say.replace("$d", "17").replace("$m", "4").replace("$cd", "9.0").replace("$cm", "9.2")
        pat = re.search(r"grep -qE -- '([^']+)' \"\$L/\$RUN-", rd(QUEUE)).group(1)
        self.assertRegex(line, pat.replace(".*", ".*"))
        self.assertNotRegex("--reserve 7 4 can't fit now", pat)
        # ... and a --reserve-ssd's too (a line on /bkoscratch: RAM first, the SSD for overflow)
        say = re.search(r'echo "(--reserve-ssd \$s \$m can never fit here[^"]*)"', rd(CMP_RUN)).group(1)
        self.assertRegex(say.replace("$s", "900").replace("$m", "4"), pat)


def flock_bash():
    b = modern_bash()
    return b if b and shutil.which("flock") else None


@unittest.skipUnless(flock_bash(), "needs bash 4 or newer with timeout, realpath and flock (the test hosts')")
class QueueShell(unittest.TestCase):
    """--pop's =LINE entries, --putback, cant_scan over a todo and reap's refused --reserve."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-queue-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.q = os.path.join(self.t, "q")
        os.makedirs(self.q)

    def cli(self, *args):
        return subprocess.run([flock_bash(), QUEUE] + list(args), capture_output=True, text=True, timeout=120)

    def test_pop_exact_lines_and_putback(self):
        wr(os.path.join(self.q, "todo"), "debian13\ndebian13+bigger\ngentoo\nmint22\n")
        p = self.cli("--pop", self.q, "copland", "0", "13", "=debian13,gentoo", "")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "debian13+bigger"), p.stderr)   # =LINE: that line only
        p = self.cli("--pop", self.q, "copland", "0", "13", "=debian13,gentoo", "")
        self.assertEqual(p.stdout.strip(), "mint22")                                           # gentoo: every line of it
        self.assertEqual(rd(os.path.join(self.q, "todo")), "debian13\ngentoo\n")
        self.assertEqual(rd(os.path.join(self.q, "todo.taken")), "debian13+bigger copland\nmint22 copland\n")
        p = self.cli("--putback", self.q, "debian13+bigger", "copland")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "@BACK"), p.stderr)
        self.assertEqual(rd(os.path.join(self.q, "todo")), "debian13+bigger\ndebian13\ngentoo\n")   # on top
        self.assertEqual(rd(os.path.join(self.q, "todo.taken")), "mint22 copland\n")
        wr(os.path.join(self.q, "todo.stopped"), "mint22+oneshot  # stop-os mint\n")
        p = self.cli("--putback", self.q, "mint22", "copland")
        self.assertEqual((p.returncode, p.stdout.strip()), (1, "@STOPPED"))
        self.assertEqual(rd(os.path.join(self.q, "todo.taken")), "mint22 copland\n")
        self.assertEqual(self.cli("--putback", self.q, "bad name", "copland").returncode, 2)

    def scan_script(self, body):
        """cant_scan's world: W's images, L's rescuer and debs, a fake ram-size.sh (6 4 for every
        name) and a cmp-run.sh whose ceiling is 9.0 9.2."""
        w, l, fake = (os.path.join(self.t, x) for x in ("w", "l", "fake"))
        for g in ("debian13", "mint22", "debian13-zfs", "redox", "freebsd15", "freebsd15-ufs", "netbsd10", "anduinos2"):
            wr(os.path.join(w, "vms", g, g + ".qcow2"), "")
        wr(os.path.join(w, "vms", "anduinos2", "vm-run-options"), "--uefi --mok\n")   # a MOK guest, and no sbverify here
        wr(os.path.join(l, "rescuer", "rescuer.qcow2"), "")
        for n in ("f2fs-tools_1_amd64.deb", "jfsutils_1_amd64.deb"):
            wr(os.path.join(l, "bin", n), "")
        wr(os.path.join(fake, "ram-size.sh"), '#!/bin/sh\nfor n; do echo "$n 6 4 table"; done\n', 0o755)
        wr(os.path.join(fake, "cmp-run.sh"), '#!/bin/sh\n[ "$1" = --ceiling ] && echo "9.0 9.2"\n', 0o755)
        wr(os.path.join(self.q, "todo"), "# the queue\ndebian13\nmint22=17%4\nmint22+bigger=8%4\ngentoo\n"
                                        "debian13-zfs\nredox\nfreebsd15\nnetbsd10\ndebian13+img\nanduinos2+reformat-ext4\nbad line!\n")
        return (shell_line(QUEUE, "LINE_RE=")
                + f"W={w} L={l} Q={self.q} QHOST='' HERE={fake} CMP_RUN={fake}/cmp-run.sh RAM=1 MODE=ram HOSTZFS=0 HOSTSBV=0 HOSTARCH=amd64\n"
                + "zfs_list=(debian13-zfs); CANT=() CANTWHY=''; declare -A PLK=(); NEWLK=''; unset HT_RESCUER_IMG\n"
                + shell_funcs(QUEUE, "guest_arch", "has_image", "needs_zfs", "has_debs", "has_mok", "ceil_now", "over_ceil", "fits_ceil",
                              "cant_why", "todo_text", "cant_scan", "close_locks")
                + body)

    def test_cant_scan(self):
        l = os.path.join(self.t, "l")
        p = subprocess.run([flock_bash(), "-c", self.scan_script(
            'cant_scan; printf "%s\\n" "${CANT[@]}"; echo "--"; echo "$CANTWHY"; echo "--"\n'
            f'rm {l}/rescuer/rescuer.qcow2; cant_scan; printf "%s\\n" "${{CANT[@]}}"; echo "--"\n'
            'todo_text() { return 1; }; cant_scan; printf "%s\\n" "${CANT[@]}"\n')],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(p.stderr, "")
        first, why, second, third = p.stdout.split("--\n")
        self.assertEqual(first.splitlines(), ["=mint22=17%4", "gentoo", "=debian13-zfs", "netbsd10",   # no netbsd11
                                              "=anduinos2+reformat-ext4"])   # a MOK guest, no sbverify: the line
        self.assertIn("mint22=17%4 (--reserve 17 4 over this host's ceiling 9.0 9.2); gentoo (no ", why)
        self.assertEqual(second.splitlines(), ["=debian13", "=mint22=17%4", "=mint22+bigger=8%4", "gentoo",
                                               "=debian13-zfs", "=redox", "netbsd10",   # not freebsd15, not +img
                                               "=anduinos2+reformat-ext4"])   # (its missing rescuer says so first)
        self.assertEqual(third.splitlines(), second.splitlines(), "an unreadable todo keeps the last skip set")

    def test_refused_reserve_is_no_quick_failure(self):
        """A --reserve cmp-run.sh refused as never fitting: back to the todo, no done row, no
        change to NQUICK; a plain quick failure still counts."""
        log = os.path.join(self.t, "r1-mint22.log")
        script = (f"Q={self.q} L={self.t} RUN=r1 H=copland QUICK='' NQUICK=0 BROKE='' newbig=0; REFUSED=()\n"
                  "declare -A JOB=() GVS=() BASEG=() PORT_OF=() PLK=() JD=() JM=() T0=() TA=() WARN=() SIG=() LINE_OF=()\n"
                  "say() { echo \"SAY $*\"; }\n"
                  + shell_funcs(QUEUE, "done_row", "reap")
                  + "job() { local i x; exec {x}>/dev/null; bash -c 'exit 1' & JOB[$1]=$! GVS[$1]=$2 BASEG[$1]=$2 PORT_OF[$1]=2600\n"
                    "        PLK[$1]=$x JD[$1]=17 JM[$1]=4 T0[$1]=$(date +%s) TA[$1]='' WARN[$1]='' SIG[$1]='' LINE_OF[$1]=$3\n"
                    "        for ((i = 0; i < 100; i++)); do [[ -e /proc/${JOB[$1]} ]] || break; sleep 0.1; done; }\n"
                  + "give_back() { echo \"BACK $1\"; return 0; }\n"
                  + f"echo '--reserve 17 4 can never fit here (at most 9.0 9.2)' >{log}\n"
                  + "job 0 mint22 mint22=17%4; reap; echo \"jobs=${#JOB[@]} nquick=$NQUICK refused=${REFUSED[*]}\"\n"
                  + "give_back() { echo \"STAYS $1\"; return 1; }\n"
                  + "job 0 mint22 mint22=17%4; reap; echo \"jobs=${#JOB[@]} nquick=$NQUICK\"\n"
                  + f": >{log}; job 0 mint22 mint22=17%4; reap; echo \"jobs=${{#JOB[@]}} nquick=$NQUICK\"\n")
        p = subprocess.run([flock_bash(), "-c", script], capture_output=True, text=True, timeout=120)
        out = p.stdout.splitlines()
        self.assertIn("BACK mint22=17%4", out)
        self.assertIn("jobs=0 nquick=0 refused==mint22=17%4", out)
        self.assertIn("STAYS mint22=17%4", out)
        self.assertEqual([x for x in out if x.startswith("jobs=")][1:], ["jobs=0 nquick=0", "jobs=0 nquick=1"])
        done = rd(os.path.join(self.q, "done")).splitlines()
        self.assertEqual([r.split("\t")[3] for r in done[1:]], ["1", "1"])   # the one that stayed, the plain failure


@unittest.skipUnless(BASH, "no bash")
class Ht1Refuses(unittest.TestCase):
    """ht1.sh refuses (exit 2, nothing made) a rebuild without the rescuer and a backup or a
    rebuild without bin/'s debs, instead of falling back to the debian13 guest."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-ht1-")
        self.addCleanup(shutil.rmtree, self.t, True)
        d = os.path.join(self.t, "testvm", "holttest1d")
        self.bin, self.resc = os.path.join(self.t, "bin"), os.path.join(self.t, "rescuer", "rescuer.qcow2")
        self.resc_arm = os.path.join(self.t, "rescuer", "rescuer-arm64.qcow2")
        text = rd(os.path.join(HT, "ht1.sh"))
        for old, new in (("BIN=/bkoscratch/holttest1d/bin\n", f"BIN={self.bin}\n"),
                         ("/bkoscratch/holttest1d/rescuer/rescuer.qcow2}", self.resc + "}"),
                         ("/bkoscratch/holttest1d/rescuer/rescuer-arm64.qcow2}", self.resc_arm + "}")):
            self.assertEqual(text.count(old), 1, old)
            text = text.replace(old, new)
        wr(os.path.join(d, "ht1.sh"), text, 0o755)
        # Past the checks, the run would make its paths: the fake library ends it there.
        wr(os.path.join(d, "ht1-lib.sh"), "ht_holt_pick() { HOLT=/fake/bko-holt; }\nht_paths() { echo PASSED; exit 0; }\n")
        self.ht1 = os.path.join(d, "ht1.sh")

    def run_ht1(self, *phases, guest="debian13", machine="x86_64", **env):
        # (uname -m stubbed: ht1.sh runs a guest only on a host of its architecture, A6)
        stub = os.path.join(self.t, "stub")
        wr(os.path.join(stub, "uname"), f'#!/bin/sh\n[ "$1" = -m ] && echo {machine} || exec /usr/bin/env -i PATH=/usr/bin:/bin uname "$@"\n', 0o755)
        e = {k: v for k, v in os.environ.items() if not k.startswith("HT_")}
        e.update(env, PATH=stub + os.pathsep + os.environ.get("PATH", ""))
        return subprocess.run([BASH, self.ht1, guest, "r1"] + list(phases), capture_output=True, text=True,
                              env=e, timeout=60)

    def test_an_arm64_guest_takes_its_rescuer_and_no_debs(self):
        p = self.run_ht1(guest="debian13-arm64", machine="aarch64")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn(f"ht1: no rescuer {self.resc_arm} on ", p.stderr)
        wr(self.resc_arm, "")
        self.assertEqual(self.run_ht1(guest="debian13-arm64", machine="aarch64").stdout.strip(), "PASSED")   # no debs
        p = self.run_ht1(guest="debian13-arm64", machine="x86_64")
        self.assertEqual(p.returncode, 2)
        self.assertIn("is an arm64 guest", p.stderr)
        self.debs()
        wr(self.resc, "")
        p = self.run_ht1(machine="aarch64")
        self.assertEqual(p.returncode, 2)
        self.assertIn("is an amd64 guest", p.stderr)

    def debs(self):
        for n in ("f2fs-tools_1.16_amd64.deb", "jfsutils_1.1_amd64.deb"):
            wr(os.path.join(self.bin, n), "")

    def test_no_rescuer(self):
        self.debs()
        p = self.run_ht1()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn(f"ht1: no rescuer {self.resc} on ", p.stderr)
        self.assertIn("HT_RESCUER_IMG=/home/m/backotter-work/vms/debian13/debian13.qcow2 takes the plain guest", p.stderr)
        self.assertNotIn("PASSED", p.stdout)
        self.assertEqual(self.run_ht1("rebuild").returncode, 2)
        self.assertEqual(self.run_ht1("source", "backup").stdout.strip(), "PASSED")   # no rebuild: no rescuer needed
        missing = os.path.join(self.t, "nope.qcow2")
        p = self.run_ht1(HT_RESCUER_IMG=missing)
        self.assertEqual(p.returncode, 2)
        self.assertIn(f"no rescuer {missing} on ", p.stderr)
        self.assertIn("(HT_RESCUER_IMG)", p.stderr)

    def test_no_debs(self):
        wr(self.resc, "")
        p = self.run_ht1()
        self.assertEqual(p.returncode, 2)
        self.assertIn(f"ht1: no {self.bin}/f2fs-tools_*.deb on ", p.stderr)
        self.assertEqual(self.run_ht1("backup").returncode, 2)
        self.assertEqual(self.run_ht1("source").stdout.strip(), "PASSED")
        wr(os.path.join(self.bin, "f2fs-tools_1.16_amd64.deb"), "")
        p = self.run_ht1()
        self.assertIn(f"ht1: no {self.bin}/jfsutils_*.deb on ", p.stderr)

    def test_with_both_it_goes_on(self):
        wr(self.resc, "")
        self.debs()
        p = self.run_ht1()
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "PASSED"), p.stderr)
        other = os.path.join(self.t, "plain.qcow2")
        wr(other, "")
        os.remove(self.resc)
        p = self.run_ht1(HT_RESCUER_IMG=other)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "PASSED"), p.stderr)

    def test_no_fallback_left(self):
        self.assertNotIn('RESCUER_IMG=$W/vms/$RESCUER/$RESCUER.qcow2\n', rd(os.path.join(HT, "ht1.sh")))


def realpath_e():
    """Whether this realpath has GNU's -e (the test hosts' does, macOS's doesn't)."""
    return subprocess.run(["realpath", "-e", "/"], capture_output=True).returncode == 0


@unittest.skipUnless(BASH and shutil.which("sha256sum"), "no bash or sha256sum")
class HoltPick(unittest.TestCase):
    """The bko-holt a HoltTest1 run tests (ht1-lib.sh's ht_holt_pick, and ht1-queue.sh
    --holt, which ht1-farm.sh asks each host): HT_HOLT's file, else HT_HOLT_BIN's set
    (DIR/bko-holt for linux when it has one, else DIR/PLAT/bko-holt), else the promoted
    W/holt-bin/PLAT/bko-holt, which its SHA256SUMS must vouch for; never bin/'s by default."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-holt-pick-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.w = os.path.join(self.t, "w")
        self.hb = os.path.join(self.w, "holt-bin")
        lines = []
        for plat in ("linux", "freebsd"):
            wr(os.path.join(self.hb, plat, "bko-holt"), plat + "\n")
            lines.append(f"{self.sha(os.path.join(self.hb, plat, 'bko-holt'))}  {plat}/bko-holt")
        wr(os.path.join(self.hb, "SHA256SUMS"), "\n".join(lines) + "\n")
        self.code = os.path.join(self.t, "code")          # HoltTest2's layout: linux's on top
        wr(os.path.join(self.code, "bko-holt"), "code linux\n")
        wr(os.path.join(self.code, "freebsd", "bko-holt"), "code freebsd\n")
        q = rd(QUEUE)
        self.assertEqual(q.count("\nW=/home/m/backotter-work\n"), 1)
        self.queue = os.path.join(self.t, "ht1-queue.sh")
        wr(self.queue, q.replace("\nW=/home/m/backotter-work\n", f"\nW={self.w}\n"), 0o755)

    @staticmethod
    def sha(p):
        return subprocess.run(["sha256sum", p], capture_output=True, text=True).stdout.split()[0]

    def pick(self, plat, **env):
        e = {"PATH": os.environ["PATH"], **env}
        lib = os.path.join(HT, "ht1-lib.sh")
        return subprocess.run([BASH, "-c", f'set -uo pipefail; W={self.w}; . {lib}; ht_holt_pick {plat}; echo "$HOLT"'],
                              capture_output=True, text=True, env=e, timeout=60)

    def test_the_default_is_the_promoted_set(self):
        for plat in ("linux", "freebsd"):
            p = self.pick(plat)
            self.assertEqual((p.returncode, p.stdout.strip()), (0, os.path.join(self.hb, plat, "bko-holt")), p.stderr)
        self.assertNotIn("holttest1d/bin", rd(os.path.join(HT, "ht1-lib.sh")).split("ht_holt_pick() {")[1].split("\n}")[0])

    def test_a_default_it_cant_vouch_for_refuses(self):
        p = self.pick("haiku")                            # no such file: no fall back to bin/
        self.assertEqual(p.returncode, 2)
        self.assertIn(f"no {self.hb}/haiku/bko-holt (the promoted holt-bin", p.stderr)
        with open(os.path.join(self.hb, "linux", "bko-holt"), "a") as f:
            f.write("changed\n")
        p = self.pick("linux")
        self.assertEqual(p.returncode, 2)
        self.assertIn(f"{self.hb}/SHA256SUMS says", p.stderr)
        wr(os.path.join(self.hb, "SHA256SUMS"), "")
        p = self.pick("freebsd")
        self.assertEqual(p.returncode, 2)
        self.assertIn(f"no freebsd/bko-holt in {self.hb}/SHA256SUMS", p.stderr)

    def test_ht_holt_bin_and_ht_holt(self):
        for plat, d, want in (("linux", self.code, "bko-holt"), ("freebsd", self.code, "freebsd/bko-holt"),
                              ("linux", self.hb, "linux/bko-holt")):
            p = self.pick(plat, HT_HOLT_BIN=d)
            self.assertEqual((p.returncode, p.stdout.strip()), (0, os.path.join(d, want)), p.stderr)
        p = self.pick("netbsd", HT_HOLT_BIN=self.code)
        self.assertEqual(p.returncode, 2)
        self.assertIn(f"no {self.code}/netbsd/bko-holt (HT_HOLT_BIN={self.code})", p.stderr)
        p = self.pick("linux", HT_HOLT="/bkoscratch/holttest1d/bin/bko-holt")   # only this way, and unchecked
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "/bkoscratch/holttest1d/bin/bko-holt"), p.stderr)

    def queue_holt(self, **env):
        return subprocess.run([BASH, self.queue, "--holt"], capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], **env}, timeout=60)

    def test_the_queue_says_and_checks_it(self):
        lx = os.path.join(self.hb, "linux", "bko-holt")
        p = self.queue_holt()
        self.assertEqual((p.returncode, p.stdout), (0, f"{self.sha(lx)[:12]} {lx}\n"), p.stderr)
        if realpath_e():                                  # (HT_HOLT_BIN is made absolute by realpath -e)
            p = self.queue_holt(HT_HOLT_BIN=self.code)
            top = os.path.join(self.code, "bko-holt")
            self.assertEqual((p.returncode, p.stdout), (0, f"{self.sha(top)[:12]} {top}\n"), p.stderr)
        wr(os.path.join(self.hb, "SHA256SUMS"), "")
        p = self.queue_holt()
        self.assertEqual(p.returncode, 2)
        self.assertIn(f"{self.hb}/SHA256SUMS doesn't vouch for {lx}", p.stderr)
        os.remove(lx)
        p = self.queue_holt()
        self.assertEqual(p.returncode, 2)
        self.assertIn(f"no {lx} (the promoted holt-bin", p.stderr)


class DriveSelfTest(unittest.TestCase):
    def test_drive_self_test(self):
        p = subprocess.run([sys.executable, "-B", os.path.join(TESTVM, "stictest1", "drive.py"), "--self-test"],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 0, "\n".join(l for l in p.stdout.splitlines() if l.startswith("FAIL")))
        self.assertIn("ok   no image of the guest here: passed over", p.stdout)
        self.assertIn("ok   novm: never started, the round ends", p.stdout)


if __name__ == "__main__":
    unittest.main()
