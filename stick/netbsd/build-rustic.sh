#!/usr/bin/env bash
# Build BackOtter's rustic for NetBSD, natively, in a throwaway copy of the
# netbsd11 test guest: branch "backotter" of github.com/mchobbit/rustic
# (whose Cargo.lock pins rustic_core's commit), with Rust from pkgsrc's binary
# packages and the comp set (compiler and linker) from the release.
#
# Usage: build-rustic.sh [PORT]
#
# Writes $BKO_BIN/netbsd/rustic-bko and rustic-commit (BKO_BIN defaults to
# ~/backotter-work/bko-bin), where make-img-bsd.sh netbsd picks it up.  PORT
# (default 2412) is the guest's SSH port here.  The comp set comes from
# platform.conf's fetch, so it's checked against the release's signed hash
# list.  Takes about half an hour on 8 CPUs.
#
# Needs: what make-img-bsd.sh needs, and git.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
BKO_BIN=${BKO_BIN:-$WORK/bko-bin}
port=${1:-2412}
PKGSRC=https://cdn.netbsd.org/pub/pkgsrc/packages/NetBSD/x86_64/11.0_2026Q3/All
REPO=https://github.com/mchobbit/rustic
die() { echo "build-rustic: $*" >&2; exit 1; }

BUILD_VM_OPTIONS=()
# shellcheck source=platform.conf
. "$here/platform.conf"
vm=$WORK/vms/$BUILD_VM
[[ -f $vm/$BUILD_VM_IMAGE && -f $vm/id_ed25519 ]] || die "no VM in $vm"
pgrep -f "^qemu-system.*:$port-:22" >/dev/null && die "port $port is in use"
media_dir=$WORK/img-media/netbsd
mkdir -p "$media_dir"
EXTRA_SETS=comp fetch "$media_dir"

work=$(mktemp -d)
# Through sh, as make-img-bsd.sh's ssh_vm, with pkgsrc's and /sbin's tools.
ssh_g() {
    local sq="'"
    local q="PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/pkg/bin; export PATH; $1"
    q=${q//$sq/$sq\\$sq$sq}
    timeout "${T:-7200}" ssh -q -i "$vm/id_ed25519" -p "$port" -o BatchMode=yes \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 \
        root@127.0.0.1 "/bin/sh -c '$q'"
}
cleanup() {
    ssh_g '/sbin/shutdown -p now' >/dev/null 2>&1 || true
    for _ in $(seq 60); do pgrep -f "^qemu-system.*:$port-:22" >/dev/null || break; sleep 2; done
    pkill -f "^qemu-system.*:$port-:22" 2>/dev/null || true
    rm -rf "$work"
}
trap cleanup EXIT

git clone -q --depth 1 -b backotter "$REPO" "$work/src"
commit=$(git -C "$work/src" rev-parse HEAD)
echo "rustic $commit, rustic_core $(sed -n '/^name = "rustic_core"/,/^source/s/.*#\([0-9a-f]*\).*/\1/p' "$work/src/Cargo.lock")"
tar -C "$work" -czf "$work/src.tgz" --exclude .git src

echo "Booting a throwaway copy of $BUILD_VM on port $port"
SSH_PORT=$port setsid -f "$here/../../testvm/vm-run" --snapshot --memory 8192 --cpus 8 \
    --serial-log "$work/serial.log" "$vm/$BUILD_VM_IMAGE" </dev/null >"$work/vm-run.log" 2>&1
deadline=$((SECONDS + 600))
until T=15 ssh_g true 2>/dev/null; do
    [[ $SECONDS -lt $deadline ]] || die "the guest's sshd didn't come up"
    pgrep -f "^qemu-system.*:$port-:22" >/dev/null || { cat "$work/vm-run.log" >&2; die "QEMU exited"; }
    sleep 5
done

echo "Installing the comp set and Rust"
# libfetch tries IPv6 first, which QEMU's user network can't reach, and the
# guest has no TLS trust anchors until certctl sets them up.
ssh_g 'printf "ip6addrctl=YES\nip6addrctl_policy=ipv4_prefer\n" >>/etc/rc.conf &&
    /etc/rc.d/ip6addrctl start >/dev/null && certctl rehash'
ssh_g 'cd / && tar -xpJf -' <"$media_dir/comp.tar.xz"
ssh_g "PKG_PATH=$PKGSRC pkg_add rust cmake gmake perl pkgconf && rustc --version && cargo --version"

echo "Building"
ssh_g 'rm -rf /root/build && mkdir /root/build && cd /root/build && tar -xzf -' <"$work/src.tgz"
start=$SECONDS
# Default features, not "release" (which adds self-update); see the fork's
# build-backotter.sh.
ssh_g 'cd /root/build/src && env -u PROJECT_VERSION cargo build --release --locked 2>&1 |
    grep -v "^ *\(Compiling\|Downloaded\|Downloading\)"'
echo "Built in $((SECONDS - start)) s"
ssh_g '/root/build/src/target/release/rustic --version && ldd /root/build/src/target/release/rustic'
mkdir -p "$BKO_BIN/netbsd"
ssh_g 'cat /root/build/src/target/release/rustic' >"$BKO_BIN/netbsd/rustic-bko.part"
chmod 755 "$BKO_BIN/netbsd/rustic-bko.part"
mv "$BKO_BIN/netbsd/rustic-bko.part" "$BKO_BIN/netbsd/rustic-bko"
echo "$commit" >"$BKO_BIN/netbsd/rustic-commit"
ls -l "$BKO_BIN/netbsd/rustic-bko"
