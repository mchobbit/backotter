#!/usr/bin/env bash
# Build an openSUSE Tumbleweed test guest for BackOtter: an unattended
# AutoYaST install from the official DVD ISO into a qcow2 disk image, under
# QEMU with KVM.
#
# Usage: ./make-opensuse.sh SNAPSHOT WORK_DIR
#   e.g. ./make-opensuse.sh 20261030 vms/tumbleweed
#
# SNAPSHOT is a Tumbleweed snapshot (YYYYMMDD).  openSUSE serves only the
# current snapshot's ISO, as openSUSE-Tumbleweed-DVD-x86_64-Current.iso: it is
# downloaded into WORK_DIR (with its .sha256 and .sha256.asc) unless already
# there, and must be SNAPSHOT's and signed by the openSUSE Project Signing
# Key.  Keep it there: the DVD holds every package the install takes, so a
# rebuild from it needs nothing of openSUSE's that expires (the history of
# old snapshots is kept for some months only), and installs the same
# snapshot.  The guest's repositories are the live ones, which vm-update
# updates it from.
#
# Results, in WORK_DIR:
#   tumbleweed.qcow2      the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#
# The guest, host name tumbleweed: BIOS + MBR, /boot (1 GiB ext4), swap
# (2 GiB), / (btrfs, the rest) in openSUSE's own layout, subvolumes and
# snapper included, 2 GB RAM, no desktop; see opensuse/autoinst.xml.  Run it
# with vm-run, and look inside it while it's off with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, gpg, gpgv, openssl,
# python3, wget, and network access.  Runs as an ordinary user in the kvm
# group.

set -euo pipefail

DISK_SIZE=16G
INSTALL_MEMORY=4096
GUEST_NAME=tumbleweed
DISK=$GUEST_NAME.qcow2
# "openSUSE Project Signing Key <opensuse@opensuse.org>"
OPENSUSE_KEY=AD485664E901B867051AB15F35A2F86E29B700A4
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-opensuse: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 SNAPSHOT WORK_DIR"
snapshot=$1
[[ $snapshot =~ ^[0-9]{8}$ ]] || die "SNAPSHOT looks like 20261030, not '$snapshot'"
ISO=openSUSE-Tumbleweed-DVD-x86_64-Current.iso
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
url=https://download.opensuse.org/tumbleweed/iso
for f in "$ISO.sha256" "$ISO.sha256.asc" "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
# The checksum file names the snapshot: ...-Snapshot20261030-Media.iso.
grep -q "Snapshot$snapshot-Media.iso\$" "$ISO.sha256" ||
    die "$ISO is not snapshot $snapshot (see $ISO.sha256); move it away to fetch the current one"
work=$(mktemp -d)
server=""
cleanup() { [[ -n $server ]] && kill "$server" 2>/dev/null; chmod -R u+w "$work"; rm -rf "$work"; }
trap cleanup EXIT
if [[ ! -s opensuse-key.gpg ]]; then
    # The ISO carries the key with its current expiry date, which the
    # keyservers may lack; only that key, by full fingerprint, is used.
    short=${OPENSUSE_KEY: -8}
    keyfile=$(xorriso -indev "$ISO" -find / -maxdepth 1 -name "gpg-pubkey-${short,,}-*.asc" 2>/dev/null |
        tr -d "'" | head -n 1)
    [[ -n $keyfile ]] || die "$ISO doesn't carry the openSUSE Project Signing Key"
    xorriso -osirrox on -indev "$ISO" -extract "$keyfile" "$work/key.asc" 2>/dev/null
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --import "$work/key.asc"
    GNUPGHOME=$gnupg gpg -q --export "$OPENSUSE_KEY" >opensuse-key.gpg
    rm -rf "$gnupg"
fi
gpgv --keyring ./opensuse-key.gpg "$ISO.sha256.asc" "$ISO.sha256" 2>/dev/null ||
    die "$ISO.sha256 is not signed by the openSUSE Project Signing Key"
[[ $(sha256sum "$ISO" | cut -d' ' -f1) == $(cut -d' ' -f1 "$ISO.sha256") ]] ||
    die "$ISO does not match $ISO.sha256"

# --- Installer kernel and initrd, and the AutoYaST profile --------------------------
xorriso -osirrox on -indev "$ISO" \
    -extract /boot/x86_64/loader/linux "$work/linux" \
    -extract /boot/x86_64/loader/initrd "$work/initrd" 2>/dev/null ||
    die "$ISO lacks /boot/x86_64/loader/linux or initrd"

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
umask 022

mkdir "$work/www"
sed -e "s|@HOSTNAME@|$GUEST_NAME|g" -e "s|@ROOT_PASSWORD_HASH@|$hash|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" \
    "$here/opensuse/autoinst.xml" >"$work/www/autoinst.xml"
if grep -n '@[A-Z_]\+@' "$work/www/autoinst.xml"; then die "autoinst.xml placeholders left unfilled"; fi
python3 -c 'import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])' "$work/www/autoinst.xml" ||
    die "opensuse/autoinst.xml is not well-formed XML"

port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# --- Install --------------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $2/install-serial.log"
# The packages from the DVD (the default install source), not today's
# repository; self_update=0 likewise keeps the installer as the ISO has it.
# YaST's "No network running" pop-up, which autoinst.xml's <networking>
# now avoids: answer-popups.py presses Enter on it should it come all the
# same (and says so on stderr).
python3 "$here/opensuse/answer-popups.py" install-serial.log "$work/serial" "No network running" &
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -nic user,ipv6=off,model=virtio-net-pci \
    -kernel "$work/linux" -initrd "$work/initrd" \
    -append "console=ttyS0,115200n8 textmode=1 self_update=0 autoyast=http://10.0.2.2:$port/autoinst.xml" \
    -chardev "socket,id=serial0,path=$work/serial,server=on,wait=off,logfile=install-serial.log" \
    -display none -serial chardev:serial0 -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
grep -aq BACKOTTER-INSTALL-OK install-serial.log || die "the install did not finish; see install-serial.log"

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
