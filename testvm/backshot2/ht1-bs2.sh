# BackShot2's tiers 1 and 2 inside ht1.sh (docs/design/backshot2.md, "Tiers"):
# sourced by ht1.sh when HT_VARIANT=bs2, it defines the hooks ht1.sh calls.
# A case runs HoltTest1's four phases with these changes:
#
#   source   as ever, then the case's edits are applied to the fresh dump
#            (bs2-host apply): the drifted layout is what load is given
#   backup   the content drift planted into the source's overlay first
#            (read-write, on the host), and for a --snapshot case N backups
#            with seeded changes between; fsmeta records the one restored
#   rebuild  targets of the case's sizes, its load options, and on tier 2
#            one NVRAM (vm-run --uefi-vars) for the rescuer and the restored
#            boot, with no --no-nvram unless the case has it
#   verify   judge.py --expect against the drifted layout, with the flags
#            the case's options call for (bs2-host apply works them out)
#
# Env (bs2-host tier1 sets them): BS2_CASE (the case file), BS2_NAME (its
# name: ht1's GV), BS2_TIER (1 or 2), BS2_HOST (bs2-host's path).
#
# What a case reuses (README "What a case reuses"; bs2lib/reuse.py has the
# rules), each on unless set to 0, falling back to the case's own when it
# can't, with a note: line saying why:
#
#   BS2_REUSE_SOURCE   the source phase takes the source's preparation
#                      (bs2-host source: these phases, run once with
#                      BS2_PREPARE=1): an overlay on its shut-down overlays,
#                      its dump, boot state, lvmid, health and serial log
#   BS2_REUSE_BACKUP   a case whose backup is that tree (no content drift,
#                      one snapshot: bs2-host apply's BS2_SHARE_BACKUP) takes
#                      the preparation's backup, copied into its share
#   BS2_RESCUER_STATE  the rescuer resumes from a state saved once per
#                      rescuer, firmware, QEMU and host (ROOT/rescuer-state/),
#                      the case's targets and decoy hot-plugged after, as
#                      vdb.. as a cold boot names them; its clock set and RNG
#                      reseeded.  Not for the leftovers set (the rescuer must
#                      meet its disk at boot)
#   BS2_FAST_DOWN      once load closed and nothing holds the targets, the
#                      guest syncs and QEMU is told to quit (it flushes and
#                      closes its images), not a guest poweroff waited for;
#                      the restored boot (-snapshot) is quit the same way
#
# BS2_PREPARE=1 (bs2-host source): no case; the source and backup phases as a
# case with no edits, no content and one snapshot runs them.

if [[ -n ${BS2_PREPARE:-} ]]; then
    [[ -n ${BS2_NAME:-} && -n ${BS2_HOST:-} ]] || { echo "ht1: BS2_PREPARE needs BS2_NAME and BS2_HOST" >&2; exit 2; }
    BS2_CASE=""
else
    [[ -n ${BS2_CASE:-} && -n ${BS2_NAME:-} && -n ${BS2_HOST:-} ]] || { echo "ht1: HT_VARIANT=bs2 needs BS2_CASE, BS2_NAME and BS2_HOST" >&2; exit 2; }
fi
BS2_TIER=${BS2_TIER:-1}
GV=$BS2_NAME
# fsmeta.py walks by path, and the content drift's deep tree is past PATH_MAX:
# it leaves srv/bs2/deep* to bs2-host deep, which walks by directory fds.
BS2_FSMETA=(--skip srv/bs2/deep0)
BS2_SNAPS=1 BS2_RESTORE=0 BS2_CONTENT=0 BS2_FLAGS=() BS2_JUDGE=(--expect) BS2_FSJUDGE=() BS2_TARGETS=() BS2_VFW=()
BS2_SHARE_BACKUP=0
# The preparation this case's source phase took (bs2_source_reuse), its backup's
# snapshot path when that serves too, and why not.
BS2_PREP_DIR="" BS2_PREP_NAME="" BS2_PREP_SNAP="" BS2_PREP_WHY="" BS2_PREP_BACKUP_WHY=""
BS2_QMP_PY=$(dirname "$BS2_HOST")/bs2-qmp.py

# After ht1.sh has its firmware options (fw, rfw): tier 2's shared NVRAM.
bs2_setup() {
    if ((BS2_TIER == 2)); then
        ((uefi)) || { echo "ht1: tier 2 is for UEFI sources" >&2; exit 2; }
        rfw=(--uefi --no-secure-boot --uefi-vars "$R/vars.fd")
        BS2_VFW=(--uefi-vars "$R/vars.fd")
        [[ " ${fw[*]} " == *" --no-secure-boot "* ]] || fw+=(--no-secure-boot)
    fi
}

# Start of phase_source: the source's preparation stands in for its boot,
# when it can (bs2-host prepared says).  Exit 1: boot it as ever.
bs2_source_reuse() {
    [[ -z ${BS2_PREPARE:-} ]] || return 1
    [[ ${BS2_REUSE_SOURCE:-1} == 1 ]] || { say "note: source: a boot of its own (BS2_REUSE_SOURCE=${BS2_REUSE_SOURCE})"; return 1; }
    [[ -z ${HT_SOURCE_PREP:-} ]] || { say "note: source: a boot of its own (HT_SOURCE_PREP)"; return 1; }
    local out rc=0 l i f
    out=$(python3 "$BS2_HOST" prepared "$G" --holt "$S/bko-holt" --tier "$BS2_TIER" --rustic-sha "${HT_RUSTIC_SHA:-}" 2>&1) || rc=$?
    # (Only its own assignments, shell-quoted: never a traceback's text.)
    while IFS= read -r l; do [[ $l =~ ^BS2_PREP_[A-Z_]+= ]] && eval "$l"; done <<<"$out"
    if ((rc)) || [[ -z $BS2_PREP_DIR ]]; then
        say "note: source: a boot of its own, not the prepared one (${BS2_PREP_WHY:-$(tail -n 1 <<<"$out")})"
        BS2_PREP_DIR="" BS2_PREP_SNAP=""; return 1
    fi
    for ((i = 0; i < n; i++)); do
        rm -f "${src[i]}"
        qemu-img create -q -f qcow2 -b "$BS2_PREP_DIR/src-$i.qcow2" -F qcow2 "${src[i]}" ||
            { say "note: source: a boot of its own (no overlay on $BS2_PREP_DIR/src-$i.qcow2)"; BS2_PREP_DIR="" BS2_PREP_SNAP=""; return 1; }
    done
    cp "$BS2_PREP_DIR/dumps/src.json" "$R/dumps/src.json" ||
        { say "note: source: a boot of its own (no dump in $BS2_PREP_DIR)"; BS2_PREP_DIR="" BS2_PREP_SNAP=""; return 1; }
    # Its records, as its own boot would have left them (ostree's too, when it has them).
    rm -f "$R/logs/source-ostree"*.txt "$R/logs/source-rollback-bootstate.txt"
    for f in "$BS2_PREP_DIR"/logs/source-*; do [[ ! -f $f ]] || cp "$f" "$R/logs/"; done
    echo "prepared $BS2_PREP_DIR prep.json $(sha256sum <"$BS2_PREP_DIR/prep.json" | cut -c1-64)" >>"$R/logs/manifest.txt"
    ok "the prepared source $BS2_PREP_NAME: its boot, dump, boot state, lvmid and health, made once for its cases ($BS2_PREP_DIR)"
}

# End of phase_source: the drifted layout, and what the case asks for.
bs2_after_source() {
    if [[ -n ${BS2_PREPARE:-} ]]; then
        ok "prepared: the source's boot state, dump and health for its cases (no case of its own)"
        return
    fi
    if python3 "$BS2_HOST" apply "$BS2_CASE" "$R/dumps/src.json" "$R/dumps/drifted.json" --tier "$BS2_TIER" --uefi "$uefi" \
        > "$R/bs2-info.sh" 2> "$R/logs/bs2-apply.err"; then
        # shellcheck disable=SC1091
        . "$R/bs2-info.sh"
        cp "$R/dumps/drifted.json" "$S/layout.json"
        ok "the case's ${BS2_EDITS:-0} edits applied to the fresh dump (targets ${BS2_TARGETS[*]}, options ${BS2_FLAGS[*]:-none})"
    else
        bad "bs2-host apply: $(tail -1 "$R/logs/bs2-apply.err")"
    fi
}

bs2_mount() {  # rw|ro
    local ro=(); [[ $1 == ro ]] && ro=(--ro)
    local key=(); [[ -n $gpass ]] && key=(--key "$gpass")
    (trap '' TERM INT HUP; exec "$T/vm-mount" "${ro[@]}" "${extra_src[@]}" "${key[@]}" "${src[0]}" "$SRC_MP") >> "$R/logs/backup-mount.log" 2>&1 || return 1
    HT_MOUNTED=$SRC_MP
}
bs2_umount() { (trap '' TERM INT HUP; exec "$T/vm-umount" "$SRC_MP") >> "$R/logs/backup-mount.log" 2>&1; HT_MOUNTED=; }

phase_backup_bs2() {
    say "== backup ($BS2_NAME: $BS2_SNAPS snapshot(s), content $BS2_CONTENT)"
    bs2_backup_reuse || bs2_backup_own || return
    cp "$RUSTIC" "$S/rustic-bko"; cp "$BIN"/*.deb "$S/"
    [[ ! -f $BIN/bcachefs ]] || cp "$BIN/bcachefs" "$S/"
    [[ -n $gpass ]] && printf '%s' "$(cat "$gpass")" > "$S/passphrase"
    chmod -R a+rX "$S"
}

# The preparation's backup, for a case that would back up that very tree:
# its source is the prepared one (bs2_source_reuse took it), it plants no
# content and takes one snapshot (BS2_SHARE_BACKUP), and its rustic-bko made
# that backup.  A copy in the case's share (the rescuer mounts it read-only);
# load restores latest:PATH of the preparation's mount point (bs2-snap-path).
bs2_backup_reuse() {
    rm -f "$R/bs2-snap-path"
    [[ -z ${BS2_PREPARE:-} && ${BS2_REUSE_BACKUP:-1} == 1 && $BS2_SHARE_BACKUP == 1 ]] || return 1
    [[ -n $BS2_PREP_DIR ]] || { say "note: backup: one of its own (its source isn't the prepared one)"; return 1; }
    [[ -n $BS2_PREP_SNAP ]] || { say "note: backup: one of its own (${BS2_PREP_BACKUP_WHY:-the preparation's backup doesn't serve})"; return 1; }
    rm -rf "$S/repo"
    if ! { cp -a --reflink=auto "$BS2_PREP_DIR/backup/repo" "$S/repo" && cp "$BS2_PREP_DIR/backup/repo-password" "$S/repo-password" &&
           cp "$BS2_PREP_DIR/src-meta.json" "$R/src-meta.json" && cp "$BS2_PREP_DIR/bs2-snapshots.json" "$R/bs2-snapshots.json"; }; then
        rm -rf "$S/repo" "$R/src-meta.json"
        say "note: backup: one of its own (copying the preparation's failed)"; return 1
    fi
    [[ ! -f $BS2_PREP_DIR/logs/backup.log ]] || cp "$BS2_PREP_DIR/logs/backup.log" "$R/logs/backup.log"
    echo "$BS2_PREP_SNAP" > "$R/bs2-snap-path"
    ok "the prepared backup, this case's tree as it stands ($(du -sh "$S/repo" | cut -f1); load restores latest:$BS2_PREP_SNAP)"
}

# The case's own: content planted, N snapshots with changes between.  Exit 1: failed.
bs2_backup_own() {
    rm -rf "$S/repo"; head -c 24 /dev/urandom | base64 > "$S/repo-password"
    mkdir -p "$SRC_MP"
    local rustic=("$RUSTIC" -r "$S/repo" --password-file "$S/repo-password" --no-progress --no-cache) r
    "${rustic[@]}" init > "$R/logs/backup.log" 2>&1 || { bad "rustic-bko init (logs/backup.log)"; return 1; }
    for ((r = 0; r < BS2_SNAPS; r++)); do
        if [[ $BS2_CONTENT == 1 ]] || ((r > 0)); then
            bs2_mount rw || { bad "mount the source read-write, to plant (logs/backup-mount.log)"; return 1; }
            if ((r == 0)); then
                if python3 "$BS2_HOST" plant "$BS2_CASE" "$SRC_MP" --layout "$R/dumps/drifted.json" > "$R/logs/bs2-plant.log" 2>&1; then
                    ok "content planted: $(tail -1 "$R/logs/bs2-plant.log")"
                else
                    bad "content planting (logs/bs2-plant.log): $(tail -1 "$R/logs/bs2-plant.log")"; bs2_umount; return 1
                fi
            else
                python3 "$BS2_HOST" change "$BS2_CASE" "$SRC_MP" "$r" >> "$R/logs/bs2-plant.log" 2>&1 || { bad "content change $r"; bs2_umount; return 1; }
            fi
            bs2_umount
        fi
        bs2_mount ro || { bad "mount the source read-only"; return 1; }
        if "${rustic[@]}" backup --set-atime yes "$SRC_MP" >> "$R/logs/backup.log" 2>&1; then
            ok "rustic-bko backup $((r + 1)) of $BS2_SNAPS ($(du -sh "$S/repo" | cut -f1))"
        else
            bad "rustic-bko backup $((r + 1)) (logs/backup.log)"; bs2_umount; return 1
        fi
        if ((r == BS2_RESTORE)); then
            python3 "$T/compare/fsmeta.py" record "$SRC_MP" "$R/src-meta.json" "${BS2_FSMETA[@]}" > "$R/logs/fsmeta-src.log" 2>&1 || bad "fsmeta record of the source"
            if [[ $BS2_CONTENT == 1 ]]; then
                python3 "$BS2_HOST" deep record "$SRC_MP" "$R/src-deep.json" > "$R/logs/bs2-deep-src.log" 2>&1 || bad "the deep tree's record of the source (logs/bs2-deep-src.log)"
            fi
        fi
        bs2_umount
    done
    "${rustic[@]}" snapshots --json > "$R/bs2-snapshots.json" 2>> "$R/logs/backup.log"
    return 0
}

# The snapshot load restores: the case's (by its index, oldest first), else ht1's;
# ID:PATH, as ht1's latest:PATH, so the tree lands at the target's root.
bs2_snapshot() {
    if ((BS2_SNAPS > 1)); then
        python3 - "$R/bs2-snapshots.json" "$BS2_RESTORE" "$SRC_MP" <<'PY'
import json, sys
def walk(v, out):
    if isinstance(v, dict):
        if "id" in v and "time" in v:
            out.append((v["time"], v["id"]))
        for x in v.values():
            walk(x, out)
    elif isinstance(v, list):
        for x in v:
            walk(x, out)
out = []
walk(json.load(open(sys.argv[1])), out)
print(sorted(set(out))[int(sys.argv[2])][1] + ":" + sys.argv[3])
PY
    else
        # (The preparation's backup: its mount point's path, bs2_backup_reuse.)
        local p=$SRC_MP; [[ ! -s $R/bs2-snap-path ]] || p=$(<"$R/bs2-snap-path")
        echo "latest:$p"
    fi
}

# In phase_verify, with the restored disk mounted: the deep tree compared.
# Whether the case has one is its content's plan (an "only" may keep none),
# not the source's record being empty; a tree planned but not recorded is a
# check of its own, so the shrinker can't trade one failure for the other.
bs2_after_dst_record() {
    [[ $BS2_CONTENT == 1 && -f $R/src-deep.json ]] || return 0
    local rc=0
    python3 "$BS2_HOST" deep compare "$DST_MP" "$R/src-deep.json" --case "$BS2_CASE" > "$R/logs/bs2-deep.txt" 2>&1 || rc=$?
    case $rc in
        0) ok "the deep tree (past PATH_MAX) agrees: $(tail -1 "$R/logs/bs2-deep.txt")" ;;
        3) say "note: no deep tree compare: $(tail -1 "$R/logs/bs2-deep.txt")" ;;
        4) bad "the source's deep tree is missing: $(tail -1 "$R/logs/bs2-deep.txt"); $(head -2 "$R/logs/bs2-deep.txt" | tr '\n' ' ')" ;;
        *) bad "the deep tree differs: $(head -3 "$R/logs/bs2-deep.txt" | tr '\n' ' ')" ;;
    esac
}

bs2_target_size() {  # I: target disk I's bytes
    echo "${BS2_TARGETS[$1]}"
}

# ---- the leftovers set (bs2lib/leftovers.py; backshot2.md, "Leftovers") ----
# Only when bs2-host leftovers runs the step: BS2_CHAIN_OUT names where this
# step's restored disk goes, for the next step; BS2_CHAIN_IN the disk the
# step before left (none: the set's first step, a blank disk).  Nothing here
# wipes anything: what's on the disk when load starts is what the step
# before (and this step's seed) left.
BS2_SEED=() BS2_EXPECT=()
BS2_PROBE=$(dirname "$BS2_HOST")/leftovers-probe.sh

bs2_target_make() {  # I PATH: target disk I, new or the step before's
    if (($1 == 0)) && [[ -n ${BS2_CHAIN_IN:-} ]]; then
        [[ -f $BS2_CHAIN_IN ]] || { bad "leftovers: no disk $BS2_CHAIN_IN from the step before"; return 1; }
        cp --sparse=always "$BS2_CHAIN_IN" "$2" || { bad "leftovers: copy $BS2_CHAIN_IN"; return 1; }
        local have; have=$(size_of "$2")
        [[ $have == "$(bs2_target_size 0)" ]] || { bad "leftovers: the step before's disk is $have bytes, this step's target $(bs2_target_size 0)"; return 1; }
        say "note: leftovers: the target is the disk the step before left, as it left it"
    else
        qemu-img create -q -f qcow2 "$2" "$(bs2_target_size "$1")"
    fi
}

# In the rescuer, before the refusals and load: this step's seed (a stale
# layer, then a reboot, so the rescuer meets it as a booting machine), and
# what the rescuer made of the disk at boot.
bs2_before_load() {
    [[ -n ${BS2_CHAIN_OUT:-} ]] || return 0
    if ((${#BS2_SEED[@]})); then
        if sshk "$rkey" "sh -s -- seed /dev/vdb ${BS2_SEED[*]}" < "$BS2_PROBE" > "$R/logs/bs2-seed.log" 2>&1; then
            ok "leftovers: seeded ${BS2_SEED[0]} on /dev/vdb at sector ${BS2_SEED[1]} (${BS2_SEED[2]} sectors)"
        else
            bad "leftovers: seeding ${BS2_SEED[0]} (logs/bs2-seed.log): $(tail -1 "$R/logs/bs2-seed.log")"; return 1
        fi
        vm_reboot "$rkey" || { bad "rescuer boots again after the seed (logs/rescuer-serial.log)"; return 1; }
        sshk "$rkey" 'mkdir -p /mnt/share && mount -t 9p -o trans=virtio,version=9p2000.L,ro bkoshare /mnt/share' >> "$R/logs/rescuer-setup.log" 2>&1 \
            || { bad "rescuer setup (the share, after the seed's reboot)"; return 1; }
    fi
    sshk "$rkey" "sh -s -- probe /dev/vdb" < "$BS2_PROBE" > "$R/logs/bs2-leftovers-rescuer.txt" 2>&1
    say "note: leftovers: the rescuer at boot:$(sed -n 's/^SUMMARY//p' "$R/logs/bs2-leftovers-rescuer.txt" | tail -1)"
    # --release: what the rescuer started on the target, stopped (nothing
    # written to it), as a user would after load refuses a busy target.
    if [[ ${BS2_RELEASE:-0} == 1 ]]; then
        sshk "$rkey" "sh -s -- release /dev/vdb" < "$BS2_PROBE" > "$R/logs/bs2-leftovers-release.txt" 2>&1
        say "note: leftovers: --release: $(grep '^RELEASED' "$R/logs/bs2-leftovers-release.txt" | cut -d' ' -f2- | tr '\n' ';' | sed 's/;$//; s/;/; /g')$(sed -n 's/^SUMMARY / (/p' "$R/logs/bs2-leftovers-release.txt" | tail -1))"
    fi
}

# In the rescuer, after load's close: every signature on each new partition
# (wipefs -n), and what blkid -p settles on.  A type other than the layout's,
# or blkid unable to choose, is a problem (a FAIL, given in the verify phase
# so that the restored disk's boot is still tried); any other old signature
# is a note.
bs2_after_load() {
    [[ -n ${BS2_CHAIN_OUT:-} ]] || return 0
    sshk "$rkey" "sh -s -- after /dev/vdb ${BS2_EXPECT[*]}" < "$BS2_PROBE" > "$R/logs/bs2-leftovers-after.txt" 2>&1
    grep -E '^(WRONG|AMBIVALENT) ' "$R/logs/bs2-leftovers-after.txt" > "$R/bs2-leftover-problems.txt" || true
    local stale; stale=$(sed -n 's/^STALE //p' "$R/logs/bs2-leftovers-after.txt" | tr '\n' ';' | sed 's/;$//; s/;/; /g')
    say "note: leftovers: after load's close: ${stale:-no old signature on the new partitions}$( [[ -s $R/bs2-leftover-problems.txt ]] && echo "; problems: $(tr '\n' ';' < "$R/bs2-leftover-problems.txt")")"
}

# After the rescuer is down: the restored disk, as load left it, for the next step.
bs2_chain_save() {
    [[ -n ${BS2_CHAIN_OUT:-} ]] || return 0
    cp --sparse=always "${dst[0]}" "$BS2_CHAIN_OUT.part" && mv -f "$BS2_CHAIN_OUT.part" "$BS2_CHAIN_OUT" \
        || bad "leftovers: keep the restored disk for the next step ($BS2_CHAIN_OUT)"
}

bs2_verify_begin() {
    [[ -s $R/bs2-leftover-problems.txt ]] || return 0
    local l; while IFS= read -r l; do bad "leftovers: after load: $l"; done < "$R/bs2-leftover-problems.txt"
}

# The restored system, booted: what it made of the disk.
bs2_restored_probe() {
    [[ -n ${BS2_CHAIN_OUT:-} ]] || return 0
    sshk "$1" "sh -s -- restored /dev/vda" < "$BS2_PROBE" > "$R/logs/bs2-leftovers-restored.txt" 2>&1
    say "note: leftovers: the restored system at boot:$(sed -n 's/^SUMMARY//p' "$R/logs/bs2-leftovers-restored.txt" | tail -1)"
}

# ---- the rescuer, resumed from a saved state (BS2_RESCUER_STATE) ----
# Once per host per key (bs2-host rescuer-key: the rescuer image, its vm-run
# firmware options, memory, QEMU, OVMF, host kernel and CPU, and the scripts
# that make and resume it), the first case that finds none boots the rescuer
# with no disk of a case's, quietens it (timers stopped: a resume jumps its
# clock, which would fire them mid-load) and saves its RAM and devices with
# QMP's migrate to ROOT/rescuer-state/HASH/state, its disk as an overlay
# beside it and its NVRAM (vars.fd); then every case: an overlay on that
# disk, a copy of that NVRAM, the same QEMU args (the share at the case's
# path: 9p isn't mounted when saved, so migrating it is allowed), -incoming
# the state, cont; each target then the decoy hot-plugged one at a time (a PC
# machine's ACPI slot, or q35's PCIe root ports made at the save), each
# waited for, so the kernel names them vdb.. in order as a cold boot does,
# and the names and sizes checked against a cold boot's; the clock set from
# the host's, and the RNG fed the host's bytes and reseeded (vmgenid also
# tells the kernel it is a copy).  Anything short of that: the resumed QEMU
# goes, the targets and decoy are made anew, and the rescuer boots cold.
BS2_RS_ROOT=${BS2_ROOT:-/bkoscratch/backshot2}/rescuer-state
BS2_RS_PORTS=6     # q35: PCIe root ports for hot-plugged disks (targets and the decoy)
BS2_RS_MEM=3072    # (phase_rebuild's --memory)
BS2_QMP="" BS2_RS_WHY=""
BS2_RS_QUIET='timeout 120 systemctl is-system-running --wait >/dev/null 2>&1
t=$(systemctl list-units --type=timer --state=active --plain --no-legend 2>/dev/null | awk "{print \$1}")
if [ -n "$t" ]; then
    systemctl stop $t
    u=$(for x in $t; do systemctl show -p Unit --value "$x"; done)
    [ -z "$u" ] || systemctl stop $u 2>/dev/null
fi
udevadm settle -t 60; sync
echo "quiet: timers stopped: $(echo $t)"'
# (Run as "T=SECONDS; $BS2_RS_FRESH" with 64 of the host's random bytes on stdin.
# RNDRESEEDCRNG is _IO('R', 0x07).)
BS2_RS_FRESH='cat >/dev/urandom && perl -e '"'"'open(my $f, ">", "/dev/urandom") or die "urandom: $!\n"; ioctl($f, 0x5207, 0) or die "RNDRESEEDCRNG: $!\n"'"'"' && date -u -s "@$T" >/dev/null && echo "clock $(date -u +%FT%TZ), RNG reseeded"'

bs2_rs_uefi() { [[ " ${rfw[*]} " == *" --uefi "* ]]; }
# QEMU's args besides vm-run's, the same at the save and every resume but for
# the share's and the QMP socket's paths (SHARE and QMP in the key).
bs2_rs_extra() {  # SHARE QMP
    local a=(-virtfs "local,path=$1,mount_tag=bkoshare,security_model=none,readonly=on" -device vmgenid,guid=auto) i
    if bs2_rs_uefi; then
        for ((i = 0; i < BS2_RS_PORTS; i++)); do
            a+=(-device "pcie-root-port,id=bs2rp$i,bus=pcie.0,chassis=$((40 + i)),addr=$(printf '0x%x' $((16 + i))),io-reserve=0")
        done
    fi
    a+=(-qmp "unix:$2,server=on,wait=off")
    printf '%s\n' "${a[@]}"
}
# rfw with VARS as its NVRAM (a UEFI rescuer's: the state's, or the case's copy).
bs2_rs_rfw() {  # VARS
    local a=() i
    for ((i = 0; i < ${#rfw[@]}; i++)); do
        [[ ${rfw[i]} == --uefi-vars ]] && { i=$((i + 1)); continue; }
        a+=("${rfw[i]}")
    done
    bs2_rs_uefi && a+=(--uefi-vars "$1")
    ((${#a[@]} == 0)) || printf '%s\n' "${a[@]}"
}
# The case's rescuer NVRAM: tier 2's (rfw's --uefi-vars, which the restored boot keeps), else its own.
bs2_rs_vars() {
    local i v=$R/rescuer-vars.fd
    for ((i = 0; i < ${#rfw[@]}; i++)); do [[ ${rfw[i]} != --uefi-vars ]] || v=${rfw[i + 1]}; done
    echo "$v"
}
# SSH answers within SECS (exit 0), or not, or the run's QEMU went (after its
# first 5 s, which vm-run takes to start it): exit 1 at once, not at the deadline.
bs2_rs_wait() {  # SECS
    local end=$((SECONDS + $1)) t0=$SECONDS
    while ((SECONDS < end)); do
        TMO=8 sshk "$rkey" true 2>/dev/null && return 0
        ((SECONDS - t0 < 5)) || [[ -n $(ht_qemus "$R") ]] || return 1
        sleep 0.5
    done
    return 1
}
# The run's QEMU (ht_qemus) gone: TERM, then KILL.  (Only where nothing on its disks matters.)
bs2_rs_kill() {
    local q; q=$(ht_qemus "$R"); [[ -n $q ]] || return 0
    # shellcheck disable=SC2086  # PIDs
    kill $q 2>/dev/null
    for _ in $(seq 50); do [[ -n $(ht_qemus "$R") ]] || return 0; sleep 0.2; done
    # shellcheck disable=SC2046
    kill -9 $(ht_qemus "$R") 2>/dev/null; sleep 1
}

# Boot the rescuer with no disk of a case's and save its state in DIR (under a
# lock; another run saving it now: exit 1 at once, this case boots cold).
bs2_rs_build() {  # DIR KEYTEXT KIND
    local sd=$1 key=$2 kind=$3 part=$1.part lk o fwb=() ex=()
    mkdir -p "$BS2_RS_ROOT" && exec {lk}>>"$sd.lock" || { BS2_RS_WHY="can't make $BS2_RS_ROOT"; return 1; }
    if ! flock -n "$lk"; then exec {lk}>&-; BS2_RS_WHY="another run is saving it now"; return 1; fi
    if [[ -f $sd/ready ]] && cmp -s <(printf '%s\n' "$key") "$sd/key.txt"; then exec {lk}>&-; return 0; fi
    rm -rf "$sd" "$part"
    if ! { mkdir -p "$part/share" && qemu-img create -q -f qcow2 -b "$RESCUER_IMG" -F qcow2 "$part/rescuer.qcow2"; }; then
        exec {lk}>&-; BS2_RS_WHY="can't make $part"; return 1
    fi
    mapfile -t fwb < <(bs2_rs_rfw "$part/vars.fd")
    mapfile -t ex < <(bs2_rs_extra "$part/share" "$BS2_QMP")
    rm -f "$BS2_QMP"
    vm_up "$part/rescuer.qcow2" "$R/logs/rescuer-state-serial.log" "${fwb[@]}" --memory "$BS2_RS_MEM" -- "${ex[@]}"
    if ! bs2_rs_wait 600; then
        BS2_RS_WHY="the rescuer didn't boot to be saved (logs/rescuer-state-serial.log, its .vm-run)"
    elif ! TMO=300 sshk "$rkey" "$BS2_RS_QUIET" >"$R/logs/rescuer-state-save.log" 2>&1; then
        BS2_RS_WHY="quietening it (logs/rescuer-state-save.log)"
    elif ! python3 "$BS2_QMP_PY" "$BS2_QMP" save "$part/state" 300 >>"$R/logs/rescuer-state-save.log" 2>&1; then
        BS2_RS_WHY="saving it: $(tail -n 1 "$R/logs/rescuer-state-save.log")"
    else
        BS2_RS_WHY=""
    fi
    # (Once migrated, QEMU has flushed and let go of its disk: a kill can't hurt it.)
    [[ -n $BS2_RS_WHY ]] || python3 "$BS2_QMP_PY" "$BS2_QMP" quit >>"$R/logs/rescuer-state-save.log" 2>&1
    bs2_rs_kill
    if [[ -n $BS2_RS_WHY ]]; then rm -rf "$part"; exec {lk}>&-; return 1; fi
    printf '%s\n' "$key" >"$part/key.txt"; echo "$kind" >"$part/kind"
    cp "$R/logs/rescuer-state-serial.log" "$part/boot-serial.log"
    chmod -R a+rX "$part"; : >"$part/ready"
    if ! mv -T "$part" "$sd"; then rm -rf "$part"; exec {lk}>&-; BS2_RS_WHY="can't rename $part"; return 1; fi
    # An older state of the same rescuer and firmware is superseded (a case
    # resuming it now has its files open still).
    for o in "$BS2_RS_ROOT"/*/; do
        o=${o%/}
        [[ $o != "$sd" && $o != *.part && -f $o/kind && $(<"$o/kind") == "$kind" ]] && rm -rf "$o" "$o.lock"
    done
    exec {lk}>&-
    say "note: rescuer: its state saved for this host's cases (${sd##*/}: logs/rescuer-state-serial.log, rescuer-state-save.log)"
}

# The saved state gone wrong after its QEMU started: that QEMU goes, the
# rescuer's disk, NVRAM, targets and decoy are made anew, for a cold boot.
bs2_rs_fallback() {  # WHY
    say "note: rescuer: a cold boot (resuming its saved state: $1)"
    bs2_rs_kill
    rm -f "$R/rescuer.qcow2" "$R/rescuer-vars.fd" "$BS2_QMP"
    ((BS2_TIER == 2)) && rm -f "$R/vars.fd"
    qemu-img create -q -f qcow2 -b "$RESCUER_IMG" -F qcow2 "$R/rescuer.qcow2"
    local i
    for ((i = 0; i < n; i++)); do rm -f "${dst[i]}"; bs2_target_make "$i" "${dst[i]}" || return 1; done
    rm -f "$R/decoy.qcow2"; qemu-img create -q -f qcow2 "$R/decoy.qcow2" "$(size_of "${imgs[0]}")"
    return 1
}

# In phase_rebuild, the targets and decoy made: exit 0 when the rescuer is up
# from its saved state with them attached as a cold boot would have them;
# exit 1 to boot it cold (as ever).
bs2_rescuer_resume() {
    # (Tier 2's NVRAM starts as the rescuer's boot leaves it, never an earlier attempt's.)
    ((BS2_TIER == 2)) && rm -f "$R/vars.fd"
    [[ ${BS2_RESCUER_STATE:-1} == 1 ]] || { say "note: rescuer: a cold boot (BS2_RESCUER_STATE=$BS2_RESCUER_STATE)"; return 1; }
    [[ -z ${BS2_CHAIN_OUT:-} ]] || { say "note: rescuer: a cold boot (the leftovers set: the rescuer meets its disk at boot)"; return 1; }
    ! bs2_rs_uefi || ((n + 1 <= BS2_RS_PORTS)) || { say "note: rescuer: a cold boot ($((n + 1)) disks to hot-plug, $BS2_RS_PORTS root ports)"; return 1; }
    mkdir -p /run/bs2 && BS2_QMP=/run/bs2/qmp-$PORT.sock || return 1
    local out hash kind key sd vars i dev bus devs want got fwr=() ex=() L=$R/logs/rescuer-resume.log
    out=$(python3 "$BS2_HOST" rescuer-key --image "$RESCUER_IMG" --memory "$BS2_RS_MEM" --rfw="${rfw[*]}" \
        --extra="$(bs2_rs_extra SHARE QMP | tr '\n' ' ')" 2>&1) || { say "note: rescuer: a cold boot (rescuer-key: $(tail -n 1 <<<"$out"))"; return 1; }
    read -r hash kind <<<"$(head -n 1 <<<"$out")"; key=$(tail -n +2 <<<"$out")
    [[ $hash =~ ^[0-9a-f]{16}$ ]] || { say "note: rescuer: a cold boot (rescuer-key: $(head -n 1 <<<"$out"))"; return 1; }
    sd=$BS2_RS_ROOT/$hash BS2_RS_WHY=""
    if ! { [[ -f $sd/ready ]] && cmp -s <(printf '%s\n' "$key") "$sd/key.txt"; }; then
        bs2_rs_build "$sd" "$key" "$kind" || { say "note: rescuer: a cold boot (no saved state: $BS2_RS_WHY)"; return 1; }
    fi
    rm -f "$R/rescuer.qcow2"
    qemu-img create -q -f qcow2 -b "$sd/rescuer.qcow2" -F qcow2 "$R/rescuer.qcow2" || { bs2_rs_fallback "no overlay on $sd/rescuer.qcow2"; return 1; }
    vars=$(bs2_rs_vars)
    if bs2_rs_uefi; then cp "$sd/vars.fd" "$vars" || { bs2_rs_fallback "no NVRAM in $sd"; return 1; }; fi
    mapfile -t fwr < <(bs2_rs_rfw "$vars")
    mapfile -t ex < <(bs2_rs_extra "$S" "$BS2_QMP")
    rm -f "$BS2_QMP"
    vm_up "$R/rescuer.qcow2" "$R/logs/rescuer-serial.log" "${fwr[@]}" --memory "$BS2_RS_MEM" -- "${ex[@]}" -incoming "file:$sd/state"
    python3 "$BS2_QMP_PY" "$BS2_QMP" resume 120 >"$L" 2>&1 || { bs2_rs_fallback "$(tail -n 1 "$L")"; return 1; }
    # (Its sshd was up when saved: SSH answers at once, or the resume went wrong.)
    bs2_rs_wait 60 || { bs2_rs_fallback "no SSH in 60 s after the resume (logs/rescuer-serial.log)"; return 1; }
    # Its clock (frozen at the save) and its RNG (every case's copy starts alike), before anything else happens in it.
    head -c 64 /dev/urandom | TMO=30 sshk "$rkey" "T=$(date -u +%s); $BS2_RS_FRESH" >>"$L" 2>&1 ||
        { bs2_rs_fallback "setting its clock and RNG: $(tail -n 1 "$L")"; return 1; }
    # The targets, then the decoy, each seen by the kernel before the next.
    devs=("${dst[@]}" "$R/decoy.qcow2") want="vda"
    for ((i = 0; i < ${#devs[@]}; i++)); do
        dev=vd${letters[i + 1]} bus=()
        bs2_rs_uefi && bus=("bs2rp$i")
        python3 "$BS2_QMP_PY" "$BS2_QMP" hotplug "bs2n$i" "${devs[i]}" "bs2d$i" "${bus[@]}" >>"$L" 2>&1 ||
            { bs2_rs_fallback "hot-plugging ${devs[i]##*/}: $(tail -n 1 "$L")"; return 1; }
        TMO=30 sshk "$rkey" "i=0; while [ ! -e /sys/block/$dev ] && [ \$i -lt 200 ]; do sleep 0.05; i=\$((i + 1)); done; [ -e /sys/block/$dev ]" ||
            { bs2_rs_fallback "${devs[i]##*/} didn't appear as /dev/$dev in 10 s"; return 1; }
        want+=" $dev:$(($(size_of "${devs[i]}") / 512))"
    done
    got=$(TMO=60 sshk "$rkey" 'udevadm settle -t 30; for b in /sys/block/vd*; do d=${b##*/}; if [ "$d" = vda ]; then printf "vda"; else printf " %s:%s" "$d" "$(cat "$b/size")"; fi; done' 2>>"$L")
    [[ $got == "$want" ]] || { bs2_rs_fallback "the rescuer sees ${got:-nothing}, a cold boot would $want"; return 1; }
    echo "rescuer-state $sd $hash" >>"$R/logs/manifest.txt"
    say "note: rescuer: resumed from its saved state ${sd##*/} (saved $(date -u -r "$sd/ready" +%FT%TZ)); $(grep '^clock ' "$L" | tail -n 1); ${want#vda } hot-plugged"
}

# At rebuild's end: once nothing holds a target or the decoy (no holder, mount,
# swap or pool) the guest syncs and QEMU is told to quit, which drains and
# flushes its disks (qcow2's metadata too) as it exits: what a poweroff adds
# beyond that is the rescuer's own.  Anything holding one: a poweroff as ever.
BS2_IDLE='for d in $DEVS; do
    for x in /sys/block/$d /sys/block/$d/$d*; do
        [ -d "$x" ] || continue
        p=${x##*/}
        [ -z "$(ls "$x/holders" 2>/dev/null)" ] || { echo "$p is held by $(ls "$x/holders" | tr "\n" " ")"; exit 1; }
        ! grep -q "^/dev/$p " /proc/mounts || { echo "$p is mounted"; exit 1; }
        ! grep -q "^/dev/$p " /proc/swaps || { echo "$p is swap"; exit 1; }
    done
done
if command -v zpool >/dev/null 2>&1; then p=$(zpool list -H -o name 2>/dev/null); [ -z "$p" ] || { echo "pools imported: $p"; exit 1; }; fi
sync && echo "idle:$DEVS; synced"'
bs2_rescuer_down() {
    if [[ ${BS2_FAST_DOWN:-1} != 1 ]] || [[ -z $(ht_qemus "$R") ]]; then vm_down "$rkey"; return; fi
    local devs="" i
    for ((i = 1; i <= n + 1; i++)); do devs+=" vd${letters[i]}"; done
    if TMO=120 sshk "$rkey" "DEVS='$devs'; $BS2_IDLE" >"$R/logs/rescuer-down.txt" 2>&1; then
        bs2_vm_quit || bad "the rescuer's QEMU didn't quit in 30 s when told to (killed): the targets may lack its last writes"
    else
        say "note: rescuer: a poweroff, not a quit ($(tail -n 1 "$R/logs/rescuer-down.txt"))"
        vm_down "$rkey"
    fi
}
# The run's QEMU told to quit (TERM: QEMU's own clean exit, its disks flushed
# and closed); exit 1 when it had to be killed after 30 s.
bs2_vm_quit() {
    local q; q=$(ht_qemus "$R"); [[ -n $q ]] || return 0
    # shellcheck disable=SC2086  # PIDs
    kill -TERM $q 2>/dev/null
    for _ in $(seq 300); do [[ -n $(ht_qemus "$R") ]] || return 0; sleep 0.1; done
    # shellcheck disable=SC2046
    kill -9 $(ht_qemus "$R") 2>/dev/null; sleep 1
    return 1
}
# At verify's end: the restored boot (-snapshot: its writes are thrown away) quit, not powered off.
bs2_restored_down() {
    if [[ ${BS2_FAST_DOWN:-1} == 1 ]]; then bs2_vm_quit || true; else vm_down "$gkey"; fi
}
