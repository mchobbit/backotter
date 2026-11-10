#!/usr/bin/env bash
# SticTest1 targeted check of ubuntu26-zfsmirror (Ubuntu 26.04, encrypted
# mirrored ZFS root rpool with acltype=posix xattr=sa, ext4 /boot on md RAID1).
# Never a whole-disk backup: in a throwaway copy of the guest (--ram, overlays
# in /mnt/bko-ram/PREFIX/ubuntu26-zfsmirror), with the guest's own ZFS and
# e2fsprogs, it runs, first alone, then the other two at once:
#   fs_roundtrip.py zfs-ds   torture.sh's tree (+ the guest's own real files,
#                            below) in file systems of the root pool rpool
#   fs_roundtrip.py ext4     the same on an ext4 loop image (/boot's type)
#   nonroot-rt.sh linux      a backup as root, restores as uid 4321 (ZFS, ext4)
# Only the source and target file systems are on rpool: the repositories
# (about 1.1 GiB each, torture.sh's 1 GiB of random data) and the ext4 images
# are on a spare disk (vdc, /var/tmp in the guest), so the mirror doesn't write
# them twice. rpool's two overlays (7.7 GiB at most, measured 2026-11-04) and
# the spare (sparse, with discard passed through, and the zfs-ds run's
# repositories gone before the ext4 run) are all in
# /mnt/bko-ram/PREFIX/ubuntu26-zfsmirror. The guest's ARC is capped at 1 GiB.
# fs_roundtrip.py does all four directions (restic->restic, rustic->rustic,
# restic->rustic, rustic->restic) into fresh file systems, a restore over a
# changed copy (immutable/append-only files in the way) and a rerun over the
# unchanged result, fsmeta.py compares, sockets skipped, and node identity of
# the two tools' snapshots (which hold the backed-up path's ancestors too).
# torture.sh here is a wrapper: torture.sh itself, then copies of the guest's
# own files: ping/mtr-packet (security.capability), /var/log/journal and its
# machine-id dir (setgid, POSIX ACLs access+default), system.journal
# (user.crtime_usec), random-seed (user.random-seed-creditable), a real file
# with holes, /home/tester (uid > 999), device nodes with rdev 0, a trusted.*
# xattr on a symlink.
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  holds restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Prints PASS/FAIL/LIMIT lines, writes results.json into the log dir
# (~/backotter-work/stictest1/ubuntu26-zfsmirror/logs/PREFIX-TIME/), exit 0 if
# nothing FAILs.  Takes a guest slot (Chopin 3 on 2700-2799, Cinqcent 3 on
# 2900-2999) and cmp-run.sh --reserve 23 MEM itself (MEM: the guest's MEMORY,
# MB, default 4096, in GiB + 0.5): 23 GiB of RAM disk for rpool's overlays and
# the spare, an estimate; ramdisk-peak-kib in the log dir is the run's peak.
set -uo pipefail
G=ubuntu26-zfsmirror self=$(realpath "$0")
WORK=${BKO_WORK:-$HOME/backotter-work}
[[ $# -ge 3 ]] || { sed -n '2,31p' "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
[[ -x $BIN/restic-bko && -x $BIN/rustic-bko ]] || { echo "no restic-bko/rustic-bko in $BIN" >&2; exit 2; }
# cleanup removes the scratch from a RAM disk other sessions share: it has to
# stay inside /mnt/bko-ram/PREFIX.
[[ $PREFIX =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "check.sh: PREFIX must be a plain name" >&2; exit 2; }
[[ ! -L /mnt/bko-ram/$PREFIX ]] || { echo "check.sh: /mnt/bko-ram/$PREFIX is a symlink" >&2; exit 2; }

if [[ -z ${STICK_INNER:-} ]]; then
    # shellcheck source=/dev/null
    source "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    case $(hostname -s) in
        chopin) slot_take 3 2700-2799 ;;
        *) slot_take 3 2900-2999 ;;
    esac
    STICK_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 23 "$(awk -v m="${MEMORY:-4096}" 'BEGIN { print m / 1024 + 0.5 }')" "$self" "$@"; rc=$?
    slot_give
    exit $rc
fi

here=${BKO_TESTVM:-$WORK/testvm}/compare
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
dir=$WORK/vms/$G
LOGS=${STC_LOGS:-$WORK/stictest1/$G/logs}/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$LOGS"
SCR=/mnt/bko-ram/$PREFIX/$G   # its own, even where PREFIX names a shared directory (tmp, holttest1d)
rm -rf "$SCR"; mkdir -p "$SCR"
export TMPDIR=$SCR RAM_DIR=$SCR   # vm-run --ram's overlays (not /dev/shm); nothing here chroots
SPARE=$SCR/spare.img   # vdc: /var/tmp in the guest (repositories, ext4 images), off the root pool; sparse
truncate -s 24G "$SPARE"
cleanup() {
    trap '' INT TERM HUP PIPE   # a later signal can't cut it short
    # The peak watcher goes last: if this shell is killed outright meanwhile,
    # it stops the VM and removes SCR.
    vm_halt 2>/dev/null; exec 9>&- 2>/dev/null
    rm -rf "$SCR" "$LOGS/console.in"; rmdir "/mnt/bko-ram/$PREFIX" 2>/dev/null
    kill "${PEAK:-}" 2>/dev/null
}
trap cleanup EXIT
trap 'trap "" INT TERM HUP PIPE; exit 1' INT TERM HUP   # (a second signal can't skip cleanup)
# What the run uses of the RAM disk, sampled: SCR (rpool's overlays, the spare)
# and any unlinked file QEMU holds there; "10s", since drive.py counts a
# check.sh's "sleep 10" as a slot wait.  If this shell is killed outright, it
# stops the VM and removes SCR, unless a later run made SCR anew.
peak() {
    local m=0 k q f ino vm="^qemu-system.*${SCR//./\\.}/"
    ino=$(stat -c %i "$SCR")
    while sleep 10s && kill -0 $$ 2>/dev/null; do
        k=$(du -sk "$SCR" 2>/dev/null | cut -f1)
        for q in $(pgrep -f "^qemu-system.*:$PORT-:22"); do
            for f in "/proc/$q/fd"/*; do
                [[ $(readlink "$f") == "$SCR/"*"(deleted)" ]] && k=$((k + $(stat -L -c %b "$f" 2>/dev/null || echo 0) / 2))
            done
        done
        ((k > m)) && m=$k && echo "$m" >"$LOGS/ramdisk-peak-kib"
    done
    kill -0 $$ 2>/dev/null && return
    [[ $(stat -c %i "$SCR" 2>/dev/null) == "$ino" ]] || return
    pkill -f "$vm"; rm -rf "$SCR"; rmdir "/mnt/bko-ram/$PREFIX" 2>/dev/null
}
peak & PEAK=$!

boot() {
    local pass opts=() deadline answered=0 n
    pass=$(cat "$dir/zfs-passphrase")
    if [[ -n $(ss -Hltn "sport = :$PORT" 2>/dev/null) ]]; then echo "port $PORT is in use" >&2; return 1; fi
    if [[ -f $dir/vm-run-options ]]; then read -ra opts <"$dir/vm-run-options" || true; fi
    rm -f "$LOGS/console.in"; mkfifo "$LOGS/console.in"
    exec 9<>"$LOGS/console.in"
    VM_GUEST=$G VM_PORT=$PORT VM_KEY=$dir/id_ed25519
    (cd "$dir" && SSH_PORT=$PORT exec setsid "$VM_LIB_DIR/vm-run" "${opts[@]}" --ram \
        --memory "${MEMORY:-4096}" "$G.qcow2" -- -drive "file=$SPARE,if=virtio,format=raw,cache=unsafe,discard=unmap,detect-zeroes=unmap" <"$LOGS/console.in" >"$LOGS/serial.log" 2>"$LOGS/vm-run.log") &
    VM_PID=$!
    deadline=$((SECONDS + 600))
    until T=15 vm_ssh true 2>/dev/null; do
        if ! kill -0 "$VM_PID" 2>/dev/null; then tail -n 5 "$LOGS/vm-run.log" >&2; VM_PID=""; return 1; fi
        n=$(grep -aoi 'enter passphrase' "$LOGS/serial.log" | wc -l)
        if [[ $n -gt $answered ]]; then sleep 1; printf '%s\r' "$pass" >&9; answered=$n; fi
        if [[ $SECONDS -ge $deadline ]]; then echo "sshd didn't come up" >&2; vm_halt; return 1; fi
        sleep 3
    done
}

echo "== $G: booting a throwaway copy on port $PORT (logs $LOGS)" >&2
boot || { echo "FAIL boot: the throwaway copy didn't come up ($LOGS/serial.log)"; exit 1; }

# The guest's copy of the harness; torture.sh a wrapper adding the guest's own files.
cat >"$SCR/torture.sh" <<'EOF'
#!/bin/bash
# torture.sh, then copies of the guest's own files (SticTest1 check.sh).
"$(dirname "$0")/torture-orig.sh" "$@"; rc=$?
R=$1/root/bko-torture/guest-real
mkdir -p "$R"
try() { "$@" 2>/dev/null || echo "guest-real: skipped (unsupported here): $*" >&2; }
for f in /usr/bin/ping /usr/bin/mtr-packet /usr/lib/x86_64-linux-gnu/gstreamer1.0/gstreamer-1.0/gst-ptp-helper; do
    [[ -e $f ]] && try cp -a --preserve=all "$f" "$R/"
done
J=/var/log/journal M=$(cat /etc/machine-id)
copy_dir_meta() {  # SRC DST: owner, mode (setgid), POSIX ACLs access+default, xattrs
    mkdir -p "$2"; chown --reference="$1" "$2"; chmod --reference="$1" "$2"
    getfacl --omit-header -p "$1" 2>/dev/null | try setfacl -M- "$2"
    touch -r "$1" "$2"
}
copy_dir_meta $J "$R/journal"
copy_dir_meta $J/$M "$R/journal/$M"
[[ -f $J/$M/system.journal ]] && try cp -a --preserve=all $J/$M/system.journal "$R/journal/$M/"
touch -r $J/$M "$R/journal/$M"; touch -r $J "$R/journal"
for d in $(getfacl -R -s -p /var/lib /etc 2>/dev/null | sed -n 's/^# file: //p' | head -n 3); do
    [[ -d $d ]] && copy_dir_meta "$d" "$R/acl$(echo "$d" | tr / _)"
done
try cp -a --preserve=all /var/lib/systemd/random-seed "$R/"
h=$(python3 - <<'PY'
import os
for top in ("/usr/lib", "/var/lib", "/usr/share", "/var/log"):
    for d, _, fs in os.walk(top):
        for n in fs:
            p = os.path.join(d, n)
            try:
                if os.path.islink(p) or not os.path.isfile(p): continue
                s = os.path.getsize(p)
                if s == 0 or s > 64 << 20: continue
                with open(p, "rb") as fh:
                    if os.lseek(fh.fileno(), 0, os.SEEK_HOLE) < s:
                        print(p); raise SystemExit
            except OSError:
                pass
PY
)
[[ -n $h ]] && try cp -a --sparse=always "$h" "$R/holey-$(basename "$h")" && echo "$h" >"$R/holey-source"
[[ -d /home/tester ]] && try cp -a --preserve=all /home/tester "$R/home-tester"
try mknod "$R/chardev-rdev0" c 0 0
try mknod "$R/blockdev-rdev0" b 0 0
mkdir -p "$R/sticky-setgid"; chmod 3775 "$R/sticky-setgid"
ln -s ping "$R/link-with-xattr"; try setfattr -h -n trusted.bko-link -v symlinkattr "$R/link-with-xattr"
try touch -h -d '2001-02-03 04:05:06.123456789' "$R/link-with-xattr"
exit $rc
EOF
vm_ssh 'rm -rf /root/bko && mkdir -p /root/bko/testvm/compare /root/bko/bin /root/bko/fs-roundtrip &&
    mkfs.ext4 -q -F /dev/vdc && mount /dev/vdc /var/tmp && chmod 1777 /var/tmp &&
    for r in ext4 zfs-ds; do mkdir /var/tmp/fsrt-$r && ln -s /var/tmp/fsrt-$r /root/bko/fs-roundtrip/$r || exit 1; done &&
    echo $((1 << 30)) >/sys/module/zfs/parameters/zfs_arc_max' || exit 1
for f in fs_roundtrip.py compare.py fsmeta.py sparse-rt.sh rt-common.sh nonroot-rt.sh nonroot-inner.sh; do
    vm_put "$here/$f" "/root/bko/testvm/compare/$f" || exit 1
done
vm_put "$here/torture.sh" /root/bko/testvm/compare/torture-orig.sh && vm_put "$SCR/torture.sh" /root/bko/testvm/compare/torture.sh &&
    vm_put "$BIN/restic-bko" /root/bko/bin/restic-bko && vm_put "$BIN/rustic-bko" /root/bko/bin/rustic-bko || exit 1
vm_sh 'chmod 755 /root/bko/testvm/compare/*.sh /root/bko/bin/*
    uname -r; zfs version | tr "\n" " "; echo; zfs get -H -o name,property,value encryption,acltype,xattr rpool; zfs list -H -o name,avail rpool; df -h /var/tmp | tail -n 1
    /root/bko/bin/restic-bko version | head -n 1; /root/bko/bin/rustic-bko --version
    if ! command -v setfacl >/dev/null || ! command -v setfattr >/dev/null; then
        { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q attr acl; } >/root/bko/apt.log 2>&1 || echo "apt failed"
    fi' >"$LOGS/guest.log" 2>&1
cat "$LOGS/guest.log" >&2

echo "== $G: fs_roundtrip zfs-ds, then ext4 and nonroot-rt linux at once" >&2
# In between, the zfs-ds run's repositories go (the tar below leaves them out
# anyway), and fstrim hands their blocks back to the RAM disk.
T=930 vm_ssh 'cd /root/bko && export RESTIC_BKO=/root/bko/bin/restic-bko RUSTIC_BKO=/root/bko/bin/rustic-bko
    date +"%T zfs-ds"; timeout 480 python3 -I testvm/compare/fs_roundtrip.py zfs-ds >zfs-ds.log 2>&1; echo $? >zfs-ds.rc
    (cd /var/tmp/fsrt-zfs-ds && rm -rf restic-bko rustic-bko restic-to-rustic rustic-to-restic); fstrim -v /var/tmp
    date +"%T zfs-ds rc $(cat zfs-ds.rc); ext4, nonroot"; zfs list -H -o name,used,avail rpool; df -h /var/tmp | tail -n 1
    (timeout 420 python3 -I testvm/compare/fs_roundtrip.py ext4 >ext4.log 2>&1; echo $? >ext4.rc) &
    (RESTIC=bin/restic-bko RUSTIC=bin/rustic-bko timeout 420 bash testvm/compare/nonroot-rt.sh linux >nonroot.log 2>&1; echo $? >nonroot.rc) &
    wait; date +"%T ext4 rc $(cat ext4.rc), nonroot rc $(cat nonroot.rc)"' >"$LOGS/run.log" 2>&1
cat "$LOGS/run.log" >&2
T=300 vm_ssh 'cd /root/bko && tar -cf - *.log *.rc nonroot/*.log guest-real-list 2>/dev/null
    for r in zfs-ds ext4; do tar -chf - --exclude="$r/restic-bko" --exclude="$r/rustic-bko" --exclude="$r/restic-to-rustic" --exclude="$r/rustic-to-restic" -C fs-roundtrip $r 2>/dev/null; done' |
    tar -C "$LOGS" -xif - 2>/dev/null
vm_halt
exec 9>&-

python3 - "$LOGS" "$FILTER" <<'PY'
import json, os, re, sys
logs, flt = sys.argv[1], sys.argv[2]
out, res = [], {}
def say(kind, need, detail):
    if flt and not re.search(flt, need, re.I): return
    out.append(f"{kind} {need}: {detail}"); res[need] = {"verdict": kind, "detail": detail}
def load(p):
    try: return json.load(open(os.path.join(logs, p)))
    except Exception: return None
fr = {fs: load(f"{fs}/results.json") for fs in ("zfs-ds", "ext4")}
T4 = ["restic-bko", "rustic-bko", "restic-to-rustic", "rustic-to-restic"]
def kinds(r, key):  # which fsmeta difference kinds, per tool
    return {t: sorted((r["tools"].get(t, {}).get(key) or {}).keys()) for t in T4}
for fs, label in (("zfs-ds", "ZFS root pool rpool (encrypted mirror, acltype=posix, xattr=sa)"), ("ext4", "ext4 (/boot's type)")):
    r = fr[fs]
    if not r or "tools" not in r:
        say("FAIL", f"Round trip, four directions, on {label}", f"no results.json ({fs}.log)"); continue
    if r.get("skipped"):
        say("FAIL", f"Round trip, four directions, on {label}", f"skipped: {r['skipped']}"); continue
    cov = r.get("coverage", {})
    bad = {t: (r["tools"][t].get("restore", {}).get("exit"), sorted((r["tools"][t].get("diff_all") or {}).keys())) for t in T4 if t in r["tools"]}
    fails = {t: v for t, v in bad.items() if v[0] != 0 or v[1]}
    missing = [t for t in T4 if t not in r["tools"]]
    say("FAIL" if fails or missing else "PASS", f"Round trip, four directions, on {label}: every attribute restored",
        (f"failing {fails} missing {missing}" if fails or missing else "all four exit 0, fsmeta diff none") + "; coverage: " + ", ".join(f"{k} {v}" for k, v in sorted(cov.items())))
    over = {t: (r["tools"][t].get("over_restore", {}).get("exit"), r["tools"][t].get("rerun_restore", {}).get("exit"),
                sorted((r["tools"][t].get("over_diff_all") or {}).keys()), sorted((r["tools"][t].get("rerun_diff_all") or {}).keys())) for t in T4 if t in r["tools"]}
    of = {t: v for t, v in over.items() if v[0] != 0 or v[1] != 0 or v[2] or v[3]}
    say("FAIL" if of else "PASS", f"Restore again over an already-restored tree with Linux immutable/append-only files ({fs}): both tools exit the same way",
        f"(over exit, rerun exit, over diff, rerun diff) per direction: {over}")
    nd = r.get("snapshot_node_diff (restic-bko vs rustic-bko)")
    n = None if nd is None else sum(map(len, nd.values()))
    say("PASS" if n == 0 else "FAIL", f"Same repository format: restic-bko and rustic-bko snapshots node-identical ({fs}, ancestors of the backed-up path included)",
        "0 node differences" if n == 0 else f"{n}: {json.dumps(nd)[:600]}")
    s = r.get("sockets", {})
    say("PASS" if s.get("ok") else "FAIL", f"Sockets skipped by both ({fs})", "; ".join(s.get("failures", [])) or f"source {s.get('source')}, none in a snapshot or restored")
    sb = r.get("sparse_blocks", {})
    sf = {t: v["failures"] for t, v in sb.items() if v.get("failures")}
    bm = {k: v for k, v in (r.get("blocks_mismatch") or {}).items() if v}
    say("FAIL" if sf or bm or not sb else "PASS", f"Sparse files / holes ({fs}): marked unix.sparse, st_blocks per SPEC3 3, same blocks by both",
        f"failures {sf} blocks_mismatch {bm}" if sf or bm else "marked " + ", ".join(p.rsplit('/', 1)[-1] for p in (r.get("sparse_marked") or {}).get("restic-bko", [])))
    xo = r.get("xattr_order", {})
    say("PASS" if xo.get("ok") else "FAIL", f"xattrs stored sorted by name ({fs})", "; ".join(xo.get("failures", [])[:4]) or "sorted")
    sk = r.get("torture_skipped", [])
    if sk: say("LIMIT", f"Fixture items the guest/fs can't set ({fs})", "; ".join(x.split(': ', 2)[-1] for x in sk)[:900])
# The guest's own files, one need each, from the zfs-ds and ext4 records.
r = fr["zfs-ds"]
real_ok = r and "tools" in r
for fs in ("zfs-ds", "ext4"):
    try:
        before = json.load(open(os.path.join(logs, fs, "before.json")))
    except Exception:
        continue
    gr = {p: e for p, e in before.items() if "/guest-real" in p}
    def has(pred): return sorted(p.split("guest-real/")[-1] for p, e in gr.items() if pred(e))
    items = {
        "security.capability (ping, mtr-packet, gst-ptp-helper)": has(lambda e: "security.capability" in (e.get("xattrs") or {})),
        "POSIX ACLs access+default (journal, setgid dirs)": has(lambda e: "system.posix_acl_default" in (e.get("xattrs") or {})),
        "user.* xattrs (user.crtime_usec, user.random-seed-creditable)": has(lambda e: any(k.startswith("user.") for k in (e.get("xattrs") or {}))),
        "a real file with holes": has(lambda e: e.get("holes")),
        "device nodes with rdev 0": has(lambda e: e.get("rdev") == [0, 0]),
        "uid > 999 (/home/tester)": has(lambda e: 999 < e.get("uid", 0) < 60000),
        "trusted.* xattr on a symlink": has(lambda e: e.get("type") == "symlink" and any(k.startswith("trusted.") for k in (e.get("xattrs") or {}))),
    }
    rr = fr[fs]
    clean = rr and "tools" in rr and all(not (rr["tools"].get(t, {}).get("diff_all")) and rr["tools"].get(t, {}).get("restore", {}).get("exit") == 0 for t in T4)
    for k, paths in items.items():
        if not paths:
            say("LIMIT" if fs == "ext4" and "trusted" in k else "FAIL", f"Guest's own {k} ({fs})", "not in the fixture (not copyable here)")
        else:
            say("PASS" if clean else "FAIL", f"Guest's own {k} ({fs})", f"{len(paths)} in the fixture ({', '.join(paths[:4])}), " + ("restored identically in all four directions" if clean else "the round trip has differences"))
# Non-root.
nr = open(os.path.join(logs, "nonroot.log"), errors="replace").read() if os.path.exists(os.path.join(logs, "nonroot.log")) else ""
summ = re.findall(r"^== summary.*?(?=^########|\Z)", nr, re.M | re.S)
if not summ:
    say("FAIL", "Non-root restore (uid 4321) on ZFS and ext4, the two tools behave the same", "no summary in nonroot.log")
else:
    for i, s in enumerate(summ):
        fs = ("zfs", "ext4")[i] if i < 2 else str(i)
        ex = re.findall(r"^\s+(\S+): exit (\d+)", s, re.M)
        vs = re.findall(r"^\s+(\S+) vs (\S+): (\d+) names differ", s, re.M)
        # S10: restic as non-root may exit 1 (EPERM it can't avoid); rustic warns, exits 0.
        badx = [d for d, e in ex if not (e == "0" or (d.endswith("-restic") and e == "1"))]
        badv = [f"{a} vs {b} {n}" for a, b, n in vs if n != "0"]
        say("FAIL" if badx or badv or not ex else "PASS", f"Non-root restore (uid 4321) on {fs}: exit 0, the two tools' restores the same",
            " ".join(s.split())[:700])
print("\n".join(out))
json.dump(res, open(os.path.join(logs, "results.json"), "w"), indent=1)
sys.exit(1 if any(v["verdict"] == "FAIL" for v in res.values()) else 0)
PY
rc=$?
echo "logs: $LOGS" >&2
exit $rc
