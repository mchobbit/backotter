#!/bin/sh
# Install a BackOtter GhostBSD test guest from GhostBSD's live system: run as
# root there, from the install media, by the command make-ghostbsd.sh types
# on the live system's serial console.  Install Station, GhostBSD's
# installer, is graphical only; this runs its backend, pc-sysinstall, with
# the configuration beside this script, and says how it went on the console,
# which make-ghostbsd.sh reads.

here=$(dirname "$(realpath "$0")")
mkdir -p /tmp/bko
cp "$here/pcinstall.cfg" "$here/post-install.sh" /tmp/bko/
cd /tmp
# pc-sysinstall's log goes to the console as it's written, but for the
# copy's line per file (cpdup's "copy-ok", "hardlink: linked"), hundreds of
# thousands of them.
log=/tmp/.pc-sysinstall/pc-sysinstall.log
tail -F "$log" 2>/dev/null | grep --line-buffered -Ev -- '-ok$|: linked$' &
# post-install.sh's output, and whether it finished, only in pc-sysinstall's
# output.
out=/tmp/bko/pc-sysinstall.out
if /usr/local/sbin/pc-sysinstall -c /tmp/bko/pcinstall.cfg >$out 2>&1 &&
	grep -q '^BACKOTTER-POST-INSTALL: OK' $out; then
	# UFS trims what it frees (newfs -t), but in pieces smaller than the
	# image's clusters, which then stay: 3.3 GB of image for 0.9 in use.
	# fsck_ffs -E trims the free room in whole runs, with / unmounted (as
	# pc-sysinstall's "umount -f" should have left it; it doesn't check).
	if [ "$(fstyp /dev/vtbd0p2 2>/dev/null)" = ufs ]; then
		umount /mnt 2>/dev/null
		if mount -p | awk '$2 == "/mnt" { found = 1 } END { exit !found }'; then
			echo "install.sh: / is busy, so the free room is not trimmed"
		elif ! fsck_ffs -E -y /dev/vtbd0p2; then
			sleep 2
			echo "BACKOTTER-INSTALL: FAILED (fsck_ffs of /)"
			poweroff
			exit 1
		fi
	fi
	sleep 2
	echo "BACKOTTER-INSTALL: OK"
	reboot
else
	sleep 2
	grep -Ev -- '-ok$|: linked$' $out | tail -n 30
	echo "BACKOTTER-INSTALL: FAILED"
	poweroff
fi
