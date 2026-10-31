#!/usr/bin/env bash
# Build a Haiku test guest for BackOtter from Haiku's official anyboot image,
# under QEMU with KVM.  Haiku has no unattended installer and no console on
# the serial port: the live image boots to a graphical welcome window.  So
# this boots it with QEMU's monitor on a socket, and haiku/drive.py presses
# the keys: "Try Haiku", then a Terminal, then a command that fetches and runs
# haiku/install.sh from a small HTTP server here.  The guest then boots once
# on its own, finishing Haiku's first-boot work, and powers off.
#
# Usage: ./make-haiku.sh RELEASE WORK_DIR
#   e.g. ./make-haiku.sh r1beta6 vms/haiku6
#
# RELEASE is a release in https://haiku-release.cdn.haiku-os.org/ (r1betaN).
# Its haiku-RELEASE-x86_64-anyboot.iso and .minisig are downloaded into
# WORK_DIR unless already there, and checked with minisign against Haiku's
# signing key (haiku-2019.pub, on haiku-os.org's download page and in every
# Haiku at /boot/system/data/trust_db).  The "ISO" is a hybrid: an MBR disk
# image with a BFS partition, which is how it's used here.
# Results, in WORK_DIR (N is the beta's number, e.g. 6):
#   haikuN.qcow2          the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" after its first boot
#   id_ed25519, .pub      SSH key that logs in as "user", Haiku's only user
#                         (uid 0); Haiku has no passwords
#   install.log           haiku/install.sh's log, from the live system
#   install-screen.png    the screen, if the install went wrong
#   first-boot-serial.log the guest's first boot: the kernel's log
#   first-boot-screen.png the screen, if the first boot never answered on SSH
#   vm-run-options        "--disk-bus sata", how vm-run must boot it
#
# The guest, host name haikuN: MBR with one BFS partition, BIOS boot (Haiku's
# MBR and boot loader), Haiku's own swap file, 2 GB RAM, DHCP, sshd started
# by the desktop session's UserBootscript, the kernel's log on the serial port
# (Haiku logs nothing else there; there's no login on it).  Run it with vm-run
# and vm-run-options: its disk is on SATA (AHCI), since R1/beta6 reportedly
# can't boot from virtio-blk.  SSH in as "user":
#   ssh -i WORK_DIR/id_ed25519 -p 2222 user@127.0.0.1
# vm-mount can't open it: Linux has no read-write BFS driver.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, minisign, sfdisk, curl,
# python3, ssh.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
INSTALL_TIMEOUT=900             # the live boot and install, in seconds
BOOT_TIMEOUT=900                # the first boot, until it has powered off
SIGNING_KEY=RWTPfbfFRi5b+T02aw733p5dp/UI/1Z946YWBInmkPmBlx2Zy1Vyw9kP    # haiku-2019.pub
TERMINAL_TRIES=3

here=$(dirname "$(realpath "$0")")
PATH=$PATH:/sbin:/usr/sbin
die() { echo "make-haiku: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 RELEASE WORK_DIR"
release=$1
[[ $release =~ ^r1beta[0-9]+$ ]] || die "RELEASE looks like r1beta6, not '$release'"
IMAGE=haiku-$release-x86_64-anyboot.iso
url=https://haiku-release.cdn.haiku-os.org/$release
guest_name=haiku${release#r1beta}
DISK=$guest_name.qcow2
out=$2
mkdir -p "$out"
cd "$out"
[[ -e $DISK ]] && die "$out/$DISK already exists; move it away to rebuild"

# --- Fetch and check the image ------------------------------------------------------
for f in "$IMAGE.minisig" "$IMAGE"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    curl -fsSL -C - -o "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
minisign -Vqm "$IMAGE" -P "$SIGNING_KEY" || die "$IMAGE does not match its signature"

# --- The disk, the key and the server ------------------------------------------------
work=$(mktemp -d)
raw=$(mktemp -p . --suffix=.raw disk.XXXXXX)
qemu_pid="" server_pid=""
cleanup() {
    # || true: under set -e, a kill of a process that's already gone would
    # end the trap before the rest.
    [[ -z $qemu_pid ]] || kill "$qemu_pid" 2>/dev/null || true
    [[ -z $server_pid ]] || kill "$server_pid" 2>/dev/null || true
    rm -rf "$work" "$raw"
}
trap cleanup EXIT

truncate -s "$DISK_SIZE" "$raw"
# One partition, type 0xeb (BFS), active: install.sh makes the file system,
# makebootable its boot record and writembr the MBR's boot code.
printf 'label: dos\nstart=2048, type=eb, bootable\n' | sfdisk -q "$raw"

[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "user@$guest_name test guest" -f id_ed25519
mkdir "$work/www"
cp id_ed25519.pub "$work/www/authorized_keys"
python3 "$here/haiku/drive.py" serve "$work/www" "$work/port" 2>"$work/http.log" &
server_pid=$!
for _ in $(seq 50); do [[ -s $work/port ]] && break; sleep 0.1; done
[[ -s $work/port ]] || die "the HTTP server didn't start"
# The guest reaches this host's 127.0.0.1 as 10.0.2.2 (QEMU's user network).
server=http://10.0.2.2:$(<"$work/port")
sed -e "s|@SERVER@|$server|" -e "s|@HOSTNAME@|$guest_name|" "$here/haiku/install.sh" \
    >"$work/www/install.sh"
if grep -n '@[A-Z_]\+@' "$work/www/install.sh"; then die "install.sh placeholders left unfilled"; fi

# --- Live boot and install -----------------------------------------------------------
drive() { python3 "$here/haiku/drive.py" "$work/mon.sock" "$@"; }
install_failed() {
    drive shot install-screen.png 2>/dev/null || true
    die "$* (screen in $out/install-screen.png)"
}

echo "Installing from the live image; the log comes back to $out/install.log"
rm -f install.log install-screen.png
# Both copies of the image with snapshot=on: neither is written to (AHCI
# disks can't be read-only).
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -device ich9-ahci,id=ahci0 \
    -drive "file=$IMAGE,format=raw,if=none,id=live,snapshot=on" -device ide-hd,drive=live,bus=ahci0.0 \
    -drive "file=$raw,format=raw,if=none,id=disk" -device ide-hd,drive=disk,bus=ahci0.1 \
    -drive "file=$IMAGE,format=raw,if=none,id=source,snapshot=on" -device ide-hd,drive=source,bus=ahci0.2 \
    -nic user,model=e1000 -vga std -display none -serial none \
    -monitor "unix:$work/mon.sock,server,nowait" -no-reboot &
qemu_pid=$!

drive wait-welcome 600 || install_failed "the welcome window never came up"
drive try-haiku
fetched=no
for _ in $(seq "$TERMINAL_TRIES"); do
    drive terminal
    drive type "curl -fsS $server/install.sh | bash"$'\n'
    for _ in $(seq 30); do
        grep -q '"GET /install.sh ' "$work/http.log" && { fetched=yes; break; }
        sleep 1
    done
    [[ $fetched == yes ]] && break
done
[[ $fetched == yes ]] || install_failed "couldn't get a Terminal to run install.sh"
wait "$qemu_pid" || true         # install.sh powers the live system off
qemu_pid=""
[[ -f $work/www/install.log ]] && cp "$work/www/install.log" install.log
[[ $(cat "$work/www/install.status" 2>/dev/null) == OK ]] ||
    die "install.sh failed or timed out: $(cat "$work/www/install.status" 2>/dev/null || echo "no status"); see $out/install.log"

qemu-img convert -f raw -O qcow2 "$raw" "$DISK"
rm -f "$raw"
echo "--disk-bus sata" >vm-run-options

# --- First boot ----------------------------------------------------------------------
# Haiku's first boot runs the packages' first-boot scripts (sshd's host keys
# among them).  Power off through sshd, never by killing QEMU: BFS would keep
# its metadata but lose the files' data written since the last sync.
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
echo "First boot; its kernel log goes to $out/first-boot-serial.log"
rm -f first-boot-screen.png
# The monitor is for a screenshot if it goes wrong.
# shellcheck disable=SC2046
SSH_PORT=$port timeout "$BOOT_TIMEOUT" "$here/vm-run" $(<vm-run-options) \
    --serial-log first-boot-serial.log "$DISK" -- -monitor "unix:$work/mon.sock,server,nowait" &
qemu_pid=$!
ssh_guest() {
    # timeout: QEMU's user network accepts the connection before the guest
    # is up, so ConnectTimeout never fires.
    timeout 20 ssh -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes \
        user@127.0.0.1 "$@" </dev/null
}
# Give up a minute before timeout would kill QEMU, for the screenshot.
up=no deadline=$((SECONDS + BOOT_TIMEOUT - 60))
while (( SECONDS < deadline )); do
    if [[ $(ssh_guest uname -n 2>/dev/null) == "$guest_name" ]]; then up=yes; break; fi
    kill -0 "$qemu_pid" 2>/dev/null || break
    sleep 10
done
if [[ $up == no ]]; then
    drive shot first-boot-screen.png 2>/dev/null || true
    die "the first boot never answered on SSH; see $out/first-boot-serial.log and first-boot-screen.png"
fi
ssh_guest 'sync; shutdown -q' 2>/dev/null || true    # sshd goes with it
wait "$qemu_pid" || die "the first boot didn't power off in time"
qemu_pid=""

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $out/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
