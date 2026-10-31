#!/bin/bash
# Install Haiku onto a BackOtter test guest's disk, from Haiku's live desktop.
# make-haiku.sh serves this script, with @SERVER@ and @HOSTNAME@ filled in,
# and types "curl ... | bash" into a Terminal there.  Everything goes to a log
# that is sent back to make-haiku.sh's server at the end, with a status file;
# then the live system powers off, which ends QEMU.
#
# The SATA ports: 0 the live image (booted), 1 the guest's disk (one MBR
# partition of type 0xeb, from make-haiku.sh), 2 an untouched copy of the live
# image.  The live system's /boot/system is packagefs's view of the packages,
# not what is on the disk, so the files come from the untouched copy: its home
# and system directories, as Haiku's Installer copies them, leaving out the
# install media's extras (_packages_, _sources_) and its trash.

set -euo pipefail
server=@SERVER@
name=@HOSTNAME@
log=/tmp/backotter-install.log
status=/tmp/backotter-install.status

finish() {
    local rc=$?
    set +e
    if [ "$rc" -eq 0 ]; then echo OK; else echo "FAILED ($rc)"; fi >"$status"
    sync
    curl -fsS -T "$log" "$server/install.log"
    curl -fsS -T "$status" "$server/install.status"
    shutdown -q
}
trap finish EXIT
exec >"$log" 2>&1
set -x

# The live system's partition, e.g. /dev/disk/scsi/2/0/0/0: bus 2, port 0.
boot=$(df /boot | awk '$NF ~ /^\/dev\/disk\// { print $NF }')
bus=${boot%/*/*/*}
target=$bus/1/0
source=$bus/2/0
[ -e "$target/raw" ] && [ -e "$target/0" ] && [ -e "$source/0" ]

mkfs -t bfs -q "$target/0" Haiku
mkdir -p /Target /Source
mount -t bfs "$target/0" /Target
mount -ro -t bfs "$source/0" /Source
copyattr -d -r /Source/home /Source/system /Target/

# What "Try Haiku" set (language and keymap): without "Locale settings", the
# guest would start the welcome window again instead of the desktop.
copyattr -d "/boot/home/config/settings/Locale settings" \
    /boot/home/config/settings/Key_map /Target/home/config/settings/

printf '%s\n' "$name" >/Target/system/settings/network/hostname

mkdir -p /Target/home/config/settings/ssh
curl -fsS -o /Target/home/config/settings/ssh/authorized_keys "$server/authorized_keys"
chmod 600 /Target/home/config/settings/ssh/authorized_keys
# net_server starts sshd from network/services, but on the first boot that is
# before openssh's first-boot script has made the host keys, and sshd exits.
# So the desktop session's UserBootscript starts it too once the keys are
# there, unless net_server's is running.
cat >>/Target/home/config/settings/boot/UserBootscript <<'EOF'

# BackOtter test guest (make-haiku.sh): SSH on the first boot.
(
	for i in $(seq 120); do
		[ -f /boot/system/settings/ssh/ssh_host_ed25519_key ] && break
		sleep 1
	done
	ps | grep -q '[/]bin/sshd' || /bin/sshd
) &
EOF

# The kernel's log on the serial port, for vm-run --serial-log.
kernel=/Target/home/config/settings/kernel/drivers/kernel
sed -i 's/^serial_debug_output false/serial_debug_output true/' "$kernel"
grep -q '^serial_debug_output true' "$kernel"

makebootable /Target
echo yes | writembr "$target/raw"
sync
unmount /Target
unmount /Source
