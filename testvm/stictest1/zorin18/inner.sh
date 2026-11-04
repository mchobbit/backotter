#!/usr/bin/env bash
# SticTest1, guest side (zorin18 and other Linux guests): build the fixture on
# scratch file systems of the guest's own types, back it up with restic-bko and
# rustic-bko, restore in all four directions, restore again over the restored
# tree (immutable and append-only files in it), one non-root run, and compare.
# Runs as root in /root/st with restic-bko, rustic-bko, fsmeta.py, rt-common.sh.
# Prints "CK<TAB>NEED<TAB>PASS|FAIL|LIMIT<TAB>DETAIL" lines; the rest is log.
#
# Env: FSLIST (scratch file system types, default: nilfs2 tmpfs), FILTER (a
# regex: only needs matching it are reported).
set -uo pipefail
cd /root/st
ST=/root/st
RESTIC=$ST/restic-bko RUSTIC=$ST/rustic-bko
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
FSLIST=${FSLIST:-nilfs2 tmpfs ext4}
mkdir -p /mnt/bkst/bin; cp $RESTIC $RUSTIC /mnt/bkst/bin/; chmod 755 /mnt/bkst /mnt/bkst/bin /mnt/bkst/bin/*
NRRESTIC=/mnt/bkst/bin/restic-bko NRRUSTIC=/mnt/bkst/bin/rustic-bko
FILTER=${FILTER:-.}
W=/mnt/bkst            # everything of ours in the guest
mkdir -p $W/img $W/repos
mountpoint -q $W/img || mount -t tmpfs -o size=1500m,mode=0755 bkst-img $W/img
mountpoint -q $W/repos || mount -t tmpfs -o size=1g bkst-repos $W/repos
export RT_TMP=$W/rt; mkdir -p $RT_TMP
. $ST/rt-common.sh

ck() {  # NEED STATUS DETAIL
    [[ $1 =~ $FILTER ]] || return 0
    printf 'CK\t%s\t%s\t%s\n' "$1" "$2" "$3"
}
log() { echo "--- $*"; }

# ---- scratch file systems -------------------------------------------------
LOOPS=()
mkfs_of() {  # FSTYPE NAME: a fresh file system of FSTYPE mounted at $W/NAME
    local t=$1 n=$2 img=$W/img/$2.img dev
    umount $W/$n 2>/dev/null; mkdir -p $W/$n
    if [[ $t == tmpfs ]]; then mount -t tmpfs -o size=1500m,mode=0755 bkst-$n $W/$n; return; fi
    for l in "${LOOPS[@]}"; do :; done
    rm -f $img; truncate -s 1500M $img
    dev=$(losetup -f --show $img) || return 1
    LOOPS+=($dev)
    case $t in
        nilfs2) mkfs.nilfs2 -q -f $dev >/dev/null ;;
        ext4) mkfs.ext4 -q -F $dev ;;
        vfat) mkfs.vfat $dev >/dev/null ;;
        *) mkfs -t $t $dev >/dev/null ;;
    esac || return 1
    mount -t $t $dev $W/$n
}
unmount_of() {  # NAME
    local dev; dev=$(findmnt -n -o SOURCE $W/$1 2>/dev/null)
    umount $W/$1 2>/dev/null
    # nilfs2 leaves its cleaner running a moment
    [[ $dev == /dev/loop* ]] && { losetup -d $dev 2>/dev/null; rm -f $W/img/$1.img; }
    true
}

# ---- xattr helpers (python: no dependence on attr/acl/libcap tools) ------
pyx() { python3 - "$@" <<'PY'
import os, sys
op = sys.argv[1]
if op == "set":            # set PATH NAME HEXVALUE
    os.setxattr(sys.argv[2], sys.argv[3], bytes.fromhex(sys.argv[4]), follow_symlinks=False)
elif op == "copy":         # copy FROM TO: every xattr of FROM onto TO
    for n in os.listxattr(sys.argv[2], follow_symlinks=False):
        os.setxattr(sys.argv[3], n, os.getxattr(sys.argv[2], n, follow_symlinks=False), follow_symlinks=False)
elif op == "has":          # has PATH NAME
    sys.exit(0 if sys.argv[3] in os.listxattr(sys.argv[2], follow_symlinks=False) else 1)
PY
}
try() { "$@" 2>/dev/null || { echo "fixture: refused here: $*" >&2; return 1; }; }
flag() {  # FLAG PATH: set, and report whether it is kept
    chattr "+$1" "$2" 2>/dev/null || { echo "fixture: chattr +$1 refused: $2" >&2; return 1; }
    [[ $(lsattr -d "$2" 2>/dev/null | cut -d' ' -f1) == *"$1"* ]] || { echo "fixture: +$1 not kept: $2" >&2; return 1; }
}

# Real examples from the guest's own files (found by the inventory).
REAL_ACL=""; for p in /run/log/journal /var/log/journal /var/log/journal/*/ ; do
    [[ -e $p ]] && python3 -c 'import os,sys; os.getxattr(sys.argv[1],"system.posix_acl_access")' "$p" 2>/dev/null && { REAL_ACL=$p; break; }; done
REAL_CAP=""; for p in /usr/bin/ping /usr/bin/mtr-packet /usr/lib/x86_64-linux-gnu/gstreamer1.0/gstreamer-1.0/gst-ptp-helper /usr/bin/newuidmap; do
    [[ -e $p ]] && python3 -c 'import os,sys; os.getxattr(sys.argv[1],"security.capability")' "$p" 2>/dev/null && { REAL_CAP=$p; break; }; done
REAL_SPARSE=$(find /usr /var /etc -xdev -type f -size +16k -printf '%b %s %p\n' 2>/dev/null | awk '$1*512+65536<$2 {print $3; exit}')

# ---- the fixture ------------------------------------------------------------
# FIXTURE ROOT: ROOT/anc1/anc2/data, the backed-up path; anc1 and anc2 are
# its ancestors, with metadata of their own.  Writes ROOT/.made (what took).
fixture() {
    local R=$1 T=$1/anc1/anc2/data made=$1/.made
    : >$made
    mkdir -p "$T"
    ok() { echo "$1" >>$made; }
    # Ownership with no names, special modes.
    touch "$T/no-such-user"; chown 1234:5678 "$T/no-such-user" && ok owner-noname
    install -m 0644 /dev/null "$T/setuid"; chown 1234:5678 "$T/setuid"; chmod 4755 "$T/setuid" && ok setuid
    install -m 0644 /dev/null "$T/setgid"; chmod 2755 "$T/setgid"
    mkdir -p "$T/sticky-dir"; chmod 1777 "$T/sticky-dir"
    mkdir -p "$T/setgid-dir"; chmod 2775 "$T/setgid-dir"; chown 0:4322 "$T/setgid-dir"
    install -m 0600 /dev/null "$T/mode-0600"; install -m 0000 /dev/null "$T/mode-0000"
    # POSIX ACLs, access and default; a real one copied from the guest.
    touch "$T/acl-file"; mkdir -p "$T/acl-dir"
    if try setfacl -m u:1234:rwx,g:5678:r "$T/acl-file"; then ok acl-access; fi
    try setfacl -m u:1234:rx "$T/acl-dir"
    if try setfacl -d -m u:1234:rwx,g:5678:rx "$T/acl-dir"; then ok acl-default; fi
    if [[ -n $REAL_ACL ]]; then mkdir -p "$T/real-acl-dir"; try pyx copy "$REAL_ACL" "$T/real-acl-dir" && ok acl-real; fi
    # xattrs: user (empty, binary, large), trusted (also on a symlink), security.
    touch "$T/xattr-file"
    if try pyx set "$T/xattr-file" user.backotter 68656c6c6f; then ok xattr-user
        pyx set "$T/xattr-file" user.binary 00ff10fe7f; pyx set "$T/xattr-file" user.empty ""
        try pyx set "$T/xattr-file" user.large "$(head -c 3000 /dev/zero | od -An -v -tx1 | tr -d ' \n' | sed 's/00/78/g')"
        mkdir -p "$T/xattr-dir"; pyx set "$T/xattr-dir" user.dir 6469720a
    fi
    try pyx set "$T/xattr-file" trusted.backotter 726f6f742d6f6e6c79 && ok xattr-trusted
    ln -sfn xattr-file "$T/xattr-symlink"; try pyx set "$T/xattr-symlink" trusted.onlink 6c6e6b && ok xattr-symlink
    try pyx set "$T/xattr-file" security.bkotest 7365632d76616c && ok xattr-security
    # A capability: the guest's own (copied with every xattr it has) and a made one.
    if [[ -n $REAL_CAP ]]; then cp "$REAL_CAP" "$T/cap-real" && try pyx copy "$REAL_CAP" "$T/cap-real" && ok cap-real; fi
    cp /usr/bin/true "$T/cap-binary"; try setcap cap_net_raw,cap_net_admin+ep "$T/cap-binary" && ok cap-made
    # Hard links, symlinks (dangling, absolute, with own times).
    echo linked >"$T/hardlink-a"; ln -f "$T/hardlink-a" "$T/hardlink-b"
    mkdir -p "$T/sub"; ln -f "$T/hardlink-a" "$T/sub/hardlink-c" && ok hardlink
    ln -sfn hardlink-a "$T/symlink"; ln -sfn /nonexistent/target "$T/symlink-dangling"
    ln -sfn /etc/hostname "$T/symlink-abs"
    # Special files: devices (rdev 0 too), FIFO, socket.
    mknod "$T/chardev" c 1 3 && mknod "$T/blockdev" b 7 0 && mknod "$T/chardev-rdev0" c 0 0 && ok devices
    mkfifo "$T/fifo" && ok fifo
    rt_mksock "$T/socket" && ok socket
    # Sparse files: holes at start, middle and end; all hole; small hole;
    # a hole in the last block; written zeros; preallocated; a real one.
    truncate -s 64M "$T/sparse-mid"; echo start | dd of="$T/sparse-mid" conv=notrunc status=none
    echo middle | dd of="$T/sparse-mid" bs=1M seek=32 conv=notrunc status=none
    echo end | dd of="$T/sparse-mid" bs=1M seek=63 conv=notrunc status=none
    truncate -s 16M "$T/sparse-start"; echo tail | dd of="$T/sparse-start" bs=1M seek=15 conv=notrunc status=none
    head -c 1M /dev/urandom >"$T/sparse-end"; truncate -s 9M "$T/sparse-end"
    truncate -s 8M "$T/sparse-allhole"
    head -c 256K /dev/urandom >"$T/sparse-small"; truncate -s +64K "$T/sparse-small"; head -c 256K /dev/urandom >>"$T/sparse-small"
    head -c 8M /dev/zero >"$T/zeros"
    fallocate -l 8M "$T/preallocated" 2>/dev/null && ok prealloc
    if [[ -n $REAL_SPARSE ]]; then cp --sparse=always "$REAL_SPARSE" "$T/sparse-real" && ok sparse-real; fi
    sync -f "$T"
    [[ $(stat -c %b "$T/sparse-mid") -lt 1000 ]] && ok sparse
    # Names: spaces, newline, non-UTF-8, 250 characters; deep and empty dirs.
    touch "$T/name with spaces" "$T/new"$'\n'"line" "$T/"$'\xff\xfe'"-not-utf8" "$T/$(printf 'n%.0s' {1..250})"
    mkdir -p "$T/"$'\xe9'"-latin1-dir"; touch "$T/"$'\xe9'"-latin1-dir/f"
    mkdir -p "$T/deep$(printf '/level%s' {1..40})"; touch "$T/deep$(printf '/level%s' {1..40})/bottom"
    mkdir -p "$T/empty-dir"; touch "$T/empty"
    # Project IDs.
    echo project >"$T/projid-file"; try chattr -p 42 "$T/projid-file" && ok projid
    mkdir -p "$T/projid-dir"; try chattr +P -p 43 "$T/projid-dir" && ok projinherit
    # Times: far past, far future, nanoseconds, atime apart, symlinks', dirs'.
    touch -d '1970-01-02 00:00:00.123456789' "$T/old"
    touch -d '2100-01-01 12:00:00.987654321' "$T/future"
    echo atime >"$T/atime"; mkdir -p "$T/atime-dir"; ln -sfn atime "$T/atime-symlink"
    for p in "$T/atime" "$T/atime-dir" "$T/atime-symlink" "$T/symlink-dangling"; do
        touch -h -m -d '2002-03-04 05:06:07.111111111' "$p"; touch -h -a -d '2003-04-05 06:07:08.222222222' "$p"
    done
    # Inode flags, last: noatime, sync, dirsync, nodump, nocow, compress,
    # append-only, immutable (files and a directory).
    touch "$T/noatime"; flag A "$T/noatime" && ok flag-A
    touch "$T/sync"; flag S "$T/sync" && ok flag-S
    mkdir -p "$T/dirsync-dir"; flag D "$T/dirsync-dir" && ok flag-D
    touch "$T/nodump"; flag d "$T/nodump" && ok flag-d
    touch "$T/nocow"; flag C "$T/nocow" && ok flag-C
    touch "$T/compress"; flag c "$T/compress" && ok flag-c
    echo append >"$T/append-only"; flag a "$T/append-only" && ok flag-a
    echo fixed >"$T/immutable"; flag i "$T/immutable" && ok flag-i
    mkdir -p "$T/immutable-dir"; touch "$T/immutable-dir/inside"; flag i "$T/immutable-dir" && ok flag-i-dir
    touch -d '2001-02-03 04:05:06.5' "$T"
    # The directories above the backed-up path.
    chown 4321:4322 "$R/anc1"; chmod 0751 "$R/anc1"; chown 1000:1000 "$R/anc1/anc2"; chmod 0700 "$R/anc1/anc2"
    pyx set "$R/anc1" user.ancestor 616e63 2>/dev/null && ok xattr-ancestor
    setfacl -m u:1234:rx "$R/anc1/anc2" 2>/dev/null && ok acl-ancestor
    touch -d '2006-06-06 06:06:06.666' "$R/anc1/anc2"; touch -d '2005-05-05 05:05:05.555' "$R/anc1"
    sync -f "$T"
}

# ---- backup, restore, compare ---------------------------------------------
backup() {  # TOOL REPO PATH
    if [[ $1 == restic ]]; then $RESTIC -r $2 init -q && $RESTIC -r $2 backup -q --no-cache --with-atime "$3"
    else { $RUSTIC --no-progress --no-cache -r $2 init >/dev/null && $RUSTIC --no-progress --no-cache -r $2 backup --set-atime yes "$3" >/dev/null; } 2>&1 | grep -v '^\[INFO\]' >&2; return ${PIPESTATUS[0]}
    fi
}
restore() {  # TOOL REPO PATH DST
    if [[ $1 == restic ]]; then $RESTIC -r $2 --no-cache restore "latest:$3" --target "$4"
    else $RUSTIC -r $2 --no-progress --no-cache restore --numeric-id "latest:$3" "$4"
    fi
}
# fsmeta difference kinds -> needs
need_of() {
    case $1 in
        missing|extra|type|content) echo "contents, types, sizes" ;;
        mode) echo "modes incl. setuid/setgid/sticky" ;;
        owner) echo "numeric ownership incl. ids with no name" ;;
        mtime*) echo "mtimes (ns) of files, dirs, symlinks" ;;
        atime*) echo "atimes (ns) of files, dirs, symlinks" ;;
        "symlink target") echo "symlinks" ;;
        "device number") echo "device nodes incl. rdev 0" ;;
        "hard links") echo "hard links" ;;
        capability) echo "xattr security.capability" ;;
        acl) echo "POSIX ACLs (access + default)" ;;
        user-xattr) echo "xattrs user.*" ;;
        trusted-xattr) echo "xattrs trusted.*" ;;
        selinux) echo "security labels" ;;
        other-xattr) echo "xattrs security.* (other)" ;;
        "inode flags") echo "Linux inode flags" ;;
        "project ID") echo "project IDs" ;;
        holes*|"allocation lost") echo "sparse files" ;;
        *) echo "other: $1" ;;
    esac
}
ALLNEEDS=("contents, types, sizes" "modes incl. setuid/setgid/sticky" "numeric ownership incl. ids with no name"
    "mtimes (ns) of files, dirs, symlinks" "atimes (ns) of files, dirs, symlinks" "symlinks" "device nodes incl. rdev 0"
    "hard links" "Linux inode flags" "sparse files" "non-UTF-8 names, deep and empty dirs" "FIFOs")
# judge a fsmeta comparison: per need, PASS or FAIL; the socket's absence expected.
judge() {  # FS LABEL BEFORE AFTER
    local fs=$1 label=$2 out kind n bad=""
    out=$(python3 $ST/fsmeta.py compare "$3" "$4" --json $W/cmp.json 2>&1)
    echo "$out" | sed 's/^/    /'
    python3 - $W/cmp.json "$fs" "$label" <<'PY' >$W/judge.txt
import json, sys
d = json.load(open(sys.argv[1]))["differences"]
for k, v in d.items():
    ex = [e for e in v["examples"]]
    if k == "missing" and v["count"] == 1 and ex == ["socket"]:
        continue
    if k == "extra" and ex == [".nilfs"]:
        continue
    print(f"{k}\t{v['count']}\t{ex[:3]}")
PY
    declare -A failed=()
    while IFS=$'\t' read -r kind n ex; do
        [[ -n $kind ]] || continue
        local need; need=$(need_of "$kind")
        failed[$need]+="$kind $n e.g. $ex; "
    done <$W/judge.txt
    for need in "${ALLNEEDS[@]}" "${XNEEDS[@]}"; do
        if [[ -n ${failed[$need]:-} ]]; then ck "$need" FAIL "$fs $label: ${failed[$need]}"
        else ck "$need" PASS "$fs $label"; fi
        unset 'failed[$need]'
    done
    for need in "${!failed[@]}"; do ck "$need" FAIL "$fs $label: ${failed[$need]}"; done
    [[ -z $(cat $W/judge.txt) ]]
}

nodes() {  # REPO PATH OUT
    rt_nodes $RESTIC $1 $2 >$3 2>$3.err || { cat $3.err; return 1; }
}
# DIR's own metadata as fsmeta.py sees it, in rt_dot's lines for rt_dot_check:
# inode flags (but the file system's own), mode, owner; atime and mtime to the
# nanosecond; project ID; every xattr (POSIX ACLs included).  Reads nothing.
# Where xattrs aren't supported, a line says so; any other failure prints a
# part only: the caller empties the record, which rt_dot_check fails.
dot() {  # DIR
    python3 - "$1" "$ST" <<'PY'
import errno, os, sys
sys.path.insert(0, sys.argv[2])
import fsmeta
p = sys.argv[1]; st = os.lstat(p); f = fsmeta.inode_flags(p, True)
ns = lambda t: f"{t // 10**9}.{t % 10**9:09d}"
print(("" if f is None else f"flags {f & ~fsmeta.FLAGS_FS_INTERNAL:x} ") + f"mode {st.st_mode & 0o7777:o} owner {st.st_uid}:{st.st_gid}")
print("atime", ns(st.st_atime_ns)); print("mtime", ns(st.st_mtime_ns))
print("projid", fsmeta.project_id(p, True))
try:
    names = sorted(os.listxattr(p, follow_symlinks=False))
except OSError as e:
    if e.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP):
        raise
    names = []; print("xattr none: not supported")
for n in names:
    print("xattr", n, os.getxattr(p, n, follow_symlinks=False).hex())
PY
}
nodot() {  # IN.json OUT.json: a fsmeta.py record without "." (the restore target itself)
    python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); d.pop(".", None); json.dump(d, open(sys.argv[2], "w"))' "$1" "$2"
}

run_fs() {  # FSTYPE
    local fs=$1 S=$W/src D=$W/dst P REPO
    log "== $fs"
    unmount_of src; mkfs_of $fs src || { ck "scratch $fs" FAIL "can't make $fs"; return; }
    fixture $S 2>$W/fixture-$fs.err; cat $W/fixture-$fs.err | sed 's/^/    /'
    P=$S/anc1/anc2/data
    echo "made: $(tr '\n' ' ' <$S/.made)"
    # What the fixture holds decides which needs are in play on this fs.
    XNEEDS=()
    m() { grep -qx "$1" $S/.made; }
    m xattr-user && XNEEDS+=("xattrs user.*"); m xattr-trusted && XNEEDS+=("xattrs trusted.*")
    m xattr-security && XNEEDS+=("xattrs security.* (other)")
    { m cap-real || m cap-made; } && XNEEDS+=("xattr security.capability")
    { m acl-access || m acl-default; } && XNEEDS+=("POSIX ACLs (access + default)")
    { m projid || m projinherit; } && XNEEDS+=("project IDs")
    for f in xattr-user acl-access acl-default cap-made cap-real xattr-trusted xattr-symlink xattr-security projid projinherit flag-A flag-S flag-D flag-d flag-C flag-c flag-a flag-i flag-i-dir sparse prealloc sparse-real acl-real xattr-ancestor acl-ancestor; do
        m $f || echo "not on $fs: $f"
    done | tr '\n' ';' | sed 's/^/    unsupported: /'; echo
    local nolist=(); for f in xattr-user xattr-trusted acl-access cap-made projid; do m $f || nolist+=($f); done
    [[ ${#nolist[@]} -gt 0 ]] && ck "fs limits recorded" LIMIT "$fs refuses: ${nolist[*]}"
    # The backups compared, the source mounted noatime: both tools see the
    # same source (under strictatime the first tool's readlink(2) would move
    # the symlinks' atimes before the second looks, and the second would
    # store those; zorin18 attempt 2, as mx25 does).
    sync -f $P; mount -o remount,noatime $S
    python3 $ST/fsmeta.py record $P $W/before-$fs.json
    rm -rf $W/repos/*
    declare -A bfail=()
    for b in restic rustic; do
        backup $b $W/repos/$b $P >$W/backup-$b.log 2>&1; rc=$?
        [[ $rc == 0 ]] || bfail[$b]=$rc
        sed 's/^/    /' $W/backup-$b.log | head -20
        [[ $rc == 0 ]] && ck "backup exits 0" PASS "$fs $b" || ck "backup exits 0" FAIL "$fs $b exit $rc: $(head -c 300 $W/backup-$b.log)"
        grep -qi sock $W/backup-$b.log && ck "sockets skipped" FAIL "$fs $b says: $(grep -i sock $W/backup-$b.log | head -2)"
    done
    # Two more backups, the source mounted strictatime: they mustn't move atimes.
    mount -o remount,strictatime $S
    for b in restic rustic; do backup $b $W/repos/$b-at $P >/dev/null 2>&1; done
    rm -rf $W/repos/restic-at $W/repos/rustic-at
    mount -o remount,noatime $S
    python3 $ST/fsmeta.py record $P $W/after-backup-$fs.json
    if python3 $ST/fsmeta.py compare $W/before-$fs.json $W/after-backup-$fs.json >$W/atime.txt; then
        ck "backup leaves the source unchanged (atimes)" PASS "$fs, strictatime source"
    elif ! grep -v '^atime (symlinks)' $W/atime.txt | grep -q .; then
        ck "backup leaves the source unchanged (atimes)" LIMIT "$fs: readlink(2) moves a symlink's atime (no O_NOATIME for it): $(tr '\n' ' ' <$W/atime.txt | head -c 200)"
    else ck "backup leaves the source unchanged (atimes)" FAIL "$fs: $(tr '\n' ' ' <$W/atime.txt | head -c 300)"; fi
    # Same repository format: node-identical snapshots, the ancestors too.
    if nodes $W/repos/restic $P $W/n-restic && nodes $W/repos/rustic $P $W/n-rustic; then
        if rt_node_diff $W/n-restic $W/n-rustic >$W/ndiff.txt; then
            ck "same repository format (node-identical snapshots)" PASS "$fs: $(wc -l <$W/n-restic) nodes"
        else ck "same repository format (node-identical snapshots)" FAIL "$fs: $(head -5 $W/ndiff.txt | tr '\n' ' ')"; cat $W/ndiff.txt | head -30; fi
        grep -q 'type=socket' $W/n-restic $W/n-rustic && ck "sockets skipped" FAIL "$fs: socket node in a snapshot" || ck "sockets skipped" PASS "$fs: no node"
        grep -P '^(fifo|chardev|blockdev|chardev-rdev0)\t' $W/n-restic | sed 's/^/    node: /'
        # sparse marks (S3: nilfs2/JFS by block count)
        local sp=""; for f in sparse-mid sparse-start sparse-end sparse-allhole sparse-small $(m sparse-real && echo sparse-real); do
            for t in restic rustic; do grep -P "^$f\t" $W/n-$t | grep -q 'unix.sparse' || sp+="$f unmarked by $t; "; done; done
        for f in zeros $(m prealloc && echo preallocated); do
            for t in restic rustic; do grep -P "^$f\t" $W/n-$t | grep -q 'unix.sparse' && sp+="$f marked by $t; "; done; done
        if m sparse; then [[ -z $sp ]] && ck "sparse files" PASS "$fs: marks by S3" || ck "sparse files" FAIL "$fs marks: $sp"
        else ck "sparse files" LIMIT "$fs keeps no holes"; fi
        grep -P '^(sparse-small|sparse-mid)\t' $W/n-restic | cut -c1-400 | sed 's/^/    node: /'
    else ck "same repository format (node-identical snapshots)" FAIL "$fs: can't decode"; fi
    # The ancestors: anc1 and anc2 as stored, against the source and between tools.
    for a in anc1 anc1/anc2; do
        local la lb
        la=$(rt_nodes $RESTIC $W/repos/restic $S/$a 2>&1 | head -1); lb=$(rt_nodes $RESTIC $W/repos/rustic $S/$a 2>&1 | head -1)
        echo "    ancestor $a restic: ${la:0:400}"; echo "    ancestor $a rustic: ${lb:0:400}"
        local want_uid want_mode; want_uid=$(stat -c %u $S/$a); want_mode=$(printf '%d' 0$(stat -c %a $S/$a))
        if [[ "$la" == "$lb" && $la == *"uid=$want_uid"* && $la == *"mtime=@$(stat -c %Y $S/$a)."* ]]; then
            ck "ancestor directories' metadata" PASS "$fs $a: uid $want_uid, mtime, same in both"
        else ck "ancestor directories' metadata" FAIL "$fs $a: restic [${la:0:200}] rustic [${lb:0:200}] want uid=$want_uid"; fi
    done
    # Four restores, each into a fresh file system, then again over it.
    for b in restic rustic; do for r in restic rustic; do
        local lab="$b->$r"
        if [[ -n ${bfail[$b]:-} ]]; then ck "restore exits 0" FAIL "$fs $lab: not run, $b's backup failed (exit ${bfail[$b]})"; continue; fi
        unmount_of dst; mkfs_of $fs dst
        # A3: a new root holding only what mkfs/newfs makes is a fresh target,
        # and the restore gives its "." the source's.  The forks take for
        # those exactly: lost+found and .snap (dirs), .sujournal (a regular
        # file), .nilfs (an empty regular file: mkfs.nilfs2 2.2.9 and older,
        # as Zorin 18's, leave it in a new root).  held is what $D holds
        # besides those (lstat, as the forks); empty, "." is judged with the
        # rest.  Anything else mkfs left makes $D not fresh: the restore must
        # leave its "." as mkfs made it, and that is reported on its own, not
        # compared with the source.  Its times may move forward ($D is
        # relatime: the restore reads it), never back to the source's (2001).
        local held; held=$(find $D -mindepth 1 -maxdepth 1 \
            ! \( -type d \( -name lost+found -o -name .snap \) \) \
            ! \( -type f -name .sujournal \) \
            ! \( -type f -name .nilfs -size 0c \) -printf '%f ')
        [[ -z $held ]] || dot $D >$W/dot.before || : >$W/dot.before
        restore $r $W/repos/$b $P $D >$W/restore.log 2>&1; rc=$?
        [[ -z $held ]] || dot $D >$W/dot.after || : >$W/dot.after
        sed 's/^/    /' $W/restore.log | grep -v '^    \[INFO\]' | head -15
        [[ $rc == 0 ]] && ck "restore exits 0" PASS "$fs $lab" || ck "restore exits 0" FAIL "$fs $lab exit $rc: $(grep -iv info $W/restore.log | head -3 | tr '\n' ' ')"
        mount -o remount,noatime $D
        python3 $ST/fsmeta.py record $D $W/after-$fs.json --skip .nilfs --skip lost+found
        log "compare $fs $lab"
        if [[ -z $held ]]; then judge $fs "$lab" $W/before-$fs.json $W/after-$fs.json
        else
            if rt_dot_check $W/dot.before $W/dot.after forward >$W/dot.txt; then
                ck ". kept as it was by the restore into a new file system holding more than mkfs makes (not a fresh target)" PASS "$fs $lab: it held ${held}before"
            else ck ". kept as it was by the restore into a new file system holding more than mkfs makes (not a fresh target)" FAIL "$fs $lab: $(tr '\n' ' ' <$W/dot.txt | head -c 300)"; fi
            sed 's/^/    new .: /' $W/dot.txt
            # Against the source, all but "." (checked above).
            nodot $W/before-$fs.json $W/before-nodot.json; nodot $W/after-$fs.json $W/after-nodot.json
            judge $fs "$lab" $W/before-nodot.json $W/after-nodot.json
        fi
        [[ -e $D/socket ]] && ck "sockets skipped" FAIL "$fs $lab: socket restored"
        # Again over the restored tree: immutable and append-only files in it,
        # one file changed.  Holding a tree, $D isn't a fresh target: the
        # restore must leave its own "." as it is now (A3, as stock restic and
        # rustic), not give it the subfolder's.  Mode 0750 and the time now,
        # so that it differs from the source's; the restore re-links the hard
        # links in $D itself, so its mtime may move forward, never back; $D is
        # noatime, so its atime stays.
        chattr -i $D/sub 2>/dev/null; echo changed >>$D/hardlink-a 2>/dev/null || true; touch $D/zeros
        chmod 750 $D; touch $D; dot $D >$W/dot.before || : >$W/dot.before
        restore $r $W/repos/$b $P $D >$W/restore2.log 2>&1; rc=$?
        dot $D >$W/dot.after || : >$W/dot.after
        sed 's/^/    over: /' $W/restore2.log | grep -v INFO | head -10
        if rt_dot_check $W/dot.before $W/dot.after forward-mtime >$W/dot.txt; then
            ck ". kept as it was by the restore over an existing tree (not a fresh target, as stock)" PASS "$fs $lab"
        else ck ". kept as it was by the restore over an existing tree (not a fresh target, as stock)" FAIL "$fs $lab: $(tr '\n' ' ' <$W/dot.txt | head -c 300)"; fi
        sed 's/^/    over .: /' $W/dot.txt
        mount -o remount,noatime $D
        python3 $ST/fsmeta.py record $D $W/after2-$fs.json --skip .nilfs --skip lost+found
        ls -li $D/hardlink-a $D/hardlink-b $D/sub/hardlink-c | sed "s/^/    over links: /"; head -c 40 $D/hardlink-a | od -c | head -2 | sed "s/^/    over hardlink-a: /"
        # Against the source, all but "." (checked above).
        nodot $W/before-$fs.json $W/before-nodot.json; nodot $W/after2-$fs.json $W/after2-nodot.json
        python3 $ST/fsmeta.py compare $W/before-nodot.json $W/after2-nodot.json >$W/over.txt; sed 's/^/    over: /' $W/over.txt
        local overbad hl; overbad=$(grep -v '^missing *1 *e.g. .socket.$' $W/over.txt | grep -v '^identical' | grep -v "^extra *1 *e.g. '.nilfs'$")
        hl=$(echo "$overbad" | grep -E "^(content|mtime|atime|hard links) .*hardlink-" ); overbad=$(echo "$overbad" | grep -vE "^(content|mtime|atime|hard links) .*hardlink-")
        [[ -z $hl ]] && ck "restore over a changed tree (a hard-linked file changed)" PASS "$fs $lab" ||
            ck "restore over a changed tree (a hard-linked file changed)" FAIL "$fs $lab: $(echo $hl) ($(ls -li $D/hardlink-a | cut -d' ' -f1-6)); restorer output: $(grep -v INFO $W/restore2.log | grep -i summary | head -1)"
        if [[ $rc == 0 && -z $overbad ]]; then ck "restore over existing immutable/append-only files" PASS "$fs $lab"
        else ck "restore over existing immutable/append-only files" FAIL "$fs $lab exit $rc: $(grep -iv info $W/restore2.log | head -2 | tr '\n' ' ') $(echo $overbad | head -c 200)"; fi
        chattr -R -i -a $D >/dev/null 2>&1
    done; done
    unmount_of dst
    # One non-root run: a user's tree, backed up and restored as that user.
    log "nonroot $fs"
    local U=$S/user N=$W/nonroot uid=1000
    id -un $uid >/dev/null 2>&1 || useradd -u $uid -m bkst >/dev/null 2>&1
    mkdir -p $U/d/e $N; echo data >$U/f; chmod 0640 $U/f; ln -s f $U/l; ln $U/f $U/f2; mkfifo $U/fifo
    truncate -s 4M $U/sp; echo x | dd of=$U/sp bs=1M seek=2 conv=notrunc status=none
    pyx set $U/f user.mine 6d696e65 2>/dev/null
    touch -d '2004-04-04 04:04:04.4' $U/f $U/d/e; chown -R $uid:$uid $U $N; chmod 0755 $N; touch -d '2004-04-04' $U/d
    unmount_of dst; mkfs_of $fs dst; chown $uid:$uid $D; rm -rf $N/*
    mount -o remount,noatime $S; python3 $ST/fsmeta.py record $U $W/nr-before.json
    local nrok=1 nrd=""
    for t in restic rustic; do
        rm -rf $D/$t
        if setpriv --reuid=$uid --regid=$uid --clear-groups env HOME=/tmp RESTIC_PASSWORD=x RUSTIC_PASSWORD=x bash -c "
            set -e; cd /tmp
            if [[ $t == restic ]]; then $NRRESTIC -r $N/$t init -q; $NRRESTIC -r $N/$t backup -q --no-cache --with-atime $U; $NRRESTIC -r $N/$t --no-cache restore latest:$U --target $D/$t
            else $NRRUSTIC --no-progress --no-cache -r $N/$t init >/dev/null; $NRRUSTIC --no-progress --no-cache -r $N/$t backup --set-atime yes $U >/dev/null; $NRRUSTIC --no-progress --no-cache -r $N/$t restore --numeric-id latest:$U $D/$t; fi
        " >$W/nr-$t.log 2>&1; then
            mount -o remount,noatime $D
            python3 $ST/fsmeta.py record $D/$t $W/nr-after.json --skip .nilfs --skip lost+found
            if ! python3 $ST/fsmeta.py compare $W/nr-before.json $W/nr-after.json >$W/nr.txt; then
                # The restore's own top directory is new: its times may differ.
                grep -v '^identical' $W/nr.txt | sed 's/^/    /'
                if grep -qvE "^(mtime \(dirs\)|atime \(dirs\)) +1 +e\.g\. '\.'$" $W/nr.txt; then nrok=0; nrd+="$t: $(tr '\n' ' ' <$W/nr.txt | head -c 250); "; fi
            fi
        else nrok=0; nrd+="$t failed: $(grep -v INFO $W/nr-$t.log | tail -3 | tr '\n' ' '); "; fi
        sed 's/^/    nr: /' $W/nr-$t.log | grep -v INFO | head -8
    done
    [[ $nrok == 1 ]] && ck "non-root backup and restore" PASS "$fs, uid $uid, both tools" || ck "non-root backup and restore" FAIL "$fs: $nrd"
    unmount_of dst; chattr -R -i -a $S >/dev/null 2>&1; unmount_of src
}

for fs in $FSLIST; do run_fs $fs; done
for l in $(losetup -l -n -O NAME,BACK-FILE | awk -v w=$W '$2 ~ w {print $1}'); do losetup -d $l; done
umount $W/img $W/repos 2>/dev/null
echo "inner: done"
