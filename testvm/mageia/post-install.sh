# BackOtter's post-install for the mageia10 test guest: DrakX's postInstall,
# run by bash in the installed system's chroot at exitInstall, after the
# bootloader is written.  testvm/make-mageia.sh fills in the at-sign
# placeholders and puts it in the profile (mageia/auto_inst.cfg.pl.in).
# Everything else is DrakX's own.  The DVD's urpmi medium gives way to the
# online ones at the first boot (make-mageia.sh), where there is a network.
exec >/root/drakx/bko-post-install.log 2>&1
set -x
fail() { echo "BACKOTTER-INSTALL-FAILED: $*"; echo "BACKOTTER-INSTALL-FAILED: $*" >/dev/ttyS0; exit 1; }

# root logs in with the build's key.
install -d -m 700 /root/.ssh || fail "/root/.ssh"
echo '@AUTHORIZED_KEY@' >/root/.ssh/authorized_keys || fail "authorized_keys"
chmod 600 /root/.ssh/authorized_keys

# The serial console, and a text boot: no splash or quiet, GRUB on the
# console and serial, the kernel's console on ttyS0 too, multi-user.target.
f=/etc/default/grub
[ -f $f ] || fail "no $f"
perl -pi -e 'if (/^GRUB_CMDLINE_LINUX(_DEFAULT)?=/) { s/\b(splash|quiet)\b//g; s/  +/ /g; s/=" /="/; s/ "$/"/ }' $f || fail "$f"
grep -q '^GRUB_CMDLINE_LINUX=' $f || echo 'GRUB_CMDLINE_LINUX=""' >>$f
grep -q '^GRUB_CMDLINE_LINUX=.*ttyS0' $f ||
    perl -pi -e 's/^GRUB_CMDLINE_LINUX="(.*)"$/GRUB_CMDLINE_LINUX="$1 console=tty0 console=ttyS0,115200n8"/; s/^GRUB_CMDLINE_LINUX=" /GRUB_CMDLINE_LINUX="/' $f
sed -i -e '/^GRUB_TERMINAL/d' -e '/^GRUB_SERIAL_COMMAND/d' $f
printf '%s\n' 'GRUB_TERMINAL="console serial"' 'GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"' >>$f
cat $f
grub2-mkconfig -o /boot/grub2/grub.cfg || fail "grub2-mkconfig"
systemctl set-default multi-user.target || fail "set-default"

# What the install left, for "What M2 found".
cat /etc/os-release; ls -la /boot /boot/grub2 /root/drakx; cat /boot/grub2/install.sh
find /boot/EFI -ls; efibootmgr -v; cat /etc/fstab; cat /etc/urpmi/urpmi.cfg
ls /etc/sysconfig/network-scripts; cat /etc/sysconfig/network-scripts/ifcfg-*; cat /etc/hostname
cat /etc/shorewall/interfaces /etc/shorewall/rules.drakx 2>/dev/null; grep -n PermitRootLogin /etc/ssh/sshd_config
rpm -qa | wc -l

echo BACKOTTER-INSTALL-OK
echo BACKOTTER-INSTALL-OK >/dev/ttyS0 || :
