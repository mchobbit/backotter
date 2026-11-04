#!/usr/bin/env bash
# SticTest1, haiku6: a targeted fixture test of BackOtter's rustic on Haiku
# (restic isn't ported to Haiku: rustic-bko alone).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR holds rustic-bko for Haiku (roundN/bin/haiku); restic-bko for
#   Linux is taken from BIN_DIR/../linux to read the Haiku repository here.
#
# Boots a throwaway overlay of haiku6 (vm-run --snapshot) with two spare
# disks, formats them BFS like the guest's /boot, builds the fixture (ck.py
# make: attrs.py's typed attributes, the guest's own attributes, owners
# without names, set-id/sticky, hard links, holes, FIFOs, symlinks with
# their own owners/times/attributes, odd names, empty and deep dirs,
# ancestors with metadata) and checks:
#   restore into a fresh BFS, restore over a changed tree, restore onto the
#   guest's own boot volume, a non-root backup and restore, the backup
#   leaving the source alone, the snapshot's attribute naming
#   (BACKOTTER.md), a repeated backup giving the same tree, rustic check
#   --read-data, and BackOtter's restic on Linux reading the repository.
# Prints PASS/FAIL/LIMIT lines, writes results.json, exits 0 if nothing FAILs.
set -u
[[ $# -ge 3 ]] || { echo "usage: $0 BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
H=$(cd "$(dirname "$0")" && pwd)
RB=$BIN/rustic-bko
RESTIC=$(realpath "$BIN/../linux/restic-bko" 2>/dev/null || true)
[[ -x $RB ]] || { echo "no $RB" >&2; exit 2; }

if [[ -z ${BKO_ADMITTED:-} ]]; then
    source ~/backotter-work/testvm/compare/guest-slot.sh
    case $(hostname -s) in chopin) slot_take 2 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
    BKO_ADMITTED=1 HOT_C=90 ~/backotter-work/cmp-run.sh --reserve 3 6 "$H/${0##*/}" "$@"; rc=$?
    slot_give; exit $rc
fi

W=/mnt/bko-ram/$PREFIX/check.$$
LOG=${STC_LOGS:-$HOME/backotter-work/stictest1/haiku6/logs}/check.$(date +%Y%m%d-%H%M%S)
mkdir -p "$W" "$LOG"
source "$H/guest-lib.sh"
cleanup() { g_stop; rm -rf "$W"; }
trap cleanup EXIT
start=$SECONDS
qemu-img create -f raw "$W/a.img" 2G >/dev/null
qemu-img create -f raw "$W/b.img" 2G >/dev/null
g_boot "$W/a.img" "$W/b.img" || { echo "FAIL boot: guest didn't come up"; exit 1; }
ssh_g 'rm -rf ~/ck && mkdir ~/ck'
ssh_g 'cat > ~/ck/rustic && chmod 755 ~/ck/rustic' <"$RB"
ssh_g 'cat > ~/ck/attrs.py' <~/backotter-work/testvm/haiku/attrs.py
ssh_g 'cat > ~/ck/ck.py' <"$H/ck.py"
ssh_g 'cat > ~/ck/guest.sh' <"$H/guest.sh"
T=840 ssh_g 'bash ~/ck/guest.sh' >"$LOG/guest.log" 2>&1
echo "guest.sh exit $?" >>"$LOG/guest.log"
ssh_g 'cd ~/ck/out && tar cf - .' | tar -C "$LOG" -xf - 2>/dev/null
# BackOtter's restic (Linux) reads the Haiku repository: check, restore.
if [[ -x $RESTIC ]]; then
    mkdir -p "$W/repo" "$W/rr"
    ssh_g 'cd ~/ck/repo && tar cf - .' | tar -C "$W/repo" -xf -
    export RESTIC_PASSWORD=stictest RESTIC_REPOSITORY=$W/repo RESTIC_CACHE_DIR=$W/cache
    { "$RESTIC" check --read-data; echo "restic check exit $?"; } >"$LOG/restic-check.log" 2>&1
    { "$RESTIC" restore latest --target "$W/rr"; echo "restic restore exit $?"; } >"$LOG/restic-restore.log" 2>&1
    { "$RESTIC" ls --json latest; } >"$LOG/restic-ls.json" 2>"$LOG/restic-ls.err"
    # contents and user.* xattrs, against the guest's dump of the source
    python3 - "$LOG/src.json" "$W/rr/bkoA/anc1/anc2/src" >"$LOG/restic-cmp.txt" 2>&1 <<'EOF'
import hashlib, json, os, sys
src, top = sys.argv[1], sys.argv[2]
bad = n = 0
for line in open(src):
    e = json.loads(line)
    p = os.path.join(top, e["path"])
    if e["kind"] == "file":
        n += 1
        try:
            d = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
        except OSError as x:
            print("missing", e["path"], x); bad += 1; continue
        if d != e["data"]:
            print("data", e["path"]); bad += 1
    if e["kind"] in ("file", "dir") and isinstance(e.get("xattr"), dict):
        for name, (ln, h) in e["xattr"].items():
            try:
                v = os.getxattr(p, name, follow_symlinks=False)
            except OSError as x:
                print("xattr", e["path"], name, x); bad += 1; continue
            if hashlib.sha256(v).hexdigest()[:16] != h:
                print("xattr value", e["path"], name); bad += 1
print("files", n, "bad", bad)
EOF
    chmod -R u+w "$W/rr" 2>/dev/null
fi
cp "$W/serial.log" "$LOG/" 2>/dev/null
echo "elapsed $((SECONDS - start)) s" >>"$LOG/guest.log"

# Verdicts
python3 - "$LOG" "$FILTER" <<'EOF'
import json, os, re, sys
log, filt = sys.argv[1], sys.argv[2]
def rd(n):
    try: return open(os.path.join(log, n), errors="replace").read()
    except OSError: return None
def diffs(n):
    t = rd(n) or ""
    out = {}
    for l in t.splitlines():
        if "\t" in l:
            k, v = l.split("\t", 1); out.setdefault(k, []).append(v)
    return out
g = rd("guest.log") or ""
ex = dict(re.findall(r"^EXIT (\S+) (\d+)", rd("exits.txt") or "", re.M))
res = []
def say(st, need, detail, ev):
    if filt and not re.search(filt, need): return
    res.append({"status": st, "need": need, "detail": detail, "evidence": ev})
    print("%s %s: %s" % (st, need, detail))
host = os.uname().nodename.split(".")[0]
E = lambda f: "%s:%s" % (host, os.path.join(log, f))
def exitok(k): return ex.get(k) == "0"
for k, what in [("backup", "backup"), ("restore1", "restore into a fresh BFS"), ("restore2", "restore over a changed tree"),
                ("restore3", "restore onto the guest's /boot volume"), ("restore4", "restore onto the guest's ramfs (/boot/system/var/shared_memory)"), ("nr-backup", "non-root backup"), ("nr-restore", "non-root restore")]:
    say("PASS" if exitok(k) else "FAIL", "exit status: " + what, "exit %s" % ex.get(k, "none"), E("exits.txt"))
warn = "\n".join(l for l in (rd("warnings.txt") or "").splitlines() if "zoneinfo" not in l)
say("PASS" if not warn.strip() else "FAIL", "no warnings in backup/restore logs", warn.strip()[:300] or "none", E("warnings.txt"))
fx = {"r1": "cmp-restore1.txt", "r2": "cmp-restore2.txt", "r3": "cmp-restore3.txt", "r4": "cmp-restore4.txt"}
d = {k: diffs(v) for k, v in fx.items()}
lim = json.loads(rd("limits.json") or "{}")
needs = [("harness", "harness: dumps made and compared"), ("contents", "contents, types, sizes (incl. empty/deep dirs, odd and non-UTF-8 names)"),
         ("modes", "modes incl. setuid/setgid/sticky"), ("owners", "numeric owners incl. ids with no name"),
         ("mtimes", "mtimes to the ns"), ("atimes", "atimes to the ns"),
         ("typed-attrs", "Haiku typed attributes (incl. the guest's real ones, empty, 20 KB, on dirs)"),
         ("xattrs", "extended attributes (XATR)"), ("symlinks", "symlink targets (relative, absolute, dangling)"),
         ("symlink-typed-attrs", "a symlink's own typed attributes"), ("symlink-xattrs", "a symlink's own xattrs"),
         ("symlink-owners", "a symlink's own owner"), ("symlink-mtimes", "a symlink's own mtime"),
         ("symlink-atimes", "a symlink's own atime"),
         ("hardlinks", "hard links"), ("sparse", "sparse files (holes kept)"), ("fifo-times", "FIFO times")]
for key, need in needs:
    if key in lim:
        say("LIMIT", need, lim[key], E("limits.json")); continue
    bad = [(r, x) for r in ("r1", "r2", "r3", "r4") for x in d[r].get(key, [])]
    rp = rd("ramfs-probe.txt") or ""
    def ramfs_limit(x):  # what Haiku's ramfs itself can't keep (ramfs-probe.txt)
        m = re.match(r"restore4-ramfs: .* (mtime|atime) (\d+) -> (\d+)$", x)
        if m and "file mtime_ns 1600000000000000000" in rp and int(m.group(3)) == int(m.group(2)) // 10**9 * 10**9:
            return True
        return bool(re.match(r"restore4-ramfs: .* mode 0o777 -> 0o0$", x)) and "symlink mode 0o120000" in rp
    lims = [x for _, x in bad if ramfs_limit(x)]
    bad = [(r, x) for r, x in bad if not ramfs_limit(x)]
    if lims and not bad:
        say("LIMIT", need, "ramfs keeps whole seconds / symlink mode 0 (ramfs-probe.txt): " + "; ".join(lims[:3]), E("cmp-restore4.txt")); continue
    say("FAIL" if bad else "PASS", need, "; ".join(x for _, x in bad[:4]) or "restore1-4 identical", E(fx[bad[0][0]] if bad else "cmp-restore1.txt"))
other = sorted({k for r in d.values() for k in r} - {k for k, _ in needs})
for k in other:
    say("FAIL", "other: " + k, "; ".join(x for r in d.values() for x in r.get(k, []))[:300], E("cmp-restore1.txt"))
for k in sorted(set(lim) - {k for k, _ in needs}):
    say("LIMIT", k, lim[k], E("limits.json"))
mk = rd("mknod.txt") or ""
say("PASS" if "mknod exit 0" in mk else "LIMIT", "device nodes", "made in the fixture" if "mknod exit 0" in mk else "Haiku can't make device nodes: " + " ".join(mk.split())[-200:], E("mknod.txt"))
so = (rd("sockets.txt") or "").split()
say("LIMIT" if so[:2] == ["not", "made"] else ("PASS" if so[-1:] == ["skipped"] else "FAIL"), "sockets skipped", " ".join(so), E("sockets.txt"))
a = diffs("cmp-ancestors.txt")
say("FAIL" if a else "PASS", "ancestor directories' metadata (mode, owner, mtime, attributes)", "; ".join(a.get("ancestors", []))[:300] or "anc1, anc2 identical", E("cmp-ancestors.txt"))
s = diffs("cmp-source.txt")
say("FAIL" if s else "PASS", "backup leaves the source's metadata alone (atimes incl.)", "; ".join(x for v in s.values() for x in v)[:300] or "source dump unchanged", E("cmp-source.txt"))
nr = diffs("cmp-nonroot.txt")
say("FAIL" if nr else "PASS", "non-root backup and restore (uid 4400; 0444 file with attrs, setgid dir)", "; ".join(x for v in nr.values() for x in v)[:300] or "identical", E("cmp-nonroot.txt"))
t = diffs("naming.txt")
say("FAIL" if t.get("naming") or not t.get("checked") else "PASS", "snapshot attribute naming per rustic_core BACKOTTER.md (haiku.attributes, XATR as xattrs, no birth time)",
    "; ".join(t.get("naming", []))[:300] or ", ".join(t.get("checked", ["no nodes"])), E("naming.txt"))
same = (rd("same-tree.txt") or "") + " fields that differ: " + (rd("tree-diff.txt") or "").strip()
try: tdiff = set(json.loads(rd("tree-diff.txt") or "{}"))
except ValueError: tdiff = {"?"}
st = "PASS" if "same tree" in same else ("LIMIT" if tdiff and tdiff <= {"atime", "subtree"} and "atimes" in lim else "FAIL")
say(st, "repeated backup of an unchanged tree gives the same tree", ("only atimes differ: BFS keeps none and stat gives a new one each time; " if st == "LIMIT" else "") + same.strip()[:200], E("tree-diff.txt"))
say("PASS" if exitok("check") else "FAIL", "rustic check --read-data", "exit %s" % ex.get("check"), E("exits.txt"))
rc = rd("restic-check.log")
if rc is not None:
    say("PASS" if "restic check exit 0" in rc else "FAIL", "same repository format: BackOtter's restic (Linux) checks the Haiku repository", rc.strip().splitlines()[-1] if rc.strip() else "", E("restic-check.log"))
    rr, cmpt = rd("restic-restore.log") or "", rd("restic-cmp.txt") or ""
    ok = "restic restore exit 0" in rr and re.search(r"bad 0$", cmpt.strip())
    say("PASS" if ok else "FAIL", "same repository format: restic restores rustic's Haiku snapshot (contents, user.* xattrs)", (rr.strip().splitlines() or [""])[-1] + "; " + (cmpt.strip().splitlines() or [""])[-1], E("restic-cmp.txt"))
say("LIMIT", "birth times", "BFS keeps st_crtim but rustic_core BACKOTTER.md: unix.birthtime neither saved nor restored on Haiku", "rustic_core/BACKOTTER.md:419")
for n, why in [("POSIX/NFSv4 ACLs", "Haiku has no ACLs"), ("BSD/Linux file flags, immutable/append-only", "Haiku/BFS has no file flags; restore over a 0444 file with attributes is covered instead"),
               ("SELinux/MAC labels, capabilities", "Haiku has no security modules or capabilities"), ("project IDs, per-file fs options", "BFS has none")]:
    say("LIMIT", n, why, "not applicable on Haiku")
json.dump(res, open(os.path.join(log, "results.json"), "w"), indent=1)
print("results: %s" % os.path.join(log, "results.json"))
sys.exit(1 if any(r["status"] == "FAIL" for r in res) else 0)
EOF
