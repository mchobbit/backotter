#!/usr/bin/env bash
# The write guard of vm-run and vm-mount (docs/design/image-store.md 2.9, unit
# test 11), with a fake qemu-img and qemu-system-x86_64 on PATH: nothing boots
# and nothing is attached.  A vm-store object without --ram or --snapshot is
# refused, by path (a writable one too, so root is refused); --ram and
# --snapshot boot it; an image in vms/ boots exactly as before.
#
# Usage: bash testvm/store/tests/vm-run-guard.sh    (exit 0: every case passed)
#
# vm-run and vm-mount need bash 4 or later: under an older bash (the Mac's
# 3.2) only in_store itself is tested, and the rest is reported as skipped.

set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
testvm=$(cd "$here/../.." && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/vm-run-guard.XXXXXX")
tmp=$(cd "$tmp" && pwd -P)
trap 'chmod -R u+w "$tmp" 2>/dev/null; rm -rf "$tmp"' EXIT
pass=0 fail=0 skip=0
ok() { echo "ok   $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }
skipped() { echo "skip $*"; skip=$((skip + 1)); }
check() { local what=$1; shift; if "$@"; then ok "$what"; else bad "$what"; fi; }

mkdir -p "$tmp/bin" "$tmp/store/objects" "$tmp/store/live/g/v/g" "$tmp/store/synth" "$tmp/vms/g" "$tmp/ram" "$tmp/t" "$tmp/mp"
cat >"$tmp/bin/qemu-img" <<'EOF'
#!/bin/sh
case $1 in
    info) echo '{"format": "qcow2"}' ;;
    create) for a; do last=$a; done; : >"$last" ;;
    map) echo "${FAKE_QEMU_MAP:-[]}" ;;
esac
EOF
cat >"$tmp/bin/qemu-system-x86_64" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" >"$FAKE_QEMU_ARGS"
EOF
chmod +x "$tmp/bin/qemu-img" "$tmp/bin/qemu-system-x86_64"
echo '{"schema": 1, "role": "cache"}' >"$tmp/store/store.json"
obj=$tmp/store/objects/sha256-$(printf '%064d' 0).qcow2
printf 'QFI\373object' >"$obj"
chmod 0444 "$obj"
ln -s ../../../objects "$tmp/store/live/g/v/objects"
ln -s "../objects/$(basename "$obj")" "$tmp/store/live/g/v/g/g.qcow2"
img=$tmp/vms/g/g.qcow2
printf 'QFI\373vms' >"$img"
img_sum=$(cksum <"$img")
# A scratch guest under the store root but outside objects/ (H2's synthetic guest): not an object.
synth=$tmp/store/synth/synth.qcow2
printf 'QFI\373synth' >"$synth"
obj_sum=$(cksum <"$obj")

export PATH="$tmp/bin:$PATH" FAKE_QEMU_ARGS="$tmp/qemu.args" RAM_DIR="$tmp/ram" TMPDIR="$tmp/t"
unset MEMORY SSH_PORT

# in_store as vm-run and vm-mount define it, under whichever bash runs this.
for script in vm-run vm-mount; do
    eval "$(sed -n '/^in_store() {/,/^}/p' "$testvm/$script")"
    check "$script in_store: an object" in_store "$obj"
    check "$script in_store: a live/ link to it" in_store "$tmp/store/live/g/v/g/g.qcow2"
    check "$script in_store: not a vms/ image" eval '! in_store "$img"'
    check "$script in_store: not a missing file" eval '! in_store "$tmp/nothing/x.qcow2"'
    check "$script in_store: not a file under the root outside objects/" eval '! in_store "$synth"'
    unset -f in_store
done

new_bash=$(bash -c 'echo $((BASH_VERSINFO[0] >= 4))')
if [[ $new_bash != 1 ]]; then
    for c in "vm-run cases" "vm-mount cases"; do skipped "$c: vm-run and vm-mount need bash 4 or later"; done
    echo "$pass passed, $fail failed, $skip skipped"
    exit $((fail > 0))
fi

run() { rm -f "$FAKE_QEMU_ARGS"; "$testvm/vm-run" "$@" >"$tmp/out" 2>&1 </dev/null; }
refused() { ! run "$@" && grep -q "is a vm-store object: boot it with --ram or --snapshot" "$tmp/out" && [[ ! -e $FAKE_QEMU_ARGS ]]; }
booted() { run "$@" && [[ -e $FAKE_QEMU_ARGS ]]; }
has_arg() { grep -qxF -- "$1" "$FAKE_QEMU_ARGS"; }

check "vm-run refuses an object" refused "$obj"
check "vm-run refuses it through a live/ link" refused "$tmp/store/live/g/v/g/g.qcow2"
chmod 0644 "$obj"
check "vm-run refuses a writable object (by path, as for root)" refused "$obj"
chmod 0444 "$obj"
if [[ $EUID -eq 0 ]]; then
    check "vm-run refuses an object as root" refused "$obj"
else
    skipped "vm-run as root: not root here (the writable object stands in)"
fi
check "vm-run refuses an object as a --disk" refused --disk "$obj" "$img"
check "vm-run --snapshot boots an object" booted --snapshot "$obj"
check "  with the object itself and -snapshot" eval 'has_arg "file=$obj,format=qcow2,if=virtio" && has_arg -snapshot'
check "vm-run --snapshot takes an object as a --disk" booted --snapshot --disk "$obj" "$img"
if mktemp --suffix=.x -u >/dev/null 2>&1; then
    check "vm-run --ram boots an object" booted --ram "$obj"
    check "  on an overlay in RAM_DIR" grep -q "^file=$tmp/ram/vm-run\..*\.qcow2,format=qcow2,cache=unsafe,if=virtio$" "$FAKE_QEMU_ARGS"
    export FAKE_QEMU_MAP='[{"depth": 0}]'
    check "vm-run --ram --keep of an object never offers qemu-img commit" eval \
        'booted --ram --keep "$obj" && grep -q "never written" "$tmp/out" && ! grep -q "qemu-img commit" "$tmp/out"'
    check "vm-run --ram --keep of a vms/ image offers qemu-img commit, as before" eval \
        'booted --ram --keep "$img" && grep -q "qemu-img commit" "$tmp/out"'
    unset FAKE_QEMU_MAP
else
    skipped "vm-run --ram: no GNU mktemp here"
fi
check "vm-run boots a test-root guest outside objects/ writable" booted "$synth"
check "vm-run boots a vms/ image writable, as before" booted "$img"
want=$(printf '%s\n' -enable-kvm -cpu host -smp 2 -m 2048 -drive "file=$img,format=qcow2,if=virtio" \
    -nic "user,ipv6=off,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22" -nographic -machine pc)
check "  with the same QEMU arguments" eval '[[ $(<"$FAKE_QEMU_ARGS") == "$want" ]]'
check "vm-run --disk of a vms/ image, as before" booted --disk "$img" "$img"
check "the images' bytes are unchanged" eval '[[ $(cksum <"$img") == "$img_sum" && $(cksum <"$obj") == "$obj_sum" ]]'

mount_out() { "$testvm/vm-mount" "$@" >"$tmp/out" 2>&1 </dev/null; }
check "vm-mount refuses an object without --ro" eval '! mount_out "$obj" "$tmp/mp" && grep -q "is a vm-store object: mount it with --ro" "$tmp/out"'
check "vm-mount refuses an object as a --disk" eval '! mount_out --disk "$obj" "$img" "$tmp/mp" && grep -q "vm-store object" "$tmp/out"'
if [[ $EUID -eq 0 ]]; then
    skipped "vm-mount --ro and vms/ images: root here would really attach them"
else
    check "vm-mount --ro of an object passes the guard (to its root check)" eval '! mount_out --ro "$obj" "$tmp/mp" && grep -qx "vm-mount: must run as root" "$tmp/out"'
    check "vm-mount of a vms/ image, as before" eval '! mount_out "$img" "$tmp/mp" && grep -qx "vm-mount: must run as root" "$tmp/out"'
fi

echo "$pass passed, $fail failed, $skip skipped"
exit $((fail > 0))
