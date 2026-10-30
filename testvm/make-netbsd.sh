#!/usr/bin/env bash
# Build a NetBSD or AlpineBSD test guest for BackOtter: an unattended install
# from NetBSD's official install ISO into a qcow2 disk image, under QEMU with
# KVM.  The install is a script of ours (netbsd/install.sh), put on the ISO in
# place of sysinst, which has no unattended mode.
#
# Usage: ./make-netbsd.sh [--alpinebsd] VERSION WORK_DIR
#   e.g. ./make-netbsd.sh 11.0 vms/netbsd11
#        ./make-netbsd.sh --alpinebsd 11.0 vms/alpinebsd
#
# VERSION is a NetBSD release.  NetBSD-VERSION-amd64.iso is downloaded into
# WORK_DIR unless already there, and checked against the release's hash list
# signed by the NetBSD Security Officer.  --alpinebsd installs AlpineBSD
# (https://wiki.alpinebsd.org) instead, from the same installer: its packages
# are built for NetBSD VERSION's kernel and come from AlpineBSD's mirror,
# signed by its repository key.  AlpineBSD is new and rolling, with no
# releases, checksums or signatures for apk.static or the key: both are
# fetched over HTTPS (trust on first use) and their SHA-256 recorded in
# WORK_DIR/alpinebsd-fetched.sha256.
#
# Results, in WORK_DIR (NAME is netbsdN, N the major version, or alpinebsd):
#   NAME.qcow2            the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#
# The guest: BIOS, console on the serial port, 2 GB RAM, DHCP and SSH.
# NetBSD: MBR and a disklabel with / (FFSv2 with a WAPBL log) and swap, as
# NetBSD lays a disk out (no /boot of its own).  AlpineBSD: GPT with an EFI
# system partition, swap and / (FFSv2 with extended attributes), booting by
# BIOS (vm-run's default) or UEFI.  Run it with vm-run.  vm-mount can't open it: Linux's ufs driver reads FFSv2 at most,
# and not through a disklabel or NetBSD's wedges.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, gpg, gpgv, openssl,
# python3, wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
# "NetBSD Security Officer 2019 Key <security-officer@NetBSD.org>", which
# signs each release's hash list in https://cdn.netbsd.org/pub/NetBSD/security/hashes/
NETBSD_KEY=271133262BF35CB50D4F504689261E17F5EF49FF
ABSD_MIRROR=https://mirror.alpinebsd.org
ABSD_KEY_NAME=drew@ddevault.org-6a69e517.rsa.pub
INSTALL_TIMEOUT=2400

here=$(dirname "$(realpath "$0")")
die() { echo "make-netbsd: $*" >&2; exit 1; }

flavor=netbsd
[[ ${1:-} == --alpinebsd ]] && { flavor=alpinebsd; shift; }
[[ $# -eq 2 ]] || die "usage: $0 [--alpinebsd] VERSION WORK_DIR"
version=$1
[[ $version =~ ^[0-9]+\.[0-9]+$ ]] || die "VERSION looks like 11.0, not '$version'"
guest_name=netbsd${version%%.*}
[[ $flavor == alpinebsd ]] && guest_name=alpinebsd
DISK=$guest_name.qcow2
ISO=NetBSD-$version-amd64.iso
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# fetch URL [FILE]: download into WORK_DIR, unless it's already there.
fetch() {
    local f=${2:-${1##*/}}
    [[ -f $f && ! -f $f.part ]] && return
    echo "Downloading $1"
    touch "$f.part"
    wget -c -q -O "$f" "$1" || die "could not download $1"
    rm "$f.part"
}

# --- Fetch and verify the ISO -------------------------------------------------------
fetch "https://cdn.netbsd.org/pub/NetBSD/NetBSD-$version/images/$ISO"
fetch "https://cdn.netbsd.org/pub/NetBSD/security/hashes/NetBSD-${version}_hashes.asc"
if [[ ! -s netbsd-key.gpg ]]; then
    # Fetched by full fingerprint, so the keyserver can't substitute a key.
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver hkps://keyserver.ubuntu.com --recv-keys "$NETBSD_KEY"
    GNUPGHOME=$gnupg gpg -q --export "$NETBSD_KEY" >netbsd-key.gpg
    rm -rf "$gnupg"
fi
hashes=$(gpgv --keyring ./netbsd-key.gpg --output - "NetBSD-${version}_hashes.asc" 2>/dev/null) ||
    die "NetBSD-${version}_hashes.asc is not signed by the NetBSD Security Officer"
want=$(sed -n "s|^SHA512 (NetBSD-$version/images/$ISO) = ||p" <<<"$hashes")
[[ -n $want && $(sha512sum "$ISO" | cut -d' ' -f1) == "$want" ]] ||
    die "$ISO does not match the signed hash list"

work=$(mktemp -d)
server=""
media=$(mktemp -p . --suffix=.iso install-media.XXXXXX)
cleanup() { [[ -n $server ]] && kill "$server" 2>/dev/null; chmod -R u+w "$work"; rm -rf "$work" "$media"; }
trap cleanup EXIT
mkdir "$work/www"
if [[ $flavor == alpinebsd ]]; then
    fetch "$ABSD_MIRROR/apk.static" apk.static
    fetch "$ABSD_MIRROR/$ABSD_KEY_NAME" "$ABSD_KEY_NAME"
    sha256sum apk.static "$ABSD_KEY_NAME" >alpinebsd-fetched.sha256
    cp apk.static "$work/www/apk.static"
    cp "$ABSD_KEY_NAME" "$work/www/absd-key.rsa.pub"
fi

umask 077
password=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9')
printf '%s\n' "$password" >root-password
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

# --- Install media: the console on the serial port, our script for sysinst --------
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
# The installer only crosses QEMU's user network, inside this host, so the
# plain password in it goes nowhere else (NetBSD's hashes can't be made here).
sed -e "s|@FLAVOR@|$flavor|" -e "s|@HOSTNAME@|$guest_name|" -e "s|@ROOT_PASSWORD@|$password|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" -e "s|@SERVER@|http://10.0.2.2:$port|" \
    -e "s|@ABSD_MIRROR@|$ABSD_MIRROR|" -e "s|@ABSD_KEY_NAME@|$ABSD_KEY_NAME|" \
    "$here/netbsd/install.sh" >"$work/install.sh"
if grep -n '@[A-Z_]\+@' "$work/install.sh"; then die "install.sh placeholders left unfilled"; fi
xorriso -osirrox on -indev "$ISO" -extract /boot.cfg "$work/boot.cfg" 2>/dev/null ||
    die "$ISO lacks /boot.cfg"
{ echo "consdev=com0,115200"; sed 's/^timeout=.*/timeout=1/' "$work/boot.cfg"; } >"$work/boot.cfg.new"
rm -f "$media"
# The ISO's own El Torito images are hidden outside its file tree, so xorriso
# can't carry them over: boot the copy of the BIOS one in /usr/mdec instead
# (BIOS only, as the guest is).  Keep the plain ISO 9660 names, which
# NetBSD's CD boot code reads.
xorriso -indev "$ISO" -outdev "$media" \
    -boot_image any discard -boot_image any bin_path=/usr/mdec/bootxx_cd9660 \
    -boot_image any emul_type=no_emulation -boot_image any load_size=2048 \
    -compliance long_names:omit_version:no_force_dots:allow_dir_id_ext \
    -map "$work/boot.cfg.new" /boot.cfg -map "$work/install.sh" /install.sh \
    -chmod 0755 /install.sh -- \
    2>"$work/xorriso.log" || { cat "$work/xorriso.log" >&2; die "could not write the install media"; }

python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# --- Install --------------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $2/install-serial.log"
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$media,media=cdrom,readonly=on" -boot order=d \
    -nic user,model=virtio-net-pci \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
grep -aq BACKOTTER-INSTALL-OK install-serial.log || {
    grep -a BACKOTTER-INSTALL-FAILED install-serial.log >&2 || true
    die "the install did not finish; see install-serial.log"
}

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
