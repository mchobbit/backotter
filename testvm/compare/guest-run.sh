#!/usr/bin/env bash
# Boot a throwaway (--ram) copy of a test guest, copy FILEs into its /root,
# run COMMAND there as root, print its output, and power the guest off: for
# one-off checks in a guest (a probe, a single tool's behaviour) beside the
# round trips.
#
# Usage: guest-run.sh GUEST [PORT] COMMAND [FILE ...]
#
# PORT is the guest's SSH port here (default 2712).  COMMAND runs with the
# guest's /bin/sh, whatever root's login shell (DragonFly's is csh), and may
# span lines.  FILEs go over ssh with cat (AlpineBSD has no scp or sftp
# server), executable.  Exits with COMMAND's status.  MEMORY (MB, default
# 4096) is the guest's; T (seconds, default 3600) bounds COMMAND.  Serial
# console and vm-run's output: ~/backotter-work/logs/guest-run-GUEST-PORT/
# (~/backotter-work is BKO_WORK, by default the directory that holds this
# testvm/).
#
# Needs: qemu-system-x86_64 with KVM, testvm/vm-run, the guest in
# ~/backotter-work/vms, ssh.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$(cd "$here/../.." && pwd)}
die() { echo "guest-run: $*" >&2; exit 2; }
# shellcheck source=../vm-lib.sh
. "$here/../vm-lib.sh"

[[ $# -ge 2 ]] || die "usage: guest-run.sh GUEST [PORT] COMMAND [FILE ...]"
guest=$1; shift
port=2712
if [[ $1 =~ ^[0-9]+$ && $# -ge 2 ]]; then port=$1; shift; fi
cmd=$1; shift
for f in "$@"; do [[ -f $f ]] || die "no $f"; done
trap vm_halt EXIT
vm_boot "$guest" "$port" "$WORK/logs/guest-run-$guest-$port" --ram --memory "${MEMORY:-4096}" ||
    die "$guest didn't come up"
for f in "$@"; do
    vm_put "$f" "/root/$(basename "$f")" || die "copying $f failed"
done
rc=0
vm_sh "$cmd" 2>&1 || rc=$?
exit "$rc"
