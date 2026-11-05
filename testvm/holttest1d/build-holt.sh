#!/usr/bin/env bash
# Build bko-holt natively for a system other than Linux, in a throwaway copy
# of a test guest of that system: holt/ has no dependencies, so cargo needs
# nothing but the guest's Rust (1.85 or later).  Run as m on a test host:
#
#   build-holt.sh PLATFORM [PORT]
#
# PLATFORM: freebsd (built on freebsd14, so that it runs on 14 and 15, and on
# HardenedBSD and GhostBSD), netbsd, openbsd, dragonfly, illumos or haiku.
# The holt/ tree comes from ~/backotter-work/holt-src (rsynced there by the
# caller).  The binary lands in /bkoscratch/holttest1d/bin/PLATFORM/bko-holt;
# the build's log in /bkoscratch/holttest1d/build-holt-PLATFORM/.
#
# A stick/build-rustic.sh build image (~/backotter-work/vms-bko-build/
# VM-rust.qcow2) is used when there is one: it has Rust already.  Otherwise
# the guest gets Rust from its packages first (network needed).
set -euo pipefail

PLATFORM=$1 PORT=${2:-2950}
WORK=/home/m/backotter-work
SRC=$WORK/holt-src
OUT=/bkoscratch/holttest1d/bin/$PLATFORM
LOGS=/bkoscratch/holttest1d/build-holt-$PLATFORM
GUEST_PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/opt/ooce/bin:/usr/gnu/bin
case $PLATFORM in
    freebsd) VM=freebsd14 RUST='env ASSUME_ALWAYS_YES=yes pkg install -y rust' ;;
    netbsd) VM=netbsd11 RUST='pkg_info -e rust >/dev/null || pkgin -y install rust' ;;
    openbsd) VM=openbsd8 RUST='pkg_add -I rust' ;;
    dragonfly) VM=dragonfly6 RUST='env ASSUME_ALWAYS_YES=yes pkg install -y rust' ;;
    illumos) VM=omnios58 RUST='pkg install -q ooce/developer/rust developer/gcc14; s=$?; [ $s -eq 0 ] || [ $s -eq 4 ]' ;;
    haiku) VM=haiku6 RUST='pkgman install -y rust_bin gcc binutils haiku_devel' GUEST_PATH=/bin:/boot/system/bin:/boot/system/non-packaged/bin
        export VM_USER=user ;;   # Haiku's uid 0
    *) echo "build-holt: PLATFORM: freebsd, netbsd, openbsd, dragonfly, illumos or haiku" >&2; exit 2 ;;
esac
[[ -f $SRC/Cargo.toml ]] || { echo "build-holt: no $SRC/Cargo.toml" >&2; exit 1; }
VM_LIB_DIR=$WORK/testvm
# shellcheck source=../vm-lib.sh
. "$WORK/testvm/vm-lib.sh"
mkdir -p "$OUT" "$LOGS"
trap vm_halt EXIT
if [[ -f $WORK/vms-bko-build/$VM-rust.qcow2 ]]; then
    VM_IMAGE=$WORK/vms-bko-build/$VM-rust.qcow2 VM_IMAGE_KEY=$WORK/vms-bko-build/$VM.id_ed25519 \
        vm_boot "$VM" "$PORT" "$LOGS" --snapshot --cpus 4 --memory 4096
else
    vm_boot "$VM" "$PORT" "$LOGS" --snapshot --cpus 4 --memory 4096
fi
env="PATH=$GUEST_PATH; export PATH"
if ! vm_ssh "$env; command -v cargo && command -v cc" >/dev/null 2>&1; then
    echo "Installing Rust"
    vm_ssh "$env; $RUST" >"$LOGS/rust-install.log" 2>&1 || { tail -20 "$LOGS/rust-install.log" >&2; exit 1; }
fi
vm_ssh "$env; rustc --version"
tar -C "$SRC" -czf - Cargo.toml Cargo.lock src tests | T=600 vm_ssh 'rm -rf /tmp/holt && mkdir /tmp/holt && cd /tmp/holt && gzip -dc | tar -xf -'
if ! T=3600 vm_ssh "$env; cd /tmp/holt && cargo build --release --offline 2>&1" >"$LOGS/build.log"; then
    grep -E "^(error|warning)" -A 6 "$LOGS/build.log" | head -80 >&2
    exit 1
fi
vm_ssh "/tmp/holt/target/release/bko-holt --version"
vm_ssh 'cat /tmp/holt/target/release/bko-holt' >"$OUT/bko-holt.part"
chmod 755 "$OUT/bko-holt.part"
mv "$OUT/bko-holt.part" "$OUT/bko-holt"
ls -l "$OUT/bko-holt"
