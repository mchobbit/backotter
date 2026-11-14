#!/bin/bash
# In the truenas25 throwaway guest, as root, from /root/bk (/ is read-only,
# /usr read-only, /tmp and /mnt noexec; /root exec): scratch pools made
# like boot-pool, read from the guest: its vdev layout (one partition of
# the guest's pool partition type on the pool scratch disk), its ashift
# and compatibility (grub2), every feature it has enabled or active
# (zpool create -d, each one enabled), its create-time properties
# (normalization, utf8only, casesensitivity), and its datasets in their
# order with their local properties (zfs get -s local), but the inactive
# boot environments (HoltTest1's).  Each dataset mounted where the guest
# mounts it (findmnt): the active BE's root (legacy, readonly=on) as the
# tree's file system, the others inside the tree at their own paths: the
# BE's 15 noauto children (/usr, /opt and /conf readonly=on, only
# /var/log/journal with POSIX ACLs), boot-pool/grub (legacy, canmount=off,
# at /boot/grub) and the system dataset .system with its children
# (legacy, at /var/db/system).  A dataset the guest mounts twice
# (.system/cores, also at /var/lib/systemd/coredump) is mounted once, at
# its first path: BackOtter backs it up once (bko-holt's
# zfs_mounted_twice).
#
# readonly=on is held back as bko-holt's load holds it back (truenas.md
# Decision 3): each tree's datasets are made writable, filled (the
# source: the fixture; a target: the restore), then set readonly=on where
# the guest's are; the restore over existing files turns it off around
# its changes and its restore.  The judge checks that each target's
# read-only datasets end readonly=on, refuse a write and hold every file.
#
# One pool per tree (the source, four restore targets, the non-root run).
# The fixture (fixture.sh) on every dataset, four directions into a fresh
# pool each, each tool restoring over its own restore with changed
# immutable/append-only files and DOS attributes, a backup pair on
# relatime, the non-root run on a dataset made like the BE's root (left
# writable), and judge.py.
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
mkdir -p $W && mkfs.ext4 -q -F $WD && mount -o noatime $WD $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L
st() { echo "$1 $2" >>$L/status; }
echo "$G" >$L/guest
# TrueNAS 25.10 has attr and acl (setfattr, setfacl), setcap and setpriv;
# apt is off and /usr read-only, so should one be missing, bkoshim.py
# stands in for setfattr and setfacl from the work disk (exec there).
mkdir -p $W/bin $W/tmp && chmod 755 $W $W/bin && chmod 1777 $W/tmp
install -m 755 $B/bkoshim.py $W/bin/bkoshim
for t in setfattr setfacl; do
    command -v $t >/dev/null || { ln -sf bkoshim $W/bin/$t; echo "$t: bkoshim.py" >>$L/shims; }
done
export PATH=$W/bin:$PATH
python3 -c 'import fcntl, sqlite3, struct; print("python3", __import__("sys").version.split()[0])' >$L/python.txt 2>&1 ||
    { echo "FAIL harness: no python3: $(cat $L/python.txt)"; exit 2; }
# The guest's ARC kept small (the scratch pools are in QEMU's overlays in RAM).
echo $((1 << 30)) >/sys/module/zfs/parameters/zfs_arc_max

# --- What the guest has ------------------------------------------------------
POOL=$(zfs list -H -o name / | cut -d/ -f1) ROOTDS=$(zfs list -H -o name /)
[[ -n $POOL && $ROOTDS == "$POOL"/* ]] || { echo "FAIL harness: / isn't a ZFS dataset ($ROOTDS)"; exit 2; }
ROOTREL=${ROOTDS#$POOL}
BEPARENT=${ROOTDS%/*}; BEPARENT=${BEPARENT#$POOL}   # /ROOT
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
# The create-time properties (zfs get -s local doesn't show them).
ctprops() { zfs get -H -o property,value normalization,utf8only,casesensitivity "$1" | tr '\t' = | paste -sd' '; }
# Where each of a pool's datasets is mounted (findmnt; the first path of
# one mounted twice): REL PATH, and the second paths in twice-SET.
atlist() {  # POOL SET
    findmnt -rn -t zfs -o SOURCE,TARGET | awk -v p="$1" -v tw="$L/twice-$2" '
        $1 == p || index($1, p "/") == 1 { r = substr($1, length(p) + 1); r = (r == "" ? "." : r)
            if (r in at) print r, at[r], $2 >tw; else at[r] = $2 }
        END { for (r in at) print r, at[r] }' | sort
}
# A pool's datasets (but zvols and the inactive boot environments), as
# NAME-relative lines: REL TYPE CANMOUNT MOUNTPOINT AT (AT: where it is
# mounted, or -).  The BEs left out go to be-other-SET.
dslist() {  # POOL SET
    : >$L/twice-$2
    atlist "$1" "$2" >$L/at-$2
    zfs list -H -o name,type,canmount,mountpoint -r -t filesystem,volume "$1" | awk -v p="$1" -v be="$BEPARENT" -v root="$ROOTREL" \
        -v atf="$L/at-$2" -v other="$L/be-other-$2" -F'\t' '
        BEGIN { while ((getline l < atf) > 0) { split(l, f, " "); at[f[1]] = f[2] } }
        { r = substr($1, length(p) + 1); r = (r == "" ? "." : r)
          if (index(r, be "/") == 1) { b = r; sub("^" be "/", "", b); sub("/.*", "", b)
              if (be "/" b != root) { if (r == be "/" b) print b >other; next } }
          print r, $2, $3, $4, (r in at ? at[r] : "-") }'
}
# Their local properties (but mountpoint and canmount, which are where the
# scratch pools mount): REL PROP=VALUE, sorted.  Only the datasets dslist
# lists (FILE).
dsprops() {  # POOL DSFILE
    zfs get -H -o name,property,value -s local -r all "$1" | awk -v p="$1" -v dsf="$2" -F'\t' '
        BEGIN { while ((getline l < dsf) > 0) { split(l, f, " "); keep[f[1]] = 1 } }
        $2 != "mountpoint" && $2 != "canmount" {r = substr($1, length(p) + 1); r = (r == "" ? "." : r); if (r in keep) print r, $2 "=" $3}' | sort
}
# The mount options of each mounted dataset, but the atime words: REL OPTIONS
# (one line a dataset: a second mount of the same dataset left out).
dsmopts() {
    findmnt -rn -t zfs -o SOURCE,OPTIONS | awk -v p="$1" '$1 == p || index($1, p "/") == 1 {
        r = substr($1, length(p) + 1); n = split($2, o, ","); s = ""
        for (i = 1; i <= n; i++) if (o[i] !~ /^(relatime|noatime|strictatime|atime)$/) s = s (s ? "," : "") o[i]
        r = (r == "" ? "." : r); if (!(r in seen)) print r, s; seen[r] = 1 }' | sort
}
TOPO=$(topo $POOL)
PDEV=$(zpool status -P $POOL | awk '$1 ~ /^\/dev\// {print $1; exit}')
PTYPE=$(lsblk -no PARTTYPE "$PDEV" 2>/dev/null | head -1)
dslist $POOL guest >$L/ds-guest; dsprops $POOL $L/ds-guest >$L/dsprops-guest; dsmopts $POOL >$L/mopts-guest
feats $POOL >$L/feat-guest; poolprops $POOL >$L/pool-guest; ctprops $POOL >$L/ct-guest; echo "$TOPO" >$L/topo-guest
zfs get -H -o name,property,value,source all $POOL >$L/zfs-get-pool.txt 2>&1
zpool status -P $POOL >$L/zpool-status-guest.txt 2>&1
zfs list -o name,canmount,mountpoint,mounted,readonly -t all -r $POOL >$L/zfs-list-guest.txt 2>&1
findmnt -rn -t zfs -o TARGET,SOURCE,OPTIONS >$L/findmnt-guest.txt 2>&1
# Every dataset mounted but the BE's root is mounted inside the tree at
# its own path; the read-only ones (readonly=on, held back on the scratch).
MOUNTS=$(awk -v r="$ROOTREL" '$2 == "filesystem" && $5 ~ /^\// && $1 != r {print $5}' $L/ds-guest | sort | paste -sd' ')
ZVOLS=$(awk '$2 == "volume" {print $1}' $L/ds-guest | paste -sd' ')
ROS=$(awk '$2 == "readonly=on" {print $1}' $L/dsprops-guest | paste -sd' ')
{ echo "POOL=$POOL ROOTDS=$ROOTDS TOPO=$TOPO PTYPE=${PTYPE:--} NPD=${#PD[@]} MOUNTS=${MOUNTS// /,} ZVOLS=${ZVOLS// /,} RO=${ROS// /,} BES=$(paste -sd, $L/be-other-guest 2>/dev/null)"; uname -r
  . /etc/os-release; echo "$PRETTY_NAME; TrueNAS $(cat /etc/version 2>/dev/null)"; zfs version | paste -sd' '
  $RESTIC version | head -1; $RUSTIC --version; } >$L/env.txt
[[ -n $TOPO && $(awk -v r="$ROOTREL" '$1 == r && $5 == "/"' $L/ds-guest) ]] || { echo "FAIL harness: no layout read (topology '$TOPO', root dataset $ROOTDS)"; exit 2; }
FEATO=(); for f in $(cat $L/feat-guest); do FEATO+=(-o "feature@$f=enabled"); done
POOLO=(); for kv in $(cat $L/pool-guest); do [[ $kv == compatibility=off ]] || POOLO+=(-o "$kv"); done
CTO=(); for kv in $(cat $L/ct-guest); do case $kv in normalization=none|utf8only=off|casesensitivity=sensitive) ;; *) CTO+=(-O "$kv") ;; esac; done
propso() {  # REL [-o|-O]: OPT PROP=VALUE for each of the guest's local properties of REL, but readonly=on (held back)
    local o=${2:--o}
    awk -v r="$1" '$1 == r && $2 != "readonly=on" {print $2}' $L/dsprops-guest | while read -r kv; do printf '%s\n%s\n' "$o" "$kv"; done
}

# --- The scratch pools -------------------------------------------------------
# On each pool scratch disk, per tree, one partition (1.5 GiB, the guest's
# pool partition type) for each child of each top-level vdev, child j on
# disk j (mod the number of disks).
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
mappath() {  # GUESTPATH SETROOT TREE: where the scratch tree has it
    if [[ $1 == / ]]; then echo "$2"; else echo "$3$1"; fi
}
mkset() {  # SET ROOT [rootonly]: the set's pool made like the guest's, its datasets made like
           # the guest's (readonly=on held back: ro-SET lists them); each mounted dataset
           # mounted where the guest has it, the BE's root at ROOT, the others at
           # TREE+their path (TREE = ROOT$P; src: $P), in the order of their paths
    local s=$1 np=bkst-$1 r=$2$P vi=0 v n j spec=() rel ty cm mp at m new
    [[ $s == src ]] && r=$P
    for v in ${TOPO//,/ }; do
        [[ ${v%%:*} == disk ]] || spec+=(${v%%:*})
        n=${v#*:}
        for ((j = 0; j < n; j++)); do spec+=(${PART[$s:$vi:$j]}); done
        vi=$((vi + 1))
    done
    read -r rel ty cm mp at < <(awk '$1 == "."' $L/ds-guest)
    [[ $mp == /* ]] && mp=$(mappath "$mp" $2 $r)
    [[ $at == - ]] || cm=noauto
    mapfile -t RO < <(propso . -O)
    zpool create -f -d "${POOLO[@]}" "${FEATO[@]}" "${CTO[@]}" "${RO[@]}" -O canmount=$cm -O mountpoint="$mp" $np "${spec[@]}" >>$L/mkpool.log 2>&1 ||
        { echo "zpool create $np failed" >>$L/mkpool.log; return 1; }
    # zpool create mounts the root dataset even with canmount=noauto: out
    # of the way until its turn below.
    zfs unmount $np >/dev/null 2>&1; [[ $mp == /* ]] && rmdir -p "$mp" 2>/dev/null
    : >$L/mounts-$s; : >$L/ro-$s
    awk '$1 == "." && $2 == "readonly=on"' $L/dsprops-guest | grep -q . && echo "$np" >>$L/ro-$s
    [[ $at == - ]] || echo "$(mappath "$at" $2 $r) $np $(awk '$1 == "." {print $3}' $L/ds-guest) $mp" >>$L/mounts-$s
    while read -r rel ty cm mp at; do
        [[ $rel == . || $ty != filesystem ]] && continue
        new=$np$rel
        mapfile -t DO < <(propso "$rel")
        [[ $mp == /* ]] && mp=$(mappath "$mp" $2 $r)
        local c=$cm; [[ $at != - && $mp != legacy ]] && c=noauto
        zfs create -u "${DO[@]}" -o canmount=$c -o mountpoint="$mp" $new >>$L/mkpool.log 2>&1 ||
            { echo "zfs create $new failed" >>$L/mkpool.log; return 1; }
        awk -v r="$rel" '$1 == r && $2 == "readonly=on"' $L/dsprops-guest | grep -q . && echo "$new" >>$L/ro-$s
        [[ $at == - ]] || echo "$(mappath "$at" $2 $r) $new $cm $mp" >>$L/mounts-$s
    done <$L/ds-guest
    sort -o $L/mounts-$s $L/mounts-$s
    while read -r m new cm mp; do
        [[ ${3:-} == rootonly && $new != "$np$ROOTREL" ]] && continue
        if [[ $mp == legacy ]]; then mkdir -p "$m" && mount -t zfs $new "$m" >>$L/mkpool.log 2>&1
        else zfs mount $new >>$L/mkpool.log 2>&1; fi || { echo "mount $new at $m failed" >>$L/mkpool.log; return 1; }
    done <$L/mounts-$s
    # canmount as the guest's (on, noauto), now that they're mounted.
    while read -r m new cm mp; do [[ $mp == legacy ]] || zfs set canmount=$cm $new >>$L/mkpool.log 2>&1; done <$L/mounts-$s
}
rdonly() {  # on|off SET: readonly on the set's datasets that the guest has readonly=on
    local d; for d in $(cat $L/ro-$2); do zfs set readonly=$1 $d >>$L/readonly.log 2>&1 || echo "zfs set readonly=$1 $d failed" >>$L/readonly.log; done
}
rocheck() {  # SET: each held-back dataset's readonly, and whether a write is refused
    local d m
    for d in $(cat $L/ro-$1); do
        m=$(findmnt -rn -t zfs -S "$d" -o TARGET | head -1)
        if [[ -n $m ]] && touch "$m/.bko-ro-probe" 2>/dev/null; then w=written; rm -f "$m/.bko-ro-probe"; else w=refused; fi
        echo "${d#bkst-$1} $(zfs get -H -o value,source readonly $d | tr '\t' :) $w"
    done >$L/rocheck-$1
}
atimes() {  # OPTION DIR: remount DIR and everything mounted below it OPTION (noatime, relatime)
    local o=$1; [[ $1 == relatime ]] && o=atime,relatime
    findmnt -rn -o TARGET -R "$2" | while read -r m; do mount -o remount,$o "$m" || echo "remount $o $m failed" >>$L/remount.err; done
}
mopts_all() { findmnt -rn -o TARGET,OPTIONS -R "$1"; }  # DIR: each mount at and below DIR, its options
dbcheck() {  # DIR NAME: SQLite's integrity check of each copy of the config database under DIR
    local f
    for f in $(cd "$1" && find . -name freenas-v1.db -type f | sort); do
        echo "$f $(python3 -c 'import sqlite3, sys; c = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro&immutable=1", uri=True); print(c.execute("PRAGMA integrity_check").fetchone()[0])' "$1/$f" 2>&1 | tail -1)"
    done >$L/dbcheck-$2
}
mkset src /mnt/src || { echo "FAIL harness: no source pool: $(tail -3 $L/mkpool.log)"; exit 2; }
bash $B/fixture.sh /mnt/src main 2>$L/fixture.err
for m in $MOUNTS; do bash $B/fixture.sh $P$m ds $m 2>>$L/fixture.err; done
sync; rdonly on src; atimes noatime /mnt/src
dslist bkst-src src >$L/ds-src; dsprops bkst-src $L/ds-src >$L/dsprops-src; dsmopts bkst-src >$L/mopts-src
feats bkst-src >$L/feat-src; poolprops bkst-src >$L/pool-src; ctprops bkst-src >$L/ct-src; topo bkst-src >$L/topo-src
zpool status -P bkst-src >$L/zpool-status-src.txt 2>&1; rocheck src
python3 $B/fsmeta.py record /mnt/src $L/src.json
dbcheck /mnt/src src
# The datasets inside the tree, by key (audit, var_log_journal ...): GUESTPATH.
INSIDE=""; for m in $MOUNTS; do k=${m#/}; INSIDE="$INSIDE ${k//\//_}:$m"; done
for i in $INSIDE; do find $P${i#*:} -xdev | wc -l >$L/count-src-${i%%:*}; done
( cd /; time rt_backup $RESTIC $W/repo-restic $P ) >$L/backup-restic.log 2>&1; st backup-restic $?
( cd /; time rt_backup $RUSTIC $W/repo-rustic $P ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
python3 $B/fsmeta.py record /mnt/src $L/src-after.json
python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
rt_nodes $RESTIC $W/repo-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
rt_nodes $RESTIC $W/repo-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
du -sk $W/repo-restic $W/repo-rustic >$L/repo-size.txt 2>&1
# Each tool checks the other's repository (all of its data).
$RESTIC -r $W/repo-rustic check --read-data >$L/check-restic-on-rustic.log 2>&1; st check-restic-on-rustic $?
$RUSTIC --no-progress --no-cache -r $W/repo-restic check --read-data >$L/check-rustic-on-restic.log 2>&1; st check-rustic-on-restic $?
for t in restic rustic; do  # the directories above, as each snapshot holds them
    $RESTIC -r $W/repo-$t ls --json latest 2>/dev/null | grep -E '"path":"/(mnt|mnt/src|mnt/src/up1|mnt/src/up1/up2)"' >$L/above-$t.json
done
# Two more backups with the source on relatime (the guest mounts /, /etc,
# /conf, /home, /opt, /root, grub and the system dataset relatime, the
# rest noatime; the compared ones ran on noatime, so that both tools saw
# the same atimes): they mustn't move the source's atimes.
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
# are (as BackOtter mounts a machine's file systems before it restores),
# readonly=on held back until the restore is done.
for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
    IFS=: read n b r <<<"$d"
    mkset $n /mnt/d$n || { echo "FAIL harness: no target pool for $n: $(tail -3 $L/mkpool.log)"; continue; }
    zfs get -H -o name,value readonly -r bkst-$n | awk '$2 == "on"' >$L/ro-before-$n
    restore $r $W/repo-$b /mnt/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; rdonly on $n; atimes noatime /mnt/d$n
    dslist bkst-$n $n >$L/ds-$n; dsprops bkst-$n $L/ds-$n >$L/dsprops-$n; dsmopts bkst-$n >$L/mopts-$n; rocheck $n
    python3 $B/fsmeta.py record /mnt/d$n/mnt/src $L/$n.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    dbcheck /mnt/d$n/mnt/src $n
    for i in $INSIDE; do
        x=/mnt/d$n$P${i#*:}
        find $x -xdev | wc -l >$L/count-$n-${i%%:*}; findmnt -n -o SOURCE --target $x/. >$L/dev-$n-${i%%:*}
    done
done
# Restore over existing immutable and append-only files (and an immutable
# directory) and a changed DOS-readonly file on every dataset: each tool
# over its own restore, readonly off around the changes and the restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=/mnt/drr s=rr || D=/mnt/duu s=uu
    : >$L/$n.before
    rdonly off $s
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
    sync; rdonly on $s; atimes noatime $D
    rocheck $s; cp $L/rocheck-$s $L/rocheck-$n
    python3 $B/fsmeta.py record $D/mnt/src $L/$n.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
done
# One non-root run (nonroot-inner.sh: backup as root, restores as uid
# 4321) on a dataset made like the BE's root (in a pool made like the
# guest's; readonly held back, never set: the run writes there),
# remounted noatime: nonroot-inner.sh compares atimes, which relatime
# moves when the backups read the files.
{ mkset nr /mnt/nr rootonly && findmnt -n -o OPTIONS /mnt/nr >$L/mopts-nr &&
    mount -o remount,noatime /mnt/nr; } ||
    echo "FAIL harness: no dataset for the non-root run"
# uid 4321 can't reach /root (0700), and /mnt is noexec: the binaries go
# on the work disk (/mnt/bkw, exec).
install -m 755 $RESTIC $RUSTIC $W/bin/
RT_TMP=$W/tmp FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/nr $W/bin/restic-bko $W/bin/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
zpool list -v >$L/zpool-list.txt 2>&1
df -m /mnt/src /mnt/d* /mnt/nr $W >$L/df.txt 2>&1
python3 $B/judge.py $L
# The logs for the host.
tar -C /mnt/bkw/logs -cf /root/bk/logs.tar .
