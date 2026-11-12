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
#            judge.py compares the dumps.  An ostree source (kinoite44) has
#            its rollback deployment booted too, on the source before its
#            dump and on the restored disk after its dump, each boot held
#            to the source's (README "The ostree guest")
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
# after that phase (a test hook).  HT_SOURCE_PREP=FILE is a script run as
# root in the source before its dump (its overlay only), rebooted after it
# when its last line is REBOOT: prep/tumbleweed-snapper.sh.
#
# HT_HOLT_BIN=DIR takes bko-holt from DIR/bko-holt (DIR/PLATFORM/bko-holt in
# ht1-bsd.sh) instead of bin/'s: a set laid out as bin/ is, HoltTest2's
# builds (T2_HOST_ROOT/holt-bin/CODE); a run refuses when it lacks the file.
# For debugging (ht1-debug.sh drives these): HT_HOLT=FILE is the bko-holt
# to test instead of bin/'s or HT_HOLT_BIN's (either is copied as the run
# starts, and that copy serves the whole run); HT_QUICK=1 leaves out rebuild's refusals, dry
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
# each phase's time and peaks), dumps/.  The rescuer is Debian 13, or
# BackOtter's own rescue image under HT_VARIANT=img.
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
PHASES=("$@")   # the default is the variant's (below): all four, or source and rebuild
W=/home/m/backotter-work
HERE=$(dirname "$(realpath "$0")")
T=$(dirname "$HERE")   # this copy's testvm/ (vm-run, vm-mount, compare/)
[[ -r $HERE/ht1-lib.sh ]] || { echo "ht1: no ht1-lib.sh beside $0" >&2; exit 2; }
# shellcheck source=ht1-lib.sh
. "$HERE/ht1-lib.sh"
BIN=/bkoscratch/holttest1d/bin
# bko-holt: HT_HOLT's file, else HT_HOLT_BIN's set (a directory laid out as
# bin/ is, DIR/bko-holt and DIR/PLATFORM/bko-holt: HoltTest2's builds in
# T2_HOST_ROOT/holt-bin/CODE), else bin/'s.  bin/'s debs and bcachefs stay.
HOLT=${HT_HOLT:-${HT_HOLT_BIN:-$BIN}/bko-holt}
[[ ${HT_VARIANT:-} == img || -n ${HT_HOLT:-} || -z ${HT_HOLT_BIN:-} || -f $HOLT ]] || { echo "ht1: no $HOLT (HT_HOLT_BIN=$HT_HOLT_BIN)" >&2; exit 2; }
# HT_VARIANT (README's HoltTest1D plan): "bigger" rebuilds onto disks 1 GiB
# bigger than the source's (GPT's backup header must move to the new end);
# "oneshot" runs the whole rebuild as one `--through close` command, as an
# administrator would; "degraded" runs the source with its last disk
# detached (a RAID member's: its arrays degraded), so load recreates those
# arrays with "missing" slots onto one disk fewer, and the restored machine
# must boot degraded too; "img" rebuilds from BackOtter's own rescue image
# (ImgTest2's panel: below).  A variant has a directory of its own, GUEST-VARIANT.
# The fitting variants (README "The fitting variants") give load
# --fit-largest, which contracts or expands the largest partition of each
# disk: "smaller" rebuilds onto disks 1 GiB smaller than the source's and
# "fitbigger" onto disks 1 GiB bigger, both judged with judge.py --fit;
# "nearfull" fills the source's / first and "refuse" takes a guest whose
# largest partition is a stack load won't fit, and both check that plan
# refuses and writes nothing, so neither backs up or restores anything.
VARIANT=${HT_VARIANT:-}
case $VARIANT in ""|bigger|oneshot|degraded|img|smaller|fitbigger|nearfull|refuse|bs2) ;;
    *) echo "HT_VARIANT: bigger, oneshot, degraded, img, smaller, fitbigger, nearfull, refuse or bs2" >&2; exit 2 ;;
esac
# DELTA: how far the targets are from the source's disks.  FIT: load is given
# --fit-largest FIT_PCT, so it fits the layout to them.  FITONLY: the run is a
# refusal, and stops after plan.
DELTA=0 FIT="" FITONLY="" FIT_PCT=10
case $VARIANT in
    bigger)              DELTA=$((1 << 30)) ;;
    fitbigger)           DELTA=$((1 << 30)); FIT=1 ;;
    smaller)             DELTA=-$((1 << 30)); FIT=1 ;;
    nearfull|refuse)     DELTA=-$((1 << 30)); FIT=1; FITONLY=1 ;;
esac
if [[ -n $FITONLY ]]; then
    HT_DONE_PHASE=rebuild   # ht1-lib's ht_end: the run is whole after it
    [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(source rebuild)
    for p in "${PHASES[@]}"; do
        [[ $p == source || $p == rebuild ]] ||
            { echo "HT_VARIANT=$VARIANT: a refusal, so it has no $p phase (source and rebuild only)" >&2; exit 2; }
    done
else
    [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(source backup rebuild verify)
fi
GV=$G${VARIANT:+-$VARIANT}
# bs2: a BackShot2 case (testvm/backshot2/ht1-bs2.sh: its hooks, and GV the case's name).
BS2_FLAGS=() BS2_VFW=() BS2_FSJUDGE=() BS2_FSMETA=()
# shellcheck source=../backshot2/ht1-bs2.sh
[[ $VARIANT != bs2 ]] || . "$(dirname "$HERE")/backshot2/ht1-bs2.sh"
# +img: the pinned --test image (t2-host tool img pin) is the rescuer, as a
# 16 GiB throwaway overlay, its own bko-holt and rustic-bko do the load, and
# nothing is installed in it; source and backup take the pin's bko-holt and
# rustic-bko, so the dumps and the load are one build.  Its sshd is started
# over the serial console with a key made for this run (img-boot.py): R's
# img-key, never in an image, deleted at the rebuild's end and in ht_release.
IMG_PIN=${T2_HOST_ROOT:-/bkoscratch/test2}/img-pin/linux
if [[ $VARIANT == img ]]; then
    [[ -f $IMG_PIN/rescuer.img && -f $IMG_PIN/rustic-bko && -f $IMG_PIN/bko-holt ]] || { echo "ht1: +img: no pinned image in $IMG_PIN (t2-host tool img pin linux)" >&2; exit 2; }
    [[ ! -e $IMG_PIN/bins-stale ]] || { echo "ht1: +img: $IMG_PIN/bins-stale: the pin's binaries aren't the image's" >&2; exit 2; }
    [[ ${HT_KEEP:-} != 1 && ${HT_CLEAN:-1} != 0 ]] || { echo "ht1: +img: no HT_KEEP or HT_CLEAN=0 (the run's key goes at its end)" >&2; exit 2; }
    export HT_RUSTIC=$IMG_PIN/rustic-bko HT_HOLT=$IMG_PIN/bko-holt
    HOLT=$HT_HOLT IMG_PLAT=linux
fi
# The pin as this run took it: the image's and the binaries' size, mtime and
# inode, and rescuer.img.sha256's hash (what the row is keyed to).  A re-pin
# renames over them; the rebuild checks the pin is still this one, else it
# isn't the image the row would be credited to (a harness FAIL), as ht1-bsd.sh.
pin_id() {
    local f; for f in rescuer.img bko-holt rustic-bko; do echo -n "$f $(stat -L -c '%s %Y %i' "$IMG_PIN/$f" 2>/dev/null || echo none); "; done
    echo "sha256 $(head -n 1 "$IMG_PIN/rescuer.img.sha256" 2>/dev/null | cut -d' ' -f1)"
}
IMG_ID=""; [[ $VARIANT == img ]] && IMG_ID=$(pin_id)
IMG_HARNESS=""   # a harness failure's why, for the RESULT line (rows_holt: HARNESS)
PORT=${HT_PORT:-2690}   # by-hand runs: 2690-2699 (ht1-queue.sh hands out 2600-2689)
export SSH_PORT=$PORT   # vm-run's guest SSH port
RESCUER=${HT_RESCUER:-debian13}
# The rescuer: make-rescuer.sh's debian13 copy with ZFS and every mkfs, when
# there is one; else the debian13 guest itself.
RESCUER_IMG=${HT_RESCUER_IMG:-/bkoscratch/holttest1d/rescuer/rescuer.qcow2}
[[ -f $RESCUER_IMG ]] || RESCUER_IMG=$W/vms/$RESCUER/$RESCUER.qcow2
if [[ $VARIANT == img ]]; then RESCUER=img-pin RESCUER_IMG=$IMG_PIN/rescuer.img; fi
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
# (+img in RAM only: on disk R is K, where a killed run would leave the key.)
[[ $VARIANT != img || $HT_MODE == ram ]] || { echo "ht1: +img: only in the RAM disk (under cmp-run.sh --reserve, not HT_RAM=0): on disk the run's key would be in $K" >&2; exit 2; }
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
if [[ $VARIANT == img ]]; then
    # The image boots in the source's firmware mode, as a stick would.  (Its shim and
    # kernel are signed; a guest with --no-secure-boot runs it with Secure Boot off.)
    ((uefi)) && rfw=(--uefi)
    [[ " ${fw[*]} " == *" --no-secure-boot "* ]] && rfw+=(--no-secure-boot)
    rkey=$R/img-key
elif ((uefi)) && [[ -z ${HT_RESCUER_IMG:-} && -f /bkoscratch/holttest1d/rescuer/rescuer-uefi.qcow2 ]]; then
    RESCUER=ubuntu26-uefi RESCUER_IMG=/bkoscratch/holttest1d/rescuer/rescuer-uefi.qcow2 rfw=(--uefi)
    rkey=$W/vms/$RESCUER/id_ed25519
fi
[[ $VARIANT != bs2 ]] || bs2_setup
# What the load runs in the rescuer (+img: the image's own, as a user's would).
RHOLT=/mnt/share/bko-holt RARCH=/mnt/share/rustic-bko
if [[ $VARIANT == img ]]; then RHOLT=/usr/local/bin/bko-holt RARCH=/usr/local/bin/rustic-bko; fi
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
# A luks-keyfile (make-bootstrap.sh's alpine-luks) unlocks the guest at boot
# from its initramfs: it serves vm-mount and load, with nothing to type (ckey).
gpass=""; for f in console-passphrase zfs-passphrase luks-passphrase luks-keyfile; do [[ -f $W/vms/$G/$f ]] && gpass=$W/vms/$G/$f && break; done
ckey=$gpass; [[ $gpass != */luks-keyfile ]] || ckey=""
# Where the run works and what a pass keeps (ht1-lib.sh; README "Where a run works, and what it keeps").
HT_BULK=(share 'src-*.qcow2' 'dst-*.qcow2' decoy.qcow2 rescuer.qcow2 rustic-bko tmp img-key img-key.pub)
HT_ZIP=(src-meta.json dst-meta.json)
# Settings that change what a run checks, or its rescuer: its report names them.
dbg=""; for v in HT_QUICK HT_THROUGH HT_KEEP HT_FAIL_AT HT_RESCUER HT_RESCUER_IMG HT_SOURCE_PREP; do [[ -z ${!v:-} ]] || dbg+=" $v=${!v}"; done
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
ht_release() { rm -f "$R/img-key" "$R/img-key.pub"; [[ -n $HT_MOUNTED ]] && mountpoint -q "$HT_MOUNTED" && "$T/vm-umount" "$HT_MOUNTED" >/dev/null 2>&1; rmdir "$SRC_MP" "$DST_MP" 2>/dev/null; }

sshk() { local key=$1; shift; timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o ServerAliveInterval=15 root@127.0.0.1 "$@"; }
scpk() { local key=$1; shift; timeout 300 scp -q -i "$key" -P "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$@"; }
vm_up() { # IMAGE LOG [vm-run options...] [-- QEMU_ARGS...]
    local image=$1 log=$2 opts=() qargs=(); shift 2
    while [[ $# -gt 0 && $1 != -- ]]; do opts+=("$1"); shift; done
    [[ ${1:-} == -- ]] && { shift; qargs=(-- "$@"); }
    # (Without ht_begin's copies of the caller's stdout and stderr: a rescuer
    # HT_KEEP keeps up outlives the run, and its caller's ssh or pipe.)
    if [[ -n ${IMG_BOOT:-} ]]; then
        # The rescue image: img-boot.py logs its console and starts sshd with the run's key.
        setsid -f python3 -B "$HERE/img-boot.py" "$log" "$IMG_BOOT" "$R/img-key.pub" -- \
            "$T/vm-run" "${opts[@]}" "$image" "${qargs[@]}" </dev/null 2>"$log.vm-run" {HT_FD1}>&- {HT_FD2}>&-
    elif [[ ${CONSOLE_KEY:-} == */luks-passphrase ]]; then
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
# Reboot the guest over SSH and wait for its next boot (a new boot ID: its
# SSH answers for a while after `systemctl reboot`, so vm_wait can't tell).
vm_reboot() { local key=$1 id now
    id=$(TMO=20 sshk "$key" cat /proc/sys/kernel/random/boot_id) && [[ -n $id ]] || return 1
    TMO=20 sshk "$key" 'systemctl reboot' >/dev/null 2>&1
    for _ in $(seq 72); do
        now=$(TMO=8 sshk "$key" cat /proc/sys/kernel/random/boot_id 2>/dev/null) && [[ -n $now && $now != "$id" ]] && return 0
        sleep 5
    done
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
# GRUB's core image names its prefix by (lvmid/VG-UUID/LV-UUID) when
# /boot/grub is on an LV (Gentoo: grub-install re-embeds it on load).  One
# EMBED line per EFI binary on a mounted vfat file system per id it embeds,
# then GRUBLV: the VG and LV UUIDs of the LV holding /boot/grub (none: "-"),
# and its VG/LV name.
LVMID='for m in $(findmnt -rn -t vfat -o TARGET); do find "$m" -xdev -type f -iname "*.efi"; done | sort -u | while read -r f; do
    for id in $(grep -a -o "lvmid/[0-9A-Za-z-]*/[0-9A-Za-z-]*" "$f" | sort -u); do echo "EMBED $f $id"; done
done
d=$(findmnt -n -o SOURCE --target /boot/grub 2>/dev/null)
l=$(lvs --noheadings -o vg_uuid,lv_uuid,vg_name,lv_name "$d" 2>/dev/null | awk "NF == 4 {print \"lvmid/\" \$1 \"/\" \$2 \" \" \$3 \"/\" \$4}")
echo "GRUBLV ${l:--}"'
lvmid_check() {  # the restored guest's: every embedded lvmid is /boot/grub's LV
    sshk "$1" "$LVMID" > "$R/logs/restored-lvmid.txt" 2>&1
    grep -q '^EMBED ' "$R/logs/restored-lvmid.txt" || return 0
    local want got bad
    want=$(awk '/^GRUBLV / {print $2}' "$R/logs/restored-lvmid.txt")
    got=$(awk '/^EMBED / {print $3}' "$R/logs/restored-lvmid.txt" | sort -u)
    bad=$(awk -v w="$want" '/^EMBED / && $3 != w {print $2 " " $3}' "$R/logs/restored-lvmid.txt")
    if [[ -z $bad && -n $want && $want != - ]]; then
        ok "GRUB's embedded lvmid is /boot/grub's LV: $(awk '/^GRUBLV / {print $3 " " $2}' "$R/logs/restored-lvmid.txt"), in $(grep -c '^EMBED ' "$R/logs/restored-lvmid.txt") EFI binary(ies): $(awk '/^EMBED / {print $2}' "$R/logs/restored-lvmid.txt" | tr '\n' ' ')$(grep -qF -- "$want" "$R/logs/source-lvmid.txt" 2>/dev/null && echo '(the source'"'"'s UUIDs)')"
    else
        bad "GRUB's embedded lvmid: /boot/grub's LV is ${want:--}, embedded $(echo "$got" | tr '\n' ' ')(logs/restored-lvmid.txt)"
    fi
}
# The degraded variant: the arrays may be degraded (and must be, see below).
[[ $VARIANT == degraded ]] && HEALTH="DEGRADED=1; $HEALTH"
# nearfull's fill, run in the source guest: random files under /var/tmp until
# df shows under 900 MiB free on /, so the fit's data check (free against what
# the shrink takes, plus its margin) must refuse.  Each round writes what is
# free beyond 700 MiB, at most 4 GiB at a time; it stops when a round frees
# nothing, rather than looping, and prints what df says at the end.
FILL='set -e
for i in $(seq 24); do
    a=$(df -k / | awk "NR > 1 {print \$4; exit}")
    [ "$a" -le 900000 ] && break
    w=$(( (a - 700000) / 1024 ))
    [ "$w" -lt 1 ] && break
    [ "$w" -gt 4096 ] && w=4096
    dd if=/dev/urandom of=/var/tmp/ht1-fill.$i bs=1M count=$w 2>/dev/null ||
        echo "round $i stopped short of $w MiB"
    sync
done
a=$(df -k / | awk "NR > 1 {print \$4; exit}")
[ "$a" -le 1048576 ] || { echo "still $a KiB free on /: nothing left to fill it with"; exit 1; }
df -h / | tail -1'
# md's view of each array: its name, level, and member slots up or missing
# ([UU_]), sorted.  A degraded run's restored machine must show the source's.
MDSTAT='awk "/^md/ {a = \$1; for (i = 3; i <= NF; i++) if (\$i ~ /^(raid|linear)/) {l = \$i; break}} / blocks / && a {print a, l, \$NF; a = \"\"}" /proc/mdstat | sort'
# An ostree guest (kinoite44; docs/design/bazzite.md, B6) has two
# deployments, the default and the rollback, and boots each in turn: the
# source for the baseline, then the restored disk, whose two boots must show
# what the source's did.  OSTREE_STATE is what a boot shows: the booted
# deployment (CHECKSUM.SERIAL, which the kernel's ostree= leads to), the
# kernel's command line (the deployment's kargs), its /etc/bko-deployment,
# `ostree admin status`, the root's type and source (the composefs
# overlay) and SELinux's mode.
OSTREE_STATE='o=$(tr " " "\n" </proc/cmdline | sed -n "s/^ostree=//p")
echo "deployment $(basename "$(readlink -f "$o")")"
echo "cmdline $(cat /proc/cmdline)"
echo "bko-deployment $(cat /etc/bko-deployment 2>/dev/null || echo none)"
echo "root $(findmnt -no FSTYPE,SOURCE /)"
echo "selinux $(getenforce 2>/dev/null || echo none)"
ostree admin status'
# OSTREE_OTHER points GRUB at the one BLS entry whose ostree= leads to a
# deployment other than the booted one, for the next boots, and prints its
# ID.  bootupd's static grub.cfg loads grubenv but never reads next_entry,
# so grub2-reboot does nothing there; a `default` in grubenv is what its
# menu takes (an index or an entry's ID, its file name without .conf).  The
# caller unsets it again: `grub2-editenv FILE unset default` leaves grubenv
# byte for byte as it was.
OSTREE_OTHER='b=$(readlink -f "$(tr " " "\n" </proc/cmdline | sed -n "s/^ostree=//p")"); n=0; id=""
for e in /boot/loader/entries/ostree-*.conf; do
    [ -f "$e" ] || continue
    o=$(sed -n "s/^options[ \t]*//p" "$e" | tr " " "\n" | sed -n "s/^ostree=//p")
    [ -n "$o" ] && [ "$(readlink -f "$o")" != "$b" ] && { id=$(basename "$e" .conf); n=$((n + 1)); }
done
[ "$n" = 1 ] || { echo "$n BLS entries lead to a deployment other than the booted $b" >&2; exit 1; }
grub2-editenv /boot/grub2/grubenv set default="$id" && echo "$id"'
OSTREE_UNSET='grub2-editenv /boot/grub2/grubenv unset default'
# The source's baseline: its two BLS entries (B5 and B6 need the two
# deployments B1 built), each deployment booted, and the default again for
# the dump, with grubenv as it was.  Exit 1 when the run can go no further.
ostree_source() {
    local L=$R/logs ne id a r
    ne=$(sshk "$gkey" 'ls /boot/loader/entries/ostree-*.conf 2>/dev/null | wc -l')
    [[ $ne == 2 ]] || { bad "ostree: the source has ${ne:-no} BLS entries, not the default's and the rollback's"; return 1; }
    sshk "$gkey" "$OSTREE_STATE" > "$L/source-ostree-first.txt" 2>&1
    sshk "$gkey" 'sha256sum </boot/grub2/grubenv' > "$L/source-grubenv-before.txt" 2>&1
    id=$(sshk "$gkey" "$OSTREE_OTHER" 2>"$L/source-ostree-other.err") ||
        { bad "ostree: no rollback entry on the source: $(tail -1 "$L/source-ostree-other.err")"; return 1; }
    vm_reboot "$gkey" || { bad "ostree: the source's rollback entry $id boots (no SSH; logs/source-serial.log)"; return 1; }
    sshk "$gkey" "$OSTREE_STATE" > "$L/source-ostree-rollback.txt" 2>&1
    bootstate "$gkey" > "$L/source-rollback-bootstate.txt"
    sshk "$gkey" "$OSTREE_UNSET" || { bad "ostree: grub2-editenv unset default on the source"; return 1; }
    vm_reboot "$gkey" || { bad "ostree: the source boots its default entry again (no SSH; logs/source-serial.log)"; return 1; }
    sshk "$gkey" "$OSTREE_STATE" > "$L/source-ostree.txt" 2>&1
    sshk "$gkey" 'sha256sum </boot/grub2/grubenv' > "$L/source-grubenv-after.txt" 2>&1
    a=$(sed -n 's/^deployment //p' "$L/source-ostree.txt") r=$(sed -n 's/^deployment //p' "$L/source-ostree-rollback.txt")
    if [[ -z $a || -z $r || $a == "$r" ]]; then
        bad "ostree: the source's rollback boot ($id) booted ${r:-nothing}, its default ${a:-nothing}"; return 1
    fi
    cmp -s "$L/source-ostree-first.txt" "$L/source-ostree.txt" ||
        { bad "ostree: the source's default boot after its rollback isn't its first: $(diff "$L/source-ostree-first.txt" "$L/source-ostree.txt" | grep '^[<>]' | head -2 | tr '\n' ' ')"; return 1; }
    cmp -s "$L/source-grubenv-before.txt" "$L/source-grubenv-after.txt" ||
        { bad "ostree: the source's grubenv isn't as it was after the rollback boot"; return 1; }
    ok "ostree: the source booted its default $a and its rollback $r (entry $id), grubenv left as it was"
}
# The restored disk's two boots against the baseline: the default (booted
# already, and dumped), then the rollback.  Its bootstate may have no line
# the source's rollback boot lacked.
ostree_verify() {
    local L=$R/logs id
    sshk "$gkey" "$OSTREE_STATE" > "$L/restored-ostree.txt" 2>&1
    if cmp -s "$L/source-ostree.txt" "$L/restored-ostree.txt"; then
        ok "ostree: the restored default boot is the source's ($(sed -n 's/^deployment //p' "$L/restored-ostree.txt"): kargs, /etc/bko-deployment, ostree admin status, composefs root, SELinux)"
    else
        bad "ostree: the restored default boot isn't the source's: $(diff "$L/source-ostree.txt" "$L/restored-ostree.txt" | grep '^[<>]' | head -4 | tr '\n' ' ')"
    fi
    id=$(sshk "$gkey" "$OSTREE_OTHER" 2>"$L/restored-ostree-other.err") ||
        { bad "ostree: no rollback entry on the restored disk: $(tail -1 "$L/restored-ostree-other.err")"; return; }
    vm_reboot "$gkey" || { bad "ostree: the restored rollback entry $id boots (no SSH; logs/restored-serial.log)"; return; }
    sshk "$gkey" "$OSTREE_STATE" > "$L/restored-ostree-rollback.txt" 2>&1
    if cmp -s "$L/source-ostree-rollback.txt" "$L/restored-ostree-rollback.txt"; then
        ok "ostree: the restored rollback boot (entry $id) is the source's ($(sed -n 's/^deployment //p' "$L/restored-ostree-rollback.txt"): kargs, /etc/bko-deployment, ostree admin status, composefs root, SELinux)"
    else
        bad "ostree: the restored rollback boot (entry $id) isn't the source's: $(diff "$L/source-ostree-rollback.txt" "$L/restored-ostree-rollback.txt" | grep '^[<>]' | head -4 | tr '\n' ' ')"
    fi
    bootstate "$gkey" > "$L/restored-rollback-bootstate.txt"
    if diff -q <(head -1 "$L/source-rollback-bootstate.txt") <(head -1 "$L/restored-rollback-bootstate.txt") >/dev/null \
        && [[ -z $(comm -13 <(tail -n +2 "$L/source-rollback-bootstate.txt") <(tail -n +2 "$L/restored-rollback-bootstate.txt")) ]]; then
        ok "ostree: the rollback's boot state as the source's ($(head -1 "$L/restored-rollback-bootstate.txt"))"
    else
        bad "ostree: the rollback's boot state: source $(tr '\n' ' ' < "$L/source-rollback-bootstate.txt"), restored $(tr '\n' ' ' < "$L/restored-rollback-bootstate.txt")"
    fi
}

phase_source() {
    say "== source: $G"
    for ((i = 0; i < n; i++)); do
        rm -f "${src[i]}"; qemu-img create -q -f qcow2 -b "${imgs[i]}" -F qcow2 "${src[i]}"
    done
    CONSOLE_KEY=$ckey vm_up "${src[0]}" "$R/logs/source-serial.log" "${fw[@]}" "${extra_src[@]}"
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
    if [[ $VARIANT == nearfull ]]; then
        # Fill / in the overlay until under a gigabyte is free, so the fit's
        # data check must refuse: random bytes, which compress as badly as a
        # real disk's (Omarchy's btrfs has compress=zstd), in its own files
        # under /var/tmp.  Nothing of the system is deleted, and only the
        # throwaway overlay is written.
        if TMO=1800 sshk "$gkey" "$FILL" > "$R/logs/source-fill.txt" 2>&1; then
            ok "source's / filled: $(tail -1 "$R/logs/source-fill.txt")"
        else
            bad "filling the source's / (logs/source-fill.txt): $(tail -1 "$R/logs/source-fill.txt")"
            vm_down "$gkey"; return
        fi
    fi
    if [[ -n ${HT_SOURCE_PREP:-} ]]; then
        # A script of the test's, run as root in the source (its throwaway
        # overlay) before the dump: e.g. snapper snapshots and a rollback.
        # A last line REBOOT has the source rebooted after it.
        scpk "$gkey" "$HT_SOURCE_PREP" root@127.0.0.1:/root/ht-source-prep
        if TMO=1800 sshk "$gkey" 'sh /root/ht-source-prep; r=$?; rm -f /root/ht-source-prep; exit $r' > "$R/logs/source-prep.txt" 2>&1; then
            ok "source prepared by ${HT_SOURCE_PREP##*/}"
        else
            bad "preparing the source with ${HT_SOURCE_PREP##*/} (logs/source-prep.txt): $(tail -1 "$R/logs/source-prep.txt")"
            vm_down "$gkey"; return
        fi
        if [[ $(tail -1 "$R/logs/source-prep.txt") == REBOOT ]]; then
            vm_down "$gkey"
            CONSOLE_KEY=$gpass vm_up "${src[0]}" "$R/logs/source-serial-2.log" "${fw[@]}" "${extra_src[@]}"
            vm_wait "$gkey" || { bad "source boots again after ${HT_SOURCE_PREP##*/} (no SSH)"; vm_down "$gkey"; return; }
            ok "source rebooted after ${HT_SOURCE_PREP##*/}"
        fi
    fi
    # An ostree source boots its rollback and its default again first: the
    # baseline the restored disk's two boots are held to.
    rm -f "$R/logs/source-ostree"*.txt "$R/logs/source-rollback-bootstate.txt"
    case $(sshk "$gkey" 'if [ -e /run/ostree-booted ]; then echo ostree; else echo plain; fi') in
        ostree) ostree_source || { vm_down "$gkey"; return; } ;;
        plain) ;;
        *) bad "source: no answer over SSH to whether it is ostree"; vm_down "$gkey"; return ;;
    esac
    bootstate "$gkey" > "$R/logs/source-bootstate.txt"
    sshk "$gkey" "$LVMID" > "$R/logs/source-lvmid.txt" 2>&1
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
    [[ $VARIANT != bs2 ]] || bs2_after_source
}

phase_backup() {
    [[ $VARIANT != bs2 ]] || { phase_backup_bs2; return; }
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

# The fitting variants' checks, before anything is erased (README "The fitting
# variants"), with load's arguments: what the fit must come to on this guest,
# that plan refuses where it has to and for the reason it has to, and that
# every target image is byte for byte what it was (plan writes nothing, and the
# rescuer's QEMU leaves a blank qcow2 it isn't written through alone).  Exit 1
# when the run can go no further.
fit_checks() {
    local plan rc=0 holds want h1 h2
    # judge.py works the fit out from the source's layout, as load does from
    # the same numbers and as the judging will: an amount here is the one the
    # whole run is held to, and a stack load won't fit is a problem.
    plan=$(python3 "$HERE/judge.py" --fit-plan "$DELTA" --fit-pct "$FIT_PCT" "$R/dumps/src.json" 2>&1) || rc=$?
    say "the fit here, targets $DELTA bytes and --fit-largest $FIT_PCT%: ${plan//$'\n'/; }"
    # The stacks the plan names on the largest partitions, for the refusal
    # check below to grep for.  A disk with no partition to fit prints "-"
    # there, and judge.py's own stderr is in $plan too: either in the
    # alternation would match any refusal text at all ("-" every one with a
    # hyphen, an empty branch every one), so only a plain type name counts.
    holds=$(awk '!/^problem: / && $5 ~ /^[A-Za-z][A-Za-z0-9_]*$/ {print $5}' <<<"$plan" | sort -u | paste -sd'|' -)
    if [[ $VARIANT == refuse ]]; then
        grep -q '^problem: ' <<<"$plan" ||
            { bad "HT_VARIANT=refuse: $G's largest partition ($holds) is one load can fit, so load has nothing to refuse"; return 1; }
    elif ((rc != 0)); then
        bad "HT_VARIANT=$VARIANT: no fit of $G to judge: ${plan//$'\n'/; }"
        return 1
    fi
    h1=$(md5sum "${dst[@]}" | cut -d' ' -f1 | paste -sd' ' -)
    if ((DELTA < 0)); then
        # Without the flag a smaller target is refused, as it was before it.
        load_step 4 refuse-smaller "$@" --only plan &&
            { grep -qi "is smaller" "$R/logs/rebuild-refuse-smaller".{out,err} ||
                bad "load refuse-smaller: refused, but not for the target's size: $(grep -v '^$' "$R/logs/rebuild-refuse-smaller".{out,err} | tail -3 | tr '\n' ' ')"; }
        # And with a PCT too small for what the fit would take out of the
        # largest partition.  (A guest whose largest partition is 100 GiB or
        # more loses under 1% of it to a gigabyte: nothing for 1% to refuse.
        # On a refuse guest the stack is refused first, whatever the PCT.)
        if [[ $VARIANT == refuse ]]; then
            ok "skipped the 1% refusal: load refuses $G's stack ($holds) whatever the percentage"
        elif python3 "$HERE/judge.py" --fit-plan "$DELTA" --fit-pct 1 "$R/dumps/src.json" >/dev/null 2>&1; then
            ok "skipped the 1% refusal: 1% of $G's largest partition covers the fit ($holds), so load has nothing to refuse there"
        else
            load_step 4 refuse-fit-1pct "$@" --fit-largest 1% --only plan &&
                { grep -qi "1%" "$R/logs/rebuild-refuse-fit-1pct".{out,err} ||
                    bad "load refuse-fit-1pct: refused, but not for the 1% limit: $(grep -v '^$' "$R/logs/rebuild-refuse-fit-1pct".{out,err} | tail -3 | tr '\n' ' ')"; }
        fi
    fi
    if [[ -n $FITONLY ]]; then
        # What the refusal has to name: the stack load won't fit, or the data
        # that wouldn't fit in what is left.
        case $VARIANT in
            refuse)   want=$(sed 's/linux_raid_member/md|raid/g; s/zfs_member/zfs/g' <<<"$holds")
                      [[ -n $want ]] || { bad "HT_VARIANT=refuse: the plan names no stack on $G's largest partition, so there is no reason to check for"; return 1; } ;;
            nearfull) want='free|used|data' ;;
        esac
        load_step 4 refuse-fit "$@" --fit-largest "$FIT_PCT%" --only plan &&
            { grep -qiE "($want)" "$R/logs/rebuild-refuse-fit".{out,err} ||
                bad "load refuse-fit: refused, but not for $VARIANT's reason ($want): $(grep -v '^$' "$R/logs/rebuild-refuse-fit".{out,err} | tail -3 | tr '\n' ' ')"; }
    else
        load_step 0 fit-plan "$@" --fit-largest "$FIT_PCT%" --only plan
    fi
    h2=$(md5sum "${dst[@]}" | cut -d' ' -f1 | paste -sd' ' -)
    if [[ $h1 == "$h2" ]]; then
        ok "the plans left every target image's bytes as they were"
    else
        bad "a plan wrote to a target image ($h1 -> $h2)"
    fi
    return 0
}

# One bko-holt load call in the rescuer: WANT exit, NAME, then load's args.
load_step() { local want=$1 name=$2; shift 2
    sshk "$rkey" "$RHOLT load /mnt/share/layout.json $*" > "$R/logs/rebuild-$name.out" 2> "$R/logs/rebuild-$name.err"
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
    # load's --key-file needs it here too, in a run with no backup phase to
    # have written it (the refusal variants) as typed, without the newline.
    [[ -z $gpass ]] || printf '%s' "$(cat "$gpass")" > "$S/passphrase"
    chmod -R a+rX "$S"
    rm -f "$R/rescuer.qcow2" "${dst[@]}"
    if [[ $VARIANT == img ]]; then
        qemu-img create -q -f qcow2 -b "$RESCUER_IMG" -F raw "$R/rescuer.qcow2" 16G
        rm -f "$R/img-key" "$R/img-key.pub"; ssh-keygen -q -t ed25519 -N '' -C ht1-img -f "$R/img-key" || { bad "rescue image: no run key"; return; }
    else
        qemu-img create -q -f qcow2 -b "$RESCUER_IMG" -F qcow2 "$R/rescuer.qcow2"
    fi
    local disks=()
    for ((i = 0; i < n; i++)); do
        if [[ $VARIANT == bs2 ]]; then
            qemu-img create -q -f qcow2 "${dst[i]}" "$(bs2_target_size "$i")"; disks+=(--disk "${dst[i]}")
        else
            qemu-img create -q -f qcow2 "${dst[i]}" "$(($(size_of "${imgs[i]}") + DELTA))"; disks+=(--disk "${dst[i]}")
        fi
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
    IMG_BOOT=${IMG_PLAT:-} vm_up "$R/rescuer.qcow2" "$R/logs/rescuer-serial.log" "${rfw[@]}" --memory 3072 "${disks[@]}" -- \
        -virtfs "local,path=$S,mount_tag=bkoshare,security_model=none,readonly=on"
    vm_wait "$rkey" || { bad "rescuer boots"; vm_down "$rkey"; return; }
    if [[ $VARIANT == img ]]; then
        # (After QEMU opened the image: the pin unchanged now means the
        # inode it booted is the one the run started with.)
        local now; now=$(pin_id)
        if [[ $now != "$IMG_ID" ]]; then
            IMG_HARNESS="the pin changed during the run"
            bad "rescue image: $IMG_HARNESS: $IMG_PIN at the start: $IMG_ID; at the rebuild: $now"
            vm_down "$rkey"; return
        fi
        say "note: booted the pin $IMG_PIN: $IMG_ID"
    fi
    if [[ $VARIANT == img ]]; then
        # The image's own tools, or a FAIL: nothing is installed in it.
        sshk "$rkey" 'mkdir -p /mnt/share && mount -t 9p -o trans=virtio,version=9p2000.L,ro bkoshare /mnt/share && for c in mkfs.ext4 mkfs.xfs mkfs.btrfs mkfs.vfat mkfs.f2fs mkfs.nilfs2 mkfs.jfs mkfs.bcachefs zpool zfs mdadm cryptsetup lvm sgdisk bko-holt rustic-bko; do command -v $c >/dev/null || { echo "missing: $c"; exit 1; }; done; uname -r' \
            > "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescue image: share and tools ($(tail -n 1 "$R/logs/rescuer-setup.log"))"; vm_down "$rkey"; return; }
    else
    sshk "$rkey" 'mkdir -p /mnt/share && mount -t 9p -o trans=virtio,version=9p2000.L,ro bkoshare /mnt/share && inst() { apt-get install -y -q -o DPkg::Lock::Timeout=600 "$@" >/dev/null; } && { command -v mkfs.f2fs >/dev/null || inst /mnt/share/f2fs-tools_*.deb; } && { command -v mkfs.jfs >/dev/null || inst /mnt/share/jfsutils_*.deb; } && { [ ! -f /mnt/share/bcachefs ] || command -v bcachefs >/dev/null || { for d in /mnt/share/bcachefs-deps-*.deb; do [ ! -f "$d" ] || inst "$d"; done; install -m 755 /mnt/share/bcachefs /usr/local/sbin/bcachefs; }; }' \
        > "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup (share, f2fs-tools, jfsutils)"; vm_down "$rkey"; return; }
    fi
    sshk "$rkey" "mkfs.ext4 -q -F -L ht1decoy $decoy" >> "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup (the decoy's filesystem)"; vm_down "$rkey"; return; }
    ok "rescuer up, share mounted"
    # The first and last MiB of every target and the decoy, before and after
    # the refusals and the dry run, which must write nothing.
    local edges="for d in ${erase[*]#--erase} $decoy; do s=\$(blockdev --getsz \$d); echo \$d \$(dd if=\$d bs=512 count=2048 status=none | md5sum | cut -c1-32) \$(dd if=\$d bs=512 skip=\$((s - 2048)) count=2048 status=none | md5sum | cut -c1-32); done"
    local flags=()
    # (bs2: tier 1 boots with a fresh NVRAM, so --no-nvram as ever; tier 2
    # keeps one, and the case's own options say.)
    ((uefi)) && [[ $VARIANT != bs2 || $BS2_TIER == 1 ]] && flags+=(--no-nvram)
    [[ $VARIANT != bs2 ]] || flags+=("${BS2_FLAGS[@]}")
    # (A refusal variant always takes these: they are what it runs for.)
    if [[ ${HT_QUICK:-} != 1 || -n $FITONLY ]]; then
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
    # The fitting variants' own checks come before the dry run (and for
    # nearfull and refuse they are as far as the run goes), on load's
    # arguments as they are here, without --fit-largest: one of the checks is
    # that plan refuses without it.
    if [[ -n $FIT && ( ${HT_QUICK:-} != 1 || -n $FITONLY ) ]]; then
        fit_checks "${map[@]}" "${erase[@]}" "${flags[@]}" || { kept_or_down; return; }
    fi
    # From here every call carries the flag, the dry run, each stage and each
    # `status` alike: a stage run without it after a fit must not go on.
    [[ -z $FIT ]] || flags+=(--fit-largest "$FIT_PCT%")
    if [[ -n $FITONLY ]]; then
        # The refusal was the whole run: nothing may have been written.
        [[ $(sshk "$rkey" "blkid -o value -s LABEL $decoy") == ht1decoy ]] || bad "the decoy lost its filesystem"
        sshk "$rkey" "$edges" > "$R/logs/edges-after.txt" 2>&1
        if [[ -s $R/logs/edges-before.txt ]] && cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
            ok "the refusals left every target's and the decoy's first and last MiB as they were"
        else
            bad "a refusal wrote to a disk (logs/edges-before.txt, edges-after.txt)"
        fi
        sshk "$rkey" 'cat /run/bko-holt/log/load.log 2>/dev/null' > "$R/logs/rebuild-load.log"
        [[ ${HT_KEEP:-} == 1 ]] && { kept_or_down; return; }
        vm_down "$rkey"
        rm -f "$R/rescuer.qcow2"
        return
    fi
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
    local repo=(--repo /mnt/share/repo --password-file /mnt/share/repo-password --archiver "$RARCH" --snapshot "latest:$SRC_MP")
    [[ $VARIANT != bs2 ]] || repo=(--repo /mnt/share/repo --password-file /mnt/share/repo-password --archiver "$RARCH" --snapshot "$(bs2_snapshot)")
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
            sshk "$rkey" 'set -x; findmnt -R /mnt/bko-target; grep -E "zfs|/mnt/bko" /proc/mounts; zpool list -v; zfs list -o name,mounted,mountpoint,canmount; fuser -vm /mnt/bko-target 2>&1; lsblk -f; cat /proc/swaps; ls -l /dev/zvol 2>&1; dmsetup ls; cat /proc/mdstat; for f in /proc/[0-9]*/mountinfo; do grep -q bko-target $f 2>/dev/null && p=${f#/proc/} && p=${p%/mountinfo} && echo "mount namespace with bko-target: pid $p $(cat /proc/$p/comm) $(readlink /proc/$p/ns/mnt)"; done; findmnt -t nsfs; for b in $(findmnt -n -t nsfs -o TARGET); do nsenter --mount=$b cat /proc/self/mountinfo 2>/dev/null | grep bko-target | sed "s|^|mount namespace bound at $b: |"; done; readlink /proc/self/ns/mnt' > "$R/logs/rebuild-failed-state.txt" 2>&1
            kept_or_down; return; }
        sshk "$rkey" "$RHOLT load /mnt/share/layout.json ${map[*]} --only status" > "$R/logs/rebuild-status-after-$s.txt" 2>&1
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
    rm -f "$R/rescuer.qcow2" "$R/img-key" "$R/img-key.pub"
}
# HT_KEEP=1: the rescuer stays up, as the stage left it; else it goes down.
kept_or_down() {
    if [[ ${HT_KEEP:-} == 1 ]]; then
        say "rescuer kept up: ssh -i $rkey -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 (load: $RHOLT load /mnt/share/layout.json ...)"
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
        python3 "$T/compare/fsmeta.py" record "$DST_MP" "$R/dst-meta.json" "${BS2_FSMETA[@]}" > "$R/logs/fsmeta-dst.log" 2>&1
        [[ $VARIANT != bs2 ]] || bs2_after_dst_record
        (trap '' TERM INT HUP; exec "$T/vm-umount" "$DST_MP") >> "$R/logs/verify-mount.log" 2>&1; HT_MOUNTED=
        python3 "$T/compare/fsmeta.py" compare "$R/src-meta.json" "$R/dst-meta.json" > "$R/logs/fsmeta-compare.txt" 2>&1
        if python3 "$HERE/judge.py" fs "$T/compare/fsmeta.py" "$R/src-meta.json" "$R/dst-meta.json" "${BS2_FSJUDGE[@]}" > "$R/logs/fsjudge.txt" 2>&1; then
            ok "fsmeta: the files agree but for the boot stage's ($(tail -1 "$R/logs/fsjudge.txt"))"
        else
            bad "fsmeta: $(grep '^DIFF' "$R/logs/fsjudge.txt" | head -5 | tr '\n' ' ')"
        fi
    else
        bad "mount the restored disk"
    fi
    CONSOLE_KEY=$ckey vm_up "${dst[0]}" "$R/logs/restored-serial.log" --snapshot "${fw[@]}" "${BS2_VFW[@]}" "${extra_dst[@]}"
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
    lvmid_check "$gkey"
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
    # An ostree source's baseline (its source phase): the rollback boots too,
    # after the dump, which is of the default boot as the source's was.
    [[ ! -f $R/logs/source-ostree-rollback.txt ]] || ostree_verify
    vm_down "$gkey"
    local how=(); [[ $VARIANT == bigger ]] && how=(--grow $((1 << 30)))
    [[ $VARIANT == degraded ]] && how=(--degraded)
    # The fitting variants: judge.py works the new geometry out as load did,
    # and lets nothing else differ.
    [[ -z $FIT ]] || how=(--fit "$DELTA" --fit-pct "$FIT_PCT")
    # bs2: the restored dump against the drifted layout the case asked for.
    local jsrc=$R/dumps/src.json
    [[ $VARIANT != bs2 ]] || { how=("${BS2_JUDGE[@]}"); jsrc=$R/dumps/drifted.json; }
    if python3 "$HERE/judge.py" "${how[@]}" "$jsrc" "$R/dumps/dst.json" > "$R/logs/judge.txt" 2>&1; then
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
say "RESULT: $([[ $fails == 0 ]] && echo PASS || echo "FAIL ($fails${IMG_HARNESS:+; harness: $IMG_HARNESS})")"
[[ $fails == 0 ]]
