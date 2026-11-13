#!/usr/bin/env bash
# Build the cachyos-zfs test guest for BackOtter: CachyOS on a ZFS root,
# booted by Limine, installed from CachyOS's own live ISO the way its Calamares
# does an online install with ZFS and Limine chosen (arch/install-cachyos-zfs.sh
# says how, and where the guest differs), into a qcow2 disk image, under QEMU
# with KVM and OVMF.
#
# Usage: ./make-cachyos-zfs.sh [--iso-dir DIR] RELEASE WORK_DIR
#   e.g. ./make-cachyos-zfs.sh --iso-dir /bkoscratch/rebuild-inputs/cachyos-zfs 260809 vms/cachyos-zfs
#
# RELEASE is a CachyOS desktop ISO release (YYMMDD, as on cachyos.org).
# cachyos-desktop-linux-RELEASE.iso with its .sig and .sha256 is looked for in
# DIR (default WORK_DIR) and downloaded there if missing, then checked against
# the signature of CachyOS's key.  The ISO is only the live system the install
# runs in: the packages are CachyOS's and Arch's current ones (CachyOS keeps
# no archive), so the guest is frozen only by its qcow2 snapshot.
#
# The live system is driven over SSH: its systemd provisions root's
# authorized_keys from an SMBIOS credential (ssh.authorized_keys.root), and the
# ISO starts sshd.  It boots to multi-user.target, so no desktop starts.
#
# Results, in WORK_DIR:
#   cachyos-zfs.qcow2     the guest's 24 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" taken after the first boot
#   uefi-vars.fd          the OVMF variables of the install, with Limine's
#                         NVRAM entry, for OVMF_CODE_4M.fd on a plain q35
#                         machine (as below; vm-run --uefi-vars can't read it:
#                         see uefi=); vm-run boots the fallback loader
#   root-password         the password of root and of "tester" (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install.log           the install script's output
#   install-serial.log, first-boot-serial.log   the serial consoles
#   first-boot.txt        what the first boot showed (zpool status, findmnt /,
#                         efibootmgr, /proc/cmdline)
#   vm-run-options        "--uefi --no-secure-boot", how vm-run must boot it
#
# The guest, host name cachyos-zfs: UEFI + GPT, a 4 GiB ESP as /boot holding
# the kernels, initramfs and Limine; the rest is the pool zpcachyos with
# zpcachyos/ROOT/cos/{root,home,varcache,varlog}; no swap partition (zram);
# linux-cachyos (default) and linux-cachyos-lts with their ZFS modules;
# mkinitcpio with the zfs hook; /etc/hostid and /etc/zfs/zpool.cache; no
# desktop, booting to text mode, sshd on, 2 GB RAM.  Needs an x86-64-v3 CPU.
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, gpg, gpgv,
# openssl, python3, wget, ssh, and network access.  Runs as an ordinary user
# in the kvm group.

set -euo pipefail

GUEST_NAME=cachyos-zfs
DISK=$GUEST_NAME.qcow2
DISK_SIZE=24G
MEMORY=2048
INSTALL_MEMORY=4096
# "CachyOS <admin@cachyos.org>", which signs the ISOs and the packages
CACHYOS_KEY=882DCFE48E2051D48E2562ABF3B607488DB35A47
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=5400

here=$(dirname "$(realpath "$0")")
die() { echo "make-cachyos-zfs: $*" >&2; exit 1; }

iso_dir=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --iso-dir) iso_dir=$(realpath "$2"); shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || die "usage: $0 [--iso-dir DIR] RELEASE WORK_DIR"
release=$1
[[ $release =~ ^[0-9]{6}$ ]] || die "RELEASE looks like 260809, not '$release'"
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"
[[ -n $iso_dir ]] || iso_dir=$PWD
ISO=cachyos-desktop-linux-$release.iso

# --- Fetch and verify the ISO -------------------------------------------------------
url=https://cdn77.cachyos.org/ISO/desktop/$release
for f in "$ISO.sha256" "$ISO.sig" "$ISO"; do
    [[ -f $iso_dir/$f && ! -f $iso_dir/$f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$iso_dir/$f.part"
    wget -c -q -O "$iso_dir/$f" "$url/$f" || die "could not download $url/$f"
    rm "$iso_dir/$f.part"
done
if [[ ! -s cachyos-key.gpg ]]; then
    # Fetched by full fingerprint, so the keyserver can't substitute a key.
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver hkps://keyserver.ubuntu.com --recv-keys "$CACHYOS_KEY"
    GNUPGHOME=$gnupg gpg -q --export "$CACHYOS_KEY" >cachyos-key.gpg
    gpgconf --homedir "$gnupg" --kill all   # its dirmngr, else left running
    rm -rf "$gnupg"
fi
gpgv --keyring ./cachyos-key.gpg "$iso_dir/$ISO.sig" "$iso_dir/$ISO" 2>/dev/null ||
    die "$ISO is not signed by CachyOS's key"
(cd "$iso_dir" && sha256sum -c --quiet "$ISO.sha256") || die "$ISO does not match $ISO.sha256"

work=$(mktemp -d)
qemu_pid=""
cleanup() {
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
trap cleanup EXIT

# --- The live system's kernel, initramfs and boot options --------------------------
xorriso -osirrox on -indev "$iso_dir/$ISO" -extract /boot/grub/grub.cfg "$work/grub.cfg" 2>/dev/null ||
    die "$ISO lacks /boot/grub/grub.cfg"
# The first entry with CachyOS's main kernel (not -lts): its linux and initrd lines.
entry=$(awk '/^menuentry/ { on = 0; k = "" } /^ *linux .*vmlinuz-linux-cachyos / { on = 1 }
    on && /^ *(linux|initrd) / { print } on && /^}/ { exit }' "$work/grub.cfg")
kernel=$(awk '$1 == "linux" { print $2 }' <<<"$entry")
initrd=$(awk '$1 == "initrd" { for (i = 2; i <= NF; i++) if ($i ~ /initramfs/) print $i }' <<<"$entry")
options=$(awk '$1 == "linux" { $1 = $2 = ""; print }' <<<"$entry" |
    sed -E 's/\b(quiet|splash)\b//g; s/\bcopytoram=[^ ]*//')
[[ -n $kernel && -n $initrd && $options == *archisobasedir* ]] || die "unexpected boot entry in grub.cfg"
xorriso -osirrox on -indev "$iso_dir/$ISO" -extract "$kernel" "$work/vmlinuz" \
    -extract "$initrd" "$work/initramfs.img" 2>/dev/null
chmod u+w "$work"/vmlinuz "$work"/initramfs.img

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
umask 022

sed -e "s|@HOSTNAME@|$GUEST_NAME|" -e "s|@ROOT_PASSWORD_HASH@|$hash|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" \
    "$here/arch/install-cachyos-zfs.sh" >"$work/install.sh"
if grep -n '@[A-Z_]\+@' "$work/install.sh"; then die "install.sh placeholders left unfilled"; fi

port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
ssh_root=(ssh -q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 root@127.0.0.1)
wait_ssh() {  # SECONDS
    local _
    for _ in $(seq $(($1 / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || return 1
        "${ssh_root[@]}" true </dev/null 2>/dev/null && return 0
        sleep 5
    done
    return 1
}
stop_guest() {
    "${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || true
    local _
    for _ in $(seq 60); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
    kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
    wait "$qemu_pid" 2>/dev/null || true
    qemu_pid=""
}
# Plain q35, not vm-run's machine: vm-run marks the flash secure (SMM only)
# even for OVMF_CODE_4M.fd, which has no SMM, so under --no-secure-boot the
# firmware can neither read nor write the variable store and keeps its
# variables in RAM: Limine's entry would never reach uefi-vars.fd.
uefi=(-machine q35 -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
    -drive "if=pflash,format=raw,unit=1,file=uefi-vars.fd")

# --- Install --------------------------------------------------------------------------
cp "$OVMF_VARS" uefi-vars.fd
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the install script's output goes to $2/install.log"
qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" "${uefi[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" \
    -drive "file=$iso_dir/$ISO,media=cdrom,readonly=on" \
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22" \
    -smbios "type=11,value=io.systemd.credential.binary:ssh.authorized_keys.root=$(base64 -w0 id_ed25519.pub)" \
    -kernel "$work/vmlinuz" -initrd "$work/initramfs.img" \
    -append "$options copytoram=n systemd.unit=multi-user.target console=tty0 console=ttyS0,115200n8" \
    -display none -serial file:install-serial.log -monitor none -no-reboot &
qemu_pid=$!
wait_ssh 600 || die "the live system never answered on SSH; see install-serial.log"
# Copied in first, so that nothing it runs can read the rest of it from stdin.
"${ssh_root[@]}" 'cat >/root/bko-install.sh' <"$work/install.sh" || die "could not copy the install script in"
timeout "$INSTALL_TIMEOUT" "${ssh_root[@]}" bash /root/bko-install.sh </dev/null >install.log 2>&1 || true
grep -q BACKOTTER-INSTALL-OK install.log || {
    tail -n 30 install.log >&2
    die "the install did not finish; see install.log"
}
stop_guest

# --- First boot, from the disk, through Limine's NVRAM entry -----------------------
qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${uefi[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" \
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22" \
    -display none -serial file:first-boot-serial.log -monitor none &
qemu_pid=$!
wait_ssh 300 || die "the installed system never answered on SSH; see first-boot-serial.log"
"${ssh_root[@]}" 'set -x; systemctl is-system-running --wait; cat /proc/cmdline; findmnt /
    zpool status zpcachyos; zfs list -o name,mountpoint,canmount,mounted; zpool get -H cachefile,compatibility zpcachyos
    efibootmgr -v; uname -r; cat /etc/hostid | od -An -tx1; systemctl --failed --no-legend' \
    </dev/null >first-boot.txt 2>&1 || true
grep -Eq '^ *(running|degraded)$' first-boot.txt || { cat first-boot.txt >&2; die "the first boot isn't up"; }
grep -q 'root=ZFS=zpcachyos/ROOT/cos/root' first-boot.txt || die "/proc/cmdline has no ZFS root"
grep -Eq '^/ +zpcachyos/ROOT/cos/root +zfs' first-boot.txt || die "/ isn't zpcachyos/ROOT/cos/root"
current=$(awk '$1 == "BootCurrent:" { print $2 }' first-boot.txt)
grep -E "^Boot$current\\*? " first-boot.txt | grep -qi limine ||
    die "the first boot didn't come through Limine's NVRAM entry (BootCurrent ${current:-none})"
stop_guest

qemu-img snapshot -c fresh-install "$DISK"
echo "--uefi --no-secure-boot" >vm-run-options
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
