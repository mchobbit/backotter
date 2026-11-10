#!/usr/bin/env bash
# HoltTest1x for a guest that can't run bko-holt itself (README: "Systems
# read off their disks"): Redox (its sshd serves no logins) and Sculpt OS
# (Genode, no shell at all).  ht1.sh hands them here.  Run as root on a
# test host (Redox's on one with ~/backotter-work/tools/redox, Cinqcent):
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
# The write path is ht1-lib.sh's, as ht1.sh's (README: "Where a run works,
# and what it keeps").  HT_HOLT is the bko-holt to test instead of bin/'s,
# HT_HOLT_BIN a set to take it from (ht1.sh's header).  HT_VARIANT takes
# "bigger" and "oneshot" as ht1.sh does, and "refuse", which checks that a
# smaller target is still refused for a layout of another family, --fit-largest
# or no --fit-largest (README "The fitting variants"): source and rebuild only.
set -uo pipefail

G=$1 RUN=$2; shift 2
PHASES=("$@")   # the default is the variant's (below): all four, or source and rebuild
W=/home/m/backotter-work
HERE=$(dirname "$(realpath "$0")")
T=$(dirname "$HERE")   # this copy's testvm/ (vm-run, compare/, redox/, sculpt-check.sh), as ht1.sh's
BIN=/bkoscratch/holttest1d/bin
# bko-holt (Linux's, run in the rescuer): HT_HOLT's, else HT_HOLT_BIN's set's
# (DIR/bko-holt: HoltTest2's builds), else bin/'s (ht1.sh's header).
HOLT=${HT_HOLT:-${HT_HOLT_BIN:-$BIN}/bko-holt}
[[ -n ${HT_HOLT:-} || -z ${HT_HOLT_BIN:-} || -f $HOLT ]] || { echo "ht1-offline: no $HOLT (HT_HOLT_BIN=$HT_HOLT_BIN)" >&2; exit 2; }
[[ -r $HERE/ht1-lib.sh ]] || { echo "ht1-offline: no ht1-lib.sh beside $0" >&2; exit 2; }
# shellcheck source=ht1-lib.sh
. "$HERE/ht1-lib.sh"
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
ht_paths   # R, where the run works, and K, where it ends up (ht1-lib.sh)
S=$R/share

case $G in
    redox) KIND=redox ;;
    sculpt*) KIND=sculpt ;;
    *) echo "ht1-offline: $G isn't Redox or Sculpt" >&2; exit 2 ;;
esac
REDOX_TOOLS=$W/tools/redox/bin
[[ $KIND != redox || ( -x $REDOX_TOOLS/redoxfs && -x $REDOX_TOOLS/redoxfs-mkfs ) ]] || { echo "ht1-offline: redox needs $REDOX_TOOLS (Cinqcent has it)" >&2; exit 2; }
gdir=$W/vms/$G
gimg=$gdir/$G.qcow2; [[ -f $gimg ]] || gimg=$(ls "$gdir"/*.qcow2 | head -n 1)
imgs=("$gimg")   # the guest's disks, for ht1-lib.sh's manifest
fw=(); [[ -f $gdir/vm-run-options ]] && read -ra fw < "$gdir/vm-run-options"
RESCUER_IMG=${HT_RESCUER_IMG:-/bkoscratch/holttest1d/rescuer/rescuer.qcow2}
rkey=$W/vms/debian13/id_ed25519
SRC=$R/src-0.qcow2 DST=$R/dst-0.qcow2
SRC_MP=/mnt/ht1-src-$GV DST_MP=/mnt/ht1-dst-$GV
size_of() { qemu-img info --output=json "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["virtual-size"])'; }
# Where the run works and what a pass keeps (ht1-lib.sh; README "Where a run
# works, and what it keeps").
HT_BULK=(share src-0.qcow2 dst-0.qcow2 decoy.qcow2 'rescuer*.qcow2' 'boot-*.qcow2' 'console-*.sock' 'tmp-*' 'check-*/sculpt-check.qcow2' rustic-bko tmp)
HT_ZIP=(src-meta.json dst-meta.json)
ht_require
case ${PHASES[0]} in   # what the first phase remakes is not copied back from K
    backup)  ht_seed share/repo/ dst-0.qcow2 decoy.qcow2 'rescuer*.qcow2' 'tmp-*' tmp/ ;;
    rebuild) ht_seed src-0.qcow2 dst-0.qcow2 decoy.qcow2 'rescuer*.qcow2' 'tmp-*' tmp/ ;;
    verify)  ht_seed src-0.qcow2 share/repo/ decoy.qcow2 'rescuer*.qcow2' 'tmp-*' tmp/ ;;
esac
ht_begin; mkdir -p "$S"
ht_rustic_take linux
HT_MOUNTED=""
ht_release() {   # before anything is moved: no mount, loop device or console VM may hold R
    local raw l
    [[ -n $HT_MOUNTED ]] && host_umount "$HT_MOUNTED" 2>/dev/null
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
sshk() { timeout "${TMO:-600}" ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o ServerAliveInterval=15 root@127.0.0.1 "$@"; }
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
    chmod -R a+rX "$S"
}

# bko-holt dump --disk of IMAGE, attached to a throwaway rescuer as vdb.
offline_dump() { # IMAGE OUT NAME
    local o=$R/rescuer-$3.qcow2
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

# Whether IMAGE boots: Redox's root login on the serial console, uname and
# hostname; Sculpt's screen and its partition used (sculpt-check.sh).
boot_check() { # IMAGE NAME
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
    if boot_check "$SRC" source; then ok "source boots ($([[ $KIND == redox ]] && echo "root's login on its serial console" || echo "sculpt-check: screen and partition"))"; else bad "source boots (logs/source-*)"; return; fi
    if offline_dump "$SRC" "$R/dumps/src.json" src; then ok "dump of the source's disks (bko-holt dump --disk, in the rescuer)"; else bad "dump of the source's disks: $(tail -1 "$R/logs/dump-src.err")"; return; fi
    cp "$R/dumps/src.json" "$S/layout.json"
}

phase_backup() {
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

load_step() { local want=$1 name=$2; shift 2
    sshk "/mnt/share/bko-holt load /mnt/share/layout.json $*" > "$R/logs/rebuild-$name.out" 2> "$R/logs/rebuild-$name.err"
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
    r_up "$R/rescuer.qcow2" "$R/logs/rescuer-serial.log" --disk "$DST" --disk "$R/decoy.qcow2" || { bad "rescuer boots"; r_down; return; }
    sshk 'for t in redoxfs redoxfs-mkfs; do [ ! -f /mnt/share/$t ] || install -m 755 /mnt/share/$t /usr/local/sbin/$t; done; mkfs.ext4 -q -F -L ht1decoy /dev/vdc' > "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup"; r_down; return; }
    ok "rescuer up, share mounted"
    local st=$((($(size_of "$gimg") + DELTA) / 512)) sd=$(($(size_of "$gimg") / 512))
    local edges="for x in /dev/vdb:$st /dev/vdc:$sd; do r=\${x%:*} s=\${x##*:}; echo \$r \$(dd if=\$r bs=512 count=2048 status=none | md5sum | cut -c1-32) \$(dd if=\$r bs=512 skip=\$((s - 2048)) count=2048 status=none | md5sum | cut -c1-32); done"
    sshk "$edges" > "$R/logs/edges-before.txt" 2>&1
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
        sshk "$edges" > "$R/logs/edges-after.txt" 2>&1
        if [[ -s $R/logs/edges-before.txt ]] && cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
            ok "the refusals left the target's and the decoy's first and last MiB as they were"
        else
            bad "a refusal wrote to a disk (logs/edges-before.txt, edges-after.txt)"
        fi
        [[ $(sshk 'blkid -o value -s LABEL /dev/vdc') == ht1decoy ]] || bad "the decoy lost its filesystem"
        r_down
        rm -f "$R/rescuer.qcow2"
        return
    fi
    load_step 0 dry-partition --map vdb=vdb --only partition
    if sshk 'test -z "$(wipefs -n /dev/vdb)"'; then ok "nothing written without --commit"; else bad "vdb was written without --commit"; fi
    sshk "$edges" > "$R/logs/edges-after.txt" 2>&1
    if [[ -s $R/logs/edges-before.txt ]] && cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
        ok "the refusals and the dry run left every target's and the decoy's first and last MiB as they were"
    else
        bad "a refusal or the dry run wrote to a disk (logs/edges-before.txt, edges-after.txt)"
    fi
    local map=(--map vdb=vdb --erase /dev/vdb)
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
        sshk "/mnt/share/bko-holt load /mnt/share/layout.json ${map[*]} --only status" > "$R/logs/rebuild-status-after-$s.txt" 2>&1
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

: > "$R/report.txt"
say "HoltTest1 $G (read off its disks), run $RUN, $(date -u +%FT%TZ), phases: ${PHASES[*]}"
tools=(); [[ $KIND == redox ]] && tools=("$REDOX_TOOLS/redoxfs" "$REDOX_TOOLS/redoxfs-mkfs")
ht_manifest "$RESCUER_IMG" "${tools[@]}"
for p in "${PHASES[@]}"; do
    ht_phase_start "$p"; "phase_$p"; ht_phase_end "$p"
    [[ $fails == 0 ]] || { say "stopped after $p"; break; }
done
ht_summary
say "RESULT: $([[ $fails == 0 ]] && echo PASS || echo "FAIL ($fails)")"
[[ $fails == 0 ]]
