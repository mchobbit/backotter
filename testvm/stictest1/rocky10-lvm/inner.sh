#!/bin/bash
# In the rocky10-lvm throwaway guest, as root: the spare disk (vdb) laid out
# as the installer laid out the guest's own (GPT, a BIOS boot partition, XFS
# partitions like /boot, a volume group of the rest whose XFS logical
# volumes are made and mounted like /), the fixture on a volume with a
# /boot-like partition mounted inside it, four directions into the same
# stack made fresh, restore over immutable/append-only, a backup pair on
# relatime, the non-root run on a volume, judge.
set -u
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
DISK=/dev/vdb VG=bkst P=/mnt/src/up1/up2/tree
mkfs.xfs -q -f /dev/vdc && mkdir -p $W && mount -o noatime /dev/vdc $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L
st() { echo "$1 $2" >>$L/status; }
# The guest's own LVM files as they are, before the scratch volume group
# changes them (pvcreate and vgcreate write /etc/lvm/devices and backup).
cp -a --preserve=all /etc/lvm $W/etc-lvm 2>$L/etc-lvm.err; st copy-etc-lvm $?

# What the guest has: its XFS features and geometry, mount options, the
# partition table and the LVM stack under / (compared with the scratch's).
feat() { xfs_info "$1" | grep -o '\(isize\|crc\|finobt\|sparse\|rmapbt\|reflink\|bigtime\|inobtcount\|nrext64\|exchange\|metadir\|parent\|ftype\|projid32bit\|attr\|lazy-count\|ascii-ci\)=[0-9]*' | tr '\n' ' '; echo; }
geom() { xfs_info "$1" | sed '1s/^meta-data=[^ ]*/meta-data=DEV/'; }
mopts() { findmnt -n -o OPTIONS --target "$1"; }
gpt() {  # DISK: the label and each partition's start, size, type (no ids)
    sfdisk -d $1 | sed -n 's/^label: //p; s/^[^ ]* : start= *\([0-9]*\), size= *\([0-9]*\), type=\([0-9A-F-]*\).*/\1 \2 \3/p'
}
lvm_of() {  # PV VG LV: format, data offset, metadata areas; extent size; segment
    pvs --noheadings --units b -o pv_fmt,pe_start,pv_mda_count,pv_mda_size $1 | xargs
    vgs --noheadings --units b -o vg_extent_size,vg_mda_copies,vg_attr $2 | xargs
    lvs --noheadings -o lv_attr,segtype,stripes,lv_read_ahead,lv_layout $2/$3 | xargs | sed 's/^\(.....\)o/\1-/'
}
{ getenforce; uname -r; rpm -q xfsprogs lvm2 | paste -sd' '; } >$L/env.txt
feat / >$L/feat-root; feat /boot >$L/feat-boot; geom /boot >$L/geom-boot
mopts / >$L/mopts-root; mopts /boot >$L/mopts-boot
rootlv=$(lvs --noheadings -o vg_name,lv_name "$(findmnt -n -o SOURCE /)" | xargs)
rootpv=$(pvs --noheadings -o pv_name -S vg_name="${rootlv% *}" | xargs)
lvm_of $rootpv ${rootlv% *} ${rootlv#* } >$L/lvm-root 2>&1
gpt /dev/vda >$L/gpt-root
grep -c 'type=AVC' /var/log/audit/audit.log >$L/avc0 2>/dev/null || echo 0 >$L/avc0

# vdb as the installer laid out vda: a 1 MiB BIOS boot partition, then XFS
# partitions (XBOOTLDR type, 1 GiB like /boot: the source's and one per
# direction), then the rest a Linux LVM partition, the volume group's PV
# (lvm2's defaults, as blivet makes them).
{ echo "label: gpt"; echo "size=1MiB, type=21686148-6449-6E6F-744E-656564454649"
  for i in 1 2 3 4 5; do echo "size=1GiB, type=BC13C2FF-59E6-4262-A352-B275FD6F7172"; done
  echo "type=E6D6D379-F507-44C2-A23C-238F2A3DF928"; } | sfdisk -q --wipe always $DISK >$L/sfdisk.log 2>&1 &&
    udevadm settle && pvcreate -q ${DISK}7 >>$L/sfdisk.log 2>&1 && vgcreate -q $VG ${DISK}7 >>$L/sfdisk.log 2>&1 ||
    { echo "FAIL harness: no partitions or volume group on $DISK: $(tail -3 $L/sfdisk.log)"; exit 2; }
gpt $DISK >$L/gpt-scratch
declare -A BOOTPART=([src]=${DISK}2 [rr]=${DISK}3 [uu]=${DISK}4 [ru]=${DISK}5 [ur]=${DISK}6)
lvfs() {  # NAME: an XFS (mkfs.xfs's defaults, as /'s) on a fresh LV, mounted at /mnt/NAME as / is (fstab defaults)
    lvcreate -q -y -L 1536M -n $1 $VG >>$L/lvcreate.log 2>&1 && mkfs.xfs -q -f /dev/$VG/$1 &&
        mkdir -p /mnt/$1 && mount /dev/$VG/$1 /mnt/$1
}
bootfs() {  # PART DIR: an XFS (mkfs.xfs's defaults, as /boot's) on PART, mounted at DIR as /boot is
    mkfs.xfs -q -f $1 && mkdir -p $2 && mount $1 $2
}
atimes() {  # OPTION DIR: remount DIR and everything mounted below it OPTION (noatime, relatime)
    # relatime alone never clears a noatime already set (libmount merges the
    # mount's current options, and the kernel's MNT_NOATIME wins): atime
    # clears it first.
    local o=$1; [[ $1 == relatime ]] && o=atime,relatime
    findmnt -rn -o TARGET -R "$2" | while read -r m; do mount -o remount,$o "$m" || echo "remount $o $m failed" >>$L/remount.err; done
}
mopts_all() { findmnt -rn -o TARGET,OPTIONS -R "$1"; }  # DIR: each mount at and below DIR, its options
# XFS's own per-file options (fsxattr xflags, extent size hints) of the fixture's xfs-* paths.
xfsx() {  # TREE OUT
    for p in xfs-extsize xfs-extsize-dir xfs-extsize-dir/inherits xfs-nodefrag xfs-filestream boot/bko-xfs-extsize; do
        printf '%s ' $p; xfs_io -r -c stat "$1/$p" 2>&1 | grep -E 'fsxattr\.(xflags|extsize|cowextsize)' | tr -s ' \n' '  '; echo
    done >$2
}
lvfs src && mkdir -p $P/boot && bootfs ${BOOTPART[src]} $P/boot || { echo "FAIL harness: no source fs"; exit 2; }
feat /mnt/src >$L/feat-src; feat $P/boot >$L/feat-srcboot; geom $P/boot >$L/geom-srcboot
mopts /mnt/src >$L/mopts-src; mopts $P/boot >$L/mopts-srcboot
lvm_of ${DISK}7 $VG src >$L/lvm-src 2>&1
LVMREAL=$W/etc-lvm bash $B/fixture.sh /mnt/src 2>$L/fixture.err
sync; atimes noatime /mnt/src
python3 $B/fsmeta.py record /mnt/src $L/src.json
xfsx $P $L/src.xfsx
find $P/boot -xdev | wc -l >$L/bootcount-src
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
# Two more backups with the source on /'s own relatime (the compared ones
# ran on noatime, so that both tools saw the same atimes): they mustn't
# move the source's atimes.
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
# Each target: a fresh volume, its /boot-like partition mounted where the
# source's is (as BackOtter mounts a machine's file systems before it
# restores), both as the guest mounts them.
for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
    IFS=: read n b r <<<"$d"
    lvfs d$n && mkdir -p /mnt/d$n$P/boot && bootfs ${BOOTPART[$n]} /mnt/d$n$P/boot ||
        { echo "FAIL harness: no target file systems for $n"; continue; }
    restore $r $W/repo-$b /mnt/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime /mnt/d$n
    python3 $B/fsmeta.py record /mnt/d$n/mnt/src $L/$n.json
    xfsx /mnt/d$n$P $L/$n.xfsx
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    find /mnt/d$n$P/boot -xdev | wc -l >$L/bootcount-$n
    findmnt -n -o SOURCE --target /mnt/d$n$P/boot/grub2 >$L/bootdev-$n
done
# Restore over existing immutable and append-only files (and an immutable
# directory), changed since: each tool over its own restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=/mnt/drr || D=/mnt/duu
    X=$D$P
    chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
        echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
        echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
    lsattr -d $X/immutable $X/append-only $X/immutable-dir >$L/$n.before 2>&1
    atimes relatime $D; mopts_all $D >$L/mopts-rel-$n
    restore $t $W/repo-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime $D
    python3 $B/fsmeta.py record $D/mnt/src $L/$n.json
    xfsx $X $L/$n.xfsx
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
done
grep -c 'type=AVC' /var/log/audit/audit.log >$L/avc1 2>/dev/null || echo 0 >$L/avc1
grep 'type=AVC' /var/log/audit/audit.log | tail -n +$(($(cat $L/avc0) + 1)) >$L/avc-new.txt 2>/dev/null
getenforce >$L/enforce-after
# One non-root run (nonroot-inner.sh: backup as root, restores as uid 4321)
# on a volume made and mounted like /.
# Remounted noatime for it, as debian13's is: its listings read the files
# they list, which on relatime would move the atimes it compares.
lvfs nr && mopts /mnt/nr >$L/mopts-nr && mount -o remount,noatime /mnt/nr || echo "FAIL harness: no volume for the non-root run"
# uid 4321 can't reach /root (0550): the binaries go where it can.
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
lvs --units m $VG >$L/lvs.txt 2>&1; df -m /mnt/src $P/boot /mnt/d* /mnt/nr $W >$L/df.txt 2>&1
python3 $B/judge.py $L
# The logs for the host (the guest has no tar).
python3 -c 'import tarfile; t = tarfile.open("/root/bk/logs.tar", "w"); t.add("/mnt/bkw/logs", arcname="."); t.close()'
