#!/bin/bash
# In the gentoo or gentoo-systemd throwaway guest, as root, from /root/bk:
# the spare disk (vdb) laid out as the guest's own (GPT: ESPs of the
# guest's ESP's size, gentoo's swap partition, then the rest one LVM PV,
# on gentoo-systemd inside LUKS2 made with the guest's header parameters),
# volume group bkst; per tree an ext4 LV made and mounted like /, with
# gentoo's XFS LV like /home and an ESP (FAT32, the guest's geometry and
# mount options) mounted inside the backed-up path where the guest has
# them (tree/home, tree/efi; gentoo-systemd's ESP is /boot: tree/boot).
# The fixture (fixture.sh) on all three, four directions into the same
# stack made fresh, each tool restoring over its own restore with changed
# immutable/append-only files and FAT attributes, a backup pair on
# relatime, and on gentoo the cross-file-system case: each tool's snapshot
# restored onto a tree whose / is XFS (the /home and ESP as before),
# judged without ext4's own inode flags.  Then the non-root run on an ext4
# LV, and judge.py.
#
# Usage: inner.sh GUEST
set -u
G=${1:?guest}
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
DISK=/dev/vdb VG=bkst P=/mnt/src/up1/up2/tree
# (gentoo-systemd has no xfsprogs: its work disk is ext4.)
{ if command -v mkfs.xfs >/dev/null; then mkfs.xfs -q -f /dev/vdc; else mkfs.ext4 -q -F /dev/vdc; fi; } && mkdir -p $W &&
    mount -o noatime /dev/vdc $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L
st() { echo "$1 $2" >>$L/status; }
echo "$G" >$L/guest
# The guest's own LVM files as they are, before the scratch volume group
# changes them (pvcreate and vgcreate write /etc/lvm/backup and archive).
cp -a --preserve=all /etc/lvm $W/etc-lvm 2>$L/etc-lvm.err; st copy-etc-lvm $?

# --- What the guest has ------------------------------------------------------
ESPM=$(findmnt -rn -t vfat -o TARGET | head -1); ESPN=${ESPM#/}
HOMEX=0; [[ $(findmnt -n -o FSTYPE /home 2>/dev/null) == xfs ]] && HOMEX=1
rootdev=$(findmnt -n -o SOURCE /) espdev=$(findmnt -n -o SOURCE "$ESPM")
# The partition under LUKS under /'s LV (lsblk -s: the device, then its parents).
CRYPTPART=$(lsblk -rsn -o NAME,TYPE "$rootdev" | awk '$2 == "crypt" {c = 1; next} c && $2 == "part" {print "/dev/" $1; exit}')
export ESPM
{ echo "ESPM=$ESPM ESPN=$ESPN HOMEX=$HOMEX CRYPTPART=${CRYPTPART:--}"; uname -r; . /etc/os-release; echo "$PRETTY_NAME"
  ls -d /var/db/pkg/sys-fs/e2fsprogs-* /var/db/pkg/sys-fs/xfsprogs-* /var/db/pkg/sys-fs/dosfstools-* /var/db/pkg/sys-fs/lvm2-* \
      /var/db/pkg/sys-fs/cryptsetup-* 2>/dev/null | sed 's|/var/db/pkg/||' | paste -sd' '; } >$L/env.txt
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
mopts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(relatime|noatime|strictatime|atime)$' | paste -sd,; }
atimeopt() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -E '^(relatime|noatime|strictatime)$' | head -1; }
gpt() {  # DISK: the label and each partition's start, size, type (no ids)
    sfdisk -d $1 | sed -n 's/^label: //p; s/^[^ ]* : start= *\([0-9]*\), size= *\([0-9]*\), type=\([0-9A-F-]*\).*/\1 \2 \3/p'
}
lvm_of() {  # PV VG LV: format, data offset, metadata areas; extent size; segment
    pvs --noheadings --units b -o pv_fmt,pe_start,pv_mda_count,pv_mda_size $1 | xargs
    vgs --noheadings --units b -o vg_extent_size,vg_mda_copies,vg_attr $2 | xargs
    lvs --noheadings -o lv_attr,segtype,stripes,lv_read_ahead,lv_layout $2/$3 | xargs | sed 's/^\(.....\)o/\1-/'
}
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
lv0=$(lvs --noheadings -o vg_name,lv_name "$rootdev" | xargs)
pv0=$(pvs --noheadings -o pv_name -S vg_name="${lv0% *}" | xargs)
extfeat "$rootdev" >$L/feat-root; extgeo "$rootdev" >$L/geo-root; mopts / >$L/mopts-root; atimeopt / >$L/atime-root
fatgeom "$espdev" >$L/fat-esp; mopts "$ESPM" >$L/mopts-esp; atimeopt "$ESPM" >$L/atime-esp
((HOMEX)) && { xfsfeat /home >$L/feat-home; mopts /home >$L/mopts-home; atimeopt /home >$L/atime-home; }
lvm_of "$pv0" ${lv0% *} ${lv0#* } >$L/lvm-root 2>&1
gpt /dev/vda >$L/gpt-root
[[ -n $CRYPTPART ]] && luksinfo "$CRYPTPART" >$L/luks-root
# mkfs as the guest's were made: ext4's features, inode and block size;
# XFS's -m/-i/-n options from /home; FAT's type, sector and cluster size.
BSZ=$(tune2fs -l "$rootdev" | sed -n 's/^Block size: *//p') ISZ=$(tune2fs -l "$rootdev" | sed -n 's/^Inode size: *//p')
EXTO=$(tr ' ' '\n' <$L/feat-root | grep . | grep -v '^has_journal$' | paste -sd,)
xv() { tr ' ' '\n' <$L/feat-home | sed -n "s/^$1=//p"; }
XFSO=()
((HOMEX)) && XFSO=(-m "crc=$(xv crc),finobt=$(xv finobt),rmapbt=$(xv rmapbt),reflink=$(xv reflink),bigtime=$(xv bigtime),inobtcount=$(xv inobtcount),metadir=$(xv metadir)"
                   -i "sparse=$(xv sparse),nrext64=$(xv nrext64),exchange=$(xv exchange)" -n "parent=$(xv parent)")
read -r FATT FATS FATC <<<"$(sed 's/^FAT\([0-9]*\) bps=\([0-9]*\) spc=\([0-9]*\).*/\1 \2 \3/' $L/fat-esp)"
EOPTS=$(mopts "$ESPM" | tr , '\n' | grep -vE '^(rw|ro)$' | paste -sd,)
mk_ext4() { mkfs.ext4 -q -F -b "$BSZ" -I "$ISZ" -O "$EXTO" "$1"; }
mk_xfs() { mkfs.xfs -q -f "${XFSO[@]}" "$1" 2>>$L/mkfs-xfs.log || { echo "mkfs.xfs ${XFSO[*]} refused: defaults" >>$L/mkfs-xfs.log; mkfs.xfs -q -f "$1"; }; }
mk_vfat() { mkfs.vfat -F "$FATT" -S "$FATS" -s "$FATC" -n EFI "$1" >/dev/null; }
echo "mkfs.ext4 -b $BSZ -I $ISZ -O $EXTO; mkfs.xfs ${XFSO[*]}; mkfs.vfat -F $FATT -S $FATS -s $FATC; ESP mount $EOPTS" >>$L/env.txt

# --- The scratch stack -------------------------------------------------------
# vdb as the guest's vda: its ESP's size (one per tree), gentoo's swap
# partition, then the rest one partition of the guest's last one's type
# (Linux LVM; gentoo-systemd's LUKS2 is a Linux filesystem partition).
SETS="src rr uu ru ur"; [[ $HOMEX == 1 ]] && SETS="$SETS xf"
espsz=$(sfdisk -d /dev/vda | sed -n "s|^$espdev : .*size= *\([0-9]*\),.*|\1|p")
[[ -n $espsz ]] || { echo "FAIL harness: no size for the guest's ESP $espdev"; exit 2; }
lasttype=$(gpt /dev/vda | tail -1 | cut -d' ' -f3)
swappart=$(gpt /dev/vda | awk '$3 == "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"' | head -1)
{ echo "label: gpt"; i=0
  for s in $SETS; do
      i=$((i + 1)); echo "size=$espsz, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
      [[ $i == 1 && -n $swappart ]] && echo "size=512MiB, type=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
  done
  echo "type=$lasttype"; } | sfdisk -q --wipe always $DISK >$L/sfdisk.log 2>&1 && udevadm settle ||
    { echo "FAIL harness: no partitions on $DISK: $(tail -3 $L/sfdisk.log)"; exit 2; }
gpt $DISK >$L/gpt-scratch
nparts=$(sfdisk -d $DISK | grep -c '^/dev/')
declare -A ESPPART
i=0; for s in $SETS; do i=$((i + 1)); [[ $i == 2 && -n $swappart ]] && i=3; ESPPART[$s]=${DISK}$i; done
PVDEV=${DISK}$nparts
if [[ -n $CRYPTPART ]]; then
    read -r -a LA <<<"$(cryptsetup luksDump "$CRYPTPART" | awk '
        /^Data segments:/ {s = "data"} /^Keyslots:/ {s = "key"} /^Tokens:/ {s = ""}
        s == "data" && /^\tcipher:/ && !c {c = $2} s == "data" && /^\tsector:/ && !z {z = $2}
        s == "key" && /^\tKey:/ && !k {k = $2} s == "key" && /^\tPBKDF:/ && !p {p = $2}
        s == "key" && /^\tTime cost:/ && !t {t = $3} s == "key" && /^\tMemory:/ && !m {m = $2} s == "key" && /^\tThreads:/ && !h {h = $2}
        END {print c, z, k, p, t, m, h}')"
    head -c 64 /dev/urandom >$W/luks-key
    cryptsetup luksFormat -q --type luks2 --cipher "${LA[0]}" --sector-size "${LA[1]}" --key-size "${LA[2]}" --pbkdf "${LA[3]}" \
        --pbkdf-force-iterations "${LA[4]}" --pbkdf-memory "${LA[5]}" --pbkdf-parallel "${LA[6]}" --key-file $W/luks-key $PVDEV >>$L/sfdisk.log 2>&1 &&
        cryptsetup open --allow-discards --key-file $W/luks-key $PVDEV bkst_crypt >>$L/sfdisk.log 2>&1 ||
        { echo "FAIL harness: no LUKS2 on $PVDEV: $(tail -3 $L/sfdisk.log)"; exit 2; }
    luksinfo $PVDEV >$L/luks-scratch
    PVDEV=/dev/mapper/bkst_crypt
fi
pvcreate -q $PVDEV >>$L/sfdisk.log 2>&1 && vgcreate -q $VG $PVDEV >>$L/sfdisk.log 2>&1 ||
    { echo "FAIL harness: no volume group on $PVDEV: $(tail -3 $L/sfdisk.log)"; exit 2; }
lvfs() {  # NAME FS MOUNTPOINT [SIZE]: a fresh LV, its file system made and mounted as the guest's
    lvcreate -q -y -L ${4:-1536M} -n $1 $VG >>$L/lvcreate.log 2>&1 && udevadm settle && mk_$2 /dev/$VG/$1 >>$L/lvcreate.log 2>&1 &&
        mkdir -p $3 && mount -o "$(atimeopt /)" /dev/$VG/$1 $3
}
espfs() {  # SET DIR: the set's ESP, made and mounted as the guest's
    mk_vfat ${ESPPART[$1]} && mkdir -p $2 && mount -t vfat -o "$EOPTS" ${ESPPART[$1]} $2
}
mkset() {  # SET ROOT ROOTFS: the root-like LV at ROOT (ROOTFS: ext4, xf's xfs), its /home LV and ESP at ROOT$P
    lvfs $1 $3 $2 || return 1
    # A restore target's own lost+found (ext4's, at its top) isn't the
    # snapshot's, whose top holds only mnt: rustic would note an extra entry.
    [[ $1 == src ]] || rmdir $2/lost+found 2>/dev/null
    local r=$2$P; [[ $1 == src ]] && r=$P
    mkdir -p $r
    if ((HOMEX)); then lvfs $1-home xfs $r/home 1024M || return 1; fi
    espfs $1 $r/$ESPN
}
atimes() {  # OPTION DIR: remount DIR and everything mounted below it OPTION (noatime, relatime)
    # relatime alone never clears a noatime already set (libmount merges the
    # mount's current options, and the kernel's MNT_NOATIME wins): atime
    # clears it first.
    local o=$1; [[ $1 == relatime ]] && o=atime,relatime
    findmnt -rn -o TARGET -R "$2" | while read -r m; do mount -o remount,$o "$m" || echo "remount $o $m failed" >>$L/remount.err; done
}
mopts_all() { findmnt -rn -o TARGET,OPTIONS -R "$1"; }  # DIR: each mount at and below DIR, its options
# XFS's own per-file options (fsxattr xflags, extent size hints) of /home's fixture's xfs-* paths.
xfsx() {  # TREE OUT
    ((HOMEX)) || { : >$2; return; }
    for p in xfs-extsize xfs-extsize-dir xfs-extsize-dir/inherits xfs-nodefrag xfs-filestream; do
        printf '%s ' $p; xfs_io -r -c stat "$1/home/$p" 2>&1 | grep -E 'fsxattr\.(xflags|extsize|cowextsize)' | tr -s ' \n' '  '; echo
    done >$2
}
fatx() { python3 $B/fatattr.py "$1/$ESPN" "$2"; }   # TREE OUT: the ESP's FAT attributes
mkset src /mnt/src ext4 || { echo "FAIL harness: no source file systems: $(tail -3 $L/lvcreate.log)"; exit 2; }
extfeat /dev/$VG/src >$L/feat-src; extgeo /dev/$VG/src >$L/geo-src; mopts /mnt/src >$L/mopts-src
fatgeom ${ESPPART[src]} >$L/fat-src; mopts $P/$ESPN >$L/mopts-srcesp
((HOMEX)) && { xfsfeat $P/home >$L/feat-srchome; mopts $P/home >$L/mopts-srchome; }
lvm_of $PVDEV $VG src >$L/lvm-src 2>&1
LVMREAL=$W/etc-lvm bash $B/fixture.sh /mnt/src ext4 2>$L/fixture.err
((HOMEX)) && bash $B/fixture.sh $P/home xfs 2>>$L/fixture.err
bash $B/fixture.sh $P/$ESPN vfat 2>>$L/fixture.err
sync; atimes noatime /mnt/src
python3 $B/fsmeta.py record /mnt/src $L/src.json
xfsx $P $L/src.xfsx; fatx $P $L/src.fat.json
for m in home $ESPN; do [[ -d $P/$m ]] && find $P/$m -xdev | wc -l >$L/count-src-$m; done
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
# Two more backups with the source on relatime (the compared ones ran on
# noatime, so that both tools saw the same atimes; the guest mounts / and
# /home noatime, its ESP relatime): they mustn't move the source's atimes.
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
# Each target: a fresh stack (/'s LV, /home's, its own ESP), mounted where
# the source's are (as BackOtter mounts a machine's file systems before it
# restores), as the guest mounts them.  xf (gentoo): / on XFS instead.
DIRS="rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic"
[[ $HOMEX == 1 ]] && DIRS="$DIRS xf:restic:restic xf2:rustic:rustic"
for d in $DIRS; do
    IFS=: read n b r <<<"$d"
    if [[ $n == xf2 ]]; then
        # The second cross-file-system restore (rustic's snapshot) goes onto
        # the xf stack made afresh.
        umount -R /mnt/dxf && lvremove -q -y $VG/xf $VG/xf-home >>$L/lvcreate.log 2>&1 || echo "xf2: xf not removed" >>$L/lvcreate.log
        mkset xf /mnt/dxf2 xfs || { echo "FAIL harness: no target file systems for $n"; continue; }
    else
        mkset $n /mnt/d$n $([[ $n == xf ]] && echo xfs || echo ext4) || { echo "FAIL harness: no target file systems for $n"; continue; }
    fi
    [[ $n == xf* ]] && { xfsfeat /mnt/d$n >$L/feat-$n; findmnt -n -o FSTYPE /mnt/d$n >$L/fstype-$n; }
    restore $r $W/repo-$b /mnt/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime /mnt/d$n
    python3 $B/fsmeta.py record /mnt/d$n/mnt/src $L/$n.json
    xfsx /mnt/d$n$P $L/$n.xfsx; fatx /mnt/d$n$P $L/$n.fat.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    for m in home $ESPN; do
        [[ -d /mnt/d$n$P/$m ]] && { find /mnt/d$n$P/$m -xdev | wc -l >$L/count-$n-$m; findmnt -n -o SOURCE --target /mnt/d$n$P/$m/. >$L/dev-$n-$m; }
    done
done
# Restore over existing immutable and append-only files (and an immutable
# directory) on / and /home, and changed FAT attributes and a changed
# read-only file on the ESP: each tool over its own restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=/mnt/drr || D=/mnt/duu
    : >$L/$n.before
    for X in $D$P $([[ $HOMEX == 1 ]] && echo $D$P/home); do
        chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
            echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
            echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
        lsattr -d $X/immutable $X/append-only $X/immutable-dir >>$L/$n.before 2>&1
    done
    E=$D$P/$ESPN
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
# 4321) on an ext4 LV made like / (noatime, as the guest's).
lvfs nr ext4 /mnt/nr && mopts /mnt/nr >$L/mopts-nr || echo "FAIL harness: no volume for the non-root run"
# uid 4321 can't reach /root (0700): the binaries go where it can.
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
# Gentoo's util-linux has no setpriv without USE=caps: setpriv.py does
# what nonroot-inner.sh asks of it.
command -v setpriv >/dev/null || { install -m 755 $B/setpriv.py /usr/local/bin/setpriv; echo "setpriv: setpriv.py" >>$L/env.txt; }
PATH=/usr/local/bin:$PATH FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
lvs --units m $VG >$L/lvs.txt 2>&1; df -m /mnt/src $P/$ESPN /mnt/d* /mnt/nr $W >$L/df.txt 2>&1
((HOMEX)) && df -m $P/home >>$L/df.txt 2>&1
python3 $B/judge.py $L
# The logs for the host.
tar -C /mnt/bkw/logs -cf /root/bk/logs.tar .
