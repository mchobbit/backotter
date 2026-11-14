#!/usr/bin/env bash
# Build a TrueNAS Community Edition 25.10 test guest for BackOtter with
# TrueNAS's own installer and updater (docs/design/truenas.md, Decision 1):
# the live ISO booted under QEMU, its installer driven through its JSON-RPC
# API (truenas-installer.service, ws://…/ws behind the ISO's nginx), the
# installed system set up through the middleware's API (wss://…/api/current),
# then updated twice from TrueNAS's offline .update files, each update a new
# boot environment (BE) boot-pool/ROOT/<version>.
#
# Usage: ./make-truenas.sh [--iso-dir DIR] FLAVOR WORK_DIR
#   e.g. ./make-truenas.sh --iso-dir /bkoscratch/rebuild-inputs/truenas25 truenas25 vms/truenas25
#
# FLAVOR is the guest:
#   truenas25         UEFI (OVMF, no Secure Boot), one 32 GiB disk vda;
#                     installed 25.10.6, updated to 25.10.7 then 25.10.8:
#                     three BEs, the oldest marked keep
#   truenas25-mirror  BIOS (SeaBIOS), two 32 GiB disks vda and vdb, boot-pool a
#                     mirror; installed 25.10.6, updated to 25.10.8: two BEs;
#                     plus a 4 GiB disk vdc with data pool "tank" (dataset
#                     tank/share, a few files, an SMB share), which takes the
#                     system dataset (.system) off boot-pool
#
# The inputs, in DIR (default WORK_DIR), none downloaded here, each checked
# against the SHA-256 pinned below (TrueNAS publishes a .sha256 beside each,
# fetched from download.sys.truenas.net):
#   TrueNAS-SCALE-25.10.6.iso (both guests' install)
#   TrueNAS-SCALE-25.10.7.update, TrueNAS-SCALE-25.10.8.update
#
# Per build: truenas_admin's password (new each build, in WORK_DIR only), an
# SSH key that logs in as root and as truenas_admin.  Nothing secret is in
# the repo.
#
# Results, in WORK_DIR:
#   FLAVOR.qcow2 (and FLAVOR-b.qcow2, FLAVOR-data.qcow2)   with a qcow2
#                       snapshot "fresh-install"
#   uefi-vars.fd        truenas25: the OVMF variables, with the installer's
#                       TrueNAS-0 NVRAM entry
#   admin-password      truenas_admin's password (mode 600)
#   id_ed25519, .pub    the SSH key
#   install-serial.log, boot-N-serial.log   the serial consoles
#   install-api.log     the installer's progress messages
#   first-boot.txt      what the last boot showed (BEs, pool, datasets, GRUB,
#                       the ESP, mounts, the config DB)
#   vm-run-options      how vm-run must boot it
#   no-vm-update        why vm-update refuses it (apt is off by design)
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, python3, ssh,
# scp.  Runs as an ordinary user in the kvm group.

set -euo pipefail

declare -A SHA256=(
    [TrueNAS-SCALE-25.10.6.iso]=a204bc92f280cb20032125981cbbdd7f126aab533b2fae4b4e37bc177019cf3b
    [TrueNAS-SCALE-25.10.7.update]=35345e41412e7ce031da5efa1ef2714559f63227cc237c9a8cea850ae5ead8c3
    [TrueNAS-SCALE-25.10.8.update]=c518e5407759686d1ba46512900b887ad4d8f5f7f80e40bc3317bf38b035190d
)
DISK_SIZE=32G
DATA_SIZE=4G
MEMORY=8192
CPUS=2
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=3600
BOOT_TIMEOUT=900
export PATH=$PATH:/usr/sbin:/sbin

here=$(dirname "$(realpath "$0")")
tnapi=$here/truenas/tnapi.py
die() { echo "make-truenas: $*" >&2; exit 1; }

iso_dir=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --iso-dir) iso_dir=$(realpath "$2"); shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || die "usage: $0 [--iso-dir DIR] FLAVOR WORK_DIR"
flavor=$1
case $flavor in
    truenas25) FIRMWARE=uefi NDISKS=1 ISO=TrueNAS-SCALE-25.10.6.iso
        UPDATES=(TrueNAS-SCALE-25.10.7.update TrueNAS-SCALE-25.10.8.update) DATA_POOL="" ;;
    truenas25-mirror) FIRMWARE=bios NDISKS=2 ISO=TrueNAS-SCALE-25.10.6.iso
        UPDATES=(TrueNAS-SCALE-25.10.8.update) DATA_POOL=tank ;;
    *) die "FLAVOR is truenas25 or truenas25-mirror, not '$flavor'" ;;
esac
GUEST_NAME=$flavor
disks=("$GUEST_NAME.qcow2"); [[ $NDISKS == 2 ]] && disks+=("$GUEST_NAME-b.qcow2")
data_disk=""; [[ -n $DATA_POOL ]] && data_disk=$GUEST_NAME-data.qcow2
mkdir -p "$2"
cd "$2"
for d in "${disks[@]}" $data_disk; do [[ -e $d ]] && die "$2/$d already exists; move it away to rebuild"; done
[[ -n $iso_dir ]] || iso_dir=$PWD

work=$(mktemp -d)
qemu_pid=""
cleanup() {
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
trap cleanup EXIT

# --- Check the inputs ------------------------------------------------------------------
for f in "$ISO" "${UPDATES[@]}"; do
    [[ -f $iso_dir/$f ]] || die "no $iso_dir/$f (fetch it first: see the header)"
    want=${SHA256[$f]}
    [[ $want != TODO ]] || die "no SHA-256 pinned for $f"
    [[ $(sha256sum <"$iso_dir/$f" | cut -d' ' -f1) == "$want" ]] || die "$f's SHA-256 isn't the pinned $want"
done

# --- The live system's kernel and initrd, for a serial console -------------------------
# The ISO's GRUB "(115200 baud)" entry's options, booted by QEMU's -kernel so
# the console is on ttyS0 from the start (no menu key to press).
xorriso -osirrox on -indev "$iso_dir/$ISO" -extract /vmlinuz "$work/vmlinuz" \
    -extract /initrd.img "$work/initrd.img" 2>/dev/null || die "$ISO lacks /vmlinuz or /initrd.img"
chmod u+w "$work"/vmlinuz "$work"/initrd.img
live_options="boot=live toram=filesystem.squashfs gfxpayload=text nomodeset console=tty0 console=ttyS0,115200n8"

# --- Secrets ---------------------------------------------------------------------------
umask 077
password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
printf '%s\n' "$password" >admin-password
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
umask 022

free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
ssh_port=$(free_port); web_port=$(free_port); https_port=$(free_port)
ssh_opts=(-q -i id_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 -o BatchMode=yes)
ssh_root=(ssh "${ssh_opts[@]}" -p "$ssh_port" root@127.0.0.1)
api="wss://127.0.0.1:$https_port/api/current"
mw() { python3 "$tnapi" --login truenas_admin admin-password "$@"; }

wait_exit() {  # SECONDS: whether QEMU exited within them
    local _
    for _ in $(seq $(($1 / 5))); do kill -0 "$qemu_pid" 2>/dev/null || { wait "$qemu_pid" 2>/dev/null || true; qemu_pid=""; return 0; }; sleep 5; done
    return 1
}
wait_ssh() {  # SECONDS
    local _
    for _ in $(seq $(($1 / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || return 1
        "${ssh_root[@]}" true </dev/null 2>/dev/null && return 0
        sleep 5
    done
    return 1
}

firmware=()
if [[ $FIRMWARE == uefi ]]; then
    # Plain q35, as vm-run's --no-secure-boot machine.
    firmware=(-machine q35 -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
        -drive "if=pflash,format=raw,unit=1,file=uefi-vars.fd")
else
    firmware=(-machine q35)
fi
# Each disk has a serial number, as real disks have: the middleware refuses
# to make a pool of disks whose serials are the same (none), and keys its
# disks by them.  vm-run-options carries them (--disk-serials) for every
# later boot.
disk_args() { local d i=0; for d in "$@"; do printf '%s\n' -drive "file=$d,if=none,id=bkod$i,format=qcow2,discard=unmap" -device "virtio-blk-pci,drive=bkod$i,serial=BKOTN$i"; i=$((i + 1)); done; }
all=("${disks[@]}"); [[ -n $data_disk ]] && all+=("$data_disk")
mapfile -t all_disks < <(disk_args "${all[@]}")
SERIALS=(); for ((i = 0; i < ${#all[@]}; i++)); do SERIALS+=("BKOTN$i"); done   # (disk_args's, for vm-run-options)
net=(-nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$ssh_port-:22,hostfwd=tcp:127.0.0.1:$web_port-:80,hostfwd=tcp:127.0.0.1:$https_port-:443")
common=(-enable-kvm -cpu host -smp "$CPUS" -m "$MEMORY")

boot_n=0
boot_installed() {  # boot the installed disks, in the background
    boot_n=$((boot_n + 1))
    qemu-system-x86_64 "${common[@]}" "${firmware[@]}" "${all_disks[@]}" "${net[@]}" \
        -display none -serial "file:boot-$boot_n-serial.log" -monitor none &
    qemu_pid=$!
}
wait_middleware() {  # until the middleware answers a login (it starts after sshd)
    mw --wait "$BOOT_TIMEOUT" "$api" system.ready >"$work/ready.json" ||
        die "the middleware never answered (boot $boot_n); see boot-$boot_n-serial.log"
    local _
    for _ in $(seq 120); do
        [[ $(mw "$api" system.ready) == true ]] && return 0
        sleep 5
    done
    die "the middleware never became ready (boot $boot_n)"
}
power_off() {
    mw "$api" system.shutdown '"bko make-truenas"' >/dev/null 2>&1 || "${ssh_root[@]}" poweroff </dev/null 2>/dev/null || true
    wait_exit 300 && return 0
    kill "$qemu_pid" 2>/dev/null
    die "the guest did not power off"
}

# --- Install -----------------------------------------------------------------------------
[[ $FIRMWARE == uefi ]] && cp "$OVMF_VARS" uefi-vars.fd
for d in "${disks[@]}"; do qemu-img create -q -f qcow2 "$d" "$DISK_SIZE"; done
[[ -n $data_disk ]] && qemu-img create -q -f qcow2 "$data_disk" "$DATA_SIZE"
echo "Installing $ISO (the console in $2/install-serial.log)"
t0=$SECONDS
qemu-system-x86_64 "${common[@]}" "${firmware[@]}" "${all_disks[@]}" \
    -drive "file=$iso_dir/$ISO,media=cdrom,readonly=on,if=ide,index=0" \
    "${net[@]}" -kernel "$work/vmlinuz" -initrd "$work/initrd.img" -append "$live_options" \
    -display none -serial file:install-serial.log -monitor none -no-reboot &
qemu_pid=$!
inst="ws://127.0.0.1:$web_port/ws"
python3 "$tnapi" --wait 900 "$inst" list_disks >"$work/disks.json" ||
    die "the installer's API never answered; see install-serial.log"
want=(vda); [[ $NDISKS == 2 ]] && want=(vda vdb)
for d in "${want[@]}"; do grep -q "\"$d\"" "$work/disks.json" || { cat "$work/disks.json" >&2; die "the installer doesn't list $d"; }; done
disks_json=$(printf '"%s",' "${want[@]}"); disks_json="[${disks_json%,}]"
pmbr=false; [[ $FIRMWARE == bios ]] && pmbr=true
params=$(python3 -c 'import json, sys; print(json.dumps({"disks": json.loads(sys.argv[1]), "set_pmbr": sys.argv[2] == "true",
    "authentication": {"username": "truenas_admin", "password": open("admin-password").read().strip()}}))' "$disks_json" "$pmbr")
# (The params hold the password: passed through a file descriptor, not argv.)
python3 - "$tnapi" "$inst" 3<<<"$params" 2>install-api.log <<'EOF' || { tail -n 20 install-api.log >&2; die "install failed; see install-api.log and install-serial.log"; }
import json, os, sys, importlib.util
spec = importlib.util.spec_from_file_location("tnapi", sys.argv[1]); t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
params = json.loads(os.fdopen(3).read())
c = t.Client(sys.argv[2])
c.call("install", params, timeout=7200)
print("installed", file=sys.stderr)
try:
    c.call("shutdown", timeout=60)
except Exception:
    pass
EOF
wait_exit 300 || { kill "$qemu_pid"; die "the installer didn't power off after the install"; }
echo "Installed in $((SECONDS - t0)) s"

# --- First boot: SSH, the serial console, keys, the data pool --------------------------
echo "First boot (the console in $2/boot-1-serial.log)"
boot_installed
wait_middleware
pub=$(cat id_ed25519.pub)
admin_id=$(mw "$api" user.query '[["username", "=", "truenas_admin"]]' '{"get": true}' | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
root_id=$(mw "$api" user.query '[["username", "=", "root"]]' '{"get": true}' | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
mw "$api" user.update "$admin_id" "$(python3 -c 'import json, sys; print(json.dumps({"sshpubkey": sys.argv[1], "sudo_commands_nopasswd": ["ALL"]}))' "$pub")" >/dev/null
mw "$api" user.update "$root_id" "$(python3 -c 'import json, sys; print(json.dumps({"sshpubkey": sys.argv[1]}))' "$pub")" >/dev/null
mw "$api" ssh.update '{"passwordauth": false}' >/dev/null
mw "$api" service.update '"ssh"' '{"enable": true}' >/dev/null
mw --job "$api" service.control '"START"' '"ssh"' >/dev/null
mw "$api" system.advanced.update '{"serialconsole": true, "serialport": "ttyS0", "serialspeed": "115200"}' >/dev/null
wait_ssh 300 || die "no SSH as root after enabling it; see boot-1-serial.log"
if [[ -n $DATA_POOL ]]; then
    mw --job "$api" pool.create "$(python3 -c 'import json, sys; print(json.dumps({"name": sys.argv[1], "topology": {"data": [{"type": "STRIPE", "disks": ["vdc"]}]}}))' "$DATA_POOL")" >/dev/null
    mw "$api" pool.dataset.create "{\"name\": \"$DATA_POOL/share\", \"share_type\": \"SMB\"}" >/dev/null
    "${ssh_root[@]}" "for i in 1 2 3; do head -c 65536 /dev/urandom >/mnt/$DATA_POOL/share/file\$i; done; echo backotter >/mnt/$DATA_POOL/share/readme.txt" </dev/null
    mw "$api" sharing.smb.create "{\"path\": \"/mnt/$DATA_POOL/share\", \"name\": \"share\"}" >/dev/null
fi

# --- Updates: each a new boot environment ----------------------------------------------
for u in "${UPDATES[@]}"; do
    echo "Updating with $u"
    scp "${ssh_opts[@]}" -P "$ssh_port" "$iso_dir/$u" "root@127.0.0.1:/root/$u" || die "could not copy $u in"
    mw --job "$api" update.manual "\"/root/$u\"" >/dev/null || die "update.manual $u failed"
    mw "$api" system.reboot '"bko make-truenas update"' >/dev/null || true
    wait_exit 600 || true        # (a reboot stays in the same QEMU process)
    sleep 30
    wait_middleware
done
oldest=${ISO#TrueNAS-SCALE-}; oldest=${oldest%.iso}
mw "$api" boot.environment.keep "{\"id\": \"$oldest\", \"value\": true}" >/dev/null

# --- What the system is now: first-boot.txt ----------------------------------------------
{ mw "$api" boot.environment.query; mw "$api" system.info; } >first-boot.txt 2>&1
"${ssh_root[@]}" 'set -x; cat /etc/version /etc/os-release /proc/cmdline; uname -r; hostname; cat /etc/fstab; od -An -tx1 /etc/hostid
    findmnt -rno TARGET,SOURCE,FSTYPE,OPTIONS
    lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTLABEL,LABEL,UUID,PARTUUID; for d in /dev/vd?; do sfdisk -d $d; done; blkid
    zfs version; zpool status; zpool get all boot-pool | grep -v -E "feature@.*disabled"
    zfs list -o name,used,mountpoint,canmount,mounted,readonly,origin -r boot-pool
    zfs get -s local,received all -r boot-pool; zfs list -t snapshot -r boot-pool
    cat /conf/truenas_root_ds.json; ls -la /data; cat /boot/grub/grub.cfg; ls -la /boot /boot/grub
    for d in /dev/vd?2; do mkdir -p /run/bko-esp; mount -o ro $d /run/bko-esp && { find /run/bko-esp -printf "%M %10s %P\n" | sort -k3; umount /run/bko-esp; }; done
    efibootmgr -v; dpkg -l | grep -Ei "grub|zfs|truenas|middleware|linux-image" ; ls /usr/local/bin | grep -i truenas
    sqlite3 /data/freenas-v1.db "PRAGMA integrity_check"; zpool list; systemctl --failed --no-legend' \
    </dev/null >>first-boot.txt 2>&1 || true
grep -q '"active": true' first-boot.txt || die "no active BE in boot.environment.query; see first-boot.txt"
nbe=$(python3 -c 'import json, sys
t = open("first-boot.txt").read(); i = t.index("["); print(len(json.JSONDecoder().raw_decode(t[i:])[0]))')
[[ $nbe == $((${#UPDATES[@]} + 1)) ]] || die "$nbe boot environments, not $((${#UPDATES[@]} + 1)); see first-boot.txt"
last=${UPDATES[-1]#TrueNAS-SCALE-}; last=${last%.update}
version=$("${ssh_root[@]}" 'cat /etc/version' </dev/null 2>/dev/null || true)   # (no newline at its end)
[[ $version == "$last" ]] || die "/etc/version is '$version', not $last; see first-boot.txt"
power_off

for d in "${all[@]}"; do qemu-img snapshot -c fresh-install "$d"; done
opts=""; [[ $FIRMWARE == uefi ]] && opts="--uefi --no-secure-boot"
[[ $NDISKS == 2 ]] && opts="$opts --disk ${disks[1]}"
[[ -n $data_disk ]] && opts="$opts --disk $data_disk"
opts="$opts --disk-serials $(IFS=,; echo "${SERIALS[*]}")"
echo "${opts# }" >vm-run-options
echo "TrueNAS: updates are new boot environments made by rebuilding with the newer .update file (testvm/make-truenas.sh; docs/design/truenas.md), apt is off" >no-vm-update
echo "Done: ${all[*]} in $2 ($(du -ch "${all[@]}" | tail -n 1 | cut -f1) on disk), snapshot \"fresh-install\", $nbe BEs"
