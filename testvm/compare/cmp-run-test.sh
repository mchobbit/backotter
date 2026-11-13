#!/usr/bin/env bash
# cmp-run.sh's ledger self-test: its edits of .reserve, .reserve.wait and
# .reserve.lock work whoever made them, m or root, in a root-owned sticky
# directory like /mnt/bko-ram (m can't rename over root's files there, and
# fs.protected_regular keeps root from opening m's with O_CREAT). Run as m
# (passwordless sudo) on a test host:
#
#   ~/backotter-work/bs2-wt/testvm/compare/cmp-run-test.sh   (or any copy's)
#
# It keeps a ledger of its own (CMP_LEDGER_DIR) in /mnt/bko-ram/cmp-run-test.*,
# made root's and 1777 as /mnt/bko-ram is, and reserves 0.1 GiB in it, beside
# the real ledger, not in it. CMP_RUN: the cmp-run.sh to test (default the
# one beside this file; a copy needs iolog.sh beside it). Cases, each about
# a minute (cmp-run.sh waits 30 s between tries):
#   1 root's files, m's --reserve: waits in line (queue_up adds, then
#     updates its row), gets the room once the row ahead's holder is gone
#     (prune), takes a row and leaves the line (leave_line);
#   2 m's files, root's --reserve: the same;
#   3 root's files, m's queue worker: its row goes to the job's sudo (the
#     rename of the row), and is pruned once the job ends.
# Each case also checks that nothing went to stderr, the files are the same
# inodes with the same owner, and no temporary file was left. One ok or FAIL
# line per check; exit 1 if any FAILs.
set -uo pipefail
HERE=$(dirname "$(realpath "$0")")
CMP_RUN=${CMP_RUN:-$HERE/cmp-run.sh}
[[ $(id -u) != 0 ]] || { echo "cmp-run-test: run it as m, not root" >&2; exit 1; }
sudo -n true || { echo "cmp-run-test: needs passwordless sudo" >&2; exit 1; }
mountpoint -q /mnt/bko-ram || { echo "cmp-run-test: no tmpfs at /mnt/bko-ram" >&2; exit 1; }
D=$(mktemp -u /mnt/bko-ram/cmp-run-test.XXXXXX) T=$(mktemp -d) TAG=cmp-run-test-$$
sudo install -d -m 1777 -o root -g root "$D" || exit 1
chmod 755 "$T"
fails=0 pids=()
ok() { echo "ok   $*"; }
bad() { echo "FAIL $*"; fails=$((fails + 1)); }
check() { local what=$1; shift; if "$@"; then ok "$what"; else bad "$what"; fi; }
cleanup() {
    local p
    for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done
    sudo rm -rf "$D"; rm -rf "$T" ~/backotter-work/cmp-logs/"$TAG"
}
trap cleanup EXIT
files() { printf '%s\n' "$D/.reserve" "$D/.reserve.lock" "$D/.reserve.wait"; }
fresh() {  # OWNER: the ledger's three files, empty and 666, OWNER's (root or m)
    sudo find "$D" -mindepth 1 -delete
    if [[ $1 == root ]]; then files | sudo sh -c 'umask 0; while read -r f; do : >"$f"; done'
    else files | (umask 0; while read -r f; do : >"$f"; done); fi
    files | xargs stat -c '%i %U' >"$T/inodes"
}
same_files() { files | xargs stat -c '%i %U' | cmp -s - "$T/inodes"; }
no_leftovers() { [[ $(sudo find "$D" -mindepth 1 | sort) == $(files | sort) ]]; }
quiet() { [[ ! -s $1 ]] || { sed 's/^/     stderr: /' "$1"; return 1; }; }
until_true() {  # SECS CMD...: true once CMD is, within SECS
    local n=$1; shift
    while ((n-- > 0)); do "$@" && return 0; sleep 1; done
    return 1
}
waiter() { awk '$2 == "0.1" && $3 == "0.1" && $5 == "reserve" && $6 == "sh" { f = 1 } END { exit !f }' "$D/.reserve.wait"; }
alive() { kill -0 "$1" 2>/dev/null || sudo kill -0 "$1" 2>/dev/null; }
gone() { ! alive "$1"; }

# A --reserve behind a row whose holder lives: in line, then (holder gone) reserved.
reserve_case() {  # N OWNER RUNNER (m: as m; root: with sudo)
    local n=$1 owner=$2 runner=$3 hold rp cmd
    fresh "$owner"
    sleep 600 & hold=$! pids+=("$hold")
    printf '%s 9999 0 - reserve held by the test\n' "$hold" | dd of="$D/.reserve" conv=nocreat,notrunc oflag=append status=none
    # CMD keeps what it sees: the ledger and the line, once it has the room.
    cmd=(sh -c 'cat "$1/.reserve" >"$2/seen.$3"; echo "--" >>"$2/seen.$3"; cat "$1/.reserve.wait" >>"$2/seen.$3"' sh "$D" "$T" "$n")
    if [[ $runner == root ]]; then
        sudo env HOME="$HOME" PATH="$PATH" CMP_LEDGER_DIR="$D" WEAR_LOG=off \
            timeout 200 "$CMP_RUN" --reserve 0.1 0.1 "${cmd[@]}" 2>"$T/err.$n" &
    else
        CMP_LEDGER_DIR="$D" WEAR_LOG=off timeout 200 "$CMP_RUN" --reserve 0.1 0.1 "${cmd[@]}" 2>"$T/err.$n" &
    fi
    pids+=($!)
    # cmp-run.sh's PID (its row's in line; as root, past sudo and timeout).
    check "$n: $runner's --reserve waits in line on $owner's files" until_true 20 waiter
    rp=$(awk '$2 == "0.1" && $3 == "0.1" && $5 == "reserve" && $6 == "sh" { print $1; exit }' "$D/.reserve.wait")
    [[ -n $rp ]] || { bad "$n: no row in line: the rest of the case is skipped"; return; }
    if ((n == 1)); then   # its next try (30 s on) changes its row in line, not adds one
        sleep 35
        check "$n: one row in line after its second try" test "$(grep -c "^$rp " "$D/.reserve.wait")" = 1
    fi
    kill "$hold"
    check "$n: reserved and done once the holder is gone" until_true 70 gone "$rp"
    check "$n: CMD saw its row in the ledger, and the holder's pruned" \
        awk -v p="$rp" '/^--$/ { exit } $1 == p && $2 == "0.1" && $3 == "0.1" && $4 == "-" && $5 == "reserve" { f = 1 }
            $1 != p { bad = 1 } END { exit !(f && !bad) }' "$T/seen.$n"
    check "$n: CMD saw the line empty (leave_line)" awk '/^--$/ { l = 1; next } l { exit 1 }' "$T/seen.$n"
    check "$n: nothing on stderr" quiet "$T/err.$n"
    check "$n: the same files, still $owner's" same_files
    check "$n: no temporary files left" no_leftovers
}
reserve_case 1 root m
reserve_case 2 m root

# 3: a queue worker's row goes to its job's sudo, then is pruned.
fresh root
cat >"$T/job.sh" <<'EOF'
#!/bin/sh
# D OUT GUEST DISK MEM SLOT: each ledger row with its holder's comm
while read -r p rest; do echo "$p $(cat /proc/$p/comm 2>/dev/null) $rest"; done <"$1/.reserve" >"$2"
EOF
chmod 755 "$T/job.sh"
(cd ~/backotter-work && CMP_LEDGER_DIR="$D" WEAR_LOG=off CMP_CMD="$T/job.sh $D $T/job.out" \
    timeout 120 "$CMP_RUN" "$TAG" 1 fakeguest=0.1%0.1 2>"$T/err.3")
check "3: the job ran (exit 0)" grep -qx "fakeguest 0 $(hostname -s)" ~/backotter-work/cmp-logs/"$TAG"/done
check "3: the job's row was its sudo's" awk -v t="$TAG" '$2 == "sudo" && $3 == "0.1" && $4 == "0.1" && $6 == t { f = 1 } END { exit !f }' "$T/job.out"
check "3: the row pruned once the job ended" test ! -s "$D/.reserve"
check "3: nothing on stderr" quiet "$T/err.3"
check "3: the same files, still root's" same_files
check "3: no temporary files left" no_leftovers
((fails == 0)) && echo "cmp-run-test: all passed" || echo "cmp-run-test: $fails FAILed"
((fails == 0))
