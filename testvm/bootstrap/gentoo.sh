# Gentoo Linux: the stage3 make-bootstrap.sh checked against Gentoo's
# Release Engineering key, made bootable the way the handbook does it and
# the BackShot1 Thinkpad was (docs/design/gentoo.md): Portage's tree from
# emerge-webrsync, binary packages from the 23.0 binhost, gentoo-kernel-bin
# installed by installkernel[dracut,grub] (dracut makes the initramfs,
# grub-mkconfig the menu), GRUB in the ESP at /efi under the id gentoo,
# OpenRC services.  glibc, profile default/linux/amd64/23.0, merged usr.
# LAYOUT efi-lvm (gentoo): UEFI, GPT, a 1 GiB ESP at /efi, a swap
# partition, LVM vg0 with / on ext4 and /home on XFS.
# GENTOO_INIT=systemd (gentoo-systemd, LAYOUT luks-esp): the systemd stage3
# (profile .../23.0/systemd), the ESP as /boot, LUKS2 holding vg0 (root
# ext4, swap), opened at boot by systemd's dracut (its crypt module and
# systemd-cryptsetup, so sys-apps/systemd with USE=cryptsetup, a source
# build) with the key file dracut copies into the initramfs and
# rd.luks.uuid on GRUB's command line; installkernel's layout pinned to
# grub in /etc/kernel/install.conf; systemd-networkd and -resolved.
#
# Every input is checked: the stage3 by make-bootstrap.sh (gpgv), the tree's
# snapshot by emerge-webrsync (the gentoo-release key the stage3 ships), the
# binary packages by Portage (binpkg-request-signature, against getuto's
# keyring).  Only SOURCE_OK may be built from source: `emerge -p` first, and
# any other "[ebuild" line stops the build, by name, before anything is
# merged.  update/in-guest.sh's Linux.portage section has the same list
# (test2/tests/test_vm_update.py checks that they agree).
#
# The stage3 is unpacked with bsdtar (libarchive, on the Arch ISO; it reads
# the xz itself), as bootstrap/chimera.sh and void.sh do: it keeps any
# extended attributes, as the handbook's tar --xattrs-include='*.*' does.
# --numeric-owner: Gentoo's IDs, not the ISO's names for them.
# ping's privilege (ruled 2026-11-12 by the user: cap_net_raw or setuid
# root): Gentoo's iputils is USE=suid, as the Thinkpad's binary package, so
# ping is setuid root (4755) with no file capability; a later update may
# give it cap_net_raw instead (USE=filecaps).  ping_privilege fails the
# build, in the final checks, if ping has neither, and the facts say which.
#
# The chroot: arch-chroot's file systems, mounted once for the whole
# install (api_mounts: new proc, sysfs, devtmpfs, devpts and shm, and the
# live system's /run bound private, whose udev database dracut's host-only
# detection reads), and plain chroot.  Not arch-chroot itself: getuto's
# gpg --lsign-key of a subkey failed under it.  Not the handbook's binds
# (rslave /dev and /sys, a slave /run) either: with them, vg0/root was still
# in use after umount -R, and vgchange -a n failed.  The initramfs is
# checked for dracut's lvm module (and, over LUKS, its crypt module,
# systemd-cryptsetup and the key file).

# The packages Gentoo's binhost has no build of with this guest's USE
# flags (and gentoo-kernel-bin's ebuild, which installs a prebuilt kernel,
# and its virtual): the source builds the Thinkpad's install made.  And
# app-doc/info-manual, whose 31.1 the binhost lagged behind the tree on
# 2026-11-12 (vm-update --try's first run stopped on it).
# And sys-apps/systemd, for gentoo-systemd: the binhost's has no
# USE=cryptsetup; and net-libs/nghttp2, which curl's USE=http2 pulled in
# on 2026-11-12 and the binhost has only with USE=-systemd (gentoo-systemd's
# first vm-update --try stopped on it); and net-misc/rsync, whose 3.5.2-r1
# the binhost lagged behind the tree on, the same day (its second).
SOURCE_OK="sys-fs/lvm2 sys-boot/grub sys-kernel/dracut sys-kernel/installkernel sys-kernel/gentoo-kernel-bin virtual/dist-kernel net-dns/avahi app-doc/info-manual sys-apps/systemd net-libs/nghttp2 net-misc/rsync"
GENTOO_INIT='@GENTOO_INIT@'            # openrc (gentoo) or systemd (gentoo-systemd)
# openrc: the Thinkpad's world set, less its firmware and microcode (a VM
# has neither) and sudo; openssh comes with the stage3.  systemd: the boot
# stack over LUKS, no XFS, and systemd's own network services.
if [[ $GENTOO_INIT == openrc ]]; then
    PACKAGES="sys-fs/lvm2 sys-fs/xfsprogs sys-fs/dosfstools sys-boot/grub sys-boot/efibootmgr net-misc/dhcpcd net-dns/avahi sys-kernel/installkernel"
else
    PACKAGES="sys-fs/lvm2 sys-fs/cryptsetup sys-fs/dosfstools sys-boot/grub sys-boot/efibootmgr sys-kernel/installkernel"
fi
KERNEL=sys-kernel/gentoo-kernel-bin

mkfs_root "$ROOT_PART"
mount -t "$ROOT_FS" "$ROOT_PART" /mnt
mount_boot
curl -fsS "$SERVER/rootfs.tar.xz" | bsdtar -xpf - --numeric-owner -C /mnt
grep -q "^ID='\?gentoo'\?$" /mnt/etc/os-release || { echo "/etc/os-release doesn't say Gentoo"; false; }
case $GENTOO_INIT in
openrc) [[ -x /mnt/sbin/openrc ]] || { echo "the stage3 has no OpenRC"; false; } ;;
systemd) [[ -x /mnt/usr/lib/systemd/systemd ]] || { echo "the stage3 has no systemd"; false; } ;;
*) echo "GENTOO_INIT=$GENTOO_INIT: openrc or systemd"; false ;;
esac
cp -L /etc/resolv.conf /mnt/etc/resolv.conf

api_mounts() {
    mount -t proc -o nosuid,noexec,nodev proc /mnt/proc
    mount -t sysfs -o nosuid,noexec,nodev,ro sys /mnt/sys
    mount -t devtmpfs -o mode=0755,nosuid udev /mnt/dev
    mount -t devpts -o mode=0620,gid=5,nosuid,noexec devpts /mnt/dev/pts
    mount -t tmpfs -o mode=1777,nosuid,nodev shm /mnt/dev/shm
    mount --bind --make-private /run /mnt/run
}
api_mounts
# The chroot's own environment, not the live system's.  EMERGE_DEFAULT_OPTS
# only here: the guest's make.conf stays as the Thinkpad's.
in_gentoo() {
    chroot /mnt /usr/bin/env -i HOME=/root TERM=dumb LC_ALL=C.UTF-8 \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        EMERGE_DEFAULT_OPTS="--color=n --nospinner --quiet-build=y" "$@"
}

# The Thinkpad's make.conf additions (MAKEOPTS for this VM's 4 CPUs) and
# package.use.
cat >>/mnt/etc/portage/make.conf <<'EOF'

# BackOtter's gentoo test guest (testvm/bootstrap/gentoo.sh), as the
# BackShot1 Thinkpad: binary packages first, UEFI.
MAKEOPTS="-j4 -l4"
FEATURES="${FEATURES} getbinpkg binpkg-request-signature"
ACCEPT_LICENSE="-* @FREE @BINARY-REDISTRIBUTABLE"
GRUB_PLATFORMS="efi-64"
EOF
mkdir -p /mnt/etc/portage/package.use
cat >/mnt/etc/portage/package.use/gentoo-guest <<'EOF'
sys-kernel/installkernel dracut grub
net-dns/avahi -gtk -qt5 -qt6 -python -mono -introspection
sys-fs/lvm2 lvm
EOF
[[ $GENTOO_INIT == openrc ]] || echo 'sys-apps/systemd cryptsetup' >>/mnt/etc/portage/package.use/gentoo-guest

in_gentoo emerge-webrsync
echo "BACKOTTER-GENTOO-TREE: $(cat /mnt/var/db/repos/gentoo/metadata/timestamp.chk)"
in_gentoo getuto

# source_builds: CATEGORY/NAME of each "[ebuild" line of emerge -p's plan on
# stdin (the version is the last -VERSION[-rN] of the name).
source_builds() {
    sed -n 's/^\[ebuild[^]]*\] *\([^ ]*\).*/\1/p' | sed -E 's/:.*//; s/-[0-9]+(\.[0-9]+)*[a-z]?(_(alpha|beta|pre|rc|p)[0-9]*)*(-r[0-9]+)?$//'
}
# plan_ok EMERGE-ARGS...: emerge -p's plan of them has binary packages and
# no source build outside SOURCE_OK.
plan_ok() {
    in_gentoo emerge -p "$@" >/tmp/plan
    cat /tmp/plan
    grep -q '^\[binary' /tmp/plan || { echo "emerge -p $* plans no binary package: no binhost?"; false; }
    bad=$(source_builds </tmp/plan | grep -vxF -f <(printf '%s\n' $SOURCE_OK) || true)
    [[ -z $bad ]] || { echo "source builds outside SOURCE_OK:" $bad; false; }
}
if [[ $GENTOO_INIT == systemd ]]; then
    # systemd again, with USE=cryptsetup (the stage3's has none).
    plan_ok --oneshot --newuse sys-apps/systemd
    in_gentoo emerge --oneshot --newuse sys-apps/systemd
fi
plan_ok --noreplace $PACKAGES $KERNEL

in_gentoo emerge --noreplace $PACKAGES

# fstab as the Thinkpad's (by UUID, noatime, the ESP root's only).  An
# ESP as /boot holds the initramfs, and with it the LUKS key: FAT has no
# modes of its own, so umask=0077 keeps it root's only there too.
uuid() { blkid -s UUID -o value "$1"; }
if [[ $LAYOUT == efi-lvm ]]; then
    cat >/mnt/etc/fstab <<EOF
UUID=$(uuid "$ESP_PART")  /efi   vfat  umask=0077          0 2
UUID=$(uuid "$SWAP_PART")  none   swap  sw                  0 0
UUID=$(uuid "$ROOT_PART")  /      $ROOT_FS  defaults,noatime    0 1
UUID=$(uuid "$HOME_PART")  /home  xfs   defaults,noatime    0 2
EOF
else
    cat >/mnt/etc/fstab <<EOF
UUID=$(uuid "$ESP_PART")  /boot  vfat  umask=0077          0 2
UUID=$(uuid "$SWAP_PART")  none   swap  sw                  0 0
UUID=$(uuid "$ROOT_PART")  /      $ROOT_FS  defaults,noatime    0 1
EOF
fi
# LUKS: crypttab names the key file, which dracut copies into the
# initramfs (install_items: its host-only crypttab is the one for /, the
# key file a file of the host's like any other), where systemd-cryptsetup
# opens / with it.  rd.luks.uuid on the command line names the volume;
# crypttab's name for it, cryptroot, wins.
LUKS_CMDLINE=""
if [[ -n $LUKS_PART ]]; then
    install -d -m 700 /mnt/etc/cryptsetup-keys.d
    (umask 0377; printf '%s' "$LUKS_KEY" >/mnt/etc/cryptsetup-keys.d/cryptroot.key)
    printf 'cryptroot\tUUID=%s\t/etc/cryptsetup-keys.d/cryptroot.key\tluks,discard\n' "$(uuid "$LUKS_PART")" >/mnt/etc/crypttab
    mkdir -p /mnt/etc/dracut.conf.d
    echo 'install_items+=" /etc/cryptsetup-keys.d/cryptroot.key "' >/mnt/etc/dracut.conf.d/10-backotter-luks.conf
    LUKS_CMDLINE=" rd.luks.uuid=$(uuid "$LUKS_PART")"
fi
echo "$GUEST_NAME" >/mnt/etc/hostname
[[ $GENTOO_INIT != openrc ]] || sed -i "s/^hostname=.*/hostname=\"$GUEST_NAME\"/" /mnt/etc/conf.d/hostname
printf '127.0.0.1\tlocalhost\n::1\t\tlocalhost\n127.0.1.1\t%s\n' "$GUEST_NAME" >/mnt/etc/hosts
ln -sf ../usr/share/zoneinfo/UTC /mnt/etc/localtime
install_ssh_key /mnt
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | in_gentoo chpasswd -e

if [[ $GENTOO_INIT == openrc ]]; then
    # OpenRC: the Thinkpad's services (lvm in boot, for lvm2[lvm]), and a
    # getty on the serial console.
    ln -sfn agetty /mnt/etc/init.d/agetty.ttyS0
    printf 'baud="115200"\nterm_type="vt100"\n' >/mnt/etc/conf.d/agetty.ttyS0
    in_gentoo rc-update add lvm boot
    for s in sshd dhcpcd dbus avahi-daemon agetty.ttyS0; do in_gentoo rc-update add $s default; done
else
    # systemd: sshd, DHCP on the wired NIC by networkd, DNS by resolved (its
    # stub resolv.conf, linked at the end); the serial getty comes from
    # console=ttyS0.  A machine-id now, so that the first boot isn't
    # systemd's first boot (systemd-firstboot's questions).
    mkdir -p /mnt/etc/systemd/network
    printf '[Match]\nName=en* eth*\n\n[Network]\nDHCP=yes\n' >/mnt/etc/systemd/network/50-wired.network
    in_gentoo systemd-machine-id-setup
    in_gentoo systemctl enable sshd.service systemd-networkd.service systemd-resolved.service
fi

# GRUB, then the kernel: installkernel runs dracut (host-only, as
# /usr/lib/dracut/dracut.conf.d/01-gentoo.conf has it) and grub-mkconfig.
# The install VM boots by BIOS: no EFI variables to write.  So GRUB under
# its own name, as the Thinkpad's, and again at the fallback path
# (EFI/BOOT/BOOTX64.EFI) that the firmware boots with no boot entries, as
# vm-run --uefi's does.
# gentoo-systemd pins installkernel's layout in /etc/kernel/install.conf
# (read instead of /usr/lib/kernel's, whole, by installkernel and by
# systemd's kernel-install alike).
cat >>/mnt/etc/default/grub <<EOF
GRUB_CMDLINE_LINUX="$SERIAL_CMDLINE$LUKS_CMDLINE"
GRUB_TIMEOUT=2
EOF
efi=/efi; [[ $LAYOUT == efi-lvm ]] || efi=/boot
if [[ $GENTOO_INIT == systemd ]]; then
    mkdir -p /mnt/etc/kernel
    printf 'layout=grub\ninitrd_generator=dracut\nuki_generator=none\n' >/mnt/etc/kernel/install.conf
fi
in_gentoo grub-install --target=x86_64-efi --efi-directory=$efi --bootloader-id=gentoo --no-nvram
in_gentoo grub-install --target=x86_64-efi --efi-directory=$efi --removable
in_gentoo emerge --noreplace $KERNEL
mods=(/mnt/lib/modules/*)
[[ ${#mods[@]} == 1 ]] || { echo "not one kernel: ${mods[*]}"; false; }
check_root_module "${mods[0]}"
kv=${mods[0]##*/}
# The kernel's name in /boot: installkernel's own script calls it
# vmlinuz-KV; under systemd's kernel-install, its 90-compat.install plugin
# (layout grub) calls it kernel-KV, which grub-mkconfig finds as well.  On
# an ESP that is /boot, grub.cfg's paths are the ESP's own (/kernel-KV).
image=vmlinuz; [[ $GENTOO_INIT == openrc ]] || image=kernel
for f in $image-$kv initramfs-$kv.img; do [[ -s /mnt/boot/$f ]] || { echo "no /boot/$f"; false; }; done
grep -Eq "^[[:space:]]*linux[[:space:]]+(/boot)?/$image-$kv " /mnt/boot/grub/grub.cfg || { echo "grub.cfg has no $kv"; false; }
# Not grep -q: lsinitrd, cut off by its exit, would fail the pipe.
in_gentoo lsinitrd -m "/boot/initramfs-$kv.img" >/tmp/modules
grep -x lvm /tmp/modules >/dev/null || { echo "initramfs-$kv.img has no lvm module"; false; }
if [[ -n $LUKS_PART ]]; then
    for m in crypt systemd; do
        grep -x $m /tmp/modules >/dev/null || { echo "initramfs-$kv.img has no $m module"; false; }
    done
    in_gentoo lsinitrd "/boot/initramfs-$kv.img" >/tmp/files
    for f in ' etc/cryptsetup-keys.d/cryptroot.key' ' etc/crypttab' '/systemd-cryptsetup'; do
        grep -E -- "$f( |$)" /tmp/files >/dev/null || { echo "initramfs-$kv.img has no${f/\// .../}"; false; }
    done
    grep -q "^[[:space:]]*linux[[:space:]].* rd\.luks\.uuid=$(uuid "$LUKS_PART")" /mnt/boot/grub/grub.cfg ||
        { echo "grub.cfg's linux line has no rd.luks.uuid"; false; }
    # Not dracut's own choice of path, /boot/MACHINE-ID/K/initrd, which it
    # takes when /boot/loader/entries and /boot/MACHINE-ID exist.
    [[ ! -e /mnt/boot/loader/entries && ! -e /mnt/boot/$(cat /mnt/etc/machine-id) ]] ||
        { echo "kernel-install made a BLS layout in /boot"; false; }
fi
# ping: cap_net_raw (getcap, the live system's: the Arch ISO has libcap),
# or setuid root (owner uid 0, setuid bit).  Prints which held.
ping_privilege() {
    local caps='' uid
    ! command -v getcap >/dev/null || caps=$(getcap "$1")
    uid=$(stat -c %u "$1") || return
    if [[ $caps == *cap_net_raw* ]]; then echo "cap_net_raw ($caps)"
    elif [[ $uid == 0 && -u $1 ]]; then echo "setuid root ($(stat -c '%a %U' "$1"))"
    else echo "$1 has neither cap_net_raw nor setuid root (${caps:-no file capability}; $(stat -c '%a uid %u' "$1"))" >&2; false
    fi
}
ping_held=$(ping_privilege /mnt/usr/bin/ping)

# What docs/design/gentoo.md's G1 settles, for the record.
echo "== facts"
echo "ping: $ping_held"
cat /mnt/usr/lib/kernel/install.conf /mnt/etc/kernel/install.conf 2>/dev/null | grep -v '^#' | grep . || true
ls -l /mnt/boot "/mnt/lib/modules/$kv" /mnt$efi/EFI/*
in_gentoo dracut --version
echo "source-built:"; for d in /mnt/var/db/pkg/*/*/; do [[ -e ${d}BINPKGMD5 ]] || echo "  ${d#/mnt/var/db/pkg/}"; done
ls "$(ls -d /mnt/var/db/pkg/sys-boot/grub-[0-9]*/)"
if [[ $GENTOO_INIT == openrc ]]; then
    in_gentoo rc-update show
else
    in_gentoo systemctl list-unit-files --state=enabled --no-pager
    grep -h CMDLINE /mnt/etc/default/grub
    in_gentoo systemctl --version | head -n 2
fi
echo "== end of facts"

rm -rf /mnt/var/cache/binpkgs/* /mnt/var/cache/distfiles/*
rm /mnt/etc/resolv.conf
[[ $GENTOO_INIT == openrc ]] || ln -s ../run/systemd/resolve/stub-resolv.conf /mnt/etc/resolv.conf
# Daemons the chroot left running (gpg's agents, from getuto and Portage's
# signature checks) would hold / open: this is the install VM's own live
# system, so they are stopped.
for p in /proc/[0-9]*; do
    if [[ $(readlink "$p/root" 2>/dev/null) == /mnt ]]; then
        echo "stopping $(cat "$p/comm" 2>/dev/null) (pid ${p#/proc/}), left running in the chroot"
        kill "${p#/proc/}" 2>/dev/null || true
    fi
done
sleep 1
umount /mnt/run /mnt/dev/shm /mnt/dev/pts /mnt/dev /mnt/sys /mnt/proc
finish
