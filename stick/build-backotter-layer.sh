#!/usr/bin/env bash
# Build BackOtter's own squashfs layer, stacked on the slimmed base layer from
# slim-base-layer.sh: casper/ubuntu-server-minimal.backotter.squashfs.
#
# Usage: sudo ./build-backotter-layer.sh SOURCE_ISO SLIM_DIR OUT_DIR
#
# SOURCE_ISO must be the ISO SLIM_DIR was built from: the packages come from
# its pool/, which pins them to exactly the base layer's release and needs no
# network.  OUT_DIR receives:
#   ubuntu-server-minimal.backotter.squashfs   the layer
#   ubuntu-server-minimal.backotter.manifest   package list of base + layer
#   ubuntu-server-minimal.backotter.size       unpacked size of the layer
#   report.txt
#
# The layer is the upper directory of an overlay mounted on SLIM_DIR/rootfs,
# so it holds only what differs from the base (deletions are overlay
# whiteouts, which squashfs keeps and casper's overlay honours).  It must
# always be rebuilt together with the base: installing packages writes the
# layer's own /var/lib/dpkg/status and /etc/passwd, which hide the base's.
# Everything else is added as new files (drop-ins), never as edited copies of
# base files; those files live under stick/layer/ next to this script.
#
# What the layer does:
#   - adds wpasupplicant (Wi-Fi), efibootmgr, zfsutils-linux, openssh-server
#   - tty1 and serial consoles run /usr/lib/backotter/console as root;
#     tty2-tty6 are root shells
#   - DHCP on wired interfaces; nothing waits for the network at boot
#   - sshd installed but off, with no host keys in the image; remote access
#     is turned on from the console with a one-time password
#   - ZFS pools are never imported or mounted automatically
#   - cloud-init disabled; the ISO's package repository source removed
#     (make-img.sh drops pool/ and dists/)
#
# Needs: Linux, root, squashfs-tools 4.6 or later.

set -euo pipefail

LAYER=ubuntu-server-minimal.backotter
PACKAGES=(wpasupplicant efibootmgr zfsutils-linux openssh-server)
# The stick must never import or mount the pools of the machine it rescues on
# its own; BackOtter imports them explicitly when restoring.
MASK_UNITS=(
    zfs-import-cache.service zfs-import-scan.service zfs-mount.service
    zfs-share.service zfs-zed.service
    systemd-networkd-wait-online.service   # nothing on the stick needs network-online
)
DISABLE_UNITS=(ssh.service ssh.socket)     # remote-access starts ssh.service
# The only base files the layer may hide (as overlay whiteouts).
ALLOWED_WHITEOUTS=(/etc/apt/sources.list.d/cdrom.sources)
# Same settings as slim-base-layer.sh; see the comment there.
MKSQUASHFS_OPTS=(-comp xz -Xbcj x86 -b 1M -Xdict-size 1M -noappend -no-progress)

here=$(dirname "$(realpath "$0")")
die() { echo "build-backotter-layer: $*" >&2; exit 1; }

[[ $# -eq 3 ]] || die "usage: $0 SOURCE_ISO SLIM_DIR OUT_DIR"
SOURCE_ISO=$(realpath "$1")
SLIM=$(realpath "$2")
OUT=$3
[[ $EUID -eq 0 ]] || die "must run as root"
[[ -f $SOURCE_ISO ]] || die "no such ISO: $SOURCE_ISO"
[[ -d $SLIM/rootfs ]] || die "$SLIM has no rootfs/ (run slim-base-layer.sh first)"
[[ -d $here/layer ]] || die "missing $here/layer"
command -v mksquashfs >/dev/null || die "missing mksquashfs"
if [[ -e $OUT ]]; then
    [[ -e $OUT/.build-backotter-layer ]] || die "$OUT exists and was not made by this script"
    rm -rf "$OUT"
fi
mkdir -p "$OUT"
touch "$OUT/.build-backotter-layer"
OUT=$(realpath "$OUT")
REPORT=$OUT/report.txt
: >"$REPORT"
log() { echo "$*" | tee -a "$REPORT"; }

work=$OUT/work
upper=$work/upper
root=$work/merged
mkdir -p "$upper" "$work/overlay-work" "$root" "$work/iso"

mounts=()
cleanup() {
    local i
    for ((i = ${#mounts[@]} - 1; i >= 0; i--)); do umount "${mounts[i]}" || true; done
    mounts=()
}
trap cleanup EXIT
mount_on() { mount "$@"; mounts+=("${!#}"); }

mount_on -t overlay overlay \
    -o "lowerdir=$SLIM/rootfs,upperdir=$upper,workdir=$work/overlay-work" "$root"
mount_on -o loop,ro "$SOURCE_ISO" "$work/iso"
for m in proc sys dev dev/pts; do mount_on --bind "/$m" "$root/$m"; done
had_cdrom_dir=no
[[ -d $root/cdrom ]] && had_cdrom_dir=yes
mkdir -p "$root/cdrom"
mount_on --bind "$work/iso" "$root/cdrom"
in_root() { chroot "$root" "$@"; }
printf '#!/bin/sh\nexit 101\n' >"$root/usr/sbin/policy-rc.d"
chmod 755 "$root/usr/sbin/policy-rc.d"
export DEBIAN_FRONTEND=noninteractive
# needrestart's apt hook would inspect the build host's processes through /proc.
export NEEDRESTART_SUSPEND=1

# --- Packages, from the ISO's own repository only ---------------------------------
# cdrom.sources is the base layer's entry for that repository (file:///cdrom,
# signed with the ISO's one-time key).
apt_dir=/tmp/backotter-apt
mkdir -p "$root$apt_dir"
cp "$root/etc/apt/sources.list.d/cdrom.sources" "$root$apt_dir/"
apt_opts=(-o "Dir::Etc::SourceList=/dev/null" -o "Dir::Etc::SourceParts=$apt_dir")
# --no-list-cleanup: keep the base's lists for Ubuntu's online archive.
in_root apt-get "${apt_opts[@]}" -q update --no-list-cleanup >/dev/null
before=$(in_root dpkg-query -W -f '${Package}\n' | sort)
in_root apt-get "${apt_opts[@]}" -y -q --no-install-recommends install "${PACKAGES[@]}" >/dev/null
added=$(comm -13 <(echo "$before") <(in_root dpkg-query -W -f '${Package}\n' | sort))
log "== Packages added (KB, name)"
# shellcheck disable=SC2086  # one package name per word
in_root dpkg-query -W -f '${Installed-Size}\t${Package}\n' $added | sort -rn |
    sed 's/^/  /' | tee -a "$REPORT"
rm -rf "$root$apt_dir"
# pool/ and dists/ are not on BackOtter's image, so the base's entry must go:
# this one deletion becomes a whiteout in the layer.
rm "$root/etc/apt/sources.list.d/cdrom.sources"

# --- Configuration ----------------------------------------------------------------
# Files from stick/layer/, as root; mode 755 if executable in the checkout.
while IFS= read -r -d '' f; do
    rel=${f#"$here/layer/"}
    mode=644
    [[ -x $f ]] && mode=755
    install -D -o root -g root -m "$mode" "$f" "$root/$rel"
done < <(find "$here/layer" -type f -print0)
chmod 600 "$root/etc/netplan/50-backotter-wired.yaml"   # netplan warns otherwise
chmod 440 "$root/etc/sudoers.d/backotter"
in_root visudo -cq -f /etc/sudoers.d/backotter || die "sudoers file rejected"

in_root systemctl mask "${MASK_UNITS[@]}" >/dev/null 2>&1
in_root systemctl disable "${DISABLE_UNITS[@]}" >/dev/null 2>&1
# openssh-server's postinst generated host keys; every stick makes its own.
rm -f "$root"/etc/ssh/ssh_host_*

# --- Checks --------------------------------------------------------------------------
in_root dpkg --audit | grep . && die "dpkg --audit reports problems"
for c in wpa_supplicant efibootmgr zpool zfs sshd; do
    in_root sh -c "command -v $c" >/dev/null || die "missing $c after install"
done
for u in "${MASK_UNITS[@]}"; do
    [[ $(in_root systemctl is-enabled "$u" 2>/dev/null || true) == masked ]] ||
        die "$u is not masked"
done
for u in "${DISABLE_UNITS[@]}"; do
    [[ $(in_root systemctl is-enabled "$u" 2>/dev/null || true) == enabled ]] &&
        die "$u is still enabled"
done
in_root bash -n /usr/lib/backotter/console
in_root bash -n /usr/lib/backotter/remote-access

in_root dpkg-query -W -f '${binary:Package}\t${Version}\n' >"$OUT/$LAYER.manifest"
rm -f "$root/usr/sbin/policy-rc.d"
cleanup
trap - EXIT
[[ $had_cdrom_dir == yes ]] || rmdir "$upper/cdrom" 2>/dev/null || true
# apt's caches and package lists (the ISO repository's, and the base's that
# apt and AppStream rewrote) are just caches: drop the layer's copies so the
# base's show through unchanged.
rm -rf "$upper/var/cache/apt" "$upper/var/lib/apt/lists" "$upper/var/lib/swcatalog"
# Everything else must be additions; overlay whiteouts are character devices.
mapfile -t whiteouts < <(cd "$upper" && find . -type c | sed 's#^\.##' | sort)
unexpected=$(comm -23 <(printf '%s\n' "${whiteouts[@]}") <(printf '%s\n' "${ALLOWED_WHITEOUTS[@]}" | sort))
[[ -z $unexpected ]] || die "the layer would hide base files:"$'\n'"$unexpected"

# --- Pack the layer -------------------------------------------------------------------
du -sx --block-size=1 "$upper" | cut -f1 >"$OUT/$LAYER.size"
mksquashfs "$upper" "$OUT/$LAYER.squashfs" "${MKSQUASHFS_OPTS[@]}" >/dev/null
log ""
log "== Layer"
log "Files:      $(find "$upper" -type f | wc -l)"
log "Hides:      ${whiteouts[*]}"
log "Unpacked:   $(awk -v b="$(cat "$OUT/$LAYER.size")" 'BEGIN { printf "%.1f MB", b / 1048576 }')"
log "Squashfs:   $(awk -v b="$(stat -c %s "$OUT/$LAYER.squashfs")" 'BEGIN { printf "%.1f MB", b / 1048576 }')"
rm -rf "$work"
