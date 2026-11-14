#!/usr/bin/env bash
# Build an openSUSE test guest for BackOtter into a qcow2 disk image, under
# QEMU with KVM: Tumbleweed by an unattended AutoYaST install from the
# official DVD ISO, or MicroOS from openSUSE's own VM image, configured at its
# first boot by combustion.
#
# Usage: ./make-opensuse.sh SNAPSHOT WORK_DIR
#        ./make-opensuse.sh microos SNAPSHOT WORK_DIR
#        ./make-opensuse.sh leap VERSION WORK_DIR
#   e.g. ./make-opensuse.sh 20261030 vms/tumbleweed
#        ./make-opensuse.sh microos 20261111 vms/microos
#        ./make-opensuse.sh leap 16.1 vms/leap16
#
# SNAPSHOT is a Tumbleweed snapshot (YYYYMMDD).  openSUSE serves only the
# current snapshot's ISO, as openSUSE-Tumbleweed-DVD-x86_64-Current.iso: it is
# downloaded into WORK_DIR (with its .sha256 and .sha256.asc) unless already
# there, and must be SNAPSHOT's and signed by the openSUSE Project Signing
# Key.  Keep it there: the DVD holds every package the install takes, so a
# rebuild from it needs nothing of openSUSE's that expires (the history of
# old snapshots is kept for some months only), and installs the same
# snapshot.  The guest's repositories are the live ones, which vm-update
# updates it from.
#
# microos: the image is
# openSUSE-MicroOS.x86_64-16.0.0-kvm-and-xen-SnapshotSNAPSHOT.qcow2 from
# download.opensuse.org/tumbleweed/appliances/, with its .sha256 and
# .sha256.asc, fetched into WORK_DIR/inputs/ unless already there (one in
# WORK_DIR itself moves there: rebuild-one.sh links it in from
# /bkoscratch/rebuild-inputs/microos, since only the current snapshot's is
# served).  The signing key, which no ISO carries here, comes
# from the repository's repomd.xml.key, by the same pinned fingerprint.  The
# image is copied, grown to 24 GiB, and booted as vm-run boots it (--uefi:
# OVMF with Secure Boot on, through SUSE's shim) with
# opensuse/combustion-microos.sh in QEMU's fw_cfg.  Combustion runs that in
# the first boot's initrd, in a new snapshot (root's password and key, the
# host name, the update timer and rebootmgr off, GRUB on the serial line);
# then the same relabel and check boots as Tumbleweed's, which also check
# the transactional root: / mounted ro, the booted snapshot the btrfs
# default, health-checker's state, the timer and rebootmgr disabled, Secure
# Boot's state.  See docs/design/aeon.md.
#
# leap: openSUSE Leap VERSION (16.1) by an unattended Agama install from
# the offline installer ISO, Leap-VERSION-offline-installer-x86_64-BuildB.install.iso
# (B pinned below per VERSION), with its .sha512 and .sha512.asc, fetched
# into WORK_DIR unless already there (rebuild-one.sh links them in from
# /bkoscratch/rebuild-inputs/leap16); the key comes from the ISO's
# /install, by the same pinned fingerprint.  The ISO boots as a PC would
# boot it, under vm-run --uefi (OVMF, Secure Boot on, Microsoft's keys)
# through its own shim and GRUB, whose first entry (the install) starts
# after 60 s.  Agama finds its profile, opensuse/agama-leap.json with
# leap-post-chroot.sh and leap-post-live.sh put in, as autoinst.json on a
# second CD labelled OEMDRV: nothing on the ISO's kernel line changes.  It
# installs the product's own default (GPT, ESP, swap, btrfs / with
# snapper, SELinux enforcing, GRUB through shim), and reboots, which ends
# QEMU (-no-reboot).  Then the same relabel and check boots, under Secure
# Boot, which must be on.  See docs/design/leap16.md.
#
# Results, in WORK_DIR:
#   GUEST.qcow2           the guest's disk (sparse: tumbleweed.qcow2 16 GiB,
#                         microos.qcow2 24 GiB), with a qcow2 snapshot
#                         "fresh-install" of the finished install, taken
#                         after its first two boots (below)
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console (microos: the
#                         combustion boot's), and install.txt (microos: the
#                         snapshots and default it left)
#   relabel-boot-serial.log, check-boot-serial.log
#                         the two boots' serial consoles
#   relabel-boot.txt, check-boot.txt
#                         what each boot showed (systemd's state, SELinux's
#                         mode, relabel triggers and units, failed units,
#                         files whose label isn't the policy's; microos: the
#                         transactional root's state too)
#   vm-run-options        microos: "--uefi", how vm-run must boot it
#   inputs/               microos: openSUSE's image, its .sha256 and .asc
#
# SELinux: the install leaves the guest enforcing with a full relabel
# pending (/etc/selinux/.autorelabel, or /.autorelabel), which its first
# boot does: the initrd's pre-pivot hook (dracut's 98selinux-microos) runs
# setfiles over / and /var, and selinux-autorelabel-generator adds a
# restorecon -R unit per other fstab file system (/root, /home, /boot, ...)
# that local-fs.target requires, with no reboot.  A pending relabel would
# make every boot of a throwaway overlay (vm-run --snapshot, compare.py's
# boots) redo it, and fail it, to emergency mode, once the guest holds an
# immutable file (torture.sh's in /root/bko-torture): restorecon can't
# relabel one.  So the build boots the image for real once, which does the
# relabel in the image itself, and then once more, which must come up
# running, enforcing, with no relabel trigger left and no relabel unit:
# the build fails otherwise.  MicroOS's image ships with the relabel
# pending, and combustion touches .autorelabel again in its snapshot.
#
# The guest, host name tumbleweed: BIOS + MBR, /boot (1 GiB ext4), swap
# (2 GiB), / (btrfs, the rest) in openSUSE's own layout, subvolumes and
# snapper included, 2 GB RAM, no desktop; see opensuse/autoinst.xml.  Host
# name microos: the image's GPT (BIOS boot, ESP, btrfs root with snapper's
# read-only snapshots and an /etc subvolume in each), UEFI with Secure Boot,
# 2 GB RAM.  Run it with vm-run (and vm-run-options), and look inside it
# while it's off with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, gpg, gpgv, openssl,
# python3, wget, and network access (microos: OVMF, and no xorriso).  Runs
# as an ordinary user in the kvm group.

set -euo pipefail

# "openSUSE Project Signing Key <opensuse@opensuse.org>"
OPENSUSE_KEY=AD485664E901B867051AB15F35A2F86E29B700A4
INSTALL_TIMEOUT=3600
MEMORY=2048
# The relabel boot: the relabel takes a minute or two, the rest of the boot seconds.
BOOT_TIMEOUT=900

here=$(dirname "$(realpath "$0")")
die() { echo "make-opensuse: $*" >&2; exit 1; }

mode=tumbleweed
[[ ${1:-} == microos || ${1:-} == leap ]] && { mode=$1; shift; }
[[ $# -eq 2 ]] || die "usage: $0 [microos] SNAPSHOT WORK_DIR, or $0 leap VERSION WORK_DIR"
snapshot=$1
if [[ $mode == leap ]]; then
    [[ $snapshot =~ ^16\.[0-9]+$ ]] || die "VERSION looks like 16.1, not '$snapshot'"
else
    [[ $snapshot =~ ^[0-9]{8}$ ]] || die "SNAPSHOT looks like 20261030, not '$snapshot'"
fi
case $mode in
tumbleweed)
    DISK_SIZE=16G INSTALL_MEMORY=4096 GUEST_NAME=tumbleweed
    ISO=openSUSE-Tumbleweed-DVD-x86_64-Current.iso ;;
microos)
    DISK_SIZE=24G GUEST_NAME=microos
    IMAGE=openSUSE-MicroOS.x86_64-16.0.0-kvm-and-xen-Snapshot$snapshot.qcow2 ;;
leap)
    # Each version's ISO build, pinned: the build a rebuild installs.
    case $snapshot in
    16.1) build=60.2 ;;
    *) die "no ISO build pinned for Leap $snapshot" ;;
    esac
    DISK_SIZE=24G INSTALL_MEMORY=4096 GUEST_NAME=leap${snapshot%%.*}
    ISO=Leap-$snapshot-offline-installer-x86_64-Build$build.install.iso ;;
esac
DISK=$GUEST_NAME.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"
t0=$SECONDS

# --- Shared helpers -------------------------------------------------------------------
fetch() {  # URL DIR FILE...: each FILE from URL into DIR, unless already there (and whole)
    local url=$1 dir=$2 f
    shift 2
    for f in "$@"; do
        [[ -f $dir/$f && ! -f $dir/$f.part ]] && continue
        echo "Downloading $url/$f"
        touch "$dir/$f.part"
        wget -c -q -O "$dir/$f" "$url/$f" || die "could not download $url/$f"
        rm "$dir/$f.part"
    done
}
import_key() {  # KEYFILE: opensuse-key.gpg, of KEYFILE's openSUSE Project Signing Key only (by full fingerprint)
    local gnupg
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --import "$1"
    GNUPGHOME=$gnupg gpg -q --export "$OPENSUSE_KEY" >opensuse-key.gpg
    rm -rf "$gnupg"
    [[ -s opensuse-key.gpg ]] || { rm -f opensuse-key.gpg; die "$1 doesn't hold the openSUSE Project Signing Key ($OPENSUSE_KEY)"; }
}
verify() {  # FILE [ALG]: FILE.ALG (sha256, Leap's sha512) signed by the key, and FILE matching it
    local alg=${2:-sha256}
    gpgv --keyring ./opensuse-key.gpg "$1.$alg.asc" "$1.$alg" 2>/dev/null ||
        die "$1.$alg is not signed by the openSUSE Project Signing Key"
    [[ $("${alg}sum" "$1" | cut -d' ' -f1) == $(cut -d' ' -f1 "$1.$alg") ]] ||
        die "$1 does not match $1.$alg"
}
free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
work=$(mktemp -d)
server="" qemu_pid=""
cleanup() {
    [[ -n $server ]] && kill "$server" 2>/dev/null
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
trap cleanup EXIT
credentials() {  # root-password, its hash in $hash, and the SSH key
    umask 077
    password=$(openssl rand -base64 18)
    printf '%s\n' "$password" >root-password
    hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
    [[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
    umask 022
}
fill() {  # TEMPLATE: TEMPLATE with the @...@ settings filled in, on stdout
    sed -e "s|@HOSTNAME@|$GUEST_NAME|g" -e "s|@ROOT_PASSWORD_HASH@|$hash|" \
        -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" "$1"
}

# --- Tumbleweed: fetch and verify the ISO, then AutoYaST -----------------------------
install_tumbleweed() {
    fetch https://download.opensuse.org/tumbleweed/iso . "$ISO.sha256" "$ISO.sha256.asc" "$ISO"
    # The checksum file names the snapshot: ...-Snapshot20261030-Media.iso.
    grep -q "Snapshot$snapshot-Media.iso\$" "$ISO.sha256" ||
        die "$ISO is not snapshot $snapshot (see $ISO.sha256); move it away to fetch the current one"
    if [[ ! -s opensuse-key.gpg ]]; then
        # The ISO carries the key with its current expiry date, which the
        # keyservers may lack; only that key, by full fingerprint, is used.
        local short=${OPENSUSE_KEY: -8} keyfile
        keyfile=$(xorriso -indev "$ISO" -find / -maxdepth 1 -name "gpg-pubkey-${short,,}-*.asc" 2>/dev/null |
            tr -d "'" | head -n 1)
        [[ -n $keyfile ]] || die "$ISO doesn't carry the openSUSE Project Signing Key"
        xorriso -osirrox on -indev "$ISO" -extract "$keyfile" "$work/key.asc" 2>/dev/null
        import_key "$work/key.asc"
    fi
    verify "$ISO"

    # Installer kernel and initrd, and the AutoYaST profile.
    xorriso -osirrox on -indev "$ISO" \
        -extract /boot/x86_64/loader/linux "$work/linux" \
        -extract /boot/x86_64/loader/initrd "$work/initrd" 2>/dev/null ||
        die "$ISO lacks /boot/x86_64/loader/linux or initrd"
    credentials
    mkdir "$work/www"
    fill "$here/opensuse/autoinst.xml" >"$work/www/autoinst.xml"
    if grep -n '@[A-Z_]\+@' "$work/www/autoinst.xml"; then die "autoinst.xml placeholders left unfilled"; fi
    python3 -c 'import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])' "$work/www/autoinst.xml" ||
        die "opensuse/autoinst.xml is not well-formed XML"

    local port
    port=$(free_port)
    python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
    server=$!

    qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
    echo "Installing; the installer's console goes to $wdir/install-serial.log"
    # The packages from the DVD (the default install source), not today's
    # repository; self_update=0 likewise keeps the installer as the ISO has it.
    # YaST's "No network running" pop-up, which autoinst.xml's <networking>
    # now avoids: answer-popups.py presses Enter on it should it come all the
    # same (and says so on stderr).
    python3 "$here/opensuse/answer-popups.py" install-serial.log "$work/serial" "No network running" &
    timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
        -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" -machine pc \
        -drive "file=$DISK,if=virtio,format=qcow2" \
        -drive "file=$ISO,media=cdrom,readonly=on" \
        -nic user,ipv6=off,model=virtio-net-pci \
        -kernel "$work/linux" -initrd "$work/initrd" \
        -append "console=ttyS0,115200n8 textmode=1 self_update=0 autoyast=http://10.0.2.2:$port/autoinst.xml" \
        -chardev "socket,id=serial0,path=$work/serial,server=on,wait=off,logfile=install-serial.log" \
        -display none -serial chardev:serial0 -monitor none -no-reboot ||
        die "the install failed or timed out; see install-serial.log"
    grep -aq BACKOTTER-INSTALL-OK install-serial.log || die "the install did not finish; see install-serial.log"
    kill "$server" 2>/dev/null || true; server=""
}

# --- MicroOS: fetch and verify the image, then combustion -----------------------------
install_microos() {
    # In inputs/, not beside the guest's disk: every tool that finds a
    # guest's images by *.qcow2 (vm-update, compare.py) would take it for
    # one.  One linked or left at the top (rebuild-one.sh) moves there.
    local f
    mkdir -p inputs
    for f in "$IMAGE" "$IMAGE.sha256" "$IMAGE.sha256.asc"; do
        if [[ -e $f || -L $f ]] && [[ ! -e inputs/$f ]]; then mv "$f" inputs/; fi
    done
    fetch https://download.opensuse.org/tumbleweed/appliances inputs "$IMAGE.sha256" "$IMAGE.sha256.asc" "$IMAGE"
    [[ $(cut -d' ' -f3 "inputs/$IMAGE.sha256") == "$IMAGE" ]] || die "$IMAGE.sha256 names another file"
    if [[ ! -s opensuse-key.gpg ]]; then
        # No ISO to take the key from: the repository's, which holds it.
        wget -q -O "$work/repomd.xml.key" https://download.opensuse.org/tumbleweed/repo/oss/repodata/repomd.xml.key ||
            die "could not download the repository's repomd.xml.key"
        import_key "$work/repomd.xml.key"
    fi
    verify "inputs/$IMAGE"

    credentials
    fill "$here/opensuse/combustion-microos.sh" >"$work/combustion"
    if grep -n '@[A-Z_]\+@' "$work/combustion"; then die "combustion-microos.sh placeholders left unfilled"; fi
    bash -n "$work/combustion" || die "opensuse/combustion-microos.sh doesn't parse"

    cp -L "inputs/$IMAGE" "$DISK"
    qemu-img resize -q "$DISK" "$DISK_SIZE"
    echo "--uefi" >vm-run-options
    # Combustion takes its script from fw_cfg on this boot only; every boot
    # after it is as vm-run (and every test) boots the guest.
    echo "Booting the image the first time (combustion); its console goes to $wdir/install-serial.log"
    boot_guest install-serial.log -fw_cfg "name=opt/org.opensuse.combustion/script,file=$work/combustion"
    grep -aq BACKOTTER-INSTALL-OK install-serial.log ||
        die "combustion's script did not finish (rolled back?); see install-serial.log"
    # shellcheck disable=SC2016  # expanded in the guest
    "${ssh_root[@]}" '
echo "default=$(btrfs subvolume get-default /)"
echo "booted=$(findmnt -no FSROOT /)"
echo "root=$(findmnt -bno SIZE /) bytes; disk $(lsblk -bndo SIZE /dev/vda) bytes"
snapper --no-dbus list 2>&1 | sed "s/^/snapper: /"
journalctl -b -o cat -t combustion 2>/dev/null | tail -n 20 | sed "s/^/combustion: /"' </dev/null >install.txt 2>&1 || true
    sed "s/^/  install: /" install.txt
    stop_guest
}

# --- Leap: fetch and verify the ISO, then Agama ---------------------------------------
install_leap() {
    fetch "https://download.opensuse.org/distribution/leap/$snapshot/offline" . "$ISO.sha512" "$ISO.sha512.asc" "$ISO"
    [[ $(awk '{ print $2 }' "$ISO.sha512") == "$ISO" ]] || die "$ISO.sha512 names another file"
    if [[ ! -s opensuse-key.gpg ]]; then
        # The ISO carries the key in /install, beside the packages.
        local short=${OPENSUSE_KEY: -8} keyfile
        keyfile=$(xorriso -indev "$ISO" -find /install -maxdepth 1 -name "gpg-pubkey-${short,,}-*.asc" 2>/dev/null |
            tr -d "'" | head -n 1)
        [[ -n $keyfile ]] || die "$ISO doesn't carry the openSUSE Project Signing Key"
        xorriso -osirrox on -indev "$ISO" -extract "$keyfile" "$work/key.asc" 2>/dev/null
        import_key "$work/key.asc"
    fi
    verify "$ISO" sha512

    # The profile: agama-leap.json with the settings and both scripts put in,
    # as autoinst.json on a CD labelled OEMDRV, where Agama looks for one.
    credentials
    mkdir "$work/oem"
    fill "$here/opensuse/agama-leap.json" >"$work/profile.json"
    python3 - "$work/profile.json" "$here/opensuse" "$work/oem/autoinst.json" <<'EOF' || die "opensuse/agama-leap.json: bad JSON or a missing script"
import json, os, re, sys
src, scripts, out = sys.argv[1:]
p = json.load(open(src))
for s in [s for k in p.get("scripts", {}).values() for s in k]:
    m = re.fullmatch(r"@SCRIPT:([\w.-]+)@", s.get("content", ""))
    if m:
        s["content"] = open(os.path.join(scripts, m.group(1))).read()
text = json.dumps(p, indent=2)
if re.search(r"@[A-Z_]+[@:]", text):
    sys.exit("placeholders left unfilled")
open(out, "w").write(text + "\n")
EOF
    xorriso -as mkisofs -quiet -V OEMDRV -J -R -o "$work/oemdrv.iso" "$work/oem" 2>/dev/null ||
        die "could not make the OEMDRV CD"

    qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
    echo "--uefi" >vm-run-options
    echo "Installing (the ISO's menu waits 60 s first); the console goes to $wdir/install-serial.log"
    # By vm-run, as every later boot: OVMF with Secure Boot and Microsoft's
    # keys, the ISO first, the profile's CD second (a CD, so that Agama's
    # proposal can't take it for a disk to install on).
    # shellcheck disable=SC2046  # vm-run-options is words
    timeout "$INSTALL_TIMEOUT" env SSH_PORT="$sshport" MEMORY="$INSTALL_MEMORY" "$here/vm-run" $(<vm-run-options) \
        --cdrom "$ISO" --cpus 4 --serial-log install-serial.log "$DISK" -- \
        -drive "file=$work/oemdrv.iso,media=cdrom,readonly=on" -no-reboot \
        </dev/null >install.vm-run.log 2>&1 || die "the install failed or timed out; see install-serial.log"
    grep -aq BACKOTTER-INSTALL-OK install-serial.log || die "the install did not finish; see install-serial.log"
    sed -n '/^== lsblk/,/BACKOTTER-INSTALL-OK/p' install-serial.log | tr -d '\r' >install.txt
}

# --- Booting the guest ----------------------------------------------------------------
wdir=$2
sshport=$(free_port)
ssh_root=(ssh -q -i id_ed25519 -p "$sshport" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 -o BatchMode=yes root@127.0.0.1)
boot_guest() {  # SERIAL_LOG [QEMU_ARGS...]
    if [[ $mode == tumbleweed ]]; then
        # Booted as vm-run boots it (BIOS, PC machine, virtio), writing to the image.
        qemu-system-x86_64 \
            -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
            -drive "file=$DISK,if=virtio,format=qcow2" \
            -nic "user,ipv6=off,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$sshport-:22" \
            -display none -serial "file:$1" -monitor none "${@:2}" &
    else
        # By vm-run itself, with vm-run-options, writing to the image.
        # shellcheck disable=SC2046  # vm-run-options is words
        SSH_PORT=$sshport MEMORY=$MEMORY "$here/vm-run" $(<vm-run-options) --serial-log "$1" "$DISK" -- "${@:2}" \
            </dev/null >"${1%.log}.vm-run.log" 2>&1 &
    fi
    qemu_pid=$!
    local _
    for _ in $(seq $((BOOT_TIMEOUT / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || die "QEMU exited before the guest answered on SSH; see $wdir/$1"
        timeout 30 "${ssh_root[@]}" true </dev/null 2>/dev/null && return 0
        sleep 5
    done
    die "the guest never answered on SSH (emergency mode?); see $wdir/$1"
}
stop_guest() {
    "${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || true
    local _
    for _ in $(seq 60); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
    kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
    wait "$qemu_pid" 2>/dev/null || true
    qemu_pid=""
}
# One KEY=VALUE line per finding.  mislabelled: files under the system's
# file systems whose label isn't the policy's (restorecon -n; the snapshots
# left out, / being one of them), the first few of them as "mislabel=".
# Its list goes in /run, which a read-only / (microos) has writable too.
# shellcheck disable=SC2016  # expanded in the guest
check_cmd='
echo "state=$(systemctl is-system-running --wait)"
echo "enforce=$(getenforce 2>&1)"
for f in /.autorelabel /etc/selinux/.autorelabel; do [ ! -e $f ] || echo "trigger=$f"; done
! grep -qw autorelabel /proc/cmdline || echo "trigger=autorelabel on /proc/cmdline"
echo "relabelled=$(stat -c %y /etc/selinux/.relabelled 2>/dev/null || echo none)"
systemctl list-units --all --no-legend --plain "*-relabel.service" mark-autorelabel-done.service | sed "s/^/relabel-unit=/"
systemctl --failed --no-legend --plain | sed "s/^/failed=/"
restorecon -R -n -v -e /.snapshots -e /proc -e /sys -e /dev -e /run / >/run/mislabelled 2>&1
echo "mislabelled=$(grep -c "Would relabel" /run/mislabelled)"
grep "Would relabel" /run/mislabelled | head -n 20 | sed "s/^/mislabel=/"; rm -f /run/mislabelled
'
# MicroOS's transactional root: / mounted ro, the booted snapshot the btrfs
# default (health-checker sets that back to the last good one when a new
# snapshot fails its first boot), health-checker's verdict and state, the
# automatic updates off, Secure Boot's state, the root's size.
# shellcheck disable=SC2016  # expanded in the guest
[[ $mode == microos ]] && check_cmd+='
echo "root-options=$(findmnt -no OPTIONS /)"
d=$(btrfs subvolume get-default /); echo "default=$d"
b=$(findmnt -no FSROOT /); echo "booted=$b"
[ "${b#/}" = "${d##* path }" ] && echo "booted-is-default=yes" || echo "booted-is-default=no"
echo "health-checker=$(systemctl is-active health-checker.service)"
sed "s/^/health-check.state: /" /var/lib/misc/health-check.state 2>&1
echo "tu-timer=$(systemctl is-enabled transactional-update.timer 2>&1)"
echo "rebootmgr=$(systemctl is-enabled rebootmgr.service 2>&1)"
echo "sb=$(mokutil --sb-state 2>&1 | head -n 1)"
echo "root-size=$(findmnt -bno SIZE /) bytes; disk $(lsblk -bndo SIZE /dev/vda) bytes"
snapper --no-dbus list 2>&1 | sed "s/^/snapper: /"
'
# Leap: Secure Boot's state (it must be on: the guest boots through
# shim), the root snapshot booted and the btrfs default, snapper's
# snapshots, and the ESP's files.
# shellcheck disable=SC2016  # expanded in the guest
[[ $mode == leap ]] && check_cmd+='
echo "sb=$(mokutil --sb-state 2>&1 | head -n 1)"
echo "default=$(btrfs subvolume get-default /)"
echo "booted=$(findmnt -no FSROOT /)"
echo "boot-current=$(efibootmgr 2>/dev/null | sed -n "s/^BootCurrent: //p")"
find /boot/efi -type f | sort | sed "s/^/esp: /"
snapper --no-dbus list 2>&1 | sed "s/^/snapper: /"
'
check_boot() {  # NAME: boot, check, power off; the findings in NAME.txt
    echo "Booting the guest ($1); its console goes to $wdir/$1-serial.log"
    boot_guest "$1-serial.log"
    "${ssh_root[@]}" "$check_cmd" </dev/null >"$1.txt" 2>&1 || true
    stop_guest
    sed "s/^/  $1: /" "$1.txt"
    local bad=""
    grep -qx 'state=running' "$1.txt" || bad+=" not running;"
    grep -qx 'enforce=Enforcing' "$1.txt" || bad+=" not enforcing;"
    ! grep -q '^trigger=' "$1.txt" || bad+=" a relabel trigger is left;"
    ! grep -q '^failed=' "$1.txt" || bad+=" failed units;"
    if [[ $mode == microos ]]; then
        grep -qE '^root-options=(.*,)?ro(,|$)' "$1.txt" || bad+=" / is not mounted ro;"
        grep -qx 'booted-is-default=yes' "$1.txt" || bad+=" the booted snapshot is not the default;"
        grep -qx 'tu-timer=disabled' "$1.txt" || bad+=" transactional-update.timer is not disabled;"
        grep -qx 'rebootmgr=disabled' "$1.txt" || bad+=" rebootmgr is not disabled;"
    fi
    if [[ $mode == leap ]]; then
        grep -qx 'sb=SecureBoot enabled' "$1.txt" || bad+=" Secure Boot is not on;"
    fi
    [[ -z $bad ]] || die "$1:$bad see $wdir/$1.txt and $wdir/$1-serial.log"
}

# --- The build ------------------------------------------------------------------------
"install_$mode"
# The first boot does the relabel the install left pending (and whatever
# else a first boot does: SSH host keys, ...).
check_boot relabel-boot
# The image as it ships: no relabel at all, so no relabel unit either.
check_boot check-boot
! grep -q '^relabel-unit=' check-boot.txt || die "the check boot relabelled again; see $2/check-boot.txt"

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\", in $((SECONDS - t0)) s"
