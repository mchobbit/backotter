# Checks boot-test.py --platform haiku runs in the Haiku image's shell on the
# serial port (bash, uid 0), one line at a time (keep each check on one line).
# Each check prints one line: "BKO-CHECK <name> <ok|FAIL> <detail>".  The
# marker is read with sed, as on the BSD images.
#
# The longer checks are in /tmp/bko-checks.sh, typed first.
cat >/tmp/bko-checks.sh <<'EOF'
f=/boot/.disk/backotter.json
note=/boot/system/var/run/backotter/remote-access
field() { sed -n "s/^ *\"$1\": \"\([^\"]*\)\",*\$/\1/p" $f | head -n 1; }
askpass() { printf '#!/bin/sh\necho %s\n' "$1" >/tmp/.askpass; chmod 700 /tmp/.askpass; }
sshpw() {
  SSH_ASKPASS=/tmp/.askpass SSH_ASKPASS_REQUIRE=force ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o NumberOfPasswordPrompts=1 -o PubkeyAuthentication=no "$@" </dev/null
}
# Haiku's ps cuts each team's command line at 63 characters.
sshd_up() { ps | grep -q '/bin/sshd\( \|$\)'; }
case $1 in
kernel)
  k=$(field kernel); v=$(uname -v | cut -d" " -f1)
  test "$v" = "$k" || { echo "running $v, marker says $k"; exit 1; }
  echo "$(uname -sv)" ;;
rootfs)
  d=$(df /boot | sed -n 's/^ *Device: *//p'); t=$(df /boot | sed -n 's/^ *File System: *//p'); n=$(df /boot | sed -n 's/^ *Volume Name: *//p')
  case $d in /dev/disk/virtual/*|"") echo "/boot is on '$d'"; exit 1 ;; /dev/disk/*) ;; *) echo "/boot is on '$d'"; exit 1 ;; esac
  test "$t" = bfs && test "$n" = '"BackOtter"' || { echo "/boot: $t, $n"; exit 1; }
  test "$(df /boot/system | sed -n 's/^ *File System: *//p')" = packagefs || { echo "/boot/system isn't packagefs"; exit 1; }
  echo "$d bfs $n, system on packagefs" ;;
console-screen)
  i=0; until ps | grep -q '^/boot/system/apps/Terminal -t BackOtter /bin/bash '; do i=$((i + 1)); test $i -lt 60 || { echo "no Terminal with the console"; exit 1; }; sleep 1; done
  echo "Terminal \"BackOtter\" runs the console" ;;
remote-access)
  /boot/system/non-packaged/lib/backotter/remote-access >/tmp/ra.out 2>&1 || { tail -n 3 /tmp/ra.out; exit 1; }
  pw=$(sed -n 's/^Password: \([^ ]*\).*/\1/p' $note)
  test -n "$pw" || { echo "remote-access showed no password"; exit 1; }
  askpass "$pw"; who=$(sshpw backotter@127.0.0.1 id -un 2>/tmp/ssh.err); rm -f /tmp/.askpass
  test "$who" = backotter || { tail -n 3 /tmp/ssh.err; exit 1; }
  echo "password login over SSH works" ;;
root-ssh-refused)
  # user (uid 0) has the same one-time password: sshd must refuse it anyway.
  sshd_up || { echo "sshd isn't running"; exit 1; }
  pw=$(sed -n 's/^Password: \([^ ]*\).*/\1/p' $note)
  askpass "$pw"; sshpw user@127.0.0.1 true 2>/tmp/ssh.err; r=$?; rm -f /tmp/.askpass
  test $r -ne 0 && grep -q "Permission denied" /tmp/ssh.err || { tail -n 3 /tmp/ssh.err; exit 1; }
  echo "uid 0 refused with the right password" ;;
sshd-off)
  ! sshd_up || { echo "sshd is running"; exit 1; }
  ! grep -q "^service ssh" /boot/system/settings/network/services || { echo "network/services starts sshd"; exit 1; }
  ! id backotter >/dev/null 2>&1 || { echo "backotter exists"; exit 1; }
  # The stick's first boot makes host keys (openssh's first-boot script):
  # that boot's own.  None may be older than the boot job's stamp.
  test -e /tmp/backotter-booted || { echo "no /tmp/backotter-booted"; exit 1; }
  old=$(find /boot/system/settings/ssh -name "ssh_host_*" ! -newer /tmp/backotter-booted)
  test -z "$old" || { echo "host keys from before this boot: $old"; exit 1; }
  echo "no sshd, no backotter, $(ls /boot/system/settings/ssh/ssh_host_*_key 2>/dev/null | wc -l) host keys, all from this boot" ;;
boot-reset)
  # What the boot job does at the next boot: no trace of remote access left.
  bash /boot/system/non-packaged/lib/backotter/boot reset
  ! id backotter >/dev/null 2>&1 || { echo "backotter is still there"; exit 1; }
  ! ls /boot/system/settings/ssh/ssh_host_* >/dev/null 2>&1 || { echo "host keys are still there"; exit 1; }
  test ! -e $note || { echo "$note is still there"; exit 1; }
  grep -q '^user::' /etc/shadow || { echo "user still has a password"; exit 1; }
  echo "backotter, passwords, host keys and the note gone" ;;
marker)
  test "$(stat -c %a $f)" = 444 || { echo "$f isn't read-only"; exit 1; }
  test "$(field platform)" = haiku && grep -q '^  "zfs": null,$' $f || { echo "platform or zfs wrong"; exit 1; }
  echo "$(field image_id) haiku $(field release), no zfs" ;;
esac
EOF
check kernel 'bash /tmp/bko-checks.sh kernel'
check hostname 'test "$(hostname)" = backotter && hostname'
check rootfs 'bash /tmp/bko-checks.sh rootfs'
check serial-debug-off 'grep -q "^serial_debug_output false" /boot/home/config/settings/kernel/drivers/kernel && test -c /dev/ports/pc_serial0 && echo "kernel log off COM1; pc_serial0 is ours"'
check tools 'for c in mkfs mount unmount diskimage makebootable writembr copyattr listattr addattr catattr ssh python3 dd; do command -v $c >/dev/null || { echo no $c; exit 1; }; done; echo all present'
check filesystems 'cd /boot/system/add-ons/kernel/file_systems && for fs in bfs fat exfat ext2 btrfs ntfs; do test -e $fs || { echo no $fs; exit 1; }; done; ls | tr "\n" " "'
check binaries 'rustic-bko --version | grep backotter && ! command -v restic-bko >/dev/null'
check bko-holt 'bko-holt --version | grep "^bko-holt [0-9]"'
check network 'i=0; until a=$(ifconfig | awk "\$1 == \"inet\" && \$2 == \"addr:\" && \$3 !~ /^127\./ { sub(\",\", \"\", \$3); print \$3 }"); test -n "$a"; do i=$((i + 1)); test $i -lt 30 || exit 1; sleep 1; done; echo $a'
check console-screen 'bash /tmp/bko-checks.sh console-screen'
check console-serial 'ps | grep -q "^/bin/python3 /boot/system/non-packaged/lib/backotter/on-serial" && echo "on-serial.py bridges pc_serial0 and $(tty)"'
check sshd-off-at-boot 'bash /tmp/bko-checks.sh sshd-off'
check remote-access 'bash /tmp/bko-checks.sh remote-access'
check root-ssh-refused 'bash /tmp/bko-checks.sh root-ssh-refused'
check boot-reset 'bash /tmp/bko-checks.sh boot-reset'
check marker 'bash /tmp/bko-checks.sh marker'
check memory 'sysinfo -mem | head -n 1 | sed "s/  */ /g"'
