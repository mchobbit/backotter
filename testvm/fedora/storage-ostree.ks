# UEFI, laid out like the Thinkpad's Bazzite (docs/design/bazzite.md): GPT,
# a 600 MiB EFI system partition at /boot/efi, /boot (2 GiB ext4), and a
# btrfs volume labelled "fedora" with three top-level subvolumes: root (/,
# the physical root that holds ostree/repo and the deployments), home (/home,
# which the image links as /var/home) and var (/var).  No swap partition:
# zram only.
zerombr
clearpart --all --initlabel --disklabel=gpt --drives=vda
part /boot/efi --fstype=efi --size=600 --ondisk=vda
part /boot --fstype=ext4 --size=2048 --ondisk=vda
part btrfs.01 --fstype=btrfs --size=4096 --grow --ondisk=vda
btrfs none --label=fedora btrfs.01
btrfs / --subvol --name=root fedora
btrfs /home --subvol --name=home fedora
btrfs /var --subvol --name=var fedora
# A serial console too, as on the other test guests.
bootloader --boot-drive=vda --append="console=tty0 console=ttyS0,115200n8"
