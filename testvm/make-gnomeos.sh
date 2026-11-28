#!/usr/bin/env bash
# Build a GNOME OS 51 test guest for BackOtter (docs/design/gnome-os.md, R2's
# path 1): upstream's raw pre-install, as gnome-build-meta's own
# utils/run-secure-vm.sh --pre-install does it: systemd-repart --image=ISO
# with upstream's repart.raw.d (gnomeos/repart.raw.d, copied from
# gnome-build-meta gnome-51) writes the ESP, usr-verity A and usr A onto an
# empty 50 GiB disk.  It runs in GNOME OS's own live system (the ISO booted
# with the disk attached, SSH by the SMBIOS below), with GNOME's systemd-repart
# (262): Debian 13's (257) finds no partition in the ISO for CopyBlocks=auto
# ("Unable to automatically discover suitable partition to copy blocks
# from").  The first boot from the disk, its initrd's repart, then adds the empty B
# slot and the btrfs root (LUKS2 + TPM2 when the VM has a TPM).  The first
# boots get sshd and root's key the upstream way, by SMBIOS: the
# io.systemd.stub.kernel-cmdline-extra string "systemd.wants=sshd.service
# gnome.initial-setup=0 console=ttyS0" and the credentials
# ssh.ephemeral-authorized_keys-all and ssh.authorized_keys.root; both are
# then made persistent on the root (sshd wanted by multi-user.target, root's
# authorized_keys), so the guest's own boots need no SMBIOS.
#
# gnomeos51-tpm then gets a real recovery key in place of the initrd's
# literal "ihavenotsetarecoverykey" (systemd-cryptenroll --recovery-key
# --wipe-slot=password), and its TPM2 slot re-enrolled with pcrlock by
# GNOME's three commands (https://os.gnome.org/docs/manual-dual-boot.html,
# 51's form: no --tpm2-public-key-policyref).
#
# Then the one update, pinned: updatectl update host@UPDATE_VERSION from a
# local copy of the feed's files (--feed-dir: the signed SHA256SUMS and .gpg
# and that build's usr, usr verity and UKI, served on 127.0.0.1 and reached
# by the guest at 10.0.2.2 through /etc/sysupdate.d/*.transfer.d drop-ins, as
# run-secure-vm.sh --local-updates does; removed after), so slot B holds
# UPDATE_VERSION and boots, and A the ISO's 51.1.  Both slots are booted:
# the default (B) and the older by bootctl set-oneshot.  testvm/gnomeos/
# probe.sh's record of every stage goes to build.txt.
#
# Usage: ./make-gnomeos.sh [--recovery-key-file FILE] [--port N] [--feed-dir DIR] FLAVOR ISO WORK_DIR
#   e.g. ./make-gnomeos.sh gnomeos51 /bkoscratch/rebuild-inputs/gnomeos51/gnome_os_51.1-x86_64.iso vms/gnomeos51
#        ./make-gnomeos.sh --recovery-key-file /bkoscratch/rebuild-inputs/gnomeos51/gnomeos51-tpm.recovery \
#            gnomeos51-tpm /bkoscratch/rebuild-inputs/gnomeos51/gnome_os_51.1-x86_64.iso vms/gnomeos51-tpm
#
# FLAVOR is the guest:
#   gnomeos51      UEFI, Secure Boot off (vm-run --no-secure-boot), no TPM:
#                  the initrd's disable-encryption.service, so a plain btrfs root
#   gnomeos51-tpm  UEFI with Secure Boot on: OVMF's variables start in setup
#                  mode (OVMF_VARS_4M.fd, no keys) and systemd-boot enrols the
#                  ESP's loader/keys/auto itself; a swtpm TPM 2.0, so the
#                  root is LUKS2 sealed to it.  Its recovery key goes to
#                  --recovery-key-file FILE (required; mode 600; never
#                  printed, never in the repository: a testbed secret)
#
# ISO is gnome_os_51.1-x86_64.iso, checked against the SHA-256 pinned below
# (GNOME publishes none).  --feed-dir defaults to sysupdate-UPDATE_VERSION
# beside the ISO; its files are checked against its SHA256SUMS, whose
# signature the guest's sysupdate checks itself.  Nothing is downloaded.
#
# Results, in WORK_DIR:
#   FLAVOR.qcow2       the guest's 50 GiB disk (sparse, as upstream's)
#   uefi-vars.fd       -tpm: the OVMF variables, GNOME's PK/KEK/db enrolled
#                      (boot copies: vm-run-options' --guest-vars)
#   tpm/               -tpm: the swtpm state the root's key is sealed to
#   recovery-key       -tpm: a link to --recovery-key-file's FILE (vm-store
#                      skips links), the 64 modhex characters, no newline
#   id_ed25519, .pub   root's SSH key
#   build.txt          what the build did, probe.sh's record of each stage
#   build-serial-N.log the serial console of each build boot
#   check-boot-{1,2}-serial.log, first-boot.txt
#                      two boots as a test boots the guest: an overlay and
#                      copies of uefi-vars.fd and tpm/, no SMBIOS, SSH alone
#   nvram.txt          -tpm: virt-fw-vars' listing of uefi-vars.fd
#   vm-run-options     "--uefi --no-secure-boot --memory 8192 --cpus 4", and
#                      for -tpm "--uefi --memory 8192 --cpus 4 --guest-vars
#                      uefi-vars.fd --guest-tpm tpm"
#
# Needs: qemu-system-x86_64 with KVM, OVMF (4M, Secure Boot build), qemu-img,
# swtpm (-tpm), python3, ssh, virt-fw-vars (nvram.txt only).  Runs as an
# ordinary user in the kvm group.

set -euo pipefail

ISO_NAME=gnome_os_51.1-x86_64.iso
ISO_SHA256=a94c7be758b185c169dc6db707fabebd69e12dfff439294891870fa96b30fd15
ISO_VERSION=51.1
UPDATE_VERSION=51.1-66
DISK_SIZE=50G
MEMORY=8192
CPUS=4
FIRST_BOOT_TIMEOUT=1500
BOOT_TIMEOUT=900
LITERAL_KEY=ihavenotsetarecoverykey

here=$(dirname "$(realpath "$0")")
die() { echo "make-gnomeos: $*" >&2; exit 1; }

recovery_out="" port="" feed=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --recovery-key-file) recovery_out=$(realpath -m "$2"); shift 2 ;;
        --port) port=$2; shift 2 ;;
        --feed-dir) feed=$(realpath "$2"); shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 3 ]] || die "usage: $0 [--recovery-key-file FILE] [--port N] [--feed-dir DIR] FLAVOR ISO WORK_DIR"
flavor=$1
case $flavor in
    gnomeos51) TPM=no ;;
    gnomeos51-tpm) TPM=yes; [[ -n $recovery_out ]] || die "gnomeos51-tpm needs --recovery-key-file FILE" ;;
    *) die "FLAVOR is gnomeos51 or gnomeos51-tpm, not '$flavor'" ;;
esac
[[ $TPM == yes || -z $recovery_out ]] || die "--recovery-key-file is for gnomeos51-tpm only"
iso=$(realpath "$2")
[[ -f $iso ]] || die "no $iso"
[[ -n $feed ]] || feed=$(dirname "$iso")/sysupdate-$UPDATE_VERSION
[[ -d $feed ]] || die "no feed copy $feed"
GUEST_NAME=$flavor
DISK=$GUEST_NAME.qcow2
mkdir -p "$3"
cd "$3"
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"
[[ -e tpm ]] && die "$3/tpm already exists; move it away to rebuild"
for t in qemu-system-x86_64 qemu-img python3 ssh ssh-keygen; do command -v $t >/dev/null || die "needs $t"; done
[[ $TPM == no ]] || command -v swtpm >/dev/null || die "needs swtpm"
for f in /usr/share/OVMF/OVMF_CODE_4M.secboot.fd /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_CODE_4M.fd; do [[ -f $f ]] || die "no $f"; done
t_start=$(date +%s)

# --- The inputs -------------------------------------------------------------------
[[ $(sha256sum <"$iso" | cut -d' ' -f1) == "$ISO_SHA256" ]] || die "$iso's SHA-256 isn't the pinned $ISO_SHA256"
[[ -f $feed/SHA256SUMS && -f $feed/SHA256SUMS.gpg ]] || die "$feed has no SHA256SUMS and SHA256SUMS.gpg"
feed_files=$(grep -E "^[0-9a-f]{64}  (gnomeos-x86-64_|usr-x86-64_)${UPDATE_VERSION//./\\.}[._]" "$feed/SHA256SUMS" | grep -v -e '-apparmor' -e '-nvidia' || true)
[[ $(wc -l <<<"$feed_files") == 3 ]] || die "$feed/SHA256SUMS doesn't list one UKI, usr and verity of $UPDATE_VERSION"
(cd "$feed" && sha256sum --quiet -c <<<"$feed_files") || die "$feed's $UPDATE_VERSION files don't match its SHA256SUMS"

if [[ -z $port ]]; then
    range=$(python3 -I -B "$here/test2/t2lib/hosts.py" get "$(hostname -s)" slot_range 2>/dev/null) ||
        die "this host isn't in testvm/hosts.json; give --port"
    lo=${range%-*}
    for p in $(seq $((lo + 40)) $((lo + 79))); do
        ss -ltnH "sport = :$p" | grep -q . || { port=$p; break; }
    done
    [[ -n $port ]] || die "no free port in $((lo + 40))-$((lo + 79))"
fi
ss -ltnH "sport = :$port" | grep -q . && die "port $port is taken"
feed_port=$((port + 1000))
ss -ltnH "sport = :$feed_port" | grep -q . && die "port $feed_port (the feed's) is taken"

work=$(mktemp -d)
vm_pid="" http_pid=""
cleanup() {
    [[ -n $vm_pid ]] && kill "$vm_pid" 2>/dev/null
    [[ -n $http_pid ]] && kill "$http_pid" 2>/dev/null
    rm -rf "$work"
}
trap cleanup EXIT

umask 077
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
umask 022
rm -f build.txt build-serial-*.log first-boot.txt check-boot-*-serial.log
note() { echo "$*"; echo "== $*" >>build.txt; }
note "build of $GUEST_NAME from $(basename "$iso") on $(hostname -s), $(date -u +%FT%TZ), SSH port $port"

if [[ $TPM == yes ]]; then
    rm -f uefi-vars.fd
    cp /usr/share/OVMF/OVMF_VARS_4M.fd uefi-vars.fd   # setup mode: no PK, KEK or db
fi

# --- Booting --------------------------------------------------------------------
pub64=$(base64 -w0 <id_ed25519.pub)
smbios=(-smbios "type=11,value=io.systemd.stub.kernel-cmdline-extra=systemd.wants=sshd.service gnome.initial-setup=0 console=tty0 console=ttyS0,,115200,value=io.systemd.credential.binary:ssh.ephemeral-authorized_keys-all=$pub64,value=io.systemd.credential.binary:ssh.authorized_keys.root=$pub64")
rssh=(ssh -q -i id_ed25519 -p "$port" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 root@127.0.0.1)
r() { timeout "${T:-600}" "${rssh[@]}" "$@" </dev/null; }
ri() { timeout "${T:-600}" "${rssh[@]}" "$@"; }   # with stdin
boots=0
# boot_vm LOG VARS TPM_DIR IMAGE SMBIOS(yes|no) [VM-RUN OPTIONS...]: vm-run in the background (vm_pid)
boot_vm() {
    local log=$1 vars=$2 tpmd=$3 image=$4 sm=$5; shift 5
    local opts=(--uefi --memory "$MEMORY" --cpus "$CPUS" --discard --serial-log "$log")
    if [[ $TPM == yes ]]; then opts+=(--uefi-vars "$vars" --tpm "$tpmd"); else opts+=(--no-secure-boot); fi
    local extra=(); [[ $sm == yes ]] && extra=(-- "${smbios[@]}")
    SSH_PORT=$port "$here/vm-run" "${opts[@]}" "$@" "$image" "${extra[@]}" </dev/null >>"$work/vm-run.out" 2>&1 &
    vm_pid=$!
}
build_boot() {  # the next build boot, on the guest's own disk, variables and TPM, with SMBIOS
    boots=$((boots + 1))
    boot_vm "build-serial-$boots.log" "$PWD/uefi-vars.fd" "$PWD/tpm" "$DISK" "${1:-yes}"
}
# wait_ssh SECONDS LOG: SSH answers as root; fails at a LUKS prompt (nothing types it)
wait_ssh() {
    local end=$((SECONDS + $1))
    while ((SECONDS < end)); do
        kill -0 "$vm_pid" 2>/dev/null || return 1
        T=20 r true 2>/dev/null && return 0
        if grep -aqiE 'enter (passphrase|recovery key)|Please enter.*for disk' "$2" 2>/dev/null; then
            echo "make-gnomeos: a LUKS prompt in $2" >&2; return 2
        fi
        sleep 10
    done
    return 1
}
probe() {  # STAGE
    { echo "=== probe: $1, $(date -u +%T)"; T=600 ri bash -s "$1" <"$here/gnomeos/probe.sh" 2>&1; } >"$work/$1.txt" || true
    cat "$work/$1.txt" >>build.txt
}
val() { sed -n "s/^BKO $2=//p" "$work/$1.txt" | head -1; }   # STAGE KEY
stop_vm() {
    T=30 r 'sync; systemctl poweroff' 2>/dev/null || true
    local _
    for _ in $(seq 120); do kill -0 "$vm_pid" 2>/dev/null || break; sleep 2; done
    if kill -0 "$vm_pid" 2>/dev/null; then kill "$vm_pid"; die "the guest did not power off"; fi
    wait "$vm_pid" 2>/dev/null || true
    vm_pid=""
}
# wait_clock: GNOME OS boots with its clock at its epoch, 2011-11-11 11:11
# UTC (systemd resets a clock more than 15 years past the epoch to it), until
# timesyncd syncs it: sysupdate's TLS and gpg checks fail before that.  Wait
# for the sync; set it from the host's after 300 s.
wait_clock() {
    local end=$((SECONDS + 300))
    while ((SECONDS < end)); do
        [[ $(T=30 r 'timedatectl show -p NTPSynchronized --value' 2>/dev/null) == yes ]] && return 0
        sleep 10
    done
    note "timesyncd never synced in 300 s; setting the guest's clock from the host's"
    T=30 r "date -u -s @$(date +%s)" >/dev/null || die "could not set the guest's clock"
}
reboot_vm() {  # [SMBIOS]: power off, boot again
    stop_vm
    build_boot "${1:-yes}"
    wait_ssh "$BOOT_TIMEOUT" "build-serial-$boots.log" || die "build boot $boots never answered on SSH; see build-serial-$boots.log"
}

# --- Upstream's raw pre-install, in the live system --------------------------------------
# The ISO boots from the CD drive; a read-only overlay of it is the second disk
# (the live system takes its /usr from the first copy it finds; repart reads
# the CD's); the guest's empty disk is the first (vda).
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
qemu-img create -q -f qcow2 -b "$iso" -F raw "$work/iso.qcow2"
note "live pre-install: the ISO booted, systemd-repart --image=/dev/sr0 --definitions=repart.raw.d --empty=force /dev/vda"
boots=$((boots + 1))
SSH_PORT=$port "$here/vm-run" --uefi --no-secure-boot --memory "$MEMORY" --cpus "$CPUS" --discard --cdrom "$iso" \
    --serial-log "build-serial-$boots.log" --disk "$work/iso.qcow2" "$DISK" -- "${smbios[@]}" </dev/null >>"$work/vm-run.out" 2>&1 &
vm_pid=$!
wait_ssh "$FIRST_BOOT_TIMEOUT" "build-serial-$boots.log" || die "the live system never answered on SSH; see build-serial-$boots.log"
T=60 r 'grep -q "root=live:gnomeos" /proc/cmdline' || die "the pre-install boot isn't the live system: $(T=30 r 'cat /proc/cmdline')"
tar -C "$here/gnomeos" -cf - repart.raw.d | T=60 "${rssh[@]}" 'mkdir -p /run/bko && tar -C /run/bko -xmf -' || die "could not copy repart.raw.d in"
out=$(T=1800 r 'systemctl --version | head -1; systemd-repart --dry-run=no --image=/dev/sr0 --definitions=/run/bko/repart.raw.d --empty=force /dev/vda 2>&1; echo "BKO-EXIT $?"; sfdisk --dump /dev/vda' 2>&1) || true
{ echo "=== live pre-install"; echo "$out"; } >>build.txt
grep -q '^BKO-EXIT 0$' <<<"$out" || die "systemd-repart in the live system failed: $(grep -v '^ ' <<<"$out" | tail -4 | tr '\n' ' ')"
stop_vm
note "pre-install done: $(grep -c '^/dev/vda[0-9]* : start=' <<<"$out") partitions; qcow2 $(du -h "$DISK" | cut -f1)"

# --- First boot: the initrd's repart, sshd by SMBIOS ------------------------------------
[[ $TPM == yes ]] && note "with a TPM: $PWD/tpm (new); OVMF variables in setup mode"
build_boot
echo "First boot; the console in $PWD/build-serial-1.log"
wait_ssh "$FIRST_BOOT_TIMEOUT" build-serial-1.log || die "no SSH as root after $FIRST_BOOT_TIMEOUT s; see build-serial-1.log"
t_install=$(date +%s)
note "first boot answered on SSH after $((t_install - t_start)) s"
probe installed
[[ $(val installed id) == org.gnome.os ]] || die "os-release ID is $(val installed id)"
[[ $(val installed image_version) == "$ISO_VERSION" ]] || die "IMAGE_VERSION is $(val installed image_version), not $ISO_VERSION"
[[ $(val installed encrypted) == "$TPM" ]] || die "encrypted is $(val installed encrypted), not $TPM"
if [[ $TPM == yes ]]; then
    [[ $(val installed secure_boot) == 01 ]] || die "not booted under Secure Boot (SecureBoot=$(val installed secure_boot), SetupMode=$(val installed setup_mode)); see build-serial-1.log"
fi

# --- Persistent SSH ---------------------------------------------------------------------
T=60 r "install -d -m 700 /root/.ssh && printf '%s\n' '$(cat id_ed25519.pub)' >/root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys &&
    systemctl add-wants multi-user.target sshd.service && hostnamectl set-hostname $GUEST_NAME" >>build.txt 2>&1 ||
    die "could not make root's key and sshd persistent"
note "root's authorized_keys written, sshd wanted by multi-user.target, hostname $GUEST_NAME"

# --- -tpm: the recovery key, pcrlock -------------------------------------------------------
if [[ $TPM == yes ]]; then
    umask 077
    T=300 r "printf %s $LITERAL_KEY >/run/bko-literal && systemd-cryptenroll --unlock-key-file=/run/bko-literal --recovery-key --wipe-slot=password /dev/disk/by-partlabel/root; e=\$?; rm -f /run/bko-literal; exit \$e" \
        >"$work/rk.out" 2>"$work/rk.err" || die "systemd-cryptenroll --recovery-key failed: $(tr '\n' ' ' <"$work/rk.err")"
    grep -Eo '[cbdefghijklnrtuv]{8}(-[cbdefghijklnrtuv]{8}){7}' "$work/rk.out" | head -1 | tr -d '\n' >"$work/recovery"
    umask 022
    [[ $(wc -c <"$work/recovery") == 71 ]] || die "no recovery key in systemd-cryptenroll's output"
    note "recovery key enrolled, the literal password slot wiped; stderr: $(sed -E 's/[cbdefghijklnrtuv]{8}(-[cbdefghijklnrtuv]{8}){7}/<KEY>/g' "$work/rk.err" | tr '\n' ' ' | cut -c1-400)"
    mkdir -p "$(dirname "$recovery_out")"
    install -m 600 "$work/recovery" "$recovery_out.new" && mv "$recovery_out.new" "$recovery_out"
    note "recovery key written to $recovery_out (sha256 $(sha256sum <"$recovery_out" | cut -c1-12))"
    # The recovery key as a --key-file (no newline), and GNOME's three commands.
    T=60 ri 'umask 077; cat >/run/bko-rk' <"$recovery_out" || die "could not copy the recovery key in"
    out=$(T=600 r '/usr/lib/systemd/systemd-pcrlock is-supported; /usr/lib/systemd/systemd-pcrlock remove-policy; echo "remove-policy: $?";
        /usr/lib/systemd/systemd-pcrlock make-policy --location=770 --pcr=0+1+2+3+4+5+7+11+13+14+15; echo "make-policy: $?";
        systemd-cryptenroll --unlock-key-file=/run/bko-rk --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs="" /dev/disk/by-partlabel/root; echo "cryptenroll: $?"' 2>&1) || true
    { echo "=== pcrlock re-enrolment"; echo "$out"; } >>build.txt
    grep -q '^make-policy: 0$' <<<"$out" || die "systemd-pcrlock make-policy failed; see build.txt"
    grep -q '^cryptenroll: 0$' <<<"$out" || die "systemd-cryptenroll --wipe-slot=tpm2 with the recovery key as --unlock-key-file failed; see build.txt"
    note "the recovery key opened the root as --unlock-key-file (no newline); TPM2 slot re-enrolled with pcrlock"
    T=30 r 'rm -f /run/bko-rk' || true
    probe enrolled
    [[ $(val enrolled tpm2_pcrlock) == true ]] || die "the TPM2 token has no pcrlock policy after re-enrolment"
    reboot_vm
    note "build boot $boots: unattended unlock after the re-enrolment"
fi

# --- The one update into B, pinned ---------------------------------------------------------
python3 -I -m http.server --bind 127.0.0.1 --directory "$feed" "$feed_port" >"$work/http.log" 2>&1 &
http_pid=$!
sleep 2
kill -0 "$http_pid" 2>/dev/null || die "could not serve $feed on 127.0.0.1:$feed_port"
out=$(T=120 r "for t in /usr/lib/sysupdate.d/*.transfer; do d=/etc/sysupdate.d/\$(basename \$t).d; mkdir -p \$d &&
    printf '[Source]\nPath=http://10.0.2.2:$feed_port/\n' >\$d/bko-local.conf; done; ls /usr/lib/sysupdate.d /etc/sysupdate.d" 2>&1) ||
    die "could not point sysupdate at the local feed: $out"
note "sysupdate pointed at the local feed ($feed, 10.0.2.2:$feed_port): $(tr '\n' ' ' <<<"$out")"
wait_clock
note "guest clock: $(T=30 r 'date -u +%FT%TZ')"
# A fresh install already lists the newest build as "current+partial" (and
# updatectl update then refuses it: "already acquired and partially
# installed"); record what sysupdate sees, and vacuum first.
{ echo "=== systemd-sysupdate list $UPDATE_VERSION, before"; T=300 r "/usr/lib/systemd/systemd-sysupdate list $UPDATE_VERSION 2>&1 | grep -v 'Got [0-9]*%'"
  echo "=== updatectl list host, before"; T=300 r 'updatectl list host 2>&1 | head -8'
  echo "=== updatectl vacuum"; T=300 r 'updatectl vacuum 2>&1; echo "BKO-EXIT $?"'
  echo "=== systemd-sysupdate list $UPDATE_VERSION, after the vacuum"; T=300 r "/usr/lib/systemd/systemd-sysupdate list $UPDATE_VERSION 2>&1 | grep -v 'Got [0-9]*%' | tail -12"; } >>build.txt 2>&1 || true
out=$(T=3600 r "updatectl update host@$UPDATE_VERSION 2>&1; echo \"BKO-EXIT \$?\"" 2>&1) || true
{ echo "=== updatectl update host@$UPDATE_VERSION"; echo "$out"; } >>build.txt
if ! grep -q '^BKO-EXIT 0$' <<<"$out"; then
    note "updatectl update host@$UPDATE_VERSION failed: $(tail -3 <<<"$out" | tr '\n' ' '); sysupdated's journal:"
    T=60 r 'journalctl -b -u systemd-sysupdated --no-pager | grep -v "Got [0-9]*%" | tail -30' >>build.txt 2>&1 || true
    die "updatectl update host@$UPDATE_VERSION failed; see build.txt"
fi
T=60 r 'rm -rf /etc/sysupdate.d && sync' || die "could not remove the local feed's drop-ins"
kill "$http_pid" 2>/dev/null; wait "$http_pid" 2>/dev/null || true; http_pid=""
note "updated to $UPDATE_VERSION; drop-ins removed"
reboot_vm
probe updated
[[ $(val updated image_version) == "$UPDATE_VERSION" ]] || die "after the update IMAGE_VERSION is $(val updated image_version), not $UPDATE_VERSION"
note "build boot $boots: booted $UPDATE_VERSION (slot B), usrhash $(val updated usrhash)"

# --- The older slot, then back -----------------------------------------------------------
old=$(T=60 r "bootctl list --no-pager 2>/dev/null | sed -n 's/^ *id: *\(gnomeos_${ISO_VERSION//./\\.}\(+[0-9-]*\)\{0,1\}\.efi\)\$/\1/p' | head -1")
[[ -n $old ]] || die "bootctl list has no entry for $ISO_VERSION"
T=60 r "bootctl set-oneshot '$old'" >>build.txt 2>&1 || die "bootctl set-oneshot $old failed"
note "bootctl set-oneshot $old"
reboot_vm
probe older
[[ $(val older image_version) == "$ISO_VERSION" ]] || die "the one-shot boot booted $(val older image_version), not $ISO_VERSION"
[[ $(val older usrhash) != "$(val updated usrhash)" ]] || die "the older boot has the same usrhash"
note "build boot $boots: booted $ISO_VERSION (slot A) by set-oneshot, usrhash $(val older usrhash)"
reboot_vm
probe final
[[ $(val final image_version) == "$UPDATE_VERSION" ]] || die "back to the default: IMAGE_VERSION is $(val final image_version)"
stamp=$(date -u +%FT%TZ)
T=60 r "echo '$GUEST_NAME $stamp' >/var/lib/bko-mark && fstrim -av; sync" >>build.txt 2>&1 || die "could not write the mark"
stop_vm
[[ $TPM == yes ]] && { command -v virt-fw-vars >/dev/null && virt-fw-vars -i uefi-vars.fd --print >nvram.txt 2>&1 || true; }
t_build=$(date +%s)

# --- Two boots as a test boots it: an overlay, copies, no SMBIOS ------------------------------
[[ $TPM == yes ]] && { cp uefi-vars.fd "$work/check-vars.fd"; cp -a tpm "$work/check-tpm"; }
qemu-img create -q -f qcow2 -b "$PWD/$DISK" -F qcow2 "$work/check.qcow2"
for n in 1 2; do
    boot_vm "check-boot-$n-serial.log" "$work/check-vars.fd" "$work/check-tpm" "$work/check.qcow2" no
    rc=0; wait_ssh "$BOOT_TIMEOUT" "check-boot-$n-serial.log" || rc=$?
    ((rc == 0)) || die "check boot $n never answered on SSH (rc $rc); see check-boot-$n-serial.log"
    probe "check$n"
    cat "$work/check$n.txt" >>first-boot.txt
    [[ $(T=30 r 'cat /var/lib/bko-mark') == "$GUEST_NAME $stamp" ]] || die "check boot $n: the mark is wrong"
    [[ $(val check$n image_version) == "$UPDATE_VERSION" ]] || die "check boot $n: IMAGE_VERSION $(val check$n image_version)"
    [[ $(val check$n cmdline_wants_sshd) == no ]] || die "check boot $n: SMBIOS reached the guest"
    stop_vm
    note "check boot $n: SSH with no SMBIOS, $UPDATE_VERSION, the mark$([[ $TPM == yes ]] && echo ', unattended unlock')"
done

if [[ $TPM == yes ]]; then
    echo "--uefi --memory $MEMORY --cpus $CPUS --guest-vars uefi-vars.fd --guest-tpm tpm" >vm-run-options
    ln -sfn "$recovery_out" recovery-key
else
    echo "--uefi --no-secure-boot --memory $MEMORY --cpus $CPUS" >vm-run-options
fi
t_end=$(date +%s)
{ echo "=== build: $((t_end - t_start)) s (pre-install and first boot $((t_install - t_start)) s, setup, update and slot boots $((t_build - t_install)) s, check boots $((t_end - t_build)) s)"
  echo "=== $DISK: $(du -h "$DISK" | cut -f1) on disk, $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["virtual-size"])') bytes virtual"; } | tee -a build.txt >>first-boot.txt
echo "BACKOTTER-INSTALL-OK $GUEST_NAME"
echo "Done: $PWD/$DISK ($(du -h "$DISK" | cut -f1) on disk)"
