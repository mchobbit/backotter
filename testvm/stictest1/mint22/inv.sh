#!/bin/sh
# Inventory of a Linux guest (SticTest1).
exec 2>&1
s() { echo "### $*"; }
s os; cat /etc/os-release; uname -a; ldd --version | head -1; ls -l /sbin/init; ps -p 1 -o comm=
s firmware; [ -d /sys/firmware/efi ] && echo UEFI || echo BIOS; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,PARTTYPE; fdisk -l /dev/vda 2>/dev/null | head -20; ls /boot /boot/grub 2>/dev/null | head; dpkg -l | grep -E '^ii +(grub|nilfs|e2fs|xfsprogs|btrfs|jfs|f2fs|zfs|attr|acl|libcap2-bin|python3 |cryptsetup|lvm2|mdadm|apparmor|selinux)' | awk '{print $2,$3}'
s mounts; findmnt -rn -o TARGET,SOURCE,FSTYPE,OPTIONS | grep -vE '^/(proc|sys|dev|run)/' 
s swap; cat /proc/swaps
for d in $(lsblk -rno PATH,FSTYPE | awk '$2!=""{print $1":"$2}'); do p=${d%%:*}; t=${d#*:}; s "fs $p $t"
 case $t in nilfs2) nilfs-tune -l $p 2>&1 | head -40; lscp 2>/dev/null | tail -3; lssu 2>/dev/null | tail -2;; f2fs) b=$(basename $(readlink -f $p)); echo "features: $(cat /sys/fs/f2fs/$b/features 2>/dev/null)"; dump.f2fs $p 2>/dev/null | grep -iE "feature|extension|encoding" | head -8;; ext*) tune2fs -l $p | grep -E 'features|Inode size|Block size|Default mount';; vfat) fsck.vfat -n $p 2>&1 | head -3;; esac; done
s lsm; cat /sys/kernel/security/lsm; echo; aa-status 2>&1 | head -5; ls /sys/fs/selinux 2>/dev/null | head -2
s nsswitch; grep -E '^(passwd|group|shadow)' /etc/nsswitch.conf
s tools; for t in python3 perl setfattr getfattr setfacl getfacl chattr lsattr setcap getcap mkfs.nilfs2 mkfs.ext4 mkfs.vfat mkfs.xfs mkfs.btrfs mknod truncate fallocate filefrag nilfs-tune mkfs.jfs mkfs.f2fs losetup; do printf '%s=%s ' $t "$(command -v $t || echo -)"; done; echo
s modules; ls /lib/modules/$(uname -r)/kernel/fs | tr '\n' ' '; echo; cat /proc/filesystems | tr '\n' ' '; echo
# on-disk census over local fs (-xdev from each real mount)
roots=$(findmnt -rn -o TARGET,FSTYPE | awk '$2 ~ /^(nilfs2|ext[234]|vfat|xfs|btrfs|jfs|f2fs)$/ {print $1}')
s roots $roots
for r in $roots; do s "census $r"
 find $r -xdev -printf '%y\n' | sort | uniq -c
 echo "hardlinked files: $(find $r -xdev -type f -links +1 | wc -l)"
 echo "setid/sticky: $(find $r -xdev \( -perm -4000 -o -perm -2000 -o -perm -1000 \) | wc -l)"
 echo "nouser/nogroup: $(find $r -xdev \( -nouser -o -nogroup \) | wc -l)"
 echo "sparse (blocks*512<size): $(find $r -xdev -type f -size +0 -printf '%b %s %p\n' | awk '$1*512<$2' | wc -l)"; find $r -xdev -type f -size +0 -printf '%b %s %p\n' | awk '$1*512<$2' | head -8
 echo "birth: $(stat -c %W $r) on $r; files with birth>0: $(find $r -xdev -maxdepth 3 -exec stat -c %W {} + 2>/dev/null | awk '$1>0' | wc -l)"
 echo "xattr names:"; getfattr -R -h -P --absolute-names -d -m - $r 2>/dev/null | grep -E '^[a-z]+\.' | cut -d= -f1 | sort | uniq -c | sort -rn | head -20
 echo "acl files:"; getfacl -R -s -P -p $r 2>/dev/null | grep -c '^# file'; getfacl -R -s -P -p $r 2>/dev/null | grep '^# file' | head -5
 echo "caps:"; getcap -r $r 2>/dev/null | head -12
 echo "lsattr flags:"; lsattr -R -a $r 2>/dev/null | awk 'NF==2{print $1}' | sort | uniq -c | sort -rn | head -10
 echo "dev/fifo/socket:"; find $r -xdev \( -type b -o -type c -o -type p -o -type s \) | head -10
 echo "non-utf8 names: $(find $r -xdev -print0 | python3 -c 'import sys;d=sys.stdin.buffer.read().split(b"\0");n=0
for x in d:
  try: x.decode()
  except: n+=1
print(n)')"
done
s root dirs; stat -c '%n %a %U:%G %X %Y' / /root /home /var 2>/dev/null
s birth; stat -c "%n %W %w" / /etc/hostname /boot 2>/dev/null
s xattr samples; getfattr -d -m - -h /var/log/journal 2>/dev/null; getfacl -p /var/log/journal 2>/dev/null; getcap /usr/bin/ping /usr/lib/x86_64-linux-gnu/gstreamer1.0/gstreamer-1.0/gst-ptp-helper 2>/dev/null; ls -la /var/log/journal | head -5
s df; df -hT | grep -v tmpfs
