#!/usr/bin/env bash
# Non-root restore spot check: back a tree up as root, restore it as an
# unprivileged user (uid 4321) into a directory that user owns, with
# restic-bko and rustic-bko in all four directions, and report what each
# restores and what it warns about (SPEC2 7.3): owner-settable file flags
# (UF_* on the BSDs; nodump/noatime inode flags on Linux), ZFS DOS flags
# (all but immutable and appendonly are the owner's), illumos system
# attributes (hidden, readonly, system, archive, offline, sparse),
# birth and creation times (FreeBSD, NetBSD, illumos), ZFS project IDs
# (illumos), times, modes, extended attributes and ACLs; and what root
# alone may set (SF_*, immutable, append, trusted.* and system extended
# attributes, SID owners, device nodes), which must give a warning, not an
# abort.  One node has an attribute only root may set before one the user
# may (AUDIT-R4 D1).  Then what differs between the two tools' restores:
# they must behave the same.
# The work is nonroot-inner.sh, next to this (with rt-common.sh).
#
# Usage: nonroot-rt.sh linux          as root on the host: a ZFS pool on a
#                                     file (bkotest-nr, or $NONROOT_POOL)
#                                     and an ext4 loop image, both made and
#                                     destroyed here
#        nonroot-rt.sh GUEST [PORT]   in a throwaway (--ram) copy of GUEST,
#                                     with its SSH on PORT (default 2711):
#           freebsd15       /bkonr a ZFS file system of its own
#           freebsd15-ufs   /bkonr on the UFS root, with POSIX.1e ACLs
#           netbsd11, alpinebsd
#                           /bkonr an FFSv2ea vnd image with POSIX.1e ACLs
#                           (alpinebsd: NetBSD's tools from
#                           ~/backotter-work/netbsd-tools, which
#                           run-guest-rt.sh fetches)
#           dragonfly6      /bkonr on the HAMMER2 root
#           openbsd8        /bkonr on the FFS root
#           omnios58        /bkonr a ZFS file system of its own
#
# RESTIC and RUSTIC (environment) name the binaries; by default
# ~/backotter-work/bko-bin/OS/restic-bko and rustic-bko (OS linux, freebsd,
# netbsd, dragonfly, openbsd, illumos for omnios58, where
# stick/illumos/build-rustic.sh puts it; BKO_BIN replaces bko-bin).
# ~/backotter-work is BKO_WORK, by default the directory that holds this
# testvm/, as for compare.py, and so the same when run as root.  Writes
# ~/backotter-work/nonroot/TARGET.log (and linux-FS.log; NONROOT_LOGS
# replaces ~/backotter-work/nonroot); the linux images live in
# ~/backotter-work/nonroot/work while it runs.  One linux run at a time:
# the pool's name and the mount points (/mnt/bkonr-ext4, /mnt/bkonr-zfs)
# are the host's.
#
# Needs: linux: root, ZFS (zpool), mkfs.ext4, python3.  GUEST:
# qemu-system-x86_64 with KVM, testvm/vm-run, the guest in
# ~/backotter-work/vms, ssh.

# No -e: the summary comes even when a step fails.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$(cd "$here/../.." && pwd)}
BKO_BIN=${BKO_BIN:-$WORK/bko-bin}
die() { echo "nonroot-rt: $*" >&2; exit 2; }
logs=${NONROOT_LOGS:-$WORK/nonroot}
inner=$here/nonroot-inner.sh
target=${1:-}
[[ -n $target ]] || die "usage: nonroot-rt.sh linux | GUEST [PORT]"
binaries() {  # OS [RUSTIC-OS]: sets restic and rustic
    restic=${RESTIC:-$BKO_BIN/$1/restic-bko} rustic=${RUSTIC:-$BKO_BIN/${2:-$1}/rustic-bko}
    [[ -f $restic && -f $rustic ]] || die "no $restic or no $rustic (set RESTIC and RUSTIC)"
    restic=$(realpath "$restic") rustic=$(realpath "$rustic")
}
mkdir -p "$logs"

if [[ $target == linux ]]; then
    [[ $(id -u) == 0 ]] || die "linux needs root"
    binaries linux
    work=$logs/work pool=${NONROOT_POOL:-bkotest-nr}
    cleanup() {
        umount /mnt/bkonr-ext4 2>/dev/null
        zpool destroy -f $pool 2>/dev/null
        rm -rf "$work" /mnt/bkonr-ext4 /mnt/bkonr-zfs
        # The logs are the work directory owner's, as the rest of it.
        chown -R --reference="$WORK" "$logs"
    }
    cleanup; trap cleanup EXIT
    mkdir -p "$work" /mnt/bkonr-ext4
    truncate -s 1G "$work/zfs.img"
    zpool create -f -O mountpoint=/mnt/bkonr-zfs -O atime=off -O acltype=posix -O xattr=sa \
        $pool "$work/zfs.img" || exit 1
    truncate -s 512M "$work/ext4.img"
    mkfs.ext4 -q "$work/ext4.img" && mount -o loop,noatime "$work/ext4.img" /mnt/bkonr-ext4 || exit 1
    for fs in zfs ext4; do
        d=/mnt/bkonr-$fs/t
        mkdir -p $d/bin
        install -m 755 "$restic" $d/bin/restic-bko; install -m 755 "$rustic" $d/bin/rustic-bko
        echo "######## linux $fs: $($d/bin/restic-bko version | head -n 1), $($d/bin/rustic-bko --version)"
        FSMETA_DIR=$here sh "$inner" $d $d/bin/restic-bko $d/bin/rustic-bko 2>&1 |
            tee "$logs/linux-$fs.log"
    done 2>&1 | tee "$logs/linux.log" | grep -A100 '^== summary\|^########'
    exit 0
fi

guest=$target port=${2:-2711}
case $guest in
    freebsd15|freebsd15-ufs) binaries freebsd ;;
    netbsd11|alpinebsd) binaries netbsd ;;
    dragonfly6) binaries dragonfly ;;
    openbsd8) binaries openbsd ;;
    omnios58) binaries illumos ;;
    *) die "no check for $guest" ;;
esac
[[ $port =~ ^[0-9]+$ ]] || die "usage: nonroot-rt.sh GUEST [PORT]"
tools=$WORK/netbsd-tools
[[ $guest != alpinebsd || -x $tools/mtree ]] ||
    die "no NetBSD tools in $tools: run-guest-rt.sh alpinebsd fetches them"
# shellcheck source=../vm-lib.sh
. "$here/../vm-lib.sh"
trap vm_halt EXIT
vm_boot "$guest" "$port" "$logs/vm-$guest" --ram --memory "${MEMORY:-4096}" || exit 1
# /root is 0700: the tree, repositories and binaries go into /bkonr; atimes
# off, as the listings read every file (NetBSD: on for the restores, by
# the hook /bkonr/t/atimes, since it drops the atimes utimensat sets
# where they are off).  The user bkonr, uid and gid 4321.
case $guest in
    freebsd*) setup='if zfs list zroot >/dev/null 2>&1; then zfs create -o mountpoint=/bkonr -o atime=off zroot/bkonr
            else mkdir /bkonr && mount -u -o noatime,acls /; fi &&
        pw groupadd bkonr -g 4321 && pw useradd bkonr -u 4321 -g 4321 -s /bin/sh -d /nonexistent' ;;
    netbsd11|alpinebsd) setup='PATH=/sbin:/usr/sbin:/bin:/usr/bin:$PATH
        dd if=/dev/zero of=/root/bkonr.img bs=1048576 count=0 seek=256 2>/dev/null &&
        newfs -F -O 2ea -s 256m /root/bkonr.img >/dev/null && vndconfig vnd0 /root/bkonr.img &&
        mkdir /bkonr && mount_ffs -o posix1eacls,noatime /dev/vnd0a /bkonr && mkdir -p /bkonr/t &&
        printf "%s\n" "#!/bin/sh" "PATH=/sbin:/usr/sbin:\$PATH" \
            "if [ \$1 = off ]; then mount -u -o noatime,posix1eacls /bkonr; else mount -u -o posix1eacls /bkonr; fi" \
            >/bkonr/t/atimes && chmod 755 /bkonr/t/atimes &&
        if command -v useradd >/dev/null; then
            groupadd -g 4321 bkonr && useradd -u 4321 -g bkonr -s /bin/sh -d /nonexistent bkonr
        else
            addgroup -g 4321 bkonr && adduser -D -H -u 4321 -G bkonr -s /bin/sh bkonr
        fi' ;;
    dragonfly6) setup='mkdir /bkonr && pw groupadd bkonr -g 4321 && pw useradd bkonr -u 4321 -g 4321 -s /bin/sh -d /nonexistent' ;;
    openbsd8) setup='mkdir /bkonr && mount -u -o noatime / &&
        groupadd -g 4321 bkonr && useradd -u 4321 -g 4321 -s /bin/sh -d /nonexistent bkonr' ;;
    omnios58) setup='zfs create -o mountpoint=/bkonr -o atime=off "$(zfs list -H -o name / | cut -d/ -f1)/bkonr" &&
        groupadd -g 4321 bkonr && useradd -u 4321 -g 4321 -s /bin/sh -d /nonexistent bkonr' ;;
esac
vm_sh "$setup && chmod 755 /bkonr" || exit 1
vm_put "$restic" /bkonr/restic-bko && vm_put "$rustic" /bkonr/rustic-bko &&
    vm_put "$inner" /root/nonroot-inner.sh && vm_put "$here/rt-common.sh" /root/rt-common.sh || exit 1
if [[ $guest == alpinebsd ]]; then
    tar -C "$tools" -cf - . | T=600 vm_ssh 'mkdir -p /root/nbtools && cd /root/nbtools && tar -xf -' || exit 1
fi
{
    echo "######## $guest: $(vm_ssh '/bkonr/restic-bko version' | head -n 1), $(vm_ssh '/bkonr/rustic-bko --version')"
    vm_ssh 'sh /root/nonroot-inner.sh /bkonr/t /bkonr/restic-bko /bkonr/rustic-bko 2>&1' 2>&1
} | tee "$logs/$guest.log" | grep -A100 '^== summary\|^########'
