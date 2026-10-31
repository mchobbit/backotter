#!/bin/ksh
# Round trip of illumos file metadata through restic, or BackOtter's rustic:
# make a tree of hard-to-preserve files, back it up, restore it as
# "latest:SRC" into another directory on the same ZFS pool, and compare the
# two name by name: type, owner, mode, links, size, mtime, symlink target,
# content, NFSv4 ACL, system attributes (chmod S+v...) and extended
# attributes (named attribute files, runat(1)).  Then, each on its own:
# creation times (crtime), the attribute files' own owner, mode, times and
# ACL, owners and ACL entries that are Windows SIDs (idmap's ephemeral ids),
# system attributes cleared from a new file's default (noarchive,
# noav_modified), and ZFS project IDs.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest)
#        roundtrip.sh --again RESTORE    (in a fresh boot of it, with the
#                                         first run's /root/bko-rt-repo and
#                                         /tmp/bko-rt.before.* put back)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  --again restores and compares only: idmap hands out the
# ephemeral ids of SID owners anew in every boot, so a restore that keeps
# the number rather than the SID gives someone else's id, or nobody.

again=""
[ "$1" = --again ] && { again=1; shift; }
R=$1 RR=${2:-$1}
SRC=/root/bko-rt DST=/root/bko-rt-restored
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
touch -h -d '2002-02-02 02:02:02' symlink

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
        stat -c '%n %F uid=%u gid=%g mode=%a links=%h size=%s mtime=%.9Y' "$p"
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
    case $2 in *.after) xattr_atimes "$1" >$2.xattr-atime ;; esac
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
check() {  # TITLE KIND: compare before.KIND with after.KIND
    echo "== $1"
    if diff /tmp/bko-rt.before.$2 /tmp/bko-rt.after.$2; then
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
fi
echo "== restore"
bko_restore "latest:$SRC" $DST; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore"
listings $DST /tmp/bko-rt.after
check "compare (< source, > restored)" main
check "creation times (unix.birthtime)" crtime
check "attribute files' owner, mode, mtime, ACL (illumos.xattr_meta)" xattr-meta
check "attribute files' atime (illumos.xattr_meta)" xattr-atime
check "SID owners and ACL entries (illumos.owner_sid, group_sid, acl sid)" sid
# idmap keeps its ephemeral ids over a restart: the restored ids still name
# the same SIDs.
svcadm disable -s idmap && svcadm enable -s idmap
sid_listing $DST >/tmp/bko-rt.after.sid-restart
cp /tmp/bko-rt.before.sid /tmp/bko-rt.before.sid-restart
check "SID owners and ACL entries after svcadm restart idmap" sid-restart
check "system attributes off by default (illumos.system_attributes)" defaults
check "ZFS project IDs (illumos.zfs_project, SPEC2 5.4)" project
t="hard links (one inode for all three)"
echo "== $t"
n=$(ls -i $DST/hardlink-a $DST/hardlink-b $DST/sub/hardlink-c | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
echo "inodes: $n"; [ "$n" = 1 ] || failed="$failed; $t"
if [ -z "$again" ]; then
    echo "== sparse (KB allocated, source then restored)"
    du -k $SRC/sparse $DST/sparse
fi
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
