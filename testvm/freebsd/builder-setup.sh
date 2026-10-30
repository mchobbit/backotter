#!/bin/sh
# Turn a fresh FreeBSD guest from make-freebsd.sh (ZFS root) into a builder
# for BackOtter's FreeBSD rescue stick.  Run once as root inside the guest,
# with the network up; running it again changes nothing.
#
# Usage: builder-setup.sh
#
# Installs the tools from packages (makefs, mkimg, gpart and mdconfig are in
# the base system already), caps the ZFS ARC so builds keep their memory, and
# makes /build: a dataset outside the boot environment, so rolling back the
# system keeps what was built.  The ARC cap takes effect at the next boot.

set -eu
PACKAGES="git rust go bash rsync xorriso python3 sudo"
ARC_MAX=2147483648              # 2 GiB
BUILD=zroot/build

zpool list zroot >/dev/null 2>&1 || { echo "builder-setup: no pool zroot" >&2; exit 1; }

export ASSUME_ALWAYS_YES=yes
pkg bootstrap -y >/dev/null 2>&1 || true   # 15.x has pkg already
pkg update
pkg install $PACKAGES

if ! grep -q '^vfs\.zfs\.arc\.max=' /boot/loader.conf; then
	printf '%s\n' '' '# BackOtter builder: leave the RAM to the compilers' \
		"vfs.zfs.arc.max=\"$ARC_MAX\"" >>/boot/loader.conf
fi

zfs list "$BUILD" >/dev/null 2>&1 ||
	zfs create -o mountpoint=/build -o compression=lz4 -o atime=off "$BUILD"

echo "builder-setup: done; reboot for the ARC cap"
