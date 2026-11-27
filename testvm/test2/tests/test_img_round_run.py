"""I8's tests (docs/design/img-speed.md §5.2 steps 4-5, §10, C10, D16, §14 row I8): stick/img_round.py
run and fanout over a fixture farm (each host a directory, reached by a fake ssh that runs the
command there; stub img-lanes.sh and img-xcopy.sh), the round lock (the Mac's and each host's
images/.round.lock, which img-refresh-holt.sh and promote-bins.sh refuse), img-one.sh's refusal
of a host whose sets are not the plan's, and img-lanes.sh's run name, refresh units and plan
environment.

No real host, no network, no sudo.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import datetime
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STICK = os.path.join(WT, "stick")
sys.path.insert(0, HERE)

import test_img_round as TR  # noqa: E402

R = TR.R
PLATS = R.PLATFORMS
BASH = shutil.which("bash")
LINUX = sys.platform.startswith("linux") and shutil.which("flock") and shutil.which("setsid")
DESTS = TR.BUILDERS + ["choubox"]

FAKE_SSH = r"""#!/usr/bin/env bash
while [[ $# -gt 0 && $1 == -* ]]; do case $1 in -o) shift 2 ;; *) shift ;; esac; done
dest=$1; shift
name=${dest#*@}; name=${name%%.*}
root=$FARM/$name
[[ -d $root ]] || { echo "ssh: no host $dest" >&2; exit 255; }
[[ ! -e $root/down ]] || { echo "ssh: connect to host $dest: No route to host" >&2; exit 255; }
e=(env FARM="$FARM" HOST="$name" BKO_WORK="$root/work" S="$FARM/stick" PATH="$FARM/bin:$PATH")
# (drop-build, drop-hold: the command goes on there, but this ssh is lost, once.)
if [[ -e $root/drop-build && $* == *img-lanes.sh* ]]; then
    rm -f "$root/drop-build"; "${e[@]}" bash -c "$*" </dev/null >/dev/null 2>&1 &
    sleep 1; echo "client_loop: ssh_packet_write_poll: Connection to $name port 22: Host is down" >&2; exit 255
fi
if [[ -e $root/drop-hold && $* == *img-round-hold* ]]; then
    rm -f "$root/drop-hold"; "${e[@]}" bash -c "$*" </dev/null &
    sleep 2; exit 255
fi
exec "${e[@]}" bash -c "$*"
"""

# img-lanes.sh's stand-in: each unit "built" into images/P as img-one.sh leaves it (done.exit 1
# when the host has a fail-P file), and what it was given in lanes.calls.
FAKE_LANES = r"""#!/usr/bin/env bash
RUN=x; [[ $1 == --run ]] && { RUN=$2; shift 2; }
[[ ! -e $BKO_WORK/../slow ]] || sleep 6
I=${BKO_IMG_DIR:-$BKO_WORK/images}
held=free
python3 -c 'import fcntl, sys; fcntl.flock(open(sys.argv[1], "a"), fcntl.LOCK_SH | fcntl.LOCK_NB)' "$I/.round.lock" 2>/dev/null || held=held
printf '%s|%s|%s|%s|%s|%s\n' "$RUN" "$*" "${BKO_PLAN_BKO_BIN:-}" "${BKO_PLAN_HOLT_BIN:-}" "${BKO_PLAN_BUILDER_netbsd:-}" "$held" >>"$BKO_WORK/../lanes.calls"
ex=0
for l in "$@"; do for u in $l; do
    p=${u%%@*} d=$I/$p
    rm -rf "$d"; mkdir -p "$d/test"
    echo "release $p" >"$d/bko-$p.img"; echo "test $p" >"$d/test/bko-$p-test.img"
    (cd "$d" && sha256sum bko-$p.img >SHA256SUMS && cd test && sha256sum bko-$p-test.img >SHA256SUMS)
    (cd "$BKO_WORK/bko-bin/$p" && sha256sum ./*-bko; cd "$BKO_WORK/holt-bin/$p" && sha256sum ./bko-holt) >"$d/bko-bin.sha256"
    printf '{"mode": "full", "built": {"run": "%s", "host": "%s"}}\n' "$RUN" "$HOST" >"$d/provenance.json"
    if [[ -e $BKO_WORK/../fail-$p ]]; then echo "$p FAIL: make-img-bsd" >"$d/result.txt"; echo 1 >"$d/done.exit"; ex=1
    else echo 0 >"$d/done.exit"; fi
    echo "built $u" >"$I/$p.lane.log"
done; done
exit $ex
"""

FAKE_XCOPY = r"""#!/usr/bin/env bash
D=$1; shift
n=${D#*@}; n=${n%%.*}
echo "$HOST $n $*" >>"$FARM/xcopy.calls"
for p; do
    mkdir -p "$FARM/$n/work/images"; rm -rf "$FARM/$n/work/images/$p"
    cp -R "$BKO_WORK/images/$p" "$FARM/$n/work/images/$p"
    [[ ! -e $FARM/$n/corrupt-$p ]] || echo junk >"$FARM/$n/work/images/$p/bko-$p.img"
done
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


def jl(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Table:
    """hosts.py's ssh(), for the farm."""

    @staticmethod
    def ssh(name, table=None):
        return f"m@{name}.local"


class Pure(unittest.TestCase):
    def test_lanes_of_spreads_longest_first(self):
        u = [("linux", "full", 25), ("freebsd", "full", 43), ("haiku", "full", 20), ("netbsd", "full", 30)]
        self.assertEqual(R.lanes_of(u, 2), [[("freebsd", "full", 43), ("haiku", "full", 20)],
                                            [("netbsd", "full", 30), ("linux", "full", 25)]])
        self.assertEqual(R.lanes_of(u[:1], 2), [[("linux", "full", 25)]])
        self.assertEqual(len(R.lanes_of(u, 1)), 1)

    def test_build_units_tokens_and_environment(self):
        plan = {"sets": {"bko-bin": "b" * 64, "holt-bin": "h" * 64},
                "platforms": {"linux": {"action": "refresh", "parent": {"test_sha256": "p" * 64}},
                              "netbsd": {"action": "full"}},
                "deal": [{"host": "cage", "lanes": 1, "units": [{"platform": "linux", "action": "refresh", "minutes": 5},
                                                                {"platform": "netbsd", "action": "full", "minutes": 30}]},
                         {"host": "czerny", "lanes": 1, "units": []}]}
        refd = {"netbsd": TR.dig("netbsd"), "linux": TR.dig("linux")}
        u = R.build_units(plan, refd)
        self.assertEqual(list(u), ["cage"])
        self.assertEqual(u["cage"]["lanes"], [["netbsd", "linux@" + "p" * 64]])
        env = dict(u["cage"]["env"])
        self.assertEqual(env["BKO_PLAN_BKO_BIN"], "b" * 64)
        self.assertEqual(env["BKO_PLAN_HOLT_BIN"], "h" * 64)
        self.assertEqual(json.loads(env["BKO_PLAN_BUILDER_netbsd"]), refd["netbsd"]["base"]["inputs"]["builder"])
        self.assertNotIn(" ", env["BKO_PLAN_BUILDER_netbsd"])   # img_inputs.py identity's canon line
        self.assertNotIn("BKO_PLAN_BUILDER_linux", env)

    def test_dest_why(self):
        row = {"bins": "%s  ./restic-bko\n%s  ./rustic-bko\n%s  ./bko-holt\n" % ("r" * 64, "u" * 64, "o" * 64)}
        good = {"holt_sums": "h" * 64, "platforms": {"netbsd": {"bko-holt": "x" * 64,
                                                                "bins": {"restic-bko": "r" * 64, "rustic-bko": "u" * 64}}}}
        self.assertIsNone(R.dest_why(good, "netbsd", row, "h" * 64))       # the plan's holt-bin set
        self.assertIsNotNone(R.dest_why(good, "netbsd", row, "z" * 64))
        good["platforms"]["netbsd"]["bko-holt"] = "o" * 64
        self.assertIsNone(R.dest_why(good, "netbsd", row, "z" * 64))       # or the image's own bko-holt
        good["platforms"]["netbsd"]["bins"]["rustic-bko"] = "q" * 64
        self.assertIn("bins-stale", R.dest_why(good, "netbsd", row, "h" * 64))
        self.assertIn("no facts", R.dest_why({"error": "x"}, "netbsd", row, "h" * 64))

    def test_dest_env(self):
        self.assertEqual(R.dest_env([]), [])
        e = dict(R.dest_env([("BKO_WORK", "/p/w"), ("S", "/p/stick")]))
        self.assertEqual((e["BKO_DEST_IMAGES"], e["BKO_DEST_HOLT_BIN"], e["BKO_DEST_STICK"]),
                         ("/p/w/images", "/p/w/holt-bin", "/p/stick"))
        e = dict(R.dest_env([("BKO_IMG_DIR", "/q/images"), ("BKO_HOLT_BIN", "/q/hb")]))
        self.assertEqual((e["BKO_DEST_IMAGES"], e["BKO_DEST_HOLT_BIN"]), ("/q/images", "/q/hb"))

    def test_round_hosts_are_every_amd64_host(self):
        rows = [{"name": h} for h in DESTS] + [{"name": "bigfoot", "arch": "arm64"}, {"name": "x", "enabled": False}]
        self.assertEqual(R.round_hosts(rows, None), DESTS)
        self.assertEqual(R.round_hosts(rows, "cage,choubox"), ["cage", "choubox"])
        with self.assertRaises(R.Usage):
            R.round_hosts(rows, "bigfoot")


class Locks(unittest.TestCase):
    """The round lock: the Mac's, and a host's images/.round.lock held until the round lets go."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i8-lock-"))
        self.addCleanup(shutil.rmtree, self.t, True)

    def holder(self, run="r1", wait="0", grace="0"):
        e = dict(os.environ, BKO_IMG_DIR=os.path.join(self.t, "images"))
        p = subprocess.Popen([sys.executable, "-I", "-B", "-c", R.HOLD_PY, run, "mac", wait, grace], env=e, text=True,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(self.done, p)
        return p

    @staticmethod
    def done(p):
        for f in (p.stdin, p.stdout):
            if not f.closed:
                f.close()
        p.wait(10)

    def test_held_until_released_and_a_second_is_busy(self):
        a = self.holder("r1")
        self.assertTrue(a.stdout.readline().startswith("HELD "))
        lk = os.path.join(self.t, "images", ".round.lock")
        self.assertTrue(rd(lk).startswith("r1 (img-round.sh from mac"))
        b = self.holder("r2")
        self.assertEqual(b.stdout.readline().strip(), "BUSY image round " + rd(lk).strip())
        self.assertEqual(b.wait(10), 3)
        with open(lk) as f:   # a promotion's shared lock is refused meanwhile
            with self.assertRaises(OSError):
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
        a.stdin.write("release\n")
        a.stdin.close()
        self.assertEqual(a.wait(10), 0)
        self.assertEqual(rd(lk), "")
        with open(lk) as f:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_a_lost_ssh_holds_on_until_the_round_takes_over(self):
        a = self.holder("r1", grace="60")
        self.assertTrue(a.stdout.readline().startswith("HELD "))
        a.stdin.close()   # (its ssh gone: no release)
        time.sleep(0.5)
        self.assertIsNone(a.poll())
        b = self.holder("r2")
        self.assertTrue(b.stdout.readline().startswith("BUSY image round r1 "))   # still held, for r1
        c = self.holder("r1", wait="10")
        self.assertTrue(c.stdout.readline().startswith("HELD "))                  # r1's next holder took over
        self.assertEqual(a.wait(10), -15)
        self.assertIn(f"pid {c.pid})", rd(os.path.join(self.t, "images", ".round.lock")))
        c.stdin.write("release\n")
        c.stdin.close()
        self.assertEqual(c.wait(10), 0)

    def test_a_promotions_shared_lock_makes_the_round_wait_then_busy(self):
        os.makedirs(os.path.join(self.t, "images"))
        with open(os.path.join(self.t, "images", ".round.lock"), "a") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            b = self.holder("r2", wait="1")
            self.assertEqual(b.stdout.readline().strip(), "BUSY a promotion or an img-refresh-holt.sh")
            b.wait(10)

    def test_the_macs_lock_is_one_round_at_a_time(self):
        a = R.MacLock(self.t, "r1")
        with self.assertRaises(R.Refused) as cm:
            R.MacLock(self.t, "r2")
        self.assertIn("r1 (pid", str(cm.exception))
        a.release()
        R.MacLock(self.t, "r3").release()

    def test_the_lanes_get_a_hup_when_the_round_lets_go(self):
        s = os.path.join(self.t, "stick")
        wr(os.path.join(s, "img-lanes.sh"), f"#!/usr/bin/env bash\ntrap 'echo hup >{self.t}/hup; exit 129' HUP\n"
                                            "while :; do sleep 0.2; done\n", 0o755)
        script = R.lanes_script([("S", s), ("BKO_IMG_DIR", os.path.join(self.t, "images"))], "r1", {"lanes": [["netbsd"]], "env": []})
        p = subprocess.Popen([BASH, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.done, p)
        self.assertIn("img-round: start ", p.stdout.readline())
        time.sleep(0.5)
        p.stdin.write("stop\n")
        p.stdin.flush()
        self.assertEqual(p.wait(20), 129)
        self.assertEqual(rd(os.path.join(self.t, "hup")), "hup\n")

    def test_lanes_go_on_when_the_rounds_ssh_is_gone(self):
        s = os.path.join(self.t, "stick")
        wr(os.path.join(s, "img-lanes.sh"), f"#!/usr/bin/env bash\ntrap 'echo hup >{self.t}/hup; exit 129' HUP\n"
                                            "sleep 2; echo lanes said; exit 3\n", 0o755)
        script = R.lanes_script([("S", s), ("BKO_IMG_DIR", os.path.join(self.t, "images"))], "r1",
                                {"lanes": [["netbsd"]], "env": []})
        p = subprocess.Popen([BASH, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.done, p)
        p.stdin.close()
        self.assertEqual(p.wait(20), 3)
        self.assertFalse(os.path.exists(os.path.join(self.t, "hup")))
        self.assertEqual(rd(os.path.join(self.t, "images", ".round-r1.exit")), "3\n")
        self.assertIn("lanes said", rd(os.path.join(self.t, "images", ".round-r1.log")))


    def test_lanes_that_end_get_no_hup(self):
        s = os.path.join(self.t, "stick")
        wr(os.path.join(s, "img-lanes.sh"), "#!/usr/bin/env bash\nexit 0\n", 0o755)
        script = R.lanes_script([("S", s), ("BKO_IMG_DIR", os.path.join(self.t, "images"))], "r1", {"lanes": [["netbsd"]], "env": []})
        p = subprocess.Popen([BASH, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.done, p)
        out = p.stdout.read()
        self.assertEqual(p.wait(20), 0)
        self.assertIn("img-lanes.sh exit 0", out)
        self.assertNotIn("let go", out)


class Farm(unittest.TestCase):
    """img_round.py run end to end over five builders and choubox."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i8-farm-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.farm = self.t
        wr(os.path.join(self.t, "bin", "ssh"), FAKE_SSH, 0o755)
        wr(os.path.join(self.t, "stick", "img-lanes.sh"), FAKE_LANES, 0o755)
        wr(os.path.join(self.t, "stick", "img-xcopy.sh"), FAKE_XCOPY, 0o755)
        for h in DESTS:
            self.host(h)
        self.sets = {"bko-bin": sha(rd(self.p("chopin", "work/bko-bin/SHA256SUMS"))),
                     "holt-bin": sha(rd(self.p("chopin", "work/holt-bin/SHA256SUMS")))}
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = os.path.join(self.t, "bin") + ":" + self.old_path
        os.environ["FARM"] = self.farm
        self.addCleanup(self.restore)
        self.said = []

    def restore(self):
        os.environ["PATH"] = self.old_path
        os.environ.pop("FARM", None)

    def p(self, h, rel):
        return os.path.join(self.farm, h, rel)

    def host(self, h, holt="holt"):
        b, hb = self.p(h, "work/bko-bin"), self.p(h, "work/holt-bin")
        sums, hsums = "", ""
        for p in PLATS:
            for n in ("restic-bko", "rustic-bko"):
                wr(os.path.join(b, p, n), f"{n} {p}\n", 0o755)
                sums += f"{sha(f'{n} {p}' + chr(10))}  {p}/{n}\n"
            wr(os.path.join(hb, p, "bko-holt"), f"{holt} {p}\n", 0o755)
            hsums += f"{sha(f'{holt} {p}' + chr(10))}  {p}/bko-holt\n"
        wr(os.path.join(b, "SHA256SUMS"), sums)
        wr(os.path.join(hb, "SHA256SUMS"), hsums)
        os.makedirs(self.p(h, "work/images"), exist_ok=True)

    def facts(self, hs):
        return {h: TR.host(sets=self.sets) for h in hs}

    def ctx(self, **kw):
        c = {"table": Table, "table_rows": TR.TABLE, "run": "r8", "rdir": os.path.join(self.t, "mac", "r8"),
             "builders": list(TR.BUILDERS), "ref": "chopin", "opts": dict(TR.OPTS), "today": TR.TODAY,
             "remote_env": [], "dests": list(DESTS), "jobs": 4, "lock_wait": 1, "fanout_only": False,
             "say": self.said.append, "gather": self.facts, "rows": [], "poll": 1, "watch_every": 1}
        c.update(kw)
        return c

    def calls(self, h):
        try:
            return [x.split("|") for x in rd(self.p(h, "lanes.calls")).splitlines()]
        except OSError:
            return []

    def test_a_full_round_builds_where_planned_and_every_host_holds_the_same_bytes(self):
        self.assertEqual(R.do_run(self.ctx()), 0, "\n".join(self.said))
        plan = jl(os.path.join(self.t, "mac", "r8", "plan.json"))
        rnd = jl(os.path.join(self.t, "mac", "r8", "round.json"))
        built = {}
        for h in TR.BUILDERS:
            for run, lanes, bb, hb, builder, held in self.calls(h):
                self.assertEqual((run, bb, hb, held), ("r8", self.sets["bko-bin"], self.sets["holt-bin"], "held"))
                for p in lanes.split():
                    built[p] = h
                if "netbsd" in lanes:
                    self.assertEqual(json.loads(builder), TR.dig("netbsd")["base"]["inputs"]["builder"])
        self.assertEqual(built, {p: plan["platforms"][p]["host"] for p in PLATS})
        for p in PLATS:
            self.assertEqual(rnd["fanout"][p], {h: "ok" for h in DESTS}, p)
            for h in DESTS:
                self.assertEqual(rd(self.p(h, f"work/images/{p}/SHA256SUMS")),
                                 rd(self.p(built[p], f"work/images/{p}/SHA256SUMS")))
        self.assertTrue(any("6 identical" in x for x in self.said))
        # The locks went with the round.
        for h in DESTS:
            with open(self.p(h, "work/images/.round.lock")) as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(rd(self.p(h, "work/images/.round.lock")), "")
        self.assertTrue(os.path.isfile(os.path.join(self.t, "mac", "r8", "summary.txt")))

    def test_a_host_another_round_holds_refuses_the_round(self):
        with open(self.p("cage", "work/images/.round.lock"), "a") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            with self.assertRaises(R.Refused) as cm:
                R.do_run(self.ctx())
        self.assertIn("cage: a promotion or an img-refresh-holt.sh", str(cm.exception))
        self.assertEqual([self.calls(h) for h in TR.BUILDERS], [[]] * 5)
        self.assertFalse(os.path.exists(os.path.join(self.t, "mac", "r8", "plan.json")))
        R.MacLock(os.path.join(self.t, "mac"), "next").release()   # the Mac's lock was let go

    def test_a_failed_unit_is_not_fanned_out(self):
        R.do_run(self.ctx(gather=lambda hs: {h: TR.host(sets=self.sets) for h in hs}, dests=[]))  # (where netbsd goes)
        h = jl(os.path.join(self.t, "mac", "r8", "plan.json"))["platforms"]["netbsd"]["host"]
        wr(self.p(h, "fail-netbsd"), "")
        os.unlink(os.path.join(self.t, "mac", "r8", "plan.json"))
        self.assertEqual(R.do_run(self.ctx(run="r9", rdir=os.path.join(self.t, "mac", "r9"))), 1)
        rnd = jl(os.path.join(self.t, "mac", "r9", "round.json"))
        self.assertFalse(rnd["units"]["netbsd"]["ok"])
        self.assertNotIn("netbsd", rnd["fanout"])
        self.assertTrue(any("FAILED: done.exit 1: netbsd FAIL: make-img-bsd" in x for x in self.said))
        self.assertEqual(rnd["fanout"]["linux"], {x: "ok" for x in DESTS})

    def test_a_dest_whose_holt_bin_is_neither_is_not_copied_to(self):
        self.host("choubox", holt="older holt")
        self.assertEqual(R.do_run(self.ctx()), 1)
        rnd = jl(os.path.join(self.t, "mac", "r8", "round.json"))
        for p in PLATS:
            self.assertIn("could roll it back", rnd["fanout"][p]["choubox"])
        self.assertEqual(os.listdir(self.p("choubox", "work/images")), [".round.lock"])

    def test_a_copy_that_differs_is_said(self):
        wr(self.p("czerny", "corrupt-haiku"), "")
        self.assertEqual(R.do_run(self.ctx()), 1)
        rnd = jl(os.path.join(self.t, "mac", "r8", "round.json"))
        self.assertTrue(rnd["fanout"]["haiku"]["czerny"].startswith("BAD: bko-haiku.img is "))

    def test_an_unreached_dest_is_left_out_and_said(self):
        wr(self.p("choubox", "down"), "")
        self.assertEqual(R.do_run(self.ctx()), 0)
        self.assertTrue(any(x.startswith("  choubox: no round lock (ssh: connect") for x in self.said), self.said)

    def test_a_lost_build_ssh_is_polled_to_its_end(self):
        wr(self.p("chopin", "drop-build"), "")
        self.assertEqual(R.do_run(self.ctx()), 0, "\n".join(self.said))
        log = rd(os.path.join(self.t, "mac", "r8", "chopin.log"))
        self.assertIn("the ssh to chopin was lost; its lanes go on", log)
        self.assertIn("img-lanes.sh exit 0 (read after the ssh was lost)", log)

    def test_a_lost_lock_ssh_is_taken_again(self):
        wr(self.p("cage", "drop-hold"), "")
        wr(self.p("cage", "slow"), "")
        self.assertEqual(R.do_run(self.ctx()), 0, "\n".join(self.said))
        ev = jl(os.path.join(self.t, "mac", "r8", "round.json"))["lock_events"]
        self.assertEqual([(e["host"], e["retaken"]) for e in ev], [("cage", True)])
        self.assertTrue(any("cage: its round lock's ssh ended at" in x and "taken again" in x for x in self.said))
        with open(self.p("cage", "work/images/.round.lock")) as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_a_name_that_ran_does_not_run_again(self):
        # A RUN is one round: its units count by provenance built.run, so a second run of r8
        # whose unit failed at its start (exit 3, 4, 5) would take the first run's image, built
        # from the first run's sets, for its own.  Its fan-out may still be finished.
        self.assertEqual(R.do_run(self.ctx(dests=[])), 0, "\n".join(self.said))
        n = {h: len(self.calls(h)) for h in TR.BUILDERS}
        with self.assertRaises(R.Refused) as cm:
            R.do_run(self.ctx())
        self.assertIn("r8 has run already", str(cm.exception))
        self.assertEqual({h: len(self.calls(h)) for h in TR.BUILDERS}, n)
        R.MacLock(os.path.join(self.t, "mac"), "next").release()   # the Mac's lock was let go
        self.assertEqual(R.do_run(self.ctx(fanout_only=True)), 0, "\n".join(self.said))
        self.assertEqual(jl(os.path.join(self.t, "mac", "r8", "fanout.json"))["fanout"]["haiku"],
                         {h: "ok" for h in DESTS})

    def test_fanout_alone_after_a_run(self):
        R.do_run(self.ctx(dests=TR.BUILDERS[:1]))
        self.assertFalse(os.path.isdir(self.p("cage", "work/images/linux")) and
                         jl(os.path.join(self.t, "mac", "r8", "plan.json"))["platforms"]["linux"]["host"] != "cage")
        self.assertEqual(R.do_run(self.ctx(fanout_only=True)), 0, "\n".join(self.said))
        rnd = jl(os.path.join(self.t, "mac", "r8", "fanout.json"))
        self.assertEqual(rnd["fanout"]["haiku"], {h: "ok" for h in DESTS})


@unittest.skipUnless(LINUX, "needs flock and setsid (a Linux host)")
class HostScripts(unittest.TestCase):
    """img-refresh-holt.sh refuses a held round lock; img-lanes.sh's units and environment."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i8-host-"))
        self.addCleanup(shutil.rmtree, self.t, True)

    def test_img_refresh_holt_refuses_while_a_round_holds_the_host(self):
        w, hr, code = os.path.join(self.t, "w"), os.path.join(self.t, "state"), "c" * 64
        wr(os.path.join(hr, "holt-bin", code, "netbsd", "bko-holt"), "new\n", 0o755)
        wr(os.path.join(w, "holt-bin", "netbsd", "bko-holt"), "old\n", 0o755)
        e = dict(os.environ, BKO_WORK=w, T2_HOST_ROOT=hr, BKO_IMG_DIR=os.path.join(w, "images"))
        h = subprocess.Popen([sys.executable, "-I", "-B", "-c", R.HOLD_PY, "r8", "mac", "0", "0"], env=e, text=True,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(Locks.done, h)
        self.assertTrue(h.stdout.readline().startswith("HELD "))
        argv = [BASH, os.path.join(STICK, "img-refresh-holt.sh"), "b" * 40, code, "netbsd"]
        r = subprocess.run(argv, env=e, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("image round r8 (img-round.sh from mac", r.stderr)
        self.assertEqual(rd(os.path.join(w, "holt-bin", "netbsd", "bko-holt")), "old\n")
        h.stdin.close()
        h.wait(10)
        r = subprocess.run(argv, env=e, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(os.path.join(w, "holt-bin", "netbsd", "bko-holt")), "new\n")

    def lanes(self, *args, **env):
        s = os.path.join(self.t, "stick")
        if not os.path.isdir(s):
            os.makedirs(s)
            shutil.copy2(os.path.join(STICK, "img-lanes.sh"), os.path.join(s, "img-lanes.sh"))
            wr(os.path.join(s, "img-one.sh"), "#!/usr/bin/env bash\n"
               f'echo "$*|$BKO_IMG_RUN|${{BKO_PLAN_BUILDER:-}}|${{BKO_PLAN_BKO_BIN:-}}" >>{self.t}/one.calls\n'
               'p=${@: -1}; mkdir -p "$BKO_IMG_DIR/$p"; echo 0 >"$BKO_IMG_DIR/$p/done.exit"\n', 0o755)
            wr(os.path.join(self.t, "cmp-run.sh"), '#!/bin/sh\nshift 3\nexec "$@"\n', 0o755)
        ram = os.path.join("/dev/shm", os.path.basename(self.t))
        os.makedirs(ram, exist_ok=True)
        self.addCleanup(shutil.rmtree, ram, True)
        e = dict(os.environ, BKO_WORK=os.path.join(self.t, "w"), BKO_IMG_DIR=os.path.join(self.t, "w", "images"),
                 CMP_RUN=os.path.join(self.t, "cmp-run.sh"), BKO_LANES_RAM="/dev/shm", T2_HOST_ROOT="/bkoscratch/test2")
        os.makedirs(e["BKO_IMG_DIR"], exist_ok=True)
        e.update(env)
        return subprocess.run([BASH, os.path.join(s, "img-lanes.sh")] + list(args), env=e, capture_output=True, text=True)

    def test_img_lanes_passes_the_run_the_refresh_and_the_plan(self):
        par = "f" * 64
        r = self.lanes("--run", "r8", f"netbsd linux@{par}", BKO_PLAN_BUILDER_netbsd='{"vm":"netbsd11"}',
                       BKO_PLAN_BKO_BIN="b" * 64)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = sorted(rd(os.path.join(self.t, "one.calls")).splitlines())
        self.assertEqual(calls, [f"--refresh-from {par} linux|r8||{'b' * 64}",
                                 f'netbsd|r8|{{"vm":"netbsd11"}}|{"b" * 64}'])

    def test_img_lanes_refuses_a_bad_unit(self):
        r = self.lanes("--run", "r8", "netbsd linux@abc")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.t, "one.calls")))


class PlanSets(unittest.TestCase):
    """img-one.sh refuses at its start when the host's sets are not the plan's (C10)."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i8-one-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.w = os.path.join(self.t, "work")
        self.s = os.path.join(self.t, "stick")
        self.path = os.path.join(self.t, "path")
        wr(os.path.join(self.w, "bko-bin", "netbsd", "rustic-bko"), "rustic\n", 0o755)
        wr(os.path.join(self.w, "bko-bin", "BUILD-INFO"), "bko-bin set\n")
        wr(os.path.join(self.w, "bko-bin", "SHA256SUMS"), "sums of bko-bin\n")
        wr(os.path.join(self.w, "holt-bin", "netbsd", "bko-holt"), "holt\n", 0o755)
        wr(os.path.join(self.w, "holt-bin", "netbsd", "COMMIT"), "ab" * 20 + "\n")
        wr(os.path.join(self.w, "holt-bin", "SHA256SUMS"), "sums of holt-bin\n")
        wr(os.path.join(self.s, "make-img-bsd.sh"),
           '#!/bin/sh\nif [ "$1" = --test ]; then d=$3; else d=$2; fi\necho image >"$d/x.img"\necho "Wrote $d/x.img"\n', 0o755)
        wr(os.path.join(self.s, "boot-test.py"), "print('  ok   tools  all')\n")
        for f in ("img_inputs.py", "img-inputs.tsv", "img-swap.tsv", "boot-modes.sh"):
            shutil.copy2(os.path.join(STICK, f), os.path.join(self.s, f))
        wr(os.path.join(self.path, "flock"), "#!/bin/sh\nexit 0\n", 0o755)
        self.d = os.path.join(self.w, "images", "netbsd")
        wr(os.path.join(self.d, "done.exit"), "0\n")
        wr(os.path.join(self.d, "old.img"), "old\n")

    def run_one(self, **env):
        e = dict(os.environ, BKO_WORK=self.w, S=self.s, PATH=self.path + ":" + os.environ["PATH"],
                 BKO_PLAN_BKO_BIN=sha("sums of bko-bin\n"), BKO_PLAN_HOLT_BIN=sha("sums of holt-bin\n"), BKO_IMG_RETAIN="0")
        e.update(env)
        return subprocess.run([BASH, os.path.join(STICK, "img-one.sh"), "netbsd"], env=e, capture_output=True, text=True)

    def test_a_set_changed_under_the_unit_refuses_it_and_moves_nothing(self):
        for k in ("BKO_PLAN_BKO_BIN", "BKO_PLAN_HOLT_BIN"):
            r = self.run_one(**{k: "0" * 64})
            self.assertEqual(r.returncode, 5, r.stderr)
            self.assertRegex(r.stderr, r"SHA256SUMS is [0-9a-f]{12}, the plan's is 000000000000: ")
            self.assertIn("the host's sets changed since the plan (C10); not built", r.stderr)
            self.assertEqual(sorted(os.listdir(self.d)), ["done.exit", "old.img"])

    def test_the_plans_sets_build_and_the_run_reaches_the_provenance(self):
        r = self.run_one(BKO_IMG_RUN="r8")
        self.assertEqual(r.returncode, 0, r.stderr + rd(os.path.join(self.d, "result.txt")))
        pv = jl(os.path.join(self.d, "provenance.json"))
        self.assertEqual(pv["built"]["run"], "r8")


if __name__ == "__main__":
    unittest.main()
