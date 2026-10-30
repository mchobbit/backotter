#!/usr/bin/env bash
# Build a Debian (or Devuan) test guest for BackOtter: an unattended install
# from an official ISO into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-debian.sh [--devuan [--init sysvinit|openrc|runit]] VERSION ROOT_FS WORK_DIR
#   e.g. ./make-debian.sh 12.0.0 ext4 vms/debian12
#        ./make-debian.sh 13.0.0 btrfs vms/debian13
#        ./make-debian.sh --devuan --init openrc 6.1.1 ext3 vms/devuan6
#
# Debian: VERSION is a point release in https://cdimage.debian.org/cdimage/archive/,
# installed from its DVD-1.  Devuan (Debian without systemd, the same
# installer): VERSION is a release in https://files.devuan.org/, installed
# from its "server" ISO, with the init system --init names (default sysvinit).
# ROOT_FS is ext3, ext4, btrfs, xfs or jfs.  The ISO and its signed checksum
# list are downloaded into WORK_DIR unless already there, and checked against
# the project's signature before use.  Results, in WORK_DIR (N is the major
# version, e.g. 12):
#   debianN.qcow2 / devuanN.qcow2
#                         the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#
# The guest, host name debN or devuanN: BIOS + MBR, /boot (1 GB ext4),
# swap (2 GB), / (ROOT_FS, the rest), 2 GB RAM, no desktop; see
# debian/preseed.cfg.  Run it with vm-run, and look inside it while it's off
# with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, cpio, gpg, gpgv,
# openssl, wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
# "Debian CD signing key <debian-cd@lists.debian.org>", per
# https://www.debian.org/CD/verify
DEBIAN_CD_KEY=DF9B9C49EAA9298432589D76DA87E80D6294BE9B
# Devuan's ISO checksums are signed by a release developer; the keys are
# listed at https://www.devuan.org/os/keyring.
declare -A DEVUAN_KEYS=(
    [6]=185E56E98DA03B6CEADAC81983161D4768BE620B   # tempforever, signs Excalibur's ISOs
)
declare -A DEVUAN_CODENAMES=([5]=daedalus [6]=excalibur)
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-debian: $*" >&2; exit 1; }

distro=debian init=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --devuan) distro=devuan; shift ;;
        --init) init=$2; shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 3 ]] || die "usage: $0 [--devuan [--init sysvinit|openrc|runit]] VERSION ROOT_FS WORK_DIR"
version=$1 root_fs=$2
[[ $version =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || die "VERSION looks like 13.0.0 or 6.1.1, not '$version'"
case $root_fs in ext3|ext4|btrfs|xfs|jfs) ;; *) die "ROOT_FS must be ext3, ext4, btrfs, xfs or jfs" ;; esac
[[ -z $init || $distro == devuan ]] || die "--init needs --devuan (Debian installs systemd)"
case ${init:-sysvinit} in sysvinit|openrc|runit) ;; *) die "--init must be sysvinit, openrc or runit" ;; esac
major=${version%%.*}

if [[ $distro == debian ]]; then
    ISO=debian-$version-amd64-DVD-1.iso
    SUMS=SHA256SUMS SIG=SHA256SUMS.sign
    url=https://cdimage.debian.org/cdimage/archive/$version/amd64/iso-dvd
    key_fpr=$DEBIAN_CD_KEY keyserver=hkps://keyring.debian.org
    kernel=/install.amd/vmlinuz initrd=/install.amd/initrd.gz
    guest_name=deb$major
else
    codename=${DEVUAN_CODENAMES[$major]:-}
    key_fpr=${DEVUAN_KEYS[$major]:-}
    [[ -n $codename && -n $key_fpr ]] || die "Devuan $major unknown; add it to DEVUAN_CODENAMES and DEVUAN_KEYS"
    ISO=devuan_${codename}_${version}_amd64_server.iso
    SUMS=SHA256SUMS.txt SIG=SHA256SUMS.txt.asc
    url=https://files.devuan.org/devuan_$codename/installer-iso
    keyserver=hkps://keyserver.ubuntu.com
    kernel=/boot/isolinux/linux initrd=/boot/isolinux/initrd.gz
    guest_name=devuan$major
fi
DISK=$distro$major.qcow2
mkdir -p "$3"
cd "$3"
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
for f in "$SUMS" "$SIG" "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
keyring=$distro-signing-key.gpg
if [[ ! -s $keyring ]]; then
    # Fetched by full fingerprint, so the keyserver can't substitute a key.
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver "$keyserver" --recv-keys "$key_fpr"
    GNUPGHOME=$gnupg gpg -q --export "$key_fpr" >"$keyring"
    rm -rf "$gnupg"
fi
gpgv --keyring "./$keyring" "$SIG" "$SUMS" 2>/dev/null || die "$SUMS is not signed by $key_fpr"
grep -E "^[0-9a-f]{64} [ *]?$ISO\$" "$SUMS" | sha256sum -c --quiet - || die "$ISO does not match $SUMS"

# --- Installer kernel and initrd, with the preseed added ------------------------
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
xorriso -osirrox on -indev "$ISO" -extract "$kernel" "$work/vmlinuz" \
    -extract "$initrd" "$work/initrd.gz" 2>/dev/null || die "$ISO lacks $kernel or $initrd"
chmod u+w "$work"/*

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

mkdir "$work/add"
sed -e "s|@ROOT_PASSWORD_HASH@|$hash|" -e "s|@HOSTNAME@|$guest_name|g" -e "s|@ROOT_FS@|$root_fs|g" \
    "$here/debian/preseed.cfg" >"$work/add/preseed.cfg"
if [[ $distro == devuan ]]; then
    # Devuan's installer asks which init system to install.  Its server ISO
    # lacks openssh-server, so it also needs Devuan's mirror; later answers
    # override the template's "no mirror".  (Devuan keeps no dated snapshots,
    # so the guest gets the packages current on the day it's built.)
    cat >>"$work/add/preseed.cfg" <<EOF
d-i choose-init/select_init select ${init:-sysvinit}
d-i apt-setup/use_mirror boolean true
d-i mirror/country string manual
d-i mirror/http/hostname string deb.devuan.org
d-i mirror/http/directory string /merged
d-i mirror/http/proxy string
d-i pkgsel/include string openssh-server
EOF
fi
if grep -n '@[A-Z_]\+@' "$work/add/preseed.cfg"; then die "preseed placeholders left unfilled"; fi
cp id_ed25519.pub "$work/add/authorized_keys"
# A second gzipped cpio archive appended to the initrd is unpacked on top of
# the first; the installer reads /preseed.cfg from it automatically.
(cd "$work/add" && printf '%s\n' preseed.cfg authorized_keys |
    cpio -o -H newc --quiet | gzip -9) >>"$work/initrd.gz"

# --- Install ----------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $3/install-serial.log"
# DEBIAN_FRONTEND=text: a plain-text installer, readable in the serial log.
# Arguments after "---" are kept on the installed system's kernel command
# line, so it has a serial console too.
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -nic user,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initrd.gz" \
    -append "auto=true priority=critical DEBIAN_FRONTEND=text console=ttyS0,115200n8 --- console=tty0 console=ttyS0,115200n8" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
# The installer only powers off at the very end (a failed step waits for an
# answer instead, until the timeout), but check that a system was written.
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 500000000 ]] ||
    die "the disk is nearly empty; see install-serial.log"

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $3/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
