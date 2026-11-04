#!/bin/bash
# SticTest1, artix-dinit: the part that runs in the guest, as root, from
# /root/bko (restic-bko, rustic-bko, rt-common.sh beside it).  check.sh
# copies it in and runs it.  The guest has no python3, so fsmeta.py can't
# run there: dump() records the same metadata with the guest's own tools
# (stat, getfattr, lsattr, sha256sum), one "PATH<TAB>KEY<TAB>VALUE" line per
# attribute, and cmpdump() compares two such records.
#
# On a JFS (the guest's root file system) and an ext4 (its /boot), each a
# loop device made with the guest's own mkfs: a fixture tree with the
# guest's real files (dinit.d, newuidmap/newgidmap capabilities, the holey
# locale-archive) and synthetic hard cases, under ancestor directories with
# metadata of their own; backed up with both tools; restored in four
# directions onto fresh file systems; restored again over the result
# (immutable and append-only files in place); the two snapshots compared
# node by node; and a non-root (uid 4321) backup and restore.
#
# Writes /root/bko/fails (NEED<TAB>WHERE PATH KEY: A != B), /root/bko/notes
# (NEED<TAB>detail) and /root/bko/inner.log.
set -u
export LC_ALL=C
B=/root/bko; cd $B || exit 2
R=$B/restic-bko U=$B/rustic-bko
export RT_TMP=/var/tmp
. $B/rt-common.sh
W=/var/tmp/bko; FAILS=$B/fails NOTES=$B/notes
umount -R /mnt/bkosrc-* $W/m/* 2>/dev/null; losetup -D 2>/dev/null
rm -rf $W; mkdir -p $W/m $W/bin; : >$FAILS; : >$NOTES
# Binaries where uid 4321 can run them too (/root is 0700).
cp $R $U $W/bin/ && chmod 755 $W $W/bin $W/bin/* && R=$W/bin/restic-bko U=$W/bin/rustic-bko
PW=backotter-compare
note() { printf '%s\t%s\n' "$1" "$2" >>$NOTES; }
fail() { printf '%s\t%s\n' "$1" "$2" >>$FAILS; }
try() { "$@" 2>/dev/null || echo "fixture: skipped (unsupported here): $*"; }
flag() {  # FLAG PATH: set, and say if the file system doesn't keep it
    if ! chattr "+$1" "$2" 2>/dev/null; then echo "fixture: chattr +$1 refused here"
    elif [[ $(lsattr -d "$2" 2>/dev/null | cut -d' ' -f1) != *"$1"* ]]; then echo "fixture: chattr +$1 not kept here"
    else echo "fixture: chattr +$1 kept"; fi
}

mkfs_loop() {  # NAME FSTYPE -> loop device, freshly made
    truncate -s 768M $W/$1.img
    local dev; dev=$(losetup -f --show $W/$1.img) || return 1
    case $2 in
        jfs) mkfs.jfs -q $dev >/dev/null 2>&1 ;;
        ext4) mkfs.ext4 -q -F $dev >/dev/null 2>&1 ;;
    esac || return 1
    echo $dev
}

emit() {  # ROOT REL LABEL: one line per attribute of ROOT/REL
    local f p=$3
    if [[ $2 == . ]]; then f=$1; else f=$1/${2#./}; fi
    local s typ mode own size blocks nl ino rdev at mt
    s=$(stat -c '%F|%a|%u:%g|%s|%b|%h|%i|%t:%T|%x|%y' -- "$f") || { printf '%s\tstat\tfailed\n' "$p"; return; }
    IFS='|' read -r typ mode own size blocks nl ino rdev at mt <<<"$s"
    P() { printf '%s\t%s\t%s\n' "$p" "$1" "$2"; }
    P type "$typ"; P mode "$mode"; P own "$own"; P mtime "$mt"; P atime "$at"
    case $typ in
        "regular file"|"regular empty file")
            P size "$size"; P sha "$(sha256sum <"$f" | cut -c1-16)"
            if [[ $((blocks * 512)) -lt $size ]]; then P sparse yes; else P sparse no; fi
            P linkino "$ino:$nl" ;;
        "symbolic link") P target "$(readlink -- "$f")" ;;
        "character special file"|"block special file") P rdev "$rdev" ;;
    esac
    case $typ in "regular file"|"regular empty file"|directory)
        # I: ext4's hashed-directory index, e: its extents, the file system's own
        # (/mnt is on the JFS root, its restored copy on an ext4).
        P flags "$(lsattr -d -- "$f" 2>/dev/null | cut -d' ' -f1 | tr Ie --)"
        P projid "$(lsattr -dp -- "$f" 2>/dev/null | awk '{print $1}')" ;;
    esac
    getfattr -h -d -m - -e hex --absolute-names -- "$f" 2>/dev/null | grep -v '^#' | grep . |
        while IFS= read -r l; do P "x:${l%%=*}" "${l#*=}"; done
}
dump() {  # ROOT OUT: ROOT's tree; hard links as groups (the first name)
    (cd "$1" && find . -path ./lost+found -prune -o -printf '%p\0') |
        while IFS= read -r -d '' p; do emit "$1" "$p" "$p"; done |
        awk -F'\t' -v OFS='\t' '{ r[NR] = $0; if ($2 == "linkino") { split($3, q, ":"); if (q[2] > 1 && (!(q[1] in g) || $1 < g[q[1]])) g[q[1]] = $1 } }
            END { for (i = 1; i <= NR; i++) { split(r[i], f, "\t")
                if (f[2] == "linkino") { split(f[3], q, ":"); f[3] = (q[1] in g) ? g[q[1]] : "-"; print f[1], f[2], f[3] }
                else print r[i] } }' | sort >"$2"
}
cmpdump() {  # A B WHERE: differences, as "WHERE<TAB>PATH<TAB>KEY<TAB>A<TAB>B"
    awk -F'\t' -v w="$3" 'NR == FNR { a[$1 "\t" $2] = $3; next }
        { k = $1 "\t" $2; s[k] = 1
          if (!(k in a)) print w "\t" k "\t<absent>\t" $3; else if (a[k] != $3) print w "\t" k "\t" a[k] "\t" $3 }
        END { for (k in a) if (!(k in s)) print w "\t" k "\t" a[k] "\t<absent>" }' "$1" "$2"
}
need_of() {  # the checklist item a difference belongs to
    awk -F'\t' -v OFS='\t' '{
        p = $2; k = $3; n = "other"
        if (p == "." || p == "./anc1" || p == "./anc1/anc2" || p == "^mnt") n = "ancestor-dirs"
        else if (k == "own") n = "ownership"
        else if (k == "mode") n = "modes-setid-sticky"
        else if (k == "mtime" || k == "atime") n = "times-ns"
        else if (k == "sha" || k == "size" || k == "stat") n = "content-and-tree"
        else if (k == "type") n = (p ~ /fifo|dev/) ? "devices-fifos" : "content-and-tree"
        else if (k == "target") n = "symlinks"
        else if (k == "linkino") n = "hard-links"
        else if (k == "rdev") n = "devices-fifos"
        else if (k == "sparse") n = "sparse-jfs-rule"
        else if (k == "flags") n = "inode-flags"
        else if (k == "projid") n = "project-ids"
        else if (k == "x:security.capability") n = "capabilities"
        else if (k ~ /^x:system\.posix_acl/) n = "posix-acls"
        else if (k ~ /^x:/) n = "xattrs"
        print n, $1 " " p " " k ": " $4 " != " $5 }'
}

restore() {  # TOOL REPO SNAP TARGET [as-user prefix...]: exit status
    local t=$1 repo=$2 snap=$3 dst=$4; shift 4
    if rt_is_rustic $t; then
        "$@" env RUSTIC_PASSWORD=$PW $t --no-progress --no-cache -r $repo restore --numeric-id --sparse by-content "$snap" $dst
    else
        "$@" env RESTIC_PASSWORD=$PW $t -r $repo --no-cache restore -q --sparse "$snap" --target $dst
    fi
}
backup() {  # TOOL REPO PATH [as-user prefix...]
    local t=$1 repo=$2 path=$3; shift 3
    if rt_is_rustic $t; then
        "$@" env RUSTIC_PASSWORD=$PW $t --no-progress --no-cache -r $repo init >/dev/null &&
            "$@" env RUSTIC_PASSWORD=$PW $t --no-progress --no-cache -r $repo backup --set-atime yes $path >/dev/null
    else
        "$@" env RESTIC_PASSWORD=$PW $t -r $repo init -q && "$@" env RESTIC_PASSWORD=$PW $t -r $repo --no-cache backup -q --with-atime $path
    fi
}

fixture() {  # FSTYPE MOUNT: the tree, under MOUNT/anc1/anc2/fix
    local fs=$1 M=$2 F=$2/anc1/anc2/fix
    mkdir -p $F
    # Ancestors with metadata of their own.
    chown 54321:54329 $M/anc1; chmod 0751 $M/anc1
    try setfattr -n user.ancestor -v one $M/anc1
    chmod 0710 $M/anc1/anc2; try setfacl -m u:1234:rx $M/anc1/anc2
    try setfacl -d -m u:1234:rwx $M/anc1/anc2
    # The guest's own files: dinit's service tree, its capability binaries,
    # its one holey file, a few of /boot's files.
    cp -a /etc/dinit.d $F/dinit.d
    cp -a /usr/bin/newuidmap /usr/bin/newgidmap $F/
    [[ $(getcap $F/newuidmap) == *cap_setuid* ]] || setcap cap_setuid=ep $F/newuidmap
    [[ $(getcap $F/newgidmap) == *cap_setgid* ]] || setcap cap_setgid=ep $F/newgidmap
    cp -a --sparse=always /usr/lib/locale/locale-archive $F/locale-archive
    mkdir -p $F/boot && cp -a /boot/grub/grub.cfg /boot/grub/grubenv $F/boot/ 2>/dev/null
    # Owners with no name, set-id and sticky modes.
    touch $F/no-such-user; chown 54321:54329 $F/no-such-user
    mkdir $F/no-name-dir; chown 4242:4343 $F/no-name-dir
    install -m 0644 /dev/null $F/setuid; chown 1234:5678 $F/setuid; chmod 4755 $F/setuid
    install -m 0644 /dev/null $F/setgid; chmod 2755 $F/setgid
    mkdir $F/sticky-dir; chmod 1777 $F/sticky-dir
    mkdir $F/setgid-dir; chmod 2775 $F/setgid-dir
    # POSIX ACLs.
    touch $F/acl-file; try setfacl -m u:1234:rwx,g:5678:r $F/acl-file
    mkdir $F/acl-dir; try setfacl -m u:1234:rx $F/acl-dir; try setfacl -d -m u:1234:rwx,g:5678:rx $F/acl-dir
    # Extended attributes: user, trusted, security, and JFS's os2.
    touch $F/xattr-file
    try setfattr -n user.backotter -v hello $F/xattr-file
    try setfattr -n user.binary -v 0x00ff10fe7f $F/xattr-file
    try setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" $F/xattr-file
    try setfattr -n trusted.backotter -v root-only $F/xattr-file
    try setfattr -n security.backotter -v sec $F/xattr-file
    if [[ $fs == jfs ]]; then try setfattr -n os2.backotter -v os2-value $F/xattr-file; fi
    cp /usr/bin/true $F/cap-binary; try setcap cap_net_raw,cap_net_admin+ep $F/cap-binary
    # Hard links, symlinks (with times and an xattr of their own).
    echo linked >$F/hardlink-a; ln $F/hardlink-a $F/hardlink-b; mkdir $F/sub; ln $F/hardlink-a $F/sub/hardlink-c
    ln -s hardlink-a $F/symlink; ln -s /nonexistent/target $F/symlink-dangling
    ln -s hardlink-a $F/symlink-xattr; try setfattr -h -n trusted.link -v on-the-link $F/symlink-xattr
    touch -h -m -d '2002-03-04 05:06:07.111111111' $F/symlink; touch -h -a -d '2003-04-05 06:07:08.222222222' $F/symlink
    # Device nodes (rdev 0 too), a FIFO, a socket (neither tool keeps one).
    mknod $F/chardev c 1 3; mknod $F/blockdev b 7 0; mknod $F/chardev-rdev0 c 0 0; mkfifo $F/fifo
    local s; for s in /etc/pacman.d/gnupg/S.*; do [[ -S $s ]] && cp -a $s $F/socket 2>/dev/null && break; done
    [[ -S $F/socket ]] || rt_mksock $F/socket >/dev/null 2>&1 || echo "fixture: no socket (nothing here makes one)"
    # Sparse files.
    truncate -s 64M $F/sparse; echo start | dd of=$F/sparse conv=notrunc status=none
    echo middle | dd of=$F/sparse bs=1M seek=32 conv=notrunc status=none
    head -c 256K /dev/urandom >$F/sparse-small; truncate -s +64K $F/sparse-small; head -c 256K /dev/urandom >>$F/sparse-small
    truncate -s 16M $F/all-hole
    # Names, empty and deep directories, times.
    touch "$F/name with spaces" "$F/"$'\xff\xfe'"-not-utf8" "$F/$(printf 'n%.0s' {1..250})"
    mkdir -p "$F/deep$(printf '/level%s' {1..40})"; touch "$F/deep$(printf '/level%s' {1..40})/bottom"
    touch $F/empty; mkdir $F/empty-dir
    touch -d '1970-01-02 00:00:00.123456789' $F/old; touch -d '2100-01-01 12:00:00.987654321' $F/future
    echo atime >$F/atime; touch -m -d '2002-03-04 05:06:07.111111111' $F/atime; touch -a -d '2003-04-05 06:07:08.222222222' $F/atime
    # Inode flags.
    touch $F/nodump; flag d $F/nodump
    touch $F/noatime-flag; flag A $F/noatime-flag
    touch $F/sync-flag; flag S $F/sync-flag
    mkdir $F/dirsync-dir; flag D $F/dirsync-dir
    touch $F/nocow; flag C $F/nocow
    touch $F/compress; flag c $F/compress
    echo project >$F/projid-file; try chattr -p 42 $F/projid-file
    mkdir $F/projinherit-dir; flag P $F/projinherit-dir
    echo append >$F/append-only; flag a $F/append-only
    echo fixed >$F/immutable; flag i $F/immutable
    mkdir $F/immutable-dir; echo in >$F/immutable-dir/file; flag i $F/immutable-dir
    touch -d '2001-02-03 04:05:06.5' $F $M/anc1/anc2
    touch -d '2000-01-01 01:01:01.25' $M/anc1
}

run_fs() {  # FSTYPE
    local fs=$1 S=/mnt/bkosrc-$1 dev d t1 t2 st
    dev=$(mkfs_loop src-$fs $fs) || { fail all-directions-exit-0 "$fs: couldn't make the source file system"; return; }
    mount -o noatime $dev $S
    echo "== $fs: fixture"
    fixture $fs $S
    [[ $fs == jfs ]] && touch -d '1999-09-09 09:09:09.9' $S
    dump $S $W/src-$fs.dump
    echo "== $fs: backups"
    backup $R $W/repo-$fs-restic $S/anc1/anc2/fix || fail all-directions-exit-0 "$fs: restic-bko backup exit $?"
    backup $U $W/repo-$fs-rustic $S/anc1/anc2/fix || fail all-directions-exit-0 "$fs: rustic-bko backup exit $?"
    emit / mnt '^mnt' | sort >$W/mnt-$fs.dump
    echo "== $fs: snapshot nodes, restic-bko's vs rustic-bko's (from /mnt down)"
    if rt_nodes $R $W/repo-$fs-restic /mnt >$W/nodes-$fs.1 && rt_nodes $R $W/repo-$fs-rustic /mnt >$W/nodes-$fs.2; then
        if rt_node_diff $W/nodes-$fs.1 $W/nodes-$fs.2 >$W/nodes-$fs.diff; then
            note same-repo-format "$fs: node-identical, $(wc -l <$W/nodes-$fs.1) nodes from /mnt down"
        else
            grep -v '(not a check)' $W/nodes-$fs.diff | while IFS= read -r l; do fail same-repo-format "$fs: $l"; done
        fi
        cat $W/nodes-$fs.diff
    else fail same-repo-format "$fs: can't decode the snapshots"; fi
    for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
        t1=${d%-*} t2=${d#*-}
        local tool=$R; [[ $t2 == rustic ]] && tool=$U
        local T=$W/m/$fs-$d
        mkdir -p $T; dev=$(mkfs_loop dst-$fs-$d $fs) && mount -o noatime $dev $T || { fail all-directions-exit-0 "$fs $d: no target"; continue; }
        echo "== $fs $d: restore"
        restore $tool $W/repo-$fs-$t1 latest $T 2>&1 | tee $W/restore-$fs-$d.msgs; st=${PIPESTATUS[0]}
        [[ $st -eq 0 ]] || fail all-directions-exit-0 "$fs $d: restore exit $st"
        grep -qi socket $W/restore-$fs-$d.msgs && fail sockets-skipped "$fs $d: restore mentions the socket"
        [[ -e $T/mnt/bkosrc-$fs/anc1/anc2/fix/socket ]] && fail sockets-skipped "$fs $d: socket restored"
        dump $T/mnt/bkosrc-$fs $W/dst-$fs-$d.dump
        { cmpdump $W/src-$fs.dump $W/dst-$fs-$d.dump "$fs $d"
          emit $T mnt '^mnt' | sort >$W/mnt-$fs-$d.dump; cmpdump $W/mnt-$fs.dump $W/mnt-$fs-$d.dump "$fs $d"
        } | grep -v $'\t\\./anc1/anc2/fix/socket\t' | need_of >>$FAILS
        if [[ $t1 == $t2 ]]; then
            echo "== $fs $d: restore again, over the restored tree (immutable, append-only files in place)"
            restore $tool $W/repo-$fs-$t1 latest $T 2>&1 | tee $W/restore2-$fs-$d.msgs; st=${PIPESTATUS[0]}
            if [[ $st -ne 0 ]]; then fail restore-over-immutable "$fs $d: restore over exit $st: $(tail -n 2 $W/restore2-$fs-$d.msgs | tr '\n' ' ')"
            else
                dump $T/mnt/bkosrc-$fs $W/dst2-$fs-$d.dump
                cmpdump $W/src-$fs.dump $W/dst2-$fs-$d.dump "$fs $d over" | grep -v $'\t\\./anc1/anc2/fix/socket\t' |
                    need_of | sed 's/^[^\t]*\t/restore-over-immutable\t/' >>$FAILS
                note restore-over-immutable "$fs $d: exit 0"
            fi
        fi
    done
    nonroot $fs $S
}

nonroot() {  # FSTYPE SRCMOUNT: uid 4321 backs up its own tree and restores it
    local fs=$1 S=$2 N=$2/nr dev T=$W/m/$fs-nonroot as tool d st
    as=(setpriv --reuid=4321 --regid=4321 --clear-groups)
    mkdir -p $N $W/nrhome; chown 4321:4321 $N $W/nrhome
    "${as[@]}" env HOME=$W/nrhome bash -c '
        N=$1; cd $N || exit 1
        echo data >file; chmod 0640 file; setfattr -n user.mine -v yes file
        setfacl -m u:1234:r file; mkdir dir; setfacl -d -m u:1234:rx dir; echo x >dir/inner
        ln file link2; ln -s file sym; mkfifo fifo
        truncate -s 8M sparse; echo s | dd of=sparse conv=notrunc status=none
        touch -d "2004-05-06 07:08:09.123456789" file; touch -a -d "2005-06-07 08:09:10.5" file
        touch -d "2006-01-01 00:00:00.75" dir' _ $N 2>&1
    dump $N $W/nr-src-$fs.dump
    mkdir -p $T; dev=$(mkfs_loop dst-$fs-nonroot $fs) && mount -o noatime $dev $T ||
        fail non-root "$fs: no target file system"
    for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
        local t1=${d%-*} t2=${d#*-} b=$R r=$R
        [[ $t1 == rustic ]] && b=$U; [[ $t2 == rustic ]] && r=$U
        mkdir -p $W/nrrepo-$fs-$t1 $T/$d; chown 4321:4321 $W/nrrepo-$fs-$t1 $T/$d
        if [[ ! -f $W/nrrepo-$fs-$t1/r/config ]]; then
            backup $b $W/nrrepo-$fs-$t1/r $N "${as[@]}" env HOME=$W/nrhome >$W/nr-backup-$fs-$t1.msgs 2>&1 ||
                { fail non-root "$fs: $t1 backup as uid 4321 exit $?: $(tail -n 2 $W/nr-backup-$fs-$t1.msgs | tr '\n' ' ')"; continue; }
        fi
        restore $r $W/nrrepo-$fs-$t1/r "latest:$N" $T/$d "${as[@]}" env HOME=$W/nrhome >$W/nr-restore-$fs-$d.msgs 2>&1; st=$?
        cat $W/nr-restore-$fs-$d.msgs
        [[ $st -eq 0 ]] || fail non-root "$fs $d: restore as uid 4321 exit $st: $(tail -n 2 $W/nr-restore-$fs-$d.msgs | tr '\n' ' ')"
        dump $T/$d $W/nr-dst-$fs-$d.dump
        cmpdump $W/nr-src-$fs.dump $W/nr-dst-$fs-$d.dump "$fs $d non-root" | grep -v $'\t\\.\t' |
            need_of | sed 's/^[^\t]*\t/non-root\t/' >>$FAILS
        note non-root "$fs $d: ran"
    done
}

for fs in jfs ext4; do mkdir -p /mnt/bkosrc-$fs; done
for fs in jfs ext4; do run_fs $fs; done
# What the fixture had (so a check that passed had something to check).
for fs in jfs ext4; do
    note fixture "$fs: $(cut -f1 $W/src-$fs.dump | sort -u | wc -l) paths, $(grep -c $'\tx:' $W/src-$fs.dump) xattrs, flags: $(awk -F'\t' '$2 == "flags" { gsub(/-/, "", $3); if ($3 != "" && $3 != "e") print $3 }' $W/src-$fs.dump | sort | uniq -c | tr -s ' \n' ' '), sparse: $(grep -c $'\tsparse\tyes' $W/src-$fs.dump), sockets copied: $([[ -S /mnt/bkosrc-$fs/anc1/anc2/fix/socket ]] && echo yes || echo no)"
done
cp $W/*.dump $B/ 2>/dev/null
for fs in jfs ext4; do chattr -R -i -a /mnt/bkosrc-$fs $W/m/$fs-* 2>/dev/null; done
umount /mnt/bkosrc-* $W/m/* 2>/dev/null; losetup -D
echo "== inner done"
