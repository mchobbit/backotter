#!/bin/bash
# PCLinuxOS (docs/design/pclinuxos.md): the whole install script that
# make-bootstrap.sh hands to the Arch ISO's live system for DISTRO pclinuxos
# and pclinuxos-bios.  Not preceded by common.sh: testvm/pclinuxos/install.sh
# partitions the disk itself, as PCLinuxOS's installer does, so that the
# guests and the A100 (realworld/pclinuxos-bootstrap.sh) install the same way.
#
# The PCLinuxOS ISO is the VM's second CD (/dev/sr1); install.sh and the
# pinned openssh-server RPM come from make-bootstrap.sh's server.  The
# install VM boots by BIOS (-machine pc, no OVMF), so the pclinuxos guest's
# GRUB goes in with --no-nvram and at the fallback path EFI/BOOT/BOOTX64.EFI,
# and has no NVRAM entry of its own.

set -eEuo pipefail
exec > >(tee /dev/@SERIAL_TTY@) 2>&1
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"; sync; sleep 2; poweroff' ERR

DISTRO='@DISTRO@'
FIRMWARE='@FIRMWARE@'
LAYOUT='@LAYOUT@'                      # install.sh's: auto, manual-xfs
GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
SERVER='@SERVER@'
SSH_RPM='@SSH_RPM@'
SSH_RPM_SUM='@SSH_RPM_SUM@'
DISK=/dev/vda

echo "BACKOTTER-INSTALL-START ($DISTRO, $FIRMWARE, $LAYOUT)"
systemctl stop reflector.service 2>/dev/null || true
for i in $(seq 30); do [[ -b /dev/sr1 ]] && break; sleep 1; done
[[ -b /dev/sr1 ]] || { echo "no /dev/sr1 (the PCLinuxOS ISO)"; false; }
mkdir -p /root/pclos
curl -fsS -o /root/pclos/install.sh "$SERVER/install-pclinuxos.sh"
curl -fsS -o "/root/pclos/$SSH_RPM" "$SERVER/$SSH_RPM"
opts=(--firmware "$FIRMWARE" --hostname "$GUEST_NAME" --user tester
    --password-hash "$ROOT_PASSWORD_HASH" --timezone UTC
    --rpm "/root/pclos/$SSH_RPM" "$SSH_RPM_SUM"
    --authorized-key "$AUTHORIZED_KEY" --serial)
[[ $FIRMWARE == uefi ]] && opts+=(--no-nvram)
bash /root/pclos/install.sh "${opts[@]}" "$DISK" "$LAYOUT" /dev/sr1 </dev/null
sync
echo "BACKOTTER-INSTALL-OK"
poweroff
