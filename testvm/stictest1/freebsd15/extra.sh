#!/bin/sh
# SticTest1 freebsd15: what roundtrip.sh doesn't cover, in a throwaway guest,
# as root.  A ZFS dataset of its own (the guest's own properties: nfsv4
# ACLs, xattr=sa, compression; atime off for the listings) holds a fixture
# copied from the guest's real files: libc.so.7 and su (schg, su setuid),
# /boot/entropy (uarch,nodump), a ZFS project ID handed down, sappnd, system
# and user extattrs, an NFSv4 ACL, a FIFO, a device node where mknod works,
# owners with no name, and ancestors with their own metadata
# (/X and /X/top above the backed-up /X/top/data).  Backed up by each tool,
# restored by each (4 directions), compared with fsmeta.sh, ZFS project IDs
# compared, the two snapshots compared node by node, ancestors included;
# then each restore is restored over again with its schg/sappnd files
# changed under their flags, compared again but for its own ".", which must
# stay as it was (rt_dot: holding a tree, the target isn't fresh, so it
# keeps its metadata, as stock).  Prints "R <key> PASS|FAIL|LIMIT detail".
#
# Usage: extra.sh DIR-WITH-BINARIES-AND-SCRIPTS
H=$1
RESTIC=$H/restic-bko RUSTIC=$H/rustic-bko
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
. $H/rt-common.sh
DS=zroot/bkostx X=/bkostx SRC=/bkostx/top/data O=/tmp/bkostx
r() { echo "R $*"; }
zfs destroy -r $DS 2>/dev/null
zfs create -o mountpoint=$X -o atime=off $DS || { r setup FAIL "can't create $DS"; exit 1; }
rm -rf $O; mkdir -p $O $SRC
D=$SRC
# Real examples from the guest.
cp -p /lib/libc.so.7 $D/libc.so.7 && chflags schg $D/libc.so.7
cp -p /usr/bin/su $D/su && chflags schg $D/su
cp -p /boot/entropy $D/entropy 2>/dev/null || { head -c 4096 /dev/random >$D/entropy; chmod 600 $D/entropy; }
chflags uarch,nodump $D/entropy
echo log >$D/sappnd-log && chflags sappnd $D/sappnd-log
echo log >$D/sunlnk && chflags sunlnk $D/sunlnk
echo h >$D/hidden-rdonly && chflags hidden,rdonly $D/hidden-rdonly
echo x >$D/xattrs; setextattr user bko-user v1 $D/xattrs; setextattr system bko-sys v2 $D/xattrs
printf '\000\001' | setextattr -i user bko-bin $D/xattrs
echo a >$D/acl && setfacl -m u:nobody:rwxp::allow,g:operator:w::deny $D/acl
mkdir -p $D/proj && zfs project -p 77 -s $D/proj && echo p >$D/proj/inherited
mkdir -p $D/proj-own && echo q >$D/proj-own/f && zfs project -p 99 $D/proj-own/f
mkfifo $D/fifo
if mknod $D/dev-null c 0 0 2>$O/mknod.err; then r devnodes-made PASS "mknod c 0 0 worked"
else r devnodes-made LIMIT "mknod on ZFS: $(cat $O/mknod.err)"; fi
touch $D/noname && chown 4242:4343 $D/noname
mkdir -p $D/sticky && chmod 1777 $D/sticky
mkdir -p $D/empty
deep=$D/deep; i=1; while [ $i -le 30 ]; do deep=$deep/d$i; i=$((i + 1)); done; mkdir -p $deep
touch "$D/$(printf '\351t\351')-latin1"
ln -f $D/xattrs $D/xattrs-link
ln -sfh libc.so.7 $D/symlink && touch -h -d 2002-02-02T02:02:02.000000007 $D/symlink
truncate -s 64m $D/sparse && echo mid | dd of=$D/sparse bs=1m seek=32 conv=notrunc 2>/dev/null
# Ancestors with metadata of their own.
chown 1234:5678 $X/top && chmod 2751 $X/top && setextattr user bko-anc anc $X/top
setfacl -m u:nobody:rx::allow $X/top
touch -d 2005-05-05T05:05:05.5 $X/top; chflags uarch,nodump $X/top
chmod 711 $X && touch -d 2006-06-06T06:06:06.6 $X
touch -d 2007-07-07T07:07:07.7 $D
projs() { (cd $1 && find . \( -type f -o -type d \) -print0 | xargs -0 zfs project -d 2>/dev/null | sed "s|$1|.|" | sort); }
projs $SRC >$O/proj.before
anc() { echo "$(stat -f '%Sp %u %g %Xf %Fm' $X/top) $(lsextattr -q user $X/top)"; }
anc >$O/anc.src
sh $H/fsmeta.sh $SRC $O/before
for B in restic rustic; do
    rt_backup $H/$B-bko $O/repo-$B $SRC >$O/backup-$B.log 2>&1; s=$?
    [ $s -eq 0 ] && r "backup-$B" PASS "exit 0" || r "backup-$B" FAIL "exit $s: $(tail -n 3 $O/backup-$B.log | tr '\n' ' ')"
done
# Node-identical snapshots, the backed-up tree and the ancestors above it.
rt_nodes $RESTIC $O/repo-restic $SRC >$O/nodes-restic 2>&1
rt_nodes $RESTIC $O/repo-rustic $SRC >$O/nodes-rustic 2>&1
if rt_node_diff $O/nodes-restic $O/nodes-rustic >$O/nodes.diff; then
    r snap-identical PASS "$(wc -l <$O/nodes-restic | tr -d ' ') nodes node-identical"
else r snap-identical FAIL "$(head -n 8 $O/nodes.diff | tr '\n' ';')"; fi
ancnodes() {  # REPO: the nodes of /bkostx and /bkostx/top
    id=$($RESTIC -r $1 --no-cache --no-lock snapshots --json latest | sed -n 's/.*"tree":"\([0-9a-f]*\)".*/\1/p' | head -n 1)
    for c in bkostx top; do
        l=$(rt_tree $RESTIC $1 $id | $rt_awk -F '\t' -v n=$c '$1 == n')
        id=$(printf '%s\n' "$l" | cut -f 2)
        printf '%s\n' "$l" | $rt_awk -F '\t' 'BEGIN { OFS = "\t" } { $2 = ""; sub(/\t\t/, "\t"); print }'
    done
}
ancnodes $O/repo-restic >$O/anc-restic; ancnodes $O/repo-rustic >$O/anc-rustic
if rt_node_diff $O/anc-restic $O/anc-rustic >$O/anc.diff && [ $(wc -l <$O/anc-restic) -eq 2 ]; then
    t=$(grep '^top' $O/anc-restic)
    ok=1; for k in bko-anc acl_nfs4 file_flags; do case $t in *$k*) ;; *) ok="" ;; esac; done
    case $t in *uid=1234*|*\"uid\":1234*) ;; *) ok="" ;; esac
    [ -n "$ok" ] && r ancestors PASS "both tools store /bkostx and /bkostx/top identically, with top's owner, xattr, ACL and flags" ||
        r ancestors FAIL "same in both but missing metadata: $(echo "$t" | cut -c1-300)"
else r ancestors FAIL "$(head -n 6 $O/anc.diff | tr '\n' ';') ($(wc -l <$O/anc-restic) ancestor nodes)"; fi
# The listings are compared with $cmp_listing, mtree's report run through
# $cmp_mtree, the project IDs with $cmp_projs: for the restore over an
# existing tree, rt_nodot_diff, rt_nodot_run and projs_nodot_diff, which
# leave out the target's own ".".
cmp_listing=diff cmp_mtree="" cmp_projs=diff
projs_nodot_diff() {  # A B: diff A B, both without "."'s line ("PROJID FLAG .")
    $rt_awk '$3 != "."' $1 >$O/projs.1; $rt_awk '$3 != "."' $2 >$O/projs.2; diff $O/projs.1 $O/projs.2
}
dot() {  # DIR: rt_dot's lines, and DIR's ZFS project ID
    rt_dot $1; echo "project $(zfs project -d $1 2>&1)"
}
cmp_tree() {  # DST KEY
    rm -rf $O/$2; sh $H/fsmeta.sh $1 $O/$2 >/dev/null 2>&1
    bad=""
    (cd $1 && $cmp_mtree mtree -f $O/before/spec -X $O/before/mtree-exclude) >$O/$2.mtree 2>&1 || bad="$bad mtree"
    for f in flags times acls extattrs hardlinks births; do
        $cmp_listing $O/before/$f.txt $O/$2/$f.txt >$O/$2.$f.diff 2>&1 || bad="$bad $f"
    done
    projs $1 >$O/$2.proj; $cmp_projs $O/proj.before $O/$2.proj >$O/$2.proj.diff || bad="$bad zfs_project"
    [ -p $1/fifo ] || bad="$bad fifo"
    echo "${bad# }"
}
for B in restic rustic; do for R in restic rustic; do
    T=$X/r-$B-$R; rm -rf $T; mkdir -p $T
    if [ $R = rustic ]; then $RUSTIC --no-progress --no-cache -r $O/repo-$B restore --numeric-id --sparse by-content latest:$SRC $T >$O/restore-$B-$R.log 2>&1
    else $RESTIC -r $O/repo-$B --no-cache restore -q --sparse latest:$SRC --target $T >$O/restore-$B-$R.log 2>&1; fi
    s=$?; bad=$(cmp_tree $T $B-$R)
    [ $s -eq 0 ] && [ -z "$bad" ] && r "rt-$B-$R" PASS "exit 0, mtree/flags/times/acls/extattrs/hardlinks/births/zfs_project/fifo identical" ||
        r "rt-$B-$R" FAIL "exit $s; differs: ${bad:-none}; $(head -n 3 $O/restore-$B-$R.log | tr '\n' ' ')"
    # Again over it, its protected files changed under their flags.  Holding
    # a tree, $T isn't a fresh target: its "." must stay as it is now (A3, as
    # stock restic and rustic), given mode 0750 and the time now so that it
    # differs from the source's.  The restore replaces libc.so.7 and re-links
    # xattrs and xattrs-link in $T itself: its mtime may move forward, never
    # back; atime=off, so its atime stays.
    chflags noschg $T/libc.so.7 && echo changed >>$T/libc.so.7 && chflags schg $T/libc.so.7
    echo more >>$T/sappnd-log
    chflags noschg $T/su && chmod 755 $T/su && chflags schg $T/su
    chmod 750 $T && touch $T; dot $T >$O/dot-$B-$R.before
    if [ $R = rustic ]; then $RUSTIC --no-progress --no-cache -r $O/repo-$B restore --numeric-id --sparse by-content latest:$SRC $T >$O/over-$B-$R.log 2>&1
    else $RESTIC -r $O/repo-$B --no-cache restore -q --sparse latest:$SRC --target $T >$O/over-$B-$R.log 2>&1; fi
    s=$?; dot $T >$O/dot-$B-$R.after
    echo "== . kept as it was by the restore over an existing tree (not a fresh target, as stock): $B->$R"
    rt_dot_check $O/dot-$B-$R.before $O/dot-$B-$R.after forward-mtime >$O/dot-$B-$R.check; ds=$?
    cat $O/dot-$B-$R.check; [ $ds -eq 0 ] && echo "kept ($(wc -l <$O/dot-$B-$R.before | tr -d ' ') lines)"
    cmp_listing=rt_nodot_diff cmp_mtree=rt_nodot_run cmp_projs=projs_nodot_diff
    bad=$(cmp_tree $T over-$B-$R)
    cmp_listing=diff cmp_mtree="" cmp_projs=diff
    [ $ds -eq 0 ] || bad="${bad:+$bad; }. not kept as it was: $(grep -v 'not a check' $O/dot-$B-$R.check | tr '\n' ' ' | cut -c1-200)"
    [ $s -eq 0 ] && [ -z "$bad" ] && r "over-$B-$R" PASS "exit 0 over schg/sappnd/sunlnk files changed under their flags; all identical but \".\", which kept its own metadata (not a fresh target, as stock)" ||
        r "over-$B-$R" FAIL "exit $s; differs: ${bad:-none}; $(head -n 3 $O/over-$B-$R.log | tr '\n' ' ')"
done; done
chflags -R 0 $X 2>/dev/null; zfs destroy -r $DS
