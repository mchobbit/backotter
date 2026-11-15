#!/bin/bash
# The FAT fixture for aeon (the ESP, /boot/efi, vfat): under $1 (a fresh
# vfat made like the ESP), up1/up2 (the directories above the backed-up
# path) and up1/up2/tree.  $2: the log directory.  As microos's: the
# guest's own ESP (shim, systemd-boot, MokManager, sdbootutil's BLS entries,
# the kernel and initrd in aeon/KVER/, pcrlock's files) with its
# times and FAT attributes, FAT attributes set and cleared on files and
# directories, read-only files, odd names, FAT's time limits.
set -u
R=$1 L=$2; U1=$R/up1; U2=$U1/up2; T=$U2/tree
mkdir -p "$T"
skip() { echo "fixture: skipped: $*" >&2; }
fat() { python3 -c 'import fcntl,os,struct,sys; fd=os.open(sys.argv[1],os.O_RDONLY); fcntl.ioctl(fd,0x40047211,struct.pack("I",int(sys.argv[2],0))); os.close(fd)' "$1" "$2" || skip "fat $*"; }
cd "$T" || exit 1
# The guest's own ESP, with its times and FAT attributes.
mkdir -p real/esp
cp -Rp /boot/efi/. real/esp/ || skip "cp /boot/efi"
python3 - /boot/efi real/esp <<'PY' || skip "the ESP's FAT attributes"
import fcntl, os, struct, sys
a, b = sys.argv[1], sys.argv[2]
for dp, dns, fns in os.walk(a):
    for n in dns + fns:
        p = os.path.join(dp, n); q = os.path.join(b, os.path.relpath(p, a))
        fd = os.open(p, os.O_RDONLY); v = fcntl.ioctl(fd, 0x80047210, b"\0" * 4); os.close(fd)
        fd = os.open(q, os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, v); os.close(fd)
PY
# cp -p set the directories' times before their files were written.
python3 - /boot/efi real/esp <<'PY' || skip "the ESP's directory times"
import os, sys
a, b = sys.argv[1], sys.argv[2]
for dp, dns, fns in os.walk(a, topdown=False):
    st = os.stat(dp); os.utime(os.path.join(b, os.path.relpath(dp, a)), ns=(st.st_atime_ns, st.st_mtime_ns))
PY
( cd /boot/efi && find . -type f -exec sha256sum {} + | sort -k2 ) >"$L/esp.sha256"
# systemd-boot's boot counting renames an entry as it counts (ID+LEFT-DONE.conf)
# and systemd-bless-boot takes the counter off once a boot of it is good;
# the guest's are blessed by now, so each gets copies under counting names
# beside it, as new snapshots' entries have them (+3), mid-count (+2-1)
# and spent (+0-3).
for e in real/esp/loader/entries/*.conf; do
    [[ -f $e ]] || continue
    b=${e%.conf}
    for c in +3 +2-1 +0-3; do cp -p "$e" "$b$c.conf" || skip "entry $b$c.conf"; done
done
echo "BLS entries: $(ls real/esp/loader/entries 2>/dev/null | tr '\n' ' ')" >&2
echo "guest ESP copied: $(find real/esp | wc -l) paths, $(du -sk real/esp | cut -f1) KiB" >&2
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
: >"$(printf 'utf8-\303\251')" 2>/dev/null || skip "utf8 name on vfat"
head -c 4194304 /dev/urandom >big
d=deep; for i in $(seq 30); do d=$d/level$i; done; mkdir -p $d; echo bottom >$d/bottom
: >empty; mkdir empty-dir
mkdir big-dir; for i in $(seq 300); do : >"big-dir/entry-$i-padding-padding"; done
# FAT's time limits (1980-01-01, 2107-12-31 23:59:58), its 2-second
# mtimes and date-only atimes.
touch -d 1980-01-01T00:00:00 fat-min; touch -d 2107-12-31T23:59:58 fat-max
touch -d 2001-02-03T04:05:07 odd-second
echo atime >atime; touch -m -d 2002-03-04T05:06:08 atime; touch -a -d 2003-04-05T00:00:00 atime
touch -m -d 2002-03-04T05:06:08 readonly-dir hidden-dir; touch -a -d 2003-04-05T00:00:00 readonly-dir hidden-dir
touch -d 2001-02-03T04:05:06 "$T"
cd /
# The directories above: a hidden, archive-cleared up1, times.
fat "$U1" 0x12
touch -d '1999-09-09 09:09:08' "$U2"; touch -d '1998-08-08 08:08:08' "$U1"
exit 0
