#!/bin/bash
# In the leap16 throwaway guest, as root, from /root/bk: lays the spare
# disk (vdb) out from the guest's own sfdisk -d, with a work partition
# after the guest's (ext4, $W: the restore targets' images, the
# repositories, the logs); for btrfs (the root's type) the fixture
# (fixture.sh) on the root's twin partition, a btrfs made like the root's:
# mkfs's defaults (the guest's features, dump-super), the guest's subvolume
# list made again with each one's flags (@/var's C), owner, mode and label,
# the same default subvolume (@/.snapshots/1/snapshot), mounted without
# subvol= with the root's options but noatime; for vfat (the ESP's) the FAT
# fixture (fixture-vfat.sh) on the ESP's twin, a FAT made like the ESP,
# mounted with its options; the restore targets made the same way on loop
# images; the four directions, each tool's check of the other's
# repository, restore over existing files (immutable and append-only on
# btrfs, read-only and FAT attributes on vfat); then the non-root run on
# btrfs; judge.py per file system.
set -u
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw LB=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
FSS=${FSS:-btrfs vfat}
. $B/rt-common.sh
# python3: the guest's, or the host's that check.sh shipped into $B/py, run
# with its own loader and libraries.  Then bkoshim.py for setcapraw and
# for whichever of setfattr and setfacl the guest lacks (it takes its part
# from its name), in /usr/local (@/usr/local).
shims=setcapraw
if [[ -d $B/py ]]; then
    printf '#!/bin/sh\nexec %s/py/so/ld-linux-x86-64.so.2 --library-path %s/py/so %s/py/bin/python3.real "$@"\n' $B $B $B >/usr/local/bin/python3
    chmod 755 /usr/local/bin/python3
fi
for n in setfattr setfacl; do command -v $n >/dev/null || shims="$shims $n"; done
mkdir -p /usr/local/lib/bkoshim
for n in $shims; do
    ln -sf $B/bkoshim.py /usr/local/lib/bkoshim/$n
    printf '#!/bin/sh\nexec python3 /usr/local/lib/bkoshim/%s "$@"\n' $n >/usr/local/bin/$n; chmod 755 /usr/local/bin/$n
done
export PATH=/usr/local/bin:$PATH
python3 -c 'import fcntl, hashlib, json, socket, struct, tarfile; print("python3", __import__("sys").version.split()[0])' ||
    { echo "FAIL harness: python3 doesn't run"; exit 2; }
# The spare disk: the guest's partition table (types, sizes, names; new
# UUIDs), then the work partition in the rest.
ls /boot/efi >/dev/null 2>&1   # an automount comes up
espdev=$(findmnt -rn -t vfat -o SOURCE /boot/efi) rootdev=$(findmnt -rn -o SOURCE / | sed 's/\[.*//')
pn() { cat /sys/class/block/${1#/dev/}/partition; }
sfdisk -d /dev/vda >/tmp/guest.sfdisk
sed -e '/^label-id:/d' -e '/^device:/d' -e '/^last-lba:/d' -e 's/, *uuid=[^,]*//' -e 's|/dev/vda|/dev/vdb|' /tmp/guest.sfdisk | sfdisk -q /dev/vdb >/tmp/vdb.sfdisk.log 2>&1 &&
    echo ',,L' | sfdisk -q -a /dev/vdb >>/tmp/vdb.sfdisk.log 2>&1 && udevadm settle ||
    { echo "FAIL harness: the guest's partition table on the spare disk: $(tail -3 /tmp/vdb.sfdisk.log)"; exit 2; }
nparts=$(ls -d /sys/class/block/vdb/vdb[0-9]* | wc -l)
VESP=/dev/vdb$(pn $espdev) VROOT=/dev/vdb$(pn $rootdev) VWORK=/dev/vdb$nparts
mkfs.ext4 -q -F $VWORK && mkdir -p $W && mount -o noatime $VWORK $W && rmdir $W/lost+found || { echo "FAIL harness: no work partition"; exit 2; }
mkdir -p $LB
cp /tmp/guest.sfdisk $LB/guest.sfdisk; sfdisk -d /dev/vdb >$LB/spare.sfdisk 2>&1; cat /tmp/vdb.sfdisk.log >>$LB/spare.sfdisk
echo "$shims" >$LB/shims.txt
# The guest's swap partition's twin is a swap area too.
for s in $(awk 'NR > 1 && $2 == "partition" { print $1 }' /proc/swaps); do
    [[ $s == /dev/vda* ]] && mkswap -q /dev/vdb$(pn $s) && echo "swap $s -> /dev/vdb$(pn $s)" >>$LB/spare.sfdisk
done
btrfs_feat() {  # DEV-or-IMAGE: the superblock's feature flags
    btrfs inspect-internal dump-super "$1" 2>/dev/null | grep -E '^(incompat_flags|compat_ro_flags|csum_type|nodesize|sectorsize)' | tr -s ' \t' ' ' | tr '\n' ';'
}
# The guest's top level (subvolid 5), read-only, to read each subvolume's
# flags, owner, mode, label and ro property; the default subvolume's path.
GTOP=/run/bko-gtop
mkdir -p $GTOP && mount -o ro,subvolid=5 $rootdev $GTOP || { echo "FAIL harness: the guest's btrfs top level"; exit 2; }
GSUBS=$(btrfs subvolume list / | sed -n 's/^ID [0-9]* gen [0-9]* top level [0-9]* path //p')
GDEF=$(btrfs subvolume get-default / | sed -n 's/^ID [0-9]* gen [0-9]* top level [0-9]* path //p')
[[ -n $GSUBS && -n $GDEF ]] || { echo "FAIL harness: the guest's subvolume list ($GSUBS) or default ($GDEF)"; exit 2; }
quota=off; btrfs qgroup show / >/dev/null 2>&1 && quota=on
layout() {  # TOP: each subvolume's path, C, owner, mode, label, ro; the default; quota
    local top=$1 p
    for p in $(btrfs subvolume list $top | sed -n 's/^ID [0-9]* gen [0-9]* top level [0-9]* path //p'); do
        printf '%s %s %s %s %s\n' "$p" "$(lsattr -d $top/$p 2>/dev/null | cut -d' ' -f1 | tr -cd 'C')" \
            "$(stat -c '%u:%g %a' $top/$p)" "$(stat -c %C $top/$p 2>/dev/null)" "$(btrfs property get -ts $top/$p ro)"
    done
    echo "default $(btrfs subvolume get-default $top | sed -n 's/^ID [0-9]* gen [0-9]* top level [0-9]* path //p')"
    echo "quota $(btrfs qgroup show $top >/dev/null 2>&1 && echo on || echo off)"
}
layout $GTOP >$LB/guest.layout
# The root's mount options, without subvol=/subvolid= (the default is
# mounted, as the guest's /), seclabel (the kernel's) and its atime
# option: noatime, so reading the source moves no atime.
ROPTS=$(findmnt -n -o OPTIONS / | tr , '\n' | grep -vE '^(rw|ro|relatime|noatime|strictatime|seclabel|subvol=.*|subvolid=.*)$' | paste -s -d , -)
echo "$ROPTS" >$LB/root-opts.txt
btrfs_layout() {  # TOP: the guest's subvolumes again, in the guest's order (by ID: each after its parent); the default
    local top=$1 p d id
    for p in $GSUBS; do
        d=$(dirname "$p")
        [[ $d == . ]] || { mkdir -p "$top/$d" && chown --reference="$GTOP/$d" "$top/$d" && chmod --reference="$GTOP/$d" "$top/$d" &&
                           { chcon --reference="$GTOP/$d" "$top/$d" 2>/dev/null; true; }; } || return 1
        btrfs -q subvolume create "$top/$p" >/dev/null || return 1
        [[ $(lsattr -d "$GTOP/$p" | cut -d' ' -f1) == *C* ]] && { chattr +C "$top/$p" || return 1; }
        chown --reference="$GTOP/$p" "$top/$p"; chmod --reference="$GTOP/$p" "$top/$p"; chcon --reference="$GTOP/$p" "$top/$p" 2>/dev/null
    done
    id=$(btrfs subvolume list $top | awk -v p="$GDEF" '$NF == p { print $2 }')
    [[ -n $id ]] && btrfs -q subvolume set-default "$id" $top || return 1
    [[ $quota == on ]] && { btrfs quota enable $top || return 1; }
    return 0
}
# The ESP's FAT: its type, sector and cluster size (the boot sector), and
# /boot/efi's mount options (fmask, dmask, codepage, iocharset, utf8...).
fatgeom() {  # DEV/IMG: "FAT16 bps=512 spc=4"
    python3 - "$1" <<'PY'
import struct, sys
b = open(sys.argv[1], "rb").read(512)
bps, spc = struct.unpack_from("<HB", b, 11)
fsz16 = struct.unpack_from("<H", b, 22)[0]
t = "FAT32" if fsz16 == 0 else (b[54:62].decode("ascii", "replace").strip() or "FAT16")
print(f"{t} bps={bps} spc={spc}")
PY
}
esp_opts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(rw|ro|relatime|noatime|strictatime|nosuid|nodev|noexec|loop)$' | paste -s -d , -; }
espgeom=$(fatgeom "$espdev") EOPTS=$(esp_opts /boot/efi)
fatT=$(echo "$espgeom" | sed 's/^FAT\([0-9]*\).*/\1/') fatS=$(echo "$espgeom" | sed 's/.*bps=\([0-9]*\).*/\1/') fatC=$(echo "$espgeom" | sed 's/.*spc=\([0-9]*\).*/\1/')
espbytes=$(blockdev --getsize64 "$espdev")
newfs() {  # FS NAME [DEV]: a fresh file system on DEV, else on a loop image, at /mnt/FS-NAME
    # btrfs: mkfs's defaults (as the guest's), the guest's subvolumes and
    # default (btrfs_layout), the default mounted with the root's options.
    # vfat with the ESP's FAT type, sector and cluster size, mounted with
    # its options.
    local dev=${3:-$W/$1-$2.img} lo=""
    [[ -n ${3:-} ]] || lo=loop,
    mkdir -p /mnt/$1-$2 || return 1
    case $1 in
        btrfs) { [[ -n ${3:-} ]] || truncate -s 1500M $dev; } &&
               mkfs.btrfs -q -f $dev >/dev/null && mkdir -p /run/bko-top-$2 && mount -o ${lo}subvolid=5 $dev /run/bko-top-$2 &&
               btrfs_layout /run/bko-top-$2 && layout /run/bko-top-$2 >$W/layout-$2 && umount /run/bko-top-$2 &&
               mount -o ${lo}noatime${ROPTS:+,$ROPTS} $dev /mnt/$1-$2 ;;
        vfat) { [[ -n ${3:-} ]] || truncate -s $espbytes $dev; } &&
              mkfs.vfat -F "$fatT" -S "$fatS" -s "$fatC" -n BKOESP $dev >$W/mkfs-$2.log 2>&1 &&
              mount -o ${lo}noatime${EOPTS:+,$EOPTS} $dev /mnt/$1-$2 ;;
    esac
}
avc() { dmesg | grep -c 'avc: *denied'; }
getenforce >$LB/enforce-before
avc >$LB/avc0
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
BTRFS_OPTS="compress-zstd compress-lzo compress-zlib compress-zstd-level compress-dir compress-dir/inherited compress-flag compress-immutable nocompress nocow nocow-dir nocow-dir/inherited"
btrfs_opts() {  # TREE: each option file's lsattr flags and btrfs.compression
    for f in $BTRFS_OPTS; do
        printf '%s %s %s\n' "$f" "$(lsattr -d $1/$f 2>/dev/null | cut -d' ' -f1)" \
            "$(python3 -c 'import os,sys
try: print(os.getxattr(sys.argv[1], "btrfs.compression").decode())
except OSError as e: print("-")' $1/$f)"
    done
}
# A restored tree's btrfs subvolumes (neither tool makes one: each comes
# back a plain directory) and the ro property of its snapshots.
subvols() { btrfs subvolume list -o "$1" 2>/dev/null | sed -n 's/^ID [0-9]* gen [0-9]* top level [0-9]* path //p'; }
# FAT_IOCTL_SET_ATTRIBUTES.
fat() { python3 -c 'import fcntl,os,struct,sys; fd=os.open(sys.argv[1],os.O_RDONLY); fcntl.ioctl(fd,0x40047211,struct.pack("I",int(sys.argv[2],0))); os.close(fd)' "$1" "$2"; }
for FS in $FSS; do
    L=$LB/$FS; mkdir -p $L
    st() { echo "$1 $2" >>$L/status; }
    if [[ $FS == btrfs ]]; then
        { getenforce; btrfs_feat $rootdev; echo; } >$L/env.txt
        newfs $FS src $VROOT || { echo "FAIL harness: no $FS source fs"; continue; }
        btrfs_feat $VROOT >>$L/env.txt; echo >>$L/env.txt
        cp $LB/guest.layout $L/guest.layout; cp $W/layout-src $L/src.layout
        echo "guest / $(findmnt -n -o FSROOT,OPTIONS /); scratch /mnt/$FS-src $(findmnt -n -o FSROOT,OPTIONS /mnt/$FS-src)" >$L/mounts.txt
    else
        { getenforce; echo "$espgeom $EOPTS"; } >$L/env.txt
        newfs $FS src $VESP || { echo "FAIL harness: no $FS source fs"; continue; }
        echo "$(fatgeom $VESP) $(esp_opts /mnt/$FS-src)" >>$L/env.txt; echo >>$L/env.txt
    fi
    P=/mnt/$FS-src/up1/up2/tree
    if [[ $FS == btrfs ]]; then bash $B/fixture.sh /mnt/$FS-src $FS $L 2>$L/fixture.err
    else bash $B/fixture-vfat.sh /mnt/$FS-src $L 2>$L/fixture.err; fi
    avc >$L/avc-fixture
    sync; python3 $B/fsmeta.py record /mnt/$FS-src $L/src.json
    [[ $FS == vfat ]] && { python3 $B/fatattr.py $P $L/src.fat.json; python3 $B/fatattr.py /mnt/$FS-src/up1 $L/src-up.fat.json; }
    [[ $FS == btrfs ]] && { lsattr -R -d $P/* 2>/dev/null >$L/src.lsattr; subvols $P >$L/src.subvols; }
    ( cd /; time rt_backup $RESTIC $W/$FS-repo-restic $P ) >$L/backup-restic.log 2>&1; st backup-restic $?
    ( cd /; time rt_backup $RUSTIC $W/$FS-repo-rustic $P ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
    python3 $B/fsmeta.py record /mnt/$FS-src $L/src-after.json
    python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
    rt_nodes $RESTIC $W/$FS-repo-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
    rt_nodes $RESTIC $W/$FS-repo-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
    rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
    # Each tool reads all of the other's repository.
    $RESTIC -r $W/$FS-repo-rustic check --read-data >$L/check-restic-on-rustic.log 2>&1; st check-restic-on-rustic $?
    $RUSTIC --no-progress --no-cache -r $W/$FS-repo-restic check --read-data >$L/check-rustic-on-restic.log 2>&1; st check-rustic-on-restic $?
    for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
        IFS=: read n b r <<<"$d"
        newfs $FS d$n || { echo "FAIL harness: no $FS target fs"; continue; }
        restore $r $W/$FS-repo-$b /mnt/$FS-d$n >$L/restore-$n.log 2>&1; st restore-$n $?
        sync; python3 $B/fsmeta.py record /mnt/$FS-d$n/mnt/$FS-src $L/$n.json
        python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
        if [[ $FS == btrfs ]]; then btrfs_opts /mnt/$FS-d$n$P >$L/$n.btrfs; subvols /mnt/$FS-d$n$P >$L/$n.subvols; cp $W/layout-d$n $L/d$n.layout
        else python3 $B/fatattr.py /mnt/$FS-d$n$P $L/$n.fat.json; python3 $B/fatattr.py /mnt/$FS-d$n/mnt/$FS-src/up1 $L/$n-up.fat.json; fi
    done
    [[ $FS == btrfs ]] && btrfs_opts $P >$L/src.btrfs
    # Restore over existing files, changed since: each tool over its own
    # restore.  btrfs: immutable and append-only files (and an immutable
    # directory); vfat: read-only files (by attribute and by mode), FAT
    # attributes cleared and set, the fallback shim rewritten, a new file
    # in a hidden directory.
    for t in restic rustic; do
        n=over-$t; [[ $t == restic ]] && D=/mnt/$FS-drr || D=/mnt/$FS-duu
        X=$D$P
        if [[ $FS == btrfs ]]; then
            chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
                echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
                echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
            chattr -i $X/compress-immutable && echo changed >>$X/compress-immutable && chattr +i $X/compress-immutable
            echo more >>$X/nocow-append
            lsattr -d $X/immutable $X/append-only $X/immutable-dir >$L/$n.before 2>&1
        else
            { fat $X/readonly-attr 0x20 && echo changed-contents >$X/readonly-attr && fat $X/readonly-attr 0x21 &&
                chmod u+w $X/readonly-chmod && echo changed >$X/readonly-chmod && chmod a-w $X/readonly-chmod &&
                fat $X/hidden 0x20 && fat $X/archive-cleared 0x20 && fat $X/system-dir 0x10 && fat $X/readonly-dir 0x10 &&
                echo changed-shim >$X/real/esp/EFI/BOOT/bootx64.efi && echo x >$X/hidden-dir/new; echo "changed $?"; } >$L/$n.before 2>&1
            python3 $B/fatattr.py $X $L/$n.before.fat.json
        fi
        restore $t $W/$FS-repo-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
        sync; python3 $B/fsmeta.py record $D/mnt/$FS-src $L/$n.json
        python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
        [[ $FS == vfat ]] && python3 $B/fatattr.py $X $L/$n.fat.json
    done
    avc >$L/avc-end
    for m in src drr duu dru dur; do umount /mnt/$FS-$m 2>/dev/null; rm -f $W/$FS-$m.img; done
    rm -rf $W/$FS-repo-restic $W/$FS-repo-rustic
done
avc >$LB/avc1
getenforce >$LB/enforce-after
dmesg | grep 'avc: *denied' | tail -20 >$LB/avc.txt
# One non-root run on btrfs (nonroot-inner.sh: backup as root, restores as uid 4321).
newfs btrfs nr
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/btrfs-nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$LB/nonroot.log 2>&1
echo "nonroot $?" >$LB/nonroot.status
umount $GTOP
for FS in $FSS; do python3 $B/judge.py $LB/$FS $FS $LB; done
python3 -c 'import tarfile; t = tarfile.open("/root/bk/logs.tar", "w"); t.add("/mnt/bkw/logs", arcname="."); t.close()'
