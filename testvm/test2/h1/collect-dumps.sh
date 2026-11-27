#!/usr/bin/env bash
# H1's raw corpus (docs/design/test2.md "Shared pieces to build", H1): for
# each of HoltTest2's sources, the newest source dump HoltTest1 recorded
# (/bkoscratch/holttest1d/RUN/GUEST/dumps/src.json) on any of HOSTS, PASS
# runs first.  The collector, a farm host (this one, or --collector H), does
# the work and keeps OUT.  Raw dumps hold the guests' serials, UUIDs and
# hostnames: they stay in OUT on the collector, never on the Mac or in the
# repository; holt/tests/corpus_build.rs makes the redacted corpus from them.
#
#   collect-dumps.sh [--collector H] [--out DIR] [--hosts H,H] [--sources FILE] [--records DIR] [--vms DIR]
#       writes OUT/GUEST/src.json (the raw dump, 0600) for each source and
#       OUT/provenance.tsv (guest host run result report_time generated
#       tool_version generation image raw_sha256), OUT 0700.  OUT holds this
#       collection only: a guest's earlier dump goes before its candidates
#       are tried, and a dump of a guest that isn't a source now is removed.
#       A guest without a dump is named and the exit is 1, after the others
#       are collected.
#   collect-dumps.sh --scan CORPUS [--collector H] [--out DIR] [--hosts H,H] [--vms DIR]
#       leakscan.py over CORPUS (holt/tests/corpus's layout) on each host,
#       with that host's own strings (passphrase files, disk serials, LAN
#       addresses, MACs, the git e-mail), which never leave it: on the
#       collector in a strings file in OUT (0600, OUT 0700), and with each raw
#       dump's own identifiers from OUT (every corpus src.json needs its raw
#       twin); on the other hosts in a private temporary directory that the
#       corpus (it is public) and leakscan.py are copied to.  The strings
#       files and that directory are deleted at the end.  Exit 0 clean, 1
#       findings, 2 a scan that couldn't be completed; findings name the
#       file, the JSON path and the class, never the text.
#
# OUT, --records and --vms are paths on the collector (--records and --vms on
# every host too).  OUT defaults to T2_HOST_ROOT/h1-raw (T2_HOST_ROOT:
# /bkoscratch/test2) there.  The collector defaults to this host, which must
# then be a host of testvm/hosts.json, so that raw dumps never land on the
# Mac; any other collector (from the Mac, say) gets a private temporary copy
# of this script, leakscan.py, the host table, classes.json, --sources FILE
# and --scan's CORPUS, runs the job there, and the copy is deleted at the
# end; only the messages come back.  HOSTS defaults to the table's default
# amd64 hosts (hosts.sh: hosts_names --default --arch amd64) that have
# HoltTest records (a directory RECORDS); a default host that ssh can't reach
# is an exit 1 (give --hosts without it).  Each host is reached by its row's
# ssh target (m@HOST.local for a name in no row), the collector directly; the
# collector copies the other hosts' dumps with scp, host to host.  The sources
# are HoltTest2's: testvm/test2/classes.json's holt.families and
# holt.no_family (45 on 2026-11-08).  Without that file, the collector's vms/
# dirs holding a qcow2, less the hard-coded copy of holt.excluded
# (freebsd15-builder, freebsd15-ufs, freebsd15-geli).  --sources FILE (a name
# a line) gives them instead.
#
# The choice, per guest (its own name only: GUEST-VARIANT, GUEST.N, .try*,
# .stopped and the like are other dirs, and the RUNs debug and debug-state
# are left out): the newest report.txt whose RESULT is PASS; else, flagged
# result=FAIL-after-source, the newest whose report has "== backup" and no
# FAIL line before it.  "Newest" is report.txt's mtime (the run's end), then
# the dump's.  A dump that isn't a whole bko-holt dump (a copy in progress)
# is passed over for the next.  provenance.tsv's image is "same" when the
# run's logs/manifest.txt image line (size, mtime, inode) still matches the
# guest's qcow2 on that host, "changed" when not, "unknown" without one.
#
# Exit 0 done, 1 a guest without a dump (or --scan findings, or a default
# host out of reach), 2 usage.
set -uo pipefail

HERE=$(dirname "$(realpath "$0")")
LEAKSCAN=$HERE/../t2lib/leakscan.py
OUT="" HOSTS="" COLLECTOR="" SOURCES="" RECORDS=/bkoscratch/holttest1d VMS='~/backotter-work/vms' SCAN=""
CLASSES=$HERE/../classes.json
usage() { awk '/^#   collect-dumps.sh /{p=1} /^# OUT, --records/{p=0} p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
while [[ $# -gt 0 ]]; do
    [[ $# -ge 2 ]] || usage
    case $1 in
    --collector) COLLECTOR=$2 ;;
    --out) OUT=$2 ;;
    --hosts) HOSTS=$2 ;;
    --sources) SOURCES=$2 ;;
    --records) RECORDS=$2 ;;
    --vms) VMS=$2 ;;
    --scan) SCAN=$2 ;;
    *) usage ;;
    esac
    shift 2
done
[[ -z $HOSTS || $HOSTS =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]] || { echo "collect-dumps.sh: --hosts H,H (short names)" >&2; exit 2; }
[[ -z $COLLECTOR || $COLLECTOR =~ ^[a-z0-9-]+$ ]] || { echo "collect-dumps.sh: --collector H (a short name)" >&2; exit 2; }
[[ $RECORDS == /* && $RECORDS != *[[:space:]\'\"]* ]] || { echo "collect-dumps.sh: --records: an absolute path without spaces or quotes" >&2; exit 2; }
[[ $VMS != *[[:space:]\'\"]* ]] || { echo "collect-dumps.sh: --vms: a path without spaces or quotes" >&2; exit 2; }
[[ $OUT != *[[:space:]\'\"]* ]] || { echo "collect-dumps.sh: --out: a path without spaces or quotes" >&2; exit 2; }
[[ -z $SOURCES || -r $SOURCES ]] || { echo "collect-dumps.sh: no sources file $SOURCES" >&2; exit 2; }
[[ -z $SCAN || -d $SCAN ]] || { echo "collect-dumps.sh: --scan $SCAN: not a directory" >&2; exit 2; }
ME=$(hostname -s)
COLLECTOR=${COLLECTOR:-$ME}
export PYTHONDONTWRITEBYTECODE=1
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15)
# shellcheck source=../../hosts.sh
. "$HERE/../../hosts.sh"
TABLE=${BKO_HOSTS_JSON:-$HERE/../../hosts.json}
# HOST's ssh target: its row's, else m@HOST.local.
target() {
    local t
    t=$(hosts_get "$1" ssh 2>/dev/null) && [[ -n $t && $t != *[[:space:]\'\"]* ]] && { echo "$t"; return; }
    echo "m@$1.local"
}

if [[ $COLLECTOR != "$ME" ]]; then
    # Here is not the collector: the job runs there, from a private copy.
    [[ -z ${H1_COLLECTOR:-} ]] || { echo "collect-dumps.sh: this host is $ME, not the collector $COLLECTOR" >&2; exit 2; }
    CT=$(target "$COLLECTOR")
    ST="" RT=""
    fwd_cleanup() {
        [[ -n $ST ]] && rm -rf "$ST"
        [[ -n $RT ]] && "${SSH[@]}" "$CT" "rm -rf '$RT'" </dev/null
    }
    trap fwd_cleanup EXIT
    trap 'exit 2' INT TERM HUP
    ST=$(mktemp -d "${TMPDIR:-/tmp}/h1-fwd.XXXXXX") || exit 2
    mkdir -p "$ST/testvm/test2/h1" "$ST/testvm/test2/t2lib" || exit 2
    cp "$HERE/collect-dumps.sh" "$ST/testvm/test2/h1/" && cp "$LEAKSCAN" "$BKO_HOSTS_PY" "$ST/testvm/test2/t2lib/" \
        && cp "$HERE/../../hosts.sh" "$ST/testvm/" && cp "$TABLE" "$ST/testvm/hosts.json" \
        || { echo "collect-dumps.sh: copying the collector's tree failed" >&2; exit 2; }
    if [[ -f $CLASSES ]]; then cp "$CLASSES" "$ST/testvm/test2/classes.json" || exit 2; fi
    args=(--collector "$COLLECTOR" --records "$RECORDS" --vms "$VMS")
    [[ -n $OUT ]] && args+=(--out "$OUT")
    [[ -n $HOSTS ]] && args+=(--hosts "$HOSTS")
    if [[ -n $SOURCES ]]; then cp "$SOURCES" "$ST/sources.txt" || exit 2; args+=(--sources @RT@/sources.txt); fi
    if [[ -n $SCAN ]]; then mkdir "$ST/corpus" && cp -R "$SCAN/." "$ST/corpus/" || exit 2; args+=(--scan @RT@/corpus); fi
    RT=$("${SSH[@]}" "$CT" 'umask 077; mktemp -d "${TMPDIR:-/tmp}/h1-collect.XXXXXX"' </dev/null) || { RT=""; echo "collect-dumps.sh: $COLLECTOR: no temporary directory" >&2; exit 2; }
    [[ $RT == /*/h1-collect.* && $RT != *[[:space:]\'\"]* ]] || { RT=""; echo "collect-dumps.sh: $COLLECTOR: an odd temporary directory" >&2; exit 2; }
    # macOS's tar: no xattrs or AppleDouble files, which GNU tar warns about.
    tarx=()
    [[ $(uname -s) == Darwin ]] && tarx=(--no-mac-metadata --no-xattrs)
    COPYFILE_DISABLE=1 tar "${tarx[@]+"${tarx[@]}"}" -C "$ST" -cf - . | "${SSH[@]}" "$CT" "tar -C '$RT' -xf -" || { echo "collect-dumps.sh: $COLLECTOR: copying the tree failed" >&2; exit 2; }
    cmd="cd '$RT' && umask 077 && H1_COLLECTOR=1 bash testvm/test2/h1/collect-dumps.sh"
    for a in "${args[@]}"; do cmd+=" '${a//@RT@/$RT}'"; done
    echo "collect-dumps.sh: collecting on $COLLECTOR ($CT)" >&2
    "${SSH[@]}" "$CT" "$cmd" </dev/null
    rc=$?
    (( rc == 255 )) && { echo "collect-dumps.sh: $COLLECTOR: ssh failed" >&2; rc=2; }
    exit "$rc"
fi

hosts_get "$ME" name >/dev/null 2>&1 || { echo "collect-dumps.sh: this host ($ME) is no host of $TABLE: name the collector, --collector H (the raw dumps stay on it)" >&2; exit 2; }
OUT=${OUT:-${T2_HOST_ROOT:-/bkoscratch/test2}/h1-raw}
OUT=$(python3 -I -c 'import os, sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))' "$OUT")

# The default hosts: the table's default amd64 hosts with HoltTest records.
if [[ -z $HOSTS ]]; then
    names=$(hosts_names --default --arch amd64) || { echo "collect-dumps.sh: no default hosts in $TABLE" >&2; exit 2; }
    kept="" left="" down=""
    for h in $names; do
        if [[ $h == "$ME" ]]; then
            [[ -d $RECORDS ]]
        else
            "${SSH[@]}" "$(target "$h")" "test -d '$RECORDS'" </dev/null
        fi
        case $? in
        0) kept+=${kept:+,}$h ;;
        1) left+=${left:+, }$h ;;
        *) down+=${down:+, }$h ;;
        esac
    done
    [[ -z $down ]] || { echo "collect-dumps.sh: default hosts out of reach: $down (give --hosts without them)" >&2; exit 1; }
    [[ -n $kept ]] || { echo "collect-dumps.sh: no default host has HoltTest records in $RECORDS" >&2; exit 1; }
    HOSTS=$kept
    echo "collect-dumps.sh: hosts: ${HOSTS//,/, } (the default amd64 hosts with $RECORDS)${left:+; none there: $left}" >&2
fi
TARGETS=""
IFS=, read -ra hs <<<"$HOSTS"
for h in "${hs[@]}"; do TARGETS+=${TARGETS:+,}$h=$(target "$h"); done

if [[ -n $SCAN ]]; then
    [[ -d $OUT ]] || { echo "collect-dumps.sh: no raw dumps in $OUT (collect them first)" >&2; exit 2; }
    [[ -r $LEAKSCAN ]] || { echo "collect-dumps.sh: no $LEAKSCAN" >&2; exit 2; }
    SCAN=$(cd "$SCAN" && pwd -P)
    umask 077
    chmod 0700 "$OUT" || exit 2
    STR="" RT="" RH=""
    cleanup() {
        [[ -n $STR ]] && rm -f "$STR"
        [[ -n $RT && -n $RH ]] && "${SSH[@]}" "$RH" "rm -rf '$RT'" </dev/null
    }
    trap cleanup EXIT
    trap 'exit 2' INT TERM HUP
    worst=0
    for h in "${hs[@]}"; do
        if [[ $h == "$ME" ]]; then
            STR=$(mktemp "$OUT/.h1-strings.XXXXXX") || exit 2
            python3 -I "$LEAKSCAN" strings --vms "$VMS" >"$STR" || { echo "collect-dumps.sh: strings on $h failed" >&2; exit 2; }
            echo "collect-dumps.sh: $h: $(wc -l <"$STR" | tr -d ' ') host strings, and the raw dumps' identifiers from $OUT"
            python3 -I "$LEAKSCAN" scan --strings "$STR" --raw "$OUT" "$SCAN"
            rc=$?
            rm -f "$STR"
            STR=""
        else
            # The corpus goes to H; H's strings stay there.
            RH=$(target "$h")
            RT=$("${SSH[@]}" "$RH" 'umask 077; mktemp -d "${TMPDIR:-/tmp}/h1-scan.XXXXXX"' </dev/null) || { RT=""; echo "collect-dumps.sh: $h: no temporary directory" >&2; exit 2; }
            [[ $RT == /*/h1-scan.* && $RT != *[[:space:]\'\"]* ]] || { RT=""; echo "collect-dumps.sh: $h: an odd temporary directory" >&2; exit 2; }
            "${SSH[@]}" "$RH" "cat > '$RT/leakscan.py'" <"$LEAKSCAN" || { echo "collect-dumps.sh: $h: copying leakscan.py failed" >&2; exit 2; }
            tar -C "$SCAN" -cf - . | "${SSH[@]}" "$RH" "mkdir '$RT/corpus' && tar -C '$RT/corpus' -xf -" || { echo "collect-dumps.sh: $h: copying the corpus failed" >&2; exit 2; }
            "${SSH[@]}" "$RH" "cd '$RT' && umask 077 && PYTHONDONTWRITEBYTECODE=1 python3 -I leakscan.py strings --vms $VMS > strings.jsonl && echo \"collect-dumps.sh: $h: \$(wc -l < strings.jsonl) host strings\" && python3 -I leakscan.py scan --strings strings.jsonl corpus" </dev/null
            rc=$?
            (( rc == 255 )) && rc=2
            "${SSH[@]}" "$RH" "rm -rf '$RT'" </dev/null && RT=""
        fi
        (( rc > worst )) && worst=$rc
    done
    exit "$worst"
fi

# One host's candidates, as JSON lines: python3 -I - RECORDS VMS SOURCES.
read -r -d '' INVENTORY <<'EOF'
import json, os, re, sys
records, vms, names = sys.argv[1], os.path.expanduser(sys.argv[2]), sys.argv[3].split(",")
def stat3(p):
    try:
        st = os.stat(p)
        return [st.st_size, int(st.st_mtime), st.st_ino]
    except OSError:
        return None
def generation(g):
    try:
        with open(os.path.join(vms, g, "image-state")) as f:
            return json.load(f).get("generation")
    except (OSError, ValueError, AttributeError):
        return None
try:
    runs = sorted(os.listdir(records))
except OSError:
    runs = []
for run in runs:
    if run in ("debug", "debug-state"):
        continue
    for g in names:
        k = os.path.join(records, run, g)
        src = os.path.join(k, "dumps", "src.json")
        if not os.path.isfile(src):
            continue
        rep = os.path.join(k, "report.txt")
        lines = []
        try:
            with open(rep, errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            pass
        results = [l for l in lines if l.startswith("RESULT:")]
        result = "none" if not results else ("PASS" if results[-1].strip() == "RESULT: PASS" else "FAIL")
        source_ok = False
        for l in lines:
            if l.startswith("FAIL"):
                break
            if l.startswith("== backup"):
                source_ok = True
                break
        image = []
        try:
            with open(os.path.join(k, "logs", "manifest.txt"), errors="replace") as f:
                for l in f:
                    w = l.split()
                    if len(w) >= 5 and w[0] == "image" and w[1].startswith(os.path.join(vms, g) + "/"):
                        image.append({"was": [int(w[2]), int(w[3]), int(w[4])], "now": stat3(w[1])})
        except (OSError, ValueError):
            image = []
        rs, ss = stat3(rep), stat3(src)
        print(json.dumps({"run": run, "guest": g, "src": src, "report_mtime": rs[1] if rs else 0, "src_mtime": ss[1] if ss else 0,
                          "result": result, "source_ok": source_ok, "image": image, "generation": generation(g)}))
EOF

# The collector, on this host: python3 -I - OUT ME TARGETS SOURCES-FILE RECORDS VMS INVENTORY CLASSES
# (TARGETS: HOST=SSH-TARGET,...).
read -r -d '' COLLECT <<'EOF'
import hashlib, json, os, shlex, shutil, subprocess, sys, time
out, me, targets, sources_file, records, vms, inventory, classes = sys.argv[1:9]
targets = dict(t.split("=", 1) for t in targets.split(","))
hosts = list(targets)
ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15"]
# holt.excluded's copy, for a tree without classes.json.
EXCLUDED = {"freebsd15-builder": "a build helper", "freebsd15-ufs": "the FreeBSD rescuer", "freebsd15-geli": "no GELI in holt; nothing types its passphrase"}
def say(*a):
    print("collect-dumps.sh:", *a, file=sys.stderr, flush=True)
holt = None
if os.path.isfile(classes):
    try:
        with open(classes) as f:
            holt = json.load(f)["holt"]
        fams = holt["families"]
        assert isinstance(fams, dict) and all(isinstance(v, list) for v in fams.values())
        EXCLUDED = dict(holt.get("excluded") or {})
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as e:
        say(f"{classes}: no holt.families in it ({type(e).__name__})")
        sys.exit(2)
d = os.path.expanduser(vms)
try:
    here = sorted(g for g in os.listdir(d) if os.path.isdir(os.path.join(d, g)) and any(n.endswith(".qcow2") for n in os.listdir(os.path.join(d, g))))
except OSError:
    here = []
if sources_file:
    with open(sources_file) as f:
        names = [l.split("#")[0].strip() for l in f]
    names = [n for n in names if n]
    how = sources_file
elif holt is not None:
    names = sorted({g for v in holt["families"].values() for g in v} | set(holt.get("no_family") or []))
    how = f"{classes}'s holt.families and holt.no_family"
    other = [g for g in here if g not in names and g not in EXCLUDED]
    if other:
        say(f"vms/ dirs here that are neither sources nor holt.excluded (not collected): {', '.join(other)}")
else:
    names = [g for g in here if g not in EXCLUDED]
    how = f"the vms/ dirs here, less {', '.join(sorted(EXCLUDED))}"
bad = [n for n in names if not n or not all(c.isalnum() or c in "._-" for c in n)]
if bad:
    say("not guest names:", ", ".join(map(repr, bad)))
    sys.exit(2)
say(f"sources: {len(names)} ({how})")
cands = []
for h in hosts:
    cmd = ["python3", "-I", "-", records, vms, ",".join(names)]
    if h != me:
        cmd = ssh + [targets[h], " ".join(shlex.quote(c) for c in cmd)]
    p = subprocess.run(cmd, input=inventory, capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        say(f"{h}: the inventory failed (exit {p.returncode}): {p.stderr.strip()[-300:]}")
        sys.exit(1)
    n = 0
    for l in p.stdout.splitlines():
        c = json.loads(l)
        c["host"] = h
        cands.append(c)
        n += 1
    say(f"{h}: {n} candidate dumps")
os.makedirs(out, mode=0o700, exist_ok=True)
os.chmod(out, 0o700)
def fetch(c, dest):
    if c["host"] == me:
        cmd = ["cp", "-p", c["src"], dest]
    else:
        cmd = ["scp", "-q", "-p", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"{targets[c['host']]}:{c['src']}", dest]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600).returncode == 0
def utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
rows, missing = [], []
for g in names:
    mine = [c for c in cands if c["guest"] == g]
    order = sorted([c for c in mine if c["result"] == "PASS"], key=lambda c: (c["report_mtime"], c["src_mtime"], c["host"]), reverse=True)
    order += sorted([c for c in mine if c["result"] != "PASS" and c["source_ok"]], key=lambda c: (c["report_mtime"], c["src_mtime"], c["host"]), reverse=True)
    d = os.path.join(out, g)
    part = os.path.join(d, ".src.json.part")
    got = None
    # An earlier collection's dump never stands in for this one's.
    if os.path.exists(os.path.join(d, "src.json")):
        os.unlink(os.path.join(d, "src.json"))
    if order:
        os.makedirs(d, mode=0o700, exist_ok=True)
    for c in order:
        if os.path.exists(part):
            os.unlink(part)
        if not fetch(c, part):
            say(f"{g}: couldn't copy {c['host']}:{c['src']}; trying the next")
            continue
        os.chmod(part, 0o600)
        with open(part, "rb") as f:
            raw = f.read()
        try:
            v = json.loads(raw)
            ok = isinstance(v, dict) and v.get("format") == "bko-holt" and isinstance(v.get("storage"), dict)
        except ValueError:
            ok = False
        if not ok:
            say(f"{g}: {c['host']}:{c['src']} isn't a whole bko-holt dump; trying the next")
            continue
        os.replace(part, os.path.join(d, "src.json"))
        img = c.get("image")
        image = "unknown" if not img else ("same" if all(i["now"] == i["was"] for i in img) else "changed")
        got = {"guest": g, "host": c["host"], "run": c["run"], "result": "PASS" if c["result"] == "PASS" else "FAIL-after-source",
               "report_time": utc(c["report_mtime"]), "generated": str(v.get("generated") or "-"),
               "tool_version": str((v.get("tool") or {}).get("version") or "-"), "generation": c.get("generation") or "-",
               "image": image, "raw_sha256": hashlib.sha256(raw).hexdigest()}
        break
    if os.path.exists(part):
        os.unlink(part)
    if got is None and os.path.isdir(d) and not os.listdir(d):
        os.rmdir(d)
    if got is None:
        missing.append(g)
        say(f"{g}: no dump ({len(mine)} candidates, none usable)")
        continue
    rows.append(got)
    say(f"{g}: {got['host']} {got['run']} ({got['result']}, {got['report_time']}, bko-holt {got['tool_version']}, image {got['image']})")
for e in sorted(os.listdir(out)):
    p = os.path.join(out, e)
    if e not in names and not e.startswith(".") and os.path.isdir(p) and not os.path.islink(p) and os.path.isfile(os.path.join(p, "src.json")):
        shutil.rmtree(p)
        say(f"{e}: an earlier collection's dump, and not a source now: removed")
cols = ["guest", "host", "run", "result", "report_time", "generated", "tool_version", "generation", "image", "raw_sha256"]
tmp = os.path.join(out, ".provenance.tsv.part")
with open(tmp, "w") as f:
    f.write("\t".join(cols) + "\n")
    for r in rows:
        f.write("\t".join(r[k] for k in cols) + "\n")
os.chmod(tmp, 0o600)
os.replace(tmp, os.path.join(out, "provenance.tsv"))
flagged = [r["guest"] for r in rows if r["result"] != "PASS"]
say(f"{len(rows)} of {len(names)} collected into {out}" + (f"; FAIL-after-source: {', '.join(flagged)}" if flagged else "")
    + (f"; image changed since the dump: {', '.join(r['guest'] for r in rows if r['image'] == 'changed')}" if any(r["image"] == "changed" for r in rows) else ""))
if missing:
    say("no dump for: " + ", ".join(missing))
    sys.exit(1)
EOF

umask 077
python3 -I - "$OUT" "$ME" "$TARGETS" "$SOURCES" "$RECORDS" "$VMS" "$INVENTORY" "$CLASSES" <<<"$COLLECT"
