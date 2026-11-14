#!/bin/bash
# openSUSE MicroOS test guest for BackOtter: the combustion script of the
# image's first boot (make-opensuse.sh microos hands it over in QEMU's
# fw_cfg, opt/org.opensuse.combustion/script; docs/design/aeon.md).
# Combustion runs it in the first boot's initrd, inside `transactional-update
# shell`: a new snapshot of the image's root, writable, which becomes the
# default when this exits 0 (and is thrown away otherwise).  No network: it
# needs none.
#
# make-opensuse.sh fills in the @...@ settings, as autoinst.xml's.
set -euo pipefail

echo '@HOSTNAME@' >/etc/hostname
echo 'root:@ROOT_PASSWORD_HASH@' | chpasswd -e
install -d -m 700 /root/.ssh
echo '@AUTHORIZED_KEY@' >/root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
systemctl enable sshd.service

# No update, and no reboot for one, in the middle of a test: vm-update
# updates the guest.  health-checker stays on: it judges every boot.  A
# preset of our own too: the first boot (an uninitialized machine-id) runs
# `systemctl preset-all` after this, which would enable them again by
# openSUSE's presets (2026-11-14: both enabled with the disable alone).
mkdir -p /etc/systemd/system-preset
cat >/etc/systemd/system-preset/00-backotter.preset <<'EOF'
disable transactional-update.timer
disable rebootmgr.service
EOF
systemctl disable transactional-update.timer rebootmgr.service
systemctl enable health-checker.service

# GRUB's menu on the serial line too (the image has gfxterm only), so its
# snapshot menu can be driven from there; the kernel's command line has the
# serial console already.  grub.cfg is the new snapshot's own.
sed -i -e '/^GRUB_TERMINAL\(_INPUT\|_OUTPUT\)\?=/d' -e '/^GRUB_SERIAL_COMMAND=/d' /etc/default/grub
cat >>/etc/default/grub <<'EOF'
GRUB_TERMINAL="serial console"
GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"
EOF
grub2-mkconfig -o /boot/grub2/grub.cfg

echo BACKOTTER-INSTALL-OK >/dev/ttyS0
