# UEFI, laid out by Anaconda's own automatic partitioning, as a click-through
# Fedora Server install with btrfs chosen gets it: GPT, a 600 MiB EFI system
# partition at /boot/efi, /boot (2 GiB, XFS: Server's default filesystem), and
# a btrfs volume whose subvolume "root" is /, mounted compress=zstd:1.  No
# swap partition: Fedora swaps to zram.  (Workstation differs: ext4 /boot and
# a "home" subvolume.)
zerombr
clearpart --all --initlabel --disklabel=gpt --drives=vda
autopart --type=btrfs --noswap
# A serial console too, as on the other test guests.
bootloader --boot-drive=vda --append="console=tty0 console=ttyS0,115200n8"
