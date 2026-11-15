# shellcheck shell=bash
# ht1.sh's checks of a transactional-update source (openSUSE MicroOS, GRUB;
# Aeon, BLS: docs/design/aeon.md, "Tests > HoltTest1", M5 and A4), sourced
# by ht1.sh.
# A source is one when / is mounted read-only and tukit is installed (the
# test bko-holt's os.transactional makes, less its snapper-slot part).  Its
# history comes from prep/microos-tu.sh (ht1.sh's HT_SOURCE_PREP for
# microos* and aeon*), whose /var/lib/bko-prep/slots names the slots: the
# rolled-back one (booted at the dump), the pending update (the default),
# the oldest, the failed one.
#
#   tu_source        the source's baseline (logs/source-tu.txt), before its dump
#   tu_mount_root    the booted slot, for vm-mount's / when an update is pending
#   tu_record_dst M  the restored health-check.state as load left it (M: the
#                    restored disk, mounted read-only)
#   tu_verify        on the restored disk, after its first boot's dump:
#     - that boot: / read-only, booted = default = the source's default (the
#       pending update), health-checker passed, SELinux as the source's, no
#       failed unit, no AVC denial the source's boots hadn't,
#       transactional-update-sync-etc-state ran without error, /etc/bko-slot
#       the slot's own and /etc/bko-live synced into it; health-check.state's
#       ID as load wrote it is the restored ID of its LAST_WORKING_SNAPSHOT;
#     - the older slots: the rolled-back one and the oldest the guest's SSH
#       reaches (combustion's), each booted alone
#       (a fresh --snapshot QEMU) through GRUB's "Start bootloader from a
#       read-only snapshot" submenu on the serial console (grub-menu.py), each
#       running with its own /etc (the source's /etc/bko-slot for it) and
#       nothing written (the default and health-check.state unchanged); the
#       oldest of all (the image's own slot 1, before sshd) on the console,
#       to its login prompt;
#       BLS (aeon): each booted alone by `bootctl set-oneshot ENTRY` over
#       SSH and a reboot (tu_boot_slot_bls), the slot's entry found by its
#       rootflags=subvol= with its boot counter taken off;
#     - (aeon, a --guest-tpm guest) the re-seal: the first restored boot must
#       have asked for the key (a new machine's TPM: the kept header's TPM2
#       token is the source TPM's) and been given the recovery key by
#       console-boot.py; then `sdbootutil enroll --method=tpm2` over SSH
#       (the key in sdbootutil's keyring, as tik does it), a reboot, and
#       that boot must unlock by the TPM with no prompt (tu_reseal);
#     - health-checker's rollback: the slot health-check.state names booted
#       from the submenu (so no boot of the default saves another state
#       first), a transaction there adding a failing plugin, a reboot: health-
#       checker must roll back to that slot, by the ID load wrote, and boot
#       it (the default afterwards = that slot's restored ID, its path = the
#       source's LAST_WORKING_SNAPSHOT).  BLS (aeon): no state (its path
#       keeps no IDs); the failing transaction made on the restored default,
#       a reboot into it: health-checker must mark it bad and systemd-boot
#       boot the slot before it again, as booted = default
#       (tu_rollback_bls).
# Every boot is of a throwaway overlay of the restored disk (--snapshot), as
# ht1.sh's own.  A silent health-checker rollback on the first boot would
# look like a pass, so booted = default = the source's default is required.

# What a boot of the guest shows: KEY VALUE lines.  booted and default are
# slot numbers (from / and btrfs's default subvolume), etc each slot's
# /etc/bko-slot (read through /.snapshots), menu the snapshot submenu's
# slots in its order (/.snapshots/grub-snapshot.cfg's, those with a
# grub-snapshot.cfg and a grub.cfg in the snapshot, as the submenu tests).
TU_STATE='num() { sed -n "s|.*\.snapshots/\([0-9][0-9]*\)/snapshot.*|\1|p"; }
echo "booted $(findmnt -no FSROOT / | num)"
echo "default $(btrfs subvolume get-default / | num)"
echo "default-id $(btrfs subvolume get-default / | awk "{print \$2}")"
echo "ro $(findmnt -no OPTIONS / | tr , "\n" | grep -qx ro && echo yes || echo no)"
echo "selinux $(getenforce 2>/dev/null || echo none)"
echo "loader $([ -d /boot/efi/loader/entries ] && echo bls || echo grub)"
[ -d /boot/efi/loader/entries ] && echo "bls-entry $(bootctl status 2>/dev/null | sed -n "s/^ *Current Entry: //p")"
echo "bko-slot $(cat /etc/bko-slot 2>/dev/null || echo none)"
echo "bko-live $(cat /etc/bko-live 2>/dev/null || echo none)"
sed -n "s/^LAST_WORKING_BTRFS_ID=/hc-id /p; s/^LAST_WORKING_SNAPSHOT=/hc-path /p" /var/lib/misc/health-check.state 2>/dev/null
sed "s/^/prep /" /var/lib/bko-prep/slots 2>/dev/null
for d in /.snapshots/[0-9]*/snapshot; do n=${d#/.snapshots/}; n=${n%/snapshot}; echo "etc $n $(cat "$d/etc/bko-slot" 2>/dev/null || echo none)"; done | sort -n -k2
for n in $(sed -n "s|.*source \"/\.snapshots/\([0-9][0-9]*\)/grub-snapshot\.cfg\".*|\1|p" /.snapshots/grub-snapshot.cfg 2>/dev/null); do
    [ -f /.snapshots/$n/grub-snapshot.cfg ] && [ -f /.snapshots/$n/snapshot/boot/grub2/grub.cfg ] && echo "menu $n"
done
btrfs subvolume list -a / | sed -n "s|^ID \([0-9]*\) .* path \(<FS_TREE>/\)\{0,1\}\(@/\.snapshots/[0-9]*/snapshot\)$|id \3 \1|p"'
# This boot's health-checker, the /etc sync and SELinux's denials.
TU_BOOT='echo "hc-active $(systemctl is-active health-checker 2>&1)"
journalctl -b -o cat -t health-checker --no-pager 2>/dev/null | sed "s/^/hc-log /"
journalctl -b -o cat --no-pager -u transactional-update-sync-etc-state.service 2>/dev/null | sed "s/^/sync /"
journalctl -b -o cat --no-pager 2>/dev/null | grep -E "transactional-update-sync-etc-state\.service: (Deactivated successfully|Failed|Main process exited)" | sed "s/^/sync-unit /"
systemctl --failed --no-legend --plain 2>/dev/null | awk "{print \"failed \" \$1}"
journalctl -b -o cat --no-pager 2>/dev/null | grep -o "avc:  denied .*" | sed -E "s/ (pid|ino|dev|name|path)=[^ ]*//g; s/^/avc /" | sort -u
journalctl -b -o cat --no-pager _COMM=systemd-cryptse 2>/dev/null | grep -q "TPM2 token unlocks" && echo "unlock tpm2"'
tu_get() { sed -n "s/^$1 //p" "$2" | head -n 1; }   # KEY FILE
tu_is() { [[ -n $(sshk "$gkey" 'command -v tukit >/dev/null && findmnt -no OPTIONS / | tr , "\n" | grep -qx ro && echo tu') ]]; }

# The source's baseline (it is up, prepared, before its dump).  Exit 1 when
# the run can go no further.
tu_source() {
    local L=$R/logs
    sshk "$gkey" "$TU_STATE" > "$L/source-tu.txt" 2>&1
    sshk "$gkey" "timeout 300 systemctl is-system-running --wait >/dev/null; $TU_BOOT" > "$L/source-tu-boot.txt" 2>&1
    sshk "$gkey" 'cat /var/lib/misc/health-check.state' > "$L/source-hcstate.txt" 2>&1
    local b d p
    b=$(tu_get booted "$L/source-tu.txt") d=$(tu_get default "$L/source-tu.txt")
    p=$(sed -n 's/^prep pending=//p' "$L/source-tu.txt")
    [[ $(tu_get ro "$L/source-tu.txt") == yes ]] || { bad "transactional: the source's / isn't read-only (logs/source-tu.txt)"; return 1; }
    if [[ -z $p ]]; then
        bad "transactional: the source has no /var/lib/bko-prep/slots (prep/microos-tu.sh, HT_SOURCE_PREP, didn't run)"; return 1
    fi
    if [[ -z $b || -z $d || $b == "$d" || $d != "$p" ]]; then
        bad "transactional: the source boots slot ${b:-?} with default ${d:-?}, not an update pending in slot $p (logs/source-tu.txt)"; return 1
    fi
    # A guest whose disk's key is sealed to its TPM (aeon): the source's boots
    # on the copy of it (gvars) unlock by it, nothing typed.
    if [[ -n ${GTPM:-} ]] && ! grep -qx 'unlock tpm2' "$L/source-tu-boot.txt"; then
        bad "transactional: the source's root wasn't unlocked by its TPM (a copy of ${GTPM##*/}): $(grep -ci 'enter passphrase' "$R/logs/source-serial.log") passphrase prompt(s) on its console (logs/source-tu-boot.txt)"; return 1
    fi
    ok "transactional: the source boots slot $b ($(tu_get loader "$L/source-tu.txt"), SELinux $(tu_get selinux "$L/source-tu.txt")) with slot $d pending; health-checker's last good $(tu_get hc-path "$L/source-tu.txt") (ID $(tu_get hc-id "$L/source-tu.txt")); prep: $(sed -n 's/^prep //p' "$L/source-tu.txt" | tr '\n' ' ')"
}

# The subvolume a mount of the source's or the restored disk must take for
# / (vm-mount's VM_MOUNT_BTRFS_ROOT): the slot the source booted, when an
# update was pending at its dump.  Offline, the btrfs default is the pending
# slot, where the live system (and a backup made on it, as a user's is) had
# the booted one at /.  Nothing when no update was pending.
tu_mount_root() {
    python3 -I -c 'import json, sys
t = ((json.load(open(sys.argv[1])).get("boot") or {}).get("transactional") or {})
if t.get("pending_update") and (t.get("booted") or {}).get("path"):
    print(t["booted"]["path"])' "$R/dumps/src.json" 2>/dev/null
}

tu_record_dst() {  # the restored disk's health-check.state, as load wrote it
    [[ -f $R/logs/source-tu.txt ]] || return 0
    cat "$1/var/lib/misc/health-check.state" > "$R/logs/restored-hcstate-load.txt" 2>&1 || true
}

# A boot's checks against what it must be: WHO (a log name), the slot it
# must boot, whether that is the default.  Prints the problems, one a line.
tu_boot_problems() {
    local who=$1 want=$2 isdefault=$3 f g S=$R/logs/source-tu.txt b d
    f=$R/logs/$who-tu.txt g=$R/logs/$who-tu-boot.txt
    b=$(tu_get booted "$f") d=$(tu_get default "$f")
    [[ $b == "$want" ]] || echo "booted slot ${b:-?}, not $want"
    if [[ $isdefault == 1 ]]; then [[ $d == "$want" ]] || echo "the default is slot ${d:-?}, not $want"
    else [[ $d == "$(tu_get default "$S")" ]] || echo "the default moved to ${d:-?} (the source's is $(tu_get default "$S"))"; fi
    [[ $(tu_get ro "$f") == yes ]] || echo "/ isn't read-only"
    [[ $(tu_get selinux "$f") == "$(tu_get selinux "$S")" ]] || echo "SELinux $(tu_get selinux "$f"), the source's $(tu_get selinux "$S")"
    local m; m=$(awk -v n="$want" '$1 == "etc" && $2 == n {$1 = $2 = ""; sub(/^ +/, ""); print}' "$S")
    [[ $(tu_get bko-slot "$f") == "${m:-none}" ]] || echo "/etc/bko-slot says '$(tu_get bko-slot "$f")', the source's slot $want's '${m:-none}'"
    [[ $(tu_get hc-active "$g") == active ]] || echo "health-checker $(tu_get hc-active "$g")"
    grep -qx 'hc-log Health check passed' "$g" || echo "health-checker didn't pass: $(sed -n 's/^hc-log //p' "$g" | tail -n 2 | tr '\n' ' ')"
    ! grep -qE '^hc-log .*(failed|rollback|didn.t come up)' "$g" || echo "health-checker: $(grep -E '^hc-log .*(failed|rollback|didn.t come up)' "$g" | head -n 2 | cut -c8- | tr '\n' ' ')"
    ! grep -q '^failed ' "$g" || echo "failed units: $(sed -n 's/^failed //p' "$g" | tr '\n' ' ')"
    ! grep -q '^sync-unit .*\(Failed\|exited\)' "$g" || echo "transactional-update-sync-etc-state: $(grep '^sync-unit' "$g" | cut -c11- | tr '\n' ' ')"
    local avc; avc=$(comm -13 <(grep '^avc ' "$R/logs/source-tu-boot.txt" | sort -u) <(grep '^avc ' "$g" | sort -u))
    [[ -z $avc ]] || echo "AVC denials the source's boot hadn't: $(head -n 3 <<<"$avc" | cut -c5- | tr '\n' ';')"
}

# One slot booted alone through GRUB's snapshot submenu: SLOT, WHO, then
# CMD to run in it before its checks (optional).  Its place in the submenu
# is the restored first boot's menu lines (logs/restored-tu.txt).
tu_boot_slot() {
    local slot=$1 who=$2 idx
    idx=$(awk -v n="$slot" '$1 == "menu" { if ($2 == n) { print i; exit } i++ }' "$R/logs/restored-tu.txt")
    [[ -n $idx ]] || { echo "slot $slot isn't in the restored snapshot submenu ($(sed -n 's/^menu //p' "$R/logs/restored-tu.txt" | tr '\n' ' '))"; return 1; }
    rm -f "$R/logs/$who-serial.log"
    GRUB_MENU="pick:^Start bootloader from a read-only snapshot\$
down:$idx
wait:bootable snapshot #$slot\\b" vm_up "${dst[0]}" "$R/logs/$who-serial.log" --snapshot "${fw[@]}" "${dvfw[@]}" "${extra_dst[@]}"
    vm_wait "$gkey" || { echo "slot $slot doesn't boot from GRUB's snapshot submenu (no SSH; logs/$who-serial.log)"; return 1; }
    grep -q 'grub-menu.py: all steps done' "$R/logs/$who-serial.log" ||
        { echo "GRUB's snapshot submenu: $(grep -o 'grub-menu.py: [^]]*' "$R/logs/$who-serial.log" | tail -n 1) (logs/$who-serial.log)"; return 1; }
    sshk "$gkey" "$TU_STATE" > "$R/logs/$who-tu.txt" 2>&1
    sshk "$gkey" "timeout 300 systemctl is-system-running --wait >/dev/null; $TU_BOOT" > "$R/logs/$who-tu-boot.txt" 2>&1
}
# A slot with no SSH of the guest's (MicroOS's slot 1, the image's own root,
# from before combustion enabled sshd and set the key): booted alone from
# the submenu, it must reach its getty's login prompt on the serial console,
# with the host name its own /etc gives (none there: localhost); then its
# QEMU is stopped.  SLOT WHO.  Prints the problems.
tu_boot_slot_console() {
    local slot=$1 who=$2 idx log=$R/logs/$2-serial.log end
    idx=$(awk -v n="$slot" '$1 == "menu" { if ($2 == n) { print i; exit } i++ }' "$R/logs/restored-tu.txt")
    [[ -n $idx ]] || { echo "slot $slot isn't in the restored snapshot submenu"; return 1; }
    rm -f "$log"   # (a rerun's: its login prompt isn't this boot's)
    GRUB_MENU="pick:^Start bootloader from a read-only snapshot\$
down:$idx" vm_up "${dst[0]}" "$log" --snapshot "${fw[@]}" "${dvfw[@]}" "${extra_dst[@]}"
    end=$((SECONDS + 400))
    while ((SECONDS < end)); do
        sed -n '/grub-menu.py: step 2 /,$p' "$log" 2>/dev/null | tr -d '\r' | grep -aqE '^[^ ]+ login: ' && break
        sleep 2
    done
    sed -n '/grub-menu.py: step 2 /,$p' "$log" 2>/dev/null | tr -d '\r' | grep -aoE '^Welcome to .*|^[^ ]+ login: |rootflags=subvol=[^ ]*' | sed "s/^/console /" > "$R/logs/$who-tu.txt"
    # (No SSH to power it off with: its QEMU, ht_qemus's, is stopped.)
    # shellcheck disable=SC2046  # PIDs
    kill $(ht_qemus "$R") 2>/dev/null; sleep 3
    # shellcheck disable=SC2046
    kill -9 $(ht_qemus "$R") 2>/dev/null
    grep -q 'grub-menu.py: all steps done' "$log" || echo "GRUB's snapshot submenu: $(grep -ao 'grub-menu.py: [^]]*' "$log" | tail -n 1)"
    grep -q '^console .* login: ' "$R/logs/$who-tu.txt" || echo "no login prompt on the console in 400 s (logs/${log##*/})"
    return 0
}
# The BLS entry ID (its file name without the boot counter, as bootctl
# names it) of slot $n: the first entry whose rootflags=subvol= is the slot.
TU_BLS_ENTRY='for f in /boot/efi/loader/entries/*.conf; do
    grep -Eq "rootflags=([^ ]*,)?subvol=/?@/\.snapshots/$n/snapshot( |,|\$)" "$f" || continue
    b=${f##*/}; b=${b%.conf}; echo "${b%%+*}.conf"; break
done'
# One slot booted alone on a BLS guest (aeon): SLOT WHO.  A fresh --snapshot
# QEMU of the restored disk boots its default (the recovery key typed at its
# prompts: CONSOLE_KEY), `bootctl set-oneshot` names the slot's entry
# (LoaderEntryOneShot, in the QEMU's NVRAM), and a reboot boots it.  Prints
# the problems; the slot's state in logs/WHO-tu.txt, as tu_boot_slot's.
tu_boot_slot_bls() {
    local slot=$1 who=$2 id
    rm -f "$R/logs/$who-serial.log"
    CONSOLE_KEY=$ckey vm_up "${dst[0]}" "$R/logs/$who-serial.log" --snapshot "${fw[@]}" "${dvfw[@]}" "${extra_dst[@]}"
    vm_wait "$gkey" || { echo "the restored disk doesn't boot before slot $slot's oneshot (no SSH; logs/$who-serial.log)"; return 1; }
    id=$(sshk "$gkey" "n=$slot; $TU_BLS_ENTRY")
    [[ -n $id ]] || { echo "no BLS entry of slot $slot on the restored ESP"; return 1; }
    sshk "$gkey" "bootctl set-oneshot '$id'" > "$R/logs/$who-oneshot.txt" 2>&1 || { echo "bootctl set-oneshot $id: $(tail -n 1 "$R/logs/$who-oneshot.txt")"; return 1; }
    vm_reboot "$gkey" || { echo "no boot after bootctl set-oneshot $id (logs/$who-serial.log)"; return 1; }
    sshk "$gkey" "$TU_STATE" > "$R/logs/$who-tu.txt" 2>&1
    sshk "$gkey" "timeout 300 systemctl is-system-running --wait >/dev/null; $TU_BOOT" > "$R/logs/$who-tu-boot.txt" 2>&1
    [[ $(tu_get bls-entry "$R/logs/$who-tu.txt") == "$id" ]] || echo "systemd-boot booted $(tu_get bls-entry "$R/logs/$who-tu.txt"), not the oneshot $id"
}

# The re-seal of a --guest-tpm guest (aeon), on the restored first boot (up,
# its checks done): it must have asked for the key on its console (the kept
# header's TPM2 token is sealed to the source's TPM, this a new machine's)
# and been unlocked by the recovery key console-boot.py typed.  Then
# `sdbootutil unenroll --method=tpm2` and `enroll --method=tpm2` over SSH,
# the key handed to sdbootutil in its keyring (keyctl add user sdbootutil,
# as tik's 15-encrypt does), read from the share's passphrase file (the
# recovery key, as ht1.sh's backup phase wrote it) on
# stdin, never on a command line or in a log; and a reboot of the same QEMU
# (its overlay keeps the new token), which must unlock by the TPM with no
# prompt on the console.
tu_reseal() {
    [[ -n ${GTPM:-} ]] || return 0
    local L=$R/logs log=$R/logs/restored-serial.log n mark
    n=$(grep -aci 'enter passphrase' "$log")
    if grep -qx 'unlock tpm2' "$L/restored-tu-boot.txt" || ((n == 0)); then
        bad "transactional: the restored first boot, on a new machine's TPM, didn't ask for its key ($n prompts; logs/restored-serial.log, restored-tu-boot.txt): the source's TPM2 token can't unseal there"; return 1
    fi
    ok "transactional: the restored first boot (a new machine's TPM) asked for the key ($n prompt(s)) and unlocked by the recovery key the kept LUKS2 header still has, typed on the console"
    TMO=600 sshk "$gkey" 'read -r k; keyctl add user sdbootutil "$k" @u >/dev/null; k=; sdbootutil unenroll --method=tpm2; sdbootutil enroll --method=tpm2; r=$?; keyctl purge user sdbootutil >/dev/null 2>&1; systemd-cryptenroll "$(blkid -t TYPE=crypto_LUKS -o device | head -n 1)"; exit $r' < "$R/share/passphrase" > "$L/restored-reseal.txt" 2>&1
    local rc=$?
    if ((rc != 0)) || ! grep -q ' tpm2$' "$L/restored-reseal.txt"; then
        bad "transactional: sdbootutil enroll --method=tpm2 on the restored machine (exit $rc): $(tail -n 3 "$L/restored-reseal.txt" | tr '\n' ' ') (logs/restored-reseal.txt)"; return 1
    fi
    mark=$(wc -c < "$log")
    if ! vm_reboot "$gkey"; then bad "transactional: no boot after the re-seal (logs/restored-serial.log)"; return 1; fi
    sshk "$gkey" "timeout 300 systemctl is-system-running --wait >/dev/null; $TU_BOOT" > "$L/restored-resealed-boot.txt" 2>&1
    n=$(tail -c +$((mark + 1)) "$log" | grep -aci 'enter passphrase')
    if grep -qx 'unlock tpm2' "$L/restored-resealed-boot.txt" && ((n == 0)); then
        ok "transactional: re-sealed to this machine's TPM (sdbootutil enroll --method=tpm2), the next boot unlocked by it with nothing typed"
    else
        bad "transactional: after the re-seal the boot $(grep -qx 'unlock tpm2' "$L/restored-resealed-boot.txt" && echo "unlocked by the TPM" || echo "didn't unlock by the TPM") with $n prompt(s) on the console (logs/restored-resealed-boot.txt)"
    fi
}

# health-checker's rollback on a BLS restored disk (aeon): a fresh QEMU of
# it (the key typed), a failing transaction on its default PEND (TU_FAIL), a
# reboot into the new slot, where health-checker must fail, mark its entry
# bad and reboot; systemd-boot then boots PEND again, which must be booted
# and the default.  WHO.  Prints the problems.
tu_rollback_bls() {
    local pend=$1 who=$2 L=$R/logs nf
    rm -f "$L/$who-serial.log"
    CONSOLE_KEY=$ckey vm_up "${dst[0]}" "$L/$who-serial.log" --snapshot "${fw[@]}" "${dvfw[@]}" "${extra_dst[@]}"
    vm_wait "$gkey" || { echo "the restored disk doesn't boot (no SSH; logs/$who-serial.log)"; return 1; }
    sshk "$gkey" "$TU_STATE" > "$L/$who-tu.txt" 2>&1
    [[ $(tu_get booted "$L/$who-tu.txt") == "$pend" ]] || { echo "the fresh boot booted slot $(tu_get booted "$L/$who-tu.txt"), not the default $pend"; return 1; }
    TMO=600 sshk "$gkey" "$TU_FAIL" > "$L/$who-fail.txt" 2>&1
    nf=$(sed -n 's/^new //p' "$L/$who-fail.txt")
    [[ -n $nf ]] && grep -qx plugin-in-default "$L/$who-fail.txt" || { echo "the failing transaction on slot $pend: $(tail -n 3 "$L/$who-fail.txt" | tr '\n' ' ')"; return 1; }
    vm_reboot "$gkey" && vm_settle "$gkey" || { echo "no settled boot after the failing slot $nf (logs/$who-serial.log)"; return 1; }
    sshk "$gkey" "$TU_STATE" > "$L/$who-after-tu.txt" 2>&1
    sshk "$gkey" "journalctl --no-pager -o cat -t health-checker; echo '== entries'; ls /boot/efi/loader/entries; echo '== boots'; journalctl --no-pager --list-boots | tail -n 4" > "$L/$who-after-hc.txt" 2>&1
    local a=$L/$who-after-tu.txt
    [[ $(tu_get booted "$a") == "$pend" && $(tu_get default "$a") == "$pend" ]] || echo "booted slot $(tu_get booted "$a") with default $(tu_get default "$a"), not slot $pend"
    grep -q "rebooting a different snapshot" "$L/$who-after-hc.txt" || echo "no health-checker \"rebooting a different snapshot\" in the journal"
    echo "$nf" > "$L/$who-failed-slot.txt"
}

# Wait until a boot of the guest settles: running or degraded, and the same
# boot a few seconds later (a boot health-checker fails reboots itself).
vm_settle() { local key=$1 a b st end=$((SECONDS + 900))
    while ((SECONDS < end)); do
        a=$(TMO=20 sshk "$key" cat /proc/sys/kernel/random/boot_id 2>/dev/null)
        if [[ -n $a ]]; then
            st=$(TMO=320 sshk "$key" 'timeout 300 systemctl is-system-running --wait' 2>/dev/null)
            sleep 3
            b=$(TMO=20 sshk "$key" cat /proc/sys/kernel/random/boot_id 2>/dev/null)
            [[ $a == "$b" && ( $st == running || $st == degraded ) ]] && return 0
        fi
        sleep 2
    done
    return 1
}

# The failing transaction tu_verify's rollback check makes (as the prep's).
TU_FAIL='set -e
out=$(transactional-update --non-interactive run sh -c "mkdir -p /usr/libexec/health-checker; printf \"%s\\n\" \"#!/bin/sh\" \"case \\\"\\\$1\\\" in check) echo bko-fail: failing on purpose; exit 1 ;; esac; exit 0\" > /usr/libexec/health-checker/bko-fail.sh; chmod 755 /usr/libexec/health-checker/bko-fail.sh" 2>&1) || { echo "$out" | tail -n 5; exit 1; }
echo "$out" | sed -n "s|.*New default snapshot is #\([0-9]*\).*|new \1|p" | tail -n 1
test -x /.snapshots/$(btrfs subvolume get-default / | sed -n "s|.*\.snapshots/\([0-9]*\)/snapshot.*|\1|p")/snapshot/usr/libexec/health-checker/bko-fail.sh && echo plugin-in-default'

tu_verify() {
    local L=$R/logs S=$R/logs/source-tu.txt pend old rb hcp hcs hcn probs id
    pend=$(tu_get default "$S") rb=$(sed -n 's/^prep rolled_back=//p' "$S") old=$(sed -n 's/^prep oldest=//p' "$S")
    hcp=$(tu_get hc-path "$S"); hcs=$(sed -n 's|.*\.snapshots/\([0-9][0-9]*\)/snapshot.*|\1|p' <<<"$hcp")
    # The first restored boot (up, its dump made).
    sshk "$gkey" "$TU_STATE" > "$L/restored-tu.txt" 2>&1
    sshk "$gkey" "timeout 300 systemctl is-system-running --wait >/dev/null; $TU_BOOT" > "$L/restored-tu-boot.txt" 2>&1
    probs=$(tu_boot_problems restored "$pend" 1)
    grep -q "after slot $pend was made" <<<"$(tu_get bko-live "$L/restored-tu.txt")" || probs+=$'\n'"/etc/bko-live lacks the edit made after slot $pend opened: '$(tu_get bko-live "$L/restored-tu.txt")' (the /etc sync didn't carry it)"
    grep -q '^sync .*syncing from old parent' "$L/restored-tu-boot.txt" || probs+=$'\n'"no transactional-update-sync-etc-state sync into the new slot in the journal"
    # health-checker saved the slot it passed: the pending one, by its restored
    # ID (GRUB's path; BLS keeps no state, as on the source).
    [[ $(tu_get loader "$S") == bls ]] || [[ $(tu_get hc-path "$L/restored-tu.txt") == "@/.snapshots/$pend/snapshot" && $(tu_get hc-id "$L/restored-tu.txt") == "$(tu_get default-id "$L/restored-tu.txt")" ]] ||
        probs+=$'\n'"health-check.state after the boot: $(tu_get hc-path "$L/restored-tu.txt") ID $(tu_get hc-id "$L/restored-tu.txt"), not slot $pend's $(tu_get default-id "$L/restored-tu.txt")"
    probs=$(sed '/^$/d' <<<"$probs")
    if [[ -z $probs ]]; then
        ok "transactional: the restored disk boots the source's pending update, slot $pend, as its default: / read-only, SELinux $(tu_get selinux "$L/restored-tu.txt"), health-checker passed (and saved slot $pend, ID $(tu_get default-id "$L/restored-tu.txt")), /etc synced (transactional-update-sync-etc-state), /etc/bko-slot '$(tu_get bko-slot "$L/restored-tu.txt")', no failed unit or new AVC denial"
    else
        bad "transactional: the restored first boot (slot $pend expected): $(tr '\n' ';' <<<"$probs") (logs/restored-tu.txt, restored-tu-boot.txt)"
    fi
    # The ID load wrote: the restored ID of LAST_WORKING_SNAPSHOT's path.
    # (BLS: none on the source, none here.)
    local lid sid rid
    if [[ $(tu_get loader "$S") == bls ]]; then
        if [[ -s $L/restored-hcstate-load.txt ]] && ! grep -q 'No such file' "$L/restored-hcstate-load.txt"; then
            bad "transactional: BLS: a health-check.state on the restored disk, none on the source (logs/restored-hcstate-load.txt)"
        else
            ok "transactional: BLS: no health-check.state on the restored disk, as on the source (health-checker's BLS path keeps no IDs)"
        fi
    else
        lid=$(sed -n 's/^LAST_WORKING_BTRFS_ID=//p' "$L/restored-hcstate-load.txt" 2>/dev/null)
        sid=$(sed -n 's/^LAST_WORKING_BTRFS_ID=//p' "$L/source-hcstate.txt")
        rid=$(awk -v p="$hcp" '$1 == "id" && $2 == p {print $3}' "$L/restored-tu.txt")
        if [[ -n $lid && $lid == "$rid" ]] && diff -q <(grep -v '^LAST_WORKING_BTRFS_ID=' "$L/source-hcstate.txt") <(grep -v '^LAST_WORKING_BTRFS_ID=' "$L/restored-hcstate-load.txt") >/dev/null; then
            ok "transactional: health-check.state as load left it names $hcp by its restored ID $rid (the source's $sid$([[ $sid == "$rid" ]] && echo ', the same here: nothing to rewrite' || echo ', rewritten')), the rest as the source's"
        else
            bad "transactional: health-check.state as load left it: ID ${lid:-none}, where $hcp is ID ${rid:-?} on the restored disk (source $sid) (logs/restored-hcstate-load.txt, source-hcstate.txt)"
        fi
    fi
    tu_reseal
    vm_down "$gkey"
    # The older slots, each booted alone from GRUB's snapshot submenu.
    local loader s who
    loader=$(tu_get loader "$S")
    # The rolled-back slot and the oldest one the guest's SSH reaches (prep's
    # base, combustion's slot), checked over SSH; the oldest of all, older
    # than sshd and the key (the image's slot 1), on the console.
    local base; base=$(sed -n 's/^prep base=//p' "$S")
    for s in $rb $base; do
        who=restored-slot$s
        if [[ $loader == bls ]]; then probs=$(tu_boot_slot_bls "$s" "$who" && tu_boot_problems "$who" "$s" 0)
        else probs=$(tu_boot_slot "$s" "$who" && tu_boot_problems "$who" "$s" 0)
        fi
        # (The submenu boots a slot as it is: health-checker saves nothing
        # where booted isn't the default, so the state is load's still.)
        [[ -n $probs || $(tu_get hc-path "$L/$who-tu.txt") == "$hcp" ]] || probs="health-check.state changed: $(tu_get hc-path "$L/$who-tu.txt"), load left $hcp"
        if [[ -z $probs ]]; then
            ok "transactional: slot $s ($([[ $s == "$rb" ]] && echo "the rolled-back one" || echo "the oldest the guest's SSH reaches")) boots alone $([[ $loader == bls ]] && echo "by bootctl set-oneshot $(tu_get bls-entry "$L/$who-tu.txt")" || echo "from GRUB's snapshot submenu"): running, / read-only, its own /etc ('$(tu_get bko-slot "$L/$who-tu.txt")'), health-checker passed, the default still slot $pend"
        else
            bad "transactional: slot $s $([[ $loader == bls ]] && echo "by bootctl set-oneshot" || echo "from GRUB's snapshot submenu"): $(tr '\n' ';' <<<"$probs") (logs/$who-*)"
        fi
        vm_down "$gkey"
    done
    if [[ -n $old && $old != "$base" && $old != "$rb" && $loader == grub ]]; then
        who=restored-slot$old
        probs=$(tu_boot_slot_console "$old" "$who")
        if [[ -z $probs ]]; then
            ok "transactional: slot $old (the oldest, with no SSH of the guest's) boots alone from GRUB's snapshot submenu to its login prompt: $(sed -n 's/^console //p' "$L/$who-tu.txt" | tr '\n' ' ')"
        else
            bad "transactional: slot $old from GRUB's snapshot submenu, on the console: $(tr '\n' ';' <<<"$probs") (logs/$who-*)"
        fi
    fi
    # health-checker's rollback on the restored machine, by the ID load wrote:
    # the slot health-check.state names, booted from the submenu (a boot of
    # the default would save its own state first), a failing transaction on
    # it, a reboot.
    who=restored-rollback
    if [[ $loader == bls ]]; then
        probs=$(tu_rollback_bls "$pend" "$who")
        if [[ -z $probs ]]; then
            ok "transactional: health-checker on the restored disk marked the failing slot $(cat "$L/$who-failed-slot.txt") bad and systemd-boot booted slot $pend again, booted = default (BLS)"
        else
            bad "transactional: health-checker's BLS rollback: $(tr '\n' ';' <<<"$probs") (logs/$who-*)"
        fi
        vm_down "$gkey"; return
    fi
    probs=$(tu_boot_slot "$hcs" "$who") || { bad "transactional: health-checker's rollback: $probs"; vm_down "$gkey"; return; }
    [[ $(tu_get booted "$L/$who-tu.txt") == "$hcs" ]] || { bad "transactional: health-checker's rollback: the submenu booted slot $(tu_get booted "$L/$who-tu.txt"), not $hcs"; vm_down "$gkey"; return; }
    TMO=600 sshk "$gkey" "$TU_FAIL" > "$L/$who-fail.txt" 2>&1
    local nf; nf=$(sed -n 's/^new //p' "$L/$who-fail.txt")
    if [[ -z $nf ]] || ! grep -qx plugin-in-default "$L/$who-fail.txt"; then
        bad "transactional: health-checker's rollback: the failing transaction on slot $hcs: $(tail -n 3 "$L/$who-fail.txt" | tr '\n' ' ')"; vm_down "$gkey"; return
    fi
    if ! vm_reboot "$gkey" || ! vm_settle "$gkey"; then
        bad "transactional: health-checker's rollback: no settled boot after the failing slot $nf (logs/$who-serial.log)"; vm_down "$gkey"; return
    fi
    sshk "$gkey" "$TU_STATE" > "$L/$who-after-tu.txt" 2>&1
    sshk "$gkey" 'journalctl --no-pager -o cat -t health-checker -b -1; echo "== boots"; journalctl --no-pager --list-boots | tail -n 4' > "$L/$who-after-hc.txt" 2>&1
    local a=$L/$who-after-tu.txt
    probs=""
    [[ $(tu_get booted "$a") == "$hcs" && $(tu_get default "$a") == "$hcs" ]] || probs+="booted slot $(tu_get booted "$a") with default $(tu_get default "$a"), not slot $hcs;"
    [[ $(tu_get default-id "$a") == "$rid" ]] || probs+="the default's ID is $(tu_get default-id "$a"), not slot $hcs's restored $rid;"
    [[ $(tu_get hc-path "$a") == "$hcp" && $(tu_get hc-id "$a") == "$rid" ]] || probs+="health-check.state names $(tu_get hc-path "$a") ID $(tu_get hc-id "$a");"
    grep -q "do a rollback" "$L/$who-after-hc.txt" || probs+="no \"do a rollback\" in the failing boot's health-checker journal;"
    if [[ -z $probs ]]; then
        ok "transactional: health-checker on the restored disk rolled the failing slot $nf back to slot $hcs by the ID load wrote ($rid; the source's $hcp) and booted it"
    else
        bad "transactional: health-checker's rollback from slot $nf: ${probs%;} (logs/$who-*)"
    fi
    vm_down "$gkey"
}
