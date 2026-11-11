#!/bin/bash
# In the kinoite44 throwaway guest, as root: for each file system the guest
# mounts (btrfs: the physical root at /sysroot, subvolumes root, home and
# var, mounted without compress though fstab's / says compress=zstd:1 (B1);
# ext4 /boot; the ESP, vfat /boot/efi, with its mount options), the fixture
# on a fresh one made like it, four directions, restore over immutable/
# append-only, on btrfs one restore per tool onto fstab's compress (as
# bko-holt load mounts /) and ostree fsck of each restored repository, then
# one non-root run (btrfs), and the judge.
set -u
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw L0=/mnt/bkw/logs
# The file systems are mounted in /var/mnt: on ostree /mnt is a link to
# var/mnt, and rustic-bko stores a source by its canonical path (restic-bko
# by the one given), so under /mnt the two tools' snapshots would name
# different paths and rustic's restores land in TARGET/var/mnt.
M=/var/mnt
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
mkfs.xfs -q -f /dev/vdb && mkdir -p $W && mount -o noatime /dev/vdb $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L0
# The physical root's live options (composefs is /, the btrfs is /sysroot),
# and fstab's / (the booted deployment's etc), which load's mount stage uses.
compress_of() { tr , '\n' | grep -E '^compress' | head -1; }
BOPTS=$(findmnt -rn -o OPTIONS /sysroot | compress_of)
FOPTS=$(awk '$2 == "/" { print $4 }' /etc/fstab | compress_of)   # compress=zstd:1
# The ESP's own mount options (fmask, dmask, codepage, iocharset, shortname...).
esp_opts() { findmnt -rn -o OPTIONS "$1" | tr , '\n' | grep -vE '^(rw|ro|relatime|noatime|strictatime|seclabel|nosuid|nodev|noexec|loop)$' | paste -sd, -; }
EOPTS=$(esp_opts /boot/efi)
newfs() {  # FSTYPE NAME [SIZE]: a fresh file system like the guest's on a loop device at /mnt/NAME
    # btrfs takes MOPTS's compress if it is set (the restores onto fstab's),
    # else the physical root's live one (none on the guest).
    local o=${MOPTS-$BOPTS}
    truncate -s ${3:-$([[ $1 == vfat ]] && echo 400M || echo 1500M)} $W/$2.img && mkdir -p $M/$2 || return 1
    case $1 in
    btrfs) mkfs.btrfs -q -f $W/$2.img >/dev/null && mount -o loop,noatime${o:+,$o} $W/$2.img $M/$2 ;;
    ext4) mkfs.ext4 -q -F $W/$2.img && mount -o loop,noatime $W/$2.img $M/$2 ;;
    vfat) mkfs.vfat -F 32 -n BKOESP $W/$2.img >/dev/null && mount -o loop,noatime${EOPTS:+,$EOPTS} $W/$2.img $M/$2 ;;
    esac || return 1
    # A fresh ext4's lost+found isn't the snapshot's: rustic would note an
    # additional entry in a restore target.
    [[ $2 == src* ]] || rmdir $M/$2/lost+found 2>/dev/null
    return 0
}
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
grep -c 'type=AVC' /var/log/audit/audit.log >$L0/avc0 2>/dev/null || echo 0 >$L0/avc0
getenforce >$L0/enforce-before
# The guest as B1 built it: two deployments, two BLS entries.
{ ls /boot/loader/entries/; ostree admin status; } >$L0/ostree-guest.txt 2>&1
ext4_feat() { tune2fs -l "$1" | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -vE '^(needs_recovery|orphan_present)?$' | sort | tr '\n' ' '; }
fsck_all() {  # ROOT NAME: ostree fsck of each repository under ROOT/up1/up2/tree/ostree
    local r rc=0
    for r in "$1"/up1/up2/tree/ostree/made/ostree/repo; do
        ostree --repo="$r" fsck >$L/fsck-$2.log 2>&1 || rc=$?
    done
    st fsck-$2 $rc
}
for FS in btrfs ext4 vfat; do
    L=$L0/$FS; mkdir -p $L
    st() { echo "$1 $2" >>$L/status; }
    if [[ $FS == btrfs ]]; then
        { findmnt -rn -o OPTIONS /sysroot | tr , '\n' | grep -E '^(compress|space_cache)' | sort | tr '\n' ' '; echo; } >$L/env.txt
    elif [[ $FS == vfat ]]; then
        { esp_opts /boot/efi | tr , ' '; blkid -o value -s VERSION $(findmnt -rn -o SOURCE /boot/efi); } | tr '\n' ' ' >$L/env.txt; echo >>$L/env.txt
    else
        { ext4_feat $(findmnt -rn -o SOURCE /boot); echo; } >$L/env.txt
    fi
    newfs $FS src$FS || { echo "FAIL harness: no source $FS"; continue; }
    S=$M/src$FS
    if [[ $FS == btrfs ]]; then
        findmnt -rn -o OPTIONS $S | tr , '\n' | grep -E '^(compress|space_cache)' | sort | tr '\n' ' ' >>$L/env.txt
    elif [[ $FS == vfat ]]; then
        { esp_opts $S | tr , ' '; blkid -o value -s VERSION $W/src$FS.img; } | tr '\n' ' ' >>$L/env.txt
    else
        ext4_feat $W/src$FS.img >>$L/env.txt
    fi
    echo >>$L/env.txt
    bash $B/fixture.sh $S $FS 2>$L/fixture.err
    P=$S/up1/up2/tree
    [[ $FS == btrfs ]] && fsck_all $S src
    sync; python3 $B/fsmeta.py record $S $L/src.json
    [[ $FS == vfat ]] && python3 $B/fatattr.py $S $L/src.fat.json
    ( cd /; time rt_backup $RESTIC $W/repo-$FS-restic $P ) >$L/backup-restic.log 2>&1; st backup-restic $?
    ( cd /; time rt_backup $RUSTIC $W/repo-$FS-rustic $P ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
    python3 $B/fsmeta.py record $S $L/src-after.json
    python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
    rt_nodes $RESTIC $W/repo-$FS-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
    rt_nodes $RESTIC $W/repo-$FS-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
    rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
    # The directories above the backed-up path too (each one's own node, named
    # by its path): both tools store them, alike (restic BACKOTTER.md).
    # rt_path_nodes reads one tree per level (/mnt, $S, up1, up2), no walk.
    for t in restic rustic; do
        rt_path_nodes $RESTIC $W/repo-$FS-$t $S/up1/up2 >$L/nodes-anc-$t.txt 2>$L/nodes-anc-$t.err
    done
    rt_node_diff $L/nodes-anc-restic.txt $L/nodes-anc-rustic.txt >$L/nodes-anc.diff 2>&1; st nodes-anc $?
    for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
        IFS=: read n b r <<<"$d"
        newfs $FS d$FS$n
        restore $r $W/repo-$FS-$b $M/d$FS$n >$L/restore-$n.log 2>&1; st restore-$n $?
        sync; python3 $B/fsmeta.py record $M/d$FS$n$S $L/$n.json
        [[ $FS == vfat ]] && python3 $B/fatattr.py $M/d$FS$n$S $L/$n.fat.json
        python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    done
    # btrfs: each tool's own snapshot restored onto a btrfs mounted with
    # fstab's compress=zstd:1, as bko-holt load's mount stage mounts / (B4's
    # whole-disk compare.py saw btrfs set FS_NOCOMP_FL there).
    if [[ $FS == btrfs ]]; then
        echo "fstab / compress: ${FOPTS:-none}" >$L/fstab-compress.txt
        for d in rz:restic uz:rustic; do
            IFS=: read n t <<<"$d"
            MOPTS=$FOPTS newfs btrfs d$FS$n || { st restore-$n newfs; continue; }
            findmnt -rn -o OPTIONS $M/d$FS$n >>$L/fstab-compress.txt
            restore $t $W/repo-$FS-$t $M/d$FS$n >$L/restore-$n.log 2>&1; st restore-$n $?
            sync; python3 $B/fsmeta.py record $M/d$FS$n$S $L/$n.json
            python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
        done
        # What this kernel's btrfs marks FS_NOCOMP_FL (m) by itself under
        # compress=: a tiny file and xz data, written after the records.
        Z=$M/d${FS}rz/nocomp-probe; mkdir -p $Z
        printf 'ld\n/usr/bin/ld.bfd\n' >$Z/tiny; head -c 600 /etc/services >$Z/small600; xz -c /etc/services >$Z/services.xz
        sync; { uname -r; lsattr $Z/tiny $Z/small600 $Z/services.xz; } >$L/nocomp-probe.txt 2>&1
    fi
    # Restore over existing immutable and append-only files (and an immutable
    # directory), changed since: each tool over its own restore.
    for t in restic rustic; do
        [[ $FS == vfat ]] && break   # FAT has no immutable or append-only files
        n=over-$t; [[ $t == restic ]] && D=$M/d${FS}rr || D=$M/d${FS}uu
        X=$D$P
        chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
            echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
            echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
        lsattr -d $X/immutable $X/append-only $X/immutable-dir >$L/$n.before 2>&1
        restore $t $W/repo-$FS-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
        sync; python3 $B/fsmeta.py record $D$S $L/$n.json
        python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    done
    # ostree fsck of each restored repository (after every record: it may
    # write in the repository).  rr and uu hold the restores over existing.
    if [[ $FS == btrfs ]]; then
        for n in rr uu ru ur rz uz; do [[ -d $M/d$FS$n$S ]] && fsck_all $M/d$FS$n$S $n; done
    fi
    # Free the loop devices and RAM of this pass.
    for m in $M/src$FS $M/d$FS*; do umount -l $m 2>/dev/null; done
    rm -f $W/*.img
    st done 0   # this pass finished (check.sh judges only finished passes after a timeout)
done
grep -c 'type=AVC' /var/log/audit/audit.log >$L0/avc1 2>/dev/null || echo 0 >$L0/avc1
getenforce >$L0/enforce-after
# One non-root run on btrfs (nonroot-inner.sh: backup as root, restores as uid 4321).
newfs btrfs nr
# uid 4321 can't reach /root (0550): the binaries go where it can
# (/usr/local is /var/usrlocal on ostree).
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh $M/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L0/nonroot.log 2>&1; echo "nonroot $?" >>$L0/status
for FS in btrfs ext4 vfat; do python3 $B/judge.py $L0 $FS; done
python3 -c 'import tarfile; t = tarfile.open("/root/bk/logs.tar", "w"); t.add("/mnt/bkw/logs", arcname="."); t.close()'
