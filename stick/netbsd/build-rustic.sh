#!/usr/bin/env bash
# Build BackOtter's rustic for NetBSD (and AlpineBSD, whose libc is NetBSD's:
# the binary runs there unchanged), natively, in a throwaway copy of the
# netbsd11 test guest: branch "backotter" of github.com/mchobbit/rustic
# (whose Cargo.lock pins rustic_core's commit), or local trees (SRC), with
# Rust from pkgsrc's binary packages and the comp set (compiler and linker)
# from the release.
#
# Usage: build-rustic.sh [--setup] [PORT]
#
# Writes $BKO_BIN/netbsd/rustic-bko and rustic-commit (BKO_BIN defaults to
# ~/backotter-work/bko-bin), where make-img-bsd.sh netbsd picks it up.  PORT
# (default 2412) is the guest's SSH port here.  The comp set comes from
# platform.conf's fetch, so it's checked against the release's signed hash
# list.  SRC, FAST, CPUS and MEMORY (here 8 and 8192 by default) and --setup
# are described in ../build-rustic-native.sh, which does the work.  Takes
# about half an hour on 8 CPUs, plus about 10 minutes to install the tools
# unless --setup made a build image (vms-bko-build/netbsd11-rust.qcow2) that
# has them.
#
# Needs: what make-img-bsd.sh needs, and git and rsync.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
BUILD_VM_OPTIONS=()
# shellcheck source=platform.conf
. "$here/platform.conf"
PLATFORM=netbsd VM=$BUILD_VM DEFAULT_PORT=2412
CPUS=${CPUS:-8} MEMORY=${MEMORY:-8192}
GUEST_PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/pkg/sbin:/usr/pkg/bin
PKGSRC=https://cdn.netbsd.org/pub/pkgsrc/packages/NetBSD/x86_64/11.0_2026Q3/All
install_tools() {
    local media_dir=$WORK/img-media/netbsd
    mkdir -p "$media_dir"
    EXTRA_SETS=comp fetch "$media_dir"
    # libfetch tries IPv6 first, which QEMU's user network can't reach, and the
    # guest has no TLS trust anchors until certctl sets them up.
    vm_sh "PATH=$GUEST_PATH; export PATH
grep -q '^ip6addrctl_policy=ipv4_prefer' /etc/rc.conf ||
    printf 'ip6addrctl=YES\nip6addrctl_policy=ipv4_prefer\n' >>/etc/rc.conf
/etc/rc.d/ip6addrctl start >/dev/null && certctl rehash"
    T=1200 vm_ssh 'cd / && tar -xpJf -' <"$media_dir/comp.tar.xz"
    vm_ssh "PATH=$GUEST_PATH; export PATH; pkg_info -e rust >/dev/null || PKG_PATH=$PKGSRC pkg_add rust cmake gmake perl pkgconf"
}
# shellcheck source=../build-rustic-native.sh
. "$here/../build-rustic-native.sh"
