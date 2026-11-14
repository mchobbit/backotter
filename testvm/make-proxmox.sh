#!/usr/bin/env bash
# Build a Proxmox VE 9 test guest for BackOtter with Proxmox's own automated
# installer: the ISO prepared by proxmox-auto-install-assistant to fetch its
# answer file from a partition labelled PROXMOX-AIS, with a first-boot
# script (proxmox/first-boot.sh) built in, into qcow2 disk images, under QEMU
# with KVM and OVMF (UEFI, no Secure Boot).  ZFS root, booted by
# systemd-boot through proxmox-boot-tool (docs/design/proxmox.md, Decision 1).
#
# Usage: ./make-proxmox.sh [--iso-dir DIR] FLAVOR WORK_DIR
#   e.g. ./make-proxmox.sh --iso-dir /bkoscratch/rebuild-inputs/proxmox9 proxmox9 vms/proxmox9
#
# FLAVOR is the guest:
#   proxmox9         one 24 GiB disk, vda, ZFS "raid0": one ESP
#   proxmox9-mirror  two 24 GiB disks, vda and vdb, ZFS "raid1" (a mirror):
#                    an ESP on each disk, both in proxmox-boot-uuids
#
# The inputs, looked for in DIR (default WORK_DIR), none downloaded here:
#   proxmox-ve_9.2-1.iso and its .asc   checked against the SHA-256 pinned
#                    below, and the signature against Proxmox's trixie
#                    release key (its fingerprint pinned below)
#   proxmox-release-trixie.gpg   that key
#   proxmox-auto-install-assistant_9.2.8_amd64.deb (in DIR or DIR/assistant)
#                    from Proxmox's trixie pve-no-subscription repository,
#                    its SHA-256 pinned below (checked once against the
#                    signed InRelease); unpacked into a scratch directory and
#                    run from there, never installed
# The prepared ISO is made in DIR once per first-boot script:
# proxmox-ve_9.2-1-auto-part-H.iso, H the first 12 hex digits of
# proxmox/first-boot.sh's SHA-256 (the script is inside it), with its
# SHA-256 beside it in .sha256 and checked on every later use.
#
# Per build: root's password (new each build; its SHA-512 crypt hash in the
# answer file's root-password-hashed), an SSH key (root-ssh-keys), and a
# 16 MiB vfat image labelled PROXMOX-AIS holding answer.toml (validated with
# the assistant's validate-answer first), attached as a SATA disk for the
# install only.  The installer powers QEMU off when it is done (reboot-mode
# "power-off").  Then the first boot: the installed system runs
# first-boot.sh (pve-no-subscription, the serial console, the user tester,
# a zvol and a container dataset in rpool/data), which prints
# BACKOTTER-INSTALL-OK on the serial console and powers off.  Then a boot
# through the installer's NVRAM entry, where first-boot.txt is collected,
# and one as vm-run boots it, with fresh UEFI variables, through the
# fallback loader EFI/BOOT/BOOTX64.EFI (proxmox9-mirror: from each disk
# alone too, the pool degraded).
#
# Results, in WORK_DIR:
#   FLAVOR.qcow2          the guest's first disk (sparse), with a qcow2
#                         snapshot "fresh-install" taken after the first boot
#   FLAVOR-b.qcow2        proxmox9-mirror: the second disk, the same snapshot
#   uefi-vars.fd          the OVMF variables of the install, with the
#                         installer's systemd-boot NVRAM entry, for
#                         OVMF_CODE_4M.fd on a plain q35 machine (vm-run
#                         --no-secure-boot's): ht1.sh and bs1-qemu.py boot a
#                         copy of it (vm-run --uefi-vars), vm-run alone a
#                         fresh store, so the fallback loader
#   root-password         the password of root and of "tester" (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   answer.toml           the answer file (mode 600: it has the hash)
#   auto-iso.txt          which prepared ISO, and its inspect-iso
#   installer.log         the installer's own log, from the installed system
#                         (/var/log/proxmox-installer.log)
#   install-serial.log, first-boot-serial.log, nvram-boot-serial.log,
#   fallback-boot-serial.log (-a, -b: each disk alone)   the serial consoles
#   first-boot.txt        what the boot through the NVRAM entry showed
#                         (findmnt, partitions, zpool and zfs, proxmox-boot-tool
#                         status and its files, each ESP's files from a
#                         read-only mount, efibootmgr, os-release, pveversion,
#                         the kernels, apt's sources, pmxcfs, failed units),
#                         then the fallback boots'
#   vm-run-options        "--uefi --no-secure-boot" (proxmox9-mirror: with
#                         "--disk proxmox9-mirror-b.qcow2"), how vm-run must
#                         boot it
#
# The guests, host name pve (fqdn pve.test, the installer's default name,
# known to the redactor): UEFI + GPT, the installer's ZFS layout on each disk
# (BIOS boot, ESP, ZFS), pool rpool with rpool/ROOT/pve-1 at /, rpool/data,
# Proxmox's kernel and OpenZFS, no swap, pmxcfs at /etc/pve; sshd on, 4 GB
# RAM, 2 CPUs.
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, mtools,
# dosfstools (mkfs.fat), dpkg-deb, gpg, openssl, python3, ssh, and network
# access for the first boot's apt-get update.  Runs as an ordinary user in
# the kvm group.

set -euo pipefail

ISO=proxmox-ve_9.2-1.iso
ISO_SHA256=4e88fe416df9b527624a175f24c9aa07c714d3332afb1ee3dbf3879573ef2c6c
# "Proxmox Bookworm Release Key"'s successor for trixie, which signs the ISOs
PROXMOX_KEY=24B30F06ECC1836A4E5EFECBA7BCD1420BFE778E
AIA_DEB=proxmox-auto-install-assistant_9.2.8_amd64.deb
AIA_DEB_SHA256=94b562ac026bf9a989e0a5834538ad24e38606f8858367af0e665af0bcbdc2e2
DISK_SIZE=24G
MEMORY=4096
CPUS=2
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=3600
FIRST_BOOT_TIMEOUT=1800
export PATH=$PATH:/usr/sbin:/sbin

here=$(dirname "$(realpath "$0")")
die() { echo "make-proxmox: $*" >&2; exit 1; }

iso_dir=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --iso-dir) iso_dir=$(realpath "$2"); shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || die "usage: $0 [--iso-dir DIR] FLAVOR WORK_DIR"
flavor=$1
case $flavor in
    proxmox9) RAID=raid0 NDISKS=1 ;;
    proxmox9-mirror) RAID=raid1 NDISKS=2 ;;
    *) die "FLAVOR is proxmox9 or proxmox9-mirror, not '$flavor'" ;;
esac
GUEST_NAME=$flavor
DISK=$GUEST_NAME.qcow2
DISK_B=$GUEST_NAME-b.qcow2
disks=("$DISK"); [[ $NDISKS == 2 ]] && disks+=("$DISK_B")
first_boot=$here/proxmox/first-boot.sh
[[ -f $first_boot ]] || die "no $first_boot"
mkdir -p "$2"
cd "$2"
for d in "${disks[@]}"; do [[ -e $d ]] && die "$2/$d already exists; move it away to rebuild"; done
[[ -n $iso_dir ]] || iso_dir=$PWD

work=$(mktemp -d)
qemu_pid=""
cleanup() {
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
trap cleanup EXIT

# --- Check the ISO and the assistant -------------------------------------------------
for f in "$ISO" "$ISO.asc" proxmox-release-trixie.gpg; do
    [[ -f $iso_dir/$f && ! -e $iso_dir/$f.part ]] || die "no $iso_dir/$f (fetch it first: see the header)"
done
[[ $(sha256sum <"$iso_dir/$ISO" | cut -d' ' -f1) == "$ISO_SHA256" ]] ||
    die "$ISO's SHA-256 isn't the pinned $ISO_SHA256"
mkdir -m 700 "$work/gnupg"
gpg --homedir "$work/gnupg" --batch -q --import "$iso_dir/proxmox-release-trixie.gpg" 2>/dev/null
# VALIDSIG's last field is the primary key's fingerprint.  The .asc also
# carries a signature by the bookworm key, which isn't imported, so gpg
# exits non-zero (NO_PUBKEY) even when the trixie signature is good: its
# status lines decide, not its exit code.
gpg_status=$(gpg --homedir "$work/gnupg" --batch --status-fd 1 --verify "$iso_dir/$ISO.asc" "$iso_dir/$ISO" 2>/dev/null || true)
awk -v k="$PROXMOX_KEY" '$1 == "[GNUPG:]" && $2 == "VALIDSIG" && $NF == k { ok = 1 } END { exit !ok }' <<<"$gpg_status" ||
    die "$ISO.asc isn't a good signature of $ISO by $PROXMOX_KEY"
gpgconf --homedir "$work/gnupg" --kill all 2>/dev/null || true
deb=$iso_dir/$AIA_DEB; [[ -f $deb ]] || deb=$iso_dir/assistant/$AIA_DEB
[[ -f $deb ]] || die "no $AIA_DEB in $iso_dir or $iso_dir/assistant"
[[ $(sha256sum <"$deb" | cut -d' ' -f1) == "$AIA_DEB_SHA256" ]] || die "$AIA_DEB's SHA-256 isn't the pinned one"
dpkg-deb -x "$deb" "$work/aia"
aia=$work/aia/usr/bin/proxmox-auto-install-assistant
[[ -x $aia ]] || die "$AIA_DEB has no usr/bin/proxmox-auto-install-assistant"

# --- The prepared ISO, once per first-boot script --------------------------------------
fb_hash=$(sha256sum <"$first_boot" | cut -c1-12)
AUTO_ISO=${ISO%.iso}-auto-part-$fb_hash.iso
if [[ ! -f $iso_dir/$AUTO_ISO ]]; then
    echo "Preparing $iso_dir/$AUTO_ISO (fetch-from partition, first-boot.sh $fb_hash)"
    # (Under a temporary name, renamed when complete: a concurrent build
    # of the other flavor may do the same.)
    tmp_iso=$iso_dir/.$AUTO_ISO.$$
    "$aia" prepare-iso --fetch-from partition --partition-label proxmox-ais \
        --on-first-boot "$first_boot" --tmp "$work" --output "$tmp_iso" "$iso_dir/$ISO" ||
        { rm -f "$tmp_iso"; die "prepare-iso failed"; }
    sha256sum <"$tmp_iso" | cut -d' ' -f1 >"$tmp_iso.sha256"
    mv "$tmp_iso.sha256" "$iso_dir/$AUTO_ISO.sha256"
    mv "$tmp_iso" "$iso_dir/$AUTO_ISO"
fi
[[ -f $iso_dir/$AUTO_ISO.sha256 ]] || die "no $iso_dir/$AUTO_ISO.sha256"
[[ $(sha256sum <"$iso_dir/$AUTO_ISO" | cut -d' ' -f1) == "$(cat "$iso_dir/$AUTO_ISO.sha256")" ]] ||
    die "$AUTO_ISO doesn't match its .sha256"
{ echo "$AUTO_ISO, first-boot.sh $fb_hash"; "$aia" inspect-iso "$iso_dir/$AUTO_ISO" 2>&1; } >auto-iso.txt || true

# --- The installer's kernel, initrd and options ----------------------------------------
# The prepared ISO's GRUB entry for the automated installer, without quiet
# and splash, the console on the serial port too.
xorriso -osirrox on -indev "$iso_dir/$AUTO_ISO" -extract /boot/grub/grub.cfg "$work/grub.cfg" 2>/dev/null ||
    die "$AUTO_ISO lacks /boot/grub/grub.cfg"
entry=$(awk '/^[ \t]*menuentry/ { on = 0 } /^[ \t]*linux.*proxmox-start-auto-installer/ { on = 1 }
    on && /^[ \t]*(linux|initrd)[ \t]/ { print } on && /^[ \t]*}/ { exit }' "$work/grub.cfg")
kernel=$(awk '$1 == "linux" { print $2; exit }' <<<"$entry")
initrd=$(awk '$1 == "initrd" { print $2; exit }' <<<"$entry")
options=$(awk '$1 == "linux" { $1 = $2 = ""; print; exit }' <<<"$entry" | sed -E 's/(^| )(quiet|splash=[^ ]*|splash)( |$)/ /g; s/  +/ /g')
[[ -n $kernel && -n $initrd && $options == *proxmox-start-auto-installer* ]] ||
    die "no automated-installer entry in $AUTO_ISO's grub.cfg"
xorriso -osirrox on -indev "$iso_dir/$AUTO_ISO" -extract "$kernel" "$work/vmlinuz" \
    -extract "$initrd" "$work/initrd.img" 2>/dev/null || die "could not extract $kernel and $initrd"
chmod u+w "$work"/vmlinuz "$work"/initrd.img

# --- Secrets, the answer file and its disk ---------------------------------------------
umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
disk_list='"vda"'; [[ $NDISKS == 2 ]] && disk_list='"vda", "vdb"'
cat >answer.toml <<EOF
# $GUEST_NAME, made by make-proxmox.sh $(date -u +%Y-%m-%dT%H:%MZ)
[global]
keyboard = "en-us"
country = "us"
fqdn = "pve.test"
mailto = "root@pve.test"
timezone = "UTC"
root-password-hashed = "$hash"
root-ssh-keys = ["$(cat id_ed25519.pub)"]
reboot-mode = "power-off"

[network]
source = "from-dhcp"

[disk-setup]
filesystem = "zfs"
zfs.raid = "$RAID"
disk-list = [$disk_list]

[first-boot]
source = "from-iso"
ordering = "fully-up"
EOF
umask 022
"$aia" validate-answer answer.toml >"$work/validate.txt" 2>&1 || { cat "$work/validate.txt" >&2; die "answer.toml isn't valid"; }
grep -q 'parsed successfully, no errors found' "$work/validate.txt" && ! grep -qi 'warning' "$work/validate.txt" ||
    { cat "$work/validate.txt" >&2; die "validate-answer has something to say of answer.toml"; }
mkfs.fat -C -n PROXMOX-AIS "$work/ais.img" 16384 >/dev/null
mcopy -i "$work/ais.img" answer.toml ::/answer.toml

port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
ssh_root=(ssh -q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 -o BatchMode=yes root@127.0.0.1)
wait_ssh() {  # SECONDS
    local _
    for _ in $(seq $(($1 / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || return 1
        "${ssh_root[@]}" true </dev/null 2>/dev/null && return 0
        sleep 5
    done
    return 1
}
wait_exit() {  # SECONDS: whether QEMU exited within them
    local _
    for _ in $(seq $(($1 / 5))); do kill -0 "$qemu_pid" 2>/dev/null || { wait "$qemu_pid" 2>/dev/null || true; qemu_pid=""; return 0; }; sleep 5; done
    return 1
}
stop_guest() {
    "${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || true
    wait_exit 120 && return 0
    kill "$qemu_pid" 2>/dev/null
    die "the guest did not power off"
}
# Plain q35, as vm-run's --no-secure-boot machine (OVMF_CODE_4M.fd has no
# SMM; vm-run's header says why that matters for the variable store).
uefi=(-machine q35 -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
    -drive "if=pflash,format=raw,unit=1,file=uefi-vars.fd")
disk_args() {  # the guest's disks, vda (and vdb), in order
    local d
    for d in "$@"; do printf '%s\n' -drive "file=$d,if=virtio,format=qcow2,discard=unmap"; done
}
mapfile -t all_disks < <(disk_args "${disks[@]}")
net=(-nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22")
common=(-enable-kvm -cpu host -smp "$CPUS" -m "$MEMORY")

# --- Install --------------------------------------------------------------------------
cp "$OVMF_VARS" uefi-vars.fd
for d in "${disks[@]}"; do qemu-img create -q -f qcow2 "$d" "$DISK_SIZE"; done
echo "Installing (the console in $2/install-serial.log)"
t0=$SECONDS
# The ISO and the answer disk on the SATA (AHCI) controller, out of the way
# of vda and vdb.
qemu-system-x86_64 "${common[@]}" "${uefi[@]}" "${all_disks[@]}" \
    -drive "file=$iso_dir/$AUTO_ISO,media=cdrom,readonly=on,if=ide,index=0" \
    -drive "file=$work/ais.img,format=raw,if=ide,index=1" \
    "${net[@]}" -kernel "$work/vmlinuz" -initrd "$work/initrd.img" \
    -append "$options console=tty0 console=ttyS0,115200n8" \
    -display none -serial file:install-serial.log -monitor none -no-reboot &
qemu_pid=$!
wait_exit "$INSTALL_TIMEOUT" || { kill "$qemu_pid"; die "the install didn't finish in $INSTALL_TIMEOUT s; see install-serial.log"; }
echo "The installer powered off after $((SECONDS - t0)) s"
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 500000000 ]] ||
    die "the disk is nearly empty; see install-serial.log"

# --- First boot: first-boot.sh runs, prints BACKOTTER-INSTALL-OK and powers off --------
echo "First boot (first-boot.sh; the console in $2/first-boot-serial.log)"
t0=$SECONDS
qemu-system-x86_64 "${common[@]}" "${uefi[@]}" "${all_disks[@]}" "${net[@]}" \
    -display none -serial file:first-boot-serial.log -monitor none &
qemu_pid=$!
wait_exit "$FIRST_BOOT_TIMEOUT" || { kill "$qemu_pid"; die "the first boot didn't power off in $FIRST_BOOT_TIMEOUT s; see first-boot-serial.log"; }
grep -a 'bko-first-boot:' first-boot-serial.log | tr -d '\r' >&2 || true
grep -aq 'BACKOTTER-INSTALL-OK' first-boot-serial.log || die "first-boot.sh didn't finish; see first-boot-serial.log"
echo "The first boot powered off after $((SECONDS - t0)) s"

# --- Through the installer's NVRAM entry: first-boot.txt -------------------------------
qemu-system-x86_64 "${common[@]}" "${uefi[@]}" "${all_disks[@]}" "${net[@]}" \
    -display none -serial file:nvram-boot-serial.log -monitor none &
qemu_pid=$!
wait_ssh 600 || die "the boot through the NVRAM entry never answered on SSH; see nvram-boot-serial.log"
"${ssh_root[@]}" 'set -x; systemctl is-system-running --wait; cat /proc/cmdline; uname -r; hostname; hostname -f
    cat /etc/hostname /etc/hosts /etc/fstab; od -An -tx1 /etc/hostid; cat /etc/os-release; cat /etc/debian_version
    findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /; findmnt -rno TARGET,SOURCE,FSTYPE; mount | grep /etc/pve
    lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTTYPENAME,PARTLABEL,LABEL,UUID,PARTUUID
    for d in /dev/vd?; do sfdisk -d $d; done; blkid
    zfs version; zpool status; zpool list -v; zpool get all rpool | grep -v -E "feature@.*(disabled)"
    zfs list -o name,used,mountpoint,canmount,mounted; zfs list -t volume; zfs get -s local,received all
    proxmox-boot-tool status; cat /etc/kernel/proxmox-boot-uuids; cat /etc/kernel/cmdline
    ls -la /etc/kernel; cat /etc/kernel/proxmox-boot-pin /etc/kernel/proxmox-boot-manual-kernels
    proxmox-boot-tool kernel list
    for u in $(cat /etc/kernel/proxmox-boot-uuids); do mkdir -p /run/bko-esp; mount -o ro,noexec,nodev /dev/disk/by-uuid/$u /run/bko-esp &&
        { df -k /run/bko-esp; find /run/bko-esp -printf "%M %10s %P\n" | sort -k3; cat /run/bko-esp/loader/loader.conf /run/bko-esp/loader/entries/*.conf; umount /run/bko-esp; }; done
    efibootmgr -v; ls -l /boot; ls /etc/initramfs/post-update.d /etc/kernel/postinst.d
    pveversion -v; dpkg -l "proxmox-kernel*" "proxmox-default-kernel" "proxmox-ve" "pve-manager" "zfsutils-linux" "zfs-initramfs" "proxmox-kernel-helper" | grep ^ii
    ls -l /etc/apt/sources.list /etc/apt/sources.list.d; head -n 50 /etc/apt/sources.list.d/*; apt-get -q update
    ls -la /etc/pve /etc/pve/nodes; ls -l /var/lib/pve-cluster; ls -ld /var/lib/vz; findmnt /var/lib/vz
    getent passwd tester; ls -la /rpool/data/subvol-101-disk-0
    journalctl -b -1 --no-pager -o cat -u "proxmox-first-boot*"
    swapon --show; systemctl --failed --no-legend' \
    </dev/null >first-boot.txt 2>&1 || true
"${ssh_root[@]}" 'cat /var/log/proxmox-installer.log' </dev/null >installer.log 2>/dev/null || true
grep -Eq '^ *(running|degraded)$' first-boot.txt || { cat first-boot.txt >&2; die "the boot through the NVRAM entry isn't up"; }
grep -Eq '(^| )root=ZFS=rpool/ROOT/pve-1( |$)' first-boot.txt || die "/proc/cmdline has no root=ZFS=rpool/ROOT/pve-1"
grep -Eq '(^| )console=ttyS0,115200n8( |$)' first-boot.txt || die "/proc/cmdline has no console=ttyS0 (first-boot.sh's refresh)"
grep -Eq '^/ +rpool/ROOT/pve-1 +zfs ' first-boot.txt || die "/ isn't rpool/ROOT/pve-1"
grep -Eq '^/dev/fuse on /etc/pve type fuse' first-boot.txt || die "pmxcfs isn't mounted at /etc/pve"
grep -q '^rpool/data/vm-100-disk-0 ' first-boot.txt && grep -q '^rpool/data/subvol-101-disk-0 ' first-boot.txt ||
    die "first-boot.sh's datasets aren't there"
[[ $(sed -n '/^+ cat \/etc\/kernel\/proxmox-boot-uuids$/,/^+ /p' first-boot.txt | grep -Ec '^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$') == "$NDISKS" ]] ||
    die "/etc/kernel/proxmox-boot-uuids doesn't list $NDISKS ESPs"
status=$(sed -n '/^+ proxmox-boot-tool status$/,/^+ /p' first-boot.txt)
[[ $(grep -c 'is configured with: uefi' <<<"$status") == "$NDISKS" ]] && ! grep -qE 'WARN|E:|not found' <<<"$status" ||
    { echo "$status" >&2; die "proxmox-boot-tool status isn't $NDISKS clean uefi ESPs"; }
[[ -z $(sed -n '/^+ systemctl --failed --no-legend$/,$p' first-boot.txt | tail -n +2) ]] ||
    die "the boot has failed units; see first-boot.txt"
current=$(awk '$1 == "BootCurrent:" { print $2 }' first-boot.txt)
grep -E "^Boot$current\\*? " first-boot.txt | grep -qiF 'systemd-bootx64.efi' ||
    die "the boot didn't come through systemd-boot's NVRAM entry (BootCurrent ${current:-none})"
stop_guest

# --- And as vm-run boots it: fresh UEFI variables, the fallback loader ------------------
fallback_boot() {  # WHAT LOG DISK...
    local what=$1 log=$2; shift 2
    local d
    mapfile -t d < <(disk_args "$@")
    cp "$OVMF_VARS" "$work/fresh-vars.fd"
    qemu-system-x86_64 "${common[@]}" -machine q35 \
        -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" \
        -drive "if=pflash,format=raw,unit=1,file=$work/fresh-vars.fd" \
        "${d[@]}" -snapshot "${net[@]}" -display none -serial "file:$log" -monitor none &
    qemu_pid=$!
    wait_ssh 600 || die "$what never answered on SSH; see $PWD/$log"
    { echo "=== $what (fresh UEFI variables)"
      "${ssh_root[@]}" 'set -x; systemctl is-system-running --wait; cat /proc/cmdline; efibootmgr; zpool status -x rpool
          proxmox-boot-tool status; systemctl --failed --no-legend' </dev/null 2>&1; } >>first-boot.txt || true
    sed -n "/^=== $what/,\$p" first-boot.txt | grep -Eq '^ *(running|degraded)$' ||
        { cat first-boot.txt >&2; die "$what isn't up"; }
    stop_guest
}
fallback_boot "fallback boot" fallback-boot-serial.log "${disks[@]}"
if [[ $NDISKS == 2 ]]; then
    fallback_boot "fallback boot from $DISK alone" fallback-boot-a-serial.log "$DISK"
    fallback_boot "fallback boot from $DISK_B alone" fallback-boot-b-serial.log "$DISK_B"
fi

for d in "${disks[@]}"; do qemu-img snapshot -c fresh-install "$d"; done
if [[ $NDISKS == 2 ]]; then echo "--uefi --no-secure-boot --disk $DISK_B"; else echo "--uefi --no-secure-boot"; fi >vm-run-options
echo "Done: ${disks[*]} in $2 ($(du -ch "${disks[@]}" | tail -n 1 | cut -f1) on disk), snapshot \"fresh-install\""
