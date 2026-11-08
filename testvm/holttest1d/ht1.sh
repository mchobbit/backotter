#!/usr/bin/env bash
# HoltTest1x driver (testvm/holttest1d/README.md): a whole-machine round trip
# of GUEST through bko-holt and rustic-bko.  Run as root on a test host:
#
#   ht1.sh GUEST RUN [PHASE...]      PHASEs: source backup rebuild verify
#                                    (default: all four, in that order)
#
# RUN names the run's directory, /bkoscratch/holttest1d/RUN/GUEST/ (K),
# where the run ends up.  Under a reservation of the RAM disk (cmp-run.sh
# --reserve D M, D from ram-size.sh) it works in
# /mnt/bko-ram/holttest1d/RUN/GUEST/ and moves to K at its end.  A pass
# that ran verify keeps only report.txt, logs/, dumps/ and the fsmeta
# records (zstd); a FAIL keeps everything (README "Where a run works, and
# what it keeps").  Later phases reuse what earlier ones left, so after a
# bko-holt fix only `rebuild verify` need running again: on a failure's
# state, copied back into RAM from K, not on records only (a pass's).
# Needs /bkoscratch/holttest1d/bin/ with bko-holt, f2fs-tools_*.deb and
# jfsutils_*.deb (installed in a rescuer that lacks them), and bcachefs
# with bcachefs-deps-*.deb (make-bcachefs-tools.sh: Debian 13 has no
# bcachefs-tools).  rustic-bko is the shipped set's
# (~/backotter-work/bko-bin/linux/, checked against its SHA256SUMS).
#
#   source   dump the live guest (a throwaway overlay; the guest's own
#            image is never written), with its boot state as the baseline
#   backup   rustic-bko backup of the overlay mounted read-only, as
#            compare.py does; fsmeta.py records it
#   rebuild  a rescuer VM (a throwaway debian13 overlay) gets a blank disk
#            and the share (9p, read-only); bko-holt load's safety checks
#            must refuse, then each stage runs with --commit, `status`
#            checked after each
#   verify   fsmeta.py compares the restored disk with the source; then the
#            restored disk boots alone (writes thrown away) and is dumped;
#            judge.py compares the dumps
#
# Where it works (ht1-lib.sh): with HT_RAM unset, in RAM when the run holds
# a reservation of at least 1 GiB, else on /bkoscratch (a note: line says
# so); HT_RAM=1 refuses without one; HT_RAM=0 works on /bkoscratch.  In RAM
# a run that outgrows its reservation is stopped (the harness's FAIL, which
# keeps the records only).  HT_CLEAN=0 leaves the run as it ends: nothing
# deleted, compressed or moved.  HT_RESERVE_PID is the ledger PID of a
# reservation that outlives the run (ht1-debug.sh's); in RAM, HT_CLEAN=0
# and HT_KEEP=1 need it.  For rustic-bko, HT_BKO_BIN=DIR takes another set,
# HT_ROUND=NAME round NAME's (~/backotter-work/roundNAME/bin), and
# HT_RUSTIC=FILE a file, unchecked.  HT_FAIL_AT=PHASE adds a FAIL line
# after that phase (a test hook).
#
# For debugging (ht1-debug.sh drives these): HT_HOLT=FILE is the bko-holt
# to test instead of bin/'s (either is copied as the run starts, and that
# copy serves the whole run); HT_QUICK=1 leaves out rebuild's refusals, dry
# run and edge checks; HT_THROUGH=STAGE ends rebuild after that load stage;
# HT_KEEP=1 leaves the rescuer running after rebuild, its disks and load's
# state as they are, for a look inside.  A rebuild first stops a rescuer
# kept up by the last try; on /bkoscratch a run from source refuses while
# one is up.  The report's note: line names those set.  A
# run stops only its own VMs (the ones it started with disks in R:
# ht1-lib.sh's ht_qemus), never another run's on its port (HT_PORT, default
# 2690; by-hand runs use 2690-2699), nor one started by hand on R's files.
#
# Exit 0 when every check of the phases run passed; 1 when one failed or
# the run ended early; 2 when it refused to start (nothing made or kept);
# 129, 130 or 143 when HUP, INT or TERM ended it (RESULT: FAIL).  Results:
# report.txt, logs/ (manifest.txt: the binaries and inputs; phases.tsv:
# each phase's time and peaks), dumps/.  The rescuer is Debian 13, not
# BackOtter's rescue image yet (README: "Before it can run").
set -uo pipefail

G=$1 RUN=$2; shift 2
# Systems other than Linux have their own driver (their rescuer is of their
# own system, and Linux can't read their filesystems).
case $G in
    freebsd*|hardenedbsd*|ghostbsd*|netbsd*|alpinebsd*|openbsd*|dragonfly*|omnios*|haiku*)
        exec "$(dirname "$(realpath "$0")")/ht1-bsd.sh" "$G" "$RUN" "$@" ;;
    # Systems that can't run bko-holt: read off their disks from Linux.
    redox|sculpt*)
        exec "$(dirname "$(realpath "$0")")/ht1-offline.sh" "$G" "$RUN" "$@" ;;
esac
PHASES=("$@"); [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(source backup rebuild verify)
W=/home/m/backotter-work
HERE=$(dirname "$(realpath "$0")")
T=$(dirname "$HERE")   # this copy's testvm/ (vm-run, vm-mount, compare/)
[[ -r $HERE/ht1-lib.sh ]] || { echo "ht1: no ht1-lib.sh beside $0" >&2; exit 2; }
# shellcheck source=ht1-lib.sh
. "$HERE/ht1-lib.sh"
BIN=/bkoscratch/holttest1d/bin
HOLT=${HT_HOLT:-$BIN/bko-holt}
# HT_VARIANT (README's HoltTest1D plan): "bigger" rebuilds onto disks 1 GiB
# bigger than the source's (GPT's backup header must move to the new end);
# "oneshot" runs the whole rebuild as one `--through close` command, as an
# administrator would; "degraded" runs the source with its last disk
# detached (a RAID member's: its arrays degraded), so load recreates those
# arrays with "missing" slots onto one disk fewer, and the restored machine
# must boot degraded too.  A variant has a directory of its own, GUEST-VARIANT.
VARIANT=${HT_VARIANT:-}
case $VARIANT in ""|bigger|oneshot|degraded) ;; *) echo "HT_VARIANT: bigger, oneshot or degraded" >&2; exit 2 ;; esac
GV=$G${VARIANT:+-$VARIANT}
PORT=${HT_PORT:-2690}   # by-hand runs: 2690-2699 (ht1-queue.sh hands out 2600-2689)
export SSH_PORT=$PORT   # vm-run's guest SSH port
RESCUER=${HT_RESCUER:-debian13}
# The rescuer: make-rescuer.sh's debian13 copy with ZFS and every mkfs, when
# there is one; else the debian13 guest itself.
RESCUER_IMG=${HT_RESCUER_IMG:-/bkoscratch/holttest1d/rescuer/rescuer.qcow2}
[[ -f $RESCUER_IMG ]] || RESCUER_IMG=$W/vms/$RESCUER/$RESCUER.qcow2
SRC_MP=/mnt/ht1-src-$GV
DST_MP=/mnt/ht1-dst-$GV
fails=0
say() { echo "$*" | tee -a "$R/report.txt"; }
ok() { say "PASS $*"; }
bad() { say "FAIL $*"; fails=$((fails + 1)); }

gkey=$W/vms/$G/id_ed25519
rkey=$W/vms/$RESCUER/id_ed25519
# The guest's disks (vm-run-options' --disk, after its own image) and
# firmware options (--uefi and the like, for the source and restored boots).
imgs=("$W/vms/$G/$G.qcow2") fw=()
if [[ -f $W/vms/$G/vm-run-options ]]; then
    read -ra o < "$W/vms/$G/vm-run-options"
    for ((i = 0; i < ${#o[@]}; i++)); do
        case ${o[i]} in
        --disk) d=${o[++i]}; [[ $d == /* ]] || d=$W/vms/$G/$d; imgs+=("$d") ;;
        *) fw+=("${o[i]}") ;;
        esac
    done
fi
# R, where the run works, and K, where it ends up (ht1-lib.sh: everything it
# reads is set by here).  Nothing is made until ht_begin.
ht_paths
S=$R/share
uefi=0; [[ " ${fw[*]} " == *" --uefi "* ]] && uefi=1
detached=""
if [[ $VARIANT == degraded ]]; then
    ((${#imgs[@]} >= 2)) || { echo "HT_VARIANT=degraded: $G has one disk" >&2; exit 2; }
    detached=${imgs[-1]}; unset 'imgs[-1]'
fi
# A UEFI source is rebuilt from a UEFI-booted rescuer (make-rescuer.sh
# --uefi, from ubuntu26-uefi), as BackOtter's stick boots in the machine's
# own firmware mode: grub-mkconfig writes UEFI's grub.cfg only there.
rfw=()
if ((uefi)) && [[ -z ${HT_RESCUER_IMG:-} && -f /bkoscratch/holttest1d/rescuer/rescuer-uefi.qcow2 ]]; then
    RESCUER=ubuntu26-uefi RESCUER_IMG=/bkoscratch/holttest1d/rescuer/rescuer-uefi.qcow2 rfw=(--uefi)
    rkey=$W/vms/$RESCUER/id_ed25519
fi
n=${#imgs[@]}
src=() dst=() extra_src=() extra_dst=() map=() erase=()
letters=(a b c d e f g h)
for ((i = 0; i < n; i++)); do
    src+=("$R/src-$i.qcow2"); dst+=("$R/dst-$i.qcow2")
    ((i > 0)) && extra_src+=(--disk "$R/src-$i.qcow2") && extra_dst+=(--disk "$R/dst-$i.qcow2")
    # The rescuer's own disk is vda: the source's vdX goes to the next letter on.
    map+=(--map "vd${letters[i]}=vd${letters[i+1]}"); erase+=(--erase "/dev/vd${letters[i+1]}")
done
size_of() { qemu-img info --output=json "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["virtual-size"])'; }
# A LUKS or ZFS passphrase the guest's disks need (vm-mount --key, and load's
# --key-file for the volumes it makes).
gpass=""; for f in console-passphrase zfs-passphrase luks-passphrase; do [[ -f $W/vms/$G/$f ]] && gpass=$W/vms/$G/$f && break; done
# Where the run works and what a pass keeps (ht1-lib.sh; README "Where a run works, and what it keeps").
HT_BULK=(share 'src-*.qcow2' 'dst-*.qcow2' decoy.qcow2 rescuer.qcow2 rustic-bko tmp)
HT_ZIP=(src-meta.json dst-meta.json)
# Settings that change what a run checks, or its rescuer: its report names them.
dbg=""; for v in HT_QUICK HT_THROUGH HT_KEEP HT_FAIL_AT HT_RESCUER HT_RESCUER_IMG; do [[ -z ${!v:-} ]] || dbg+=" $v=${!v}"; done
[[ -z $dbg ]] || ht_note "set for debugging or tests:$dbg"
ht_require
# A rerun from a later phase goes on from K's state (a failure's): all of it
# but what the rerun makes again and the disks only a rebuild uses, so the
# attempt it leaves in K can be rerun again.
case ${PHASES[0]} in
    backup)  ht_seed share/repo/ 'dst-*.qcow2' decoy.qcow2 rescuer.qcow2 tmp/ ;;
    rebuild) ht_seed 'dst-*.qcow2' decoy.qcow2 rescuer.qcow2 tmp/ ;;
    verify)  ht_seed decoy.qcow2 rescuer.qcow2 tmp/ ;;
esac
ht_begin; mkdir -p "$S"
ht_rustic_take linux
# bko-holt as the run starts (bin/'s may be replaced while it runs): the
# dumps and the load are one build's.
cp --remove-destination "$HOLT" "$S/bko-holt"
HT_MOUNTED=""
ht_release() { [[ -n $HT_MOUNTED ]] && mountpoint -q "$HT_MOUNTED" && "$T/vm-umount" "$HT_MOUNTED" >/dev/null 2>&1; rmdir "$SRC_MP" "$DST_MP" 2>/dev/null; }

sshk() { local key=$1; shift; timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o ServerAliveInterval=15 root@127.0.0.1 "$@"; }
scpk() { local key=$1; shift; timeout 300 scp -q -i "$key" -P "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$@"; }
vm_up() { # IMAGE LOG [vm-run options...] [-- QEMU_ARGS...]
    local image=$1 log=$2 opts=() qargs=(); shift 2
    while [[ $# -gt 0 && $1 != -- ]]; do opts+=("$1"); shift; done
    [[ ${1:-} == -- ]] && { shift; qargs=(-- "$@"); }
    # (Without ht_begin's copies of the caller's stdout and stderr: a rescuer
    # HT_KEEP keeps up outlives the run, and its caller's ssh or pipe.)
    if [[ ${CONSOLE_KEY:-} == */luks-passphrase ]]; then
        # Asked for on the screen (Plymouth, Omarchy): typed on the guest's
        # keyboard through QEMU's monitor, every 30 s until SSH answers (at
        # a login prompt it does no harm), as compare.py does.
        rm -f "$log.monitor"
        [[ ${#qargs[@]} -gt 0 ]] || qargs=(--)
        setsid -f "$T/vm-run" --serial-log "$log" "${opts[@]}" "$image" "${qargs[@]}" -monitor "unix:$log.monitor,server,nowait" </dev/null >"$log.vm-run" 2>&1 {HT_FD1}>&- {HT_FD2}>&-
        setsid -f python3 "$T/omarchy/type-luks-passphrase.py" "$log.monitor" "$CONSOLE_KEY" $(seq 25 30 295) </dev/null >/dev/null 2>&1 {HT_FD1}>&- {HT_FD2}>&-
    elif [[ -n ${CONSOLE_KEY:-} ]]; then
        # The guest asks for its disk passphrase on the serial console:
        # console-boot.py logs the console and types it at each prompt.
        setsid -f python3 "$T/console-boot.py" "$log" "$CONSOLE_KEY" -- \
            "$T/vm-run" "${opts[@]}" "$image" "${qargs[@]}" </dev/null 2>"$log.vm-run" {HT_FD1}>&- {HT_FD2}>&-
    else
        setsid -f "$T/vm-run" --serial-log "$log" "${opts[@]}" "$image" "${qargs[@]}" </dev/null >"$log.vm-run" 2>&1 {HT_FD1}>&- {HT_FD2}>&-
    fi
}
vm_wait() { local key=$1
    for _ in $(seq 72); do TMO=8 sshk "$key" true 2>/dev/null && return 0; sleep 5; done
    return 1
}
# This run's VM only (ht_qemus: a QEMU with its disks in R that this run
# started, ht_own), never another run's that took the port while none of
# this run's was up, nor one started by hand on R's (K's) disks.
vm_down() { local key=$1
    [[ -n $(ht_qemus "$R") ]] || return 0
    TMO=20 sshk "$key" poweroff >/dev/null 2>&1
    for _ in $(seq 36); do [[ -n $(ht_qemus "$R") ]] || return 0; sleep 5; done
    # shellcheck disable=SC2046  # PIDs
    kill $(ht_qemus "$R") 2>/dev/null; sleep 5
    # shellcheck disable=SC2046
    kill -9 $(ht_qemus "$R") 2>/dev/null; sleep 1
}
# The restored machine's health (README step 4): one line per check, FAIL
# lines for those that fail, exit 1 if any did.
HEALTH='f=0; no() { echo "FAIL $*"; f=1; }
findmnt --verify >/dev/null 2>&1 && echo "fstab verifies" || no "findmnt --verify: $(findmnt --verify 2>&1 | grep -iE "error|warn" | head -2 | tr "\n" " ")"
m=0; for t in $(awk "\$1 !~ /^#/ && \$2 ~ /^\// && \$4 !~ /noauto/ {print \$2}" /etc/fstab); do mountpoint -q "$t" && m=$((m + 1)) || no "fstab mount $t not mounted"; done; echo "$m fstab mounts mounted"
[ "$(grep -c . /proc/swaps)" -gt 1 ] && echo "swap on" || no "no swap"
if command -v zpool >/dev/null && [ -n "$(zpool list -H -o name 2>/dev/null)" ]; then zpool status -x | grep -q "all pools are healthy" && echo "zpool healthy" || no "zpool status -x: $(zpool status -x | head -3 | tr "\n" " ")"; fi
[ -r /proc/mdstat ] && for a in $(awk "/^md/ {print \$1}" /proc/mdstat); do st=$(mdadm --detail /dev/$a | sed -n "s/^ *State : *//p" | sed "s/ *$//"); case $st in clean|active|"active, resyncing"|"clean, resyncing") echo "$a $st" ;; "clean, degraded"|"active, degraded") [ "${DEGRADED:-0}" = 1 ] && echo "$a $st" || no "$a: $st" ;; *) no "$a: $st" ;; esac; done
[ -f /etc/crypttab ] && for c in $(awk "\$1 !~ /^#/ && NF {print \$1}" /etc/crypttab); do cryptsetup status "$c" 2>/dev/null | grep -q "is active" && echo "$c open" || no "LUKS $c not open"; done
ls /var/log/bko-holt/load.log >/dev/null 2>&1 && echo "load log kept" || no "/var/log/bko-holt/load.log missing"
exit $f'
# Boot state: line 1 systemd's overall state (or the init system's name),
# then one line per failed unit or service that isn't up, sorted.  A
# restored machine may have no line its source lacked.
BOOTSTATE='if [ -d /run/systemd/system ]; then
    timeout 300 systemctl is-system-running --wait; systemctl --failed --no-legend --plain | awk "{print \$1}" | sort
elif command -v rc-status >/dev/null; then
    echo openrc
    { rc-status -a | awk "/^Runlevel: / {lv=\$2; next} /^Dynamic Runlevel: / {lv=\$3; next} (lv == \"sysinit\" || lv == \"boot\" || lv == \"default\" || lv == \"needed/wanted\") && !/\\[ *started *\\]/ {print \"down \" \$1}"
      rc-status --crashed | sed "s/^/crashed /"; } | sort
elif command -v dinitctl >/dev/null; then
    echo dinit
    dinitctl list | awk "!/^\\[[^]]*\\+[^]]*\\]/ {sub(/^\\[[^]]*\\] */, \"\"); print \"down \" \$1}" | sort
elif command -v s6-rc >/dev/null; then
    echo s6
    s6-rc -da list | sed "s/^/down /" | sort
else
    echo "unknown init"
fi'
bootstate() { sshk "$1" "$BOOTSTATE"; }
# The degraded variant: the arrays may be degraded (and must be, see below).
[[ $VARIANT == degraded ]] && HEALTH="DEGRADED=1; $HEALTH"
# md's view of each array: its name, level, and member slots up or missing
# ([UU_]), sorted.  A degraded run's restored machine must show the source's.
MDSTAT='awk "/^md/ {a = \$1; for (i = 3; i <= NF; i++) if (\$i ~ /^(raid|linear)/) {l = \$i; break}} / blocks / && a {print a, l, \$NF; a = \"\"}" /proc/mdstat | sort'

phase_source() {
    say "== source: $G"
    for ((i = 0; i < n; i++)); do
        rm -f "${src[i]}"; qemu-img create -q -f qcow2 -b "${imgs[i]}" -F qcow2 "${src[i]}"
    done
    CONSOLE_KEY=$gpass vm_up "${src[0]}" "$R/logs/source-serial.log" "${fw[@]}" "${extra_src[@]}"
    vm_wait "$gkey" || { bad "source boots (no SSH)"; vm_down "$gkey"; return; }
    ok "source boots${detached:+, with ${detached##*/} detached}"
    if [[ -n $detached ]]; then
        sshk "$gkey" "$MDSTAT" > "$R/logs/source-mdstat.txt"
        if grep -q '_' "$R/logs/source-mdstat.txt"; then
            ok "source's arrays degraded: $(grep '_' "$R/logs/source-mdstat.txt" | tr '\n' ';' | sed 's/;$//; s/;/; /g')"
        else
            bad "no array of the source is degraded with ${detached##*/} detached (logs/source-mdstat.txt)"
        fi
    fi
    bootstate "$gkey" > "$R/logs/source-bootstate.txt"
    sshk "$gkey" "$HEALTH" 2>&1 | grep -v 'bko-holt/load.log' > "$R/logs/source-health.txt"
    scpk "$gkey" "$S/bko-holt" root@127.0.0.1:/root/bko-holt
    if sshk "$gkey" /root/bko-holt dump > "$R/dumps/src.json" 2> "$R/logs/source-dump.err"; then
        ok "dump on the live source"
    else
        bad "dump on the live source: $(tail -1 "$R/logs/source-dump.err")"
    fi
    sshk "$gkey" 'rm -f /root/bko-holt'
    vm_down "$gkey"
    cp "$R/dumps/src.json" "$S/layout.json"
}

phase_backup() {
    say "== backup"
    rm -rf "$S/repo"; head -c 24 /dev/urandom | base64 > "$S/repo-password"
    mkdir -p "$SRC_MP"
    local key=(); [[ -n $gpass ]] && key=(--key "$gpass")
    # vm-mount and vm-umount ignore TERM, INT and HUP (a ^C, an abort): one
    # cut short leaves the host's devices half done.  The driver's own trap
    # runs once it returns.
    (trap '' TERM INT HUP; exec "$T/vm-mount" --ro "${extra_src[@]}" "${key[@]}" "${src[0]}" "$SRC_MP") > "$R/logs/backup-mount.log" 2>&1 || { bad "mount the source read-only"; return; }
    HT_MOUNTED=$SRC_MP
    local rustic=("$RUSTIC" -r "$S/repo" --password-file "$S/repo-password" --no-progress --no-cache)
    if "${rustic[@]}" init > "$R/logs/backup.log" 2>&1 && "${rustic[@]}" backup --set-atime yes "$SRC_MP" >> "$R/logs/backup.log" 2>&1; then
        ok "rustic-bko backup ($(du -sh "$S/repo" | cut -f1))"
    else
        bad "rustic-bko backup (logs/backup.log)"
    fi
    python3 "$T/compare/fsmeta.py" record "$SRC_MP" "$R/src-meta.json" > "$R/logs/fsmeta-src.log" 2>&1 || bad "fsmeta record of the source"
    (trap '' TERM INT HUP; exec "$T/vm-umount" "$SRC_MP") >> "$R/logs/backup-mount.log" 2>&1; HT_MOUNTED=
    cp "$RUSTIC" "$S/rustic-bko"; cp "$BIN"/*.deb "$S/"   # (bko-holt is there from the run's start)
    [[ ! -f $BIN/bcachefs ]] || cp "$BIN/bcachefs" "$S/"   # make-bcachefs-tools.sh's
    # As typed: without the file's newline (cryptsetup keeps it in a key).
    [[ -n $gpass ]] && printf '%s' "$(cat "$gpass")" > "$S/passphrase"
    chmod -R a+rX "$S"
}

# One bko-holt load call in the rescuer: WANT exit, NAME, then load's args.
load_step() { local want=$1 name=$2; shift 2
    sshk "$rkey" "/mnt/share/bko-holt load /mnt/share/layout.json $*" > "$R/logs/rebuild-$name.out" 2> "$R/logs/rebuild-$name.err"
    local got=$?
    if [[ $got == "$want" ]]; then ok "load $name (exit $got)"; return 0; fi
    bad "load $name: exit $got, want $want: $(grep -v '^$' "$R/logs/rebuild-$name.err" | tail -3 | tr '\n' ' ')"
    return 1
}

phase_rebuild() {
    say "== rebuild"
    # A rescuer HT_KEEP left up from the last try.
    [[ -z $(ht_qemus "$R") ]] || vm_down "$rkey"
    cp "$RUSTIC" "$S/rustic-bko"; cp "$BIN"/*.deb "$S/"   # this run's, when rerun after a fix (bko-holt: copied as the run started)
    [[ ! -f $BIN/bcachefs ]] || cp "$BIN/bcachefs" "$S/"
    rm -f "$R/rescuer.qcow2" "${dst[@]}"
    qemu-img create -q -f qcow2 -b "$RESCUER_IMG" -F qcow2 "$R/rescuer.qcow2"
    local disks=() grow=0
    [[ $VARIANT == bigger ]] && grow=$((1 << 30))
    for ((i = 0; i < n; i++)); do
        qemu-img create -q -f qcow2 "${dst[i]}" "$(($(size_of "${imgs[i]}") + grow))"; disks+=(--disk "${dst[i]}")
    done
    # The decoy: a disk after the targets that already holds a filesystem,
    # as big as the source's first disk, so not being blank is the only
    # reason load can give for refusing it.
    local decoy=/dev/vd${letters[n+1]}
    rm -f "$R/decoy.qcow2"; qemu-img create -q -f qcow2 "$R/decoy.qcow2" "$(size_of "${imgs[0]}")"
    disks+=(--disk "$R/decoy.qcow2")
    # The rescuer boots in the source's firmware mode.  Its UEFI variables
    # aren't the restored machine's (each vm-run --uefi starts fresh), so
    # the boot stage runs with --no-nvram, and the restored disk boots from
    # the ESP's removable path.
    vm_up "$R/rescuer.qcow2" "$R/logs/rescuer-serial.log" "${rfw[@]}" --memory 3072 "${disks[@]}" -- \
        -virtfs "local,path=$S,mount_tag=bkoshare,security_model=none,readonly=on"
    vm_wait "$rkey" || { bad "rescuer boots"; vm_down "$rkey"; return; }
    sshk "$rkey" 'mkdir -p /mnt/share && mount -t 9p -o trans=virtio,version=9p2000.L,ro bkoshare /mnt/share && inst() { apt-get install -y -q -o DPkg::Lock::Timeout=600 "$@" >/dev/null; } && { command -v mkfs.f2fs >/dev/null || inst /mnt/share/f2fs-tools_*.deb; } && { command -v mkfs.jfs >/dev/null || inst /mnt/share/jfsutils_*.deb; } && { [ ! -f /mnt/share/bcachefs ] || command -v bcachefs >/dev/null || { for d in /mnt/share/bcachefs-deps-*.deb; do [ ! -f "$d" ] || inst "$d"; done; install -m 755 /mnt/share/bcachefs /usr/local/sbin/bcachefs; }; }' \
        > "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup (share, f2fs-tools, jfsutils)"; vm_down "$rkey"; return; }
    sshk "$rkey" "mkfs.ext4 -q -F -L ht1decoy $decoy" >> "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup (the decoy's filesystem)"; vm_down "$rkey"; return; }
    ok "rescuer up, share mounted"
    # The first and last MiB of every target and the decoy, before and after
    # the refusals and the dry run, which must write nothing.
    local edges="for d in ${erase[*]#--erase} $decoy; do s=\$(blockdev --getsz \$d); echo \$d \$(dd if=\$d bs=512 count=2048 status=none | md5sum | cut -c1-32) \$(dd if=\$d bs=512 skip=\$((s - 2048)) count=2048 status=none | md5sum | cut -c1-32); done"
    local flags=()
    ((uefi)) && flags+=(--no-nvram)
    if [[ ${HT_QUICK:-} != 1 ]]; then
    sshk "$rkey" "$edges" > "$R/logs/edges-before.txt" 2>&1
    # Safety first: each refuses and writes nothing.
    load_step 4 refuse-nomap
    load_step 4 refuse-root --map vda=vda --erase vda
    # The decoy, mapped without --erase, must be refused for holding a
    # filesystem (the other disks map to their blank targets).
    load_step 4 refuse-decoy "--map vda=${decoy#/dev/}" "${map[@]:2}" \
        && { cat "$R/logs/rebuild-refuse-decoy".{out,err} | grep -q "isn't blank" || bad "load refuse-decoy: refused, but not for the decoy's filesystem"; }
    fi
    [[ -n $gpass ]] && flags+=($(python3 - "$S/layout.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
# Every LUKS volume (crypttab's, and Ubuntu's ZFS keystore, which isn't in
# crypttab) gets the source's passphrase, as does a ZFS encryption root
# that asks for one; a root with a raw or hex key file gets a new key.
names = {e["name"] for e in (d["storage"].get("crypttab") or [])}
for dm in d["storage"].get("dm_devices") or []:
    if (dm.get("dm") or {}).get("target") == "crypt" and not any(n == dm["dm"].get("dm_name") for n in names):
        names.add(dm["dm"]["dm_name"])
for n in sorted(names):
    print(f"--key-file {n}=/mnt/share/passphrase")
for ds in ((d["storage"].get("zfs") or {}).get("tools") or {}).get("datasets") or []:
    if ds.get("encryptionroot") == ds.get("name") and ds.get("keyformat") == "passphrase":
        print(f"--key-file {ds['name']}=/mnt/share/passphrase")
PY
))
    if [[ ${HT_QUICK:-} != 1 ]]; then
    load_step 0 dry-partition "${map[@]}" "${flags[@]}" --only partition
    if sshk "$rkey" 'test -z "$(wipefs -n /dev/vdb)"'; then ok "nothing written without --commit"; else bad "vdb was written without --commit"; fi
    sshk "$rkey" "$edges" > "$R/logs/edges-after.txt" 2>&1
    if [[ -s $R/logs/edges-before.txt ]] && cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
        ok "the refusals and the dry run left every target's and the decoy's first and last MiB as they were"
    else
        bad "a refusal or the dry run wrote to a disk (logs/edges-before.txt, edges-after.txt)"
    fi
    fi
    local map=("${map[@]}" "${erase[@]}" "${flags[@]}")
    local repo=(--repo /mnt/share/repo --password-file /mnt/share/repo-password --archiver /mnt/share/rustic-bko --snapshot "latest:$SRC_MP")
    local stages=(partition assemble format mount restore boot close)
    # oneshot: the whole rebuild as one command; `status` after it must show
    # every stage it can see done.
    [[ $VARIANT == oneshot ]] && stages=(oneshot)
    if [[ -n ${HT_THROUGH:-} ]]; then
        [[ " ${stages[*]} " == *" $HT_THROUGH "* ]] || { bad "HT_THROUGH=$HT_THROUGH: not one of ${stages[*]}"; vm_down "$rkey"; return; }
        local upto=() st; for st in "${stages[@]}"; do upto+=("$st"); [[ $st == "$HT_THROUGH" ]] && break; done; stages=("${upto[@]}")
    fi
    for s in "${stages[@]}"; do
        local how=(--only "$s") extra=()
        [[ $s == restore || $s == oneshot ]] && extra=("${repo[@]}")
        [[ $s == oneshot ]] && how=(--through close)
        TMO=3600 load_step 0 "$s" "${map[@]}" "${extra[@]}" "${how[@]}" --commit || {
            sshk "$rkey" 'cat /run/bko-holt/log/load.log' > "$R/logs/rebuild-load.log"
            # What the rescuer looked like when the stage failed.
            sshk "$rkey" 'set -x; findmnt -R /mnt/bko-target; grep -E "zfs|/mnt/bko" /proc/mounts; zpool list -v; zfs list -o name,mounted,mountpoint,canmount; fuser -vm /mnt/bko-target 2>&1; lsblk -f; cat /proc/swaps; ls -l /dev/zvol 2>&1; dmsetup ls; cat /proc/mdstat; for f in /proc/[0-9]*/mountinfo; do grep -q bko-target $f 2>/dev/null && p=${f#/proc/} && p=${p%/mountinfo} && echo "mount namespace with bko-target: pid $p $(cat /proc/$p/comm) $(readlink /proc/$p/ns/mnt)"; done; readlink /proc/self/ns/mnt' > "$R/logs/rebuild-failed-state.txt" 2>&1
            kept_or_down; return; }
        sshk "$rkey" "/mnt/share/bko-holt load /mnt/share/layout.json ${map[*]} --only status" > "$R/logs/rebuild-status-after-$s.txt" 2>&1
        if [[ $s == oneshot ]]; then
            # Once closed, mount, restore and boot can't be seen (status
            # says so), just as after the staged run's close.
            local st
            for st in partition format close; do
                grep -q "^$st  *done" "$R/logs/rebuild-status-after-$s.txt" || bad "status after the one-command rebuild: $(grep "^$st " "$R/logs/rebuild-status-after-$s.txt")"
            done
            grep -q "^next: nothing" "$R/logs/rebuild-status-after-$s.txt" || bad "status after the one-command rebuild: $(grep '^next' "$R/logs/rebuild-status-after-$s.txt")"
        else
            grep -q "^$s  *done" "$R/logs/rebuild-status-after-$s.txt" || [[ $s == assemble ]] || bad "status after $s: $(grep "^$s " "$R/logs/rebuild-status-after-$s.txt")"
        fi
    done
    # The decoy was never a target: it still holds its filesystem.
    [[ $(sshk "$rkey" "blkid -o value -s LABEL $decoy") == ht1decoy ]] || bad "the decoy lost its filesystem"
    sshk "$rkey" 'cat /run/bko-holt/log/load.log' > "$R/logs/rebuild-load.log"
    [[ ${HT_KEEP:-} == 1 ]] && { kept_or_down; return; }
    vm_down "$rkey"
    rm -f "$R/rescuer.qcow2"
}
# HT_KEEP=1: the rescuer stays up, as the stage left it; else it goes down.
kept_or_down() {
    if [[ ${HT_KEEP:-} == 1 ]]; then
        say "rescuer kept up: ssh -i $rkey -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 (load: /mnt/share/bko-holt load /mnt/share/layout.json ...)"
    else
        vm_down "$rkey"
    fi
}

phase_verify() {
    say "== verify"
    # A rescuer HT_KEEP left up has the targets open.
    [[ -z $(ht_qemus "$R") ]] || vm_down "$rkey"
    mkdir -p "$DST_MP"
    local key=(); [[ -n $gpass ]] && key=(--key "$gpass")
    # (As in backup: vm-mount and vm-umount ignore TERM, INT and HUP.)
    if (trap '' TERM INT HUP; exec "$T/vm-mount" --ro "${extra_dst[@]}" "${key[@]}" "${dst[0]}" "$DST_MP") > "$R/logs/verify-mount.log" 2>&1; then
        HT_MOUNTED=$DST_MP
        python3 "$T/compare/fsmeta.py" record "$DST_MP" "$R/dst-meta.json" > "$R/logs/fsmeta-dst.log" 2>&1
        (trap '' TERM INT HUP; exec "$T/vm-umount" "$DST_MP") >> "$R/logs/verify-mount.log" 2>&1; HT_MOUNTED=
        python3 "$T/compare/fsmeta.py" compare "$R/src-meta.json" "$R/dst-meta.json" > "$R/logs/fsmeta-compare.txt" 2>&1
        if python3 "$HERE/judge.py" fs "$T/compare/fsmeta.py" "$R/src-meta.json" "$R/dst-meta.json" > "$R/logs/fsjudge.txt" 2>&1; then
            ok "fsmeta: the files agree but for the boot stage's ($(tail -1 "$R/logs/fsjudge.txt"))"
        else
            bad "fsmeta: $(grep '^DIFF' "$R/logs/fsjudge.txt" | head -5 | tr '\n' ' ')"
        fi
    else
        bad "mount the restored disk"
    fi
    CONSOLE_KEY=$gpass vm_up "${dst[0]}" "$R/logs/restored-serial.log" --snapshot "${fw[@]}" "${extra_dst[@]}"
    if ! vm_wait "$gkey"; then bad "the restored disk boots by itself (no SSH; logs/restored-serial.log)"; vm_down "$gkey"; return; fi
    ok "the restored disk boots by itself, SSH with the guest's key"
    if [[ -n $detached ]]; then
        sshk "$gkey" "$MDSTAT" > "$R/logs/restored-mdstat.txt"
        if cmp -s "$R/logs/source-mdstat.txt" "$R/logs/restored-mdstat.txt"; then
            ok "restored arrays degraded as the source's: $(grep '_' "$R/logs/restored-mdstat.txt" | tr '\n' ';' | sed 's/;$//; s/;/; /g')"
        else
            bad "restored arrays: $(tr '\n' ' ' < "$R/logs/restored-mdstat.txt"), source's $(tr '\n' ' ' < "$R/logs/source-mdstat.txt")"
        fi
    fi
    bootstate "$gkey" > "$R/logs/restored-bootstate.txt"
    if diff -q <(head -1 "$R/logs/source-bootstate.txt") <(head -1 "$R/logs/restored-bootstate.txt") >/dev/null \
        && [[ -z $(comm -13 <(tail -n +2 "$R/logs/source-bootstate.txt") <(tail -n +2 "$R/logs/restored-bootstate.txt")) ]]; then
        ok "boot state as the source's ($(head -1 "$R/logs/restored-bootstate.txt"))"
    else
        bad "boot state: source $(tr '\n' ' ' < "$R/logs/source-bootstate.txt"), restored $(tr '\n' ' ' < "$R/logs/restored-bootstate.txt")"
    fi
    # A check that failed on the source too isn't the restore's doing.
    sshk "$gkey" "$HEALTH" > "$R/logs/restored-health.txt" 2>&1
    local worse; worse=$(comm -13 <(grep '^FAIL' "$R/logs/source-health.txt" 2>/dev/null | sort) <(grep '^FAIL' "$R/logs/restored-health.txt" | sort))
    if [[ -z $worse ]] && grep -q 'load log kept' "$R/logs/restored-health.txt"; then
        ok "health: $(grep -v '^FAIL' "$R/logs/restored-health.txt" | tr '\n' ',' | sed 's/,$//; s/,/, /g')$(grep -q '^FAIL' "$R/logs/restored-health.txt" && echo "; as on the source: $(grep '^FAIL' "$R/logs/restored-health.txt" | tr '\n' ' ')")"
    else
        bad "health: $(echo "$worse" | tr '\n' ' ')"
    fi
    scpk "$gkey" "$S/bko-holt" root@127.0.0.1:/root/bko-holt
    sshk "$gkey" '/root/bko-holt dump; rm -f /root/bko-holt' > "$R/dumps/dst.json" 2> "$R/logs/restored-dump.err" || bad "dump of the restored system"
    vm_down "$gkey"
    local grow=(); [[ $VARIANT == bigger ]] && grow=(--grow $((1 << 30)))
    [[ $VARIANT == degraded ]] && grow=(--degraded)
    if python3 "$HERE/judge.py" "${grow[@]}" "$R/dumps/src.json" "$R/dumps/dst.json" > "$R/logs/judge.txt" 2>&1; then
        ok "the dumps agree ($(tail -1 "$R/logs/judge.txt"))"
    else
        bad "the dumps differ: $(grep -c '^DIFF' "$R/logs/judge.txt") fields (logs/judge.txt)"
    fi
}

: > "$R/report.txt"
say "HoltTest1 $G, run $RUN, $(date -u +%FT%TZ), phases: ${PHASES[*]}"
ht_manifest "$RESCUER_IMG" "$BIN/bcachefs" "$BIN"/*.deb "$S/bko-holt"
# Each phase needs the one before it: stop at the first that fails.
for p in "${PHASES[@]}"; do
    ht_phase_start "$p"; "phase_$p"; ht_phase_end "$p"
    [[ $fails == 0 ]] || { say "stopped after $p"; break; }
done
ht_summary
say "RESULT: $([[ $fails == 0 ]] && echo PASS || echo "FAIL ($fails)")"
[[ $fails == 0 ]]
