#!/bin/ksh
# Round trip of illumos file metadata through restic: make a tree of
# hard-to-preserve files, back it up, restore it as "latest:SRC" into another
# directory on the same ZFS pool, and compare the two name by name: type,
# owner, mode, links, size, mtime, symlink target, content, NFSv4 ACL, system
# attributes (chmod S+v...) and extended attributes (named attribute files,
# runat(1)).
#
# Usage: roundtrip.sh RESTIC        (as root, in a throwaway guest)

R=$1
SRC=/root/bko-rt DST=/root/bko-rt-restored
export RESTIC_PASSWORD=backotter-compare RESTIC_REPOSITORY=/root/bko-rt-repo
PATH=/usr/gnu/bin:$PATH   # GNU stat and touch, for nanoseconds and symlinks
export LC_ALL=C           # file names that aren't UTF-8 sort too
try() { "$@" 2>/dev/null || echo "roundtrip: skipped (unsupported here): $*" >&2; }

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
runat xattrs sh -c 'echo hello >note; printf "\000\377\020" >binary'

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
            runat "$p" sh -c 'for a in *; do [ -f "$a" ] && echo "  xattr $a $(digest -a md5 "$a")"; done' 2>/dev/null
        fi
    done)
}

listing $SRC >/tmp/bko-rt.before
echo "listing: $(wc -l </tmp/bko-rt.before) lines"
$R init -q && $R backup -q $SRC || { echo "BACKUP FAILED"; exit 1; }
echo "== restore"
$R restore -q --sparse "latest:$SRC" --target $DST; echo "exit $?"
listing $DST >/tmp/bko-rt.after
echo "== compare (< source, > restored)"
diff /tmp/bko-rt.before /tmp/bko-rt.after && echo identical
echo "== hard links (one inode for all three)"
ls -i $DST/hardlink-a $DST/hardlink-b $DST/sub/hardlink-c | awk '{print $1}' | sort -u | wc -l | sed 's/^ */inodes: /'
echo "== sparse (KB allocated, source then restored)"
du -k $SRC/sparse $DST/sparse
