#!/bin/sh
# SticTest1 netbsd11: the part of the fixture roundtrip.sh doesn't cover, run
# as root in a throwaway netbsd11 with restic-bko, rustic-bko and
# rt-common.sh in /root.  On a fresh FFSv2 (WAPBL, like the root) vnd image:
# real device nodes copied from /dev (console c 0,0 and wd0a b 0,0: rdev 0),
# real setuid/hard-linked binaries, owners with no name, deep and empty
# directories, file flags, and ancestors with their own metadata above the
# backed-up path.  Both tools' snapshots compared node by node; restores in
# all four directions onto fresh FFSv2 images, compared with mtree; a second
# restore over the restored files after changing them under uchg/uappnd/
# schg/sappnd; one non-root backup and restore per direction.
# Prints "CHK PASS|FAIL need: detail" lines.
PATH=/sbin:/usr/sbin:/bin:/usr/bin
export LC_ALL=C RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
RS=/root/restic-bko RU=/root/rustic-bko
. /root/rt-common.sh
pass() { echo "CHK PASS $*"; }
fail() { echo "CHK FAIL $*"; }
mkvnd() {  # N DIR
    dd if=/dev/zero of=/root/bkx-vnd$1.img bs=1048576 count=0 seek=128 2>/dev/null
    newfs -F -O 2 -s 128m /root/bkx-vnd$1.img >/dev/null && vndconfig vnd$1 /root/bkx-vnd$1.img &&
        mkdir -p "$2" && mount_ffs -o log /dev/vnd$1a "$2" || { echo "can't mount vnd$1 at $2"; exit 1; }
}
freevnd() { umount -f "$2"; vndconfig -u vnd$1; rm -f /root/bkx-vnd$1.img; rmdir "$2" 2>/dev/null; }
tool() { case $1 in restic) echo $RS ;; rustic) echo $RU ;; esac; }
restore() {  # TOOL REPO SNAPSHOT-PATH TARGET
    if [ "$1" = rustic ]; then $RU -r "$2" --no-progress --no-cache restore --numeric-id "$3" "$4"
    else $RS -r "$2" restore -q "$3" --target "$4"; fi
}
KEYS=type,uid,gid,mode,nlink,size,time,link,sha256,flags,device

# --- The fixture -----------------------------------------------------------
mkvnd 0 /root/bkx
A=/root/bkx/anc S=/root/bkx/anc/tree
mkdir -p $S/dev $S/real && cd $S || exit 1
for d in console wd0a null tty00 rwd0d ld0a; do
    [ -e /dev/$d ] || continue
    k=$(ls -ld /dev/$d | cut -c1)
    set -- $(stat -f '%Hr %Lr %Lp %u %g' /dev/$d)
    mknod dev/$d $k $1 $2 && chmod $3 dev/$d && chown $4:$5 dev/$d
done
mknod dev/zero-c c 0 0; mknod dev/zero-b b 0 0; chown 4321:4322 dev/zero-b
mkfifo fifo
pax -rw -pe /usr/bin/su /usr/bin/passwd /bin/test /bin/[ /usr/bin/wall real/
echo noname >noname; chown 4321:4322 noname; chmod 6751 noname
mkdir sticky; chmod 1777 sticky
echo l >hl1; ln hl1 hl2
mkdir empty empty-owned; chown 4321:4322 empty-owned; chmod 0700 empty-owned
p=deep i=0; while [ $i -lt 40 ]; do p=$p/d$i; i=$((i + 1)); done
mkdir -p $p; echo bottom >$p/bottom
ln -s noname symlink; ln -s /nonexistent dangling
printf 'x' >"$(printf 'name-\377\376')"
for f in f-uchg f-uappnd f-schg f-sappnd f-nodump f-arch; do echo "$f content" >$f; done
mkdir dflag; echo in >dflag/in
find . -exec touch -h -d 2001-02-03T04:05:06.123456789 {} +
touch -h -d 2009-09-09T09:09:09.900000009 symlink
chflags uchg f-uchg; chflags uappnd f-uappnd; chflags schg f-schg; chflags sappnd f-sappnd
chflags nodump f-nodump; chflags arch f-arch; chflags uchg dflag
chmod 0751 $A; chown 1234:5678 $A; chmod 0705 /root/bkx
touch -d 2002-02-02T02:02:02.222222222 $S $A; touch -d 2003-03-03T03:03:03.333333333 /root/bkx
mount -u -o log,noatime /root/bkx
mtree -c -p $S -k $KEYS >/root/bkx.spec

# --- Backups, node identity ------------------------------------------------
cd /
rt_backup $RS /root/bkx-repo-restic $S >/root/bkx-backup-restic.log 2>&1; echo "restic backup exit $?"
rt_backup $RU /root/bkx-repo-rustic $S >/root/bkx-backup-rustic.log 2>&1; echo "rustic backup exit $?"
cat /root/bkx-backup-*.log
rt_nodes $RS /root/bkx-repo-restic /root/bkx >/root/bkx.nodes.restic
rt_nodes $RS /root/bkx-repo-rustic /root/bkx >/root/bkx.nodes.rustic
echo "== nodes: restic $(wc -l </root/bkx.nodes.restic) rustic $(wc -l </root/bkx.nodes.rustic)"
rt_node_diff /root/bkx.nodes.restic /root/bkx.nodes.rustic >/root/bkx.nodediff; echo "node diff exit $?"
cat /root/bkx.nodediff
grep -v '^  (not a check)' /root/bkx.nodediff | grep '^  ' >/root/bkx.nd
dev=$(grep -E '^  anc/tree/dev/' /root/bkx.nd)
anc=$(grep -E '^  (\.|anc):' /root/bkx.nd)
rest=$(grep -vE '^  (anc/tree/dev/|\.:|anc:)' /root/bkx.nd)
[ -z "$dev" ] && pass "rdev0-format: device nodes (incl. console c0,0, wd0a b0,0, zero-c/zero-b) node-identical in both snapshots" ||
    fail "rdev0-format: $(echo "$dev" | tr '\n' ';' | cut -c1-300)"
[ -z "$anc" ] && pass "ancestors-format: ancestor dirs above the backup path node-identical in both snapshots" ||
    fail "ancestors-format: $(echo "$anc" | tr '\n' ';' | cut -c1-300)"
[ -z "$rest" ] && [ -s /root/bkx.nodes.restic ] && pass "snapshot-identity: fixture tree node-identical ($(wc -l </root/bkx.nodes.restic | tr -d ' ') nodes)" ||
    fail "snapshot-identity: $(echo "$rest" | tr '\n' ';' | cut -c1-300)"

# --- Restores, four directions, and once more over immutable files -----------
for dir in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${dir%-*} r=${dir#*-} T=/root/bkx-t
    mkvnd 1 $T
    restore $r /root/bkx-repo-$b latest $T >/root/bkx-r.log 2>&1; rc=$?
    echo "== $dir restore exit $rc"; cat /root/bkx-r.log
    RT=$T/root/bkx/anc/tree
    out=$(mtree -p $RT -f /root/bkx.spec 2>&1); mrc=$?
    echo "$out"
    [ $rc = 0 ] && [ $mrc = 0 ] && [ -z "$out" ] && pass "restore-$dir: devices (rdev 0), set-id, no-name owners, flags, hard links, deep/empty dirs, mtree $KEYS identical" ||
        fail "restore-$dir: exit $rc, mtree $mrc: $(echo "$out" | tr '\n' ';' | cut -c1-300)"
    sa=$(stat -f '%Lp %u %g %Fm' $A /root/bkx | tr '\n' ' ')
    ta=$(stat -f '%Lp %u %g %Fm' $T/root/bkx/anc $T/root/bkx | tr '\n' ' ')
    [ "$sa" = "$ta" ] && pass "ancestors-$dir: anc and bkx restored with their mode, owner, mtime ($sa)" ||
        fail "ancestors-$dir: source '$sa' restored '$ta'"
    # Over the restored files: changed under their flags, flags back on.
    cd $RT && chflags 0 f-uchg f-uappnd f-schg f-sappnd dflag &&
        for f in f-uchg f-uappnd f-schg f-sappnd dflag/in; do echo changed >>$f; done &&
        chflags uchg f-uchg dflag && chflags uappnd f-uappnd && chflags schg f-schg && chflags sappnd f-sappnd
    cd /
    restore $r /root/bkx-repo-$b latest $T >/root/bkx-r.log 2>&1; rc=$?
    echo "== $dir restore over exit $rc"; cat /root/bkx-r.log
    out=$(mtree -p $RT -f /root/bkx.spec 2>&1); mrc=$?
    echo "$out"
    [ $rc = 0 ] && [ $mrc = 0 ] && [ -z "$out" ] && pass "over-immutable-$dir: restore over changed uchg/uappnd/schg/sappnd files and a uchg dir gives the source back" ||
        fail "over-immutable-$dir: exit $rc, mtree $mrc: $(echo "$out" | tr '\n' ';' | cut -c1-300)"
    freevnd 1 $T
done

# --- Non-root ----------------------------------------------------------------
useradd -m -u 4400 -s /bin/sh bkonr || echo "no useradd"
mkdir -p /tmp/bkxbin && cp $RS $RU /tmp/bkxbin/ && chmod 755 /tmp/bkxbin /tmp/bkxbin/*
H=/home/bkonr
cat >/tmp/bkx-nr.sh <<'EOF'
PATH=/tmp/bkxbin:/sbin:/usr/sbin:/bin:/usr/bin
export LC_ALL=C RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
H=/home/bkonr
S=$H/src
case $1 in
fixture)
    mkdir -p $S/sub/deeper $S/empty && cd $S || exit 1
    echo a >a; echo b >sub/b; ln a a-link; ln -s a sym; mkfifo fifo
    chmod 0640 a; chmod 0711 sub; chmod 2750 sub/deeper
    for f in f-uchg f-uappnd f-nodump f-arch; do echo $f >$f; done
    find . -exec touch -h -d 2005-05-05T05:05:05.555555555 {} +
    chflags uchg f-uchg; chflags uappnd f-uappnd; chflags nodump f-nodump
    chflags arch f-arch 2>/dev/null
    touch -d 2005-05-05T05:05:05.555555555 $S
    mtree -c -p $S -k type,uid,gid,mode,nlink,size,time,link,sha256,flags >$H/spec ;;
backup)
    if [ $2 = rustic ]; then rustic-bko -r $H/repo-rustic --no-progress --no-cache init >/dev/null &&
        rustic-bko -r $H/repo-rustic --no-progress --no-cache backup --set-atime yes $S >/dev/null
    else restic-bko -r $H/repo-restic init -q && restic-bko -r $H/repo-restic backup -q --with-atime $S; fi
    echo "backup $2 exit $?" ;;
restore)  # BACKUP RESTORE
    D=$H/dst-$2-$3; rm -rf $D; mkdir $D
    if [ $3 = rustic ]; then rustic-bko -r $H/repo-$2 --no-progress --no-cache restore --numeric-id latest:$S $D
    else restic-bko -r $H/repo-$2 restore -q latest:$S --target $D; fi
    echo "restore exit $?"
    m=$(mtree -p $D -f $H/spec 2>&1); echo "mtree exit $? lines $(printf '%s' "$m" | grep -c .)"
    [ -n "$m" ] && echo "$m"; true ;;
esac
EOF
chmod 755 /tmp/bkx-nr.sh
su bkonr -c "sh /tmp/bkx-nr.sh fixture" && su bkonr -c "sh /tmp/bkx-nr.sh backup restic" && su bkonr -c "sh /tmp/bkx-nr.sh backup rustic"
for dir in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${dir%-*} r=${dir#*-}
    out=$(su bkonr -c "sh /tmp/bkx-nr.sh restore $b $r" 2>&1)
    echo "== nonroot $dir"; echo "$out"
    case $out in
        *"restore exit 0"*"mtree exit 0 lines 0"*)
            pass "nonroot-$dir: as uid 4400: modes, setgid dir, times, uchg/uappnd/nodump/arch, links, fifo identical (mtree)" ;;
        *) fail "nonroot-$dir: $(echo "$out" | tr '\n' ';' | cut -c1-300)" ;;
    esac
done
# Cleanup inside the throwaway (it is discarded anyway).
freevnd 0 /root/bkx
echo "probe done"
