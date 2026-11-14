#!/usr/bin/env bash
# SticTest1 guix15 and guix15-bios, the part that runs in the guest as root
# (/root).  Guix System 1.5.0 has no python3, perl, getfattr/setfattr or
# setfacl: the fixture and the comparison use bash, gawk and the guest's own
# tools (find -printf, lsattr, sha256sum, stat; btrfs on guix15-bios).
# Every file system is a fresh one made like the guest's root: ext4 with the
# root's features (guix15), or Btrfs with the subvolumes @ (/), @home and
# @gnu (compress=zstd) mounted where the guest has them (guix15-bios).
# Beyond nixos2605's tests, a Guix-store-like tree (mtime 1, modes
# 0444/0555, directories 0555, a .links directory hard-linking every file)
# and a copy of a part of the guest's own /gnu/store, whose hard links,
# mtimes, modes and contents are judged after every restore.
# Prints "PASS|FAIL|LIMIT need: detail" lines; details in /root/st1/.
set -u
cd /root
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
B=/var/tmp/st1bin RS=/var/tmp/st1bin/restic-bko RU=/var/tmp/st1bin/rustic-bko
W=/var/tmp/st1 M=/mnt/bkc L=/root/st1
STORE_MB=${STORE_MB:-200}   # how much of the guest's /gnu/store to copy
mkdir -p $W $M $L $B
cp /root/restic-bko /root/rustic-bko $B/ && chmod 755 $B $B/*
. /root/rt-common.sh
# rt_nodes (rt-common.sh) that doesn't descend into the directories whose
# path (relative to PATH, with a trailing /) matches the ERE PRUNE: one
# "cat blob" a tree is minutes for the real store copy's thousand trees.
# The pruned directories' own nodes are still listed.
rt_nodes_pruned() {  # RESTIC REPO PATH PRUNE
    rt_id=$(RESTIC_PASSWORD=backotter-compare $1 -r $2 --no-cache --no-lock snapshots --json latest |
        sed -n 's/.*"tree":"\([0-9a-f]*\)".*/\1/p' | head -n 1)
    [ -n "$rt_id" ] || { echo "rt_nodes_pruned: no snapshot in $2" >&2; return 1; }
    rt_rest=${3#/} rt_self=""
    while [ -n "$rt_rest" ]; do
        rt_comp=${rt_rest%%/*}
        case $rt_rest in */*) rt_rest=${rt_rest#*/} ;; *) rt_rest="" ;; esac
        rt_self=$(rt_tree $1 $2 $rt_id | $rt_awk -F '\t' -v n="$rt_comp" '$1 == n')
        [ -n "$rt_self" ] || { echo "rt_nodes_pruned: no $rt_comp in $2's tree" >&2; return 1; }
        rt_id=$(printf '%s\n' "$rt_self" | cut -f 2)
    done
    printf '%s\n' "$rt_self" | $rt_awk -F '\t' 'BEGIN { OFS = "\t" } { $1 = "."; $2 = ""; sub(/\t\t/, "\t"); print }'
    # Breadth first, the trees still to read in a queue: ID<TAB>PREFIX.
    printf '%s\t\n' "$rt_id" >$rt_tmp/bko-rt-nodes.queue
    while [ -s $rt_tmp/bko-rt-nodes.queue ]; do
        IFS='	' read -r rt_id rt_pre <$rt_tmp/bko-rt-nodes.queue
        sed 1d $rt_tmp/bko-rt-nodes.queue >$rt_tmp/bko-rt-nodes.queue2 && mv $rt_tmp/bko-rt-nodes.queue2 $rt_tmp/bko-rt-nodes.queue
        rt_tree $1 $2 $rt_id >$rt_tmp/bko-rt-nodes.tree || { echo "rt_nodes_pruned: can't read tree $rt_id" >&2; return 1; }
        $rt_awk -F '\t' -v pre="$rt_pre" -v prune="$4" '$2 != "-" && (pre $1 "/") !~ prune { print $2 "\t" pre $1 "/" }' \
            $rt_tmp/bko-rt-nodes.tree >>$rt_tmp/bko-rt-nodes.queue
        $rt_awk -F '\t' -v pre="$rt_pre" 'BEGIN { OFS = "\t" } { $1 = pre $1; $2 = ""; sub(/\t\t/, "\t"); print }' \
            $rt_tmp/bko-rt-nodes.tree
    done
    rm -f $rt_tmp/bko-rt-nodes.queue $rt_tmp/bko-rt-nodes.tree
}
res() { echo "$1 $2: $3"; }
FS=$(findmnt -no FSTYPE / | head -1)
ROOTDEV=$(findmnt -no SOURCE / | head -1 | sed 's/\[.*//')
case $FS in ext4|btrfs) ;; *) res FAIL "fixture" "root is $FS, not ext4 or btrfs"; exit 1 ;; esac
# ext4: the root's own features, as mkfs.ext4 -O takes them
EXTFEAT=""
[ $FS = ext4 ] && EXTFEAT=$(tune2fs -l "$ROOTDEV" | sed -n 's/^Filesystem features: *//p' |
    tr ' ' '\n' | grep -vE '^(needs_recovery|orphan_present|)$' | paste -sd, -)
LOOPS=""
# mkscratch NAME MNT [SUBDIR]: a fresh file system like the guest's root at
# MNT.  On Btrfs, the subvolumes @, @home and @gnu, with @ at MNT and, given
# SUBDIR (the restored data directory below MNT), @home at SUBDIR/home and
# @gnu at SUBDIR/gnu with compress=zstd, as the guest mounts /home and /gnu.
mkscratch() {
    truncate -s 2G $W/$1.img || return 1
    local d; d=$(losetup -f --show $W/$1.img) || return 1
    LOOPS="$LOOPS $d"
    mkdir -p $2
    if [ $FS = ext4 ]; then
        mkfs.ext4 -q -F ${EXTFEAT:+-O "$EXTFEAT"} $d >$L/mkfs-$1.log 2>&1 &&
            mount -t ext4 -o noatime $d $2
    else
        mkfs.btrfs -f -L guixroot $d >$L/mkfs-$1.log 2>&1 || return 1
        local top=$W/top-$1; mkdir -p $top
        mount -t btrfs $d $top && btrfs subvolume create $top/@ >>$L/mkfs-$1.log &&
            btrfs subvolume create $top/@home >>$L/mkfs-$1.log && btrfs subvolume create $top/@gnu >>$L/mkfs-$1.log &&
            umount $top || return 1
        mount -t btrfs -o noatime,subvol=@ $d $2 || return 1
        if [ -n "${3:-}" ]; then
            mkdir -p $3/home $3/gnu &&
                mount -t btrfs -o noatime,subvol=@home $d $3/home &&
                mount -t btrfs -o noatime,subvol=@gnu,compress=zstd $d $3/gnu
        fi
    fi
}
{
    echo "kernel: $(uname -r)"; echo "root: $FS on $ROOTDEV"
    $RS version | head -1; $RU --version | head -1
    echo "nsswitch: $(grep -E '^(passwd|group)' /etc/nsswitch.conf | tr '\n' ' ')"
    findmnt -no SOURCE,TARGET,FSTYPE,OPTIONS -R / | grep -vE ' (proc|sysfs|devtmpfs|devpts|tmpfs|debugfs|efivarfs) '
    [ $FS = ext4 ] && echo "ext4 features: $EXTFEAT"
    [ $FS = btrfs ] && btrfs subvolume list / 2>&1
    echo "guest /gnu/store: $(find /gnu/store -xdev -type f | wc -l) files, $(find /gnu/store -xdev -type f -links +1 | wc -l) with links, $(ls /gnu/store/.links | wc -l) in .links"
} >$L/info 2>&1

# The manifest: one "PATH|KEY|VALUE" line per fact, sorted.  Hard links are
# named by the first path of their (device, inode): Btrfs subvolumes have
# inode numbers of their own.  Sparse is "blocks*512 < size".  Sockets are
# left out (both tools skip them, by decision; "Sockets skipped by both
# tools" below judges them), so no comparison counts one.
manifest() {  # DIR
    (cd "$1" || exit 1
    find . -mindepth 1 ! -type s -printf '%P\t%y\t%m\t%U:%G\t%s\t%T@\t%A@\t%l\t%D:%i\t%n\t%b\n' | sort | gawk -F'\t' '{
        p=$1; y=$2
        print p "|type|" y; print p "|mode|" $3; print p "|owner|" $4
        print p "|mtime|" $6; print p "|atime|" $7
        if (y != "d") print p "|size|" $5
        if (y == "l") print p "|target|" $8
        if (y == "f") print p "|sparse|" ($11*512 < $5 ? 1 : 0)
        if (y != "d" && $10 > 1) { if (!($9 in first)) first[$9] = p; print p "|link|" first[$9] }}'
    find . -mindepth 1 \( -type c -o -type b \) -printf '%P\n' | while IFS= read -r f; do
        printf '%s|rdev|%s\n' "$f" "$(stat -c '%t:%T' "$f")"; done
    find . -mindepth 1 -type f -print0 | xargs -0 -r sha256sum | sed -E 's#^\\?([0-9a-f]+)  \./(.*)$#\2|sha|\1#'
    # Inode flags and project IDs in one lsattr pass, for files and directories
    find . -mindepth 1 \( -type f -o -type d \) -printf './%P\n' >$W/fd.list
    lsattr -pRa . 2>/dev/null | gawk 'NR == FNR { want[$0] = 1; next }
        NF >= 3 { p = $0; sub(/^ *[^ ]+ +[^ ]+ /, "", p)
            if (p in want) { sub(/^\.\//, "", p); print p "|flags|" $2; print p "|projid|" $1 } }' $W/fd.list -
    # (one btrfs call a path: not in the stores' thousands, which have none)
    if [ $FS = btrfs ]; then
        find . -mindepth 1 \( -path '*/gnu/store' -o -path '*/gnu/real-store' \) -prune -o \
            \( -type f -o -type d \) -printf '%P\n' | while IFS= read -r f; do
            v=$(btrfs property get "./$f" compression 2>/dev/null | sed -n 's/^compression=//p')
            [ -n "$v" ] && printf '%s|btrprop|compression=%s\n' "$f" "$v"; done
    fi
    ) | grep -v '^lost+found' | LC_ALL=C sort
}
# The stores' own facts: per tree, the count of files with links
# (find -type f -links +1), each file's nlink, and each inode's group of
# paths, sorted.
STORES="gnu/store gnu/real-store"
storeman() {  # DATA_DIR
    for t in $STORES; do (cd "$1/$t" || { echo "$t|missing|1"; exit 0; }
        echo "$t|count|$(find . -type f -links +1 | wc -l)"
        find . -mindepth 1 -type f -printf '%P\t%n\t%D:%i\n' | sort | gawk -F'\t' -v t=$t '{
            print t "/" $1 "|nlink|" $2; g[$3] = ($3 in g) ? g[$3] " " $1 : $1 }
            END { for (i in g) if (index(g[i], " ")) print t "|group|" g[i] }'
    ); done | LC_ALL=C sort
}
# Which need a differing manifest key belongs to
need_of() { gawk -F'|' '{k=$2
    if (k == "flags") n="flags"; else if (k == "projid") n="projid"; else if (k == "btrprop") n="btrprop"
    else if (k == "mtime" || k == "atime") n="times"; else if (k == "mode" || k == "owner") n="owner"
    else if (k == "link") n="link"; else if (k == "rdev") n="dev"; else if (k == "sparse") n="sparse"
    else n="content"; print n "\t" $0}'; }
# diff two manifests, one line per differing fact: "NEED<TAB>< or >PATH|KEY|VALUE"
mdiff() { diff "$1" "$2" | grep -E '^[<>]' | sed 's/^\([<>]\) /\1/' | gawk '{print substr($0,1,1) "\t" substr($0,2)}' |
    while IFS=$'\t' read -r side line; do printf '%s\n' "$line" | need_of | sed "s/\t/\t$side/"; done; }
# The stores' paths in a manifest diff (anc/data/gnu/store..., real-store...)
instore() { grep -E $'\t''[<>]anc/data/gnu/(store|real-store)(/|[|])'; }
notstore() { grep -vE $'\t''[<>]anc/data/gnu/(store|real-store)(/|[|])'; }

# --- the fixture ------------------------------------------------------------
A=$M/src/anc D=$A/data
mkscratch src $M/src $D || { res FAIL "fixture" "can't make the source $FS ($(tail -2 $L/mkfs-src.log | tr '\n' ' '))"; exit 1; }
mkdir -p $D
bash /root/torture.sh $D 2>$L/torture.err >/dev/null
T=$D/root/bko-torture
rm -f $T/socket
rt_mksock $T/socket 2>>$L/torture.err ||
    guile -c '(bind (socket AF_UNIX SOCK_STREAM 0) AF_UNIX (cadr (command-line)))' $T/socket 2>>$L/torture.err
# The guest's own examples: set-id programs, the profile links, the
# daemon's database, Shepherd's log
G=$D/guest; mkdir -p $G/privileged $G/profiles $G/db $G/log
cp -a /run/privileged/bin/su /run/privileged/bin/passwd $G/privileged/ 2>>$L/torture.err
cp -a /var/guix/profiles/system /var/guix/profiles/system-*-link $G/profiles/ 2>>$L/torture.err
cp -a --sparse=always /var/guix/db/db.sqlite $G/db/ 2>>$L/torture.err
cp -a /var/log/messages $G/log/ 2>>$L/torture.err
# A file with a hole between blocks, a big sparse file, random data
printf A >$D/hole; truncate -s 12288 $D/hole; printf B | dd of=$D/hole bs=1 seek=12287 conv=notrunc status=none
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
echo p >$D/projid-file; PROJID=1; chattr -p 42 $D/projid-file 2>$L/projid.err || PROJID=0
# Btrfs: a file's and a directory's own compression property
if [ $FS = btrfs ]; then
    touch $D/compress-own; btrfs property set $D/compress-own compression lzo 2>>$L/torture.err
    head -c 200000 /dev/zero >>$D/compress-own
    mkdir $D/compress-dir; btrfs property set $D/compress-dir compression zstd 2>>$L/torture.err; echo c >$D/compress-dir/inherits
fi
# @home's own contents (a directory on ext4)
mkdir -p $D/home/alice/.config; echo h >$D/home/alice/.config/x; chown -R 1000:998 $D/home/alice; chmod 700 $D/home/alice
mkdir -p $D/empty-deep/a/b/c/d/e/f/g
touch "$D/$(printf 'bad-\377\376-name')"

# --- the store-like tree: as guix-daemon leaves /gnu/store --------------------
# 40 items of 75 files: bin/ 0555, lib/ and share/ 0444; the share/ files
# the same in every item, bin/ shared by every fifth, lib/ each its own.
# Every file is hard-linked from .links/CONTENT (nlink 2+), as the daemon
# deduplicates; directories 0555; files, directories and symlinks mtime 1.
S=$D/gnu/store; mkdir -p $S/.links
for k in $(seq -w 0 39); do
    I=$S/$(printf '%032d' $((10#$k)) | tr 0-9 a-j)-pkg$k-1.$k
    mkdir -p $I/bin $I/lib $I/share/doc
    for j in $(seq -w 0 74); do
        if [ $j -lt 10 ]; then f=$I/bin/prog$j c=bin$((10#$k % 5))-$j
        elif [ $j -lt 40 ]; then f=$I/lib/lib$k-$j.so.1 c=lib$k-$j
        else f=$I/share/doc/doc$j c=doc$j; fi
        if [ -e $S/.links/$c ]; then ln $S/.links/$c $f
        else printf 'content %s\n%0*d\n' $c $((10#$j * 37 + 1)) 0 >$f; ln $f $S/.links/$c; fi
    done
    ln -s lib$k-10.so.1 $I/lib/lib$k.so
    ln -s ../../$(basename $I)/bin/prog00 $I/bin/prog-alias 2>/dev/null
done
# A profile of symlinks into the store, and two into .links
mkdir -p $D/gnu/profile/bin
for p in $S/*-pkg0[0-4]-*/bin/prog0[0-2]; do ln -s ../../store/${p#$S/} $D/gnu/profile/bin/$(basename $(dirname $(dirname $p)))-$(basename $p); done
ln -s ../store/.links/doc40 $D/gnu/profile/doc40; ln -s ../store/.links/lib07-12 $D/gnu/profile/lib07
find $S -mindepth 1 -path $S/.links -prune -o -type f -path '*/bin/*' -exec chmod 0555 {} + -o -type f -exec chmod 0444 {} +
find $S -mindepth 1 -path $S/.links -prune -o -type d -exec chmod 0555 {} +
find $S/.links $D/gnu/profile -mindepth 1 -exec touch -h -d @1 {} +
find $S -mindepth 1 -path $S/.links -prune -o -exec touch -h -d @1 {} +
chmod 1775 $S; chmod 0755 $S/.links

# --- a copy of the guest's own /gnu/store, STORE_MB of it -------------------
# Whole items, in name order, those under 60 MB until STORE_MB, and the
# .links entries of their files, in one tar so that the links stay links.
RSD=$D/gnu/real-store; mkdir -p $RSD
(cd /gnu/store && for i in $(ls | grep -v '^\.links$'); do printf '%s\t%s\n' "$(du -sk "$i" | cut -f1)" "$i"; done) >$W/items.du
gawk -F'\t' -v b=$((STORE_MB * 1024)) '$1 < 61440 && s < b { s += $1; print $2 }' $W/items.du >$W/items.list
(cd /gnu/store && find $(cat $W/items.list) -type f -links +1 -printf '%i\n' | sort -u) >$W/items.inodes
find /gnu/store/.links -maxdepth 1 -type f -printf '%i\t.links/%f\n' | gawk -F'\t' 'NR == FNR { i[$1] = 1; next } $1 in i { print $2 }' $W/items.inodes - >$W/links.list
cat $W/items.list $W/links.list >$W/store.list
tar -C /gnu/store --numeric-owner -cf - -T $W/store.list 2>>$L/torture.err | tar -C $RSD --numeric-owner -xpf - 2>>$L/torture.err
chmod --reference=/gnu/store/.links $RSD/.links; touch -r /gnu/store/.links $RSD/.links
chmod 1775 $RSD; touch -r /gnu/store $RSD
echo "real store: $(wc -l <$W/items.list) items ($(gawk -F'\t' 'NR == FNR { w[$0] = 1; next } $2 in w { s += $1 } END { print int(s / 1024) }' $W/items.list $W/items.du) MiB), $(wc -l <$W/links.list) .links entries; tar: $(grep -c . $L/torture.err) message lines so far" >>$L/info

# The directories above the backed-up path, with metadata of their own
chown 4321:8765 $A; chmod 2751 $A
touch -h -m -d '2004-05-06 07:08:09.123456789' $A $D
touch -h -a -d '2005-06-07 08:09:10.987654321' $A $D
sync
manifest $M/src >$L/src.man
storeman $D >$L/src.store
{ echo "fixture: $(cut -d'|' -f1 $L/src.man | sort -u | wc -l) paths, $(grep -c '|sparse|1' $L/src.man) sparse,"\
  "$(grep '|flags|' $L/src.man | grep -vc '|flags|-*$') with flags, $(grep '|projid|' $L/src.man | grep -vc '|projid|0$') with project IDs,"\
  "$(grep -c '|btrprop|' $L/src.man) with btrfs properties"
  echo "stores: $(grep '|count|' $L/src.store | tr '\n' ' '); $(grep -c '|group|' $L/src.store) inode groups"
  echo "store-like modes: $(grep -E '^anc/data/gnu/store/.*\|mode\|' $L/src.man | cut -d'|' -f3 | sort | uniq -c | tr -s ' \n' ' ')"
  echo "store-like mtimes: $(grep -E '^anc/data/gnu/store/[^.].*\|mtime\|' $L/src.man | cut -d'|' -f3 | sort | uniq -c | tr -s ' \n' ' ')"
} >>$L/info

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
        res PASS "$t-bko runs natively on Guix System 1.5.0 (owners with no name)" "backup exit 0" ||
        res FAIL "$t-bko runs natively on Guix System 1.5.0 (owners with no name)" "exit $s: $(tail -3 $L/repo-$t.backup | tr '\n' ' ')"
done
manifest $M/src >$L/src-after.man
mdiff $L/src.man $L/src-after.man >$L/src-after.diff
[ -s $L/src-after.diff ] && res FAIL "Backups leave the source alone" "$(head -4 $L/src-after.diff | tr '\n\t' '  ')" ||
    res PASS "Backups leave the source alone" "manifest unchanged"

# --- same repository format: node identity, ancestors included -------------
# Not below the real store copy's items (their trees are thousands)
PRUNE='/gnu/real-store/[^/]+/$'
rt_nodes_pruned $RS $W/repo-restic /mnt "$PRUNE" >$L/nodes.restic 2>$L/nodes.err
rt_nodes_pruned $RS $W/repo-rustic /mnt "$PRUNE" >$L/nodes.rustic 2>>$L/nodes.err
if [ -s $L/nodes.restic ] && rt_node_diff $L/nodes.restic $L/nodes.rustic >$L/nodes.diff 2>&1; then
    res PASS "Same repository format: snapshots node-identical" "$(wc -l <$L/nodes.restic) nodes from /mnt down"
else
    res FAIL "Same repository format: snapshots node-identical" "$(grep -v '(not a check)' $L/nodes.diff | head -8 | tr '\n' ' ')"
fi
echo "nodes with extended attributes: $(grep -c 'extended_attributes=' $L/nodes.restic)" >>$L/info

# --- restores in four directions --------------------------------------------
restore() {  # TOOL REPO TARGET
    if [[ $1 == *rustic* ]]; then $1 --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $1 -r $2 restore -q latest --target $3; fi
}
NEEDS="content:contents, sizes, types, symlink targets
times:mtime and atime to the ns
owner:numeric owners (no-name uids), modes incl. set-id/sticky
flags:inode flags (immutable, append, nodump, noatime)
link:hard links (outside the stores)
dev:device nodes (incl. rdev 0)
sparse:sparse files marked by block count (holes, a 64 MiB sparse file)"
[ $PROJID = 1 ] && NEEDS="$NEEDS
projid:Linux project IDs on ext4"
[ $FS = btrfs ] && NEEDS="$NEEDS
btrprop:a file's own btrfs compression property (btrfs.compression)"
DIRS="restic-restic rustic-rustic restic-rustic rustic-restic"
: >$L/all.diff
for d in $DIRS; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    mkscratch $d $M/$d $M/$d$D || { echo "exit|$d|no target" >>$L/all.diff; continue; }
    restore $bin $W/repo-$b $M/$d >$L/$d.restore 2>&1; s=$?
    echo "exit|$d|$s" >>$L/all.diff
    sync; manifest $M/$d$M/src >$L/$d.man
    storeman $M/$d$D >$L/$d.store
    mdiff $L/src.man $L/$d.man >$L/$d.diff
    sed "s/^/$d\t/" $L/$d.diff >>$L/all.diff
    [ -e $M/$d$T/socket ] && echo "socket|$d|restored" >>$L/all.diff
    [ $FS = btrfs ] && echo "$d: $(findmnt -no TARGET,OPTIONS -R $M/$d | tr '\n' ' ')" >>$L/info
done
for d in $DIRS; do
    s=$(grep "^exit|$d|" $L/all.diff | cut -d'|' -f3)
    [ "$s" = 0 ] && res PASS "Restore $d exits 0" "$(grep -ciE 'warn|error' $L/$d.restore) message lines" ||
        res FAIL "Restore $d exits 0" "exit $s: $(grep -iE 'error|warn' $L/$d.restore | head -3 | tr '\n' ' ')"
done
while IFS= read -r nl; do
    k=${nl%%:*} label=${nl#*:}
    bad=$(notstore <$L/all.diff | gawk -F'\t' -v k=$k '$2 == k {print $1 ": " $3}' | head -4 | tr '\n' ' ')
    [ -z "$bad" ] && res PASS "$label" "same in all four directions" || res FAIL "$label" "$bad"
done <<<"$NEEDS"
# Ancestors above the source mount: as each repository restores them
for d in $DIRS; do stat -c '%a %u:%g %Y' $M/$d/mnt $M/$d/mnt/bkc 2>/dev/null | tr '\n' ';'; echo; done >$L/upper
[ $(sort -u $L/upper | wc -l) = 1 ] && res PASS "Ancestor directories restored the same from both tools' repositories" "$(head -1 $L/upper)" ||
    res FAIL "Ancestor directories restored the same from both tools' repositories" "$(tr '\n' '|' <$L/upper)"

# --- restore again over the restored tree (immutable/append-only files kept) --
for d in restic-restic rustic-rustic; do
    b=${d%-*}; bin=$RS; [ $b = rustic ] && bin=$RU
    t=$M/$d$D
    echo changed >$t/big-random.tmp; mv $t/big-random.tmp $t/big-random
    restore $bin $W/repo-$b $M/$d >$L/$d.over 2>&1; s=$?
    sync; manifest $M/$d$M/src >$L/$d.over.man
    storeman $M/$d$D >$L/$d-over.store
    mdiff $L/src.man $L/$d.over.man >$L/$d.over.diff
    sed "s/^/$d-over\t/" $L/$d.over.diff | instore >>$L/all.diff
    if [ $s = 0 ] && ! notstore <$L/$d.over.diff | grep -q .; then res PASS "Restore over existing immutable/append-only files ($b)" "exit 0, identical"
    else res FAIL "Restore over existing immutable/append-only files ($b)" "exit $s; $(notstore <$L/$d.over.diff | head -4 | tr '\n\t' '  '); $(grep -iE 'error|warn' $L/$d.over | head -3 | tr '\n' ' ')"; fi
done

# --- the stores: hard links, mtime 1, read-only modes, contents ---------------
# Per restore (four directions, and the two restores over a restored tree):
# the manifest's store paths and the stores' link facts.  Hard links broken
# by rustic-bko (rustic-rustic) FAIL; broken in a direction restic-bko backed
# up or restored, while rustic-rustic keeps them, are a LIMIT: cross-tool
# fidelity is a courtesy (docs/design/guix.md, Risks).
lk_ru="" lk_rs="" meta=""
for d in $DIRS restic-restic-over rustic-rustic-over; do
    sf=$L/$d.store; [ -f $sf ] || { meta="$meta $d: no restore;"; continue; }
    sd=$(diff $L/src.store $sf | grep -E '^[<>]' | head -3 | tr '\n' ' ')
    ml=$(gawk -F'\t' -v d=$d '$1 == d && $2 == "link" {print $3}' <(instore <$L/all.diff) | head -3 | tr '\n' ' ')
    mm=$(gawk -F'\t' -v d=$d '$1 == d && $2 != "link" {print $2 ": " $3}' <(instore <$L/all.diff) | head -3 | tr '\n' ' ')
    lk="${sd}${ml}"
    if [ -n "$lk" ]; then case $d in rustic-rustic*) lk_ru="$lk_ru $d: $lk;" ;; *) lk_rs="$lk_rs $d: $lk;" ;; esac; fi
    [ -n "$mm" ] && meta="$meta $d: $mm;"
done
cnt=$(grep '|count|' $L/src.store | tr '\n' ' ')
[ -z "$lk_ru" ] && res PASS "Store hard links kept by rustic-bko (.links, nlink, inode groups; store-like tree and the real /gnu/store copy)" "rustic-rustic and over it: same as the source ($cnt)" ||
    res FAIL "Store hard links kept by rustic-bko (.links, nlink, inode groups; store-like tree and the real /gnu/store copy)" "$lk_ru"
if [ -z "$lk_rs" ]; then res PASS "Store hard links kept in restic-bko's directions (restic-restic, restic-rustic, rustic-restic)" "same as the source ($cnt)"
elif [ -z "$lk_ru" ]; then res LIMIT "Store hard links kept in restic-bko's directions (restic-restic, restic-rustic, rustic-restic)" "$lk_rs"
else res FAIL "Store hard links kept in restic-bko's directions (restic-restic, restic-rustic, rustic-restic)" "$lk_rs"; fi
[ -z "$meta" ] && res PASS "Store mtime 1, 0444/0555 modes, 0555 directories, contents (store-like tree and the real /gnu/store copy)" "same in all four directions and over a restored tree" ||
    res FAIL "Store mtime 1, 0444/0555 modes, 0555 directories, contents (store-like tree and the real /gnu/store copy)" "$meta"

# --- sockets ------------------------------------------------------------------
sk=$(grep -c '^socket|' $L/all.diff)
nsock=$(cat $L/nodes.restic $L/nodes.rustic | grep -c 'type=socket')
src_sock=$(find $M/src -type s | wc -l)
if [ $src_sock = 0 ]; then res FAIL "Sockets skipped by both tools" "no socket in the source (see torture.err)"
elif [ $sk = 0 ] && [ $nsock = 0 ]; then res PASS "Sockets skipped by both tools" "$src_sock in the source; no socket node, none restored"
else res FAIL "Sockets skipped by both tools" "$sk restored, $nsock nodes"; fi

# --- one non-root run (uid 4321 backs up its own tree and restores it) -------
# No setpriv or runuser on Guix: coreutils' chroot --userspec drops to a
# uid with no name.
mkscratch nonroot $M/nonroot
N=$M/nonroot; mkdir -p $N/src $N/out; chown 4321:4321 $N $N/src $N/out
su_() { chroot --userspec=4321:4321 --skip-chdir / env HOME=/tmp RESTIC_PASSWORD=x RUSTIC_PASSWORD=x "$@"; }
su_ sh -c "cd $N/src && mkdir -p d/e && echo a >d/f && chmod 0640 d/f && ln -s f d/l && ln d/f d/hard && mkfifo p && head -c 100000 /dev/urandom >big && truncate -s 10M sparse && echo z >>sparse && touch -d '2003-03-03 03:03:03.3' d/f && touch -d @1 big && chmod 0444 big && chmod 0555 d/e"
manifest $N/src >$L/nr-src.man
nrd=""
for d in $DIRS; do
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
[ -z "$nrd" ] && res PASS "Non-root backup and restore (uid 4321, own tree: FIFO, sparse, hard link, times, read-only modes)" "all four directions identical" ||
    res FAIL "Non-root backup and restore (uid 4321, own tree: FIFO, sparse, hard link, times, read-only modes)" "$nrd"

# --- limits -------------------------------------------------------------------
res LIMIT "Extended attributes, POSIX ACLs and file capabilities" "Guix System 1.5.0's base system has no attr, acl or libcap tools (no getfattr, setfattr, setfacl, setcap), so the fixture sets none and the manifest reads none; the store has none"
res LIMIT "SELinux labels" "Guix System 1.5.0 here has no SELinux or AppArmor labels on disk"
res LIMIT "Birth times" "$FS keeps them, Linux can't set them (BACKOTTER.md)"
[ $PROJID = 0 ] && res LIMIT "Linux project IDs" "the guest's $FS root has none: chattr -p refused ($(head -1 $L/projid.err))"
[ $FS = ext4 ] && res LIMIT "Whole-system restore (UEFI GRUB, vfat ESP, ext4 /, swap)" "out of scope: whole-disk"
[ $FS = btrfs ] && res LIMIT "Whole-system restore (BIOS GRUB on GPT, Btrfs / with @, @home, @gnu, swap)" "out of scope: whole-disk"
sed 's/^/torture: /' $L/torture.err >>$L/info

# --- clean up -----------------------------------------------------------------
cd /
for m in $(findmnt -rno TARGET | grep "^$M/" | sort -r); do
    find $m -xdev \( -type f -o -type d \) -exec chattr -i -a {} + 2>/dev/null; umount $m; done
for d in $LOOPS; do losetup -d $d 2>/dev/null; done
rm -rf $W $B
tar -C /root -czf /root/st1.tgz st1
exit 0
