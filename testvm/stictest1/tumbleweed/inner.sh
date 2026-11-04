#!/bin/bash
# In the tumbleweed throwaway guest, as root: for each of btrfs (the root's
# type) and ext4 (/boot's), the fixture on a fresh file system made like
# the guest's, the four directions, restore over immutable/append-only;
# then the non-root run on btrfs; judge.py per file system.
set -u
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw LB=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
FSS=${FSS:-btrfs ext4}
. $B/rt-common.sh
# The stand-ins for setfattr, setfacl (and setcapraw) the guest lacks.
install -m 755 $B/bkoshim.py /usr/local/bin/bkoshim
for n in setfattr setfacl setcapraw; do ln -sf bkoshim /usr/local/bin/$n; done
export PATH=/usr/local/bin:$PATH
mkfs.ext4 -q -F /dev/vdb && mkdir -p $W && mount -o noatime /dev/vdb $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $LB
btrfs_feat() {  # DEV-or-IMAGE: the superblock's feature flags
    btrfs inspect-internal dump-super "$1" 2>/dev/null | grep -E '^(incompat_flags|compat_ro_flags|csum_type|nodesize|sectorsize)' | tr -s ' \t' ' ' | tr '\n' ';'
}
ext4_feat() { dumpe2fs -h "$1" 2>/dev/null | sed -n 's/^Filesystem features: *//p' | tr -d '\n'; }
newfs() {  # FS NAME: a fresh file system (mkfs's defaults, as the guest's) on a loop device at /mnt/FS-NAME
    # btrfs as the installer makes it: the files in subvolume @, the default
    # one (snapper's layout; DEFAULT_SUBVOL), mounted with the root's options
    # but noatime.  ext4 without lost+found, which rustic would warn about
    # in a restore target ("additional entries exist in destination").
    truncate -s 1500M $W/$1-$2.img && mkdir -p /mnt/$1-$2 || return 1
    case $1 in
        btrfs) mkfs.btrfs -q -f $W/$1-$2.img >/dev/null && mount -o loop $W/$1-$2.img /mnt/$1-$2 &&
               btrfs -q subvolume create /mnt/$1-$2/@ >/dev/null && btrfs -q subvolume set-default /mnt/$1-$2/@ &&
               umount /mnt/$1-$2 && mount -o loop,noatime,discard=async,space_cache=v2 $W/$1-$2.img /mnt/$1-$2 ;;
        ext4) mkfs.ext4 -q -F $W/$1-$2.img && mount -o loop,noatime $W/$1-$2.img /mnt/$1-$2 && rmdir /mnt/$1-$2/lost+found ;;
    esac
}
avc() { dmesg | grep -c 'avc: *denied'; }
getenforce >$LB/enforce-before
avc >$LB/avc0
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
for FS in $FSS; do
    L=$LB/$FS; mkdir -p $L
    st() { echo "$1 $2" >>$L/status; }
    rootdev=$(findmnt -rn -o SOURCE ${FS/btrfs//} | sed 's/\[.*//'); [[ $FS == ext4 ]] && rootdev=$(findmnt -rn -o SOURCE /boot)
    { getenforce; if [[ $FS == btrfs ]]; then btrfs_feat $rootdev; else ext4_feat $rootdev; fi; echo; } >$L/env.txt
    newfs $FS src || { echo "FAIL harness: no $FS source fs"; continue; }
    if [[ $FS == btrfs ]]; then btrfs_feat $W/$FS-src.img; else ext4_feat $W/$FS-src.img; fi >>$L/env.txt; echo >>$L/env.txt
    bash $B/fixture.sh /mnt/$FS-src $FS 2>$L/fixture.err
    avc >$L/avc-fixture
    P=/mnt/$FS-src/up1/up2/tree
    sync; python3 $B/fsmeta.py record /mnt/$FS-src $L/src.json
    lsattr -R -d $P/* 2>/dev/null >$L/src.lsattr
    ( cd /; time rt_backup $RESTIC $W/$FS-repo-restic $P ) >$L/backup-restic.log 2>&1; st backup-restic $?
    ( cd /; time rt_backup $RUSTIC $W/$FS-repo-rustic $P ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
    python3 $B/fsmeta.py record /mnt/$FS-src $L/src-after.json
    python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
    rt_nodes $RESTIC $W/$FS-repo-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
    rt_nodes $RESTIC $W/$FS-repo-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
    rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
    for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
        IFS=: read n b r <<<"$d"
        newfs $FS d$n || { echo "FAIL harness: no $FS target fs"; continue; }
        restore $r $W/$FS-repo-$b /mnt/$FS-d$n >$L/restore-$n.log 2>&1; st restore-$n $?
        sync; python3 $B/fsmeta.py record /mnt/$FS-d$n/mnt/$FS-src $L/$n.json
        python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
        [[ $FS == btrfs ]] && for f in compress-zstd compress-lzo compress-zlib compress-zstd-level compress-dir compress-dir/inherited \
                compress-flag compress-immutable nocompress nocow nocow-dir nocow-dir/inherited; do
            x=/mnt/$FS-d$n/mnt/$FS-src/up1/up2/tree/$f
            printf '%s %s %s\n' "$f" "$(lsattr -d $x 2>/dev/null | cut -d' ' -f1)" \
                "$(python3 -c 'import os,sys
try: print(os.getxattr(sys.argv[1], "btrfs.compression").decode())
except OSError as e: print("-")' $x)"
        done >$L/$n.btrfs
    done
    [[ $FS == btrfs ]] && for f in compress-zstd compress-lzo compress-zlib compress-zstd-level compress-dir compress-dir/inherited \
            compress-flag compress-immutable nocompress nocow nocow-dir nocow-dir/inherited; do
        x=$P/$f
        printf '%s %s %s\n' "$f" "$(lsattr -d $x 2>/dev/null | cut -d' ' -f1)" \
            "$(python3 -c 'import os,sys
try: print(os.getxattr(sys.argv[1], "btrfs.compression").decode())
except OSError as e: print("-")' $x)"
    done >$L/src.btrfs
    # Restore over existing immutable and append-only files (and an immutable
    # directory), changed since: each tool over its own restore.
    for t in restic rustic; do
        n=over-$t; [[ $t == restic ]] && D=/mnt/$FS-drr || D=/mnt/$FS-duu
        X=$D$P
        chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
            echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
            echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
        if [[ $FS == btrfs ]]; then
            chattr -i $X/compress-immutable && echo changed >>$X/compress-immutable && chattr +i $X/compress-immutable
            echo more >>$X/nocow-append
        fi
        lsattr -d $X/immutable $X/append-only $X/immutable-dir >$L/$n.before 2>&1
        restore $t $W/$FS-repo-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
        sync; python3 $B/fsmeta.py record $D/mnt/$FS-src $L/$n.json
        python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
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
python3 -c 'import tarfile; t = tarfile.open("/root/bk/logs.tar", "w"); t.add("/mnt/bkw/logs", arcname="."); t.close()'
