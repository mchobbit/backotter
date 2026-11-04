#!/bin/bash
# SticTest1 debian12, the part that runs in the guest as root (/root).
# Prints "PASS|FAIL|LIMIT need: detail" lines; details in /root/st1/.
set -u
cd /root
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
RS=/root/restic-bko RU=/root/rustic-bko
W=/var/tmp/st1 M=/mnt/bkc L=/root/st1
mkdir -p $W $M $L
. /root/rt-common.sh
res() { echo "$1 $2: $3"; }
mkext4() {  # NAME: a fresh ext4 made like the guest's root (mke2fs defaults, 256-byte inodes)
    truncate -s 3G $W/$1.img && mkfs.ext4 -q -F -I 256 $W/$1.img && mkdir -p $M/$1 && mount -o loop $W/$1.img $M/$1
}
rec() { python3 /root/fsmeta.py record "$1" "$2"; }
fcmp() { python3 /root/fsmeta.py compare "$1" "$2" --json "$3" >"$3.txt" 2>&1; }
same() { python3 - "$@" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ok_missing = {"anc/data/root/bko-torture/socket", "lost+found"}
ok_kinds = set(sys.argv[2:])
diffs = d.get("differences", {}) if isinstance(d, dict) else {}
bad, notes = {}, []
for k, v in diffs.items():
    if k in ok_kinds:
        notes.append(f"{k} x{v['count']} (expected)"); continue
    if k == "missing":
        ex = [e for e in v["examples"] if e not in ok_missing]
        cnt = v["count"] - (len(v["examples"]) - len(ex))
        if cnt == 0:
            notes.append("socket and lost+found not restored (expected)"); continue
        v = {"count": cnt, "examples": ex}
    bad[k] = v
if not isinstance(d, dict): bad = d
print(("same" + ("; " + "; ".join(notes) if notes else "")) if not bad else json.dumps(bad)[:600]); sys.exit(1 if bad else 0)
PY
}
echo "root features: $(dumpe2fs -h $(findmnt -no SOURCE /) 2>/dev/null | grep -i 'features:')" >$L/info
echo "nsswitch: $(grep -E '^(passwd|group)' /etc/nsswitch.conf | tr '\n' ' ')" >>$L/info

# --- the fixture ------------------------------------------------------------
mkext4 src || { res FAIL "fixture" "can't make the source ext4"; exit 1; }
mount -o remount,strictatime $M/src
dumpe2fs -h $W/src.img 2>/dev/null | grep -i 'features:' >>$L/info
A=$M/src/anc D=$A/data
mkdir -p $D
bash /root/torture.sh $D 2>$L/torture.err
T=$D/root/bko-torture
# 1 GiB of random bytes isn't needed here
head -c 8M /dev/urandom >$T/big-random
python3 - $T <<'PY'
import os, struct, sys
T = sys.argv[1]
def acl(entries):
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", t, p, i) for t, p, i in entries)
X = 0xffffffff
os.setxattr(f"{T}/acl-file", "system.posix_acl_access",
            acl([(1, 6, X), (2, 7, 1234), (4, 4, X), (8, 4, 5678), (0x10, 7, X), (0x20, 4, X)]))
os.setxattr(f"{T}/acl-dir", "system.posix_acl_access",
            acl([(1, 7, X), (2, 5, 1234), (4, 5, X), (0x10, 5, X), (0x20, 5, X)]))
os.setxattr(f"{T}/acl-dir", "system.posix_acl_default",
            acl([(1, 7, X), (2, 7, 1234), (4, 5, X), (8, 5, 5678), (0x10, 7, X), (0x20, 5, X)]))
f = f"{T}/xattr-file"
for n, v in [("user.backotter", b"hello"), ("user.binary", bytes.fromhex("00ff10fe7f")),
             ("user.large", b"x" * 3000), ("user.empty", b""), ("trusted.backotter", b"root-only"),
             ("security.bko", b"sec")]:
    os.setxattr(f, n, v)
os.setxattr(f"{T}/symlink", "trusted.on-symlink", b"lnk", follow_symlinks=False)
os.setxattr(f"{T}/fifo", "trusted.on-fifo", b"fifo", follow_symlinks=False)
os.setxattr(f"{T}/chardev", "trusted.on-dev", b"dev", follow_symlinks=False)
PY
# The guest's own: ping's capability, the journal's ACLs and user.crtime_usec, random-seed's xattr
mkdir -p $D/guest/usr/bin $D/guest/var/log $D/guest/var/lib/systemd
cp -a /usr/bin/ping $D/guest/usr/bin/
cp -a /var/log/journal $D/guest/var/log/ 2>>$L/torture.err
cp -a /var/lib/systemd/random-seed $D/guest/var/lib/systemd/
cp -a /usr/lib/locale/locale-archive $D/guest/ 2>/dev/null
# Owners with no name (nsswitch "files systemd"), set-id, rdev 0, more flags
mkdir -p $D/noname-dir; chown 4321:8765 $D/noname-dir; touch $D/noname-dir/f; chown 54321:65432 $D/noname-dir/f
mknod $D/rdev0-char c 0 0; mknod $D/rdev0-block b 0 0
echo s >$D/sync-file; chattr +S $D/sync-file
mkdir $D/dirsync-dir; chattr +D $D/dirsync-dir
echo n >$D/noatime-file; chattr +A $D/noatime-file
echo j >$D/journal-data; chattr +j $D/journal-data 2>>$L/torture.err
echo ai >$D/append-nodump; chattr +a +d $D/append-nodump
mkdir $D/immutable-dir; echo in >$D/immutable-dir/inside; chattr +i $D/immutable-dir
mkdir -p $D/empty-deep/a/b/c/d/e/f/g
# The directories above the backed-up path, with metadata of their own
chown 4321:8765 $A; chmod 2751 $A
python3 -c 'import os; os.setxattr("'$A'", "user.ancestor", b"anc"); os.setxattr("'$A'", "trusted.ancestor", b"t")'
touch -h -m -d '2004-05-06 07:08:09.123456789' $A $D
touch -h -a -d '2005-06-07 08:09:10.987654321' $A $D
chmod 0705 $M/src; touch -d '2006-01-01 00:00:00.5' $M/src
sync
mount -o remount,noatime $M/src
rec $M/src $L/src.json
cp $L/src.json $L/src0.json
python3 - $L/src.json <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("fixture:", len(d), "entries;", sum(1 for e in d.values() if e.get("xattrs")), "with xattrs;",
      sum(1 for e in d.values() if e.get("flags")), "with flags")
PY
reset_atimes() { python3 - $L/src0.json $M/src <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1]))
for rel, e in d.items():
    try: os.utime(os.path.join(sys.argv[2], rel), ns=(e["atime"], e["mtime"]), follow_symlinks=False)
    except OSError as ex: print("reset", rel, ex)
PY
}

# --- backups ----------------------------------------------------------------
bk() {  # TOOL REPO (strictatime unless NOSTRICT is set)
    [ -n "${NOSTRICT:-}" ] || mount -o remount,strictatime $M/src
    if [[ $1 == *rustic* ]]; then
        { $1 --no-progress --no-cache -r $2 init && $1 --no-progress --no-cache -r $2 backup --set-atime yes $D; } >$L/$(basename $2).backup 2>&1
    else
        { $1 -r $2 init -q && $1 -r $2 backup -q --with-atime $D; } >$L/$(basename $2).backup 2>&1
    fi
    local s=$?
    mount -o remount,noatime $M/src
    echo $s
}
for t in restic rustic; do
    bin=$RS; [ $t = rustic ] && bin=$RU
    s=$(NOSTRICT=1 bk $bin $W/repo-$t)
    echo "$t backup exit $s; $(grep -ciE 'warn|error' $L/repo-$t.backup) warning lines" >>$L/info
    eval bs_$t=$s
done
# What the directories above the backed-up path were at backup time (the restore targets go under /mnt/bkc later)
w2=$(stat -c '%a %u:%g %Y' /mnt /mnt/bkc | tr '\n' ';')
for t in restic rustic; do
    bin=$RS; [ $t = rustic ] && bin=$RU
    s=$(bk $bin $W/repo-$t-at)
    echo "$t atime backup exit $s" >>$L/info
    rec $M/src $L/src-after-$t.json
    fcmp $L/src0.json $L/src-after-$t.json $L/atime-$t.json
    reset_atimes >>$L/info
done
grep -hiE 'warn|error|signal|panic' $L/repo-*.backup | head -20 >$L/backup-msgs
if [ "$bs_rustic" = 0 ] && ! grep -qiE 'signal|panic|floating' $L/repo-rustic.backup; then
    res PASS "rustic-bko backs up owners with no name (nsswitch files systemd)" "exit 0 with uids 1234, 4321, 54321 unnamed"
else
    res FAIL "rustic-bko backs up owners with no name (nsswitch files systemd)" "exit $bs_rustic: $(tail -3 $L/repo-rustic.backup | tr '\n' ' ')"
fi
[ "$bs_restic" = 0 ] && res PASS "restic-bko runs natively (Debian 12, glibc 2.36)" "backup exit 0" ||
    res FAIL "restic-bko runs natively (Debian 12, glibc 2.36)" "backup exit $bs_restic"
for t in restic rustic; do
    r=$(same $L/atime-$t.json "atime (symlinks)"); [ $? = 0 ] && res PASS "$t-bko backup leaves source atimes alone (strictatime)" "$r" ||
        res FAIL "$t-bko backup leaves source atimes alone (strictatime)" "$r"
done

# --- same repository format: node identity, ancestors included -------------
rt_nodes $RS $W/repo-restic /mnt >$L/nodes.restic 2>$L/nodes.err
rt_nodes $RS $W/repo-rustic /mnt >$L/nodes.rustic 2>>$L/nodes.err
if [ -s $L/nodes.restic ] && rt_node_diff $L/nodes.restic $L/nodes.rustic >$L/nodes.diff 2>&1; then
    res PASS "Same repository format: snapshots node-identical" "$(wc -l <$L/nodes.restic) nodes from /mnt down"
else
    res FAIL "Same repository format: snapshots node-identical" "$(grep -v '(not a check)' $L/nodes.diff | head -8 | tr '\n' ' ')"
fi
grep -E '^  (\.|bkc|bkc/src|bkc/src/anc):' $L/nodes.diff >$L/anc.diff
[ -s $L/anc.diff ] && res FAIL "Ancestor directories stored the same by both tools" "$(head -4 $L/anc.diff | tr '\n' ' ')" ||
    res PASS "Ancestor directories stored the same by both tools" "/mnt, /mnt/bkc, /mnt/bkc/src, anc: no node differences"

# --- restores in four directions --------------------------------------------
restore() {  # TOOL REPO TARGET [restic opts]
    if [[ $1 == *rustic* ]]; then $1 --no-progress --no-cache -r $2 restore --numeric-id latest $3
    else $1 -r $2 restore -q latest --target $3; fi
}
for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${d%-*} r=${d#*-}; bin=$RS; [ $r = rustic ] && bin=$RU
    mkext4 $d || { res FAIL "restore $d" "can't make the target"; continue; }
    restore $bin $W/repo-$b $M/$d >$L/$d.restore 2>&1; s=$?
    sync; mount -o remount,noatime $M/$d
    rec $M/$d$M/src $L/$d.json
    fcmp $L/src0.json $L/$d.json $L/$d.cmp.json
    r2=$(same $L/$d.cmp.json); ok=$?
    msgs=$(grep -viE '^\[INFO\]|^Files:|^Dirs:|restore done|time zone' $L/$d.restore | grep -ciE 'warn|error|fail')
    if [ $s = 0 ] && [ $ok = 0 ]; then res PASS "Round trip $d (fsmeta: xattrs, ACLs, caps, flags, owners, set-id, links, sparse, devices, FIFOs, symlinks, names, times, ancestors)" "identical; $msgs message lines"
    else res FAIL "Round trip $d (fsmeta: xattrs, ACLs, caps, flags, owners, set-id, links, sparse, devices, FIFOs, symlinks, names, times, ancestors)" "exit $s; $r2"; fi
    stat -c '%n %a %u:%g %X %Y' $M/$d/mnt $M/$d/mnt/bkc >$L/$d.upper
done
# The ancestors above the source's mount (/mnt, /mnt/bkc) as each repository restores them
if cmp -s <(cut -d' ' -f2- $L/restic-restic.upper) <(cut -d' ' -f2- $L/rustic-rustic.upper) &&
   cmp -s <(cut -d' ' -f2- $L/restic-restic.upper) <(cut -d' ' -f2- $L/rustic-restic.upper) &&
   cmp -s <(cut -d' ' -f2- $L/restic-restic.upper) <(cut -d' ' -f2- $L/restic-rustic.upper); then
    res PASS "Ancestor directories restored the same from both tools' repositories" "$(cut -d' ' -f2- $L/restic-restic.upper | tr '\n' ';')"
else
    res FAIL "Ancestor directories restored the same from both tools' repositories" "$(paste -d'|' $L/*.upper | tr '\n' ' ')"
fi
want=$(stat -c '%a %u:%g %X %Y' /mnt /mnt/bkc | tr '\n' ';')
have=$(cut -d' ' -f2- $L/restic-restic.upper | tr '\n' ';')
# /mnt and /mnt/bkc atimes move when this script looks them up: mode, owner, mtime
h2=$(awk '{print $2, $3, $5}' $L/restic-restic.upper | tr '\n' ';')
[ "$w2" = "$h2" ] && res PASS "Ancestor directories stored with their real metadata" "mode/owner/mtime of /mnt, /mnt/bkc as at backup time ($w2); src (0705) and anc (2751, 4321:8765, xattrs, times) in the fsmeta round trips" ||
    res FAIL "Ancestor directories stored with their real metadata" "source $w2 restored $h2"

# --- restore again over the restored tree (immutable/append-only files kept) --
for d in restic-restic rustic-rustic; do
    b=${d%-*}; bin=$RS; [ $b = rustic ] && bin=$RU
    t=$M/$d$M/src/anc/data
    mount -o remount,strictatime $M/$d
    echo changed >$t/root/bko-torture/hardlink-a.tmp; mv $t/root/bko-torture/hardlink-a.tmp $t/root/bko-torture/empty
    restore $bin $W/repo-$b $M/$d >$L/$d.over 2>&1; s=$?
    sync; mount -o remount,noatime $M/$d
    rec $M/$d$M/src $L/$d.over.json
    fcmp $L/src0.json $L/$d.over.json $L/$d.over.cmp.json
    r2=$(same $L/$d.over.cmp.json); ok=$?
    if [ $s = 0 ] && [ $ok = 0 ]; then res PASS "Restore over existing immutable/append-only files ($b)" "exit 0, identical"
    else res FAIL "Restore over existing immutable/append-only files ($b)" "exit $s; $r2; $(grep -iE 'error|warn' $L/$d.over | head -3 | tr '\n' ' ')"; fi
done

# --- sockets ------------------------------------------------------------------
sk=0; for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
    [ -e $M/$d$M/src/anc/data/root/bko-torture/socket ] && sk=$((sk+1)); done
nsock=$(grep -c 'type=socket' $L/nodes.restic $L/nodes.rustic | awk -F: '{s+=$2} END {print s+0}')
[ $sk = 0 ] && [ $nsock = 0 ] && res PASS "Sockets skipped by both tools" "no socket node, none restored" ||
    res FAIL "Sockets skipped by both tools" "$sk restored, $nsock nodes"

# --- one non-root run (uid 4321 backs up its own tree and restores it) -------
mkext4 nonroot
N=$M/nonroot; mkdir -p $N/src $N/out; chown 4321:4321 $N $N/src $N/out
install -m 0755 $RS $RU /usr/local/bin/
NRS=/usr/local/bin/restic-bko NRU=/usr/local/bin/rustic-bko
su_() { setpriv --reuid=4321 --regid=4321 --clear-groups env HOME=/tmp RESTIC_PASSWORD=x RUSTIC_PASSWORD=x "$@"; }
su_ sh -c "cd $N/src && mkdir -p d/e && echo a >d/f && chmod 0640 d/f && ln -s f d/l && mkfifo p && head -c 100000 /dev/urandom >big && truncate -s 10M sparse && echo z >>sparse && touch -d '2003-03-03 03:03:03.3' d/f && python3 -c 'import os; os.setxattr(\"d/f\", \"user.nr\", b\"1\")'"
python3 - $N/src/d <<'PY'
import os, struct, sys
X = 0xffffffff
os.setxattr(sys.argv[1], "system.posix_acl_default", struct.pack("<I", 2) + b"".join(
    struct.pack("<HHI", t, p, i) for t, p, i in [(1, 7, X), (4, 5, X), (0x20, 5, X)]))
PY
chown -h 4321:4321 -R $N/src
mount -o remount,noatime $N
rec $N/src $L/nr-src.json
nrok=0 nrd=""
for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${d%-*} r=${d#*-}; bb=$NRS; [ $b = rustic ] && bb=$NRU; rb=$NRS; [ $r = rustic ] && rb=$NRU
    if [ ! -d $N/repo-$b ]; then
        if [ $b = rustic ]; then su_ sh -c "$bb --no-progress --no-cache -r $N/repo-$b init && $bb --no-progress --no-cache -r $N/repo-$b backup --set-atime yes $N/src" >$L/nr-$b.backup 2>&1
        else su_ sh -c "$bb -r $N/repo-$b --no-cache init -q && $bb -r $N/repo-$b --no-cache backup -q --with-atime $N/src" >$L/nr-$b.backup 2>&1; fi
        echo "nonroot backup $b exit $?" >>$L/info
    fi
    mkdir -p $N/out/$d; chown 4321:4321 $N/out/$d
    if [ $r = rustic ]; then su_ $rb --no-progress --no-cache -r $N/repo-$b restore --numeric-id latest:$N/src $N/out/$d >$L/nr-$d.restore 2>&1
    else su_ $rb -r $N/repo-$b --no-cache restore -q latest:$N/src --target $N/out/$d >$L/nr-$d.restore 2>&1; fi
    s=$?
    rec $N/out/$d $L/nr-$d.json; fcmp $L/nr-src.json $L/nr-$d.json $L/nr-$d.cmp.json
    r2=$(same $L/nr-$d.cmp.json); ok=$?
    [ $s = 0 ] && [ $ok = 0 ] || { nrok=1; nrd="$nrd $d: exit $s $r2;"; }
done
[ $nrok = 0 ] && res PASS "Non-root backup and restore (uid 4321, own tree: xattrs, default ACL, FIFO, sparse, times)" "all four directions identical" ||
    res FAIL "Non-root backup and restore (uid 4321, own tree: xattrs, default ACL, FIFO, sparse, times)" "$nrd"

# --- limits -------------------------------------------------------------------
res LIMIT "SELinux labels" "Debian 12 runs AppArmor (no on-disk labels); no SELinux"
res LIMIT "Birth times" "ext4 keeps them, Linux can't set them (BACKOTTER.md)"
res LIMIT "Project IDs, compression, nocow, casefold" "the guest's ext4 has no project/casefold feature; ext4 has no c/C (fs_roundtrip.py ext4 covers project and casefold)"
cat $L/torture.err | sed 's/^/torture: /' >>$L/info

# --- clean up -----------------------------------------------------------------
cd /
for m in $M/*; do mountpoint -q $m && { find $m -xdev \( -type f -o -type d \) -exec chattr -i -a {} + 2>/dev/null; umount $m; }; done
rm -rf $W
tar -C /root -czf /root/st1.tgz st1
exit 0
