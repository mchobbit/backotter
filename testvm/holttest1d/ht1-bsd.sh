#!/usr/bin/env bash
# HoltTest1x for a guest that isn't Linux (README: "Systems other than
# Linux"): ht1.sh hands such guests here.  Run as root on a test host:
#
#   ht1-bsd.sh GUEST RUN [PHASE...]    PHASEs: source backup rebuild verify
#
# The same round trip as ht1.sh, through bko-holt and rustic-bko built for
# the guest's own system (bin/PLATFORM/bko-holt from build-holt.sh, or
# HT_HOLT_BIN/PLATFORM/bko-holt, and
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
# The write path is ht1-lib.sh's (README: "Where a run works, and what it
# keeps"): R is /mnt/bko-ram/holttest1d/RUN/GUEST when the caller holds a
# reservation, and the run ends up in /bkoscratch/holttest1d/RUN/GUEST; the
# restores use a checked copy of the shipped set's PLATFORM/rustic-bko
# (HT_BKO_BIN, HT_ROUND, HT_RUSTIC).  The debugging options are ht1.sh's
# (HT_RAM, HT_CLEAN, HT_HOLT, HT_QUICK, HT_THROUGH, HT_KEEP; ht1-debug.sh
# drives them).  Each rebuild runs its rescuer on a throwaway overlay of the
# one holding the backup (R/rescuer-try.qcow2), so no try leaves anything
# behind for the next (an imported pool, a log).  HT_VARIANT=img rebuilds in
# BackOtter's own rescue image of the system instead (below, "+img").
set -uo pipefail

G=$1 RUN=$2; shift 2
PHASES=("$@"); [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(source backup rebuild verify)
W=/home/m/backotter-work
BIN=/bkoscratch/holttest1d/bin
HERE=$(dirname "$(realpath "$0")")
T=$(dirname "$HERE")   # this copy's testvm/ (vm-run, vm-lib, compare/), as ht1.sh's
[[ -r $HERE/ht1-lib.sh ]] || { echo "ht1-bsd: no ht1-lib.sh beside $0" >&2; exit 2; }
# shellcheck source=ht1-lib.sh
. "$HERE/ht1-lib.sh"
VARIANT=${HT_VARIANT:-}
case $VARIANT in ""|bigger|oneshot|img) ;; *) echo "HT_VARIANT: bigger, oneshot or img" >&2; exit 2 ;; esac
GV=$G${VARIANT:+-$VARIANT}
PORT=${HT_PORT:-2690}
export SSH_PORT=$PORT
fails=0
say() { echo "$*" | tee -a "$R/report.txt"; }
ok() { say "PASS $*"; }
bad() { say "FAIL $*"; fails=$((fails + 1)); }
ht_paths   # R, where the run works, and K, where it ends up (ht1-lib.sh)

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
        # (After /etc/rc is done: sshd starts before rc's local daemons,
        # cron among them, so a check as soon as SSH answers can find cron
        # not started yet, on a fast boot: openbsd8-crypt's source, run
        # obc1.  reorder_kernel, which rc leaves running, is its own ksh.)
        BOOTSTATE='i=0; while pgrep -f "^/bin/sh /etc/rc( |\$)" >/dev/null && [ $i -lt 90 ]; do sleep 2; i=$((i + 1)); done; rcctl ls failed | sed "s/^/down /" | sort'
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
# +img (ImgTest2's panel, as ht1.sh's HT_VARIANT=img): the rebuild runs in
# BackOtter's own rescue image of this system, the --test image t2-host tool
# img pin pinned, as the first disk through a 16 GiB throwaway overlay
# (R/img.qcow2), booted with the rescuer guest's options and the source's
# firmware.  Its own bko-holt and rustic-bko do the load, as a user's would;
# source and backup take the pin's copies of them (checked against the
# image's bko-bin.sha256 when it was pinned), so the dumps and the load are
# one build.  backup and verify keep the rescuer guest: its disk with the
# repository (R/rescuer.qcow2, through the throwaway R/rescuer-try.qcow2) is
# the image's last disk, after the decoy, mounted read-only at IMG_RR.  The
# image's sshd is off and refuses root by its own settings: img-boot.py
# starts one over the serial console with a key made for this run
# (img-boot.d/PLATFORM.json says what differs per system), R/img-key, never
# in an image, deleted at the rebuild's end and in ht_release.  +img runs
# only in the RAM disk, and its rebuild checks that the pin is still the one
# the run started with.
IMG=0; [[ $VARIANT == img ]] && IMG=1
IMG_PIN=${T2_HOST_ROOT:-/bkoscratch/test2}/img-pin/$PLAT
IMG_BIN=/usr/local/bin; [[ $PLAT == haiku ]] && IMG_BIN=/boot/system/non-packaged/bin
IMG_RR=/tmp/bko-repo
if ((IMG)); then
    [[ -f $IMG_PIN/rescuer.img && -f $IMG_PIN/rustic-bko && -f $IMG_PIN/bko-holt ]] || { echo "ht1-bsd: +img: no pinned image in $IMG_PIN (t2-host tool img pin $PLAT)" >&2; exit 2; }
    [[ ! -e $IMG_PIN/bins-stale ]] || { echo "ht1-bsd: +img: $IMG_PIN/bins-stale: the pin's binaries aren't the image's" >&2; exit 2; }
    [[ ${HT_KEEP:-} != 1 && ${HT_CLEAN:-1} != 0 ]] || { echo "ht1-bsd: +img: no HT_KEEP or HT_CLEAN=0 (the run's key goes at its end)" >&2; exit 2; }
    # (In RAM only: on disk R is K, where a killed run would leave the key.)
    [[ $HT_MODE == ram ]] || { echo "ht1-bsd: +img: only in the RAM disk (under cmp-run.sh --reserve, not HT_RAM=0): on disk the run's key would be in $K" >&2; exit 2; }
    [[ -f $HERE/img-boot.py && -f $HERE/img-boot.d/$PLAT.json && -f $T/../stick/boot-test.py ]] ||
        { echo "ht1-bsd: +img: no img-boot.py or img-boot.d/$PLAT.json beside $0, or no stick/boot-test.py (img-boot.py's) beside $T" >&2; exit 2; }
    python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$HERE/img-boot.d/$PLAT.json" 2>/dev/null ||
        { echo "ht1-bsd: +img: img-boot.d/$PLAT.json doesn't read as JSON" >&2; exit 2; }
    export HT_RUSTIC=$IMG_PIN/rustic-bko HT_HOLT=$IMG_PIN/bko-holt
fi
# The pin as this run took it: the image's and the binaries' size, mtime and
# inode, and rescuer.img.sha256's hash (what the row is keyed to).  A re-pin
# renames over them; the rebuild checks the pin is still this one, else it
# isn't the image the row would be credited to (a harness FAIL).
pin_id() {
    local f; for f in rescuer.img bko-holt rustic-bko; do echo -n "$f $(stat -L -c '%s %Y %i' "$IMG_PIN/$f" 2>/dev/null || echo none); "; done
    echo "sha256 $(head -n 1 "$IMG_PIN/rescuer.img.sha256" 2>/dev/null | cut -d' ' -f1)"
}
IMG_ID=""; ((IMG)) && IMG_ID=$(pin_id)
IMG_HARNESS=""   # a harness failure's why, for the RESULT line (rows_holt: HARNESS)
# What the load runs in the rescuer, and where it finds the repository (+img:
# the image's own binaries; the repository on its disk, Haiku's /boot/home
# being that volume's /home).
RHOLT=$RH/bko-holt RARCH=$RH/rustic-bko RREPO=$RH
((IMG)) && RHOLT=$IMG_BIN/bko-holt RARCH=$IMG_BIN/rustic-bko RREPO=$IMG_RR${RH#/boot}
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
        # guest's disks in order, the decoy.  The rescue image (+img's
        # rebuild, bkoimg) is its own disk, and the rescuer guest's disk
        # (bkoresc, the repository's) comes after the decoy.
        local by; by=$(sshk "$rkey" 'iostat -En' | awk '/^c[0-9]/ {d = $1} /Serial No:/ {s = $0; sub(/.*Serial No: */, "", s); sub(/ .*/, "", s); print s, d}')
        if grep -q '^bkoresc ' <<<"$by"; then
            local order=(bkoresc) last=() i
            grep -q '^bkoimg ' <<<"$by" && order=(bkoimg) last=(bkoresc)
            for ((i = 0; i < n; i++)); do order+=("bkodisk-$i"); done
            order+=(bkodecoy "${last[@]}")
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
# bko-holt: HT_HOLT's, else HT_HOLT_BIN's set's (DIR/PLATFORM/bko-holt, as in
# bin/: HoltTest2's builds), else bin/'s (ht1.sh's header).
HOLT=${HT_HOLT:-${HT_HOLT_BIN:-$BIN}/$PLAT/bko-holt}
[[ -n ${HT_HOLT:-} || -z ${HT_HOLT_BIN:-} || -f $HOLT ]] || { echo "ht1-bsd: no $HOLT (HT_HOLT_BIN=$HT_HOLT_BIN)" >&2; exit 2; }
gdir=$W/vms/$G gkey=$gdir/id_ed25519
rdir=$W/vms/$RG rkey=$rdir/id_ed25519
# GELI (docs/design/geli.md, freebsd15-geli): the loader asks for the
# passphrase in console-passphrase, so the source and the restored disks boot
# through console-boot.py.  In the rescuer it is GELI_KEY, in a tmpfs of its
# own (GELI_DIR: a default install's /tmp is on its disk),
# for geli attach -j and load --key-file.
gpass=""; [[ -f $gdir/console-passphrase ]] && gpass=$gdir/console-passphrase
GELI_DIR=/tmp/ht1-geli GELI_KEY=/tmp/ht1-geli/passphrase
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
# Where the run works and what a pass keeps (ht1-lib.sh; README "Where a run
# works, and what it keeps").  RESC holds the repository: a rerun from
# rebuild or verify needs it.
HT_BULK=('src-*.qcow2' 'dst-*.qcow2' rescuer.qcow2 rescuer-try.qcow2 decoy.qcow2 prep-boot.qcow2 rustic-bko repo-password tmp img.qcow2 img-key img-key.pub)
HT_ZIP=(src.listing dst.listing)
ht_require
case ${PHASES[0]} in   # what the first phase remakes is not copied back from K
    backup)  ht_seed rescuer.qcow2 rescuer-try.qcow2 'dst-*.qcow2' decoy.qcow2 img.qcow2 tmp/ ;;
    rebuild) ht_seed 'src-*.qcow2' 'dst-*.qcow2' rescuer-try.qcow2 decoy.qcow2 img.qcow2 tmp/ ;;
    verify)  ht_seed 'src-*.qcow2' rescuer-try.qcow2 decoy.qcow2 img.qcow2 tmp/ ;;
esac
ht_begin
ht_rustic_take "$PLAT"
# Each one-time rescuer below stays on /bkoscratch, and is made under a lock
# (checked again inside it): two jobs can't both write its .part, and the
# second finds it made.  The wait is in the background, so that a signal (the
# sampler's stop, a kill) ends it at once; ht_release ends the waiter then.
# Up to 2 h: DragonFly's prep can take up to 90 min (boot, cpdup, poweroff).
plw=""
prep_lock() {
    { exec {plk}>>"/bkoscratch/holttest1d/rescuer/.$RG-prep.lock" && { flock -w 7200 "$plk" & plw=$!; wait "$plw"; }; }
    local s=$?; plw=""
    ((s == 0)) || { echo "ht1-bsd: no lock for $prep" >&2; exit 1; }
}
ht_release() { rm -f "$R/img-key" "$R/img-key.pub"; [[ -z $plw ]] || kill "$plw" 2>/dev/null; }   # (ht_end calls it)
# The prep's VM: the QEMU on its image (vm-run names it by its real path; the
# lock keeps it the only one), never another run's on the port.
prep_qemu() { pgrep -f "^qemu-system.*$(realpath -m -- "$prep.part" | sed 's/[][\.*^$]/\\&/g')"; }
# OpenBSD finds its partitions by the disklabel's UID (DUID), and two disks
# with one DUID make it refuse both: the rescuer is a copy of openbsd8
# with a DUID of its own (fstab rewritten to match), made once.
if [[ $PLAT == openbsd ]]; then
    prep=/bkoscratch/holttest1d/rescuer/$RG-newduid.qcow2
    mkdir -p "${prep%/*}"
    prep_lock
    if [[ ! -f $prep ]]; then
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
        for _ in $(seq 36); do [[ -n $(prep_qemu) ]] || break; sleep 5; done
        # shellcheck disable=SC2046  # PIDs
        kill $(prep_qemu) 2>/dev/null
        grep -q "^DUID" "$R/logs/rescuer-prep.log" && mv "$prep.part" "$prep"
        [[ -f $prep ]] && ht_note "made the one-time rescuer $prep ($(du -sh "$prep" | cut -f1) on /bkoscratch)"
    fi
    exec {plk}>&-
    [[ -f $prep ]] || { echo "ht1-bsd: no OpenBSD rescuer with its own DUID (logs/rescuer-prep.log)" >&2; exit 1; }
    rimg=$prep
fi
# illumos won't import a pool with the GUID of one it has imported, and the
# source's rpool is a copy of omnios58's own: the rescuer is a copy of
# omnios58 whose pool has a new GUID (zpool reguid), made once.
if [[ $PLAT == illumos ]]; then
    prep=/bkoscratch/holttest1d/rescuer/$RG-reguid.qcow2
    mkdir -p "${prep%/*}"
    prep_lock
    if [[ ! -f $prep ]]; then
        qemu-img convert -O qcow2 "$rimg" "$prep.part"
        setsid -f "$T/vm-run" "${rfw[@]}" --serial-log "$R/logs/rescuer-prep-serial.log" "$prep.part" </dev/null >/dev/null 2>&1
        for _ in $(seq 120); do timeout 8 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@127.0.0.1 true 2>/dev/null && break; sleep 5; done
        timeout 300 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 '/usr/sbin/zpool reguid rpool && /usr/sbin/zpool get -H -o value guid rpool && echo PREP-DONE' > "$R/logs/rescuer-prep.log" 2>&1
        timeout 30 ssh -q -i "$rkey" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 "/usr/sbin/shutdown -y -i5 -g0" >/dev/null 2>&1
        for _ in $(seq 36); do [[ -n $(prep_qemu) ]] || break; sleep 5; done
        # shellcheck disable=SC2046  # PIDs
        kill $(prep_qemu) 2>/dev/null
        grep -q "^PREP-DONE" "$R/logs/rescuer-prep.log" && mv "$prep.part" "$prep"
        [[ -f $prep ]] && ht_note "made the one-time rescuer $prep ($(du -sh "$prep" | cut -f1) on /bkoscratch)"
    fi
    exec {plk}>&-
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
    mkdir -p "${prep%/*}"
    prep_lock
    if [[ ! -f $prep ]]; then
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
        for _ in $(seq 36); do [[ -n $(prep_qemu) ]] || break; sleep 5; done
        # shellcheck disable=SC2046  # PIDs
        kill $(prep_qemu) 2>/dev/null
        rm -f "$R/prep-boot.qcow2"
        grep -q "^PREP-DONE" "$R/logs/rescuer-prep.log" && mv "$prep.part" "$prep"
        [[ -f $prep ]] && ht_note "made the one-time rescuer $prep ($(du -sh "$prep" | cut -f1) on /bkoscratch)"
    fi
    exec {plk}>&-
    [[ -f $prep ]] || { echo "ht1-bsd: no DragonFly rescuer with a HAMMER2 of its own (logs/rescuer-prep.log)" >&2; exit 1; }
    rimg=$prep
fi
rfw=(); [[ -f $rdir/vm-run-options ]] && read -ra rfw < "$rdir/vm-run-options"
# +img: the image's options, the rescuer guest's (omnios58's
# --legacy-virtio-net, haiku6's --disk-bus sata) less its disks and firmware,
# with the source's firmware (a stick boots in the machine's own mode), and
# img-boot.d's disk_bus where the rescuer guest names none.
ifw=()
if ((IMG)); then
    for ((i = 0; i < ${#rfw[@]}; i++)); do
        case ${rfw[i]} in --disk) ((i++)) ;; --uefi|--no-secure-boot|--q35) ;; *) ifw+=("${rfw[i]}") ;; esac
    done
    [[ " ${fw[*]} " == *" --uefi "* ]] && ifw+=(--uefi)
    [[ " ${fw[*]} " == *" --no-secure-boot "* ]] && ifw+=(--no-secure-boot)
    bus=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("disk_bus") or "")' "$HERE/img-boot.d/$PLAT.json") ||
        { echo "ht1-bsd: +img: img-boot.d/$PLAT.json doesn't read" >&2; exit 2; }
    [[ -z $bus || " ${ifw[*]} " == *" --disk-bus "* ]] || ifw+=(--disk-bus "$bus")
fi
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
    # +img: the image is the rescuer's own disk (bkoimg), and the
    # repository's disk follows the decoy.  That disk is the rescuer guest's
    # and keeps its serial, bkoresc, as a disk moved to another machine
    # does: its pool's label names the disk by a devid made from it, and
    # illumos opens a whole-disk vdev whose devid doesn't match by that devid
    # instead.  With bkoresc on the image, the import read the image's
    # partition as the pool ("corrupted data", then EOVERFLOW and an abort
    # in zpool import).
    ((IMG)) && s_decoy=(--disk-serials "$(IFS=,; echo "bkoimg,${ids[*]},bkodecoy,bkoresc")" --disk-slots "$(IFS=,; echo "16,${sl[*]},$((4 + n)),$((5 + n))")")
fi
size_of() { qemu-img info --output=json "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["virtual-size"])'; }

# The command goes on sh's stdin: no quoting to survive (csh is DragonFly's
# root shell; sh is everywhere).
sshk() { local key=$1; shift; printf 'PATH=%s; export PATH\n%s\n' "$GPATH" "$*" | timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o ServerAliveInterval=15 "$GUSER@127.0.0.1" "/bin/sh -s"; }
# A script on stdin, for anything longer than a line.
shk() { local key=$1; { printf 'PATH=%s; export PATH\n' "$GPATH"; cat; } | timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o ServerAliveInterval=15 "$GUSER@127.0.0.1" "/bin/sh -s"; }
put() { local key=$1 file=$2 dest=$3; timeout 600 ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$GUSER@127.0.0.1" "cat > '$dest' && chmod 755 '$dest'" < "$file"; }
# (Without ht_begin's copies of the caller's stdout and stderr: a rescuer
# HT_KEEP keeps up outlives the run, and its caller's ssh or pipe.)
vm_up() { local image=$1 log=$2; shift 2
    if [[ -n ${IMG_BOOT:-} ]]; then
        # The rescue image (+img): img-boot.py logs its console and starts sshd with the run's key.
        setsid -f python3 -B "$HERE/img-boot.py" "$log" "$PLAT" "$R/img-key.pub" -- \
            "$T/vm-run" "$@" "$image" </dev/null >"$log.vm-run" 2>&1 {HT_FD1}>&- {HT_FD2}>&-
        return
    fi
    if [[ -n ${CONSOLE_KEY:-} ]]; then
        # A loader that asks for the GELI passphrase on the serial console:
        # console-boot.py logs the console at LOG (as --serial-log would) and
        # types the passphrase at each prompt, as ht1.sh's vm_up.
        setsid -f python3 "$T/console-boot.py" "$log" "$CONSOLE_KEY" -- \
            "$T/vm-run" "$@" "$image" </dev/null >"$log.vm-run" 2>&1 {HT_FD1}>&- {HT_FD2}>&-
        return
    fi
    setsid -f "$T/vm-run" --serial-log "$log" "$@" "$image" </dev/null >"$log.vm-run" 2>&1 {HT_FD1}>&- {HT_FD2}>&-
}
vm_wait() { local key=$1
    for _ in $(seq 120); do TMO=8 sshk "$key" true 2>/dev/null && return 0; sleep 5; done
    return 1
}
# This run's VM only (ht_qemus: a QEMU with its disks in R that this run
# started, ht_own), never another run's that took the port while none of
# this run's was up, nor one started by hand on R's (K's) disks.
vm_down() { local key=$1
    [[ -n $(ht_qemus "$R") ]] || return 0
    TMO=20 sshk "$key" "/sbin/poweroff || /sbin/shutdown -p now || /usr/sbin/shutdown -y -i5 -g0 || shutdown -q" >/dev/null 2>&1
    for _ in $(seq 36); do [[ -n $(ht_qemus "$R") ]] || return 0; sleep 5; done
    # shellcheck disable=SC2046  # PIDs
    kill $(ht_qemus "$R") 2>/dev/null; sleep 5
    # shellcheck disable=SC2046
    kill -9 $(ht_qemus "$R") 2>/dev/null; sleep 1
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

# The layout's GELI providers, one line each: the partition (or disk) that
# holds one, the source's provider name less .eli (load's --key-file NAME)
# and how it is keyed.  The one reader of the dump's GELI records, where
# their shape may still move: a content of type geli, or a geli object on
# the partition or in its content; the provider from geli.provider, else
# PARTITION.eli; keyed_by passphrase when it says nothing.
geli_provs() { # LAYOUT
    python3 - "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
def geli_of(x):
    c = x.get("content") if isinstance(x.get("content"), dict) else {}
    for g in (x.get("geli"), c.get("geli")):
        if isinstance(g, dict):
            return g
    return c if c.get("type") == "geli" else None
for disk in (d.get("storage") or {}).get("disks") or []:
    for x in [disk] + (disk.get("partitions") or []):
        g = geli_of(x)
        if g is None:
            continue
        prov = str(g.get("provider") or x["name"] + ".eli").removeprefix("/dev/")
        print(x["name"], prov.removesuffix(".eli"), g.get("keyed_by") or "passphrase")
PY
}
# OpenBSD's softraid volumes (docs/design/openbsd-crypto.md, openbsd8-crypt),
# one line each: the volume's name in the dump (sd1: load's --key-file NAME),
# its chunks (sd0a, comma-separated), its DUID, its level and what unlocks a
# CRYPTO one.  The one reader of the dump's softraid_volumes here.
sr_vols() { # LAYOUT
    python3 - "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for v in (d.get("storage") or {}).get("softraid_volumes") or []:
    sr = v.get("softraid") or {}
    print(v.get("name") or "-", ",".join(sr.get("chunks") or []) or "-", v.get("duid") or "-", sr.get("level") or "-", sr.get("key") or "-")
PY
}
# The passphrase into the rescuer (KEY, its ssh key) at GELI_KEY, mode 600,
# without its final newline (as console-boot.py types it), and out again.
# The same for a softraid CRYPTO volume's (bioctl -p, load --key-file; bioctl
# reads the file's first line, so a final newline would do as well), in an
# mfs: OpenBSD has no tmpfs.
geli_key_put() { # KEY
    [[ -n $gpass ]] || { echo "ht1-bsd: the layout has GELI providers or softraid CRYPTO volumes, and $gdir has no console-passphrase" >&2; return 1; }
    if [[ $PLAT == openbsd ]]; then
        shk "$1" <<EOF || return 1
mkdir -p $GELI_DIR || exit 1
mount | grep -q " on $GELI_DIR type mfs " || mount_mfs -s 8192 -o nodev,nosuid,noexec swap $GELI_DIR || exit 1
chmod 700 $GELI_DIR || exit 1
EOF
    else
        shk "$1" <<EOF || return 1
mkdir -p $GELI_DIR && chmod 700 $GELI_DIR || exit 1
[ "\$(mount -p | awk '\$2 == "$GELI_DIR" { print \$3 }')" = tmpfs ] || mount -t tmpfs -o mode=0700,size=1m tmpfs $GELI_DIR || exit 1
EOF
    fi
    python3 -c 'import sys; sys.stdout.buffer.write(open(sys.argv[1], "rb").read().rstrip(b"\n"))' "$gpass" |
        timeout 60 ssh -q -i "$1" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$GUSER@127.0.0.1" "umask 077; cat > $GELI_KEY" || return 1
    # (OpenBSD: and GELI_KEY.nl, with the newline bioctl -p wants, for the
    # mount script's attach; load gets GELI_KEY and adds it itself.)
    if [[ $PLAT == openbsd ]]; then
        sshk "$1" "umask 077; { cat $GELI_KEY; echo; } > $GELI_KEY.nl && chmod 600 $GELI_KEY.nl" || return 1
    fi
    sshk "$1" "[ -s $GELI_KEY ] && [ \"\$(stat -f %Lp $GELI_KEY)\" = 600 ]"
}
geli_key_rm() { sshk "$1" "rm -f $GELI_KEY $GELI_KEY.nl; umount $GELI_DIR 2>/dev/null; rmdir $GELI_DIR 2>/dev/null; true" >/dev/null 2>&1; }   # KEY

# Mounts the layout's filesystems read-only at MP, its first disk being
# the rescuer's Nth (and so on): a shell script for the rescuer.
mount_script() { # LAYOUT FIRST MP
    local geli sr; geli=$(geli_provs "$1") && sr=$(sr_vols "$1") || return 1
    HT_GELI=$geli HT_SR=$sr HT_GELI_KEY=$GELI_KEY HT_GELI_DIR=$GELI_DIR python3 - "$1" "$2" "$3" "$PLAT" "${RN[@]}" <<'PY'
import json, os, re, sys
d = json.load(open(sys.argv[1])); first = int(sys.argv[2]); mp = sys.argv[3]; plat = sys.argv[4]; rn = sys.argv[5:]
geli = [l.split() for l in os.environ.get("HT_GELI", "").splitlines() if l.strip()]
sr = [l.split() for l in os.environ.get("HT_SR", "").splitlines() if l.strip()]
disks = [x["name"] for x in d["storage"]["disks"]]
def node(part):
    # A softraid volume's partition (sd1a): on the device that has the
    # volume's DUID in the rescuer, $ht1srI, which the attach below sets.
    for i, (vn, *_) in enumerate(sr):
        if part.startswith(vn) and len(part) == len(vn) + 1:
            return f"/dev/${{ht1sr{i}}}{part[-1]}"
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
# GELI providers first, read-only with the passphrase, so that the pool's
# member (or the UFS) is NODE.eli, as on the source; one already attached
# (the kernel, with the rescuer's loader's key) is left so.  Each goes in
# GELI_DIR's list, for umount_script's detach.
for part, _, keyed in geli:
    n = node(part)
    if keyed != "passphrase":
        out.append(f"echo 'GELI on {part} is keyed by {keyed}: the run has only the passphrase'; exit 1")
        continue
    out.append(f"[ -c {n}.eli ] || geli attach -r -j {os.environ['HT_GELI_KEY']} {n}")
    out.append(f"[ -c {n}.eli ] || {{ echo 'geli attach {n}: no {n}.eli'; exit 1; }}")
    out.append(f"echo {n}.eli >> {os.environ['HT_GELI_DIR']}/attached")
# softraid CRYPTO volumes (OpenBSD): attached on their chunk with the
# passphrase (bioctl -c C on a chunk with softraid metadata attaches it; on
# one without, it would make a new volume, so the metadata is checked
# first), unless a volume with that DUID is already there.  Each attached
# DUID goes in GELI_DIR's list, for umount_script's bioctl -d.
for i, (vn, chunks, duid, level, key) in enumerate(sr):
    if level != "CRYPTO" or key != "passphrase" or "," in chunks:
        out.append(f"echo 'softraid volume {vn}: {level} on {chunks}, unlocked by {key}: the run attaches passphrase CRYPTO volumes only'; exit 1")
        continue
    c = node(chunks)
    find = f"sysctl -n hw.disknames | tr , '\\n' | awk -F: '$2 == \"{duid}\" {{ print $1 }}'"
    out.append(f"if [ -z \"$({find})\" ]; then")
    out.append(f"  dd if={c.replace('/dev/', '/dev/r', 1)} bs=512 skip=16 count=1 2>/dev/null | grep -q marcCRAM || {{ echo 'no softraid metadata on {c}'; exit 1; }}")
    out.append(f"  bioctl -c C -l {c} -p {os.environ['HT_GELI_KEY']}.nl softraid0")
    out.append(f"  echo {duid} >> {os.environ['HT_GELI_DIR']}/attached-sr")
    out.append("fi")
    out.append(f"ht1sr{i}=$({find})")
    out.append(f"[ -n \"$ht1sr{i}\" ] || {{ echo 'softraid volume {vn} ({duid}): not attached'; exit 1; }}")
    out.append(f"echo \"HT1-SR {vn} {duid} on $ht1sr{i}: $(bioctl $ht1sr{i} | tr -s ' \\n' ' ')\"")
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
    if (e.get("eli") or e["spec"].endswith(".eli")) and not n.endswith(".eli"):
        n += ".eli"   # a UFS on GELI (resolve() gives the partition, eli: true)
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
    local geli sr; geli=$(geli_provs "$1") && sr=$(sr_vols "$1") || return 1
    HT_GELI=$geli HT_SR=$sr HT_GELI_DIR=$GELI_DIR python3 - "$1" "$2" "$PLAT" <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1])); mp = sys.argv[2]; plat = sys.argv[3]
if plat == "illumos":
    print(f"awk '{{ print $2 }}' /etc/mnttab | awk '$0 == \"{mp}\" || index($0, \"{mp}/\") == 1' | sort -r | while read -r m; do umount -f \"$m\" 2>/dev/null; done")
else:
    print(f"mount | sed -e 's/ type [^ ]* (.*//' -e 's/ (.*//' -e 's/^.* on //' | awk '$0 == \"{mp}\" || index($0, \"{mp}/\") == 1' | sort -r | while read -r m; do umount -f \"$m\"; done")
for p in ((d["storage"].get("zfs") or {}).get("tools") or {}).get("imported_pools") or []:
    if plat == "illumos":
        print("[ ! -f /tmp/ht1-mountpoint-set ] || { zfs inherit mountpoint ht1src; rm -f /tmp/ht1-mountpoint-set; }")
    print(f"zpool export -f {'ht1src' if plat == 'illumos' else p['name']} 2>/dev/null || true")
if os.environ.get("HT_GELI", "").strip():
    # The providers mount_script attached (or found attached), after the export.
    a = os.environ["HT_GELI_DIR"] + "/attached"
    print(f"[ ! -f {a} ] || {{ while read -r e; do geli detach $e || echo \"geli detach $e failed\"; done < {a}; rm -f {a}; }}")
if os.environ.get("HT_SR", "").strip():
    # The softraid volumes mount_script attached (by DUID), after the unmounts.
    a = os.environ["HT_GELI_DIR"] + "/attached-sr"
    print(f"[ ! -f {a} ] || {{ while read -r u; do v=$(sysctl -n hw.disknames | tr , '\\n' | awk -F: -v u=$u '$2 == u {{ print $1 }}'); [ -z \"$v\" ] || bioctl -d $v || echo \"bioctl -d $v ($u) failed\"; done < {a}; rm -f {a}; }}")
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
    CONSOLE_KEY=$gpass vm_up "$SRC" "$R/logs/source-serial.log" "${fw[@]}" "${s_guest[@]}" "${src_more[@]}"
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
    # GELI: the rescuer's loader probes every disk's partitions for a pool,
    # and may ask for a GELIBOOT provider's passphrase too (not yet seen), so
    # it boots through console-boot.py when the source has one.
    # So too for a softraid CRYPTO volume (OpenBSD's /boot probes every
    # disk's softraid metadata), and the mount script attaches it with the
    # passphrase.
    local geli sr ck=""
    geli=$(geli_provs "$R/dumps/src.json" 2>/dev/null) || geli=""   # (no layout: the mount script says so)
    sr=$(sr_vols "$R/dumps/src.json" 2>/dev/null) || sr=""
    [[ -n $geli$sr ]] && ck=$gpass
    CONSOLE_KEY=$ck vm_up "$RESC" "$R/logs/backup-serial.log" "${rfw[@]}" --memory 3072 "${s_resc[@]}" "${all_src[@]}"
    vm_wait "$rkey" || { bad "rescuer boots"; vm_down "$rkey"; return; }
    rescuer_ready $((1 + n))
    if [[ -n $geli$sr ]]; then
        geli_key_put "$rkey" || { bad "the GELI or softraid passphrase into the rescuer's tmpfs $GELI_DIR"; vm_down "$rkey"; return; }
    fi
    put "$rkey" "$RUSTIC" $RH/rustic-bko
    head -c 24 /dev/urandom | base64 > "$R/repo-password"
    put "$rkey" "$R/repo-password" $RH/repo-password
    mount_script "$R/dumps/src.json" 1 /mnt/src > "$R/logs/backup-mount.sh" 2>> "$R/logs/backup-mount.log" ||
        { bad "the mount script (logs/backup-mount.log)"; vm_down "$rkey"; return; }
    if shk "$rkey" < "$R/logs/backup-mount.sh" > "$R/logs/backup-mount.log" 2>&1; then
        ok "the source mounted read-only in the rescuer"
        grep '^HT1-POOL ' "$R/logs/backup-mount.log" | while read -r _ pn rest; do ok "the source's pool $pn imported from all its disks ($rest)"; done
        grep '^HT1-SR ' "$R/logs/backup-mount.log" | while read -r _ vn rest; do ok "the source's softraid volume $vn attached with the passphrase ($rest)"; done
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
    [[ -z $geli$sr ]] || geli_key_rm "$rkey"
    vm_down "$rkey"
}

# +img: the rescue image ready for the load, a script for it: the
# repository's disk (the rescuer guest's, disk n + 2, after the decoy)
# mounted read-only at IMG_RR, where its root's RH is, as each system's own
# tools mount the rescuer guest's root; and the image's own binaries there.
# Nothing is installed.  Its last line says what's wrong, or HT1-IMG-READY.
img_setup_script() {
    local rd=$((n + 2)) d
    d=${RN[rd]}
    echo "mkdir -p $IMG_RR || exit 1"
    case $PLAT in
        freebsd)   # freebsd15-ufs: its root is the UFS partition of its GPT
            cat <<EOF
p=; for x in /dev/${d}p*; do [ "\$(fstyp \$x 2>/dev/null)" = ufs ] && { p=\$x; break; }; done
[ -n "\$p" ] || { echo "no UFS partition on /dev/$d"; exit 1; }
mount -r \$p $IMG_RR || { echo "mount -r \$p failed"; exit 1; }
EOF
            ;;
        netbsd)    # netbsd11: an MBR and its disklabel, root in "a"; /dev is the image's MAKEDEV in tmpfs
            local all=() i; for ((i = 0; i <= rd; i++)); do all+=("${RN[i]}"); done
            cat <<EOF
for x in ${all[*]}; do [ -e /dev/r\${x}d ] || (cd /dev && sh ./MAKEDEV \$x); done
mount -r -t ffs /dev/${d}a $IMG_RR || { echo "mount -r /dev/${d}a failed"; exit 1; }
EOF
            ;;
        openbsd)   # openbsd8 (its DUID of its own): root in the disklabel's "a"
            echo "mount -r /dev/${d}a $IMG_RR || { echo \"mount -r /dev/${d}a failed\"; exit 1; }" ;;
        dragonfly) # dragonfly6 (its HAMMER2 of its own): root the PFS ROOT of s1d
            echo "mount -t hammer2 -o ro /dev/${d}s1d@ROOT $IMG_RR || { echo \"mount /dev/${d}s1d@ROOT failed\"; exit 1; }" ;;
        illumos)   # omnios58 (its pool GUID of its own): rpool, imported by its GUID under another name, read-only
            cat <<EOF
id=; for x in /dev/dsk/${d}s*; do id=\$(zdb -l \$x 2>/dev/null | awk '\$1 == "pool_guid:" { print \$2; exit }'); [ -n "\$id" ] && break; done
[ -n "\$id" ] || { echo "no pool label on $d"; exit 1; }
zpool import -f -o readonly=on -N -R $IMG_RR -t "\$id" bkorepo || { echo "zpool import \$id failed"; exit 1; }
b=\$(zpool get -H -o value bootfs bkorepo); b=bkorepo/\${b#*/}
if [ "\$(zfs get -H -o value mountpoint \$b)" = legacy ]; then mount -F zfs -o ro \$b $IMG_RR; else zfs mount \$b; fi || { echo "mount \$b failed"; exit 1; }
EOF
            ;;
        haiku)     # haiku6: its BFS, partition 0; mounted at boot before rescuer_ready stopped mount_server
            local p; p=$(rdev $rd)/0
            cat <<EOF
for m in \$(df | sed 's/\x1b\[[0-9;]*m//g' | awk -v d=$p '{ if (\$NF == d) print last; last = \$1 }'); do unmount "\$m"; done
mount -ro -t bfs $p $IMG_RR || { echo "mount -ro $p failed"; exit 1; }
EOF
            ;;
    esac
    cat <<EOF
[ -f $RREPO/repo/config ] && [ -f $RREPO/repo-password ] || { echo "no repository at $RREPO on the last disk ($d)"; exit 1; }
for c in $RHOLT $RARCH; do [ -x \$c ] || { echo "missing: \$c"; exit 1; }; done
echo HT1-IMG-READY
EOF
}
img_setup() {
    local out rc
    out=$(img_setup_script | shk "$rkey" 2>&1); rc=$?
    printf '%s\n' "$out" >> "$R/logs/rescuer-img-setup.log"
    ((rc == 0)) && [[ $(tail -n 1 <<<"$out") == HT1-IMG-READY ]]
}

load_step() { local want=$1 name=$2; shift 2
    sshk "$rkey" "$RHOLT load $RH/layout.json $*" > "$R/logs/rebuild-$name.out" 2> "$R/logs/rebuild-$name.err"
    local got=$?
    if [[ $got == "$want" ]]; then ok "load $name (exit $got)"; return 0; fi
    bad "load $name: exit $got, want $want: $(grep -v '^$' "$R/logs/rebuild-$name.err" | tail -3 | tr '\n' ' ')"
    return 1
}

phase_rebuild() {
    say "== rebuild"
    [[ -f $RESC ]] || { bad "no rescuer with the backup (run the backup phase)"; return; }
    # A rescuer HT_KEEP left up from the last try.
    [[ -z $(ht_qemus "$R") ]] || vm_down "$rkey"
    local try=$R/rescuer-try.qcow2
    rm -f "$try"; qemu-img create -q -f qcow2 -b "$RESC" -F qcow2 "$try"
    local grow=0; [[ $VARIANT == bigger ]] && grow=$((1 << 30))
    rm -f "${dst[@]}" "$R/decoy.qcow2"
    for ((i = 0; i < n; i++)); do
        qemu-img create -q -f qcow2 "${dst[i]}" "$(($(size_of "${imgs[i]}") + grow))"
    done
    qemu-img create -q -f qcow2 "$R/decoy.qcow2" "$(size_of "$gimg")"
    # (+img: the rescuer is the image, reached with the run's key, from here
    # to the rebuild's end.)
    local rkey=$rkey
    if ((IMG)); then
        rkey=$R/img-key
        rm -f "$R/img.qcow2" "$R/img-key" "$R/img-key.pub" "$R/logs/rescuer-img-setup.log"
        qemu-img create -q -f qcow2 -b "$IMG_PIN/rescuer.img" -F raw "$R/img.qcow2" 16G
        ssh-keygen -q -t ed25519 -N '' -C ht1-img -f "$R/img-key" || { bad "rescuer setup (the rescue image: no key for the run)"; return; }
        IMG_BOOT=1 vm_up "$R/img.qcow2" "$R/logs/rescuer-serial.log" "${ifw[@]}" --memory 3072 "${s_decoy[@]}" "${all_dst[@]}" --disk "$R/decoy.qcow2" --disk "$try"
    else
        vm_up "$try" "$R/logs/rescuer-serial.log" "${rfw[@]}" --memory 3072 "${s_decoy[@]}" "${all_dst[@]}" --disk "$R/decoy.qcow2"
    fi
    vm_wait "$rkey" || { bad "rescuer boots"; vm_down "$rkey"; return; }
    if ((IMG)); then
        # (After QEMU opened the image: the pin unchanged now means the
        # inode it booted is the one the run started with.)
        local now; now=$(pin_id)
        if [[ $now != "$IMG_ID" ]]; then
            IMG_HARNESS="the pin changed during the run"
            bad "rescuer setup (the rescue image: $IMG_HARNESS: $IMG_PIN at the start: $IMG_ID; at the rebuild: $now)"
            vm_down "$rkey"; return
        fi
        say "note: booted the pin $IMG_PIN: $IMG_ID"
    fi
    rescuer_ready $((n + 2 + IMG))
    local how=""
    if ((IMG)); then
        img_setup || { bad "rescuer setup (the rescue image: the repository's disk and its own binaries: $(tail -n 1 "$R/logs/rescuer-img-setup.log"))"; vm_down "$rkey"; return; }
        how=": the rescue image ($(sshk "$rkey" "uname -sr" 2>/dev/null)), its own $RHOLT and $RARCH, the repository read-only at $RREPO"
    else
        put "$rkey" "$HOLT" $RH/bko-holt
    fi
    put "$rkey" "$R/dumps/src.json" $RH/layout.json
    ((IMG)) || put "$rkey" "$RUSTIC" $RH/rustic-bko   # the run's binary, for a rebuild rerun too
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
    # GELI: each provider made again with the source's passphrase, load's
    # --key-file NAME=FILE (the source's provider name), on every load below.
    # A softraid CRYPTO volume (OpenBSD) the same: its passphrase the
    # source's, --key-file NAME=FILE by the volume's name in the dump (sd1).
    local geli sr kf=() gname
    geli=$(geli_provs "$R/dumps/src.json" 2>/dev/null) || geli=""   # (no layout: the disk count below says so)
    sr=$(sr_vols "$R/dumps/src.json" 2>/dev/null) || sr=""
    if [[ -n $geli$sr ]]; then
        geli_key_put "$rkey" || { bad "rescuer setup (the GELI or softraid passphrase into its tmpfs $GELI_DIR)"; vm_down "$rkey"; return; }
        [[ -z $geli ]] || while read -r _ gname _; do kf+=(--key-file "$gname=$GELI_KEY"); done <<<"$geli"
        [[ -z $sr ]] || while read -r gname _; do kf+=(--key-file "$gname=$GELI_KEY"); done <<<"$sr"
        how+="; ${geli:+GELI}${sr:+softraid}: ${kf[*]}"
    fi
    ok "rescuer up$how"
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
    load_step 4 refuse-nomap "${kf[@]}"
    load_step 4 refuse-root "--map ${srcn[0]}=${RN[0]} --erase ${RN[0]}" "${others[@]}" "${kf[@]}"
    load_step 4 refuse-decoy "--map ${srcn[0]}=${RN[D]}" "${others[@]}" "${kf[@]}" \
        && { cat "$R/logs/rebuild-refuse-decoy".{out,err} | grep -q "isn't blank" || bad "load refuse-decoy: refused, but not for the decoy's filesystem"; }
    }
    local map=() erase=() written=()
    for ((i = 0; i < n; i++)); do
        map+=(--map "${srcn[i]}=${RN[i + 1]}")
        erase+=(--erase "$([[ $PLAT == haiku || $PLAT == illumos ]] && echo "${RN[i + 1]}" || rdev $((i + 1)))")
    done
    map+=("${kf[@]}")
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
    local repo=(--repo $RREPO/repo --password-file $RREPO/repo-password --archiver $RARCH --snapshot "latest:/mnt/src")
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
            sshk "$rkey" "set -x; mount; df; fstat -f /mnt/bko-target; gpart show; disklabel ${RN[1]}; dkctl ${RN[1]} listwedges; zpool status; zpool list -v; zfs list -o name,mounted,mountpoint,canmount; glabel status; swapctl -l" > "$R/logs/rebuild-failed-state.txt" 2>&1
            kept_or_down; return; }
        [[ $PLAT == haiku ]] && sshk "$rkey" "echo $s \$(stat -c %Y.%y /mnt/bko-target 2>&1)" >> "$R/logs/haiku-root-mtime.txt" 2>&1
        # Haiku publishes a new table's partitions only at boot.
        if [[ $PLAT == haiku && $s == partition ]]; then
            TMO=20 sshk "$rkey" "shutdown -r -q" >/dev/null 2>&1
            sleep 20
            vm_wait "$rkey" || { bad "the rescuer's restart after partition"; vm_down "$rkey"; return; }
            rescuer_ready $((n + 2 + IMG))
            if ((IMG)); then
                # (img-boot.py started sshd again at the image's menu.)
                img_setup || { bad "the rescuer's restart after partition (the rescue image's repository disk: $(tail -n 1 "$R/logs/rescuer-img-setup.log"))"; vm_down "$rkey"; return; }
                put "$rkey" "$R/dumps/src.json" $RH/layout.json
            else
                put "$rkey" "$HOLT" $RH/bko-holt
            fi
        fi
        sshk "$rkey" "$RHOLT load $RH/layout.json ${map[*]} --only status" > "$R/logs/rebuild-status-after-$s.txt" 2>&1
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
    [[ -z $geli$sr ]] || geli_key_rm "$rkey"
    vm_down "$rkey"
    rm -f "$try" "$R/img.qcow2" "$R/img-key" "$R/img-key.pub"
}
# HT_KEEP=1: the rescuer stays up, as the stage left it; else it goes down.
kept_or_down() {
    if [[ ${HT_KEEP:-} == 1 ]]; then
        say "rescuer kept up: ssh -i $rkey -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $GUSER@127.0.0.1 (load: $RHOLT load $RH/layout.json ...)"
    else
        vm_down "$rkey"
    fi
}

# mtree's report, but for what the boot stage writes.
BOOT_STAGE_RE='^(extra: |missing: )?(\./)?(var/log/bko-holt|var/log|boot/zfs/zpool\.cache|etc/zfs/zpool\.cache|boot/zfs|etc/zfs|\.sujournal)( |:|/|$)'

phase_verify() {
    # A rescuer HT_KEEP left up has the targets open.
    [[ -z $(ht_qemus "$R") ]] || vm_down "$rkey"
    say "== verify"
    # GELI, as the backup's: the rescuer boots through console-boot.py and
    # attaches the restored providers with the passphrase load gave them.
    # load rewrites /var/backups/PROVIDER.eli for the new provider and
    # removes a stale one (the design's D5): mtree may see those change.
    local geli sr ck="" boot_re=$EXTRA_BOOT_RE
    geli=$(geli_provs "$R/dumps/src.json" 2>/dev/null) || geli=""   # (no layout: the mount script says so)
    sr=$(sr_vols "$R/dumps/src.json" 2>/dev/null) || sr=""
    if [[ -n $geli ]]; then
        ck=$gpass
        boot_re+='|^(extra: |missing: )?(\./)?var/backups(/[^/ ]+\.eli)?( |:|$)'
    fi
    # softraid: the rescuer's attach and detach (bioctl -d rewrites the
    # volume's flags, clearing the bootable one installboot set) would leave
    # the restored disk unbootable, so its rescuer runs on --snapshot.
    local vsnap=("${v_snap[@]}")
    [[ -n $sr ]] && { ck=$gpass; vsnap=(--snapshot); }
    if [[ -f $RESC ]]; then
        CONSOLE_KEY=$ck vm_up "$RESC" "$R/logs/verify-rescuer-serial.log" "${vsnap[@]}" "${rfw[@]}" --memory 3072 "${s_resc[@]}" "${all_dst[@]}"
        if vm_wait "$rkey"; then
            rescuer_ready $((1 + n))
            if [[ -n $geli$sr ]] && ! geli_key_put "$rkey"; then
                bad "the GELI or softraid passphrase into the rescuer's tmpfs $GELI_DIR"
                echo exit 1 > "$R/logs/verify-mount.sh"
            elif ! mount_script "$R/dumps/src.json" 1 /mnt/dst > "$R/logs/verify-mount.sh" 2> "$R/logs/verify-mount.log"; then
                echo exit 1 > "$R/logs/verify-mount.sh"
            fi
            if shk "$rkey" < "$R/logs/verify-mount.sh" > "$R/logs/verify-mount.log" 2>&1; then
                grep '^HT1-POOL ' "$R/logs/verify-mount.log" | while read -r _ pn rest; do ok "the restored pool $pn imported from all its disks ($rest)"; done
                grep '^HT1-SR ' "$R/logs/verify-mount.log" | while read -r _ vn rest; do ok "the restored softraid volume $vn attached with the passphrase load gave it ($rest)"; done
                if [[ $PLAT == haiku || $PLAT == illumos ]]; then
                    TMO=3600 sshk "$rkey" "$(list_tree /mnt/dst)" > "$R/dst.listing" 2>/dev/null
                    diff "$R/src.listing" "$R/dst.listing" | sed -n 's/^< /missing: /p; s/^> /extra: /p' > "$R/logs/fsmeta-compare.txt"
                else
                    put "$rkey" "$R/src.listing" $RH/src.mtree
                    TMO=3600 sshk "$rkey" "cd /mnt/dst && mtree -f $RH/src.mtree -p /mnt/dst" > "$R/logs/fsmeta-compare.txt" 2>&1
                fi
                local diffs; diffs=$(grep -vE "$BOOT_STAGE_RE" "$R/logs/fsmeta-compare.txt" | grep -vE "$boot_re" | grep -vE "$KNOWN_RE" | grep -vE '^[[:space:]]' | grep -c .)
                local known; known=$(grep -cE "$KNOWN_RE" "$R/logs/fsmeta-compare.txt")
                if [[ $diffs == 0 ]]; then
                    ok "mtree: the files agree but for the boot stage's ($(grep -c . "$R/logs/fsmeta-compare.txt") lines of it)$([[ $known -gt 0 ]] && echo "; known archiver limit: the volume root's mtime ($known lines)")"
                else
                    bad "mtree: $diffs differences: $(grep -vE "$BOOT_STAGE_RE" "$R/logs/fsmeta-compare.txt" | grep -vE "$boot_re" | grep -vE "$KNOWN_RE" | grep -vE '^[[:space:]]' | head -5 | tr '\n' ' ')"
                fi
            else
                bad "mount the restored disk in the rescuer (logs/verify-mount.log)"
            fi
            umount_script "$R/dumps/src.json" /mnt/dst | shk "$rkey" >> "$R/logs/verify-mount.log" 2>&1
            [[ -z $geli$sr ]] || geli_key_rm "$rkey"
        else
            bad "rescuer boots for the mtree comparison"
        fi
        vm_down "$rkey"
    fi
    # (GELI: SSH answering means the loader took the passphrase load gave
    # the restored provider, and the kernel mounted the pool inside it.)
    CONSOLE_KEY=$gpass vm_up "$DST" "$R/logs/restored-serial.log" --snapshot "${fw[@]}" "${s_guest[@]}" "${dst_more[@]}"
    if ! vm_wait "$gkey"; then bad "the restored disk boots by itself (no SSH; logs/restored-serial.log)"; vm_down "$gkey"; return; fi
    ok "the restored disk boots by itself, SSH with the guest's key"
    # (softraid: SSH answering means /boot took the passphrase load gave the
    # volume.)  Each volume as the source had it: its device, CRYPTO, on its
    # chunk, by bioctl in the restored system.
    if [[ -n $sr ]]; then
        local vn chunks _r
        while read -r vn chunks _r; do
            sshk "$gkey" "bioctl $vn" > "$R/logs/restored-bioctl-$vn.txt" 2>&1
            if awk -v v="$vn" '$0 ~ ("^softraid0 ") && $(NF-1) == v && $NF == "CRYPTO" { f = 1 } END { exit !f }' "$R/logs/restored-bioctl-$vn.txt" &&
                grep -qF "<$chunks>" "$R/logs/restored-bioctl-$vn.txt"; then
                ok "bioctl $vn: CRYPTO on $chunks, as the source's"
            else
                bad "bioctl $vn: not CRYPTO on $chunks (logs/restored-bioctl-$vn.txt: $(tr -s ' \n' ' ' < "$R/logs/restored-bioctl-$vn.txt"))"
            fi
        done <<<"$sr"
    fi
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
((IMG)) && say "rebuilt in the rescue image $IMG_PIN/rescuer.img (+img)"
if ((IMG)); then ht_manifest "$rimg" "$IMG_PIN/rescuer.img" "$HERE/img-boot.py" "$HERE/img-boot.d/$PLAT.json" "$T/../stick/boot-test.py"
else ht_manifest "$rimg"; fi
for p in "${PHASES[@]}"; do ht_phase_start "$p"; "phase_$p"; ht_phase_end "$p"; done
ht_summary
if ((fails == 0)); then say "RESULT: PASS"; exit 0; fi
say "RESULT: FAIL ($fails${IMG_HARNESS:+; harness: $IMG_HARNESS})"
exit 1
