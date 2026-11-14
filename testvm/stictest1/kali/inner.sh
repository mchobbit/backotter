#!/bin/bash
# SticTest1 kali, the part that runs in the throwaway guest as root, from
# /root/bk (restic-bko, rustic-bko, fsmeta.py, rt-common.sh, torture.sh,
# judge.py, fatattr.py, nracl.py and check.sh's esp.img.gz beside it).
# Prints "PASS|FAIL|LIMIT need: detail" lines; the records in /mnt/bkw/logs,
# tarred to /root/bk/logs.tar for the host.
#
# The spare disk (vdb, the guest's vda's size) laid out from the guest's
# own `sfdisk -d` (every partition's start, size and type; fresh ids), its
# file systems made like the guest's: the swap partition, the ESP (check.sh's
# FAT32, made with the guest's ESP's geometry), the ext4 (the guest's /'s
# features, inode and block size, journal), mounted with the guest's fstab
# options: the ext4 at /mnt/src (like /), the ESP inside the backed-up path
# at tree/boot/efi (umask=0077), and ubuntu26-uefi's ext4 with project
# quotas and casefolding (loop) at tree/feat.  The fixture (torture.sh and
# ubuntu26-uefi's items, the guest's own files at their own paths) on each.
# Four directions, each into a disk image (vdc) laid out and mounted the
# same, made fresh; each tool restoring over its own restore with changed
# immutable/append-only files and FAT attributes; each tool's check
# --read-data of the other's repository; a backup pair under strictatime;
# one non-root run (uid 4321's own tree) on an ext4 made like /; judge.py.
set -u
B=/root/bk RS=/root/bk/restic-bko RU=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
DISK=/dev/vdb WD=/dev/vdc
A=/mnt/src/anc1 D=/mnt/src/anc1/data
res() { echo "$1 $2: $3"; }
rootdev=$(findmnt -n -o SOURCE /) espdev=$(findmnt -n -o SOURCE /boot/efi)
GDISK=/dev/$(lsblk -no PKNAME "$rootdev")
[[ $(blockdev --getsize64 $DISK) == "$(blockdev --getsize64 $GDISK)" && $(blockdev --getsize64 $WD) == $((80 << 30)) ]] ||
    { res FAIL harness "vdb/vdc aren't the spare disks: $(lsblk -dbno NAME,SIZE | paste -sd' ')"; exit 2; }
mkfs.ext4 -q -F $WD && mkdir -p $W && mount -o noatime $WD $W || { res FAIL harness "no work disk"; exit 2; }
mkdir -p $L $W/rt
export RT_TMP=$W/rt
. $B/rt-common.sh
st() { echo "$1 $2" >>$L/status; }

# --- What the guest has ------------------------------------------------------
pn() { cat /sys/class/block/$(basename "$1")/partition; }
swapdev=$(swapon --show=NAME --noheadings | grep "^$GDISK" | head -1)
RN=$(pn "$rootdev") EN=$(pn "$espdev") SN=$(pn "${swapdev:-/dev/null}" 2>/dev/null)
ROOTOPTS=$(awk '$1 !~ /^#/ && $2 == "/" {print $4}' /etc/fstab)
ESPOPTS=$(awk '$1 !~ /^#/ && $2 == "/boot/efi" {print $4}' /etc/fstab)
[[ -n $RN && -n $EN && -n $SN && -n $ROOTOPTS && -n $ESPOPTS ]] ||
    { res FAIL harness "can't read the guest's layout: root $rootdev/$RN esp $espdev/$EN swap ${swapdev:-?}/$SN fstab / $ROOTOPTS /boot/efi $ESPOPTS"; exit 2; }
{ uname -r; . /etc/os-release; echo "$PRETTY_NAME ($ID $VERSION_ID)"
  echo "root $rootdev ($RN) esp $espdev ($EN) swap $swapdev ($SN) disk $GDISK; fstab / $ROOTOPTS, /boot/efi $ESPOPTS"
  dpkg-query -W -f '${Package}=${Version} ' e2fsprogs fdisk util-linux libcap2-bin busybox python3 2>/dev/null; echo
  echo "versions: $($RS version) | $($RU --version 2>&1 | head -1)"; } >$L/env.txt
cat /sys/kernel/security/lsm >$L/lsm; echo >>$L/lsm
ls /etc/apparmor.d 2>/dev/null | wc -l >$L/apparmor-profiles
extfeat() { dumpe2fs -h "$1" 2>/dev/null | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | paste -sd' '; }
extgeo() { dumpe2fs -h "$1" 2>/dev/null | grep -E '^(Inode size|Block size|Default mount options|Flex block group size|Default directory hash|Filesystem flags|Total journal size|Reserved block count):' | tr -s ' ' | paste -sd';'; }
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
    sfdisk -d $GDISK | python3 -c '
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
FEATS=$(extfeat "$rootdev" | tr ' ' ',')
ISZ=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Inode size: *//p')
BSZ=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Block size: *//p')
JMB=$(dumpe2fs -h "$rootdev" 2>/dev/null | sed -n 's/^Total journal size: *\([0-9]*\)M$/\1/p')
gpt $GDISK >$L/gpt-guest
extfeat "$rootdev" >$L/feat-guest; extgeo "$rootdev" >>$L/feat-guest
fatgeom "$espdev" >$L/fat-guest
{ echo "/ $(mopts /)"; echo "/boot/efi $(mopts /boot/efi)"; } >$L/mopts-guest
echo "$swapdev $(blockdev --getsize64 "$swapdev") $(blkid -o value -s TYPE "$swapdev")" >$L/swap-guest
echo "mkfs.ext4 -b $BSZ -I $ISZ -J size=$JMB -O none,$FEATS; FAT32 $(cat $L/fat-guest); / $ROOTOPTS; ESP $ESPOPTS" >>$L/env.txt

# --- The scratch stack ---------------------------------------------------------
mkroot() {  # DEV-or-IMAGE: an ext4 made like the guest's /
    mkfs.ext4 -q -F -b "$BSZ" -I "$ISZ" ${JMB:+-J size=$JMB} -O "none,$FEATS" "$1"
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
mkstack() {  # PARTPREFIX ROOTMOUNT FEATIMAGE: swap, the ESP and the ext4 on PARTPREFIX{SN,EN,RN}; the ext4 at ROOTMOUNT, ESP and feat inside the tree
    local p=$1 r=$2 t=$2$D
    [[ $r == /mnt/src ]] && t=$D
    mkswap -q -L kali-swap ${p}$SN >>$L/mkfs.log 2>&1 && mkesp ${p}$EN >>$L/mkfs.log 2>&1 && mkroot ${p}$RN >>$L/mkfs.log 2>&1 || return 1
    mkdir -p $r && mount -o "$ROOTOPTS" ${p}$RN $r && mkdir -p $t/boot/efi || return 1
    mount -t vfat -o "$ESPOPTS" ${p}$EN $t/boot/efi && mkfeat "$3" $t/feat
}
mkparts $DISK && udevadm settle && mkstack $DISK /mnt/src $W/srcfeat.img ||
    { res FAIL harness "no scratch stack on $DISK: $(tail -3 $L/sfdisk.log $L/mkfs.log | paste -sd' ')"; exit 2; }
gpt $DISK >$L/gpt-src
extfeat $DISK$RN >$L/feat-src; extgeo $DISK$RN >>$L/feat-src
fatgeom $DISK$EN >$L/fat-src
{ echo "/ $(mopts /mnt/src)"; echo "/boot/efi $(mopts $D/boot/efi)"; } >$L/mopts-src
echo "$DISK$SN $(blockdev --getsize64 $DISK$SN) $(blkid -o value -s TYPE $DISK$SN)" >$L/swap-src
ALL_MOUNTS="/mnt/src $D/boot/efi $D/feat"
for m in $ALL_MOUNTS; do mount -o remount,strictatime $m; done

# --- the fixture ------------------------------------------------------------
# torture.sh, but with 8 MiB of random bytes instead of 1 GiB.  The guest
# has no setfattr or setfacl: what torture.sh skips for want of them is set
# from Python below.
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
# its set-id programs, shadow, passwd, group, fstab, GRUB's defaults,
# Kali's apt source, /boot/grub and the kernels' config and System.map,
# tester's home (with /home's metadata), the journal with its ACLs and
# user.crtime_usec; files with capabilities (if any), hard-linked files and
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
cpreal $SETID $CAPS /etc/shadow /etc/gshadow /etc/passwd /etc/group /etc/fstab /etc/default/grub /etc/apt/sources.list.d/kali.sources \
       /boot/grub /boot/config-* /boot/System.map-* /etc/os-release /etc/sudoers
ref() { chmod --reference="$2" "$1"; chown --reference="$2" "$1"; touch -h --reference="$2" "$1"; }
mkdir -p $D/home && ref $D/home /home
cpreal /home/tester /var/log/journal
for p in /etc /etc/default /etc/apt /etc/apt/sources.list.d /usr /usr/bin /usr/sbin /usr/lib /boot /var /var/log; do
    [[ -d $D$p ]] && ref $D$p $p
done
# The journal's ACLs and xattrs, as the guest has them (cp copies them;
# this makes sure, without getfacl/setfacl).
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
         $(find / -xdev -path /mnt -prune -o -type f -size +0 -printf '%b %s %p\n' 2>/dev/null | awk '$1*512 < $2 {print $3}' | grep -v -e '^/root/' -e '^/var/log/journal/' -e '^/proc/' | head -5); do
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
# The ESP: the guest's own (EFI/kali/grubx64.efi, EFI/BOOT/BOOTX64.EFI) at
# its own paths with its times and FAT attributes, then FAT's own: hidden,
# system, archive set and cleared, read-only by chmod and by attribute, on
# files and dirs; long, case-mixed and UTF-8 names (the guest mounts it
# utf8); deep, empty and big dirs; FAT's time limits.
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
# The ancestor (anc1): both tools store its lstat (restic-bko, and rustic-bko
# since round 16); compared node by node.
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
    truncate -s "$(blockdev --getsize64 $GDISK)" $W/$d.img && mkparts $W/$d.img && lo=$(losetup -P --show -f $W/$d.img) && udevadm settle &&
        mkstack ${lo}p $t $W/$d-feat.img || { res FAIL harness "no target stack for $d: $(tail -3 $L/mkfs.log | paste -sd' ')"; continue; }
    echo "$d $lo" >>$L/loops
    gpt $W/$d.img >$L/gpt-$d
    restore $bin $W/repo-$b $t >$L/$d.restore 2>&1; st restore-$d $?
    sync; for m in $t $t$D/boot/efi $t$D/feat; do mount -o remount,noatime $m; done
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
open(os.path.join(E, "EFI/kali/grubx64.efi"), "r+b").write(b"changed")
print("FAT attributes changed before the restore over: readonly-attr (contents), hidden, archive-cleared, system-dir, readonly-dir; grubx64.efi overwritten")
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
df -m /mnt/src $E $D/feat /mnt/t-* $N $W >$L/df.txt 2>&1
python3 $B/judge.py $L

# --- the logs for the host ------------------------------------------------------
tar -C $L -cf /root/bk/logs.tar .
exit 0
