#!/bin/bash
# SteamOS 3: a test guest for BackOtter, installed by Valve's own installer
# (/home/deck/tools/repair_device.sh all, from Valve's recovery image)
# unattended, then seeded offline.  Run by make-steamos.sh as root in the
# recovery image's own system, by the unit bko-install.service that
# make-steamos.sh hands its systemd as an SMBIOS credential, from the
# second disk (a vfat image labelled BKOINST: this script, filled in, and
# the seed files).  Nothing is written into the recovery image's own
# partitions but what its running system writes anyway.  Its output goes to
# the second serial port (make-steamos.sh's install.log).  Prints
# BACKOTTER-INSTALL-OK or -FAILED; the unit then powers the VM off.
#
# 1. Valve's installer: NOPROMPT=1 POWEROFF=1 repair_device.sh all, onto
#    /dev/nvme0n1 (the target qcow2 on QEMU's NVMe; repair_device.sh
#    hardcodes it).  NOPROMPT skips every zenity step.  Its last step,
#    prompt_reboot, runs "systemctl poweroff": a systemctl first on PATH
#    (/run/bko-bin) answers that one call (poweroff or reboot) with a note
#    and passes every other on to /usr/bin/systemctl, so the seed below can
#    run before the real power-off.  The script's copy is printed in base64
#    between BKO-REPAIR-BEGIN and -END, for make-steamos.sh to pin.
# 2. The seed, offline: the target's partitions by number (the recovery
#    disk has the same partlabels: esp, efi-A, rootfs-A, var-A, home), with
#    no SteamOS code run on them.  Into var-A's /etc upper
#    (lib/overlays/etc/upper; var-A was just made by mkfs, so the upper is
#    made here with the modes the initrd gives it):
#      sshd.service enabled (multi-user.target.wants);
#      sshd_config.d/50-bko.conf: root by key only, keys also read from
#        /etc/ssh/bko_authorized_keys/%u (root's /root is an offload bind
#        of /home/.steamos/offload/root, made at the first boot);
#      the guest's host key (ssh_host_ed25519_key);
#      passwd, shadow, group, gshadow: the lower's, with root's and the
#        image user's password set and the user tester (UID 1001, in wheel)
#        added;
#      hostname @HOSTNAME@;
#      sddm.conf.d/zz-steamos-autologin.conf: the image user into Plasma
#        (Decision 12), so graphical.target is reached on QEMU's VGA and the
#        OOBE's Game Mode session (and its update) never starts;
#      atomic-update.conf.d/bko.conf: the keep list's own extension point,
#        so the seed's files survive updates;
#      bko-slot "a" (not keep-listed: each slot has its own letter).
#    The serial console (console=ttyS0,115200) on the linux line of both
#    slots' EFI/steamos/grub.cfg.  /home/tester on home.
#
# Settings, filled in by make-steamos.sh: @HOSTNAME@, @ROOT_PASSWORD_HASH@,
# @AUTHORIZED_KEY@ (one line), @HOST_KEY_B64@ (the private host key, base64).

set -uo pipefail
GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
HOST_KEY_B64='@HOST_KEY_B64@'
DISK=/dev/nvme0n1
REPAIR=/home/deck/tools/repair_device.sh
REPAIR_TIMEOUT=5400
fail() { echo "BACKOTTER-INSTALL-FAILED: $*"; exit 1; }
x() { echo "+ $*"; "$@" 2>&1; }

echo "BACKOTTER-INSTALL-START ($GUEST_NAME)"
date -u
x cat /etc/os-release
x cat /etc/steamos-atomupd/manifest.json; echo
x systemctl --version
x uname -r
x cat /proc/cmdline
x lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,PARTLABEL,PARTTYPE,UUID,PARTUUID,MOUNTPOINTS
x findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
[[ -b $DISK ]] || fail "no $DISK"
[[ -f $REPAIR ]] || fail "no $REPAIR"
x ls -la /home/deck/tools
echo "BKO repair_sha256=$(sha256sum <"$REPAIR" | cut -d' ' -f1)"
echo BKO-REPAIR-BEGIN; base64 -w76 "$REPAIR"; echo BKO-REPAIR-END
for t in jupiter-biosupdate jupiter-controller-update; do
    echo "BKO ${t}_sha256=$(sha256sum </usr/bin/$t 2>/dev/null | cut -d' ' -f1)"
done
x nvme id-ctrl "$DISK" -H | grep -iE 'sanicap|fna|oacs|^mn|^fr'
x nvme sanitize-log "$DISK"
echo "BKO sanitize_log_rc=$?"

# --- 1. Valve's installer --------------------------------------------------------
mkdir -p /run/bko-bin
cat >/run/bko-bin/systemctl <<'EOF'
#!/bin/bash
case ${1-} in
poweroff|reboot) echo "BKO: repair_device.sh asked for systemctl $*: held for the seed" >&2; exit 0 ;;
esac
exec /usr/bin/systemctl "$@"
EOF
chmod 755 /run/bko-bin/systemctl
echo "=== repair_device.sh all (NOPROMPT=1 POWEROFF=1)"
t0=$(date +%s)
env PATH=/run/bko-bin:$PATH NOPROMPT=1 POWEROFF=1 timeout "$REPAIR_TIMEOUT" bash "$REPAIR" all
rc=$?
echo "BKO repair_rc=$rc repair_seconds=$(($(date +%s) - t0))"
[[ $rc == 0 ]] || fail "repair_device.sh all exited $rc"
sync
x sfdisk -d "$DISK"
x lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTLABEL,UUID,PARTUUID "$DISK"
x blkid "$DISK"p*
x efibootmgr -v

# --- 2. The seed, offline -----------------------------------------------------------
part() { echo "$DISK"p"$1"; }
declare -A want=([1]=esp [2]=efi-A [3]=efi-B [4]=rootfs-A [5]=rootfs-B [6]=var-A [7]=var-B [8]=home)
for n in "${!want[@]}"; do
    l=$(blkid -o value -s PARTLABEL "$(part $n)")
    [[ $l == "${want[$n]}" ]] || fail "$(part $n) is '$l', not ${want[$n]}"
done
M=/run/bko-seed
mkdir -p $M/rootA $M/varA $M/home $M/efiA $M/efiB
mount -o ro "$(part 4)" $M/rootA || fail "mount rootfs-A"
mount "$(part 6)" $M/varA || fail "mount var-A"
mount "$(part 8)" $M/home || fail "mount home"
mount "$(part 2)" $M/efiA || fail "mount efi-A"
mount "$(part 3)" $M/efiB || fail "mount efi-B"
echo "=== var-A as the installer left it"; x find $M/varA -maxdepth 3
echo "=== home as the installer left it"; x find $M/home -maxdepth 3
echo "=== efi-A"; x find $M/efiA
x cat $M/efiA/EFI/steamos/grub.cfg
L=$M/rootA/etc
U=$M/varA/lib/overlays/etc/upper
install -d -m 755 $M/varA/lib $M/varA/lib/overlays $M/varA/lib/overlays/etc
install -d -m 755 $U
# The image user: UID 1000 in the lower passwd.
user=$(awk -F: '$3 == 1000 { print $1; exit }' $L/passwd)
[[ -n $user ]] || fail "no UID 1000 in the image's passwd"
echo "BKO image_user=$user"
x grep -E "^($user|root):" $L/passwd

# sshd
install -d -m 755 $U/systemd $U/systemd/system $U/systemd/system/multi-user.target.wants
unit=/usr/lib/systemd/system/sshd.service
[[ -e $M/rootA$unit ]] || fail "no $unit in the image"
ln -s $unit $U/systemd/system/multi-user.target.wants/sshd.service
install -d -m 755 $U/ssh $U/ssh/sshd_config.d $U/ssh/bko_authorized_keys
grep -nE '^[[:space:]]*Include' $L/ssh/sshd_config || echo "BKO note: the image's sshd_config has no Include"
cat >$U/ssh/sshd_config.d/50-bko.conf <<'EOF'
# BackOtter test guest (make-steamos.sh)
PermitRootLogin prohibit-password
AuthorizedKeysFile .ssh/authorized_keys /etc/ssh/bko_authorized_keys/%u
EOF
printf '%s\n' "$AUTHORIZED_KEY" >$U/ssh/bko_authorized_keys/root
printf '%s\n' "$AUTHORIZED_KEY" >$U/ssh/bko_authorized_keys/tester
chmod 644 $U/ssh/bko_authorized_keys/*
printf '%s' "$HOST_KEY_B64" | base64 -d >$U/ssh/ssh_host_ed25519_key
chmod 600 $U/ssh/ssh_host_ed25519_key
ssh-keygen -y -f $U/ssh/ssh_host_ed25519_key >$U/ssh/ssh_host_ed25519_key.pub || fail "the host key"
chmod 644 $U/ssh/ssh_host_ed25519_key.pub

# Users: root's and the image user's password, and tester.
for f in passwd group; do cp -a $L/$f $U/$f; done
for f in shadow gshadow; do cp -a $L/$f $U/$f; done
awk -F: -v OFS=: -v u="$user" -v h="$ROOT_PASSWORD_HASH" '$1 == "root" || $1 == u { $2 = h } { print }' $L/shadow >$U/shadow
grep -q '^tester:' $U/passwd && fail "the image already has a tester"
echo "tester:x:1001:1001:BackOtter tester:/home/tester:/bin/bash" >>$U/passwd
echo "tester:$ROOT_PASSWORD_HASH:20000:0:99999:7:::" >>$U/shadow
echo "tester:x:1001:" >>$U/group
echo "tester:!::" >>$U/gshadow
sed -i -E 's/^(wheel:[^:]*:[^:]*:)(.*)$/\1\2,tester/; s/^(wheel:[^:]*:[^:]*:),/\1/' $U/group
grep -q '^wheel:' $U/gshadow && sed -i -E 's/^(wheel:[^:]*:[^:]*:)(.*)$/\1\2,tester/; s/^(wheel:[^:]*:[^:]*:),/\1/' $U/gshadow
x grep -E '^(wheel|tester):' $U/group
install -d -m 700 -o 1001 -g 1001 $M/home/tester

echo "$GUEST_NAME" >$U/hostname

# The session: Plasma, autologin as the image user.
x ls $L/sddm.conf.d $M/rootA/usr/lib/sddm/sddm.conf.d 2>/dev/null
x grep -rh -A3 '^\[Autologin\]' $L/sddm.conf.d $M/rootA/usr/lib/sddm/sddm.conf.d 2>/dev/null
x ls $M/rootA/usr/share/wayland-sessions $M/rootA/usr/share/xsessions
session=plasma
[[ -e $M/rootA/usr/share/wayland-sessions/plasma.desktop || -e $M/rootA/usr/share/xsessions/plasma.desktop ]] ||
    fail "no plasma.desktop session in the image"
install -d -m 755 $U/sddm.conf.d
cat >$U/sddm.conf.d/zz-steamos-autologin.conf <<EOF
# BackOtter test guest (make-steamos.sh): Plasma, not Game Mode (docs/design/steamos.md, Decision 12)
[Autologin]
User=$user
Session=$session
Relogin=true
EOF

# The keep list's extension point.
x ls $L/atomic-update.conf.d $M/rootA/usr/lib/steamos-atomupd 2>/dev/null
x cat $L/atomic-update.conf
install -d -m 755 $U/atomic-update.conf.d
cat >$U/atomic-update.conf.d/bko.conf <<'EOF'
# BackOtter test guest (make-steamos.sh): keep the seed across updates.
/etc/ssh/sshd_config.d/*
/etc/ssh/bko_authorized_keys/*
/etc/ssh/ssh_host_*
/etc/sddm.conf.d/zz-steamos-autologin.conf
/etc/hostname
/etc/passwd
/etc/shadow
/etc/group
/etc/gshadow
/etc/systemd/system/multi-user.target.wants/sshd.service
EOF

echo a >$U/bko-slot
x find $U -ls

# The serial console in both slots' grub.cfg.
for e in efiA efiB; do
    c=$M/$e/EFI/steamos/grub.cfg
    [[ -f $c ]] || fail "no $c"
    grep -q 'console=ttyS0' "$c" || sed -i -E '/^[[:space:]]*(steamenv_boot[[:space:]]+)?linux[[:space:]]/ s/$/ console=ttyS0,115200/' "$c"
    grep -E '^[[:space:]]*(steamenv_boot[[:space:]]+)?linux[[:space:]]' "$c" | grep -q 'console=ttyS0,115200' || fail "$c: no serial console"
    echo "BKO grub_linux_$e=$(grep -m1 -E '^[[:space:]]*(steamenv_boot[[:space:]]+)?linux[[:space:]]' "$c" | tr -s ' \t' ' ')"
    echo "BKO grub_search_$e=$(grep -m1 -E '^[[:space:]]*search' "$c" | tr -s ' \t' ' ')"
done
sync
umount $M/efiA $M/efiB $M/home $M/varA $M/rootA || fail "umount"
sync
echo BACKOTTER-INSTALL-OK
