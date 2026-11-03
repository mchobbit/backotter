#!/usr/bin/env bash
# SticTest1: the targeted metadata check for artix-s6 (Artix, s6 +
# s6-rc + s6-linux-init, ext4 root and /boot, glibc, nsswitch "files" only).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR      restic-bko and rustic-bko for Linux (roundN/bin/linux)
#   PORT         the throwaway guest's SSH port
#   PREFIX       name prefix: scratch in /mnt/bko-ram/PREFIX/artix-s6
#   NEED_FILTER  an ERE: print only the checklist items it matches
#
# 1. On this host (whose nsswitch.conf may name a module source such as
#    systemd): both tools back up and restore, as root, a tree owned by
#    uids/gids that have no name (rustic-bko's static glibc died there with
#    SIGFPE in round 13).
# 2. In a throwaway overlay of the guest (vm-run --snapshot), inner.sh: the
#    fixture on an ext4 made with the guest's own mkfs.ext4, four
#    directions, restore over the result, snapshot identity, non-root.
# Prints "PASS need: detail", "FAIL need: detail" or "LIMIT need: reason"
# per checklist item, writes results.json beside its logs, exits 0 if
# nothing FAILs.  Logs: ~/backotter-work/stictest1/artix-s6/logs/STAMP/.
set -uo pipefail
[[ $# -ge 3 ]] || { sed -n '2,20p' "$0" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-.}
S=/mnt/bko-ram/$PREFIX/artix-s6
LOGS=${CHECK_LOGS:-$here/logs/$(date +%Y%m%d-%H%M%S)}
[[ -x $BIN/restic-bko && -x $BIN/rustic-bko ]] || { echo "no restic-bko/rustic-bko in $BIN" >&2; exit 2; }

if [[ ${1:-} != --guest && -z ${CHECK_INNER:-} ]]; then
    mkdir -p "$LOGS" "$S"
    # Run the whole check inside a guest slot and a reservation.
    # shellcheck source=/dev/null
    source "$WORK/testvm/compare/guest-slot.sh"
    slot_take 3 2900-2999
    cd "$WORK" && HOT_C=90 CHECK_INNER=1 CHECK_LOGS=$LOGS ./cmp-run.sh --reserve 3 6 \
        timeout 900 "$here/check.sh" "$BIN" "$PORT" "$PREFIX" "$FILTER" >"$LOGS/run.log" 2>&1
    st=$?
    slot_give
    sudo rm -rf "$S"; rmdir "/mnt/bko-ram/$PREFIX" 2>/dev/null
    if [[ ! -f $LOGS/results.txt ]]; then echo "FAIL harness: no results (exit $st, $LOGS/run.log)"; exit 1; fi
    grep -E "^(PASS|FAIL|LIMIT) ($FILTER)" "$LOGS/results.txt"
    ! grep -q '^FAIL' "$LOGS/results.txt"
    exit
fi

# ---- inside the reservation ----
mkdir -p "$LOGS" "$S"
. "$WORK/testvm/vm-lib.sh"
VM_PID=""
cleanup() { vm_halt; sudo rm -rf "$S/host"; }
trap cleanup EXIT
OUT=$LOGS/results.txt; : >"$OUT"
FAILS=$LOGS/fails NOTES=$LOGS/notes; : >"$FAILS"; : >"$NOTES"

echo "== host part: owners with no name, on $(hostname -s) ($(grep -E '^(passwd|group):' /etc/nsswitch.conf | tr -s ' \n' ' '))"
H=$S/host; sudo rm -rf "$H"; sudo mkdir -p "$H/src/d"
sudo sh -c "echo data >$H/src/f; chown 54321:54329 $H/src/f $H/src/d; mkfifo $H/src/p; chown 4242:4343 $H/src/p; touch $H/src/g; chown 0:54329 $H/src/g"
for t in restic rustic; do
    b=$BIN/$t-bko
    if [[ $t == rustic ]]; then
        sudo env RUSTIC_PASSWORD=x "$b" --no-progress --no-cache -r "$H/repo-$t" init >/dev/null 2>"$LOGS/host-$t.err" &&
            sudo env RUSTIC_PASSWORD=x "$b" --no-progress --no-cache -r "$H/repo-$t" backup --set-atime yes "$H/src" >/dev/null 2>>"$LOGS/host-$t.err"
        st=$?
        [[ $st -eq 0 ]] && { sudo env RUSTIC_PASSWORD=x "$b" --no-progress --no-cache -r "$H/repo-$t" restore --numeric-id latest:"$H/src" "$H/dst-$t" 2>>"$LOGS/host-$t.err"; st=$?; }
    else
        sudo env RESTIC_PASSWORD=x "$b" -r "$H/repo-$t" init -q 2>"$LOGS/host-$t.err" &&
            sudo env RESTIC_PASSWORD=x "$b" -r "$H/repo-$t" --no-cache backup -q --with-atime "$H/src" 2>>"$LOGS/host-$t.err"
        st=$?
        [[ $st -eq 0 ]] && { sudo env RESTIC_PASSWORD=x "$b" -r "$H/repo-$t" --no-cache restore -q latest:"$H/src" --target "$H/dst-$t" 2>>"$LOGS/host-$t.err"; st=$?; }
    fi
    want=$(cd "$H/src" && sudo stat -c '%n %u:%g' f d p g | tr '\n' ' ')
    got=$(cd "$H/dst-$t" 2>/dev/null && sudo stat -c '%n %u:%g' f d p g 2>/dev/null | tr '\n' ' ')
    if [[ $st -eq 0 && $want == "$got" ]]; then
        printf 'host-unnamed-ids-nss\t%s-bko on %s: backup and restore exit 0, owners kept (%s)\n' "$t" "$(hostname -s)" "$want" >>"$NOTES"
    else
        printf 'host-unnamed-ids-nss\t%s-bko on %s: exit %s (signal %s), owners %s != %s: %s\n' "$t" "$(hostname -s)" "$st" \
            "$([[ $st -gt 128 ]] && kill -l $((st - 128)) || echo none)" "$got" "$want" "$(tail -n 2 "$LOGS/host-$t.err" | tr '\n' ' ')" >>"$FAILS"
    fi
done
file "$BIN/rustic-bko" | sed 's/^[^:]*: /rustic-bko: /' >>"$LOGS/run.log"

echo "== guest part: artix-s6 on port $PORT"
export TMPDIR=$S
if vm_boot artix-s6 "$PORT" "$LOGS/vm" --snapshot --memory 3072; then
    tar -C "$BIN" -cf - restic-bko rustic-bko | vm_ssh 'rm -rf /root/bko && mkdir -p /root/bko && tar -C /root/bko -xf -'
    vm_put "$here/inner.sh" /root/bko/inner.sh
    vm_put "$WORK/testvm/compare/rt-common.sh" /root/bko/rt-common.sh 644
    T=780 vm_ssh 'bash /root/bko/inner.sh' >"$LOGS/inner.log" 2>&1
    echo "inner.sh exit $?" >>"$LOGS/inner.log"
    vm_ssh 'cat /root/bko/fails' >>"$FAILS"
    vm_ssh 'cat /root/bko/notes' >>"$NOTES"
    vm_ssh 'cd /root/bko && tar -cf - *.dump' | tar -C "$LOGS" -xf - 2>/dev/null
    grep -q '^== inner done' "$LOGS/inner.log" || printf 'harness\tinner.sh did not finish (%s)\n' "$LOGS/inner.log" >>"$FAILS"
    vm_halt
else
    printf 'harness\tthe guest did not boot\n' >>"$FAILS"
fi

# ---- the checklist ----
item() {  # NEED DETAIL-IF-PASS
    local n=$1 f
    f=$(awk -F'\t' -v n="$n" '$1 == n { print $2 }' "$FAILS")
    if [[ -n $f ]]; then
        echo "FAIL $n: $(wc -l <<<"$f") differences; $(head -n 4 <<<"$f" | tr '\n' ';')" >>"$OUT"
    else
        echo "PASS $n: $2" >>"$OUT"
    fi
}
notes() { awk -F'\t' -v n="$1" '$1 == n { print $2 }' "$NOTES" | tr '\n' ';'; }
item harness "the guest booted and inner.sh finished"
item host-unnamed-ids-nss "$(notes host-unnamed-ids-nss)"
item all-directions-exit-0 "backups and restores exit 0, ext4, four directions"
item content-and-tree "contents, every path present, non-UTF-8/space/250-byte names, empty and 40-deep dirs, the guest's /etc/s6 tree (FIFOs, setgid dirs) and /boot files"
item ownership "numeric owners incl. 54321:54329 and 4242:4343 with no name"
item modes-setid-sticky "setuid, setgid (file, dir), sticky"
item times-ns "mtime and atime to the ns, files, dirs, symlinks, 1970 and 2100"
item symlinks "targets, dangling, own times, trusted.* on a symlink"
item hard-links "a three-name link group"
item devices-fifos "c 1:3, b 7:0, c 0:0 (rdev 0), FIFO"
item sockets-skipped "the socket is in no restore and no message names it"
item xattrs "user.* (binary, 3000 B), trusted.*, security.*"
item capabilities "security.capability: the guest's newuidmap/newgidmap, cap_net_raw,cap_net_admin+ep"
item posix-acls "access and default ACLs"
item inode-flags "immutable (file, dir), append, nodump, noatime, sync, dirsync (ext4 keeps i a d A S D)"
item project-ids "as on the source (neither file system here has project quotas: 0 everywhere)"
item sparse "holey files (64 MiB with data at 0 and 32 MiB, 16 MiB all hole, a 64 KiB hole, the guest's locale-archive) restored holey"
item ancestor-dirs "/mnt, the mount root, anc1 (54321:54329 0751 user.ancestor), anc2 (0710, access+default ACL) restored with their metadata"
item same-repo-format "$(notes same-repo-format)"
item restore-over-immutable "$(notes restore-over-immutable)"
item non-root "uid 4321 backs up and restores its own tree (mode, xattr, ACL, hard link, symlink, FIFO, sparse, times), four directions, ext4"
item other "no other differences"
echo "LIMIT selinux: no SELinux (LSMs capability,landlock,lockdown,yama,bpf); no labels to keep" >>"$OUT"
echo "LIMIT nfsv4-acls: Linux ext4 has none" >>"$OUT"
echo "LIMIT birth-times: ext4 keeps them but Linux can't set one on restore (BACKOTTER.md)" >>"$OUT"
echo "LIMIT bsd-file-flags: Linux guest; chattr flags stand in" >>"$OUT"
echo "LIMIT fs-options: no ZFS, bcachefs or btrfs here; nocow (C) and compression (c) refused by ext4" >>"$OUT"
awk -F'\t' '{ print "note " $1 ": " $2 }' "$NOTES" >>"$LOGS/run.log"
python3 - "$OUT" "$LOGS/results.json" <<'PY'
import json, re, sys
items = []
for line in open(sys.argv[1]):
    m = re.match(r"(PASS|FAIL|LIMIT) ([^:]+): (.*)", line.rstrip("\n"))
    if m: items.append({"status": m[1], "need": m[2], "detail": m[3]})
json.dump({"guest": "artix-s6", "items": items}, open(sys.argv[2], "w"), indent=1)
PY
cp "$LOGS/results.json" "$here/results.json"
