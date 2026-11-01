#!/usr/bin/env python3
"""Verdicts for run-all.sh: what each round-trip script's output says, as
rows of a table, and the table for a whole round.

Usage: verdict.py judge KIND LOG [--results DIR] [--platform P] [--step S]
                  [--rc N] [--wall SECONDS] [--expect N] [--identity]
                  [--baseline SUMMARY.json]
         Print one JSON row per line for one step's output:
         guest-rt  run-guest-rt.sh's PASS/FAIL lines, one row per direction
                   (--expect: how many directions there must be)
         nonroot   nonroot-rt.sh's "== summary" blocks, one row per file
                   system or guest, per direction
         fsrt      fs_roundtrip.py's results.json in DIR (one row per tool,
                   and one for what the tools share); --identity: SPEC3 2's
                   snapshot identity must have run
         zfsguest  zfs-guest-rt.sh's DIR/GUEST/FSTYPE/results.json (as
                   fsrt) and DIR/GUEST/sparse.log (as sparse)
         sparse    each "sparse-rt: PASS" or "sparse-rt: FAIL: ..." line
         A step with no verdict in its output (crashed, timed out: --rc 124)
         is a FAIL, with the tail of its log.
       verdict.py summary LOGDIR
         LOGDIR/rows.jsonl (the judge rows of a round) into
         LOGDIR/summary.txt and summary.json; prints the table; exits 0
         only if every row passes (SKIP: a file system this host can't
         make, as fs_roundtrip.py says; not a failure).

Accepted, not failures (documented in the forks' BACKOTTER.md and the
round reports): a restore's exit 1 from restic as non-root or over
immutable files (S10: only the first restore's exit is checked, and
nonroot's restic exit may be 1); symlink atimes moved by a backup (S1);
holes lost on a file neither tool marked unix.sparse (S3: an unmarked
file restores dense, e.g. on JFS); restored onto another file system,
ext4's lost+found and the PROJINHERIT flag ext4 refuses on a file.  A
check that didn't run is a failure: no socket made in the source, too few
directions, no results.json, identity not run though OLD_BIN was given.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


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
    if a.get("rc") not in ("0", "", None):
        rows.append(row(a, guest, "", False, [f"run-guest-rt.sh exit {a['rc']}"] + tail(Path(a["log"]))))
    return rows


# --- nonroot-rt.sh ----------------------------------------------------------
def nonroot(a):
    rows, label, block = [], "", None
    blocks = []
    for l in Path(a["log"]).read_text(errors="replace").splitlines():
        if l.startswith("######## "):
            label = l[9:].split(":")[0]
        elif l.startswith("== summary"):
            block = [label, []]
            blocks.append(block)
            if "(sockets in the source: 0)" in l:  # SPEC3 1 not exercised
                block[1].append("no socket in the source")
        elif block is not None and l.startswith("  "):
            block[1].append(l.strip())
        elif block is not None:
            block = None
    if not blocks:
        return crashed(a, "no summary from nonroot-rt.sh")
    base = baseline_info(a)
    for target, ls in blocks:
        shared = []
        for l in ls:
            if l == "no socket in the source":
                shared.append("no socket in the source (rt_mksock failed): SPEC3 1 not checked")
            elif l.startswith("backup by "):
                if not l.split(": ", 1)[1].startswith("0 socket nodes in the snapshot, 0 messages"):
                    shared.append(l)
            elif " vs " in l.split(":")[0]:
                if not l.endswith(": 0 names differ"):
                    shared.append(f"tools differ: {l}")
            else:
                d, _, rest = l.partition(": ")
                checks = []
                ex = rest.split(",")[0].replace("exit ", "")
                tool = d.split("-")[-1]
                if not (ex == "0" or (tool == "restic" and ex == "1")):
                    checks.append(f"{tool} restore exit {ex}")
                if "D1, user.b back on xattr-order: no" in rest:
                    checks.append("D1: user.b lost on xattr-order")
                if "sockets restored: 0, messages naming one: 0" not in rest:
                    checks.append("socket restored or named")
                info = rest.split(";")[0]
                b = base.get((a.get("platform"), a.get("step"), target, d))
                if b is not None and b != info:
                    checks.append(f"changed from the baseline ({b})")
                r = row(a, target, d, not checks, checks)
                r["info"] = info
                rows.append(r)
        rows.append(row(a, target, "tools", not shared, shared))
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


def fsrt_results(a, results: Path, target: str):
    try:
        r = json.loads((results / "results.json").read_text())
    except (OSError, ValueError) as ex:
        return [row(a, target, "", False, [f"no results.json in {results}: {ex}"] + tail(Path(a["log"])))]
    if r.get("skipped"):
        return [row(a, target, "", True, [f"skipped: {r['skipped']}"], "SKIP")]
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
    c = [f"sockets: {m}" for m in (r.get("sockets") or {"failures": ["not checked"]})["failures"]]
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
    if a.get("rc") not in ("0", "", None):
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


def sparse(a):
    rows = sparse_lines(a, Path(a["log"]), a.get("target", ""))
    if not rows:
        return crashed(a, "no verdict from sparse-rt.sh")
    if a.get("rc") not in ("0", "", None) and all(r["verdict"] == "PASS" for r in rows):
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
    if not rows:
        return crashed(a, "no results from zfs-guest-rt.sh")
    if a.get("rc") not in ("0", "", None) and all(r["verdict"] != "FAIL" for r in rows):
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
    rows.sort(key=lambda r: (order[r["platform"]],))
    ok = bool(rows) and all(r["verdict"] in ("PASS", "SKIP") for r in rows)
    meta = {}
    if (logdir / "meta.json").exists():
        meta = json.loads((logdir / "meta.json").read_text())
    (logdir / "summary.json").write_text(json.dumps({"pass": ok, **meta, "rows": rows}, indent=1))
    hdr = ("PLATFORM", "STEP", "TARGET", "DIRECTION", "VERDICT", "WALL", "FAILED CHECKS")
    w = [max([len(hdr[i])] + [len(str(r[k])) for r in rows]) for i, k in
         enumerate(("platform", "step", "target", "direction", "verdict", "wall"))]
    out = []
    fmt = lambda c: "  ".join(str(x).ljust(w[i]) for i, x in enumerate(c[:6])) + "  " + c[6]
    out.append(fmt(hdr))
    for r in rows:
        checks = r["checks"] if r["verdict"] == "FAIL" else []
        out.append(fmt((r["platform"], r["step"], r["target"], r["direction"], r["verdict"], r["wall"],
                        checks[0] if checks else "")).rstrip())
        for c in checks[1:12]:
            out.append(" " * (sum(w) + 12) + c)
        if len(checks) > 12:
            out.append(" " * (sum(w) + 12) + f"... {len(checks) - 12} more: {r['log']}")
    nf = sum(r["verdict"] == "FAIL" for r in rows)
    out.append(f"run-all: {'PASS' if ok else 'FAIL'}: {len(rows)} rows, {nf} failed"
               + (f", wall {meta['wall']} s" if "wall" in meta else ""))
    for n in meta.get("notes", []):
        out.append(f"note: {n}")
    text = "\n".join(out) + "\n"
    (logdir / "summary.txt").write_text(text)
    sys.stdout.write(text)
    return 0 if ok else 1


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["summary"] and len(args) == 2:
        return summary(Path(args[1]))
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
