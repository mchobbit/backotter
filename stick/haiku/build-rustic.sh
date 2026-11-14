#!/usr/bin/env bash
# Build BackOtter's rustic for Haiku, natively, in a throwaway copy of the
# haiku6 test guest (testvm/make-haiku.sh): branch "backotter" of
# github.com/mchobbit/rustic (whose Cargo.lock pins rustic_core's commit),
# or local trees (SRC), with Rust (rust_bin) and gcc from HaikuPorts.
#
# Usage: build-rustic.sh [PORT]
#
# Smoke-tests the binary in the guest (backs up and restores a small tree
# with a fifo, a symlink and a BFS attribute), then writes
# $BKO_BIN/haiku/rustic-bko and rustic-commit (BKO_BIN defaults to
# ~/backotter-work/bko-bin).  PORT (default 2430) is the guest's SSH port
# here.  stick/build-round.sh runs it for every round, with SRC and FAST.
#
#   SRC=DIR   build DIR/rustic and DIR/rustic_core (checkouts of the forks, or
#             copies without .git) instead of the pushed branch: in the guest,
#             prep-rustic-src.sh points rustic's [patch.crates-io] at that
#             rustic_core (and its vendored walkdir) before the --locked
#             build, as the other platforms' build-rustic.sh do.  rustic-commit
#             says "local, unpushed"; RUSTIC_COMMIT and RUSTIC_CORE_COMMIT give
#             the commits when the trees have no .git (else they're "unknown").
#   RUSTIC_CORE=DIR
#             the pushed rustic with the rustic_core tree in DIR (a checkout
#             of the fork) instead of the commit Cargo.lock pins, e.g. a
#             branch not yet pushed; rustic-commit then says so, with
#             RUSTIC_CORE_COMMIT if DIR is a copy without its git metadata.
#             Not with SRC.
#   FAST=1    no LTO and 16 codegen units (CARGO_PROFILE_RELEASE_*): quicker,
#             for test rounds, and rustic-commit says so.  Without it, the
#             fork's release profile (fat LTO, one codegen unit), which is
#             what images ship.
#
# HaikuPorts has one rolling repository, so the Rust and gcc it installs are
# whatever is current, unlike the other platforms' pinned package sets.
#
# The build grows the guest's throwaway overlay by gigabytes.  It goes in
# $TMPDIR if set, else in BKO_SCRATCH/tmp (default /bkoscratch, Chopin's
# spare NVMe, where it exists), else where vm-run puts it.
#
# cargo runs detached in the guest, into ~/build/cargo-N.log, not through a
# pipe held by an ssh (once it vanished mid-build and the script hung on the
# pipe for most of an hour).  A watchdog polls it every BKO_HAIKU_POLL s (30)
# and fails, with diagnostics, when cargo and rustc vanish without an exit
# status, when QEMU uses under BKO_HAIKU_STALL_PCT % (10; an idle haiku6
# takes 2) of one CPU for BKO_HAIKU_STALL s (900; fat-LTO links print
# nothing for minutes, but burn CPU), when the guest stops answering, or past BKO_HAIKU_BUILD_TIMEOUT s
# (5400).  A vanish, or cargo failing to spawn a process (Haiku's
# B_BAD_VALUE, "(never executed)"), gets one retry between them,
# incrementally, with -j 4; a second failure of any kind is final.  The
# crates are fetched first (cargo fetch, one retry), and the build runs
# --offline.  A kernel panic (KDL in QEMU's serial log) fails at once, while
# booting or building, with "the haiku6 guest's kernel panicked", which
# build-round.sh retries on a fresh guest.  Logs go in
# BKO_LOG_DIR/haiku-build if set (build-round.sh sets it), else in
# BKO_WORK/logs/haiku-build-DATE: always the watch log and cargo's log; on a
# failure also the guest's ps, syslog, memory and target/release listing and
# QEMU's serial.log and vm-run.log, in a directory named for the failure.
#
# Needs: qemu-system-x86_64 with KVM, git, rsync, ssh, and testvm/vm-run.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
BKO_BIN=${BKO_BIN:-$WORK/bko-bin}
port=${1:-2430}
REPO=https://github.com/mchobbit/rustic
# haiku_devel: crti.o and the like.  zstd_devel: see the build below.
PACKAGES="rust_bin gcc binutils haiku_devel zstd_devel cmake make perl pkgconfig"
kdl() {  # the PANIC: line, if the guest's serial log shows Haiku's kernel debugger
    local f=${work:-/nonexistent}/serial.log p
    grep -q 'Welcome to Kernel Debugging Land' "$f" 2>/dev/null || return 1
    p=$(tr -d '\r' <"$f" | sed -n '/PANIC:/h; /Welcome to Kernel Debugging Land/{x;p;q;}') || true
    p=$(printf '%s\n' "$p" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | cut -c 1-300)
    echo "${p:-unknown panic}"
}
die() {  # build-round.sh greps for "the haiku6 guest's kernel panicked"
    local p
    if [[ $* != "the haiku6 guest's kernel panicked"* ]] && p=$(kdl); then
        set -- "the haiku6 guest's kernel panicked (KDL): $p (then: $*)"
    fi
    echo "build-rustic: $*" >&2
    exit 1
}
logdir=${BKO_LOG_DIR:+$BKO_LOG_DIR/haiku-build}
logdir=${logdir:-$WORK/logs/haiku-build-$(date +%Y%m%d-%H%M%S)}

vm=$WORK/vms/haiku6
[[ -f $vm/haiku6.qcow2 && -f $vm/id_ed25519 && -f $vm/vm-run-options ]] || die "no VM in $vm"
pgrep -f "^qemu-system.*:$port-:22" >/dev/null && die "port $port is in use"
if [[ -n ${SRC:-} ]]; then
    [[ -z ${RUSTIC_CORE:-} ]] || die "SRC and RUSTIC_CORE don't go together"
    SRC=$(cd "$SRC" && pwd)
    [[ -f $SRC/rustic/Cargo.toml && -f $SRC/rustic_core/crates/core/Cargo.toml &&
        -f $SRC/rustic_core/vendor/walkdir/Cargo.toml ]] ||
        die "SRC=$SRC has no rustic/ and rustic_core/ (with vendor/walkdir)"
elif [[ -n ${RUSTIC_CORE:-} ]]; then
    [[ -f $RUSTIC_CORE/crates/core/Cargo.toml ]] || die "RUSTIC_CORE=$RUSTIC_CORE isn't a rustic_core tree"
fi

scratch=${BKO_SCRATCH:-/bkoscratch}
if [[ -z ${TMPDIR:-} && -d $scratch && -w $scratch ]]; then
    mkdir -p "$scratch/tmp" && export TMPDIR=$scratch/tmp
fi
work=$(mktemp -d)
# Haiku's only user is "user" (uid 0); its shell is bash.
ssh_g() {
    # --foreground: in our process group, so an outer timeout's TERM reaches
    # it, and the traps below run before its KILL.
    timeout --foreground "${T:-7200}" ssh -q -i "$vm/id_ed25519" -p "$port" -o BatchMode=yes \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 \
        user@127.0.0.1 "$@"
}
signalled=no
cleanup() {
    local s=$?
    if [[ $s != 0 && -f $work/serial.log ]]; then
        { mkdir -p "$logdir" && cp -f "$work/serial.log" "$work/vm-run.log" "$logdir/"; } 2>/dev/null || true
    fi
    # A clean shutdown, though the disk is a throwaway: QEMU killed early
    # would be the same, and is what a signal gets (an outer timeout's KILL
    # follows soon; its TERM may reach a child first, which set -e exits on).
    if [[ $signalled == no && $s -lt 128 ]]; then
        T=30 ssh_g 'shutdown -q' >/dev/null 2>&1 || true
        for _ in $(seq 60); do pgrep -f "^qemu-system.*:$port-:22" >/dev/null || break; sleep 2; done
    fi
    pkill -f "^qemu-system.*:$port-:22" 2>/dev/null || true
    # Gone before we exit, so a rerun (build-round.sh's, after a panic) finds the port free.
    for _ in $(seq 10); do pgrep -f "^qemu-system.*:$port-:22" >/dev/null || break; sleep 1; done
    rm -rf "$work"
}
trap cleanup EXIT
trap 'signalled=yes; exit 129' HUP
trap 'signalled=yes; exit 130' INT
trap 'signalled=yes; exit 143' TERM

# The trees go to the guest as ~/build/rustic (and ~/build/rustic_core).
mkdir "$work/build"
locked=--locked prep=no
if [[ -n ${SRC:-} ]]; then
    tree_commit() {  # DIR VAR: the tree's commit, and whether it has changes
        local c=${!2:-}
        # Only the tree's own .git counts: git would take a copy inside some
        # other checkout for part of it, and give that checkout's commit.
        if [[ -e $1/.git ]]; then
            [[ -n $c ]] || c=$(git -C "$1" rev-parse HEAD 2>/dev/null) || c=""
            [[ -z $(git -C "$1" status --porcelain 2>/dev/null) ]] || c="${c:-unknown}, with uncommitted changes"
        fi
        echo "${c:-unknown (no .git, no $2)}"
    }
    commit="$(tree_commit "$SRC/rustic" RUSTIC_COMMIT) (local, unpushed, from $SRC/rustic)"
    core="$(tree_commit "$SRC/rustic_core" RUSTIC_CORE_COMMIT) (local, unpushed, from $SRC/rustic_core)"
    for d in rustic rustic_core; do
        rsync -rlp --exclude .git --exclude target "$SRC/$d/" "$work/build/$d/"
    done
    cp "$here/../prep-rustic-src.sh" "$work/build/"
    prep=yes
else
    git clone -q --depth 1 -b backotter "$REPO" "$work/build/rustic"
    commit=$(git -C "$work/build/rustic" rev-parse HEAD)
    rm -rf "$work/build/rustic/.git"
    core=$(sed -n '/^name = "rustic_core"/,/^source/s/.*#\([0-9a-f]*\).*/\1/p' "$work/build/rustic/Cargo.lock")
    if [[ -n ${RUSTIC_CORE:-} ]]; then
        # A patch in .cargo/config.toml wins over Cargo.toml's; Cargo.lock then
        # changes for rustic_core, so not --locked.
        core="${RUSTIC_CORE_COMMIT:-$(git -C "$RUSTIC_CORE" rev-parse HEAD 2>/dev/null || echo unknown)} from $RUSTIC_CORE"
        [[ -z $(git -C "$RUSTIC_CORE" status --porcelain 2>/dev/null) ]] || core+=" (with uncommitted changes)"
        mkdir -p "$work/build/rustic/.cargo"
        printf '[patch.crates-io]\nrustic_core = { path = "../rustic_core/crates/core" }\n' \
            >>"$work/build/rustic/.cargo/config.toml"
        rsync -rlp --exclude .git --exclude target "$RUSTIC_CORE/" "$work/build/rustic_core/"
        locked=
    fi
fi
echo "rustic $commit"
echo "rustic_core $core"
tar -C "$work/build" -czf "$work/src.tgz" .

echo "Booting a throwaway copy of haiku6 on port $port"
# shellcheck disable=SC2046
SSH_PORT=$port setsid -f "$here/../../testvm/vm-run" $(<"$vm/vm-run-options") --snapshot \
    --memory 8192 --cpus 8 --serial-log "$work/serial.log" "$vm/haiku6.qcow2" \
    </dev/null >"$work/vm-run.log" 2>&1
for _ in $(seq 30); do pgrep -f "^qemu-system.*:$port-:22" >/dev/null && break; sleep 1; done
# Until the guest answers on SSH and $1 succeeds there.
wait_guest() {
    local deadline=$((SECONDS + 600)) p
    until T=15 ssh_g "$1" 2>/dev/null; do
        if p=$(kdl); then die "the haiku6 guest's kernel panicked (KDL): $p"; fi
        [[ $SECONDS -lt $deadline ]] || die "the guest's sshd didn't come up"
        pgrep -f "^qemu-system.*:$port-:22" >/dev/null || { cat "$work/vm-run.log" >&2; die "QEMU exited"; }
        sleep 5
    done
}
wait_guest true

echo "Installing $PACKAGES"
ssh_g "pkgman install -y $PACKAGES >/dev/null"
# haiku_devel must match the haiku package, so pkgman upgrades that too, and
# then all of it only takes effect at the next boot.  The guest reboots in
# the same QEMU, so on the same throwaway overlay; /tmp is emptied at boot.
echo "Rebooting into the upgraded system"
ssh_g 'touch /tmp/bko-before-reboot && shutdown -r -q' >/dev/null 2>&1 || true
wait_guest '[ ! -e /tmp/bko-before-reboot ]'
toolchain=$(ssh_g 'rustc --version && gcc --version | head -n 1') || die "no Rust or gcc in the guest"
ssh_g 'cargo --version'
echo "$toolchain"

ssh_g 'rm -rf ~/build && mkdir ~/build && cd ~/build && tar -xzmf -' <"$work/src.tgz"
if [[ $prep == yes ]]; then
    echo "Pointing rustic at the local rustic_core"
    ssh_g 'cd ~/build && sh ./prep-rustic-src.sh ~/build' || die "prep-rustic-src.sh failed"
fi

fast="" fast_note=""
if [[ ${FAST:-} == 1 ]]; then
    fast="CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16"
    fast_note=" (FAST: no LTO, 16 codegen units)"
fi
# Fetch first, then build --offline: the long-lived build then holds no TCP
# sockets (cargo keeps crates.io's open), and Haiku's TCP teardown has
# panicked during team deletion ("bound endpoint ... not in hash!", in
# vfs_put_io_context), and may hang there, as cargo once seemed to.
echo "Fetching the crates"
mkdir -p "$logdir"
: >"$logdir/fetch.log"
fetched=no
for try in 1 2; do
    if T=1800 ssh_g "cd ~/build/rustic && env -u PROJECT_VERSION ZSTD_SYS_USE_PKG_CONFIG=1 cargo fetch $locked" \
        >>"$logdir/fetch.log" 2>&1 </dev/null; then
        fetched=yes
        break
    fi
    if p=$(kdl); then die "the haiku6 guest's kernel panicked (KDL) during cargo fetch: $p"; fi
    if [[ $try == 1 ]]; then echo "cargo fetch failed; trying again in 30 s"; sleep 30; fi
done
if [[ $fetched == no ]]; then
    tail -n 20 "$logdir/fetch.log" >&2
    die "cargo fetch failed (log: $logdir/fetch.log)"
fi
echo "Fetched (log: $logdir/fetch.log)"
echo "Building$fast_note"
mkdir -p "$logdir"
watch=$logdir/watch.log
: >"$watch"
echo "Watching the build every ${BKO_HAIKU_POLL:-30} s; logs in $logdir"
# The guest's half.  "start N CMD...": CMD detached, into cargo-N.log, its
# status into cargo-N.rc.  "poll N": one line, rc= (- while it runs), log=
# (cargo-N.log's size), teams= (cargo and rustc teams, by the path ps
# prints first; ? if ps failed) and mem= (sysinfo's first line).
T=60 ssh_g 'cat >~/build/bko-build.sh' <<'EOF'
b=~/build
case $1 in
start)
    n=$2; shift 2
    rm -f "$b/cargo-$n.rc" "$b/cargo-$n.rc.part"
    # A wrapper left from a vanished attempt mustn't write an rc later.
    for f in "$b"/cargo-*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; done
    cd "$b/rustic" || exit 1
    nh=; command -v nohup >/dev/null 2>&1 && nh=nohup
    $nh bash "$0" run "$n" "$@" </dev/null >/dev/null 2>&1 &
    echo $! >"$b/cargo-$n.pid"
    ;;
run)
    n=$2; shift 2
    trap '' HUP
    "$@" >"$b/cargo-$n.log" 2>&1
    echo $? >"$b/cargo-$n.rc.part" && mv -f "$b/cargo-$n.rc.part" "$b/cargo-$n.rc"
    ;;
poll)
    n=$2 t='?' rc=-
    if p=$(ps 2>/dev/null) && [ -n "$p" ]; then
        t=$(printf '%s\n' "$p" | awk '{c = $1; sub(/.*\//, "", c)} c == "cargo" || c == "rustc" {k++} END {print k + 0}')
    fi
    [ -f "$b/cargo-$n.rc" ] && rc=$(cat "$b/cargo-$n.rc")
    sz=$(wc -c <"$b/cargo-$n.log" 2>/dev/null) || sz=0
    m=$(sysinfo -mem 2>/dev/null | head -n 1 | sed 's/  */ /g; s/^ //')
    echo "rc=${rc:-?} log=$((sz + 0)) teams=${t:-?} mem=$m"
    ;;
esac
EOF

poll=${BKO_HAIKU_POLL:-30} stall=${BKO_HAIKU_STALL:-900} stall_pct=${BKO_HAIKU_STALL_PCT:-10}
limit=${BKO_HAIKU_BUILD_TIMEOUT:-5400}
clk=$(getconf CLK_TCK 2>/dev/null) || clk=100
qpid=$(pgrep -f "^qemu-system.*:$port-:22" | head -n 1) || true
[[ -n $qpid ]] || die "QEMU isn't running"
qemu_ticks() {  # QEMU's user+system CPU time, in clock ticks
    local s f
    s=$(cat "/proc/$qpid/stat" 2>/dev/null) || return 1
    s=${s##*) }   # past the command name, so state is f[0]
    read -r -a f <<<"$s"
    echo $((f[11] + f[12]))
}
poll_guest() {  # g_rc, g_log, g_teams, g_mem for attempt $attempt; 1 if no answer
    local out a b c
    out=$(T=60 ssh_g "bash ~/build/bko-build.sh poll $attempt" 2>/dev/null </dev/null) || return 1
    out=${out%%$'\n'*}
    [[ $out == rc=* ]] || return 1
    read -r a b c g_mem <<<"$out"
    g_rc=${a#rc=} g_log=${b#log=} g_teams=${c#teams=} g_mem=${g_mem#mem=}
}
diag() {  # TAG: what can be known about the build, into $logdir/TAG; never fails
    local d=$logdir/$1 e n s
    mkdir -p "$d" || return 0
    cp -f "$work/serial.log" "$work/vm-run.log" "$d/" 2>/dev/null || true
    # A guest in the kernel debugger answers no ssh.
    if kdl >/dev/null; then
        echo "(the guest is in KDL; see serial.log)" >"$d/guest-skipped"
        return 0
    fi
    for e in "cargo.log|cat ~/build/cargo-$attempt.log" "ps|ps" "ps-a|ps -a | head -n 3000" \
        "syslog|tail -n 200 /boot/system/var/log/syslog" "sysinfo|sysinfo -mem; sysinfo" \
        "target|ls -la ~/build/rustic/target/release; ls -lt ~/build/rustic/target/release/deps | head -n 60" \
        "desktop|ls -la ~/Desktop; for f in ~/Desktop/*.report; do [ -f \"\$f\" ] && echo \"== \$f\" && tail -n 300 \"\$f\"; done"; do
        n=${e%%|*} s=0
        T=30 ssh_g "${e#*|}" >"$d/$n" 2>&1 </dev/null || s=$?
        if [[ $s == 124 || $s == 255 ]]; then
            echo "(the guest didn't answer: $s)" >>"$d/$n"
            break
        fi
    done
    return 0
}
fail() {  # TAG MESSAGE: save diagnostics (in TAG, TAG-2 on the retry), and die
    local tag=$1 msg=$2 p
    if [[ $tag != kernel-panic ]] && p=$(kdl); then
        tag=kernel-panic msg="the haiku6 guest's kernel panicked (KDL) $((SECONDS - ${start:-$SECONDS})) s into the build: $p (then: $msg)"
    fi
    [[ ${attempt:-1} == 1 ]] || tag+=-2
    diag "$tag"
    {   echo "build-rustic: diagnostics in $logdir/$tag, the watch log in $watch"
        echo "build-rustic: the end of cargo's log:"
        tail -n 30 "$logdir/$tag/cargo.log" 2>/dev/null || true
    } >&2
    die "$msg${first:+ (on the retry, after: $first)}"
}
retry() {  # TAG MESSAGE: save diagnostics in TAG, and start attempt 2 with -j 4
    watch_line "$2; retrying once with -j 4"
    diag "$1"
    echo "build-rustic: $2; diagnostics in $logdir/$1; retrying once with -j 4" >&2
    first=$2 attempt=2 jobs="-j 4"
    start_cargo
}
watch_line() {  # MESSAGE
    printf '%s %s\n' "$(date +%T)" "$*" >>"$watch"
}
start_cargo() {  # attempt $attempt, with $jobs
    t_att=$SECONDS gone=0 misses=0
    prev_t=0 prev_c=$(qemu_ticks) || fail qemu-exited "QEMU exited before the build"
    hist_t=(0) hist_c=("$prev_c")
    watch_line "attempt $attempt: cargo build --release --offline $locked $jobs"
    # Default features, not "release" (which adds self-update); see the fork's
    # build-backotter.sh.  zstd from the system: iana-time-zone links Haiku's
    # libbe, which uses libzstd.so, and the zstd crate's own copy has hidden
    # symbols, which ld refuses to an executable whose libraries need them.
    T=60 ssh_g "bash ~/build/bko-build.sh start $attempt env -u PROJECT_VERSION ZSTD_SYS_USE_PKG_CONFIG=1 $fast cargo build --release --offline $locked $jobs" </dev/null ||
        fail start "couldn't start cargo in the guest"
    sleep 5
    if poll_guest && [[ $g_rc == - && $g_teams == 0 ]]; then
        fail start "no cargo or rustc team in the guest 5 s after starting the build (it didn't outlive the ssh that started it, or ps doesn't show it)"
    fi
}

start=$SECONDS attempt=1 jobs="" first="" spawn=no
start_cargo
while :; do
    sleep "$poll"
    now=$((SECONDS - t_att)) total=$((SECONDS - start))
    ticks=$(qemu_ticks) || fail qemu-exited "QEMU exited, $total s into the build"
    if poll_guest; then
        misses=0
    else
        if p=$(kdl); then fail kernel-panic "the haiku6 guest's kernel panicked (KDL) $total s into the build: $p"; fi
        misses=$((misses + 1))
        g_rc=- g_log='?' g_teams='?' g_mem="(no answer from the guest, $misses in a row)"
    fi
    pct=0
    if [[ $now -gt $prev_t ]]; then pct=$((100 * (ticks - prev_c) / clk / (now - prev_t))); fi
    watch_line "$(printf 'attempt %d %5d s  log %9s B  cargo/rustc %s  qemu cpu %d s, %d%% of a CPU  %s' \
        "$attempt" "$now" "$g_log" "$g_teams" $((ticks / clk)) "$pct" "$g_mem")"
    prev_t=$now prev_c=$ticks
    hist_t+=("$now") hist_c+=("$ticks")
    if [[ $g_rc != - ]]; then
        T=300 ssh_g "cat ~/build/cargo-$attempt.log" >"$logdir/cargo.log" 2>/dev/null </dev/null || true
        # Haiku sometimes fails a spawn that works the next time.
        if [[ $g_rc != 0 ]] && grep -q 'could not execute process.*(never executed)' "$logdir/cargo.log" &&
            grep -q 'os error -21474836' "$logdir/cargo.log"; then
            spawn=yes
            if [[ $attempt == 1 ]]; then
                err=$(grep -o 'os error -[0-9]*' "$logdir/cargo.log" | head -n 1) || true
                retry spawn-failed-1 "cargo couldn't start a process in the haiku6 guest (Haiku spawn error, $err)"
                continue
            fi
        fi
        break
    fi
    [[ $misses -lt 4 ]] || fail unreachable "the guest stopped answering on SSH, $total s into the build"
    if [[ $g_teams == 0 ]]; then
        gone=$((gone + 1))
    elif [[ $g_teams != '?' ]]; then
        gone=0
    fi
    if [[ $gone -ge 2 ]]; then
        msg="cargo vanished in the haiku6 guest after $now s without an exit status"
        [[ $attempt == 1 ]] || fail vanished "$msg"
        retry vanished-1 "$msg"
        continue
    fi
    # The newest sample at least $stall s old: did QEMU use any CPU since?
    i=-1
    for j in "${!hist_t[@]}"; do
        if [[ ${hist_t[j]} -le $((now - stall)) ]]; then i=$j; fi
    done
    if [[ $i -ge 0 ]]; then
        w=$((now - hist_t[i])) d=$((ticks - hist_c[i]))
        if [[ $((d * 100)) -lt $((w * clk * stall_pct)) ]]; then
            fail stalled "the build stalled: no CPU in the guest for $w s (QEMU used $((d / clk)) s of CPU)"
        fi
    fi
    [[ $total -lt $limit ]] || fail timeout "the build ran over $limit s"
done
grep -v '^ *\(Compiling\|Downloaded\|Downloading\|Locking\|Adding\|Updating\)' "$logdir/cargo.log" || true
if [[ $g_rc != 0 ]]; then
    [[ $spawn == no ]] || fail spawn-failed "the build failed: cargo couldn't start a process (Haiku spawn error), exit $g_rc"
    fail failed "the build failed (cargo exited $g_rc)"
fi
watch_line "done: cargo exited 0"
echo "Built in $((SECONDS - start)) s${jobs:+ (the second attempt, with $jobs)}"
bin='~/build/rustic/target/release/rustic'   # the guest's ~
version=$(ssh_g "$bin --version") || die "the binary doesn't run in the guest"
echo "$version"
[[ $version == *backotter* ]] || die "the binary isn't BackOtter's rustic: $version"
ssh_g "objdump -p $bin | grep NEEDED"
# Back up and restore a small tree with a fifo, a symlink and a BFS
# attribute, as the other platforms' builds do (less the attribute).  Not
# under /tmp, a symlink to /boot/system/cache/tmp: the snapshot takes the
# real path, and restoring latest:/tmp/... finds no such node.
smoke=$(ssh_g "export RUSTIC_PASSWORD=x; R=$bin S=/boot/home/bko-smoke
rm -rf \$S && mkdir -p \$S/src/d || exit 1
echo hello >\$S/src/a && ln -s a \$S/src/l && mkfifo \$S/src/f && echo x >\$S/src/d/b && chmod 640 \$S/src/a &&
    addattr -t string BKO:smoke hello \$S/src/a || exit 1
\$R -r \$S/repo init >/dev/null 2>&1 &&
    \$R -r \$S/repo backup --set-atime yes \$S/src >\$S/backup.log 2>&1 &&
    \$R -r \$S/repo restore --numeric-id latest:\$S/src \$S/dst >\$S/restore.log 2>&1 || { cat \$S/*.log; exit 1; }
diff -r \$S/src/d \$S/dst/d && test -p \$S/dst/f && test -L \$S/dst/l &&
    test \"\$(catattr -d BKO:smoke \$S/dst/a)\" = hello && echo SMOKE-OK") || true
echo "$smoke" | tail -n 20
[[ $smoke == *SMOKE-OK* ]] || die "the smoke test failed in haiku6"
mkdir -p "$BKO_BIN/haiku"
ssh_g "cat $bin" >"$BKO_BIN/haiku/rustic-bko.part"
[[ -s $BKO_BIN/haiku/rustic-bko.part ]] || die "couldn't copy the binary out of the guest"
chmod 755 "$BKO_BIN/haiku/rustic-bko.part"
mv "$BKO_BIN/haiku/rustic-bko.part" "$BKO_BIN/haiku/rustic-bko"
printf '%s\nrustic_core %s\n%s, built natively in haiku6%s\n' \
    "$commit" "$core" "$(echo "$toolchain" | paste -sd ' ' - | sed 's/ gcc / and gcc /')" "$fast_note" \
    >"$BKO_BIN/haiku/rustic-commit"
ls -l "$BKO_BIN/haiku/rustic-bko"
