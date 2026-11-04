#!/usr/bin/env bash
# SticTest1 targeted check for guest "arch" (Arch Linux, ext4 root and /boot).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR      holds restic-bko and rustic-bko for Linux (roundN/bin/linux)
#   PORT         the throwaway guest's SSH port (ours only)
#   PREFIX       name prefix of our scratch: /mnt/bko-ram/PREFIX/arch-check
#   NEED_FILTER  extended regex: print only the needs it matches (all run)
#
# Boots a throwaway overlay of ~/backotter-work/vms/arch (vm-run --snapshot,
# TMPDIR in our RAM dir) through guest-slot.sh and cmp-run.sh --reserve; in
# it, check-inner.sh (next to this script) builds the fixture on an ext4 made
# by the guest's own mkfs.ext4 with its root's features, backs it up with both
# tools, restores in the four directions, restores over existing immutable/
# append-only trees, and runs one non-root backup+restore.  The ext4 images
# come back to the host (tar -S), where fsmeta.py (the committed one, next to
# this script) compares them read-only.  Prints "PASS|FAIL|LIMIT need: ...",
# writes results.json in the log dir (~/backotter-work/stictest1/arch/logs/
# TIMESTAMP), exits 0 if nothing FAILs.  Needs: sudo, python3, qemu/KVM.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
GUEST=arch

if [[ ${1:-} == --guest-phase ]]; then
    # Run under the slot and the reservation: boot, run the inner part, fetch the images.
    shift; BIN=$1 PORT=$2 W=$3 LOGS=$4
    export TMPDIR=$W/tmp; mkdir -p "$TMPDIR" "$W/img"
    . "$WORK/testvm/vm-lib.sh"
    trap vm_halt EXIT
    vm_boot $GUEST "$PORT" "$LOGS/vm" --snapshot --memory 4096 || { echo "guest didn't come up"; exit 3; }
    vm_sh 'mkdir -p /root/bko-st' || exit 3
    for f in "$BIN/restic-bko" "$BIN/rustic-bko" "$here/check-inner.sh" "$WORK/testvm/compare/rt-common.sh"; do
        vm_put "$f" "/root/bko-st/$(basename "$f")" || { echo "copying $f failed"; exit 3; }
    done
    T=720 vm_sh 'bash /root/bko-st/check-inner.sh' >"$LOGS/inner.log" 2>&1; echo "inner exit $?" >>"$LOGS/inner.log"
    T=300 vm_ssh 'cd /root/bko-st/img && tar -cSf - *.img' | tar -xSf - -C "$W/img" || echo "fetching images failed" >>"$LOGS/inner.log"
    T=60 vm_ssh 'tar -cf - -C /root/bko-st logs nodes.restic nodes.rustic anc.restic anc.rustic' 2>/dev/null | tar -xf - -C "$LOGS" 2>/dev/null
    exit 0
fi

[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(cd "$1" && pwd) PORT=$2 PREFIX=$3 FILTER=${4:-.}
[[ -x $BIN/restic-bko && -x $BIN/rustic-bko ]] || { echo "no restic-bko/rustic-bko in $BIN" >&2; exit 2; }
W=/mnt/bko-ram/$PREFIX/arch-check
LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}/$(date +%Y%m%d-%H%M%S)
mkdir -p "$LOGS"; sudo rm -rf "$W"; mkdir -p "$W/m"
case $(hostname -s) in
    chopin) SLOTMAX=2 SLOTRANGE=2700-2799 ;;
    *) SLOTMAX=3 SLOTRANGE=2900-2999 ;;
esac
cleanup() {
    for m in "$W"/m/*; do mountpoint -q "$m" && sudo umount "$m"; done
    sudo rm -rf "$W"; rmdir "$(dirname "$W")" 2>/dev/null
}
trap cleanup EXIT
echo "check.sh $GUEST: bin $BIN ($(cat "$BIN/restic-commit" 2>/dev/null | head -n1); $(cat "$BIN/rustic-commit" 2>/dev/null | head -n1)), port $PORT, logs $LOGS"

# --- guest phase, under a slot and a reservation ---------------------------------------
. "$WORK/testvm/compare/guest-slot.sh"
slot_take $SLOTMAX $SLOTRANGE
HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 6 "$here/check.sh" --guest-phase "$BIN" "$PORT" "$W" "$LOGS" \
    >"$LOGS/guest-phase.log" 2>&1
gp=$?
slot_give
echo "guest phase exit $gp; inner: $(tail -n 1 "$LOGS/inner.log" 2>/dev/null)"

# --- host phase: fsmeta on the images, read-only ----------------------------------------
# arch's own fsmeta.py is testvm/compare's of cfc875e, before 512-byte holes, ZFS and bcachefs.
FS="sudo python3 $here/fsmeta.py"
for img in "$W"/img/*.img; do
    [[ -f $img ]] || continue
    n=$(basename "$img" .img); mkdir -p "$W/m/$n"
    sudo mount -o ro,noload,noatime,loop "$img" "$W/m/$n" || echo "mount $n failed"
done
TREE=anc1/anc2/tree
S=$W/m/src
if sudo test -d "$S/$TREE"; then
    $FS record "$S/$TREE" "$LOGS/src.json"
    for d in rr uu ru ur ovr ovu; do
        if sudo test -d "$W/m/$d/bko/src/$TREE"; then
            $FS record "$W/m/$d/bko/src/$TREE" "$LOGS/$d.json"
            $FS compare "$LOGS/src.json" "$LOGS/$d.json" --json "$LOGS/cmp-$d.json" >"$LOGS/cmp-$d.txt"
            for a in "" anc1 anc1/anc2; do
                echo "$d ${a:-.} src $(sudo stat -c '%u:%g %a %.9Y' "$S/$a") dst $(sudo stat -c '%u:%g %a %.9Y' "$W/m/$d/bko/src/$a")"
            done >>"$LOGS/ancestors.txt"
            [[ -n $(sudo find "$W/m/$d/bko/src/$TREE" -maxdepth 1 -type s) ]] && echo "$d" >>"$LOGS/sockets-restored.txt"
        fi
    done
fi
if sudo test -d "$W/m/nr/src"; then
    $FS record "$W/m/nr/src" "$LOGS/nr-src.json"
    for d in out-rr out-uu; do
        $FS record "$W/m/nr/$d" "$LOGS/nr-$d.json"
        $FS compare "$LOGS/nr-src.json" "$LOGS/nr-$d.json" --json "$LOGS/cmp-nr-$d.json" >"$LOGS/cmp-nr-$d.txt"
    done
fi
sudo chown -R "$(id -u):$(id -g)" "$LOGS"

# --- verdicts --------------------------------------------------------------------------------
python3 - "$LOGS" "$FILTER" "$gp" <<'PY'
import json, os, re, sys
logs, flt, gp = sys.argv[1], sys.argv[2], int(sys.argv[3])
inner = open(os.path.join(logs, "inner.log"), errors="replace").read() if os.path.exists(os.path.join(logs, "inner.log")) else ""
def val(tag):
    m = re.findall(r"^" + tag + r" ?(.*)$", inner, re.M)
    return m[-1] if m else None
try:
    socks = {p for p, e in json.load(open(os.path.join(logs, "src.json"))).items() if e["type"] == "socket"}
except Exception:
    socks = set()
def cmp(d):
    # The source's socket is "missing" from every restore by design (both tools skip sockets; checked below).
    p = os.path.join(logs, f"cmp-{d}.json")
    if not os.path.exists(p): return None
    c = json.load(open(p))
    m = c["differences"].get("missing")
    if m:
        m["examples"] = [x for x in m["examples"] if x not in socks]
        m["count"] -= len([x for x in socks])
        if m["count"] <= 0: del c["differences"]["missing"]
    return c
out = []
def add(st, need, detail): out.append({"status": st, "need": need, "detail": detail})
for l in re.findall(r"^R (PASS|FAIL|LIMIT) ([^:]*): (.*)$", inner, re.M):
    add(*l)
dirs = {"rr": "restic->restic", "uu": "rustic->rustic", "ru": "restic->rustic", "ur": "rustic->restic"}
src = cmp("rr") or cmp("uu") or {}
cov = src.get("coverage", {})
exits = dict(re.findall(r"^EXIT (\w+) (\d+)", inner, re.M))
for d, name in dirs.items():
    c = cmp(d)
    if c is None:
        add("FAIL", f"restore {name}", "no restored tree to compare (see inner.log)"); continue
    diff = c["differences"]; e = exits.get(d, "?")
    ok = e == "0" and not diff
    add("PASS" if ok else "FAIL", f"restore {name}",
        f"exit {e}, fsmeta {c['paths']} paths " + ("identical" if not diff else "; ".join(f"{k} {v['count']} e.g. {v['examples'][:2]}" for k, v in diff.items())))
kinds = [
    ("xattrs user/trusted/security on ext4 (empty, binary, large values)", ["user-xattr", "trusted-xattr", "other-xattr"], ["user-xattr", "trusted-xattr", "other-xattr"]),
    ("security.capability (the guest's newuidmap/newgidmap and setcap)", ["capability"], ["capability"]),
    ("POSIX ACLs access + default (incl. the guest's /var/log/journal ACLs)", ["acl"], ["acl"]),
    ("Linux inode flags on ext4 (immutable, append, nodump, noatime, sync, dirsync, data-journal)", ["inode flags"], ["immutable", "append", "nodump", "noatime"]),
    ("ownership incl. ids with no name, set-id and sticky modes", ["owner", "mode"], ["setuid/setgid/sticky"]),
    ("hard links", ["hard links"], ["hard-linked"]),
    ("sparse files on ext4", ["holes lost", "holes gained", "allocation lost"], ["with holes"]),
    ("device nodes (incl. rdev 0) and FIFOs", ["device number", "type"], ["chardev", "blockdev", "fifo"]),
    ("symlinks with their own times and xattrs", ["symlink target", "mtime (symlinks)", "atime (symlinks)"], ["symlink"]),
    ("non-UTF-8 and odd names, empty and deep directories", ["missing", "extra"], ["dir"]),
    ("ns mtimes and atimes (files, dirs)", ["mtime", "mtime (dirs)", "atime", "atime (dirs)"], ["atime != mtime"]),
    ("file contents", ["content"], ["file"]),
]
for need, ks, covk in kinds:
    bad = []
    for d, name in dirs.items():
        c = cmp(d)
        if c is None: bad.append(f"{name}: not compared"); continue
        for k in ks:
            if k in c["differences"]:
                bad.append(f"{name}: {k} {c['differences'][k]['count']} e.g. {c['differences'][k]['examples'][:2]}")
    have = {k: cov.get(k, 0) for k in covk}
    if not any(have.values()):
        bad.append(f"not in the fixture ({have})")
    add("FAIL" if bad else "PASS", need, ("; ".join(bad) if bad else f"same in all four directions; source has {have}"))
# sockets
sb = (val("SOCKBAD") or "").strip()
sr = open(os.path.join(logs, "sockets-restored.txt")).read().split() if os.path.exists(os.path.join(logs, "sockets-restored.txt")) else []
rm = val("RESTOREMSG") or ""
sockmsg = [x for x in rm.split() if "sock" in x.lower()]
made = "fixture: socket made" in inner
st = "PASS" if made and not sb and not sr and not sockmsg else "FAIL"
add(st, "sockets skipped by both tools", f"socket in fixture: {made}; in a snapshot/backup message: {sb or 'no'}; restored in: {sr or 'none'}; restore messages naming it: {sockmsg or 'none'}")
# ancestors
ad = (val("ANCDIFF") or "").strip(); ar = val("ANCREAL") or ""
anct = open(os.path.join(logs, "ancestors.txt")).read() if os.path.exists(os.path.join(logs, "ancestors.txt")) else ""
restored_bad = [l for l in anct.splitlines() if l.split(" src ")[1].split(" dst ")[0] != l.split(" dst ")[1]] if anct else ["no restores to check"]
ok = not ad and "restic=yes" in ar and "rustic=yes" in ar
add("PASS" if ok else "FAIL", "ancestor directories of a backup path stored with their real metadata, the same by both tools",
    f"stored real metadata: {ar}; node diff restic vs rustic: {ad or 'none'}; whole-snapshot restores where the dirs above differ from the source: {len(restored_bad)} ({'; '.join(restored_bad[:4])})")
# restore over existing immutable/append-only
over = dict((m[0], (m[1], m[2])) for m in re.findall(r"^OVER (\w+) exits ([\d ]+) MSG ?(.*)$", inner, re.M))
det, okall = [], True
for d, name in (("ovr", "restic-bko"), ("ovu", "rustic-bko")):
    c = cmp(d)
    ex, msg = over.get(d, ("?", ""))
    diff = c["differences"] if c else {"not compared": {"count": 0, "examples": []}}
    ok = ex.split() == ["0", "0", "0"] and not diff and not re.search(r"panic|crash", msg, re.I)
    okall &= ok
    det.append(f"{name}: exits (fresh, unchanged, changed) {ex.strip()}, fsmeta after: " + ("identical" if not diff else "; ".join(f"{k} {v['count']} e.g. {v['examples'][:2]}" for k, v in diff.items())) + (f", messages: {msg[:300]}" if msg else ""))
add("PASS" if okall else "FAIL", "restore over an existing tree with immutable/append-only files (Linux, ext4)", " | ".join(det))
# non-root
m = re.search(r"^NONROOT exits backup (\d+) (\d+) restore (\d+) (\d+) MSG ?(.*)$", inner, re.M)
nd = []
for d in ("out-rr", "out-uu"):
    c = cmp(f"nr-{d}")
    if c is None: nd.append(f"{d}: not compared")
    elif c["differences"]: nd.append(f"{d}: " + "; ".join(f"{k} {v['count']} e.g. {v['examples'][:2]}" for k, v in c["differences"].items()))
ok = m and m.group(1, 2, 3, 4) == ("0",) * 4 and not nd
add("PASS" if ok else "FAIL", "non-root backup and restore (uid 4321, both tools)",
    (f"exits backup restic {m.group(1)} rustic {m.group(2)}, restore {m.group(3)} {m.group(4)}; " if m else "no result; ") + ("; ".join(nd) if nd else "fsmeta identical") + (f"; messages: {m.group(5)[:300]}" if m and m.group(5) else ""))
bm = (val("BACKUPMSG") or "").strip()
add("PASS" if not bm else "FAIL", "backups say nothing (no warnings or errors)", bm or "none")
# limits
skipped = re.findall(r"^fixture: (?:skipped \(unsupported\)|not kept): (.*)$", inner, re.M)
lim = [
    ("SELinux labels and the security module's mode", "no SELinux on arch (LSMs capability,landlock,lockdown,yama,bpf); security.* xattrs are checked as xattrs"),
    ("NFSv4 ACLs", "Linux ext4 has none (POSIX ACLs only)"),
    ("BSD/FreeBSD file flags", "not on Linux; Linux inode flags checked instead"),
    ("ZFS/bcachefs/btrfs per-file options", "arch has ext4 only"),
    ("project IDs", "arch's ext4 has no project feature (chattr -p refused)"),
    ("nocow inode flag and real compression", "ext4 refuses chattr +C and doesn't compress (its c flag is kept and checked with the inode flags): " + "; ".join(s for s in skipped if "+c " in s or "+C " in s)),
    ("birth times", "ext4 keeps them but Linux can't set one: not stored on Linux (BACKOTTER.md, unix.birthtime)"),
    ("system.* xattrs other than POSIX ACLs", "none on Linux ext4"),
    ("whole-disk: restored system boots, bootloader", "out of scope: whole-disk"),
]
for n, r in lim: add("LIMIT", n, r)
if gp != 0: add("FAIL", "check ran", f"guest phase exit {gp} (see guest-phase.log)")
json.dump({"guest": "arch", "results": out}, open(os.path.join(logs, "results.json"), "w"), indent=1)
for r in out:
    if re.search(flt, r["need"]): print(f"{r['status']} {r['need']}: {r['detail']}")
sys.exit(1 if any(r["status"] == "FAIL" for r in out) else 0)
PY
rc=$?
echo "results: $LOGS/results.json"
exit $rc
