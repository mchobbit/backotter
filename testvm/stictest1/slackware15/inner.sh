#!/bin/bash
# SticTest1 slackware15 and slackware15-uefi, the part that runs in the
# throwaway guest as root, from /root/bk (restic-bko, rustic-bko, fsmeta.py,
# rt-common.sh, torture.sh, judge.py, fatattr.py and nracl.py beside it).
# solus49's inner.sh with Slackware's layouts.  Prints "PASS|FAIL|LIMIT need:
# detail" lines; the records in /mnt/bkw/logs, tarred to /root/bk/logs.tar
# for the host.
#
# Reads the guest's layout first: / (ext4 on slackware15, XFS on -uefi; by
# its device number, since a kernel with no initrd mounts it as /dev/root),
# the swap partition (/proc/swaps) and, on -uefi, the ESP at /boot/efi.  The
# spare disk (vdb, the guest's vda's size) laid out from the guest's own
# `sfdisk -d` (MBR or GPT: every partition's start, size, type, name and
# bootable flag; fresh ids), its file systems made with the guest's own
# tools as the guest's are: the swap (mkswap), / (ext4: the root's features,
# inode and block size, journal; XFS: the root's -m/-i options) at /mnt/src
# with fstab's options, the ESP (FAT32 with the guest's ESP's geometry)
# inside the backed-up path at tree/boot/efi with the guest's ESP's mount
# options.  slackware15 adds ubuntu26-uefi's ext4 with project quotas and
# casefolding (loop) at tree/feat; on -uefi tree/feat is a directory on the
# XFS, with XFS's project IDs and per-file options (gentoo's).  The fixture
# (torture.sh and ubuntu26-uefi's items, the guest's own files at their own
# paths) on each.  Four directions, each into a disk image (vdc) laid out and
# mounted the same, made fresh; each tool restoring over its own restore
# with changed immutable/append-only files (and LILO's map and lilo.conf, or
# the ESP's elilo.efi, elilo.conf and FAT attributes); each tool's check
# --read-data of the other's repository; a backup pair under strictatime;
# one non-root run (uid 4321's own tree) on a file system made like /;
# judge.py.
#
# Usage: inner.sh GUEST
set -u
G=${1:?guest}
# Slackware's tools are in /sbin and /usr/sbin; an ssh command's PATH may lack them.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
B=/root/bk RS=/root/bk/restic-bko RU=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
DISK=/dev/vdb WD=/dev/vdc
A=/mnt/src/anc1 D=/mnt/src/anc1/data
res() { echo "$1 $2: $3"; }
# Progress on stdout (check.out; check.sh keeps only PASS/FAIL/LIMIT lines) and in $L/stages
stage() { echo "stage $(date +%T) $*"; [[ -d ${L:-} ]] && echo "$(date +%T) $*" >>$L/stages; true; }
stage start
# loop is a module on both kernels; on -uefi's generic kernel vfat, xfs and
# ext4 are too (mount loads them on its own; loading them here is harmless).
modprobe -a loop ext4 xfs vfat 2>/dev/null
# What the fixture needs of the guest: attr, acl, libcap, e2fsprogs,
# xfsprogs, dosfstools, util-linux, eudev and python3 are in series a, d and
# l (an assumption the first run confirms).
for c in python3 mkfs.ext4 dumpe2fs chattr lsattr mkswap sfdisk losetup setpriv blkid lsblk findmnt mountpoint blockdev udevadm \
         setfacl setfattr setcap sha256sum fallocate; do
    command -v $c >/dev/null || { res FAIL harness "the guest has no $c"; exit 2; }
done
# / by its device number: a kernel with no initrd (slackware15's huge one)
# mounts it as /dev/root, which mountinfo names.
rootdev=$(findmnt -n -o SOURCE /)
if [[ ! -b $rootdev || $rootdev == /dev/root ]]; then
    rootdev=/dev/$(basename "$(readlink -f "/sys/dev/block/$(mountpoint -d /)")")
fi
ROOTFS=$(findmnt -n -o FSTYPE /)
GDISK=/dev/$(lsblk -no PKNAME "$rootdev" | head -1)
case $ROOTFS in ext4|xfs) ;; *) res FAIL harness "/ is $ROOTFS on $rootdev, not ext4 or XFS"; exit 2 ;; esac
ESP=0
[[ $(findmnt -n -o FSTYPE /boot/efi 2>/dev/null) == vfat ]] && ESP=1
[[ $ROOTFS == xfs ]] && for c in mkfs.xfs xfs_info xfs_io; do command -v $c >/dev/null || { res FAIL harness "the guest has no $c"; exit 2; }; done
((ESP)) && { command -v mkfs.vfat >/dev/null || { res FAIL harness "the guest has no mkfs.vfat"; exit 2; }; }
swapdev=$(awk 'NR > 1 && $2 == "partition" {print $1; exit}' /proc/swaps)
[[ -b ${swapdev:-} ]] || swapdev=$(blkid -o device -t TYPE=swap "$GDISK"* 2>/dev/null | head -1)
espdev=""; ((ESP)) && espdev=$(findmnt -n -o SOURCE /boot/efi)
[[ -b $GDISK && $(blockdev --getsize64 $DISK) == "$(blockdev --getsize64 "$GDISK")" && $(blockdev --getsize64 $WD) == $((80 << 30)) ]] ||
    { res FAIL harness "vdb/vdc aren't the spare disks (the guest's disk ${GDISK:-?}): $(lsblk -dbno NAME,SIZE | paste -sd' ')"; exit 2; }
mkfs.ext4 -q -F $WD && mkdir -p $W && mount -o noatime $WD $W || { res FAIL harness "no work disk"; exit 2; }
mkdir -p $L $W/rt
export RT_TMP=$W/rt
# shellcheck source=/dev/null
. $B/rt-common.sh
st() { echo "$1 $2" >>$L/status; }
echo "$G" >$L/guest
echo "$ROOTFS $ESP" >$L/layout

stage layout
# --- What the guest has ------------------------------------------------------
pn() { cat "/sys/class/block/$(basename "$1")/partition"; }
RN=$(pn "$rootdev") SN=$(pn "${swapdev:-/dev/null}" 2>/dev/null) EN=""
((ESP)) && EN=$(pn "$espdev")
PTLABEL=$(sfdisk -d "$GDISK" 2>/dev/null | sed -n 's/^label: //p')
ROOTOPTS=$(awk '$1 !~ /^#/ && $2 == "/" {print $4}' /etc/fstab)
ESPOPTS=""; ((ESP)) && ESPOPTS=$(findmnt -n -o OPTIONS /boot/efi)
awk '$1 !~ /^#/ && ($2 == "/" || $2 == "/boot/efi" || $3 == "swap")' /etc/fstab >$L/fstab-guest
[[ -n $RN && -n $SN && -n $ROOTOPTS && -n $PTLABEL ]] && { ((!ESP)) || [[ -n $EN && -n $ESPOPTS ]]; } ||
    { res FAIL harness "can't read the guest's layout: $PTLABEL root $rootdev/$RN swap ${swapdev:-?}/$SN esp ${espdev:--}/$EN fstab / $ROOTOPTS ESP ${ESPOPTS:--}"; exit 2; }
# The kernel /boot/vmlinuz names (vmlinuz-huge-5.15.x or vmlinuz-generic-5.15.x)
KIMG=$(readlink /boot/vmlinuz) KVER=${KIMG#vmlinuz-*-} KFLAV=${KIMG#vmlinuz-}; KFLAV=${KFLAV%%-*}
[[ -n $KIMG && $KVER != "$KIMG" && -f /boot/$KIMG ]] || { res FAIL harness "no kernel: /boot/vmlinuz -> ${KIMG:-?}"; exit 2; }
echo "$KFLAV $KVER" >$L/kver
{ uname -r; cat /etc/slackware-version; . /etc/os-release; echo "$PRETTY_NAME ($ID $VERSION_ID)"
  echo "$PTLABEL: root $rootdev ($RN, $ROOTFS) swap ${swapdev:-} ($SN) esp ${espdev:--} (${EN:--}) disk $GDISK; fstab / $ROOTOPTS; ESP ${ESPOPTS:--}"
  echo "kernel /boot/vmlinuz -> $KIMG ($KFLAV $KVER); initrd: $(ls -d /boot/initrd.gz /boot/initrd-tree 2>/dev/null | paste -sd' ' || true)"
  echo "boot loader: $( ((ESP)) && echo "ELILO: $(ls /boot/efi/EFI/*/elilo.conf 2>/dev/null | paste -sd' ')" || echo "LILO: $(ls -l /etc/lilo.conf /boot/map 2>/dev/null | awk '{print $1, $NF}' | paste -sd' ')")"
  # pkgtools' entries are NAME-VERSION-ARCH-BUILD
  ls /var/lib/pkgtools/packages | grep -E '^(e2fsprogs|xfsprogs|dosfstools|util-linux|python3|attr|acl|libcap|lilo|elilo|mkinitrd|kernel-(huge|generic|modules)|pkgtools|sysvinit|sysvinit-scripts|aaa_base|glibc)-[0-9]' | paste -sd' '
  echo "pkgtools: $(ls /var/lib/pkgtools/packages | wc -l) packages; /var/log/packages -> $(readlink /var/log/packages || echo 'not a symlink')"
  echo "versions: $($RS version) | $($RU --version 2>&1 | head -1)"; } >$L/env.txt
cat /sys/kernel/security/lsm >$L/lsm 2>/dev/null; echo >>$L/lsm
extfeat() { dumpe2fs -h "$1" 2>/dev/null | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | paste -sd' '; }
extgeo() { dumpe2fs -h "$1" 2>/dev/null | grep -E '^(Inode size|Block size|Default mount options|Flex block group size|Default directory hash|Filesystem flags|Total journal size|Reserved block count):' | tr -s ' ' | paste -sd';'; }
xfsfeat() { xfs_info "$1" 2>/dev/null | grep -o '\(isize\|crc\|finobt\|sparse\|rmapbt\|reflink\|bigtime\|inobtcount\|nrext64\|ftype\|projid32bit\|attr\|lazy-count\|ascii-ci\|bsize\|sectsz\|agcount\|version\)=[0-9]*' | sort -u | paste -sd' '; }
rootfeat() {  # DEV-or-MOUNTPOINT: the features and geometry of / as made (ext4: the device; XFS: the mount)
    if [[ $ROOTFS == ext4 ]]; then extfeat "$1"; extgeo "$1"; else xfsfeat "$1"; fi
}
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
ptable() {  # DISK: label, first/last LBA and sector size, then each partition: start, size, type, name, attrs, bootable
    sfdisk -d "$1" | python3 -c '
import re, sys
ls = sys.stdin.read().splitlines()
hd = dict(l.split(": ", 1) for l in ls if ": " in l and not l.startswith("/"))
print(hd.get("label", "?"), hd.get("first-lba", "-"), hd.get("last-lba", "-"), hd.get("sector-size", "?"))
for l in ls:
    if l.startswith("/") and " : " in l:
        r = l.split(" : ", 1)[1]
        f = dict((k, v.strip().strip(chr(34))) for k, v in re.findall(r"(\w+)=\s*(\"[^\"]*\"|[^,]+)", r))
        boot = "bootable" if re.search(r"(^|,)\s*bootable\s*(,|$)", r) else "-"
        print(f.get("start"), f.get("size"), f.get("type"), f.get("name", "-"), f.get("attrs", "-"), boot)'
}
mkparts() {  # DISK-or-IMAGE: the guest's partition table (its label; on GPT its first/last LBA; partitions with their flags; fresh ids)
    sfdisk -d "$GDISK" | python3 -c '
import re, sys
ls = sys.stdin.read().splitlines()
for l in ls:  # the label; first and last usable LBA as on vda (sfdisk would start a new GPT at 2048)
    if l.startswith(("label:", "first-lba:", "last-lba:", "sector-size:")): print(l)
for l in ls:
    if l.startswith("/") and " : " in l:
        r = l.split(" : ", 1)[1]
        f = dict(re.findall(r"(\w+)=\s*(\"[^\"]*\"|[^,]+)", r))
        s = ["start=" + f["start"].strip(), "size=" + f["size"].strip(), "type=" + f["type"].strip()]
        s += ["name=" + f["name"]] if "name" in f else []
        s += ["attrs=" + f["attrs"]] if "attrs" in f else []
        s += ["bootable"] if re.search(r"(^|,)\s*bootable\s*(,|$)", r) else []
        print(", ".join(s))' | sfdisk -q --wipe always "$1" >>$L/sfdisk.log 2>&1
}
mopts() { findmnt -n -o OPTIONS "$1"; }
swapinfo() {  # PARTITION: "SIZE FSTYPE": its size and what blkid finds on it (swap, as the guest's)
    local t
    t=$(blkid -p -o value -s TYPE "$1" 2>/dev/null)
    echo "$(blockdev --getsize64 "$1") ${t:-none}"
}
if [[ $ROOTFS == ext4 ]]; then
    FEATS=$(extfeat "$rootdev" | tr ' ' ',')
    ISZ=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Inode size: *//p')
    BSZ=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Block size: *//p')
    JMB=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Total journal size: *\([0-9]*\)M$/\1/p')
    [[ -n $FEATS && -n $ISZ && -n $BSZ ]] || { res FAIL harness "can't read / ($rootdev)'s ext4 features"; exit 2; }
    MKROOT="mkfs.ext4 -b $BSZ -I $ISZ ${JMB:+-J size=$JMB} -O none,$FEATS"
else
    # XFS: the root's -m and -i options, as xfs_info shows them (xfsprogs
    # 5.13 knows these and no newer ones: nrext64, exchange, metadir, parent
    # are later).
    XF=$(xfsfeat /)
    xv() { tr ' ' '\n' <<<"$XF" | sed -n "s/^$1=//p" | head -1; }
    xm=""; for k in crc finobt rmapbt reflink bigtime inobtcount; do v=$(xv $k); [[ -n $v ]] && xm+="${xm:+,}$k=$v"; done
    xi="size=$(xv isize)"; v=$(xv sparse); [[ -n $v ]] && xi+=",sparse=$v"
    XFSO=(-m "$xm" -i "$xi")
    [[ -n $xm && $xi != size= ]] || { res FAIL harness "can't read /'s XFS features: $XF"; exit 2; }
    MKROOT="mkfs.xfs ${XFSO[*]}"
fi
if ((ESP)); then
    read -r EBPS ESPC ERES ENF EHID < <(python3 - "$espdev" <<'PY'
import struct, sys
b = open(sys.argv[1], "rb").read(512)
bps, spc, res, nf = struct.unpack_from("<HBHB", b, 11)
print(bps, spc, res, nf, struct.unpack_from("<I", b, 28)[0])
PY
)
    [[ ${EHID:-} =~ ^[0-9]+$ ]] || { res FAIL harness "can't read the guest's ESP geometry from $espdev"; exit 2; }
fi
ptable "$GDISK" >$L/pt-guest
rootfeat "$( [[ $ROOTFS == ext4 ]] && echo "$rootdev" || echo / )" >$L/feat-guest
((ESP)) && fatgeom "$espdev" >$L/fat-guest
{ echo "/ $(mopts /)"; ((ESP)) && echo "/boot/efi $(mopts /boot/efi)"; } >$L/mopts-guest
swapinfo "$swapdev" >$L/swap-guest
echo "$MKROOT; $( ((ESP)) && echo "mkfs.vfat -F 32 -S $EBPS -s $ESPC -R $ERES -f $ENF -h $EHID; ESP $ESPOPTS; ")/ $ROOTOPTS" >>$L/env.txt
((ESP)) && mkfs.vfat --help 2>&1 | head -1 >>$L/env.txt
[[ $ROOTFS == xfs ]] && mkfs.xfs -V >>$L/env.txt 2>&1
[[ $ROOTFS == ext4 ]] && mke2fs -V 2>&1 | head -1 >>$L/env.txt

stage scratch stack
# --- The scratch stack ---------------------------------------------------------
mkroot() {  # DEV-or-IMAGE: a file system made like the guest's /
    if [[ $ROOTFS == ext4 ]]; then
        mkfs.ext4 -q -F -b "$BSZ" -I "$ISZ" ${JMB:+-J size=$JMB} -O "none,$FEATS" "$1"
    else
        mkfs.xfs -q -f "${XFSO[@]}" "$1" || { echo "mkfs.xfs ${XFSO[*]} refused: defaults" >>$L/mkfs.log; mkfs.xfs -q -f "$1"; }
    fi
}
mkesp() {  # DEV: a FAT32 with the guest's ESP's geometry (a fresh volume id; no label, as the guest's)
    mkfs.vfat -F 32 -S "$EBPS" -s "$ESPC" -R "$ERES" -f "$ENF" -h "$EHID" "$1"
}
mkfeat() {  # IMAGE MOUNTPOINT: ubuntu26-uefi's ext4 with ext4's optional features
    truncate -s 256M "$1" && mkfs.ext4 -q -F -O project,casefold "$1" && mkdir -p "$2" && mount -o loop "$1" "$2"
}
mkstack() {  # PARTPREFIX ROOTMOUNT FEATIMAGE: PARTPREFIX{SN,RN,EN}: the swap made, / at ROOTMOUNT, the ESP and feat inside the tree
    local p=$1 r=$2 t=$2$D
    [[ $r == /mnt/src ]] && t=$D
    mkswap "${p}$SN" >>$L/mkfs.log 2>&1 && mkroot "${p}$RN" >>$L/mkfs.log 2>&1 || return 1
    if ((ESP)); then mkesp "${p}$EN" >>$L/mkfs.log 2>&1 || return 1; fi
    mkdir -p "$r" && mount -o "$ROOTOPTS" "${p}$RN" "$r" && mkdir -p "$t" || return 1
    if ((ESP)); then mkdir -p "$t/boot/efi" && mount -t vfat -o "$ESPOPTS" "${p}$EN" "$t/boot/efi" || return 1; fi
    if [[ $ROOTFS == ext4 ]]; then mkfeat "$3" "$t/feat" || return 1
    elif [[ $r == /mnt/src ]]; then mkdir -p "$t/feat"; fi
}
mkparts $DISK && udevadm settle && mkstack $DISK /mnt/src $W/srcfeat.img ||
    { res FAIL harness "no scratch stack on $DISK: $(tail -3 $L/sfdisk.log $L/mkfs.log 2>/dev/null | paste -sd' ')"; exit 2; }
ptable $DISK >$L/pt-src
rootfeat "$( [[ $ROOTFS == ext4 ]] && echo $DISK$RN || echo /mnt/src )" >$L/feat-src
((ESP)) && fatgeom $DISK$EN >$L/fat-src
{ echo "/ $(mopts /mnt/src)"; ((ESP)) && echo "/boot/efi $(mopts $D/boot/efi)"; } >$L/mopts-src
swapinfo $DISK$SN >$L/swap-src
ALL_MOUNTS=/mnt/src
((ESP)) && ALL_MOUNTS="$ALL_MOUNTS $D/boot/efi"
[[ $ROOTFS == ext4 ]] && ALL_MOUNTS="$ALL_MOUNTS $D/feat"
for m in $ALL_MOUNTS; do mount -o remount,strictatime "$m"; done

stage fixture: torture
# --- the fixture ------------------------------------------------------------
# torture.sh, but with 8 MiB of random bytes instead of 1 GiB.  Slackware
# has setfattr, setfacl and setcap; what torture.sh sets with the first two
# is set again from Python below (solus49's), so the fixture is solus49's.
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
# its set-id programs (root's, uucp's, daemon's, messagebus's), shadow,
# passwd, group, fstab, inittab, os-release, slackware-version, HOSTNAME,
# /etc/rc.d whole (rc.S, rc.M, rc.K, rc.6 and rc.0 -> rc.6, rc.inet1 and
# rc.inet1.conf, the services: on when executable, rc.sshd among them, off
# when not), slackpkg's config, pkgtools' database (/var/lib/pkgtools, and
# /var/log/packages, scripts and setup, its symlinks), syslog's files, /boot
# (but the ESP's mount point): LILO's lilo.conf and map and the huge kernel
# with its symlinks, or the generic kernel, initrd.gz and mkinitrd's
# initrd-tree (its device nodes, busybox links and command_line); files with
# capabilities (if any), hard-linked files and files with holes
# (ubuntu26-uefi's guest/ copies).
stage fixture: real files
n=0
cpreal() {  # PATH...: cp -a each to the tree at its own path (parents with the guest's metadata)
    local f d
    for f in "$@"; do
        [[ -e $f || -L $f ]] || { echo "real: missing $f" >>$L/fixture.err; continue; }
        d=$(dirname "$f"); mkdir -p "$D$d"
        cp -a --preserve=all "$f" "$D$d/" 2>>$L/fixture.err && n=$((n + 1)) && echo "real: $f" >>$L/real.txt
    done
}
cpopt() {  # PATH...: as cpreal, for files a Slackware install may not have yet (logs, LILO's saved boot sector): noted, not missed
    local f
    for f in "$@"; do
        if [[ -e $f || -L $f ]]; then cpreal "$f"; else echo "real (optional): none: $f" >>$L/env.txt; fi
    done
}
SETID=$(find / -xdev -type f -perm /6000 2>/dev/null | sort)
CAPS=$(python3 - <<'PY'
import os
out = []
for top in ["/usr", "/etc", "/opt", "/bin", "/sbin"]:
    for d, ds, fs in os.walk(top):
        for f in fs:
            p = os.path.join(d, f)
            try:
                if not os.path.islink(p) and "security.capability" in os.listxattr(p): out.append(p)
            except OSError: pass
print(" ".join(out))
PY
)
echo "set-id programs: $(echo "$SETID" | wc -w): $(echo $SETID)" >>$L/env.txt
echo "files with capabilities: ${CAPS:-none}" >>$L/env.txt
# shellcheck disable=SC2086
cpreal $SETID $CAPS /etc/shadow /etc/passwd /etc/group /etc/fstab /etc/inittab /etc/os-release /etc/slackware-version \
       /etc/rc.d /var/lib/pkgtools /var/log/packages
cpopt /etc/gshadow /etc/HOSTNAME /etc/hosts /etc/slackpkg /etc/lilo.conf /etc/mkinitrd.conf /var/log/scripts /var/log/setup \
      /var/log/messages /var/log/syslog /var/log/secure /var/log/wtmp /var/log/lastlog /var/log/dmesg
mkdir -p $D/boot
for f in /boot/* /boot/.[!.]*; do
    [[ -e $f || -L $f ]] || continue
    [[ $f == /boot/efi ]] && continue   # the ESP's mount point: the ESP is its own file system, below
    cpreal "$f"
done
echo "rc.d: $(find /etc/rc.d -maxdepth 1 -name 'rc.*' -type f -perm -u+x | wc -l) executable, $(find /etc/rc.d -maxdepth 1 -name 'rc.*' -type f ! -perm -u+x | wc -l) not: off $(find /etc/rc.d -maxdepth 1 -name 'rc.*' -type f ! -perm -u+x | sed 's|.*/||' | paste -sd' ')" >>$L/env.txt
echo "pkgtools DB: $(du -sh /var/lib/pkgtools | cut -f1), $(ls /var/lib/pkgtools/packages | wc -l) packages, $(ls /var/lib/pkgtools/scripts 2>/dev/null | wc -l) scripts" >>$L/env.txt
ref() { chmod --reference="$2" "$1"; chown --reference="$2" "$1"; touch -h --reference="$2" "$1"; }
for p in /bin /sbin /etc /usr /usr/bin /usr/sbin /usr/libexec /usr/lib64 /var /var/lib /var/log /boot; do
    [[ -d $D$p && ! -L $D$p ]] && ref $D$p $p
done
G2=$D/guest; mkdir -p $G2
for f in $(find /usr /etc -xdev -type f -links +1 2>/dev/null | head -2) \
         $(find / -xdev -path /mnt -prune -o -type f -size +0 -printf '%b %s %p\n' 2>/dev/null | awk '$1*512 < $2 {print $3}' | grep -v -e '^/root/' -e '^/proc/' | head -5); do
    [ -e "$f" ] || continue
    mkdir -p "$G2$(dirname "$f")"
    cp -a --sparse=always "$f" "$G2$f" 2>>$L/fixture.err && n=$((n+1)) && echo "guest file: $f" >>$L/real.txt
done
echo "real files copied: $n" >>$L/env.txt
stage fixture: inode items
# Owners with no name, big ids, rdev 0, more inode flags (ubuntu26-uefi's;
# those the file system doesn't keep are noted and left out)
mkdir -p $D/noname-dir; chown 4321:8765 $D/noname-dir; touch $D/noname-dir/f; chown 54321:65432 $D/noname-dir/f
touch $D/bigid; chown 3000000000:3000000001 $D/bigid
mknod $D/rdev0-char c 0 0; mknod $D/rdev0-block b 0 0
for fl in S A j s u c t T x; do
    echo $fl >$D/flag-$fl; chattr +$fl $D/flag-$fl 2>>$L/torture.err
    [[ $(lsattr -d $D/flag-$fl 2>/dev/null | cut -d' ' -f1) == *"$fl"* ]] && echo "flag $fl kept" >>$L/env.txt || { echo "flag $fl not kept on $ROOTFS" >>$L/env.txt; rm -f $D/flag-$fl; }
done
mkdir $D/dirsync-dir; chattr +D $D/dirsync-dir
mkdir $D/topdir; chattr +T $D/topdir 2>>$L/torture.err
echo ai >$D/append-nodump; chattr +a +d $D/append-nodump
mkdir $D/immutable-dir; echo in >$D/immutable-dir/inside; chattr +i $D/immutable-dir
echo ap >$D/append-only2; chattr +a $D/append-only2
mkdir -p $D/empty-deep/a/b/c/d/e/f/g
# Project IDs: on slackware15 the feature ext4 (with casefolding), on
# -uefi the XFS itself (projid32bit: an ID above 2^31), with XFS's own
# per-file options (gentoo's: extent size hints on a file and on a
# directory its new files inherit, nodefrag, filestream).
F=$D/feat
echo p >$F/projid-file; chattr -p 42 $F/projid-file
mkdir $F/projid-dir; chattr +P -p 43 $F/projid-dir; echo inh >$F/projid-dir/inherits; mkdir $F/projid-dir/sub
echo i >$F/immutable-projid; chattr -p 45 $F/immutable-projid; chattr +i $F/immutable-projid
truncate -s 1M $F/sparse-projid; echo e >>$F/sparse-projid; chattr -p 46 $F/sparse-projid
xfail() { "$@" 2>/dev/null || echo "fixture: skipped: $*" >>$L/fixture.err; }
if [[ $ROOTFS == ext4 ]]; then
    mkdir $F/casefold-dir; chattr +F $F/casefold-dir; echo m >$F/casefold-dir/MixedCase; mkdir $F/casefold-dir/SubDir
    mkdir $F/casefold-dir/SubDir/nested
    mkdir $F/casefold-projid; chattr +F +P -p 44 $F/casefold-projid; echo x >$F/casefold-projid/Both
else
    echo big >$F/projid-big; xfail chattr -p 4000000000 $F/projid-big
    touch $F/xfs-extsize; xfail xfs_io -c "extsize 1m" $F/xfs-extsize; head -c 64K /dev/urandom >>$F/xfs-extsize
    mkdir $F/xfs-extsize-dir; xfail xfs_io -c "extsize 2m" $F/xfs-extsize-dir; head -c 64K /dev/urandom >$F/xfs-extsize-dir/inherits
    echo n >$F/xfs-nodefrag; xfail xfs_io -c "chattr +f" $F/xfs-nodefrag
    mkdir $F/xfs-filestream; xfail xfs_io -c "chattr +S" $F/xfs-filestream
fi
lsattr -p -R $F >>$L/env.txt 2>&1
# XFS's own per-file options (fsxattr xflags, extent size hints) of feat's xfs-* paths
xfsx() {  # TREE OUT
    [[ $ROOTFS == xfs ]] || { : >$2; return; }
    for p in xfs-extsize xfs-extsize-dir xfs-extsize-dir/inherits xfs-nodefrag xfs-filestream; do
        printf '%s ' $p; xfs_io -r -c stat "$1/feat/$p" 2>&1 | grep -E 'fsxattr\.(xflags|extsize|cowextsize)' | tr -s ' \n' '  '; echo
    done >$2
}
# The ESP (-uefi): the guest's own (ELILO in EFI/Slackware: elilo.efi,
# elilo.conf, vmlinuz, initrd.gz; the same four in EFI/BOOT with elilo.efi
# as BOOTX64.EFI) at its own paths with its times and FAT attributes, the
# sha256 of each as the guest has it; then FAT's own: hidden, system,
# archive set and cleared, read-only by chmod and by attribute, on files and
# dirs; long, case-mixed and UTF-8 names; deep, empty and big dirs; FAT's
# time limits.
E=$D/boot/efi
fat() {  # PATH ATTRS: FAT_IOCTL_SET_ATTRIBUTES
    python3 -c 'import fcntl,os,struct,sys; fd=os.open(sys.argv[1],os.O_RDONLY); fcntl.ioctl(fd,0x40047211,struct.pack("I",int(sys.argv[2],0))); os.close(fd)' "$1" "$2" ||
        echo "fixture: skipped: FAT attributes $2 on $1" >>$L/fixture.err
}
if ((ESP)); then
stage fixture: the ESP
python3 - /boot/efi $E $L/esp-guest.sha256 <<'PY' >>$L/env.txt 2>>$L/fixture.err || echo "fixture: skipped: the copy of the guest's ESP" >>$L/fixture.err
import fcntl, hashlib, os, shutil, sys
a, b, sums = sys.argv[1:4]
copied, dirs = [], []
for dp, dns, fns in os.walk(a):
    rel = os.path.relpath(dp, a)
    q = b if rel == "." else os.path.join(b, rel)
    for n in sorted(dns):
        os.mkdir(os.path.join(q, n))
    for n in sorted(fns):
        p = os.path.join(dp, n)
        shutil.copyfile(p, os.path.join(q, n))
        st = os.stat(p)
        os.utime(os.path.join(q, n), ns=(st.st_atime_ns, st.st_mtime_ns))
        copied.append((p, os.path.join(q, n)))
    if rel != ".":
        dirs.append((dp, q))
for p, q in reversed(dirs):  # dirs' times once their contents are in (FAT's root has none)
    st = os.stat(p)
    os.utime(q, ns=(st.st_atime_ns, st.st_mtime_ns))
for p, q in copied + dirs:  # then the FAT attributes, as the guest's
    fd = os.open(p, os.O_RDONLY); v = fcntl.ioctl(fd, 0x80047210, b"\0" * 4); os.close(fd)
    fd = os.open(q, os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, v); os.close(fd)
def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()
with open(sums, "w") as f:  # what the guest's ESP holds, to judge the copy by
    for p, q in copied:
        f.write(f"{sha256(p)}  boot/efi/{os.path.relpath(p, a)}\n")
print(f"esp: {len(copied)} files and {len(dirs)} dirs copied from the guest's ESP")
PY
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
# A UTF-8 name needs the mount's utf8 (or iocharset=utf8); Slackware's
# kernels default to utf8 (CONFIG_FAT_DEFAULT_UTF8=y), whether or not the
# options show it.
if [[ ,$ESPOPTS, == *,utf8,* || ,$ESPOPTS, == *,iocharset=utf8,* ]]; then
    : >"café-ünïcode" || echo "fixture: skipped: a UTF-8 name" >>$L/fixture.err
else
    : >"café-ünïcode" 2>/dev/null && echo "esp: a UTF-8 name made without utf8 in the options ($ESPOPTS)" >>$L/env.txt ||
        echo "esp: no UTF-8 name: the ESP is mounted without utf8 ($ESPOPTS)" >>$L/env.txt
fi
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
fi
# The directory above the backed-up path, with metadata of its own
chown 4321:8765 $A; chmod 2751 $A
python3 - $A <<'PY'
import os, struct, sys
X = 0xffffffff
os.setxattr(sys.argv[1], "user.ancestor", b"anc"); os.setxattr(sys.argv[1], "trusted.ancestor", b"t")
os.setxattr(sys.argv[1], "system.posix_acl_access", struct.pack("<I", 2) + b"".join(
    struct.pack("<HHI", t, p, i) for t, p, i in [(1, 7, X), (2, 5, 1234), (4, 5, X), (0x10, 5, X), (0x20, 1, X)]))
PY
# $D/boot/efi is the FAT's root, which has no times: left as it is.
touch -h -m -d '2004-05-06 07:08:09.123456789' $A $D $F
touch -h -a -d '2005-06-07 08:09:10.987654321' $A $D $F
sync
noatime() { for m in $ALL_MOUNTS; do mount -o remount,noatime "$m"; done; }
strictatime() { for m in $ALL_MOUNTS; do mount -o remount,strictatime "$m"; done; }
rec() { python3 $B/fsmeta.py record "$1" "$2" --skip root/bko-torture/socket; }
cmp() { python3 $B/fsmeta.py compare "$1" "$2" --json "$3" >"$3.txt" 2>&1; }
fatx() { if ((ESP)); then python3 $B/fatattr.py "$1" "$2"; else echo '{}' >"$2"; fi; }   # ESP OUT
stage record source
noatime
rec $D $L/src0.json
fatx $E $L/src.fat.json
xfsx $D $L/src.xfsx
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
    stage backup $t; s=$(bk $bin $W/repo-$t); st backup-$t $s
done
for t in restic rustic; do
    bin=$RS; [ $t = rustic ] && bin=$RU
    rec $D $L/src-before-atime-$t.json
    stage backup strictatime $t; strictatime; s=$(bk $bin $W/repo-atime-$t); noatime
    st backup-atime-$t $s
    rec $D $L/src-after-atime-$t.json
done

stage nodes and checks
# --- same repository format ---------------------------------------------------
rt_nodes $RS $W/repo-restic $D >$L/nodes.restic 2>$L/nodes.err
rt_nodes $RS $W/repo-rustic $D >$L/nodes.rustic 2>>$L/nodes.err
rt_node_diff $L/nodes.restic $L/nodes.rustic >$L/nodes.diff 2>&1; st nodes $?
$RS -r $W/repo-rustic check --read-data >$L/check-restic-on-rustic.log 2>&1; st check-restic-on-rustic $?
$RU --no-progress --no-cache -r $W/repo-restic check --read-data >$L/check-rustic-on-restic.log 2>&1; st check-rustic-on-restic $?
# The ancestor (anc1): both tools store its lstat; compared node by node.
rt_nodes $RS $W/repo-restic $A >$L/anc.restic 2>/dev/null; rt_nodes $RS $W/repo-rustic $A >$L/anc.rustic 2>/dev/null
awk -F '\t' '$1 == "."' $L/anc.restic >$L/anc1.restic; awk -F '\t' '$1 == "."' $L/anc.rustic >$L/anc1.rustic

# --- restores in four directions --------------------------------------------
# Each target: a disk image laid out as the guest's disk, its file systems
# made and mounted as the source's (as BackOtter mounts a machine's file
# systems before it restores).
restore() {  # TOOL REPO TARGET
    if [[ $1 == *rustic* ]]; then $1 --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $1 -r $2 restore -q latest --target $3; fi
}
remount_noatime() {  # TARGET: its mounts noatime, as the source's when recorded
    local m
    for m in $1 $( ((ESP)) && echo $1$D/boot/efi) $([[ $ROOTFS == ext4 ]] && echo $1$D/feat); do mount -o remount,noatime $m; done
}
DIRS="restic-restic rustic-rustic restic-rustic rustic-restic"
for d in $DIRS; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    t=/mnt/t-$d
    truncate -s "$(blockdev --getsize64 "$GDISK")" $W/$d.img && mkparts $W/$d.img && lo=$(losetup -P --show -f $W/$d.img) && udevadm settle &&
        mkstack ${lo}p $t $W/$d-feat.img || { res FAIL harness "no target stack for $d: $(tail -3 $L/mkfs.log | paste -sd' ')"; continue; }
    echo "$d $lo" >>$L/loops
    ptable $W/$d.img >$L/pt-$d
    swapinfo ${lo}p$SN >$L/swap-$d
    stage restore $d; restore $bin $W/repo-$b $t >$L/$d.restore 2>&1; st restore-$d $?
    sync; remount_noatime $t
    rec $t$D $L/$d.json
    fatx $t$E $L/$d.fat.json
    xfsx $t$D $L/$d.xfsx
    cmp $L/src0.json $L/$d.json $L/$d.cmp.json
    anc_stat $t$A >$L/$d.anc
    ((ESP)) && findmnt -n -o SOURCE,FSTYPE --target $t$E/. >$L/$d.espmount
    [ -e $t$T/socket ] && echo "socket restored" >>$L/$d.sock
done

# --- restore again over the restored tree, its immutable/append-only files
# (and LILO's map and lilo.conf, or the ESP's elilo.efi, elilo.conf and FAT
# attributes) changed
for d in restic-restic rustic-rustic; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    t=/mnt/t-$d$D
    [[ -d $t/feat ]] || continue
    chattr -i $t/root/bko-torture/immutable; echo changed >$t/root/bko-torture/immutable; chattr +i $t/root/bko-torture/immutable
    chattr -a $t/root/bko-torture/append-only; echo more >>$t/root/bko-torture/append-only; chattr +a $t/root/bko-torture/append-only
    chattr -i $t/feat/immutable-projid; chmod 0600 $t/feat/immutable-projid; chattr +i $t/feat/immutable-projid
    chattr -i $t/immutable-dir; echo new >$t/immutable-dir/inside; chmod 0700 $t/immutable-dir; chattr +i $t/immutable-dir
    echo changed >$t/root/bko-torture/empty
    if ((ESP)); then
        python3 - $t/boot/efi <<'PY' >>$L/$d.over.before 2>&1
import fcntl, os, struct, sys
E = sys.argv[1]
def setfat(p, v):
    fd = os.open(os.path.join(E, p), os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, struct.pack("I", v)); os.close(fd)
setfat("readonly-attr", 0x20)
open(os.path.join(E, "readonly-attr"), "w").write("changed-contents\n")
setfat("readonly-attr", 0x21)
for p, v in (("hidden", 0x20), ("archive-cleared", 0x20), ("system-dir", 0x10), ("readonly-dir", 0x10)):
    setfat(p, v)
open(os.path.join(E, "EFI/Slackware/elilo.efi"), "r+b").write(b"changed")
open(os.path.join(E, "EFI/Slackware/elilo.conf"), "a").write("delay=9\n")
print("FAT attributes changed before the restore over: readonly-attr (contents), hidden, archive-cleared, system-dir, readonly-dir; "
      "elilo.efi overwritten, elilo.conf appended to")
PY
    else
        { [[ -f $t/boot/map ]] && printf changed | dd of=$t/boot/map conv=notrunc status=none && echo "boot/map overwritten"
          [[ -f $t/etc/lilo.conf ]] && echo "timeout = 90" >>$t/etc/lilo.conf && echo "etc/lilo.conf appended to"; } >>$L/$d.over.before 2>&1
    fi
    stage restore over $d; restore $bin $W/repo-$b /mnt/t-$d >$L/$d.over 2>&1; st restore-over-$d $?
    sync
    rec $t $L/$d.over.json
    fatx $t/boot/efi $L/$d.over.fat.json
    xfsx $t $L/$d.over.xfsx
    cmp $L/src0.json $L/$d.over.json $L/$d.over.cmp.json
done

stage non-root
# --- one non-root run (uid 4321 backs up its own tree and restores it) -------
truncate -s 1G $W/nonroot.img && mkroot $W/nonroot.img >>$L/mkfs.log 2>&1 && mkdir -p /mnt/nonroot && mount -o loop,$ROOTOPTS $W/nonroot.img /mnt/nonroot ||
    res FAIL harness "no $ROOTFS for the non-root run"
N=/mnt/nonroot; mkdir -p $N/src $N/out; chown 4321:4321 $N $N/src $N/out
# /root is 0710 or tighter: the user runs copies of the binaries and nracl.py
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
df -m /mnt/src $( ((ESP)) && echo $E) $F /mnt/t-* $N $W >$L/df.txt 2>&1
stage judge
python3 $B/judge.py $L

# --- the logs for the host ------------------------------------------------------
tar -C $L -cf /root/bk/logs.tar .
exit 0
