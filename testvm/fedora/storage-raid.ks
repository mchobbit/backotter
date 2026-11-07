# UEFI on two disks, everything mirrored, as Anaconda's custom partitioning
# makes it when each mount point is given RAID 1 across both: GPT on vda and
# vdb, each with three partitions:
#   /boot/efi  md RAID1 "boot_efi" (600 MiB members), FAT; Anaconda gives an
#              EFI system partition on RAID metadata 1.0, at the members'
#              ends, so the firmware reads either member as a plain FAT
#   /boot      md RAID1 "boot" (2 GiB members), XFS (Server's default)
#   /          btrfs RAID1 (data and metadata) across the rest of each disk,
#              labelled "fedora", subvolumes root (/) and home (/home)
# No swap partition: Fedora swaps to zram.
zerombr
clearpart --all --initlabel --disklabel=gpt --drives=vda,vdb
ignoredisk --only-use=vda,vdb
part raid.11 --size=600 --ondisk=vda
part raid.12 --size=600 --ondisk=vdb
part raid.21 --size=2048 --ondisk=vda
part raid.22 --size=2048 --ondisk=vdb
part btrfs.31 --fstype=btrfs --size=4096 --grow --ondisk=vda
part btrfs.32 --fstype=btrfs --size=4096 --grow --ondisk=vdb
raid /boot/efi --level=1 --device=boot_efi --fstype=efi raid.11 raid.12
raid /boot --level=1 --device=boot --fstype=xfs raid.21 raid.22
btrfs none --label=fedora --data=raid1 --metadata=raid1 btrfs.31 btrfs.32
btrfs / --subvol --name=root fedora
btrfs /home --subvol --name=home fedora
# A serial console too, as on the other test guests.
bootloader --boot-drive=vda --append="console=tty0 console=ttyS0,115200n8"

# The arrays' first resync, finished before the install ends, so the image's
# arrays are in sync (make-fedora.sh's QEMU writes zeros as holes).
%post --nochroot
for md in /dev/md/*; do mdadm --wait "$md" || true; done
cat /proc/mdstat
%end
