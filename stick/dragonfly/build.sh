#!/bin/sh
# BackOtter rescue image, DragonFly BSD: make-img-bsd.sh runs this as root in
# a DragonFly VM (see make-img-bsd.sh's header for the contract).
#
#   build.sh info      unpacks and checks the official USB image, prints KERNEL=
#   build.sh assemble  writes out/image.img
#
# The image is laid out like DragonFly's own USB image (nrelease's mkimg
# target): an MBR with
#   slice 1  type 0xEF, the official image's FAT32 EFI system partition, copied
#            as is (EFI/BOOT/BOOTX64.EFI is DragonFly's boot1.efi)
#   slice 2  type 0x6C, active, a disklabel64 with boot1_64/boot2_64 and one
#            UFS partition "a": the official image's system, read-only
#   slice 3  type 0x0E, BackOtter's config partition (marker.py's config.img,
#            byte for byte)
# Slice 2 is cut to its contents plus some room; the official one is 1.7 GB.
#
# Root is mounted read-only from part-by-label/BKO_<image id without its
# dashes>.a, a disklabel64 label of the image's own, so another DragonFly disk in the machine (the
# official media are all labelled DragonFly_v6.4.2) can't be taken for it.
# /etc, /var, /root and /tmp are tmpfs, the first three filled from the image
# at boot (mount_tmpfs -C).

set -eu
PATH=/sbin:/bin:/usr/sbin:/usr/bin
die() { echo "build.sh: $*" >&2; exit 1; }

ESP_START=63                    # as on the official image
HEADS=256 SECTORS=63            # nrelease's geometry: 16128-sector cylinders
CONFIG_SECTORS=65536            # marker.py's config.img
ROOM_MB=256                     # free space in the root file system
console_dir=usr/local/lib/backotter

vns=
mounts=
cleanup() {
    for m in $mounts; do umount "$m" 2>/dev/null || umount -f "$m" 2>/dev/null || true; done
    for v in $vns; do vnconfig -u "$v" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

# attach FILE VAR: the first free vn device, now backed by FILE, into VAR.
# Not echoed: in a $(...) subshell, vns wouldn't reach the cleanup trap.
attach() {
    for u in 0 1 2 3 4 5 6 7; do
        if vnconfig "vn$u" "$1" 2>/dev/null; then
            vns="vn$u $vns"
            eval "$2=vn\$u"
            return
        fi
    done
    die "no free vn device for $1"
}
mnt() { mount "$@"; eval "mounts=\"\${$#} \$mounts\""; }

# The official image, uncompressed and checked against md5.txt; then mounted
# read-only on src/ (its system, slice 2 "a") with vn device $src_vn.
official() {
    set -- in/*.img.bz2
    [ $# -eq 1 ] && [ -f "$1" ] || die "expected one *.img.bz2 in in/"
    img=${1%.bz2}
    if [ ! -f "$img" ]; then
        bunzip2 -c "$1" >"$img.part"
        mv "$img.part" "$img"
    fi
    want=$(sed -n "s/^MD5 ($(basename "$img")) = //p" in/md5.txt)
    [ -n "$want" ] || die "md5.txt has no $(basename "$img")"
    [ "$(md5 -q "$img")" = "$want" ] || die "$img doesn't match md5.txt"
    attach "$img" src_vn
    mkdir -p src
    mnt -o ro "/dev/${src_vn}s2a" src
}

info() {
    official
    kernel=$(strings src/boot/kernel/kernel | sed -n 's/^DragonFly v\([^ ]*\) .*/\1/p' | head -n 1)
    [ -n "$kernel" ] || die "no version string in the official image's kernel"
    echo "KERNEL=$kernel"
}

# json_field NAME FILE: a top-level string from marker.py's backotter.json.
json_field() { sed -n "s/^  \"$1\": \"\([^\"]*\)\",*\$/\1/p" "$2"; }

assemble() {
    image_id=$(json_field image_id in/backotter.json)
    [ -n "$image_id" ] || die "no image_id in in/backotter.json"
    label=BKO_$(echo "$image_id" | tr -d -)
    official

    # --- The layout ------------------------------------------------------------------
    # Slices 1 and 2 as on the official image: the same ESP, and slice 2 on
    # cylinder boundaries of nrelease's geometry (fdisk rounds to them), sized
    # for the system, our files and ROOM_MB, plus 2 MiB for the disklabel64
    # (its boot area, label and backup label).
    set -- $(fdisk -s "$src_vn" | awk '$1 == "1:" { print $2, $3 } $1 == "2:" { print $2 }')
    [ $# -eq 3 ] && [ "$1" -eq $ESP_START ] || die "the official image's slices aren't as expected"
    esp_sectors=$2 root_start=$3
    cyl=$((HEADS * SECTORS))
    [ $((ESP_START + esp_sectors)) -eq "$root_start" ] && [ $((root_start % cyl)) -eq 0 ] ||
        die "the official image's slice 2 isn't where expected"
    used_kb=$(du -skx src | cut -f1)
    ours_kb=$(du -skL in/restic-bko in/rustic-bko 2>/dev/null | awk '{ n += $1 } END { print n }')
    root_sectors=$(( ((used_kb + ours_kb) * 11 / 10 + (ROOM_MB + 2) * 1024) * 2 ))
    root_sectors=$(( (root_sectors + cyl - 1) / cyl * cyl ))
    config_start=$((root_start + root_sectors))
    total=$((config_start + CONFIG_SECTORS))
    truncate -s $((total * 512)) out/image.img
    attach out/image.img dst_vn

    # The MBR's boot code, then the slices.  Slice 3 can't end on a cylinder
    # boundary (65536 isn't a multiple of 63), so fdisk sets it alone with a
    # geometry of one-sector cylinders, which it then doesn't round to; its
    # CHS fields become all ones (LBA only), as for any slice past cylinder
    # 1023.  fdisk -f changes only the slices it's given.
    # Quietly: fdisk warns about the blank disk's table and the geometries.
    fdisk -b src/boot/mbr -IB "$dst_vn" >fdisk.log 2>&1 </dev/null || { cat fdisk.log >&2; die "fdisk -IB failed"; }
    cat >fdisk.conf <<EOF
g c$((total / cyl)) h$HEADS s$SECTORS
p 1 239 $ESP_START $esp_sectors
p 2 108 $root_start $root_sectors
p 3 0 0 0
p 4 0 0 0
a 2
EOF
    fdisk -f fdisk.conf "$dst_vn" >fdisk.log 2>&1 </dev/null || { cat fdisk.log >&2; die "fdisk -f failed"; }
    printf 'g c%s h1 s1\np 3 14 %s %s\n' $total $config_start $CONFIG_SECTORS >fdisk3.conf
    fdisk -f fdisk3.conf "$dst_vn" >fdisk.log 2>&1 </dev/null || { cat fdisk.log >&2; die "fdisk -f failed"; }
    got=$(fdisk -s "$dst_vn" | awk '$1 ~ /^[1-4]:$/ { print $1, $2, $3, $4, $5 }' | tr '\n' ' ')
    want="1: $ESP_START $esp_sectors 0xef 0x00 2: $root_start $root_sectors 0x6c 0x80 3: $config_start $CONFIG_SECTORS 0x0e 0x00 "
    [ "$got" = "$want" ] || die "fdisk made slices '$got', not '$want'"

    # --- Slice 1: the official ESP; slice 3: the config partition -----------------
    dd if="/dev/${src_vn}s1" of="/dev/${dst_vn}s1" bs=1m 2>/dev/null
    dd if=in/config.img of="/dev/${dst_vn}s3" bs=1m 2>/dev/null

    # --- Slice 2: disklabel64, boot code, UFS ---------------------------------------
    disklabel64 -w -r "${dst_vn}s2" auto
    disklabel64 -r "${dst_vn}s2" | sed "s/^label:.*/label: $label/" >label.txt
    echo 'a: * * 4.2BSD' >>label.txt
    disklabel64 -R -r "${dst_vn}s2" label.txt
    disklabel64 -B -b src/boot/boot1_64 -s src/boot/boot2_64 "${dst_vn}s2"
    newfs -m 0 "/dev/${dst_vn}s2a" >/dev/null
    mkdir -p dst
    mnt "/dev/${dst_vn}s2a" dst
    cpdup -i0 src dst
    (cd dst; configure)
    df -h dst | tail -n 1 | awk '{ print "Root file system: " $3 " used, " $4 " free" }'
}

# Make the system in the current directory (dst/) BackOtter's.
configure() {
    # Gone: the installer (a passwordless uid-0 account, and the prompt that
    # points at it); /firstboot, which rc removes (read-only here); and
    # rc.d/pfi, which at every boot looks for a pfi.conf on CDs and then on
    # slice 1 of da0, da1 and da8 (da0s1 is often the machine's own ESP),
    # and from one runs scripts and installs root's authorized_keys.
    sed -i '' -e '/^installer:/d' etc/master.passwd
    # Root needs no password at the console (getty starts BackOtter's console
    # as root); remote-access sets one for su.
    sed -i '' -e 's/^root::/root:*:/' etc/master.passwd
    pwd_mkdb -p -d etc etc/master.passwd
    printf '\nBackOtter rescue stick\n\n' >etc/issue
    rm -f firstboot etc/rc.d/pfi

    cat >etc/fstab <<EOF
/dev/part-by-label/$label.a	/	ufs	ro,noatime	0	0
tmpfs	/etc	tmpfs	rw,-C	0	0
tmpfs	/var	tmpfs	rw,-C	0	0
tmpfs	/root	tmpfs	rw,-C	0	0
tmpfs	/tmp	tmpfs	rw	0	0
proc	/proc	procfs	rw	0	0
EOF
    cat >>etc/rc.conf <<'EOF'

# BackOtter rescue stick: root stays read-only (/etc, /var, /root and /tmp
# are in memory), DHCP on every interface, sshd only once someone turns on
# remote access from the console.
hostname="backotter"
root_rw_mount="NO"
dhcpcd_enable="YES"
sshd_enable="NO"
dumpdev="NO"
EOF

    # The loader and the kernel's console on the screen, as DragonFly's
    # defaults have them: its kernel has one console, and boot_serial would
    # move it to COM1, leaving a PC's screen blank until BackOtter's console
    # (and a panic or mountroot prompt unseen).  Nothing before getty reaches
    # COM1 (boot2 uses it only if a /boot.config asks); getty runs BackOtter's
    # console there.
    {
        echo 'dm_load="YES"'
        echo "vfs.root.mountfrom=\"ufs:part-by-label/$label.a\""
        if [ "${TEST:-0}" = 1 ]; then echo 'autoboot_delay="2"'; fi
    } >boot/loader.conf

    # BackOtter's console instead of login, on the screen and COM1 (if the
    # machine has one), as root: getty's al= logs in by itself and runs lo=.
    # ttyv1-ttyv7 log root straight into a shell (DragonFly's own al.Pc), as
    # tty2-tty6 do on the Linux image; the official image has no ttyv1.
    cat >>etc/gettytab <<EOF

#
# BackOtter rescue stick: its console instead of login(1), as root.
#
bko.Pc:\\
	:al=root:lo=/$console_dir/console:tc=Pc:
bko.9600:\\
	:al=root:lo=/$console_dir/console:nc:tc=std.9600:
EOF
    awk -v OFS='\t' '
        /^ttyv0[ \t]/ {
            print "ttyv0", "\"/usr/libexec/getty bko.Pc\"", "cons25", "on  secure ifexists"
            for (i = 1; i <= 7; i++)
                print "ttyv" i, "\"/usr/libexec/getty al.Pc\"", "cons25", "on  secure ifexists"
            next
        }
        /^ttyv[1-7][ \t]/ { next }
        /^ttyd0[ \t]/ { print "ttyd0", "\"/usr/libexec/getty bko.9600\"", "dialup", "on  secure ifexists"; next }
        { print }' etc/ttys >etc/ttys.new
    mv etc/ttys.new etc/ttys
    [ "$(grep -c '^ttyv0.*getty bko.Pc"' etc/ttys)" = 1 ] && [ "$(grep -c '^ttyd0.*getty bko.9600"' etc/ttys)" = 1 ] &&
        [ "$(grep -c '^ttyv[1-7].*getty al.Pc"' etc/ttys)" = 7 ] || die "couldn't change etc/ttys"

    # sshd: BackOtter's settings first (sshd keeps the first value it reads),
    # and no host keys: remote-access makes them on the stick.
    { cat ../in/sshd_config.backotter; echo; cat etc/ssh/sshd_config; } >etc/ssh/sshd_config.new
    mv etc/ssh/sshd_config.new etc/ssh/sshd_config
    rm -f etc/ssh/ssh_host_*

    mkdir -p "$console_dir" .disk
    install -m 755 ../in/console ../in/remote-access "$console_dir/"
    install -m 755 ../in/restic-bko usr/local/bin/
    if [ -f ../in/rustic-bko ]; then install -m 755 ../in/rustic-bko usr/local/bin/; fi
    install -m 444 ../in/backotter.json .disk/
}

case ${1:-} in
info) info ;;
assemble) assemble ;;
*) die "usage: build.sh info|assemble" ;;
esac
