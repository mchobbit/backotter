#!/usr/bin/env bash
# Build BackOtter's own squashfs layer, stacked on the slimmed base layer from
# slim-base-layer.sh: casper/ubuntu-server-minimal.backotter.squashfs.
#
# Usage: sudo ./build-backotter-layer.sh SOURCE_ISO SLIM_DIR OUT_DIR
#
# SOURCE_ISO must be the ISO SLIM_DIR was built from: the packages come from
# its pool/, which pins them to exactly the base layer's release.  The few the
# ISO lacks come from the network, each named below: from Ubuntu's archive (its
# release pocket, whose packages never change) and from apt.bcachefs.org.
# OUT_DIR receives:
#   ubuntu-server-minimal.backotter.squashfs   the layer
#   ubuntu-server-minimal.backotter.manifest   package list of base + layer
#   ubuntu-server-minimal.backotter.size       unpacked size of the layer
#   report.txt
#
# The layer is the upper directory of an overlay mounted on SLIM_DIR/rootfs,
# so it holds only what differs from the base (deletions are overlay
# whiteouts, which squashfs keeps and casper's overlay honours).  It must
# always be rebuilt together with the base: installing packages writes the
# layer's own /var/lib/dpkg/status and /etc/passwd, which hide the base's,
# and so do depmod's module indexes.  Everything else is added as new files
# (drop-ins), never as edited copies of base files; those files live under
# stick/layer/ next to this script.
#
# What the layer does:
#   - adds wpasupplicant (Wi-Fi), efibootmgr, zfsutils-linux, openssh-server
#   - adds the mkfs tools a restore needs beyond the base's: dosfstools (EFI
#     system partitions), jfsutils, nilfs-tools, f2fs-tools, bcachefs-tools,
#     and a bcachefs kernel
#     module built by DKMS for the image's kernel.  The module isn't signed,
#     so it loads only with Secure Boot off.  Its build (compiler, kernel
#     headers, DKMS) happens in a throwaway overlay; only the module lands
#     in the layer.
#   - tty1 and serial consoles run /usr/lib/backotter/console as root;
#     tty2-tty6 are root shells
#   - DHCP on wired interfaces; nothing waits for the network at boot
#   - sshd installed but off, with no host keys in the image; remote access
#     is turned on from the console with a one-time password
#   - ZFS pools are never imported or mounted automatically
#   - cloud-init disabled; the ISO's package repository source removed
#     (make-img.sh drops pool/ and dists/), and so are the build's own
#     sources for Ubuntu's archive and apt.bcachefs.org
#
# Needs: Linux, root, squashfs-tools 4.6 or later, and HTTP(S) access to
# archive.ubuntu.com and apt.bcachefs.org.

set -euo pipefail

LAYER=ubuntu-server-minimal.backotter
# From the ISO's pool.
PACKAGES=(wpasupplicant efibootmgr zfsutils-linux openssh-server jfsutils)
# From Ubuntu's archive, release pocket, main and universe: only these may
# come from there (apt preferences pin everything else away).
ARCHIVE=http://archive.ubuntu.com/ubuntu/
ARCHIVE_PACKAGES=(dosfstools nilfs-tools f2fs-tools)
ARCHIVE_DEPENDENCIES=(libsodium23)          # bcachefs-tools needs it
# bcachefs, from its own repository: the tools into the layer, and the DKMS
# source of the same version to build the module from.  Pinned to the version
# boot-tested; the repository keeps only its latest, so a new release there
# fails the build until this is bumped.
BCACHEFS_REPO=https://apt.bcachefs.org/resolute/
BCACHEFS_SUITE=bcachefs-tools-release
BCACHEFS_KEY=keys/apt.bcachefs.org.asc       # from https://apt.bcachefs.org/apt.bcachefs.org.asc
BCACHEFS_KEY_FPR=64DA57BD19CDE340D3B2DA48F7578FFBB284AA61
BCACHEFS_VERSION=1.39.7
BCACHEFS_DEB_VERSION=1:$BCACHEFS_VERSION
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
[[ -f $here/$BCACHEFS_KEY ]] || die "missing $here/$BCACHEFS_KEY"
command -v mksquashfs >/dev/null || die "missing mksquashfs"
# The image's one kernel, which the bcachefs module is built for (in a chroot
# uname -r is the build host's).
kernels=("$SLIM"/rootfs/lib/modules/*/)
[[ ${#kernels[@]} -eq 1 && -d ${kernels[0]} ]] || die "the base must have exactly one kernel in /lib/modules"
KVER=$(basename "${kernels[0]}")
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
# unmount_from N: unmount mounts[N] and everything mounted after it.  Right
# after the module build, the build root's /dev has been busy for a moment
# (with nothing left running in the chroot), so each gets a few tries.
unmount_from() {
    local i n
    for ((i = ${#mounts[@]} - 1; i >= $1; i--)); do
        n=0
        until umount "${mounts[i]}" 2>/dev/null; do
            ((++n < 10)) || die "can't unmount ${mounts[i]}"
            echo "build-backotter-layer: ${mounts[i]} is busy, trying again" >&2
            sleep 1
        done
        unset 'mounts[i]'
    done
}
# chroot_api ROOT: the host's /proc, /sys and /dev in ROOT, and a tmpfs on its
# /run holding the host's resolver configuration, for apt's downloads (the
# base's /etc/resolv.conf points there).  Whatever package scripts leave in
# /run stays out of the layer, as it would at boot.  The binds are private:
# a bind of the host's (shared) /dev would join its peer group, and the next
# chroot's /dev/pts would show up in it too, and on the host's /dev/pts.
chroot_api() {
    local m
    for m in proc sys dev dev/pts; do
        mount_on --bind "/$m" "$1/$m"
        mount --make-private "$1/$m"
    done
    mount_on -t tmpfs tmpfs "$1/run"
    mkdir -p "$1/run/systemd/resolve"
    cp -L /etc/resolv.conf "$1/run/systemd/resolve/stub-resolv.conf"
}
# write_source DIR NAME URI SUITES KEY [COMPONENTS]: a deb822 apt source, for
# component main unless COMPONENTS says otherwise.
write_source() {
    printf 'Types: deb\nURIs: %s\nSuites: %s\nComponents: %s\nSigned-By: %s\n' \
        "$3" "$4" "${6:-main}" "$5" >"$1/$2.sources"
}
# The host part of a URI: what an apt preference's "Pin: origin" matches.
uri_host() { local h=${1#*://}; echo "${h%%/*}"; }

mount_on -t overlay overlay \
    -o "lowerdir=$SLIM/rootfs,upperdir=$upper,workdir=$work/overlay-work" "$root"
mount_on -o loop,ro "$SOURCE_ISO" "$work/iso"
[[ $(readlink "$root/etc/resolv.conf") == ../run/systemd/resolve/stub-resolv.conf ]] ||
    die "the base's /etc/resolv.conf isn't systemd-resolved's stub"
chroot_api "$root"
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

# --- Packages ------------------------------------------------------------------------
# cdrom.sources is the base layer's entry for the ISO's repository (file:///cdrom,
# signed with the ISO's one-time key).  Beside it, for what the ISO lacks:
# Ubuntu's archive (the same release's pocket, checked against the base's own
# archive key) and apt.bcachefs.org (checked against the key in this
# checkout).  Preferences pin both to the packages named above.
apt_dir=/tmp/backotter-apt
mkdir -p "$root$apt_dir"/{sources,preferences,lists/partial,gnupg}
chmod 700 "$root$apt_dir/gnupg"
cp "$root/etc/apt/sources.list.d/cdrom.sources" "$root$apt_dir/sources/"
suite=$(sed -n 's/^Suites: *//p' "$root/etc/apt/sources.list.d/cdrom.sources")
[[ $suite =~ ^[a-z]+$ ]] || die "can't tell the release from cdrom.sources (Suites: $suite)"
cp "$here/$BCACHEFS_KEY" "$root$apt_dir/bcachefs.asc"
fpr=$(in_root env GNUPGHOME="$apt_dir/gnupg" gpg --batch --quiet --show-keys --with-colons \
    "$apt_dir/bcachefs.asc" | awk -F: '$1 == "fpr" { print $10; exit }')
[[ $fpr == "$BCACHEFS_KEY_FPR" ]] || die "$BCACHEFS_KEY is key $fpr, not $BCACHEFS_KEY_FPR"
write_source "$root$apt_dir/sources" ubuntu "$ARCHIVE" "$suite" /usr/share/keyrings/ubuntu-archive-keyring.gpg \
    "main universe"
write_source "$root$apt_dir/sources" bcachefs "$BCACHEFS_REPO" "$BCACHEFS_SUITE" "$apt_dir/bcachefs.asc"
cat >"$root$apt_dir/preferences/backotter" <<EOF
Package: *
Pin: origin "$(uri_host "$ARCHIVE")"
Pin-Priority: -1

Package: ${ARCHIVE_PACKAGES[*]} ${ARCHIVE_DEPENDENCIES[*]}
Pin: origin "$(uri_host "$ARCHIVE")"
Pin-Priority: 500

Package: *
Pin: origin "$(uri_host "$BCACHEFS_REPO")"
Pin-Priority: -1

Package: bcachefs-tools
Pin: origin "$(uri_host "$BCACHEFS_REPO")"
Pin-Priority: 500
EOF
# The base's own sources, preferences and package lists play no part.
apt_opts=(-o "Dir::Etc::SourceList=/dev/null" -o "Dir::Etc::SourceParts=$apt_dir/sources"
          -o "Dir::Etc::Preferences=/dev/null" -o "Dir::Etc::PreferencesParts=$apt_dir/preferences"
          -o "Dir::State::Lists=$apt_dir/lists")
in_root apt-get "${apt_opts[@]}" -q update >/dev/null
before=$(in_root dpkg-query -W -f '${Package}\n' | sort)
in_root apt-get "${apt_opts[@]}" -y -q --no-install-recommends install \
    "${PACKAGES[@]}" "${ARCHIVE_PACKAGES[@]}" "bcachefs-tools=$BCACHEFS_DEB_VERSION" >/dev/null
added=$(comm -13 <(echo "$before") <(in_root dpkg-query -W -f '${Package}\n' | sort))
log "== Packages added (KB, name)"
# shellcheck disable=SC2086  # one package name per word
in_root dpkg-query -W -f '${Installed-Size}\t${Package}\n' $added | sort -rn |
    sed 's/^/  /' | tee -a "$REPORT"
rm -rf "$root$apt_dir"
# pool/ and dists/ are not on BackOtter's image, so the base's entry must go:
# this one deletion becomes a whiteout in the layer.
rm "$root/etc/apt/sources.list.d/cdrom.sources"

# --- The bcachefs module -------------------------------------------------------------
# Built by DKMS in a throwaway overlay on the layer so far, so the compiler,
# the kernel's headers and DKMS itself never reach the layer; only the module
# does.  The toolchain comes from the release pocket, with the headers for
# exactly KVER from -updates (the only place they are): not linux-headers-
# generic, which bcachefs-kernel-dkms depends on and which would be the
# newest kernel's.  So the DKMS package isn't installed, only unpacked.
kb=$work/kmod
kb_root=$kb/root
mkdir -p "$kb/upper" "$kb/overlay-work" "$kb_root"
kb_mounts=${#mounts[@]}
mount_on -t overlay overlay -o "lowerdir=$root,upperdir=$kb/upper,workdir=$kb/overlay-work" "$kb_root"
chroot_api "$kb_root"
in_kb() { chroot "$kb_root" "$@"; }
kb_apt=/tmp/backotter-kmod-apt
mkdir -p "$kb_root$kb_apt"/{sources,preferences,lists/partial}
cp "$here/$BCACHEFS_KEY" "$kb_root$kb_apt/bcachefs.asc"
write_source "$kb_root$kb_apt/sources" ubuntu "$ARCHIVE" "$suite $suite-updates" /usr/share/keyrings/ubuntu-archive-keyring.gpg
write_source "$kb_root$kb_apt/sources" bcachefs "$BCACHEFS_REPO" "$BCACHEFS_SUITE" "$kb_apt/bcachefs.asc"
cat >"$kb_root$kb_apt/preferences/backotter" <<EOF
Package: *
Pin: release a=$suite-updates
Pin-Priority: -1

Package: linux-headers-$KVER linux-headers-${KVER%-*}
Pin: release a=$suite-updates
Pin-Priority: 500
EOF
kb_apt_opts=(-o "Dir::Etc::SourceList=/dev/null" -o "Dir::Etc::SourceParts=$kb_apt/sources"
             -o "Dir::Etc::Preferences=/dev/null" -o "Dir::Etc::PreferencesParts=$kb_apt/preferences"
             -o "Dir::State::Lists=$kb_apt/lists")
in_kb apt-get "${kb_apt_opts[@]}" -q update >/dev/null
# -updates lists a kernel's headers only while that kernel is recent.
in_kb apt-cache "${kb_apt_opts[@]}" show "linux-headers-$KVER" >/dev/null 2>&1 ||
    die "$suite-updates no longer has linux-headers-$KVER: rebuild the base from a newer ISO"
in_kb apt-get "${kb_apt_opts[@]}" -y -q --no-install-recommends install \
    dkms gcc make "linux-headers-$KVER" >/dev/null
mkdir "$kb_root$kb_apt/deb"
in_kb chown _apt "$kb_apt/deb"     # apt downloads as _apt
in_kb sh -c "cd $kb_apt/deb && apt-get ${kb_apt_opts[*]} -q download bcachefs-kernel-dkms=$BCACHEFS_DEB_VERSION" >/dev/null
in_kb sh -c "dpkg-deb -x $kb_apt/deb/bcachefs-kernel-dkms_*.deb /"
# Unsigned: a key made up here would be no more trusted than no signature.
echo 'sign_file=/nonexistent' >"$kb_root/etc/dkms/framework.conf.d/backotter-unsigned.conf"
in_kb dkms add -m bcachefs -v "$BCACHEFS_VERSION" >/dev/null
# Build it here: the module's build would first fetch a prebuilt one from
# module.bcachefs.org (given a URL that fails, it doesn't), and leave out
# the parts written in Rust.
kb_dkms=/var/lib/dkms/bcachefs/$BCACHEFS_VERSION/$KVER/x86_64
in_kb env BCACHEFS_MODULE_URL=file:///nonexistent BCACHEFS_RUST=0 \
    dkms build -m bcachefs -v "$BCACHEFS_VERSION" -k "$KVER" >/dev/null ||
    { tail -n 20 "$kb_root$kb_dkms/log/make.log" >&2; die "the bcachefs module didn't build"; }
# DKMS keeps the module's debug info (bcachefs's dkms.conf says STRIP=no, to
# keep a prebuilt module's signature): 19 MB rather than 2 MB.
in_kb sh -c "zstd -dq -c $kb_dkms/module/bcachefs.ko.zst >/tmp/bcachefs.ko &&
             strip --strip-debug /tmp/bcachefs.ko && zstd -q /tmp/bcachefs.ko -o /tmp/bcachefs.ko.zst"
cp "$kb_root/tmp/bcachefs.ko.zst" "$work/bcachefs.ko.zst"
unmount_from "$kb_mounts"
rm -rf --one-file-system "$kb"
install -D -o root -g root -m 644 "$work/bcachefs.ko.zst" "$root/lib/modules/$KVER/updates/dkms/bcachefs.ko.zst"
in_root depmod "$KVER"
log ""
log "== bcachefs module for $KVER: $(du -k "$work/bcachefs.ko.zst" | cut -f1) KB"

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
for c in wpa_supplicant efibootmgr zpool zfs sshd mkfs.vfat mkfs.jfs mkfs.nilfs2 mkfs.f2fs mkfs.bcachefs; do
    in_root sh -c "command -v $c" >/dev/null || die "missing $c after install"
done
vermagic=$(in_root modinfo -k "$KVER" -F vermagic bcachefs) || die "no bcachefs module for $KVER"
[[ $vermagic == "$KVER "* ]] || die "the bcachefs module is for ${vermagic%% *}, not $KVER"
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
# apt's caches, package lists and AppStream's catalog are just caches: drop
# the layer's copies so the base's show through unchanged.
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
rm -rf --one-file-system "$work"
