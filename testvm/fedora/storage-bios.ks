# BIOS + MBR with /boot (ext4), swap, and a btrfs volume laid out the way
# Fedora's own installer does it: subvolumes "root" (/) and "home" (/home) on
# a volume labelled "fedora".
zerombr
clearpart --all --initlabel --disklabel=msdos --drives=vda
part /boot --fstype=ext4 --size=1024 --asprimary --ondisk=vda
part swap --size=2048 --asprimary --ondisk=vda
part btrfs.01 --fstype=btrfs --size=4096 --grow --asprimary --ondisk=vda
btrfs none --label=fedora btrfs.01
btrfs / --subvol --name=root fedora
btrfs /home --subvol --name=home fedora
# A serial console too, as on the other test guests.
bootloader --location=mbr --boot-drive=vda --append="console=tty0 console=ttyS0,115200n8"
