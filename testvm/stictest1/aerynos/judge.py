#!/usr/bin/env python3
"""SticTest1 aerynos verdicts (adapted from void-musl's), on the host: per-kind restore results from
the guest's dumps, node identity of the two tools' snapshots (with
restic-bko cat blob), the ancestors' nodes.  Usage: judge.py RESTIC OUTDIR"""
import json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
R, O = sys.argv[1], sys.argv[2]
res = []
def out(need, st, detail): res.append({"need": need, "status": st, "detail": detail}); print(f"{st} {need}: {detail}")
inner = open(f"{O}/inner.log", errors="replace").read()
for m in re.finditer(r"^RESULT (.*?)\|(PASS|FAIL|LIMIT)\|(.*)$", inner, re.M): out(m.group(1), m.group(2), m.group(3))

def load(p):
    d = {}
    for l in open(p, errors="surrogateescape"):
        l = l.rstrip("\n")
        if l.startswith("LINKGROUP"): d.setdefault("#links", []).append(l)
        else: d[l.split("|", 1)[0]] = l
    return d
src = load(f"{O}/src.dump")
KIND = {"caps": "security.capability on f2fs (synthetic v2 ep, setcap v2 and v3: the guest has no file capability of its own)",
        "xattr": "user/trusted/security xattrs on f2fs (empty, binary, large, 40 names; files, dirs, symlinks)",
        "acl": "POSIX ACLs, access + default, on f2fs",
        "flags": "Linux inode flags f2fs takes (i, a, d, A, S on files; i, a, A, S, D, P on dirs)",
        "own": "Ownership incl. uids/gids with no name, setuid/setgid/sticky",
        "hl": "Hard links on f2fs (the guest's own link group + synthetic)",
        "sparse": "Sparse files on f2fs (holes, all-hole, data at end, written zeros, preallocated)",
        "dev": "Device nodes (incl. rdev 0, big numbers) and FIFOs",
        "sym": "Symlinks (relative, dangling, to dir, long) with their own times",
        "names": "Non-UTF-8, newline, space and 250-byte names",
        "deep": "Deep (60 levels) and empty directories", "empty": "Deep (60 levels) and empty directories",
        "times": "Far-past and far-future times (ns)",
        ".": "Backed-up directory's own metadata (mtime, atime, mode, owner)"}
RO = "f2fs's own inode state (N inline data, I indexed dir): set by the file system, not by a restore"
def kind(path):
    p = path.split("/")
    return "." if path == "." else (p[1] if len(p) > 1 else ".")
bad = {}
dirs = {}
for d in ("rr", "uu", "ru", "ur"):
    m = re.search(rf"^RESTORE {d} (\d+) (\d+)$", inner, re.M)
    if not m or not os.path.exists(f"{O}/{d}.dump"):
        out(f"Restore {d}", "FAIL", "did not run"); continue
    dirs[d] = int(m.group(1))
    dst = load(f"{O}/{d}.dump")
    for p in set(src) | set(dst):
        a, b = src.get(p), dst.get(p)
        if a != b:
            na, nb = (re.sub(r"\|ro=[NI]*\|", "|ro=|", x) if isinstance(x, str) else x for x in (a, b))
            k = RO if na == nb else ("hl" if p == "#links" else kind(p))
            bad.setdefault(KIND.get(k, k), []).append(f"{d}: {p} src={str(a)[:200]} dst={str(b)[:200]}")
dirsdesc = ", ".join(f"{d} exit {e}" for d, e in dirs.items())
for k in dict.fromkeys(KIND.values()):
    if k in bad: out(k, "FAIL", f"{len(bad[k])} differences ({dirsdesc}); e.g. {bad[k][0]}")
    else: out(k, "PASS", f"identical in all four directions ({dirsdesc})")
for k in bad:
    if k == RO: out(k, "LIMIT", f"{len(bad[k])} paths differ only in N/I; e.g. {bad[k][0]}")
    elif k not in KIND.values(): out(k, "FAIL", bad[k][0])
if RO not in bad: out(RO, "PASS", "the same in all four directions")
if any(e != 0 for e in dirs.values()): out("Restores exit 0 in all four directions", "FAIL", dirsdesc)
else: out("Restores exit 0 in all four directions", "PASS", dirsdesc)
ov = re.findall(r"^OVER (\w+) (\d+) (\d+)$", inner, re.M)
def nro(x): return re.sub(r"\|ro=[NI]*\|", "|ro=|", x) if isinstance(x, str) else x
ovd = {}
for d, e, n in ov:
    if os.path.exists(f"{O}/{d}-over.dump"):
        dd = load(f"{O}/{d}-over.dump"); ovd[d] = [p for p in set(src) | set(dd) if nro(src.get(p)) != nro(dd.get(p))]
okov = ov and all(e == "0" and not ovd.get(d, ["no dump"]) for d, e, n in ov) and len(ov) == 4
out("Restore over an existing tree with immutable/append-only files (changed immutable, appended append-only, on f2fs)", "PASS" if okov else "FAIL",
    "; ".join(f"{d} exit {e}, {n} lines differ ({len(ovd.get(d, []))} beyond N/I{': ' + str(sorted(ovd[d])[:3]) if ovd.get(d) else ''})" for d, e, n in ov) or "did not run")
out("Birth times", "LIMIT", "the guest's f2fs has no inode_crtime (superblock features 0), and Linux can't set a birth time (BACKOTTER.md)")
out("Project IDs", "LIMIT", "the guest's f2fs has no project_quota (superblock features 0): chattr -p refused" if "LIMITPROJ" in inner else "project ID set but not compared")
out("f2fs compression and casefold", "LIMIT", "not enabled on the guest's root f2fs (superblock features 0): chattr +c/+F refused" if "LIMITCOMP" in inner and "LIMITCF" in inner else "one was set: see inner.log")
out("Very large xattr (60000 bytes)", "LIMIT", "f2fs keeps an inode's xattrs in its inline area plus one 4 KiB block: setfattr refused" if "LIMITXATTR" in inner else "set; compared with the xattrs")
out("SELinux labels / NFSv4 ACLs / BSD flags", "LIMIT", "not on AerynOS (LSMs capability,landlock,lockdown,yama,bpf) or Linux f2fs")
out("Sockets", "LIMIT", "skipped by both tools by design (user decision); no socket tool in the guest (no python3)")

env = dict(os.environ, RESTIC_PASSWORD="bkocheck")
def run(repo, *a): return subprocess.run([R, "-r", repo, "--no-cache", "--no-lock", *a], env=env, capture_output=True, check=True).stdout
def nodes(repo):
    snap = json.loads(run(repo, "snapshots", "--json", "latest"))[0]
    o = {}; level = [(snap["tree"], "")]
    with ThreadPoolExecutor(8) as ex:
        while level:
            trees = list(ex.map(lambda t: json.loads(run(repo, "cat", "blob", t[0])), level)); nxt = []
            for (tid, pre), tr in zip(level, trees):
                for n in tr["nodes"]:
                    p = pre + "/" + n["name"]
                    if n.get("subtree"): nxt.append((n["subtree"], p))
                    # mode 0: rustic-bko writes "mode": 0, restic leaves it out
                    o[p] = {k: v for k, v in n.items() if k not in ("content", "subtree", "device_id")
                            and not (k == "mode" and v == 0)}
            level = nxt
    return o
try:
    a = nodes(f"{O}/w/repo-restic"); b = nodes(f"{O}/w/repo-rustic")
    anc = {"/mnt", "/mnt/bkosrc2"}
    diff = [p for p in sorted(set(a) | set(b)) if p not in anc and a.get(p) != b.get(p)]
    ex = [f"{p}: restic={json.dumps(a.get(p))[:300]} rustic={json.dumps(b.get(p))[:300]}" for p in diff[:3]]
    out("Same repository format: restic and rustic snapshots node-identical", "FAIL" if diff else "PASS",
        f"{len(a)} vs {len(b)} nodes, {len(diff)} differ (ancestors apart)" + (f"; e.g. {ex}" if ex else ""))
    ad = [p for p in sorted(anc) if a.get(p) != b.get(p)]
    pa, pb = a.get("/mnt/bkosrc2", {}), b.get("/mnt/bkosrc2", {})
    real = lambda n: n.get("uid") == 4321 and n.get("gid") == 4322 and str(n.get("mtime", "")).startswith("2024-03-04T05:06:07.111111111")
    st = "PASS" if not ad and real(pa) and real(pb) else "FAIL"
    out("Ancestor directories of a backup path stored with their real metadata, the same by both tools", st,
        f"/mnt/bkosrc2 restic={json.dumps(pa)[:400]} rustic={json.dumps(pb)[:400]}; differing ancestors: {ad}")
except Exception as e:
    out("Same repository format: restic and rustic snapshots node-identical", "FAIL", f"node read failed: {e}")
json.dump(res, open(f"{O}/results.json", "w"), indent=1)
sys.exit(1 if any(r["status"] == "FAIL" for r in res) else 0)
