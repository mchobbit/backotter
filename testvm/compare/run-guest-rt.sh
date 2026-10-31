#!/usr/bin/env bash
# Run a guest's metadata round trip (testvm/compare/OS/roundtrip.sh) in all
# four directions, each in its own throwaway (--ram) copy of the guest:
# restic->restic, rustic->rustic, restic->rustic, rustic->restic (backed up
# with the first, restored with the second).  Prints PASS or FAIL per
# direction, with the lines of every check that failed.
#
# Usage: run-guest-rt.sh GUEST [PORT] RESTIC_BIN RUSTIC_BIN [DIRECTION ...]
#   GUEST       freebsd15 freebsd15-ufs openbsd8 netbsd11 alpinebsd dragonfly6 omnios58
#   PORT        the guest's SSH port here (default 2710)
#   RESTIC_BIN  restic-bko built for the guest's OS (bko-bin/OS/restic-bko)
#   RUSTIC_BIN  rustic-bko built for it (stick/OS/build-rustic.sh; alpinebsd
#               runs the NetBSD one)
#   DIRECTION   restic-restic rustic-rustic restic-rustic rustic-restic (default: all)
#
# omnios58: after each direction, its repository goes into a second fresh
# boot and is restored there again ("again" in the summary): the ephemeral
# ids of SID owners are handed out anew in every boot.
# alpinebsd has no mtree, chflags, stat, getfacl or *extattr: NetBSD's go in
# with it (/root/nbtools), from ~/backotter-work/netbsd-tools, which is
# fetched from a throwaway netbsd11 (on the same PORT) the first time.
#
# Writes logs to ~/backotter-work/rt/GUEST/DIRECTION.log (RT_LOGS replaces
# ~/backotter-work/rt), and DIRECTION-again.log; omnios58's repositories go
# to DIRECTION.tar there.  ~/backotter-work is BKO_WORK, by default the
# directory that holds this testvm/, as for compare.py.  MEMORY (MB, default
# 4096) is the guest's; RT_TIMEOUT (seconds, default 3600) bounds each run.
#
# Needs: qemu-system-x86_64 with KVM, testvm/vm-run, the guests in
# ~/backotter-work/vms, ssh, tar.

# No -e: a direction that fails mustn't stop the others.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$(cd "$here/../.." && pwd)}
die() { echo "run-guest-rt: $*" >&2; exit 2; }
# shellcheck source=../vm-lib.sh
. "$here/../vm-lib.sh"

[[ $# -ge 3 ]] || { sed -n '2,15p' "$0" >&2; exit 2; }
guest=$1; shift
port=2710
if [[ $1 =~ ^[0-9]+$ ]]; then port=$1; shift; fi
[[ $# -ge 2 ]] || die "usage: run-guest-rt.sh GUEST [PORT] RESTIC_BIN RUSTIC_BIN [DIRECTION ...]"
[[ -f $1 && -f $2 ]] || die "no $1 or no $2"
restic=$(realpath "$1") rustic=$(realpath "$2"); shift 2
directions=("$@")
[[ $# -gt 0 ]] || directions=(restic-restic rustic-rustic restic-rustic rustic-restic)
logs=${RT_LOGS:-$WORK/rt}/$guest
tools=$WORK/netbsd-tools
mkdir -p "$logs"
case $guest in
    freebsd*) os=freebsd ;;
    openbsd*) os=openbsd ;;
    netbsd*|alpinebsd) os=netbsd ;;
    dragonfly*) os=dragonfly ;;
    omnios*) os=illumos ;;
    *) die "no round trip for $guest" ;;
esac
files=("$here/$os/roundtrip.sh" "$here/rt-common.sh")
[[ $os == freebsd ]] && files+=("$here/freebsd/torture.sh" "$here/freebsd/fsmeta.sh")
boot() {  # GUEST: a throwaway copy of it, up and answering ssh
    vm_boot "$1" "$port" "$logs/vm" --ram --memory "${MEMORY:-4096}"
}
trap vm_halt EXIT

if [[ $guest == alpinebsd && ! -x $tools/mtree ]]; then
    echo "== fetching NetBSD's tools from netbsd11" >&2
    boot netbsd11 || exit 1
    rm -rf "$tools.part" && mkdir -p "$tools.part"
    vm_ssh 'cd /root && rm -rf nbtools && mkdir nbtools && cp /usr/sbin/mtree /usr/bin/stat /usr/bin/chflags /bin/chmod /bin/setfacl /bin/getfacl /usr/bin/setextattr /usr/bin/getextattr /usr/bin/lsextattr /usr/bin/rmextattr /usr/bin/md5 /usr/bin/sha256 /bin/ls /usr/bin/touch nbtools/ && tar -cf - nbtools' |
        tar -C "$tools.part" --strip-components=1 -xf - || die "couldn't fetch NetBSD's tools"
    vm_halt
    rm -rf "$tools" && mv "$tools.part" "$tools"
fi

# DIRECTION AGAIN: one run; AGAIN restores the repository saved by the
# run before, in a fresh boot.  Its output goes to the log.
one() {
    local dir=$1 again=$2 b r f
    b=${dir%-*} r=${dir#*-}
    boot "$guest" || return 1
    vm_put "$restic" /root/restic-bko && vm_put "$rustic" /root/rustic-bko || return 1
    for f in "${files[@]}"; do vm_put "$f" "/root/$(basename "$f")" || return 1; done
    if [[ $guest == alpinebsd ]]; then
        tar -C "$tools" -cf - . | T=600 vm_ssh 'mkdir -p /root/nbtools && cd /root/nbtools && tar -xf -' || return 1
    fi
    vm_ssh "/root/restic-bko version; /root/rustic-bko --version" 2>&1
    # The scripts' stderr joins stdout in the guest: ssh would mix the two
    # out of order.
    if [[ -n $again ]]; then
        T=600 vm_ssh 'cd / && tar -xf -' <"$logs/$dir.tar" || return 1
        # The source tree isn't there: only the restore and the comparison.
        T=${RT_TIMEOUT:-3600} vm_ssh "cd /root && ./roundtrip.sh --again /root/$r-bko 2>&1" 2>&1
    else
        T=${RT_TIMEOUT:-3600} vm_ssh "cd /root && ./roundtrip.sh /root/$b-bko /root/$r-bko 2>&1" 2>&1
        if [[ $os == illumos ]]; then
            T=600 vm_ssh 'cd / && tar -cf - root/bko-rt-repo tmp/bko-rt.before*' >"$logs/$dir.tar"
        fi
    fi
    vm_halt
}

# The lines of each failed check: the sections a "roundtrip: FAIL: a; b"
# line names, from their "== a" header to the next one.
failures() {  # LOG
    awk '/^roundtrip: FAIL: / { n = split(substr($0, 18), f, "; "); for (i = 1; i <= n; i++) bad["== " f[i]] = 1 }
         { lines[NR] = $0 }
         END { for (i = 1; i <= NR; i++) {
                   if (lines[i] ~ /^== /) show = (lines[i] in bad)
                   if (show) print "    " lines[i] } }' "$1"
}

summary=()
for dir in "${directions[@]}"; do
    case $dir in restic-restic|rustic-rustic|restic-rustic|rustic-restic) ;;
        *) die "no such direction: $dir" ;; esac
    runs=("$dir")
    [[ $os == illumos ]] && runs+=("$dir-again")
    for run in "${runs[@]}"; do
        echo "== $guest $run" >&2
        again=""; [[ $run == *-again ]] && again=1
        one "$dir" "$again" >"$logs/$run.log" 2>&1
        vm_halt
        verdict=$(grep -a '^roundtrip: ' "$logs/$run.log" | tail -n 1)
        case $verdict in
            "roundtrip: PASS") summary+=("PASS  ${run/-/->}") ;;
            "roundtrip: FAIL"*) summary+=("FAIL  ${run/-/->}: ${verdict#roundtrip: FAIL: }"
                                          "$(failures "$logs/$run.log")") ;;
            *) summary+=("FAIL  ${run/-/->}: no verdict (see $logs/$run.log)"
                         "$(tail -n 15 "$logs/$run.log" | sed 's/^/    /')") ;;
        esac
    done
done
echo "== $guest: $restic, $rustic"
printf '%s\n' "${summary[@]}" | grep -v '^$'
