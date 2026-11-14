#!/bin/bash
# In the anduinos2 throwaway guest, as root, from /root/bk: the spare disk
# (vdb) laid out as the guest's vda (its GPT: each partition's start, size,
# type and name, the last taking the rest: BIOS boot, the ESP, swap,
# Btrfs), the swap partition made, the ESP made with the guest's FAT
# geometry, the Btrfs with the guest's subvolumes in the guest's order
# (@root, @home, @log, @snapshots, @containers, @libvirt), mounted with
# the guest's fstab options: @root at /mnt/src (like /), the others and the
# ESP inside the backed-up path where the guest has them (tree/home,
# tree/var/log, ...  tree/boot/efi).  The fixture (fixture.sh) on each,
# then read-only snapshots of the scratch @root and @home under @snapshots
# by the guest's own snapshots' paths (the snapshot manager's
# deployments/<id>/root, personal/snapshots/<id>/home).  Four directions,
# each into a disk image (vdc) laid out and mounted the same, made fresh;
# each tool restoring over its own restore with changed
# immutable/append-only files and FAT attributes; each tool's check
# --read-data of the other's repository; then the non-root run on a Btrfs
# mounted like /, and judge.py.
set -u
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
DISK=/dev/vdb P=/mnt/src/up1/up2/tree
# The guest has no attr package: bkoshim.py stands in for setfattr.
if ! command -v setfattr >/dev/null; then
    install -m 755 $B/bkoshim.py /usr/local/bin/bkoshim && ln -sf bkoshim /usr/local/bin/setfattr
fi
export PATH=/usr/local/bin:$PATH
[[ $(lsblk -dbno SIZE /dev/vdb) == $((8 << 30)) && $(lsblk -dbno SIZE /dev/vdc) == $((24 << 30)) ]] ||
    { echo "FAIL harness: vdb/vdc aren't the spare disks: $(lsblk -dno NAME,SIZE | paste -sd' ')"; exit 2; }
mkfs.ext4 -q -F /dev/vdc && mkdir -p $W && mount -o noatime /dev/vdc $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L
st() { echo "$1 $2" >>$L/status; }

# --- What the guest has ------------------------------------------------------
rootdev=$(findmnt -n -o SOURCE / | sed 's/\[.*//') espdev=$(findmnt -n -o SOURCE /boot/efi)
GDISK=/dev/$(lsblk -no PKNAME "$rootdev")
{ uname -r; . /etc/os-release; echo "$PRETTY_NAME ($ID $VERSION_ID)"; cat /sys/kernel/security/lsm; echo
  echo "root $rootdev esp $espdev disk $GDISK"; btrfs --version; mkfs.vfat --help 2>&1 | head -1; } >$L/env.txt
cat /sys/kernel/security/lsm >$L/lsm; echo >>$L/lsm
# The guest's fstab: each Btrfs mount (mount point, options) and the ESP's.
awk '$1 !~ /^#/ && $3 == "btrfs" {print $2, $4}' /etc/fstab >$L/fstab-btrfs
ESPOPTS=$(awk '$1 !~ /^#/ && $2 == "/boot/efi" {print $4}' /etc/fstab)
ROOTOPTS=$(awk '$1 == "/" {print $2}' $L/fstab-btrfs)
[[ -n $ROOTOPTS && -n $ESPOPTS ]] || { echo "FAIL harness: no / or /boot/efi in the guest's fstab"; exit 2; }
# The subvolumes at the top level, in the guest's order (by ID).
btrfs subvolume list -a / | awk '$7 == 5 {sub("<FS_TREE>/", "", $9); print $9}' >$L/subvols
SUBVOLS=$(paste -sd' ' $L/subvols)
btrfs_feat() {  # DEV: the superblock's features (incompat less the compression bits, LZO 0x8 and ZSTD 0x10, which writing compressed data sets)
    btrfs inspect-internal dump-super "$1" 2>/dev/null | python3 -c '
import sys
f = dict(l.split(None, 1) for l in sys.stdin.read().splitlines() if len(l.split(None, 1)) == 2)
out = []
for k in ("csum_type", "sectorsize", "nodesize", "compat_flags", "compat_ro_flags", "incompat_flags"):
    v = f.get(k, "?").split()[0]
    if k == "incompat_flags" and v.startswith("0x"): v = hex(int(v, 16) & ~0x18)
    out.append(f"{k}={v}")
print(" ".join(out))'
}
subvol_list() {  # MOUNT: every subvolume (path from the top level, read-only or not), sorted
    { btrfs subvolume list -a "$1" | awk '{sub("<FS_TREE>/", "", $9); print $9, "rw"}'
      btrfs subvolume list -a -r "$1" | awk '{sub("<FS_TREE>/", "", $9); print $9, "ro"}'; } |
        sort -k1,1 -k2,2 | awk '!seen[$1]++'
}
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
mopts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -v '^subvolid=' | paste -sd,; }  # (subvolid: the IDs are the guest's too, but by order)
gpt() {  # DISK: the label, then each partition: start, size (but the last's), type, name
    sfdisk -d "$1" | python3 -c '
import re, sys
ls = sys.stdin.read().splitlines()
print(next((l.split(": ", 1)[1] for l in ls if l.startswith("label:")), "?"))
ps = [l for l in ls if l.startswith("/") and " : " in l]
for i, l in enumerate(ps):
    f = dict((k, v.strip().strip(chr(34))) for k, v in re.findall(r"(\w+)=\s*(\"[^\"]*\"|[^,]+)", l.split(" : ", 1)[1]))
    print(f.get("start"), f.get("size") if i < len(ps) - 1 else "rest", f.get("type"), f.get("name", "-"))'
}
mkparts() {  # DISK-or-IMAGE: the guest's GPT (fresh ids; the last partition takes the rest)
    sfdisk -d $GDISK | python3 -c '
import re, sys
ps = [l for l in sys.stdin.read().splitlines() if l.startswith("/") and " : " in l]
print("label: gpt")
for i, l in enumerate(ps):
    f = dict(re.findall(r"(\w+)=\s*(\"[^\"]*\"|[^,]+)", l.split(" : ", 1)[1]))
    s = ["start=" + f["start"].strip()] + (["size=" + f["size"].strip()] if i < len(ps) - 1 else []) + ["type=" + f["type"].strip()]
    s += ["name=" + f["name"]] if "name" in f else []
    s += ["attrs=" + f["attrs"]] if "attrs" in f else []
    print(", ".join(s))' | sfdisk -q --wipe always "$1" >>$L/sfdisk.log 2>&1
}
read -r FATT FATS FATC <<<"$(fatgeom "$espdev" | sed 's/^FAT\([0-9]*\) bps=\([0-9]*\) spc=\([0-9]*\).*/\1 \2 \3/')"
gpt $GDISK >$L/gpt-guest
btrfs_feat "$rootdev" >$L/feat-guest
subvol_list / >$L/subvols-guest
fatgeom "$espdev" >$L/fat-guest
{ while read -r mp o; do echo "$mp $(mopts $mp)"; done <$L/fstab-btrfs; echo "/boot/efi $(mopts /boot/efi)"; } >$L/mopts-guest
swappart=$(blkid -t TYPE=swap -o device | grep "^$GDISK" | head -1)
echo "${swappart:+$swappart }$(blockdev --getsize64 ${swappart:-/dev/null} 2>/dev/null)" >$L/swap-guest
# The guest's read-only snapshots under @snapshots, each with the
# subvolume it was taken of (by its parent UUID).
btrfs subvolume list -a -r -q -u / | awk '{sub("<FS_TREE>/", "", $13); print $13, $11, $9}' >$L/snaps-guest.raw  # PATH UUID PARENT_UUID
btrfs subvolume list -a -u / | awk '{sub("<FS_TREE>/", "", $11); print $9, $11}' >$L/uuids-guest                     # UUID PATH
while read -r p u pu; do echo "$p $(awk -v u=$pu '$1 == u {print $2}' $L/uuids-guest)"; done <$L/snaps-guest.raw >$L/snaps-guest
echo "mkfs.btrfs (defaults) for $(cat $L/feat-guest); subvolumes $SUBVOLS; mkfs.vfat -F $FATT -S $FATS -s $FATC; / $ROOTOPTS; ESP $ESPOPTS" >>$L/env.txt

# --- The scratch stack ---------------------------------------------------------
mk_vfat() { mkfs.vfat -F "$FATT" -S "$FATS" -s "$FATC" -n BKSTESP "$1" >/dev/null; }
mkstack() {  # PARTPREFIX ROOT: the file systems on PARTPREFIX{2,3,4}, @root at ROOT, the others under ROOT$P
    local d=$1 r=$2 t=$2$P mp o
    [[ $r == /mnt/src ]] && t=$P
    mkswap -q -L bkst-swap ${d}3 >>$L/mkfs.log 2>&1 && mk_vfat ${d}2 >>$L/mkfs.log 2>&1 && mkfs.btrfs -q -f -L bkst ${d}4 >>$L/mkfs.log 2>&1 || return 1
    mkdir -p /mnt/bkst-top && mount -o subvolid=5 ${d}4 /mnt/bkst-top || return 1
    for s in $SUBVOLS; do btrfs -q subvolume create /mnt/bkst-top/$s >>$L/mkfs.log 2>&1 || { umount /mnt/bkst-top; return 1; }; done
    umount /mnt/bkst-top
    mkdir -p $r && mount -o "$ROOTOPTS" ${d}4 $r && mkdir -p $t || return 1
    # By mount point depth: a mount inside another's after it (none here).
    while read -r mp o; do
        [[ $mp == / ]] && continue
        mkdir -p $t$mp && mount -o "$o" ${d}4 $t$mp || return 1
    done < <(awk '{print gsub("/", "/"), $0}' $L/fstab-btrfs | sort -n | cut -d' ' -f2-)
    mkdir -p $t/boot/efi && mount -t vfat -o "$ESPOPTS" ${d}2 $t/boot/efi
}
KINDOF() { case $1 in /home) echo home ;; /var/log) echo log ;; /.snapshots) echo snapshots ;;
                      /var/lib/containers) echo containers ;; /var/lib/libvirt/images) echo libvirt ;; *) echo other ;; esac; }
mkparts $DISK && udevadm settle && mkstack $DISK /mnt/src ||
    { echo "FAIL harness: no scratch stack on $DISK: $(tail -3 $L/sfdisk.log $L/mkfs.log | paste -sd' ')"; exit 2; }
gpt $DISK >$L/gpt-src
fatgeom ${DISK}2 >$L/fat-src
echo "${DISK}3 $(blockdev --getsize64 ${DISK}3) $(blkid -o value -s TYPE ${DISK}3)" >$L/swap-src
{ while read -r mp o; do [[ $mp == / ]] && echo "/ $(mopts /mnt/src)" || echo "$mp $(mopts $P$mp)"; done <$L/fstab-btrfs
  echo "/boot/efi $(mopts $P/boot/efi)"; } >$L/mopts-src
fx() { bash $B/fixture.sh "$@" 2>>$L/fixture.err; }
fx /mnt/src root
while read -r mp o; do
    [[ $mp == / ]] && continue
    k=$(KINDOF $mp); [[ $k == other ]] && { echo "fixture: skipped: no fixture for $mp" >>$L/fixture.err; continue; }
    fx $P$mp $k
done <$L/fstab-btrfs
fx $P/boot/efi vfat
# The read-only snapshots, as the guest's: each of the scratch subvolume
# its guest twin was taken of (@root: /mnt/src; another: where it's
# mounted), at the same path under @snapshots; the directory it's in keeps
# the guest's times.
while read -r path of; do
    rel=${path#@snapshots/}
    [[ $rel != "$path" ]] || { echo "fixture: skipped: snapshot $path isn't under @snapshots" >>$L/fixture.err; continue; }
    if [[ $of == "$(head -1 $L/subvols)" ]]; then from=/mnt/src
    else from=$P$(awk -v s="subvol=$of" '{n = split($2, o, ","); for (i = 1; i <= n; i++) if (o[i] == s) print $1}' $L/fstab-btrfs | head -1); fi
    [[ $from == "$P" ]] && { echo "fixture: skipped: snapshot $path: $of isn't mounted" >>$L/fixture.err; continue; }
    mkdir -p "$(dirname $P/.snapshots/$rel)"
    btrfs -q subvolume snapshot -r $from $P/.snapshots/$rel >>$L/mkfs.log 2>&1 || echo "fixture: skipped: snapshot -r $from $rel" >>$L/fixture.err
    touch -r "/.snapshots/$(dirname $rel)" "$(dirname $P/.snapshots/$rel)"
    echo "$P/.snapshots/$rel $of $from" >>$L/snaps-made
done <$L/snaps-guest
[[ -s $L/snaps-made ]] || echo "fixture: skipped: no read-only snapshot made" >>$L/fixture.err
touch -d '2001-02-03 04:05:06' $P
btrfs_feat ${DISK}4 >$L/feat-src
subvol_list /mnt/src >$L/subvols-src
# The same inode numbers in other subvolumes: the tree's hard links, the
# snapshot's copies of them and @home's (each subvolume numbers its own).
{ for f in $P/hardlink-a $P/home/hardlink-a; do stat -c '%d %i %h %n' $f; done
  while read -r s of from; do [[ $from == /mnt/src ]] && stat -c '%d %i %h %n' $s${P#/mnt/src}/hardlink-a; done <$L/snaps-made; } >$L/inodes-src 2>&1
# A nested subvolume's place in a snapshot: Btrfs's empty stub directory
# (inode 2), whose times Btrfs makes up each time it reads the inode in.
find $P -path $P/boot/efi -prune -o -type d -inum 2 -print | sed "s|^/mnt/src/||" >$L/stubs-src
sync
python3 $B/fsmeta.py record /mnt/src $L/src.json
python3 $B/fatattr.py $P/boot/efi $L/src.fat.json
# Snapshots' state: read-only, their extents shared with their subvolumes.
snapstate() {  # TREE OUT: for each snapshot path, subvolume or not, ro, btrfs fi du's total/exclusive/shared
    while read -r s of from; do
        x=$1${s#$P}
        printf '%s ' "${s#$P/}"
        if btrfs subvolume show "$x" >/dev/null 2>&1; then printf 'subvolume %s ' "$(btrfs property get "$x" ro)"; else printf 'directory - '; fi
        btrfs filesystem du -s --raw "$x" 2>/dev/null | awk 'NR == 2 {printf "total=%s exclusive=%s shared=%s", $1, $2, $3}'; echo
    done <$L/snaps-made
}
snapstate $P $L/snaps-src >$L/snaps-src
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
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
BTRFSF="compress-zstd compress-lzo compress-zlib compress-zstd-level compress-dir compress-dir/inherited compress-flag compress-immutable nocompress nocow nocow-dir nocow-dir/inherited"
btrfsx() {  # TREE: each per-file option file's lsattr flags and btrfs.compression
    for f in $BTRFSF; do
        printf '%s %s %s\n' "$f" "$(lsattr -d $1/$f 2>/dev/null | cut -d' ' -f1)" \
            "$(python3 -c 'import os,sys
try: print(os.getxattr(sys.argv[1], "btrfs.compression").decode())
except OSError as e: print("-")' $1/$f)"
    done
}
mounts() {  # TREE N: for each mount inside the tree, the file system and subvolume it's on, and its paths
    while read -r mp o; do
        [[ $mp == / ]] && continue
        echo "$mp $(findmnt -n -o SOURCE,FSROOT --target $1$mp/. | tr -s ' ' | sed 's/\[.*\]//') $(find $1$mp | wc -l)"
    done <$L/fstab-btrfs
    echo "/boot/efi $(findmnt -n -o SOURCE,FSROOT --target $1/boot/efi/. | tr -s ' ') $(find $1/boot/efi | wc -l)"
}
btrfsx $P >$L/src.btrfs
mounts $P >$L/mounts-src
# Each target: a disk image laid out as the guest's disk, its file systems
# made and mounted as the source's (as BackOtter mounts a machine's file
# systems before it restores).
for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
    IFS=: read n b r <<<"$d"
    truncate -s 8G $W/d$n.img && mkparts $W/d$n.img && lo=$(losetup -P --show -f $W/d$n.img) && udevadm settle &&
        mkstack ${lo}p /mnt/d$n || { echo "FAIL harness: no target stack for $n: $(tail -3 $L/mkfs.log | paste -sd' ')"; continue; }
    echo "$n $lo" >>$L/loops
    restore $r $W/repo-$b /mnt/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync
    python3 $B/fsmeta.py record /mnt/d$n/mnt/src $L/$n.json
    python3 $B/fatattr.py /mnt/d$n$P/boot/efi $L/$n.fat.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    btrfsx /mnt/d$n$P >$L/$n.btrfs
    mounts /mnt/d$n$P >$L/mounts-$n
    snapstate /mnt/d$n$P >$L/snaps-$n
    subvol_list /mnt/d$n >$L/subvols-$n
done
# Restore over existing immutable and append-only files (and an immutable
# directory), Btrfs's immutable compressed and append-only nocow files, and
# changed FAT attributes and a changed read-only file on the ESP: each
# tool over its own restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=/mnt/drr || D=/mnt/duu
    X=$D$P
    chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
        echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
        echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
    chattr -i $X/compress-immutable && echo changed >>$X/compress-immutable && chattr +i $X/compress-immutable
    echo more >>$X/nocow-append
    lsattr -d $X/immutable $X/append-only $X/immutable-dir $X/compress-immutable $X/nocow-append >$L/$n.before 2>&1
    python3 - $X/boot/efi <<'PY' >>$L/$n.before 2>&1
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
    restore $t $W/repo-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
    sync
    python3 $B/fsmeta.py record $D/mnt/src $L/$n.json
    python3 $B/fatattr.py $X/boot/efi $L/$n.fat.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
done
# One non-root run (nonroot-inner.sh: backup as root, restores as uid
# 4321) on a Btrfs mounted like / (its @root, the guest's options).
truncate -s 1500M $W/nr.img && mkfs.btrfs -q -f $W/nr.img >>$L/mkfs.log 2>&1 && mkdir -p /mnt/nrtop /mnt/nr &&
    mount -o loop $W/nr.img /mnt/nrtop && btrfs -q subvolume create /mnt/nrtop/$(head -1 $L/subvols) >>$L/mkfs.log 2>&1 && umount /mnt/nrtop &&
    mount -o loop,$ROOTOPTS $W/nr.img /mnt/nr && mopts /mnt/nr >$L/mopts-nr || echo "FAIL harness: no Btrfs for the non-root run"
# uid 4321 can't reach /root (0700): the binaries go where it can.
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
df -m /mnt/src $P/boot/efi /mnt/d* /mnt/nr $W >$L/df.txt 2>&1
btrfs filesystem usage -b /mnt/src >$L/usage-src.txt 2>&1
python3 $B/judge.py $L
# The logs for the host.
tar -C /mnt/bkw/logs -cf /root/bk/logs.tar .
