#!/usr/bin/env bash
# Rebuild one platform's rescue image from bko-bin and holt-bin: the release
# image in images/P/, a --test copy in images/P/test/, and boot-test the
# --test copy.  The previous contents of images/P/ go to images/P/prev-STAMP/.
#
#   img-one.sh PLATFORM
#
# PLATFORM: linux, freebsd, netbsd, openbsd, dragonfly, illumos or haiku.
# From outside: BKO_WORK (the work tree, default /home/m/backotter-work: its
# bko-bin/, holt-bin/, server/ and vms/), BKO_IMG_DIR (the images directory,
# default BKO_WORK/images), S (the stick/ to build with, default
# BKO_WORK/stick), ISO and SL (Linux's source ISO and slimmed base),
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
# 1), 2 when T2_HOST_ROOT is a test's and the images directory is the real
# one, 3 when a build of this platform is already under way.
set -u
P=$1
W=${BKO_WORK:-/home/m/backotter-work}
S=${S:-$W/stick}
I=${BKO_IMG_DIR:-$W/images}
D=$I/$P
same_dir() { local a b; a=$(stat -Lc %d:%i -- "$1" 2>/dev/null) && b=$(stat -Lc %d:%i -- "$2" 2>/dev/null) && [[ $a == "$b" ]]; }
if [[ ${T2_HOST_ROOT:-/bkoscratch/test2} != /bkoscratch/test2 ]] && same_dir "$I" /home/m/backotter-work/images; then
    echo "img-one: T2_HOST_ROOT=$T2_HOST_ROOT is a test's, and $I is the real set: set BKO_IMG_DIR to a world's" >&2; exit 2
fi
ISO=${ISO:-$W/server/ubuntu-26.04.1-live-server-amd64.iso} SL=${SL:-$W/server/slim}
H=${BKO_HOLT_BIN:-$W/holt-bin}; export BKO_HOLT_BIN=$H
B=${BKO_BIN:-$W/bko-bin}; export BKO_BIN=$B
mkdir -p "$D" || exit 1
# (Fd 9 is closed for what outlives this script: the FreeBSD builder.)
exec 9>"$I/.lock-$P" || exit 1
flock -n 9 || { echo "img-one: a build of $P is under way ($I/.lock-$P is held)" >&2; exit 3; }
cd "$D" || exit 1
stamp=$(date -u +%Y%m%d-%H%M%S)
if [[ -n $(find . -maxdepth 1 -mindepth 1 ! -name 'prev-*') ]]; then
    mkdir "prev-$stamp" || exit 1
    find . -maxdepth 1 -mindepth 1 ! -name 'prev-*' -exec mv -t "prev-$stamp/" {} +
fi
mkdir test
r=$D/result.txt; : >"$r"
fail() { echo "$P FAIL: $*" | tee -a "$r"; echo 1 >"$D/done.exit"; exit 1; }
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
# (Signals ignored first: a second one must not cut the builder's shutdown.)
trap 'trap "" TERM INT HUP; stop_builder 15; exit 143' TERM
trap 'trap "" TERM INT HUP; stop_builder 15; exit 130' INT
trap 'trap "" TERM INT HUP; stop_builder 15; exit 129' HUP
if [[ $P == linux ]]; then
    sudo -n rm -rf layer
    sudo -n env BKO_HOLT_BIN="$H" BKO_BIN="$B" "$S/build-backotter-layer.sh" "$ISO" "$SL" layer >layer.log 2>&1 || fail "layer (layer.log)"
    sudo -n chown -R m:m layer; cp layer/report.txt layer-report.txt
    "$S/make-img.sh" "$ISO" "$SL" layer . >build.log 2>&1 || fail "make-img (build.log)"
    "$S/make-img.sh" --test "$ISO" "$SL" layer test >test/build.log 2>&1 || fail "make-img --test (test/build.log)"
    rm -rf layer
    modes="bios uefi uefi-secureboot cdrom"
else
    if [[ $P == freebsd ]]; then
        port_busy && fail "something is already on port 2400"
        fb_up=1
        (cd "$fb" && SSH_PORT=2400 setsid -f bash -c 'echo $$ >"$1"; shift; exec "$@"' bko-fb "$fb_pidf" "$S/../testvm/vm-run" $(cat vm-run-options) --serial-log serial.log freebsd15.qcow2 </dev/null >vm-run.log 2>&1 9>&-)
        for _ in $(seq 60); do timeout 15 "${fb_ssh[@]}" true 2>/dev/null && break; sleep 5; done
    fi
    "$S/make-img-bsd.sh" "$P" . >build.log 2>&1; rc=$?
    [[ $rc -eq 0 ]] && { "$S/make-img-bsd.sh" --test "$P" test >test/build.log 2>&1; rc=$?; }
    stop_builder 60
    [[ $rc -eq 0 ]] || fail "make-img-bsd (build.log, test/build.log)"
    modes="bios uefi"
fi
bins | cmp -s - bko-bin.sha256 || fail "bko-bin/$P or holt-bin/$P changed during the build"
echo "release: $(tail -n 1 build.log)" >>"$r"
echo "test:    $(tail -n 1 test/build.log)" >>"$r"
img=$(ls test/*.img) || fail "no test image"
fails=0
for m in $modes; do
    media=usb mm=$m; [[ $m == cdrom ]] && { media=cdrom; mm=bios; }
    python3 "$S/boot-test.py" "$img" "$mm" --platform "$P" --media "$media" --log "test/boot-$m.log" >"test/boot-$m.out" 2>&1
    rc=$?; [[ $rc -eq 0 ]] || fails=$((fails + 1))
    echo "boot $m rc=$rc ($(grep -c '^  ok' "test/boot-$m.out") ok, $(grep -c '^  FAIL' "test/boot-$m.out") FAIL) $(grep -E '^  FAIL|^  ok +(binaries|restic-bko|rustic-bko|bko-holt) ' "test/boot-$m.out" | tr -s ' ' | tr '\n' ' ')" >>"$r"
done
# A build that passed: its .xz for download, and this platform's old images
# compressed.  The boot tests' verdict in done.exit doesn't depend on this.
if [[ $fails -eq 0 ]]; then
    if "$S/compress-images.sh" -p "$P" "${D%/*}" >compress.log 2>&1; then c=ok; else c="FAILED (compress.log)"; fi
    echo "compress: $c" >>"$r"
fi
echo "$fails" >"$D/done.exit"
