#!/usr/bin/env bash
# Whether uc24-fde, booted on a new machine (a NEW swtpm state and fresh
# OVMF variables), asks for its recovery key on the serial console, and
# whether, once the key is typed, the NEXT boot on that same new TPM is
# unattended: Ubuntu Core re-provisioning onto a different TPM
# (docs/design/ubuntu-core.md, HoltTest1's cell d).  The guest itself is
# only read: every boot is of a qcow2 overlay on it in WORK, with WORK's
# TPM and variables.  BOOTS boots (default 3) on the same overlay, TPM and
# variables, each typing the key at each prompt (type-recovery-key.py) and
# waiting for SSH in run mode; each one's console in WORK/boot-N.log, its
# probe in WORK/boot-N.txt, and one summary line per boot on stdout:
#   BKO-NEWTPM boot N: typed K, run mode over SSH
#
# --same-tpm: a copy of the guest's own tpm/ instead of a new one;
# --same-vars: a copy of its uefi-vars.fd instead of fresh variables (with
# both: the same machine, as HoltTest1's cell c boots it).
#
# Usage: new-tpm-test.sh [--same-tpm] [--same-vars] GUEST_DIR KEY_FILE WORK PORT [BOOTS]
set -euo pipefail
same_tpm=no same_vars=no
while [[ ${1:-} == --same-* ]]; do
    case $1 in --same-tpm) same_tpm=yes ;; --same-vars) same_vars=yes ;; *) echo "new-tpm-test: unknown option $1" >&2; exit 1 ;; esac
    shift
done
here=$(dirname "$(realpath "$0")")
testvm=$(dirname "$here")
die() { echo "new-tpm-test: $*" >&2; exit 1; }
[[ $# -ge 4 ]] || die "usage: $0 [--same-tpm] [--same-vars] GUEST_DIR KEY_FILE WORK PORT [BOOTS]"
gdir=$(realpath "$1") key=$(realpath "$2") work=$3 port=$4 boots=${5:-3}
g=$(basename "$gdir")
[[ -f $gdir/$g.qcow2 && -f $gdir/id_ed25519 && -f $key ]] || die "need $gdir/$g.qcow2, its id_ed25519 and $key"
ss -ltnH "sport = :$port" | grep -q . && die "port $port is taken"
mkdir -p "$work"; work=$(realpath "$work")
[[ -e $work/disk.qcow2 ]] && die "$work/disk.qcow2 exists: a new WORK each run"
qemu-img create -q -f qcow2 -b "$gdir/$g.qcow2" -F qcow2 "$work/disk.qcow2"
if [[ $same_vars == yes ]]; then cp "$gdir/uefi-vars.fd" "$work/vars.fd"; else cp /usr/share/OVMF/OVMF_VARS_4M.ms.fd "$work/vars.fd"; fi
if [[ $same_tpm == yes ]]; then cp -a "$gdir/tpm" "$work/tpm"; else mkdir "$work/tpm"; fi
echo "BKO-NEWTPM $g: TPM $([[ $same_tpm == yes ]] && echo "a copy of the guest's" || echo new), variables $([[ $same_vars == yes ]] && echo "a copy of the guest's" || echo fresh)"
gssh=(ssh -q -i "$gdir/id_ed25519" -p "$port" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 tester@127.0.0.1)
pid=""
trap '[[ -n $pid ]] && kill "$pid" 2>/dev/null; true' EXIT
for n in $(seq "$boots"); do
    python3 -I "$here/type-recovery-key.py" "$work/boot-$n.log" "$key" -- \
        env SSH_PORT="$port" "$testvm/vm-run" --uefi --memory 2048 --uefi-vars "$work/vars.fd" --tpm "$work/tpm" "$work/disk.qcow2" \
        </dev/null >/dev/null 2>"$work/boot-$n.err" &
    pid=$!
    up=no
    for _ in $(seq 120); do
        kill -0 "$pid" 2>/dev/null || break
        timeout 20 "${gssh[@]}" 'grep -q snapd_recovery_mode=run /proc/cmdline' </dev/null 2>/dev/null && { up=yes; break; }
        sleep 10
    done
    typed=$(grep -ac 'type-recovery-key.py: typed' "$work/boot-$n.log" || true)
    prompts=$(grep -aic 'please enter the recovery key' "$work/boot-$n.log" || true)
    if [[ $up == yes ]]; then
        # Let snapd do what it does after such a boot (a reseal, if any) before probing.
        timeout 600 "${gssh[@]}" 'sudo snap wait system seed.loaded; sleep 60; snap changes' </dev/null >"$work/boot-$n.changes" 2>&1 || true
        timeout 300 "${gssh[@]}" sudo bash -s "newtpm-$n" <"$here/probe.sh" >"$work/boot-$n.txt" 2>&1 || true
        timeout 120 "${gssh[@]}" 'sudo journalctl -b --no-pager -o short-monotonic | grep -iE "recovery key|reseal|reprovision|provision|unlock|sealed|tpm|fde|secboot" | grep -vE "[0-9]{5}-[0-9]{5}"; echo "--- /run/snapd/snap-bootstrap"; sudo ls -la /run/snapd/snap-bootstrap; for f in /run/snapd/snap-bootstrap/*.json; do echo "== $f"; sudo cat "$f"; echo; done' \
            </dev/null >"$work/boot-$n.journal" 2>&1 || true
        timeout 30 "${gssh[@]}" 'sudo sync; sudo systemctl poweroff' </dev/null 2>/dev/null || true
    fi
    for _ in $(seq 90); do kill -0 "$pid" 2>/dev/null || break; sleep 2; done
    kill -0 "$pid" 2>/dev/null && { kill "$pid"; sleep 5; }
    wait "$pid" 2>/dev/null || true
    pid=""
    echo "BKO-NEWTPM boot $n: prompts $prompts, typed $typed, $([[ $up == yes ]] && echo "run mode over SSH" || echo "NO SSH")"
    [[ $up == yes ]] || die "boot $n never reached run mode over SSH; see $work/boot-$n.log"
done
