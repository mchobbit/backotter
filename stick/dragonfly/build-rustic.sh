#!/usr/bin/env bash
# Build BackOtter's rustic for DragonFly BSD by cross-compiling it here:
# branch "backotter" of github.com/mchobbit/rustic (whose Cargo.lock pins
# rustic_core's commit), or local rustic and rustic_core trees (SRC).
#
# Why cross-compile: DragonFly's own Rust (dports, 6.4 and the development
# repository alike) is 1.85.1, and rustic needs 1.91, so the native build the
# other BSDs get isn't possible.  x86_64-unknown-dragonfly is a tier-3 Rust
# target, with no std from rustup, so this builds with a pinned nightly and
# -Zbuild-std; the C code in the dependencies (ring, zstd-sys, aws-lc-sys,
# mimalloc, ...) and the link go through clang and lld, against a sysroot
# (/lib, /usr/lib with the crt*.o, /usr/include) copied from a throwaway copy
# of the dragonfly6 test guest (testvm/make-dragonfly.sh).
#
# Usage: build-rustic.sh [PORT]
#
# Writes $BKO_BIN/dragonfly/rustic-bko and rustic-commit (BKO_BIN defaults to
# ~/backotter-work/bko-bin), where make-img-bsd.sh dragonfly picks it up.  PORT
# (default 2421) is the SSH port here of the throwaway dragonfly6, which
# gives the sysroot (on the first run) and runs the new binary's --version.
#
#   SRC=DIR   build DIR/rustic and DIR/rustic_core (checkouts of the forks, or
#             copies without .git) instead of the pushed branch:
#             ../prep-rustic-src.sh points rustic's [patch.crates-io] at that
#             rustic_core, and at walkdir (vendored in rustic_core), and
#             refreshes Cargo.lock for those two before the --locked build,
#             as the native builds do.  rustic-commit says "local,
#             unpushed"; RUSTIC_COMMIT and RUSTIC_CORE_COMMIT give the commits
#             when the trees have no .git (else they're "unknown").
#   RUST_TOOLCHAIN
#             the nightly to build with (default below, the one tested).
#
# Everything it sets up is cached in $BKO_WORK/dragonfly-cross (default
# ~/backotter-work/dragonfly-cross), so later runs only rebuild what changed:
# the toolchain's clang and lld if the host has none, the sysroot, the
# sources, and cargo's target directory (std included).  rm -rf it to start
# over, e.g. after the dragonfly6 guest is updated (the sysroot is refreshed
# by itself when the guest's image changes, but cargo's target directory
# keeps C code built against the old one).  A first run takes about 6
# minutes on 12 CPUs; one with nothing to rebuild, under a minute.
#
# Needs: rustup (https://rustup.rs) for this user: the nightly and its rust-src
# are installed with it, in ~/.rustup, if missing; a C compiler for the host
# (build scripts); clang and ld.lld, the host's if it has them (any version
# from /usr/lib/llvm-N or PATH), else Debian's or Ubuntu's clang-N/lld-N
# packages, downloaded with apt-get download and unpacked into the cache, not
# installed (N is a libllvmN the host has installed); qemu-system-x86_64 with
# KVM, testvm/vm-run, the dragonfly6 guest in ~/backotter-work/vms, git,
# rsync, ssh.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
BKO_BIN=${BKO_BIN:-$WORK/bko-bin}
CACHE=$WORK/dragonfly-cross
port=${1:-2421}
REPO=https://github.com/mchobbit/rustic
# rustc 1.101.0-nightly (92b5b8c87 2026-10-29)
TOOLCHAIN=${RUST_TOOLCHAIN:-nightly-2026-10-31}
TARGET=x86_64-unknown-dragonfly
VM=dragonfly6
die() { echo "build-rustic: $*" >&2; exit 1; }
start_all=$SECONDS

vm=$WORK/vms/$VM
[[ -f $vm/$VM.qcow2 && -f $vm/id_ed25519 ]] || die "no VM in $vm"
qemu_running() { pgrep -f "^qemu-system.*:$port-:22" >/dev/null; }
qemu_running && die "port $port is in use"
[[ -z $(ss -Hltn "sport = :$port" 2>/dev/null) ]] || die "port $port is in use"
if [[ -n ${SRC:-} ]]; then
    SRC=$(cd "$SRC" && pwd)
    [[ -f $SRC/rustic/Cargo.toml && -f $SRC/rustic_core/crates/core/Cargo.toml &&
        -f $SRC/rustic_core/vendor/walkdir/Cargo.toml ]] ||
        die "SRC=$SRC has no rustic/ and rustic_core/ (with vendor/walkdir)"
fi
mkdir -p "$CACHE"
# Not in /tmp: cargo looks for rustic's workspace in its parent directories,
# and takes any Cargo.toml it finds there.
work=$(mktemp -d "$CACHE/work.XXXXXX")

# --- The throwaway guest, booted first so that it comes up during the rest --------
# root's shell there is csh: everything through sh.
ssh_g() {
    local sq="'"
    local q=${1//$sq/$sq\\$sq$sq}
    timeout "${T:-600}" ssh -q -i "$vm/id_ed25519" -p "$port" -o BatchMode=yes \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 \
        root@127.0.0.1 "/bin/sh -c '$q'"
}
qemu_pid=""
cleanup() {
    if [[ -n $qemu_pid ]]; then
        T=30 ssh_g '/sbin/shutdown -p now' >/dev/null 2>&1 || true
        for _ in $(seq 60); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
        kill "$qemu_pid" 2>/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT
echo "Booting a throwaway copy of $VM on port $port"
SSH_PORT=$port setsid -f "$here/../../testvm/vm-run" --snapshot --memory 2048 --cpus 2 \
    --serial-log "$work/serial.log" "$vm/$VM.qcow2" </dev/null >"$work/vm-run.log" 2>&1
for _ in $(seq 30); do qemu_pid=$(pgrep -f "^qemu-system.*:$port-:22" | head -n 1) && break; sleep 1; done
[[ -n $qemu_pid ]] || { cat "$work/vm-run.log" >&2; die "QEMU didn't start"; }
wait_guest() {
    local deadline=$((SECONDS + 600))
    until T=15 ssh_g true 2>/dev/null; do
        [[ $SECONDS -lt $deadline ]] || die "the guest's sshd didn't come up"
        kill -0 "$qemu_pid" 2>/dev/null || { cat "$work/vm-run.log" >&2; die "QEMU exited"; }
        sleep 5
    done
}

# --- Rust ---------------------------------------------------------------------------
rustup=$(command -v rustup || echo "$HOME/.cargo/bin/rustup")
[[ -x $rustup ]] || die "needs rustup for this user (https://rustup.rs)"
export RUSTUP_AUTO_INSTALL=0
if ! "$rustup" toolchain list | grep -q "^$TOOLCHAIN-"; then
    echo "Installing Rust $TOOLCHAIN with rust-src, for this user (rustup, in ~/.rustup)"
    "$rustup" toolchain install --no-self-update --profile minimal --component rust-src "$TOOLCHAIN"
elif ! "$rustup" component list --toolchain "$TOOLCHAIN" --installed | grep -q '^rust-src'; then
    echo "Adding rust-src to Rust $TOOLCHAIN, for this user (rustup, in ~/.rustup)"
    "$rustup" component add --toolchain "$TOOLCHAIN" rust-src
fi
rustc_version=$("$rustup" run "$TOOLCHAIN" rustc --version)
echo "$rustc_version"

# --- clang and lld --------------------------------------------------------------------
# The host's, newest first; else the distribution's packages, unpacked.
newest() {  # TOOL: the newest /usr/lib/llvm-N/bin/TOOL, or TOOL from PATH
    local d
    for d in $(ls -d /usr/lib/llvm-[0-9]* 2>/dev/null | sort -t- -k2 -rn); do
        [[ -x $d/bin/$1 ]] && { echo "$d/bin/$1"; return; }
    done
    command -v "$1" || true
}
clang=$(newest clang) lld=$(newest ld.lld) llvm_libs=""
if [[ -z $clang || -z $lld ]]; then
    command -v apt-get >/dev/null && command -v dpkg-query >/dev/null ||
        die "no clang or ld.lld here, and no apt-get to fetch them: install clang and lld"
    # The version of the host's clang, else of the newest libllvmN installed
    # (which the unpacked clang and lld then use).
    if [[ $clang == /usr/lib/llvm-*/bin/clang ]]; then
        n=${clang#/usr/lib/llvm-}; n=${n%%/*}
    else
        n=$(dpkg-query -W -f '${db:Status-Abbrev} ${Package}\n' 'libllvm[0-9]*' 2>/dev/null |
            sed -n 's/^ii  *libllvm\([0-9][0-9]*\)\(:.*\)\{0,1\}$/\1/p' | sort -rn | head -n 1)
        [[ -n $n ]] || die "no clang, and no libllvmN installed to unpack one against: install clang and lld"
    fi
    pkgs=()
    [[ -n $lld ]] || pkgs+=("lld-$n")
    [[ -n $clang ]] || pkgs+=("clang-$n" "libclang-cpp$n" "libclang-common-$n-dev")
    root=$CACHE/llvm-$n
    stamp=$root/.complete-$(IFS=-; echo "${pkgs[*]}")
    if [[ ! -f $stamp ]]; then
        echo "Unpacking ${pkgs[*]} from apt into $root (not installed)"
        rm -rf "$root" && mkdir -p "$root/debs"
        (cd "$root/debs" && apt-get download "${pkgs[@]}") || die "apt-get download ${pkgs[*]} failed"
        for f in "$root"/debs/*.deb; do dpkg-deb -x "$f" "$root/root"; done
        touch "$stamp"
    fi
    [[ -n $lld ]] || lld=$root/root/usr/lib/llvm-$n/bin/ld.lld
    [[ -n $clang ]] || clang=$root/root/usr/lib/llvm-$n/bin/clang
    llvm_libs=$root/root/usr/lib/x86_64-linux-gnu:$root/root/usr/lib/llvm-$n/lib
fi
ar=$(newest llvm-ar); [[ -n $ar ]] || ar=$(command -v ar) || die "no llvm-ar or ar"
sysroot=$CACHE/sysroot
mkdir -p "$CACHE/bin"
# The wrapper's LD_LIBRARY_PATH line, when clang's libraries came from the
# .debs (built apart: in one ${..:+..} the inner } would end the outer one).
libs_line=
[[ -z ${llvm_libs:-} ]] ||
    libs_line="LD_LIBRARY_PATH=$llvm_libs\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}; export LD_LIBRARY_PATH"
for cc in cc c++; do
    drv=$clang; [[ $cc == c++ ]] && drv=${clang%clang}clang++
    cat >"$CACHE/bin/dfly-$cc.part" <<EOF
#!/bin/sh
# Written by stick/dragonfly/build-rustic.sh: $(basename "$drv") for $TARGET.
$libs_line
exec $drv --target=$TARGET --sysroot=$sysroot -fuse-ld=$lld "\$@"
EOF
    chmod 755 "$CACHE/bin/dfly-$cc.part"
    mv "$CACHE/bin/dfly-$cc.part" "$CACHE/bin/dfly-$cc"
done
"$CACHE/bin/dfly-cc" --version | head -n 1
LD_LIBRARY_PATH=$llvm_libs "$lld" --version

# --- The sysroot, from the guest --------------------------------------------------------
image_id=$(stat -L -c '%s %Y' "$vm/$VM.qcow2")
if [[ $(cat "$sysroot/.image" 2>/dev/null) != "$image_id" ]]; then
    wait_guest
    echo "Copying the sysroot from $VM ($(ssh_g 'uname -sr'))"
    rm -rf "$sysroot.part" && mkdir "$sysroot.part"
    # One line: csh, which runs it, would end the command at a newline.
    T=1200 ssh_g 'cd / && tar -cf - --exclude usr/lib/debug --exclude usr/lib/i18n --exclude usr/lib/gcc47 lib usr/lib usr/include' |
        tar -C "$sysroot.part" -xf -
    [[ -f $sysroot.part/usr/lib/crt1.o && -f $sysroot.part/usr/include/stdio.h ]] || die "the sysroot is incomplete"
    # Absolute symlinks (usr/lib/libc.so -> /lib/libc.so.8) would point into the host.
    (cd "$sysroot.part" && find . -type l -lname '/*' | while read -r l; do
        ln -sfn "$(realpath -m --relative-to="$(dirname "$l")" ".$(readlink "$l")")" "$l"; done)
    echo "$image_id" >"$sysroot.part/.image"
    rm -rf "$sysroot" && mv "$sysroot.part" "$sysroot"
fi
printf '#include <stdio.h>\nint main(void) { puts("hello"); return 0; }\n' >"$work/hello.c"
"$CACHE/bin/dfly-cc" -o "$work/hello" "$work/hello.c" || die "clang can't link for $TARGET"

# --- The sources ----------------------------------------------------------------------
# Staged in $work, then copied into $CACHE/src only where they changed, so that
# cargo sees unchanged files as unchanged.
if [[ -z ${SRC:-} ]]; then
    git clone -q --depth 1 -b backotter "$REPO" "$work/rustic"
    commit=$(git -C "$work/rustic" rev-parse HEAD)
    core=$(sed -n '/^name = "rustic_core"/,/^source/s/.*#\([0-9a-f]*\).*/\1/p' "$work/rustic/Cargo.lock")
    rm -rf "$work/rustic/.git" "$CACHE/src/rustic_core"
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
        rsync -rlp --exclude .git --exclude target "$SRC/$d/" "$work/$d/"
    done
    # The helper runs cargo by a path: one that runs this toolchain's.
    printf '#!/bin/sh\nexec "%s" run "%s" cargo "$@"\n' "$rustup" "$TOOLCHAIN" >"$work/cargo"
    chmod 755 "$work/cargo"
    CARGO=$work/cargo "$here/../prep-rustic-src.sh" "$work" || die "prep-rustic-src.sh failed"
fi
echo "rustic $commit"
echo "rustic_core $core"
mkdir -p "$CACHE/src"
for d in rustic rustic_core; do
    if [[ -d $work/$d ]]; then rsync -rlp --checksum --delete "$work/$d/" "$CACHE/src/$d/"; fi
done

# --- Build ------------------------------------------------------------------------------
echo "Building"
start=$SECONDS
t=${TARGET//-/_}
export "CARGO_TARGET_${t^^}_LINKER=$CACHE/bin/dfly-cc" "CC_$t=$CACHE/bin/dfly-cc" \
    "CXX_$t=$CACHE/bin/dfly-c++" "AR_$t=$ar" CARGO_TARGET_DIR=$CACHE/target
# Default features, not "release" (which adds self-update); see the fork's
# build-backotter.sh.  The release profile is the fork's own.
(cd "$CACHE/src/rustic" && env -u PROJECT_VERSION nice "$rustup" run "$TOOLCHAIN" cargo build \
    --release --locked -Zbuild-std=std,panic_abort --target "$TARGET" 2>&1 |
    grep -v '^ *\(Compiling\|Downloaded\|Downloading\)'; exit "${PIPESTATUS[0]}")
echo "Built in $((SECONDS - start)) s"
bin=$CACHE/target/$TARGET/release/rustic
file "$bin"

# --- Check it in the guest, and install it -----------------------------------------------
wait_guest
ssh_g 'cat >/tmp/rustic-bko && chmod 755 /tmp/rustic-bko' <"$bin"
ssh_g '/tmp/rustic-bko --version && ldd /tmp/rustic-bko'
mkdir -p "$BKO_BIN/dragonfly"
cp "$bin" "$BKO_BIN/dragonfly/rustic-bko.part"
chmod 755 "$BKO_BIN/dragonfly/rustic-bko.part"
mv "$BKO_BIN/dragonfly/rustic-bko.part" "$BKO_BIN/dragonfly/rustic-bko"
printf '%s\nrustic_core %s\n%s, cross-compiled for %s with the sysroot of %s\n' \
    "$commit" "$core" "$rustc_version" "$TARGET" "$VM" >"$BKO_BIN/dragonfly/rustic-commit"
ls -l "$BKO_BIN/dragonfly/rustic-bko"
echo "Done in $((SECONDS - start_all)) s"
