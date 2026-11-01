# Functions for scripts that work in a throwaway copy of a test guest:
# boot it with vm-run, run commands and copy files in over ssh, power it off.
# Sourced (bash), by stick/build-rustic-native.sh and testvm/compare's
# drivers.  One guest at a time per script; the caller sets WORK (the
# backotter-work directory, with vms/GUEST/ in it).
#
#   vm_boot GUEST PORT LOGDIR [VM-RUN OPTION...]
#             Boot a copy of WORK/vms/GUEST/GUEST.qcow2 (else its only
#             *.qcow2; VM_IMAGE names another image, e.g. a build image)
#             with SSH on 127.0.0.1:PORT, the options in its vm-run-options,
#             then the ones given (--ram or --snapshot among them, for a
#             throwaway copy), and wait for its sshd (VM_BOOT_TIMEOUT seconds,
#             default 600).  The serial console goes to LOGDIR/serial.log,
#             vm-run's output to LOGDIR/vm-run.log.  Returns 1 if the guest
#             didn't come up (QEMU is stopped then).
#   vm_ssh COMMAND
#             Run COMMAND as root through /bin/sh -c, whatever root's login
#             shell is (DragonFly's is csh, which can't parse sh's syntax);
#             stdin and stdout pass through.  Keep it to one line: csh ends a
#             quoted string at a newline.  T bounds it (seconds, default
#             3600): ssh's own ConnectTimeout never fires here, since QEMU's
#             user network accepts the connection before the guest is up.
#   vm_sh SCRIPT
#             Run SCRIPT, any number of lines, with the guest's /bin/sh (fed
#             on stdin, so not for commands that read stdin themselves).
#   vm_put FILE DEST [MODE]
#             Copy FILE to DEST in the guest (mode MODE, default 755) over
#             ssh with cat: AlpineBSD has no scp or sftp server.
#   vm_halt   Power the guest off and make sure its QEMU is gone, by PID.
#             The poweroff goes over ssh only while this script's QEMU is
#             the one listening on PORT (vm_owns_port): copies of one guest
#             share its key, so a poweroff sent after ours had exited would
#             stop whichever copy another script (another session's run on
#             the same port) has booted there since.  vm_boot likewise only
#             takes an answer from its own QEMU.
#
# Needs: qemu-system-x86_64 with KVM, testvm/vm-run, ssh, timeout, ss.
# shellcheck shell=bash

VM_PID="" VM_GUEST="" VM_PORT="" VM_KEY=""
VM_LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

vm_boot() {
    local guest=$1 port=$2 logdir=$3; shift 3
    local dir=$WORK/vms/$guest image opts=() deadline
    image=${VM_IMAGE:-}
    if [[ -z $image ]]; then
        image=$dir/$guest.qcow2
        [[ -f $image ]] || image=$(ls "$dir"/*.qcow2 2>/dev/null | head -n 1 || true)
    fi
    [[ -f $image && -f $dir/id_ed25519 ]] || { echo "vm_boot: no guest $guest in $dir" >&2; return 1; }
    if [[ -n $(ss -Hltn "sport = :$port" 2>/dev/null) ]]; then
        echo "vm_boot: port $port is in use" >&2
        return 1
    fi
    if [[ -f $dir/vm-run-options ]]; then read -ra opts <"$dir/vm-run-options" || true; fi
    # illumos' vioif can't map the modern virtio-net's registers: without
    # this the guest has no network (and no sshd to reach).
    if [[ $guest == omnios* && " ${opts[*]} " != *" --legacy-virtio-net "* ]]; then
        opts+=(--legacy-virtio-net)
    fi
    mkdir -p "$logdir"
    VM_GUEST=$guest VM_PORT=$port VM_KEY=$dir/id_ed25519
    # setsid: Ctrl-C here doesn't reach QEMU, and vm_halt does the stopping.
    # Not setsid -f: $! has to be vm-run itself.
    SSH_PORT=$port setsid "$VM_LIB_DIR/vm-run" "${opts[@]}" "$@" --serial-log "$logdir/serial.log" \
        "$image" </dev/null >"$logdir/vm-run.log" 2>&1 &
    VM_PID=$!
    deadline=$((SECONDS + ${VM_BOOT_TIMEOUT:-600}))
    until vm_owns_port && T=15 vm_ssh true 2>/dev/null && vm_owns_port; do
        if ! kill -0 "$VM_PID" 2>/dev/null; then
            echo "vm_boot: QEMU exited; $logdir/vm-run.log says:" >&2
            tail -n 5 "$logdir/vm-run.log" >&2
            wait "$VM_PID" 2>/dev/null || true
            VM_PID=""
            return 1
        fi
        if [[ $SECONDS -ge $deadline ]]; then
            echo "vm_boot: $guest's sshd didn't come up (serial console: $logdir/serial.log)" >&2
            vm_halt
            return 1
        fi
        sleep 3
    done
}

# Whether this script's QEMU (vm-run's child, or VM_PID itself) is what
# listens on VM_PORT, by ss's process column.  Where ss shows no process for
# the port's listener (not ours to see), as long as our QEMU runs: it can't be
# told.
vm_owns_port() {
    [[ -n $VM_PID ]] && kill -0 "$VM_PID" 2>/dev/null || return 1
    local line pids p
    line=$(ss -Hltnp "sport = :$VM_PORT" 2>/dev/null)
    [[ -n $line ]] || return 1
    pids=$(grep -o 'pid=[0-9]*' <<<"$line" | cut -d= -f2)
    [[ -n $pids ]] || return 0
    for p in $pids; do
        [[ $p == "$VM_PID" ]] && return 0
        [[ $(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ') == "$VM_PID" ]] && return 0
    done
    return 1
}

vm_ssh() {
    local sq="'"
    local q=${1//$sq/$sq\\$sq$sq}
    timeout "${T:-3600}" ssh -q -i "$VM_KEY" -p "$VM_PORT" -o BatchMode=yes \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 \
        -o ServerAliveInterval=30 root@127.0.0.1 "/bin/sh -c '$q'"
}

vm_sh() {
    printf '%s\n' "$1" | vm_ssh '/bin/sh -s'
}

vm_put() {
    local sq="'"
    local dest=${2//$sq/$sq\\$sq$sq}
    T=${T:-600} vm_ssh "cat >'$dest' && chmod ${3:-755} '$dest'" <"$1"
}

vm_halt() {
    [[ -n $VM_PID ]] || return 0
    local pid=$VM_PID q
    VM_PID=""
    # Full paths: NetBSD root's PATH has no /sbin.  OpenBSD has no poweroff,
    # illumos has its shutdown in /usr/sbin.
    if VM_PID=$pid vm_owns_port; then
        T=30 vm_ssh '/sbin/poweroff || /sbin/shutdown -p now || /usr/sbin/shutdown -y -i5 -g0' \
            >/dev/null 2>&1 || true
    fi
    for _ in $(seq 30); do kill -0 "$pid" 2>/dev/null || break; sleep 2; done
    if kill -0 "$pid" 2>/dev/null; then
        # vm-run passes TERM on to its QEMU, then deletes its --ram overlay.
        kill "$pid" 2>/dev/null || true
        for _ in $(seq 15); do kill -0 "$pid" 2>/dev/null || break; sleep 2; done
    fi
    if kill -0 "$pid" 2>/dev/null; then
        # QEMU by its PID (vm-run's child): vm-run then cleans up and exits.
        for q in $(pgrep -P "$pid" 2>/dev/null); do kill -KILL "$q" 2>/dev/null || true; done
        for _ in $(seq 10); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
        # Only if vm-run itself hangs; a --ram overlay may then stay in /dev/shm.
        kill -KILL "$pid" 2>/dev/null || true
        echo "vm_halt: had to kill $VM_GUEST's QEMU" >&2
    fi
    wait "$pid" 2>/dev/null || true
}
