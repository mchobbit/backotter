#!/bin/bash
# Run as root in AnduinOS 2's live system by make-anduinos.sh (fetched by
# kernel-command-line.service, systemd.run=): install AnduinOS on /dev/vda with
# its own installer backend, unattended, then give the guest what the other
# test guests get, record what the build found, and power off.
#
# 1. plan.py makes the installer's InstallPlan for "erase disk, Btrfs" the
#    way its GUI does (frontend.py, installer_core.planning.build_plan), and
#    anduinos-installer-executor runs it: one JSON line on stdin, no
#    arguments and no environment marker (its RELEASE policy), its JSON-lines
#    events on this script's output, which is the serial console.
# 2. On the installed system (Btrfs subvolume @root at /, the ESP at
#    /boot/efi): root's password (the same hash as tester's) and SSH key;
#    sshd enabled (the installer leaves ssh.service and ssh.socket disabled
#    unless its "SSH password login" box is ticked); text mode at boot, a
#    serial console (GRUB_CMDLINE_LINUX), update-grub; the snapshot manager's
#    scheduler timer disabled (the image keeps the installer's baselines only;
#    its apt hook stays, and vm-update's in-guest.sh runs apt without it).
# 3. The facts, as a tar written raw on /dev/vdb: os-release, the partition
#    table, fstab, the ESP's files with their SHA-256s, the subvolumes, the
#    MOK's certificate, the executor's events.
# The last line is BACKOTTER-INSTALL-OK, or BACKOTTER-INSTALL-FAILED: WHY.
set -uo pipefail

HOSTNAME='@HOSTNAME@'
USERNAME='@USERNAME@'
PASSWORD_HASH='@PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
URL='@URL@'
DISK=/dev/vda FACTS_DISK=/dev/vdb
T=/mnt/bko-target TOP=/mnt/bko-top F=/run/bko-facts

mkdir -p $F
# The serial console opened afresh for each line: a getty's vhangup on ttyS0
# would cut off an fd held open (the kernel line masks it too).
exec > >(while IFS= read -r l; do
    printf '%s\n' "$l" >>$F/install.log; printf '%s\r\n' "$l" >/dev/ttyS0 2>/dev/null
done) 2>&1
say() { echo "bko-install: $*"; }
save_facts() {
    sync
    if [[ -b $FACTS_DISK ]]; then
        tar -C $F -cf $FACTS_DISK . && sync && say "facts written to $FACTS_DISK"
    fi
}
cleanup_mounts() {
    local m
    for m in $T/dev/pts $T/dev $T/proc $T/sys $T/run $T/boot/efi $T $TOP; do
        mountpoint -q $m && umount $m
    done
}
finish() {
    cleanup_mounts
    save_facts
    echo "$1"
    sleep 2
    systemctl poweroff
    exit 0
}
fail() { finish "BACKOTTER-INSTALL-FAILED: $*"; }
in_t() { chroot $T "$@"; }

say "the live system: $(uname -r), $(. /etc/os-release; echo "$PRETTY_NAME")"
say "kernel command line: $(cat /proc/cmdline)"
for _ in $(seq 60); do [[ -b $DISK && -b $FACTS_DISK ]] && break; sleep 1; done
[[ -b $DISK ]] || fail "no $DISK"

curl -fsS -o /run/bko-plan.py "$URL/plan.py" || fail "could not fetch plan.py"
umask 077
printf '%s\n' "$PASSWORD_HASH" >/run/bko-password-hash
umask 022

# --- 1. The installer --------------------------------------------------------
dpkg-query -W anduinos-installer-beta anduinos-grub anduinos-shim-signed anduinos-secureboot-toolkit \
    anduinos-btrfs-snapshots-manager 2>&1 | sed 's/^/bko-install: live package: /'
python3 /run/bko-plan.py $DISK "$HOSTNAME" "$USERNAME" /run/bko-password-hash >/run/bko-plan.json ||
    fail "plan.py could not make the plan"
say "running anduinos-installer-executor"
/usr/bin/anduinos-installer-executor </run/bko-plan.json | tee $F/executor.jsonl | sed -u 's/^/executor: /'
rc=${PIPESTATUS[0]}
rm -f /run/bko-plan.json /run/bko-password-hash
last=$(tail -n 1 $F/executor.jsonl)
say "executor exit $rc, last event: $last"
[[ $rc == 0 && $last == '{"event": "complete", "error": ""}' ]] || fail "the executor failed: $last"

# --- 2. What the other test guests get ---------------------------------------
btrfs_dev=$(lsblk -lnpo NAME,FSTYPE $DISK | awk '$2 == "btrfs" { print $1; exit }')
esp_dev=$(lsblk -lnpo NAME,FSTYPE $DISK | awk '$2 == "vfat" { print $1; exit }')
[[ -n $btrfs_dev && -n $esp_dev ]] || fail "no Btrfs or no ESP on $DISK: $(lsblk -lnpo NAME,FSTYPE $DISK | tr '\n' ' ')"
mkdir -p $T $TOP
mount -o subvol=@root,compress=zstd:3 "$btrfs_dev" $T || fail "could not mount @root"
mount "$esp_dev" $T/boot/efi || fail "could not mount the ESP"
for d in dev dev/pts proc sys run; do mount --bind /$d $T/$d || fail "could not bind /$d"; done

printf 'root:%s\n' "$PASSWORD_HASH" | in_t chpasswd -e || fail "chpasswd"
install -d -m 700 $T/root/.ssh
printf '%s\n' "$AUTHORIZED_KEY" >$T/root/.ssh/authorized_keys
chmod 600 $T/root/.ssh/authorized_keys
in_t systemctl enable ssh.service || fail "could not enable ssh.service"
in_t systemctl set-default multi-user.target || fail "set-default"
in_t systemctl disable anduinos-btrfs-snapshots-manager-scheduler.timer || fail "could not disable the scheduler timer"
# The serial console (ttyS0 last: it is /dev/console), and no quiet splash.
g=$T/etc/default/grub
sed -i -e 's/^GRUB_CMDLINE_LINUX=.*/GRUB_CMDLINE_LINUX="console=tty0 console=ttyS0,115200n8"/' \
    -e 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=""/' $g
grep -q '^GRUB_CMDLINE_LINUX="console=tty0 console=ttyS0,115200n8"$' $g || fail "GRUB_CMDLINE_LINUX not set"
in_t update-grub || fail "update-grub"
in_t sshd -T 2>/dev/null | grep -Ei '^(permitrootlogin|passwordauthentication) ' | sed 's/^/bko-install: sshd: /'

# --- 3. The facts ------------------------------------------------------------
cp $T/usr/lib/os-release $F/os-release
sfdisk -d $DISK >$F/partition-table.txt
lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTTYPENAME,PARTLABEL,LABEL,UUID,MOUNTPOINTS $DISK >$F/lsblk.txt
blkid >$F/blkid.txt
cp $T/etc/fstab $F/fstab
cp $T/etc/default/grub $F/etc-default-grub
(cd $T/boot/efi && find . -type f | sort | xargs -d '\n' sha256sum) >$F/esp-files.sha256
(cd $T/boot/efi && find . | sort | xargs -d '\n' ls -ld --time-style=+) >$F/esp-ls.txt
cp $T/boot/efi/EFI/AnduinOS/grub.cfg $F/esp-grub.cfg 2>/dev/null
ls -la $T/boot >$F/boot-ls.txt
cp $T/var/lib/shim-signed/mok/MOK.der $F/MOK.der || fail "no MOK.der"
efibootmgr -v >$F/efibootmgr.txt 2>&1
in_t dpkg-query -W >$F/packages.txt
in_t systemctl is-enabled ssh.service ssh.socket anduinos-btrfs-snapshots-manager-scheduler.timer \
    anduinos-btrfs-snapshots-manager-confirm.service >$F/units.txt 2>&1
mount -o subvolid=5 "$btrfs_dev" $TOP || fail "could not mount the Btrfs top level"
{ btrfs subvolume list -t -a -u -q -R $TOP; echo; echo "read-only:"; btrfs subvolume list -r -a $TOP; } >$F/subvolumes.txt
btrfs filesystem usage $TOP >$F/btrfs-usage.txt 2>&1
btrfs filesystem show "$btrfs_dev" >$F/btrfs-show.txt 2>&1
findmnt -no OPTIONS $T >$F/root-mount-options.txt
(cd $T/var/lib && find anduinos-btrfs-snapshots-manager -maxdepth 3 | sort) >$F/manager-state.txt 2>&1
(cd $T/etc && find anduinos-btrfs-snapshots-manager | sort) >>$F/manager-state.txt 2>&1
(cd $TOP && find @snapshots -maxdepth 4 | sort) >$F/snapshots-tree.txt 2>&1
cleanup_mounts
finish BACKOTTER-INSTALL-OK
