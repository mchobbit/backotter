# Checks boot-test.py --platform netbsd runs in the image's root shell, one
# line at a time (keep each check on one line, and lines short: NetBSD's tty
# takes 1 KB of input at most).  Each check prints one line:
# "BKO-CHECK <name> <ok|FAIL> <detail>".  /tmp/bko-ssh.sh logs in over SSH
# with the one-time password remote-access set: as backotter (must work), or
# as root (must be refused).
cat >/tmp/bko-ssh.sh <<'EOF'
pw=$(sed -n "s/^Password: \([^ ]*\).*/\1/p" /var/run/backotter/remote-access)
test -n "$pw" || { echo "no password in /var/run/backotter/remote-access"; exit 1; }
printf '#!/bin/sh\necho %s\n' "$pw" >/tmp/bko-askpass
chmod 700 /tmp/bko-askpass
SSH_ASKPASS=/tmp/bko-askpass SSH_ASKPASS_REQUIRE=force ssh -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null -o NumberOfPasswordPrompts=1 "$1@127.0.0.1" whoami </dev/null 2>/tmp/bko-ssh.err
r=$?; rm -f /tmp/bko-askpass; exit $r
EOF
check kernel 'm=$(sed -n "s/.*\"kernel\": \"\([^\"]*\)\".*/\1/p" /.disk/backotter.json); echo "$(uname -sr) $(uname -v | cut -d" " -f3), marker $m"; test "$(uname -r)" = "$m"'
check hostname 'test "$(hostname)" = backotter && hostname'
check rootfs 'mount | grep " on / type ffs (.*read-only" && test "$(sysctl -n kern.root_device)" != "" && ! touch /.bko-w 2>/dev/null'
check rw-in-memory 'for d in etc var root home; do mount | grep -q " on /$d type null" || { echo "/$d: not a copy in memory"; exit 1; }; touch /$d/.bko-w && rm /$d/.bko-w || exit 1; done; mount | grep " on /dev type tmpfs" | cut -d" " -f1-5'
check rw-modes 'for x in /etc:drwxr-xr-x /var/tmp:drwxrwxrwt /tmp:drwxrwxrwt /root:drwxr-xr-x; do m=$(stat -f %Sp ${x%%:*}); test "$m" = ${x#*:} || { echo "${x%%:*} is $m"; exit 1; }; done; echo as on disk'
check network 'for i in $(seq 30); do a=$(ifconfig -a | awk "\$1 == \"inet\" && \$2 !~ /^127\\./ { print \$2 }"); test -n "$a" && { echo "$a"; exit 0; }; sleep 1; done; exit 1'
check console-screen 'ps -ax -o tty= -o command= | awk "/backotter\\/console/ { print \$1 }" | grep -E "^(tty)?E0\$"'
check console-serial 'ps -ax -o tty= -o command= | awk "/backotter\\/console/ { print \$1 }" | grep -E "^(tty)?00\$"'
check ttyE1-root-shell 'ps -ax -o tty= -o user= -o comm= | awk "\$1 ~ /^(tty)?E1\$/ && \$2 == \"root\" && \$3 ~ /sh\$/" | grep .'
check sshd-off-at-boot '! pgrep -x sshd >/dev/null && ! ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1 && echo "off, no host keys"'
check remote-access '/usr/local/lib/backotter/remote-access >/tmp/ra.out 2>&1 || { tail -n 3 /tmp/ra.out; exit 1; }; who=$(sh /tmp/bko-ssh.sh backotter); test "$who" = backotter && echo "password login over SSH works" || { tail -n 3 /tmp/bko-ssh.err; exit 1; }'
check root-ssh-refused '! sh /tmp/bko-ssh.sh root >/dev/null && grep -i -m 1 "denied" /tmp/bko-ssh.err'
check restic-bko 'restic-bko version'
check rustic-bko 'rustic-bko --version'
check tools 'for c in gpt dkctl disklabel fdisk newfs newfs_msdos fsck_ffs mount_msdos zpool zfs lvm raidctl cgdconfig ssh pax; do command -v $c >/dev/null || { echo missing $c; exit 1; }; done; echo all present'
check marker 'f=/.disk/backotter.json; grep -q "\"platform\": \"netbsd\"" $f && grep -q "\"zfs\": null" $f && sed -n -E "s/.*\"(image_id|release)\": \"([^\"]*)\".*/\\2/p" $f | tr "\n" " "'
check memory 'echo "$(($(sysctl -n hw.physmem64) / 1048576)) MB; in memory: $(df -k /.backotter-rw | awk "NR == 2 { print \$3 }") KB"'
