#!/usr/bin/env bash
# Build the Qubes OS 4.3 test guest for BackOtter, qubes43
# (docs/design/qubes.md): Qubes's own installer from its ISO, unattended by
# a kickstart, under QEMU with KVM and OVMF (UEFI, no Secure Boot), Xen nested
# under the host's KVM (AMD hosts with kvm_amd nested=1: QEMU's amd-iommu).
# The installer's default layout: GPT with the ESP, /boot and LUKS2 holding
# VG qubes_dom0 (thin pools root-pool and vm-pool, swap).  Initial setup
# makes the Fedora 44 and Debian 13 templates and the default qubes, without
# sys-usb or Whonix; then a scripted dom0 session installs the check unit and
# makes revisions on purpose; then a boot runs the check.
#
# Usage: ./make-qubes.sh [--iso-dir DIR] [--check-only] qubes43 WORK_DIR
#   e.g. ./make-qubes.sh --iso-dir /bkoscratch/rebuild-inputs/qubes43 qubes43 vms/qubes43
#   --check-only   only the check boot (below) of the guest already in
#                  WORK_DIR, on a throwaway overlay; rebuild-one.sh's boot check
#
# The inputs, looked for in DIR (default WORK_DIR), none downloaded here
# (from mirrors.edge.kernel.org/qubes/iso/ and keys.qubes-os.org):
#   Qubes-R4.3.2-x86_64.iso          its SHA-256 pinned below
#   Qubes-R4.3.2-x86_64.iso.DIGESTS and .DIGESTS.asc   the ISO's digests, and
#                    their signature by the Qubes OS Release 4.3 Signing Key
#   qubes-release-4.3-signing-key.asc, qubes-master-signing-key.asc   the
#                    release key, which must be signed by the master key; both
#                    fingerprints pinned below
# The prepared ISO is made in DIR once per installer menu:
# Qubes-R4.3.2-x86_64-bko-H.iso, H the first 12 hex digits of
# qubes/install-grub.cfg's SHA-256, with its SHA-256 beside it in .sha256 and
# checked on every later use.  It is the ISO with that one menu, in the ISO9660
# tree and in images/efiboot.img (both /EFI/BOOT/grub.cfg and BOOT.conf):
# Xen's console on com1, dom0's on hvc0, Anaconda in text mode from the
# kickstart, and dom0's initcall_blacklist=print_s5_reset_status_mmio, without
# which dom0 panics on the farm's AMD Zen hosts.
#
# Per build: the LUKS passphrase and tester's password (new each build; root
# is locked), the kickstart (qubes/ks.cfg.in filled in), and a 16 MiB FAT disk
# labelled QUBES_OEM holding it as ks.cfg, which the installer's
# inst.ks=hd:LABEL=QUBES_OEM reads (attached for the install only, with a boot
# index: OVMF connects no virtio disk without one, and GRUB wouldn't see it).
# Then:
#   - the install (~7 min), which powers off;
#   - the first boot (~30 min), driven by qubes/qubes-drive.py over the serial
#     console and QEMU's monitor: the passphrase; Qubes's initial-setup, whose
#     GUI waits for a human by design, by keys (Whonix 18 and sys-usb unticked,
#     checked on screendumps); the templates and qubes it makes (~10 min);
#     then, logged in as tester and root on hvc0, qubes/revisions.sh (with the
#     check unit) typed in as a base64 tar: it installs bko-qubes-check.service,
#     sets revisions_to_keep 2 on work:private, starts and stops work and
#     personal twice (each time writing a seed file) and the Debian template
#     once; and powers off;
#   - the check boot, on a throwaway overlay: the passphrase, then
#     bko-qubes-check.sh's BKO-QUBES-CHECK lines on the serial console, which
#     must end in "BKO-QUBES-CHECK DONE PASS"; then it logs in and powers off.
#
# Results, in WORK_DIR:
#   qubes43.qcow2         the guest's disk (sparse), with a qcow2 snapshot
#                         "fresh-install" taken after the first boot's session
#   uefi-vars.fd          the OVMF variables of the install, with Qubes's NVRAM
#                         entry (\EFI\qubes\grubx64.efi), for OVMF_CODE_4M.fd
#   console-passphrase    the LUKS passphrase (mode 600), typed at dom0's
#                         "Please enter passphrase for disk luks-...:"
#   root-password         tester's password (mode 600; root is locked, tester
#                         has sudo)
#   ks.cfg                the kickstart (mode 600: it has the passphrase)
#   install-iso.txt       which prepared ISO
#   install-serial.log, first-boot-serial.log, check-boot-serial.log   the
#                         serial consoles (Xen and dom0)
#   setup-NN-*.png        screendumps of the first boot's initial-setup, before
#                         and after each step
#   revisions.txt         what the first boot's session did (BKO-QUBES-SETUP
#                         lines: times, the LVs with their origins)
#   check.txt             the check boot's BKO-QUBES-CHECK and -INFO lines
#   vm-run-options        "--uefi --no-secure-boot --cpus 4 --memory 10240"
#   qemu-options          the QEMU options it needs that vm-run has no word for:
#                         q35 with kernel-irqchip=split, amd-iommu,
#                         ICH9-LPC.disable_s3=1 (else OVMF's ACPI NVS at 8-9 MiB
#                         clashes with the PV dom0 kernel), no NIC
#
# The guest: 64 GiB disk, 10 GiB RAM, 4 CPUs; dom0 has no network (Qubes gives
# NICs to sys-net, which here has none), so no SSH: the serial console only.
#
# Needs: qemu-system-x86_64 with KVM and nested SVM, OVMF, qemu-img, xorriso,
# mtools, gpg, openssl, python3.  Runs as an ordinary user in the kvm group.

set -euo pipefail

ISO=Qubes-R4.3.2-x86_64.iso
ISO_SHA256=13bc750b4635939acf68e37994c57e200e2a0035e6ad43fc917d239fdc726776
# Qubes Master Signing Key, and the Qubes OS Release 4.3 Signing Key it signs
MASTER_KEY=427F11FD0FAA4B080123F01CDDFA1A3E36879494
RELEASE_KEY=F3FA3F99D6281F7B3A3E5E871C3D9B627F3FADA4
DISK_SIZE=64G
MEMORY=10240
CPUS=4
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=3600
FIRST_BOOT_TIMEOUT=14400        # qubes-drive.py's own steps time out sooner
CHECK_TIMEOUT=5400
export PATH=$PATH:/usr/sbin:/sbin

here=$(dirname "$(realpath "$0")")
die() { echo "make-qubes: $*" >&2; exit 1; }

iso_dir="" check_only=no
while [[ $# -gt 0 ]]; do
    case $1 in
        --iso-dir) iso_dir=$(realpath "$2"); shift 2 ;;
        --check-only) check_only=yes; shift ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || die "usage: $0 [--iso-dir DIR] [--check-only] qubes43 WORK_DIR"
[[ $1 == qubes43 ]] || die "the only guest is qubes43, not '$1'"
GUEST_NAME=$1
DISK=$GUEST_NAME.qcow2
for f in ks.cfg.in install-grub.cfg qubes-drive.py revisions.sh bko-qubes-check.sh bko-qubes-check.service; do
    [[ -f $here/qubes/$f ]] || die "no $here/qubes/$f"
done
[[ $(cat /sys/module/kvm_amd/parameters/nested 2>/dev/null) == [1Y] ]] ||
    die "needs an AMD host with kvm_amd nested=1 (Xen runs nested, with QEMU's amd-iommu)"
mkdir -p "$2"
cd "$2"
[[ -n $iso_dir ]] || iso_dir=$PWD
if [[ $check_only == yes ]]; then
    for f in "$DISK" uefi-vars.fd console-passphrase root-password; do [[ -f $f ]] || die "--check-only: no $2/$f"; done
else
    [[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"
fi

work=$(mktemp -d)
qemu_pid=""
cleanup() {
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
trap cleanup EXIT

wait_exit() {  # SECONDS: whether QEMU exited within them
    local _
    for _ in $(seq $(($1 / 5))); do kill -0 "$qemu_pid" 2>/dev/null || { wait "$qemu_pid" 2>/dev/null || true; qemu_pid=""; return 0; }; sleep 5; done
    return 1
}
shot() {  # PNG: a screendump of the running guest
    python3 -I "$here/qubes/qubes-drive.py" shot "$work/mon.sock" "$1" 2>/dev/null || true
}
kill_guest() {  # WHY: a last screendump, QEMU killed, and die
    shot "$PWD/failed-screen.png"
    kill "$qemu_pid" 2>/dev/null; wait "$qemu_pid" 2>/dev/null || true; qemu_pid=""
    die "$1"
}
# q35 with an AMD IOMMU before the other devices (Xen enables AMD-Vi on its
# IVRS table), kernel-irqchip=split for its interrupt remapping, and S3 off:
# with S3, OVMF keeps ACPI NVS at 8-9 MiB, inside the PV dom0 kernel's
# pseudo-physical 2-64 MiB, and dom0 halts ("Xen hypervisor allocated kernel
# memory conflicts with E820 map").  No NIC: dom0 has no network.
QEMU_OPTIONS="-machine q35,accel=kvm,kernel-irqchip=split -device amd-iommu,intremap=on -global ICH9-LPC.disable_s3=1 -nic none"
qemu() {  # VARS SERIAL_ARGS... -- MORE...: QEMU in the background, its monitor on $work/mon.sock
    local vars=$1; shift
    local -a qopts
    read -ra qopts <<<"$QEMU_OPTIONS"
    rm -f "$work/mon.sock"
    qemu-system-x86_64 "${qopts[@]}" -cpu host -smp "$CPUS" -m "$MEMORY" \
        -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" \
        -drive "if=pflash,format=raw,unit=1,file=$vars" \
        -drive "file=$DISK,if=none,id=d0,format=qcow2,discard=unmap" -device virtio-blk-pci,drive=d0 \
        -vga std -display none -monitor "unix:$work/mon.sock,server=on,wait=off" "$@" &
    qemu_pid=$!
}
serial_sock() {  # LOG: the serial console on $work/ser.sock, logged by QEMU to LOG
    rm -f "$work/ser.sock"
    printf '%s\n' -chardev "socket,id=ser0,path=$work/ser.sock,server=on,wait=off,logfile=$1" -serial chardev:ser0
}

# --- The check boot: bko-qubes-check.service's lines on the serial console --------------
check_boot() {
    echo "Check boot (bko-qubes-check.service; the console in $PWD/check-boot-serial.log)"
    local t0=$SECONDS rc=0 serial
    cp uefi-vars.fd "$work/check-vars.fd"
    mapfile -t serial < <(serial_sock check-boot-serial.log)
    qemu "$work/check-vars.fd" "${serial[@]}" -snapshot
    timeout "$CHECK_TIMEOUT" python3 -I "$here/qubes/qubes-drive.py" check "$work/ser.sock" "$work/mon.sock" \
        "$PWD" console-passphrase root-password || rc=$?
    if [[ $rc != 0 ]]; then
        shot "$PWD/failed-screen.png"
        kill "$qemu_pid" 2>/dev/null; wait "$qemu_pid" 2>/dev/null || true; qemu_pid=""
    fi
    wait_exit 300 || kill_guest "the check boot didn't power off; see check-boot-serial.log"
    tr -d '\r' <check-boot-serial.log | grep -aoE 'BKO-QUBES-(CHECK|INFO) .*' >check.txt || true
    grep -a 'BKO-QUBES-CHECK' check.txt >&2 || true
    if [[ $rc != 0 ]] || ! grep -q '^BKO-QUBES-CHECK DONE PASS' check.txt; then
        die "the check boot didn't end in BKO-QUBES-CHECK DONE PASS (driver exit $rc); see check.txt and check-boot-serial.log"
    fi
    echo "The check boot passed after $((SECONDS - t0)) s"
}
if [[ $check_only == yes ]]; then
    check_boot
    exit 0
fi

# --- Check the ISO, its digests and the keys ---------------------------------------
for f in "$ISO" "$ISO.DIGESTS" "$ISO.DIGESTS.asc" qubes-release-4.3-signing-key.asc qubes-master-signing-key.asc; do
    [[ -f $iso_dir/$f && ! -e $iso_dir/$f.part ]] || die "no $iso_dir/$f (fetch it first: see the header)"
done
mkdir -m 700 "$work/gnupg"
gpg --homedir "$work/gnupg" --batch -q --import "$iso_dir/qubes-master-signing-key.asc" "$iso_dir/qubes-release-4.3-signing-key.asc" 2>/dev/null
# The release key, signed by the master key ("sig:!": a good signature).
gpg --homedir "$work/gnupg" --batch --with-colons --check-sigs "$RELEASE_KEY" 2>/dev/null |
    awk -F: -v k="$MASTER_KEY" '$1 == "sig" && $2 == "!" && $13 == k { ok = 1 } END { exit !ok }' ||
    die "the release key $RELEASE_KEY isn't signed by the master key $MASTER_KEY"
gpg_status=$(gpg --homedir "$work/gnupg" --batch --status-fd 1 --verify "$iso_dir/$ISO.DIGESTS.asc" "$iso_dir/$ISO.DIGESTS" 2>/dev/null || true)
awk -v k="$RELEASE_KEY" '$1 == "[GNUPG:]" && $2 == "VALIDSIG" && $NF == k { ok = 1 } END { exit !ok }' <<<"$gpg_status" ||
    die "$ISO.DIGESTS.asc isn't a good signature of $ISO.DIGESTS by $RELEASE_KEY"
gpgconf --homedir "$work/gnupg" --kill all 2>/dev/null || true
grep -qE "^$ISO_SHA256 [ *]$ISO\$" "$iso_dir/$ISO.DIGESTS" || die "$ISO.DIGESTS doesn't give the pinned SHA-256 $ISO_SHA256"
[[ $(sha256sum <"$iso_dir/$ISO" | cut -d' ' -f1) == "$ISO_SHA256" ]] ||
    die "$ISO's SHA-256 isn't the pinned $ISO_SHA256"

# --- The prepared ISO, once per installer menu -----------------------------------------
grub_cfg=$here/qubes/install-grub.cfg
menu_hash=$(sha256sum <"$grub_cfg" | cut -c1-12)
PREP_ISO=${ISO%.iso}-bko-$menu_hash.iso
if [[ ! -f $iso_dir/$PREP_ISO ]]; then
    echo "Preparing $iso_dir/$PREP_ISO (install-grub.cfg $menu_hash)"
    xorriso -osirrox on -indev "$iso_dir/$ISO" -extract /images/efiboot.img "$work/efiboot.img" 2>/dev/null ||
        die "$ISO lacks /images/efiboot.img"
    chmod u+w "$work/efiboot.img"
    for f in grub.cfg BOOT.conf; do
        mdir -i "$work/efiboot.img" "::EFI/BOOT/$f" >/dev/null 2>&1 || die "$ISO's efiboot.img has no EFI/BOOT/$f"
        mcopy -o -i "$work/efiboot.img" "$grub_cfg" "::EFI/BOOT/$f"
    done
    # (Under a temporary name, renamed when complete.)
    tmp_iso=$iso_dir/.$PREP_ISO.$$
    xorriso -indev "$iso_dir/$ISO" -outdev "$tmp_iso" -boot_image any replay \
        -map "$grub_cfg" /EFI/BOOT/grub.cfg -map "$grub_cfg" /EFI/BOOT/BOOT.conf \
        -map "$work/efiboot.img" /images/efiboot.img >"$work/xorriso.log" 2>&1 ||
        { cat "$work/xorriso.log" >&2; rm -f "$tmp_iso"; die "xorriso failed"; }
    sha256sum <"$tmp_iso" | cut -d' ' -f1 >"$tmp_iso.sha256"
    mv "$tmp_iso.sha256" "$iso_dir/$PREP_ISO.sha256"
    mv "$tmp_iso" "$iso_dir/$PREP_ISO"
fi
[[ -f $iso_dir/$PREP_ISO.sha256 ]] || die "no $iso_dir/$PREP_ISO.sha256"
[[ $(sha256sum <"$iso_dir/$PREP_ISO" | cut -d' ' -f1) == "$(cat "$iso_dir/$PREP_ISO.sha256")" ]] ||
    die "$PREP_ISO doesn't match its .sha256"
echo "$PREP_ISO: $ISO with qubes/install-grub.cfg $menu_hash as its menu" >install-iso.txt

# --- Secrets, the kickstart and its disk ----------------------------------------------
# Letters and digits only, so they need no quoting anywhere (the kickstart,
# the serial console).
umask 077
openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 20 >console-passphrase
passphrase=$(cat console-passphrase)
[[ $passphrase =~ ^[A-Za-z0-9]{16,}$ ]] || die "could not make a passphrase"
openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 16 >root-password
password=$(cat root-password)
[[ $password =~ ^[A-Za-z0-9]{12,}$ ]] || die "could not make a password"
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
sed -e "s|@PASSPHRASE@|$passphrase|" -e "s|@PASSWORD_HASH@|$hash|" "$here/qubes/ks.cfg.in" >ks.cfg
umask 022
if grep -n '@[A-Z_0-9]\+@' ks.cfg; then die "ks.cfg placeholders left unfilled"; fi
truncate -s 16M "$work/oem.img"
mformat -i "$work/oem.img" -v QUBES_OEM ::
mcopy -i "$work/oem.img" ks.cfg ::ks.cfg
# The dom0 files the first boot's session types in.
tar -czf "$work/dom0.tgz" --owner=0 --group=0 -C "$here/qubes" revisions.sh bko-qubes-check.sh bko-qubes-check.service

# --- Install ---------------------------------------------------------------------------
cp "$OVMF_VARS" uefi-vars.fd
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing (the console in $PWD/install-serial.log)"
t0=$SECONDS
qemu uefi-vars.fd -serial file:install-serial.log -no-reboot \
    -drive "file=$iso_dir/$PREP_ISO,if=none,id=cd,media=cdrom,readonly=on" -device ide-cd,drive=cd,bootindex=0 \
    -drive "file=$work/oem.img,if=none,id=oem,format=raw" -device virtio-blk-pci,drive=oem,bootindex=1
wait_exit "$INSTALL_TIMEOUT" || kill_guest "the install didn't finish in $INSTALL_TIMEOUT s; see install-serial.log"
echo "The installer powered off after $((SECONDS - t0)) s"
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 4000000000 ]] ||
    die "the disk is nearly empty; see install-serial.log"

# --- First boot: initial-setup, then revisions.sh -------------------------------------
echo "First boot (initial-setup, revisions.sh; the console in $PWD/first-boot-serial.log)"
t0=$SECONDS
rc=0
mapfile -t serial < <(serial_sock first-boot-serial.log)
qemu uefi-vars.fd "${serial[@]}"
timeout "$FIRST_BOOT_TIMEOUT" python3 -I "$here/qubes/qubes-drive.py" firstboot "$work/ser.sock" "$work/mon.sock" \
    "$PWD" console-passphrase root-password "$work/dom0.tgz" || rc=$?
[[ $rc == 0 ]] || kill_guest "the first boot failed (driver exit $rc); see first-boot-serial.log and the setup-*.png screendumps"
wait_exit 300 || kill_guest "the first boot didn't power off; see first-boot-serial.log"
tr -d '\r' <first-boot-serial.log | grep -aoE 'BKO-QUBES-SETUP .*' >revisions.txt || true
grep -q '^BKO-QUBES-SETUP DONE PASS' revisions.txt || die "revisions.sh didn't pass; see revisions.txt"
echo "The first boot powered off after $((SECONDS - t0)) s"
# (Nothing prints the passphrase or the password; should anything ever, it isn't kept.)
sed -i -e "s/$passphrase/<console-passphrase>/g" -e "s/$password/<root-password>/g" \
    install-serial.log first-boot-serial.log revisions.txt

check_boot
sed -i -e "s/$passphrase/<console-passphrase>/g" -e "s/$password/<root-password>/g" check-boot-serial.log check.txt

qemu-img snapshot -c fresh-install "$DISK"
echo "--uefi --no-secure-boot --cpus $CPUS --memory $MEMORY" >vm-run-options
echo "$QEMU_OPTIONS" >qemu-options
echo "Done: $DISK in $PWD ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
