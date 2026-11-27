#!/bin/sh
# BackOtter rescue image, illumos: make-img-bsd.sh runs this as root in an
# OmniOS VM (see make-img-bsd.sh's header for the contract).
#
#   build.sh info      prints KERNEL= and ZFS_VERSION=, and writes the ZFS
#                      features into in/zfs-features
#   build.sh assemble  writes out/image.img
#   build.sh list      out/image.img's stick root and boot archive, read-only, as a tar
#   build.sh room      out/image.img's free space for a refresh (BKO-ROOM)
#   build.sh refresh   out/image.img, the parent, with the binaries and the
#                      marker swapped in place in its boot archive, and the
#                      archive, its hash, the marker and .volsetid in place on
#                      s2 (docs/design/img-speed.md §4.2): exit 3 when they
#                      don't fit
#
# The system is OmniOS's installer miniroot, as kayak (OmniOS's own media
# tools, in/kayak-*.tar) makes it from OmniOS's signed packages, with more
# packages and files kept (in/pkglist.backotter, keep.backotter,
# services.backotter) and BackOtter's console in place of the installer.  It
# boots into RAM: the loader loads the kernel and the system (the boot archive,
# a UFS image, gzipped) from the stick, and the kernel runs with the boot
# archive as its root, a RAM disk.  Nothing on the stick is mounted, and so
# nothing on it is ever written.  The image is a GPT, OmniOS's own USB layout
# with BackOtter's config partition after it:
#
#   1 (s0)  EFI system partition, FAT: loader64.efi as EFI/BOOT/BOOTX64.EFI
#   2 (s1)  V_BOOT: gptzfsboot, the BIOS loader's second stage
#   3 (s2)  V_ROOT, UFS: /boot, the kernel, the boot archive, the marker
#   4 (s3)  BackOtter's config partition (marker.py's config.img, byte for byte)
#
# The image's system is pinned to the VM's own version (osnet-incorporation
# and release/name), so that the VM's svccfg matches the image's repository
# and the marker's kernel is the VM's.  Update the VM (testvm/vm-update) to
# build from newer packages.
#
# KAYAK_FROM=STEP (cull, strip, sanity, ...) runs kayak again from that step
# of an earlier run in the same VM, keeping its packages: for working on the
# lists.

set -eu
# xpg4 first: POSIX grep (-E) and sed.
PATH=/usr/xpg4/bin:/usr/bin:/usr/sbin:/sbin
die() { echo "build.sh: $*" >&2; exit 1; }

DATASET=rpool/bko-build
CONFIG_SECTORS=524288           # marker.py's config.img, 256 MiB
ESP_SECTORS=69632               # mkgpt.py's 34 MiB
BA_ROOM_KB=49152                # free space in the RAM disk's file system
BINS="restic-bko rustic-bko bko-holt"   # in /usr/local/bin, in the boot archive
HEADROOM_MIN_KIB=65536          # free in the archive and on s2: at least this (img-speed.md §4.2)
console_dir=usr/local/lib/backotter
here=$(pwd)
in=$here/in

lofis=
mounts=
cleanup() {
    for m in $mounts; do umount "$m" 2>/dev/null || umount -f "$m" 2>/dev/null || true; done
    for l in $lofis; do lofiadm -d "$l" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

mnt() { mount "$@"; eval "mounts=\"\${$#} \$mounts\""; }
# unmnt DIR: umount DIR, which mnt mounted.
unmnt() { umount "$1"; mounts=$(echo " $mounts " | sed "s| $1 | |; s|^ *||; s| *\$||"); }
# unlofi DEV: detach DEV, which lofis holds.
unlofi() { lofiadm -d "$1"; lofis=$(echo " $lofis " | sed "s| $1 | |; s|^ *||; s| *\$||"); }
# free_kib DIR: the free KiB (size less what is used) of the file system mounted on DIR.
free_kib() { df -k "$1" | awk 'NR == 2 { print $2 - $3 }'; }

# headroom: the free space the headroom rule asks of the boot archive and of s2,
# in KiB: max(64 MiB, half the binaries' size) (img-speed.md §4.2, D1).
headroom() {
    b=0
    for n in $BINS; do
        [ ! -f "$in/$n" ] || b=$((b + ($(wc -c <"$in/$n") + 1023) / 1024))
    done
    if [ $((b / 2)) -gt $HEADROOM_MIN_KIB ]; then echo $((b / 2)); else echo $HEADROOM_MIN_KIB; fi
}

info() {
    echo "KERNEL=$(uname -v)"
    echo "ZFS_VERSION=illumos-r$(awk -F= '$1 == "VERSION_ID" { print $2 }' /etc/os-release | tr -d '"a-z')"
    # The feature table, up to the legacy versions after it.
    zpool upgrade -v | awk '/^The following legacy/ { exit } /^-+$/ { t = 1; next } t && /^[a-z]/ { print $1 }' \
        >in/zfs-features
    [ -s in/zfs-features ] || die "zpool upgrade -v listed no features"
}

# json_field NAME FILE: a top-level string from marker.py's backotter.json.
json_field() { sed -n "s/^  \"$1\": \"\([^\"]*\)\",*\$/\1/p" "$2"; }

# The FMRI, without the publisher, of an installed package of the VM's.
installed() { pkg list -Hv "$1" | awk '{ sub("^pkg://[^/]*/", "", $1); print $1 }'; }

# Change kayak to build BackOtter's system: its lists, and five changes to
# build/miniroot, each checked:
#   - the publisher requires signatures, from OmniOS's CA (the VM's copy)
#   - no "pkg update" after the install, which would undo the pin
#   - BKO_HOOK runs before the SMF repository is made, to add our services
#   - the sanity step stops only at a missing library that isn't lazily
#     loaded (its baseline is of whichever packages kayak was last released
#     with), and keeps its report in sanity.txt
#   - it stops after the sanity step: build.sh makes the boot archive
prepare_kayak() {
    rm -rf kayak pax_global_header
    # git archive's pax header (its commit) is the one thing illumos tar
    # doesn't know: it complains, makes it a file, and fails.
    tar xf in/kayak-*.tar 2>tar.log || ! grep -v pax_global_header tar.log >&2 || die "couldn't unpack kayak"
    rm -f pax_global_header
    d=kayak/data
    {
        grep -v '^#' in/pkglist.backotter
        installed consolidation/osnet/osnet-incorporation
        installed release/name
    } >>$d/miniroot.pkglist
    { grep -v '^system/filesystem/autofs.xml$' $d/miniroot.services; grep -v '^#' in/services.backotter; } \
        >$d/services.new
    mv $d/services.new $d/miniroot.services
    cp in/keep.backotter $d/keep.backotter

    m=kayak/build/miniroot
    sed -e '/|| fail "image-create"$/a\
    pkg set-publisher --approve-ca-cert /etc/ssl/pkg/OmniOSce_CA.pem \\\
        --set-property signature-policy=require-signatures $OOCEPUB || fail "signature policy"' \
        -e 's/^    pkg update$/    : BackOtter: no update, the packages are pinned/' \
        -e '/^function step_smf {$/a\
    [ -z "$BKO_HOOK" ] || . "$BKO_HOOK" || fail "BackOtter hook"' \
        -e 's|^        diff -U0 \$DATADIR/baseline \$of$|        cp $of $WORKDIR/sanity.txt; ! grep "^Missing" $of \| grep -v "LAZY]$"|' \
        -e '/^function step_sanity {$/,/^}$/s/^    chkpt mkfs$/    exit 0/' \
        $m >$m.new
    for want in 'signature-policy=require-signatures' 'BackOtter: no update' 'BKO_HOOK' \
        'sanity.txt' '^    exit 0$'; do
        [ "$(grep -c -- "$want" $m.new)" = 1 ] || die "couldn't change kayak's build/miniroot ($want)"
    done
    mv $m.new $m
    chmod 755 $m

    # Run by kayak's step_smf, in its shell, before it seeds the repository.
    cat >hook.sh <<EOF
mkdir -p \$ROOTDIR/lib/svc/manifest/application/backotter \$ROOTDIR/$console_dir
cp $here/in/console.xml $here/in/network.xml \$ROOTDIR/lib/svc/manifest/application/backotter/
chmod 444 \$ROOTDIR/lib/svc/manifest/application/backotter/*
cp $here/in/on-tty $here/in/net-up $here/in/console $here/in/remote-access \$ROOTDIR/$console_dir/
chmod 755 \$ROOTDIR/$console_dir/*
EOF
}

assemble() {
    image_id=$(json_field image_id in/backotter.json)
    [ -n "$image_id" ] || die "no image_id in in/backotter.json"

    # --- The system, by kayak ----------------------------------------------------------
    prepare_kayak
    from=${KAYAK_FROM:-begin}
    if [ "$from" = begin ]; then
        zfs destroy -r $DATASET 2>/dev/null || true
        zfs create $DATASET
    fi
    root=$(zfs get -Ho value mountpoint $DATASET)/miniroot
    echo "Building the system with kayak, from its $from step (log: kayak.log)"
    rm -f "$(dirname "$root")/sanity.txt"
    BKO_HOOK=$here/hook.sh bash kayak/build/miniroot $DATASET "$from" >kayak.log 2>&1 ||
        { grep -Ev '^(Download|Actions)' kayak.log | tail -n 40 >&2; die "kayak failed"; }
    [ -f "$(dirname "$root")/sanity.txt" ] || { tail -n 20 kayak.log >&2; die "kayak stopped before its sanity step"; }
    # Every real report lists unused libraries: an empty one means the
    # checker itself failed (its error is in kayak.log), and checked nothing.
    grep -Eq '^(Missing dependency|Unused library)' "$(dirname "$root")/sanity.txt" ||
        { tail -n 20 kayak.log >&2; die "kayak's sanity check reported nothing"; }
    echo "$(grep -c '^Missing.*LAZY]$' "$(dirname "$root")/sanity.txt") lazily loaded libraries are missing, as in OmniOS's installer"
    grep -E '^ --- (removed|stripped|discarding)' kayak.log || true     # none when KAYAK_FROM skips them

    # --- BackOtter's -------------------------------------------------------------------
    (cd "$root"; configure)

    # --- The boot archive: a UFS image of the whole system -----------------------------
    # With the headroom a refresh needs to swap the binaries in place (img-speed.md
    # §4.2, D1): made again with the shortfall added when it has less.
    want=$(headroom)
    kb=$(du -sk "$root" | awk -v room=$BA_ROOM_KB '{ print int($1 * 1.1) + room }')
    # (UFS keeps some of what is added for its own metadata: a quarter more, and again if short.)
    make_archive $kb
    n=0
    while [ "$ba_free" -lt "$want" ]; do
        [ $n -lt 3 ] || die "the boot archive has $ba_free KiB free, under the headroom rule's $want"
        kb=$((kb + (want - ba_free) * 5 / 4 + 4096)) n=$((n + 1))
        make_archive $kb
    done
    echo "Headroom: $ba_free KiB free in the boot archive (the rule: $want)"

    # --- The stick's root: what the loader reads, as on OmniOS's own media --------------
    rm -rf stick
    mkdir stick
    (cd "$root" && find . -print | grep -Ev '^\./(usr|bin|sbin|lib|kernel|\.zfs)(/|$)' |
        cpio -pdum "$here/stick" 2>cpio.log) || { cat cpio.log >&2; die "cpio failed"; }
    gzip -9c ba.ufs >stick/platform/i86pc/amd64/boot_archive.gz
    digest -a sha1 ba.ufs >stick/platform/i86pc/amd64/boot_archive.hash
    rm ba.ufs
    {
        echo 'loader_menu_title="BackOtter rescue stick (illumos)"'
        # The loader on the screen and COM1; the kernel's console is the
        # screen (the first), and BackOtter's console runs on both.
        echo 'console="text,ttya"'
        echo 'ttya-mode="115200,8,n,1,-"'
        if [ "${TEST:-0}" = 1 ]; then echo 'autoboot_delay=2'; else echo 'autoboot_delay=5'; fi
    } >stick/boot/loader.conf.local
    echo "$image_id" >stick/.volsetid
    # Its files' own sizes, each rounded up to UFS's 8 KB blocks: du counts
    # what the build VM's ZFS stores, compressed.  Plus a quarter and 16 MB
    # for UFS's own metadata.
    stick_kb=$(find stick -type f -print | xargs ls -ln | awk '{ k += int(($5 + 8191) / 8192) * 8 } END { print k }')
    root_sectors=$(( (stick_kb * 5 / 4 + 16384) * 2 ))

    # --- The image ----------------------------------------------------------------------
    # s2 with the headroom too: the image made again, s2 larger by the shortfall, when short.
    [ $(wc -c <in/config.img) -eq $((CONFIG_SECTORS * 512)) ] || die "config.img isn't $CONFIG_SECTORS sectors"
    make_disk $root_sectors
    n=0
    while [ "$s2_free" -lt "$want" ]; do
        [ $n -lt 3 ] || die "the stick root has $s2_free KiB free, under the headroom rule's $want"
        unmnt "$here/mnt"
        unlofi "$lofi"
        root_sectors=$((root_sectors + ((want - s2_free) * 5 / 4 + 4096) * 2)) n=$((n + 1))
        make_disk $root_sectors
    done
    echo "Headroom: $s2_free KiB free in the stick root (the rule: $want)"
    # pmbr into the MBR (keeping its table), gptzfsboot into V_BOOT, and the
    # UEFI loader into the ESP, all from the image's own /boot.
    installboot -F -m -f -b "$here/mnt/boot" "${rdisk}s2" >installboot.log 2>&1 ||
        { cat installboot.log >&2; die "installboot failed"; }
    unmnt "$here/mnt"
    dd if=in/config.img of="${rdisk}s3" bs=1024k 2>/dev/null

    for s in s0:pcfs s2:ufs s3:pcfs; do
        [ "$(fstyp "${rdisk}${s%:*}" 2>/dev/null)" = "${s#*:}" ] || die "${s%:*} isn't ${s#*:}"
    done
    dd if="${rdisk}s3" bs=1024k count=$((CONFIG_SECTORS / 2048)) 2>/dev/null | cmp -s - in/config.img || die "s3 isn't config.img"
    mkdir -p esp
    mnt -F pcfs -o ro "${disk}s0" "$here/esp"
    [ -f esp/EFI/BOOT/BOOTX64.EFI ] || [ -f esp/efi/boot/bootx64.efi ] || die "installboot put no BOOTX64.EFI in the ESP"
    unmnt "$here/esp"
    unlofi "$lofi"
}

# make_archive KB: ba.ufs, a KB KiB UFS of the system in $root, with its /dev;
# ba_free its free KiB.
make_archive() {
    rm -f ba.ufs
    mkfile ${1}k ba.ufs
    lofi=$(lofiadm -a "$here/ba.ufs"); lofis="$lofi $lofis"
    yes | newfs -m 0 "$(echo "$lofi" | sed 's,/lofi/,/rlofi/,')" >/dev/null 2>&1 || die "newfs of the boot archive failed"
    mkdir -p ba
    mnt -o nologging "$lofi" "$here/ba"
    (cd "$root" && find . | cpio -pdum "$here/ba" 2>cpio.log) || { cat cpio.log >&2; die "cpio failed"; }
    devfsadm -r "$here/ba"
    df -k "$here/ba" | awk 'NR == 2 { printf "Boot archive: %d MB used, %d MB free\n", $3 / 1024, $4 / 1024 }'
    ba_free=$(free_kib "$here/ba")
    unmnt "$here/ba"
    unlofi "$lofi"
}

# make_disk ROOT_SECTORS: out/image.img, its GPT (s2 ROOT_SECTORS, 1 MiB aligned), the ESP's
# FAT and s2's UFS with stick/ in it, left mounted on mnt and attached as $lofi ($disk,
# $rdisk); s2_free its free KiB.
make_disk() {
    root_sectors=$(( ($1 + 2047) / 2048 * 2048 ))
    rm -f out/image.img
    python3 in/mkgpt.py out/image.img $root_sectors $CONFIG_SECTORS >layout.txt || die "mkgpt.py failed"
    lofi=$(lofiadm -la "$here/out/image.img"); lofis="$lofi $lofis"
    disk=${lofi%p0}
    [ "$disk" != "$lofi" ] || die "lofiadm -la gave $lofi, not a whole disk (p0)"
    rdisk=$(echo "$disk" | sed 's,/dsk/,/rdsk/,')
    # prtvtoc's slices must be mkgpt.py's partitions (slice N-1 is partition N).
    got=$(prtvtoc -h "${rdisk}p0" | awk '$1 <= 3 { print $1 + 1, $4, $5 }')
    [ "$got" = "$(cat layout.txt)" ] || die "illumos sees the partitions as: $got"

    yes | mkfs -F pcfs -o b=EFI,fat=32,spc=1,nofdisk,size=$ESP_SECTORS "${rdisk}s0" >/dev/null 2>&1 ||
        die "mkfs of the ESP failed"
    yes | newfs -m 0 "${rdisk}s2" >/dev/null 2>&1 || die "newfs of the root failed"
    mkdir -p mnt
    mnt -o nologging "${disk}s2" "$here/mnt"
    (cd stick && find . -print | cpio -pdum "$here/mnt" 2>../cpio.log) || { cat cpio.log >&2; die "cpio failed"; }
    touch mnt/.metadata_never_index
    df -k "$here/mnt" | awk 'NR == 2 { printf "Stick root: %d MB used, %d MB free\n", $3 / 1024, $4 / 1024 }'
    s2_free=$(free_kib "$here/mnt")
}

# Make the system in the current directory (kayak's miniroot) BackOtter's.
configure() {
    mkdir -p usr/local/bin
    cp "$here/in/restic-bko" "$here/in/rustic-bko" "$here/in/bko-holt" usr/local/bin/
    chmod 755 usr/local/bin/restic-bko usr/local/bin/rustic-bko usr/local/bin/bko-holt
    mkdir -p .disk home
    cp "$here/in/backotter.json" .disk/
    chmod 444 .disk/backotter.json

    echo backotter >etc/nodename
    rm -f etc/hostid etc/zfs/zpool.cache
    printf '\nBackOtter rescue stick\n\n' >etc/motd
    # Root needs no password at the console (BackOtter's console runs as
    # root); remote-access sets one for su.
    awk -F: -v OFS=: '$1 == "root" { $2 = "*LK*" } { print }' etc/shadow >etc/shadow.new
    cat etc/shadow.new >etc/shadow; rm etc/shadow.new

    # sshd: BackOtter's settings first (sshd keeps the first value it reads),
    # and no host keys: remote-access makes them.
    { cat "$here/in/sshd_config.backotter"; echo; cat etc/ssh/sshd_config; } >etc/ssh/sshd_config.new
    mv etc/ssh/sshd_config.new etc/ssh/sshd_config
    rm -f etc/ssh/ssh_host_*

    # The repository.  console-login (from SMF's seed repository) runs
    # on-tty instead of ttymon and login, which kayak culls: BackOtter's
    # console on the screen (default), root shells on vt2-vt6.  Without a
    # console login that can run, svc.startd would take the console for a
    # maintenance login (kayak's installer then takes it back with its
    # takeover-console).  The virtual consoles' daemon is on, with its
    # hotkeys (Alt-Ctrl-Fn); cryptosvc is on, for sshd, which stays off;
    # idmap is on, for the archivers' SID owners and ACL entries (libidmap).
    # Checked, since svccfg on an image's repository fails quietly at times.
    SVCCFG_REPOSITORY=$(pwd)/etc/svc/repository.db
    SVCCFG_DTD=$(pwd)/usr/share/lib/xml/dtd/service_bundle.dtd.1
    export SVCCFG_REPOSITORY SVCCFG_DTD
    svccfg -s system/console-login setprop start/exec = astring: /usr/local/lib/backotter/on-tty
    for i in default vt2 vt3 vt4 vt5 vt6; do
        svccfg -s system/console-login:$i setprop general/enabled = boolean: true
    done
    svccfg -s system/vtdaemon setprop options/hotkeys = boolean: true
    for s in system/vtdaemon:default system/cryptosvc:default system/idmap:default; do
        svccfg -s $s setprop general/enabled = boolean: true
    done
    got=$(svccfg -s system/console-login listprop start/exec | awk '{ print $3 }')
    [ "$got" = /usr/local/lib/backotter/on-tty ] || die "console-login's start method is $got"
    for want in system/vtdaemon:default:true system/cryptosvc:default:true system/idmap:default:true \
        network/ssh:default:false \
        system/console-login:default:true system/console-login:vt2:true \
        application/backotter/console:serial:true application/backotter/network:default:true; do
        got=$(svccfg -s "${want%:*}" listprop general/enabled 2>/dev/null | awk '{ print $3 }')
        [ "$got" = "${want##*:}" ] || die "${want%:*} is enabled=$got in the image's repository, not ${want##*:}"
    done
    unset SVCCFG_REPOSITORY SVCCFG_DTD
    [ ! -e .initialboot ] && [ ! -d kayak ] || die "kayak's installer is still in the system"
}

# list: out/image.img's stick root (s2) and the boot archive in it, read-only,
# as a tar of stick/ and archive/ (python3's: illumos tar's headers vary).
list() {
    lofi=$(lofiadm -la "$here/out/image.img"); lofis="$lofi $lofis"
    disk=${lofi%p0}
    mkdir -p lst/stick lst/archive
    mnt -F ufs -o ro "${disk}s2" "$here/lst/stick"
    gzip -dc lst/stick/platform/i86pc/amd64/boot_archive.gz >ba-list.ufs
    ba=$(lofiadm -a "$here/ba-list.ufs"); lofis="$ba $lofis"
    mnt -F ufs -o ro "$ba" "$here/lst/archive"
    (cd lst && python3 -c 'import sys, tarfile
t = tarfile.open(fileobj=sys.stdout.buffer, mode="w|", format=tarfile.PAX_FORMAT)
for n in sys.argv[1:]:
    t.add(n)
t.close()' stick archive)
}

# room: out/image.img's room for a refresh, read-only, as refresh-lib.sh's BKO-ROOM
# line: free_kib the less of the boot archive's free space and s2's (a refresh writes
# the new binaries into the archive, and the archive, compressed, back onto s2: what
# the binaries grow by, s2 must hold too), total_kib the archive's, bins_kib the
# binaries' blocks in the archive.
room() {
    . "$in/refresh-lib.sh"
    lofi=$(lofiadm -la "$here/out/image.img"); lofis="$lofi $lofis"
    disk=${lofi%p0}
    mkdir -p rm/stick rm/archive
    mnt -F ufs -o ro "${disk}s2" "$here/rm/stick"
    s2=$(free_kib "$here/rm/stick")
    gzip -dc rm/stick/platform/i86pc/amd64/boot_archive.gz >ba-room.ufs
    ba=$(lofiadm -a "$here/ba-room.ufs"); lofis="$ba $lofis"
    mnt -F ufs -o ro "$ba" "$here/rm/archive"
    # shellcheck disable=SC2046
    set -- $(df -k "$here/rm/archive" | awk 'NR == 2 { print $2 - $3, $2 }')
    echo "room: $1 KiB free in the boot archive, $s2 in the stick root"
    free=$1
    [ "$s2" -ge "$free" ] || free=$s2
    # shellcheck disable=SC2086
    bko_room "$here/rm/archive" /usr/local/bin "$free" "$2" $BINS
    unmnt "$here/rm/archive"; unlofi "$ba"; rm -f ba-room.ufs
    unmnt "$here/rm/stick"; unlofi "$lofi"
}

# fsck_clean RAW_DEVICE WHAT WHEN: fsck -n of a UFS, forced, which must find it clean.
fsck_clean() {
    fsck -F ufs -n -o f "$1" >fsck.out 2>&1 || { cat fsck.out >&2; die "fsck $3 the swap: $2 is not clean"; }
    grep -q ' files, .* used, .* free' fsck.out || { cat fsck.out >&2; die "fsck $3 the swap: no summary for $2"; }
    echo "fsck $3 ($2): $(grep ' files, .* used, .* free' fsck.out | tail -n 1)"
}

# check_marker DIR: the new marker's kernel and ZFS fields (marker.py was given
# the parent's) are the system's in DIR, the parent's boot archive: uname -v's
# version in the kernel (unix), illumos' ZFS version from its os-release, and each
# feature in the zfs module.
check_marker() {
    python3 -c 'import json, sys
m = json.load(open(sys.argv[1]))
z = m.get("zfs") or {}
print(m["kernel"]); print(z.get("version", "")); print("\n".join(z.get("features", [])))' in/backotter.json >marker.txt ||
        die "can't read in/backotter.json"
    k=$(sed -n 1p marker.txt) z=$(sed -n 2p marker.txt)
    [ -n "$k" ] && strings -a "$1/platform/i86pc/kernel/amd64/unix" | grep -qxF -- "$k" ||
        die "the parent's kernel (unix) is not '$k', as its marker says"
    v="illumos-r$(awk -F= '$1 == "VERSION_ID" { print $2 }' "$1/etc/os-release" | tr -d '"a-z')"
    [ "$v" = "$z" ] || die "the parent's os-release says ZFS $v, its marker $z"
    # (Within a string: the compiler keeps "async_destroy" as the tail of "com.delphix:async_destroy".)
    strings -a "$1/kernel/fs/amd64/zfs" | sort -u >zfs-strings.txt
    n=0
    for f in $(sed -n '3,$p' marker.txt); do
        grep -qF -- "$f" zfs-strings.txt || die "the parent's zfs module has no feature $f, which its marker lists"
        n=$((n + 1))
    done
    [ $n -gt 0 ] || die "the marker lists no ZFS features"
    echo "kernel $k, ZFS $z and its $n features, as the parent's marker says"
}

# zero_free DIR: the free blocks of the file system on DIR zeroed, as a full build's are
# (mkfile's), so that the binaries a swap replaced don't stay in them: the boot archive
# is gzipped onto s2, where they would cost room (a refresh's archive measured 4 MB
# larger without this), and the image is compressed at publish.
zero_free() {
    dd if=/dev/zero of="$1/.bko-zero" bs=1024k 2>/dev/null || true
    rm -f "$1/.bko-zero"
    sync
}

# refresh: out/image.img, the parent (make-img-bsd.sh puts it there), with the
# binaries and the marker swapped in place in its boot archive (img-speed.md §4.2):
# the archive unpacked from s2, checked, swapped and checked again, then gzipped back
# into its file on s2 with its SHA-1 in boot_archive.hash, and s2's marker and
# .volsetid (the id sites) rewritten, each in place.  The ESP, the boot code and the
# GPT stay as they are; the host writes config.img into its partition and gives the
# GPT new GUIDs.  The release and the test copy are each refreshed from their own
# parent, so TEST plays no part.  Exit 3 when the binaries don't fit the archive or
# the new archive doesn't fit s2.
refresh() {
    for f in backotter.json bko-holt refresh-lib.sh; do
        [ -e "$in/$f" ] || die "no in/$f"
    done
    [ -f out/image.img ] || die "no out/image.img, the parent"
    . "$in/refresh-lib.sh"
    image_id=$(json_field image_id in/backotter.json)
    [ -n "$image_id" ] || die "no image_id in in/backotter.json"
    lofi=$(lofiadm -la "$here/out/image.img"); lofis="$lofi $lofis"
    disk=${lofi%p0}
    rdisk=$(echo "$disk" | sed 's,/dsk/,/rdsk/,')
    [ "$(fstyp "${rdisk}s2" 2>/dev/null)" = ufs ] || die "the parent's s2 isn't UFS"
    fsck_clean "${rdisk}s2" "the stick root" before
    mkdir -p mnt ba
    mnt -o nologging "${disk}s2" "$here/mnt"
    a=mnt/platform/i86pc/amd64
    gzip -dc $a/boot_archive.gz >ba.ufs || die "can't unpack the parent's boot archive"
    [ "$(digest -a sha1 ba.ufs)" = "$(cat $a/boot_archive.hash)" ] || die "the parent's boot_archive.hash is not its archive's"
    ba=$(lofiadm -a "$here/ba.ufs"); lofis="$ba $lofis"
    rba=$(echo "$ba" | sed 's,/lofi/,/rlofi/,')
    fsck_clean "$rba" "the boot archive" before
    mnt -o nologging "$ba" "$here/ba"
    check_marker "$here/ba"
    # shellcheck disable=SC2086
    bko_fit "$here/ba" /usr/local/bin "$(free_kib "$here/ba")" $BINS || exit $?
    # shellcheck disable=SC2086
    bko_swap "$here/ba" /usr/local/bin $BINS || die "the swap failed"
    zero_free "$here/ba"
    unmnt "$here/ba"
    fsck_clean "$rba" "the boot archive" after
    unlofi "$ba"

    # The archive back onto s2, if it fits there: in place, as are the marker and .volsetid.
    gzip -9c ba.ufs >ba.gz
    new=$(( ($(wc -c <ba.gz) + 8191) / 8192 * 8 + 256 ))
    have=$(( $(free_kib "$here/mnt") + $(bko_du $a/boot_archive.gz) ))
    if [ "$new" -gt "$have" ]; then
        echo "refresh: the new boot archive needs $new KiB, and the stick root has $have: build it in full" >&2
        exit $BKO_NO_ROOM
    fi
    echo "refresh: the new boot archive needs $new KiB of the stick root's $have"
    bko_put "$here/ba.gz" $a/boot_archive.gz 644 || die "couldn't write the boot archive"
    digest -a sha1 ba.ufs >$a/boot_archive.hash
    echo "refresh: boot_archive.gz and boot_archive.hash replaced"
    if [ -f mnt/.disk/backotter.json ]; then
        bko_put "$in/backotter.json" mnt/.disk/backotter.json 444 || die "couldn't write s2's marker"
        echo "refresh: s2's /.disk/backotter.json replaced"
    fi
    [ -f mnt/.volsetid ] || die "the parent's s2 has no .volsetid"
    echo "$image_id" >mnt/.volsetid
    echo "refresh: .volsetid is $image_id"
    rm -f ba.ufs ba.gz
    zero_free "$here/mnt"
    unmnt "$here/mnt"
    fsck_clean "${rdisk}s2" "the stick root" after
    unlofi "$lofi"
}

case ${1:-} in
info) info ;;
assemble) assemble ;;
list) list ;;
room) room ;;
refresh) refresh ;;
*) die "usage: build.sh info|assemble|list|room|refresh" ;;
esac
