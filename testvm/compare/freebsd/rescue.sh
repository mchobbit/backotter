#!/bin/sh
# The FreeBSD side of ../compare-freebsd.sh, run as root in a FreeBSD guest
# standing in for a rescue system, with the system to test attached as vtbd1
# and a blank disk as vtbd2.  It adds torture.sh's files to vtbd1, backs it up
# with restic as a rescue system would (mounted read-only), lays vtbd2 out
# like vtbd1 (partitions, boot code, UFS with the same newfs and tunefs
# settings, or a ZFS pool with the same datasets and properties), restores
# onto it, and compares the two with fsmeta.sh.
#
# Usage: rescue.sh BACKUP [RESTORE]
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  Results in /root/results: before/ and after/ (fsmeta.sh),
# diff.txt, restore.txt (the restore's messages).

set -eu
[ $# -eq 1 ] || [ $# -eq 2 ] || { echo "usage: $0 BACKUP [RESTORE]" >&2; exit 1; }
R=$1 RR=${2:-$1}
here=$(cd "$(dirname "$0")" && pwd)
SRC=vtbd1 DST=vtbd2
OUT=/root/results
export RESTIC_PASSWORD=backotter-compare RESTIC_REPOSITORY=/root/repo
# Back up with $R, restore with $RR.  BackOtter's rustic (a binary named
# *rustic*) takes the same repository as restic, with its own options: the
# real atime, as restic keeps it, and numeric ids, as restic restores them.
export RUSTIC_PASSWORD=$RESTIC_PASSWORD RUSTIC_REPOSITORY=$RESTIC_REPOSITORY
is_rustic() { case $(basename "$1") in *rustic*) true ;; *) false ;; esac; }
bko_init() {
    if is_rustic $R; then $R --no-progress --no-cache init >/dev/null; else $R init -q; fi
}
bko_backup() {
    if is_rustic $R; then $R --no-progress --no-cache backup --set-atime yes "$@" >/dev/null
    else $R backup -q "$@"; fi
}
bko_restore() {
    if is_rustic $RR; then $RR --no-progress --no-cache restore --numeric-id --sparse by-content "$1" "$2"
    else $RR restore -q --sparse "$1" --target "$2"; fi
}
bko_exclude() { if is_rustic $R; then echo "--glob !$(basename "$1")"; else echo "--exclude $1"; fi; }
rm -rf $OUT $RESTIC_REPOSITORY
mkdir -p $OUT /mnt/src /mnt/dst
say() { echo "== $*"; }

# The target must be blank: this script partitions it.
if gpart show $DST >/dev/null 2>&1; then
    echo "rescue: $DST already has a partition table; not touching it" >&2
    exit 1
fi
part() { gpart show -p $SRC | awk -v t="$1" '$4 == t { print $3; exit }'; }
bootpart=$(part freebsd-boot) ufspart=$(part freebsd-ufs) zfspart=$(part freebsd-zfs)
[ -n "$bootpart" ] || { echo "rescue: no freebsd-boot partition on $SRC" >&2; exit 1; }
dstpart() { echo "$1" | sed "s/^$SRC/$DST/"; }

mount_zfs() {  # POOL ALTROOT [readonly]: import and mount every dataset
    if [ "${3:-}" = readonly ]; then
        zpool import -f -N -o readonly=on -R "$2" "$1"
    else
        zpool import -f -N -R "$2" "$1"
    fi
    zfs mount "$(zpool get -H -o value bootfs "$1")"
    zfs mount -a
}

export_pool() {  # POOL [force]: export, retrying while it's briefly busy
    # Reading every file's extended attributes (fsmeta.sh) leaves ZFS on
    # FreeBSD holding the pool for a moment; an export then fails with "pool
    # is busy", and a later one succeeds.
    for _ in 1 2 3 4 5; do
        zpool export "$1" 2>/dev/null && return 0
        sleep 2
    done
    if [ "${2:-}" = force ]; then
        zpool export -f "$1"
    else
        zpool export "$1"
    fi
}

# --- The source, with test files, then read-only -----------------------------------
if [ -n "$ufspart" ]; then
    kind=ufs
    # POSIX.1e ACLs, so that there are some to test.
    tunefs -a enable /dev/$ufspart >/dev/null
    mount /dev/$ufspart /mnt/src
    sh "$here/torture.sh" /mnt/src
    umount /mnt/src
    mount -r /dev/$ufspart /mnt/src
else
    kind=zfs
    pool=$(zdb -l /dev/$zfspart | awk '$1 == "name:" { gsub(/\047/, "", $2); print $2; exit }')
    mount_zfs "$pool" /mnt/src
    sh "$here/torture.sh" /mnt/src
    export_pool "$pool"
    mount_zfs "$pool" /mnt/src readonly
fi
say "source: $kind on $SRC"
gpart show $SRC

say "recording the source's metadata"
sh "$here/fsmeta.sh" /mnt/src $OUT/before

say "backup"
# The UFS journal belongs to the file system, which newfs -j makes anew.
start=$(date +%s)
bko_init
bko_backup $(bko_exclude /mnt/src/.sujournal) /mnt/src
echo "backup took $(( $(date +%s) - start )) s, repository $(du -sh $RESTIC_REPOSITORY | cut -f1)"

# --- The target, laid out like the source -----------------------------------------
if [ $kind = ufs ]; then
    newfs_cmd=$(dumpfs -m /dev/$ufspart | sed -n 's/^newfs /newfs /p')
    acls=$(tunefs -p /dev/$ufspart 2>&1)
    umount /mnt/src
else
    props=$(zfs get -H -o name,property,value -s local -r all "$pool")
    datasets=$(zfs list -H -o name -r "$pool")
    bootfs=$(zpool get -H -o value bootfs "$pool")
    # The vdev's, from its label: the pool property is 0 ("automatic").
    ashift=$(zdb -l /dev/$zfspart | awk '$1 == "ashift:" { print $2; exit }')
    export_pool "$pool" force   # read-only, so forcing loses nothing
fi

say "target: partitions and boot code"
gpart backup $SRC | gpart restore -F $DST
bootidx=${bootpart##*p}
if [ $kind = ufs ]; then
    gpart bootcode -b /boot/pmbr -p /boot/gptboot -i "$bootidx" $DST
    dst=$(dstpart "$ufspart")
    say "target: $(echo "$newfs_cmd" | sed "s#/dev/$ufspart#/dev/$dst#")"
    $(echo "$newfs_cmd" | sed "s#/dev/$ufspart#/dev/$dst#") >/dev/null
    echo "$acls" | grep -q 'POSIX.1e ACLs: (-a) *enabled' && tunefs -a enable /dev/$dst
    echo "$acls" | grep -q 'NFSv4 ACLs: (-N) *enabled' && tunefs -N enable /dev/$dst
    mount /dev/$dst /mnt/dst
else
    gpart bootcode -b /boot/pmbr -p /boot/gptzfsboot -i "$bootidx" $DST
    dst=$(dstpart "$zfspart")
    # Local properties, with the import's altroot taken off mountpoints.
    opts() {  # DATASET: -o/-O property=value for its local properties
        echo "$props" | awk -F'\t' -v ds="$1" -v flag="$2" '
            $1 == ds { v = $3; if ($2 == "mountpoint") { sub("^/mnt/src", "", v); if (v == "") v = "/" }
                       printf "%s %s=%s ", flag, $2, v }'
    }
    say "target: zpool create $pool on $dst, ashift $ashift"
    # shellcheck disable=SC2046
    zpool create -f -o altroot=/mnt/dst -o cachefile=none -o ashift="$ashift" \
        $(opts "$pool" -O) "$pool" /dev/$dst
    for ds in $datasets; do
        [ "$ds" = "$pool" ] && continue
        # shellcheck disable=SC2046
        zfs create -u $(opts "$ds" -o) "$ds"
    done
    zfs mount "$bootfs"
    zfs mount -a
    zfs list -o name,mountpoint,canmount,compression -r "$pool"
fi

say "restore"
start=$(date +%s)
if bko_restore "latest:/mnt/src" /mnt/dst >$OUT/restore.txt 2>&1; then
    echo "restore OK"
else
    echo "restore reported errors:"
fi
cat $OUT/restore.txt
echo "restore took $(( $(date +%s) - start )) s"

say "recording the target's metadata, and comparing"
sh "$here/fsmeta.sh" /mnt/dst $OUT/after
{
    echo "== mtree: type, owner, group, mode, flags, links, size, mtime, symlink target, SHA-256"
    (cd /mnt/dst && mtree -f $OUT/before/spec -X $OUT/before/mtree-exclude) && echo "identical"
    # On ZFS, compression rather than holes decides what a file allocates.
    checks="acls extattrs hardlinks"
    [ $kind = ufs ] && checks="$checks sparse"
    for f in $checks; do
        echo "== $f"
        diff $OUT/before/$f.txt $OUT/after/$f.txt && echo "identical ($(wc -l <$OUT/before/$f.txt | tr -d ' ') lines)"
    done
} >$OUT/diff.txt 2>&1 || true
cat $OUT/diff.txt

if [ $kind = ufs ]; then
    umount /mnt/dst
else
    zpool set bootfs="$bootfs" "$pool"
    export_pool "$pool"
fi
say "done"
