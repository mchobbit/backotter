#!/bin/sh
# SticTest1 chimera-uefi, the part that runs in the throwaway guest, as root,
# from /root/c4 (restic-bko, rustic-bko, fsmeta.py, rt-common.sh, fatattr.py
# beside it).  Derived from ../chimera/inner.sh (XFS / and ext4 /boot there):
# here / is ext4 on LVM on LUKS2 and /boot is the ESP, vfat.
# POSIX sh and Chimera's userland: FreeBSD's sh and tools (chimerautils:
# stat -f, no stat -c, no date -d, sed -i takes a suffix), util-linux,
# e2fsprogs, dosfstools, and attr-progs, acl-progs, libcap-progs (check.sh
# installs those).  No bash.
#
# The fixture on loop ext4 made like / (tune2fs's features, inode size) and
# a FAT fixture on loop vfat made like the ESP (FAT type, sector and cluster
# size, /boot's mount options), one pass each: restic->restic,
# rustic->rustic, restic->rustic, rustic->restic into fresh file systems,
# each tool restoring once more over its own restore with changed
# immutable/append-only files (ext4) or changed FAT attributes and read-only
# files (vfat), the two tools' snapshots compared node by node, each tool's
# check --read-data of the other's repository; then one non-root run on
# ext4.  Prints PASS/FAIL/LIMIT lines.
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
rootdev=$(findmnt -n -o SOURCE /)
bootdev=$(findmnt -n -o SOURCE /boot)
echo "== guest: $(uname -r); $(. /etc/os-release; echo "$PRETTY_NAME ($ID)"); sh $(readlink -f /bin/sh); musl $(ls /usr/lib/ld-musl* 2>/dev/null)"
echo "== mounts: $(findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS --target /); $(findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS --target /boot)"
echo "== stack: $(lsblk -n -s -o NAME,TYPE,FSTYPE,FSVER,SIZE "$rootdev" 2>&1 | oneline)"
crypt=$(lsblk -n -s -r -o NAME,TYPE "$rootdev" | awk '$2 == "crypt" { print $1; exit }')
cryptdev=$(cryptsetup status "$crypt" 2>/dev/null | awk '$1 == "device:" { print $2 }')
echo "== luks: $crypt: $(cryptsetup status "$crypt" 2>&1 | grep -E 'type|cipher|keysize|device:' | oneline); $cryptdev header $(cryptsetup luksDump "$cryptdev" 2>&1 | grep -E '^Version' | oneline), $(cryptsetup luksDump "$cryptdev" 2>/dev/null | grep -cE '^ +[0-9]+: luks2') keyslot(s)"
echo "== lvm: $(lvs --noheadings -o lv_name,vg_name,lv_size,lv_attr 2>&1 | oneline)"
echo "== boot loader: $(ls /boot/EFI/* 2>&1 | oneline); entries $(ls /boot/loader/entries 2>&1 | oneline)"
echo "== LSMs: $(cat /sys/kernel/security/lsm)"
echo "== tools: $(apk info -e attr-progs acl-progs libcap-progs dosfstools e2fsprogs cryptsetup lvm2 2>&1 | oneline)"
# Without them the fixture lacks what they set, and fsmeta still finds the
# restores equal to it: their absence is a FAIL, not a smaller fixture.
TOOLS="setfattr getfattr setfacl getfacl setcap getcap chattr lsattr mkfs.ext4 tune2fs mkfs.vfat python3"
miss=; for t in $TOOLS; do command -v $t >/dev/null 2>&1 || miss="$miss $t"; done
if [ -z "$miss" ]; then pass "tools-present" "$TOOLS"
else fail "tools-present" "not in the guest:$miss"; fi
rv=$($R version 2>&1 | head -n 1); uv=$($U --version 2>&1 | head -n 1)
echo "== versions: $rv / $uv"
file $R $U 2>&1
if $R version >/dev/null 2>&1 && $U --version >/dev/null 2>&1; then
    pass "tools-run" "restic-bko and rustic-bko run on Chimera (musl, $(uname -r)): $rv; $uv"
else fail "tools-run" "restic-bko version or rustic-bko --version failed: $rv; $uv"; fi

# ext4 features of / (tune2fs), as mkfs.ext4's.
extfeat() { tune2fs -l "$1" | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | tr '\n' ' '; }
rootfeat=$(extfeat "$rootdev")
rootisz=$(tune2fs -l "$rootdev" | sed -n 's/^Inode size: *//p')
rootbsz=$(tune2fs -l "$rootdev" | sed -n 's/^Block size: *//p')
extO=$(echo "$rootfeat" | tr ' ' '\n' | grep . | grep -v -e '^has_journal$' | tr '\n' ',' | sed 's/,$//')
echo "== / ext4: $rootfeat inode size $rootisz, block size $rootbsz; mkfs.ext4 -O $extO"
# The ESP's FAT: its type, sector and cluster size (the boot sector), and
# /boot's mount options (fmask, dmask, codepage, iocharset, shortname...).
fatgeom() {  # DEV/IMG: "FAT32 bps=512 spc=8"
    python3 - "$1" <<'PY'
import struct, sys
b = open(sys.argv[1], "rb").read(512)
bps, spc = struct.unpack_from("<HB", b, 11)
fsz16 = struct.unpack_from("<H", b, 22)[0]
t = "FAT32" if fsz16 == 0 else (b[54:62].decode("ascii", "replace").strip() or "FAT16")
print(f"{t} bps={bps} spc={spc}")
PY
}
esp_opts() { findmnt -n -o OPTIONS "$1" | tr , '\n' | grep -vE '^(rw|ro|relatime|noatime|strictatime|nosuid|nodev|noexec|loop)$' | paste -s -d , -; }
bootgeom=$(fatgeom "$bootdev") EOPTS=$(esp_opts /boot)
fatT=$(echo "$bootgeom" | sed 's/^FAT\([0-9]*\).*/\1/') fatS=$(echo "$bootgeom" | sed 's/.*bps=\([0-9]*\).*/\1/') fatC=$(echo "$bootgeom" | sed 's/.*spc=\([0-9]*\).*/\1/')
echo "== /boot (ESP): $bootgeom; mount options $EOPTS; mkfs.vfat -F $fatT -S $fatS -s $fatC"
mkfs_ext4() { mkfs.ext4 -q -F -b "$rootbsz" -I "$rootisz" -O "$extO" "$1"; }
mkfs_vfat() { mkfs.vfat -F "$fatT" -S "$fatS" -s "$fatC" -n BKOESP "$1"; }
mkimg() {  # FS NAME [SIZE_MIB]: a fresh FS image, mounted at /mnt/NAME
    img=$W/$2.img
    rm -f -- "$img"; truncate -s "${3:-640}M" "$img"
    mkfs_$1 "$img" >$W/mkfs-$2.log 2>&1 || { echo "mkfs.$1 $2: $(cat $W/mkfs-$2.log | oneline)"; return 1; }
    mkdir -p "/mnt/$2" || return 1
    if [ $1 = vfat ]; then mount -o loop,noatime${EOPTS:+,$EOPTS} "$img" "/mnt/$2"
    else mount -o loop "$img" "/mnt/$2"; fi
}
rmimg() { umount "/mnt/$1" 2>/dev/null; rm -f -- "$W/$1.img"; }
# COV_JSON KIND,KIND...: the kinds fsmeta's coverage counts none of.
covcheck() { python3 -c 'import json,sys; c=json.load(open(sys.argv[1]))["coverage"]; print(", ".join(k for k in sys.argv[2].split(",") if not c.get(k)))' "$1" "$2"; }
# A directory's own mode, owner, times (FreeBSD stat), xattrs and ACL.
dirmeta() { for d; do stat -f '%N %Lp %u %g %Fm %Fa' "$d"; getfattr -h -d -m - -e hex "$d" 2>/dev/null | grep -v '^#' | grep . | sort; done | oneline 900; }
# ext4's inode flags of the fixture's flagged paths (lsattr, path last).
EXTF="nodump noatime sync flagdir append-only append-dir immutable immutable-dir immutable-nodump dirsync-dir topdir journal-data"
extflags() { for p in $EXTF; do lsattr -d "$1/$p" 2>&1 | awk -v p=$p '{ print p, $1 }'; done; }
# FAT_IOCTL_SET_ATTRIBUTES.
fat() { python3 -c 'import fcntl,os,struct,sys; fd=os.open(sys.argv[1],os.O_RDONLY); fcntl.ioctl(fd,0x40047211,struct.pack("I",int(sys.argv[2],0))); os.close(fd)' "$1" "$2"; }

fixture() {  # DIR FS: the fixture in DIR, on a file system of type FS (ext4)
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
    # The guest's own files with xattrs, if any (/etc, /var, /usr/bin),
    # copied with them.
    mkdir real-xattr
    getfattr -R -h -m - --absolute-names /etc /var /usr/bin 2>/dev/null | sed -n 's/^# file: //p' | head -n 3 | while read -r f; do
        [ -f "$f" ] || continue
        cp -p "$f" "real-xattr/$(basename "$f")" &&
            python3 -c 'import os,sys
for k in os.listxattr(sys.argv[1], follow_symlinks=False): os.setxattr(sys.argv[2], k, os.getxattr(sys.argv[1], k, follow_symlinks=False), follow_symlinks=False)' "$f" "real-xattr/$(basename "$f")"
        echo "guest file with xattrs: $f: $(getfattr -h -d -m - "$f" 2>/dev/null | grep -v '^#' | grep . | oneline 200)"
    done
    [ -n "$(ls real-xattr)" ] || echo "guest files with xattrs: none in /etc /var /usr/bin"
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
    # File capabilities: the guest's own, if it has any (Chimera's tarball
    # gives ping and clockdiff theirs, but this image was unpacked by GNU tar
    # without --xattrs, bootstrap/chimera.sh, which drops them), one set
    # here, and a raw one.
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
    # Project IDs (ext4 only with its project feature, which / lacks here).
    echo project >projid-file; chattr -p 42 projid-file 2>/dev/null || echo "chattr -p refused on $fs"
    mkdir projid-dir; chattr +P -p 43 projid-dir 2>/dev/null || echo "chattr +P -p refused on $fs"
    : >projid-dir/inherits
    # Times, deepest first (BSD touch: -d YYYY-MM-DDThh:mm:SS.frac), and an
    # old and a far-future one; then the flags that would block them.
    touch -d 1970-01-02T00:00:00.123456789 old; touch -d 2100-01-01T12:00:00.987654321 future
    echo atime >atime; mkdir atime-dir; ln -s atime atime-symlink
    : >nodump; : >noatime; : >sync; mkdir flagdir
    echo append >append-only; echo fixed >immutable; mkdir immutable-dir; echo in >immutable-dir/child
    : >immutable-nodump; mkdir append-dir; echo in >append-dir/child
    # ext4's own: dirsync (D), topdir (T), data journalling (j).
    mkdir dirsync-dir topdir; echo in >dirsync-dir/child; echo journalled >journal-data
    find . -depth ! -name old ! -name future -exec touch -h -m -d $TS_M {} + -exec touch -h -a -d $TS_A {} +
    for p in atime atime-dir atime-symlink symlink-xattr; do
        touch -h -m -d 2004-05-06T07:08:09.333333333 $p; touch -h -a -d 2005-06-07T08:09:10.444444444 $p; done
    chattr +d nodump; chattr +A noatime; chattr +S sync; chattr +d +A flagdir
    chattr +D dirsync-dir || echo "chattr +D refused on $fs"; chattr +T topdir || echo "chattr +T refused on $fs"
    chattr +j journal-data || echo "chattr +j refused on $fs"
    chattr +a append-only append-dir; chattr +i immutable immutable-dir; chattr +i +d immutable-nodump
    echo "flags kept on $fs: $(lsattr -d $EXTF 2>&1 | oneline)"
    echo "projects on $fs: $(lsattr -p -d projid-file projid-dir 2>&1 | oneline)"
    cd /
}

fixture_vfat() {  # DIR: the FAT fixture in DIR, on vfat made like the ESP
    T=$1
    mkdir -p "$T" && cd "$T" || return 1
    # The guest's own ESP (/boot: systemd-boot, its loader entries, the
    # kernel and initramfs), with its times and FAT attributes.
    mkdir -p real/esp
    cp -Rp /boot/. real/esp/ 2>&1 | head -n 3
    python3 - /boot real/esp <<'PY' || echo "the ESP's FAT attributes not copied"
import fcntl, os, struct, sys
a, b = sys.argv[1], sys.argv[2]
for dp, dns, fns in os.walk(a):
    for n in dns + fns:
        p = os.path.join(dp, n); q = os.path.join(b, os.path.relpath(p, a))
        fd = os.open(p, os.O_RDONLY); v = fcntl.ioctl(fd, 0x80047210, b"\0" * 4); os.close(fd)
        fd = os.open(q, os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, v); os.close(fd)
PY
    echo "guest ESP copied: $(find real/esp | wc -l | tr -d ' ') paths, $(du -sk real/esp | cut -f1) KiB"
    # FAT attributes: hidden, system, archive set and cleared, read-only (by
    # chmod and by attribute), on files and directories.
    echo h >hidden; fat hidden 0x22
    echo s >system; fat system 0x24
    echo hs >hidden-system-noarchive; fat hidden-system-noarchive 0x06
    echo n >archive-cleared; fat archive-cleared 0
    echo a >archive-set; fat archive-set 0x20
    echo r >readonly-chmod; chmod a-w readonly-chmod
    echo r >readonly-attr; fat readonly-attr 0x21
    mkdir hidden-dir system-dir readonly-dir; echo in >hidden-dir/f; echo in >readonly-dir/f
    fat hidden-dir 0x12; fat system-dir 0x14; fat readonly-dir 0x11
    # Long, odd and case-mixed names; deep, empty and big directories; sizes.
    : >"name with spaces"; : >MixedCase.Txt; : >UPPER.TXT; : >"a+b,c;d=e[f]"; : >.dotfile
    : >"$(head -c 200 /dev/zero | tr '\0' n)"
    : >"$(printf 'utf8-\303\251')" 2>/dev/null || echo "utf8 name refused on vfat ($EOPTS)"
    mkdir -p EFI/BOOT; head -c 307200 /dev/urandom >EFI/BOOT/BOOTX64.EFI; head -c 20971520 /dev/urandom >big
    d=deep; for i in $(seq 30); do d=$d/level$i; done; mkdir -p $d; echo bottom >$d/bottom
    : >empty; mkdir empty-dir
    mkdir big-dir; for i in $(seq 300); do : >"big-dir/entry-$i-padding-padding"; done
    # FAT's time limits (1980-01-01, 2107-12-31 23:59:58), its 2-second
    # mtimes and date-only atimes.
    touch -d 1980-01-01T00:00:00 fat-min; touch -d 2107-12-31T23:59:58 fat-max
    touch -d 2001-02-03T04:05:07 odd-second
    echo atime >atime; touch -m -d 2002-03-04T05:06:08 atime; touch -a -d 2003-04-05T00:00:00 atime
    touch -m -d 2002-03-04T05:06:08 readonly-dir hidden-dir; touch -a -d 2003-04-05T00:00:00 readonly-dir hidden-dir
    echo "FAT attributes: $(python3 $C/fatattr.py . /dev/stdout | tr -d '\n' | cut -c1-400)"
    cd /
}

restore() {  # TOOL REPO DEST
    if [ $1 = U ]; then $U --no-progress --no-cache -r "$2" restore --numeric-id latest "$3"
    else $R -r "$2" restore latest --target "$3"; fi
}
msgs() {  # LOG: a restore's warnings and errors, the expected ones aside
    grep -v -e '^\[INFO\]' -e 'additional entries exist in destination' "$1" | grep -iE 'warn|error|fail|denied|not permitted|ignor' | head -n 3 | oneline 400
}
fatcmp() {  # SRC.json DST.json [EXTRA]: the FAT attributes that differ, "" if none
    python3 - "$1" "$2" "${3:-}" <<'PY'
import json, sys
a, b = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
h = lambda v: hex(v) if isinstance(v, int) else v
d = [f"{p} {h(a[p])}->{h(b.get(p))}" for p in sorted(a) if b.get(p) != a[p]]
d += [f"{p} not in the source" for p in sorted(set(b) - set(a)) if p != sys.argv[3]]
print(f"{len(d)} differ, e.g. {d[:6]}" if d else "")
PY
}

for fs in ext4 vfat; do
    S=src-$fs
    mkimg $fs $S $([ $fs = vfat ] && echo 400) || { fail "scratch-$fs" "no source $fs file system"; continue; }
    if [ $fs = ext4 ]; then
        a=$rootfeat; b=$(extfeat $(findmnt -n -o SOURCE /mnt/$S)); what="/'s ext4"
        a="$a inode=$rootisz block=$rootbsz"; b="$b inode=$(tune2fs -l $(findmnt -n -o SOURCE /mnt/$S) | sed -n 's/^Inode size: *//p') block=$(tune2fs -l $(findmnt -n -o SOURCE /mnt/$S) | sed -n 's/^Block size: *//p')"
    else
        a="$bootgeom $EOPTS"; b="$(fatgeom $W/$S.img) $(esp_opts /mnt/$S)"; what="the ESP's FAT and /boot's mount options"
    fi
    if [ "$a" = "$b" ]; then pass "scratch-$fs" "the scratch $fs has $what: $b"
    else fail "scratch-$fs" "$what: $a; scratch: $b"; fi
    # The directories above the backed-up path, with metadata of their own
    # (on FAT only times: the mount gives every file its owner and mode).
    mkdir -p /mnt/$S/anc1/anc2
    if [ $fs = ext4 ]; then
        chown 1111:2222 /mnt/$S/anc1; chmod 0751 /mnt/$S/anc1
        chmod 0705 /mnt/$S/anc1/anc2; chown 3333:4444 /mnt/$S/anc1/anc2
        setfattr -n user.anc -v up /mnt/$S/anc1; setfacl -m u:1234:rx /mnt/$S/anc1/anc2
    else fat /mnt/$S/anc1 0x12; fi
    P=/mnt/$S/anc1/anc2/tree
    if [ $fs = ext4 ]; then fixture $P $fs >$W/fixture-$fs.log 2>&1; else fixture_vfat $P >$W/fixture-$fs.log 2>&1; fi
    sed 's/^/  fixture: /' $W/fixture-$fs.log
    touch -d 2001-02-03T04:05:06.5 $P /mnt/$S/anc1/anc2; touch -d 1999-09-09T09:09:08.25 /mnt/$S/anc1
    sync; mount -o remount,noatime /mnt/$S
    python3 $C/fsmeta.py record $P $W/src-$fs.json
    # Sockets are skipped by design (both tools): not expected back.
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d.pop("socket",None); json.dump(d,open(sys.argv[1],"w"))' $W/src-$fs.json
    [ $fs = ext4 ] && extflags $P >$W/src-$fs.flags
    [ $fs = vfat ] && { python3 $C/fatattr.py /mnt/$S/anc1 $W/src-$fs.fat.json; anc_fat=$(python3 -c 'import json,sys; print(hex(json.load(open(sys.argv[1]))["."]))' $W/src-$fs.fat.json); }
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
    if [ $fs != vfat ]; then
        # What the fixture sets, in the source as fsmeta saw it (a tool
        # missing or refused leaves it out, and the restores would still
        # compare equal).
        covwant="acl,user-xattr,trusted-xattr,capability,immutable,append,nodump,hard-linked,symlink,chardev,blockdev,fifo,with holes,setuid/setgid/sticky"
        [ $fs = ext4 ] && covwant="$covwant,noatime"
        [ $fs = zfs ] && covwant="$covwant,project ID"
        if covmiss=$(covcheck $W/cov-$fs.json "$covwant" 2>&1) && [ -z "$covmiss" ]; then
            pass "fixture-coverage-$fs" "the source has each of: $covwant"
        else fail "fixture-coverage-$fs" "not in the source (fsmeta coverage): $covmiss"; fi
    fi
    # Same repository format: snapshots node-identical.
    rt_nodes $R $W/repo-$fs-R $P >$W/nodes-$fs-R.txt 2>$W/nodes-$fs-R.err
    rt_nodes $R $W/repo-$fs-U $P >$W/nodes-$fs-U.txt 2>$W/nodes-$fs-U.err
    if [ -s $W/nodes-$fs-R.txt ] && rt_node_diff $W/nodes-$fs-R.txt $W/nodes-$fs-U.txt >$W/nodediff-$fs.txt 2>&1; then
        pass "snapshots-identical-$fs" "$(n $W/nodes-$fs-R.txt) nodes, restic and rustic snapshots node-identical; $(grep -c 'not a check' $W/nodediff-$fs.txt) not-a-check lines"
    else fail "snapshots-identical-$fs" "$(head -n 8 $W/nodediff-$fs.txt $W/nodes-$fs-R.err | oneline)"; fi
    if [ $fs = ext4 ]; then
        if grep -q '^socket	' $W/nodes-$fs-R.txt $W/nodes-$fs-U.txt; then fail "sockets-skipped-$fs" "a socket node is in a snapshot"
        else pass "sockets-skipped-$fs" "no socket node in either snapshot"; fi
    fi
    # Each tool reads all of the other's repository.
    $R -r $W/repo-$fs-U check --read-data >$W/chk-$fs-RU.log 2>&1; c1=$?
    $U --no-progress --no-cache -r $W/repo-$fs-R check --read-data >$W/chk-$fs-UR.log 2>&1; c2=$?
    if [ $c1 = 0 ] && [ $c2 = 0 ]; then pass "cross-check-$fs" "restic-bko check --read-data of rustic's repository and rustic-bko's of restic's: exit 0"
    else fail "cross-check-$fs" "restic on rustic's exit $c1, rustic on restic's exit $c2: $(tail -n 3 $W/chk-$fs-RU.log $W/chk-$fs-UR.log | oneline)"; fi
    [ $fs = vfat ] && python3 $C/fatattr.py $P $W/src-$fs.fattree.json
    for d in RR UU RU UR; do
        b=${d%?} r=${d#?}
        D=dst-$fs-$d
        mkimg $fs $D $([ $fs = vfat ] && echo 400) || { fail "roundtrip-$fs-$d" "no target file system"; continue; }
        # A fresh ext4's lost+found isn't the snapshot's.
        [ $fs = ext4 ] && rmdir /mnt/$D/lost+found 2>/dev/null
        restore $r $W/repo-$fs-$b /mnt/$D >$W/rs-$fs-$d.log 2>&1; rc=$?
        sync; mount -o remount,noatime /mnt/$D
        python3 $C/fsmeta.py record /mnt/$D$P $W/dst-$fs-$d.json
        cmpout=$(python3 $C/fsmeta.py compare $W/src-$fs.json $W/dst-$fs-$d.json 2>&1); crc=$?
        warn=$(msgs $W/rs-$fs-$d.log)
        if [ $rc = 0 ] && [ $crc = 0 ] && [ -z "$warn" ]; then pass "roundtrip-$fs-$d" "$cmpout; restore exit 0, no warnings"
        else fail "roundtrip-$fs-$d" "restore rc=$rc; fsmeta: $(echo "$cmpout" | oneline); msgs: $warn"; fi
        anc_dst=$(dirmeta /mnt/$D/mnt/$S/anc1 /mnt/$D/mnt/$S/anc1/anc2 | sed "s|/mnt/$D||g")
        if [ $fs = vfat ]; then
            python3 $C/fatattr.py /mnt/$D/mnt/$S/anc1 $W/dst-$fs-$d.ancfat.json
            anc_dfat=$(python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))["."]; print(hex(v) if isinstance(v,int) else v)' $W/dst-$fs-$d.ancfat.json)
            anc_src2="$anc_src fat=$anc_fat" anc_dst="$anc_dst fat=$anc_dfat"
        else anc_src2=$anc_src; fi
        if [ "$anc_src2" = "$anc_dst" ]; then pass "ancestors-$fs-$d" "anc1, anc2 mode owner mtime atime xattrs ACL$([ $fs = vfat ] && echo ' (FAT: the mount'"'"'s owner and mode), anc1'"'"'s FAT attributes'): $anc_dst"
        else fail "ancestors-$fs-$d" "source: $anc_src2; restored: $anc_dst"; fi
        if [ $fs = ext4 ]; then
            extflags /mnt/$D$P >$W/dst-$fs-$d.flags
            if [ -s $W/src-$fs.flags ] && cmp -s $W/src-$fs.flags $W/dst-$fs-$d.flags; then
                pass "ext4-inode-flags-$d" "d A S i a, ext4's D T j, on files and dirs: $(oneline <$W/src-$fs.flags)"
            else fail "ext4-inode-flags-$d" "$(diff $W/src-$fs.flags $W/dst-$fs-$d.flags | grep '^[<>]' | head -n 6 | oneline)"; fi
        else
            python3 $C/fatattr.py /mnt/$D$P $W/dst-$fs-$d.fattree.json
            fd=$(fatcmp $W/src-$fs.fattree.json $W/dst-$fs-$d.fattree.json)
            if [ -z "$fd" ]; then pass "fat-attributes-$d" "readonly, hidden, system, archive set and cleared, on files and dirs, and the guest's ESP's: $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(len(d), "paths, attributes", sorted({hex(v) for v in d.values() if isinstance(v,int)}))' $W/src-$fs.fattree.json)"
            else fail "fat-attributes-$d" "$fd"; fi
        fi
        if [ $d = RR ] || [ $d = UU ]; then
            X=/mnt/$D$P
            if [ $fs = ext4 ]; then
                # Restore over the restore, its immutable and append-only
                # files changed since (and an immutable directory given a new
                # file).
                if chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
                    setfattr -n user.extra -v 1 $X/immutable && echo x >$X/immutable-dir/new && chmod 600 $X/immutable-nodump &&
                    chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump && echo appended >>$X/append-only; then ch=ok
                else ch=failed; fi
                echo "  over-$fs-$d before ($ch): $(lsattr -d $X/immutable $X/append-only $X/immutable-dir $X/immutable-nodump 2>&1 | sed "s|$X/||" | oneline); immutable: $(cat $X/immutable); append-only: $(cat $X/append-only | oneline)"
                xnew=immutable-dir/new need=restore-over-immutable-$fs-$d
                what="changed +i file (contents, a user xattr), +i dir (a new file), +i+d file (mode), +a file (a line more)"
            else
                # Restore over the restore, its FAT attributes and read-only
                # files changed since (and a hidden directory given a new
                # file).
                if fat $X/readonly-attr 0x20 && echo changed-contents >$X/readonly-attr && fat $X/readonly-attr 0x21 &&
                    chmod u+w $X/readonly-chmod && echo changed >$X/readonly-chmod && chmod a-w $X/readonly-chmod &&
                    fat $X/hidden 0x20 && fat $X/archive-cleared 0x20 && fat $X/system-dir 0x10 && fat $X/readonly-dir 0x10 &&
                    echo x >$X/hidden-dir/new; then ch=ok
                else ch=failed; fi
                echo "  over-$fs-$d before ($ch): $(python3 $C/fatattr.py $X /dev/stdout | grep -E '"(readonly-attr|readonly-chmod|hidden|archive-cleared|system-dir|readonly-dir)"' | oneline); readonly-attr: $(cat $X/readonly-attr)"
                xnew=hidden-dir/new need=restore-over-existing-$fs-$d
                what="changed read-only files (by attribute and by mode: contents), hidden cleared, archive set where cleared, system and read-only cleared on dirs, a new file in a hidden dir"
            fi
            restore $r $W/repo-$fs-$b /mnt/$D >$W/ov-$fs-$d.log 2>&1; rc=$?
            sync
            python3 $C/fsmeta.py record /mnt/$D$P $W/ov-$fs-$d.json
            cmpout=$(python3 $C/fsmeta.py compare $W/src-$fs.json $W/ov-$fs-$d.json 2>&1); crc=$?
            # The new file isn't the snapshot's: a restore leaves what it
            # doesn't know (both tools, no --delete).
            cmpout2=$(echo "$cmpout" | grep -v -e "^extra  *1  *e.g. .$xnew.\$")
            fd=
            if [ $fs = vfat ]; then
                python3 $C/fatattr.py $X $W/ov-$fs-$d.fattree.json
                fd=$(fatcmp $W/src-$fs.fattree.json $W/ov-$fs-$d.fattree.json $xnew)
            fi
            if [ $ch != ok ]; then fail "$need" "harness: couldn't change the restored files first"
            elif [ $rc = 0 ] && { [ $crc = 0 ] || [ -z "$cmpout2" ]; } && [ -z "$fd" ]; then
                pass "$need" "$what: back as backed up: $(echo "$cmpout" | oneline)$([ $fs = vfat ] && echo '; FAT attributes as backed up'); exit 0"
            else fail "$need" "rc=$rc; $(echo "$cmpout" | oneline); FAT attributes: ${fd:-as backed up}; $(msgs $W/ov-$fs-$d.log)"; fi
        fi
        [ $fs = ext4 ] && chattr -R -i -a /mnt/$D 2>/dev/null
        rmimg $D
    done
    [ $fs = ext4 ] && chattr -R -i -a /mnt/$S 2>/dev/null
    rmimg $S
    rm -rf $W/repo-$fs-R $W/repo-$fs-U
done

# --- One non-root run per tool: uid 4321 (no name) backs up and restores its
# own tree, on ext4 made like /.
asuser() {  # COMMAND...: as uid 4321, gid 4322, no supplementary groups
    python3 -c 'import os,sys; os.setgroups([]); os.setgid(4322); os.setuid(4321); os.environ["HOME"]="/mnt/nr/home"; os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}
if mkimg ext4 nr 320; then
    mkdir -p /mnt/nr/home/t /mnt/nr/bin; cp $R $U /mnt/nr/bin/; chmod 755 /mnt/nr/bin/*
    chown -R 4321:4322 /mnt/nr/home; chmod 0755 /mnt/nr
    asuser sh -c 'cd /mnt/nr/home/t && echo a >f && chmod 0640 f && ln -s f l && mkfifo p && ln f f2 && mkdir -p d/e && chmod 2750 d &&
      setfattr -n user.x -v y f && truncate -s 16M sp && printf x | dd of=sp bs=4096 seek=100 conv=notrunc status=none &&
      echo n >nd && chattr +d nd &&
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
        elif [ $rc = 0 ] && [ $crc = 0 ]; then pass "nonroot-$t" "$nm as uid 4321 (no name; user xattr, setgid dir, hard link, holes, fifo, symlink, nodump, times) on ext4: $cmpout"
        else fail "nonroot-$t" "$nm rc=$rc: $(echo "$out" | grep -v INFO | head -n 3 | oneline); fsmeta: $(echo "$cmpout" | oneline)"; fi
    done
    rmimg nr
else fail "nonroot-R" "no ext4 for the non-root run"; fi

# rustic-bko's WARN when /etc/localtime can't be read.
if $U --no-progress --no-cache -r /nonexistent snapshots 2>&1 | grep -q 'localtime not understood'; then
    fail "no-spurious-warnings" "rustic-bko warns '/etc/localtime not understood' on every command; /etc/localtime -> $(readlink /etc/localtime)"
else pass "no-spurious-warnings" "rustic-bko silent about /etc/localtime (-> $(readlink /etc/localtime))"; fi
limit "selinux-labels" "no SELinux on Chimera (LSMs $(cat /sys/kernel/security/lsm))"
limit "nfsv4-acls" "Linux ext4 and vfat have no NFSv4 ACLs"
limit "birth-times" "ext4 keeps birth times (FAT its creation time) but Linux can't set them (BACKOTTER.md)"
limit "bsd-file-flags" "Linux guest: no BSD file flags"
case " $rootfeat " in
    *" project "*) ;;
    *) limit "project-ids-ext4" "the guest's ext4 (/) has no project feature: chattr -p refused; its only other file system is the ESP (vfat), which has none: not applicable" ;;
esac
echo INNER-DONE
exit 0
