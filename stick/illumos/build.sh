#!/bin/sh
# BackOtter rescue image, illumos: make-img-bsd.sh runs this as root in an
# OmniOS VM (see make-img-bsd.sh's header for the contract).
#
#   build.sh info      prints KERNEL= and ZFS_VERSION=, and writes the ZFS
#                      features into in/zfs-features
#   build.sh assemble  writes out/image.img
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
CONFIG_SECTORS=65536            # marker.py's config.img
ESP_SECTORS=69632               # mkgpt.py's 34 MiB
BA_ROOM_KB=49152                # free space in the RAM disk's file system
console_dir=usr/local/lib/backotter
here=$(pwd)

lofis=
mounts=
cleanup() {
    for m in $mounts; do umount "$m" 2>/dev/null || umount -f "$m" 2>/dev/null || true; done
    for l in $lofis; do lofiadm -d "$l" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

mnt() { mount "$@"; eval "mounts=\"\${$#} \$mounts\""; }

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
    kb=$(du -sk "$root" | awk -v room=$BA_ROOM_KB '{ print int($1 * 1.1) + room }')
    mkfile ${kb}k ba.ufs
    lofi=$(lofiadm -a "$here/ba.ufs"); lofis="$lofi $lofis"
    yes | newfs -m 0 "$(echo "$lofi" | sed 's,/lofi/,/rlofi/,')" >/dev/null 2>&1 || die "newfs of the boot archive failed"
    mkdir -p ba
    mnt -o nologging "$lofi" "$here/ba"
    (cd "$root" && find . | cpio -pdum "$here/ba" 2>cpio.log) || { cat cpio.log >&2; die "cpio failed"; }
    devfsadm -r "$here/ba"
    df -k "$here/ba" | awk 'NR == 2 { printf "Boot archive: %d MB used, %d MB free\n", $3 / 1024, $4 / 1024 }'
    umount "$here/ba"; mounts=
    lofiadm -d "$lofi"; lofis=

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
    root_sectors=$(( (root_sectors + 2047) / 2048 * 2048 ))

    # --- The image ----------------------------------------------------------------------
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
    # pmbr into the MBR (keeping its table), gptzfsboot into V_BOOT, and the
    # UEFI loader into the ESP, all from the image's own /boot.
    installboot -F -m -f -b "$here/mnt/boot" "${rdisk}s2" >installboot.log 2>&1 ||
        { cat installboot.log >&2; die "installboot failed"; }
    umount "$here/mnt"; mounts=
    dd if=in/config.img of="${rdisk}s3" bs=1024k 2>/dev/null

    for s in s0:pcfs s2:ufs s3:pcfs; do
        [ "$(fstyp "${rdisk}${s%:*}" 2>/dev/null)" = "${s#*:}" ] || die "${s%:*} isn't ${s#*:}"
    done
    dd if="${rdisk}s3" bs=1024k count=32 2>/dev/null | cmp -s - in/config.img || die "s3 isn't config.img"
    mkdir -p esp
    mnt -F pcfs -o ro "${disk}s0" "$here/esp"
    [ -f esp/EFI/BOOT/BOOTX64.EFI ] || [ -f esp/efi/boot/bootx64.efi ] || die "installboot put no BOOTX64.EFI in the ESP"
    umount "$here/esp"; mounts=
    lofiadm -d "$lofi"; lofis=
}

# Make the system in the current directory (kayak's miniroot) BackOtter's.
configure() {
    mkdir -p usr/local/bin
    cp "$here/in/restic-bko" "$here/in/rustic-bko" usr/local/bin/
    chmod 755 usr/local/bin/restic-bko usr/local/bin/rustic-bko
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
    # hotkeys (Alt-Ctrl-Fn); cryptosvc is on, for sshd, which stays off.
    # Checked, since svccfg on an image's repository fails quietly at times.
    SVCCFG_REPOSITORY=$(pwd)/etc/svc/repository.db
    SVCCFG_DTD=$(pwd)/usr/share/lib/xml/dtd/service_bundle.dtd.1
    export SVCCFG_REPOSITORY SVCCFG_DTD
    svccfg -s system/console-login setprop start/exec = astring: /usr/local/lib/backotter/on-tty
    for i in default vt2 vt3 vt4 vt5 vt6; do
        svccfg -s system/console-login:$i setprop general/enabled = boolean: true
    done
    svccfg -s system/vtdaemon setprop options/hotkeys = boolean: true
    for s in system/vtdaemon:default system/cryptosvc:default; do
        svccfg -s $s setprop general/enabled = boolean: true
    done
    got=$(svccfg -s system/console-login listprop start/exec | awk '{ print $3 }')
    [ "$got" = /usr/local/lib/backotter/on-tty ] || die "console-login's start method is $got"
    for want in system/vtdaemon:default:true system/cryptosvc:default:true network/ssh:default:false \
        system/console-login:default:true system/console-login:vt2:true \
        application/backotter/console:serial:true application/backotter/network:default:true; do
        got=$(svccfg -s "${want%:*}" listprop general/enabled 2>/dev/null | awk '{ print $3 }')
        [ "$got" = "${want##*:}" ] || die "${want%:*} is enabled=$got in the image's repository, not ${want##*:}"
    done
    unset SVCCFG_REPOSITORY SVCCFG_DTD
    [ ! -e .initialboot ] && [ ! -d kayak ] || die "kayak's installer is still in the system"
}

case ${1:-} in
info) info ;;
assemble) assemble ;;
*) die "usage: build.sh info|assemble" ;;
esac
