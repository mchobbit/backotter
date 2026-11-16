#!/bin/sh
# Round trip of NetBSD's file metadata through restic, or BackOtter's rustic:
# make a tree of hard-to-preserve files, back it up, restore it as
# "latest:SRC" into another directory, and compare the two.  The tree spans
# three file systems: the root (FFSv2: file flags, no extended attributes)
# and two FFSv2ea images mounted inside it, one with POSIX.1e ACLs and one
# with NFSv4 ACLs (extended attributes in both namespaces); the restore
# target gets the same two mounts.  Compared: mtree (type, owner, links,
# size, mtime to the microsecond, symlink target, SHA-256), modes, file
# flags, symlink modes, atimes and mtimes to the nanosecond, birth times,
# ACLs, and extended attributes of each namespace, each on its own, so that
# what a tool doesn't keep shows up as just that.  Where the root has
# extended attributes (AlpineBSD's is FFSv2ea), files on it get them too,
# compared on their own.
#
# Then, as on Linux (fs_roundtrip.py): a restore over the restored tree
# after changing it (rt-common.sh's rt_over), compared again but for its
# own ".", which must stay as it was (rt_dot: the target isn't fresh, so it
# keeps its metadata, as stock); when BACKUP and RESTORE are the two
# tools, the snapshot each of them makes of the tree, node by node
# (rt_snapshots); and with the vnd images of the restore target freed: a
# tree on FFSv1, which has no birth times (stat gives 0: no unix.birthtime
# may be stored), restored onto FFSv1; and the FFSv2ea part's snapshot
# restored onto FFSv1 and onto plain FFSv2 (no extended attributes): the
# restore must warn, naming each attribute it can't set, and narrow the
# group bits of each name whose POSIX.1e ACL it can't set.
#
# The tree has a Unix socket too, which neither tool keeps (restic never
# has; rustic since rustic_core 1a2a4a4): no node of it in either tool's
# snapshot, none restored, and no message about it from any backup or
# restore.  It is left out of the listings compared.  Each snapshot decoded
# must store a node's extended attributes sorted by name, though zz-first
# was set before the others.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest:
#                                         kern.securelevel <= 0, or schg and
#                                         sappnd can't be cleared again)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  Runs on AlpineBSD too (NetBSD's kernel and libc): with NetBSD's
# mtree, stat, chflags, setfacl, getfacl, *extattr, md5, ls and touch in
# /root/nbtools, which comes first in PATH.  rt-common.sh is next to this
# script.

PATH=/root/nbtools:/sbin:/usr/sbin:/bin:/usr/bin
R=$1 RR=${2:-$1}
here=$(cd "$(dirname "$0")" && pwd)
. "$here/rt-common.sh"
SRC=/root/bko-rt DST=/root/bko-rt-restored
export RESTIC_PASSWORD=backotter-compare RESTIC_REPOSITORY=/root/bko-rt-repo
export LC_ALL=C           # file names that aren't UTF-8 sort too
try() { "$@" 2>/dev/null || echo "roundtrip: skipped (unsupported here): $*" >&2; }
# Back up with $R, restore with $RR.  BackOtter's rustic (a binary named
# *rustic*) takes the same repository as restic.  BackOtter's flags (both
# forks keep stock's defaults): the real atime (restic --with-atime, rustic
# --set-atime yes) and numeric ids (rustic --numeric-id; restic's default).
export RUSTIC_PASSWORD=$RESTIC_PASSWORD RUSTIC_REPOSITORY=$RESTIC_REPOSITORY
is_rustic() { case $(basename "$1") in *rustic*) true ;; *) false ;; esac; }
bko_init() {
    if is_rustic $R; then $R --no-progress --no-cache init >/dev/null; else $R init -q; fi
}
bko_backup() {
    if is_rustic $R; then $R --no-progress --no-cache backup --set-atime yes "$@" >/dev/null
    else $R backup -q --with-atime "$@"; fi
}
bko_restore() {
    if is_rustic $RR; then $RR --no-progress --no-cache restore --numeric-id --sparse by-content "$1" "$2"
    else $RR restore -q --sparse "$1" --target "$2"; fi
}

# --- When restored bytes differ from the source's -----------------------------
# Not a check (the verdict is the same with or without it): a record of a
# failing content comparison to look at later, after rel9's alpinebsd
# restic->rustic run restored "sparse" with other bytes once in 61 runs and
# nothing of it was kept.  Where compare_all's mtree check fails, before
# the restore over the tree changes it, fx_capture: for each regular file
# whose bytes differ from the source's (cmp; up to FX_MAXFILES) its size,
# blocks and du, both SHA-256s, the first differing bytes (cmp -l: offset
# from 1, source and restored in octal), and each 64 KiB region that
# differs (up to FX_MAXREG): its offset, how many bytes differ, both
# regions' SHA-256 ("zero" for 64 KiB of zeros), the two regions themselves
# for the first FX_MAXBIN; a whole file of 1 MiB or less as it is; the
# snapshot's node of the file (content ids); the repository's config (its
# id) and the snapshot (its id).  After the verdict, while the caller's ssh
# timeout (RT_SECONDS) leaves time, fx_recheck: the file from that snapshot
# by its id through restic's and rustic's dump (what the repository holds),
# the snapshot restored again with $RR and with the other tool into fresh
# directories, each against the source's and the bad restore's SHA-256,
# and restic check --read-data: wrong bytes stored at backup, or a restore
# that went wrong, and whether it does again.  All of it in $FX (kept
# files within FX_CAP_KB), which check.sh fetches into its logs before the
# next direction's reset removes it, and in the output as "fx: " lines.
FX=/root/bko-rt-fx
FX_MAXFILES=${FX_MAXFILES:-8} FX_MAXREG=${FX_MAXREG:-64} FX_MAXBIN=${FX_MAXBIN:-8} FX_CAP_KB=${FX_CAP_KB:-16384}
fx_until=$(($(date +%s) + ${RT_SECONDS:-420} - 60))
rm -rf $FX
fx_left() { [ $((fx_until - $(date +%s))) -ge "$1" ]; }  # SECONDS
fx_say() { echo "fx: $*"; printf '%s\n' "$*" >>$FX/report.txt; }
fx_sha() {  # SHA-256 of stdin
    if command -v sha256 >/dev/null 2>&1; then sha256 | awk '{ print $1 }'
    elif command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{ print $1 }'
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 | awk '{ print $1 }'
    else cksum -a sha256 | awk '{ print $NF }'; fi
}
fx_rustic() {  # the rustic among $R $RR, else rustic-bko next to $R
    for fx_x in $R $RR; do is_rustic $fx_x && { echo $fx_x; return; }; done
    echo "$(dirname $R)/rustic-bko"
}
fx_stat() {  # FILE
    echo "$(stat -f 'size %z, %b blocks (%k-byte I/O), flags %Sf, mtime %Fm' "$1"), du $(du -k "$1" | awk '{ print $1 }') KB"
}
fx_which() {  # SHA SOURCE-SHA BAD-SHA
    if [ "$1" = "$2" ]; then echo "the source's bytes"
    elif [ "$1" = "$3" ]; then echo "the bad restore's bytes"
    else echo "neither the source's nor the bad restore's bytes ($1)"; fi
}
fx_init() {
    [ -d $FX ] && return 0
    mkdir -p $FX || return 1
    fx_bud=$FX_CAP_KB
    fx_rs=$(rt_restic $R $RR) fx_ru=$(fx_rustic)
    fx_zero=$(dd if=/dev/zero bs=65536 count=1 2>/dev/null | fx_sha)
    fx_say "roundtrip.sh $(basename $R) -> $(basename $RR), $(date), $(uname -srm)"
    $fx_rs -r $RESTIC_REPOSITORY --no-cache --no-lock cat config </dev/null >$FX/repo-config.json 2>&1
    $fx_rs -r $RESTIC_REPOSITORY --no-cache --no-lock snapshots --json </dev/null >$FX/snapshots.json 2>&1
    fx_repo=$(tr ',{' '\n\n' <$FX/repo-config.json | sed -n 's/^"id":"\([0-9a-f]*\)".*/\1/p' | head -n 1)
    fx_snap=$(tr ',{' '\n\n' <$FX/snapshots.json | sed -n 's/^"id":"\([0-9a-f]*\)".*/\1/p' | head -n 1)
    fx_say "repository $RESTIC_REPOSITORY, id ${fx_repo:-unknown}; snapshot ${fx_snap:-unknown} (its only one: rt_snapshots's goes into -2); read with $fx_rs"
}
fx_file() {  # RELPATH: one regular file whose bytes differ, $SRC's and $DST's
    fx_s=$SRC/${1#./} fx_d=$DST/${1#./}
    fx_name=$fx_tag.$(printf '%s' "${1#./}" | tr -c 'A-Za-z0-9._-' '_')
    fx_say "-- $1 (files $fx_name.*)"
    fx_say "source:   $(fx_stat "$fx_s")"
    fx_say "restored: $(fx_stat "$fx_d")"
    fx_ss=$(fx_sha <"$fx_s") fx_ds=$(fx_sha <"$fx_d")
    fx_say "SHA-256: source $fx_ss, restored $fx_ds"
    printf '%s\t%s\t%s\t%s\n' "$fx_tag" "$1" "$fx_ss" "$fx_ds" >>$FX/paths
    fx_say "first difference: $(cmp "$fx_s" "$fx_d" 2>&1 | head -n 2 | tr '\n' ' ')"
    # The first 64 differing bytes as they are, then the 64 KiB regions
    # they are in and how many differ in each ("+": more than counted);
    # cmp -l stops when awk has read enough.
    cmp -l "$fx_s" "$fx_d" 2>/dev/null | $rt_awk -v maxr=$FX_MAXREG -v maxl=4000000 -v hf=$FX/$fx_name.cmp-l '
        NR <= 64 { print >hf }
        { r = int(($1 - 1) / 65536)
          if (!seen || r != cur) {
              if (seen) print cur, n
              if (++nreg > maxr) { stop = "regions"; seen = 0; exit }
              cur = r; n = 0; seen = 1 }
          n++
          if (NR >= maxl) { stop = "bytes"; exit } }
        END { if (seen) print cur, n (stop == "bytes" ? "+" : "")
              if (stop != "") print "stop", stop }' >$FX/$fx_name.regions
    fx_say "first differing bytes (offset from 1, source, restored; octal): $(head -n 8 $FX/$fx_name.cmp-l 2>/dev/null | awk '{ printf "%s%s %s %s", s, $1, $2, $3; s = "; " }')"
    fx_i=0
    while read -r fx_r fx_c; do
        if [ "$fx_r" = stop ]; then
            case $fx_c in
                regions) fx_say "(more than $FX_MAXREG regions differ: the rest not listed)" ;;
                *) fx_say "(more than 4000000 bytes differ: the rest not listed)" ;;
            esac
            continue
        fi
        fx_o=$((fx_r * 65536))
        fx_h1=$(dd if="$fx_s" bs=65536 skip=$fx_r count=1 2>/dev/null | fx_sha)
        fx_h2=$(dd if="$fx_d" bs=65536 skip=$fx_r count=1 2>/dev/null | fx_sha)
        [ "$fx_h1" = "$fx_zero" ] && fx_h1=zero
        [ "$fx_h2" = "$fx_zero" ] && fx_h2=zero
        fx_kept=""
        if [ $fx_i -lt $FX_MAXBIN ] && [ $fx_bud -ge 128 ]; then
            dd if="$fx_s" of=$FX/$fx_name.r$fx_o.source.bin bs=65536 skip=$fx_r count=1 2>/dev/null
            dd if="$fx_d" of=$FX/$fx_name.r$fx_o.restored.bin bs=65536 skip=$fx_r count=1 2>/dev/null
            fx_bud=$((fx_bud - 128)) fx_i=$((fx_i + 1)) fx_kept=", kept: $fx_name.r$fx_o.{source,restored}.bin"
        fi
        fx_say "region at $fx_o (64 KiB #$fx_r): $fx_c bytes differ; source $fx_h1, restored $fx_h2$fx_kept"
    done <$FX/$fx_name.regions
    fx_z1=$(stat -f %z "$fx_s") fx_z2=$(stat -f %z "$fx_d")
    if [ "$fx_z1" -le 1048576 ] && [ "$fx_z2" -le 1048576 ] && [ $fx_bud -ge $(((fx_z1 + fx_z2) / 1024 + 2)) ]; then
        cp "$fx_s" $FX/$fx_name.source.file && cp "$fx_d" $FX/$fx_name.restored.file &&
            fx_say "kept whole: $fx_name.source.file, $fx_name.restored.file"
        fx_bud=$((fx_bud - (fx_z1 + fx_z2) / 1024 - 2))
    fi
    (RT_CONTENT=1; rt_path_nodes $fx_rs $RESTIC_REPOSITORY "$SRC/${1#./}" </dev/null 2>&1 | tail -n 1) >$FX/$fx_name.node
    fx_say "its node in the snapshot, content ids too: $fx_name.node ($(wc -c <$FX/$fx_name.node | tr -d ' ') bytes)"
}
fx_capture() {  # X (compare_all's): after mtree's check failed
    fx_tag=fresh; [ -n "$1" ] && fx_tag=over
    fx_init || return 0
    fx_say "== mtree's check failed$1: the regular files whose bytes differ from the source's"
    (cd $SRC && find . -type f | sort) >$FX/$fx_tag.files
    fx_n=0
    while IFS= read -r fx_p; do
        [ -f "$DST/$fx_p" ] || continue
        cmp -s "$SRC/$fx_p" "$DST/$fx_p" && continue
        fx_n=$((fx_n + 1))
        if [ $fx_n -le "$FX_MAXFILES" ]; then fx_file "$fx_p" </dev/null
        else fx_say "-- $fx_p: differs too (more than $FX_MAXFILES files: not looked at)"; fi
    done <$FX/$fx_tag.files
    [ $fx_n -gt 0 ] || fx_say "none: no regular file's bytes differ (mtree's failure is about something else)"
}
fx_restore() {  # TOOL SNAPSHOT:PATH TARGET: bko_restore's, by TOOL
    if is_rustic $1; then $1 --no-progress --no-cache -r $RESTIC_REPOSITORY restore --numeric-id --sparse by-content "$2" "$3"
    else $1 -r $RESTIC_REPOSITORY restore -q --sparse "$2" --target "$3"; fi
}
fx_recheck() {  # after the verdict: the bad files' paths in $FX/paths
    [ -s $FX/paths ] || return 0
    [ -n "$fx_snap" ] || { fx_say "== no snapshot id: nothing restored again"; return 0; }
    fx_say "== the bad files again, from snapshot $fx_snap (${RT_SECONDS:-420} s in all for roundtrip.sh)"
    fx_cut=$(cut -f 2 $FX/paths | sort -u)
    for fx_w in restic:$fx_rs rustic:$fx_ru; do
        fx_b=${fx_w#*:}
        printf '%s\n' "$fx_cut" | while IFS= read -r fx_p; do
            fx_left 30 || { fx_say "$fx_p through ${fx_w%%:*}'s dump: skipped, too little time left"; continue; }
            [ -x "$fx_b" ] || { fx_say "$fx_p through ${fx_w%%:*}'s dump: no $fx_b"; continue; }
            if is_rustic $fx_b; then
                fx_h=$($fx_b --no-progress --no-cache -r $RESTIC_REPOSITORY dump "$fx_snap:$SRC/${fx_p#./}" </dev/null 2>$FX/dump.err | fx_sha)
            else
                fx_h=$($fx_b -r $RESTIC_REPOSITORY --no-cache --no-lock dump $fx_snap "$SRC/${fx_p#./}" </dev/null 2>$FX/dump.err | fx_sha)
            fi
            awk -F '\t' -v p="$fx_p" '$2 == p { print $1, $3, $4 }' $FX/paths | while read -r fx_tg fx_ss fx_ds; do
                fx_say "$fx_p through ${fx_w%%:*}'s dump (what the repository holds), against the $fx_tg restore: $(fx_which $fx_h $fx_ss $fx_ds)"
            done
            [ -s $FX/dump.err ] && fx_say "  it said: $(grep -v '^\[INFO\]' $FX/dump.err | head -n 3 | tr '\n' ' ')"
        done
    done
    if is_rustic $RR; then fx_other=$(rt_restic $RR); else fx_other=$fx_ru; fi
    for fx_w in "same:$RR" "other:$fx_other"; do
        fx_l=${fx_w%%:*} fx_b=${fx_w#*:} fx_t=/root/bko-rt-fx-$fx_l
        fx_left 90 || { fx_say "restore again with $(basename $fx_b) ($fx_l tool): skipped, too little time left"; continue; }
        [ -x "$fx_b" ] || { fx_say "restore again ($fx_l tool): no $fx_b"; continue; }
        mkdir -p $fx_t
        fx_restore $fx_b "$fx_snap:$SRC" $fx_t </dev/null >$FX/restore-$fx_l.msgs 2>&1; fx_st=$?
        if [ "$(wc -c <$FX/restore-$fx_l.msgs)" -gt 65536 ]; then
            head -c 65536 $FX/restore-$fx_l.msgs >$FX/restore-$fx_l.msgs.h && mv $FX/restore-$fx_l.msgs.h $FX/restore-$fx_l.msgs
        fi
        fx_say "restored again with $(basename $fx_b) (the $fx_l tool) into a fresh $fx_t: exit $fx_st (its messages: restore-$fx_l.msgs)"
        while IFS='	' read -r fx_tg fx_p fx_ss fx_ds; do
            if [ -f "$fx_t/$fx_p" ]; then
                fx_say "  $fx_p, against the $fx_tg restore: $(fx_which "$(fx_sha <"$fx_t/$fx_p")" $fx_ss $fx_ds); $(fx_stat "$fx_t/$fx_p")"
            else
                fx_say "  $fx_p: not restored"
            fi
        done <$FX/paths
        chflags -R 0 $fx_t 2>/dev/null; find $fx_t -type l -exec chflags -h 0 {} + 2>/dev/null; rm -rf $fx_t
    done
    if fx_left 45 && [ -x "$fx_rs" ]; then
        $fx_rs -r $RESTIC_REPOSITORY --no-cache check --read-data </dev/null >$FX/check.txt 2>&1
        fx_say "restic check --read-data: exit $? ($(grep -v '^ *$' $FX/check.txt | tail -n 1); all of it in check.txt)"
    else
        fx_say "restic check --read-data: skipped, too little time left or no restic"
    fi
    fx_say "kept in $FX: $(du -sk $FX | awk '{ print $1 }') KB"
}

# A file system on a vnd(4) image, mounted at DIR: FFSv2ea (UFS2 extended
# attributes) with the ACL kind given, or another format of newfs -O.
# vnd0-3 are the four GENERIC has.
vnd=0
vnd_mount() {  # DIR FORMAT [OPTIONS]
    dd if=/dev/zero of=/root/bko-vnd$vnd.img bs=1048576 count=0 seek=64 2>/dev/null
    newfs -F -O "$2" -s 64m /root/bko-vnd$vnd.img >/dev/null &&
        vndconfig vnd$vnd /root/bko-vnd$vnd.img && mkdir -p "$1" &&
        mount_ffs ${3:+-o "$3"} /dev/vnd${vnd}a "$1" ||
        { echo "can't mount an FFS image, newfs -O $2, ${3:-no options}, at $1"; exit 1; }
    vnd=$((vnd + 1))
}
ea_mount() { vnd_mount "$1" 2ea "$2"; }  # DIR posix1eacls|nfs4acls

# What every one of the three file systems gets: file flags, symlink
# modes, birth times.
common() {
    cd "$1" || exit 1
    echo linked >hardlink-a; ln hardlink-a hardlink-b
    ln -s hardlink-a symlink; ln -s hardlink-a symlink-0700; ln -s hardlink-a symlink-0777
    chmod -h 0700 symlink-0700; chmod -h 0777 symlink-0777
    # Birth times: the kernel lowers one to an mtime set earlier than it, so
    # these were born in 2001 and last changed in 2010.  "unborn" was born
    # now and changed in 1990: its birth comes down to 1990 with the mtime.
    echo born >born; mkdir born-dir; ln -s born born-symlink
    for p in born born-dir; do touch -t 200101010101 $p; touch -t 201001010101 $p; done
    touch -h -t 200101010101 born-symlink; touch -h -t 201001010101 born-symlink
    touch -t 199001010101 unborn
    # An atime apart from the mtime, to the nanosecond: a file, a directory
    # and a symlink.
    echo atime >atime-ns; mkdir atime-ns-dir; ln -s atime-ns atime-ns-symlink
    touch -d 2004-04-04T04:04:04.444444444 atime-ns atime-ns-dir
    touch -h -d 2004-04-04T04:04:04.444444444 atime-ns-symlink
    touch -a -d 2003-03-03T03:03:03.333333333 atime-ns atime-ns-dir
    touch -h -a -d 2003-03-03T03:03:03.333333333 atime-ns-symlink
}
# File flags, last: most of them stop any change afterwards.
flags() {
    cd "$1" || exit 1
    for f in uchg uappnd nodump opaque arch schg sappnd; do echo $f >flag-$f; try chflags $f flag-$f; done
    mkdir uchg-dir; echo inside >uchg-dir/inside; try chflags uchg uchg-dir
    try chflags schg,nodump hardlink-a
    try chflags -h uchg,nodump symlink
    # SF_LOG (0x400000) marks the WAPBL journal's inode: the kernel lets
    # root set it, the tools must not keep it.  Octal 20000001: SF_LOG|nodump.
    echo log >flag-log; try chflags 20000001 flag-log
}

mkdir -p $SRC && cd $SRC || exit 1
touch no-such-user; chown 1234:5678 no-such-user
touch setuid; chown 1234:5678 setuid; chmod 4755 setuid
touch setgid; chmod 2755 setgid
mkdir sticky-dir; chmod 1777 sticky-dir
ln -s /nonexistent/target symlink-dangling
mkfifo fifo
# The socket: nc listening on it, stopped; the name stays.
nc -lU bko-socket </dev/null >/dev/null 2>&1 &
sock_pid=$!
for i in 1 2 3 4 5 6 7 8 9 10; do [ -S bko-socket ] && break; sleep 1; done
kill $sock_pid 2>/dev/null; wait $sock_pid 2>/dev/null
[ -S bko-socket ] || { echo "can't make a socket with nc -lU"; exit 1; }
dd if=/dev/zero of=sparse bs=1048576 count=0 seek=1024 2>/dev/null
echo start | dd of=sparse conv=notrunc 2>/dev/null
echo middle | dd of=sparse bs=1048576 seek=512 conv=notrunc 2>/dev/null
touch "name with spaces" "$(printf '\377\376')-not-utf8"
touch -d 2001-02-03T04:05:06.123456789 nanoseconds
touch -d 2100-01-01T12:00:00 future
common $SRC

# The root's own extended attributes, where it has them (AlpineBSD).
root_ea=""
echo data >root-xattrs
if setextattr user backotter hello root-xattrs 2>/dev/null; then
    root_ea=1
    setextattr user binary "$(printf '\377\020\376')" root-xattrs
    setextattr system backotter root-only root-xattrs
    ln -s root-xattrs root-xattrs-symlink; setextattr -h user backotter on-the-link root-xattrs-symlink
    mkdir root-xattrs-dir; setextattr user backotter on-a-dir root-xattrs-dir
fi
ea_mount $SRC/posix1e posix1eacls
ea_mount $SRC/nfs4 nfs4acls
for fs in posix1e nfs4; do
    common $SRC/$fs
    # Extended attributes: user and system namespace, one value binary (no
    # NUL: setextattr takes it as an argument); one on a symlink.
    echo data >xattrs
    # zz-first set first: listed before the others, stored sorted (AUDIT-R6 N4).
    setextattr user zz-first first xattrs
    setextattr user backotter hello xattrs
    setextattr user binary "$(printf '\377\020\376')" xattrs
    setextattr system backotter root-only xattrs
    setextattr -h user backotter on-the-link symlink
done
# POSIX.1e: named entries, on a file and a symlink; a default ACL that a
# directory hands down.
cd $SRC/posix1e
echo acl >acl-file; setfacl -m u:1001:rwx,g:5678:r,m::rwx acl-file
setfacl -h -m u:1001:r,m::r symlink-0700
mkdir acl-dir; setfacl -d -m u::rwx,g::r-x,o::r-x,m::rwx,u:1001:r-x acl-dir
echo inherited >acl-dir/inherited
# NFSv4: a named entry, a deny entry, and ones a directory hands down.
cd $SRC/nfs4
echo acl >acl-file; setfacl -m u:1001:rwx::allow,g:5678:w::deny acl-file
mkdir acl-dir; setfacl -m u:1001:rx:fd:allow acl-dir
echo inherited >acl-dir/inherited
for d in $SRC $SRC/posix1e $SRC/nfs4; do flags $d; done
touch -d 2001-02-03T04:05:06.000000006 $SRC/posix1e $SRC/nfs4 $SRC

# The listings compared, one file each, for the tree below $1; without
# the socket, which has a check of its own.
echo bko-socket >/tmp/bko-rt.mtree-exclude
listings() {  # DIR OUT-PREFIX
    cd "$1" || exit 1
    find . ! -name bko-socket | sort >$2.names
    # mtree compares symlink modes too: those are a check of their own.
    mtree -c -p . -X /tmp/bko-rt.mtree-exclude -k type,uid,gid,nlink,size,time,link,sha256 >$2.mtree
    find . ! -type l ! -name bko-socket -exec stat -f '%N %Mp%Lp' {} + | sort >$2.modes
    # flag-log (SF_LOG|nodump) comes back as nodump only: a check of its own.
    find . ! -name bko-socket -exec stat -f '%N %Xf' {} + | sort >$2.flags-all
    grep -v '/flag-log ' $2.flags-all >$2.flags
    grep '/flag-log ' $2.flags-all >$2.flags-log
    find . -type l -exec stat -f '%N %Lp' {} + | sort >$2.symlink-modes
    # What a perfect restore gives: the birth time, or the mtime when that is
    # earlier (the kernel has no way to set a birth time later than it).
    find . ! -name bko-socket -exec stat -f '%FB %Fm %N' {} + | awk '{
        split($1, b, "."); split($2, m, ".")
        early = (b[1] + 0 < m[1] + 0 || (b[1] + 0 == m[1] + 0 && b[2] + 0 <= m[2] + 0)) ? $1 : $2
        print substr($0, length($1) + length($2) + 3), early }' | sort >$2.birth
    for fs in posix1e nfs4; do
        find ./$fs ! -type l | sort | while IFS= read -r p; do getfacl -n "$p"; done
        find ./$fs -type d | sort | while IFS= read -r p; do echo "# default"; getfacl -n -d "$p"; done
        find ./$fs -type l | sort | while IFS= read -r p; do getfacl -n -h "$p"; done
    done >$2.acls 2>&1
    for ns in user system; do
        { find ./posix1e ./nfs4; [ -n "$root_ea" ] && find . -name 'root-xattrs*'; } | sort | while IFS= read -r p; do
            for name in $(lsextattr -q -h $ns "$p" | tr ' \t' '\n\n' | sort); do
                # The ACLs' own storage: compared as ACLs.
                case $ns.$name in system.posix1e.acl_access|system.posix1e.acl_default|system.nfs4.acl) continue ;; esac
                echo "$p $name $(getextattr -q -h -x $ns "$name" "$p" | md5)"
            done
        done >$2.extattrs-$ns 2>&1
    done
}
failed=""
after=/tmp/bko-rt.after
# With $cmp_listing: diff, or for the restore over an existing tree
# rt_nodot_diff, which leaves out the target's own "." (as $cmp_mtree,
# rt_nodot_run, does from mtree's report).
cmp_listing=diff cmp_mtree=""
check() {  # TITLE KIND: compare before.KIND with $after.KIND
    echo "== $1"
    if $cmp_listing /tmp/bko-rt.before.$2 $after.$2; then
        echo "identical ($(wc -l </tmp/bko-rt.before.$2 | tr -d ' ') lines)"
    else
        failed="$failed; $1"
    fi
}

# Atimes and mtimes to the nanosecond (mtree compares mtimes to the
# microsecond), name by name from the source's list: looking a name up
# moves no atime, reading a file or listing a directory does.  The source's
# are taken with its file systems mounted noatime, from the listings
# through the backup; but NetBSD drops the atimes utimensat(2) sets on one
# mounted noatime too, so the restore has atimes on, and the restored
# times are taken before anything reads the tree.
times_of() {  # DIR OUT-PREFIX
    (cd "$1" && tr '\n' '\0' </tmp/bko-rt.before.names | xargs -0 stat -f '%N %Fa %Fm' | sort >$2.times)
}
# mount -u replaces the options: the ACL kind and the root's journal go
# along again.
atimes() {  # noatime|atime: remount the source's file systems so
    log=""; mount | grep -q ' on / type ffs (log' && log=,log
    mount -u -o $1$log / &&
        mount -u -o $1,posix1eacls $SRC/posix1e && mount -u -o $1,nfs4acls $SRC/nfs4 ||
        { echo "can't remount the source's file systems $1"; exit 1; }
}
atimes noatime
listings $SRC /tmp/bko-rt.before
times_of $SRC /tmp/bko-rt.before
bko_init || { echo "INIT FAILED"; exit 1; }
# A backup that fails may still have saved a snapshot (restic's exit 3: a
# file it couldn't read): what it holds is restored all the same.
echo "== backup"
bko_backup $SRC 2>/tmp/bko-rt.backup.msgs; status=$?
cat /tmp/bko-rt.backup.msgs
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; backup"
rm -f /tmp/bko-rt.nodes.1 /tmp/bko-rt.nodes.2
rt_snapshots $R $RR $RESTIC_REPOSITORY $SRC
# The socket: in neither tool's snapshot (rt_snapshots decoded both, the
# second the other tool's; else this one's is decoded here), and nothing
# said about it.
t="socket: no node in the snapshot, no message about it at backup"
echo "== $t"
if [ -s /tmp/bko-rt.nodes.1 ] || rt_nodes $(rt_restic $R $RR) $RESTIC_REPOSITORY $SRC >/tmp/bko-rt.nodes.1; then
    if cat /tmp/bko-rt.nodes.1 /tmp/bko-rt.nodes.2 2>/dev/null | grep -e '^bko-socket	' -e 'socket' ||
        grep -i socket /tmp/bko-rt.backup.msgs; then
        failed="$failed; $t"
    else
        echo "none ($(wc -l </tmp/bko-rt.nodes.1 | tr -d ' ') nodes$([ -s /tmp/bko-rt.nodes.2 ] && echo ", in both tools' snapshots"))"
    fi
else
    echo "can't decode the snapshot"; failed="$failed; $t"
fi
# Extended attributes stored sorted by name, in each tool's snapshot that
# was decoded, whatever order they were set in (zz-first first) or are
# listed in (AUDIT-R6 N4).
t="extended attributes stored sorted by name (xattr_names), zz-first among them"
echo "== $t"
for p in posix1e/xattrs nfs4/xattrs; do echo "  $p as the source lists its user ones: $(lsextattr -q user $SRC/$p) (not a check)"; done
ok=1
for n in /tmp/bko-rt.nodes.1 /tmp/bko-rt.nodes.2; do
    [ -s $n ] || continue
    for p in posix1e/xattrs nfs4/xattrs ${root_ea:+root-xattrs}; do
        o=$(awk -F '\t' -v n="$p" '$1 == n' $n | tr '\t' '\n' | sed -n 's/^xattr_names=//p')
        s=$(printf '%s\n' "$o" | tr ',' '\n' | sort | tr '\n' ','); s=${s%,}
        echo "  $(basename $n): $p: ${o:-none}"
        [ -n "$o" ] && [ "$o" = "$s" ] || ok=""
        case $p in root-xattrs) ;; *) case ,$o, in *zz-first,*) ;; *) ok="" ;; esac ;; esac
    done
done
[ -n "$ok" ] && echo "sorted" || failed="$failed; $t"
atimes atime
mkdir -p $DST
ea_mount $DST/posix1e posix1eacls
ea_mount $DST/nfs4 nfs4acls
echo "== restore"
bko_restore "latest:$SRC" $DST >/tmp/bko-rt.restore.msgs 2>&1; status=$?
cat /tmp/bko-rt.restore.msgs
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore"
# The restored tree against the source, its listings into $after.*; X goes
# after each check's title.
compare_all() {  # X
    x=$1
    times_of $DST $after
    listings $DST $after
    t="mtree: type, owner, group, links, size, mtime, symlink target, SHA-256$x"
    echo "== $t"
    (cd $DST && $cmp_mtree mtree -f /tmp/bko-rt.before.mtree -p .) && echo identical || { failed="$failed; $t"; fx_capture "$x"; }
    check "modes (not symlinks)$x" modes
    t="socket: not restored, no message about it$x"
    echo "== $t"
    if [ -e $DST/bko-socket ] || [ -L $DST/bko-socket ]; then
        ls -l $DST/bko-socket; failed="$failed; $t"
    elif grep -i socket /tmp/bko-rt.restore.msgs; then
        failed="$failed; $t"
    else
        echo "none"
    fi
    check "file flags (netbsd.file_flags)$x" flags
    t="SF_LOG dropped, nodump kept (netbsd.file_flags mask 0xff1fffff)$x"
    echo "== $t"
    cat /tmp/bko-rt.before.flags-log
    if [ "$(grep -c ' 400001$' /tmp/bko-rt.before.flags-log)" != 3 ]; then
        echo "the source hasn't SF_LOG|nodump on its three flag-log files"; failed="$failed; $t"
    elif sed 's/ 400001$/ 1/' /tmp/bko-rt.before.flags-log | diff - $after.flags-log; then
        echo "restored: nodump only (3 lines)"
    else
        failed="$failed; $t"
    fi
    check "symlink modes$x" symlink-modes
    check "atime and mtime to the nanosecond$x" times
    check "birth times, min(birth, mtime) (unix.birthtime)$x" birth
    check "ACLs (netbsd.acl_*)$x" acls
    check "user extended attributes$x" extattrs-user
    check "system extended attributes (netbsd.system_extattrs)$x" extattrs-system
    [ -n "$root_ea" ] && echo "   (both with the root's own: root-xattrs*)"
    t="hard links (one inode for both)$x"
    echo "== $t"
    n=$(ls -i $DST/hardlink-a $DST/hardlink-b | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
    echo "inodes: $n"; [ "$n" = 1 ] || failed="$failed; $t"
}
compare_all ""
echo "== sparse (KB allocated, source then restored)"
du -k $SRC/sparse $DST/sparse
# S3 marks it by its blocks (NetBSD has no SEEK_HOLE probe), and a marked
# file is restored by block (SPEC3 3 as revised): only the blocks a blob
# puts a byte other than zero in.  Its holes are 1 MiB apart, aligned to
# any block size, so it takes no more than the source's.
t="sparse: marked unix.sparse, restored with no more blocks than the source (SPEC3 3)"
echo "== $t"
sync
sk=$(du -k $SRC/sparse | awk '{ print $1 }') dk=$(du -k $DST/sparse | awk '{ print $1 }')
echo "source $sk KB, restored $dk KB"
grep '^sparse	' /tmp/bko-rt.nodes.1 | grep -q '"unix.sparse":true' || { echo "not marked"; failed="$failed; $t"; }
[ "$dk" -le "$sk" ] || failed="$failed; $t"

# Again over the restored tree, changed: the file flags off (or nothing
# could change), a directory replaced by a symlink to one elsewhere, files
# with other bytes, mode and mtime, an extended attribute removed, the
# target's own mode and times.  Holding a tree, the target isn't fresh: its
# "." must stay as it is now (flags, mode, owner, ACLs, extended
# attributes), as stock restic and rustic leave it.  Its times may only move
# forward: the restore replaces uchg-dir's symlink in it, and atimes are on
# here.
echo "== restore over an existing, changed tree"
chflags -R 0 $DST && find $DST -type l -exec chflags -h 0 {} +
rmextattr user backotter $DST/posix1e/xattrs
rt_over $DST uchg-dir atime-ns posix1e/xattrs nfs4/acl-file ||
    failed="$failed; preparing the restore over an existing tree"
rt_dot $DST >/tmp/bko-rt.dot.before
bko_restore "latest:$SRC" $DST >/tmp/bko-rt.restore.msgs 2>&1; status=$?
rt_dot $DST >/tmp/bko-rt.dot.after
cat /tmp/bko-rt.restore.msgs
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore over an existing tree"
t=". kept as it was by the restore over an existing tree (not a fresh target, as stock)"
echo "== $t"
rt_dot_check /tmp/bko-rt.dot.before /tmp/bko-rt.dot.after forward &&
    echo "kept ($(wc -l </tmp/bko-rt.dot.before | tr -d ' ') lines)" || failed="$failed; $t"
t="what the replaced directory's symlink pointed to, untouched"
echo "== $t"
rt_over_check && echo "untouched ($(wc -l </tmp/bko-rt.elsewhere.before | tr -d ' ') names)" || failed="$failed; $t"
after=/tmp/bko-rt.over
echo "(the target's own \".\" is left out of the comparisons below: checked above)"
cmp_listing=rt_nodot_diff cmp_mtree=rt_nodot_run
compare_all " (over an existing tree)"
cmp_listing=diff cmp_mtree=""

# The restore target's two images free vnd2 and vnd3 for the rest.
umount $DST/posix1e && umount $DST/nfs4 && vndconfig -u vnd2 && vndconfig -u vnd3 ||
    { echo "can't free vnd2 and vnd3"; exit 1; }
vnd=2
# FFSv1: no birth times (stat shows 0), so none in the snapshot, and a
# restore onto FFSv1 that sets none and says nothing about them.
F1=/root/bko-ffs1 F1R=/root/bko-ffs1-restored
vnd_mount $F1 1
vnd_mount $F1R 1
(cd $F1 && echo born >born && mkdir born-dir && ln -s born born-symlink &&
    for p in born born-dir; do touch -t 200101010101 $p; touch -t 201001010101 $p; done &&
    touch -h -t 201001010101 born-symlink && touch -d 2001-02-03T04:05:06.123456789 nanoseconds)
echo "== FFSv1: birth times as stat shows them (not a check)"
(cd $F1 && stat -f '  %N birth %FB mtime %Fm' born born-dir born-symlink nanoseconds)
mount -u -o noatime $F1
mtree -c -p $F1 -k type,uid,gid,mode,nlink,size,time,link,sha256 >/tmp/bko-rt.ffs1.mtree
(cd $F1 && stat -f '%N %Fa %Fm' born born-dir born-symlink nanoseconds) >/tmp/bko-rt.ffs1.times
echo "== FFSv1: backup with $(basename $R)"
rt_backup $R /root/bko-rt-repo-ffs1 $F1; echo "exit $?"
t="FFSv1: no unix.birthtime in the snapshot (birth 0 isn't one)"
echo "== $t"
if rt_nodes $(rt_restic $R $RR) /root/bko-rt-repo-ffs1 $F1 >/tmp/bko-rt.ffs1.nodes; then
    sed 's/^/  /' /tmp/bko-rt.ffs1.nodes
    if grep -q 'unix\.birthtime' /tmp/bko-rt.ffs1.nodes; then failed="$failed; $t"; else echo "none"; fi
else
    echo "can't decode the snapshot"; failed="$failed; $t"
fi
t="FFSv1 restored onto FFSv1: exit 0, no messages, mtree and times identical"
echo "== $t"
bko_restore_repo() {  # REPO SNAPSHOT-PATH TARGET: $RR's restore from REPO, its messages in /tmp/bko-rt.msgs
    repo=$RESTIC_REPOSITORY
    export RESTIC_REPOSITORY=$1 RUSTIC_REPOSITORY=$1
    bko_restore "$2" "$3" >/tmp/bko-rt.msgs 2>&1; rs=$?
    export RESTIC_REPOSITORY=$repo RUSTIC_REPOSITORY=$repo
    grep -v -e 'failed to get system time zone' -e '^\[INFO\]' -e '^Files: ' -e '^Dirs: ' \
        -e '^restore done' -e '^using no cache' -e '^Summary: Restored ' /tmp/bko-rt.msgs >/tmp/bko-rt.msgs.f
    sed 's/^/  says: /' /tmp/bko-rt.msgs.f
    return $rs
}
bko_restore_repo /root/bko-rt-repo-ffs1 "latest:$F1" $F1R; status=$?
echo "exit $status"
ok=1
[ $status -eq 0 ] && [ ! -s /tmp/bko-rt.msgs.f ] || ok=""
(cd $F1R && stat -f '%N %Fa %Fm' born born-dir born-symlink nanoseconds) >/tmp/bko-rt.ffs1.times-after
diff /tmp/bko-rt.ffs1.times /tmp/bko-rt.ffs1.times-after || ok=""
mtree -f /tmp/bko-rt.ffs1.mtree -p $F1R || ok=""
[ -n "$ok" ] && echo "yes" || failed="$failed; $t"

# The FFSv2ea part's snapshot (extended attributes, ACLs, birth times) onto
# file systems without extended attributes: FFSv1, then plain FFSv2.
# Its attributes, NAMESPACE.NAME: a system one is named with its namespace,
# a user one by its bare name in a message that isn't about a system one.
names=$(for ns in user system; do sed -n "s|^\./posix1e/[^ ]* \([^ ]*\) .*|$ns.\1|p" /tmp/bko-rt.before.extattrs-$ns; done |
    sort -u | tr '\n' ' ')
names=${names% }
noea() {  # TARGET KIND
    t="the FFSv2ea part restored onto $2: warns, naming each extended attribute ($names)"
    echo "== $t"
    bko_restore_repo /root/bko-rt-repo "latest:$SRC/posix1e" $1; status=$?
    echo "exit $status"
    missing=""
    for a in $names; do
        case $a in
            system.*) grep -q -- "$a" /tmp/bko-rt.msgs.f ;;
            *) grep -v -- "system.${a#user.}" /tmp/bko-rt.msgs.f | grep -q -- "${a#user.}" ;;
        esac || missing="$missing $a"
    done
    if [ -z "$missing" ]; then echo "every one named"; else echo "not named:$missing"; failed="$failed; $t"; fi
    # ACLs it can't hold are a warning too, not an error (SPEC2 2, 7.1).
    t="the FFSv2ea part restored onto $2: exit 0, the ACLs not set a warning"
    echo "== $t"
    if [ $status -eq 0 ] && grep -qi 'acl' /tmp/bko-rt.msgs.f; then echo "ok"; else failed="$failed; $t"; fi
    # Where a POSIX.1e ACL can't be set, the group bits (the ACL's mask)
    # come back narrowed to the owning group's own entry (group::), so that
    # nothing is more open than it was (SPEC2 1.2; both BACKOTTER.md files).
    t="the FFSv2ea part restored onto $2: the group bits of each name with a POSIX.1e ACL narrowed to group::"
    echo "== $t"
    ok=1 n=0
    for p in $(cd $SRC/posix1e && find . | sort); do
        h=""; [ -L "$SRC/posix1e/$p" ] && h=-h
        acl=$(getfacl -n $h "$SRC/posix1e/$p" 2>/dev/null)
        printf '%s\n' "$acl" | grep -q '^mask::' || continue
        g=$(printf '%s\n' "$acl" | sed -n 's/^group::\(...\).*/\1/p')
        m=$(stat -f %Sp "$SRC/posix1e/$p" | cut -c5-7)
        want=$(echo "$m $g" | awk '{ for (i = 1; i <= 3; i++) { c = substr($2, i, 1); printf "%s", (c != "-" && substr($1, i, 1) != "-") ? c : "-" } }')
        got=$(stat -f %Sp "$1/$p" | cut -c5-7)
        echo "  $p: group bits $m, group:: $g; restored $got (want $want)"
        n=$((n + 1)); [ "$got" = "$want" ] || ok=""
    done
    [ $n -gt 0 ] || { echo "  no name with a POSIX.1e ACL in the source"; ok=""; }
    [ -n "$ok" ] && echo "every one narrowed" || failed="$failed; $t"
}
noea $F1R/posix1e FFSv1
umount $F1R && vndconfig -u vnd3 || { echo "can't free vnd3"; exit 1; }
vnd=3
vnd_mount /root/bko-ffs2-plain 2
noea /root/bko-ffs2-plain/posix1e "plain FFSv2"
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
# After the verdict, which it doesn't change: a bad file's bytes again
# (fx_recheck, above).
fx_recheck
