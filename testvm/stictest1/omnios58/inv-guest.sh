#!/bin/ksh
# Inventory of an omnios guest (run as root in a throwaway boot).
PATH=/usr/bin:/usr/sbin:/sbin:/usr/gnu/bin:$PATH; export LC_ALL=C
echo "== os"; uname -a; cat /etc/release; isainfo -k; pkg list -H entire 2>/dev/null
echo "== libc"; ls -l /lib/libc.so.1; pvs /lib/libc.so.1 2>/dev/null | tail -2
echo "== init"; ps -p 1 -o comm=; svcs -H | wc -l; svcs -a | grep -i -e idmap -e smb -e labeld -e tsol
echo "== boot"; bootadm list-menu 2>&1 | head -5; prtconf -v 2>/dev/null | grep -i -A1 efi | head -4; ls /boot; ls -la /boot/efi 2>/dev/null | head -2; prtvtoc /dev/rdsk/$(zpool status rpool | awk '/c[0-9]/{print $1; exit}')s0 2>/dev/null | head -3; zpool status
echo "== mounts"; mount -p; df -n 2>&1 | head -20
echo "== zfs"; zfs list -o name,used,mountpoint,canmount,compression,atime,aclmode,aclinherit,xattr,casesensitivity,normalization,utf8only,encryption,recordsize,dedup,copies,checksum,nbmand,setuid,exec,readonly
zfs get -H -s local,received all | grep -v -e '@' | head -60
zpool get -H all rpool | grep -v -e 'disabled' | awk '{print $2"="$3}' | tr '\n' ' '; echo
echo "== security"; cat /etc/system 2>/dev/null | grep -v '^\*' | grep . ; ls /etc/security/tsol 2>/dev/null | head -3; svcs -H labeld 2>&1; zoneadm list -cv
echo "== idmap"; svcs -H idmap; idmap list 2>&1 | head
echo "== tmpfs xattr"; touch /tmp/x$$ && runat /tmp/x$$ 'echo a >b' && echo "tmpfs xattr ok"; /usr/bin/chmod S+vhidden /tmp/x$$ 2>&1 && echo "tmpfs sysattr ok"; /usr/bin/chmod A+user:nobody:read_data:allow /tmp/x$$ 2>&1 && echo "tmpfs acl ok"; ls -V /tmp/x$$; rm -f /tmp/x$$
echo "== find counts (/ zfs datasets, -xdev per mount)"
for m in $(zfs list -H -o mountpoint -t filesystem | grep '^/'); do
  [ -d "$m" ] || continue
  printf "%s files=%s xattr=%s acl=%s\n" $m $(find $m -mount | wc -l) $(find $m -mount -xattr | wc -l) $(find $m -mount -acl | wc -l)
done
echo "== examples xattr"; find / -mount -xattr 2>/dev/null | head -10
echo "== examples acl"; find / -mount -acl 2>/dev/null | head -10 | while read p; do ls -dV "$p" | head -4; done
echo "== python scan"
/usr/bin/python3 -I /root/inv-scan.py $(zfs list -H -o mountpoint -t filesystem | grep '^/')
