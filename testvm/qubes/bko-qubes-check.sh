#!/bin/bash
# BackOtter's boot check for the Qubes OS test guest qubes43
# (docs/design/qubes.md, "The check unit").  Test-only code in the guest's
# dom0, as sculpt-check.sh is test-only: bko-qubes-check.service runs it as
# root at every boot, once qubesd is up.  dom0 has no network, so it reports
# on the serial console (/dev/hvc0, Xen's console, so com1) and in
# /var/log/bko-qubes-check.log, one line per item:
#   BKO-QUBES-CHECK ITEM PASS|FAIL DETAIL
# and last
#   BKO-QUBES-CHECK DONE PASS|FAIL
# with BKO-QUBES-INFO lines between them (qvm-ls, the LVs) for the record.
#
# It starts the Fedora and Debian templates, sys-firewall, work and
# personal, one after another (qvm-start, timed); runs a command in each
# (qvm-run --pass-io) that prints its hostname and the SHA-256 of
# /home/user/bko-seed.txt where there is one (work and personal: the seed
# make-qubes.sh's revisions.sh wrote in its second round, which must read
# back as written); lists the qubes; shuts every qube down; then lists
# qubes_dom0's LVs with their origins and attributes and checks the pools,
# work's revision chain (revisions_to_keep 2: vm-work-private and two
# -back revisions) and that no -snap LV is left.
#
# Usage: bko-qubes-check.sh        (as root in dom0)
# Exit 0 if DONE PASS.
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

LOG=/var/log/bko-qubes-check.log
VG=qubes_dom0
QUBES=(fedora-44-xfce debian-13-xfce sys-firewall work personal)
SEEDED=" work personal "
START_TIMEOUT=900
RUN_TIMEOUT=300
ok=1

say() {  # LINE: to the serial console, the log and stdout (the journal)
    echo "$1" >>"$LOG"
    echo "$1" >/dev/hvc0 2>/dev/null || true
    echo "$1"
}
item() {  # ITEM PASS|FAIL DETAIL...
    local name=$1 res=$2; shift 2
    [[ $res == PASS ]] || ok=0
    say "BKO-QUBES-CHECK $name $res $*"
}
info() { say "BKO-QUBES-INFO $*"; }
# What revisions.sh writes in round R into QUBE's seed file.
seed_text() { printf 'bko-qubes seed %s round %s\n' "$1" "$2"; }

: >"$LOG"
say "BKO-QUBES-CHECK BEGIN $(date -u +%Y-%m-%dT%H:%M:%SZ) boot $(cat /proc/sys/kernel/random/boot_id)"

# qubesd answers
t0=$SECONDS
until qvm-ls --raw-list >/dev/null 2>&1; do
    if ((SECONDS - t0 > 300)); then break; fi
    sleep 5
done
if qvm-ls --raw-list >/dev/null 2>&1; then item qubesd PASS "answered after $((SECONDS - t0)) s"
else item qubesd FAIL "no answer in 300 s"; fi

for q in "${QUBES[@]}"; do
    t0=$SECONDS
    if err=$(timeout "$START_TIMEOUT" qvm-start --skip-if-running "$q" 2>&1); then
        item "start-$q" PASS "$((SECONDS - t0)) s"
    else
        item "start-$q" FAIL "after $((SECONDS - t0)) s: $(tr '\n' ' ' <<<"$err" | cut -c1-200)"
        continue
    fi
    out=$(timeout "$RUN_TIMEOUT" qvm-run --pass-io --no-gui "$q" \
        'hostname; if [ -f /home/user/bko-seed.txt ]; then sha256sum </home/user/bko-seed.txt | cut -c1-64; else echo none; fi' 2>&1 </dev/null)
    rc=$?
    host=$(sed -n 1p <<<"$out") seed=$(sed -n 2p <<<"$out")
    if [[ $SEEDED == *" $q "* ]]; then
        want=$(seed_text "$q" 2 | sha256sum | cut -c1-64)
    else
        want=none
    fi
    if [[ $rc == 0 && $host == "$q" && $seed == "$want" ]]; then
        item "run-$q" PASS "hostname=$host seed=${seed:0:16}"
    else
        item "run-$q" FAIL "rc=$rc hostname=$host seed=${seed:0:16} want=${want:0:16}: $(tr '\n' ' ' <<<"$out" | cut -c1-200)"
    fi
done

# The qubes, their state, class and virt mode
if list=$(qvm-ls --raw-data --fields NAME,STATE,CLASS,VIRT_MODE,TEMPLATE 2>&1); then
    while read -r l; do info "qvm-ls $l"; done <<<"$list"
    missing=""
    for q in dom0 sys-net "${QUBES[@]}"; do
        grep -q "^$q|Running|" <<<"$list" || missing+=" $q"
    done
    if [[ -z $missing ]]; then item qvm-ls PASS "$(wc -l <<<"$list") qubes, all checked ones running"
    else item qvm-ls FAIL "not running:$missing"; fi
else
    item qvm-ls FAIL "$(tr '\n' ' ' <<<"$list" | cut -c1-200)"
fi

t0=$SECONDS
if err=$(timeout 900 qvm-shutdown --wait --all 2>&1) && [[ $(qvm-ls --running --raw-list 2>/dev/null | grep -vcx dom0) == 0 ]]; then
    item shutdown PASS "all qubes down in $((SECONDS - t0)) s"
else
    item shutdown FAIL "after $((SECONDS - t0)) s: $(qvm-ls --running --raw-list 2>&1 | tr '\n' ' ') $(tr '\n' ' ' <<<"$err" | cut -c1-200)"
fi

# The LVs: name, pool, origin, attributes, size
if lvs=$(lvs --noheadings --separator '|' -o lv_name,pool_lv,origin,lv_attr,lv_size "$VG" 2>&1 | sed 's/^ *//'); then
    while read -r l; do info "lv $l"; done <<<"$lvs"
    origin_of() { awk -F'|' -v n="$1" '$1 == n { print $3 }' <<<"$lvs"; }
    chain=0 lv=vm-work-private
    while [[ -n $lv ]] && ((chain < 20)); do chain=$((chain + 1)); lv=$(origin_of "$lv"); done
    thin=$(awk -F'|' '$2 != ""' <<<"$lvs" | wc -l)
    snaps=$(awk -F'|' '$3 != ""' <<<"$lvs" | wc -l)
    left=$(awk -F'|' '$1 ~ /-snap$/ { print $1 }' <<<"$lvs" | tr '\n' ' ')
    pools=$(awk -F'|' '$1 == "root-pool" || $1 == "vm-pool"' <<<"$lvs" | wc -l)
    detail="thin=$thin with-origin=$snaps work-private-chain=$chain"
    if [[ $pools == 2 && $chain -ge 3 && -z $left ]]; then item lvm PASS "$detail"
    else item lvm FAIL "$detail pools=$pools snap-left=${left:-none}"; fi
else
    item lvm FAIL "$(tr '\n' ' ' <<<"$lvs" | cut -c1-200)"
fi

if ((ok)); then say "BKO-QUBES-CHECK DONE PASS"; exit 0; fi
say "BKO-QUBES-CHECK DONE FAIL"
exit 1
