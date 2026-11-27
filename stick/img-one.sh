#!/usr/bin/env bash
# Rebuild one platform's rescue image from bko-bin and holt-bin: the release
# image in images/P/, a --test copy in images/P/test/, and boot-test the
# --test copy.  The previous contents of images/P/ go to images/P/prev-STAMP/.
#
#   img-one.sh [--refresh-from PARENT] PLATFORM
#
# PLATFORM: linux, freebsd, netbsd, openbsd, dragonfly, illumos or haiku.
#
# --refresh-from PARENT (docs/design/img-speed.md §4.1, Linux only until I4):
# the two images are made from PARENT's layer with only the binaries swapped
# (build-backotter-layer.sh --refresh-from), not built in full.  PARENT is a
# set's directory (images/P itself means the set this build moves to
# prev-STAMP), or the SHA-256 of its test image (the plan's parent): the first
# of images/P and its prev-*, newest first, whose provenance names it.  The
# parent must have a provenance whose two images are there as it records them,
# whose base (less the media) is the one this build would use, and whose OS
# epoch is within the cadence (C4, C5, D3: img_inputs.py parent; that its test
# image passed ImgTest2 and the panel is the plan's to check).  Once both
# images are listed, img_inputs.py refresh-check holds them to the parent's
# (C2, C3: refresh-check.txt) and refuses (done.exit 1) on any difference
# outside the swap list; the boot tests then run as for a full build, and the
# provenance says mode refresh, names the parent and keeps its OS epoch.
# From outside: BKO_WORK (the work tree, default /home/m/backotter-work: its
# bko-bin/, holt-bin/, server/ and vms/), BKO_IMG_DIR (the images directory,
# default BKO_WORK/images), S (the stick/ to build with, default
# BKO_WORK/stick), ISO and SL (Linux's source ISO, by default the one SL's
# report.txt names, and slimmed base),
# BKO_HOLT_BIN (default BKO_WORK/holt-bin).  stick/img-lanes.sh runs it under
# a reservation.
#
# A flock on images/.lock-PLATFORM is held for the whole build, and done.exit
# is absent until it ends: `t2-host tool img pin` refuses either way.
# images/P/bko-holt.commit gets the 40 hex of holt-bin/P/COMMIT, written after
# bko-bin.sha256 and only when holt-bin/P/bko-holt still is what that file
# records: ImgTest2's refusal looks a bko-holt up by this commit, and an image
# without the file is refused.  A stopped build (TERM, INT, HUP) shuts down the
# FreeBSD builder it started and leaves no done.exit.  Exit 0 with done.exit
# written (0, or how many boot modes failed), 1 on a failed build (done.exit
# 1), 2 on bad usage or when T2_HOST_ROOT is a test's and the images
# directory is the real one, 3 when a build of this platform is already under
# way, 4 when a refresh's parent is refused (nothing moved, no done.exit
# written).
#
# The boot tests (stick/boot-modes.sh, img-speed.md §9): BKO_BOOT_WIDTH modes at
# once (default 1, one after another; img-lanes.sh gives as many as its
# reservation holds at 4.5 GiB each), each mode's boot-MODE.log, .out and .rc
# in test/.  With BKO_BOOT_RAM (a directory in the reservation's RAM disk) the
# test image is first copied there and the modes boot the copy, when it fits in
# BKO_BOOT_RAM_GIB and has test/SHA256SUMS' SHA-256; else the image itself.
#
# Provenance (docs/design/img-speed.md §3, C7): the inputs' digests are taken
# when the build starts (img_inputs.py digest; for FreeBSD once its builder is
# up and fingerprinted), and the build is refused when its stick/ inputs, its
# builder's identity, the ISO, slim, the host tools or the binaries moved by
# the time it ends; BKO_PLAN_BUILDER (the builder identity a plan computed,
# `img_inputs.py identity P`'s line) also refuses a builder that isn't the
# plan's.  The FreeBSD builder's vms/freebsd15-builder/.vm-update.lock is held
# while it runs (make-img-bsd.sh holds a snapshot builder's), and its
# fingerprint (freebsd-version -ku and its build tools' SHA-256) is written to
# vms/freebsd15-builder/fingerprint and must not change during the build.
# Each image's OS file systems are listed in os-tree.txt beside it (test/ for
# the test copy), and provenance.json is written last, before done.exit.
#
# No compression on the farm (img-speed.md §7): images/P/SHA256SUMS (the raw
# release image's line, "SHA256  NAME" as compress-images.sh writes it) and
# images/P/test/SHA256SUMS (the test image's) are written once the build is
# checked, and the images stay raw; stick/img-publish.sh compresses a set for
# download.  BKO_IMG_COMPRESS=1 runs compress-images.sh after a pass, as
# before (it adds the .xz lines).  After a pass the D6 retention rule runs
# (stick/img-retain.sh: images/P keeps the last two prev-* sets, under this
# build's own lock); BKO_IMG_RETAIN=0 skips it.
set -u
# The boot modes each image is tested in (img_inputs.py's tests digest reads these two lines).
MODES_linux="bios uefi uefi-secureboot cdrom"
MODES_other="bios uefi"
REFRESH=""
if [[ ${1:-} == --refresh-from ]]; then REFRESH=${2:-}; shift 2 || exit 2; fi
P=${1:-}
case $P in linux|freebsd|netbsd|openbsd|dragonfly|illumos|haiku) ;; *) echo "img-one: usage: img-one.sh [--refresh-from PARENT] PLATFORM" >&2; exit 2 ;; esac
if [[ -n $REFRESH && $P != linux ]]; then echo "img-one: no refresh of $P yet (img-speed.md I4)" >&2; exit 2; fi
W=${BKO_WORK:-/home/m/backotter-work}
S=${S:-$W/stick}
I=${BKO_IMG_DIR:-$W/images}
D=$I/$P
same_dir() { local a b; a=$(stat -Lc %d:%i -- "$1" 2>/dev/null) && b=$(stat -Lc %d:%i -- "$2" 2>/dev/null) && [[ $a == "$b" ]]; }
if [[ ${T2_HOST_ROOT:-/bkoscratch/test2} != /bkoscratch/test2 ]] && same_dir "$I" /home/m/backotter-work/images; then
    echo "img-one: T2_HOST_ROOT=$T2_HOST_ROOT is a test's, and $I is the real set: set BKO_IMG_DIR to a world's" >&2; exit 2
fi
# Linux's source ISO is, by default, the one SL was made from (its report.txt's
# "Source ISO:"), so repointing server/slim moves both together.
SL=${SL:-$W/server/slim}
ISO=${ISO:-$(sed -n 's/^Source ISO: *//p' "$SL/report.txt" 2>/dev/null | head -n 1)}
ISO=${ISO:-$W/server/ubuntu-26.04.1-live-server-amd64.iso}
H=${BKO_HOLT_BIN:-$W/holt-bin}; export BKO_HOLT_BIN=$H
B=${BKO_BIN:-$W/bko-bin}; export BKO_BIN=$B
mkdir -p "$D" || exit 1
# (Fd 9 is closed for what outlives this script: the FreeBSD builder.)
exec 9>"$I/.lock-$P" || exit 1
flock -n 9 || { echo "img-one: a build of $P is under way ($I/.lock-$P is held)" >&2; exit 3; }
cd "$D" || exit 1
dig() { python3 "$S/img_inputs.py" "$@"; }
export S SL ISO BKO_WORK=$W
# A refresh's parent (C4, C5), checked before anything moves, so that a refused
# refresh leaves images/P as it was (exit 4): its set's directory, $pdir, and
# its release image, $prel.  images/P itself is the set moved to prev-STAMP below.
pdir="" prel="" pjson=""
if [[ -n $REFRESH ]]; then
    pargs=()
    if [[ $REFRESH =~ ^[0-9a-f]{64}$ ]]; then
        while IFS= read -r c; do pargs+=(--dir "$c"); done < <(echo "$D"; ls -d "$D"/prev-* 2>/dev/null | sort -r)
        pargs+=(--test-sha256 "$REFRESH")
    else
        [[ -d $REFRESH ]] || { echo "img-one: no parent set at $REFRESH" >&2; exit 2; }
        pargs=(--dir "$(realpath "$REFRESH")")
    fi
    pjson=$(dig parent --platform "$P" "${pargs[@]}" 2>"$I/.parent-$P.err") ||
        { echo "img-one: no refresh from $REFRESH: $(sed 's/^img_inputs: //' "$I/.parent-$P.err")" >&2; rm -f "$I/.parent-$P.err"; exit 4; }
    rm -f "$I/.parent-$P.err"
    pj() { python3 -c 'import json, sys; d = json.loads(sys.argv[1]); print(eval(sys.argv[2]))' "$pjson" "$1"; }
    pdir=$(pj 'd["dir"]') && prel=$(pj 'd["release"]["file"]') || { echo "img-one: can't read the parent check: $pjson" >&2; exit 4; }
fi
stamp=$(date -u +%Y%m%d-%H%M%S)
if [[ -n $(find . -maxdepth 1 -mindepth 1 ! -name 'prev-*') ]]; then
    mkdir "prev-$stamp" || exit 1
    find . -maxdepth 1 -mindepth 1 ! -name 'prev-*' -exec mv -t "prev-$stamp/" {} +
fi
mkdir test
r=$D/result.txt; : >"$r"
fail() { echo "$P FAIL: $*" | tee -a "$r"; echo 1 >"$D/done.exit"; exit 1; }
if [[ -n $pdir ]]; then
    [[ $pdir == "$(realpath "$D")" ]] && pdir=$D/prev-$stamp
    [[ -f $pdir/provenance.json && -f $pdir/$prel ]] || fail "the parent set is gone from $pdir"
    echo "refresh of $pdir: $(pj '"test image " + d["test"]["sha256"][:12] + ", OS epoch " + str(d["os_epoch"])')" >>"$r"
fi
bins() { (cd "$B/$P" && sha256sum ./*-bko) && (cd "$H/$P" && sha256sum ./bko-holt); }
bins >bko-bin.sha256 || fail "no bko-bin/$P or holt-bin/$P"
{ head -n 1 "$B/BUILD-INFO"; echo "bko-holt from holt-bin, commit $(cut -c1-7 "$H/$P/COMMIT")"; } >>"$r"
# The commit of the bko-holt this image carries, or no file (and the image is refused).
commit_file() {
    local want got c
    want=$(awk '$2 == "./bko-holt" { print $1 }' bko-bin.sha256)
    got=$(sha256sum "$H/$P/bko-holt" 2>/dev/null | cut -d' ' -f1)
    c=$(head -c 41 "$H/$P/COMMIT" 2>/dev/null | head -n 1)
    if [[ -n $want && $got == "$want" && $c =~ ^[0-9a-f]{40}$ ]]; then
        echo "$c" >bko-holt.commit
    else
        echo "no bko-holt.commit: holt-bin/$P/bko-holt is ${got:0:12}, bko-bin.sha256 says ${want:0:12}, COMMIT says '${c:0:40}'" >>"$r"
    fi
}
commit_file

# The builder's identity now, against the plan's when one was given.
plan_check() {
    local id
    [[ $P != linux ]] || return 0
    id=$(dig identity "$P" 2>>"$D/inputs.log") || fail "no identity for $P's builder (inputs.log)"
    if [[ -n ${BKO_PLAN_BUILDER:-} && $id != "$BKO_PLAN_BUILDER" ]]; then
        fail "the builder is not the plan's: $id, the plan has $BKO_PLAN_BUILDER"
    fi
    builder_id=$id
}
builder_id=""
fb=$W/vms/freebsd15-builder
fb_ssh=(ssh -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "$fb/id_ed25519" -p 2400 root@127.0.0.1)
port_busy() { [[ -n $(ss -Hltn "sport = :2400" 2>/dev/null) ]]; }
fb_pidf=$D/.fb-builder.pid fb_up=""
# The builder this script started is detached (setsid -f): nothing else would stop it.
# $1: how many 2-s waits to give its own shutdown before a TERM of its recorded PID
# (vm-run passes that on to its QEMU).
stop_builder() {
    [[ -n $fb_up ]] || return 0
    local p
    timeout 30 "${fb_ssh[@]}" '/sbin/shutdown -p now' >/dev/null 2>&1
    for _ in $(seq "$1"); do port_busy || break; sleep 2; done
    p=$(cat "$fb_pidf" 2>/dev/null)
    if [[ $p =~ ^[0-9]+$ ]] && tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null | grep -qF freebsd15.qcow2; then
        kill -TERM "$p" 2>/dev/null
        for _ in $(seq 30); do [[ -d /proc/$p ]] || break; sleep 2; done
    fi
    rm -f "$fb_pidf"; fb_up=""
}
# The running FreeBSD builder's fingerprint: its release and the tools the build runs.
fb_fingerprint() {
    timeout 60 "${fb_ssh[@]}" 'freebsd-version -ku && sha256 /usr/sbin/makefs /usr/bin/mkimg /sbin/mdconfig /sbin/gpart /usr/bin/tar /bin/pax /usr/sbin/freebsd-update' 2>/dev/null
}
# tree_linux IMG OUT: IMG's ISO 9660 tree and its BackOtter layer, listed (img_inputs.py tree-list).
tree_linux() {
    local t rc=0
    t=$(mktemp -d "$D/.tree.XXXXXX") || return 1
    ( set -o pipefail
      xorriso -osirrox on -indev "$1" -extract / "$t/iso" >/dev/null 2>&1 &&
      sudo -n unsquashfs -n -d "$t/layer" "$t/iso/casper/ubuntu-server-minimal.backotter.squashfs" >/dev/null 2>&1 &&
      { tar -C "$t" -cf - iso | dig tree-list --no-owner &&
        sudo -n tar -C "$t" --xattrs --format=pax -cf - layer | dig tree-list; } >"$2.part" ) || rc=1
    sudo -n rm -rf "$t"
    if [[ $rc -eq 0 ]]; then mv "$2.part" "$2"; else rm -f "$2.part"; fi
    return $rc
}
# (Signals ignored first: a second one must not cut the builder's shutdown.)
trap 'trap "" TERM INT HUP; stop_builder 15; exit 143' TERM
trap 'trap "" TERM INT HUP; stop_builder 15; exit 130' INT
trap 'trap "" TERM INT HUP; stop_builder 15; exit 129' HUP
plan_check
if [[ $P == linux ]]; then
    dig digest "$P" >.inputs-start.json 2>>inputs.log || fail "no input digest (inputs.log)"
    sudo -n rm -rf layer
    if [[ -n $pdir ]]; then
        sudo -n env BKO_HOLT_BIN="$H" BKO_BIN="$B" BKO_DIGEST_CACHE="${BKO_DIGEST_CACHE:-$W/.img-digest-cache.json}" \
            BKO_PARENT_PROVENANCE="$pdir/provenance.json" BKO_PARENT_REPORT="$pdir/layer-report.txt" \
            "$S/build-backotter-layer.sh" --refresh-from "$pdir/$prel" "$ISO" "$SL" layer >layer.log 2>&1 || fail "layer refresh (layer.log)"
    else
        sudo -n env BKO_HOLT_BIN="$H" BKO_BIN="$B" "$S/build-backotter-layer.sh" "$ISO" "$SL" layer >layer.log 2>&1 || fail "layer (layer.log)"
    fi
    sudo -n chown -R m:m layer; cp layer/report.txt layer-report.txt
    "$S/make-img.sh" "$ISO" "$SL" layer . >build.log 2>&1 || fail "make-img (build.log)"
    "$S/make-img.sh" --test "$ISO" "$SL" layer test >test/build.log 2>&1 || fail "make-img --test (test/build.log)"
    rm -rf layer
    for i in ./*.img test/*.img; do
        tree_linux "$i" "${i%/*}/os-tree.txt" || echo "tree listing: FAILED for $i" >>"$r"
    done
    # C2 and C3: the two images against the parent's, before anything boots them.
    if [[ -n $pdir ]]; then
        dig refresh-check --platform "$P" --parent "$pdir" --dir "$D" >refresh-check.txt 2>&1 ||
            fail "the refresh is refused: $(tail -n 1 refresh-check.txt | sed 's/^img_inputs: //') (refresh-check.txt)"
        echo "refresh check: $(tr '\n' ' ' <refresh-check.txt)" >>"$r"
    fi
    modes=$MODES_linux
else
    if [[ $P == freebsd ]]; then
        port_busy && fail "something is already on port 2400"
        exec 8>>"$fb/.vm-update.lock" || fail "can't open $fb/.vm-update.lock"
        flock -n 8 || fail "a vm-update is working on freebsd15-builder ($fb/.vm-update.lock is held)"
        fb_up=1
        (cd "$fb" && SSH_PORT=2400 setsid -f bash -c 'echo $$ >"$1"; shift; exec "$@"' bko-fb "$fb_pidf" "$S/../testvm/vm-run" $(cat vm-run-options) --serial-log serial.log freebsd15.qcow2 </dev/null >vm-run.log 2>&1 8>&- 9>&-)
        for _ in $(seq 60); do timeout 15 "${fb_ssh[@]}" true 2>/dev/null && break; sleep 5; done
        fp=$(fb_fingerprint) && [[ -n $fp ]] || { stop_builder 60; fail "couldn't fingerprint freebsd15-builder"; }
        if [[ -n ${BKO_PLAN_BUILDER:-} ]] && ! cmp -s <(printf '%s\n' "$fp") "$fb/fingerprint"; then
            stop_builder 60; fail "freebsd15-builder's fingerprint is not the plan's ($fb/fingerprint)"
        fi
        cmp -s <(printf '%s\n' "$fp") "$fb/fingerprint" 2>/dev/null ||
            { printf '%s\n' "$fp" >"$fb/fingerprint.part" && mv "$fb/fingerprint.part" "$fb/fingerprint"; } ||
            { stop_builder 60; fail "couldn't write $fb/fingerprint"; }
        builder_id=$(dig identity "$P" 2>>inputs.log) || { stop_builder 60; fail "no identity for freebsd15-builder (inputs.log)"; }
    fi
    dig digest "$P" >.inputs-start.json 2>>inputs.log || { stop_builder 60; fail "no input digest (inputs.log)"; }
    BKO_BUILDER_ID=$builder_id BKO_TREE_OUT=$D/os-tree.txt "$S/make-img-bsd.sh" "$P" . >build.log 2>&1; rc=$?
    [[ $rc -eq 0 ]] && { BKO_BUILDER_ID=$builder_id BKO_TREE_OUT=$D/test/os-tree.txt "$S/make-img-bsd.sh" --test "$P" test >test/build.log 2>&1; rc=$?; }
    if [[ $P == freebsd && $rc -eq 0 ]]; then
        [[ $(fb_fingerprint) == "$fp" ]] || { stop_builder 60; fail "freebsd15-builder's fingerprint changed during the build"; }
    fi
    stop_builder 60
    exec 8>&-
    [[ $rc -eq 0 ]] || fail "make-img-bsd (build.log, test/build.log)"
    modes=$MODES_other
fi
bins | cmp -s - bko-bin.sha256 || fail "bko-bin/$P or holt-bin/$P changed during the build"
why=$(dig moved .inputs-start.json 2>&1); case $? in
    0) ;; 3) fail "the build's inputs moved: ${why#img_inputs: }" ;; *) fail "couldn't check the inputs: $why" ;;
esac
# sums DIR: DIR/SHA256SUMS, each raw image's line (as compress-images.sh records it).
sums() {
    local f h
    ( set -o pipefail
      for f in "$1"/*.img; do
          [[ -f $f ]] || continue
          h=$(sha256sum <"$f" | cut -d' ' -f1) && [[ $h =~ ^[0-9a-f]{64}$ ]] || exit 1
          printf '%s  %s\n' "$h" "${f##*/}"
      done | LC_ALL=C sort -k2 >"$1/SHA256SUMS.part" ) && mv -f "$1/SHA256SUMS.part" "$1/SHA256SUMS"
}
sums "$D" && sums "$D/test" || fail "couldn't write SHA256SUMS"
echo "release: $(tail -n 1 build.log)" >>"$r"
echo "test:    $(tail -n 1 test/build.log)" >>"$r"
img=$(ls test/*.img) || fail "no test image"
# The modes side by side, BKO_BOOT_WIDTH at once, from a copy in BKO_BOOT_RAM (img-speed.md §9).
bopts=(--width "${BKO_BOOT_WIDTH:-1}")
if [[ -n ${BKO_BOOT_RAM:-} ]]; then
    bopts+=(--ram "$BKO_BOOT_RAM" --sha256 "$(cut -d' ' -f1 test/SHA256SUMS | head -n 1)")
    [[ -z ${BKO_BOOT_RAM_GIB:-} ]] || bopts+=(--ram-gib "$BKO_BOOT_RAM_GIB")
fi
bash "$S/boot-modes.sh" "${bopts[@]}" "$P" "$img" test $modes 2>>"$r"
echo "boot: $(grep -m 1 '^width' test/boot-modes.txt), $(grep -m 1 '^image' test/boot-modes.txt)" >>"$r"
fails=0 boots=()
for m in $modes; do
    rc=$(cat "test/boot-$m.rc" 2>/dev/null); [[ $rc =~ ^[0-9]+$ ]] || rc=2
    [[ $rc -eq 0 ]] || fails=$((fails + 1)); boots+=(--boot "$m=$rc")
    echo "boot $m rc=$rc ($(grep -c '^  ok' "test/boot-$m.out") ok, $(grep -c '^  FAIL' "test/boot-$m.out") FAIL) $(grep -E '^  FAIL|^  ok +(binaries|restic-bko|rustic-bko|bko-holt) ' "test/boot-$m.out" | tr -s ' ' | tr '\n' ' ')" >>"$r"
done
# A build that passed: with BKO_IMG_COMPRESS=1, its .xz for download and this
# platform's old images compressed; then, once the provenance is written, the
# old sets past the last two deleted (D6).  The boot tests' verdict in
# done.exit doesn't depend on either.
if [[ $fails -eq 0 ]]; then
    if [[ ${BKO_IMG_COMPRESS:-0} == 1 ]]; then
        if "$S/compress-images.sh" -p "$P" "${D%/*}" >compress.log 2>&1; then c=ok; else c="FAILED (compress.log)"; fi
        echo "compress: $c" >>"$r"
    fi
fi
# The provenance, last before done.exit but for retention: a failure to write it leaves the
# image as built (a plan treats an image without one as due a full build).
# --start: inputs that moved after the build's check (a vm-update or a sync
# during the boot tests) refuse it, rather than record inputs the image was
# not built from.
rel=$(cd "$D" && ls ./*.img 2>/dev/null | head -n 1)
if pv=$(dig provenance --platform "$P" --dir "$D" --release "${rel#./}" --test "${img#"$D"/}" \
        --run "${BKO_IMG_RUN:-}" --start .inputs-start.json ${pdir:+--parent "$pdir"} "${boots[@]}" 2>&1); then
    echo "$pv" >>"$r"
else
    echo "provenance: FAILED: $pv" >>"$r"
fi
# Retention after the provenance: a refresh's parent may be an older prev-*
# set that the rule deletes, and the provenance reads the parent's.
if [[ $fails -eq 0 && ${BKO_IMG_RETAIN:-1} != 0 ]]; then
    if BKO_RETAIN_HELD=$P "$S/img-retain.sh" -p "$P" "${D%/*}" >retain.log 2>&1; then c=$(tail -n 1 retain.log); else c="FAILED (retain.log)"; fi
    echo "retain: $c" >>"$r"
fi
rm -f .inputs-start.json
[[ -s inputs.log ]] || rm -f inputs.log
echo "$fails" >"$D/done.exit"
