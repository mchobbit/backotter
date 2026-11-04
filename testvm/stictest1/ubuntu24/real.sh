#!/bin/bash
# SticTest1 ubuntu24, the guest side: a fixture of the guest's own examples
# on a ZFS dataset of its root pool (acltype=posix, xattr=sa, as rpool/ROOT),
# backed up and restored in all four directions, a restore over it again,
# the two tools' snapshot nodes, and the backed-up path's ancestors.
# Usage (root, in the guest): real.sh PREFIX RESTIC RUSTIC OUT
# Prints "NEED-RESULT key value" lines for check.sh to judge.
set -u
P=$1 RB=$2 UB=$3 OUT=$4
C=/root/bko/testvm/compare
mkdir -p $OUT
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare RT_TMP=$OUT
. $C/rt-common.sh
POOL=$(zfs list -H -o name / | cut -d/ -f1)
DS=$POOL/$P-real M=/mnt/$P-real
r() { echo "NEED-RESULT $*"; }
cleanup() {
    chattr -R -i -a $M 2>/dev/null
    zfs destroy -r $DS 2>/dev/null
    rm -rf $M
}
cleanup
zfs create -o mountpoint=$M -o acltype=posix -o xattr=sa -o atime=on -o relatime=on $DS || { r setup FAIL; exit 1; }
zfs create $DS/src
A1=$M/src/anc1 A2=$M/src/anc1/anc2 T=$M/src/anc1/anc2/tree
mkdir -p $T
# The guest's own examples
cp -a --preserve=all /usr/bin/ping /usr/bin/mtr-packet $T/ 2>/dev/null
cp -a --preserve=all /usr/lib/snapd/snap-confine $T/ 2>/dev/null
cp -a --preserve=all /var/log/journal $T/journal
cp -a --preserve=all /var/lib/systemd/random-seed $T/random-seed
cp -a --preserve=all /usr/bin/passwd /usr/bin/chage /usr/bin/crontab $T/ 2>/dev/null
for f in /var/cache/man/index.db; do [ -f $f ] && cp -a --sparse=always $f $T/; done
# Modes, owners (with and without names), links, special files
mkdir $T/sticky $T/sgid $T/empty; chmod 1777 $T/sticky; chmod 2775 $T/sgid
echo a >$T/nouid; chown 54321 $T/nouid
echo b >$T/nogid; chgrp 54322 $T/nogid
echo c >$T/noboth; chown 54323:54324 $T/noboth
mkdir $T/nodir; chown 54325:54326 $T/nodir
echo t >$T/tester; chown 1000:1000 $T/tester
ln $T/nouid $T/nouid.hl
ln -s ping $T/link; chown -h 54327:54328 $T/link; touch -h -d '2002-01-01 01:02:03.123456789' $T/link
mknod $T/null c 1 3; mknod $T/rdev0 c 0 0; mknod $T/blk b 7 200; mkfifo $T/fifo
truncate -s 1M $T/sparse; printf x | dd of=$T/sparse bs=1 seek=500000 conv=notrunc status=none
touch "$T/$(printf 'bad-\377\376-name')" 2>/dev/null || echo "NEED-RESULT nonutf8 limit (pool utf8only=on)"
mkdir -p $T/deep/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p; echo deep >$T/deep/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/leaf
# xattrs: user, trusted, security (capability set above), out of order; ACLs
echo x >$T/xattrs; setfattr -n user.zz-first -v 1 $T/xattrs; setfattr -n user.aa -v 2 $T/xattrs
setfattr -n trusted.bko -v t $T/xattrs; setfattr -n security.bko -v s $T/xattrs 2>/dev/null
echo y >$T/acl; setfacl -m u:1000:rw,g:54329:r,m::rw $T/acl
mkdir $T/dacl; setfacl -m u:1000:rwx -d -m u:1000:rx,g:54330:rx $T/dacl
# Inode flags and project IDs (Linux ioctls on ZFS)
echo i >$T/immutable; echo p >$T/append; echo d >$T/nodump; echo n >$T/noatime; echo j >$T/proj
mkdir $T/projdir; chattr -p 77 +P $T/projdir; chattr -p 42 $T/proj
chattr +d $T/nodump; chattr +A $T/noatime
# a socket, which neither tool keeps
rt_mksock $T/sock >/dev/null 2>&1
chattr +i $T/immutable; chattr +a $T/append
# The ancestors' own metadata (backed-up path: $T)
chown 1000:54331 $A1; chmod 0751 $A1; setfattr -n user.anc -v one $A1
chmod 2770 $A2; chgrp 54332 $A2; setfacl -m u:1000:rx $A2; setfattr -n user.anc -v two $A2
chmod 0705 $M/src; setfattr -n user.anc -v src $M/src
touch -d '2003-04-05 06:07:08.5' $A2 $A1 $M/src
r fixture "$(find $T | wc -l) entries"
getcap -r $T 2>/dev/null | sed 's/^/  cap: /'
# fsmeta reads contents: no atime updates while it records (relatime would move them),
# strict atimes while the tools back up (they must leave them alone)
zfs set atime=off $DS/src
python3 $C/fsmeta.py record $M/src $OUT/src.json --skip $T/sock >$OUT/record.log 2>&1
zfs set atime=on $DS/src; zfs set relatime=off $DS/src
zfs snapshot $DS/src@pre

for t in restic rustic; do
    eval B=\$$( [ $t = restic ] && echo RB || echo UB )
    [ $t = rustic ] && zfs rollback $DS/src@pre
    $B -r $OUT/repo-$t init >$OUT/init-$t.log 2>&1
    # BackOtter's flags: real atimes (both forks keep stock's mtime default).
    if [ $t = restic ]; then fl=--with-atime; else fl="--set-atime yes"; fi
    $B -r $OUT/repo-$t backup $fl $T >$OUT/backup-$t.log 2>&1; rc=$?
    r backup-$t $rc
    grep -ci sock $OUT/backup-$t.log | sed "s/^/NEED-RESULT backup-$t-socketmsgs /"
done
zfs set atime=off $DS/src
python3 $C/fsmeta.py record $M/src $OUT/src-after.json --skip $T/sock >/dev/null 2>&1
python3 $C/fsmeta.py compare $OUT/src.json $OUT/src-after.json --json $OUT/diff-src-after.json >$OUT/diff-src-after.txt 2>&1
r source-unchanged $?
# node identity, ancestors included (from /mnt)
rt_nodes $RB $OUT/repo-restic /mnt >$OUT/nodes-restic.txt 2>$OUT/nodes-restic.err
rt_nodes $RB $OUT/repo-rustic /mnt >$OUT/nodes-rustic.txt 2>$OUT/nodes-rustic.err
rt_node_diff $OUT/nodes-restic.txt $OUT/nodes-rustic.txt >$OUT/nodes-diff.txt; r nodes $? "$(wc -l <$OUT/nodes-restic.txt)"
for t in restic rustic; do echo "NEED-RESULT socketnodes $(grep -c sock <$OUT/nodes-$t.txt)"; done
restore() {  # TOOL REPO DST
    if [ $1 = restic ]; then $RB -r $2 restore latest --target $3; else $UB -r $2 restore --numeric-id latest $3; fi
}
for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${d%-*} t=${d#*-}
    zfs create -o mountpoint=$M/dst-$d $DS/dst-$d
    restore $t $OUT/repo-$b $M/dst-$d >$OUT/restore-$d.log 2>&1; r restore-$d $?
    zfs set atime=off $DS/dst-$d  # fsmeta reads contents: keep relatime off the restored atimes
    python3 $C/fsmeta.py record $M/dst-$d$M/src $OUT/dst-$d.json >/dev/null 2>&1
    python3 $C/fsmeta.py compare $OUT/src.json $OUT/dst-$d.json --json $OUT/diff-$d.json >$OUT/diff-$d.txt 2>&1
    r compare-$d $?
    [ -e $M/dst-$d$T/sock ] && r socket-restored-$d 1 || r socket-restored-$d 0
    # restore again over the existing tree (immutable, append-only kept)
    echo changed >>$M/dst-$d$T/tester
    zfs set atime=on $DS/dst-$d
    restore $t $OUT/repo-$b $M/dst-$d >$OUT/over-$d.log 2>&1; r over-$d $?
    zfs set atime=off $DS/dst-$d
    python3 $C/fsmeta.py record $M/dst-$d$M/src $OUT/over-$d.json >/dev/null 2>&1
    python3 $C/fsmeta.py compare $OUT/src.json $OUT/over-$d.json --json $OUT/diff-over-$d.json >$OUT/diff-over-$d.txt 2>&1
    r compare-over-$d $?
    lsattr -d $M/dst-$d$T/immutable $M/dst-$d$T/append 2>&1 | sed 's/^/  lsattr: /'
done
chattr -R -i -a $M 2>/dev/null
rm -rf $OUT/repo-*
zfs destroy -r $DS; rmdir $M 2>/dev/null
exit 0
