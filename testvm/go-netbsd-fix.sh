# shellcheck shell=bash
# Sourced (bash 3.2 or newer): is a Go toolchain one that builds a safe
# restic-bko for NetBSD?
#
# golang/go#80285: NetBSD amd64 (seen under QEMU/KVM on AMD Zen2, Zen3 and a
# Ryzen 7 255) doesn't keep the upper halves of the YMM registers across a
# signal's delivery.  Go's async preemption signals land inside its AVX
# memmove, so restic-bko for NetBSD silently stored wrong bytes: a blob hashed
# over corrupted data, which restic check --read-data passes (the alpinebsd
# failure of 2026-11-15, t2s-20261115T1944-05fb; reproduced 4 in 1304 runs
# with rel9's restic-bko, go1.25.10, and 0 in 2796 with rel16's, go1.26.10).
# Go's fix, internal/cpu/cpu_x86_netbsd.go, turns AVX and up off on NetBSD:
# go1.26.8 on the 1.26 line (d1434db) and go1.27.0; no go1.25.x has it.
#
#   go_ver_of TEXT            the first goN.M[.P] word of TEXT, as `go version`
#                             and `go version FILE` print it ("go version
#                             go1.26.10 linux/amd64", "FILE: go1.26.10");
#                             exit 1 when there is none (a prerelease such as
#                             go1.27rc1, a devel build, no Go)
#   go_netbsd_avx_fixed TEXT  exit 0 when TEXT's Go has the fix, 1 when it
#                             hasn't, 2 when TEXT names no Go release
#   GO_NETBSD_FIX_RULE        the rule, for messages
#
# testvm/go-netbsd-fix-test.sh tests them.

GO_NETBSD_FIX_RULE="go1.26.8 or newer on the 1.26 line, or go1.27.0 or newer (golang/go#80285)"

go_ver_of() {
    local w
    while read -r w; do
        w=${w%[:,;]}
        if [[ $w =~ ^go[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
            echo "$w"
            return 0
        fi
    done < <(printf '%s\n' "$1" | tr -s ' \t' '\n\n')
    return 1
}

go_netbsd_avx_fixed() {
    local v maj min pat
    v=$(go_ver_of "$1") || return 2
    v=${v#go}
    maj=${v%%.*} v=${v#*.}
    min=${v%%.*}
    if [[ $v == *.* ]]; then pat=${v#*.}; else pat=0; fi
    if ((10#$maj != 1)); then ((10#$maj > 1)) && return 0; return 1; fi
    ((10#$min >= 27)) && return 0
    ((10#$min == 26 && 10#$pat >= 8)) && return 0
    return 1
}
