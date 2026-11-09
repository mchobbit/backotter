#!/usr/bin/env bash
# H1's raw corpus (docs/design/test2.md "Shared pieces to build", H1): for
# each of HoltTest2's sources, the newest source dump HoltTest1 recorded
# (/bkoscratch/holttest1d/RUN/GUEST/dumps/src.json) on either host, PASS runs
# first.  Run it on Chopin, as m.  Raw dumps hold the guests' serials, UUIDs
# and hostnames: they stay in OUT on this host, never on the Mac or in the
# repository; holt/tests/corpus_build.rs makes the redacted corpus from them.
#
#   collect-dumps.sh [--out DIR] [--hosts H,H] [--sources FILE] [--records DIR] [--vms DIR]
#       writes OUT/GUEST/src.json (the raw dump, 0600) for each source and
#       OUT/provenance.tsv (guest host run result report_time generated
#       tool_version generation image raw_sha256), OUT 0700.  OUT holds this
#       collection only: a guest's earlier dump goes before its candidates
#       are tried, and a dump of a guest that isn't a source now is removed.
#       A guest without a dump is named and the exit is 1, after the others
#       are collected.
#   collect-dumps.sh --scan CORPUS [--out DIR] [--hosts H,H] [--vms DIR]
#       leakscan.py over CORPUS (holt/tests/corpus's layout) on each host,
#       with that host's own strings (passphrase files, disk serials, LAN
#       addresses, MACs, the git e-mail), which never leave it: here in a
#       strings file in OUT (0600, OUT 0700), and with each raw dump's own
#       identifiers from OUT (every corpus src.json needs its raw twin); on
#       the other hosts in a private temporary directory that the corpus (it
#       is public) and leakscan.py are copied to.  The strings files and that
#       directory are deleted at the end.  Exit 0 clean, 1 findings, 2 a scan
#       that couldn't be completed; findings name the file, the JSON path and
#       the class, never the text.
#
# OUT defaults to T2_HOST_ROOT/h1-raw (T2_HOST_ROOT: /bkoscratch/test2).
# HOSTS (default chopin,cinqcent) are reached as m@HOST.local, this host
# directly; Chopin copies Cinqcent's dumps with scp, host to host.  The
# sources are HoltTest2's: testvm/test2/classes.json's holt.families and
# holt.no_family (45 on 2026-11-08).  Without that file, this host's vms/
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
# Exit 0 done, 1 a guest without a dump (or --scan findings), 2 usage.
set -uo pipefail

HERE=$(dirname "$(realpath "$0")")
LEAKSCAN=$HERE/../t2lib/leakscan.py
OUT="" HOSTS="chopin,cinqcent" SOURCES="" RECORDS=/bkoscratch/holttest1d VMS='~/backotter-work/vms' SCAN=""
CLASSES=$HERE/../classes.json
usage() { sed -n '9,27p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
while [[ $# -gt 0 ]]; do
    [[ $# -ge 2 ]] || usage
    case $1 in
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
[[ $HOSTS =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]] || { echo "collect-dumps.sh: --hosts H,H (short names)" >&2; exit 2; }
[[ $RECORDS == /* && $RECORDS != *[[:space:]\'\"]* ]] || { echo "collect-dumps.sh: --records: an absolute path without spaces or quotes" >&2; exit 2; }
[[ $VMS != *[[:space:]\'\"]* ]] || { echo "collect-dumps.sh: --vms: a path without spaces or quotes" >&2; exit 2; }
[[ -z $SOURCES || -r $SOURCES ]] || { echo "collect-dumps.sh: no sources file $SOURCES" >&2; exit 2; }
OUT=${OUT:-${T2_HOST_ROOT:-/bkoscratch/test2}/h1-raw}
OUT=$(realpath -m "$OUT")
ME=$(hostname -s)
export PYTHONDONTWRITEBYTECODE=1
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15)

if [[ -n $SCAN ]]; then
    [[ -d $SCAN ]] || { echo "collect-dumps.sh: --scan $SCAN: not a directory" >&2; exit 2; }
    [[ -d $OUT ]] || { echo "collect-dumps.sh: no raw dumps in $OUT (collect them first)" >&2; exit 2; }
    [[ -r $LEAKSCAN ]] || { echo "collect-dumps.sh: no $LEAKSCAN" >&2; exit 2; }
    SCAN=$(realpath "$SCAN")
    umask 077
    chmod 0700 "$OUT" || exit 2
    STR="" RT="" RH=""
    cleanup() {
        [[ -n $STR ]] && rm -f "$STR"
        [[ -n $RT && -n $RH ]] && "${SSH[@]}" "m@$RH.local" "rm -rf '$RT'" </dev/null
    }
    trap cleanup EXIT
    trap 'exit 2' INT TERM HUP
    worst=0
    IFS=, read -ra hs <<<"$HOSTS"
    for h in "${hs[@]}"; do
        if [[ $h == "$ME" ]]; then
            STR=$(mktemp "$OUT/.h1-strings.XXXXXX") || exit 2
            python3 -I "$LEAKSCAN" strings --vms "$VMS" >"$STR" || { echo "collect-dumps.sh: strings on $h failed" >&2; exit 2; }
            echo "collect-dumps.sh: $h: $(wc -l <"$STR") host strings, and the raw dumps' identifiers from $OUT"
            python3 -I "$LEAKSCAN" scan --strings "$STR" --raw "$OUT" "$SCAN"
            rc=$?
            rm -f "$STR"
            STR=""
        else
            # The corpus goes to H; H's strings stay there.
            RH=$h
            RT=$("${SSH[@]}" "m@$h.local" 'umask 077; mktemp -d "${TMPDIR:-/tmp}/h1-scan.XXXXXX"' </dev/null) || { echo "collect-dumps.sh: $h: no temporary directory" >&2; exit 2; }
            [[ $RT == /*/h1-scan.* && $RT != *[[:space:]\'\"]* ]] || { echo "collect-dumps.sh: $h: an odd temporary directory" >&2; exit 2; }
            "${SSH[@]}" "m@$h.local" "cat > '$RT/leakscan.py'" <"$LEAKSCAN" || { echo "collect-dumps.sh: $h: copying leakscan.py failed" >&2; exit 2; }
            tar -C "$SCAN" -cf - . | "${SSH[@]}" "m@$h.local" "mkdir '$RT/corpus' && tar -C '$RT/corpus' -xf -" || { echo "collect-dumps.sh: $h: copying the corpus failed" >&2; exit 2; }
            "${SSH[@]}" "m@$h.local" "cd '$RT' && umask 077 && PYTHONDONTWRITEBYTECODE=1 python3 -I leakscan.py strings --vms $VMS > strings.jsonl && echo \"collect-dumps.sh: $h: \$(wc -l < strings.jsonl) host strings\" && python3 -I leakscan.py scan --strings strings.jsonl corpus" </dev/null
            rc=$?
            "${SSH[@]}" "m@$h.local" "rm -rf '$RT'" </dev/null && RT=""
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

# The collector, on this host: python3 -I - OUT ME HOSTS SOURCES-FILE RECORDS VMS.
read -r -d '' COLLECT <<'EOF'
import hashlib, json, os, shlex, shutil, subprocess, sys, time
out, me, hosts, sources_file, records, vms, inventory, classes = sys.argv[1:9]
hosts = hosts.split(",")
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
        cmd = ssh + [f"m@{h}.local", " ".join(shlex.quote(c) for c in cmd)]
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
        cmd = ["cp", "--preserve=timestamps", c["src"], dest]
    else:
        cmd = ["scp", "-q", "-p", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"m@{c['host']}.local:{c['src']}", dest]
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
python3 -I - "$OUT" "$ME" "$HOSTS" "$SOURCES" "$RECORDS" "$VMS" "$INVENTORY" "$CLASSES" <<<"$COLLECT"
