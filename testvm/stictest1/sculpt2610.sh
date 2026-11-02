#!/usr/bin/env bash
# SticTest1 for sculpt2610 (Sculpt OS 26.10, Genode on base-hw, x86_64).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  holds restic-bko and rustic-bko for Linux (roundN/bin/linux):
#            neither tool runs on Genode, so the only way BackOtter backs a
#            Sculpt disk up is offline, from Linux (the rescue stick)
#   PORT     unused (Sculpt has no SSH; nothing is booted)
#   PREFIX   your name prefix: scratch in /mnt/bko-ram/PREFIX/check-sculpt2610
#   NEED_FILTER  a regex: only needs matching it are reported
#
# Native: the guest's own disk says what runs there (grub.cfg boots Genode's
# base-hw kernel through bender; no Linux/BSD ABI, no Go port, no Rust
# target), so those needs are reported "LIMIT ... (not applicable)".
# Offline (the rescue-stick path): a throwaway raw copy of the guest's disk in
# RAM, its GENODE partition (ext2, written partly by Genode's own ext2 driver:
# times 0, no extra inode fields) mounted read-only on a loop device, backed up
# by both tools, restored in all four directions into fresh ext2 file systems
# made like it, restored once more over a changed restored tree, compared with
# fsmeta.py, and the two snapshots compared node by node (rt-common.sh).
# Prints PASS/FAIL/LIMIT lines, writes logs/results.json, exits 0 if nothing
# FAILs. Runs under cmp-run.sh --reserve 2 2; takes about a minute.
set -uo pipefail
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
GUEST=sculpt2610
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
C=$WORK/testvm/compare
BIN=$(cd "$1" && pwd); PORT=$2; PREFIX=$3; FILTER=${4:-.}
LOGS=$HERE/logs; mkdir -p "$LOGS"
for f in restic-bko rustic-bko; do [[ -x $BIN/$f ]] || { echo "check: no $BIN/$f" >&2; exit 2; }; done
IMG=$WORK/vms/$GUEST/$GUEST.qcow2
[[ -f $IMG ]] || { echo "check: no guest $GUEST" >&2; exit 2; }

if [[ ${STC_INNER:-} != "$PREFIX" ]]; then
    HOT_C=90 "$WORK/cmp-run.sh" --reserve 2 2 sudo env STC_INNER="$PREFIX" BKO_WORK="$WORK" \
        bash "$HERE/$(basename "$0")" "$BIN" "$PORT" "$PREFIX" "$FILTER"
    exit $?
fi

# ---- inner, as root ----
SCR=/mnt/bko-ram/$PREFIX/check-$GUEST
OUT=$LOGS/check.log
LOOPS=()
cleanup() {
    for m in $(findmnt -rno TARGET | grep "^$SCR/" | sort -r); do umount "$m"; done
    for l in "${LOOPS[@]}"; do losetup -d "$l" 2>/dev/null; done
    rm -rf "$SCR"
    chown -R "$SUDO_UID:$SUDO_GID" "$LOGS" 2>/dev/null
}
trap cleanup EXIT
trap 'exit 130' INT TERM
rm -rf "$SCR"; mkdir -p "$SCR"; : >"$OUT"
exec 3>&1 >>"$OUT" 2>&1
ck() { printf 'CK\t%s\t%s\t%s\n' "$1" "$2" "$3"; }
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
RESTIC=$BIN/restic-bko RUSTIC=$BIN/rustic-bko
"$RESTIC" version; "$RUSTIC" --version
. "$C/rt-common.sh"

# Native applicability, from the guest's disk.
qemu-img convert -O raw "$IMG" $SCR/disk.raw || { ck harness FAIL "can't convert $IMG"; }
P3=$(/usr/sbin/sfdisk -d $SCR/disk.raw | awk -F'[=,]' '/name="GENODE/ {gsub(/ /,"");print $2" "$4}')
read -r start size <<<"$P3"
dd if=$SCR/disk.raw of=$SCR/genode.ext2 bs=512 skip="$start" count="$size" conv=sparse status=none
rm -f $SCR/disk.raw
L=$(losetup -r -f --show $SCR/genode.ext2); LOOPS+=("$L")
S=$SCR/src; mkdir -p $S; mount -t ext2 -o ro "$L" $S
cat $S/boot/grub/grub.cfg
KERN=$(zcat $S/boot/image-hw.elf.gz | file -b - | cut -c1-60)
if grep -q 'multiboot2 /boot/bender' $S/boot/grub/grub.cfg && grep -q 'image-hw.elf' $S/boot/grub/grub.cfg; then
    ck "restic-bko runs natively" LIMIT "not applicable: grub.cfg boots Genode base-hw (bender + image-hw.elf: $KERN); Go has no GOOS for Genode, and Genode has no Linux/BSD binary ABI"
    ck "rustic-bko runs natively" LIMIT "not applicable: no Rust target for Genode (would need a custom target spec on Genode's libc + -Zbuild-std, and a Goa package)"
    ck "native metadata (owners, xattrs, ACLs, flags, devices, hard links)" LIMIT "not applicable: Genode's VFS/File_system session has only type, size, rwx and mtime; no owners, xattrs, ACLs, flags, device nodes or hard links"
else
    ck "restic-bko runs natively" FAIL "grub.cfg doesn't boot Genode base-hw: re-inventory"
fi

# Offline: the GENODE partition as the source.
mkfs_dst() {  # NAME -> mounts a fresh ext2 like GENODE at $SCR/NAME
    truncate -s 512M $SCR/$1.img
    mkfs.ext2 -q -F -b 4096 -I 256 -O ext_attr,resize_inode,dir_index,filetype,sparse_super,large_file,^has_journal $SCR/$1.img
    local l; l=$(losetup -f --show $SCR/$1.img); LOOPS+=("$l")
    mkdir -p $SCR/$1; mount -t ext2 -o noatime "$l" $SCR/$1
}
python3 "$C/fsmeta.py" record $S $SCR/before.json
echo "source: $(find $S | wc -l) entries; $(getfattr -R -d -m - --absolute-names $S 2>/dev/null | grep -c '^[a-z]') xattrs; flags: $(lsattr -Ra $S 2>/dev/null | awk '{print $1}' | sort | uniq -c | tr '\n' ' ')"
mkdir -p $SCR/repos
"$RESTIC" -r $SCR/repos/restic init -q && "$RESTIC" -r $SCR/repos/restic backup -q $S; rc1=$?
"$RUSTIC" --no-progress --no-cache -r $SCR/repos/rustic init >/dev/null && "$RUSTIC" --no-progress --no-cache -r $SCR/repos/rustic backup --set-atime yes $S >/dev/null; rc2=$?
[[ $rc1 == 0 ]] && ck "backup exits 0 (offline, GENODE ext2)" PASS "restic" || ck "backup exits 0 (offline, GENODE ext2)" FAIL "restic exit $rc1"
[[ $rc2 == 0 ]] && ck "backup exits 0 (offline, GENODE ext2)" PASS "rustic" || ck "backup exits 0 (offline, GENODE ext2)" FAIL "rustic exit $rc2"
if rt_nodes "$RESTIC" $SCR/repos/restic $S >$SCR/n-restic && rt_nodes "$RESTIC" $SCR/repos/rustic $S >$SCR/n-rustic; then
    cp $SCR/n-restic $LOGS/nodes-restic.txt; cp $SCR/n-rustic $LOGS/nodes-rustic.txt
    if rt_node_diff $SCR/n-restic $SCR/n-rustic >$SCR/ndiff; then
        ck "same repository format (node-identical snapshots)" PASS "$(wc -l <$SCR/n-restic) nodes incl. Genode-written inodes (mtime 0)"
    else ck "same repository format (node-identical snapshots)" FAIL "$(head -5 $SCR/ndiff | tr '\n' ' ')"; cat $SCR/ndiff; fi
else ck "same repository format (node-identical snapshots)" FAIL "can't decode the snapshots"; fi

restore() {  # TOOL REPO DST
    if [[ $1 == restic ]]; then "$RESTIC" -r $2 restore latest --target $3 -q
    else "$RUSTIC" --no-progress --no-cache -r $2 restore latest $3 >/dev/null; fi
}
cmp_tree() {  # LABEL DIR
    python3 "$C/fsmeta.py" record $2$S $SCR/after.json
    if python3 "$C/fsmeta.py" compare $SCR/before.json $SCR/after.json >$SCR/cmp.txt; then
        ck "metadata round trip (contents, types, modes, owners incl. uid 1007 with no name, mtimes/atimes incl. 0 from Genode-written inodes, mode 0777 files)" PASS "$1"
    else ck "metadata round trip (contents, types, modes, owners incl. uid 1007 with no name, mtimes/atimes incl. 0 from Genode-written inodes, mode 0777 files)" FAIL "$1: $(head -c 400 $SCR/cmp.txt | tr '\n' ' ')"; cat $SCR/cmp.txt; fi
}
for d in restic:restic rustic:rustic restic:rustic rustic:restic; do
    b=${d%:*} r=${d#*:} n=${b}2${r}
    mkfs_dst $n
    restore $r $SCR/repos/$b $SCR/$n; rc=$?
    [[ $rc == 0 ]] && ck "restore exits 0" PASS "$b->$r" || ck "restore exits 0" FAIL "$b->$r exit $rc"
    cmp_tree "$b->$r" $SCR/$n
done
# Restore over a changed restored tree (no immutable/append-only: Genode sets no flags).
for t in restic rustic; do
    T=$SCR/${t}2${t}$S
    printf 'X' | dd of=$T/boot/boot.png conv=notrunc status=none; echo more >>$T/depot/version
    chmod 600 $T/boot/bender; touch $T/depot; chown 0:0 $T/boot
    restore $t $SCR/repos/$t $SCR/${t}2${t}; rc=$?
    [[ $rc == 0 ]] && ck "restore over an existing changed tree" PASS "$t exit 0" || ck "restore over an existing changed tree" FAIL "$t exit $rc"
    python3 "$C/fsmeta.py" record $T $SCR/after.json
    python3 "$C/fsmeta.py" compare $SCR/before.json $SCR/after.json >$SCR/cmp.txt &&
        ck "restore over an existing changed tree" PASS "$t: tree as backed up" ||
        { ck "restore over an existing changed tree" FAIL "$t: $(head -c 300 $SCR/cmp.txt | tr '\n' ' ')"; cat $SCR/cmp.txt; }
done
ck "restore over existing immutable/append-only files" LIMIT "not applicable: Genode's ext2 driver sets no inode flags (none on the guest's disk)"
ck "non-root backup and restore" LIMIT "not applicable: Genode has no users; the offline path runs as root on the rescue stick"
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
json.dump({"guest": "sculpt2610", "bin": sys.argv[3], "needs": out,
           "verdict": "FAIL" if bad else "PASS"}, open(sys.argv[2], "w"), indent=1)
sys.exit(1 if bad else 0)
PY
