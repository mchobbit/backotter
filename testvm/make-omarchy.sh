#!/usr/bin/env bash
# Build an Omarchy test guest for BackOtter: Omarchy's own unattended install
# from its official ISO, given a "cidata" disc in place of its setup wizard,
# into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-omarchy.sh VERSION WORK_DIR
#   e.g. ./make-omarchy.sh 4.0.6 vms/omarchy
#
# VERSION is an Omarchy release.  omarchy-VERSION.iso with its .sha256 and
# .sig is downloaded into WORK_DIR unless already there, and checked against
# the signature of Omarchy's package key.
#
# Results, in WORK_DIR:
#   omarchy.qcow2         the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" taken after the first boot
#   root-password         the password of root and of the user "tester" (mode 600)
#   luks-passphrase       the passphrase of / (mode 600), asked for at every boot
#   id_ed25519, .pub      SSH key that logs in as root and as tester
#   install-serial.log    the installer's serial console (the installer itself
#                         draws on the screen, and logs in the live system)
#   vm-run-options        "--uefi --no-secure-boot", how vm-run must boot it
#
# The guest, host name omarchy: Omarchy's own layout, which its installer
# fixes: UEFI + GPT, a 2 GiB EFI system partition as /boot (Limine), and the
# rest LUKS2-encrypted btrfs with subvolumes @, @home, @log and @pkg; swap is
# zram, so there is no swap partition.  Desktop installed but booting to text
# mode (multi-user.target), sshd on, 2 GB RAM.  At every boot the passphrase
# must be typed on its screen: vm-run shows the screen only with a display,
# so for a headless run use type-luks-passphrase.py (as this script does).
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, gpg, gpgv,
# openssl, python3, wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
INSTALL_MEMORY=4096
GUEST_NAME=omarchy
DISK=$GUEST_NAME.qcow2
# "Omarchy <pkgs@omarchy.org>", which signs the ISOs
OMARCHY_KEY=40DFB630FF42BCFFB047046CF0134EE680CAC571
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-omarchy: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 VERSION WORK_DIR"
version=$1
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "VERSION looks like 4.0.6, not '$version'"
ISO=omarchy-$version.iso
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
url=https://iso.omarchy.org
for f in "$ISO.sha256" "$ISO.sig" "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
if [[ ! -s omarchy-key.gpg ]]; then
    # Fetched by full fingerprint, so the keyserver can't substitute a key.
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver hkps://keyserver.ubuntu.com --recv-keys "$OMARCHY_KEY"
    GNUPGHOME=$gnupg gpg -q --export "$OMARCHY_KEY" >omarchy-key.gpg
    rm -rf "$gnupg"
fi
gpgv --keyring ./omarchy-key.gpg "$ISO.sig" "$ISO" 2>/dev/null || die "$ISO is not signed by Omarchy's key"
sha256sum -c --quiet "$ISO.sha256" || die "$ISO does not match $ISO.sha256"

work=$(mktemp -d)
server=""
cleanup() { [[ -n $server ]] && kill "$server" 2>/dev/null; chmod -R u+w "$work"; rm -rf "$work"; }
trap cleanup EXIT

# --- The live system's kernel and boot options -------------------------------------
xorriso -osirrox on -indev "$ISO" -extract /boot/grub/grub.cfg "$work/grub.cfg" 2>/dev/null ||
    die "$ISO lacks /boot/grub/grub.cfg"
# The default entry, "archlinux": its linux and initrd lines.
entry=$(awk '/--id .archlinux./ { on = 1 } on && /^ *(linux|initrd) / { print } on && /^}/ { exit }' "$work/grub.cfg")
kernel=$(awk '$1 == "linux" { print $2 }' <<<"$entry")
initrd=$(awk '$1 == "initrd" { print $2 }' <<<"$entry")
options=$(awk '$1 == "linux" { $1 = $2 = ""; print }' <<<"$entry" | sed 's/\bquiet\b//; s/\bsplash\b//')
[[ -n $kernel && -n $initrd && $options == *archisobasedir* ]] || die "unexpected boot entry in grub.cfg"
xorriso -osirrox on -indev "$ISO" -extract "$kernel" "$work/vmlinuz" \
    -extract "$initrd" "$work/initramfs.img" 2>/dev/null
chmod u+w "$work"/vmlinuz "$work"/initramfs.img

umask 077
password=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9')
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
# Letters and digits only: typed on the guest's keyboard at every boot.
passphrase=$(openssl rand -base64 24 | tr -dc 'a-z0-9' | head -c 20)
printf '%s\n' "$passphrase" >luks-passphrase
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
umask 022

# --- The cidata disc: the wizard's answers, as its configurator writes them -------
mkdir "$work/cidata"
python3 "$here/omarchy/cidata.py" "$work/cidata" "$GUEST_NAME" /dev/vda "$hash" "$passphrase" \
    "$(cat id_ed25519.pub)" || die "could not write the cidata files"
xorriso -as mkisofs -quiet -o "$work/cidata.iso" -V cidata -J -r "$work/cidata" 2>"$work/xorriso.log" ||
    { cat "$work/xorriso.log" >&2; die "could not make the cidata ISO"; }

# --- Install --------------------------------------------------------------------------
cp "$OVMF_VARS" "$work/vars.fd"
uefi=(-machine q35 -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
    -drive "if=pflash,format=raw,unit=1,file=$work/vars.fd")
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; this takes a while, and shows on the guest's screen only"
# The installer reboots into the installed system when done, which -no-reboot
# turns into QEMU exiting.
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" "${uefi[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -drive "file=$work/cidata.iso,media=cdrom,readonly=on" \
    -nic user,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initramfs.img" \
    -append "$options console=ttyS0,115200n8 console=tty0" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 2000000000 ]] ||
    die "the disk is nearly empty: the install failed (its log is in the live system only)"

# --- First boot: unlock, then root's SSH key and text mode -------------------------
echo "--uefi --no-secure-boot" >vm-run-options
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${uefi[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22" \
    -display none -serial file:first-boot-serial.log -monitor "unix:$work/monitor,server,nowait" &
server=$!
python3 "$here/omarchy/type-luks-passphrase.py" "$work/monitor" luks-passphrase ||
    die "could not type the passphrase"
ssh_t=(ssh -q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 tester@127.0.0.1)
for _ in $(seq 60); do "${ssh_t[@]}" true 2>/dev/null && break; sleep 5; done
"${ssh_t[@]}" "printf '%s\n' '$password' | sudo -S -p '' sh -c '
    install -d -m 700 /root/.ssh
    cp /home/tester/.ssh/authorized_keys /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    systemctl set-default multi-user.target
    sync; systemctl poweroff'" || die "could not finish the guest over SSH"
wait "$server" || true
server=""

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
