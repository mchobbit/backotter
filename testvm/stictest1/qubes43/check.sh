#!/usr/bin/env bash
# SticTest1 for qubes43 (Qubes OS 4.3.2: Xen 4.19 with a Fedora 41 dom0; GPT
# with the ESP, ext4 /boot and LUKS2 holding VG qubes_dom0: thin pool
# root-pool with dom0's ext4 root, thin pool vm-pool with the qubes'
# volumes and their revisions; docs/design/qubes.md, Q6).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  holds restic-bko and rustic-bko for Linux (roundN/bin/linux):
#            dom0 has no network (Qubes gives the NICs to sys-net, which
#            here has none: docs/design/qubes.md Decision 4), so the only
#            way BackOtter backs a Qubes disk up is offline, from Linux (the
#            rescue stick)
#   PORT     unused (dom0 has no SSH; nothing is booted)
#   PREFIX   your name prefix: scratch in STC_SCRATCH_DIR/PREFIX/check-qubes43
#   NEED_FILTER  a regex: only needs matching it are reported
#
# Offline, as Sculpt's check (sculpt2610/check.sh): the guest's disk attached
# read-only here by vm-mount --ro --key with the guest's console-passphrase
# (LUKS opened, qubes_dom0 activated by LVM from throwaway snapshots of its
# PV, as vm-mount does for thin pools), and dom0's root file system alone
# (a non-recursive bind: no /boot, no ESP) is the source: backed up by both
# tools, restored in all four directions into fresh ext4 file systems made
# with the source's features, block and inode size, restored once more over
# a changed restored tree, compared with fsmeta.py, and the two snapshots
# compared node by node (rt-common.sh).  One restore target at a time (the
# tree is ~5.5 GiB est.).  The qubes' volumes are raw block images, not
# file systems, and the disk's layout (GPT, LUKS2, the thin pools and their
# snapshots, Xen's GRUB entries, the NVRAM) is whole-disk: both are
# HoltTest1 1Obscure's (bko-holt's raw volumes, docs/design/qubes.md Q7),
# LIMITs here.  vm-mount activates qubes_dom0 under its own name: one Qubes
# disk at a time on a host (a HoltTest1 run of qubes43 holds it too).
# Prints PASS/FAIL/LIMIT lines, writes results.json to
# ~/backotter-work/stictest1/qubes43/logs ($STC_LOGS), exits 0 if nothing
# FAILs.  Runs under cmp-run.sh --reserve 16 4 (est.: two repositories and
# one restored tree, ~14 GiB); STC_SCRATCH_DIR (default /mnt/bko-ram)
# outside the RAM disk (/bkoscratch) reserves no RAM disk (--reserve 0 4).
set -uo pipefail
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
GUEST=qubes43
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
TV=${BKO_TESTVM:-$WORK/testvm}
C=$TV/compare
BIN=$(cd "$1" && pwd); PORT=$2; PREFIX=$3; FILTER=${4:-.}
LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}; mkdir -p "$LOGS"
for f in restic-bko rustic-bko; do [[ -x $BIN/$f ]] || { echo "check: no $BIN/$f" >&2; exit 2; }; done
GDIR=$WORK/vms/$GUEST
IMG=$GDIR/$GUEST.qcow2
[[ -f $IMG ]] || { echo "check: no guest $GUEST" >&2; exit 2; }
[[ -f $GDIR/console-passphrase ]] || { echo "check: no $GDIR/console-passphrase" >&2; exit 2; }
SCRATCH=${STC_SCRATCH_DIR:-/mnt/bko-ram}

if [[ ${STC_INNER:-} != "$PREFIX" ]]; then
    inner=(sudo env STC_INNER="$PREFIX" BKO_WORK="$WORK" STC_LOGS="$LOGS" STC_SCRATCH_DIR="$SCRATCH"
        ${BKO_TESTVM:+BKO_TESTVM="$BKO_TESTVM"} bash "$HERE/$(basename "$0")" "$BIN" "$PORT" "$PREFIX" "$FILTER")
    if [[ $SCRATCH == /mnt/bko-ram ]]; then HOT_C=90 "$WORK/cmp-run.sh" --reserve 16 4 "${inner[@]}"
    else HOT_C=90 "$WORK/cmp-run.sh" --reserve 0 4 "${inner[@]}"; fi
    exit $?
fi

# ---- inner, as root ----
SCR=$SCRATCH/$PREFIX/check-$GUEST
OUT=$LOGS/check.log
LOOPS=() MNT=""
cleanup() {
    for m in $(findmnt -rno TARGET | grep "^$SCR/" | grep -v "^$SCR/mnt" | sort -r); do umount "$m"; done
    [[ -z $MNT ]] || "$TV/vm-umount" "$MNT"
    for l in "${LOOPS[@]}"; do losetup -d "$l" 2>/dev/null; done
    # Never into a mount left behind (vm-umount failed): the guest's tree.
    findmnt -rno TARGET | grep -q "^$SCR/" || rm -rf "${SCR:?}"
    chown -R "$SUDO_UID:$SUDO_GID" "$LOGS" 2>/dev/null
}
trap cleanup EXIT
trap 'exit 130' INT TERM
rm -rf "${SCR:?}"; mkdir -p "$SCR"; : >"$OUT"
exec 3>&1 >>"$OUT" 2>&1
ck() { printf 'CK\t%s\t%s\t%s\n' "$1" "$2" "$3"; }
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
RESTIC=$BIN/restic-bko RUSTIC=$BIN/rustic-bko
"$RESTIC" version; "$RUSTIC" --version
. "$C/rt-common.sh"

# The guest's disk, read-only: vm-mount's throwaway PV snapshots (their
# copy-on-write files in TMPDIR) go in the scratch.
mkdir -p "$SCR/mnt" "$SCR/tmp"
if TMPDIR=$SCR/tmp "$TV/vm-mount" --ro --key "$GDIR/console-passphrase" "$IMG" "$SCR/mnt"; then
    MNT=$SCR/mnt
else
    ck harness FAIL "vm-mount --ro --key of $IMG failed"
fi
S=$SCR/src; mkdir -p "$S"
[[ -n $MNT ]] && mount --bind "$SCR/mnt" "$S" && mount -o remount,bind,ro "$S"
ID=$(sed -n 's/^ID=//p' "$S/usr/lib/os-release" "$S/etc/os-release" 2>/dev/null | head -n 1 | tr -d '"')
DEV=$(findmnt -no SOURCE "$SCR/mnt")
echo "source: $DEV, ID=$ID; $(find "$S" -xdev | wc -l) entries; $(du -sxh "$S" | cut -f1)"
if [[ $ID == qubes ]]; then
    ck "restic-bko runs natively" LIMIT "not run: dom0 has no network (docs/design/qubes.md Decision 4), so BackOtter backs a Qubes disk up offline, from the rescue stick (this check)"
    ck "rustic-bko runs natively" LIMIT "not run: dom0 has no network (docs/design/qubes.md Decision 4), so BackOtter backs a Qubes disk up offline, from the rescue stick (this check)"
else
    ck harness FAIL "the root at $DEV isn't Qubes OS's (os-release ID=$ID)"
fi

# Offline: dom0's root as the source.
FEAT=$(dumpe2fs -h "$DEV" 2>/dev/null | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' |
    grep -vxE 'needs_recovery|orphan_present|mmp|' | paste -sd, -)
BS=$(dumpe2fs -h "$DEV" 2>/dev/null | awk -F: '$1 == "Block size" { gsub(/ /, "", $2); print $2 }')
IS=$(dumpe2fs -h "$DEV" 2>/dev/null | awk -F: '$1 == "Inode size" { gsub(/ /, "", $2); print $2 }')
echo "ext4: block $BS, inode $IS, features $FEAT"
mkfs_dst() {  # NAME -> mounts a fresh ext4 like dom0's root at $SCR/NAME
    truncate -s 24G "$SCR/$1.img"
    mkfs.ext4 -q -F -b "${BS:-4096}" -I "${IS:-256}" ${FEAT:+-O "$FEAT"} "$SCR/$1.img"
    local l; l=$(losetup -f --show "$SCR/$1.img"); LOOPS+=("$l")
    mkdir -p "$SCR/$1"; mount -t ext4 -o noatime "$l" "$SCR/$1"
}
drop_dst() {  # NAME
    local l; l=$(findmnt -no SOURCE "$SCR/$1")
    umount "$SCR/$1"; [[ -z $l ]] || losetup -d "$l"; rm -f "$SCR/$1.img"; rmdir "$SCR/$1"
}
python3 "$C/fsmeta.py" record "$S" "$SCR/before.json"
# Both tools skip sockets (SPEC3 1), and dom0's root has two: the
# qrexec policy daemon's /etc/qubes-rpc/policy.EvalSimple and policy.EvalGUI
# (systemd's qubes-qrexec-policy-daemon socket units, left on the disk).
# Left out of the comparison (a restored one would show as "extra"), and
# checked apart: none in either snapshot.
SOCKS=$(python3 - "$SCR/before.json" <<'PY'
import json, sys
b = json.load(open(sys.argv[1]))
s = sorted(k for k, v in b.items() if v["type"] == "socket")
json.dump({k: v for k, v in b.items() if v["type"] != "socket"}, open(sys.argv[1], "w"))
print(" ".join(s))
PY
)
echo "source sockets: ${SOCKS:-none}"
echo "source: $(getfattr -R -d -m - --absolute-names "$S" 2>/dev/null | grep -c '^[a-z]') xattrs; flags: $(lsattr -Ra "$S" 2>/dev/null | awk '{print $1}' | sort | uniq -c | tr '\n' ' ')"
mkdir -p "$SCR/repos"
"$RESTIC" -r "$SCR/repos/restic" init -q && "$RESTIC" -r "$SCR/repos/restic" backup -q --with-atime "$S"; rc1=$?
"$RUSTIC" --no-progress --no-cache -r "$SCR/repos/rustic" init >/dev/null && "$RUSTIC" --no-progress --no-cache -r "$SCR/repos/rustic" backup --set-atime yes "$S" >/dev/null; rc2=$?
[[ $rc1 == 0 ]] && ck "backup exits 0 (offline, dom0's root on LVM thin on LUKS)" PASS "restic" || ck "backup exits 0 (offline, dom0's root on LVM thin on LUKS)" FAIL "restic exit $rc1"
[[ $rc2 == 0 ]] && ck "backup exits 0 (offline, dom0's root on LVM thin on LUKS)" PASS "rustic" || ck "backup exits 0 (offline, dom0's root on LVM thin on LUKS)" FAIL "rustic exit $rc2"
if rt_nodes "$RESTIC" "$SCR/repos/restic" "$S" >"$SCR/n-restic" && rt_nodes "$RESTIC" "$SCR/repos/rustic" "$S" >"$SCR/n-rustic"; then
    cp "$SCR/n-restic" "$LOGS/nodes-restic.txt"; cp "$SCR/n-rustic" "$LOGS/nodes-rustic.txt"
    if rt_node_diff "$SCR/n-restic" "$SCR/n-rustic" >"$SCR/ndiff"; then
        ck "same repository format (node-identical snapshots)" PASS "$(wc -l <"$SCR/n-restic") nodes"
    else ck "same repository format (node-identical snapshots)" FAIL "$(head -5 "$SCR/ndiff" | tr '\n' ' ')"; cat "$SCR/ndiff"; fi
    ns=$(cat "$SCR/n-restic" "$SCR/n-rustic" | grep -c 'type="socket"')
    [[ $ns == 0 ]] && ck "sockets skipped by both tools" PASS "source: $(wc -w <<<"$SOCKS") (${SOCKS:-none}); none in either snapshot" ||
        ck "sockets skipped by both tools" FAIL "$ns socket nodes in the snapshots"
else ck "same repository format (node-identical snapshots)" FAIL "can't decode the snapshots"; fi

restore() {  # TOOL REPO DST
    if [[ $1 == restic ]]; then "$RESTIC" -r "$2" restore latest --target "$3" -q
    else "$RUSTIC" --no-progress --no-cache -r "$2" restore --numeric-id latest "$3" >/dev/null; fi
}
NEED="metadata round trip (contents, types, modes, owners, times, xattrs incl. security.*, hard links, sparse files)"
cmp_tree() {  # LABEL DIR
    python3 "$C/fsmeta.py" record "$2$S" "$SCR/after.json"
    if python3 "$C/fsmeta.py" compare "$SCR/before.json" "$SCR/after.json" >"$SCR/cmp.txt"; then
        ck "$NEED" PASS "$1"
    else ck "$NEED" FAIL "$1: $(head -c 400 "$SCR/cmp.txt" | tr '\n' ' ')"; cat "$SCR/cmp.txt"; fi
}
for d in restic:restic rustic:rustic restic:rustic rustic:restic; do
    b=${d%:*} r=${d#*:} n=${b}2${r}
    mkfs_dst "$n"
    restore "$r" "$SCR/repos/$b" "$SCR/$n"; rc=$?
    [[ $rc == 0 ]] && ck "restore exits 0" PASS "$b->$r" || ck "restore exits 0" FAIL "$b->$r exit $rc"
    cmp_tree "$b->$r" "$SCR/$n"
    if [[ $b == "$r" ]]; then
        # Restore over a changed restored tree.
        T=$SCR/$n$S
        printf 'X' | dd of="$T/etc/fstab" conv=notrunc status=none; echo more >>"$T/etc/hostname"
        chmod 600 "$T/etc/hosts"; touch "$T/etc"; chown 1000:1000 "$T/etc/default"
        restore "$b" "$SCR/repos/$b" "$SCR/$n"; rc=$?
        [[ $rc == 0 ]] && ck "restore over an existing changed tree" PASS "$b exit 0" || ck "restore over an existing changed tree" FAIL "$b exit $rc"
        python3 "$C/fsmeta.py" record "$T" "$SCR/after.json"
        python3 "$C/fsmeta.py" compare "$SCR/before.json" "$SCR/after.json" >"$SCR/cmp.txt" &&
            ck "restore over an existing changed tree" PASS "$b: tree as backed up" ||
            { ck "restore over an existing changed tree" FAIL "$b: $(head -c 300 "$SCR/cmp.txt" | tr '\n' ' ')"; cat "$SCR/cmp.txt"; }
    fi
    drop_dst "$n"
done
ck "the qubes' volumes (vm-* LVs in vm-pool, with their revisions)" LIMIT "out of scope: raw block images, not file systems (template roots are whole disk images): bko-holt's raw volumes, HoltTest1 1Obscure (docs/design/qubes.md Q3, Q7)"
ck "Whole-disk needs (GPT, LUKS2, LVM thin pools and snapshots, Xen's GRUB entries, Qubes's NVRAM entry)" LIMIT "out of scope: whole-disk (HoltTest1 1Obscure recreates the disk, restores the raw volumes and boots Xen and dom0 alone); here only dom0's root's contents are restored"
ck "restore over existing immutable/append-only files" LIMIT "not run in this offline check: the same Linux code paths the online ext4 checks run (debian13's and the others')"
ck "non-root backup and restore" LIMIT "not applicable: the offline path runs as root on the rescue stick"
echo "inner: done"

exec >&3 2>&1
python3 - "$OUT" "$LOGS/results.json" "$BIN" "$FILTER" <<'PY'
import json, re, sys
order, res = [], {}
for line in open(sys.argv[1], errors="replace"):
    p = line.rstrip("\n").split("\t")
    if len(p) != 4 or p[0] != "CK" or not re.search(sys.argv[4], p[1]):
        continue
    _, need, st, det = p
    if need not in res:
        order.append(need); res[need] = {"PASS": [], "FAIL": [], "LIMIT": []}
    res[need][st].append(det)
out, bad = [], False
for need in order:
    r = res[need]
    st = "FAIL" if r["FAIL"] else "PASS" if r["PASS"] else "LIMIT"
    bad |= st == "FAIL"
    print(f"{st} {need}: " + "; ".join(r[st][:4]))
    out.append({"need": need, "result": st, "details": r[st]})
if not any("inner: done" in l for l in open(sys.argv[1], errors="replace")):
    print("FAIL harness: didn't finish"); bad = True
json.dump({"guest": "qubes43", "bin": sys.argv[3], "needs": out,
           "verdict": "FAIL" if bad else "PASS"}, open(sys.argv[2], "w"), indent=1)
sys.exit(1 if bad else 0)
PY
