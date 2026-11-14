#!/usr/bin/env bash
# Build an Ubuntu Core 24 test guest for BackOtter from Canonical's stock
# signed image (docs/design/ubuntu-core.md, Decision 3): the image unpacked
# onto an 8 GiB qcow2 and booted under QEMU with KVM, OVMF with Secure Boot
# on, and a NoCloud drive labelled CIDATA.  The image's own install mode
# runs (it partitions the disk, encrypts it when it finds a TPM, and copies
# the seed's snaps), then run mode, where cloud-init makes the user tester.
# Then, over SSH: every snap refreshed until snapd settles (through the
# reboots that a kernel, base, gadget or snapd refresh makes), auto-refresh
# held, a second recovery system created through snapd's REST API, the snap
# hello at two revisions with data in each, and the marks.
#
# Usage: ./make-ubuntu-core.sh [--recovery-key-file FILE] [--port N] FLAVOR IMG WORK_DIR
#   e.g. ./make-ubuntu-core.sh uc24 /bkoscratch/rebuild-inputs/ubuntu-core24/ubuntu-core-24-amd64.img.xz vms/uc24
#        ./make-ubuntu-core.sh --recovery-key-file /bkoscratch/rebuild-inputs/ubuntu-core24/uc24-fde.recovery \
#            uc24-fde /bkoscratch/rebuild-inputs/ubuntu-core24/ubuntu-core-24-amd64.img.xz vms/uc24-fde
#
# FLAVOR is the guest:
#   uc24      installed with no TPM, so not encrypted
#   uc24-fde  installed with a TPM 2.0 (swtpm, vm-run --tpm), so ubuntu-save
#             and ubuntu-data are LUKS2 volumes whose keys are sealed to it.
#             Its recovery key (snap recovery --show-keys) is written to
#             --recovery-key-file FILE (required; mode 600; never print it,
#             never put it in the repository): a testbed secret
#
# IMG is ubuntu-core-24-amd64.img.xz, checked against the SHA-256 pinned
# below (and against the SHA256SUMS beside it, if there is one; that file's
# .gpg is checked once, when it is fetched).  Nothing is downloaded here but
# what the guest's snapd fetches from the snap store itself.
#
# --port N: the SSH port on 127.0.0.1 (default: the first free one from the
# 41st of this host's slot range, testvm/hosts.json).
#
# Results, in WORK_DIR:
#   FLAVOR.qcow2         the guest's 8 GiB disk (sparse)
#   uefi-vars.fd         the OVMF variables of the install and of every
#                        build boot (OVMF_CODE_4M.secboot.fd's, from the
#                        Microsoft-keys template): part of the guest, as the
#                        TPM is.  Boot a copy of it (vm-run --uefi-vars)
#   tpm/                 uc24-fde: the swtpm state the keys are sealed to.
#                        Boot a copy of it (vm-run --tpm): a boot that
#                        reseals writes to it, and a disk that is thrown
#                        away afterwards (--ram) then no longer matches
#   tester-password      the password of tester (mode 600; SSH takes the key only)
#   id_ed25519, .pub     SSH key that logs in as tester and as root
#   user-data            the NoCloud user-data
#   build-serial.log     the serial console of the build's one VM, from the
#                        install mode to the last power-off
#   refresh.log          the snap refreshes, each round's output
#   build.txt            what the build did and saw over SSH, with
#                        ubuntu-core/probe.sh's output after each stage
#   nvram.txt            virt-fw-vars' listing of uefi-vars.fd
#   check-boot-1-serial.log, check-boot-2-serial.log, first-boot.txt
#                        two boots as a test boots the guest: an overlay on
#                        FLAVOR.qcow2 and copies of uefi-vars.fd and tpm/,
#                        each to SSH in run mode with no recovery-key prompt
#   vm-run-options       "--uefi --memory 2048": how vm-run boots it, less
#                        --uefi-vars and --tpm, which take copies of the
#                        guest's own (above)
#
# The guests, host name FLAVOR: UEFI + GPT from the pc gadget (BIOS Boot,
# ubuntu-seed vfat ESP, ubuntu-boot, ubuntu-save, ubuntu-data ext4, the last
# grown to the disk), Secure Boot through the gadget's shim and GRUB; user
# tester (sudo without a password); root logs in with the key too; snapd's
# auto-refresh held (snap refresh --hold), 2 GB RAM.
#
# Needs: qemu-system-x86_64 with KVM, OVMF (Secure Boot build, MS vars),
# qemu-img, xz, swtpm (uc24-fde), cloud-localds, openssl, python3, ssh,
# virt-fw-vars (nvram.txt only), and the network (the snap store) for the
# guest.  Runs as an ordinary user in the kvm group.

set -euo pipefail

IMG_NAME=ubuntu-core-24-amd64.img.xz
IMG_SHA256=d82b5b9b86e7b592dc6b48edfbce7b16be6c5064c779db91f720a1ce071622a0
DISK_SIZE=8G
MEMORY=2048
FIRST_BOOT_TIMEOUT=2400
SETTLE_TIMEOUT=3600
BOOT_TIMEOUT=900

here=$(dirname "$(realpath "$0")")
die() { echo "make-ubuntu-core: $*" >&2; exit 1; }

recovery_out="" port=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --recovery-key-file) recovery_out=$(realpath -m "$2"); shift 2 ;;
        --port) port=$2; shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 3 ]] || die "usage: $0 [--recovery-key-file FILE] [--port N] FLAVOR IMG WORK_DIR"
flavor=$1
case $flavor in
    uc24) FDE=no ;;
    uc24-fde) FDE=yes; [[ -n $recovery_out ]] || die "uc24-fde needs --recovery-key-file FILE" ;;
    *) die "FLAVOR is uc24 or uc24-fde, not '$flavor'" ;;
esac
[[ $FDE == yes || -z $recovery_out ]] || die "--recovery-key-file is for uc24-fde only"
img=$(realpath "$2")
[[ -f $img ]] || die "no $img"
GUEST_NAME=$flavor
DISK=$GUEST_NAME.qcow2
mkdir -p "$3"
cd "$3"
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"
[[ -e tpm ]] && die "$3/tpm already exists; move it away to rebuild"
for t in qemu-system-x86_64 qemu-img xz cloud-localds openssl python3 ssh; do command -v $t >/dev/null || die "needs $t"; done
[[ $FDE == no ]] || command -v swtpm >/dev/null || die "needs swtpm"
for f in /usr/share/OVMF/OVMF_CODE_4M.secboot.fd /usr/share/OVMF/OVMF_VARS_4M.ms.fd; do [[ -f $f ]] || die "no $f"; done
t_start=$(date +%s)

# --- The image ---------------------------------------------------------------------
[[ $(sha256sum <"$img" | cut -d' ' -f1) == "$IMG_SHA256" ]] || die "$img's SHA-256 isn't the pinned $IMG_SHA256"
sums=$(dirname "$img")/SHA256SUMS
if [[ -f $sums ]]; then
    grep -q "^$IMG_SHA256 \*\?$IMG_NAME\$" "$sums" || die "$sums doesn't give the pinned SHA-256 for $IMG_NAME"
fi

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

work=$(mktemp -d)
vm_pid=""
cleanup() {
    [[ -n $vm_pid ]] && kill "$vm_pid" 2>/dev/null
    rm -rf "$work"
}
trap cleanup EXIT

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >tester-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "tester@$GUEST_NAME test guest" -f id_ed25519
sed -e "s|@HOSTNAME@|$GUEST_NAME|" -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" "$here/ubuntu-core/user-data" >user-data
umask 022
if grep -n '@[A-Z_0-9]\+@' user-data; then die "user-data placeholders left unfilled"; fi
printf 'instance-id: %s-%s\nlocal-hostname: %s\n' "$GUEST_NAME" "$(date -u +%Y%m%d%H%M%S)" "$GUEST_NAME" >"$work/meta-data"
cloud-localds "$work/cidata.img" user-data "$work/meta-data" || die "cloud-localds failed"

echo "Unpacking $(basename "$img") onto $DISK ($DISK_SIZE)"
xz -dc "$img" | dd of="$work/raw" bs=1M conv=sparse status=none
qemu-img convert -q -f raw -O qcow2 "$work/raw" "$DISK"
rm -f "$work/raw"
qemu-img resize -q "$DISK" "$DISK_SIZE"
rm -f uefi-vars.fd

gssh=(ssh -q -i id_ed25519 -p "$port" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 tester@127.0.0.1)
rssh=("${gssh[@]:0:${#gssh[@]}-1}" root@127.0.0.1)
g() { timeout "${T:-600}" "${gssh[@]}" "$@" </dev/null; }
note() { echo "$*"; echo "== $*" >>build.txt; }
# boot_vm LOG VARS TPM_DIR IMAGE [VM-RUN OPTIONS...]: vm-run in the background (vm_pid)
boot_vm() {
    local log=$1 vars=$2 tpm=$3 image=$4; shift 4
    local opts=(--uefi --memory "$MEMORY" --discard --uefi-vars "$vars" --serial-log "$log")
    [[ $FDE == yes ]] && opts+=(--tpm "$tpm")
    SSH_PORT=$port "$here/vm-run" "${opts[@]}" "$@" "$image" </dev/null >>"$work/vm-run.out" 2>&1 &
    vm_pid=$!
}
# wait_run SECONDS: SSH answers as tester, in run mode
wait_run() {
    local end=$((SECONDS + $1))
    while ((SECONDS < end)); do
        kill -0 "$vm_pid" 2>/dev/null || return 1
        T=20 g 'grep -q "snapd_recovery_mode=run" /proc/cmdline' 2>/dev/null && return 0
        sleep 10
    done
    return 1
}
# settle SECONDS: in run mode, with no snapd change in progress, twice 20 s apart
settle() {
    local end=$((SECONDS + $1)) ok=0 out
    while ((SECONDS < end)); do
        kill -0 "$vm_pid" 2>/dev/null || return 1
        out=$(T=60 g 'grep -q "snapd_recovery_mode=run" /proc/cmdline && echo BKO-RUN; snap changes 2>&1; echo BKO-OK' 2>/dev/null) || out=""
        if [[ $out == *BKO-RUN* && $out == *BKO-OK* ]] &&
            ! awk 'NR > 1 && $2 ~ /^(Do|Doing|Undo|Undoing|Wait|Hold)$/' <<<"$out" | grep -q .; then
            ((++ok >= 2)) && return 0
        else
            ok=0
        fi
        sleep 20
    done
    return 1
}
probe() {  # STAGE
    { echo "=== probe: $1"; T=300 timeout 300 "${gssh[@]}" sudo bash -s "$1" <"$here/ubuntu-core/probe.sh" 2>&1; } >"$work/$1.txt" || true
    cat "$work/$1.txt" >>build.txt
}
val() { sed -n "s/^BKO $2=//p" "$work/$1.txt" | head -1; }   # STAGE KEY
stop_vm() {
    T=30 g 'sudo sync; sudo systemctl poweroff' 2>/dev/null || true
    local _
    for _ in $(seq 90); do kill -0 "$vm_pid" 2>/dev/null || break; sleep 2; done
    if kill -0 "$vm_pid" 2>/dev/null; then kill "$vm_pid"; die "the guest did not power off"; fi
    wait "$vm_pid" 2>/dev/null || true
    vm_pid=""
}

# --- Install mode, then run mode and cloud-init ----------------------------------------
rm -f build.txt refresh.log build-serial.log
note "build of $GUEST_NAME from $(basename "$img") on $(hostname -s), $(date -u +%FT%TZ), SSH port $port"
[[ $FDE == yes ]] && note "with a TPM: $PWD/tpm (new)"
boot_vm build-serial.log "$PWD/uefi-vars.fd" "$PWD/tpm" "$DISK" --disk "$work/cidata.img"
echo "Installing; the console in $PWD/build-serial.log"
wait_run "$FIRST_BOOT_TIMEOUT" ||
    die "no SSH as tester in run mode after $FIRST_BOOT_TIMEOUT s (cloud-init refused the CIDATA drive?); see build-serial.log"
t_install=$(date +%s)
note "run mode answered on SSH after $((t_install - t_start)) s"
T=900 g 'sudo snap wait system seed.loaded' || die "snap wait system seed.loaded failed"
# tester's password, in extrausers' shadow (see ubuntu-core/user-data).
T=60 g "sudo sed -i 's|^tester:[^:]*:|tester:$hash:|' /var/lib/extrausers/shadow" || die "could not set tester's password"
probe installed
[[ $(val installed mode) == run ]] || die "modeenv's mode isn't run: $(val installed mode)"
[[ $(val installed secure_boot) == 1 ]] || die "not booted under Secure Boot: $(val installed secure_boot)"
[[ $(val installed encrypted) == "$FDE" ]] || die "encrypted is $(val installed encrypted), not $FDE"

# --- Refresh until settled, auto-refresh held -----------------------------------------
settle "$SETTLE_TIMEOUT" || die "snapd never settled after the first boot"
T=120 g 'sudo snap refresh --hold' >>build.txt 2>&1 || die "snap refresh --hold failed"
uptodate=no
# (snap refresh --no-wait prints only a change's ID, also for "Refresh all
# snaps: no updates": snap refresh --list says whether any are left.)
for round in 1 2 3 4 5 6 7 8; do
    out=$(T=600 g 'snap refresh --list 2>&1' 2>&1) || true
    { echo "=== round $round, $(date -u +%T): snap refresh --list"; echo "$out"; } >>refresh.log
    if grep -q 'All snaps up to date' <<<"$out"; then uptodate=yes; break; fi
    out=$(T=600 g 'sudo snap refresh --no-wait 2>&1; echo "BKO-EXIT $?"' 2>&1) || true
    { echo "--- snap refresh --no-wait"; echo "$out"; } >>refresh.log
    echo "Refresh round $round: change $(grep -v '^BKO-EXIT' <<<"$out" | tr '\n' ' ')"
    sleep 30
    settle "$SETTLE_TIMEOUT" || die "snapd never settled after refresh round $round; see refresh.log, build-serial.log"
done
[[ $uptodate == yes ]] || die "snaps still not up to date after 8 rounds; see refresh.log"
T=60 g 'snap refresh --time; snap list --all' >>refresh.log 2>&1 || true
probe refreshed
[[ $(val refreshed hold) == forever ]] || die "auto-refresh isn't held: $(val refreshed hold)"

# --- cmdline-append (uc24 only: whether the stock gadget allows it) ----------------------
if [[ $FDE == no ]]; then
    out=$(T=120 g 'sudo snap set system system.kernel.cmdline-append="bko.test=1" 2>&1; echo "BKO-EXIT $?"' 2>&1) || true
    note "snap set system system.kernel.cmdline-append=bko.test=1: $(tr '\n' ' ' <<<"$out")"
    if grep -q '^BKO-EXIT 0$' <<<"$out"; then
        sleep 30
        settle "$SETTLE_TIMEOUT" || die "snapd never settled after cmdline-append"
        note "after it: /proc/cmdline: $(T=30 g 'cat /proc/cmdline')"
        T=120 g 'sudo snap unset system system.kernel.cmdline-append' >>build.txt 2>&1 || true
        sleep 30
        settle "$SETTLE_TIMEOUT" || die "snapd never settled after unsetting cmdline-append"
        note "unset again: /proc/cmdline: $(T=30 g 'cat /proc/cmdline')"
    fi
fi

# --- hello at two revisions, with data in each ------------------------------------------
T=900 g 'sudo snap install hello' >>build.txt 2>&1 || die "snap install hello failed"
rev() { T=60 g "snap list hello | awk 'NR == 2 { print \$3 }'"; }
rev_a=$(rev)
T=60 g "sudo mkdir -p /var/snap/hello/$rev_a && echo 'bko hello revision $rev_a' | sudo tee /var/snap/hello/$rev_a/bko-data >/dev/null" ||
    die "could not write /var/snap/hello/$rev_a"
rev_b=$rev_a
for ch in candidate beta edge; do
    T=900 g "sudo snap refresh hello --channel=latest/$ch" >>build.txt 2>&1 || true
    rev_b=$(rev)
    [[ $rev_b != "$rev_a" ]] && break
done
[[ $rev_b != "$rev_a" ]] || die "hello has one revision in every channel ($rev_a)"
T=60 g "sudo mkdir -p /var/snap/hello/$rev_b && echo 'bko hello revision $rev_b' | sudo tee /var/snap/hello/$rev_b/bko-data-b >/dev/null" ||
    die "could not write /var/snap/hello/$rev_b"
note "hello: revisions $rev_a and $rev_b ($ch), data in /var/snap/hello/{$rev_a,$rev_b}"

# --- A second recovery system, through snapd's REST API ----------------------------------
label=bko-$(date -u +%Y%m%d)
body="{\"action\": \"create\", \"label\": \"$label\", \"test-system\": true, \"mark-default\": false}"
out=$(T=300 g "echo '$body' | sudo snap debug api -X POST -H 'Content-Type: application/json' /v2/systems 2>&1; echo \"BKO-EXIT \$?\"" 2>&1) || true
note "POST /v2/systems $body (snap debug api): $(tr '\n' ' ' <<<"$out")"
grep -q '^BKO-EXIT 0$' <<<"$out" || die "creating the recovery system failed"
change=$(sed -n 's/.*"change": *"\([0-9]*\)".*/\1/p' <<<"$out" | head -1)
sleep 30
settle "$SETTLE_TIMEOUT" || die "snapd never settled after creating the recovery system"
[[ -n $change ]] && note "change $change: $(T=60 g "snap change $change" 2>&1 | tr -s ' ' | tr '\n' ';')"
T=60 g 'sudo snap recovery' | grep -q "^$label " || die "snap recovery doesn't list $label"

# --- The marks, root's key, the recovery key ----------------------------------------------
stamp=$(date -u +%FT%TZ)
# (Not /var/lib/bko-mark: /var/lib is the base snap's, read-only, but for
# the writable paths bound over it.  The top of ubuntu-data instead.)
T=60 g "echo '$GUEST_NAME data $stamp' | sudo tee /run/mnt/data/bko-mark >/dev/null &&
    echo '$GUEST_NAME save $stamp' | sudo tee /run/mnt/ubuntu-save/bko-mark >/dev/null && sync" || die "could not write the marks"
if ! timeout 30 "${rssh[@]}" true </dev/null 2>/dev/null; then
    note "root has no SSH login from cloud-init's disable_root: false; adding the key"
    T=60 g "sudo install -d -m 700 /root/.ssh && echo '$(cat id_ed25519.pub)' | sudo tee /root/.ssh/authorized_keys >/dev/null &&
        sudo chmod 600 /root/.ssh/authorized_keys" || die "could not give root the key"
    timeout 30 "${rssh[@]}" true </dev/null || die "root still has no SSH login"
fi
note "root logs in over SSH with id_ed25519"
if [[ $FDE == yes ]]; then
    umask 077
    T=120 g 'sudo snap recovery --show-keys' >"$work/keys.txt" 2>&1 || true
    sed -n 's/^recovery: *//p' "$work/keys.txt" | head -1 >"$work/recovery"
    umask 022
    grep -Eqx '[0-9]{5}(-[0-9]{5}){7}' "$work/recovery" ||
        die "no recovery key in snap recovery --show-keys' output: $(sed -E 's/[0-9]{5}(-[0-9]{5}){7}/<KEY>/g' "$work/keys.txt" | tr '\n' ' ')"
    mkdir -p "$(dirname "$recovery_out")"
    install -m 600 "$work/recovery" "$recovery_out.new" && mv "$recovery_out.new" "$recovery_out"
    note "recovery key written to $recovery_out (sha256 $(sha256sum <"$recovery_out" | cut -c1-12)); --show-keys' other lines: $(sed -E 's/[0-9]{5}(-[0-9]{5}){7}/<KEY>/g' "$work/keys.txt" | grep -v '^recovery:' | tr '\n' ' ')"
fi
probe final
[[ $(val final recovery_systems) == *"$label"* ]] || die "the second recovery system is missing"
T=120 g 'sudo fstrim -av' >>build.txt 2>&1 || true
stop_vm
command -v virt-fw-vars >/dev/null && virt-fw-vars -i uefi-vars.fd --print >nvram.txt 2>&1 || true
t_build=$(date +%s)

# --- Two boots as a test boots it: an overlay, copies of the variables and TPM -------------
cp uefi-vars.fd "$work/check-vars.fd"
[[ $FDE == yes ]] && cp -a tpm "$work/check-tpm"
qemu-img create -q -f qcow2 -b "$PWD/$DISK" -F qcow2 "$work/check.qcow2"
rm -f first-boot.txt
for n in 1 2; do
    boot_vm "check-boot-$n-serial.log" "$work/check-vars.fd" "$work/check-tpm" "$work/check.qcow2"
    wait_run "$BOOT_TIMEOUT" || die "check boot $n never answered on SSH in run mode; see check-boot-$n-serial.log"
    probe "check$n"
    cat "$work/check$n.txt" >>first-boot.txt
    grep -aqi 'recovery key' "check-boot-$n-serial.log" && die "check boot $n asked for the recovery key; see check-boot-$n-serial.log"
    [[ $(val check$n mark_data) == "$GUEST_NAME data $stamp" && $(val check$n mark_save) == "$GUEST_NAME save $stamp" ]] ||
        die "check boot $n: the marks are wrong"
    [[ $(val check$n encrypted) == "$FDE" ]] || die "check boot $n: encrypted isn't $FDE"
    [[ $(wc -w <<<"$(val check$n recovery_systems)") == 2 && $(wc -w <<<"$(val check$n hello_revs)") == 2 ]] ||
        die "check boot $n: not two recovery systems and two hello revisions"
    T=120 g 'sudo snap wait system seed.loaded && snap changes' >>first-boot.txt 2>&1 || true
    stop_vm
    note "check boot $n: run mode over SSH, no recovery-key prompt, the marks, both recovery systems, both hello revisions"
done

echo "--uefi --memory $MEMORY" >vm-run-options
t_end=$(date +%s)
{ echo "=== build: $((t_end - t_start)) s (install to SSH $((t_install - t_start)) s, refresh and setup $((t_build - t_install)) s, check boots $((t_end - t_build)) s)"
  echo "=== $DISK: $(du -h "$DISK" | cut -f1) on disk, $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["virtual-size"])') bytes virtual"; } | tee -a build.txt >>first-boot.txt
echo "BACKOTTER-INSTALL-OK $GUEST_NAME"
echo "Done: $PWD/$DISK ($(du -h "$DISK" | cut -f1) on disk)"
