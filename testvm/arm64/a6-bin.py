#!/usr/bin/env python3
"""ARM64 A6's hosttest2:bin helper (docs/design/arm64.md, "What A6 built"): the
expectations GateTest2's gate:bin:version makes, and its judging, for the run's
linux-arm64 set.

  a6-bin.py expect --git DIR RESTIC_COMMIT_FILE RUSTIC_COMMIT_FILE OUT_DIR
      On the Mac (a6-run.sh): OUT_DIR/expect.tsv, bincheck.sh --expect's lines
      (NAME<TAB>VERSION<TAB>CORE, NAME linux-arm64/restic-bko and
      linux-arm64/rustic-bko), and OUT_DIR/commits.json, the commits they are
      for, as t2lib/gate.py's expected_versions makes them: each fork's version
      line at the set's commit in BKO_GIT (DIR/restic, DIR/rustic,
      DIR/rustic_core).  Exit 1, and nothing written, when a commit isn't there.

  a6-bin.py judge --set DIR --expect DIR STATIC_OUT VERSION_OUT HOLT_OUT
      On Bigfoot (a6-stages.sh hosttest2:bin): bincheck.sh's outputs judged as
      t2lib/gate_judge.py judges gate:bin:static and gate:bin:version (an
      unchecked set binary FAILs, never a SKIP; bko-holt must PASS against
      holt/Cargo.toml's version).  The commits in --expect DIR/commits.json must
      be the set's (DIR/linux-arm64/*-commit).  Prints one line per verdict;
      exit 0 PASS, 1 otherwise.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "test2"))
from t2lib import gate_judge as gj   # noqa: E402
from t2lib.facts_host import commit_of   # noqa: E402
from t2lib.gate import cargo_version   # noqa: E402

PLAT = "linux-arm64"


def set_commits(restic_file, rustic_file):
    """{NAME: commit}, as facts_host's sets: restic a commit, rustic {commit, rustic_core}."""
    out = {}
    rc, _ = commit_of(restic_file)
    uc, core = commit_of(rustic_file)
    if rc:
        out[f"{PLAT}/restic-bko"] = rc
    if uc:
        out[f"{PLAT}/rustic-bko"] = {"commit": uc, "rustic_core": core}
    return out


def git_show(git, repo, commit, path):
    p = subprocess.run(["git", "-C", os.path.join(git, repo), "show", f"{commit}:{path}"],
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def expectations(git, commits):
    """({NAME: (version, core)}, [why not]) as gate.expected_versions."""
    out, bad = {}, []
    for name, c in sorted(commits.items()):
        if name.endswith("/restic-bko"):
            m = re.search(r'Version\s*=\s*"([^"]+)"', git_show(git, "restic", c, "internal/global/global.go") or "")
            if m:
                out[name] = (m.group(1), "")
            else:
                bad.append(f"{name}: restic {c[:12]} isn't in {git}/restic")
        elif name.endswith("/rustic-bko"):
            v = cargo_version(git_show(git, "rustic", c["commit"], "Cargo.toml"))
            core = cargo_version(git_show(git, "rustic_core", c["rustic_core"], "crates/core/Cargo.toml")) \
                if c.get("rustic_core") else None
            if v and (core or not c.get("rustic_core")):
                out[name] = (v, core or "")
            else:
                bad.append(f"{name}: rustic {c['commit'][:12]} or rustic_core {str(c.get('rustic_core'))[:12]} "
                           f"isn't in {git}")
    for n in (f"{PLAT}/restic-bko", f"{PLAT}/rustic-bko"):
        if n not in commits:
            bad.append(f"{n}: no commit in its commit file")
    return out, bad


def cmd_expect(a):
    git, rf, uf, od = a
    commits = set_commits(rf, uf)
    exp, bad = expectations(os.path.expanduser(git), commits)
    if bad:
        print("a6-bin: no expectations: " + "; ".join(bad), file=sys.stderr)
        return 1
    os.makedirs(od, exist_ok=True)
    with open(os.path.join(od, "expect.tsv"), "w") as f:
        f.write("".join(f"{n}\t{v}\t{c}\n" for n, (v, c) in sorted(exp.items())))
    with open(os.path.join(od, "commits.json"), "w") as f:
        json.dump(commits, f, indent=1, sort_keys=True)
        f.write("\n")
    for n, (v, c) in sorted(exp.items()):
        print(f"expect {n} {v}{' core ' + c if c else ''}")
    return 0


def read(p):
    try:
        with open(p, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def judge(set_dir, exp_dir, static_out, version_out, holt_out):
    """[(stage part, verdict, why)]."""
    res = []
    commits = set_commits(os.path.join(set_dir, PLAT, "restic-commit"), os.path.join(set_dir, PLAT, "rustic-commit"))
    try:
        with open(os.path.join(exp_dir, "commits.json")) as f:
            made_for = json.load(f)
    except (OSError, ValueError):
        made_for = None
    exp = {}
    for line in read(os.path.join(exp_dir, "expect.tsv")).splitlines():
        f = line.split("\t")
        if len(f) >= 2:
            exp[f[0]] = (f[1], f[2] if len(f) > 2 else "")
    if made_for is None or not exp:
        res.append(("expect", "FAIL", f"no {exp_dir}/expect.tsv and commits.json (a6-run.sh makes them on the Mac)"))
    elif made_for != commits:
        res.append(("expect", "FAIL", f"expect.tsv is for {json.dumps(made_for, sort_keys=True)}, "
                                      f"the set's commits are {json.dumps(commits, sort_keys=True)}"))
    else:
        res.append(("expect", "PASS", "made for the set's commits"))
    for check, text in (("static", read(static_out)), ("version", read(version_out))):
        rc = 0 if not re.search(r"^FAIL ", text, re.M) else 1
        o = gj.bin_outcome(check, text, rc, commits=commits if check == "version" else None)
        if check == "version":
            gj.version_unchecked(o, commits, exp)
        res.append((check, o.verdict, "; ".join(o.fails) or ", ".join(f"{k} {v}" for k, v in sorted(o.needs.items()))))
    lines = gj.parse_bincheck(read(holt_out))
    hv = [v for v, _, n, _, _ in lines if n.endswith("bko-holt")]
    if hv == ["PASS"]:
        res.append(("holt", "PASS", lines[0][4]))
    else:
        res.append(("holt", "FAIL", "; ".join(f"{v} {d}" for v, _, _, _, d in lines) or "bincheck.sh gave bko-holt no line"))
    return res


def cmd_judge(argv):
    set_dir = exp_dir = None
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] in ("--set", "--expect") and i + 1 < len(argv):
            if argv[i] == "--set":
                set_dir = argv[i + 1]
            else:
                exp_dir = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    if not set_dir or not exp_dir or len(rest) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    res = judge(set_dir, exp_dir, *rest)
    for part, v, why in res:
        print(f"{v} {part}: {why}")
    return 0 if all(v == "PASS" for _, v, _ in res) else 1


def main(argv):
    if len(argv) >= 1 and argv[0] == "expect" and len(argv) == 6 and argv[1] == "--git":
        return cmd_expect([argv[2]] + argv[3:])
    if len(argv) >= 1 and argv[0] == "judge":
        return cmd_judge(argv[1:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
