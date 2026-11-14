#!/usr/bin/env bash
# The host-side facts of an Ubuntu Core guest's disk, read only, with the
# guest powered off: the GPT (names, type GUIDs, PARTUUIDs, sizes), each
# partition's filesystem or LUKS2 label and UUID (blkid on the first 64 MiB
# of the partition, copied out with qemu-img dd), and for a LUKS2
# partition its header (cryptsetup luksDump and the JSON metadata: the
# keyslots and tokens, no key material).  With KEY_FILE (the recovery key's
# dashed form, as the builder writes it), it also tests whether that key
# opens each LUKS2 header, as 16 raw bytes (ubuntu-core/recovery-key-bytes.py)
# and as the dashed string itself, with cryptsetup open --test-passphrase
# on the header copy (no device-mapper, nothing opened).  It never prints a
# key, only the converted bytes' SHA-256 prefix.
#
# Usage: disk-facts.sh IMAGE [KEY_FILE]   (cryptsetup and blkid: run as root, or with sudo -n)
set -euo pipefail
here=$(dirname "$(realpath "$0")")
die() { echo "disk-facts: $*" >&2; exit 1; }
[[ $# -ge 1 && $# -le 2 ]] || die "usage: $0 IMAGE [KEY_FILE]"
image=$(realpath "$1") key=${2:-}
[[ -f $image ]] || die "no $image"
qemu-img info "$image" >/dev/null 2>&1 || die "can't open $image (a VM running on it?)"
as_root=(); [[ $(id -u) == 0 ]] || as_root=(sudo -n)
PATH=$PATH:/usr/sbin:/sbin
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fmt=$(qemu-img info --output=json "$image" | python3 -c 'import json, sys; print(json.load(sys.stdin)["format"])')
qemu-img dd -f "$fmt" -O raw bs=1M count=1 if="$image" of="$tmp/gpt" 2>/dev/null
echo "=== $image: GPT"
python3 -I - "$tmp/gpt" >"$tmp/parts" <<'EOF'
import struct, sys, uuid
d = open(sys.argv[1], "rb").read()
h = d[512:1024]
assert h[:8] == b"EFI PART", "no GPT"
lba, n, size = struct.unpack_from("<QII", h, 72)
for i in range(n):
    e = d[lba * 512 + i * size: lba * 512 + (i + 1) * size]
    if e[:16] == bytes(16):
        continue
    first, last, attrs = struct.unpack_from("<QQQ", e, 32)
    name = e[56:128].decode("utf-16-le").rstrip("\0")
    print(i + 1, first, last, uuid.UUID(bytes_le=e[:16]), uuid.UUID(bytes_le=e[16:32]), hex(attrs), name.replace(" ", "_"), sep="\t")
EOF
printf 'nr\tfirst\tlast\ttype\tpartuuid\tattrs\tname\n'; cat "$tmp/parts"
while IFS=$'\t' read -r nr first last type partuuid attrs name; do
    mib=$(( (last - first + 1) / 2048 ))
    n=$(( mib < 64 ? mib : 64 ))
    off=$(( first / 2048 ))
    (( first % 2048 == 0 )) || die "partition $nr isn't MiB-aligned"
    # (qemu-img dd's count is where the copy ends, in blocks from the start, not how many.)
    qemu-img dd -f "$fmt" -O raw bs=1M skip="$off" count="$((off + n))" if="$image" of="$tmp/p$nr" 2>/dev/null ||
        die "qemu-img dd of partition $nr failed"
    echo "=== partition $nr ($name): ${mib} MiB at MiB $off"
    # (blkid -p takes no ext4 bigger than its device, the 64 MiB copy: dumpe2fs -h does.)
    "${as_root[@]}" blkid -p -o export "$tmp/p$nr" 2>/dev/null | grep -E '^(TYPE|LABEL|UUID|VERSION|SUBSYSTEM)=' ||
        "${as_root[@]}" dumpe2fs -h "$tmp/p$nr" 2>/dev/null |
            grep -E '^(Filesystem volume name|Filesystem UUID|Filesystem features|Block count|Block size):' ||
        echo "(blkid, dumpe2fs: nothing)"
    if "${as_root[@]}" cryptsetup isLuks "$tmp/p$nr" 2>/dev/null; then
        echo "--- luksDump"
        # (Less the salts' and digests' hex lines: long, and of no use here.)
        "${as_root[@]}" cryptsetup luksDump "$tmp/p$nr" | grep -vE '^\s+(Salt|Digest):|^\s+([0-9a-f]{2} ){4,}[0-9a-f]{2}\s*$'
        echo "--- JSON metadata (tokens, keyslots' kinds)"
        "${as_root[@]}" cryptsetup luksDump --dump-json-metadata "$tmp/p$nr" 2>/dev/null |
            python3 -I -c 'import json, sys
m = json.load(sys.stdin)
for k, s in sorted(m.get("keyslots", {}).items()):
    print("keyslot", k, s.get("type"), s.get("kdf", {}).get("type"), "priority", s.get("priority", "normal"))
for k, t in sorted(m.get("tokens", {}).items()):
    print("token", k, t.get("type"), "keyslots", t.get("keyslots"), "fields", sorted(f for f in t if f not in ("type", "keyslots")))
print("config", json.dumps(m.get("config", {})))' || echo "(no --dump-json-metadata in this cryptsetup)"
        if [[ -n $key ]]; then
            echo "--- the recovery key"
            python3 -I "$here/recovery-key-bytes.py" "$tmp/key16" <"$key" | sed 's/^/16 bytes, sha256 /'
            tr -d '\n' <"$key" >"$tmp/keydash"
            for kf in key16 keydash; do
                if out=$("${as_root[@]}" cryptsetup open --test-passphrase -v --key-file "$tmp/$kf" "$tmp/p$nr" 2>&1); then
                    echo "$kf: opens it ($(grep -o 'Key slot [0-9]* unlocked' <<<"$out" | head -1))"
                else
                    echo "$kf: does not open it ($(tail -n 1 <<<"$out"))"
                fi
            done
        fi
    fi
done <"$tmp/parts"
echo "=== qcow2: $(du -k "$image" | cut -f1) KiB allocated, $(qemu-img info --output=json "$image" | python3 -c 'import json, sys; print(json.load(sys.stdin)["virtual-size"])') bytes virtual"
