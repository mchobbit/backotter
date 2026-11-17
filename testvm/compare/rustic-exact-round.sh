#!/usr/bin/env bash
# rustic-exact-round.sh: the paused rustic-bko round, driven from the Mac.
# The Mac's ~/git/rustic_core (0.13.0+backotter.15 over ba93d6e) and
# ~/git/rustic (0.11.4+backotter.16 over c517a69) hold the fixes: restore
# --delete leaves a case variant of a planned entry, a kept hard-link group's
# later names are in the way, and restore_contents doesn't panic when a
# planned file is gone (.14); on NetBSD an atime the restore's own reads
# moved is put back alone (.15: restore/exact.rs, backend/osattrs.rs).
# Either uncommitted (rustic_core at ba93d6e, rustic at c517a69) or
# committed over those (rustic_core ad51297, rustic 7625a3c, both clean;
# rustic's Cargo.lock still pins ba93d6e).  Now all committed: rustic_core
# 3fd3921 and rustic 2c3b237, whose Cargo.lock pins it (step 3 finds that
# done already).  This builds and tests the trees as they are on the farm,
# collects the NetBSD trace of test_restore_exact_twice_changes_nothing,
# commits what isn't committed (at least rustic's Cargo.lock), and runs the
# round again from the commits.
#
# Run it on the Mac, from the checkout (never on a farm host):
#
#   cd ~/git/backotter
#   testvm/compare/rustic-exact-round.sh --dry-run        # every step's commands, printed; nothing run
#   testvm/compare/rustic-exact-round.sh                  # steps 1-4, stopping at the first that fails
#   testvm/compare/rustic-exact-round.sh --steps 1        # build + test the trees (HEAD + any changes)
#   testvm/compare/rustic-exact-round.sh --steps 2        # NetBSD (and FreeBSD) ktrace diagnosis
#   testvm/compare/rustic-exact-round.sh --steps 3        # commit (takes the newest step 1 pass)
#   testvm/compare/rustic-exact-round.sh --steps 4        # the farm round from the commits
#   testvm/compare/rustic-exact-round.sh --steps 3,4 --log-dir ~/git/bko-rustic-round-STAMP
#   testvm/compare/rustic-exact-round.sh --dry-run --steps 4    # one step's commands
#
# Steps (--steps LIST, default 1,2,3,4; 1 and 2 run at once when both are
# chosen, and are judged in order):
#
#   1  Build and test.  On a host testvm/pick-host chooses (--host), the Mac's
#      two trees as they are, HEAD plus any uncommitted changes (rsync, without
#      target/ and .git; the log says each HEAD and whether it is clean), go to
#      ~/backotter-work/RUN/src; a copy is pointed at the local rustic_core by
#      stick/prep-rustic-src.sh (as the SRC= builds are), and rustic's
#      build-backotter.sh builds the Linux rustic-bko (rustup's 1.94.1 in
#      ~/.cargo/bin, release, x86_64-unknown-linux-musl).  Then cargo test -p
#      rustic_core as the user, the restore unit and integration tests as root
#      (sudo -n) with TMPDIR on a tmpfs and on a loop btrfs, and exact-rt.sh
#      as root with the new binary, each under cmp-run.sh --reserve.  The
#      binary and the logs come back to LOG_DIR/step1/, and LOG_DIR/step1.pass
#      records the trees' digest on a pass.
#   2  NetBSD diagnosis (not a fix).  In a throwaway copy of netbsd11's build
#      image (vms-bko-build/netbsd11-rust.qcow2, stick/netbsd/build-rustic.sh
#      --setup's), rustic_core's integration test binary is built from the same
#      trees, twice: as it is, and with a hook in
#      test_restore_exact_twice_changes_nothing (in the guest's copy only) that
#      runs stat -f (birth, change, modify and access times in ns, flags,
#      inode) on the test's files after each restore.  Both run under
#      ktrace -i -t cn, with TMPDIR on the root FFS and on /tmp; kdump -T gives
#      the second --exact restore's lines (from the test's 20 ms sleep, or
#      between the hook's two marks) and, of those, the calls that change
#      metadata (utimensat, futimens, chmod, chown, chflags, extattr, open,
#      write, rename, unlink, ...).  The same in freebsd15-ufs, its binaries
#      built in freebsd14's build image.  The output lands in
#      LOG_DIR/step2/OS/out/; the step's outcome is "diagnosis collected", never
#      a PASS or FAIL of the test (it failed on NetBSD with rustic_core .13
#      and .14; the trace found why, and .15 fixes it: it passes there now).
#   3  Commit, only after step 1 passed (LOG_DIR/step1.pass with the same
#      trees' digest; --force-commit skips that).  rustic_core, one of:
#      (a) HEAD ba93d6e with exactly the eight files changed (restore.rs,
#      restore/files_writer.rs, restore/exact.rs, backend/osattrs.rs,
#      tests/integration/restore.rs, BACKOTTER.md, Cargo.lock,
#      crates/core/Cargo.toml): committed in two commits (the fixes and
#      tests; then the version and BACKOTTER.md); a later run that finds
#      those two commits goes on with rustic.  (b) A clean HEAD that
#      descends from ba93d6e and whose diff from it touches only those eight
#      files (ad51297, now 3fd3921): no rustic_core commit.  rustic, one
#      of: HEAD c517a69 with exactly BACKOTTER.md, Cargo.toml and
#      build-backotter.sh changed (those and Cargo.lock are committed), or
#      a clean HEAD that descends from c517a69 and changes only those three
#      (7625a3c: Cargo.lock alone is committed; now 2c3b237, its lock commits
#      on top, "done already").  Anything else is refused.  Cargo.lock must take rustic_core's
#      HEAD, which is on no remote: cargo runs on a farm host against a bare
#      copy of the Mac's rustic_core (a git bundle), through git's
#      url.insteadOf for the fork's URL (GIT_CONFIG_COUNT,
#      CARGO_NET_GIT_FETCH_WITH_CLI) and a private CARGO_HOME, so the lock
#      records the github URL with that commit, as c517a69's does for the
#      unpushed ba93d6e.  The lock that comes back must differ from c517a69's
#      in exactly the rustic-rs version, rustic_core's version and the two
#      commit pins (rustic_core, walkdir), else nothing is committed in
#      rustic.  A rustic HEAD that is already this step's lock commit, pinning
#      rustic_core's HEAD, is "done already".  Nothing is ever pushed, and no
#      commit is amended.
#   4  The farm round.  stick/build-round.sh builds the round (ROUND, --round)
#      from the committed HEADs as they are after step 3 (rustic_core: clean,
#      over ba93d6e; rustic: clean, step 3's lock commit on top, its Cargo.lock
#      pinning that rustic_core HEAD), through clean clones of the three repositories
#      (git clone --shared at the commits: what build-round.sh syncs is those
#      trees, not the working trees): restic-bko 0.19.1+backotter.12 from
#      12a275f62, rustic-bko for Linux and DragonFly, and the native FreeBSD,
#      NetBSD, OpenBSD and illumos builds (SRC= mode).  Then, each job on the
#      host pick-host prefers that this run has given the fewest jobs, under
#      cmp-run.sh --reserve (and guest-slot.sh for the guests): run-all.sh with
#      EXPECT_* from build-round.sh and OLD_BIN the c517a69/ba93d6e build for
#      fs_roundtrip.py on ext4, btrfs, xfs, nonroot-rt.sh linux, sparse-rt.sh on
#      ext4, zfs-guest-rt.sh ubuntu24, the round trips of freebsd15 (and its
#      nonroot), freebsd15-ufs, netbsd11, openbsd8, omnios58, dragonfly6;
#      exact-rt.sh with each tool; compare.py debian13, the four directions.
#      Each job's summary comes back to LOG_DIR/step4/JOB/, and one table ends
#      the step (verdict.py summary for run-all.sh's).  No unit tests run
#      here: rustic_core's restore tests run by hand in netbsd11 and
#      freebsd15-ufs guests (test_restore_exact_twice_changes_nothing passes
#      on NetBSD since .15; step 2 runs it alone).
#
# Options:
#   --steps LIST     comma-separated, of 1 2 3 4 (default all)
#   --dry-run        print every command that would change something, and run
#                    only read-only lookups (pick-host, ssh test/ss/sensors,
#                    git status); job scripts are still written to LOG_DIR
#   --log-dir DIR    the Mac's log directory (default
#                    ~/git/bko-rustic-round-STAMP, -dry for a dry run); give
#                    step 1's to a later --steps 3
#   --host H         step 1's host and step 3's cargo host (default pick-host)
#   --nb-host H, --fb-host H
#                    step 2's NetBSD and FreeBSD hosts (default: a host
#                    pick-host gives that has the build image)
#   --hosts H,H      step 4's job hosts, of the host table's default amd64
#                    hosts (default pick-host's, which are those)
#   --build-hosts H,H
#                    build-round.sh's --hosts, of the host table's amd64
#                    build-role hosts (default all of those); the job hosts
#                    outside them get the round's bin (--dist-hosts)
#   --round NAME     build-round.sh's ROUND (default rr15-STAMP)
#   --old-bin DIR    OLD_BIN: a directory of OS/restic-bko, rustic-bko and
#                    their commit files, all c517a69/ba93d6e; none for none
#                    (default: assembled from the earlier round's copies on the
#                    Mac, RX_LINUX_BIN and RX_BSD_BIN below)
#   --force-commit   step 3 without step 1's pass
#   --keep           keep the hosts' work directories after a pass
#   --job-hours N    a job still running N hours after cmp-run.sh first gave
#                    it its room is stopped, a FAIL (default 10; one that
#                    never got room, 2N hours after it started): the watchdog
#   -h, --help       this text
#
# Environment: BKO_GIT (~/git), BKO_RUST_TOOLCHAIN (1.94.1), CHOPIN_ADDR
# (192.168.68.194: Chopin is ssh -o HostKeyAlias=chopin.local m@CHOPIN_ADDR,
# the others m@NAME.local), HOT_MAX (95: heavy work on Chopin waits while
# sensors' Tctl is over it, checked before each job there), RX_LINUX_BIN and
# RX_BSD_BIN (the c517a69/ba93d6e binaries kept on the Mac: rx-linux/bin, flat,
# and rx-bsd/bin/OS, in the scratchpad of the session that built them),
# ALLOW_OTHER_GO=1 (restic-bko built by another Go than go1.25.10: each
# build host's Go, the one build-round.sh would take, is checked before it
# runs).  The script's own ssh reaches Chopin by its address, but
# testvm/pick-host and stick/build-round.sh, which it runs, reach it as
# m@chopin.local over mDNS (testvm/hosts.json; CHOPIN= can't carry the
# HostKeyAlias option): if mDNS fails, pick-host leaves Chopin out and
# build-round.sh fails, so check `ssh m@chopin.local true` first.
#
# On the hosts.  Each host used gets ~/backotter-work/RUN (RUN is
# rustic-round-STAMP: sources, job scripts, logs, binaries) and
# ~/backotter-work/RUN-wt (the Mac's testvm/ and stick/, rsynced; never
# testvm/sync-hosts.sh, never the shared ~/backotter-work/testvm), and every
# job runs detached there (setsid -f, its exit status in RUN/logs/JOB.exit),
# so the Mac's ssh may drop; the Mac polls every minute.  On a pass both
# directories go (--keep keeps them); on a failure they are kept and their
# paths printed.  build-round.sh's roundROUND is the round's, kept.  Ctrl-C,
# TERM or a STOP (any exit of the driver while jobs it started still run)
# stops those jobs, by the process groups they recorded (checked against
# /proc/PID/cmdline first) and, for step 2's, by their recorded vm-run
# (never pkill or killall), and build-round.sh, and prints what is kept.
# cmp-run.sh --free is not used.  Step 2's guests run under cmp-run.sh
# --reserve 2 10 and a guest slot; compare.py's QEMU gets the job's BKO_GUEST_SLOT.
# cmp-debian13 has no PASS of compare.py's (its exit is always 0): its
# results.json is judged FAIL on a failed restore, no ssh or new failed units
# in a boot, a direction missing; else PASS with no differences, or SEE (the
# differences are in the table, to look at; not counted as passed).
#
# Needs: on the Mac bash (3.2 will do), git, rsync, ssh, python3, awk, shasum,
# tar; on the hosts what the scripts it runs need.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$here/../.." && pwd)
me=$here/$(basename "$0")
GIT=${BKO_GIT:-$HOME/git}
CORE=$GIT/rustic_core RUSTIC=$GIT/rustic RESTIC=$GIT/restic
CORE_BASE=ba93d6e6cb2604f65bbc6106691698a078583306
RUSTIC_BASE=c517a69c338144a6683045bb781b56957ced444d
RESTIC_HEAD=12a275f6218210b9957e5dad809ab7dd321691d0
# The versions the HEADs have now: rustic_core 3fd3921 (.15, NetBSD's
# atimes put back alone, in e21b85b; then BACKOTTER.md) and rustic 2c3b237
# (.16, ea4ee26; then the lock taking 3fd3921) over the .14/.15 commits.
CORE_VER_OLD=0.13.0+backotter.13 CORE_VER=0.13.0+backotter.15
RUSTIC_VER_OLD=0.11.4+backotter.14 RUSTIC_VER=0.11.4+backotter.16
RESTIC_VER=0.19.1+backotter.12
CORE_URL=https://github.com/mchobbit/rustic_core.git
# What the fixes since ba93d6e touch (.14's restore.rs and files_writer.rs,
# .15's exact.rs and osattrs.rs for NetBSD's atimes), and the version files.
CORE_CODE=(crates/core/src/commands/restore.rs crates/core/src/commands/restore/files_writer.rs
    crates/core/src/commands/restore/exact.rs crates/core/src/backend/osattrs.rs
    crates/core/tests/integration/restore.rs)
CORE_VERFILES=(BACKOTTER.md Cargo.lock crates/core/Cargo.toml)
CORE_NF=$(( ${#CORE_CODE[@]} + ${#CORE_VERFILES[@]} ))   # how many: the messages say it
RUSTIC_FILES=(BACKOTTER.md Cargo.toml build-backotter.sh)
CORE_MSG1="backotter: restore --delete leaves a case variant of a planned entry; a kept group's later names in the way; no panic when a planned file is gone"
CORE_MSG2="backotter: $CORE_VER; BACKOTTER.md on restore --delete's case variants, a kept group's later names and a planned file gone"
RUSTIC_MSG_PREFIX="backotter: Cargo.lock takes rustic_core"
COAUTHOR="Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
TC=${BKO_RUST_TOOLCHAIN:-1.94.1}
SCRATCH=/private/tmp/claude-501/-Users-mchobbit-git-backotter/b1242dfa-05e2-431d-8d8b-21ef05e69ab2/scratchpad
RX_LINUX_BIN=${RX_LINUX_BIN:-$SCRATCH/rx-linux/bin}
RX_BSD_BIN=${RX_BSD_BIN:-$SCRATCH/rx-bsd/bin}
CHOPIN_ADDR=${CHOPIN_ADDR:-192.168.68.194}
usage() { sed -n '2,/^$/{s/^# \{0,1\}//;p;}' "$me"; exit "${1:-0}"; }
say() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { say "STOP: $*" >&2; exit 1; }
q() { local a s=""; for a in "$@"; do s+=" $(printf %q "$a")"; done; printf '%s' "${s# }"; }
# x CMD...: CMD, or with --dry-run only printed (everything that changes state goes through x).
x() {
    if [[ $DRY == 0 ]]; then "$@"
    elif [[ $1 == hssh ]]; then echo "    [dry-run] ssh $(dest "$2"): $3"
    else echo "    [dry-run] $(q "$@")"; fi
}
# The host table's lines come after say and die, which their failures call.
# shellcheck source=../hosts.sh
. "$REPO/testvm/hosts.sh"
# The farm, from the host table (never a list of names here, as ht1-farm.sh
# does it): ALL_HOSTS is pick-host's default amd64 hosts, BUILD_ALL the amd64
# hosts with the build role, which is what build-round.sh will take.
ALL_HOSTS=$(hosts_names --default --arch amd64 | tr '\n' ' ') || die "no hosts from the host table"
BUILD_ALL=$(hosts_names --role build --arch amd64 | tr '\n' ' ') || die "no build hosts from the host table"
ALL_HOSTS=${ALL_HOSTS% } BUILD_ALL=${BUILD_ALL% }
[[ -n $ALL_HOSTS && -n $BUILD_ALL ]] || die "the host table has no default or no build host"

steps=1,2,3,4 DRY=0 LOGDIR="" HOST1="" NBHOST="" FBHOST="" POOL="" BUILD_HOSTS="" ROUND=""
OLD_ARG="" FORCE=0 KEEP=0 JOB_HOURS=10
while [[ $# -gt 0 ]]; do
    case $1 in
        --steps) steps=${2:?--steps LIST}; shift ;;
        --dry-run) DRY=1 ;;
        --log-dir) LOGDIR=${2:?--log-dir DIR}; shift ;;
        --host) HOST1=${2:?--host H}; shift ;;
        --nb-host) NBHOST=${2:?--nb-host H}; shift ;;
        --fb-host) FBHOST=${2:?--fb-host H}; shift ;;
        --hosts) POOL=${2:?--hosts H,H}; shift ;;
        --build-hosts) BUILD_HOSTS=${2:?--build-hosts H,H}; shift ;;
        --round) ROUND=${2:?--round NAME}; shift ;;
        --old-bin) OLD_ARG=${2:?--old-bin DIR|none}; shift ;;
        --force-commit) FORCE=1 ;;
        --keep) KEEP=1 ;;
        --job-hours) JOB_HOURS=${2:?--job-hours N}; shift ;;
        -h|--help) usage 0 ;;
        *) echo "rustic-exact-round: unknown argument $1 (--help)" >&2; exit 2 ;;
    esac
    shift
done
[[ $steps =~ ^[1-4](,[1-4])*$ ]] || { echo "--steps: a comma list of 1 2 3 4, not $steps" >&2; exit 2; }
[[ $JOB_HOURS =~ ^[0-9]+$ && $JOB_HOURS -gt 0 ]] || { echo "--job-hours: hours, not $JOB_HOURS" >&2; exit 2; }
want() { [[ ",$steps," == *",$1,"* ]]; }
for h in $HOST1 $NBHOST $FBHOST $(echo "$POOL" | tr ',' ' '); do
    [[ " $ALL_HOSTS " == *" $h "* ]] || { echo "no such farm host: $h (of $ALL_HOSTS)" >&2; exit 2; }
done
# Step 1 builds there: --host is a build-role host.
[[ -z $HOST1 || " $BUILD_ALL " == *" $HOST1 "* ]] || { echo "--host: $HOST1 is no build host (of $BUILD_ALL)" >&2; exit 2; }
# build-round.sh's --hosts takes only the build-role hosts.
for h in $(echo "$BUILD_HOSTS" | tr ',' ' '); do
    [[ " $BUILD_ALL " == *" $h "* ]] || { echo "--build-hosts: $h is no build host (of $BUILD_ALL)" >&2; exit 2; }
done
[[ $(uname -s) == Darwin ]] || { echo "rustic-exact-round: run this on the Mac, not on $(hostname -s)" >&2; exit 2; }

STAMP=$(date +%Y%m%d-%H%M%S)
RUN=rustic-round-$STAMP
TAG6=$(date +%H%M%S)
[[ -n $ROUND ]] || ROUND=rr15-$STAMP
[[ $ROUND =~ ^[A-Za-z0-9._-]+$ ]] || { echo "--round: letters, digits, . _ -, not $ROUND" >&2; exit 2; }
# Never "X=...$([[ $DRY == 1 ]] && echo -dry)": on a real run that substitution's
# status, 1, is the assignment's, and set -e ends the script there.
sfx=""
if [[ $DRY == 1 ]]; then sfx=-dry; fi
[[ -n $LOGDIR ]] || LOGDIR=$(cd "$REPO/.." && pwd)/bko-rustic-round-$STAMP$sfx
mkdir -p "$LOGDIR"
LOGDIR=$(cd "$LOGDIR" && pwd)
exec > >(trap '' INT TERM; tee -a "$LOGDIR/driver.log") 2>&1

# --- ssh and rsync to the farm ----------------------------------------------------------
SSHO=(-o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=10)
dest() { if [[ $1 == chopin ]]; then echo "m@$CHOPIN_ADDR"; else echo "m@$1.local"; fi; }
sshv() { if [[ $1 == chopin ]]; then echo "ssh $(q "${SSHO[@]}") -o HostKeyAlias=chopin.local"; else echo "ssh $(q "${SSHO[@]}")"; fi; }
# hssh HOST CMD: ssh to a farm host (read-only lookups call it directly, the rest through x).
hssh() {
    local h=$1; shift
    if [[ $h == chopin ]]; then ssh -n "${SSHO[@]}" -o HostKeyAlias=chopin.local "$(dest "$h")" "$@"
    else ssh -n "${SSHO[@]}" "$(dest "$h")" "$@"; fi
}
# hput HOST REMOTE_DIR SRC...: rsync to ~/REMOTE_DIR/ there (no target/, .git, caches); STOP if it fails.
hput() {
    local h=$1 d=$2 s; shift 2
    for s in "$@"; do [[ -n $s && $s != / ]] || die "hput $h $d: refusing source '$s'"; done
    x rsync -rlp --checksum --exclude target --exclude .git --exclude __pycache__ --exclude .pytest_cache \
        -e "$(sshv "$h")" "$@" "$(dest "$h"):$d/" || die "rsync to $h:~/$d/ failed ($*)"
}
# hget HOST REMOTE_PATH LOCAL_DIR: rsync back (no work/ trees).
hget() {
    mkdir -p "$3"
    x rsync -rlp --exclude work --exclude '*.qcow2' --exclude '*.img' --exclude run.tgz --exclude core.tgz -e "$(sshv "$1")" "$(dest "$1"):$2" "$3/" || true
}
W=backotter-work          # the hosts' ~/backotter-work, relative to home
R=$W/$RUN                 # this run's directory there
WT=$W/$RUN-wt             # this run's testvm/ and stick/ there
TOUCHED=""                # hosts this run made directories on

lo_of() { local r; r=$(hosts_get "$1" slot_range) || die "no slot_range for $1 in testvm/hosts.json"; echo "${r%-*}"; }
# chopin_cool: before a heavy job on Chopin, wait while sensors' Tctl is over HOT_MAX.
chopin_cool() {
    local t
    while :; do
        t=$(hssh chopin "sensors 2>/dev/null | sed -n 's/^Tctl: *+\([0-9]*\).*/\1/p' | head -n 1" 2>/dev/null) || t=""
        if [[ -z $t ]]; then say "chopin: no Tctl from sensors (not waiting)"; return 0; fi
        if [[ $t -le ${HOT_MAX:-95} || $DRY == 1 ]]; then say "chopin: Tctl $t C"; return 0; fi
        say "chopin: Tctl $t C, over ${HOT_MAX:-95} C: waiting"
        sleep 60
    done
}

# --- the host side: files every job sources ---------------------------------------------
write_remote() {
    local d=$LOGDIR/remote
    mkdir -p "$d"
    cat >"$d/jobs.sh" <<'EOF'
# shellcheck shell=bash
# The host side of testvm/compare/rustic-exact-round.sh, written by it into
# ~/backotter-work/RUN/jobs.sh: each job script (RUN/jobs/JOB.sh) sets its
# variables and sources this, then job_run, detached (setsid -f): its output
# in RUN/logs/JOB.out, its process group in JOB.pid, its status in JOB.exit.
set -uo pipefail
W=$HOME/backotter-work
R=$W/$RUN WT=$W/$RUN-wt L=$W/$RUN/logs CR=$W/cmp-run.sh
CLEANUP=()
note() { echo "$(date +%T) $JOB: $*"; }
# Chopin's cooler is the farm's weakest: heavy work there waits while Tctl is over HOT_MAX.
hot_wait() {
    [ "$(hostname -s)" = chopin ] || return 0
    local t
    while :; do
        t=$(sensors 2>/dev/null | sed -n 's/^Tctl: *+\([0-9]*\).*/\1/p' | head -n 1)
        [ -n "$t" ] || { note "no Tctl from sensors: not waiting"; return 0; }
        [ "$t" -gt "${HOT_MAX:-95}" ] || return 0
        note "Tctl $t C over ${HOT_MAX:-95} C: waiting"
        sleep 60
    done
}
# reserve TAG D M CMD...: CMD under cmp-run.sh --reserve D M, from ~/backotter-work.
# JOB.granted gets the time the job first had room (the Mac's watchdog counts from it).
reserve() {
    local tag=$1 d=$2 m=$3; shift 3
    hot_wait
    (cd "$W" && WEAR_TAG="test:$RUN:$tag" "$CR" --reserve "$d" "$m" \
        sh -c '[ -s "$0" ] || date +%s >"$0"; exec "$@"' "$L/$JOB.granted" "$@")
}
# ram_keep DIR: a failed job's RAM work goes to /bkoscratch/RUN (unless something is mounted in it).
ram_keep() {
    [ -e "$1" ] || return 0
    if grep -q " $1" /proc/mounts; then note "kept in RAM (mounted): $1"; return 0; fi
    sudo -n mkdir -p "/bkoscratch/$RUN" && sudo -n mv "$1" "/bkoscratch/$RUN/" &&
        note "failed run's work: /bkoscratch/$RUN/$(basename "$1")"
}
job_cleanup() { local c; for c in "${CLEANUP[@]+"${CLEANUP[@]}"}"; do eval "$c" || true; done; CLEANUP=(); }
# fgw CMD...: CMD in the background, waited for, so that a TERM's trap runs at
# once (a foreground vm_ssh, under timeout in a process group of its own,
# would hold it off until it returned).
fgw() { "$@" <&0 & wait $!; }   # <&0: an asynchronous list's stdin would be /dev/null
job_run() {
    mkdir -p "$L"
    rm -f "$L/$JOB.exit" "$L/$JOB.granted" "$L/$JOB.vmpid"
    echo $$ >"$L/$JOB.pid"
    exec >>"$L/$JOB.out" 2>&1 </dev/null
    trap 'note "stopped (TERM)"; job_cleanup; echo 143 >"$L/$JOB.exit"; exit 143' TERM INT HUP
    note "start on $(hostname -s) ($KIND), pid $$"
    local rc=0
    "job_$KIND" || rc=$?
    job_cleanup
    note "exit $rc"
    echo "$rc" >"$L/$JOB.exit"
}

E=(env HOME="$HOME" PATH="$HOME/.cargo/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" RUSTUP_TOOLCHAIN="$TC")

# Step 1: build rustic-bko from the working trees, test rustic_core as the user
# and as root, exact-rt.sh.
job_s1() {
    local B=$R/s1/build O=$L/s1 out v lib int ram=/mnt/bko-ram/$RUN-ut
    mkdir -p "$B" "$O" "$R/bin/linux"
    rm -rf "$B/rustic" "$B/rustic_core"
    cp -a "$R/src/rustic" "$R/src/rustic_core" "$B/" || return 1
    note "prep-rustic-src.sh: rustic takes $B/rustic_core"
    "${E[@]}" CARGO="$HOME/.cargo/bin/cargo" sh "$WT/stick/prep-rustic-src.sh" "$B" >"$O/prep.log" 2>&1 ||
        { note "prep-rustic-src.sh failed: $O/prep.log"; tail -n 20 "$O/prep.log"; return 1; }
    note "build-backotter.sh (release, x86_64-unknown-linux-musl)"
    reserve s1-build 0 6 "${E[@]}" bash -c 'cd "$1/rustic" && ./build-backotter.sh' _ "$B" >"$O/build.log" 2>&1 ||
        { note "the build failed: $O/build.log"; tail -n 30 "$O/build.log"; return 1; }
    out=$(ls "$B"/rustic/dist/rustic-backotter-*-x86_64-unknown-linux-musl 2>/dev/null | head -n 1)
    [ -n "$out" ] || { note "no binary in $B/rustic/dist"; return 1; }
    cp "$out" "$R/bin/linux/rustic-bko"
    v=$("$R/bin/linux/rustic-bko" --version 2>&1 | head -n 1)
    note "built $out: $v"
    [ "$v" = "rustic $RUSTIC_VER" ] || { note "its version is '$v', not rustic $RUSTIC_VER"; return 1; }
    printf '%s\n' "$RUSTIC_SRC" "rustic_core $CORE_SRC" "rustc $TC, x86_64-unknown-linux-musl, release, on $(hostname -s)" \
        >"$R/bin/linux/rustic-commit"
    sha256sum "$R/bin/linux/rustic-bko" | tee "$O/rustic-bko.sha256"

    note "cargo test -p rustic_core as $(id -un), TMPDIR $ram"
    mkdir -p "$ram"
    CLEANUP+=("rm -rf '$ram'")
    reserve s1-test 2 6 "${E[@]}" TMPDIR="$ram" bash -c 'cd "$1/rustic_core" && cargo test -p rustic_core --locked --no-fail-fast' \
        _ "$B" >"$O/test-user.log" 2>&1
    local rc=$?
    rm -rf "$ram"
    grep -E '^test result:|^failures:|FAILED|panicked' "$O/test-user.log" | head -n 40
    [ $rc = 0 ] || { note "cargo test failed ($rc): $O/test-user.log"; return 1; }

    "${E[@]}" bash -c 'cd "$1/rustic_core" && cargo test -p rustic_core --locked --no-run' _ "$B" >"$O/no-run.log" 2>&1 ||
        { note "cargo test --no-run failed: $O/no-run.log"; return 1; }
    lib=$(sed -n 's/.*Executable unittests src\/lib.rs (\(.*\))$/\1/p' "$O/no-run.log" | head -n 1)
    int=$(sed -n 's/.*Executable tests\/integration.rs (\(.*\))$/\1/p' "$O/no-run.log" | head -n 1)
    [ -n "$lib" ] && [ -n "$int" ] || { note "no test executables in $O/no-run.log"; return 1; }
    note "restore tests as root: $lib commands::restore, $int restore (TMPDIR: tmpfs, loop btrfs)"
    reserve s1-root 3 4 sudo -n bash "$R/roottests.sh" "$B/rustic_core" "$lib" "$int" "/mnt/bko-ram/$RUN-root" "$O" \
        >"$O/test-root.log" 2>&1 || { note "the root tests failed: $O/test-root.log"; tail -n 40 "$O/test-root.log"; return 1; }
    cat "$O/test-root.log"

    note "exact-rt.sh as root, with $R/bin/linux/rustic-bko"
    local w=/mnt/bko-ram/$RUN-exact
    reserve s1-exact 5 2 sudo -n env PATH=/usr/sbin:/usr/bin:/sbin:/bin WORK="$w" "$WT/testvm/compare/exact-rt.sh" \
        "$R/bin/linux/rustic-bko" >"$O/exact-rt.log" 2>&1
    rc=$?
    tail -n 4 "$O/exact-rt.log"
    if [ $rc != 0 ] || [ "$(tail -n 1 "$O/exact-rt.log")" != PASS ]; then
        note "exact-rt.sh failed ($rc): $O/exact-rt.log"; ram_keep "$w"; return 1
    fi
    touch "$O/PASS"
}

# Step 2: rustic_core's integration test under ktrace in a throwaway BSD guest,
# under cmp-run.sh --reserve 2 10 (the 8 GiB build guest; then a 4 GiB one).
job_s2() {
    reserve "$JOB" 2 10 env HOME="$HOME" PATH="$PATH" JOB="$JOB" KIND="$KIND" RUN="$RUN" TC="$TC" OSN="$OSN" PORT="$PORT" \
        bash -c '. "$HOME/backotter-work/$RUN/jobs.sh" || exit 2
            trap "note \"stopped (TERM)\"; job_cleanup; exit 143" TERM INT HUP
            rc=0; job_s2in || rc=$?; job_cleanup; exit $rc'
}
job_s2in() {
    local O=$R/s2/$OSN bvm rvm gp img key r
    . "$WT/testvm/vm-lib.sh" || return 1
    . "$WT/testvm/compare/guest-slot.sh" || return 1
    # shellcheck disable=SC2034  # vm-lib.sh's
    WORK=$W
    case $OSN in
        netbsd) bvm=netbsd11 rvm="" gp=/sbin:/usr/sbin:/bin:/usr/bin:/usr/pkg/sbin:/usr/pkg/bin ;;
        freebsd) bvm=freebsd14 rvm=freebsd15-ufs gp=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin ;;
        *) note "no OS $OSN"; return 2 ;;
    esac
    img=$W/vms-bko-build/$bvm-rust.qcow2 key=$W/vms-bko-build/$bvm.id_ed25519
    [ -f "$img" ] && [ -f "$key" ] || { note "no build image $img and $key (stick/$OSN/build-rustic.sh --setup makes them)"; return 1; }
    rm -rf "$O"; mkdir -p "$O"
    tar -C "$R/src" --exclude target -czf "$O/core.tgz" rustic_core || return 1
    r=$(slot_range_here) || return 1
    slot_take 3 "$r"
    CLEANUP+=("vm_halt" "slot_give")
    hot_wait
    note "booting a throwaway copy of $img on port $PORT"
    VM_IMAGE=$img VM_IMAGE_KEY=$key vm_boot "$bvm" "$PORT" "$O/boot-build" --snapshot --cpus 6 --memory 8192 ||
        { note "$bvm's build image didn't come up"; return 1; }
    # stop_job on the Mac stops the guest by this PID (vm-run's), whatever the job is doing.
    echo "$VM_PID" >"$L/$JOB.vmpid"
    note "up: $(vm_ssh 'uname -sr')"
    T=900 fgw vm_ssh 'rm -rf /root/bko-diag && mkdir -p /root/bko-diag && cd /root/bko-diag && gzip -dc | tar -xf -' <"$O/core.tgz" || return 1
    vm_put "$R/diag.sh" /root/bko-diag/diag.sh && vm_put "$R/diag-stat.sh" /root/bko-diag/diag-stat.sh &&
        vm_put "$R/s2/restore-diag.rs" /root/bko-diag/restore-diag.rs 644 || return 1
    note "building the integration test binary (as it is, and with the stat hook)"
    T=14400 fgw vm_ssh "PATH=$gp; export PATH; sh /root/bko-diag/diag.sh build" >"$O/build.log" 2>&1 ||
        { note "the build in $bvm failed: $O/build.log"; tail -n 40 "$O/build.log"; return 1; }
    tail -n 3 "$O/build.log"
    if [ -n "$rvm" ]; then
        # The binaries and the tree they read fixtures from (CARGO_MANIFEST_DIR) to the same path in $rvm.
        T=1800 fgw vm_ssh 'cd /root/bko-diag && tar -cf - --exclude target rustic_core bin out diag.sh diag-stat.sh | gzip -c' \
            >"$O/run.tgz" || { note "copying out of $bvm failed"; return 1; }
        vm_halt
        note "booting a throwaway copy of $rvm on port $PORT"
        vm_boot "$rvm" "$PORT" "$O/boot-run" --snapshot --cpus 4 --memory 4096 || { note "$rvm didn't come up"; return 1; }
        echo "$VM_PID" >"$L/$JOB.vmpid"
        note "up: $(vm_ssh 'uname -sr')"
        T=900 fgw vm_ssh 'rm -rf /root/bko-diag && mkdir -p /root/bko-diag && cd /root/bko-diag && gzip -dc | tar -xf -' <"$O/run.tgz" || return 1
    fi
    note "running test_restore_exact_twice_changes_nothing under ktrace"
    T=7200 fgw vm_ssh "PATH=$gp; export PATH; sh /root/bko-diag/diag.sh run" >"$O/run.log" 2>&1 ||
        { note "diag.sh run failed: $O/run.log"; tail -n 40 "$O/run.log"; }
    cat "$O/run.log"
    T=1800 fgw vm_ssh 'cd /root/bko-diag && tar -cf - out | gzip -c' >"$O/out.tgz" || { note "fetching the output failed"; return 1; }
    ls -l "$O/out.tgz"
    vm_halt
    slot_give
    CLEANUP=()
    rm -f "$L/$JOB.vmpid"
    grep -q '^== ' "$O/run.log" || return 1
    touch "$O/COLLECTED"
}

# Step 3: Cargo.lock for rustic against the Mac's committed rustic_core (in a bundle).
job_lock() {
    local S=$R/s3
    rm -rf "$S/core.git" "$S/cargo-home"
    git clone -q --bare "$S/core.bundle" "$S/core.git" || return 1
    local got
    got=$(git -C "$S/core.git" rev-parse refs/heads/backotter) || return 1
    [ "$got" = "$CORE_NEW" ] || { note "the bundle's backotter is $got, not $CORE_NEW"; return 1; }
    # A CARGO_HOME of its own, registry included (its lock is CARGO_HOME's, so
    # the shared ~/.cargo/registry is only read): the index copied, the .crate
    # files hard-linked (cargo never rewrites one), the rest fetched.
    mkdir -p "$S/cargo-home/registry"
    [ -d "$HOME/.cargo/registry/index" ] && cp -a "$HOME/.cargo/registry/index" "$S/cargo-home/registry/" 2>/dev/null
    [ -d "$HOME/.cargo/registry/cache" ] && cp -al "$HOME/.cargo/registry/cache" "$S/cargo-home/registry/" 2>/dev/null
    [ -f "$HOME/.cargo/config.toml" ] && cp "$HOME/.cargo/config.toml" "$S/cargo-home/"
    local G=(env CARGO_HOME="$S/cargo-home" CARGO_NET_GIT_FETCH_WITH_CLI=true GIT_CONFIG_COUNT=1
        GIT_CONFIG_KEY_0="url.file://$S/core.git.insteadOf" GIT_CONFIG_VALUE_0="$CORE_URL")
    cd "$S/rustic" || return 1
    note "cargo update -p rustic_core -p walkdir ($CORE_URL is $S/core.git here)"
    "${E[@]}" "${G[@]}" "$HOME/.cargo/bin/cargo" update -p rustic_core -p walkdir || return 1
    note "cargo metadata --locked"
    "${E[@]}" "${G[@]}" "$HOME/.cargo/bin/cargo" metadata --locked --format-version 1 >/dev/null || return 1
    grep -A2 -E '^name = "(rustic_core|walkdir|rustic-rs)"$' Cargo.lock
}

# Step 4: run-all.sh under a reservation.
job_runall() {
    reserve "$JOB" "$D" "$M" env HOME="$HOME" PATH="$PATH" BKO_WORK="$W" OLD_BIN="$OLDB" "${EXP[@]}" \
        MAX_GUESTS=3 COUNT_OTHERS=1 ${PB:+PORT_BASE="$PB"} RUN_PREFIX="$PFX" ANY_HOST=1 "${EXTRA[@]+"${EXTRA[@]}"}" \
        "$WT/testvm/compare/run-all.sh" --platforms "$PLATS" ${ONLY:+--only "$ONLY"} "$BIN" "$L/$JOB"
}
# Step 4: exact-rt.sh as root.
job_exact() {
    local w=/mnt/bko-ram/$PFX-exact rc=0
    mkdir -p "$L/$JOB"
    reserve "$JOB" "$D" "$M" sudo -n env PATH=/usr/sbin:/usr/bin:/sbin:/bin WORK="$w" "$WT/testvm/compare/exact-rt.sh" \
        "$BIN/linux/$TOOL" >"$L/$JOB/exact-rt.log" 2>&1 || rc=$?
    tail -n 4 "$L/$JOB/exact-rt.log"
    [ $rc = 0 ] && [ "$(tail -n 1 "$L/$JOB/exact-rt.log")" = PASS ] && return 0
    ram_keep "$w"
    return 1
}
# Step 4: compare.py GUEST, the four directions, in a guest slot.
job_compare() {
    mkdir -p "$L/$JOB"
    reserve "$JOB" "$D" "$M" env HOME="$HOME" PATH="$PATH" bash -c '
        c=$1 g=$2 p=$3 b=$4 n=$5
        . "$c/guest-slot.sh" && r=$(slot_range_here) && slot_take 3 "$r" || exit 2
        cd ~/backotter-work && sudo -n env PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME="$HOME" BKO_GUEST_SLOT="$BKO_GUEST_SLOT" RESTIC_BKO="$b/restic-bko" \
            RUSTIC_BKO="$b/rustic-bko" COMPARE_NAME="$n" COMPARE_PORT="$p" python3 "$c/compare.py" "$g" \
            restic-bko rustic-bko restic-to-rustic rustic-to-restic' _ \
        "$WT/testvm/compare" "$GUEST" "$PORT" "$BIN/linux" "$PFX" >"$L/$JOB/compare.log" 2>&1
}
EOF
    cat >"$d/roottests.sh" <<'EOF'
#!/usr/bin/env bash
# roottests.sh CORE LIB INT RAMDIR OUTDIR: rustic_core's restore unit tests
# (LIB commands::restore) and integration tests (INT restore) as root, with
# TMPDIR on a tmpfs and on a loop btrfs, both made in RAMDIR (removed after).
# Written by testvm/compare/rustic-exact-round.sh; run with sudo -n.
set -u
core=$1 lib=$2 int=$3 ram=$4 out=$5
PATH=/usr/sbin:/usr/bin:/sbin:/bin
cleanup() {
    local m
    for m in "$ram/btrfs" "$ram/tmpfs"; do mountpoint -q "$m" && umount "$m"; done
    rm -rf "$ram"
    [ -n "${SUDO_UID:-}" ] && chown -R "$SUDO_UID:$SUDO_GID" "$out"
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP
mkdir -p "$ram/tmpfs" "$ram/btrfs" "$out"
mount -t tmpfs -o size=1g,mode=1777 bko-rr-tmpfs "$ram/tmpfs" || exit 1
truncate -s 1G "$ram/btrfs.img" && mkfs.btrfs -q "$ram/btrfs.img" >/dev/null &&
    mount -o loop "$ram/btrfs.img" "$ram/btrfs" && chmod 1777 "$ram/btrfs" || exit 1
rc=0
one() {  # NAME BINARY FILTER: one test binary's tests matching FILTER, TMPDIR=$d
    TMPDIR=$d "$2" "$3" >"$out/root-$fs-$1.log" 2>&1
    local s=$?
    grep -E '^test result:|FAILED|panicked' "$out/root-$fs-$1.log" | sed 's/^/  /'
    echo "  $1 $3: exit $s"
    [ $s = 0 ] || rc=1
}
cd "$core/crates/core" || exit 1
for d in "$ram/tmpfs" "$ram/btrfs"; do
    fs=$(stat -f -c %T "$d")
    echo "=== TMPDIR=$d ($fs)"
    one lib "$core/$lib" commands::restore
    one int "$core/$int" restore
done
exit $rc
EOF
    cat >"$d/diag.sh" <<'EOF'
#!/bin/sh
# diag.sh build|run, in a NetBSD or FreeBSD guest's /root/bko-diag (written
# by testvm/compare/rustic-exact-round.sh, step 2).  build: rustic_core's
# integration test binary as it is (bin/integration-pure) and with the stat
# hook of restore-diag.rs (bin/integration-diag); the sources are put back.
# run: test_restore_exact_twice_changes_nothing with each, under
# ktrace -i -t cn, TMPDIR on the root file system and on /tmp; kdump -T of
# each (out/LABEL.kdump), the second restore's lines of the test's process
# (LABEL-second.kdump: pure from the test's 20 ms sleep, diag between the
# hook's marks), the metadata calls among them (LABEL-calls.txt) and their
# counts (LABEL-callcounts.txt), stat -f after each restore (diag: LABEL-stat.txt).
D=/root/bko-diag
T=test_restore_exact_twice_changes_nothing
unset PROJECT_VERSION
mkdir -p "$D/out" "$D/bin"
typ='function typ(  i) { for (i = 2; i <= 6 && i <= NF; i++) if ($i ~ /^(CALL|RET|NAMI|GIO|PSIG|EMUL|STRU|USER|CSW|SCTL|MISC|CAP|FAULT|FAULTEND|PFLT|PRET|EXEC)$/) return i; return 0 }'
section() {  # KIND <KDUMP: the second restore, the test process's lines only
    awk -v kind="$1" "$typ"'
        { t = typ() }
        t && main == "" && $t == "CALL" { main = $1 }
        kind == "pure" && !on && t && $t == "CALL" && $(t + 1) ~ /nanosleep/ { on = 1; next }
        kind == "diag" && t && $t == "NAMI" && /BKO-MARK-1/ { on = 1; next }
        kind == "diag" && t && $t == "NAMI" && /BKO-MARK-2/ { exit }
        on && $1 == main { print }'
}
calls() {  # <KDUMP: the calls that change a file or its metadata, with their NAMI and RET lines
    awk "$typ"'
        { t = typ() }
        t && $t == "CALL" { n = $(t + 1); sub(/\(.*/, "", n)
            k[$1 " " $2] = (n ~ /utime|futime|lutime|chmod|chown|chflags|extattr|xattr|rename|unlink|link|mkdir|rmdir|truncate|open|write|mknod|mkfifo|fsync|acl/) }
        t && k[$1 " " $2] { print }'
}
counts() {  # <KDUMP: the calls, by name
    awk "$typ"'{ t = typ() } t && $t == "CALL" { n = $(t + 1); sub(/\(.*/, "", n); print n }' | sort | uniq -c | sort -rn
}
case ${1:-} in
build)
    cd "$D/rustic_core" || exit 1
    echo "== $(uname -a)"
    rustc --version && cargo --version || exit 1
    f=crates/core/tests/integration/restore.rs
    cargo test -p rustic_core --locked --test integration --no-run >"$D/out/build-pure.log" 2>&1 ||
        { tail -n 40 "$D/out/build-pure.log"; exit 1; }
    p=$(sed -n 's/.*Executable tests\/integration.rs (\(.*\))$/\1/p' "$D/out/build-pure.log" | tail -n 1)
    [ -n "$p" ] && cp "$p" "$D/bin/integration-pure" || { echo "no integration test binary"; exit 1; }
    cp "$f" "$D/restore.rs.orig" && cp "$D/restore-diag.rs" "$f" || exit 1
    cargo test -p rustic_core --locked --test integration --no-run >"$D/out/build-diag.log" 2>&1 ||
        { tail -n 40 "$D/out/build-diag.log"; cp "$D/restore.rs.orig" "$f"; exit 1; }
    p=$(sed -n 's/.*Executable tests\/integration.rs (\(.*\))$/\1/p' "$D/out/build-diag.log" | tail -n 1)
    cp "$D/restore.rs.orig" "$f"
    [ -n "$p" ] && cp "$p" "$D/bin/integration-diag" || { echo "no hooked integration test binary"; exit 1; }
    ls -l "$D/bin" ;;
run)
    cd "$D/rustic_core/crates/core" || exit 1
    { uname -a; echo; mount; echo; df -h / /tmp; } >"$D/out/system.txt" 2>&1
    for kind in pure diag; do
        for spec in "root:$D/tmp-root" "tmp:/tmp/bko-diag-tmp"; do
            l=$kind-${spec%%:*} td=${spec#*:}
            rm -rf "$td" && mkdir -p "$td" || exit 1
            echo "== $l: TMPDIR=$td ($(df "$td" | tail -n 1))"
            env TMPDIR="$td" BKO_DIAG_STAT="$D/diag-stat.sh" BKO_DIAG_OUT="$D/out/$l-stat.txt" \
                ktrace -i -t cn -f "$D/out/$l.ktrace" "$D/bin/integration-$kind" "$T" --test-threads 1 --nocapture \
                >"$D/out/$l-test.log" 2>&1
            rc=$?
            echo "rc=$rc" >>"$D/out/$l-test.log"
            echo "   test exit $rc: $(grep -E '^test |^test result|panicked' "$D/out/$l-test.log" | head -n 4 | tr '\n' ' ')"
            kdump -T -f "$D/out/$l.ktrace" >"$D/out/$l.kdump" 2>&1 && rm -f "$D/out/$l.ktrace"
            section "$kind" <"$D/out/$l.kdump" >"$D/out/$l-second.kdump"
            calls <"$D/out/$l-second.kdump" >"$D/out/$l-calls.txt"
            counts <"$D/out/$l-second.kdump" >"$D/out/$l-callcounts.txt"
            echo "   second restore: $(wc -l <"$D/out/$l-second.kdump") trace lines, $(grep -c CALL "$D/out/$l-calls.txt") metadata calls:" \
                "$(head -n 12 "$D/out/$l-callcounts.txt" | awk '{ printf "%s %s, ", $2, $1 }')"
            rm -rf "$td"
        done
    done ;;
*) echo "usage: diag.sh build|run" >&2; exit 2 ;;
esac
EOF
    cat >"$d/diag-stat.sh" <<'EOF'
#!/bin/sh
# diag-stat.sh TARGET N: run by restore-diag.rs's hook after restore N of
# test_restore_exact_twice_changes_nothing: marks the trace (a file named
# ...-BKO-MARK-N) and appends stat -f of the test's restored entries (lstat:
# no directory is read, no atime moves) to BKO_DIAG_OUT.
t=$1/test n=$2 o=${BKO_DIAG_OUT:-/dev/stderr}
: >"${o%.txt}-BKO-MARK-$n"
{
    echo "== after restore $n ($(date))"
    for p in "$t" "$t/d" "$t/d/a" "$t/d/b" "$t/c" "$t/e" "$t/s"; do
        stat -f '%N ino=%i mode=%Sp nlink=%l uid=%u gid=%g flags=%f size=%z birth=%.9FB ctime=%.9Fc mtime=%.9Fm atime=%.9Fa' "$p" 2>/dev/null ||
            stat -f '%N ino=%i mode=%Sp nlink=%l uid=%u gid=%g flags=%f size=%z birth=%B ctime=%c mtime=%m atime=%a' "$p"
    done
} >>"$o" 2>&1
EOF
}

# --- hosts and jobs ------------------------------------------------------------------------
# prep_host HOST: this run's directories, its testvm/ and stick/ copy, and the job files.
prep_host() {
    local h=$1
    [[ " $TOUCHED " == *" $h "* ]] && return 0
    TOUCHED+=" $h"
    say "$h: ~/$R and ~/$WT (the Mac's testvm/ and stick/)"
    x hssh "$h" "mkdir -p $R/jobs $R/logs $R/s2 $R/s3 $WT" || die "$h: couldn't make ~/$R"
    hput "$h" "$WT" "$REPO/testvm" "$REPO/stick"
    # compare.py takes its guests from its testvm/'s parent: the shared vms/ and friends, linked.
    x hssh "$h" "cd $WT && for d in vms vms-bko-build netbsd-tools img-media; do [ -e ../\$d ] && [ ! -e \$d ] && ln -s ../\$d \$d; done; true" ||
        die "$h: couldn't link the guests into ~/$WT"
    hput "$h" "$R" "$LOGDIR/remote/jobs.sh" "$LOGDIR/remote/roottests.sh" "$LOGDIR/remote/diag.sh" "$LOGDIR/remote/diag-stat.sh"
}
SRC_ON=""
push_src() {  # HOST: the Mac's two working trees to RUN/src
    [[ " $SRC_ON " == *" $1 "* ]] && return 0
    SRC_ON+=" $1"
    hput "$1" "$R/src" "$CORE" "$RUSTIC"
}
# The jobs this run started: name, host, state (run, done), status, start time,
# the time cmp-run.sh first gave it room (the host's JOB.granted).
JN=() JH=() JS=() JR=() JT=() JG=()
HDR=""
hv() { HDR+="$1=$(printf %q "$2")"$'\n'; }
ha() { local n=$1; shift; HDR+="$n=($(q "$@"))"$'\n'; }
# job NAME HOST KIND: write LOG_DIR/jobs/NAME.sh from HDR, send it, start it detached.
job() {
    local n=$1 h=$2 k=$3 f=$LOGDIR/jobs/$1.sh
    mkdir -p "$LOGDIR/jobs"
    { echo '#!/usr/bin/env bash'
      echo "# $n on $h, written by testvm/compare/rustic-exact-round.sh ($RUN)"
      printf 'JOB=%q KIND=%q RUN=%q TC=%q\n' "$n" "$k" "$RUN" "$TC"
      printf '%s' "$HDR"
      echo '. "$HOME/backotter-work/$RUN/jobs.sh" || exit 2'
      echo 'job_run'; } >"$f"
    HDR=""
    hput "$h" "$R/jobs" "$f"
    [[ $h == chopin ]] && chopin_cool
    say "start $n on $h ($LOGDIR/jobs/$n.sh)"
    x hssh "$h" "setsid -f bash $R/jobs/$n.sh </dev/null >/dev/null 2>&1" || die "couldn't start $n on $h"
    JN+=("$n") JH+=("$h") JS+=(run) JR+=("") JT+=("$(date +%s)") JG+=("")
}
# wait_jobs: poll until every job started has an exit status (or ran past --job-hours).
wait_jobs() {
    local i n h out line left last=0 now jn jk jv
    [[ ${#JN[@]} -gt 0 ]] || return 0
    if [[ $DRY == 1 ]]; then
        for i in "${!JN[@]}"; do [[ ${JS[$i]} == run ]] && JS[i]="done" JR[i]=dry; done
        return 0
    fi
    while :; do
        left=0
        for h in $ALL_HOSTS; do
            n=""
            for i in "${!JN[@]}"; do [[ ${JH[$i]} == "$h" && ${JS[$i]} == run ]] && n+=" ${JN[$i]}"; done
            [[ -n $n ]] || continue
            out=$(hssh "$h" "cd $R/logs && for j in$n; do if [ -s \$j.exit ]; then echo \"\$j exit \$(cat \$j.exit)\";
                elif [ -s \$j.granted ]; then echo \"\$j granted \$(cat \$j.granted)\"; fi; done; true" 2>/dev/null) || {
                say "$h didn't answer (polling again)"; left=1; continue; }
            while read -r line; do
                [[ -n $line ]] || continue
                read -r jn jk jv <<<"$line"
                for i in "${!JN[@]}"; do
                    [[ ${JN[$i]} == "$jn" && ${JS[$i]} == run ]] || continue
                    if [[ $jk == exit ]]; then
                        JS[i]="done" JR[i]=${jv:-?}
                        say "done ${JN[$i]} on $h: exit ${JR[$i]} ($(( ($(date +%s) - JT[i]) / 60 )) min)"
                    elif [[ $jk == granted && -z ${JG[$i]} && $jv =~ ^[0-9]+$ ]]; then
                        JG[i]=$jv
                    fi
                done
            done <<<"$out"
        done
        now=$(date +%s)
        for i in "${!JN[@]}"; do
            [[ ${JS[$i]} == run ]] || continue
            # From when it first had room; one still in line, from its start, twice as long.
            if [[ -n ${JG[$i]} ]] && (( now - JG[i] > JOB_HOURS * 3600 )); then
                say "watchdog: ${JN[$i]} on ${JH[$i]} ran over $JOB_HOURS h since it had room: stopping it"
            elif [[ -z ${JG[$i]} ]] && (( now - JT[i] > 2 * JOB_HOURS * 3600 )); then
                say "watchdog: ${JN[$i]} on ${JH[$i]} got no room in $((2 * JOB_HOURS)) h: stopping it"
            else
                left=1; continue
            fi
            stop_job "$i"
            JS[i]="done" JR[i]=timeout
        done
        [[ $left == 1 ]] || return 0
        if (( now - last >= 600 )); then
            last=$now
            for i in "${!JN[@]}"; do
                [[ ${JS[$i]} == run ]] && say "  running ${JN[$i]} on ${JH[$i]}, $(( (now - JT[i]) / 60 )) min: $(
                    hssh "${JH[$i]}" "tail -n 1 $R/logs/${JN[$i]}.out 2>/dev/null | cut -c1-120" 2>/dev/null)"
            done
        fi
        sleep 60
    done
}
jrc() { local i; for i in "${!JN[@]}"; do [[ ${JN[$i]} == "$1" ]] && { echo "${JR[$i]}"; return 0; }; done; echo none; }
jhost() { local i; for i in "${!JN[@]}"; do [[ ${JN[$i]} == "$1" ]] && { echo "${JH[$i]}"; return 0; }; done; return 0; }
# stop_job INDEX: if the job hasn't ended, TERM to its process group (the PID it
# recorded, if /proc says that is still its jobs/JOB.sh) and to its guest's
# vm-run (step 2's JOB.vmpid, if that is still a vm-run or console-boot.py).
stop_job() {
    local n=${JN[$1]} h=${JH[$1]}
    [[ $DRY == 0 ]] || return 0
    hssh "$h" "cd $R/logs 2>/dev/null || exit 0; [ -s $n.exit ] && exit 0
        p=\$(cat $n.pid 2>/dev/null)
        if [ -n \"\$p\" ] && tr '\\0' ' ' </proc/\$p/cmdline 2>/dev/null | grep -q 'jobs/$n\.sh'; then
            kill -TERM -- -\$p && echo \"TERM to process group \$p\"; fi
        v=\$(cat $n.vmpid 2>/dev/null)
        if [ -n \"\$v\" ] && tr '\\0' ' ' </proc/\$v/cmdline 2>/dev/null | grep -qE 'vm-run|console-boot'; then
            { kill -TERM \$v 2>/dev/null || sudo -n kill -TERM \$v; } && echo \"TERM to vm-run \$v\"; fi
        true" 2>/dev/null | sed "s/^/    $h $n: /" || true
}
# On any exit (a STOP, Ctrl-C, TERM, or the end): the jobs this run started
# that still run are stopped, build-round.sh too, and what is kept is said.
BR_PID="" CAF_PID="" ENDED=0
# shellcheck disable=SC2317  # trap handlers
on_exit() {
    local rc=$? i ran=""
    trap - EXIT INT TERM
    for i in "${!JN[@]}"; do
        [[ ${JS[$i]} == run ]] || continue
        ran+=" ${JN[$i]}@${JH[$i]}"
        stop_job "$i"
    done
    [[ -n $ran ]] && say "stopped the jobs still running:$ran"
    [[ -n $BR_PID ]] && kill -TERM "$BR_PID" 2>/dev/null && say "stopped build-round.sh (pid $BR_PID)"
    [[ -n $CAF_PID ]] && kill "$CAF_PID" 2>/dev/null
    if [[ $ENDED == 0 ]]; then
        [[ -n $TOUCHED && $DRY == 0 ]] && say "kept for a look: ~/$R and ~/$WT on$TOUCHED"
        say "logs: $LOGDIR (driver.log)"
    fi
    exit "$rc"
}
# shellcheck disable=SC2317
on_signal() { say "interrupted"; exit 130; }
trap on_exit EXIT
trap on_signal INT TERM

# Ports this run gave out, per host (HOST:PORT lines), from LO+50..LO+79 of the
# host's slot_range (LO+43..49 are bs2's on Cinqcent, LO+80..99 drive.py's).
PORTS=""
PORT_GOT=""
alloc_port() {  # HOST: PORT_GOT, a free port of the host's
    local h=$1 lo p busy
    lo=$(lo_of "$h") || die "no port range for $h"
    busy=$(hssh "$h" "ss -Hltn 2>/dev/null | awk '{ print \$4 }' | sed 's/.*://'" 2>/dev/null) || busy=""
    for (( p = lo + 50; p < lo + 80; p++ )); do
        [[ $PORTS == *" $h:$p "* ]] && continue
        grep -qx "$p" <<<"$busy" && continue
        PORTS+=" $h:$p "
        PORT_GOT=$p
        return 0
    done
    die "no free port on $h in $((lo + 50))-$((lo + 79))"
}
# place D M GUEST NEEDS: pick-host's candidates for the job, best first; of those,
# the one this run has given the fewest jobs (ties: pick-host's order).
ASSIGNED=""
place() {
    local d=$1 m=$2 g=$3 n=$4 c h hc k best="" bs=999 i=0 args=(--all --field name --reserve "$1" "$2")
    [[ -n $g ]] && args+=(--guest "$g")
    [[ -n $n ]] && args+=(--needs "$n")
    [[ -n $POOL ]] && args+=(--hosts "$POOL")
    c=$(python3 -I -B "$REPO/testvm/pick-host" "${args[@]}" 2>>"$LOGDIR/pick-host.log") || c=""
    if [[ -z $c ]]; then
        echo "$(date +%T) pick-host $(q "${args[@]}") gave nothing: falling back" >>"$LOGDIR/pick-host.log"
        for h in $(echo "${POOL:-$ALL_HOSTS}" | tr ',' ' '); do
            # The host's own ceiling, as pick-host asks it: cmp-run.sh refuses a
            # --reserve over it at once.  No figures: no job there (as pick-host).
            hc=$(hssh "$h" "cd backotter-work && testvm/compare/cmp-run.sh --ceiling" 2>/dev/null |
                awk '$1 ~ /^[0-9]/ && $2 ~ /^[0-9]/ { print $1, $2; exit }') || hc=""
            if [[ -z $hc ]]; then
                echo "$(date +%T) fallback: no cmp-run.sh --ceiling from $h: left out" >>"$LOGDIR/pick-host.log"
                continue
            fi
            awk -v d="$d" -v m="$m" -v c="$hc" 'BEGIN { split(c, f, " "); exit !(d <= f[1] && m <= f[2]) }' || continue
            [[ -z $g ]] || hssh "$h" "test -e backotter-work/vms/$g" 2>/dev/null || continue
            c+=" $h"
        done
    fi
    for h in $c; do
        k=$(grep -cx "$h" <<<"$ASSIGNED" || true)
        (( k * 2 + i < bs )) && { best=$h; bs=$((k * 2 + i)); }
        i=$((i + 1))
    done
    echo "$best"
}

# The two working trees' digest: HEADs, uncommitted changes, untracked files.
src_digest() {
    local r
    for r in "$CORE" "$RUSTIC"; do
        git -C "$r" rev-parse HEAD
        git -C "$r" diff HEAD --binary
        git -C "$r" ls-files --others --exclude-standard
    done | shasum -a 256 | cut -d' ' -f1
}
is_clean() { [[ -z $(git -C "$1" status --porcelain=v1 --untracked-files=all) ]]; }
tree_state() { if is_clean "$1"; then echo clean; else echo "with uncommitted changes"; fi; }
tree_desc() {  # REPO: "SHA, clean" or "SHA, with uncommitted changes (the Mac's working tree)"
    local c
    c=$(git -C "$1" rev-parse HEAD)
    if is_clean "$1"; then echo "$c, clean"; else echo "$c, with uncommitted changes (the Mac's working tree)"; fi
}
tree_line() {  # REPO: "HEAD SHORT (SUBJECT), clean" or "..., with uncommitted changes", for the log
    echo "HEAD $(git -C "$1" log -1 --format='%h (%s)'), $(tree_state "$1")"
}
# only_files REPO FROM TO PATH...: TO descends from FROM (or is it) and git diff FROM TO touches only the PATHs.
only_files() {
    local r=$1 a=$2 b=$3 f bad=0; shift 3
    git -C "$r" merge-base --is-ancestor "$a" "$b" 2>/dev/null || { say "$r: ${b:0:7} doesn't descend from ${a:0:7}"; return 1; }
    while IFS= read -r f; do
        [[ -n $f ]] || continue
        [[ " $* " == *" $f "* ]] || { say "$r: $f changed between ${a:0:7} and ${b:0:7}"; bad=1; }
    done < <(git -C "$r" diff --name-only "$a" "$b")
    [[ $bad == 0 ]]
}
porcelain_is() {  # REPO PATH...: git status is exactly " M PATH" for each PATH
    local want got
    want=$(for p in "${@:2}"; do echo " M $p"; done | LC_ALL=C sort)
    got=$(git -C "$1" status --porcelain=v1 --untracked-files=all | LC_ALL=C sort)
    [[ $got == "$want" ]] || { say "$1: git status is"; echo "${got:-  (clean)}" | sed 's/^/      /'; say "  where it should be"; echo "$want" | sed 's/^/      /'; return 1; }
}

STATUS=""   # "step N: verdict" lines
result() { STATUS+="  step $1: $2"$'\n'; say "step $1: $2"; }
FAILED=0

# --- step 1 -----------------------------------------------------------------------------------
step1_start() {
    say "== step 1: build and test the trees as they are (HEAD plus any uncommitted changes)"
    local h=$HOST1 b bp=""
    # Step 1 builds (prep-rustic-src.sh, cargo test): only a build-role host,
    # of --hosts when it is given (choubox is a default host with no build role).
    for b in $BUILD_ALL; do [[ -z $POOL || ",$POOL," == *",$b,"* ]] && bp+=${bp:+,}$b; done
    [[ -n $h || -n $bp ]] || die "step 1: no build host among --hosts ($POOL; build hosts: $BUILD_ALL)"
    [[ -n $h ]] || h=$(POOL=$bp place 3 6 "" "")
    [[ -n $h ]] || die "step 1: pick-host found no host"
    ASSIGNED+=$'\n'"$h"
    S1_HOST=$h
    src_digest >"$LOGDIR/step1-src.digest"
    say "step 1 on $h; rustic_core $(tree_line "$CORE"); rustic $(tree_line "$RUSTIC")"
    prep_host "$h"
    push_src "$h"
    hv RUSTIC_VER "$RUSTIC_VER"
    hv RUSTIC_SRC "$(tree_desc "$RUSTIC")"
    hv CORE_SRC "$(tree_desc "$CORE")"
    job s1 "$h" s1
}
step1_judge() {
    local h=$S1_HOST rc
    rc=$(jrc s1)
    mkdir -p "$LOGDIR/step1"
    hget "$h" "$R/logs/s1.out" "$LOGDIR/step1"
    hget "$h" "$R/logs/s1/" "$LOGDIR/step1/logs"
    hget "$h" "$R/bin/linux/" "$LOGDIR/step1/bin"
    if [[ $rc == dry ]]; then result 1 "dry run"; return 0; fi
    if [[ $rc == 0 && -f $LOGDIR/step1/logs/PASS ]]; then
        { echo "digest $(cat "$LOGDIR/step1-src.digest")"; echo "host $h"; echo "date $(date '+%F %T')"
          echo "rustic_core $(tree_desc "$CORE")"; echo "rustic $(tree_desc "$RUSTIC")"
          echo "binary $(cat "$LOGDIR/step1/logs/rustic-bko.sha256" 2>/dev/null)"; } >"$LOGDIR/step1.pass"
        [[ $(src_digest) == "$(cat "$LOGDIR/step1-src.digest")" ]] ||
            { rm -f "$LOGDIR/step1.pass"; result 1 "FAIL: the working trees changed while step 1 ran"; return 1; }
        result 1 "PASS (binary $LOGDIR/step1/bin/rustic-bko, logs $LOGDIR/step1/logs)"
        return 0
    fi
    result 1 "FAIL (exit $rc): $LOGDIR/step1/s1.out, $LOGDIR/step1/logs; kept on $h: ~/$R"
    return 1
}

# --- step 2 -----------------------------------------------------------------------------------
# with_image GUEST FILE...: of pick-host's hosts for GUEST, the first that has every FILE (in ~/backotter-work).
with_image() {
    local g=$1 h c t; shift
    c=$(python3 -I -B "$REPO/testvm/pick-host" --all --field name --reserve 2 10 --guest "$g" 2>>"$LOGDIR/pick-host.log") ||
        c=$ALL_HOSTS
    t=$(for f in "$@"; do printf 'test -f backotter-work/%s && ' "$f"; done)
    # A host this run has given no job yet first.
    for h in $c; do
        grep -qx "$h" <<<"$ASSIGNED" && continue
        hssh "$h" "${t}true" 2>/dev/null && { echo "$h"; return 0; }
    done
    for h in $c; do
        hssh "$h" "${t}true" 2>/dev/null && { echo "$h"; return 0; }
    done
    return 1
}
step2_start() {
    say "== step 2: the NetBSD trace of test_restore_exact_twice_changes_nothing (and FreeBSD's)"
    local f=$CORE/crates/core/tests/integration/restore.rs hook nb fb
    mkdir -p "$LOGDIR/step2"
    # The hook goes in the guest's copy only: after each of the test's two restores.
    hook='    if let Some(p) = std::env::var_os("BKO_DIAG_STAT") { let _ = std::process::Command::new(p).arg(target.path()).arg("@N@").status(); }'
    awk -v hook="$hook" '
        /^fn test_restore_exact_twice_changes_nothing\(/ { inf = 1 }
        { print }
        inf && $0 == "    restore_with(&repo, target.path(), exact, late_atime)?;" { n++; h = hook; sub(/@N@/, n, h); print h }
        inf && /^}$/ { inf = 0 }
        END { exit n != 2 }' "$f" >"$LOGDIR/step2/restore-diag.rs" ||
        die "step 2: test_restore_exact_twice_changes_nothing in $f no longer has its two restore_with lines"
    nb=$NBHOST fb=$FBHOST
    [[ -n $nb ]] || nb=$(with_image netbsd11 vms-bko-build/netbsd11-rust.qcow2 vms-bko-build/netbsd11.id_ed25519) ||
        die "step 2: no host has vms-bko-build/netbsd11-rust.qcow2 and its key"
    ASSIGNED+=$'\n'"$nb"
    [[ -n $fb ]] || fb=$(with_image freebsd15-ufs vms-bko-build/freebsd14-rust.qcow2 vms-bko-build/freebsd14.id_ed25519) ||
        die "step 2: no host has vms-bko-build/freebsd14-rust.qcow2 and its key, and freebsd15-ufs"
    ASSIGNED+=$'\n'"$fb"
    for o in netbsd:$nb freebsd:$fb; do
        local os=${o%%:*} h=${o#*:}
        prep_host "$h"
        push_src "$h"
        hput "$h" "$R/s2" "$LOGDIR/step2/restore-diag.rs"
        alloc_port "$h"
        hv OSN "$os"
        hv PORT "$PORT_GOT"
        job "s2-$os" "$h" s2
    done
}
step2_judge() {
    local os h rc ok=1
    for os in netbsd freebsd; do
        h=$(jhost "s2-$os")
        rc=$(jrc "s2-$os")
        mkdir -p "$LOGDIR/step2/$os"
        hget "$h" "$R/logs/s2-$os.out" "$LOGDIR/step2/$os"
        hget "$h" "$R/s2/$os/" "$LOGDIR/step2/$os/host"
        [[ $rc == dry ]] && continue
        if [[ -f $LOGDIR/step2/$os/host/out.tgz ]]; then
            mkdir -p "$LOGDIR/step2/$os/out-dir"
            tar -xzf "$LOGDIR/step2/$os/host/out.tgz" -C "$LOGDIR/step2/$os/out-dir" || true
        fi
        if [[ $rc == 0 && -f $LOGDIR/step2/$os/host/COLLECTED ]]; then
            say "$os ($h): $LOGDIR/step2/$os/out-dir/out/ (LABEL-second.kdump, -calls.txt, -callcounts.txt, -stat.txt, -test.log)"
            sed -n '/^== /,$p' "$LOGDIR/step2/$os/host/run.log" 2>/dev/null | sed 's/^/    /'
        else
            ok=0
            say "$os ($h): not collected (exit $rc): $LOGDIR/step2/$os/s2-$os.out"
        fi
    done
    if [[ ${rc:-} == dry ]]; then result 2 "dry run"; return 0; fi
    if [[ $ok == 1 ]]; then result 2 "diagnosis collected: $LOGDIR/step2/{netbsd,freebsd}/out-dir/out/"; return 0; fi
    result 2 "FAIL: diagnosis not collected (logs: $LOGDIR/step2; kept: ~/$R on $(jhost s2-netbsd) $(jhost s2-freebsd))"
    return 1
}

# --- step 3 -----------------------------------------------------------------------------------
step3() {
    say "== step 3: commit rustic_core $CORE_VER and rustic $RUSTIC_VER"
    local S=$LOGDIR/step3 ch rh core_mode rustic_mode new short lockhost m body tailtxt files=()
    mkdir -p "$S"
    [[ $(git -C "$CORE" branch --show-current) == backotter ]] || die "step 3: $CORE is not on branch backotter"
    [[ $(git -C "$RUSTIC" branch --show-current) == backotter ]] || die "step 3: $RUSTIC is not on branch backotter"
    ch=$(git -C "$CORE" rev-parse HEAD) rh=$(git -C "$RUSTIC" rev-parse HEAD)

    # rustic_core: commit = (a) ba93d6e with the eight files changed, committed here;
    # mine = this step's own two commits, from an earlier run; held = (b) a clean
    # descendant of ba93d6e that changes only the eight files (ad51297, 3fd3921): no commit.
    if [[ $ch == "$CORE_BASE" ]]; then
        porcelain_is "$CORE" "${CORE_CODE[@]}" "${CORE_VERFILES[@]}" || die "step 3: rustic_core has other changes than the $CORE_NF files: refusing"
        core_mode=commit
    elif ! is_clean "$CORE"; then
        git -C "$CORE" status --short | sed 's/^/      /'
        die "step 3: rustic_core's HEAD is ${ch:0:7}, not ba93d6e, and its tree has uncommitted changes (above): refusing"
    elif [[ $(git -C "$CORE" rev-parse HEAD~2 2>/dev/null) == "$CORE_BASE" && $(git -C "$CORE" log -1 --format=%s) == "$CORE_MSG2" &&
        $(git -C "$CORE" log -1 --format=%s HEAD~1) == "$CORE_MSG1" ]]; then
        core_mode=mine
        say "rustic_core: this step's two commits are there already ($(git -C "$CORE" log -1 --format=%h)): on to rustic"
    elif only_files "$CORE" "$CORE_BASE" "$ch" "${CORE_CODE[@]}" "${CORE_VERFILES[@]}"; then
        core_mode=held
        say "rustic_core: $(tree_line "$CORE"); its diff from ba93d6e touches only the $CORE_NF files: no rustic_core commit"
    else
        die "step 3: rustic_core's HEAD ${ch:0:7} is neither ba93d6e with the $CORE_NF files changed nor a clean descendant of it that changes only them (above): refusing"
    fi
    grep -q "^version = \"$CORE_VER\"\$" "$CORE/crates/core/Cargo.toml" || die "step 3: rustic_core's crates/core/Cargo.toml isn't version $CORE_VER"

    # rustic: done = this step's lock commit on top, pinning rustic_core's HEAD;
    # commit = c517a69 with its three files changed (they and Cargo.lock are
    # committed); held = a clean descendant of c517a69 that changes only those
    # three (7625a3c: Cargo.lock alone is committed).  c517a69's own subject has
    # the lock commit's prefix too, hence rh != c517a69.
    if [[ $rh != "$RUSTIC_BASE" ]] && is_clean "$RUSTIC" && [[ $(git -C "$RUSTIC" log -1 --format=%s) == "$RUSTIC_MSG_PREFIX"* ]] &&
        [[ $(grep -c "#$ch\"" "$RUSTIC/Cargo.lock") == 2 ]] && only_files "$RUSTIC" "$RUSTIC_BASE" "$rh" "${RUSTIC_FILES[@]}" Cargo.lock; then
        result 3 "done already: rustic $(git -C "$RUSTIC" log -1 --format='%h %s')"
        return 0
    fi
    if [[ $rh == "$RUSTIC_BASE" ]]; then
        porcelain_is "$RUSTIC" "${RUSTIC_FILES[@]}" || die "step 3: rustic has other changes than its three files: refusing"
        rustic_mode=commit files=("${RUSTIC_FILES[@]}" Cargo.lock)
    elif ! is_clean "$RUSTIC"; then
        git -C "$RUSTIC" status --short | sed 's/^/      /'
        die "step 3: rustic's HEAD is ${rh:0:7}, not c517a69, and its tree has uncommitted changes (above): refusing"
    elif only_files "$RUSTIC" "$RUSTIC_BASE" "$rh" "${RUSTIC_FILES[@]}"; then
        rustic_mode=held files=(Cargo.lock)
        say "rustic: $(tree_line "$RUSTIC"), over c517a69 touching only ${RUSTIC_FILES[*]}: only Cargo.lock is committed"
    else
        die "step 3: rustic's HEAD ${rh:0:7} is neither c517a69 with its three files changed nor a clean descendant of it that changes only them (above): refusing"
    fi
    grep -q "^version = \"$RUSTIC_VER\"\$" "$RUSTIC/Cargo.toml" || die "step 3: rustic's Cargo.toml isn't version $RUSTIC_VER"
    grep -q "^bko_core=$CORE_VER\$" "$RUSTIC/build-backotter.sh" || die "step 3: rustic's build-backotter.sh doesn't want $CORE_VER"
    [[ $(git -C "$RUSTIC" show "$RUSTIC_BASE:Cargo.lock" | grep -c "#$CORE_BASE\"") == 2 ]] ||
        die "step 3: c517a69's Cargo.lock doesn't pin ba93d6e for rustic_core and walkdir"

    # Step 1's pass on these very trees (not for "mine": those commits are this step's own, made after it checked).
    if [[ $core_mode != mine && $FORCE == 0 ]]; then
        local pass=$LOGDIR/step1.pass
        if [[ ! -f $pass ]]; then
            pass=$(ls -t "$(dirname "$LOGDIR")"/bko-rustic-round-*/step1.pass 2>/dev/null | head -n 1 || true)
        fi
        if [[ -z $pass || ! -f $pass ]]; then
            [[ $DRY == 1 ]] || die "step 3: no step 1 pass (LOG_DIR/step1.pass; --log-dir, or --force-commit)"
            say "dry run: no step 1 pass yet; a real run stops here"
        elif [[ $(sed -n 's/^digest //p' "$pass") != "$(src_digest)" ]]; then
            [[ $DRY == 1 ]] || die "step 3: the trees changed since step 1 passed ($pass): run step 1 again"
            say "dry run: the trees changed since step 1 passed ($pass); a real run stops here"
        else
            say "step 1 passed on these trees: $pass"
        fi
    fi

    # rustic_core (a): the fixes and tests, then the version and BACKOTTER.md.
    if [[ $core_mode == commit ]]; then
        printf '%s\n\n%s\n\n%s\n\n%s\n\n%s\n' "$CORE_MSG1" \
"In a case-insensitive directory a name the walk sees (SAME) can be the
entry of a snapshot name it doesn't (same). 0.13.0+backotter.13's --delete
removed it once the walk was done, after planning the snapshot's file
there, and then panicked reading that file's contents (exit 134, no
metadata pass). Such an entry is now taken as there, and --delete leaves
it (Aliases: paths folded to lower case, the same device and inode)." \
"The later names of a hard-link group --exact breaks (LinkGroup) are in
the way too, as restic-bko's linkedInTheWay: .13 only counted them for the
group, so an unexpected name of such a name's old file kept its
restricting flags and stayed." \
"A file the restore planned to take blobs from that can't be read when it
does (gone since the plan) is an error for each file that was to get
them, and the restore goes on (restore_contents unwrapped it)." \
"$COAUTHOR" >"$S/core-msg1.txt"
        printf '%s\n\n%s\n\n%s\n' "$CORE_MSG2" \
"BACKOTTER.md also lists the known limits the review of .13 found, and
test_restore_exact_twice_changes_nothing's failure on NetBSD 11's FFS
(every regular file's change time moves on the second --exact restore),
not yet traced." \
"$COAUTHOR" >"$S/core-msg2.txt"
        say "rustic_core: commit 1 (${CORE_CODE[*]}): $CORE_MSG1"
        x git -C "$CORE" add -- "${CORE_CODE[@]}" || die "step 3: git add in $CORE failed (see git status there)"
        x git -C "$CORE" commit -q -F "$S/core-msg1.txt" ||
            die "step 3: rustic_core's first commit failed: nothing committed; ${CORE_CODE[*]} are staged (git -C $CORE reset -q undoes that)"
        say "rustic_core: commit 2 (${CORE_VERFILES[*]}): $CORE_MSG2"
        x git -C "$CORE" add -- "${CORE_VERFILES[@]}" || die "step 3: git add in $CORE failed after commit 1 (see git status there)"
        x git -C "$CORE" commit -q -F "$S/core-msg2.txt" ||
            die "step 3: rustic_core's second commit failed: commit 1 is in, ${CORE_VERFILES[*]} staged; finish it by hand (git -C $CORE commit -F $S/core-msg2.txt)"
        if [[ $DRY == 0 ]]; then
            [[ -z $(git -C "$CORE" status --porcelain) && $(git -C "$CORE" rev-parse HEAD~2) == "$CORE_BASE" ]] ||
                die "step 3: rustic_core isn't clean two commits over ba93d6e after committing"
        fi
    fi
    if [[ $DRY == 1 && $core_mode == commit ]]; then new="<rustic_core HEAD after the commits>"
    else new=$(git -C "$CORE" rev-parse HEAD); fi
    short=${new:0:7}
    say "rustic_core is at $new"

    # rustic's commit message: (a)'s as it was; (b)'s names the round's two fixes.
    if [[ $rustic_mode == held ]]; then
        tailtxt="Only the lock: version $RUSTIC_VER, BACKOTTER.md and build-backotter.sh's
bko_core=$CORE_VER are in ${rh:0:7}."
    else
        tailtxt="Version $RUSTIC_VER; build-backotter.sh wants $CORE_VER."
    fi
    if [[ $core_mode == held ]]; then
        local nc ncw=commits
        nc=$(git -C "$CORE" rev-list --count "$CORE_BASE..$new")
        [[ $nc != 1 ]] || ncw=commit
        m="$RUSTIC_MSG_PREFIX $short ($CORE_VER): restore --delete's case variants and a kept group's later names"
        body="rustic_core $short ($CORE_VER, $nc $ncw over ${CORE_BASE:0:7}): restore --delete
leaves an entry that is a snapshot name's under another case in a
case-insensitive directory, and the later names of a hard-link group
--exact breaks are in the way too.

$tailtxt"
    else
        m=$(printf '%s %s (%s): restore --delete leaves a case variant of a planned entry; a kept group'"'"'s later names in the way; no panic when a planned file is gone' \
            "$RUSTIC_MSG_PREFIX" "$short" "$CORE_VER")
        body="rustic_core $short ($CORE_VER, two commits over ${CORE_BASE:0:7}): restore --delete
leaves an entry that is a snapshot name's under another case in a
case-insensitive directory (it removed it, then panicked), the later
names of a hard-link group --exact breaks are in the way too, and a
planned file that can't be read is an error, not a panic. $tailtxt"
    fi
    printf '%s\n\n%s\n\n%s\n' "$m" "$body" "$COAUTHOR" >"$S/rustic-msg.txt"

    # rustic's Cargo.lock, by cargo on a farm host, against a bare copy of that commit.
    lockhost=$HOST1
    [[ -n $lockhost ]] || lockhost=$(sed -n 's/^host //p' "$LOGDIR/step1.pass" 2>/dev/null || true)
    [[ -n $lockhost ]] || lockhost=$(place 0 2 "" "")
    [[ -n $lockhost ]] || die "step 3: no host for cargo update"
    say "Cargo.lock: cargo update on $lockhost, rustic_core and walkdir to $new"
    x git -C "$CORE" bundle create "$S/core.bundle" HEAD refs/heads/backotter || die "step 3: git bundle failed (rustic not committed)"
    prep_host "$lockhost"
    hput "$lockhost" "$R/s3" "$S/core.bundle" "$RUSTIC"
    hv CORE_NEW "$new"
    hv CORE_URL "$CORE_URL"
    job s3-lock "$lockhost" lock
    wait_jobs
    hget "$lockhost" "$R/logs/s3-lock.out" "$S"
    if [[ $DRY == 1 ]]; then
        echo "    [dry-run] fetch $lockhost:~/$R/s3/rustic/Cargo.lock, check it against c517a69's with only the"
        echo "              rustic-rs version, rustic_core's version and the two pins (to $short) changed, then:"
        x cp "$S/Cargo.lock" "$RUSTIC/Cargo.lock"
        say "rustic: commit (${files[*]}), message $S/rustic-msg.txt: $m"
        x git -C "$RUSTIC" add -- "${files[@]}"
        x git -C "$RUSTIC" commit -q -F "$S/rustic-msg.txt"
        result 3 "dry run (rustic_core: $core_mode, rustic: $rustic_mode; rustic would commit ${files[*]})"
        return 0
    fi
    [[ $(jrc s3-lock) == 0 ]] || die "step 3: cargo update failed on $lockhost: $S/s3-lock.out (rustic not committed)"
    x rsync -e "$(sshv "$lockhost")" "$(dest "$lockhost"):$R/s3/rustic/Cargo.lock" "$S/Cargo.lock" ||
        die "step 3: couldn't fetch Cargo.lock from $lockhost (rustic not committed)"
    # What it must be: c517a69's lock with the two versions and two pins moved, nothing else.
    git -C "$RUSTIC" show "$RUSTIC_BASE:Cargo.lock" | awk -v old="$CORE_BASE" -v new="$new" -v rv0="$RUSTIC_VER_OLD" -v rv="$RUSTIC_VER" \
        -v cv0="$CORE_VER_OLD" -v cv="$CORE_VER" '
        /^name = / { name = $3 }
        name == "\"rustic-rs\"" && $0 == "version = \"" rv0 "\"" { print "version = \"" rv "\""; next }
        name == "\"rustic_core\"" && $0 == "version = \"" cv0 "\"" { print "version = \"" cv "\""; next }
        { i = index($0, "#" old "\""); if (i) $0 = substr($0, 1, i) new substr($0, i + 1 + length(old)); print }' >"$S/Cargo.lock.expected"
    if ! cmp -s "$S/Cargo.lock" "$S/Cargo.lock.expected"; then
        diff -u "$S/Cargo.lock.expected" "$S/Cargo.lock" | head -n 80
        die "step 3: cargo's Cargo.lock ($S/Cargo.lock) isn't c517a69's with only rustic-rs, rustic_core and walkdir moved: rustic not committed"
    fi
    [[ $(grep -c "#$new\"" "$S/Cargo.lock") == 2 ]] || die "step 3: $S/Cargo.lock doesn't pin $new for rustic_core and walkdir"
    cp "$S/Cargo.lock" "$RUSTIC/Cargo.lock" || die "step 3: couldn't copy the lock into $RUSTIC"
    porcelain_is "$RUSTIC" "${files[@]}" || die "step 3: rustic's changes aren't ${files[*]} after the lock"
    say "rustic: commit (${files[*]}): $m"
    x git -C "$RUSTIC" add -- "${files[@]}" || die "step 3: git add in $RUSTIC failed"
    x git -C "$RUSTIC" commit -q -F "$S/rustic-msg.txt" || die "step 3: rustic's commit failed: ${files[*]} staged (git -C $RUSTIC reset -q undoes that)"
    [[ -z $(git -C "$RUSTIC" status --porcelain) && $(git -C "$RUSTIC" rev-parse HEAD~1) == "$rh" ]] ||
        die "step 3: rustic isn't clean one commit over ${rh:0:7} after committing"
    result 3 "committed (not pushed): rustic_core $(git -C "$CORE" log -1 --format=%h) ($core_mode), rustic $(git -C "$RUSTIC" log -1 --format=%h) (${files[*]})"
}

# --- step 4 -----------------------------------------------------------------------------------
# old_bin: LOG_DIR/step4/old/OS/, the c517a69/ba93d6e build of every OS run-all.sh needs.
old_bin() {
    local o=$LOGDIR/step4/old os f src
    case $OLD_ARG in
        none) echo none; return 0 ;;
        "") rm -rf "$o"; mkdir -p "$o"
            for os in linux freebsd netbsd openbsd dragonfly illumos; do
                if [[ $os == linux ]]; then src=$RX_LINUX_BIN; else src=$RX_BSD_BIN/$os; fi
                mkdir -p "$o/$os"
                for f in restic-bko rustic-bko restic-commit rustic-commit; do
                    [[ -s $src/$f ]] || die "step 4: no $src/$f for OLD_BIN (--old-bin DIR, or --old-bin none)"
                    cp -p "$src/$f" "$o/$os/$f"
                done
            done ;;
        *) [[ -d $OLD_ARG ]] || die "step 4: no --old-bin directory $OLD_ARG"
           o=$(cd "$OLD_ARG" && pwd) ;;
    esac
    for os in linux freebsd netbsd openbsd dragonfly illumos; do
        [[ $(head -n 1 "$o/$os/rustic-commit") == "$RUSTIC_BASE"* ]] &&
            grep -q "^rustic_core $CORE_BASE" "$o/$os/rustic-commit" &&
            [[ $(head -n 1 "$o/$os/restic-commit") == "$RESTIC_HEAD"* ]] ||
            die "step 4: $o/$os's commit files aren't c517a69/ba93d6e and 12a275f62"
    done
    echo "$o"
}
step4() {
    say "== step 4: the farm round $ROUND from the commits"
    local S=$LOGDIR/step4 G ch rh eh old exp=() line h n spec i
    mkdir -p "$S"
    ch=$(git -C "$CORE" rev-parse HEAD) rh=$(git -C "$RUSTIC" rev-parse HEAD) eh=$(git -C "$RESTIC" rev-parse HEAD)
    # The HEADs as they are now, after step 3 (never the ba93d6e/c517a69 constants):
    # rustic_core clean over ba93d6e; rustic clean, step 3's lock commit on top, pinning it.
    local core_ok=0 rustic_ok=0
    if [[ $ch != "$CORE_BASE" ]] && is_clean "$CORE" && git -C "$CORE" merge-base --is-ancestor "$CORE_BASE" "$ch"; then core_ok=1; fi
    if [[ $core_ok == 1 && $rh != "$RUSTIC_BASE" ]] && is_clean "$RUSTIC" && git -C "$RUSTIC" merge-base --is-ancestor "$RUSTIC_BASE" "$rh" &&
        [[ $(git -C "$RUSTIC" log -1 --format=%s) == "$RUSTIC_MSG_PREFIX"* && $(grep -c "#$ch\"" "$RUSTIC/Cargo.lock") == 2 ]]; then
        rustic_ok=1
    fi
    if [[ $rustic_ok == 1 ]]; then
        say "rustic_core $(tree_line "$CORE"); rustic $(tree_line "$RUSTIC") (its Cargo.lock pins that rustic_core)"
    elif [[ $DRY == 1 ]]; then
        say "dry run: step 3's commits aren't all there yet; the commands below take what step 3 will commit"
        if [[ $core_ok == 1 ]]; then say "rustic_core $(tree_line "$CORE") is the one step 3's lock will pin"
        else ch="<rustic_core HEAD after step 3>"; fi
        rh="<rustic HEAD after step 3's Cargo.lock commit>"
    else
        die "step 4: rustic_core and rustic aren't step 3's clean commits (rustic_core ${ch:0:7}, rustic ${rh:0:7}, whose Cargo.lock must pin it): run step 3"
    fi
    [[ $eh == "$RESTIC_HEAD" && -z $(git -C "$RESTIC" status --porcelain) ]] ||
        die "step 4: ~/git/restic isn't a clean 12a275f62 (it is $eh)"
    old=$(old_bin) || die "step 4: no OLD_BIN (above)"
    [[ $old == none || ( $old == /* && -d $old && -n $(ls -A "$old") ) ]] || die "step 4: OLD_BIN '$old' isn't a directory with binaries"
    say "OLD_BIN: $old"

    # Clean clones at the commits: build-round.sh syncs what is committed, not the working trees.
    G=$S/git
    if [[ $DRY == 0 ]]; then
        rm -rf "$G"; mkdir -p "$G" || die "step 4: no $G"
        for spec in "restic:$eh" "rustic_core:$ch" "rustic:$rh"; do
            if ! { git clone -q --shared --no-checkout "$GIT/${spec%%:*}" "$G/${spec%%:*}" &&
                git -C "$G/${spec%%:*}" checkout -q --detach "${spec#*:}"; }; then
                die "step 4: couldn't clone ${spec%%:*} at ${spec#*:} into $G"
            fi
        done
    else
        echo "    [dry-run] git clone --shared --no-checkout $GIT/{restic,rustic_core,rustic} $G/ and check out $eh, $ch, $rh"
    fi
    local bh dist="" gv g125=0
    bh=$(echo "${BUILD_HOSTS:-$BUILD_ALL}" | tr ',' ' ')
    # The job hosts that build nothing get the round's bin from build-round.sh:
    # with or without --build-hosts, since a default host need not be a build
    # host (choubox has no build role), and place() may give it a job.
    for h in $(echo "${POOL:-$ALL_HOSTS}" | tr ',' ' '); do [[ " $bh " == *" $h "* ]] || dist+=${dist:+,}$h; done
    # restic-bko goes to a build host with Go, which takes go on PATH, else the
    # newest ~/sdk/go*/bin/go (build-round.sh's choice, and BKO_GO doesn't reach
    # it): each such host's must be go1.25.10, checked now, not after the round.
    for h in $bh; do
        # shellcheck disable=SC2016  # expanded there
        gv=$(hssh "$h" 'go=$(command -v go 2>/dev/null) || go=$(ls -d "$HOME"/sdk/go*/bin/go 2>/dev/null | sort -V | tail -n 1)
            [ -n "$go" ] && [ -x "$go" ] && "$go" version' 2>/dev/null) || gv=""
        say "$h: ${gv:-no Go (gets no restic unit)}"
        [[ -n $gv ]] || continue
        if [[ $gv == *" go1.25.10 "* ]]; then g125=1
        elif [[ ${ALLOW_OTHER_GO:-} != 1 && $DRY == 0 ]]; then
            die "step 4: $h would build restic-bko with $gv, not go1.25.10 (--build-hosts without $h, or ALLOW_OTHER_GO=1)"
        fi
    done
    [[ $g125 == 1 || ${ALLOW_OTHER_GO:-} == 1 || $DRY == 1 ]] || die "step 4: no build host has go1.25.10 (ALLOW_OTHER_GO=1 takes another)"
    for h in $bh; do prep_host "$h"; done
    [[ " $bh " == *" chopin "* ]] && chopin_cool
    say "build-round.sh $ROUND (log $S/build-round.log)${dist:+; --dist-hosts $dist}"
    local only=restic,linux,dragonfly,freebsd,netbsd,openbsd,illumos
    if [[ $DRY == 1 ]]; then
        x env BKO_GIT="$G" BKO_REMOTE_DIR="$WT" "$REPO/stick/build-round.sh" ${BUILD_HOSTS:+--hosts "$BUILD_HOSTS"} \
            ${dist:+--dist-hosts "$dist"} --only "$only" "$ROUND"
        exp=(EXPECT_RESTIC_COMMIT="$RESTIC_HEAD" EXPECT_RESTIC_DIGEST="<from build-round.sh>" EXPECT_RUSTIC_COMMIT="$rh"
            EXPECT_RUSTIC_CORE_COMMIT="$ch")
    else
        env BKO_GIT="$G" BKO_REMOTE_DIR="$WT" "$REPO/stick/build-round.sh" ${BUILD_HOSTS:+--hosts "$BUILD_HOSTS"} \
            ${dist:+--dist-hosts "$dist"} --only "$only" "$ROUND" >"$S/build-round.log" 2>&1 &
        BR_PID=$!
        local brc=0
        wait "$BR_PID" || brc=$?
        BR_PID=""
        sed -n '/^HOST\tBINARY/,$p' "$S/build-round.log" | sed 's/^/    /'
        [[ $brc == 0 ]] || { result 4 "FAIL: build-round.sh exit $brc ($S/build-round.log)"; return 1; }
        line=$(grep '^EXPECT_RESTIC_COMMIT=' "$S/build-round.log" | tail -n 1)
        for i in $line; do
            [[ $i =~ ^EXPECT_[A-Z_]+=[0-9a-f]+$ ]] || die "step 4: build-round.sh's EXPECT line has $i"
            exp+=("$i")
        done
        [[ ${#exp[@]} == 4 && " ${exp[*]} " == *" EXPECT_RUSTIC_CORE_COMMIT=$ch "* && " ${exp[*]} " == *" EXPECT_RUSTIC_COMMIT=$rh "* ]] ||
            die "step 4: build-round.sh's EXPECT line ($line) isn't rustic $rh, rustic_core $ch"
        h=${bh%% *}
        if ! hssh "$h" "grep -q go1.25.10 backotter-work/round$ROUND/bin/linux/restic-commit"; then
            [[ ${ALLOW_OTHER_GO:-} == 1 ]] || die "step 4: round$ROUND's restic-bko wasn't built by go1.25.10 (ALLOW_OTHER_GO=1 takes it)"
            say "restic-bko built by another Go than go1.25.10 (ALLOW_OTHER_GO=1)"
        fi
    fi
    exp+=(EXPECT_RESTIC_VERSION="$RESTIC_VER" EXPECT_RUSTIC_VERSION="$RUSTIC_VER")

    # The jobs: NAME;KIND;DISK;MEM;GUEST;NEEDS;PORT OFFSET;PLATFORMS;ONLY;EXTRA (run-all's env, or exact's tool)
    local jobs='lx-ext4;runall;8;4;;;;linux;;LINUX_FS=ext4
lx-btrfs;runall;8;4;;;;linux;;LINUX_FS=btrfs
lx-xfs;runall;8;4;;;;linux;;LINUX_FS=xfs
nonroot-linux;runall;2;2;;zfs;;linux;;LINUX_FS=nonroot
sparse-ext4;runall;20;4;;;;sparse;;SPARSE_FS=ext4
zfs-ubuntu24;runall;8;8;ubuntu24;;17;zfsguests;^zfsguests/zfs-ubuntu24$;
exact-rustic;exact;5;2;;;;;;rustic-bko
exact-restic;exact;5;2;;;;;;restic-bko
cmp-debian13;compare;12;6;debian13;;0;;;
rt-freebsd15;runall;1;5;freebsd15;;10;freebsd;^freebsd/(rt|nonroot)-freebsd15$;
rt-freebsd15-ufs;runall;1;5;freebsd15-ufs;;11;freebsd;^freebsd/rt-freebsd15-ufs$;
rt-netbsd11;runall;1;5;netbsd11;;15;netbsd;^netbsd/rt-netbsd11$;
rt-openbsd8;runall;1;5;openbsd8;;12;openbsd_illumos;^openbsd_illumos/rt-openbsd8$;
rt-omnios58;runall;1;6;omnios58;;13;openbsd_illumos;^openbsd_illumos/rt-omnios58$;
rt-dragonfly6;runall;1;5;dragonfly6;;10;dragonfly;^dragonfly/rt-dragonfly6$;'
    local name kind d m g nd off plats onlyre extra pfx k=0 OLDS=""
    while IFS=';' read -r name kind d m g nd off plats onlyre extra; do
        [[ -n $name ]] || continue
        k=$((k + 1))
        h=$(place "$d" "$m" "$g" "$nd")
        [[ -n $h ]] || die "step 4: pick-host found no host for $name (--reserve $d $m${g:+ --guest $g}${nd:+ --needs $nd})"
        ASSIGNED+=$'\n'"$h"
        prep_host "$h"
        if [[ $old != none && " $OLDS " != *" $h "* ]]; then
            [[ $old == /?* && -d $old ]] || die "step 4: OLD_BIN '$old' isn't a directory"
            OLDS+=" $h"; hput "$h" "$R/old" "$old/"
        fi
        pfx=bkorr$TAG6$(printf '%02d' "$k")
        hv D "$d"; hv M "$m"; hv PFX "$pfx"; hv BIN "\$HOME/$W/round$ROUND/bin"
        case $kind in
            runall)
                if [[ $old == none ]]; then hv OLDB none; else hv OLDB "\$HOME/$R/old"; fi
                ha EXP "${exp[@]}"
                if [[ -n $extra ]]; then ha EXTRA "$extra"; else HDR+="EXTRA=()"$'\n'; fi
                hv PLATS "$plats"; hv ONLY "$onlyre"
                if [[ -n $off ]]; then alloc_port "$h"; hv PB "$((PORT_GOT - off))"; else hv PB ""; fi ;;
            exact) hv TOOL "$extra" ;;
            compare) alloc_port "$h"; hv GUEST "$g"; hv PORT "$PORT_GOT" ;;
        esac
        # The values quoted with %q keep $HOME literal: expand it on the host.
        HDR=${HDR//\\\$HOME/\$HOME}
        job "$name" "$h" "$kind"
    done <<<"$jobs"
    wait_jobs

    # Results: each job's logs back, one table.
    local tab=$S/table.txt v detail fails=0 sees=0
    printf '%-18s %-9s %-8s %s\n' JOB HOST VERDICT DETAIL >"$tab"
    for i in "${!JN[@]}"; do
        n=${JN[$i]} h=${JH[$i]}
        [[ $n == s3-lock || $n == s1 || $n == s2-* ]] && continue
        hget "$h" "$R/logs/$n.out" "$S/$n"
        hget "$h" "$R/logs/$n/" "$S/$n"
        v=FAIL detail=""
        if [[ ${JR[$i]} == dry ]]; then v=dry detail="$LOGDIR/jobs/$n.sh"
        elif [[ -f $S/$n/summary.txt ]]; then
            detail=$(grep '^run-all: ' "$S/$n/summary.txt" | tail -n 1)
            cp "$S/$n/summary.txt" "$S/$n/summary.host.txt"
            if [[ ${JR[$i]} == 0 ]] && python3 -I -B "$REPO/testvm/compare/verdict.py" summary "$S/$n" >"$S/$n/verdict.txt" 2>&1; then v=PASS; fi
            grep -vE ' PASS +[0-9]* *$|^PLATFORM|^run-all:|^note:' "$S/$n/summary.host.txt" | sed "s/^/    $n: /" >>"$S/failed-rows.txt" || true
        elif [[ -f $S/$n/exact-rt.log ]]; then
            detail=$(tail -n 1 "$S/$n/exact-rt.log")
            [[ ${JR[$i]} == 0 && $detail == PASS ]] && v=PASS
        elif [[ -f $S/$n/compare.log ]]; then
            # compare.py always exits 0: judge its results.json.
            hget "$h" "$WT/compare/" "$S/$n/compare-results"
            local rj=""
            for rj in "$S/$n/compare-results"/*/*/results.json; do [[ -f $rj ]] && break; rj=""; done
            if [[ -z $rj ]]; then
                detail="compare.py exit ${JR[$i]}, no results.json: $S/$n/compare.log"
            else
                detail=$(python3 -I -B - "$rj" <<'PY' 2>&1
import json, sys
r = json.load(open(sys.argv[1]))
want = ["restic-bko", "rustic-bko", "restic-to-rustic", "rustic-to-restic"]
bad, diffs = [], []
for t in want:
    v = r.get("tools", {}).get(t)
    if v is None:
        bad.append(f"{t} missing"); continue
    if v.get("run") != r.get("started"):
        bad.append(f"{t} from an earlier run")
    if not v.get("restore_ok"):
        bad.append(f"{t} restore failed")
    b = v.get("boot", {})
    if not b.get("ssh"):
        bad.append(f"{t} no ssh")
    elif b.get("new_failures"):
        bad.append(f"{t} new failed units {','.join(b['new_failures'])}")
    d = ", ".join(f"{k} {x['count']}" for k, x in v.get("diff", {}).items())
    if d:
        diffs.append(f"{t}: {d}")
if bad:
    print("FAIL " + "; ".join(bad))
elif diffs:
    print("SEE " + "; ".join(diffs))
else:
    print("PASS restored and booted, no differences, four directions")
PY
)
                case $detail in
                    "PASS "*) [[ ${JR[$i]} == 0 ]] && v=PASS ;;
                    "SEE "*) [[ ${JR[$i]} == 0 ]] && v=SEE ;;
                esac
                detail="${detail#* } ($rj)"
            fi
        else
            detail="exit ${JR[$i]}, no results: $S/$n/$n.out"
        fi
        [[ $v == SEE ]] && sees=$((sees + 1))
        [[ $v == PASS || $v == dry || $v == SEE ]] || fails=$((fails + 1))
        printf '%-18s %-9s %-8s %s\n' "$n" "$h" "$v" "${detail:-exit ${JR[$i]}}" >>"$tab"
    done
    echo
    cat "$tab"
    [[ -s $S/failed-rows.txt ]] && { echo "rows not passed:"; cat "$S/failed-rows.txt"; }
    echo
    if [[ $DRY == 1 ]]; then result 4 "dry run (job scripts: $LOGDIR/jobs/)"; return 0; fi
    if [[ $fails == 0 && $sees == 0 ]]; then result 4 "PASS: every job ($tab); round binaries: ~/$W/round$ROUND/bin on$bh"; return 0; fi
    if [[ $fails == 0 ]]; then
        result 4 "PASS but $sees to look at (SEE: compare.py's differences, not counted as passed; $tab); round binaries: ~/$W/round$ROUND/bin on$bh"
        return 0
    fi
    result 4 "FAIL: $fails jobs ($tab); kept: ~/$R and ~/$WT on$TOUCHED"
    return 1
}

# --- the run --------------------------------------------------------------------------------
say "rustic-exact-round: steps $steps$([[ $DRY == 1 ]] && echo ', dry run'); run $RUN; logs $LOGDIR"
[[ $DRY == 1 ]] || { caffeinate -i -w $$ & CAF_PID=$!; }
write_remote
S1_HOST=""
# The start functions run with set -e in force; the judges and steps 3 and 4,
# called in a condition, check what they run themselves (and STOP on it).
if want 1 || want 2; then
    if want 1; then step1_start; fi
    if want 2; then step2_start; fi
    wait_jobs
    if want 1; then step1_judge || FAILED=1; fi
    if [[ $FAILED == 0 ]] && want 2; then step2_judge || FAILED=1; fi
fi
if [[ $FAILED == 0 ]] && want 3; then step3 || FAILED=1; fi
if [[ $FAILED == 0 ]] && want 4; then step4 || FAILED=1; fi

echo
echo "rustic-exact-round $RUN:"
printf '%s' "$STATUS"
if [[ $FAILED == 0 && $DRY == 0 && $KEEP == 0 ]]; then
    for h in $TOUCHED; do
        say "$h: removing ~/$R and ~/$WT"
        hssh "$h" "sudo -n rm -rf -- \$HOME/$R \$HOME/$WT" || say "$h: couldn't remove them"
    done
elif [[ -n $TOUCHED && $DRY == 0 ]]; then
    say "kept: ~/$R and ~/$WT on$TOUCHED"
fi
say "logs: $LOGDIR (driver.log)"
ENDED=1
exit $FAILED
