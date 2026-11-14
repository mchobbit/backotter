#!/bin/bash
# Proxmox VE 9: the first-boot script of BackOtter's test guests proxmox9
# and proxmox9-mirror.  make-proxmox.sh embeds it in the automated
# installer's ISO (proxmox-auto-install-assistant prepare-iso
# --on-first-boot), and the answer file's [first-boot] (source "from-iso",
# ordering "fully-up") has the installed system run it once, as root, from
# proxmox-first-boot-fully-up.service, when the system is up and online.
# The prepared ISO is named by this file's SHA-256: change it and
# make-proxmox.sh prepares a new one.  No secrets here: the root password
# (hashed) and SSH key come from the answer file.
#
# It:
#   - points apt at Proxmox's free pve-no-subscription repository
#     (proxmox.sources, deb822), and disables every source on
#     enterprise.proxmox.com (pve-enterprise.sources, and Ceph's enterprise
#     repository in ceph.sources), which answer 401 without a subscription;
#     then apt-get update must work;
#   - appends console=ttyS0,115200n8 to /etc/kernel/cmdline and runs
#     proxmox-boot-tool refresh, so the boot entries on every ESP get it;
#   - adds the user "tester", with root's password (its hash, copied);
#   - makes rpool/data/vm-100-disk-0 (a 64 MiB zvol, as a VM's disk) and
#     rpool/data/subvol-101-disk-0 (a file system dataset with a few files,
#     as an unprivileged container's root, with the properties Proxmox gives
#     one: acltype=posixacl, xattr=sa, a refquota);
#   - prints BACKOTTER-INSTALL-OK (or BACKOTTER-INSTALL-FAILED and why) on
#     the serial console, /dev/ttyS0, and powers off 15 s after it exits, so
#     the service finishes first (make-proxmox.sh waits for QEMU to exit).
set -euo pipefail

say() { echo "bko-first-boot: $*"; echo "bko-first-boot: $*" >/dev/ttyS0 2>/dev/null || true; }
done_ok=no
finish() {
    local rc=$?
    if [[ $done_ok == yes ]]; then
        say "BACKOTTER-INSTALL-OK"
    else
        say "BACKOTTER-INSTALL-FAILED (exit $rc, line ${failed_line:-?})"
    fi
    systemd-run --quiet --on-active=15s /bin/systemctl poweroff || true
}
trap 'failed_line=$LINENO' ERR
trap finish EXIT

say "started ($*)"

# --- apt: pve-no-subscription, no enterprise repositories --------------------------
. /etc/os-release
codename=${VERSION_CODENAME:?no VERSION_CODENAME in /etc/os-release}
shopt -s nullglob
for f in /etc/apt/sources.list.d/*.sources; do
    grep -q 'enterprise\.proxmox\.com' "$f" || continue
    # Every stanza of the file: Enabled: no ahead of its Types:.
    sed -i -e '/^Enabled:/d' -e 's/^Types:/Enabled: no\nTypes:/' "$f"
    say "disabled $f"
done
for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do
    [[ -f $f ]] && grep -q '^[^#]*enterprise\.proxmox\.com' "$f" || continue
    sed -i 's/^\([^#]*enterprise\.proxmox\.com\)/# \1/' "$f"
    say "commented out the enterprise lines of $f"
done
cat >/etc/apt/sources.list.d/proxmox.sources <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: $codename
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
say "wrote /etc/apt/sources.list.d/proxmox.sources (pve-no-subscription, $codename)"
apt-get -q update
say "apt-get update ok"

# --- The serial console on every boot entry -------------------------------------------
if ! grep -qw 'console=ttyS0,115200n8' /etc/kernel/cmdline; then
    sed -i '1s/[[:space:]]*$/ console=ttyS0,115200n8/' /etc/kernel/cmdline
fi
# make-proxmox.sh boots the installer with QEMU's -kernel/-initrd, whose
# EFI loader adds "initrd=initrd" to the command line; the installer copies
# its console words, and that one, into /etc/kernel/cmdline.  A real
# install's has no initrd= (the entries name their own), so drop it, and
# the doubled space it leaves.
sed -i -e '1s/[[:space:]]*initrd=initrd\b//g' -e '1s/  */ /g' /etc/kernel/cmdline
say "/etc/kernel/cmdline: $(cat /etc/kernel/cmdline)"
proxmox-boot-tool refresh
say "proxmox-boot-tool refresh ok"

# --- tester ----------------------------------------------------------------------------
id tester >/dev/null 2>&1 || useradd -m -s /bin/bash -c "BackOtter tester" tester
usermod -p "$(getent shadow root | cut -d: -f2)" tester
say "user tester, with root's password"

# --- rpool/data: a VM disk and a container's root --------------------------------------
zfs list rpool/data/vm-100-disk-0 >/dev/null 2>&1 || zfs create -V 64M rpool/data/vm-100-disk-0
zfs list rpool/data/subvol-101-disk-0 >/dev/null 2>&1 ||
    zfs create -o acltype=posixacl -o xattr=sa -o refquota=1G rpool/data/subvol-101-disk-0
udevadm settle
# Something on the VM disk: a label in its first MiB.
printf 'BackOtter test VM 100 disk\n' | dd of=/dev/zvol/rpool/data/vm-100-disk-0 conv=notrunc status=none
ct=/rpool/data/subvol-101-disk-0
mkdir -p "$ct/etc" "$ct/root" "$ct/var/log"
echo ct101 >"$ct/etc/hostname"
printf 'BackOtter test container 101\n' >"$ct/root/README"
head -c 1048576 /dev/urandom >"$ct/var/log/random.bin"
ln -sfn ../root/README "$ct/etc/motd"
# An unprivileged container's files belong to its mapped ids (100000+).
chown -R 100000:100000 "$ct/etc" "$ct/root" "$ct/var"
say "rpool/data/vm-100-disk-0 and rpool/data/subvol-101-disk-0"
sync
done_ok=yes
