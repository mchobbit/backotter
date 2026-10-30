#!/usr/bin/env bash
# Build an OpenBSD test guest for BackOtter: an unattended autoinstall(8)
# from an official ISO into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-openbsd.sh VERSION WORK_DIR
#   e.g. ./make-openbsd.sh 8.0 vms/openbsd8
#
# VERSION is a release in https://cdn.openbsd.org/pub/OpenBSD/, installed
# from its installNN.iso, which carries the install sets.  The ISO and its
# signed checksum list (SHA256.sig) are downloaded into WORK_DIR unless
# already there, and checked with signify against the release's base key
# before use.  Results, in WORK_DIR (N is the major version, e.g. 8):
#   openbsdN.qcow2        the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#
# The guest, host name obsdN: BIOS + MBR, OpenBSD's automatic FFS layout over
# the whole disk, every set but X and games, console on the serial port,
# 2 GB RAM; see openbsd/install.conf.  Run it with vm-run.  vm-mount can't
# open it: Linux can't mount OpenBSD's disklabel partitions read-write.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, signify (Debian
# package "signify-openbsd"), openssl, python3, wget.  Runs as an ordinary
# user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
# Each release's base key, which signs its SHA256.sig, from
# https://www.openbsd.org/NN.html and openbsd/src's etc/signify (they agree).
declare -A BASE_KEYS=(
    [8.0]=RWSMSt0erHTctUxq2JDAgQRo64ztikp8JvQeiSPte3NZ0Geou0TkTEiE
)
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-openbsd: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 VERSION WORK_DIR"
version=$1
[[ $version =~ ^[0-9]+\.[0-9]$ ]] || die "VERSION looks like 8.0, not '$version'"
key=${BASE_KEYS[$version]:-}
[[ -n $key ]] || die "OpenBSD $version unknown; add its base key to BASE_KEYS"
major=${version%%.*}
signify=$(command -v signify-openbsd || command -v signify) || die "signify is missing (Debian: signify-openbsd)"

ISO=install${version/./}.iso
url=https://cdn.openbsd.org/pub/OpenBSD/$version/amd64
guest_name=obsd$major
DISK=openbsd$major.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
for f in SHA256.sig "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
server=""
work=$(mktemp -d)
media=$(mktemp -p . --suffix=.iso install-media.XXXXXX)
cleanup() { [[ -n $server ]] && kill "$server" 2>/dev/null; rm -rf "$work" "$media"; }
trap cleanup EXIT
printf 'untrusted comment: openbsd %s base public key\n%s\n' "$version" "$key" >"$work/base.pub"
"$signify" -C -q -p "$work/base.pub" -x SHA256.sig "$ISO" || die "$ISO does not match SHA256.sig, or SHA256.sig is not signed by $key"

# --- Install media on the serial console, and the response file ------------------
# The boot loader reads /etc/boot.conf from the CD; the stock one only names
# the install kernel.
xorriso -osirrox on -indev "$ISO" -extract /etc/boot.conf "$work/boot.conf" 2>/dev/null ||
    die "$ISO lacks /etc/boot.conf"
{ printf '%s\n' 'stty com0 115200' 'set tty com0'; cat "$work/boot.conf"; } >"$work/boot.conf.new"
rm -f "$media"
# The boot loader reads the plain ISO 9660 names, not the Rock Ridge ones, and
# looks for /8.0/amd64/bsd.rd and /etc/boot.conf as the stock ISO spells them;
# by default xorriso would rewrite those as 8_0, BOOT.CON;1 and so on.
xorriso -indev "$ISO" -outdev "$media" -boot_image any replay \
    -compliance long_names:omit_version:no_force_dots:allow_dir_id_ext \
    -map "$work/boot.conf.new" /etc/boot.conf \
    -chown 0 /etc/boot.conf -- -chgrp 0 /etc/boot.conf -- -chmod 0644 /etc/boot.conf -- \
    2>"$work/xorriso.log" || { cat "$work/xorriso.log" >&2; die "could not write the install media"; }

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

# Serve install.conf and the disklabel template to the guest only: QEMU's
# user-mode network maps 10.0.2.2 in the guest to the host's 127.0.0.1.
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
www=http://10.0.2.2:$port
mkdir "$work/www"
cp "$here/openbsd/disklabel" "$work/www/disklabel"
# The installer takes the password as given or as a bcrypt hash, which
# nothing here can make; the response file only ever crosses QEMU's user
# network, inside this host.
sed -e "s|@HOSTNAME@|$guest_name|" -e "s|@ROOT_PASSWORD@|$password|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" -e "s|@DISKLABEL_URL@|$www/disklabel|" \
    "$here/openbsd/install.conf" >"$work/www/install.conf"
if grep -n '@[A-Z_]\+@' "$work/www/install.conf"; then die "install.conf placeholders left unfilled"; fi
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# --- Install ----------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $2/install-serial.log"
# The installer asks at the console whether to autoinstall, and where the
# response file is; drive-install.py answers both, then waits for the
# reboot at the end, which -no-reboot turns into QEMU exiting.
python3 "$here/openbsd/drive-install.py" install-serial.log "$www/install.conf" \
    "$INSTALL_TIMEOUT" -- qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$media,media=cdrom,readonly=on" -boot order=d \
    -nic user,model=virtio-net-pci \
    -display none -serial stdio -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
