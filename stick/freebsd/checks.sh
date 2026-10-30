# Checks boot-test.py runs in the FreeBSD image's root shell, one line at a
# time (keep each check on one line, and every line under 300 characters: the
# serial port's driver, uart(4), holds 384 bytes while the system is busy and
# drops the rest).  Each check prints one line:
# "BKO-CHECK <name> <ok|FAIL> <detail>".  The base system has no Python:
# /tmp/bko-json.lua reads JSON with flua (FreeBSD's own Lua, with libucl);
# config-checks.sh uses it too.  /tmp/bko-ssh.sh USER CMD logs in over SSH
# with the password remote-access set.
#
# FreeBSD's sh edits lines (libedit) on a terminal, echoing despite stty -echo
# and losing the ends of long lines typed faster than it redraws them; the
# first line turns that off, so the terminal reads whole lines itself (and
# the stty -echo boot-test.py typed before then didn't last).
set +o emacs +o vi; stty -echo
cat >/tmp/bko-json.lua <<'EOF'
-- flua bko-json.lua FILE KEY...: print each KEY (a.b.c, or #a.b for a list's length); fails if one is missing
local ucl = require("ucl")
local p = ucl.parser()
assert(p:parse_file(arg[1]))
local doc, out = p:get_object(), {}
for i = 2, #arg do
  local key, count, v = arg[i], false, nil
  if key:sub(1, 1) == "#" then key, count = key:sub(2), true end
  v = doc
  for k in key:gmatch("[^.]+") do v = type(v) == "table" and v[k] or nil end
  assert(v ~= nil, "no " .. arg[i] .. " in " .. arg[1])
  out[#out + 1] = tostring(count and #v or v)
end
print(table.concat(out, " "))
EOF
cat >/tmp/bko-ssh.sh <<'EOF'
pw=$(awk '/^Password: / { print $2 }' /var/run/backotter/remote-access)
printf '#!/bin/sh\necho %s\n' "$pw" >/tmp/bko-askpass
chmod 700 /tmp/bko-askpass
SSH_ASKPASS=/tmp/bko-askpass SSH_ASKPASS_REQUIRE=force ssh -o LogLevel=ERROR \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o NumberOfPasswordPrompts=1 "$1@127.0.0.1" "$2" </dev/null 2>&1
r=$?; rm -f /tmp/bko-askpass; exit $r
EOF
check kernel 'k=$(/usr/libexec/flua /tmp/bko-json.lua /.disk/backotter.json kernel); test "$(uname -r)" = "$k" || { echo "uname -r says $(uname -r), the marker $k"; exit 1; }; echo "$k, as in the marker"'
check hostname 'test "$(hostname)" = backotter && hostname'
check rootfs 'set -- $(mount -p | awk "\$2 == \"/\""); echo "$1 $3 $4"; test "$3" = ufs && case $4 in ro|ro,*) ;; *) exit 1 ;; esac && ! touch /.bko-rw-test 2>/dev/null'
check memory-dirs 'for d in /etc /root /home /var /tmp; do f=$(df "$d" | awk "NR == 2 { print \$1, \$6 }"); case $f in "tmpfs $d") ;; *) echo "$d is on $f"; exit 1 ;; esac; touch $d/.bko && rm $d/.bko || exit 1; done; echo "all tmpfs, writable"'
check installer-gone 'test ! -e /etc/rc.local && test ! -e /usr/freebsd-packages && ! pgrep -q bsdinstall'
check tools 'for c in gpart zpool zfs newfs newfs_msdos fsck_ffs fsck_msdosfs growfs tunefs geli gmirror graid gstripe gconcat mdconfig camcontrol nvmecontrol efibootmgr boot0cfg fstyp ssh sshd; do command -v $c >/dev/null || { echo missing $c; exit 1; }; done; echo all present'
check restic-bko '/usr/local/bin/restic-bko version'
check rustic-bko '/usr/local/bin/rustic-bko --version'
check zfs-no-autoimport 'test "$(sysrc -n zfs_enable)" = NO && ! kldstat -q -n zfs.ko && echo "zfs_enable is NO, zfs.ko not loaded"'
check network 'for i in $(seq 30); do a=$(ifconfig -a inet | awk "\$1 == \"inet\" && \$2 !~ /^127\\./ { print \$2 }"); test -n "$a" && break; sleep 1; done; test -n "$a" && echo "$a," $(grep "^nameserver" /etc/resolv.conf)'
check console-screen 'ps -ax -o tt= -o command= | awk "\$1 == \"v0\" && /backotter\\/console/" | grep .'
check console-serial 'ps -ax -o tt= -o command= | awk "\$1 == \"u0\" && /backotter\\/console/" | grep .'
check ttyv1-root-shell 'ps -ax -o tt= -o user= -o comm= | awk "\$1 == \"v1\" && \$2 == \"root\" && \$3 == \"sh\"" | grep .'
check sshd-off-at-boot 'test "$(sysrc -n sshd_enable)" = NO && ! service sshd onestatus >/dev/null 2>&1 && ! ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1'
check remote-access '/usr/local/lib/backotter/remote-access >/tmp/ra.out 2>&1 || { tail -n 3 /tmp/ra.out; exit 1; }; out=$(sh /tmp/bko-ssh.sh backotter whoami); test "$out" = backotter || { echo "$out" | tail -n 3; exit 1; }; echo "password login over SSH works"'
check root-ssh-refused 'out=$(sh /tmp/bko-ssh.sh root "echo logged-in"); case $out in *logged-in*) echo "root logged in"; exit 1 ;; *"Permission denied"*) echo "refused, even with the right password" ;; *) echo "$out"; exit 1 ;; esac'
check marker 'm=$(/usr/libexec/flua /tmp/bko-json.lua /.disk/backotter.json image_id platform base.os base.release zfs.version "#zfs.features") || exit 1; set -- $m; test "$2" = freebsd && test "$6" -gt 0 && echo "$1 $2 $3 $4 zfs $5 $6 features"'
check memory 'echo "$(top -b | sed -n "s/^Mem: //p") of $(( $(sysctl -n hw.physmem) / 1048576 )) MB"'
