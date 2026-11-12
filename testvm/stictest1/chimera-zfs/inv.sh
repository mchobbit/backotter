#!/bin/sh
# SticTest1 chimera-zfs inventory: what the guest's file systems are and
# support, printed for the log.  Runs in the throwaway guest as root (POSIX
# sh, Chimera's FreeBSD userland).
set -u
sec() { echo; echo "== $*"; }
sec "kernel, OS"; uname -a; . /etc/os-release; echo "$PRETTY_NAME ($ID)"; ls /usr/lib/ld-musl* 2>/dev/null
sec "ZFS"; zfs version 2>&1; cat /etc/hostid 2>/dev/null | od -An -tx1; ls -l /etc/zfs 2>&1
sec "pools"; zpool list -v 2>&1; zpool status 2>&1 | head -n 20
sec "rpool features (enabled/active)"; zpool get -H -o property,value all rpool 2>&1 | grep -v -e '^feature@.*	disabled$' | tr '\t' ' ' | tr '\n' ';'; echo
sec "datasets"; zfs list -o name,used,mountpoint,canmount,mounted 2>&1
sec "rpool/ROOT/chimera properties not default"; zfs get -H -o property,value,source all rpool/ROOT/chimera 2>&1 | grep -v -e '	default$' -e '	-$' | tr '\t' ' '
sec "properties that matter for metadata, every dataset"
zfs get -H -o name,property,value,source xattr,acltype,aclmode,aclinherit,compression,dnodesize,recordsize,atime,relatime,normalization,utf8only,casesensitivity,checksum,encryption,devices,setuid,exec,snapdir,mountpoint 2>&1 | grep -v '	default$' | tr '\t' ' '
sec "mounts"; findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS -t zfs,ext4,vfat,xfs,btrfs 2>&1; cat /etc/fstab
sec "/boot ext4"; d=$(findmnt -n -o SOURCE /boot); echo "dev $d"; tune2fs -l "$d" 2>&1 | grep -E '^(Filesystem features|Inode size|Block size|Default mount options|Filesystem flags)'
sec "/boot/efi vfat"; d=$(findmnt -n -o SOURCE /boot/efi); echo "dev $d"; blkid "$d" 2>&1; fsck.vfat -n -v "$d" 2>&1 | head -n 12; ls -la /boot/efi; find /boot/efi | head -n 30
sec "LSMs"; cat /sys/kernel/security/lsm; echo
sec "tools"; for c in setfattr getfattr setfacl getfacl setcap getcap chattr lsattr xfs_io mkfs.ext4 mkfs.vfat mkfs.fat fatlabel bash python3 zfs zpool truncate fallocate mknod file; do printf '%s=%s ' $c "$(command -v $c || echo -)"; done; echo
sec "apk search"; apk search attr-progs acl-progs libcap-progs dosfstools e2fsprogs python 2>&1 | head -n 20
sec "set-id files on /"; find / -xdev \( -perm -4000 -o -perm -2000 \) -type f 2>/dev/null
sec "hard-link groups under /usr (first 5)"; find /usr -xdev -type f -links +1 2>/dev/null | head -n 5
sec "ACLs anywhere on / (getfacl -s)"; command -v getfacl >/dev/null && getfacl -R -s -p / 2>/dev/null | grep '^# file' | head -n 10
sec "xattrs on / and its top dirs"; command -v getfattr >/dev/null && getfattr -d -m - -h --absolute-names / /usr /etc /var /root /boot 2>&1 | head -n 30
sec "file capabilities under /usr"; command -v getcap >/dev/null && getcap -r /usr 2>/dev/null | head
sec "inode flags on / top"; lsattr -d / /usr /etc /var /root /boot 2>&1
sec "ZFS DOS flags on / (ZFS_IOC_GETDOSFLAGS)"; python3 -c 'import fcntl,os,sys
for p in ["/", "/etc", "/usr", "/etc/passwd"]:
    fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK); b = bytearray(8)
    try: fcntl.ioctl(fd, 0x80088301, b); print(p, hex(int.from_bytes(b, "little")))
    except OSError as e: print(p, e)'
sec "project IDs"; lsattr -p -d / /etc 2>&1
sec "memory, disk"; free -m 2>/dev/null || cat /proc/meminfo | head -n 3; df -Pk / /boot /boot/efi
