#!/bin/sh
# Run as root in a test guest by vm-update, after the update's reboot: hand
# the room its file systems have free back to the disk (TRIM), which vm-run
# --discard turns into unallocated clusters, so that the image doesn't keep
# every block an update ever wrote (downloads, replaced packages and kernels,
# copy-on-write's old blocks on ZFS, NILFS2's segments).  Prints what it did;
# exit status 1 if a trim it tried failed (the room then stays allocated).
PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin:$PATH; export PATH
say() { echo "trim: $*"; }
rc=0
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
fi
if command -v zpool >/dev/null; then       # Linux, FreeBSD and OmniOS pools
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
exit $rc
