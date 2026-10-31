#!/usr/bin/env bash
# Make BackOtter's rescue image for a BSD: FreeBSD, NetBSD, OpenBSD or
# DragonFly.  The image is built inside a VM of that BSD, since only it has the
# tools for its own file systems, partition tables and boot code.
#
# Usage: ./make-img-bsd.sh [--test] PLATFORM [OUT_IMG]
#
# OUT_IMG defaults to backotter-live-PLATFORM-VERSION-amd64.img in the current
# directory, or in OUT_IMG if that's a directory, with VERSION as make-img.sh
# makes it.  Runs on any KVM host laid out like chopin (~/backotter-work/vms,
# testvm/ next to stick/).  Each platform is a directory, stick/PLATFORM/:
#
#   platform.conf   bash, sourced here.  Sets BASE_OS, BASE_RELEASE,
#                   CONFIG_PART (the config partition's number), BUILD_VM
#                   (a directory in ~/backotter-work/vms with the VM's qcow2,
#                   id_ed25519 and maybe vm-run-options), BUILD_VM_IMAGE,
#                   BUILD_VM_PORT (its SSH port here), BUILD_VM_MODE ("running":
#                   a builder that is already up on that port; "snapshot":
#                   boot a throwaway copy, thrown away when done) and
#                   BUILD_VM_OPTIONS (more vm-run options, an array);
#                   RUSTIC_REQUIRED=yes if the image must have rustic-bko; and
#                   defines fetch MEDIA_DIR, which downloads and verifies the
#                   official media into MEDIA_DIR and sets MEDIA to their names.
#   build.sh        sh, run as root in the VM, in a fresh directory of its own:
#                     build.sh info      prints KERNEL=..., and ZFS_VERSION=...
#                                        with in/zfs-features if the image has
#                                        OpenZFS
#                     build.sh assemble  writes out/image.img from in/
#                   in/ holds the media, console, remote-access and
#                   sshd_config.backotter (from stick/bsd/), restic-bko and
#                   rustic-bko (if there is one for this platform, and always
#                   with RUSTIC_REQUIRED=yes), and for
#                   assemble also backotter.json and config.img (marker.py's).
#                   TEST=1 in its environment for --test (shorter boot menus).
#   checks.sh, config-checks.sh
#                   boot-test.py --platform PLATFORM's checks.
#
# The binaries come from $BKO_BIN/PLATFORM/ (default ~/backotter-work/bko-bin):
# restic-bko is required, rustic-bko optional unless platform.conf sets
# RUSTIC_REQUIRED=yes.
#
# Needs: qemu with KVM (vm-run), ssh, python3 3.11 or later, dosfstools,
# mtools, and what PLATFORM's fetch needs.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
BKO_BIN=${BKO_BIN:-$WORK/bko-bin}
SSH_WAIT=600                    # seconds for the VM's sshd to come up
die() { echo "make-img-bsd: $*" >&2; exit 1; }

test=0
if [[ ${1:-} == --test ]]; then test=1; shift; fi
[[ $# -eq 1 || $# -eq 2 ]] || die "usage: $0 [--test] PLATFORM [OUT_IMG]"
PLATFORM=$1 OUT_IMG=${2:-.}
pdir=$here/$PLATFORM
[[ -f $pdir/platform.conf && -f $pdir/build.sh ]] || die "no stick/$PLATFORM/{platform.conf,build.sh}"
BUILD_VM_OPTIONS=() RUSTIC_REQUIRED=no
# shellcheck source=/dev/null
. "$pdir/platform.conf"
for v in BASE_OS BASE_RELEASE CONFIG_PART BUILD_VM BUILD_VM_IMAGE BUILD_VM_PORT BUILD_VM_MODE; do
    [[ -n ${!v:-} ]] || die "$PLATFORM/platform.conf doesn't set $v"
done
case $BUILD_VM_MODE in running|snapshot) ;; *) die "BUILD_VM_MODE must be running or snapshot" ;; esac
vm=$WORK/vms/$BUILD_VM
[[ -f $vm/$BUILD_VM_IMAGE && -f $vm/id_ed25519 ]] || die "no VM in $vm"
[[ -x $BKO_BIN/$PLATFORM/restic-bko ]] || die "no $BKO_BIN/$PLATFORM/restic-bko"
[[ $RUSTIC_REQUIRED != yes || -x $BKO_BIN/$PLATFORM/rustic-bko ]] ||
    die "no $BKO_BIN/$PLATFORM/rustic-bko, and $PLATFORM's image needs it"

if [[ -d $OUT_IMG ]]; then
    version=dev.$(date -u +%Y%m%d)
    if commit=$(git -C "$here" rev-parse --short=7 HEAD 2>/dev/null); then
        version+=.$commit
        [[ -z $(git -C "$here" status --porcelain -- . 2>/dev/null) ]] || version+=.dirty
    fi
    OUT_IMG=$OUT_IMG/backotter-live-$PLATFORM-$version-amd64.img
fi
[[ -e $OUT_IMG ]] && die "$OUT_IMG already exists"

# --- The official media ---------------------------------------------------------
MEDIA=()
media_dir=$WORK/img-media/$PLATFORM
mkdir -p "$media_dir"
fetch "$media_dir"
[[ ${#MEDIA[@]} -gt 0 ]] || die "$PLATFORM's fetch found no media"

# --- The build VM ---------------------------------------------------------------
work=$(mktemp -d)
started=no
ssh_cmd=(ssh -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ServerAliveInterval=30 -i "$vm/id_ed25519" -p "$BUILD_VM_PORT" root@127.0.0.1)
# Through sh: root's shell may be csh (DragonFly's is).
ssh_vm() { local sq="'"; local q=${1//$sq/$sq\\$sq$sq}; "${ssh_cmd[@]}" "/bin/sh -c '$q'"; }
cleanup() {
    if [[ $started == yes ]]; then
        # By path: NetBSD's sshd gives commands no /sbin in PATH.
        ssh_vm '/sbin/shutdown -p now' >/dev/null 2>&1 || true
        for _ in $(seq 60); do pgrep -f "^qemu-system.*:$BUILD_VM_PORT-:22" >/dev/null || break; sleep 2; done
        pkill -f "^qemu-system.*:$BUILD_VM_PORT-:22" 2>/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT

if [[ $BUILD_VM_MODE == snapshot ]]; then
    pgrep -f "^qemu-system.*:$BUILD_VM_PORT-:22" >/dev/null && die "port $BUILD_VM_PORT is in use"
    opts=()
    # read fails at a last line without a newline, having read it anyway.
    [[ -f $vm/vm-run-options ]] && { read -r -a opts <"$vm/vm-run-options" || [[ ${#opts[@]} -gt 0 ]]; }
    echo "Booting a throwaway copy of $BUILD_VM on port $BUILD_VM_PORT"
    SSH_PORT=$BUILD_VM_PORT setsid -f "$here/../testvm/vm-run" --snapshot "${opts[@]}" \
        "${BUILD_VM_OPTIONS[@]}" --serial-log "$work/serial.log" "$vm/$BUILD_VM_IMAGE" \
        </dev/null >"$work/vm-run.log" 2>&1
    started=yes
    # vm-run is still setting up when setsid returns: wait for its QEMU.
    for _ in $(seq 30); do
        pgrep -f "^qemu-system.*:$BUILD_VM_PORT-:22" >/dev/null && break
        sleep 1
    done
fi
# QEMU's user network accepts the connection before the guest is up, so
# ConnectTimeout never fires; timeout does.
deadline=$((SECONDS + SSH_WAIT))
until timeout 15 "${ssh_cmd[@]}" true 2>/dev/null; do
    [[ $SECONDS -lt $deadline ]] || die "$BUILD_VM's sshd didn't come up on port $BUILD_VM_PORT"
    if [[ $started == yes ]] && ! pgrep -f "^qemu-system.*:$BUILD_VM_PORT-:22" >/dev/null; then
        cat "$work/vm-run.log" >&2
        die "$BUILD_VM's QEMU exited; see above and $work/serial.log"
    fi
    sleep 5
done

# --- Build ------------------------------------------------------------------------
mkdir "$work/in"
cp "$here/bsd/console" "$here/bsd/remote-access" "$here/bsd/sshd_config.backotter" \
    "$pdir/build.sh" "$work/in/"
cp "$BKO_BIN/$PLATFORM/restic-bko" "$work/in/"
if [[ -x $BKO_BIN/$PLATFORM/rustic-bko ]]; then
    cp "$BKO_BIN/$PLATFORM/rustic-bko" "$work/in/"
else
    echo "make-img-bsd: no rustic-bko for $PLATFORM; the image will have restic-bko only" >&2
fi
for m in "${MEDIA[@]}"; do ln -s "$media_dir/$m" "$work/in/$m"; done
echo "Copying $(du -shL "$work/in" | cut -f1) into $BUILD_VM"
rdir=/var/tmp/bko-build.$(hostname -s).$$     # this run's, in the VM
ssh_vm "rm -rf $rdir && mkdir -p $rdir/out"
tar -C "$work" -chf - in | ssh_vm "tar -C $rdir -xf -"

ssh_vm "cd $rdir && sh in/build.sh info" >"$work/info" || die "build.sh info failed"
kernel=$(sed -n 's/^KERNEL=//p' "$work/info")
zfs=$(sed -n 's/^ZFS_VERSION=//p' "$work/info")
[[ -n $kernel ]] || die "build.sh info gave no KERNEL"
zfs_args=()
if [[ -n $zfs ]]; then
    ssh_vm "cat $rdir/in/zfs-features" >"$work/zfs-features"
    zfs_args=(--zfs-version "$zfs" --zfs-features "$work/zfs-features")
fi
image_id=$(python3 "$here/marker.py" --platform "$PLATFORM" --base-os "$BASE_OS" \
    --base-release "$BASE_RELEASE" --kernel "$kernel" --config-part "$CONFIG_PART" \
    "${zfs_args[@]}" "$work/marker") || die "marker.py failed"
tar -C "$work/marker" -cf - backotter.json config.img | ssh_vm "tar -C $rdir/in -xf -"

echo "Assembling the image in $BUILD_VM"
ssh_vm "cd $rdir && TEST=$test sh in/build.sh assemble" || die "build.sh assemble failed"
ssh_vm "cat $rdir/out/image.img" >"$OUT_IMG.part" || die "couldn't copy the image out"
ssh_vm "rm -rf $rdir"
mv "$OUT_IMG.part" "$OUT_IMG"
echo "Wrote $OUT_IMG ($(du -h "$OUT_IMG" | cut -f1)), image $image_id"
