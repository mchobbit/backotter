# Checks boot-test.py --platform illumos runs in the illumos image's root shell
# (ksh93 as sh), one line at a time (keep each check on one line).  Each check
# prints one line: "BKO-CHECK <name> <ok|FAIL> <detail>".  No Python here:
# the marker is read with sed.  The longer checks are in /tmp/bko-checks.sh,
# typed first.
cat >/tmp/bko-checks.sh <<'EOF'
PATH=/usr/xpg4/bin:/usr/bin:/usr/sbin:/sbin:/usr/local/bin
f=/.disk/backotter.json
field() { sed -n "s/^ *\"$1\": \"\([^\"]*\)\",*\$/\1/p" $f | head -n 1; }
askpass() { printf '#!/bin/sh\necho %s\n' "$1" >/root/.askpass; chmod 700 /root/.askpass; }
sshpw() {
  SSH_ASKPASS=/root/.askpass SSH_ASKPASS_REQUIRE=force ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o NumberOfPasswordPrompts=1 "$@" </dev/null
}
online() { test "$(svcs -Ho state "$1" 2>/dev/null)" = online || { echo "$1: $(svcs -Ho state "$1" 2>&1)"; exit 1; }; }
case $1 in
kernel)
  k=$(field kernel); v=$(uname -v)
  test "$v" = "$k" || { echo "running $v, marker says $k"; exit 1; }; echo "$v" ;;
rootfs-ram)
  r=$(mount -p | awk '$3 == "/" { print $1, $4 }')
  test "$r" = "/devices/ramdisk:a ufs" || { echo "/ is $r"; exit 1; }
  o=$(mount -p | awk '$4 == "ufs" && $3 != "/" { print $1 " on " $3 }')
  test -z "$o" || { echo "also mounted: $o"; exit 1; }
  echo "/ is $r; nothing of the stick is mounted" ;;
tmpfs)
  for d in /tmp /var/run /etc/svc/volatile; do
    mount -p | awk -v d=$d '$3 == d && $4 == "tmpfs"' | grep -q . || { echo "$d: not tmpfs"; exit 1; }
  done; echo /tmp /var/run /etc/svc/volatile ;;
tools)
  for c in format fdisk fmthard prtvtoc mkfs newfs labelit fstyp zpool zfs zdb zstreamdump installboot \
      bootadm beadm eeprom devfsadm cfgadm hotplug lofiadm ramdiskadm raidctl stmsboot diskinfo iostat \
      prtconf nvmeadm fcinfo luxadm runat idmap uuidgen dumpadm swap useradd ipadm dladm dhcpinfo ssh \
      ssh-keygen sshd openssl dd cpio gzip xz curl tar setpgrp; do
    command -v $c >/dev/null || { echo "no $c"; exit 1; }
  done
  for p in /usr/lib/fs/ufs/fsck /usr/lib/fs/pcfs/mount /usr/lib/fs/pcfs/mkfs /usr/lib/fs/pcfs/fsck /usr/gnu/bin/stat; do
    test -x $p || { echo "no $p"; exit 1; }
  done; echo all present ;;
console)
  online $2
  t=$(ps -eo tty,args | awk -v t=$3 '$1 == t && /backotter\/console/ { print $1; exit }')
  test -n "$t" || { echo "no console on $3: $(ps -eo pid,sid,tty,args | awk '$3 == "console" || /backotter\/console/' | grep -v awk | tr -s ' ' | tr '\n' ';') vt: $(ls /dev/vt | tr '\n' ' ')"; exit 1; }
  echo "online, on $t" ;;
root-shells)
  for n in 2 3 4 5 6; do online system/console-login:vt$n; done
  ps -eo tty,args | awk '$1 == "vt/2" && /bash/' | grep . ;;
remote-access)
  /usr/local/lib/backotter/remote-access >/tmp/ra.out 2>&1 || { tail -n 3 /tmp/ra.out; exit 1; }
  pw=$(sed -n 's/^Password: \([^ ]*\).*/\1/p' /var/run/backotter/remote-access)
  test -n "$pw" || { echo "remote-access showed no password"; exit 1; }
  askpass "$pw"; who=$(sshpw backotter@127.0.0.1 /usr/xpg4/bin/id -un 2>/tmp/ssh.err); rm -f /root/.askpass
  test "$who" = backotter || { tail -n 3 /tmp/ssh.err; exit 1; }
  echo "password login over SSH works" ;;
root-ssh-refused)
  # Root has the same one-time password: sshd must refuse it anyway.
  pgrep -x sshd >/dev/null || { echo "sshd isn't running"; exit 1; }
  pw=$(sed -n 's/^Password: \([^ ]*\).*/\1/p' /var/run/backotter/remote-access)
  askpass "$pw"; sshpw root@127.0.0.1 true 2>/tmp/ssh.err; r=$?; rm -f /root/.askpass
  test $r -ne 0 && grep -q "Permission denied" /tmp/ssh.err || { tail -n 3 /tmp/ssh.err; exit 1; }
  echo "refused with the right password" ;;
marker)
  test "$(ls -l $f | cut -c1-10)" = "-r--r--r--" || { echo "$f isn't read-only"; exit 1; }
  test "$(field platform)" = illumos && grep -q '"version": "illumos-r' $f || { echo "platform or zfs wrong"; exit 1; }
  echo "$(field image_id) illumos $(field release), $(grep -c '^      "' $f) zfs features" ;;
smf)
  x=$(svcs -x 2>&1 | grep '^svc:/' | sed 's/ .*//')
  test -z "$x" || { echo "in trouble: $x" | tr '\n' ' '; exit 1; }; echo "$(svcs -H | wc -l | tr -d ' ') services online, none in trouble" ;;
esac
EOF
check kernel 'sh /tmp/bko-checks.sh kernel'
check firmware 'if prtconf -v | grep -q efi-systab; then echo UEFI; else echo BIOS; fi'
check hostname 'test "$(hostname)" = backotter && hostname'
check rootfs-ram 'sh /tmp/bko-checks.sh rootfs-ram'
check tmpfs 'sh /tmp/bko-checks.sh tmpfs'
check installer-gone 'test ! -e /.initialboot && test ! -d /kayak && grep -q "^root:\*LK\*:" /etc/shadow && echo "no installer, root locked"'
check tools 'sh /tmp/bko-checks.sh tools'
check binaries 'restic-bko version | head -n 1 | grep backotter && rustic-bko --version | grep backotter'
check network 'i=0; until a=$(ifconfig -a | awk "\$1 == \"inet\" && \$2 !~ /^127\./ { print \$2 }"); test -n "$a"; do i=$((i + 1)); test $i -lt 60 || exit 1; sleep 1; done; echo $a'
check console-screen 'sh /tmp/bko-checks.sh console system/console-login:default console'
check console-serial 'sh /tmp/bko-checks.sh console application/backotter/console:serial term/a'
check root-shells 'sh /tmp/bko-checks.sh root-shells'
check sshd-off-at-boot 'test "$(svcs -Ho state network/ssh)" = disabled && ! ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1 && echo "disabled, no host keys"'
check remote-access 'sh /tmp/bko-checks.sh remote-access'
check root-ssh-refused 'sh /tmp/bko-checks.sh root-ssh-refused'
check marker 'sh /tmp/bko-checks.sh marker'
check zfs-no-import 'test ! -e /etc/zfs/zpool.cache && test -z "$(zpool list -H 2>/dev/null)" && echo "no pools imported"'
check idmap 'svcs -H idmap'
check smf 'sh /tmp/bko-checks.sh smf'
check memory 'echo "$(( $(vmstat 1 2 | tail -n 1 | awk "{ print \$5 }") / 1024 )) MB free of $(prtconf | awk "/^Memory size/ { print \$3 }")"'
