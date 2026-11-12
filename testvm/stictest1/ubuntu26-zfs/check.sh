#!/usr/bin/env bash
# SticTest1: the targeted metadata test of ubuntu26-zfs (Ubuntu 26.04 by UEFI
# with Secure Boot, laid out by Subiquity's guided ZFS: bpool for /boot,
# rpool for /), ubuntu24's check, whose real.sh it runs.  Never a whole-disk
# run.
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  holds restic-bko and rustic-bko for Linux (roundN/bin/linux)
#   PORT     the throwaway guest's SSH port; PREFIX names scratch and datasets
#   NEED_FILTER  a regex: print only the needs it matches
# In a throwaway overlay of ~/backotter-work/vms/ubuntu26-zfs (vm-run --snapshot,
# its overlay in /mnt/bko-ram/PREFIX), taken through guest-slot.sh and
# cmp-run.sh --reserve 13 6 (the overlay grows to about 12 GiB):
#   1. fs_roundtrip.py zfs-ds (datasets in the guest's own root pool), zfs
#      (pools on files) and ext4 (ubuntu24's /boot type; this guest's /boot
#      is on bpool): torture.sh's tree
#      (xattrs, ACLs, inode flags, project IDs, DOS flags, sparse files,
#      devices, FIFOs, the socket, owners with no name, odd names...) in all
#      four directions, a restore over a changed copy, node identity; judged
#      by verdict.py judge fsrt;
#   2. real.sh: the guest's own examples (capabilities of ping/mtr-packet/
#      snap-confine, the journal's ACLs, holes and user.crtime_usec, the
#      random seed's xattr, set-id programs), owners with no name under the
#      guest's own NSS, ancestors of the backed-up path, immutable and
#      append-only files restored over, all four directions, node identity;
#   3. nonroot-inner.sh on a ZFS dataset: restores as uid 4321.
# Prints "PASS need: detail", "FAIL need: detail" or "LIMIT need: reason",
# writes results.json and logs in ~/backotter-work/stictest1/ubuntu26-zfs/logs/,
# exit 0 if nothing FAILs.
set -uo pipefail
[[ $# -ge 3 ]] || { sed -n '2,29p' "$0" >&2; exit 2; }
BIN=$(cd "$1" && pwd) PORT=$2 PREFIX=$3 FILTER=${4:-}
WORK=$HOME/backotter-work GUEST=ubuntu26-zfs
SELF=$(readlink -f "$0")
HERE=$(dirname "$SELF") STC_LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}
[[ -x $BIN/restic-bko && -x $BIN/rustic-bko ]] || { echo "no restic-bko/rustic-bko in $BIN" >&2; exit 2; }

if [[ -z ${CHECK_RESERVED:-} ]]; then
    . ${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh
    slot_take 3 "$(slot_range_here)"
    CHECK_RESERVED=1 HOT_C=90 $WORK/cmp-run.sh --reserve 13 6 "$SELF" "$BIN" "$PORT" "$PREFIX" "$FILTER"
    rc=$?
    slot_give
    exit $rc
fi

RAM=/mnt/bko-ram/$PREFIX/$GUEST
LOGS=$STC_LOGS/$(date +%Y%m%d-%H%M%S)
mkdir -p "$RAM" "$LOGS"
: >"$LOGS/verdicts.txt"
. ${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh
cleanup() { vm_halt; rm -rf "$RAM"; rmdir /mnt/bko-ram/$PREFIX 2>/dev/null; }
trap cleanup EXIT
v() {  # VERDICT NEED DETAIL
    echo "$1 $2: $3" >>"$LOGS/verdicts.txt"
}
START=$SECONDS
export TMPDIR=$RAM
vm_boot $GUEST "$PORT" "$LOGS/vm" --snapshot --memory 4096 || { echo "FAIL boot: throwaway $GUEST did not boot"; exit 1; }
unset TMPDIR
C=/root/bko/testvm/compare
vm_ssh "mkdir -p $C /root/bko/bin" || exit 1
for f in fs_roundtrip.py compare.py fsmeta.py torture.sh rt-common.sh nonroot-inner.sh; do vm_put ${BKO_TESTVM:-$WORK/testvm}/compare/$f $C/$f || exit 1; done
vm_put "$HERE/../ubuntu24/real.sh" $C/real.sh || exit 1
vm_put "$BIN/restic-bko" /root/bko/bin/restic-bko && vm_put "$BIN/rustic-bko" /root/bko/bin/rustic-bko || exit 1
RB=/root/bko/bin/restic-bko UB=/root/bko/bin/rustic-bko
T=300 vm_sh 'uname -r; ldd --version | head -1; grep -E "^(passwd|group)" /etc/nsswitch.conf; zfs version | head -1
  /root/bko/bin/restic-bko version | head -1; /root/bko/bin/rustic-bko --version
  if ! command -v setfattr >/dev/null || ! command -v setfacl >/dev/null; then
    { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q attr acl; } >/root/bko/apt.log 2>&1 || echo "could not install attr acl"
  fi; tail -n 25 /root/bko/apt.log 2>/dev/null; for c in setfattr setfacl getcap; do command -v $c; done' >"$LOGS/guest.txt" 2>&1
grep -q "/setfacl$" "$LOGS/guest.txt" || v FAIL harness "no setfacl in the guest (see guest.txt)"

# Can this guest's ZFS keep an atime it is given?  The 24.04 release's
# (zfs-2.2.2-0ubuntu9 on 6.8.0-31) stores every atime set on it as 0: touch -a
# reads back 0, so no restore can bring one back there (later 0ubuntu9.x keep
# them).  Then an atime difference on ZFS whose restored atime is 0 isn't
# counted (a LIMIT); every other one is, and ext4 here checks atimes in full.
ZAT=$(T=120 vm_sh "P=\$(zfs list -H -o name / | cut -d/ -f1)
  zfs create -o mountpoint=/mnt/$PREFIX-atime \$P/$PREFIX-atime >/dev/null 2>&1 && : >/mnt/$PREFIX-atime/f &&
    touch -a -d @1000000000 /mnt/$PREFIX-atime/f && stat -c %X /mnt/$PREFIX-atime/f
  zfs destroy \$P/$PREFIX-atime >/dev/null 2>&1; rmdir /mnt/$PREFIX-atime 2>/dev/null; true")
echo "ZFS atime probe (touch -a -d @1000000000 reads back): ${ZAT:-nothing}" >>"$LOGS/guest.txt"
ZFS_ATIME0=""
[[ $ZAT == 0 ]] && ZFS_ATIME0=1

# 1. fs_roundtrip.py
for fs in zfs-ds zfs ext4; do
    s=$SECONDS
    T=420 vm_ssh "cd /root/bko && BKO_FSRT_PREFIX=$PREFIX RESTIC_BKO=$RB RUSTIC_BKO=$UB python3 -I $C/fs_roundtrip.py $fs" >"$LOGS/fsrt-$fs.log" 2>&1
    rc=$?
    rm -rf "${LOGS:?}/fsrt-$fs" && mkdir -p "$LOGS/fsrt-$fs"
    T=300 vm_ssh "d=\$(dirname \$(find /root -path '*fs-roundtrip*/$PREFIX/$fs/results.json' | head -n 1)) && cd \$d && tar -cf - --exclude=./restic-bko --exclude=./rustic-bko --exclude=./restic-to-rustic --exclude=./rustic-to-restic . && rm -rf \$d" |
        tar -C "$LOGS/fsrt-$fs" -xf - 2>/dev/null
    if [[ -n $ZFS_ATIME0 && $fs == zfs* && -f $LOGS/fsrt-$fs/results.json ]]; then
        # results.orig.json keeps what fs_roundtrip.py found
        python3 - "$LOGS/fsrt-$fs" >"$LOGS/fsrt-$fs.atime0" 2>&1 <<'EOF'
import json, os, sys
d = sys.argv[1]
r = json.load(open(os.path.join(d, "results.json")))
json.dump(r, open(os.path.join(d, "results.orig.json"), "w"), indent=1)
def load(n):
    try: return json.load(open(os.path.join(d, n)))
    except (OSError, ValueError): return {}
n = 0
for tool, t in r.get("tools", {}).items():
    for key, man in (("diff_all", f"after-{tool}.json"), ("over_diff_all", f"after-over-{tool}.json"),
                     ("rerun_diff_all", f"after-rerun-{tool}.json")):
        dd, m = t.get(key) or {}, load(man)
        for k in ("atime", "atime (dirs)", "atime (symlinks)"):
            if k in dd:
                keep = [p for p in dd[k] if (m.get(p) or {}).get("atime") != 0]
                n += len(dd[k]) - len(keep)
                if keep: dd[k] = keep
                else: del dd[k]
# fs_roundtrip.py puts the atimes the first backup moved back before the
# second: here that sets them to 0, which the second tool then stores
nd = r.get("snapshot_node_diff (restic-bko vs rustic-bko)") or {}
if "atime" in nd:
    keep = [x for x in nd["atime"] if '"1970-01-01T00:00:00Z"' not in x]
    n += len(nd["atime"]) - len(keep)
    if keep: nd["atime"] = keep
    else: del nd["atime"]
json.dump(r, open(os.path.join(d, "results.json"), "w"), indent=1)
print(f"{n} atime differences with a 0 atime not counted")
EOF
    fi
    python3 ${BKO_TESTVM:-$WORK/testvm}/compare/verdict.py judge fsrt "$LOGS/fsrt-$fs.log" --results "$LOGS/fsrt-$fs" --platform linux \
        --step ubuntu26-zfs --target "$fs" --rc $rc --wall $((SECONDS - s)) >"$LOGS/fsrt-$fs.rows" 2>&1
    python3 - "$LOGS/fsrt-$fs.rows" "$fs" >>"$LOGS/verdicts.txt" <<'EOF'
import json, sys
rows, fs = [], sys.argv[2]
for l in open(sys.argv[1]):
    try: rows.append(json.loads(l))
    except ValueError: pass
if not rows: print(f"FAIL fs_roundtrip {fs}: no verdict rows"); sys.exit()
for r in rows:
    d = r.get("direction") or "shared"
    c = "; ".join(r.get("checks") or [])[:600] or "all checks"
    print(f"{'PASS' if r['verdict'] == 'PASS' else 'FAIL'} fs_roundtrip {fs} {d}: {c}")
EOF
done

# 2. the guest's own examples
T=600 vm_ssh "bash $C/real.sh $PREFIX $RB $UB /root/bko/real" >"$LOGS/real.log" 2>&1
mkdir -p "$LOGS/real"
T=120 vm_ssh "cd /root/bko/real && tar -cf - . && rm -rf /root/bko/real" | tar -C "$LOGS/real" -xf - 2>/dev/null
python3 - "$LOGS/real.log" "$LOGS/real" "$ZFS_ATIME0" >>"$LOGS/verdicts.txt" <<'EOF'
import json, sys, re, os
res = {}
for l in open(sys.argv[1], errors="replace"):
    if l.startswith("NEED-RESULT "):
        p = l.split(None, 2); res.setdefault(p[1], []).append(p[2].strip() if len(p) > 2 else "")
d = sys.argv[2]
def manifest(name):
    try: return json.load(open(os.path.join(d, name)))
    except Exception: return {}
def atime0(name):
    """This ZFS stores every atime set on it as 0 (ZFS_ATIME0): are all the
    restored tree's atime differences 0s?"""
    if not sys.argv[3] or name == "diff-src-after.json": return False
    m = manifest(("over-" + name[10:]) if name.startswith("diff-over-") else ("dst-" + name[5:]))
    s = manifest("src.json")
    return bool(m) and all(m[p].get("atime") == 0 for p in s.keys() & m.keys() if s[p].get("atime") != m[p].get("atime"))
def diffpaths(name):
    try: j = json.load(open(os.path.join(d, name)))
    except Exception: return None
    df = {k: [e for e in v.get("examples", []) if not (k == "missing" and e.endswith("/sock"))] for k, v in (j.get("differences") or {}).items()}
    # S1 (BACKOTTER.md): reading a symlink moves its atime; fsmeta reads restored trees
    df.pop("atime (symlinks)", None)
    if atime0(name): df.pop("atime", None); df.pop("atime (dirs)", None)
    return json.dumps({k: v for k, v in df.items() if v})
def txt(name):
    try: return open(os.path.join(d, name), errors="replace").read().strip().replace("\n", " | ")[:500]
    except OSError: return "(none)"
def first(k): return (res.get(k) or ["(missing)"])[0]
dirs = ["restic-restic", "rustic-rustic", "restic-rustic", "rustic-restic"]
needs = {  # need: paths under the fixture whose differences fail it
 "xattrs user/trusted/security, order": ["xattrs", "random-seed", "journal"],
 "security.capability (ping, mtr-packet, snap-confine)": ["/ping", "mtr-packet", "snap-confine"],
 "POSIX ACLs access+default (journal, acl, dacl)": ["journal", "/acl", "dacl"],
 "inode flags (immutable, append, nodump, noatime) and project IDs on ZFS": ["immutable", "append", "nodump", "noatime", "proj"],
 "ownership incl. uids/gids with no name": ["nouid", "nogid", "noboth", "nodir", "tester", "/link"],
 "set-id and sticky modes": ["passwd", "chage", "crontab", "sticky", "sgid"],
 "hard links": ["nouid.hl"],
 "sparse files (guest's journal, index.db, made)": ["sparse", "index.db", "journal"],
 "device nodes (incl. rdev 0) and FIFOs": ["null", "rdev0", "blk", "fifo"],
 "symlinks with their own times and owner": ["/link"],
 "non-UTF-8 names, empty and deep dirs": ["bad-", "empty", "deep"],
 "ancestors of the backed-up path": ["anc1", "anc2", "\"\"", "\".\""],
}
for k in ["backup-restic", "backup-rustic"]:
    rc = first(k)
    print(f"{'PASS' if rc == '0' else 'FAIL'} {k} (guest NSS files systemd, owners with no name): exit {rc}")
for dd in dirs:
    for pre in ["restore", "over"]:
        rc = first(f"{pre}-{dd}")
        print(f"{'PASS' if rc == '0' else 'FAIL'} {'restore over existing immutable/append-only' if pre == 'over' else 'restore'} {dd}: exit {rc}" + ("" if rc == "0" else " " + txt(f"{pre}-{dd}.log")[:300]))
for need, keys in needs.items():
    bad = []
    for dd in dirs:
        for pre in ["diff-", "diff-over-"]:
            s = diffpaths(f"{pre}{dd}.json")
            if s is None: bad.append(f"{pre}{dd}: no diff"); continue
            for k in keys:
                if k in s: bad.append(f"{pre}{dd} names {k.strip(chr(34)) or 'root'}"); break
    print(f"{'FAIL' if bad else 'PASS'} real-examples {need}: " + ("; ".join(bad)[:500] if bad else "same in all four directions and over-restores"))
for dd in dirs:
    rc = "0" if diffpaths(f"diff-{dd}.json") == "{}" else first(f"compare-{dd}")
    print(f"{'PASS' if rc == '0' else 'FAIL'} real-examples fsmeta {dd}: " + txt(f"diff-{dd}.txt")[:400])
    rc = "0" if diffpaths(f"diff-over-{dd}.json") == "{}" else first(f"compare-over-{dd}")
    print(f"{'PASS' if rc == '0' else 'FAIL'} real-examples fsmeta over {dd}: " + txt(f"diff-over-{dd}.txt")[:400])
n = first("nodes")
print(f"{'PASS' if n.split()[0] == '0' else 'FAIL'} same repository format: restic-bko and rustic-bko snapshot nodes from /mnt down (ancestors incl.): {n.split()[-1] if n.split() else ''} nodes; " + txt("nodes-diff.txt")[:400])
sk = [x.split()[-1] for x in res.get("socketnodes", [])] + [first(f"socket-restored-{dd}") for dd in dirs] + [first("backup-restic-socketmsgs"), first("backup-rustic-socketmsgs")]
print(f"{'PASS' if all(x == '0' for x in sk) else 'FAIL'} sockets skipped (nodes, restored, messages): {' '.join(sk)}")
sa = diffpaths("diff-src-after.json")
print(f"{'PASS' if sa == '{}' else 'FAIL'} backups leave the source alone (atimes; symlinks' moved by readlink, S1): " + txt("diff-src-after.txt")[:300])
EOF

# 3. one non-root run
# uid 4321 can't reach /root: the binaries go to /opt for this run
T=600 vm_sh "mkdir -p /opt/$PREFIX && cp $RB $UB /opt/$PREFIX/ && chmod 755 /opt/$PREFIX /opt/$PREFIX/* &&
  zfs create -o mountpoint=/mnt/$PREFIX-nr -o acltype=posix -o xattr=sa \$(zfs list -H -o name / | cut -d/ -f1)/$PREFIX-nr &&
  FSMETA_DIR=$C sh $C/nonroot-inner.sh /mnt/$PREFIX-nr /opt/$PREFIX/restic-bko /opt/$PREFIX/rustic-bko; rc=\$?; rm -rf /opt/$PREFIX; chattr -R -i -a /mnt/$PREFIX-nr 2>/dev/null
  zfs destroy -r \$(zfs list -H -o name / | cut -d/ -f1)/$PREFIX-nr; rmdir /mnt/$PREFIX-nr; exit \$rc" >"$LOGS/nonroot.log" 2>&1
nrc=$?
python3 - "$LOGS/nonroot.log" $nrc >>"$LOGS/verdicts.txt" <<'EOF'
import re, sys
t = open(sys.argv[1], errors="replace").read()
s = t[t.rfind("== summary"):] if "== summary" in t else ""
if not s: print(f"FAIL non-root restore: no summary (exit {sys.argv[2]})"); sys.exit()
bad = []
for l in s.splitlines():
    m = re.match(r"\s+(\S+): exit (\d+).*D1, user.b back on xattr-order: ([^;\s]+).*sockets restored: (\d+), messages naming one: (\d+)", l)
    if m:
        if m.group(2) not in ("0", "1"): bad.append(f"{m.group(1)} exit {m.group(2)}")
        if m.group(3) not in ("yes", "n/a"): bad.append(f"{m.group(1)} user.b lost")
        if m.group(4) != "0" or m.group(5) != "0": bad.append(f"{m.group(1)} socket")
# the restores against each other, but atimes only of hard links and symlinks (fsmeta reads them)
sec = None
for l in t.splitlines():
    m = re.match(r"== (\S+ vs \S+) ", l)
    if m: sec = m.group(1); continue
    if l.startswith("=="): sec = None; continue
    m = re.match(r"\s+(\S+): (.*)", l)
    if sec and m and not (re.match(r"(hardlink-|symlink)", m.group(1)) and re.fullmatch(r"atime=\d+ -> \d+", m.group(2))):
        bad.append(f"{sec}: {l.strip()[:120]}")
print(f"{'FAIL' if bad else 'PASS'} non-root restore (uid 4321, ZFS): " + ("; ".join(bad) if bad else " ".join(x.strip() for x in s.splitlines()[1:])[:500]))
EOF

[[ -n $ZFS_ATIME0 ]] && v LIMIT "atimes restored on ZFS" "this image's ZFS ($(sed -n 5p "$LOGS/guest.txt") on $(sed -n 1p "$LOGS/guest.txt")) stores every atime set on it as 0 (touch -a -d reads back 0): differences whose restored atime is 0 not counted ($(cat "$LOGS"/fsrt-zfs*.atime0 2>/dev/null | tr '\n' ' ')and the guest's examples); ext4 here checks atimes in full"
v LIMIT "birth times" "ZFS crtime and ext4 btime can't be set on Linux (BACKOTTER.md)"
v LIMIT "SELinux labels, NFSv4 ACLs, BSD file flags, system.* other than ACLs" "not on Ubuntu 26.04 with ZFS acltype=posix (AppArmor has no on-disk labels)"
v LIMIT "whole-disk needs (boot, zpool.cache, bootloader)" "out of scope: whole-disk"
echo "wall: $((SECONDS - START)) s" >>"$LOGS/guest.txt"

python3 - "$LOGS/verdicts.txt" "$LOGS/results.json" "$FILTER" <<'EOF'
import json, re, sys
lines = [l.rstrip("\n") for l in open(sys.argv[1]) if l.strip()]
f = sys.argv[3]
out = []
for l in lines:
    verdict, rest = l.split(" ", 1)
    need, _, detail = rest.partition(": ")
    if f and not re.search(f, need): continue
    out.append({"verdict": verdict, "need": need, "detail": detail}); print(l)
json.dump(out, open(sys.argv[2], "w"), indent=1)
sys.exit(1 if any(o["verdict"] == "FAIL" for o in out) else 0)
EOF
rc=$?
cp "$LOGS/results.json" "$STC_LOGS/results.json"
echo "logs: $LOGS"
exit $rc
