#!/usr/bin/env bash
# HoltTest1x driver (testvm/holttest1d/README.md): a whole-machine round trip
# of GUEST through bko-holt and rustic-bko.  Run as root on a test host:
#
#   ht1.sh GUEST RUN [PHASE...]      PHASEs: source backup rebuild verify
#                                    (default: all four, in that order)
#
# RUN names the run's directory, /bkoscratch/holttest1d/RUN/GUEST/.  Later
# phases reuse what earlier ones left there, so after a bko-holt fix only
# `rebuild verify` need running again.  Needs /bkoscratch/holttest1d/bin/
# with bko-holt, rustic-bko and f2fs-tools_*.deb (installed in the rescuer).
#
#   source   dump the live guest (a throwaway overlay; the guest's own
#            image is never written), with its boot state as the baseline
#   backup   rustic-bko backup of the overlay mounted read-only, as
#            compare.py does; fsmeta.py records it
#   rebuild  a rescuer VM (a throwaway debian13 overlay) gets a blank disk
#            and the share (9p, read-only); bko-holt load's safety checks
#            must refuse, then each stage runs with --commit, `status`
#            checked after each
#   verify   fsmeta.py compares the restored disk with the source; then the
#            restored disk boots alone (writes thrown away) and is dumped;
#            judge.py compares the dumps
#
# Exit 0 when every check of the phases run passed.  Results: logs/,
# dumps/, report.txt.  The rescuer is Debian 13, not BackOtter's rescue
# image yet (README: "Before it can run").
set -uo pipefail

G=$1 RUN=$2; shift 2
PHASES=("$@"); [[ ${#PHASES[@]} -gt 0 ]] || PHASES=(source backup rebuild verify)
W=/home/m/backotter-work
T=$W/testvm
BIN=/bkoscratch/holttest1d/bin
HERE=$(dirname "$(realpath "$0")")
R=/bkoscratch/holttest1d/$RUN/$G
S=$R/share
PORT=${HT_PORT:-2970}
export SSH_PORT=$PORT   # vm-run's guest SSH port
RESCUER=${HT_RESCUER:-debian13}
SRC_MP=/mnt/ht1-src-$G
DST_MP=/mnt/ht1-dst-$G
mkdir -p "$R/logs" "$R/dumps" "$S" /bkoscratch/tmp
export TMPDIR=/bkoscratch/tmp RT_RAM=0
fails=0
say() { echo "$*" | tee -a "$R/report.txt"; }
ok() { say "PASS $*"; }
bad() { say "FAIL $*"; fails=$((fails + 1)); }

img=$W/vms/$G/$G.qcow2
gkey=$W/vms/$G/id_ed25519
rkey=$W/vms/$RESCUER/id_ed25519
size=$(qemu-img info --output=json "$img" | python3 -c 'import json,sys; print(json.load(sys.stdin)["virtual-size"])')

sshk() { local key=$1; shift; timeout "${TMO:-600}" ssh -q -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o ServerAliveInterval=15 root@127.0.0.1 "$@"; }
scpk() { local key=$1; shift; timeout 300 scp -q -i "$key" -P "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$@"; }
vm_up() { # IMAGE LOG [vm-run options...] [-- QEMU_ARGS...]
    local image=$1 log=$2 opts=() qargs=(); shift 2
    while [[ $# -gt 0 && $1 != -- ]]; do opts+=("$1"); shift; done
    [[ ${1:-} == -- ]] && { shift; qargs=(-- "$@"); }
    setsid -f "$T/vm-run" --serial-log "$log" "${opts[@]}" "$image" "${qargs[@]}" </dev/null >"$log.vm-run" 2>&1
}
vm_wait() { local key=$1
    for _ in $(seq 72); do TMO=8 sshk "$key" true 2>/dev/null && return 0; sleep 5; done
    return 1
}
vm_down() { local key=$1
    TMO=20 sshk "$key" poweroff >/dev/null 2>&1
    for _ in $(seq 36); do pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null || return 0; sleep 5; done
    pkill -f "^qemu-system.*:$PORT-:22"; sleep 5
    pkill -9 -f "^qemu-system.*:$PORT-:22"; sleep 1
}
bootstate() { sshk "$1" 'systemctl is-system-running; systemctl --failed --no-legend --plain | awk "{print \$1}" | sort'; }

phase_source() {
    say "== source: $G"
    rm -f "$R/src.qcow2"
    qemu-img create -q -f qcow2 -b "$img" -F qcow2 "$R/src.qcow2"
    vm_up "$R/src.qcow2" "$R/logs/source-serial.log"
    vm_wait "$gkey" || { bad "source boots (no SSH)"; vm_down "$gkey"; return; }
    ok "source boots"
    bootstate "$gkey" > "$R/logs/source-bootstate.txt"
    scpk "$gkey" "$BIN/bko-holt" root@127.0.0.1:/root/bko-holt
    if sshk "$gkey" /root/bko-holt dump > "$R/dumps/src.json" 2> "$R/logs/source-dump.err"; then
        ok "dump on the live source"
    else
        bad "dump on the live source: $(tail -1 "$R/logs/source-dump.err")"
    fi
    sshk "$gkey" 'rm -f /root/bko-holt'
    vm_down "$gkey"
    cp "$R/dumps/src.json" "$S/layout.json"
}

phase_backup() {
    say "== backup"
    rm -rf "$S/repo"; head -c 24 /dev/urandom | base64 > "$S/repo-password"
    mkdir -p "$SRC_MP"
    "$T/vm-mount" --ro "$R/src.qcow2" "$SRC_MP" > "$R/logs/backup-mount.log" 2>&1 || { bad "mount the source read-only"; return; }
    local rustic=("$BIN/rustic-bko" -r "$S/repo" --password-file "$S/repo-password" --no-progress --no-cache)
    if "${rustic[@]}" init > "$R/logs/backup.log" 2>&1 && "${rustic[@]}" backup --set-atime yes "$SRC_MP" >> "$R/logs/backup.log" 2>&1; then
        ok "rustic-bko backup ($(du -sh "$S/repo" | cut -f1))"
    else
        bad "rustic-bko backup (logs/backup.log)"
    fi
    python3 "$T/compare/fsmeta.py" record "$SRC_MP" "$R/src-meta.json" > "$R/logs/fsmeta-src.log" 2>&1 || bad "fsmeta record of the source"
    "$T/vm-umount" "$SRC_MP" >> "$R/logs/backup-mount.log" 2>&1
    cp "$BIN/bko-holt" "$BIN/rustic-bko" "$BIN"/f2fs-tools_*.deb "$S/"
    chmod -R a+rX "$S"
}

# One bko-holt load call in the rescuer: WANT exit, NAME, then load's args.
load_step() { local want=$1 name=$2; shift 2
    sshk "$rkey" "/mnt/share/bko-holt load /mnt/share/layout.json $*" > "$R/logs/rebuild-$name.out" 2> "$R/logs/rebuild-$name.err"
    local got=$?
    if [[ $got == "$want" ]]; then ok "load $name (exit $got)"; return 0; fi
    bad "load $name: exit $got, want $want: $(grep -v '^$' "$R/logs/rebuild-$name.err" | tail -3 | tr '\n' ' ')"
    return 1
}

phase_rebuild() {
    say "== rebuild"
    cp "$BIN/bko-holt" "$S/"   # the latest build, when rerun after a fix
    rm -f "$R/rescuer.qcow2" "$R/dst-0.qcow2"
    qemu-img create -q -f qcow2 -b "$W/vms/$RESCUER/$RESCUER.qcow2" -F qcow2 "$R/rescuer.qcow2"
    qemu-img create -q -f qcow2 "$R/dst-0.qcow2" "$size"
    vm_up "$R/rescuer.qcow2" "$R/logs/rescuer-serial.log" --memory 3072 --disk "$R/dst-0.qcow2" -- \
        -virtfs "local,path=$S,mount_tag=bkoshare,security_model=none,readonly=on"
    vm_wait "$rkey" || { bad "rescuer boots"; vm_down "$rkey"; return; }
    sshk "$rkey" 'mkdir -p /mnt/share && mount -t 9p -o trans=virtio,version=9p2000.L,ro bkoshare /mnt/share && dpkg -i /mnt/share/f2fs-tools_*.deb >/dev/null' \
        > "$R/logs/rescuer-setup.log" 2>&1 || { bad "rescuer setup (share, f2fs-tools)"; vm_down "$rkey"; return; }
    ok "rescuer up, share mounted"
    # Safety first: each refuses and writes nothing.
    load_step 4 refuse-nomap
    load_step 4 refuse-root --map vda=vda --erase vda
    load_step 0 dry-partition --map vda=vdb --only partition
    if sshk "$rkey" 'test -z "$(wipefs -n /dev/vdb)"'; then ok "nothing written without --commit"; else bad "vdb was written without --commit"; fi
    local map=(--map vda=vdb --erase /dev/vdb)
    local repo=(--repo /mnt/share/repo --password-file /mnt/share/repo-password --archiver /mnt/share/rustic-bko --snapshot "latest:$SRC_MP")
    for s in partition assemble format mount restore boot close; do
        local extra=(); [[ $s == restore ]] && extra=("${repo[@]}")
        TMO=3600 load_step 0 "$s" "${map[@]}" "${extra[@]}" --only "$s" --commit || { sshk "$rkey" 'cat /run/bko-holt/log/load.log' > "$R/logs/rebuild-load.log"; vm_down "$rkey"; return; }
        sshk "$rkey" "/mnt/share/bko-holt load /mnt/share/layout.json ${map[*]} --only status" > "$R/logs/rebuild-status-after-$s.txt" 2>&1
        grep -q "^$s  *done" "$R/logs/rebuild-status-after-$s.txt" || [[ $s == assemble ]] || bad "status after $s: $(grep "^$s " "$R/logs/rebuild-status-after-$s.txt")"
    done
    sshk "$rkey" 'cat /run/bko-holt/log/load.log' > "$R/logs/rebuild-load.log"
    vm_down "$rkey"
    rm -f "$R/rescuer.qcow2"
}

phase_verify() {
    say "== verify"
    mkdir -p "$DST_MP"
    if "$T/vm-mount" --ro "$R/dst-0.qcow2" "$DST_MP" > "$R/logs/verify-mount.log" 2>&1; then
        python3 "$T/compare/fsmeta.py" record "$DST_MP" "$R/dst-meta.json" > "$R/logs/fsmeta-dst.log" 2>&1
        "$T/vm-umount" "$DST_MP" >> "$R/logs/verify-mount.log" 2>&1
        python3 "$T/compare/fsmeta.py" compare "$R/src-meta.json" "$R/dst-meta.json" > "$R/logs/fsmeta-compare.txt" 2>&1
        if python3 "$HERE/judge.py" fs "$T/compare/fsmeta.py" "$R/src-meta.json" "$R/dst-meta.json" > "$R/logs/fsjudge.txt" 2>&1; then
            ok "fsmeta: the files agree but for the boot stage's ($(tail -1 "$R/logs/fsjudge.txt"))"
        else
            bad "fsmeta: $(grep '^DIFF' "$R/logs/fsjudge.txt" | head -5 | tr '\n' ' ')"
        fi
    else
        bad "mount the restored disk"
    fi
    vm_up "$R/dst-0.qcow2" "$R/logs/restored-serial.log" --snapshot
    if ! vm_wait "$gkey"; then bad "the restored disk boots by itself (no SSH; logs/restored-serial.log)"; vm_down "$gkey"; return; fi
    ok "the restored disk boots by itself, SSH with the guest's key"
    bootstate "$gkey" > "$R/logs/restored-bootstate.txt"
    if diff -q <(head -1 "$R/logs/source-bootstate.txt") <(head -1 "$R/logs/restored-bootstate.txt") >/dev/null \
        && [[ -z $(comm -13 <(tail -n +2 "$R/logs/source-bootstate.txt") <(tail -n +2 "$R/logs/restored-bootstate.txt")) ]]; then
        ok "boot state as the source's ($(head -1 "$R/logs/restored-bootstate.txt"))"
    else
        bad "boot state: source $(tr '\n' ' ' < "$R/logs/source-bootstate.txt"), restored $(tr '\n' ' ' < "$R/logs/restored-bootstate.txt")"
    fi
    if sshk "$gkey" 'findmnt --verify >/dev/null 2>&1 && swapon --show --noheadings | grep -q . && ls /var/log/bko-holt/load.log >/dev/null' > "$R/logs/restored-health.txt" 2>&1; then
        ok "health: fstab verifies, swap on, load's log in /var/log/bko-holt"
    else
        bad "health checks (findmnt --verify, swap, /var/log/bko-holt)"
    fi
    scpk "$gkey" "$BIN/bko-holt" root@127.0.0.1:/root/bko-holt
    sshk "$gkey" '/root/bko-holt dump; rm -f /root/bko-holt' > "$R/dumps/dst.json" 2> "$R/logs/restored-dump.err" || bad "dump of the restored system"
    vm_down "$gkey"
    if python3 "$HERE/judge.py" "$R/dumps/src.json" "$R/dumps/dst.json" > "$R/logs/judge.txt" 2>&1; then
        ok "the dumps agree ($(tail -1 "$R/logs/judge.txt"))"
    else
        bad "the dumps differ: $(grep -c '^DIFF' "$R/logs/judge.txt") fields (logs/judge.txt)"
    fi
}

: > "$R/report.txt"
say "HoltTest1 $G, run $RUN, $(date -u +%FT%TZ), phases: ${PHASES[*]}"
# Each phase needs the one before it: stop at the first that fails.
for p in "${PHASES[@]}"; do
    "phase_$p"
    [[ $fails == 0 ]] || { say "stopped after $p"; break; }
done
say "RESULT: $([[ $fails == 0 ]] && echo PASS || echo "FAIL ($fails)")"
[[ $fails == 0 ]]
