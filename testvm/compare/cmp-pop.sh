#!/bin/sh
# cmp-pop.sh QUEUE MAXDISK MAXMEM [WHO]: print and remove the first line of QUEUE
# whose needs fit: a trailing =N GiB of RAM disk (default 8) and %N GiB of
# memory (default 5). Exit 0 with the line, 1 if lines are left but none fits
# (or the lock timed out), 3 if QUEUE is empty or missing, 4 if it couldn't
# work on QUEUE (QUEUE is then unchanged); any other status is the shell's own
# failure (sh exits 2 when it can't read this script). Every line taken goes
# to QUEUE.taken with WHO, so one lost in transit (ssh) can be found and
# requeued; add lines only under the lock:
#   flock QUEUE.lock sh -c 'echo LINE >>QUEUE'
# cmp-run.sh workers on every host call it, over ssh from the other host.
q=$1 d=$2 m=$3 who=${4:-?}
# command: a failed redirection on a plain exec would end sh with status 2.
command exec 9>>"$q.lock" || exit 4
flock -w 60 9 || exit 1
[ -e "$q" ] || exit 3
grep -q '[^[:space:]]' "$q" || exit 3
rm -f "$q.new"
line=$(awk -v d="$d" -v m="$m" -v new="$q.new" '
    function suffix(s, mark,   i) {   # the value of a trailing MARKnum, else ""
        if (match(s, "[" mark "][0-9.]+$")) return substr(s, RSTART + 1)
        return ""
    }
    NF == 0 { next }
    { s = $0; nm = suffix(s, "%"); if (nm != "") s = substr(s, 1, length(s) - length(nm) - 1)
      nd = suffix(s, "="); if (nd == "") nd = 8; if (nm == "") nm = 5
      if (!got && nd + 0 <= d + 0 && nm + 0 <= m + 0) { got = 1; print; next }
      print > new }
    END { close(new); if (!got) exit 1 }' "$q")
rc=$?
case $rc in
0)  [ -e "$q.new" ] || : >"$q.new"
    mv -f "$q.new" "$q" || { rm -f "$q.new"; exit 4; }
    printf '%s %s %s\n' "$(date -u +%FT%T)" "$who" "$line" >>"$q.taken"
    printf '%s\n' "$line" ;;
1)  rm -f "$q.new" ;;
*)  rm -f "$q.new"; rc=4 ;;     # awk failed (QUEUE.new not written?)
esac
exit $rc
