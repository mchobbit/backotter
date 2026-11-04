#!/bin/bash
# SticTest1 void-musl: the part that runs in the throwaway guest, as root.
# Disks: vdb = source btrfs, vdc = source ext4 (mounted at s/boot, like the
# guest's /boot), vdd = work btrfs (repositories, loop images of the restore
# targets).  Prints "RESULT need|STATUS|detail" lines for check.sh.
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
    R "Tool runs natively on Void musl" PASS "restic-bko and rustic-bko exit 0 on musl $(ls /lib/ld-musl* 2>/dev/null); rustic interpreter: ${interp:-none (static)}"
else
    R "Tool runs natively on Void musl" FAIL "restic version exit $rv, rustic --version exit $uv; rustic interpreter ${interp:-none}"
fi
for t in mkfs.btrfs mkfs.ext4 chattr lsattr setpriv chpst getfattr setfattr setfacl getfacl; do printf '%s=%s ' $t "$(command -v $t || echo MISSING)"; done; echo

echo "== file systems"
P=/mnt/bkosrc2; S=$P/s; W=/w
mkdir -p $P $W
mkfs.btrfs -q -f /dev/vdb && mkfs.btrfs -q -f /dev/vdd && mkfs.ext4 -q -F /dev/vdc || { R "Scratch file systems (btrfs + ext4)" FAIL "mkfs failed"; exit 1; }
mkdir -p $S; mount -o noatime /dev/vdb $S && mkdir $S/boot && mount -o noatime /dev/vdc $S/boot && mount -o noatime /dev/vdd $W ||
    { R "Scratch file systems (btrfs + ext4)" FAIL "mount failed"; exit 1; }
btrfs inspect-internal dump-super /dev/vdb 2>/dev/null | grep -i incompat_flags -A8 | tr -s ' \n' ' '; echo

echo "== fixture"
cd $S
# Real examples from the guest: the file capability, a hard-link group, setuid.
mkdir caps; cp /usr/bin/iputils-ping caps/
capv=$(getfattr -n security.capability -e hex /usr/bin/iputils-ping 2>/dev/null | sed -n 's/^security.capability=//p')
setfattr -n security.capability -v "$capv" caps/iputils-ping; echo "cap from guest: $capv"
setfattr -n security.capability -v 0x0100000200200000000000000000000000000000 caps/synth-cap-ep 2>/dev/null || { cp /bin/true caps/synth-cap-ep; setfattr -n security.capability -v 0x0100000200200000000000000000000000000000 caps/synth-cap-ep; }
mkdir hl
hlf=$(find /usr/bin /usr/sbin /usr/libexec -xdev -type f -links +1 2>/dev/null | head -1); ino=$(stat -c %i "$hlf")
first=""; for f in $(find $(dirname "$hlf") -xdev -inum $ino); do
    if [ -z "$first" ]; then cp -p "$f" hl/; first=hl/$(basename "$f"); else ln "$first" "hl/$(basename "$f")"; fi; done
echo "hard-link group from guest: $hlf ($(ls hl | tr '\n' ' '))"
mkdir hl/sub; echo syn > hl/syn1; ln hl/syn1 hl/sub/syn2; ln hl/syn1 hl/syn3
mkdir own; su1=$(find /usr/bin -xdev -perm -4000 -type f | head -1); cp -p "$su1" own/; echo "setuid from guest: $su1"
echo a > own/nouser; chown 4321:4322 own/nouser
echo b > own/bigid; chown 70000:70001 own/bigid
echo c > own/setuid-nouser; chown 4321:4322 own/setuid-nouser; chmod 4755 own/setuid-nouser
echo d > own/setgid; chmod 2751 own/setgid
mkdir own/sticky; chmod 1777 own/sticky
mkdir own/setgid-dir; chown 4321:4322 own/setgid-dir; chmod 2775 own/setgid-dir
echo e > own/plain; chmod 0600 own/plain
# xattrs: user (empty, binary, large), trusted, security, on files, dirs, symlinks.
mkdir xattr; cd xattr
echo x > f; setfattr -n user.bko -v hello f; setfattr -n user.empty f; setfattr -n user.bin -v 0x00ff10fe7f f
setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" f; setfattr -n trusted.bko -v 0x00ff01 f
setfattr -n security.bko -v sec f 2>/dev/null || echo "security.bko not settable"
mkdir d; setfattr -n user.dir -v dirval d; setfattr -n trusted.dir -v t d
ln -s f sym; setfattr -h -n trusted.sym -v symval sym; setfattr -h -n security.sym -v s sym 2>/dev/null || true
cd $S
# POSIX ACLs, access and default, and a file that inherited a default ACL.
mkdir acl; echo x > acl/f; setfacl -m u:4321:rw,g:4322:r,m::rw acl/f
mkdir acl/d; setfacl -m u:4321:rx acl/d; setfacl -d -m u:4321:rwx,g:4322:rx acl/d; echo y > acl/d/inherited; mkdir acl/d/subdir
# Linux inode flags on btrfs, and btrfs per-file options.
mkdir flags; cd flags
echo i > immut; echo a > append; echo d > nodump; echo A > noatime; echo S > sync
touch nocow; chattr +C nocow; head -c 300000 /dev/urandom > nocow
touch comp; chattr +c comp; head -c 200000 /dev/zero > comp
touch nocomp; chattr +m nocomp; echo n > nocomp
mkdir dir-C; chattr +C dir-C; touch dir-C/inherits
mkdir dir-c; chattr +c dir-c; echo x > dir-c/inherits
mkdir dir-i; echo in > dir-i/child; mkdir dir-a; echo in > dir-a/child; mkdir dir-dA
echo zstd > zstd; setfattr -n btrfs.compression -v zstd zstd
echo lzo > lzo-immut; setfattr -n btrfs.compression -v lzo lzo-immut
echo zlib > zlib-app; setfattr -n btrfs.compression -v zlib zlib-app
echo none > comp-none; setfattr -n btrfs.compression -v none comp-none 2>/dev/null || echo "btrfs.compression none refused"
mkdir dir-zstd; setfattr -n btrfs.compression -v zstd dir-zstd; echo in > dir-zstd/child
echo p > projid; chattr -p 42 projid 2>/dev/null && echo "projid set" || echo "LIMITPROJ btrfs has no project IDs"
cd $S
# Sparse files.
mkdir sparse
truncate -s 64M sparse/holey; dd if=/dev/urandom of=sparse/holey bs=4096 count=3 seek=8000 conv=notrunc status=none
dd if=/dev/urandom of=sparse/holey bs=4096 count=1 conv=notrunc status=none
truncate -s 8M sparse/allhole
truncate -s 16M sparse/dataend; dd if=/dev/urandom of=sparse/dataend bs=4096 count=1 seek=4095 conv=notrunc status=none
head -c 1048576 /dev/zero > sparse/zeros-written
# Device nodes (rdev 0 too), FIFOs, symlinks, odd names, empty and deep dirs.
mkdir dev; mknod dev/null c 1 3; mknod dev/loop0 b 7 0; mknod dev/rdev0-c c 0 0; mknod dev/rdev0-b b 0 0; mkfifo dev/fifo; mkfifo -m 600 dev/fifo600
mkdir sym; ln -s ../own/plain sym/rel; ln -s /nonexistent sym/dangling; ln -s ../xattr sym/todir; ln -s "$(head -c 900 /dev/zero | tr '\0' y)" sym/long
mkdir names; echo 1 > "names/$(printf 'bad-\xff\xfe-name')"; echo 2 > "names/with space"; echo 3 > "names/$(head -c 250 /dev/zero | tr '\0' n)"; echo 4 > "names/é-utf8"
mkdir -p empty deep; d=deep; for i in $(seq 1 60); do d=$d/l$i; done; mkdir -p $d; echo bottom > $d/file
# The ext4 part (as the guest's /boot): flags, xattrs, ACLs, hard links, holes.
cd $S/boot
echo i > immut; echo a > append; echo d > nodump; echo A > noatime; echo s > sync
echo x > xa; setfattr -n user.bko -v boot xa; setfattr -n trusted.bko -v 0x01 xa
echo x > acl; setfacl -m u:4321:r acl; mkdir acldir; setfacl -d -m g:4322:rx acldir
echo h > hl1; ln hl1 hl2
truncate -s 4M holey; dd if=/dev/urandom of=holey bs=4096 count=1 seek=500 conv=notrunc status=none
echo o > owned; chown 4321:4322 owned; chmod 4750 owned
mkdir grub; echo cfg > grub/grub.cfg; ln -s grub/grub.cfg cfglink
cd $S
# Times on everything, deepest first; then the flags that would block it.
find $S -depth -print0 | xargs -0 touch -h -m -d "$TS_M"
find $S -depth -print0 | xargs -0 touch -h -a -d "$TS_A"
chattr +i flags/immut flags/lzo-immut flags/dir-i boot/immut; chattr +a flags/append flags/zlib-app flags/dir-a boot/append
chattr +d flags/nodump boot/nodump; chattr +A flags/noatime boot/noatime flags/dir-dA; chattr +S flags/sync boot/sync; chattr +D flags/dir-dA 2>/dev/null
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
      case $t in "regular file"|"regular empty file"|directory) printf 'fl=%s|' "$(lsattr -d "$f" 2>/dev/null | cut -d' ' -f1)"
          printf 'bc=%s|' "$(getfattr -n btrfs.compression --only-values "$f" 2>/dev/null)" ;; esac
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

mkt() {  # NAME: a fresh btrfs at /t/NAME with a fresh ext4 at /t/NAME/boot
    truncate -s 1200M $W/$1.btrfs; truncate -s 256M $W/$1.ext4
    mkfs.btrfs -q -f $W/$1.btrfs; mkfs.ext4 -q -F $W/$1.ext4
    mkdir -p /t/$1; mount -o loop,noatime $W/$1.btrfs /t/$1 && mkdir /t/$1/boot && mount -o loop,noatime $W/$1.ext4 /t/$1/boot
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
    rst $d $(repo $d) /t/$d $C/$d-over.out; e=$?
    dump /t/$d > $C/$d-over.dump; nd=$(diff $C/src.dump $C/$d-over.dump | grep -c '^>')
    echo "restore-over $d exit $e, differing lines $nd"; grep -i "warn\|error\|fail\|not permitted" $C/$d-over.out | head -5
    diff $C/src.dump $C/$d-over.dump | head -20
    echo "OVER $d $e $nd"
done
echo "== non-root (uid 4321, no name)"
# The guest bootstrap (genfstab) dropped stock Void's fstab line 'tmpfs /tmp tmpfs defaults,nosuid,nodev',
# leaving the ROOTFS tarball's 0755 /tmp: give this throwaway overlay the stock tmpfs /tmp so a non-root user can write there.
[ "$(stat -c %a /tmp)" = 1777 ] || { mount -t tmpfs -o nosuid,nodev tmpfs /tmp && echo "non-root: mounted stock Void tmpfs on /tmp (overlay had 0755 /tmp)"; }
asuser() { if command -v setpriv >/dev/null; then setpriv --reuid=4321 --regid=4322 --clear-groups env HOME=$W/nr "$@"; else chpst -u :4321:4322 env HOME=$W/nr "$@"; fi; }
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
x=$(diff $C/nr-src.dump $C/nr-r.dump | grep -c '^>'); y=$(diff $C/nr-src.dump $C/nr-u.dump | grep -c '^>')
diff $C/nr-src.dump $C/nr-r.dump | head; diff $C/nr-src.dump $C/nr-u.dump | head; cat $C/nr-r.out $C/nr-u.out | grep -i "warn\|error" | head
if [ $a$b$c$e = 0000 ] && [ $x = 0 ] && [ $y = 0 ]; then R "Non-root backup and restore (uid 4321, no name)" PASS "both tools exit 0, restored trees identical (user xattr, setgid dir, hard link, holes, fifo)"
else R "Non-root backup and restore (uid 4321, no name)" FAIL "restic backup $a restore $b ($x lines differ); rustic backup $c restore $e ($y lines differ)"; fi
for d in rr uu ru ur; do umount /t/$d/boot /t/$d 2>/dev/null; done
sync
echo INNER-DONE
