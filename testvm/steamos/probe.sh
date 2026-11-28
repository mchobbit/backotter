#!/bin/bash
# What make-steamos.sh records of a steamos3 boot, run as root in the guest
# over SSH (bash -s STAGE).  Lines "BKO key=value" are what make-steamos.sh
# checks; the rest is first-boot.txt's record (and S1's answers to the
# spec's *check* items: docs/design/steamos.md, "What S1 found").  STAGE:
# first (the first boot, all of it), updated (after the update, before the
# reboot), or any other name (a later boot: the slot, its marks, the confs).
stage=${1:-boot}
set -u
x() { echo "+ $*"; "$@" 2>&1; }
have() { command -v "$1" >/dev/null; }
first() { local c; for c in "$@"; do have "$c" && { echo "$c"; return; }; done; }
bootconf=$(first holo-bootconf steamos-bootconf)
echo "BKO bootconf_cmd=$bootconf"
echo "BKO update_cmd=$(first steamos-update holo-update)"
echo "BKO reboot_cmd=$(first holo-reboot steamos-reboot)"
slot=$($bootconf this-image 2>/dev/null)
echo "BKO slot=$slot"
other=B; [[ $slot == B ]] && other=A
echo "BKO bko_slot=$(cat /etc/bko-slot 2>/dev/null || echo none)"
echo "BKO cmdline=$(cat /proc/cmdline)"
echo "BKO running=$(systemctl is-system-running --wait 2>/dev/null)"
echo "BKO graphical=$(systemctl is-active graphical.target)"
bootsvc=$(systemctl list-unit-files --no-legend 'holo-boot.service' 'steamos-boot.service' 2>/dev/null | awk '{ print $1; exit }')
echo "BKO boot_service=$bootsvc $(systemctl is-active "$bootsvc" 2>/dev/null) $(systemctl show -P Result "$bootsvc" 2>/dev/null)"
for _ in $(seq 60); do [[ $(systemctl is-active "$bootsvc" 2>/dev/null) =~ ^(active|inactive|failed)$ && $(systemctl show -P ActiveEnterTimestampMonotonic "$bootsvc") != 0 ]] && break; sleep 2; done
ls /esp/SteamOS/conf >/dev/null 2>&1; ls /efi/EFI >/dev/null 2>&1
echo "BKO boot_attempts=$(sed -n 's/^boot-attempts:[[:space:]]*//p' /esp/SteamOS/conf/$slot.conf 2>/dev/null)"
echo "BKO selected=$($bootconf selected-image 2>/dev/null)"
echo "BKO self_build=$(python3 -c 'import json; m = json.load(open("/etc/steamos-atomupd/manifest.json")); print(m.get("buildid"), m.get("version"), m.get("variant"))' 2>/dev/null)"
echo "BKO os_release=$(grep -E '^(ID|ID_LIKE|VERSION_ID|VARIANT_ID|BUILD_ID|VERSION_CODENAME)=' /etc/os-release | tr '\n' ' ')"
echo "BKO grub_console=$(grep -qE '^[[:space:]]*(steamenv_boot[[:space:]]+)?linux[[:space:]].*console=ttyS0' /efi/EFI/steamos/grub.cfg && echo yes || echo no)"
echo "BKO root_partlabel=$(lsblk -no PARTLABEL "$(findmnt -vno SOURCE /)")"
# The other slot, read-only, by partsets (holo-chroot's way, by hand).
M=/run/bko-probe; mkdir -p $M/root $M/var $M/efi
od=$(readlink -f /dev/disk/by-partsets/other/rootfs 2>/dev/null)
[[ -b $od ]] || od=$(readlink -f /dev/disk/by-partlabel/rootfs-$other)
mount -o ro,rescue=nologreplay "$od" $M/root 2>/dev/null &&
    echo "BKO other_build=$(python3 -c 'import json, sys; m = json.load(open(sys.argv[1])); print(m.get("buildid"), m.get("version"), m.get("variant"))' $M/root/etc/steamos-atomupd/manifest.json 2>/dev/null)"
mount -o ro,noload "$(readlink -f /dev/disk/by-partlabel/var-$other)" $M/var 2>/dev/null &&
    echo "BKO other_bko_slot=$(cat $M/var/lib/overlays/etc/upper/bko-slot 2>/dev/null || echo none)"
mount -o ro "$(readlink -f /dev/disk/by-partlabel/efi-$other)" $M/efi 2>/dev/null &&
    echo "BKO other_grub_console=$(grep -qE '^[[:space:]]*(steamenv_boot[[:space:]]+)?linux[[:space:]].*console=ttyS0' $M/efi/EFI/steamos/grub.cfg && echo yes || echo no)"
echo "=== the ESP's confs"; for f in /esp/SteamOS/conf/*.conf; do echo "--- $f"; cat "$f"; done
x $bootconf list-images
echo "=== this slot's grub.cfg (search, linux, initrd)"; grep -nE '^[[:space:]]*(search|steamenv_boot|linux|initrd)' /efi/EFI/steamos/grub.cfg
echo "=== other slot's grub.cfg"; grep -nE '^[[:space:]]*(search|steamenv_boot|linux|initrd)' $M/efi/EFI/steamos/grub.cfg 2>/dev/null
x lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTLABEL,UUID,PARTUUID,MOUNTPOINTS
x findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
x btrfs property get -ts / ro
x btrfs property get -ts $M/root ro
x systemctl --failed --no-legend
x systemctl list-jobs --no-legend
x cat /etc/os-release
x uname -r
x hostname
if [[ $stage == first || $stage == b ]]; then
    x cat /etc/steamos-atomupd/manifest.json; echo
    x cat $M/root/etc/steamos-atomupd/manifest.json; echo
    x ls -la /usr/lib/steamos-atomupd /etc/steamos-atomupd
    x cat /etc/steamos-atomupd/preferences.conf /var/lib/overlays/etc/upper/steamos-atomupd/preferences.conf
    x getent passwd 1000 1001
    x id tester
    x loginctl list-sessions --no-legend
    x systemctl status sddm --no-pager -n 0
    x sh -c 'cat /etc/sddm.conf.d/*'
    x sh -c 'ps -eo pid,user,args | grep -E "plasma|kwin|gamescope|steam" | grep -v grep'
    echo "=== /mnt (empty?)"; ls -la /mnt
    x cat /etc/fstab
    x ls -la /etc/fstab
    x sh -c 'systemctl list-units --all --no-legend "*.automount" "*.mount" | grep -E "efi|esp|home|var|offload|opt|root|srv|nix"'
    for u in efi.automount esp.automount efi.mount esp.mount home.mount; do echo "--- $u"; systemctl cat "$u" 2>&1 | head -40; done
    x sh -c 'ls /run/systemd/generator /run/systemd/generator.late 2>&1'
    x sh -c 'grep -rl "efi\|esp" /run/systemd/generator* 2>/dev/null'
    x sh -c 'systemctl status systemd-repart systemd-growfs@home.service "systemd-growfs*" --no-pager -n 3 2>&1 | head -40'
    x sh -c 'ls /usr/lib/repart.d /etc/repart.d 2>&1; cat /usr/lib/repart.d/* 2>&1'
    x df -h / /var /home /efi /esp
    x sh -c 'compsize -x / 2>&1'
    x sh -c 'du -sxh --apparent-size / 2>&1'
    x btrfs filesystem df /
    x btrfs filesystem show
    x sh -c 'lsattr -d /home /home/* /home/*/.local /home/*/.local/share /home/*/.local/share/Steam 2>&1 | head -30'
    x sh -c 'find /home -xdev -maxdepth 6 -type d -exec lsattr -d {} + 2>/dev/null | grep -E "^[^ ]*F" | head -30'
    x tune2fs -l "$(findmnt -vno SOURCE /home)"
    x tune2fs -l "$(readlink -f /dev/disk/by-partlabel/var-$slot)"
    for u in avahi-daemon.service avahi-daemon.socket sshd.service firewalld.service atomupd.service rauc.service holo-boot.service steamos-boot.service \
             holo-post-update-shutdown.service steamos-post-update-shutdown.service swapfile.service systemd-repart.service; do
        echo "BKO unit $u: $(systemctl is-enabled $u 2>&1) $(systemctl is-active $u 2>&1)"
    done
    x sh -c 'ls /usr/bin | grep -E "^(steamos|holo|jupiter)-" | tr "\n" " "'
    x sh -c 'pacman -Q --dbpath /usr/lib/holo/pacmandb 2>/dev/null | wc -l; pacman -Q --dbpath /usr/lib/steamos/pacmandb 2>/dev/null | wc -l; pacman -Q 2>/dev/null | wc -l; ls -d /usr/lib/*/pacmandb /var/lib/pacman 2>&1'
    x sh -c 'ls -la /usr/lib/steamos-atomupd/client.conf /etc/steamos-atomupd/client.conf 2>&1'
    x sh -c 'cat /etc/atomic-update.conf 2>/dev/null | head -60; ls /etc/atomic-update.conf.d /usr/lib/atomic-update.conf.d 2>&1'
    x sh -c 'find / -xdev -name "*atomic-update*keep*" 2>/dev/null; find /usr/share/steamos-atomupd /usr/lib/rauc -maxdepth 2 2>/dev/null | head'
    x sh -c 'blkid; for p in /dev/nvme0n1p*; do echo "$p $(blkid -o value -s TYPE $p) $(blkid -o value -s LABEL $p)"; done'
    x sgdisk -p /dev/nvme0n1
    x sh -c 'for i in 1 2 3 4 5 6 7 8; do sgdisk -i $i /dev/nvme0n1 | grep -E "Partition name|Attribute"; done'
    x sh -c 'for p in $(blkid -t TYPE=btrfs -o device); do echo "$p $(btrfs inspect-internal dump-super $p | grep -E "^(fsid|label|incompat_flags|compat_ro_flags)")"; done'
    x sh -c 'ls /etc/systemd/system /var/lib/overlays/etc/upper'
    x efibootmgr -v
    x sh -c 'for f in /usr/bin/jupiter-biosupdate /usr/bin/jupiter-controller-update; do echo "== $f"; sed -n "1,40p" $f | grep -nE "DMI|product|board|Skipping|exit" ; done'
    x sh -c 'journalctl -b -u jupiter-biosupdate -u jupiter-controller-update -u steamos-finish-oobe-migration -u holo-finish-oobe-migration --no-pager 2>&1 | tail -20'
    x sh -c 'zgrep -h -E "CONFIG_UNICODE|CONFIG_XFS_FS=" /proc/config.gz 2>/dev/null'
    x sh -c 'ls /usr/share/wayland-sessions /usr/share/xsessions'
    x sh -c 'journalctl -b --no-pager -o short-monotonic | grep -iE "credential|extra-unit|dropin|oobe|atomupd|steamos-update|holo-update" | head -40'
    x sh -c 'ls -la /home /home/.steamos /home/.steamos/offload 2>&1'
    x free -m
fi
if [[ $stage == updated ]]; then
    x ls -la /run/steamos-atomupd
    x cat /esp/SteamOS/conf/A.conf /esp/SteamOS/conf/B.conf
fi
umount $M/efi $M/var $M/root 2>/dev/null
true
