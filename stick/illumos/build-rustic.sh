#!/usr/bin/env bash
# Build BackOtter's rustic for illumos, natively, in a throwaway copy of the
# omnios58 test guest (testvm/make-omnios.sh): branch "backotter" of
# github.com/mchobbit/rustic, or local trees (SRC), with Rust, gcc 14, cmake
# and GNU make from OmniOS's packages (Rust and cmake from the ooce
# publisher).  The binary goes into the illumos rescue image (make-img-bsd.sh
# illumos) and to testvm/compare/illumos/roundtrip.sh, through
# testvm/compare/run-guest-rt.sh.
#
# Usage: build-rustic.sh [--setup] [PORT]
#
# Writes $BKO_BIN/illumos/rustic-bko and rustic-commit (BKO_BIN defaults to
# ~/backotter-work/bko-bin).  PORT (default 2903) is the guest's SSH port
# here.  SRC, FAST, CPUS, MEMORY and --setup (a build image with the tools
# installed) are described in ../build-rustic-native.sh, which does the work.
# About 20 minutes on 4 CPUs with FAST=1.  The guest's network needs the
# legacy virtio-net (--legacy-virtio-net, in its vm-run-options; added if
# missing).
#
# Needs: qemu-system-x86_64 with KVM, testvm/vm-run, the omnios58 guest in
# ~/backotter-work/vms, git, rsync, ssh.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
PLATFORM=illumos VM=omnios58 DEFAULT_PORT=2903
GUEST_PATH=/sbin:/usr/sbin:/bin:/usr/bin:/opt/ooce/bin:/opt/gcc-14/bin
install_tools() {
    # pkg's exit status 4 is "nothing to do".
    vm_ssh 'pkg install -q ooce/developer/rust developer/gcc14 developer/build/gnu-make ooce/developer/cmake; s=$?; [ $s -eq 0 ] || [ $s -eq 4 ]'
}
# shellcheck source=../build-rustic-native.sh
. "$here/../build-rustic-native.sh"
