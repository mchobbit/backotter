#!/usr/bin/env python3
"""The VM reference of the platforms that ship their cross-built bko-holt, and E2 on a
holt-change round (docs/design/img-speed.md §8.2, §8.3, D11; I12).  On the Mac, from a checkout:

  holt-ref.py run [--all-guests] [--repin] [--name NAME] [--lock-wait T] PLAT...
        E2 steps 0-3 for each PLAT, from this checkout's t2 and holt, under private roots
        (MAC_ROOT/holt-ref/NAME/t2mac{,2}, /bkoscratch/holt-ref/NAME/t2host{,2}; NAME defaults
        to ref-YYYYMMDDTHHMM):
          A      a HoltTest2 run with T2_HOLT_BUILD=vm: each PLAT's VM build, the reference,
                 and its cross check build beside it (holt.py's)
          e2     holt-e2.sh steps 0-2 on the host that made the cross build, against the VM build
          set    the cross builds as a set (SHA256SUMS) on every default amd64 host
          B      the same guests again on that set (--holt-bin), the same guest generations
          dumps  holt_e2.py fetch and dumps: both PASS, and the dumps equal by judge.py
        The guests: one per platform (REF_GUEST, the weekly reference's "step 3 on one guest"),
        or with --all-guests every holt spec entry of the platform (E2 on a holt-change round,
        which D11 asks of two consecutive rounds before a platform switches).  The result is
        written to MAC_ROOT/holt-ref/PLAT.json (t2lib/holtbuild.py: a FAIL sends the platform back
        to its VM build until a run passes).  With --repin, a PASS on a kind other than
        testvm/test2/holt/builds.json's sets that kind there (the toolchain or the sysroot's guest
        moved; commit it).  MAC_ROOT: T2_REF_MAC_ROOT, else T2_MAC_ROOT, else ~/backotter-work/test2.
  holt-ref.py due
        each switched platform's reference: when it last passed, and whether it is due.

Exit 0 when every PLAT passed (due: none due), 1 when one failed (due: one is due), 2 on a
usage error.  Runs go one platform set at a time; the runs' own logs are under
MAC_ROOT/holt-ref/NAME/.
"""

import datetime
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
T2D = os.path.join(REPO, "testvm", "test2")
sys.path.insert(0, T2D)
from t2lib import holtbuild, keys  # noqa: E402
from t2lib import hosts as ht  # noqa: E402

REF_GUEST = {"freebsd": "freebsd14", "netbsd": "netbsd11", "illumos": "omnios58", "openbsd": "openbsd8",
             "dragonfly": "dragonfly6"}


def say(*a):
    print(*a, flush=True)


def guests_of(plat, every):
    """PLAT's guests: REF_GUEST's, or EVERY: each holt spec entry whose plat is PLAT."""
    if not every:
        return [REF_GUEST[plat]]
    sf = keys.Specs(keys.TreeSource(REPO)).get("holt")
    out = []
    for g in sf.names():
        try:
            if sf.entry(g).get("plat") == plat:
                out.append(g)
        except KeyError:
            continue
    return out


def mac_root():
    return os.path.expanduser(os.environ.get("T2_REF_MAC_ROOT") or os.environ.get("T2_MAC_ROOT")
                              or "~/backotter-work/test2")


def ssh(host, cmd, timeout=None):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=30",
                           ht.ssh(host), cmd], capture_output=True, text=True, timeout=timeout)


class Ref:
    def __init__(self, name, plats, guests, lock_wait):
        self.name, self.plats, self.guests, self.lock_wait = name, plats, guests, lock_wait
        self.d = os.path.join(mac_root(), holtbuild.REF_DIR, name)
        self.h = f"/bkoscratch/holt-ref/{name}"
        self.all = [g for p in plats for g in guests[p]]
        os.makedirs(self.d, exist_ok=True)

    def t2(self, tag, extra, env_extra):
        env = dict(os.environ, T2_MAC_ROOT=f"{self.d}/t2mac{tag}", T2_HOST_ROOT=f"{self.h}/t2host{tag}", **env_extra)
        env.pop("T2_REF_MAC_ROOT", None)
        with open(f"{self.d}/run{tag or 'A'}.log", "a") as log:
            rc = subprocess.run([os.path.join(T2D, "t2"), "HoltTest2", *self.all, *extra, "--lock-wait", self.lock_wait],
                                env=env, stdout=log, stderr=subprocess.STDOUT, cwd=T2D).returncode
        say(f"run {tag or 'A'}: exit {rc}")
        return rc

    def newest(self, tag):
        rd = f"{self.d}/t2mac{tag}/runs"
        runs = sorted(r for r in (os.listdir(rd) if os.path.isdir(rd) else [])
                      if r.startswith("t2h-") and os.path.exists(f"{rd}/{r}/holt.json"))
        if not runs:
            return None, {}
        with open(f"{rd}/{runs[-1]}/holt.json", encoding="utf-8") as f:
            return runs[-1], json.load(f)

    def step_e2(self, code, cross):
        res = {}

        def one(p):
            b = cross.get(p) or {}
            if b.get("rc") != 0 or not isinstance(b.get("info"), dict):
                res[p] = (False, f"no cross check build in run A (exit {b.get('rc')})")
                return
            h, src = b["host"], b["info"]["src"]
            cdir = f"{self.h}/t2host/holt-bin/{code}"
            e2 = os.path.join(os.path.dirname(src), "testvm/holttest1d/holt-e2.sh")
            out = f"{self.h}/e2/{p}"
            r = ssh(h, f"rm -rf {out}; mkdir -p {self.h}/e2; env HOLT_SRC={src} HOLT_RESERVE=3 {e2} --reserved "
                       f"{self.name} {p} {cdir}/{p}/bko-holt {cdir}/cross/{p}/bko-holt {out} >{self.h}/e2/{p}.log 2>&1;"
                       f" echo exit $?; cat {out}/e2.txt", timeout=4 * 3600)
            lines = r.stdout.strip().splitlines()
            res[p] = ("exit 0" in lines[:1], f"{h}: " + " | ".join(lines))
            os.makedirs(f"{self.d}/e2/{p}", exist_ok=True)
            subprocess.run(["rsync", "-a", f"{ht.ssh(h)}:{out}/", f"{self.d}/e2/{p}/"], check=False)

        ts = [threading.Thread(target=one, args=(p,)) for p in self.plats]
        [t.start() for t in ts]
        [t.join() for t in ts]
        return res

    def step_set(self, code, cross):
        loc = f"{self.d}/crossset"
        sums = []
        for p in self.plats:
            b = cross[p]
            os.makedirs(f"{loc}/{p}", exist_ok=True)
            subprocess.run(["rsync", "-a", f"{ht.ssh(b['host'])}:{self.h}/t2host/holt-bin/{code}/cross/{p}/bko-holt",
                            f"{loc}/{p}/"], check=True)
            sha = subprocess.run(["shasum", "-a", "256", f"{loc}/{p}/bko-holt"], capture_output=True,
                                 text=True).stdout.split()[0]
            if sha != b["info"]["sha256"]:
                raise SystemExit(f"{p}: the copied cross build is {sha[:12]}, run A's {b['info']['sha256'][:12]}")
            sums.append(f"{sha}  {p}/bko-holt\n")
        with open(f"{loc}/SHA256SUMS", "w") as f:
            f.write("".join(sorted(sums, key=lambda s: s.split()[1])))
        for h in ht.names(default=True, arch="amd64"):
            ssh(h, f"mkdir -p {self.h}")
            r = subprocess.run(["rsync", "-a", "--delete", f"{loc}/", f"{ht.ssh(h)}:{self.h}/crossset/"])
            say(f"cross set to {h}: rsync exit {r.returncode}")

    def step_dumps(self, run_a, run_b):
        e2py = os.path.join(HERE, "holt_e2.py")
        for tag, run in (("A", run_a), ("B", run_b)):
            subprocess.run([sys.executable, e2py, "fetch", f"{self.d}/step3/{tag}", run, *self.all], check=False)
        res = {}
        for p in self.plats:
            r = subprocess.run([sys.executable, e2py, "dumps", p, f"{self.d}/step3/A", f"{self.d}/step3/B",
                                *self.guests[p]], capture_output=True, text=True)
            with open(f"{self.d}/step3-{p}.txt", "w") as f:
                f.write(r.stdout + r.stderr)
            res[p] = (r.returncode == 0, (r.stdout.strip().splitlines() or ["no output"])[-1])
        return res


def cmd_run(args):
    every = repin = False
    name, lock_wait, plats = None, "4h", []
    it = iter(args)
    for a in it:
        if a == "--all-guests":
            every = True
        elif a == "--repin":
            repin = True
        elif a == "--name":
            name = next(it, "")
            if not name or not name.replace("-", "").replace(".", "").isalnum():
                return usage("--name NAME: a plain name")
        elif a == "--lock-wait":
            lock_wait = next(it, "4h")
        elif a.startswith("-"):
            return usage(f"no option {a}")
        else:
            plats.append(a)
    bad = [p for p in plats if p not in REF_GUEST]
    if not plats or bad:
        return usage(f"PLAT: some of {', '.join(REF_GUEST)}" + (f", not {', '.join(bad)}" if bad else ""))
    name = name or "ref-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M")
    ref = Ref(name, plats, {p: guests_of(p, every) for p in plats}, lock_wait)
    say(f"holt-ref {name}: {', '.join(plats)}, guests {' '.join(ref.all)} ({ref.d})")
    ref.t2("", [], {"T2_HOLT_BUILD": "vm"})
    run_a, ha = ref.newest("")
    if not run_a:
        return finish(ref, plats, {p: (False, "run A made no run") for p in plats}, None, None, {}, repin)
    code, cross = ha.get("code"), ha.get("cross") or {}
    verdict = {p: [True, []] for p in plats}
    for p, (ok, why) in ref.step_e2(code, cross).items():
        verdict[p][0] &= ok
        verdict[p][1].append(f"steps 0-2: {why}")
    live = [p for p in plats if verdict[p][0]]
    run_b = None
    if live:
        ref.plats = live
        ref.all = [g for p in live for g in ref.guests[p]]
        ref.step_set(code, cross)
        ref.t2("2", ["--holt-bin", f"{ref.h}/crossset"], {})
        run_b, _ = ref.newest("2")
        for p, (ok, why) in (ref.step_dumps(run_a, run_b) if run_b else
                             {p: (False, "run B made no run") for p in live}).items():
            verdict[p][0] &= ok
            verdict[p][1].append(f"step 3: {why}")
    return finish(ref, plats, {p: (v[0], "; ".join(v[1])) for p, v in verdict.items()}, run_a, run_b, cross, repin,
                  code)


def finish(ref, plats, verdict, run_a, run_b, cross, repin, code=None):
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    builds = holtbuild.load(keys.TreeSource(REPO))
    rc = 0
    for p in plats:
        ok, why = verdict[p]
        info = (cross.get(p) or {}).get("info") or {}
        rec = {"when": when, "result": "PASS" if ok else "FAIL", "kind": holtbuild.kind_of(info) if info else None,
               "code": code, "cross_sha256": info.get("sha256"), "runs": [r for r in (run_a, run_b) if r],
               "guests": ref.guests[p], "name": ref.name, "why": why}
        holtbuild.write_ref(mac_root(), p, rec)
        say(f"holt-ref {p}: {rec['result']} ({rec['kind']}): {why}")
        rc |= 0 if ok else 1
        pinned = ((builds.get("cross") or {}).get(p) or {}).get("kind")
        if ok and repin and pinned and rec["kind"] != pinned:
            builds["cross"][p]["kind"] = rec["kind"]
            path = os.path.join(REPO, holtbuild.BUILDS)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(builds, f, indent=1, sort_keys=True)
                f.write("\n")
            say(f"holt-ref {p}: {holtbuild.BUILDS} repinned from {pinned} to {rec['kind']}: commit it")
    return rc


def cmd_due():
    builds = holtbuild.load(keys.TreeSource(REPO))
    rc = 0
    for p in sorted(builds.get("cross") or {}):
        ref = holtbuild.read_ref(mac_root(), p)
        why = holtbuild.ref_due(builds, ref, p)
        last = ref.get("last") or {}
        say(f"{p}: last {last.get('result') or 'none'} {str(last.get('when') or '')[:10]}; "
            + (f"due: {why}" if why else "not due"))
        rc |= 1 if why or last.get("result") == "FAIL" else 0
    return rc


def usage(why):
    print(f"holt-ref: {why}\n" + __doc__.split("\n\n")[1], file=sys.stderr)
    return 2


def main(argv):
    if argv[:1] == ["run"]:
        return cmd_run(argv[1:])
    if argv == ["due"]:
        return cmd_due()
    return usage("run or due")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
