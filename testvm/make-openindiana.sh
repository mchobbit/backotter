#!/usr/bin/env bash
# Build an OpenIndiana Hipster (illumos) test guest for BackOtter, under QEMU
# with KVM, in one of two ways (docs/design/openindiana.md, "Guests"):
#
#   ./make-openindiana.sh NAME TEXT_ISO WORK_DIR          (UEFI)
#       e.g. ./make-openindiana.sh oi2610 .../OI-hipster-text-20261030.iso vms/oi2610
#     OpenIndiana's text installer, from its text ISO, driven over the serial
#     console by openindiana/install.py: the whole disk, the installer's own
#     layout (ESP, rpool, BE, swap and dump volumes), booted with OVMF.
#
#   ./make-openindiana.sh --cloud NAME CLOUD_IMG_ZST WORK_DIR   (BIOS)
#       e.g. ./make-openindiana.sh --cloud oi2610-bios .../OI-hipster-cloudimage-20261107.img.zst vms/oi2610-bios
#     OpenIndiana's supplemental cloud image (built by oi-packer), with a
#     NoCloud "cidata" CD for its own metadata agent (not Python's
#     cloud-init: a subset of cloud-config, network-config version 1), as
#     make-omnios.sh does with OmniOS's.
#
# The media are pinned by SHA-256 (PINNED below); OpenIndiana publishes a
# .sha256sum beside each and no signature, so the pin is what guards a
# rebuild.  Nothing is downloaded: rebuild-one.sh hands in the copies in
# /bkoscratch/rebuild-inputs/NAME.
# Results, in WORK_DIR:
#   NAME.qcow2            the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install"
#   root-password         root's password (mode 600), for the serial console
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the console of the install (or first boot)
#   setup.log             what the setup over SSH did
#   vm-run-options        how vm-run must boot it: "--legacy-virtio-net"
#                         (illumos' vioif can't map the modern virtio NIC's
#                         registers), and "--uefi --no-secure-boot" for the
#                         installer's guest
#   uefi-vars.fd          the installer's guest: OVMF's variables after the
#                         install, kept for reference only (vm-run starts
#                         each boot from blank ones: the loader is found on
#                         the ESP's fallback path, as on a new machine)
#
# The guest: host name NAME, root with the key and the password, sshd
# letting root in by key, swap on rpool/swap, the console on the serial
# port, 2 GiB RAM.
# vm-mount can't open it: Linux's OpenZFS may refuse an illumos pool, and
# importing it would mark it as this host's.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, zstd, openssl,
# python3, OVMF (/usr/share/OVMF) for the installer's guest.  Runs as an
# ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=${MEMORY:-3072}
BOOT_TIMEOUT=900
INSTALL_TIMEOUT=3600

# The media this script knows, by file name: their SHA-256 as published
# beside them on dlc.openindiana.org (read 2026-11-14).
declare -A PINNED=(
    [OI-hipster-text-20261030.iso]=c5cd9572219f49975c333ac8339b18989e5477ab2e81844b0b84c3ae776a6e1a
    [OI-hipster-cloudimage-20261107.img.zst]=cd56ca63763b48ded9563fd029719390321193bf209ef9c9fa2c1821697875bc
)

here=$(dirname "$(realpath "$0")")
die() { echo "make-openindiana: $*" >&2; exit 1; }

cloud=no
[[ ${1:-} == --cloud ]] && { cloud=yes; shift; }
[[ $# -eq 3 ]] || die "usage: $0 [--cloud] NAME MEDIA WORK_DIR"
name=$1 media=$(realpath "$2") work_dir=$3
[[ $name =~ ^[a-z][a-z0-9-]*$ ]] || die "NAME '$name' isn't a host name"
base=${media##*/}
want=${PINNED[$base]:-}
[[ -n $want ]] || die "$base isn't one of the pinned media (${!PINNED[*]})"
[[ $cloud == yes && $base == *.img.zst ]] || [[ $cloud == no && $base == *.iso ]] ||
    die "$base: --cloud takes the cloud image, the installer the text ISO"
echo "Checking $base"
echo "$want  $media" | sha256sum -c --quiet - || die "$base does not match its pinned SHA-256"

DISK=$name.qcow2
mkdir -p "$work_dir"
cd "$work_dir"
[[ -e $DISK ]] && die "$work_dir/$DISK already exists; move it away to rebuild"

work=$(mktemp -d)
qpid=
trap 'rm -rf "$work"; [[ -z $qpid ]] || kill "$qpid" 2>/dev/null' EXIT

umask 077
# Hex: typed into the installer and the console.
password=$(openssl rand -hex 12)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$name test guest" -f id_ed25519
umask 022

port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
gssh() { timeout "${TMO:-60}" ssh -q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@127.0.0.1 "$@"; }
if [[ $cloud == yes ]]; then
    fw=(-machine pc)
else
    fw=(-machine q35 -drive "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd"
        -drive "if=pflash,format=raw,unit=1,file=$PWD/uefi-vars.fd")
fi
nic=(-nic "user,model=virtio-net-pci,mac=52:54:00:12:34:56,hostfwd=tcp:127.0.0.1:$port-:22" -global virtio-net-pci.disable-modern=on)
wait_ssh() { # PID: its sshd answering with root's key
    local i
    for ((i = 0; i < BOOT_TIMEOUT / 5; i++)); do
        TMO=10 gssh true 2>/dev/null && return 0
        kill -0 "$1" 2>/dev/null || return 1
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

if [[ $cloud == yes ]]; then
    # --- The cloud image, and its first boot's seed --------------------------------
    raw=$(mktemp -p . --suffix=.raw image.XXXXXX)
    zstd -q -d -f "$media" -o "$raw"
    qemu-img convert -f raw -O qcow2 "$raw" "$DISK"
    rm -f "$raw"
    qemu-img resize -q "$DISK" "$DISK_SIZE"
    mkdir "$work/cidata"
    # OI's agent: the blank line after #cloud-config matters (its README).
    printf '#cloud-config\n\nusers:\n  - name: root\n    ssh_authorized_keys:\n      - %s\n' "$(cat id_ed25519.pub)" \
        >"$work/cidata/user-data"
    printf 'instance-id: %s\nhostname: %s\n' "$name-$(date -u +%Y%m%d%H%M%S)" "$name" >"$work/cidata/meta-data"
    cat >"$work/cidata/network-config" <<'NET'
network:
  version: 1
  config:
    - type: physical
      name: net0
      mac_address: "52:54:00:12:34:56"
      subnets:
        - type: dhcp4
    - type: nameserver
      address: [10.0.2.3]
      search: []
NET
    xorriso -as mkisofs -quiet -o "$work/cidata.iso" -V cidata -J -r "$work/cidata" 2>"$work/xorriso.log" ||
        { cat "$work/xorriso.log" >&2; die "could not make the cidata ISO"; }
    echo "First boot; its console goes to $work_dir/install-serial.log"
    qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${fw[@]}" \
        -drive "file=$DISK,if=virtio,format=qcow2" \
        -drive "file=$work/cidata.iso,media=cdrom,readonly=on" "${nic[@]}" \
        -display none -serial file:install-serial.log -monitor none -no-reboot </dev/null &
    qpid=$!
    wait_ssh "$qpid" || { halt; die "the first boot never let root in by key; see install-serial.log"; }
else
    # --- The text installer ----------------------------------------------------------
    cp /usr/share/OVMF/OVMF_VARS_4M.fd uefi-vars.fd
    qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
    printf '%s\n' "$password" >"$work/password"
    # QEMU with its console and monitor on sockets in $work/STAGE, driven by
    # openindiana/install.py (the loader takes keys only from the monitor's
    # sendkey); the console is kept as LOG.
    drive() { # STAGE LOG TIMEOUT INSTALL_PY_ARGS... -- QEMU_DRIVE_ARGS...
        local stage=$1 log=$2 tmo=$3; shift 3
        local args=() rc=0
        while [[ $1 != -- ]]; do args+=("$1"); shift; done; shift
        mkdir -p "$work/$stage"
        qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${fw[@]}" "$@" "${nic[@]}" \
            -display none -serial "unix:$work/$stage/con.sock,server,wait=off" \
            -monitor "unix:$work/$stage/mon.sock,server,wait=off" -no-reboot </dev/null &
        qpid=$!
        timeout "$tmo" python3 -I "$here/openindiana/install.py" "${args[0]}" "$work/$stage" "${args[@]:1}" || rc=$?
        cp "$work/$stage/console.log" "$log" 2>/dev/null || true
        if [[ $rc -ne 0 ]]; then kill "$qpid" 2>/dev/null || true; fi
        wait "$qpid" 2>/dev/null || true
        qpid=
        return $rc
    }
    echo "Installing; the console goes to $work_dir/install-serial.log"
    drive install install-serial.log "$INSTALL_TIMEOUT" install "$name" "$work/password" -- \
        -drive "file=$DISK,if=virtio,format=qcow2" -drive "file=$media,media=cdrom,readonly=on" ||
        die "the install failed or timed out; see install-serial.log"
    # The first boot from the disk, with no NVRAM entry (the installer makes
    # none: the firmware finds \EFI\BOOT\BOOTX64.EFI on the ESP): tester
    # logs in on the console and su's, and root gets the key.
    echo "First boot from the disk; the console goes to $work_dir/firstboot-serial.log"
    drive first firstboot-serial.log "$BOOT_TIMEOUT" first-boot "$name" "$work/password" "$PWD/id_ed25519.pub" -- \
        -drive "file=$DISK,if=virtio,format=qcow2" ||
        die "the first boot failed; see firstboot-serial.log"
    qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${fw[@]}" \
        -drive "file=$DISK,if=virtio,format=qcow2" "${nic[@]}" \
        -display none -serial file:boot-serial.log -monitor none -no-reboot </dev/null &
    qpid=$!
    wait_ssh "$qpid" || { halt; die "the installed system never let root in by key; see boot-serial.log"; }
fi

# --- Setup, over SSH ------------------------------------------------------------------
echo "Setting up; see $work_dir/setup.log"
TMO=1800 gssh /bin/sh -s "$name" "$hash" <<'SETUP' >setup.log 2>&1 || { cat setup.log >&2; halt; die "the setup failed; see setup.log"; }
set -e
PATH=/usr/gnu/bin:/usr/sbin:/usr/bin:/sbin
name=$1 hash=$2
cat /etc/os-release || true
uname -a
echo "$name" >/etc/nodename
hostname "$name"
# root's password, for the serial console (a role on some installs: a
# plain account here, so that the console login works).
sed -i "s|^root:[^:]*:|root:$hash:|" /etc/shadow
if grep -q '^root::::type=role' /etc/user_attr 2>/dev/null; then
    sed -i 's|^root::::type=role;|root::::|' /etc/user_attr
fi
# sshd: root by key only.
c=/etc/ssh/sshd_config
grep -q '^PermitRootLogin' $c && sed -i 's/^PermitRootLogin.*/PermitRootLogin without-password/' $c ||
    echo 'PermitRootLogin without-password' >>$c
# Swap: a volume in the pool, as illumos' installer makes it.
if ! swap -l 2>/dev/null | grep -q zvol; then
    zfs list rpool/swap >/dev/null 2>&1 || zfs create -V 2G rpool/swap
    grep -q rpool/swap /etc/vfstab || printf '/dev/zvol/dsk/rpool/swap\t-\t\t-\t\tswap\t-\tno\t-\n' >>/etc/vfstab
    swap -a /dev/zvol/dsk/rpool/swap
fi
# The console on the serial port (the cloud image's already is).
grep -q '^console=' /boot/conf.d/* 2>/dev/null || { mkdir -p /boot/conf.d; echo 'console="ttya,text"' >/boot/conf.d/serial; }
# The cloud image's metadata agent: done with (later boots have no cidata).
# (metadata, and useragent and userscript, which need it.)
for s in $(svcs -H -o fmri 2>/dev/null | grep -E '^svc:/system/illumos/(metadata|useragent|userscript):' || true); do svcadm disable "$s"; done
zpool list; zfs list; swap -l; beadm list
svcs -x || true
echo SETUP-OK
SETUP
grep -q '^SETUP-OK' setup.log || { halt; die "the setup didn't finish; see setup.log"; }
halt

if [[ $cloud == yes ]]; then echo "--legacy-virtio-net"; else echo "--uefi --no-secure-boot --legacy-virtio-net"; fi >vm-run-options
qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $work_dir/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
