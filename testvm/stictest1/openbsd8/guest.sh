#!/bin/sh
# SticTest1 openbsd8, guest side (as root, in a throwaway overlay).  /root
# holds restic-bko, rustic-bko, rt-common.sh.  Prints "PASS need: ...",
# "FAIL need: ..." or "LIMIT need: ..." lines, details above them.
cd /root || exit 1
. /root/rt-common.sh
R=/root/restic-bko U=/root/rustic-bko
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
res() { echo "$1 $2: $3"; }
F=/mnt/bkof T=/mnt/rst
echo "## env"; uname -a; $R version; $U --version; sysctl kern.securelevel
mount -u -o noatime / || { res FAIL "harness" "can't remount / noatime"; exit 1; }

# Scratch FFS2 file systems on vnd (the guest's root is FFS2).
mkfs() {  # N DIR MB
    umount $2 2>/dev/null; vnconfig -u vnd$1 2>/dev/null
    rm -f /root/fs$1.img; dd if=/dev/zero of=/root/fs$1.img bs=1m count=0 seek=$3 2>/dev/null
    vnconfig vnd$1 /root/fs$1.img && newfs -O 2 vnd$1c >/dev/null 2>&1 && mkdir -p $2 &&
        mount -o noatime /dev/vnd$1c $2
}
mkfs 0 $F 2048 || { res FAIL harness "no scratch FFS2 on vnd0"; exit 1; }
mount | grep vnd0

cat >/tmp/lut.c <<'C'
#include <fcntl.h>
#include <stdlib.h>
#include <sys/stat.h>
int main(int c, char **v) { struct timespec t[2];
  t[0].tv_sec = atoll(v[2]); t[0].tv_nsec = atol(v[3]); t[1].tv_sec = atoll(v[4]); t[1].tv_nsec = atol(v[5]);
  return utimensat(AT_FDCWD, v[1], t, AT_SYMLINK_NOFOLLOW) != 0; }
C
cc -o /tmp/lut /tmp/lut.c || exit 1
cat >/tmp/sock.c <<'C'
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <string.h>
int main(int c, char **v) { struct sockaddr_un a; int s; memset(&a, 0, sizeof a); a.sun_family = AF_UNIX;
  strlcpy(a.sun_path, v[1], sizeof a.sun_path); s = socket(AF_UNIX, SOCK_STREAM, 0); return bind(s, (struct sockaddr *)&a, sizeof a) != 0; }
C
cc -o /tmp/sock /tmp/sock.c || exit 1

# ---- the fixture
A=$F/top/anc1/anc2 D=$A/data
mkdir -p $D && cd $D || exit 1
touch no-such-user; chown 1234:5678 no-such-user
touch setuid; chown 1234:5678 setuid; chmod 4755 setuid
touch setgid; chgrp 5678 setgid; chmod 2755 setgid
mkdir sticky-dir; chmod 1777 sticky-dir; mkdir empty-dir
echo linked >hardlink-a; ln hardlink-a hardlink-b; mkdir sub; ln hardlink-a sub/hardlink-c
ln -s hardlink-a symlink; ln -s /nonexistent/target symlink-dangling
ln -s hardlink-a symlink-0700; chmod -h 0700 symlink-0700
mkfifo fifo; chmod 0640 fifo
# Device nodes: rdev 0 (as /dev/console c 0,0 and /dev/wd0a b 0,0), copies
# of real ones, a large rdev.
mknod dev-c00 c 0 0; mknod dev-b00 b 0 0
for n in null zero wd0a console ttyC0 sd0c; do
    [ -e /dev/$n ] && mknod dev-real-$n $(stat -f '%HT %Hr %Lr' /dev/$n | sed 's/^Character.*Device/c/;s/^Block.*Device/b/')
done
mknod dev-c-big c 200 65535; chown 1234:5678 dev-c-big; chmod 0600 dev-c-big
dd if=/dev/zero of=sparse bs=1m count=0 seek=1024 2>/dev/null
echo start | dd of=sparse conv=notrunc 2>/dev/null; echo mid | dd of=sparse bs=1m seek=512 conv=notrunc 2>/dev/null
# A real holey file of the guest's, its holes kept (cp -r has no sparse
# mode here: dd conv=sparse writes no zero blocks).
dd if=/usr/mdec/vmboot of=real-sparse-vmboot conv=sparse bs=16k 2>/dev/null
touch "name with spaces" "$(printf '\377\376')-not-utf8"
p=deep; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do p=$p/d$i; done; mkdir -p $p; echo deep >$p/leaf
touch -d 2001-02-03T04:05:06.123456789 nanoseconds; touch -d 2100-01-01T12:00:00 future
echo atime >atime-ns; touch -d 2004-04-04T04:04:04.444444444 atime-ns; touch -a -d 2003-03-03T03:03:03.333333333 atime-ns
/tmp/lut symlink-dangling 1046660583 333333333 1081051444 444444444
/tmp/lut fifo 1046660583 1 1081051444 2
# File flags (user ones apart, for the restore over them).
mkdir flags-user flags-sys
for f in uchg uappnd nodump arch; do echo $f >flags-user/flag-$f; chflags $f flags-user/flag-$f; done
echo both >flags-user/uchg-uappnd; chflags uchg,uappnd,nodump flags-user/uchg-uappnd
mkdir flags-user/uchg-dir; echo inside >flags-user/uchg-dir/inside; chflags uchg flags-user/uchg-dir
ln -s flag-uchg flags-user/uchg-symlink; chflags -h uchg flags-user/uchg-symlink
for f in schg sappnd; do echo $f >flags-sys/flag-$f; chflags $f flags-sys/flag-$f; done
echo filepath-only >$F/top/anc1/fileonly; chown 4321:99 $F/top/anc1/fileonly
# Ancestors with their own metadata.
chown 1234:5678 $F/top; chmod 0750 $F/top; chown 0:5678 $F/top/anc1; chmod 0711 $F/top/anc1
chown 99:99 $A; chmod 2755 $A
for d in sub empty-dir sticky-dir; do touch -d 2002-02-02T02:02:02.2 $d; done
touch -d 2005-05-05T05:05:05.5 $A; touch -d 2006-06-06T06:06:06.6 $F/top/anc1; touch -d 2007-07-07T07:07:07.7 $F/top
/tmp/sock $D/socket && [ -S $D/socket ] || res FAIL harness "no socket"
touch -d 2001-02-03T04:05:06.000000006 $D; touch -d 2008-08-08T08:08:08.8 $F
cd /root

# Backed-up paths: the fixture dir, a lone file path (ancestors), the real
# /dev (rdev 0 console and wd0a), a real holey file.
P="$D $F/top/anc1/fileonly /dev /usr/mdec/vmboot"
ANC="/mnt $F $F/top $F/top/anc1 $A"
# The live /dev's device nodes go without their atime: the system moves it at
# will (/dev/urandom's, between this listing and the backups).  Everything
# else of theirs is compared, and the fixture's own device nodes (rdev 0,
# copies of real ones, a big rdev) keep the atime too.
lst() {  # PREFIX: one line per entry (dir sizes and nlink apart)
    { for a in $ANC; do echo "$1$a"; done; for p in $P; do find "$1$p"; done; } | sort | while IFS= read -r f; do
        stat -f "%N|%HT|%Hr,%Lr|%Sp|%u|%g|%z|%Fm|%Fa|%f" "$f"; done | sed "s|^$1||" |
        awk -F'|' 'BEGIN{OFS="|"} $2 !~ /Device/ {$3="-"} $2 ~ /Directory/ {$7="-"} $1 ~ /^\/dev\// && $2 ~ /Device/ {$9="-"} {print}'
}
grp() { for p in $P; do find "$1$p" -type f -links +1 -exec stat -f '%i %N' {} + ; done | sed "s| $1| |" | sort -k1,1n -k2 | awk '{if($1!=l&&g!="")print g; if($1!=l)g=$2; else g=g" "$2; l=$1}END{print g}' | sort; }
sums() { (cd "$1$D" && find . -type f -exec sha256 {} + | sort); sha256 -q "$1/usr/mdec/vmboot" "$1$F/top/anc1/fileonly"; }
blk() { for f in $D/sparse $D/real-sparse-vmboot /usr/mdec/vmboot; do stat -f '%b %z' "$1$f"; done; }
lst "" | grep -v '|Socket|' >/tmp/src.lst; grp "" >/tmp/src.grp; sums "" >/tmp/src.sum; blk "" >/tmp/src.blk
echo "fixture entries: $(wc -l </tmp/src.lst)  link groups: $(wc -l </tmp/src.grp)"
grep -E 'dev-|/dev/(console|wd0a)\|' /tmp/src.lst | head -12

ok=1
for t in restic rustic; do
    B=$R; [ $t = rustic ] && B=$U
    echo "## backup with $t"; rt_backup $B /root/repo-$t $P 2>&1; s=$?; echo "exit $s"
    [ $s = 0 ] || ok=0
done
[ $ok = 1 ] && res PASS "Both tools back up the fixture" "exit 0" || res FAIL "Both tools back up the fixture" "a backup exited non-zero"

# ---- snapshot identity
ndiff() {  # PATH -> /tmp/n.diff
    rt_nodes $R /root/repo-restic "$1" >/tmp/n.restic && rt_nodes $R /root/repo-rustic "$1" >/tmp/n.rustic &&
        rt_node_diff /tmp/n.restic /tmp/n.rustic >/tmp/n.diff
}
echo "## nodes /mnt"; ndiff /mnt; s=$?; cat /tmp/n.diff | head -40
# Ancestors of the backup paths: /mnt itself (.), bkof, top, anc1, anc2.
grep -E '^  (\.|bkof|bkof/top|bkof/top/anc1|bkof/top/anc1/anc2):' /tmp/n.diff >/tmp/anc.diff
grep -E '^(\.|bkof|bkof/top|bkof/top/anc1|bkof/top/anc1/anc2)	' /tmp/n.rustic | head -5
need="Ancestor directories of a backup path stored with their real metadata, the same by both tools"
if [ -s /tmp/anc.diff ]; then res FAIL "$need" "$(wc -l </tmp/anc.diff | tr -d ' ') node keys differ, e.g. $(head -1 /tmp/anc.diff | sed 's/^ *//')"
else res PASS "$need" "5 ancestor nodes identical"; fi
grep -vE '^  (\.|bkof|bkof/top|bkof/top/anc1|bkof/top/anc1/anc2):' /tmp/n.diff | grep -v '(not a check)' | grep -v 'dev-' >/tmp/rest.diff
need="Cross-tool snapshot identity for the fixture on FFS2"
[ -s /tmp/rest.diff ] && res FAIL "$need" "$(head -2 /tmp/rest.diff | tr '\n' ' ')" || res PASS "$need" "$(wc -l </tmp/n.restic | tr -d ' ') nodes"
grep 'dev-' /tmp/n.diff >/tmp/dev.diff
echo "## nodes /dev"; ndiff /dev; cat /tmp/n.diff >>/tmp/dev.diff; head -20 /tmp/dev.diff
grep -E '^(console|wd0a)	' /tmp/n.restic /tmp/n.rustic
need="Same repository format: device nodes with rdev 0 stored the same by both tools"
[ -s /tmp/dev.diff ] && res FAIL "$need" "$(head -2 /tmp/dev.diff | tr '\n' ' ' | sed 's/  */ /g')" || res PASS "$need" "fixture c/b 0,0 and real /dev (console, wd0a) nodes identical"
# The live /dev's device nodes' times move under us between the listing above
# and the backups: /dev/urandom's atime, and on the rebuilt image (serial
# console) /dev/tty00's mtime on every console write.  Their restores are held
# to the mtime the snapshot holds (both tools' /dev nodes are identical,
# above); the fixture's own device nodes (copies of real ones, rdev 0, a big
# rdev) keep the listing's.
$rt_awk -F '\t' '{ for (i = 2; i <= NF; i++) if ($i ~ /^mtime=@/) { sub(/^mtime=@/, "", $i); print "/dev/" $1 "|" $i } }' /tmp/n.restic >/tmp/dev.mtime
$rt_awk -F'|' 'BEGIN{OFS="|"} NR == FNR { m[$1] = $2; next } $1 ~ /^\/dev\// && $2 ~ /Device/ && ($1 in m) && m[$1] != $8 { print "live /dev mtime moved: " $1 " " $8 " -> " m[$1] >"/dev/stderr"; $8 = m[$1] } { print }' \
    /tmp/dev.mtime /tmp/src.lst >/tmp/src.lst2 && mv /tmp/src.lst2 /tmp/src.lst
ndiff /usr/mdec; [ -s /tmp/n.diff ] && echo "/usr/mdec nodes differ: $(head -3 /tmp/n.diff)"
need="Sockets skipped (no node)"
cat /tmp/n.restic /tmp/n.rustic | grep -q 'type=socket' && res FAIL "$need" "socket node" || {
    rt_nodes $R /root/repo-rustic $D | grep -q 'type=socket' && res FAIL "$need" "socket node" || res PASS "$need" "none in either snapshot"; }

# ---- four directions into fresh FFS2, then once more over the restored tree
restore() {  # TOOL REPO TARGET
    if [ $1 = restic ]; then $R -r $2 restore -q --sparse latest --target $3
    else $U --no-progress --no-cache -r $2 restore --numeric-id --sparse by-content latest $3; fi
}
cmpall() {  # TAG TARGET
    lst $2 >/tmp/d.lst; grp $2 >/tmp/d.grp; sums $2 >/tmp/d.sum; blk $2 >/tmp/d.blk
    if diff /tmp/src.lst /tmp/d.lst >/tmp/dd; then res PASS "Metadata ($1): type, rdev, mode, owner, size, ns mtime/atime, file flags, ancestors" "$(wc -l </tmp/d.lst | tr -d ' ') entries identical"
    else head -30 /tmp/dd; res FAIL "Metadata ($1): type, rdev, mode, owner, size, ns mtime/atime, file flags, ancestors" "$(grep -c '^>' /tmp/dd) entries differ, e.g. $(grep '^>' /tmp/dd | head -1)"; fi
    diff -q /tmp/src.grp /tmp/d.grp >/dev/null && res PASS "Hard links ($1)" "$(wc -l </tmp/d.grp | tr -d ' ') groups" || res FAIL "Hard links ($1)" "$(diff /tmp/src.grp /tmp/d.grp | head -2 | tr '\n' ' ')"
    diff -q /tmp/src.sum /tmp/d.sum >/dev/null && res PASS "Contents ($1)" "sha256 identical" || res FAIL "Contents ($1)" "$(diff /tmp/src.sum /tmp/d.sum | head -2 | tr '\n' ' ')"
    paste /tmp/src.blk /tmp/d.blk | awk '{ if ($3 > $1 + 64) bad=1; printf "%s->%s ", $1, $3 } END { exit bad }' >/tmp/sp; s=$?
    [ $s = 0 ] && res PASS "Sparse files ($1)" "blocks src->restored $(cat /tmp/sp)" || res FAIL "Sparse files ($1)" "blocks src->restored $(cat /tmp/sp)"
    n=$(find $2 -type s | wc -l | tr -d ' '); [ $n = 0 ] && res PASS "Sockets not restored ($1)" "0" || res FAIL "Sockets not restored ($1)" "$n"
}
n=1
for b in restic rustic; do for r in restic rustic; do
    tag="$b->$r"; echo "## restore $tag"
    mkfs 1 $T 2048 || { res FAIL "Restore ($tag)" "no fresh FFS2"; continue; }
    restore $r /root/repo-$b $T 2>/tmp/err; s=$?; grep -v '^\[INFO\]' /tmp/err | head -10
    [ $s = 0 ] && res PASS "Restore ($tag) exits 0" "" || res FAIL "Restore ($tag) exits 0" "exit $s"
    cmpall "$tag" $T
    # Over the restored tree: an immutable file changed, an append-only one
    # appended to (user flags; schg/sappnd can't be cleared at securelevel 1).
    U1=$T$D/flags-user
    chflags nouchg $U1/flag-uchg && echo changed >>$U1/flag-uchg && chmod 0600 $U1/flag-uchg && chflags uchg $U1/flag-uchg
    echo more >>$U1/flag-uappnd; echo more >>$U1/uchg-uappnd 2>/dev/null
    touch $T$D/nanoseconds
    restore $r /root/repo-$b $T 2>/tmp/err; s=$?; grep -v '^\[INFO\]' /tmp/err | head -10
    lst $T >/tmp/d.lst; sums $T >/tmp/d.sum
    need="Restore over existing immutable/append-only files ($tag)"
    # schg/sappnd can't be cleared at securelevel 1 (the LIMIT below): restic
    # makes each EPERM there an error and exits 1, rustic-bko warns and exits 0
    # (S10, the one exit-status difference).  restic's exit 1 passes only if
    # every error it gave is EPERM on the fixture's schg/sappnd files.
    xs=$s
    if [ $s = 1 ] && [ $r = restic ] && grep -q '^Fatal: There were [0-9]* errors$' /tmp/err &&
        ! grep -v -e '^\[INFO\]' -e '^Summary: ' -e '^Fatal: There were [0-9]* errors$' /tmp/err |
            grep -v -E '/flags-sys/flag-s(chg|appnd)"?: .*operation not permitted$' | grep -q .; then
        s=0 xs="1 (only EPERM on schg/sappnd, S10)"
    fi
    if [ $s = 0 ] && diff /tmp/src.lst /tmp/d.lst >/tmp/dd && diff -q /tmp/src.sum /tmp/d.sum >/dev/null; then res PASS "$need" "exit $xs, tree identical"
    else head -10 /tmp/dd; res FAIL "$need" "exit $s; $(grep -c '^>' /tmp/dd) entries differ; contents $(diff -q /tmp/src.sum /tmp/d.sum >/dev/null && echo same || echo differ)"; fi
    umount $T; vnconfig -u vnd1
done; done
res LIMIT "Restore over existing schg/sappnd files" "securelevel 1: system flags can't be cleared (BACKOTTER.md)"
res LIMIT "ACLs, extended attributes, MAC labels, capabilities" "OpenBSD has none"
res LIMIT "Birth times" "OpenBSD/FFS2 keeps none (stat %B 0)"

# ---- non-root
echo "## non-root"
useradd -u 4321 -m bkonr 2>/dev/null
mkfs 2 /mnt/nr 512 || res FAIL "Non-root backup and restore" "no FFS2"
mkdir -p /mnt/nr/src /mnt/nr/dst; chown -R 4321:4321 /mnt/nr; chmod 0755 /mnt/nr
cp /root/restic-bko /root/rustic-bko /mnt/nr/; chmod 0755 /mnt/nr/*-bko
su bkonr -c 'cd /mnt/nr/src && echo a >f && ln f f2 && mkfifo p && ln -s f l && chmod 0640 f && mkdir d && touch -d 2001-01-01T01:01:01.1 d f && echo u >u && chflags uchg u && echo a >ap && chflags uappnd,nodump ap && chmod 2755 d'
NP=/mnt/nr/src
nlst() { find $1 | sort | while IFS= read -r f; do stat -f "%N|%HT|%Sp|%u|%g|%z|%Fm|%f" "$f"; done | sed "s|^$1||" | awk -F'|' 'BEGIN{OFS="|"} $2 ~ /Directory/ {$6="-"} {print}'; }
nlst $NP >/tmp/nr.src
for b in restic rustic; do
    su bkonr -c "cd /mnt/nr && RESTIC_PASSWORD=x RUSTIC_PASSWORD=x; export RESTIC_PASSWORD RUSTIC_PASSWORD; if [ $b = restic ]; then ./restic-bko -r repo-$b init -q && ./restic-bko -r repo-$b backup -q --with-atime $NP; else ./rustic-bko --no-progress --no-cache -r repo-$b init >/dev/null && ./rustic-bko --no-progress --no-cache -r repo-$b backup --set-atime yes $NP >/dev/null; fi" 2>&1 | grep -v INFO
    for r in restic rustic; do
        su bkonr -c "cd /mnt/nr && RESTIC_PASSWORD=x RUSTIC_PASSWORD=x; export RESTIC_PASSWORD RUSTIC_PASSWORD; rm -rf dst-$b-$r; mkdir dst-$b-$r; if [ $r = restic ]; then ./restic-bko -r repo-$b restore -q latest --target dst-$b-$r; else ./rustic-bko --no-progress --no-cache -r repo-$b restore --numeric-id latest dst-$b-$r; fi" 2>&1 | grep -v INFO; s=$?
        nlst /mnt/nr/dst-$b-$r$NP >/tmp/nr.dst
        need="Non-root backup and restore (uid 4321, $b->$r)"
        diff /tmp/nr.src /tmp/nr.dst >/tmp/nd && res PASS "$need" "$(wc -l </tmp/nr.dst | tr -d ' ') entries identical (owner flags uchg, uappnd, nodump kept)" || { cat /tmp/nd | head; res FAIL "$need" "$(grep -c '^>' /tmp/nd) entries differ"; }
    done
done
echo "## done"
