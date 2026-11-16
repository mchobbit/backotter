#!/usr/bin/env bash
# Rename a test guest's host name inside its image, in place: no rebuild, no
# boot.  Made for debian13-arm64 on Bigfoot, installed as deb13-arm64 before
# make-debian.sh named its arm64 guests debarmN: deb13-arm64 is also Debian's
# arm64 kernel flavour (6.12.38+deb13-arm64), so bko-holt's bug-report
# redaction couldn't take the host name out of the guest's dump
# (docs/design/arm64.md, "What A6 built").
#
# Usage: sudo rename-guest-host.sh [--dry-run] [--vms DIR] GUEST OLD NEW
#   e.g. sudo -n systemd-run --scope --quiet --slice=bko.slice -- \
#          testvm/arm64/rename-guest-host.sh --vms /home/m/backotter-work/vms debian13-arm64 deb13-arm64 debarm13
#
# How: vm-update's mechanics, with the guest off.
#   1. An overlay on the image, DIR/GUEST/.rename-host.GUEST.qcow2, which
#      vm-mount mounts read-write: the image itself is only read.
#   2. OLD replaced by NEW where it is a name of its own, never inside a
#      longer one (so 6.12.38+deb13-arm64, the kernel's flavour, stays), in
#      the text files under the guest's /etc and /root: /etc/hostname, /etc/hosts'
#      127.0.1.1 line, the SSH host keys' .pub comments, authorized_keys'
#      comment, /etc/mailname and the like.  /etc/hostname must be OLD.
#      What else holds OLD (the installer's logs in /var/log/installer) is
#      kept; --dry-run lists it.
#   3. vm-umount, then vm-mount --ro of the overlay again (vm-update's host
#      mount check), /etc/hostname read back.
#   4. vm-update's own COMMIT_SCRIPT merges the overlay (qemu-img commit, the
#      fresh-install snapshot retaken so a revert keeps NEW, qcow2-punch.py),
#      and its MARKS_RM_SCRIPT removes the merge marks.  Then
#      DIR/GUEST/id_ed25519.pub's comment gets NEW too, and update.log a line.
# vm-chroot isn't needed: no program of the guest's runs.
#
# Why not vm-update --hook before=FILE: a real update takes the fleet lock on
# Chopin (Bigfoot can't reach it, and only Chopin talks to Bigfoot), runs
# only from ~/backotter-work/testvm, and brings the guest's first OS update
# with it.  So this is for a guest that vm-update has never merged: one
# with an image-state is refused (use vm-update --hook there).
#
#   --dry-run  vm-mount --ro the image itself, print what would change and
#              what would stay, and the steps; change nothing
#   --vms DIR  the guests' folder (default: SUDO_USER's ~/backotter-work/vms)
#
# Refused, before anything changes: not root; a guest with other disks
# (vm-run-options --disk) or a passphrase file; one in use (vm-update's
# BUSY_SCRIPT: a QEMU program has it open, an overlay is on top of it);
# vm-update's lock on the guest held (it is held here, start to end);
# image-state, .vm-update.pending or a merge mark there; /etc/hostname not OLD.
# Exit 0 done, a dry run, or nothing to do (/etc/hostname already NEW);
# 1 refused or failed.  Up to the merge, a failure leaves the image as it was
# (the overlay is removed); if the merge itself fails, its message says what
# to do, and the overlay stays.
set -euo pipefail

ME=$(realpath "$0")
TESTVM=$(cd "$(dirname "$ME")/.." && pwd)
die() { echo "rename-guest-host: $*" >&2; exit 1; }
say() { echo "rename-guest-host: $*"; }

DRY=0 VMS=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) DRY=1; shift ;;
        --vms) [[ $# -ge 2 && -n $2 ]] || die "--vms needs a value"; VMS=$2; shift 2 ;;
        -h|--help) sed -n '2,49p' "$ME" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 3 ]] || die "usage: $0 [--dry-run] [--vms DIR] GUEST OLD NEW"
G=$1 OLD=$2 NEW=$3
[[ $G =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "bad GUEST $G"
for h in "$OLD" "$NEW"; do
    [[ $h =~ ^[a-z][a-z0-9-]{0,62}$ && $h != *- ]] || die "$h isn't a host name: lower-case letters, digits and -"
done
[[ $OLD != "$NEW" ]] || die "OLD and NEW are the same"
[[ $EUID -eq 0 ]] || die "must run as root (vm-mount)"
for t in qemu-img sha256sum flock python3; do command -v "$t" >/dev/null || die "no $t here"; done
if [[ -z $VMS ]]; then
    home=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)
    VMS=$home/backotter-work/vms
fi
D=$(realpath -e -- "$VMS/$G" 2>/dev/null) || die "no guest folder $VMS/$G"
img=$D/$G.qcow2 ovl=$D/.rename-host.$G.qcow2 pub=$D/id_ed25519.pub

# A script of vm-update's (BUSY_SCRIPT, COMMIT_SCRIPT, MARKS_RM_SCRIPT), by its name.
vmu_script() {
    python3 -I -B - "$TESTVM/vm-update" "$1" <<'PY'
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("vm_update", sys.argv[1])
m = importlib.util.module_from_spec(importlib.util.spec_from_loader("vm_update", loader))
loader.exec_module(m)
sys.stdout.write(getattr(m, sys.argv[2]))
PY
}

# names MODE ROOT OLD NEW [FILE...]: OLD as a name (not after a letter, digit,
# + . _ or -, nor before a letter, digit, _ or -) in the text files under
# ROOT's etc/ and root/ (MODE edit: replaced in place, inode, owner and mode
# kept; scan and check: only listed), and with scan (a read-only mount)
# under ROOT's var/, boot/, home/, srv/ and opt/ too, only listed ("keep"):
# on a read-write mount a file opened on the guest's separate /boot would
# make ext4 record this host's mount point as its last_mounted_on, which
# its dump shows (the guest's / takes "/" again as it boots; /boot may
# not).  FILEs (outside ROOT) are as etc's.
# ROOT - : the FILEs only (never /: that would be this host's /etc).
# Prints "change PATH N" and "keep PATH N" lines (N: how many), then each
# changed file's lines.  Symbolic links, files over 4 MiB and binary files
# (a NUL in the first 8 KiB) are passed by.
names() {
    python3 -I -B - "$@" <<'PY'
import os, re, stat, sys
mode, root, old, new, *extra = sys.argv[1:]
if root == "/" or (root != "-" and not os.path.isdir(root)):
    sys.exit(f"names: bad ROOT {root}")
pat = re.compile(rb"(?<![A-Za-z0-9+._-])" + re.escape(old.encode()) + rb"(?![A-Za-z0-9_-])")
def files(top):
    if root == "-" or not os.path.isdir(top) or os.path.islink(top):
        return
    for d, dirs, fs in os.walk(top):
        dirs.sort()
        for f in sorted(fs):
            yield os.path.join(d, f)
def look(p):
    try:
        st = os.lstat(p)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > 4 << 20:
        return None
    with open(p, "rb") as fh:
        data = fh.read()
    if b"\0" in data[:8192]:
        return None
    n = len(pat.findall(data))
    return (data, n) if n else None
rel = lambda p: "/" + os.path.relpath(p, root) if root != "-" and p.startswith(root.rstrip("/") + "/") else p
changed = []
for p in [*(q for t in ("etc", "root") for q in files(os.path.join(root, t))), *extra]:
    got = look(p)
    if got:
        data, n = got
        print(f"change {rel(p)} {n}")
        changed.append((p, data))
for t in ("var", "boot", "home", "srv", "opt") if mode == "scan" else ():
    for p in files(os.path.join(root, t)):
        got = look(p)
        if got:
            print(f"keep {rel(p)} {got[1]}")
for p, data in changed:
    for line in data.splitlines():
        if pat.search(line):
            print(f"  {rel(p)}: {line.decode(errors='replace')}")
    if mode == "edit":
        with open(p, "r+b") as fh:
            fh.write(pat.sub(new.encode(), data))
            fh.truncate()
PY
}

# --- Refusals, before anything changes ---------------------------------------------
[[ -f $img ]] || die "no $img"
mapfile -t others < <(find "$D" -maxdepth 1 -name '*.qcow2' ! -name '.*' ! -name "$G.qcow2" -printf '%f\n')
((${#others[@]} == 0)) || die "$D has other disks besides $G.qcow2 (${others[*]}): one-disk guests only"
grep -qw -- --disk "$D/vm-run-options" 2>/dev/null && die "$G's vm-run-options names other disks (--disk): one-disk guests only"
for f in luks-passphrase zfs-passphrase passphrase; do
    [[ ! -e $D/$f ]] || die "$G has $f: vm-mount would need --key; not handled here"
done
[[ ! -e $D/image-state ]] || die "$G has an image-state: vm-update has merged it, and its records would no longer match; rename it with vm-update --hook before=FILE on a fleet host"
[[ ! -e $D/.vm-update.pending ]] || die "$G has a .vm-update.pending: a merge left undone (vm-update --finish)"
for m in "$D/.vm-update.$G.qcow2.merging" "$D/.vm-update.$G.qcow2.merging.committed"; do
    [[ ! -e $m ]] || die "$m is there: a merge left undone (vm-update --finish)"
done
[[ ! -e $ovl ]] || die "$ovl is there: an earlier run's overlay (if nothing has it open, and $img's SHA-256 is as before, remove it)"
# (vm-update's lock, and later update.log, stay the folder owner's: vm-update runs as that user.)
own() { [[ -e $1 ]] || { : >>"$1"; chown --reference="$D" -- "$1"; }; }
own "$D/.vm-update.lock"
exec 8>>"$D/.vm-update.lock"
flock -n 8 || die "vm-update's lock on $G is held: a vm-update is working on it"
BUSY=$(vmu_script BUSY_SCRIPT) || die "can't read vm-update's BUSY_SCRIPT from $TESTVM/vm-update"
users=$(bash -c "$BUSY" bash "$img" "$D") || die "vm-update's busy check failed"
[[ -z $users ]] || die "$img is in use: $users"
info=$(qemu-img info --output=json "$img" 2>&1) || die "qemu-img can't open $img (in use?): $(head -n 1 <<<"$info")"
grep -q '"backing-filename"' <<<"$info" && die "$img has a backing file: not a guest's image"
nsnap=$(qemu-img snapshot -l "$img" | awk 'NR > 2 && $2 == "fresh-install"' | wc -l)
((nsnap == 1)) || die "$img has $nsnap fresh-install snapshots, not 1"

MNT=$(mktemp -d "${TMPDIR:-/tmp}/rename-host.$G.XXXXXX")
mounted=0 merging=0
cleanup() {
    local rc=$?
    if ((mounted)); then "$TESTVM/vm-umount" "$MNT" || echo "rename-guest-host: vm-umount $MNT failed: undo it by hand" >&2; fi
    rmdir "$MNT" 2>/dev/null || true
    if ((!merging)) && [[ -e $ovl ]]; then rm -f -- "$ovl"; fi
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP
mount_it() {   # mount_it [--ro] IMAGE
    "$TESTVM/vm-mount" "$@" "$MNT" >/dev/null || die "vm-mount $* failed"
    mounted=1
}
umount_it() {
    "$TESTVM/vm-umount" "$MNT" || die "vm-umount $MNT failed"
    mounted=0
}

say "$G: $img, $(stat -c %s "$img") bytes; host name $OLD -> $NEW"
want=$(sha256sum -- "$img" | cut -d' ' -f1)
say "image sha256 $want"

if ((DRY)); then
    mount_it --ro "$img"
    now=$(cat "$MNT/etc/hostname" 2>/dev/null || true)
    say "dry run: /etc/hostname is '${now}'"
    names scan "$MNT" "$OLD" "$NEW" "$pub"
    umount_it
    [[ $now == "$OLD" || $now == "$NEW" ]] || die "/etc/hostname is '$now', not $OLD: refused"
    [[ $now != "$NEW" ]] || { say "dry run: already $NEW: nothing to do"; exit 0; }
    cat <<EOF
rename-guest-host: dry run: the steps, none taken:
   qemu-img create -f qcow2 -b $img -F qcow2 $ovl
   vm-mount $ovl $MNT; the "change" lines above (/etc, /root) get $NEW; /etc/hostname read back; vm-umount
   vm-mount --ro $ovl $MNT; /etc/hostname must be $NEW; vm-umount
   vm-update's COMMIT_SCRIPT $ovl $img fresh-install $want: qemu-img commit, fresh-install retaken, qcow2-punch.py
   vm-update's MARKS_RM_SCRIPT $img; $pub's comment; a line in $D/update.log
EOF
    exit 0
fi

# --- The overlay, its files renamed -------------------------------------------------
qemu-img create -q -f qcow2 -b "$img" -F qcow2 "$ovl"
mount_it "$ovl"
now=$(cat "$MNT/etc/hostname" 2>/dev/null || true)
if [[ $now == "$NEW" ]]; then
    umount_it
    say "/etc/hostname is already $NEW: nothing to do"
    exit 0
fi
[[ $now == "$OLD" ]] || die "/etc/hostname is '$now', not $OLD: refused, nothing changed"
names edit "$MNT" "$OLD" "$NEW"
[[ $(cat "$MNT/etc/hostname") == "$NEW" ]] || die "/etc/hostname isn't $NEW after the edit: nothing merged"
left=$(names check "$MNT" "$OLD" "$NEW" | grep '^change ' || true)
[[ -z $left ]] || die "$OLD is still in: $left; nothing merged"
grep -E "^127\.0\.1\.1[[:space:]]" "$MNT/etc/hosts" | sed 's/^/rename-guest-host: \/etc\/hosts: /' || true
umount_it

# vm-update's host-mount check: this host still mounts it read-only.
mount_it --ro "$ovl"
[[ $(cat "$MNT/etc/hostname") == "$NEW" ]] || die "the overlay, mounted again, doesn't say $NEW: nothing merged"
umount_it

# --- The merge, as vm-update does it ------------------------------------------------
[[ $(sha256sum -- "$img" | cut -d' ' -f1) == "$want" ]] || die "$img changed meanwhile: nothing merged"
COMMIT=$(vmu_script COMMIT_SCRIPT) || die "can't read vm-update's COMMIT_SCRIPT"
MARKS_RM=$(vmu_script MARKS_RM_SCRIPT) || die "can't read vm-update's MARKS_RM_SCRIPT"
osum=$(sha256sum -- "$ovl" | cut -d' ' -f1)
say "merging $(stat -c %s "$ovl") bytes of overlay into $img"
merging=1
trap '' INT TERM HUP   # (the merge runs to its end, as vm-update's does; its children inherit this)
# BKO_WORK: COMMIT_SCRIPT's qcow2-punch.py is this tree's ($TESTVM/qcow2-punch.py).
sum=$(BKO_WORK=$(dirname "$TESTVM") bash -c "$COMMIT" bash "$ovl" "$img" fresh-install "$want" "$osum") ||
    die "the merge failed (its message above); $ovl and the marks beside $img stay: see vm-update --finish's notes before anything else"
bash -c "$MARKS_RM" bash "$img"
merging=0
nsnap=$(qemu-img snapshot -l "$img" | awk 'NR > 2 && $2 == "fresh-install"' | wc -l)
((nsnap == 1)) || die "$img has $nsnap fresh-install snapshots after the merge, not 1"
[[ ! -f $pub ]] || names edit - "$OLD" "$NEW" "$pub" >/dev/null
own "$D/update.log"
echo "$(date '+%F %T') host name $OLD -> $NEW in /etc and /root (testvm/arm64/rename-guest-host.sh, no boot); sha256 $want -> $sum" >>"$D/update.log"
say "done: $img sha256 $sum (was $want); fresh-install retaken"
