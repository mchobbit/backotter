# Sourced: boot a throwaway overlay of haiku6 and talk to it.
# Needs: PORT, W (scratch dir).  g_boot [EXTRA_DISK...]; ssh_g CMD; g_stop
VM=$HOME/backotter-work/vms/haiku6
VMRUN=$HOME/backotter-work/testvm/vm-run
ssh_g() {
    timeout "${T:-900}" ssh -q -i "$VM/id_ed25519" -p "$PORT" -o BatchMode=yes \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 \
        -o ConnectTimeout=10 "$(cat "$VM/ssh-user")"@127.0.0.1 "$@"
}
g_running() { pgrep -f "^qemu-system.*:$PORT-:22" >/dev/null; }
g_boot() {
    local d extra=()
    g_running && { echo "port $PORT in use" >&2; return 1; }
    for d in "$@"; do extra+=(--disk "$d"); done
    # shellcheck disable=SC2046
    TMPDIR=$W SSH_PORT=$PORT setsid -f "$VMRUN" $(<"$VM/vm-run-options") --snapshot \
        --memory 4096 --cpus 4 "${extra[@]}" --serial-log "$W/serial.log" "$VM/haiku6.qcow2" \
        </dev/null >"$W/vm-run.log" 2>&1
    for _ in $(seq 30); do g_running && break; sleep 1; done
    local deadline=$((SECONDS + 400))
    until T=15 ssh_g true 2>/dev/null; do
        [[ $SECONDS -lt $deadline ]] || { echo "sshd did not come up" >&2; return 1; }
        g_running || { cat "$W/vm-run.log" >&2; echo "QEMU exited" >&2; return 1; }
        sleep 4
    done
}
g_stop() {
    T=20 ssh_g "shutdown -q" >/dev/null 2>&1 || true
    for _ in $(seq 40); do g_running || break; sleep 1; done
    pkill -f "^qemu-system.*:$PORT-:22" 2>/dev/null || true
    for _ in $(seq 10); do g_running || break; sleep 1; done
}
