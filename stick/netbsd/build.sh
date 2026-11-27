#!/bin/sh
# NetBSD's rescue image: make-img-bsd.sh's build.sh, run as root in a NetBSD
# VM of the same release, in a fresh directory with the sets in in/.  See
# README.md for the layout and the boot sequence.
#
#   build.sh info       KERNEL=, the kernel's release as uname -r prints it
#   build.sh assemble   out/image.img
#   build.sh list       out/image.img's root, read-only, as a tar on stdout
#   build.sh room       out/image.img's root: its free space (BKO-ROOM)
#   build.sh refresh    out/image.img, the parent, with the binaries and
#                       the marker swapped in place (docs/design/img-speed.md
#                       §4.2): exit 3 when they don't fit
#
# Follows NetBSD's own recipe for its USB images
# (src/distrib/common/bootimage/Makefile.bootimage): the sets extracted into a
# directory, makefs for the FFSv2 root and the EFI system partition, gpt for
# the table, installboot and gpt biosboot for BIOS boot.  No vnd, no
# first-boot resize.  Run as root, so the extracted files keep their owners
# and makefs needs no mtree spec.

set -eu
set -o pipefail                 # the sets go through xz | tar
PATH=/sbin:/usr/sbin:/bin:/usr/bin
export PATH
LC_ALL=C
export LC_ALL

top=$(pwd)
in=$top/in
out=$top/out
die() { echo "build.sh: $*" >&2; exit 1; }

SECTOR=512
MIB=2048                        # sectors
ESP_MIB=64                      # FAT32 needs 65525 clusters: 33 MiB of 512 bytes
CONFIG_PART=3                   # as platform.conf says
CONFIG_SECTORS=524288           # marker.py's, 256 MiB
RW=/.backotter-rw               # the tmpfs behind /etc, /var, /root and /home
BINS="restic-bko rustic-bko bko-holt"   # in /usr/local/bin
HEADROOM_MIN_KIB=65536          # the root's free space: at least this (img-speed.md §4.2)

# kernel_release KERNEL: "11.1" from "NetBSD 11.1 (GENERIC) #0: ..." in its
# version string, as uname -r prints it.
kernel_release() {
    grep -a -o -m 1 'NetBSD [0-9][0-9A-Za-z._-]* ([A-Z0-9_]*) #' "$1" | awk '{ print $2; exit }'
}

info() {
    xz -dc "$in/kern-GENERIC.tar.xz" | tar -C "$top" -xf - ./netbsd
    k=$(kernel_release "$top/netbsd")
    rm -f "$top/netbsd"
    [ -n "$k" ] || die "no version string in kern-GENERIC's netbsd"
    echo "KERNEL=$k"
}

# configure ROOT: everything the image adds or changes in the extracted sets.
configure() {
    r=$1

    # /dev: only MAKEDEV.  With no /dev/console, init makes /dev a tmpfs and
    # runs MAKEDEV in it, as it does on NetBSD's install media, so the
    # read-only root needs no device nodes.
    [ -f "$r/dev/MAKEDEV" ] || die "no /dev/MAKEDEV in the sets"
    find "$r/dev" -mindepth 1 -maxdepth 1 ! -name 'MAKEDEV*' -exec rm -rf {} +

    # /mnt/bko-target: where bko-holt load mounts what it restores (its
    # default --target), which the read-only root can't make at run time.
    mkdir -p "$r$RW" "$r/home" "$r/kern" "$r/proc" "$r/var/shm" "$r/.disk" \
        "$r/usr/local/bin" "$r/usr/local/lib/backotter" "$r/mnt/bko-target"

    cat >"$r/etc/fstab" <<'EOF'
# BackOtter rescue stick: / is read-only, and never checked or written.
# /etc, /var, /root and /home are copies in memory (/etc/rc.d/backotter_rw).
ROOT.		/		ffs	ro		0 0
tmpfs		/tmp		tmpfs	rw,-m1777,-sram%25
tmpfs		/var/shm	tmpfs	rw,-m1777,-sram%25
kernfs		/kern		kernfs	rw
ptyfs		/dev/pts	ptyfs	rw
procfs		/proc		procfs	rw
EOF

    # Copies /etc, /var, /root and /home into a tmpfs and null-mounts the
    # copies over them, right after rc.d/root and before anything writes.
    cat >"$r/etc/rc.d/backotter_rw" <<EOF
#!/bin/sh
#
# BackOtter rescue stick: the root file system is read-only, so give /etc,
# /var, /root and /home writable copies in memory.  NetBSD has no switch for
# this: copy each into a tmpfs and null-mount the copy over it.
#
# PROVIDE: backotter_rw
# REQUIRE: root
# BEFORE: devpubd zfs ccd rndctl cgd lvm raidframe sysctl

\$_rc_subr_loaded . /etc/rc.subr

name="backotter_rw"
start_cmd="backotter_rw_start"
stop_cmd=":"

backotter_rw_start()
{
	rw=$RW
	# At most half the memory: there's no swap to fall back on.
	mount -t tmpfs -o -m755,-sram%50 tmpfs \$rw || stop_boot
	for d in etc var root home; do
		mkdir \$rw/\$d &&
		(cd /\$d && pax -rw -pe . \$rw/\$d) &&
		mount -t null \$rw/\$d /\$d || {
			# Half of them read-only is no system to rescue with.
			echo "\$name: no copy of /\$d in memory"
			stop_boot
		}
	done
}

load_rc_config \$name
run_rc_command "\$1"
EOF
    chmod 555 "$r/etc/rc.d/backotter_rw"

    cat >>"$r/etc/rc.conf" <<'EOF'

# BackOtter rescue stick.
rc_configured=YES
hostname=backotter
dhcpcd=YES		dhcpcd_flags="-qMb"	# in the background: don't wait for a lease
sshd=NO						# remote-access starts it
entropy=""					# don't wait for entropy at boot
no_swap=YES
wscons=YES					# ttyE1, root's shell
savecore=NO
quota=NO
update_motd=NO
makemandb=NO
virecover=NO
postfix=NO
inetd=NO
cron=NO
EOF

    # The console on the screen (ttyE0) and the serial port (tty00), with a
    # root shell on ttyE1, as the Linux image has on tty2.  getty logs root in
    # (al=) and runs the console as its login program (lo=).
    cat >>"$r/etc/gettytab" <<'EOF'

# BackOtter rescue stick.
bko.Pc|BackOtter console on the screen:\
	:al=root:lo=/usr/local/lib/backotter/console:tc=Pc:
bko.serial|BackOtter console on the serial port:\
	:al=root:lo=/usr/local/lib/backotter/console:tc=std.115200:
bko.shell|root shell on the screen:\
	:al=root:tc=Pc:
EOF
    awk '
        $1 == "console" || $1 == "constty" { sub(/[ \t]on[ \t]/, " off ") }
        $1 == "ttyE0" { $0 = "ttyE0\t\"/usr/libexec/getty bko.Pc\"\t\twsvt25\ton secure" }
        $1 == "ttyE1" { $0 = "ttyE1\t\"/usr/libexec/getty bko.shell\"\twsvt25\ton secure" }
        $1 == "tty00" { $0 = "tty00\t\"/usr/libexec/getty bko.serial\"\tvt100\ton secure" }
        { print }' "$r/etc/ttys" >"$r/etc/ttys.new"
    mv "$r/etc/ttys.new" "$r/etc/ttys"
    # wscons makes screen 0 (ttyE0) only when it's the kernel's console: add
    # it otherwise (when it's there, wsconscfg fails, quietly).
    cat >>"$r/etc/rc.local" <<'EOF'

# BackOtter rescue stick: the console on the screen, ttyE0, even when the
# kernel's console is the serial port.
wsconscfg -e vt100 0 2>/dev/null
EOF
    for t in ttyE0 ttyE1 tty00; do
        grep -q "^$t[[:space:]].*[[:space:]]on[[:space:]]" "$r/etc/ttys" || die "/etc/ttys: $t isn't on"
    done

    # sshd: BackOtter's lines first (sshd takes the first value it reads).
    # No host keys: remote-access makes this boot's.
    { cat "$in/sshd_config.backotter"; echo; cat "$r/etc/ssh/sshd_config"; } >"$r/etc/ssh/sshd_config.new"
    mv "$r/etc/ssh/sshd_config.new" "$r/etc/ssh/sshd_config"
    rm -f "$r"/etc/ssh/ssh_host_*

    # TLS trust anchors, which certctl_init would otherwise set up at first
    # boot; the links point into /usr/share/certs, the same in the image.
    certctl -c "$r/etc/openssl/certs" rehash
    pwd_mkdb -d "$r" -p "$r/etc/master.passwd"

    install -m 555 "$in/console" "$in/remote-access" "$r/usr/local/lib/backotter/"
    install -m 555 "$in/restic-bko" "$r/usr/local/bin/"
    [ ! -f "$in/rustic-bko" ] || install -m 555 "$in/rustic-bko" "$r/usr/local/bin/"
    install -m 555 "$in/bko-holt" "$r/usr/local/bin/"
    install -m 444 "$in/backotter.json" "$r/.disk/backotter.json"

    # BIOS boot's second stage, and the boot menu (for UEFI too).
    cp "$r/usr/mdec/boot" "$r/boot"
    {
        echo "banner=BackOtter rescue stick, NetBSD $(kernel_release "$r/netbsd")"
        if [ "${TEST:-0}" = 1 ]; then
            echo "consdev=com0,115200"
        fi
        echo "menu=Boot BackOtter:boot netbsd"
        echo "menu=Boot BackOtter, console on the serial port:consdev com0,115200;boot netbsd"
        echo "menu=Boot BackOtter single-user:boot netbsd -s"
        echo "menu=Boot prompt:prompt"
        echo "default=1"
        echo "timeout=$([ "${TEST:-0}" = 1 ] && echo 1 || echo 5)"
    } >"$r/boot.cfg"
}

assemble() {
    for f in base.tar.xz etc.tar.xz kern-GENERIC.tar.xz backotter.json config.img console \
        remote-access sshd_config.backotter restic-bko bko-holt; do
        [ -e "$in/$f" ] || die "no in/$f"
    done
    [ "$(stat -L -f %z "$in/config.img")" -eq $((CONFIG_SECTORS * SECTOR)) ] ||
        die "config.img isn't $CONFIG_SECTORS sectors"
    r=$top/root
    rm -rf "$r" "$top/esp"
    mkdir -p "$r" "$top/esp/EFI/BOOT" "$out"
    for s in "$in"/*.tar.xz; do
        echo "Extracting $(basename "$s")"
        xz -dc "$s" | tar -C "$r" -xpf -
    done
    configure "$r"

    echo "Making the file systems"
    # 10% free blocks and inodes: room for nothing in particular, as / is
    # read-only, but makefs's minimum is too tight for fsck's liking.  And
    # the headroom a refresh needs to swap the binaries in place
    # (img-speed.md §4.2, D1): max(64 MiB, half the binaries' size) free,
    # the shortfall added (makefs adds a -b SIZE to the size before the 10%).
    makefs -t ffs -o version=2 -b 10% -f 10% "$top/root.img" "$r" >/dev/null
    want=$(headroom)
    free=$(fs_free "$top/root.img")
    if [ "$free" -lt "$want" ]; then
        makefs -t ffs -o version=2 -b 10% -b $((want - free + 1024))k -f 10% "$top/root.img" "$r" >/dev/null
        free=$(fs_free "$top/root.img")
        [ "$free" -ge "$want" ] || die "the root has $free KiB free, under the headroom rule's $want"
    fi
    echo "Headroom: $free KiB free in the root (the rule: $want)"
    installboot -m amd64 "$top/root.img" "$r/usr/mdec/bootxx_ffsv2"
    cp "$r/usr/mdec/bootx64.efi" "$top/esp/EFI/BOOT/BOOTX64.EFI"
    cp "$r/usr/mdec/bootia32.efi" "$top/esp/EFI/BOOT/BOOTIA32.EFI"
    makefs -t msdos -o fat_type=32,sectors_per_cluster=1,volume_label=EFI -s ${ESP_MIB}m "$top/esp.img" "$top/esp" >/dev/null

    # The table: EFI system partition, root, config; each 1 MiB aligned.
    root_s=$(( ($(stat -f %z "$top/root.img") / SECTOR + MIB - 1) / MIB * MIB ))
    esp_b=$MIB
    root_b=$((esp_b + ESP_MIB * MIB))
    cfg_b=$((root_b + root_s))
    total=$((cfg_b + CONFIG_SECTORS + MIB))      # room for the backup table
    img=$out/image.img
    rm -f "$img"
    dd if=/dev/zero of="$img" bs=$SECTOR count=1 seek=$((total - 1)) 2>/dev/null
    gpt -q create "$img"
    gpt -q add -i 1 -b $esp_b -s $((ESP_MIB * MIB)) -t efi -l "EFI system" "$img"
    gpt -q add -i 2 -b $root_b -s "$root_s" -t ffs -l "BackOtter root" "$img"
    gpt -q add -i $CONFIG_PART -b $cfg_b -s $CONFIG_SECTORS -t windows -l "BackOtter config" "$img"
    gpt -q biosboot -i 2 -c "$r/usr/mdec/gptmbr.bin" "$img"
    gpt -q set -a bootme -i 2 "$img"
    dd if="$top/esp.img" of="$img" bs=1m seek=$((esp_b / MIB)) conv=notrunc 2>/dev/null
    dd if="$top/root.img" of="$img" bs=1m seek=$((root_b / MIB)) conv=notrunc 2>/dev/null
    dd if="$in/config.img" of="$img" bs=1m seek=$((cfg_b / MIB)) conv=notrunc 2>/dev/null
    gpt show -l "$img"
    rm -rf "$r" "$top/esp" "$top/root.img" "$top/esp.img"
}

# headroom: the root's free space the headroom rule asks, in KiB: max(64 MiB,
# half the binaries' size).
headroom() {
    b=0
    for n in $BINS; do
        [ ! -f "$in/$n" ] || b=$((b + ($(wc -c <"$in/$n") + 1023) / 1024))
    done
    [ $((b / 2)) -gt $HEADROOM_MIN_KIB ] && echo $((b / 2)) || echo $HEADROOM_MIN_KIB
}

# fs_free FFS_IMAGE: the free KiB (its size less what is used) of the file system in a file.
fs_free() {
    v=
    for u in 0 1 2 3 4 5 6 7; do
        if vndconfig -c -r "vnd$u" "$1" 2>/dev/null; then v=vnd$u; break; fi
    done
    [ -n "$v" ] || die "no free vnd device"
    mkdir -p "$top/hr"
    mount -r -t ffs "/dev/${v}a" "$top/hr" || { vndconfig -u "$v"; die "can't mount $1"; }
    df -k "$top/hr" | awk 'NR == 2 { print $2 - $3 }'
    umount "$top/hr"
    vndconfig -u "$v"
}

# attach IMG [-r]: IMG on a free vnd ($vnd), and its root wedge ($wedge, the
# GPT partition "BackOtter root").
attach() {
    vnd= wedge=
    for u in 0 1 2 3 4 5 6 7; do
        if vndconfig -c ${2:-} "vnd$u" "$1" 2>/dev/null; then vnd=vnd$u; break; fi
    done
    [ -n "$vnd" ] || die "no free vnd device"
    for _ in 1 2; do
        wedge=$(dkctl "$vnd" listwedges 2>/dev/null | sed -n 's/^\(dk[0-9]*\): BackOtter root,.*/\1/p')
        [ -n "$wedge" ] && break
        dkctl "$vnd" makewedges >/dev/null 2>&1 || true
    done
    [ -n "$wedge" ] || { vndconfig -u "$vnd"; die "no BackOtter root wedge on $vnd"; }
}

# list: out/image.img's root, read-only, as a tar.
list() {
    attach "$out/image.img" -r
    trap 'umount "$top/lst/root" 2>/dev/null; vndconfig -u "$vnd" 2>/dev/null' EXIT
    mkdir -p "$top/lst/root"
    mount -r -t ffs "/dev/$wedge" "$top/lst/root"
    tar -C "$top/lst" -cf - root
    umount "$top/lst/root"
    vndconfig -u "$vnd"
    trap - EXIT
}

# room: out/image.img's root, read-only: its free space and the binaries' blocks,
# as refresh-lib.sh's BKO-ROOM line.
room() {
    . "$in/refresh-lib.sh"
    attach "$out/image.img" -r
    trap 'umount "$top/rm/root" 2>/dev/null; vndconfig -u "$vnd" 2>/dev/null' EXIT
    mkdir -p "$top/rm/root"
    mount -r -t ffs "/dev/$wedge" "$top/rm/root"
    # shellcheck disable=SC2046
    set -- $(df -k "$top/rm/root" | awk 'NR == 2 { print $2 - $3, $2 }')
    # shellcheck disable=SC2086
    bko_room "$top/rm/root" /usr/local/bin "$1" "$2" $BINS
    umount "$top/rm/root"
    vndconfig -u "$vnd"
    trap - EXIT
}

# fsck_clean WEDGE WHEN: fsck_ffs -n -f of the root, which must find nothing.
fsck_clean() {
    fsck_ffs -n -f "/dev/r$1" >"$top/fsck.out" 2>&1 ||
        { cat "$top/fsck.out" >&2; die "fsck_ffs $2 the swap: the root is not clean"; }
    echo "fsck $2: $(tail -n 1 "$top/fsck.out")"
}

# refresh: out/image.img, the parent (make-img-bsd.sh puts it there), with the
# binaries and the marker swapped in place in its root (img-speed.md §4.2).  The ESP, the boot code and
# the GPT stay as they are; the host writes config.img into its partition and
# gives the GPT new GUIDs.  The release and the test copy are each refreshed
# from their own parent, so TEST plays no part.
refresh() {
    for f in backotter.json bko-holt refresh-lib.sh; do
        [ -e "$in/$f" ] || die "no in/$f"
    done
    [ -f "$out/image.img" ] || die "no out/image.img, the parent"
    . "$in/refresh-lib.sh"
    mkdir -p "$top/mnt"
    attach "$out/image.img"
    trap 'umount "$top/mnt" 2>/dev/null; vndconfig -u "$vnd" 2>/dev/null' EXIT
    fsck_clean "$wedge" before
    mount -t ffs "/dev/$wedge" "$top/mnt"
    # The marker's kernel is the parent's (marker.py was given it): it must
    # still be the parent's /netbsd.
    k=$(kernel_release "$top/mnt/netbsd")
    m=$(sed -n 's/.*"kernel": "\([^"]*\)".*/\1/p' "$in/backotter.json")
    [ -n "$k" ] && [ "$k" = "$m" ] || die "the parent's /netbsd is NetBSD '$k', its marker says '$m'"
    echo "kernel: $k, as the parent's marker says"
    # shellcheck disable=SC2046
    set -- $(df -k "$top/mnt" | awk 'NR == 2 { print $2 - $3 }')
    rc=0
    # shellcheck disable=SC2086
    bko_fit "$top/mnt" /usr/local/bin "$1" $BINS || rc=$?
    if [ $rc -ne 0 ]; then
        umount "$top/mnt"; vndconfig -u "$vnd"; trap - EXIT
        exit $rc
    fi
    # shellcheck disable=SC2086
    bko_swap "$top/mnt" /usr/local/bin $BINS || die "the swap failed"
    umount "$top/mnt"
    fsck_clean "$wedge" after
    vndconfig -u "$vnd"
    trap - EXIT
}

case ${1:-} in
info) info ;;
assemble) assemble ;;
list) list ;;
room) room ;;
refresh) refresh ;;
*) die "usage: build.sh info|assemble|list|room|refresh" ;;
esac
