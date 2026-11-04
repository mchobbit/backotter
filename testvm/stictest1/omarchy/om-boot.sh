# Sourced (bash) after testvm/vm-lib.sh: om_boot PORT LOGDIR [VM-RUN OPTION...]
# boots a copy of vms/omarchy as vm_boot does, plus a QEMU monitor socket
# (LOGDIR/mon) on which type-luks-passphrase.py types the LUKS passphrase
# at Plymouth's prompt (Omarchy asks on its screen, not on the serial
# console) every 30 s until sshd answers.  vm_ssh/vm_put/vm_halt then work.
om_boot() {
    local port=$1 logdir=$2; shift 2
    local dir=$WORK/vms/omarchy opts=() deadline typer
    [[ -f $dir/omarchy.qcow2 && -f $dir/luks-passphrase ]] || { echo "om_boot: no omarchy in $dir" >&2; return 1; }
    [[ -z $(ss -Hltn "sport = :$port" 2>/dev/null) ]] || { echo "om_boot: port $port is in use" >&2; return 1; }
    read -ra opts <"$dir/vm-run-options" || true
    mkdir -p "$logdir"
    VM_GUEST=omarchy VM_PORT=$port VM_KEY=$dir/id_ed25519
    SSH_PORT=$port setsid "$WORK/testvm/vm-run" "${opts[@]}" "$@" --serial-log "$logdir/serial.log" \
        "$dir/omarchy.qcow2" -- -monitor "unix:$logdir/mon,server,nowait" </dev/null >"$logdir/vm-run.log" 2>&1 &
    VM_PID=$!
    python3 "$WORK/testvm/omarchy/type-luks-passphrase.py" "$logdir/mon" "$dir/luks-passphrase" $(seq 20 20 400) \
        >"$logdir/typer.log" 2>&1 &
    typer=$!
    deadline=$((SECONDS + ${VM_BOOT_TIMEOUT:-480}))
    until vm_owns_port && T=15 vm_ssh true 2>/dev/null && vm_owns_port; do
        if ! kill -0 "$VM_PID" 2>/dev/null; then
            echo "om_boot: QEMU exited: $(tail -n 3 "$logdir/vm-run.log")" >&2
            kill $typer 2>/dev/null; wait "$VM_PID" 2>/dev/null; VM_PID=""; return 1
        fi
        if [[ $SECONDS -ge $deadline ]]; then
            echo "om_boot: sshd didn't come up (see $logdir/serial.log)" >&2
            kill $typer 2>/dev/null; vm_halt; return 1
        fi
        sleep 3
    done
    kill $typer 2>/dev/null; wait $typer 2>/dev/null
    return 0
}
