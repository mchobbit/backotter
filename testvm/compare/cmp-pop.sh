#!/bin/sh
# cmp-pop.sh QUEUE MAXDISK MAXMEM [WHO [nodisk|anydisk]]: print and remove the first line of QUEUE
# whose needs fit: a trailing =N GiB of RAM disk (default 8) and %N GiB of
# memory (default 5). nodisk (a worker without cmp-run.sh's DISKS_DIR): no
# "!" line (GUEST...!=D%M, its restored disks on DISKS_DIR) fits, so that
# one is left for a host that can run it. Without a fifth argument (a worker
# that can take "!" lines) it also leaves QUEUE.disks, the mark that one has
# asked; anydisk takes any line, as that does, but leaves no mark (a nodisk
# worker that gave up waiting). Exit 0 with the line, 1 if lines are
# left but none fits (printing the first line's "DISK MEM" among those it may
# take, for cmp-run.sh's waiting line; with nodisk and only "!" lines left,
# "! N K", N their number, K 1 when QUEUE.disks is there, else 0) or the
# lock timed out (printing nothing), 3 if QUEUE is empty or missing, 4 if it couldn't
# work on QUEUE (QUEUE is then unchanged); any other status is the shell's own
# failure (sh exits 2 when it can't read this script). Every line taken goes
# to QUEUE.taken with WHO, so one lost in transit (ssh) can be found and
# requeued; add lines only under the lock:
#   flock QUEUE.lock sh -c 'echo LINE >>QUEUE'
# cmp-run.sh workers on every host call it, over ssh from the other host.
q=$1 d=$2 m=$3 who=${4:-?} nodisk=0 mark=1
case ${5:-} in "") ;; nodisk) nodisk=1 mark=0 ;; anydisk) mark=0 ;;
    *) echo "cmp-pop.sh: the fifth argument is nodisk, anydisk or none" >&2; exit 4 ;; esac
# command: a failed redirection on a plain exec would end sh with status 2.
command exec 9>>"$q.lock" || exit 4
flock -w 60 9 || exit 1
[ -e "$q" ] || exit 3
grep -q '[^[:space:]]' "$q" || exit 3
[ $mark = 0 ] || [ -e "$q.disks" ] || : >"$q.disks" 2>/dev/null
k=0; [ -e "$q.disks" ] && k=1
rm -f "$q.new"
line=$(awk -v d="$d" -v m="$m" -v nodisk="$nodisk" -v k="$k" -v new="$q.new" '
    function suffix(s, mark,   i) {   # the value of a trailing MARKnum, else ""
        if (match(s, "[" mark "][0-9.]+$")) return substr(s, RSTART + 1)
        return ""
    }
    NF == 0 { next }
    { s = $0; nm = suffix(s, "%"); if (nm != "") s = substr(s, 1, length(s) - length(nm) - 1)
      nd = suffix(s, "="); if (nd != "") s = substr(s, 1, length(s) - length(nd) - 1)
      if (nd == "") nd = 8; if (nm == "") nm = 5
      if (nodisk && s ~ /!$/) { bang++; print > new; next }   # restored disks on DISKS_DIR: not here
      if (first == "") first = nd " " nm
      if (!got && nd + 0 <= d + 0 && nm + 0 <= m + 0) { got = 1; print; next }
      print > new }
    END { close(new); if (!got) { print (first != "" ? first : "! " bang " " k); exit 1 } }' "$q")
rc=$?
case $rc in
0)  [ -e "$q.new" ] || : >"$q.new"
    mv -f "$q.new" "$q" || { rm -f "$q.new"; exit 4; }
    printf '%s %s %s\n' "$(date -u +%FT%T)" "$who" "$line" >>"$q.taken"
    printf '%s\n' "$line" ;;
1)  rm -f "$q.new"; printf '%s\n' "$line" ;;
*)  rm -f "$q.new"; rc=4 ;;     # awk failed (QUEUE.new not written?)
esac
exit $rc
