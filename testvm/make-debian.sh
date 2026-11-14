#!/usr/bin/env bash
# Build a Debian (or Devuan, or Kali) test guest for BackOtter: an unattended
# install from an official ISO into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-debian.sh [--devuan [--init sysvinit|openrc|runit] | --kali] VERSION ROOT_FS WORK_DIR
#   e.g. ./make-debian.sh 12.0.0 ext4 vms/debian12
#        ./make-debian.sh 13.0.0 btrfs vms/debian13
#        ./make-debian.sh --devuan --init openrc 6.1.1 ext3 vms/devuan6
#        ./make-debian.sh --kali 2026.3 ext4 vms/kali
#
# Debian: VERSION is a point release in https://cdimage.debian.org/cdimage/archive/,
# installed from its DVD-1.  Devuan (Debian without systemd, the same
# installer): VERSION is a release in https://files.devuan.org/, installed
# from its "server" ISO, with the init system --init names (default sysvinit).
# Kali (Debian testing with Kali's own archive, the same installer): VERSION
# is a release in https://cdimage.kali.org/ (e.g. 2026.3), installed from its
# netinst ISO with Kali's mirror, so the guest gets kali-rolling as it is on
# the day it's built; docs/design/kali.md is the design.
# ROOT_FS is ext3, ext4, btrfs, xfs or jfs (Kali: ext4 only).  The ISO and
# its signed checksum list are downloaded into WORK_DIR unless already there,
# and checked against the project's signature before use.  Results, in
# WORK_DIR (N is the major version, e.g. 12):
#   debianN.qcow2 / devuanN.qcow2 / kali.qcow2
#                         the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600); Kali's user tester
#                         has the same one
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#   vm-run-options        Kali only: "--uefi --no-secure-boot"
#
# The guest, host name debN or devuanN: BIOS + MBR, /boot (1 GB ext4),
# swap (2 GB), / (ROOT_FS, the rest), 2 GB RAM, no desktop; see
# debian/preseed.cfg.  Kali's, host name kali: UEFI with Secure Boot off
# (Kali's kernel and GRUB are unsigned, and it installs no shim), GPT with
# the installer's guided "all files in one partition" layout (ESP, ext4 /,
# swap), kali-linux-core and the standard task, no desktop.  Run it with
# vm-run, and look inside it while it's off with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, cpio, gpg, gpgv,
# openssl, wget; OVMF (/usr/share/OVMF) for --kali.  Runs as an ordinary
# user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
# "Debian CD signing key <debian-cd@lists.debian.org>", per
# https://www.debian.org/CD/verify
DEBIAN_CD_KEY=DF9B9C49EAA9298432589D76DA87E80D6294BE9B
# Devuan's ISO checksums are signed by a release developer; the keys are
# listed at https://www.devuan.org/os/keyring.
declare -A DEVUAN_KEYS=(
    [6]=185E56E98DA03B6CEADAC81983161D4768BE620B   # tempforever, signs Excalibur's ISOs
)
declare -A DEVUAN_CODENAMES=([5]=daedalus [6]=excalibur)
# "Kali Linux Archive Automatic Signing Key (2025)", per
# https://www.kali.org/docs/introduction/download-official-kali-linux-images/;
# it signs the release directories' SHA256SUMS too.  Expires 2028-04-17.
KALI_KEY=827C8569F2518CC677FECA1AED65462EC8D5E4C5
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-debian: $*" >&2; exit 1; }

distro=debian init=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --devuan|--kali)
            [[ $distro == debian ]] || die "--devuan and --kali exclude each other"
            distro=${1#--}; shift ;;
        --init) init=$2; shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 3 ]] || die "usage: $0 [--devuan [--init sysvinit|openrc|runit] | --kali] VERSION ROOT_FS WORK_DIR"
version=$1 root_fs=$2
[[ $version =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || die "VERSION looks like 13.0.0, 6.1.1 or 2026.3, not '$version'"
case $root_fs in ext3|ext4|btrfs|xfs|jfs) ;; *) die "ROOT_FS must be ext3, ext4, btrfs, xfs or jfs" ;; esac
# Kali's guest takes the installer's own guided layout (the atomic recipe),
# whose / is always ext4.
[[ $distro != kali || $root_fs == ext4 ]] || die "--kali installs Kali's guided layout, whose / is ext4: ROOT_FS must be ext4"
[[ -z $init || $distro == devuan ]] || die "--init needs --devuan (Debian and Kali install systemd)"
case ${init:-sysvinit} in sysvinit|openrc|runit) ;; *) die "--init must be sysvinit, openrc or runit" ;; esac
major=${version%%.*}

if [[ $distro == debian ]]; then
    ISO=debian-$version-amd64-DVD-1.iso
    SUMS=SHA256SUMS SIG=SHA256SUMS.sign
    url=https://cdimage.debian.org/cdimage/archive/$version/amd64/iso-dvd
    key_fpr=$DEBIAN_CD_KEY keyserver=hkps://keyring.debian.org
    kernel=/install.amd/vmlinuz initrd=/install.amd/initrd.gz
    guest_name=deb$major DISK=debian$major.qcow2
elif [[ $distro == devuan ]]; then
    codename=${DEVUAN_CODENAMES[$major]:-}
    key_fpr=${DEVUAN_KEYS[$major]:-}
    [[ -n $codename && -n $key_fpr ]] || die "Devuan $major unknown; add it to DEVUAN_CODENAMES and DEVUAN_KEYS"
    ISO=devuan_${codename}_${version}_amd64_server.iso
    SUMS=SHA256SUMS.txt SIG=SHA256SUMS.txt.asc
    url=https://files.devuan.org/devuan_$codename/installer-iso
    keyserver=hkps://keyserver.ubuntu.com
    kernel=/boot/isolinux/linux initrd=/boot/isolinux/initrd.gz
    guest_name=devuan$major DISK=devuan$major.qcow2
else
    # A fixed release directory, not current/, so a rebuild gets the same
    # installer.  The guest's names carry no version: kali-rolling moves on.
    ISO=kali-linux-$version-installer-netinst-amd64.iso
    SUMS=SHA256SUMS SIG=SHA256SUMS.gpg
    url=https://cdimage.kali.org/kali-$version
    key_fpr=$KALI_KEY keyserver=hkps://keys.openpgp.org
    kernel=/install.amd/vmlinuz initrd=/install.amd/initrd.gz
    guest_name=kali DISK=kali.qcow2
    for f in OVMF_CODE_4M.fd OVMF_VARS_4M.fd; do
        [[ -s /usr/share/OVMF/$f ]] || die "--kali needs /usr/share/OVMF/$f (ovmf)"
    done
fi
mkdir -p "$3"
cd "$3"
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
for f in "$SUMS" "$SIG" "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
keyring=$distro-signing-key.gpg
if [[ ! -s $keyring ]]; then
    # Fetched by full fingerprint, so the keyserver can't substitute a key.
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver "$keyserver" --recv-keys "$key_fpr"
    GNUPGHOME=$gnupg gpg -q --export "$key_fpr" >"$keyring"
    rm -rf "$gnupg"
fi
gpgv --keyring "./$keyring" "$SIG" "$SUMS" 2>/dev/null || die "$SUMS is not signed by $key_fpr"
grep -E "^[0-9a-f]{64} [ *]?$ISO\$" "$SUMS" | sha256sum -c --quiet - || die "$ISO does not match $SUMS"

# --- Installer kernel and initrd, with the preseed added ------------------------
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
xorriso -osirrox on -indev "$ISO" -extract "$kernel" "$work/vmlinuz" \
    -extract "$initrd" "$work/initrd.gz" 2>/dev/null || die "$ISO lacks $kernel or $initrd"
chmod u+w "$work"/*

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

mkdir "$work/add"
template=$here/debian/preseed.cfg
if [[ $distro == kali ]]; then
    # Kali's answers go after the template's (d-i keeps the last answer for
    # each question), but the template's disk recipe has to go: an
    # expert_recipe wins over choose_recipe whatever comes later.  Then
    # Kali's profile preseed, read from the CD after this one, has the last
    # word over both; see the comments in the block below.
    sed -e '/^# The disk: /,/^d-i partman-auto\/choose_recipe select backotter$/d' \
        "$template" >"$work/kali-preseed.cfg"
    if grep -qE 'expert_recipe|select backotter' "$work/kali-preseed.cfg" ||
        ! grep -q '^d-i partman-auto/purge_lvm_from_device ' "$work/kali-preseed.cfg"; then
        die "debian/preseed.cfg's disk section changed; fix the Kali arm's sed"
    fi
    cat >>"$work/kali-preseed.cfg" <<'EOF'

# --- Kali (make-debian.sh --kali) ---------------------------------------------
# Kali's profile preseed (/cdrom/simple-cdd/kali.preseed) comes after this
# file and sets, among others, the same mirror, pkgsel/upgrade full-upgrade
# and passwd/root-login false; none of the answers below depends on its.

# The netinst ISO holds little more than the installer: everything else
# comes from Kali's mirror.  (Kali's initrd has a /preseed.cfg of its own
# with these answers; this file replaces it.)
d-i apt-setup/use_mirror boolean true
d-i mirror/country string manual
d-i mirror/http/hostname string http.kali.org
d-i mirror/http/directory string /kali
d-i mirror/http/proxy string
d-i mirror/suite string kali-rolling
d-i mirror/codename string kali-rolling
d-i apt-setup/contrib boolean true
d-i apt-setup/non-free boolean true
d-i apt-setup/non-free-firmware boolean true

# The disk: the installer's guided default, "use entire disk, all files in
# one partition", on GPT; under UEFI that's an ESP, ext4 / and swap.
d-i partman-auto/disk string /dev/vda
d-i partman-auto/method string regular
d-i partman-partitioning/default_label string gpt
d-i partman-auto/choose_recipe select atomic

# Kali's tasksel installs its core task (kali-linux-core) always; add the
# standard task and nothing else: no desktop, firmware or tool
# metapackages.  Kali has no ssh-server task.
tasksel tasksel/first multiselect standard
d-i pkgsel/include string openssh-server

# With root login off (Kali's profile) the installer locks root and insists
# on a user: tester, with root's password.  late_command gives root its
# password back.
d-i passwd/make-user boolean true
d-i passwd/user-fullname string tester
d-i passwd/username string tester
d-i passwd/user-password-crypted password @ROOT_PASSWORD_HASH@

# vm-run boots every UEFI guest with a fresh OVMF variable store, so no
# NVRAM entry survives the install: GRUB goes to the removable path
# EFI/BOOT/BOOTX64.EFI too.  (The template's grub-installer/bootdev is
# ignored on EFI.)
d-i grub-installer/force-efi-extra-removable boolean true

# The template's late command, plus root's password, and ssh enabled
# outright: Kali's policy leaves network services off by default.
d-i preseed/late_command string \
    mkdir -p -m 700 /target/root/.ssh; \
    cp /authorized_keys /target/root/.ssh/authorized_keys; \
    chmod 600 /target/root/.ssh/authorized_keys; \
    in-target usermod -p '@ROOT_PASSWORD_HASH@' root; \
    in-target systemctl enable ssh.service
EOF
    template=$work/kali-preseed.cfg
fi
sed -e "s|@ROOT_PASSWORD_HASH@|$hash|" -e "s|@HOSTNAME@|$guest_name|g" -e "s|@ROOT_FS@|$root_fs|g" \
    "$template" >"$work/add/preseed.cfg"
if [[ $distro == devuan ]]; then
    # Devuan's installer asks which init system to install.  Its server ISO
    # lacks openssh-server, so it also needs Devuan's mirror; later answers
    # override the template's "no mirror".  (Devuan keeps no dated snapshots,
    # so the guest gets the packages current on the day it's built.)
    cat >>"$work/add/preseed.cfg" <<EOF
d-i choose-init/select_init select ${init:-sysvinit}
d-i apt-setup/use_mirror boolean true
d-i mirror/country string manual
d-i mirror/http/hostname string deb.devuan.org
d-i mirror/http/directory string /merged
d-i mirror/http/proxy string
d-i pkgsel/include string openssh-server
EOF
fi
if grep -n '@[A-Z_]\+@' "$work/add/preseed.cfg"; then die "preseed placeholders left unfilled"; fi
cp id_ed25519.pub "$work/add/authorized_keys"
# A second gzipped cpio archive appended to the initrd is unpacked on top of
# the first; the installer reads /preseed.cfg from it automatically.
(cd "$work/add" && printf '%s\n' preseed.cfg authorized_keys |
    cpio -o -H newc --quiet | gzip -9) >>"$work/initrd.gz"

# --- Install ----------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $3/install-serial.log"
# DEBIAN_FRONTEND=text: a plain-text installer, readable in the serial log.
# Arguments after "---" are kept on the installed system's kernel command
# line, so it has a serial console too.
installer_args="auto=true priority=critical DEBIAN_FRONTEND=text console=ttyS0,115200n8"
if [[ $distro == kali ]]; then
    # UEFI without Secure Boot, as vm-run --uefi --no-secure-boot boots it:
    # q35 and OVMF's code without SMM, with a variable store of the
    # install's own.
    cp /usr/share/OVMF/OVMF_VARS_4M.fd "$work/vars.fd"
    firmware=(-machine q35
        -drive "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd"
        -drive "if=pflash,format=raw,unit=1,file=$work/vars.fd")
    # What Kali's own boot menu passes, so its profile preseed and
    # kali.postinst run as on a real install, less desktop= (tasksel is
    # answered) and plus nozsh: kali.postinst then leaves root's shell bash,
    # which the farm's ssh harness expects.
    installer_args+=" net.ifnames=0 preseed/file=/cdrom/simple-cdd/default.preseed simple-cdd/profiles=kali nozsh"
else
    firmware=(-machine pc)
fi
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${firmware[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -nic user,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initrd.gz" \
    -append "$installer_args --- console=tty0 console=ttyS0,115200n8" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
# The installer only powers off at the very end (a failed step waits for an
# answer instead, until the timeout), but check that a system was written.
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 500000000 ]] ||
    die "the disk is nearly empty; see install-serial.log"

qemu-img snapshot -c fresh-install "$DISK"
# How vm-run (and compare.py, ht1.sh, rebuild-one.sh) must boot it.
if [[ $distro == kali ]]; then echo "--uefi --no-secure-boot" >vm-run-options; fi
echo "Done: $3/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
