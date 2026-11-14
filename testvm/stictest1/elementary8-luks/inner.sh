#!/bin/bash
# SticTest1 elementary8 and elementary8-luks, the part that runs in the
# throwaway guest as root, from /root/bk (restic-bko, rustic-bko,
# fsmeta.py, rt-common.sh, torture.sh, judge.py, fatattr.py, nracl.py and
# check.sh's esp.img.gz beside it).  Prints "PASS|FAIL|LIMIT need: detail"
# lines; the records in /mnt/bkw/logs, tarred to /root/bk/logs.tar for the
# host.  The same file in both guests' directories: the layout is read
# from the guest.
#
# The spare disk (vdb, the guest's vda's size) laid out from the guest's
# own GPT (every partition's start, size, type, name and attributes,
# copied by gptcopy.py: the guest has no sfdisk; fresh ids), its file
# systems made like the guest's: the ESP (check.sh's FAT32,
# made with the guest's ESP's geometry), /boot's ext4 where the guest has
# one (elementary8-luks), and on the PV's partition (inside LUKS2 made
# with the guest's header parameters, opened with a key file, where the
# guest has it) a volume group made like data: the PV's metadata, the
# VG's extent size, each LV (root, swap) at the guest's extents, none
# free; mkswap on swap, the ext4 like / (its features, inode and block
# size, journal) on root.  Mounted with the guest's fstab options: the
# ext4 at /mnt/src (like /), /boot and the ESP inside the backed-up path
# at tree/boot and tree/boot/efi, and ubuntu26-uefi's ext4 with project
# quotas and casefolding (loop) at tree/feat.  The fixture (torture.sh and
# ubuntu26-uefi's items, the guest's own files at their own paths) on
# each.  Four directions, each into a disk image (vdc) laid out and made
# the same (a VG of its own), made fresh; each tool restoring over its own
# restore with changed immutable/append-only files, GRUB's grub.cfg and
# FAT attributes; each tool's check --read-data of the other's repository;
# a backup pair under strictatime; one non-root run (uid 4321's own tree)
# on an ext4 made like /; judge.py.
set -u
B=/root/bk RS=/root/bk/restic-bko RU=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
DISK=/dev/vdb WD=/dev/vdc
A=/mnt/src/anc1 D=/mnt/src/anc1/data
res() { echo "$1 $2: $3"; }
rootdev=$(findmnt -n -o SOURCE /) espdev=$(findmnt -n -o SOURCE /boot/efi) bootdev=$(findmnt -n -o SOURCE -M /boot)
GDISK=/dev/$(lsblk -no PKNAME "$espdev")
[[ $(blockdev --getsize64 $DISK) == "$(blockdev --getsize64 $GDISK)" && $(blockdev --getsize64 $WD) == $((200 << 30)) ]] ||
    { res FAIL harness "vdb/vdc aren't the spare disks: $(lsblk -dbno NAME,SIZE | paste -sd' ')"; exit 2; }
mkfs.ext4 -q -F $WD && mkdir -p $W && mount -o noatime $WD $W || { res FAIL harness "no work disk"; exit 2; }
mkdir -p $L $W/rt
export RT_TMP=$W/rt
. $B/rt-common.sh
st() { echo "$1 $2" >>$L/status; }
# The logs for the host even when the run stops early (a harness FAIL).
trap '[[ -s /root/bk/logs.tar ]] || tar -C $L -cf /root/bk/logs.tar . 2>/dev/null' EXIT
# The guest's own LVM files as they are, before the scratch volume groups
# change them (pvcreate and vgcreate write /etc/lvm/backup and archive).
cp -a --preserve=all /etc/lvm $W/etc-lvm 2>$L/etc-lvm.err; st copy-etc-lvm $?

# --- What the guest has ------------------------------------------------------
pn() { cat /sys/class/block/$(basename "$(readlink -f "$1")")/partition; }
read -r VG ROOTLV <<<"$(lvs --noheadings -o vg_name,lv_name "$rootdev" 2>/dev/null | xargs)"
PVDEV=$(pvs --noheadings -o pv_name -S vg_name="$VG" 2>/dev/null | xargs)
CRYPTNAME="" CRYPTPART=""
if [[ -n $PVDEV && $(lsblk -dno TYPE "$PVDEV") == crypt ]]; then
    CRYPTNAME=$(lsblk -dno NAME "$PVDEV") CRYPTPART=$(cryptsetup status "$PVDEV" | awk '$1 == "device:" {print $2}')
    PVPART=$CRYPTPART
else
    PVPART=$PVDEV
fi
# The VG's LVs in the PV's order (pvs --segments; a free segment would be "-").
LVS=$(pvs --noheadings --segments -o pvseg_start,pvseg_size,lv_name "$PVDEV" 2>/dev/null | awk '{print $1 ":" $2 ":" ($3 == "" ? "-" : $3)}' | paste -sd' ')
SWAPLV=$(for s in $LVS; do n=${s##*:}; [[ $n != - && $(blkid -o value -s TYPE /dev/$VG/$n) == swap ]] && echo $n; done | head -1)
PN=$(pn "${PVPART:-/dev/null}" 2>/dev/null) EN=$(pn "$espdev") BN=""
[[ -n $bootdev ]] && BN=$(pn "$bootdev")
ROOTOPTS=$(awk '$1 !~ /^#/ && $2 == "/" {print $4}' /etc/fstab)
ESPOPTS=$(awk '$1 !~ /^#/ && $2 == "/boot/efi" {print $4}' /etc/fstab)
BOOTOPTS=$(awk '$1 !~ /^#/ && $2 == "/boot" {print $4}' /etc/fstab)
[[ -n $VG && $ROOTLV && -n $PN && -n $EN && -n $SWAPLV && -n $ROOTOPTS && -n $ESPOPTS && $LVS != *":-"* && ( -z $BN || -n $BOOTOPTS ) ]] ||
    { res FAIL harness "can't read the guest's layout: root $rootdev ($VG/$ROOTLV) PV $PVDEV on $PVPART ($PN) LVs [$LVS] swap ${SWAPLV:-?} esp $espdev ($EN) /boot ${bootdev:--} fstab / $ROOTOPTS /boot/efi $ESPOPTS /boot ${BOOTOPTS:--}"; exit 2; }
{ uname -r; . /etc/os-release; echo "$PRETTY_NAME ($ID $VERSION_ID)"
  echo "root $rootdev ($VG/$ROOTLV) PV $PVDEV on $PVPART ($PN)${CRYPTNAME:+ LUKS2 $CRYPTNAME} LVs $LVS swap $SWAPLV esp $espdev ($EN) /boot ${bootdev:-on /}${BN:+ ($BN)} disk $GDISK"
  echo "fstab / $ROOTOPTS, /boot/efi $ESPOPTS${BN:+, /boot $BOOTOPTS}"
  dpkg-query -W -f '${Package}=${Version} ' e2fsprogs fdisk util-linux lvm2 cryptsetup-bin libcap2-bin attr acl python3 shim-signed grub-efi-amd64-signed 2>/dev/null; echo
  echo "versions: $($RS version) | $($RU --version 2>&1 | head -1)"; } >$L/env.txt
cat /sys/kernel/security/lsm >$L/lsm; echo >>$L/lsm
ls /etc/apparmor.d 2>/dev/null | wc -l >$L/apparmor-profiles
extfeat() { dumpe2fs -h "$1" 2>/dev/null | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | paste -sd' '; }
extgeo() { dumpe2fs -h "$1" 2>/dev/null | grep -E '^(Inode size|Block size|Default mount options|Flex block group size|Default directory hash|Filesystem flags|Total journal size|Reserved block count|Block count|Inode count):' | tr -s ' ' | paste -sd';'; }
fatgeom() {  # DEV: "FAT32 bps=512 spc=8 nfats=2 reserved=32 hidden=2048"
    python3 - "$1" <<'PY'
import struct, sys
b = open(sys.argv[1], "rb").read(512)
bps, spc, res, nf = struct.unpack_from("<HBHB", b, 11)
fsz16 = struct.unpack_from("<H", b, 22)[0]
hid = struct.unpack_from("<I", b, 28)[0]
t = "FAT32" if fsz16 == 0 else "FAT16"
print(f"{t} bps={bps} spc={spc} nfats={nf} reserved={res} hidden={hid}")
PY
}
gpt() { python3 $B/gptcopy.py dump "$1"; }   # DISK-or-IMAGE: first/last usable LBA, sector size; each partition's start, size, type, name, attrs
mkparts() {  # DISK-or-IMAGE: the guest's GPT (its first/last LBA, partitions; fresh ids), written by gptcopy.py (no sfdisk here)
    python3 $B/gptcopy.py copy $GDISK "$1" >>$L/sfdisk.log 2>&1 || return 1
    [[ -b $1 ]] || return 0
    blockdev --rereadpt "$1" >>$L/sfdisk.log 2>&1 || partx -u "$1" >>$L/sfdisk.log 2>&1
}
lvmof() {  # VG PV: the PV's format, data offset, metadata areas and their size, size; the VG's
           # extent size, metadata copies, attributes, PVs, LVs, extents, free extents; each
           # PV segment: start, extents, its LV's name, attributes (open masked), type, stripes, read-ahead
    pvs --noheadings --units b --nosuffix -o pv_fmt,pe_start,pv_mda_count,pv_mda_size,dev_size "$2" | xargs
    vgs --noheadings --units b --nosuffix -o vg_extent_size,vg_mda_copies,vg_attr,pv_count,lv_count,vg_extent_count,vg_free_count "$1" | xargs
    local s n
    for s in $(pvs --noheadings --segments -o pvseg_start,pvseg_size,lv_name "$2" | awk '{print $1 ":" $2 ":" ($3 == "" ? "-" : $3)}'); do
        n=${s##*:}
        echo "$s $([[ $n == - ]] || lvs --noheadings -o lv_attr,segtype,stripes,lv_read_ahead "$1/$n" | xargs | sed 's/^\(.....\)o/\1-/')"
    done
}
luksinfo() {  # PART: LUKS2's header, as luksFormat's options make it (pikaos4-luks's)
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
mopts() { findmnt -n -o OPTIONS "$1"; }
FEATS=$(extfeat "$rootdev" | tr ' ' ',')
ISZ=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Inode size: *//p')
BSZ=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Block size: *//p')
JMB=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Total journal size: *\([0-9]*\)M$/\1/p')
if [[ -n $BN ]]; then
    BFEATS=$(extfeat "$bootdev" | tr ' ' ',')
    BISZ=$(dumpe2fs -h "$bootdev" 2>/dev/null | sed -n 's/^Inode size: *//p')
    BBSZ=$(dumpe2fs -h "$bootdev" 2>/dev/null | sed -n 's/^Block size: *//p')
    BJMB=$(dumpe2fs -h "$bootdev" 2>/dev/null | sed -n 's/^Total journal size: *\([0-9]*\)M$/\1/p')
fi
gpt $GDISK >$L/gpt-guest
extfeat "$rootdev" >$L/feat-guest; extgeo "$rootdev" >>$L/feat-guest
[[ -n $BN ]] && { extfeat "$bootdev" >$L/feat-guest-boot; extgeo "$bootdev" >>$L/feat-guest-boot; }
fatgeom "$espdev" >$L/fat-guest
{ echo "/ $(mopts /)"; [[ -n $BN ]] && echo "/boot $(mopts /boot)"; echo "/boot/efi $(mopts /boot/efi)"; } >$L/mopts-guest
lvmof "$VG" "$PVDEV" >$L/lvm-guest 2>&1
[[ -n $CRYPTPART ]] && luksinfo "$CRYPTPART" >$L/luks-guest
echo "$SWAPLV $(blockdev --getsize64 /dev/$VG/$SWAPLV) $(blkid -o value -s TYPE /dev/$VG/$SWAPLV)" >$L/swap-guest
echo "mkfs.ext4 -b $BSZ -I $ISZ -J size=$JMB -O none,$FEATS${BN:+; /boot mkfs.ext4 -b $BBSZ -I $BISZ -J size=$BJMB -O none,$BFEATS}; FAT32 $(cat $L/fat-guest); / $ROOTOPTS; ESP $ESPOPTS${BN:+; /boot $BOOTOPTS}" >>$L/env.txt

# --- The scratch stack ---------------------------------------------------------
mkroot() {  # DEV-or-IMAGE: an ext4 made like the guest's /
    mkfs.ext4 -q -F -b "$BSZ" -I "$ISZ" ${JMB:+-J size=$JMB} -O "none,$FEATS" "$1"
}
mkboot() {  # DEV: an ext4 made like the guest's /boot
    mkfs.ext4 -q -F -b "$BBSZ" -I "$BISZ" ${BJMB:+-J size=$BJMB} -O "none,$BFEATS" "$1"
}
mkesp() {  # DEV: check.sh's FAT32 (the guest's ESP's geometry), with a fresh volume id
    zcat $B/esp.img.gz | dd of="$1" bs=1M conv=sparse,fsync status=none && python3 - "$1" <<'PY'
import os, sys
with open(sys.argv[1], "r+b") as f:
    v = os.urandom(4)
    for off in (67, 6 * 512 + 67):  # BS_VolID, in the boot sector and its backup
        f.seek(off); f.write(v)
PY
}
mkfeat() {  # IMAGE MOUNTPOINT: ubuntu26-uefi's ext4 with ext4's optional features
    truncate -s 256M "$1" && mkfs.ext4 -q -F -O project,casefold "$1" && mkdir -p "$2" && mount -o loop "$1" "$2"
}
mkluks() {  # PART NAME: LUKS2 made with the guest's header parameters, opened with a key file
    local la
    read -r -a la <<<"$(cryptsetup luksDump "$CRYPTPART" | awk '
        /^Data segments:/ {s = "data"} /^Keyslots:/ {s = "key"} /^Tokens:/ {s = ""}
        s == "data" && /^\toffset:/ && !o {o = $2} s == "data" && /^\tcipher:/ && !c {c = $2} s == "data" && /^\tsector:/ && !z {z = $2}
        s == "key" && /^\tKey:/ && !k {k = $2} s == "key" && /^\tPBKDF:/ && !p {p = $2}
        s == "key" && /^\tTime cost:/ && !t {t = $3} s == "key" && /^\tMemory:/ && !m {m = $2} s == "key" && /^\tThreads:/ && !h {h = $2}
        END {print c, z, k, p, t, m, h, o / 512}')"
    [[ -f $W/luks-key ]] || head -c 64 /dev/urandom >$W/luks-key
    cryptsetup luksFormat -q --type luks2 --cipher "${la[0]}" --sector-size "${la[1]}" --key-size "${la[2]}" --pbkdf "${la[3]}" \
        --pbkdf-force-iterations "${la[4]}" --pbkdf-memory "${la[5]}" --pbkdf-parallel "${la[6]}" --offset "${la[7]}" \
        --key-file $W/luks-key "$1" >>$L/luks.log 2>&1 &&
        cryptsetup open --allow-discards --key-file $W/luks-key "$1" "$2" >>$L/luks.log 2>&1
}
mkvg() {  # PV VGNAME: a PV and VG like the guest's, its LVs at the guest's extents, swap made, the ext4 like / on root
    local s st sz n
    pvcreate -q -y -ff "$1" >>$L/lvm.log 2>&1 && vgcreate -q -s "$(( $(vgs --noheadings --units b --nosuffix -o vg_extent_size "$VG" | xargs) / 1024 ))k" "$2" "$1" >>$L/lvm.log 2>&1 || return 1
    for s in $LVS; do
        IFS=: read -r st sz n <<<"$s"
        lvcreate -q -y -W y -Z y -l "$sz" -n "$n" "$2" "$1:$st-$((st + sz - 1))" >>$L/lvm.log 2>&1 || return 1
    done
    udevadm settle
    mkswap -q /dev/$2/$SWAPLV >>$L/mkfs.log 2>&1 && mkroot /dev/$2/$ROOTLV >>$L/mkfs.log 2>&1
}
mkstack() {  # PARTPREFIX ROOTMOUNT FEATIMAGE SET: the ESP, /boot, [LUKS2] and the VG bkst_SET on PARTPREFIX{EN,BN,PN}; root at ROOTMOUNT, /boot, the ESP and feat inside the tree
    local p=$1 r=$2 t=$2$D s=$4 pv=$1$PN
    [[ $r == /mnt/src ]] && t=$D
    mkesp ${p}$EN >>$L/mkfs.log 2>&1 || return 1
    if [[ -n $BN ]]; then mkboot ${p}$BN >>$L/mkfs.log 2>&1 || return 1; fi
    if [[ -n $CRYPTPART ]]; then mkluks $pv bkst_${s}_crypt || return 1; pv=/dev/mapper/bkst_${s}_crypt; fi
    mkvg $pv bkst_$s || return 1
    echo "$s $pv" >>$L/pvs
    mkdir -p $r && mount -o "$ROOTOPTS" /dev/bkst_$s/$ROOTLV $r && mkdir -p $t/boot || return 1
    if [[ -n $BN ]]; then mount -t ext4 -o "$BOOTOPTS" ${p}$BN $t/boot || return 1; fi
    mkdir -p $t/boot/efi && mount -t vfat -o "$ESPOPTS" ${p}$EN $t/boot/efi && mkfeat "$3" $t/feat
}
mkparts $DISK && udevadm settle && [[ -b $DISK$EN && -b $DISK$PN ]] && mkstack $DISK /mnt/src $W/srcfeat.img src ||
    { res FAIL harness "no scratch stack on $DISK: $(tail -3 $L/sfdisk.log $L/luks.log $L/lvm.log $L/mkfs.log 2>/dev/null | paste -sd' ')"; exit 2; }
gpt $DISK >$L/gpt-src
extfeat /dev/bkst_src/$ROOTLV >$L/feat-src; extgeo /dev/bkst_src/$ROOTLV >>$L/feat-src
[[ -n $BN ]] && { extfeat $DISK$BN >$L/feat-src-boot; extgeo $DISK$BN >>$L/feat-src-boot; }
fatgeom $DISK$EN >$L/fat-src
{ echo "/ $(mopts /mnt/src)"; [[ -n $BN ]] && echo "/boot $(mopts $D/boot)"; echo "/boot/efi $(mopts $D/boot/efi)"; } >$L/mopts-src
srcpv=$(awk '$1 == "src" {print $2}' $L/pvs)
lvmof bkst_src "$srcpv" >$L/lvm-src 2>&1
[[ -n $CRYPTPART ]] && luksinfo $DISK$PN >$L/luks-src
echo "$SWAPLV $(blockdev --getsize64 /dev/bkst_src/$SWAPLV) $(blkid -o value -s TYPE /dev/bkst_src/$SWAPLV)" >$L/swap-src
ALL_MOUNTS="/mnt/src ${BN:+$D/boot }$D/boot/efi $D/feat"
for m in $ALL_MOUNTS; do mount -o remount,strictatime $m; done

# --- the fixture ------------------------------------------------------------
# torture.sh, but with 8 MiB of random bytes instead of 1 GiB.  What it
# skips for want of setfattr or setfacl is set from Python below (as
# kali's; the guest has attr and acl or not).
sed 's|head -c 1G /dev/urandom|head -c 8M /dev/urandom|' $B/torture.sh >$W/torture.sh
bash $W/torture.sh $D 2>$L/torture.err
T=$D/root/bko-torture
python3 - $T <<'PY' 2>>$L/fixture.err
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
# The guest's own files at their own paths in the tree (the tree is like /):
# its set-id programs, shadow, passwd, group, fstab, crypttab (the LUKS
# guest), GRUB's defaults, apt's sources (Ubuntu's and elementary's
# PPAs), /etc/lvm as it was before the scratch VGs, /boot/grub and the
# kernels' config and System.map (on the LUKS guest, onto its /boot's
# ext4), the users' homes (with /home's metadata), the journal with its
# ACLs and user.crtime_usec; files with capabilities, hard-linked files and
# files with holes (ubuntu26-uefi's guest/ copies).
n=0
cpreal() {  # PATH...: cp -a each to the tree at its own path (parents with the guest's metadata)
    local f d
    for f in "$@"; do
        [[ -e $f ]] || { echo "real: missing $f" >>$L/fixture.err; continue; }
        d=$(dirname "$f"); mkdir -p "$D$d"
        cp -a --preserve=all "$f" "$D$d/" 2>>$L/fixture.err && n=$((n + 1)) && echo "real: $f" >>$L/real.txt
    done
}
SETID=$(find / -xdev -type f -perm /6000 2>/dev/null | sort)
CAPS=$(python3 - <<'PY'
import os
out = []
for top in ["/usr", "/etc", "/opt"]:
    for d, ds, fs in os.walk(top):
        for f in fs:
            p = os.path.join(d, f)
            try:
                if not os.path.islink(p) and "security.capability" in os.listxattr(p): out.append(p)
            except OSError: pass
print(" ".join(out))
PY
)
echo "set-id programs: $(echo $SETID | wc -w): $(echo $SETID)" >>$L/env.txt
echo "files with capabilities: ${CAPS:-none}" >>$L/env.txt
cpreal $SETID $CAPS /etc/shadow /etc/gshadow /etc/passwd /etc/group /etc/fstab /etc/default/grub /etc/apt/sources.list.d \
       /boot/grub /boot/config-* /boot/System.map-* /etc/os-release /etc/sudoers
[[ -n $CRYPTPART ]] && cpreal /etc/crypttab
mkdir -p $D/etc && cp -a --preserve=all $W/etc-lvm $D/etc/lvm 2>>$L/fixture.err && echo "real: /etc/lvm" >>$L/real.txt
ref() { chmod --reference="$2" "$1"; chown --reference="$2" "$1"; touch -h --reference="$2" "$1"; }
mkdir -p $D/home && ref $D/home /home
HOMES=$(find /home -mindepth 1 -maxdepth 1 -type d | sort)
echo "homes: $(echo $HOMES)" >>$L/env.txt
cpreal $HOMES /var/log/journal
for p in /etc /etc/default /etc/apt /etc/apt/sources.list.d /usr /usr/bin /usr/sbin /usr/lib /boot /var /var/log; do
    [[ -d $D$p ]] && ref $D$p $p
done
# The journal's ACLs and xattrs, as the guest has them (cp copies them;
# this makes sure).
python3 - /var/log/journal $D/var/log/journal <<'PY' 2>>$L/fixture.err
import os, sys
a, b = sys.argv[1], sys.argv[2]
for dp, dns, fns in os.walk(a):
    for n in [""] + dns + fns:
        p = os.path.join(dp, n) if n else dp
        q = os.path.join(b, os.path.relpath(p, a))
        if not os.path.lexists(q):
            print(f"fixture: skipped: journal copy lacks {q}", file=sys.stderr); continue
        for x in os.listxattr(p, follow_symlinks=False):
            v = os.getxattr(p, x, follow_symlinks=False)
            try: cur = os.getxattr(q, x, follow_symlinks=False)
            except OSError: cur = None
            if cur != v: os.setxattr(q, x, v, follow_symlinks=False)
PY
G=$D/guest; mkdir -p $G
for f in $(find /usr /etc -xdev -type f -links +1 2>/dev/null | head -2) \
         $(find / -xdev -path /mnt -prune -o -type f -size +0 -printf '%b %s %p\n' 2>/dev/null | awk '$1*512 < $2 {print $3}' | grep -v -e '^/root/' -e '^/var/log/journal/' -e '^/proc/' -e '^/swap' | head -5); do
    [ -e "$f" ] || continue
    mkdir -p "$G$(dirname "$f")"
    cp -a --sparse=always "$f" "$G$f" 2>>$L/fixture.err && n=$((n+1)) && echo "guest file: $f" >>$L/real.txt
done
echo "real files copied: $n" >>$L/env.txt
# Owners with no name, big ids, rdev 0, more inode flags (ubuntu26-uefi's)
mkdir -p $D/noname-dir; chown 4321:8765 $D/noname-dir; touch $D/noname-dir/f; chown 54321:65432 $D/noname-dir/f
touch $D/bigid; chown 3000000000:3000000001 $D/bigid
mknod $D/rdev0-char c 0 0; mknod $D/rdev0-block b 0 0
for fl in S A j s u c t T x; do
    echo $fl >$D/flag-$fl; chattr +$fl $D/flag-$fl 2>>$L/torture.err
    [[ $(lsattr -d $D/flag-$fl 2>/dev/null | cut -d' ' -f1) == *"$fl"* ]] && echo "flag $fl kept" >>$L/env.txt || { echo "flag $fl not kept on ext4" >>$L/env.txt; rm -f $D/flag-$fl; }
done
mkdir $D/dirsync-dir; chattr +D $D/dirsync-dir
mkdir $D/topdir; chattr +T $D/topdir 2>>$L/torture.err
echo ai >$D/append-nodump; chattr +a +d $D/append-nodump
mkdir $D/immutable-dir; echo in >$D/immutable-dir/inside; chattr +i $D/immutable-dir
echo ap >$D/append-only2; chattr +a $D/append-only2
mkdir -p $D/empty-deep/a/b/c/d/e/f/g
if [[ -n $BN ]]; then
    # /boot's own ext4 (the LUKS guest's): inode flags, an immutable and
    # an append-only file, an ACL and xattrs on it too.
    echo bi >$D/boot/bko-immutable; chattr +i $D/boot/bko-immutable
    echo ba >$D/boot/bko-append; chattr +a $D/boot/bko-append
    echo bx >$D/boot/bko-xattr
    python3 - $D/boot/bko-xattr <<'PY' 2>>$L/fixture.err
import os, struct, sys
X = 0xffffffff
os.setxattr(sys.argv[1], "user.boot", b"b"); os.setxattr(sys.argv[1], "trusted.boot", b"t")
os.setxattr(sys.argv[1], "system.posix_acl_access", struct.pack("<I", 2) + b"".join(
    struct.pack("<HHI", t, p, i) for t, p, i in [(1, 6, X), (2, 4, 1234), (4, 4, X), (0x10, 4, X), (0x20, 0, X)]))
PY
    truncate -s 4M $D/boot/bko-sparse; echo e >>$D/boot/bko-sparse
fi
# Project IDs and casefolding on the feature ext4
F=$D/feat
echo p >$F/projid-file; chattr -p 42 $F/projid-file
mkdir $F/projid-dir; chattr +P -p 43 $F/projid-dir; echo inh >$F/projid-dir/inherits; mkdir $F/projid-dir/sub
mkdir $F/casefold-dir; chattr +F $F/casefold-dir; echo m >$F/casefold-dir/MixedCase; mkdir $F/casefold-dir/SubDir
mkdir $F/casefold-dir/SubDir/nested
mkdir $F/casefold-projid; chattr +F +P -p 44 $F/casefold-projid; echo x >$F/casefold-projid/Both
echo i >$F/immutable-projid; chattr -p 45 $F/immutable-projid; chattr +i $F/immutable-projid
truncate -s 1M $F/sparse-projid; echo e >>$F/sparse-projid; chattr -p 46 $F/sparse-projid
lsattr -p -R $F >>$L/env.txt 2>&1
# The ESP: the guest's own (shim, the signed GRUB, MokManager in EFI/<id>
# and the fallback EFI/BOOT) at its own paths with its times and FAT
# attributes, then FAT's own: hidden, system, archive set and cleared,
# read-only by chmod and by attribute, on files and dirs; long, case-mixed
# and UTF-8 names; deep, empty and big dirs; FAT's time limits.
E=$D/boot/efi
fat() {  # PATH ATTRS: FAT_IOCTL_SET_ATTRIBUTES
    python3 -c 'import fcntl,os,struct,sys; fd=os.open(sys.argv[1],os.O_RDONLY); fcntl.ioctl(fd,0x40047211,struct.pack("I",int(sys.argv[2],0))); os.close(fd)' "$1" "$2" ||
        echo "fixture: skipped: FAT attributes $2 on $1" >>$L/fixture.err
}
cp -R --preserve=timestamps /boot/efi/. $E/ 2>>$L/fixture.err || echo "fixture: skipped: copy of /boot/efi" >>$L/fixture.err
python3 - /boot/efi $E <<'PY' 2>>$L/fixture.err || echo "fixture: skipped: the ESP's FAT attributes" >>$L/fixture.err
import fcntl, os, sys
a, b = sys.argv[1], sys.argv[2]
for dp, dns, fns in os.walk(a):
    for n in dns + fns:
        p = os.path.join(dp, n); q = os.path.join(b, os.path.relpath(p, a))
        fd = os.open(p, os.O_RDONLY); v = fcntl.ioctl(fd, 0x80047210, b"\0" * 4); os.close(fd)
        fd = os.open(q, os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, v); os.close(fd)
PY
# The GRUB the guest's NVRAM entry starts (EFI/<id>/grubx64.efi, not the fallback's)
EFIGRUB=$(cd /boot/efi && ls EFI/*/grubx64.efi 2>/dev/null | grep -v '^EFI/BOOT/' | head -1)
echo "EFI GRUB: ${EFIGRUB:-none}; ESP: $(cd /boot/efi && find . -type f | sort | paste -sd' ')" >>$L/env.txt
(
cd $E || exit 1
echo h >hidden; fat hidden 0x22
echo s >system; fat system 0x24
echo hs >hidden-system-noarchive; fat hidden-system-noarchive 0x06
echo n >archive-cleared; fat archive-cleared 0
echo a >archive-set; fat archive-set 0x20
echo r >readonly-chmod; chmod a-w readonly-chmod
echo r >readonly-attr; fat readonly-attr 0x21
mkdir hidden-dir system-dir readonly-dir; echo in >hidden-dir/f; echo in >readonly-dir/f
fat hidden-dir 0x12; fat system-dir 0x14; fat readonly-dir 0x11
: >"name with spaces"; : >MixedCase.Txt; : >UPPER.TXT; : >"a+b,c;d=e[f]"; : >.dotfile
: >"café-ünïcode" || echo "fixture: skipped: a UTF-8 name" >>$L/fixture.err
: >"$(head -c 200 /dev/zero | tr '\0' n)"
mkdir -p EFI/BKO; head -c 307200 /dev/urandom >EFI/BKO/bkox64.efi; head -c 20971520 /dev/urandom >big
d=deep; for i in $(seq 30); do d=$d/level$i; done; mkdir -p $d; echo bottom >$d/bottom
: >empty; mkdir empty-dir
mkdir big-dir; for i in $(seq 300); do : >"big-dir/entry-$i-padding-padding"; done
touch -d 1980-01-01T00:00:00 fat-min; touch -d 2107-12-31T23:59:58 fat-max
touch -d 2001-02-03T04:05:08 even-second
echo atime >atime; touch -m -d 2002-03-04T05:06:08 atime; touch -a -d 2003-04-05T00:00:00 atime
touch -m -d 2002-03-04T05:06:08 readonly-dir hidden-dir; touch -a -d 2003-04-05T00:00:00 readonly-dir hidden-dir
)
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
noatime() { for m in $ALL_MOUNTS; do mount -o remount,noatime $m; done; }
strictatime() { for m in $ALL_MOUNTS; do mount -o remount,strictatime $m; done; }
rec() { python3 $B/fsmeta.py record "$1" "$2" --skip root/bko-torture/socket; }
cmp() { python3 $B/fsmeta.py compare "$1" "$2" --json "$3" >"$3.txt" 2>&1; }
noatime
rec $D $L/src0.json
python3 $B/fatattr.py $E $L/src.fat.json
python3 - $L/src0.json <<'PY' >>$L/env.txt
import json, sys
sys.path.insert(0, "/root/bk"); import fsmeta
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
anc_stat $A >$L/anc-src

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
    s=$(bk $bin $W/repo-$t); st backup-$t $s
done
for t in restic rustic; do
    bin=$RS; [ $t = rustic ] && bin=$RU
    rec $D $L/src-before-atime-$t.json
    strictatime; s=$(bk $bin $W/repo-atime-$t); noatime
    st backup-atime-$t $s
    rec $D $L/src-after-atime-$t.json
done

# --- same repository format ---------------------------------------------------
rt_nodes $RS $W/repo-restic $D >$L/nodes.restic 2>$L/nodes.err
rt_nodes $RS $W/repo-rustic $D >$L/nodes.rustic 2>>$L/nodes.err
rt_node_diff $L/nodes.restic $L/nodes.rustic >$L/nodes.diff 2>&1; st nodes $?
$RS -r $W/repo-rustic check --read-data >$L/check-restic-on-rustic.log 2>&1; st check-restic-on-rustic $?
$RU --no-progress --no-cache -r $W/repo-restic check --read-data >$L/check-rustic-on-restic.log 2>&1; st check-rustic-on-restic $?
# The ancestor (anc1): both tools store its lstat; compared node by node.
rt_nodes $RS $W/repo-restic $A >$L/anc.restic 2>/dev/null; rt_nodes $RS $W/repo-rustic $A >$L/anc.rustic 2>/dev/null
grep -P '^\.\t' $L/anc.restic >$L/anc1.restic; grep -P '^\.\t' $L/anc.rustic >$L/anc1.rustic

# --- restores in four directions --------------------------------------------
# Each target: a disk image laid out as the guest's disk, its file systems
# (and VG) made and mounted as the source's (as BackOtter mounts a
# machine's file systems before it restores).
restore() {  # TOOL REPO TARGET
    if [[ $1 == *rustic* ]]; then $1 --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $1 -r $2 restore -q latest --target $3; fi
}
DIRS="restic-restic rustic-rustic restic-rustic rustic-restic"
declare -A SET=([restic-restic]=rr [rustic-rustic]=uu [restic-rustic]=ru [rustic-restic]=ur)
for d in $DIRS; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    t=/mnt/t-$d
    truncate -s "$(blockdev --getsize64 $GDISK)" $W/$d.img && mkparts $W/$d.img && lo=$(losetup -P --show -f $W/$d.img) && udevadm settle &&
        mkstack ${lo}p $t $W/$d-feat.img ${SET[$d]} ||
        { res FAIL harness "no target stack for $d: $(tail -3 $L/luks.log $L/lvm.log $L/mkfs.log 2>/dev/null | paste -sd' ')"; continue; }
    echo "$d $lo" >>$L/loops
    gpt $W/$d.img >$L/gpt-$d
    lvmof bkst_${SET[$d]} "$(awk -v s=${SET[$d]} '$1 == s {print $2}' $L/pvs)" >$L/lvm-$d 2>&1
    restore $bin $W/repo-$b $t >$L/$d.restore 2>&1; st restore-$d $?
    sync; for m in $t ${BN:+$t$D/boot} $t$D/boot/efi $t$D/feat; do mount -o remount,noatime $m; done
    rec $t$D $L/$d.json
    python3 $B/fatattr.py $t$E $L/$d.fat.json
    cmp $L/src0.json $L/$d.json $L/$d.cmp.json
    anc_stat $t$A >$L/$d.anc
    findmnt -n -o SOURCE,FSTYPE --target $t/. >$L/$d.rootmount
    findmnt -n -o SOURCE,FSTYPE --target $t$D/boot/. >$L/$d.bootmount
    findmnt -n -o SOURCE,FSTYPE --target $t$E/. >$L/$d.espmount
    [ -e $t$T/socket ] && echo "socket restored" >>$L/$d.sock
done

# --- restore again over the restored tree, its immutable/append-only files, GRUB and FAT attributes changed
for d in restic-restic rustic-rustic; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    t=/mnt/t-$d$D
    mountpoint -q $t/feat || continue
    chattr -i $t/root/bko-torture/immutable; echo changed >$t/root/bko-torture/immutable; chattr +i $t/root/bko-torture/immutable
    chattr -a $t/root/bko-torture/append-only; echo more >>$t/root/bko-torture/append-only; chattr +a $t/root/bko-torture/append-only
    chattr -i $t/feat/immutable-projid; chmod 0600 $t/feat/immutable-projid; chattr +i $t/feat/immutable-projid
    chattr -i $t/immutable-dir; echo new >$t/immutable-dir/inside; chmod 0700 $t/immutable-dir; chattr +i $t/immutable-dir
    echo changed >$t/root/bko-torture/empty
    [[ -f $t/boot/grub/grub.cfg ]] && echo "# changed" >>$t/boot/grub/grub.cfg
    if [[ -n $BN ]]; then
        chattr -i $t/boot/bko-immutable; echo changed >$t/boot/bko-immutable; chattr +i $t/boot/bko-immutable
        chattr -a $t/boot/bko-append; echo more >>$t/boot/bko-append; chattr +a $t/boot/bko-append
    fi
    python3 - $t/boot/efi "$EFIGRUB" <<'PY' >>$L/$d.over.before 2>&1
import fcntl, os, struct, sys
E, grub = sys.argv[1], sys.argv[2]
def setfat(p, v):
    fd = os.open(os.path.join(E, p), os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, struct.pack("I", v)); os.close(fd)
setfat("readonly-attr", 0x20)
open(os.path.join(E, "readonly-attr"), "w").write("changed-contents\n")
setfat("readonly-attr", 0x21)
for p, v in (("hidden", 0x20), ("archive-cleared", 0x20), ("system-dir", 0x10), ("readonly-dir", 0x10)):
    setfat(p, v)
if grub: open(os.path.join(E, grub), "r+b").write(b"changed")
print(f"FAT attributes changed before the restore over: readonly-attr (contents), hidden, archive-cleared, system-dir, readonly-dir; {grub or 'no GRUB'} overwritten")
PY
    restore $bin $W/repo-$b /mnt/t-$d >$L/$d.over 2>&1; st restore-over-$d $?
    sync
    rec $t $L/$d.over.json
    python3 $B/fatattr.py $t/boot/efi $L/$d.over.fat.json
    cmp $L/src0.json $L/$d.over.json $L/$d.over.cmp.json
done

# --- one non-root run (uid 4321 backs up its own tree and restores it) -------
truncate -s 1G $W/nonroot.img && mkroot $W/nonroot.img >>$L/mkfs.log 2>&1 && mkdir -p /mnt/nonroot && mount -o loop,$ROOTOPTS $W/nonroot.img /mnt/nonroot ||
    res FAIL harness "no ext4 for the non-root run"
N=/mnt/nonroot; mkdir -p $N/src $N/out; chown 4321:4321 $N $N/src $N/out
# /root is 0700: the user runs copies of the binaries and nracl.py
mkdir -p $W/nrbin; install -m 755 $RS $RU $B/nracl.py $W/nrbin/; chmod 755 $W
NRS=$W/nrbin/restic-bko NRU=$W/nrbin/rustic-bko
su_() { setpriv --reuid=4321 --regid=4321 --clear-groups env HOME=/tmp RESTIC_PASSWORD=x RUSTIC_PASSWORD=x "$@"; }
su_ sh -c "cd $N/src && mkdir -p d/e empty && echo a >d/f && chmod 0640 d/f && ln -s f d/l && ln d/f d/hl && mkfifo p && head -c 100000 /dev/urandom >big && truncate -s 10M sparse && echo z >>sparse && chattr +d big && chmod 2750 d/e && touch -d '2003-03-03 03:03:03.3' d/f && python3 $W/nrbin/nracl.py"
echo "nonroot fixture exit $?" >>$L/env.txt
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
lsblk -o NAME,TYPE,FSTYPE,SIZE,PARTLABEL,MOUNTPOINTS $DISK $(cut -d' ' -f2 $L/loops 2>/dev/null) >$L/lsblk.txt 2>&1
lvs --units m >$L/lvs.txt 2>&1
df -m /mnt/src ${BN:+$D/boot} $E $D/feat /mnt/t-* $N $W >$L/df.txt 2>&1
python3 $B/judge.py $L

# --- the logs for the host ------------------------------------------------------
tar -C $L -cf /root/bk/logs.tar .
exit 0
