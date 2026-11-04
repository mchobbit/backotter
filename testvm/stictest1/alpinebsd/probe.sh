#!/bin/sh
# SticTest1 alpinebsd: what roundtrip.sh doesn't cover, as root in a throwaway
# alpinebsd (NetBSD 11 kernel and libc, busybox userland) with restic-bko,
# rustic-bko and rt-common.sh in /root and NetBSD's tools in /root/nbtools.
# First the on-disk counts the inventory needs (flags, birth times, holes).
# On a fresh FFSv2ea (WAPBL, posix1eacls) vnd image, like the root: real
# device nodes from /dev (console c 0,0 and wd0a b 0,0: rdev 0), the guest's
# real set-id binaries, its hard-linked pair (newfs/mount_mfs), its setgid
# s6-rc repository (names with ':'), a real s6 fifo, owners with no name,
# deep/empty dirs, file flags, user/system extattrs (incl. on a symlink),
# POSIX.1e access+default ACLs, a sparse file, non-UTF-8 names, ancestors
# with their own metadata.  Both tools' snapshots node by node; restores in
# four directions onto fresh FFSv2ea images (mtree + extattrs + ACLs), again
# over changed uchg/uappnd/schg/sappnd files; a restore onto tmpfs (the
# guest's /run); one non-root backup and restore per direction.
# Prints "CHK PASS|FAIL|LIMIT need: detail" and "INV ..." lines.
PATH=/root/nbtools:/sbin:/usr/sbin:/bin:/usr/bin
export LC_ALL=C RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
RS=/root/restic-bko RU=/root/rustic-bko
. /root/rt-common.sh
pass() { echo "CHK PASS $*"; }
fail() { echo "CHK FAIL $*"; }
limit() { echo "CHK LIMIT $*"; }
one() { tr '\n' ';' | cut -c1-400; }

# --- Inventory counts ----------------------------------------------------------
find / -xdev -exec stat -f '%f|%z|%b|%B|%HT|%N' {} + >/tmp/inv.st 2>/dev/null
echo "INV names $(wc -l </tmp/inv.st | tr -d ' ')"
echo "INV flags-nonzero $(awk -F'|' '$1!="0"' /tmp/inv.st | wc -l | tr -d ' '): $(awk -F'|' '$1!="0" {print $1}' /tmp/inv.st | sort | uniq -c | tr '\n' ' ')"
awk -F'|' '$1!="0" {print "INV   flags", $1, $6}' /tmp/inv.st | head -10
echo "INV birthtimes nonzero $(awk -F'|' '$4>0' /tmp/inv.st | wc -l | tr -d ' ') zero $(awk -F'|' '$4<=0' /tmp/inv.st | wc -l | tr -d ' ')"
echo "INV sparse $(awk -F'|' '$5=="Regular File" && $3*512 < $2-8192' /tmp/inv.st | wc -l | tr -d ' ')"
awk -F'|' '$5=="Regular File" && $3*512 < $2-8192 {print "INV   sparse", $2, $3, $6}' /tmp/inv.st | head -5
echo "INV symlink-modes $(find / -xdev -type l -exec stat -f '%Lp' {} + | sort | uniq -c | tr '\n' ' ')"

mkvnd() {  # N DIR
    dd if=/dev/zero of=/root/bkx-vnd$1.img bs=1048576 count=0 seek=160 2>/dev/null
    newfs -F -O 2ea -s 160m /root/bkx-vnd$1.img >/dev/null && vndconfig vnd$1 /root/bkx-vnd$1.img &&
        mkdir -p "$2" && mount_ffs -o log,posix1eacls /dev/vnd$1a "$2" || { echo "can't mount vnd$1 at $2"; exit 1; }
}
freevnd() { umount -f "$2"; vndconfig -u vnd$1; rm -f /root/bkx-vnd$1.img; rmdir "$2" 2>/dev/null; }
restore() {  # TOOL REPO SNAPSHOT-PATH TARGET
    if [ "$1" = rustic ]; then $RU -r "$2" --no-progress --no-cache restore --numeric-id --sparse by-content "$3" "$4"
    else $RS -r "$2" restore -q --sparse "$3" --target "$4"; fi
}
KEYS=type,uid,gid,mode,nlink,size,time,link,sha256,flags,device
# Extended attributes, ACLs, birth times, symlink modes and block counts of the
# fixture's files that carry them, relative to DIR.
meta() {  # DIR
    ( cd "$1" || exit 1
      for f in f-ea f-acl dacl dacl/inherited symlink-ea sparse $(printf 'name-\377\376') real/usr/bin/passwd; do
          echo "## $f"
          for ns in user system; do
              for a in $(lsextattr -q -h $ns "$f" 2>/dev/null | tr ' \t' '\n\n' | sort); do echo "$ns.$a=$(getextattr -q -h $ns "$a" "$f" | od -An -c | tr -s ' ' | tr -d '\n')"; done
          done
          getfacl -h "$f" 2>/dev/null | grep -v '^#'
          [ -d "$f" ] && getfacl -h -d "$f" 2>/dev/null | grep -v '^#' | sed 's/^/default:/'
          stat -f 'birth %B.%FB mode %Lp' "$f"
      done
      stat -f 'sparse blocks %b' sparse )
}

# --- The fixture -----------------------------------------------------------
mkvnd 0 /root/bkx
A=/root/bkx/anc S=/root/bkx/anc/tree
mkdir -p $S/dev $S/real && cd $S || exit 1
for d in console wd0a null tty00 rwd0d ld0a dk2 rdk2; do
    [ -e /dev/$d ] || continue
    k=$(ls -ld /dev/$d | cut -c1)
    set -- $(stat -f '%Hr %Lr %Lp %u %g' /dev/$d)
    mknod dev/$d $k $1 $2 && chmod $3 dev/$d && chown $4:$5 dev/$d
done
mknod dev/zero-c c 0 0; mknod dev/zero-b b 0 0; chown 4321:4322 dev/zero-b
mkfifo fifo
# The guest's own: set-id binaries (busybox-suid's bbsuid), a hard-linked pair,
# s6-rc's setgid repository, an s6 fifo.
( cd / && tar -cf - sbin/ping usr/bin/bbsuid usr/bin/login usr/bin/passwd sbin/newfs sbin/mount_mfs \
    var/lib/s6-rc/repository etc/rc/run-image/shutdownd/fifo ) | ( cd real && tar -xpf - )
echo "INV real $(find real | wc -l | tr -d ' ') names, $(find real -type f -links +1 | wc -l | tr -d ' ') hard-linked, $(find real -perm -2000 | wc -l | tr -d ' ') setgid, $(find real -type p | wc -l | tr -d ' ') fifo"
echo noname >noname; chown 4321:4322 noname; chmod 6751 noname
mkdir sticky; chmod 1777 sticky
echo l >hl1; ln hl1 hl2
mkdir empty empty-owned; chown 4321:4322 empty-owned; chmod 0700 empty-owned
p=deep i=0; while [ $i -lt 40 ]; do p=$p/d$i; i=$((i + 1)); done
mkdir -p $p; echo bottom >$p/bottom
ln -s noname symlink; ln -s /nonexistent dangling; ln -s noname symlink-ea
printf 'x' >"$(printf 'name-\377\376')"
dd if=/dev/zero of=sparse bs=1 count=1 seek=8388607 2>/dev/null; printf 'mid' | dd of=sparse bs=1 seek=4194304 conv=notrunc 2>/dev/null
echo ea >f-ea; echo acl >f-acl; mkdir dacl
for f in f-uchg f-uappnd f-schg f-sappnd f-nodump f-arch f-opaque; do echo "$f content" >$f; done
mkdir dflag; echo in >dflag/in
setextattr user bko.zz-first "first" f-ea; setextattr user bko.text "hello world" f-ea
setextattr user bko.bin "$(printf 'a\001b')" f-ea; setextattr system bko.sys "sysval" f-ea
setextattr -h user bko.link "on the link" symlink-ea
setextattr user bko.odd "odd" "$(printf 'name-\377\376')"
setfacl -m u:4321:rw-,g:4322:r--,m::rw- f-acl
setfacl -m u:4321:rwx,m::rwx dacl; setfacl -d -m u::rwx,g::r-x,o::---,u:4321:r-x,m::r-x dacl
echo in >dacl/inherited
setextattr user bko.on-setuid "x" real/usr/bin/passwd
find . -exec touch -h -d 2001-02-03T04:05:06.123456789 {} +
touch -h -d 2009-09-09T09:09:09.900000009 symlink symlink-ea; chmod -h 0700 symlink-ea
chflags uchg f-uchg; chflags uappnd f-uappnd; chflags schg f-schg; chflags sappnd f-sappnd
chflags nodump f-nodump; chflags arch f-arch; chflags opaque f-opaque; chflags uchg dflag
chmod 0751 $A; chown 1234:5678 $A; chmod 0705 /root/bkx
touch -d 2002-02-02T02:02:02.222222222 $S $A; touch -d 2003-03-03T03:03:03.333333333 /root/bkx
mount -u -o log,noatime,posix1eacls /root/bkx
mtree -c -p $S -k $KEYS >/root/bkx.spec
meta $S >/root/bkx.meta
echo "== source meta"; cat /root/bkx.meta

# --- Backups, node identity ------------------------------------------------
cd /
rt_backup $RS /root/bkx-repo-restic $S >/root/bkx-backup-restic.log 2>&1; rb=$?; echo "restic backup exit $rb"
rt_backup $RU /root/bkx-repo-rustic $S >/root/bkx-backup-rustic.log 2>&1; ub=$?; echo "rustic backup exit $ub"
cat /root/bkx-backup-*.log
[ $rb = 0 ] && [ $ub = 0 ] && pass "backup-exit: both tools back up the fixture (devices, ACLs, extattrs, flags, s6-rc tree) with exit 0" ||
    fail "backup-exit: restic $rb rustic $ub: $(cat /root/bkx-backup-*.log | one)"
rt_nodes $RS /root/bkx-repo-restic /root/bkx >/root/bkx.nodes.restic
rt_nodes $RS /root/bkx-repo-rustic /root/bkx >/root/bkx.nodes.rustic
echo "== nodes: restic $(wc -l </root/bkx.nodes.restic) rustic $(wc -l </root/bkx.nodes.rustic)"
rt_node_diff /root/bkx.nodes.restic /root/bkx.nodes.rustic >/root/bkx.nodediff; echo "node diff exit $?"
cat /root/bkx.nodediff
grep -v '^  (not a check)' /root/bkx.nodediff | grep '^  ' >/root/bkx.nd
dev=$(grep -E '^  anc/tree/dev/' /root/bkx.nd)
anc=$(grep -E '^  (\.|anc):' /root/bkx.nd)
rest=$(grep -vE '^  (anc/tree/dev/|\.:|anc:)' /root/bkx.nd)
[ -z "$dev" ] && pass "rdev0-format: device nodes (console c0,0, wd0a b0,0, zero-c/zero-b, dk2/rdk2) node-identical in both snapshots" ||
    fail "rdev0-format: $(echo "$dev" | one)"
# rustic_core BACKOTTER.md: directories above the backed-up path get rustic's
# own nodes (0755, no owner or times): accepted, restic keeps their lstat.
if [ -z "$anc" ]; then pass "ancestors-format: ancestor dirs above the backup path node-identical in both snapshots"
elif ! echo "$anc" | grep -q '!= (none)$' || echo "$anc" | grep -vE '^  (\.|anc): [a-z_]+ .* != \(none\)$|^  (\.|anc): mode [0-9]+ != 493$' | grep -q .; then
    fail "ancestors-format: $(echo "$anc" | one)"
else limit "ancestors-format: rustic-bko's ancestor nodes are its own (mode 0755, no owner/times), restic's their lstat: accepted difference (rustic_core BACKOTTER.md, directories above the backed-up path)"; fi
[ -z "$rest" ] && [ -s /root/bkx.nodes.restic ] && pass "snapshot-identity: fixture tree node-identical ($(wc -l </root/bkx.nodes.restic | tr -d ' ') nodes: ACLs, extattrs, flags, birth times, real s6-rc/set-id files)" ||
    fail "snapshot-identity: $(echo "$rest" | one)"
grep -q 'sparse' /root/bkx.nodes.restic && echo "(nodes mention sparse)"

# --- Restores, four directions, and once more over immutable files -----------
for dir in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${dir%-*} r=${dir#*-} T=/root/bkx-t
    mkvnd 1 $T
    restore $r /root/bkx-repo-$b latest $T >/root/bkx-r.log 2>&1; rc=$?
    echo "== $dir restore exit $rc"; cat /root/bkx-r.log
    RT=$T/root/bkx/anc/tree
    out=$(mtree -p $RT -f /root/bkx.spec 2>&1); mrc=$?
    echo "$out"
    [ $rc = 0 ] && [ $mrc = 0 ] && [ -z "$out" ] && pass "restore-$dir: devices (rdev 0), set-id, s6-rc setgid tree, no-name owners, flags, hard links, fifos, deep/empty dirs, sparse, mtree $KEYS identical" ||
        fail "restore-$dir: exit $rc, mtree $mrc: $(echo "$out" | one)"
    meta $RT >/root/bkx.meta.r; md=$(diff /root/bkx.meta /root/bkx.meta.r)
    echo "$md"
    [ -z "$md" ] && pass "meta-$dir: user/system extattrs (incl. symlink, setuid file, non-UTF-8 name), POSIX.1e access+default ACLs, birth times, symlink mode, holes identical" ||
        fail "meta-$dir: $(echo "$md" | one)"
    sa=$(stat -f '%Lp %u %g %Fm' $A /root/bkx | tr '\n' ' ')
    ta=$(stat -f '%Lp %u %g %Fm' $T/root/bkx/anc $T/root/bkx | tr '\n' ' ')
    if [ "$sa" = "$ta" ]; then pass "ancestors-$dir: anc and bkx restored with their mode, owner, mtime ($sa)"
    elif [ $b = rustic ]; then limit "ancestors-$dir: rustic-bko's snapshot has no metadata for dirs above the backed-up path (restored '$ta'): accepted (rustic_core BACKOTTER.md)"
    else fail "ancestors-$dir: source '$sa' restored '$ta'"; fi
    cd $RT && chflags 0 f-uchg f-uappnd f-schg f-sappnd dflag &&
        for f in f-uchg f-uappnd f-schg f-sappnd dflag/in; do echo changed >>$f; done &&
        chflags uchg f-uchg dflag && chflags uappnd f-uappnd && chflags schg f-schg && chflags sappnd f-sappnd
    cd /
    restore $r /root/bkx-repo-$b latest $T >/root/bkx-r.log 2>&1; rc=$?
    echo "== $dir restore over exit $rc"; cat /root/bkx-r.log
    out=$(mtree -p $RT -f /root/bkx.spec 2>&1); mrc=$?
    echo "$out"
    [ $rc = 0 ] && [ $mrc = 0 ] && [ -z "$out" ] && pass "over-immutable-$dir: restore over changed uchg/uappnd/schg/sappnd files and a uchg dir gives the source back" ||
        fail "over-immutable-$dir: exit $rc, mtree $mrc: $(echo "$out" | one)"
    freevnd 1 $T
done

# --- Restore onto tmpfs (the guest mounts one on /run) -----------------------
for t in restic rustic; do
    T=/root/bkx-tmpfs; mkdir -p $T && mount_tmpfs -s 256m tmpfs $T
    restore $t /root/bkx-repo-$t latest $T >/root/bkx-r.log 2>&1; rc=$?
    echo "== tmpfs $t restore exit $rc"; cat /root/bkx-r.log
    RT=$T/root/bkx/anc/tree
    out=$(mtree -p $RT -f /root/bkx.spec -k type,uid,gid,mode,nlink,size,time,link,sha256,flags,device 2>&1); mrc=$?
    echo "$out"
    other=$(grep -viE 'extended attribute|extattr|acl|not supported|warning|^Summary:|^\[INFO\]|^Files: |^Dirs: |restore done|^$' /root/bkx-r.log)
    meta $RT >/root/bkx.meta.r; md=$(diff /root/bkx.meta /root/bkx.meta.r); echo "$md"
    # Only the group bits of the names with a POSIX.1e ACL (narrowed: documented)
    nar=$(echo "$out" | grep -vE '^(f-acl|dacl):?$|permissions \(0664, 0644\)|permissions \(0775, 0755\)|^$')
    warned=$(grep -ciE 'attribute|acl' /root/bkx-r.log)
    echo "tmpfs $t: other='$other' nar='$nar' warned=$warned"
    if [ $rc = 0 ] && [ -z "$nar" ] && [ -z "$other" ] && { [ -z "$md" ] || [ $warned -gt 0 ]; }; then
        pass "tmpfs-$t: restore onto tmpfs exits 0; contents, modes, owners, flags, devices, times identical; extattrs/ACLs $( [ -z "$md" ] && echo kept || echo "not set, warned ($warned lines)"); group bits of ACL'd names narrowed to group:: where ACLs can't be set (documented)"
    else
        fail "tmpfs-$t: exit $rc, mtree $mrc: $(echo "$out" | one) other: $(echo "$other" | one) warned $warned meta: $(echo "$md" | one)"
    fi
    umount -f $T; rmdir $T
done

# --- Non-root ----------------------------------------------------------------
mkdir -p /home; useradd -m -u 4400 -s /bin/sh bkonr || echo "no useradd"
mkdir -p /home/bkonr && chown 4400 /home/bkonr && chmod 755 /home/bkonr; ls -ld /home/bkonr
mkdir -p /tmp/bkxbin && cp $RS $RU /root/nbtools/* /tmp/bkxbin/ && chmod 755 /tmp/bkxbin /tmp/bkxbin/*
cat >/tmp/bkx-nr.sh <<'EOF'
PATH=/tmp/bkxbin:/sbin:/usr/sbin:/bin:/usr/bin
export LC_ALL=C RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
H=/home/bkonr
S=$H/src
case $1 in
fixture)
    mkdir -p $S/sub/deeper $S/empty && cd $S || exit 1
    echo a >a; echo b >sub/b; ln a a-link; ln -s a sym; mkfifo fifo
    chgrp $(id -g) . sub sub/deeper; chmod 0640 a; chmod 0711 sub; chmod 2750 sub/deeper
    for f in f-uchg f-uappnd f-nodump f-arch f-ea; do echo $f >$f; done
    setextattr user bko.nr "nonroot" f-ea
    find . -exec touch -h -d 2005-05-05T05:05:05.555555555 {} +
    chflags uchg f-uchg; chflags uappnd f-uappnd; chflags nodump f-nodump
    chflags arch f-arch 2>$H/arch.err
    touch -d 2005-05-05T05:05:05.555555555 $S
    mtree -c -p $S -k type,uid,gid,mode,nlink,size,time,link,sha256,flags >$H/spec
    ls -ld sub/deeper ;;
backup)
    if [ $2 = rustic ]; then rustic-bko -r $H/repo-rustic --no-progress --no-cache init >$H/init.out &&
        rustic-bko -r $H/repo-rustic --no-progress --no-cache backup --set-atime yes $S >$H/backup.out
    else restic-bko -r $H/repo-restic init -q && restic-bko -r $H/repo-restic backup -q --with-atime $S; fi
    echo "backup $2 exit $?" ;;
restore)  # BACKUP RESTORE
    D=$H/dst-$2-$3; rm -rf $D; mkdir $D
    if [ $3 = rustic ]; then rustic-bko -r $H/repo-$2 --no-progress --no-cache restore --numeric-id latest:$S $D
    else restic-bko -r $H/repo-$2 restore -q latest:$S --target $D; fi
    echo "restore exit $?"
    m=$(mtree -p $D -f $H/spec 2>&1); echo "mtree exit $? lines $(printf '%s' "$m" | grep -c .)"
    [ -n "$m" ] && echo "$m"
    echo "ea=$(getextattr -q user bko.nr $D/f-ea)" ;;
esac
EOF
chmod 755 /tmp/bkx-nr.sh
su bkonr -c "sh /tmp/bkx-nr.sh fixture" && su bkonr -c "sh /tmp/bkx-nr.sh backup restic" && su bkonr -c "sh /tmp/bkx-nr.sh backup rustic"
for dir in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${dir%-*} r=${dir#*-}
    out=$(su bkonr -c "sh /tmp/bkx-nr.sh restore $b $r" 2>&1)
    echo "== nonroot $dir"; echo "$out"
    case $out in
        *"restore exit 0"*"mtree exit 0 lines 0"*"ea=nonroot"*)
            pass "nonroot-$dir: as uid 4400: modes, setgid dir, times, uchg/uappnd/nodump, user extattr, links, fifo identical (mtree)" ;;
        *) fail "nonroot-$dir: $(echo "$out" | one)" ;;
    esac
done
chflags -R 0 /root/bkx 2>/dev/null
freevnd 0 /root/bkx
echo "probe done"
