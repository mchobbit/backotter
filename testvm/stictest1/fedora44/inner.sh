#!/bin/bash
# In the fedora44 throwaway guest, as root: for each file system the guest
# mounts (btrfs /, compress=zstd:1, subvolumes; ext4 /boot), the fixture on
# a fresh one made like it, four directions, restore over immutable/
# append-only, then one non-root run (btrfs), and the judge.
set -u
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw L0=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
mkfs.xfs -q -f /dev/vdb && mkdir -p $W && mount -o noatime /dev/vdb $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L0
BOPTS=$(findmnt -rn -o OPTIONS / | tr , '\n' | grep -E '^compress' | head -1)   # compress=zstd:1
newfs() {  # FSTYPE NAME: a fresh file system like the guest's on a loop device at /mnt/NAME
    truncate -s ${3:-1500M} $W/$2.img && mkdir -p /mnt/$2 || return 1
    case $1 in
    btrfs) mkfs.btrfs -q -f $W/$2.img >/dev/null && mount -o loop,noatime,$BOPTS $W/$2.img /mnt/$2 ;;
    ext4) mkfs.ext4 -q -F $W/$2.img && mount -o loop,noatime $W/$2.img /mnt/$2 ;;
    esac || return 1
    # A fresh ext4's lost+found isn't the snapshot's: rustic would note an
    # additional entry in a restore target.
    [[ $2 == src* ]] || rmdir /mnt/$2/lost+found 2>/dev/null
    return 0
}
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
grep -c 'type=AVC' /var/log/audit/audit.log >$L0/avc0 2>/dev/null || echo 0 >$L0/avc0
getenforce >$L0/enforce-before
for FS in btrfs ext4; do
    L=$L0/$FS; mkdir -p $L
    st() { echo "$1 $2" >>$L/status; }
    if [[ $FS == btrfs ]]; then
        { findmnt -rn -o OPTIONS / | tr , '\n' | grep -E '^(compress|space_cache)' | sort | tr '\n' ' '; echo; } >$L/env.txt
    else
        { tune2fs -l $(findmnt -rn -o SOURCE /boot) | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -vE '^(needs_recovery|orphan_present)?$' | sort | tr '\n' ' '; echo; } >$L/env.txt
    fi
    newfs $FS src$FS || { echo "FAIL harness: no source $FS"; continue; }
    S=/mnt/src$FS
    if [[ $FS == btrfs ]]; then
        findmnt -rn -o OPTIONS $S | tr , '\n' | grep -E '^(compress|space_cache)' | sort | tr '\n' ' ' >>$L/env.txt
    else
        tune2fs -l $(findmnt -rn -o SOURCE $S) | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -vE '^(needs_recovery|orphan_present)?$' | sort | tr '\n' ' ' >>$L/env.txt
    fi
    echo >>$L/env.txt
    bash $B/fixture.sh $S $FS 2>$L/fixture.err
    P=$S/up1/up2/tree
    sync; python3 $B/fsmeta.py record $S $L/src.json
    ( cd /; time rt_backup $RESTIC $W/repo-$FS-restic $P ) >$L/backup-restic.log 2>&1; st backup-restic $?
    ( cd /; time rt_backup $RUSTIC $W/repo-$FS-rustic $P ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
    python3 $B/fsmeta.py record $S $L/src-after.json
    python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
    rt_nodes $RESTIC $W/repo-$FS-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
    rt_nodes $RESTIC $W/repo-$FS-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
    rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
    for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
        IFS=: read n b r <<<"$d"
        newfs $FS d$FS$n
        restore $r $W/repo-$FS-$b /mnt/d$FS$n >$L/restore-$n.log 2>&1; st restore-$n $?
        sync; python3 $B/fsmeta.py record /mnt/d$FS$n$S $L/$n.json
        python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    done
    # Restore over existing immutable and append-only files (and an immutable
    # directory), changed since: each tool over its own restore.
    for t in restic rustic; do
        n=over-$t; [[ $t == restic ]] && D=/mnt/d${FS}rr || D=/mnt/d${FS}uu
        X=$D$P
        chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
            echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
            echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
        lsattr -d $X/immutable $X/append-only $X/immutable-dir >$L/$n.before 2>&1
        restore $t $W/repo-$FS-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
        sync; python3 $B/fsmeta.py record $D$S $L/$n.json
        python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    done
    # Free the loop devices and RAM of this pass.
    for m in /mnt/src$FS /mnt/d$FS*; do umount -l $m 2>/dev/null; done
    rm -f $W/*.img
done
grep -c 'type=AVC' /var/log/audit/audit.log >$L0/avc1 2>/dev/null || echo 0 >$L0/avc1
getenforce >$L0/enforce-after
# One non-root run on btrfs (nonroot-inner.sh: backup as root, restores as uid 4321).
newfs btrfs nr
# uid 4321 can't reach /root (0550): the binaries go where it can.
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L0/nonroot.log 2>&1; echo "nonroot $?" >>$L0/status
for FS in btrfs ext4; do python3 $B/judge.py $L0 $FS; done
python3 -c 'import tarfile; t = tarfile.open("/root/bk/logs.tar", "w"); t.add("/mnt/bkw/logs", arcname="."); t.close()'
