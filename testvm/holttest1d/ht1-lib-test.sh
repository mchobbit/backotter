#!/usr/bin/env bash
# ht1-lib.sh's self-test: fake phases, no VM.  Run as root on a test host,
# under a reservation of the RAM disk (1 GiB is plenty):
#
#   cd ~/backotter-work && ./cmp-run.sh --reserve 1 1 sudo -E PATH/ht1-lib-test.sh
#
# It works in /mnt/bko-ram/ht1-lib-test (HT_WORK) and /bkoscratch/ht1-lib-test
# (HT_RECORDS), keeps its fake rustic-bko sets and other fixtures in
# /mnt/bko-ram/ht1-lib-test-fx, and removes all three at the end.  One ok or
# FAIL line per case; exit 1 if any case FAILs.  Its fake driver is this
# script too, `ht1-lib-test.sh run RUN [PHASE...]`: ht1.sh's calls in ht1.sh's
# order, with phases that write small files, steered by T1_* (t1_load).
set -uo pipefail
HERE=$(dirname "$(realpath "$0")")
FX=/mnt/bko-ram/ht1-lib-test-fx

if [[ ${1:-} == run ]]; then
    exec 9>&-   # (the self-test's own lock)
    G=fake RUN=$2; shift 2
    PHASES=("$@"); [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(source backup rebuild verify)
    VARIANT="" GV=$G PORT=${T1_PORT:-${T1_PORT0:-}} W=$FX T=$(dirname "$HERE") fails=0
    HOLT=$FX/bko-holt imgs=("$FX/guest.qcow2")
    [[ -z ${T1_RSYNC_FAIL:-} ]] || PATH=$FX/fakebin:$PATH
    say() { echo "$*" | tee -a "$R/report.txt"; }
    ok() { say "PASS $*"; }
    bad() { say "FAIL $*"; fails=$((fails + 1)); }
    # A fake QEMU as vm_up starts one (not the driver's child): a copy of sleep
    # named qemu-system-x86_64 (comm qemu-system-x86, as a real one's), its
    # command line as argv[0], FILE's disk and a hostfwd on PORT, and the
    # run's TMPDIR in its environment.  Prints its PID, also kept in
    # FX/fakeq.pids for the test's clean-up.
    t1_fakeq() {  # FILE PORT
        local i
        (exec -a "qemu-system-x86_64 -drive file=$1,format=qcow2,if=virtio -nic user,ipv6=off,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$2-:22" \
            "$FX/qbin/qemu-system-x86_64" 600) </dev/null >/dev/null 2>&1 &
        echo "$!" >>"$FX/fakeq.pids"
        for ((i = 0; i < 50; i++)); do grep -q '^qemu-system' "/proc/$!/cmdline" 2>/dev/null && break; sleep 0.1; done
        echo "$!"
    }
    gone_pid() { local s; s=$(ps -o stat= -p "$1" 2>/dev/null); [[ -z $s || $s == Z* ]]; }
    # ht1.sh's vm_down, with the test's shorter waits (and no guest to power off).
    vm_down() {
        [[ -n $(ht_qemus "$R") ]] || return 0
        for _ in 1 2 3 4 5 6; do [[ -n $(ht_qemus "$R") ]] || return 0; sleep 0.5; done
        # shellcheck disable=SC2046  # PIDs
        kill $(ht_qemus "$R") 2>/dev/null; sleep 1
        # shellcheck disable=SC2046
        kill -9 $(ht_qemus "$R") 2>/dev/null; sleep 0.2
    }
    t1_load() {  # PHASE: what T1_* asks of it
        local i q
        if [[ ${T1_BIG:-} == "$1" ]]; then
            dd if=/dev/zero of="$R/big" bs=1M count="${T1_BIGMB:-64}" status=none; sleep "${T1_BIGWAIT:-7}"; rm -f "$R/big"
        fi
        if [[ ${T1_OWNQ:-} == "$1" ]]; then   # the run's own VMs (its TMPDIR, not its port), beside the test's VM on K's disk
            q=$(t1_fakeq "$R/dst-0.qcow2" "$T1_QPORT"); echo "$q" >"$R/logs/ownq1.pid"
            ht_qemus "$R" >"$R/logs/qemus.txt"
            vm_down
            if gone_pid "$q"; then echo down >"$R/logs/ownq1.txt"; else echo up >"$R/logs/ownq1.txt"; fi
            q=$(t1_fakeq "$R/src-0.qcow2" "$T1_QPORT"); echo "$q" >"$R/logs/ownq2.pid"   # left up, for ht_end's ht_free
        fi
        if [[ ${T1_KEEPBIG:-} == "$1" ]]; then   # a move slow enough to catch, with few bytes for the P3
            dd if=/dev/urandom of="$R/big" bs=1M count=1 status=none; (cd "$R" && sha256sum big) >"$R/logs/big.sha256"
            mkdir -p "$R/many"; for ((i = 1; i <= 8000; i++)); do : >"$R/many/f$i"; done
        fi
        if [[ ${T1_MOUNTING:-} == "$1" ]]; then   # over the limit while a vm-mount is at work, and R held from outside the driver's tree
            setsid -f sleep 7777.27182 <"$R/logs/source-bootstate.txt" >/dev/null 2>&1
            sleep 0.5; pgrep -f '^sleep 7777\.27182$' >"$R/logs/holder.pid"
            dd if=/dev/zero of="$R/big" bs=1M count=64 status=none
            (exec -a "$T/vm-mount" bash -c 'sleep 8; : >"$1"' _ "$R/logs/mount-done")
            rm -f "$R/big"
        fi
        [[ ${T1_SLEEP:-} != "$1" ]] || sleep 120   # until the test signals it
        # The same in a function call whose stdio is in R, as sshk ... >"$R/logs/X" is.
        [[ ${T1_REDIR:-} != "$1" ]] || t1_wait >"$R/logs/redir.out" 2>"$R/logs/redir.err" <"$R/logs/source-bootstate.txt"
        # shellcheck disable=SC2154  # unset on purpose: set -u ends the driver here
        [[ ${T1_UNBOUND:-} != "$1" ]] || echo "$t1_unset"
    }
    t1_wait() { sleep 120; }
    phase_source() {
        say "== source: $G (fake)"
        qemu-img create -q -f qcow2 "$R/src-0.qcow2" 1M
        echo '{"storage": {"disks": []}}' >"$R/dumps/src.json"; cp "$R/dumps/src.json" "$S/layout.json"
        echo running >"$R/logs/source-bootstate.txt"
        echo "$TMPDIR" >"$R/logs/source-tmpdir.txt"; mktemp >/dev/null
        "$RUSTIC" --version >"$R/logs/source-rustic.txt"
        t1_load source
        ok "source (fake)"
    }
    phase_backup() {
        say "== backup (fake)"
        mkdir -p "$S/repo"; echo pack >"$S/repo/pack"; echo pw >"$S/repo-password"
        cp "$FX/meta.json" "$R/src-meta.json"
        t1_load backup
        ok "backup (fake)"
    }
    phase_rebuild() {
        local f
        say "== rebuild (fake)"
        [[ -s $R/dumps/src.json && -d $S/repo ]] || bad "rebuild's inputs (dumps/src.json, share/repo)"
        for f in rescuer dst-0 decoy; do qemu-img create -q -f qcow2 "$R/$f.qcow2" 1M; done
        echo "the plan" >"$R/logs/rebuild-dry-partition.out"
        if [[ ${T1_OVERLAY:-} == 1 ]]; then
            qemu-img create -q -f qcow2 -b "$R/rescuer.qcow2" -F qcow2 "$R/rescuer-try.qcow2"
            ln -s "$R/src-0.qcow2" "$R/src-link"
            mkdir -p "$R/check-x"; ln -s "$R/dst-0.qcow2" "$R/check-x/sculpt-check.qcow2"
            bad "rebuild (fake): a failure with an overlay into R"; return
        fi
        if [[ ${T1_LEAK:-} == 1 ]]; then
            truncate -s 1M "$R/tmp-leak.raw"; losetup -f --show "$R/tmp-leak.raw" >"$R/logs/leak-loop.txt"
            mkdir -p "$R/mnt-leak"; mount -t tmpfs -o size=1m t1leak "$R/mnt-leak" && echo x >"$R/mnt-leak/x"
            findmnt -rn -o TARGET "$R/mnt-leak" >"$R/logs/leak-mount.txt"
            setsid -f sleep 7777.31415 <"$R/logs/leak-loop.txt" >/dev/null 2>&1
            sleep 0.5; pgrep -f '^sleep 7777\.31415$' >"$R/logs/leak-holder.txt"
            bad "rebuild (fake): a failure that leaves R held"; return
        fi
        t1_load rebuild
        ok "rebuild (fake)"
    }
    phase_verify() {
        local f
        say "== verify (fake)"
        (cd "$R" && find . -mindepth 1 ! -path ./.ht ! -path './.ht/*' | sed 's|^\./||' | LC_ALL=C sort) >"$R/logs/verify-saw.txt"
        for f in dst-0.qcow2 src-meta.json dumps/src.json logs/source-bootstate.txt; do
            [[ -e $R/$f ]] || bad "verify's input $f"
        done
        cp "$R/src-meta.json" "$R/dst-meta.json"; echo '{"storage": {"disks": []}}' >"$R/dumps/dst.json"
        [[ -z ${T1_INPLACE:-} ]] || echo "rewritten in place" >"$S/layout.json"   # as > and cp onto a file do
        t1_load verify
        ok "verify (fake)"
    }
    ht_release() {
        local i
        [[ -d $R ]] && echo "$RUN" >>"$FX/released"
        [[ -z ${T1_HOLD:-} ]] || { : >"$FX/hold-$RUN"; sleep "$T1_HOLD"; }
        # T1_GO: until the test says go (FX/go-RUN), up to 5 min.
        if [[ -n ${T1_GO:-} ]]; then
            : >"$FX/hold-$RUN"
            for ((i = 0; i < 1500; i++)); do [[ -e $FX/go-$RUN ]] && break; sleep 0.2; done
        fi
    }

    . "$HERE/ht1-lib.sh"
    [[ -z ${T1_LEDGER:-} ]] || HT_LEDGER=$T1_LEDGER   # a ledger of the test's own (case 36)
    ht_paths
    S=$R/share
    HT_BULK=(share 'src-*.qcow2' 'dst-*.qcow2' decoy.qcow2 rescuer.qcow2 rescuer-try.qcow2 'check-*/sculpt-check.qcow2' big many rustic-bko tmp)
    HT_ZIP=(src-meta.json dst-meta.json)
    ht_require
    case ${PHASES[0]} in   # ht1.sh's, as they are there
        backup)  ht_seed share/repo/ 'dst-*.qcow2' decoy.qcow2 rescuer.qcow2 tmp/ ;;
        rebuild) ht_seed 'dst-*.qcow2' decoy.qcow2 rescuer.qcow2 tmp/ ;;
        verify)  ht_seed decoy.qcow2 rescuer.qcow2 tmp/ ;;
    esac
    ht_begin; mkdir -p "$S"
    ht_rustic_take linux
    [[ -n ${T1_APPEND:-} ]] || : >"$R/report.txt"   # (ht1-bsd.sh only appends)
    say "HoltTest1 $G (ht1-lib-test), run $RUN, $(date -u +%FT%TZ), phases: ${PHASES[*]}"
    ht_manifest "$FX/rescuer.qcow2" "$FX/bin/extra.deb" "$FX/bin/none.deb"
    for p in "${PHASES[@]}"; do
        ht_phase_start "$p"; "phase_$p"; ht_phase_end "$p"
        [[ $fails == 0 ]] || { say "stopped after $p"; break; }
    done
    ht_summary
    say "RESULT: $([[ $fails == 0 ]] && echo PASS || echo "FAIL ($fails)")"
    [[ -z ${T1_TERM_AFTER:-} ]] || kill -TERM $$   # a signal after the RESULT line
    [[ $fails == 0 ]]
    exit
fi

[[ $EUID == 0 ]] || { echo "ht1-lib-test: run as root, under cmp-run.sh --reserve 1 1 (the header says how)" >&2; exit 1; }
mountpoint -q /mnt/bko-ram || { echo "ht1-lib-test: no RAM disk at /mnt/bko-ram" >&2; exit 1; }
(($(df -Pk /mnt/bko-ram | awk 'NR == 2 { print $4 }') >= 2 << 20)) ||
    { echo "ht1-lib-test: under 2 GiB free in /mnt/bko-ram: too little for its cases" >&2; exit 1; }
# One self-test at a time: its start-up deletes its roots and lock files.
exec 9>>/run/lock/bko-ht1-test.lock && flock -n 9 ||
    { echo "ht1-lib-test: another run of it holds /run/lock/bko-ht1-test.lock" >&2; exit 1; }
unset "${!HT_@}" "${!T1_@}"
export HT_WORK=/mnt/bko-ram/ht1-lib-test HT_RECORDS=/bkoscratch/ht1-lib-test
LEDGER=/mnt/bko-ram/.reserve
# The ledger row this test runs under, by a walk of its own (through /proc), to check the lib's.
ROW="" p=$$
for _ in $(seq 64); do
    awk -v p="$p" '$1 == p { f = 1 } END { exit !f }' "$LEDGER" 2>/dev/null && { ROW=$p; break; }
    p=$(awk '$1 == "PPid:" { print $2 }' "/proc/$p/status" 2>/dev/null)
    [[ ${p:-0} -gt 1 ]] || break
done
[[ -n $ROW ]] || { echo "ht1-lib-test: no cmp-run.sh --reserve row among its ancestors (the header says how)" >&2; exit 1; }
RES=$(awk -v p="$ROW" '$1 == p { print $2; exit }' "$LEDGER")
awk -v r="$RES" 'BEGIN { exit !(r >= 1) }' || { echo "ht1-lib-test: reserve at least 1 GiB, not $RES" >&2; exit 1; }
DEAD=999999; while [[ -e /proc/$DEAD ]]; do DEAD=$((DEAD + 1)); done
LOCKS=/run/lock/bko-ht1
# The fake runs' port (ht_paths takes only a port): one nothing listens on or forwards.
T1_PORT0=""
for p in $(seq 64990 64999); do
    [[ -z $(ss -Hltn "sport = :$p" 2>/dev/null) && -z $(pgrep -f "^qemu-system.*:$p-:22") ]] && { T1_PORT0=$p; break; }
done
[[ -n $T1_PORT0 ]] || { echo "ht1-lib-test: no free port in 64990-64999" >&2; exit 1; }
export T1_PORT0

TV=$(dirname "$HERE")   # this copy's testvm/ (vm-mount, vm-umount)
# Case 46's look at a run's disk, by hand: a vm-mount of a file in R at LMP.
LMP=/mnt/ht1-lib-test-look
LST=/run/vm-mount/$(systemd-escape --path "$LMP")
# It is undone by its vm-umount while the test's qemu-nbd (on a file in
# HT_WORK) still serves the state's device; else (by then the device may be
# free, or another's: vm-umount disconnects by number) only its mount and
# state go.
unlook() {
    local dv q=""
    [[ -f $LST ]] || { rmdir "$LMP" 2>/dev/null; return 0; }
    dv=$(head -n 1 "$LST" 2>/dev/null)
    [[ $dv != /dev/nbd* ]] || q=$(cat "/sys/block/${dv#/dev/}/pid" 2>/dev/null)
    if [[ $q =~ ^[0-9]+$ ]] && tr '\0' ' ' 2>/dev/null <"/proc/$q/cmdline" | grep -qF " $HT_WORK/"; then
        "$TV/vm-umount" "$LMP" && { rmdir "$LMP" 2>/dev/null; return 0; }
    fi
    umount -l "$LMP" 2>/dev/null; rm -f "$LST"; rmdir "$LMP" 2>/dev/null
    return 1
}

KIDS=()   # the async drivers and helpers, ended at the exit whatever happens
t1_cleanup() {
    local m l f
    ((${#KIDS[@]} == 0)) || kill -9 "${KIDS[@]}" 2>/dev/null
    unlook >/dev/null 2>&1
    pkill -f '^sleep 7777\.31415$' 2>/dev/null   # the release case's holder, should the lib miss it
    pkill -f '^sleep 7777\.27182$' 2>/dev/null   # case 39's
    if [[ -f $FX/fakeq.pids ]]; then   # the fake QEMU the fake runs started (cases 42-44), should the lib miss one
        while read -r l; do
            [[ $l =~ ^[0-9]+$ && $(readlink "/proc/$l/exe" 2>/dev/null) == "$FX/qbin/qemu-system-x86_64" ]] && kill -9 "$l"
        done <"$FX/fakeq.pids"
    fi
    for m in $(findmnt -rn -o TARGET | awk -v a="$HT_WORK/" -v b="$HT_RECORDS/" 'index($0, a) == 1 || index($0, b) == 1' | sort -r); do
        umount -l "$m"
    done
    while read -r l f; do
        [[ $f == "$HT_WORK"/* || $f == "$HT_RECORDS"/* ]] && losetup -d "$l"
    done < <(losetup -l -n -O NAME,BACK-FILE 2>/dev/null)
    rm -rf --one-file-system "$HT_WORK" "$HT_RECORDS" "$FX"
    rm -f "$LOCKS"/%bkoscratch%ht1-lib-test%*.lock
}
t1_cleanup; trap t1_cleanup EXIT

# Fixtures.
mkdir -p "$FX/bin" "$FX/fakebin" "$FX/qbin"
cp "$(command -v sleep)" "$FX/qbin/qemu-system-x86_64"   # the fake QEMU (cases 42-44)
sha() { sha256sum "$1" | cut -d' ' -f1; }
mkbin() { mkdir -p "${1%/*}"; printf '#!/bin/sh\necho "%s"\n' "$2" >"$1"; chmod 755 "$1"; }   # FILE VERSION
mkbin "$FX/bko-holt" "bko-holt 0.0.0-t1"
# The default set, W/bko-bin (bko-bin's lines: HASH *PLAT/FILE).
mkbin "$FX/bko-bin/linux/rustic-bko" "rustic 0.11.4+t1 (bko-bin)"
printf '%s *linux/rustic-bko\n%s *netbsd/rustic-bko\n' "$(sha "$FX/bko-bin/linux/rustic-bko")" \
    "$(printf x | sha256sum | cut -d' ' -f1)" >"$FX/bko-bin/SHA256SUMS"
echo "fake bko-bin set" >"$FX/bko-bin/BUILD-INFO"
echo "0123456789abcdef0123 (fake)" >"$FX/bko-bin/linux/rustic-commit"
mkbin "$FX/set-star/linux/rustic-bko" "rustic star"
echo "$(sha "$FX/set-star/linux/rustic-bko") *linux/rustic-bko" >"$FX/set-star/SHA256SUMS"
echo "star set" >"$FX/set-star/BUILD-INFO"
# A round's lines (HASH  ./PLAT/FILE), and a digest line that must not match.
mkbin "$FX/set-dot/linux/rustic-bko" "rustic dot"
printf '%s  ./linux/rustic-bko.digest\n%s  ./linux/rustic-bko\n' "$(printf y | sha256sum | cut -d' ' -f1)" \
    "$(sha "$FX/set-dot/linux/rustic-bko")" >"$FX/set-dot/SHA256SUMS"
mkbin "$FX/roundt9/bin/linux/rustic-bko" "rustic round t9"
echo "$(sha "$FX/roundt9/bin/linux/rustic-bko")  ./linux/rustic-bko" >"$FX/roundt9/bin/SHA256SUMS"
mkbin "$FX/set-wrong/linux/rustic-bko" "rustic wrong"
echo "$(printf z | sha256sum | cut -d' ' -f1) *linux/rustic-bko" >"$FX/set-wrong/SHA256SUMS"
mkbin "$FX/set-none/linux/rustic-bko" "rustic none"
echo "$(sha "$FX/set-none/linux/rustic-bko") *freebsd/rustic-bko" >"$FX/set-none/SHA256SUMS"
mkbin "$FX/loose/rustic-bko" "rustic loose"
qemu-img create -q -f qcow2 "$FX/guest.qcow2" 1M
qemu-img create -q -f qcow2 "$FX/rescuer.qcow2" 1M
(cd "$FX" && sha256sum rescuer.qcow2 >rescuer.qcow2.sha256)
echo deb >"$FX/bin/extra.deb"
python3 -c 'import json; print(json.dumps({".": {"type": "dir"}, "etc/x": {"type": "file", "size": 3}}))' >"$FX/meta.json"
# An rsync that fails the move of everything and lets the records-only copy
# through (T1_RSYNC_FAIL=all: fails that too).
printf '#!/bin/sh\n[ "$T1_RSYNC_FAIL" = all ] && exit 11\ncase " $* " in *" --include=/report.txt "*) exec %s "$@" ;; esac\nexit 11\n' \
    "$(command -v rsync)" >"$FX/fakebin/rsync"
chmod 755 "$FX/fakebin/rsync"
# A guest's disk for vm-mount (case 46): an MBR partition from 1 MiB holding
# a small ext4 with an empty etc/fstab, made in the file itself (mke2fs -d
# at an offset: no loop device).
mkdir -p "$FX/lookroot/etc"; : >"$FX/lookroot/etc/fstab"
{ truncate -s 8M "$FX/look.raw" && echo 'start=2048, type=83' | sfdisk -q "$FX/look.raw" &&
    mkfs.ext4 -q -F -E offset=1048576 -d "$FX/lookroot" "$FX/look.raw" 6M; } >"$FX/look.log" 2>&1 || rm -f "$FX/look.raw"
H12=$(sha "$FX/bko-holt" | cut -c1-12)
SDEF=$(sha "$FX/bko-bin/linux/rustic-bko")
BINS="binaries: bko-holt $H12 ($FX/bko-holt); rustic-bko linux"

# Checks.
nfail=0 ncase=0 prob="" nrun=0 rc=0 out=""
chk() { local d=$1; shift; ("$@") >/dev/null 2>&1 || prob+="${prob:+; }$d"; }   # WHAT CMD... (in a subshell)
verdict() {  # N TITLE [LOG...]
    local n=$1 t=$2 f; shift 2
    ncase=$((ncase + 1))
    if [[ -z $prob ]]; then echo "ok $n - $t"; else
        echo "FAIL $n - $t: $prob"; nfail=$((nfail + 1))
        for f; do [[ ! -f $f ]] || { echo "    --- $f"; tail -n 20 "$f" | sed 's/^/    | /'; }; done
    fi
    prob=""
}
drive() {  # RUN [PHASE...]: one fake run, with the caller's env; rc, out
    nrun=$((nrun + 1)) out=$FX/out-$1.$nrun
    timeout -k 10 300 "$0" run "$@" >"$out" 2>&1; rc=$?
}
same() { [[ $1 == "$2" ]]; }
has() { grep -qxF -- "$2" "$1"; }      # FILE LINE
hasre() { grep -qE -- "$2" "$1"; }     # FILE ERE
lacks() { ! grep -qxF -- "$2" "$1"; }  # FILE LINE
starts() { awk -v p="$2" 'index($0, p) == 1 { f = 1 } END { exit !f }' "$1"; }   # FILE PREFIX
there() { local d=$1 f; shift; for f; do [[ -e $d/$f || -L $d/$f ]] || return 1; done; }   # DIR PATH...
gone() { local f; for f; do [[ ! -e $f && ! -L $f ]] || return 1; done; }
nothing_under() { [[ -z $(find "$HT_WORK" "$HT_RECORDS" -mindepth 1 -print -quit 2>/dev/null) ]]; }
ls_of() { (cd "$1" 2>/dev/null && LC_ALL=C ls -A | tr '\n' ' '); }
waitfor() { local i n=$1; shift; for ((i = 0; i < n * 5; i++)); do "$@" >/dev/null 2>&1 && return 0; sleep 0.2; done; return 1; }   # SECONDS CMD...
dead() { local s; s=$(ps -o stat= -p "$1" 2>/dev/null); [[ -z $s || $s == Z* ]]; }   # PID: gone, or a zombie
desc() { local c; for c in $(pgrep -P "$1"); do echo "$c"; desc "$c"; done; }   # PID's descendants
pk() {  # RUN COLUMN: that column of RUN's last row in peaks.tsv
    awk -F'\t' -v run="$1" -v col="$2" 'NR == 1 { for (i = 1; i <= NF; i++) c[$i] = i; next }
        $c["run"] == run { v = $c[col] } END { print v }' "$HT_RECORDS/peaks.tsv" 2>/dev/null
}
nrows() { local n; n=$(wc -l 2>/dev/null <"$HT_RECORDS/peaks.tsv") || n=1; echo $((n - 1)); }
lockfile() { (. "$HERE/ht1-lib.sh"; ht_lockfile "$HT_RECORDS/$1/fake"); }   # RUN
unlocked() { flock -n "$(lockfile "$1")" true; }   # RUN
backing() { qemu-img info -U --output=json "$1" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("backing-filename", ""))'; }
kept_ls="dst-meta.json.zst dumps logs report.txt src-meta.json.zst "

# 1-12: the basic paths (a pass, a FAIL, K.N, references, a seeded rerun, the binaries, a TERM, HT_CLEAN=0,
# the sampler); 13-18 the overrun stop, the sweep, a TERM during the move, the seeded column, the port and
# run-lock refusals; 19-36 further edge cases of the same paths; 37-41 a dead run's R taken by a new run,
# the overrun stop beside a vm-mount at work, and K put back on /bkoscratch; 42-43 the run's own processes
# (on /bkoscratch what others started on K's files is left alone; in RAM an outside holder of R still goes);
# 44 a full RAM disk stops only a run over its reservation; 45 an earlier attempt's VM still up on K's
# files (a run from source on /bkoscratch refuses); 46 a vm-mount someone else made of a file in R stays up.

drive c1 source bogus
chk "exit 2, not $rc" same "$rc" 2
chk "says why" grep -q "no phase bogus (source backup rebuild verify)" "$out"
chk "nothing under either root" nothing_under
verdict 1 "an unknown phase is refused, nothing made" "$out"

HT_RAM=1 HT_RESERVE_PID=$DEAD drive c2
chk "exit 2, not $rc" same "$rc" 2
chk "asks for a reservation" grep -q "no reservation of the RAM disk: run under cmp-run.sh --reserve D M" "$out"
chk "nothing under either root" nothing_under
verdict 2 "HT_RAM=1 without a reservation (HT_RESERVE_PID dead) is refused, nothing made" "$out"

drive c3
k=$HT_RECORDS/c3/fake m=$HT_RECORDS/c3/fake/logs/manifest.txt
chk "exit 0, not $rc" same "$rc" 0
chk "R is gone" gone "$HT_WORK/c3"
chk "K is the kept set, not $(ls_of "$k")" same "$(ls_of "$k")" "$kept_ls"
chk "dumps/ is $(ls_of "$k/dumps")" same "$(ls_of "$k/dumps")" "dst.json src.json "
chk "logs/ has its records" there "$k/logs" kept.txt manifest.txt phases.tsv source-bootstate.txt rebuild-dry-partition.out
chk "kept.txt: pass" hasre "$k/logs/kept.txt" '^pass: records only, [0-9TZ:-]+; deleted share src-\*\.qcow2 .* tmp; zstd src-meta.json dst-meta.json$'
chk "the fsmeta record decompresses to the original" cmp -s <(zstd -dc "$k/src-meta.json.zst") "$FX/meta.json"
chk "peaks.tsv: one row, PASS" same "$(nrows) $(pk c3 result)" "1 PASS"
chk "phases.tsv: the four phases" same "$(awk -F'\t' 'NR > 1 { printf "%s ", $1 }' "$k/logs/phases.tsv")" "source backup rebuild verify "
chk "report: binaries line" has "$k/report.txt" "$BINS $SDEF $FX/bko-bin/linux/rustic-bko (fake bko-bin set; rustic 0123456789ab)"
chk "report: RAM disk line" hasre "$k/report.txt" "^RAM disk: peak [0-9]+\.[0-9] GiB, reserved $RES GiB; memory: peak [0-9]+\.[0-9] GiB anon\$"
chk "report: RESULT: PASS last" same "$(tail -n 1 "$k/report.txt")" "RESULT: PASS"
chk "manifest: driver line" has "$m" "driver $HERE/ht1-lib-test.sh mode ram work $HT_WORK/c3/fake records $k reserved $RES (pid $ROW)"
chk "manifest: rustic-bko line" has "$m" "rustic-bko linux $SDEF $FX/bko-bin/linux/rustic-bko (fake bko-bin set; rustic 0123456789ab)"
chk "manifest: sha256 lines" eval 'has "$m" "sha256 $(sha "$FX/bko-holt") $FX/bko-holt" && has "$m" "sha256 $SDEF $FX/bko-bin/linux/rustic-bko" &&
    has "$m" "sha256 $(sha "$HERE/ht1-lib-test.sh") $HERE/ht1-lib-test.sh" && has "$m" "sha256 $(sha "$HERE/ht1-lib.sh") $HERE/ht1-lib.sh" &&
    has "$m" "sha256 $(sha "$FX/bin/extra.deb") $FX/bin/extra.deb" && ! grep -q "none.deb" "$m" && ! grep -q "^sha256 .*\.qcow2$" "$m"'
chk "manifest: image lines" eval 'hasre "$m" "^image $FX/rescuer\.qcow2 [0-9]+ [0-9]+ [0-9]+ sha256-file $(sha "$FX/rescuer.qcow2")\$" &&
    hasre "$m" "^image $FX/guest\.qcow2 [0-9]+ [0-9]+ [0-9]+\$"'
chk "manifest: version line" has "$m" "version bko-holt 0.0.0-t1; rustic 0.11.4+t1 (bko-bin)"
chk "manifest: env lines" eval 'has "$m" "env HT_RECORDS=$HT_RECORDS" && has "$m" "env HT_WORK=$HT_WORK"'
chk "manifest: host line" starts "$m" "host $(hostname -s) kernel $(uname -r) "
chk "ht_release ran, R still there" grep -qx c3 "$FX/released"
chk "the caller's log says where it went" has "$out" "ht1-lib-test.sh: the run is in $k"
chk "the run lock is free" unlocked c3
verdict 3 "a pass keeps its records only, in K" "$out"

HT_FAIL_AT=rebuild drive c4
k=$HT_RECORDS/c4/fake
chk "exit 1, not $rc" same "$rc" 1
chk "R is gone" gone "$HT_WORK/c4"
chk "kept.txt: fail" hasre "$k/logs/kept.txt" '^fail: everything kept \(exit 1\), [0-9TZ:-]+$'
chk "K holds everything" there "$k" src-0.qcow2 dst-0.qcow2 decoy.qcow2 rescuer.qcow2 rustic-bko src-meta.json share/repo/pack \
    share/repo-password share/layout.json dumps/src.json logs/source-bootstate.txt logs/phases.tsv logs/manifest.txt
chk "TMPDIR was R/tmp" has "$k/logs/source-tmpdir.txt" "$HT_WORK/c4/fake/tmp"
chk "and its file is kept" compgen -G "$k/tmp/tmp.*"
chk "the hook's FAIL line" has "$k/report.txt" "FAIL HT_FAIL_AT=rebuild: a test hook (no check failed)"
chk "stopped after rebuild" has "$k/report.txt" "stopped after rebuild"
chk "RESULT: FAIL (1) last" same "$(tail -n 1 "$k/report.txt")" "RESULT: FAIL (1)"
chk "no verify" lacks "$k/report.txt" "== verify (fake)"
chk "phases.tsv: fails after each" same "$(awk -F'\t' 'NR > 1 { printf "%s:%s ", $1, $8 }' "$k/logs/phases.tsv")" "source:0 backup:0 rebuild:1 "
chk "peaks row: FAIL, rc 1" same "$(pk c4 result) $(pk c4 rc)" "FAIL 1"
verdict 4 "a FAIL (HT_FAIL_AT=rebuild) keeps everything, in K" "$out"

cp "$HT_RECORDS/c3/fake/report.txt" "$FX/c3-report.1"
drive c3
k=$HT_RECORDS/c3/fake
chk "exit 0, not $rc" same "$rc" 0
chk "K.1 is the first run" cmp -s "$FX/c3-report.1" "$k.1/report.txt"
chk "K is the second, a pass" hasre "$k/logs/kept.txt" '^pass:'
chk "the caller's log names both" has "$out" "ht1-lib-test.sh: the run is in $k (the one before it: $k.1)"
chk "no K.2" gone "$k.2"
verdict 5 "a second run: the earlier K becomes K.1" "$out"

T1_OVERLAY=1 drive c6 source backup rebuild
k=$HT_RECORDS/c6/fake
chk "exit 1, not $rc" same "$rc" 1
chk "rescuer-try.qcow2 backs onto K's rescuer.qcow2, not $(backing "$k/rescuer-try.qcow2")" same "$(backing "$k/rescuer-try.qcow2")" "$k/rescuer.qcow2"
chk "its chain opens" qemu-img info --backing-chain "$k/rescuer-try.qcow2"
chk "check-x/sculpt-check.qcow2 points into K" same "$(readlink "$k/check-x/sculpt-check.qcow2")" "$k/dst-0.qcow2"
chk "src-link points into K" same "$(readlink "$k/src-link")" "$k/src-0.qcow2"
chk "R is gone" gone "$HT_WORK/c6"
drive c6 source   # a rerun: the failure becomes K.1, and its references go with it
chk "the rerun: exit 0, not $rc" same "$rc" 0
chk "K.1's rescuer-try.qcow2 backs onto K.1's rescuer.qcow2, not $(backing "$k.1/rescuer-try.qcow2")" same "$(backing "$k.1/rescuer-try.qcow2")" "$k.1/rescuer.qcow2"
chk "  its chain opens" qemu-img info --backing-chain "$k.1/rescuer-try.qcow2"
chk "  K.1's symlinks point into K.1" eval 'same "$(readlink "$k.1/check-x/sculpt-check.qcow2")" "$k.1/dst-0.qcow2" && same "$(readlink "$k.1/src-link")" "$k.1/src-0.qcow2"'
verdict 6 "an overlay and symlinks into R point into K after the move, and into K.N after the next" "$out"

T1_APPEND=1 drive c4 verify
k=$HT_RECORDS/c4/fake saw=$HT_RECORDS/c4/fake/logs/verify-saw.txt
chk "exit 0, not $rc" same "$rc" 0
chk "the seeded note" has "$k/report.txt" "note: this run's state came from the attempt before it ($k until this run moves in, then $k.1)"
chk "the report is this attempt's alone (one RESULT line, not $(grep -c '^RESULT:' "$k/report.txt"))" same "$(grep -c '^RESULT:' "$k/report.txt")" 1
chk "K.1 is the failure, with its disks" eval 'hasre "$k.1/logs/kept.txt" "^fail:" && there "$k.1" src-0.qcow2 share/repo/pack dst-0.qcow2'
chk "K is a pass" hasre "$k/logs/kept.txt" '^pass:'
chk "verify had its inputs" eval 'for f in dst-0.qcow2 src-meta.json dumps/src.json logs/source-bootstate.txt share/layout.json share/repo-password; do has "$saw" "$f" || exit 1; done'
chk "and the source's disk and repository (an earlier phase reruns on what it leaves)" eval 'has "$saw" src-0.qcow2 && has "$saw" share/repo/pack'
chk "the patterns stayed out" eval 'for f in decoy.qcow2 rescuer.qcow2 logs/phases.tsv logs/kept.txt; do lacks "$saw" "$f" || exit 1; done'
chk "tmp/ came empty" eval '! grep -q "^tmp/." "$saw"'
chk "K's phases.tsv: verify only" same "$(awk -F'\t' 'NR > 1 { printf "%s ", $1 }' "$k/logs/phases.tsv")" "verify "
chk "manifest: seeded from K.1" hasre "$k/logs/manifest.txt" " seeded from $k\.1\$"
chk "R is gone" gone "$HT_WORK/c4"
verdict 7 "a verify rerun on a failure's K is seeded into R, passes, K.1 is the failure" "$out"

cp "$HT_RECORDS/c3/fake/report.txt" "$FX/c3-report.2"
drive c3 rebuild
chk "exit 2, not $rc" same "$rc" 2
chk "says why" grep -q "holds records only (logs/kept.txt: pass)" "$out"
chk "no R" gone "$HT_WORK/c3"
chk "K untouched, no K.2" eval 'cmp -s "$FX/c3-report.2" "$HT_RECORDS/c3/fake/report.txt" && gone "$HT_RECORDS/c3/fake.2"'
verdict 8 "a rebuild rerun on a pass's record is refused" "$out"

HT_BKO_BIN=$FX/set-star drive c9a source
chk "a *linux/rustic-bko entry: exit 0, not $rc" same "$rc" 0
chk "  recorded" has "$HT_RECORDS/c9a/fake/report.txt" "$BINS $(sha "$FX/set-star/linux/rustic-bko") $FX/set-star/linux/rustic-bko (star set; rustic ?)"
chk "  the checked copy is what ran" has "$HT_RECORDS/c9a/fake/logs/source-rustic.txt" "rustic star"
HT_BKO_BIN=$FX/set-dot drive c9b source
chk "a ./linux/rustic-bko entry: exit 0, not $rc" same "$rc" 0
chk "  recorded, the set's path for its id" has "$HT_RECORDS/c9b/fake/report.txt" "$BINS $(sha "$FX/set-dot/linux/rustic-bko") $FX/set-dot/linux/rustic-bko ($FX/set-dot; rustic ?)"
HT_ROUND=t9 drive c9c source
chk "HT_ROUND=t9: exit 0, not $rc" same "$rc" 0
chk "  W/roundt9/bin, recorded" has "$HT_RECORDS/c9c/fake/report.txt" "$BINS $(sha "$FX/roundt9/bin/linux/rustic-bko") $FX/roundt9/bin/linux/rustic-bko (round t9; rustic ?)"
HT_BKO_BIN=$FX/set-wrong drive c9d source
chk "a wrong hash: exit 2, not $rc" same "$rc" 2
chk "  says both" grep -qF "$FX/set-wrong/linux/rustic-bko is $(sha "$FX/set-wrong/linux/rustic-bko"); $FX/set-wrong/SHA256SUMS says $(printf z | sha256sum | cut -d' ' -f1)" "$out"
chk "  nothing left" gone "$HT_WORK/c9d" "$HT_RECORDS/c9d"
HT_BKO_BIN=$FX/set-none drive c9e source
chk "no entry: exit 2, not $rc" same "$rc" 2
chk "  says so" grep -qF "no linux/rustic-bko in $FX/set-none/SHA256SUMS" "$out"
chk "  nothing left" gone "$HT_WORK/c9e" "$HT_RECORDS/c9e"
HT_RUSTIC=$FX/loose/rustic-bko drive c9f source
chk "HT_RUSTIC: exit 0, not $rc" same "$rc" 0
chk "  recorded as no set" has "$HT_RECORDS/c9f/fake/report.txt" "$BINS $(sha "$FX/loose/rustic-bko") $FX/loose/rustic-bko (HT_RUSTIC, no set; rustic ?)"
HT_RUSTIC=$FX/no-such-file drive c9g source
chk "HT_RUSTIC without its file: exit 2, not $rc" same "$rc" 2
chk "  nothing left" gone "$HT_WORK/c9g" "$HT_RECORDS/c9g"
chk "no release for a refusal" eval '! grep -qx -e c9d -e c9e -e c9g "$FX/released"'
verdict 9 "ht_rustic_take: the set's forms, HT_ROUND, HT_RUSTIC, and its refusals" "$out"

out=$FX/out-c10 r=$HT_WORK/c10/fake k=$HT_RECORDS/c10/fake
T1_SLEEP=rebuild "$0" run c10 >"$out" 2>&1 &
d=$!; KIDS+=("$d")
waitfor 60 grep -qx '== rebuild (fake)' "$r/report.txt"
pidf=$(cat "$r/.ht/pid" 2>/dev/null) ph=$(pgrep -P "$d" -x sleep)
kill -TERM "$d"; sleep 1
# shellcheck disable=SC2086  # PIDs
[[ -z $ph ]] || kill -TERM $ph   # its foreground command, as the queue's abort kills QEMU
wait "$d"; rc=$?
chk "exit 143, not $rc" same "$rc" 143
chk ".ht/pid was the driver's" same "$pidf" "$d"
chk "the report ends with the RESULT line, not '$(tail -n 1 "$k/report.txt")'" same "$(tail -n 1 "$k/report.txt")" "RESULT: FAIL (1; ended by SIGTERM before its RESULT, exit 143)"
chk "a RAM disk line before it" hasre "$k/report.txt" '^RAM disk: peak '
chk "kept.txt" hasre "$k/logs/kept.txt" '^fail: everything kept \(exit 143, SIGTERM\), '
chk "K holds everything" there "$k" src-0.qcow2 share/repo/pack src-meta.json rescuer.qcow2 dst-0.qcow2 decoy.qcow2 logs/phases.tsv
chk "R is gone" gone "$HT_WORK/c10"
chk "peaks row: FAIL, rc 143" same "$(pk c10 result) $(pk c10 rc)" "FAIL 143"
verdict 10 "TERM in a sleeping phase: RESULT: FAIL (ended by SIGTERM), everything kept, exit 143" "$out"

n0=$(nrows)
HT_RAM=1 HT_CLEAN=0 HT_RESERVE_PID=$ROW drive c11 source
r=$HT_WORK/c11/fake
chk "exit 0, not $rc" same "$rc" 0
chk "R stays" there "$r" report.txt src-0.qcow2 share/layout.json logs/manifest.txt rustic-bko
chk "its .ht holds only 'held', the reservation's pid" same "$(ls_of "$r/.ht")$(cat "$r/.ht/held" 2>/dev/null)" "held $ROW"
chk "K never made" gone "$HT_RECORDS/c11"
chk "no peaks row" same "$(nrows)" "$n0"
chk "manifest: reserved, pid" hasre "$r/logs/manifest.txt" " reserved $RES \(pid $ROW\)\$"
chk "no release" eval '! grep -qx c11 "$FX/released"'
chk "the run lock is free" unlocked c11
verdict 11 "HT_CLEAN=0 with HT_RESERVE_PID: R stays as it is, nothing in K or peaks.tsv" "$out"
# (c11's R stays for case 14's sweep, which must leave it.)

T1_BIG=backup drive c12 source backup
k=$HT_RECORDS/c12/fake
chk "exit 0, not $rc" same "$rc" 0
chk "backup's peak_disk_kib >= 65536" awk -F'\t' '$1 == "backup" && $4 >= 65536 { f = 1 } END { exit !f }' "$k/logs/phases.tsv"
chk "its peak_anon_kib is measured (the reservation's scope)" awk -F'\t' '$1 == "backup" && $5 ~ /^[0-9]+$/ && $5 > 0 { f = 1 } END { exit !f }' "$k/logs/phases.tsv"
chk "peaks row: peak_disk_kib >= 65536" awk -v p="$(pk c12 peak_disk_kib)" 'BEGIN { exit !(p >= 65536) }'
verdict 12 "the sampler sees 64 MiB written and deleted within a phase" "$out" "$k/logs/phases.tsv"

HT_LIMIT_KIB=32768 T1_BIG=backup T1_BIGWAIT=120 drive c13
k=$HT_RECORDS/c13/fake
chk "exit 1, not $rc" same "$rc" 1
chk "the phase's foreground writer was stopped too (wall $(pk c13 wall_s) s, not 120+)" awk -v w="$(pk c13 wall_s)" 'BEGIN { exit !(w ~ /^[0-9]+$/ && w < 60) }'
chk "the harness's RESULT line last, not '$(tail -n 1 "$k/report.txt")'" eval 'tail -n 1 "$k/report.txt" | grep -qE "^RESULT: FAIL \(1; harness: over its reservation: [0-9.]+ GiB, past the limit of 0\.0 GiB \($RES GiB reserved\); stopped before its RESULT, exit 143\)\$"'
chk "records only, not $(ls_of "$k")" same "$(ls_of "$k")" "dumps logs report.txt src-meta.json.zst "
chk "kept.txt: overrun" hasre "$k/logs/kept.txt" '^overrun: records only, [0-9TZ:-]+; harness: over its reservation: .*; deleted share .*; zstd src-meta.json$'
chk "it stopped in backup" lacks "$k/report.txt" "== rebuild (fake)"
chk "peaks row: FAIL" same "$(pk c13 result)" FAIL
chk "R is gone" gone "$HT_WORK/c13"
drive c13 verify
chk "a verify rerun on its records: exit 2, not $rc" same "$rc" 2
verdict 13 "over its reservation (HT_LIMIT_KIB): stopped, RESULT: FAIL harness, records only" "$FX/out-c13.$((nrun - 1))" "$out"

out=$FX/out-c14a
T1_SLEEP=rebuild "$0" run c14a >"$FX/out-c14a" 2>&1 &
a=$!; KIDS+=("$a")
T1_SLEEP=rebuild "$0" run c14b >"$FX/out-c14b" 2>&1 &
b=$!; KIDS+=("$b")
waitfor 60 grep -qx '== rebuild (fake)' "$HT_WORK/c14a/fake/report.txt"
waitfor 60 grep -qx '== rebuild (fake)' "$HT_WORK/c14b/fake/report.txt"
bk=$(pgrep -P "$b" | tr '\n' ' ') bs=$(pgrep -P "$b" -x sleep)
kill -9 "$b"; wait "$b" 2>/dev/null
# shellcheck disable=SC2086  # PIDs
[[ -z $bs ]] || kill -9 $bs   # its phase's sleep; its sampler and lock watcher go by themselves
mkdir -p "$HT_WORK/debug/fake/logs" "$HT_WORK/debug-state/fake"
chk "the dead run's lock frees itself" waitfor 10 unlocked c14b
"$HERE/ht1-lib.sh" --sweep "$HT_WORK" >"$FX/out-sweep" 2>&1; src=$?
k=$HT_RECORDS/c14b/fake
chk "the sweep: exit 0, not $src" same "$src" 0
chk "it says what it moved" has "$FX/out-sweep" "ht1-lib.sh: swept $HT_WORK/c14b/fake into $k"
chk "and only that" same "$(grep -c swept "$FX/out-sweep")" 1
chk "the dead run's R is gone" gone "$HT_WORK/c14b"
chk "its records are in K, not $(ls_of "$k")" same "$(ls_of "$k")" "dumps logs report.txt "
chk "kept.txt: swept" hasre "$k/logs/kept.txt" "^swept: records only \(the run died; pid $b\), "
chk "the live run is untouched" there "$HT_WORK/c14a/fake" src-0.qcow2 share/repo/pack .ht/pid
chk "and runs on" kill -0 "$a"
chk "debug/ and debug-state/ stay" there "$HT_WORK" debug/fake/logs debug-state/fake
chk "c11's R stays (the reservation in its .ht/held lives)" there "$HT_WORK/c11/fake" report.txt .ht/held
# shellcheck disable=SC2086  # PIDs
chk "the dead run's sampler and lock watcher ended by themselves" waitfor 10 eval '! kill -0 '"${bk:-0}"' 2>/dev/null'
as=$(pgrep -P "$a" -x sleep); kill -TERM "$a"; sleep 1
# shellcheck disable=SC2086
[[ -z $as ]] || kill -TERM $as
wait "$a"; rc=$?
chk "the live run then ends as a TERM does, not $rc" same "$rc" 143
rm -rf "$HT_WORK/debug" "$HT_WORK/debug-state" "$HT_WORK/c11"
verdict 14 "--sweep moves a SIGKILLed run's records and empties R; live, held and debug runs stay" "$FX/out-sweep" "$FX/out-c14b"

out=$FX/out-c15
T1_HOLD=3 T1_KEEPBIG=source HT_FAIL_AT=source "$0" run c15 source >"$out" 2>&1 &
d=$!; KIDS+=("$d")
waitfor 60 test -e "$FX/hold-c15"
# shellcheck disable=SC2046  # PIDs
kill -TERM "$d" $(desc "$d") 2>/dev/null   # the driver and all it runs, as an abort does: in the release
sleep 0.5
chk "the run lock is still held" eval '! unlocked c15'
seen=""
for ((i = 0; i < 3000; i++)); do   # then again while rsync copies R to K
    for c in $(desc "$d"); do [[ $(cat "/proc/$c/comm" 2>/dev/null) == rsync ]] && seen=1; done
    [[ -z $seen ]] && kill -0 "$d" 2>/dev/null || break
    sleep 0.01
done
# shellcheck disable=SC2046
[[ -z $seen ]] || kill -TERM "$d" $(desc "$d") 2>/dev/null
wait "$d"; rc=$?
k=$HT_RECORDS/c15/fake
chk "a TERM reached the copy (rsync seen)" test -n "$seen"
chk "exit 1 (its FAIL), not $rc" same "$rc" 1
chk "the copy completed: big" same "$(sha "$k/big" 2>/dev/null)" "$(cut -d' ' -f1 "$k/logs/big.sha256" 2>/dev/null)"
n15=$(find "$k/many" -type f 2>/dev/null | wc -l) x15=$(find "$k/many" -type f ! -name 'f[0-9]*' 2>/dev/null | head -n 3 | tr '\n' ' ')
chk "the copy completed: 8000 files, not $n15${x15:+ (also $x15)}" same "$n15" 8000
chk "kept.txt: fail, not lost" hasre "$k/logs/kept.txt" '^fail: everything kept \(exit 1\), '
chk "no copy failure said" eval '! grep -q "couldn.t copy" "$out"'
chk "R is gone" gone "$HT_WORK/c15"
how="rsync took the TERM, retried"; grep -q "rsync exit .* (try 1 of 3)" "$out" || how="rsync ignored the TERM"
verdict 15 "TERMs to the driver's tree during ht_end's release and copy: the move completes ($how)" "$out"

chk "the header" same "$(head -n 1 "$HT_RECORDS/peaks.tsv")" "$(printf 'end_utc\thost\tdriver\trun\tgv\tphases\tseeded\trc\tresult\tmode\treserved_gib\tpeak_disk_kib\tpeak_anon_kib\twall_s')"
chk "c3's run from source: seeded 0" same "$(pk c3 seeded) $(pk c3 phases)" "0 source,backup,rebuild,verify"
chk "c4's seeded verify: seeded 1" same "$(pk c4 seeded) $(pk c4 phases)" "1 verify"
verdict 16 "peaks.tsv's seeded column"

python3 -c 'import socket, time
s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1); print(s.getsockname()[1], flush=True); time.sleep(120)' >"$FX/port" &
lp=$!; KIDS+=("$lp")
waitfor 10 test -s "$FX/port"; port=$(cat "$FX/port")
T1_PORT=$port drive c17 source
chk "exit 2, not $rc" same "$rc" 2
chk "says why" grep -qF "port $port in use (a listener" "$out"
chk "nothing made" gone "$HT_WORK/c17" "$HT_RECORDS/c17"
kill "$lp"; wait "$lp" 2>/dev/null
verdict 17 "a port with a listener is refused, nothing made" "$out"

lf=$(lockfile c18)
(exec {fd}>>"$lf"; flock -n "$fd" || exit 1; : >"$FX/locked"; while [[ ! -e $FX/unlock ]]; do sleep 0.2 {fd}>&-; done) &
lh=$!; KIDS+=("$lh")
waitfor 10 test -e "$FX/locked"
drive c18 source
chk "exit 2, not $rc" same "$rc" 2
chk "says why" grep -qF "c18/fake is in use (another run holds $lf)" "$out"
chk "nothing made" gone "$HT_WORK/c18" "$HT_RECORDS/c18"
: >"$FX/unlock"; wait "$lh"
drive c18 source
chk "once free, the run goes: exit 0, not $rc" same "$rc" 0
verdict 18 "a run whose RUN/GV lock is held is refused, nothing made" "$FX/out-c18.$((nrun - 1))"

HT_RAM=2 drive c19
chk "exit 2, not $rc" same "$rc" 2
chk "says why" grep -qF "HT_RAM=2: 1 (in RAM), 0 (on /bkoscratch) or unset" "$out"
chk "nothing made" gone "$HT_WORK/c19" "$HT_RECORDS/c19"
verdict 19 "HT_RAM=2 is refused" "$out"

HT_RESERVE_PID=$DEAD drive c20
k=$HT_RECORDS/c20/fake
chk "exit 0, not $rc" same "$rc" 0
chk "nothing in the RAM disk" gone "$HT_WORK/c20"
chk "the note" has "$k/report.txt" "note: no reservation of the RAM disk (cmp-run.sh --reserve D M): working on /bkoscratch, as HT_RAM=0"
chk "the disk line" hasre "$k/report.txt" "^disk \($HT_RECORDS, HT_RAM=0\): peak [0-9]+\.[0-9] GiB; memory: peak [0-9]+\.[0-9] GiB anon\$"
chk "the kept set in place, not $(ls_of "$k")" same "$(ls_of "$k")" "$kept_ls"
chk "manifest: mode disk" has "$k/logs/manifest.txt" "driver $HERE/ht1-lib-test.sh mode disk work $k records $k reserved 0"
chk "TMPDIR was K/tmp" has "$k/logs/source-tmpdir.txt" "$k/tmp"
chk "peaks row: disk" same "$(pk c20 mode)" disk
cp "$k/report.txt" "$FX/c20-report"
HT_RAM=0 drive c20 rebuild
chk "a rebuild rerun on its pass: exit 2, not $rc" same "$rc" 2
HT_RAM=0 HT_BKO_BIN=$FX/set-wrong drive c20 source
chk "a refused rerun from source: exit 2, not $rc" same "$rc" 2
chk "  K is put back, no K.1" eval 'cmp -s "$FX/c20-report" "$k/report.txt" && gone "$k.1"'
HT_RAM=0 drive c20 source
chk "a rerun from source: exit 0, not $rc" same "$rc" 0
chk "  the earlier K is K.1" cmp -s "$FX/c20-report" "$k.1/report.txt"
chk "  K is the new run's, everything kept (no verify)" eval 'there "$k" src-0.qcow2 share/layout.json && hasre "$k/logs/kept.txt" "^fail: everything kept \(exit 0, no verify\), "'
verdict 20 "no reservation: on /bkoscratch with a note; disk mode's refusals and K.N" "$out"

r=$HT_WORK/c21/fake
mkdir -p "$r/.ht"; echo $$ >"$r/.ht/pid"
drive c21 source
chk "exit 2, not $rc" same "$rc" 2
chk "says why" grep -qF "$r is in use (pid $$)" "$out"
chk "R as it was" same "$(ls_of "$r")$(cat "$r/.ht/pid")" ".ht $$"
chk "no K" gone "$HT_RECORDS/c21"
rm -rf "$HT_WORK/c21"
verdict 21 "an R whose .ht/pid is a live ht1 process is refused" "$out"

HT_CLEAN=0 drive c22 source
rc1=$rc o1=$out
HT_KEEP=1 drive c22 source
chk "HT_CLEAN=0: exit 2, not $rc1" same "$rc1" 2
chk "HT_KEEP=1: exit 2, not $rc" same "$rc" 2
chk "says why" grep -qF "only with HT_RESERVE_PID" "$o1"
chk "nothing made" gone "$HT_WORK/c22" "$HT_RECORDS/c22"
verdict 22 "HT_CLEAN=0 and HT_KEEP=1 in RAM without HT_RESERVE_PID are refused" "$o1" "$out"

T1_UNBOUND=backup drive c23
k=$HT_RECORDS/c23/fake
chk "exit 1, not $rc" same "$rc" 1
chk "the RESULT line last, not '$(tail -n 1 "$k/report.txt")'" same "$(tail -n 1 "$k/report.txt")" "RESULT: FAIL (1; ended before its RESULT, exit 1)"
chk "kept.txt" hasre "$k/logs/kept.txt" '^fail: everything kept \(exit 1\), '
chk "K holds the state so far" there "$k" src-0.qcow2 share/repo/pack src-meta.json
chk "R is gone" gone "$HT_WORK/c23"
verdict 23 "an unbound variable ends the run: RESULT: FAIL (ended before its RESULT), everything kept" "$out"

T1_LEAK=1 drive c24 source backup rebuild
k=$HT_RECORDS/c24/fake
chk "exit 1, not $rc" same "$rc" 1
chk "the phase made its loop device, mount and holder" eval 'grep -qx "/dev/loop[0-9]*" "$k/logs/leak-loop.txt" &&
    has "$k/logs/leak-mount.txt" "$HT_WORK/c24/fake/mnt-leak" && grep -qx "[0-9]*" "$k/logs/leak-holder.txt"'
chk "the mount was undone before the copy (its file stayed in it)" gone "$k/mnt-leak/x"
chk "the holder is gone" eval '! kill -0 "$(cat "$k/logs/leak-holder.txt")"'
chk "no loop device on the test's files" test -z "$(losetup -l -n -O NAME,BACK-FILE 2>/dev/null | awk -v a="$HT_WORK/" -v b="$HT_RECORDS/" 'index($2, a) == 1 || index($2, b) == 1')"
chk "nothing mounted in R or K" test -z "$(findmnt -rn -o TARGET | awk -v a="$HT_WORK/" -v b="$HT_RECORDS/" 'index($0, a) == 1 || index($0, b) == 1')"
chk "the process holding R is gone" eval '! pgrep -f "^sleep 7777\.31415\$"'
chk "K holds everything" there "$k" tmp-leak.raw mnt-leak logs/leak-loop.txt src-0.qcow2 share/repo/pack
chk "R is gone" gone "$HT_WORK/c24"
chk "nothing still held" eval '! grep -q "is still held" "$out"'
verdict 24 "the release: a loop device, a mount and a process holding R are undone before the move" "$out"

lim() { (. "$HERE/ht1-lib.sh"; ht_limit_kib "$1"); }
chk "1 GiB reserved: 2 GiB, not $(lim 1)" same "$(lim 1)" $((2 << 20))
chk "2.5: 3.5 GiB, not $(lim 2.5)" same "$(lim 2.5)" $((7 << 19))
chk "15: 16.5 GiB, not $(lim 15)" same "$(lim 15)" $((33 << 19))
chk "20: 22 GiB, not $(lim 20)" same "$(lim 20)" $((22 << 20))
verdict 25 "the limit: RESERVED + max(1 GiB, 10 %)"

T1_RSYNC_FAIL=1 HT_FAIL_AT=backup drive c26 source backup
k=$HT_RECORDS/c26/fake
chk "exit 1, not $rc" same "$rc" 1
chk "the records (and the fsmeta record) only, not $(ls_of "$k")" same "$(ls_of "$k")" "dumps logs report.txt src-meta.json "
chk "kept.txt says what was lost" hasre "$k/logs/kept.txt" '^lost: records only, the copy of the rest to K failed \([0-9TZ:-]+\); it was fail: everything kept \(exit 1\), '
chk "and stderr" grep -qF "couldn't copy $HT_WORK/c26/fake to $k: keeping its records only" "$out"
chk "R is gone anyway" gone "$HT_WORK/c26"
drive c26 verify
chk "a verify rerun on them: exit 2, not $rc" same "$rc" 2
cp "$k/logs/kept.txt" "$FX/c26-kept"
T1_RSYNC_FAIL=all HT_FAIL_AT=backup drive c26 source backup
chk "both copies failing: exit 1, not $rc" same "$rc" 1
chk "  says so" grep -qF "couldn't copy $HT_WORK/c26/fake's records to $k either: they are lost" "$out"
chk "  K as it was, no K.1 or K.part" eval 'cmp -s "$FX/c26-kept" "$k/logs/kept.txt" && gone "$k.1" "$k.part"'
chk "  R is gone anyway" gone "$HT_WORK/c26"
verdict 26 "a failed move keeps the records in K and still empties R; a failed records copy leaves K as it was" "$FX/out-c26.$((nrun - 2))" "$out"

HT_FAIL_AT=rebuild drive c27
HT_FAIL_AT=verify drive c27 verify
k=$HT_RECORDS/c27/fake
chk "the seeded rerun: exit 1, not $rc" same "$rc" 1
chk "its unchanged files are hard links to K.1's" eval 'for f in dst-0.qcow2 share/layout.json src-meta.json; do same "$(stat -c %i "$k/$f")" "$(stat -c %i "$k.1/$f")" || exit 1; done'
chk "what it wrote is its own" eval '! same "$(stat -c %i "$k/report.txt")" "$(stat -c %i "$k.1/report.txt")"'
verdict 27 "a seeded failure moves with --link-dest: unchanged files become hard links" "$out"

# A dead run's R, as the lib leaves one: DIR [MARKER...] (.ht/pid dead, .ht/k).
deadr() { local d=$1 m; shift; mkdir -p "$d/.ht" "$d/logs" "$d/dumps"; echo "$DEAD" >"$d/.ht/pid"; for m; do : >"$d/.ht/$m"; done; }
# The tests' fake QEMU: a process ht_qemus and the port check take for one of R's on PORT.
fakeq() {  # R PORT
    bash -c 'exec -a "qemu-system-x86_64 -drive file=$1/fake.qcow2 -nic user,hostfwd=tcp::$2-:22" sleep 300' _ "$1" "$2" &
    FQ=$!; KIDS+=("$FQ")
}

r=$HT_WORK/c28a/fake; mkdir -p "$r"; echo theirs >"$r/report.txt"
(cd "$r" && exec sleep 300) & hold=$!; KIDS+=("$hold")
r=$HT_WORK/c28b/fake k=$HT_RECORDS/c28b/fake
deadr "$r" moved; echo "$k" >"$r/.ht/k"; mkdir -p "$k/logs"; echo "fail: the moved run" >"$k/logs/kept.txt"
r=$HT_WORK/c28c/fake k=$HT_RECORDS/alt/c28c/fake
deadr "$r"; echo "$k" >"$r/.ht/k"; echo "RESULT: FAIL (1)" >"$r/report.txt"; echo x >"$r/logs/x"; echo big >"$r/src-0.qcow2"
"$HERE/ht1-lib.sh" --sweep "$HT_WORK" >"$FX/out-sweep28" 2>&1; src=$?
chk "the sweep: exit 0, not $src" same "$src" 0
chk "an R without .ht stays" there "$HT_WORK/c28a/fake" report.txt
chk "  and what holds it lives" kill -0 "$hold"
chk "  nothing said of it" eval '! grep -q c28a "$FX/out-sweep28"'
chk "an R already moved is only deleted" eval 'gone "$HT_WORK/c28b" "$HT_RECORDS/c28b/fake.1" && has "$HT_RECORDS/c28b/fake/logs/kept.txt" "fail: the moved run"'
chk "  and said so" has "$FX/out-sweep28" "ht1-lib.sh: deleted $HT_WORK/c28b/fake (its run is in $HT_RECORDS/c28b/fake)"
chk "a run's own K (.ht/k, another HT_RECORDS): its records go there" eval 'same "$(ls_of "$k")" "dumps logs report.txt " && hasre "$k/logs/kept.txt" "^swept: records only \(the run died; pid $DEAD\), " && gone "$HT_RECORDS/c28c"'
"$HERE/ht1-lib.sh" --sweep /mnt/bko-ram >"$FX/out-sweep28b" 2>&1; src=$?
chk "--sweep of another directory: exit 2, not $src" same "$src" 2
chk "  says why" grep -qF "/mnt/bko-ram isn't HT_WORK ($HT_WORK)" "$FX/out-sweep28b"
HT_RECORDS=$HT_RECORDS-other "$HERE/ht1-lib.sh" --sweep >"$FX/out-sweep28c" 2>&1; src=$?
chk "--sweep with another NAME's HT_RECORDS: exit 2, not $src" same "$src" 2
chk "  says why" grep -qF "HT_RECORDS $HT_RECORDS-other: /mnt/bko-ram/NAME and /bkoscratch/NAME, one NAME" "$FX/out-sweep28c"
HT_WORK=$HT_WORK/c28a HT_RECORDS=$HT_RECORDS/c28a "$HERE/ht1-lib.sh" --sweep >>"$FX/out-sweep28c" 2>&1; src=$?
chk "--sweep below a NAME: exit 2, not $src" same "$src" 2
kill "$hold"; wait "$hold" 2>/dev/null; rm -rf "$HT_WORK/c28a"
verdict 28 "--sweep: only R the lib made (R/.ht); a moved R only deleted; .ht/k's K; HT_WORK only, paired with its HT_RECORDS" \
    "$FX/out-sweep28" "$FX/out-sweep28b" "$FX/out-sweep28c"

r=$HT_WORK/c29/fake
deadr "$r"; fakeq "$r" "$port"; waitfor 10 pgrep -f "^qemu-system.*:$port-:22"
T1_PORT=$port drive c29 rebuild
chk "a dead run's QEMU on the port: exit 2, not $rc" same "$rc" 2
chk "  says why" grep -qF "port $port in use (QEMU pid $FQ, not this run's)" "$out"
rm -rf "$r/.ht"; mkdir -p "$r/.ht"; echo "$ROW" >"$r/.ht/held"
T1_PORT=$port drive c29 source
chk "a rescuer left up on purpose, for a run from source: exit 2, not $rc" same "$rc" 2
T1_PORT=$port drive c29 rebuild
chk "  for a rebuild: let through (its fake rebuild fails on its inputs: exit 1), not $rc" eval 'same "$rc" 1 && has "$out" "== rebuild (fake)"'
chk "  and ht_free took it down at the end" dead "$FQ"
chk "  R is gone" gone "$HT_WORK/c29"
verdict 29 "the port: a dead run's QEMU is refused; one left up on purpose (.ht/held) only for rebuild or verify" "$FX/out-c29.$((nrun - 2))" "$out"

k=$HT_RECORDS/c27/fake
l0=$(stat -c %h "$k/share/layout.json" 2>/dev/null); cp "$k.1/share/layout.json" "$FX/c27-layout"
(cd "$k" && exec sleep 300) & hold=$!; KIDS+=("$hold")
T1_INPLACE=1 HT_RAM=0 drive c27 verify
chk "K's share/layout.json was a hard link to K.1's ($l0 links)" test "${l0:-0}" -gt 1
chk "a rerun in K, on /bkoscratch: exit 0, not $rc" same "$rc" 0
chk "K.1's copy is as it was" cmp -s "$FX/c27-layout" "$k.1/share/layout.json"
chk "a shell in K is left alone" kill -0 "$hold"
chk "K is a pass, records only" hasre "$k/logs/kept.txt" '^pass:'
kill "$hold"; wait "$hold" 2>/dev/null
verdict 30 "on /bkoscratch a rerun in K rewrites no file of K.N's, and kills nothing that holds K" "$out"

T1_TERM_AFTER=1 drive c31
k=$HT_RECORDS/c31/fake
chk "exit 0, not $rc" same "$rc" 0
chk "RESULT: PASS stays the last line" same "$(tail -n 1 "$k/report.txt")" "RESULT: PASS"
chk "kept.txt: pass" hasre "$k/logs/kept.txt" '^pass: records only'
chk "peaks row: PASS, rc 143" same "$(pk c31 result) $(pk c31 rc)" "PASS 143"
verdict 31 "a TERM after RESULT: PASS ends a passed run" "$out"

HT_WORK=/mnt/bko-ram/holttest1d HT_LIMIT_KIB=1 drive c32 source
chk "HT_LIMIT_KIB with the default HT_WORK: exit 2, not $rc" same "$rc" 2
chk "  says why" grep -qF "HT_LIMIT_KIB=1: a number of KiB, for tests (with their own HT_WORK) only" "$out"
chk "  nothing made" gone /mnt/bko-ram/holttest1d/c32 "$HT_RECORDS/c32"
verdict 32 "HT_LIMIT_KIB is refused outside a test's HT_WORK" "$out"

HT_FAIL_AT=rebuild drive c33
r=$HT_WORK/c33/fake k=$HT_RECORDS/c33/fake
cutseed() {  # a seed cut short: some of K's state in R
    deadr "$r" seeding; echo "$k" >"$r/.ht/k"; mkdir -p "$r/share"
    cp "$k/dumps/src.json" "$r/dumps/"; cp "$k/share/layout.json" "$r/share/"
}
cutseed
"$HERE/ht1-lib.sh" --sweep "$HT_WORK" >"$FX/out-sweep33" 2>&1; src=$?
chk "the sweep: exit 0, not $src" same "$src" 0
chk "  deletes it" eval 'gone "$HT_WORK/c33" && has "$FX/out-sweep33" "ht1-lib.sh: deleted $r (a seed cut short; $k as it was)"'
chk "  K as it was, no K.1" eval 'hasre "$k/logs/kept.txt" "^fail:" && there "$k" src-0.qcow2 share/repo/pack && gone "$k.1"'
cutseed
drive c33 verify
chk "a verify rerun on the cut seed: exit 0, not $rc" same "$rc" 0
chk "  the seed went on (share/repo-password reached R)" has "$k/logs/verify-saw.txt" share/repo-password
chk "  no .ht in K" gone "$k/.ht"
chk "  K.1 is the failure" hasre "$k.1/logs/kept.txt" '^fail:'
verdict 33 "a seed cut short (R/.ht/seeding): the sweep deletes it, a rerun goes on with it" "$FX/out-sweep33" "$out"

T1_PORT=abc drive c34 source
r1=$rc o1=$out
T1_PORT=1023 drive c34 source
r2=$rc
T1_PORT=65536 drive c34 source
r3=$rc
drive debug-state source
r4=$rc o4=$out
drive debug source
r5=$rc o5=$out
chk "HT_PORT=abc: exit 2, not $r1" same "$r1" 2
chk "  says why" grep -qF "HT_PORT=abc: a port, 1024-65535" "$o1"
chk "1023 and 65536: exit 2, not $r2 and $r3" same "$r2 $r3" "2 2"
chk "RUN debug-state: exit 2, not $r4" same "$r4" 2
chk "  says why" grep -qF "RUN debug-state is ht1-debug.sh's own state" "$o4"
chk "RUN debug without HT_CLEAN=0 and HT_RESERVE_PID: exit 2, not $r5" same "$r5" 2
chk "  says why" grep -qF "RUN debug is ht1-debug.sh's (with HT_CLEAN=0 and HT_RESERVE_PID)" "$o5"
chk "nothing made" gone "$HT_WORK/c34" "$HT_RECORDS/c34" "$HT_WORK/debug" "$HT_WORK/debug-state" "$HT_RECORDS/debug" "$HT_RECORDS/debug-state"
HT_RAM=1 HT_CLEAN=0 HT_RESERVE_PID=$ROW drive debug source
chk "RUN debug as ht1-debug.sh runs it: exit 0, not $rc" eval 'same "$rc" 0 && there "$HT_WORK/debug/fake" report.txt .ht/held'
rm -rf "$HT_WORK/debug"
verdict 34 "ht_paths refuses an HT_PORT outside 1024-65535 and ht1-debug.sh's RUNs, nothing made" "$o1" "$o4" "$o5" "$out"

out=$FX/out-c35 r=$HT_WORK/c35/fake k=$HT_RECORDS/c35/fake
T1_REDIR=rebuild "$0" run c35 >"$out" 2>&1 &
d=$!; KIDS+=("$d")
waitfor 60 grep -qx '== rebuild (fake)' "$r/report.txt"
waitfor 10 pgrep -P "$d" -x sleep
ph=$(pgrep -P "$d" -x sleep) fd1=""
[[ -z $ph ]] || fd1=$(readlink "/proc/$ph/fd/1")
kill -TERM "$d"; sleep 1
# shellcheck disable=SC2086  # PIDs
[[ -z $ph ]] || kill -TERM $ph
t0=$SECONDS; wait "$d"; rc=$? t=$((SECONDS - t0))
chk "the phase's sleep had its stdout in R, not '$fd1'" same "$fd1" "$r/logs/redir.out"
chk "exit 143, not $rc" same "$rc" 143
chk "the caller's log has the RESULT line" has "$out" "RESULT: FAIL (1; ended by SIGTERM before its RESULT, exit 143)"
chk "and says where the run went" has "$out" "ht1-lib-test.sh: the run is in $k"
chk "the call's files in R got nothing of ht_end's (stderr: bash's own Terminated for its sleep)" \
    eval '[[ -f $k/logs/redir.out && ! -s $k/logs/redir.out ]] && ! grep -qvx Terminated "$k/logs/redir.err"'
chk "nothing held R" eval '! grep -q "is still held" "$out"'
chk "it ended within 20 s of the TERM, not $t s" test "$t" -lt 20
chk "R is gone" gone "$HT_WORK/c35"
verdict 35 "a TERM inside a function call whose stdio is in R: ht_end writes to the driver's own" "$out" "$k/logs/redir.out" "$k/logs/redir.err"

sleep 300 & holder=$!; KIDS+=("$holder")
echo "$holder 1 1 - reserve ht1-lib-test's own" >"$FX/ledger"
out=$FX/out-c36 r=$HT_WORK/c36/fake k=$HT_RECORDS/c36/fake
T1_LEDGER=$FX/ledger HT_RAM=1 HT_RESERVE_PID=$holder T1_SLEEP=rebuild "$0" run c36 >"$out" 2>&1 &
d=$!; KIDS+=("$d")
waitfor 60 grep -qx '== rebuild (fake)' "$r/report.txt"
kill "$holder"; wait "$holder" 2>/dev/null
t0=$SECONDS; wait "$d"; rc=$? t=$((SECONDS - t0))
chk "exit 1 (the harness's stop), not $rc" same "$rc" 1
chk "stopped within 30 s of the end of its reservation, not $t s" test "$t" -lt 30
chk "the harness's RESULT line last, not '$(tail -n 1 "$k/report.txt")'" same "$(tail -n 1 "$k/report.txt")" \
    "RESULT: FAIL (1; harness: its reservation ended (no live ledger row, pid $holder); stopped before its RESULT, exit 143)"
chk "records only, not $(ls_of "$k")" same "$(ls_of "$k")" "dumps logs report.txt src-meta.json.zst "
chk "kept.txt says why" hasre "$k/logs/kept.txt" "^overrun: records only, [0-9TZ:-]+; harness: its reservation ended \(no live ledger row, pid $holder\); deleted "
chk "the manifest: the reservation it had" hasre "$k/logs/manifest.txt" " reserved 1 \(pid $holder\)\$"
chk "R is gone" gone "$HT_WORK/c36"
verdict 36 "a run whose reservation ended (no live ledger row) is stopped: RESULT: FAIL harness, records only" "$out"

r=$HT_WORK/c37/fake k=$HT_RECORDS/c37/fake
deadr "$r" summed; echo "$k" >"$r/.ht/k"; echo "over its reservation: a dead run's" >"$r/.ht/overrun"
echo "the dead attempt" >"$r/report.txt"; echo x >"$r/logs/dead.log"; echo big >"$r/src-0.qcow2"
drive c37 source
chk "exit 0, not $rc" same "$rc" 0
chk "the caller's log says it swept the dead run's R first" has "$out" "ht1-lib-test.sh: swept $r into $k"
chk "  and so does the report's note: line" eval 'grep "^note: " "$k/report.txt" | grep -qF "a run that died had left $r: swept first (its records to K)"'
chk "K.1 is the dead attempt's records, swept, not $(ls_of "$k.1")" eval 'same "$(ls_of "$k.1")" "dumps logs report.txt " &&
    has "$k.1/report.txt" "the dead attempt" && has "$k.1/logs/dead.log" x && hasre "$k.1/logs/kept.txt" "^swept: records only \(the run died; pid $DEAD\), "'
chk "K is the new run's alone" eval 'lacks "$k/report.txt" "the dead attempt" && gone "$k/logs/dead.log" && same "$(tail -n 1 "$k/report.txt")" "RESULT: PASS"'
chk "  with none of the dead run's markers (kept.txt: everything kept, no verify; peaks row PASS)" eval \
    'hasre "$k/logs/kept.txt" "^fail: everything kept \(exit 0, no verify\), " && same "$(pk c37 result)" PASS'
chk "R is gone" gone "$HT_WORK/c37"
verdict 37 "a run from source on a dead run's R sweeps it first: its records are K.1, and K is the new run's alone" "$out"

HT_FAIL_AT=rebuild drive c38
r=$HT_WORK/c38/fake k=$HT_RECORDS/c38/fake
mkdir -p "$r"; cp -a "$k/." "$r/"   # a dead run's R with the failure's state: it died before its move
deadr "$r" summed moved; echo "$k" >"$r/.ht/k"
HT_LIMIT_KIB=32768 T1_BIG=verify T1_BIGWAIT=120 drive c38 verify
chk "without the dead run's .ht/summed the sampler stops it: exit 1, not $rc" same "$rc" 1
chk "  the harness's RESULT line last, not '$(tail -n 1 "$k/report.txt")'" eval \
    'tail -n 1 "$k/report.txt" | grep -qE "^RESULT: FAIL \(1; harness: over its reservation: "'
chk "  R is gone" gone "$HT_WORK/c38"
HT_FAIL_AT=rebuild drive c38b
r=$HT_WORK/c38b/fake k=$HT_RECORDS/c38b/fake
mkdir -p "$r"; cp -a "$k/." "$r/"
deadr "$r"; echo "$k" >"$r/.ht/k"; echo "over its reservation: a dead run's" >"$r/.ht/overrun"
drive c38b verify
chk "without the dead run's .ht/overrun a pass: exit 0, not $rc" same "$rc" 0
chk "  RESULT: PASS last, and no harness line" eval 'same "$(tail -n 1 "$k/report.txt")" "RESULT: PASS" && ! grep -q harness "$k/report.txt"'
chk "  kept.txt: pass; peaks row PASS" eval 'hasre "$k/logs/kept.txt" "^pass: records only" && same "$(pk c38b result)" PASS'
chk "  R is gone" gone "$HT_WORK/c38b"
verdict 38 "a later phase on a dead run's R goes on with its state, without its markers (summed, overrun, moved)" "$FX/out-c38.$((nrun - 2))" "$out"

out=$FX/out-c39 r=$HT_WORK/c39/fake k=$HT_RECORDS/c39/fake
HT_LIMIT_KIB=32768 T1_MOUNTING=backup T1_HOLD=3 "$0" run c39 source backup >"$out" 2>&1 &
d=$!; KIDS+=("$d")
waitfor 90 test -e "$FX/hold-c39"
hp=$(cat "$r/logs/holder.pid" 2>/dev/null)
chk "what holds R from outside the driver's tree lived through the stop (pid ${hp:-none})" eval '[[ -n $hp ]] && kill -0 "$hp"'
wait "$d"; rc=$?
chk "exit 1, not $rc" same "$rc" 1
chk "the harness's RESULT line last, not '$(tail -n 1 "$k/report.txt")'" eval \
    'tail -n 1 "$k/report.txt" | grep -qE "^RESULT: FAIL \(1; harness: over its reservation: "'
chk "the vm-mount at work had no TERM: it finished" test -e "$k/logs/mount-done"
chk "ht_end's release then ended the holder" eval '[[ -n $hp ]] && ! kill -0 "$hp" 2>/dev/null'
chk "records only, not $(ls_of "$k")" same "$(ls_of "$k")" "dumps logs report.txt src-meta.json.zst "
chk "R is gone" gone "$HT_WORK/c39"
verdict 39 "the overrun stop: a vm-mount at work finishes, and only ht_end's release takes what else holds R" "$out"

r=$HT_WORK/c40/fake k=$HT_RECORDS/c40/fake
deadr "$r"; echo "$k" >"$r/.ht/k"; echo "the dead attempt" >"$r/report.txt"
HT_BKO_BIN=$FX/set-wrong drive c40 verify
chk "a refused rerun on a dead run's R: exit 2, not $rc" same "$rc" 2
chk "  R stays with its .ht, a dead run's again, not $(ls_of "$r/.ht")" there "$r" report.txt .ht/pid .ht/k
"$HERE/ht1-lib.sh" --sweep "$HT_WORK" >"$FX/out-sweep40" 2>&1; src=$?
chk "the sweep: exit 0, not $src" same "$src" 0
chk "  takes it: its records in K" eval 'gone "$HT_WORK/c40" && has "$k/report.txt" "the dead attempt" && hasre "$k/logs/kept.txt" "^swept: records only "'
verdict 40 "a refusal after ht_begin on a dead run's R leaves it, .ht and all, for the sweep" "$out" "$FX/out-sweep40"

HT_RAM=0 T1_OVERLAY=1 drive c41 source backup rebuild
k=$HT_RECORDS/c41/fake
HT_RAM=0 HT_BKO_BIN=$FX/set-wrong drive c41 source
chk "a refused rerun from source on /bkoscratch: exit 2, not $rc" same "$rc" 2
chk "  K is back, no K.1" eval 'there "$k" rescuer-try.qcow2 check-x/sculpt-check.qcow2 src-link && gone "$k.1"'
chk "  its overlay backs onto K's rescuer.qcow2 again, not $(backing "$k/rescuer-try.qcow2")" same "$(backing "$k/rescuer-try.qcow2")" "$k/rescuer.qcow2"
chk "  its symlinks point into K again" eval 'same "$(readlink "$k/check-x/sculpt-check.qcow2")" "$k/dst-0.qcow2" && same "$(readlink "$k/src-link")" "$k/src-0.qcow2"'
verdict 41 "on /bkoscratch a refused rerun from source puts K back, its overlays and symlinks into K again" "$out"

# A fake QEMU started by hand, as by someone looking at a run's disks: the
# fake runs' copy of sleep named qemu-system-x86_64, FILE's disk, a hostfwd
# on PORT, TMPDIR=TMP in its environment (none when TMP is empty), stdin
# STDIN.  Sets OQ (one of KIDS).
outq() {  # FILE PORT TMP [STDIN]
    (if [[ -n $3 ]]; then export TMPDIR=$3; else unset TMPDIR; fi
     exec -a "qemu-system-x86_64 -drive file=$1,format=qcow2,if=virtio -nic user,ipv6=off,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$2-:22" \
         "$FX/qbin/qemu-system-x86_64" 600) <"${4:-/dev/null}" >/dev/null 2>&1 &
    OQ=$!; KIDS+=("$OQ")
    waitfor 5 grep -q '^qemu-system' "/proc/$OQ/cmdline"
}
qp=$((T1_PORT0 + 100))   # the port of the VMs started by hand, and +1 the fake runs' own (nothing listens on either)

k=$HT_RECORDS/c42/fake
HT_RAM=0 drive c42 source backup rebuild; r0=$rc
outq "$k/dst-0.qcow2" "$qp" "$k/tmp/by-hand"; dq=$OQ   # a look at that attempt's restored disk (a TMPDIR below K/tmp, not it)
HT_RAM=0 T1_OWNQ=source T1_QPORT=$((qp + 1)) drive c42 source; o1=$out
chk "the attempt looked at: exit 0, not $r0" same "$r0" 0
chk "a run from source on /bkoscratch: exit 0, not $rc" same "$rc" 0
chk "  the attempt it renamed is K.1" eval 'there "$k.1" dst-0.qcow2 && hasre "$k.1/logs/kept.txt" "^fail: everything kept"'
chk "  ht_qemus took the run's own VM only ($(cat "$k/logs/ownq1.pid" 2>/dev/null)), not '$(tr '\n' ' ' <"$k/logs/qemus.txt" 2>/dev/null)'" \
    eval '[[ -s $k/logs/ownq1.pid ]] && same "$(cat "$k/logs/qemus.txt")" "$(cat "$k/logs/ownq1.pid")"'
chk "  vm_down took it down" has "$k/logs/ownq1.txt" down
chk "  ht_free took down the one it left up" eval 'p=$(cat "$k/logs/ownq2.pid") && [[ $p =~ ^[0-9]+$ ]] && dead "$p"'
chk "  the VM started by hand on K's dst-0.qcow2 lives" eval '! dead "$dq"'
truncate -s 1M "$k/look.raw"; ll=$(losetup -f --show "$k/look.raw")
mkdir -p "$k/look"; mount -t tmpfs -o size=1m t1look "$k/look"
(cd "$k/look" && exec sleep 600) </dev/null >/dev/null 2>&1 &
sh=$!; KIDS+=("$sh")
HT_RAM=0 T1_OWNQ=backup T1_QPORT=$((qp + 1)) drive c42 backup
chk "a rerun in K: exit 0, not $rc" same "$rc" 0
chk "  its own VMs went (ht_qemus, vm_down, ht_free)" eval '[[ -s $k/logs/ownq1.pid ]] && same "$(cat "$k/logs/qemus.txt")" "$(cat "$k/logs/ownq1.pid")" &&
    has "$k/logs/ownq1.txt" down && p=$(cat "$k/logs/ownq2.pid") && [[ $p =~ ^[0-9]+$ ]] && dead "$p"'
chk "  the VM started by hand still lives" eval '! dead "$dq"'
chk "  the loop device on K's look.raw is still there (${ll:-none})" same "$(losetup -l -n -O BACK-FILE "$ll" 2>/dev/null | tr -d ' ')" "$k/look.raw"
chk "  the mount at K/look is still there" mountpoint -q "$k/look"
chk "  a shell in it lives" eval '! dead "$sh"'
kill "$dq" "$sh" 2>/dev/null; wait "$dq" "$sh" 2>/dev/null
umount "$k/look" 2>/dev/null; [[ -z $ll ]] || losetup -d "$ll" 2>/dev/null
verdict 42 "on /bkoscratch a run takes down only what it started: a VM, loop device, mount and shell of another's on K's files stay" "$o1" "$out"

out=$FX/out-c43 r=$HT_WORK/c43/fake k=$HT_RECORDS/c43/fake
T1_HOLD=6 "$0" run c43 source >"$out" 2>&1 &
d=$!; KIDS+=("$d")
waitfor 60 test -e "$FX/hold-c43"   # in ht_release: then outside holders of R
outq "$r/src-0.qcow2" "$qp" "" "$r/report.txt"; oq=$OQ
(unset TMPDIR; cd "$r" && exec sleep 600) </dev/null >/dev/null 2>&1 &
oh=$!; KIDS+=("$oh")
wait "$d"; rc=$?
chk "exit 0, not $rc" same "$rc" 0
chk "a VM started by hand on R's disk, holding R, was killed after ht_release (by the holder pass: it isn't the run's)" dead "$oq"
chk "and so was a shell in R" dead "$oh"
chk "nothing still held" eval '! grep -q "is still held" "$out"'
chk "R is gone" gone "$HT_WORK/c43"
chk "the run is in K" there "$k" report.txt logs/kept.txt
verdict 43 "in RAM what holds R from outside the run is still killed after ht_release" "$out"

HT_FREE_KIB=524288 T1_BIG=backup T1_BIGWAIT=12 drive c44a; oa=$out
k=$HT_RECORDS/c44a/fake
chk "within its reservation: exit 0, not $rc" same "$rc" 0
chk "  RESULT: PASS last, not '$(tail -n 1 "$k/report.txt")'" same "$(tail -n 1 "$k/report.txt")" "RESULT: PASS"
chk "  a note: line says the RAM disk was that full, once" \
    same "$(grep -c '^note: .*the RAM disk had under 1 GiB free; this run was within its reservation' "$k/report.txt")" 1
chk "  no harness stop" eval '! grep -q harness "$k/report.txt" && hasre "$k/logs/kept.txt" "^pass: records only" && same "$(pk c44a result)" PASS'
chk "  R is gone" gone "$HT_WORK/c44a"
mb=$(awk -v r="$RES" 'BEGIN { printf "%d", r * 1024 + 16 }')   # MiB: the reservation and 16 MiB, under its limit
fr=$(df -Pk /mnt/bko-ram | awk 'NR == 2 { print $4 }')
if ((fr >= (mb + 2048) * 1024)); then   # (2 GiB left free, for others' runs)
    HT_FREE_KIB=524288 T1_BIG=backup T1_BIGMB=$mb T1_BIGWAIT=60 drive c44b
    k=$HT_RECORDS/c44b/fake
    chk "over its reservation ($mb MiB): exit 1, not $rc" same "$rc" 1
    chk "  stopped at once (wall $(pk c44b wall_s) s, not 60+)" awk -v w="$(pk c44b wall_s)" 'BEGIN { exit !(w ~ /^[0-9]+$/ && w < 45) }'
    chk "  the harness's RESULT line last, not '$(tail -n 1 "$k/report.txt")'" eval 'tail -n 1 "$k/report.txt" | grep -qE "^RESULT: FAIL \(1; harness: the RAM disk has under 1 GiB free \(0\.5 GiB\), the run over its reservation at [0-9]+\.[0-9]+ GiB of $RES GiB reserved; stopped before its RESULT, exit 143\)\$"'
    chk "  records only, not $(ls_of "$k")" same "$(ls_of "$k")" "dumps logs report.txt src-meta.json.zst "
    chk "  kept.txt: overrun" hasre "$k/logs/kept.txt" '^overrun: records only, [0-9TZ:-]+; harness: the RAM disk has under 1 GiB free '
    chk "  no within-its-reservation note" eval '! grep -q "this run was within its reservation" "$k/report.txt"'
    chk "  R is gone" gone "$HT_WORK/c44b"
else
    prob+="${prob:+; }/mnt/bko-ram had $((fr / 1024)) MiB free, under the $((mb + 2048)) MiB the run over its reservation needs: not run"
fi
ob=$out
# (HT_RAM=2 is refused next, should a lib let HT_FREE_KIB through: nothing is made in the real HT_WORK.)
HT_RAM=2 HT_WORK=/mnt/bko-ram/holttest1d HT_FREE_KIB=1 drive c44c source
chk "HT_FREE_KIB with the default HT_WORK: exit 2, not $rc" same "$rc" 2
chk "  says why" grep -qF "HT_FREE_KIB=1: a number of KiB, for tests (with their own HT_WORK) only" "$out"
chk "  nothing made" gone /mnt/bko-ram/holttest1d/c44c "$HT_RECORDS/c44c"
verdict 44 "a RAM disk under 1 GiB free (HT_FREE_KIB): a note: line within the reservation, past it a stop with records only" "$oa" "$ob" "$out"

# Every attempt in K has the TMPDIR K/tmp: an earlier one's VM still up on
# K's files would be a new run's own.  On another port than the run's.
k=$HT_RECORDS/c45/fake
HT_RAM=0 HT_KEEP=1 T1_OWNQ=rebuild T1_QPORT=$((qp + 2)) drive c45 source backup rebuild; r0=$rc o0=$out
kq=$(cat "$k/logs/ownq2.pid" 2>/dev/null)
chk "HT_KEEP=1 on /bkoscratch kept its rescuer ($kq) up: exit 0, not $r0" \
    eval '[[ $r0 == 0 && $kq =~ ^[0-9]+$ && -f $k/.ht/held ]] && ! dead "$kq"'
HT_RAM=0 drive c45 source; o1=$out
chk "a run from source then: exit 2, not $rc" same "$rc" 2
chk "  says why" grep -qF "an earlier attempt's VM is up on $k's files (QEMU pid $kq, kept up: $k/.ht/held): stop it first" "$o1"
chk "  nothing renamed or stopped: no K.1, K/.ht/held stays, the rescuer lives" eval 'gone "$k.1" && [[ -f $k/.ht/held ]] && ! dead "$kq"'
HT_RAM=0 drive c45 rebuild; o2=$out
chk "a rerun from rebuild in K takes it over: exit 0, not $rc" same "$rc" 0
chk "  and ht_free took it down" dead "$kq"
outq "$k/dst-0.qcow2" "$((qp + 3))" "$k/tmp"; oq=$OQ   # a dead driver's VM: K's own TMPDIR
HT_RAM=0 drive c45 source; o3=$out
chk "a dead driver's VM up on K's files: a run from source, exit 2, not $rc" same "$rc" 2
chk "  says why" grep -qF "an earlier attempt's VM is up on $k's files (QEMU pid $oq): stop it first" "$o3"
chk "  the VM lives" eval '! dead "$oq"'
kill "$oq" 2>/dev/null; wait "$oq" 2>/dev/null
HT_RAM=0 drive c45 source
chk "once it is stopped, the run from source goes: exit 0, not $rc" same "$rc" 0
chk "  the attempt before is K.1" eval 'there "$k.1" dst-0.qcow2 rescuer.qcow2 && gone "$k.2"'
verdict 45 "on /bkoscratch a run from source refuses while an earlier attempt's VM is up on K's files; a rerun in K takes it over" \
    "$o0" "$o1" "$o2" "$o3" "$out"

# A look at a run's disk while it runs, by hand: a vm-mount of a file in R,
# its mount point elsewhere, without the run's TMPDIR.  Its qemu-nbd serves
# a mounted disk.  On the last free nbd devices, which others' vm-mounts
# (the first free one) take last.
out=$FX/out-c46 r=$HT_WORK/c46/fake k=$HT_RECORDS/c46/fake
T1_GO=1 "$0" run c46 source >"$out" 2>&1 &
d=$!; KIDS+=("$d")
waitfor 60 test -e "$FX/hold-c46"   # in ht_release
mrc=- nq="" dv=""
if [[ -f $FX/look.raw && -d $r ]] && cp --sparse=always "$FX/look.raw" "$r/look.raw"; then
    nbds=$(for b in /sys/block/nbd*; do [[ -e $b/pid ]] || echo "${b##*/}"; done | tail -n 3 | tr '\n' ' ')
    (unset TMPDIR; export VM_MOUNT_NBD=$nbds; exec "$TV/vm-mount" --ro "$r/look.raw" "$LMP") >"$FX/out-c46-mount" 2>&1; mrc=$?
    dv=$(head -n 1 "$LST" 2>/dev/null)
    nq=$(cat "/sys/block/${dv#/dev/}/pid" 2>/dev/null) && nq=$(awk '$1 == "Tgid:" { print $2 }' "/proc/$nq/status" 2>/dev/null)
fi
: >"$FX/go-c46"
wait "$d"; rc=$?
chk "the fixture (FX/look.raw)" test -f "$FX/look.raw"
chk "the vm-mount by hand: exit 0, not $mrc" same "$mrc" 0
chk "the run: exit 0, not $rc" same "$rc" 0
chk "its qemu-nbd (${nq:-none}) lives and serves ${dv:-no device}" eval '[[ $nq =~ ^[0-9]+$ && -e /sys/block/${dv#/dev/}/pid ]] && ! dead "$nq"'
chk "  its state and mount stay" eval '[[ -f $LST ]] && mountpoint -q "$LMP" && [[ -f $LMP/etc/fstab ]]'
chk "the caller's log says R is still held by it, once" eval 'grep -qF "$r is still held: vm-mount:$LMP (qemu-nbd $nq): a vm-mount the run didn'"'"'t make" "$out" &&
    same "$(grep -c "is still held" "$out")" 1'
chk "R is gone, the run is in K" eval 'gone "$HT_WORK/c46" && there "$k" report.txt logs/kept.txt look.raw'
unlook >"$FX/out-c46-umount" 2>&1; urc=$?
chk "its vm-umount then: exit 0, not $urc" same "$urc" 0
chk "  its qemu-nbd is gone, and its state" eval '[[ -n $nq ]] && dead "$nq" && [[ ! -e $LST ]]'
verdict 46 "in RAM a vm-mount someone else made of a file in R stays up (its qemu-nbd isn't killed), and R is said still held" \
    "$out" "$FX/out-c46-mount" "$FX/out-c46-umount" "$FX/look.log"

if ((nfail == 0)); then echo "ht1-lib-test: all $ncase cases ok"; exit 0; fi
echo "ht1-lib-test: $nfail of $ncase cases FAIL"
exit 1
