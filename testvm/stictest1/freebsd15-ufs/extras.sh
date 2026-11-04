#!/bin/sh
# SticTest1 freebsd15-ufs: what the round trips (run-guest-rt.sh) don't
# cover, in one throwaway guest, with both tools: the guest's own files
# (schg base files, setuid su, nodump entropy, .sujournal's flags, the
# schg /var/empty, real files with holes) copied into a fixture beside
# torture.sh's; device nodes (rdev 0 and not); the ancestors of the
# backed-up path with metadata of their own; each tool backs it up, each
# restores each snapshot (4 directions) onto a fresh UFS (newfs -j, as the
# root, mounted acls) and once more over it with immutable/append-only
# files changed, compared again but for the target's own ".", which must
# stay as it was (rt_dot: holding a tree, the target isn't fresh, so it
# keeps its metadata, as stock); a whole-snapshot restore for the
# ancestors; the two tools' snapshots node by node.
# Prints "RESULT need|PASS/FAIL/LIMIT|detail".
# Usage: extras.sh RESTIC RUSTIC   (as root, in a throwaway guest; with
#                                   fsmeta.sh, torture.sh, rt-common.sh)
R=$1 U=$2
here=$(cd "$(dirname "$0")" && pwd)
. "$here/rt-common.sh"
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
B=/root/bkosx A=/root/bkosx/anc P=/root/bkosx/anc/b/src OUT=/tmp/bkosx
res() { echo "RESULT $1|$2|$3"; }
mkdir -p $P $OUT
mount -u -o acls /
# The guest's own files: cp -p keeps flags, owner, mode, times.
mkdir -p $P/real
for f in /lib/libc.so.7 /libexec/ld-elf.so.1 /sbin/init /usr/bin/su /usr/bin/passwd /entropy /boot/entropy \
         /var/db/services.db /usr/share/misc/termcap.db /usr/bin/yes; do
    [ -e $f ] && cp -p $f "$P/real/$(echo ${f#/} | tr / _)"
done
mkdir -p $P/real/var_empty
chown $(stat -f %u:%g /var/empty) $P/real/var_empty; chmod $(stat -f %Mp%Lp /var/empty) $P/real/var_empty
echo "real files' flags and blocks (not a check):"; ls -lso $P/real | sed 's/^/  /'
echo journal >$P/real/sujournal-like; chflags schg,sunlnk,nodump,opaque $P/real/sujournal-like
# Device nodes: rdev 0 and another; a block one if UFS takes it.
dev_ok=1
mknod $P/dev-c00 c 0 0 && mknod $P/dev-c1234 c 12 34 || dev_ok=""
mknod $P/dev-b00 b 0 0 2>/dev/null || echo "mknod b refused (not a check)"
ls -l $P/dev-* 2>/dev/null | sed 's/^/  /'
sh $here/torture.sh $P/t >/dev/null 2>&1
chflags schg $P/real/var_empty
# Ancestors with metadata of their own, set after their contents.
chown 4321:4322 $A/b; chmod 0751 $A/b; chown 0:5 $A; chmod 0705 $A; chmod 0711 $B
touch -h -d 2005-05-05T05:05:05.123456789 $A/b; touch -h -d 2006-06-06T06:06:06.987654321 $A
touch -h -d 2007-07-07T07:07:07.5 $B
touch -a -d 2004-04-04T04:04:04.25 $A/b $A $B
mount -u -o noatime /
anc_stat() {  # ROOT: the ancestors' metadata under ROOT
    for d in root root/bkosx root/bkosx/anc root/bkosx/anc/b; do
        echo "/$d $(stat -f '%Sp %u %g %Fm' $1/$d)"
    done
}
anc_stat "" >$OUT/anc.src
sh $here/fsmeta.sh $P $OUT/before
for t in $R $U; do
    n=$(basename $t); rt_backup $t /var/bkosx-repo-$n $P; echo "backup $n: exit $?"
done
# Ancestors in each snapshot (restic-bko ls reads both repositories).
for n in restic-bko rustic-bko; do
    $R -r /var/bkosx-repo-$n --no-cache --no-lock ls --json latest | grep '"struct_type":"node"' |
        grep -E '"path":"/root(/bkosx(/anc(/b)?)?)?"' |
        sed -e 's/"inode":[0-9]*,//' -e 's/"device_id":[0-9]*,//' >$OUT/anc.$n
    echo "ancestors in $n's snapshot:"; sed 's/^/  /' $OUT/anc.$n
done
if [ -s $OUT/anc.restic-bko ] && cmp -s $OUT/anc.restic-bko $OUT/anc.rustic-bko; then
    res "ancestor-nodes-identical" PASS "ancestor nodes of $P identical in both tools' snapshots (ls --json)"
else
    res "ancestor-nodes-identical" FAIL "$(diff $OUT/anc.restic-bko $OUT/anc.rustic-bko | grep '^>' | head -n 2 | cut -c1-300 | tr '\n' ' ')"
fi
# Node identity of the backed-up tree.
if rt_nodes $R /var/bkosx-repo-restic-bko $P >$OUT/nodes.r && rt_nodes $R /var/bkosx-repo-rustic-bko $P >$OUT/nodes.u &&
        rt_node_diff $OUT/nodes.r $OUT/nodes.u >$OUT/nodes.diff; then
    res "snapshot-identity" PASS "real files + devices + torture: $(wc -l <$OUT/nodes.r | tr -d ' ') nodes identical"
else
    cat $OUT/nodes.diff; res "snapshot-identity" FAIL "$(head -n 3 $OUT/nodes.diff | tr '\n' ' ' | cut -c1-300)"
fi
# The listings are compared with $cmp_listing, mtree's report run through
# $cmp_mtree: for the restore over an existing tree, rt_nodot_diff and
# rt_nodot_run, which leave out the target's own ".".
cmp_listing=diff cmp_mtree=""
compare() {  # DIR LABEL: DIR against the source; prints what differs, returns 1 if anything
    rm -rf $OUT/a; sh $here/fsmeta.sh $1 $OUT/a; c=0
    (cd $1 && $cmp_mtree mtree -f $OUT/before/spec -X $OUT/before/mtree-exclude) >$OUT/mtree.out 2>&1 || { c=1; sed 's/^/  mtree: /' $OUT/mtree.out | head -n 20; }
    for f in times flags acls extattrs hardlinks sparse births; do
        $cmp_listing $OUT/before/$f.txt $OUT/a/$f.txt >$OUT/d.out || { c=1; echo "  $f differs:"; head -n 10 $OUT/d.out | sed 's/^/    /'; }
    done
    return $c
}
mdn=0
md_new() {  # DIR: a fresh UFS like the root's (SU+J), with POSIX.1e ACLs, noatime
    md=$(mdconfig -a -t swap -s 3g) && newfs -j /dev/$md >/dev/null && mkdir -p $1 && mount -o acls,noatime /dev/$md $1
}
dirs_ok=""; over_ok=1; over_bad=""; real_ok=1; real_bad=""; anc_ok=1; anc_bad=""; dev_r=1
for b in restic-bko rustic-bko; do for r in $R $U; do
    rn=$(basename $r) D=/mnt/bkosx-$b-$rn
    md_new $D || { res "restore-$b-$rn" FAIL "no UFS md disk"; continue; }
    if [ $(basename $r) = rustic-bko ]; then rcmd() { $r --no-progress --no-cache -r /var/bkosx-repo-$b restore --numeric-id --sparse by-content "$1" "$2"; }
    else rcmd() { $r -r /var/bkosx-repo-$b restore -q --sparse "$1" --target "$2"; }; fi
    rcmd latest:$P $D/x >$OUT/msgs 2>&1; s=$?
    grep -v '^\[INFO\]' $OUT/msgs | head -n 20
    echo "== $b -> $rn: exit $s"
    if [ $s -eq 0 ] && compare $D/x; then res "restore-$b-$rn" PASS "fixture identical (flags, ACLs, extattrs, times, births, links, sparse, devices)"
    else res "restore-$b-$rn" FAIL "exit $s or differences (see log)"; real_ok=""; real_bad="$real_bad $b->$rn"; fi
    for p in dev-c00 dev-c1234; do
        [ "$(stat -f '%HT %Hr %Lr' $P/$p 2>/dev/null)" = "$(stat -f '%HT %Hr %Lr' $D/x/$p 2>/dev/null)" ] || { dev_r=""; echo "  $p: $(stat -f '%HT %Hr,%Lr' $D/x/$p 2>&1)"; }
    done
    # Over existing immutable/append-only files, changed.  Holding a tree,
    # $D/x isn't a fresh target: its "." must stay as it is now (A3, as stock
    # restic and rustic), given mode 0750 and the time now so that it
    # differs from the source's.  The restore may make the device nodes
    # dev-c00 and dev-c1234 in $D/x itself anew: its mtime may move forward,
    # never back; noatime, so its atime stays.
    X=$D/x/t/root/bko-torture
    for f in flag-schg flag-sappnd flag-uchg flag-uappnd flag-sunlnk; do
        fl=$(stat -f %Sf $X/$f); chflags 0 $X/$f && echo changed >>$X/$f && chflags $fl $X/$f
    done
    fl=$(stat -f %Sf $D/x/real/lib_libc.so.7); chflags 0 $D/x/real/lib_libc.so.7; echo x >>$D/x/real/lib_libc.so.7; chflags $fl $D/x/real/lib_libc.so.7
    chmod 750 $D/x && touch $D/x; rt_dot $D/x >$OUT/dot.before
    rcmd latest:$P $D/x >$OUT/msgs 2>&1; s=$?
    rt_dot $D/x >$OUT/dot.after
    grep -v '^\[INFO\]' $OUT/msgs | head -n 20
    echo "== over immutable/append-only, $b -> $rn: exit $s"
    echo "== . kept as it was by the restore over an existing tree (not a fresh target, as stock), $b -> $rn"
    if rt_dot_check $OUT/dot.before $OUT/dot.after forward-mtime; then echo "  kept ($(wc -l <$OUT/dot.before | tr -d ' ') lines)"
    else over_ok=""; over_bad="$over_bad $b->$rn(. not kept as it was)"; fi
    cmp_listing=rt_nodot_diff cmp_mtree=rt_nodot_run
    if [ $s -eq 0 ] && compare $D/x; then :; else over_ok=""; over_bad="$over_bad $b->$rn(exit $s)"; fi
    cmp_listing=diff cmp_mtree=""
    # The whole snapshot: the ancestors come back with their own metadata.
    rcmd latest $D/w >$OUT/msgs 2>&1; s=$?
    anc_stat $D/w | sed 1d >$OUT/anc.dst; sed 1d $OUT/anc.src >$OUT/anc.srcx
    echo "== whole snapshot $b -> $rn: exit $s"; diff $OUT/anc.srcx $OUT/anc.dst && echo "  ancestors identical"
    # /root itself: mode and owner only (its mtime moves as the test works in it).
    r0=$(sed -n 1p $OUT/anc.src | cut -d' ' -f2-4); r1=$(stat -f '%Sp %u %g' $D/w/root)
    if [ $s -eq 0 ] && cmp -s $OUT/anc.srcx $OUT/anc.dst && [ "$r0" = "$r1" ]; then :; else
        anc_ok=""; anc_bad="$anc_bad $b->$rn: $(diff $OUT/anc.srcx $OUT/anc.dst | grep '^>' | head -n 1)"; fi
    chflags -R 0 $D; umount -f $D; mdconfig -d -u ${md#md}
done; done
if [ -n "$dev_ok" ]; then
    [ -n "$dev_r" ] && res "device-nodes" PASS "c 0,0 (rdev 0) and c 12,34 on UFS, all 4 directions" || res "device-nodes" FAIL "rdev or type differs"
else res "device-nodes" LIMIT "mknod refused on this UFS"; fi
[ -n "$over_ok" ] && res "restore-over-immutable" PASS "schg/sappnd/uchg/uappnd/sunlnk + schg libc, changed, restored over in 4 directions, identical; the target's own \".\" kept as it was (not a fresh target, as stock)" ||
    res "restore-over-immutable" FAIL "$over_bad"
[ -n "$real_ok" ] && res "guest-own-files" PASS "schg libc/ld-elf/init, setuid+schg su/passwd, nodump entropy, sujournal flags, schg /var/empty, holey services.db/termcap.db/yes" ||
    res "guest-own-files" FAIL "$real_bad"
[ -n "$anc_ok" ] && res "ancestor-restore" PASS "whole-snapshot restore gives the ancestors their own mode, owner, mtime (4 directions)" ||
    res "ancestor-restore" FAIL "$anc_bad"
chflags -R 0 $B; rm -rf $B /var/bkosx-repo-* $OUT
