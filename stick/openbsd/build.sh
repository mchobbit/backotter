#!/bin/sh
# BackOtter's rescue image for OpenBSD: run by make-img-bsd.sh as root in a VM
# of OpenBSD with the comp set (cc), in a directory of its own holding in/
# and out/ (see make-img-bsd.sh's header, and README.md here).
#
#   build.sh info       KERNEL=, from the release's bsd.mp
#   build.sh assemble   out/image.img
#
# OpenBSD has no live system, so this makes one the way the installer makes
# a system: the base set, bsd.mp as /bsd, the etc set (inside base) for /etc,
# MAKEDEV for /dev.  The root stays read-only: /etc/rc is patched not to
# remount it read-write nor relink the kernel, and /etc, /var, /dev, /tmp,
# /root and /home become memory file systems at boot.
#
# The image, in 512-byte sectors, an MBR (OpenBSD's) with:
#   slot 0  ESP, FAT, with efi/boot/BOOTX64.EFI and BOOTIA32.EFI  (UEFI)
#   slot 1  the config partition, FAT16, config.img as is
#   slot 3  OpenBSD's, active, with biosboot (BIOS) and the disklabel:
#           a = the root FFS, i = the ESP, j = the config partition

set -eu
umask 022
PATH=/sbin:/bin:/usr/sbin:/usr/bin
die() { echo "openbsd/build.sh: $*" >&2; exit 1; }

ESP_START=2048 ESP_SIZE=32768           # 1 MiB in, 16 MiB
CFG_START=34816 CFG_SIZE=65536          # marker.py's 32 MiB
ROOT_START=100352                       # to the end of the image
LIB=/usr/local/lib/backotter

# The release's sets, checked again here against SHA256.sig with this
# system's copy of the release's key.
cmd=${1:-}
set -- in/base[0-9][0-9].tgz
[ $# -eq 1 ] && [ -f "$1" ] || die "need exactly one in/baseNN.tgz"
base=${1#in/}
r=${base#base}; r=${r%.tgz}
key=/etc/signify/openbsd-$r-base.pub
[ -f $key ] || die "no $key: this VM's OpenBSD doesn't know release $r"
(cd in && signify -C -q -p $key -x SHA256.sig "$base" bsd.mp) ||
    die "$base or bsd.mp doesn't match SHA256.sig"

case $cmd in
info)
    # As uname -rv prints it: "8.0 GENERIC.MP#84".
    k=$(grep -a -o 'OpenBSD [0-9.]* ([A-Z.]*) #[0-9]*' in/bsd.mp | head -n 1 |
        sed 's/^OpenBSD \([0-9.]*\) (\([A-Z.]*\)) /\1 \2/')
    [ -n "$k" ] || die "no version string in bsd.mp"
    echo "KERNEL=$k"
    exit 0 ;;
assemble) ;;
*) die "usage: build.sh info|assemble" ;;
esac
command -v cc >/dev/null || die "no cc: the build VM needs OpenBSD's comp set"
for f in console remote-access sshd_config.backotter restic-bko backotter.json config.img; do
    [ -f in/$f ] || die "no in/$f"
done
[ "$(wc -c <in/config.img)" -eq $((CFG_SIZE * 512)) ] || die "config.img isn't $CFG_SIZE sectors"

# replace FILE LINE NEW: replace the one line that is LINE (leading blanks
# aside) with NEW, indented the same; an empty NEW deletes it.
replace() {
    awk -v old="$2" -v new="$3" '
        { s = $0; sub(/^[ \t]+/, "", s) }
        s == old { n++; if (new != "") print substr($0, 1, length($0) - length(s)) new; next }
        { print }
        END { exit (n != 1) }' "$1" >"$1.new" || die "$1: no single line \"$2\""
    cat "$1.new" >"$1"
    rm "$1.new"
}

# --- The root -------------------------------------------------------------------
root=$PWD/root
rm -rf "$root"
mkdir "$root"
echo "Extracting $base"
tar -xzpf "in/$base" -C "$root"
tar -xzpf "$root/var/sysmerge/etc.tgz" -C "$root"
# The objects for relinking the kernel and libraries at boot, which a
# read-only root can't do; sysmerge's copies of the etc set.
rm -rf "$root/usr/share/relink" "$root/var/sysmerge"/*
install -o root -g wheel -m 600 in/bsd.mp "$root/bsd"
(cd "$root/dev" && sh ./MAKEDEV all)
# rc feeds this to /dev/random at boot and writes a new one.  It's the same
# on every stick, so it adds no entropy (that comes from the boot loader and
# CPU); it's here so rc's first read doesn't fail.
dd if=/dev/random of="$root/var/db/host.random" bs=65536 count=1 status=none
chmod 600 "$root/var/db/host.random"

echo backotter >"$root/etc/myname"
cat >"$root/etc/rc.conf.local" <<'EOF'
# BackOtter rescue stick.  The root file system is read-only: no relinking.
library_aslr=NO
# Off until remote access is turned on from the console.
sshd_flags=NO
smtpd_flags=NO
sndiod_flags=NO
check_quotas=NO
EOF

# /etc/rc: keep the root read-only, set up the memory file systems instead;
# no host keys until remote access is turned on; no kernel relinking.
rc=$root/etc/rc
replace "$rc" 'mount -uw /' "sh $LIB/boot-setup	# BackOtter: the root stays read-only"
replace "$rc" 'chmod og-rwx /bsd' ''
replace "$rc" 'ln -fh /bsd /bsd.booted' ''
replace "$rc" '[[ -f $_ssh_pub ]] || _show_ssh_fp=true' ''
replace "$rc" 'ssh-keygen -A' ': # BackOtter: host keys are made when remote access is turned on'
replace "$rc" '/usr/libexec/reorder_kernel &' ''

# The console on the screen and the first serial port, without logging in;
# the other virtual terminals keep getty.  "local": no carrier needed.
awk -v lib=$LIB '
    $1 == "ttyC0" { printf "ttyC0\t\"%s/autologin\"\tvt220\ton  secure\n", lib; n++; next }
    $1 == "tty00" { printf "tty00\t\"%s/autologin 115200\"\tvt220\ton  secure local\n", lib; n++; next }
    { print }
    END { exit (n != 2) }' "$root/etc/ttys" >"$root/etc/ttys.new" || die "no ttyC0 or tty00 in /etc/ttys"
mv "$root/etc/ttys.new" "$root/etc/ttys"

{ cat in/sshd_config.backotter; echo; cat "$root/etc/ssh/sshd_config"; } >"$root/etc/ssh/sshd_config.new"
mv "$root/etc/ssh/sshd_config.new" "$root/etc/ssh/sshd_config"

# The boot loader and kernel stay on the screen, even for boot-test.py: with
# them on com0, QEMU's BIOS VGA gets no wsdisplay, so no screen console.
if [ "${TEST:-0}" = 1 ]; then
    echo 'set timeout 1' >"$root/etc/boot.conf"
fi

mkdir -p "$root$LIB" "$root/.disk"
install -m 755 in/console in/remote-access "$root$LIB/"
install -m 755 in/restic-bko "$root/usr/local/bin/"
[ ! -f in/rustic-bko ] || install -m 755 in/rustic-bko "$root/usr/local/bin/"
install -m 644 in/backotter.json "$root/.disk/"

cat >"$root$LIB/boot-setup" <<'EOF'
#!/bin/sh
# BackOtter rescue stick, OpenBSD: run by /etc/rc where it would remount the
# root read-write.  The root stays read-only; what must be writable becomes a
# memory file system (mfs: GENERIC has no tmpfs), filled from the root's copy,
# so each boot starts the same and nothing is kept.  The sizes are limits: an
# mfs takes memory as files fill it, and there's no swap, so /tmp gets a
# quarter of the memory.  A mount that fails says so and the others go on.
set -u
PATH=/sbin:/bin:/usr/sbin:/usr/bin
# mfs DIR OPTION...: an mfs on DIR.
mfs() {
    dir=$1
    shift
    mount_mfs "$@" swap "$dir" || echo "BackOtter: no memory file system on $dir: it stays read-only" >&2
}
# mount_mfs -P copies through a directory in /tmp: /tmp comes first.
mfs /tmp -s $(($(sysctl -n hw.physmem) / 4 / 512)) -o nodev,nosuid
chmod 1777 /tmp
mfs /etc -s 64m -o nodev,nosuid -P /etc
mfs /var -s 256m -o nodev,nosuid -P /var
mfs /dev -s 16m -o nosuid -P /dev           # ptys need a writable /dev
mfs /root -s 64m -o nodev,nosuid -P /root
mfs /home -s 256m -o nodev,nosuid           # for remote access's user

# DHCP and SLAAC on every network card; netstart, later in rc, starts them.
for i in $(ifconfig -a | sed -n 's/^\([a-z][a-z0-9]*\): flags=.*/\1/p'); do
    ifconfig "$i" | grep -q '^[[:space:]]*media:' || continue
    [ -e "/etc/hostname.$i" ] || printf 'inet autoconf\ninet6 autoconf\n' >"/etc/hostname.$i"
    chmod 640 "/etc/hostname.$i"
done
EOF
chmod 755 "$root$LIB/boot-setup"

cat >autologin.c <<'EOF'
/*
 * BackOtter rescue stick, OpenBSD: the console on a terminal without logging
 * in, as agetty --autologin does on Linux.  OpenBSD's getty(8) can't log in
 * by itself, so /etc/ttys runs this instead:
 *
 *	ttyC0	"/usr/local/lib/backotter/autologin"		vt220 on secure
 *	tty00	"/usr/local/lib/backotter/autologin 115200"	vt220 on secure local
 *
 * init(8) adds the terminal's name.  Like getty, this opens the terminal,
 * makes it the controlling terminal and stdin, stdout and stderr, and sets
 * sane modes (and the speed, if given); then it runs the console, which
 * init starts again whenever it exits.
 */
#define TTYDEFCHARS		/* ttydefchars[], from termios.h's ttydefaults.h */
#include <sys/types.h>

#include <err.h>
#include <fcntl.h>
#include <limits.h>
#include <paths.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>
#include <util.h>

#define CONSOLE	"/usr/local/lib/backotter/console"

int
main(int argc, char *argv[])
{
	struct termios t;
	char path[PATH_MAX];
	const char *errstr;
	long long speed = 0;
	int fd;

	if (argc != 2 && argc != 3)
		errx(1, "usage: autologin [speed] tty");
	if (argc == 3) {
		speed = strtonum(argv[1], 50, 4000000, &errstr);
		if (errstr != NULL)
			errx(1, "speed %s is %s", argv[1], errstr);
	}
	if (snprintf(path, sizeof(path), "%s%s", _PATH_DEV, argv[argc - 1]) >=
	    (int)sizeof(path))
		errx(1, "%s: name too long", argv[argc - 1]);
	if ((fd = open(path, O_RDWR)) == -1) {
		/* No such port here: don't have init retry every few seconds. */
		sleep(3600);
		err(1, "%s", path);
	}
	if (login_tty(fd) == -1)
		err(1, "%s", path);
	if (tcgetattr(0, &t) == -1)
		err(1, "%s", path);
	t.c_iflag = TTYDEF_IFLAG;
	t.c_oflag = TTYDEF_OFLAG;
	t.c_lflag = TTYDEF_LFLAG;
	t.c_cflag = TTYDEF_CFLAG | (t.c_cflag & (CLOCAL | CRTSCTS | MDMBUF));
	memcpy(t.c_cc, ttydefchars, sizeof(ttydefchars));
	if (speed != 0)
		cfsetspeed(&t, speed);
	if (tcsetattr(0, TCSAFLUSH, &t) == -1)
		err(1, "%s", path);
	setenv("TERM", "vt220", 1);
	execl(CONSOLE, "console", (char *)NULL);
	err(1, "%s", CONSOLE);
}
EOF
cc -O2 -Wall -Werror -o "$root$LIB/autologin" autologin.c -lutil

# --- The image ------------------------------------------------------------------
# The root's size, plus an eighth for FFS's own and 32 MiB, in whole MiB.
kb=$(du -sk "$root" | cut -f1)
root_size=$(( (kb + kb / 8 + 32768) / 1024 * 2048 ))
total=$((ROOT_START + root_size))
img=$PWD/out/image.img
mnt=$PWD/mnt
rm -f "$img"
mkdir -p "$mnt"
dd if=/dev/zero of="$img" bs=512 count=0 seek=$total status=none
vnd=$(vnconfig "$img")
cleanup() { umount "$mnt" 2>/dev/null || true; vnconfig -u "$vnd" 2>/dev/null || true; }
trap cleanup EXIT

fdisk -iy -b $ESP_SIZE@$ESP_START -f "$root/usr/mdec/mbr" "$vnd" >/dev/null
# fdisk -e asks: id, edit in CHS mode, offset, size.
printf '%s\n' "edit 3" A6 n $ROOT_START $root_size \
    "edit 1" 0E n $CFG_START $CFG_SIZE "flag 3" write quit | fdisk -e "$vnd" >/dev/null
got=$(fdisk "$vnd" | sed -n 's/^[ *]*\([0-3]\): \([0-9A-F][0-9A-F]\) .*\[ *\([0-9]*\): *\([0-9]*\) ].*/\1 \2 \3 \4/p' |
      tr '\n' ' ')
[ "$got" = "0 EF $ESP_START $ESP_SIZE 1 0E $CFG_START $CFG_SIZE 2 00 0 0 3 A6 $ROOT_START $root_size " ] ||
    die "the MBR came out as: $got"
fdisk "$vnd" | grep -q '^\*3: A6' || die "slot 3 isn't the active one"

# The disklabel: "a" over all of OpenBSD's slot; disklabel adds the other
# slots' FAT as i and j.
echo '/ 1M-* 100%' >disklabel.template
disklabel -w -A -T disklabel.template "$vnd"
got=$(disklabel "$vnd" | awk '$1 ~ /^[a-p]:$/ && $1 != "c:" { printf "%s %s %s %s ", $1, $2, $3, $4 }')
[ "$got" = "a: $root_size $ROOT_START 4.2BSD i: $ESP_SIZE $ESP_START MSDOS j: $CFG_SIZE $CFG_START MSDOS " ] ||
    die "the disklabel came out as: $got"
duid=$(disklabel "$vnd" | sed -n 's/^duid: //p')
[ -n "$duid" ] || die "the disklabel has no DUID"
# Mounted read-only by the kernel and never remounted; never checked.
echo "$duid.a / ffs ro 0 0" >"$root/etc/fstab"

newfs -t msdos "/dev/r${vnd}i" >/dev/null
mount -t msdos "/dev/${vnd}i" "$mnt"
mkdir -p "$mnt/efi/boot"
cp "$root/usr/mdec/BOOTX64.EFI" "$root/usr/mdec/BOOTIA32.EFI" "$mnt/efi/boot/"
umount "$mnt"

dd if=in/config.img of="/dev/r${vnd}j" bs=1m status=none

newfs -m 0 -o space "/dev/r${vnd}a" >/dev/null
mount "/dev/${vnd}a" "$mnt"
echo "Copying $((kb / 1024)) MiB into the root file system"
(cd "$root" && pax -rw -pe . "$mnt")
installboot -r "$mnt" "$vnd" "$root/usr/mdec/biosboot" "$root/usr/mdec/boot"
df -h "$mnt" | tail -n 1
umount "$mnt"
vnconfig -u "$vnd"
trap - EXIT
rm -rf "$root"
