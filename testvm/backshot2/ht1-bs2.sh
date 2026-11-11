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

[[ -n ${BS2_CASE:-} && -n ${BS2_NAME:-} && -n ${BS2_HOST:-} ]] || { echo "ht1: HT_VARIANT=bs2 needs BS2_CASE, BS2_NAME and BS2_HOST" >&2; exit 2; }
BS2_TIER=${BS2_TIER:-1}
GV=$BS2_NAME
# fsmeta.py walks by path, and the content drift's deep tree is past PATH_MAX:
# it leaves srv/bs2/deep* to bs2-host deep, which walks by directory fds.
BS2_FSMETA=(--skip srv/bs2/deep0)
BS2_SNAPS=1 BS2_RESTORE=0 BS2_CONTENT=0 BS2_FLAGS=() BS2_JUDGE=(--expect) BS2_FSJUDGE=() BS2_TARGETS=() BS2_VFW=()

# After ht1.sh has its firmware options (fw, rfw): tier 2's shared NVRAM.
bs2_setup() {
    if ((BS2_TIER == 2)); then
        ((uefi)) || { echo "ht1: tier 2 is for UEFI sources" >&2; exit 2; }
        rfw=(--uefi --no-secure-boot --uefi-vars "$R/vars.fd")
        BS2_VFW=(--uefi-vars "$R/vars.fd")
        [[ " ${fw[*]} " == *" --no-secure-boot "* ]] || fw+=(--no-secure-boot)
    fi
}

# End of phase_source: the drifted layout, and what the case asks for.
bs2_after_source() {
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
    rm -rf "$S/repo"; head -c 24 /dev/urandom | base64 > "$S/repo-password"
    mkdir -p "$SRC_MP"
    local rustic=("$RUSTIC" -r "$S/repo" --password-file "$S/repo-password" --no-progress --no-cache) r
    "${rustic[@]}" init > "$R/logs/backup.log" 2>&1 || { bad "rustic-bko init (logs/backup.log)"; return; }
    for ((r = 0; r < BS2_SNAPS; r++)); do
        if [[ $BS2_CONTENT == 1 ]] || ((r > 0)); then
            bs2_mount rw || { bad "mount the source read-write, to plant (logs/backup-mount.log)"; return; }
            if ((r == 0)); then
                if python3 "$BS2_HOST" plant "$BS2_CASE" "$SRC_MP" --layout "$R/dumps/drifted.json" > "$R/logs/bs2-plant.log" 2>&1; then
                    ok "content planted: $(tail -1 "$R/logs/bs2-plant.log")"
                else
                    bad "content planting (logs/bs2-plant.log): $(tail -1 "$R/logs/bs2-plant.log")"; bs2_umount; return
                fi
            else
                python3 "$BS2_HOST" change "$BS2_CASE" "$SRC_MP" "$r" >> "$R/logs/bs2-plant.log" 2>&1 || { bad "content change $r"; bs2_umount; return; }
            fi
            bs2_umount
        fi
        bs2_mount ro || { bad "mount the source read-only"; return; }
        if "${rustic[@]}" backup --set-atime yes "$SRC_MP" >> "$R/logs/backup.log" 2>&1; then
            ok "rustic-bko backup $((r + 1)) of $BS2_SNAPS ($(du -sh "$S/repo" | cut -f1))"
        else
            bad "rustic-bko backup $((r + 1)) (logs/backup.log)"; bs2_umount; return
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
    cp "$RUSTIC" "$S/rustic-bko"; cp "$BIN"/*.deb "$S/"
    [[ ! -f $BIN/bcachefs ]] || cp "$BIN/bcachefs" "$S/"
    [[ -n $gpass ]] && printf '%s' "$(cat "$gpass")" > "$S/passphrase"
    chmod -R a+rX "$S"
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
        echo "latest:$SRC_MP"
    fi
}

# In phase_verify, with the restored disk mounted: the deep tree compared.
bs2_after_dst_record() {
    [[ $BS2_CONTENT == 1 && -f $R/src-deep.json ]] || return 0
    if python3 "$BS2_HOST" deep compare "$DST_MP" "$R/src-deep.json" > "$R/logs/bs2-deep.txt" 2>&1; then
        ok "the deep tree (past PATH_MAX) agrees: $(tail -1 "$R/logs/bs2-deep.txt")"
    else
        bad "the deep tree differs: $(head -3 "$R/logs/bs2-deep.txt" | tr '\n' ' ')"
    fi
}

bs2_target_size() {  # I: target disk I's bytes
    echo "${BS2_TARGETS[$1]}"
}
