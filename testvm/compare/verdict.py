#!/usr/bin/env python3
"""Verdicts for run-all.sh: what each round-trip script's output says, as
rows of a table, and the table for a whole round.

Usage: verdict.py judge KIND LOG [--results DIR] [--platform P] [--step S]
                  [--target T] [--rc N] [--wall SECONDS] [--expect N]
                  [--identity] [--baseline SUMMARY.json] [--targets T,...]
                  [--full LOG] [--allow-skip T,...]
         Print one JSON row per line for one step's output (LOG: what the
         step printed):
         guest-rt  run-guest-rt.sh's PASS/FAIL lines, one row per direction
                   (--expect: how many directions there must be)
         nonroot   nonroot-rt.sh's full log (--full: NONROOT_LOGS/TARGET.log;
                   what it prints is cut), one row per file system or guest
                   (--targets: each must be there, e.g. "linux zfs,linux
                   ext4"), per direction, all four
         fsrt      fs_roundtrip.py's results.json in DIR (one row per tool,
                   all four, and one for what the tools share); --identity:
                   SPEC3 2's snapshot identity must have run
         zfsguest  zfs-guest-rt.sh's DIR/GUEST/FSTYPE/results.json (as
                   fsrt) and DIR/GUEST/sparse.log (as sparse)
         sparse    each "sparse-rt: PASS" or "sparse-rt: FAIL: ..." line;
                   --identity: SPEC3 2's checks against the old binaries ran
         A step with no verdict in its output (crashed, timed out: --rc 124),
         or a non-zero exit, is a FAIL, with the tail of its log.  A file
         system fs_roundtrip.py skips is a SKIP row, which fails the round
         unless --allow-skip names its target.
       verdict.py summary LOGDIR
         LOGDIR/rows.jsonl (the judge rows of a round) into
         LOGDIR/summary.txt and summary.json; prints the table; exits 0
         only if every row passes.  LOGDIR/meta.json's "steps" must each
         have a row, and each of its "lost" (a check run-all.sh couldn't
         run) is a FAIL row.
       verdict.py expect RESULTS.json...
         expected-coverage.json from a passing round's results.json files:
         per file system (and onto), what torture.sh covered and skipped.

Accepted, not failures (documented in the forks' BACKOTTER.md and the
round reports): a restore's exit 1 from restic as non-root or over
immutable files (S10: only the first restore's exit is checked, and
nonroot's restic exit may be 1); symlink atimes moved by a backup (S1);
holes lost on a file neither tool marked unix.sparse (S3: an unmarked
file restores dense, e.g. on JFS); restored onto another file system,
ext4's lost+found and the PROJINHERIT flag ext4 refuses on a file.  A
check that didn't run is a failure: no socket made in the source, too few
directions, a file system or tool missing, no results.json, identity not
run though OLD_BIN was given, and coverage lost: torture.sh skipped or
didn't cover what expected-coverage.json (here; round 9's) says it does on
that file system.  nonroot, as uid 4321: every name that differs from the
source must be named in one of that restore's messages, every message must
be a permission one (EPERM, "not owner", not running as root), and every
name must be the user's.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = ("restic-bko", "rustic-bko", "restic-to-rustic", "rustic-to-restic")
DIRECTIONS = ("restic-restic", "rustic-rustic", "restic-rustic", "rustic-restic")


def tail(log: Path, n: int = 15) -> list[str]:
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError as ex:
        return [f"no log: {ex}"]
    return ["log tail: " + l for l in lines[-n:]]


def row(a, target, direction, ok, checks, verdict=None):
    return {"platform": a.get("platform", ""), "step": a.get("step", ""), "target": target,
            "direction": direction, "verdict": verdict or ("PASS" if ok else "FAIL"),
            "wall": a.get("wall", ""), "checks": checks, "log": a.get("log", "")}


def nonzero(a):
    return a.get("rc") not in ("0", "", None)


def crashed(a, why):
    rc = a.get("rc", "")
    why = "timed out" if rc == "124" else why
    return [row(a, "", "", False, [f"{why} (exit {rc})"] + tail(Path(a["log"])))]


# --- run-guest-rt.sh --------------------------------------------------------
def guest_rt(a):
    rows, cur = [], None
    lines = Path(a["log"]).read_text(errors="replace").splitlines()
    guest = next((l.split()[1].rstrip(":") for l in lines if l.startswith("== ") and l.endswith("-bko")), "")
    for l in lines:
        if l.startswith(("PASS  ", "FAIL  ")):
            v, rest = l[:4], l[6:]
            d, _, why = rest.partition(": ")
            cur = row(a, guest, d, v == "PASS", [why] if why else [])
            rows.append(cur)
        elif cur is not None and l.startswith("    ") and cur["verdict"] == "FAIL":
            cur["checks"].append(l.strip())
    if not rows:
        return crashed(a, "no verdict from run-guest-rt.sh")
    want = int(a.get("expect") or 0)
    if want and len(rows) < want:
        rows.append(row(a, guest, "", False, [f"{len(rows)} of {want} directions ran"] + tail(Path(a["log"]))))
    if nonzero(a):
        rows.append(row(a, guest, "", False, [f"run-guest-rt.sh exit {a['rc']}"] + tail(Path(a["log"]))))
    return rows


# --- nonroot-rt.sh ----------------------------------------------------------
# A restore as uid 4321 may only lose what the user can't set, and must say
# so: a message per name, each one a permission one.
PERM = re.compile(r"operation not permitted|not owner|not running as root|EPERM", re.I)
TALLY = re.compile(r"^(Summary: Restored .*|Fatal: There were \d+ errors)$")


def nonroot_parse(text):
    """{label: {"summary": [lines], "dirs": {direction: details}}}"""
    out, label, sec, part = {}, "", None, None
    for l in text.splitlines():
        if l.startswith("######## "):
            label = l[9:].split(":")[0]
            out[label] = {"summary": None, "dirs": {}}
            sec = part = None
            continue
        t = out.setdefault(label, {"summary": None, "dirs": {}})
        m = re.match(r"^== backed up by (\w+), restored by (\w+) as uid \d+: exit (\S+)", l)
        if m:
            sec = t["dirs"][f"{m[1]}-{m[2]}"] = {"exit": m[3], "msgs": [], "diff": [], "notowned": None}
            part = None
        elif l.startswith("== summary"):
            sec, part = None, "summary"
            t["summary"] = ["no socket in the source"] if "(sockets in the source: 0)" in l else []
        elif l.startswith("== "):
            sec = part = None
        elif part == "summary":
            if l.startswith("  "):
                t["summary"].append(l.strip())
            else:
                part = None
        elif sec is not None:
            m = re.match(r"^-- names not owned by \S+: (\d+)", l)
            if l.startswith("-- messages"):
                part = "msgs"
            elif m:
                sec["notowned"], part = m[1], None
            elif l.startswith("-- what differs"):
                part = "diff"
            elif l.startswith("--"):
                part = None
            elif part in ("msgs", "diff") and l.startswith("  "):
                sec[part].append(l.strip())
    return out


def nonroot_details(d):
    """What a direction's details say against the rules above."""
    c = []
    if d is None:
        return ["no details for this direction in the log"]
    if d["notowned"] != "0":
        c.append(f"names not owned by 4321:4321: {d['notowned'] or 'not reported'}")
    for m in d["msgs"]:
        if not (PERM.search(m) or TALLY.match(m)):
            c.append(f"message: {m[:200]}")
    for x in d["diff"]:
        n = x.split(":")[0]
        if not any(re.search(r"(^|[/ ])" + re.escape(n) + r"([/: @]|$)", m) for m in d["msgs"]):
            c.append(f"differs, unsaid: {x[:200]}")
    return c


def nonroot(a):
    full = Path(a.get("full") or a["log"])
    if not full.exists():
        return crashed(a, f"no {full} from nonroot-rt.sh")
    parsed = nonroot_parse(full.read_text(errors="replace"))
    want = [t for t in (a.get("targets") or "").split(",") if t]
    rows = []
    for t in want:
        if parsed.get(t, {}).get("summary") is None:
            rows.append(row(a, t, "", False, [f"no summary for {t}: not run, or cut short"] + tail(full)))
    base = baseline_info(a)
    for target, p in parsed.items():
        if p["summary"] is None:
            continue
        shared, seen, vs, backups = [], set(), 0, 0
        for l in p["summary"]:
            if l == "no socket in the source":
                shared.append("no socket in the source (rt_mksock failed): SPEC3 1 not checked")
            elif l.startswith("backup by "):
                backups += 1
                if not l.split(": ", 1)[1].startswith("0 socket nodes in the snapshot, 0 messages"):
                    shared.append(l)
            elif " vs " in l.split(":")[0]:
                vs += 1
                if not l.endswith(": 0 names differ"):
                    shared.append(f"tools differ: {l}")
            else:
                d, _, rest = l.partition(": ")
                seen.add(d)
                checks = []
                ex = rest.split(",")[0].replace("exit ", "")
                tool = d.split("-")[-1]
                if not (ex == "0" or (tool == "restic" and ex == "1")):
                    checks.append(f"{tool} restore exit {ex}")
                if "D1, user.b back on xattr-order: no" in rest:
                    checks.append("D1: user.b lost on xattr-order")
                if "sockets restored: 0, messages naming one: 0" not in rest:
                    checks.append("socket restored or named")
                checks += nonroot_details(p["dirs"].get(d))
                info = rest.split(";")[0]
                b = base.get((a.get("platform"), a.get("step"), target, d))
                if b is not None and b != info:
                    checks.append(f"changed from the baseline ({b})")
                r = row(a, target, d, not checks, checks)
                r["info"] = info
                rows.append(r)
        for d in DIRECTIONS:
            if d not in seen:
                rows.append(row(a, target, d, False, ["direction not in the summary: not run"]))
        if backups != 2:
            shared.append(f"{backups} of 2 backups in the summary")
        if vs != 3:
            shared.append(f"{vs} of 3 tool-vs-tool comparisons in the summary")
        rows.append(row(a, target, "tools", not shared, shared))
    if not rows:
        return crashed(a, "no summary from nonroot-rt.sh")
    if nonzero(a):
        rows.append(row(a, "", "", False, [f"nonroot-rt.sh exit {a['rc']}"] + tail(Path(a["log"]))))
    return rows


def baseline_info(a):
    if not a.get("baseline"):
        return {}
    try:
        rows = json.loads(Path(a["baseline"]).read_text())["rows"]
    except (OSError, ValueError, KeyError):
        return {}
    return {(r["platform"], r["step"], r["target"], r["direction"]): r["info"] for r in rows if "info" in r}


# --- fs_roundtrip.py --------------------------------------------------------
def rel(p: str) -> str:
    return p.split("/src/", 1)[1] if "/src/" in p else p


def fs_key(r) -> str:
    return r.get("fs", "") if r.get("onto") in (None, r.get("fs")) else f"{r.get('fs')} onto {r.get('onto')}"


def norm_skip(s: str) -> str:
    """A torture.sh skip line without where it ran."""
    return re.sub(r"x{20,}", "x...", re.sub(r"\S*/bko-torture/", "TORTURE/", s))


def coverage_of(r):
    over = sorted({norm_skip(x) for t in r.get("tools", {}).values() for x in t.get("over_skipped") or []})
    return {"coverage": sorted(r.get("coverage") or {}), "skipped": sorted({norm_skip(x) for x in r.get("torture_skipped") or []}),
            "over_skipped": over}


def coverage_checks(r):
    """What torture.sh lost against expected-coverage.json."""
    try:
        exp = json.loads((HERE / "expected-coverage.json").read_text())
    except (OSError, ValueError) as ex:
        return [f"no expected coverage: {ex}"]
    k = fs_key(r)
    if k not in exp:
        return [f"no expected coverage for {k} in expected-coverage.json (verdict.py expect)"]
    e, got, c = exp[k], coverage_of(r), []
    c += [f"coverage lost: {x}" for x in e["coverage"] if x not in got["coverage"]]
    c += [f"torture.sh skipped: {x}" for x in got["skipped"] if x not in e["skipped"]]
    c += [f"restore over skipped: {x}" for x in got["over_skipped"] if x not in e["over_skipped"]]
    return c


def fsrt_results(a, results: Path, target: str):
    try:
        r = json.loads((results / "results.json").read_text())
    except (OSError, ValueError) as ex:
        return [row(a, target, "", False, [f"no results.json in {results}: {ex}"] + tail(Path(a["log"])))]
    if r.get("skipped"):
        allowed = target in (a.get("allow-skip") or "").split(",")
        x = row(a, target, "", allowed, [f"skipped: {r['skipped']}"] + ([] if allowed else ["not in ALLOW_SKIP"]), "SKIP")
        x["allowed"] = allowed
        return [x]
    onto = r.get("onto") != r.get("fs")
    marked = {rel(p) for v in (r.get("sparse_marked") or {}).values() for p in v}

    def accepted(kind, path):
        if kind == "holes lost" and path not in marked:
            return True
        if onto and kind == "extra" and path.rstrip("/").endswith("lost+found"):
            return True
        if onto and kind == "inode flags" and path.endswith("projid-dir/inherited"):
            return True
        return False

    def diffs(name, d):
        out = []
        for k, v in (d or {}).items():
            v = [p for p in v if not accepted(k, p)]
            if v:
                out.append(f"{name} {k} {len(v)}: {', '.join(v[:6])}")
        return out

    rows = []
    for tool, t in r.get("tools", {}).items():
        c = []
        ex = t.get("restore", {}).get("exit")
        if ex != 0:
            c.append(f"restore exit {ex}: {t.get('restore', {}).get('stderr_tail', '').strip()[-300:]}")
        c += diffs("diff", t.get("diff_all"))
        c += diffs("over-existing diff", t.get("over_diff_all"))
        c += diffs("rerun diff", t.get("rerun_diff_all"))
        sc = {k: v for k, v in (t.get("source_changed_by_backup") or {}).items() if k != "atime (symlinks)"}
        if sc:
            c.append(f"source changed by backup: {json.dumps(sc)[:300]}")
        sb = (r.get("sparse_blocks") or {}).get(tool)
        if sb:
            c += [f"sparse blocks: {m}" for m in sb.get("failures", [])]
            if not onto:
                c += [f"sparse blocks over the source's: {m}" for m in sb.get("over", [])]
        elif "unix.sparse" in json.dumps(r.get("sparse_marked")):
            c.append("sparse blocks not measured")
        rows.append(row(a, target, tool, not c, c))
    rows += [row(a, target, t, False, ["not run (not in results.json)"]) for t in TOOLS if t not in r.get("tools", {})]
    c = coverage_checks(r)
    c += [f"sockets: {m}" for m in (r.get("sockets") or {"failures": ["not checked"]})["failures"]]
    c += [f"xattr order: {m}" for m in (r.get("xattr_order") or {"failures": ["not checked"]})["failures"]]
    nd = r.get("snapshot_node_diff (restic-bko vs rustic-bko)")
    if nd is None:
        c.append("snapshot nodes not compared")
    for k, v in (nd or {}).items():
        c.append(f"snapshot nodes differ: {k} {len(v)}: {'; '.join(v[:4])}")
    for k, v in (r.get("blocks_mismatch") or {}).items():
        if v:
            c.append(f"st_blocks differ {k}: {', '.join(map(str, v[:6]))}")
    idn = r.get("identity")
    if idn is None and a.get("identity"):
        c.append("snapshot identity (SPEC3 2) not run")
    for m in (idn or {}).get("failures", []):
        c.append(f"snapshot identity: {m}")
    rows.append(row(a, target, "tools", not c, c))
    return rows


def fsrt(a):
    res = Path(a["results"])
    if not (res / "results.json").exists():
        return crashed(a, "no results.json from fs_roundtrip.py")
    rows = fsrt_results(a, res, a.get("target", ""))
    if nonzero(a):
        rows.append(row(a, a.get("target", ""), "", False, [f"exit {a['rc']}"] + tail(Path(a["log"]))))
    return rows


# --- sparse-rt.sh -----------------------------------------------------------
def sparse_lines(a, log: Path, target: str):
    rows, nosock = [], False
    for l in log.read_text(errors="replace").splitlines():
        if "(no socket: nothing here could make one)" in l:
            nosock = True  # SPEC3 1 not exercised
        elif l.startswith("sparse-rt: PASS"):
            rows.append(row(a, target, "all", not nosock, ["no socket made: SPEC3 1 not checked"] if nosock else []))
        elif l.startswith("sparse-rt: FAIL"):
            rows.append(row(a, target, "all", False, [m.strip() for m in l[17:].split(";")]
                            + (["no socket made: SPEC3 1 not checked"] if nosock else [])))
        if l.startswith("sparse-rt: "):
            nosock = False
    return rows


def identity_ran(a, log: Path, target: str):
    """With --identity: sparse-rt.sh's SPEC3 2 checks against both old binaries."""
    t = log.read_text(errors="replace")
    miss = [x for x in ("restic", "rustic") if f"== snapshot identity, old {x} vs new" not in t]
    if a.get("identity") and miss:
        return [row(a, target, "identity", False, [f"snapshot identity (SPEC3 2) against the old {x} not run" for x in miss])]
    return []


def sparse(a):
    rows = sparse_lines(a, Path(a["log"]), a.get("target", ""))
    if not rows:
        return crashed(a, "no verdict from sparse-rt.sh")
    rows += identity_ran(a, Path(a["log"]), a.get("target", ""))
    if nonzero(a) and all(r["verdict"] == "PASS" for r in rows):
        rows.append(row(a, a.get("target", ""), "", False, [f"exit {a['rc']}"] + tail(Path(a["log"]))))
    return rows


# --- zfs-guest-rt.sh --------------------------------------------------------
def zfsguest(a):
    rows = []
    for g in sorted(p for p in Path(a["results"]).iterdir() if p.is_dir()) if Path(a["results"]).is_dir() else []:
        for fs in ("zfs", "zfs-ds"):
            if (g / f"{fs}.log").exists():
                rows += fsrt_results(a, g / fs, f"{g.name}/{fs}")
            else:
                rows.append(row(a, f"{g.name}/{fs}", "", False, [f"{fs} didn't run"] + tail(Path(a["log"]))))
        if (g / "sparse.log").exists():
            rows += sparse_lines(a, g / "sparse.log", f"{g.name}/sparse") or [
                row(a, f"{g.name}/sparse", "", False, ["no verdict"] + tail(g / "sparse.log"))]
            rows += identity_ran(a, g / "sparse.log", f"{g.name}/sparse")
        else:
            rows.append(row(a, f"{g.name}/sparse", "", False, ["sparse-rt.sh didn't run"] + tail(Path(a["log"]))))
    if not rows:
        return crashed(a, "no results from zfs-guest-rt.sh")
    if nonzero(a) and all(r["verdict"] != "FAIL" for r in rows):
        rows.append(row(a, "", "", False, [f"zfs-guest-rt.sh exit {a['rc']}"] + tail(Path(a["log"]))))
    return rows


def summary(logdir: Path) -> int:
    rows = []
    rf = logdir / "rows.jsonl"
    if rf.exists():
        rows = [json.loads(l) for l in rf.read_text().splitlines() if l.strip()]
    order = {}
    for r in rows:
        order.setdefault(r["platform"], len(order))
    meta = {}
    if (logdir / "meta.json").exists():
        meta = json.loads((logdir / "meta.json").read_text())
    have = {f"{r['platform']}/{r['step']}" for r in rows}
    for s in meta.get("steps", []):
        if s not in have:
            p, _, n = s.partition("/")
            rows.append(row({"platform": p, "step": n}, "", "", False, ["no verdict row for this step"]))
    for x in meta.get("lost", []):
        rows.append(row({"platform": x.get("platform", ""), "step": x.get("step", "")}, x.get("target", ""), "",
                        False, [f"not checked: {x.get('why', '')}"]))
    for r in rows:
        order.setdefault(r["platform"], len(order))
    rows.sort(key=lambda r: (order[r["platform"]],))
    passed = lambda r: r["verdict"] == "PASS" or (r["verdict"] == "SKIP" and r.get("allowed"))
    ok = bool(rows) and all(passed(r) for r in rows)
    (logdir / "summary.json").write_text(json.dumps({"pass": ok, **meta, "rows": rows}, indent=1))
    hdr = ("PLATFORM", "STEP", "TARGET", "DIRECTION", "VERDICT", "WALL", "FAILED CHECKS")
    w = [max([len(hdr[i])] + [len(str(r[k])) for r in rows]) for i, k in
         enumerate(("platform", "step", "target", "direction", "verdict", "wall"))]
    out = []
    fmt = lambda c: "  ".join(str(x).ljust(w[i]) for i, x in enumerate(c[:6])) + "  " + c[6]
    out.append(fmt(hdr))
    for r in rows:
        checks = [] if passed(r) else r["checks"]
        out.append(fmt((r["platform"], r["step"], r["target"], r["direction"], r["verdict"], r["wall"],
                        checks[0] if checks else "")).rstrip())
        for c in checks[1:12]:
            out.append(" " * (sum(w) + 12) + c)
        if len(checks) > 12:
            out.append(" " * (sum(w) + 12) + f"... {len(checks) - 12} more: {r['log']}")
    nf = sum(not passed(r) for r in rows)
    out.append(f"run-all: {'PASS' if ok else 'FAIL'}: {len(rows)} rows, {nf} failed"
               + (f", wall {meta['wall']} s" if "wall" in meta else ""))
    for n in meta.get("notes", []):
        out.append(f"note: {n}")
    text = "\n".join(out) + "\n"
    (logdir / "summary.txt").write_text(text)
    sys.stdout.write(text)
    return 0 if ok else 1


def expect(files) -> int:
    """Per file system: the coverage every result had, the skips any had."""
    out = {}
    for f in files:
        r = json.loads(Path(f).read_text())
        if r.get("skipped"):
            continue
        c, k = coverage_of(r), fs_key(r)
        if k in out:
            e = out[k]
            c = {"coverage": [x for x in e["coverage"] if x in c["coverage"]],
                 "skipped": sorted(set(e["skipped"]) | set(c["skipped"])),
                 "over_skipped": sorted(set(e["over_skipped"]) | set(c["over_skipped"]))}
        out[k] = c
    print(json.dumps(dict(sorted(out.items())), indent=1))
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["summary"] and len(args) == 2:
        return summary(Path(args[1]))
    if args[:1] == ["expect"] and len(args) > 1:
        return expect(args[1:])
    if args[:1] != ["judge"] or len(args) < 3:
        raise SystemExit(__doc__)
    kind, a = args[1], {"log": args[2]}
    rest = args[3:]
    while rest:
        k = rest.pop(0)
        if k == "--identity":
            a["identity"] = True
        elif k.startswith("--") and rest:
            a[k[2:]] = rest.pop(0)
        else:
            raise SystemExit(__doc__)
    judge = {"guest-rt": guest_rt, "nonroot": nonroot, "fsrt": fsrt, "zfsguest": zfsguest, "sparse": sparse}
    if kind not in judge:
        raise SystemExit(__doc__)
    try:
        rows = judge[kind](a) if Path(a["log"]).exists() else crashed(a, "no log")
    except Exception as ex:  # a judge that can't read the output: the step fails
        rows = [row(a, "", "", False, [f"verdict.py {kind}: {type(ex).__name__}: {ex}"] + tail(Path(a["log"])))]
    for r in rows:
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
