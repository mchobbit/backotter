#!/bin/bash
# Vanilla OS 3: a test guest for BackOtter, installed from Vanilla's own
# live ISO the way its GUI installer (vanilla-installer) does an automatic
# install on a blank disk, unattended.  Run by make-vanilla.sh as root in the
# live system, by the unit bko-install.service that make-vanilla.sh hands
# the live systemd as an SMBIOS credential, from the second CD (BKOINST),
# after make-vanilla.sh filled in the @...@ settings.  Its output goes to
# the second serial port (make-vanilla.sh's install.log).  Prints
# BACKOTTER-INSTALL-OK or -FAILED; the unit then powers the VM off.
#
# What the GUI does: Processor.gen_install_recipe(log_path, finals,
# sys_recipe) (vanilla_installer/utils/processor.py) turns the answers of
# its pages ("finals") and its recipe.json into an Albius recipe (and writes
# /tmp/boot-grub.cfg, /tmp/abroot.cfg and /tmp/abimage.abr, which the
# recipe's steps read); then "sudo albius RECIPE".  Here the ISO's own
# processor.py is called with the answers of an automatic install: disk
# /dev/vda ("auto", nothing to remove), no encryption, language en_US.UTF-8,
# keyboard us (pc105), time zone Etc/UTC, the VM-tools page's "no" and the
# default image (recipe.json's images.default), each in the form its page's
# get_finals() gives (vanilla_installer/defaults/*.py).  So the layout, the image,
# the post-install steps and the first-setup user "vanilla" are the
# installer's own.  processor.py imports vanilla_installer.core.disks and
# .system (GTK-free, but the package's __init__ may not be): if those
# imports fail, they are replaced by stubs answering what this VM would
# (UEFI: yes; /dev/vda removable: no), and the log says so (on the 3.0 ISO
# they import as they are).
#
# Then, appended to the recipe's postInstallation (after the installer's
# late steps, so with the /etc overlay on vos-a's upper mounted), one chroot
# step running guest.sh below: only what every BackOtter test guest gets,
# and what this one needs to be driven:
#   host name @HOSTNAME@; root's password and SSH key; a "tester" user with
#   the same password (in sudo); ssh.service enabled (openssh-server is in
#   the image); multi-user.target as the default (so GDM and the
#   first-setup session never start: the user "vanilla" stays as the
#   installer made it); vanilla-updates.timer masked (it would run vso
#   upgrade, an abroot upgrade, on its own: the guest is updated only by
#   vm-update); the serial console in the kernel's options (/etc/abroot/kargs,
#   ABRoot's default with quiet and splash replaced, and init's
#   vos-a/abroot.cfg the same) and in the master /boot/grub/grub.cfg
#   (terminal serial and console, so a test can pick an entry); and the
#   fallback loader EFI/BOOT/BOOTX64.EFI: a copy of EFI/vanilla's shim, with
#   its grubx64.efi, mmx64.efi and grub.cfg (no fbx64.efi, so shim starts
#   GRUB at once), since vm-run's fresh UEFI variables boot nothing else.
#   The installer doesn't make it.

set -euo pipefail
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"' ERR

GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
ENCRYPT='@ENCRYPT@'
DRY_RUN='@DRY_RUN@'    # make-vanilla.sh --dry-run: stop before albius
DISK=/dev/vda
CONSOLE='console=tty0 console=ttyS0,115200n8'
W=/root/bko
mkdir -p $W

echo "BACKOTTER-INSTALL-START ($GUEST_NAME)"
date -u
# (The live system's /etc/os-release is live-build's: ID=ubuntu, NAME and
# VERSION_ID "3"; /usr/lib/os-release is base-files', ID=vanilla.)
grep -qx 'NAME="Vanilla OS"' /etc/os-release || { echo "not Vanilla OS's live system"; cat /etc/os-release; false; }
grep -E '^(VERSION_ID|VERSION|BUILD_ID)=' /etc/os-release || true
command -v albius >/dev/null || { echo "no albius"; false; }
[[ $ENCRYPT == no ]] || { echo "encryption isn't built yet"; false; }
systemctl --version | head -1
dpkg-query -W -f '${Package} ${Version}\n' albius vanilla-installer abroot 2>/dev/null || true

# --- The network (the install pulls its image from ghcr.io) ---------------------
for i in $(seq 90); do
    timeout 5 bash -c 'exec 3<>/dev/tcp/ghcr.io/443' 2>/dev/null && break
    [[ $i == 90 ]] && { echo "ghcr.io:443 unreachable"; ip a; false; }
    sleep 2
done
echo "ghcr.io:443 reachable"

# --- The installer's recipe -------------------------------------------------------
proc=$(find /usr -path '*/vanilla_installer/utils/processor.py' -print -quit)
[[ -n $proc ]] || { echo "no vanilla_installer/utils/processor.py"; false; }
pkgdir=$(dirname "$(dirname "$(dirname "$proc")")")
sysrecipe=""
for f in /etc/vanilla-installer/recipe.json "$pkgdir/recipe.json" /usr/share/vanilla-installer/recipe.json; do
    [[ -f $f ]] && { sysrecipe=$f; break; }
done
[[ -n $sysrecipe ]] || sysrecipe=$(find /usr/share /etc -name recipe.json -path '*vanilla*' -print -quit)
[[ -n $sysrecipe ]] || { echo "no installer recipe.json"; false; }
echo "processor: $proc"
echo "recipe:    $sysrecipe"
# (VANILLA_FAKE and VANILLA_SKIP_POSTINSTALL must be unset: the real recipe.)
env -u VANILLA_FAKE -u VANILLA_SKIP_POSTINSTALL python3 -I - "$pkgdir" "$sysrecipe" "$DISK" "$W/installer-recipe.json" <<'EOF'
import importlib, json, logging, os, shutil, sys, types
pkgdir, sysrecipe, disk, out = sys.argv[1:5]
logging.basicConfig(level=logging.INFO)
sys.path.insert(0, pkgdir)
try:
    importlib.import_module("vanilla_installer.core.disks")
    importlib.import_module("vanilla_installer.core.system")
    print("installer modules: the ISO's own vanilla_installer.core.disks and .system")
except Exception as e:
    print(f"installer modules: stubbed ({e!r})")
    for name in ("vanilla_installer", "vanilla_installer.core", "vanilla_installer.utils"):
        m = types.ModuleType(name); m.__path__ = [os.path.join(pkgdir, *name.split("."))]
        sys.modules[name] = m
    disks = types.ModuleType("vanilla_installer.core.disks")
    class Disk:
        def __init__(self, name): self.is_removable = open(f"/sys/block/{name}/removable").read().strip() == "1"
    class Diskutils:
        pass
    disks.Disk, disks.Diskutils = Disk, Diskutils
    system = types.ModuleType("vanilla_installer.core.system")
    class Systeminfo:
        @staticmethod
        def is_uefi(): return os.path.isdir("/sys/firmware/efi")
    system.Systeminfo = Systeminfo
    sys.modules["vanilla_installer.core.disks"] = disks
    sys.modules["vanilla_installer.core.system"] = system
from vanilla_installer.utils.processor import Processor
sys_recipe = json.load(open(sysrecipe))
print("images:", sys_recipe.get("images"))
finals = [
    {"language": "en_US.UTF-8"},
    {"keyboard": [{"layout": "us", "model": "pc105", "variant": ""}]},
    {"timezone": {"region": "Etc", "zone": "UTC"}},
    {"disk": {"auto": {"disk": disk, "pvs_to_remove": [], "vgs_to_remove": []}}},
    {"vm": {"use-vm-tools": False}},
    {"default-image": True},
    {"encryption": {"use_encryption": False, "encryption_key": ""}},
]
path = Processor.gen_install_recipe("/tmp/vanilla-installer.log", finals, sys_recipe)
if not path:
    sys.exit("gen_install_recipe made no recipe")
shutil.copy(path, out)
print("gen_install_recipe:", path)
EOF
for f in /tmp/boot-grub.cfg /tmp/abroot.cfg /tmp/abimage.abr; do
    [[ -s $f ]] || { echo "the processor wrote no $f"; false; }
done

# --- This guest's additions: guest.sh, run in the target by the recipe's last step
cat >$W/guest.sh <<EOF
#!/bin/bash
# BackOtter's additions to vanilla3, run by Albius as the recipe's last
# step, in a chroot of the target (/mnt/a) with /etc the overlay on vos-a's
# upper, /var, /boot, /boot/efi and /boot/init mounted.
# (Traced, but not the lines with the password's hash.)
set -euo pipefail
exec 2>&1
GUEST_NAME='$GUEST_NAME'
ROOT_PASSWORD_HASH='$ROOT_PASSWORD_HASH'
AUTHORIZED_KEY='$AUTHORIZED_KEY'
CONSOLE='$CONSOLE'
EOF
cat >>$W/guest.sh <<'EOF'
set -x
findmnt -no SOURCE,FSTYPE /etc 2>/dev/null || grep ' /etc ' /proc/mounts 2>/dev/null || true
echo "$GUEST_NAME" >/etc/hostname
if grep -qE '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
    sed -i -E "s/^(127\\.0\\.1\\.1[[:space:]]+).*/\\1$GUEST_NAME/" /etc/hosts
else
    printf '127.0.1.1\t%s\n' "$GUEST_NAME" >>/etc/hosts
fi
{ set +x; } 2>/dev/null
usermod -p "$ROOT_PASSWORD_HASH" root
useradd -m -s /bin/bash -c "Test user" -G sudo tester
usermod -p "$ROOT_PASSWORD_HASH" tester
echo "+ (root's and tester's passwords set)"
set -x
install -d -m 700 /root/.ssh
echo "$AUTHORIZED_KEY" >/root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
systemctl unmask ssh.service || true
systemctl enable ssh.service
systemctl set-default multi-user.target
systemctl mask vanilla-updates.timer
# The kernel's options: ABRoot's default kargs (core/kargs.go's
# DefaultKargs in v2.6.0 and main, what an upgrade writes when
# /etc/abroot/kargs is missing: the binary doesn't hold it as one string to
# check) with quiet and splash replaced by the serial console; and the same
# in the installer's vos-a/abroot.cfg (its own line, which also has
# lsm=integrity).
kargs='quiet splash bgrt_disable $vt_handoff'
abroot --version 2>&1 | head -1 || true
mkdir -p /etc/abroot
printf '%s' "${kargs/quiet splash/$CONSOLE}" >/etc/abroot/kargs
cfg=/boot/init/vos-a/abroot.cfg
grep -q ' quiet splash ' $cfg
sed -i "s| quiet splash | $CONSOLE |" $cfg
# The master grub.cfg: GRUB on the serial console too.
g=/boot/grub/grub.cfg
grep -qx 'terminal_output gfxterm' $g
sed -i 's|^terminal_output gfxterm$|serial --unit=0 --speed=115200\nterminal_input console serial\nterminal_output gfxterm serial|' $g
# The fallback loader.
e=/boot/efi/EFI
[[ ! -e $e/BOOT ]] || { echo "the installer made an EFI/BOOT"; ls -la $e/BOOT; false; }
mkdir $e/BOOT
cp $e/vanilla/shimx64.efi $e/BOOT/BOOTX64.EFI
cp $e/vanilla/grubx64.efi $e/vanilla/mmx64.efi $e/vanilla/grub.cfg $e/BOOT/
echo "--- kargs: $(cat /etc/abroot/kargs)"
echo "--- abroot.cfg"; cat $cfg
echo "--- EFI"; find $e | sort
echo "--- guest.sh done"
rm -f "$0"
EOF
# The recipe as the installer made it, and with the guest's steps appended
# (the guest.sh copy: the live system's /root isn't in the chroot).
python3 -I - "$W/installer-recipe.json" "$W/recipe.json" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
r["postInstallation"] += [
    {"chroot": False, "operation": "shell", "params": ["install -m 700 /root/bko/guest.sh /mnt/a/var/tmp/bko-guest.sh"]},
    {"chroot": True, "operation": "shell", "params": ["bash /var/tmp/bko-guest.sh"]},
]
json.dump(r, open(sys.argv[2], "w"), indent=2)
EOF
echo "BKO-RECIPE-BEGIN"
python3 -I -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1])), indent=2))' "$W/recipe.json"
echo "BKO-RECIPE-END"
echo "--- /tmp/abroot.cfg"; cat /tmp/abroot.cfg
echo "--- /tmp/abimage.abr"; cat /tmp/abimage.abr

# --- Albius -----------------------------------------------------------------------
[[ $DRY_RUN == no ]] || { echo "BACKOTTER-INSTALL-DRY"; exit 0; }
echo "--- albius $W/recipe.json ($(date -u +%T))"
albius "$W/recipe.json"
echo "--- albius finished ($(date -u +%T))"

# --- What it left -----------------------------------------------------------------
T=/mnt/a
findmnt -R $T || true
findmnt /mnt/b || true
for f in etc/fstab abimage.abr boot/grub/grub.cfg boot/init/vos-a/abroot.cfg etc/abroot/abroot.json \
    boot/efi/EFI/vanilla/shimx64.efi boot/efi/EFI/BOOT/BOOTX64.EFI var/lib/abroot/etc/vos-a/fstab; do
    if [[ -s $T/$f ]]; then echo "present: /$f"; else echo "missing: /$f"; fi
done
[[ -s $T/abimage.abr && -s $T/boot/init/vos-a/abroot.cfg && -s $T/boot/efi/EFI/BOOT/BOOTX64.EFI ]] ||
    { echo "the install is incomplete"; false; }
ls $T/boot/init/vos-a/vmlinuz-* $T/boot/init/vos-a/initrd.img-* >/dev/null || { echo "no kernel in init/vos-a"; false; }
[[ -z $(ls -A $T/boot/init/vos-b) ]] && echo "init/vos-b is empty, as expected"
grep -qF "$CONSOLE" $T/boot/init/vos-a/abroot.cfg || { echo "abroot.cfg has no serial console"; false; }
echo "--- os-release"; cat $T/usr/lib/os-release; ls -l $T/etc/os-release
echo "--- fstab (the root's lower)"; cat $T/etc/fstab
echo "--- /abimage.abr"; cat $T/abimage.abr
echo "--- master grub.cfg"; cat $T/boot/grub/grub.cfg
echo "--- init"; ls -la $T/boot/init $T/boot/init/vos-a $T/boot/init/vos-b
echo "--- abroot.cfg"; cat $T/boot/init/vos-a/abroot.cfg
echo "--- /etc/abroot"; ls -la $T/etc/abroot; cat $T/etc/abroot/abroot.json; echo; cat $T/etc/abroot/kargs; echo
echo "--- the /etc upper"; ls -la $T/var/lib/abroot/etc/vos-a | head -80
echo "--- ESP"; find $T/boot/efi | sort
echo "--- efibootmgr"; efibootmgr -v || true
echo "--- LVM"; lvs -a -o+seg_type,devices,pool_lv,data_percent,metadata_percent,chunk_size,discards,zero 2>&1 || true
vgs; pvs
echo "--- GPT"; parted -s $DISK print
lsblk -o NAME,TYPE,FSTYPE,LABEL,PARTLABEL,UUID,SIZE
echo "--- room"; df -h $T $T/var $T/boot $T/boot/efi $T/boot/init /mnt/b 2>/dev/null || true

# --- finish: unmount --------------------------------------------------------------
for p in /proc/[0-9]*; do
    r=$(readlink "$p/root" 2>/dev/null) || continue
    [[ $r == $T || $r == $T/* ]] || continue
    echo "stopping ${p#/proc/} ($(tr '\0' ' ' <"$p/cmdline"), left by the chroot)"
    kill "${p#/proc/}" 2>/dev/null || true
done
sleep 2
fstrim -v $T/boot/efi || true
fstrim -v $T/boot || true
fstrim -v $T/boot/init || true
fstrim -v $T/var || true
fstrim -v $T || true
fstrim -v /mnt/b || true
umount -R $T
umount -R /mnt/b 2>/dev/null || true
findmnt -rno TARGET | grep -E '^/mnt/(a|b)(/|$)' && { echo "mounts left"; false; }
vgchange -an vos-root vos-var
sync
echo "BACKOTTER-INSTALL-OK"
