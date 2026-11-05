#!/bin/sh
# Post-install for a BackOtter GhostBSD test guest: pc-sysinstall's runScript,
# run inside the new system, chrooted, after the live system was copied in.
# make-ghostbsd.sh fills in the placeholders.  It makes the copy of a live
# desktop a server: no desktop, DHCP, sshd, root's key.
set -e
# pc-sysinstall's TMPDIR, which isn't in the chroot: sysrc's mktemp fails.
TMPDIR=/tmp; export TMPDIR
# No /dev in the chroot, and pkg's scripts want one.  Unmounted again, or
# pc-sysinstall can't unmount the new system.
mount -t devfs devfs /dev
trap 'umount /dev' EXIT

# What Install Station's own configuration undoes of the live system: its
# user (which pc-sysinstall deletes too, for installType=GhostBSD), the
# installer starting at boot and from root's .xinitrc.  Setup Station
# (initial_setup, the first boot's user and network dialogs) stays off.
if grep -q '^ghostbsd:' /etc/passwd; then pw userdel -n ghostbsd -r; fi
rm -f /etc/rc.conf.d/install_station /etc/rc.conf.d/initial_setup /etc/rc.conf.d/xconfig \
	/root/.xinitrc
# GhostBSD's fix for fresh 26.2 installs (news, 2026-11-03): xconfig leaves
# the NVIDIA driver repository behind, active, and pkg then pulls NVIDIA's
# driver into installs on any hardware.
rm -rf /usr/local/etc/pkg/repos/XDrivers.conf /xdrivers

# No desktop: every package but the base system (GhostBSD-*), pkg and the
# repositories' configuration (ghostbsd-pkg-conf): 0.9 GB stay of 6.8.
# GhostBSD marks the desktop's metapackages vital, which pkg won't delete.
# One autoremove leaves orphans (gcc14, once openblas is gone): it runs
# while it removes something.
export ASSUME_ALWAYS_YES=yes
keep='^(GhostBSD-.*|pkg|ghostbsd-pkg-conf)$'
pkg set -y -A 0 $(pkg query %n | grep -E "$keep") >/dev/null
for p in $(pkg query -e '%V = 1' %n | grep -vE "$keep"); do pkg set -y -v 0 "$p" >/dev/null; done
pkg delete -y $(pkg query -e '%a = 0' %n | grep -vE "$keep") >/tmp/pkg-delete.log 2>&1 ||
	{ tail -n 20 /tmp/pkg-delete.log; exit 1; }
while pkg query -e '%a = 1' %n | grep -q .; do
	n=$(pkg info | wc -l)
	pkg autoremove -y >>/tmp/pkg-delete.log 2>&1 || { tail -n 20 /tmp/pkg-delete.log; exit 1; }
	[ "$(pkg info | wc -l)" -lt "$n" ] || break
done
echo "Packages left: $(pkg info | wc -l), of them not GhostBSD-*: $(pkg query %n | grep -v '^GhostBSD-' | tr '\n' ' ')"
# The users and groups the packages leave behind, as they say ("You should
# manually remove the "avahi" user", "use 'pw userdel cyrus'").
for u in $(sed -n -e 's/.*manually remove the "\(.*\)" user.*/\1/p' \
	-e "s/.*'pw userdel \([^']*\)'.*/\1/p" /tmp/pkg-delete.log); do
	pw userdel -n "$u" 2>/dev/null || true
done
for g in $(sed -n -e 's/.*manually remove the "\(.*\)" group.*/\1/p' \
	-e "s/.*'pw groupdel \([^']*\)'.*/\1/p" /tmp/pkg-delete.log); do
	pw groupdel -n "$g" 2>/dev/null || true
done
rm -f /tmp/pkg-delete.log
rm -rf /usr/local/etc/lightdm
sysrc -x lightdm_enable dbus_enable cupsd_enable avahi_daemon_enable avahi_dnsconfd_enable \
	webcamd_enable 2>/dev/null || true
# mdns (nss_mdns, now gone) took 5 s to give up on each address, and sshd's
# TCP wrappers look up every client's: longer than vm-lib.sh waits for a
# banner.
sed -i '' 's/^hosts:.*/hosts: files dns/' /etc/nsswitch.conf
# The freed room back to the image (the install's disk has discard=unmap).
# For ufs, install.sh does it once / is unmounted.
case @ROOT_FS@ in zfs) zpool trim -w zroot ;; esac

# GhostBSD's firewall (ipfw, "workstation") lets in only what
# firewall_myservices lists.
sysrc ifconfig_vtnet0=DHCP sshd_enable=YES firewall_myservices=22/tcp firewall_allowservices=any

echo '@ROOT_PASSWORD_HASH@' | pw usermod root -H 0
mkdir -p -m 700 /root/.ssh
echo '@AUTHORIZED_KEY@' >/root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
# FreeBSD's sshd refuses root by default; the first setting in the file wins.
printf '%s\n' '' '# BackOtter test guest' 'PermitRootLogin prohibit-password' >>/etc/ssh/sshd_config

# Loader and kernel on the serial console as well as the screen, so vm-run's
# terminal (and --serial-log) shows the boot and a login prompt.
cat >>/boot/loader.conf <<'EOF'
boot_multicons="YES"
console="comconsole,vidconsole"
autoboot_delay="3"
EOF
# pc-sysinstall goes on when this fails; install.sh looks for this line.
echo "BACKOTTER-POST-INSTALL: OK"
