#!/bin/sh
# SticTest1 chimera, the part that runs in the throwaway guest, as root, from
# /root/c4 (restic-bko, rustic-bko, fsmeta.py, rt-common.sh beside it).
# POSIX sh and Chimera's userland: FreeBSD's sh and tools (chimerautils:
# stat -f, no stat -c, no date -d, sed -i takes a suffix), util-linux,
# e2fsprogs, xfsprogs, and attr-progs, acl-progs, libcap-progs (check.sh
# installs those three).  No bash.
#
# The fixture on loop XFS made like / (xfs_info's features) and loop ext4
# made like /boot (tune2fs's), one pass each: restic->restic, rustic->rustic,
# restic->rustic, rustic->restic into fresh file systems, each tool restoring
# once more over its own restore with changed immutable/append-only files,
# the two tools' snapshots compared node by node, each tool's check
# --read-data of the other's repository; then one non-root run.  Prints
# PASS/FAIL/LIMIT lines.
set -u
C=/root/c4
cd $C || exit 2
R=$C/restic-bko U=$C/rustic-bko
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
RT_TMP=$C/rt; mkdir -p $RT_TMP; export RT_TMP
. $C/rt-common.sh
W=$C/w; mkdir -p $W
pass() { echo "PASS $1: $2"; }
fail() { echo "FAIL $1: $2"; }
limit() { echo "LIMIT $1: $2"; }
oneline() { tr '\n' ' ' | cut -c1-${1:-600}; }
n() { echo $(( $(wc -l <"$1") )); }  # BSD wc pads its count
TS_M=2002-03-04T05:06:07.111111111 TS_A=2003-04-05T06:07:08.222222222

# --- What the guest has --------------------------------------------------
echo "== guest: $(uname -r); $(. /etc/os-release; echo "$PRETTY_NAME ($ID)"); sh $(readlink -f /bin/sh); musl $(ls /usr/lib/ld-musl* 2>/dev/null)"
echo "== mounts: $(findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS --target /); $(findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS --target /boot)"
echo "== LSMs: $(cat /sys/kernel/security/lsm)"
echo "== tools: $(apk info -e attr-progs acl-progs libcap-progs xfsprogs e2fsprogs 2>&1 | oneline)"
# Without them the fixture lacks what they set, and fsmeta still finds the
# restores equal to it: their absence is a FAIL, not a smaller fixture.
TOOLS="setfattr getfattr setfacl getfacl setcap getcap chattr lsattr mkfs.xfs xfs_io xfs_info mkfs.ext4 tune2fs python3"
miss=; for t in $TOOLS; do command -v $t >/dev/null 2>&1 || miss="$miss $t"; done
if [ -z "$miss" ]; then pass "tools-present" "$TOOLS"
else fail "tools-present" "not in the guest:$miss"; fi
rv=$($R version 2>&1 | head -n 1); rc1=$?; uv=$($U --version 2>&1 | head -n 1); rc2=$?
echo "== versions: $rv / $uv"
file $R $U 2>&1
if $R version >/dev/null 2>&1 && $U --version >/dev/null 2>&1; then
    pass "tools-run" "restic-bko and rustic-bko run on Chimera (musl, $(uname -r)): $rv; $uv"
else fail "tools-run" "restic-bko version or rustic-bko --version failed: $rv; $uv"; fi

# XFS features of / (xfs_info), as mkfs.xfs options, and ext4 features of
# /boot (tune2fs), as mkfs.ext4's.
xfsfeat() { xfs_info "$1" | grep -o '\(isize\|crc\|finobt\|sparse\|rmapbt\|reflink\|bigtime\|inobtcount\|nrext64\|exchange\|metadir\|parent\|ftype\|projid32bit\|attr\|ascii-ci\)=[0-9]*' | tr '\n' ' '; }
xfsopts=$(xfs_info / | awk '{
    for (i = 1; i <= NF; i++) { f = $i; gsub(",", "", f)
        if (f ~ /^(crc|finobt|rmapbt|reflink|bigtime|inobtcount|metadir)=/) m = m "," f
        else if (f ~ /^(sparse|nrext64|exchange)=/) ino = ino "," f
        else if (f ~ /^parent=/) nm = nm "," f }
} END { printf "-m %s -i %s", substr(m, 2), substr(ino, 2); if (nm != "") printf " -n %s", substr(nm, 2) }')
extfeat() { tune2fs -l "$1" | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | tr '\n' ' '; }
bootdev=$(findmnt -n -o SOURCE /boot)
bootfeat=$(extfeat "$bootdev")
bootisz=$(tune2fs -l "$bootdev" | sed -n 's/^Inode size: *//p')
extO=$(echo "$bootfeat" | tr ' ' '\n' | grep . | grep -v -e '^has_journal$' | tr '\n' ',' | sed 's/,$//')
echo "== / XFS: $(xfsfeat /); mkfs.xfs $xfsopts"
echo "== /boot ext4: $bootfeat inode size $bootisz; mkfs.ext4 -O $extO"
mkfs_xfs() { mkfs.xfs -q -f $xfsopts "$1"; }
mkfs_ext4() { mkfs.ext4 -q -F -b 4096 -I "$bootisz" -O "$extO" "$1"; }
mkimg() {  # FS NAME [SIZE_MIB]: a fresh FS image, mounted at /mnt/NAME
    img=$W/$2.img
    rm -f -- "$img"; truncate -s "${3:-640}M" "$img"
    mkfs_$1 "$img" >$W/mkfs-$2.log 2>&1 || { echo "mkfs.$1 $2: $(cat $W/mkfs-$2.log | oneline)"; return 1; }
    mkdir -p "/mnt/$2" && mount -o loop "$img" "/mnt/$2"
}
rmimg() { umount "/mnt/$1" 2>/dev/null; rm -f -- "$W/$1.img"; }

# XFS's own per-file options (fsxattr), the preallocated flag (p, 0x2) aside:
# a dense restore preallocates, a sparse one doesn't (both BACKOTTER.md,
# linux.xfs_options).
XFSX="xfs-extsize xfs-extsize-dir xfs-extsize-dir/inherits xfs-nodefrag xfs-filestream xfs-cowextsize xfs-nosymlinks"
xfsx() {  # TREE [PATH...]: each PATH's (default $XFSX) fsxattr, one line each
    t=$1; shift; [ $# -gt 0 ] || set -- $XFSX
    for p; do
        printf '%s ' $p; xfs_io -r -c stat "$t/$p" 2>&1 | grep -E 'fsxattr\.(xflags|extsize|cowextsize)' |
            awk '{ if ($1 == "fsxattr.xflags") { v = $3; sub(/^0x/, "", v); x = 0
                     for (i = 1; i <= length(v); i++) x = x * 16 + index("0123456789abcdef", tolower(substr(v, i, 1))) - 1
                     if (int(x / 2) % 2) x -= 2; printf "xflags=0x%x ", x }
                   else printf "%s=%s ", substr($1, 9), $3 }'
        echo
    done
}
# COV_JSON KIND,KIND...: the kinds fsmeta's coverage counts none of.
covcheck() { python3 -c 'import json,sys; c=json.load(open(sys.argv[1]))["coverage"]; print(", ".join(k for k in sys.argv[2].split(",") if not c.get(k)))' "$1" "$2"; }
# A directory's own mode, owner, times (FreeBSD stat), xattrs and ACL.
dirmeta() { for d; do stat -f '%N %Lp %u %g %Fm %Fa' "$d"; getfattr -h -d -m - -e hex "$d" 2>/dev/null | grep -v '^#' | grep . | sort; done | oneline 900; }

fixture() {  # DIR FS: the fixture in DIR, on a file system of type FS
    T=$1 fs=$2
    mkdir -p "$T" && cd "$T" || return 1
    # Owners with no name, set-id and sticky modes.
    : >no-such-user; chown 1234:5678 no-such-user
    : >bigid; chown 70000:70001 bigid
    : >setuid; chown 4321:4322 setuid; chmod 4755 setuid
    : >setgid; chmod 2755 setgid
    mkdir sticky-dir setgid-dir; chmod 1777 sticky-dir; chown 4321:4322 setgid-dir; chmod 2775 setgid-dir
    # The guest's own set-id files, copied with their modes.
    mkdir real-setid
    find / -xdev \( -perm -4000 -o -perm -2000 \) -type f 2>/dev/null | head -n 5 | while read -r f; do cp -p "$f" real-setid/; done
    echo "real set-id files: $(ls real-setid | oneline)"
    # POSIX ACLs, access and default, and a file that inherited the default.
    : >acl-file; setfacl -m u:1234:rwx,g:5678:r acl-file
    mkdir acl-dir; setfacl -m u:1234:rx acl-dir; setfacl -d -m u:1234:rwx,g:5678:rx acl-dir
    : >acl-dir/inherits; mkdir acl-dir/subdir
    # xattrs: user (empty, binary, large), trusted, security; files, dirs, symlinks.
    : >xattr-file
    setfattr -n user.backotter -v hello xattr-file
    setfattr -n user.empty xattr-file
    setfattr -n user.binary -v 0x00ff10fe7f xattr-file
    setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" xattr-file
    setfattr -n trusted.backotter -v root-only xattr-file
    setfattr -n security.backotter -v sec-label xattr-file || echo "security.backotter refused on $fs"
    mkdir xattr-dir; setfattr -n user.dir -v dirval xattr-dir; setfattr -n trusted.dir -v 0x00ff01 xattr-dir
    if [ "$fs" = xfs ]; then
        # The guest's own xattr: trusted.xfs:autofsck on / itself.
        mkdir autofsck-dir; setfattr -n trusted.xfs:autofsck -v repair autofsck-dir
        echo "guest / trusted.xfs:autofsck=$(getfattr --absolute-names -n trusted.xfs:autofsck --only-values / 2>&1)"
    fi
    # File capabilities: the guest's own (Chimera's tarball gives ping and
    # clockdiff theirs; images built before the fix after C7 lost them to
    # bootstrap/chimera.sh's GNU tar, so "if it has any"), one set here, and
    # a raw one.
    mkdir real-caps
    getcap -r /usr/bin 2>/dev/null | while read -r f c; do
        cp -p "$f" real-caps/ && setcap "$c" "real-caps/$(basename "$f")" && echo "guest capability: $f $c"
    done
    [ -n "$(ls real-caps)" ] || echo "guest capabilities: none under /usr/bin"
    cp /usr/bin/true cap-binary; setcap cap_net_raw,cap_net_admin+ep cap-binary || echo "setcap refused on $fs"
    : >cap-root-ns; setfattr -n security.capability -v 0x0100000200200000000000000000000000000000 cap-root-ns
    # Hard links (and the guest's own link group, if it has one).
    echo linked >hardlink-a; ln hardlink-a hardlink-b; mkdir sub; ln hardlink-a sub/hardlink-c
    mkdir real-links
    hlf=$(find /usr -xdev -type f -links +1 2>/dev/null | head -n 1)
    if [ -n "$hlf" ]; then
        # Numbered: the group's names may be one basename in two directories.
        first= i=0
        for f in $(find / -xdev -inum "$(stat -f %i "$hlf")" 2>/dev/null); do
            i=$((i + 1))
            if [ -z "$first" ]; then first=real-links/$i-$(basename "$f"); cp -p "$f" "$first"
            else ln "$first" "real-links/$i-$(basename "$f")"; fi
            echo "  guest link $i: $f"
        done
        echo "guest hard-link group: $hlf, $(stat -f %l "$hlf") links ($(ls real-links | oneline))"
    else echo "guest hard-link group: none under /usr"; fi
    # Symlinks, with their own xattrs and times.
    ln -s hardlink-a symlink; ln -s /nonexistent/target symlink-dangling; ln -s ../xattr-dir sub/symlink-to-dir
    ln -s "$(head -c 900 /dev/zero | tr '\0' y)" symlink-long
    ln -s hardlink-a symlink-xattr; setfattr -h -n trusted.onlink -v yes symlink-xattr
    # Devices (rdev 0 too), FIFOs, a socket (both tools skip sockets).
    mknod chardev c 1 3; mknod blockdev b 7 0; mknod rdev0 c 0 0; mknod rdev0-b b 0 0; mkfifo fifo; mkfifo -m 600 fifo600
    python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' socket
    # Sparse files.
    truncate -s 64M sparse; printf start | dd of=sparse conv=notrunc status=none
    printf middle | dd of=sparse bs=1048576 seek=32 conv=notrunc status=none
    truncate -s 8M all-hole
    truncate -s 16M data-at-end; head -c 4096 /dev/urandom | dd of=data-at-end bs=4096 seek=4095 conv=notrunc status=none
    head -c 262144 /dev/urandom >sparse-small; truncate -s +8192 sparse-small; head -c 262144 /dev/urandom >>sparse-small
    head -c 4194304 /dev/urandom >random
    fallocate -l 4M preallocated; head -c 2097152 /dev/zero >zeros
    # Odd names, deep, empty and big directories.
    : >"name with spaces"; : >"$(printf 'new\nline')"; : >"$(printf '\377\376-not-utf8')"; : >"$(printf 'utf8-\303\251')"
    : >"$(head -c 250 /dev/zero | tr '\0' n)"
    d=deep; for i in $(seq 40); do d=$d/level$i; done; mkdir -p $d; echo bottom >$d/bottom
    mkdir empty-dir; : >empty
    mkdir big-dir; for i in $(seq 600); do : >"big-dir/entry-$i-padding-padding-padding"; done
    # Project IDs (XFS's projid32bit; ext4 only with its project feature).
    echo project >projid-file; chattr -p 42 projid-file 2>/dev/null || echo "chattr -p refused on $fs"
    mkdir projid-dir; chattr +P -p 43 projid-dir 2>/dev/null || echo "chattr +P -p refused on $fs"
    : >projid-dir/inherits
    if [ "$fs" = xfs ]; then
        # XFS's own per-file options (xfs_io; linux.xfs_options).
        : >xfs-extsize; xfs_io -c "extsize 1m" xfs-extsize; head -c 65536 /dev/urandom >>xfs-extsize
        mkdir xfs-extsize-dir; xfs_io -c "extsize 2m" xfs-extsize-dir; head -c 65536 /dev/urandom >xfs-extsize-dir/inherits
        echo n >xfs-nodefrag; xfs_io -c "chattr +f" xfs-nodefrag
        mkdir xfs-filestream; xfs_io -c "chattr +S" xfs-filestream
        : >xfs-cowextsize; xfs_io -c "cowextsize 1m" xfs-cowextsize; head -c 65536 /dev/urandom >>xfs-cowextsize
        mkdir xfs-nosymlinks; xfs_io -c "chattr +n" xfs-nosymlinks
        mkdir xfs-realtime-inherit; xfs_io -c "chattr +t" xfs-realtime-inherit 2>&1 | head -n 2
    fi
    # Times, deepest first (BSD touch: -d YYYY-MM-DDThh:mm:SS.frac), and an
    # old and a far-future one; then the flags that would block them.
    touch -d 1970-01-02T00:00:00.123456789 old; touch -d 2100-01-01T12:00:00.987654321 future
    echo atime >atime; mkdir atime-dir; ln -s atime atime-symlink
    : >nodump; : >noatime; : >sync; mkdir flagdir
    echo append >append-only; echo fixed >immutable; mkdir immutable-dir; echo in >immutable-dir/child
    : >immutable-nodump; mkdir append-dir; echo in >append-dir/child
    # ext4's dirsync (D), topdir (T) and data journalling (j).  XFS refuses
    # D (its fileattr takes no FS_DIRSYNC_FL: "chattr +D refused on xfs").
    mkdir dirsync-dir; echo in >dirsync-dir/child
    [ "$fs" = ext4 ] && { mkdir topdir; echo journalled >journal-data; }
    find . -depth ! -name old ! -name future -exec touch -h -m -d $TS_M {} + -exec touch -h -a -d $TS_A {} +
    for p in atime atime-dir atime-symlink symlink-xattr; do
        touch -h -m -d 2004-05-06T07:08:09.333333333 $p; touch -h -a -d 2005-06-07T08:09:10.444444444 $p; done
    chattr +d nodump; chattr +A noatime; chattr +S sync; chattr +d +A flagdir
    chattr +D dirsync-dir || echo "chattr +D refused on $fs"
    if [ "$fs" = ext4 ]; then chattr +T topdir || echo "chattr +T refused on $fs"; chattr +j journal-data || echo "chattr +j refused on $fs"; fi
    chattr +a append-only append-dir; chattr +i immutable immutable-dir; chattr +i +d immutable-nodump
    echo "flags kept on $fs: $(lsattr -d nodump noatime sync append-only immutable immutable-dir immutable-nodump flagdir append-dir dirsync-dir $([ "$fs" = ext4 ] && echo topdir journal-data) 2>&1 | oneline)"
    echo "projects on $fs: $(lsattr -p -d projid-file projid-dir 2>&1 | oneline)"
    cd /
}

restore() {  # TOOL REPO DEST
    if [ $1 = U ]; then $U --no-progress --no-cache -r "$2" restore --numeric-id latest "$3"
    else $R -r "$2" restore latest --target "$3"; fi
}
msgs() {  # LOG: a restore's warnings and errors, the expected ones aside
    grep -v -e '^\[INFO\]' -e 'additional entries exist in destination' "$1" | grep -iE 'warn|error|fail|denied|not permitted' | head -n 3 | oneline 400
}

for fs in xfs ext4; do
    S=src-$fs
    mkimg $fs $S || { fail "scratch-$fs" "no source $fs file system"; continue; }
    if [ $fs = xfs ]; then a=$(xfsfeat /); b=$(xfsfeat /mnt/$S); what="/'s XFS"
    else a=$bootfeat; b=$(extfeat $(findmnt -n -o SOURCE /mnt/$S)); what="/boot's ext4"; fi
    if [ "$a" = "$b" ]; then pass "scratch-$fs" "the scratch $fs has $what features: $b"
    else fail "scratch-$fs" "$what: $a; scratch: $b"; fi
    # The directories above the backed-up path, with metadata of their own.
    mkdir -p /mnt/$S/anc1/anc2; chown 1111:2222 /mnt/$S/anc1; chmod 0751 /mnt/$S/anc1
    chmod 0705 /mnt/$S/anc1/anc2; chown 3333:4444 /mnt/$S/anc1/anc2
    setfattr -n user.anc -v up /mnt/$S/anc1; setfacl -m u:1234:rx /mnt/$S/anc1/anc2
    P=/mnt/$S/anc1/anc2/tree
    fixture $P $fs >$W/fixture-$fs.log 2>&1; sed 's/^/  fixture: /' $W/fixture-$fs.log
    touch -d 2001-02-03T04:05:06.5 $P /mnt/$S/anc1/anc2; touch -d 1999-09-09T09:09:09.25 /mnt/$S/anc1
    sync; mount -o remount,noatime /mnt/$S
    python3 $C/fsmeta.py record $P $W/src-$fs.json
    # Sockets are skipped by design (both tools): not expected back.
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d.pop("socket",None); json.dump(d,open(sys.argv[1],"w"))' $W/src-$fs.json
    [ $fs = xfs ] && { xfsx $P >$W/src-$fs.xfsx; xfsx $P xfs-realtime-inherit >$W/src-$fs.rtx; rtkept= rtlost= rtback=; }
    anc_src=$(dirmeta /mnt/$S/anc1 /mnt/$S/anc1/anc2)
    for t in R U; do
        b=$R; [ $t = U ] && b=$U
        if ( cd /; rt_backup $b $W/repo-$fs-$t $P ) >$W/bk-$fs-$t.log 2>&1; then pass "backup-$fs-$t" "$(basename $b) backed up the fixture on $fs, exit 0"
        else fail "backup-$fs-$t" "$(basename $b) backup on $fs failed: $(tail -n 3 $W/bk-$fs-$t.log | oneline)"; fi
        sed 's/^/    backup msg: /' $W/bk-$fs-$t.log | head -n 10
    done
    python3 $C/fsmeta.py record $P $W/src-after-$fs.json
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d.pop("socket",None); json.dump(d,open(sys.argv[1],"w"))' $W/src-after-$fs.json
    if out=$(python3 $C/fsmeta.py compare $W/src-$fs.json $W/src-after-$fs.json --json $W/cov-$fs.json 2>&1); then
        pass "backup-leaves-source-$fs" "after both backups: $out"
    else fail "backup-leaves-source-$fs" "the backups changed the source: $(echo "$out" | oneline)"; fi
    echo "  coverage $fs: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["coverage"])' $W/cov-$fs.json)"
    # What the fixture sets, in the source as fsmeta saw it (a tool missing
    # or refused leaves it out, and the restores would still compare equal).
    covwant="acl,user-xattr,trusted-xattr,capability,immutable,append,nodump,noatime,hard-linked,symlink,chardev,blockdev,fifo,with holes,setuid/setgid/sticky"
    [ $fs = xfs ] && covwant="$covwant,project ID"
    if covmiss=$(covcheck $W/cov-$fs.json "$covwant" 2>&1) && [ -z "$covmiss" ]; then
        pass "fixture-coverage-$fs" "the source has each of: $covwant"
    else fail "fixture-coverage-$fs" "not in the source (fsmeta coverage): $covmiss"; fi
    # Same repository format: snapshots node-identical.
    rt_nodes $R $W/repo-$fs-R $P >$W/nodes-$fs-R.txt 2>$W/nodes-$fs-R.err
    rt_nodes $R $W/repo-$fs-U $P >$W/nodes-$fs-U.txt 2>$W/nodes-$fs-U.err
    if [ -s $W/nodes-$fs-R.txt ] && rt_node_diff $W/nodes-$fs-R.txt $W/nodes-$fs-U.txt >$W/nodediff-$fs.txt 2>&1; then
        pass "snapshots-identical-$fs" "$(n $W/nodes-$fs-R.txt) nodes, restic and rustic snapshots node-identical; $(grep -c 'not a check' $W/nodediff-$fs.txt) not-a-check lines"
    else fail "snapshots-identical-$fs" "$(head -n 8 $W/nodediff-$fs.txt $W/nodes-$fs-R.err | oneline)"; fi
    if grep -q '^socket	' $W/nodes-$fs-R.txt $W/nodes-$fs-U.txt; then fail "sockets-skipped-$fs" "a socket node is in a snapshot"
    else pass "sockets-skipped-$fs" "no socket node in either snapshot"; fi
    # Each tool reads all of the other's repository.
    $R -r $W/repo-$fs-U check --read-data >$W/chk-$fs-RU.log 2>&1; c1=$?
    $U --no-progress --no-cache -r $W/repo-$fs-R check --read-data >$W/chk-$fs-UR.log 2>&1; c2=$?
    if [ $c1 = 0 ] && [ $c2 = 0 ]; then pass "cross-check-$fs" "restic-bko check --read-data of rustic's repository and rustic-bko's of restic's: exit 0"
    else fail "cross-check-$fs" "restic on rustic's exit $c1, rustic on restic's exit $c2: $(tail -n 3 $W/chk-$fs-RU.log $W/chk-$fs-UR.log | oneline)"; fi
    for d in RR UU RU UR; do
        b=${d%?} r=${d#?}
        D=dst-$fs-$d
        mkimg $fs $D || { fail "roundtrip-$fs-$d" "no target file system"; continue; }
        restore $r $W/repo-$fs-$b /mnt/$D >$W/rs-$fs-$d.log 2>&1; rc=$?
        sync; mount -o remount,noatime /mnt/$D
        python3 $C/fsmeta.py record /mnt/$D$P $W/dst-$fs-$d.json
        cmpout=$(python3 $C/fsmeta.py compare $W/src-$fs.json $W/dst-$fs-$d.json 2>&1); crc=$?
        warn=$(msgs $W/rs-$fs-$d.log)
        if [ $rc = 0 ] && [ $crc = 0 ] && [ -z "$warn" ]; then pass "roundtrip-$fs-$d" "$cmpout; restore exit 0, no warnings"
        else fail "roundtrip-$fs-$d" "restore rc=$rc; fsmeta: $(echo "$cmpout" | oneline); msgs: $warn"; fi
        anc_dst=$(dirmeta /mnt/$D/mnt/$S/anc1 /mnt/$D/mnt/$S/anc1/anc2 | sed "s|/mnt/$D||g")
        if [ "$anc_src" = "$anc_dst" ]; then pass "ancestors-$fs-$d" "anc1, anc2 mode owner mtime atime xattrs ACL: $anc_dst"
        else fail "ancestors-$fs-$d" "source: $anc_src; restored: $anc_dst"; fi
        if [ $fs = xfs ]; then
            xfsx /mnt/$D$P >$W/dst-$fs-$d.xfsx
            if [ -s $W/src-$fs.xfsx ] && cmp -s $W/src-$fs.xfsx $W/dst-$fs-$d.xfsx; then
                pass "xfs-options-$d" "fsxattr (extent size hints and inheritance, cowextsize, nodefrag f, filestream S, nosymlinks n; p aside) identical: $(oneline <$W/src-$fs.xfsx)"
            else fail "xfs-options-$d" "$(diff $W/src-$fs.xfsx $W/dst-$fs-$d.xfsx | grep '^[<>]' | head -n 6 | oneline)"; fi
            xfsx /mnt/$D$P xfs-realtime-inherit >$W/dst-$fs-$d.rtx
            if cmp -s $W/src-$fs.rtx $W/dst-$fs-$d.rtx; then rtkept="$rtkept $d"; else rtlost="$rtlost $d: $(oneline <$W/dst-$fs-$d.rtx)"; fi
        fi
        if [ $d = RR ] || [ $d = UU ]; then
            # Restore over the restore, its immutable and append-only files
            # changed since (and an immutable directory given a new file).
            X=/mnt/$D$P
            if chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
                setfattr -n user.extra -v 1 $X/immutable && echo x >$X/immutable-dir/new && chmod 600 $X/immutable-nodump &&
                chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump && echo appended >>$X/append-only; then ch=ok
            else ch=failed; fi
            echo "  over-$fs-$d before ($ch): $(lsattr -d $X/immutable $X/append-only $X/immutable-dir $X/immutable-nodump 2>&1 | sed "s|$X/||" | oneline); immutable: $(cat $X/immutable); append-only: $(cat $X/append-only | oneline)"
            restore $r $W/repo-$fs-$b /mnt/$D >$W/ov-$fs-$d.log 2>&1; rc=$?
            sync
            python3 $C/fsmeta.py record /mnt/$D$P $W/ov-$fs-$d.json
            cmpout=$(python3 $C/fsmeta.py compare $W/src-$fs.json $W/ov-$fs-$d.json 2>&1); crc=$?
            # The new file in the immutable directory isn't the snapshot's:
            # a restore leaves what it doesn't know (both tools, no --delete).
            cmpout2=$(echo "$cmpout" | grep -v -e '^extra  *1  *e.g. .immutable-dir/new.$')
            if [ $ch != ok ]; then fail "restore-over-immutable-$fs-$d" "harness: couldn't change the restored immutable/append-only files first"
            elif [ $rc = 0 ] && { [ $crc = 0 ] || [ -z "$cmpout2" ]; }; then
                pass "restore-over-immutable-$fs-$d" "changed +i file (contents, a user xattr), +i dir (a new file), +i+d file (mode), +a file (a line more) back as backed up: $(echo "$cmpout" | oneline); exit 0"
            else fail "restore-over-immutable-$fs-$d" "rc=$rc; $(echo "$cmpout" | oneline); $(msgs $W/ov-$fs-$d.log)"; fi
        fi
        if [ $fs = xfs ] && [ $d = RR ]; then
            # Would the restored directory take rtinherit back?  (xfs-rtinherit)
            xfs_io -c "chattr +t" /mnt/$D$P/xfs-realtime-inherit >$W/rtback.log 2>&1
            rtback="chattr +t on the RR restore's directory: exit $?, $(xfsx /mnt/$D$P xfs-realtime-inherit | oneline) $(oneline 200 <$W/rtback.log)"
        fi
        chattr -R -i -a /mnt/$D 2>/dev/null; rmimg $D
    done
    if [ $fs = xfs ]; then
        # rtinherit (t, 0x100) goes on a directory of an XFS with no realtime
        # device, and would go back on a restored one: only realtime on a
        # file needs the device.  Neither tool stores it: BACKOTTER.md's
        # linux.xfs_options leaves out "realtime and its inheritance" by
        # design (its reason, "which need a realtime device", holds for
        # realtime only).  A tool exclusion, not an OS or FS limit: LIMIT
        # until the user rules on keeping it.
        src_rt=$(oneline <$W/src-$fs.rtx)
        if [ -z "$rtlost" ]; then pass "xfs-rtinherit" "source: $src_rt; kept in$rtkept"
        elif [ -z "$rtkept" ] && grep -q 'xflags=0x[0-9a-f]*[13579bdf][0-9a-f][0-9a-f] ' $W/src-$fs.rtx; then
            limit "xfs-rtinherit" "tool exclusion pending the user's ruling, not an FS limit: the kernel sets rtinherit (t, 0x100) on a directory of an XFS with no realtime device (source: $src_rt) and would on a restored one, but BACKOTTER.md's linux.xfs_options leaves realtime and its inheritance out by design, so none of the 4 directions restores it:$rtlost; $rtback"
        else fail "xfs-rtinherit" "source: $src_rt; kept in:${rtkept:- none}; lost in$rtlost"; fi
    fi
    chattr -R -i -a /mnt/$S 2>/dev/null; rmimg $S
    rm -rf $W/repo-$fs-R $W/repo-$fs-U
done

# --- One non-root run per tool: uid 4321 (no name) backs up and restores its
# own tree, on XFS made like /.
asuser() {  # COMMAND...: as uid 4321, gid 4322, no supplementary groups
    python3 -c 'import os,sys; os.setgroups([]); os.setgid(4322); os.setuid(4321); os.environ["HOME"]="/mnt/nr/home"; os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}
if mkimg xfs nr 320; then
    mkdir -p /mnt/nr/home/t /mnt/nr/bin; cp $R $U /mnt/nr/bin/; chmod 755 /mnt/nr/bin/*
    chown -R 4321:4322 /mnt/nr/home; chmod 0755 /mnt/nr
    asuser sh -c 'cd /mnt/nr/home/t && echo a >f && chmod 0640 f && ln -s f l && mkfifo p && ln f f2 && mkdir -p d/e && chmod 2750 d &&
      setfattr -n user.x -v y f && truncate -s 16M sp && printf x | dd of=sp bs=4096 seek=100 conv=notrunc status=none &&
      find . -depth -exec touch -h -m -d 2005-05-05T05:05:05.5 {} + -exec touch -h -a -d 2006-06-06T06:06:06.25 {} +' >$W/nr-setup.log 2>&1
    nrset=$?
    sync; mount -o remount,noatime /mnt/nr
    python3 $C/fsmeta.py record /mnt/nr/home/t $W/nr-src.json
    for t in R U; do
        if [ $t = U ]; then
            out=$(asuser sh -c 'cd /mnt/nr/home && b=/mnt/nr/bin/rustic-bko && $b --no-progress --no-cache -r /mnt/nr/home/repo-U init >/dev/null &&
              $b --no-progress --no-cache -r /mnt/nr/home/repo-U backup --set-atime yes /mnt/nr/home/t >/dev/null &&
              $b --no-progress --no-cache -r /mnt/nr/home/repo-U restore --numeric-id latest:/mnt/nr/home/t /mnt/nr/home/out-U' 2>&1); rc=$?
        else
            out=$(asuser sh -c 'cd /mnt/nr/home && b=/mnt/nr/bin/restic-bko && $b -r /mnt/nr/home/repo-R init -q &&
              $b -r /mnt/nr/home/repo-R backup -q --with-atime /mnt/nr/home/t &&
              $b -r /mnt/nr/home/repo-R restore -q latest:/mnt/nr/home/t --target /mnt/nr/home/out-R' 2>&1); rc=$?
        fi
        sync
        python3 $C/fsmeta.py record /mnt/nr/home/out-$t $W/nr-$t.json
        cmpout=$(python3 $C/fsmeta.py compare $W/nr-src.json $W/nr-$t.json 2>&1); crc=$?
        nm=restic-bko; [ $t = U ] && nm=rustic-bko
        if [ $nrset != 0 ]; then fail "nonroot-$t" "harness: uid 4321's tree wasn't made (exit $nrset): $(oneline 300 <$W/nr-setup.log)"
        elif [ $rc = 0 ] && [ $crc = 0 ]; then pass "nonroot-$t" "$nm as uid 4321 (no name; user xattr, setgid dir, hard link, holes, fifo, symlink, times): $cmpout"
        else fail "nonroot-$t" "$nm rc=$rc: $(echo "$out" | grep -v INFO | head -n 3 | oneline); fsmeta: $(echo "$cmpout" | oneline)"; fi
    done
    rmimg nr
else fail "nonroot-R" "no XFS for the non-root run"; fi

# rustic-bko's WARN when /etc/localtime can't be read (Alpine without tzdata).
if $U --no-progress --no-cache -r /nonexistent snapshots 2>&1 | grep -q 'localtime not understood'; then
    fail "no-spurious-warnings" "rustic-bko warns '/etc/localtime not understood' on every command; /etc/localtime -> $(readlink /etc/localtime)"
else pass "no-spurious-warnings" "rustic-bko silent about /etc/localtime (-> $(readlink /etc/localtime))"; fi
limit "selinux-labels" "no SELinux on Chimera (LSMs $(cat /sys/kernel/security/lsm))"
limit "nfsv4-acls" "Linux XFS/ext4 have no NFSv4 ACLs"
limit "birth-times" "XFS/ext4 keep birth times but Linux can't set them (BACKOTTER.md)"
limit "bsd-file-flags" "Linux guest: no BSD file flags"
case " $bootfeat " in
    *" project "*) ;;
    *) limit "project-ids-ext4" "the guest's ext4 (/boot) has no project feature: chattr -p refused; project IDs are checked on XFS" ;;
esac
echo INNER-DONE
exit 0
