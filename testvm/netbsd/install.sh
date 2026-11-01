#!/bin/sh
# NetBSD (or AlpineBSD) test guest for BackOtter: the install, done by hand
# the way sysinst would, unattended.  make-netbsd.sh fills in the @...@
# settings and puts this file on the install ISO in place of /install.sh,
# which the ISO's console getty runs as root (instead of sysinst).  Output
# goes to the console, which is the serial port; it powers the VM off when
# done, printing BACKOTTER-INSTALL-OK or BACKOTTER-INSTALL-FAILED first.
#
# NetBSD: MBR with one NetBSD partition, its disklabel holding / (a, FFSv2,
# WAPBL log) and swap (b), the base sets but compilers, games, tests and X.
#
# AlpineBSD (NetBSD's kernel, an Alpine-like userspace built from aports:
# apk, busybox, s6): GPT, as its installation guide has it, with a 1 GiB EFI
# system partition, swap, and / (FFSv2 with extended attributes), installed
# with apk from AlpineBSD's mirror, booting from BIOS through gpt biosboot
# or from UEFI through NetBSD's loader on the EFI system partition.

FLAVOR='@FLAVOR@'                      # netbsd or alpinebsd
GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD='@ROOT_PASSWORD@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
SERVER='@SERVER@'                      # make-netbsd.sh's HTTP server, for its files
ABSD_MIRROR='@ABSD_MIRROR@'
DISK=ld0                               # virtio-blk
NIC=vioif0                             # virtio-net
SETS="base etc kern-GENERIC modules man misc rescue text"
T=/targetroot

fail() { echo "BACKOTTER-INSTALL-FAILED: $*"; sync; sleep 2; halt -p; exit 1; }
run() { "$@" || fail "$*"; }
echo "BACKOTTER-INSTALL-START ($FLAVOR)"
PATH=/sbin:/usr/sbin:/bin:/usr/bin
export PATH

# The network, for AlpineBSD's packages and this script's files.
run ifconfig $NIC up
run dhcpcd -w -q $NIC

if [ "$FLAVOR" = netbsd ]; then
    # The whole disk for NetBSD (MBR type 169), from sector 2048; the
    # disklabel inside it: a (/) is all but 2 GiB, b is swap.
    run fdisk -f -u -i -0 -a -s 169/2048 $DISK
    run fdisk -f -c /usr/mdec/mbr $DISK
    total=$(fdisk -S $DISK | sed -n 's/^PART0SIZE=//p')
    swap=$((2 * 1024 * 1024 * 2))
    # The kernel's default label (there is none on the disk yet), edited;
    # disklabel complains about its zero boot block sizes, and fails.
    disklabel $DISK >/tmp/label 2>/dev/null
    grep -q '^total sectors:' /tmp/label || fail "disklabel"
    awk -v start=2048 -v total="$total" -v swap="$swap" '
        /^ *[a-p]:/ && !done {
            printf " a: %d %d 4.2BSD 0 0 0\n", total - swap, start
            printf " b: %d %d swap\n", swap, start + total - swap
            printf " c: %d %d unused 0 0\n", total, start
            printf " d: %d 0 unused 0 0\n", start + total
            done = 1
        }
        /^ *[a-p]:/ { next }
        /^[0-9]+ partitions:/ { print "16 partitions:"; next }
        { print }' /tmp/label >/tmp/label.new
    run disklabel -R -r $DISK /tmp/label.new
    run newfs -O 2 /dev/r${DISK}a
    run mount -o log /dev/${DISK}a $T
    for s in $SETS; do
        echo "Extracting $s"
        (cd $T && tar -xpJf /amd64/binary/sets/$s.tar.xz) || fail "set $s"
    done
    run cp /usr/mdec/boot $T/boot
    run installboot -v -o console=com0,speed=115200,timeout=3 /dev/r${DISK}a /usr/mdec/bootxx_ffsv2
    cat >$T/boot.cfg <<'EOF'
consdev=com0,115200
menu=Boot normally:rndseed /var/db/entropy-file;boot
menu=Boot single user:rndseed /var/db/entropy-file;boot -s
default=1
timeout=3
EOF
    (cd $T/dev && sh ./MAKEDEV all) || fail MAKEDEV
    cat >$T/etc/fstab <<EOF
/dev/${DISK}a	/		ffs	rw,log	1 1
/dev/${DISK}b	none		swap	sw	0 0
kernfs		/kern		kernfs	rw
ptyfs		/dev/pts	ptyfs	rw
procfs		/proc		procfs	rw
tmpfs		/var/shm	tmpfs	rw,-m1777,-sram%25
EOF
    mkdir -p $T/kern $T/proc $T/var/shm
    cat >>$T/etc/rc.conf <<EOF
rc_configured=YES
hostname=$GUEST_NAME
dhcpcd=YES
sshd=YES
ntpd=YES
ip6addrctl=YES
ip6addrctl_policy=ipv4_prefer
EOF
    sed -i 's/^#\{0,1\}PermitRootLogin .*/PermitRootLogin prohibit-password/' $T/etc/ssh/sshd_config
    run chroot $T sh -c "usermod -p '$(chroot $T pwhash "$ROOT_PASSWORD")' root"
    # What updates it, which the base sets lack: the CA certificates the
    # base set ships, put in place (certctl), for TLS; pkgin, from pkgsrc's
    # binary packages for this release; and sysupgrade, for the base.
    # IPv4 first, above, where a network hands out IPv6 that leads nowhere.
    # vm-update's in-guest.sh does the same for a guest built without them,
    # and writes sysupgrade.conf at every update.
    run chroot $T certctl rehash
    cp /etc/resolv.conf $T/etc/resolv.conf
    pkgs=https://cdn.netbsd.org/pub/pkgsrc/packages/NetBSD/$(uname -m)/$(uname -r | sed 's/_.*//')/All
    echo "Installing pkgin and sysupgrade from $pkgs"
    run chroot $T env PKG_PATH=$pkgs pkg_add pkgin
    echo "$pkgs" >$T/usr/pkg/etc/pkgin/repositories.conf
    run chroot $T /usr/pkg/bin/pkgin -y install sysupgrade
else
    # GPT, as AlpineBSD's installation guide lays it out.
    run gpt create -f $DISK
    run gpt add -l EFI -t efi -s 1G $DISK
    run gpt add -l swap -t swap -s 2G $DISK
    run gpt add -l AlpineBSD -t ffs $DISK
    sleep 2
    efi=$(dkctl $DISK listwedges | awk -F: '/EFI/ { print $1; exit }')
    root=$(dkctl $DISK listwedges | awk -F: '/AlpineBSD/ { print $1; exit }')
    [ -n "$efi" ] && [ -n "$root" ] || fail "no wedges for the new partitions"
    run newfs_msdos -F 32 /dev/r$efi
    run newfs -O 2ea /dev/r$root
    run mount /dev/$root $T
    mkdir -p $T/etc/apk/keys $T/sbin
    echo x86_64 >$T/etc/apk/arch
    printf '%s\n' "$ABSD_MIRROR/edge/base" "$ABSD_MIRROR/edge/main" >$T/etc/apk/repositories
    # apk.static and the repository key, as make-netbsd.sh fetched them.
    run ftp -o $T/sbin/apk "$SERVER/apk.static"
    run ftp -o "$T/etc/apk/keys/@ABSD_KEY_NAME@" "$SERVER/absd-key.rsa.pub"
    chmod +x $T/sbin/apk
    cp /etc/resolv.conf $T/etc/resolv.conf
    # The packages' signatures are checked against that key; only the TLS
    # certificates go unchecked, as the guide does it (no CA bundle here).
    run $T/sbin/apk -p $T --initdb --check-certificate=no add absd-base netbsd-kernel-generic-bin \
        netbsd-firmware netbsd-bootloader-mbr netbsd-bootloader-efi ifupdown ifupdown-s6 dhcpcd dhcpcd-s6 \
        dropbear dropbear-s6 openssh-sftp-server
    (cd $T/dev && sh ./MAKEDEV all) || fail MAKEDEV
    # The stock /etc/rc.conf sources /etc/defaults/rc.conf the shell's way,
    # but s6 reads it with envfile, which takes only name=value lines: as
    # shipped (2026-10), every service that reads it, dropbear's among them,
    # fails to start.  So put the defaults' assignments in its place.
    grep -E '^[A-Za-z_][A-Za-z0-9_]*=' $T/etc/defaults/rc.conf >/tmp/rc.defaults || fail "rc.conf defaults"
    sed -i -e '/^if \[ -e \/etc\/defaults\/rc.conf \]/,/^fi$/{' -e '/^fi$/r /tmp/rc.defaults' -e 'd' -e '}' \
        $T/etc/rc.conf
    cat >$T/etc/fstab <<'EOF'
NAME=AlpineBSD	/	ffs	rw,log	1 1
NAME=swap	none	swap	sw	0 0
EOF
    echo "$GUEST_NAME" >$T/etc/hostname
    # The entropy its rngseed service loads at boot (and fails without).
    mkdir -p $T/var/db
    run rndctl -S $T/var/db/entropy-file
    mkdir -p $T/etc/network
    printf 'auto lo0\niface lo0 inet loopback\n\nauto %s\niface %s inet dhcp\n' $NIC $NIC \
        >$T/etc/network/interfaces
    cat >$T/etc/boot.cfg <<'EOF'
consdev=com0,115200
menu=Boot AlpineBSD:root NAME=AlpineBSD;rndseed /var/db/entropy-file;boot
timeout=3
EOF
    ln -s etc/boot.cfg $T/boot.cfg 2>/dev/null || true
    # BIOS boot: GPT's protective MBR boots the partition's first-stage
    # boot code, which loads /boot.
    run cp $T/usr/mdec/boot $T/boot
    run installboot -v -o console=com0,speed=115200,timeout=3 /dev/r$root $T/usr/mdec/bootxx_ffsv2
    run gpt biosboot -c $T/usr/mdec/gptmbr.bin -L AlpineBSD $DISK
    # UEFI boot too, from the EFI system partition: NetBSD's loader, which
    # finds / by itself.  Not mounted in the guest.
    mkdir -p /tmp/efi
    run mount -t msdos /dev/$efi /tmp/efi
    mkdir -p /tmp/efi/EFI/BOOT
    run cp $T/usr/mdec/bootx64.efi /tmp/efi/EFI/BOOT/BOOTX64.EFI
    run umount /tmp/efi
    # Root's password, in whichever password file this userspace keeps.
    hash=$(pwhash "$ROOT_PASSWORD")
    if [ -f $T/etc/master.passwd ]; then
        sed -i "s|^root:[^:]*:|root:$hash:|" $T/etc/master.passwd
        run pwd_mkdb -d $T -p $T/etc/master.passwd
    elif [ -f $T/etc/shadow ]; then
        sed -i "s|^root:[^:]*:|root:$hash:|" $T/etc/shadow
    else
        fail "no password file for root"
    fi
    # s6, as the guide sets it up: the gettys switchvt wants, the network as
    # its networking page has it, dropbear, and a getty on the serial
    # console.  The instances come first: the repository's first compile
    # fails while switchvt's gettys don't exist yet.
    for i in getty@ttyE1 getty@ttyE2 getty@ttyE3 getty@tty00 ifupdown@lo0 ifupdown@$NIC; do
        run chroot $T s6-instance $i
    done
    run chroot $T s6 repo init
    for i in ifupdown@lo0 ifupdown@$NIC; do
        touch $T/etc/s6/sources/bundle-if_ready/contents.d/$i || fail "bundle-if_ready"
    done
    run chroot $T s6 repo sync
    echo "Services AlpineBSD offers:"; chroot $T s6 set status
    run chroot $T s6 set enable getty@ttyE1 getty@ttyE2 getty@ttyE3 getty@tty00 wscons switchvt \
        root persist-log mountall hostname fsck_root fsck rngseed ifupdown@lo0 ifupdown@$NIC \
        dropbear-daemon dropbear-log
    run chroot $T s6 set commit
    run chroot $T s6 live install --init
fi

mkdir -p $T/root/.ssh
echo "$AUTHORIZED_KEY" >$T/root/.ssh/authorized_keys
chmod 700 $T/root/.ssh
chmod 600 $T/root/.ssh/authorized_keys
cd /
run umount $T
sync
echo "BACKOTTER-INSTALL-OK"
halt -p
