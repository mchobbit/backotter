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
# Needs /bkoscratch/holttest1d/bin/ with f2fs-tools_*.deb and
# jfsutils_*.deb (installed in a rescuer that lacks them), and bcachefs
# with bcachefs-deps-*.deb (make-bcachefs-tools.sh: Debian 13 has no
# bcachefs-tools; installed only for a layout with a bcachefs, and of the
# debs only those the rescuer has no version of), and
# /bkoscratch/holttest1d/rescuer/rescuer.qcow2 (make-rescuer.sh): a run
# that would use the debs or the rescuer refuses to start without them
# (exit 2).  rustic-bko is the shipped set's
# (~/backotter-work/bko-bin/linux/, checked against its SHA256SUMS), and
# bko-holt the promoted one's (~/backotter-work/holt-bin/linux/bko-holt,
# checked against holt-bin's SHA256SUMS; missing or unmatched, exit 2).
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
#            to the source's (README "The ostree guest"); so does an ABRoot
#            source (vanilla3) its other root, GRUB's other entry picked on
#            the serial console (README "The ABRoot guest").  A Proxmox
#            source's ESPs (proxmox-boot-tool's, mounted by nothing) are
#            recorded on their own and judged by content (judge.py esp);
#            with one on each disk (proxmox9-mirror), each restored disk
#            boots alone too, the others detached (the spec's
#            --boot-each-disk: HT_BOOT_EACH_DISK=1 asks for it on any
#            source of more than one disk, =0 never).  A ZFSBootMenu source
#            (its dump's boot.zfsbootmenu: void-zbm, void-zbm-enc) has each
#            of its other boot environments booted too, picked from ZBM's
#            serial menu, on the source after its dump and on the restored
#            disk after its dump, each held to the source's (zbm_source,
#            zbm_verify), and judge.py zbm holds its images, BEs and pool
#            properties (docs/design/zfsbootmenu.md, "HoltTest1 1V")
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
# HT_RUSTIC=FILE a file, unchecked.  HT_NVRAM=1 runs a UEFI load without
# --no-nvram, so it writes the rescuer's own (throwaway) NVRAM, which is
# recorded after the load (logs/rescuer-nvram.txt): for debugging.
# HT_FAIL_AT=PHASE adds a FAIL line
# after that phase (a test hook).  HT_SOURCE_PREP=FILE is a script run as
# root in the source before its dump (its overlay only), rebooted after it
# when its last line is REBOOT: prep/tumbleweed-snapper.sh (the default for
# leap16*); or, when its last
# line is AGAIN, rebooted, waited for until a boot settles (running or
# degraded, and no reboot of its own), and run again, up to 10 rounds: a
# prep of several boots, which keeps its own place (prep/microos-tu.sh,
# the default for microos* and aeon*; HT_SOURCE_PREP= runs none).  A
# transactional-update source (/ read-only, tukit) is checked by ht1-tu.sh.
# HT_REFORMAT="MOUNT=TYPE ..." gives every load call from the dry run on a
# --reformat MOUNT=TYPE for each (docs/design/reformat.md), and judge.py's
# fs and dump forms the same --reformats, which hold the restored disk to
# what the change predicts; the source's btrfs read-only snapshots are then
# left out by design, so one missing after the restore isn't a FAIL.
#
# HT_HOLT_BIN=DIR takes bko-holt from DIR/bko-holt, else DIR/linux/bko-holt
# (DIR/PLATFORM/bko-holt in ht1-bsd.sh), instead of holt-bin's: HoltTest2's
# builds (T2_HOST_ROOT/holt-bin/CODE) or a set laid out as holt-bin is; a
# run refuses when it lacks the file (the ledger row holds a CODE set's to
# its SHA256SUMS).
# HT_VARIANT=bs2 (BackShot2's cases) may take the source's preparation for
# the source phase and the backup, and resume the rescuer from a saved state
# and quit it once synced (testvm/backshot2/ht1-bs2.sh; BS2_REUSE_SOURCE,
# BS2_REUSE_BACKUP, BS2_RESCUER_STATE, BS2_FAST_DOWN =0 turn each off).
#
# For debugging (ht1-debug.sh drives these): HT_HOLT=FILE is the bko-holt
# to test instead of holt-bin's or HT_HOLT_BIN's, unchecked (the old
# /bkoscratch/holttest1d/bin/bko-holt is used only this way; whichever it is
# is copied as the run starts, and that copy serves the whole run);
# HT_QUICK=1 leaves out rebuild's refusals, dry
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
    freebsd*|hardenedbsd*|ghostbsd*|netbsd*|alpinebsd*|openbsd*|dragonfly*|omnios*|oi*|haiku*)
        exec "$(dirname "$(realpath "$0")")/ht1-bsd.sh" "$G" "$RUN" "$@" ;;
    # Systems that can't run bko-holt: read off their disks from Linux.
    # (Qubes OS: dom0 has no network, so no SSH: docs/design/qubes.md.)
    redox|sculpt*|qubes*)
        exec "$(dirname "$(realpath "$0")")/ht1-offline.sh" "$G" "$RUN" "$@" ;;
esac
PHASES=("$@")   # the default is the variant's (below): all four, or source and rebuild
W=/home/m/backotter-work
HERE=$(dirname "$(realpath "$0")")
T=$(dirname "$HERE")   # this copy's testvm/ (vm-run, vm-mount, compare/)
[[ -r $HERE/ht1-lib.sh ]] || { echo "ht1: no ht1-lib.sh beside $0" >&2; exit 2; }
# shellcheck source=ht1-lib.sh
. "$HERE/ht1-lib.sh"
# BIN: the debs and bcachefs.  bko-holt: HT_HOLT's file, else HT_HOLT_BIN's
# set, else the promoted holt-bin's linux/bko-holt, vouched for by its
# SHA256SUMS (ht_holt_pick); +img takes the pin's (below).
BIN=/bkoscratch/holttest1d/bin
# GARCH: the guest's architecture (hosts.py's guest_arch: *-arm64 is arm64,
# every other amd64), and PLAT its binaries' platform: linux-arm64's for an
# arm64 guest (bko-holt and rustic-bko in PLAT/ of their sets, the +img pin in
# img-pin/linux-arm64; docs/design/arm64.md A2, A6).  A guest runs only on a
# host of its own architecture: an arm64 one on an aarch64 host (Bigfoot).
GARCH=amd64 PLAT=linux
[[ $G != *-arm64 ]] || GARCH=arm64 PLAT=linux-arm64
if [[ $GARCH == arm64 && $(uname -m) != aarch64 || $GARCH == amd64 && $(uname -m) == aarch64 ]]; then
    echo "ht1: $G is an $GARCH guest, and $(hostname -s) is $(uname -m): run it on a host of its architecture" >&2; exit 2
fi
HOLT=""; [[ ${HT_VARIANT:-} == img ]] || ht_holt_pick "$PLAT"
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
# t2-only: reformat-*
# "reformat-TYPE" (HoltTest2's GUEST+reformat-TYPE cells) is a plain run with
# HT_REFORMAT="/=TYPE" (HT_REFORMAT's above; one set too must say just that),
# in a directory of its own, GUEST-reformat-TYPE; judge.py gets the
# --reformat as under HT_REFORMAT.  (A t2-only block: only those cells key
# these lines, testvm/test2/t2lib/keys.py only_text, so it must do nothing in
# any other run.)
RF_VARIANT=""
if [[ $VARIANT == reformat-* ]]; then
    [[ $VARIANT =~ ^reformat-[a-z0-9]+$ ]] || { echo "HT_VARIANT=reformat-TYPE, TYPE a filesystem type: not $VARIANT" >&2; exit 2; }
    [[ -z ${HT_REFORMAT:-} || $HT_REFORMAT == "/=${VARIANT#reformat-}" ]] ||
        { echo "HT_VARIANT=$VARIANT is HT_REFORMAT=/=${VARIANT#reformat-}, not HT_REFORMAT=$HT_REFORMAT" >&2; exit 2; }
    export HT_REFORMAT=/=${VARIANT#reformat-}
    RF_VARIANT=$VARIANT VARIANT=""
fi
# t2-only-end
case $VARIANT in ""|bigger|oneshot|degraded|img|smaller|fitbigger|nearfull|refuse|bs2) ;;
    *) echo "HT_VARIANT: bigger, oneshot, degraded, img, smaller, fitbigger, nearfull, refuse or bs2" >&2; exit 2 ;;
esac
# HT_REFORMAT: load's --reformat, one per MOUNT=TYPE.
REFORMAT=()
for f in ${HT_REFORMAT:-}; do
    [[ $f == /*=* ]] || { echo "HT_REFORMAT: MOUNT=TYPE ..., not $f" >&2; exit 2; }
    REFORMAT+=(--reformat "$f")
done
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
# t2-only: reformat-*
[[ -z $RF_VARIANT ]] || GV=$G-$RF_VARIANT
# t2-only-end
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
IMG_PIN=${T2_HOST_ROOT:-/bkoscratch/test2}/img-pin/$PLAT
if [[ $VARIANT == img ]]; then
    [[ -f $IMG_PIN/rescuer.img && -f $IMG_PIN/rustic-bko && -f $IMG_PIN/bko-holt ]] || { echo "ht1: +img: no pinned image in $IMG_PIN (t2-host tool img pin $PLAT)" >&2; exit 2; }
    [[ ! -e $IMG_PIN/bins-stale ]] || { echo "ht1: +img: $IMG_PIN/bins-stale: the pin's binaries aren't the image's" >&2; exit 2; }
    [[ ${HT_KEEP:-} != 1 && ${HT_CLEAN:-1} != 0 ]] || { echo "ht1: +img: no HT_KEEP or HT_CLEAN=0 (the run's key goes at its end)" >&2; exit 2; }
    export HT_RUSTIC=$IMG_PIN/rustic-bko HT_HOLT=$IMG_PIN/bko-holt
    HOLT=$HT_HOLT IMG_PLAT=$PLAT
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
IMG_HARNESS=""   # the pin's change, a harness failure's why for the RESULT line (rows_holt: HARNESS)
PORT=${HT_PORT:-2690}   # by-hand runs: 2690-2699 (ht1-queue.sh hands out 2600-2689)
export SSH_PORT=$PORT   # vm-run's guest SSH port
RESCUER=${HT_RESCUER:-debian13}
# The rescuer: make-rescuer.sh's debian13 copy with ZFS and every mkfs, or
# HT_RESCUER_IMG's image (the debian13 guest's own, say).  A rebuild without
# it refuses to start: never a quiet fallback to a rescuer with no ZFS or
# extra mkfs, and another rescuer than the other hosts'.  So do a backup and
# a rebuild without bin/'s f2fs-tools and jfsutils debs (the share's, for a
# rescuer that lacks them).  +img takes neither.
RESCUER_IMG=${HT_RESCUER_IMG:-/bkoscratch/holttest1d/rescuer/rescuer.qcow2}
# An arm64 guest's rescuer: make-rescuer.sh --arch arm64's copy of
# ubuntu26-arm64 (UEFI, as every arm64 guest is; Ubuntu's OpenZFS 2.4 and
# every mkfs installed in it), with no debs: bin/'s are amd64's.
[[ $GARCH != arm64 ]] || RESCUER=${HT_RESCUER:-ubuntu26-arm64} RESCUER_IMG=${HT_RESCUER_IMG:-/bkoscratch/holttest1d/rescuer/rescuer-arm64.qcow2}
if [[ $VARIANT == img ]]; then RESCUER=img-pin RESCUER_IMG=$IMG_PIN/rescuer.img
else
    if [[ " ${PHASES[*]} " == *" rebuild "* && ! -f $RESCUER_IMG ]]; then
        echo "ht1: no rescuer $RESCUER_IMG on $(hostname -s)${HT_RESCUER_IMG:+ (HT_RESCUER_IMG)}: copy it and its .sha256 from a host" \
             "that has it, or make it (make-rescuer.sh); HT_RESCUER_IMG=$W/vms/$RESCUER/$RESCUER.qcow2 takes the plain guest" >&2
        exit 2
    fi
    if [[ $GARCH == amd64 ]] && [[ " ${PHASES[*]} " == *" backup "* || " ${PHASES[*]} " == *" rebuild "* ]]; then
        for p in f2fs-tools jfsutils; do
            compgen -G "$BIN/${p}_*.deb" >/dev/null ||
                { echo "ht1: no $BIN/${p}_*.deb on $(hostname -s): copy bin/ from a host that has it" >&2; exit 2; }
        done
    fi
fi
SRC_MP=/mnt/ht1-src-$GV
DST_MP=/mnt/ht1-dst-$GV
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

gkey=$W/vms/$G/id_ed25519
rkey=$W/vms/$RESCUER/id_ed25519
# The guest's disks (vm-run-options' --disk, after its own image) and
# firmware options (--uefi and the like, for the source and restored boots).
# Its --guest-vars store (GVFROM) is the source's alone (gvars).  Its
# --guest-tpm state (GTPM: aeon's aeon.tpm, which its disk's key is sealed
# to) too: the source boots on a copy of it, the restored disk on a new
# machine's TPM (gvars; docs/design/aeon.md, A4).
imgs=("$W/vms/$G/$G.qcow2") fw=() GVFROM="" GTPM=""
if [[ -f $W/vms/$G/vm-run-options ]]; then
    read -ra o < "$W/vms/$G/vm-run-options"
    for ((i = 0; i < ${#o[@]}; i++)); do
        case ${o[i]} in
        --disk) d=${o[++i]}; [[ $d == /* ]] || d=$W/vms/$G/$d; imgs+=("$d") ;;
        --guest-vars) d=${o[++i]}; [[ $d == /* ]] || d=$W/vms/$G/$d; GVFROM=$d ;;
        --guest-tpm) d=${o[++i]}; [[ $d == /* ]] || d=$W/vms/$G/$d; GTPM=$d ;;
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
elif [[ $GARCH == arm64 ]]; then
    rfw=(--uefi)   # (on an aarch64 host UEFI is always on: --uefi is Secure Boot, as ubuntu26-arm64's own)
    rkey=$W/vms/$RESCUER/id_ed25519
elif ((uefi)) && [[ -z ${HT_RESCUER_IMG:-} && -f /bkoscratch/holttest1d/rescuer/rescuer-uefi.qcow2 ]]; then
    RESCUER=ubuntu26-uefi RESCUER_IMG=/bkoscratch/holttest1d/rescuer/rescuer-uefi.qcow2 rfw=(--uefi)
    rkey=$W/vms/$RESCUER/id_ed25519
fi
[[ $VARIANT != bs2 ]] || bs2_setup
# A guest with an NVRAM of its own (uefi-vars.fd beside its disk: its
# install's, with its boot loader's entry, e.g. cachyos-zfs's Limine) boots
# the source and the restored disk each on a fresh copy of it, as the machine
# it was installed on: through that entry, as the Thinkpad boots, not the
# ESP's removable path.  With Secure Boot off only (the store is
# OVMF_CODE_4M.fd's), and not for BackShot2, whose NVRAM is its own (tier 2's
# shared store, judge.py's --nvram-kept and --new-uuids); otherwise each boot
# has a fresh store, as ever.  GV_ENTRY: the store has an entry on a disk.
GVARS=${GVFROM:-$W/vms/$G/uefi-vars.fd} GV_ENTRY=0
((uefi)) && [[ $VARIANT != bs2 && -f $GVARS && " ${fw[*]} " == *" --no-secure-boot "* ]] || GVARS=""
[[ -n $GVARS ]] && command -v virt-fw-vars >/dev/null &&
    virt-fw-vars -i "$GVARS" --print 2>/dev/null | grep -qE '^Boot[0-9A-F]{4} .*devpath=Partition\(' && GV_ENTRY=1
# NAME: VFW, vm-run's options for a boot on R/NAME-vars.fd, made a fresh copy of it (none without).
# Without GVARS (BackShot2), the source alone boots on a copy of the store
# vm-run-options names (--guest-vars: void-zbm, which has no EFI/BOOT
# fallback), and a restored disk on the NVRAM its run gives it, never on that.
# With GTPM, VFW also has the boot's TPM (vm-run --tpm, which wins over
# --guest-tpm): every source boot R/source.tpm, a copy of the guest's made
# by phase_source, so what the source's boots seal (sdbootutil's pcrlock
# policy for a new slot's entry) carries over from one to the next; every
# restored boot R/restored.tpm, a new machine's, made empty by phase_verify.
gvars() {
    VFW=()
    if [[ -n $GTPM ]]; then if [[ $1 == source* ]]; then VFW=(--tpm "$R/source.tpm"); else VFW=(--tpm "$R/restored.tpm"); fi; fi
    if [[ -z $GVARS ]]; then [[ $1 != source || -z $GVFROM ]] || VFW+=(--guest-vars "$GVFROM"); return 0; fi
    cp "$GVARS" "$R/$1-vars.fd" && VFW+=(--uefi-vars "$R/$1-vars.fd")
}
# The entry the guest booted through: its efibootmgr -v line (HD(...): on a disk).
# Without efibootmgr (Guix System), nvboot-efivars.sh reads it from efivarfs.
NVBOOT="command -v efibootmgr >/dev/null || {
$(<"$HERE/nvboot-efivars.sh")
}
"'c=$(efibootmgr | sed -n "s/^BootCurrent: *//p"); efibootmgr -v | grep -E "^Boot$c\\*? " || echo "BootCurrent ${c:-none}, no such entry"'
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
# HT_POOLS=NAME[,NAME]: load's --pools, so only those ZFS pools are restored
# (TrueNAS's boot-pool: the spec's Decision 6).  A disk that holds another
# pool and none of them (TrueNAS's data pool's) is no target: the rescuer
# and every restored boot get it as the backup read it, dst-I.qcow2 an
# overlay of src-I.qcow2 (the guest's own image is never written), attached
# unchanged and never mapped or erased, as a machine whose boot disks are
# replaced keeps its data disks.  pools_keep sets KEEP[I]=1 for each, from
# the source's dump.
KEEP=()
pools_keep() {
    KEEP=()
    [[ -n ${HT_POOLS:-} ]] || return 0
    local i
    for i in $(python3 -I - "$R/dumps/src.json" "$HT_POOLS" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
want = set(sys.argv[2].split(","))
for disk in (d.get("storage") or {}).get("disks") or []:
    name = str(disk.get("name") or "")
    cs = [disk.get("content") or {}] + [p.get("content") or {} for p in disk.get("partitions") or []]
    pools = {c.get("label") or "" for c in cs if c.get("type") == "zfs_member"}
    if len(name) == 3 and name.startswith("vd") and pools and not pools & want:
        print(ord(name[2]) - ord("a"))
PY
    ); do KEEP[i]=1; done
}
# A LUKS or ZFS passphrase the guest's disks need (vm-mount --key, and load's
# --key-file for the volumes it makes).
# A luks-keyfile (make-bootstrap.sh's alpine-luks) unlocks the guest at boot
# from its initramfs: it serves vm-mount and load, with nothing to type (ckey).
# A recovery-key (aeon: tik's, which its kept LUKS2 header still has) serves
# vm-mount and load too, and is typed at the restored disk's prompts: its
# TPM2 token doesn't unseal on a new machine's TPM (gvars).
gpass=""; for f in console-passphrase zfs-passphrase luks-passphrase luks-keyfile recovery-key; do [[ -f $W/vms/$G/$f ]] && gpass=$W/vms/$G/$f && break; done
ckey=$gpass; [[ $gpass != */luks-keyfile ]] || ckey=""
# Where the run works and what a pass keeps (ht1-lib.sh; README "Where a run works, and what it keeps").
HT_BULK=(share 'src-*.qcow2' 'dst-*.qcow2' decoy.qcow2 rescuer.qcow2 rustic-bko tmp img-key img-key.pub source-vars.fd restored-vars.fd 'restored-disk*-vars.fd')
HT_BULK+=(source.tpm restored.tpm)   # (a --guest-tpm guest's: gvars)
HT_BULK+=('source-zbm-*-vars.fd' 'restored-zbm-*-vars.fd')   # (ZFSBootMenu's menu boots: zbm_menu_boot)
[[ $VARIANT != bs2 ]] || HT_BULK+=(rescuer-vars.fd)   # (a resumed UEFI rescuer's NVRAM)
HT_ZIP=(src-meta.json dst-meta.json)
# Settings that change what a run checks, or its rescuer: its report names them.
dbg=""; for v in HT_QUICK HT_THROUGH HT_KEEP HT_FAIL_AT HT_RESCUER HT_RESCUER_IMG HT_SOURCE_PREP HT_BOOT_EACH_DISK HT_NVRAM HT_REFORMAT HT_POOLS; do [[ -z ${!v:-} ]] || dbg+=" $v=${!v}"; done
[[ -z $dbg ]] || ht_note "set for debugging or tests:$dbg"
# A transactional-update guest's own history of slots (docs/design/aeon.md, M5).
case $G in microos*|aeon*) [[ -n ${HT_SOURCE_PREP+set} ]] || HT_SOURCE_PREP=$HERE/prep/microos-tu.sh ;; esac
# openSUSE Leap 16: snapper's chain, the old root and a rolled-back default
# restored as on Tumbleweed (docs/design/leap16.md, L5).
case $G in leap16*) [[ -n ${HT_SOURCE_PREP+set} ]] || HT_SOURCE_PREP=$HERE/prep/tumbleweed-snapper.sh ;; esac
# shellcheck source=ht1-tu.sh
. "$HERE/ht1-tu.sh"
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
ht_rustic_take "$PLAT"
# bko-holt as the run starts (bin/'s may be replaced while it runs): the
# dumps and the load are one build's.
cp --remove-destination "$HOLT" "$S/bko-holt"
HT_MOUNTED=""
ht_release() { rm -f "$R/img-key" "$R/img-key.pub"; [[ -n $HT_MOUNTED ]] && mountpoint -q "$HT_MOUNTED" && "$T/vm-umount" "$HT_MOUNTED" >/dev/null 2>&1; rmdir "$SRC_MP" "$DST_MP" 2>/dev/null; }

# ConnectTimeout is SSH_CTO (ht1-lib.sh, 30 s: a rescuer's sshd busy after a
# big write can take longer than 5 s to answer); vm_wait's polls keep their
# own TMO=8, so a guest not up yet costs no more.
sshk() { local key=$1; shift; timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=$SSH_CTO -o ServerAliveInterval=15 root@127.0.0.1 "$@"; }
scpk() { local key=$1; shift; timeout 300 scp -q -i "$key" -P "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$@"; }
# edges_read KEY CMD FILE WANT: the disks' first and last MiB (CMD, which only
# reads) into FILE, WANT lines, one a disk.  sshk waits SSH_CTO seconds for the
# sshd to answer, and on a loaded host a slow one answers nothing at all, so an
# empty or short capture is not a disk that changed: try again a few times.
# (WANT or more lines: a platform that prints a warning of its own still counts).
# 1: it still couldn't be read, which is the harness's problem, not a write.
edges_read() {
    local key=$1 cmd=$2 file=$3 want=$4 i
    for i in 1 2 3; do
        [[ $i == 1 ]] || sleep 3
        sshk "$key" "$cmd" > "$file" 2>&1 && [[ $(grep -c . "$file") -ge "$want" ]] && return 0
    done
    return 1
}
VDB_WHY=""
# vdb_blank KEY: whether the dry run left /dev/vdb unwritten, by wipefs -n.
# 0 no signature (nothing written), 1 a signature (something was), 2 nothing
# to go by: no wipefs on the rescuer, wipefs itself failed, or the sshd didn't
# answer three tries (VDB_WHY says which).  An unanswered call is not a PASS.
# (wipefs's stdout only, where it lists a signature: a warning on stderr, the
# rescuer's shell's own, say, goes to logs/vdb-blank.err and isn't a write.)
vdb_blank() {
    local key=$1 i out rc
    for i in 1 2 3; do
        [[ $i == 1 ]] || sleep 3
        out=$(sshk "$key" 'command -v wipefs >/dev/null || { echo "no wipefs on the rescuer"; exit 9; }
            wipefs -n /dev/vdb || { echo "wipefs -n failed"; exit 9; }' 2>"$R/logs/vdb-blank.err"); rc=$?
        if ((rc == 0)); then [[ -z ${out//[[:space:]]/} ]]; return $?; fi
        if ((rc == 9)); then VDB_WHY="$(tr '\n' ' ' <<<"$out")$(tail -n 1 "$R/logs/vdb-blank.err")"; return 2; fi
        VDB_WHY="the rescuer didn't answer (exit $rc)"
    done
    return 2
}
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
    elif [[ -n ${GRUB_PICK:-} ]]; then
        # A GRUB menu entry picked on the serial console (an ABRoot guest's
        # other root: its master grub.cfg reads `default` literally, so
        # grub-reboot can't): console-keys.py logs the console and types
        # GRUB_KEYS once GRUB_PICK (a regular expression) shows.  With
        # CONSOLE_KEY (a serial console's: luks-passphrase is above) it types
        # that at each prompt too, before or after GRUB_PICK, and sends
        # GRUB_KEYS' first chunk at GRUB_PICK, the rest once the menu is
        # drawn (ZFSBootMenu asks for its passphrase after Escape: zbm_menu_boot).
        setsid -f python3 "$T/vanilla/console-keys.py" ${CONSOLE_KEY:+--passphrase "$CONSOLE_KEY"} "$log" "$GRUB_PICK" "$GRUB_KEYS" -- \
            "$T/vm-run" "${opts[@]}" "$image" "${qargs[@]}" </dev/null 2>"$log.vm-run" {HT_FD1}>&- {HT_FD2}>&-
    elif [[ -n ${GRUB_MENU:-} ]]; then
        # GRUB's serial menu walked by grub-menu.py, its steps GRUB_MENU's
        # lines (a transactional guest's older slots: ht1-tu.sh).
        local steps=(); mapfile -t steps <<<"$GRUB_MENU"
        # (Its stdout nowhere: a caller's $(...) would wait for it.)
        setsid -f python3 "$HERE/grub-menu.py" "$log" "${steps[@]}" -- \
            "$T/vm-run" "${opts[@]}" "$image" "${qargs[@]}" </dev/null >/dev/null 2>"$log.vm-run" {HT_FD1}>&- {HT_FD2}>&-
    elif [[ -n ${CONSOLE_KEY:-} ]]; then
        # The guest asks for its disk passphrase on the serial console:
        # console-boot.py logs the console and types it at each prompt.
        setsid -f python3 "$T/console-boot.py" "$log" "$CONSOLE_KEY" -- \
            "$T/vm-run" "${opts[@]}" "$image" "${qargs[@]}" </dev/null 2>"$log.vm-run" {HT_FD1}>&- {HT_FD2}>&-
    else
        setsid -f "$T/vm-run" --serial-log "$log" "${opts[@]}" "$image" "${qargs[@]}" </dev/null >"$log.vm-run" 2>&1 {HT_FD1}>&- {HT_FD2}>&-
    fi
}
# (The waits below ask every second, for at least as long as they gave when
# they asked every 5 s: a guest up in 11 s was seen at 15 then, about 2.5 s
# lost a boot, and as much a shutdown.)
vm_wait() { local key=$1 end=$((SECONDS + 600))
    while ((SECONDS < end)); do TMO=8 sshk "$key" true 2>/dev/null && return 0; sleep 1; done
    return 1
}
# Reboot the guest over SSH and wait for its next boot (a new boot ID: its
# SSH answers for a while after `systemctl reboot`, so vm_wait can't tell).
vm_reboot() { local key=$1 id now
    id=$(TMO=20 sshk "$key" cat /proc/sys/kernel/random/boot_id) && [[ -n $id ]] || return 1
    # Guix System (Shepherd) has no systemctl: its own reboot.
    TMO=20 sshk "$key" 'if command -v systemctl >/dev/null; then systemctl reboot; else reboot; fi' >/dev/null 2>&1
    local end=$((SECONDS + 600))
    while ((SECONDS < end)); do
        now=$(TMO=8 sshk "$key" cat /proc/sys/kernel/random/boot_id 2>/dev/null) && [[ -n $now && $now != "$id" ]] && return 0
        sleep 1
    done
    return 1
}
# This run's VM only (ht_qemus: a QEMU with its disks in R that this run
# started, ht_own), never another run's that took the port while none of
# this run's was up, nor one started by hand on R's (K's) disks.
vm_down() { local key=$1
    [[ -n $(ht_qemus "$R") ]] || return 0
    # Guix System has no poweroff: Shepherd's halt powers off.
    TMO=20 sshk "$key" 'if command -v poweroff >/dev/null; then poweroff; else halt; fi' >/dev/null 2>&1
    for _ in $(seq 180); do [[ -n $(ht_qemus "$R") ]] || return 0; sleep 1; done
    # shellcheck disable=SC2046  # PIDs
    kill $(ht_qemus "$R") 2>/dev/null; sleep 5
    # shellcheck disable=SC2046
    kill -9 $(ht_qemus "$R") 2>/dev/null; sleep 1
}
# The restored machine's health (README step 4): one line per check, FAIL
# lines for those that fail, exit 1 if any did.  A check whose tool is
# missing never ran: "MISSING-TOOL name", which the source can't excuse.
HEALTH='f=0; no() { echo "FAIL $*"; f=1; }; gone() { echo "MISSING-TOOL $1"; f=1; }
if command -v findmnt >/dev/null; then findmnt --verify >/dev/null 2>&1 && echo "fstab verifies" || no "findmnt --verify: $(findmnt --verify 2>&1 | grep -iE "error|warn" | head -2 | tr "\n" " ")"; else gone findmnt; fi
if command -v mountpoint >/dev/null; then m=0; for t in $(awk "\$1 !~ /^#/ && \$2 ~ /^\// && \$4 !~ /noauto/ {print \$2}" /etc/fstab); do mountpoint -q "$t" && m=$((m + 1)) || no "fstab mount $t not mounted"; done; echo "$m fstab mounts mounted"; else gone mountpoint; fi
[ "$(grep -c . /proc/swaps)" -gt 1 ] && echo "swap on" || no "no swap"
if command -v zpool >/dev/null && [ -n "$(zpool list -H -o name 2>/dev/null)" ]; then zpool status -x | grep -q "all pools are healthy" && echo "zpool healthy" || no "zpool status -x: $(zpool status -x | head -3 | tr "\n" " ")"; elif awk "\$3 == \"zfs\"" /proc/mounts | grep -q .; then command -v zpool >/dev/null && no "ZFS mounted, zpool lists no pool" || gone zpool; fi
[ -r /proc/mdstat ] && grep -q "^md" /proc/mdstat && ! command -v mdadm >/dev/null && gone mdadm
[ -r /proc/mdstat ] && command -v mdadm >/dev/null && for a in $(awk "/^md/ {print \$1}" /proc/mdstat); do st=$(mdadm --detail /dev/$a | sed -n "s/^ *State : *//p" | sed "s/ *$//"); case $st in clean|active|"active, resyncing"|"clean, resyncing") echo "$a $st" ;; "clean, degraded"|"active, degraded") [ "${DEGRADED:-0}" = 1 ] && echo "$a $st" || no "$a: $st" ;; *) no "$a: $st" ;; esac; done
[ -f /etc/crypttab ] && awk "\$1 !~ /^#/ && NF" /etc/crypttab | grep -q . && ! command -v cryptsetup >/dev/null && gone cryptsetup
[ -f /etc/crypttab ] && command -v cryptsetup >/dev/null && for c in $(awk "\$1 !~ /^#/ && NF {print \$1}" /etc/crypttab); do cryptsetup status "$c" 2>/dev/null | grep -q "is active" && echo "$c open" || no "LUKS $c not open"; done
ls /var/log/bko-holt/load.log >/dev/null 2>&1 && echo "load log kept" || no "/var/log/bko-holt/load.log missing"
exit $f'
# Boot state: line 1 systemd's overall state (or the init system's name;
# Slackware's BSD-style rc with its runlevel), then for Slackware each rc
# script that is executable (on) whose daemon isn't running,
# then one line per failed unit or service that isn't up, sorted.  A
# restored machine may have no line its source lacked.  Under s6 the boot's
# s6-rc change may still be running when SSH answers (sshd is a longrun,
# up before the oneshots behind it, e.g. Artix's mount-net): wait for it to
# end first, as systemd's is-system-running --wait does (up to 300 s).  So
# with OpenRC (while an openrc, or Gentoo's older rc, process runs the
# runlevel, or rc-status shows a service starting) and dinit (while dinitctl
# list shows one starting, "<<").  Each wait logs on stderr.
BOOTSTATE='if [ -d /run/systemd/system ]; then
    timeout 300 systemctl is-system-running --wait; systemctl --failed --no-legend --plain | awk "{print \$1}" | sort
elif command -v rc-status >/dev/null; then
    echo openrc
    i=0; while [ $i -lt 300 ] && { grep -qsxE "openrc|rc" /proc/[0-9]*/comm || rc-status -a 2>/dev/null | grep -q "\[ *starting"; }; do sleep 1; i=$((i + 1)); done; [ $i = 0 ] || echo "bootstate: waited ${i} s for openrc" >&2
    { rc-status -a | awk "/^Runlevel: / {lv=\$2; next} /^Dynamic Runlevel: / {lv=\$3; next} (lv == \"sysinit\" || lv == \"boot\" || lv == \"default\" || lv == \"needed/wanted\") && !/\\[ *started *\\]/ {print \"down \" \$1}"
      rc-status --crashed | sed "s/^/crashed /"; } | sort
elif command -v dinitctl >/dev/null; then
    echo dinit
    i=0; while [ $i -lt 300 ] && dinitctl list 2>/dev/null | grep -q "^\[[^]]*<<"; do sleep 1; i=$((i + 1)); done; [ $i = 0 ] || echo "bootstate: waited ${i} s for dinit" >&2
    dinitctl list | awk "!/^\\[[^]]*\\+[^]]*\\]/ {sub(/^\\[[^]]*\\] */, \"\"); print \"down \" \$1}" | sort
elif command -v s6-rc >/dev/null; then
    echo s6
    i=0; while [ $i -lt 300 ] && grep -qsx s6-rc /proc/[0-9]*/comm; do sleep 1; i=$((i + 1)); done; [ $i = 0 ] || echo "bootstate: waited ${i} s for s6-rc" >&2
    s6-rc -da list | sed "s/^/down /" | sort
elif command -v herd >/dev/null; then
    # Guix System: herd status lists its services under Started:, Stopped:
    # and Failed: (then Running timers: and One-shot:), one " X NAME" each.
    # sshd-N is one SSH connection of the inetd-style sshd: stopped once
    # that connection ends, and listed until Shepherd forgets it, so it says
    # nothing of the boot (t2h-20261124T1449-d9fd: "down sshd-4").
    echo shepherd
    herd status | awk "/^[A-Z][^:]*:\$/ {sec=\$0; next} sec == \"Stopped:\" && \$2 !~ /^sshd-[0-9]+\$/ {print \"down \" \$2} sec == \"Failed:\" {print \"failed \" \$2}" | sort
elif [ -f /etc/rc.d/rc.M ] && [ -f /etc/slackware-version ]; then
    echo "slackware-rc runlevel $(runlevel | cut -d " " -f 2)"
    for p in rc.sshd:sshd rc.syslog:syslogd rc.crond:crond rc.atd:atd rc.acpid:acpid rc.udev:udevd rc.elogind:elogind-daemon rc.messagebus:dbus-daemon rc.inet1:dhcpcd rc.ntpd:ntpd rc.cups:cupsd; do
        [ -x "/etc/rc.d/${p%%:*}" ] || continue; pgrep -x "${p#*:}" >/dev/null || echo "down ${p%%:*}"
    done | sort
else
    echo "unknown init"
fi'
# FLAKY_UNITS (space-separated): units known to fail on some boots of this
# guest and not others, whatever the restore did: left out of its boot
# state, and "degraded" read as "running" when they were all that failed.
# Each boot's state as it was is in logs/bootstate-raw.txt.
FLAKY_UNITS=""
case $G in
    # V1 of docs/design/vanillaos.md: networkd's wait-online fails on some
    # boots of the guest (QEMU's user network), on the source as well.
    vanilla3*) FLAKY_UNITS="systemd-networkd-wait-online.service" ;;
    # EndeavourOS's installer leaves six of /etc/group's groups (adm, wheel,
    # uucp, nopasswdlogin, rfkill, sys) out of /etc/gshadow, so shadow.service
    # (pwck -qr, then grpck -r) fails whenever shadow.timer starts it: grpck
    # -r exits 2 in both guests' own images.  The timer is daily and
    # Persistent with no random delay, and the image has no stamp of it: the
    # source's boot makes one, the backup holds it, and a restored boot past
    # the guest's next midnight (UTC) starts the unit at once, where the
    # source's boot never did (run t2h-20261122T2116-b591: the source up at
    # 23:57 UTC, the restored disk at 00:02, "degraded shadow.service").
    endeavouros*) FLAKY_UNITS="shadow.service" ;;
esac
# TrueNAS (docs/design/truenas.md): its middleware wants more than vm-run's
# 2 GiB, so the source's and the restored boots get 4 GiB (the spec's T6;
# the rescuer has its own --memory); and the mirror guest's data pool is
# left out of the restore (HT_POOLS, below).
case $G in truenas*) export MEMORY=${MEMORY:-4096} ;; esac
case $G in truenas*-mirror) [[ -n ${HT_POOLS+set} ]] || HT_POOLS=boot-pool ;; esac
bootstate() {
    local o; o=$(sshk "$1" "$BOOTSTATE")
    [[ -n $FLAKY_UNITS ]] || { printf '%s\n' "$o"; return; }
    { echo "== $(date -u +%FT%TZ)"; printf '%s\n' "$o"; } >> "$R/logs/bootstate-raw.txt"
    printf '%s\n' "$o" | awk -v f=" $FLAKY_UNITS " 'NR == 1 { first = $0; next } index(f, " " $1 " ") { d = 1; next } { rest[++n] = $0 }
        END { if (first == "degraded" && n == 0 && d) first = "running"; print first; for (i = 1; i <= n; i++) print rest[i] }'
}
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
# A guest whose vm-run-options enroll a MOK (--mok: AnduinOS's GRUB, signed
# on the machine with its own key) boots with Secure Boot on, the source and
# the restored disk alike (both take the guest's options).  Its MOK
# certificate is vms/GUEST/GUEST-mok.der (the source's MOK.der).
mok=0; [[ " ${fw[*]} " == *" --mok "* ]] && mok=1
# openSUSE Leap 16 (leap16: --uefi, SUSE's Microsoft-signed shim, no MOK of
# its own; docs/design/leap16.md, L5) must boot under Secure Boot too, the
# source and the restored disk alike: sb_check wants "SecureBoot enabled".
sbreq=0; case $G in leap16*) [[ $uefi == 1 && " ${fw[*]} " != *" --no-secure-boot "* ]] && sbreq=1 ;; esac
MOKCERT=$W/vms/$G/$G-mok.der
SBSTATE='mokutil --sb-state 2>&1; [ -f /var/lib/shim-signed/mok/MOK.der ] && mokutil --test-key /var/lib/shim-signed/mok/MOK.der 2>&1; true'
sb_check() {  # the restored guest's Secure Boot state, against the source's
    ((mok || sbreq)) || return 0
    sshk "$1" "$SBSTATE" > "$R/logs/restored-sbstate.txt" 2>&1
    if grep -q '^SecureBoot enabled' "$R/logs/restored-sbstate.txt"; then
        ok "the restored disk booted under Secure Boot$( ((mok)) && echo " with the MOK enrolled by vm-run --mok"): $(tr '\n' ' ' < "$R/logs/restored-sbstate.txt")"
    else
        bad "mokutil --sb-state on the restored guest: $(tr '\n' ' ' < "$R/logs/restored-sbstate.txt") (the source's: $(tr '\n' ' ' < "$R/logs/source-sbstate.txt" 2>/dev/null))"
    fi
}
# SELinux (docs/design/leap16.md, L5): a label the restore wrote wrongly, or
# one the boot stage made, shows only as a denial.  SELINUX_STATE prints
# getenforce's word, "relabel pending FILE" for a relabel left for the next
# boot, and "avc N KEY" for each kind of AVC denial since boot (KEY: the
# permissions, comm, name, scontext, tcontext, tclass and permissive; pids,
# inodes and devices left out), from auditd's log when it runs, else the
# journal.  leap16 must come up Enforcing with no relabel pending and no
# denial beyond SELINUX_KNOWN; tumbleweed, fedora* and rocky* get the same
# line as a note, no FAIL, until each has passed with it once.  Other
# guests: nothing.
selinux=none; case $G in leap16*) selinux=check ;; tumbleweed*|fedora*|rocky*) selinux=note ;; esac
# Denials expected on a restored boot: "ERE<TAB>reason", matched against KEY.
SELINUX_KNOWN=()
SELINUX_STATE='command -v getenforce >/dev/null 2>&1 || { echo "selinux none"; exit 0; }
echo "selinux $(getenforce 2>/dev/null)"
for f in /.autorelabel /etc/selinux/.autorelabel; do [ -e "$f" ] && echo "relabel pending $f"; done
if command -v ausearch >/dev/null 2>&1 && systemctl -q is-active auditd 2>/dev/null; then l=auditd; else l=journal; fi
echo "avc-log $l"
{ if [ $l = auditd ]; then ausearch -m AVC,USER_AVC -ts boot -r 2>/dev/null; else journalctl -b -o cat 2>/dev/null; fi; } |
    grep "avc: *denied" | awk "{ k = \$0; sub(/.*denied *\\{ */, \"\", k); sub(/ *\\}.*/, \"\", k); k = \"{ \" k \" }\"
        for (i = 1; i <= NF; i++) if (\$i ~ /^(comm|name|scontext|tcontext|tclass|permissive)=/) k = k \" \" \$i; print k }" |
    sort | uniq -c | awk "{ n = \$1; \$1 = \"\"; print \"avc \" n \$0 }"
true'
selinux_record() {  # WHO KEY
    [[ $selinux != none ]] || return 0
    sshk "$2" "$SELINUX_STATE" > "$R/logs/$1-selinux.txt" 2>&1
}
selinux_check() {  # KEY: the restored guest's SELinux mode, relabel and denials
    [[ $selinux != none ]] || return 0
    selinux_record restored "$1"
    local f=$R/logs/restored-selinux.txt s=$R/logs/source-selinux.txt mode n k e known line unk=() why=()
    mode=$(sed -n 's/^selinux //p' "$f")
    while read -r _ _ k; do
        known=""
        for e in ${SELINUX_KNOWN[@]+"${SELINUX_KNOWN[@]}"}; do [[ $k =~ ${e%%$'\t'*} ]] && { known=${e#*$'\t'}; break; }; done
        if [[ -n $known ]]; then why+=("$known"); else unk+=("$k"); fi
    done < <(grep '^avc ' "$f")
    n=$(awk '/^avc / {s += $2} END {print s + 0}' "$f")
    line="SELinux on the restored guest: ${mode:-no answer}, $n AVC denial(s) since boot (in $(sed -n 's/^avc-log //p' "$f")), ${#unk[@]} kind(s) beyond the known list${why:+ (known: ${why[*]})}, $(grep -q '^relabel pending' "$f" && grep '^relabel pending' "$f" | tr '\n' ' ' || echo "no relabel pending"); the source's: $(sed -n 's/^selinux //p' "$s" 2>/dev/null), $(awk '/^avc / {s += $2} END {print s + 0}' "$s" 2>/dev/null) denial(s) (logs/restored-selinux.txt)"
    if [[ $selinux == note ]]; then say "note: $line"; return 0; fi
    if [[ $mode == Enforcing && ${#unk[@]} == 0 ]] && ! grep -q '^relabel pending' "$f"; then
        ok "$line"
    else
        bad "$line${unk:+: ${unk[*]:0:3}}"
    fi
}
mok_check() {  # MNT: the restored tree, mounted: every grubx64.efi on its vfat file systems is signed by the source's MOK
    ((mok)) || return 0
    local m=$1 f n=0 bad_=() pem=$R/tmp/mok.pem
    mkdir -p "$R/tmp"
    [[ -f $MOKCERT ]] || { bad "MOK signature: no $MOKCERT (the source's MOK.der)"; return; }
    openssl x509 -inform DER -in "$MOKCERT" -out "$pem" 2>/dev/null || cp "$MOKCERT" "$pem"
    : > "$R/logs/restored-sbverify.txt"
    while read -r f; do
        n=$((n + 1))
        { echo "== ${f#"$m"/}"; sbverify --cert "$pem" "$f" 2>&1; } >> "$R/logs/restored-sbverify.txt" || bad_+=("${f#"$m"/}")
    done < <(findmnt -rn -t vfat -o TARGET | awk -v m="$m" 'index($0, m "/") == 1' | while read -r v; do find "$v" -xdev -type f -iname grubx64.efi; done | sort)
    if ((n == 0)); then
        bad "MOK signature: no grubx64.efi on the restored disk's ESPs"
    elif ((${#bad_[@]})); then
        bad "MOK signature: sbverify --cert ${MOKCERT##*/} fails on ${bad_[*]} (logs/restored-sbverify.txt)"
    else
        ok "MOK signature: $n restored grubx64.efi verify with sbverify --cert ${MOKCERT##*/} ($(sha256sum < "$MOKCERT" | cut -c1-16)...): $(grep '^== ' "$R/logs/restored-sbverify.txt" | cut -c4- | tr '\n' ' ')"
    fi
}
# Solus (solus49; docs/design/solus.md, S5): the ESP is /boot, but not in
# fstab (clr-boot-manager mounts it when it needs it), so nothing mounts it
# at boot and `bko-holt dump` refuses until something has.  Before each
# dump, the source's and the restored system's, SOLUS_BOOT mounts it as the
# user would: `clr-boot-manager mount-boot`, else the one partition of the
# ESP's GPT type.  (The backup and fsmeta's records are of vm-mount's tree,
# which mounts the ESP at /boot itself.)  The restored disk boots alone
# under the guest's vm-run-options (--uefi --mok: sb_check wants
# "SecureBoot enabled", mok_check systemd-boot's grubx64.efi signed by
# solus-enroll-me.cer), and SOLUS_LOADER must say systemd-boot booted it
# (LoaderInfo, bootctl status), as on the source.
solus=0; case $G in solus*) solus=1 ;; esac
SOLUS_BOOT='if mountpoint -q /boot; then how=already
elif clr-boot-manager mount-boot >/dev/null 2>&1 && mountpoint -q /boot; then how="clr-boot-manager mount-boot"
else
    p=$(lsblk -rnpo NAME,PARTTYPE | awk "tolower(\$2) == \"c12a7328-f81f-11d2-ba4b-00a0c93ec93b\" {print \$1}")
    if [ "$(printf "%s\n" "$p" | grep -c .)" = 1 ] && mount "$p" /boot; then how="mount $p, the ESP by GPT type (clr-boot-manager mount-boot failed)"
    else echo "no ESP at /boot: clr-boot-manager mount-boot failed; ESPs by GPT type: ${p:-none}"; exit 1; fi
fi
echo "$how: $(findmnt -no SOURCE,FSTYPE,OPTIONS /boot)"
ls -d /boot/EFI/com.solus-project/bootloaderx64.efi /boot/loader/loader.conf'
SOLUS_LOADER='f=/sys/firmware/efi/efivars/LoaderInfo-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f
if [ -r "$f" ]; then echo "LoaderInfo $(tail -c +5 "$f" | tr -d "\000")"; else echo "LoaderInfo none"; fi
bootctl status 2>/dev/null | sed -n "s/^ *Product: */Product /p" | head -1; true'
solus_mount_boot() {  # WHO KEY: the ESP at /boot before WHO's dump (source, restored)
    ((solus)) || return 0
    if sshk "$2" "$SOLUS_BOOT" > "$R/logs/$1-solus-boot.txt" 2>&1; then
        ok "solus: the ESP at /boot for the $1 system's dump: $(head -1 "$R/logs/$1-solus-boot.txt")"
    else
        bad "solus: the ESP at /boot on the $1 system: $(tr '\n' ' ' < "$R/logs/$1-solus-boot.txt")"; return 1
    fi
}
solus_loader_check() {  # KEY: the restored guest booted through systemd-boot, under Secure Boot
    ((solus)) || return 0
    ((mok)) || bad "solus: the guest's vm-run-options have no --mok, so its boots aren't under Secure Boot (sb_check skipped)"
    sshk "$1" "$SOLUS_LOADER" > "$R/logs/restored-loader.txt" 2>&1
    if grep -Eq '^(LoaderInfo|Product) systemd-boot' "$R/logs/restored-loader.txt"; then
        ok "solus: the restored disk booted through systemd-boot: $(tr '\n' ' ' < "$R/logs/restored-loader.txt")"
    else
        bad "solus: the restored disk's boot loader: $(tr '\n' ' ' < "$R/logs/restored-loader.txt") (the source's: $(tr '\n' ' ' < "$R/logs/source-loader.txt" 2>/dev/null))"
    fi
}
# Slackware 15.0 (slackware15, slackware15-uefi; docs/design/slackware.md,
# S6).  Its fstab names devices (/dev/vda2 /, the uefi guest's /dev/vda1 at
# /boot/efi), which vm-mount resolves, and the ESP is mounted at boot, so the
# dumps need nothing mounted by hand.  slackware15 boots LILO from the MBR:
# load runs lilo on a copy of lilo.conf with root= by name in append=, since
# lilo stores root as a device number and virtio_blk's major is the rescue
# kernel's there.  slack_mbr_check reads the restored disk's sector 0 on the
# host (LILO's first stage: "LILO" at offset 6, then its version), and
# slack_root_check wants the restored boot's / on the source's device, as
# the source has it (SLACK_ROOT: by the root's major:minor in /sys, as a
# boot without an initrd shows / as /dev/root): a wrong numeric root panics
# the kernel or mounts another.  slackware15-uefi boots ELILO from the
# fallback path, on a fresh NVRAM (--uefi --no-secure-boot): elilo_check
# wants ELILO in the restored ESP's EFI/Slackware (elilo.efi) and EFI/BOOT
# (BOOTX64.EFI), each with its elilo.conf, a kernel with the bytes of one of
# the restored /boot/vmlinuz-*, and an initrd with those of the restored
# /boot/initrd.gz (load remakes it with the source's mkinitrd command and
# copies it over the ESP's).  judge.py fs holds the rest (LILO_STAGE,
# ELILO_KEPT).
slack=0; case $G in slackware*) slack=1 ;; esac
SLACK_ROOT='d=$(mountpoint -d /); n=$(basename "$(readlink -f "/sys/dev/block/$d")")
echo "root $n $(findmnt -no FSTYPE --mountpoint / | tail -1) $(findmnt -no OPTIONS --mountpoint / | tail -1 | cut -d, -f1)"
echo "cmdline $(cat /proc/cmdline)"'
slack_root_check() {  # KEY: the restored boot's / as the source's
    ((slack)) || return 0
    sshk "$1" "$SLACK_ROOT" > "$R/logs/restored-root.txt" 2>&1
    local want got
    want=$(grep '^root ' "$R/logs/source-root.txt" 2>/dev/null) got=$(grep '^root ' "$R/logs/restored-root.txt")
    if [[ -n $want && $got == "$want" ]]; then
        ok "slackware: the restored boot's / is the source's: ${got#root } ($(sed -n 's/^cmdline //p' "$R/logs/restored-root.txt"))"
    else
        bad "slackware: the restored boot's /: ${got:-none} (the source's: ${want:-none}; logs/restored-root.txt)"
    fi
}
slack_mbr_check() {  # the restored disk's MBR holds LILO's first stage (slackware15)
    ((slack && !uefi)) || return 0
    local f=$R/tmp/restored-mbr.bin sig
    mkdir -p "$R/tmp"; rm -f "$f"
    if ! qemu-img dd -f qcow2 -O raw bs=512 count=1 if="${dst[0]}" of="$f" > "$R/logs/restored-mbr.txt" 2>&1; then
        bad "slackware: read the restored disk's MBR: $(tail -1 "$R/logs/restored-mbr.txt")"; return
    fi
    sig=$(dd if="$f" bs=1 skip=6 count=4 status=none)
    od -A d -t x1z -N 16 "$f" >> "$R/logs/restored-mbr.txt"
    if [[ $sig == LILO ]]; then
        ok "slackware: the restored disk's MBR holds LILO's first stage (\"LILO\" at offset 6, version $(od -A n -t u1 -j 10 -N 2 "$f" | xargs | tr ' ' .))"
    else
        bad "slackware: the restored disk's MBR isn't LILO's: offset 6 holds \"$(printf '%s' "$sig" | tr -c '[:print:]' .)\" (logs/restored-mbr.txt)"
    fi
    rm -f "$f"
}
elilo_check() {  # MNT: the restored tree, mounted: ELILO in the ESP's EFI/Slackware and EFI/BOOT (slackware15-uefi)
    ((slack && uefi)) || return 0
    local m=$1 esp=$1/boot/efi d dir efi conf img ird k v bad_=() said=()
    : > "$R/logs/restored-elilo.txt"
    for d in Slackware:elilo.efi BOOT:BOOTX64.EFI; do
        dir=$esp/EFI/${d%%:*} efi=${d#*:} conf=$esp/EFI/${d%%:*}/elilo.conf
        if [[ ! -f $dir/$efi || ! -f $conf ]]; then bad_+=("EFI/${d%%:*}: no $efi or elilo.conf"); continue; fi
        # elilo.conf's image= and initrd=: beside it, or from the ESP's root.
        img=$(sed -n 's/^[[:space:]]*image[[:space:]]*=[[:space:]]*//p' "$conf" | head -1 | tr -d '"')
        ird=$(sed -n 's/^[[:space:]]*initrd[[:space:]]*=[[:space:]]*//p' "$conf" | head -1 | tr -d '"')
        [[ $img == /* ]] && img=$esp$img || img=$dir/${img:-vmlinuz}
        [[ $ird == /* ]] && ird=$esp$ird || ird=$dir/${ird:-initrd.gz}
        { echo "== EFI/${d%%:*}"; sha256sum "$dir/$efi" "$img" "$ird" "$m"/boot/vmlinuz-* "$m/boot/initrd.gz" 2>&1 | sed "s|$m/||"; } >> "$R/logs/restored-elilo.txt"
        k=""
        for v in "$m"/boot/vmlinuz-*; do [[ -f $v && ! -L $v ]] && cmp -s "$img" "$v" && { k=${v##*/}; break; }; done
        [[ -n $k ]] || bad_+=("EFI/${d%%:*}/${img##*/}: no restored /boot/vmlinuz-* has its bytes")
        cmp -s "$ird" "$m/boot/initrd.gz" || bad_+=("EFI/${d%%:*}/${ird##*/}: not the restored /boot/initrd.gz")
        said+=("EFI/${d%%:*}: $efi, ${img##*/} = /boot/${k:-?}, ${ird##*/} = /boot/initrd.gz")
    done
    if ((${#bad_[@]})); then
        bad "slackware: ELILO on the restored ESP: ${bad_[*]} (logs/restored-elilo.txt)"
    else
        ok "slackware: ELILO on the restored ESP: ${said[*]}"
    fi
}
# btrfs read-only snapshots (AnduinOS's baselines under @snapshots,
# snapper's): one SNAP line per read-only subvolume, its path from the top
# level, its ro property and `btrfs filesystem du` total, exclusive and set
# shared bytes where a mount reaches it ("unmounted" where none does).  load
# makes each a snapshot of its origin and restores it with --clone-from, so
# its extents stay shared (docs/design/btrfs-snapshots.md).
SNAPS='command -v btrfs >/dev/null || exit 0
M=$(findmnt -rn -t btrfs -o UUID,TARGET,FSROOT | sort -u)
for u in $(echo "$M" | cut -d" " -f1 | sort -u); do
    m=$(echo "$M" | awk -v u="$u" "\$1 == u {print \$2; exit}")
    btrfs subvolume list -r "$m" 2>/dev/null | sed "s/.* path //" | while read -r p; do
        best= bl=-1 root=
        while read -r uu t r; do
            [ "$uu" = "$u" ] || continue
            [ "$r" = / ] && r=
            case "/$p/" in
            "$r"/*) if [ -z "$r" ]; then c="${t%/}/$p"; else c="${t%/}${p#"${r#/}"}"; fi
                # (The subvolume mounted at / itself, a transactional root:
                # through another mount (/.snapshots) if one reaches it, as
                # btrfs filesystem du of / would walk the other file systems.)
                if [ -z "$c" ]; then root=/
                elif [ ${#r} -gt $bl ]; then bl=${#r}; best=$c; fi ;;
            esac
        done <<EOF
$M
EOF
        [ -n "$best" ] || best=$root
        if [ -n "$best" ] && [ -d "$best" ]; then
            ro=$(btrfs property get -ts "$best" ro 2>/dev/null | sed "s/^ro=//")
            # (bootx: the exclusive bytes of its initramfs images, which the
            # boot stage rebuilds in the origin, so that the copies in the
            # snapshot share nothing with it any more.)
            bx=$(btrfs filesystem du -s --raw "$best"/boot/initrd.img-* "$best"/boot/initramfs-* "$best"/boot/initrd-* 2>/dev/null | awk "NR > 1 && \$2 ~ /^[0-9]+\$/ {s += \$2} END {print s + 0}")
            echo "SNAP $p ro=${ro:-?} $(btrfs filesystem du -s --raw "$best" 2>/dev/null | awk "NR == 2 {print \"total=\" \$1, \"excl=\" \$2, \"shared=\" \$3}") bootx=$bx"
        else
            echo "SNAP $p unmounted"
        fi
    done
done'
# The restored read-only snapshots against the source's: each still there and
# read-only, its exclusive bytes at most twice the source's plus 64 MiB (a
# snapshot restored in full would have nearly all of its total exclusive),
# not counting its initramfs images: the boot stage rebuilds the origin's, so
# the snapshot's own copies are its alone (anduinos2's 87 MiB, run a5-1).
snaps_check() {
    grep -q '^SNAP ' "$R/logs/source-snaps.txt" 2>/dev/null || return 0
    TMO=1800 sshk "$1" "$SNAPS" > "$R/logs/restored-snaps.txt" 2>&1
    local out rc=0
    # HT_REFORMAT: a snapshot of a btrfs made another type isn't restored
    # (docs/design/reformat.md); one that is still there is judged as ever.
    out=$(awk -v reformat="${HT_REFORMAT:-}" 'NR == FNR { if ($1 == "SNAP") src[$2] = $0; next }
        $1 == "SNAP" { dst[$2] = $0 }
        function f(l, k,   n, i, a, kv) { n = split(l, a, " "); for (i = 3; i <= n; i++) { split(a[i], kv, "="); if (kv[1] == k) return kv[2] } return "" }
        END {
            bad = 0
            for (p in src) {
                if (src[p] ~ / unmounted$/) { print "skip " p " (no mount reaches it on the source)"; continue }
                if (!(p in dst) && reformat != "") { print "ok " p " left out (HT_REFORMAT=" reformat ")"; continue }
                if (!(p in dst)) { print "FAIL " p " missing"; bad = 1; continue }
                if (f(src[p], "ro") == "true" && f(dst[p], "ro") != "true") { print "FAIL " p " not read-only (" dst[p] ")"; bad = 1; continue }
                se = f(src[p], "excl"); de = f(dst[p], "excl")
                if (se == "" || de == "") { print "FAIL " p " no exclusive size (source " src[p] ", restored " dst[p] ")"; bad = 1; continue }
                bx = f(dst[p], "bootx") + 0
                if (de - bx > 2 * se + 64 * 1048576) { bad = 1; w = "FAIL" } else w = "ok"
                printf "%s %s ro=%s excl %.1f MiB (source %.1f MiB; %.1f MiB of it the initramfs the boot stage rebuilt in its origin) of %.1f MiB\n", w, p, f(dst[p], "ro"), de / 1048576, se / 1048576, bx / 1048576, f(dst[p], "total") / 1048576
            }
            exit bad
        }' "$R/logs/source-snaps.txt" "$R/logs/restored-snaps.txt") || rc=$?
    printf '%s\n' "$out" > "$R/logs/snaps-judge.txt"
    if ((rc == 0)); then
        ok "btrfs read-only snapshots kept read-only, extents shared: $(tr '\n' ';' <<<"$out" | sed 's/;$//; s/;/; /g; s/^ok //; s/; ok /; /g')"
    else
        bad "btrfs read-only snapshots: $(grep '^FAIL' <<<"$out" | tr '\n' ' ')(logs/snaps-judge.txt)"
    fi
}
# AnduinOS's Disk Snapshots Manager keys its records by subvolume UUID
# (Decision 6 of docs/design/anduinos.md): what its CLI says after the
# restore, with the file system's subvolume UUIDs beside it.  Recorded,
# never judged.
SNAPMGR='c=anduinos-btrfs-snapshots-manager-cli; command -v $c >/dev/null || exit 0
echo "== status --json"; timeout 120 $c status --json 2>&1; echo "== exit $?"
echo "== list --json"; timeout 120 $c list --json 2>&1; echo "== exit $?"
echo "== btrfs subvolume list -u -q /"; btrfs subvolume list -u -q / 2>&1'
snapmgr_summary() {  # FILE: one line of what the CLI's JSON says, and which UUIDs it names
    python3 -I - "$1" <<'PY' 2>&1 | head -c 600
import json, re, sys
t = open(sys.argv[1]).read()
parts = dict(re.findall(r"^== (.+?)\n(.*?)(?=^== |\Z)", t, re.S | re.M))
uuids = set(re.findall(r"\buuid ([0-9a-f-]{36})", parts.get("btrfs subvolume list -u -q /", "")))
out = []
for name in ("status --json", "list --json"):
    try:
        d = json.loads(parts.get(name, ""))
    except ValueError:
        out.append(f"{name.split()[0]}: not JSON: {parts.get(name, '').strip()[:80]!r}")
        continue
    named = set()
    def walk(v, k=""):
        if isinstance(v, dict):
            for kk, x in v.items():
                walk(x, kk)
        elif isinstance(v, list):
            for x in v:
                walk(x, k)
        elif isinstance(v, str) and k in ("snapshot_uuid", "snapshot_parent_uuid") and re.fullmatch(r"[0-9a-f-]{36}", v):
            named.add(v)
    walk(d)
    head = {k: d[k] for k in ("available", "state", "issues") if isinstance(d, dict) and k in d}
    out.append(f"{name.split()[0]}: {json.dumps(head)[:160]}, {len(named)} snapshot UUIDs named, {len(named & uuids)} of them on this file system")
print("; ".join(out))
PY
}
snapmgr_record() {  # WHO KEY
    sshk "$2" "$SNAPMGR" > "$R/logs/$1-snapmgr.txt" 2>&1
    [[ -s $R/logs/$1-snapmgr.txt ]] || { rm -f "$R/logs/$1-snapmgr.txt"; return 0; }
    [[ $1 == restored ]] || return 0
    local sum; sum=$(snapmgr_summary "$R/logs/restored-snapmgr.txt")
    say "note: the snapshot manager after the restore (recorded, not judged; logs/restored-snapmgr.txt, source-snapmgr.txt): $sum"
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
# the dump, with grubenv as it was.  The rollback reboots in the same QEMU,
# as the restored disk's does; the default's boot for the dump is a new
# QEMU, as abroot_source's and guix_source's are, on a fresh NVRAM (gvars:
# a new copy of a GVARS store), as the restored disk's first boot (its
# dump) is.  Fedora 44's bootloader-update.service (bootupd) adds a
# "Fedora" entry for EFI/fedora/shimx64.efi at a first boot on a store
# without one, and a reboot in the same QEMU boots through it: the source's
# dump then had BootCurrent on that entry, the restored disk's on the
# removable path (kinoite44, t2h-20261118T0132-4a99).  Exit 1 when the run
# can go no further.
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
    vm_down "$gkey"
    gvars source; local vfw=("${VFW[@]}")
    CONSOLE_KEY=$ckey vm_up "${src[0]}" "$L/source-serial-3.log" "${fw[@]}" "${vfw[@]}" "${extra_src[@]}"
    vm_wait "$gkey" || { bad "ostree: the source boots its default entry again (no SSH; logs/source-serial-3.log)"; return 1; }
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
# A Guix System guest (guix15, guix15-bios; docs/design/guix.md, GX6) keeps
# its system generations, each a GRUB entry: the current one first, the
# others in the submenu "GNU system, old configurations...", newest first.
# The source boots the one before its current one (picked on GRUB's serial
# menu by console-keys.py: v, Enter, Enter) and its current one again, for
# the baseline; the restored disk does the same, alone, and must show what
# the source did.  Nothing is switched (switch-generation rebuilds the boot
# loader's files, from substitutes: GX6's gx6-11 waited on them past 10 min).
# GUIX_STATE is what a boot shows: the booted and current system (store
# paths), the system profile's link (by name: a reconfigure writes it whole,
# switch-generation relative) and the kernel's root= and gnu.system= (where
# it leads: switch-generation's grub.cfg names the profile's link, a
# reconfigure's the store path).
GUIX_STATE='echo "booted $(readlink -f /run/booted-system)"
echo "current $(readlink -f /run/current-system)"
echo "profile $(basename "$(readlink /var/guix/profiles/system)")"
s=$(tr " " "\n" </proc/cmdline | sed -n "s/^gnu\.system=//p"); echo "gnu.system $(readlink -f "$s")"
tr " " "\n" </proc/cmdline | grep "^root="'
# GUIX_GENS prints "CURRENT PREVIOUS PREVIOUS-SYSTEM": the profile's
# generation numbers (its link is system-N-link, or that by its whole path
# once a reconfigure has made it) and where the previous one leads.
GUIX_GENS='n=$(readlink /var/guix/profiles/system | sed -n "s/^.*system-\([0-9]*\)-link\$/\1/p")
p=$(ls /var/guix/profiles | sed -n "s/^system-\([0-9]*\)-link\$/\1/p" | sort -n | awk -v n="$n" "\$1 < n" | tail -n 1)
[ -n "$n" ] && [ -n "$p" ] || { echo "no generation before ${n:-the current one}" >&2; exit 1; }
echo "$n $p $(readlink -f /var/guix/profiles/system-$p-link)"'
# "v" is GRUB's menu Down as one byte.  The Down arrow is three (ESC [ B),
# and on UEFI two readers share COM1 (terminal-inputs console and serial:
# OVMF's TerminalDxe and GRUB's own serial driver), so one could take the
# ESC and the other the rest; GRUB waits 100 ms for the rest of a sequence,
# so Down was lost and the first Enter booted the current generation
# (t2h-20261118T2037-e7d1 on Copland).  guix15-bios has only GRUB's reader.
GUIX_PICK_RE='GNU system, old configurations' GUIX_PICK_KEYS='v|\r|\r'
# The source's baseline: its previous generation booted, then its current
# one again, with every GUIX_STATE line as at first.  Exit 1 when the run
# can go no further.
guix_source() {
    local L=$R/logs n p ps
    sshk "$gkey" "$GUIX_STATE" > "$L/source-guix-first.txt" 2>&1
    read -r n p ps < <(sshk "$gkey" "$GUIX_GENS" 2>"$L/source-guix-gens.err") ||
        { bad "guix: the source's generations: $(tail -1 "$L/source-guix-gens.err")"; return 1; }
    vm_down "$gkey"
    # (CONSOLE_KEY: guix15-luks's passphrase, typed at GRUB's prompt and the
    # initrd's, before and after the menu.)
    GRUB_PICK=$GUIX_PICK_RE GRUB_KEYS=$GUIX_PICK_KEYS CONSOLE_KEY=$ckey vm_up "${src[0]}" "$L/source-serial-prev.log" "${fw[@]}" "${svfw[@]}" "${extra_src[@]}"
    vm_wait "$gkey" || { bad "guix: the source's generation $p boots (no SSH; logs/source-serial-prev.log)"; return 1; }
    grep -q 'console-keys.py: typed' "$L/source-serial-prev.log" || { bad "guix: GRUB's menu never showed \"$GUIX_PICK_RE\" on the source's serial console"; return 1; }
    sshk "$gkey" "$GUIX_STATE" > "$L/source-guix-prev.txt" 2>&1
    bootstate "$gkey" > "$L/source-prev-bootstate.txt"
    vm_down "$gkey"
    CONSOLE_KEY=$ckey vm_up "${src[0]}" "$L/source-serial-3.log" "${fw[@]}" "${svfw[@]}" "${extra_src[@]}"
    vm_wait "$gkey" || { bad "guix: the source boots its generation $n again (no SSH; logs/source-serial-3.log)"; return 1; }
    sshk "$gkey" "$GUIX_STATE" > "$L/source-guix.txt" 2>&1
    if ! cmp -s "$L/source-guix-first.txt" "$L/source-guix.txt"; then
        bad "guix: the source's generation $n doesn't boot as at first: $(diff "$L/source-guix-first.txt" "$L/source-guix.txt" | grep '^[<>]' | head -3 | tr '\n' ' ')"; return 1
    fi
    if [[ $(sed -n 's/^booted //p' "$L/source-guix-prev.txt") != "$ps" ]]; then
        bad "guix: the source's pick of \"$GUIX_PICK_RE\" booted $(sed -n 's/^booted //p' "$L/source-guix-prev.txt"), not generation $p ($ps)"; return 1
    fi
    echo "$n $p $ps" > "$L/source-guix-gens.txt"
    ok "guix: the source boots its generation $p ($ps), picked on GRUB's serial menu, then $n again"
}
# The restored disk: its current generation (booted already, and dumped),
# then the previous one, the restored disk alone again (a fresh --snapshot
# boot), each as the source's; its boot state may have no line the source's
# previous-generation boot lacked.
guix_verify() {
    local L=$R/logs n p ps
    read -r n p ps < "$L/source-guix-gens.txt"
    sshk "$gkey" "$GUIX_STATE" > "$L/restored-guix.txt" 2>&1
    if cmp -s "$L/source-guix.txt" "$L/restored-guix.txt"; then
        ok "guix: the restored disk boots generation $n as the source did ($(sed -n 's/^booted //p' "$L/restored-guix.txt"))"
    else
        bad "guix: the restored generation $n boot isn't the source's: $(diff "$L/source-guix.txt" "$L/restored-guix.txt" | grep '^[<>]' | head -4 | tr '\n' ' ')"
    fi
    vm_down "$gkey"
    GRUB_PICK=$GUIX_PICK_RE GRUB_KEYS=$GUIX_PICK_KEYS CONSOLE_KEY=$ckey vm_up "${dst[0]}" "$L/restored-serial-prev.log" --snapshot "${fw[@]}" "${dvfw[@]}" "${BS2_VFW[@]}" "${extra_dst[@]}"
    vm_wait "$gkey" || { bad "guix: the restored generation $p boots (no SSH; logs/restored-serial-prev.log)"; return; }
    grep -q 'console-keys.py: typed' "$L/restored-serial-prev.log" || bad "guix: GRUB's menu never showed \"$GUIX_PICK_RE\" on the restored disk's serial console"
    sshk "$gkey" "$GUIX_STATE" > "$L/restored-guix-prev.txt" 2>&1
    if cmp -s "$L/source-guix-prev.txt" "$L/restored-guix-prev.txt"; then
        ok "guix: the restored disk boots generation $p as the source did ($(sed -n 's/^booted //p' "$L/restored-guix-prev.txt"))"
    else
        bad "guix: the restored generation $p boot isn't the source's: $(diff "$L/source-guix-prev.txt" "$L/restored-guix-prev.txt" | grep '^[<>]' | head -4 | tr '\n' ' ')"
    fi
    bootstate "$gkey" > "$L/restored-prev-bootstate.txt"
    if diff -q <(head -1 "$L/source-prev-bootstate.txt") <(head -1 "$L/restored-prev-bootstate.txt") >/dev/null \
        && [[ -z $(comm -13 <(tail -n +2 "$L/source-prev-bootstate.txt") <(tail -n +2 "$L/restored-prev-bootstate.txt")) ]]; then
        ok "guix: generation $p's boot state as the source's ($(head -1 "$L/restored-prev-bootstate.txt"))"
    else
        bad "guix: generation $p's boot state: source $(tr '\n' ' ' < "$L/source-prev-bootstate.txt"), restored $(tr '\n' ' ' < "$L/restored-prev-bootstate.txt")"
    fi
}
# An ABRoot guest (vanilla3; docs/design/vanillaos.md, V7) has two roots,
# each with its kernel and initramfs in the init LV, its abroot.cfg and its
# /etc upper; the master grub.cfg's literal `set default=N` picks one (B,
# "Current State (B)", after the guest's upgrade), and its other entry
# ("Previous State (A)") boots the other.  grub-reboot would do nothing, so
# that entry is picked on GRUB's serial menu (console-keys.py, vm_up's
# GRUB_PICK).  The source boots its default, the other root, and its
# default again, for the baseline; the restored disk boots each the same
# way, alone, and must show what the source did.  ABROOT_STATE is what a
# boot shows: the root `abroot status` calls present, and the one it marks
# ✓ (the root GRUB's default boots: it never says "previous", V1), the
# booted root's label, its /abimage.abr digest, /etc/bko-root, the
# kernel's command line, and the master grub.cfg's and grub.cfg.future's
# sha256 (only an upgrade or a rollback writes them, never a boot).
ABROOT_STATE='echo "present $(abroot status --json 2>/dev/null | sed -n "s/.*\"present\": *\"\([^\"]*\)\".*/\1/p")"
echo "checked $(abroot status 2>/dev/null | sed "s/\x1b\[[0-9;]*m//g" | sed -n "s/.*\(Present\|Future\): \([^ ]*\) ✓.*/\2/p" | tr "\n" " ")"
echo "root $(lsblk -dno LABEL "$(findmnt -vno SOURCE /)")"
echo "abimage $(tr -d " \n\t" </abimage.abr 2>/dev/null | sed -n "s/.*\"digest\":\"\([^\"]*\)\".*/\1/p")"
echo "bko-root $(cat /etc/bko-root 2>/dev/null || echo none)"
echo "cmdline $(cat /proc/cmdline)"
for f in /boot/grub/grub.cfg /boot/grub/grub.cfg.future; do echo "sha256 $f $(sha256sum <"$f" 2>/dev/null | cut -d" " -f1)"; done'
# The master grub.cfg's default and its entries' titles, one a line.
ABROOT_MENU='sed -n "s/^set default=//p" /boot/grub/grub.cfg | head -1; sed -n "s/^menuentry \"\([^\"]*\)\".*/\1/p" /boot/grub/grub.cfg'
# The lines this boot's journal has of an ABRoot repair.
ABROOT_REPAIRS='journalctl -b --no-pager 2>/dev/null | grep -i abroot | grep -iE "repair|integrity check"'
# PICK_RE and PICK_KEYS, vm_up's GRUB_PICK and GRUB_KEYS for the master
# grub.cfg's other entry (logs/source-abroot-menu.txt): its title as a
# regular expression, and GRUB's one-byte Up (^) or Down (v) toward it
# twice (two entries: the second press stays there), then Enter.  Not the
# arrows' ESC [ A: the master grub.cfg's terminal_input console serial puts
# two readers on COM1 under UEFI (OVMF's TerminalDxe and GRUB's serial
# driver), either can take the ESC, and GRUB waits 100 ms for the rest
# (guix15, 33ff371).  ABROOT_OTHER: its title.
abroot_pick() {
    local m=$R/logs/source-abroot-menu.txt d o t=()
    mapfile -t t < <(tail -n +2 "$m")
    d=$(head -1 "$m")
    [[ ${#t[@]} == 2 && $d =~ ^[01]$ ]] || return 1
    o=$((1 - d)) ABROOT_OTHER=${t[o]}
    PICK_RE=$(sed 's/[][\.*^$()+?{}|]/\\&/g' <<<"${t[o]}")
    if ((o < d)); then PICK_KEYS='^|^|\r'; else PICK_KEYS='v|v|\r'; fi
}
# WHO LOG KEY: ABRoot's mount-sys (its init hook, before systemd: on the
# console only, never in the journal; it mounts /var and /etc's overlay,
# and repairs the root's links and paths, RepairRootIntegrity, when they're
# broken) must end in its success line in this boot's console log, and
# print nothing of a repair; nor may the journal (ABROOT_REPAIRS).
mountsys_check() {
    local who=$1 log=$2 key=$3 seg rep
    seg=$(sed 's/\x1b\[[0-9;]*[A-Za-z]//g' "$log" 2>/dev/null | tr -d '\r' |
        awk '/Running hook: \/usr\/share\/init\.d\/100-abroot-mount-sys\.sh/ {on = 1} on {print} on && /Successfully ran hook: \/usr\/share\/init\.d\/100-abroot-mount-sys\.sh/ {exit}')
    rep=$(sshk "$key" "$ABROOT_REPAIRS" 2>/dev/null)
    printf '== %s\n%s\n== journal\n%s\n' "${log##*/}" "$seg" "$rep" > "$R/logs/$who-mountsys.txt"
    if grep -q 'The system mounts have been performed successfully' <<<"$seg" && ! grep -qiE 'repair|integrity check' <<<"$seg" && [[ -z $rep ]]; then
        ok "abroot: $who: mount-sys mounted the system, no integrity repair (console, journal)"
    else
        bad "abroot: $who: mount-sys: $(grep -iE 'repair|integrity check|error|fail' <<<"$seg$rep" | head -3 | tr '\n' ' ')$(grep -q 'performed successfully' <<<"$seg" || echo "no success line ")(logs/$who-mountsys.txt)"
    fi
}
# The source's baseline: its default boot (up already), the other root, and
# its default again for the dump.  Exit 1 when the run can go no further.
abroot_source() {
    local L=$R/logs a o
    sshk "$gkey" "$ABROOT_MENU" > "$L/source-abroot-menu.txt" 2>&1
    abroot_pick || { bad "abroot: the source's master grub.cfg hasn't a default of 0 or 1 and two entries: $(tr '\n' ' ' < "$L/source-abroot-menu.txt")"; return 1; }
    sshk "$gkey" "$ABROOT_STATE" > "$L/source-abroot-first.txt" 2>&1
    mountsys_check source "$L/source-serial.log" "$gkey"
    vm_down "$gkey"
    GRUB_PICK=$PICK_RE GRUB_KEYS=$PICK_KEYS vm_up "${src[0]}" "$L/source-serial-other.log" "${fw[@]}" "${svfw[@]}" "${extra_src[@]}"
    vm_wait "$gkey" || { bad "abroot: the source's \"$ABROOT_OTHER\" boots (no SSH; logs/source-serial-other.log)"; return 1; }
    grep -q 'console-keys.py: typed' "$L/source-serial-other.log" || { bad "abroot: GRUB's menu never showed \"$ABROOT_OTHER\" on the source's serial console"; return 1; }
    sshk "$gkey" "$ABROOT_STATE" > "$L/source-abroot-other.txt" 2>&1
    bootstate "$gkey" > "$L/source-other-bootstate.txt"
    mountsys_check source-other "$L/source-serial-other.log" "$gkey"
    vm_down "$gkey"
    vm_up "${src[0]}" "$L/source-serial-3.log" "${fw[@]}" "${svfw[@]}" "${extra_src[@]}"
    vm_wait "$gkey" || { bad "abroot: the source boots its default entry again (no SSH; logs/source-serial-3.log)"; return 1; }
    sshk "$gkey" "$ABROOT_STATE" > "$L/source-abroot.txt" 2>&1
    a=$(sed -n 's/^root //p' "$L/source-abroot.txt") o=$(sed -n 's/^root //p' "$L/source-abroot-other.txt")
    if [[ -z $a || -z $o || $a == "$o" ]] || ! grep -qx "present $a" "$L/source-abroot.txt" || ! grep -qx "present $o" "$L/source-abroot-other.txt"; then
        bad "abroot: the source's \"$ABROOT_OTHER\" booted ${o:-nothing}, its default ${a:-nothing} ($(grep '^present' "$L/source-abroot-other.txt"), $(grep '^present' "$L/source-abroot.txt"))"; return 1
    fi
    cmp -s "$L/source-abroot-first.txt" "$L/source-abroot.txt" ||
        { bad "abroot: the source's default boot after its other root isn't its first: $(diff "$L/source-abroot-first.txt" "$L/source-abroot.txt" | grep '^[<>]' | head -2 | tr '\n' ' ')"; return 1; }
    [[ $(sed -n 's/^sha256 //p' "$L/source-abroot-other.txt") == "$(sed -n 's/^sha256 //p' "$L/source-abroot.txt")" ]] ||
        { bad "abroot: the source's boot of \"$ABROOT_OTHER\" changed grub.cfg or grub.cfg.future"; return 1; }
    ok "abroot: the source booted its default $a ($(sed -n 's/^bko-root //p' "$L/source-abroot.txt")) and \"$ABROOT_OTHER\" $o ($(sed -n 's/^bko-root //p' "$L/source-abroot-other.txt")) picked on GRUB's serial menu; grub.cfg as it was"
}
# The restored disk's two boots against the baseline: the default (booted
# already, and dumped), then the other root, the restored disk alone again
# (a fresh --snapshot boot).  The other's boot state may have no line the
# source's lacked.
abroot_verify() {
    local L=$R/logs
    sshk "$gkey" "$ABROOT_STATE" > "$L/restored-abroot.txt" 2>&1
    if cmp -s "$L/source-abroot.txt" "$L/restored-abroot.txt"; then
        ok "abroot: the restored default boot is the source's ($(sed -n 's/^root //p' "$L/restored-abroot.txt"): abroot status, /abimage.abr, /etc/bko-root, the command line, grub.cfg and grub.cfg.future unwritten)"
    else
        bad "abroot: the restored default boot isn't the source's: $(diff "$L/source-abroot.txt" "$L/restored-abroot.txt" | grep '^[<>]' | head -4 | tr '\n' ' ')"
    fi
    mountsys_check restored "$L/restored-serial.log" "$gkey"
    vm_down "$gkey"
    abroot_pick || { bad "abroot: no other entry in the source's logs/source-abroot-menu.txt"; return; }
    GRUB_PICK=$PICK_RE GRUB_KEYS=$PICK_KEYS vm_up "${dst[0]}" "$L/restored-serial-other.log" --snapshot "${fw[@]}" "${dvfw[@]}" "${BS2_VFW[@]}" "${extra_dst[@]}"
    vm_wait "$gkey" || { bad "abroot: the restored \"$ABROOT_OTHER\" boots (no SSH; logs/restored-serial-other.log)"; return; }
    grep -q 'console-keys.py: typed' "$L/restored-serial-other.log" || bad "abroot: GRUB's menu never showed \"$ABROOT_OTHER\" on the restored disk's serial console"
    sshk "$gkey" "$ABROOT_STATE" > "$L/restored-abroot-other.txt" 2>&1
    if cmp -s "$L/source-abroot-other.txt" "$L/restored-abroot-other.txt"; then
        ok "abroot: the restored \"$ABROOT_OTHER\" boot is the source's ($(sed -n 's/^root //p' "$L/restored-abroot-other.txt"): abroot status, /abimage.abr, /etc/bko-root, the command line, grub.cfg and grub.cfg.future unwritten)"
    else
        bad "abroot: the restored \"$ABROOT_OTHER\" boot isn't the source's: $(diff "$L/source-abroot-other.txt" "$L/restored-abroot-other.txt" | grep '^[<>]' | head -4 | tr '\n' ' ')"
    fi
    mountsys_check restored-other "$L/restored-serial-other.log" "$gkey"
    bootstate "$gkey" > "$L/restored-other-bootstate.txt"
    if diff -q <(head -1 "$L/source-other-bootstate.txt") <(head -1 "$L/restored-other-bootstate.txt") >/dev/null \
        && [[ -z $(comm -13 <(tail -n +2 "$L/source-other-bootstate.txt") <(tail -n +2 "$L/restored-other-bootstate.txt")) ]]; then
        ok "abroot: \"$ABROOT_OTHER\"'s boot state as the source's ($(head -1 "$L/restored-other-bootstate.txt"))"
    else
        bad "abroot: \"$ABROOT_OTHER\"'s boot state: source $(tr '\n' ' ' < "$L/source-other-bootstate.txt"), restored $(tr '\n' ' ' < "$L/restored-other-bootstate.txt")"
    fi
}

# Proxmox (docs/design/proxmox.md): proxmox-boot-tool's ESPs, listed by volume
# ID in /etc/kernel/proxmox-boot-uuids, are mounted by nothing, so neither the
# backup nor fsmeta's record of MP holds them.  Each one on the disks vm-mount
# attached for MP is mounted read-only by itself and recorded by fsmeta.py as
# esp/WHO-ID.json, beside the list as esp/WHO-uuids, for judge.py esp.  A tree
# without the list has none.  Exit 1 when an ESP couldn't be found or read.
# TrueNAS's ESPs (docs/design/truenas.md) are mounted by nothing too: each
# vfat partition of GPT type EF00 on those disks (its installer's p2), listed
# by volume ID in disk order in esp/WHO-uuids, with esp/WHO-kind "truenas"
# for judge.py esp's rules.
is_truenas() { [[ -f $1/data/freenas-v1.db && -f $1/usr/local/bin/truenas-grub.py ]]; }   # MP: a TrueNAS tree
esp_record() { local mp=$1 who=$2 st u p q at f=0 devs=()
    st=/run/vm-mount/$(systemd-escape --path "$(realpath "$mp")")
    mapfile -t devs < <(awk 'NR == 1 { print; next } $1 == "nbd" { print $2 }' "$st")
    if [[ -f $mp/etc/kernel/proxmox-boot-uuids ]]; then
        mkdir -p "$R/esp" "$R/tmp"; rm -f "$R/esp/$who-"*
        cp "$mp/etc/kernel/proxmox-boot-uuids" "$R/esp/$who-uuids"
    elif is_truenas "$mp"; then
        mkdir -p "$R/esp" "$R/tmp"; rm -f "$R/esp/$who-"*
        echo truenas > "$R/esp/$who-kind"
        for q in $(for d in "${devs[@]}"; do compgen -G "${d}p*"; done); do
            [[ $(lsblk -dno PARTTYPE "$q" 2>/dev/null) == c12a7328-f81f-11d2-ba4b-00a0c93ec93b && $(blkid -s TYPE -o value "$q" 2>/dev/null) == vfat ]] &&
                blkid -s UUID -o value "$q"
        done > "$R/esp/$who-uuids"
        [[ -s $R/esp/$who-uuids ]] || { echo "$who: TrueNAS, and no vfat ESP on ${devs[*]}"; rm -f "$R/esp/$who-"*; return 0; }
    else
        return 0
    fi
    for u in $(cat "$R/esp/$who-uuids"); do
        p=""
        for q in $(for d in "${devs[@]}"; do compgen -G "${d}p*"; done); do
            [[ $(blkid -s TYPE -o value "$q" 2>/dev/null) == vfat && $(blkid -s UUID -o value "$q" 2>/dev/null) == "$u" ]] && { p=$q; break; }
        done
        [[ -n $p ]] || { echo "$who: no vfat partition with the ID $u on ${devs[*]}"; f=1; continue; }
        at=$R/tmp/esp-$who-$u; mkdir -p "$at"
        if mount -t vfat -o ro,nodev,nosuid,noexec "$p" "$at"; then
            python3 "$T/compare/fsmeta.py" record "$at" "$R/esp/$who-$u.json" || f=1
            echo "$who: ESP $u on $p: $(find "$at" -type f | wc -l) files"
            umount "$at"
        else
            echo "$who: can't mount $p (ESP $u) read-only"; f=1
        fi
        rmdir "$at"
    done
    return $f
}
# Whether the source's proxmox-boot-tool keeps more than one ESP, or its
# TrueNAS boot-pool is on more than one disk (a mirror: GRUB on each).
multi_esp() { python3 -I -c 'import json, sys
d = json.load(open(sys.argv[1]))
tn = (d.get("boot") or {}).get("truenas") and sum(1 for k in (d.get("storage") or {}).get("disks") or []
    if any((p.get("content") or {}).get("type") == "zfs_member" and (p.get("content") or {}).get("label") == "boot-pool" for p in k.get("partitions") or []))
sys.exit(0 if len(((d.get("boot") or {}).get("proxmox_boot") or {}).get("uuids") or []) > 1 or (tn or 0) > 1 else 1)' "$R/dumps/src.json" 2>/dev/null; }
# Each restored disk booted by itself, the others detached (the spec's
# --boot-each-disk): firmware that finds one disk of the pair must still boot
# from its ESP.  The pool comes up DEGRADED, and no worse (no vdev FAULTED,
# no data errors); the boot state must be the source's, as for the restored
# disks' boot together.  A fresh copy of the guest's NVRAM each, as that boot.
# A disk HT_POOLS keeps (a data pool's) stays attached to each, and isn't
# booted alone; on TrueNAS its middleware must import that pool and list
# the source's shares (truenas_disk_check).
boot_each_disk() { local i k vfw pools keep=()
    for ((k = 0; k < n; k++)); do [[ -z ${KEEP[k]:-} ]] || keep+=(--disk "${dst[k]}"); done
    for ((i = 0; i < n; i++)); do
        [[ -z ${KEEP[i]:-} ]] || continue
        gvars "restored-disk$i"; vfw=("${VFW[@]}")
        CONSOLE_KEY=$ckey vm_up "${dst[i]}" "$R/logs/restored-disk$i-serial.log" --snapshot "${fw[@]}" "${vfw[@]}" "${keep[@]}"
        if ! vm_wait "$gkey"; then bad "restored disk $i boots alone, the others detached (no SSH; logs/restored-disk$i-serial.log)"; vm_down "$gkey"; continue; fi
        if [[ -n $GVARS ]]; then sshk "$gkey" "$NVBOOT" > "$R/logs/restored-disk$i-nvram-boot.txt" 2>&1; else echo "no NVRAM of its own: a fresh store" > "$R/logs/restored-disk$i-nvram-boot.txt"; fi
        [[ ! -f $R/logs/source-truenas.txt ]] || truenas_disk_check "$i"
        sshk "$gkey" 'zpool list -H -o name,health; echo; zpool status -v' > "$R/logs/restored-disk$i-zpool.txt" 2>&1
        bootstate "$gkey" > "$R/logs/restored-disk$i-bootstate.txt"
        vm_down "$gkey"
        pools=$(awk -F'\t' '/^$/ { exit } NF == 2 { print $1 " " $2 }' "$R/logs/restored-disk$i-zpool.txt" | tr '\n' ',' | sed 's/,$//; s/,/, /g')
        if ! grep -qP '^\S+\tDEGRADED$' "$R/logs/restored-disk$i-zpool.txt" || grep -qP '^\S+\t(FAULTED|UNAVAIL|SUSPENDED|OFFLINE)$' "$R/logs/restored-disk$i-zpool.txt" \
            || grep -qE '^ +(errors: [1-9]|.*(FAULTED|corrupted))' "$R/logs/restored-disk$i-zpool.txt" || ! grep -q '^errors: No known data errors' "$R/logs/restored-disk$i-zpool.txt"; then
            bad "restored disk $i alone: its pool isn't just DEGRADED: ${pools:-none} (logs/restored-disk$i-zpool.txt)"
        elif ! diff -q <(head -1 "$R/logs/source-bootstate.txt") <(head -1 "$R/logs/restored-disk$i-bootstate.txt") >/dev/null \
            || [[ -n $(comm -13 <(tail -n +2 "$R/logs/source-bootstate.txt") <(tail -n +2 "$R/logs/restored-disk$i-bootstate.txt")) ]]; then
            bad "restored disk $i alone: boot state: source $(tr '\n' ' ' < "$R/logs/source-bootstate.txt"), restored $(tr '\n' ' ' < "$R/logs/restored-disk$i-bootstate.txt")"
        else
            ok "restored disk $i boots alone, the others detached: ${pools}, boot state as the source's ($(head -1 "$R/logs/restored-disk$i-bootstate.txt")), through $(head -1 "$R/logs/restored-disk$i-nvram-boot.txt")"
        fi
    done
}

# TrueNAS (docs/design/truenas.md, T6): the middleware's view of a boot, as
# midclt (root's, over SSH) gives it once system.ready says so: the
# version, each boot environment (by ID, active, activated, keep,
# can_activate: not its dataset's size or creation time, which a restore
# makes anew), the pools and their health and the SMB shares.  TN_READY
# waits up to 10 min for the middleware (2-4 min after a boot).
TN_READY='for i in $(seq 120); do case $(midclt call system.ready 2>/dev/null) in True|true) exit 0 ;; esac; sleep 5; done; echo "system.ready not true after 10 min" >&2; exit 1'
TN_STATE='echo "version $(midclt call system.version 2>/dev/null)"
midclt call boot.environment.query 2>/dev/null | python3 -c "import json, sys
for b in sorted(json.load(sys.stdin), key=lambda b: b[\"id\"]):
    print(\"be\", b[\"id\"], *(k + \"=\" + str(b.get(k)).lower() for k in (\"active\", \"activated\", \"keep\", \"can_activate\")))"
zpool list -H -o name,health | sort | sed "s/^/pool /; s/\t/ /"
midclt call sharing.smb.query 2>/dev/null | python3 -c "import json, sys
for x in sorted(json.load(sys.stdin), key=lambda x: x[\"name\"]):
    print(\"share\", x[\"name\"], x[\"path\"], \"enabled=\" + str(x.get(\"enabled\")).lower())"'
# The config database's own check, on the running system (read-only).
TN_DBCHECK='python3 -c "import sqlite3; print(sqlite3.connect(\"file:/data/freenas-v1.db?mode=ro\", uri=True).execute(\"PRAGMA integrity_check\").fetchone()[0])"'
# The source's baseline: its middleware's view (logs/source-truenas.txt).
# Exit 1 when the run can go no further.
truenas_source() {
    TMO=700 sshk "$gkey" "$TN_READY" 2> "$R/logs/source-truenas.err" || { bad "TrueNAS: the source's middleware: $(tail -1 "$R/logs/source-truenas.err")"; return 1; }
    sshk "$gkey" "$TN_STATE" > "$R/logs/source-truenas.txt" 2>> "$R/logs/source-truenas.err"
    grep -q '^be .* active=true' "$R/logs/source-truenas.txt" || { bad "TrueNAS: the source's boot.environment.query shows no active BE (logs/source-truenas.txt)"; return 1; }
    ok "TrueNAS: the source's middleware ready: $(grep -c '^be ' "$R/logs/source-truenas.txt") BEs, $(head -1 "$R/logs/source-truenas.txt")"
}
# From a tree vm-mount mounted at MP (WHO: src, dst): grub.cfg's menu, each
# menuentry's and submenu's title and each linux line's root=ZFS=, in order
# (logs/WHO-grubmenu.txt), and the sha256 of each BE's config database and
# its key, the inactive BEs' under run/bko-be (logs/WHO-db.txt).
truenas_record() { local mp=$1 who=$2 f
    is_truenas "$mp" || return 0
    sed -nE "s/^[[:space:]]*(menuentry|submenu) '([^']*)'.*/\1 \2/p; s/^[[:space:]]*linux[[:space:]].*(root=ZFS=[^[:space:]]+).*/  \1/p" "$mp/boot/grub/grub.cfg" > "$R/logs/$who-grubmenu.txt" 2>&1
    for f in data/freenas-v1.db data/pwenc_secret "$mp"/run/bko-be/*/data/freenas-v1.db "$mp"/run/bko-be/*/data/pwenc_secret; do
        f=${f#"$mp/"}; [[ -f $mp/$f ]] && echo "$(sha256sum < "$mp/$f" | cut -d' ' -f1) $f"
    done > "$R/logs/$who-db.txt"
}
# The restored tree's records against the source's (verify, before boot).
truenas_judge_tree() {
    [[ -f $R/logs/src-db.txt ]] || return 0
    if [[ -s $R/logs/src-db.txt ]] && cmp -s "$R/logs/src-db.txt" "$R/logs/dst-db.txt"; then
        ok "TrueNAS: every BE's config database and key byte for byte before boot: $(awk '{print $2}' "$R/logs/dst-db.txt" | tr '\n' ' ')"
    else
        bad "TrueNAS: config databases and keys: $(diff "$R/logs/src-db.txt" "$R/logs/dst-db.txt" | grep '^[<>]' | head -4 | tr '\n' ' ') (logs/src-db.txt, dst-db.txt)"
    fi
    if grep -q '^menuentry ' "$R/logs/src-grubmenu.txt" 2>/dev/null && cmp -s "$R/logs/src-grubmenu.txt" "$R/logs/dst-grubmenu.txt"; then
        ok "TrueNAS: grub.cfg's menu as the source's, in order: $(grep -c '^menuentry ' "$R/logs/dst-grubmenu.txt") entries and $(grep -c '^submenu ' "$R/logs/dst-grubmenu.txt") submenus, $(grep -c 'root=ZFS=' "$R/logs/dst-grubmenu.txt") root=ZFS= lines ($(grep -o 'root=ZFS=[^ ]*' "$R/logs/dst-grubmenu.txt" | uniq | sed 's/root=ZFS=//' | tr '\n' ' ')in order)"
    else
        bad "TrueNAS: grub.cfg's menu: $(diff "$R/logs/src-grubmenu.txt" "$R/logs/dst-grubmenu.txt" 2>&1 | grep '^[<>]' | head -4 | tr '\n' ' ') (logs/src-grubmenu.txt, dst-grubmenu.txt)"
    fi
}
# The restored boot against the baseline (after its dump): the middleware's
# view the source's, the config database's integrity check, then the oldest
# BE activated (boot.environment.activate) and booted: its /etc/version, its
# root, and the middleware naming it active.  The boot is the restored
# disk's --snapshot one, so the copy isn't changed.
truenas_verify() {
    local L=$R/logs old v
    if ! TMO=700 sshk "$gkey" "$TN_READY" 2> "$L/restored-truenas.err"; then bad "TrueNAS: the restored middleware: $(tail -1 "$L/restored-truenas.err")"; return; fi
    sshk "$gkey" "$TN_STATE" > "$L/restored-truenas.txt" 2>> "$L/restored-truenas.err"
    if cmp -s "$L/source-truenas.txt" "$L/restored-truenas.txt"; then
        ok "TrueNAS: the restored middleware's view is the source's: $(head -1 "$L/restored-truenas.txt"), $(grep '^be ' "$L/restored-truenas.txt" | awk '{print $2 ($3 == "active=true" ? " (active)" : "")}' | tr '\n' ' ')$(grep '^pool \|^share ' "$L/restored-truenas.txt" | tr '\n' ' ')"
    else
        bad "TrueNAS: the restored middleware's view: $(diff "$L/source-truenas.txt" "$L/restored-truenas.txt" | grep '^[<>]' | head -4 | tr '\n' ' ') (logs/source-truenas.txt, restored-truenas.txt)"
    fi
    v=$(sshk "$gkey" "$TN_DBCHECK" 2>&1)
    if [[ $v == ok ]]; then ok "TrueNAS: /data/freenas-v1.db's PRAGMA integrity_check after boot: ok"; else bad "TrueNAS: /data/freenas-v1.db's PRAGMA integrity_check after boot: ${v:0:200}"; fi
    old=$(awk '$1 == "be" {print $2}' "$L/source-truenas.txt" | sort -V | head -1)
    if grep -q "^be $old active=true" "$L/source-truenas.txt"; then say "note: TrueNAS: the oldest BE $old is the active one: no other to activate"; return; fi
    sshk "$gkey" "midclt call boot.environment.activate '{\"id\": \"$old\"}'" > "$L/restored-activate.txt" 2>&1 ||
        { bad "TrueNAS: boot.environment.activate $old: $(tail -1 "$L/restored-activate.txt")"; return; }
    sshk "$gkey" 'zpool get -H -o value bootfs boot-pool' >> "$L/restored-activate.txt" 2>&1
    vm_reboot "$gkey" || { bad "TrueNAS: the restored disk boots its oldest BE $old once activated (no SSH; logs/restored-serial.log)"; return; }
    TMO=700 sshk "$gkey" "$TN_READY" 2>> "$L/restored-truenas.err" || bad "TrueNAS: the middleware on BE $old: $(tail -1 "$L/restored-truenas.err")"
    sshk "$gkey" 'echo "version $(cat /etc/version)"; echo "root $(findmnt -no SOURCE /)"; echo "bootfs $(zpool get -H -o value bootfs boot-pool)"' > "$L/restored-oldest.txt" 2>&1
    sshk "$gkey" "$TN_STATE" >> "$L/restored-oldest.txt" 2>&1
    if grep -qx "version $old" "$L/restored-oldest.txt" && grep -qx "root boot-pool/ROOT/$old" "$L/restored-oldest.txt" && grep -q "^be $old active=true" "$L/restored-oldest.txt"; then
        ok "TrueNAS: the oldest BE activated and booted: /etc/version $old, / boot-pool/ROOT/$old, the middleware names it active"
    else
        bad "TrueNAS: the oldest BE $old activated and booted: $(grep -E '^(version|root|bootfs) |active=true' "$L/restored-oldest.txt" | tr '\n' ' ')(logs/restored-oldest.txt)"
    fi
}
# Restored disk I booted alone (boot_each_disk): the middleware imports the
# kept data pools and lists the source's shares.
truenas_disk_check() { local i=$1 L=$R/logs
    TMO=700 sshk "$gkey" "$TN_READY" 2> "$L/restored-disk$i-truenas.err" || { bad "TrueNAS: restored disk $i alone: the middleware: $(tail -1 "$L/restored-disk$i-truenas.err")"; return; }
    sshk "$gkey" "$TN_STATE" > "$L/restored-disk$i-truenas.txt" 2>> "$L/restored-disk$i-truenas.err"
    if diff -q <(grep -E '^(pool|share) ' "$L/source-truenas.txt" | grep -v '^pool boot-pool ') <(grep -E '^(pool|share) ' "$L/restored-disk$i-truenas.txt" | grep -v '^pool boot-pool ') >/dev/null; then
        ok "TrueNAS: restored disk $i alone: $(grep -E '^(pool|share) ' "$L/restored-disk$i-truenas.txt" | tr '\n' ' ')"
    else
        bad "TrueNAS: restored disk $i alone: data pools and shares: $(grep -E '^(pool|share) ' "$L/restored-disk$i-truenas.txt" | tr '\n' ' '), the source's $(grep -E '^(pool|share) ' "$L/source-truenas.txt" | tr '\n' ' ')"
    fi
}

# ZFSBootMenu (docs/design/zfsbootmenu.md, "HoltTest1 1V"; void-zbm,
# void-zbm-enc): keyed on the source dump's boot.zfsbootmenu.  ZBM picks a
# boot environment (BE) at each boot, so each BE is booted: the booted one
# by the boot every guest gets (alone, through the source's NVRAM entries:
# GVARS, as proxmox9), each other one picked from ZBM's menu on the serial
# console by console-keys.py (vm_up's GRUB_PICK), every boot under
# --snapshot.  The source's boots are the baseline (after its dump); the
# restored disk's must show what they did.  On an encrypted pool ZBM asks
# for the passphrase after its countdown (or after Escape opens its menu:
# z7a's logs): console-boot.py types it (the
# plain boots), or console-keys.py --passphrase (the menu picks: the two
# can't be nested); each boot must have had that one prompt, no second:
# the BE's initramfs opens the pool with its key file, printing its own
# "Encrypted ZFS password for zroot: [1/5]:" once with nothing typed and no
# retry, the key available from that file (judge.py zbm-boot).  judge.py zbm then
# holds the images, every BE, the pool's properties and the key settings.
# ZBM_STATE is what a boot shows: the guest's own /etc/bko-be (make-void-zfs.sh
# writes the BE's name there), the kernel, / and the command line, and the
# key of /'s encryption root ("key -" with none): its keylocation, keystatus
# and whether a file:// key is in the initramfs ZBM loaded with that kernel
# (lsinitrd's listing only, never the file).
ZBM_STATE='echo "bko-be $(cat /etc/bko-be 2>/dev/null || echo none)"
echo "uname $(uname -r)"
echo "root $(findmnt -no SOURCE /)"
echo "cmdline $(cat /proc/cmdline)"
e=$(zfs get -H -o value encryptionroot "$(findmnt -no SOURCE /)" 2>/dev/null)
if [ -z "$e" ] || [ "$e" = - ]; then echo "key -"; else
    l=$(zfs get -H -o value keylocation "$e"); i=no
    case $l in file:///*) lsinitrd "/boot/initramfs-$(uname -r).img" 2>/dev/null | awk -v p="${l#file:///}" "\$NF == p {f = 1} END {exit !f}" && i=yes ;; esac
    echo "key $e $l $(zfs get -H -o value keystatus "$e") initramfs:$i"
fi'
# The menu's keys (Z1, "ZBM over the serial console"): Escape during the
# countdown, which shows "seconds" (plain text: ZBM's colour codes split
# anything longer across reads), 3.5 s for fzf's menu to draw, Down once a
# line, 1 s, Enter: console-keys.py's chunks are 0.5 s apart.  The menu
# lists the BEs sorted by name with the cursor on the first (Z1: void, then
# void-6.18; void is also the default, so which of the two the cursor starts
# on isn't settled by these guests).
ZBM_PICK_RE='seconds'
zbm_pick_keys() { local i=$1 k='\x1b|||||||'; for ((; i > 0; i--)); do k+='\x1b[B|'; done; printf '%s' "$k|\\r"; }
# Whether a dump (FILE) is a ZFSBootMenu source's.
zbm_is() { python3 -I -c 'import json, sys
sys.exit(0 if ((json.load(open(sys.argv[1])).get("boot") or {}).get("zfsbootmenu") or {}).get("bes") else 1)' "$1" 2>/dev/null; }
# From the source's dump, logs/source-zbm.tsv: "prompts N" (1 when a BE's
# pool has a passphrase encryption root: ZBM's prompt, else 0), then a line
# a BE: be, its line in the menu (0 first, by dataset name), name, dataset,
# bootfs and mounted (1/0), its newest kernel (the one ZBM boots), and the
# words of its own commandline property (%{parent} left out).
zbm_list() { python3 -I - "$R/dumps/src.json" <<'PY'
import json, re, sys
d = json.load(open(sys.argv[1]))
bes = d["boot"]["zfsbootmenu"]["bes"]
pools = {b.get("pool") for b in bes}
dss = (((d.get("storage") or {}).get("zfs") or {}).get("tools") or {}).get("datasets") or []
enc = any(x.get("encryptionroot") == x.get("name") and x.get("keyformat") == "passphrase" and str(x.get("name")).split("/")[0] in pools for x in dss)
print(f"prompts {1 if enc else 0}")
key = lambda v: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", v)]
order = sorted(b["dataset"] for b in bes)
for b in bes:
    ks = sorted((k.removeprefix("vmlinuz-") for k in b.get("kernels") or [] if k.startswith("vmlinuz-")), key=key)
    words = " ".join(w for w in str(b.get("commandline") or "").split() if w != "%{parent}")
    print("\t".join(["be", str(order.index(b["dataset"])), b["name"], b["dataset"], str(int(bool(b.get("bootfs")))),
                     str(int(bool(b.get("mounted")))), ks[-1] if ks else "-", words]))
PY
}
# WHO NAME DATASET KVER WORDS LOG PROMPTS: WHO's boot of BE NAME (its
# logs/WHO-zbm-NAME.txt, and its console LOG) against what the BE must
# boot: /etc/bko-be NAME (or none), its newest kernel, root=zfs:DATASET and
# the BE's own words on the command line, and judge.py zbm-boot's
# prompts: ZBM's line booting that kernel for DATASET on the console, before
# it PROMPTS of ZBM's passphrase prompts, after it nothing typed and (on an
# encrypted pool) the initramfs's one load-key, no retry, the key loaded
# from its file, which is in that initramfs (logs/WHO-zbm-NAME-prompts.txt);
# the restored disk's also the source's state.  Exit 1 if any isn't so.
zbm_judge_boot() {
    local who=$1 name=$2 ds=$3 kver=$4 words=$5 log=$6 want=$7 f=$R/logs/$1-zbm-$2.txt why=() w b j=$R/logs/$1-zbm-$2-prompts.txt
    b=$(sed -n 's/^bko-be //p' "$f")
    [[ $b == "$name" || $b == none ]] || why+=("/etc/bko-be ${b:-unread}")
    [[ $(sed -n 's/^uname //p' "$f") == "$kver" ]] || why+=("uname -r $(sed -n 's/^uname //p' "$f"), not $kver")
    for w in "root=zfs:$ds" $words; do
        tr ' ' '\n' <<<"$(sed -n 's/^cmdline //p' "$f")" | grep -qxF -- "$w" || why+=("no $w on the command line")
    done
    python3 -I "$HERE/judge.py" zbm-boot "$f" "$log" "$ds" "$kver" "$want" > "$j" 2>&1 ||
        why+=("$( (grep '^DIFF ' "$j" || tail -1 "$j") | sed 's/^DIFF //' | head -4 | paste -sd ';' - | sed 's/;/; /g') (logs/${j##*/})")
    if [[ $who == restored ]] && ! cmp -s "$R/logs/source-zbm-$name.txt" "$f"; then
        why+=("not the source's boot: $(diff "$R/logs/source-zbm-$name.txt" "$f" | grep '^[<>]' | head -2 | tr '\n' ' ')")
    fi
    if ((${#why[@]})); then
        bad "ZFSBootMenu: the $who boot of $name: $(printf '%s; ' "${why[@]}" | sed 's/; $//') (logs/${f##*/}, ${log##*/})"; return 1
    fi
    ok "ZFSBootMenu: the $who boot of $name: /etc/bko-be $b, $kver, root=zfs:$ds${words:+ and $words}, $(tail -1 "$j" | sed 's/^0 differences; //')$([[ $who == restored ]] && echo ", as the source's")"
}
# WHO IMAGE INDEX NAME [vm-run options...]: boot BE NAME (the menu's line
# INDEX) picked on ZBM's menu, under --snapshot; WHO's state recorded.
# Exit 1 when it didn't come up (or the menu never showed).
zbm_menu_boot() {
    local who=$1 img=$2 idx=$3 name=$4 log; shift 4
    log=$R/logs/$who-zbm-$name-serial.log
    gvars "$who-zbm-$name"
    GRUB_PICK=$ZBM_PICK_RE GRUB_KEYS=$(zbm_pick_keys "$idx") CONSOLE_KEY=$ckey vm_up "$img" "$log" --snapshot "${fw[@]}" "${VFW[@]}" "$@"
    if ! vm_wait "$gkey"; then bad "ZFSBootMenu: the $who's $name, picked on ZBM's menu, boots (no SSH; logs/${log##*/})"; vm_down "$gkey"; return 1; fi
    grep -q 'console-keys.py: typed' "$log" || { bad "ZFSBootMenu: ZBM's countdown (\"$ZBM_PICK_RE\") never showed on the $who's serial console (logs/${log##*/})"; vm_down "$gkey"; return 1; }
    sshk "$gkey" "$ZBM_STATE" > "$R/logs/$who-zbm-$name.txt" 2>&1
    [[ -z $GVARS ]] || sshk "$gkey" "$NVBOOT" > "$R/logs/$who-zbm-$name-nvram-boot.txt" 2>&1
    bootstate "$gkey" > "$R/logs/$who-zbm-$name-bootstate.txt"
    vm_down "$gkey"
}
# The source's baseline, after its dump (the source up in its booted BE):
# that boot's state, then each other BE's boot.  Exit 1 when the run can go
# no further.
zbm_source() {
    local L=$R/logs p b=() l idx name ds bfs mnt kver words
    zbm_list > "$L/source-zbm.tsv" 2> "$L/source-zbm.err" || { bad "ZFSBootMenu: the source dump's boot.zfsbootmenu: $(tail -1 "$L/source-zbm.err")"; return 1; }
    p=$(awk '$1 == "prompts" {print $2}' "$L/source-zbm.tsv")
    mapfile -t b < <(awk -F'\t' '$1 == "be"' "$L/source-zbm.tsv")
    grep -qP '^be\t\d+\t[^\t]+\t[^\t]+\t1\t1\t' "$L/source-zbm.tsv" || { bad "ZFSBootMenu: the source dump has no BE that is the bootfs and booted (logs/source-zbm.tsv)"; return 1; }
    for l in "${b[@]}"; do
        IFS=$'\t' read -r _ idx name ds bfs mnt kver words <<<"$l"
        ((mnt)) || continue
        sshk "$gkey" "$ZBM_STATE" > "$L/source-zbm-$name.txt" 2>&1
        zbm_judge_boot source "$name" "$ds" "$kver" "$words" "$L/source-serial.log" "$p" || return 1
    done
    vm_down "$gkey"
    for l in "${b[@]}"; do
        IFS=$'\t' read -r _ idx name ds bfs mnt kver words <<<"$l"
        ((!mnt)) || continue
        zbm_menu_boot source "${src[0]}" "$idx" "$name" "${extra_src[@]}" || return 1
        zbm_judge_boot source "$name" "$ds" "$kver" "$words" "$L/source-zbm-$name-serial.log" "$p" || return 1
    done
}
# The restored disk (after its dump, up in its booted BE): that boot, then
# each other BE's from the menu, alone through the source's NVRAM entries,
# each held to the source's (its boot state may have no line the source's
# boot of it lacked).
zbm_verify() {
    local L=$R/logs p b=() l idx name ds bfs mnt kver words
    p=$(awk '$1 == "prompts" {print $2}' "$L/source-zbm.tsv")
    mapfile -t b < <(awk -F'\t' '$1 == "be"' "$L/source-zbm.tsv")
    for l in "${b[@]}"; do
        IFS=$'\t' read -r _ idx name ds bfs mnt kver words <<<"$l"
        ((mnt)) || continue
        sshk "$gkey" "$ZBM_STATE" > "$L/restored-zbm-$name.txt" 2>&1
        zbm_judge_boot restored "$name" "$ds" "$kver" "$words" "$L/restored-serial.log" "$p"
    done
    vm_down "$gkey"
    for l in "${b[@]}"; do
        IFS=$'\t' read -r _ idx name ds bfs mnt kver words <<<"$l"
        ((!mnt)) || continue
        zbm_menu_boot restored "${dst[0]}" "$idx" "$name" "${BS2_VFW[@]}" "${extra_dst[@]}" || continue
        zbm_judge_boot restored "$name" "$ds" "$kver" "$words" "$L/restored-zbm-$name-serial.log" "$p"
        if grep -qs 'HD(' "$L/source-nvram-boot.txt" && [[ -n $GVARS ]]; then
            cmp -s "$L/source-nvram-boot.txt" "$L/restored-zbm-$name-nvram-boot.txt" ||
                bad "ZFSBootMenu: the restored boot of $name came through $(head -1 "$L/restored-zbm-$name-nvram-boot.txt"), the source through $(head -1 "$L/source-nvram-boot.txt")"
        fi
        if diff -q <(head -1 "$L/source-zbm-$name-bootstate.txt") <(head -1 "$L/restored-zbm-$name-bootstate.txt") >/dev/null \
            && [[ -z $(comm -13 <(tail -n +2 "$L/source-zbm-$name-bootstate.txt") <(tail -n +2 "$L/restored-zbm-$name-bootstate.txt")) ]]; then
            ok "ZFSBootMenu: $name's boot state as the source's ($(head -1 "$L/restored-zbm-$name-bootstate.txt"))"
        else
            bad "ZFSBootMenu: $name's boot state: source $(tr '\n' ' ' < "$L/source-zbm-$name-bootstate.txt"), restored $(tr '\n' ' ' < "$L/restored-zbm-$name-bootstate.txt")"
        fi
    done
}

phase_source() {
    say "== source: $G"
    # (bs2: the source's preparation stands in for its boot when it can.)
    if [[ $VARIANT == bs2 ]] && bs2_source_reuse; then cp "$R/dumps/src.json" "$S/layout.json"; bs2_after_source; return; fi
    for ((i = 0; i < n; i++)); do
        rm -f "${src[i]}"; qemu-img create -q -f qcow2 -b "${imgs[i]}" -F qcow2 "${src[i]}"
    done
    if [[ -n $GTPM ]]; then
        rm -rf "$R/source.tpm"
        cp -a "$GTPM" "$R/source.tpm" && chmod -R u+w "$R/source.tpm" || { bad "copy the guest's TPM ($GTPM) for the source's boots"; return; }
    fi
    gvars source; local svfw=("${VFW[@]}")
    CONSOLE_KEY=$ckey vm_up "${src[0]}" "$R/logs/source-serial.log" "${fw[@]}" "${svfw[@]}" "${extra_src[@]}"
    vm_wait "$gkey" || { bad "source boots (no SSH)"; vm_down "$gkey"; return; }
    ok "source boots${detached:+, with ${detached##*/} detached}"
    if [[ -n $GVARS ]]; then
        sshk "$gkey" "$NVBOOT" > "$R/logs/source-nvram-boot.txt" 2>&1
        if grep -q 'HD(' "$R/logs/source-nvram-boot.txt"; then
            ok "source booted through its own NVRAM entry (a copy of uefi-vars.fd): $(head -1 "$R/logs/source-nvram-boot.txt")"
        elif ((GV_ENTRY)); then
            bad "source didn't boot through the entry in its uefi-vars.fd: $(head -1 "$R/logs/source-nvram-boot.txt") (logs/source-nvram-boot.txt)"
        else
            say "note: source: on a copy of uefi-vars.fd, with no entry on a disk: $(head -1 "$R/logs/source-nvram-boot.txt")"
        fi
    fi
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
        # A last line AGAIN has it rebooted and run again once a boot
        # settles (a prep of several boots: prep/microos-tu.sh).
        local round=1 rout=$R/logs/source-prep-round.txt
        : > "$R/logs/source-prep.txt"
        while :; do
            scpk "$gkey" "$HT_SOURCE_PREP" root@127.0.0.1:/root/ht-source-prep
            TMO=1800 sshk "$gkey" 'sh /root/ht-source-prep; r=$?; rm -f /root/ht-source-prep; exit $r' > "$rout" 2>&1
            local prc=$?
            { ((round == 1)) || echo "== round $round"; cat "$rout"; } >> "$R/logs/source-prep.txt"
            if ((prc != 0)); then
                bad "preparing the source with ${HT_SOURCE_PREP##*/}$( ((round > 1)) && echo ", round $round") (logs/source-prep.txt): $(tail -1 "$rout")"
                vm_down "$gkey"; return
            fi
            [[ $(tail -1 "$rout") == AGAIN ]] || break
            if ((round >= 10)); then bad "${HT_SOURCE_PREP##*/} still says AGAIN after 10 rounds"; vm_down "$gkey"; return; fi
            if ! vm_reboot "$gkey" || ! vm_settle "$gkey"; then
                bad "source boots and settles again after ${HT_SOURCE_PREP##*/}'s round $round (logs/source-serial.log)"; vm_down "$gkey"; return
            fi
            round=$((round + 1))
        done
        rm -f "$rout"
        ok "source prepared by ${HT_SOURCE_PREP##*/}$( ((round > 1)) && echo " ($round rounds, rebooted between them)")"
        if [[ $(tail -1 "$R/logs/source-prep.txt") == REBOOT ]]; then
            vm_down "$gkey"
            CONSOLE_KEY=$gpass vm_up "${src[0]}" "$R/logs/source-serial-2.log" "${fw[@]}" "${svfw[@]}" "${extra_src[@]}"
            vm_wait "$gkey" || { bad "source boots again after ${HT_SOURCE_PREP##*/} (no SSH)"; vm_down "$gkey"; return; }
            ok "source rebooted after ${HT_SOURCE_PREP##*/}"
        fi
    fi
    # An ostree source boots its rollback and its default again first: the
    # baseline the restored disk's two boots are held to.
    rm -f "$R/logs/source-ostree"*.txt "$R/logs/source-rollback-bootstate.txt"
    # So does an ABRoot source its other root, then its default again.
    rm -f "$R/logs/source-abroot"*.txt "$R/logs/source-other-bootstate.txt"
    # So does a Guix System source its previous generation, then its current one.
    rm -f "$R/logs/source-guix"*.txt "$R/logs/source-prev-bootstate.txt"
    # And a transactional-update source records its slots (ht1-tu.sh).
    rm -f "$R/logs/source-tu"*.txt "$R/logs/source-hcstate.txt"
    # And a TrueNAS source its middleware's view.
    rm -f "$R/logs/source-truenas.txt"
    # And a ZFSBootMenu source each BE's boot, after its dump (zbm_source).
    rm -f "$R/logs/source-zbm"*
    case $(sshk "$gkey" 'if [ -e /run/ostree-booted ]; then echo ostree; elif [ -f /usr/share/abroot/abroot.json ] && command -v abroot >/dev/null; then echo abroot; elif [ -d /var/guix/profiles ] && command -v guix >/dev/null; then echo guix; elif command -v tukit >/dev/null && findmnt -no OPTIONS / | tr , "\n" | grep -qx ro; then echo tu; elif [ -f /data/freenas-v1.db ] && command -v midclt >/dev/null; then echo truenas; else echo plain; fi') in
        ostree) ostree_source || { vm_down "$gkey"; return; } ;;
        abroot) abroot_source || { vm_down "$gkey"; return; } ;;
        guix) guix_source || { vm_down "$gkey"; return; } ;;
        tu) tu_source || { vm_down "$gkey"; return; } ;;
        truenas) truenas_source || { vm_down "$gkey"; return; } ;;
        plain) ;;
        *) bad "source: no answer over SSH to whether it is ostree"; vm_down "$gkey"; return ;;
    esac
    bootstate "$gkey" > "$R/logs/source-bootstate.txt"
    sshk "$gkey" "$LVMID" > "$R/logs/source-lvmid.txt" 2>&1
    ((mok || sbreq)) && sshk "$gkey" "$SBSTATE" > "$R/logs/source-sbstate.txt" 2>&1
    selinux_record source "$gkey"
    ((solus)) && sshk "$gkey" "$SOLUS_LOADER" > "$R/logs/source-loader.txt" 2>&1
    ((slack)) && sshk "$gkey" "$SLACK_ROOT" > "$R/logs/source-root.txt" 2>&1
    TMO=1800 sshk "$gkey" "$SNAPS" > "$R/logs/source-snaps.txt" 2>&1
    snapmgr_record source "$gkey"
    sshk "$gkey" "$HEALTH" 2>&1 | grep -v 'bko-holt/load.log' > "$R/logs/source-health.txt"
    solus_mount_boot source "$gkey" || { vm_down "$gkey"; return; }
    scpk "$gkey" "$S/bko-holt" root@127.0.0.1:/root/bko-holt
    # A guest whose disk's key is sealed to its own TPM (GTPM: aeon) keeps
    # its LUKS2 headers (dump --keep-luks-header, in /run/bko-holt/luks-headers,
    # RAM, never in the backup): load restores them, so the recovery key
    # and the TPM2 token come back (docs/design/aeon.md, A2).  The files go
    # beside the layout in the share, where load looks for them.
    local dflags=()
    if [[ -n $GTPM ]]; then
        read -ra dflags <<<"$(sshk "$gkey" 'for d in $(blkid -t TYPE=crypto_LUKS -o device); do printf -- "--keep-luks-header %s " "$d"; done')"
    fi
    if sshk "$gkey" /root/bko-holt dump "${dflags[@]}" > "$R/dumps/src.json" 2> "$R/logs/source-dump.err"; then
        ok "dump on the live source${dflags[*]:+ (${dflags[*]})}"
    else
        bad "dump on the live source: $(tail -1 "$R/logs/source-dump.err")"
    fi
    if ((${#dflags[@]})); then
        rm -f "$S"/luks2-header-*.img
        if scpk "$gkey" 'root@127.0.0.1:/run/bko-holt/luks-headers/*' "$S/" && compgen -G "$S/luks2-header-*.img" >/dev/null; then
            ok "the source's kept LUKS2 headers copied beside the layout: $(cd "$S" && echo luks2-header-*.img)"
        else
            bad "copy the source's kept LUKS2 headers (/run/bko-holt/luks-headers) to the share"
        fi
    fi
    sshk "$gkey" 'rm -f /root/bko-holt'
    if zbm_is "$R/dumps/src.json"; then zbm_source || { vm_down "$gkey"; return; }; fi
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
    # (A transactional source with an update pending: / is its booted slot, as the live system's: ht1-tu.sh.)
    local -x VM_MOUNT_BTRFS_ROOT; VM_MOUNT_BTRFS_ROOT=$(tu_mount_root)
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
    esp_record "$SRC_MP" src >> "$R/logs/esp-record.log" 2>&1 || bad "record the source's ESPs that nothing mounts (logs/esp-record.log)"
    truenas_record "$SRC_MP" src
    (trap '' TERM INT HUP; exec "$T/vm-umount" "$SRC_MP") >> "$R/logs/backup-mount.log" 2>&1; HT_MOUNTED=
    cp "$RUSTIC" "$S/rustic-bko"; [[ $GARCH != amd64 ]] || cp "$BIN"/*.deb "$S/"   # (bko-holt is there from the run's start; bin/'s debs are amd64's)
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
    ht_load_try "$R/logs/rebuild-$name.out" "$R/logs/rebuild-$name.err" sshk "$rkey" "echo $HT_STARTED; $RHOLT load /mnt/share/layout.json $*"
    local got=$?
    if [[ $got == "$want" ]]; then ok "load $name (exit $got)"; return 0; fi
    bad "load $name: exit $got, want $want: $(grep -v '^$' "$R/logs/rebuild-$name.err" | tail -3 | tr '\n' ' ')"
    return 1
}

# Whether LAYOUT (a dump) has a TYPE anywhere in its storage: exit 0 it has,
# 1 it hasn't; a layout that can't be read counts as having it.
layout_has() {
    python3 -I -c 'import json, sys
def has(v):
    if isinstance(v, dict):
        return any(has(x) for x in v.values())
    if isinstance(v, list):
        return any(has(x) for x in v)
    return v == sys.argv[2]
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
except (OSError, ValueError):
    sys.exit(0)
sys.exit(0 if has(d.get("storage", d) if isinstance(d, dict) else d) else 1)' "$1" "$2"
}

phase_rebuild() {
    say "== rebuild"
    # A rescuer HT_KEEP left up from the last try.
    [[ -z $(ht_qemus "$R") ]] || vm_down "$rkey"
    cp "$RUSTIC" "$S/rustic-bko"; [[ $GARCH != amd64 ]] || cp "$BIN"/*.deb "$S/"   # this run's, when rerun after a fix (bko-holt: copied as the run started)
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
    # HT_POOLS: the disks of the pools it leaves out are kept, not targets.
    pools_keep
    map=() erase=()
    for ((i = 0; i < n; i++)); do
        [[ -z ${KEEP[i]:-} ]] || continue
        map+=(--map "vd${letters[i]}=vd${letters[i+1]}"); erase+=(--erase "/dev/vd${letters[i+1]}")
    done
    ((${#KEEP[@]} == 0)) || say "note: HT_POOLS=$HT_POOLS: disk(s) $(for i in "${!KEEP[@]}"; do echo -n "vd${letters[i]} "; done)kept as the source's (an overlay of each), not rebuilt"
    local disks=()
    for ((i = 0; i < n; i++)); do
        if [[ $VARIANT == bs2 ]]; then
            bs2_target_make "$i" "${dst[i]}" || return; disks+=(--disk "${dst[i]}")
        elif [[ -n ${KEEP[i]:-} ]]; then
            (cd "$R" && qemu-img create -q -f qcow2 -b "src-$i.qcow2" -F qcow2 "dst-$i.qcow2") || { bad "an overlay of src-$i.qcow2 for the kept disk vd${letters[i]}"; return; }
            disks+=(--disk "${dst[i]}")
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
    # the ESP's removable path (GVARS: through the entries of its own store).
    # (bs2: resumed from a saved state, its disks hot-plugged, when it can: ht1-bs2.sh.)
    if [[ $VARIANT == bs2 ]] && bs2_rescuer_resume; then :; else
    IMG_BOOT=${IMG_PLAT:-} vm_up "$R/rescuer.qcow2" "$R/logs/rescuer-serial.log" "${rfw[@]}" --memory 3072 "${disks[@]}" -- \
        -virtfs "local,path=$S,mount_tag=bkoshare,security_model=none,readonly=on"
    vm_wait "$rkey" || { bad "rescuer boots"; vm_down "$rkey"; return; }
    fi
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
    # The share, and what the rescuer lacks: f2fs-tools and jfsutils (the
    # rescuers have both), and bcachefs (make-bcachefs-tools.sh's, built on
    # Debian 13) only when the layout has a bcachefs.  Its deps go in in one
    # apt call, only those the rescuer has no version of (the Ubuntu 26
    # rescuer's own libblkid1, libudev1 and the rest stay as they are), with
    # needrestart held off (it restarted udev, journald and ssh mid-run), and
    # bcachefs must then run, or the setup fails.
    local bch=0; ! layout_has "$S/layout.json" bcachefs || bch=1
    sshk "$rkey" "BCH=$bch"'; mkdir -p /mnt/share && mount -t 9p -o trans=virtio,version=9p2000.L,ro bkoshare /mnt/share && export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 NEEDRESTART_MODE=l && inst() { apt-get install -y -q -o DPkg::Lock::Timeout=600 "$@" >/dev/null; } && { command -v mkfs.f2fs >/dev/null || inst /mnt/share/f2fs-tools_*.deb; } && { command -v mkfs.jfs >/dev/null || inst /mnt/share/jfsutils_*.deb; } && { [ "$BCH" = 0 ] || command -v bcachefs >/dev/null || { [ -f /mnt/share/bcachefs ] || { echo "the layout has a bcachefs, and neither the rescuer nor the share has bcachefs (make-bcachefs-tools.sh)"; exit 1; }; want=; for d in /mnt/share/bcachefs-deps-*.deb; do [ -f "$d" ] || continue; p=$(dpkg-deb -f "$d" Package) || exit 1; dpkg-query -W -f "\${Status}" "$p" 2>/dev/null | grep -q " installed$" || want="$want $d"; done; { [ -z "$want" ] || inst $want; } && install -m 755 /mnt/share/bcachefs /usr/local/sbin/bcachefs && LD_BIND_NOW=1 bcachefs version >/dev/null || { echo "bcachefs: its deps or the binary failed (deps:${want:- none})"; exit 1; }; echo "bcachefs installed (deps:${want:- none})"; }; }' \
        > "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup (share, f2fs-tools, jfsutils$( ((bch)) && echo ", bcachefs")): $(tail -n 1 "$R/logs/rescuer-setup.log")"; vm_down "$rkey"; return; }
    fi
    sshk "$rkey" "mkfs.ext4 -q -F -L ht1decoy $decoy" >> "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup (the decoy's filesystem)"; vm_down "$rkey"; return; }
    ok "rescuer up, share mounted"
    # (bs2's leftovers set: a seed, and what the rescuer found on the disk.)
    [[ $VARIANT != bs2 ]] || bs2_before_load || { vm_down "$rkey"; return; }
    # The first and last MiB of every target and the decoy, before and after
    # the refusals and the dry run, which must write nothing.
    local edges="for d in ${erase[*]#--erase} $decoy; do s=\$(blockdev --getsz \$d); echo \$d \$(dd if=\$d bs=512 count=2048 status=none | md5sum | cut -c1-32) \$(dd if=\$d bs=512 skip=\$((s - 2048)) count=2048 status=none | md5sum | cut -c1-32); done"
    # A line per target (erase holds --erase and the device, so half of it) and
    # the decoy's; before is 0 while that capture couldn't be read.
    local nedges=$(( ${#erase[@]} / 2 + 1 )) before=0
    local flags=()
    # (bs2: tier 1 boots with a fresh NVRAM, so --no-nvram as ever; tier 2
    # keeps one, and the case's own options say.)
    ((uefi)) && [[ $VARIANT != bs2 || $BS2_TIER == 1 ]] && [[ ${HT_NVRAM:-} != 1 ]] && flags+=(--no-nvram)
    [[ $VARIANT != bs2 ]] || flags+=("${BS2_FLAGS[@]}")
    flags+=("${REFORMAT[@]}")
    [[ -z ${HT_POOLS:-} ]] || flags+=(--pools "$HT_POOLS")
    # (A refusal variant always takes these: they are what it runs for.)
    if [[ ${HT_QUICK:-} != 1 || -n $FITONLY ]]; then
    if edges_read "$rkey" "$edges" "$R/logs/edges-before.txt" "$nedges"; then
        before=1
    else
        harness "the targets' and the decoy's first and last MiB couldn't be read before the refusals (logs/edges-before.txt): what they write isn't checked"
    fi
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
        if ((before)); then
            if ! edges_read "$rkey" "$edges" "$R/logs/edges-after.txt" "$nedges"; then
                harness "the targets' and the decoy's first and last MiB couldn't be read after the refusals (logs/edges-after.txt)"
            elif cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
                ok "the refusals left every target's and the decoy's first and last MiB as they were"
            else
                bad "a refusal wrote to a disk (logs/edges-before.txt, edges-after.txt)"
            fi
        fi
        sshk "$rkey" 'cat /run/bko-holt/log/load.log 2>/dev/null' > "$R/logs/rebuild-load.log"
        [[ ${HT_KEEP:-} == 1 ]] && { kept_or_down; return; }
        vm_down "$rkey"
        rm -f "$R/rescuer.qcow2"
        return
    fi
    if [[ ${HT_QUICK:-} != 1 ]]; then
    load_step 0 dry-partition "${map[@]}" "${flags[@]}" --only partition
    vdb_blank "$rkey"
    case $? in
        0) ok "nothing written without --commit" ;;
        1) bad "vdb was written without --commit" ;;
        *) harness "whether vdb was written without --commit couldn't be checked: $VDB_WHY" ;;
    esac
    if ((before)); then
        if ! edges_read "$rkey" "$edges" "$R/logs/edges-after.txt" "$nedges"; then
            harness "the targets' and the decoy's first and last MiB couldn't be read after the refusals and the dry run (logs/edges-after.txt)"
        elif cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
            ok "the refusals and the dry run left every target's and the decoy's first and last MiB as they were"
        else
            bad "a refusal or the dry run wrote to a disk (logs/edges-before.txt, edges-after.txt)"
        fi
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
        ht_status_read "$R/logs/rebuild-status-after-$s.txt" sshk "$rkey" "$RHOLT load /mnt/share/layout.json ${map[*]} --only status" ||
            { harness "the load status after $s couldn't be read: ssh got no answer (logs/rebuild-status-after-$s.txt)"; continue; }
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
    [[ ${HT_NVRAM:-} != 1 ]] || sshk "$rkey" 'efibootmgr -v' > "$R/logs/rescuer-nvram.txt" 2>&1
    [[ $VARIANT != bs2 ]] || bs2_after_load
    [[ ${HT_KEEP:-} == 1 ]] && { kept_or_down; return; }
    # (bs2: QEMU quits once the targets are let go of and synced: ht1-bs2.sh.)
    if [[ $VARIANT == bs2 ]]; then bs2_rescuer_down; else vm_down "$rkey"; fi
    [[ $VARIANT != bs2 ]] || bs2_chain_save
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
    pools_keep   # (HT_POOLS's kept disks: boot_each_disk)
    [[ $VARIANT != bs2 ]] || bs2_verify_begin
    # A rescuer HT_KEEP left up has the targets open.
    [[ -z $(ht_qemus "$R") ]] || vm_down "$rkey"
    mkdir -p "$DST_MP"
    local key=(); [[ -n $gpass ]] && key=(--key "$gpass")
    local -x VM_MOUNT_BTRFS_ROOT; VM_MOUNT_BTRFS_ROOT=$(tu_mount_root)   # (as in backup)
    # (As in backup: vm-mount and vm-umount ignore TERM, INT and HUP.)
    if (trap '' TERM INT HUP; exec "$T/vm-mount" --ro "${extra_dst[@]}" "${key[@]}" "${dst[0]}" "$DST_MP") > "$R/logs/verify-mount.log" 2>&1; then
        HT_MOUNTED=$DST_MP
        python3 "$T/compare/fsmeta.py" record "$DST_MP" "$R/dst-meta.json" "${BS2_FSMETA[@]}" > "$R/logs/fsmeta-dst.log" 2>&1
        [[ $VARIANT != bs2 ]] || bs2_after_dst_record
        esp_record "$DST_MP" dst >> "$R/logs/esp-record.log" 2>&1 || bad "record the restored ESPs that nothing mounts (logs/esp-record.log)"
        truenas_record "$DST_MP" dst
        mok_check "$DST_MP"
        elilo_check "$DST_MP"
        tu_record_dst "$DST_MP"
        (trap '' TERM INT HUP; exec "$T/vm-umount" "$DST_MP") >> "$R/logs/verify-mount.log" 2>&1; HT_MOUNTED=
        python3 "$T/compare/fsmeta.py" compare "$R/src-meta.json" "$R/dst-meta.json" > "$R/logs/fsmeta-compare.txt" 2>&1
        if python3 "$HERE/judge.py" fs "$T/compare/fsmeta.py" "$R/src-meta.json" "$R/dst-meta.json" "${BS2_FSJUDGE[@]}" "${REFORMAT[@]}" > "$R/logs/fsjudge.txt" 2>&1; then
            ok "fsmeta: the files agree but for the boot stage's ($(tail -1 "$R/logs/fsjudge.txt"))"
        else
            bad "fsmeta: $(grep '^DIFF' "$R/logs/fsjudge.txt" | head -5 | tr '\n' ' ')"
        fi
        if [[ -f $R/esp/src-uuids ]]; then
            local whose="proxmox-boot-tool's ESPs"; [[ ! -f $R/esp/src-kind ]] || whose="TrueNAS's ESPs"
            if python3 "$HERE/judge.py" esp "$R/esp" > "$R/logs/espjudge.txt" 2>&1; then
                ok "$whose: the source's by content ($(tail -1 "$R/logs/espjudge.txt"); logs/espjudge.txt)"
            else
                bad "$whose: $(grep '^DIFF' "$R/logs/espjudge.txt" | head -3 | tr '\n' ' ')"
            fi
        fi
        truenas_judge_tree
    else
        bad "mount the restored disk"
    fi
    slack_mbr_check
    # A --guest-tpm guest's restored disk boots on a new machine's TPM (gvars).
    [[ -z $GTPM ]] || { rm -rf "$R/restored.tpm"; mkdir -m 700 "$R/restored.tpm"; }
    gvars restored; local dvfw=("${VFW[@]}")
    CONSOLE_KEY=$ckey vm_up "${dst[0]}" "$R/logs/restored-serial.log" --snapshot "${fw[@]}" "${dvfw[@]}" "${BS2_VFW[@]}" "${extra_dst[@]}"
    if ! vm_wait "$gkey"; then bad "the restored disk boots by itself (no SSH; logs/restored-serial.log)"; vm_down "$gkey"; return; fi
    ok "the restored disk boots by itself, SSH with the guest's key"
    # GRUB's environment block (grubenv's env_block=512+1, in btrfs's
    # reserved area, which mkfs.btrfs zeroes): load's format stage writes
    # it again.  GRUB says so on the console when it can't read it.
    if grep -aqs 'invalid environment block' "$R/logs/restored-serial.log" && ! grep -aqs 'invalid environment block' "$R/logs/source-serial.log"; then
        bad "GRUB on the restored disk: \"invalid environment block\" (logs/restored-serial.log), not on the source's"
    fi
    if grep -qs 'HD(' "$R/logs/source-nvram-boot.txt" && [[ -n $GVARS ]]; then
        sshk "$gkey" "$NVBOOT" > "$R/logs/restored-nvram-boot.txt" 2>&1
        if cmp -s "$R/logs/source-nvram-boot.txt" "$R/logs/restored-nvram-boot.txt"; then
            ok "the restored disk booted through the source's NVRAM entry: $(head -1 "$R/logs/restored-nvram-boot.txt")"
        else
            bad "the restored disk booted through $(head -1 "$R/logs/restored-nvram-boot.txt"), the source through $(head -1 "$R/logs/source-nvram-boot.txt")"
        fi
    fi
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
    sb_check "$gkey"
    selinux_check "$gkey"
    solus_loader_check "$gkey"
    slack_root_check "$gkey"
    snaps_check "$gkey"
    snapmgr_record restored "$gkey"
    [[ $VARIANT != bs2 ]] || bs2_restored_probe "$gkey"
    # A check that failed on the source too isn't the restore's doing.
    sshk "$gkey" "$HEALTH" > "$R/logs/restored-health.txt" 2>&1
    # MISSING-TOOL: a check that never ran, a FAIL whatever the source said.
    local worse; worse=$(comm -13 <(grep '^FAIL' "$R/logs/source-health.txt" 2>/dev/null | sort) <(grep '^FAIL' "$R/logs/restored-health.txt" | sort); grep '^MISSING-TOOL' "$R/logs/restored-health.txt")
    if [[ -z $worse ]] && grep -q 'load log kept' "$R/logs/restored-health.txt"; then
        ok "health: $(grep -v '^FAIL' "$R/logs/restored-health.txt" | tr '\n' ',' | sed 's/,$//; s/,/, /g')$(grep -q '^FAIL' "$R/logs/restored-health.txt" && echo "; as on the source: $(grep '^FAIL' "$R/logs/restored-health.txt" | tr '\n' ' ')")"
    else
        bad "health: $(echo "$worse" | tr '\n' ' ')"
    fi
    scpk "$gkey" "$S/bko-holt" root@127.0.0.1:/root/bko-holt
    solus_mount_boot restored "$gkey"
    sshk "$gkey" '/root/bko-holt dump; rm -f /root/bko-holt' > "$R/dumps/dst.json" 2> "$R/logs/restored-dump.err" || bad "dump of the restored system"
    # An ostree source's baseline (its source phase): the rollback boots too,
    # after the dump, which is of the default boot as the source's was.
    [[ ! -f $R/logs/source-ostree-rollback.txt ]] || ostree_verify
    # So does an ABRoot source's other root.
    [[ ! -f $R/logs/source-abroot-other.txt ]] || abroot_verify
    # So does a Guix System source's previous generation.
    [[ ! -f $R/logs/source-guix-gens.txt ]] || guix_verify
    # And a transactional-update source's slots and health-checker (ht1-tu.sh).
    [[ ! -f $R/logs/source-tu.txt ]] || tu_verify
    # And a TrueNAS source's middleware, and its oldest BE activated and booted.
    [[ ! -f $R/logs/source-truenas.txt ]] || truenas_verify
    # And a ZFSBootMenu source's other BEs, picked from ZBM's menu.
    [[ ! -f $R/logs/source-zbm.tsv ]] || zbm_verify
    if [[ $VARIANT == bs2 ]]; then bs2_restored_down; else vm_down "$gkey"; fi
    # Each disk alone (the spec's --boot-each-disk): a source with an ESP on each disk, or asked for.
    local nb=$((n - ${#KEEP[@]}))   # (HT_POOLS's kept disks aren't booted alone)
    case ${HT_BOOT_EACH_DISK:-auto} in
        1) ((nb > 1)) && boot_each_disk ;;
        auto) ((nb > 1)) && multi_esp && boot_each_disk ;;
    esac
    local how=(); [[ $VARIANT == bigger ]] && how=(--grow $((1 << 30)))
    [[ $VARIANT == degraded ]] && how=(--degraded)
    # The fitting variants: judge.py works the new geometry out as load did,
    # and lets nothing else differ.
    [[ -z $FIT ]] || how=(--fit "$DELTA" --fit-pct "$FIT_PCT")
    # bs2: the restored dump against the drifted layout the case asked for.
    local jsrc=$R/dumps/src.json
    [[ $VARIANT != bs2 ]] || { how=("${BS2_JUDGE[@]}"); jsrc=$R/dumps/drifted.json; }
    # (--reformat first: --degraded has to come right before the dumps.)
    if python3 "$HERE/judge.py" "${REFORMAT[@]}" "${how[@]}" "$jsrc" "$R/dumps/dst.json" > "$R/logs/judge.txt" 2>&1; then
        ok "the dumps agree ($(tail -1 "$R/logs/judge.txt"))"
    else
        bad "the dumps differ: $(grep -c '^DIFF' "$R/logs/judge.txt") fields (logs/judge.txt)"
    fi
    # ZFSBootMenu: its images, every BE, the pool's properties and the key settings.
    if [[ -f $R/logs/source-zbm.tsv ]]; then
        if python3 "$HERE/judge.py" zbm "$jsrc" "$R/dumps/dst.json" "$R/src-meta.json" "$R/dst-meta.json" > "$R/logs/zbmjudge.txt" 2>&1; then
            ok "ZFSBootMenu: images, boot environments, pool and ZBM properties as the source's ($(tail -1 "$R/logs/zbmjudge.txt"); logs/zbmjudge.txt)"
        else
            bad "ZFSBootMenu: $(grep '^DIFF' "$R/logs/zbmjudge.txt" | head -4 | tr '
' ' ')(logs/zbmjudge.txt)"
        fi
    fi
    # TrueNAS: its boot-pool's datasets, properties and bootfs on their own.
    if [[ -f $R/logs/source-truenas.txt ]]; then
        if python3 "$HERE/judge.py" bes "$jsrc" "$R/dumps/dst.json" > "$R/logs/bejudge.txt" 2>&1; then
            ok "TrueNAS: boot-pool's datasets, local and user properties and bootfs as the source's: $(head -1 "$R/logs/bejudge.txt")"
        else
            bad "TrueNAS: boot-pool: $(grep '^DIFF' "$R/logs/bejudge.txt" | head -4 | tr '\n' ' ')(logs/bejudge.txt)"
        fi
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
# (The pin's why stands: the run stopped there.  harness()'s only when every
# FAIL was one.)
[[ -n $IMG_HARNESS ]] || ((hfails < fails)) || IMG_HARNESS=$HARNESS_WHY
say "RESULT: $([[ $fails == 0 ]] && echo PASS || echo "FAIL ($fails${IMG_HARNESS:+; harness: $IMG_HARNESS})")"
[[ $fails == 0 ]]
