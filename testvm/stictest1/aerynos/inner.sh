#!/bin/bash
# SticTest1 aerynos (adapted from void-musl's): the part that runs in
# the throwaway guest, as root.  Disks: vdb = source f2fs, vdc = work f2fs
# (repositories, loop images of the restore targets).  Every f2fs is made by
# the guest's own mkfs.f2fs with the root's superblock features (the root was
# made with mkfs.f2fs's defaults: features 0, so no extra_attr, project_quota,
# inode_crtime, casefold or compression) and mounted with the root's default
# options plus noatime.  Prints "RESULT need|STATUS|detail" lines for check.sh.
set -u
C=/root/c; B=$C/b
export PATH=$PATH:$C/g/bin
export RESTIC_PASSWORD=bkocheck RUSTIC_PASSWORD=bkocheck
RESTIC=$B/restic-bko; RUSTIC=$B/rustic-bko
R() { echo "RESULT $1|$2|$3"; }
TS_M="2025-01-02 03:04:05.123456789"; TS_A="2025-06-07 08:09:10.987654321"

echo "== tools"
$RESTIC version; rv=$?; $RUSTIC --version; uv=$?
interp=$(head -c 4096 $RUSTIC | tr -c '[:print:]' '\n' | grep -m1 '^/lib.*ld-' || true)
if [ $rv = 0 ] && [ $uv = 0 ]; then
    R "Tool runs natively on AerynOS" PASS "restic-bko and rustic-bko exit 0 on $(ldd --version 2>&1 | head -1); rustic interpreter: ${interp:-none (static)}"
else
    R "Tool runs natively on AerynOS" FAIL "restic version exit $rv, rustic --version exit $uv; rustic interpreter ${interp:-none}"
fi
for t in mkfs.f2fs dump.f2fs chattr lsattr setpriv getfattr setfattr setfacl getfacl setcap getcap python3 e2fsck; do printf '%s=%s ' $t "$(command -v $t || echo MISSING)"; done; echo
stat --version | head -1

echo "== file systems"
feat() { dump.f2fs "$1" 2>/dev/null | grep -m1 'superblock features' | sed 's/.*features = //'; }
rootdev=$(findmnt -no SOURCE /); rootfeat=$(feat "$rootdev")
rootopts=$(findmnt -no OPTIONS / | tr ',' '\n' | grep -v '^relatime$\|^noatime$\|^rw$' | sort | tr '\n' ,)
echo "root $rootdev features: $rootfeat; options: $rootopts"
# The root's features by name, for mkfs.f2fs -O (none on this guest).
fopts=$(echo "$rootfeat" | sed 's/^[0-9a-fx]* *: *//' | tr -s ' ' ',' | sed 's/^,//; s/,$//')
mkf2fs() { mkfs.f2fs -q -f ${fopts:+-O $fopts} -l "$2" "$1" >/dev/null; }
P=/mnt/bkosrc2; S=$P/s; W=/w
mkdir -p $P $W
mkf2fs /dev/vdb bkosrc && mkf2fs /dev/vdc bkowork || { R "Scratch file systems (f2fs like the root)" FAIL "mkfs.f2fs failed"; exit 1; }
mkdir -p $S; mount -o noatime /dev/vdb $S && mount -o noatime /dev/vdc $W ||
    { R "Scratch file systems (f2fs like the root)" FAIL "mount failed"; exit 1; }
srcfeat=$(feat /dev/vdb)
srcopts=$(findmnt -no OPTIONS $S | tr ',' '\n' | grep -v '^relatime$\|^noatime$\|^rw$' | sort | tr '\n' ,)
if [ "$srcfeat" = "$rootfeat" ] && [ "$srcopts" = "$rootopts" ]; then
    R "Scratch file systems (f2fs like the root)" PASS "superblock features '$rootfeat' as the root's; mount options as the root's (+noatime): $srcopts"
else
    R "Scratch file systems (f2fs like the root)" FAIL "root features '$rootfeat' opts $rootopts; scratch '$srcfeat' opts $srcopts"
fi

echo "== fixture"
cd $S
# Real examples from the guest: a file capability (if any), a hard-link group, setuid.
mkdir caps
realcap=$(getcap -r /usr 2>/dev/null | head -1 | cut -d' ' -f1)
if [ -n "$realcap" ]; then
    cp "$realcap" caps/; capv=$(getfattr -n security.capability -e hex "$realcap" 2>/dev/null | sed -n 's/^security.capability=//p')
    setfattr -n security.capability -v "$capv" "caps/$(basename "$realcap")"; echo "cap from guest: $realcap $capv"
else echo "cap from guest: none (getcap -r /usr finds no file capability)"; fi
cp /usr/bin/true caps/synth-cap-ep; setfattr -n security.capability -v 0x0100000200200000000000000000000000000000 caps/synth-cap-ep
cp /usr/bin/true caps/setcap-v2; setcap cap_net_raw,cap_net_admin+ep caps/setcap-v2 2>/dev/null || echo "setcap v2 refused"
cp /usr/bin/true caps/setcap-v3; setcap -n 1234 cap_net_raw+ep caps/setcap-v3 2>/dev/null || echo "setcap v3 refused"
mkdir hl
hlf=$(find /usr/bin /usr/sbin /usr/libexec -xdev -type f -links +1 2>/dev/null | head -1); ino=$(stat -c %i "$hlf")
first=""; for f in $(find $(dirname "$hlf") -xdev -inum $ino); do
    if [ -z "$first" ]; then cp -p "$f" hl/; first=hl/$(basename "$f"); else ln "$first" "hl/$(basename "$f")"; fi; done
echo "hard-link group from guest: $hlf ($(ls hl | wc -l) names: $(ls hl | head -8 | tr '\n' ' ')...)"
mkdir hl/sub; echo syn > hl/syn1; ln hl/syn1 hl/sub/syn2; ln hl/syn1 hl/syn3
mkdir own; su1=$(find /usr/bin -xdev -perm -4000 -type f | head -1); cp -p "$su1" own/; echo "setuid from guest: $su1"
echo a > own/nouser; chown 4321:4322 own/nouser
echo b > own/bigid; chown 70000:70001 own/bigid
echo c > own/setuid-nouser; chown 4321:4322 own/setuid-nouser; chmod 4755 own/setuid-nouser
echo d > own/setgid; chmod 2751 own/setgid
mkdir own/sticky; chmod 1777 own/sticky
mkdir own/setgid-dir; chown 4321:4322 own/setgid-dir; chmod 2775 own/setgid-dir
echo e > own/plain; chmod 0600 own/plain
echo f > own/maxid; chown 4294967294:4294967294 own/maxid 2>/dev/null || echo "chown 4294967294 refused"
# xattrs: user (empty, binary, large), trusted, security, on files, dirs, symlinks.
# f2fs keeps an inode's xattrs in its inline area plus one 4 KiB block.
mkdir xattr; cd xattr
echo x > f; setfattr -n user.bko -v hello f; setfattr -n user.empty f; setfattr -n user.bin -v 0x00ff10fe7f f
setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" f; setfattr -n trusted.bko -v 0x00ff01 f
setfattr -n security.bko -v sec f 2>/dev/null || echo "security.bko not settable"
echo m > many; for i in $(seq 1 40); do setfattr -n "user.many$i" -v "v$i" many; done
echo h > huge; setfattr -n user.huge -v "$(head -c 60000 /dev/zero | tr '\0' y)" huge 2>/dev/null && echo "user.huge 60000 set" || echo "LIMITXATTR f2fs refuses a 60000-byte xattr (E2BIG)"
mkdir d; setfattr -n user.dir -v dirval d; setfattr -n trusted.dir -v t d
ln -s f sym; setfattr -h -n trusted.sym -v symval sym; setfattr -h -n security.sym -v s sym 2>/dev/null || true
cd $S
# POSIX ACLs, access and default, and a file that inherited a default ACL.
mkdir acl; echo x > acl/f; setfacl -m u:4321:rw,g:4322:r,m::rw acl/f
mkdir acl/d; setfacl -m u:4321:rx acl/d; setfacl -d -m u:4321:rwx,g:4322:rx acl/d; echo y > acl/d/inherited; mkdir acl/d/subdir
# Linux inode flags f2fs takes (F2FS_SETTABLE_FS_FL less what needs features
# the root lacks: c/m need compression, F casefold, project IDs project_quota).
mkdir flags; cd flags
echo i > immut; echo a > append; echo d > nodump; echo A > noatime; echo S > sync
head -c 200000 /dev/urandom > big-immut; head -c 200000 /dev/urandom > big-append
mkdir dir-i; echo in > dir-i/child; mkdir dir-a; echo in > dir-a/child; mkdir dir-dA; mkdir dir-D; echo in > dir-D/child
mkdir dir-P; echo in > dir-P/child; mkdir dir-S; echo in > dir-S/child
echo c > comp; chattr +c comp 2>/dev/null && echo "compression flag set" || echo "LIMITCOMP f2fs without the compression feature refuses +c"
mkdir dir-F; chattr +F dir-F 2>/dev/null && echo "casefold set" || echo "LIMITCF f2fs without the casefold feature refuses +F"
echo p > projid; chattr -p 42 projid 2>/dev/null && echo "projid set" || echo "LIMITPROJ f2fs without project_quota refuses project IDs"
cd $S
# Sparse files.
mkdir sparse
truncate -s 64M sparse/holey; dd if=/dev/urandom of=sparse/holey bs=4096 count=3 seek=8000 conv=notrunc status=none
dd if=/dev/urandom of=sparse/holey bs=4096 count=1 conv=notrunc status=none
truncate -s 8M sparse/allhole
truncate -s 16M sparse/dataend; dd if=/dev/urandom of=sparse/dataend bs=4096 count=1 seek=4095 conv=notrunc status=none
head -c 1048576 /dev/zero > sparse/zeros-written
fallocate -l 4M sparse/prealloc 2>/dev/null || echo "fallocate refused"
# Device nodes (rdev 0 too), FIFOs, symlinks, odd names, empty and deep dirs.
mkdir dev; mknod dev/null c 1 3; mknod dev/loop0 b 7 0; mknod dev/rdev0-c c 0 0; mknod dev/rdev0-b b 0 0; mkfifo dev/fifo; mkfifo -m 600 dev/fifo600
mknod dev/big c 511 1048575 2>/dev/null || mknod dev/big c 259 65536
mkdir sym; ln -s ../own/plain sym/rel; ln -s /nonexistent sym/dangling; ln -s ../xattr sym/todir; ln -s "$(head -c 900 /dev/zero | tr '\0' y)" sym/long
mkdir names; echo 1 > "names/$(printf 'bad-\xff\xfe-name')"; echo 2 > "names/with space"; echo 3 > "names/$(head -c 250 /dev/zero | tr '\0' n)"; echo 4 > "names/é-utf8"
echo 5 > "names/new"$'\n'"line"
mkdir -p empty deep; d=deep; for i in $(seq 1 60); do d=$d/l$i; done; mkdir -p $d; echo bottom > $d/file
mkdir times; echo o > times/old; echo f > times/future
cd $S
# Times on everything, deepest first; then the flags that would block it.
find $S -depth -print0 | xargs -0 touch -h -m -d "$TS_M"
find $S -depth -print0 | xargs -0 touch -h -a -d "$TS_A"
touch -d '1970-01-02 00:00:00.123456789' times/old; touch -d '2100-01-01 12:00:00.987654321' times/future
chattr +i flags/immut flags/big-immut flags/dir-i; chattr +a flags/append flags/big-append flags/dir-a
chattr +d flags/nodump; chattr +A flags/noatime flags/dir-dA; chattr +S flags/sync flags/dir-S; chattr +D flags/dir-D flags/dir-dA; chattr +P flags/dir-P
echo "flags set: $(lsattr -d flags/* | tr '\n' ';')"
# The directories above the backed-up path, with metadata of their own.
chown 4321:4322 $P; chmod 0751 $P; setfattr -n user.anc -v above $P
touch -m -d "2024-03-04 05:06:07.111111111" $P; touch -a -d "2024-03-05 05:06:07.222222222" $P
echo "fixture: $(find $S | wc -l) paths"

dump() {  # DIR: one line per path below DIR
  ( cd "$1" && find . -print0 | LC_ALL=C sort -z | while IFS= read -r -d '' f; do
      t=$(stat -c %F "$f")
      printf '%q|%s|%s|' "$f" "$t" "$(stat -c '%a|%u|%g|%y|%x' "$f")"
      case $t in
        "regular file"|"regular empty file") h=dense; [ $(( $(stat -c %b "$f") * 512 )) -lt $(stat -c %s "$f") ] && h=holey
            printf 'size=%s|n=%s|sum=%s|%s|' "$(stat -c %s "$f")" "$(stat -c %h "$f")" "$(sha256sum < "$f" | cut -c1-16)" $h ;;
        "symbolic link") printf 'L=%q|' "$(readlink "$f")" ;;
        *special*) printf 'rdev=%s|' "$(stat -c %t:%T "$f")" ;;
      esac
      case $t in "regular file"|"regular empty file"|directory)
          fl=$(lsattr -d "$f" 2>/dev/null | cut -d' ' -f1)
          # N (inline data) and I (indexed dir) are f2fs's own state, not settable: kept apart.
          printf 'fl=%s|ro=%s|' "$(printf %s "$fl" | tr 'NI' '--')" "$(printf %s "$fl" | tr -cd 'NI')" ;; esac
      printf 'xa=%s|' "$(getfattr -h -d -m - -e hex "$f" 2>/dev/null | grep -v '^#' | grep . | LC_ALL=C sort | tr '\n' ';')"
      [ -L "$f" ] || printf 'acl=%s|' "$(getfacl -p -n "$f" 2>/dev/null | grep -v '^#' | grep . | tr '\n' ';')"
      echo
    done
    find . -type f -links +1 -printf '%i %p\n' | sort | awk '{g[$1]=g[$1]" "$2} END{for(i in g) print "LINKGROUP" g[i]}' | sort )
}
dump $S > $C/src.dump
echo "== backups"
$RESTIC -r $W/repo-restic init -q; $RESTIC -r $W/repo-restic backup -q --with-atime $S > $C/restic-backup.out 2>&1; rb=$?
$RUSTIC -r $W/repo-rustic --no-progress --no-cache init > /dev/null 2>&1; $RUSTIC -r $W/repo-rustic --no-progress --no-cache backup --set-atime yes $S > $C/rustic-backup.out 2>&1; ub=$?
echo "restic backup exit $rb; rustic backup exit $ub"; grep -i "warn\|error" $C/restic-backup.out $C/rustic-backup.out | head
dump $S > $C/src-after.dump
if [ $rb = 0 ] && [ $ub = 0 ] && cmp -s $C/src.dump $C/src-after.dump; then R "Backups exit 0 and leave the source unchanged" PASS "restic 0, rustic 0"
else R "Backups exit 0 and leave the source unchanged" FAIL "restic $rb, rustic $ub; source changed: $(diff $C/src.dump $C/src-after.dump | grep -c '^>')"; fi

mkt() {  # NAME: a fresh f2fs (like the root) at /t/NAME
    truncate -s 1500M $W/$1.f2fs && mkf2fs $W/$1.f2fs bko$1 && mkdir -p /t/$1 && mount -o loop,noatime $W/$1.f2fs /t/$1
}
rst() {  # DIRECTION REPO TARGET LOG
    case $1 in
      rr|ur) $RESTIC -r $2 restore latest:$S --target $3 > $4 2>&1 ;;
      uu|ru) $RUSTIC -r $2 --no-progress --no-cache restore --numeric-id latest:$S $3 > $4 2>&1 ;;
    esac
}
repo() { case $1 in rr|ru) echo $W/repo-restic ;; *) echo $W/repo-rustic ;; esac; }
echo "== restores"
for d in rr uu ru ur; do
    mkt $d || { R "Restore $d" FAIL "target file systems not made"; continue; }
    rst $d $(repo $d) /t/$d $C/$d.out; e=$?
    dump /t/$d > $C/$d.dump; nd=$(diff $C/src.dump $C/$d.dump | grep -c '^>')
    echo "restore $d exit $e, differing lines $nd"; grep -i "warn\|error\|fail" $C/$d.out | head -5
    diff $C/src.dump $C/$d.dump | head -40 > $C/$d.diff
    echo "RESTORE $d $e $nd"
done
echo "== restores over the restored trees (immutable/append-only files in place)"
for d in rr uu ru ur; do
    echo changed >> /t/$d/own/plain
    chattr -i /t/$d/flags/big-immut && echo changed > /t/$d/flags/big-immut && chattr +i /t/$d/flags/big-immut
    echo appended >> /t/$d/flags/big-append
    rst $d $(repo $d) /t/$d $C/$d-over.out; e=$?
    dump /t/$d > $C/$d-over.dump; nd=$(diff $C/src.dump $C/$d-over.dump | grep -c '^>')
    echo "restore-over $d exit $e, differing lines $nd"; grep -i "warn\|error\|fail\|not permitted" $C/$d-over.out | head -5
    diff $C/src.dump $C/$d-over.dump | head -20 | tee $C/$d-over.diff
    echo "OVER $d $e $nd"
done
echo "== non-root (uid 4321, no name)"
asuser() { setpriv --reuid=4321 --regid=4322 --clear-groups env HOME=$W/nr "$@"; }
N=$W/nr; mkdir -p $N/src; cd $N/src
echo hi > f; chmod 640 f; setfattr -n user.nr -v val f; mkdir d; chmod 2750 d; echo x > d/g; ln -s f link; truncate -s 2M holes
mkfifo fifo; ln d/g d/g2
find . -depth -print0 | xargs -0 touch -h -m -d "$TS_M"; find . -depth -print0 | xargs -0 touch -h -a -d "$TS_A"
mkdir $N/dst-r $N/dst-u $N/bin; cp $RESTIC $RUSTIC $N/bin/; chown -R -h 4321:4322 $N; cd /
RESTIC=$N/bin/restic-bko RUSTIC=$N/bin/rustic-bko  # /root is 0700: uid 4321 runs its own copies
dump $N/src > $C/nr-src.dump
asuser $RESTIC -r $N/repo-r init -q; asuser $RESTIC -r $N/repo-r --no-cache backup -q --with-atime $N/src; a=$?
asuser $RESTIC -r $N/repo-r --no-cache restore latest:$N/src --target $N/dst-r > $C/nr-r.out 2>&1; b=$?
asuser $RUSTIC -r $N/repo-u --no-progress --no-cache init >/dev/null 2>&1; asuser $RUSTIC -r $N/repo-u --no-progress --no-cache backup --set-atime yes $N/src > $C/nr-u-backup.out 2>&1; c=$?
asuser $RUSTIC -r $N/repo-u --no-progress --no-cache restore --numeric-id latest:$N/src $N/dst-u > $C/nr-u.out 2>&1; e=$?
dump $N/dst-r > $C/nr-r.dump; dump $N/dst-u > $C/nr-u.dump
nro() { sed 's/|ro=[NI]*|/|ro=|/' "$1"; }  # N/I are f2fs's own state (judge.py keeps them apart too)
x=$(diff <(nro $C/nr-src.dump) <(nro $C/nr-r.dump) | grep -c '^>'); y=$(diff <(nro $C/nr-src.dump) <(nro $C/nr-u.dump) | grep -c '^>')
xr=$(diff $C/nr-src.dump $C/nr-r.dump | grep -c '^>'); yr=$(diff $C/nr-src.dump $C/nr-u.dump | grep -c '^>')
diff $C/nr-src.dump $C/nr-r.dump | head; diff $C/nr-src.dump $C/nr-u.dump | head; cat $C/nr-r.out $C/nr-u.out | grep -i "warn\|error" | head
if [ $a$b$c$e = 0000 ] && [ $x = 0 ] && [ $y = 0 ]; then R "Non-root backup and restore (uid 4321, no name)" PASS "both tools exit 0, restored trees identical (user xattr, setgid dir, hard link, holes, fifo) but for f2fs inline data N ($xr/$yr lines)"
else R "Non-root backup and restore (uid 4321, no name)" FAIL "restic backup $a restore $b ($x lines differ beyond N/I, $xr raw); rustic backup $c restore $e ($y lines differ beyond N/I, $yr raw)"; fi
for d in rr uu ru ur; do umount /t/$d 2>/dev/null; done
sync
echo INNER-DONE
