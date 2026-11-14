#!/bin/bash
# SticTest1 solus49, the part that runs in the throwaway guest as root, from
# /root/bk (restic-bko, rustic-bko, fsmeta.py, rt-common.sh, torture.sh,
# judge.py, fatattr.py and nracl.py beside it).  kali's inner.sh with the
# Solus layout.  Prints "PASS|FAIL|LIMIT need: detail" lines; the records in
# /mnt/bkw/logs, tarred to /root/bk/logs.tar for the host.
#
# The ESP isn't in fstab: clr-boot-manager mounts it at /boot when it needs
# it.  This mounts it first (`clr-boot-manager mount-boot`, else a plain
# `mount ESP /boot` as clr-boot-manager does it), then reads the guest's
# layout.  The spare disk (vdb, the guest's vda's size) laid out from the
# guest's own `sfdisk -d` (every partition's start, size, type and name;
# fresh ids), its file systems made like the guest's: the ESP (the guest's
# mkfs.vfat, with the guest's ESP's geometry), the BIOS boot partition left
# unformatted, the ext4 (the guest's /'s features, inode and block size,
# journal), mounted with the guest's options: the ext4 at /mnt/src with
# fstab's (like /), the ESP inside the backed-up path at tree/boot with the
# options clr-boot-manager's mount has, and ubuntu26-uefi's ext4 with project
# quotas and casefolding (loop) at tree/feat.  The fixture (torture.sh and
# ubuntu26-uefi's items, the guest's own files at their own paths) on each.
# Four directions, each into a disk image (vdc) laid out and mounted the
# same, made fresh; each tool restoring over its own restore with changed
# immutable/append-only files and FAT attributes; each tool's check
# --read-data of the other's repository; a backup pair under strictatime;
# one non-root run (uid 4321's own tree) on an ext4 made like /; judge.py.
#
# Only the current kernel's (default-current's) image, initrd and System.map
# are copied, from the ESP and from /usr/lib64/kernel; an older kernel's are
# left out (env.txt names them) to keep the RAM disk small.  Its cmdline and
# config files and its loader entry are copied.
set -u
B=/root/bk RS=/root/bk/restic-bko RU=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
DISK=/dev/vdb WD=/dev/vdc
A=/mnt/src/anc1 D=/mnt/src/anc1/data
ESPTYPE=c12a7328-f81f-11d2-ba4b-00a0c93ec93b BIOSTYPE=21686148-6449-6e6f-744e-656564454649
res() { echo "$1 $2: $3"; }
# Progress on stdout (check.out; check.sh keeps only PASS/FAIL/LIMIT lines) and in $L/stages
stage() { echo "stage $(date +%T) $*"; [[ -d ${L:-} ]] && echo "$(date +%T) $*" >>$L/stages; true; }
stage start
# What the fixture needs of the guest (attr, acl, e2fsprogs, dosfstools,
# util-linux and python3 are in the build's package list: an assumption the
# first run confirms).
for c in python3 mkfs.ext4 dumpe2fs chattr lsattr mkfs.vfat sfdisk losetup setpriv blkid lsblk findmnt udevadm; do
    command -v $c >/dev/null || { res FAIL harness "the guest has no $c"; exit 2; }
done
rootdev=$(findmnt -n -o SOURCE /)
GDISK=/dev/$(lsblk -no PKNAME "$rootdev")
ESPHOW="already mounted"
if ! mountpoint -q /boot; then
    if clr-boot-manager mount-boot >/root/bk/cbm-mount.log 2>&1 && mountpoint -q /boot; then
        ESPHOW="clr-boot-manager mount-boot"
    else
        e=$(lsblk -prno NAME,PARTTYPE "$GDISK" | awk -v t=$ESPTYPE 'tolower($2) == t {print $1; exit}')
        [[ -n $e ]] && mount "$e" /boot && ESPHOW="mount $e /boot (clr-boot-manager mount-boot: $(tail -1 /root/bk/cbm-mount.log))"
    fi
fi
[[ $(findmnt -n -o FSTYPE /boot) == vfat ]] || { res FAIL harness "the ESP isn't mounted at /boot ($ESPHOW): $(findmnt -n /boot)"; exit 2; }
espdev=$(findmnt -n -o SOURCE /boot)
[[ $(blockdev --getsize64 $DISK) == "$(blockdev --getsize64 "$GDISK")" && $(blockdev --getsize64 $WD) == $((80 << 30)) ]] ||
    { res FAIL harness "vdb/vdc aren't the spare disks: $(lsblk -dbno NAME,SIZE | paste -sd' ')"; exit 2; }
mkfs.ext4 -q -F $WD && mkdir -p $W && mount -o noatime $WD $W || { res FAIL harness "no work disk"; exit 2; }
mkdir -p $L $W/rt
export RT_TMP=$W/rt
# shellcheck source=/dev/null
. $B/rt-common.sh
st() { echo "$1 $2" >>$L/status; }
echo "$ESPHOW" >$L/esp-how
cp /root/bk/cbm-mount.log $L/ 2>/dev/null

stage layout
# --- What the guest has ------------------------------------------------------
pn() { cat "/sys/class/block/$(basename "$1")/partition"; }
biosdev=$(lsblk -prno NAME,PARTTYPE "$GDISK" | awk -v t=$BIOSTYPE 'tolower($2) == t {print $1; exit}')
RN=$(pn "$rootdev") EN=$(pn "$espdev") BN=$(pn "${biosdev:-/dev/null}" 2>/dev/null)
ROOTOPTS=$(awk '$1 !~ /^#/ && $2 == "/" {print $4}' /etc/fstab)
ESPOPTS=$(findmnt -n -o OPTIONS /boot)
awk '$1 !~ /^#/ && $2 ~ /^\/boot/' /etc/fstab >$L/fstab-boot
[[ -n $RN && -n $EN && -n $BN && -n $ROOTOPTS && -n $ESPOPTS ]] ||
    { res FAIL harness "can't read the guest's layout: root $rootdev/$RN esp $espdev/$EN bios ${biosdev:-?}/$BN fstab / $ROOTOPTS /boot $ESPOPTS"; exit 2; }
KCUR=$(readlink /usr/lib64/kernel/default-current) KVER=${KCUR#com.solus-project.*.}
[[ -n $KCUR && $KVER != "$KCUR" && -f /usr/lib64/kernel/$KCUR ]] ||
    { res FAIL harness "no current kernel: /usr/lib64/kernel/default-current -> ${KCUR:-?}"; exit 2; }
echo "$KVER" >$L/kver
{ uname -r; . /etc/os-release 2>/dev/null || . /usr/lib64/os-release; echo "$PRETTY_NAME ($ID $VERSION_ID)"
  echo "root $rootdev ($RN) esp $espdev ($EN) bios ${biosdev:-} ($BN) disk $GDISK; fstab / $ROOTOPTS; ESP at /boot by $ESPHOW: $ESPOPTS"
  echo "fstab's /boot lines: $(paste -sd';' $L/fstab-boot)"
  echo "current kernel $KCUR ($KVER); usr/lib -> $(readlink /usr/lib || echo 'not a symlink')"
  # eopkg's package dirs are NAME-VERSION-RELEASE
  ls /var/lib/eopkg/package | grep -E '^(e2fsprogs|util-linux|python3|attr|acl|dosfstools|libcap2|clr-boot-manager|shim|systemd|eopkg|linux-current)-[0-9]' | paste -sd' '
  echo "versions: $($RS version) | $($RU --version 2>&1 | head -1)"; } >$L/env.txt
cat /sys/kernel/security/lsm >$L/lsm; echo >>$L/lsm
extfeat() { dumpe2fs -h "$1" 2>/dev/null | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | paste -sd' '; }
extgeo() { dumpe2fs -h "$1" 2>/dev/null | grep -E '^(Inode size|Block size|Default mount options|Flex block group size|Default directory hash|Filesystem flags|Total journal size|Reserved block count):' | tr -s ' ' | paste -sd';'; }
fatgeom() {  # DEV: "FAT32 bps=512 spc=8 nfats=2 reserved=32 hidden=4096"
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
gpt() {  # DISK: label, first-lba, last-lba, then each partition: start, size, type, name
    sfdisk -d "$1" | python3 -c '
import re, sys
ls = sys.stdin.read().splitlines()
hd = dict(l.split(": ", 1) for l in ls if ": " in l and not l.startswith("/"))
print(hd.get("label", "?"), hd.get("first-lba", "?"), hd.get("last-lba", "?"), hd.get("sector-size", "?"))
for l in ls:
    if l.startswith("/") and " : " in l:
        f = dict((k, v.strip().strip(chr(34))) for k, v in re.findall(r"(\w+)=\s*(\"[^\"]*\"|[^,]+)", l.split(" : ", 1)[1]))
        print(f.get("start"), f.get("size"), f.get("type"), f.get("name", "-"), f.get("attrs", "-"))'
}
mkparts() {  # DISK-or-IMAGE: the guest's GPT (its first/last LBA, partitions; fresh ids)
    sfdisk -d "$GDISK" | python3 -c '
import re, sys
ls = sys.stdin.read().splitlines()
ps = [l for l in ls if l.startswith("/") and " : " in l]
print("label: gpt")
for l in ls:  # first and last usable LBA as on vda (sfdisk would start a new table at 2048)
    if l.startswith(("first-lba:", "last-lba:", "sector-size:")): print(l)
for l in ps:
    f = dict(re.findall(r"(\w+)=\s*(\"[^\"]*\"|[^,]+)", l.split(" : ", 1)[1]))
    s = ["start=" + f["start"].strip(), "size=" + f["size"].strip(), "type=" + f["type"].strip()]
    s += ["name=" + f["name"]] if "name" in f else []
    s += ["attrs=" + f["attrs"]] if "attrs" in f else []
    print(", ".join(s))' | sfdisk -q --wipe always "$1" >>$L/sfdisk.log 2>&1
}
mopts() { findmnt -n -o OPTIONS "$1"; }
biosinfo() {  # PARTITION: "SIZE FSTYPE NONZERO": its size, file system (none: unformatted, as the guest's), non-zero bytes in its first MiB
    local t
    t=$(blkid -p -o value -s TYPE "$1" 2>/dev/null)
    echo "$(blockdev --getsize64 "$1") ${t:-none} $(head -c 1048576 "$1" | tr -d '\0' | wc -c)"
}
FEATS=$(extfeat "$rootdev" | tr ' ' ',')
ISZ=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Inode size: *//p')
BSZ=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Block size: *//p')
JMB=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Total journal size: *\([0-9]*\)M$/\1/p')
read -r EBPS ESPC ERES ENF EHID < <(python3 - "$espdev" <<'PY'
import struct, sys
b = open(sys.argv[1], "rb").read(512)
bps, spc, res, nf = struct.unpack_from("<HBHB", b, 11)
print(bps, spc, res, nf, struct.unpack_from("<I", b, 28)[0])
PY
)
[[ ${EHID:-} =~ ^[0-9]+$ ]] || { res FAIL harness "can't read the guest's ESP geometry from $espdev"; exit 2; }
gpt "$GDISK" >$L/gpt-guest
extfeat "$rootdev" >$L/feat-guest; extgeo "$rootdev" >>$L/feat-guest
fatgeom "$espdev" >$L/fat-guest
{ echo "/ $(mopts /)"; echo "/boot $(mopts /boot)"; } >$L/mopts-guest
biosinfo "$biosdev" >$L/bios-guest
echo "mkfs.ext4 -b $BSZ -I $ISZ -J size=$JMB -O none,$FEATS; mkfs.vfat -F 32 -S $EBPS -s $ESPC -R $ERES -f $ENF -h $EHID; / $ROOTOPTS; ESP $ESPOPTS" >>$L/env.txt
mkfs.vfat --help 2>&1 | head -1 >>$L/env.txt

stage scratch stack
# --- The scratch stack ---------------------------------------------------------
mkroot() {  # DEV-or-IMAGE: an ext4 made like the guest's /
    mkfs.ext4 -q -F -b "$BSZ" -I "$ISZ" ${JMB:+-J size=$JMB} -O "none,$FEATS" "$1"
}
mkesp() {  # DEV: a FAT32 with the guest's ESP's geometry (a fresh volume id; no label, as the guest's)
    mkfs.vfat -F 32 -S "$EBPS" -s "$ESPC" -R "$ERES" -f "$ENF" -h "$EHID" "$1"
}
mkfeat() {  # IMAGE MOUNTPOINT: ubuntu26-uefi's ext4 with ext4's optional features
    truncate -s 256M "$1" && mkfs.ext4 -q -F -O project,casefold "$1" && mkdir -p "$2" && mount -o loop "$1" "$2"
}
mkstack() {  # PARTPREFIX ROOTMOUNT FEATIMAGE: the ESP and the ext4 on PARTPREFIX{EN,RN} (BIOS boot left alone); the ext4 at ROOTMOUNT, ESP and feat inside the tree
    local p=$1 r=$2 t=$2$D
    [[ $r == /mnt/src ]] && t=$D
    mkesp "${p}$EN" >>$L/mkfs.log 2>&1 && mkroot "${p}$RN" >>$L/mkfs.log 2>&1 || return 1
    mkdir -p "$r" && mount -o "$ROOTOPTS" "${p}$RN" "$r" && mkdir -p "$t/boot" || return 1
    mount -t vfat -o "$ESPOPTS" "${p}$EN" "$t/boot" && mkfeat "$3" "$t/feat"
}
mkparts $DISK && udevadm settle && mkstack $DISK /mnt/src $W/srcfeat.img ||
    { res FAIL harness "no scratch stack on $DISK: $(tail -3 $L/sfdisk.log $L/mkfs.log | paste -sd' ')"; exit 2; }
gpt $DISK >$L/gpt-src
extfeat $DISK$RN >$L/feat-src; extgeo $DISK$RN >>$L/feat-src
fatgeom $DISK$EN >$L/fat-src
{ echo "/ $(mopts /mnt/src)"; echo "/boot $(mopts $D/boot)"; } >$L/mopts-src
biosinfo $DISK$BN >$L/bios-src
ALL_MOUNTS="/mnt/src $D/boot $D/feat"
for m in $ALL_MOUNTS; do mount -o remount,strictatime "$m"; done

stage fixture: torture
# --- the fixture ------------------------------------------------------------
# torture.sh, but with 8 MiB of random bytes instead of 1 GiB.  What
# torture.sh sets with setfattr/setfacl is set again from Python below (as
# on kali, which has neither), so the fixture is the same either way.
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
# /usr/lib -> lib64 first (the paths below go through lib64), its set-id
# programs, shadow, passwd, group, fstab, os-release, the kernel command
# line's drop-ins, /usr/lib64/kernel (the current kernel's files, every
# cmdline and config, the symlinks, cmdline.d), eopkg's DB, tester's home
# (with /home's metadata), the journal with its ACLs if the guest keeps one;
# files with capabilities (if any), hard-linked files and files with holes
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
cpopt() {  # PATH...: as cpreal, for files a Solus install may not have (stateless /etc): noted, not missed
    local f
    for f in "$@"; do
        if [[ -e $f || -L $f ]]; then cpreal "$f"; else echo "real (optional): none: $f" >>$L/env.txt; fi
    done
}
mkdir -p $D/usr
if [[ -L /usr/lib ]]; then cp -a /usr/lib $D/usr/ 2>>$L/fixture.err && n=$((n + 1)) && echo "real: /usr/lib -> $(readlink /usr/lib)" >>$L/real.txt
else echo "usr/lib: a directory, not a symlink" >>$L/env.txt; fi
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
echo "set-id programs: $(echo "$SETID" | wc -w): $(echo $SETID)" >>$L/env.txt
echo "files with capabilities: ${CAPS:-none}" >>$L/env.txt
# shellcheck disable=SC2086
cpreal $SETID $CAPS /etc/shadow /etc/passwd /etc/group /etc/fstab /usr/lib64/os-release \
       /etc/kernel/cmdline.d /etc/kernel/cmdline-removal.d
cpopt /etc/gshadow /etc/os-release /etc/hostname /etc/sudoers /etc/locale.conf /etc/vconsole.conf
K=/usr/lib64/kernel
mkdir -p $D$K
for f in "$K"/* "$K"/.[!.]*; do
    [[ -e $f || -L $f ]] || continue
    case ${f##*/} in
        com.solus-project.*|initrd-com.solus-project.*|System.map-*)
            [[ ${f##*/} == *"$KVER"* ]] || { echo "kernel store: left out $f (an older kernel's image, initrd or System.map)" >>$L/env.txt; continue; } ;;
    esac
    cpreal "$f"
done
# eopkg's DB: all of it but package/, of which a sample: the boot chain's
# and the tools' packages and the first 24 by name.  All 943 package dirs
# take rt_nodes (a restic cat blob per directory, per repository) over 30
# minutes.
EP=/var/lib/eopkg/package
for x in /var/lib/eopkg/* /var/lib/eopkg/.[!.]*; do [[ ( -e $x || -L $x ) && $x != "$EP" ]] && cpreal "$x"; done
mkdir -p $D$EP
EPKGS=$( (cd $EP && { for q in eopkg clr-boot-manager linux-current shim systemd-boot systemd e2fsprogs dosfstools python3 util-linux; do ls -d $q-[0-9]* 2>/dev/null; done; ls | head -24; }) | sort -u)
# shellcheck disable=SC2086
for q in $EPKGS; do cpreal $EP/$q; done
echo "eopkg DB: $(du -sh /var/lib/eopkg | cut -f1), $(ls $EP | wc -l) packages, $(echo $EPKGS | wc -w) of them copied: $(echo $EPKGS); $(du -sh /var/lib/eopkg/* | tr '\t\n' ' ')" >>$L/env.txt
ref() { chmod --reference="$2" "$1"; chown --reference="$2" "$1"; touch -h --reference="$2" "$1"; }
mkdir -p $D/home && ref $D/home /home
cpreal /home/tester
if [[ -d /var/log/journal ]]; then cpreal /var/log/journal; echo "journal: kept (/var/log/journal)" >>$L/env.txt
else echo "journal: none (no /var/log/journal: the guest's journal is volatile)" >>$L/env.txt; fi
for p in /etc /etc/kernel /usr /usr/bin /usr/sbin /usr/lib64 /usr/lib64/kernel /var /var/lib /var/lib/eopkg /var/lib/eopkg/package /var/log; do
    [[ -d $D$p && ! -L $D$p ]] && ref $D$p $p
done
# The journal's ACLs and xattrs, as the guest has them (cp copies them;
# this makes sure).
[[ -d /var/log/journal ]] && python3 - /var/log/journal $D/var/log/journal <<'PY' 2>>$L/fixture.err
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
         $(find / -xdev -path /mnt -prune -o -type f -size +0 -printf '%b %s %p\n' 2>/dev/null | awk '$1*512 < $2 {print $3}' | grep -v -e '^/root/' -e '^/var/log/journal/' -e '^/proc/' | head -5); do
    [ -e "$f" ] || continue
    mkdir -p "$G$(dirname "$f")"
    cp -a --sparse=always "$f" "$G$f" 2>>$L/fixture.err && n=$((n+1)) && echo "guest file: $f" >>$L/real.txt
done
echo "real files copied: $n" >>$L/env.txt
stage fixture: ext4 items
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
# The ESP: the guest's own (shim as EFI/BOOT/BOOTX64.EFI and
# EFI/com.solus-project/bootloaderx64.efi, fbx64.efi, systemd-boot as
# grubx64.efi, mmx64.efi, BOOTX64.CSV, the current kernel and initrd,
# loader/loader.conf and the entries, solus-enroll-me.cer) at its own paths
# with its times and FAT attributes (an older kernel's image and initrd left
# out), the sha256 of each as the guest has it; then FAT's own: hidden,
# system, archive set and cleared, read-only by chmod and by attribute, on
# files and dirs; long, case-mixed and UTF-8 names; deep, empty and big
# dirs; FAT's time limits.
E=$D/boot
fat() {  # PATH ATTRS: FAT_IOCTL_SET_ATTRIBUTES
    python3 -c 'import fcntl,os,struct,sys; fd=os.open(sys.argv[1],os.O_RDONLY); fcntl.ioctl(fd,0x40047211,struct.pack("I",int(sys.argv[2],0))); os.close(fd)' "$1" "$2" ||
        echo "fixture: skipped: FAT attributes $2 on $1" >>$L/fixture.err
}
stage fixture: the ESP
python3 - /boot $E "$KVER" $L/esp-guest.sha256 <<'PY' >>$L/env.txt 2>>$L/fixture.err || echo "fixture: skipped: the copy of the guest's ESP" >>$L/fixture.err
import fcntl, hashlib, os, re, shutil, sys
a, b, kv, sums = sys.argv[1:5]
copied, dirs = [], []
for dp, dns, fns in os.walk(a):
    rel = os.path.relpath(dp, a)
    q = b if rel == "." else os.path.join(b, rel)
    for n in sorted(dns):
        os.mkdir(os.path.join(q, n))
    for n in sorted(fns):
        p = os.path.join(dp, n)
        if re.match(r"(kernel|initrd)-com\.solus-project\.", n) and not n.endswith("." + kv):
            print(f"esp: left out {p} (an older kernel's)"); continue
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
        f.write(f"{sha256(p)}  boot/{os.path.relpath(p, a)}\n")
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
# A UTF-8 name needs the mount's utf8 (or iocharset=utf8); clr-boot-manager's
# mount takes the kernel's default iocharset, so without it this is a note.
if [[ ,$ESPOPTS, == *,utf8,* || ,$ESPOPTS, == *,iocharset=utf8,* ]]; then
    : >"café-ünïcode" || echo "fixture: skipped: a UTF-8 name" >>$L/fixture.err
else
    : >"café-ünïcode" 2>/dev/null && echo "esp: a UTF-8 name made without utf8 ($ESPOPTS)" >>$L/env.txt ||
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
# The directory above the backed-up path, with metadata of its own
chown 4321:8765 $A; chmod 2751 $A
python3 - $A <<'PY'
import os, struct, sys
X = 0xffffffff
os.setxattr(sys.argv[1], "user.ancestor", b"anc"); os.setxattr(sys.argv[1], "trusted.ancestor", b"t")
os.setxattr(sys.argv[1], "system.posix_acl_access", struct.pack("<I", 2) + b"".join(
    struct.pack("<HHI", t, p, i) for t, p, i in [(1, 7, X), (2, 5, 1234), (4, 5, X), (0x10, 5, X), (0x20, 1, X)]))
PY
# $D/boot is the FAT's root, which has no times: left as it is.
touch -h -m -d '2004-05-06 07:08:09.123456789' $A $D $F
touch -h -a -d '2005-06-07 08:09:10.987654321' $A $D $F
sync
noatime() { for m in $ALL_MOUNTS; do mount -o remount,noatime "$m"; done; }
strictatime() { for m in $ALL_MOUNTS; do mount -o remount,strictatime "$m"; done; }
rec() { python3 $B/fsmeta.py record "$1" "$2" --skip root/bko-torture/socket; }
cmp() { python3 $B/fsmeta.py compare "$1" "$2" --json "$3" >"$3.txt" 2>&1; }
stage record source
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
grep -P '^\.\t' $L/anc.restic >$L/anc1.restic; grep -P '^\.\t' $L/anc.rustic >$L/anc1.rustic

# --- restores in four directions --------------------------------------------
# Each target: a disk image laid out as the guest's disk, its file systems
# made and mounted as the source's (as BackOtter mounts a machine's file
# systems before it restores).
restore() {  # TOOL REPO TARGET
    if [[ $1 == *rustic* ]]; then $1 --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $1 -r $2 restore -q latest --target $3; fi
}
DIRS="restic-restic rustic-rustic restic-rustic rustic-restic"
for d in $DIRS; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    t=/mnt/t-$d
    truncate -s "$(blockdev --getsize64 "$GDISK")" $W/$d.img && mkparts $W/$d.img && lo=$(losetup -P --show -f $W/$d.img) && udevadm settle &&
        mkstack ${lo}p $t $W/$d-feat.img || { res FAIL harness "no target stack for $d: $(tail -3 $L/mkfs.log | paste -sd' ')"; continue; }
    echo "$d $lo" >>$L/loops
    gpt $W/$d.img >$L/gpt-$d
    biosinfo ${lo}p$BN >$L/bios-$d
    stage restore $d; restore $bin $W/repo-$b $t >$L/$d.restore 2>&1; st restore-$d $?
    sync; for m in $t $t$D/boot $t$D/feat; do mount -o remount,noatime $m; done
    rec $t$D $L/$d.json
    python3 $B/fatattr.py $t$E $L/$d.fat.json
    cmp $L/src0.json $L/$d.json $L/$d.cmp.json
    anc_stat $t$A >$L/$d.anc
    findmnt -n -o SOURCE,FSTYPE --target $t$E/. >$L/$d.espmount
    [ -e $t$T/socket ] && echo "socket restored" >>$L/$d.sock
done

# --- restore again over the restored tree, its immutable/append-only files and FAT attributes changed
for d in restic-restic rustic-rustic; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    t=/mnt/t-$d$D
    mountpoint -q $t/feat || continue
    chattr -i $t/root/bko-torture/immutable; echo changed >$t/root/bko-torture/immutable; chattr +i $t/root/bko-torture/immutable
    chattr -a $t/root/bko-torture/append-only; echo more >>$t/root/bko-torture/append-only; chattr +a $t/root/bko-torture/append-only
    chattr -i $t/feat/immutable-projid; chmod 0600 $t/feat/immutable-projid; chattr +i $t/feat/immutable-projid
    chattr -i $t/immutable-dir; echo new >$t/immutable-dir/inside; chmod 0700 $t/immutable-dir; chattr +i $t/immutable-dir
    echo changed >$t/root/bko-torture/empty
    python3 - $t/boot <<'PY' >>$L/$d.over.before 2>&1
import fcntl, os, struct, sys
E = sys.argv[1]
def setfat(p, v):
    fd = os.open(os.path.join(E, p), os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, struct.pack("I", v)); os.close(fd)
setfat("readonly-attr", 0x20)
open(os.path.join(E, "readonly-attr"), "w").write("changed-contents\n")
setfat("readonly-attr", 0x21)
for p, v in (("hidden", 0x20), ("archive-cleared", 0x20), ("system-dir", 0x10), ("readonly-dir", 0x10)):
    setfat(p, v)
open(os.path.join(E, "EFI/com.solus-project/grubx64.efi"), "r+b").write(b"changed")
open(os.path.join(E, "loader/loader.conf"), "a").write("timeout 9\n")
print("FAT attributes changed before the restore over: readonly-attr (contents), hidden, archive-cleared, system-dir, readonly-dir; "
      "systemd-boot (grubx64.efi) overwritten, loader.conf appended to")
PY
    stage restore over $d; restore $bin $W/repo-$b /mnt/t-$d >$L/$d.over 2>&1; st restore-over-$d $?
    sync
    rec $t $L/$d.over.json
    python3 $B/fatattr.py $t/boot $L/$d.over.fat.json
    cmp $L/src0.json $L/$d.over.json $L/$d.over.cmp.json
done

stage non-root
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
df -m /mnt/src $E $D/feat /mnt/t-* $N $W >$L/df.txt 2>&1
stage judge
python3 $B/judge.py $L

# --- the logs for the host ------------------------------------------------------
tar -C $L -cf /root/bk/logs.tar .
exit 0
