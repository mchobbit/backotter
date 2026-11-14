#!/bin/bash
# In the microos throwaway guest, as root, from /var/lib/bko-stic (/ is a
# read-only snapshot: the helpers, the mount points and the work disk go in
# writable places): for btrfs (the root's type) the fixture (fixture.sh) on
# a fresh file system made like the guest's, holding snapper/t-u slots as
# the guest's (read-only snapshots with a nested writable /etc); for vfat
# (the ESP's) the FAT fixture (fixture-vfat.sh) on a FAT made like the
# ESP; the four directions, restore over existing files (immutable and
# append-only on btrfs, read-only and FAT attributes on vfat); then the
# non-root run on btrfs; judge.py per file system.
set -u
B=/var/lib/bko-stic RESTIC=/var/lib/bko-stic/restic-bko RUSTIC=/var/lib/bko-stic/rustic-bko W=/mnt/bkw LB=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
FSS=${FSS:-btrfs vfat}
. $B/rt-common.sh
# The guest has no python3: the host's, shipped by check.sh into $B/py,
# run with its own loader and libraries.  Then the stand-ins for setfattr,
# setfacl (and setcapraw) the guest lacks (bkoshim.py: it takes its part
# from its name), all in /usr/local (@/usr/local, writable).
printf '#!/bin/sh\nexec %s/py/so/ld-linux-x86-64.so.2 --library-path %s/py/so %s/py/bin/python3.real "$@"\n' $B $B $B >/usr/local/bin/python3
chmod 755 /usr/local/bin/python3
mkdir -p /usr/local/lib/bkoshim
for n in setfattr setfacl setcapraw; do
    ln -sf $B/bkoshim.py /usr/local/lib/bkoshim/$n
    printf '#!/bin/sh\nexec /usr/local/bin/python3 /usr/local/lib/bkoshim/%s "$@"\n' $n >/usr/local/bin/$n; chmod 755 /usr/local/bin/$n
done
export PATH=/usr/local/bin:$PATH
python3 -c 'import fcntl, hashlib, json, socket, struct, tarfile; print("python3", __import__("sys").version.split()[0])' ||
    { echo "FAIL harness: the shipped python3 doesn't run"; exit 2; }
# / (and so /mnt) is read-only: the mount points go on a tmpfs over /mnt,
# labelled as /mnt is.
mnt_label=$(stat -c %C /mnt 2>/dev/null)
mount -t tmpfs -o mode=0755${mnt_label:+,rootcontext=$mnt_label} bko-mnt /mnt || { echo "FAIL harness: no tmpfs on /mnt"; exit 2; }
mkfs.btrfs -q -f /dev/vdb >/dev/null && mkdir -p $W && mount -o noatime /dev/vdb $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $LB
btrfs_feat() {  # DEV-or-IMAGE: the superblock's feature flags
    btrfs inspect-internal dump-super "$1" 2>/dev/null | grep -E '^(incompat_flags|compat_ro_flags|csum_type|nodesize|sectorsize)' | tr -s ' \t' ' ' | tr '\n' ';'
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
espdev=$(findmnt -n -o SOURCE /boot/efi)
espgeom=$(fatgeom "$espdev") EOPTS=$(esp_opts /boot/efi)
fatT=$(echo "$espgeom" | sed 's/^FAT\([0-9]*\).*/\1/') fatS=$(echo "$espgeom" | sed 's/.*bps=\([0-9]*\).*/\1/') fatC=$(echo "$espgeom" | sed 's/.*spc=\([0-9]*\).*/\1/')
# The scratch FAT is the ESP's size, so mkfs.vfat takes the ESP's FAT type
# with its cluster size (the fixture fits beside the ESP's files).
espbytes=$(blockdev --getsize64 "$espdev")
newfs() {  # FS NAME: a fresh file system (mkfs's defaults, as the guest's) on a loop device at /mnt/FS-NAME
    # btrfs as MicroOS's image has it: the files in subvolume @, the default
    # one, mounted with the root's options but rw and noatime.  vfat with
    # the ESP's FAT type, sector and cluster size, mounted with its options.
    mkdir -p /mnt/$1-$2 || return 1
    case $1 in
        btrfs) truncate -s 1500M $W/$1-$2.img &&
               mkfs.btrfs -q -f $W/$1-$2.img >/dev/null && mount -o loop $W/$1-$2.img /mnt/$1-$2 &&
               btrfs -q subvolume create /mnt/$1-$2/@ >/dev/null && btrfs -q subvolume set-default /mnt/$1-$2/@ &&
               umount /mnt/$1-$2 && mount -o loop,noatime,discard=async,space_cache=v2 $W/$1-$2.img /mnt/$1-$2 ;;
        vfat) truncate -s $espbytes $W/$1-$2.img &&
              mkfs.vfat -F "$fatT" -S "$fatS" -s "$fatC" -n BKOESP $W/$1-$2.img >$W/mkfs-$2.log 2>&1 &&
              mount -o loop,noatime${EOPTS:+,$EOPTS} $W/$1-$2.img /mnt/$1-$2 ;;
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
# FAT_IOCTL_SET_ATTRIBUTES.
fat() { python3 -c 'import fcntl,os,struct,sys; fd=os.open(sys.argv[1],os.O_RDONLY); fcntl.ioctl(fd,0x40047211,struct.pack("I",int(sys.argv[2],0))); os.close(fd)' "$1" "$2"; }
for FS in $FSS; do
    L=$LB/$FS; mkdir -p $L
    st() { echo "$1 $2" >>$L/status; }
    if [[ $FS == btrfs ]]; then
        rootdev=$(findmnt -rn -o SOURCE / | sed 's/\[.*//')
        { getenforce; btrfs_feat $rootdev; echo; } >$L/env.txt
    else
        { getenforce; echo "$espgeom $EOPTS"; } >$L/env.txt
    fi
    newfs $FS src || { echo "FAIL harness: no $FS source fs"; continue; }
    if [[ $FS == btrfs ]]; then btrfs_feat $W/$FS-src.img; else echo "$(fatgeom $W/$FS-src.img) $(esp_opts /mnt/$FS-src)"; fi >>$L/env.txt; echo >>$L/env.txt
    P=/mnt/$FS-src/up1/up2/tree
    if [[ $FS == btrfs ]]; then bash $B/fixture.sh /mnt/$FS-src $FS $L 2>$L/fixture.err
    else bash $B/fixture-vfat.sh /mnt/$FS-src $L 2>$L/fixture.err; fi
    avc >$L/avc-fixture
    sync; python3 $B/fsmeta.py record /mnt/$FS-src $L/src.json
    [[ $FS == vfat ]] && { python3 $B/fatattr.py $P $L/src.fat.json; python3 $B/fatattr.py /mnt/$FS-src/up1 $L/src-up.fat.json; }
    [[ $FS == btrfs ]] && lsattr -R -d $P/* 2>/dev/null >$L/src.lsattr
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
        if [[ $FS == btrfs ]]; then btrfs_opts /mnt/$FS-d$n$P >$L/$n.btrfs
        else python3 $B/fatattr.py /mnt/$FS-d$n$P $L/$n.fat.json; python3 $B/fatattr.py /mnt/$FS-d$n/mnt/$FS-src/up1 $L/$n-up.fat.json; fi
    done
    [[ $FS == btrfs ]] && btrfs_opts $P >$L/src.btrfs
    # Restore over existing files, changed since: each tool over its own
    # restore.  btrfs: immutable and append-only files (and an immutable
    # directory); vfat: read-only files (by attribute and by mode), FAT
    # attributes cleared and set, a new file in a hidden directory.
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
for FS in $FSS; do python3 $B/judge.py $LB/$FS $FS $LB; done
python3 -c 'import tarfile; t = tarfile.open("/var/lib/bko-stic/logs.tar", "w"); t.add("/mnt/bkw/logs", arcname="."); t.close()'
