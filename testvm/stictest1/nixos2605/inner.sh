#!/usr/bin/env bash
# SticTest1 nixos2605, the part that runs in the guest as root (/root).
# NixOS has no python3: the fixture and the comparison use bash, perl and the
# guest's own tools (find -printf, getfattr, lsattr, sha256sum).  Every file
# system is a fresh bcachefs made like the guest's root (block_size 512).
# Prints "PASS|FAIL|LIMIT need: detail" lines; details in /root/st1/.
set -u
cd /root
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
B=/var/tmp/st1bin RS=/var/tmp/st1bin/restic-bko RU=/var/tmp/st1bin/rustic-bko
W=/var/tmp/st1 M=/mnt/bkc L=/root/st1
mkdir -p $W $M $L $B
cp /root/restic-bko /root/rustic-bko $B/ && chmod 755 $B $B/*
. /root/rt-common.sh
res() { echo "$1 $2: $3"; }
LOOPS=""
mkbch() {  # NAME: a fresh bcachefs like the guest's root, mounted noatime
    truncate -s 2G $W/$1.img || return 1
    local d; d=$(losetup -f --show $W/$1.img) || return 1
    LOOPS="$LOOPS $d"
    { bcachefs format --block_size=512 --acl $d || bcachefs format -f --block_size=512 $d; } >$L/mkfs-$1.log 2>&1 &&
        mkdir -p $M/$1 && mount -t bcachefs -o noatime $d $M/$1
}
{
    echo "kernel: $(uname -r)"; bcachefs version 2>&1
    $RS version | head -1; $RU --version | head -1
    echo "nsswitch: $(grep -E '^(passwd|group)' /etc/nsswitch.conf | tr '\n' ' ')"
    bcachefs show-super $(findmnt -no SOURCE / | head -1) 2>/dev/null | grep -iE 'block_size|version|features' | head -8
} >$L/info 2>&1

# The manifest: one "PATH|KEY|VALUE" line per fact, sorted.  Hard links are
# named by the first path of their inode; sparse is "blocks*512 < size".
# Sockets are left out (both tools skip them, by decision; "Sockets skipped
# by both tools" below judges them), so no comparison counts one.
manifest() {  # DIR
    (cd "$1" || exit 1
    find . -mindepth 1 ! -type s -printf '%P\t%y\t%m\t%U:%G\t%s\t%T@\t%A@\t%l\t%i\t%n\t%b\n' | sort | perl -F'\t' -lane '
        my ($p,$y,$m,$o,$s,$t,$a,$l,$i,$n,$b) = @F;
        print "$p|type|$y"; print "$p|mode|$m"; print "$p|owner|$o";
        print "$p|mtime|$t"; print "$p|atime|$a";
        print "$p|size|$s" if $y ne "d";
        print "$p|target|$l" if $y eq "l";
        if ($y eq "f") { print "$p|sparse|" . ($b*512 < $s ? 1 : 0); }
        if ($y ne "d" && $n > 1) { $first{$i} //= $p; print "$p|link|$first{$i}"; }'
    find . -mindepth 1 \( -type c -o -type b \) -printf '%P\n' | while IFS= read -r f; do
        printf '%s|rdev|%s\n' "$f" "$(stat -c '%t:%T' "$f")"; done
    find . -mindepth 1 -type f -print0 | xargs -0 -r sha256sum | sed -E 's#^([0-9a-f]+)  \./(.*)$#\2|sha|\1#'
    find . -mindepth 1 \( -type f -o -type d \) -printf '%P\n' | while IFS= read -r f; do
        printf '%s|flags|%s\n' "$f" "$(lsattr -d "./$f" 2>/dev/null | cut -d' ' -f1)"
        printf '%s|projid|%s\n' "$f" "$(lsattr -pd "./$f" 2>/dev/null | awk '{print $1}')"; done
    find . -mindepth 1 ! -type s -printf '%P\n' | while IFS= read -r f; do
        getfattr -h -d -m - -e hex --absolute-names "./$f" 2>/dev/null | grep '=' | sed "s#^#$f|xattr:#"; done
    ) | grep -v '^lost+found' | LC_ALL=C sort
}
# Which need a differing manifest key belongs to
need_of() { awk -F'|' '{k=$2
    if (k ~ /^xattr:system\.posix_acl/) n="acl"; else if (k == "xattr:security.capability") n="cap"
    else if (k ~ /^xattr:bcachefs/) n="bchopt"; else if (k ~ /^xattr:/) n="xattr"
    else if (k == "flags") n="flags"; else if (k == "projid") n="projid"
    else if (k == "mtime" || k == "atime") n="times"; else if (k == "mode" || k == "owner") n="owner"
    else if (k == "link") n="link"; else if (k == "rdev") n="dev"; else if (k == "sparse") n="sparse"
    else n="content"; print n "\t" $0}'; }
# diff two manifests, one line per differing fact: "NEED<TAB>< or >PATH|KEY|VALUE"
mdiff() { diff "$1" "$2" | grep -E '^[<>]' | sed 's/^\([<>]\) /\1/' | awk '{print substr($0,1,1) "\t" substr($0,2)}' |
    while IFS=$'\t' read -r side line; do printf '%s\n' "$line" | need_of | sed "s/\t/\t$side/"; done; }

# --- the fixture ------------------------------------------------------------
mkbch src || { res FAIL "fixture" "can't make the source bcachefs ($(tail -2 $L/mkfs-src.log | tr '\n' ' '))"; exit 1; }
A=$M/src/anc D=$A/data
mkdir -p $D
bash /root/torture.sh $D 2>$L/torture.err >/dev/null
T=$D/root/bko-torture
rm -f $T/socket; rt_mksock $T/socket 2>>$L/torture.err
# The guest's own examples
G=$D/guest; mkdir -p $G/perl-env $G/var/log $G/var/lib/systemd $G/wrappers
P=$(ls -d /nix/store/*-perl-5.42.3-env/bin 2>/dev/null | head -1)
[ -n "$P" ] && for f in perl cpan perldoc; do [ -e "$P/$f" ] && cp --sparse=always -L "$P/$f" $G/perl-env/$f; done
cp -a /var/log/journal $G/var/log/ 2>>$L/torture.err
cp -a /var/lib/systemd/random-seed $G/var/lib/systemd/ 2>>$L/torture.err
for w in ping mount; do [ -e /run/wrappers/bin/$w ] && cp -a --preserve=all /run/wrappers/bin/$w $G/wrappers/ 2>>$L/torture.err; done
mkdir -p $G/nix-store-like; chown 0:30000 $G/nix-store-like; chmod 1775 $G/nix-store-like
echo s >$G/nix-store-like/epoch1; touch -h -d '@1' $G/nix-store-like/epoch1
mkdir $G/var-empty; chattr +i $G/var-empty
# A file with a 1536-byte hole between 512-byte blocks
printf A >$D/hole512; truncate -s 2560 $D/hole512; printf B | dd of=$D/hole512 bs=1 seek=2559 conv=notrunc status=none
truncate -s 64M $D/big-sparse; printf z | dd of=$D/big-sparse bs=1 seek=33554432 conv=notrunc status=none
head -c 4M /dev/urandom >$D/big-random
# Owners with no name, set-id, rdev 0, flags, project IDs
mkdir -p $D/noname-dir; chown 4321:8765 $D/noname-dir; touch $D/noname-dir/f; chown 54321:65432 $D/noname-dir/f
mknod $D/rdev0-char c 0 0; mknod $D/rdev0-block b 0 0
echo s >$D/setuid; chmod 4755 $D/setuid; echo g >$D/setgid; chmod 2755 $D/setgid
echo n >$D/noatime-file; chattr +A $D/noatime-file
echo ai >$D/append-nodump; chattr +a +d $D/append-nodump
echo ii >$D/immutable-file; chattr +i $D/immutable-file
mkdir $D/immutable-dir; echo in >$D/immutable-dir/inside; chattr +i $D/immutable-dir
echo p >$D/projid-file; chattr -p 42 $D/projid-file
mkdir $D/projdir; chattr -p 77 +P $D/projdir 2>>$L/torture.err; echo q >$D/projdir/inner
# A file's own bcachefs options
mkdir $D/bchopts; setfattr -n bcachefs.compression -v lz4 $D/bchopts 2>>$L/torture.err
echo c >$D/bchopts/inherits
echo o >$D/bch-own; for o in "compression=zstd" "background_compression=lz4" "data_replicas=1"; do
    setfattr -n bcachefs.${o%%=*} -v ${o#*=} $D/bch-own 2>>$L/torture.err; done
echo w >$D/bch-nocow; setfattr -n bcachefs.nocow -v 1 $D/bch-nocow 2>>$L/torture.err
# xattrs and ACLs of our own
echo x >$D/xattrs; setfattr -n user.zz -v 1 $D/xattrs; setfattr -n user.aa -v 0x00ff10 $D/xattrs
setfattr -n trusted.bko -v t $D/xattrs; setfattr -n security.bko -v s $D/xattrs 2>>$L/torture.err
echo y >$D/acl; setfacl -m u:1234:rw,g:5678:r,m::rw $D/acl
mkdir $D/dacl; setfacl -m u:1234:rwx -d -m u:1234:rx,g:5678:rx $D/dacl
mkdir -p $D/empty-deep/a/b/c/d/e/f/g
touch "$D/$(printf 'bad-\377\376-name')"
# The directories above the backed-up path, with metadata of their own
chown 4321:8765 $A; chmod 2751 $A; setfattr -n user.ancestor -v anc $A
touch -h -m -d '2004-05-06 07:08:09.123456789' $A $D
touch -h -a -d '2005-06-07 08:09:10.987654321' $A $D
sync
manifest $M/src >$L/src.man
{ echo "fixture: $(cut -d'|' -f1 $L/src.man | sort -u | wc -l) paths, $(grep -c '|xattr:' $L/src.man) xattrs,"\
  "$(grep -c '|xattr:bcachefs' $L/src.man) bcachefs options, $(grep -c '|sparse|1' $L/src.man) sparse,"\
  "$(grep '|flags|' $L/src.man | grep -vc '|flags|-*$') with flags, $(grep '|projid|' $L/src.man | grep -vc '|projid|0$') with project IDs"; } >>$L/info

# --- backups ----------------------------------------------------------------
bk() {  # TOOL REPO
    if [[ $1 == *rustic* ]]; then
        { $1 --no-progress --no-cache -r $2 init && $1 --no-progress --no-cache -r $2 backup --set-atime yes $D; } >$L/$(basename $2).backup 2>&1
    else
        { $1 -r $2 init -q && $1 -r $2 backup -q --with-atime $D; } >$L/$(basename $2).backup 2>&1
    fi
    echo $?
}
for t in restic rustic; do
    bin=$RS; [ $t = rustic ] && bin=$RU
    s=$(bk $bin $W/repo-$t); eval bs_$t=$s
    echo "$t backup exit $s; $(grep -ciE 'warn|error' $L/repo-$t.backup) warning lines" >>$L/info
    [ $s = 0 ] && ! grep -qiE 'signal|panic|floating' $L/repo-$t.backup &&
        res PASS "$t-bko runs natively on NixOS 26.05 (owners with no name)" "backup exit 0" ||
        res FAIL "$t-bko runs natively on NixOS 26.05 (owners with no name)" "exit $s: $(tail -3 $L/repo-$t.backup | tr '\n' ' ')"
done
manifest $M/src >$L/src-after.man
mdiff $L/src.man $L/src-after.man >$L/src-after.diff
[ -s $L/src-after.diff ] && res FAIL "Backups leave the source alone" "$(head -4 $L/src-after.diff | tr '\n\t' '  ')" ||
    res PASS "Backups leave the source alone" "manifest unchanged"

# --- same repository format: node identity, ancestors included -------------
rt_nodes $RS $W/repo-restic /mnt >$L/nodes.restic 2>$L/nodes.err
rt_nodes $RS $W/repo-rustic /mnt >$L/nodes.rustic 2>>$L/nodes.err
if [ -s $L/nodes.restic ] && rt_node_diff $L/nodes.restic $L/nodes.rustic >$L/nodes.diff 2>&1; then
    res PASS "Same repository format: snapshots node-identical" "$(wc -l <$L/nodes.restic) nodes from /mnt down"
else
    res FAIL "Same repository format: snapshots node-identical" "$(grep -v '(not a check)' $L/nodes.diff | head -8 | tr '\n' ' ')"
fi

# --- restores in four directions --------------------------------------------
restore() {  # TOOL REPO TARGET
    if [[ $1 == *rustic* ]]; then $1 --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $1 -r $2 restore -q latest --target $3; fi
}
NEEDS="content:contents, sizes, types, symlink targets
times:mtime and atime to the ns (Nix store mtime 1)
owner:numeric owners (no-name uids), modes incl. set-id/sticky
acl:POSIX ACLs access+default (journal, acl, dacl)
cap:file capabilities (security.capability)
xattr:extended attributes user/trusted/security
bchopt:a file's own bcachefs options (compression, background_compression, data_replicas, nocow)
flags:inode flags (immutable, append, nodump, noatime)
projid:Linux project IDs on bcachefs
link:hard links
dev:device nodes (incl. rdev 0)
sparse:sparse files marked by block count (perl-env wrappers, journal, 512-byte holes)"
: >$L/all.diff
for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    mkbch $d || { echo "exit|$d|no target" >>$L/all.diff; continue; }
    restore $bin $W/repo-$b $M/$d >$L/$d.restore 2>&1; s=$?
    echo "exit|$d|$s" >>$L/all.diff
    sync; manifest $M/$d$M/src >$L/$d.man
    mdiff $L/src.man $L/$d.man >$L/$d.diff
    sed "s/^/$d\t/" $L/$d.diff >>$L/all.diff
    [ -e $M/$d$M/src/anc/data/root/bko-torture/socket ] && echo "socket|$d|restored" >>$L/all.diff
done
for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
    s=$(grep "^exit|$d|" $L/all.diff | cut -d'|' -f3)
    [ "$s" = 0 ] && res PASS "Restore $d exits 0" "$(grep -ciE 'warn|error' $L/$d.restore) message lines" ||
        res FAIL "Restore $d exits 0" "exit $s: $(grep -iE 'error|warn' $L/$d.restore | head -3 | tr '\n' ' ')"
done
while IFS= read -r nl; do
    k=${nl%%:*} label=${nl#*:}
    bad=$(awk -F'\t' -v k=$k '$2 == k {print $1 ": " $3}' $L/all.diff | head -4 | tr '\n' ' ')
    [ -z "$bad" ] && res PASS "$label" "same in all four directions" || res FAIL "$label" "$bad"
done <<<"$NEEDS"
# Ancestors above the source mount: as each repository restores them
for d in restic-restic rustic-rustic restic-rustic rustic-restic; do stat -c '%a %u:%g %Y' $M/$d/mnt $M/$d/mnt/bkc 2>/dev/null | tr '\n' ';'; echo; done >$L/upper
[ $(sort -u $L/upper | wc -l) = 1 ] && res PASS "Ancestor directories restored the same from both tools' repositories" "$(head -1 $L/upper)" ||
    res FAIL "Ancestor directories restored the same from both tools' repositories" "$(tr '\n' '|' <$L/upper)"

# --- restore again over the restored tree (immutable/append-only files kept) --
for d in restic-restic rustic-rustic; do
    b=${d%-*}; bin=$RS; [ $b = rustic ] && bin=$RU
    t=$M/$d$M/src/anc/data
    echo changed >$t/big-random.tmp; mv $t/big-random.tmp $t/big-random
    restore $bin $W/repo-$b $M/$d >$L/$d.over 2>&1; s=$?
    sync; manifest $M/$d$M/src >$L/$d.over.man
    mdiff $L/src.man $L/$d.over.man >$L/$d.over.diff
    if [ $s = 0 ] && [ ! -s $L/$d.over.diff ]; then res PASS "Restore over existing immutable/append-only files ($b)" "exit 0, identical"
    else res FAIL "Restore over existing immutable/append-only files ($b)" "exit $s; $(head -4 $L/$d.over.diff | tr '\n\t' '  '); $(grep -iE 'error|warn' $L/$d.over | head -3 | tr '\n' ' ')"; fi
done

# --- sockets ------------------------------------------------------------------
sk=$(grep -c '^socket|' $L/all.diff)
nsock=$(cat $L/nodes.restic $L/nodes.rustic | grep -c 'type=socket')
src_sock=$(find $M/src -type s | wc -l)
[ $sk = 0 ] && [ $nsock = 0 ] && res PASS "Sockets skipped by both tools" "$src_sock in the source; no socket node, none restored" ||
    res FAIL "Sockets skipped by both tools" "$sk restored, $nsock nodes"

# --- one non-root run (uid 4321 backs up its own tree and restores it) -------
mkbch nonroot
N=$M/nonroot; mkdir -p $N/src $N/out; chown 4321:4321 $N $N/src $N/out
su_() { setpriv --reuid=4321 --regid=4321 --clear-groups env HOME=/tmp RESTIC_PASSWORD=x RUSTIC_PASSWORD=x "$@"; }
su_ sh -c "cd $N/src && mkdir -p d/e && echo a >d/f && chmod 0640 d/f && ln -s f d/l && mkfifo p && head -c 100000 /dev/urandom >big && truncate -s 10M sparse && echo z >>sparse && touch -d '2003-03-03 03:03:03.3' d/f && setfattr -n user.nr -v 1 d/f && setfacl -d -m u::rwx,g::rx,o::rx d"
manifest $N/src >$L/nr-src.man
nrd=""
for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${d%-*} r=${d#*-}; bb=$RS; [ $b = rustic ] && bb=$RU; rb=$RS; [ $r = rustic ] && rb=$RU
    if [ ! -d $N/repo-$b ]; then
        if [ $b = rustic ]; then su_ sh -c "$bb --no-progress --no-cache -r $N/repo-$b init && $bb --no-progress --no-cache -r $N/repo-$b backup --set-atime yes $N/src" >$L/nr-$b.backup 2>&1
        else su_ sh -c "$bb -r $N/repo-$b --no-cache init -q && $bb -r $N/repo-$b --no-cache backup -q --with-atime $N/src" >$L/nr-$b.backup 2>&1; fi
        echo "nonroot backup $b exit $?" >>$L/info
    fi
    mkdir -p $N/out/$d; chown 4321:4321 $N/out/$d
    if [ $r = rustic ]; then su_ $rb --no-progress --no-cache -r $N/repo-$b restore --numeric-id latest:$N/src $N/out/$d >$L/nr-$d.restore 2>&1
    else su_ $rb -r $N/repo-$b --no-cache restore -q latest:$N/src --target $N/out/$d >$L/nr-$d.restore 2>&1; fi
    s=$?
    manifest $N/out/$d >$L/nr-$d.man; mdiff $L/nr-src.man $L/nr-$d.man >$L/nr-$d.diff
    [ $s = 0 ] && [ ! -s $L/nr-$d.diff ] || nrd="$nrd $d: exit $s $(head -3 $L/nr-$d.diff | tr '\n\t' '  ');"
done
[ -z "$nrd" ] && res PASS "Non-root backup and restore (uid 4321, own tree: xattrs, default ACL, FIFO, sparse, times)" "all four directions identical" ||
    res FAIL "Non-root backup and restore (uid 4321, own tree: xattrs, default ACL, FIFO, sparse, times)" "$nrd"

# --- limits -------------------------------------------------------------------
res LIMIT "SELinux labels" "NixOS 26.05 here has no SELinux or AppArmor labels on disk"
res LIMIT "Birth times" "bcachefs keeps them, Linux can't set them (BACKOTTER.md)"
res LIMIT "Whole-system restore (BIOS GRUB, ext4 /boot, bcachefs /)" "out of scope: whole-disk"
sed 's/^/torture: /' $L/torture.err >>$L/info

# --- clean up -----------------------------------------------------------------
cd /
for m in $M/*; do mountpoint -q $m && { find $m -xdev \( -type f -o -type d \) -exec chattr -i -a {} + 2>/dev/null; umount $m; }; done
for d in $LOOPS; do losetup -d $d 2>/dev/null; done
rm -rf $W $B
tar -C /root -czf /root/st1.tgz st1
exit 0
