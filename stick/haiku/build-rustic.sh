#!/usr/bin/env bash
# Build BackOtter's rustic for Haiku, natively, in a throwaway copy of the
# haiku6 test guest (testvm/make-haiku.sh): branch "backotter" of
# github.com/mchobbit/rustic (whose Cargo.lock pins rustic_core's commit),
# or local trees (SRC), with Rust (rust_bin) and gcc from HaikuPorts.
#
# Usage: build-rustic.sh [PORT]
#
# Smoke-tests the binary in the guest (backs up and restores a small tree
# with a fifo, a symlink and a BFS attribute), then writes
# $BKO_BIN/haiku/rustic-bko and rustic-commit (BKO_BIN defaults to
# ~/backotter-work/bko-bin).  PORT (default 2430) is the guest's SSH port
# here.  stick/build-round.sh runs it for every round, with SRC and FAST.
#
#   SRC=DIR   build DIR/rustic and DIR/rustic_core (checkouts of the forks, or
#             copies without .git) instead of the pushed branch: in the guest,
#             prep-rustic-src.sh points rustic's [patch.crates-io] at that
#             rustic_core (and its vendored walkdir) before the --locked
#             build, as the other platforms' build-rustic.sh do.  rustic-commit
#             says "local, unpushed"; RUSTIC_COMMIT and RUSTIC_CORE_COMMIT give
#             the commits when the trees have no .git (else they're "unknown").
#   RUSTIC_CORE=DIR
#             the pushed rustic with the rustic_core tree in DIR (a checkout
#             of the fork) instead of the commit Cargo.lock pins, e.g. a
#             branch not yet pushed; rustic-commit then says so, with
#             RUSTIC_CORE_COMMIT if DIR is a copy without its git metadata.
#             Not with SRC.
#   FAST=1    no LTO and 16 codegen units (CARGO_PROFILE_RELEASE_*): quicker,
#             for test rounds, and rustic-commit says so.  Without it, the
#             fork's release profile (fat LTO, one codegen unit), which is
#             what images ship.
#
# HaikuPorts has one rolling repository, so the Rust and gcc it installs are
# whatever is current, unlike the other platforms' pinned package sets.
#
# The build grows the guest's throwaway overlay by gigabytes.  It goes in
# $TMPDIR if set, else in BKO_SCRATCH/tmp (default /bkoscratch, Chopin's
# spare NVMe, where it exists), else where vm-run puts it.
#
# Needs: qemu-system-x86_64 with KVM, git, rsync, ssh, and testvm/vm-run.

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
if [[ -n ${SRC:-} ]]; then
    [[ -z ${RUSTIC_CORE:-} ]] || die "SRC and RUSTIC_CORE don't go together"
    SRC=$(cd "$SRC" && pwd)
    [[ -f $SRC/rustic/Cargo.toml && -f $SRC/rustic_core/crates/core/Cargo.toml &&
        -f $SRC/rustic_core/vendor/walkdir/Cargo.toml ]] ||
        die "SRC=$SRC has no rustic/ and rustic_core/ (with vendor/walkdir)"
elif [[ -n ${RUSTIC_CORE:-} ]]; then
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

# The trees go to the guest as ~/build/rustic (and ~/build/rustic_core).
mkdir "$work/build"
locked=--locked prep=no
if [[ -n ${SRC:-} ]]; then
    tree_commit() {  # DIR VAR: the tree's commit, and whether it has changes
        local c=${!2:-}
        # Only the tree's own .git counts: git would take a copy inside some
        # other checkout for part of it, and give that checkout's commit.
        if [[ -e $1/.git ]]; then
            [[ -n $c ]] || c=$(git -C "$1" rev-parse HEAD 2>/dev/null) || c=""
            [[ -z $(git -C "$1" status --porcelain 2>/dev/null) ]] || c="${c:-unknown}, with uncommitted changes"
        fi
        echo "${c:-unknown (no .git, no $2)}"
    }
    commit="$(tree_commit "$SRC/rustic" RUSTIC_COMMIT) (local, unpushed, from $SRC/rustic)"
    core="$(tree_commit "$SRC/rustic_core" RUSTIC_CORE_COMMIT) (local, unpushed, from $SRC/rustic_core)"
    for d in rustic rustic_core; do
        rsync -rlp --exclude .git --exclude target "$SRC/$d/" "$work/build/$d/"
    done
    cp "$here/../prep-rustic-src.sh" "$work/build/"
    prep=yes
else
    git clone -q --depth 1 -b backotter "$REPO" "$work/build/rustic"
    commit=$(git -C "$work/build/rustic" rev-parse HEAD)
    rm -rf "$work/build/rustic/.git"
    core=$(sed -n '/^name = "rustic_core"/,/^source/s/.*#\([0-9a-f]*\).*/\1/p' "$work/build/rustic/Cargo.lock")
    if [[ -n ${RUSTIC_CORE:-} ]]; then
        # A patch in .cargo/config.toml wins over Cargo.toml's; Cargo.lock then
        # changes for rustic_core, so not --locked.
        core="${RUSTIC_CORE_COMMIT:-$(git -C "$RUSTIC_CORE" rev-parse HEAD 2>/dev/null || echo unknown)} from $RUSTIC_CORE"
        [[ -z $(git -C "$RUSTIC_CORE" status --porcelain 2>/dev/null) ]] || core+=" (with uncommitted changes)"
        mkdir -p "$work/build/rustic/.cargo"
        printf '[patch.crates-io]\nrustic_core = { path = "../rustic_core/crates/core" }\n' \
            >>"$work/build/rustic/.cargo/config.toml"
        rsync -rlp --exclude .git --exclude target "$RUSTIC_CORE/" "$work/build/rustic_core/"
        locked=
    fi
fi
echo "rustic $commit"
echo "rustic_core $core"
tar -C "$work/build" -czf "$work/src.tgz" .

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
toolchain=$(ssh_g 'rustc --version && gcc --version | head -n 1') || die "no Rust or gcc in the guest"
ssh_g 'cargo --version'
echo "$toolchain"

ssh_g 'rm -rf ~/build && mkdir ~/build && cd ~/build && tar -xzmf -' <"$work/src.tgz"
if [[ $prep == yes ]]; then
    echo "Pointing rustic at the local rustic_core"
    ssh_g 'cd ~/build && sh ./prep-rustic-src.sh ~/build' || die "prep-rustic-src.sh failed"
fi

fast="" fast_note=""
if [[ ${FAST:-} == 1 ]]; then
    fast="CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16"
    fast_note=" (FAST: no LTO, 16 codegen units)"
fi
echo "Building$fast_note"
start=$SECONDS
# Default features, not "release" (which adds self-update); see the fork's
# build-backotter.sh.  zstd from the system: iana-time-zone links Haiku's
# libbe, which uses libzstd.so, and the zstd crate's own copy has hidden
# symbols, which ld refuses to an executable whose libraries need them.
ssh_g "cd ~/build/rustic && env -u PROJECT_VERSION ZSTD_SYS_USE_PKG_CONFIG=1 $fast cargo build --release $locked 2>&1 |
    grep -v '^ *\(Compiling\|Downloaded\|Downloading\|Locking\|Adding\|Updating\)'; exit \${PIPESTATUS[0]}" ||
    die "the build failed"
echo "Built in $((SECONDS - start)) s"
bin='~/build/rustic/target/release/rustic'   # the guest's ~
version=$(ssh_g "$bin --version") || die "the binary doesn't run in the guest"
echo "$version"
[[ $version == *backotter* ]] || die "the binary isn't BackOtter's rustic: $version"
ssh_g "objdump -p $bin | grep NEEDED"
# Back up and restore a small tree with a fifo, a symlink and a BFS
# attribute, as the other platforms' builds do (less the attribute).  Not
# under /tmp, a symlink to /boot/system/cache/tmp: the snapshot takes the
# real path, and restoring latest:/tmp/... finds no such node.
smoke=$(ssh_g "export RUSTIC_PASSWORD=x; R=$bin S=/boot/home/bko-smoke
rm -rf \$S && mkdir -p \$S/src/d || exit 1
echo hello >\$S/src/a && ln -s a \$S/src/l && mkfifo \$S/src/f && echo x >\$S/src/d/b && chmod 640 \$S/src/a &&
    addattr -t string BKO:smoke hello \$S/src/a || exit 1
\$R -r \$S/repo init >/dev/null 2>&1 &&
    \$R -r \$S/repo backup --set-atime yes \$S/src >\$S/backup.log 2>&1 &&
    \$R -r \$S/repo restore --numeric-id latest:\$S/src \$S/dst >\$S/restore.log 2>&1 || { cat \$S/*.log; exit 1; }
diff -r \$S/src/d \$S/dst/d && test -p \$S/dst/f && test -L \$S/dst/l &&
    test \"\$(catattr -d BKO:smoke \$S/dst/a)\" = hello && echo SMOKE-OK") || true
echo "$smoke" | tail -n 20
[[ $smoke == *SMOKE-OK* ]] || die "the smoke test failed in haiku6"
mkdir -p "$BKO_BIN/haiku"
ssh_g "cat $bin" >"$BKO_BIN/haiku/rustic-bko.part"
[[ -s $BKO_BIN/haiku/rustic-bko.part ]] || die "couldn't copy the binary out of the guest"
chmod 755 "$BKO_BIN/haiku/rustic-bko.part"
mv "$BKO_BIN/haiku/rustic-bko.part" "$BKO_BIN/haiku/rustic-bko"
printf '%s\nrustic_core %s\n%s, built natively in haiku6%s\n' \
    "$commit" "$core" "$(echo "$toolchain" | paste -sd ' ' - | sed 's/ gcc / and gcc /')" "$fast_note" \
    >"$BKO_BIN/haiku/rustic-commit"
ls -l "$BKO_BIN/haiku/rustic-bko"
