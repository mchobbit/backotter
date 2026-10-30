#!/bin/ksh
# Round trip of illumos file metadata through restic, or BackOtter's rustic:
# make a tree of hard-to-preserve files, back it up, restore it as
# "latest:SRC" into another directory on the same ZFS pool, and compare the
# two name by name: type, owner, mode, links, size, mtime, symlink target,
# content, NFSv4 ACL, system attributes (chmod S+v...) and extended
# attributes (named attribute files, runat(1)).
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.

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
bko_exclude() { if is_rustic $R; then echo "--glob !$(basename "$1")"; else echo "--exclude $1"; fi; }

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

# Timestamps: nanoseconds, far future, a symlink's own.
touch -d '2001-02-03 04:05:06.123456789' nanoseconds
touch -d '2037-12-31 12:00:00' future     # GNU touch here stops short of 2038
touch -h -d '2002-02-02 02:02:02' symlink

# System attributes last: immutable and appendonly stop most changes.
for a in hidden readonly system archive nodump appendonly nounlink immutable; do
    echo $a >sattr-$a; try /usr/bin/chmod S+v$a sattr-$a
done
mkdir immutable-dir; echo inside >immutable-dir/inside
try /usr/bin/chmod S+vimmutable immutable-dir
try /usr/bin/chmod S+vimmutable hardlink-a
touch -d '2001-02-03 04:05:06' $SRC

listing() {  # every name below $1, one block each
    (cd "$1" && find . | sort | while IFS= read -r p; do
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

listing $SRC >/tmp/bko-rt.before
echo "listing: $(wc -l </tmp/bko-rt.before) lines"
bko_init && bko_backup $SRC || { echo "BACKUP FAILED"; exit 1; }
echo "== restore"
bko_restore "latest:$SRC" $DST; echo "exit $?"
listing $DST >/tmp/bko-rt.after
echo "== compare (< source, > restored)"
diff /tmp/bko-rt.before /tmp/bko-rt.after && echo identical
echo "== hard links (one inode for all three)"
ls -i $DST/hardlink-a $DST/hardlink-b $DST/sub/hardlink-c | awk '{print $1}' | sort -u | wc -l | sed 's/^ */inodes: /'
echo "== sparse (KB allocated, source then restored)"
du -k $SRC/sparse $DST/sparse
