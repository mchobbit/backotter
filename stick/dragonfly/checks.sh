# Checks boot-test.py --platform dragonfly runs in the DragonFly image's root
# shell (sh), one line at a time (keep each check on one line).  Each check
# prints one line: "BKO-CHECK <name> <ok|FAIL> <detail>".  No Python here:
# the marker is read with sed.
#
# The longer checks are in /tmp/bko-checks.sh, typed first: boot-test.py
# types at 9600 baud here (its TYPING_RATE), so short lines keep it quick.
cat >/tmp/bko-checks.sh <<'EOF'
f=/.disk/backotter.json
field() { sed -n "s/^ *\"$1\": \"\([^\"]*\)\",*\$/\1/p" $f | head -n 1; }
askpass() { printf '#!/bin/sh\necho %s\n' "$1" >/root/.askpass; chmod 700 /root/.askpass; }
sshpw() {
  SSH_ASKPASS=/root/.askpass SSH_ASKPASS_REQUIRE=force ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o NumberOfPasswordPrompts=1 "$@" </dev/null
}
case $1 in
kernel)
  k=$(field kernel); v=$(sysctl -n kern.version | head -n 1)
  case "$v" in "DragonFly v$k "*) echo "$v" | cut -c1-50 ;; *) echo "running $v, marker says $k"; exit 1 ;; esac ;;
remote-access)
  /usr/local/lib/backotter/remote-access >/tmp/ra.out 2>&1 || { tail -n 3 /tmp/ra.out; exit 1; }
  pw=$(sed -n 's/^Password: \([^ ]*\).*/\1/p' /var/run/backotter/remote-access)
  test -n "$pw" || { echo "remote-access showed no password"; exit 1; }
  askpass "$pw"; who=$(sshpw backotter@127.0.0.1 whoami 2>/tmp/ssh.err); rm -f /root/.askpass
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
  test "$(stat -f %Lp $f)" = 444 || { echo "$f isn't read-only"; exit 1; }
  test "$(field platform)" = dragonfly && grep -q '^  "zfs": null,$' $f || { echo "platform or zfs wrong"; exit 1; }
  echo "$(field image_id) dragonfly $(field release), no zfs" ;;
esac
EOF
check kernel 'sh /tmp/bko-checks.sh kernel'
check firmware 'if test "$(sysctl -b machdep.efi_map 2>/dev/null | wc -c)" -gt 0; then echo UEFI; else echo BIOS; fi'
check hostname 'test "$(hostname)" = backotter && hostname'
check rootfs-ro 'mount | grep " on / (ufs, .*read-only" | cut -d" " -f1-3 | grep . && ! touch /.bko-rw-test 2>/dev/null'
check tmpfs 'for d in /etc /var /root /tmp; do mount | grep -q " on $d (tmpfs" || { echo "$d: not tmpfs"; exit 1; }; done; echo /etc /var /root /tmp'
check installer-gone '! grep -q "^installer:" /etc/master.passwd && grep -q "^root:\*:" /etc/master.passwd && echo "no installer, root locked"'
check pfi-gone 'test ! -e /etc/rc.d/pfi'
check tools 'for c in fdisk disklabel64 gpt newfs newfs_hammer2 hammer2 newfs_msdos mount_msdos cryptsetup lvm dmsetup camcontrol cpdup ssh; do command -v $c >/dev/null || { echo no $c; exit 1; }; done; echo all present'
check binaries 'restic-bko version | head -n 1 | grep backotter || exit 1; rustic-bko --version 2>/dev/null || echo "(no rustic-bko)"'
check network 'i=0; until a=$(ifconfig -a | awk "\$1 == \"inet\" && \$2 !~ /^127\./ { print \$2 }"); test -n "$a"; do i=$((i + 1)); test $i -lt 30 || exit 1; sleep 1; done; echo $a'
check console-screen 'ps -axo tt,command | awk "\$1 == \"v0\" && /backotter\/console/" | grep . && grep -q "^ttyv0.*getty bko.Pc" /etc/ttys'
check console-serial 'ps -axo tt,command | awk "\$1 == \"d0\" && /backotter\/console/" | grep . && grep -q "^ttyd0.*getty bko.9600" /etc/ttys'
check root-shells 'test "$(grep -c "^ttyv[1-7].*getty al.Pc" /etc/ttys)" = 7 && ps -axo tt,command | awk "\$1 == \"v1\"" | grep csh'
check sshd-off-at-boot '! pgrep -x sshd >/dev/null && ! ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1'
check remote-access 'sh /tmp/bko-checks.sh remote-access'
check root-ssh-refused 'sh /tmp/bko-checks.sh root-ssh-refused'
check marker 'sh /tmp/bko-checks.sh marker'
check memory 'echo "$(( $(sysctl -n vm.stats.vm.v_free_count) * $(sysctl -n hw.pagesize) >> 20 )) MB free of $(( $(sysctl -n hw.physmem) >> 20 ))"'
