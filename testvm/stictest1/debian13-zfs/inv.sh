#!/bin/sh
echo "== os"; cat /etc/os-release; cat /etc/devuan_version 2>/dev/null; uname -a
echo "== libc"; ldd --version 2>&1 | head -1
echo "== init"; ls -l /sbin/init; cat /proc/1/comm; ls /etc/init.d | wc -l; dpkg -l | grep -E '^ii +(sysvinit-core|runit|openrc|systemd|elogind|libpam-elogind|eudev|udev|acl|attr|libcap2-bin|e2fsprogs|python3 |xfsprogs|btrfs-progs|python3-minimal|grub-pc|grub-efi)' | awk '{print $2, $3}'
echo "== fw"; [ -d /sys/firmware/efi ] && echo UEFI || echo BIOS; ls /boot; ls /boot/grub 2>/dev/null | head; fdisk -l /dev/vda 2>/dev/null | head -20; lsblk -f
echo "== mounts"; cat /proc/mounts; cat /etc/fstab
echo "== swaps"; cat /proc/swaps
for d in $(awk '$3 ~ /^(ext[234])$/ {print $1}' /proc/mounts | sort -u); do echo "== tune2fs $d"; tune2fs -l $d | grep -E 'features|Inode size|Block size|mount options|Filesystem flags|Default directory hash|Filesystem created'; done
echo "== lsm"; cat /sys/kernel/security/lsm 2>/dev/null; echo; ls /sys/kernel/security/; cat /sys/module/apparmor/parameters/enabled 2>/dev/null; aa-status 2>/dev/null | head -3; getenforce 2>/dev/null; cat /proc/cmdline
echo "== tools"; for t in python3 perl setfattr getfattr setfacl getfacl setcap getcap chattr lsattr mkfs.ext4 mkfs.xfs mkfs.btrfs mkfs.vfat losetup filefrag cc gcc nc busybox mknod; do printf "%s=%s " $t "$(command -v $t)"; done; echo
echo "== disk"; df -h -x tmpfs -x devtmpfs; free -m
echo "== counts (root fs + /boot, xdev)"
for m in / /boot; do mountpoint -q $m || continue
  echo "-- $m"
  find $m -xdev -printf '%y\n' | sort | uniq -c
  echo "hardlinked files: $(find $m -xdev -type f -links +1 | wc -l)"
  echo "setuid: $(find $m -xdev -perm -4000 | wc -l) setgid: $(find $m -xdev -perm -2000 | wc -l) sticky: $(find $m -xdev -perm -1000 | wc -l)"
  echo "nouser: $(find $m -xdev -nouser | wc -l) nogroup: $(find $m -xdev -nogroup | wc -l)"
  echo "sparse (blocks*512 < size): $(find $m -xdev -type f -size +0 -printf '%b %s %p\n' | awk '$1*512 < $2' | wc -l)"
  find $m -xdev -type f -size +0 -printf '%b %s %p\n' | awk '$1*512 < $2' | head -8
done
echo "== xattrs"
if command -v getfattr >/dev/null; then
 getfattr -R -h -P -d -m - --absolute-names / /boot 2>/dev/null | grep -v '^#' | grep = | cut -d= -f1 | sort | uniq -c
 echo "-- examples"; getfattr -R -h -P -d -m - -e hex --absolute-names / 2>/dev/null | grep -B1 '=' | grep -E '^# file|security|system|trusted|user' | head -30
else echo "no getfattr"; python3 -c '
import os,collections
c=collections.Counter()
for top in ["/","/boot"]:
  dev=os.lstat(top).st_dev
  for d,ds,fs in os.walk(top):
    ds[:]=[x for x in ds if os.lstat(os.path.join(d,x)).st_dev==dev]
    for n in ds+fs:
      p=os.path.join(d,n)
      try:
        for a in os.listxattr(p,follow_symlinks=False): c[a]+=1
      except OSError: pass
for k,v in c.items(): print(v,k)' 2>&1; fi
echo "== caps"; getcap -r / 2>/dev/null
echo "== acl"; getfacl -R -s -p / 2>/dev/null | grep '^# file' | head; getfacl -R -s -p / 2>/dev/null | grep -c '^# file'
echo "== lsattr"; lsattr -R -a / 2>/dev/null | awk '{print $1}' | sort | uniq -c | sort -rn | head -20
lsattr -R -a / 2>/dev/null | grep -v -- '--------------e-------' | grep -vE '^-+ ' | head -20
lsattr -R -a /boot 2>/dev/null | awk '{print $1}' | sort | uniq -c
echo "== projid"; lsattr -p -d / /root /boot 2>/dev/null
echo "== birth"; stat -c '%w %n' / /etc/hostname /boot 2>&1
echo "== special"; find / -xdev \( -type b -o -type c -o -type p -o -type s \) -printf '%y %p\n' 2>/dev/null | head
echo "== tmpfs/others"; findmnt -l -o TARGET,FSTYPE,OPTIONS
echo "== kernel fs"; cat /proc/filesystems | grep -v nodev; ls /lib/modules/$(uname -r)/kernel/fs | tr '\n' ' '
echo; echo "== users"; awk -F: '$3>=1000' /etc/passwd; grep -E '^(passwd|group)' /etc/nsswitch.conf
echo "== root-dir metadata"; stat -c '%A %U:%G %y %n' / /root /root/.. 
echo "== btrfs"; for m in $(awk '$3=="btrfs" {print $2}' /proc/mounts); do echo "-- $m"; btrfs filesystem show $m 2>&1|head; btrfs subvolume list -a $m 2>&1|head -20; btrfs subvolume get-default $m 2>&1; btrfs filesystem df $m 2>&1; btrfs property get $m 2>&1; done
echo "== xfs"; for m in $(awk '$3=="xfs" {print $2}' /proc/mounts); do xfs_info $m; done
echo "== selinux"; ls /etc/selinux 2>&1; sestatus 2>&1 | head -5
echo "== python xattr walk"; python3 - <<'PY'
import os,collections
c=collections.Counter(); ex={}
for top in ["/","/boot","/boot/efi"]:
  if not os.path.isdir(top): continue
  dev=os.lstat(top).st_dev
  for d,ds,fs in os.walk(top):
    ds[:]=[x for x in ds if os.lstat(os.path.join(d,x)).st_dev==dev]
    for n in ds+fs:
      p=os.path.join(d,n)
      try:
        for a in os.listxattr(p,follow_symlinks=False): c[a]+=1; ex.setdefault(a,p)
      except OSError: pass
for k,v in c.items(): print(v,k,ex[k])
PY
echo "== birth sample"; stat -c "%W %n" /etc/hostname /usr/bin/ls /boot/vmlinuz* 2>&1 | head -3
echo "== lsattr counts per mount"; for m in / /boot /boot/efi; do mountpoint -q $m && { echo "-- $m"; lsattr -R -a $m 2>/dev/null | awk '{print $1}' | grep -v : | sort | uniq -c | sort -rn | head; }; done
echo "== zfs"; zfs version 2>&1; zpool list -v 2>&1; zpool status 2>&1 | head -30
zfs list -o name,mountpoint,canmount,mounted,used 2>&1
for ds in $(zfs list -H -o name 2>/dev/null); do echo "-- $ds"; zfs get -H -o property,value,source acltype,xattr,dnodesize,utf8only,normalization,casesensitivity,compression,atime,relatime,encryption,recordsize,mountpoint,exec,setuid,devices,aclinherit,aclmode,checksum $ds 2>&1 | tr '\t' ' ' | paste -sd ';'; done
for p in $(zpool list -H -o name 2>/dev/null); do echo "-- features $p"; zpool get -H -o property,value all $p | grep -E 'feature@' | awk '$2 != "disabled" {print $1"="$2}' | paste -sd ' '; zpool get -H -o property,value compatibility,cachefile,ashift $p; done
echo "== examples"; for f in /usr/bin/ping /bin/ping /usr/bin/mtr-packet /usr/bin/passwd /usr/bin/chage /usr/bin/crontab /usr/bin/newgrp /usr/bin/su /usr/bin/mount /var/cache/man/index.db /var/lib/systemd/random-seed /var/log/journal; do [ -e $f ] && stat -c '%A %U:%G %s %b %n' $f; done
getcap /usr/bin/ping /usr/bin/* /usr/sbin/* /usr/lib/*/* 2>/dev/null | head
ls -la /var/log/journal 2>&1 | head; getfacl -p /var/log/journal 2>/dev/null; getfattr -d -m - /var/log/journal/*/system.journal 2>/dev/null | head
echo "== pkgs"; dpkg -l | awk '/^ii/ {print $2}' | grep -E '^(iputils-ping|mtr|mtr-tiny|cron|man-db|attr|acl|libcap2-bin|e2fsprogs|dosfstools|xfsprogs|btrfs-progs|zfsutils-linux|zfs-dkms|systemd|apparmor|selinux)' | paste -sd ' '
echo "== nss"; getent passwd 1000 2>&1; ls -la /home
