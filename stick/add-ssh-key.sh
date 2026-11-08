#!/usr/bin/env bash
# Let SSH keys into a Linux rescue image: at boot, a stick with this file starts
# sshd by itself and takes these keys for the "backotter" user, who has
# passwordless sudo (stick/layer/usr/lib/backotter/key-access).
#
# Usage: ./add-ssh-key.sh [--host-key PRIVATE_KEY] IMG PUBKEY_FILE...
#
# IMG is an image from make-img.sh, or a stick it was written to.  This replaces
# backotter/authorized_keys in its BACKOTTER partition, and changes nothing else.
# Anyone who can write to the stick could add a key the same way, so treat it as
# you would any boot medium.
#
# --host-key makes the stick present that ed25519 host key on every boot, so a
# controller can pin it.  That puts a private key on the stick, readable by
# anyone holding it: for a testbed only.  Without it, no secret goes on the stick.

set -euo pipefail
export PATH=$PATH:/usr/sbin:/sbin MTOOLS_SKIP_CHECK=1

die() { echo "add-ssh-key: $*" >&2; exit 1; }

host_key=
if [[ ${1:-} == --host-key ]]; then
    host_key=${2:?--host-key needs a file}
    shift 2
    grep -q 'BEGIN OPENSSH PRIVATE KEY' "$host_key" || die "$host_key isn't an OpenSSH private key"
fi
[[ $# -ge 2 ]] || die "usage: $0 [--host-key PRIVATE_KEY] IMG PUBKEY_FILE..."
img=$1; shift
for c in sfdisk mcopy mdir python3; do
    command -v "$c" >/dev/null || die "missing $c"
done
[[ -f $img || -b $img ]] || die "$img is neither a file nor a block device"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
for k in "$@"; do
    grep -qE '^(ssh-(ed25519|rsa)|ecdsa-sha2-[a-z0-9-]+|sk-[a-z0-9@.-]+) ' "$k" || die "$k has no public key"
    ! grep -q 'PRIVATE KEY' "$k" || die "$k is a private key"
    cat "$k" >>"$work/authorized_keys"
done

# The partition make-img.sh labels BACKOTTER; marker.py makes it number 3.
start=$(sfdisk -J "$img" | python3 -c '
import json, sys
print(next(p["start"] for p in json.load(sys.stdin)["partitiontable"]["partitions"]
           if p["node"].endswith("3")))') || die "$img has no partition 3"
target=$img@@$((start * 512))
mdir -i "$target" ::/backotter/stick.json >/dev/null 2>&1 || die "partition 3 of $img isn't a BACKOTTER partition"
mcopy -o -i "$target" "$work/authorized_keys" ::/backotter/authorized_keys
[[ -z $host_key ]] || mcopy -o -i "$target" "$host_key" ::/backotter/ssh_host_ed25519_key
mdir -i "$target" ::/backotter/
