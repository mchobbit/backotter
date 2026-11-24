# shellcheck shell=bash
# HoltTest1's HT_VARIANT=json (docs/design/holt-json.md, "Testing"; RT10j),
# sourced by ht1.sh and ht1-bsd.sh in their "# t2-only: json" blocks, and
# only in a json run: the rebuild runs as ever (the text run), then again on
# new targets in a second throwaway rescuer with the same arguments and
# --json (the json pass), and so does the verify, on the json pass's disks.
#
# The json pass (JPASS=1) is the driver's own phase_rebuild and phase_verify
# run again, their logs and dumps going to logs/json/ and dumps/json/
# (json_swap_in, json_swap_out), with HT_QUICK's refusals left out (the text
# run had them) and these in their place, each its PASS or FAIL line,
# "json: " before it:
#   - before the stages, under BKO_HOLT_NO_COMMIT=1: `--version --json` says
#     no_commit; `load ... --commit --json` is refused (exit 2, a
#     bko-holt-error no_commit); `plan --json` of the whole argument list is
#     ok; and a dry run, `load ... --through close --json` without
#     --commit, ends 0, every command `would`, boot and close among them,
#     and leaves every target's and the decoy's image as it was;
#   - each stage, its `plan --json` first and then the stage with --json
#     --expect-plan of that plan's hash: its events pass holt_schema.py
#     (testvm/rescue's, the checker the fake's scenarios pass) and end 0,
#     each command run with its cmd_done 0; and `status --json` after it,
#     the stage done, as the text run's status check;
#   - after the rebuild: the commands the events ran are the text run's (in
#     its load.log, in order), the dry run's are the writing runs', and the
#     json pass's load.log is the text run's but for its argv lines, the
#     archiver's flag and status line, and a private write's text;
#   - after the verify: judge.py's verdict is the text run's (its dumps and
#     its files), and the restored /var/log/bko-holt is the text run's but
#     for the same.
# In both runs the key files and the repository's password are copies in a
# private directory of the rescuer (JKEYS, 0700, each file 0600), as
# --json's key-file checks want (J3): the text run's commands are then the
# json pass's, paths and all.  Each --json call's argv beside its output
# (logs/json/json-NAME.argv), for ht1-json.py record.
#
# The driver sets, before sourcing: JDRV (linux, bsd), JLAYOUT (the layout's
# path in the rescuer), JKEYS, JREPO_PW (the password file's path the
# driver gives load) and JROOT (files in R the verify writes: fsmeta's
# record, mtree's listing).  It reads the driver's n, rkey, dst and (BSD)
# all_dst, dst_more and DST, and sets them for the json pass.
# shellcheck disable=SC2154,SC2034

JPY=$HERE/ht1-json.py
JSCHEMA=$T/rescue/holt_schema.py
JSTAGES=" partition assemble format mount restore boot close "
[[ -f $JPY && -f $JSCHEMA ]] || { echo "${0##*/}: HT_VARIANT=json: no $JPY or $JSCHEMA" >&2; exit 2; }
# The json pass's report lines say so.
ok() { say "PASS ${JTAG:-}$*"; }
bad() { say "FAIL ${JTAG:-}$*"; fails=$((fails + 1)); }

jpy() { python3 -I "$JPY" "$@"; }
jschema() { python3 -I "$JSCHEMA" --quiet "$@" 2>&1 | tail -n 4 | tr '\n' ' '; return "${PIPESTATUS[0]}"; }
jtail() { grep -v '^$' "$1" 2>/dev/null | tail -n "${2:-3}" | tr '\n' ' '; }

# The key files and the password file into JKEYS, in both runs.
json_keys_put() {
    local key=$1
    case $JDRV in
        linux) sshk "$key" "install -d -m 700 $JKEYS && { [ ! -f /mnt/share/passphrase ] || install -m 600 /mnt/share/passphrase $JKEYS/passphrase; } && install -m 600 $JREPO_PW $JKEYS/repo-password" ;;
        *) sshk "$key" "umask 077; mkdir -p $JKEYS && chmod 700 $JKEYS && cp $JREPO_PW $JKEYS/repo-password && chmod 600 $JKEYS/repo-password" ;;
    esac
}
# The caller's map and repo (phase_rebuild's locals) with JKEYS' copies.
json_private_args() {
    local i
    for i in "${!map[@]}"; do
        [[ ${map[i]} != *=/mnt/share/passphrase ]] || map[i]=${map[i]%=/mnt/share/passphrase}=$JKEYS/passphrase
    done
    for i in "${!repo[@]}"; do
        [[ ${repo[i]} != "$JREPO_PW" ]] || repo[i]=$JKEYS/repo-password
    done
}

# ---- the json pass's own files and disks -------------------------------------

json_swap_in() {
    local d f
    for d in logs dumps; do
        rm -rf "${R:?}/$d.text"
        mv "$R/$d" "$R/$d.text" && cp -a "$R/$d.text" "$R/$d" || return 1
    done
    for f in "${JROOT[@]}"; do [[ ! -e $R/$f ]] || mv -f "$R/$f" "$R/$f.text"; done
    # (The copies keep their times: what the pass writes, even as it was, is newer.)
    touch "$R/.ht/json-pass"
}
# What the pass wrote in logs/ and dumps/ goes to their json/, and the text
# run's files come back.
json_swap_out() {
    local d f rel
    for d in logs dumps; do
        mkdir -p "$R/$d.text/json"
        while IFS= read -r -d '' rel; do
            rel=${rel#./}
            mkdir -p "$(dirname "$R/$d.text/json/$rel")" && mv -f "$R/$d/$rel" "$R/$d.text/json/$rel"
        done < <(cd "$R/$d" && find . -type f ! -path './json/*' -newer "$R/.ht/json-pass" -print0)
        rm -rf "${R:?}/$d" && mv "$R/$d.text" "$R/$d"
    done
    for f in "${JROOT[@]}"; do
        [[ ! -e $R/$f ]] || mv -f "$R/$f" "$R/${f%.*}-json.${f##*.}"
        [[ ! -e $R/$f.text ]] || mv -f "$R/$f.text" "$R/$f"
    done
}
# The driver's disk arrays: the json pass's targets, dst-json-I.qcow2, and back.
json_disks() {
    local i
    if [[ $1 == json ]]; then
        JD_SAVE=("${dst[@]}"); dst=()
        for ((i = 0; i < n; i++)); do dst+=("$R/dst-json-$i.qcow2"); done
    else
        dst=("${JD_SAVE[@]}")
    fi
    case $JDRV in
        linux) extra_dst=(); for ((i = 1; i < n; i++)); do extra_dst+=(--disk "${dst[i]}"); done ;;
        *) all_dst=() dst_more=()
           for ((i = 0; i < n; i++)); do all_dst+=(--disk "${dst[i]}"); ((i == 0)) || dst_more+=(--disk "${dst[i]}"); done
           DST=${dst[0]} ;;
    esac
}

# ---- the calls ---------------------------------------------------------------

# json_call NAME WANT CMD: CMD in the rescuer (ht_load_try's start check),
# its stdout logs/json-NAME.json or .events, stderr .err, exit .exit; 0
# when its exit is WANT.  JCALL_RC is its exit.
json_call() {
    local name=$1 want=$2 cmd=$3 ext=json
    [[ $name == dry || " $JSTAGES " == *" $name "* ]] && ext=events
    ht_load_try "$R/logs/json-$name.$ext" "$R/logs/json-$name.err" sshk "$rkey" "echo $HT_STARTED; $cmd"
    JCALL_RC=$?
    echo "$JCALL_RC" > "$R/logs/json-$name.exit"
    [[ $JCALL_RC == "$want" ]]
}
json_argv() { local name=$1; shift; printf '%s\n' "$@" > "$R/logs/json-$name.argv"; }
# A stream's checks: the schema's, then ht1-json.py's for MODE.
json_stream_ok() {
    local f=$1 mode=$2 what=$3 s
    if ! s=$(jschema "$f"); then bad "$what: its events fail the schema: $s"; return 1; fi
    if ! s=$(jpy stream "$f" "$mode" 2>&1); then bad "$what: $(tr '\n' ' ' <<<"$s" | cut -c1-600)"; return 1; fi
    ok "$what: its events pass the schema and end 0 ($s)"
}
# The images of the json pass's targets and the decoy, read on the host,
# after a sync in the rescuer.
json_images() { sshk "$rkey" sync >/dev/null 2>&1; md5sum "${dst[@]}" "$R/decoy.qcow2" 2>&1; }

# Before the json pass's stages (phase_rebuild's map, repo and stages).
json_before_stages() {
    local full=("${map[@]}" "${repo[@]}") L=$R/logs s h
    local nc="BKO_HOLT_NO_COMMIT=1"
    json_argv version --version --json
    if json_call version 0 "$nc $RHOLT --version --json" && s=$(jschema "$L/json-version.json") &&
        s=$(jpy doc "$L/json-version.json" bko-holt-api no_commit=true dry_run=true 2>&1); then
        ok "--version --json under $nc: no_commit and dry_run true"
    else
        bad "--version --json under $nc: exit $JCALL_RC: ${s:-$(jtail "$L/json-version.err")}"; return 1
    fi
    json_argv refuse-commit load "$JLAYOUT" "${full[@]}" --through close --json --commit
    if json_call refuse-commit 2 "$nc $RHOLT load $JLAYOUT ${full[*]} --through close --json --commit" &&
        s=$(jschema "$L/json-refuse-commit.json") && s=$(jpy doc "$L/json-refuse-commit.json" bko-holt-error code=no_commit exit=2 2>&1); then
        ok "load --commit --json refused under $nc (exit 2, no_commit)"
    else
        bad "load --commit --json under $nc: exit $JCALL_RC, want 2 and a no_commit document: ${s:-$(jtail "$L/json-refuse-commit.err")}"; return 1
    fi
    json_argv plan load "$JLAYOUT" "${full[@]}" --only plan --json
    if json_call plan 0 "$RHOLT load $JLAYOUT ${full[*]} --only plan --json" && s=$(jschema "$L/json-plan.json") &&
        h=$(jpy doc "$L/json-plan.json" bko-holt-plan ok=true --print plan_sha256 2>&1); then
        ok "plan --json of the whole run: ok, plan $h"
    else
        bad "plan --json of the whole run: exit $JCALL_RC: ${h:-${s:-$(jtail "$L/json-plan.err")}}"; return 1
    fi
    local before after
    before=$(json_images)
    json_argv dry load "$JLAYOUT" "${full[@]}" --through close --json
    if json_call dry 0 "$nc $RHOLT load $JLAYOUT ${full[*]} --through close --json"; then
        ok "the dry run under $nc (exit 0)"
    else
        bad "the dry run under $nc: exit $JCALL_RC: $(jtail "$L/json-dry.err")"; return 1
    fi
    json_stream_ok "$L/json-dry.events" dry "the dry run" || return 1
    after=$(json_images)
    if [[ -n $before && $before == "$after" ]]; then
        ok "the dry run left every target's and the decoy's image as it was"
    else
        bad "the dry run changed a disk image (before: $(tr '\n' ' ' <<<"$before"); after: $(tr '\n' ' ' <<<"$after"))"; return 1
    fi
}

# A stage of the json pass (load_step's, for a stage NAME): its plan --json,
# then the stage with --json --expect-plan, its events checked.
json_load_step() {
    local want=$1 name=$2; shift 2
    local a=("$@") pa=() i L=$R/logs h s
    for ((i = 0; i < ${#a[@]}; i++)); do
        case ${a[i]} in --commit) ;; --only|--through) ((i++)) ;; *) pa+=("${a[i]}") ;; esac
    done
    json_argv "plan-$name" load "$JLAYOUT" "${pa[@]}" --only plan --json
    if ! json_call "plan-$name" 0 "$RHOLT load $JLAYOUT ${pa[*]} --only plan --json" ||
        ! h=$(jpy doc "$L/json-plan-$name.json" bko-holt-plan ok=true --print plan_sha256 2>&1); then
        bad "load $name: its plan --json first: exit $JCALL_RC: ${h:-$(jtail "$L/json-plan-$name.err")}"; return 1
    fi
    json_argv "$name" load "$JLAYOUT" "$@" --json --expect-plan "$h"
    json_call "$name" "$want" "$RHOLT load $JLAYOUT $* --json --expect-plan $h"
    local got=$JCALL_RC
    # (The text run's name for its stderr, which the driver's failure paths read.)
    cp -f "$L/json-$name.err" "$L/rebuild-$name.err" 2>/dev/null
    if [[ $got != "$want" ]]; then
        bad "load $name: exit $got, want $want: $(jtail "$L/json-$name.err") $(grep -h '"event":"failed"' "$L/json-$name.events" 2>/dev/null | cut -c1-400)"
        return 1
    fi
    ok "load $name (exit $got, --expect-plan ${h:0:12})"
    json_stream_ok "$L/json-$name.events" commit "load $name" || return 1
}
json_is_stage() { [[ " $JSTAGES " == *" $1 "* ]]; }

# status --json after a stage of the json pass.
json_status_after() {
    local s=$1 f=$R/logs/json-status-after-$1.json i rc x
    json_argv "status-after-$s" load "$JLAYOUT" "${map[@]}" --only status --json
    for i in 1 2 3; do
        [[ $i == 1 ]] || sleep 3
        sshk "$rkey" "$RHOLT load $JLAYOUT ${map[*]} --only status --json" > "$f" 2> "${f%.json}.err"; rc=$?
        [[ $rc != 255 && $rc != 124 && -s $f ]] && break
    done
    echo "$rc" > "${f%.json}.exit"
    if ! x=$(jschema "$f"); then bad "status --json after $s: exit $rc: $x"; return; fi
    if x=$(jpy status "$f" "$s" 2>&1); then ok "status --json after $s: $x"; else bad "status --json after $s: $x"; fi
}

# ---- the run's two halves ----------------------------------------------------

# After the text run's rebuild (phase_rebuild's end).
json_rebuild() {
    if ((fails)); then say "note: json: no --json rebuild: the text run's failed"; return; fi
    say "== rebuild again with --json (HT_VARIANT=json), on new targets"
    # (An earlier attempt's, when this is a rerun from rebuild.)
    rm -rf "${R:?}/logs/json" "$R/dumps/json"
    json_swap_in || { harness "json: logs/ and dumps/ couldn't be set aside for the --json rebuild"; return; }
    json_disks json
    local f0=$fails
    JPASS=1 JTAG="json: " HT_QUICK=1 phase_rebuild
    json_disks text
    json_swap_out
    ((fails == f0)) || return
    local L=$R/logs J=$R/logs/json s ev=()
    for s in $JSTAGES; do [[ ! -f $J/json-$s.events ]] || ev+=("$J/json-$s.events"); done
    JTAG="json: "
    if s=$(jpy inlog "$L/rebuild-load.log" "${ev[@]}" 2>&1); then ok "the commands the events ran are the text run's: $s"
    else bad "the commands the events ran aren't the text run's: $(tr '\n' ' ' <<<"$s" | cut -c1-600)"; fi
    if s=$(jpy same "$J/json-dry.events" "${ev[@]}" 2>&1); then ok "the dry run's commands are the ones that ran: $s"
    else bad "the dry run's commands aren't the ones that ran: $(tr '\n' ' ' <<<"$s" | cut -c1-600)"; fi
    if s=$(jpy logcmp "$L/rebuild-load.log" "$J/rebuild-load.log" 2>&1); then ok "load.log as the text run's: $s"
    else bad "load.log: $(tr '\n' ' ' <<<"$s" | cut -c1-900) (logs/rebuild-load.log, logs/json/rebuild-load.log)"; fi
    # J20 over the run: the whole run's plan, made before anything was
    # written, against the restore stage's of the same arguments, made after
    # the stages before it.  Unlike, a Resume that kept the first plan's hash
    # would meet plan_changed: a note, not a FAIL (RT10j's finding).
    local h0 h1
    h0=$(jpy doc "$J/json-plan.json" bko-holt-plan --print plan_sha256 2>/dev/null)
    h1=$(jpy doc "$J/json-plan-restore.json" bko-holt-plan --print plan_sha256 2>/dev/null)
    if [[ -n $h0 && $h0 == "$h1" ]]; then
        ok "the whole run's plan before the partition stage is the restore stage's after it (${h0:0:12})"
    else
        say "note: json: the whole run's plan before the partition stage (${h0:0:12}) isn't the restore stage's after it (${h1:0:12}): $(python3 -I -c 'import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:])
for x, y in zip(a.get("disks") or [], b.get("disks") or []):
    t, u = x.get("target") or {}, y.get("target") or {}
    for k in ("node", "by_id", "size_bytes", "serial", "wwid"):
        if t.get(k) != u.get(k): print("%s %s %s -> %s" % (t.get("node"), k, t.get(k), u.get(k)), end="; ")
if [s.get("commands") for s in a.get("stages") or []] != [s.get("commands") for s in b.get("stages") or []]: print("the commands differ", end="")' "$J/json-plan.json" "$J/json-plan-restore.json" 2>&1)"
    fi
    JTAG=""
    ((fails == f0)) && : > "$J/rebuilt"
}

# The restored system's /var/log/bko-holt (the driver's verify, the
# restored disk booted), in both runs.
json_restored_log() {
    sshk "$1" 'for d in /var/log/bko-holt /boot/system/var/log/bko-holt; do [ -d "$d" ] || continue; for f in "$d"/*; do [ -f "$f" ] && { echo "==== FILE ${f##*/}"; cat "$f"; }; done; done' \
        > "$R/logs/restored-bko-holt.txt" 2>&1
}

# After the text run's verify (phase_verify's end).
json_verify() {
    [[ -f $R/logs/json/rebuilt ]] || { say "note: json: no verify of the --json rebuild: it didn't pass"; return; }
    if ((fails)); then say "note: json: no verify of the --json rebuild: the text run's verify failed"; return; fi
    say "== verify again, of the --json rebuild's disks"
    json_swap_in || { harness "json: logs/ and dumps/ couldn't be set aside for the --json verify"; return; }
    json_disks json
    local f0=$fails
    JPASS=1 JTAG="json: " phase_verify
    json_disks text
    json_swap_out
    ((fails == f0)) || return
    local L=$R/logs J=$R/logs/json s
    JTAG="json: "
    if s=$(jpy verdict "$L/judge.txt" "$J/judge.txt" 2>&1); then ok "judge.py's verdict is the text run's: $s"
    else bad "judge.py's verdict isn't the text run's: $(tr '\n' ' ' <<<"$s" | cut -c1-600) (logs/judge.txt, logs/json/judge.txt)"; fi
    if [[ -f $L/fsjudge.txt ]]; then
        if s=$(jpy verdict "$L/fsjudge.txt" "$J/fsjudge.txt" 2>&1); then ok "judge.py fs's verdict is the text run's: $s"
        else bad "judge.py fs's verdict isn't the text run's: $(tr '\n' ' ' <<<"$s" | cut -c1-600) (logs/fsjudge.txt, logs/json/fsjudge.txt)"; fi
    fi
    if [[ ! -s $L/restored-bko-holt.txt || ! -s $J/restored-bko-holt.txt ]]; then
        bad "the restored /var/log/bko-holt of each run wasn't read (logs/restored-bko-holt.txt, logs/json/)"
    elif s=$(jpy logcmp "$L/restored-bko-holt.txt" "$J/restored-bko-holt.txt" 2>&1); then ok "the restored /var/log/bko-holt is the text run's: $s"
    else bad "the restored /var/log/bko-holt: $(tr '\n' ' ' <<<"$s" | cut -c1-900)"; fi
    JTAG=""
}
