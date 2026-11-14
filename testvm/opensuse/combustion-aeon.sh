#!/bin/bash
# openSUSE Aeon test guest for BackOtter: the combustion script of the
# installer's first boot (make-opensuse.sh aeon hands it over in QEMU's
# fw_cfg, opt/org.opensuse.combustion/script; docs/design/aeon.md).  The
# installer is itself an Aeon system, and tik deploys it by copying its own
# running root (systemd-repart's CopyFiles=/, /root, /var...), so what this
# sets is the deployed guest's too: root's password and key, sshd, the host
# name, multi-user.target as the default (no GDM, so no GNOME session and no
# tik started in one: make-opensuse.sh runs tik over SSH), the updates off.
# For tik itself: the disk to install on.  Combustion runs this in the first
# boot's initrd, inside `transactional-update shell` (a new snapshot, which
# becomes the default when this exits 0).  No network: it needs none.
#
# make-opensuse.sh fills in the @...@ settings, as autoinst.xml's.
set -euo pipefail

echo '@HOSTNAME@' >/etc/hostname
echo 'root:@ROOT_PASSWORD_HASH@' | chpasswd -e
install -d -m 700 /root/.ssh
echo '@AUTHORIZED_KEY@' >/root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
systemctl enable sshd.service
systemctl set-default multi-user.target

# tik: the one disk to deploy to, so that its disk list asks nothing (the
# installer stick itself is USB, which tik leaves out anyway), by its
# serial (vm-run --disk-serials aeon): a disk with no /dev/disk/by-id name
# makes tik warn in a dialog it shows inside a $(...), where nobody sees its
# "Press any key", and wait for it for ever.  On a line of its own: the
# shipped file ends without a newline, in a comment.
printf '\nTIK_INSTALL_DEVICE="/dev/disk/by-id/virtio-aeon"\n' >>/etc/tik/config

# No update, and no reboot for one, in the middle of a test: vm-update
# updates the guest (Aeon has no rebootmgr; t-u's timer reboots by itself).
# A preset too, should a first boot run `systemctl preset-all`, as MicroOS's
# did (docs/design/aeon.md, "What M1 found").
mkdir -p /etc/systemd/system-preset /etc/systemd/user-preset
cat >/etc/systemd/system-preset/00-backotter.preset <<'EOF'
disable transactional-update.timer
disable update-system-flatpaks.timer
EOF
echo 'disable update-user-flatpaks.timer' >/etc/systemd/user-preset/00-backotter.preset
systemctl disable transactional-update.timer update-system-flatpaks.timer
systemctl --global disable update-user-flatpaks.timer

echo BACKOTTER-INSTALLER-READY >/dev/ttyS0
