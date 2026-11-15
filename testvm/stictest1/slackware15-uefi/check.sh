#!/usr/bin/env bash
# SticTest1's targeted check for slackware15 and slackware15-uefi (Slackware
# 15.0: glibc, sysvinit with BSD-style /etc/rc.d scripts, pkgtools; series
# a, ap, d, l and n; fstab and root= by device name; docs/design/slackware.md).
#   slackware15: BIOS, MBR: vda1 swap, vda2 ext4 / (bootable); LILO in the
#     MBR (/etc/lilo.conf, /boot/map), the huge kernel, no initrd.
#   slackware15-uefi: UEFI (no Secure Boot), GPT: vda1 the ESP at /boot/efi
#     (vfat), vda2 swap, vda3 XFS /; ELILO in EFI/Slackware (and EFI/BOOT),
#     the generic kernel with mkinitrd's /boot/initrd.gz.
# solus49's check (the scratch disk laid out from the guest's own `sfdisk -d`,
# its file systems made in the guest with the guest's own mkfs and mounted
# with the guest's options, the ESP inside the backed-up path) with devuan6's
# ext4 and rocky10's and gentoo's XFS.
#
# Boots a throwaway overlay (vm-run --snapshot; the guest's vm-run-options)
# with two spare disks: vdb laid out like the guest's vda (each partition's
# start, size, type and flags: the swap made with mkswap, / made like the
# guest's: ext4 with its features, inode and block size and journal, or XFS
# with its -m/-i options; on -uefi the ESP, a FAT32 with the guest's ESP's
# geometry, at tree/boot/efi, mounted with the guest's options), vdc for the
# repositories, the restore targets and the logs.  slackware15's fixture also
# has ubuntu26-uefi's ext4 with project quotas and casefolding at tree/feat;
# slackware15-uefi's has XFS's project IDs and per-file options (extent size
# hints, nodefrag, filestream) on its XFS.  The guest's own files as real
# examples: the rc.d scripts with their exec bits on and off (what turns a
# service on), inittab, pkgtools' /var/lib/pkgtools/packages, set-id
# programs, shadow, fstab, the syslog files, /boot (LILO's lilo.conf and map
# and the huge kernel; or the generic kernel, /boot/initrd.gz and mkinitrd's
# /boot/initrd-tree with its device nodes and busybox links) and on -uefi the
# ESP's ELILO files with their FAT attributes.  Runs restic->restic,
# rustic->rustic, restic->rustic, rustic->restic, a restore over existing
# immutable/append-only files (and LILO's map, or the ESP's elilo.efi and
# changed FAT attributes) with each tool, each tool's check --read-data of
# the other's repository, a backup pair under strictatime, one non-root run
# (uid 4321's own tree), and judges (judge.py, fsmeta.py, fatattr.py,
# rt-common.sh's node comparison).  Never a whole-disk run: the partition
# table, the swap's contents, LILO's boot sector and ELILO's NVRAM entry are
# HoltTest1's (a LIMIT here).  Slackware has python3 (d/python3), attr, acl,
# libcap, e2fsprogs, xfsprogs, dosfstools and util-linux's setpriv.
# slackware15/check.sh and slackware15-uefi/check.sh are the same file; both
# run slackware15/inner.sh, judge.py, fatattr.py and nracl.py, which read the
# guest's layout from the guest.
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json beside the
# logs (~/backotter-work/stictest1/GUEST/logs/PREFIX-*), exits 0 if nothing
# FAILs.  Takes a guest slot and a cmp-run.sh reservation itself.
set -u
here=$(cd "$(dirname "$0")" && pwd)
GUEST=$(basename "$here")
WORK=$HOME/backotter-work
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
# cleanup removes the scratch from a RAM disk other sessions share: it has to
# stay inside /mnt/bko-ram/PREFIX.
[[ $PREFIX =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "check.sh: PREFIX must be a plain name" >&2; exit 2; }
for b in restic-bko rustic-bko; do [[ -x $BIN/$b ]] || { echo "FAIL binaries: no $BIN/$b"; exit 1; }; done
if [[ -z ${BKST_INNER:-} ]]; then
    # shellcheck source=/dev/null
    . "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    # 4 GiB, an estimate (solus49's, whose check this follows): /boot (one
    # kernel; on -uefi also initrd.gz and the initrd tree) and on -uefi the
    # ESP's two copies of the kernel and initrd, in the source and 4
    # targets.  The run logs ram-used: measure it in S5.
    BKST_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 4 5 "$here/check.sh" "$@"
    rc=$?
    slot_give
    exit $rc
fi
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}
S=/mnt/bko-ram/$PREFIX/$GUEST LOGS=$STC_LOGS/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$S" "$LOGS"
export TMPDIR=$S
# shellcheck source=/dev/null
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
# vdb: the guest-like disk, the guest's vda's size (20 GiB); vdc: work.
qemu-img create -q -f qcow2 "$S/stack.qcow2" 20G && qemu-img create -q -f qcow2 "$S/work.qcow2" 80G
VM_BOOT_TIMEOUT=300 vm_boot "$GUEST" "$PORT" "$S/vm" --snapshot --memory 3072 --cpus 2 \
    --disk "$S/stack.qcow2" --disk "$S/work.qcow2" ||
    { echo "FAIL harness: $GUEST didn't boot"; exit 2; }
T=60 vm_ssh "mkdir -p /root/bk"
for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" \
         "${BKO_TESTVM:-$WORK/testvm}/compare/torture.sh" "$here/../slackware15/inner.sh" "$here/../slackware15/judge.py" \
         "$here/../slackware15/fatattr.py" "$here/../slackware15/nracl.py"; do
    vm_put "$f" "/root/bk/$(basename "$f")" || { echo "FAIL harness: copying $f"; exit 2; }
done
T=1800 vm_ssh "bash /root/bk/inner.sh $GUEST" >"$LOGS/check.out" 2>&1
echo "inner exit $?" >>"$LOGS/check.out"
# inner.sh's own tar, else (a timeout) what its logs dir holds so far, with the processes then
T=60 vm_ssh "ps -eo pid,ppid,etime,time,stat,rss,args --forest; free -m; df -m" >"$LOGS/guest-ps.txt" 2>&1
T=300 vm_ssh "if [ -f /root/bk/logs.tar ]; then cat /root/bk/logs.tar; else tar -C /mnt/bkw/logs -cf - .; fi" 2>"$LOGS/tar.err" | tar xf - -C "$LOGS" 2>>"$LOGS/tar.err"
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
