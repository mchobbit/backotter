#!/usr/bin/env bash
# Build an OmniOS (illumos) test guest for BackOtter from OmniOS's official
# cloud image, under QEMU with KVM.  The image is a finished install with
# cloud-init; its first boot here sets the host name, root's password and
# SSH key, then powers off.
#
# Usage: ./make-omnios.sh RELEASE WORK_DIR
#   e.g. ./make-omnios.sh r151058 vms/omnios58
#
# RELEASE is a release in https://downloads.omnios.org/media/.  Its
# omnios-RELEASE.cloud.raw.zst and .sha256 are downloaded into WORK_DIR unless
# already there.  OmniOS signs neither, so the check against the .sha256 only
# catches a damaged download; both come over HTTPS from the same server.
# Results, in WORK_DIR (N is the release's last two digits, e.g. 58):
#   omniosN.qcow2         the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the configured image
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the serial console of the first boot
#   vm-run-options        "--legacy-virtio-net", how vm-run must boot it
#
# The guest, host name omniosN: GPT with the image's own layout, ZFS pool
# "rpool" grown to fill the disk, swap on a 2 GiB volume in it (rpool/swap,
# illumos' usual place for swap), console on the serial port, 2 GB RAM,
# cloud-init turned off after its first run; see omnios/user-data.  Run it
# with vm-run and vm-run-options, which give it a legacy virtio NIC (illumos'
# vioif can't map the modern one's registers, and the guest then has no
# network).
# vm-mount can't open it: Linux's OpenZFS may refuse an illumos pool, and
# importing it would mark it as this host's.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, zstd, openssl,
# python3 with yaml, wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
BOOT_TIMEOUT=900

here=$(dirname "$(realpath "$0")")
die() { echo "make-omnios: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 RELEASE WORK_DIR"
release=$1
[[ $release =~ ^r1510[0-9]{2}$ ]] || die "RELEASE looks like r151058, not '$release'"
IMAGE=omnios-$release.cloud.raw.zst
url=https://downloads.omnios.org/media/$release
guest_name=omnios${release: -2}
DISK=$guest_name.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# --- Fetch and check the image ------------------------------------------------------
for f in "$IMAGE.sha256" "$IMAGE"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
want=$(grep -oE '^[0-9a-f]{64}' "$IMAGE.sha256") || die "$IMAGE.sha256 holds no SHA-256"
echo "$want  $IMAGE" | sha256sum -c --quiet - || die "$IMAGE does not match $IMAGE.sha256"

# --- The disk, and the first-boot settings ------------------------------------------
work=$(mktemp -d)
raw=$(mktemp -p . --suffix=.raw image.XXXXXX)
trap 'rm -rf "$work" "$raw"' EXIT
zstd -q -d -f "$IMAGE" -o "$raw"
qemu-img convert -f raw -O qcow2 "$raw" "$DISK"
rm -f "$raw"
qemu-img resize -q "$DISK" "$DISK_SIZE"

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

mkdir "$work/cidata"
sed -e "s|@HOSTNAME@|$guest_name|" -e "s|@PASSWORD_HASH@|$hash|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" "$here/omnios/user-data" >"$work/cidata/user-data"
if grep -n '@[A-Z_]\+@' "$work/cidata/user-data"; then die "user-data placeholders left unfilled"; fi
# cloud-init skips what it can't use and carries on, and the first boot then
# never powers off; catch that here.  (A runcmd line with ": " in it, unquoted,
# is a mapping to YAML, not a command.)
python3 -c '
import sys, yaml
config = yaml.safe_load(open(sys.argv[1]))
assert all(isinstance(c, str) for c in config["runcmd"]), "a runcmd entry is not a string"
' "$work/cidata/user-data" || die "omnios/user-data is not valid (needs python3-yaml to check)"
printf 'instance-id: %s\nlocal-hostname: %s\n' "$guest_name-$(date -u +%Y%m%d%H%M%S)" "$guest_name" \
    >"$work/cidata/meta-data"
xorriso -as mkisofs -quiet -o "$work/cidata.iso" -V CIDATA -J -r "$work/cidata" 2>"$work/xorriso.log" ||
    { cat "$work/xorriso.log" >&2; die "could not make the CIDATA ISO"; }

# --- First boot ---------------------------------------------------------------------
echo "First boot; its console goes to $2/install-serial.log"
# cloud-init powers the guest off when it's done.
timeout "$BOOT_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$work/cidata.iso,media=cdrom,readonly=on" \
    -nic user,model=virtio-net-pci -global virtio-net-pci.disable-modern=on \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the first boot failed or timed out; see install-serial.log"
grep -q 'BACKOTTER-FIRSTBOOT: OK' install-serial.log ||
    die "cloud-init didn't finish; see install-serial.log"

echo "--legacy-virtio-net" >vm-run-options
qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
