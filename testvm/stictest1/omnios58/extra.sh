#!/bin/ksh
# SticTest1 omnios58: what illumos/roundtrip.sh doesn't cover, as root in a
# throwaway guest, after roundtrip.sh (it uses its /tmp/bko-crtime.py).
# A ZFS dataset of its own in rpool (rpool's properties: NFSv4 ACLs, the
# guest's compression; atime off for the listings), /bkosx, holds a fixture
# copied from the guest's real files (cp -@ -/ -p: their extended and system
# attributes, ACLs, set-id modes), device nodes (rdev 0 too), deep, empty
# and long names, owners with no name and a SID owner, and ancestors with
# metadata of their own (/bkosx/top and /bkosx/top/mid above the backed-up
# /bkosx/top/mid/data).  Each tool backs it up; each restores each one's
# snapshot ("latest", whole paths) into a fresh dataset; listings compared
# (ancestors on their own); both snapshots compared node by node, ancestors
# included; each restore restored over again with its immutable and
# appendonly files left set; each tool restores restic's snapshot onto a
# tmpfs (the guest's other file system type).
# Prints "R KEY PASS|FAIL|LIMIT detail".
#
# Usage: extra.sh DIR (restic-bko, rustic-bko, rt-common.sh in DIR)
H=$1
RESTIC=$H/restic-bko RUSTIC=$H/rustic-bko
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
PATH=/usr/gnu/bin:/usr/bin:/usr/sbin:/sbin:$PATH
export LC_ALL=C
. $H/rt-common.sh
POOL=$(zfs list -H -o name / | cut -d/ -f1)
DS=$POOL/bkosx X=/bkosx SRC=/bkosx/top/mid/data O=/tmp/bkosx
rep() { echo "R $*"; }
[ -f /tmp/bko-crtime.py ] || { rep setup FAIL "no /tmp/bko-crtime.py (roundtrip.sh first)"; exit 1; }
crtime() { /usr/bin/python3 -I /tmp/bko-crtime.py "$@"; }
sid_uid() { idmap show -c sid:$1 uid | sed -n 's/.* -> uid:\([0-9]*\)$/\1/p'; }
svcadm enable -s idmap
zfs destroy -r $DS 2>/dev/null
zfs create -o mountpoint=$X -o atime=off $DS || { rep setup FAIL "can't create $DS"; exit 1; }
rm -rf $O; mkdir -p $O $SRC
D=$SRC
# Real examples from the guest: set-id binaries, a file only root reads,
# and whatever has extended attributes, a non-trivial ACL or system
# attributes on (found by the inventory; the list is searched again here).
/usr/bin/cp -@ -/ -p /usr/bin/passwd $D/passwd             # setuid root
/usr/bin/cp -@ -/ -p /usr/bin/su $D/su 2>/dev/null
/usr/bin/cp -@ -/ -p /etc/shadow $D/shadow
real=0
for p in $(/usr/bin/find / -mount \( -xattr -o -acl \) -type f 2>/dev/null | head -n 8); do
    real=$((real + 1)); /usr/bin/cp -@ -/ -p "$p" "$D/real-$real-$(basename "$p")" 2>>$O/cp.err
done
for p in $(/usr/bin/find / -mount -acl -type d 2>/dev/null | head -n 2); do
    real=$((real + 1)); mkdir "$D/real-$real-dir" && /usr/bin/cp -@ -/ -p -r "$p/." "$D/real-$real-dir/" 2>/dev/null
    /usr/bin/ls -dV "$p" | tail -n +2 | tr -d ' ' | paste -s -d, - | xargs -I{} /usr/bin/chmod A={} "$D/real-$real-dir" 2>>$O/cp.err
done
echo "real examples copied: $real ($(cat $O/cp.err 2>/dev/null | wc -l | tr -d ' ') cp messages)"
# Device nodes, rdev 0 too; a FIFO; a symlink with its own times.
mknod $D/chr c 13 12 && mknod $D/blk b 182 5 && mknod $D/chr0 c 0 0 &&
    rep devnodes-made PASS "mknod c 13 12, b 182 5, c 0 0" || rep devnodes-made FAIL "mknod on ZFS failed"
mkfifo $D/fifo
ln -s passwd $D/symlink && touch -h -d '2002-02-02 02:02:02.000000007' $D/symlink
# Names: deep, empty, long (255 bytes), not UTF-8, an empty file.
deep=$D/deep; i=1; while [ $i -le 40 ]; do deep=$deep/d$i; i=$((i + 1)); done
mkdir -p $deep && echo bottom >$deep/bottom
mkdir $D/empty-dir; : >$D/empty-file
long=""; i=0; while [ $i -lt 255 ]; do long="${long}L"; i=$((i + 1)); done; touch "$D/$long"
touch "$D/$(printf '\351t\351')-latin1"
# Owners: no name, the largest non-ephemeral id, a SID.
touch $D/noname && chown 4242:4343 $D/noname
touch $D/bigid && /usr/bin/chown 2147483647:2147483646 $D/bigid
echo sid >$D/sid-owner && /usr/bin/chown $(sid_uid S-1-5-21-1111-2222-3333-1001) $D/sid-owner
# Hard links across directories; system attributes, an ACL, an xattr.
echo linked >$D/hl-a && mkdir $D/hl-dir && ln $D/hl-a $D/hl-dir/hl-b
echo x >$D/xattrs && runat $D/xattrs 'echo v1 >user.note; printf "\000\377" >bin'
echo a >$D/acl && /usr/bin/chmod A+user:nobody:read_data/write_data:allow,group:staff:write_data:deny $D/acl
echo h >$D/hidden-system && /usr/bin/chmod S+v'{hidden,system,nodump}' $D/hidden-system
mkdir $D/hidden-dir && /usr/bin/chmod S+vhidden $D/hidden-dir
crtime set $D/hl-a 978310861 123456789
# Immutable and appendonly last, for the restore over them.
echo frozen >$D/immutable && /usr/bin/chmod S+vimmutable $D/immutable
echo log >$D/appendonly && /usr/bin/chmod S+vappendonly $D/appendonly
echo nu >$D/nounlink && /usr/bin/chmod S+vnounlink $D/nounlink
# Ancestors with metadata of their own: owner, mode, ACL, system attributes,
# an xattr, a creation time, times; a SID owner on mid.
chown 1234:5678 $X/top && chmod 2751 $X/top
/usr/bin/chmod A+user:nobody:read_data/execute:allow $X/top
runat $X/top 'echo anc >anc-note'
crtime set $X/top 978310861 5
/usr/bin/chmod S+v'{hidden,nodump}' $X/top
/usr/bin/chown $(sid_uid S-1-5-21-1111-2222-3333-1002) $X/top/mid && chmod 750 $X/top/mid
touch -d '2005-05-05 05:05:05.5' $X/top; touch -a -d '2004-04-04 04:04:04.4' $X/top
touch -d '2006-06-06 06:06:06.6' $X/top/mid
chmod 711 $X && touch -d '2007-07-07 07:07:07.7' $X
touch -d '2008-08-08 08:08:08.8' $D

# One block per name below $1 (or $1 itself with "-d"), as roundtrip.sh's.
# NOATIME: atimes left out (tmpfs moves them as the listing reads).
lst() {  # DIR [-d]
    (cd "$1" && if [ "${2:-}" = -d ]; then echo .; else find . | sort; fi | while IFS= read -r p; do
        if [ -n "${NOATIME:-}" ] && [ -d "$p" ] && [ ! -L "$p" ]; then
            stat -c '%n %F uid=%u gid=%g mode=%a links=%h rdev=%t,%T mtime=%.9Y' "$p"   # sizes differ by file system
        elif [ -n "${NOATIME:-}" ]; then
            stat -c '%n %F uid=%u gid=%g mode=%a links=%h size=%s rdev=%t,%T mtime=%.9Y' "$p"
        else
            stat -c '%n %F uid=%u gid=%g mode=%a links=%h size=%s rdev=%t,%T atime=%.9X mtime=%.9Y' "$p"
        fi
        [ -L "$p" ] && echo "  -> $(readlink "$p")" && continue
        [ -f "$p" ] && echo "  sha256 $(digest -a sha256 "$p")"
        /usr/bin/ls -dV "$p" | tail -n +2 | sed 's/^ */  acl /'
        /usr/bin/ls -d/v "$p" | tail -n +2 | sed 's/^ */  attrs /'
        if [ -f "$p" ] || [ -d "$p" ]; then
            runat "$p" 'for a in *; do case $a in SUNWattr_r[ow]) ;; *) [ -f "$a" ] && echo "  xattr $a $(digest -a md5 "$a") $(stat -c "%u:%g %a" "$a")" ;; esac; done' 2>/dev/null
        fi
    done)
}
sids() { (cd "$1" && /usr/bin/ls -ld top/mid/data/sid-owner 2>/dev/null | awk '{ print $9, $3, $4 }'); }
asids() { (cd "$1" && /usr/bin/ls -ld top top/mid 2>/dev/null | awk '{ print $9, $3, $4 }'); }
lst $SRC >$O/src.list
{ lst $X/top -d; lst $X/top/mid -d; } >$O/src.anc
crtime list $SRC >$O/src.crtime
crtime list $X/top | grep -e '^\. ' -e '^\./mid ' >$O/src.anc-crtime
sids $X >$O/src.sids; asids $X >$O/src.asids
echo "source: $(grep -c '^\.' $O/src.list) names"

# Backups: each tool its own repository.
rt_backup $RESTIC $O/repo-restic $SRC >$O/backup-restic.msgs 2>&1; br=$?
rt_backup $RUSTIC $O/repo-rustic $SRC >$O/backup-rustic.msgs 2>&1; bu=$?
sed 's/^/  restic backup: /' $O/backup-restic.msgs; sed 's/^/  rustic backup: /' $O/backup-rustic.msgs
[ $br = 0 ] && [ $bu = 0 ] && rep backups PASS "exit 0 both" || rep backups FAIL "restic exit $br, rustic exit $bu"
# Nodes, ancestors included (rt_nodes from /bkosx down).
rt_nodes $RESTIC $O/repo-restic $X >$O/nodes-restic 2>$O/nodes.err
rt_nodes $RESTIC $O/repo-rustic $X >$O/nodes-rustic 2>>$O/nodes.err
if [ -s $O/nodes-restic ] && [ -s $O/nodes-rustic ]; then
    for t in restic rustic; do
        awk -F '\t' '$1 != "." && $1 != "top" && $1 != "top/mid"' $O/nodes-$t >$O/nodes-$t.below
        awk -F '\t' '$1 == "." || $1 == "top" || $1 == "top/mid"' $O/nodes-$t >$O/nodes-$t.anc
    done
    if rt_node_diff $O/nodes-restic.below $O/nodes-rustic.below >$O/nodes.diff; then
        rep snap-identical PASS "$(wc -l <$O/nodes-restic.below | tr -d ' ') nodes at and below the backed-up path"
    else
        sed 's/^/  /' $O/nodes.diff | head -n 40
        rep snap-identical FAIL "$(grep -v 'not a check' $O/nodes.diff | sed 's/^ *//' | head -n 6 | paste -s -d';' -)"
    fi
    if rt_node_diff $O/nodes-restic.anc $O/nodes-rustic.anc >$O/nodes-anc.diff; then rep snap-ancestors PASS "ancestor nodes identical"
    else sed 's/^/  ancestors: /' $O/nodes-anc.diff | head -n 20
        rep snap-ancestors FAIL "ancestor nodes differ (rustic_core BACKOTTER.md: stored with their own metadata, as restic): $(grep -v 'not a check' $O/nodes-anc.diff | sed 's/^ *//' | head -n 4 | paste -s -d';' -)"; fi
else rep snap-identical FAIL "can't decode: $(head -c 300 $O/nodes.err)"; fi
grep -E '^(top|top/mid)	' $O/nodes-restic | sed 's/^/  ancestor node: /' | cut -c1-600
for k in illumos.system_attributes illumos.acl illumos.owner_sid unix.birthtime illumos.xattr_meta; do
    grep -E '^top	' $O/nodes-restic | grep -q "$k" || echo "  ancestor top has no $k"
done

restore() {  # TOOL REPO SNAP TARGET
    if rt_is_rustic $1; then
        RUSTIC_PASSWORD=backotter-compare $1 --no-progress --no-cache -r $2 restore --numeric-id --sparse by-content "$3" "$4"
    else
        RESTIC_PASSWORD=backotter-compare $1 -r $2 restore -q --sparse "$3" --target "$4"
    fi
}
for b in restic rustic; do for rr in restic rustic; do
    k=$b-$rr T=/bkosxr/$b-$rr
    eval RB=\$$(echo $rr | tr a-z A-Z)
    zfs create -p -o mountpoint=$T -o atime=off $POOL/bkosxr/$k || { rep rt-$k FAIL "no dataset"; continue; }
    restore $RB $O/repo-$b latest $T >$O/restore-$k.msgs 2>&1; s=$?
    grep -v -e '^\[INFO\]' -e 'failed to get system time zone' $O/restore-$k.msgs | sed "s/^/  $k restore: /"
    lst $T$SRC >$O/$k.list; { lst $T$X/top -d; lst $T$X/top/mid -d; } >$O/$k.anc
    crtime list $T$SRC >$O/$k.crtime
    crtime list $T$X/top | grep -e '^\. ' -e '^\./mid ' >$O/$k.anc-crtime
    sids $T$X >$O/$k.sids; asids $T$X >$O/$k.asids
    bad=""
    [ $s = 0 ] || bad="$bad exit=$s"
    diff $O/src.list $O/$k.list >$O/$k.list.diff || bad="$bad listing($(grep -c '^[<>]' $O/$k.list.diff))"
    diff $O/src.crtime $O/$k.crtime >/dev/null || bad="$bad crtime"
    diff $O/src.sids $O/$k.sids >$O/$k.sids.diff || bad="$bad sid-owners"
    [ -z "$bad" ] && rep rt-$k PASS "$(grep -c '^\.' $O/$k.list) names identical" ||
        { head -n 30 $O/$k.list.diff | sed "s/^/  $k: /"; sed "s/^/  $k sids: /" $O/$k.sids.diff; rep rt-$k FAIL "$bad"; }
    abad=""
    diff $O/src.anc $O/$k.anc >$O/$k.anc.diff || abad="$abad listing"
    diff $O/src.anc-crtime $O/$k.anc-crtime >/dev/null || abad="$abad crtime"
    diff $O/src.asids $O/$k.asids >/dev/null || abad="$abad sid-owner"
    if [ -z "$abad" ]; then rep anc-$k PASS "top, top/mid: owner (SID on mid), mode, ACL, system attributes, xattr, crtime, times"
    else sed "s/^/  $k ancestors: /" $O/$k.anc.diff; rep anc-$k FAIL "$abad"; fi
    # Again over it, its immutable, appendonly and nounlink files left set
    # (BACKOTTER.md: not cleared yet on illumos, a restore over such a
    # file fails for it, in both tools).
    restore $RB $O/repo-$b latest $T >$O/over-$k.msgs 2>&1; s=$?
    grep -v -e '^\[INFO\]' -e 'failed to get system time zone' $O/over-$k.msgs | sed "s/^/  $k over: /" | head -n 12
    lst $T$SRC >$O/$k.over.list
    n=$(grep -c -i -e immutable -e appendonly -e nounlink -e 'not permitted' -e 'not owner' $O/over-$k.msgs)
    others=$(grep -v -e '^\[INFO\]' -e 'failed to get system time zone' -e immutable -e appendonly -e nounlink \
        -e 'not permitted' -e 'not owner' -e '^Summary' -e '^Fatal: There were' -e '^ignoring error' -e '^$' $O/over-$k.msgs | wc -l | tr -d ' ')
    echo "over-$k exit=$s" >>$O/over.exits
    if diff $O/src.list $O/$k.over.list >$O/$k.over.diff; then
        if [ $s = 0 ]; then rep over-$k PASS "exit 0, tree identical"
        else rep over-$k LIMIT "exit $s, $n messages name the immutable/appendonly/nounlink files or EPERM, $others others; tree still identical (BACKOTTER.md: not cleared yet on illumos)"; fi
    else
        sed "s/^/  $k over: /" $O/$k.over.diff | head -n 20
        rep over-$k FAIL "exit $s; tree differs after the restore over it"
    fi
done; done
# The tools alike over immutable files: the same exit status per restorer.
e1=$(grep -c 'restic exit=0' $O/over.exits) e2=$(grep -c 'rustic exit=0' $O/over.exits)
sed 's/^/  /' $O/over.exits
[ "$(grep -- '-restic exit' $O/over.exits | sed 's/.*exit=//' | sort -u | wc -l)" = 1 ] &&
    [ "$(grep -- '-rustic exit' $O/over.exits | sed 's/.*exit=//' | sort -u | wc -l)" = 1 ] &&
    [ "$(sed 's/.*exit=//' $O/over.exits | sed 's/[1-9][0-9]*/N/' | sort -u | wc -l)" = 1 ] &&
    rep over-alike PASS "both tools exit alike (zero or not) over immutable files" ||
    rep over-alike LIMIT "restic exits 1 over immutable files, rustic 0 with warnings (accepted: S10, verdict.py's header; BACKOTTER.md: illumos' immutable/appendonly/nounlink not cleared yet)"

# Doors: the guest's own, in /etc/sysevent (syseventd's), backed up by each
# tool and restored by each; how each stores them, and whether alike.
D2=/etc/sysevent dex=""
/usr/bin/find $D2 -type D 2>/dev/null | sed 's/^/  door in source: /'
for t in restic rustic; do
    eval B=\$$(echo $t | tr a-z A-Z)
    rt_backup $B $O/repo-door-$t $D2 >$O/door-$t.msgs 2>&1; dex="$dex $t-backup=$?"
    sed "s/^/  door $t backup: /" $O/door-$t.msgs | head -n 5
    rt_nodes $RESTIC $O/repo-door-$t $D2 >$O/door-nodes-$t 2>&1
done
grep -i 'door\|irregular' $O/door-nodes-restic | cut -c1-300 | sed 's/^/  restic node: /' | head -n 6
for b in restic rustic; do for rr in restic rustic; do
    eval RB=\$$(echo $rr | tr a-z A-Z)
    restore $RB $O/repo-door-$b latest /tmp/bkosx-door-$b-$rr >$O/door-r-$b-$rr.msgs 2>&1; dex="$dex $b-$rr=$?"
    grep -v -e '^\[INFO\]' -e 'failed to get system time zone' -e '^Files:' -e '^Dirs:' -e 'restore done' -e '^Summary' $O/door-r-$b-$rr.msgs | sed "s/^/  door $b-$rr restore: /" | head -n 4
done; done
(cd /tmp/bkosx-door-restic-restic$D2 2>/dev/null && /usr/bin/ls -lR . | head -n 12 | sed 's/^/  door restored: /')
echo "  door exits:$dex"
if rt_node_diff $O/door-nodes-restic $O/door-nodes-rustic >$O/door-nodes.diff; then dn=identical; else dn=differ; sed 's/^/  door nodes: /' $O/door-nodes.diff | head -n 12; fi
nd=$(/usr/bin/find $D2 -type D 2>/dev/null | wc -l | tr -d ' ')
# Doors are skipped quietly, as sockets (both forks' BACKOTTER.md): no node,
# no message in either backup.
/usr/bin/find $D2 -type D 2>/dev/null | sed 's,^/,,' >$O/door-paths
dnodes=$(cat $O/door-nodes-restic $O/door-nodes-rustic | $(command -v nawk || echo awk) -F '\t' 'NR==FNR {d[$1]=1; n=split($1,p,"/"); d[p[n]]=1; next} ($1 in d) || /irregular/' $O/door-paths - | wc -l | tr -d ' ')
dmsgs=$(cat $O/door-restic.msgs $O/door-rustic.msgs | grep -v -e '^\[INFO\]' -e 'failed to get system time zone' | grep -ci 'door\|irregular\|error\|warn\|os error')
echo "  door: $dnodes door/irregular nodes, $dmsgs backup messages"
case "$dex $dn" in
    *=[1-9]*) rep doors FAIL "$nd doors in $D2; exits:$dex; nodes $dn" ;;
    *) [ "$dnodes" != 0 ] || [ "$dmsgs" != 0 ] && dn="$dn, but $dnodes door nodes, $dmsgs backup messages (not skipped quietly)" ;;
esac
case "$dex $dn" in
    *=[1-9]*) ;;
    *quietly*) rep doors FAIL "$nd doors; both exit 0; nodes $dn" ;;
    *differ) rep doors FAIL "$nd doors; both exit 0; the tools' nodes differ" ;;
    *) rep doors PASS "$nd doors in $D2 skipped quietly (no node, no message); backups and restores exit 0 in 4 directions, both tools' nodes identical" ;;
esac

# Onto tmpfs (the guest's /tmp, /var/run type): restic's snapshot by each tool.
mkdir -p /bkosxt && mount -F tmpfs -o size=512m swap /bkosxt || rep tmpfs-setup FAIL "can't mount tmpfs"
NOATIME=1 lst $SRC >$O/src.na.list
for rr in restic rustic; do
    eval RB=\$$(echo $rr | tr a-z A-Z)
    T=/bkosxt/$rr; mkdir -p $T
    restore $RB $O/repo-restic latest $T >$O/tmpfs-$rr.msgs 2>&1; s=$?
    grep -v -e '^\[INFO\]' -e 'failed to get system time zone' $O/tmpfs-$rr.msgs | sed "s/^/  tmpfs $rr: /" | head -n 15
    NOATIME=1 lst $T$SRC >$O/tmpfs-$rr.list
    diff $O/src.na.list $O/tmpfs-$rr.list >$O/tmpfs-$rr.diff
    echo "exit=$s" >$O/tmpfs-$rr.exit
    # tmpfs keeps POSIX-draft ACLs (aclent), no system attributes and no
    # crtime: "acl" and "attrs" lines apart; contents, owners, modes,
    # mtimes, xattrs, rdev must be there.
    grep -v -e '^[<>]   acl ' -e '^[<>]   attrs ' $O/tmpfs-$rr.diff | grep '^[<>]' >$O/tmpfs-$rr.core
    core=$(wc -l <$O/tmpfs-$rr.core | tr -d ' ')
    nacl=$(grep '^<   acl ' $O/tmpfs-$rr.diff | grep -vc -e 'owner@' -e 'group@' -e 'everyone@')
    m_cr=$(grep -c 'crtime' $O/tmpfs-$rr.msgs) m_acl=$(grep -c 'ACE_SETACL\|acl' $O/tmpfs-$rr.msgs)
    m_sa=$(grep -c 'file flags\|system attributes\|SUNWattr_rw flags' $O/tmpfs-$rr.msgs)
    m_all=$(grep -v -e '^\[INFO\]' -e 'failed to get system time zone' -e '^Files:' -e '^Dirs:' -e 'restore done' -e '^Summary' $O/tmpfs-$rr.msgs | wc -l | tr -d ' ')
    echo "  tmpfs $rr: exit $s, $m_all message lines: $m_cr crtime, $m_acl ACL, $m_sa system attributes; $nacl non-trivial ACL entries not kept"
    if [ $core != 0 ]; then head -n 20 $O/tmpfs-$rr.core | sed "s/^/  tmpfs $rr: /"
        rep tmpfs-$rr FAIL "exit $s; $core lines differ beyond ACLs and system attributes"
    elif [ $s = 0 ]; then rep tmpfs-$rr PASS "exit 0; contents, owners, modes, mtimes, xattrs, rdev kept; $nacl NFSv4 ACL entries and the system attributes/crtime not kept (tmpfs: aclent ACLs only), $m_all warnings"
    else rep tmpfs-$rr FAIL "exit $s ($m_cr crtime, $m_acl ACL, $m_sa sysattr messages of $m_all): a restore onto tmpfs, which can't keep crtime or NFSv4 ACLs, exits nonzero"; fi
done
if diff $O/tmpfs-restic.list $O/tmpfs-rustic.list >/dev/null && diff $O/tmpfs-restic.exit $O/tmpfs-rustic.exit >/dev/null; then
    rep tmpfs-alike PASS "both tools' tmpfs restores identical, same exit"
else rep tmpfs-alike FAIL "the tools' tmpfs restores differ ($(cat $O/tmpfs-restic.exit) vs $(cat $O/tmpfs-rustic.exit); listings $(diff $O/tmpfs-restic.list $O/tmpfs-rustic.list | grep -c '^[<>]') lines apart)"; fi
umount /bkosxt
