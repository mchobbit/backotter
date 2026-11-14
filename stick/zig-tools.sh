#!/usr/bin/env bash
# The pinned zig and cargo-zigbuild that build-round.sh's arm64 rustic-bko
# builds take (docs/design/arm64.md, A2; testvm/compare/README.md, "Builds"),
# for this user, in BKO_TOOLS (default ~/backotter-work/tools):
#
#   BKO_TOOLS/zig-ZIG_VERSION/zig
#   BKO_TOOLS/cargo-zigbuild-ZIGBUILD_VERSION/bin/cargo-zigbuild
#
# Usage: zig-tools.sh --check | --install | --path | --versions
#
#   --check     exit 0 when both are there at their pins (zig version,
#               cargo-zigbuild --version), else 1 and say how to install them
#   --install   zig: its tarball for this machine (x86_64 or aarch64) from
#               ziglang.org, checked against the sha256 pinned below before
#               it is unpacked (and its minisign signature, by Zig's key, when
#               minisign is installed); cargo-zigbuild: cargo install
#               --locked --version =ZIGBUILD_VERSION (cargo checks crates.io's
#               checksums), with rustup's BKO_RUST_TOOLCHAIN (default 1.94.1).
#               Nothing outside BKO_TOOLS but cargo's registry cache in
#               CARGO_HOME; no sudo.  Installed already: nothing is done.
#   --path      the two directories for PATH, zig's first, colon-separated
#   --versions  "zig ZIG_VERSION cargo-zigbuild ZIGBUILD_VERSION"
#
# RUSTUP_HOME and CARGO_HOME, when unset, are this host's row's in
# testvm/hosts.json (bigfoot's toolchains live in its staging folder), else
# rustup's defaults.  The pins change only with a new sha256 for both of
# zig's tarballs here (the minisign check made when they were pinned:
# 2026-11-14 on Chopin, both verified), and a build's digest records both
# versions, so a new pin builds afresh.

set -euo pipefail

ZIG_VERSION=0.15.2
ZIGBUILD_VERSION=0.24.1
# sha256 of zig-ARCH-linux-ZIG_VERSION.tar.xz, by uname -m.
zig_sha256() {
    case $1 in
        x86_64) echo 02aa270f183da276e5b5920b1dac44a63f1a49e55050ebde3aecc9eb82f93239 ;;
        aarch64) echo 958ed7d1e00d0ea76590d27666efbf7a932281b3d7ba0c6b01b0ff26498f667f ;;
        *) return 1 ;;
    esac
}
# Zig's public key (ziglang.org/download).
ZIG_MINISIGN_KEY=RWSGOq2NVecA2UPNdBUZykf1CCb147pkmdtYxgb3Ti+JO/wCYvhbAb/U

here=$(cd "$(dirname "$0")" && pwd)
die() { echo "zig-tools: $*" >&2; exit 2; }
TOOLS=${BKO_TOOLS:-$HOME/backotter-work/tools}
zdir=$TOOLS/zig-$ZIG_VERSION
cdir=$TOOLS/cargo-zigbuild-$ZIGBUILD_VERSION
if [[ -f $here/../testvm/hosts.sh ]]; then
    # shellcheck source=../testvm/hosts.sh
    . "$here/../testvm/hosts.sh" && hosts_rust_env 2>/dev/null || true
fi

check() {
    local v
    v=$("$zdir/zig" version 2>/dev/null) && [[ $v == "$ZIG_VERSION" ]] ||
        { echo "no zig $ZIG_VERSION in $zdir: $0 --install"; return 1; }
    v=$("$cdir/bin/cargo-zigbuild" --version 2>/dev/null) && [[ $v == "cargo-zigbuild $ZIGBUILD_VERSION" ]] ||
        { echo "no cargo-zigbuild $ZIGBUILD_VERSION in $cdir/bin: $0 --install"; return 1; }
}

case ${1:-} in
    --check) check ;;
    --path) echo "$zdir:$cdir/bin" ;;
    --versions) echo "zig $ZIG_VERSION cargo-zigbuild $ZIGBUILD_VERSION" ;;
    --install)
        m=$(uname -m)
        want=$(zig_sha256 "$m") || die "no zig pin for a $m machine (x86_64, aarch64)"
        mkdir -p "$TOOLS"
        if [[ $("$zdir/zig" version 2>/dev/null || true) != "$ZIG_VERSION" ]]; then
            t=zig-$m-linux-$ZIG_VERSION.tar.xz
            tmp=$(mktemp -d "$TOOLS/.zig-install.XXXXXX")
            trap 'rm -rf "$tmp"' EXIT
            echo "zig-tools: downloading $t"
            curl -fsSL --proto '=https' -o "$tmp/$t" "https://ziglang.org/download/$ZIG_VERSION/$t" ||
                die "couldn't download $t"
            got=$(sha256sum "$tmp/$t" | cut -d' ' -f1)
            [[ $got == "$want" ]] || die "$t has sha256 $got, not the pinned $want: not unpacked"
            if command -v minisign >/dev/null 2>&1; then
                curl -fsSL --proto '=https' -o "$tmp/$t.minisig" "https://ziglang.org/download/$ZIG_VERSION/$t.minisig" ||
                    die "couldn't download $t.minisig"
                minisign -Vqm "$tmp/$t" -P "$ZIG_MINISIGN_KEY" -x "$tmp/$t.minisig" ||
                    die "$t: its minisign signature isn't Zig's"
                echo "zig-tools: $t: sha256 as pinned, signed by Zig's key"
            else
                echo "zig-tools: $t: sha256 as pinned (no minisign here)"
            fi
            mkdir "$tmp/x" && tar -C "$tmp/x" -xJf "$tmp/$t" || die "couldn't unpack $t"
            rm -rf "$zdir" && mv "$tmp/x/zig-$m-linux-$ZIG_VERSION" "$zdir" || die "no zig-$m-linux-$ZIG_VERSION in $t"
            rm -rf "$tmp"; trap - EXIT
        fi
        [[ $("$zdir/zig" version) == "$ZIG_VERSION" ]] || die "$zdir/zig isn't zig $ZIG_VERSION"
        if [[ $("$cdir/bin/cargo-zigbuild" --version 2>/dev/null || true) != "cargo-zigbuild $ZIGBUILD_VERSION" ]]; then
            tc=${BKO_RUST_TOOLCHAIN:-1.94.1}
            PATH=$HOME/.cargo/bin:$PATH RUSTUP_TOOLCHAIN=$tc RUSTUP_AUTO_INSTALL=0 \
                cargo install --locked --version "=$ZIGBUILD_VERSION" --root "$cdir" cargo-zigbuild ||
                die "cargo install cargo-zigbuild $ZIGBUILD_VERSION failed (Rust $tc: BKO_RUST_TOOLCHAIN)"
        fi
        check || exit 1
        echo "zig-tools: zig $ZIG_VERSION in $zdir, cargo-zigbuild $ZIGBUILD_VERSION in $cdir/bin"
        ;;
    *) sed -n '9p' "$0" | sed 's/^# //' >&2; exit 2 ;;
esac
