#!/bin/bash
# What an Ubuntu Core guest shows, for make-ubuntu-core.sh's first-boot.txt
# and docs/design/ubuntu-core.md's checks.  Run as root in the guest
# (sudo bash -s STAGE <probe.sh).  "BKO key=value" lines are what the
# builder tests; the rest is evidence.  It never prints a key: of the FDE
# key files it lists names and sizes only.
stage=${1:-probe}
kv() { printf 'BKO %s=%s\n' "$1" "$2"; }
sec() { printf '\n--- %s\n' "$*"; }
cmdline=$(cat /proc/cmdline)
kv stage "$stage"
kv cmdline "$cmdline"
mode=$(sed -n 's/^mode=//p' /var/lib/snapd/modeenv 2>/dev/null)
kv mode "$mode"
kv boot_id "$(cat /proc/sys/kernel/random/boot_id)"
kv running "$(systemctl is-system-running 2>/dev/null)"
sb=$(od -An -tu1 -j4 -N1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c 2>/dev/null | tr -d ' ')
kv secure_boot "${sb:-none}"
kv tpm "$(ls /sys/class/tpm 2>/dev/null | tr '\n' ' ')"
kv encrypted "$(ls /dev/mapper | grep -q '^ubuntu-data' && echo yes || echo no)"
kv recovery_systems "$(snap recovery 2>/dev/null | awk 'NR > 1 { print $1 }' | tr '\n' ' ')"
kv hello_revs "$(snap list --all hello 2>/dev/null | awk 'NR > 1 { print $3 }' | tr '\n' ' ')"
kv mark_data "$(cat /run/mnt/data/bko-mark 2>/dev/null)"
kv mark_save "$(cat /run/mnt/ubuntu-save/bko-mark 2>/dev/null)"
kv changes_in_progress "$(snap changes 2>/dev/null | awk 'NR > 1 && $2 ~ /^(Do|Doing|Undo|Undoing|Wait)$/' | wc -l)"
kv hold "$(snap refresh --time 2>/dev/null | sed -n 's/^hold: *//p')"

sec os-release; cat /etc/os-release
sec uname; uname -a
sec model; snap model --verbose 2>&1 | grep -v '^ *sign-key'
sec snap version; snap version
sec snap list --all; snap list --all
sec snap recovery; snap recovery
sec snap refresh --time; snap refresh --time
sec modeenv; cat /var/lib/snapd/modeenv
sec boot-vars; snap debug boot-vars 2>&1
sec seed grubenv; tr -d '#' </run/mnt/ubuntu-seed/EFI/ubuntu/grubenv | tr -s '\n'
sec boot grubenv; tr -d '#' </run/mnt/ubuntu-boot/EFI/ubuntu/grubenv | tr -s '\n'
sec lsblk; lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,PARTLABEL,UUID,PARTUUID,MOUNTPOINT
sec blkid; blkid
sec /dev/mapper; ls -l /dev/mapper
sec /dev/disk/by-label; ls -l /dev/disk/by-label
sec findmnt /run/mnt; findmnt -rno TARGET,SOURCE,FSTYPE | grep -E '^/run/mnt|^/writable|^/ |^/var/lib/snapd/save'
sec df; df -k /run/mnt/ubuntu-seed /run/mnt/ubuntu-boot /run/mnt/ubuntu-save /run/mnt/data
sec du -sxk; du -sxk /run/mnt/ubuntu-seed /run/mnt/ubuntu-boot /run/mnt/ubuntu-save /run/mnt/data 2>/dev/null
sec seed tree; (cd /run/mnt/ubuntu-seed && find . -maxdepth 3 | sort)
sec boot tree; (cd /run/mnt/ubuntu-boot && find . | sort)
sec save tree; (cd /run/mnt/ubuntu-save && find . -printf '%M %s %p\n' | sort -k3)
sec "FDE key files (names and sizes only)"
for d in /run/mnt/ubuntu-boot/device/fde /run/mnt/ubuntu-seed/device/fde /run/mnt/ubuntu-save/device/fde /var/lib/snapd/device/fde; do
    echo "$d:"; ls -la "$d" 2>&1
done
sec cmdline-append; snap get -d system system.kernel 2>&1
sec console-conf
ls -la /var/lib/console-conf 2>&1
systemctl list-units --all --no-legend 'console-conf*' 'serial-console-conf*' 'getty@*' 'serial-getty@*' 2>&1
sec cloud-init
cloud-init status --long 2>&1
ls -la /etc/cloud /etc/cloud/cloud.cfg.d 2>&1
for f in /etc/cloud/cloud.cfg.d/*snap* /etc/cloud/cloud.cfg.d/*restrict* /etc/cloud/cloud.cfg.d/*datasource*; do
    [ -f "$f" ] && { echo "== $f"; cat "$f"; }
done
ls -la /etc/cloud/cloud-init.disabled 2>&1
sec users
getent passwd tester; id tester; ls -la /var/lib/extrausers 2>&1; ls -la /etc/sudoers.d 2>&1
ls -la /root/.ssh /home/tester/.ssh 2>&1
grep -iE '^(permitrootlogin|passwordauthentication)' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/* 2>/dev/null
sec "efivars: boot entries"
ls /sys/firmware/efi/efivars | grep -E '^Boot[0-9A-F]{4}-|^BootOrder-|^BootCurrent-'
for v in /sys/firmware/efi/efivars/Boot[0-9A-F][0-9A-F][0-9A-F][0-9A-F]-8be4df61-93ca-11d2-aa0d-00e098032b8c; do
    [ -e "$v" ] || continue
    printf '%s: ' "${v##*/}"; cat "$v" | tail -c +11 | tr -d '\0' | tr -c '[:print:]' '.' | cut -c1-120; echo
done
sec failed units; systemctl --failed --no-legend
sec journal: snap-bootstrap and secboot this boot
journalctl -b --no-pager -o cat 2>/dev/null | grep -iE 'recovery key|reseal|reprovision|unlock|sealed|tpm|fde' | grep -viE 'key=|[0-9]{5}-[0-9]{5}' | tail -n 60
exit 0
