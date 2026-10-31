#!/usr/bin/env bash
# Build BackOtter's rustic for OpenBSD, natively, in a throwaway copy of the
# openbsd8 test guest (testvm/make-openbsd.sh, platform.conf's BUILD_VM):
# branch "backotter" of github.com/mchobbit/rustic, or local trees (SRC),
# with Rust, cmake and gmake from OpenBSD's packages.
#
# Usage: build-rustic.sh [--setup] [PORT]
#
# Writes $BKO_BIN/openbsd/rustic-bko and rustic-commit (BKO_BIN defaults to
# ~/backotter-work/bko-bin), where make-img-bsd.sh openbsd picks it up.  PORT
# (default 2902) is the guest's SSH port here.  SRC, FAST, CPUS, MEMORY and
# --setup (a build image with the tools installed) are described in
# ../build-rustic-native.sh, which does the work.  About 30 minutes on 4 CPUs
# with FAST=1.
#
# Needs: qemu-system-x86_64 with KVM, testvm/vm-run, the openbsd8 guest in
# ~/backotter-work/vms, git, rsync, ssh.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
BUILD_VM_OPTIONS=()
# shellcheck source=platform.conf
. "$here/platform.conf"
PLATFORM=openbsd VM=$BUILD_VM DEFAULT_PORT=2902
GUEST_PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
install_tools() {
    vm_ssh 'pkg_add -I rust cmake gmake'
}
# shellcheck source=../build-rustic-native.sh
. "$here/../build-rustic-native.sh"
