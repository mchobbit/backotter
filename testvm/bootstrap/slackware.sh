# --- The distribution's part ----------------------------------------------------------
# Slackware 15.0 (docs/design/slackware.md), installed the way Slackware's
# own setup installs it: installpkg --root of each package of the chosen
# series into /mnt, in series order and by name within a series, then the
# files setup's configuration scripts write (fstab, rc.inet1.conf, the
# host name, the time zone, the boot loader).  setup has no answer file, so
# those files are written here; what differs from setup's is said where it's
# done.
#
# make-bootstrap.sh checked the tree (CHECKSUMS.md5 against Slackware's key,
# each package's MD5 from it, and each package's own .asc) and serves it at
# $SERVER/tree/, with pkglist.txt, the packages to install, one path a line
# (slackware64/a/aaa_base-15.0-x86_64-3.txz ...).  installpkg is Slackware's
# own, from the tree's a/pkgtools package, run from the Arch live system as
# setup runs it from its install ramdisk.
#
# KERNEL: huge (slackware15: no initrd, LILO in the MBR) or generic
# (slackware15-uefi: mkinitrd's initrd, ELILO on the ESP).

KERNEL='@KERNEL@'
# Commands in the new system, with Slackware's PATH: its tools are in
# /sbin and /usr/sbin, which the Arch live system's PATH lacks.
in_slk() { arch-chroot /mnt env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "$@"; }
MIRROR=https://slackware.osuosl.org/slackware64-15.0/

# / as Slackware's setup makes it, with its own tools' defaults: the live
# system's mkfs is newer and turns on features Slackware 15 can't use
# (ext4's orphan_file, which its e2fsck 1.46.5 refuses at boot; XFS's
# nrext64 and exchange, which its 5.15 kernel won't mount).  ext4: mke2fs
# with the mke2fs.conf of the tree's e2fsprogs; XFS: xfsprogs' LTS 5.10
# profile, xfsprogs 5.13's defaults (bigtime, inobtcount and rmapbt off,
# reflink on).
case $ROOT_FS in
    ext4)
        e2p=$(curl -fsS "$SERVER/pkglist.txt" | grep '/e2fsprogs-[^/]*\.txz$')
        curl -fsS "$SERVER/tree/$e2p" | tar -xJOf - etc/mke2fs.conf.new >/tmp/slk-mke2fs.conf
        grep -q '^[[:space:]]*ext4 = {' /tmp/slk-mke2fs.conf || { echo "no ext4 in $e2p's mke2fs.conf"; false; }
        MKE2FS_CONFIG=/tmp/slk-mke2fs.conf mkfs.ext4 -q -F "$ROOT_PART"
        ;;
    xfs)
        prof=/usr/share/xfsprogs/mkfs/lts_5.10.conf
        [[ -e $prof ]] || { echo "no $prof in the live system's xfsprogs"; false; }
        mkfs.xfs -q -f -c options="$prof" "$ROOT_PART"
        ;;
    *) echo "slackware: no Slackware defaults for $ROOT_FS"; false ;;
esac
mount -t "$ROOT_FS" "$ROOT_PART" /mnt
mount_boot

# Slackware's pkgtools, unpacked beside the live system's own tools.
pkgtools=$(curl -fsS "$SERVER/pkglist.txt" | grep '/pkgtools-[^/]*\.txz$')
mkdir -p /tmp/pkgtools
curl -fsS "$SERVER/tree/$pkgtools" | tar -xJf - -C /tmp/pkgtools
installpkg=/tmp/pkgtools/sbin/installpkg
[[ -x $installpkg ]] || { echo "no sbin/installpkg in $pkgtools"; false; }

mkdir -p /mnt/tmp/bko-pkgs
n=0
while read -r p; do
    [[ -n $p ]] || continue
    f=/mnt/tmp/bko-pkgs/${p##*/}
    curl -fsS -o "$f" "$SERVER/tree/$p"
    # --terse: one line a package, as setup's "terse" mode.
    "$installpkg" --root /mnt --terse "$f" </dev/null
    rm -f "$f"
    n=$((n + 1))
done < <(curl -fsS "$SERVER/pkglist.txt")
rmdir /mnt/tmp/bko-pkgs
echo "installed $n packages"
kver=$(ls /mnt/lib/modules)
[[ $(wc -w <<<"$kver") -eq 1 ]] || { echo "expected one kernel in /lib/modules, found: $kver"; false; }
[[ -e /mnt/boot/vmlinuz-$KERNEL-$kver ]] || { echo "no /boot/vmlinuz-$KERNEL-$kver"; ls -l /mnt/boot; false; }

# What setup's final steps run (setup's "SeTconfig" phase, the
# non-interactive part).
in_slk /sbin/ldconfig

# fstab, as setup's SeTpartitions and SeTswap write it: device names.
{
    printf '%-16s %-16s %-11s %-16s %s\n' "$SWAP_PART" swap swap defaults "0   0"
    printf '%-16s %-16s %-11s %-16s %s\n' "$ROOT_PART" / "$ROOT_FS" defaults "1   1"
    [[ -z $ESP_PART ]] || printf '%-16s %-16s %-11s %-16s %s\n' "$ESP_PART" /boot/efi vfat defaults "1   0"
    printf '%-16s %-16s %-11s %-16s %s\n' "#/dev/cdrom" /mnt/cdrom auto "noauto,owner,ro,comment=x-gvfs-show" "0   0"
    printf '%-16s %-16s %-11s %-16s %s\n' /dev/fd0 /mnt/floppy auto noauto,owner "0   0"
    printf '%-16s %-16s %-11s %-16s %s\n' devpts /dev/pts devpts gid=5,mode=620 "0   0"
    printf '%-16s %-16s %-11s %-16s %s\n' proc /proc proc defaults "0   0"
    printf '%-16s %-16s %-11s %-16s %s\n' tmpfs /dev/shm tmpfs nosuid,nodev,noexec "0   0"
} >/mnt/etc/fstab

# netconfig: the host name and DHCP on eth0.
echo "$GUEST_NAME.localdomain" >/mnt/etc/HOSTNAME
sed -i "s/^127\.0\.0\.1.*/127.0.0.1\t\tlocalhost $GUEST_NAME.localdomain $GUEST_NAME/" /mnt/etc/hosts
sed -i 's/^USE_DHCP\[0\]=.*/USE_DHCP[0]="yes"/' /mnt/etc/rc.d/rc.inet1.conf
grep -q '^USE_DHCP\[0\]="yes"' /mnt/etc/rc.d/rc.inet1.conf || { echo "rc.inet1.conf has no USE_DHCP[0]"; false; }

# timeconfig: UTC, the hardware clock in UTC.
ln -sf /usr/share/zoneinfo/UTC /mnt/etc/localtime-copied-from
cp /mnt/usr/share/zoneinfo/UTC /mnt/etc/localtime
echo UTC >/mnt/etc/hardwareclock

# The services setup's menu leaves on by default (rc.sshd among them):
# whatever the packages shipped executable.  Made sure of for sshd.
chmod 755 /mnt/etc/rc.d/rc.sshd

# Root's password and SSH key; a getty on the serial console (inittab has
# only a commented 9600-baud line for it).
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | in_slk chpasswd -e
install_ssh_key /mnt
echo 's0:12345:respawn:/sbin/agetty -L ttyS0 115200 vt100' >>/mnt/etc/inittab

# slackpkg: one official mirror, and Slackware's key for it.
cp /etc/resolv.conf /mnt/etc/resolv.conf
echo "$MIRROR" >>/mnt/etc/slackpkg/mirrors
in_slk slackpkg -batch=on -default_answer=y update gpg

if [[ $KERNEL == huge ]]; then
    # liloconfig's "simple" mode, MBR target, as lilo 24.2's sbin/liloconfig
    # (simplelilo) writes it for /dev/vda: no append= of the user's, its
    # disk= line for vda, the bitmap menu, change-rules, vga normal.  Two
    # changes: timeout 20 (liloconfig: 1200, two minutes), and the serial
    # console in append=.
    cat >/mnt/etc/lilo.conf <<EOF
# LILO configuration file
# generated by 'liloconfig'
#
# Start LILO global section
# Append any additional kernel parameters:
append=" $SERIAL_CMDLINE"

disk = $DISK bios=0x80 max-partitions=7
boot = $DISK
# This option loads the kernel and initrd much faster:
compact

# Boot BMP Image.
# Bitmap in BMP format: 640x480x8
  bitmap = /boot/slack.bmp
# Menu colors (foreground, background, shadow, highlighted
# foreground, highlighted background, highlighted shadow):
  bmp-colors = 255,0,255,0,255,0
# Location of the option table: location x, location y, number of
# columns, lines per column (max 15), "spill" (this is how many
# entries must be in the first column before the next begins to
# be used. We don't specify it here, as there's just one column.
  bmp-table = 60,6,1,16
# Timer location x, timer location y, foreground color,
# background color, shadow color.
  bmp-timer = 65,27,0,255

# Standard menu.
# Or, you can comment out the bitmap menu above and 
# use a boot message with the standard menu:
#message = /boot/boot_message.txt

# Wait until the timeout to boot (if commented out, boot the
# first entry immediately):
prompt
# Timeout before the first entry boots.
# This is given in tenths of a second, so 600 for every minute:
timeout = 20
# Override dangerous defaults that rewrite the partition table:
change-rules
  reset
# Normal VGA console
vga = normal
# Ask for video mode at boot (time out to normal in 30s)
#vga = ask
# End LILO global section
# Linux bootable partition config begins
image = /boot/vmlinuz
  root = $ROOT_PART
  label = Linux
  read-only
# Linux bootable partition config ends
EOF
    [[ -e /mnt/boot/vmlinuz ]] || { echo "no /boot/vmlinuz for lilo.conf"; ls -l /mnt/boot; false; }
    # lilo turns "root = /dev/vda2" into the device's number as the running
    # kernel has it (lilo 24.2 bsect.c, dev_number: stat's st_rdev), and
    # virtio_blk's block major is dynamic: 254 under the Arch live kernel,
    # 253 under Slackware's huge kernel, which then can't mount 254:2.  So
    # this one run, from the live system, reads a copy of lilo.conf with
    # root=$ROOT_PART last in append=: the kernel takes the last root= and
    # finds it by name.  /etc/lilo.conf stays as liloconfig writes it; the
    # first lilo run in the guest (in-guest.sh's, after a kernel update)
    # writes the map a real install has.
    sed "s|^append=\"\(.*\)\"\$|append=\"\1 root=$ROOT_PART\"|" /mnt/etc/lilo.conf >/mnt/etc/lilo.conf.bko-install
    grep -q "^append=\".* root=$ROOT_PART\"\$" /mnt/etc/lilo.conf.bko-install || { echo "no root= added to lilo.conf's append="; false; }
    in_slk lilo -C /etc/lilo.conf.bko-install -t -v
    in_slk lilo -C /etc/lilo.conf.bko-install -v
    rm /mnt/etc/lilo.conf.bko-install
else
    # mkinitrd as setup's "generate an initrd" step makes it: the command
    # mkinitrd_command_generator.sh prints for this root, run.
    cmd=$(in_slk /usr/share/mkinitrd/mkinitrd_command_generator.sh -r -k "$kver" | grep '^mkinitrd ')
    echo "mkinitrd command: $cmd"
    for m in virtio_blk "$ROOT_FS"; do
        [[ $cmd == *"$m"* ]] || { echo "the generated mkinitrd command has no $m"; false; }
    done
    in_slk sh -c "$cmd"
    [[ -s /mnt/boot/initrd.gz ]] || { echo "mkinitrd made no /boot/initrd.gz"; false; }
    # eliloconfig's files (elilo 3.16's usr/sbin/eliloconfig), in
    # EFI/Slackware: elilo.conf as it writes it for a generic kernel with
    # an initrd, the serial console added to append=.  The test guests boot with an
    # empty UEFI variable store (vm-run --uefi), so the firmware's fallback
    # path, EFI/BOOT, gets the same four files (eliloconfig writes no
    # fallback copy, and its efibootmgr entry can't be made here).
    for d in Slackware BOOT; do
        e=/mnt/boot/efi/EFI/$d
        mkdir -p "$e"
        if [[ $d == BOOT ]]; then
            cp -a /mnt/boot/elilo-x86_64.efi "$e/BOOTX64.EFI"
        else
            cp -a /mnt/boot/elilo-x86_64.efi "$e/elilo.efi"
        fi
        cp "/mnt/boot/vmlinuz-generic-$kver" "$e/vmlinuz"
        touch -r "/mnt/boot/vmlinuz-generic-$kver" "$e/vmlinuz"
        cp /mnt/boot/initrd.gz "$e/initrd.gz"
        touch -r /mnt/boot/initrd.gz "$e/initrd.gz"
        cat >"$e/elilo.conf" <<EOF
chooser=simple
delay=1
timeout=1
#
image=vmlinuz
        label=vmlinuz
        initrd=initrd.gz
        read-only
        append="root=$ROOT_PART vga=normal ro $SERIAL_CMDLINE"
EOF
    done
fi

rm /mnt/etc/resolv.conf
finish
