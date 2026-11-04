#!/bin/bash
# SticTest1, arch: the part of check.sh that runs in the throwaway guest, as
# root.  Makes ext4 file systems like the guest's root (its own mkfs.ext4,
# the root's features and inode size) in image files under /root/bko-st/img,
# builds the fixture on one, backs it up with both tools, restores in the
# four directions (whole snapshot, so the directories above the backed-up
# path come back too), restores twice over existing immutable/append-only
# trees, and runs one non-root backup+restore.  The images go back to the
# host (tar -S), where fsmeta.py compares them.  Lines "R STATUS need: ..."
# are results; the rest is log.
set -u
export LC_ALL=C
B=/root/bko-st
cd $B || exit 2
R=$B/restic-bko U=$B/rustic-bko
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
. $B/rt-common.sh
res() { echo "R $*"; }
IMG=$B/img M=/bko
mkdir -p $IMG $M $B/logs
SRCP=$M/src/anc1/anc2/tree

echo "== tools"
$R version; $U --version
file $R $U 2>/dev/null
okrun=1
$R version >/dev/null 2>&1 || okrun=0
$U --version >/dev/null 2>&1 || okrun=0
[ $okrun = 1 ] && res "PASS tools run natively: restic-bko and rustic-bko start on Arch (glibc $(ldd --version | head -n1 | awk '{print $NF}'))" ||
    res "FAIL tools run natively: one of the tools does not start"

# --- file systems like the root ---------------------------------------------------
rootdev=$(findmnt -no SOURCE /)
feat=$(tune2fs -l "$rootdev" | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' |
    grep -vxE 'needs_recovery|orphan_present|' | paste -sd, -)
isz=$(tune2fs -l "$rootdev" | sed -n 's/^Inode size: *//p')
bsz=$(tune2fs -l "$rootdev" | sed -n 's/^Block size: *//p')
echo "root $rootdev features: $feat; inode size $isz; block size $bsz"
mkfs_like() {  # NAME SIZE
    rm -f $IMG/$1.img; truncate -s $2 $IMG/$1.img
    mkfs.ext4 -q -F -b "$bsz" -I "$isz" -O "$feat" -L bko$1 $IMG/$1.img || { echo "mkfs $1 failed"; return 1; }
    mkdir -p $M/$1; mount -o loop $IMG/$1.img $M/$1
}
mkfs_like src 512M || exit 2
mfeat=$(tune2fs -l $IMG/src.img | sed -n 's/^Filesystem features: *//p')
echo "scratch features: $mfeat; block size $(tune2fs -l $IMG/src.img | sed -n 's/^Block size: *//p')"
findmnt -no OPTIONS $M/src

# --- the fixture ------------------------------------------------------------------------
T=$SRCP
mkdir -p $T
try() { "$@" 2>/dev/null || echo "fixture: skipped (unsupported here): $*"; }
flag() {  # FLAG PATH
    if ! chattr "+$1" "$2" 2>/dev/null; then echo "fixture: skipped (unsupported): chattr +$1 $2"
    elif [[ $(lsattr -d "$2" 2>/dev/null | cut -d' ' -f1) != *"$1"* ]]; then echo "fixture: not kept: chattr +$1 $2"
    else echo "fixture: flag $1 on ${2#$T/}"; fi
}
# ownership, set-id, sticky
touch $T/no-such-user; chown 4321:4322 $T/no-such-user
install -m 0644 /dev/null $T/setuid; chown 4321:4322 $T/setuid; chmod 4755 $T/setuid
install -m 0644 /dev/null $T/setgid; chmod 2755 $T/setgid
mkdir -p $T/sticky-dir; chmod 1777 $T/sticky-dir
mkdir -p $T/setgid-dir; chown 0:4322 $T/setgid-dir; chmod 2775 $T/setgid-dir
mkdir -p $T/unowned-dir; chown 54321:54322 $T/unowned-dir; chmod 0710 $T/unowned-dir
# POSIX ACLs: synthetic, and the guest's own (/var/log/journal and a journal file)
touch $T/acl-file; try setfacl -m u:1234:rwx,g:5678:r $T/acl-file
mkdir -p $T/acl-dir; try setfacl -m u:1234:rx $T/acl-dir; try setfacl -d -m u:1234:rwx,g:5678:rx $T/acl-dir
mkdir -p $T/journal-dir
if [ -d /var/log/journal ]; then
    chown --reference=/var/log/journal $T/journal-dir; chmod --reference=/var/log/journal $T/journal-dir
    getfacl -p /var/log/journal | setfacl --set-file=- $T/journal-dir
    jf=$(find /var/log/journal -type f -name '*.journal' | head -n 1)
    if [ -n "$jf" ]; then
        head -c 65536 "$jf" >$T/journal-dir/system.journal
        chown --reference="$jf" $T/journal-dir/system.journal; chmod --reference="$jf" $T/journal-dir/system.journal
        getfacl -p "$jf" | setfacl --set-file=- $T/journal-dir/system.journal
    fi
fi
echo "fixture ACLs:"; getfacl -p $T/journal-dir $T/journal-dir/system.journal 2>&1 | grep -v '^#' | paste -sd' ' -
# xattrs: user (incl. empty, binary, large), trusted, security
touch $T/xattr-file
try setfattr -n user.backotter -v hello $T/xattr-file
try setfattr -n user.binary -v 0x00ff10fe7f $T/xattr-file
try setfattr -n user.empty $T/xattr-file
try setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" $T/xattr-file
try setfattr -n trusted.backotter -v root-only $T/xattr-file
try setfattr -n security.backotter -v sec $T/xattr-file
mkdir -p $T/xattr-dir; try setfattr -n user.dir -v d $T/xattr-dir; try setfattr -n trusted.dir -v t $T/xattr-dir
# capabilities: the guest's own newuidmap/newgidmap, and a synthetic one
cp -a /usr/bin/newuidmap $T/newuidmap; cp -a /usr/bin/newgidmap $T/newgidmap
cp /usr/bin/true $T/cap-binary; try setcap cap_net_raw,cap_net_admin+ep $T/cap-binary
echo "fixture caps: $(getcap $T/newuidmap $T/newgidmap $T/cap-binary | paste -sd' ' -)"
# hard links, symlinks (own times, trusted xattr on a symlink)
echo linked >$T/hardlink-a; ln -f $T/hardlink-a $T/hardlink-b; mkdir -p $T/sub; ln -f $T/hardlink-a $T/sub/hardlink-c
ln -sfn hardlink-a $T/symlink; ln -sfn /nonexistent/target $T/symlink-dangling
ln -sfn hardlink-a $T/symlink-xattr; try setfattr -h -n trusted.lnk -v on-a-symlink $T/symlink-xattr
# devices (one with rdev 0), FIFO, socket
mknod $T/chardev c 1 3; mknod $T/blockdev b 7 0; try mknod $T/rdev0 c 0 0; mkfifo $T/fifo
rt_mksock $T/socket 2>/dev/null || {
    systemd-socket-activate -l $T/socket true >/dev/null 2>&1 & sp=$!
    for i in 1 2 3 4 5 6 7 8 9 10; do [ -S $T/socket ] && break; sleep 0.3; done; kill $sp 2>/dev/null; wait $sp 2>/dev/null; }
[ -S $T/socket ] && echo "fixture: socket made" || echo "fixture: NO socket"
# sparse files and allocation
truncate -s 64M $T/sparse
echo start | dd of=$T/sparse conv=notrunc status=none
head -c 1M /dev/urandom | dd of=$T/sparse bs=1M seek=32 conv=notrunc status=none
echo end | dd of=$T/sparse bs=1 seek=$((64 * 1048576 - 4)) conv=notrunc status=none
truncate -s 8M $T/sparse-all-hole
head -c 256K /dev/urandom >$T/sparse-small; truncate -s +8K $T/sparse-small; head -c 256K /dev/urandom >>$T/sparse-small
head -c 4096 /dev/urandom >$T/sparse-tail; truncate -s 4099 $T/sparse-tail
head -c 4M /dev/zero >$T/zeros; try fallocate -l 4M $T/preallocated
# real holes from the guest's own files, if any
for f in /usr/lib/locale/locale-archive; do
    [ -f $f ] && cp --sparse=always $f $T/copy-$(basename $f) && echo "fixture: $f copied sparse ($(stat -c '%s %b' $T/copy-$(basename $f)))"
done
# names, empty and deep directories
touch "$T/name with spaces" "$T/new"$'\n'"line" "$T/"$'\xff\xfe'"-not-utf8"
mkdir -p "$T/dir-"$'\xe9'"-latin1"; touch "$T/dir-"$'\xe9'"-latin1/f"
touch "$T/$(printf 'n%.0s' {1..250})"
mkdir -p "$T/deep$(printf '/level%s' {1..40})"; touch "$T/deep$(printf '/level%s' {1..40})/bottom"
touch $T/empty; mkdir -p $T/empty-dir
# times
touch -d '1970-01-02 00:00:00.123456789' $T/old
touch -d '2100-01-01 12:00:00.987654321' $T/future
echo atime >$T/atime; mkdir -p $T/atime-dir; ln -sfn atime $T/atime-symlink
for p in $T/atime $T/atime-dir $T/atime-symlink $T/symlink-xattr; do
    touch -h -m -d '2002-03-04 05:06:07.111111111' $p; touch -h -a -d '2003-04-05 06:07:08.222222222' $p
done
# inode flags, last (immutable/append-only file and directory)
touch $T/nodump; flag d $T/nodump
echo noatime >$T/noatime; flag A $T/noatime
echo sync >$T/sync; flag S $T/sync
mkdir -p $T/dirsync-dir; flag D $T/dirsync-dir
echo journal >$T/data-journal; flag j $T/data-journal
echo c >$T/compressed; flag c $T/compressed
touch $T/nocow; flag C $T/nocow
echo p >$T/projid-file; try chattr -p 42 $T/projid-file
echo append >$T/append-only; flag a $T/append-only
echo fixed >$T/immutable; flag i $T/immutable
mkdir -p $T/immutable-dir; echo inside >$T/immutable-dir/f
mkdir -p $T/append-dir; echo inside >$T/append-dir/f
touch -d '2001-02-03 04:05:06.5' $T/immutable-dir $T/append-dir
flag i $T/immutable-dir; flag a $T/append-dir
# the directories above the backed-up path, real metadata of their own
touch -d '2001-02-03 04:05:06.123' $T
chown 1234:5678 $M/src/anc1; chmod 0750 $M/src/anc1; setfattr -n user.anc -v one $M/src/anc1
chown 0:5678 $M/src/anc1/anc2; chmod 2751 $M/src/anc1/anc2; setfacl -m u:1234:rx $M/src/anc1/anc2
touch -d '2005-06-07 08:09:10.111' $M/src/anc1/anc2
touch -d '2006-07-08 09:10:11.222' $M/src/anc1
touch -d '2007-08-09 10:11:12.333' $M/src
mount -o remount,noatime $M/src
echo "fixture: $(find $T | wc -l) paths; lsattr:"; lsattr -a -R $T 2>/dev/null | grep -v -- '^--------------e-------\|^-------------e-------\|^$' | grep -E '^[^ ]*[iadASDj]' | head -20

# --- backups ----------------------------------------------------------------------------
msgs() { grep -v -e '^\[INFO\]' -e '^Summary:' "$1" | grep -iE 'warn|error|panic|fail|not restored|unsupported|sock|skip|ignor' ; }
bk() {  # BIN REPO PATH LOG
    if rt_is_rustic $1; then
        { $1 --no-progress --no-cache -r $2 init && $1 --no-progress --no-cache -r $2 backup --set-atime yes $3; } >$4 2>&1
    else
        { $1 -r $2 init -q && $1 -r $2 backup -q --with-atime $3; } >$4 2>&1
    fi
}
rs() {  # BIN REPO SNAPSHOT DST LOG
    if rt_is_rustic $1; then $1 -r $2 --no-progress --no-cache restore --numeric-id $3 $4 >$5 2>&1
    else $1 -r $2 restore $3 --target $4 >$5 2>&1; fi
}
echo "== backups"
bk $R $B/repo-restic $SRCP $B/logs/backup-restic.log; br=$?
bk $U $B/repo-rustic $SRCP $B/logs/backup-rustic.log; bu=$?
echo "restic backup exit $br; rustic backup exit $bu"; tail -n 5 $B/logs/backup-*.log
bmsg=$(msgs $B/logs/backup-restic.log; msgs $B/logs/backup-rustic.log)

# --- snapshot identity -----------------------------------------------------------------
echo "== snapshot nodes"
rt_nodes $R $B/repo-restic $SRCP >$B/nodes.restic 2>$B/logs/nodes.err
rt_nodes $R $B/repo-rustic $SRCP >$B/nodes.rustic 2>>$B/logs/nodes.err
if [ $br -ne 0 ] || [ $bu -ne 0 ] || ! [ -s $B/nodes.restic ] || ! [ -s $B/nodes.rustic ]; then
    res "FAIL same repository format: no snapshots to compare (backup exits restic $br, rustic $bu; $(head -c 300 $B/logs/nodes.err))"
elif d=$(rt_node_diff $B/nodes.restic $B/nodes.rustic); then
    res "PASS same repository format: snapshots node-identical ($(wc -l <$B/nodes.restic) nodes, all fields but content/subtree/device_id)"
else
    res "FAIL same repository format: $(printf '%s' "$d" | head -n 8 | paste -sd';' -)"
fi
if grep -q $'^socket\t' $B/nodes.restic $B/nodes.rustic || [ -n "$(printf '%s' "$bmsg" | grep -i sock)" ]; then
    sockbad="socket node or message at backup"
else sockbad=""; fi
# the directories above: /bko/src, anc1, anc2 (nodes "src", "src/anc1", "src/anc1/anc2" under /bko)
anc() { rt_nodes $R $1 $M 2>/dev/null | awk -F '\t' '$1 == "src" || $1 == "src/anc1" || $1 == "src/anc1/anc2"'; }
anc $B/repo-restic >$B/anc.restic; anc $B/repo-rustic >$B/anc.rustic
echo "ancestors, restic's:"; cat $B/anc.restic; echo "ancestors, rustic's:"; cat $B/anc.rustic
ancdiff=$(rt_node_diff $B/anc.restic $B/anc.rustic | head -n 6 | paste -sd';' -)
grep -q $'^src/anc1\t.*uid=1234' $B/anc.restic && ar=yes || ar=no
grep -q $'^src/anc1\t.*uid=1234' $B/anc.rustic && au=yes || au=no
echo "ANC restic-real=$ar rustic-real=$au diff=$ancdiff"
echo "ANCDIFF $ancdiff"
echo "ANCREAL restic=$ar rustic=$au"

# --- restores in four directions ---------------------------------------------------------
echo "== restores"
rmsg=""
for d in rr uu ru ur ovr ovu; do mkfs_like $d 256M || exit 2; done
rs $R $B/repo-restic latest $M/rr $B/logs/restore-rr.log; e_rr=$?
rs $U $B/repo-rustic latest $M/uu $B/logs/restore-uu.log; e_uu=$?
rs $U $B/repo-restic latest $M/ru $B/logs/restore-ru.log; e_ru=$?
rs $R $B/repo-rustic latest $M/ur $B/logs/restore-ur.log; e_ur=$?
for d in rr uu ru ur; do
    eval "e=\$e_$d"; m=$(msgs $B/logs/restore-$d.log | head -n 5 | paste -sd';' -)
    echo "EXIT $d $e MSG $m"
    [ -n "$m" ] && rmsg="$rmsg $d:$m"
done

# --- restores over existing immutable/append-only trees ----------------------------------
echo "== restores over existing trees"
change() {  # ROOT: change the restored tree under ROOT (flags cleared, then put back)
    t=$1$SRCP
    chattr -i $t/immutable $t/immutable-dir; chattr -a $t/append-only $t/append-dir
    printf XX | dd of=$t/immutable conv=notrunc 2>/dev/null; echo changed >>$t/immutable; chmod 600 $t/immutable
    echo changed >>$t/append-only; chmod 600 $t/append-only
    echo changed >>$t/immutable-dir/f; echo changed >>$t/append-dir/f
    chmod 700 $t/immutable-dir; touch $t/immutable-dir $t/append-dir
    chattr +i $t/immutable $t/immutable-dir; chattr +a $t/append-only $t/append-dir
    echo "changed: $(lsattr -d $t/immutable $t/append-only $t/immutable-dir $t/append-dir | paste -sd' ' -)"
}
for p in "ovr $R $B/repo-restic" "ovu $U $B/repo-rustic"; do
    set -- $p; d=$1 bin=$2 repo=$3
    rs $bin $repo latest $M/$d $B/logs/over-$d-1.log; e1=$?
    rs $bin $repo latest $M/$d $B/logs/over-$d-2.log; e2=$?   # unchanged tree
    change $M/$d
    rs $bin $repo latest $M/$d $B/logs/over-$d-3.log; e3=$?   # changed contents behind i/a
    m=$(for i in 1 2 3; do msgs $B/logs/over-$d-$i.log; done | head -n 6 | paste -sd';' -)
    echo "OVER $d exits $e1 $e2 $e3 MSG $m"
    tail -n 8 $B/logs/over-$d-3.log
done

# --- one non-root backup and restore -------------------------------------------------------
echo "== non-root"
mkfs_like nr 256M || exit 2
NR=$M/nr; UID_=4321
mkdir -p $NR/src $NR/home $NR/out-rr $NR/out-uu; chown $UID_:$UID_ $NR/src $NR/home $NR/out-rr $NR/out-uu
as_user() { setpriv --reuid=$UID_ --regid=$UID_ --clear-groups env HOME=$NR/home RESTIC_PASSWORD=$RESTIC_PASSWORD RUSTIC_PASSWORD=$RUSTIC_PASSWORD "$@"; }
cp $R $U $NR/home/; chown $UID_ $NR/home/*-bko
as_user sh -c '
S='$NR'/src
echo data >$S/file; chmod 640 $S/file
setfattr -n user.mine -v x $S/file
setfacl -m u:1234:r $S/file
mkdir $S/dir; chmod 700 $S/dir; setfacl -d -m u:1234:rx $S/dir; echo in >$S/dir/f
ln $S/file $S/hard; ln -s file $S/link; mkfifo $S/fifo
truncate -s 4M $S/sparse; echo x | dd of=$S/sparse bs=1M seek=2 conv=notrunc status=none
touch $S/nodump; chattr +d $S/nodump
touch -d "2004-05-06 07:08:09.123456789" $S/file $S/dir
touch -h -d "2004-05-06 07:08:09.5" $S/link
' 2>&1
touch -d '2004-05-06 07:08:09' $NR/src
mount -o remount,noatime $NR
H=$NR/home
as_user sh -c "cd $H && { ./restic-bko -r $NR/home/repo-restic --no-cache init -q && ./restic-bko -r $NR/home/repo-restic --no-cache backup -q --with-atime $NR/src; }" >$B/logs/nr-backup-restic.log 2>&1; nbr=$?
as_user sh -c "cd $H && { ./rustic-bko --no-progress --no-cache -r $NR/home/repo-rustic init && ./rustic-bko --no-progress --no-cache -r $NR/home/repo-rustic backup --set-atime yes $NR/src; }" >$B/logs/nr-backup-rustic.log 2>&1; nbu=$?
as_user sh -c "cd $H && ./restic-bko -r $NR/home/repo-restic --no-cache restore latest:$NR/src --target $NR/out-rr" >$B/logs/nr-restore-rr.log 2>&1; nrr=$?
as_user sh -c "cd $H && ./rustic-bko --no-progress --no-cache -r $NR/home/repo-rustic restore --numeric-id latest:$NR/src $NR/out-uu" >$B/logs/nr-restore-uu.log 2>&1; nru=$?
nm=$(for f in $B/logs/nr-*.log; do msgs $f; done | head -n 6 | paste -sd';' -)
echo "NONROOT exits backup $nbr $nbu restore $nrr $nru MSG $nm"
cat $B/logs/nr-*.log | grep -v '^\[INFO\]' | tail -n 20

echo "BACKUPMSG $(printf '%s' "$bmsg" | head -n 5 | paste -sd';' -)"
echo "RESTOREMSG $rmsg"
echo "SOCKBAD $sockbad"
sync
for d in src rr uu ru ur ovr ovu nr; do umount $M/$d || umount -l $M/$d; done
ls -ls $IMG
echo "== inner done"
