#!/bin/bash
# Agama post-install script for the leap16 test guest (make-opensuse.sh
# leap), run in the installed system (chroot, no systemd running): what a
# test guest needs beyond Agama's own install.  Everything else (the disk
# layout, snapper, SELinux, the boot loader) is the product's default.
set -ux
# SSH in as root with the build's key (Agama wrote it to root's
# authorized_keys); firewalld's public zone lets SSH in, but say so.
systemctl enable sshd.service
command -v firewall-offline-cmd >/dev/null && firewall-offline-cmd --zone=public --add-service=ssh
# No desktop: boot to the text console.
systemctl set-default multi-user.target
exit 0
