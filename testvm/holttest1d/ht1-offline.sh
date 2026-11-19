#!/usr/bin/env bash
# HoltTest1x for a guest that can't run bko-holt itself (README: "Systems
# read off their disks"): Redox (its sshd serves no logins), Sculpt OS
# (Genode, no shell at all) and Qubes OS (dom0 has no network, so no SSH:
# docs/design/qubes.md, Q7).  ht1.sh hands them here.  Run as root on a
# test host (Redox's on one with ~/backotter-work/tools/redox, Cinqcent;
# Qubes's on one with nested KVM, the UEFI rescuer and ~12 GiB free):
#
#   ht1-offline.sh GUEST RUN [PHASE...]    PHASEs: source backup rebuild verify
#
# The same round trip as ht1.sh, but:
#   - the dumps are bko-holt dump --disk, run in the Linux rescuer with the
#     guest's disk attached (the source's, and the restored one's);
#   - the backup and the file comparison mount the root filesystem here,
#     from a raw throwaway copy: RedoxFS through its FUSE driver (redoxfs),
#     Sculpt's GENODE ext2 read-only;
#   - a boot is checked as the guest allows: Redox by logging in as root on
#     its serial console (redox/console.py) and asking uname and /etc/hostname,
#     Sculpt by sculpt-check.sh (its screen, and its GENODE partition used).
#
# Qubes OS (qubes43) differs, being Linux in dom0 on LVM thin on LUKS:
#   - its dumps are bko-holt's own Linux dump, run chrooted into dom0's
#     root in the UEFI rescuer (rescuer-uefi.qcow2) after the rescuer opened
#     LUKS with the guest's console-passphrase and activated qubes_dom0, on a
#     throwaway overlay of the disk (QUBES_DUMP; the way bko-holt reads an
#     offline Linux disk is Q7's to check, docs/design/qubes.md);
#   - the backup mounts a throwaway overlay of the source here with vm-mount
#     --key, writes each raw volume the dump lists (storage.backup_sources'
#     fstype "raw": the qubes' vm-* LVs, sparse) into a scratch dir
#     bind-mounted at /.bko-holt/raw, and backs up the tree with them in one
#     snapshot; the raw volumes' SHA-256s are recorded there and again from
#     the restored disk (vm-mount --ro), for judge.py qubes;
#   - load gets --key-file for the LUKS volume and --no-nvram, and the
#     restored disk boots on a copy of the guest's uefi-vars.fd (Qubes's
#     NVRAM entry), with vm-run-options' --qemu-options (Xen nested);
#   - a boot is checked by bko-qubes-check.service's BKO-QUBES-CHECK lines
#     on the serial console (qubes/qubes-drive.py check types the LUKS
#     passphrase, waits for DONE, logs in and powers off), the restored
#     disk's held to the source's by judge.py qubes-check; and the dumps by
#     judge.py and judge.py qubes (snapshot LVs new, origins, activation
#     skip, pool used data within 5 %, raw volumes' checksums).
# The raw volumes, thin snapshots and boot.xen are bko-holt's Q2-Q4 (the
# field names here are those Q2-Q4 gave, not yet in a promoted bko-holt).
#
# The write path is ht1-lib.sh's, as ht1.sh's (README: "Where a run works,
# and what it keeps").  bko-holt is the promoted holt-bin's, HT_HOLT a file
# to test instead, HT_HOLT_BIN a set to take it from (ht1.sh's header).
# HT_VARIANT takes "bigger" and "oneshot" as ht1.sh does, and "refuse", which
# checks that a smaller target is still refused for a layout of another
# family, --fit-largest or no --fit-largest (README "The fitting variants"):
# source and rebuild only.
set -uo pipefail

G=$1 RUN=$2; shift 2
PHASES=("$@")   # the default is the variant's (below): all four, or source and rebuild
W=/home/m/backotter-work
HERE=$(dirname "$(realpath "$0")")
T=$(dirname "$HERE")   # this copy's testvm/ (vm-run, compare/, redox/, sculpt-check.sh), as ht1.sh's
[[ -r $HERE/ht1-lib.sh ]] || { echo "ht1-offline: no ht1-lib.sh beside $0" >&2; exit 2; }
# shellcheck source=ht1-lib.sh
. "$HERE/ht1-lib.sh"
# bko-holt (Linux's, run in the rescuer): HT_HOLT's, else HT_HOLT_BIN's set's,
# else the promoted holt-bin's, vouched for by its SHA256SUMS (ht_holt_pick).
ht_holt_pick linux
VARIANT=${HT_VARIANT:-}
case $VARIANT in ""|bigger|oneshot|refuse) ;; *) echo "HT_VARIANT: bigger, oneshot or refuse" >&2; exit 2 ;; esac
GV=$G${VARIANT:+-$VARIANT}
# "refuse" here is the fitting variants' check that a layout of another family
# is still refused on a smaller target: Redox's and Sculpt's tables are load's
# own write-table's, not the sfdisk script --fit-largest rewrites.  The target
# is 1 GiB smaller, plan must refuse naming the family, and nothing may be
# written, so the run has no backup, restore or verify (README "The fitting
# variants").
DELTA=0 FIT_PCT=10
[[ $VARIANT != bigger ]] || DELTA=$((1 << 30))
[[ $VARIANT != refuse ]] || DELTA=-$((1 << 30))
if [[ $VARIANT == refuse ]]; then
    HT_DONE_PHASE=rebuild   # ht1-lib's ht_end: the run is whole after it
    [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(source rebuild)
    for p in "${PHASES[@]}"; do
        [[ $p == source || $p == rebuild ]] ||
            { echo "HT_VARIANT=refuse: a refusal, so it has no $p phase (source and rebuild only)" >&2; exit 2; }
    done
else
    [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(source backup rebuild verify)
fi
PORT=${HT_PORT:-2690}
export SSH_PORT=$PORT
fails=0
say() { echo "$*" | tee -a "$R/report.txt"; }
ok() { say "PASS $*"; }
bad() { say "FAIL $*"; fails=$((fails + 1)); }
# harness WHY: a FAIL of the harness itself, not of bko-holt (a read of the
# rescuer that never came back, say).  The run carries on, so its first reason
# goes on the RESULT line, which classes the run HARNESS, not FAIL (rows_holt),
# only when every FAIL of the run was one: a real FAIL beside it stays a FAIL
# and is rerun.
hfails=0 HARNESS_WHY=""
harness() { bad "$*"; hfails=$((hfails + 1)); HARNESS_WHY=${HARNESS_WHY:-$*}; }
ht_paths   # R, where the run works, and K, where it ends up (ht1-lib.sh)
S=$R/share

case $G in
    redox) KIND=redox ;;
    sculpt*) KIND=sculpt ;;
    qubes*) KIND=qubes ;;
    *) echo "ht1-offline: $G isn't Redox, Sculpt or Qubes OS" >&2; exit 2 ;;
esac
REDOX_TOOLS=$W/tools/redox/bin
[[ $KIND != redox || ( -x $REDOX_TOOLS/redoxfs && -x $REDOX_TOOLS/redoxfs-mkfs ) ]] || { echo "ht1-offline: redox needs $REDOX_TOOLS (Cinqcent has it)" >&2; exit 2; }
gdir=$W/vms/$G
gimg=$gdir/$G.qcow2; [[ -f $gimg ]] || gimg=$(ls "$gdir"/*.qcow2 | head -n 1)
imgs=("$gimg")   # the guest's disks, for ht1-lib.sh's manifest
fw=(); [[ -f $gdir/vm-run-options ]] && read -ra fw < "$gdir/vm-run-options"
RESCUER_IMG=${HT_RESCUER_IMG:-/bkoscratch/holttest1d/rescuer/rescuer.qcow2}
rkey=$W/vms/debian13/id_ed25519
rfw=()   # the rescuer's firmware options (Qubes's: UEFI)
if [[ $KIND == qubes ]]; then
    # vm-run-options' --qemu-options FILE is beside the guest's disk, which a
    # restored disk (no backing file) isn't: named in full.
    for i in "${!fw[@]}"; do
        if [[ ${fw[$i]} == --qemu-options && ${fw[$((i + 1))]:-} != /* ]]; then fw[i + 1]=$gdir/${fw[$((i + 1))]}; fi
    done
    # A UEFI source is rebuilt from a UEFI-booted rescuer (ht1.sh's rule).
    if [[ -z ${HT_RESCUER_IMG:-} ]]; then
        RESCUER_IMG=/bkoscratch/holttest1d/rescuer/rescuer-uefi.qcow2 rkey=$W/vms/ubuntu26-uefi/id_ed25519
    fi
    rfw=(--uefi)
    gpass=$gdir/console-passphrase
    for f in "$gpass" "$gdir/root-password" "$gdir/uefi-vars.fd" "$T/qubes/qubes-drive.py"; do
        [[ -f $f ]] || { echo "ht1-offline: $G needs $f" >&2; exit 2; }
    done
    nested=$(cat /sys/module/kvm_amd/parameters/nested /sys/module/kvm_intel/parameters/nested 2>/dev/null)
    [[ $nested == *[1Y]* ]] || { echo "ht1-offline: $G runs Xen nested: this host has no kvm_amd/kvm_intel nested=1" >&2; exit 2; }
fi
QUBES_CHECK_TMO=${HT_QUBES_CHECK_TMO:-5400}   # one check boot, from QEMU's start (make-qubes.sh's CHECK_TIMEOUT)
SRC=$R/src-0.qcow2 DST=$R/dst-0.qcow2
SRC_MP=/mnt/ht1-src-$GV DST_MP=/mnt/ht1-dst-$GV
size_of() { qemu-img info --output=json "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["virtual-size"])'; }
# Where the run works and what a pass keeps (ht1-lib.sh; README "Where a run
# works, and what it keeps").
HT_BULK=(share src-0.qcow2 dst-0.qcow2 decoy.qcow2 'rescuer*.qcow2' 'boot-*.qcow2' 'console-*.sock' 'console-*.mon' '*-vars.fd' 'tmp-*' 'check-*/sculpt-check.qcow2' raw rustic-bko tmp)
HT_ZIP=(src-meta.json dst-meta.json)
ht_require
case ${PHASES[0]} in   # what the first phase remakes is not copied back from K
    backup)  ht_seed share/repo/ dst-0.qcow2 decoy.qcow2 'rescuer*.qcow2' 'tmp-*' tmp/ ;;
    rebuild) ht_seed src-0.qcow2 dst-0.qcow2 decoy.qcow2 'rescuer*.qcow2' 'tmp-*' tmp/ ;;
    verify)  ht_seed src-0.qcow2 share/repo/ decoy.qcow2 'rescuer*.qcow2' 'tmp-*' tmp/ ;;
esac
ht_begin; mkdir -p "$S"
ht_rustic_take linux
HT_MOUNTED="" QMOUNTED=""
ht_release() {   # before anything is moved: no mount, loop device or console VM may hold R
    local raw l
    [[ -n $HT_MOUNTED ]] && host_umount "$HT_MOUNTED" 2>/dev/null
    [[ -n $QMOUNTED ]] && qubes_umount "$QMOUNTED" 2>/dev/null
    # The raw copies are throwaway copies of src-0 or dst-0: gone on a failure too.
    for raw in "$R"/tmp-*.raw; do [[ -f $raw ]] || continue
        for l in $(losetup -j "$raw" | cut -d: -f1); do losetup -d "$l"; done; rm -f "$raw"; done
    rm -f "$R"/tmp-*.loop
    # shellcheck disable=SC2046  # PIDs
    kill $(console_qemus "$R/console-") 2>/dev/null
}
# This run's console QEMU (boot_check's) on a socket whose path starts SOCK
# (ht_qemus: one this run started), never another's with such a path.
console_qemus() {  # SOCK
    local q
    for q in $(ht_qemus "$R"); do tr '\0' ' ' 2>/dev/null <"/proc/$q/cmdline" | grep -qF "path=$1" && echo "$q"; done
    return 0
}
# ConnectTimeout is SSH_CTO (ht1-lib.sh, 30 s); the boot poll keeps its own
# TMO=8, so a rescuer not up yet costs no more.
sshk() { timeout "${TMO:-600}" ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=$SSH_CTO -o ServerAliveInterval=15 root@127.0.0.1 "$@"; }
# edges_read CMD FILE WANT: the disks' first and last MiB (CMD, which only
# reads) into FILE, WANT lines, one a disk.  sshk waits SSH_CTO seconds for the
# sshd to answer, and on a loaded host a slow one answers nothing at all, so an
# empty or short capture is not a disk that changed: try again a few times.
# (WANT or more lines: a platform that prints a warning of its own still counts).
# 1: it still couldn't be read, which is the harness's problem, not a write.
edges_read() {
    local cmd=$1 file=$2 want=$3 i
    for i in 1 2 3; do
        [[ $i == 1 ]] || sleep 3
        sshk "$cmd" > "$file" 2>&1 && [[ $(grep -c . "$file") -ge "$want" ]] && return 0
    done
    return 1
}
VDB_WHY=""
# vdb_blank: whether the dry run left /dev/vdb unwritten, by wipefs -n.
# 0 no signature (nothing written), 1 a signature (something was), 2 nothing
# to go by: no wipefs on the rescuer, wipefs itself failed, or the sshd didn't
# answer three tries (VDB_WHY says which).  An unanswered call is not a PASS.
# (wipefs's stdout only, where it lists a signature: a warning on stderr, the
# rescuer's shell's own, say, goes to logs/vdb-blank.err and isn't a write.)
vdb_blank() {
    local i out rc
    for i in 1 2 3; do
        [[ $i == 1 ]] || sleep 3
        out=$(sshk 'command -v wipefs >/dev/null || { echo "no wipefs on the rescuer"; exit 9; }
            wipefs -n /dev/vdb || { echo "wipefs -n failed"; exit 9; }' 2>"$R/logs/vdb-blank.err"); rc=$?
        if ((rc == 0)); then [[ -z ${out//[[:space:]]/} ]]; return $?; fi
        if ((rc == 9)); then VDB_WHY="$(tr '\n' ' ' <<<"$out")$(tail -n 1 "$R/logs/vdb-blank.err")"; return 2; fi
        VDB_WHY="the rescuer didn't answer (exit $rc)"
    done
    return 2
}
r_up() { # IMAGE LOG [vm-run options...]: the rescuer, the share on 9p
    local image=$1 log=$2; shift 2
    setsid -f "$T/vm-run" --serial-log "$log" --memory 3072 "$@" "$image" -- \
        -virtfs "local,path=$S,mount_tag=bkoshare,security_model=none,readonly=on" </dev/null >"$log.vm-run" 2>&1
    for _ in $(seq 72); do TMO=8 sshk true 2>/dev/null && break; sleep 5; done
    TMO=8 sshk true 2>/dev/null || return 1
    sshk 'mkdir -p /mnt/share && mount -t 9p -o trans=virtio,version=9p2000.L,ro bkoshare /mnt/share'
}
# This run's rescuer only (ht_qemus: a QEMU with its disks in R that this
# run started, ht_own), never another run's that took the port while none
# of this run's was up, nor one started by hand on R's (K's) disks.
r_down() {
    [[ -n $(ht_qemus "$R") ]] || return 0
    TMO=20 sshk poweroff >/dev/null 2>&1
    for _ in $(seq 36); do [[ -n $(ht_qemus "$R") ]] || return 0; sleep 5; done
    # shellcheck disable=SC2046  # PIDs
    kill $(ht_qemus "$R") 2>/dev/null; sleep 5
    # shellcheck disable=SC2046
    kill -9 $(ht_qemus "$R") 2>/dev/null; sleep 1
}
share_tools() {
    cp "$HOLT" "$S/bko-holt"
    [[ $KIND != redox ]] || cp "$REDOX_TOOLS/redoxfs" "$REDOX_TOOLS/redoxfs-mkfs" "$S/"
    # As typed: without the file's newline (cryptsetup keeps it in a key).
    [[ $KIND != qubes ]] || printf '%s' "$(cat "$gpass")" > "$S/passphrase"
    chmod -R a+rX "$S"
}

# Qubes: dom0's own dump, in the rescuer (IMAGE a throwaway overlay of the
# disk, as vdb): LUKS opened with the passphrase under crypttab's name
# (luks-UUID), qubes_dom0 activated, dom0's root and its /boot and /boot/efi
# mounted read-only, /dev, /proc, /sys and /run bound in, the share at /mnt,
# then bko-holt dump chrooted there (bko-holt's dump of a Linux system
# that isn't running: Q7's to check, docs/design/qubes.md).
QUBES_DUMP='L=""; for p in $(lsblk -lnpo NAME /dev/vdb); do [ "$(blkid -s TYPE -o value "$p" 2>/dev/null)" = crypto_LUKS ] && L=$p; done
[ -n "$L" ] || { echo "no LUKS partition on /dev/vdb" >&2; exit 1; }
N=luks-$(cryptsetup luksUUID "$L") M=/mnt/bko-qubes
cryptsetup open --key-file /mnt/share/passphrase "$L" "$N" || exit 1
vgchange -ay qubes_dom0 >/dev/null && udevadm settle || exit 1
mkdir -p $M && mount -o ro /dev/mapper/qubes_dom0-root $M || exit 1
while read -r spec file _; do case $file in /boot|/boot/efi) mount -o ro "$(findfs "$spec")" "$M$file" || exit 1 ;; esac; done < $M/etc/fstab
for x in dev proc sys run; do mount --rbind /$x $M/$x || exit 1; done
mount --bind /mnt/share $M/mnt || exit 1
chroot $M /mnt/bko-holt dump; s=$?
umount -R $M >/dev/null 2>&1; vgchange -an qubes_dom0 >/dev/null 2>&1; cryptsetup close "$N" >/dev/null 2>&1
exit $s'

# bko-holt dump --disk of IMAGE, attached to a throwaway rescuer as vdb
# (Qubes: QUBES_DUMP, on a throwaway overlay of IMAGE).
offline_dump() { # IMAGE OUT NAME
    local o=$R/rescuer-$3.qcow2
    if [[ $KIND == qubes ]]; then
        local ov=$R/tmp-dump-$3.qcow2 s
        rm -f "$o" "$ov"; qemu-img create -q -f qcow2 -b "$RESCUER_IMG" -F qcow2 "$o"
        qemu-img create -q -f qcow2 -b "$1" -F qcow2 "$ov"
        share_tools
        r_up "$o" "$R/logs/dump-$3-serial.log" "${rfw[@]}" --disk "$ov" || { r_down; rm -f "$o" "$ov"; return 1; }
        sshk "$QUBES_DUMP" > "$2" 2> "$R/logs/dump-$3.err"
        s=$?
        r_down; rm -f "$o" "$ov"
        return $s
    fi
    rm -f "$o"; qemu-img create -q -f qcow2 -b "$RESCUER_IMG" -F qcow2 "$o"
    share_tools
    r_up "$o" "$R/logs/dump-$3-serial.log" --disk "$1" || { r_down; rm -f "$o"; return 1; }
    sshk '/mnt/share/bko-holt dump --disk /dev/vdb' > "$2" 2> "$R/logs/dump-$3.err"
    local s=$?
    r_down; rm -f "$o"
    return $s
}

# The root filesystem of IMAGE (the layout says where), mounted at MP from a
# raw copy: RedoxFS's driver opens what it mounts read-write.
host_mount() { # IMAGE LAYOUT MP
    local raw=$R/tmp-${3##*/}.raw
    qemu-img convert -O raw "$1" "$raw" || return 1
    read -r off len < <(python3 - "$2" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
root = next(e["resolves_to"] for e in d["storage"]["fstab"] if e["file"] == "/")
for x in d["storage"]["disks"]:
    for p in x["partitions"]:
        if p["name"] == root:
            print(p["start_bytes"], p["size_bytes"])
PY
)
    local loop; loop=$(losetup -f --show -o "$off" --sizelimit "$len" "$raw") || return 1
    echo "$loop $raw" > "$R/tmp-${3##*/}.loop"
    # Set before the mount: a signal during redoxfs (it returns once the mount
    # is up) or mount still leaves ht_release a host_umount to do.
    HT_MOUNTED=$3
    mkdir -p "$3"
    if [[ $KIND == redox ]]; then
        "$REDOX_TOOLS/redoxfs" "$loop" "$3" > "$R/logs/redoxfs-${3##*/}.log" 2>&1
        for _ in $(seq 30); do mountpoint -q "$3" && return 0; sleep 1; done
        return 1
    fi
    mount -o ro "$loop" "$3"
}
host_umount() { # MP
    local loop="" raw=""
    umount "$1" 2>/dev/null || fusermount3 -u "$1" 2>/dev/null
    # Cleared before the loop device goes: ht_release finds what is left by
    # the raw copy (losetup -j), never by a loop name that may be another's now.
    HT_MOUNTED=
    sleep 1
    read -r loop raw < "$R/tmp-${1##*/}.loop" || return 1
    losetup -d "$loop"; rm -f "$raw" "$R/tmp-${1##*/}.loop"
}

# Qubes: IMAGE's tree mounted at MP by vm-mount (LUKS opened with the
# passphrase, qubes_dom0 activated by LVM under its own names: so one Qubes
# disk at a time on this host), read-only unless RW is "rw".  Its raw
# volumes' bind mount (/.bko-holt/raw) goes with it.
qubes_mount() { # IMAGE MP [rw]
    local ro=(--ro); [[ ${3:-} == rw ]] && ro=()
    mkdir -p "$2"
    QMOUNTED=$2
    (trap '' TERM INT HUP; exec "$T/vm-mount" "${ro[@]}" --key "$gpass" "$1" "$2")
}
qubes_umount() { # MP
    if mountpoint -q "$1/.bko-holt/raw" 2>/dev/null; then umount "$1/.bko-holt/raw"; fi
    (trap '' TERM INT HUP; exec "$T/vm-umount" "$1"); local s=$?
    QMOUNTED=
    return $s
}
# The raw volumes of the dump LAYOUT (storage.backup_sources' fstype "raw"),
# a line each: the LV (VG/LV) and its path in the backup (/.bko-holt/raw/...).
qubes_raws() { # LAYOUT
    python3 - "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for b in (d.get("storage") or {}).get("backup_sources") or []:
    if b.get("fstype") == "raw" and str(b.get("source") or "").startswith("/dev/") and b.get("path"):
        print(b["source"][len("/dev/"):], b["path"])
PY
}
# Each raw volume of LAYOUT on the disk vm-mount set up at MP: activated
# (-K: an activation-skip revision) on the PVs vm-mount activated the group
# from, its SHA-256 a line of OUT ("VG/LV SHA256"); with DIR, also copied
# sparse to DIR/<its path under /.bko-holt/raw>.
qubes_raw_pass() { # LAYOUT MP OUT [DIR]
    local st pvs lv path dev
    st=/run/vm-mount/$(systemd-escape --path "$2")
    pvs=$(awk '$1 == "vg" && $2 == "qubes_dom0" { print $3 }' "$st" 2>/dev/null | tail -n 1)
    [[ -n $pvs ]] || { echo "no qubes_dom0 activated by vm-mount at $2 ($st)" >&2; return 1; }
    : > "$3"
    while read -r lv path; do
        lvchange --devicesfile "" --devices "$pvs" -ay -K "$lv" >/dev/null 2>&1
        dev=/dev/$lv
        [[ -b $dev ]] || { echo "$lv: not active ($dev)" >&2; return 1; }
        if [[ -n ${4:-} ]]; then
            mkdir -p "$(dirname "$4/${path#/.bko-holt/raw/}")"
            cp --sparse=always "$dev" "$4/${path#/.bko-holt/raw/}" || return 1
        fi
        echo "$lv $(sha256sum < "$dev" | cut -c1-64)" >> "$3"
    done < <(qubes_raws "$1")
    [[ -s $3 ]] || { echo "the dump lists no raw volumes (storage.backup_sources fstype raw: bko-holt's Q3)" >&2; return 1; }
}

# Whether IMAGE boots: Redox's root login on the serial console, uname and
# hostname; Sculpt's screen and its partition used (sculpt-check.sh).
boot_check() { # IMAGE NAME
    if [[ $KIND == qubes ]]; then
        # bko-qubes-check.service's lines: qubes-drive.py types the
        # passphrase, waits for DONE, logs in and powers off.  On a copy of
        # the guest's NVRAM (Qubes's entry), the writes thrown away.
        local d=$R/check-$2 sock=$R/console-$2.sock mon=$R/console-$2.mon log=$R/logs/$2-serial.log ov=$R/boot-$2.qcow2 vars=$R/$2-vars.fd s=1
        rm -rf "${R:?}/check-$2"; mkdir -p "$d"; rm -f "$sock" "$mon" "$ov"
        qemu-img create -q -f qcow2 -b "$1" -F qcow2 "$ov"
        cp "$gdir/uefi-vars.fd" "$vars"
        setsid -f "$T/vm-run" "${fw[@]}" --uefi-vars "$vars" "$ov" -- \
            -chardev "socket,id=console,path=$sock,server=on,wait=off,logfile=$log" -serial chardev:console \
            -monitor "unix:$mon,server=on,wait=off" -no-reboot </dev/null >"$R/logs/$2.vm-run" 2>&1
        timeout "$QUBES_CHECK_TMO" python3 -I "$T/qubes/qubes-drive.py" check "$sock" "$mon" "$d" "$gpass" "$gdir/root-password" \
            > "$R/logs/$2-drive.log" 2>&1 && s=0
        for _ in $(seq 60); do [[ -n $(console_qemus "$sock,") ]] || break; sleep 5; done
        # shellcheck disable=SC2046  # PIDs
        kill $(console_qemus "$sock,") 2>/dev/null; sleep 2
        tr -d '\r' < "$log" | grep -aoE 'BKO-QUBES-(CHECK|INFO) .*' > "$R/logs/$2-check.log"
        rm -f "$ov" "$sock" "$mon" "$vars"
        return $s
    fi
    if [[ $KIND == sculpt ]]; then
        local d=$R/check-$2
        rm -rf "$d"; mkdir -p "$d"
        ln -s "$1" "$d/sculpt-check.qcow2"; cp "$gdir/vm-run-options" "$d/"
        "$T/sculpt-check.sh" "$d" > "$R/logs/$2-check.log" 2>&1
        return
    fi
    local sock=$R/console-$2.sock log=$R/logs/$2-serial.log ov=$R/boot-$2.qcow2
    rm -f "$sock" "$ov"; qemu-img create -q -f qcow2 -b "$1" -F qcow2 "$ov"
    setsid -f "$T/vm-run" "${fw[@]}" "$ov" -- -chardev "socket,id=console,path=$sock,server=on,wait=off,logfile=$log" \
        -serial chardev:console -monitor none -no-reboot </dev/null >"$R/logs/$2.vm-run" 2>&1
    local c=(python3 "$T/redox/console.py" "$sock" "$gdir/root-password")
    local s=1
    if "${c[@]}" login 600 > "$R/logs/$2-login.log" 2>&1; then
        "${c[@]}" run 'uname -s; cat /etc/hostname' > "$R/logs/$2-check.log" 2>&1 && grep -qx Redox "$R/logs/$2-check.log" && s=0
        "${c[@]}" send 'poweroff' >/dev/null 2>&1
    fi
    for _ in $(seq 24); do [[ -n $(console_qemus "$sock,") ]] || break; sleep 5; done
    # shellcheck disable=SC2046  # PIDs
    kill $(console_qemus "$sock,") 2>/dev/null; sleep 2
    rm -f "$ov" "$sock"
    return $s
}

phase_source() {
    say "== source: $G (read off its disks)"
    rm -f "$SRC"; qemu-img create -q -f qcow2 -b "$gimg" -F qcow2 "$SRC"
    local how="sculpt-check: screen and partition"
    [[ $KIND == redox ]] && how="root's login on its serial console"
    if boot_check "$SRC" source; then
        [[ $KIND == qubes ]] && how="bko-qubes-check.service: $(grep -c '^BKO-QUBES-CHECK .* PASS' "$R/logs/source-check.log") items PASS, $(grep '^BKO-QUBES-CHECK DONE' "$R/logs/source-check.log")"
        ok "source boots ($how)"
    else bad "source boots (logs/source-*)"; return; fi
    if offline_dump "$SRC" "$R/dumps/src.json" src; then ok "dump of the source's disks (bko-holt dump --disk, in the rescuer)"; else bad "dump of the source's disks: $(tail -1 "$R/logs/dump-src.err")"; return; fi
    cp "$R/dumps/src.json" "$S/layout.json"
}

phase_backup() {
    [[ $KIND != qubes ]] || { qubes_backup; return; }
    say "== backup"
    rm -rf "$S/repo"; head -c 24 /dev/urandom | base64 > "$S/repo-password"
    host_mount "$SRC" "$R/dumps/src.json" "$SRC_MP" > "$R/logs/backup-mount.log" 2>&1 || { bad "mount the source's root here (logs/backup-mount.log)"; return; }
    local rustic=("$RUSTIC" -r "$S/repo" --password-file "$S/repo-password" --no-progress --no-cache)
    if "${rustic[@]}" init > "$R/logs/backup.log" 2>&1 && "${rustic[@]}" backup --set-atime yes "$SRC_MP" >> "$R/logs/backup.log" 2>&1; then
        ok "rustic-bko backup ($(du -sh "$S/repo" | cut -f1))"
    else
        bad "rustic-bko backup (logs/backup.log)"
    fi
    python3 "$T/compare/fsmeta.py" record "$SRC_MP" "$R/src-meta.json" > "$R/logs/fsmeta-src.log" 2>&1 || bad "fsmeta record of the source"
    host_umount "$SRC_MP" >> "$R/logs/backup-mount.log" 2>&1
    cp "$RUSTIC" "$S/rustic-bko"
}

# Qubes: the tree of a throwaway overlay of the source, mounted read-write
# (its /.bko-holt/raw made for the raw volumes, as a backup of the running
# machine would), fsmeta's record of it, the raw volumes copied sparse into
# R/raw bind-mounted there, and one rustic-bko snapshot of it all.
qubes_backup() {
    say "== backup"
    rm -rf "${S:?}/repo" "${R:?}/raw"; head -c 24 /dev/urandom | base64 > "$S/repo-password"
    local bk=$R/tmp-backup.qcow2
    rm -f "$bk"; qemu-img create -q -f qcow2 -b "$SRC" -F qcow2 "$bk"
    qubes_mount "$bk" "$SRC_MP" rw > "$R/logs/backup-mount.log" 2>&1 || { bad "mount the source here (vm-mount --key: logs/backup-mount.log)"; return; }
    mkdir -p "$SRC_MP/.bko-holt/raw" "$R/raw"
    python3 "$T/compare/fsmeta.py" record "$SRC_MP" "$R/src-meta.json" > "$R/logs/fsmeta-src.log" 2>&1 || bad "fsmeta record of the source"
    mount --bind "$R/raw" "$SRC_MP/.bko-holt/raw"
    if qubes_raw_pass "$R/dumps/src.json" "$SRC_MP" "$R/dumps/src-raw.txt" "$R/raw" > "$R/logs/backup-raw.log" 2>&1; then
        ok "raw volumes copied: $(wc -l < "$R/dumps/src-raw.txt") ($(du -sh "$R/raw" | cut -f1) sparse)"
    else
        bad "raw volumes: $(tail -1 "$R/logs/backup-raw.log")"
    fi
    local rustic=("$RUSTIC" -r "$S/repo" --password-file "$S/repo-password" --no-progress --no-cache)
    if "${rustic[@]}" init > "$R/logs/backup.log" 2>&1 && "${rustic[@]}" backup --set-atime yes "$SRC_MP" >> "$R/logs/backup.log" 2>&1; then
        ok "rustic-bko backup ($(du -sh "$S/repo" | cut -f1))"
    else
        bad "rustic-bko backup (logs/backup.log)"
    fi
    qubes_umount "$SRC_MP" >> "$R/logs/backup-mount.log" 2>&1
    rm -rf "${R:?}/raw"; rm -f "$bk"
    cp "$RUSTIC" "$S/rustic-bko"
    chmod -R a+rX "$S"
}

load_step() { local want=$1 name=$2; shift 2
    ht_load_try "$R/logs/rebuild-$name.out" "$R/logs/rebuild-$name.err" sshk "echo $HT_STARTED; /mnt/share/bko-holt load /mnt/share/layout.json $*"
    local got=$?
    if [[ $got == "$want" ]]; then ok "load $name (exit $got)"; return 0; fi
    bad "load $name: exit $got, want $want: $(grep -v '^$' "$R/logs/rebuild-$name.err" | tail -3 | tr '\n' ' ')"
    return 1
}

phase_rebuild() {
    say "== rebuild"
    share_tools
    cp "$RUSTIC" "$S/rustic-bko"
    rm -f "$R/rescuer.qcow2" "$DST" "$R/decoy.qcow2"
    qemu-img create -q -f qcow2 -b "$RESCUER_IMG" -F qcow2 "$R/rescuer.qcow2"
    qemu-img create -q -f qcow2 "$DST" "$(($(size_of "$gimg") + DELTA))"
    qemu-img create -q -f qcow2 "$R/decoy.qcow2" "$(size_of "$gimg")"
    r_up "$R/rescuer.qcow2" "$R/logs/rescuer-serial.log" "${rfw[@]}" --disk "$DST" --disk "$R/decoy.qcow2" || { bad "rescuer boots"; r_down; return; }
    sshk 'for t in redoxfs redoxfs-mkfs; do [ ! -f /mnt/share/$t ] || install -m 755 /mnt/share/$t /usr/local/sbin/$t; done; mkfs.ext4 -q -F -L ht1decoy /dev/vdc' > "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup"; r_down; return; }
    ok "rescuer up, share mounted"
    local st=$((($(size_of "$gimg") + DELTA) / 512)) sd=$(($(size_of "$gimg") / 512))
    local edges="for x in /dev/vdb:$st /dev/vdc:$sd; do r=\${x%:*} s=\${x##*:}; echo \$r \$(dd if=\$r bs=512 count=2048 status=none | md5sum | cut -c1-32) \$(dd if=\$r bs=512 skip=\$((s - 2048)) count=2048 status=none | md5sum | cut -c1-32); done"
    # A line for the target and one for the decoy; before is 0 while that
    # capture couldn't be read.
    local nedges=2 before=0
    if edges_read "$edges" "$R/logs/edges-before.txt" "$nedges"; then
        before=1
    else
        harness "the target's and the decoy's first and last MiB couldn't be read before the refusals (logs/edges-before.txt): what they write isn't checked"
    fi
    load_step 4 refuse-nomap
    load_step 4 refuse-root --map vdb=vda --erase vda
    load_step 4 refuse-decoy --map vdb=vdc && { cat "$R/logs/rebuild-refuse-decoy".{out,err} | grep -q "isn't blank" || bad "load refuse-decoy: refused, but not for the decoy's filesystem"; }
    if [[ $VARIANT == refuse ]]; then
        # A smaller target is refused without the flag, and with it too: this
        # family's table isn't the sfdisk script --fit-largest rewrites.  The
        # target image must be byte for byte what it was after both.
        local h1 h2 fam
        fam=$(python3 -c 'import json, sys; print((json.load(open(sys.argv[1])).get("os") or {}).get("family") or "")' "$R/dumps/src.json")
        if [[ -z $fam ]]; then
            bad "HT_VARIANT=refuse: the dump of $G names no OS family, so there is no refusal of one to check for"
            r_down; return
        fi
        h1=$(md5sum "$DST" | cut -d' ' -f1)
        load_step 4 refuse-smaller --map vdb=vdb --erase /dev/vdb --only plan &&
            { grep -qi "is smaller" "$R/logs/rebuild-refuse-smaller".{out,err} ||
                bad "load refuse-smaller: refused, but not for the target's size"; }
        load_step 4 refuse-fit --map vdb=vdb --erase /dev/vdb --fit-largest "$FIT_PCT%" --only plan &&
            { grep -qiF -- "$fam" "$R/logs/rebuild-refuse-fit".{out,err} ||
                bad "load refuse-fit: refused, but not for $fam's own partition table: $(grep -v '^$' "$R/logs/rebuild-refuse-fit".{out,err} | tail -3 | tr '\n' ' ')"; }
        h2=$(md5sum "$DST" | cut -d' ' -f1)
        if [[ $h1 == "$h2" ]]; then
            ok "the plans left the target image's bytes as they were"
        else
            bad "a plan wrote to the target image ($h1 -> $h2)"
        fi
        if ((before)); then
            if ! edges_read "$edges" "$R/logs/edges-after.txt" "$nedges"; then
                harness "the target's and the decoy's first and last MiB couldn't be read after the refusals (logs/edges-after.txt)"
            elif cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
                ok "the refusals left the target's and the decoy's first and last MiB as they were"
            else
                bad "a refusal wrote to a disk (logs/edges-before.txt, edges-after.txt)"
            fi
        fi
        [[ $(sshk 'blkid -o value -s LABEL /dev/vdc') == ht1decoy ]] || bad "the decoy lost its filesystem"
        r_down
        rm -f "$R/rescuer.qcow2"
        return
    fi
    load_step 0 dry-partition --map vdb=vdb --only partition
    vdb_blank
    case $? in
        0) ok "nothing written without --commit" ;;
        1) bad "vdb was written without --commit" ;;
        *) harness "whether vdb was written without --commit couldn't be checked: $VDB_WHY" ;;
    esac
    if ((before)); then
        if ! edges_read "$edges" "$R/logs/edges-after.txt" "$nedges"; then
            harness "the target's and the decoy's first and last MiB couldn't be read after the refusals and the dry run (logs/edges-after.txt)"
        elif cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
            ok "the refusals and the dry run left every target's and the decoy's first and last MiB as they were"
        else
            bad "a refusal or the dry run wrote to a disk (logs/edges-before.txt, edges-after.txt)"
        fi
    fi
    local map=(--map vdb=vdb --erase /dev/vdb)
    # Qubes: the LUKS volume gets the source's passphrase (crypttab's names,
    # and any other crypt device's), and the boot stage --no-nvram: the
    # restored disk boots through the guest's own NVRAM entry (boot_check).
    if [[ $KIND == qubes ]]; then
        map+=(--no-nvram)
        while read -r n; do map+=(--key-file "$n=/mnt/share/passphrase"); done < <(python3 - "$S/layout.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
names = {e["name"] for e in (d["storage"].get("crypttab") or [])}
names |= {(x.get("dm") or {}).get("dm_name") for x in d["storage"].get("dm_devices") or [] if (x.get("dm") or {}).get("target") == "crypt"}
print("\n".join(sorted(n for n in names if n)))
PY
)
    fi
    local repo=(--repo /mnt/share/repo --password-file /mnt/share/repo-password --archiver /mnt/share/rustic-bko --snapshot "latest:$SRC_MP")
    local stages=(partition assemble format mount restore boot close)
    [[ $VARIANT == oneshot ]] && stages=(oneshot)
    for s in "${stages[@]}"; do
        local how=(--only "$s") extra=()
        [[ $s == restore || $s == oneshot ]] && extra=("${repo[@]}")
        [[ $s == oneshot ]] && how=(--through close)
        TMO=3600 load_step 0 "$s" "${map[@]}" "${extra[@]}" "${how[@]}" --commit || {
            sshk 'cat /run/bko-holt/log/load.log' > "$R/logs/rebuild-load.log"
            sshk 'set -x; findmnt -R /mnt/bko-target; lsblk -f; sfdisk -d /dev/vdb' > "$R/logs/rebuild-failed-state.txt" 2>&1
            r_down; return; }
        ht_status_read "$R/logs/rebuild-status-after-$s.txt" sshk "/mnt/share/bko-holt load /mnt/share/layout.json ${map[*]} --only status" ||
            { harness "the load status after $s couldn't be read: ssh got no answer (logs/rebuild-status-after-$s.txt)"; continue; }
        if [[ $s == oneshot ]]; then
            grep -q "^next: nothing" "$R/logs/rebuild-status-after-$s.txt" || bad "status after the one-command rebuild: $(grep '^next' "$R/logs/rebuild-status-after-$s.txt")"
        else
            grep -qE "^$s +(done|nothing to do)" "$R/logs/rebuild-status-after-$s.txt" || [[ $s == assemble ]] || bad "status after $s: $(grep "^$s " "$R/logs/rebuild-status-after-$s.txt")"
        fi
    done
    [[ $(sshk 'blkid -o value -s LABEL /dev/vdc') == ht1decoy ]] || bad "the decoy lost its filesystem"
    sshk 'cat /run/bko-holt/log/load.log' > "$R/logs/rebuild-load.log"
    r_down
    rm -f "$R/rescuer.qcow2"
}

phase_verify() {
    [[ $KIND != qubes ]] || { qubes_verify; return; }
    say "== verify"
    if host_mount "$DST" "$R/dumps/src.json" "$DST_MP" > "$R/logs/verify-mount.log" 2>&1; then
        python3 "$T/compare/fsmeta.py" record "$DST_MP" "$R/dst-meta.json" > "$R/logs/fsmeta-dst.log" 2>&1
        host_umount "$DST_MP" >> "$R/logs/verify-mount.log" 2>&1
        python3 "$T/compare/fsmeta.py" compare "$R/src-meta.json" "$R/dst-meta.json" > "$R/logs/fsmeta-compare.txt" 2>&1
        local na=(); [[ $KIND == redox ]] && na=(--no-atime)
        if python3 "$HERE/judge.py" fs "$T/compare/fsmeta.py" "$R/src-meta.json" "$R/dst-meta.json" "${na[@]}" > "$R/logs/fsjudge.txt" 2>&1; then
            ok "fsmeta: the files agree but for the boot stage's ($(tail -1 "$R/logs/fsjudge.txt"))"
        else
            bad "fsmeta: $(grep '^DIFF' "$R/logs/fsjudge.txt" | head -5 | tr '\n' ' ')"
        fi
    else
        bad "mount the restored root here (logs/verify-mount.log)"
    fi
    if boot_check "$DST" restored; then ok "the restored disk boots by itself ($(tr '\n' ' ' < "$R/logs/restored-check.log" | cut -c1-120))"; else bad "the restored disk boots by itself (logs/restored-*)"; fi
    if offline_dump "$DST" "$R/dumps/dst.json" dst; then
        local grow=(); [[ $VARIANT == bigger ]] && grow=(--grow $((1 << 30)))
        if python3 "$HERE/judge.py" "${grow[@]}" "$R/dumps/src.json" "$R/dumps/dst.json" > "$R/logs/judge.txt" 2>&1; then
            ok "the dumps agree ($(tail -1 "$R/logs/judge.txt"))"
        else
            bad "the dumps differ: $(grep -c '^DIFF' "$R/logs/judge.txt") fields (logs/judge.txt)"
        fi
    else
        bad "dump of the restored disks: $(tail -1 "$R/logs/dump-dst.err")"
    fi
}

# Qubes: fsmeta and the raw volumes' checksums from the restored disk
# (vm-mount --ro), its boot by the check unit, held to the source's
# (judge.py qubes-check), and its dump, judged by judge.py and judge.py qubes.
qubes_verify() {
    say "== verify"
    if qubes_mount "$DST" "$DST_MP" > "$R/logs/verify-mount.log" 2>&1; then
        python3 "$T/compare/fsmeta.py" record "$DST_MP" "$R/dst-meta.json" > "$R/logs/fsmeta-dst.log" 2>&1
        qubes_raw_pass "$R/dumps/src.json" "$DST_MP" "$R/dumps/dst-raw.txt" > "$R/logs/verify-raw.log" 2>&1 ||
            bad "raw volumes of the restored disk: $(tail -1 "$R/logs/verify-raw.log")"
        qubes_umount "$DST_MP" >> "$R/logs/verify-mount.log" 2>&1
        if python3 "$HERE/judge.py" fs "$T/compare/fsmeta.py" "$R/src-meta.json" "$R/dst-meta.json" > "$R/logs/fsjudge.txt" 2>&1; then
            ok "fsmeta: the files agree but for the boot stage's ($(tail -1 "$R/logs/fsjudge.txt"))"
        else
            bad "fsmeta: $(grep '^DIFF' "$R/logs/fsjudge.txt" | head -5 | tr '\n' ' ')"
        fi
    else
        bad "mount the restored disk here (logs/verify-mount.log)"
    fi
    if boot_check "$DST" restored; then
        if python3 "$HERE/judge.py" qubes-check "$R/logs/source-check.log" "$R/logs/restored-check.log" > "$R/logs/qubes-check.txt" 2>&1; then
            ok "the restored disk boots by itself, its qubes as the source's ($(tail -1 "$R/logs/qubes-check.txt"))"
        else
            bad "the restored disk's check: $(grep '^DIFF' "$R/logs/qubes-check.txt" | head -3 | tr '\n' ' ')"
        fi
    else
        bad "the restored disk boots by itself (no BKO-QUBES-CHECK DONE PASS: logs/restored-*)"
    fi
    if offline_dump "$DST" "$R/dumps/dst.json" dst; then
        local grow=(); [[ $VARIANT == bigger ]] && grow=(--grow $((1 << 30)))
        if python3 "$HERE/judge.py" "${grow[@]}" "$R/dumps/src.json" "$R/dumps/dst.json" > "$R/logs/judge.txt" 2>&1; then
            ok "the dumps agree ($(tail -1 "$R/logs/judge.txt"))"
        else
            bad "the dumps differ: $(grep -c '^DIFF' "$R/logs/judge.txt") fields (logs/judge.txt)"
        fi
        if python3 "$HERE/judge.py" qubes "$R/dumps/src.json" "$R/dumps/dst.json" "$R/dumps/src-raw.txt" "$R/dumps/dst-raw.txt" > "$R/logs/judge-qubes.txt" 2>&1; then
            ok "Qubes: $(tail -1 "$R/logs/judge-qubes.txt")"
        else
            bad "Qubes: $(grep '^DIFF' "$R/logs/judge-qubes.txt" | head -3 | tr '\n' ' ') (logs/judge-qubes.txt)"
        fi
    else
        bad "dump of the restored disks: $(tail -1 "$R/logs/dump-dst.err")"
    fi
}

: > "$R/report.txt"
say "HoltTest1 $G (read off its disks), run $RUN, $(date -u +%FT%TZ), phases: ${PHASES[*]}"
tools=(); [[ $KIND == redox ]] && tools=("$REDOX_TOOLS/redoxfs" "$REDOX_TOOLS/redoxfs-mkfs")
[[ $KIND == qubes ]] && tools=("$T/qubes/qubes-drive.py")
ht_manifest "$RESCUER_IMG" "${tools[@]}"
for p in "${PHASES[@]}"; do
    ht_phase_start "$p"; "phase_$p"; ht_phase_end "$p"
    [[ $fails == 0 ]] || { say "stopped after $p"; break; }
done
ht_summary
# (harness()'s why only when every FAIL was one.)
((hfails < fails)) && HARNESS_WHY=""
say "RESULT: $([[ $fails == 0 ]] && echo PASS || echo "FAIL ($fails${HARNESS_WHY:+; harness: $HARNESS_WHY})")"
[[ $fails == 0 ]]
