#!/usr/bin/env bash
# HoltTest1x for a guest that isn't Linux (README: "Systems other than
# Linux"): ht1.sh hands such guests here.  Run as root on a test host:
#
#   ht1-bsd.sh GUEST RUN [PHASE...]    PHASEs: source backup rebuild verify
#
# The same round trip as ht1.sh, through bko-holt and rustic-bko built for
# the guest's own system (bin/PLATFORM/bko-holt from build-holt.sh, and
# ~/backotter-work/bko-bin/PLATFORM/rustic-bko), with a rescuer of that
# system: a throwaway copy of another of its guests, whose own disk is
# first, so that no pool, label or volume name of its own is the source's.
# Linux can't read most of these filesystems, so the rescuer does the
# offline backup too (the source's disks attached read-only), and keeps the
# repository and the source's mtree(8) listing on its own disk
# (R/rescuer.qcow2, kept from backup to verify).  verify compares the
# restored files with that listing (fsmeta's part here), boots the restored
# disks alone, and judges its dump against the source's (judge.py).
#
# The guest's disks are its image and vm-run-options' --disk ones (named
# relative to its directory), as ht1.sh takes them: each goes through every
# phase, src-I and dst-I, attached to the rescuer after its own disk in that
# order, the decoy after the targets.  A ZFS pool on more than one disk must
# import with every one of them ONLINE wherever the script mounts it.
#
# The debugging options are ht1.sh's (HT_RAM, HT_HOLT, HT_QUICK, HT_THROUGH,
# HT_KEEP; ht1-debug.sh drives them).  Each rebuild runs its rescuer on a
# throwaway overlay of the one holding the backup (R/rescuer-try.qcow2), so
# no try leaves anything behind for the next (an imported pool, a log).
set -uo pipefail

G=$1 RUN=$2; shift 2
PHASES=("$@"); [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(source backup rebuild verify)
W=/home/m/backotter-work
BIN=/bkoscratch/holttest1d/bin
HERE=$(dirname "$(realpath "$0")")
T=$(dirname "$HERE")   # this copy's testvm/ (vm-run, vm-lib, compare/), as ht1.sh's
VARIANT=${HT_VARIANT:-}
case $VARIANT in ""|bigger|oneshot) ;; *) echo "HT_VARIANT: bigger or oneshot" >&2; exit 2 ;; esac
GV=$G${VARIANT:+-$VARIANT}
BASE=/bkoscratch/holttest1d; [[ ${HT_RAM:-} == 1 ]] && BASE=/mnt/bko-ram/holttest1d
R=$BASE/$RUN/$GV
PORT=${HT_PORT:-2970}
export SSH_PORT=$PORT
mkdir -p "$R/logs" "$R/dumps" /bkoscratch/tmp
export TMPDIR=/bkoscratch/tmp; [[ ${HT_RAM:-} == 1 ]] && TMPDIR=/mnt/bko-ram/tmp
install -d -m 1777 "$TMPDIR"
fails=0
say() { echo "$*" | tee -a "$R/report.txt"; }
ok() { say "PASS $*"; }
bad() { say "FAIL $*"; fails=$((fails + 1)); }

# The guest's system, its rescuer guest, and the rescuer's disk names: its
# own disk is the first (D0), the others follow in attachment order.
# RAWSFX is the whole-disk partition's letter, where reading the disk goes
# through one (NetBSD's d, OpenBSD's c).  BOOTSTATE lists the enabled
# services that aren't running; EXTRA_BOOT_RE what else mtree may see the
# boot stage change.
GPATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin
MTREE_KEYS=type,uid,gid,mode,nlink,size,link,time,flags,sha256digest
SERVICE_STATE='for s in $(service -e); do o=$($s status 2>&1); case $o in *"is not running"*) echo "down ${s##*/}" ;; esac; done | sort'
EXTRA_BOOT_RE='^$' KNOWN_RE='^$'
case $G in
    freebsd*|hardenedbsd*|ghostbsd*)
        PLAT=freebsd RG=freebsd15-ufs DISK=vtbd RAWSFX= BOOTSTATE=$SERVICE_STATE ;;
    netbsd*|alpinebsd*)
        PLAT=netbsd RG=netbsd11 DISK=ld RAWSFX=d BOOTSTATE=$SERVICE_STATE
        MTREE_KEYS=type,uid,gid,mode,nlink,size,link,time,flags,sha256 ;;
    dragonfly*)
        PLAT=dragonfly RG=dragonfly6 DISK=vbd RAWSFX= BOOTSTATE=$SERVICE_STATE
        # DragonFly's mtree reports every file's flags as differing ("none"
        # is not "none"): left out.
        MTREE_KEYS=type,uid,gid,mode,nlink,size,link,time,sha256digest ;;
    openbsd*)
        PLAT=openbsd RG=openbsd8 DISK=sd RAWSFX=c
        BOOTSTATE='rcctl ls failed | sed "s/^/down /" | sort'
        # installboot -r puts the rescue system's /boot (the same release's).
        EXTRA_BOOT_RE='^(extra: |missing: )?(\./)?boot( |:|$)' ;;
    omnios*)
        # The rescuer is a copy of the guest: its own pool is rpool too, so
        # load makes the target's under another name until close, and this
        # script imports the source's and target's as ht1src.
        PLAT=illumos RG=omnios58 DISK= RAWSFX=
        GPATH=/usr/gnu/bin:/usr/sbin:/usr/bin:/sbin:/opt/ooce/bin
        BOOTSTATE='i=0; while svcs -H -o state | grep -q "\*" && [ $i -lt 60 ]; do sleep 2; i=$((i + 1)); done; svcs -H -o state,fmri | awk "\$1 != \"online\" && \$1 != \"disabled\" {print \"down \" \$2}" | sort'
        # bootadm update-archive writes in /etc (system.d, versions) and
        # the root directory; the load log goes in /var/log/bko-holt.  Where
        # the source's archive was out of date (omnios58-mirror's: its own
        # shutdown left it), the rebuild also writes boot/solaris's stat
        # files and the archive cache, and leaves repository.db's mtime (the
        # same content: only its metadata line may differ).
        EXTRA_BOOT_RE='/(etc/zfs/zpool\.cache|platform/i86pc(/amd64)?/boot_archive(\.hash)?|platform/i86pc(/amd64)?(/archive_cache(/.*)?)?|boot(/solaris(/filestat\.ramdisk|/timestamp\.cache)?)?|var/log(/bko-holt(/load\.log)?)?|etc/zfs|etc/system\.d|etc/versions|etc/svc)$|^(missing|extra): d [0-9]+ [0-9]+ [0-9]+ [0-9.]+ \.$|^(missing|extra): f [0-9]+ [0-9]+ [0-9]+ [0-9]+ [0-9.]+  \./etc/svc/repository\.db$' ;;
    haiku*)
        PLAT=haiku RG=haiku6 DISK= RAWSFX= GUSER=user
        GPATH=/bin:/boot/system/bin:/boot/system/non-packaged/bin
        BOOTSTATE='echo haiku'
        # The boot stage's log goes in system/var/log there (/var is it).
        EXTRA_BOOT_RE='\./system/var/log(/bko-holt(/.*)?)?$' ;;
    *) echo "ht1-bsd: no rescuer for $G yet" >&2; exit 2 ;;
esac
GUSER=${GUSER:-root}
# Where the rescuer keeps the repository and the tools: root's home.
RH=/root; [[ $PLAT == haiku ]] && RH=/boot/home
# The rescuer's disks' names, its own first (RN, set by rescuer_ready once
# it's up: Haiku numbers them by the controller's ports); rdev N is the Nth's
# device, rraw N the node that reads it whole.
RN=(); for i in 0 1 2 3 4 5 6 7; do RN+=("$DISK$i"); done
rescuer_ready() {
    if [[ $PLAT == illumos ]]; then
        # (A disk attached at boot can be late into /dev: devfsadm, and wait
        # until diskinfo lists every one.)
        local want=$1
        for _ in $(seq 30); do
            mapfile -t RN < <(sshk "$rkey" 'devfsadm -c disk >/dev/null 2>&1; diskinfo -Hp | cut -f2')
            (( ${#RN[@]} >= want )) && break
            sleep 2
        done
        # By their serials (s_resc, s_decoy): the rescuer's own, the
        # guest's disks in order, the decoy.
        local by; by=$(sshk "$rkey" 'iostat -En' | awk '/^c[0-9]/ {d = $1} /Serial No:/ {s = $0; sub(/.*Serial No: */, "", s); sub(/ .*/, "", s); print s, d}')
        if grep -q '^bkoresc ' <<<"$by"; then
            local order=(bkoresc) i
            for ((i = 0; i < n; i++)); do order+=("bkodisk-$i"); done
            order+=(bkodecoy)
            RN=()
            for i in "${order[@]}"; do
                local d; d=$(awk -v s="$i" '$1 == s {print $2}' <<<"$by")
                [[ -n $d ]] && RN+=("$d")
            done
        fi
        return 0
    fi
    [[ $PLAT == haiku ]] || return 0
    # Haiku's mount_server mounts every BFS volume it finds: stopped, so the
    # source and target are mounted where load and this script say only.
    sshk "$rkey" 'p=$(ps | awk "/mount_server/ {print \$2}" | head -n 1); [ -z "$p" ] || kill $p' >/dev/null 2>&1
    mapfile -t RN < <(sshk "$rkey" 'for r in $(find /dev/disk -name raw | grep -v virtual | sort); do d=${r%/raw}; echo ${d#/dev/disk/} | tr / _; done')
}
rdev() { case $PLAT in haiku) echo "/dev/disk/$(tr _ / <<<"${RN[$1]}")" ;; illumos) echo "/dev/dsk/${RN[$1]}" ;; *) echo "/dev/${RN[$1]}" ;; esac; }
rraw() { case $PLAT in haiku) echo "$(rdev "$1")/raw" ;; illumos) echo "/dev/rdsk/${RN[$1]}p0" ;; *) echo "/dev/${RAWSFX:+r}${RN[$1]}$RAWSFX" ;; esac; }
# mount(8)'s mount points, whichever system's format.
LISTMOUNTS='mount | sed -e "s/ type [^ ]* (.*//" -e "s/ (.*//" -e "s/^.* on //"'
# A disk's sectors (the name, ld1), and a checksum of some of them.
SECTORS='sectors() { case $(uname) in FreeBSD) diskinfo /dev/$1 | cut -f4 ;; DragonFly) echo $(( $(printf %d 0x$(diskinfo /dev/$1 | sed -n "s/.*size=0x\([0-9a-fA-F]*\).*/\1/p")) / 512 )) ;; *) disklabel $1 | awk "/^total sectors:/ {print \$3}" ;; esac; }'
HOLT=${HT_HOLT:-$BIN/$PLAT/bko-holt}
RUSTIC=${HT_RUSTIC:-$W/bko-bin/$PLAT/rustic-bko}
gdir=$W/vms/$G gkey=$gdir/id_ed25519
rdir=$W/vms/$RG rkey=$rdir/id_ed25519
# The guest's other disks (vm-run-options' --disk, named relative to its
# directory) and its other options (firmware) for the source and restored
# boots; its image is $G.qcow2, else the one *.qcow2 that isn't a --disk.
fw=() gextra=()
if [[ -f $gdir/vm-run-options ]]; then
    read -ra o < "$gdir/vm-run-options"
    for ((i = 0; i < ${#o[@]}; i++)); do
        case ${o[i]} in
        --disk) d=${o[++i]}; [[ $d == /* ]] || d=$gdir/$d; gextra+=("$d") ;;
        *) fw+=("${o[i]}") ;;
        esac
    done
fi
gimg=$gdir/$G.qcow2
if [[ ! -f $gimg ]]; then
    gimg=
    for f in "$gdir"/*.qcow2; do
        isx=0; for x in "${gextra[@]}"; do [[ $f -ef $x ]] && isx=1; done
        ((isx)) || { gimg=$f; break; }
    done
fi
imgs=("$gimg" "${gextra[@]}") n=$((1 + ${#gextra[@]}))
rimg=$rdir/$RG.qcow2; [[ -f $rimg ]] || rimg=$(ls "$rdir"/*.qcow2 | head -n 1)
rfw=(); [[ -f $rdir/vm-run-options ]] && read -ra rfw < "$rdir/vm-run-options"
# OpenBSD finds its partitions by the disklabel's UID (DUID), and two disks
# with one DUID make it refuse both: the rescuer is a copy of openbsd8
# with a DUID of its own (fstab rewritten to match), made once.
if [[ $PLAT == openbsd ]]; then
    prep=/bkoscratch/holttest1d/rescuer/$RG-newduid.qcow2
    if [[ ! -f $prep ]]; then
        mkdir -p "${prep%/*}"
        qemu-img convert -O qcow2 "$rimg" "$prep.part"
        setsid -f "$T/vm-run" --serial-log "$R/logs/rescuer-prep-serial.log" "$prep.part" </dev/null >/dev/null 2>&1
        for _ in $(seq 120); do timeout 8 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@127.0.0.1 true 2>/dev/null && break; sleep 5; done
        timeout 120 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 /bin/sh -s <<'PREP' > "$R/logs/rescuer-prep.log" 2>&1
set -e
old=$(disklabel sd0 | awk '/^duid:/ {print $2}')
new=$(openssl rand -hex 8)
printf 'i
%s
w
q
' "$new" | disklabel -E sd0 >/dev/null
sed -i "s/$old/$new/g" /etc/fstab
echo "DUID $old -> $new"
sync
PREP
        timeout 20 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 "shutdown -p now" >/dev/null 2>&1
        for _ in $(seq 36); do pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null || break; sleep 5; done
        pkill -f "^qemu-system.*:$PORT-:22" || true
        grep -q "^DUID" "$R/logs/rescuer-prep.log" && mv "$prep.part" "$prep"
    fi
    [[ -f $prep ]] || { echo "ht1-bsd: no OpenBSD rescuer with its own DUID (logs/rescuer-prep.log)" >&2; exit 1; }
    rimg=$prep
fi
# illumos won't import a pool with the GUID of one it has imported, and the
# source's rpool is a copy of omnios58's own: the rescuer is a copy of
# omnios58 whose pool has a new GUID (zpool reguid), made once.
if [[ $PLAT == illumos ]]; then
    prep=/bkoscratch/holttest1d/rescuer/$RG-reguid.qcow2
    if [[ ! -f $prep ]]; then
        mkdir -p "${prep%/*}"
        qemu-img convert -O qcow2 "$rimg" "$prep.part"
        setsid -f "$T/vm-run" "${rfw[@]}" --serial-log "$R/logs/rescuer-prep-serial.log" "$prep.part" </dev/null >/dev/null 2>&1
        for _ in $(seq 120); do timeout 8 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@127.0.0.1 true 2>/dev/null && break; sleep 5; done
        timeout 300 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 '/usr/sbin/zpool reguid rpool && /usr/sbin/zpool get -H -o value guid rpool && echo PREP-DONE' > "$R/logs/rescuer-prep.log" 2>&1
        timeout 30 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 "/usr/sbin/shutdown -y -i5 -g0" >/dev/null 2>&1
        for _ in $(seq 36); do pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null || break; sleep 5; done
        pkill -f "^qemu-system.*:$PORT-:22" || true
        grep -q "^PREP-DONE" "$R/logs/rescuer-prep.log" && mv "$prep.part" "$prep"
    fi
    [[ -f $prep ]] || { echo "ht1-bsd: no illumos rescuer with a pool GUID of its own (logs/rescuer-prep.log)" >&2; exit 1; }
    rimg=$prep
fi
# DragonFly won't mount a HAMMER2 volume with the ID of one it has mounted
# ("Device busy"), and the source's is a copy of dragonfly6's own: the
# rescuer is dragonfly6 copied onto a new disk (the same MBR, disklabel64
# and boot blocks; /boot by newfs, / by newfs_hammer2, so a volume ID of its
# own; cpdup the files), made once.
if [[ $PLAT == dragonfly ]]; then
    prep=/bkoscratch/holttest1d/rescuer/$RG-newfsid.qcow2
    if [[ ! -f $prep ]]; then
        mkdir -p "${prep%/*}"
        rm -f "$prep.part" "$R/prep-boot.qcow2"
        qemu-img create -q -f qcow2 "$prep.part" "$(qemu-img info --output=json "$rimg" | python3 -c 'import json,sys; print(json.load(sys.stdin)["virtual-size"])')"
        qemu-img create -q -f qcow2 -b "$rimg" -F qcow2 "$R/prep-boot.qcow2"
        setsid -f "$T/vm-run" --serial-log "$R/logs/rescuer-prep-serial.log" --disk "$prep.part" "$R/prep-boot.qcow2" </dev/null >/dev/null 2>&1
        for _ in $(seq 120); do timeout 8 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@127.0.0.1 true 2>/dev/null && break; sleep 5; done
        timeout 3600 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 /bin/sh -s <<'PREP' > "$R/logs/rescuer-prep.log" 2>&1
set -ex
PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
dd if=/dev/vbd0 of=/dev/vbd1 bs=512 count=63
disklabel64 -r vbd0s1 > /tmp/label
disklabel64 -R -B vbd1s1 /tmp/label
newfs /dev/vbd1s1a
newfs_hammer2 -L ROOT /dev/vbd1s1d
mkdir -p /mnt/new
mount -t hammer2 /dev/vbd1s1d@ROOT /mnt/new
mkdir -p /mnt/new/boot
mount /dev/vbd1s1a /mnt/new/boot
cpdup -i0 -x / /mnt/new
cpdup -i0 -x /boot /mnt/new/boot
umount /mnt/new/boot
umount /mnt/new
echo PREP-DONE
PREP
        timeout 20 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 "/sbin/poweroff" >/dev/null 2>&1
        for _ in $(seq 36); do pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null || break; sleep 5; done
        pkill -f "^qemu-system.*:$PORT-:22" || true
        rm -f "$R/prep-boot.qcow2"
        grep -q "^PREP-DONE" "$R/logs/rescuer-prep.log" && mv "$prep.part" "$prep"
    fi
    [[ -f $prep ]] || { echo "ht1-bsd: no DragonFly rescuer with a HAMMER2 of its own (logs/rescuer-prep.log)" >&2; exit 1; }
    rimg=$prep
fi
rfw=(); [[ -f $rdir/vm-run-options ]] && read -ra rfw < "$rdir/vm-run-options"
# The throwaway copies of the guest's disks (src-I) and the targets (dst-I),
# and --disk options attaching all of them (all_*) or all but the first.
src=() dst=() src_more=() dst_more=() all_src=() all_dst=()
for ((i = 0; i < n; i++)); do
    src+=("$R/src-$i.qcow2"); dst+=("$R/dst-$i.qcow2")
    all_src+=(--disk "$R/src-$i.qcow2"); all_dst+=(--disk "$R/dst-$i.qcow2")
    ((i > 0)) && src_more+=(--disk "$R/src-$i.qcow2") && dst_more+=(--disk "$R/dst-$i.qcow2")
done
SRC=${src[0]} DST=${dst[0]} RESC=$R/rescuer.qcow2
# illumos: serial numbers for the disks, as real disks have (devids), a
# source disk and its target sharing one (bkodisk-I); and in the rescuer,
# its own disk in a PCI slot out of the way (16, booted first), so that the
# disks it works on are in the slots the machine itself has them in (4 on).
# illumos finds a mirrored rpool's other half by its physical path first: a
# rescuer whose own disk took slot 4 left the restored mirror half found
# (omnios58-mirror's DEGRADED rpool), where a real rescue stick, on USB,
# takes no disk's slot.  So the rebuild's rescuer has its own disk in slot
# 16 and the targets in the slots they have in the machine.  Only there: the
# rescuer's own rpool label names slot 4 too, and with a real rpool in slot 4
# (the source's, the restored one) it boots that one instead.  The backup's
# and the verify's rescuers keep slot 4, and the verify's runs on --snapshot,
# for its import (read-write, by an alias) would put their slots in the
# restored labels.  rescuer_ready names the disks by their serials.
s_guest=() s_resc=() s_decoy=() v_snap=()
if [[ $PLAT == illumos ]]; then
    ids=() sl=(); for ((i = 0; i < n; i++)); do ids+=("bkodisk-$i"); sl+=($((4 + i))); done
    s_guest=(--disk-serials "$(IFS=,; echo "${ids[*]}")")
    s_resc=(--disk-serials "$(IFS=,; echo "bkoresc,${ids[*]}")")
    v_snap=(--snapshot)
    s_decoy=(--disk-serials "$(IFS=,; echo "bkoresc,${ids[*]},bkodecoy")" --disk-slots "$(IFS=,; echo "16,${sl[*]},$((4 + n))")")
fi
size_of() { qemu-img info --output=json "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["virtual-size"])'; }

# The command goes on sh's stdin: no quoting to survive (csh is DragonFly's
# root shell; sh is everywhere).
sshk() { local key=$1; shift; printf 'PATH=%s; export PATH\n%s\n' "$GPATH" "$*" | timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o ServerAliveInterval=15 "$GUSER@127.0.0.1" "/bin/sh -s"; }
# A script on stdin, for anything longer than a line.
shk() { local key=$1; { printf 'PATH=%s; export PATH\n' "$GPATH"; cat; } | timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o ServerAliveInterval=15 "$GUSER@127.0.0.1" "/bin/sh -s"; }
put() { local key=$1 file=$2 dest=$3; timeout 600 ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$GUSER@127.0.0.1" "cat > '$dest' && chmod 755 '$dest'" < "$file"; }
vm_up() { local image=$1 log=$2; shift 2
    setsid -f "$T/vm-run" --serial-log "$log" "$@" "$image" </dev/null >"$log.vm-run" 2>&1
}
vm_wait() { local key=$1
    for _ in $(seq 120); do TMO=8 sshk "$key" true 2>/dev/null && return 0; sleep 5; done
    return 1
}
vm_down() { local key=$1
    TMO=20 sshk "$key" "/sbin/poweroff || /sbin/shutdown -p now || /usr/sbin/shutdown -y -i5 -g0 || shutdown -q" >/dev/null 2>&1
    for _ in $(seq 36); do pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null || return 0; sleep 5; done
    pkill -f "^qemu-system.*:$PORT-:22"; sleep 5
    pkill -9 -f "^qemu-system.*:$PORT-:22"; sleep 1
}

HEALTH='f=0; no() { echo "FAIL $*"; f=1; }
m=0; for t in $(awk "\$1 !~ /^#/ && \$2 ~ /^\// && \$3 != \"swap\" && \$4 !~ /noauto/ {print \$2}" /etc/fstab); do mount | sed -e "s/ type [^ ]* (.*//" -e "s/ (.*//" -e "s/^.* on //" | grep -qx "$t" && m=$((m + 1)) || no "fstab mount $t not mounted"; done; echo "$m fstab mounts mounted"
if grep -qE "^[^#].*[[:space:]]swap[[:space:]]" /etc/fstab; then [ "$(swapctl -l | grep -c /dev/)" -gt 0 ] && echo "swap on" || no "no swap"; fi
if command -v zpool >/dev/null && [ -n "$(zpool list -H -o name 2>/dev/null)" ]; then zpool status -x | grep -q "all pools are healthy" && echo "zpool healthy" || no "zpool status -x: $(zpool status -x | head -3 | tr "\n" " ")"; fi
ls /var/log/bko-holt/load.log >/dev/null 2>&1 && echo "load log kept" || no "/var/log/bko-holt/load.log missing"
exit $f'

# illumos has /etc/vfstab, not fstab: mount point $3, type $4, mount at boot
# $6; `mount -p` lists what's mounted in that format.
if [[ $PLAT == illumos ]]; then
    HEALTH='f=0; no() { echo "FAIL $*"; f=1; }
m=0; for t in $(awk "\$1 !~ /^#/ && \$3 ~ /^\// && \$4 != \"swap\" && \$6 == \"yes\" {print \$3}" /etc/vfstab); do mount -p | awk "{print \$3}" | grep -qx "$t" && m=$((m + 1)) || no "vfstab mount $t not mounted"; done; echo "$m vfstab mounts mounted"
if awk "\$1 !~ /^#/ && \$4 == \"swap\"" /etc/vfstab | grep -q .; then [ "$(swap -l 2>/dev/null | grep -c /dev/)" -gt 0 ] && echo "swap on" || no "no swap"; fi
if command -v zpool >/dev/null && [ -n "$(zpool list -H -o name 2>/dev/null)" ]; then zpool status -x | grep -q "all pools are healthy" && echo "zpool healthy" || no "zpool status -x: $(zpool status -x | head -3 | tr "\n" " ")"; fi
ls /var/log/bko-holt/load.log >/dev/null 2>&1 && echo "load log kept" || no "/var/log/bko-holt/load.log missing"
exit $f'
fi

if [[ $PLAT == haiku ]]; then
    HEALTH='f=0; no() { echo "FAIL $*"; f=1; }
df | grep -q "/dev/disk/" && echo "boot volume mounted" || no "no boot volume"
ls /boot/system/var/log/bko-holt/load.log >/dev/null 2>&1 && echo "load log kept" || no "load log missing"
exit $f'
fi

# Mounts the layout's filesystems read-only at MP, its first disk being
# the rescuer's Nth (and so on): a shell script for the rescuer.
mount_script() { # LAYOUT FIRST MP
    python3 - "$1" "$2" "$3" "$PLAT" "${RN[@]}" <<'PY'
import json, re, sys
d = json.load(open(sys.argv[1])); first = int(sys.argv[2]); mp = sys.argv[3]; plat = sys.argv[4]; rn = sys.argv[5:]
disks = [x["name"] for x in d["storage"]["disks"]]
def node(part):
    for i, dn in enumerate(disks):
        if part.startswith(dn):
            n = rn[first + i] + part[len(dn):]
            # Haiku: scsi_3_0_0_0 is /dev/disk/scsi/3/0/0/0.
            if plat == "haiku":
                return "/dev/disk/" + n.replace("_", "/")
            return ("/dev/dsk/" if plat == "illumos" else "/dev/") + n
    # A NetBSD wedge (dkN): by its name, as the source's fstab names it.
    for x in d["storage"]["disks"]:
        for p in x.get("partitions") or []:
            if p.get("name") == part and p.get("wedge_name"):
                return "NAME=" + p["wedge_name"]
    return None
out = ["set -e", f"mkdir -p {mp}"]
zfs = (d["storage"].get("zfs") or {}).get("tools") or {}
for p in zfs.get("imported_pools") or []:
    # illumos' rescuer has its own rpool: the source's (or target's) goes by
    # ht1src here (-t: the disks keep the name), read-write, so that its own
    # dataset, whose default mount point would be /ht1src, mounts at the
    # pool's own name (the boot menu is there).  The copies are throwaway.
    alias = "ht1src" if plat == "illumos" else ""
    if alias:
        # By the id of the importable pool of that name (the target's GUID
        # isn't the source's on illumos; the rescuer's own isn't importable).
        # By the GUID on the attached disk's own label (the target's isn't
        # the source's on illumos, and zpool import lists only one rpool).
        member = next((pt["name"] for x in d["storage"]["disks"] for pt in x.get("partitions") or [] if (pt.get("content") or {}).get("type") == "zfs_member"), None)
        out.append(f"id=$(zdb -l {node(member)} | awk '$1 == \"pool_guid:\" {{ print $2; exit }}')")
        out.append(f"zpool import -f -N -R {mp} -t \"$id\" {alias}")
        # (Undone by umount_script: the restored disk is dumped again after.)
        out.append(f"[ \"$(zfs get -H -o source mountpoint {alias})\" != default ] || {{ zfs set mountpoint=/{p['name']} {alias} && touch /tmp/ht1-mountpoint-set; }}")
    else:
        out.append(f"zpool import -f -N -o readonly=on -R {mp} {p['properties']['guid']}")
    # A pool on more than one disk: imported from every one of them (ONLINE,
    # as many ONLINE leaves as the layout's), or the mount fails.
    def leaves(t):
        return sum(1 if v.get("vdev_type", "disk") in ("disk", "file") else leaves(v.get("vdevs") or {}) for v in (t or {}).values())
    root = next(iter(((p.get("vdevs") or {}).get("vdevs") or {}).values()), {})
    nl = leaves(root.get("vdevs"))
    if nl > 1:
        pn = alias or p["name"]
        out.append(f"h=$(zpool list -H -o health {pn}); l=$(zpool status {pn} | awk '/^config:/ {{ c = 1; next }} /^errors:/ {{ c = 0 }} c && $1 ~ /^(logs|cache|spares|special|dedup)$/ {{ c = 0 }} c && $2 == \"ONLINE\" && $1 != \"{pn}\" && $1 !~ /^(mirror|raidz[0-9]*|draid[0-9:a-z]*|replacing|spare)-/' | wc -l | tr -d ' ')")
        out.append(f"[ \"$h\" = ONLINE ] && [ \"$l\" = {nl} ] || {{ echo \"pool {pn} is $h, $l of {nl} disks ONLINE\"; zpool status {pn}; exit 1; }}")
        out.append(f"echo \"HT1-POOL {p['name']} ONLINE, $l of {nl} disks\"")
    # The root first (at MP itself, which must be empty), then the rest.
    bootfs = p["properties"].get("bootfs")
    if bootfs and bootfs != "-":
        # (mountpoint=legacy, as OmniOS's cloud image's boot environment:
        # the kernel mounts it at / from bootfs, zfs mount won't.)
        legacy = any(ds.get("name") == bootfs and ds.get("mountpoint") == "legacy" for ds in zfs.get("datasets") or [])
        if alias:
            bootfs = alias + bootfs[len(p["name"]):]
        out.append(f"mount {'-F' if plat == 'illumos' else '-t'} zfs {bootfs} {mp}" if legacy else f"zfs mount {bootfs}")
    if alias:
        out.append(f"zfs mount -O {alias} || true")
if zfs.get("imported_pools"):
    # (Read-only, a dataset whose mount point isn't there can't mount: the
    # rest do.)
    out.append("zfs mount -a || true")
fs = []
for e in d["storage"].get("fstab") or []:
    if e.get("resolves_to") and e["file"].startswith("/") and e["type"] not in ("swap",):
        fs.append(e)
fs.sort(key=lambda e: (0 if e["file"] == "/" else e["file"].count("/"), e["file"]))
for e in fs:
    t = {"ufs": "ufs", "msdosfs": "msdosfs"}.get(e["type"], e["type"])
    target = mp + ("" if e["file"] == "/" else e["file"])
    n = node(e["resolves_to"])
    if e["type"] == "hammer2" and "@" in e["spec"]:
        n += "@" + e["spec"].split("@", 1)[1]   # the same PFS (DragonFly)
    if plat == "haiku":
        # Mounted at boot before mount_server was stopped: unmounted first.
        out.append(f"for m in $(df | sed 's/\\x1b\\[[0-9;]*m//g' | awk -v d={n} '{{ if ($NF == d) print last; last = $1 }}'); do unmount \"$m\"; done")
        out.append(f"mkdir -p {target}; mount -ro -t bfs {n} {target}")
        continue
    out.append(f"mkdir -p {target} 2>/dev/null || true; mount -r -t {t} {n} {target}")
print("\n".join(out))
PY
}
umount_script() { # LAYOUT MP
    if [[ $PLAT == haiku ]]; then echo "unmount $2"; return; fi
    python3 - "$1" "$2" "$PLAT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); mp = sys.argv[2]; plat = sys.argv[3]
if plat == "illumos":
    print(f"awk '{{ print $2 }}' /etc/mnttab | awk '$0 == \"{mp}\" || index($0, \"{mp}/\") == 1' | sort -r | while read -r m; do umount -f \"$m\" 2>/dev/null; done")
else:
    print(f"mount | sed -e 's/ type [^ ]* (.*//' -e 's/ (.*//' -e 's/^.* on //' | awk '$0 == \"{mp}\" || index($0, \"{mp}/\") == 1' | sort -r | while read -r m; do umount -f \"$m\"; done")
for p in ((d["storage"].get("zfs") or {}).get("tools") or {}).get("imported_pools") or []:
    if plat == "illumos":
        print("[ ! -f /tmp/ht1-mountpoint-set ] || { zfs inherit mountpoint ht1src; rm -f /tmp/ht1-mountpoint-set; }")
    print(f"zpool export -f {'ht1src' if plat == 'illumos' else p['name']} 2>/dev/null || true")
PY
}
# The tree at DIR listed, for comparing a restore with its source: mtree's
# spec where there's mtree (sockets left out: no archiver keeps them;
# mtree -c continues long entries with a backslash, joined first), else
# (Haiku) GNU find's listing and every file's SHA-256.
list_tree() { # DIR
    if [[ $PLAT == haiku || $PLAT == illumos ]]; then
        echo "cd $1 && { find . ! -type d ! -type s -printf '%y %m %U %G %s %T@ %l %p\n'; find . -type d -printf '%y %m %U %G %T@ %p\n'; find . -type f -exec sha256sum {} +; } | LC_ALL=C sort"
    else
        echo "cd $1 && mtree -c -k $MTREE_KEYS -p $1 | awk '{ l = l \$0 } /\\\\\$/ { sub(/\\\\\$/, \"\", l); next } { if (l !~ / type=socket( |\$)/) print l; l = \"\" }'"
    fi
}

phase_source() {
    say "== source: $G"
    for ((i = 0; i < n; i++)); do
        rm -f "${src[i]}"; qemu-img create -q -f qcow2 -b "${imgs[i]}" -F qcow2 "${src[i]}"
    done
    vm_up "$SRC" "$R/logs/source-serial.log" "${fw[@]}" "${s_guest[@]}" "${src_more[@]}"
    vm_wait "$gkey" || { bad "source boots (no SSH)"; vm_down "$gkey"; return; }
    ok "source boots"
    sshk "$gkey" "$BOOTSTATE" > "$R/logs/source-bootstate.txt" 2>&1
    sshk "$gkey" "$HEALTH" 2>&1 | grep -v 'bko-holt/load.log' > "$R/logs/source-health.txt"
    put "$gkey" "$HOLT" /tmp/bko-holt
    if sshk "$gkey" /tmp/bko-holt dump > "$R/dumps/src.json" 2> "$R/logs/source-dump.err"; then
        ok "dump on the live source"
    else
        bad "dump on the live source: $(tail -1 "$R/logs/source-dump.err")"
    fi
    sshk "$gkey" "rm -f /tmp/bko-holt"
    vm_down "$gkey"
}

phase_backup() {
    say "== backup"
    rm -f "$RESC"; qemu-img create -q -f qcow2 -b "$rimg" -F qcow2 "$RESC"
    qemu-img resize -q "$RESC" +8G 2>/dev/null || true
    vm_up "$RESC" "$R/logs/backup-serial.log" "${rfw[@]}" --memory 3072 "${s_resc[@]}" "${all_src[@]}"
    vm_wait "$rkey" || { bad "rescuer boots"; vm_down "$rkey"; return; }
    rescuer_ready $((1 + n))
    put "$rkey" "$RUSTIC" $RH/rustic-bko
    head -c 24 /dev/urandom | base64 > "$R/repo-password"
    put "$rkey" "$R/repo-password" $RH/repo-password
    mount_script "$R/dumps/src.json" 1 /mnt/src > "$R/logs/backup-mount.sh" 2>> "$R/logs/backup-mount.log" ||
        { bad "the mount script (logs/backup-mount.log)"; vm_down "$rkey"; return; }
    if shk "$rkey" < "$R/logs/backup-mount.sh" > "$R/logs/backup-mount.log" 2>&1; then
        ok "the source mounted read-only in the rescuer"
        grep '^HT1-POOL ' "$R/logs/backup-mount.log" | while read -r _ pn rest; do ok "the source's pool $pn imported from all its disks ($rest)"; done
    else
        bad "mount the source read-only (logs/backup-mount.log)"; vm_down "$rkey"; return
    fi
    if TMO=7200 sshk "$rkey" "R=\"$RH/rustic-bko -r $RH/repo --password-file $RH/repo-password --no-progress --no-cache\"; rm -rf $RH/repo; \$R init && \$R backup --set-atime yes /mnt/src" > "$R/logs/backup.log" 2>&1; then
        ok "rustic-bko backup ($(sshk "$rkey" "du -sh $RH/repo" | cut -f1))"
    else
        bad "rustic-bko backup (logs/backup.log)"
    fi
    # Sockets aren't backed up (neither archiver keeps them).
    TMO=3600 sshk "$rkey" "$(list_tree /mnt/src)" > "$R/src.listing" 2> "$R/logs/fsmeta-src.log" || bad "the listing of the source's files"
    umount_script "$R/dumps/src.json" /mnt/src | shk "$rkey" >> "$R/logs/backup-mount.log" 2>&1
    vm_down "$rkey"
}

load_step() { local want=$1 name=$2; shift 2
    sshk "$rkey" "$RH/bko-holt load $RH/layout.json $*" > "$R/logs/rebuild-$name.out" 2> "$R/logs/rebuild-$name.err"
    local got=$?
    if [[ $got == "$want" ]]; then ok "load $name (exit $got)"; return 0; fi
    bad "load $name: exit $got, want $want: $(grep -v '^$' "$R/logs/rebuild-$name.err" | tail -3 | tr '\n' ' ')"
    return 1
}

phase_rebuild() {
    say "== rebuild"
    [[ -f $RESC ]] || { bad "no rescuer with the backup (run the backup phase)"; return; }
    # A rescuer HT_KEEP left up from the last try.
    pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null && vm_down "$rkey"
    local try=$R/rescuer-try.qcow2
    rm -f "$try"; qemu-img create -q -f qcow2 -b "$RESC" -F qcow2 "$try"
    local grow=0; [[ $VARIANT == bigger ]] && grow=$((1 << 30))
    rm -f "${dst[@]}" "$R/decoy.qcow2"
    for ((i = 0; i < n; i++)); do
        qemu-img create -q -f qcow2 "${dst[i]}" "$(($(size_of "${imgs[i]}") + grow))"
    done
    qemu-img create -q -f qcow2 "$R/decoy.qcow2" "$(size_of "$gimg")"
    vm_up "$try" "$R/logs/rescuer-serial.log" "${rfw[@]}" --memory 3072 "${s_decoy[@]}" "${all_dst[@]}" --disk "$R/decoy.qcow2"
    vm_wait "$rkey" || { bad "rescuer boots"; vm_down "$rkey"; return; }
    rescuer_ready $((n + 2))
    put "$rkey" "$HOLT" $RH/bko-holt
    put "$rkey" "$R/dumps/src.json" $RH/layout.json
    # The targets are the rescuer's disks 1..n, the decoy the one after.
    local D=$((n + 1))
    local decoy; decoy=$(rdev $D)
    local mkdecoy="newfs -L ht1decoy $decoy" isdecoy="dumpfs $decoy 2>/dev/null | awk '/^volname/ {print \$2}' | grep -qx ht1decoy"
    case $PLAT in
        freebsd) ;;
        illumos) mkdecoy="zpool create -f ht1decoy ${RN[D]} && zpool export ht1decoy"
                 isdecoy="zpool import 2>/dev/null | grep -q ht1decoy" ;;
        haiku) mkdecoy="mkfs -t bfs -q $(rraw $D) ht1decoy"
               isdecoy="dd if=$(rraw $D) bs=512 skip=1 count=1 2>/dev/null | strings | grep -q ht1decoy" ;;
        *) mkdecoy="newfs_msdos -L HT1DECOY $(rraw $D)"
           isdecoy="dd if=$(rraw $D) bs=512 count=1 2>/dev/null | strings | grep -q HT1DECOY" ;;
    esac
    sshk "$rkey" "$mkdecoy" > "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup (the decoy's filesystem)"; vm_down "$rkey"; return; }
    ok "rescuer up"
    # The source's disks, by the layout's names, in the order they were
    # attached (the dump lists them so).
    local srcn; mapfile -t srcn < <(python3 -c 'import json,sys; [print(x["name"]) for x in json.load(open(sys.argv[1]))["storage"]["disks"]]' "$R/dumps/src.json")
    (( ${#srcn[@]} >= n )) || { bad "the layout lists ${#srcn[@]} disks, the guest has $n"; vm_down "$rkey"; return; }
    # Each source disk but the first mapped to its own target: with them, a
    # refusal can only be for the first's mapping.
    local others=()
    for ((i = 1; i < n; i++)); do others+=(--map "${srcn[i]}=${RN[i + 1]}"); done
    # (Their sizes are known here: this script made them.)
    local edges="for x in" i
    for ((i = 0; i < n; i++)); do edges+=" $(rraw $((i + 1))):$((($(size_of "${imgs[i]}") + grow) / 512))"; done
    edges+=" $(rraw $D):$(($(size_of "$gimg") / 512)); do r=\${x%:*} s=\${x##*:}; echo \$r \$(dd if=\$r bs=512 count=2048 2>/dev/null | cksum) \$(dd if=\$r bs=512 skip=\$((s - 2048)) count=2048 2>/dev/null | cksum); done"
    local quick=${HT_QUICK:-}
    [[ $quick == 1 ]] || {
    sshk "$rkey" "$edges" > "$R/logs/edges-before.txt" 2>&1
    load_step 4 refuse-nomap
    load_step 4 refuse-root "--map ${srcn[0]}=${RN[0]} --erase ${RN[0]}" "${others[@]}"
    load_step 4 refuse-decoy "--map ${srcn[0]}=${RN[D]}" "${others[@]}" \
        && { cat "$R/logs/rebuild-refuse-decoy".{out,err} | grep -q "isn't blank" || bad "load refuse-decoy: refused, but not for the decoy's filesystem"; }
    }
    local map=() erase=() written=()
    for ((i = 0; i < n; i++)); do
        map+=(--map "${srcn[i]}=${RN[i + 1]}")
        erase+=(--erase "$([[ $PLAT == haiku || $PLAT == illumos ]] && echo "${RN[i + 1]}" || rdev $((i + 1)))")
    done
    [[ $quick == 1 ]] || {
    load_step 0 dry-partition "${map[@]}" --only partition
    for ((i = 1; i <= n; i++)); do
        sshk "$rkey" "dd if=$(rraw $i) bs=512 count=34 2>/dev/null | od -An -v | tr -d ' 0\n' | grep -q ." >/dev/null 2>&1 && written+=("$(rdev $i)")
    done
    if ((${#written[@]})); then bad "${written[*]} written without --commit"; else ok "nothing written without --commit"; fi
    sshk "$rkey" "$edges" > "$R/logs/edges-after.txt" 2>&1
    if [[ -s $R/logs/edges-before.txt ]] && cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
        ok "the refusals and the dry run left every target's and the decoy's first and last MiB as they were"
    else
        bad "a refusal or the dry run wrote to a disk (logs/edges-before.txt, edges-after.txt)"
    fi
    }
    map+=("${erase[@]}")
    local repo=(--repo $RH/repo --password-file $RH/repo-password --archiver $RH/rustic-bko --snapshot "latest:/mnt/src")
    local stages=(partition assemble format mount restore boot close)
    [[ $VARIANT == oneshot ]] && stages=(oneshot)
    if [[ -n ${HT_THROUGH:-} ]]; then
        [[ " ${stages[*]} " == *" $HT_THROUGH "* ]] || { bad "HT_THROUGH=$HT_THROUGH: not one of ${stages[*]}"; vm_down "$rkey"; return; }
        local upto=() st; for st in "${stages[@]}"; do upto+=("$st"); [[ $st == "$HT_THROUGH" ]] && break; done; stages=("${upto[@]}")
    fi
    for s in "${stages[@]}"; do
        local how=(--only "$s") extra=()
        [[ $s == restore || $s == oneshot ]] && extra=("${repo[@]}")
        [[ $s == oneshot ]] && how=(--through close)
        TMO=7200 load_step 0 "$s" "${map[@]}" "${extra[@]}" "${how[@]}" --commit || {
            sshk "$rkey" "cat /var/run/bko-holt/log/load.log" > "$R/logs/rebuild-load.log"
            sshk "$rkey" "set -x; mount; df; gpart show; disklabel ${RN[1]}; dkctl ${RN[1]} listwedges; zpool status; zpool list -v; zfs list -o name,mounted,mountpoint,canmount; glabel status; swapctl -l" > "$R/logs/rebuild-failed-state.txt" 2>&1
            kept_or_down; return; }
        [[ $PLAT == haiku ]] && sshk "$rkey" "echo $s \$(stat -c %Y.%y /mnt/bko-target 2>&1)" >> "$R/logs/haiku-root-mtime.txt" 2>&1
        # Haiku publishes a new table's partitions only at boot.
        if [[ $PLAT == haiku && $s == partition ]]; then
            TMO=20 sshk "$rkey" "shutdown -r -q" >/dev/null 2>&1
            sleep 20
            vm_wait "$rkey" || { bad "the rescuer's restart after partition"; vm_down "$rkey"; return; }
            rescuer_ready $((n + 2))
            put "$rkey" "$HOLT" $RH/bko-holt
        fi
        sshk "$rkey" "$RH/bko-holt load $RH/layout.json ${map[*]} --only status" > "$R/logs/rebuild-status-after-$s.txt" 2>&1
        if [[ $s == oneshot ]]; then
            local st
            for st in partition format close; do
                grep -q "^$st  *done" "$R/logs/rebuild-status-after-$s.txt" || bad "status after the one-command rebuild: $(grep "^$st " "$R/logs/rebuild-status-after-$s.txt")"
            done
            grep -q "^next: nothing" "$R/logs/rebuild-status-after-$s.txt" || bad "status after the one-command rebuild: $(grep '^next' "$R/logs/rebuild-status-after-$s.txt")"
        else
            grep -q "^$s  *done" "$R/logs/rebuild-status-after-$s.txt" || [[ $s == assemble ]] || bad "status after $s: $(grep "^$s " "$R/logs/rebuild-status-after-$s.txt")"
        fi
    done
    # The decoy was never a target: its first and last MiB are as they were.
    if [[ $quick != 1 ]]; then
        sshk "$rkey" "$edges" > "$R/logs/edges-end.txt" 2>&1
        [[ $(sed -n "${D}p" "$R/logs/edges-end.txt") == "$(sed -n "${D}p" "$R/logs/edges-before.txt")" && -n $(sed -n "${D}p" "$R/logs/edges-end.txt") ]] || bad "the decoy changed (logs/edges-before.txt, edges-end.txt)"
    fi
    sshk "$rkey" "cat /var/run/bko-holt/log/load.log" > "$R/logs/rebuild-load.log"
    [[ ${HT_KEEP:-} == 1 ]] && { kept_or_down; return; }
    vm_down "$rkey"
    rm -f "$try"
}
# HT_KEEP=1: the rescuer stays up, as the stage left it; else it goes down.
kept_or_down() {
    if [[ ${HT_KEEP:-} == 1 ]]; then
        say "rescuer kept up: ssh -i $rkey -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $GUSER@127.0.0.1 (load: $RH/bko-holt load $RH/layout.json ...)"
    else
        vm_down "$rkey"
    fi
}

# mtree's report, but for what the boot stage writes.
BOOT_STAGE_RE='^(extra: |missing: )?(\./)?(var/log/bko-holt|var/log|boot/zfs/zpool\.cache|etc/zfs/zpool\.cache|boot/zfs|etc/zfs|\.sujournal)( |:|/|$)'

phase_verify() {
    # A rescuer HT_KEEP left up has the targets open.
    pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null && vm_down "$rkey"
    say "== verify"
    if [[ -f $RESC ]]; then
        vm_up "$RESC" "$R/logs/verify-rescuer-serial.log" "${v_snap[@]}" "${rfw[@]}" --memory 3072 "${s_resc[@]}" "${all_dst[@]}"
        if vm_wait "$rkey"; then
            rescuer_ready $((1 + n))
            mount_script "$R/dumps/src.json" 1 /mnt/dst > "$R/logs/verify-mount.sh" 2> "$R/logs/verify-mount.log" ||
                echo exit 1 > "$R/logs/verify-mount.sh"
            if shk "$rkey" < "$R/logs/verify-mount.sh" > "$R/logs/verify-mount.log" 2>&1; then
                grep '^HT1-POOL ' "$R/logs/verify-mount.log" | while read -r _ pn rest; do ok "the restored pool $pn imported from all its disks ($rest)"; done
                if [[ $PLAT == haiku || $PLAT == illumos ]]; then
                    TMO=3600 sshk "$rkey" "$(list_tree /mnt/dst)" > "$R/dst.listing" 2>/dev/null
                    diff "$R/src.listing" "$R/dst.listing" | sed -n 's/^< /missing: /p; s/^> /extra: /p' > "$R/logs/fsmeta-compare.txt"
                else
                    put "$rkey" "$R/src.listing" $RH/src.mtree
                    TMO=3600 sshk "$rkey" "cd /mnt/dst && mtree -f $RH/src.mtree -p /mnt/dst" > "$R/logs/fsmeta-compare.txt" 2>&1
                fi
                local diffs; diffs=$(grep -vE "$BOOT_STAGE_RE" "$R/logs/fsmeta-compare.txt" | grep -vE "$EXTRA_BOOT_RE" | grep -vE "$KNOWN_RE" | grep -vE '^[[:space:]]' | grep -c .)
                local known; known=$(grep -cE "$KNOWN_RE" "$R/logs/fsmeta-compare.txt")
                if [[ $diffs == 0 ]]; then
                    ok "mtree: the files agree but for the boot stage's ($(grep -c . "$R/logs/fsmeta-compare.txt") lines of it)$([[ $known -gt 0 ]] && echo "; known archiver limit: the volume root's mtime ($known lines)")"
                else
                    bad "mtree: $diffs differences: $(grep -vE "$BOOT_STAGE_RE" "$R/logs/fsmeta-compare.txt" | grep -vE "$EXTRA_BOOT_RE" | grep -vE "$KNOWN_RE" | grep -vE '^[[:space:]]' | head -5 | tr '\n' ' ')"
                fi
            else
                bad "mount the restored disk in the rescuer (logs/verify-mount.log)"
            fi
            umount_script "$R/dumps/src.json" /mnt/dst | shk "$rkey" >> "$R/logs/verify-mount.log" 2>&1
        else
            bad "rescuer boots for the mtree comparison"
        fi
        vm_down "$rkey"
    fi
    vm_up "$DST" "$R/logs/restored-serial.log" --snapshot "${fw[@]}" "${s_guest[@]}" "${dst_more[@]}"
    if ! vm_wait "$gkey"; then bad "the restored disk boots by itself (no SSH; logs/restored-serial.log)"; vm_down "$gkey"; return; fi
    ok "the restored disk boots by itself, SSH with the guest's key"
    sshk "$gkey" "$BOOTSTATE" > "$R/logs/restored-bootstate.txt" 2>&1
    if diff -q "$R/logs/source-bootstate.txt" "$R/logs/restored-bootstate.txt" >/dev/null; then
        ok "boot state as the source's ($(grep -c . "$R/logs/restored-bootstate.txt") services down on both)"
    else
        bad "boot state: source $(tr '\n' ' ' < "$R/logs/source-bootstate.txt"), restored $(tr '\n' ' ' < "$R/logs/restored-bootstate.txt")"
    fi
    sshk "$gkey" "$HEALTH" > "$R/logs/restored-health.txt" 2>&1
    local newfails; newfails=$(grep '^FAIL' "$R/logs/restored-health.txt" | grep -vxFf <(grep '^FAIL' "$R/logs/source-health.txt" 2>/dev/null) || true)
    if [[ -z $newfails ]]; then ok "health: $(grep -v '^FAIL' "$R/logs/restored-health.txt" | tr '\n' ',' | sed 's/,$//; s/,/, /g')"; else bad "health: $(tr '\n' ' ' <<<"$newfails")"; fi
    put "$gkey" "$HOLT" /tmp/bko-holt
    sshk "$gkey" /tmp/bko-holt dump > "$R/dumps/restored.json" 2> "$R/logs/restored-dump.err" || bad "dump of the restored system: $(tail -1 "$R/logs/restored-dump.err")"
    vm_down "$gkey"
    local grow=(); [[ $VARIANT == bigger ]] && grow=(--grow $((1 << 30)))
    if python3 "$HERE/judge.py" "${grow[@]}" "$R/dumps/src.json" "$R/dumps/restored.json" > "$R/logs/judge.txt" 2>&1; then
        ok "the dumps agree ($(tail -1 "$R/logs/judge.txt"))"
    else
        bad "the dumps differ: $(grep -c '^DIFF' "$R/logs/judge.txt") fields (logs/judge.txt)"
    fi
}

say "HoltTest1 $G ($PLAT), run $RUN, $(date -u +%FT%TZ), phases: ${PHASES[*]}"
for p in "${PHASES[@]}"; do "phase_$p"; done
if ((fails == 0)); then
    say "RESULT: PASS"
    [[ " ${PHASES[*]} " == *" verify "* ]] && rm -f "${src[@]}" "${dst[@]}" "$RESC" "$R/rescuer-try.qcow2" "$R/decoy.qcow2"
    exit 0
fi
say "RESULT: FAIL ($fails)"
exit 1
