#!/usr/bin/env bash
# Build bcachefs-tools for the HoltTest1 rescuer, which is Debian 13: Debian
# dropped the package, though its 6.12 kernel still has bcachefs.  Run as
# root on a test host:
#
#   make-bcachefs-tools.sh [VERSION] [PORT]
#
# VERSION (default 1.13.0) is the release whose on-disk format the 6.12
# kernel reads and writes: newer tools format a newer version, which that
# kernel can't mount.  The vendored release tarball is built in a throwaway
# overlay of the rescuer, and the result lands in /bkoscratch/holttest1d/bin/:
# bcachefs (the binary) and the .debs of the libraries it needs that the
# rescuer lacks.  ht1.sh installs them in the rescuer for a source with
# bcachefs.
set -euo pipefail

VER=${1:-1.13.0}
PORT=${2:-2978}
W=/home/m/backotter-work
D=/bkoscratch/holttest1d
BIN=$D/bin
B=$D/bcachefs-build
KEY=$W/vms/debian13/id_ed25519
mkdir -p "$B"
export SSH_PORT=$PORT TMPDIR=/bkoscratch/tmp
ssh_r() { timeout "${TMO:-3600}" ssh -q -i "$KEY" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@127.0.0.1 "$@"; }
down() {
    TMO=20 ssh_r poweroff >/dev/null 2>&1 || true
    for _ in $(seq 60); do pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null || return 0; sleep 2; done
    pkill -f "^qemu-system.*:$PORT-:22"; sleep 3
}
trap down EXIT

rm -f "$B/vm.qcow2"
qemu-img create -q -f qcow2 -F qcow2 -b "$D/rescuer/rescuer.qcow2" "$B/vm.qcow2"
setsid -f "$W/testvm/vm-run" --memory 4096 --cpus 4 --serial-log "$B/serial.log" "$B/vm.qcow2" </dev/null >"$B/vm-run.log" 2>&1
for _ in $(seq 120); do TMO=8 ssh_r true 2>/dev/null && break; sleep 5; done
ssh_r "set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q build-essential pkg-config cargo rustc libclang-dev llvm-dev zstd wget \
    libaio-dev libblkid-dev libkeyutils-dev liblz4-dev libsodium-dev liburcu-dev libzstd-dev \
    uuid-dev zlib1g-dev libudev-dev systemd-dev
cd /root
wget -q https://evilpiepirate.org/bcachefs-tools/bcachefs-tools-vendored-$VER.tar.zst
tar --zstd -xf bcachefs-tools-vendored-$VER.tar.zst
cd bcachefs-tools-$VER
# liburcu 0.15 (Debian 13) made uatomic_set a statement: 1.13 returns it.
sed -i -E 's/return (__ATOMIC_SET(_RELEASE)?\()/\1/' include/linux/atomic.h
make -j4 bcachefs
target/release/bcachefs version
mkdir -p /root/out
cp target/release/bcachefs /root/out/
cd /root/out
# The runtime libraries' packages, for a rescuer without them.
for l in \$(ldd bcachefs | awk '/=> \\// { print \$3 }'); do dpkg -S \"\$(realpath \$l)\" 2>/dev/null || dpkg -S \"\$l\"; done | cut -d: -f1 | sort -u > packages
cat packages
apt-get download \$(grep -vxE 'libc6|libgcc-s1' packages)
" > "$B/build.log" 2>&1 || { tail -30 "$B/build.log" >&2; exit 1; }
rm -rf "$B/out"
scp -q -i "$KEY" -P "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -r root@127.0.0.1:/root/out "$B/out"
rm -f "$BIN"/bcachefs-deps-*.deb
install -m 755 "$B/out/bcachefs" "$BIN/bcachefs"
for f in "$B"/out/*.deb; do cp "$f" "$BIN/bcachefs-deps-$(basename "$f")"; done
chown -R m:m "$BIN"
ls -l "$BIN"/bcachefs*
