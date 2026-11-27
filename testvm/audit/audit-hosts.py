#!/usr/bin/env python3
"""Which farm hosts can run the audits' jobs, and how rust-lint.sh spreads its targets
over them (docs/design/img-speed.md F10: rust-lint.sh, stock-baseline.sh and fork-tests.sh
took Chopin and Cinqcent by name, the hosts that happened to have the toolchains).

Usage, on the Mac (rust-lint.sh and stock-baseline.sh run it; python3 -I -B):
    audit-hosts.py probe
        the probe's shell, for `ssh HOST sh -s`: it reads only (rustup's lists, PATH),
        and prints KEY VALUE lines, "end" last
    audit-hosts.py lint-plan --probe-dir DIR --hosts H,H [--targets T,T] [--spread N]
                             [--tests] [--toolchain TC] [--floor TC] [--nightly TC]
        rust-lint.sh's deal: one line "HOST ROLE TARGETS" a host it uses (ROLE primary,
        once: the host that also runs fmt, doc, holt's parser pass, its FLOOR check,
        --tests and rustic; or clippy: clippy over TARGETS only), the primary first.
        --hosts are the candidates best first (pick-host's ranking), DIR/HOST.txt each
        one's probe.  A host short of what a role needs for a target is not dealt that
        target; each one left out says why on stderr, with the rustup commands that
        would fix it.  Exit 1 when no host can be the primary or a target has no host
    audit-hosts.py stock-need --probe FILE NAME [--toolchain TC]
        whether that host can run audit-host.sh's NAME (restic: a Go; rustic_core,
        rustic: rustup's TC): exit 0, or 1 and why on stderr

A role needs (rust-lint-host.sh's header): every host TOOLCHAIN with clippy, flock,
/usr/bin/time and cc; the primary also TOOLCHAIN's rustfmt and the FLOOR toolchain;
a target with rustup's std (linux-gnu, the musl ones, freebsd, netbsd, illumos) its
rust-std in TOOLCHAIN; a build-std target (openbsd, dragonfly, haiku) NIGHTLY with
clippy and rust-src.

The deal: the primary is the best host that can be it and run linux-gnu (its once-only
steps are on that target).  Up to SPREAD - 1 more hosts, the best that can take one of
the targets left, share them: each target, in order, goes to the least-loaded host that
can take it (ties: the better host), the primary starting with a load of ONCE_LOAD (its
once-only steps, ONCE_LOAD_TESTS more with --tests) and every target counting 1.
Nothing is started: tests check it with fixture probes (testvm/test2/tests/
test_audit_hosts.py).
"""

import argparse
import os
import sys

HOST_TARGET = "x86_64-unknown-linux-gnu"
BUILD_STD = ("x86_64-unknown-openbsd", "x86_64-unknown-dragonfly", "x86_64-unknown-haiku")
TOOLCHAIN, FLOOR, NIGHTLY = "1.94.1", "1.85.1", "nightly-2026-10-31"
TOOLS = ("flock", "time", "cc")
ONCE_LOAD, ONCE_LOAD_TESTS = 3, 2

PROBE_SH = r'''
export PATH="$HOME/.cargo/bin:$PATH"
if command -v rustup >/dev/null 2>&1; then
    for tc in $(rustup toolchain list 2>/dev/null | sed 's/ .*//; s/-x86_64-unknown-linux-gnu$//'); do
        echo "tc $tc"
        rustup +"$tc" target list --installed 2>/dev/null | sed "s/^/target $tc /"
        rustup +"$tc" component list --installed 2>/dev/null |
            sed -n "s/^\(clippy\|rustfmt\|rust-src\)\(-x86_64-unknown-linux-gnu\)\{0,1\}$/comp $tc \1/p"
    done
fi
g=$(command -v go 2>/dev/null) || g=$(ls -d "$HOME"/sdk/go*/bin/go 2>/dev/null | sort -V | tail -n 1)
[ -n "$g" ] && echo "go $g"
command -v flock >/dev/null 2>&1 && echo "tool flock"
[ -x /usr/bin/time ] && echo "tool time"
command -v cc >/dev/null 2>&1 && echo "tool cc"
echo "machine $(uname -m)"
echo end
'''


def parse(text):
    """A probe's output -> {"tcs": {TC: {"targets": set, "comps": set}}, "go": str|None,
    "tools": set, "machine": str|None, "complete": bool}."""
    p = {"tcs": {}, "go": None, "tools": set(), "machine": None, "complete": False}
    for line in text.splitlines():
        w = line.split()
        if not w:
            continue
        if w[0] == "tc" and len(w) == 2:
            p["tcs"].setdefault(w[1], {"targets": set(), "comps": set()})
        elif w[0] in ("target", "comp") and len(w) == 3:
            t = p["tcs"].setdefault(w[1], {"targets": set(), "comps": set()})
            t["targets" if w[0] == "target" else "comps"].add(w[2])
        elif w[0] == "go" and len(w) >= 2:
            p["go"] = w[1]
        elif w[0] == "tool" and len(w) == 2:
            p["tools"].add(w[1])
        elif w[0] == "machine" and len(w) == 2:
            p["machine"] = w[1]
        elif w == ["end"]:
            p["complete"] = True
    return p


def _tc_need(p, tc, comps=(), targets=()):
    """What P lacks of toolchain TC with COMPS and TARGETS: [(what, rustup command)]."""
    t = p["tcs"].get(tc)
    if t is None:
        cmd = f"rustup toolchain install {tc} --profile minimal"
        if comps:
            cmd += " -c " + ",".join(comps)
        if targets:
            cmd += " -t " + ",".join(targets)
        return [(f"no toolchain {tc}", cmd)]
    out = [(f"no {c} in {tc}", f"rustup +{tc} component add {c}") for c in comps if c not in t["comps"]]
    out += [(f"no {x} std in {tc}", f"rustup +{tc} target add {x}") for x in targets if x not in t["targets"]]
    return out


def base_needs(p, tc=TOOLCHAIN):
    """Every rust-lint host's: a complete amd64 probe, TOOLCHAIN's clippy, the tools."""
    if not p["complete"]:
        return [("no answer to the probe (ssh failed or cut short)", None)]
    out = []
    if p["machine"] not in (None, "x86_64"):
        out.append((f"machine {p['machine']}, not x86_64", None))
    out += _tc_need(p, tc, ("clippy",))
    out += [(f"no {x}", None) for x in TOOLS if x not in p["tools"]]
    return out


def primary_needs(p, tc=TOOLCHAIN, floor=FLOOR):
    return _tc_need(p, tc, ("rustfmt",)) + _tc_need(p, floor)


def target_needs(p, target, tc=TOOLCHAIN, nightly=NIGHTLY):
    if target in BUILD_STD:
        return _tc_need(p, nightly, ("clippy", "rust-src"))
    if target == HOST_TARGET:
        return []          # the toolchain's own std (base_needs has the toolchain)
    return _tc_need(p, tc, targets=(target,))


def _say(host, what, needs):
    for why, cmd in needs:
        print(f"audit-hosts: {host}: {what}: {why}" + (f" (there: {cmd})" if cmd else ""), file=sys.stderr)


def lint_plan(probes, hosts, targets, spread=3, tests=False, tc=TOOLCHAIN, floor=FLOOR, nightly=NIGHTLY):
    """[(HOST, ROLE, [TARGET...])], the primary first; probes {HOST: parse()}, hosts best
    first.  Raises ValueError (its message: why) when there is no primary or a target
    has no host.  Reasons for hosts left out go to stderr."""
    if spread < 1:
        raise ValueError("--spread must be 1 or more")
    ok = {}
    for h in hosts:
        p = probes.get(h) or parse("")
        n = base_needs(p, tc)
        if n:
            _say(h, "not used", n)
        else:
            ok[h] = p
    primary = None
    for h in ok:
        n = primary_needs(ok[h], tc, floor) + target_needs(ok[h], HOST_TARGET, tc, nightly)
        if not n:
            primary = h
            break
        _say(h, "not the primary (fmt, doc, the floor check)", n)
    if primary is None:
        raise ValueError("no host can be the primary: " + (", ".join(hosts) or "no candidates")
                         + " (see the reasons above)")
    rest = [t for t in targets if t != HOST_TARGET]
    others = []
    for h in ok:
        if h == primary or len(others) >= spread - 1:
            continue
        can = [t for t in rest if not target_needs(ok[h], t, tc, nightly)]
        if can:
            others.append(h)
        else:
            for t in rest:
                _say(h, f"no {t}", target_needs(ok[h], t, tc, nightly))
    deal = {primary: [HOST_TARGET] if HOST_TARGET in targets else []}
    load = {primary: ONCE_LOAD + (ONCE_LOAD_TESTS if tests else 0) + len(deal[primary])}
    for h in others:
        deal[h], load[h] = [], 0
    order = [primary] + others
    for t in rest:
        can = [h for h in order if not target_needs(ok[h], t, tc, nightly)]
        if not can:
            for h in order:
                _say(h, f"no {t}", target_needs(ok[h], t, tc, nightly))
            raise ValueError(f"no host for target {t}")
        h = min(can, key=lambda x: (load[x], order.index(x)))
        deal[h].append(t)
        load[h] += 1
    return [(h, "primary" if h == primary else "clippy", deal[h]) for h in order if h == primary or deal[h]]


def stock_need(p, name, tc=TOOLCHAIN):
    if not p["complete"]:
        return [("no answer to the probe", None)]
    if name == "restic":
        return [] if p["go"] else [("no go (PATH or ~/sdk/go*/bin/go)", None)]
    if name in ("rustic_core", "rustic"):
        return _tc_need(p, tc)
    raise ValueError(f"unknown NAME {name}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="audit-hosts.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    lp = sub.add_parser("lint-plan")
    lp.add_argument("--probe-dir", required=True)
    lp.add_argument("--hosts", required=True)
    lp.add_argument("--targets", default=HOST_TARGET)
    lp.add_argument("--spread", type=int, default=3)
    lp.add_argument("--tests", action="store_true")
    sn = sub.add_parser("stock-need")
    sn.add_argument("--probe", required=True)
    sn.add_argument("name")
    for p in (lp, sn):
        p.add_argument("--toolchain", default=TOOLCHAIN)
    lp.add_argument("--floor", default=FLOOR)
    lp.add_argument("--nightly", default=NIGHTLY)
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        sys.stdout.write(PROBE_SH.lstrip())
        return 0
    if a.cmd == "lint-plan":
        hosts = [h for h in a.hosts.split(",") if h]
        probes = {}
        for h in hosts:
            f = os.path.join(a.probe_dir, h + ".txt")
            probes[h] = parse(open(f).read() if os.path.isfile(f) else "")
        targets = [t for t in a.targets.split(",") if t]
        try:
            plan = lint_plan(probes, hosts, targets, a.spread, a.tests, a.toolchain, a.floor, a.nightly)
        except ValueError as e:
            print(f"audit-hosts: {e}", file=sys.stderr)
            return 1
        for h, role, ts in plan:
            print(h, role, ",".join(ts) or "-")
        return 0
    with open(a.probe) as f:
        p = parse(f.read())
    try:
        n = stock_need(p, a.name, a.toolchain)
    except ValueError as e:
        print(f"audit-hosts: {e}", file=sys.stderr)
        return 2
    if n:
        _say(os.path.basename(a.probe).rsplit(".", 1)[0], a.name, n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
