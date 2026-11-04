#!/bin/bash
# SticTest1 debian13, the part that runs in the guest as root (/root).
# Prints "PASS|FAIL|LIMIT need: detail" lines; details in /root/st1/.
# Source: a btrfs made like the guest's root (mkfs.btrfs defaults, the files
# in subvolume @rootfs, mounted subvol=@rootfs,discard=async,space_cache=v2)
# holding the fixture (torture.sh, and tumbleweed's btrfs fixture.sh at
# data/tw: per-file compression property, c/m/C, a nested subvolume,
# capabilities v2/v3, a 15000-byte xattr, the guest's journal and
# random-seed), with an ext4 made like its /boot (mkfs.ext4 defaults:
# metadata_csum(_seed), orphan_file) at data/boot and an ext4 with the
# features the kernel supports but its disks don't use (project, casefold)
# at data/feat; each restore target gets the same three, fresh.
set -u
cd /root
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
RS=/root/restic-bko RU=/root/rustic-bko
W=/var/tmp/st1 M=/mnt/bkosb L=/root/st1
mkdir -p $W $M $L
export RT_TMP=$W
. /root/rt-common.sh
res() { echo "$1 $2: $3"; }
mkfsat() {  # DIR NAME SIZE MKFS [MKFS OPTS]: a fresh file system (loop file) mounted at DIR
    local dir=$1 n=$2 size=$3; shift 3
    truncate -s $size $W/$n.img && "$@" -q -F $W/$n.img && mkdir -p $dir && mount -o loop $W/$n.img $dir
}
mkext4() { mkfsat $M/$1 $1 ${SIZE:-2G} mkfs.ext4; }
mkroot() {  # NAME: a btrfs like the guest's /: files in subvolume @rootfs, mounted with its options
    local n=$1; truncate -s 3G $W/$n.img && mkfs.btrfs -q -f $W/$n.img >/dev/null && mkdir -p $M/$n.top $M/$n &&
    mount -o loop $W/$n.img $M/$n.top && btrfs -q subvolume create $M/$n.top/@rootfs >/dev/null &&
    umount $M/$n.top && rmdir $M/$n.top && mount -o loop,subvol=@rootfs,discard=async,space_cache=v2 $W/$n.img $M/$n
}
btrfs_feat() { btrfs inspect-internal dump-super "$1" 2>/dev/null | grep -E '^(incompat_flags|compat_ro_flags|csum_type|nodesize|sectorsize)' | tr -s ' \t' ' ' | tr '\n' ';'; }
mkboot() { mkfsat $1 $2 512M mkfs.ext4; }                          # like the guest's /boot (ext4)
mkfeat() { mkfsat $1 $2 256M mkfs.ext4 -O project,casefold; }      # ext4's optional features
rec() { python3 /root/fsmeta.py record "$1" "$2" --skip root/bko-torture/socket --skip boot/root/bko-torture/socket --skip tw/up1/up2/tree/socket; }
cmp() { python3 /root/fsmeta.py compare "$1" "$2" --json "$3" >"$3.txt" 2>&1; }
ROOTDEV=$(findmnt -no SOURCE / | sed 's/\[.*//')
echo "root: $ROOTDEV $(dumpe2fs -h $ROOTDEV 2>/dev/null | grep -i 'features:')" >$L/info
echo "root opts: $(findmnt -no OPTIONS /)" >>$L/info
echo "versions: $($RS version) | $($RU --version 2>&1 | head -1)" >>$L/info

# --- the fixture ------------------------------------------------------------
feats() { dumpe2fs -h $1 2>/dev/null | grep -i '^filesystem features:' | sed 's/.*: *//; s/ needs_recovery//; s/ orphan_present//' | tr ' ' '\n' | sort | xargs; }
mkroot src || { res FAIL fixture "can't make the source btrfs"; exit 1; }
A=$M/src/anc1 D=$M/src/anc1/data
mkdir -p $D
mkboot $D/boot srcboot || { res FAIL fixture "can't make the /boot-like ext4"; exit 1; }
mkfeat $D/feat srcfeat || { res FAIL fixture "can't make the project/casefold ext4"; exit 1; }
for m in $M/src $D/boot $D/feat; do mount -o remount,strictatime $m; done
echo "src (root-like) $(findmnt -no FSTYPE,OPTIONS $M/src): $(btrfs_feat $W/src.img)" >>$L/info
echo "srcboot (/boot-like) $(findmnt -no FSTYPE $D/boot): $(feats $W/srcboot.img)" >>$L/info
[ "$(btrfs_feat $W/src.img)" = "$(btrfs_feat $ROOTDEV)" ] && echo "src btrfs has the root's features" >>$L/info || echo "NOTE src features differ from root's: $(btrfs_feat $ROOTDEV)" >>$L/info
BOOTDEV=$(findmnt -no SOURCE /boot)
[ "$(feats $W/srcboot.img)" = "$(feats $BOOTDEV)" ] && echo "srcboot ext4 has /boot's features" >>$L/info || echo "NOTE srcboot features differ from /boot's: $(feats $BOOTDEV)" >>$L/info
# torture.sh, but with 8 MiB of random bytes instead of 1 GiB
sed 's|head -c 1G /dev/urandom|head -c 8M /dev/urandom|' /root/torture.sh >$W/torture.sh
bash $W/torture.sh $D 2>$L/torture.err
bash $W/torture.sh $D/boot 2>>$L/torture.err
T=$D/root/bko-torture
for T in $D/root/bko-torture $D/boot/root/bko-torture; do
python3 - $T <<'PY'
import os, struct, sys
T = sys.argv[1]
def acl(entries):
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", t, p, i) for t, p, i in entries)
X = 0xffffffff
os.setxattr(f"{T}/acl-file", "system.posix_acl_access",
            acl([(1, 6, X), (2, 7, 1234), (4, 4, X), (8, 4, 5678), (0x10, 7, X), (0x20, 4, X)]))
os.setxattr(f"{T}/acl-dir", "system.posix_acl_access",
            acl([(1, 7, X), (2, 5, 1234), (4, 5, X), (0x10, 5, X), (0x20, 5, X)]))
os.setxattr(f"{T}/acl-dir", "system.posix_acl_default",
            acl([(1, 7, X), (2, 7, 1234), (4, 5, X), (8, 5, 5678), (0x10, 7, X), (0x20, 5, X)]))
f = f"{T}/xattr-file"
for n, v in [("user.backotter", b"hello"), ("user.binary", bytes.fromhex("00ff10fe7f")),
             ("user.large", b"x" * 3000), ("user.empty", b""), ("user.zz-first", b"z"),
             ("trusted.backotter", b"root-only"), ("security.bko", b"sec")]:
    os.setxattr(f, n, v)
for p, n in [("symlink", "trusted.on-symlink"), ("symlink", "security.on-symlink"),
             ("fifo", "trusted.on-fifo"), ("chardev", "trusted.on-dev"), ("sticky-dir", "user.on-dir")]:
    os.setxattr(f"{T}/{p}", n, n.encode(), follow_symlinks=False)
PY
done
T=$D/root/bko-torture
# tumbleweed's btrfs fixture (setfattr/setfacl/setcapraw: bkoshim.py, the
# guest has no attr/acl tools): compression property, c m C, nested
# subvolume, capabilities v2/v3, 15000-byte user xattr, journal and
# random-seed copied with their xattrs, sparse/prealloc/swap files.
install -m 755 /root/bkoshim.py /usr/local/bin/bkoshim
for n in setfattr setfacl setcapraw; do ln -sf bkoshim /usr/local/bin/$n; done
PATH=/usr/local/bin:$PATH bash /root/fixture.sh $D/tw btrfs 2>$L/fixture.err
TW=$D/tw/up1/up2/tree
# The guest's journal directories' ACLs (fixture.sh copies mode/owner/times)
J=$(ls -d /var/log/journal/*/ 2>/dev/null | head -1); J=${J%/}
python3 /root/cpxattr.py /var/log/journal $TW/real/var-log/journal $J $TW/real/var-log/journal/$(basename "$J") 2>>$L/fixture.err
lsattr -R -a /var 2>/dev/null | awk '$1 ~ /C/ {print}' >$L/guest-nocow
# The guest's own files with metadata: set-id programs, hard-linked files,
# files with holes (locale-archive), any with xattrs (the inventory found
# none on / or /boot: no getfattr/getfacl installed, none listed by python).
G=$D/guest; mkdir -p $G
n=0
xf=$(python3 - <<'PY'
import os
out = []
for top in ["/etc", "/usr", "/var", "/boot", "/root", "/opt", "/srv"]:
    for d, ds, fs in os.walk(top):
        if d.startswith("/var/tmp/st1"): ds[:] = []; continue
        for n in fs:
            p = os.path.join(d, n)
            try:
                if os.listxattr(p, follow_symlinks=False): out.append(p)
            except OSError: pass
print(" ".join(out[:10]))
PY
)
echo "guest files with xattrs: ${xf:-none}" >>$L/info
for f in $xf $(find / -xdev -type f -perm /6000 2>/dev/null | head -4) \
         $(find /usr /etc -xdev -type f -links +1 2>/dev/null | head -2) \
         $(find / /boot -xdev -path /var/tmp/st1 -prune -o -type f -size +0 -printf '%b %s %p\n' 2>/dev/null | awk '$1*512 < $2 {print $3}' | grep -v '^/root/' | head -5) \
         $(awk '$1 ~ /C/ {print $2}' $L/guest-nocow | head -6); do
    [ -f "$f" ] && [[ $(lsattr -d "$f" 2>/dev/null) == *C* ]] && { mkdir -p "$G$(dirname "$f")"; touch "$G$f"; chattr +C "$G$f"; echo "nocow: $f" >>$L/info; }
    [ -e "$f" ] || continue
    mkdir -p "$G$(dirname "$f")"
    cp -a --sparse=always "$f" "$G$f" 2>>$L/torture.err && n=$((n+1)) && echo "guest file: $f" >>$L/info
done
echo "guest files copied: $n" >>$L/info
# Owners with no name, big ids, set-id, rdev 0, more inode flags
mkdir -p $D/noname-dir; chown 4321:8765 $D/noname-dir; touch $D/noname-dir/f; chown 54321:65432 $D/noname-dir/f
touch $D/bigid; chown 3000000000:3000000001 $D/bigid
mknod $D/rdev0-char c 0 0; mknod $D/rdev0-block b 0 0
for fl in S A j s u c t T x; do
    echo $fl >$D/flag-$fl; chattr +$fl $D/flag-$fl 2>>$L/torture.err
    [[ $(lsattr -d $D/flag-$fl 2>/dev/null | cut -d' ' -f1) == *"$fl"* ]] && echo "flag $fl kept" >>$L/info || { echo "flag $fl not kept on ext4" >>$L/info; rm -f $D/flag-$fl; }
done
mkdir $D/dirsync-dir; chattr +D $D/dirsync-dir
mkdir $D/topdir; chattr +T $D/topdir 2>>$L/torture.err
echo ai >$D/append-nodump; chattr +a +d $D/append-nodump
mkdir $D/immutable-dir; echo in >$D/immutable-dir/inside; chattr +i $D/immutable-dir
echo ap >$D/append-only2; chattr +a $D/append-only2
mkdir -p $D/empty-deep/a/b/c/d/e/f/g
# Project IDs and casefolding on the feature ext4
F=$D/feat
echo p >$F/projid-file; chattr -p 42 $F/projid-file
mkdir $F/projid-dir; chattr +P -p 43 $F/projid-dir; echo inh >$F/projid-dir/inherits; mkdir $F/projid-dir/sub
mkdir $F/casefold-dir; chattr +F $F/casefold-dir; echo m >$F/casefold-dir/MixedCase; mkdir $F/casefold-dir/SubDir
mkdir $F/casefold-dir/SubDir/nested
mkdir $F/casefold-projid; chattr +F +P -p 44 $F/casefold-projid; echo x >$F/casefold-projid/Both
echo i >$F/immutable-projid; chattr -p 45 $F/immutable-projid; chattr +i $F/immutable-projid
truncate -s 1M $F/sparse-projid; echo e >>$F/sparse-projid; chattr -p 46 $F/sparse-projid
lsattr -p -R $F >>$L/info 2>&1
# The directory above the backed-up path, with metadata of its own
chown 4321:8765 $A; chmod 2751 $A
python3 - $A <<'PY'
import os, struct, sys
X = 0xffffffff
os.setxattr(sys.argv[1], "user.ancestor", b"anc"); os.setxattr(sys.argv[1], "trusted.ancestor", b"t")
os.setxattr(sys.argv[1], "system.posix_acl_access", struct.pack("<I", 2) + b"".join(
    struct.pack("<HHI", t, p, i) for t, p, i in [(1, 7, X), (2, 5, 1234), (4, 5, X), (0x10, 5, X), (0x20, 1, X)]))
PY
touch -h -m -d '2004-05-06 07:08:09.123456789' $A $D $F $D/boot
touch -h -a -d '2005-06-07 08:09:10.987654321' $A $D $F $D/boot
sync
noatime() { for m in $M/src $D/boot $F; do mount -o remount,noatime $m; done; }
strictatime() { for m in $M/src $D/boot $F; do mount -o remount,strictatime $m; done; }
noatime
rec $D $L/src0.json
python3 - $L/src0.json <<'PY' >>$L/info
import json, sys
sys.path.insert(0, "/root"); import fsmeta
d = json.load(open(sys.argv[1]))
print("fixture:", len(d), "entries; coverage", json.dumps(fsmeta.coverage(d), sort_keys=True))
PY
anc_stat() { python3 - "$1" <<'PY'
import os, sys
p = sys.argv[1]; st = os.lstat(p)
x = sorted((n, os.getxattr(p, n, follow_symlinks=False).hex()) for n in os.listxattr(p, follow_symlinks=False))
print(oct(st.st_mode & 0o7777), f"{st.st_uid}:{st.st_gid}", st.st_mtime_ns, x)
PY
}
anc_src=$(anc_stat $A)

# --- backups ----------------------------------------------------------------
# The two snapshots compared below are taken with the source mounted
# noatime (so that neither backup changes what the other sees); then one
# more backup with each tool, mounted strictatime, for the atimes.
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
    s=$(bk $bin $W/repo-$t)
    echo "$t backup exit $s; $(grep -ciE 'warn|error' $L/repo-$t.backup) warning lines" >>$L/info
    eval bs_$t=$s
done
for t in restic rustic; do
    bin=$RS; [ $t = rustic ] && bin=$RU
    rec $D $L/src-before-atime-$t.json
    strictatime; s=$(bk $bin $W/repo-atime-$t); noatime
    echo "$t strictatime backup exit $s" >>$L/info
    rec $D $L/src-after-atime-$t.json
    cmp $L/src-before-atime-$t.json $L/src-after-atime-$t.json $L/atime-$t.json
done
grep -hiE 'warn|error|signal|panic' $L/repo-*.backup | head -20 >$L/backup-msgs
for t in restic rustic; do
    eval s=\$bs_$t
    m=$(grep -iE 'warn|error|signal|panic|floating' $L/repo-$t.backup | grep -v '^\[INFO\]' | head -3 | tr '\n' ' ')
    [ "$s" = 0 ] && [ -z "$m" ] && res PASS "$t-bko runs natively and backs up the fixture" "exit 0, no warnings (owners with no name, big ids included)" ||
        res FAIL "$t-bko runs natively and backs up the fixture" "exit $s: $m"
done
for t in restic rustic; do
    r=$(python3 /root/judge.py same $L/src-before-atime-$t.json $L/src-after-atime-$t.json "atime (symlinks)"); [ $? = 0 ] &&
        res PASS "$t-bko backup leaves source atimes alone (strictatime)" "$r; symlinks' atimes move (readlink), accepted in S1, the snapshot keeps the earlier ones" ||
        res FAIL "$t-bko backup leaves source atimes alone (strictatime)" "$r"
done

# --- same repository format ---------------------------------------------------
rt_nodes $RS $W/repo-restic $D >$L/nodes.restic 2>$L/nodes.err
rt_nodes $RS $W/repo-rustic $D >$L/nodes.rustic 2>>$L/nodes.err
if [ -s $L/nodes.restic ] && rt_node_diff $L/nodes.restic $L/nodes.rustic >$L/nodes.diff 2>&1; then
    res PASS "Same repository format: snapshots node-identical" "$(wc -l <$L/nodes.restic) nodes at and below the backup path"
else
    res FAIL "Same repository format: snapshots node-identical" "$(grep -v '(not a check)' $L/nodes.diff | head -8 | tr '\n' ' ')"
fi
c1=$($RS -r $W/repo-rustic check 2>&1); s1=$?
c2=$($RU --no-progress --no-cache -r $W/repo-restic check 2>&1); s2=$?
echo "$c1" >$L/check-restic-on-rustic; echo "$c2" >$L/check-rustic-on-restic
[ $s1 = 0 ] && [ $s2 = 0 ] && ! grep -qiE '^error|\[ERROR\]' $L/check-r*-on-* &&
    res PASS "Same repository format: each tool checks the other's repository" "restic check and rustic check exit 0" ||
    res FAIL "Same repository format: each tool checks the other's repository" "restic $s1, rustic $s2: $(grep -iE 'error' $L/check-r*-on-* | head -3 | tr '\n' ' ')"
# The ancestor (anc1) and those above it: restic stores their lstat,
# rustic its own 0755 nodes without times (rustic_core BACKOTTER.md, accepted)
rt_nodes $RS $W/repo-restic $A >$L/anc.restic 2>/dev/null; rt_nodes $RS $W/repo-rustic $A >$L/anc.rustic 2>/dev/null
grep -P '^\.\t' $L/anc.restic >$L/anc1.restic; grep -P '^\.\t' $L/anc.rustic >$L/anc1.rustic

# --- restores in four directions --------------------------------------------
restore() {  # TOOL REPO TARGET
    if [[ $1 == *rustic* ]]; then $1 --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $1 -r $2 restore -q latest --target $3; fi
}
mktarget() {  # NAME: fresh targets: root-like ext3, /boot-like ext4 and feature ext4 where they go
    mkroot $1 && mkboot $M/$1$D/boot $1-boot && mkfeat $M/$1$D/feat $1-feat
}
DIRS="restic-restic rustic-rustic restic-rustic rustic-restic"
for d in $DIRS; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    mktarget $d || { res FAIL "restore $d" "can't make the target"; continue; }
    restore $bin $W/repo-$b $M/$d >$L/$d.restore 2>&1; echo "exit $?" >$L/$d.exit
    sync; for m in $M/$d $M/$d$D/boot $M/$d$D/feat; do mount -o remount,noatime $m; done
    rec $M/$d$D $L/$d.json
    cmp $L/src0.json $L/$d.json $L/$d.cmp.json
    anc_stat $M/$d$A >$L/$d.anc
    [ -e $M/$d$T/socket ] || [ -e $M/$d$D/boot/root/bko-torture/socket ] || [ -e $M/$d$D/tw/up1/up2/tree/socket ] && echo "socket restored" >>$L/$d.sock
    btrfs subvolume list -o $M/$d >$L/$d.subvols 2>&1
done

# --- restore again over the restored tree, its immutable/append-only files changed
for d in $DIRS; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    t=$M/$d$D
    mountpoint -q $t/feat || continue
    chattr -i $t/boot/root/bko-torture/immutable; echo changed >$t/boot/root/bko-torture/immutable; chattr +i $t/boot/root/bko-torture/immutable
    chattr -i $t/root/bko-torture/immutable; echo changed >$t/root/bko-torture/immutable; chattr +i $t/root/bko-torture/immutable
    chattr -a $t/root/bko-torture/append-only; echo more >>$t/root/bko-torture/append-only; chattr +a $t/root/bko-torture/append-only
    chattr -i $t/feat/immutable-projid; chmod 0600 $t/feat/immutable-projid; chattr +i $t/feat/immutable-projid
    chattr -i $t/immutable-dir; echo new >$t/immutable-dir/inside; chmod 0700 $t/immutable-dir; chattr +i $t/immutable-dir
    echo changed >$t/root/bko-torture/empty
    X=$t/tw/up1/up2/tree
    [ -e $X/compress-immutable ] && { chattr -i $X/compress-immutable; echo changed >>$X/compress-immutable; chattr +i $X/compress-immutable; }
    [ -e $X/nocow-append ] && { chattr -a $X/nocow-append; echo more >>$X/nocow-append; chattr +a $X/nocow-append; }
    restore $bin $W/repo-$b $M/$d >$L/$d.over 2>&1; echo "exit $?" >$L/$d.over.exit
    sync
    rec $t $L/$d.over.json
    cmp $L/src0.json $L/$d.over.json $L/$d.over.cmp.json
done

# --- one non-root run (uid 4321 backs up its own tree and restores it) -------
mkroot nonroot
N=$M/nonroot; mkdir -p $N/src $N/out; chown 4321:4321 $N $N/src $N/out
# /root is 0700: the user runs copies of the binaries and nracl.py
mkdir -p $W/nrbin; install -m 755 $RS $RU /root/nracl.py $W/nrbin/; chmod 755 $W
NRS=$W/nrbin/restic-bko NRU=$W/nrbin/rustic-bko
su_() { setpriv --reuid=4321 --regid=4321 --clear-groups env HOME=/tmp RESTIC_PASSWORD=x RUSTIC_PASSWORD=x "$@"; }
su_ sh -c "cd $N/src && mkdir -p d/e empty && echo a >d/f && chmod 0640 d/f && ln -s f d/l && ln d/f d/hl && mkfifo p && head -c 100000 /dev/urandom >big && truncate -s 10M sparse && echo z >>sparse && chattr +d big && chmod 2750 d/e && touch -d '2003-03-03 03:03:03.3' d/f && python3 $W/nrbin/nracl.py"
echo "nonroot fixture exit $?" >>$L/info
mount -o remount,noatime $N
rec $N/src $L/nr-src.json
for d in $DIRS; do
    b=${d%-*} r=${d#*-}; bb=$NRS; [ $b = rustic ] && bb=$NRU; rb=$NRS; [ $r = rustic ] && rb=$NRU
    if [ ! -d $N/repo-$b ]; then
        if [ $b = rustic ]; then su_ sh -c "$bb --no-progress --no-cache -r $N/repo-$b init && $bb --no-progress --no-cache -r $N/repo-$b backup --set-atime yes $N/src" >$L/nr-$b.backup 2>&1
        else su_ sh -c "$bb -r $N/repo-$b --no-cache init -q && $bb -r $N/repo-$b --no-cache backup -q --with-atime $N/src" >$L/nr-$b.backup 2>&1; fi
        echo "exit $?" >$L/nr-$b.backup.exit
    fi
    mkdir -p $N/out/$d; chown 4321:4321 $N/out/$d
    if [ $r = rustic ]; then su_ $rb --no-progress --no-cache -r $N/repo-$b restore --numeric-id latest:$N/src $N/out/$d >$L/nr-$d.restore 2>&1
    else su_ $rb -r $N/repo-$b --no-cache restore -q latest:$N/src --target $N/out/$d >$L/nr-$d.restore 2>&1; fi
    echo "exit $?" >$L/nr-$d.exit
    rec $N/out/$d $L/nr-$d.json; cmp $L/nr-src.json $L/nr-$d.json $L/nr-$d.cmp.json
done

# --- judge ----------------------------------------------------------------------
python3 /root/judge.py judge $L "$anc_src"
cat $L/torture.err | sed 's/^/torture: /' >>$L/info

# --- clean up -----------------------------------------------------------------
cd /
for m in $(findmnt -rn -o TARGET | grep "^$M" | sort -r); do
    find $m -xdev \( -type f -o -type d \) -exec chattr -i -a {} + 2>/dev/null; umount $m || umount -l $m
done
rm -rf $W
tar -C /root -czf /root/st1.tgz st1
exit 0
