#!/bin/sh
# Run as root in a test guest by vm-update, after the update's reboot: hand
# the room its file systems have free back to the disk (TRIM), which vm-run
# --discard turns into unallocated clusters, so that the image doesn't keep
# every block an update ever wrote (downloads, replaced packages and kernels,
# copy-on-write's old blocks on ZFS, NILFS2's segments).  A file system that
# can't TRIM has its free room written with zeros instead, which vm-run
# --discard also turns into unallocated clusters: bcachefs (no FITRIM),
# FreeBSD's UFS, NetBSD's and OpenBSD's FFS, DragonFly's HAMMER2 and UFS.
# Prints what it did; exit status 1 if a trim it tried failed (the room then
# stays allocated).
PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin:$PATH; export PATH
say() { echo "trim: $*"; }
rc=0
# zero_fill MOUNTPOINT KEEP_MIB [PREPARE]: write zeros over all but KEEP_MIB
# of the room MOUNTPOINT's file system has free, then delete them.  dd stops
# at a full file system anyway (no failure: the zeros are there); the margin
# is for whatever else writes meanwhile.  PREPARE, a command, gets the zeros'
# directory first.
zero_fill() {
    z=${1%/}/.bko-zero
    mkdir "$z" || { rc=1; return 1; }
    [ -z "$3" ] || $3 "$z" || { rmdir "$z"; rc=1; return 1; }
    mb=$(df -Pk "$1" | awk -v k="$2" 'NR == 2 { print int($4 / 1024) - k }')
    [ "$mb" -le 0 ] || dd if=/dev/zero of="$z/zeros" bs=1048576 count="$mb" 2>/dev/null ||
        say "$1: full before $mb MiB were written"
    sync; rm -rf "$z"; sync
    say "$1: $mb MiB of its free room zeroed"
}
# Whether DEV, the device of a mounted file system, is an encrypted volume
# (OpenBSD's softraid CRYPTO, FreeBSD's GELI, NetBSD's cgd): zeros written
# through it reach the disk as ciphertext, so zero_fill would allocate the
# whole free room instead of freeing it (openbsd8-crypt: an 11.8 GiB overlay
# of a 16 GiB disk).  Its free room stays as it is.
encrypted() {
    case $(uname):$1 in
        FreeBSD:*.eli|NetBSD:/dev/cgd*) true ;;
        OpenBSD:/dev/sd*) d=${1#/dev/}; bioctl "${d%[a-p]}" 2>/dev/null | grep -qw CRYPTO ;;
        # A DUID (fstab's "739d98841b65e745.a"): its disk by hw.disknames.
        OpenBSD:*.[a-p])
            d=$(sysctl -n hw.disknames | tr , '\n' | awk -F: -v u="${1%.?}" '$2 == u { print $1 }')
            [ -n "$d" ] && bioctl "$d" 2>/dev/null | grep -qw CRYPTO ;;
        *) false ;;
    esac
}
# Whether ZFS is there to trim: zpool alone, where the base system has it
# (FreeBSD, NetBSD), would load ZFS's kernel module just to find no pool.
zfs_here() {
    case $(uname) in
        Linux) [ -d /sys/module/zfs ] ;;
        FreeBSD) kldstat -q -m zfs ;;
        NetBSD) modstat zfs 2>/dev/null | grep -qw zfs ;;
        *) true ;;
    esac
}
if [ "$(uname)" = Linux ]; then
    if fstrim --help 2>&1 | grep -q -- '--all'; then
        # util-linux: every mounted file system that can, skipping the rest
        # (ZFS, read-only ones); 64: some were trimmed, some failed (LUKS
        # without allow-discards), 32: all failed.
        fstrim -av; r=$?
        case $r in 0|64) ;; 32) say "no mounted file system could be trimmed" ;; *) rc=1 ;; esac
    elif command -v fstrim >/dev/null; then
        # BusyBox's fstrim takes one mount point at a time.
        for mp in $(awk '$3 ~ /^(ext[234]|xfs|btrfs|f2fs|vfat|jfs|nilfs2)$/ && $4 ~ /(^|,)rw(,|$)/ { print $2 }' /proc/mounts); do
            fstrim -v "$mp" || say "$mp: not trimmed"
        done
    else
        say "no fstrim here"
    fi
    # bcachefs has no FITRIM (fstrim: "the discard operation is not
    # supported", nixos2605): a 121 MiB update left a 1030 MiB overlay.  Its
    # free room is zeroed, but for 64 MiB (it keeps a reserve of its own).
    for mp in $(awk '$3 == "bcachefs" && $4 ~ /(^|,)rw(,|$)/ { print $2 }' /proc/mounts | sort -u); do
        zero_fill "$mp" 64
    done
fi
case $(uname) in
    # UFS and FFS: no TRIM unless the file system's TRIM flag was set
    # (FreeBSD's tunefs -t, unmounted) or the disk takes it, so zeros.
    FreeBSD) zmounts=$(mount -t ufs | awk '!/read-only/ { print $1 ":" $3 }') ;;
    NetBSD|OpenBSD) zmounts=$(mount | awk '$5 == "ffs" && !/read-only/ { print $1 ":" $3 }') ;;
    *) zmounts= ;;
esac
for m in $zmounts; do
    dev=${m%%:*} mp=${m#*:}
    if encrypted "$dev"; then
        say "$mp: on an encrypted volume ($dev), not zeroed"
        continue
    fi
    zero_fill "$mp" 16
done
if command -v zpool >/dev/null && zfs_here; then       # Linux, FreeBSD and OmniOS pools
    for pool in $(zpool list -H -o name); do
        if zpool trim -w "$pool" 2>/tmp/bko-trim.err; then
            say "$pool: trimmed"
        elif grep -qi -e usage -e 'invalid option' -e 'illegal option' /tmp/bko-trim.err && zpool trim "$pool"; then
            # No -w (older illumos): wait while zpool status says it trims.
            n=0
            while zpool status -t "$pool" 2>/dev/null | grep -q "trimmed, started" && [ $n -lt 1500 ]; do
                sleep 5; n=$((n + 5))
            done
            say "$pool: trimmed"
        else
            cat /tmp/bko-trim.err; say "$pool: not trimmed"; rc=1
        fi
    done
fi
if [ "$(uname)" = DragonFly ]; then
    h2_plain() { hammer2 setcomp none "$1" >/dev/null && hammer2 setcheck none "$1" >/dev/null; }
    # Neither HAMMER2 nor DragonFly's virtio disk can TRIM: instead the room
    # each file system has free is written with zeros (all but a little:
    # half a GiB of HAMMER2's for its own use, as with a quarter dd ran out
    # of room, and with a whole one a base-system rebuild left 750 MiB in
    # the image), which vm-run --discard turns into clusters that hold no
    # data.  HAMMER2 writes no zeros where it compresses, so in a directory
    # without compression (nor check codes, for dedup); and what it freed is
    # free for that only after two bulkfree passes (allocated, then possibly
    # free, then free).
    for m in $(mount -t hammer2,ufs | awk '!/read-only/ { gsub(/[(,]/, "", $4); print $4 ":" $3 }'); do
        fs=${m%%:*} mp=${m#*:}
        if [ "$fs" = ufs ]; then
            zero_fill "$mp" 16
            continue
        fi
        { hammer2 bulkfree "$mp" && hammer2 bulkfree "$mp"; } >/dev/null || { say "$mp: bulkfree failed"; rc=1; continue; }
        zero_fill "$mp" 512 h2_plain
        { hammer2 bulkfree "$mp" && hammer2 bulkfree "$mp"; } >/dev/null || rc=1
    done
fi

exit $rc
