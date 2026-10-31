# Functions the guests' round trips (OS/roundtrip.sh) and nonroot-inner.sh
# share, sourced by them: decoding a snapshot's nodes, comparing two tools'
# snapshots of one tree, and preparing a restore over an existing tree.
# POSIX sh (and illumos' ksh); awk with functions (nawk, gawk, BSD awk,
# busybox awk).
#
#   rt_backup BIN REPO PATH...
#             init REPO and back PATH... up with BIN (restic or BackOtter's
#             rustic, by its name), as the round trips do; stderr shown
#   rt_restic BIN...
#             the restic among BIN... (by its name), else restic-bko in the
#             directory of the first: run-guest-rt.sh puts both tools in
#             /root, whichever direction runs
#   rt_nodes RESTIC REPO PATH
#             every node of REPO's latest snapshot at and below PATH, read
#             with RESTIC's "cat blob" (the JSON as the tool that wrote it
#             wrote it), one line each: "NAME<TAB>key=value<TAB>..." (NAME
#             "." for PATH itself, then relative paths), keys sorted, the
#             values canonical: object keys sorted, times as @SEC.NSEC in
#             UTC (restic writes "Z", rustic "+00:00", and either may drop a
#             zero fraction), null taken as absent; extended attributes
#             sorted by name, their stored order in the extra key
#             ~xattr_names (not compared: illumos' rustic keeps directory
#             order, restic sorts, AUDIT-R4 D4).  Leaves out content and
#             subtree (each repository chunks on its own) and device_id
#             (rustic stores it only with --set-devid).
#   rt_node_diff A B
#             what differs between two rt_nodes listings, one line per name
#             and key ("  NAME: key a != b"); keys starting with ~ apart,
#             in lines starting "  (not a check)".  Status 1 if a compared
#             key differs.
#   rt_snapshots BACKUP RESTORE REPO PATH...
#             when BACKUP and RESTORE are the two tools (restic and rustic,
#             either way round), the snapshot comparison (AUDIT-R4 F4): back
#             PATH... up with RESTORE too, into REPO-2, and compare the
#             nodes at and below each PATH in REPO's latest snapshot and
#             REPO-2's (rt_nodes, rt_node_diff).  Run it right after the
#             backup into REPO, before anything changes the source.  Prints
#             a "== " section per PATH and adds its title to $failed when
#             the nodes differ; nothing when BACKUP and RESTORE are one
#             tool.
#   rt_over DST DIR FILE...
#             change a restored tree before a restore over it (AUDIT-R4 F3):
#             DST/DIR moves to /root/bko-rt-elsewhere (a restore must
#             replace it, not write through it; its files get a line more,
#             and hard links of DST's files become copies) and a symlink to
#             there takes its place; each FILE
#             gets other bytes at its start, a line more, mode 0600 and the
#             time now.  DST's file flags must be cleared first (the
#             caller's job: chflags, chmod S-v).  Writes
#             /tmp/bko-rt.elsewhere.before, a listing of what is in
#             /root/bko-rt-elsewhere, for rt_over_check
#   rt_over_check
#             whether /root/bko-rt-elsewhere is as rt_over left it: status 1
#             (and what changed) if a restore wrote through the symlink

rt_awk=awk
for a in gawk nawk; do command -v $a >/dev/null 2>&1 && { rt_awk=$a; break; }; done

rt_is_rustic() { case $(basename "$1") in *rustic*) true ;; *) false ;; esac; }

rt_restic() {  # BIN...
    for rt_b in "$@"; do rt_is_rustic $rt_b || { echo $rt_b; return; }; done
    echo "$(dirname $1)/restic-bko"
}

rt_backup() {  # BIN REPO PATH...
    rt_b=$1 rt_r=$2; shift 2
    if rt_is_rustic $rt_b; then
        # Its stderr but the [INFO] lines.
        { RUSTIC_PASSWORD=backotter-compare $rt_b --no-progress --no-cache -r $rt_r init >/dev/null &&
            RUSTIC_PASSWORD=backotter-compare $rt_b --no-progress --no-cache -r $rt_r backup \
                --set-atime yes "$@" >/dev/null; } 2>/tmp/bko-rt-backup.err
        rt_s=$?
        grep -v '^\[INFO\]' /tmp/bko-rt-backup.err >&2
        return $rt_s
    else
        RESTIC_PASSWORD=backotter-compare $rt_b -r $rt_r init -q &&
            RESTIC_PASSWORD=backotter-compare $rt_b -r $rt_r backup -q "$@"
    fi
}

# The decoder: a tree's JSON on stdin; per node, one line:
# NAME<TAB>SUBTREE-or-"-"<TAB>key=value<TAB>... (as described above).
rt_decoder=/tmp/bko-rt-nodes.awk
cat >$rt_decoder <<'AWK'
# Tokens are cut off the front of s: matching at the start of a string is
# quick in every awk, indexing into the middle of a long one isn't (nawk).
function peekc() {
    if (index(" \t\r\n", substr(s, 1, 1))) sub(/^[ \t\r\n]+/, "", s)
    return substr(s, 1, 1)
}
function tokn(   c) {
    c = peekc()
    if (c == "") { tok = ""; return }
    if (c == "\"") match(s, /^"([^"\\]|\\.)*"/)
    else if (index("{}[],:", c)) RLENGTH = 1
    else match(s, /^[^],:{}[ \t\r\n]+/)
    tok = substr(s, 1, RLENGTH); s = substr(s, RLENGTH + 1)
}
function civil(y, m, d,   era, yoe, doy) {   # days since 1970-01-01
    if (m <= 2) y--
    era = int((y >= 0 ? y : y - 399) / 400); yoe = y - era * 400
    doy = int((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1
    return era * 146097 + yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy - 719468
}
function normtime(v,   t, frac, off, rest, sec) {
    if (v !~ /^"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/) return v
    t = substr(v, 2, length(v) - 2); rest = substr(t, 20); frac = ""
    if (substr(rest, 1, 1) == ".") {
        match(rest, /^\.[0-9]+/); frac = substr(rest, 2, RLENGTH - 1); rest = substr(rest, RLENGTH + 1)
    }
    if (rest == "Z") off = 0
    else if (rest ~ /^[+-][0-9][0-9]:[0-9][0-9]$/) {
        off = (substr(rest, 2, 2) * 3600 + substr(rest, 5, 2) * 60) * (substr(rest, 1, 1) == "-" ? -1 : 1)
    } else return v
    sec = civil(substr(t, 1, 4) + 0, substr(t, 6, 2) + 0, substr(t, 9, 2) + 0) * 86400 \
        + substr(t, 12, 2) * 3600 + substr(t, 15, 2) * 60 + substr(t, 18, 2) - off
    frac = substr(frac "000000000", 1, 9)
    return sprintf("@%d.%s", sec, frac)
}
function sortarr(a, cnt,   i, j, t) {
    for (i = 2; i <= cnt; i++) { t = a[i]; for (j = i - 1; j >= 1 && a[j] > t; j--) a[j + 1] = a[j]; a[j + 1] = t }
}
# The canonical text of the next value.  KEY: the key it is the value of.
function val(key,   t, c, k, v, cnt, i, keys, vals, out) {
    tokn(); t = tok
    if (t == "{") {
        cnt = 0
        for (;;) {
            tokn()
            if (tok == "}" || tok == "") break
            if (tok == ",") continue
            k = tok; tokn()                       # the ':'
            v = val(k)
            if (v == "null") continue
            keys[++cnt] = k; vals[k] = v
        }
        sortarr(keys, cnt); out = "{"
        for (i = 1; i <= cnt; i++) out = out (i > 1 ? "," : "") keys[i] ":" vals[keys[i]]
        return out "}"
    }
    if (t == "[") {
        cnt = 0
        for (;;) {
            c = peekc()
            if (c == "]" || c == "") { tokn(); break }
            if (c == ",") { tokn(); continue }
            keys[++cnt] = val("")
        }
        if (key == "\"extended_attributes\"") {
            xnames = ""
            for (i = 1; i <= cnt; i++) {
                v = keys[i]; sub(/^\{"name":"/, "", v); sub(/".*/, "", v); xnames = xnames (i > 1 ? "," : "") v
            }
            sortarr(keys, cnt)
        }
        out = "["
        for (i = 1; i <= cnt; i++) out = out (i > 1 ? "," : "") keys[i]
        return out "]"
    }
    if (substr(t, 1, 1) == "\"") return normtime(t)
    return t
}
{ s = s $0 "\n" }
END {
    i = index(s, "\"nodes\"")
    if (!i) exit 1
    s = substr(s, i + 7); tokn()                  # the ':'
    if (peekc() != "[") exit 0                     # "nodes": null
    tokn()
    for (;;) {
        c = peekc()
        if (c == "]" || c == "") break
        if (c == ",") { tokn(); continue }
        tokn()                                     # the node's '{'
        cnt = 0; name = "?"; subtree = "-"; xnames = ""; split("", nk); split("", nv)
        for (;;) {
            tokn()
            if (tok == "}" || tok == "") break
            if (tok == ",") continue
            k = tok; tokn()
            v = val(k)
            if (k == "\"name\"") name = substr(v, 2, length(v) - 2)
            if (k == "\"subtree\"") subtree = substr(v, 2, length(v) - 2)
            if (v == "null" || k == "\"name\"" || k == "\"subtree\"" || k == "\"content\"" || k == "\"device_id\"") continue
            nk[++cnt] = substr(k, 2, length(k) - 2); nv[nk[cnt]] = v
        }
        sortarr(nk, cnt)
        line = name "\t" subtree
        for (j = 1; j <= cnt; j++) line = line "\t" nk[j] "=" nv[nk[j]]
        if (xnames != "") line = line "\t~xattr_names=" xnames
        print line
    }
}
AWK

rt_tree() {  # RESTIC REPO ID: the decoder's lines for one tree
    RESTIC_PASSWORD=backotter-compare $1 -r $2 --no-cache --no-lock cat blob $3 | $rt_awk -f $rt_decoder
}

rt_nodes() {  # RESTIC REPO PATH
    rt_id=$(RESTIC_PASSWORD=backotter-compare $1 -r $2 --no-cache --no-lock snapshots --json latest |
        sed -n 's/.*"tree":"\([0-9a-f]*\)".*/\1/p' | head -n 1)
    [ -n "$rt_id" ] || { echo "rt_nodes: no snapshot in $2" >&2; return 1; }
    rt_rest=${3#/} rt_self=""
    while [ -n "$rt_rest" ]; do
        rt_comp=${rt_rest%%/*}
        case $rt_rest in */*) rt_rest=${rt_rest#*/} ;; *) rt_rest="" ;; esac
        rt_self=$(rt_tree $1 $2 $rt_id | $rt_awk -F '\t' -v n="$rt_comp" '$1 == n')
        [ -n "$rt_self" ] || { echo "rt_nodes: no $rt_comp in $2's tree" >&2; return 1; }
        rt_id=$(printf '%s\n' "$rt_self" | cut -f 2)
    done
    printf '%s\n' "$rt_self" | $rt_awk -F '\t' 'BEGIN { OFS = "\t" } { $1 = "."; $2 = ""; sub(/\t\t/, "\t"); print }'
    # Breadth first, the trees still to read in a queue: ID<TAB>PREFIX.
    printf '%s\t\n' "$rt_id" >/tmp/bko-rt-nodes.queue
    while [ -s /tmp/bko-rt-nodes.queue ]; do
        IFS='	' read -r rt_id rt_pre </tmp/bko-rt-nodes.queue
        sed 1d /tmp/bko-rt-nodes.queue >/tmp/bko-rt-nodes.queue2 && mv /tmp/bko-rt-nodes.queue2 /tmp/bko-rt-nodes.queue
        rt_tree $1 $2 $rt_id >/tmp/bko-rt-nodes.tree || { echo "rt_nodes: can't read tree $rt_id" >&2; return 1; }
        $rt_awk -F '\t' -v pre="$rt_pre" '$2 != "-" { print $2 "\t" pre $1 "/" }' \
            /tmp/bko-rt-nodes.tree >>/tmp/bko-rt-nodes.queue
        $rt_awk -F '\t' -v pre="$rt_pre" 'BEGIN { OFS = "\t" } { $1 = pre $1; $2 = ""; sub(/\t\t/, "\t"); print }' \
            /tmp/bko-rt-nodes.tree
    done
    rm -f /tmp/bko-rt-nodes.queue /tmp/bko-rt-nodes.tree
}

rt_node_diff() {  # A B
    $rt_awk -F '\t' '
        function load(line, arr,   i, nf, f, k) {
            nf = split(line, f, "\t")
            for (i = 2; i <= nf; i++) { k = substr(f[i], 1, index(f[i], "=") - 1); arr[k] = substr(f[i], index(f[i], "=") + 1) }
        }
        NR == FNR { a[$1] = $0; next }
        { seen[$1] = 1
          if (!($1 in a)) { print "  " $1 ": only in the second"; bad = 1; next }
          if (a[$1] == $0) next
          split("", x); split("", y); load(a[$1], x); load($0, y)
          for (k in y) if (!(k in x)) x[k] = "(none)"
          for (k in x) {
              v = (k in y) ? y[k] : "(none)"
              if (x[k] == v) continue
              if (substr(k, 1, 1) == "~") print "  (not a check) " $1 ": " k " " x[k] " != " v
              else { print "  " $1 ": " k " " substr(x[k], 1, 200) " != " substr(v, 1, 200); bad = 1 }
          } }
        END { for (p in a) if (!(p in seen)) { print "  " p ": only in the first"; bad = 1 }
              exit bad }' "$1" "$2" | sort
    # (sort hides awk's status: compared again)
    $rt_awk -F '\t' 'function strip(l) { gsub(/\t~[^\t]*/, "", l); return l }
        NR == FNR { a[$1] = strip($0); next }
        { seen[$1] = 1; if (!($1 in a) || a[$1] != strip($0)) bad = 1 }
        END { for (p in a) if (!(p in seen)) bad = 1; exit bad }' "$1" "$2"
}

rt_snapshots() {  # BACKUP RESTORE REPO PATH...
    rt_sb=$1 rt_sr=$2 rt_repo=$3; shift 3
    if rt_is_rustic $rt_sb && ! rt_is_rustic $rt_sr; then rt_restic=$rt_sr
    elif rt_is_rustic $rt_sr && ! rt_is_rustic $rt_sb; then rt_restic=$rt_sb
    else return 0; fi
    echo "== backup with $(basename $rt_sr) too, into $rt_repo-2, for the snapshot comparison"
    rt_backup $rt_sr $rt_repo-2 "$@"
    echo "exit $?"
    for rt_p in "$@"; do
        rt_t="snapshot nodes of $rt_p, $(basename $rt_sb)'s vs $(basename $rt_sr)'s (all fields but content, subtree, device_id)"
        echo "== $rt_t"
        if ! rt_nodes $rt_restic $rt_repo $rt_p >/tmp/bko-rt.nodes.1 ||
            ! rt_nodes $rt_restic $rt_repo-2 $rt_p >/tmp/bko-rt.nodes.2; then
            echo "can't decode the snapshots"; failed="$failed; $rt_t"; continue
        fi
        if rt_node_diff /tmp/bko-rt.nodes.1 /tmp/bko-rt.nodes.2; then
            echo "identical ($(wc -l </tmp/bko-rt.nodes.1 | tr -d ' ') nodes)"
        else
            failed="$failed; $rt_t"
        fi
    done
}

# What is in /root/bko-rt-elsewhere: names, and the checksums of files with
# one link (another link's file may be rewritten in place, by right).
rt_links() { ls -ld "$1" | $rt_awk '{ print $2 }'; }
rt_list_elsewhere() {
    (cd /root/bko-rt-elsewhere && find . | sort | while IFS= read -r p; do
        if [ -f "$p" ] && [ ! -L "$p" ] && [ "$(rt_links "$p")" = 1 ]; then echo "$p $(cksum <"$p")"
        else echo "$p"; fi
    done)
}

rt_over() {  # DST DIR FILE...
    rt_dst=$1 rt_dir=$2; shift 2
    rm -rf /root/bko-rt-elsewhere
    mv "$rt_dst/$rt_dir" /root/bko-rt-elsewhere || return 1
    for rt_f in /root/bko-rt-elsewhere/*; do
        [ -f "$rt_f" ] && [ ! -L "$rt_f" ] || continue
        # A hard link of a file in DST: a copy of its own instead, so that
        # DST's link counts are the restore's alone.
        if [ "$(rt_links "$rt_f")" != 1 ]; then
            cp "$rt_f" "$rt_f.copy" && rm -f "$rt_f" && mv "$rt_f.copy" "$rt_f"
        fi
        echo "elsewhere's own" >>"$rt_f"
    done
    ln -s /root/bko-rt-elsewhere "$rt_dst/$rt_dir"
    for rt_f in "$@"; do
        printf XX | dd of="$rt_dst/$rt_f" conv=notrunc 2>/dev/null
        echo changed >>"$rt_dst/$rt_f"
        chmod 600 "$rt_dst/$rt_f"; touch "$rt_dst/$rt_f"
    done
    rt_list_elsewhere >/tmp/bko-rt.elsewhere.before
}

rt_over_check() {
    rt_list_elsewhere >/tmp/bko-rt.elsewhere.after
    diff /tmp/bko-rt.elsewhere.before /tmp/bko-rt.elsewhere.after
}
