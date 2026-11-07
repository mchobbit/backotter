#!/bin/bash
# SticTest1 debian13-stack, the part that runs in the guest as root (/root).
# Prints "PASS|FAIL|LIMIT need: detail" lines; details in /root/st1/.
# $1: the spare disks' size in bytes (check.sh gives the VM two of them).
#
# The guest's storage, made afresh on the two spare disks with its build's
# tools and options (make-debian-stack.sh: trixie's defaults): GPT on each,
# with five ESPs (FAT32, 512 MiB: mkfs.vfat -F 32, mounted umask=0077), five
# 1 GiB md RAID1 members and the rest one more; md RAID1 arrays (metadata
# 1.2, --assume-clean, homehost debian13-stack) of the 1 GiB pairs, each an
# ext4 like /boot (mkfs.ext4 defaults, mounted defaults); the big pair's
# array holds LUKS2 (aes-xts-plain64, Argon2id at 256 MiB, as the build;
# opened with discards, as the guest's crypttab says) and in it LVM, VG
# bkst-vg (not the guest's name), with LVs for an ext4 like / (mounted
# errors=remount-ro), an XFS like /srv (mkfs.xfs defaults) and an ext4
# with the features the kernel supports but the guest's disks don't use
# (project, casefold).  One set (ESP on each disk, /boot array, /, /srv,
# feat) per file system tree: the source, then each of the four directions'
# restore target, fresh.
#
# The source tree, backed up as one path (data), nested as the guest's are:
#   $M/src (ext4 /)/anc1/data   torture.sh, tumbleweed's fixture (tw), the
#                               guest's own files (guest), more inode flags
#     data/boot   (ext4 /boot on md)  torture.sh, the guest's /boot/grub and kernel
#                 config, /boot's own mode and times
#     data/boot/efi, data/boot/efi2 (FAT32 ESPs): the guest's ESPs with
#                 their FAT attributes, the FAT fixture under up1/up2/tree
#     data/srv    (XFS /srv on LVM)  torture.sh, the fixture for XFS (tw):
#                 project IDs, extent size hints, nodefrag, filestream; the
#                 guest's /srv's (empty) own mode, owner and times
#     data/feat   (ext4 -O project,casefold on LVM)  project IDs, casefolding
set -u
cd /root
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
RS=/root/restic-bko RU=/root/rustic-bko
W=/var/tmp/st1 M=/mnt/bkosb L=/root/st1
mkdir -p $W $M $L
# st1/ goes back to check.sh however inner.sh ends (the stack's logs explain
# an early stop)
trap 'cd /; tar -C /root -czf /root/st1.tgz st1 2>/dev/null' EXIT
export RT_TMP=$W
. /root/rt-common.sh
res() { echo "$1 $2: $3"; }
VG=bkst-vg

# --- the storage stack ---------------------------------------------------------
SPARE=${1:-27917287424}
mapfile -t SP < <(lsblk -dbnpo NAME,SIZE,TYPE | awk -v s="$SPARE" '$2 == s && $3 == "disk" {print $1}')
{ echo "spare disks: ${SP[*]}"; lsblk -dbnpo NAME,SIZE,TYPE; } >>$L/info
[ ${#SP[@]} = 2 ] || { res FAIL fixture "want 2 spare disks of $SPARE bytes, found ${#SP[@]}"; exit 1; }
part() { [ -b ${1}$2 ] && echo ${1}$2 || echo ${1}p$2; }   # DISK N: its partition N
ESP_TYPE=C12A7328-F81F-11D2-BA4B-00A0C93EC93B RAID_TYPE=A19D880F-05FC-4D3B-A006-743F0F84911E
for d in "${SP[@]}"; do
    { echo "label: gpt"
      for i in 1 2 3 4 5; do echo "size=512MiB, type=$ESP_TYPE, name=\"EFI system partition\""; done
      for i in 1 2 3 4 5; do echo "size=1GiB, type=$RAID_TYPE, name=\"boot\""; done
      echo "type=$RAID_TYPE, name=\"system\""; } | sfdisk -q $d >>$L/stack.log 2>&1 ||
        { res FAIL fixture "can't partition $d"; exit 1; }
done
udevadm settle
A=${SP[0]} B=${SP[1]}
mkarray() {  # NAME PARTN: an md RAID1 like the guest's, of partition PARTN of each spare disk
    mdadm --create /dev/md/bkst-$1 --run --metadata=1.2 --level=1 --raid-devices=2 --assume-clean \
        --homehost=debian13-stack --name=bkst-$1 $(part $A $2) $(part $B $2) >>$L/stack.log 2>&1
}
mkarray sys 11 || { res FAIL fixture "can't make the system array"; exit 1; }
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' >$W/luks-key
cryptsetup luksFormat --batch-mode --type luks2 --pbkdf argon2id --pbkdf-memory 262144 --key-file $W/luks-key \
    /dev/md/bkst-sys >>$L/stack.log 2>&1 &&
    cryptsetup open --allow-discards --key-file $W/luks-key /dev/md/bkst-sys bkst_crypt >>$L/stack.log 2>&1 &&
    pvcreate -q /dev/mapper/bkst_crypt >>$L/stack.log 2>&1 && vgcreate -q $VG /dev/mapper/bkst_crypt >>$L/stack.log 2>&1 ||
    { res FAIL fixture "can't make LUKS2 and LVM on the system array"; exit 1; }
# The guest's layers and the scratch ones, field by field
md_info() { mdadm --detail $1 | grep -E '^ *(Version|Raid Level|Raid Devices|Persistence|Consistency Policy|Intent Bitmap) :' | sed 's/^ *//' | tr -s ' ' | paste -sd';'; }
md_dev() { mdadm --examine $1 | grep -E '^ *(Version|Feature Map|Data Offset|Super Offset|Bad Block Log) :' | sed 's/^ *//; s/ at offset.*//' | tr -s ' ' | paste -sd';'; }
luks_info() { cryptsetup luksDump $1 | grep -E '^\s*(Version|cipher|sector|Key|PBKDF|Memory|AF hash|offset):' | tr -s ' \t' ' ' | paste -sd';'; }
crypt_flags() { cryptsetup status $1 | grep -E '^ *(type|cipher|keysize|sector size|flags):' | tr -s ' ' | paste -sd';'; }
pv_info() { pvs --noheadings -o pv_fmt,pe_start,vg_extent_size $1 | tr -s ' ' | sed 's/^ //'; }
stack_cmp() {  # WHAT GUEST SCRATCH
    if [ "$2" = "$3" ]; then echo "same $1: $2" >>$L/stack; else echo "DIFF $1: guest [$2] scratch [$3]" >>$L/stack; fi
}
: >$L/stack

# --- the file systems ----------------------------------------------------------
# Sets: 1 the source, 2-5 the four directions' targets; ESP N on each spare
# disk (partition N) and /boot array N (partitions N+5).
lv() { lvcreate -q -y -L $2 -n $1 $VG >>$L/stack.log 2>&1 && udevadm settle && echo /dev/$VG/$1; }
mkroot() {  # NAME: an ext4 like the guest's / on an LV, at $M/NAME
    local dev; dev=$(lv $1-root 1536M) && mkfs.ext4 -q -F $dev && mkdir -p $M/$1 && mount -o errors=remount-ro $dev $M/$1 || return 1
    # A restore target's own lost+found, at its top, isn't the snapshot's
    # (whose top holds only mnt): rustic would note an additional entry.
    [ $1 = src ] || rmdir $M/$1/lost+found
}
mkboot() {  # DIR NAME SET: an ext4 like /boot on md RAID1 array SET
    mkarray boot$3 $(($3 + 5)) && mkfs.ext4 -q -F /dev/md/bkst-boot$3 && mkdir -p $1 && mount /dev/md/bkst-boot$3 $1
}
mkesp() {  # DIR DISK SET: a FAT32 ESP like the guest's on partition SET of DISK
    mkfs.vfat -F 32 $(part $2 $3) >/dev/null && mkdir -p $1 && mount -o umask=0077 $(part $2 $3) $1
}
mksrv() {  # DIR NAME: an XFS like /srv on an LV
    local dev; dev=$(lv $2-srv 1024M) && mkfs.xfs -q -f $dev && mkdir -p $1 && mount $dev $1
}
mkfeat() {  # DIR NAME: an ext4 with project quotas and casefolding on an LV
    local dev; dev=$(lv $2-feat 256M) && mkfs.ext4 -q -F -O project,casefold $dev && mkdir -p $1 && mount $dev $1
}
mkset() {  # NAME SET: the root-like ext4 and, at its $D, /boot, its ESPs, /srv, feat
    local r=$D; [ $1 = src ] || r=$M/$1$D
    mkroot $1 && mkdir -p $r && mkboot $r/boot $1 $2 && mkesp $r/boot/efi $A $2 && mkesp $r/boot/efi2 $B $2 &&
        mksrv $r/srv $1 && mkfeat $r/feat $1
}
feats() { dumpe2fs -h $1 2>/dev/null | grep -E '^(Filesystem features|Inode size|Block size|Default mount options|Flex block group size|Default directory hash):' | sed 's/ needs_recovery//; s/ orphan_present//' | tr -s ' ' | paste -sd';'; }
xfs_feat() { xfs_info "$1" | grep -oE '(isize|crc|finobt|sparse|rmapbt|reflink|bigtime|inobtcount|nrext64|exchange|parent|metadir|projid32bit|attr|ftype|zoned|bsize|sectsz)=[0-9]+' | sort | uniq | paste -sd' '; }
fat_feat() { { fsck.fat -n -v $1 2>/dev/null | grep -E 'bytes per (logical sector|cluster)|reserved sectors|FATs,|bytes per FAT|System ID' | sed 's/^ *//'; blkid -p -o value -s VERSION $1; } | paste -sd';'; }
esp_opts() { findmnt -rn -o OPTIONS "$1" | tr , '\n' | grep -vE '^(rw|ro|relatime|noatime|strictatime|nosuid|nodev|noexec)$' | paste -sd,; }
fs_opts() { findmnt -rn -o FSTYPE,OPTIONS "$1" | sed 's/,\?\(relatime\|noatime\|strictatime\)//'; }

rec() { python3 /root/fsmeta.py record "$1" "$2" --skip root/bko-torture/socket --skip boot/root/bko-torture/socket \
            --skip srv/root/bko-torture/socket --skip tw/up1/up2/tree/socket --skip srv/tw/up1/up2/tree/socket; }
cmp() { python3 /root/fsmeta.py compare "$1" "$2" --json "$3" >"$3.txt" 2>&1; }
echo "root: $(findmnt -no SOURCE /) $(feats $(findmnt -no SOURCE /))" >>$L/info
echo "versions: $($RS version) | $($RU --version 2>&1 | head -1)" >>$L/info

# --- the fixture ------------------------------------------------------------
A_=$M/src/anc1 D=$M/src/anc1/data
mkset src 1 || { res FAIL fixture "can't make the source file systems"; exit 1; }
SRCM="$M/src $D/boot $D/boot/efi $D/boot/efi2 $D/srv $D/feat"
for m in $SRCM; do mount -o remount,strictatime $m; done
# The scratch layers and file systems against the guest's own
G0=$(findmnt -no SOURCE /) GB=$(findmnt -no SOURCE /boot) GS=$(findmnt -no SOURCE /srv) GE=$(findmnt -no SOURCE /boot/efi)
stack_cmp "md RAID1 (/boot's md0)" "$(md_info /dev/md0)" "$(md_info /dev/md/bkst-boot1)"
stack_cmp "md RAID1 (md1, under LUKS)" "$(md_info /dev/md1)" "$(md_info /dev/md/bkst-sys)"
stack_cmp "md member superblock" "$(md_dev $(mdadm --detail /dev/md0 | awk '/active sync/ {print $NF; exit}'))" "$(md_dev $(part $A 6))"
stack_cmp "LUKS2 header" "$(luks_info /dev/md1)" "$(luks_info /dev/md/bkst-sys)"
stack_cmp "dm-crypt mapping" "$(crypt_flags md1_crypt | sed 's/md1_crypt//')" "$(crypt_flags bkst_crypt)"
stack_cmp "LVM PV" "$(pv_info /dev/mapper/md1_crypt)" "$(pv_info /dev/mapper/bkst_crypt)"
stack_cmp "LV layout (/'s, /srv's)" "$(lvs --noheadings -o lv_layout,seg_count $G0 $GS | tr -s ' ' | paste -sd';')" \
    "$(lvs --noheadings -o lv_layout,seg_count /dev/$VG/src-root /dev/$VG/src-srv | tr -s ' ' | paste -sd';')"
stack_cmp "GPT partition types (ESP, md member)" "$(lsblk -no PARTTYPE,PARTLABEL $(findmnt -no SOURCE /boot/efi) $(mdadm --detail /dev/md0 | awk '/active sync/ {print $NF; exit}') | tr -s ' ' | paste -sd';')" \
    "$(lsblk -no PARTTYPE,PARTLABEL $(part $A 1) $(part $A 6) | tr -s ' ' | paste -sd';')"
stack_cmp "ext4 / features" "$(feats $G0)" "$(feats /dev/$VG/src-root)"
stack_cmp "ext4 /boot features" "$(feats $GB)" "$(feats /dev/md/bkst-boot1)"
stack_cmp "XFS /srv features" "$(xfs_feat /srv)" "$(xfs_feat $D/srv)"
stack_cmp "FAT32 ESP" "$(fat_feat $GE)" "$(fat_feat $(part $A 1))"
stack_cmp "mount options /" "$(fs_opts /)" "$(fs_opts $M/src)"
stack_cmp "mount options /boot" "$(fs_opts /boot)" "$(fs_opts $D/boot)"
stack_cmp "mount options /srv" "$(fs_opts /srv)" "$(fs_opts $D/srv)"
stack_cmp "mount options /boot/efi" "$(esp_opts /boot/efi)" "$(esp_opts $D/boot/efi)"
stack_cmp "mount options /boot/efi2" "$(esp_opts /boot/efi2)" "$(esp_opts $D/boot/efi2)"
# The guest's /'s and /boot's mkfs features without what a full one keeps on
# (orphan_present, needs_recovery: both stripped), and mount options with
# strictatime/noatime aside (this test switches them).
if ! grep -q '^DIFF' $L/stack; then
    res PASS "Scratch storage made like the guest's (GPT, md RAID1, LUKS2, LVM; ext4 / and /boot, XFS /srv, FAT32 ESPs; mount options)" \
        "$(grep -c '^same' $L/stack) layers and file systems the same: $(cut -d: -f1 $L/stack | sed 's/^same //' | paste -sd';' | sed 's/;/; /g')"
else
    res FAIL "Scratch storage made like the guest's (GPT, md RAID1, LUKS2, LVM; ext4 / and /boot, XFS /srv, FAT32 ESPs; mount options)" "$(grep '^DIFF' $L/stack | head -4 | tr '\n' ' ')"
fi
# torture.sh, but with 8 MiB of random bytes instead of 1 GiB
sed 's|head -c 1G /dev/urandom|head -c 8M /dev/urandom|' /root/torture.sh >$W/torture.sh
install -m 755 /root/bkoshim.py /usr/local/bin/bkoshim
for n in setfattr setfacl setcapraw; do ln -sf bkoshim /usr/local/bin/$n; done
for t in $D $D/boot $D/srv; do
    PATH=/usr/local/bin:$PATH bash $W/torture.sh $t 2>>$L/torture.err
python3 - $t/root/bko-torture <<'PY'
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
# tumbleweed's fixture (setfattr/setfacl/setcapraw: bkoshim.py, the guest
# has no attr/acl tools) on the /-like ext4 and the /srv-like XFS:
# capabilities v2/v3, big xattrs, journal and random-seed copied with their
# xattrs, sparse/prealloc/swap files; on XFS project IDs and its fsxattr
# options.  The FAT fixture on both ESPs, with copies of the guest's.
PATH=/usr/local/bin:$PATH bash /root/fixture.sh $D/tw ext4 2>$L/fixture.err
PATH=/usr/local/bin:$PATH bash /root/fixture.sh $D/srv/tw xfs 2>>$L/fixture.err
bash /root/fixture.sh $D/boot/efi vfat /boot/efi 2>>$L/fixture.err
bash /root/fixture.sh $D/boot/efi2 vfat /boot/efi2 2>>$L/fixture.err
TW=$D/tw/up1/up2/tree
# The guest's journal directories' ACLs (fixture.sh copies mode/owner/times)
J=$(ls -d /var/log/journal/*/ 2>/dev/null | head -1); J=${J%/}
python3 /root/cpxattr.py /var/log/journal $TW/real/var-log/journal $J $TW/real/var-log/journal/$(basename "$J") 2>>$L/fixture.err
# The /srv-like XFS's root takes the guest's /srv's mode, owner and times
chown --reference=/srv $D/srv; chmod --reference=/srv $D/srv
# The guest's own files with metadata: set-id programs, any with holes (the
# trixie guest has none; the journal is copied sparse), any with xattrs, a
# hard-linked pair (below), and the storage
# stack's configuration (crypttab, mdadm.conf, lvm.conf, the VG's backup).
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
         $(find / /boot -xdev -path /var/tmp/st1 -prune -o -type f -size +0 -printf '%b %s %p\n' 2>/dev/null | awk '$1*512 < $2 {print $3}' | grep -v '^/root/' | head -5) \
         /etc/crypttab /etc/mdadm/mdadm.conf /etc/lvm/lvm.conf /etc/lvm/backup/debian13-stack-vg /etc/fstab \
         /boot/grub/grubenv $(ls /boot/config-* 2>/dev/null | head -1); do
    [ -e "$f" ] || continue
    mkdir -p "$G$(dirname "$f")"
    cp -a --sparse=always "$f" "$G$f" 2>>$L/torture.err && n=$((n+1)) && echo "guest file: $f" >>$L/info
done
# A hard-linked pair (all of one inode's names), in one cp so it stays linked
hl=$(find /usr /etc -xdev -type f -links +1 -printf '%i %p\n' 2>/dev/null | sort -n | awk '$1 == i {print p; print $2; exit} {i = $1; p = $2}')
[ -n "$hl" ] && cp -a --parents $hl $G 2>>$L/torture.err && n=$((n + $(wc -w <<<"$hl"))) && echo "guest hard-linked: $(echo $hl)" >>$L/info
echo "guest files copied: $n" >>$L/info
# /boot's own files on the /boot-like ext4, where they live: GRUB's
# directory (modules, fonts, locale, grubenv, grub.cfg) and the kernel's config
cp -a /boot/grub $D/boot/grub 2>>$L/torture.err && cp -a $(ls /boot/config-* | head -1) $D/boot/ 2>>$L/torture.err &&
    echo "guest /boot files: $(find $D/boot/grub -type f | wc -l) in grub, $(ls $D/boot | grep config-)" >>$L/info
touch --reference=/boot $D/boot
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
chown 4321:8765 $A_; chmod 2751 $A_
python3 - $A_ <<'PY'
import os, struct, sys
X = 0xffffffff
os.setxattr(sys.argv[1], "user.ancestor", b"anc"); os.setxattr(sys.argv[1], "trusted.ancestor", b"t")
os.setxattr(sys.argv[1], "system.posix_acl_access", struct.pack("<I", 2) + b"".join(
    struct.pack("<HHI", t, p, i) for t, p, i in [(1, 7, X), (2, 5, 1234), (4, 5, X), (0x10, 5, X), (0x20, 1, X)]))
PY
touch -h -m -d '2004-05-06 07:08:09.123456789' $A_ $D $F
touch -h -a -d '2005-06-07 08:09:10.987654321' $A_ $D $F
touch --reference=/boot $D/boot
touch --reference=/srv $D/srv
sync
noatime() { for m in $SRCM; do mount -o remount,noatime $m; done; }
strictatime() { for m in $SRCM; do mount -o remount,strictatime $m; done; }
noatime
rec $D $L/src0.json
# FAT attributes and XFS's own per-file options, which fsmeta doesn't record
fatrec() { for e in efi efi2; do python3 /root/fatattr.py $1/boot/$e $2.$e.fat.json; done; }
XP="srv/tw/up1/up2/tree"
xfsx() { for p in xfs-extsize xfs-extsize-dir xfs-nodefrag xfs-filestream xfs-cowextsize xfs-cowextsize-dir xfs-nosymlinks; do printf '%s ' $p; xfs_io -r -c stat "$1/$XP/$p" 2>&1 | grep -E 'fsxattr\.(xflags|extsize|cowextsize)' | tr -s ' \n' '  '; echo; done >$2; }
fatrec $D $L/src0; xfsx $D $L/src0.xfsx
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
anc_src=$(anc_stat $A_)

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
    res PASS "Same repository format: snapshots node-identical" "$(wc -l <$L/nodes.restic) nodes at and below the backup path (ext4, md ext4, FAT32, XFS, ext4 project/casefold)"
else
    res FAIL "Same repository format: snapshots node-identical" "$(grep -v '(not a check)' $L/nodes.diff | head -8 | tr '\n' ' ')"
fi
c1=$($RS -r $W/repo-rustic check 2>&1); s1=$?
c2=$($RU --no-progress --no-cache -r $W/repo-restic check 2>&1); s2=$?
echo "$c1" >$L/check-restic-on-rustic; echo "$c2" >$L/check-rustic-on-restic
[ $s1 = 0 ] && [ $s2 = 0 ] && ! grep -qiE '^error|\[ERROR\]' $L/check-r*-on-* &&
    res PASS "Same repository format: each tool checks the other's repository" "restic check and rustic check exit 0" ||
    res FAIL "Same repository format: each tool checks the other's repository" "restic $s1, rustic $s2: $(grep -iE 'error' $L/check-r*-on-* | head -3 | tr '\n' ' ')"
# The ancestor (anc1) and those above it (/mnt, /mnt/bkosb, the source's
# root), as each snapshot holds them: one tree read per level
rt_path_nodes $RS $W/repo-restic $A_ >$L/anc1.restic 2>$L/anc1.err; rt_path_nodes $RS $W/repo-rustic $A_ >$L/anc1.rustic 2>>$L/anc1.err

# --- restores in four directions --------------------------------------------
restore() {  # TOOL REPO TARGET
    if [[ $1 == *rustic* ]]; then $1 --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $1 -r $2 restore -q latest --target $3; fi
}
DIRS="restic-restic rustic-rustic restic-rustic rustic-restic"
set_=2
for d in $DIRS; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    mkset $d $set_ || { res FAIL "restore $d" "can't make the target"; set_=$((set_ + 1)); continue; }
    set_=$((set_ + 1))
    t=$M/$d$D
    restore $bin $W/repo-$b $M/$d >$L/$d.restore 2>&1; echo "exit $?" >$L/$d.exit
    sync; for m in $M/$d $t/boot $t/boot/efi $t/boot/efi2 $t/srv $t/feat; do mount -o remount,noatime $m; done
    rec $t $L/$d.json
    cmp $L/src0.json $L/$d.json $L/$d.cmp.json
    fatrec $t $L/$d; xfsx $t $L/$d.xfsx
    anc_stat $M/$d$A_ >$L/$d.anc
    for s in root/bko-torture boot/root/bko-torture srv/root/bko-torture tw/up1/up2/tree srv/tw/up1/up2/tree; do
        [ -e $t/$s/socket ] && echo "socket restored: $s" >>$L/$d.sock
    done
done

# --- restore again over the restored tree, its immutable/append-only files changed
for d in $DIRS; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    t=$M/$d$D
    mountpoint -q $t/feat || continue
    for x in $t $t/boot $t/srv; do
        chattr -i $x/root/bko-torture/immutable; echo changed >$x/root/bko-torture/immutable; chattr +i $x/root/bko-torture/immutable
    done
    chattr -a $t/root/bko-torture/append-only; echo more >>$t/root/bko-torture/append-only; chattr +a $t/root/bko-torture/append-only
    chattr -a $t/srv/root/bko-torture/append-only; echo more >>$t/srv/root/bko-torture/append-only; chattr +a $t/srv/root/bko-torture/append-only
    chattr -i $t/feat/immutable-projid; chmod 0600 $t/feat/immutable-projid; chattr +i $t/feat/immutable-projid
    chattr -i $t/immutable-dir; echo new >$t/immutable-dir/inside; chmod 0700 $t/immutable-dir; chattr +i $t/immutable-dir
    X=$t/srv/tw/up1/up2/tree
    chattr -i $X/immutable-dir; echo x >$X/immutable-dir/f; chattr +i $X/immutable-dir
    echo changed >$t/root/bko-torture/empty
    echo changed >$t/boot/efi/up1/up2/tree/archive-set
    # A FAT attribute changed (hidden cleared) on an unchanged file
    python3 -c 'import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, struct.pack("I", 0x20))
print("before restore over:", sys.argv[1], hex(struct.unpack("I", fcntl.ioctl(fd, 0x80047210, b"\0" * 4))[0])); os.close(fd)' \
        $t/boot/efi/up1/up2/tree/hidden >>$L/info 2>>$L/torture.err
    restore $bin $W/repo-$b $M/$d >$L/$d.over 2>&1; echo "exit $?" >$L/$d.over.exit
    sync
    rec $t $L/$d.over.json
    cmp $L/src0.json $L/$d.over.json $L/$d.over.cmp.json
    fatrec $t $L/$d.over; xfsx $t $L/$d.over.xfsx
done

# --- one non-root run (uid 4321 backs up its own tree and restores it) -------
N=$M/nonroot
dev=$(lv nonroot 1024M) && mkfs.ext4 -q -F $dev && mkdir -p $N && mount -o errors=remount-ro $dev $N
mkdir -p $N/src $N/out; chown 4321:4321 $N $N/src $N/out
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
df -h $W >>$L/info; vgs $VG >>$L/info 2>&1; lvs $VG >>$L/info 2>&1

# --- clean up -----------------------------------------------------------------
cd /
for m in $(findmnt -rn -o TARGET | grep "^$M" | sort -r); do
    find $m -xdev \( -type f -o -type d \) -exec chattr -i -a {} + 2>/dev/null; umount $m || umount -l $m
done
vgchange -q -a n $VG >/dev/null 2>&1; cryptsetup close bkst_crypt 2>/dev/null
for a in /dev/md/bkst-*; do mdadm --stop $a >/dev/null 2>&1; done
rm -rf $W
exit 0
