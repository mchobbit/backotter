#!/usr/bin/env bash
# Make BackOtter's rescue image for a BSD (FreeBSD, NetBSD, OpenBSD or
# DragonFly), illumos or Haiku.  The image is built inside a VM of that
# system, since only it has the tools for its own file systems, partition
# tables and boot code.
#
# Usage: ./make-img-bsd.sh [--test] [--refresh-from PARENT_IMG] PLATFORM [OUT_IMG]
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
#                   maybe BUILD_VM_POWEROFF (the command that powers it off,
#                   by default /sbin/shutdown -p now), BUILD_VM_USER (whom
#                   to log in as, by default root; Haiku's uid 0 is "user")
#                   and BUILD_FILES (more files from stick/PLATFORM/ for in/,
#                   an array; one named like a file from stick/bsd/ replaces
#                   it); GPT_IDS=random if the build draws random GPT GUIDs
#                   (a refresh then draws them anew); RESTIC_REQUIRED=no if the image may go without
#                   restic-bko (Haiku has none); RUSTIC_REQUIRED=yes if the
#                   image must have rustic-bko; and
#                   defines fetch MEDIA_DIR, which downloads and verifies the
#                   official media into MEDIA_DIR and sets MEDIA to their paths
#                   there (in/ gets each by its file name).
#   build.sh        sh, run as root (BUILD_VM_USER) in the VM, in a fresh
#                   directory of its own:
#                     build.sh info      prints KERNEL=..., and ZFS_VERSION=...
#                                        with in/zfs-features if the image has
#                                        ZFS (OpenZFS, or illumos' own)
#                     build.sh assemble  writes out/image.img from in/
#                     build.sh list      out/image.img's OS file systems,
#                                        mounted read-only, as a tar on
#                                        stdout: one top-level directory per
#                                        tree (img_inputs.py tree-list, and
#                                        stick/img-swap.tsv's tree names)
#                     build.sh room      (optional) out/image.img's free space
#                                        where the binaries are: a BKO-ROOM
#                                        line (stick/bsd/refresh-lib.sh)
#                     build.sh refresh   (optional) out/image.img, the parent
#                                        put there, with in/'s binaries and
#                                        marker swapped in place (§4.2); exit
#                                        3 when they don't fit
#                   in/ holds the media, console, remote-access and
#                   sshd_config.backotter (from stick/bsd/), BUILD_FILES,
#                   restic-bko (unless RESTIC_REQUIRED=no and there is none)
#                   and rustic-bko (if there is one for this platform, and
#                   always with RUSTIC_REQUIRED=yes), bko-holt, and for
#                   assemble also backotter.json and config.img (marker.py's),
#                   and refresh-lib.sh (from stick/bsd/), which room and
#                   refresh source.  TEST=1 in its environment for --test
#                   (shorter boot menus).
#   checks.sh, config-checks.sh
#                   boot-test.py --platform PLATFORM's checks.
#
# The binaries come from $BKO_BIN/PLATFORM/ (default ~/backotter-work/bko-bin):
# restic-bko is required unless platform.conf sets RESTIC_REQUIRED=no,
# rustic-bko optional unless it sets RUSTIC_REQUIRED=yes.  bko-holt, which
# every image has, comes from $BKO_HOLT_BIN/PLATFORM/ (default
# ~/backotter-work/holt-bin: a pinned set of holt/'s builds, as
# testvm/holttest1d/build-holt.sh makes them, each PLATFORM/ with the COMMIT
# it was built from, and SHA256SUMS).
#
# The builder (docs/design/img-speed.md C7): a snapshot builder's
# vms/BUILD_VM/.vm-update.lock is held from before its VM boots until this
# script has shut it down, so that vm-update refuses that guest meanwhile; one
# still held after a minute (a vm-update's) refuses the build.  With BKO_BUILDER_ID (img_inputs.py identity PLATFORM's
# line, as img-one.sh took it), the builder's identity is read again once the
# VM is up and once the image is out, and a different one refuses the build.
# fetch's MEDIA go to img-media/PLATFORM/MEDIA (the base digest's media).  With
# BKO_TREE_OUT, `build.sh list`'s tar becomes that file, the tree listing
# (img_inputs.py tree-list); a listing that fails is said, and the image stands.
#
# With BKO_ROOM_OUT, `build.sh room`'s line becomes that file, a JSON object
# (free_kib, total_kib, bins_kib: provenance's room); a full build whose free
# space is under the headroom rule, max(64 MiB, half the binaries' size), is
# refused (img-speed.md §4.2, D1).
#
# --refresh-from PARENT_IMG (docs/design/img-speed.md §4.2, D1, D2): the image
# is PARENT_IMG with its binaries and marker swapped in place, inside the VM
# (`build.sh refresh`), not built: no media are fetched.  The parent's root is
# first listed in the VM (with BKO_TREE_PARENT, its os-tree.txt, which the
# listing must equal) and its marker read; the new marker (a new image_id)
# keeps the parent's base, release, kernel and ZFS fields, which build.sh
# refresh checks against the parent's tree.  The host then writes the new
# config.img into partition CONFIG_PART with dd, and with GPT_IDS=random gives
# the GPT new GUIDs (img_gpt.py).  Exit 6 when the new binaries don't fit the
# parent's free space (build.sh refresh's exit 3): the planner gives the
# platform a full build.
#
# Needs: qemu with KVM (vm-run), ssh, python3 3.11 or later, dosfstools,
# mtools, flock, and what PLATFORM's fetch needs.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
BKO_BIN=${BKO_BIN:-$WORK/bko-bin}
BKO_HOLT_BIN=${BKO_HOLT_BIN:-$WORK/holt-bin}
SSH_WAIT=600                    # seconds for the VM's sshd to come up
die() { echo "make-img-bsd: $*" >&2; exit 1; }

test=0 parent=""
while [[ ${1:-} == --* ]]; do
    case $1 in
    --test) test=1; shift ;;
    --refresh-from) [[ -f ${2:-} ]] || die "--refresh-from: no image ${2:-}"; parent=$(realpath "$2"); shift 2 ;;
    *) break ;;
    esac
done
[[ $# -eq 1 || $# -eq 2 ]] || die "usage: $0 [--test] [--refresh-from PARENT_IMG] PLATFORM [OUT_IMG]"
PLATFORM=$1 OUT_IMG=${2:-.}
pdir=$here/$PLATFORM
[[ -f $pdir/platform.conf && -f $pdir/build.sh ]] || die "no stick/$PLATFORM/{platform.conf,build.sh}"
# has SUBCOMMAND: whether PLATFORM's build.sh has it.
has() { grep -Eq "^[[:space:]]*$1\)" "$pdir/build.sh"; }
[[ -z $parent ]] || has refresh || die "stick/$PLATFORM/build.sh has no refresh"
BUILD_VM_OPTIONS=() BUILD_FILES=() RESTIC_REQUIRED=yes RUSTIC_REQUIRED=no GPT_IDS=""
BUILD_VM_POWEROFF='/sbin/shutdown -p now' BUILD_VM_USER=root
# shellcheck source=/dev/null
. "$pdir/platform.conf"
for v in BASE_OS BASE_RELEASE CONFIG_PART BUILD_VM BUILD_VM_IMAGE BUILD_VM_PORT BUILD_VM_MODE; do
    [[ -n ${!v:-} ]] || die "$PLATFORM/platform.conf doesn't set $v"
done
case $BUILD_VM_MODE in running|snapshot) ;; *) die "BUILD_VM_MODE must be running or snapshot" ;; esac
vm=$WORK/vms/$BUILD_VM
[[ -f $vm/$BUILD_VM_IMAGE && -f $vm/id_ed25519 ]] || die "no VM in $vm"
[[ $RESTIC_REQUIRED == no || -x $BKO_BIN/$PLATFORM/restic-bko ]] || die "no $BKO_BIN/$PLATFORM/restic-bko"
[[ $RUSTIC_REQUIRED != yes || -x $BKO_BIN/$PLATFORM/rustic-bko ]] ||
    die "no $BKO_BIN/$PLATFORM/rustic-bko, and $PLATFORM's image needs it"
[[ -x $BKO_BIN/$PLATFORM/restic-bko || -x $BKO_BIN/$PLATFORM/rustic-bko ]] ||
    die "neither restic-bko nor rustic-bko in $BKO_BIN/$PLATFORM"
[[ -x $BKO_HOLT_BIN/$PLATFORM/bko-holt ]] || die "no $BKO_HOLT_BIN/$PLATFORM/bko-holt"

if [[ -d $OUT_IMG ]]; then
    version=dev.$(date -u +%Y%m%d)
    if commit=$(git -C "$here" rev-parse --short=7 HEAD 2>/dev/null); then
        version+=.$commit
        [[ -z $(git -C "$here" status --porcelain -- . 2>/dev/null) ]] || version+=.dirty
    elif [[ -s $here/.backotter-commit ]]; then
        # A copy on a test host: testvm/sync-hosts.sh says which commit.
        version+=.$(head -n 1 "$here/.backotter-commit")
    fi
    OUT_IMG=$OUT_IMG/backotter-live-$PLATFORM-$version-amd64.img
fi
[[ -e $OUT_IMG ]] && die "$OUT_IMG already exists"

# --- The official media (a refresh reads none: its OS bits are the parent's) -------
MEDIA=()
media_dir=$WORK/img-media/$PLATFORM
if [[ -z $parent ]]; then
    mkdir -p "$media_dir"
    fetch "$media_dir"
    [[ ${#MEDIA[@]} -gt 0 ]] || die "$PLATFORM's fetch found no media"
    printf '%s\n' "${MEDIA[@]}" >"$media_dir/MEDIA.part" && mv "$media_dir/MEDIA.part" "$media_dir/MEDIA"
fi

# The builder's identity, again, against the one the build started with.
same_builder() {
    local id
    [[ -n ${BKO_BUILDER_ID:-} ]] || return 0
    id=$(S=$here BKO_WORK=$WORK python3 "$here/img_inputs.py" identity "$PLATFORM") || die "no identity for $BUILD_VM"
    [[ $id == "$BKO_BUILDER_ID" ]] ||
        { rm -f "$OUT_IMG.part"; die "$BUILD_VM moved $1: it is $id, the build started with $BKO_BUILDER_ID"; }
}

# --- The build VM ---------------------------------------------------------------
work=$(mktemp -d)
started=no
ssh_cmd=(ssh -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ServerAliveInterval=30 -i "$vm/id_ed25519" -p "$BUILD_VM_PORT" "$BUILD_VM_USER@127.0.0.1")
# Through sh: root's shell may be csh (DragonFly's is).  Archives are unpacked
# with cd first: illumos tar's -C names a directory to archive from only.
ssh_vm() { local sq="'"; local q=${1//$sq/$sq\\$sq$sq}; "${ssh_cmd[@]}" "/bin/sh -c '$q'"; }
cleanup() {
    if [[ $started == yes ]]; then
        # By path: NetBSD's sshd gives commands no /sbin in PATH.
        ssh_vm "$BUILD_VM_POWEROFF" >/dev/null 2>&1 || true
        for _ in $(seq 60); do pgrep -f "^qemu-system.*:$BUILD_VM_PORT-:22" >/dev/null || break; sleep 2; done
        pkill -f "^qemu-system.*:$BUILD_VM_PORT-:22" 2>/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT

if [[ $BUILD_VM_MODE == snapshot ]]; then
    pgrep -f "^qemu-system.*:$BUILD_VM_PORT-:22" >/dev/null && die "port $BUILD_VM_PORT is in use"
    exec 7>>"$vm/.vm-update.lock" || die "can't open $vm/.vm-update.lock"
    flock -w "${BKO_GUEST_LOCK_WAIT:-60}" 7 || die "a vm-update is working on $BUILD_VM ($vm/.vm-update.lock is held)"
    opts=()
    # read fails at a last line without a newline, having read it anyway.
    [[ -f $vm/vm-run-options ]] && { read -r -a opts <"$vm/vm-run-options" || [[ ${#opts[@]} -gt 0 ]]; }
    echo "Booting a throwaway copy of $BUILD_VM on port $BUILD_VM_PORT"
    SSH_PORT=$BUILD_VM_PORT setsid -f "$here/../testvm/vm-run" --snapshot "${opts[@]}" \
        "${BUILD_VM_OPTIONS[@]}" --serial-log "$work/serial.log" "$vm/$BUILD_VM_IMAGE" \
        </dev/null >"$work/vm-run.log" 2>&1 7>&-
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
same_builder "while its VM came up"

# --- Build ------------------------------------------------------------------------
mkdir "$work/in"
cp "$here/bsd/console" "$here/bsd/remote-access" "$here/bsd/sshd_config.backotter" \
    "$here/bsd/refresh-lib.sh" "$pdir/build.sh" "$work/in/"
for f in "${BUILD_FILES[@]}"; do cp "$pdir/$f" "$work/in/"; done
if [[ -x $BKO_BIN/$PLATFORM/restic-bko ]]; then
    cp "$BKO_BIN/$PLATFORM/restic-bko" "$work/in/"
else
    echo "make-img-bsd: no restic-bko for $PLATFORM; the image will have rustic-bko only" >&2
fi
if [[ -x $BKO_BIN/$PLATFORM/rustic-bko ]]; then
    cp "$BKO_BIN/$PLATFORM/rustic-bko" "$work/in/"
else
    echo "make-img-bsd: no rustic-bko for $PLATFORM; the image will have restic-bko only" >&2
fi
cp "$BKO_HOLT_BIN/$PLATFORM/bko-holt" "$work/in/"
for m in "${MEDIA[@]}"; do ln -s "$media_dir/$m" "$work/in/${m##*/}"; done
[[ -z $parent ]] || ln -s "$parent" "$work/in/parent.img"
echo "Copying $(du -shL "$work/in" | cut -f1) into $BUILD_VM"
rdir=/var/tmp/bko-build.$(hostname -s).$$     # this run's, in the VM
ssh_vm "rm -rf $rdir && mkdir -p $rdir/out"
tar -C "$work" -chf - in | ssh_vm "cd $rdir && tar -xf -"

zfs_args=()
if [[ -z $parent ]]; then
    ssh_vm "cd $rdir && sh in/build.sh info" >"$work/info" || die "build.sh info failed"
    kernel=$(sed -n 's/^KERNEL=//p' "$work/info")
    zfs=$(sed -n 's/^ZFS_VERSION=//p' "$work/info")
    [[ -n $kernel ]] || die "build.sh info gave no KERNEL"
    if [[ -n $zfs ]]; then
        ssh_vm "cat $rdir/in/zfs-features" >"$work/zfs-features"
        zfs_args=(--zfs-version "$zfs" --zfs-features "$work/zfs-features")
    fi
    base_os=$BASE_OS base_release=$BASE_RELEASE
else
    # The parent, as out/image.img: its OS file systems listed (against its
    # os-tree.txt, BKO_TREE_PARENT) and its marker read, which the new one's
    # base, release, kernel and ZFS fields come from (`build.sh info` would
    # rebuild the root from the media, and on FreeBSD reach the network).
    ssh_vm "cd $rdir && mv in/parent.img out/image.img" || die "couldn't put the parent in place"
    ssh_vm "cd $rdir && sh in/build.sh list" >"$work/parent.tar" || die "build.sh list of the parent failed"
    if [[ -n ${BKO_TREE_PARENT:-} ]]; then
        S=$here BKO_WORK=$WORK python3 "$here/img_inputs.py" tree-list <"$work/parent.tar" >"$work/parent-tree.txt" ||
            die "couldn't list the parent's tree"
        cmp -s "$work/parent-tree.txt" "$BKO_TREE_PARENT" ||
            die "the parent's file systems are not its listing $BKO_TREE_PARENT: $(diff "$BKO_TREE_PARENT" "$work/parent-tree.txt" | grep -c '^[<>]') lines differ"
        echo "The parent's $(wc -l <"$work/parent-tree.txt") entries are its listing's"
    fi
    pm=$(tar -tf "$work/parent.tar" | grep -m 1 -E '^(\./)?[^/]+/\.disk/backotter\.json$') || die "the parent has no /.disk/backotter.json"
    tar -xOf "$work/parent.tar" "$pm" >"$work/parent-marker.json" || die "couldn't read the parent's marker"
    rm -f "$work/parent.tar"
    pmv=$(python3 - "$work/parent-marker.json" "$PLATFORM" "$CONFIG_PART" "$work/zfs-features" <<'PY'
import json, shlex, sys
m = json.load(open(sys.argv[1]))
assert m.get("format") == "backotter-image" and m.get("platform") == sys.argv[2], "not this platform's marker"
assert (m.get("config_partition") or {}).get("number") == int(sys.argv[3]), "another config partition"
print("base_os=%s base_release=%s kernel=%s" % tuple(shlex.quote(str(x)) for x in
      (m["base"]["os"], m["base"]["release"], m["kernel"])))
z = m.get("zfs")
if z:
    open(sys.argv[4], "w").write("".join(f + "\n" for f in z["features"]))
    print("zfs=%s" % shlex.quote(z["version"]))
else:
    print("zfs=")
PY
) || die "the parent's marker is not one a refresh can keep: $(cat "$work/parent-marker.json")"
    eval "$pmv"
    [[ -z $zfs ]] || zfs_args=(--zfs-version "$zfs" --zfs-features "$work/zfs-features")
    echo "The parent's marker: $base_os $base_release, kernel $kernel${zfs:+, ZFS $zfs}"
fi
# The dump schemas the image's bko-holt load reads, in its own words (run in
# the VM, its system), for the marker's dump_schemas.
ssh_vm "cd $rdir && ./in/bko-holt --version --json" >"$work/holt-api.json" ||
    die "bko-holt --version --json failed in $BUILD_VM"
image_id=$(python3 "$here/marker.py" --platform "$PLATFORM" --base-os "$base_os" \
    --base-release "$base_release" --kernel "$kernel" --config-part "$CONFIG_PART" \
    --holt-api "$work/holt-api.json" "${zfs_args[@]}" "$work/marker") || die "marker.py failed"
tar -C "$work/marker" -cf - backotter.json config.img | ssh_vm "cd $rdir/in && tar -xf -"

if [[ -z $parent ]]; then
    echo "Assembling the image in $BUILD_VM"
    ssh_vm "cd $rdir && TEST=$test sh in/build.sh assemble" || die "build.sh assemble failed"
else
    echo "Refreshing ${parent##*/} in $BUILD_VM"
    rc=0; ssh_vm "cd $rdir && sh in/build.sh refresh" || rc=$?
    if [[ $rc -eq 3 ]]; then
        echo "make-img-bsd: the new binaries don't fit the parent's free space: build $PLATFORM in full" >&2
        exit 6
    fi
    [[ $rc -eq 0 ]] || die "build.sh refresh failed"
fi
if has room; then
    ssh_vm "cd $rdir && sh in/build.sh room" >"$work/room" || die "build.sh room failed"
    python3 - "$work/room" "$work/room.json" "$( [[ -n $parent ]] && echo refresh || echo full)" \
        "$work"/in/restic-bko "$work"/in/rustic-bko "$work/in/bko-holt" <<'PY' || die "the room check failed"
import json, os, re, sys
m = re.search(r"BKO-ROOM free_kib=(\d+) total_kib=(\d+) bins_kib=(\d+)", open(sys.argv[1]).read())
assert m, "build.sh room gave no BKO-ROOM line"
room = dict(zip(("free_kib", "total_kib", "bins_kib"), map(int, m.groups())))
b = sum((os.path.getsize(f) + 1023) // 1024 for f in sys.argv[4:] if os.path.isfile(f))
room["headroom_kib"] = max(65536, b // 2)
json.dump(room, open(sys.argv[2], "w"), sort_keys=True)
print(f"Room: {room['free_kib']} KiB free of {room['total_kib']}, the binaries {room['bins_kib']} KiB"
      f" (the headroom rule: {room['headroom_kib']})")
if sys.argv[3] == "full" and room["free_kib"] < room["headroom_kib"]:
    sys.exit(f"make-img-bsd: {room['free_kib']} KiB free, under the headroom rule's {room['headroom_kib']} (img-speed.md §4.2)")
PY
    [[ -z ${BKO_ROOM_OUT:-} ]] || cp "$work/room.json" "$BKO_ROOM_OUT"
fi
ssh_vm "cat $rdir/out/image.img" >"$OUT_IMG.part" || die "couldn't copy the image out"
if [[ -n $parent ]]; then
    # The new config.img into its partition, at the offset the parent's table gives.
    read -r cfg_first cfg_sectors < <(python3 "$here/img_gpt.py" part "$OUT_IMG.part" "$CONFIG_PART") ||
        die "no partition $CONFIG_PART in the parent's GPT"
    [[ $((cfg_sectors * 512)) -eq $(stat -c %s "$work/marker/config.img") ]] ||
        die "partition $CONFIG_PART is $cfg_sectors sectors, not config.img's size"
    dd if="$work/marker/config.img" of="$OUT_IMG.part" bs=512 seek="$cfg_first" conv=notrunc,fsync status=none ||
        die "couldn't write config.img"
    if [[ $GPT_IDS == random ]]; then
        python3 "$here/img_gpt.py" new-ids "$OUT_IMG.part" >"$work/gpt-ids" || die "couldn't give the GPT new GUIDs"
        echo "New GPT GUIDs: $(tr '\n' ' ' <"$work/gpt-ids")"
    fi
fi
if [[ -n ${BKO_TREE_OUT:-} ]]; then
    if ssh_vm "cd $rdir && sh in/build.sh list" | S=$here BKO_WORK=$WORK python3 "$here/img_inputs.py" tree-list >"$BKO_TREE_OUT.part" &&
        [[ -s $BKO_TREE_OUT.part ]]; then
        mv "$BKO_TREE_OUT.part" "$BKO_TREE_OUT"
        echo "Listed $(wc -l <"$BKO_TREE_OUT") entries of the image's file systems in $BKO_TREE_OUT"
    else
        rm -f "$BKO_TREE_OUT.part"
        echo "make-img-bsd: the image's tree listing failed (build.sh list); the image stands" >&2
    fi
fi
ssh_vm "rm -rf $rdir"
same_builder "during the build"
mv "$OUT_IMG.part" "$OUT_IMG"
# Its size, not du's: a host's ZFS may not have written it all out yet.
echo "Wrote $OUT_IMG ($(( $(stat -c %s "$OUT_IMG") >> 20 )) MiB), image $image_id"
