#!/bin/bash
# In the vanilla3 throwaway guest, as root, from /root/bk: the spare disk
# (vdb) laid out as the guest's own disk, read from the guest, once per
# tree: GPT partitions with the guest's names and types (vos-boot ext4 and
# vos-efi FAT32 of the guest's geometry, smaller; PVs vos-root and
# vos-var), VG vos-root-SET with a linear `init` LV (ext4 like vos-init)
# and a thin pool `root` made as Albius makes it (a data LV and a
# metadata LV joined by lvconvert: the guest's chunk size, discards and
# zeroing, lvol0_pmspare) holding thin LVs root-a and root-b, each as big
# as the pool (over-provisioned as the guest's), btrfs like vos-a/vos-b;
# VG vos-var-SET with `var`, btrfs like vos-var.  Labels are the guest's
# with -SET added (two disks with ABRoot's labels confuse ABRoot and
# lsblk).  Mounted at ABRoot's own points as bko-holt's dump lists them:
# root-b (the present root) holds the tree, root-a is tree/part-future,
# var tree/var, boot tree/boot, the ESP tree/boot/efi, init tree/boot/init.
# The fixture (fixture.sh) on all six; four directions into the same
# stack made fresh, each tool restoring over its own restore (with the
# overlay upper's opaque, redirect and a whiteout changed too), a backup
# pair on relatime; each tree's /etc overlays (vos-b over tree/etc, vos-a
# over tree/part-future/etc) and its containers layer stack mounted
# read-only and their merged views recorded, then vos-b mounted read-write
# as mount-sys mounts it; the non-root run on a thin btrfs; judge.py.
#
# Usage: inner.sh GUEST
set -u
G=${1:?guest}
# Vanilla's /mnt is a symlink (to var/mnt, as each root's home, opt, srv
# and root): rustic-bko keeps a backed-up path as it resolves it, restic-bko
# as it is given, so everything goes in the real directory, M.
M=$(realpath /mnt)
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=$M/bkw L=$M/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
DISK=/dev/vdb P=$M/src/up1/up2/tree
mkfs.ext4 -q -F /dev/vdc && mkdir -p $W && mount -o noatime /dev/vdc $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L
trap 'tar -C $L -cf /root/bk/logs.tar .' EXIT   # (the logs for the host, also after a harness FAIL)
st() { echo "$1 $2" >>$L/status; }
echo "$G" >$L/guest
# Vanilla's image has no attr package: bkoshim.py is setfattr (fixture.sh
# and nonroot-inner.sh use it).  setfacl, setcap and setpriv are the guest's.
mkdir -p /usr/local/bin
command -v setfattr >/dev/null || { install -m 755 $B/bkoshim.py /usr/local/bin/bkoshim; ln -sf bkoshim /usr/local/bin/setfattr; echo "setfattr: bkoshim.py" >$L/shims; }
export PATH=/usr/local/bin:$PATH

# --- What the guest has ------------------------------------------------------
ESPM=/boot/efi BOOTM=/boot
espdev=$(findmnt -n -o SOURCE $ESPM) bootdev=$(findmnt -n -o SOURCE $BOOTM)
presdev=$(findmnt -n -o SOURCE / | sed 's/\[.*//') vardev=$(findmnt -n -o SOURCE /var | sed 's/\[.*//')
read -r GVR presLV < <(lvs --noheadings -o vg_name,lv_name "$presdev" | xargs)
read -r GVV varLV < <(lvs --noheadings -o vg_name,lv_name "$vardev" | xargs)
POOL=$(lvs --noheadings -o pool_lv "$presdev" | xargs)
otherLV=$(lvs --noheadings -o lv_name,pool_lv "$GVR" | awk -v p="$POOL" -v me="$presLV" '$2 == p && $1 != me {print $1}' | head -1)
initdev=/dev/$GVR/init
PRESL=$(blkid -s LABEL -o value "$presdev") OTHERL=$(blkid -s LABEL -o value "/dev/$GVR/$otherLV")
lab() { blkid -s LABEL -o value "$1"; }
python3 $B/gpt.py /dev/vda >$L/gpt-root || { echo "FAIL harness: no GPT on the guest's disk"; exit 2; }
declare -A ROLE
rootpv=$(pvs --noheadings -o pv_name -S vg_name="$GVR" | xargs) varpv=$(pvs --noheadings -o pv_name -S vg_name="$GVV" | xargs)
for d in /sys/block/vda/vda*; do
    n=$(cat $d/partition) dev=/dev/${d##*/}
    case $dev in "$espdev") ROLE[$n]=esp ;; "$bootdev") ROLE[$n]=boot ;; "$rootpv") ROLE[$n]=rootpv ;; "$varpv") ROLE[$n]=varpv ;; *) ROLE[$n]=other ;; esac
done
ROLES=$(tail -n +2 $L/gpt-root | while read -r n _; do echo "${ROLE[$n]:-other}"; done | paste -sd,)
export ESPM BOOTM
{ echo "ESPM=$ESPM BOOTM=$BOOTM GVR=$GVR GVV=$GVV POOL=$POOL PRES=$presLV OTHER=$otherLV PRESL=$PRESL OTHERL=$OTHERL ROLES=$ROLES"; uname -r; . /etc/os-release; echo "$PRETTY_NAME"
  python3 - <<'PY'
import re
s = open("/var/lib/dpkg/status", errors="replace").read()
want = {"btrfs-progs", "e2fsprogs", "dosfstools", "lvm2", "thin-provisioning-tools", "parted", "util-linux", "acl", "libcap2-bin", "abroot"}
print(" ".join(f"{p}={v}" for p, v in re.findall(r"^Package: (\S+)\n(?:.*\n)*?Version: (\S+)", s, re.M) if p in want))
PY
} >$L/env.txt
extfeat() { tune2fs -l "$1" | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | tr '\n' ' '; echo; }
extgeo() { tune2fs -l "$1" | grep -E '^(Inode size|Block size|Default mount options|Flex block group size|Default directory hash|Filesystem flags):' | tr -s ' ' | paste -sd';'; }
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
mopts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(relatime|noatime|strictatime|atime|subvolid=.*|ro|rw)$' | paste -sd,; }
fullopts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(rw|ro|subvolid=.*|subvol=.*)$' | paste -sd,; }
btrfsfeat() {  # DEV: the superblock's feature flags, checksum, node and sector size
    btrfs inspect-internal dump-super "$1" 2>/dev/null | grep -E '^(incompat_flags|compat_ro_flags|csum_type|nodesize|sectorsize)' | tr -s ' \t' ' ' | sed 's/ $//' | tr '\n' ';'; echo
}
atimeopt() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -E '^(relatime|noatime|strictatime)$' | head -1; }
lvm_of() {  # VG: PVs' format, data offset, metadata areas; extent size, copies; each LV's attributes (but open, active), segment, sizes; the pool's chunk, discards, zeroing; over-provisioning
    local vg=$1
    pvs --noheadings --units b -o pv_fmt,pe_start,pv_mda_count,pv_mda_size -S vg_name="$vg" | xargs
    vgs --noheadings --units b -o vg_extent_size,vg_mda_copies,vg_attr "$vg" | xargs
    lvs -a --noheadings --units m -o lv_name,lv_attr,segtype,stripes,chunk_size,discards,zero,pool_lv "$vg" |
        awk '{a = $2; $2 = substr(a, 1, 4) "--" substr(a, 7); print}' | sed 's/\[//; s/\]//' | sort
    lvs --noheadings --units b --nosuffix -o lv_name,segtype,lv_size "$vg" | awk '
        $2 == "thin-pool" {pool = $3} $2 == "thin" {virt += $3; n++}
        END {if (pool) printf "thin LVs %d, virtual/pool %.2f\n", n, virt / pool}'
}
sizes() {  # VG: each LV's size, MiB (the guest's, then the scratch's, are in the log; not compared)
    lvs -a --noheadings --units m -o lv_name,lv_size,lv_metadata_size "$1" | xargs -L1
}
mkdir -p $M/guest-init $M/guest-other
mount -o ro,noload "$initdev" $M/guest-init 2>$L/guest-mounts.err; st mount-guest-init $?
mount -o ro,rescue=nologreplay "/dev/$GVR/$otherLV" $M/guest-other 2>>$L/guest-mounts.err; st mount-guest-other $?
export INITM=$M/guest-init FUTM=$M/guest-other
btrfsfeat "$presdev" >$L/feat-root; mopts / >$L/mopts-root; atimeopt / >$L/atime-root
btrfsfeat "/dev/$GVR/$otherLV" >$L/feat-other
btrfsfeat "$vardev" >$L/feat-var; mopts /var >$L/mopts-var; atimeopt /var >$L/atime-var
extfeat "$bootdev" >$L/feat-boot; extgeo "$bootdev" >$L/geo-boot; mopts $BOOTM >$L/mopts-boot; atimeopt $BOOTM >$L/atime-boot
extfeat "$initdev" >$L/feat-init; extgeo "$initdev" >$L/geo-init
fatgeom "$espdev" >$L/fat-esp; mopts $ESPM >$L/mopts-esp; atimeopt $ESPM >$L/atime-esp
lvm_of "$GVR" >$L/lvm-root 2>&1; lvm_of "$GVV" >$L/lvm-var 2>&1; { sizes "$GVR"; sizes "$GVV"; } >$L/lvsizes-guest
findmnt -n -o OPTIONS /etc >$L/etc-overlay-opts
for p in /sys/module/overlay/parameters/*; do echo "${p##*/}=$(cat $p)"; done >$L/overlay-params
# mkfs as the guest's were made: btrfs's node and sector size and checksum
# from its superblock; ext4's features, inode and block size (/boot's and
# init's); FAT's type, sector and cluster size from the ESP.  Each mounted
# with the guest's own options (/'s without ro; init isn't mounted on the
# running system: ext4's defaults).
bv() { btrfs inspect-internal dump-super "$presdev" | awk -v k="$1" '$1 == k {print $2}'; }
BTRO=(-n "$(bv nodesize)" -s "$(bv sectorsize)" --csum "$(btrfs inspect-internal dump-super "$presdev" | sed -n 's/^csum_type.*(\(.*\))/\1/p')")
extopts() {  # DEV: mkfs.ext4's -b -I -O for it
    echo "-b $(tune2fs -l "$1" | sed -n 's/^Block size: *//p') -I $(tune2fs -l "$1" | sed -n 's/^Inode size: *//p') -O $(extfeat "$1" | tr ' ' '\n' | grep . | grep -v '^has_journal$' | paste -sd,)"
}
EXTB=$(extopts "$bootdev") EXTI=$(extopts "$initdev")
read -r FATT FATS FATC <<<"$(sed 's/^FAT\([0-9]*\) bps=\([0-9]*\) spc=\([0-9]*\).*/\1 \2 \3/' $L/fat-esp)"
ROPTS=$(fullopts /) VOPTS=$(fullopts /var) BOPTS=$(fullopts $BOOTM) EOPTS=$(fullopts $ESPM) IOPTS=defaults
EXTSZ=$(vgs --noheadings --units k --nosuffix -o vg_extent_size "$GVR" | xargs | cut -d. -f1)
read -r CHUNK DISC ZERO < <(lvs --noheadings --units k --nosuffix -o chunk_size,discards,zero "$GVR/$POOL" | xargs | awk '{sub(/\..*/, "", $1); print $1, $2, ($3 == "zero" ? "y" : "n")}')
PMSPARE=$(lvs -a --noheadings -o lv_name "$GVR" | grep -c 'pmspare')
mk_btrfs() { mkfs.btrfs -q -f "${BTRO[@]}" -L "$2" "$1" >/dev/null 2>>$L/mkfs-btrfs.log || { echo "mkfs.btrfs ${BTRO[*]} refused: defaults" >>$L/mkfs-btrfs.log; mkfs.btrfs -q -f -L "$2" "$1" >/dev/null; }; }
mk_ext4() { mkfs.ext4 -q -F $3 -L "$2" "$1"; }   # DEV LABEL OPTS
mk_vfat() { mkfs.vfat -F "$FATT" -S "$FATS" -s "$FATC" -n "$2" "$1" >/dev/null 2>>$L/mkfs-vfat.log; }
echo "mkfs.btrfs ${BTRO[*]}; mkfs.ext4 boot $EXTB, init $EXTI; mkfs.vfat -F $FATT -S $FATS -s $FATC; pool chunk ${CHUNK}k discards $DISC zero $ZERO pmspare $PMSPARE extent ${EXTSZ}k; mounts / $ROPTS, /var $VOPTS, /boot $BOPTS, ESP $EOPTS, init $IOPTS" >>$L/env.txt

# --- The scratch stack -------------------------------------------------------
# vdb as the guest's vda, once per set: each of its partitions in its
# order, type and name; the ESP of the guest's size, /boot 256 MiB, the
# PVs 1600 and 704 MiB.  Then one PV like vos-root for the non-root run.
SETS="src rr uu ru ur"
declare -A PART   # SET:ROLE -> /dev/vdbN
parted -s $DISK mklabel gpt >$L/parted.log 2>&1
k=0 next=2048 ok=1
mkpart() {  # SECTORS TYPE NAME: the next partition, 1 MiB aligned
    local sz=$1 ty=$2 nm=$3
    k=$((k + 1))
    parted -s -a none $DISK unit s mkpart "$nm" "$next" "$((next + sz - 1))" >>$L/parted.log 2>&1 &&
        parted -s $DISK type $k "$ty" >>$L/parted.log 2>&1 || return 1
    next=$(( (next + sz + 2047) / 2048 * 2048 ))
}
for s in $SETS; do
    while read -r n _ size ty _ nm; do
        r=${ROLE[$n]:-other}
        case $r in esp) ;; boot) size=$((256 * 2048)) ;; rootpv) size=$((1600 * 2048)) ;; varpv) size=$((704 * 2048)) ;; *) size=$((16 * 2048)) ;; esac
        mkpart $size $ty $nm || ok=0
        PART[$s:$r]=$DISK$k
    done < <(tail -n +2 $L/gpt-root)
done
read -r _ _ _ rty _ rnm < <(awk -v p="$(cat /sys/class/block/${rootpv#/dev/}/partition)" 'NR > 1 && $1 == p' $L/gpt-root)
mkpart $((1600 * 2048)) $rty $rnm || ok=0; PART[nr:rootpv]=$DISK$k
partx -u $DISK >>$L/parted.log 2>&1; udevadm settle
((ok)) && [[ -b ${PART[nr:rootpv]} ]] || { echo "FAIL harness: no partitions on $DISK: $(tail -3 $L/parted.log)"; exit 2; }
python3 $B/gpt.py $DISK >$L/gpt-scratch
# The guest's udev doesn't make the nodes of an LV that lvcreate and
# lvconvert activate only to wipe it (the pool's metadata: "not found:
# device not cleared"); verify_udev_operations has LVM make them itself.
lvcreate() { command lvcreate --config 'activation{verify_udev_operations=1}' "$@"; }
lvconvert() { command lvconvert --config 'activation{verify_udev_operations=1}' "$@"; }
mkvgs() {  # SET: its two volume groups, the root one as Albius makes it (nr: the root group only, one thin LV)
    local s=$1 vr=$GVR-$1 vv=$GVV-$1
    {
        pvcreate -q -y ${PART[$s:rootpv]} && vgcreate -q -s ${EXTSZ}k $vr ${PART[$s:rootpv]} &&
            { [[ $s == nr ]] || lvcreate -q -y -L 192M -n init $vr; } &&
            lvcreate -q -y -L 1280M -n $POOL $vr && lvcreate -q -y -L 32M -n $POOL-meta $vr && udevadm settle &&
            lvconvert -q -y --type thin-pool --poolmetadata $vr/$POOL-meta --chunksize ${CHUNK}k --discards $DISC --zero $ZERO \
                --poolmetadataspare $([[ $PMSPARE -gt 0 ]] && echo y || echo n) $vr/$POOL &&
            lvcreate -q -y -V 1280M -T $vr/$POOL -n $presLV &&
            { [[ $s == nr ]] || lvcreate -q -y -V 1280M -T $vr/$POOL -n $otherLV; } || return 1
        [[ $s == nr ]] && return 0
        pvcreate -q -y ${PART[$s:varpv]} && vgcreate -q -s ${EXTSZ}k $vv ${PART[$s:varpv]} && lvcreate -q -y -l 100%FREE -n $varLV $vv
    } >>$L/lvcreate.log 2>&1
}
lvdev() { echo "/dev/$1/$2"; }   # VG LV
mkset() {  # SET ROOT: the set's file systems, made, at ROOT (the present root) and inside ROOT$P (src: $P)
    local s=$1 r=$2$P vr=$GVR-$1 vv=$GVV-$1
    [[ $s == src ]] && r=$P
    mkvgs $s && udevadm settle || return 1
    mk_btrfs $(lvdev $vr $presLV) $PRESL-$s && mkdir -p $2 && mount -t btrfs -o "$ROPTS" $(lvdev $vr $presLV) $2 || return 1
    mkdir -p $r/var $r$BOOTM $r/part-future || return 1
    mk_btrfs $(lvdev $vr $otherLV) $OTHERL-$s && mount -t btrfs -o "$ROPTS" $(lvdev $vr $otherLV) $r/part-future || return 1
    mk_btrfs $(lvdev $vv $varLV) $(lab "$vardev")-$s && mount -t btrfs -o "$VOPTS" $(lvdev $vv $varLV) $r/var || return 1
    mk_ext4 ${PART[$s:boot]} $(lab "$bootdev")-$s "$EXTB" >>$L/mkfs.log 2>&1 && mount -t ext4 -o "$BOPTS" ${PART[$s:boot]} $r$BOOTM || return 1
    mkdir -p $r$ESPM $r$BOOTM/init || return 1
    mk_vfat ${PART[$s:esp]} $(lab "$espdev")-$s && mount -t vfat -o "$EOPTS" ${PART[$s:esp]} $r$ESPM || return 1
    mk_ext4 $(lvdev $vr init) $(lab "$initdev")-$s "$EXTI" >>$L/mkfs.log 2>&1 && mount -t ext4 -o "$IOPTS" $(lvdev $vr init) $r$BOOTM/init
}
labels_of() {  # SET: its file systems' types, labels and UUIDs, as made
    local d
    for d in $(lvdev $GVR-$1 $presLV) $(lvdev $GVR-$1 $otherLV) $(lvdev $GVV-$1 $varLV) $(lvdev $GVR-$1 init) ${PART[$1:boot]} ${PART[$1:esp]}; do
        echo "$1 $d $(blkid -s TYPE -o value $d) $(blkid -s LABEL -o value $d) $(blkid -s UUID -o value $d)"
    done >>$L/labels-made
}
atimes() {  # OPTION DIR: remount DIR and everything mounted below it OPTION (noatime, relatime)
    local o=$1; [[ $1 == relatime ]] && o=atime,relatime
    findmnt -rn -o TARGET -R "$2" | while read -r m; do mount -o remount,$o "$m" || echo "remount $o $m failed" >>$L/remount.err; done
}
mopts_all() { findmnt -rn -o TARGET,OPTIONS -R "$1"; }
btrfsx() {  # TREE OUT: btrfs's per-file options of the present root's fixture
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
fatx() { python3 $B/fatattr.py "$1$ESPM" "$2"; }   # TREE OUT
# The file systems inside the tree, by key.
INSIDE="var:/var future:/part-future boot:$BOOTM esp:$ESPM init:$BOOTM/init"
STOR=var/lib/abroot/storage/graph/overlay
CL1=$STOR/1111111111111111111111111111111111111111111111111111111111111111/diff CL2=$STOR/2222222222222222222222222222222222222222222222222222222222222222/diff
merged() {  # NAME TREE: each overlay of TREE mounted read-only (the upper as the top lower) and recorded: vos-b, vos-a, the containers stack
    local n=$1 t=$2 m=$M/bkmv x low up
    mkdir -p $m
    for x in b a c; do
        case $x in
        b) up=$t/var/lib/abroot/etc/$PRESL low=$t/etc ;;
        a) up=$t/var/lib/abroot/etc/$OTHERL low=$t/part-future/etc ;;
        c) up=$t/$CL2 low=$t/$CL1 ;;
        esac
        # (Relative to the tree: mount(8)'s fsconfig takes 256 bytes a value.)
        if (cd $t && mount -t overlay overlay -o "ro,noatime,lowerdir=${up#$t/}:${low#$t/},redirect_dir=follow" $m) 2>>$L/merged-$n.err; then
            python3 $B/fsmeta.py record $m $L/merged-$n-$x.json 2>>$L/merged-$n.err
            umount $m
        else echo "mount $x failed" >>$L/merged-$n.err; fi
    done
    rmdir $m
}
mergedrw() {  # NAME TREE: vos-b mounted read-write as mount-sys mounts /etc (lowerdir, upperdir, workdir), recorded
    local n=$1 t=$2 m=$M/bkmv
    mkdir -p $m
    if (cd $t && mount -t overlay overlay -o "noatime,lowerdir=etc,upperdir=var/lib/abroot/etc/$PRESL,workdir=var/lib/abroot/etc/$PRESL-work" $m) 2>>$L/mergedrw-$n.err; then
        python3 $B/fsmeta.py record $m $L/mergedrw-$n-b.json 2>>$L/mergedrw-$n.err
        # A write, a delete (a new whiteout) and a read through it.
        { echo "written through the overlay" >$m/bko-written.conf && rm $m/keep.conf && cat $m/modified.conf >$L/mergedrw-$n-modified.txt &&
            [[ -c $t/var/lib/abroot/etc/$PRESL/keep.conf && -f $t/var/lib/abroot/etc/$PRESL/bko-written.conf ]] && echo ok >$L/mergedrw-$n-write; } 2>>$L/mergedrw-$n.err
        umount $m || echo "umount failed" >>$L/mergedrw-$n.err
    else echo "mount failed" >>$L/mergedrw-$n.err; fi
    rmdir $m
    dmesg | grep -i overlay | tail -5 >>$L/mergedrw-$n.dmesg
}
mkset src $M/src || { echo "FAIL harness: no source file systems: $(tail -3 $L/lvcreate.log) $(tail -3 $L/mkfs.log)"; exit 2; }
lvm_of $GVR-src >$L/lvm-srcroot 2>&1; lvm_of $GVV-src >$L/lvm-srcvar 2>&1; { sizes $GVR-src; sizes $GVV-src; } >$L/lvsizes-src
btrfsfeat $(lvdev $GVR-src $presLV) >$L/feat-src; btrfsfeat $(lvdev $GVR-src $otherLV) >$L/feat-srcother; btrfsfeat $(lvdev $GVV-src $varLV) >$L/feat-srcvar
mopts $M/src >$L/mopts-src; mopts $P/var >$L/mopts-srcvar; mopts $P/part-future >$L/mopts-srcfuture
extfeat ${PART[src:boot]} >$L/feat-srcboot; extgeo ${PART[src:boot]} >$L/geo-srcboot; mopts $P$BOOTM >$L/mopts-srcboot
extfeat $(lvdev $GVR-src init) >$L/feat-srcinit; extgeo $(lvdev $GVR-src init) >$L/geo-srcinit
fatgeom ${PART[src:esp]} >$L/fat-src; mopts $P$ESPM >$L/mopts-srcesp
labels_of src
for d in "$presdev" "/dev/$GVR/$otherLV" "$vardev" "$initdev" "$bootdev" "$espdev"; do
    echo "guest $d $(blkid -s TYPE -o value $d) $(blkid -s LABEL -o value $d) $(blkid -s UUID -o value $d)"
done >$L/labels-guest
export FIXLOG=$L OVL_LOWER_B=$P/etc OVL_LOWER_A=$P/part-future/etc
bash $B/fixture.sh $M/src btrfs main 2>$L/fixture.err
bash $B/fixture.sh $P/part-future btrfs future 2>>$L/fixture.err
bash $B/fixture.sh $P/var btrfs var 2>>$L/fixture.err
bash $B/fixture.sh $P$BOOTM ext4 boot 2>>$L/fixture.err
bash $B/fixture.sh $P$BOOTM/init ext4 init 2>>$L/fixture.err
bash $B/fixture.sh $P$ESPM vfat 2>>$L/fixture.err
umount $M/guest-init $M/guest-other 2>>$L/guest-mounts.err
sync; atimes noatime $M/src
python3 $B/fsmeta.py record $M/src $L/src.json
btrfsx $P $L/src.xfsx; fatx $P $L/src.fat.json
merged src $P
for i in $INSIDE; do find $P${i#*:} -xdev | wc -l >$L/count-src-${i%%:*}; done
( cd /; time rt_backup $RESTIC $W/repo-restic $P ) >$L/backup-restic.log 2>&1; st backup-restic $?
( cd /; time rt_backup $RUSTIC $W/repo-rustic $P ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
python3 $B/fsmeta.py record $M/src $L/src-after.json
python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
rt_nodes $RESTIC $W/repo-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
rt_nodes $RESTIC $W/repo-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
$RESTIC -r $W/repo-rustic check --read-data >$L/check-restic-on-rustic.log 2>&1; st check-restic-on-rustic $?
$RUSTIC --no-progress --no-cache -r $W/repo-restic check --read-data >$L/check-rustic-on-restic.log 2>&1; st check-rustic-on-restic $?
for t in restic rustic; do  # the directories above, as each snapshot holds them
    $RESTIC -r $W/repo-$t ls --json latest 2>/dev/null | grep -E "\"path\":\"($M|$M/src|$M/src/up1|$M/src/up1/up2)\"" >$L/above-$t.json
done
# Two more backups with the source on relatime (the guest's / and /var
# mount relatime): they mustn't move the source's atimes.
atimes relatime $M/src; mopts_all $M/src >$L/mopts-rel-src
for t in restic rustic; do
    [[ $t == restic ]] && b=$RESTIC || b=$RUSTIC
    ( cd /; rt_backup $b $W/repo-rel-$t $P ) >$L/backup-rel-$t.log 2>&1; st backup-rel-$t $?
done
sync; atimes noatime $M/src
python3 $B/fsmeta.py record $M/src $L/src-rel.json
python3 $B/fsmeta.py compare $L/src.json $L/src-rel.json --json $L/src-rel.cmp.json >$L/src-rel.cmp.txt
rm -rf $W/repo-rel-restic $W/repo-rel-rustic
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
    IFS=: read n b r <<<"$d"
    mkset $n $M/d$n || { echo "FAIL harness: no target file systems for $n: $(tail -3 $L/lvcreate.log) $(tail -3 $L/mkfs.log)"; continue; }
    labels_of $n
    restore $r $W/repo-$b $M/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime $M/d$n
    python3 $B/fsmeta.py record $M/d$n$M/src $L/$n.json
    btrfsx $M/d$n$P $L/$n.xfsx; fatx $M/d$n$P $L/$n.fat.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    merged $n $M/d$n$P
    for i in $INSIDE; do
        x=$M/d$n$P${i#*:}
        find $x -xdev | wc -l >$L/count-$n-${i%%:*}; findmnt -n -o SOURCE --target $x/. >$L/dev-$n-${i%%:*}
    done
    findmnt -n -o SOURCE --target $M/d$n$P/. >$L/dev-$n-root
done
# Restore over existing immutable and append-only files (and an immutable
# directory) on each file system but the ESP, changed FAT attributes and a
# changed read-only file on the ESP, and in /var's /etc upper an opaque
# directory made plain, a redirect pointing elsewhere and a whiteout
# removed: each tool over its own restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=$M/drr || D=$M/duu
    : >$L/$n.before
    for X in $D$P $D$P/part-future $D$P/var $D$P$BOOTM $D$P$BOOTM/init; do
        chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
            echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
            echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
        lsattr -d $X/immutable $X/append-only $X/immutable-dir >>$L/$n.before 2>&1
    done
    U=$D$P/var/lib/abroot/etc/$PRESL
    { setfattr -x trusted.overlay.opaque $U/replaced.d && setfattr -n trusted.overlay.redirect -v /elsewhere $U/newdir && rm $U/deleted.conf &&
        echo "upper changed: replaced.d not opaque, newdir redirected to /elsewhere, the whiteout deleted.conf removed"; } >>$L/$n.before 2>&1
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
    python3 $B/fsmeta.py record $D$M/src $L/$n.json
    btrfsx $D$P $L/$n.xfsx; fatx $D$P $L/$n.fat.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    merged $n $D$P
done
# Last (it writes the workdir and the upper): vos-b mounted read-write as
# mount-sys mounts /etc, over the source and each restore.
mergedrw src $P
for n in rr uu ru ur; do [[ -d $M/d$n$P/etc ]] && mergedrw $n $M/d$n$P; done
# One non-root run (nonroot-inner.sh: backup as root, restores as uid
# 4321) on a thin btrfs made and mounted like / (vos-root-nr), then
# remounted noatime: nonroot-inner.sh compares atimes.
{ mkvgs nr && udevadm settle && mk_btrfs $(lvdev $GVR-nr $presLV) $PRESL-nr && mkdir -p $M/nr &&
    mount -t btrfs -o "$ROPTS" $(lvdev $GVR-nr $presLV) $M/nr && mopts $M/nr >$L/mopts-nr &&
    mount -o remount,noatime $M/nr; } || echo "FAIL harness: no file system for the non-root run"
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh $M/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
lsblk -o NAME,TYPE,FSTYPE,LABEL,SIZE,PARTLABEL,MOUNTPOINTS $DISK >$L/lsblk.txt 2>&1
lvs -a --units m >$L/lvs.txt 2>&1
df -m $M/src $P/var $P/part-future $P$BOOTM $P$ESPM $P$BOOTM/init $M/d* $M/nr $W >$L/df.txt 2>&1
python3 $B/judge.py $L
