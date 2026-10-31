#!/bin/ksh
# Round trip of illumos file metadata through restic, or BackOtter's rustic:
# make a tree of hard-to-preserve files, back it up, restore it as
# "latest:SRC" into another directory on the same ZFS pool, and compare the
# two name by name: type, owner, mode, links, size, atime and mtime (to the
# nanosecond), symlink target, content, NFSv4 ACL, system attributes
# (chmod S+v...) and extended attributes (named attribute files, runat(1)).
# Then, each on its own:
# creation times (crtime), the attribute files' own owner, mode, times and
# ACL, owners and ACL entries that are Windows SIDs (idmap's ephemeral ids),
# system attributes cleared from a new file's default (noarchive,
# noav_modified), and ZFS project IDs.
#
# Then (not --again), as on Linux (fs_roundtrip.py): a restore over the
# restored tree after changing it (rt-common.sh's rt_over), compared again;
# when BACKUP and RESTORE are the two tools, the snapshot each of them makes
# of the tree, node by node (rt_snapshots); a file with av_quarantined, in
# a tree of its own (no one may read it: both tools leave it out); and a
# restore of sid/ with idmap's door moved away (idmapd still running, so
# the kernel's own ids stay; libidmap gets an RPC error): BACKOTTER.md has
# every SID owner and ACL entry come back as nobody (60001), with a warning
# per attribute, not per file, and exit 0; and a backup of sid/ with the
# door moved away: one warning, exit 0, the numbers alone kept.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest)
#        roundtrip.sh --again RESTORE    (in a fresh boot of it, with the
#                                         first run's /root/bko-rt-repo and
#                                         /tmp/bko-rt.before.* put back)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  --again restores and compares only: idmap hands out the
# ephemeral ids of SID owners anew in every boot, so a restore that keeps
# the number rather than the SID gives someone else's id, or nobody.
# rt-common.sh is next to this script.

again=""
[ "$1" = --again ] && { again=1; shift; }
R=$1 RR=${2:-$1}
SRC=/root/bko-rt DST=/root/bko-rt-restored
here=$(cd "$(dirname "$0")" && pwd)
. "$here/rt-common.sh"
export RESTIC_PASSWORD=backotter-compare RESTIC_REPOSITORY=/root/bko-rt-repo
PATH=/usr/gnu/bin:$PATH   # GNU stat and touch, for nanoseconds and symlinks
export LC_ALL=C           # file names that aren't UTF-8 sort too
try() { "$@" 2>/dev/null || echo "roundtrip: skipped (unsupported here): $*" >&2; }
# Back up with $R, restore with $RR.  BackOtter's rustic (a binary named
# *rustic*) takes the same repository as restic, with its own options: the
# real atime, as restic keeps it, and numeric ids, as restic restores them.
export RUSTIC_PASSWORD=$RESTIC_PASSWORD RUSTIC_REPOSITORY=$RESTIC_REPOSITORY
is_rustic() { case $(basename "$1") in *rustic*) true ;; *) false ;; esac; }
bko_init() {
    if is_rustic $R; then $R --no-progress --no-cache init >/dev/null; else $R init -q; fi
}
bko_backup() {
    if is_rustic $R; then $R --no-progress --no-cache backup --set-atime yes "$@" >/dev/null
    else $R backup -q "$@"; fi
}
bko_restore() {
    if is_rustic $RR; then $RR --no-progress --no-cache restore --numeric-id --sparse by-content "$1" "$2"
    else $RR restore -q --sparse "$1" --target "$2"; fi
}
# SID owners need idmap: it maps a SID to an ephemeral id (> 2147483647)
# and back.
svcadm enable -s idmap || exit 1
# Reading a file or a directory must not move its atime, here or in the
# backup: atimes are compared to the nanosecond.  (In the fresh boot of
# --again too: the guest is a throwaway copy, and the property goes with it.)
zfs set atime=off "$(zfs list -H -o name /root)" || exit 1
sid_uid() { idmap show -c sid:$1 uid | sed -n 's/.* -> uid:\([0-9]*\)$/\1/p'; }
sid_gid() { idmap show -c sid:$1 gid | sed -n 's/.* -> gid:\([0-9]*\)$/\1/p'; }

# Creation times (crtime), which only the SUNWattr_rw view of a file or
# directory shows and sets: an XDR-encoded nvlist (libnvpair's
# NV_ENCODE_XDR).  "list DIR" prints every file's and directory's below DIR
# as NAME SEC.NSEC, sorted as sort(1) does with LC_ALL=C; "set PATH SEC
# NSEC" writes a minimal nvlist, {crtime=[SEC,NSEC]}, which changes nothing
# else.
cat >/tmp/bko-crtime.py <<'PY'
import os, stat, struct, sys

O_XATTR = 0x4000


def view(path: str, mode: int) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        return os.open("SUNWattr_rw", mode | O_XATTR, dir_fd=fd)
    finally:
        os.close(fd)


def crtime(path: str) -> str:
    v = view(path, os.O_RDONLY)
    try:
        b = os.read(v, 65536)
    finally:
        os.close(v)
    off = 12                      # encoding, endian, version, flags
    while off + 8 <= len(b):
        size, _, namelen = struct.unpack_from(">III", b, off)
        if size == 0:
            break
        name = b[off + 12:off + 12 + namelen]
        if name == b"crtime":
            value = off + 12 + (namelen + 3) // 4 * 4 + 12   # type, nelem, count
            sec, nsec = struct.unpack_from(">QQ", b, value)
            return f"{sec}.{nsec:09d}"
        off += size
    return "none"


if sys.argv[1] == "set":
    sec, nsec = int(sys.argv[3]), int(sys.argv[4])
    nv = (bytes.fromhex("010100000000000000000001")
          + struct.pack(">III8sIIIQQ", 0x30, 0x28, 6, b"crtime", 0x10, 2, 2, sec, nsec)
          + bytes(8))
    v = view(sys.argv[2], os.O_RDWR)
    os.write(v, nv)
    os.close(v)
else:
    os.chdir(sys.argv[2])
    out = []
    for d, dirs, files in os.walk("."):
        for name in ["."] * (d == ".") + dirs + files:
            p = os.path.normpath(os.path.join(d, name))
            p = "." if p == "." else "./" + p
            if stat.S_ISREG(os.lstat(p).st_mode) or stat.S_ISDIR(os.lstat(p).st_mode):
                out.append(os.fsencode(p) + b" " + crtime(p).encode())
    sys.stdout.buffer.write(b"".join(l + b"\n" for l in sorted(out)))
PY
crtime() { /usr/bin/python3 -I /tmp/bko-crtime.py "$@"; }

# The tree (not --again).
if [ -z "$again" ]; then
mkdir -p $SRC && cd $SRC || exit 1
touch no-such-user; chown 1234:5678 no-such-user
touch setuid; chown 1234:5678 setuid; chmod 4755 setuid
touch setgid; chmod 2755 setgid
mkdir sticky-dir; chmod 1777 sticky-dir
echo linked >hardlink-a; ln hardlink-a hardlink-b; mkdir sub; ln hardlink-a sub/hardlink-c
ln -s hardlink-a symlink; ln -s /nonexistent/target symlink-dangling
mkfifo fifo
truncate -s 1G sparse
echo start | dd of=sparse conv=notrunc 2>/dev/null
echo middle | dd of=sparse bs=1M seek=512 conv=notrunc 2>/dev/null
touch "name with spaces" "$(printf '\377\376')-not-utf8"

# NFSv4 ACLs: named entries, a deny entry, and ones a directory hands down.
echo acl >acl-file
/usr/bin/chmod A+user:nobody:read_data/write_data/append_data:allow,group:staff:read_data:deny acl-file
mkdir acl-dir
/usr/bin/chmod A+user:nobody:read_data/execute:file_inherit/dir_inherit:allow acl-dir

# Extended attributes: named attribute files, one binary.
echo data >xattrs
runat xattrs 'echo hello >note; printf "\000\377\020" >binary'
mkdir xattr-dir; runat xattr-dir 'echo inside >dir-note'

# The attribute files' own owner, mode, times and ACL, one entry a SID.
echo meta >xattr-meta
runat xattr-meta 'echo private >secret; /usr/bin/chown 1500:1501 secret; /usr/bin/chmod 600 secret
    /usr/bin/chmod A+user:nobody:read_data:allow,usersid:S-1-5-21-1111-2222-3333-1006:write_data:deny secret
    /usr/gnu/bin/touch -d "2001-01-01 01:01:01.123456789" secret'

# Owners and ACL entries that are SIDs (sid/, compared on its own).
mkdir sid
echo owner >sid/owner
/usr/bin/chown $(sid_uid S-1-5-21-1111-2222-3333-1001):$(sid_gid S-1-5-21-1111-2222-3333-513) sid/owner
echo acl >sid/acl
/usr/bin/chmod A+usersid:S-1-5-21-1111-2222-3333-1004:read_data:allow,groupsid:S-1-5-21-1111-2222-3333-1005:write_data:deny sid/acl

# System attributes off that a new file has on (defaults/, compared on its
# own): a restore that writes the file sets archive and av_modified.
mkdir defaults
echo x >defaults/noarchive; /usr/bin/chmod S-varchive defaults/noarchive
echo x >defaults/noav_modified; /usr/bin/chmod S-vav_modified defaults/noav_modified
echo x >defaults/neither; /usr/bin/chmod S-v'{archive,av_modified}' defaults/neither

# ZFS project IDs, one inherited.
mkdir project; zfs project -p 77 -s project; echo inherited >project/inherited
echo own >project-file; zfs project -p 78 project-file

# Timestamps: nanoseconds, far future, a symlink's own.
touch -d '2001-02-03 04:05:06.123456789' nanoseconds
touch -d '2037-12-31 12:00:00' future     # GNU touch here stops short of 2038
touch -h -d '2002-02-02 02:02:02.000000007' symlink
# An atime apart from the mtime, to the nanosecond: a file, a directory and
# a symlink.
echo atime >atime-ns; mkdir atime-ns-dir; ln -s atime-ns atime-ns-symlink
touch -d '2004-04-04 04:04:04.444444444' atime-ns atime-ns-dir
touch -h -d '2004-04-04 04:04:04.444444444' atime-ns-symlink
touch -a -d '2003-03-03 03:03:03.333333333' atime-ns atime-ns-dir
touch -h -a -d '2003-03-03 03:03:03.333333333' atime-ns-symlink

# Creation times: 2001 on a file and a directory, 2030 (later than the
# mtime: ZFS takes any), and an old one on a file made immutable below.
echo born >crtime-2001; mkdir crtime-2001-dir; echo born >crtime-2030; echo born >crtime-immutable
crtime set crtime-2001 978310861 123456789
crtime set crtime-2001-dir 978310861 5
crtime set crtime-2030 1900000000 0
crtime set crtime-immutable 978310861 0

# System attributes last: immutable and appendonly stop most changes.
for a in hidden readonly system archive nodump offline sparse appendonly nounlink immutable; do
    echo $a >sattr-$a; try /usr/bin/chmod S+v$a sattr-$a
done
mkdir immutable-dir; echo inside >immutable-dir/inside
try /usr/bin/chmod S+vimmutable immutable-dir
try /usr/bin/chmod S+vimmutable hardlink-a
try /usr/bin/chmod S+vimmutable crtime-immutable
touch -d '2001-02-03 04:05:06' $SRC/sid $SRC/defaults $SRC
fi

listing() {  # every name below $1, one block each, but sid/ and defaults/
    (cd "$1" && find . | sort | while IFS= read -r p; do
        case $p in ./sid/*|./defaults/*) continue ;; esac
        stat -c '%n %F uid=%u gid=%g mode=%a links=%h size=%s atime=%.9X mtime=%.9Y' "$p"
        [ -L "$p" ] && echo "  -> $(readlink "$p")" && continue
        [ -f "$p" ] && echo "  sha256 $(digest -a sha256 "$p")"
        /usr/bin/ls -dV "$p" | tail -n +2 | sed 's/^ */  acl /'
        /usr/bin/ls -d/v "$p" | tail -n +2 | sed 's/^ */  attrs /'
        if [ -f "$p" ] || [ -d "$p" ]; then
            # runat(1) runs its arguments as one shell command line.
            runat "$p" 'for a in *; do case $a in SUNWattr_r[ow]) ;; *) [ -f "$a" ] && echo "  xattr $a $(digest -a md5 "$a")" ;; esac; done' 2>/dev/null
        fi
    done)
}
# The attribute files' atimes (illumos.xattr_meta keeps them too): reading
# a value moves its atime, so the source's are taken after the main listing
# has read them (as the backup then finds them), the restored ones before.
xattr_atimes() {  # DIR
    (cd "$1" && find . \( -type f -o -type d \) | sort | while IFS= read -r p; do
        runat "$p" 'for a in *; do case $a in SUNWattr_r[ow]) ;; *) [ -f "$a" ] &&
            stat -c "%n atime=%.9X" "$a" ;; esac; done' | sed "s|^|$p: |"
    done 2>/dev/null)
}
# Owners as names: a SID, for an ephemeral id idmap knows.
sid_listing() {  # DIR
    (cd "$1" && for p in sid/*; do /usr/bin/ls -l "$p" | awk '{ print $9, $3, $4 }'; /usr/bin/ls -V "$p" | tail -n +2; done)
}
# The listings of the checks on their own, one file each.
listings() {  # DIR OUT-PREFIX
    case $2 in *.before) ;; *) xattr_atimes "$1" >$2.xattr-atime ;; esac
    listing "$1" >$2.main
    case $2 in *.before) xattr_atimes "$1" >$2.xattr-atime ;; esac
    crtime list "$1" >$2.crtime
    cd "$1" || exit 1
    # Not the atimes: the main listing has just read the values.
    find . \( -type f -o -type d \) | sort | while IFS= read -r p; do
        runat "$p" 'for a in *; do case $a in SUNWattr_r[ow]) ;; *) [ -f "$a" ] &&
            stat -c "%n uid=%u gid=%g mode=%a mtime=%.9Y" "$a" && /usr/bin/ls -V "$a" | tail -n +2 ;; esac; done' |
            sed "s|^|$p: |"
    done >$2.xattr-meta 2>/dev/null
    sid_listing "$1" >$2.sid
    for p in defaults/*; do echo "$p"; /usr/bin/ls -/v "$p" | tail -n +2; done >$2.defaults
    # By name: zfs project -r opens the FIFO too, and waits for a writer.
    { zfs project -d project; zfs project project/inherited project-file; } >$2.project 2>&1
}
failed=""
after=/tmp/bko-rt.after
check() {  # TITLE KIND: compare before.KIND with $after.KIND
    echo "== $1"
    if diff /tmp/bko-rt.before.$2 $after.$2; then
        echo "identical ($(wc -l </tmp/bko-rt.before.$2 | tr -d ' ') lines)"
    else
        failed="$failed; $1"
    fi
}

if [ -z "$again" ]; then
    listings $SRC /tmp/bko-rt.before
    echo "listing: $(wc -l </tmp/bko-rt.before.main) lines"
    bko_init || { echo "INIT FAILED"; exit 1; }
    # A backup that fails may still have saved a snapshot (restic's exit 3: a
    # file it couldn't read): what it holds is restored all the same.
    echo "== backup"
    bko_backup $SRC; status=$?
    echo "exit $status"
    [ $status -eq 0 ] || failed="$failed; backup"
    rt_snapshots $R $RR $RESTIC_REPOSITORY $SRC
fi
echo "== restore"
bko_restore "latest:$SRC" $DST; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore"
# The restored tree against the source, its listings into $after.*; X goes
# after each check's title.
compare_all() {  # X
    x=$1
    listings $DST $after
    check "compare (< source, > restored)$x" main
    check "creation times (unix.birthtime)$x" crtime
    check "attribute files' owner, mode, mtime, ACL (illumos.xattr_meta)$x" xattr-meta
    check "attribute files' atime (illumos.xattr_meta)$x" xattr-atime
    check "SID owners and ACL entries (illumos.owner_sid, group_sid, acl sid)$x" sid
    # idmap keeps its ephemeral ids over a restart: the restored ids still
    # name the same SIDs.
    svcadm disable -s idmap && svcadm enable -s idmap
    sid_listing $DST >$after.sid-restart
    cp /tmp/bko-rt.before.sid /tmp/bko-rt.before.sid-restart
    check "SID owners and ACL entries after svcadm restart idmap$x" sid-restart
    check "system attributes off by default (illumos.system_attributes)$x" defaults
    check "ZFS project IDs (illumos.zfs_project, SPEC2 5.4)$x" project
    t="hard links (one inode for all three)$x"
    echo "== $t"
    n=$(ls -i $DST/hardlink-a $DST/hardlink-b $DST/sub/hardlink-c | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
    echo "inodes: $n"; [ "$n" = 1 ] || failed="$failed; $t"
}
compare_all ""
if [ -z "$again" ]; then
    echo "== sparse (KB allocated, source then restored)"
    du -k $SRC/sparse $DST/sparse

    # Again over the restored tree, changed: the system attributes that stop
    # changes off, a directory replaced by a symlink to one elsewhere, files
    # with other bytes, mode and mtime, an attribute file removed.
    echo "== restore over an existing, changed tree"
    find $DST ! -type l -exec /usr/bin/chmod S-v'{immutable,appendonly,nounlink,readonly,av_quarantined}' {} \; 2>/dev/null
    runat $DST/xattrs rm note
    rt_over $DST sub atime-ns xattrs || failed="$failed; preparing the restore over an existing tree"
    bko_restore "latest:$SRC" $DST; status=$?
    echo "exit $status"
    [ $status -eq 0 ] || failed="$failed; restore over an existing tree"
    t="what the replaced directory's symlink pointed to, untouched"
    echo "== $t"
    rt_over_check && echo "untouched ($(wc -l </tmp/bko-rt.elsewhere.before | tr -d ' ') names)" || failed="$failed; $t"
    after=/tmp/bko-rt.over
    compare_all " (over an existing tree)"

    # av_quarantined: no one may open such a file, root included (EACCES),
    # so it gets a tree and a repository of its own, beside the round trip
    # above.  Both BACKOTTER.md files: a backup leaves it out, node and
    # all, with a message naming it (restic an error and exit 3, rustic a
    # warning and exit 0), and the source keeps the attribute; the rest of
    # the tree comes back.
    t="av_quarantined (its own tree: no one may read such a file): left out of the backup with a message naming it (restic exit 3, rustic 0), the source's attribute kept, the rest restored"
    echo "== $t"
    Q=/root/bko-rt-quar
    mkdir $Q && echo quarantined >$Q/quarantined && echo plain >$Q/plain &&
        /usr/bin/chmod S+vav_quarantined $Q/quarantined && touch -d '2001-02-03 04:05:06' $Q/quarantined $Q
    rt_backup $R /root/bko-rt-repo-quar $Q >/tmp/bko-rt.quar.backup 2>&1; bstatus=$?
    sed 's/^/  backup says: /' /tmp/bko-rt.quar.backup
    want=3; is_rustic $R && want=0
    echo "backup exit $bstatus (expected $want)"
    (export RESTIC_REPOSITORY=/root/bko-rt-repo-quar RUSTIC_REPOSITORY=/root/bko-rt-repo-quar
        bko_restore "latest:$Q" $Q-restored) >/tmp/bko-rt.quar.msgs 2>&1; status=$?
    grep -v -e 'failed to get system time zone' -e '^\[INFO\]' /tmp/bko-rt.quar.msgs | sed 's/^/  restore says: /'
    echo "restore exit $status"
    for d in $Q $Q-restored; do
        /usr/bin/ls -/v $d/quarantined 2>&1 | tr -d '\t' | sed "s|^|  $d: |"
    done
    ok=1
    [ $bstatus -eq $want ] && [ $status -eq 0 ] || ok=""
    grep -q "$Q/quarantined" /tmp/bko-rt.quar.backup || { echo "no message names $Q/quarantined"; ok=""; }
    /usr/bin/ls -/v $Q/quarantined 2>/dev/null | grep -q '[{,]av_quarantined[,}]' || { echo "the source lost av_quarantined"; ok=""; }
    [ -e $Q-restored/quarantined ] && { echo "quarantined was restored"; ok=""; }
    cmp $Q/plain $Q-restored/plain || ok=""
    [ -n "$ok" ] && echo "yes" || failed="$failed; $t"

    # sid/ restored with idmap out of reach: libidmap's door moved away.
    t="sid/ restored with idmap unreachable: nobody (60001) for every SID owner and ACL entry, exit 0, a warning per attribute, not per file"
    echo "== $t"
    door=/var/run/rpc_door/rpc_100172.1
    mv $door $door.bko-away || { echo "can't move $door"; exit 1; }
    idmap show -c sid:S-1-5-21-1111-2222-3333-1001 uid 2>&1 | sed 's/^/  idmap show: /'
    bko_restore "latest:$SRC/sid" /root/bko-rt-noidmap >/tmp/bko-rt.noidmap.msgs 2>&1; status=$?
    mv $door.bko-away $door
    echo "exit $status"
    grep -v -e 'failed to get system time zone' -e '^\[INFO\]' /tmp/bko-rt.noidmap.msgs |
        tee /tmp/bko-rt.noidmap.msgs.f | sed 's/^/  says: /'
    (cd /root/bko-rt-noidmap && for p in *; do /usr/bin/ls -n "$p" | awk '{ print $9, $3, $4 }'; /usr/bin/ls -V "$p" | tail -n +2; done) |
        tee /tmp/bko-rt.noidmap.list | sed 's/^/  /'
    ok=1
    [ $status -eq 0 ] || ok=""
    grep -q '^owner 60001 60001$' /tmp/bko-rt.noidmap.list || ok=""
    # ls -V's compact form: user:nobody:r-------------:-------:allow
    [ "$(grep -c -e 'user:nobody:r-*:-*:allow' -e 'group:nobody:-w-*:-*:deny' /tmp/bko-rt.noidmap.list)" = 2 ] || ok=""
    w=$(grep -ci -e idmap -e 'S-1-' /tmp/bko-rt.noidmap.msgs.f)
    # Both BACKOTTER.md files: a warning once per attribute, and sid/ has
    # all three (owner_sid, group_sid, acl), each once.
    echo "$w message lines name idmap or a SID (one per attribute: owner, group, ACL entries)"
    [ "$w" = 3 ] || ok=""
    for a in illumos.owner_sid illumos.group_sid illumos.acl; do
        n=$(grep -c "$a[: ]" /tmp/bko-rt.noidmap.msgs.f)
        echo "  $a: $n"
        [ "$n" = 1 ] || ok=""
    done
    [ -n "$ok" ] && echo "yes" || failed="$failed; $t"

    # sid/ backed up with idmap out of reach, the door moved away again:
    # both BACKOTTER.md files have one warning ("idmap offline: SID owners
    # not kept"), exit 0, and the numbers alone kept (no illumos.owner_sid
    # in the snapshot), which in this boot still name the same SIDs.
    t="sid/ backed up with idmap unreachable: one idmap offline warning, exit 0, no SIDs kept, the numbers restored as they were (same boot)"
    echo "== $t"
    mv $door $door.bko-away || { echo "can't move $door"; exit 1; }
    rt_backup $R /root/bko-rt-repo-noidmap $SRC/sid >/tmp/bko-rt.noidmapb.msgs 2>&1; bstatus=$?
    mv $door.bko-away $door
    grep -v -e 'failed to get system time zone' /tmp/bko-rt.noidmapb.msgs | sed 's/^/  backup says: /'
    echo "backup exit $bstatus"
    (export RESTIC_REPOSITORY=/root/bko-rt-repo-noidmap RUSTIC_REPOSITORY=/root/bko-rt-repo-noidmap
        bko_restore "latest:$SRC/sid" /root/bko-rt-noidmapb) >/tmp/bko-rt.noidmapb.rmsgs 2>&1; status=$?
    grep -v -e 'failed to get system time zone' -e '^\[INFO\]' /tmp/bko-rt.noidmapb.rmsgs | sed 's/^/  restore says: /'
    echo "restore exit $status"
    numeric() { (cd "$1" && for p in *; do /usr/bin/ls -n "$p" | awk '{ print $9, $3, $4 }'; /usr/bin/ls -V "$p" | tail -n +2; done); }
    numeric $SRC/sid >/tmp/bko-rt.noidmapb.before
    numeric /root/bko-rt-noidmapb >/tmp/bko-rt.noidmapb.after
    ok=1
    [ $bstatus -eq 0 ] && [ $status -eq 0 ] || ok=""
    n=$(grep -c 'idmap offline' /tmp/bko-rt.noidmapb.msgs)
    echo "$n idmap offline warnings"
    [ "$n" = 1 ] || ok=""
    if rt_nodes "$(rt_restic $R $RR)" /root/bko-rt-repo-noidmap $SRC/sid >/tmp/bko-rt.noidmapb.nodes; then
        n=$(grep -c -e owner_sid -e group_sid -e '"sid"' /tmp/bko-rt.noidmapb.nodes)
        echo "$n snapshot nodes with a SID"
        [ "$n" = 0 ] || ok=""
    else
        echo "can't decode the snapshot"; ok=""
    fi
    diff /tmp/bko-rt.noidmapb.before /tmp/bko-rt.noidmapb.after || ok=""
    [ -n "$ok" ] && echo "yes" || failed="$failed; $t"
fi
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
