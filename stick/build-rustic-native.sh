# The native build of BackOtter's rustic in a throwaway copy of a test guest,
# shared by stick/{freebsd,openbsd,netbsd,omnios}/build-rustic.sh, which
# source it (bash) after setting:
#
#   PLATFORM      its directory in bko-bin: freebsd, openbsd, netbsd, omnios
#   VM            the test guest (testvm/make-*.sh) in ~/backotter-work/vms
#   DEFAULT_PORT  the guest's SSH port here when none is given
#   GUEST_PATH    PATH in the guest, with its Rust and C compiler in it
#   install_tools a function that installs the C compiler, Rust, cmake, gmake
#                 and so on into the booted guest (with vm_ssh, vm_sh) from
#                 its package system
#
# Usage: build-rustic.sh [--setup] [PORT]
#
# Builds branch "backotter" of github.com/mchobbit/rustic (whose Cargo.lock
# pins rustic_core's commit), or local trees (SRC), natively in a throwaway
# copy of VM with its SSH on PORT, smoke-tests the binary there (backs up and
# restores a small tree with a fifo and a symlink), and writes
# $BKO_BIN/PLATFORM/rustic-bko and rustic-commit (rustic's and rustic_core's
# commits, rustc, the guest).  BKO_BIN defaults to ~/backotter-work/bko-bin,
# where make-img-bsd.sh picks it up; a test round gives it a BKO_BIN of its
# own.
#
#   SRC=DIR   build DIR/rustic and DIR/rustic_core (checkouts of the forks, or
#             copies without .git) instead of the pushed branch: in the guest,
#             prep-rustic-src.sh points rustic's [patch.crates-io] at that
#             rustic_core (and its vendored walkdir) and refreshes Cargo.lock
#             for the two before the --locked build, as
#             dragonfly/build-rustic.sh does.  rustic-commit says "local,
#             unpushed"; RUSTIC_COMMIT and RUSTIC_CORE_COMMIT give the commits
#             when the trees have no .git (else they're "unknown").
#   FAST=1    no LTO and 16 codegen units (CARGO_PROFILE_RELEASE_*): several
#             times quicker, for test rounds, and rustic-commit says so.
#             Without it, the fork's release profile (fat LTO, one codegen
#             unit), which is what images ship.
#   CPUS, MEMORY
#             the guest's CPUs and MB of RAM (default 6 and 8192)
#
#   --setup   make (or bring up to date) VM's build image instead of building:
#             ~/backotter-work/vms-bko-build/VM-rust.qcow2, an overlay on a
#             private copy of the guest's image (VM-base.qcow2: updating the
#             guest can't break it), with install_tools' tools in it and
#             cargo's registry filled for the sources.  Builds then boot a
#             throwaway copy of it instead of the guest, and skip the
#             installs.  To go back, e.g. after the guest was updated, delete
#             both files.
#
# Logs: ~/backotter-work/logs/build-rustic-PLATFORM-PORT/ (build.log,
# serial.log, vm-run.log), kept.  Sources are staged in
# ~/backotter-work/tmp, not /tmp: cargo looks for a workspace in the parent
# directories, and takes any stray Cargo.toml it finds there.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, testvm/vm-run, the guest in
# ~/backotter-work/vms (BKO_WORK overrides the directory), git, rsync, ssh.
# shellcheck shell=bash

set -euo pipefail

WORK=${BKO_WORK:-$HOME/backotter-work}
BKO_BIN=${BKO_BIN:-$WORK/bko-bin}
REPO=https://github.com/mchobbit/rustic
stick=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
die() { echo "build-rustic: $*" >&2; exit 1; }
# shellcheck source=../testvm/vm-lib.sh
. "$stick/../testvm/vm-lib.sh"
start_all=$SECONDS

setup=no
if [[ ${1:-} == --setup ]]; then setup=yes; shift; fi
[[ $# -le 1 ]] || die "usage: build-rustic.sh [--setup] [PORT]"
port=${1:-$DEFAULT_PORT}
[[ $port =~ ^[0-9]+$ ]] || die "usage: build-rustic.sh [--setup] [PORT]"
guest_dir=$WORK/vms/$VM
guest_image=$guest_dir/$VM.qcow2
[[ -f $guest_image ]] || guest_image=$(ls "$guest_dir"/*.qcow2 2>/dev/null | head -n 1 || true)
[[ -f $guest_image && -f $guest_dir/id_ed25519 ]] || die "no guest $VM in $guest_dir"
if [[ -n ${SRC:-} ]]; then
    SRC=$(cd "$SRC" && pwd)
    [[ -f $SRC/rustic/Cargo.toml && -f $SRC/rustic_core/crates/core/Cargo.toml &&
        -f $SRC/rustic_core/vendor/walkdir/Cargo.toml ]] ||
        die "SRC=$SRC has no rustic/ and rustic_core/ (with vendor/walkdir)"
fi
build_dir=$WORK/vms-bko-build
base=$build_dir/$VM-base.qcow2
image=$build_dir/$VM-rust.qcow2
logs=$WORK/logs/build-rustic-$PLATFORM-$port
mkdir -p "$logs" "$WORK/tmp"
work=$(mktemp -d "$WORK/tmp/build-rustic-$PLATFORM.XXXXXX")
trap 'vm_halt; rm -rf "$work"' EXIT

# --- The guest ------------------------------------------------------------------------
opts=(--cpus "${CPUS:-6}" --memory "${MEMORY:-8192}")
if [[ $setup == yes ]]; then
    mkdir -p "$build_dir"
    if [[ ! -f $base ]]; then
        echo "Copying $guest_image to $base"
        cp --sparse=always "$guest_image" "$base.part"
        mv "$base.part" "$base"
    fi
    [[ -f $image ]] || qemu-img create -q -f qcow2 -b "$base" -F qcow2 "$image"
    echo "Booting $image on port $port (kept, not a throwaway copy)"
    VM_IMAGE=$image vm_boot "$VM" "$port" "$logs" "${opts[@]}" || die "$VM didn't come up"
elif [[ -f $image ]]; then
    echo "Booting a throwaway copy of $image on port $port"
    VM_IMAGE=$image vm_boot "$VM" "$port" "$logs" --snapshot "${opts[@]}" || die "$VM didn't come up"
else
    echo "Booting a throwaway copy of $VM on port $port"
    vm_boot "$VM" "$port" "$logs" --snapshot "${opts[@]}" || die "$VM didn't come up"
fi
echo "Up: $(vm_ssh 'uname -sr')"

# --- The sources ------------------------------------------------------------------
mkdir "$work/src"
if [[ -z ${SRC:-} ]]; then
    git clone -q --depth 1 -b backotter "$REPO" "$work/src/rustic"
    commit=$(git -C "$work/src/rustic" rev-parse HEAD)
    core=$(sed -n '/^name = "rustic_core"/,/^source/s/.*#\([0-9a-f]*\).*/\1/p' "$work/src/rustic/Cargo.lock")
    rm -rf "$work/src/rustic/.git"
else
    tree_commit() {  # DIR VAR: the tree's commit, and whether it has changes
        local c=${!2:-}
        # A copy without .git (the README's rsync makes those) still builds.
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
        rsync -rlp --exclude .git --exclude target "$SRC/$d/" "$work/src/$d/"
    done
    cp "$stick/prep-rustic-src.sh" "$work/src/"
fi
echo "rustic $commit"
echo "rustic_core $core"
tar -C "$work/src" -czf "$work/src.tgz" .

# --- Tools ---------------------------------------------------------------------------
if [[ $setup == yes || ! -f $image ]]; then
    echo "Installing the tools"
    install_tools
fi
guest_env="PATH=$GUEST_PATH; export PATH; unset PROJECT_VERSION"
in_src="$guest_env; cd /root/bko-build"
rustc_version=$(vm_ssh "$guest_env; rustc --version") || die "no Rust in $VM"
echo "$rustc_version"
T=600 vm_ssh 'rm -rf /root/bko-build && mkdir /root/bko-build && cd /root/bko-build && gzip -dc | tar -xf -' \
    <"$work/src.tgz"
if [[ -n ${SRC:-} ]]; then
    echo "Pointing rustic at the local rustic_core"
    vm_ssh "$in_src && sh ./prep-rustic-src.sh /root/bko-build" || die "prep-rustic-src.sh failed"
fi

if [[ $setup == yes ]]; then
    echo "Filling cargo's registry"
    vm_ssh "$in_src/rustic && cargo fetch --locked && rm -rf /root/bko-build" || die "cargo fetch failed"
    vm_halt
    echo "$image is ready ($(du -h "$image" | cut -f1)); done in $((SECONDS - start_all)) s"
    exit 0
fi

# --- Build -----------------------------------------------------------------------------
fast="" fast_note=""
if [[ ${FAST:-} == 1 ]]; then
    fast="CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16; "
    fast+="export CARGO_PROFILE_RELEASE_LTO CARGO_PROFILE_RELEASE_CODEGEN_UNITS; "
    fast_note=" (FAST: no LTO, 16 codegen units)"
fi
echo "Building$fast_note"
start=$SECONDS
rc=0
# Default features, not "release" (which adds self-update); see the fork's
# build-backotter.sh.  The release profile is the fork's own.
T=14400 vm_ssh "$in_src/rustic && ${fast}nice cargo build --release --locked 2>&1" |
    tee "$logs/build.log" | { grep -v '^ *\(Compiling\|Downloaded\|Downloading\)' || true; } || rc=$?
[[ $rc == 0 ]] || die "the build failed (log: $logs/build.log)"
echo "Built in $((SECONDS - start)) s"

# --- Check it in the guest, and keep it ---------------------------------------------------
bin=/root/bko-build/rustic/target/release/rustic
smoke=$(vm_sh "export RUSTIC_PASSWORD=x
R=$bin S=/tmp/bko-smoke
rm -rf \$S && mkdir -p \$S/src/d || exit 1
echo hello >\$S/src/a && ln -s a \$S/src/l && mkfifo \$S/src/f && echo x >\$S/src/d/b &&
    chmod 640 \$S/src/a || exit 1
\$R --version && ldd \$R || exit 1
\$R -r \$S/repo init >/dev/null 2>&1 &&
    \$R -r \$S/repo backup \$S/src >\$S/backup.log 2>&1 &&
    \$R -r \$S/repo restore latest:\$S/src \$S/dst >\$S/restore.log 2>&1 || { cat \$S/*.log; exit 1; }
diff -r \$S/src/d \$S/dst/d && test -p \$S/dst/f && test -L \$S/dst/l && echo SMOKE-OK") || true
echo "$smoke"
[[ $smoke == *SMOKE-OK* ]] || die "the smoke test failed in $VM"
mkdir -p "$BKO_BIN/$PLATFORM"
vm_ssh "cat $bin" >"$BKO_BIN/$PLATFORM/rustic-bko.part"
[[ -s $BKO_BIN/$PLATFORM/rustic-bko.part ]] || die "couldn't copy the binary out of $VM"
chmod 755 "$BKO_BIN/$PLATFORM/rustic-bko.part"
mv "$BKO_BIN/$PLATFORM/rustic-bko.part" "$BKO_BIN/$PLATFORM/rustic-bko"
printf '%s\nrustic_core %s\n%s, built natively in %s%s\n' \
    "$commit" "$core" "$rustc_version" "$VM" "$fast_note" >"$BKO_BIN/$PLATFORM/rustic-commit"
ls -l "$BKO_BIN/$PLATFORM/rustic-bko"
echo "Done in $((SECONDS - start_all)) s"
