#!/bin/bash
# BackOtter rescue image, Haiku: make-img-bsd.sh runs this in a Haiku VM as
# "user", Haiku's uid 0 (see make-img-bsd.sh's header for the contract).
#
#   build.sh info      checks the official anyboot image, prints KERNEL=
#   build.sh assemble  writes out/image.img
#
# The system is the anyboot image's, updated: each of its packages is
# replaced by this VM's version where that differs.  The VM is a throwaway
# copy of the haiku6 test guest, which testvm's vm-update keeps up to date
# from Haiku's r1beta6 repository (pkgman full-sync), so the image gets the
# packages of the guest's last update.
#
# The image is an MBR disk:
#   1  type 0xEB, active: BFS "BackOtter", the anyboot image's system and home
#      directories (as Haiku's Installer copies them, without the install
#      media's _packages_ and _sources_), BackOtter's files, and
#      makebootable's boot block for BIOS boots
#   2  type 0xEF: the anyboot image's EFI system partition, as is
#      (EFI/BOOT/BOOTX64.EFI is Haiku's haiku_loader.efi)
#   3  type 0x0E: BackOtter's config partition (marker.py's config.img, byte
#      for byte)
# with writembr's boot code, which boots the active partition.  The anyboot
# image's ISO9660 part (for CDs) isn't kept: the image is for USB sticks.
#
# Haiku mounts the BFS read-write: its kernel puts a write overlay over the
# boot volume only on read-only media (vfs_boot.cpp), and a USB stick isn't.
# The system itself is read-only all the same (packagefs); what Haiku writes
# is settings, logs and caches.  BackOtter's boot job (haiku/boot) forgets the
# last boot's remote access at every boot.

set -euo pipefail
PATH=/bin:/boot/system/bin
die() { echo "build.sh: $*" >&2; exit 1; }

BFS_START=2048                  # 1 MiB, as Haiku's DriveSetup aligns
ALIGN=2048
CONFIG_SECTORS=65536            # marker.py's config.img
ROOM_MB=256                     # free space in the BFS
lib=system/non-packaged/lib/backotter

disks=() mounts=()
cleanup() {
    local m d
    for m in "${mounts[@]}"; do unmount "$m" 2>/dev/null || true; done
    for d in "${disks[@]}"; do diskimage unregister "$d" >/dev/null 2>&1 || true; done
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

# register FILE VAR: FILE as a disk device, its directory (with raw and the
# partitions 0, 1, ...) into VAR, once its partitions NPARTS are there.
register() {
    local out dev i
    out=$(diskimage register "$1") || die "diskimage register $1 failed"
    dev=$(echo "$out" | sed -n 's|.*"\(/dev/disk/virtual/files/[0-9]*\)/raw".*|\1|p')
    [ -n "$dev" ] || die "diskimage said '$out'"
    disks=("$1" "${disks[@]}")
    for i in $(seq 50); do [ -e "$dev/$(($3 - 1))" ] && break; sleep 0.2; done
    [ -e "$dev/$(($3 - 1))" ] || die "$1 has no partition $3 as $dev"
    printf -v "$2" %s "$dev"
}
mnt() { mount "$@"; mounts=("${@: -1}" "${mounts[@]}"); }

# entry N OFFSET: a 32-bit field of MBR entry N of the official image.
entry() { od -An -t u4 -j $((446 + 16 * ($1 - 1) + $2)) -N 4 "$iso" | tr -d ' '; }

# The official image, registered as $src_dev, its BFS mounted read-only on src/.
official() {
    set -- in/*.iso
    [ $# -eq 1 ] && [ -f "$1" ] || die "expected one *.iso in in/"
    iso=$1
    [ "$(od -An -t x1 -j $((446 + 4)) -N 1 "$iso" | tr -d ' ')" = eb ] &&
        [ "$(od -An -t x1 -j $((446 + 16 + 4)) -N 1 "$iso" | tr -d ' ')" = ef ] ||
        die "$iso's partitions 1 and 2 aren't BFS and EFI"
    register "$iso" src_dev 2
    mkdir -p src
    mnt -ro -t bfs "$src_dev/0" src
    [ -d src/system/packages ] && [ -d src/home ] || die "$iso's BFS has no system and home"
}

# updated NAME.hpkg: this VM's package of the same name, if it has one
# other than NAME.hpkg; nothing otherwise.  (Package names have no "-".)
updated() {
    local b=${1##*/} f
    for f in /boot/system/packages/"${b%%-*}"-*.hpkg; do
        [ -f "$f" ] && [ "${f##*/}" != "$b" ] && echo "$f"
    done
    return 0
}

info() {
    official
    set -- src/system/packages/haiku-r1*.hpkg
    [ $# -eq 1 ] && [ -f "$1" ] || die "expected one haiku package in the official image"
    u=$(updated "$1")
    [ -z "$u" ] || set -- "$u"
    # haiku-r1~beta6_hrev59866_79-1-x86_64.hpkg: hrev59866+79, the first word
    # of the kernel's uname -v.
    kernel=$(basename "$1" | sed -n 's/^haiku-[^_]*_\(hrev[0-9]*\)\(_[0-9]*\)\{0,1\}-[0-9]*-x86_64\.hpkg$/\1\2/p' | tr _ +)
    [ -n "$kernel" ] || die "can't read the hrev from $(basename "$1")"
    echo "KERNEL=$kernel"
}

# json_field NAME FILE: a top-level string from marker.py's backotter.json.
json_field() { sed -n "s/^  \"$1\": \"\([^\"]*\)\",*\$/\1/p" "$2"; }

assemble() {
    [ -n "$(json_field image_id in/backotter.json)" ] || die "no image_id in in/backotter.json"
    official

    # --- The layout ------------------------------------------------------------------
    esp_start=$(entry 2 8) esp_sectors=$(entry 2 12)
    used_kb=$(du -sk src/system src/home | awk '{ n += $1 } END { print n }')
    ours_kb=$(du -skL in/rustic-bko in/bko-holt | awk '{ n += $1 } END { print n }')
    # BFS keeps small attributes in the inodes, which du doesn't count: 10%
    # for those, the journal and the indices.
    bfs_sectors=$(( ((used_kb + ours_kb) * 11 / 10 + ROOM_MB * 1024) * 2 ))
    bfs_sectors=$(( (bfs_sectors + ALIGN - 1) / ALIGN * ALIGN ))
    new_esp=$((BFS_START + bfs_sectors))
    config_start=$(( new_esp + (esp_sectors + ALIGN - 1) / ALIGN * ALIGN ))
    total=$((config_start + CONFIG_SECTORS))
    rm -f out/image.img
    truncate -s $((total * 512)) out/image.img
    python3 in/mbr.py out/image.img "eb:$BFS_START:$bfs_sectors:active" \
        "ef:$new_esp:$esp_sectors" "0e:$config_start:$CONFIG_SECTORS"

    # --- Partition 2: the official ESP; partition 3: the config partition -----------
    [ "$(stat -c %s in/config.img)" -eq $((CONFIG_SECTORS * 512)) ] || die "config.img isn't $CONFIG_SECTORS sectors"
    dd if="$iso" of=out/image.img bs=512 skip="$esp_start" seek="$new_esp" count="$esp_sectors" \
        conv=notrunc 2>/dev/null
    dd if=in/config.img of=out/image.img bs=512 seek="$config_start" conv=notrunc 2>/dev/null

    # --- Partition 1: BFS ---------------------------------------------------------
    register out/image.img dst_dev 3
    mkfs -t bfs -q "$dst_dev/0" BackOtter
    mkdir -p dst
    mnt -t bfs "$dst_dev/0" dst
    copyattr -d -r src/home src/system dst/
    loader=no
    for f in dst/system/packages/*.hpkg; do
        u=$(updated "$f")
        [ -n "$u" ] || continue
        echo "  ${f##*/} -> ${u##*/}"
        rm "$f"
        copyattr -d "$u" dst/system/packages/
        case ${f##*/} in haiku_loader-*) loader=yes ;; esac
    done
    (cd dst; configure)
    makebootable dst
    df dst | grep -E '(Used|Free) Blocks' || true
    sync
    unmount dst
    mounts=("${mounts[@]:1}")   # dst was mounted last: first in the list
    echo yes | writembr "$dst_dev/raw" >writembr.log 2>&1 || { cat writembr.log >&2; die "writembr failed"; }
    # An updated haiku_loader package: the ESP's loader to match (BIOS boots
    # take theirs from the package itself).  This VM's is that package's.
    if [ $loader = yes ]; then
        efi=/boot/system/data/platform_loaders/haiku_loader.efi
        [ -f $efi ] || die "this VM has no $efi"
        mkdir -p esp
        mnt -t fat "$dst_dev/1" esp
        [ -f esp/EFI/BOOT/BOOTX64.EFI ] || die "the ESP has no EFI/BOOT/BOOTX64.EFI"
        cp $efi esp/EFI/BOOT/BOOTX64.EFI
        sync
        unmount esp
        mounts=("${mounts[@]:1}")
    fi
    diskimage unregister out/image.img >/dev/null
    disks=("${disks[@]:1}")
}

# Make the system in the current directory (dst/) BackOtter's.
configure() {
    printf 'backotter\n' >system/settings/network/hostname

    # sshd: off until remote-access starts it (net_server starts what
    # network/services lists, and the official image lists sshd); BackOtter's
    # settings first (sshd keeps the first value it reads); no host keys
    # (remote-access makes them, and the boot job deletes them).
    grep -q '^service ssh' system/settings/network/services || die "no sshd in network/services"
    printf '# BackOtter rescue stick: nothing here; remote-access starts sshd.\n' \
        >system/settings/network/services
    { cat ../in/sshd_config.backotter; echo; cat system/settings/ssh/sshd_config; } >sshd_config.new
    mv sshd_config.new system/settings/ssh/sshd_config
    rm -f system/settings/ssh/ssh_host_*

    # The kernel's log stays off the serial port, as Haiku has it by default:
    # pc_serial leaves out the port the kernel uses, and with it off COM1 is
    # /dev/ports/pc_serial0, where the boot job runs BackOtter's console.
    grep -q '^serial_debug_output false' home/config/settings/kernel/drivers/kernel ||
        die "the official image's kernel settings don't turn serial debug output off"

    # At every boot, before anything else of ours: the boot job (forgets the
    # last boot's remote access, then runs the console on COM1).
    mkdir -p system/settings/launch
    cat >system/settings/launch/backotter <<EOF
# BackOtter rescue stick: see /boot/$lib/boot.
job x-vnd.BackOtter-boot {
	launch /bin/bash /boot/$lib/boot
	requires x-vnd.Haiku-registrar
}
EOF
    # Straight to the desktop: without "Locale settings", a writable boot
    # volume gets the welcome window (language, keymap, "Install Haiku"),
    # as a read-only one always does.  This VM's, from "Try Haiku" with its
    # defaults (English, US-International), as testvm/haiku/install.sh copies
    # them into the test guest.
    for f in "Locale settings" Key_map; do
        [ -f "/boot/home/config/settings/$f" ] || die "this VM has no ~/config/settings/$f"
        copyattr -d "/boot/home/config/settings/$f" home/config/settings/
    done
    # On the desktop: the console in a Terminal window.
    cat >>home/config/settings/boot/UserBootscript <<EOF

# BackOtter rescue stick: its console, in a Terminal window.
/boot/system/apps/Terminal -t BackOtter /bin/bash /boot/$lib/console &
EOF
    # Every shell here is uid 0's: a root prompt, as on the other images
    # (Haiku's own ends in "> ").
    mkdir -p system/non-packaged/data/profile.d
    cat >system/non-packaged/data/profile.d/backotter.sh <<'EOF'
# BackOtter rescue stick: every shell is uid 0's, so a root prompt.
PS1='\h:\w# '
EOF

    mkdir -p "$lib" system/non-packaged/bin .disk
    install -m 755 ../in/console ../in/remote-access ../in/boot ../in/on-serial.py "$lib/"
    install -m 755 ../in/rustic-bko ../in/bko-holt system/non-packaged/bin/
    install -m 444 ../in/backotter.json .disk/
}

case ${1:-} in
info) info ;;
assemble) assemble ;;
*) die "usage: build.sh info|assemble" ;;
esac
