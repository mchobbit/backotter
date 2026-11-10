#!/usr/bin/env bash
# GuestTest2's probe of Redox (contract 2.1 step 4, ht1-offline.sh's boot check): no SSH, so
# boot a throwaway overlay in reserved RAM, log in as root on the serial console, uname and
# hostname.  probe-redox.sh GUEST OUT writes OUT/hook-probe.out (=== cmd probe, ok or what
# failed, === end), OUT/serial.log and OUT/result.json.  The guest's disk is only read.
set -u
G=$1 OUT=$2
here=$(dirname "$(realpath "$0")")
TV=$(dirname "$(dirname "$here")")
W=${BKO_WORK:-$HOME/backotter-work}
gdir=$W/vms/$G
img=$gdir/$G.qcow2; [[ -f $img ]] || img=$(ls "$gdir"/*.qcow2 | head -n 1)
R=$(mktemp -d /mnt/bko-ram/t2-guest-probe.XXXXXX) || exit 2
printf '{"pid": %d, "work": "%s"}\n' "$$" "$(realpath "$W")" > "$R/owner.json"   # for preflight's sweep, after a KILL
sock=$R/console.sock ov=$R/boot.qcow2 log=$OUT/serial.log
pid= term=0
t0=$SECONDS
stop_vm() {  # after a normal end the guest powers off; after a TERM vm-run has it too (job.sh signals the group)
    [[ -n $pid ]] || return 0
    local n=24 s=5
    if [[ $term -eq 1 ]]; then n=30 s=1; fi
    for _ in $(seq "$n"); do kill -0 "$pid" 2>/dev/null || return 0; sleep "$s"; done
    kill "$pid" 2>/dev/null; sleep 2
}
trap 'trap "" TERM INT HUP; stop_vm; rm -rf "$R"' EXIT
trap 'term=1; exit 143' TERM INT HUP
qemu-img create -q -f qcow2 -b "$img" -F qcow2 "$ov" || exit 2
fw=(); [[ -f $gdir/vm-run-options ]] && read -ra fw < "$gdir/vm-run-options"
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
SSH_PORT=$port "$TV/vm-run" "${fw[@]}" "$ov" -- -chardev "socket,id=console,path=$sock,server=on,wait=off,logfile=$log" \
    -serial chardev:console -monitor none -no-reboot </dev/null >"$OUT/vm-run.log" 2>&1 &
pid=$!
c=(python3 "$TV/redox/console.py" "$sock" "$gdir/root-password")
{ echo "=== cmd probe"
  if "${c[@]}" login 600 >"$OUT/login.log" 2>&1; then
      if "${c[@]}" run 'uname -s; cat /etc/hostname' >"$OUT/check.log" 2>&1 && grep -qx Redox "$OUT/check.log"; then
          echo ok
      else
          echo "uname didn't say Redox"
      fi
      "${c[@]}" send 'poweroff' >/dev/null 2>&1
  else
      echo "no root login on the console"
  fi
  echo "=== end"; } > "$OUT/hook-probe.out"
python3 -I - "$OUT/result.json" "$G" $((SECONDS - t0)) <<'PY'
import json, sys
json.dump({"guest": sys.argv[2], "mode": "console", "rc": 0, "phase": "done", "wall_s": int(sys.argv[3])}, open(sys.argv[1], "w"))
PY
