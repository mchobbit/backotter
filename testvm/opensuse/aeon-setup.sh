#!/bin/bash
# openSUSE Aeon test guest for BackOtter: what make-opensuse.sh aeon sets up
# over SSH (as root, by `bash -s`) at the deployed guest's first boot, on top
# of what the installer's combustion script (combustion-aeon.sh) left in the
# copy tik made of it.  All of it in /etc (a writable subvolume of the
# booted snapshot's), /var, /home and the ESP: no transaction, so no new
# snapshot.  See docs/design/aeon.md.
set -euo pipefail

# A user, as Aeon's own setup would leave one, so that GNOME's first-run
# wizard has nothing to ask should the guest ever boot to GDM: no initial
# setup for the system (gdm's block file, which tik's sicu module removed)
# nor for the user.  No password: root's key logs in.
id bko >/dev/null 2>&1 || useradd -m -c 'BackOtter test user' -s /bin/bash bko
install -d -o bko -g bko -m 700 ~bko/.config
touch ~bko/.config/gnome-initial-setup-done
chown bko:bko ~bko/.config/gnome-initial-setup-done
install -d /var/lib/gdm
touch /var/lib/gdm/block-initial-setup

# The serial console as the system console (the last console=), where
# systemd and a LUKS prompt write, and the kernel's messages not quieted:
# the installer's command line (copied to /etc/kernel/cmdline) puts tty0
# last and is quiet.  sdbootutil rewrites every snapshot's entry from it,
# and updates the TPM's predictions for the new command line (PCR 9).
read -r -a words </etc/kernel/cmdline
keep=()
for w in "${words[@]}"; do
    case $w in
    quiet|loglevel=*|systemd.show_status=*|vt.global_cursor_default=*|console=*) ;;
    *) keep+=("$w") ;;
    esac
done
echo "console=tty0 console=ttyS0,115200 ${keep[*]}" >/etc/kernel/cmdline
echo "cmdline: $(cat /etc/kernel/cmdline)"
sdbootutil update-all-entries
systemctl set-default multi-user.target

# No default entry in NVRAM, as after a real install (tik's sdbootutil runs
# with --no-variables): the one there is the installer's own, left by
# combustion's snapshot 2 on the stick (aeon-...-2.conf), and would name the
# guest's first update's entry before sdbootutil ever chose it.
bootctl set-default ''

# The updates off (combustion-aeon.sh did it in the installer, and so here;
# again, should a first boot's preset-all have enabled them).
systemctl disable transactional-update.timer update-system-flatpaks.timer
systemctl --global disable update-user-flatpaks.timer
echo BACKOTTER-SETUP-OK
