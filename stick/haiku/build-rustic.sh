#!/usr/bin/env bash
# Build BackOtter's rustic for Haiku, natively, in a throwaway copy of the
# haiku6 test guest (testvm/make-haiku.sh): branch "backotter" of
# github.com/mchobbit/rustic (whose Cargo.lock pins rustic_core's commit),
# with Rust (rust_bin) and gcc from HaikuPorts.
#
# Usage: build-rustic.sh [PORT]
#
# Writes $BKO_BIN/haiku/rustic-bko and rustic-commit (BKO_BIN defaults to
# ~/backotter-work/bko-bin).  PORT (default 2430) is the guest's SSH port
# here.  RUSTIC_CORE=DIR builds with the rustic_core tree in DIR (a checkout
# of the fork) instead of the commit Cargo.lock pins, e.g. a branch not yet
# pushed; rustic-commit then says so, with RUSTIC_CORE_COMMIT if DIR is a
# copy without its git metadata.
#
# HaikuPorts has one rolling repository, so the Rust and gcc it installs are
# whatever is current, unlike the other platforms' pinned package sets.
#
# The build grows the guest's throwaway overlay by gigabytes.  It goes in
# $TMPDIR if set, else in BKO_SCRATCH/tmp (default /bkoscratch, Chopin's
# spare NVMe, where it exists), else where vm-run puts it.
#
# Needs: qemu-system-x86_64 with KVM, git, ssh, and testvm/vm-run.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
BKO_BIN=${BKO_BIN:-$WORK/bko-bin}
port=${1:-2430}
REPO=https://github.com/mchobbit/rustic
# haiku_devel: crti.o and the like.  zstd_devel: see the build below.
PACKAGES="rust_bin gcc binutils haiku_devel zstd_devel cmake make perl pkgconfig"
die() { echo "build-rustic: $*" >&2; exit 1; }

vm=$WORK/vms/haiku6
[[ -f $vm/haiku6.qcow2 && -f $vm/id_ed25519 && -f $vm/vm-run-options ]] || die "no VM in $vm"
pgrep -f "^qemu-system.*:$port-:22" >/dev/null && die "port $port is in use"
if [[ -n ${RUSTIC_CORE:-} ]]; then
    [[ -f $RUSTIC_CORE/crates/core/Cargo.toml ]] || die "RUSTIC_CORE=$RUSTIC_CORE isn't a rustic_core tree"
fi

scratch=${BKO_SCRATCH:-/bkoscratch}
if [[ -z ${TMPDIR:-} && -d $scratch && -w $scratch ]]; then
    mkdir -p "$scratch/tmp" && export TMPDIR=$scratch/tmp
fi
work=$(mktemp -d)
# Haiku's only user is "user" (uid 0); its shell is bash.
ssh_g() {
    timeout "${T:-7200}" ssh -q -i "$vm/id_ed25519" -p "$port" -o BatchMode=yes \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 \
        user@127.0.0.1 "$@"
}
cleanup() {
    # A clean shutdown, though the disk is a throwaway: QEMU killed early
    # would be the same.
    T=30 ssh_g 'shutdown -q' >/dev/null 2>&1 || true
    for _ in $(seq 60); do pgrep -f "^qemu-system.*:$port-:22" >/dev/null || break; sleep 2; done
    pkill -f "^qemu-system.*:$port-:22" 2>/dev/null || true
    rm -rf "$work"
}
trap cleanup EXIT

git clone -q --depth 1 -b backotter "$REPO" "$work/src"
commit=$(git -C "$work/src" rev-parse HEAD)
core=$(sed -n '/^name = "rustic_core"/,/^source/s/.*#\([0-9a-f]*\).*/\1/p' "$work/src/Cargo.lock")
locked=--locked
if [[ -n ${RUSTIC_CORE:-} ]]; then
    # A patch in .cargo/config.toml wins over Cargo.toml's; Cargo.lock then
    # changes for rustic_core, so not --locked.
    core="${RUSTIC_CORE_COMMIT:-$(git -C "$RUSTIC_CORE" rev-parse HEAD 2>/dev/null || echo unknown)} from $RUSTIC_CORE"
    [[ -z $(git -C "$RUSTIC_CORE" status --porcelain 2>/dev/null) ]] || core+=" (with uncommitted changes)"
    mkdir -p "$work/src/.cargo"
    printf '[patch.crates-io]\nrustic_core = { path = "../rustic_core/crates/core" }\n' \
        >>"$work/src/.cargo/config.toml"
    tar -C "$RUSTIC_CORE/.." -cf - --exclude .git --exclude target \
        --transform "s|^$(basename "$RUSTIC_CORE")|rustic_core|" "$(basename "$RUSTIC_CORE")" |
        tar -C "$work" -xmf -
    locked=
fi
echo "rustic $commit, rustic_core $core"
tar -C "$work" -czf "$work/src.tgz" --exclude .git src $([[ -d $work/rustic_core ]] && echo rustic_core)

echo "Booting a throwaway copy of haiku6 on port $port"
# shellcheck disable=SC2046
SSH_PORT=$port setsid -f "$here/../../testvm/vm-run" $(<"$vm/vm-run-options") --snapshot \
    --memory 8192 --cpus 8 --serial-log "$work/serial.log" "$vm/haiku6.qcow2" \
    </dev/null >"$work/vm-run.log" 2>&1
for _ in $(seq 30); do pgrep -f "^qemu-system.*:$port-:22" >/dev/null && break; sleep 1; done
# Until the guest answers on SSH and $1 succeeds there.
wait_guest() {
    local deadline=$((SECONDS + 600))
    until T=15 ssh_g "$1" 2>/dev/null; do
        [[ $SECONDS -lt $deadline ]] || die "the guest's sshd didn't come up"
        pgrep -f "^qemu-system.*:$port-:22" >/dev/null || { cat "$work/vm-run.log" >&2; die "QEMU exited"; }
        sleep 5
    done
}
wait_guest true

echo "Installing $PACKAGES"
ssh_g "pkgman install -y $PACKAGES >/dev/null"
# haiku_devel must match the haiku package, so pkgman upgrades that too, and
# then all of it only takes effect at the next boot.  The guest reboots in
# the same QEMU, so on the same throwaway overlay; /tmp is emptied at boot.
echo "Rebooting into the upgraded system"
ssh_g 'touch /tmp/bko-before-reboot && shutdown -r -q' >/dev/null 2>&1 || true
wait_guest '[ ! -e /tmp/bko-before-reboot ]'
ssh_g 'rustc --version && cargo --version && gcc --version | head -1'

echo "Building"
ssh_g 'rm -rf ~/build && mkdir ~/build && cd ~/build && tar -xzmf -' <"$work/src.tgz"
start=$SECONDS
# Default features, not "release" (which adds self-update); see the fork's
# build-backotter.sh.  zstd from the system: iana-time-zone links Haiku's
# libbe, which uses libzstd.so, and the zstd crate's own copy has hidden
# symbols, which ld refuses to an executable whose libraries need them.
ssh_g "cd ~/build/src && ZSTD_SYS_USE_PKG_CONFIG=1 env -u PROJECT_VERSION cargo build --release $locked 2>&1 |
    grep -v '^ *\(Compiling\|Downloaded\|Downloading\|Locking\|Adding\|Updating\)'; exit \${PIPESTATUS[0]}"
echo "Built in $((SECONDS - start)) s"
ssh_g '~/build/src/target/release/rustic --version && objdump -p ~/build/src/target/release/rustic | grep NEEDED'
mkdir -p "$BKO_BIN/haiku"
ssh_g 'cat ~/build/src/target/release/rustic' >"$BKO_BIN/haiku/rustic-bko.part"
chmod 755 "$BKO_BIN/haiku/rustic-bko.part"
mv "$BKO_BIN/haiku/rustic-bko.part" "$BKO_BIN/haiku/rustic-bko"
printf '%s\nrustic_core %s\n' "$commit" "$core" >"$BKO_BIN/haiku/rustic-commit"
ls -l "$BKO_BIN/haiku/rustic-bko"
