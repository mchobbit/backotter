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
# a minute (cmp-run.sh waits 30 s between tries), except case 0, which is
# a moment:
#   0 --ceiling and --free with no ledger there at all: each prints its
#     "DISK MEM" (--free also "waiting N" and "after DISK MEM", and never
#     more than the ceiling: its DISK within the ceiling's, its MEM, for a
#     job's D + M, within the ceiling's two together), and neither makes a file, not even the
#     ledger's; then 0b, --free against a ledger and a line that hold rows
#     (one an older cmp-run.sh's, with no SINCE), 0c, which
#     pins the one thing a huge row cannot: that a row within the 2 GiB of
#     headroom ceiling() holds back and budget() doesn't comes off the
#     memory once, not twice, and 0d, that the ledger's own files on the RAM
#     disk cost the room nothing (a tenth of a GiB, before EPS_GIB) and a
#     --reserve of exactly the ceiling is admitted at once;
#   1 root's files, m's --reserve: waits in line (queue_up adds, then
#     updates its row), gets the room once the row ahead's holder is gone
#     (prune), takes a row and leaves the line (leave_line);
#   2 m's files, root's --reserve: the same;
#   3 root's files, m's queue worker: its row goes to the job's sudo (the
#     rename of the row), and is pruned once the job ends;
#   4-6 backfill, on m's files, with a ledger row that leaves about 1 GiB of
#     RAM disk (its size less 2.05 GiB: the tmpfs's size is fixed, so the room
#     is exactly known; skipped where its free space or the memory can't give
#     that) and figures of 0 memory (BASE_GIB=0, HOT_C and NVME_C out of
#     reach, so other sessions' memory and heat stay out of it):
#   4 a young head in line that needs 2 GiB, more than the room: --free's
#     "after" is the room all the same, and a 0.1 GiB --reserve passes it and
#     runs at once;
#   5 the same head having waited past BACKFILL_MAX (its SINCE an hour ago; a
#     row with no SINCE, and BACKFILL_MAX=0 with a young one, the same):
#     "after" is 0, the 0.1 GiB --reserve waits in line, and starts once the
#     ledger row is gone and the head's 2 GiB is off the room;
#   6 a real head: a 2 GiB --reserve waits, the ledger row is swapped for one
#     that leaves it just 2 GiB, and a 0.5 GiB --reserve that comes then
#     (first, as a waiter polling before the head would) waits; the head runs
#     first, and the small one only once the head has ended.
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
# A row in line (PID DISK MEM HOT_C SINCE WHAT...) for a --reserve DISK MEM sh ...
waiter() { inline "${1:-0.1}" "${2:-0.1}"; }
inline() { awk -v d="$1" -v m="$2" '$2 == d && $3 == m && $5 ~ /^[0-9]+$/ && $6 == "reserve" && $7 == "sh" { f = 1 } END { exit !f }' "$D/.reserve.wait"; }
alive() { kill -0 "$1" 2>/dev/null || sudo kill -0 "$1" 2>/dev/null; }
gone() { ! alive "$1"; }

# 0: --ceiling and --free write nothing, and --free stays within the ceiling
# (its MEM is for a job's D + M: within the ceiling's DISK + MEM).
# (pick-host asks every host --free over ssh, so it may not touch the ledger.)
sudo find "$D" -mindepth 1 -delete
cf=$(CMP_LEDGER_DIR="$D" "$CMP_RUN" --ceiling 2>"$T/err.0")
ff=$(CMP_LEDGER_DIR="$D" "$CMP_RUN" --free 2>>"$T/err.0")
check "0: --ceiling prints DISK MEM" grep -Eqx '[0-9]+[.][0-9] -?[0-9]+[.][0-9]' <<<"$cf"
check "0: --free prints DISK MEM, then waiting N, then after DISK MEM" \
    awk 'NR == 1 && /^[0-9]+[.][0-9] [0-9]+[.][0-9]$/ { f = 1 } NR == 2 && /^waiting [0-9]+$/ { w = 1 }
        NR == 3 && /^after [0-9]+[.][0-9] [0-9]+[.][0-9]$/ { a = 1 }
        END { exit !(NR == 3 && f && w && a) }' <<<"$ff"
check "0: with nobody in line, after is the free room" \
    awk 'NR == 1 { r = $0 } NR == 3 { exit !("after " r == $0) }' <<<"$ff"
check "0: --free is within the ceiling" \
    awk -v c="$cf" 'NR == 1 { split(c, x, " "); exit !($1 + 0 <= x[1] + 0 && $2 + 0 <= x[1] + x[2] + 0.001) }' <<<"$ff"
check "0: neither made a file" test -z "$(sudo find "$D" -mindepth 1)"
check "0: nothing on stderr" quiet "$T/err.0"

# 0b: --free against a ledger that holds rows, and a line with rows in it: the
# live rows' room is subtracted (a row of 1e6 GiB leaves none), a row whose
# holder is gone doesn't count, and "waiting N" counts the line's live rows
# only.  Still nothing written: these are the files this test made, unchanged.
fresh m
sleep 600 & hold0=$! pids+=("$hold0")
printf '%s 1000000 1000000 - reserve held by the test\n' "$hold0" |
    dd of="$D/.reserve" conv=nocreat,notrunc oflag=append status=none
# (The line's row has no SINCE, as an older cmp-run.sh's: read all the same.)
printf '%s 0.1 0.1 95 reserve held by the test\nnotapid 1 1 95 junk, as a torn write leaves\n' "$hold0" |
    dd of="$D/.reserve.wait" conv=nocreat,notrunc oflag=append status=none
f0b=$(CMP_LEDGER_DIR="$D" "$CMP_RUN" --free 2>"$T/err.0b")
# (END and NR, not "NR == 1" alone: a --free that printed nothing at all would
# pass an exit taken in a rule that never ran.)
check "0: --free takes off what the ledger's live rows hold" \
    awk 'NR == 1 { ok = ($1 + 0 == 0 && $2 + 0 == 0) } END { exit !(NR == 3 && ok) }' <<<"$f0b"
check "0: --free counts the line's live rows only" grep -qx "waiting 1" <<<"$f0b"
check "0: --free's after is none either" grep -qx "after 0.0 0.0" <<<"$f0b"
kill "$hold0"
if awk 'NR == 1 { exit !($1 + 0 == 0 && $2 + 0 == 0) }' <<<"$ff"; then
    ok "0: (this host has no room of its own: the gone-holder check is skipped)"
else
    check "0: a row whose holder is gone doesn't count" until_true 10 sh -c \
        "CMP_LEDGER_DIR='$D' '$CMP_RUN' --free 2>/dev/null |
             awk 'NR == 1 { exit !(\$1 + 0 > 0 || \$2 + 0 > 0) }'"
fi
check "0: nothing on stderr" quiet "$T/err.0b"
check "0: the same files, still m's" same_files
check "0: no temporary files left" no_leftovers

# 0c: what a row takes off the memory, which 0b's 1e6-GiB row cannot pin, since
# min(ceiling, budget) and the old min(ceiling - held, budget) both floor that
# at 0.0 0.0.  A SMALL row tells them apart, because the first 2 GiB of rows sit
# inside the headroom ceiling() holds back and budget() does not: --free must
# report the room admission would really give (a --reserve of that much starts
# at once), so a 1.5 GiB memory row costs the figure nothing at all while the
# ceiling is what binds, where the old formula always lost the whole 1.5.  A
# 0-disk row keeps the disk out of it (budget() takes the rows off the disk,
# there as in admission, so --free does lose that).  Since 2026-11-24 --free's
# memory is for a job's D + M, and what binds it is the ceiling's DISK + MEM
# (cj below), which budget() is 3 GiB over with no rows here (the 2 GiB of
# headroom and the RAM disk's 1 GiB): the row must cost --free nothing.
#
# The two formulas differ only while the ceiling binds and budget()'s live
# memory check (MemAvailable and the rest) is above it, so BASE_GIB is used to
# put the ceiling 2 GiB up from nothing, well under that check: BASE_GIB=0
# gives the ceiling with no base at all, and BASE_GIB=that-2 leaves a 2 GiB
# memory ceiling.  Both calls get the same BASE_GIB, as a host's own would be.
# Where the live check binds anyway (m0 under the ceiling) there is nothing to
# tell apart and the check is skipped; a host whose live check landed on
# exactly its ceiling would read the same either way, and is the one case this
# can cry wolf over.
fresh m
c00=$(BASE_GIB=0 CMP_LEDGER_DIR="$D" "$CMP_RUN" --ceiling 2>"$T/err.0c")
B=$(awk 'NR == 1 { b = $2 - 2; if (b >= 0) printf "%.1f", b }' <<<"$c00")
if [[ -z $B ]]; then
    ok "0: (this host has under 2 GiB of memory ceiling without a base: the headroom check is skipped)"
else
    cm=$(BASE_GIB="$B" CMP_LEDGER_DIR="$D" "$CMP_RUN" --ceiling 2>>"$T/err.0c" |
             awk 'NR == 1 { print $2 + 0 }')
    cj=$(BASE_GIB="$B" CMP_LEDGER_DIR="$D" "$CMP_RUN" --ceiling 2>>"$T/err.0c" |
             awk 'NR == 1 { print $1 + $2 }')
    m0=$(BASE_GIB="$B" CMP_LEDGER_DIR="$D" "$CMP_RUN" --free 2>>"$T/err.0c" |
             awk 'NR == 1 { print $2 + 0 }')
    sleep 600 & hold1=$! pids+=("$hold1")
    printf '%s 0 1.5 - reserve held by the test\n' "$hold1" |
        dd of="$D/.reserve" conv=nocreat,notrunc oflag=append status=none
    m1=$(BASE_GIB="$B" CMP_LEDGER_DIR="$D" "$CMP_RUN" --free 2>>"$T/err.0c" |
             awk 'NR == 1 { print $2 + 0 }')
    if awk -v cm="$cm" -v cj="$cj" -v m0="$m0" 'BEGIN { exit !(cm >= 1.9 && m0 >= cj - 0.05) }'; then
        # The old formula printed exactly cj - 1.5 here; this one prints cj.
        check "0: a row inside the ceiling's headroom comes off the memory once, not twice" \
            awk -v cj="$cj" -v m1="$m1" 'BEGIN { exit !(m1 > cj - 1.45) }'
    else
        ok "0: (this host's live memory check binds, not its ceiling: the headroom check is skipped)"
    fi
    kill "$hold1"
fi
check "0: nothing on stderr" quiet "$T/err.0c"
check "0: the same files, still m's" same_files
check "0: no temporary files left" no_leftovers

# 0d: the ledger's own few KiB cost no room (choubox, 2026-11-17).  The ledger's
# three files are on the RAM disk itself, so with it otherwise empty df shows
# 4 KiB used and budget()'s room (the free space less 1 GiB) is a hair under
# the ceiling's (the size less 1 GiB): floored without EPS_GIB's tolerance that
# was a whole tenth less -- 2.9 against a ceiling of 3.0 on choubox's 4 GiB
# tmpfs -- and a --reserve of exactly the ceiling, as SticTest's 3 6 is there,
# could never be admitted however long it waited.  So --free's RAM disk room
# must be the ceiling's here, and such a --reserve must start at once.  Memory
# and heat are kept out of it as 4-6 do; skipped when the RAM disk holds more
# than EPS_GIB's worth of anything (half a MiB: then the room really is lower,
# and must be) or has no memory room for that much RAM disk (a job's D is
# memory too: the --reserve D 0 asks --free's MEM for D).
B0=(env BASE_GIB=0 HOT_C=200 NVME_C=200 CMP_LEDGER_DIR="$D" WEAR_LOG=off)
fresh m
# A line of its own, so that the ledger certainly holds a page of the tmpfs
# (empty files need none, and the case would then prove nothing).  It is junk
# that no holder owns, as a torn write leaves: live_rows drops it, so it takes
# nothing off the room and only its page is in the way.
printf 'notapid 1 1 - junk, as a torn write leaves\n' |
    dd of="$D/.reserve" conv=nocreat,notrunc oflag=append status=none
used_k=$(df -Pk /mnt/bko-ram | awk 'NR == 2 { print $3 }')
c0=$("${B0[@]}" "$CMP_RUN" --ceiling 2>"$T/err.0d")
f0=$("${B0[@]}" "$CMP_RUN" --free 2>>"$T/err.0d")
if ! awk -v u="${used_k:-0}" -v m="$(awk 'NR == 1 { print $2 + 0 }' <<<"$f0")" -v d="${c0%% *}" \
        'BEGIN { exit !(u > 0 && u <= 512 && m >= d + 0) }'; then
    ok "0: (the RAM disk holds more than the ledger's files (${used_k:-?} KiB), or no memory room with BASE_GIB=0: the ledger's-own-files check is skipped)"
else
    check "0: the ledger's own files take nothing off the RAM disk room" \
        awk -v c="$c0" 'NR == 1 { split(c, x, " "); exit !($1 + 0 == x[1] + 0) }' <<<"$f0"
    "${B0[@]}" timeout 100 "$CMP_RUN" --reserve "${c0%% *}" 0 \
        sh -c 'date +%s.%N >"$1"' sh "$T/ran.0d" 2>>"$T/err.0d" &
    pids+=($!)
    check "0: a --reserve of exactly the ceiling's RAM disk is admitted at once" until_true 20 test -s "$T/ran.0d"
fi
check "0: nothing on stderr" quiet "$T/err.0d"
check "0: the same files, still m's" same_files
check "0: no temporary files left" no_leftovers

# A --reserve behind a row whose holder lives: in line, then (holder gone) reserved.
reserve_case() {  # N OWNER RUNNER (m: as m; root: with sudo)
    local n=$1 owner=$2 runner=$3 hold rp cmd since
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
    rp=$(awk '$2 == "0.1" && $3 == "0.1" && $6 == "reserve" && $7 == "sh" { print $1; exit }' "$D/.reserve.wait")
    [[ -n $rp ]] || { bad "$n: no row in line: the rest of the case is skipped"; return; }
    if ((n == 1)); then   # its next try (30 s on) changes its row in line, not adds one
        since=$(awk -v p="$rp" '$1 == p { print $5 }' "$D/.reserve.wait")
        check "$n: its row in line has a SINCE, about now" \
            awk -v s="$since" -v now="$(date +%s)" 'BEGIN { exit !(s ~ /^[0-9]+$/ && now - s >= 0 && now - s < 60) }'
        sleep 35
        check "$n: one row in line after its second try" test "$(grep -c "^$rp " "$D/.reserve.wait")" = 1
        check "$n: its row kept its SINCE" awk -v p="$rp" -v s="$since" '$1 == p { exit !($5 == s) }' "$D/.reserve.wait"
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

# 4-6: backfill.  The ledger is ours alone, but budget() reads the real tmpfs and
# memory: the RAM disk's room is size - 1 - what the ledger holds (or its free
# space - 1, when less), so a ledger row of size - 1 - R leaves exactly R while the
# tmpfs has R + 1 GiB free; the memory is kept out of it with 0-memory figures and
# BASE_GIB=0, and the heat with HOT_C and NVME_C out of reach.
B4=(env BASE_GIB=0 HOT_C=200 NVME_C=200 CMP_LEDGER_DIR="$D" WEAR_LOG=off)
read -r size_k avail_k < <(df -Pk /mnt/bko-ram | awk 'NR == 2 { print $2, $4 }')
S=$(awk -v k="$size_k" 'BEGIN { printf "%.3f", k / 1048576 - 1 }')   # budget()'s disk, no rows
fresh m
mem4=$("${B4[@]}" "$CMP_RUN" --free 2>/dev/null | awk 'NR == 1 { print $2 + 0 }')
if ! awk -v a="$avail_k" -v s="$S" -v m="${mem4:-0}" 'BEGIN { exit !(a / 1048576 - 1 >= 3.5 && s >= 3.5 && m >= 0.1) }'; then
    ok "4-6: (this host's RAM disk has under 4.5 GiB free, or no memory room even with BASE_GIB=0: backfill cases skipped)"
else
    lrow() { awk -v s="$S" -v r="$1" 'BEGIN { printf "%.3f", s - r }'; }   # R -> a ledger row's DISK leaving R
    hold() {  # DISK: a ledger row held by a sleep of ours: its PID in $held
        sleep 600 & held=$! pids+=("$held")
        printf '%s %s 0 - reserve held by the test\n' "$held" "$1" | dd of="$D/.reserve" conv=nocreat,notrunc oflag=append status=none
    }
    head_row() {  # SINCE: a 2 GiB waiter in line, of a sleep of ours ("-": no SINCE): its PID in $hp
        sleep 600 & hp=$! pids+=("$hp")
        printf '%s 2.0 0 200 %s\n' "$hp" "$([[ $1 == - ]] && echo "reserve the test's head" || echo "$1 reserve the test's head")" |
            dd of="$D/.reserve.wait" conv=nocreat,notrunc oflag=append status=none
    }
    after() { "${B4[@]}" "$@" "$CMP_RUN" --free 2>>"$T/err.$n" | awk '$1 == "after" { print $2 + 0 }'; }
    small() {  # DISK FILE: a --reserve DISK 0 that writes the time it ran to FILE
        "${B4[@]}" timeout 150 "$CMP_RUN" --reserve "$1" 0 sh -c 'date +%s.%N >"$1"' sh "$2" 2>>"$T/err.$n" &
        pids+=($!)
    }

    n=4; : >"$T/err.$n"
    fresh m; hold "$(lrow 1.05)"; head_row "$(date +%s)"
    check "4: --free's after passes the young head that needs more than the room" \
        awk -v a="$(after)" 'BEGIN { exit !(a >= 0.9 && a <= 1.1) }'
    small 0.1 "$T/ran.4"
    check "4: a 0.1 GiB --reserve passes it and runs at once" until_true 15 test -s "$T/ran.4"
    check "4: the head is still in line, first" awk -v p="$hp" 'NR == 1 { exit !($1 == p && $2 == "2.0") }' "$D/.reserve.wait"
    check "4: nothing on stderr" quiet "$T/err.4"
    check "4: the same files, still m's" same_files
    kill "$hp" "$held"

    n=5; : >"$T/err.$n"
    fresh m; hold "$(lrow 1.05)"; head_row "$(( $(date +%s) - 3600 ))"
    check "5: --free's after holds back for the head that has waited past BACKFILL_MAX" test "$(after)" = 0
    check "5: and for a young one with BACKFILL_MAX=0" test "$(after BACKFILL_MAX=0)" = 0
    check "5: a young one is passed with BACKFILL_MAX=10000 (its own figure)" \
        awk -v a="$(after BACKFILL_MAX=10000)" 'BEGIN { exit !(a >= 0.9) }'
    small 0.1 "$T/ran.5"
    check "5: a 0.1 GiB --reserve waits in line behind it" until_true 20 inline 0.1 0
    check "5: and doesn't run" test ! -e "$T/ran.5"
    kill "$held"                       # the room frees: the head's 2 GiB comes off it first
    check "5: it runs once the ledger row is gone, beside the head's room" until_true 70 test -s "$T/ran.5"
    kill "$hp"
    fresh m; head_row -                # an older cmp-run.sh's row: waited forever
    hold "$(lrow 1.05)"
    check "5: a row with no SINCE is held back for" test "$(after)" = 0
    kill "$hp" "$held"
    check "5: nothing on stderr" quiet "$T/err.5"
    check "5: the same files, still m's" same_files

    n=6; : >"$T/err.$n"
    fresh m; hold "$(lrow 1.05)"; a6=$held
    "${B4[@]}" timeout 200 "$CMP_RUN" --reserve 2.0 0 \
        sh -c 'date +%s.%N >"$1.start"; sleep 8; date +%s.%N >"$1.end"' sh "$T/head.6" 2>>"$T/err.6" &
    pids+=($!)
    check "6: a 2 GiB --reserve waits in line (1 GiB of room)" until_true 20 inline 2.0 0
    hold "$(lrow 2.05)"                # the room it fits exactly, once the first row is gone
    kill "$a6"; wait "$a6" 2>/dev/null
    small 0.5 "$T/ran.6"               # comes now, likely before the head's next try
    check "6: a 0.5 GiB --reserve that comes then waits: the head fits, and has its room first" \
        until_true 10 inline 0.5 0
    check "6: the head runs" until_true 45 test -s "$T/head.6.start"
    check "6: the small one runs after it" until_true 90 test -s "$T/ran.6"
    check "6: only once the head had ended" \
        awk -v e="$(cat "$T/head.6.end" 2>/dev/null)" -v s="$(cat "$T/ran.6" 2>/dev/null)" 'BEGIN { exit !(e != "" && s + 0 >= e + 0) }'
    kill "$held"
    check "6: nothing on stderr" quiet "$T/err.6"
    check "6: the same files, still m's" same_files
    check "6: no temporary files left" no_leftovers
fi
((fails == 0)) && echo "cmp-run-test: all passed" || echo "cmp-run-test: $fails FAILed"
((fails == 0))
