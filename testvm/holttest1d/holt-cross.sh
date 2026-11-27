#!/usr/bin/env bash
# Cross-build bko-holt for FreeBSD, NetBSD, illumos, OpenBSD or DragonFly here
# on an amd64 Linux host, with no guest booted once its sysroot is cached
# (docs/design/img-speed.md §8.1, D9, D10; I10, I11).  Run as m on a build host:
#
#   holt-cross.sh PLATFORM                   build bko-holt (TMPDIR: cargo's target/, in the RAM disk)
#   holt-cross.sh --reserved RUN PLATFORM    HoltTest2's: the same under a reservation of its own
#   holt-cross.sh --tests PLATFORM DIR       E2 step 2: holt's test binaries, cross-built, into DIR
#   holt-cross.sh --kind PLATFORM            the build kind (cross:TOOLCHAIN:GUEST@SYSROOT12), built nothing
#
# PLATFORM: freebsd (the sysroot of freebsd14, so that it runs on 14 and 15,
# HardenedBSD and GhostBSD, as the VM build does), netbsd (netbsd11) or
# illumos (omnios58), openbsd (openbsd8) or dragonfly (dragonfly6).  haiku
# stays a VM build (D9): refused, exit 2.
#
# Rust, never the guest's packaged Rust nor the host's default (D10): for the
# tier-2 three, rustup's pinned HOLT_RUST_TOOLCHAIN (default 1.94.1, the Linux
# bko-holt's), whose std for the target is rustup's, added to that toolchain
# when missing; for openbsd and dragonfly (tier 3: rustup has no std for
# them), the pinned nightly HOLT_RUST_NIGHTLY (default the one
# stick/dragonfly/build-rustic.sh builds DragonFly's rustic-bko with), its
# rust-src added when missing, and std built with the program
# (-Zbuild-std=std,panic_unwind; std's own crates, which rust-src's Cargo.lock
# pins, from cargo's cache or crates.io, so no --offline there).  OpenBSD's
# VM build has IBT landing pads (endbr64, and lld's GNU property note, which
# it writes only when every object carries one), so OpenBSD's code, std's
# included, is built with -Zcf-protection=branch (§8.2 step 0, D11).  OpenBSD's
# own lld does three things upstream lld does not, which this does instead:
# -lNAME takes the newest libNAME.so.MAJOR.MINOR (a wrapper gives lld
# -l:FILE), .openbsd.randomdata.* (the retguard cookies of the base's crt
# objects) is gathered into .openbsd.randomdata and so PT_OPENBSD_RANDOMIZE
# (an INSERT script), and the code segment is execute-only (holt_e2.py xonly,
# once it holds code alone).  The link goes through clang and lld with --sysroot, as
# stick/dragonfly/build-rustic.sh links DragonFly's rustic-bko: the host's
# clang and ld.lld if it has them, else Debian's clang-N and lld-N (N the
# host's newest libllvmN), downloaded with apt-get download and unpacked into
# the cache, not installed.  Paths are remapped (--remap-path-prefix: the
# tree is /holt in the binary).  holt/ has no dependencies, so nothing is
# fetched for the build itself (cargo --offline --locked).
#
# The sysroot: /lib and /usr/lib (illumos: their amd64/ and gcc's runtime
# and crt objects; dragonfly: and /usr/lib/gcc80, gcc's crt objects and libgcc) copied once from a throwaway (-snapshot) boot of the
# image the VM build builds in: stick/build-rustic-native.sh's build image
# WORK/vms-bko-build/VM-rust.qcow2 when the host has one (build-holt.sh boots
# it too; the test guests of NetBSD and OmniOS lack the comp set and the C
# runtime objects a link needs), else WORK/vms/VM.  It is cached in
# CACHE/sysroot/VM, made again by itself when that image changes (its size
# and mtime, and the guest's image-state generation), and named by
# SYSROOT, the sha256 over its sorted "path<TAB>sha256" lines: the same files
# on two hosts give the same SYSROOT.  The guest takes a port of 2600-2689
# under ht1-queue.sh's /tmp/bko-ht1-ports locks.  One maker per cache at a
# time (CACHE/.lock-VM, exclusive), and never while a build uses that sysroot
# (each holds the lock shared to its end).
#
# The output: HOLT_OUT/bko-holt, where HOLT_OUT must end in cross/PLATFORM
# (HoltTest2's HOST_ROOT/holt-bin/CODE/cross/PLATFORM, §8.2a): a cross build
# is a check build until I12 and never lands in CODE/PLATFORM/, nor in the
# shipped sets (WORK/holt-bin, WORK/bko-bin) or /bkoscratch/holttest1d, by
# any path (the same device and inode, as build-holt.sh checks): exit 2.
# Default CACHE/out/cross/PLATFORM.  HOLT_LOGS (default
# CACHE/logs/PLATFORM): build.log, the sysroot guest's logs.  HOLT_INFO=FILE
# gets build-holt.sh's JSON (platform, sha256, rustc, host, time, src, out)
# and kind "cross", build (the build kind), toolchain, tier, target, sysroot,
# sysroot_guest, sysroot_image, clang and lld (their --version lines: the
# linker is not in the kind).  HOLT_SRC: the holt tree (default
# WORK/holt-src).  HOLT_CROSS_CACHE: the cache (default WORK/holt-cross).
#
# --reserved RUN PLATFORM (testvm/test2/t2lib/holt.py starts it, detached):
#   WEAR_TAG=ht1:RUN:cross-PLATFORM cmp-run.sh --reserve HOLT_RESERVE 4 env TMPDIR=DIR holt-cross.sh PLATFORM
# HOLT_RESERVE GiB of RAM disk (default 1.5), DIR /mnt/bko-ram/t2x-RUN-PLATFORM,
# made before and removed after; its exit status in HOLT_LOGS/exit.
#
# --tests PLATFORM DIR: `cargo test --no-run` for the target (E2 step 2; the test
# profile, as gate:holt's cargo test).  DIR/artifacts.tsv lists the executables it
# made (holt_e2.py artifacts: NAME<TAB>1 for a test binary, 0 for a program<TAB>
# PATH), DIR/tree the path the tests' CARGO_MANIFEST_DIR names: the tree is built
# at TMPDIR/e2/holt, beside a docs/design/holt-json.md (a test reads it), into
# TMPDIR/e2-target, and a guest must see all of them at those same paths (the
# tests run bko-holt by its CARGO_BIN_EXE path).  No remapping here.
#
# Exit 0 built; 1 a build or sysroot failure; 2 refused.
set -euo pipefail

WORK=${BKO_WORK:-/home/m/backotter-work}
HERE=$(dirname "$(realpath "$0")")
CACHE=${HOLT_CROSS_CACHE:-$WORK/holt-cross}
die() { echo "holt-cross: $*" >&2; exit "${2:-1}"; }

plat_vars() {  # PLATFORM: TARGET CTARGET VM TIER TC, or exit 2
    TIER=2
    case $1 in
        freebsd) TARGET=x86_64-unknown-freebsd CTARGET=x86_64-unknown-freebsd14.0 VM=freebsd14 ;;
        netbsd) TARGET=x86_64-unknown-netbsd CTARGET=x86_64-unknown-netbsd11.0 VM=netbsd11 ;;
        illumos) TARGET=x86_64-unknown-illumos CTARGET=x86_64-pc-solaris2.11 VM=omnios58 ;;
        openbsd) TARGET=x86_64-unknown-openbsd CTARGET=x86_64-unknown-openbsd VM=openbsd8 TIER=3 ;;
        dragonfly) TARGET=x86_64-unknown-dragonfly CTARGET=x86_64-unknown-dragonfly VM=dragonfly6 TIER=3 ;;
        haiku) die "haiku stays a VM build (docs/design/img-speed.md D9)" 2 ;;
        *) die "PLATFORM: freebsd, netbsd, illumos, openbsd or dragonfly" 2 ;;
    esac
    # (stick/dragonfly/build-rustic.sh's TOOLCHAIN: rustc 1.101.0-nightly (92b5b8c87 2026-10-29))
    if [[ $TIER == 3 ]]; then TC=${HOLT_RUST_NIGHTLY:-nightly-2026-10-31}; else TC=${HOLT_RUST_TOOLCHAIN:-1.94.1}; fi
}

# --- Where a build may land (§8.2a) ----------------------------------------------------
SHARED=("$WORK/holt-bin" "$WORK/bko-bin" /bkoscratch/holttest1d)
in_shared() {  # PATH: the shared directory it is or is in (by device and inode), printed
    local p d; p=$(realpath -m -- "$1")
    while :; do
        if [[ -e $p ]]; then
            for d in "${SHARED[@]}"; do
                [[ -e $d && $p -ef $d ]] && { echo "$d"; return 0; }
            done
        fi
        [[ $p == / ]] && return 1
        p=$(dirname -- "$p")
    done
}
check_out() {  # OUT PLATFORM
    local out p d
    out=$(realpath -m -- "$1") p=$2
    [[ $(basename -- "$out") == "$p" && $(basename -- "$(dirname -- "$out")") == cross ]] ||
        die "HOLT_OUT=$1 must end in cross/$p: a cross build is a check build, never CODE/$p/ (docs/design/img-speed.md §8.2a)" 2
    if d=$(in_shared "$out"); then
        die "HOLT_OUT=$1 is in $d (the same device and inode): a cross build never lands in a shipped set" 2
    fi
    for v in HOLT_LOGS HOLT_INFO; do
        [[ -n ${!v:-} ]] || continue
        local q=${!v}; [[ $v == HOLT_INFO ]] && q=$(dirname -- "$q")
        if d=$(in_shared "$q"); then die "$v=${!v} is in $d: not a cross build's" 2; fi
    done
}

# --- --reserved ------------------------------------------------------------------------
if [[ ${1:-} == --reserved ]]; then
    [[ $# -eq 3 ]] || die "--reserved RUN PLATFORM" 2
    RUN=$2 PLATFORM=$3
    [[ $RUN =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "RUN '$RUN': a plain name" 2
    plat_vars "$PLATFORM"
    check_out "${HOLT_OUT:-$CACHE/out/cross/$PLATFORM}" "$PLATFORM"
    LOGS=${HOLT_LOGS:-$CACHE/logs/$PLATFORM}
    mkdir -p "$LOGS"; rm -f "$LOGS/exit"
    CMP_RUN=${HOLT_CMP_RUN:-$WORK/cmp-run.sh}
    r=${HOLT_RAM_ROOT:-/mnt/bko-ram}
    tmp=$r/t2x-$RUN-$PLATFORM rc=0
    finish() { rm -rf --one-file-system "$tmp"; echo "$1" >"$LOGS/exit"; exit "$1"; }
    trap 'finish 143' TERM; trap 'finish 130' INT; trap 'finish 129' HUP
    mountpoint -q "$r" || { echo "holt-cross: no RAM disk at $r" >&2; finish 2; }
    rm -rf --one-file-system "$tmp"; mkdir -p "$tmp" || finish 1
    WEAR_TAG=ht1:$RUN:cross-$PLATFORM "$CMP_RUN" --reserve "${HOLT_RESERVE:-1.5}" 4 \
        env TMPDIR="$tmp" "$HERE/holt-cross.sh" "$PLATFORM" || rc=$?
    finish "$rc"
fi

mode=build
case ${1:-} in
    --tests) mode=tests; [[ $# -eq 3 ]] || die "--tests PLATFORM DIR" 2; TDIR=$3; set -- "$2" ;;
    --kind) mode=kind; [[ $# -eq 2 ]] || die "--kind PLATFORM" 2; set -- "$2" ;;
esac
PLATFORM=${1:-}
plat_vars "$PLATFORM"
[[ $# -eq 1 ]] || die "usage: holt-cross.sh [--reserved RUN | --tests | --kind] PLATFORM" 2
SRC=${HOLT_SRC:-$WORK/holt-src}
OUT=${HOLT_OUT:-$CACHE/out/cross/$PLATFORM}
LOGS=${HOLT_LOGS:-$CACHE/logs/$PLATFORM}
[[ $mode != build ]] || check_out "$OUT" "$PLATFORM"
[[ $mode == kind ]] || [[ -f $SRC/Cargo.toml ]] || die "no $SRC/Cargo.toml (HOLT_SRC)"
if [[ $mode != kind ]]; then
    [[ -n ${TMPDIR:-} && -d $TMPDIR ]] || die "TMPDIR must name a directory for cargo's target/ (one in the RAM disk, under cmp-run.sh --reserve)" 2
fi
mkdir -p "$CACHE" "$LOGS"

# --- Rust ----------------------------------------------------------------------------------
rustup=$HOME/.cargo/bin/rustup cargo=$HOME/.cargo/bin/cargo
[[ -x $rustup && -x $cargo ]] || die "no rustup in ~/.cargo/bin (https://rustup.rs)"
export RUSTUP_TOOLCHAIN=$TC RUSTUP_AUTO_INSTALL=0
"$rustup" run "$TC" rustc -V >/dev/null 2>&1 ||
    die "no Rust $TC (HOLT_RUST_TOOLCHAIN): rustup toolchain install $TC --profile minimal"
if [[ $TIER == 3 ]]; then
    if ! "$rustup" component list --installed --toolchain "$TC" 2>/dev/null | grep -q '^rust-src'; then
        [[ $mode == kind ]] || { echo "Adding rust-src to Rust $TC (rustup, in ~/.rustup)"
            "$rustup" component add --toolchain "$TC" rust-src >&2; }
    fi
elif ! "$rustup" target list --installed --toolchain "$TC" 2>/dev/null | grep -qxF "$TARGET"; then
    [[ $mode == kind ]] || { echo "Adding $TARGET's std to Rust $TC (rustup, in ~/.rustup)"
        "$rustup" target add --toolchain "$TC" "$TARGET" >&2; }
fi
rustc_version=$("$rustup" run "$TC" rustc --version)

# --- The sysroot ---------------------------------------------------------------------------
sysroot=$CACHE/sysroot/$VM
image=$WORK/vms-bko-build/$VM-rust.qcow2 key=$WORK/vms-bko-build/$VM.id_ed25519 base=$WORK/vms-bko-build/$VM-base.qcow2
if [[ ! -f $image || ! -f $key ]]; then
    image=$WORK/vms/$VM/$VM.qcow2 key=$WORK/vms/$VM/id_ed25519 base=""
fi
image_id() {  # what the cached sysroot was made from
    local f
    printf '%s\n' "${image#"$WORK"/}"
    for f in "$image" ${base:+"$base"}; do stat -L -c "${f##*/} %s %Y" "$f"; done
    sed -n 's/.*"generation": *"\([0-9a-f]*\)".*/generation \1/p' "$WORK/vms/$VM/image-state" 2>/dev/null
}
sysroot_digest() {  # sha256 over the sysroot's sorted "path<TAB>sha256" lines (links as path<TAB>->target)
    (cd "$sysroot" && find . ! -type d ! -name .image ! -name .digest -print0 | LC_ALL=C sort -z |
        while IFS= read -r -d '' f; do
            if [[ -L $f ]]; then printf '%s\t->%s\n' "${f#./}" "$(readlink "$f")"
            else printf '%s\t%s\n' "${f#./}" "$(sha256sum <"$f" | cut -d' ' -f1)"; fi
        done) | sha256sum | cut -d' ' -f1
}
# The files a link needs, as the guest's /bin/sh lists them (one line: csh-safe
# not needed, these guests' root shell is sh), into pax's ustar on stdout.
case $PLATFORM in
    freebsd|netbsd) LIST='cd / && for f in lib/* usr/lib/*; do [ -d "$f" ] || echo "$f"; done' ;;
    illumos) LIST='cd / && { for f in lib/amd64/* usr/lib/amd64/* usr/gcc/14/lib/amd64/*; do [ -d "$f" ] || echo "$f"; done; find opt/gcc-14/lib/gcc \( -name "*.o" -o -name "*.a" -o -name "*.so*" \) ! -path "*plugin*"; }' ;;
    # OpenBSD has no /lib.  DragonFly's root shell is csh, which runs this line (no "!" in it):
    # gcc80/ holds the crtbegin objects and libgcc_pic.a that clang's DragonFly driver links.
    openbsd) LIST='cd / && for f in usr/lib/*; do if [ -f "$f" ]; then echo "$f"; fi; done' ;;
    dragonfly) LIST='cd / && for f in lib/* usr/lib/* usr/lib/gcc80/*; do if [ -f "$f" ]; then echo "$f"; fi; done' ;;
esac
# What the throwaway guest needs first: illumos's crtbegin.o, crtend.o and libgcc.a are gcc's
# (developer/gcc14 in /opt/gcc-14, which the VM build links with; the build image has it, and
# pkg's exit 4 is "nothing to do").  Its runtime, libgcc_s, is the base's /usr/gcc/14/lib/amd64.
PREP=true
[[ $PLATFORM != illumos ]] ||
    PREP='PATH=/usr/bin:/usr/sbin:/sbin:/bin; export PATH; pkg install -q developer/gcc14; s=$?; [ $s -eq 0 ] || [ $s -eq 4 ]'
sysroot_current() { [[ -f $sysroot/.image && $(cat "$sysroot/.image") == "$(image_id)" && -f $sysroot/.digest ]]; }
# CACHE/.lock-VM: a build holds it shared from here to its end (it links against the sysroot,
# and its kind names the sysroot's digest), a maker exclusive, so that a sysroot is never
# replaced under a build that is using it (a vm-update of the image between two builds).
make_sysroot() {
    [[ -f $image ]] || die "no image for $VM ($WORK/vms-bko-build/$VM-rust.qcow2 or $WORK/vms/$VM/$VM.qcow2)"
    local port="" plk="" p
    exec {SYSLK}>>"$CACHE/.lock-$VM"
    flock -s "$SYSLK"
    sysroot_current && return 0
    flock -n "$SYSLK" || { echo "holt-cross: waiting for the other builds or sysroot maker of $VM" >&2; flock "$SYSLK"; }
    if sysroot_current; then flock -s "$SYSLK"; return 0; fi
    mkdir -p /tmp/bko-ht1-ports
    for p in $(seq 2600 2689); do
        [[ -z $(ss -Hltn "sport = :$p" 2>/dev/null) ]] || continue
        exec {plk}>>"/tmp/bko-ht1-ports/$p.lock" || continue
        if flock -n "$plk"; then port=$p; break; fi
        exec {plk}>&-; plk=""
    done
    [[ -n $port ]] || die "no free port in 2600-2689 for the sysroot guest"
    VM_LIB=$(dirname "$HERE")/vm-lib.sh; [[ -f $VM_LIB ]] || VM_LIB=$WORK/testvm/vm-lib.sh
    # shellcheck source=../vm-lib.sh
    . "$VM_LIB"
    echo "Copying the sysroot from a throwaway copy of ${image#"$WORK"/} (port $port)"
    local glogs=$LOGS/sysroot-guest
    mkdir -p "$glogs"
    VM_IMAGE=$image VM_IMAGE_KEY=$key vm_boot "$VM" "$port" "$glogs" --snapshot --cpus 2 --memory 2048 ||
        die "the $VM guest didn't come up (logs $glogs)"
    rm -rf "$sysroot.part" && mkdir -p "$sysroot.part"
    T=1800 vm_ssh "$PREP" >"$glogs/prep.log" 2>&1 || { vm_halt; die "$VM: $PREP failed ($glogs/prep.log)"; }
    if ! T=1200 vm_ssh "$LIST | pax -w -x ustar" | tar -C "$sysroot.part" -xf -; then
        vm_halt; die "copying the sysroot from $VM failed"
    fi
    vm_halt
    exec {plk}>&-
    case $PLATFORM in
        freebsd) need=(usr/lib/crt1.o usr/lib/crti.o usr/lib/crtbeginS.o lib/libc.so.7 usr/lib/libc.so lib/libgcc_s.so.1) ;;
        netbsd) need=(usr/lib/crt0.o usr/lib/crti.o usr/lib/crtbeginS.o lib/libc.so.12 usr/lib/libc.so usr/lib/libgcc_s.so) ;;
        openbsd) need=(usr/lib/crt0.o usr/lib/crtbegin.o usr/lib/crtend.o usr/lib/libcompiler_rt.a)
            for p in c pthread c++abi; do
                compgen -G "$sysroot.part/usr/lib/lib$p.so.*" >/dev/null || die "the sysroot from $VM has no /usr/lib/lib$p.so.*"
            done ;;
        dragonfly) need=(usr/lib/crt1.o usr/lib/crti.o usr/lib/gcc80/crtbeginS.o usr/lib/gcc80/libgcc_pic.a lib/libc.so.8 usr/lib/libc.so usr/lib/libpthread.so) ;;
        illumos) need=(usr/lib/amd64/crt1.o usr/lib/amd64/crti.o usr/lib/amd64/values-Xa.o lib/amd64/libc.so.1)
            p=$(ls -d "$sysroot.part"/opt/gcc-14/lib/gcc/x86_64-pc-solaris2.11/*/crtbegin.o 2>/dev/null | head -n 1)
            [[ -n $p ]] || die "the sysroot from $VM has no gcc 14 crtbegin.o (/opt/gcc-14)"
            need+=("${p#"$sysroot.part"/}") ;;
    esac
    for p in "${need[@]}"; do [[ -e $sysroot.part/$p || -L $sysroot.part/$p ]] || die "the sysroot from $VM has no /$p"; done
    # Absolute links (usr/lib/libc.so -> /lib/libc.so.12) would point into the host.
    (cd "$sysroot.part" && find . -type l -lname '/*' | while read -r l; do
        ln -sfn "$(realpath -m --relative-to="$(dirname "$l")" ".$(readlink "$l")")" "$l"; done)
    rm -rf "$sysroot" && mv "$sysroot.part" "$sysroot"
    sysroot_digest >"$sysroot/.digest"
    image_id >"$sysroot/.image"
    flock -s "$SYSLK"   # (shared again, to this script's end)
}

if [[ $mode == kind ]]; then
    [[ -f $sysroot/.digest && -f $sysroot/.image && $(cat "$sysroot/.image") == "$(image_id)" ]] ||
        die "no current sysroot of $VM in $CACHE (a build makes it)"
    echo "cross:$TC:$VM@$(cut -c1-12 "$sysroot/.digest")"
    exit 0
fi
make_sysroot
SYSROOT=$(cat "$sysroot/.digest")
KIND=cross:$TC:$VM@${SYSROOT:0:12}

# --- clang and lld (stick/dragonfly/build-rustic.sh's way) ----------------------------------
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
        exec {llk}>"$CACHE/.lock-llvm"; flock "$llk"
        if [[ ! -f $stamp ]]; then
            echo "Unpacking ${pkgs[*]} from apt into $root (not installed)"
            rm -rf "$root" && mkdir -p "$root/debs"
            (cd "$root/debs" && apt-get download "${pkgs[@]}") >&2 || die "apt-get download ${pkgs[*]} failed"
            for f in "$root"/debs/*.deb; do dpkg-deb -x "$f" "$root/root"; done
            touch "$stamp"
        fi
        exec {llk}>&-
    fi
    [[ -n $lld ]] || lld=$root/root/usr/lib/llvm-$n/bin/ld.lld
    [[ -n $clang ]] || clang=$root/root/usr/lib/llvm-$n/bin/clang
    llvm_libs=$root/root/usr/lib/x86_64-linux-gnu:$root/root/usr/lib/llvm-$n/lib
fi
# The linker driver: clang for the target, against the sysroot, through lld.
ccw=$TMPDIR/holt-cross-cc ld=$lld cc_extra=
case $PLATFORM in
    # DragonFly's gcc (the VM build's) links PIE, names /libexec/ld-elf.so.2 and gcc80's runpath, and
    # puts read-only data in the code segment; clang's DragonFly driver drops rustc's -pie.
    dragonfly) cc_extra="-Wl,-pie -Wl,--dynamic-linker=/libexec/ld-elf.so.2 -Wl,-rpath,/usr/lib/gcc80 -Wl,--no-rosegment" ;;
    # NetBSD's gcc names /usr/libexec/ld.elf_so (the VM build's), clang /libexec's.
    netbsd) cc_extra=-Wl,--dynamic-linker=/usr/libexec/ld.elf_so ;;
    # gcc is in /opt/gcc-14, which clang's search for one (/usr/gcc/N) doesn't look in.
    # gcc's runtime (libgcc_s, libssp) is the base's /usr/gcc/14/lib/amd64, which the VM build's
    # gcc names with -L and -R.
    illumos) g=$(ls -d "$sysroot"/opt/gcc-14/lib/gcc/x86_64-pc-solaris2.11/* | head -n 1)
        cc_extra="--gcc-install-dir=$g -L$sysroot/usr/gcc/14/lib/amd64 -Wl,-rpath,/usr/gcc/14/lib/amd64" ;;
esac
libs_line=
[[ -z $llvm_libs ]] || libs_line="LD_LIBRARY_PATH=$llvm_libs\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}; export LD_LIBRARY_PATH"
if [[ $PLATFORM == openbsd ]]; then
    # OpenBSD's libraries are libNAME.so.MAJOR.MINOR alone, and its own ld takes -lNAME for the
    # newest; lld looks for libNAME.so only.  -l:FILE is that file, and names it in DT_NEEDED as
    # OpenBSD's ld does (libc.so.104.0: ld.so takes that major and any minor at least as new).
    # OpenBSD's lld also gathers .openbsd.randomdata.* (the retguard cookies of the base's crt
    # objects) into .openbsd.randomdata, which gets PT_OPENBSD_RANDOMIZE, so that the kernel fills it
    # with random bytes; upstream lld leaves them apart, unrandomised, unless a script says so.
    cat >"$TMPDIR/holt-cross-openbsd.ld" <<'LDS'
SECTIONS { .openbsd.randomdata : { *(.openbsd.randomdata .openbsd.randomdata.*) } }
INSERT AFTER .dtors;
LDS
    cc_extra+=" -Wl,-T,$TMPDIR/holt-cross-openbsd.ld"
    ld=$TMPDIR/holt-cross-ld
    cat >"$ld" <<LDW
#!/usr/bin/env bash
# holt-cross.sh: OpenBSD's -lNAME, for $lld
$libs_line
a=()
for x in "\$@"; do
    # (rustc's openbsd target adds -L/usr/lib: the sysroot's, not this host's)
    [[ \$x != -L/usr/lib ]] || x=-L$sysroot/usr/lib
    if [[ \$x == -l[!:]* && ! -e $sysroot/usr/lib/lib\${x#-l}.so ]]; then
        f=\$(cd $sysroot/usr/lib && ls -d lib\${x#-l}.so.[0-9]* 2>/dev/null | sort -t. -k3,3n -k4,4n | tail -n 1)
        [[ -z \$f ]] || x=-l:\$f
    fi
    a+=("\$x")
done
exec $lld "\${a[@]}"
LDW
    chmod 755 "$ld"
elif [[ $PLATFORM == illumos ]]; then
    # clang's Solaris driver speaks Solaris ld (-C, -z ignore and record, the
    # interpreter left to ld's default): lld gets those in its own words.
    ld=$TMPDIR/holt-cross-ld
    cat >"$ld" <<LDW
#!/usr/bin/env bash
# holt-cross.sh: Solaris ld's arguments, from clang's Solaris driver, for $lld
$libs_line
a=() z=0
for x in "\$@"; do
    if ((z)); then
        z=0
        case \$x in ignore) a+=(--as-needed) ;; record) a+=(--no-as-needed) ;; *) a+=(-z "\$x") ;; esac
        continue
    fi
    case \$x in -C) ;; -z) z=1 ;; *) a+=("\$x") ;; esac
done
exec $lld --eh-frame-hdr --dynamic-linker=/usr/lib/amd64/ld.so.1 "\${a[@]}"
LDW
    chmod 755 "$ld"
fi
{
    echo '#!/bin/sh'
    echo "# holt-cross.sh: $(basename "$clang") for $CTARGET, sysroot $sysroot"
    [[ -z $libs_line ]] || echo "$libs_line"
    echo "exec $clang --target=$CTARGET --sysroot=$sysroot -fuse-ld=$ld $cc_extra \"\$@\""
} >"$ccw"
chmod 755 "$ccw"
clang_version=$("$ccw" --version | head -n 1)
lld_version=$(${llvm_libs:+env LD_LIBRARY_PATH=$llvm_libs} "$lld" --version | head -n 1)

# --- The build -------------------------------------------------------------------------------
t=${TARGET//-/_}
export "CARGO_TARGET_${t^^}_LINKER=$ccw" "CC_$t=$ccw"
# Tier 3: std built here, for the target, with the program (rust-src's Cargo.lock: --locked).
zstd=() offline=--offline flags=()
if [[ $TIER == 3 ]]; then
    zstd=(-Zbuild-std=std,panic_unwind) offline=""
    rsrc=$("$rustup" run "$TC" rustc --print sysroot)/lib/rustlib/src/rust
    flags+=("--remap-path-prefix=$rsrc=/rustc/src" "--remap-path-prefix=$HOME/.cargo/registry/src=/cargo/registry/src")
fi
[[ $PLATFORM != openbsd ]] || flags+=(-Zcf-protection=branch)
if [[ $mode == tests ]]; then
    # E2 step 2: the tree where the guest will see it too (CARGO_MANIFEST_DIR).
    tree=$TMPDIR/e2/holt
    rm -rf "$TMPDIR/e2"; mkdir -p "$tree" "$TMPDIR/e2/docs/design"
    tar -C "$SRC" --exclude=./target -cf - . | tar -C "$tree" -xf -   # (the tests read README.md too)
    [[ ! -f $SRC/../docs/design/holt-json.md ]] || cp "$SRC/../docs/design/holt-json.md" "$TMPDIR/e2/docs/design/"
    if ! (cd "$tree" && RUSTFLAGS="${flags[*]}" CARGO_TARGET_DIR=$TMPDIR/e2-target "$cargo" test --no-run $offline --locked \
            "${zstd[@]}" --target "$TARGET" --message-format=json-render-diagnostics) >"$LOGS/tests.json" 2>"$LOGS/tests.log"; then
        grep -E "^(error|warning)" -A 6 "$LOGS/tests.log" | head -80 >&2
        exit 1
    fi
    mkdir -p "$TDIR"
    python3 -I "$HERE/holt_e2.py" artifacts "$LOGS/tests.json" >"$TDIR/artifacts.tsv"
    if [[ $PLATFORM == openbsd ]]; then   # (as the build's, above)
        while IFS=$'\t' read -r _ _ path; do
            python3 -I "$HERE/holt_e2.py" xonly "$path" >>"$LOGS/tests.log" || { tail -n 1 "$LOGS/tests.log" >&2; exit 1; }
        done <"$TDIR/artifacts.tsv"
    fi
    echo "$tree" >"$TDIR/tree"
    echo "$KIND" >"$TDIR/kind"
    echo "$(grep -c "	1	" "$TDIR/artifacts.tsv") test binaries, listed in $TDIR/artifacts.tsv ($rustc_version, $KIND)"
    exit 0
fi
tree=$TMPDIR/holt-src
rm -rf "$tree"; mkdir -p "$tree"
tar -C "$SRC" -cf - Cargo.toml Cargo.lock LICENSE src tests | tar -C "$tree" -xf -
echo "$rustc_version, $clang_version, $KIND"
if ! (cd "$tree" && RUSTFLAGS="--remap-path-prefix=$tree=/holt${flags[*]:+ ${flags[*]}}" CARGO_TARGET_DIR=$TMPDIR/holt-target \
        nice "$cargo" build --release $offline --locked "${zstd[@]}" --bin bko-holt --target "$TARGET") >"$LOGS/build.log" 2>&1; then
    grep -E "^(error|warning)" -A 6 "$LOGS/build.log" | head -80 >&2
    exit 1
fi
b=$TMPDIR/holt-target/$TARGET/release/bko-holt
mkdir -p "$OUT"
check_out "$OUT" "$PLATFORM"   # (again, just before the write)
cp "$b" "$OUT/bko-holt.part"
# OpenBSD's lld links amd64 code execute-only (E, the VM build's); upstream lld can't, so the
# code segment is marked so here, once it is known to hold code alone (holt_e2.py xonly).
if [[ $PLATFORM == openbsd ]] && ! python3 -I "$HERE/holt_e2.py" xonly "$OUT/bko-holt.part" >>"$LOGS/build.log"; then
    rm -f "$OUT/bko-holt.part"; tail -n 1 "$LOGS/build.log" >&2; exit 1
fi
chmod 755 "$OUT/bko-holt.part"
mv "$OUT/bko-holt.part" "$OUT/bko-holt"
sha=$(sha256sum "$OUT/bko-holt" | cut -d' ' -f1)
if [[ -n ${HOLT_INFO:-} ]]; then
    python3 -I -c 'import json, socket, sys, time
p, sha, rustc, src, out, kind, tc, target, sysroot, vm, image, clang, lld, tier = sys.argv[1:15]
print(json.dumps({"platform": p, "sha256": sha, "rustc": rustc, "clang": clang, "lld": lld,
                  "host": socket.gethostname().split(".")[0],
                  "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "src": src, "out": out,
                  "kind": "cross", "build": kind, "toolchain": tc, "tier": int(tier), "target": target, "sysroot": sysroot,
                  "sysroot_guest": vm, "sysroot_image": image}, sort_keys=True))' \
        "$PLATFORM" "$sha" "$rustc_version" "$SRC" "$OUT/bko-holt" "$KIND" "$TC" "$TARGET" "$SYSROOT" "$VM" \
        "${image#"$WORK"/}" "$clang_version" "$lld_version" "$TIER" >"$HOLT_INFO.part" && mv -f "$HOLT_INFO.part" "$HOLT_INFO"
fi
ls -l "$OUT/bko-holt"
