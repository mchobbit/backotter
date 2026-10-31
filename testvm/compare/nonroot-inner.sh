#!/bin/sh
# The inside of nonroot-rt.sh: run as root on Linux or FreeBSD, on one file
# system.  Makes a tree in FSDIR/src, backs it up as root with each tool
# (each its own repository), restores it as uid 4321 into a directory that
# uid owns, in all four directions, and reports per direction the exit
# status, the messages, and every name whose metadata differs from the
# source's; then what differs between the tools' restores.
#
# Usage: nonroot-inner.sh FSDIR RESTIC RUSTIC
# Linux: needs python3, and FSMETA_DIR: the directory with fsmeta.py
# (testvm/compare), for its ioctls.

FS=$1 RESTIC=$2 RUSTIC=$3
U=4321
SRC=$FS/src OUT=$FS/out
export LC_ALL=C
os=$(uname -s)
mkdir -p $OUT
chmod 755 $FS
try() { "$@" 2>/dev/null || echo "nonroot: skipped (unsupported here): $*"; }

as_user() {  # COMMAND...: as uid 4321, with its own HOME
    mkdir -p $FS/home && chown $U:$U $FS/home
    if [ $os = Linux ]; then
        setpriv --reuid=$U --regid=$U --clear-groups env HOME=$FS/home "$@"
    else
        su -m bkonr -c "env HOME=$FS/home $*"
    fi
}

# --- the tree --------------------------------------------------------------
mkdir -p $SRC && cd $SRC || exit 1
echo plain >plain
echo owned >owned-1234; chown 1234:5678 owned-1234
echo setuid >setuid; chmod 4755 setuid
echo setgid >setgid; chmod 2755 setgid
echo linked >hardlink-a; ln hardlink-a hardlink-b
ln -s plain symlink
mkfifo fifo
echo times >times-ns
if [ $os = Linux ]; then
    touch -d '2004-04-04 04:04:04.444444444' times-ns
    touch -a -d '2003-03-03 03:03:03.333333333' times-ns
    try mknod chardev c 1 3
    echo x >xattrs; setfattr -n user.bko -v hello xattrs; try setfattr -n trusted.bko -v root-only xattrs
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
else
    touch -d 2004-04-04T04:04:04.444444444 times-ns
    touch -a -d 2003-03-03T03:03:03.333333333 times-ns
    try mknod chardev c 0 13 root:wheel
    echo x >xattrs; setextattr user bko hello xattrs; try setextattr system bko root-only xattrs
    echo acl >acl
    setfacl -m u:65534:rw::allow acl 2>/dev/null || try setfacl -m u:65534:r,m::r acl
    # Birth times: born 2001, changed 2010 (the owner can lower a birth time
    # with utimensat too).
    echo born >born; touch -t 200101010101 born; touch -t 201001010101 born
    mkdir born-dir; touch -t 200101010101 born-dir; touch -t 201001010101 born-dir
    # File flags: UF_* (0xffff) are the owner's, SF_* root's.  ZFS refuses
    # uchg, uappnd and uunlnk, and gives a new file uarch.
    for f in nodump uchg uappnd uunlnk hidden system rdonly offline sparse uarch schg sappnd; do
        echo $f >flag-$f; try chflags $f flag-$f
    done
    echo x >flag-hidden-schg; try chflags hidden,schg flag-hidden-schg
    mkdir flag-hidden-dir; try chflags hidden flag-hidden-dir
    echo x >flag-noarchive; try chflags nouarch flag-noarchive
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
else
    listing() {  # DIR
        (cd "$1" && find . ! -name . | sort | while IFS= read -r p; do
            n=${p#./}
            x=""; v=""
            for ns in user system; do
                for a in $(lsextattr -qh $ns "$p" 2>/dev/null | tr ' \t' '\n\n' | sort); do
                    x="$x,$ns.$a"; v="$v$(getextattr -qh $ns $a "$p" | md5)"
                done
            done
            acl=-; [ -L "$p" ] || acl=$(getfacl -qn "$p" 2>/dev/null | md5 | cut -c1-8)
            sha=-; [ -f "$p" ] && [ ! -L "$p" ] && sha=$(sha256 -q "$p" | cut -c1-8)
            size=-; [ -d "$p" ] || size=$(stat -f %z "$p")
            printf '%s\t%s size=%s birth=%s sha=%s xattrs=%s xvalues=%s acl=%s\n' "$n" \
                "$(stat -f 'mode=%Sp flags=%Xf atime=%Fa mtime=%Fm rdev=%Hr,%Lr' "$p")" "$size" \
                "$(stat -f '%FB %Fm' "$p" | awk '{ split($1, b, "."); split($2, m, ".")
                    print (b[1] + 0 < m[1] + 0 || (b[1] + 0 == m[1] + 0 && b[2] + 0 <= m[2] + 0)) ? $1 : $2 }')" \
                "$sha" "${x#,}" "$(printf %s "$v" | md5 | cut -c1-8)" "$acl" |
                sed 's/xattrs= /xattrs=- /; s/xvalues=d41d8cd9/xvalues=-/'
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

listing $SRC >$OUT/source.list
echo "== source: $(wc -l <$OUT/source.list | tr -d ' ') names"
sed 's/^/  /' $OUT/source.list

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
    grep -v 'failed to get system time zone' $OUT/backup-$b.msgs | sed 's/^/  /'
    # The restoring user takes a lock, and reads everything.
    chmod -R a+rwX $repo
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
    echo "-- what differs from the source (but owners):"
    differ $OUT/source.list $OUT/$d.list | tee $OUT/$d.diff
    summary="$summary
  $d: exit $status, $(wc -l <$OUT/$d.msgs.f | tr -d ' ') message lines, $(wc -l <$OUT/$d.diff | tr -d ' ') names differ"
done
for pair in "restic-restic rustic-rustic" "restic-restic restic-rustic" "rustic-rustic rustic-restic"; do
    set -- $pair
    echo "== $1 vs $2 (what differs between the two restores)"
    differ $OUT/$1.list $OUT/$2.list | tee $OUT/$1-vs-$2.diff
    [ -s $OUT/$1-vs-$2.diff ] || echo "  identical"
    summary="$summary
  $1 vs $2: $(wc -l <$OUT/$1-vs-$2.diff | tr -d ' ') names differ"
done
echo "== summary$summary"
