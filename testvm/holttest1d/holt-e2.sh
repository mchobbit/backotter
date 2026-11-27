#!/usr/bin/env bash
# E2 steps 0-2 for one platform (docs/design/img-speed.md §8.2; I10): a cross-built
# bko-holt (holt-cross.sh) against the VM build of the same code (build-holt.sh), on
# a KVM test host, as m (freebsd, netbsd and illumos: I10; openbsd and dragonfly: I11):
#
#   holt-e2.sh PLATFORM VM_BIN CROSS_BIN OUT          (TMPDIR: a directory in the RAM disk)
#   holt-e2.sh --reserved NAME PLATFORM VM_BIN CROSS_BIN OUT
#             the same under cmp-run.sh --reserve HOLT_RESERVE M (default 3; M the guest's
#             GiB and 2: its 4 GiB, illumos's 8, whose fork and exec reserve swap for a
#             child's whole address space, so a 4 GiB guest's native test link fails with
#             ENOMEM), TMPDIR /mnt/bko-ram/t2e-NAME-PLATFORM, made before and removed after
#
#   step 0  holt_e2.py elf: the two ELF files' shapes (and the sysroot's symbol versions)  OUT/step0.txt
#   step 1  both binaries' `--version --json` in the guest, holt_e2.py api  OUT/step1.txt
#   step 2  holt's tests twice in the same guest boot, from one copy of the tree at the
#           path both builds name (TMPDIR/e2/, their CARGO_MANIFEST_DIR): cross-built here
#           (holt-cross.sh --tests, its executables put at the paths they were built at),
#           and built natively there by the guest's own Rust (cargo test --no-run), each
#           test binary run as root with --test-threads=2; holt_e2.py tests compares
#           them (OUT/cross/ and OUT/native/: tests.tsv NAME RC SECS, NAME.log)  OUT/step2.txt
# The guest: a throwaway (-snapshot) copy of the image the VM build builds in,
# WORK/vms-bko-build/VM-rust.qcow2 (the test guest freebsd14, netbsd11, omnios58 or openbsd8
# with Rust and a C compiler), else of the test guest (dragonfly6, which has no build image), given Rust as build-holt.sh gives it
# (network).  OUT/e2.txt: the three verdict lines.  Exit 0 when all three PASS, 1 when one
# fails, 2 refused.  HOLT_SRC (the holt tree, beside a docs/design/holt-json.md),
# HOLT_CROSS_CACHE and HOLT_RUST_TOOLCHAIN go to holt-cross.sh.  The guest takes a port
# of 2600-2689 under ht1-queue.sh's /tmp/bko-ht1-ports locks.  Step 3 (HoltTest2 side by
# side) runs from the Mac: holt_e2.py fetch and dumps (the module's docstring).
set -uo pipefail

WORK=${BKO_WORK:-/home/m/backotter-work}
HERE=$(dirname "$(realpath "$0")")
die() { echo "holt-e2: $*" >&2; exit "${2:-2}"; }

if [[ ${1:-} == --reserved ]]; then
    [[ $# -eq 6 ]] || die "--reserved NAME PLATFORM VM_BIN CROSS_BIN OUT"
    name=$2; shift 2
    [[ $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "NAME '$name': a plain name"
    r=${HOLT_RAM_ROOT:-/mnt/bko-ram}
    mountpoint -q "$r" || die "no RAM disk at $r"
    # (Absolute paths: the reservation's command doesn't run in this directory.)
    set -- "$1" "$(realpath -m -- "$2")" "$(realpath -m -- "$3")" "$(realpath -m -- "$4")"
    tmp=$r/t2e-$name-$1 rc=0 mem=6
    [[ $1 != illumos ]] || mem=10
    finish() { rm -rf --one-file-system "$tmp"; exit "$1"; }
    trap 'finish 143' TERM; trap 'finish 130' INT; trap 'finish 129' HUP
    rm -rf --one-file-system "$tmp"; mkdir -p "$tmp" || finish 2
    WEAR_TAG=e2:$name:$1 "${HOLT_CMP_RUN:-$WORK/cmp-run.sh}" --reserve "${HOLT_RESERVE:-3}" "$mem" \
        env TMPDIR="$tmp" "$HERE/holt-e2.sh" "$@" || rc=$?
    finish "$rc"
fi

[[ $# -eq 4 ]] || die "usage: holt-e2.sh [--reserved NAME] PLATFORM VM_BIN CROSS_BIN OUT"
PLATFORM=$1 VMB=$(realpath -- "$2") XB=$(realpath -- "$3") OUT=$4
GUEST_PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/opt/ooce/bin:/usr/gnu/bin
MEM=4096
case $PLATFORM in   # (build-holt.sh's guests and Rust installs)
    freebsd) VM=freebsd14 RUST='env ASSUME_ALWAYS_YES=yes pkg install -y rust' ;;
    netbsd) VM=netbsd11 RUST='pkg_info -e rust >/dev/null || pkgin -y install rust' ;;
    illumos) VM=omnios58 RUST='pkg install -q ooce/developer/rust developer/gcc14; s=$?; [ $s -eq 0 ] || [ $s -eq 4 ]' MEM=8192 ;;
    openbsd) VM=openbsd8 RUST='pkg_add -I rust' ;;
    dragonfly) VM=dragonfly6 RUST='export ASSUME_ALWAYS_YES=yes; pkg upgrade -y && pkg install -y rust && cargo --version' ;;
    *) die "PLATFORM: freebsd, netbsd, illumos, openbsd or dragonfly" ;;
esac
[[ -f $VMB && -f $XB ]] || die "no $VMB or $XB"
[[ -n ${TMPDIR:-} && -d $TMPDIR ]] || die "TMPDIR must name a directory (in the RAM disk)"
mkdir -p "$OUT/cross" "$OUT/native" || die "can't make $OUT"
OUT=$(realpath -- "$OUT")
E2PY=$HERE/holt_e2.py
: >"$OUT/e2.txt"
verdict() { tail -n 1 "$1" | tee -a "$OUT/e2.txt"; }
fail_rest() {  # WHY: steps 1 and 2 fail with it
    echo "E2 step 1 $PLATFORM: FAIL ($1)" | tee "$OUT/step1.txt" >>"$OUT/e2.txt"
    echo "E2 step 2 $PLATFORM: FAIL ($1)" | tee "$OUT/step2.txt" >>"$OUT/e2.txt"
    exit 1
}

# --- step 0 ----------------------------------------------------------------------------
# (the cross build's sysroot, holt-cross.sh's cache: the symbol versions its libraries define)
sr=${HOLT_CROSS_CACHE:-$WORK/holt-cross}/sysroot/$VM
[[ -f $sr/.digest ]] || sr=""
python3 -I "$E2PY" elf "$PLATFORM" "$VMB" "$XB" ${sr:+"$sr"} >"$OUT/step0.txt" 2>&1
verdict "$OUT/step0.txt"

# --- step 2's cross-built tests first (no guest needed) ------------------------------------
trc=0
"$HERE/holt-cross.sh" --tests "$PLATFORM" "$OUT/testbins" >"$OUT/tests-build.log" 2>&1 || trc=$?

# --- the guest ---------------------------------------------------------------------------
VM_LIB=$(dirname "$HERE")/vm-lib.sh; [[ -f $VM_LIB ]] || VM_LIB=$WORK/testvm/vm-lib.sh
# shellcheck source=../vm-lib.sh
. "$VM_LIB"
port="" plk=""
mkdir -p /tmp/bko-ht1-ports
for p in $(seq 2600 2689); do
    [[ -z $(ss -Hltn "sport = :$p" 2>/dev/null) ]] || continue
    exec {plk}>>"/tmp/bko-ht1-ports/$p.lock" || continue
    if flock -n "$plk"; then port=$p; break; fi
    exec {plk}>&-; plk=""
done
[[ -n $port ]] || die "no free port in 2600-2689" 1
trap vm_halt EXIT
image=$WORK/vms-bko-build/$VM-rust.qcow2 key=$WORK/vms-bko-build/$VM.id_ed25519
if [[ -f $image && -f $key ]]; then
    VM_IMAGE=$image VM_IMAGE_KEY=$key vm_boot "$VM" "$port" "$OUT" --snapshot --cpus 4 --memory "$MEM" ||
        fail_rest "the $VM build guest didn't come up"
else
    vm_boot "$VM" "$port" "$OUT" --snapshot --cpus 4 --memory "$MEM" || fail_rest "the $VM guest didn't come up"
fi
env="PATH=$GUEST_PATH; export PATH"
if ! vm_ssh "$env; command -v cargo" >/dev/null 2>&1; then
    vm_ssh "$env; $RUST" >"$OUT/rust-install.log" 2>&1 || fail_rest "no Rust in the guest ($OUT/rust-install.log)"
fi
vm_ssh "$env; rustc --version" >"$OUT/native-rustc.txt" 2>&1
G=/tmp/bko-e2
vm_ssh "rm -rf $G && mkdir -p $G"
vm_put "$VMB" "$G/vm-bko-holt"
vm_put "$XB" "$G/cross-bko-holt"

# --- step 1 -----------------------------------------------------------------------------
vm_ssh "$G/vm-bko-holt --version --json" </dev/null >"$OUT/api-vm.json" 2>"$OUT/api-vm.err"
vm_ssh "$G/cross-bko-holt --version --json" </dev/null >"$OUT/api-cross.json" 2>"$OUT/api-cross.err"
python3 -I "$E2PY" api "$OUT/api-vm.json" "$OUT/api-cross.json" "$PLATFORM" >"$OUT/step1.txt" 2>&1
verdict "$OUT/step1.txt"

# --- step 2 -----------------------------------------------------------------------------
run_tests() {  # ARTIFACTS.tsv DIR: each test binary (NAME 1 PATH) run in the tree, DIR/NAME.log
    local name test path s rc
    : >"$2/tests.tsv"
    # (fd 3: vm_ssh's ssh reads stdin, and would eat the list)
    while IFS=$'\t' read -r -u 3 name test path; do
        [[ $test == 1 ]] || continue
        s=$SECONDS rc=0
        T=3600 vm_ssh "cd $tree && $path --test-threads=2 2>&1" </dev/null >"$2/$name.log" 2>&1 || rc=$?
        printf '%s\t%s\t%s\n' "$name" "$rc" "$((SECONDS - s))" >>"$2/tests.tsv"
    done 3<"$1"
}
if [[ $trc -ne 0 || ! -s $OUT/testbins/artifacts.tsv ]]; then
    { tail -n 20 "$OUT/tests-build.log"; echo "E2 step 2 $PLATFORM: FAIL (holt-cross.sh --tests exit $trc)"; } >"$OUT/step2.txt"
else
    tree=$(cat "$OUT/testbins/tree")
    top=$(dirname "$tree")   # TMPDIR/e2: holt/ and docs/design/holt-json.md
    vm_ssh "mkdir -p $top"
    # (its entries, not "./": DragonFly's tar won't extract over the existing directory)
    (cd "$top" && tar -cf - -- *) | T=1200 vm_ssh "cd $top && tar -xf -"
    # The cross-built executables where they were built (the tests run bko-holt by that path).
    while IFS=$'\t' read -r -u 3 _ _ path; do
        vm_ssh "mkdir -p $(dirname "$path")" </dev/null
        vm_put "$path" "$path"
    done 3<"$OUT/testbins/artifacts.tsv"
    run_tests "$OUT/testbins/artifacts.tsv" "$OUT/cross"
    # The same tests built natively in this guest, from the same tree.
    if T=3600 vm_ssh "$env; cd $tree && CARGO_TARGET_DIR=$G/native-target cargo test --no-run --offline --message-format=json-render-diagnostics" \
            </dev/null >"$OUT/native.json" 2>"$OUT/native-build.log"; then
        python3 -I "$E2PY" artifacts "$OUT/native.json" >"$OUT/native/artifacts.tsv"
        run_tests "$OUT/native/artifacts.tsv" "$OUT/native"
    else
        echo "native build failed: $OUT/native-build.log" >"$OUT/native/failed"
    fi
    python3 -I "$E2PY" tests "$OUT/native" "$OUT/cross" "$PLATFORM" >"$OUT/step2.txt" 2>&1
fi
verdict "$OUT/step2.txt"
! grep -q ': FAIL' "$OUT/e2.txt"
