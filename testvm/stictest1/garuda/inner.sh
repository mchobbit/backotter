#!/bin/bash
# In the garuda throwaway guest, as root, from /root/bk: the spare disk
# (vdb) laid out as the guest's own (GPT: per tree an ESP of the guest's
# ESP's size, FAT32 like it, and a btrfs partition made like the guest's /,
# with its subvolumes: @ and the others the guest mounts), vdc for the
# repositories and logs.  Per tree, @ is mounted as the guest mounts /
# (noatime,compress=zstd...), the other subvolumes and the ESP inside the
# backed-up path where the guest has them (tree/home, tree/var/log, ...,
# tree/boot/efi), each restore target the same, mounted before the restore
# as BackOtter does.  The fixture (fixture.sh) on the btrfs tree and on the
# ESP; the backups run on the guest's own mount options (btrfs noatime, the
# ESP's), the four directions, each tool restoring over its own restore with
# changed immutable/append-only files; then the non-root run on a btrfs
# made like the guest's, and judge.py.
set -u
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko W=/mnt/bkw L=/mnt/bkw/logs
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
DISK=/dev/vdb P=/mnt/src/up1/up2/tree
mkfs.ext4 -q -F /dev/vdc && mkdir -p $W && mount -o noatime /dev/vdc $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L
st() { echo "$1 $2" >>$L/status; }

# --- What the guest has ------------------------------------------------------
# / and the other btrfs subvolumes it mounts (from findmnt: TARGET FSROOT
# SOURCE), its ESP, their options.
rootdev=$(findmnt -n -o SOURCE /); rootdev=${rootdev%%\[*}
ROOTSV=$(findmnt -n -o FSROOT /); ROOTSV=${ROOTSV#/}
SUBS=$(findmnt -rn -t btrfs -o TARGET,FSROOT,SOURCE | awk -v d="$rootdev" '$1 != "/" && index($3, d "[") == 1 {print substr($1, 2) ":" substr($2, 2)}')
ESPM=$(findmnt -rn -t vfat -o TARGET | head -1); ESPN=${ESPM#/}
espdev=$(findmnt -n -o SOURCE "$ESPM")
export SUBS ESPM
[[ -n $ROOTSV && -n $SUBS && -n $ESPM ]] || { echo "FAIL harness: the guest's layout: / ${ROOTSV:-?} subvolumes ${SUBS:-?} ESP ${ESPM:-?}"; exit 2; }
mopts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(subvolid=.*|subvol=.*)$' | paste -sd,; }   # MOUNTPOINT: options but which subvolume
atimeopt() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -E '^(relatime|noatime|strictatime)$' | head -1; }
{ echo "ROOTSV=$ROOTSV ESPN=$ESPN SUBS=$(echo $SUBS | tr ' ' ,)"; uname -r; . /etc/os-release; echo "$PRETTY_NAME"
  pacman -Q btrfs-progs dosfstools e2fsprogs util-linux attr acl libcap 2>/dev/null | paste -sd' '; } >$L/env.txt
python3 $B/btrfsx.py super "$rootdev" >$L/super-root
mopts / >$L/mopts-root; atimeopt / >$L/atime-root
for s in $SUBS; do echo "${s%%:*} ${s#*:} $(mopts /${s%%:*})"; done >$L/mopts-subs-root
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
gpt() {  # DISK: the label and each partition's start, size, type (no ids)
    sfdisk -d $1 | sed -n 's/^label: //p; s/^[^ ]* : start= *\([0-9]*\), size= *\([0-9]*\), type=\([0-9A-F-]*\).*/\1 \2 \3/p'
}
fatgeom "$espdev" >$L/fat-esp; mopts "$ESPM" >$L/mopts-esp; atimeopt "$ESPM" >$L/atime-esp
gpt /dev/vda >$L/gpt-root
# mkfs as the guest's were made: btrfs's features, checksum, node and sector
# size from /'s superblock (btrfsx.py mkfs), FAT's type, sector and cluster
# size from the ESP's boot sector; mounted with the guest's options.
mkfs.btrfs -O list-all >$L/mkfs-btrfs-features 2>&1
mapfile -t MKB < <(python3 $B/btrfsx.py mkfs "$rootdev" $L/mkfs-btrfs-features)
read -r FATT FATS FATC <<<"$(sed 's/^FAT\([0-9]*\) bps=\([0-9]*\) spc=\([0-9]*\).*/\1 \2 \3/' $L/fat-esp)"
BOPTS=$(mopts / | tr , '\n' | grep -vE '^(rw|ro)$' | paste -sd,)
declare -A SOPTS
for s in $SUBS; do SOPTS[${s%%:*}]=$(mopts /${s%%:*} | tr , '\n' | grep -vE '^(rw|ro)$' | paste -sd,); done
EOPTS=$(mopts "$ESPM" | tr , '\n' | grep -vE '^(rw|ro)$' | paste -sd,)
ROOTA=$(cat $L/atime-root) ESPA=$(cat $L/atime-esp)
mk_btrfs() { mkfs.btrfs -q -f "${MKB[@]}" "$1" 2>>$L/mkfs-btrfs.log || { echo "mkfs.btrfs ${MKB[*]} refused: defaults" >>$L/mkfs-btrfs.log; mkfs.btrfs -q -f "$1"; }; }
mk_vfat() { mkfs.vfat -F "$FATT" -S "$FATS" -s "$FATC" -n EFI "$1" >/dev/null; }
echo "mkfs.btrfs ${MKB[*]}; btrfs mount $BOPTS; mkfs.vfat -F $FATT -S $FATS -s $FATC; ESP mount $EOPTS" >>$L/env.txt

# --- The scratch disk --------------------------------------------------------
# Per set an ESP of the guest's ESP's size and a btrfs partition (the
# guest's / partition's type), then one btrfs partition for the non-root run.
SETS="src rr uu ru ur"
espsz=$(sfdisk -d /dev/vda | sed -n "s|^$espdev : .*size= *\([0-9]*\),.*|\1|p")
roottype=$(sfdisk -d /dev/vda | sed -n "s|^$rootdev : .*type=\([0-9A-F-]*\).*|\1|p")
[[ -n $espsz && -n $roottype ]] || { echo "FAIL harness: no size for the guest's ESP $espdev or type for $rootdev"; exit 2; }
{ echo "label: gpt"
  for s in $SETS; do echo "size=$espsz, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B"; echo "size=1600MiB, type=$roottype"; done
  echo "type=$roottype"; } | sfdisk -q --wipe always $DISK >$L/sfdisk.log 2>&1 && udevadm settle ||
    { echo "FAIL harness: no partitions on $DISK: $(tail -3 $L/sfdisk.log)"; exit 2; }
gpt $DISK >$L/gpt-scratch
declare -A ESPPART BTRPART
i=0; for s in $SETS; do ESPPART[$s]=${DISK}$((i + 1)) BTRPART[$s]=${DISK}$((i + 2)); i=$((i + 2)); done
BTRPART[nr]=${DISK}$((i + 1))
mkset() {  # SET ROOT: the set's btrfs (@ and the guest's other subvolumes) with @ at ROOT; the others and the ESP at ROOT$P's places
    local dev=${BTRPART[$1]} r=$2$P s
    [[ $1 == src ]] && r=$P
    mk_btrfs $dev >>$L/mkfs.log 2>&1 && mkdir -p /mnt/top && mount -t btrfs $dev /mnt/top || return 1
    for s in $ROOTSV $(for s in $SUBS; do echo ${s#*:}; done); do btrfs -q subvolume create /mnt/top/$s >/dev/null || { umount /mnt/top; return 1; }; done
    umount /mnt/top
    mkdir -p $2 && mount -t btrfs -o "subvol=/$ROOTSV,$BOPTS" $dev $2 || return 1
    [[ $1 == nr ]] && return 0
    mkdir -p $r
    for s in $SUBS; do mkdir -p $r/${s%%:*} && mount -t btrfs -o "subvol=/${s#*:},${SOPTS[${s%%:*}]}" $dev $r/${s%%:*} || return 1; done
    mk_vfat ${ESPPART[$1]} && mkdir -p $r/$ESPN && mount -t vfat -o "$EOPTS" ${ESPPART[$1]} $r/$ESPN
}
atimes() {  # guest|noatime DIR: remount DIR and everything mounted below it with the guest's atime options, or noatime
    # relatime alone never clears a noatime already set (libmount merges
    # the mount's current options, and the kernel's MNT_NOATIME wins):
    # atime clears it first.
    local m t o
    findmnt -rn -o TARGET,FSTYPE -R "$2" | while read -r m t; do
        o=noatime
        [[ $1 == guest ]] && { [[ $t == vfat ]] && o=$ESPA || o=$ROOTA; }
        [[ $o == relatime || $o == strictatime ]] && o=atime,$o
        mount -o remount,$o "$m" || echo "remount $o $m failed" >>$L/remount.err
    done
}
mopts_all() { findmnt -rn -o TARGET,FSTYPE,OPTIONS -R "$1"; }   # DIR: each mount at and below DIR, its type and options
fatx() { python3 $B/fatattr.py "$1/$ESPN" "$2"; }   # TREE OUT: the ESP's FAT attributes
# btrfs's per-file options and how each file's data is stored (FIEMAP).
BFILES="compress-zstd compress-lzo compress-zlib compress-zstd-level compress-dir compress-dir/inherited compress-flag
        compress-immutable compress-mount nocompress nocow nocow-dir nocow-dir/inherited"
SNAPS=".snapshots .snapshots/1/snapshot subvol"
btrfsrec() {  # TREE NAME: NAME.btrfs (per-file options) and NAME.subvols
    python3 $B/btrfsx.py files "$1" $BFILES >$L/$2.btrfs 2>&1
    python3 $B/btrfsx.py subvols "$1" $SNAPS >$L/$2.subvols 2>&1
}
counts() {  # TREE NAME: per mounted file system inside the tree, its paths and its device
    local s m
    for m in $(for s in $SUBS; do echo ${s%%:*}; done) $ESPN; do
        echo "$m $(find "$1/$m" -xdev | wc -l) $(findmnt -n -o SOURCE --target "$1/$m/.")"
    done >$L/$2.counts
}

mkset src /mnt/src || { echo "FAIL harness: no source file systems: $(tail -3 $L/mkfs.log)"; exit 2; }
python3 $B/btrfsx.py super ${BTRPART[src]} >$L/super-src-new
mopts /mnt/src >$L/mopts-src; mopts $P/$ESPN >$L/mopts-srcesp; fatgeom ${ESPPART[src]} >$L/fat-src
for s in $SUBS; do echo "${s%%:*} ${s#*:} $(mopts $P/${s%%:*})"; done >$L/mopts-subs-src
btrfs subvolume list -a /mnt/src 2>&1 | awk '{print $NF}' | sed 's|^<FS_TREE>/||' | sort >$L/subvols-src
bash $B/fixture.sh /mnt/src btrfs 2>$L/fixture.err
bash $B/fixture.sh $P/$ESPN vfat 2>>$L/fixture.err
sync
# btrfs's placeholder directories in the snapshot (where a nested subvolume
# sits in it: .snapshots, subvol): their times are when they were looked
# up, which no archiver can keep; the judge leaves only those out.
python3 $B/btrfsx.py placeholders $P >$L/placeholders 2>$L/placeholders.err
python3 $B/btrfsx.py super ${BTRPART[src]} >$L/super-src
atimes noatime /mnt/src
python3 $B/fsmeta.py record /mnt/src $L/src.json
btrfsrec $P src; fatx $P $L/src.fat.json; counts $P src
# The backups on the guest's own mount options (btrfs noatime, the ESP's
# own): they mustn't move any atime, a symlink's either.
atimes guest /mnt/src; mopts_all /mnt/src >$L/mopts-backup
( cd /; time rt_backup $RESTIC $W/repo-restic $P ) >$L/backup-restic.log 2>&1; st backup-restic $?
( cd /; time rt_backup $RUSTIC $W/repo-rustic $P ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
sync; atimes noatime /mnt/src
python3 $B/fsmeta.py record /mnt/src $L/src-after.json
python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
rt_nodes $RESTIC $W/repo-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
rt_nodes $RESTIC $W/repo-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
# Each target: a fresh set (@ at /mnt/dN, the other subvolumes and its own
# ESP mounted where the source's are), on the guest's mount options.
for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
    IFS=: read n b r <<<"$d"
    mkset $n /mnt/d$n || { echo "FAIL harness: no target file systems for $n: $(tail -3 $L/mkfs.log)"; continue; }
    mopts_all /mnt/d$n >$L/mopts-$n
    restore $r $W/repo-$b /mnt/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime /mnt/d$n
    python3 $B/fsmeta.py record /mnt/d$n/mnt/src $L/$n.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    btrfsrec /mnt/d$n$P $n; fatx /mnt/d$n$P $L/$n.fat.json; counts /mnt/d$n$P $n
done
# Restore over existing immutable and append-only files (and an immutable
# directory), changed since: each tool over its own restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=/mnt/drr || D=/mnt/duu
    X=$D$P
    chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
        echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
        echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
    chattr -i $X/compress-immutable && echo changed >>$X/compress-immutable && chattr +i $X/compress-immutable
    echo more >>$X/nocow-append
    lsattr -d $X/immutable $X/append-only $X/immutable-dir >$L/$n.before 2>&1
    atimes guest $D
    restore $t $W/repo-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime $D
    python3 $B/fsmeta.py record $D/mnt/src $L/$n.json
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
done
# One non-root run (nonroot-inner.sh: backup as root, restores as uid
# 4321) on a btrfs made and mounted like / (@, noatime, compress=zstd).
mkset nr /mnt/nr && mopts /mnt/nr >$L/mopts-nr || echo "FAIL harness: no file system for the non-root run"
# uid 4321 can't reach /root (0700): the binaries go where it can.
install -d -m 755 /usr/local/bko && install -m 755 $RESTIC $RUSTIC /usr/local/bko/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh /mnt/nr /usr/local/bko/restic-bko /usr/local/bko/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
df -m /mnt/src $P/$ESPN /mnt/d* /mnt/nr $W >$L/df.txt 2>&1
btrfs filesystem df /mnt/src >>$L/df.txt 2>&1
python3 $B/judge.py $L
# The logs for the host.
tar -C /mnt/bkw/logs -cf /root/bk/logs.tar .
