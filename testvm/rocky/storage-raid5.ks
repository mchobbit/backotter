# make-rocky.sh --raid5: three disks, BIOS + MBR, each with two primary
# partitions:
#   1  1 GiB  a member of md RAID1 "boot" across all three, /boot (XFS);
#             GRUB goes in every disk's MBR
#   2  rest   a member of md RAID5 "pv00" (metadata 1.2, mdadm's default
#             left-symmetric layout, an internal write-intent bitmap, as
#             Anaconda makes them), the PV of volume group rl_@HOSTNAME@,
#             with the logical volumes swap (2 GiB) and root (XFS, /) for
#             the rest
zerombr
clearpart --all --initlabel --disklabel=msdos --drives=vda,vdb,vdc
ignoredisk --only-use=vda,vdb,vdc
part raid.11 --size=1024 --asprimary --ondisk=vda
part raid.12 --size=1024 --asprimary --ondisk=vdb
part raid.13 --size=1024 --asprimary --ondisk=vdc
part raid.21 --size=4096 --grow --asprimary --ondisk=vda
part raid.22 --size=4096 --grow --asprimary --ondisk=vdb
part raid.23 --size=4096 --grow --asprimary --ondisk=vdc
raid /boot --level=1 --device=boot --fstype=xfs raid.11 raid.12 raid.13
raid pv.01 --level=5 --device=pv00 raid.21 raid.22 raid.23
volgroup rl_@HOSTNAME@ pv.01
logvol swap --vgname=rl_@HOSTNAME@ --name=swap --size=2048
logvol / --vgname=rl_@HOSTNAME@ --name=root --fstype=xfs --size=4096 --grow

# The arrays' first resync, finished before the install ends, so the image's
# arrays are in sync (make-rocky.sh's QEMU writes zeros as holes).
%post --nochroot
for md in /dev/md/*; do mdadm --wait "$md" || true; done
cat /proc/mdstat
mdadm --detail /dev/md/pv00
%end
