#!/bin/bash
# In the pikaos4 or pikaos4-luks throwaway guest, as root, from
# /root/bk: the spare disk (vdb) laid out as the guest's own disk, read
# from the guest: per tree one partition for each of the guest's (GPT, the
# installer's names, types and attributes: linux-efi ESP and linux-boot
# XBOOTLDR of the guest's sizes, linux-root), each file system made and
# mounted like the guest's: a / like the guest's (the tree), an ext4 like
# /boot mounted inside it at tree/boot, a FAT32 ESP like /boot/efi mounted
# inside that at tree/boot/efi; one partition more for the non-root run.
# pikaos4's / is XFS; pikaos4-luks's is LUKS2 (made with the guest's
# header parameters, as gentoo-systemd's) holding a Btrfs whose @ is the
# tree and whose @home is mounted at tree/home, as the guest mounts them.
# The fixture (fixture.sh) on all three, four directions into the same
# stack made fresh, each tool restoring over its own restore with changed
# immutable/append-only files and FAT attributes, a backup pair on
# relatime, the non-root run on a file system like /, and judge.py.
#
# Usage: inner.sh GUEST
set -u
G=${1:?guest}
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
DISK=/dev/vdb P=/mnt/src/up1/up2/tree
mkfs.xfs -q -f /dev/vdc && mkdir -p $W && mount -o noatime /dev/vdc $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L
st() { echo "$1 $2" >>$L/status; }
echo "$G" >$L/guest
# PikaOS has no attr package: bkoshim.py is setfattr (fixture.sh and
# nonroot-inner.sh use it).  setfacl, setcap and setpriv are the guest's.
mkdir -p /usr/local/bin
command -v setfattr >/dev/null || { install -m 755 $B/bkoshim.py /usr/local/bin/bkoshim; ln -sf bkoshim /usr/local/bin/setfattr; echo "setfattr: bkoshim.py" >$L/shims; }
command -v setpriv >/dev/null || echo "setpriv: missing" >>$L/shims
export PATH=/usr/local/bin:$PATH

# --- What the guest has ------------------------------------------------------
# Its partitions in disk order, each with its role: esp (the vfat mount),
# boot (/boot), root (/, or LUKS2 under /), home (/home), other.
ESPM=$(findmnt -rn -t vfat -o TARGET | head -1) BOOTM=/boot
rootsrc=$(findmnt -n -o SOURCE / | sed 's/\[.*//') ROOTFS=$(findmnt -n -o FSTYPE /)
espdev=$(findmnt -n -o SOURCE "$ESPM") bootdev=$(findmnt -n -o SOURCE "$BOOTM")
# The partition under LUKS under / (lsblk -s: the device, then its parents).
CRYPTPART=$(lsblk -rsn -o NAME,TYPE "$rootsrc" | awk '$2 == "crypt" {c = 1; next} c && $2 == "part" {print "/dev/" $1; exit}')
rootpart=${CRYPTPART:-$rootsrc}
python3 $B/gpt.py /dev/vda >$L/gpt-root || { echo "FAIL harness: no GPT on the guest's disk"; exit 2; }
declare -A ROLE
for d in /sys/block/vda/vda*; do
    n=$(cat $d/partition) dev=/dev/${d##*/}
    case $dev in "$espdev") ROLE[$n]=esp ;; "$bootdev") ROLE[$n]=boot ;; "$rootpart") ROLE[$n]=root ;; *)
        [[ $(findmnt -n -o SOURCE /home 2>/dev/null) == "$dev" ]] && ROLE[$n]=home || ROLE[$n]=other ;; esac
done
ROLES=$(tail -n +2 $L/gpt-root | while read -r n _; do echo "${ROLE[$n]:-other}"; done | paste -sd,)
# On a Btrfs /: the subvolume mounted at / (ROOTSUB, /@) and the others of
# the same file system mounted elsewhere (SUBS: TARGET:FSROOT, /home:/@home),
# each made in the scratch Btrfs and mounted there inside the tree.
ROOTSUB=- SUBS=""
if [[ $ROOTFS == btrfs ]]; then
    ROOTSUB=$(findmnt -n -o FSROOT /)
    SUBS=$(findmnt -rn -t btrfs -o TARGET,FSROOT,SOURCE | awk -v d="$rootsrc" '$1 != "/" && $3 ~ "^" d "(\\[|$)" {print $1 ":" $2}' | sort -u | paste -sd' ')
fi
export ESPM BOOTM
{ echo "ESPM=$ESPM BOOTM=$BOOTM ROOTFS=$ROOTFS CRYPTPART=${CRYPTPART:--} ROLES=$ROLES ROOTSUB=$ROOTSUB SUBS=${SUBS// /,}"; uname -r; . /etc/os-release; echo "$PRETTY_NAME"
  dpkg-query -W -f '${Package}=${Version} ' xfsprogs e2fsprogs dosfstools btrfs-progs cryptsetup-bin util-linux acl libcap2-bin booster refind kernel-pika 2>/dev/null; echo; } >$L/env.txt
[[ $ROOTFS == xfs || $ROOTFS == btrfs ]] || { echo "FAIL harness: / is $ROOTFS: only an XFS / (pikaos4) or a Btrfs / (pikaos4-luks) is built"; exit 2; }
[[ $ROOTFS == btrfs ]] && btrfs subvolume list / >$L/subvols-root 2>&1 && btrfs subvolume get-default / >>$L/subvols-root 2>&1
extfeat() { tune2fs -l "$1" | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | tr '\n' ' '; echo; }
extgeo() { tune2fs -l "$1" | grep -E '^(Inode size|Block size|Default mount options|Flex block group size|Default directory hash|Filesystem flags):' | tr -s ' ' | paste -sd';'; }
xfsfeat() { xfs_info "$1" | grep -o '\(isize\|crc\|finobt\|sparse\|rmapbt\|reflink\|bigtime\|inobtcount\|nrext64\|exchange\|metadir\|parent\|ftype\|projid32bit\|attr\|lazy-count\|ascii-ci\|bsize\|sectsz\)=[0-9]*' | sort -u | tr '\n' ' '; echo; }
fatgeom() {  # DEV: "FAT32 bps=512 spc=8 nfats=2 reserved=32"
    python3 - "$1" <<'PY'
import struct, sys
b = open(sys.argv[1], "rb").read(512)
bps, spc, res, nf = struct.unpack_from("<HBHB", b, 11)
fsz16 = struct.unpack_from("<H", b, 22)[0]
t = "FAT32" if fsz16 == 0 else (b[54:62].decode("ascii", "replace").strip() or "FAT16")
print(f"{t} bps={bps} spc={spc} nfats={nf} reserved={res}")
PY
}
# (A Btrfs subvolume's id, subvolid=, is left out: it is the order the
# installer made them in, not a property of the mount.)
mopts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(relatime|noatime|strictatime|atime|subvolid=.*)$' | paste -sd,; }
btrfsfeat() {  # DEV: the superblock's feature flags, checksum, node and sector size
    btrfs inspect-internal dump-super "$1" 2>/dev/null | grep -E '^(incompat_flags|compat_ro_flags|csum_type|nodesize|sectorsize)' | tr -s ' \t' ' ' | sed 's/ $//' | tr '\n' ';'; echo
}
rootfeat() { if [[ $ROOTFS == xfs ]]; then xfsfeat "$1"; else btrfsfeat "$2"; fi; }   # MOUNTPOINT DEV
atimeopt() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -E '^(relatime|noatime|strictatime)$' | head -1; }
luksinfo() {  # PART: LUKS2's header, as luksFormat's options make it
    cryptsetup luksDump "$1" | awk '
        /^Version:/ {print "version " $2}
        /^Data segments:/ {s = "data"} /^Keyslots:/ {s = "key"} /^Tokens:/ {s = ""}
        s == "data" && /^\toffset:/ {print "data-offset " $2}
        s == "data" && /^\tcipher:/ {print "data-cipher " $2}
        s == "data" && /^\tsector:/ {print "sector " $2}
        s == "key" && /^\tKey:/ {print "key-bits " $2}
        s == "key" && /^\tPBKDF:/ {print "pbkdf " $2}
        s == "key" && /^\tTime cost:/ {print "time " $3}
        s == "key" && /^\tMemory:/ {print "memory " $2}
        s == "key" && /^\tThreads:/ {print "threads " $2}' | sort -u | paste -sd' '
}
rootfeat / "$rootsrc" >$L/feat-root; mopts / >$L/mopts-root; atimeopt / >$L/atime-root
for sv in $SUBS; do mopts ${sv%%:*} >$L/mopts-root-$(basename ${sv%%:*}); done
extfeat "$bootdev" >$L/feat-boot; extgeo "$bootdev" >$L/geo-boot; mopts "$BOOTM" >$L/mopts-boot; atimeopt "$BOOTM" >$L/atime-boot
fatgeom "$espdev" >$L/fat-esp; mopts "$ESPM" >$L/mopts-esp; atimeopt "$ESPM" >$L/atime-esp
[[ -n $CRYPTPART ]] && luksinfo "$CRYPTPART" >$L/luks-root
# mkfs as the guest's were made: XFS's -m/-i/-n options from /, or
# Btrfs's node and sector size and checksum from its superblock; ext4's
# features, inode and block size from /boot; FAT's type, sector and
# cluster size from the ESP.  Each mounted with the guest's own options.
BSZ=$(tune2fs -l "$bootdev" | sed -n 's/^Block size: *//p') ISZ=$(tune2fs -l "$bootdev" | sed -n 's/^Inode size: *//p')
EXTO=$(tr ' ' '\n' <$L/feat-boot | grep . | grep -v '^has_journal$' | paste -sd,)
xv() { tr ' ' '\n' <$L/feat-root | sed -n "s/^$1=//p"; }
XFSO=(-m "crc=$(xv crc),finobt=$(xv finobt),rmapbt=$(xv rmapbt),reflink=$(xv reflink),bigtime=$(xv bigtime),inobtcount=$(xv inobtcount),metadir=$(xv metadir)"
      -i "sparse=$(xv sparse),nrext64=$(xv nrext64),exchange=$(xv exchange)" -n "parent=$(xv parent)")
read -r FATT FATS FATC <<<"$(sed 's/^FAT\([0-9]*\) bps=\([0-9]*\) spc=\([0-9]*\).*/\1 \2 \3/' $L/fat-esp)"
fullopts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(rw|ro|subvolid=.*|subvol=.*)$' | paste -sd,; }
bv() { btrfs inspect-internal dump-super "$rootsrc" | awk -v k="$1" '$1 == k {print $2}'; }
[[ $ROOTFS == btrfs ]] && BTRO=(-n "$(bv nodesize)" -s "$(bv sectorsize)" --csum "$(btrfs inspect-internal dump-super "$rootsrc" | sed -n 's/^csum_type.*(\(.*\))/\1/p')") || BTRO=()
ROPTS=$(fullopts /) BOPTS=$(fullopts "$BOOTM") EOPTS=$(fullopts "$ESPM")
mk_ext4() { mkfs.ext4 -q -F -b "$BSZ" -I "$ISZ" -O "$EXTO" "$1"; }
mk_xfs() { mkfs.xfs -q -f "${XFSO[@]}" "$1" 2>>$L/mkfs-xfs.log || { echo "mkfs.xfs ${XFSO[*]} refused: defaults" >>$L/mkfs-xfs.log; mkfs.xfs -q -f "$1"; }; }
mk_vfat() { mkfs.vfat -F "$FATT" -S "$FATS" -s "$FATC" -n EFI "$1" >/dev/null; }
mk_btrfs() { mkfs.btrfs -q -f "${BTRO[@]}" "$1" >/dev/null 2>>$L/mkfs-btrfs.log || { echo "mkfs.btrfs ${BTRO[*]} refused: defaults" >>$L/mkfs-btrfs.log; mkfs.btrfs -q -f "$1" >/dev/null; }; }
echo "mkfs.xfs ${XFSO[*]}; mkfs.btrfs ${BTRO[*]}; mkfs.ext4 -b $BSZ -I $ISZ -O $EXTO; mkfs.vfat -F $FATT -S $FATS -s $FATC; mounts / $ROPTS, /boot $BOPTS, ESP $EOPTS" >>$L/env.txt

# --- The scratch stack -------------------------------------------------------
# vdb as the guest's vda, once per set: each of its partitions in its
# order, type, name and attributes; the ESP and /boot of the guest's
# sizes, / (and anything else) smaller.  Then one partition like / for the
# non-root run.
SETS="src rr uu ru ur"
NP=$(tail -n +2 $L/gpt-root | wc -l)
declare -A PART   # SET:ROLE -> /dev/vdbN
sgdisk -Z $DISK >$L/sgdisk.log 2>&1
k=0 next=2048
mkpart() {  # SECTORS TYPE ATTRS NAME: the next partition, 1 MiB aligned
    local sz=$1 ty=$2 at=$3 nm=$4 bit a=()
    for ((bit = 0; bit < 64; bit++)); do (( (at >> bit) & 1 )) && a+=(-A "$((k + 1)):set:$bit"); done
    k=$((k + 1))
    sgdisk -n "$k:$next:$((next + sz - 1))" -t "$k:$ty" -c "$k:$nm" "${a[@]}" $DISK >>$L/sgdisk.log 2>&1 || return 1
    next=$(( (next + sz + 2047) / 2048 * 2048 ))
}
ok=1
for s in $SETS; do
    while read -r n _ size ty at nm; do
        r=${ROLE[$n]:-other}
        case $r in esp | boot) ;; root) size=$((1536 * 2048)) ;; *) size=$((64 * 2048)) ;; esac
        mkpart $size $ty $at $nm || ok=0
        PART[$s:$r]=$DISK$k
    done < <(tail -n +2 $L/gpt-root)
done
read -r _ _ _ rty rat rnm < <(awk -v p="$(cat /sys/class/block/${rootpart#/dev/}/partition)" 'NR > 1 && $1 == p' $L/gpt-root)
mkpart $((1536 * 2048)) $rty $rat $rnm || ok=0; PART[nr:root]=$DISK$k
partx -u $DISK >>$L/sgdisk.log 2>&1; udevadm settle
((ok)) && [[ -b ${PART[nr:root]} ]] || { echo "FAIL harness: no partitions on $DISK: $(tail -3 $L/sgdisk.log)"; exit 2; }
python3 $B/gpt.py $DISK >$L/gpt-scratch
declare -A RDEV   # SET -> the root file system's device (a LUKS2 mapping when the guest has one)
for s in $SETS nr; do
    RDEV[$s]=${PART[$s:root]}
    if [[ -n $CRYPTPART ]]; then
        # LUKS2 made with the guest's header parameters, opened with a key file.
        read -r -a LA <<<"$(cryptsetup luksDump "$CRYPTPART" | awk '
            /^Data segments:/ {s = "data"} /^Keyslots:/ {s = "key"} /^Tokens:/ {s = ""}
            s == "data" && /^\tcipher:/ && !c {c = $2} s == "data" && /^\tsector:/ && !z {z = $2}
            s == "key" && /^\tKey:/ && !k {k = $2} s == "key" && /^\tPBKDF:/ && !p {p = $2}
            s == "key" && /^\tTime cost:/ && !t {t = $3} s == "key" && /^\tMemory:/ && !m {m = $2} s == "key" && /^\tThreads:/ && !h {h = $2}
            END {print c, z, k, p, t, m, h}')"
        [[ -f $W/luks-key ]] || head -c 64 /dev/urandom >$W/luks-key
        cryptsetup luksFormat -q --type luks2 --cipher "${LA[0]}" --sector-size "${LA[1]}" --key-size "${LA[2]}" --pbkdf "${LA[3]}" \
            --pbkdf-force-iterations "${LA[4]}" --pbkdf-memory "${LA[5]}" --pbkdf-parallel "${LA[6]}" --key-file $W/luks-key ${PART[$s:root]} >>$L/sgdisk.log 2>&1 &&
            cryptsetup open --allow-discards --key-file $W/luks-key ${PART[$s:root]} bkst_$s >>$L/sgdisk.log 2>&1 ||
            { echo "FAIL harness: no LUKS2 on ${PART[$s:root]}: $(tail -3 $L/sgdisk.log)"; exit 2; }
        [[ $s == src ]] && luksinfo ${PART[$s:root]} >$L/luks-scratch
        RDEV[$s]=/dev/mapper/bkst_$s
    fi
done
mkroot() {  # SET ROOT: the set's / like the guest's at ROOT (Btrfs: its subvolumes made in the guest's order, ROOTSUB mounted)
    if [[ $ROOTFS == xfs ]]; then
        mk_xfs ${RDEV[$1]} >>$L/mkfs.log 2>&1 && mkdir -p $2 && mount -t xfs -o "$ROPTS" ${RDEV[$1]} $2
        return
    fi
    local t=/mnt/bktop-$1 sv
    mk_btrfs ${RDEV[$1]} >>$L/mkfs.log 2>&1 && mkdir -p $t && mount -t btrfs ${RDEV[$1]} $t || return 1
    # The mounted subvolumes in the guest's id order (btrfs subvolume list).
    for sv in $(awk '{print $2, $NF}' $L/subvols-root | grep '^[0-9]' | sort -n | cut -d' ' -f2); do
        [[ /$sv == "$ROOTSUB" || " $SUBS " == *":/$sv "* ]] && { btrfs -q subvolume create $t/$sv >>$L/mkfs.log 2>&1 || return 1; }
    done
    umount $t && rmdir $t && mkdir -p $2 && mount -t btrfs -o "$ROPTS,subvol=${ROOTSUB#/}" ${RDEV[$1]} $2
}
mksubs() {  # SET TREE: the other subvolumes mounted inside the tree as the guest mounts them
    local sv m
    for sv in $SUBS; do
        m=${sv%%:*}
        mkdir -p $2$m && mount -t btrfs -o "$(fullopts $m),subvol=${sv#*:/}" ${RDEV[$1]} $2$m || return 1
    done
}
mkset() {  # SET ROOT: the set's /-like file system at ROOT, its /boot-like ext4 at ROOT$P/boot (src: $P/boot), its ESP inside that, its other subvolumes
    local r=$2$P
    [[ $1 == src ]] && r=$P
    mkroot $1 $2 || return 1
    mkdir -p $r && mksubs $1 $r || return 1
    mkdir -p $r$BOOTM && mk_ext4 ${PART[$1:boot]} >>$L/mkfs.log 2>&1 && mount -t ext4 -o "$BOPTS" ${PART[$1:boot]} $r$BOOTM || return 1
    mkdir -p $r$ESPM && mk_vfat ${PART[$1:esp]} >>$L/mkfs.log 2>&1 && mount -t vfat -o "$EOPTS" ${PART[$1:esp]} $r$ESPM
}
atimes() {  # OPTION DIR: remount DIR and everything mounted below it OPTION (noatime, relatime)
    # relatime alone never clears a noatime already set (libmount merges the
    # mount's current options, and the kernel's MNT_NOATIME wins): atime
    # clears it first.
    local o=$1; [[ $1 == relatime ]] && o=atime,relatime
    findmnt -rn -o TARGET -R "$2" | while read -r m; do mount -o remount,$o "$m" || echo "remount $o $m failed" >>$L/remount.err; done
}
mopts_all() { findmnt -rn -o TARGET,OPTIONS -R "$1"; }  # DIR: each mount at and below DIR, its options
# XFS's own per-file options (fsxattr xflags, extent size hints) of the
# main tree's xfs-* paths; on Btrfs, its compression property and flags.
xfsx() {  # TREE OUT
    [[ $ROOTFS == btrfs ]] && { btrfsx "$@"; return; }
    for p in xfs-extsize xfs-extsize-dir xfs-extsize-dir/inherits xfs-nodefrag xfs-filestream; do
        printf '%s ' $p; xfs_io -r -c stat "$1/$p" 2>&1 | grep -E 'fsxattr\.(xflags|extsize|cowextsize)' | tr -s ' \n' '  '; echo
    done >$2
}
btrfsx() {  # TREE OUT
    local f x
    for f in compress-zstd compress-lzo compress-zlib compress-zstd-level compress-dir compress-dir/inherited \
             compress-flag nocompress nocow nocow-dir nocow-dir/inherited nocow-sparse; do
        x=$1/$f
        printf '%s %s %s\n' "$f" "$(lsattr -d $x 2>/dev/null | cut -d' ' -f1)" \
            "$(python3 -c 'import os,sys
try: print(os.getxattr(sys.argv[1], "btrfs.compression").decode())
except OSError as e: print("-")' $x)"
    done >$2
}
fatx() { python3 $B/fatattr.py "$1$ESPM" "$2"; }   # TREE OUT: the ESP's FAT attributes
mkset src /mnt/src || { echo "FAIL harness: no source file systems: $(tail -3 $L/mkfs.log)"; exit 2; }
rootfeat /mnt/src ${RDEV[src]} >$L/feat-src; mopts /mnt/src >$L/mopts-src
for sv in $SUBS; do mopts $P${sv%%:*} >$L/mopts-src-$(basename ${sv%%:*}); done
extfeat ${PART[src:boot]} >$L/feat-srcboot; extgeo ${PART[src:boot]} >$L/geo-srcboot; mopts $P$BOOTM >$L/mopts-srcboot
fatgeom ${PART[src:esp]} >$L/fat-src; mopts $P$ESPM >$L/mopts-srcesp
bash $B/fixture.sh /mnt/src $ROOTFS main 2>$L/fixture.err
for sv in $SUBS; do bash $B/fixture.sh $P${sv%%:*} btrfs home 2>>$L/fixture.err; done
bash $B/fixture.sh $P$BOOTM ext4 boot 2>>$L/fixture.err
bash $B/fixture.sh $P$ESPM vfat 2>>$L/fixture.err
sync; atimes noatime /mnt/src
python3 $B/fsmeta.py record /mnt/src $L/src.json
xfsx $P $L/src.xfsx; fatx $P $L/src.fat.json
# The file systems (and subvolumes) inside the tree, by key: boot, esp, and each SUBS's name (home).
INSIDE="boot:$BOOTM esp:$ESPM"; for sv in $SUBS; do INSIDE="$INSIDE $(basename ${sv%%:*}):${sv%%:*}"; done
for i in $INSIDE; do find $P${i#*:} -xdev | wc -l >$L/count-src-${i%%:*}; done
( cd /; time rt_backup $RESTIC $W/repo-restic $P ) >$L/backup-restic.log 2>&1; st backup-restic $?
( cd /; time rt_backup $RUSTIC $W/repo-rustic $P ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
python3 $B/fsmeta.py record /mnt/src $L/src-after.json
python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
rt_nodes $RESTIC $W/repo-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
rt_nodes $RESTIC $W/repo-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
# Each tool checks the other's repository (all of its data).
$RESTIC -r $W/repo-rustic check --read-data >$L/check-restic-on-rustic.log 2>&1; st check-restic-on-rustic $?
$RUSTIC --no-progress --no-cache -r $W/repo-restic check --read-data >$L/check-rustic-on-restic.log 2>&1; st check-rustic-on-restic $?
for t in restic rustic; do  # the directories above, as each snapshot holds them
    $RESTIC -r $W/repo-$t ls --json latest 2>/dev/null | grep -E '"path":"/(mnt|mnt/src|mnt/src/up1|mnt/src/up1/up2)"' >$L/above-$t.json
done
# Two more backups with the source on relatime (the guest's /, /boot and
# ESP all mount relatime; the compared ones ran on noatime, so that both
# tools saw the same atimes): they mustn't move the source's atimes.
atimes relatime /mnt/src; mopts_all /mnt/src >$L/mopts-rel-src
for t in restic rustic; do
    [[ $t == restic ]] && b=$RESTIC || b=$RUSTIC
    ( cd /; rt_backup $b $W/repo-rel-$t $P ) >$L/backup-rel-$t.log 2>&1; st backup-rel-$t $?
done
sync; atimes noatime /mnt/src
python3 $B/fsmeta.py record /mnt/src $L/src-rel.json
python3 $B/fsmeta.py compare $L/src.json $L/src-rel.json --json $L/src-rel.cmp.json >$L/src-rel.cmp.txt
rm -rf $W/repo-rel-restic $W/repo-rel-rustic
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
# Each target: a fresh stack (/'s file system and subvolumes, /boot's ext4, its ESP), mounted
# where the source's are (as BackOtter mounts a machine's file systems
# before it restores), as the guest mounts them.
for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
    IFS=: read n b r <<<"$d"
    mkset $n /mnt/d$n || { echo "FAIL harness: no target file systems for $n: $(tail -3 $L/mkfs.log)"; continue; }
    restore $r $W/repo-$b /mnt/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime /mnt/d$n
    python3 $B/fsmeta.py record /mnt/d$n/mnt/src $L/$n.json
    xfsx /mnt/d$n$P $L/$n.xfsx; fatx /mnt/d$n$P $L/$n.fat.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    for i in $INSIDE; do
        x=/mnt/d$n$P${i#*:}
        find $x -xdev | wc -l >$L/count-$n-${i%%:*}; findmnt -n -o SOURCE --target $x/. >$L/dev-$n-${i%%:*}
    done
done
# Restore over existing immutable and append-only files (and an immutable
# directory) on /, /boot (and /home), and changed FAT attributes and a changed
# read-only file on the ESP: each tool over its own restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=/mnt/drr || D=/mnt/duu
    : >$L/$n.before
    for X in $D$P $D$P$BOOTM $(for sv in $SUBS; do echo $D$P${sv%%:*}; done); do
        chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
            echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
            echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
        lsattr -d $X/immutable $X/append-only $X/immutable-dir >>$L/$n.before 2>&1
    done
    E=$D$P$ESPM
    python3 - $E <<'PY' >>$L/$n.before 2>&1
import fcntl, os, struct, sys
E = sys.argv[1]
def setfat(p, v):
    fd = os.open(os.path.join(E, p), os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, struct.pack("I", v)); os.close(fd)
setfat("readonly-attr", 0x20)
open(os.path.join(E, "readonly-attr"), "w").write("changed-contents\n")
setfat("readonly-attr", 0x21)
for p, v in (("hidden", 0x20), ("archive-cleared", 0x20), ("system-dir", 0x10), ("readonly-dir", 0x10)):
    setfat(p, v)
print("FAT attributes changed before the restore over: readonly-attr (contents), hidden, archive-cleared, system-dir, readonly-dir")
PY
    atimes relatime $D; mopts_all $D >$L/mopts-rel-$n
    restore $t $W/repo-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime $D
    python3 $B/fsmeta.py record $D/mnt/src $L/$n.json
    xfsx $D$P $L/$n.xfsx; fatx $D$P $L/$n.fat.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
done
# One non-root run (nonroot-inner.sh: backup as root, restores as uid
# 4321) on a file system made and mounted like / (Btrfs: its root
# subvolume made and mounted as /'s), then remounted noatime (as
# rocky10-lvm's): nonroot-inner.sh compares atimes, which relatime moves
# when the backups read the files.
{ mkroot nr /mnt/nr && mopts /mnt/nr >$L/mopts-nr &&
    mount -o remount,noatime /mnt/nr; } ||
    echo "FAIL harness: no file system for the non-root run"
# uid 4321 can't reach /root (0700): the binaries go where it can.
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
lsblk -o NAME,TYPE,FSTYPE,SIZE,PARTLABEL,PARTTYPE,MOUNTPOINTS $DISK >$L/lsblk.txt 2>&1
df -m /mnt/src $P$BOOTM $P$ESPM /mnt/d* /mnt/nr $W >$L/df.txt 2>&1
python3 $B/judge.py $L
# The logs for the host.
tar -C /mnt/bkw/logs -cf /root/bk/logs.tar .
