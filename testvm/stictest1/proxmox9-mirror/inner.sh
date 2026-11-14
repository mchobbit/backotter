#!/bin/bash
# In the proxmox9 or proxmox9-mirror throwaway guest, as root, from
# /root/bk: scratch pools made like the guest's root pool, read from the
# guest: its vdev layout (one disk, or a mirror: one partition per child on
# each of the pool scratch disks, of the guest's pool partition type), its
# ashift, autotrim and compatibility, every feature it has enabled or
# active (zpool create -d, each one enabled), and its datasets in their
# order with their local properties (zfs get -s local), each mounted where
# the guest mounts it: the root dataset (rpool/ROOT/pve-1, /) as the tree's
# file system, the others (rpool at /rpool, rpool/ROOT, rpool/data, a CT's
# rpool/data/subvol-N-disk-M with xattr=sa and refquota, rpool/var-lib-vz
# at /var/lib/vz) inside the tree at their own mount points, before the
# restore, as BackOtter mounts them.  zvols (VM disks) aren't made: no
# files.  One pool per tree (the source, four restore targets, the
# non-root run).  The fixture (fixture.sh) on every dataset, four
# directions into a fresh pool each, each tool restoring over its own
# restore with changed immutable/append-only files and DOS attributes, a
# backup pair on relatime, the non-root run on a dataset like the root
# dataset, and judge.py.
#
# Usage: inner.sh GUEST
set -u
G=${1:?guest}
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
P=/mnt/src/up1/up2/tree
# The scratch disks, by their sizes (check.sh's): the pool disks 10 GiB
# each, the work disk 6 GiB.
PD=() WD=
for d in /sys/block/vd*; do
    case $(($(cat $d/size) * 512)) in $((10 << 30))) PD+=(/dev/${d##*/}) ;; $((6 << 30))) WD=/dev/${d##*/} ;; esac
done
[[ -n $WD && ${#PD[@]} -ge 1 ]] || { echo "FAIL harness: no scratch disks (pool ${PD[*]}, work $WD)"; exit 2; }
mkfs.ext4 -q -F $WD && mkdir -p $W && mount -o noatime $WD $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L
st() { echo "$1 $2" >>$L/status; }
echo "$G" >$L/guest
# Proxmox has neither attr nor acl: bkoshim.py is setfattr and setfacl
# (fixture.sh and nonroot-inner.sh use them).  setcap and setpriv are the guest's.
mkdir -p /usr/local/bin
for t in setfattr setfacl; do
    command -v $t >/dev/null || { install -m 755 $B/bkoshim.py /usr/local/bin/bkoshim; ln -sf bkoshim /usr/local/bin/$t; echo "$t: bkoshim.py" >>$L/shims; }
done
export PATH=/usr/local/bin:$PATH
# The guest's ARC kept small (the scratch pools are in QEMU's overlays in RAM).
echo $((1 << 30)) >/sys/module/zfs/parameters/zfs_arc_max

# --- What the guest has ------------------------------------------------------
POOL=$(zfs list -H -o name / | cut -d/ -f1) ROOTDS=$(zfs list -H -o name /)
[[ -n $POOL && $ROOTDS == "$POOL"/* ]] || { echo "FAIL harness: / isn't a ZFS dataset ($ROOTDS)"; exit 2; }
topo() {  # POOL: its top-level vdevs, TYPE:CHILDREN (mirror:2, disk:1), comma-separated
    zpool status -P "$1" | awk -v p="$1" '
        /^config:/ {c = 1; next} /^errors:/ {c = 0}
        c && $1 == p {inp = 1; next}
        c && inp && NF && $1 !~ /^(logs|cache|spares|special|dedup)$/ {
            match($0, /^\t */); d = RLENGTH - 1
            if (d == 2) { if (t != "") print t ":" n; t = $1; n = 0; if (t ~ /^\//) { t = "disk"; n = 1 } else sub(/-[0-9]+$/, "", t) }
            else if (d == 4) n++
        }
        END { if (t != "") print t ":" n }' | paste -sd,
}
feats() { zpool get -H -o property,value all "$1" | awk '$1 ~ /^feature@/ && $2 != "disabled" {sub("feature@", "", $1); print $1}' | sort | paste -sd' '; }
poolprops() { zpool get -H -o property,value ashift,autotrim,compatibility,autoexpand,multihost "$1" | tr '\t' = | paste -sd' '; }
# A pool's datasets (but zvols), as NAME-relative lines: REL TYPE CANMOUNT MOUNTPOINT
dslist() { zfs list -H -o name,type,canmount,mountpoint -r -t filesystem,volume "$1" | awk -v p="$1" -F'\t' '{r = substr($1, length(p) + 1); print (r == "" ? "." : r), $2, $3, $4}'; }
# Their local properties (but mountpoint and canmount, which are where the
# scratch pools mount): REL PROP=VALUE, sorted.
dsprops() {
    zfs get -H -o name,property,value -s local -r all "$1" | awk -v p="$1" -F'\t' '$2 != "mountpoint" && $2 != "canmount" {r = substr($1, length(p) + 1); print (r == "" ? "." : r), $2 "=" $3}' | sort
}
# The mount options of each mounted dataset, but the atime words: REL OPTIONS
dsmopts() {
    findmnt -rn -t zfs -o SOURCE,OPTIONS | awk -v p="$1" '$1 == p || index($1, p "/") == 1 {
        r = substr($1, length(p) + 1); n = split($2, o, ","); s = ""
        for (i = 1; i <= n; i++) if (o[i] !~ /^(relatime|noatime|strictatime|atime)$/) s = s (s ? "," : "") o[i]
        print (r == "" ? "." : r), s }' | sort
}
TOPO=$(topo $POOL)
PDEV=$(zpool status -P $POOL | awk '$1 ~ /^\/dev\// {print $1; exit}')
PTYPE=$(lsblk -no PARTTYPE "$PDEV" 2>/dev/null | head -1)
dslist $POOL >$L/ds-guest; dsprops $POOL >$L/dsprops-guest; dsmopts $POOL >$L/mopts-guest
feats $POOL >$L/feat-guest; poolprops $POOL >$L/pool-guest; echo "$TOPO" >$L/topo-guest
zfs get -H -o name,property,value,source all $POOL >$L/zfs-get-pool.txt 2>&1
zpool status -P $POOL >$L/zpool-status-guest.txt 2>&1
zfs list -t all -r $POOL >$L/zfs-list-guest.txt 2>&1
# The root dataset's mount point is /; the rest (but zvols and canmount=off
# or none/legacy ones) are mounted at their own mount points.
ROOTREL=${ROOTDS#$POOL}
MOUNTS=$(awk -v r="$ROOTREL" '$2 == "filesystem" && $3 != "off" && $4 ~ /^\// && $1 != r {print $4}' $L/ds-guest | sort | paste -sd' ')
ZVOLS=$(awk '$2 == "volume" {print $1}' $L/ds-guest | paste -sd' ')
{ echo "POOL=$POOL ROOTDS=$ROOTDS TOPO=$TOPO PTYPE=${PTYPE:--} NPD=${#PD[@]} MOUNTS=${MOUNTS// /,} ZVOLS=${ZVOLS// /,}"; uname -r
  . /etc/os-release; echo "$PRETTY_NAME; $(pveversion 2>/dev/null)"; zfs version | paste -sd' '
  dpkg-query -W -f '${Package}=${Version} ' zfsutils-linux proxmox-kernel-helper pve-manager libcap2-bin e2fsprogs util-linux gdisk 2>/dev/null; echo
  $RESTIC version | head -1; $RUSTIC --version; } >$L/env.txt
[[ -n $TOPO && $(awk -v r="$ROOTREL" '$1 == r && $4 == "/"' $L/ds-guest) ]] || { echo "FAIL harness: no layout read (topology '$TOPO', root dataset $ROOTDS)"; exit 2; }
FEATO=(); for f in $(cat $L/feat-guest); do FEATO+=(-o "feature@$f=enabled"); done
POOLO=(); for kv in $(cat $L/pool-guest); do [[ $kv == compatibility=off ]] || POOLO+=(-o "$kv"); done
propso() {  # REL: -o PROP=VALUE for each of the guest's local properties of REL (zpool create: -O)
    local o=${2:--o}
    awk -v r="$1" '$1 == r {print $2}' $L/dsprops-guest | while read -r kv; do printf '%s\n%s\n' "$o" "$kv"; done
}

# --- The scratch pools -------------------------------------------------------
# On each pool scratch disk, per tree, one partition (1.5 GiB, the guest's
# pool partition type) for each child of each top-level vdev, child j on
# disk j (mod the number of disks): a mirror's two sides on two disks.
SETS="src rr uu ru ur nr"
declare -A PART   # SET:VDEV:CHILD -> /dev/vdXN
declare -A NEXT
for d in "${PD[@]}"; do sgdisk -Z $d >>$L/sgdisk.log 2>&1; NEXT[$d]=0; done
ok=1
for s in $SETS; do
    vi=0
    for v in ${TOPO//,/ }; do
        n=${v#*:}
        for ((j = 0; j < n; j++)); do
            d=${PD[$((j % ${#PD[@]}))]}; k=$((NEXT[$d] + 1)); NEXT[$d]=$k
            sgdisk -n "$k:0:+1536M" -t "$k:${PTYPE:-bf01}" $d >>$L/sgdisk.log 2>&1 || ok=0
            PART[$s:$vi:$j]=$d$k
        done
        vi=$((vi + 1))
    done
done
for d in "${PD[@]}"; do partx -u $d >>$L/sgdisk.log 2>&1; done; udevadm settle
((ok)) || { echo "FAIL harness: no partitions on ${PD[*]}: $(tail -3 $L/sgdisk.log)"; exit 2; }
lsblk -o NAME,SIZE,PARTTYPE "${PD[@]}" >$L/lsblk-scratch.txt 2>&1
mappath() {  # GUESTMOUNT SETROOT TREE: where the scratch tree mounts it
    if [[ $1 == / ]]; then echo "$2"; else echo "$3$1"; fi
}
mkset() {  # SET ROOT [rootonly]: the set's pool made like the guest's, its datasets made like
           # the guest's; the root dataset mounted at ROOT, the others at TREE+their mount point
           # (TREE = ROOT$P; src: $P), in the order of their mount points
    local s=$1 np=bkst-$1 r=$2$P vi=0 v n j spec=() rel ty cm mp m new
    [[ $s == src ]] && r=$P
    for v in ${TOPO//,/ }; do
        [[ ${v%%:*} == disk ]] || spec+=(${v%%:*})
        n=${v#*:}
        for ((j = 0; j < n; j++)); do spec+=(${PART[$s:$vi:$j]}); done
        vi=$((vi + 1))
    done
    m=$(awk '$1 == "." {print $4}' $L/ds-guest)
    mapfile -t RO < <(propso . -O)
    zpool create -f -d "${POOLO[@]}" "${FEATO[@]}" "${RO[@]}" -O canmount=noauto -O mountpoint="$(mappath "$m" $2 $r)" $np "${spec[@]}" >>$L/mkpool.log 2>&1 ||
        { echo "zpool create $np failed" >>$L/mkpool.log; return 1; }
    # zpool create mounts the root dataset even with canmount=noauto: out
    # of the way (and its mount point's directories off the guest's own /)
    # until its turn below.
    zfs unmount $np >>$L/mkpool.log 2>&1; rmdir -p "$(mappath "$m" $2 $r)" 2>/dev/null
    : >$L/mounts-$s
    echo "$(mappath "$m" $2 $r) $np $(awk '$1 == "." {print $3}' $L/ds-guest)" >>$L/mounts-$s
    while read -r rel ty cm mp; do
        [[ $rel == . || $ty != filesystem ]] && continue
        new=$np$rel
        mapfile -t DO < <(propso "$rel")
        if [[ $mp == /* ]]; then m=$(mappath "$mp" $2 $r); else m=$mp; fi
        zfs create -u "${DO[@]}" -o canmount=noauto -o mountpoint="$m" $new >>$L/mkpool.log 2>&1 ||
            { echo "zfs create $new failed" >>$L/mkpool.log; return 1; }
        [[ $mp == /* && $cm != off ]] && echo "$m $new $cm" >>$L/mounts-$s
    done <$L/ds-guest
    sort -o $L/mounts-$s $L/mounts-$s
    while read -r m new cm; do
        [[ ${3:-} == rootonly && $new != "$np$ROOTREL" ]] && continue
        zfs mount $new >>$L/mkpool.log 2>&1 || { echo "zfs mount $new at $m failed" >>$L/mkpool.log; return 1; }
    done <$L/mounts-$s
    # canmount as the guest's (on, noauto), now that they're mounted.
    while read -r m new cm; do zfs set canmount=$cm $new >>$L/mkpool.log 2>&1; done <$L/mounts-$s
}
atimes() {  # OPTION DIR: remount DIR and everything mounted below it OPTION (noatime, relatime)
    local o=$1; [[ $1 == relatime ]] && o=atime,relatime
    findmnt -rn -o TARGET -R "$2" | while read -r m; do mount -o remount,$o "$m" || echo "remount $o $m failed" >>$L/remount.err; done
}
mopts_all() { findmnt -rn -o TARGET,OPTIONS -R "$1"; }  # DIR: each mount at and below DIR, its options
mkset src /mnt/src || { echo "FAIL harness: no source pool: $(tail -3 $L/mkpool.log)"; exit 2; }
dslist bkst-src >$L/ds-src; dsprops bkst-src >$L/dsprops-src; dsmopts bkst-src >$L/mopts-src
feats bkst-src >$L/feat-src; poolprops bkst-src >$L/pool-src; topo bkst-src >$L/topo-src
zpool status -P bkst-src >$L/zpool-status-src.txt 2>&1
bash $B/fixture.sh /mnt/src main 2>$L/fixture.err
for m in $MOUNTS; do bash $B/fixture.sh $P$m ds $m 2>>$L/fixture.err; done
sync; atimes noatime /mnt/src
python3 $B/fsmeta.py record /mnt/src $L/src.json
# The datasets inside the tree, by key (rpool, rpool_data, var_lib_vz ...): GUESTMOUNT.
INSIDE=""; for m in $MOUNTS; do k=${m#/}; INSIDE="$INSIDE ${k//\//_}:$m"; done
for i in $INSIDE; do find $P${i#*:} -xdev | wc -l >$L/count-src-${i%%:*}; done
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
# Two more backups with the source on relatime (the guest's datasets all
# mount relatime: atime=on relatime=on on rpool; the compared ones ran on
# noatime, so that both tools saw the same atimes): they mustn't move the
# source's atimes.
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
# Each target: a fresh pool with the datasets mounted where the source's
# are (as BackOtter mounts a machine's file systems before it restores).
for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
    IFS=: read n b r <<<"$d"
    mkset $n /mnt/d$n || { echo "FAIL harness: no target pool for $n: $(tail -3 $L/mkpool.log)"; continue; }
    dsprops bkst-$n >$L/dsprops-$n; dsmopts bkst-$n >$L/mopts-$n
    restore $r $W/repo-$b /mnt/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime /mnt/d$n
    python3 $B/fsmeta.py record /mnt/d$n/mnt/src $L/$n.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    for i in $INSIDE; do
        x=/mnt/d$n$P${i#*:}
        find $x -xdev | wc -l >$L/count-$n-${i%%:*}; findmnt -n -o SOURCE --target $x/. >$L/dev-$n-${i%%:*}
    done
done
# Restore over existing immutable and append-only files (and an immutable
# directory) and a changed DOS-readonly file on every dataset: each tool
# over its own restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=/mnt/drr || D=/mnt/duu
    : >$L/$n.before
    for X in $D$P $(for m in $MOUNTS; do echo $D$P$m; done); do
        chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
            echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
            echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
        python3 - $X/dos-readonly <<'PY' >>$L/$n.before 2>&1
import fcntl, os, struct, sys
def dos(p, v):
    fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK); fcntl.ioctl(fd, 0x40088302, struct.pack("Q", v)); os.close(fd)
dos(sys.argv[1], 0x800000000); open(sys.argv[1], "a").write("changed\n"); dos(sys.argv[1], 0x900000000)
print(f"{sys.argv[1]}: DOS readonly file changed")
PY
        lsattr -d $X/immutable $X/append-only $X/immutable-dir >>$L/$n.before 2>&1
    done
    atimes relatime $D; mopts_all $D >$L/mopts-rel-$n
    restore $t $W/repo-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime $D
    python3 $B/fsmeta.py record $D/mnt/src $L/$n.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
done
# One non-root run (nonroot-inner.sh: backup as root, restores as uid
# 4321) on a dataset made like the root dataset (in a pool made like the
# guest's), remounted noatime: nonroot-inner.sh compares atimes, which
# relatime moves when the backups read the files.
{ mkset nr /mnt/nr rootonly && findmnt -n -o OPTIONS /mnt/nr >$L/mopts-nr &&
    mount -o remount,noatime /mnt/nr; } ||
    echo "FAIL harness: no dataset for the non-root run"
# uid 4321 can't reach /root (0700): the binaries go where it can.
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
zpool list -v >$L/zpool-list.txt 2>&1
df -m /mnt/src /mnt/d* /mnt/nr $W >$L/df.txt 2>&1
python3 $B/judge.py $L
# The logs for the host.
tar -C /mnt/bkw/logs -cf /root/bk/logs.tar .
