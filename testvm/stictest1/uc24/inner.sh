#!/bin/bash
# In the uc24 throwaway guest, as root, from /root/bk: the spare disk (vdb)
# laid out as the guest's own disk, read from the guest, once per tree: a
# GPT with the guest's partitions by name and type (BIOS Boot, ubuntu-seed
# with the ESP type, ubuntu-boot, ubuntu-save, ubuntu-data), smaller where
# the gadget lets the size go (seed big enough for its FAT32 geometry, boot
# 256 MiB, data 1536 MiB; BIOS Boot and save the guest's own).  ubuntu-seed
# vfat as the image makes it (the guest's FAT geometry, label and fixed
# volume ID, ABA0-ACE2 on every install); ubuntu-boot and ubuntu-save ext4
# with the guest's features, block and inode size; ubuntu-data LUKS2 as
# uc24-fde's (docs/design/ubuntu-core.md, "What U1 found": label
# ubuntu-data-enc, aes-xts-plain64 512-bit, 2 MiB metadata areas, 2.5 MiB
# of keyslots, the data at 7 MiB; keyslot 0 preferred (a random key, as
# the TPM's), keyslot 1 a 16-byte key as the recovery key's) with a
# systemd-tpm2-like token, holding ext4 ubuntu-data.  All four mounted at
# snap-bootstrap's points under a tmpfs (as /run is one): the tree is
# $M/src/up1/up2/run/mnt/{ubuntu-seed,ubuntu-boot,ubuntu-save,data}, and
# each backup holds the four roots, as bko-holt's dump lists them.
#
# Each restore target is made as bko-holt load makes one (the plans in
# "What U2/U3 found"): the source's LUKS2 header put back with
# luksHeaderRestore and opened by key (no luksFormat, no luksAddKey), ext4
# inside and beside it with the source's UUID, label and hash seed,
# mkfs.fat with the source's volume ID and label.  Four directions into the
# same layout made fresh, each tool restoring over its own restore (a
# changed .snap among the changes), a backup pair on relatime, the non-root
# run on an ext4 of data's features; then every LUKS2 header compared byte
# for byte with the source's (its keyslots, priorities and token too) and
# each one's keyslots opened by key; judge.py.
#
# Usage: inner.sh GUEST
set -u
G=${1:?guest}
B=/root/bk RESTIC=/root/bk/restic-bko RUSTIC=/root/bk/rustic-bko
# Ubuntu Core's /usr and /mnt are the base snap's (read-only): the scratch
# is a tmpfs of our own under /run.
M=/run/bkst
mkdir -p $M && mount -t tmpfs -o mode=755,size=64m bkst $M || { echo "FAIL harness: no tmpfs at $M"; exit 2; }
W=$M/w L=$M/w/logs K=$M/w/keys
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C RT_TMP=/tmp
. $B/rt-common.sh
DISK=/dev/vdb P=$M/src/up1/up2/run/mnt
mkfs.ext4 -q -F /dev/vdc && mkdir -p $W && mount -o noatime /dev/vdc $W || { echo "FAIL harness: no work disk"; exit 2; }
mkdir -p $L && install -d -m 700 $K
trap 'tar -C $L -cf /root/bk/logs.tar .' EXIT   # (the logs for the host, also after a harness FAIL)
st() { echo "$1 $2" >>$L/status; }
echo "$G" >$L/guest
# The base has no attr or acl package: bkoshim.py is setfattr and setfacl
# (fixture.sh and nonroot-inner.sh use them).  setcap and setpriv are the base's.
mkdir -p $B/shim
for n in setfattr setfacl; do
    command -v $n >/dev/null || { ln -sf $B/bkoshim.py $B/shim/$n; echo "$n: bkoshim.py" >>$L/shims; }
done
export PATH=$B/shim:$PATH

# --- What the guest has ------------------------------------------------------
SEEDM=/run/mnt/ubuntu-seed BOOTM=/run/mnt/ubuntu-boot SAVEM=/run/mnt/ubuntu-save DATAM=/run/mnt/data
export SEEDM BOOTM SAVEM DATAM
declare -A GDEV GMNT=([seed]=$SEEDM [boot]=$BOOTM [save]=$SAVEM [data]=$DATAM) RD=([seed]=ubuntu-seed [boot]=ubuntu-boot [save]=ubuntu-save [data]=data)
for r in seed boot save data; do GDEV[$r]=$(findmnt -n -o SOURCE "${GMNT[$r]}"); done
[[ -b ${GDEV[seed]} && -b ${GDEV[data]} ]] || { echo "FAIL harness: the guest's four roots aren't mounted at /run/mnt (${GDEV[*]})"; exit 2; }
GD=/dev/$(lsblk -n -o PKNAME "${GDEV[seed]}" | head -1)
python3 $B/gpt.py "$GD" >$L/gpt-guest || { echo "FAIL harness: no GPT on the guest's disk $GD"; exit 2; }
declare -A ROLE PNAME
while read -r n _ _ _ _ nm; do
    case $nm in BIOS_Boot) ROLE[$n]=bios ;; ubuntu-seed) ROLE[$n]=seed ;; ubuntu-boot) ROLE[$n]=boot ;; ubuntu-save) ROLE[$n]=save ;; ubuntu-data) ROLE[$n]=data ;; *) ROLE[$n]=other ;; esac
    PNAME[$n]=${nm//_/ }
done < <(tail -n +2 $L/gpt-guest)
ROLES=$(tail -n +2 $L/gpt-guest | while read -r n _; do echo "${ROLE[$n]}"; done | paste -sd,)
lab() { blkid -p -s LABEL -o value "$1"; }
blk() {  # DEV: TYPE LABEL UUID LABEL_FATBOOT as blkid's low-level probe sees them
    blkid -p -o export "$1" | grep -E '^(TYPE|LABEL|UUID|LABEL_FATBOOT)=' | sort | paste -sd' '
}
fatgeom() {  # DEV: "FAT32 bps=512 spc=1 nfats=2 reserved=32"
    python3 - "$1" <<'PY'
import struct, sys
b = open(sys.argv[1], "rb").read(512)
bps, spc, res, nf = struct.unpack_from("<HBHB", b, 11)
fsz16 = struct.unpack_from("<H", b, 22)[0]
t = "FAT32" if fsz16 == 0 else (b[54:62].decode("ascii", "replace").strip() or "FAT16")
print(f"{t} bps={bps} spc={spc} nfats={nf} reserved={res}")
PY
}
fatid() {  # DEV: the boot sector's volume ID and label (FAT32's offsets, else FAT12/16's)
    python3 - "$1" <<'PY'
import struct, sys
b = open(sys.argv[1], "rb").read(512)
o = 67 if struct.unpack_from("<H", b, 22)[0] == 0 else 39
v = struct.unpack_from("<I", b, o)[0]
print(f"{v >> 16:04X}-{v & 0xffff:04X} {b[o + 4:o + 15].decode('ascii', 'replace').rstrip()!r}")
PY
}
extfeat() { tune2fs -l "$1" | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | tr '\n' ' '; echo; }
extgeo() { tune2fs -l "$1" | grep -E '^(Inode size|Block size|Default mount options|Flex block group size|Default directory hash|Filesystem flags):' | tr -s ' ' | paste -sd';'; }
extid() { tune2fs -l "$1" | sed -n 's/^\(Filesystem UUID\|Directory Hash Seed\): *//p' | paste -sd' '; }   # DEV: "UUID HASHSEED"
# "(none)" where only rw and the atime option are left (ubuntu-boot's
# rw,relatime): the judge reads an empty record as a probe that failed.
mopts() { local o; o=$(findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(relatime|noatime|strictatime|atime|ro|rw)$' | paste -sd,); echo "${o:-(none)}"; }
fullopts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(rw|ro)$' | paste -sd,; }
atimeopt() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -E '^(relatime|noatime|strictatime)$' | head -1; }
FATG=$(fatgeom "${GDEV[seed]}")
read -r FATT FATS FATC FATN FATR <<<"$(sed 's/^FAT\([0-9]*\) bps=\([0-9]*\) spc=\([0-9]*\) nfats=\([0-9]*\) reserved=\([0-9]*\)/\1 \2 \3 \4 \5/' <<<"$FATG")"
VOLID=$(blkid -p -s UUID -o value "${GDEV[seed]}")
# The seed scratch: 128 MiB, or what FAT32 needs at the guest's cluster size.
need=$(( (65600 * FATC * FATS + 1048575) / 1048576 + 8 ))
SEED_MIB=$(( need > 128 ? need : 128 ))
for r in boot save data; do
    extfeat "${GDEV[$r]}" >$L/feat-guest-$r; extgeo "${GDEV[$r]}" >$L/geo-guest-$r
done
for r in seed boot save data; do
    mopts "${GMNT[$r]}" >$L/mopts-guest-$r; atimeopt "${GMNT[$r]}" >$L/atime-guest-$r
    echo "guest $r ${GDEV[$r]} $(blk "${GDEV[$r]}")" >>$L/labels-guest
done
echo "fatgeom $FATG" >$L/fat-guest; echo "fatid $(fatid "${GDEV[seed]}")" >>$L/fat-guest
{ echo "ROLES=$ROLES VOLID=$VOLID SEED_MIB=$SEED_MIB GD=$GD P=$P"; uname -r; . /etc/os-release; echo "$PRETTY_NAME"
  snap version 2>&1 | paste -sd' '
  snap list --all 2>&1 | awk 'NR > 1 { printf "%s=%s ", $1, $3 } END { print "" }'
  cryptsetup --version; mksquashfs -version 2>&1 | head -1; mkfs.fat --help 2>&1 | head -1; mke2fs -V 2>&1 | head -1
} >$L/env.txt
cp /run/mnt/data/system-data/var/lib/snapd/modeenv $L/modeenv-guest 2>/dev/null
# mkfs as the guest's were made (bko-holt load's commands): FAT's type,
# sector and cluster size, FATs and reserved sectors, with -i VOLID; ext4's
# features, block and inode size; each mounted with the guest's options.
extopts() {  # DEV: mke2fs's -O -b -I for it
    echo "-O none,$(extfeat "$1" | tr ' ' '\n' | grep . | paste -sd,) -b $(tune2fs -l "$1" | sed -n 's/^Block size: *//p') -I $(tune2fs -l "$1" | sed -n 's/^Inode size: *//p')"
}
declare -A EXTO MOPT
for r in boot save data; do EXTO[$r]=$(extopts "${GDEV[$r]}"); done
for r in seed boot save data; do MOPT[$r]=$(fullopts "${GMNT[$r]}"); MOPT[$r]=${MOPT[$r]:-defaults}; done
SLABEL=$(lab "${GDEV[seed]}")
mk_vfat() {  # DEV
    mkfs.fat -F "$FATT" -i "${VOLID//-/}" -n "$SLABEL" -S "$FATS" -s "$FATC" -f "$FATN" -R "$FATR" -h 0 "$1" >>$L/mkfs-vfat.log 2>&1
}
mk_ext4() {  # DEV ROLE LABEL [UUID HASHSEED]: as load makes it (the source's UUID and hash seed for a target)
    local ids=()
    [[ -n ${4:-} ]] && ids=(-U "$4" -E "hash_seed=$5")
    # shellcheck disable=SC2086
    mke2fs -F -q -t ext4 ${EXTO[$2]} -L "$3" "${ids[@]}" "$1" >>$L/mkfs.log 2>&1
}
echo "mkfs.fat -F $FATT -i ${VOLID//-/} -n $SLABEL -S $FATS -s $FATC -f $FATN -R $FATR -h 0; mke2fs boot ${EXTO[boot]}, save ${EXTO[save]}, data ${EXTO[data]}; mounts seed ${MOPT[seed]}, boot ${MOPT[boot]}, save ${MOPT[save]}, data ${MOPT[data]}" >>$L/env.txt

# --- The scratch disk --------------------------------------------------------
# vdb as the guest's vda, once per set: each of its partitions in its
# order, type and name.  Then one ext4 more for the non-root run.
SETS="src rr uu ru ur"
declare -A PART   # SET:ROLE -> /dev/vdbN
attrs_of() {  # HEX: sfdisk's attrs= words
    local v=$(( $1 )) b w=()
    for b in $(seq 0 63); do
        (( (v >> b) & 1 )) || continue
        case $b in 0) w+=(RequiredPartition) ;; 1) w+=(NoBlockIOProtocol) ;; 2) w+=(LegacyBIOSBootable) ;; *) ((b >= 48)) && w+=("GUID:$b") ;; esac
    done
    (IFS=,; echo "${w[*]}")
}
k=0
{
    echo "label: gpt"
    for s in $SETS; do
        while read -r n _ size ty at _; do
            r=${ROLE[$n]}
            case $r in bios | save) sz=$size ;; seed) sz=$((SEED_MIB * 2048)) ;; boot) sz=$((256 * 2048)) ;; data) sz=$((1536 * 2048)) ;; *) sz=$((16 * 2048)) ;; esac
            a=$(attrs_of "$at")
            echo "size=$sz, type=$ty, name=\"${PNAME[$n]}\"${a:+, attrs=\"$a\"}"
            k=$((k + 1)); echo "$s:$r $DISK$k" >>$L/parts
        done < <(tail -n +2 $L/gpt-guest)
    done
    echo "size=$((512 * 2048)), type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=\"bkst-nonroot\""
    k=$((k + 1)); echo "nr:data $DISK$k" >>$L/parts
} >$L/sfdisk-in.txt
while read -r key dev; do PART[$key]=$dev; done <$L/parts
sfdisk -q --wipe always $DISK <$L/sfdisk-in.txt >$L/sfdisk.log 2>&1; partx -u $DISK >>$L/sfdisk.log 2>&1; udevadm settle
[[ -b ${PART[nr:data]} ]] || { echo "FAIL harness: no partitions on $DISK: $(tail -3 $L/sfdisk.log)"; exit 2; }
python3 $B/gpt.py $DISK >$L/gpt-scratch

# The source's LUKS2: as uc24-fde's header (the KDFs' memory smaller: 32
# KiB for keyslot 0 as the guest's, 64 MiB for keyslot 1 where the guest's
# takes ~773 MiB), keyslot 0 preferred, the token in slot 0.  The keys are
# random, on the work disk only (never printed, never in a backup).
luks_new() {  # DEV
    head -c 32 /dev/urandom >$K/key0 && head -c 16 /dev/urandom >$K/rk && chmod 600 $K/key0 $K/rk || return 1
    cryptsetup luksFormat --batch-mode --type luks2 --cipher aes-xts-plain64 --key-size 512 --sector-size 512 \
        --pbkdf argon2i --pbkdf-memory 32 --pbkdf-force-iterations 4 --pbkdf-parallel 1 \
        --luks2-metadata-size 2048k --luks2-keyslots-size 2560k --offset 14336 --label ubuntu-data-enc \
        --key-file $K/key0 "$1" &&
        cryptsetup luksAddKey --batch-mode --pbkdf argon2i --pbkdf-memory 65536 --pbkdf-force-iterations 4 --pbkdf-parallel 1 \
            --key-file $K/key0 "$1" $K/rk &&
        cryptsetup config "$1" --priority prefer --key-slot 0 || return 1
    python3 - "$K/token.json" <<'PY' || return 1
import base64, json, os, sys
json.dump({"type": "systemd-tpm2", "keyslots": ["0"], "tpm2-blob": base64.b64encode(os.urandom(222)).decode(),
           "tpm2-pcrs": [7], "tpm2-pcr-bank": "sha256", "tpm2-primary-alg": "ecc",
           "tpm2-policy-hash": os.urandom(32).hex(), "tpm2-pin": False}, open(sys.argv[1], "w"))
PY
    if ! cryptsetup token import --token-id 0 --json-file $K/token.json "$1" 2>$L/token-import.err; then
        # (A systemd-tpm2 token plugin that refuses this one: the same, typed as our own.)
        sed -i 's/"systemd-tpm2"/"bko-tpm2-like"/' $K/token.json
        cryptsetup token import --token-id 0 --json-file $K/token.json "$1" 2>>$L/token-import.err || return 1
    fi
    sed -n 's/.*"type": *"\([^"]*\)".*/\1/p' $K/token.json >$L/token-type
}
luks_facts() {  # NAME DEV: the header's facts, a header backup's SHA-256, the token, each keyslot opened by its key
    local n=$1 d=$2
    python3 $B/luksfacts.py "$d" $L/luks-$n.json 2>>$L/luks-$n.err
    rm -f $W/hdr-$n.img
    cryptsetup luksHeaderBackup "$d" --header-backup-file $W/hdr-$n.img 2>>$L/luks-$n.err && sha256sum <$W/hdr-$n.img | cut -c1-64 >$L/hdrsha-$n
    stat -c %s $W/hdr-$n.img >>$L/hdrsha-$n 2>/dev/null
    cryptsetup token export --token-id 0 "$d" >$L/token-$n.json 2>>$L/luks-$n.err
    cryptsetup open --test-passphrase --key-slot 0 --key-file $K/key0 "$d" 2>>$L/luks-$n.err; st luks-slot0-$n $?
    cryptsetup open --test-passphrase --key-slot 1 --key-file $K/rk "$d" 2>>$L/luks-$n.err; st luks-slot1-$n $?
}
declare -A SRCID   # ROLE -> "UUID HASHSEED" of the source's ext4
mkset() {  # SET BASE: the set's tmpfs at BASE and its four roots, made (a target's from the source's), mounted below BASE$P (src: $P)
    local s=$1 b=$2 r=$2$P d=bkst-data-$1
    [[ $s == src ]] && r=$P
    mkdir -p $b && mount -t tmpfs -o mode=755,size=64m bkst-$s $b || return 1
    mkdir -p $r/ubuntu-seed $r/ubuntu-boot $r/ubuntu-save $r/data || return 1
    if [[ $s == src ]]; then
        luks_new ${PART[src:data]} 2>>$L/luks-new.err || { echo "luks_new failed" >>$L/luks-new.err; return 1; }
        cryptsetup luksHeaderBackup ${PART[src:data]} --header-backup-file $W/hdr-orig.img || return 1
    else
        cryptsetup luksHeaderRestore --batch-mode ${PART[$s:data]} --header-backup-file $W/hdr-orig.img 2>>$L/luks-restore.err || return 1
    fi
    cryptsetup open --key-file $K/key0 ${PART[$s:data]} $d 2>>$L/luks-open.err && udevadm settle || return 1
    if [[ $s == src ]]; then
        mk_ext4 /dev/mapper/$d data "$(lab "${GDEV[data]}")" && mk_ext4 ${PART[src:boot]} boot "$(lab "${GDEV[boot]}")" &&
            mk_ext4 ${PART[src:save]} save "$(lab "${GDEV[save]}")" || return 1
        SRCID[data]=$(extid /dev/mapper/$d) SRCID[boot]=$(extid ${PART[src:boot]}) SRCID[save]=$(extid ${PART[src:save]})
    else
        # shellcheck disable=SC2086
        mk_ext4 /dev/mapper/$d data "$(lab /dev/mapper/bkst-data-src)" ${SRCID[data]} && mk_ext4 ${PART[$s:boot]} boot "$(lab ${PART[src:boot]})" ${SRCID[boot]} &&
            mk_ext4 ${PART[$s:save]} save "$(lab ${PART[src:save]})" ${SRCID[save]} || return 1
    fi
    mk_vfat ${PART[$s:seed]} || return 1
    mount -t ext4 -o "${MOPT[data]}" /dev/mapper/$d $r/data && mount -t ext4 -o "${MOPT[boot]}" ${PART[$s:boot]} $r/ubuntu-boot &&
        mount -t ext4 -o "${MOPT[save]}" ${PART[$s:save]} $r/ubuntu-save && mount -t vfat -o "${MOPT[seed]}" ${PART[$s:seed]} $r/ubuntu-seed
}
labels_of() {  # SET: each role's TYPE, LABEL, UUID (LUKS's and the ext4's inside it for data), the seed's boot sector
    local s=$1
    { for r in seed boot save data; do echo "$s $r ${PART[$s:$r]} $(blk ${PART[$s:$r]})"; done
      echo "$s inner /dev/mapper/bkst-data-$s $(blk /dev/mapper/bkst-data-$s)"
      echo "$s fatid $(fatid ${PART[$s:seed]})"; } >>$L/labels-made
    for r in boot save; do extfeat ${PART[$s:$r]} >$L/feat-$s-$r; extgeo ${PART[$s:$r]} >$L/geo-$s-$r; done
    extfeat /dev/mapper/bkst-data-$s >$L/feat-$s-data; extgeo /dev/mapper/bkst-data-$s >$L/geo-$s-data
    fatgeom ${PART[$s:seed]} >$L/fat-$s
}
atimes() {  # OPTION DIR: remount DIR and everything mounted below it OPTION (noatime, relatime)
    local o=$1; [[ $1 == relatime ]] && o=atime,relatime
    findmnt -rn -o TARGET -R "$2" | while read -r m; do mount -o remount,$o "$m" || echo "remount $o $m failed" >>$L/remount.err; done
}
mopts_all() { findmnt -rn -o TARGET,OPTIONS -R "$1"; }
fatx() { python3 $B/fatattr.py "$1/ubuntu-seed" "$2"; }   # TREE OUT
snapl() {  # TREE OUT: every .snap in the four roots: path, SHA-256, unsquashfs -s, its listing's SHA-256
    ( cd "$1" && find . -name '*.snap' -type f | LC_ALL=C sort | while read -r f; do
        printf '%s %s %s %s\n' "$f" "$(sha256sum <"$f" | cut -c1-64)" "$(unsquashfs -s "$f" >/dev/null 2>&1 && echo ok || echo bad)" \
            "$(unsquashfs -lls -d /x "$f" 2>/dev/null | sha256sum | cut -c1-16)"
    done ) >"$2"
}
# The file systems in the tree, by key: the four roots.
INSIDE="seed:/ubuntu-seed boot:/ubuntu-boot save:/ubuntu-save data:/data"
mkset src $M/src || { echo "FAIL harness: no source file systems: $(tail -3 $L/luks-new.err) $(tail -3 $L/mkfs.log) $(tail -3 $L/mkfs-vfat.log)"; exit 2; }
labels_of src
for r in seed boot save data; do mopts $P/${RD[$r]} >$L/mopts-src-$r; done
luks_facts src ${PART[src:data]}
# The directories above (on the tmpfs, as /run is): owners without names,
# odd modes, xattrs, an ACL; run and run/mnt as the guest's.
U1=$M/src/up1 U2=$M/src/up1/up2
chown 1234:5678 $U1; chmod 0710 $U1
{ setfattr -n user.above -v up1 $U1 && python3 - $U1 <<'PY'; } 2>>$L/fixture.err || echo "fixture: skipped: the directories above' xattrs and ACL (tmpfs)" >>$L/fixture.err
import os, struct, sys
N = 0xffffffff
b = struct.pack('<I', 2) + b"".join(struct.pack('<HHI', t, p, i) for t, p, i in ((1, 7, N), (2, 5, 4321), (4, 1, N), (16, 5, N), (32, 0, N)))
os.setxattr(sys.argv[1], "system.posix_acl_access", b)
PY
chmod 1755 $U2; setfattr -n trusted.above -v up2 $U2 2>>$L/fixture.err || echo "fixture: skipped: trusted.above on up2" >>$L/fixture.err
chmod --reference=/run $U2/run; chmod --reference=/run/mnt $P
export FIXLOG=$L
bash $B/fixture.sh $P/ubuntu-seed vfat seed 2>>$L/fixture.err
bash $B/fixture.sh $P/ubuntu-boot ext4 boot 2>>$L/fixture.err
bash $B/fixture.sh $P/ubuntu-save ext4 save 2>>$L/fixture.err
bash $B/fixture.sh $P/data ext4 data 2>>$L/fixture.err
touch -r /run/mnt $P; touch -d '1997-07-07 07:07:07.7' $U2/run
touch -d '1999-09-09 09:09:09.123' $U2; touch -d '1998-08-08 08:08:08.456' $U1
sync; atimes noatime $M/src
python3 $B/fsmeta.py record $M/src $L/src.json
fatx $P $L/src.fat.json; snapl $P $L/snaps-src.txt
for i in $INSIDE; do find $P${i#*:} -xdev | wc -l >$L/count-src-${i%%:*}; done
SRCS="$P/ubuntu-seed $P/ubuntu-boot $P/ubuntu-save $P/data"
# shellcheck disable=SC2086
( cd /; time rt_backup $RESTIC $W/repo-restic $SRCS ) >$L/backup-restic.log 2>&1; st backup-restic $?
# shellcheck disable=SC2086
( cd /; time rt_backup $RUSTIC $W/repo-rustic $SRCS ) >$L/backup-rustic.log 2>&1; st backup-rustic $?
python3 $B/fsmeta.py record $M/src $L/src-after.json
python3 $B/fsmeta.py compare $L/src.json $L/src-after.json --json $L/src-after.cmp.json >$L/src-after.cmp.txt
rt_nodes $RESTIC $W/repo-restic $P >$L/nodes-restic.txt 2>$L/nodes-restic.err
rt_nodes $RESTIC $W/repo-rustic $P >$L/nodes-rustic.txt 2>$L/nodes-rustic.err
rt_node_diff $L/nodes-restic.txt $L/nodes-rustic.txt >$L/nodes.diff 2>&1; st nodes $?
$RESTIC -r $W/repo-rustic check --read-data >$L/check-restic-on-rustic.log 2>&1; st check-restic-on-rustic $?
$RUSTIC --no-progress --no-cache -r $W/repo-restic check --read-data >$L/check-rustic-on-restic.log 2>&1; st check-rustic-on-restic $?
for t in restic rustic; do  # the directories above, as each snapshot holds them
    $RESTIC -r $W/repo-$t ls --json latest 2>/dev/null | grep -E "\"path\":\"($M|$M/src|$M/src/up1|$M/src/up1/up2|$M/src/up1/up2/run|$P)\"" >$L/above-$t.json
done
# Two more backups with the source on relatime (the guest's four roots
# mount relatime): they mustn't move the source's atimes.
atimes relatime $M/src; mopts_all $M/src >$L/mopts-rel-src
for t in restic rustic; do
    [[ $t == restic ]] && b=$RESTIC || b=$RUSTIC
    # shellcheck disable=SC2086
    ( cd /; rt_backup $b $W/repo-rel-$t $SRCS ) >$L/backup-rel-$t.log 2>&1; st backup-rel-$t $?
done
sync; atimes noatime $M/src
python3 $B/fsmeta.py record $M/src $L/src-rel.json
python3 $B/fsmeta.py compare $L/src.json $L/src-rel.json --json $L/src-rel.cmp.json >$L/src-rel.cmp.txt
rm -rf $W/repo-rel-restic $W/repo-rel-rustic
restore() {  # TOOL REPO TARGET
    if [[ $1 == rustic ]]; then $RUSTIC --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $RESTIC -r $2 restore latest --target $3; fi
}
for d in rr:restic:restic uu:rustic:rustic ru:restic:rustic ur:rustic:restic; do
    IFS=: read n b r <<<"$d"
    mkset $n $M/d$n || { echo "FAIL harness: no target file systems for $n: $(tail -3 $L/luks-restore.err) $(tail -3 $L/luks-open.err) $(tail -3 $L/mkfs.log)"; continue; }
    labels_of $n
    for x in seed boot save data; do mopts $M/d$n$P/${RD[$x]} >$L/mopts-$n-$x; done
    luks_facts $n ${PART[$n:data]}
    restore $r $W/repo-$b $M/d$n >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime $M/d$n
    python3 $B/fsmeta.py record $M/d$n$M/src $L/$n.json
    fatx $M/d$n$P $L/$n.fat.json; snapl $M/d$n$P $L/snaps-$n.txt
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
    for i in $INSIDE; do
        x=$M/d$n$P${i#*:}
        find $x -xdev | wc -l >$L/count-$n-${i%%:*}; findmnt -n -o SOURCE --target $x/. >$L/dev-$n-${i%%:*}
    done
done
# Restore over existing immutable and append-only files (and an immutable
# directory) on each ext4 root, changed FAT attributes and a changed
# read-only file on the seed, and a changed .snap on the seed and in data:
# each tool over its own restore.
for t in restic rustic; do
    n=over-$t; [[ $t == restic ]] && D=$M/drr || D=$M/duu
    : >$L/$n.before
    for X in $D$P/ubuntu-boot $D$P/ubuntu-save $D$P/data; do
        chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
            echo x >$X/immutable-dir/f && chmod 600 $X/immutable-nodump && chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump &&
            echo appended >>$X/append-only && setfattr -n user.extra -v 1 $X/append-only 2>/dev/null
        lsattr -d $X/immutable $X/append-only $X/immutable-dir >>$L/$n.before 2>&1
    done
    { echo appended >>$D$P/data/system-data/var/lib/snapd/snaps/bko-squash-xz_1.snap &&
        truncate -s 4096 $D$P/ubuntu-seed/snaps/bko-squash-xz_1.snap &&
        echo "snaps changed: data's bko-squash-xz_1.snap appended to, the seed's truncated to 4096"; } >>$L/$n.before 2>&1
    E=$D$P/ubuntu-seed
    python3 - $E <<'PY' >>$L/$n.before 2>&1
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
    atimes relatime $D; mopts_all $D >$L/mopts-rel-$n
    restore $t $W/repo-$t $D >$L/restore-$n.log 2>&1; st restore-$n $?
    sync; atimes noatime $D
    python3 $B/fsmeta.py record $D$M/src $L/$n.json
    fatx $D$P $L/$n.fat.json; snapl $D$P $L/snaps-$n.txt
    python3 $B/fsmeta.py compare $L/src.json $L/$n.json --json $L/$n.cmp.json >$L/$n.cmp.txt
done
# One non-root run (nonroot-inner.sh: backup as root, restores as uid
# 4321) on an ext4 made like data's, mounted noatime: nonroot-inner.sh
# compares atimes.  Its binaries where uid 4321 can run them (/root is 0700).
{ mk_ext4 ${PART[nr:data]} data bkst-nonroot && mkdir -p $M/nr &&
    mount -t ext4 -o "${MOPT[data]}" ${PART[nr:data]} $M/nr && mopts $M/nr >$L/mopts-nr &&
    mount -o remount,noatime $M/nr; } || echo "FAIL harness: no file system for the non-root run"
install -d -m 755 $W/nrbin && install -m 755 $RESTIC $RUSTIC $W/nrbin/
FSMETA_DIR=$B timeout 400 sh $B/nonroot-inner.sh $M/nr $W/nrbin/restic-bko $W/nrbin/rustic-bko >$L/nonroot.log 2>&1; st nonroot $?
lsblk -o NAME,TYPE,FSTYPE,LABEL,SIZE,PARTLABEL,MOUNTPOINTS $DISK >$L/lsblk.txt 2>&1
df -m $P/ubuntu-seed $P/ubuntu-boot $P/ubuntu-save $P/data $M/d*$P/* $M/nr $W >$L/df.txt 2>&1
# Last: every set's file systems unmounted and its LUKS2 closed, then each
# header read again (after the restores, the restores over existing and
# the closes): the same bytes as the source's.
umount $M/nr 2>>$L/umount.err
for s in $SETS; do
    [[ $s == src ]] && b=$M/src || b=$M/d$s
    umount -R $b 2>>$L/umount.err; udevadm settle
    cryptsetup close bkst-data-$s 2>>$L/umount.err; st close-$s $?
done
for s in $SETS; do luks_facts end-$s ${PART[$s:data]}; done
python3 $B/judge.py $L
