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
if [ "$(uname)" = DragonFly ]; then
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
        fs=${m%%:*} mp=${m#*:} keep=16
        z=${mp%/}/.bko-zero
        if [ "$fs" = hammer2 ]; then
            keep=512
            { hammer2 bulkfree "$mp" && hammer2 bulkfree "$mp"; } >/dev/null || { say "$mp: bulkfree failed"; rc=1; continue; }
        fi
        mkdir "$z" || { rc=1; continue; }
        [ "$fs" != hammer2 ] || { hammer2 setcomp none "$z" && hammer2 setcheck none "$z"; } >/dev/null || rc=1
        mb=$(df -m "$mp" | awk -v k=$keep 'NR == 2 { print $4 - k }')
        [ "$mb" -le 0 ] || dd if=/dev/zero of="$z/zeros" bs=1m count="$mb" 2>/dev/null || rc=1
        sync; rm -rf "$z"; sync
        [ "$fs" != hammer2 ] || { hammer2 bulkfree "$mp" && hammer2 bulkfree "$mp"; } >/dev/null || rc=1
        say "$mp: $mb MiB of its free room zeroed"
    done
fi

exit $rc
