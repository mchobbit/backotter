#!/usr/bin/env bash
# Build an OmniOS (illumos) test guest for BackOtter from OmniOS's official
# cloud image, under QEMU with KVM.  The image is a finished install with
# cloud-init; its first boot here sets the host name, root's password and
# SSH key, then powers off.
#
# Usage: ./make-omnios.sh [--rpool-mirror] RELEASE WORK_DIR
#   e.g. ./make-omnios.sh r151058 vms/omnios58
#        ./make-omnios.sh --rpool-mirror r151058 vms/omnios58-mirror
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
#                         (with --rpool-mirror, "--disk rpool-b.qcow2" too)
#   rpool-b.qcow2         with --rpool-mirror: the pool's second disk, the
#                         same size, snapshot "fresh-install" too (a name
#                         that sorts after omniosN-mirror.qcow2)
#   mirror-serial.log, boot-a-serial.log, boot-b-serial.log
#                         with --rpool-mirror: the consoles of the boot that
#                         attaches the second disk and of each disk's boot
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
# --rpool-mirror: rpool a two-way mirror.  After the first boot, a second
# boot with a second blank disk attaches it (zpool attach of the whole disk,
# which labels it as the first, with a system partition of the pool's
# bootsize), waits for the resilver, and puts the loader on both disks
# (bootadm install-bootloader -P rpool, as illumos does for a mirrored root
# pool); then each disk is booted alone (writes thrown away) and must come
# up with rpool DEGRADED on that disk.  Host name omniosN-mirror.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, zstd, openssl,
# python3 with yaml, wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
BOOT_TIMEOUT=900

here=$(dirname "$(realpath "$0")")
die() { echo "make-omnios: $*" >&2; exit 1; }

mirror=no
[[ ${1:-} == --rpool-mirror ]] && { mirror=yes; shift; }
[[ $# -eq 2 ]] || die "usage: $0 [--rpool-mirror] RELEASE WORK_DIR"
release=$1
[[ $release =~ ^r1510[0-9]{2}$ ]] || die "RELEASE looks like r151058, not '$release'"
IMAGE=omnios-$release.cloud.raw.zst
url=https://downloads.omnios.org/media/$release
guest_name=omnios${release: -2}
[[ $mirror == yes ]] && guest_name+=-mirror
DISK=$guest_name.qcow2 DISK_B=rpool-b.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"
[[ $mirror == yes && -e $DISK_B ]] && die "$2/$DISK_B already exists; move it away to rebuild"

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
qpid=
trap 'rm -rf "$work" "$raw"; [[ -z $qpid ]] || kill "$qpid" 2>/dev/null' EXIT
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

# --- The mirror (--rpool-mirror) ---------------------------------------------------
if [[ $mirror == yes ]]; then
    qemu-img create -q -f qcow2 "$DISK_B" "$DISK_SIZE"
    port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
    gssh() { timeout "${TMO:-60}" ssh -q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@127.0.0.1 "$@"; }
    boot() { # LOG DRIVE_OPTIONS...: up, and its sshd answering
        local log=$1; shift
        qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc "$@" \
            -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22" -global virtio-net-pci.disable-modern=on \
            -display none -serial "file:$log" -monitor none -no-reboot </dev/null &
        qpid=$!
        # (QEMU's user network accepts the connection before the guest is up:
        # ssh under timeout.)
        local i
        for ((i = 0; i < BOOT_TIMEOUT / 5; i++)); do
            TMO=10 gssh true 2>/dev/null && return 0
            kill -0 "$qpid" 2>/dev/null || return 1
            sleep 5
        done
        return 1
    }
    halt() {
        TMO=30 gssh '/usr/sbin/shutdown -y -i5 -g0' >/dev/null 2>&1 || true
        local i
        for ((i = 0; i < 60; i++)); do kill -0 "$qpid" 2>/dev/null || break; sleep 5; done
        kill "$qpid" 2>/dev/null || true
        wait "$qpid" 2>/dev/null || true
        qpid=
    }
    echo "Attaching $DISK_B to rpool; the console goes to $2/mirror-serial.log"
    boot mirror-serial.log -drive "file=$DISK,if=virtio,format=qcow2" -drive "file=$DISK_B,if=virtio,format=qcow2" ||
        { halt; die "the guest didn't come up with its second disk; see mirror-serial.log"; }
    TMO=3600 gssh /bin/sh -s <<'MIRROR' >mirror.log 2>&1 || { cat mirror.log >&2; halt; die "attaching the second disk failed; see mirror.log"; }
set -e
PATH=/usr/sbin:/usr/bin:/sbin
# The pool's one disk (c3t0d0s1: its slice) and the other, blank one.
a=$(zpool status rpool | awk '$1 ~ /^c[0-9]+t[0-9]+d[0-9]+(s[0-9]+)?$/ { print $1; exit }')
devfsadm -c disk >/dev/null 2>&1 || true
b=$(diskinfo -Hp | cut -f2 | grep -vx "${a%s[0-9]*}")
[ -n "$a" ] && [ "$(echo "$b" | wc -l)" -eq 1 ] && [ -n "$b" ] || { echo "rpool on '$a', other disks '$b'"; exit 1; }
echo "attaching $b to rpool's $a"
# The whole disk: zpool labels it as the bootable pool's own (system
# partition, then the pool's slice).
zpool attach -f rpool "$a" "$b"
sleep 5
while zpool status rpool | grep -q 'resilver in progress'; do sleep 5; done
bootadm install-bootloader -M -f -P rpool
zpool status rpool
prtvtoc "/dev/rdsk/${b}s1" 2>/dev/null || prtvtoc "/dev/rdsk/$b" || true
[ "$(zpool list -H -o health rpool)" = ONLINE ]
zpool status rpool | grep -q 'mirror-0 *ONLINE'
echo MIRROR-OK
MIRROR
    grep -q '^MIRROR-OK' mirror.log || { halt; die "rpool isn't an ONLINE mirror; see mirror.log"; }
    halt
    # Each disk alone (writes thrown away): it boots, rpool on it DEGRADED.
    for d in "$DISK" "$DISK_B"; do
        l=boot-$([[ $d == "$DISK" ]] && echo a || echo b)
        echo "Booting $d alone; the console goes to $2/$l-serial.log"
        boot "$l-serial.log" -drive "file=$d,if=virtio,format=qcow2,snapshot=on" ||
            { halt; die "$d doesn't boot alone; see $l-serial.log"; }
        TMO=120 gssh '/usr/sbin/zpool status rpool' >"$l.log" 2>&1 || true
        halt
        grep -q 'state: DEGRADED' "$l.log" || die "$d alone: rpool isn't DEGRADED; see $l.log"
    done
    qemu-img snapshot -c fresh-install "$DISK_B"
fi

if [[ $mirror == yes ]]; then echo "--legacy-virtio-net --disk $DISK_B"; else echo "--legacy-virtio-net"; fi >vm-run-options
qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk)$([[ $mirror == no ]] || echo " and $DISK_B ($(du -h "$DISK_B" | cut -f1))"), snapshot \"fresh-install\""
