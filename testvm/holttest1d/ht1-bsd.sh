#!/usr/bin/env bash
# HoltTest1x for a guest that isn't Linux (README: "Systems other than
# Linux"): ht1.sh hands such guests here.  Run as root on a test host:
#
#   ht1-bsd.sh GUEST RUN [PHASE...]    PHASEs: source backup rebuild verify
#
# The same round trip as ht1.sh, through bko-holt and rustic-bko built for
# the guest's own system (~/backotter-work/holt-bin/PLATFORM/bko-holt,
# checked against its SHA256SUMS, or HT_HOLT_BIN/PLATFORM/bko-holt or
# HT_HOLT, as in ht1.sh; and ~/backotter-work/bko-bin/PLATFORM/rustic-bko),
# with a rescuer of that system: a throwaway copy of another of its guests,
# whose own disk is first, so that no pool, label or volume name of its own
# is the source's.
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
HERE=$(dirname "$(realpath "$0")")
T=$(dirname "$HERE")   # this copy's testvm/ (vm-run, vm-lib, compare/), as ht1.sh's
[[ -r $HERE/ht1-lib.sh ]] || { echo "ht1-bsd: no ht1-lib.sh beside $0" >&2; exit 2; }
# shellcheck source=ht1-lib.sh
. "$HERE/ht1-lib.sh"
VARIANT=${HT_VARIANT:-}
# t2-only: json
# "json" (docs/design/holt-json.md, "Testing"; RT10j): ht1.sh's, a plain run
# whose rebuild and verify run again with --json (ht1-json.sh), in
# GUEST-json.  (No Test2 cell's: these blocks do nothing in any other run.)
JSON_RUN=""
[[ $VARIANT != json ]] || JSON_RUN=1 VARIANT=""
# t2-only-end
# t2-only: espfmt
# "espfmt" (H7-09b's proof, 833e96b; testvm/audit/rust/findings.tsv): an
# +img run whose load reads the dump with each imaged ESP left out (no
# esp_image, esp_image_too_big set, as holt's golden_plans.rs espfmt()
# writes GUEST+espfmt), so load's format stage makes the FAT with the rescue
# image's own mkfs (illumos mkfs -F pcfs, Haiku mkfs -t fat) and fat-set, in
# GUEST-espfmt.  The source's dump itself, which judge.py reads, is
# untouched.  (No Test2 cell's: by hand only.)
ESPFMT=""
[[ $VARIANT != espfmt ]] || ESPFMT=1 VARIANT=img
# t2-only-end
case $VARIANT in ""|bigger|oneshot|img) ;; *) echo "HT_VARIANT: bigger, oneshot or img" >&2; exit 2 ;; esac
GV=$G${VARIANT:+-$VARIANT}
# t2-only: json
[[ -z $JSON_RUN ]] || GV=$G-json
[[ -z $ESPFMT ]] || GV=$G-espfmt
# t2-only-end
PORT=${HT_PORT:-2690}
export SSH_PORT=$PORT
fails=0
say() { echo "$*" | tee -a "$R/report.txt"; }
ok() { say "PASS $*"; }
bad() { say "FAIL $*"; fails=$((fails + 1)); }
# harness WHY: a FAIL of the harness itself, not of bko-holt (a read of the
# rescuer that never came back, say).  The run carries on, so its first reason
# goes on the RESULT line, which classes the run HARNESS, not FAIL (rows_holt),
# only when every FAIL of the run was one: a real FAIL beside it stays a FAIL
# and is rerun.
hfails=0 HARNESS_WHY=""
harness() { bad "$*"; hfails=$((hfails + 1)); HARNESS_WHY=${HARNESS_WHY:-$*}; }
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
        # (After /etc/rc is done, "sh /etc/rc autoboot" as init starts it:
        # sshd starts before rc's local daemons, cron among them, so a check
        # as soon as SSH answers can find cron not started yet.  So it did
        # on openbsd8-crypt's source, whose image hadn't booted since its
        # install: rc.firsttime's fw_update held rc up, runs obc1, obc2.
        # reorder_kernel, which rc leaves running, is its own ksh.)
        BOOTSTATE='i=0; while pgrep -f "^(/bin/)?sh /etc/rc( |\$)" >/dev/null && [ $i -lt 150 ]; do sleep 2; i=$((i + 1)); done; rcctl ls failed | sed "s/^/down /" | sort'
        # installboot -r puts the rescue system's /boot (the same release's).
        EXTRA_BOOT_RE='^(extra: |missing: )?(\./)?boot( |:|$)' ;;
    omnios*|oi*)
        # The rescuer is a copy of the guest (of omnios58 for OpenIndiana's
        # too, as the illumos image is OmniOS-based): its own pool is rpool
        # too, so load makes the target's under another name until close,
        # and this script imports the source's and target's as ht1src.
        PLAT=illumos RG=omnios58 DISK= RAWSFX=
        GPATH=/usr/gnu/bin:/usr/sbin:/usr/bin:/sbin:/opt/ooce/bin
        # Settled: nothing in transition (*) and nothing enabled still
        # offline, waiting on its dependencies (a first boot's NWAM can hold
        # milestone/network and autofs back well after SSH answers), or 120 s.
        BOOTSTATE='i=0; while svcs -H -o state | grep -q -e "\*" -e "^offline" && [ $i -lt 60 ]; do sleep 2; i=$((i + 1)); done; svcs -H -o state,fmri | awk "\$1 != \"online\" && \$1 != \"disabled\" {print \"down \" \$2}" | sort'
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
IMG_HARNESS=""   # the pin's change, a harness failure's why for the RESULT line (rows_holt: HARNESS)
# What the load runs in the rescuer, and where it finds the repository (+img:
# the image's own binaries; the repository on its disk, Haiku's /boot/home
# being that volume's /home).
RHOLT=$RH/bko-holt RARCH=$RH/rustic-bko RREPO=$RH
((IMG)) && RHOLT=$IMG_BIN/bko-holt RARCH=$IMG_BIN/rustic-bko RREPO=$IMG_RR${RH#/boot}
# t2-only: json
if [[ -n $JSON_RUN ]]; then
    JDRV=bsd JLAYOUT=$RH/layout.json JKEYS=$RH/ht1-keys JREPO_PW=$RREPO/repo-password JROOT=(dst.listing)
    # shellcheck source=ht1-json.sh
    . "$HERE/ht1-json.sh"
fi
# t2-only-end
# The rescuer's disks' names, its own first (RN, set by rescuer_ready once
# it's up: Haiku numbers them by the controller's ports); rdev N is the Nth's
# device, rraw N the node that reads it whole.
RN=(); for i in 0 1 2 3 4 5 6 7; do RN+=("$DISK$i"); done
# rescuer_ready WANT: the rescuer's disks named (RN), WANT of them.  1 when
# they couldn't all be named, RR_WHY saying what was seen (the caller's
# FAIL line): a short RN would end the run at its first ${RN[i]} (set -u),
# with no FAIL line.
RR_WHY=""
rescuer_ready() {
    RR_WHY=""
    # NetBSD's heartbeat(9) panics a CPU whose clock didn't move in 15 s of
    # its own ticks, as a vCPU's beside a starved one on a loaded host
    # (resc_up): off in the rescuer for the rest of its run.  Since
    # 2026-11-25 a NetBSD rescuer boots with one vCPU (resc_up's --cpus 1),
    # with no other CPU to fall behind, so this is a second guard that costs
    # nothing, for a rescuer someone boots with more.  (Nothing the
    # rig passes turns it off sooner: kern.heartbeat.max_period is a sysctl
    # only, its 15 s default a kernel option, and boot.cfg sets neither.
    # The one place earlier is the rescuer guest's own /etc/sysctl.conf,
    # which rc.d/sysctl reads before the network: netbsd11 2026-11-18
    # panicked after it, at "Building databases", the "[ 2.36]" stamps
    # being that stalled clock's, not 2 s of boot.  That is an image change,
    # hence the one vCPU, and resc_up's one boot again for any other panic
    # before SSH answers.)
    [[ $PLAT == netbsd ]] && sshk "$rkey" 'sysctl -w kern.heartbeat.max_period=0' >/dev/null 2>&1
    if [[ $PLAT == illumos ]]; then
        # (A disk attached at boot can be late into /dev and into iostat's
        # kstats: devfsadm, and wait until diskinfo lists every one and, by
        # serial, each is named.  copland 2026-11-19, omnios58-mirror's
        # rebuild rescuer, SSH up 7 s after its login prompt: diskinfo
        # listed WANT disks, iostat -En one serial short, RN had 3 of 4 and
        # ${RN[D]} (the decoy's) ended the run with no FAIL line.)
        # By their serials (s_resc, s_decoy): the rescuer's own, the
        # guest's disks in order, the decoy.  The rescue image (+img's
        # rebuild, bkoimg) is its own disk, and the rescuer guest's disk
        # (bkoresc, the repository's) comes after the decoy.
        local want=$1 out dl by i d try
        for try in $(seq 30); do
            ((try == 1)) || sleep 2
            out=$(sshk "$rkey" 'devfsadm -c disk >/dev/null 2>&1; diskinfo -Hp | cut -f2; echo HT1-IOSTAT; iostat -En')
            dl=$(sed '/^HT1-IOSTAT$/,$d' <<<"$out")
            by=$(sed '1,/^HT1-IOSTAT$/d' <<<"$out" | awk '/^c[0-9]/ {d = $1} /Serial No:/ {s = $0; sub(/.*Serial No: */, "", s); sub(/ .*/, "", s); print s, d}')
            mapfile -t RN < <(grep . <<<"$dl")
            if grep -q '^bkoresc ' <<<"$by"; then
                local order=(bkoresc) last=()
                grep -q '^bkoimg ' <<<"$by" && order=(bkoimg) last=(bkoresc)
                for ((i = 0; i < n; i++)); do order+=("bkodisk-$i"); done
                order+=(bkodecoy "${last[@]}")
                RN=()
                for i in "${order[@]}"; do
                    d=$(awk -v s="$i" '$1 == s {print $2}' <<<"$by")
                    [[ -n $d ]] && RN+=("$d")
                done
            fi
            (( ${#RN[@]} >= want )) && break
        done
        { echo "rescuer_ready $want, try $try: diskinfo $(grep -c . <<<"$dl"): $(tr '\n' ' ' <<<"$dl"); serials: $(tr '\n' ',' <<<"$by"); RN: ${RN[*]}"; } >> "$R/logs/rescuer-disks.txt"
        (( ${#RN[@]} >= want )) && return 0
        RR_WHY="$want disks wanted, ${#RN[@]} named after $try tries (diskinfo listed $(grep -c . <<<"$dl"); serials: $(grep . <<<"$by" | tr '\n' ',' | sed 's/,$//'); logs/rescuer-disks.txt)"
        return 1
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
# bko-holt: HT_HOLT's, else HT_HOLT_BIN's set's DIR/PLATFORM/bko-holt, else
# the promoted holt-bin's, vouched for by its SHA256SUMS (ht_holt_pick).
HOLT=""; ht_holt_pick "$PLAT"
gdir=$W/vms/$G gkey=$gdir/id_ed25519
rdir=$W/vms/$RG rkey=$rdir/id_ed25519
# GELI (docs/design/geli.md, freebsd15-geli): the loader asks for the
# passphrase in console-passphrase, so the source and the restored disks boot
# through console-boot.py.  In the rescuer it is GELI_KEY, in a tmpfs of its
# own (GELI_DIR: a default install's /tmp is on its disk),
# for geli attach -j and load --key-file.
# Key slot 1 (docs/design/geli2.md, freebsd15-geli-ufs): a second passphrase
# in console-passphrase-slot1, beside GELI_KEY as GELI_KEY1, for load's
# --key-file NAME:1=FILE and the verify's read-only attach of that slot.
gpass=""; [[ -f $gdir/console-passphrase ]] && gpass=$gdir/console-passphrase
gpass1=""; [[ -f $gdir/console-passphrase-slot1 ]] && gpass1=$gdir/console-passphrase-slot1
GELI_DIR=/tmp/ht1-geli GELI_KEY=/tmp/ht1-geli/passphrase GELI_KEY1=/tmp/ht1-geli/passphrase-slot1
GELI_WHY=""   # geli_key_put's own reason, for the caller's FAIL line
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
# t2-only: json
[[ -z $JSON_RUN ]] || HT_ZIP+=(dst-json.listing)
# t2-only-end
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
# The watchdog (ht1-lib.sh's ht_stage): the wait is its stage "wait prep
# lock", never stopped (the run that holds the lock has its own watchdog,
# and flock -w bounds the wait); the prep is its stage "prep", where the
# .part its QEMU writes, outside R, counts as progress (DragonFly's cpdup
# prints nothing); prep_unlock goes back to the phase's.
plw=""
prep_lock() {
    ht_stage "wait prep lock"
    { exec {plk}>>"/bkoscratch/holttest1d/rescuer/.$RG-prep.lock" && { flock -w 7200 "$plk" & plw=$!; wait "$plw"; }; }
    local s=$?; plw=""
    ((s == 0)) || { echo "ht1-bsd: no lock for $prep" >&2; exit 1; }
    ht_stage prep "$prep.part"
}
prep_unlock() { exec {plk}>&-; ht_stage "$HT_PHASE"; }
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
    prep_unlock
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
    prep_unlock
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
    prep_unlock
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
# root shell; sh is everywhere).  SSH_CTO (ht1-lib.sh, 30 s) is ssh's
# ConnectTimeout, which covers the TCP connect, the banner and the key
# exchange: 5 s was too short for a guest busy just after a big write
# (copland 2026-11-19, hardenedbsd15-zfs: the source's listing, the first ssh
# after the 587M backup, closed preauth 5 s in, ssh's silent 255, an empty
# src.listing, and the verify's mtree against it read the whole restored tree
# as "extra: .").  vm_wait's polls keep their own TMO=8, so a guest not up yet
# costs no more.
sshk() { local key=$1; shift; printf 'PATH=%s; export PATH\n%s\n' "$GPATH" "$*" | timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=$SSH_CTO -o ServerAliveInterval=15 "$GUSER@127.0.0.1" "/bin/sh -s"; }
# A script on stdin, for anything longer than a line.
shk() { local key=$1; { printf 'PATH=%s; export PATH\n' "$GPATH"; cat; } | timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=$SSH_CTO -o ServerAliveInterval=15 "$GUSER@127.0.0.1" "/bin/sh -s"; }
# put KEY FILE DEST: FILE copied to DEST (mode 755), with sshk's
# ConnectTimeout, and once more after 3 s if ssh itself failed (255: no
# answer in time, or a session cut off: the second cat > replaces whatever
# the first wrote).
put() { local key=$1 file=$2 dest=$3 i rc
    for i in 1 2; do
        [[ $i == 1 ]] || { echo "ht1-bsd: put $(basename "$file") to $dest: ssh failed (255), once more" >&2; sleep 3; }
        timeout 600 ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=$SSH_CTO "$GUSER@127.0.0.1" "cat > '$dest' && chmod 755 '$dest'" < "$file"; rc=$?
        ((rc == 255)) || return $rc
    done
    return $rc
}
# t2-only: espfmt
# (espfmt: the load's layout.json is src.json less its ESP images, made once.)
if [[ -n $ESPFMT ]]; then
    eval "put_plain() $(declare -f put | tail -n +2)"
    put() {
        local f=$2
        if [[ $f == "$R/dumps/src.json" && $3 == */layout.json ]]; then
            f=$R/dumps/src-espfmt.json
            [[ -s $f ]] || python3 -c '
import json, sys
d = json.load(open(sys.argv[1])); n = 0
for disk in d["storage"]["disks"]:
    for p in disk.get("partitions") or []:
        if "esp_image" in p:
            del p["esp_image"]; p["esp_image_too_big"] = True; n += 1
if not n:
    sys.exit("espfmt: the dump has no ESP image (esp_image) to leave out")
json.dump(d, open(sys.argv[2], "w")); print("espfmt: %d ESP image(s) left out of the load\x27s layout.json" % n)
' "$2" "$f" >> "$R/report.txt" 2>&1 || return 1
        fi
        put_plain "$1" "$f" "$3"
    }
fi
# t2-only-end
# edges_read KEY CMD FILE WANT: the disks' first and last MiB (CMD, which only
# reads) into FILE, WANT lines, one a disk.  sshk waits SSH_CTO seconds for the
# sshd to answer, and on a loaded host a slow one answers nothing at all, so an
# empty or short capture is not a disk that changed: try again a few times.
# (WANT or more lines: a platform that prints a warning of its own still counts).
# 1: it still couldn't be read, which is the harness's problem, not a write.
edges_read() {
    local key=$1 cmd=$2 file=$3 want=$4 i
    for i in 1 2 3; do
        [[ $i == 1 ]] || sleep 3
        sshk "$key" "$cmd" > "$file" 2>&1 && [[ $(grep -c . "$file") -ge "$want" ]] && return 0
    done
    return 1
}
# head_written KEY RAW: 0 the first 34 sectors of RAW hold a byte that isn't
# zero (something was written there), 1 they are all zero (grep's own "no
# match": nothing was written), 2 the read didn't come back (ssh's 255,
# timeout's 124, a shell that couldn't run it), tried three times.
head_written() {
    local key=$1 raw=$2 i rc
    for i in 1 2 3; do
        [[ $i == 1 ]] || sleep 3
        sshk "$key" "dd if=$raw bs=512 count=34 2>/dev/null | od -An -v | tr -d ' 0\n' | grep -q ." >/dev/null 2>&1
        rc=$?
        ((rc == 0 || rc == 1)) && return $rc
    done
    return 2
}
# (Without ht_begin's copies of the caller's stdout and stderr: a rescuer
# HT_KEEP keeps up outlives the run, and its caller's ssh or pipe.)
vm_up() { local image=$1 log=$2; shift 2
    # (A console left from an earlier try, until QEMU or the console's
    # reader opens LOG anew, would be read by vm_wait's log_panicked.)
    rm -f "$log"
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
# vm_wait KEY [LOG]: 0 sshd answers, 1 it didn't in ~26 minutes, 2 (with
# LOG, the VM's console) its kernel panicked first (log_panicked).
vm_wait() { local key=$1 log=${2:-}
    for _ in $(seq 120); do
        TMO=8 sshk "$key" true 2>/dev/null && return 0
        [[ -n $log ]] && log_panicked "$log" && return 2
        sleep 5
    done
    return 1
}
# 0: the console at LOG has a kernel's panic line: "panic: " at a line's
# start (FreeBSD, OpenBSD, DragonFly; NetBSD's after its "[ 2.36] "
# timestamp), illumos' "panic[cpu1]/thread=ADDR: ".  Not "ddb.onpanic: "
# nor savecore's "reboot after panic: ", which come after a line's start.
# Haiku's: its kernel debugger's "Welcome to Kernel Debugging Land..." (after
# a "PANIC: " line, or any other way into KDL), where it then waits.
log_panicked() { # LOG
    [[ -f $1 ]] && tr -d '\r' < "$1" | grep -aqE '^(\[ *[0-9.]+\] )?panic(\[cpu[0-9]+\]/thread=[0-9a-fx]+)?: |^Welcome to Kernel Debugging Land'
}
# wait_why RC: what a FAIL line says of vm_wait's RC (1 or 2).
wait_why() {
    if (($1 == 2)); then echo "its kernel panicked before SSH answered"; else echo "no SSH"; fi
}
# A rescuer's boot: vm_up IMAGE LOG ARGS... and vm_wait KEY, once more when
# its kernel panicked before sshd answered (the first console kept as
# LOG.panic-1, a note in the report).  The rescuer isn't what the run tests,
# and a loaded host panics a guest whose vCPU it starved: netbsd11's backup
# rescuer, 2026-11-18 on Chopin at 91 C, "cpu1: time has not advanced in 1501
# heartbeats" in /etc/rc, then a hang in its crash dump until vm_wait gave
# up 25 minutes later.  So a NetBSD rescuer (the backup's, the decoy's or
# +img's, the verify's) boots with one vCPU since 2026-11-25: heartbeat(9)
# panics a CPU whose clock fell 15 s behind, and with one there is no other
# CPU to fall behind.  It fires in /etc/rc, before rescuer_ready's sysctl
# could turn it off.  The cost is a slower rescuer; the second boot stays,
# for the panics one vCPU doesn't prevent.  --cpus 1 goes last, after the
# rescuer guest's vm-run-options (rfw), as vm-run's last --cpus wins.  The
# source's and the restored disk's boots are what the run tests, so they
# don't come here and keep their CPUs.  (CONSOLE_KEY and IMG_BOOT, set on
# this call, reach vm_up as they would on its own.)
resc_up() { local key=$1 image=$2 log=$3 rc; shift 3
    [[ $PLAT == netbsd ]] && set -- "$@" --cpus 1
    vm_up "$image" "$log" "$@"
    vm_wait "$key" "$log"; rc=$?
    ((rc == 2)) || return $rc
    ht_note "the rescuer's kernel panicked at boot, booted again ($(basename "$log").panic-1: $(tr -d '\r' < "$log" | grep -aiE -m 1 '^(\[ *[0-9.]+\] )?panic' | sed 's/^\[ *[0-9.]*\] //' | cut -c1-120))"
    # shellcheck disable=SC2046  # PIDs
    kill $(ht_qemus "$R") 2>/dev/null
    for _ in $(seq 12); do [[ -n $(ht_qemus "$R") ]] || break; sleep 5; done
    # shellcheck disable=SC2046
    [[ -z $(ht_qemus "$R") ]] || { kill -9 $(ht_qemus "$R") 2>/dev/null; sleep 2; }
    mv -f "$log" "$log.panic-1"
    vm_up "$image" "$log" "$@"
    vm_wait "$key" "$log"; rc=$?
    ((rc == 2)) && echo "ht1-bsd: the rescuer's kernel panicked at boot again ($log)" >&2
    return $rc
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

# A check whose tool is missing never ran: "MISSING-TOOL name", which the
# source can't excuse.
HEALTH='f=0; no() { echo "FAIL $*"; f=1; }; gone() { echo "MISSING-TOOL $1"; f=1; }
m=0; for t in $(awk "\$1 !~ /^#/ && \$2 ~ /^\// && \$3 != \"swap\" && \$4 !~ /noauto/ {print \$2}" /etc/fstab); do mount | sed -e "s/ type [^ ]* (.*//" -e "s/ (.*//" -e "s/^.* on //" | grep -qx "$t" && m=$((m + 1)) || no "fstab mount $t not mounted"; done; echo "$m fstab mounts mounted"
if grep -qE "^[^#].*[[:space:]]swap[[:space:]]" /etc/fstab; then [ "$(swapctl -l | grep -c /dev/)" -gt 0 ] && echo "swap on" || no "no swap"; fi
if command -v zpool >/dev/null && [ -n "$(zpool list -H -o name 2>/dev/null)" ]; then zpool status -x | grep -q "all pools are healthy" && echo "zpool healthy" || no "zpool status -x: $(zpool status -x | head -3 | tr "\n" " ")"; elif mount | grep -qE "\\(zfs[,)]| type zfs "; then command -v zpool >/dev/null && no "ZFS mounted, zpool lists no pool" || gone zpool; fi
ls /var/log/bko-holt/load.log >/dev/null 2>&1 && echo "load log kept" || no "/var/log/bko-holt/load.log missing"
exit $f'

# illumos has /etc/vfstab, not fstab: mount point $3, type $4, mount at boot
# $6; `mount -p` lists what's mounted in that format.
if [[ $PLAT == illumos ]]; then
    HEALTH='f=0; no() { echo "FAIL $*"; f=1; }; gone() { echo "MISSING-TOOL $1"; f=1; }
m=0; for t in $(awk "\$1 !~ /^#/ && \$3 ~ /^\// && \$4 != \"swap\" && \$6 == \"yes\" {print \$3}" /etc/vfstab); do mount -p | awk "{print \$3}" | grep -qx "$t" && m=$((m + 1)) || no "vfstab mount $t not mounted"; done; echo "$m vfstab mounts mounted"
if awk "\$1 !~ /^#/ && \$4 == \"swap\"" /etc/vfstab | grep -q .; then [ "$(swap -l 2>/dev/null | grep -c /dev/)" -gt 0 ] && echo "swap on" || no "no swap"; fi
if command -v zpool >/dev/null && [ -n "$(zpool list -H -o name 2>/dev/null)" ]; then zpool status -x | grep -q "all pools are healthy" && echo "zpool healthy" || no "zpool status -x: $(zpool status -x | head -3 | tr "\n" " ")"; elif mount -p | awk "\$4 == \"zfs\"" | grep -q .; then command -v zpool >/dev/null && no "ZFS mounted, zpool lists no pool" || gone zpool; fi
ls /var/log/bko-holt/load.log >/dev/null 2>&1 && echo "load log kept" || no "/var/log/bko-holt/load.log missing"
exit $f'
fi

if [[ $PLAT == haiku ]]; then
    HEALTH='f=0; no() { echo "FAIL $*"; f=1; }
df | grep -q "/dev/disk/" && echo "boot volume mounted" || no "no boot volume"
ls /boot/system/var/log/bko-holt/load.log >/dev/null 2>&1 && echo "load log kept" || no "load log missing"
exit $f'
fi

# The layout's GELI providers, one line each, five fields: the partition (or
# disk) that holds one, the source's provider name less .eli (load's
# --key-file NAME), how it is keyed, the key slots in use (comma-separated,
# "0" when the record says nothing) and the key files that open it
# (comma-separated paths in the source's own tree, "-" for none).  The one
# reader of the dump's GELI records, where their shape may still move: a
# content of type geli, or a geli object on the partition or in its content;
# the provider from geli.provider, else PARTITION.eli; keyed_by passphrase
# when it says nothing.
geli_provs() { # LAYOUT
    python3 - "$1" <<'PY'
import json, re, sys
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
        keys = [k for k in (g.get("keys") or []) if isinstance(k, int)]
        kfs = [k for k in (g.get("keyfiles") or []) if isinstance(k, str)]
        for k in kfs:
            # The fields are white-space separated and the paths comma-separated:
            # a path with either in it would be read as another field.
            if re.search(r"[,\s]", k):
                sys.exit(f"ht1-bsd: the GELI key file {k!r} on {x['name']}: a comma or white space in its path, which the harness can't pass on")
        print(x["name"], prov.removesuffix(".eli"), g.get("keyed_by") or "passphrase",
              ",".join(str(k) for k in keys) or "0", ",".join(kfs) or "-")
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
# GELI being geli_provs' lines, a provider with key slot 1 in use also takes
# console-passphrase-slot1, beside it as GELI_KEY1.  Why it couldn't goes in
# GELI_WHY, for the caller's FAIL line.
geli_key_put() { # KEY [GELI]
    local want1=0
    GELI_WHY=""
    if [[ -n ${2:-} ]] && awk '{ n = split($4, s, ","); for (i = 1; i <= n; i++) if (s[i] == "1") f = 1 } END { exit !f }' <<<"$2"; then
        want1=1
    fi
    [[ -n $gpass ]] || {
        GELI_WHY="$gdir has no console-passphrase"
        echo "ht1-bsd: the layout has GELI providers or softraid CRYPTO volumes, and $GELI_WHY" >&2; return 1; }
    ((want1 == 0)) || [[ -n $gpass1 ]] || {
        GELI_WHY="$gdir has no console-passphrase-slot1"
        echo "ht1-bsd: the layout's GELI key slot 1 is in use, and $GELI_WHY" >&2; return 1; }
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
    sshk "$1" "[ -s $GELI_KEY ] && [ \"\$(stat -f %Lp $GELI_KEY)\" = 600 ]" || {
        GELI_WHY="$GELI_KEY in the rescuer is empty or not mode 600"; return 1; }
    ((want1)) || return 0
    # Slot 1's, beside it: load's --key-file NAME:1=FILE and the verify's
    # geli attach -n 1 -j.  Never slot 0's file for slot 1, nor the other
    # way round.
    python3 -c 'import sys; sys.stdout.buffer.write(open(sys.argv[1], "rb").read().rstrip(b"\n"))' "$gpass1" |
        timeout 60 ssh -q -i "$1" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$GUSER@127.0.0.1" "umask 077; cat > $GELI_KEY1" || {
            GELI_WHY="$GELI_KEY1 couldn't be written in the rescuer"; return 1; }
    sshk "$1" "[ -s $GELI_KEY1 ] && [ \"\$(stat -f %Lp $GELI_KEY1)\" = 600 ]" || {
        GELI_WHY="$GELI_KEY1 in the rescuer is empty or not mode 600"; return 1; }
}
geli_key_rm() { sshk "$1" "rm -f $GELI_KEY $GELI_KEY.nl $GELI_KEY1; umount $GELI_DIR 2>/dev/null; rmdir $GELI_DIR 2>/dev/null; true" >/dev/null 2>&1; }   # KEY

# The mount point the layout's fstab puts on PART's GELI provider (geli2.md's
# /data on vtbd0p4.eli), or nothing.  Swap is none: it has no mount point.
geli_fstab_mp() { # LAYOUT PART
    python3 - "$1" "$2" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); part = sys.argv[2]
for e in (d.get("storage") or {}).get("fstab") or []:
    if e.get("resolves_to") == part and e.get("type") != "swap" \
            and (e.get("eli") or str(e.get("spec") or "").endswith(".eli")) \
            and str(e.get("file") or "").startswith("/"):
        print(e["file"])
        break
PY
}
# The devices (vtbd0p2) whose swap the dump marks onetime: GELI made at each
# boot by swapon, over a partition with no metadata on it, so load makes the
# partition and nothing else (geli2.md's freebsd15-geli-bios).
onetime_swaps() { # LAYOUT
    python3 - "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for w in (d.get("storage") or {}).get("swaps") or []:
    if w.get("onetime") and w.get("device"):
        print(w["device"])
PY
}
# The files on a file system at DIR and their SHA-256, for comparing a GELI
# provider's own contents with the source's (FreeBSD's sha256 -r prints
# "HASH path"); paths relative to DIR, so the source's and the restored
# system's listings compare whatever DIR they were read at.
geli_files_list() { # DIR
    echo "cd $1 && find . -type f -exec sha256 -r {} + | LC_ALL=C sort"
}
# Key slot 1 (geli2.md): each restored provider whose keys have 1 opened
# read-only with slot 1's passphrase alone and closed again -- a script for
# the verify's rescuer, one HT1-GELI-SLOT1 line a provider.  It runs before
# the mount script, which attaches with slot 0; a provider the rescuer's
# loader took is detached first, nothing being mounted from it yet.
geli_slot1_script() { # LAYOUT FIRST
    local geli; geli=$(geli_provs "$1") || return 1
    HT_GELI=$geli HT_GELI_KEY1=$GELI_KEY1 python3 - "$1" "$2" "${RN[@]}" <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1])); first = int(sys.argv[2]); rn = sys.argv[3:]
geli = [l.split() for l in os.environ.get("HT_GELI", "").splitlines() if l.strip()]
key1 = os.environ["HT_GELI_KEY1"]
# GELI is FreeBSD's, where a partition is its disk's name and a suffix:
# mount_script's node(), without the other systems' cases.
disks = [x["name"] for x in d["storage"]["disks"]]
out = []
for part, _, _, *rest in geli:
    if "1" not in (rest[0] if rest else "0").split(","):
        continue
    n = next((f"/dev/{rn[first + i]}{part[len(dn):]}" for i, dn in enumerate(disks) if part.startswith(dn)), None)
    if n is None:
        out.append(f"echo 'HT1-GELI-SLOT1 {part} no-node'")
        continue
    out.append(f"[ ! -c {n}.eli ] || geli detach {n}.eli || echo 'HT1-GELI-SLOT1 {part} busy'")
    out.append(f"if geli attach -r -n 1 -j {key1} {n}; then")
    out.append(f"  geli detach {n}.eli && echo 'HT1-GELI-SLOT1 {part} ok' || echo 'HT1-GELI-SLOT1 {part} no-detach'")
    out.append("else")
    out.append(f"  echo 'HT1-GELI-SLOT1 {part} no'")
    out.append("fi")
print("\n".join(out))
PY
}
# geli_slot1_script run in the rescuer: one ok or bad a provider whose keys
# have 1 (GELI being geli_provs' lines).
geli_slot1_check() { # LAYOUT FIRST KEY GELI
    local s log=$R/logs/verify-geli-slot1.log part slots
    s=$(geli_slot1_script "$1" "$2") || { bad "the GELI key slot 1 check's script (geli_slot1_script)"; return; }
    [[ -n $s ]] || return 0
    shk "$3" <<<"$s" > "$log" 2>&1
    while read -r part _ _ slots _; do
        case ,${slots:-0}, in *,1,*) ;; *) continue ;; esac
        if grep -qxF "HT1-GELI-SLOT1 $part ok" "$log"; then
            ok "slot 1 opens the restored $part, read-only, with its own passphrase"
        else
            bad "slot 1 opens the restored $part (logs/verify-geli-slot1.log: $(tr -s ' \n' ' ' < "$log" | tail -c 300))"
        fi
    done <<<"$4"
}

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
key0 = os.environ["HT_GELI_KEY"]
gdir = os.environ["HT_GELI_DIR"]
# The file systems this script mounts, in the order it mounts them: the
# fstab entries below, and / where a ZFS pool's boot environment goes.  A
# GELI provider opened by a key file waits for the one that holds the file.
zfs = (d["storage"].get("zfs") or {}).get("tools") or {}
fs = []
for e in d["storage"].get("fstab") or []:
    if e.get("resolves_to") and e["file"].startswith("/") and e["type"] not in ("swap",):
        fs.append(e)
fs.sort(key=lambda e: (0 if e["file"] == "/" else e["file"].count("/"), e["file"]))
mounts = (["/"] if zfs.get("imported_pools") else []) + [e["file"] for e in fs]
def holder(path):
    """The deepest of those mount points that holds PATH, or None."""
    return max((m for m in mounts if path == m or path.startswith(m.rstrip("/") + "/")), key=len, default=None)
def attach(part, keyed, kfs):
    """The lines that attach PART's provider read-only.  A key file's path is
    the source's own (/etc/geli/p4.key), so MP + it in the rescuer, its file
    system being mounted at MP + its own mount point."""
    n = node(part)
    ks = " ".join(f"-k {mp}{f}" for f in kfs)
    if keyed == "passphrase":
        how = f"-j {key0}"
    elif keyed in ("keyfile", "both") and not kfs:
        return [f"echo 'GELI on {part}: keyed by {keyed}, and the dump names no key file'; exit 1"]
    elif keyed == "keyfile":
        how = f"-p {ks}"
    elif keyed == "both":
        how = f"-j {key0} {ks}"
    else:
        return [f"echo 'GELI on {part}: keyed by {keyed}, which the run cannot open'; exit 1"]
    return [f"[ -c {n}.eli ] || geli attach -r {how} {n}",
            f"[ -c {n}.eli ] || {{ echo 'geli attach {n}: no {n}.eli'; exit 1; }}",
            f"echo {n}.eli >> {gdir}/attached"]
# GELI providers keyed by a passphrase first, so that the pool's member (or
# the UFS) is NODE.eli, as on the source; one already attached (the kernel,
# with the rescuer's loader's key) is left so.  Each goes in GELI_DIR's list,
# for umount_script's detach.  A provider whose key file is on another of the
# layout's file systems (geli2.md's /data on vtbd0p4, its key file on the
# encrypted root) is attached once that file system is mounted: pending below.
pending = []   # (part, keyed, key files, the mount point that holds them)
for part, _, keyed, *rest in geli:
    kfs = [] if len(rest) < 2 or rest[1] == "-" else rest[1].split(",")
    if keyed == "passphrase" or not kfs:
        # (No key file where one is wanted: attach() says so and stops.)
        out += attach(part, keyed, kfs)
        continue
    hs = {holder(f) for f in kfs}
    if len(hs) != 1 or None in hs:
        out.append(f"echo 'GELI on {part}: its key file ({', '.join(kfs)}) is not on one file system this script mounts'; exit 1")
        continue
    pending.append((part, keyed, kfs, hs.pop()))
def released(m):
    """The pending providers whose key file is on the file system just
    mounted at M, attached now."""
    lines = []
    for p in [x for x in pending if x[3] == m]:
        pending.remove(p)
        lines += attach(p[0], p[1], p[2])
    return lines
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
        # The boot environment's own children (OpenIndiana's /var:
        # canmount=noauto, mounted with it at boot), as load mounts them:
        # those mounted when the dump was made.  zfs mount -a skips them.
        kids = sorted((ds for ds in zfs.get("datasets") or []
                       if (ds.get("name") or "").startswith(p["properties"]["bootfs"] + "/") and ds.get("canmount") == "noauto"
                       and ds.get("mounted") == "yes" and (ds.get("mountpoint") or "").startswith("/")),
                      key=lambda ds: (ds["mountpoint"].count("/"), ds["mountpoint"]))
        for ds in kids:
            n = (alias + ds["name"][len(p["name"]):]) if alias else ds["name"]
            out.append(f"zfs mount {'-O ' if plat == 'illumos' else ''}{n}")
    if alias:
        out.append(f"zfs mount -O {alias} || true")
if zfs.get("imported_pools"):
    # (Read-only, a dataset whose mount point isn't there can't mount: the
    # rest do.)
    out.append("zfs mount -a || true")
    out += released("/")
for e in fs:
    t = {"ufs": "ufs", "msdosfs": "msdosfs"}.get(e["type"], e["type"])
    target = mp + ("" if e["file"] == "/" else e["file"])
    n = node(e["resolves_to"])
    if (e.get("eli") or e["spec"].endswith(".eli")) and not n.endswith(".eli"):
        n += ".eli"   # a UFS on GELI (resolve() gives the partition, eli: true)
    if e["type"] == "hammer2" and "@" in e["spec"]:
        n += "@" + e["spec"].split("@", 1)[1]   # the same PFS (DragonFly)
    # A file system on a provider still waiting for its key file: the mount
    # would come before the attach, so the layout can't be mounted in order.
    waiting = next((p for p in pending if n == node(p[0]) + ".eli"), None)
    if waiting:
        here = e["file"]
        out.append(f"echo 'GELI on {waiting[0]}: {here} is on it, and its key file is under {waiting[3]}, which this script mounts later'; exit 1")
        continue
    if plat == "haiku":
        # Mounted at boot before mount_server was stopped: unmounted first.
        out.append(f"for m in $(df | sed 's/\\x1b\\[[0-9;]*m//g' | awk -v d={n} '{{ if ($NF == d) print last; last = $1 }}'); do unmount \"$m\"; done")
        out.append(f"mkdir -p {target}; mount -ro -t bfs {n} {target}")
        out += released(e["file"])
        continue
    out.append(f"mkdir -p {target} 2>/dev/null || true; mount -r -t {t} {n} {target}")
    out += released(e["file"])
if pending:
    out.append("echo 'GELI on " + ", ".join(p[0] for p in pending) + ": this script mounts nothing that holds the key file'; exit 1")
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
# illumos: list_tree's listing of DIR into OUT (its stderr into ERR) under a
# watchdog, not one ssh session that waits up to an hour.  On
# t2h-20261127T1312-9ca9 (omnios58-mirror, copland) the verify's listing
# of /mnt/dst stopped, the rescuer idle (no CPU, no write) and its ssh
# session open, until the job's 30-minute stall stopped the run with
# nothing to say why (the cause: list_diag_sh, below).  Here the listing runs in the rescuer by itself
# (nohup, /tmp/ht1-list.*), and every HT_LIST_POLL_SEC (15) an ssh of its
# own asks how it does: done (its exit status), or the CPU time of its
# find, sha256sum and sort and what the pools but the rescuer's own rpool
# have read (kstat zfs:0:POOL:nread; the rescuer's own disk is read all
# the time, if only for the polls' commands).  When neither has changed
# for HT_LIST_STALL_SEC (300; a healthy listing of a 525M tree takes 10 to
# 30 s and uses CPU all along), or the listing
# has taken HT_LIST_MAX_SEC (3600, the old ssh's limit), the rescuer's
# state goes to DIAG (list_diag_sh: the listing's kernel stacks, mdb's
# ::stacks, ::zio_state, the vioblk queues' indexes, iostat, the system
# log; a lost virtio notify, the cause found, named in LIST_WHY too) and
# the listing is a FAIL that says so, at once.
# The start and the fetch are tried 3 times (an ssh that got no answer, as
# the old loop did).  Returns the listing's exit status; 124 when it
# stalled, 125 when it ran too long, 255 when it couldn't be started or
# read back; LIST_WHY says it.
list_tree_watched() { # KEY DIR OUT ERR DIAG
    local lkey=$1 ldir=$2 lout=$3 lerr=$4 ldiag=$5 g=/tmp/ht1-list
    local stall=${HT_LIST_STALL_SEC:-300} cap=${HT_LIST_MAX_SEC:-3600} poll=${HT_LIST_POLL_SEC:-15}
    local t0=$SECONDS lmark=$SECONDS lsig="" lst rc li tok
    LIST_WHY=""
    : > "$lout"; : > "$lerr"
    # (TOK: a start tried again after one whose answer was lost has a token
    # of its own, so the polls never take the other one's end for its own.)
    for li in 1 2 3; do
        [[ $li == 1 ]] || sleep 3
        tok=$$.$li.$RANDOM
        TMO=60 sshk "$lkey" "rm -f $g.*
cat > $g.sh <<'HT1EOF'
( $(list_tree "$ldir") ) > $g.out 2> $g.err
echo \"$tok \$?\" > $g.rc.t; mv $g.rc.t $g.rc
HT1EOF
cat > $g.poll <<'HT1EOF'
$(list_poll_sh)
HT1EOF
cat > $g.diag <<'HT1EOF'
$(list_diag_sh)
HT1EOF
nohup /bin/sh $g.sh < /dev/null > /dev/null 2>&1 &
echo started" 2>> "$lerr" | grep -qx started && break
        ((li < 3)) || { LIST_WHY="it could not be started in the rescuer"; return 255; }
    done
    t0=$SECONDS lmark=$SECONDS
    while :; do
        sleep "$poll"
        lst=$(TMO=60 sshk "$lkey" "/bin/sh $g.poll $tok" 2>/dev/null)
        case $lst in
            "done "*) break ;;
            "run "*) [[ $lst == "$lsig" ]] || { lsig=$lst; lmark=$SECONDS; } ;;
        esac
        if ((SECONDS - lmark >= stall || SECONDS - t0 >= cap)); then
            if ((SECONDS - lmark >= stall)); then
                rc=124 LIST_WHY="no progress for $((SECONDS - lmark)) s (${lsig:-no answer from the rescuer}), the rescuer's state in $(basename "$ldiag")"
            else
                rc=125 LIST_WHY="still running after $((SECONDS - t0)) s (${lsig:-no answer from the rescuer}), the rescuer's state in $(basename "$ldiag")"
            fi
            { echo "ht1-bsd: the listing of $ldir: $LIST_WHY"; TMO=300 sshk "$lkey" "/bin/sh $g.diag" 2>&1; } > "$ldiag"
            # (list_diag_sh's verdict: the illumos virtio lost notify.)
            lst=$(grep -m 1 '^HT1-LOSTKICK ' "$ldiag") &&
                LIST_WHY+="; the rescuer's virtio queue lost a notify (illumos): ${lst#HT1-LOSTKICK }"
            return "$rc"
        fi
    done
    # (The fetch: a 12 MiB listing takes seconds; 5 minutes is a stall too.)
    rc=${lst#done }
    for li in 1 2 3; do
        [[ $li == 1 ]] || sleep 3
        TMO=300 sshk "$lkey" "cat $g.err >&2; cat $g.out" > "$lout" 2>> "$lerr" && break
        lst=$?
        ((li < 3)) || { LIST_WHY="it finished (exit $rc), but its output could not be read back (ssh exit $lst)"; return 255; }
    done
    TMO=60 sshk "$lkey" "rm -f $g.*" 2>/dev/null
    [[ $rc =~ ^[0-9]+$ ]] || rc=255
    return "$rc"
}
# The rescuer's half of list_tree_watched: how the listing does (illumos).
list_poll_sh() {
    cat <<'EOF'
if [ -f /tmp/ht1-list.rc ]; then set -- "$1" $(cat /tmp/ht1-list.rc); [ "$2" != "$1" ] || { echo "done $3"; exit 0; }; fi
c=$(ps -e -o time= -o comm= 2>/dev/null | awk '$2 ~ /(^|\/)(find|sha256sum|sort)$/ { printf "%s,", $1 }')
r=$(kstat -p -m zfs -c disk -s nread 2>/dev/null | awk '$1 !~ /:rpool:nread$/ { s += $2 } END { print s + 0 }')
echo "run cpu=${c:-none} nread=$r"
EOF
}
# ... and its state when the listing stalled: mdb -k first (it takes no
# lock, and it reads a process that pstack and pfiles would hang on: one in
# zio_wait); then every vioblk queue's indexes (a request the driver put in
# the avail ring that the device never took, with used's NO_NOTIFY clear,
# is a lost notify: HT1-LOSTKICK, below); a 4 KiB read of each disk, which
# notifies its queue again, and the indexes after it; zpool status last.
# Found on t2h-20261127T1312-9ca9's rescuer, reproduced in the bugfix loop
# of 2026-11-27 (copland, iteration 26): sha256sum in zio_wait on a 4 KiB
# read of bkodisk-0 for 365 s, vioblk#1's avail idx 2fea and used idx 2fe9,
# one request in flight, used flags 0; one read of that disk and it went
# on.  OmniOS r151058's virtio_queue_flush_locked stores avail->idx and then
# tests used->flags with only membar_producer between (no full fence), so
# the CPU may read flags before its store is seen and skip the notify while
# QEMU, done with its batch, re-enables notifies and finds no new request.
list_diag_sh() {
    cat <<'EOF'
k() { echo "== mdb -k: $1"; echo "$1" | mdb -k 2>&1 | head -n "${2:-300}"; }
vq() {
    for d in $(echo "::prtconf -d vioblk" | mdb -k 2>/dev/null | awk '/driver name: vioblk/ { for (i = 1; i <= NF; i++) if ($i ~ /^fffff[0-9a-f]+$/) { print $i; break } }'); do
        v=$(echo "$d::print struct dev_info devi_driver_data | ::print vioblk_t vib_vq" | mdb -k 2>/dev/null | awk '{ print $NF }')
        id=$(echo "$d::print struct dev_info devi_driver_data | ::print vioblk_t vib_devid" | mdb -k 2>/dev/null | sed -n 's/.*\[ "\(.*\)" \].*/\1/p')
        in=$(echo "$d::print -d struct dev_info devi_instance" | mdb -k 2>/dev/null | awk '{ print $NF }')
        echo "$v::print virtio_queue_t viq_dma_driver viq_dma_device viq_inflight.avl_numnodes" | mdb -k 2>/dev/null | awk '{ print $1, $NF }' > /tmp/ht1-list.vq
        drv=$(awk '$1 == "viq_dma_driver" { print $2 }' /tmp/ht1-list.vq)
        dev=$(awk '$1 == "viq_dma_device" { print $2 }' /tmp/ht1-list.vq)
        nf=$(awk '$1 ~ /avl_numnodes/ { print $2 }' /tmp/ht1-list.vq)
        a=$(echo "$drv/2x" | mdb -k 2>/dev/null | awk '{ print $3 }')
        u=$(echo "$dev/2x" | mdb -k 2>/dev/null | awk '{ print $2, $3 }')
        echo "vioblk#$in $id: in flight $nf, avail idx $a, used flags/idx $u"
    done; }
echo "== date, uptime"; date; uptime
pids=$(ps -e -o pid= -o comm= | awk '$2 ~ /(^|\/)(find|sha256sum|sort)$/ { print $1 }')
echo "== the listing's processes"; ps -e -o pid,ppid,s,time,etime,wchan,args | awk 'NR == 1 || /find|sha256sum|sort|ht1-list/'
for p in $pids; do
    k "0t$p::pid2proc | ::walk thread | ::findstack -v"
    k "0t$p::pid2proc | ::pfiles"
done
echo "== the vioblk queues"; vq > /tmp/ht1-list.vq0; cat /tmp/ht1-list.vq0
k "::stacks -m zfs"
k "::stacks -m blkdev"
k "::zio_state -r"
k "::spa -v"
k "::stacks" 600
echo "== iostat -xn"; iostat -xn 2>&1
echo "== /var/adm/messages"; tail -n 60 /var/adm/messages 2>&1
echo "== a read of each disk, and the queues after it"
for r in /dev/rdsk/c*t0d0p0; do dd if="$r" of=/dev/null bs=4k count=1 iseek=1 2>/dev/null; done
sleep 5; vq > /tmp/ht1-list.vq1; cat /tmp/ht1-list.vq1
# A queue with requests in flight, the avail ring ahead of the used one and
# used's flags 0 before the reads, whose used idx moved after them.
awk 'NR == FNR { gsub(/,/, ""); b[$1] = $0; next } { gsub(/,/, ""); split(b[$1], o, " "); if (o[5] > 0 && o[8] != o[12] && o[11] == 0 && $12 != o[12]) print "HT1-LOSTKICK " $1 " " $2 " avail idx " o[8] ", used idx " o[12] " until a read of the disk notified it again (used idx " $12 " after)" }' /tmp/ht1-list.vq0 /tmp/ht1-list.vq1
echo "== zpool status -v"; zpool status -v 2>&1
EOF
}

phase_source() {
    say "== source: $G"
    for ((i = 0; i < n; i++)); do
        rm -f "${src[i]}"; qemu-img create -q -f qcow2 -b "${imgs[i]}" -F qcow2 "${src[i]}"
    done
    CONSOLE_KEY=$gpass vm_up "$SRC" "$R/logs/source-serial.log" "${fw[@]}" "${s_guest[@]}" "${src_more[@]}"
    local wrc; vm_wait "$gkey" "$R/logs/source-serial.log"; wrc=$?
    ((wrc == 0)) || { bad "source boots ($(wait_why $wrc); logs/source-serial.log)"; vm_down "$gkey"; return; }
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

# BACKUP_STATE: "failed" from the backup phase's start until rustic-bko's
# backup made the repository ("ok"); empty when this run has no backup phase
# (a rerun from rebuild or verify uses the last run's).  Without the
# repository the rebuild and the verify would only add FAILs of their own
# to the backup's, so they say they weren't run instead (a note: the
# backup's FAIL lines stay the run's only ones, and its RESULT a FAIL).
BACKUP_STATE=""
backup_failed() { # PHASE
    [[ $BACKUP_STATE == failed ]] || return 1
    say "note: $1 not run: the backup failed (its FAIL lines above)"
}
phase_backup() {
    say "== backup"
    BACKUP_STATE=failed
    rm -f "$RESC"; qemu-img create -q -f qcow2 -b "$rimg" -F qcow2 "$RESC"
    qemu-img resize -q "$RESC" +8G 2>/dev/null || true
    # GELI: the rescuer's loader probes every disk's partitions for a pool,
    # and may ask for a GELIBOOT provider's passphrase too (seen: run geli1,
    # 2026-11-14), so it boots through console-boot.py when the source has one.
    # So too for a softraid CRYPTO volume (OpenBSD's /boot probes every
    # disk's softraid metadata), and the mount script attaches it with the
    # passphrase.
    local geli sr ck=""
    geli=$(geli_provs "$R/dumps/src.json" 2>/dev/null) || geli=""   # (no layout: the mount script says so)
    sr=$(sr_vols "$R/dumps/src.json" 2>/dev/null) || sr=""
    [[ -n $geli$sr ]] && ck=$gpass
    CONSOLE_KEY=$ck resc_up "$rkey" "$RESC" "$R/logs/backup-serial.log" "${rfw[@]}" --memory 3072 "${s_resc[@]}" "${all_src[@]}" ||
        { bad "rescuer boots"; vm_down "$rkey"; return; }
    rescuer_ready $((1 + n)) || { harness "rescuer setup (the backup's rescuer's disks: $RR_WHY)"; vm_down "$rkey"; return; }
    if [[ -n $geli$sr ]]; then
        geli_key_put "$rkey" "$geli" || { bad "the GELI or softraid passphrase into the rescuer's tmpfs $GELI_DIR${GELI_WHY:+: $GELI_WHY}"; vm_down "$rkey"; return; }
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
    # A GELI provider of its own opened by a key file (geli2.md's /data on
    # vtbd0p4, its key file read from the source's root once that is
    # mounted): the files on it and their SHA-256, to compare with the
    # restored system's at the verify's own boot.  Hashes and paths only.
    local gpart gprov gkeyed gmp
    [[ -z $geli ]] || while read -r gpart gprov gkeyed _ _; do
        case $gkeyed in keyfile|both) ;; *) continue ;; esac
        gmp=$(geli_fstab_mp "$R/dumps/src.json" "$gpart")
        [[ -n $gmp ]] || { bad "the source's fstab mounts nothing on $gpart's GELI provider, which a key file opens"; continue; }
        if TMO=1800 sshk "$rkey" "$(geli_files_list "/mnt/src$gmp")" > "$R/logs/source-geli-files-$gpart.txt" 2> "$R/logs/source-geli-files-$gpart.err"; then
            ok "the source's $gmp on $gprov.eli, attached with the key file on the source's root ($(grep -c . "$R/logs/source-geli-files-$gpart.txt") files listed)"
        else
            bad "the source's files on $gmp ($gprov.eli): $(tail -1 "$R/logs/source-geli-files-$gpart.err")"
        fi
    done <<<"$geli"
    if TMO=7200 sshk "$rkey" "R=\"$RH/rustic-bko -r $RH/repo --password-file $RH/repo-password --no-progress --no-cache\"; rm -rf $RH/repo; \$R init && \$R backup --set-atime yes /mnt/src" > "$R/logs/backup.log" 2>&1; then
        ok "rustic-bko backup ($(sshk "$rkey" "du -sh $RH/repo" | cut -f1))"
        BACKUP_STATE=ok
    else
        bad "rustic-bko backup (logs/backup.log)"
    fi
    # Sockets aren't backed up (neither archiver keeps them).  The listing
    # only reads: an ssh that got no answer (255, nothing on either stream)
    # is tried again, and an empty listing is never one (a tree has its
    # root's line at least).
    # (illumos: under list_tree_watched's watchdog.)
    local lrc li
    LIST_WHY=""
    if [[ $PLAT == illumos ]]; then
        list_tree_watched "$rkey" /mnt/src "$R/src.listing" "$R/logs/fsmeta-src.log" "$R/logs/backup-list-stall.txt"; lrc=$?
    else
        for li in 1 2 3; do
            [[ $li == 1 ]] || { echo "ht1-bsd: no answer listing the source's files (exit $lrc), try $li of 3" >&2; sleep 3; }
            TMO=3600 sshk "$rkey" "$(list_tree /mnt/src)" > "$R/src.listing" 2> "$R/logs/fsmeta-src.log"; lrc=$?
            [[ $lrc == 255 && ! -s $R/src.listing && ! -s $R/logs/fsmeta-src.log ]] || break
        done
    fi
    if [[ $lrc != 0 ]]; then
        bad "the listing of the source's files: exit $lrc${LIST_WHY:+, $LIST_WHY}$([[ -s $R/logs/fsmeta-src.log ]] && echo ": $(tail -1 "$R/logs/fsmeta-src.log")")"
    elif [[ ! -s $R/src.listing ]]; then
        bad "the listing of the source's files: empty (logs/fsmeta-src.log)"
    fi
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
    # t2-only: json
    if [[ -n ${JPASS:-} ]] && json_is_stage "$name"; then json_load_step "$want" "$name" "$@"; return; fi
    # t2-only-end
    ht_load_try "$R/logs/rebuild-$name.out" "$R/logs/rebuild-$name.err" sshk "$rkey" "echo $HT_STARTED; $RHOLT load $RH/layout.json $*"
    local got=$?
    if [[ $got == "$want" ]]; then ok "load $name (exit $got)"; return 0; fi
    bad "load $name: exit $got, want $want: $(grep -v '^$' "$R/logs/rebuild-$name.err" | tail -3 | tr '\n' ' ')"
    return 1
}

phase_rebuild() {
    say "== rebuild"
    backup_failed rebuild && return
    local fails0=$fails
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
        IMG_BOOT=1 resc_up "$rkey" "$R/img.qcow2" "$R/logs/rescuer-serial.log" "${ifw[@]}" --memory 3072 "${s_decoy[@]}" "${all_dst[@]}" --disk "$R/decoy.qcow2" --disk "$try"
    else
        resc_up "$rkey" "$try" "$R/logs/rescuer-serial.log" "${rfw[@]}" --memory 3072 "${s_decoy[@]}" "${all_dst[@]}" --disk "$R/decoy.qcow2"
    fi || { bad "rescuer boots"; vm_down "$rkey"; return; }
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
    rescuer_ready $((n + 2 + IMG)) || { harness "rescuer setup (the rebuild's rescuer's disks: $RR_WHY)"; vm_down "$rkey"; return; }
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
    # A provider with key slot 1 in use (geli2.md's freebsd15-geli-ufs) takes
    # --key-file NAME:1=FILE too, slot 1's own passphrase; kf0 is the same
    # without those, for the refusal below.
    local geli sr kf=() kf0=() slot1=() gname gpart gslots gkeyed
    geli=$(geli_provs "$R/dumps/src.json" 2>/dev/null) || geli=""   # (no layout: the disk count below says so)
    sr=$(sr_vols "$R/dumps/src.json" 2>/dev/null) || sr=""
    if [[ -n $geli$sr ]]; then
        geli_key_put "$rkey" "$geli" || { bad "rescuer setup (the GELI or softraid passphrase into its tmpfs $GELI_DIR${GELI_WHY:+: $GELI_WHY})"; vm_down "$rkey"; return; }
        [[ -z $geli ]] || while read -r gpart gname gkeyed gslots _; do
            # A provider opened by its key file alone gets none: load makes
            # it with a temporary key and setkeys the restored key file in.
            [[ $gkeyed == keyfile ]] && continue
            kf+=(--key-file "$gname=$GELI_KEY"); kf0+=(--key-file "$gname=$GELI_KEY")
            case ,${gslots:-0}, in
            *,1,*) kf+=(--key-file "$gname:1=$GELI_KEY1"); slot1+=("$gpart") ;;
            esac
        done <<<"$geli"
        [[ -z $sr ]] || while read -r gname _; do
            kf+=(--key-file "$gname=$GELI_KEY"); kf0+=(--key-file "$gname=$GELI_KEY")
        done <<<"$sr"
        how+="; ${geli:+GELI}${sr:+softraid}: ${kf[*]}"
    fi
    ok "rescuer up$how"
    # t2-only: json
    [[ -z $JSON_RUN ]] || json_keys_put "$rkey" || { bad "rescuer setup (the password file into $JKEYS)"; vm_down "$rkey"; return; }
    # t2-only-end
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
    local quick=${HT_QUICK:-} nedges=$((n + 1)) before=0   # a line per target, and the decoy's
    [[ $quick == 1 ]] || {
    if edges_read "$rkey" "$edges" "$R/logs/edges-before.txt" "$nedges"; then
        before=1
    else
        harness "the targets' and the decoy's first and last MiB couldn't be read before the refusals (logs/edges-before.txt): what they write isn't checked"
    fi
    load_step 4 refuse-nomap "${kf[@]}"
    load_step 4 refuse-root "--map ${srcn[0]}=${RN[0]} --erase ${RN[0]}" "${others[@]}" "${kf[@]}"
    load_step 4 refuse-decoy "--map ${srcn[0]}=${RN[D]}" "${others[@]}" "${kf[@]}" \
        && { cat "$R/logs/rebuild-refuse-decoy".{out,err} | grep -q "isn't blank" || bad "load refuse-decoy: refused, but not for the decoy's filesystem"; }
    }
    local map=() erase=() written=() unread=()
    for ((i = 0; i < n; i++)); do
        map+=(--map "${srcn[i]}=${RN[i + 1]}")
        erase+=(--erase "$([[ $PLAT == haiku || $PLAT == illumos ]] && echo "${RN[i + 1]}" || rdev $((i + 1)))")
    done
    # GELI key slot 1 (geli2.md, the design's D4): the same plan without
    # --key-file NAME:1=FILE must refuse, and name the slot, before the real
    # load -- a restored system whose slot 1 was dropped loses that way in.
    # (Not under HT_QUICK's skip: the real load below gets the :1 key files,
    # and nothing else would show load still asks for them.)
    if ((${#slot1[@]})) && load_step 4 refuse-slot1 "${map[@]}" "${kf0[@]}"; then
        for gpart in "${slot1[@]}"; do
            if grep -q "$gpart.*key slot 1 is in use" "$R/logs/rebuild-refuse-slot1".{out,err}; then
                ok "load without --key-file $gpart:1 refuses: $gpart's GELI key slot 1 is in use"
            else
                bad "load without --key-file $gpart:1 refused, but not for $gpart's GELI key slot 1 (logs/rebuild-refuse-slot1.out, .err)"
            fi
        done
    fi
    map+=("${kf[@]}")
    [[ $quick == 1 ]] || {
    load_step 0 dry-partition "${map[@]}" --only partition
    for ((i = 1; i <= n; i++)); do
        head_written "$rkey" "$(rraw $i)"
        case $? in
            0) written+=("$(rdev $i)") ;;
            1) ;;                            # all zero: nothing was written there
            *) unread+=("$(rdev $i)") ;;     # no answer: neither written nor blank
        esac
    done
    if ((${#written[@]})); then bad "${written[*]} written without --commit"
    elif ((${#unread[@]})); then harness "${unread[*]} couldn't be read after the dry run: nothing says whether they were written"
    else ok "nothing written without --commit"; fi
    if ((before)); then
        if ! edges_read "$rkey" "$edges" "$R/logs/edges-after.txt" "$nedges"; then
            harness "the targets' and the decoy's first and last MiB couldn't be read after the refusals and the dry run (logs/edges-after.txt)"
        elif cmp -s "$R/logs/edges-before.txt" "$R/logs/edges-after.txt"; then
            ok "the refusals and the dry run left every target's and the decoy's first and last MiB as they were"
        else
            bad "a refusal or the dry run wrote to a disk (logs/edges-before.txt, edges-after.txt)"
        fi
    fi
    }
    map+=("${erase[@]}")
    local repo=(--repo $RREPO/repo --password-file $RREPO/repo-password --archiver $RARCH --snapshot "latest:/mnt/src")
    # t2-only: json
    [[ -z $JSON_RUN ]] || json_private_args
    # t2-only-end
    local stages=(partition assemble format mount restore boot close)
    [[ $VARIANT == oneshot ]] && stages=(oneshot)
    if [[ -n ${HT_THROUGH:-} ]]; then
        [[ " ${stages[*]} " == *" $HT_THROUGH "* ]] || { bad "HT_THROUGH=$HT_THROUGH: not one of ${stages[*]}"; vm_down "$rkey"; return; }
        local upto=() st; for st in "${stages[@]}"; do upto+=("$st"); [[ $st == "$HT_THROUGH" ]] && break; done; stages=("${upto[@]}")
    fi
    # t2-only: json
    [[ -z ${JPASS:-} ]] || json_before_stages || { kept_or_down; return; }
    # t2-only-end
    for s in "${stages[@]}"; do
        local how=(--only "$s") extra=()
        [[ $s == restore || $s == oneshot ]] && extra=("${repo[@]}")
        [[ $s == oneshot ]] && how=(--through close)
        TMO=7200 load_step 0 "$s" "${map[@]}" "${extra[@]}" "${how[@]}" --commit || {
            sshk "$rkey" "cat /var/run/bko-holt/log/load.log" > "$R/logs/rebuild-load.log"
            sshk "$rkey" "set -x; mount; df; fstat | awk -v m=/mnt/bko-target 'NR == 1 { print; next } { for (i = 5; i <= NF; i++) if (\$i == m) { print; next } }'; gpart show; disklabel ${RN[1]}; dkctl ${RN[1]} listwedges; zpool status; zpool list -v; zfs list -o name,mounted,mountpoint,canmount; glabel status; swapctl -l" > "$R/logs/rebuild-failed-state.txt" 2>&1
            # (The rescuer's sshd log, as the rebuild's end keeps it below.)
            sshk "$rkey" "cat /var/log/auth.log /var/log/authlog 2>/dev/null; true" > "$R/logs/rebuild-auth.log" 2>&1
            kept_or_down; return; }
        [[ $PLAT == haiku ]] && sshk "$rkey" "echo $s \$(stat -c %Y.%y /mnt/bko-target 2>&1)" >> "$R/logs/haiku-root-mtime.txt" 2>&1
        # Haiku publishes a new table's partitions only at boot.
        if [[ $PLAT == haiku && $s == partition ]]; then
            TMO=20 sshk "$rkey" "shutdown -r -q" >/dev/null 2>&1
            sleep 20
            # (The same QEMU: its console still goes to rescuer-serial.log.)
            local wrc; vm_wait "$rkey" "$R/logs/rescuer-serial.log"; wrc=$?
            ((wrc == 0)) || { bad "the rescuer's restart after partition ($(wait_why $wrc); logs/rescuer-serial.log)"; vm_down "$rkey"; return; }
            rescuer_ready $((n + 2 + IMG)) || { harness "the rescuer's restart after partition (its disks: $RR_WHY)"; vm_down "$rkey"; return; }
            if ((IMG)); then
                # (img-boot.py started sshd again at the image's menu.)
                img_setup || { bad "the rescuer's restart after partition (the rescue image's repository disk: $(tail -n 1 "$R/logs/rescuer-img-setup.log"))"; vm_down "$rkey"; return; }
                put "$rkey" "$R/dumps/src.json" $RH/layout.json
            else
                put "$rkey" "$HOLT" $RH/bko-holt
            fi
        fi
        ht_status_read "$R/logs/rebuild-status-after-$s.txt" sshk "$rkey" "$RHOLT load $RH/layout.json ${map[*]} --only status" ||
            { harness "the load status after $s couldn't be read: ssh got no answer (logs/rebuild-status-after-$s.txt)"; continue; }
        if [[ $s == oneshot ]]; then
            local st
            for st in partition format close; do
                grep -q "^$st  *done" "$R/logs/rebuild-status-after-$s.txt" || bad "status after the one-command rebuild: $(grep "^$st " "$R/logs/rebuild-status-after-$s.txt")"
            done
            grep -q "^next: nothing" "$R/logs/rebuild-status-after-$s.txt" || bad "status after the one-command rebuild: $(grep '^next' "$R/logs/rebuild-status-after-$s.txt")"
        else
            grep -q "^$s  *done" "$R/logs/rebuild-status-after-$s.txt" || [[ $s == assemble ]] || bad "status after $s: $(grep "^$s " "$R/logs/rebuild-status-after-$s.txt")"
        fi
        # t2-only: json
        [[ -z ${JPASS:-} ]] || json_status_after "$s"
        # t2-only-end
    done
    # The decoy was never a target: its first and last MiB are as they were.
    # (Without the before capture there is nothing to compare, and the harness
    # has said so already.)
    if [[ $quick != 1 ]] && ((before)); then
        if ! edges_read "$rkey" "$edges" "$R/logs/edges-end.txt" "$nedges"; then
            harness "the decoy's first and last MiB couldn't be read at the end of the rebuild (logs/edges-end.txt)"
        elif [[ $(sed -n "${D}p" "$R/logs/edges-end.txt") != "$(sed -n "${D}p" "$R/logs/edges-before.txt")" ]]; then
            bad "the decoy changed (logs/edges-before.txt, edges-end.txt)"
        fi
    fi
    sshk "$rkey" "cat /var/run/bko-holt/log/load.log" > "$R/logs/rebuild-load.log"
    # A rebuild that failed keeps the rescuer's sshd log: the overlay it
    # wrote goes below, and an ssh 255 (ht_load_try) is told by it --
    # "closed preauth" (no answer in time) or a session cut off.
    # (auth.log: FreeBSD, DragonFly; authlog: OpenBSD, NetBSD, illumos.)
    ((fails == fails0)) || sshk "$rkey" "cat /var/log/auth.log /var/log/authlog 2>/dev/null; true" > "$R/logs/rebuild-auth.log" 2>&1
    [[ ${HT_KEEP:-} == 1 ]] && { kept_or_down; return; }
    [[ -z $geli$sr ]] || geli_key_rm "$rkey"
    vm_down "$rkey"
    rm -f "$try" "$R/img.qcow2" "$R/img-key" "$R/img-key.pub"
    # t2-only: json
    [[ -z $JSON_RUN || -n ${JPASS:-} ]] || json_rebuild
    # t2-only-end
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
    backup_failed verify && return
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
        if CONSOLE_KEY=$ck resc_up "$rkey" "$RESC" "$R/logs/verify-rescuer-serial.log" "${vsnap[@]}" "${rfw[@]}" --memory 3072 "${s_resc[@]}" "${all_dst[@]}"; then
            if ! rescuer_ready $((1 + n)); then
                harness "rescuer setup (the verify's rescuer's disks: $RR_WHY)"
                echo exit 1 > "$R/logs/verify-mount.sh"
            elif [[ -n $geli$sr ]] && ! geli_key_put "$rkey" "$geli"; then
                bad "the GELI or softraid passphrase into the rescuer's tmpfs $GELI_DIR${GELI_WHY:+: $GELI_WHY}"
                echo exit 1 > "$R/logs/verify-mount.sh"
            else
                # GELI key slot 1 before the mount script, which attaches the
                # providers with slot 0: on the booted guest the root is
                # attached already, so this is the only place to try slot 1.
                [[ -z $geli ]] || geli_slot1_check "$R/dumps/src.json" 1 "$rkey" "$geli"
                mount_script "$R/dumps/src.json" 1 /mnt/dst > "$R/logs/verify-mount.sh" 2> "$R/logs/verify-mount.log" ||
                    echo exit 1 > "$R/logs/verify-mount.sh"
            fi
            if shk "$rkey" < "$R/logs/verify-mount.sh" > "$R/logs/verify-mount.log" 2>&1; then
                grep '^HT1-POOL ' "$R/logs/verify-mount.log" | while read -r _ pn rest; do ok "the restored pool $pn imported from all its disks ($rest)"; done
                grep '^HT1-SR ' "$R/logs/verify-mount.log" | while read -r _ vn rest; do ok "the restored softraid volume $vn attached with the passphrase load gave it ($rest)"; done
                if [[ ! -s $R/src.listing ]]; then
                    # No listing of the source to compare with (the backup's FAIL
                    # says why): mtree -f on an empty spec reads the whole
                    # restored tree as one "extra: .".
                    bad "mtree: not compared: no listing of the source's files (the backup's)"
                elif [[ $PLAT == illumos ]] && { list_tree_watched "$rkey" /mnt/dst "$R/dst.listing" "$R/logs/fsmeta-dst.log" "$R/logs/verify-list-stall.txt"; [[ $? =~ ^(124|125|255)$ ]]; }; then
                    # (illumos: list_tree_watched's watchdog, its state of the
                    # rescuer in logs/verify-list-stall.txt.)
                    bad "mtree: not compared: the listing of the restored files in the rescuer: $LIST_WHY"
                else
                    if [[ $PLAT == illumos ]]; then
                        diff "$R/src.listing" "$R/dst.listing" | sed -n 's/^< /missing: /p; s/^> /extra: /p' > "$R/logs/fsmeta-compare.txt"
                    elif [[ $PLAT == haiku ]]; then
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
    local wrc; vm_wait "$gkey" "$R/logs/restored-serial.log"; wrc=$?
    if ((wrc != 0)); then bad "the restored disk boots by itself ($(wait_why $wrc); logs/restored-serial.log)"; vm_down "$gkey"; return; fi
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
    # GELI (geli2.md): a provider opened by a key file alone is attached by
    # the restored system's own rc.d/geli, with the key file restored on its
    # encrypted root, its file system mounted where fstab says, and the
    # source's files on it (by SHA-256; the key file's own bytes are never
    # read here, only its path in the dump).
    if [[ -n $geli ]]; then
        sshk "$gkey" "geli status; echo HT1-MOUNTS; mount -p" > "$R/logs/restored-geli.txt" 2>&1
        local gpart gprov gkeyed gmp
        while read -r gpart gprov gkeyed _ _; do
            case $gkeyed in keyfile|both) ;; *) continue ;; esac
            if awk -v p="$gprov.eli" -v c="$gprov" '/^HT1-MOUNTS$/ { s = 1 } !s && $1 == p && $3 == c { f = 1 } END { exit !f }' "$R/logs/restored-geli.txt"; then
                ok "geli status: the restored $gprov.eli is attached on $gprov (rc.conf's geli_devices, its key file on the encrypted root)"
            else
                bad "geli status: the restored $gprov.eli isn't attached on $gprov (logs/restored-geli.txt)"
            fi
            gmp=$(geli_fstab_mp "$R/dumps/src.json" "$gpart")
            if [[ -z $gmp ]]; then
                bad "the source's fstab mounts nothing on $gpart's GELI provider, which a key file opens"
                continue
            fi
            if awk -v d="/dev/$gprov.eli" -v m="$gmp" '/^HT1-MOUNTS$/ { s = 1; next } s && $1 == d && $2 == m { f = 1 } END { exit !f }' "$R/logs/restored-geli.txt"; then
                ok "the restored $gmp is mounted from $gprov.eli"
            else
                bad "the restored $gmp isn't mounted from $gprov.eli (logs/restored-geli.txt)"
            fi
            TMO=1800 sshk "$gkey" "$(geli_files_list "$gmp")" > "$R/logs/restored-geli-files-$gpart.txt" 2>&1
            if [[ ! -s $R/logs/source-geli-files-$gpart.txt ]]; then
                bad "the source's files on $gmp were never listed (logs/source-geli-files-$gpart.txt): run the backup phase"
            elif diff -q "$R/logs/source-geli-files-$gpart.txt" "$R/logs/restored-geli-files-$gpart.txt" >/dev/null; then
                ok "the files on the restored $gmp are the source's ($(grep -c . "$R/logs/restored-geli-files-$gpart.txt") files, by SHA-256)"
            else
                bad "the files on the restored $gmp aren't the source's: $(diff "$R/logs/source-geli-files-$gpart.txt" "$R/logs/restored-geli-files-$gpart.txt" | grep -c '^[<>]') lines differ (logs/source-geli-files-$gpart.txt, restored-geli-files-$gpart.txt)"
            fi
        done <<<"$geli"
    fi
    # A swap the source's dump marks onetime: GELI swapon makes anew at each
    # boot over a partition with no metadata, so the restored system's
    # swapinfo names the .eli device (geli2.md's freebsd15-geli-bios).
    local onetime sw; onetime=$(onetime_swaps "$R/dumps/src.json" 2>/dev/null) || onetime=""
    if [[ -n $onetime ]]; then
        sshk "$gkey" "swapinfo" > "$R/logs/restored-swapinfo.txt" 2>&1
        while read -r sw; do
            [[ -n $sw ]] || continue
            if awk -v d="/dev/$sw.eli" '$1 == d { f = 1 } END { exit !f }' "$R/logs/restored-swapinfo.txt"; then
                ok "swapinfo: the restored $sw.eli is on (the onetime swap swapon encrypts at boot)"
            else
                bad "swapinfo: no /dev/$sw.eli on the restored system (logs/restored-swapinfo.txt: $(tr -s ' \n' ' ' < "$R/logs/restored-swapinfo.txt"))"
            fi
        done <<<"$onetime"
    fi
    sshk "$gkey" "$BOOTSTATE" > "$R/logs/restored-bootstate.txt" 2>&1
    if diff -q "$R/logs/source-bootstate.txt" "$R/logs/restored-bootstate.txt" >/dev/null; then
        ok "boot state as the source's ($(grep -c . "$R/logs/restored-bootstate.txt") services down on both)"
    else
        bad "boot state: source $(tr '\n' ' ' < "$R/logs/source-bootstate.txt"), restored $(tr '\n' ' ' < "$R/logs/restored-bootstate.txt")"
    fi
    sshk "$gkey" "$HEALTH" > "$R/logs/restored-health.txt" 2>&1
    # MISSING-TOOL: a check that never ran, a FAIL whatever the source said.
    local newfails; newfails=$({ grep '^FAIL' "$R/logs/restored-health.txt" | grep -vxFf <(grep '^FAIL' "$R/logs/source-health.txt" 2>/dev/null); grep '^MISSING-TOOL' "$R/logs/restored-health.txt"; } || true)
    if [[ -z $newfails ]]; then ok "health: $(grep -v '^FAIL' "$R/logs/restored-health.txt" | tr '\n' ',' | sed 's/,$//; s/,/, /g')"; else bad "health: $(tr '\n' ' ' <<<"$newfails")"; fi
    # t2-only: json
    [[ -z $JSON_RUN ]] || json_restored_log "$gkey"
    # t2-only-end
    put "$gkey" "$HOLT" /tmp/bko-holt
    sshk "$gkey" /tmp/bko-holt dump > "$R/dumps/restored.json" 2> "$R/logs/restored-dump.err" || bad "dump of the restored system: $(tail -1 "$R/logs/restored-dump.err")"
    vm_down "$gkey"
    local grow=(); [[ $VARIANT == bigger ]] && grow=(--grow $((1 << 30)))
    if python3 "$HERE/judge.py" "${grow[@]}" "$R/dumps/src.json" "$R/dumps/restored.json" > "$R/logs/judge.txt" 2>&1; then
        ok "the dumps agree ($(tail -1 "$R/logs/judge.txt"))"
    else
        bad "the dumps differ: $(grep -c '^DIFF' "$R/logs/judge.txt") fields (logs/judge.txt)"
    fi
    # t2-only: json
    [[ -z $JSON_RUN || -n ${JPASS:-} ]] || json_verify
    # t2-only-end
}

say "HoltTest1 $G ($PLAT), run $RUN, $(date -u +%FT%TZ), phases: ${PHASES[*]}"
((IMG)) && say "rebuilt in the rescue image $IMG_PIN/rescuer.img (+img)"
if ((IMG)); then ht_manifest "$rimg" "$IMG_PIN/rescuer.img" "$HERE/img-boot.py" "$HERE/img-boot.d/$PLAT.json" "$T/../stick/boot-test.py"
else ht_manifest "$rimg"; fi
for p in "${PHASES[@]}"; do ht_phase_start "$p"; "phase_$p"; ht_phase_end "$p"; done
# t2-only: json
[[ -z $JSON_RUN ]] || json_done
# t2-only-end
ht_summary
if ((fails == 0)); then say "RESULT: PASS"; exit 0; fi
# (The pin's why stands whatever else failed: the phases after it ran on a
# rescuer the row isn't credited to.  harness()'s only when every FAIL was one.)
[[ -n $IMG_HARNESS ]] || ((hfails < fails)) || IMG_HARNESS=$HARNESS_WHY
say "RESULT: FAIL ($fails${IMG_HARNESS:+; harness: $IMG_HARNESS})"
exit 1
