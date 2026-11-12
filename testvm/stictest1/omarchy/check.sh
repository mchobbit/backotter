#!/usr/bin/env bash
# SticTest1, omarchy (Omarchy 4.0.6, Arch-based: LUKS2 + btrfs root with
# subvolumes @ @home @log @pkg, compress=zstd:3; FAT32 ESP on /boot with
# Limine; glibc, systemd, UEFI): the targeted fixture test of restic-bko and
# rustic-bko.
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  holds restic-bko and rustic-bko for Linux (roundN/bin/linux)
#   PORT     the guest's SSH port; PREFIX: scratch in /mnt/bko-ram/PREFIX
#   NEED_FILTER  an extended regex: print only the checklist items it matches
#
# Boots a throwaway (--snapshot, overlay in /mnt/bko-ram/PREFIX) copy of
# vms/omarchy with a spare disk, through guest-slot.sh and cmp-run.sh
# --reserve, typing its LUKS passphrase (om-boot.sh, beside this script); in
# the guest (inner.sh below) builds the fixture on fresh btrfs (made and
# mounted like the root: default subvolume @, compress=zstd:3, noatime so
# that listing moves no atimes) and FAT32 (mounted like /boot) file systems
# (loop images on the spare disk), backs it up with each tool, compares the
# two snapshots node by node (rt-common.sh), restores in the four directions
# into fresh file systems, compares each restore with the source (dump.pl/
# cmp.pl: stat, getfattr, getfacl, lsattr, FAT attributes), restores again
# over changed immutable and append-only files, and does one non-root (uid
# 4321) run on btrfs.  Prints "PASS|FAIL|LIMIT need: detail" per checklist
# item, writes results.json and the guest's logs to
# ~/backotter-work/stictest1/omarchy/logs/run-TIME/, exits 0 if nothing FAILs.
set -u
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-.}
WORK=${BKO_WORK:-$HOME/backotter-work} G=omarchy
HERE=$(cd "$(dirname "$0")" && pwd)
[[ -x $BIN/restic-bko && -x $BIN/rustic-bko ]] || { echo "check.sh: no restic-bko/rustic-bko in $BIN" >&2; exit 2; }
if [[ -z ${BKO_CHECK_ADMITTED:-} ]]; then
    source ${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh
    slot_take 3 "$(slot_range_here)"
    BKO_CHECK_ADMITTED=1 HOT_C=90 $WORK/cmp-run.sh --reserve 3 6 "$HERE/$(basename "$0")" "$@"
    rc=$?; slot_give; exit $rc
fi
LOGS=${STC_LOGS:-$WORK/stictest1/$G/logs}/run-$(date +%Y%m%d-%H%M%S)
RUN=/mnt/bko-ram/$PREFIX/check.$$
mkdir -p "$LOGS/res" "$RUN"
. ${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh
. $HERE/om-boot.sh
cleanup() { vm_halt; rm -rf "$RUN"; rmdir /mnt/bko-ram/$PREFIX 2>/dev/null; }
trap cleanup EXIT; trap "exit 143" TERM INT HUP
export TMPDIR=$RUN
cat >$RUN/inner.sh <<'BKO_EOF_inner'
#!/bin/bash
# SticTest1 omarchy, the guest's half (run as root in a throwaway copy, with
# a spare disk /dev/vdb): fixture trees on btrfs (the root's type, made and
# mounted as the root is) and FAT32 (/boot's, the ESP), four directions,
# restore over immutable/append-only, a non-root run.  Prints "R NEED STATUS
# DETAIL" lines; the host turns them into the checklist.  Needs
# /root/bin/{restic-bko,rustic-bko}, /root/{rt-common.sh,dump.pl,cmp.pl}.
set -u
export LC_ALL=C TZ=UTC
RESTIC=/root/bin/restic-bko RUSTIC=/root/bin/rustic-bko
W=/w M=/mnt/bko L=/root/res
mkdir -p $W $M $L
mkfs.ext4 -q -F /dev/vdb && mount /dev/vdb $W || { echo "R harness FAIL no work disk"; exit 1; }
mkdir -p $W/img $W/rt
export RT_TMP=$W/rt
. /root/rt-common.sh
r() { echo "R $*"; }
FSS=${FSS:-btrfs vfat}
# The guest's own mount options (fstab), noatime added.
BTRFS_OPTS=noatime,compress=zstd:3,space_cache=v2
VFAT_OPTS=noatime,fmask=0177,dmask=0077,codepage=437,iocharset=ascii,shortname=mixed,utf8,errors=remount-ro

mkfs_() {  # FS IMG
    case $1 in
        btrfs) mkfs.btrfs -q -f "$2" >/dev/null ;;  # the guest's mkfs defaults
        vfat) mkfs.fat -F 32 "$2" >/dev/null ;;
    esac
}
mkvol() {  # FS NAME [SIZE]: a fresh file system at $M/FS-NAME
    local img=$W/img/$1-$2 dir=$M/$1-$2
    truncate -s ${3:-1G} $img && mkfs_ $1 $img && mkdir -p $dir || return 1
    case $1 in
        # As archinstall makes it: the root in subvolume @, here the default
        # one so that a plain mount gives it.
        btrfs) mount -o loop $img $dir && btrfs -q subvolume create $dir/@ >/dev/null &&
               btrfs -q subvolume set-default $dir/@ && umount $dir &&
               mount -o loop,$BTRFS_OPTS $img $dir ;;
        vfat) mount -o loop,$VFAT_OPTS $img $dir ;;
    esac
}
# FAT attributes (FAT_IOCTL_GET/SET_ATTRIBUTES): fatattr PATH [VALUE]
fatattr() {
    perl -e 'use Fcntl; sysopen(my $f, $ARGV[0], O_RDONLY) or die "$ARGV[0]: $!\n";
        if (@ARGV > 1) { ioctl($f, 0x40047211, pack("L", oct $ARGV[1])) or die "set $ARGV[0]: $!\n"; exit 0 }
        my $b = pack("L", 0); ioctl($f, 0x80047210, $b) or die "get: $!\n"; printf "0x%02x\n", unpack("L", $b)' "$@"
}

# The flags tried, and which the file system kept (it may take a flag and
# not keep it).
KEPT=""
flag() {  # FLAG PATH
    if chattr "+$1" "$2" 2>/dev/null && [[ $(lsattr -d "$2" 2>/dev/null | cut -d' ' -f1) == *"$1"* ]]; then
        KEPT="$KEPT $1"
    else
        chattr "-$1" "$2" 2>/dev/null; echo "fixture: flag $1 not kept on $2" >&2
    fi
}
try() { "$@" 2>/dev/null || echo "fixture: refused: $*" >&2; }

mkfix() {  # FSROOT: the fixture in FSROOT/fx; FSROOT is the ancestor checked
    local A=$1 F=$1/fx
    mkdir -p $F && cd $F || return 1
    # Ancestor directory metadata (stored by both tools).
    chown 1234:5678 $A; chmod 0750 $A
    # Ownership with no names, set-id and sticky modes.
    touch own-noname; chown 1234:5678 own-noname
    install -m 0644 /dev/null mode-setuid; chown 1234:5678 mode-setuid; chmod 4755 mode-setuid
    install -m 0644 /dev/null mode-setgid; chown 0:5678 mode-setgid; chmod 2711 mode-setgid
    mkdir mode-sticky-dir; chmod 1777 mode-sticky-dir
    mkdir mode-setgid-dir; chown 1234:5678 mode-setgid-dir; chmod 2775 mode-setgid-dir; touch mode-setgid-dir/in
    install -m 0000 /dev/null mode-0000
    # Extended attributes.
    touch xattr-user
    setfattr -n user.backotter -v hello xattr-user
    setfattr -n user.binary -v 0x00ff10fe7f xattr-user
    setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" xattr-user
    try setfattr -n user.empty xattr-user
    touch xattr-trusted; setfattr -n trusted.backotter -v root-only xattr-trusted
    setfattr -n user.b -v after xattr-trusted
    touch xattr-security; try setfattr -n security.backotter -v sec xattr-security
    mkdir xattr-dir; setfattr -n user.dir -v d xattr-dir; setfattr -n trusted.dir -v td xattr-dir
    # A value near btrfs's limit (a leaf item: about 16 KiB less headers).
    touch xattr-user-15k; setfattr -n user.big -v "$(head -c 15000 /dev/zero | tr '\0' y)" xattr-user-15k ||
        echo "fixture: 15000-byte value refused" >&2
    # systemd's journal (user.crtime_usec, set binary) as the guest has it.
    touch xattr-user-crtime; setfattr -n user.crtime_usec -v 0x78ad1e0dd8410600 xattr-user-crtime
    # Capabilities: the guest's own newuidmap/newgidmap, and one set here.
    cp -a /usr/bin/newuidmap cap-real-newuidmap; cp -a /usr/bin/newgidmap cap-real-newgidmap
    cp /usr/bin/true cap-set; setcap cap_net_raw,cap_net_admin+ep cap-set
    cp /usr/bin/true cap-set-root; setcap cap_sys_admin+eip cap-set-root
    cp -a /usr/bin/btop cap-real-btop 2>/dev/null || { cp /usr/bin/true cap-real-btop; setcap cap_dac_read_search,cap_perfmon=ep cap-real-btop; }
    cp /usr/bin/true cap-permitted-only; setcap cap_chown=p cap-permitted-only
    # POSIX ACLs, access and default, and a file that inherited a default.
    touch acl-file; setfacl -m u:1234:rwx,g:5678:r,u:0:r acl-file
    mkdir acl-dir; setfacl -m u:1234:rx acl-dir; setfacl -d -m u:1234:rwx,g:5678:rx acl-dir
    touch acl-dir/inherited; mkdir acl-dir/subdir
    mkdir acl-default-only; setfacl -d -m u:4321:rw acl-default-only
    # Hard links, in one and another directory.
    echo linked >hl-a; ln hl-a hl-b; mkdir hl-dir; ln hl-a hl-dir/hl-c
    echo other >hl-x; ln hl-x hl-dir/hl-y
    # Symlinks: relative, dangling, absolute, own times, a trusted xattr.
    ln -s hl-a sym-rel; ln -s /nonexistent/target sym-dangling; ln -s /etc/hostname sym-abs
    ln -s hl-a sym-xattr; try setfattr -h -n trusted.sym -v on-symlink sym-xattr
    # Device nodes (rdev 0 too), FIFOs, a socket (skipped by both tools).
    mknod dev-chr c 1 3; mknod dev-blk b 7 0; mknod dev-chr0 c 0 0; mknod dev-blk0 b 0 0
    mknod dev-big c 4095 1048575 2>/dev/null || mknod dev-big c 259 70000
    mkfifo fifo; mkfifo fifo-owned; chown 1234:5678 fifo-owned; chmod 0600 fifo-owned
    rt_mksock $F/sock || echo "fixture: no socket" >&2
    # Holes and dense files that must stay dense.
    truncate -s 256M sparse-big; echo start | dd of=sparse-big conv=notrunc status=none
    echo middle | dd of=sparse-big bs=1M seek=128 conv=notrunc status=none
    head -c 256K /dev/urandom >sparse-small; truncate -s +64K sparse-small; head -c 256K /dev/urandom >>sparse-small
    head -c 4096 /dev/urandom >sparse-tail; truncate -s 1M sparse-tail
    truncate -s 8M sparse-allhole
    head -c 4M /dev/zero >dense-zeros
    fallocate -l 4M dense-prealloc 2>/dev/null || echo "fixture: fallocate refused" >&2
    # Names, empty and deep directories.
    touch "name with spaces" "name-new"$'\n'"line" name-$'\xff\xfe'-not-utf8 "name-$(printf 'n%.0s' {1..245})"
    mkdir -p "deep$(printf '/l%s' {1..40})"; touch "deep$(printf '/l%s' {1..40})/bottom"
    mkdir empty-dir; touch empty-file
    # Real examples from the guest: pacman's keyring (0700, its gnupg
    # sockets), systemd's enablement symlinks, /etc/ssh, setuid programs,
    # tpm2-tss's keystore (setgid, ACL), the journal directory (nocow C
    # inherited by its files, setgid, access and default ACLs,
    # user.crtime_usec, files with holes), glibc's locale archive (a hole).
    cp -a /etc/pacman.d/gnupg real-gnupg
    cp -a /etc/systemd/system real-systemd
    cp -a /etc/ssh real-ssh
    mkdir real-setid; cp -a /usr/bin/passwd /usr/bin/su /usr/bin/sudo /usr/lib/dbus-daemon-launch-helper /usr/bin/write real-setid/
    cp -a /var/lib/tpm2-tss real-tpm2-tss
    mkdir real-journal; chattr +C real-journal; cp -a /var/log/journal/. real-journal/
    cp -a --sparse=always /usr/lib/locale/locale-archive real-locale-archive
    # Times: far past, far future, odd ns, atime apart (files, dir, symlink).
    touch -d '1970-01-02 00:00:00.123456789' time-old
    touch -d '2100-01-01 12:00:00.987654321' time-future
    echo atime >time-atime; mkdir time-dir; ln -s time-atime time-symlink
    for p in time-atime time-dir time-symlink; do
        touch -h -m -d '2002-03-04 05:06:07.111111111' $p; touch -h -a -d '2003-04-05 06:07:08.222222222' $p
    done
    # btrfs's own: the compression property (btrfs.compression) per file
    # and directory (inherited), a nested subvolume (as var/lib/machines,
    # swap and .snapshots are in @), a swap file (nocow, preallocated).
    if [[ $FSNAME == btrfs ]]; then
        big() { for i in $(seq 200); do echo "line $i of $1, compressible text compressible text"; done; }
        for c in zstd lzo zlib zstd:5 no; do
            touch "compress-$c"; try setfattr -n btrfs.compression -v $c "compress-$c"; big $c >"compress-$c"
        done
        mkdir compress-dir; try setfattr -n btrfs.compression -v lzo compress-dir; big in >compress-dir/inherits
        btrfs -q subvolume create subvol >/dev/null || echo "fixture: refused: subvolume create" >&2
        echo in-subvol >subvol/file; chmod 0750 subvol; chown 1234:5678 subvol
        setfattr -n user.subvol -v sv subvol; touch -d '2005-05-05 05:05:05.5' subvol
        btrfs filesystem mkswapfile -s 64m dense-swapfile >/dev/null 2>&1 || echo "fixture: refused: mkswapfile" >&2
        mkdir flag-nocow-dir; flag C flag-nocow-dir; head -c 100K /dev/urandom >flag-nocow-dir/inherits
        echo m >flag-nocompress; flag m flag-nocompress
    fi
    # Project IDs, where the file system has them.
    echo p >proj-file; chattr -p 42 proj-file 2>/dev/null && echo "fixture: project ids kept" >&2 || echo "fixture: project ids refused" >&2
    # Inode flags, last.
    KEPT=""
    touch flag-nodump; flag d flag-nodump
    echo n >flag-noatime; flag A flag-noatime
    echo s >flag-sync; flag S flag-sync
    mkdir flag-dirsync; flag D flag-dirsync
    echo t >flag-notail; flag t flag-notail
    echo u >flag-undel; flag u flag-undel
    echo s >flag-secrm; flag s flag-secrm
    echo j >flag-journal; flag j flag-journal
    mkdir flag-topdir; flag T flag-topdir
    touch flag-nocow; flag C flag-nocow
    echo c >flag-compress; flag c flag-compress
    mkdir flag-compress-dir; flag c flag-compress-dir; echo inherits >flag-compress-dir/f
    echo append >flag-append; flag a flag-append
    echo fixed >flag-immutable; flag i flag-immutable
    mkdir flag-immutable-dir; echo inside >flag-immutable-dir/f; flag i flag-immutable-dir
    mkdir flag-append-dir; echo inside >flag-append-dir/f; flag a flag-append-dir
    echo "fixture: flags kept:$KEPT" >&2
    echo "$KEPT" >$W/kept-$FSNAME
    touch -d '2001-02-03 04:05:06.5' $F
    touch -d '1999-12-31 23:59:59' $A
    cd /
}

# The fixture for FAT32 (the ESP): no owners, links, devices, xattrs, ACLs
# or flags but FAT's attributes; 2 s mtimes, atimes by the day.
mkfix_vfat() {  # FSROOT
    local A=$1 F=$1/fx
    mkdir -p $F && cd $F || return 1
    echo plain >name-plain; touch empty-file; mkdir empty-dir
    touch "name with spaces" "name-Mixed-Case.TXT" "name-$(printf 'n%.0s' {1..240})" name-$'\xc3\xa9t\xc3\xa9-utf8'
    mkdir -p "deep$(printf '/l%s' {1..30})"; touch "deep$(printf '/l%s' {1..30})/bottom"
    head -c 3M /dev/urandom >data-random; head -c 4M /dev/zero >dense-zeros
    # Real examples: the ESP's Limine files and its configuration.
    cp -r --preserve=timestamps /boot/EFI real-EFI; cp --preserve=timestamps /boot/limine.conf real-limine.conf
    # FAT attributes: read-only (the mode's w bits), hidden, system, archive cleared.
    echo ro >fat-readonly; chmod a-w fat-readonly
    echo h >fat-hidden; fatattr fat-hidden 0x22 || echo "fixture: refused: hidden" >&2
    echo s >fat-system; fatattr fat-system 0x24 || echo "fixture: refused: system" >&2
    echo n >fat-noarchive; fatattr fat-noarchive 0x00 || echo "fixture: refused: archive cleared" >&2
    mkdir fat-hidden-dir; fatattr fat-hidden-dir 0x12 || echo "fixture: refused: hidden dir" >&2
    for f in fat-*; do echo "fixture: $f attributes $(fatattr $f)" >&2; done
    touch -d '1980-01-02 00:00:02' time-old; touch -d '2099-12-31 23:59:58' time-future
    echo a >time-atime; mkdir time-dir
    for p in time-atime time-dir; do touch -m -d '2002-03-04 05:06:08' $p; touch -a -d '2003-04-05 00:00:00' $p; done
    touch -d '2001-02-03 04:05:06' $F
    touch -d '1999-12-31 23:59:58' $A
    echo "" >$W/kept-$FSNAME
    cd /
}

need_of() {  # a dump path: its checklist item
    case $1 in
        .) echo times ;;
        own-*|mode-*) echo ownership_modes ;;
        xattr-user*|xattr-dir*) echo xattrs_user_trusted ;;
        xattr-trusted*|sym-xattr) echo xattrs_user_trusted ;;
        xattr-security*|cap-*) echo xattrs_security_caps ;;
        compress-*) echo btrfs_compression ;;
        subvol*) echo btrfs_subvolume ;;
        fat-*) echo fat_attributes ;;
        data-*) echo contents ;;
        acl-*) echo posix_acls ;;
        hl-*) echo hardlinks ;;
        sym-*) echo symlinks ;;
        dev-*|fifo*) echo devices_fifos ;;
        sock*) echo sockets_skipped ;;
        sparse-*|dense-*) echo sparse ;;
        name*|deep*|empty-*) echo names_dirs ;;
        real-*) echo real_examples ;;
        time-*) echo times ;;
        proj-*) echo project_ids ;;
        flag-*) echo inode_flags ;;
        *) echo other ;;
    esac
}
# Report a cmp.pl diff: one FAIL line per need it touches.
report_diff() {  # CONTEXT DIFFFILE
    local need line
    while IFS= read -r line; do
        p=${line%%:*}; need=$(need_of "${p%%/*}")
        r "$need FAIL $1: $line"
    done <"$2"
}
nosock() { awk -F '\t' '$2 != "s"' "$1"; }
restore_() {  # TOOL REPO TARGET LOG
    if rt_is_rustic $1; then
        RUSTIC_PASSWORD=backotter-compare $1 --no-progress --no-cache -r $2 restore --numeric-id latest $3 >$4 2>&1
    else
        RESTIC_PASSWORD=backotter-compare $1 -r $2 --no-cache restore -q latest --target $3 >$4 2>&1
    fi
}
# The sparse check: marked files come back with no more blocks than the
# source's (plus one per 1 MiB blob of slack), and still sparse; dense ones dense.
sparse_check() {  # CONTEXT SRCDUMP DSTDUMP [over]
    awk -F '\t' -v ctx="$1" -v over="${4:-}" '
        NR == FNR { if ($2 == "f") { sz[$1] = $7; bl[$1] = $17 } next }
        $2 == "f" && ($1 ~ /^(sparse|dense)-/) {
            s = bl[$1]; d = $17; size = sz[$1]
            if ($1 ~ /^sparse-/) {
                if (over == "" && (d > s + 2048 * (int(size / 1048576) + 1) || d * 512 >= size))
                    printf "R sparse FAIL %s: %s blocks %d, source %d, size %d\n", ctx, $1, d, s, size
            } else if (d * 512 < size)
                printf "R sparse FAIL %s: %s dense in the source, restored with %d blocks for %d bytes\n", ctx, $1, d, size
        }' "$2" "$3"
}

for fs in $FSS; do
    SRC=$M/$fs-src
    mkvol $fs src || { r "harness FAIL can't make $fs"; continue; }
    if [[ $fs == vfat ]]; then FSNAME=$fs mkfix_vfat $SRC 2>$L/fixture-$fs.log
    else FSNAME=$fs mkfix $SRC 2>$L/fixture-$fs.log; fi
    sed 's/^/  /' $L/fixture-$fs.log
    perl /root/dump.pl $SRC/fx >$L/$fs-src.dump
    # Backups, one repository per tool.
    for t in $RESTIC $RUSTIC; do
        n=$(basename $t); repo=$W/repo-$fs-$n
        rt_backup $t $repo $SRC/fx >$L/$fs-backup-$n.log 2>&1; rc=$?
        [[ $rc == 0 ]] && r "backup_restore_exit PASS $fs $n backup exit 0" || r "backup_restore_exit FAIL $fs $n backup exit $rc ($(head -c 300 $L/$fs-backup-$n.log | tr '\n' ' '))"
        grep -qi sock $L/$fs-backup-$n.log && r "sockets_skipped FAIL $fs $n backup mentions a socket: $(grep -i sock $L/$fs-backup-$n.log | head -2 | tr '\n' ' ')"
    done
    perl /root/dump.pl $SRC/fx >$L/$fs-src-after.dump
    if perl /root/cmp.pl $L/$fs-src.dump $L/$fs-src-after.dump >$L/$fs-srcchange.diff; then :; else
        r "harness FAIL $fs source changed by the backups: $(head -3 $L/$fs-srcchange.diff | tr '\n' ' ')"; fi
    # Snapshots node-identical (all fields but content, subtree, device_id).
    if rt_nodes $RESTIC $W/repo-$fs-restic-bko $SRC/fx >$L/$fs-nodes-restic && rt_nodes $RESTIC $W/repo-$fs-rustic-bko $SRC/fx >$L/$fs-nodes-rustic; then
        if rt_node_diff $L/$fs-nodes-restic $L/$fs-nodes-rustic >$L/$fs-nodes.diff; then
            r "same_format PASS $fs snapshots node-identical ($(wc -l <$L/$fs-nodes-restic) nodes)"
        else r "same_format FAIL $fs snapshots differ: $(head -5 $L/$fs-nodes.diff | tr '\n' ' ')"; fi
        for k in restic rustic; do
            c=$(grep -c 'type="socket"' $L/$fs-nodes-$k); [[ $c == 0 ]] || r "sockets_skipped FAIL $fs $k snapshot has $c socket nodes"
        done
    else r "same_format FAIL $fs can't decode the snapshots"; fi
    # The ancestor directory (FSROOT): both tools store its lstat, the same
    # node (both forks' BACKOTTER.md, since round 19's rustic-bko).
    for k in restic rustic; do
        rt_nodes $RESTIC $W/repo-$fs-$k-bko $SRC 2>/dev/null | head -1 >$L/$fs-ancestor-$k
    done
    ar=$(cat $L/$fs-ancestor-restic) au=$(cat $L/$fs-ancestor-rustic)
    echo "  ancestor node, restic: $ar"; echo "  ancestor node, rustic: $au"
    if [[ $fs == vfat ]]; then want='mode=2147484096'; else want='mode=2147484136'; fi
    if [[ $ar == *"$want"* && $ar == *mtime=* && ($fs == vfat || $ar == *'uid=1234'*) ]] &&
        rt_node_diff $L/$fs-ancestor-restic $L/$fs-ancestor-rustic >$L/$fs-ancestor.diff; then
        r "ancestor_dirs PASS $fs both tools store the ancestor's lstat, node-identical"
    else r "ancestor_dirs FAIL $fs ancestor nodes differ or lack its lstat: restic [$ar] rustic [$au] $(head -3 $L/$fs-ancestor.diff 2>/dev/null | tr '\n' ' ')"; fi
    # Four directions into fresh file systems.
    for d in rr uu ru ur; do
        case $d in rr) b=restic-bko t=$RESTIC ;; uu) b=rustic-bko t=$RUSTIC ;; ru) b=restic-bko t=$RUSTIC ;; ur) b=rustic-bko t=$RESTIC ;; esac
        ctx="$fs $(basename ${b%-bko})->$(basename $t | sed 's/-bko//')"
        mkvol $fs $d || { r "harness FAIL $ctx no target"; continue; }
        restore_ $t $W/repo-$fs-$b $M/$fs-$d $L/$fs-restore-$d.log; rc=$?
        [[ $rc == 0 ]] && r "backup_restore_exit PASS $ctx restore exit 0" || r "backup_restore_exit FAIL $ctx restore exit $rc: $(grep -v '^\[INFO\]' $L/$fs-restore-$d.log | head -3 | tr '\n' ' ')"
        msgs=$(grep -v -E '^\[INFO\]|^$' $L/$fs-restore-$d.log | head -5 | tr '\n' ' ')
        [[ -n $msgs ]] && echo "  $ctx restore said: $msgs"
        grep -qi sock $L/$fs-restore-$d.log && r "sockets_skipped FAIL $ctx restore mentions a socket"
        T=$M/$fs-$d$SRC/fx
        # The restored ancestor (FSROOT): mode and owner, and mtime but on
        # FAT (whose root directory has no times).
        sa=$(stat -c '%a %u %g %Y' $SRC) ta=$(stat -c '%a %u %g %Y' $M/$fs-$d$SRC 2>&1)
        [[ $fs == vfat ]] && sa=${sa% *} ta=${ta% *}
        [[ $sa == "$ta" ]] && r "ancestor_dirs PASS $ctx restored ancestor $ta" ||
            r "ancestor_dirs FAIL $ctx restored ancestor [$ta] != source [$sa]"
        perl /root/dump.pl $T >$L/$fs-$d.dump
        nosock $L/$fs-src.dump >$L/$fs-src.nosock
        if perl /root/cmp.pl $L/$fs-src.nosock $L/$fs-$d.dump >$L/$fs-$d.diff; then
            r "restore_match PASS $ctx $(wc -l <$L/$fs-$d.dump) entries match the source"
        else report_diff "$ctx" $L/$fs-$d.diff; fi
        sparse_check "$ctx" $L/$fs-src.dump $L/$fs-$d.dump
        c=$(find $M/$fs-$d -type s | wc -l); [[ $c == 0 ]] || r "sockets_skipped FAIL $ctx restore made $c sockets"
    done
    # Restore over existing immutable/append-only files, changed first.
    for d in rr uu; do
        case $d in rr) t=$RESTIC b=restic-bko ;; uu) t=$RUSTIC b=rustic-bko ;; esac
        ctx="$fs over $(basename $t | sed 's/-bko//')"
        T=$M/$fs-$d$SRC/fx
        if [[ $fs == vfat ]]; then
            # FAT has no immutable or append-only: read-only and hidden files, changed.
            chmod u+w $T/fat-readonly; echo changed >$T/fat-readonly; chmod a-w $T/fat-readonly
            echo changed >$T/fat-hidden; fatattr $T/fat-hidden 0x22; echo changed >$T/name-plain; rm -f $T/empty-file
        else
            chattr -i -a $T/flag-immutable $T/flag-append $T/flag-immutable-dir $T/flag-append-dir 2>/dev/null
            echo changed >$T/flag-immutable; echo more >>$T/flag-append; echo changed >$T/flag-immutable-dir/f
            echo more >>$T/flag-append-dir/f; echo changed >$T/hl-a; chmod 600 $T/own-noname; setfattr -x user.backotter $T/xattr-user
            chattr +i $T/flag-immutable $T/flag-immutable-dir; chattr +a $T/flag-append $T/flag-append-dir
        fi
        restore_ $t $W/repo-$fs-$b $M/$fs-$d $L/$fs-over-$d.log; rc=$?
        [[ $rc == 0 ]] && r "restore_over_immutable PASS $ctx exit 0" || r "restore_over_immutable FAIL $ctx exit $rc: $(grep -v '^\[INFO\]' $L/$fs-over-$d.log | head -3 | tr '\n' ' ')"
        perl /root/dump.pl $T >$L/$fs-over-$d.dump
        if perl /root/cmp.pl $L/$fs-src.nosock $L/$fs-over-$d.dump >$L/$fs-over-$d.diff; then
            r "restore_over_immutable PASS $ctx tree matches the source again"
        else while IFS= read -r line; do r "restore_over_immutable FAIL $ctx: $line"; done <$L/$fs-over-$d.diff; fi
    done
    # Birth times: btrfs and FAT keep them, Linux can't set one (BACKOTTER.md).
    bt=$(stat -c %W $SRC/fx/empty-file)
    if [[ $bt == 0 || $bt == - ]]; then r "birth_times LIMIT $fs keeps no birth time (stat %W 0)"
    else r "birth_times LIMIT $fs keeps birth times, but Linux can't set one; unix.birthtime isn't stored on Linux (restic BACKOTTER.md)"; fi
    grep -q 'project ids refused' $L/fixture-$fs.log && r "project_ids LIMIT $fs has no project IDs (chattr -p refused)"
    echo "R flags_kept INFO $fs:$(cat $W/kept-$fs 2>/dev/null)"
done

# One non-root run on btrfs: uid 4321 (no name) backs up its own tree and
# restores it into a directory it owns, four directions.
if [[ " $FSS " == *" btrfs "* ]]; then
    mkvol btrfs nr
    N=$M/btrfs-nr U="setpriv --reuid=4321 --regid=4321 --clear-groups env HOME=/tmp"
    mkdir -p $N/src $W/nr $W/nrbin; chown 4321:4321 $N/src $N $W/nr
    cp $RESTIC $RUSTIC /root/rt-common.sh $W/nrbin/; chmod 755 $W $W/nrbin $W/nrbin/*
    RESTIC=$W/nrbin/restic-bko RUSTIC=$W/nrbin/rustic-bko
    rm -f /tmp/bko-rt-backup.err   # rt_backup's, root's from above
    $U bash -c '
        set -u; cd '$N'/src
        echo a >file; setfattr -n user.mine -v v file; setfacl -m u:1234:r file
        mkdir dir; setfacl -d -m u:1234:rx dir; touch dir/in
        ln file hl; ln -s file sym; mkfifo fifo; chmod 2750 dir; chmod 0640 file
        truncate -s 64M sparse; echo x | dd of=sparse conv=notrunc status=none
        echo d >nodump; chattr +d nodump 2>/dev/null; echo A >noatime; chattr +A noatime
        touch -h -d "2002-03-04 05:06:07.1" sym file; touch -d "1999-01-01" dir/in
        touch -d "2010-01-01" .'
    perl /root/dump.pl $N/src >$L/nr-src.dump
    for t in $RESTIC $RUSTIC; do
        n=$(basename $t)
        $U bash -c "RT_TMP=/tmp; . $W/nrbin/rt-common.sh; rt_backup $t $W/nr/repo-$n $N/src" >$L/nr-backup-$n.log 2>&1; rc=$?
        [[ $rc == 0 ]] && r "nonroot PASS $n backup as uid 4321 exit 0" || r "nonroot FAIL $n backup as uid 4321 exit $rc: $(head -3 $L/nr-backup-$n.log | tr '\n' ' ')"
    done
    for d in rr uu ru ur; do
        case $d in rr) b=restic-bko t=$RESTIC ;; uu) b=rustic-bko t=$RUSTIC ;; ru) b=restic-bko t=$RUSTIC ;; ur) b=rustic-bko t=$RESTIC ;; esac
        ctx="nonroot $(basename ${b%-bko})->$(basename $t | sed 's/-bko//')"
        mkdir -p $N/out-$d; chown 4321:4321 $N/out-$d
        $U bash -c "$(declare -f restore_ rt_is_rustic); restore_ $t $W/nr/repo-$b $N/out-$d /tmp/nr-$d.log"; rc=$?
        cp /tmp/nr-$d.log $L/nr-restore-$d.log
        perl /root/dump.pl $N/out-$d$N/src >$L/nr-$d.dump
        msgs=$(grep -v -E '^\[INFO\]|^$' $L/nr-restore-$d.log | head -4 | tr '\n' ' ')
        if [[ $rc == 0 ]] && perl /root/cmp.pl $L/nr-src.dump $L/nr-$d.dump >$L/nr-$d.diff; then
            r "nonroot PASS $ctx exit 0, tree matches${msgs:+ (said: $msgs)}"
        else r "nonroot FAIL $ctx exit $rc: $(head -4 $L/nr-$d.diff 2>/dev/null | tr '\n' ' ') ${msgs}"; fi
    done
fi
r "done PASS inner finished"
BKO_EOF_inner
cat >$RUN/dump.pl <<'BKO_EOF_dump'
#!/usr/bin/perl
# dump.pl DIR: one line per entry at and below DIR, tab-separated:
# path type mode uid gid nlink size rdev mtime atime target sha linkgrp xattrs acl flags blocks
# Times from GNU find (ns), xattrs from getfattr, ACLs from getfacl, flags
# from lsattr.  Sockets are listed (type s) so a restore that makes one shows.
use strict; use warnings; use Digest::SHA; use Fcntl;
my $root = shift or die "usage: dump.pl DIR\n";
$root =~ s{/+$}{};
sub esc { my $s = shift; $s =~ s/([^\x21-\x7e])/sprintf("\\x%02x", ord $1)/ge; $s }
sub run { my @c = @_; my $pid = open(my $fh, '-|') // die; if (!$pid) { open STDERR, '>', '/dev/null'; exec @c; exit 127 } local $/; my $o = <$fh> // ''; close $fh; $o }
my %times;
{ local $/ = "\0";
  # Keyed by device and inode: a nested btrfs subvolume numbers its own from 256.
  open(my $fh, '-|', 'find', $root, '-printf', '%D:%i %T@ %A@\0') or die;
  while (<$fh>) { chomp; my ($i, $m, $a) = split / /; $times{$i} = [$m, $a] } close $fh; }
my @paths;
{ local $/ = "\0"; open(my $fh, '-|', 'find', $root, '-print0') or die; while (<$fh>) { chomp; push @paths, $_ } close $fh; }
my (%rows, %byino);
for my $p (@paths) {
  my @st = lstat $p or next;
  my ($ino, $mode, $nl, $uid, $gid, $rdev, $size, $blocks) = ("$st[0]:$st[1]", @st[2,3,4,5,6,7,12]);
  my $ft = ($mode & 0170000);
  my $t = $ft == 0100000 ? 'f' : $ft == 0040000 ? 'd' : $ft == 0120000 ? 'l' : $ft == 0010000 ? 'p' :
          $ft == 0140000 ? 's' : $ft == 0020000 ? 'c' : $ft == 0060000 ? 'b' : '?';
  my $rel = $p eq $root ? '.' : substr($p, length($root) + 1);
  my ($mt, $at) = @{$times{$ino} // ['?', '?']};
  my $dev = ($t eq 'c' || $t eq 'b') ? sprintf('%d,%d', ($rdev >> 8) & 0xfff | (($rdev >> 32) & ~0xfff), ($rdev & 0xff) | (($rdev >> 12) & ~0xff)) : '-';
  my $tgt = $t eq 'l' ? esc(readlink $p) : '-';
  my $sha = '-';
  if ($t eq 'f') { my $d = Digest::SHA->new(256); $d->addfile($p, 'b'); $sha = substr($d->hexdigest, 0, 16) }
  my @x;
  if ($t ne 's') {
    my $o = run('getfattr', '-h', '-d', '-m', '-', '-e', 'hex', '--absolute-names', '--', $p);
    for (split /\n/, $o) { next if /^#/ || !/=/; next if /^system\.posix_acl_/; push @x, $_ }
  }
  my $acl = '-';
  if ($t eq 'f' || $t eq 'd') {
    my $o = run('getfacl', '-P', '-n', '-c', '--', $p);
    my @a = grep { length && !/^#/ } split /\n/, $o;
    $acl = join(',', @a) if grep { /^(default:|user:[0-9]|group:[0-9]|mask)/ } @a;
  }
  my $fl = '-';
  if ($t eq 'f' || $t eq 'd') { my $o = run('lsattr', '-d', '--', $p); ($fl) = split / /, $o; $fl //= '?'; $fl =~ s/I/-/g;
    # FAT's attributes (FAT_IOCTL_GET_ATTRIBUTES; ENOTTY elsewhere).
    if (sysopen(my $fh, $p, O_RDONLY | O_NONBLOCK)) { my $b = pack('L', 0);
      $fl .= sprintf('/fat:%02x', unpack('L', $b)) if ioctl($fh, 0x80047210, $b); close $fh } }
  my $sz = ($t eq 'f' || $t eq 'l') ? $size : '-';
  $rows{$rel} = [$t, sprintf('%04o', $mode & 07777), $uid, $gid, ($t eq 'd' ? '-' : $nl), $sz, $dev, $mt,
                 ($t eq 'l' || $t eq 'f' || $t eq 'p' || $t eq 'c' || $t eq 'b' || $t eq 'd') ? $at : '-',
                 $tgt, $sha, '-', join(';', sort @x) || '-', $acl, $fl, ($t eq 'f' ? $blocks : '-')];
  push @{$byino{$ino}}, $rel if $t ne 'd' && $nl > 1;
}
for my $g (values %byino) { my @s = sort @$g; $rows{$_}[11] = esc($s[0]) for @s }
print join("\t", esc($_), @{$rows{$_}}), "\n" for sort keys %rows;
BKO_EOF_dump
cat >$RUN/cmp.pl <<'BKO_EOF_cmp'
#!/usr/bin/perl
# cmp.pl A B: differences between two dump.pl listings, one line per path
# and field ("PATH: FIELD a != b"); blocks are left to the sparse check.
# Exit 1 if anything differs.
use strict; use warnings;
my @f = qw(type mode uid gid nlink size rdev mtime atime target sha linkgrp xattrs acl flags blocks);
my (%a, %b);
for my $x ([$ARGV[0], \%a], [$ARGV[1], \%b]) {
  open(my $fh, '<', $x->[0]) or die "$x->[0]: $!\n";
  while (my $l = <$fh>) { chomp $l; my @r = split /\t/, $l, -1; my $p = shift @r; $x->[1]{$p} = \@r }
}
my $bad = 0;
for my $p (sort keys %{{%a, %b}}) {
  if (!$a{$p}) { print "$p: only in the restore\n"; $bad = 1; next }
  if (!$b{$p}) { print "$p: missing from the restore\n"; $bad = 1; next }
  for my $i (0 .. $#f - 1) {
    next if $a{$p}[$i] eq $b{$p}[$i];
    # Under plain compress= (not compress-force), btrfs marks an inode NOCOMPRESS
    # (m) itself when a range written doesn't compress well, now and then on a
    # restore.  A gained m on a source with neither m nor btrfs.compression is
    # that, not a tool's doing: noted, not counted.
    if ($f[$i] eq 'flags' && $a{$p}[$i] !~ /m/ && $a{$p}[12] !~ /btrfs\.compression/) {
      (my $y = $b{$p}[$i]) =~ s/m/-/;
      if ($y eq $a{$p}[$i]) { print STDERR "$p: btrfs set its own NOCOMPRESS (m) on the restore\n"; next }
    }
    print "$p: $f[$i] " . substr($a{$p}[$i], 0, 160) . " != " . substr($b{$p}[$i], 0, 160) . "\n"; $bad = 1;
  }
}
exit $bad;
BKO_EOF_cmp
truncate -s 16G $RUN/spare.raw
start=$SECONDS
om_boot $PORT $LOGS/boot --snapshot --memory 4096 --cpus 2 --disk $RUN/spare.raw || { echo "FAIL harness: $G didn't boot"; exit 1; }
vm_ssh 'mkdir -p /root/bin'
for f in $BIN/restic-bko $BIN/rustic-bko; do vm_put $f /root/bin/$(basename $f); done
for f in $RUN/inner.sh $RUN/dump.pl $RUN/cmp.pl ${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh; do vm_put $f /root/$(basename $f); done
T=780 vm_ssh "FSS='${FSS:-btrfs vfat}' bash /root/inner.sh" >$LOGS/inner.log 2>&1
echo "inner exit $? after $((SECONDS - start)) s" >>$LOGS/inner.log
T=120 vm_ssh 'cd /root/res && tar cf - .' | tar xf - -C $LOGS/res
vm_halt
cp $BIN/*commit* $LOGS/ 2>/dev/null
# The checklist: every need's R lines, FAIL over LIMIT over PASS.
python3 - "$LOGS" "$FILTER" <<'PY'
import json, re, sys
logs, flt = sys.argv[1], sys.argv[2]
order = ["xattrs_user_trusted", "xattrs_security_caps", "posix_acls", "inode_flags",
         "ownership_modes", "times", "hardlinks", "sparse", "devices_fifos", "symlinks", "names_dirs",
         "real_examples", "btrfs_compression", "btrfs_subvolume", "fat_attributes", "contents", "sockets_skipped", "same_format", "ancestor_dirs", "restore_over_immutable",
         "nonroot", "birth_times", "project_ids", "backup_restore_exit", "restore_match", "harness", "other", "done"]
res = {}
lines = open(logs + "/inner.log", errors="replace").read().splitlines()
for n, l in enumerate(lines, 1):
    m = re.match(r"R (\S+) (PASS|FAIL|LIMIT|INFO) (.*)", l)
    if not m: continue
    need, st, d = m.groups()
    e = res.setdefault(need, {"PASS": [], "FAIL": [], "LIMIT": [], "INFO": []})
    e[st].append("%s (inner.log:%d)" % (d, n))
# Needs judged through the restores: PASS when no restore differed there.
matched = [x for x in res.get("restore_match", {}).get("PASS", [])]
for need in order[:16]:
    e = res.setdefault(need, {"PASS": [], "FAIL": [], "LIMIT": [], "INFO": []})
    if not e["FAIL"] and matched:
        e["PASS"].append("no difference in %d restores (restore_match)" % len(matched))
if "done" not in res:
    res.setdefault("harness", {"PASS": [], "FAIL": [], "LIMIT": [], "INFO": []})["FAIL"].append("inner.sh didn't finish")
out, bad = {}, False
for need in order + sorted(set(res) - set(order)):
    if need not in res or need == "done": continue
    e = res[need]
    st = "FAIL" if e["FAIL"] else "LIMIT" if e["LIMIT"] else "PASS" if e["PASS"] else "INFO"
    d = (e["FAIL"] or e["LIMIT"] or e["PASS"] or e["INFO"])
    out[need] = {"status": st, "details": e}
    bad |= st == "FAIL"
    if re.search(flt, need):
        print("%s %s: %s%s" % (st, need, d[0], " (+%d more)" % (len(d) - 1) if len(d) > 1 else ""))
json.dump({"guest": "omarchy", "logs": logs, "needs": out}, open(logs + "/results.json", "w"), indent=1)
open(logs + "/../last-run", "w").write(logs + "\n")
sys.exit(1 if bad else 0)
PY
