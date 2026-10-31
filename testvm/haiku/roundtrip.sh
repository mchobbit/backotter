#!/usr/bin/env bash
# Back up and restore a tree of files with Haiku's typed attributes with
# BackOtter's rustic, in a throwaway copy of the haiku6 test guest, and check
# that every attribute (type and data), mode, time and content comes back:
#   1. a restore into a new directory
#   2. an in-place restore over that restore after attrs.py mutate has
#      changed its attributes: extras removed, the rest put back
# The tree (attrs.py make) has attributes of many types, empty ones, a 20 KB
# one, xattrs (type XATR), and attributes on a directory, a symlink itself,
# a 0444 file and a file with a non-ASCII name.
#
# Usage: roundtrip.sh RUSTIC_BKO [PORT]
#   e.g. roundtrip.sh ~/backotter-work/bko-bin/haiku/rustic-bko
#
# RUSTIC_BKO is rustic built for Haiku (stick/haiku/build-rustic.sh).  PORT
# (default 2431) is the guest's SSH port here.  Exits 1 if anything differs,
# with the differences.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
die() { echo "roundtrip: $*" >&2; exit 1; }

[[ $# -ge 1 && $# -le 2 ]] || die "usage: $0 RUSTIC_BKO [PORT]"
rustic=$1
port=${2:-2431}
vm=$WORK/vms/haiku6
[[ -f $rustic ]] || die "no $rustic"
[[ -f $vm/haiku6.qcow2 && -f $vm/id_ed25519 && -f $vm/vm-run-options ]] || die "no VM in $vm"
pgrep -f "^qemu-system.*:$port-:22" >/dev/null && die "port $port is in use"

work=$(mktemp -d)
ssh_g() {
    timeout "${T:-1800}" ssh -q -i "$vm/id_ed25519" -p "$port" -o BatchMode=yes \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 \
        user@127.0.0.1 "$@"
}
cleanup() {
    T=30 ssh_g 'shutdown -q' >/dev/null 2>&1 || true
    for _ in $(seq 60); do pgrep -f "^qemu-system.*:$port-:22" >/dev/null || break; sleep 2; done
    pkill -f "^qemu-system.*:$port-:22" 2>/dev/null || true
    rm -rf "$work"
}
trap cleanup EXIT

echo "Booting a throwaway copy of haiku6 on port $port"
# shellcheck disable=SC2046
SSH_PORT=$port setsid -f "$here/../vm-run" $(<"$vm/vm-run-options") --snapshot \
    --serial-log "$work/serial.log" "$vm/haiku6.qcow2" </dev/null >"$work/vm-run.log" 2>&1
for _ in $(seq 30); do pgrep -f "^qemu-system.*:$port-:22" >/dev/null && break; sleep 1; done
deadline=$((SECONDS + 600))
until T=15 ssh_g true 2>/dev/null; do
    [[ $SECONDS -lt $deadline ]] || die "the guest's sshd didn't come up"
    pgrep -f "^qemu-system.*:$port-:22" >/dev/null || { cat "$work/vm-run.log" >&2; die "QEMU exited"; }
    sleep 5
done

ssh_g 'rm -rf ~/rt && mkdir ~/rt && cat >~/rt/rustic && chmod 755 ~/rt/rustic' <"$rustic"
ssh_g 'cat >~/rt/attrs.py' <"$here/attrs.py"
ssh_g '~/rt/rustic --version'

# On the guest: rt/src is backed up, restored to rt/dst, then rt/dst is
# changed and restored over.  Each dump is compared with the source's.
ssh_g 'bash -s' <<'EOF'
set -euo pipefail
cd ~/rt
export RUSTIC_PASSWORD=roundtrip RUSTIC_REPOSITORY=$HOME/rt/repo
# Haiku has no zoneinfo for rustic's jiff to find, nor TZ: a POSIX TZ needs
# none, and spares a warning for each time it shows.
export TZ=UTC0
python3 attrs.py make src
python3 attrs.py dump src >src.json
./rustic init >init.log 2>&1 || { cat init.log; exit 1; }
./rustic backup "$HOME/rt/src" >backup.log 2>&1 || { cat backup.log; exit 1; }
grep -i warn backup.log || true
./rustic restore "latest:$HOME/rt/src" dst >restore1.log 2>&1 || { cat restore1.log; exit 1; }
grep -i warn restore1.log || true
python3 attrs.py dump dst >dst1.json
python3 attrs.py mutate dst
./rustic restore "latest:$HOME/rt/src" dst >restore2.log 2>&1 || { cat restore2.log; exit 1; }
grep -i warn restore2.log || true
python3 attrs.py dump dst >dst2.json
EOF

status=0
for n in 1 2; do
    what=$([[ $n == 1 ]] && echo "restore into a new directory" || echo "in-place restore over changed attributes")
    if ssh_g "diff ~/rt/src.json ~/rt/dst$n.json" >"$work/diff$n"; then
        echo "OK: $what"
    else
        echo "DIFFERENT: $what (< source, > restored)"
        cat "$work/diff$n"
        status=1
    fi
done
echo "Entries: $(ssh_g 'wc -l <~/rt/src.json'), attributes: $(ssh_g "grep -o '\"[0-9a-f]\{8\}\", ' ~/rt/src.json | wc -l")"
exit $status
