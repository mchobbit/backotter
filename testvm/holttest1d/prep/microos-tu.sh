# HT_SOURCE_PREP for microos (and aeon later; ht1.sh): the changes
# transactional-update makes in real use, so that the restore has a history
# of slots to keep (docs/design/aeon.md, "Tests > HoltTest1"):
#
#   1. three `transactional-update run` transactions in a chain (the first
#      on the booted slot, then --continue), each changing /usr (files
#      added, rewritten and deleted, as tumbleweed-snapper.sh does) and
#      writing /etc/bko-slot, the slot's own marker, into its /etc; then an
#      edit of the running /etc after the last one opened (/etc/bko-live),
#      which transactional-update-sync-etc-state must carry into it at the
#      next boot;
#   2. a reboot into the last, which health-checker records as good;
#   3. a forced health-checker rollback: a transaction that adds a failing
#      plugin (/usr/libexec/health-checker/bko-fail.sh), a reboot, and
#      health-checker's rollback to the last good slot and its own reboot,
#      which must have happened (GRUB: the journal's "do a rollback"; BLS:
#      "rebooting a different snapshot");
#   4. `transactional-update rollback N` to the first of step 1's slots, and
#      a reboot, so that the source runs a rolled-back default;
#   5. one more transaction, not booted: the dump sees an update pending
#      (default != booted).
#
# It takes several boots, so it is resumable: each run does one step, keeps
# where it is in /var/lib/bko-prep/stage (/var is one subvolume for every
# slot) and ends with the line AGAIN while there is more to do: ht1.sh then
# reboots the source, waits until a boot of it settles (health-checker's
# failed boot reboots by itself, and ht1.sh waits through it), and runs it
# again.  The last run prints no AGAIN (nor REBOOT): the dump runs with the
# update pending.  /var/lib/bko-prep/slots, KEY=NUMBER lines (first, last,
# failed, rolled_back, pending, oldest), stays for ht1.sh's checks of the
# restored machine (ht1-tu.sh).
#
# Run as root in the guest's throwaway overlay.  Offline: nothing is
# downloaded.  GRUB (MicroOS) or BLS (Aeon: /boot/efi/loader/entries, as
# health-checker tells them apart); the BLS steps are written to
# health-checker's BLS path but untested until the aeon guest (A4).
set -eu
S=/var/lib/bko-prep
mkdir -p "$S"
stage=$(cat "$S/stage" 2>/dev/null || echo 0)
loader=grub; [ -d /boot/efi/loader/entries ] && loader=bls
say() { echo "microos-tu: stage $stage: $*"; }
die() { echo "microos-tu: stage $stage: $*" >&2; exit 1; }
put() { sed -i "/^$1=/d" "$S/slots" 2>/dev/null || true; echo "$1=$2" >> "$S/slots"; }
get() { sed -n "s/^$1=//p" "$S/slots" | tail -n 1; }
# The booted slot (the subvolume mounted at /) and the default, by number.
num() { sed -n 's|.*\.snapshots/\([0-9][0-9]*\)/snapshot.*|\1|p'; }
booted() { findmnt -no FSROOT / | num; }
default() { btrfs subvolume get-default / | num; }
hc_last() { sed -n 's/^LAST_WORKING_SNAPSHOT=//p' /var/lib/misc/health-check.state 2>/dev/null | num; }
next() { echo "$stage" > "$S/stage"; }
# transactional-update, its output kept; the new default's number printed.
tu() {
    out=$(transactional-update --non-interactive "$@" 2>&1) || { printf '%s\n' "$out" | tail -n 20 >&2; die "transactional-update $*: failed"; }
    printf '%s\n' "$out" | grep -E 'New default snapshot|Snapshot [0-9]' | sed 's/^/  t-u: /' >&2 || true
    n=$(printf '%s\n' "$out" | sed -n 's|.*New default snapshot is #\([0-9][0-9]*\).*|\1|p' | tail -n 1)
    [ -n "$n" ] || n=$(default)
    echo "$n"
}
# The command each transaction runs in its new snapshot: /usr/share/bko-tu
# changed as a package update would (ROUND 1-4), and /etc/bko-slot naming the
# slot it writes (its number from the root it runs in, else WANT's).
TX='set -e
n=$(findmnt -no FSROOT / 2>/dev/null | sed -n "s|.*\.snapshots/\([0-9][0-9]*\)/snapshot.*|\1|p")
[ -n "$n" ] || n=$WANT
d=/usr/share/bko-tu
rnd() { head -c "$(($2 << 20))" /dev/urandom > "$1"; }
case $ROUND in
1) mkdir -p $d; rnd $d/base.bin 24; for i in $(seq 1 200); do echo "file $i" > $d/s$i; done; ln -sf s1 $d/link ;;
2) rnd $d/pkg-a.bin 8; dd if=/dev/urandom of=$d/base.bin bs=1M count=2 seek=6 conv=notrunc status=none; rm $d/s5; ln -sf s2 $d/link ;;
3) rnd $d/pkg-b.bin 6; echo changed >> $d/s10; rm $d/pkg-a.bin ;;
4) rnd $d/pkg-c.bin 4; echo "changed again" >> $d/s11; rm $d/s6 ;;
esac
echo "slot $n" > /etc/bko-slot
echo "round $ROUND in slot $n" >> /etc/bko-tu-history'
# The slot transactional-update will make next, for TX when it can't tell
# from its root: one past the highest snapshot snapper knows.
want() { snapper --no-dbus --csvout list --columns number | tail -n +2 | sort -n | tail -n 1 | awk '{print $1 + 1}'; }
check_slot_etc() {  # N: slot N's own /etc says it is slot N
    m=$(cat "/.snapshots/$1/snapshot/etc/bko-slot" 2>/dev/null || true)
    [ "$m" = "slot $1" ] || die "slot $1's /etc/bko-slot says '${m:-nothing}'"
}

case $stage in
0)
    command -v transactional-update >/dev/null || die "no transactional-update"
    findmnt -no OPTIONS / | tr , '\n' | grep -qx ro || die "/ isn't read-only: not a transactional system"
    b=$(booted) d=$(default)
    [ -n "$b" ] && [ "$b" = "$d" ] || die "booted slot ${b:-?}, default ${d:-?}: an update is pending already"
    say "loader $loader, booted = default = slot $b"
    put oldest "$(snapper --no-dbus --csvout list --columns number | tail -n +2 | awk '$1 > 0' | sort -n | head -n 1)"
    put base "$b"
    n1=$(tu run env ROUND=1 WANT="$(want)" sh -c "$TX")
    n2=$(tu --continue run env ROUND=2 WANT="$(want)" sh -c "$TX")
    n3=$(tu --continue run env ROUND=3 WANT="$(want)" sh -c "$TX")
    [ "$n1" != "$n2" ] && [ "$n2" != "$n3" ] || die "the transactions made slots $n1 $n2 $n3"
    for n in $n1 $n2 $n3; do check_slot_etc "$n"; done
    [ "$(default)" = "$n3" ] || die "the default is $(default), not the last transaction's $n3"
    put first "$n1"; put last "$n3"
    # An edit of the running /etc after the last snapshot opened: the next
    # boot's transactional-update-sync-etc-state carries it into slot n3's.
    echo "edited in slot $b after slot $n3 was made" > /etc/bko-live
    say "slots $n1 $n2 $n3 made (a chain), each with its /etc/bko-slot; /etc/bko-live edited after; rebooting into $n3"
    stage=1; next; echo AGAIN ;;
1)
    last=$(get last) b=$(booted) d=$(default)
    [ "$b" = "$last" ] && [ "$d" = "$last" ] || die "after the reboot: booted ${b:-?}, default ${d:-?}, not slot $last"
    check_slot_etc "$last"
    grep -q "after slot $last was made" /etc/bko-live 2>/dev/null || die "slot $last's /etc lacks the edit made after it opened (/etc/bko-live: $(cat /etc/bko-live 2>/dev/null || echo none)): the /etc sync didn't carry it"
    if [ $loader = grub ]; then
        [ "$(hc_last)" = "$last" ] || die "health-checker's last working slot is $(hc_last), not $last"
    fi
    say "booted slot $last: health-checker recorded it, /etc/bko-live synced"
    # A transaction that makes the next boot fail health-checker.
    nf=$(tu run sh -c 'set -e; n=$(findmnt -no FSROOT / | sed -n "s|.*\.snapshots/\([0-9][0-9]*\)/snapshot.*|\1|p"); [ -n "$n" ] || n=$WANT
        mkdir -p /usr/libexec/health-checker
        printf "%s\n" "#!/bin/sh" "# bko-holt HoltTest1 (prep/microos-tu.sh): fail every check, so health-checker rolls this slot back" "case \"\$1\" in check) echo \"bko-fail: failing on purpose\"; exit 1 ;; esac; exit 0" > /usr/libexec/health-checker/bko-fail.sh
        chmod 755 /usr/libexec/health-checker/bko-fail.sh
        echo "slot $n" > /etc/bko-slot; echo "failing plugin in slot $n" >> /etc/bko-tu-history')
    [ "$nf" != "$last" ] || die "the failing transaction made no new slot"
    put failed "$nf"
    say "slot $nf has a failing health-checker plugin; rebooting into it (health-checker must roll back to $last and reboot)"
    stage=2; next; echo AGAIN ;;
2)
    last=$(get last) nf=$(get failed) b=$(booted) d=$(default)
    [ "$b" = "$last" ] && [ "$d" = "$last" ] || die "after the failing boot: booted ${b:-?}, default ${d:-?}, not slot $last (health-checker's rollback from $nf)"
    if [ $loader = grub ]; then
        journalctl --no-pager -o cat -t health-checker -b -1 2>/dev/null | grep -q "do a rollback" ||
            die "the boot before this one has no health-checker \"do a rollback\" (journalctl -b -1 -t health-checker: $(journalctl --no-pager -o cat -t health-checker -b -1 2>&1 | tail -n 3 | tr '\n' ' '))"
        [ "$(hc_last)" = "$last" ] || die "health-checker's last working slot is $(hc_last), not $last"
    else
        journalctl --no-pager -o cat -t health-checker 2>/dev/null | grep -q "rebooting a different snapshot" ||
            die "no health-checker \"rebooting a different snapshot\" in the journal"
    fi
    say "health-checker rolled the failing slot $nf back to $last and rebooted: $(journalctl --no-pager -o cat -t health-checker -b -1 2>/dev/null | grep -E 'rollback|failed' | head -n 2 | tr '\n' ' ')"
    # A rollback by hand to the first slot of the chain.
    to=$(get first)
    transactional-update --non-interactive rollback "$to" 2>&1 | tail -n 3 | sed 's/^/  t-u: /' >&2
    [ "$(default)" = "$to" ] || die "transactional-update rollback $to: the default is $(default)"
    put rolled_back "$to"
    say "transactional-update rollback $to; rebooting into it"
    stage=3; next; echo AGAIN ;;
3)
    to=$(get rolled_back) b=$(booted) d=$(default)
    [ "$b" = "$to" ] && [ "$d" = "$to" ] || die "after the rollback: booted ${b:-?}, default ${d:-?}, not slot $to"
    check_slot_etc "$to"
    if [ $loader = grub ]; then
        [ "$(hc_last)" = "$to" ] || die "health-checker's last working slot is $(hc_last), not the rolled-back $to"
    fi
    # The pending update: a transaction on the rolled-back slot, not booted.
    np=$(tu run env ROUND=4 WANT="$(want)" sh -c "$TX")
    check_slot_etc "$np"
    [ "$(default)" = "$np" ] && [ "$(booted)" = "$to" ] || die "the pending update: default $(default), booted $(booted)"
    put pending "$np"
    echo "edited in slot $to after slot $np was made" > /etc/bko-live
    stage=done; next
    say "slot $np pending (default), slot $to booted; not rebooted"
    echo "slots: $(tr '\n' ' ' < "$S/slots")"
    snapper --no-dbus list
    btrfs subvolume list -a -p -u -q /
    btrfs subvolume get-default /
    cat /var/lib/misc/health-check.state
    cat /var/lib/misc/transactional-update.state 2>/dev/null || true
    sync ;;
done) die "already prepared (stage done): a fresh overlay of the guest starts at stage 0" ;;
*) die "unknown stage '$stage' in $S/stage" ;;
esac
