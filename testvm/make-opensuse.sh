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
#                         snapshot "fresh-install" of the finished install,
#                         taken after its first two boots (below)
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#   relabel-boot-serial.log, check-boot-serial.log
#                         the two boots' serial consoles
#   relabel-boot.txt, check-boot.txt
#                         what each boot showed (systemd's state, SELinux's
#                         mode, relabel triggers and units, failed units,
#                         files whose label isn't the policy's)
#
# SELinux: the install leaves the guest enforcing with a full relabel
# pending (/etc/selinux/.autorelabel, or /.autorelabel), which its first
# boot does: the initrd's pre-pivot hook (dracut's 98selinux-microos) runs
# setfiles over / and /var, and selinux-autorelabel-generator adds a
# restorecon -R unit per other fstab file system (/root, /home, /boot, ...)
# that local-fs.target requires, with no reboot.  A pending relabel would
# make every boot of a throwaway overlay (vm-run --snapshot, compare.py's
# boots) redo it, and fail it, to emergency mode, once the guest holds an
# immutable file (torture.sh's in /root/bko-torture): restorecon can't
# relabel one.  So the build boots the image for real once, which does the
# relabel in the image itself, and then once more, which must come up
# running, enforcing, with no relabel trigger left and no relabel unit:
# the build fails otherwise.
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
MEMORY=2048
# The relabel boot: the relabel takes a minute or two, the rest of the boot seconds.
BOOT_TIMEOUT=900

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
server="" qemu_pid=""
cleanup() {
    [[ -n $server ]] && kill "$server" 2>/dev/null
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
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
kill "$server" 2>/dev/null || true; server=""

# --- The SELinux relabel, then a clean boot -------------------------------------------
# Booted as vm-run boots it (BIOS, PC machine, virtio), writing to the image.
wdir=$2
sshport=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
ssh_root=(ssh -q -i id_ed25519 -p "$sshport" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 -o BatchMode=yes root@127.0.0.1)
boot_guest() {  # SERIAL_LOG
    qemu-system-x86_64 \
        -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
        -drive "file=$DISK,if=virtio,format=qcow2" \
        -nic "user,ipv6=off,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$sshport-:22" \
        -display none -serial "file:$1" -monitor none &
    qemu_pid=$!
    local _
    for _ in $(seq $((BOOT_TIMEOUT / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || die "QEMU exited before the guest answered on SSH; see $wdir/$1"
        "${ssh_root[@]}" true </dev/null 2>/dev/null && return 0
        sleep 5
    done
    die "the guest never answered on SSH (emergency mode?); see $wdir/$1"
}
stop_guest() {
    "${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || true
    local _
    for _ in $(seq 60); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
    kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
    wait "$qemu_pid" 2>/dev/null || true
    qemu_pid=""
}
# One KEY=VALUE line per finding.  mislabelled: files under the system's
# file systems whose label isn't the policy's (restorecon -n; the snapshots
# left out, / being one of them), the first few of them as "mislabel=".
# shellcheck disable=SC2016  # expanded in the guest
check_cmd='
echo "state=$(systemctl is-system-running --wait)"
echo "enforce=$(getenforce 2>&1)"
for f in /.autorelabel /etc/selinux/.autorelabel; do [ ! -e $f ] || echo "trigger=$f"; done
! grep -qw autorelabel /proc/cmdline || echo "trigger=autorelabel on /proc/cmdline"
echo "relabelled=$(stat -c %y /etc/selinux/.relabelled 2>/dev/null || echo none)"
systemctl list-units --all --no-legend --plain "*-relabel.service" mark-autorelabel-done.service | sed "s/^/relabel-unit=/"
systemctl --failed --no-legend --plain | sed "s/^/failed=/"
restorecon -R -n -v -e /.snapshots -e /proc -e /sys -e /dev -e /run / >/tmp/mislabelled 2>&1
echo "mislabelled=$(grep -c "Would relabel" /tmp/mislabelled)"
grep "Would relabel" /tmp/mislabelled | head -n 20 | sed "s/^/mislabel=/"; rm -f /tmp/mislabelled
'
check_boot() {  # NAME: boot, check, power off; the findings in NAME.txt
    echo "Booting the guest ($1); its console goes to $wdir/$1-serial.log"
    boot_guest "$1-serial.log"
    "${ssh_root[@]}" "$check_cmd" </dev/null >"$1.txt" 2>&1 || true
    stop_guest
    sed "s/^/  $1: /" "$1.txt"
    local bad=""
    grep -qx 'state=running' "$1.txt" || bad+=" not running;"
    grep -qx 'enforce=Enforcing' "$1.txt" || bad+=" not enforcing;"
    ! grep -q '^trigger=' "$1.txt" || bad+=" a relabel trigger is left;"
    ! grep -q '^failed=' "$1.txt" || bad+=" failed units;"
    [[ -z $bad ]] || die "$1:$bad see $wdir/$1.txt and $wdir/$1-serial.log"
}
# The first boot does the relabel the install left pending (and whatever
# else a first boot does: SSH host keys, ...).
check_boot relabel-boot
# The image as it ships: no relabel at all, so no relabel unit either.
check_boot check-boot
! grep -q '^relabel-unit=' check-boot.txt || die "the check boot relabelled again; see $2/check-boot.txt"

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
