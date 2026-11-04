#!/bin/bash
# In the rocky10 throwaway guest, as root: fixture on a fresh XFS, four
# directions, restore over immutable/append-only, non-root run, judge.
set -u
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
mkfs.xfs -q -f /dev/vdb && mkdir -p $W && mount -o noatime /dev/vdb $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L
newfs() {  # NAME: a fresh XFS (mkfs.xfs's defaults, as the root's) on a loop device at /mnt/NAME
    truncate -s ${2:-1500M} $W/$1.img && mkfs.xfs -q -f $W/$1.img && mkdir -p /mnt/$1 &&
        mount -o loop,noatime $W/$1.img /mnt/$1
}
st() { echo "$1 $2" >>$L/status; }
{ getenforce; xfs_info / | grep -o 'crc=[01]\|reflink=[01]\|bigtime=[01]\|nrext64=[01]\|rmapbt=[01]\|projid32bit=[01]\|ftype=[01]' | tr '\n' ' '; echo; } >$L/env.txt
grep -c 'type=AVC' /var/log/audit/audit.log >$L/avc0 2>/dev/null || echo 0 >$L/avc0
newfs src || { echo "FAIL harness: no source fs"; exit 2; }
xfs_info /mnt/src | grep -o 'crc=[01]\|reflink=[01]\|bigtime=[01]\|nrext64=[01]\|rmapbt=[01]\|projid32bit=[01]\|ftype=[01]' | tr '\n' ' ' >>$L/env.txt; echo >>$L/env.txt
bash $B/fixture.sh /mnt/src 2>$L/fixture.err
P=/mnt/src/up1/up2/tree
sync; python3 $B/fsmeta.py record /mnt/src $L/src.json
( cd /; time rt_backup $RESTIC $W/repo-restic $P ) >$L/backup-restic.log 2>&1; st backup-restic $?
( cd /; time rt_backup $RUSTIC $W/repo-rustic $P ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
python3 $B/fsmeta.py record /mnt/src $L/src-after.json
python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
rt_nodes $RESTIC $W/repo-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
rt_nodes $RESTIC $W/repo-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
for t in restic rustic; do  # the directories above, as each snapshot holds them
    $RESTIC -r $W/repo-$t ls --json latest 2>/dev/null | grep -E '"path":"/(mnt|mnt/src|mnt/src/up1|mnt/src/up1/up2)"' >$L/above-$t.json
done
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
    IFS=: read n b r <<<"$d"
    newfs d$n
    restore $r $W/repo-$b /mnt/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; python3 $B/fsmeta.py record /mnt/d$n/mnt/src $L/$n.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
done
# Restore over existing immutable and append-only files (and an immutable
# directory), changed since: each tool over its own restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=/mnt/drr || D=/mnt/duu
    X=$D$P
    chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
        echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
        echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
    lsattr -d $X/immutable $X/append-only $X/immutable-dir >$L/$n.before 2>&1
    restore $t $W/repo-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; python3 $B/fsmeta.py record $D/mnt/src $L/$n.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
done
grep -c 'type=AVC' /var/log/audit/audit.log >$L/avc1 2>/dev/null || echo 0 >$L/avc1
getenforce >$L/enforce-after
# One non-root run (nonroot-inner.sh: backup as root, restores as uid 4321).
newfs nr
# uid 4321 can't reach /root (0550): the binaries go where it can.
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
python3 $B/judge.py $L
# The logs for the host (the guest has no tar).
python3 -c 'import tarfile; t = tarfile.open("/root/bk/logs.tar", "w"); t.add("/mnt/bkw/logs", arcname="."); t.close()'
