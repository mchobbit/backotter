#!/bin/bash
# Agama post-install script for the leap16 test guest (make-opensuse.sh
# leap), run in the installer's live system with the new system at /mnt:
# what the install made, on the serial console (install-serial.log), then
# the line make-opensuse.sh waits for.  The live kernel has no serial
# console of its own, so this writes to /dev/ttyS0 itself.
exec >/dev/ttyS0 2>&1
t=/mnt
fact() { echo "== $1"; shift; "$@" 2>&1 | sed 's/^/   /'; }
fact lsblk lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,PARTTYPENAME,PARTLABEL,MOUNTPOINTS
fact sfdisk sfdisk -d /dev/vda
fact subvolumes btrfs subvolume list -pqu "$t"
fact default btrfs subvolume get-default "$t"
fact fstab cat "$t/etc/fstab"
fact default-grub cat "$t/etc/default/grub"
fact sysconfig-bootloader cat "$t/etc/sysconfig/bootloader"
fact esp find "$t/boot/efi" -type f -printf '%p %s\n'
fact efibootmgr efibootmgr -v
fact selinux-config grep -v '^#' "$t/etc/selinux/config"
fact relabel ls -l "$t/.autorelabel" "$t/etc/selinux/.autorelabel"
fact packages rpm --root "$t" -q kernel-default shim grub2 grub2-x86_64-efi snapper selinux-policy-targeted dracut openssh-server firewalld NetworkManager
fact snapper-configs ls "$t/etc/snapper/configs"
fact os-release cat "$t/etc/os-release"
echo BACKOTTER-INSTALL-OK
exit 0
