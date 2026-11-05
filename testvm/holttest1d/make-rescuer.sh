#!/usr/bin/env bash
# Build the HoltTest1 rescuer: a standalone copy of the debian13 guest with
# every tool bko-holt load may call, and no pool or array of its own, so it
# can rebuild any source (a ZFS rescuer would already have its own rpool
# imported).  Run as root on a test host:
#
#   make-rescuer.sh [--uefi] [PORT]
#
# Writes /bkoscratch/holttest1d/rescuer/rescuer.qcow2 (a copy, not an
# overlay: the debian13 guest may be updated under it).  OpenZFS comes
# from trixie-backports (2.4), for Ubuntu 26.04's pools.  ht1.sh boots
# throwaway overlays of it.  It stands in for BackOtter's rescue .img, which
# has ZFS too, until bko-holt and an SSH key are in that image.
#
# --uefi makes rescuer-uefi.qcow2 instead, from the ubuntu26-uefi guest
# (Ubuntu 26.04, GPT, booted by OVMF), for UEFI sources: grub-mkconfig
# writes a UEFI system's grub.cfg only on a system booted by UEFI, as
# BackOtter's stick is on such a machine.
set -euo pipefail

UEFI=0; [[ ${1:-} == --uefi ]] && { UEFI=1; shift; }
PORT=${1:-2979}
W=/home/m/backotter-work
D=/bkoscratch/holttest1d/rescuer
if ((UEFI)); then BASE=ubuntu26-uefi NAME=rescuer-uefi FW=(--uefi); else BASE=debian13 NAME=rescuer FW=(); fi
KEY=$W/vms/$BASE/id_ed25519
mkdir -p "$D"
export SSH_PORT=$PORT TMPDIR=/bkoscratch/tmp
ssh_r() { timeout "${TMO:-1800}" ssh -q -i "$KEY" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@127.0.0.1 "$@"; }
up() {
    setsid -f "$W/testvm/vm-run" "${FW[@]}" --memory 4096 --cpus 4 --serial-log "$D/$NAME-serial.log" "$D/$NAME.new.qcow2" </dev/null >"$D/$NAME-vm-run.log" 2>&1
    for _ in $(seq 120); do TMO=8 ssh_r true 2>/dev/null && return 0; sleep 5; done
    echo "rescuer: no SSH (see $D/$NAME-serial.log)" >&2; return 1
}
down() {
    TMO=20 ssh_r poweroff >/dev/null 2>&1 || true
    for _ in $(seq 60); do pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null || return 0; sleep 2; done
    pkill -f "^qemu-system.*:$PORT-:22"; sleep 3
}

rm -f "$D/$NAME.new.qcow2"
qemu-img convert -O qcow2 "$W/vms/$BASE/$BASE.qcow2" "$D/$NAME.new.qcow2"
qemu-img resize -q "$D/$NAME.new.qcow2" +6G
up
if ((UEFI)); then
    INSTALL='apt-get update -q
apt-get install -y -q zfsutils-linux f2fs-tools nilfs-tools jfsutils mdadm cryptsetup lvm2 xfsprogs btrfs-progs dosfstools efibootmgr gdisk'
else
    INSTALL='for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do
    [ -f "$f" ] && sed -i -E "/^(Components:|deb )/ { / contrib/! s/ main/ main contrib/ }" "$f"
done
# OpenZFS from trixie-backports (2.4): it makes a 2.4 pool (Ubuntu 26.04s),
# and an older one exactly, given only its feature flags.
echo "deb http://deb.debian.org/debian trixie-backports main contrib" > /etc/apt/sources.list.d/backports.list
apt-get update -q
apt-get install -y -q linux-image-amd64 linux-headers-amd64
apt-get install -y -q -t trixie-backports zfs-dkms zfsutils-linux
apt-get install -y -q f2fs-tools nilfs-tools jfsutils mdadm cryptsetup lvm2 xfsprogs btrfs-progs dosfstools efibootmgr gdisk'
fi
ssh_r "set -e
export DEBIAN_FRONTEND=noninteractive
$(cat <<'GROW'
# Room for the new kernel and DKMS: grow / if its partition is last.
root=$(findmnt -no SOURCE / | sed "s/\\[.*//"); disk=/dev/$(lsblk -no PKNAME "$root"); n=${root##*[!0-9]}
if command -v sfdisk >/dev/null && [ "$(lsblk -nro NAME "$disk" | tail -1)" = "$(basename "$root")" ]; then
    sfdisk --relocate gpt-bak-std "$disk" 2>/dev/null || true   # GPT: its backup header to the new end
    echo ", +" | sfdisk -q -N "$n" --no-reread "$disk" || true; partx -u "$disk" || true
    case $(findmnt -no FSTYPE /) in ext4) resize2fs "$root" ;; btrfs) btrfs filesystem resize max / ;; xfs) xfs_growfs / ;; esac
fi
GROW
)
$INSTALL
$(cat <<'REST'
# No pool of its own, and nothing found on the target disks imported.
systemctl disable zfs-import-cache.service zfs-import-scan.service zfs-mount.service zfs-zed.service 2>/dev/null || true
apt-get clean
df -h /
REST
)" > "$D/$NAME-build.log" 2>&1 || { echo "rescuer: install failed (see $D/$NAME-build.log)" >&2; tail -20 "$D/$NAME-build.log" >&2; down; exit 1; }
ssh_r reboot || true
sleep 10
up
ssh_r 'modprobe zfs && modprobe 9pnet_virtio && modprobe 9p && echo "kernel $(uname -r), $(zfs version | head -2 | tr "\n" " ")"; for t in mkfs.f2fs mkfs.nilfs2 mdadm cryptsetup lvm mkfs.xfs mkfs.btrfs mkfs.vfat efibootmgr zpool; do command -v $t >/dev/null || echo "MISSING $t"; done' | tee -a "$D/$NAME-build.log"
down
mv "$D/$NAME.new.qcow2" "$D/$NAME.qcow2"
sha256sum "$D/$NAME.qcow2" | tee "$D/$NAME.qcow2.sha256"
