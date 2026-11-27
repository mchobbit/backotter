#!/bin/sh
# BackOtter's rescue image for OpenBSD: run by make-img-bsd.sh as root in a VM
# of OpenBSD with the comp set (cc), in a directory of its own holding in/
# and out/ (see make-img-bsd.sh's header, and README.md here).
#
#   build.sh info       KERNEL=, from the kernel the image gets
#   build.sh assemble   out/image.img
#   build.sh list       out/image.img's root, read-only, as a tar on stdout
#   build.sh room       out/image.img's root: its free space (BKO-ROOM)
#   build.sh refresh    out/image.img, the parent, with the binaries and the
#                       marker swapped in place, a new DUID in its disklabel
#                       and /etc/fstab (docs/design/img-speed.md §4.2): exit 3
#                       when they don't fit
#
# OpenBSD has no live system, so this makes one the way the installer makes
# a system: the base set, bsd.mp as /bsd, the etc set (inside base) for /etc,
# MAKEDEV for /dev.  Then the release's errata, as syspatch would install
# them: each in/syspatchNN-*.tgz in turn, its files over the root's (only
# those the root has: no X11, no comp set), its kernel objects into the
# base's relink kit, from which GENERIC.MP is linked anew (as syspatch's
# reorder_kernel does) whenever a patch changed the kernel.  The root stays read-only: /etc/rc is patched not to
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
CFG_START=34816 CFG_SIZE=524288         # marker.py's 256 MiB
ROOT_START=$((CFG_START + CFG_SIZE))    # 559104, 1 MiB aligned; to the end of the image
LIB=/usr/local/lib/backotter
BINS="restic-bko rustic-bko bko-holt"   # in /usr/local/bin
HEADROOM_MIN_KIB=65536                  # the root's free space: at least this (img-speed.md §4.2)
in=$PWD/in                              # (stick/bsd/refresh-lib.sh's)

# kernel_version KERNEL: as uname -rv prints it, "8.0 GENERIC.MP#84".
kernel_version() {
    grep -a -o 'OpenBSD [0-9.]* ([A-Z.]*) #[0-9]*' "$1" | head -n 1 |
        sed 's/^OpenBSD \([0-9.]*\) (\([A-Z.]*\)) /\1 \2/'
}

# headroom: the root's free space the headroom rule asks, in KiB: max(64 MiB,
# half the binaries' size).
headroom() {
    b=0
    for n in $BINS; do
        [ ! -f "in/$n" ] || b=$((b + ($(wc -c <"in/$n") + 1023) / 1024))
    done
    if [ $((b / 2)) -gt $HEADROOM_MIN_KIB ]; then echo $((b / 2)); else echo $HEADROOM_MIN_KIB; fi
}

# list: out/image.img's root (disklabel partition a), read-only, as a tar.
list() {
    mkdir -p lst/root
    vnd=$(vnconfig out/image.img)
    trap 'umount lst/root 2>/dev/null; vnconfig -u "$vnd" 2>/dev/null' EXIT
    mount -r "/dev/${vnd}a" lst/root
    tar -C lst -cf - root
    umount lst/root
    vnconfig -u "$vnd"
    trap - EXIT
}

# room: out/image.img's root, read-only: its free space (size less what is
# used) and the binaries' blocks, as refresh-lib.sh's BKO-ROOM line.
room() {
    . in/refresh-lib.sh
    mkdir -p rmt/root
    vnd=$(vnconfig out/image.img)
    trap 'umount rmt/root 2>/dev/null; vnconfig -u "$vnd" 2>/dev/null' EXIT
    mount -r "/dev/${vnd}a" rmt/root
    # shellcheck disable=SC2046
    set -- $(df -k rmt/root | awk 'NR == 2 { print $2 - $3, $2 }')
    # shellcheck disable=SC2086
    bko_room "$PWD/rmt/root" /usr/local/bin "$1" "$2" $BINS
    umount rmt/root
    vnconfig -u "$vnd"
    trap - EXIT
}

# fsck_clean WHEN: fsck_ffs -n -f of the root ($vnd's a), which must find
# nothing: no error, and no question it answered no to.
fsck_clean() {
    fsck_ffs -n -f "/dev/r${vnd}a" >fsck.out 2>&1 && ! grep -q '? no$' fsck.out ||
        { cat fsck.out >&2; die "fsck_ffs $1 the swap: the root is not clean"; }
    echo "fsck $1: $(tail -n 1 fsck.out)"
}

# refresh: out/image.img, the parent (make-img-bsd.sh puts it there), with the
# binaries and the marker swapped in place in its root (img-speed.md §4.2),
# and a new DUID (D2): in the disklabel (the label editor's i, its other
# lines checked to be the parent's) and in /etc/fstab, which mounts the root by
# it, rewritten in place.  The MBR, the ESP and the boot code stay as they
# are; the host writes config.img into its partition.  The release and the
# test copy are each refreshed from their own parent, so TEST plays no part.
refresh() {
    for f in backotter.json bko-holt refresh-lib.sh; do
        [ -e "in/$f" ] || die "no in/$f"
    done
    [ -f out/image.img ] || die "no out/image.img, the parent"
    . in/refresh-lib.sh
    mkdir -p mnt
    vnd=$(vnconfig out/image.img)
    trap 'umount mnt 2>/dev/null; vnconfig -u "$vnd" 2>/dev/null' EXIT
    disklabel "$vnd" >label.before
    old=$(sed -n 's/^duid: //p' label.before)
    [ -n "$old" ] && [ "$old" != 0000000000000000 ] || die "the parent's disklabel has no DUID"
    new=
    while [ -z "$new" ] || [ "$new" = "$old" ] || [ "$new" = 0000000000000000 ]; do
        new=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')
    done
    fsck_clean before
    mount "/dev/${vnd}a" mnt
    # The marker's kernel is the parent's (marker.py was given it): it must
    # still be the parent's /bsd.
    k=$(kernel_version mnt/bsd)
    m=$(sed -n 's/.*"kernel": "\([^"]*\)".*/\1/p' in/backotter.json)
    [ -n "$k" ] && [ "$k" = "$m" ] || die "the parent's /bsd is OpenBSD '$k', its marker says '$m'"
    echo "kernel: $k, as the parent's marker says"
    [ "$(cat mnt/etc/fstab)" = "$old.a / ffs ro 0 0" ] ||
        die "the parent's /etc/fstab doesn't mount the root by its DUID $old: $(cat mnt/etc/fstab)"
    # shellcheck disable=SC2046
    set -- $(df -k mnt | awk 'NR == 2 { print $2 - $3 }')
    rc=0
    # shellcheck disable=SC2086
    bko_fit "$PWD/mnt" /usr/local/bin "$1" $BINS || rc=$?
    if [ $rc -ne 0 ]; then
        umount mnt; vnconfig -u "$vnd"; trap - EXIT
        exit $rc
    fi
    BKO_BIN_MODE=755            # a binary the parent lacks, as assemble installs it
    # shellcheck disable=SC2086
    bko_swap "$PWD/mnt" /usr/local/bin $BINS || die "the swap failed"
    echo "$new.a / ffs ro 0 0" >mnt/etc/fstab       # (the same inode)
    echo "refresh: /etc/fstab mounts the root by $new"
    sync
    umount mnt
    # The label editor's i (disklabel -R would draw a DUID of its own, whatever
    # its file says).
    printf 'i\n%s\nw\nq\n' "$new" | disklabel -E "$vnd" >label.edit 2>&1 ||
        { cat label.edit >&2; die "disklabel -E couldn't set the DUID"; }
    disklabel "$vnd" >label.after
    [ "$(sed -n 's/^duid: //p' label.after)" = "$new" ] || { cat label.edit >&2; die "the disklabel's DUID isn't $new"; }
    sed '/^duid: /d' label.before >label.b
    sed '/^duid: /d' label.after >label.a
    cmp -s label.b label.a || { diff label.b label.a >&2 || :; die "the label editor changed more than the DUID"; }
    echo "disklabel: DUID $old -> $new, the rest as it was"
    fsck_clean after
    vnconfig -u "$vnd"
    trap - EXIT
}

# The parent's subcommands, which read no media.
cmd=${1:-}
case $cmd in
list) list; exit 0 ;;
room) room; exit 0 ;;
refresh) refresh; exit 0 ;;
esac

# The release's sets, checked again here against SHA256.sig with this
# system's copy of the release's key.
set -- in/base[0-9][0-9].tgz
[ $# -eq 1 ] && [ -f "$1" ] || die "need exactly one in/baseNN.tgz"
base=${1#in/}
r=${base#base}; r=${r%.tgz}
key=/etc/signify/openbsd-$r-base.pub
[ -f $key ] || die "no $key: this VM's OpenBSD doesn't know release $r"
(cd in && signify -C -q -p $key -x SHA256.sig "$base" bsd.mp) ||
    die "$base or bsd.mp doesn't match SHA256.sig"
patches=$(cd in && ls syspatch$r-*.tgz 2>/dev/null | sort) || true
if [ -n "$patches" ]; then
    skey=/etc/signify/openbsd-$r-syspatch.pub
    [ -f $skey ] || die "no $skey"
    # shellcheck disable=SC2086  # one file name per word
    (cd in && signify -C -q -p $skey -x syspatch-SHA256.sig $patches) ||
        die "a syspatch doesn't match its signed list"
fi

# --- The errata, and the kernel ---------------------------------------------------
# Once, by info; assemble runs in the same directory.  errata/ ends up with
# every patched file, later patches over earlier ones; bsd is the kernel.
if [ ! -f bsd ]; then
    rm -rf errata kit
    mkdir errata kit
    for p in $patches; do
        echo "Syspatch $p"
        tar -xzpf "in/$p" -C errata
    done
    kobj=errata/usr/share/relink/kernel/GENERIC.MP
    if [ -d $kobj ]; then
        echo "Relinking GENERIC.MP with the errata's objects"
        tar -xzpf "in/$base" -C kit ./usr/share/relink/kernel.tgz
        tar -xzf kit/usr/share/relink/kernel.tgz -C kit GENERIC.MP
        cp -p $kobj/* kit/GENERIC.MP/
        (cd kit/GENERIC.MP && make newbsd) >relink.log 2>&1 ||
            { tail -n 20 relink.log >&2; die "relinking the kernel failed"; }
        mv kit/GENERIC.MP/bsd bsd.new
    else
        cp in/bsd.mp bsd.new
    fi
    rm -rf kit errata/usr/share/relink
    mv bsd.new bsd
fi

case $cmd in
info)
    # As uname -rv prints it: "8.0 GENERIC.MP#84".
    k=$(kernel_version bsd)
    [ -n "$k" ] || die "no version string in the kernel"
    echo "KERNEL=$k"
    exit 0 ;;
assemble) ;;
*) die "usage: build.sh info|assemble|list|room|refresh" ;;
esac
command -v cc >/dev/null || die "no cc: the build VM needs OpenBSD's comp set"
for f in console remote-access sshd_config.backotter restic-bko bko-holt backotter.json config.img; do
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
install -o root -g wheel -m 600 bsd "$root/bsd"
# The errata's files, where the root has them (syspatch's install_file), and
# their records in /var/syspatch, so that syspatch -l lists them.
(cd errata && find . ! -type d ! -path './var/syspatch/*') | while read -r f; do
    [ -e "$root/$f" ] || [ -h "$root/$f" ] || continue
    rm -f "$root/$f"
    (cd errata && tar -cf - "$f") | tar -xpf - -C "$root"
done
[ ! -d errata/var/syspatch ] || (cd errata && tar -cf - ./var/syspatch) | tar -xpf - -C "$root"
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

# /mnt/bko-target: where bko-holt load mounts what it restores (its default
# --target), which the read-only root can't make at run time.
mkdir -p "$root$LIB" "$root/.disk" "$root/mnt/bko-target"
install -m 755 in/console in/remote-access "$root$LIB/"
install -m 755 in/restic-bko "$root/usr/local/bin/"
[ ! -f in/rustic-bko ] || install -m 755 in/rustic-bko "$root/usr/local/bin/"
install -m 755 in/bko-holt "$root/usr/local/bin/"
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
mfs /root -s 1g -o nodev,nosuid -P /root    # restic's and rustic's caches (a restore's), as FreeBSD's
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
# The root's size, plus an eighth for FFS's own and 32 MiB, in whole MiB; and
# more if that leaves the root less free space than the headroom rule asks
# (img-speed.md §4.2, D1: a refresh swaps the binaries in place).
kb=$(du -sk "$root" | cut -f1)
root_size=$(( (kb + kb / 8 + 32768) / 1024 * 2048 ))
img=$PWD/out/image.img
mnt=$PWD/mnt
mkdir -p "$mnt"

# make_image: $img with a root of $root_size sectors; $free, its free KiB.
make_image() {
total=$((ROOT_START + root_size))
rm -f "$img"
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
free=$(df -k "$mnt" | awk 'NR == 2 { print $2 - $3 }')
umount "$mnt"
vnconfig -u "$vnd"
trap - EXIT
}

want=$(headroom)
make_image
if [ "$free" -lt "$want" ]; then
    # The shortfall, in whole MiB and one more.
    root_size=$((root_size + ((want - free) / 1024 + 2) * 2048))
    echo "Headroom: $free KiB free in the root, under the rule's $want: made again with $((root_size / 2048)) MiB"
    make_image
    [ "$free" -ge "$want" ] || die "the root has $free KiB free, under the headroom rule's $want"
fi
echo "Headroom: $free KiB free in the root (the rule: $want)"
rm -rf "$root"
