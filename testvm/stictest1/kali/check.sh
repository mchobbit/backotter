#!/usr/bin/env bash
# SticTest1's targeted check for kali (Kali Linux Rolling 2026.3: Debian
# testing based, glibc, systemd, AppArmor; UEFI with Secure Boot off, GRUB
# (EFI/kali/grubx64.efi and the fallback EFI/BOOT/BOOTX64.EFI, no shim),
# initramfs-tools; d-i's atomic GPT: an 883 MiB ESP at /boot/efi
# (umask=0077), ext4 / (errors=remount-ro), 858 MiB swap;
# docs/design/kali.md).  ubuntu26-uefi's check (an ext4 like /, ext4's
# optional features on their own ext4, the ESP) with chimera-uefi's
# fatattr.py for the ESP and anduinos2's frame: a scratch disk laid out
# from the guest's own `sfdisk -d`, file systems mounted inside the
# backed-up path, each restore target a disk image laid out the same.
#
# Boots a throwaway overlay (vm-run --snapshot; the guest's vm-run-options:
# --uefi --no-secure-boot) with two spare disks: vdb laid out like the
# guest's vda (its GPT: each partition's start, size and type: the ESP,
# ext4 /, swap), vdc for the repositories, the restore targets and the
# logs.  The fixture (inner.sh) is on the ext4 made like / (the guest's
# features, inode and block size, mounted with fstab's options), with the
# ESP (FAT32 made with the guest's ESP's geometry, mounted with fstab's
# umask=0077) at tree/boot/efi and an ext4 with project quotas and
# casefolding at tree/feat, the guest's own files as real examples (set-id
# programs, shadow, fstab, GRUB's defaults and /boot/grub, kali.sources,
# tester's home, the journal with its ACLs, the ESP's GRUB).  Runs
# restic->restic, rustic->rustic, restic->rustic, rustic->restic, a restore
# over existing immutable/append-only files and changed FAT attributes with
# each tool, each tool's check --read-data of the other's repository, one
# non-root run (uid 4321's own tree), and judges (judge.py, fsmeta.py,
# fatattr.py, rt-common.sh's node comparison).  Never a whole-disk run: the
# GPT, GRUB's NVRAM entry and the initramfs are HoltTest1's (a LIMIT here).
# Kali has no attr, acl or dosfstools package: the fixture sets xattrs and
# ACLs from Python, and the FAT32 file systems are made on the host
# (mkfs.vfat with the geometry the guest reads off its own ESP) and copied
# into place in the guest.
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json beside the
# logs (~/backotter-work/stictest1/kali/logs/PREFIX-*), exits 0 if nothing
# FAILs.  Takes a guest slot and a cmp-run.sh reservation itself.
set -u
GUEST=kali
here=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
for b in restic-bko rustic-bko; do [[ -x $BIN/$b ]] || { echo "FAIL binaries: no $BIN/$b"; exit 1; }; done
if [[ -z ${BKST_INNER:-} ]]; then
    # shellcheck source=/dev/null
    . "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    BKST_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 5 "$here/check.sh" "$@"
    rc=$?
    slot_give
    exit $rc
fi
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}
S=/mnt/bko-ram/$PREFIX/$GUEST LOGS=$STC_LOGS/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$S" "$LOGS"
export TMPDIR=$S
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
ram_used() {  # MiB this run holds in the RAM disk: its files and QEMU's --snapshot overlays (deleted, open)
    local p q k=0 f
    desc() { local c; for c in $(pgrep -P "$1"); do echo "$c"; desc "$c"; done; }
    [[ -n ${VM_PID:-} ]] && for p in $VM_PID $(desc "$VM_PID"); do
        [[ $(cat /proc/$p/comm 2>/dev/null) == qemu* ]] || continue
        for f in /proc/$p/fd/*; do
            [[ $(readlink "$f") == "$S"/*" (deleted)" ]] && q=$(stat -L -c '%b*%B' "$f" 2>/dev/null) && k=$((k + q))
        done
    done
    echo "$(( ($(du -sk "$S" | cut -f1) * 1024 + k) >> 20 )) MiB in $S (overlays $((k >> 20)) MiB)"
}
cleanup() {  # the RAM the run took (for --reserve), then everything
    ram_used >"$LOGS/ram-used" 2>&1; vm_halt; rm -rf "$S"; rmdir "/mnt/bko-ram/$PREFIX" 2>/dev/null
}
trap cleanup EXIT
# vdb: the guest-like disk, the guest's vda's size (16 GiB); vdc: work.
qemu-img create -q -f qcow2 "$S/stack.qcow2" 16G && qemu-img create -q -f qcow2 "$S/work.qcow2" 80G
VM_BOOT_TIMEOUT=300 vm_boot $GUEST "$PORT" "$S/vm" --snapshot --memory 3072 --cpus 2 \
    --disk "$S/stack.qcow2" --disk "$S/work.qcow2" ||
    { echo "FAIL harness: $GUEST didn't boot"; exit 2; }
T=60 vm_ssh "mkdir -p /root/bk"
for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" \
         "${BKO_TESTVM:-$WORK/testvm}/compare/torture.sh" "$here/inner.sh" "$here/judge.py" "$here/fatattr.py" "$here/nracl.py"; do
    vm_put "$f" "/root/bk/$(basename "$f")" || { echo "FAIL harness: copying $f"; exit 2; }
done
# The FAT32s: the guest has no mkfs.vfat.  Its ESP's size and geometry
# (bytes per sector, sectors per cluster, reserved sectors, FATs, hidden
# sectors: its start), read in the guest; the file system made here,
# compressed, and written to each ESP partition in the guest (a fresh
# volume id each time).
geo=$(T=60 vm_ssh 'python3 - <<PY
import os, struct
src = os.popen("findmnt -n -o SOURCE /boot/efi").read().strip()
b = open(src, "rb").read(512)
bps, spc, res, nf = struct.unpack_from("<HBHB", b, 11)
hid = struct.unpack_from("<I", b, 28)[0]
n = os.path.basename(src)
size = int(open(f"/sys/class/block/{n}/size").read()) * 512
print(size, bps, spc, res, nf, hid)
PY') && read -r esz bps spc res nf hid <<<"$geo" && [[ $esz =~ ^[0-9]+$ && $esz -gt 0 ]] ||
    { echo "FAIL harness: can't read the guest's ESP geometry: $geo"; exit 2; }
echo "$geo" >"$LOGS/esp-geometry"
truncate -s "$esz" "$S/esp.img" && /usr/sbin/mkfs.vfat -F 32 -S "$bps" -s "$spc" -R "$res" -f "$nf" -h "$hid" "$S/esp.img" >"$LOGS/mkfs.vfat.log" 2>&1 &&
    gzip -1 -c "$S/esp.img" >"$S/esp.img.gz" && rm -f "$S/esp.img" && vm_put "$S/esp.img.gz" /root/bk/esp.img.gz 644 ||
    { echo "FAIL harness: no FAT32 for the ESP: $(tail -2 "$LOGS/mkfs.vfat.log")"; exit 2; }
/usr/sbin/mkfs.vfat --help 2>&1 | head -1 >>"$LOGS/mkfs.vfat.log"; dpkg-query -W dosfstools >>"$LOGS/mkfs.vfat.log" 2>&1
T=1500 vm_ssh "bash /root/bk/inner.sh" >"$LOGS/check.out" 2>&1
echo "inner exit $?" >>"$LOGS/check.out"
T=300 vm_ssh "cat /root/bk/logs.tar" 2>"$LOGS/tar.err" | tar xf - -C "$LOGS" 2>>"$LOGS/tar.err"
cp "$S/vm/serial.log" "$LOGS/serial.log" 2>/dev/null
grep -E '^(PASS|FAIL|LIMIT) ' "$LOGS/check.out" | { [[ -n $FILTER ]] && grep -E -- "$FILTER" || cat; } | tee "$LOGS/lines.txt"
python3 - "$LOGS" "$GUEST" "$BIN" >"$LOGS/results.json" <<'PY'
import json, sys, os, re
L = sys.argv[1]
out = []
for l in open(os.path.join(L, "lines.txt")):
    m = re.match(r"(PASS|FAIL|LIMIT) (.*?): (.*)", l.rstrip("\n"))
    if m: out.append({"result": m[1], "need": m[2], "detail": m[3]})
json.dump({"guest": sys.argv[2], "bin": sys.argv[3], "logs": L, "results": out}, sys.stdout, indent=1)
PY
cp "$LOGS/results.json" "$STC_LOGS/results.json"
echo "logs: $LOGS"
[[ -s $LOGS/lines.txt ]] || { echo "FAIL harness: no results (see $LOGS/check.out)"; exit 1; }
! grep -q '^FAIL ' "$LOGS/lines.txt"
