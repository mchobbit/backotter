#!/usr/bin/env bash
# Build BackOtter's rustic for FreeBSD, natively, in a throwaway copy of the
# freebsd14 test guest (testvm/make-freebsd.sh; not the long-lived
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
# FreeBSD 14, not 15: a binary links each libc function at the newest
# symbol version of the libc it is built against, and FreeBSD 15's
# (setgroups@FBSD_1.8, posix_spawn_file_actions_addchdir@FBSD_1.9) aren't in
# 14, where such a binary doesn't start.  One built on 14 runs on 14 and 15
# (15 keeps the older versions); the libc crate takes FreeBSD 12's ABI
# either way.
#
# Needs: qemu-system-x86_64 with KVM, testvm/vm-run, the freebsd14 guest in
# ~/backotter-work/vms, git, rsync, ssh.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
PLATFORM=freebsd VM=freebsd14 DEFAULT_PORT=2901
GUEST_PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin
install_tools() {
    vm_ssh 'env ASSUME_ALWAYS_YES=yes pkg install -y rust cmake-core perl5 gmake pkgconf'
}
# shellcheck source=../build-rustic-native.sh
. "$here/../build-rustic-native.sh"
