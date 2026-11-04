#!/bin/bash
# SticTest1 alpine324, the part that runs in the guest (as root, in /root):
# fixture on XFS (formatted like /) and ext4 (like /boot), four directions,
# restore over immutable/append-only, non-root run, snapshot identity.
# Prints PASS/FAIL/LIMIT lines.  Needs restic-bko, rustic-bko, fsmeta.py,
# rt-common.sh beside it.
set -u
cd /root
R=/root/restic-bko U=/root/rustic-bko
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare
RT_TMP=/root/rt; mkdir -p $RT_TMP; . /root/rt-common.sh
W=/root/w; mkdir -p $W
pass() { echo "PASS $1: $2"; }
fail() { echo "FAIL $1: $2"; }
limit() { echo "LIMIT $1: $2"; }
xfsopts=$(xfs_info / | awk '
  /crc=/ {for(i=1;i<=NF;i++) if($i ~ /^(crc|finobt|rmapbt|reflink|bigtime|inobtcount)=/) m=m","$i}
  /sparse=/ {for(i=1;i<=NF;i++) if($i ~ /^(sparse|nrext64|exchange)=/) n=n","$i}
  END{print "-m " substr(m,2) " -i " substr(n,2)}')
mkfs_xfs() { mkfs.xfs -q -f $xfsopts "$1" 2>/dev/null || mkfs.xfs -q -f "$1"; }
mkfs_ext4() { mkfs.ext4 -q -F -O metadata_csum,orphan_file,64bit "$1" 2>/dev/null || mkfs.ext4 -q -F "$1"; }
mkimg() {  # FS NAME [SIZE] -> mounted at /mnt/NAME
    local img="${W:?}/${2:?}.img"
    rm -f -- "$img"; truncate -s "${3:-640M}" "$img"; mkfs_$1 "$img"
    mkdir -p "/mnt/$2"; mount -o loop "$img" "/mnt/$2"
}
rmimg() { umount "/mnt/${1:?}" 2>/dev/null; rm -f -- "${W:?}/${1:?}.img"; }
echo "== versions: $($R version 2>&1 | head -1) / $($U --version 2>&1 | head -1); alpine $(cat /etc/alpine-release); $(uname -r); xfs opts: $xfsopts"
file $R $U 2>/dev/null; ldd $U 2>&1 | head -2

fixture() {  # DIR FS
    T=$1; fs=$2; mkdir -p "$T"; cd "$T" || return
    touch no-such-user; chown 1234:5678 no-such-user
    install -m 0644 /dev/null setuid; chown 4321:4322 setuid; chmod 4755 setuid
    install -m 0644 /dev/null setgid; chmod 2755 setgid
    mkdir sticky-dir setgid-dir; chmod 1777 sticky-dir; chmod 2775 setgid-dir
    # Real set-id files of the guest, copied with their modes.
    mkdir real-setid
    find / -xdev \( -perm -4000 -o -perm -2000 \) -type f 2>/dev/null | head -5 | while read -r f; do cp -a "$f" real-setid/; done
    touch acl-file; setfacl -m u:1234:rwx,g:5678:r acl-file
    mkdir acl-dir; setfacl -m u:1234:rx acl-dir; setfacl -d -m u:1234:rwx,g:5678:rx acl-dir
    touch acl-dir/inherits
    touch xattr-file
    setfattr -n user.backotter -v hello xattr-file
    setfattr -n user.empty xattr-file
    setfattr -n user.binary -v 0x00ff10fe7f xattr-file
    setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" xattr-file
    setfattr -n trusted.backotter -v root-only xattr-file
    setfattr -n security.backotter -v sec-label xattr-file 2>/dev/null || echo "security.backotter refused"
    # The guest's own xattr: trusted.xfs:autofsck="repair" (on / itself).
    mkdir autofsck-dir; setfattr -n trusted.xfs:autofsck -v repair autofsck-dir
    echo "guest / trusted.xfs:autofsck=$(getfattr --absolute-names -n trusted.xfs:autofsck --only-values / 2>&1)"
    cp /bin/busybox cap-binary; setcap cap_net_raw,cap_net_admin+ep cap-binary
    echo linked >hardlink-a; ln hardlink-a hardlink-b; mkdir sub; ln hardlink-a sub/hardlink-c
    ln -s hardlink-a symlink; ln -s /nonexistent/target symlink-dangling
    ln -s hardlink-a symlink-xattr; setfattr -h -n trusted.onlink -v yes symlink-xattr
    mknod chardev c 1 3; mknod blockdev b 7 0; mknod rdev0 c 0 0; mkfifo fifo
    python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' socket
    truncate -s 64M sparse; echo start | dd of=sparse conv=notrunc status=none
    echo middle | dd of=sparse bs=1M seek=32 conv=notrunc status=none
    truncate -s 8M all-hole
    head -c 256K /dev/urandom >sparse-small; truncate -s +8K sparse-small; head -c 256K /dev/urandom >>sparse-small
    head -c 4M /dev/urandom >random
    fallocate -l 4M preallocated; head -c 2M /dev/zero >zeros
    touch "name with spaces" "new"$'\n'"line" $'\xff\xfe'"-not-utf8" "$(printf 'n%.0s' {1..250})"
    mkdir -p "deep$(printf '/level%s' {1..40})"; touch "deep$(printf '/level%s' {1..40})/bottom"
    mkdir empty-dir; touch empty
    mkdir big-dir; for i in $(seq 600); do : >"big-dir/entry-$i-padding-padding-padding"; done
    echo project >projid-file; chattr -p 42 projid-file 2>/dev/null || echo "chattr -p refused on $fs"
    mkdir projid-dir; chattr +P -p 43 projid-dir 2>/dev/null || echo "chattr +P -p refused on $fs"
    touch -d '1970-01-02 00:00:00.123456789' old; touch -d '2100-01-01 12:00:00.987654321' future
    echo atime >atime; mkdir atime-dir; ln -s atime atime-symlink
    touch nodump; chattr +d nodump; touch noatime; chattr +A noatime; touch sync; chattr +S sync
    mkdir flagdir; chattr +d flagdir 2>/dev/null; chattr +A flagdir 2>/dev/null
    echo append >append-only; chattr +a append-only; echo fixed >immutable; chattr +i immutable
    for p in atime atime-dir atime-symlink symlink-xattr; do
        touch -h -m -d '2002-03-04 05:06:07.111111111' $p; touch -h -a -d '2003-04-05 06:07:08.222222222' $p; done
    echo "flags kept on $fs: $(lsattr -d nodump noatime sync append-only immutable flagdir projid-file 2>&1 | tr '\n' ';')"
    echo "projects on $fs: $(lsattr -p -d projid-file projid-dir 2>&1 | tr '\n' ';')"
    cd /root
}

restore() {  # TOOL REPO DEST
    if [[ $1 == U ]]; then $U --no-progress --no-cache -r "$2" restore --numeric-id latest "$3"
    else $R -r "$2" restore -q latest --target "$3"; fi
}

declare -A REPO
for fs in xfs ext4; do
    S=src-$fs; mkimg $fs $S
    # Ancestors with their own metadata above the backed-up path.
    mkdir -p /mnt/$S/anc1/anc2; chown 1111:2222 /mnt/$S/anc1; chmod 0751 /mnt/$S/anc1
    chmod 0705 /mnt/$S/anc1/anc2; chown 3333:4444 /mnt/$S/anc1/anc2
    setfattr -n user.anc -v up /mnt/$S/anc1
    P=/mnt/$S/anc1/anc2/tree
    fixture $P $fs 2>&1 | sed 's/^/  fixture: /'
    touch -d '2001-02-03 04:05:06' $P /mnt/$S/anc1/anc2; touch -d '1999-09-09 09:09:09' /mnt/$S/anc1
    mount -o remount,noatime /mnt/$S
    python3 /root/fsmeta.py record $P $W/src-$fs.json
    # Sockets are skipped by design (both tools): not expected back.
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d.pop("socket",None); json.dump(d,open(sys.argv[1],"w"))' $W/src-$fs.json
    anc_src=$(stat -c '%a %u %g %Y' /mnt/$S/anc1 /mnt/$S/anc1/anc2 | tr '\n' ' ')
    for t in R U; do
        b=$R; [[ $t == U ]] && b=$U
        REPO[$fs$t]=$W/repo-$fs-$t
        if rt_backup $b ${REPO[$fs$t]} $P >$W/bk-$fs-$t.log 2>&1; then pass "backup-$fs-$t" "$(basename $b) backed up the fixture on $fs, exit 0"
        else fail "backup-$fs-$t" "$(basename $b) backup on $fs failed: $(tail -3 $W/bk-$fs-$t.log | tr '\n' ' ')"; fi
        [[ -s $W/bk-$fs-$t.log ]] && sed 's/^/    backup msg: /' $W/bk-$fs-$t.log | head -10
    done
    # Same repository format: snapshots node-identical.
    rt_nodes $R ${REPO[${fs}R]} $P >$W/nodes-$fs-R.txt; rt_nodes $R ${REPO[${fs}U]} $P >$W/nodes-$fs-U.txt
    if [[ -s $W/nodes-$fs-R.txt ]] && rt_node_diff $W/nodes-$fs-R.txt $W/nodes-$fs-U.txt >$W/nodediff-$fs.txt; then
        pass "snapshots-identical-$fs" "$(wc -l <$W/nodes-$fs-R.txt) nodes, restic and rustic snapshots node-identical; $(grep -c 'not a check' $W/nodediff-$fs.txt) not-a-check lines"
    else fail "snapshots-identical-$fs" "$(head -8 $W/nodediff-$fs.txt | tr '\n' ' ')"; fi
    if grep -q '^socket	' $W/nodes-$fs-R.txt $W/nodes-$fs-U.txt; then fail "sockets-skipped-$fs" "a socket node is in a snapshot"
    else pass "sockets-skipped-$fs" "no socket node in either snapshot"; fi
    for d in RR UU RU UR; do
        b=${d:0:1} r=${d:1:1}
        D=dst-$fs-$d; mkimg $fs $D
        restore $r ${REPO[$fs$b]} /mnt/$D >$W/rs-$fs-$d.log 2>&1; rc=$?
        mount -o remount,noatime /mnt/$D
        python3 /root/fsmeta.py record /mnt/$D$P $W/dst-$fs-$d.json
        cmpout=$(python3 /root/fsmeta.py compare $W/src-$fs.json $W/dst-$fs-$d.json 2>&1); crc=$?
        anc_dst=$(stat -c '%a %u %g %Y' /mnt/$D/mnt/$S/anc1 /mnt/$D/mnt/$S/anc1/anc2 2>&1 | tr '\n' ' ')
        warn=$(grep -v '^\[INFO\]' $W/rs-$fs-$d.log | grep -v 'etc/localtime not understood' | grep -v 'additional entries exist in destination' | grep -iE 'warn|error|fail' | head -3 | tr '\n' ' ')
        if [[ $rc -eq 0 && $crc -eq 0 && -z $warn ]]; then pass "roundtrip-$fs-$d" "$cmpout; restore exit 0, no warnings (rustic's /etc/localtime WARN aside)"
        else fail "roundtrip-$fs-$d" "restore rc=$rc; fsmeta: $(echo "$cmpout" | tr '\n' ' '); msgs: $warn"; fi
        if [[ $anc_src == "$anc_dst" ]]; then pass "ancestors-$fs-$d" "anc1/anc2 mode uid gid mtime: $anc_dst"
        else fail "ancestors-$fs-$d" "source $anc_src, restored $anc_dst"; fi
        if [[ $d == RR || $d == UU ]]; then
            # Restore over the existing tree (immutable/append-only files in it).
            restore $r ${REPO[$fs$b]} /mnt/$D >$W/ov-$fs-$d.log 2>&1; rc=$?
            python3 /root/fsmeta.py record /mnt/$D$P $W/ov-$fs-$d.json
            cmpout=$(python3 /root/fsmeta.py compare $W/src-$fs.json $W/ov-$fs-$d.json 2>&1); crc=$?
            if [[ $rc -eq 0 && $crc -eq 0 ]]; then pass "restore-over-immutable-$fs-$d" "$cmpout; exit 0"
            else fail "restore-over-immutable-$fs-$d" "rc=$rc; $(echo "$cmpout" | tr '\n' ' '); $(grep -v '^\[INFO\]' $W/ov-$fs-$d.log | tail -3 | tr '\n' ' ')"; fi
        fi
        chattr -R -i -a /mnt/$D 2>/dev/null; rmimg $D
    done
    chattr -R -i -a /mnt/$S 2>/dev/null; rmimg $S
done

# One non-root run per tool: back up and restore a user's tree as the user.
adduser -D -u 2001 bkouser 2>/dev/null
mkimg xfs nr 320M; chmod 1777 /mnt/nr
su bkouser -s /bin/sh -c 'mkdir -p /mnt/nr/home/t/d && cd /mnt/nr/home/t && echo a >f && chmod 0640 f && ln -s f l && mkfifo p &&
  setfattr -n user.x -v y f && truncate -s 16M sp && touch -d "2005-05-05 05:05:05.5" f d'
mount -o remount,noatime /mnt/nr
python3 /root/fsmeta.py record /mnt/nr/home/t $W/nr-src.json
# rustic-bko's WARN when /etc/localtime is a dangling link (no tzdata), on every run.
if $U --no-progress --no-cache -r /nonexistent snapshots 2>&1 | grep -q 'localtime not understood'; then
    fail "no-spurious-warnings" "rustic-bko warns '/etc/localtime not understood, times are in UTC' on every command; /etc/localtime -> $(readlink /etc/localtime) (no tzdata); restic-bko is silent"
else pass "no-spurious-warnings" "rustic-bko silent about /etc/localtime"; fi
cp $R $U /mnt/nr/; chmod 755 /mnt/nr/restic-bko /mnt/nr/rustic-bko
for t in R U; do
    b=/mnt/nr/restic-bko; [[ $t == U ]] && b=/mnt/nr/rustic-bko
    out=$(su bkouser -s /bin/sh -c "cd /mnt/nr && export RESTIC_PASSWORD=x RUSTIC_PASSWORD=x HOME=/mnt/nr;
      if [ $t = U ]; then $b --no-progress --no-cache -r /mnt/nr/repo-$t init >/dev/null && $b --no-progress --no-cache -r /mnt/nr/repo-$t backup --set-atime yes /mnt/nr/home/t >/dev/null &&
        $b --no-progress --no-cache -r /mnt/nr/repo-$t restore --numeric-id latest:/mnt/nr/home/t /mnt/nr/out-$t >/dev/null
      else $b -r /mnt/nr/repo-$t init -q && $b -r /mnt/nr/repo-$t backup -q --with-atime /mnt/nr/home/t && $b -r /mnt/nr/repo-$t restore -q latest:/mnt/nr/home/t --target /mnt/nr/out-$t; fi" 2>&1); rc=$?
    python3 /root/fsmeta.py record /mnt/nr/out-$t $W/nr-$t.json
    cmpout=$(python3 /root/fsmeta.py compare $W/nr-src.json $W/nr-$t.json 2>&1); crc=$?
    if [[ $rc -eq 0 && $crc -eq 0 ]]; then pass "nonroot-$t" "$(basename $b) as uid 2001: $cmpout"
    else fail "nonroot-$t" "$(basename $b) rc=$rc: $(echo "$out" | grep -v INFO | head -3 | tr '\n' ' '); fsmeta: $(echo "$cmpout" | tr '\n' ' ')"; fi
done
rmimg nr
limit "selinux-labels" "no SELinux on Alpine (LSMs landlock,lockdown,yama,loadpin,safesetid,integrity)"
limit "nfsv4-acls" "Linux XFS/ext4 have no NFSv4 ACLs"
limit "birth-times" "XFS/ext4 keep birth times but Linux can't set them (BACKOTTER.md)"
limit "bsd-file-flags" "Linux guest: no BSD file flags"
limit "project-ids-ext4" "the guest's ext4 (/boot) has no project feature: chattr -p refused; project IDs are checked on XFS"
exit 0
