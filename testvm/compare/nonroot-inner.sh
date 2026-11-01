#!/bin/sh
# The inside of nonroot-rt.sh: run as root on Linux, FreeBSD, NetBSD (and
# AlpineBSD), DragonFly, OpenBSD or illumos, on one file system.  Makes a
# tree in FSDIR/src, backs it up as root with each tool (each its own
# repository), restores it as uid 4321 into a directory that uid owns, in
# all four directions, and reports per direction the exit status, the
# messages, and every name whose metadata differs from the source's; then
# what differs between the tools' restores.
#
# One node, xattr-order, has an extended attribute only root may set
# (trusted.* on Linux, the system namespace on FreeBSD and NetBSD) made
# before a user one: a restore that stops at the first attribute it can't
# set loses the user one (AUDIT-R4 D1).  The summary says per direction
# whether user.b came back, and how each snapshot orders the two (the
# order the test needs: on FreeBSD and NetBSD the system namespace is a
# generic attribute of its own, not in the node's extended attributes).
#
# A Unix socket, socket, is in the tree but in no listing: neither tool
# backs one up (restic never did, rustic since rustic_core 1a2a4a4), so
# the summary counts socket nodes in each snapshot, sockets each restore
# makes and messages that name one: all must be 0.
#
# Usage: nonroot-inner.sh FSDIR RESTIC RUSTIC
# Needs rt-common.sh next to it.  Linux: python3, and FSMETA_DIR: the
# directory with fsmeta.py (testvm/compare), for its ioctls.  illumos:
# python3 (creation times), GNU stat and touch.  The BSDs: mtree's
# siblings stat, md5, sha256 (AlpineBSD: NetBSD's, in /root/nbtools).
# NetBSD drops the atimes utimensat(2) sets on a file system mounted
# noatime: there FSDIR is mounted noatime while the source is listed and
# backed up, and with atimes on for the restores (nonroot-rt.sh does that
# between, by the hook FSDIR/atimes, if there).

FS=$1 RESTIC=$2 RUSTIC=$3
U=4321
SRC=$FS/src OUT=$FS/out
export LC_ALL=C
os=$(uname -s)
here=$(cd "$(dirname "$0")" && pwd)
. "$here/rt-common.sh"
case $os in
    SunOS) PATH=/usr/gnu/bin:/usr/bin:/usr/sbin:$PATH ;;
    Linux) ;;
    *) PATH=/root/nbtools:/sbin:/usr/sbin:/bin:/usr/bin:$PATH ;;
esac
mkdir -p $OUT
chmod 755 $FS
try() { "$@" 2>/dev/null || echo "nonroot: skipped (unsupported here): $*"; }

as_user() {  # COMMAND...: as uid 4321, with its own HOME
    mkdir -p $FS/home && chown $U:$U $FS/home
    case $os in
        Linux) setpriv --reuid=$U --regid=$U --clear-groups env HOME=$FS/home "$@" ;;
        SunOS) su bkonr -c "env HOME=$FS/home $*" ;;
        *) su -m bkonr -c "env HOME=$FS/home $*" ;;
    esac
}

# illumos' creation times (crtime), which only the SUNWattr_rw view of a file
# or directory shows and sets (as illumos/roundtrip.sh): "get PATH" prints
# SEC.NSEC, "set PATH SEC NSEC" writes a minimal {crtime=[SEC,NSEC]} nvlist.
if [ $os = SunOS ]; then
    cat >$OUT/crtime.py <<'PY'
import os, struct, sys
O_XATTR = 0x4000
def view(path, mode):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        return os.open("SUNWattr_rw", mode | O_XATTR, dir_fd=fd)
    finally:
        os.close(fd)
if sys.argv[1] == "set":
    sec, nsec = int(sys.argv[3]), int(sys.argv[4])
    nv = (bytes.fromhex("010100000000000000000001")
          + struct.pack(">III8sIIIQQ", 0x30, 0x28, 6, b"crtime", 0x10, 2, 2, sec, nsec) + bytes(8))
    v = view(sys.argv[2], os.O_RDWR); os.write(v, nv); os.close(v)
else:
    try:
        v = view(sys.argv[2], os.O_RDONLY)
    except OSError:
        print("-"); sys.exit(0)
    b = os.read(v, 65536); os.close(v); off = 12; out = "none"
    while off + 8 <= len(b):
        size, _, namelen = struct.unpack_from(">III", b, off)
        if size == 0:
            break
        if b[off + 12:off + 12 + namelen] == b"crtime":
            sec, nsec = struct.unpack_from(">QQ", b, off + 12 + (namelen + 3) // 4 * 4 + 12)
            out = f"{sec}.{nsec:09d}"
            break
        off += size
    print(out)
PY
    crtime() { /usr/bin/python3 -I $OUT/crtime.py "$@"; }
fi

# --- the tree --------------------------------------------------------------
mkdir -p $SRC && cd $SRC || exit 1
echo plain >plain
echo owned >owned-1234; chown 1234:5678 owned-1234
echo setuid >setuid; chmod 4755 setuid
echo setgid >setgid; chmod 2755 setgid
echo linked >hardlink-a; ln hardlink-a hardlink-b
ln -s plain symlink
mkfifo fifo
if command -v python3 >/dev/null 2>&1; then
    try python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' socket
else  # nc(1) leaves its socket behind
    nc -lU socket >/dev/null 2>&1 & pid=$!
    sleep 1; kill $pid 2>/dev/null; wait $pid 2>/dev/null
    [ -S socket ] || echo "nonroot: skipped (unsupported here): a socket"
fi
echo times >times-ns
if [ $os = Linux ]; then
    touch -d '2004-04-04 04:04:04.444444444' times-ns
    touch -a -d '2003-03-03 03:03:03.333333333' times-ns
    try mknod chardev c 1 3
    echo x >xattrs; setfattr -n user.bko -v hello xattrs; try setfattr -n trusted.bko -v root-only xattrs
    # D1: the attribute non-root can't set first, then one it can.
    echo x >xattr-order; try setfattr -n trusted.a -v root-only xattr-order; setfattr -n user.b -v settable xattr-order
    echo acl >acl; try setfacl -m u:65534:r acl
    # Inode flags: nodump and noatime are the owner's; immutable and append
    # need CAP_LINUX_IMMUTABLE.
    for f in nodump:d noatime:A immutable:i append:a; do
        echo ${f%:*} >iflag-${f%:*}; try chattr +${f#*:} iflag-${f%:*}
    done
    if [ "$(stat -f -c %t .)" = 2fc12fc1 ]; then
        # ZFS's DOS attributes (SPEC2 3): all but immutable and appendonly are
        # the owner's.  Written whole, archive with them where it stays.
        dosflags() {  # PATH VALUE
            python3 -c 'import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
fcntl.ioctl(fd, 0x40088302, struct.pack("Q", int(sys.argv[2], 0)))' "$@"
        }
        a=0x800000000
        echo x >dos-hidden-system; dosflags dos-hidden-system $((0x600000000 | a))
        echo x >dos-readonly; dosflags dos-readonly $((0x100000000 | a))
        echo x >dos-noarchive; dosflags dos-noarchive 0
        echo x >dos-nounlink; dosflags dos-nounlink $((0x2000000000 | a))
        echo x >dos-offline-sparse; dosflags dos-offline-sparse $((0x300000000000 | a))
        mkdir dos-hidden-dir; dosflags dos-hidden-dir $((0x200000000 | a))
        echo x >dos-hidden-immutable; dosflags dos-hidden-immutable $((0x200000000 | a))
        try chattr +i dos-hidden-immutable
    fi
elif [ $os = SunOS ]; then
    touch -d '2004-04-04 04:04:04.444444444' times-ns
    touch -a -d '2003-03-03 03:03:03.333333333' times-ns
    try mknod chardev c 13 2
    # Extended attributes (named attribute files; the owner's), one with an
    # owner and ACL of its own (illumos.xattr_meta: the owner part root's).
    echo x >xattrs; runat xattrs 'echo hello >note'
    echo x >xattr-meta; runat xattr-meta 'echo private >secret; /usr/bin/chown 1500:1501 secret
        /usr/bin/chmod A+user:nobody:read_data:allow secret'
    echo acl >acl; /usr/bin/chmod A+user:nobody:read_data:allow,group:staff:write_data:deny acl
    # A SID owner (root's to give) and SID ACL entries (the owner's).
    svcadm enable -s idmap
    sid() { idmap show -c sid:$1 $2 | sed -n "s/.* -> $2:\([0-9]*\)\$/\1/p"; }
    echo sid >sid-owner
    /usr/bin/chown $(sid S-1-5-21-1111-2222-3333-1001 uid):$(sid S-1-5-21-1111-2222-3333-513 gid) sid-owner
    echo sid >sid-acl
    /usr/bin/chmod A+usersid:S-1-5-21-1111-2222-3333-1004:read_data:allow sid-acl
    # Creation times: born 2001, changed 2010 (the owner sets crtime too).
    echo born >born; mkdir born-dir
    for p in born born-dir; do crtime set $p 978310861 123456789; touch -t 201001010101 $p; done
    # A ZFS project ID.
    echo x >project; try zfs project -p 77 project
    # System attributes: hidden, readonly, system, archive, offline and
    # sparse the owner's; nodump, appendonly, nounlink and immutable root's;
    # av_modified the owner may set but not clear (chmod S+v as uid 4321 on
    # OmniOS r151058, round 6).  A restore as the owner must still set the
    # owner's next to root's it can't.  (Not av_quarantined: no one may
    # read such a file, root included, so it fails the backups;
    # illumos/roundtrip.sh has it in a tree of its own.)
    for a in hidden system readonly archive nodump offline sparse av_modified appendonly nounlink immutable; do
        echo $a >sattr-$a; try /usr/bin/chmod S+v$a sattr-$a
    done
    echo x >sattr-noarchive; /usr/bin/chmod S-v'{archive,av_modified}' sattr-noarchive
    echo x >sattr-hidden-immutable; try /usr/bin/chmod S+v'{hidden,immutable}' sattr-hidden-immutable
else
    touch -t 200404040404.04 times-ns
    touch -a -t 200303030303.03 times-ns
    touch -d 2004-04-04T04:04:04.444444444 times-ns 2>/dev/null
    touch -a -d 2003-03-03T03:03:03.333333333 times-ns 2>/dev/null
    case $os in
        FreeBSD) try mknod chardev c 0 13 root:wheel ;;
        DragonFly)  # no mknod(8): mknod(2) by a helper, as dragonfly/roundtrip.sh
            printf '%s\n' '#include <sys/types.h>' '#include <sys/stat.h>' \
                'int main(int c, char **v) { return c != 2 || mknod(v[1], S_IFCHR | 0644, makedev(13, 2)) != 0; }' \
                >$OUT/mknod.c && cc -o $OUT/mknod $OUT/mknod.c && try $OUT/mknod chardev ;;
        *) try mknod chardev c 2 2 ;;   # /dev/null on NetBSD and OpenBSD
    esac
    echo x >xattrs; try setextattr user bko hello xattrs; try setextattr system bko root-only xattrs
    # D1: the attribute non-root can't set first, then one it can.
    echo x >xattr-order; try setextattr system a root-only xattr-order; try setextattr user b settable xattr-order
    echo acl >acl
    setfacl -m u:65534:rw::allow acl 2>/dev/null || try setfacl -m u:65534:r,m::r acl
    # Birth times: born 2001, changed 2010 (the owner can lower a birth time
    # with utimensat too).
    echo born >born; touch -t 200101010101 born; touch -t 201001010101 born
    mkdir born-dir; touch -t 200101010101 born-dir; touch -t 201001010101 born-dir
    # File flags: UF_* (0xffff) are the owner's, SF_* root's.  ZFS refuses
    # uchg, uappnd and uunlnk, and gives a new file uarch.  Per OS, the
    # names its chflags(1) knows.
    case $os in
        FreeBSD) fl="nodump uchg uappnd uunlnk hidden system rdonly offline sparse uarch schg sappnd" ;;
        NetBSD) fl="nodump uchg uappnd opaque arch schg sappnd" ;;
        DragonFly) fl="nodump uchg uappnd opaque nohistory cache arch schg sappnd noshistory" ;;
        *) fl="nodump uchg uappnd arch schg sappnd" ;;
    esac
    for f in $fl; do echo $f >flag-$f; try chflags $f flag-$f; done
    if [ $os = FreeBSD ]; then
        echo x >flag-hidden-schg; try chflags hidden,schg flag-hidden-schg
        mkdir flag-hidden-dir; try chflags hidden flag-hidden-dir
        echo x >flag-noarchive; try chflags nouarch flag-noarchive
    else
        echo x >flag-nodump-schg; try chflags nodump,schg flag-nodump-schg
        mkdir flag-nodump-dir; try chflags nodump flag-nodump-dir
    fi
fi
touch -d 2001-02-03T04:05:06 $SRC 2>/dev/null || touch -t 200102030405.06 $SRC

# --- listings: "NAME<tab>key=value ...", the same keys on every line ----------
# Owners apart: as uid 4321 every restored name is its.
if [ $os = Linux ]; then
    cat >$OUT/listing.py <<'PY'
import hashlib, os, stat, sys
sys.path.insert(0, os.environ["FSMETA_DIR"])
import fsmeta
root = sys.argv[1]
zfs = fsmeta.zfs_devices()
out = []
for d, dirs, files in os.walk(root):
    for n in dirs + files:
        p = os.path.join(d, n)
        st = os.lstat(p)
        kind = fsmeta.TYPES.get(stat.S_IFMT(st.st_mode), "unknown")
        if kind == "socket":
            continue
        isdir = kind == "dir"
        reg = kind in ("file", "dir")
        fl = fsmeta.inode_flags(p, isdir) if reg else None
        dos = fsmeta.dos_flags(p, isdir) if reg and st.st_dev in zfs else None
        try:
            names = sorted(os.listxattr(p, follow_symlinks=False))
            vals = b"".join(os.getxattr(p, x, follow_symlinks=False) for x in names)
        except OSError:
            names, vals = [], b""
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()[:8] if kind == "file" else "-"
        rdev = f"{os.major(st.st_rdev)},{os.minor(st.st_rdev)}" if kind in ("chardev", "blockdev") else "-"
        out.append(f"{os.path.relpath(p, root)}\ttype={kind} mode={stat.S_IMODE(st.st_mode):o}"
                   f" iflags={'-' if fl is None else hex(fl)} dos={'-' if dos is None else hex(dos)}"
                   f" atime={st.st_atime_ns} mtime={st.st_mtime_ns} size={st.st_size if kind == 'file' else '-'}"
                   f" sha={h} rdev={rdev} xattrs={','.join(names) or '-'}"
                   f" xvalues={hashlib.md5(vals).hexdigest()[:8] if names else '-'}")
print("\n".join(sorted(out)))
PY
    listing() { FSMETA_DIR=$FSMETA_DIR python3 -I $OUT/listing.py "$1"; }
    owners() { find "$1" -mindepth 1 \( ! -user $U -o ! -group $U \) | sed "s|^$1/||" | sort; }
elif [ $os = SunOS ]; then
    listing() {  # DIR
        (cd "$1" && find . ! -name . ! -type s | sort | while IFS= read -r p; do
            n=${p#./}
            st="type=$(stat -c %F "$p" | sed 's/regular empty file/file/; s/regular file/file/; s/symbolic link/symlink/; s/character special file/chardev/' | tr ' ' -) $(stat -c 'mode=%a atime=%.9X mtime=%.9Y' "$p")"
            sha=-; size=-; acl=-; sattrs=-; x=-; v=-; cr=-; proj=-
            if [ -f "$p" ] && [ ! -L "$p" ]; then sha=$(digest -a sha256 "$p" | cut -c1-8); size=$(stat -c %s "$p"); fi
            if [ ! -L "$p" ]; then
                acl=$(/usr/bin/ls -dV "$p" | tail -n +2 | digest -a md5 | cut -c1-8)
                sattrs=$(/usr/bin/ls -d/v "$p" | tail -n +2 | tr -d ' \t{}')
                [ -n "$sattrs" ] || sattrs=-
            fi
            if [ -f "$p" ] || [ -d "$p" ]; then
                cr=$(crtime get "$p")
                # The ZFS project ID and inheritance ("77/-"; zfs project waits
                # on a FIFO, so files and directories alone; -d, a directory
                # itself, is refused for a file).
                pd=""; [ -d "$p" ] && pd=-d
                proj=$(zfs project $pd "$p" 2>/dev/null | awk '{ print $1 "/" $2 }')
                [ -n "$proj" ] || proj=-
                x=$(runat "$p" 'for a in *; do case $a in SUNWattr_r[ow]) ;; *) [ -f "$a" ] && echo "$a" ;; esac; done' 2>/dev/null | tr '\n' ',')
                v=$(runat "$p" 'for a in *; do case $a in SUNWattr_r[ow]) ;; *) [ -f "$a" ] && { /usr/bin/ls -n "$a" | cut -c1-10; cat "$a"; } ;; esac; done' 2>/dev/null | digest -a md5 | cut -c1-8)
                x=${x%,}; [ -n "$x" ] || { x=-; v=-; }
            fi
            printf '%s\t%s size=%s sha=%s crtime=%s project=%s acl=%s sattrs=%s xattrs=%s xvalues=%s\n' "$n" "$st" "$size" \
                "$sha" "$cr" "$proj" "$acl" "$sattrs" "$x" "$v"
        done)
    }
    owners() { (cd "$1" && find . ! -name . \( ! -user $U -o ! -group $U \) | sed 's|^\./||' | sort); }
else
    # By the names of the source: every one's stat first, before anything
    # reads one (NetBSD restores atimes only where they are on, and hard
    # links share them), then the rest; then names that are new.
    listing() {  # DIR
        (cd "$1" && while IFS= read -r p; do
            s=$(stat -f 'mode=%Sp flags=%Xf atime=%Fa mtime=%Fm' "$p" 2>/dev/null) || s=MISSING
            # (OpenBSD fills st_rdev for every file: devices' alone.)
            r=-; if [ -b "$p" ] || [ -c "$p" ]; then r=$(stat -f %Hr,%Lr "$p"); fi
            s="$s rdev=$r"
            b=-
            case $s.$os in MISSING*) ;; *.FreeBSD|*.NetBSD)
                b=$(stat -f '%FB %Fm' "$p" | awk '{ split($1, b, "."); split($2, m, ".")
                    print (b[1] + 0 < m[1] + 0 || (b[1] + 0 == m[1] + 0 && b[2] + 0 <= m[2] + 0)) ? $1 : $2 }') ;;
            esac
            printf '%s\t%s birth=%s\n' "${p#./}" "$s" "$b"
        done <$OUT/names >$OUT/listing.stat
        while IFS= read -r p; do
            [ -e "$p" ] || [ -L "$p" ] || { echo; continue; }
            x=""; v=""
            for ns in user system; do
                for a in $(lsextattr -qh $ns "$p" 2>/dev/null | tr ' \t' '\n\n' | sort); do
                    case $ns.$a in system.posix1e.acl_access|system.posix1e.acl_default|system.nfs4.acl) continue ;; esac
                    x="$x,$ns.$a"; v="$v$(getextattr -qh $ns $a "$p" | md5)"
                done
            done
            acl=-; [ -L "$p" ] || acl=$(getfacl -qn "$p" 2>/dev/null | md5 | cut -c1-8)
            sha=-; [ -f "$p" ] && [ ! -L "$p" ] && sha=$(sha256 <"$p" | cut -c1-8)
            size=-; [ -d "$p" ] || size=$(stat -f %z "$p")
            printf 'size=%s sha=%s xattrs=%s xvalues=%s acl=%s\n' "$size" "$sha" "${x#,}" \
                "$(printf %s "$v" | md5 | cut -c1-8)" "$acl" |
                sed 's/xattrs= /xattrs=- /; s/xvalues=d41d8cd9/xvalues=-/'
        done <$OUT/names >$OUT/listing.rest
        paste -d ' ' $OUT/listing.stat $OUT/listing.rest | grep -v '	MISSING '
        find . ! -name . ! -type s | sort | while IFS= read -r p; do
            grep -qxF "$p" $OUT/names || printf '%s\ttype=new\n' "${p#./}"
        done)
    }
    owners() { (cd "$1" && find . ! -name . \( ! -user $U -o ! -group $U \) | sed 's|^\./||' | sort); }
fi
# What differs between two listings, per name: "key a -> b".
differ() {  # A B
    awk -F '\t' 'NR == FNR { a[$1] = $2; next }
        { seen[$1] = 1
          if (!($1 in a)) { print "  " $1 ": not in the source"; next }
          if (a[$1] == $2) next
          n = split(a[$1], x, " "); split($2, y, " "); s = ""
          for (i = 1; i <= n; i++) if (x[i] != y[i]) s = s "; " x[i] " -> " substr(y[i], index(y[i], "=") + 1)
          print "  " $1 ": " substr(s, 3) }
        END { for (p in a) if (!(p in seen)) print "  " p ": missing" }' "$1" "$2" | sort
}

# NetBSD: atimes off while the source is listed and backed up.
[ -x $FS/atimes ] && $FS/atimes off
(cd $SRC && find . ! -name . ! -type s | sort) >$OUT/names
listing $SRC >$OUT/source.list
echo "== source: $(wc -l <$OUT/source.list | tr -d ' ') names"
sed 's/^/  /' $OUT/source.list
sockets=$(find $SRC -type s | wc -l | tr -d ' ')
echo "== sockets in the source (in no listing): $sockets"

# --- backups, as root -------------------------------------------------------
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
for b in restic rustic; do
    repo=$FS/repo-$b
    if [ $b = rustic ]; then
        $RUSTIC --no-progress --no-cache -r $repo init >/dev/null 2>&1 &&
            $RUSTIC --no-progress --no-cache -r $repo backup --set-atime yes $SRC >/dev/null 2>$OUT/backup-$b.msgs
    else
        $RESTIC -r $repo init -q && $RESTIC -r $repo backup -q $SRC 2>$OUT/backup-$b.msgs
    fi
    echo "== backup with $b as root: exit $?"
    grep -v -e 'failed to get system time zone' -e '^\[INFO\]' $OUT/backup-$b.msgs | sed 's/^/  /'
done
[ -x $FS/atimes ] && $FS/atimes on
# D1's precondition: the order of xattr-order's attributes in each snapshot.
snap=""
echo "== xattr-order in the snapshots (D1 needs the attribute root alone may set first; not a check)"
for b in restic rustic; do
    if rt_nodes $RESTIC $FS/repo-$b $SRC >$OUT/nodes-$b; then
        o=$(awk -F '\t' '$1 == "xattr-order"' $OUT/nodes-$b | tr '\t' '\n' | sed -n 's/^xattr_names=//p')
        g=$(awk -F '\t' '$1 == "xattr-order"' $OUT/nodes-$b | tr '\t' '\n' | grep '^generic_attributes=' | cut -c1-120)
        echo "  $b's: extended attributes in this order: ${o:-none}${g:+; $g}"
        n=$(grep -c 'type="socket"' $OUT/nodes-$b); m=$(grep -ci socket $OUT/backup-$b.msgs)
        echo "  $b's: socket nodes: $n; backup messages naming a socket: $m (both must be 0)"
        snap="$snap
  backup by $b: $n socket nodes in the snapshot, $m messages naming a socket"
    else
        echo "  $b's: can't decode it"
    fi
    # The restoring user takes a lock, and reads everything.
    chmod -R a+rwX $FS/repo-$b
done

# --- restores, as uid 4321 --------------------------------------------------
cat >$FS/restore.sh <<'EOF'
# restore.sh TOOL REPO SNAPSHOT-PATH TARGET
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
case $(basename "$1") in
    *rustic*) "$1" --no-progress --no-cache -r "$2" restore --numeric-id --sparse by-content "$3" "$4" ;;
    *) "$1" -r "$2" --no-cache restore -q --sparse "$3" --target "$4" ;;
esac
EOF
summary=""
for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${d%-*} r=${d#*-}
    tool=$RESTIC; [ $r = rustic ] && tool=$RUSTIC
    dst=$FS/nr-$d
    mkdir $dst && chown $U:$U $dst
    as_user sh $FS/restore.sh $tool $FS/repo-$b "latest:$SRC" $dst >$OUT/$d.msgs 2>&1
    status=$?
    # Messages but rustic's progress and time-zone lines.
    grep -v -e 'failed to get system time zone' -e '^\[INFO\]' -e '^Files: ' -e '^Dirs: ' \
        -e '^restore done' $OUT/$d.msgs >$OUT/$d.msgs.f
    listing $dst >$OUT/$d.list
    echo "== backed up by $b, restored by $r as uid $U: exit $status"
    owner=$(grep -c 'setting UID/GID failed' $OUT/$d.msgs.f)
    echo "-- messages: $(wc -l <$OUT/$d.msgs.f | tr -d ' ') lines; $owner of them \"NAME: setting UID/GID failed\", not shown:"
    grep -v 'setting UID/GID failed' $OUT/$d.msgs.f | sed -e "s|$FS/||g" -e 's/^/  /' | head -n 60
    echo "-- names not owned by $U:$U: $(owners $dst | wc -l | tr -d ' ')"
    owners $dst | sed 's/^/  /'
    socks=$(find $dst -type s | wc -l | tr -d ' ') msock=$(grep -ci socket $OUT/$d.msgs.f)
    echo "-- sockets restored: $socks; messages naming a socket: $msock (both must be 0)"
    find $dst -type s | sed "s|^$dst/|  |"
    echo "-- what differs from the source (but owners):"
    differ $OUT/source.list $OUT/$d.list | tee $OUT/$d.diff
    d1=no; awk -F '\t' '$1 == "xattr-order"' $OUT/$d.list | grep -q 'xattrs=[^ ]*user\.b' && d1=yes
    awk -F '\t' '$1 == "xattr-order"' $OUT/source.list | grep -q 'xattrs=[^ ]*user\.b' ||
        d1="n/a (no user.b on xattr-order here)"
    summary="$summary
  $d: exit $status, $(wc -l <$OUT/$d.msgs.f | tr -d ' ') message lines, $(wc -l <$OUT/$d.diff | tr -d ' ') names differ; D1, user.b back on xattr-order: $d1; sockets restored: $socks, messages naming one: $msock"
done
for pair in "restic-restic rustic-rustic" "restic-restic restic-rustic" "rustic-rustic rustic-restic"; do
    set -- $pair
    echo "== $1 vs $2 (what differs between the two restores)"
    differ $OUT/$1.list $OUT/$2.list | tee $OUT/$1-vs-$2.diff
    [ -s $OUT/$1-vs-$2.diff ] || echo "  identical"
    summary="$summary
  $1 vs $2: $(wc -l <$OUT/$1-vs-$2.diff | tr -d ' ') names differ"
done
echo "== summary (sockets in the source: $sockets)$snap$summary"
