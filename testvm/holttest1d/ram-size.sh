#!/usr/bin/env bash
# The room a HoltTest1 run needs, for `cmp-run.sh --reserve D M`: D GiB of
# the RAM disk and M GiB of memory (README "Where a run works, and what it
# keeps"; the queue's =D and %M override it: "Queues: ht1-queue.sh").
#
#   ram-size.sh [--table FILE] [--peaks FILE] GUEST[+VARIANT]...
#       a line "GUEST[+VARIANT] D M FROM" for each; FROM is where D came from:
#       peaks:N        its last N (at most 3) whole passes in peaks.tsv (result
#                      PASS, all four phases, not seeded), when it has 2 or
#                      more: D = 1.10 x their largest RAM-disk peak + 0.25 GiB,
#                      M = 1.10 x their largest anon, rounded up; D at least 2
#                      (3 for BSD, illumos and Haiku), M at least 4
#       table+peaks:1  one such pass: the larger of its D and M and the table's
#                      (the default's, without a row)
#       table          its row in ram-sizes.tsv: this host's, else the largest;
#                      a variant without a row has its guest's
#   +nearfull on the end: 4 GiB more D, for the free space that variant fills
#   in the source overlay (no row of its own tells anyone how much)
#       default        D 6 (Linux), 4 (BSD, illumos, Haiku), 2 (redox,
#                      sculpt); M 4
#   ram-size.sh [--table FILE] [--peaks FILE] --big GIB
#       the names whose D is GIB or more, one per line: the table's, the
#       peaks', and the variants of each (ht1-queue.sh's big guests)
#
# The FILEs default to ram-sizes.tsv beside this script and
# /bkoscratch/holttest1d/peaks.tsv (ht1-lib.sh's, under HT_RECORDS when that
# is set).  It writes nothing.  Exit 0, or 2 on bad usage.
set -uo pipefail

HERE=$(dirname "$(realpath "$0")")
TABLE=$HERE/ram-sizes.tsv PEAKS=${HT_RECORDS:-/bkoscratch/holttest1d}/peaks.tsv BIG=""
usage() { echo "usage: ram-size.sh [--table FILE] [--peaks FILE] GUEST[+VARIANT]... | --big GIB" >&2; exit 2; }
while [[ ${1:-} == --* ]]; do
    [[ $# -ge 2 ]] || usage
    case $1 in
    --table) TABLE=$2 ;;
    --peaks) PEAKS=$2 ;;
    --big) [[ $2 =~ ^[0-9]+([.][0-9]+)?$ ]] || usage; BIG=$2 ;;
    *) usage ;;
    esac
    shift 2
done
if [[ -n $BIG ]]; then [[ $# -eq 0 ]] || usage; else [[ $# -gt 0 ]] || usage; fi
name='^[A-Za-z0-9][A-Za-z0-9_.-]*([+](bigger|oneshot|degraded|smaller|fitbigger|nearfull|refuse))?$'
for n; do [[ $n =~ $name ]] || { echo "ram-size.sh: not GUEST[+VARIANT]: $n" >&2; exit 2; }; done
[[ -r $TABLE && ! -d $TABLE ]] || { echo "ram-size.sh: no table $TABLE" >&2; exit 2; }
[[ -r $PEAKS && ! -d $PEAKS ]] || PEAKS=/dev/null   # no run logged yet

# ram-sizes.tsv: guest variant host left_bytes D MEM source ("#" comments, a
# header).  peaks.tsv (ht1-lib.sh): end_utc host driver run gv phases seeded
# rc result mode reserved_gib peak_disk_kib peak_anon_kib wall_s, a header
# first; its columns are found by name, else taken in that order.
awk -F'\t' -v host="$(hostname -s)" -v big="$BIG" -v names="$*" '
    function fam(g) {   # as ht1.sh picks the driver: 1 ht1-bsd.sh, 2 ht1-offline.sh, 0 Linux
        if (g ~ /^(freebsd|hardenedbsd|ghostbsd|netbsd|alpinebsd|openbsd|dragonfly|omnios|haiku)/) return 1
        if (g == "redox" || g ~ /^sculpt/) return 2
        return 0
    }
    function up(x,   c) { c = int(x); if (c < x - 1e-9) c++; return c }   # rounded up (no float noise)
    function table(n, g) {   # -> "D M table", from the row of n, else of its guest; else "D M default"
        if (!(n in td)) n = g
        if (n in td) return td[n] " " tm[n] " table"
        return (fam(g) == 1 ? 4 : fam(g) == 2 ? 2 : 6) " 4 default"
    }
    function size(n,   v, i, r, p) {   # GUEST[+VARIANT] -> "D M FROM", the variant included
        v = ""; i = index(n, "+"); if (i) v = substr(n, i + 1)
        r = runsize(n)
        # nearfull fills / in the source guest in its source phase, so the
        # overlay grows by the free space it writes away: 4 GiB over a plain
        # run of the same guest, which the table and the peaks know nothing of.
        if (v == "nearfull") { split(r, p, " "); r = (p[1] + 4) " " p[2] " " p[3] "+nearfull" }
        return r
    }
    function runsize(n,   g, v, gv, i, k, p, a, d, m, fl, tb) {   # GUEST[+VARIANT] -> "D M FROM"
        g = n; v = ""; i = index(n, "+"); if (i) { g = substr(n, 1, i - 1); v = substr(n, i + 1) }
        gv = g (v != "" ? "-" v : "")
        if (!(gv in np)) return table(n, g)
        p = 0; a = 0
        for (k = 0; k < 3; k++) if ((gv, k) in pd) {
            if (pd[gv, k] > p) p = pd[gv, k]
            if (pa[gv, k] > a) a = pa[gv, k]
        }
        fl = fam(g) == 1 ? 3 : 2
        d = up(1.10 * p / 1048576 + 0.25); if (d < fl) d = fl
        m = up(1.10 * a / 1048576); if (m < 4) m = 4
        if (np[gv] >= 2) return d " " m " peaks:" (np[gv] < 3 ? np[gv] : 3)
        split(table(n, g), tb, " ")   # one pass only: never under the table
        return (tb[1] + 0 > d ? tb[1] : d) " " (tb[2] + 0 > m ? tb[2] : m) " table+peaks:1"
    }
    BEGIN { cg = 5; cp = 6; cs = 7; cr = 9; cd = 12; ca = 13 }
    t {   # the table: per name, the largest D among the rows for this host, and among all
        if ($1 ~ /^#/ || $5 !~ /^[0-9]+([.][0-9]+)?$/) next      # comments, the header
        n = $1 ($2 != "-" && $2 != "" ? "+" $2 : ""); d = $5 + 0; m = ($6 ~ /^[0-9]/) ? $6 + 0 : 4
        if (!(n in ad) || d > ad[n] || (d == ad[n] && m > am[n])) { ad[n] = d; am[n] = m }
        if ($3 == host && (!(n in hd) || d > hd[n] || (d == hd[n] && m > hm[n]))) { hd[n] = d; hm[n] = m }
        next
    }
    {   # the header (a field "gv"): where the columns are
        for (i = 1; i <= NF && $i != "gv"; i++) ;
        if (i <= NF) {
            for (i = 1; i <= NF; i++) { h = $i; sub(/\(.*/, "", h); col[h] = i }
            cg = col["gv"]; cs = ("seeded" in col) ? col["seeded"] : 0
            if ("phases" in col) cp = col["phases"]; if ("result" in col) cr = col["result"]
            if ("peak_disk_kib" in col) cd = col["peak_disk_kib"]; if ("peak_anon_kib" in col) ca = col["peak_anon_kib"]
            next
        }
    }
    {   # a run: kept when it is a whole pass; three per guest, the latest
        ph = $cp; gsub(/[ ,]+/, ",", ph)
        if ($cg == "" || $cd !~ /^[0-9]+$/ || ph != "source,backup,rebuild,verify" || $cr != "PASS" || (cs && $cs != "0")) next
        gv = $cg; k = np[gv]++ % 3
        pd[gv, k] = $cd + 0; pa[gv, k] = ($ca ~ /^[0-9]+$/) ? $ca + 0 : -1   # anon "-": none
        n = gv; if (match(n, /-(bigger|oneshot|degraded|smaller|fitbigger|nearfull|refuse)$/)) n = substr(n, 1, RSTART - 1) "+" substr(n, RSTART + 1)
        known[n] = 1
    }
    END {
        for (n in ad) if (n in hd) { td[n] = hd[n]; tm[n] = hm[n] } else { td[n] = ad[n]; tm[n] = am[n] }
        if (big == "") { k = split(names, a, " "); for (i = 1; i <= k; i++) print a[i], size(a[i]); exit }
        for (n in td) known[n] = 1
        for (n in known) { g = n; sub(/[+].*/, "", g); base[g] = 1 }
        for (g in base) { known[g] = 1
            split("bigger oneshot degraded smaller fitbigger nearfull refuse", vs, " ")
            for (i in vs) known[g "+" vs[i]] = 1 }
        for (n in known) { split(size(n), r, " "); if (r[1] + 0 >= big + 0) print n | "sort" }
        close("sort")
    }' t=1 "$TABLE" t=0 "$PEAKS"
