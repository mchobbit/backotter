#!/usr/bin/env bash
# ZFS round trips inside a Linux guest whose root is on ZFS (SPEC2 8.2),
# with the guest's own ZFS module and tools rather than the host's:
# ubuntu24 (a plain root pool) and ubuntu26-zfsmirror (an encrypted root
# pool on a mirror of two disks, whose passphrase, vms/GUEST/zfs-passphrase,
# it asks for on its console at every boot; this script types it).  In a
# throwaway (--ram) copy of GUEST, fs_roundtrip.py (with compare.py,
# fsmeta.py and torture.sh) runs once per FSTYPE: torture.sh's files with
# ZFS DOS flags, extended attributes, POSIX ACLs and project IDs, backed up
# and restored by both tools in all four directions, compared with
# fsmeta.py, restored again over a changed copy, and the two tools'
# snapshots compared node by node.
#
# Usage: zfs-guest-rt.sh GUEST [PORT] [FSTYPE ...]
#   GUEST    ubuntu24, ubuntu26-zfsmirror (any guest with python3 and ZFS)
#   PORT     the guest's SSH port here (default 2713)
#   FSTYPE   zfs     pools on files in the guest's /var/tmp
#            zfs-ds  file systems in the guest's root pool (rpool: on
#                    ubuntu26-zfsmirror encrypted and mirrored)
#            (default: both)
#
# RESTIC and RUSTIC (environment) name the Linux binaries; by default
# ~/backotter-work/bko-bin/linux/restic-bko and rustic-bko (BKO_BIN
# replaces bko-bin).  ~/backotter-work is BKO_WORK, by default the
# directory that holds this testvm/.  setfattr and setfacl are installed in
# the copy with apt if it lacks them (torture.sh skips what they set
# otherwise).  Writes ~/backotter-work/zfs-guest/GUEST/FSTYPE.log and the
# guest's fs-roundtrip/FSTYPE/ (results.json, the fsmeta records and
# diffs; not the repositories, which are deleted in the guest after each
# FSTYPE) into FSTYPE/ there; ZFS_GUEST_LOGS replaces
# ~/backotter-work/zfs-guest.  MEMORY (MB, default 6144) is the guest's;
# RT_TIMEOUT (seconds, default 7200) bounds each FSTYPE.
#
# Needs: qemu-system-x86_64 with KVM, testvm/vm-run, the guest in
# ~/backotter-work/vms, ssh, tar.

set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$(cd "$here/../.." && pwd)}
BKO_BIN=${BKO_BIN:-$WORK/bko-bin}
die() { echo "zfs-guest-rt: $*" >&2; exit 2; }
# shellcheck source=../vm-lib.sh
. "$here/../vm-lib.sh"

[[ $# -ge 1 ]] || { sed -n '2,13p' "$0" >&2; exit 2; }
guest=$1; shift
port=2713
if [[ ${1:-} =~ ^[0-9]+$ ]]; then port=$1; shift; fi
fstypes=("$@")
[[ $# -gt 0 ]] || fstypes=(zfs zfs-ds)
for f in "${fstypes[@]}"; do [[ $f == zfs || $f == zfs-ds ]] || die "no such FSTYPE: $f"; done
restic=${RESTIC:-$BKO_BIN/linux/restic-bko} rustic=${RUSTIC:-$BKO_BIN/linux/rustic-bko}
[[ -f $restic && -f $rustic ]] || die "no $restic or no $rustic (set RESTIC and RUSTIC)"
dir=$WORK/vms/$guest
[[ -d $dir ]] || die "no guest $guest in $WORK/vms"
logs=${ZFS_GUEST_LOGS:-$WORK/zfs-guest}/$guest
mkdir -p "$logs" && logs=$(cd "$logs" && pwd)

# vm_boot, for a guest that asks for its pool's passphrase on the serial
# console: the console on a pipe instead of a file, the passphrase typed
# each time the prompt comes.  (vm-run-options name its second disk
# relative to its directory.)
boot_with_passphrase() {
    local pass opts=() deadline answered=0 n
    pass=$(cat "$dir/zfs-passphrase")
    if [[ -n $(ss -Hltn "sport = :$port" 2>/dev/null) ]]; then echo "port $port is in use" >&2; return 1; fi
    if [[ -f $dir/vm-run-options ]]; then read -ra opts <"$dir/vm-run-options" || true; fi
    rm -f "$logs/console.in"; mkfifo "$logs/console.in"
    # Open for reading and writing: neither end waits for the other.
    exec 9<>"$logs/console.in"
    VM_GUEST=$guest VM_PORT=$port VM_KEY=$dir/id_ed25519
    (cd "$dir" && SSH_PORT=$port exec setsid "$VM_LIB_DIR/vm-run" "${opts[@]}" --ram \
        --memory "${MEMORY:-6144}" "$guest.qcow2" <"$logs/console.in" >"$logs/serial.log" 2>"$logs/vm-run.log") &
    VM_PID=$!
    deadline=$((SECONDS + ${VM_BOOT_TIMEOUT:-900}))
    until T=15 vm_ssh true 2>/dev/null; do
        if ! kill -0 "$VM_PID" 2>/dev/null; then
            echo "QEMU exited; $logs/vm-run.log says:" >&2; tail -n 5 "$logs/vm-run.log" >&2
            VM_PID=""; return 1
        fi
        n=$(grep -aoi 'enter passphrase' "$logs/serial.log" | wc -l)
        if [[ $n -gt $answered ]]; then
            sleep 1; printf '%s\r' "$pass" >&9; answered=$n
            echo "typed the passphrase (prompt $n)" >&2
        fi
        if [[ $SECONDS -ge $deadline ]]; then
            echo "$guest's sshd didn't come up (console: $logs/serial.log)" >&2; vm_halt; return 1
        fi
        sleep 3
    done
}

trap 'vm_halt; exec 9>&-' EXIT
echo "== $guest: booting a throwaway copy" >&2
if [[ -f $dir/zfs-passphrase ]]; then
    boot_with_passphrase || exit 1
else
    vm_boot "$guest" "$port" "$logs/vm" --ram --memory "${MEMORY:-6144}" || exit 1
fi
vm_ssh 'mkdir -p /root/bko/testvm/compare /root/bko/bin' || exit 1
for f in fs_roundtrip.py compare.py fsmeta.py torture.sh; do
    vm_put "$here/$f" "/root/bko/testvm/compare/$f" || exit 1
done
vm_put "$restic" /root/bko/bin/restic-bko && vm_put "$rustic" /root/bko/bin/rustic-bko || exit 1
{
    echo "######## $guest: $(vm_ssh '/root/bko/bin/restic-bko version' | head -n 1), $(vm_ssh '/root/bko/bin/rustic-bko --version')"
    vm_sh 'uname -r; zfs version 2>&1 | tr "\n" " "; echo
        zpool list -H -o name,health,size,free; zfs get -H -o name,value encryption,keystatus "$(zfs list -H -o name / | cut -d/ -f1)"
        zpool status -P | grep -E "mirror|/dev/" | sed "s/^/  /"
        if ! command -v setfattr >/dev/null || ! command -v setfacl >/dev/null; then
            echo "installing attr and acl (setfattr, setfacl)"
            { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q attr acl; } >/root/bko/apt.log 2>&1 ||
                echo "zfs-guest-rt: could not install attr and acl: torture.sh will skip xattrs and ACLs"
        fi' 2>&1
} | tee "$logs/guest.log"
status=0
for fs in "${fstypes[@]}"; do
    echo "== $guest: fs_roundtrip.py $fs" >&2
    T=${RT_TIMEOUT:-7200} vm_ssh "RESTIC_BKO=/root/bko/bin/restic-bko RUSTIC_BKO=/root/bko/bin/rustic-bko python3 -I /root/bko/testvm/compare/fs_roundtrip.py $fs 2>&1" \
        >"$logs/$fs.log" 2>&1 || status=1
    # A few tries: right after a run, ubuntu26-zfsmirror's sshd once closed
    # the connection at once (round 7: no tar stream, the next ssh fine).
    copied=0
    for try in 1 2 3; do
        rm -rf "${logs:?}/$fs" && mkdir -p "$logs/$fs"
        if T=600 vm_ssh "cd /root/bko/fs-roundtrip/$fs && tar -cf - --exclude=./restic-bko --exclude=./rustic-bko --exclude=./restic-to-rustic --exclude=./rustic-to-restic ." |
            tar -C "$logs/$fs" -xf -; then
            copied=1; break
        fi
        echo "zfs-guest-rt: copying $fs's results failed (try $try)" >&2; sleep 15
    done
    # The repositories take room the next FSTYPE needs (the root pool is
    # the guest's disk); kept if their results couldn't be copied.
    if [[ $copied == 1 ]]; then vm_ssh "rm -rf /root/bko/fs-roundtrip/$fs"; else status=1; fi
    # The summary fs_roundtrip.py prints at the end.
    sed -n '/ onto .*: skipped /,$p; /^fs_roundtrip: skipped/p' "$logs/$fs.log"
done
exit $status
