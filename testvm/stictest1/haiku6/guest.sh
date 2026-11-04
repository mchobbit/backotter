# SticTest1 haiku6, guest side (bash on Haiku, as "user", uid 0).  Writes ~/ck/out/.
cd ~/ck || exit 1
OUT=~/ck/out; rm -rf "$OUT"; mkdir -p "$OUT"
R=~/ck/rustic
export TZ=UTC0 RUSTIC_PASSWORD=stictest RUSTIC_REPOSITORY=$HOME/ck/repo RUSTIC_NO_PROGRESS=true
ex() { echo "EXIT $1 $2" >>"$OUT/exits.txt"; }
step() { echo "== $* ($(date +%T))"; }
# The two spare disks: raw devices other than the boot disk's.
boot=$(df /boot | awk '/Device:/ {print $NF}')
bootdir=${boot%/*}
spares=()
for d in $(ls /dev/disk/*/*/raw /dev/disk/*/*/*/raw /dev/disk/*/*/*/*/raw 2>/dev/null | sort -u); do
    [[ ${d%/*} == "$bootdir" || $boot == "$d" ]] && continue
    spares+=("$d")
done
echo "boot $boot; spares ${spares[*]}"
[[ ${#spares[@]} -ge 2 ]] || { echo "need two spare disks"; exit 1; }
A=${spares[0]} B=${spares[1]}
fmt() {  # DEV MOUNTPOINT NAME: a fresh BFS, as the guest's /boot (default block size, indices)
    unmount "$2" 2>/dev/null
    mkfs -t bfs -q "$1" "$3" >/dev/null 2>&1 || mkfs -t bfs "$1" "$3" </dev/null || return 1
    mkdir -p "$2" && mount -t bfs "$1" "$2"
}
step format; fmt "$A" /bkoA bkoA && fmt "$B" /bkoB bkoB || exit 1
df /boot /bkoA /bkoB >"$OUT/df.txt" 2>&1

step fixture
python3 ck.py make /bkoA >"$OUT/make.json" 2>"$OUT/make.err"; ex make $?
src=/bkoA/anc1/anc2/src
lim='{}'
# What the OS/file system can't keep: probe the fixture as made.
python3 - "$src" >"$OUT/limits.json" <<'EOF'
import json, os, sys
src = sys.argv[1]; lim = {}
st = os.lstat(os.path.join(src, "sparse.bin"))
if st.st_blocks * 512 >= st.st_size:
    lim["sparse"] = "BFS allocates holes: sparse.bin (%d bytes) has %d blocks, no file has holes on BFS" % (st.st_size, st.st_blocks)
p = os.path.join(src, "plain.txt"); a = os.lstat(p).st_atime_ns
os.utime(p, ns=(1234567891234567891, os.lstat(p).st_mtime_ns))
if os.lstat(p).st_atime_ns != 1234567891234567891:
    lim["atimes"] = "BFS keeps no access time (utimensat of atime doesn't stick; st_atime is %d)" % os.lstat(p).st_atime_ns
    lim["symlink-atimes"] = lim["atimes"]
os.utime(p, ns=(a, os.lstat(p).st_mtime_ns))
f = os.path.join(src, "fifo"); m = os.lstat(f).st_mtime_ns
try: os.utime(f, ns=(m + 10**9, m + 10**9))
except OSError: pass
if os.lstat(f).st_mtime_ns == m:
    lim["fifo-times"] = "Haiku's utimensat leaves a FIFO's times as they are (no error), as testvm/haiku/attrs.py notes"
if not os.path.exists(os.path.join(src, "hl_b")):
    lim["hardlinks"] = "BFS refuses link(2)"
if not any(n.startswith(b"bad") for n in os.listdir(os.fsencode(src))):
    lim["nonutf8"] = "Haiku refuses non-UTF-8 names"
print(json.dumps(lim))
EOF
# Device nodes and sockets: can they exist here?
python3 -c 'import os; os.mknod("/bkoA/dev-probe", 0o20644, os.makedev(1, 2))' >"$OUT/mknod.txt" 2>&1; echo "mknod exit $?" >>"$OUT/mknod.txt"
rm -f /bkoA/dev-probe
python3 -c 'import socket; s=socket.socket(socket.AF_UNIX); s.bind("/bkoA/anc1/anc2/src/sock"); print("socket made")' >"$OUT/socket.txt" 2>&1
ls -l "$src/sock" >>"$OUT/socket.txt" 2>&1
python3 ck.py dump "$src" "$OUT/src.json"
python3 ck.py dump /bkoA "$OUT/srcall.json"
SKIP=$(python3 -c 'import json,sys; print("atime" if "atimes" in json.load(open(sys.argv[1])) else "")' "$OUT/limits.json")
# ramfs (the guest's /boot/system/var/shared_memory): what it keeps
python3 - >"$OUT/ramfs-probe.txt" 2>&1 <<'EOF'
import os
d = "/boot/system/var/shared_memory/ck-probe"; os.makedirs(d, exist_ok=True)
f = d + "/f"; open(f, "w").close(); os.utime(f, ns=(1600000000123456789, 1600000000987654321))
print("file mtime_ns", os.lstat(f).st_mtime_ns)
os.symlink("f", d + "/l"); print("symlink mode", oct(os.lstat(d + "/l").st_mode))
os.utime(d + "/l", ns=(1600000000123456789, 1600000000987654321), follow_symlinks=False); print("symlink mtime_ns", os.lstat(d + "/l").st_mtime_ns)
os.remove(f); os.remove(d + "/l"); os.rmdir(d)
EOF
listattr -l /boot/home/Desktop >"$OUT/real-desktop-attrs.txt" 2>&1

step backup
$R init >"$OUT/init.log" 2>&1; ex init $?
$R backup --set-atime yes "$src" >"$OUT/backup.log" 2>&1; ex backup $?
python3 ck.py dump "$src" "$OUT/src-after.json"
python3 ck.py cmp "$OUT/src.json" "$OUT/src-after.json" source "$SKIP" >"$OUT/cmp-source.txt"

step restore1 fresh BFS
$R restore --numeric-id "latest" /bkoB/r1 >"$OUT/restore1.log" 2>&1; ex restore1 $?
python3 ck.py dump "/bkoB/r1$src" "$OUT/r1.json"
python3 ck.py cmp "$OUT/src.json" "$OUT/r1.json" restore1 "$SKIP" >"$OUT/cmp-restore1.txt"
{ grep -q "socket made" "$OUT/socket.txt" && echo made || echo "not made"; [ -e "/bkoB/r1$src/sock" ] && echo restored || echo skipped; } >"$OUT/sockets.txt"
python3 ck.py dump /bkoB/r1/bkoA "$OUT/r1all.json"
python3 ck.py anc "$OUT/srcall.json" "$OUT/r1all.json" >"$OUT/cmp-ancestors.txt"

step restore2 over a changed tree
python3 ck.py mutate "/bkoB/r1$src" >"$OUT/mutate.log" 2>&1; ex mutate $?
$R restore --numeric-id --delete "latest:$src" "/bkoB/r1$src" >"$OUT/restore2.log" 2>&1; ex restore2 $?
python3 ck.py dump "/bkoB/r1$src" "$OUT/r2.json"
python3 ck.py cmp "$OUT/src.json" "$OUT/r2.json" restore2 "$SKIP" >"$OUT/cmp-restore2.txt"

step restore3 onto /boot
rm -rf /boot/home/ck/r3
$R restore --numeric-id "latest:$src" /boot/home/ck/r3 >"$OUT/restore3.log" 2>&1; ex restore3 $?
python3 ck.py dump /boot/home/ck/r3 "$OUT/r3.json"
python3 ck.py cmp "$OUT/src.json" "$OUT/r3.json" restore3 "$SKIP" >"$OUT/cmp-restore3.txt"

step restore4 onto ramfs
r4=/boot/system/var/shared_memory/ck-r4
rm -rf $r4
$R restore --numeric-id "latest:$src" $r4 >"$OUT/restore4.log" 2>&1; ex restore4 $?
python3 ck.py dump $r4 "$OUT/r4.json"
python3 ck.py cmp "$OUT/src.json" "$OUT/r4.json" restore4-ramfs "$SKIP" >"$OUT/cmp-restore4.txt"
rm -rf $r4

step snapshot naming and determinism
python3 ck.py treewalk "$R" latest "$src" "$OUT/tree.json" >"$OUT/treewalk.txt" 2>&1
python3 ck.py tree "$OUT/tree.json" "$OUT/src.json" >"$OUT/naming.txt" 2>&1
$R backup --set-atime yes "$src" >"$OUT/backup2.log" 2>&1; ex backup2 $?
python3 ck.py treewalk "$R" latest "$src" "$OUT/tree2.json" >>"$OUT/treewalk.txt" 2>&1
python3 - "$OUT/tree.json" "$OUT/tree2.json" >"$OUT/tree-diff.txt" <<'EOF'
import json, sys
a, b = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
fields = {}
for p in set(a) | set(b):
    x, y = a.get(p, {}), b.get(p, {})
    for k in set(x) | set(y):
        if x.get(k) != y.get(k): fields.setdefault(k, []).append(p)
print(json.dumps({k: (len(v), sorted(v)[:5]) for k, v in fields.items()}))
EOF
for p in /bkoA /bkoA/anc1 /bkoA/anc1/anc2; do echo "== $p"; $R cat tree "latest:$p" 2>&1; done >"$OUT/anc-tree.txt"
$R snapshots --json >"$OUT/snapshots.json" 2>"$OUT/snapshots.err"
python3 - "$OUT/snapshots.json" >"$OUT/same-tree.txt" <<'EOF'
import json, sys
t = []
def walk(x):
    if isinstance(x, dict):
        if "tree" in x and "time" in x: t.append((x["time"], x["tree"]))
        for v in x.values(): walk(v)
    elif isinstance(x, list):
        for v in x: walk(v)
walk(json.load(open(sys.argv[1])))
t = [x[1] for x in sorted(t)]
print(("same tree %s" % t[-1]) if len(t) >= 2 and t[-1] == t[-2] else "trees differ: %s" % t)
EOF
$R check --read-data >"$OUT/check.log" 2>&1; ex check $?
$R cat config >"$OUT/config.json" 2>&1

step non-root
chown 4400:4400 /bkoB
mkdir -p /bkoB/nrrepo /bkoB/nrdst && chown 4400:4400 /bkoB/nrrepo /bkoB/nrdst
cp $R /bkoB/rustic-nr && chmod 755 /bkoB/rustic-nr
asuser() { python3 -c 'import os, sys; os.setgroups([]) if hasattr(os, "setgroups") else None; os.setgid(4400); os.setuid(4400); os.environ["HOME"] = "/bkoB/nrdst"; os.execvp(sys.argv[1], sys.argv[1:])' "$@"; }
asuser id >"$OUT/nr-id.txt" 2>&1
RUSTIC_REPOSITORY=/bkoB/nrrepo asuser /bkoB/rustic-nr init >"$OUT/nr-init.log" 2>&1
RUSTIC_REPOSITORY=/bkoB/nrrepo asuser /bkoB/rustic-nr backup --set-atime yes /bkoA/nr >"$OUT/nr-backup.log" 2>&1; ex nr-backup $?
RUSTIC_REPOSITORY=/bkoB/nrrepo asuser /bkoB/rustic-nr restore --numeric-id latest:/bkoA/nr /bkoB/nrdst/nr >"$OUT/nr-restore.log" 2>&1; ex nr-restore $?
python3 ck.py dump /bkoA/nr "$OUT/nr-src.json"
python3 ck.py dump /bkoB/nrdst/nr "$OUT/nr-dst.json"
python3 ck.py cmp "$OUT/nr-src.json" "$OUT/nr-dst.json" nonroot "$SKIP" >"$OUT/cmp-nonroot.txt"

grep -iH 'warn\|error' "$OUT"/backup.log "$OUT"/restore[1234].log "$OUT"/nr-backup.log "$OUT"/nr-restore.log "$OUT"/backup2.log >"$OUT/warnings.txt"
$R --version >"$OUT/version.txt" 2>&1
step done
