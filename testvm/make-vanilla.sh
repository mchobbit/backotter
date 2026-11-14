#!/usr/bin/env bash
# Build a Vanilla OS 3 "Reunion" test guest for BackOtter from Vanilla's own
# live ISO, with Vanilla's own installer backend, unattended: the GUI
# installer's recipe generator (vanilla-installer's processor.py) for an
# automatic install, then the ISO's albius with that recipe (vanilla/install.sh
# says how, and where the guest differs), into a qcow2 disk image, under QEMU
# with KVM and OVMF.  Then one "abroot upgrade --force", so that both ABRoot
# roots hold a system and boot, and a boot into each.
#
# Usage: ./make-vanilla.sh [--iso-dir DIR] [--dry-run] FLAVOR WORK_DIR
#   e.g. ./make-vanilla.sh --iso-dir /bkoscratch/rebuild-inputs/vanilla3 vanilla3 vms/vanilla3
#
# --dry-run: boot the live system and make the recipe, but stop before
# albius (nothing is written to the disk, nothing pulled): WORK_DIR gets
# recipe.json, install.log and install-serial.log only.  For a new ISO.
#
# FLAVOR is the guest:
#   vanilla3       the installer's automatic layout, no encryption, Secure
#                  Boot on (the guest's own shim and Debian's signed GRUB)
#   vanilla3-luks  (not built yet: docs/design/vanillaos.md's V10) the
#                  installer's encryption option, LUKS2 on vos-var/var
#
# The ISO, Vanilla-OS-3-stable-amd64.20261106.iso with its .sha256.txt, is
# looked for in DIR (default WORK_DIR) and downloaded there if missing (from
# GitHub, 16 ranged connections at once: one gets about 0.4 MB/s), then
# checked against the SHA-256 pinned below (Vanilla publishes no signature,
# only the .sha256.txt, which is checked too).  Unlike the other live-ISO
# builders, the ISO isn't the whole install: albius pulls the system image
# ghcr.io/vanilla-os/gnome:latest (recipe.json's default image; 98 layers,
# about 10 minutes from ghcr.io), so the guest is whatever that tag is at the
# build.  The upgrade below takes the cached image unless the tag moved in
# between.  install.log's /abimage.abr, and first-boot.txt's, record both
# roots' digests.  The live system's own /etc/os-release is live-build's
# (ID=ubuntu); the installed one is base-files' (ID=vanilla, VERSION_ID 3.0).
#
# The live system has no SSH server and no serial login worth driving, so it
# is given its work as a systemd unit: an SMBIOS credential
# (systemd.extra-unit.bko-install.service, read by systemd-debug-generator,
# systemd >= 256) and systemd.wants= on the kernel's command line.  The
# live kernel and initrd are booted directly, with the ISO's own live
# options (its GRUB's first entry) less quiet and splash, plus the serial
# console and multi-user.target (no GNOME, no installer GUI).  The unit
# mounts a second CD (label BKOINST: install.sh, filled in) and runs it,
# its output to the second serial port (install.log), then powers off.
#
# Then three boots from the disk, each as vm-run --uefi boots it: Secure
# Boot on, fresh variables (OVMF_VARS_4M.ms.fd), so through the fallback
# loader EFI/BOOT/BOOTX64.EFI (shim, then GRUB):
#   1. the first boot, root A (vos-a): its checks (first-boot.txt), the mark
#      /etc/bko-root "a" (in vos-a's /etc upper), then abroot upgrade
#      --force: no newer image is needed; it fills root B (vos-b) from the
#      cached image, builds init/vos-b/ (kernel, initramfs, abroot.cfg, with
#      /etc/abroot/kargs' serial console) and swaps the master grub.cfg, so
#      B is the default ("set default=1", "Current State (B)").
#   2. the default entry, root B: checks, the mark /etc/bko-root "b".  (B's
#      abroot.cfg is its own grub-mkconfig output: a second menu with one
#      entry, timeout 0, gfxterm only, so it boots at once.)
#   3. "Previous State (A)", picked on GRUB's serial menu (console-keys.py),
#      root A again: checks that its mark is still "a".
#
# Results, in WORK_DIR:
#   FLAVOR.qcow2          the guest's 64 GiB disk (sparse; the installer's
#                         minimum is 50 GiB), with a qcow2 snapshot
#                         "fresh-install" taken after the three boots: B
#                         the default (abroot status: present vos-b, future
#                         vos-a, when booted so), A the "Previous State"
#   uefi-vars.fd          the OVMF variables of the install, with Albius'
#                         NVRAM entry "vanilla", for OVMF_CODE_4M.fd on a
#                         plain q35 machine (Secure Boot off); vm-run --uefi
#                         boots a fresh store with Secure Boot instead
#   root-password         the password of root and of "tester" (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   recipe.json           the Albius recipe, the installer's own with this
#                         guest's two steps appended (no secrets: those are in
#                         guest.sh, which deletes itself)
#   install.log           the install script's output (the second serial port)
#   upgrade.log           abroot upgrade --force's output (with its progress
#                         bars: 75 MB)
#   install-serial.log, first-boot-serial.log, b-boot-serial.log,
#   a-boot-serial.log     the serial consoles
#   first-boot.txt        what the boots showed (vanilla/probe.sh: abroot
#                         status, mounts, LVM, both roots' init files and
#                         abroot.cfg, overlay xattrs and whiteouts, LV UUIDs
#                         named anywhere, room), and the build's time
#   vm-run-options        "--uefi --memory 4096", how vm-run must boot it
#
# The guest vanilla3, host name vanilla3: UEFI + GPT, the installer's
# automatic layout (sizes in MiB): vda1 "vos-boot" 1-1025 ext4 at /boot, vda2
# "vos-efi" 1025-1537 ESP at /boot/efi, vda3 PV of VG vos-root (init 1024
# linear ext4 vos-init; thin pool root of 19456 with thin LVs root-a, root-b,
# btrfs vos-a, vos-b), vda4 PV of VG vos-var (var, btrfs vos-var, /var); the
# running / is the present root read-only, /etc an overlay with its upper in
# /var/lib/abroot/etc/<label>.  The first-setup user "vanilla" as the
# installer makes it, GNOME installed but booting to text mode, sshd on,
# vanilla-updates.timer masked, 4 GB RAM.  About 21 GiB written (each root
# ~6 GiB, /var ~7.8 GiB with the cached image); a build takes ~17 minutes and
# ~22 GiB of WORK_DIR at its peak.
#
# Needs: qemu-system-x86_64 with KVM, OVMF (with the Secure Boot build and
# its Microsoft-keys variable store), qemu-img, xorriso, openssl, python3,
# curl, ssh, and the network (ghcr.io) for the install VM.  Runs as an
# ordinary user in the kvm group.

set -euo pipefail

ISO=Vanilla-OS-3-stable-amd64.20261106.iso
ISO_SHA256=78e81fc55310edda40c6296b972da1508d76fed8f56ecaf889db311f6f20f70d
ISO_URL=https://github.com/Vanilla-OS/live-iso/releases/download/3.0
DISK_SIZE=64G
MEMORY=4096
INSTALL_MEMORY=6144
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
OVMF_SB_CODE=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd
OVMF_SB_VARS=/usr/share/OVMF/OVMF_VARS_4M.ms.fd
INSTALL_TIMEOUT=7200
UPGRADE_TIMEOUT=5400

here=$(dirname "$(realpath "$0")")
die() { echo "make-vanilla: $*" >&2; exit 1; }

iso_dir="" dry_run=no
while [[ $# -gt 0 ]]; do
    case $1 in
        --iso-dir) iso_dir=$(realpath "$2"); shift 2 ;;
        --dry-run) dry_run=yes; shift ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || die "usage: $0 [--iso-dir DIR] [--dry-run] FLAVOR WORK_DIR"
flavor=$1
case $flavor in
    vanilla3) ENCRYPT=no ;;
    vanilla3-luks) die "vanilla3-luks isn't built yet (docs/design/vanillaos.md, V10)" ;;
    *) die "FLAVOR is vanilla3, not '$flavor'" ;;
esac
GUEST_NAME=$flavor
DISK=$GUEST_NAME.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"
[[ -n $iso_dir ]] || iso_dir=$PWD
for f in "$OVMF_CODE" "$OVMF_VARS" "$OVMF_SB_CODE" "$OVMF_SB_VARS"; do [[ -f $f ]] || die "no $f"; done
t_start=$(date +%s)

# --- Fetch and verify the ISO -------------------------------------------------------
fetch() {  # NAME
    local f=$1 size n=16 i from to pids=()
    size=$(curl -sfIL "$ISO_URL/$f" | tr -d '\r' | awk 'tolower($1) == "content-length:" { s = $2 } END { print s }')
    [[ $size =~ ^[0-9]+$ ]] || die "no size for $ISO_URL/$f"
    if [[ $size -lt $((64 << 20)) ]]; then
        curl -sfL -o "$iso_dir/$f" "$ISO_URL/$f" || die "could not download $ISO_URL/$f"
        return
    fi
    for ((i = 0; i < n; i++)); do
        from=$((size * i / n)) to=$((size * (i + 1) / n - 1))
        curl -sfL --retry 5 -r "$from-$to" -o "$iso_dir/$f.part.$i" "$ISO_URL/$f" &
        pids+=($!)
    done
    for i in "${pids[@]}"; do wait "$i" || die "could not download $ISO_URL/$f"; done
    for ((i = 0; i < n; i++)); do cat "$iso_dir/$f.part.$i"; done >"$iso_dir/$f.joined"
    [[ $(stat -c %s "$iso_dir/$f.joined") -eq $size ]] || die "$f: got the wrong size"
    mv "$iso_dir/$f.joined" "$iso_dir/$f"
    rm -f "$iso_dir/$f".part.*
}
sums=${ISO%.iso}.sha256.txt
for f in "$sums" "$ISO"; do
    [[ -f $iso_dir/$f && ! -f $iso_dir/$f.part ]] && continue
    echo "Downloading $ISO_URL/$f"
    touch "$iso_dir/$f.part"
    fetch "$f"
    rm "$iso_dir/$f.part"
done
[[ $(sha256sum <"$iso_dir/$ISO" | cut -d' ' -f1) == "$ISO_SHA256" ]] ||
    die "$ISO's SHA-256 isn't the pinned $ISO_SHA256"
[[ $(awk '{ print $1; exit }' "$iso_dir/$sums") == "$ISO_SHA256" ]] || die "$sums doesn't give the pinned SHA-256"

work=$(mktemp -d)
qemu_pid=""
cleanup() {
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
trap cleanup EXIT

# --- The live system's kernel, initrd and boot options ------------------------------
# Debian live-build: the kernel and initrd in /live, the options in the
# first "linux" line of its GRUB's grub.cfg (the EFI boot's): less quiet
# and splash, and less findiso=${iso_path} (GRUB's loopback boot only);
# plus noprompt (else live-boot's shutdown waits for Enter to eject the CD).
xorriso -osirrox on -indev "$iso_dir/$ISO" -extract /live/vmlinuz "$work/vmlinuz" -extract /live/initrd.img "$work/initrd" \
    -extract /boot/grub/grub.cfg "$work/grub.cfg" 2>/dev/null || die "could not extract $ISO's /live/vmlinuz, initrd.img and grub.cfg"
chmod u+w "$work/vmlinuz" "$work/initrd" "$work/grub.cfg"
kernel=$work/vmlinuz initrd=$work/initrd
options=$(grep -m1 -E '^[[:space:]]*linux[[:space:]]+/live/vmlinuz' "$work/grub.cfg" |
    sed -E 's/^[[:space:]]*linux[[:space:]]+[^[:space:]]+//; s/(^|[[:space:]])(quiet|splash|findiso=[^[:space:]]*)([[:space:]]|$)/ /g; s/(^|[[:space:]])(quiet|splash)([[:space:]]|$)/ /g; s/[[:space:]]+/ /g; s/^ //; s/ $//')
[[ $options == *boot=live* && $options != *'$'* ]] || die "unexpected live entry in the ISO's grub.cfg: $options"

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
umask 022

# The second CD: install.sh, filled in.
mkdir "$work/cd"
sed -e "s|@HOSTNAME@|$GUEST_NAME|" -e "s|@ROOT_PASSWORD_HASH@|$hash|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" -e "s|@ENCRYPT@|$ENCRYPT|" -e "s|@DRY_RUN@|$dry_run|" \
    "$here/vanilla/install.sh" >"$work/cd/install.sh"
if grep -n '@[A-Z_0-9]\+@' "$work/cd/install.sh"; then die "install.sh placeholders left unfilled"; fi
xorriso -as mkisofs -quiet -V BKOINST -J -R -o "$work/bkoinst.iso" "$work/cd" 2>/dev/null || die "could not make the BKOINST CD"
# The unit, as the live systemd's credential (systemd expands $$ to $).
# It powers off at once (-ff: install.sh has unmounted the target and
# synced): an orderly shutdown ends at live-boot's "Please remove the
# live-medium ... and press ENTER", noprompt or not.
cat >"$work/bko-install.service" <<'EOF'
[Unit]
Description=BackOtter: unattended Vanilla OS install (make-vanilla.sh)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
StandardOutput=journal+console
StandardError=journal+console
ExecStart=/bin/bash -c 'mkdir -p /run/bko-cd; for i in $$(seq 60); do mount -o ro /dev/disk/by-label/BKOINST /run/bko-cd 2>/dev/null && break; sleep 2; done; /bin/bash /run/bko-cd/install.sh 2>&1 | tee /dev/ttyS1'
ExecStopPost=/bin/sh -c 'sync; systemctl poweroff -ff'
EOF

port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
ssh_root=(ssh -q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 -o ServerAliveCountMax=10 root@127.0.0.1)
wait_ssh() {  # SECONDS
    local _
    for _ in $(seq $(($1 / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || return 1
        timeout 20 "${ssh_root[@]}" true </dev/null 2>/dev/null && return 0
        sleep 5
    done
    return 1
}
stop_guest() {
    timeout 30 "${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || true
    local _
    for _ in $(seq 90); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
    kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
    wait "$qemu_pid" 2>/dev/null || true
    qemu_pid=""
}
probe() {  # STAGE TITLE: vanilla/probe.sh in the guest, its output added to first-boot.txt and kept in $work/STAGE.txt
    { echo "=== $2"; timeout 600 "${ssh_root[@]}" bash -s "$1" <"$here/vanilla/probe.sh" 2>&1; } | tee "$work/$1.txt" >>first-boot.txt || true
}
val() { sed -n "s/^BKO $2=//p" "$work/$1.txt" | head -1; }   # STAGE KEY
# Each boot as vm-run --uefi's: Secure Boot's firmware, SMM, a fresh copy
# of the Microsoft-keys variable store, so the fallback loader.
sb_args() {  # VARS_FILE
    cp "$OVMF_SB_VARS" "$1"
    echo -machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on \
        -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_SB_CODE" -drive "if=pflash,format=raw,unit=1,file=$1"
}
vm=(-enable-kvm -cpu host -smp 4 -m "$MEMORY" -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap"
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22" -display none -monitor none)

# --- Install --------------------------------------------------------------------------
cp "$OVMF_VARS" uefi-vars.fd
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the install script's output goes to $2/install.log"
rm -f install.log install-serial.log
qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" -machine q35 \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" -drive "if=pflash,format=raw,unit=1,file=uefi-vars.fd" \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" \
    -drive "file=$iso_dir/$ISO,media=cdrom,readonly=on" \
    -drive "file=$work/bkoinst.iso,media=cdrom,readonly=on" \
    -nic "user,model=virtio-net-pci" \
    -smbios "type=11,value=io.systemd.credential.binary:systemd.extra-unit.bko-install.service=$(base64 -w0 "$work/bko-install.service")" \
    -kernel "$kernel" -initrd "$initrd" \
    -append "$options noprompt console=tty0 console=ttyS0,115200n8 systemd.unit=multi-user.target systemd.wants=bko-install.service" \
    -display none -serial file:install-serial.log -serial file:install.log -monitor none -no-reboot &
qemu_pid=$!
for ((i = 0; i < INSTALL_TIMEOUT / 10; i++)); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 10; done
if kill -0 "$qemu_pid" 2>/dev/null; then die "the install didn't finish in $INSTALL_TIMEOUT s; see install.log, install-serial.log"; fi
wait "$qemu_pid" 2>/dev/null || true; qemu_pid=""
# (Less the firmware's screen-clearing escapes: OVMF writes to every serial port.)
sed -i -E 's/\r$//; s/\x1b(\[[0-9;=?]*[A-Za-z]|c)//g' install.log
if [[ $dry_run == yes ]]; then
    sed -n '/^BKO-RECIPE-BEGIN$/,/^BKO-RECIPE-END$/p' install.log | sed '1d;$d' >recipe.json
    grep -q '^BACKOTTER-INSTALL-DRY' install.log || { tail -n 30 install.log >&2; die "the dry run failed; see install.log"; }
    rm -f "$DISK" uefi-vars.fd
    echo "Dry run done: $2/recipe.json"
    exit 0
fi
grep -q '^BACKOTTER-INSTALL-OK' install.log || {
    tail -n 30 install.log >&2
    die "the install did not finish; see install.log, install-serial.log"
}
sed -n '/^BKO-RECIPE-BEGIN$/,/^BKO-RECIPE-END$/p' install.log | sed '1d;$d' >recipe.json
python3 -I -c 'import json, sys; json.load(open(sys.argv[1]))' recipe.json || die "install.log has no readable recipe"
t_install=$(date +%s)

# --- 1. The first boot: root A; checks; the mark "a"; the upgrade into B ------------
rm -f first-boot.txt
qemu-system-x86_64 "${vm[@]}" $(sb_args "$work/vars1.fd") -serial file:first-boot-serial.log &
qemu_pid=$!
wait_ssh 600 || die "the first boot never answered on SSH; see first-boot-serial.log"
probe first "first boot (Secure Boot, fresh variables)"
[[ $(val first root_label) == vos-a ]] || die "the first boot's root isn't vos-a: $(val first root_label)"
[[ $(val first sb_state) == *enabled* ]] || die "the first boot isn't under Secure Boot: $(val first sb_state)"
[[ $(val first cmdline) == *console=ttyS0,115200n8* ]] || die "the first boot's command line has no serial console"
[[ $(val first running) =~ ^(running|degraded)$ ]] || die "the first boot isn't up: $(val first running)"
[[ $(val first timer) == masked ]] || die "vanilla-updates.timer isn't masked"
[[ $(val first other_has_usr) == no ]] || echo "make-vanilla: note: a fresh install's root B isn't empty" >&2
timeout 60 "${ssh_root[@]}" 'echo a >/etc/bko-root; sync; cat /var/lib/abroot/etc/vos-a/bko-root' </dev/null | grep -qx a ||
    die "/etc/bko-root didn't land in vos-a's upper"
echo "Upgrading into root B; the output goes to $2/upgrade.log"
timeout "$UPGRADE_TIMEOUT" "${ssh_root[@]}" 'abroot upgrade --force; echo "BKO-UPGRADE-EXIT $?"' </dev/null >upgrade.log 2>&1 || true
grep -q '^BKO-UPGRADE-EXIT 0$' upgrade.log || { tail -n 30 upgrade.log >&2; die "abroot upgrade --force failed; see upgrade.log"; }
probe upgraded "after abroot upgrade --force"
[[ $(val upgraded default) == 1 ]] || die "the upgrade didn't make entry 1 (B) the default: $(val upgraded default)"
[[ $(val upgraded other_has_usr) == yes ]] || die "root B is still empty after the upgrade"
[[ $(val upgraded init_vos-b) == *vmlinuz-* && $(val upgraded init_vos-b) == *initrd.img-* && $(val upgraded init_vos-b) == *abroot.cfg* ]] ||
    die "init/vos-b lacks a kernel, initramfs or abroot.cfg: $(val upgraded init_vos-b)"
[[ $(val upgraded abroot_cfg_vos-b) == *console=ttyS0,115200n8* ]] || die "vos-b's abroot.cfg has no serial console (kargs)"
stop_guest

# --- 2. The default entry: root B; the mark "b" ---------------------------------------
qemu-system-x86_64 "${vm[@]}" $(sb_args "$work/vars2.fd") -serial file:b-boot-serial.log &
qemu_pid=$!
wait_ssh 600 || die "the boot into B never answered on SSH; see b-boot-serial.log"
probe b-before "boot 2: the default entry (B), before its mark"
[[ $(val b-before root_label) == vos-b ]] || die "the default entry didn't boot vos-b: $(val b-before root_label)"
[[ $(val b-before running) =~ ^(running|degraded)$ ]] || die "the boot into B isn't up: $(val b-before running)"
[[ $(val b-before timer) == masked ]] || die "vanilla-updates.timer isn't masked in B"
timeout 60 "${ssh_root[@]}" 'echo b >/etc/bko-root; sync; cat /var/lib/abroot/etc/vos-b/bko-root' </dev/null | grep -qx b ||
    die "/etc/bko-root didn't land in vos-b's upper"
probe b "boot 2: root B, marked"
[[ $(val b bko_root) == b && $(val b upper_a_bko_root) == a ]] || die "the marks are wrong in B: $(val b bko_root), A's $(val b upper_a_bko_root)"
stop_guest

# --- 3. "Previous State (A)" on GRUB's serial menu: root A ----------------------------
# (After the upgrade the entries keep their order: 0 is A, now "Previous
# State (A)", 1 is B, the default.  Up twice, then Enter.)
python3 "$here/vanilla/console-keys.py" a-boot-serial.log 'Previous State \(A\)' '\x1b[A|\x1b[A|\r' -- \
    qemu-system-x86_64 "${vm[@]}" $(sb_args "$work/vars3.fd") -serial stdio </dev/null >/dev/null &
qemu_pid=$!
wait_ssh 600 || die "the boot into the previous root never answered on SSH; see a-boot-serial.log"
probe a "boot 3: GRUB's Previous State (A)"
grep -q 'console-keys.py: typed' a-boot-serial.log || die "GRUB's menu never showed on the serial console"
[[ $(val a root_label) == vos-a ]] || die "Previous State didn't boot vos-a: $(val a root_label)"
[[ $(val a bko_root) == a ]] || die "root A's mark isn't a: $(val a bko_root)"
[[ $(val a running) =~ ^(running|degraded)$ ]] || die "the boot into A isn't up: $(val a running)"
stop_guest

qemu-img snapshot -c fresh-install "$DISK"
echo "--uefi --memory 4096" >vm-run-options
t_end=$(date +%s)
{ echo "=== build: $((t_end - t_start)) s (install $((t_install - t_start)) s, boots and upgrade $((t_end - t_install)) s)"
  echo "=== $DISK: $(du -h "$DISK" | cut -f1) on disk, $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["virtual-size"])') bytes virtual"; } >>first-boot.txt
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
