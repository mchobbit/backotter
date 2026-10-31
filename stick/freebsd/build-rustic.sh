#!/usr/bin/env bash
# Build BackOtter's rustic for FreeBSD, natively, in a throwaway copy of the
# freebsd15 test guest (testvm/make-freebsd.sh; not the long-lived
# freebsd15-builder, which must keep running for the images): branch
# "backotter" of github.com/mchobbit/rustic, or local trees (SRC), with Rust,
# cmake, gmake, perl and pkgconf from FreeBSD's packages.
#
# Usage: build-rustic.sh [--setup] [PORT]
#
# Writes $BKO_BIN/freebsd/rustic-bko and rustic-commit (BKO_BIN defaults to
# ~/backotter-work/bko-bin), where make-img-bsd.sh freebsd picks it up.  PORT
# (default 2901) is the guest's SSH port here.  SRC, FAST, CPUS, MEMORY and
# --setup (a build image with the tools installed) are described in
# ../build-rustic-native.sh, which does the work.  About 20 minutes on 4 CPUs
# with FAST=1.
#
# Needs: qemu-system-x86_64 with KVM, testvm/vm-run, the freebsd15 guest in
# ~/backotter-work/vms, git, rsync, ssh.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
PLATFORM=freebsd VM=freebsd15 DEFAULT_PORT=2901
GUEST_PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
install_tools() {
    vm_ssh 'env ASSUME_ALWAYS_YES=yes pkg install -y rust cmake-core perl5 gmake pkgconf'
}
# shellcheck source=../build-rustic-native.sh
. "$here/../build-rustic-native.sh"
