# Functions the guests' round trips (OS/roundtrip.sh) and nonroot-inner.sh
# share, sourced by them: decoding a snapshot's nodes, comparing two tools'
# snapshots of one tree, and preparing a restore over an existing tree.
# POSIX sh (and illumos' ksh); awk with functions (nawk, gawk, BSD awk,
# busybox awk).
#
#   rt_backup BIN REPO PATH...
#             init REPO and back PATH... up with BIN (restic or BackOtter's
#             rustic, by its name), as the round trips do, with BackOtter's
#             flags (real atimes: restic --with-atime, rustic --set-atime
#             yes; both forks keep stock's mtime-as-atime default); stderr
#             shown
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
#             zero fraction), null taken as absent, as is a mode of 0
#             (rustic-bko writes "mode":0, restic leaves it out); extended
#             attributes sorted by name, their stored order in the extra key
#             xattr_names (compared: both tools store them sorted by name
#             on every OS but Windows, AUDIT-R6 N4).  Leaves out content and
#             subtree (each repository chunks on its own) and device_id
#             (rustic stores it only with --set-devid).  With RT_CONTENT
#             set, content is kept too: content=["ID",...].
#   rt_nodes RESTIC REPO PATH PRUNE
#             the same, but not descending into the directories whose path
#             (relative to PATH, with a trailing /) matches the ERE PRUNE:
#             their own nodes are listed, not what is below them
#   rt_path_nodes RESTIC REPO PATH
#             the own node of each directory on the way to PATH and of PATH
#             itself (not the snapshot's root), as rt_nodes prints them but
#             each named by its absolute path ("/mnt", "/mnt/src", ...):
#             one tree read per level, not a walk of what is below
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
#             time now.  DST itself gets mode 0750 and the time now: holding
#             a tree, it isn't a fresh target, so a restore over it must
#             leave its own "." as it is then (rt_dot), not give it the
#             subfolder's.  DST's file flags must be cleared first (the
#             caller's job: chflags, chmod S-v).  Writes
#             /tmp/bko-rt.elsewhere.before, a listing of what is in
#             /root/bko-rt-elsewhere, for rt_over_check
#   rt_over_check
#             whether /root/bko-rt-elsewhere is as rt_over left it: status 1
#             (and what changed) if a restore wrote through the symlink
#   rt_dot DIR
#             DIR's own metadata, one line each: its flags (but on
#             illumos), mode and owner; its atime; its mtime (to the
#             nanosecond); its ACL and default ACL (getfacl -n; ls -dV on
#             illumos), its system attributes (illumos: ls -d/v) and its
#             extended attributes (name and MD5 of the value: lsextattr in
#             both namespaces, runat on illumos); neither on OpenBSD or
#             DragonFly, elsewhere a "missing-tool NAME" line (and status
#             1) for a tool not there.  The
#             stat(1) lines come first, and stat reads nothing; reading the
#             ACLs and extended attributes may move the atime where atimes
#             are on, so an exact check needs DIR's file system noatime.  BSD
#             stat(1) but on illumos (GNU stat there: /usr/gnu/bin's, else
#             /usr/bin's, OpenIndiana's)
#   rt_dot_check BEFORE AFTER [forward|forward-mtime]
#             whether two rt_dot records of a restore target are the same,
#             for a restore over a target that isn't fresh (A3: its "." is
#             left as it was, as stock restic and rustic leave it).  Both
#             must hold the flags, mode and owner line, the atime and the
#             mtime.  With "forward", the atime and the mtime may also have
#             moved forward (shown, not a check): the restore made or
#             replaced an entry in DIR itself, and DIR's file system keeps
#             atimes (or updates one with the mtime, as OpenBSD's noatime
#             does); with "forward-mtime", the mtime alone (atimes off).
#             Never back: the subfolder's metadata copied onto DIR would set
#             them to the source's.  Status 1, and what differs, otherwise
#   rt_nodot
#             a listing on stdin without the restore target's own entries
#             ("."), the rest as it was: lines whose first word is "." or
#             ".:" (stat -f '%N ...', mtree -f's report, illumos' listing,
#             fsmeta.sh's) and the lines indented below one; fsmeta.sh's
#             extended attribute lines "NAMESPACE . NAME"; getfacl's
#             "# file: ." block, up to a blank line, a "getfacl:" message
#             or the next "# file:"
#   rt_nodot_diff A B
#             diff A B, both without "."'s entries (rt_nodot)
#   rt_nodot_run COMMAND...
#             COMMAND... (mtree -f: a tree against a spec), its output and
#             errors shown without "."'s report (rt_nodot); status 0 if
#             COMMAND's is, or if nothing it said is left
#   rt_mksock PATH
#             a Unix socket at PATH, bound and closed, with whatever the
#             system has: python3, perl, cc (a few lines of C), or nc -lU
#             killed once the socket is there (it leaves it).  Status 1, and
#             why, if none of them could.  Both tools skip sockets (SPEC3 1):
#             it makes one for a tree's backup to leave out
#
# rt_nodes' scratch files and the decoder go in RT_TMP (default /tmp): a
# script on a host that runs others at the same time (sparse-rt.sh) gives
# it a directory of its own.
#
# How rt_nodes reads the trees.  Every restic call derives the key with
# scrypt (~0.5 s: init tunes it to that) and loads the whole index, so one
# "cat blob" a tree was hours for a tree of 16k directories (qubes43's dom0
# root: ~0.8 s a call, two repositories).  rt_nodes reads each tree blob's
# bytes, unchanged, with the rustic beside RESTIC (rustic-bko, or rustic)
# instead: given the master key (restic's "cat masterkey", once), so no
# KDF, and from a view of REPO in RT_TMP holding only REPO's tree packs
# (hard links, else copies) and an index of them alone (rustic's "repair
# index" of the view), so each call loads a few hundred KiB of index, not
# all of REPO's.  A blob's bytes are the same whichever tool decrypts them
# (the JSON as the tool that wrote it wrote it, decoded as before); the
# view is checked against restic's "cat blob" of the snapshot's root tree
# before it is used, and any tree it fails to give is read with restic.
# No rustic, or any step failing: restic for every tree, as before.  The
# trees of one level are read in RT_JOBS lanes at once (default the CPUs
# online, 4 to 12: rt_cpus), each lane's trees decoded by an awk of its own,
# and the lanes' listings put together in the order the old queue read the
# trees (breadth first, each tree's subtrees in node order), so the listing
# is the same.  Reading a tree is quick (~20 ms with the view); decoding a
# big one was what took the time, one awk at a time and in the square of
# its size, before its window (the decoder's "Tokens are cut off..."):
# rt_nodes of a repository of chopin's /usr/share (200k nodes) took 1189 s,
# now 96 s (104 s in 4 lanes), the listing byte for byte the same
# (2026-11-18).

rt_awk=awk
rt_tmp=${RT_TMP:-/tmp}
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
            RESTIC_PASSWORD=backotter-compare $rt_b -r $rt_r backup -q --with-atime "$@"
    fi
}

# The decoder: a tree's JSON on stdin; per node, one line:
# NAME<TAB>SUBTREE-or-"-"<TAB>key=value<TAB>... (as described above).
rt_decoder=$rt_tmp/bko-rt-nodes.awk
cat >$rt_decoder <<'AWK'
# Tokens are cut off the front of s: matching at the start of a string is
# quick in every awk, indexing into the middle of a long one isn't (nawk).
# And s is a window of the tree, not the whole of it: cutting a token off
# copies what is left, so a tree's decoding took time in the square of its
# size (a directory of thousands of entries: minutes; most of rt_walk's
# time on a real root, qubes43's dom0, more than reading the trees).  The
# tree is cut into chunks of at most 1024 bytes once (load, in halves: as
# quick in an awk whose substr counts UTF-8 characters from the start), and
# the window takes the next chunk when it runs low (more); a token is
# matched again with more of the tree while it reaches the window's end (a
# string's closing quote not in it yet), so the tokens, and every line
# printed, are the same.
function load(t) { rt_nc = 0; rt_nx = 1; s = ""; cut(t) }
function cut(t,   h) {
    h = length(t)
    if (h <= 1024) { rt_ch[++rt_nc] = t; return }
    h = int(h / 2); cut(substr(t, 1, h)); cut(substr(t, h + 1))
}
function more(n) {   # N more chunks onto the window: 0 when there were none
    if (rt_nx > rt_nc) return 0
    for (; n > 0 && rt_nx <= rt_nc; n--) { s = s rt_ch[rt_nx]; delete rt_ch[rt_nx]; rt_nx++ }
    return 1
}
function peekc() {
    for (;;) {
        if (length(s) < 256) more(1)
        if (index(" \t\r\n", substr(s, 1, 1))) sub(/^[ \t\r\n]+/, "", s)
        if (s != "" || rt_nx > rt_nc) return substr(s, 1, 1)
    }
}
function tokn(   c, n) {
    c = peekc()
    if (c == "") { tok = ""; return }
    n = 1
    if (c == "\"") { while (!match(s, /^"([^"\\]|\\.)*"/) && more(n)) n *= 2 }
    else if (index("{}[],:", c)) RLENGTH = 1
    else { while (match(s, /^[^],:{}[ \t\r\n]+/) && RLENGTH == length(s) && more(n)) n *= 2 }
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
# One node: NAME<TAB>SUBTREE REST (REST: "<TAB>key=value"...).  Walking
# (rt_nodes' levels), PRE NAME and REST instead (a lone tab after the name
# when REST is empty, as the old "$2 = """ left it), and the subtree, if
# any and not pruned, to the next level's queue: SUBTREE<TAB>PRE NAME/.
function emit(name, subtree, rest) {
    if (!walk) { print name "\t" subtree rest; return }
    if (subtree != "-" && (prune == "" || (pre name "/") !~ prune)) print subtree "\t" pre name "/" >queue
    print pre name (rest == "" ? "\t" : rest)
}
# The tree in s: 0 if it has no "nodes".
function tree(   c, j, rest) {
    i = index(s, "\"nodes\"")
    if (!i) return 0
    load(substr(s, i + 7)); tokn()                # the ':'
    if (peekc() != "[") return 1                   # "nodes": null
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
            if (k == "\"content\"" && keepcontent != "" && v != "null") { nk[++cnt] = "content"; nv["content"] = v; continue }
            if (v == "null" || k == "\"name\"" || k == "\"subtree\"" || k == "\"content\"" || k == "\"device_id\"") continue
            if (k == "\"mode\"" && v == "0") continue    # rustic writes 0, restic leaves it out
            nk[++cnt] = substr(k, 2, length(k) - 2); nv[nk[cnt]] = v
        }
        sortarr(nk, cnt)
        rest = ""
        for (j = 1; j <= cnt; j++) rest = rest "\t" nk[j] "=" nv[nk[j]]
        if (xnames != "") rest = rest "\txattr_names=" xnames
        emit(name, subtree, rest)
    }
    return 1
}
function bad() { print id >badfile; failed = 1; exit 1 }
# Walking, each tree comes after a line "\001ID<TAB>PRE".
walk && substr($0, 1, 1) == "\001" {
    if (started && !tree()) bad()
    t = substr($0, 2); id = substr(t, 1, index(t, "\t") - 1); pre = substr(t, index(t, "\t") + 1)
    s = ""; started = 1; next
}
{ s = s $0 "\n" }
END {
    if (failed) exit 1
    if (!walk) { if (!tree()) exit 1; exit 0 }
    if (started && !tree()) bad()
}
AWK

# The fast reader (see "How rt_nodes reads the trees" above).
# Its variables and rt_walk's are rt_fast_* and rt_w_*: rt_nodes runs in
# its caller's shell, and a caller's own (rt_snapshots' rt_p) must outlive it.
rt_fast_repo="" rt_fast_dir="" rt_fast_bin=""
rt_rustic() {  # ARG...: rt_fast_bin with the master key, none of the caller's passwords
    (unset RUSTIC_PASSWORD RUSTIC_PASSWORD_FILE RUSTIC_PASSWORD_COMMAND RUSTIC_KEY RUSTIC_KEY_FILE RUSTIC_KEY_COMMAND
     exec "$rt_fast_bin" --no-progress --no-cache --key-file "$rt_fast_dir/key" "$@") </dev/null
}
rt_fast_close() {
    [ -z "$rt_fast_dir" ] || rm -rf "$rt_fast_dir"
    rt_fast_repo="" rt_fast_dir=""
}
rt_fast_packs() {  # REPO: the packs holding tree blobs, from REPO's index files
    rm -f "$rt_fast_dir/index.bad"
    for rt_fast_f in "$1"/index/*; do
        [ -f "$rt_fast_f" ] || continue
        rt_rustic -r "$1" cat index "${rt_fast_f##*/}" 2>>"$rt_fast_dir/err" || { : >"$rt_fast_dir/index.bad"; break; }
    done | tr -d ' \011\012\015' | tr '{' '\012' | $rt_awk '
        /"blobs":\[/ { p = ""; if (match($0, /"id":"[0-9a-f]+"/)) p = substr($0, RSTART + 6, RLENGTH - 7); next }
        /"type":"tree"/ && p != "" { if (!seen[p]++) print p; p = "" }'
    [ ! -f "$rt_fast_dir/index.bad" ]
}
rt_fast_link() {  # REPO: the packs on stdin into the view, hard linked (else copied)
    while IFS= read -r rt_fast_p; do
        rt_fast_d=${rt_fast_p%"${rt_fast_p#??}"}
        [ -f "$1/data/$rt_fast_d/$rt_fast_p" ] && mkdir -p "$rt_fast_dir/view/data/$rt_fast_d" &&
            { ln "$1/data/$rt_fast_d/$rt_fast_p" "$rt_fast_dir/view/data/$rt_fast_d/$rt_fast_p" 2>/dev/null ||
                cp "$1/data/$rt_fast_d/$rt_fast_p" "$rt_fast_dir/view/data/$rt_fast_d/$rt_fast_p"; } || return 1
    done
}
rt_fast_open() {  # RESTIC REPO TREE-ID: the view of REPO, if it gives TREE-ID as restic does
    rt_fast_close
    rt_fast_bin=""
    for rt_fast_c in "$(dirname "$1")/rustic-bko" "$(dirname "$1")/rustic"; do
        [ -x "$rt_fast_c" ] && { rt_fast_bin=$rt_fast_c; break; }
    done
    [ -n "$rt_fast_bin" ] && [ -f "$2/config" ] && [ -d "$2/index" ] && [ -d "$2/data" ] || return 1
    rt_fast_dir=$rt_tmp/bko-rt-fast
    rm -rf "$rt_fast_dir"
    rt_fast_v=$rt_fast_dir/view
    if mkdir -p "$rt_fast_v/data" "$rt_fast_v/index" "$rt_fast_v/keys" "$rt_fast_v/snapshots" && cp "$2/config" "$rt_fast_v/config" &&
        RESTIC_PASSWORD=backotter-compare $1 -r $2 --no-cache --no-lock cat masterkey \
            >"$rt_fast_dir/key" 2>"$rt_fast_dir/err" </dev/null &&
        rt_fast_packs "$2" >"$rt_fast_dir/packs" && [ -s "$rt_fast_dir/packs" ] &&
        rt_fast_link "$2" <"$rt_fast_dir/packs" &&
        rt_rustic -r "$rt_fast_v" repair index >/dev/null 2>>"$rt_fast_dir/err" &&
        RESTIC_PASSWORD=backotter-compare $1 -r $2 --no-cache --no-lock cat blob $3 \
            >"$rt_fast_dir/root.restic" 2>>"$rt_fast_dir/err" </dev/null &&
        echo >>"$rt_fast_dir/root.restic" &&
        rt_rustic -r "$rt_fast_v" cat tree-blob $3 >"$rt_fast_dir/root.rustic" 2>>"$rt_fast_dir/err" &&
        cmp -s "$rt_fast_dir/root.restic" "$rt_fast_dir/root.rustic"; then
        rt_fast_repo=$2
        return 0
    fi
    rt_fast_close
    return 1
}

rt_blob() {  # RESTIC REPO ID: tree blob ID's bytes (the view's when REPO's is open, else restic's)
    if [ -n "$rt_fast_repo" ] && [ "x$rt_fast_repo" = "x$2" ] &&
        rt_rustic -r "$rt_fast_dir/view" cat tree-blob "$3" 2>/dev/null; then
        return 0
    fi
    RESTIC_PASSWORD=backotter-compare $1 -r $2 --no-cache --no-lock cat blob $3 </dev/null
}

rt_tree() {  # RESTIC REPO ID: the decoder's lines for one tree
    rt_blob $1 $2 $3 | $rt_awk -v keepcontent="${RT_CONTENT:-}" -f $rt_decoder
}

# A lane of a level: for each ID<TAB>PREFIX on stdin, "\001ID<TAB>PREFIX"
# and the tree's blob; a tree it can't read ends it, its ID in .unread.
rt_lane() {  # RESTIC REPO
    while IFS='	' read -r rt_lid rt_lpre; do
        printf '\001%s\t%s\n' "$rt_lid" "$rt_lpre"
        rt_blob "$1" "$2" "$rt_lid" || { echo "$rt_lid" >>$rt_tmp/bko-rt-nodes.unread; return 1; }
        echo
    done
}

rt_nodes() {  # RESTIC REPO PATH [PRUNE]
    rt_id=$(RESTIC_PASSWORD=backotter-compare $1 -r $2 --no-cache --no-lock snapshots --json latest |
        sed -n 's/.*"tree":"\([0-9a-f]*\)".*/\1/p' | head -n 1)
    [ -n "$rt_id" ] || { echo "rt_nodes: no snapshot in $2" >&2; return 1; }
    rt_fast_open "$1" "$2" "$rt_id"
    rt_walk "$@"
    rt_w_s=$?
    rt_fast_close
    return $rt_w_s
}

rt_walk() {  # RESTIC REPO PATH [PRUNE], rt_id the snapshot's tree: rt_nodes' listing
    rt_rest=${3#/} rt_self=""
    while [ -n "$rt_rest" ]; do
        rt_comp=${rt_rest%%/*}
        case $rt_rest in */*) rt_rest=${rt_rest#*/} ;; *) rt_rest="" ;; esac
        rt_self=$(rt_tree $1 $2 $rt_id | $rt_awk -F '\t' -v n="$rt_comp" '$1 == n')
        [ -n "$rt_self" ] || { echo "rt_nodes: no $rt_comp in $2's tree" >&2; return 1; }
        rt_id=$(printf '%s\n' "$rt_self" | cut -f 2)
    done
    printf '%s\n' "$rt_self" | $rt_awk -F '\t' 'BEGIN { OFS = "\t" } { $1 = "."; $2 = ""; sub(/\t\t/, "\t"); print }'
    # Breadth first, a level at a time: the level's trees (ID<TAB>PREFIX)
    # in .level, read in RT_JOBS lanes of consecutive lines, each lane's
    # trees decoded by an awk of its own into its own .out file and its own
    # .queue.N of the next level's trees; then the lanes' files, in order,
    # are the listing and the next level's .queue: what one awk over all the
    # lanes' trees in order printed (each tree is decoded by itself).
    rt_w_jobs=${RT_JOBS:-$(rt_cpus)}
    rm -f $rt_tmp/bko-rt-nodes.out.* $rt_tmp/bko-rt-nodes.queue.* $rt_tmp/bko-rt-nodes.bad.*
    printf '%s\t\n' "$rt_id" >$rt_tmp/bko-rt-nodes.queue
    while [ -s $rt_tmp/bko-rt-nodes.queue ]; do
        mv $rt_tmp/bko-rt-nodes.queue $rt_tmp/bko-rt-nodes.level
        rm -f $rt_tmp/bko-rt-nodes.unread
        rt_w_n=$(wc -l <$rt_tmp/bko-rt-nodes.level | tr -d ' ')
        rt_w_c=$(( (rt_w_n + rt_w_jobs - 1) / rt_w_jobs )) rt_w_j=0 rt_w_pids=""
        while [ $((rt_w_j * rt_w_c)) -lt "$rt_w_n" ]; do
            sed -n "$((rt_w_j * rt_w_c + 1)),$(((rt_w_j + 1) * rt_w_c))p" $rt_tmp/bko-rt-nodes.level |
                rt_lane $1 $2 | $rt_awk -v keepcontent="${RT_CONTENT:-}" -v walk=1 -v prune="${4:-}" \
                    -v queue=$rt_tmp/bko-rt-nodes.queue.$rt_w_j -v badfile=$rt_tmp/bko-rt-nodes.bad.$rt_w_j \
                    -f $rt_decoder >$rt_tmp/bko-rt-nodes.out.$rt_w_j &
            rt_w_pids="$rt_w_pids $!" rt_w_j=$((rt_w_j + 1))
        done
        rt_w_s=0
        for rt_w_p in $rt_w_pids; do wait $rt_w_p || rt_w_s=1; done
        rt_w_k=0
        : >$rt_tmp/bko-rt-nodes.queue
        while [ $rt_w_k -lt $rt_w_j ]; do
            cat $rt_tmp/bko-rt-nodes.out.$rt_w_k
            [ ! -f $rt_tmp/bko-rt-nodes.queue.$rt_w_k ] || cat $rt_tmp/bko-rt-nodes.queue.$rt_w_k >>$rt_tmp/bko-rt-nodes.queue
            [ ! -f $rt_tmp/bko-rt-nodes.bad.$rt_w_k ] || cat $rt_tmp/bko-rt-nodes.bad.$rt_w_k >>$rt_tmp/bko-rt-nodes.unread
            rm -f $rt_tmp/bko-rt-nodes.out.$rt_w_k $rt_tmp/bko-rt-nodes.queue.$rt_w_k $rt_tmp/bko-rt-nodes.bad.$rt_w_k
            rt_w_k=$((rt_w_k + 1))
        done
        if [ -s $rt_tmp/bko-rt-nodes.unread ] || [ $rt_w_s != 0 ]; then
            echo "rt_nodes: can't read tree $(head -n 1 $rt_tmp/bko-rt-nodes.unread 2>/dev/null)" >&2
            rm -f $rt_tmp/bko-rt-nodes.queue $rt_tmp/bko-rt-nodes.level $rt_tmp/bko-rt-nodes.unread
            return 1
        fi
    done
    rm -f $rt_tmp/bko-rt-nodes.queue $rt_tmp/bko-rt-nodes.level $rt_tmp/bko-rt-nodes.unread
}

# RT_JOBS' default: the CPUs online, at least 4 (the old default: a guest
# of 2 vCPUs keeps it) and at most 12 (chopin's threads).  Each lane is a
# rustic process at a time and its awk; the listing is the same for any.
rt_cpus() {
    rt_cpus_n=$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null)
    case $rt_cpus_n in '' | *[!0-9]*) rt_cpus_n=4 ;; esac
    [ "$rt_cpus_n" -ge 4 ] || rt_cpus_n=4
    [ "$rt_cpus_n" -le 12 ] || rt_cpus_n=12
    echo $rt_cpus_n
}

rt_path_nodes() {  # RESTIC REPO PATH
    rt_id=$(RESTIC_PASSWORD=backotter-compare $1 -r $2 --no-cache --no-lock snapshots --json latest |
        sed -n 's/.*"tree":"\([0-9a-f]*\)".*/\1/p' | head -n 1)
    [ -n "$rt_id" ] || { echo "rt_path_nodes: no snapshot in $2" >&2; return 1; }
    rt_rest=${3#/} rt_at=""
    while [ -n "$rt_rest" ]; do
        rt_comp=${rt_rest%%/*}
        case $rt_rest in */*) rt_rest=${rt_rest#*/} ;; *) rt_rest="" ;; esac
        rt_at=$rt_at/$rt_comp
        rt_self=$(rt_tree $1 $2 $rt_id | $rt_awk -F '\t' -v n="$rt_comp" '$1 == n')
        [ -n "$rt_self" ] || { echo "rt_path_nodes: no $rt_comp in $2's tree" >&2; return 1; }
        rt_id=$(printf '%s\n' "$rt_self" | cut -f 2)
        printf '%s\n' "$rt_self" | $rt_awk -F '\t' -v p="$rt_at" 'BEGIN { OFS = "\t" } { $1 = p; $2 = ""; sub(/\t\t/, "\t"); print }'
    done
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
    chmod 750 "$rt_dst" && touch "$rt_dst" || return 1
    rt_list_elsewhere >/tmp/bko-rt.elsewhere.before
}

rt_over_check() {
    rt_list_elsewhere >/tmp/bko-rt.elsewhere.after
    diff /tmp/bko-rt.elsewhere.before /tmp/bko-rt.elsewhere.after
}

rt_dot() {  # DIR
    if [ "$(uname -s)" = SunOS ]; then
        # GNU stat: /usr/gnu/bin's on OmniOS, /usr/bin's on OpenIndiana
        # (its gnu-coreutils puts stat there, illumos having none).
        rt_st=/usr/gnu/bin/stat; [ -x $rt_st ] || rt_st=/usr/bin/stat
        $rt_st -c 'mode %a owner %u:%g' "$1"
        $rt_st -c 'atime %.9X' "$1"
        $rt_st -c 'mtime %.9Y' "$1"
        /usr/bin/ls -dV "$1" | tail -n +2 | sed 's/^ */acl /'
        /usr/bin/ls -d/v "$1" | tail -n +2 | sed 's/^ */attrs /'
        # runat(1) runs its arguments as one shell command line.
        runat "$1" 'for a in *; do case $a in SUNWattr_r[ow]) ;; *) [ -f "$a" ] && echo "xattr $a $(digest -a md5 "$a")" ;; esac; done' 2>/dev/null
        return 0
    fi
    stat -f 'flags %Xf mode %Mp%Lp owner %u:%g' "$1"
    stat -f 'atime %Fa' "$1"
    stat -f 'mtime %Fm' "$1"
    # OpenBSD and DragonFly have no ACLs or extended attributes (nor the
    # tools): nothing to read, so no lines.  Elsewhere (FreeBSD, NetBSD) a
    # missing tool is a "missing-tool" line, which rt_dot_check fails.
    case $(uname -s) in OpenBSD|DragonFly) rt_noea=1 ;; *) rt_noea="" ;; esac
    rt_st=0
    # Where there are none, the same message both times.
    if command -v getfacl >/dev/null 2>&1; then
        getfacl -n "$1" 2>&1 | sed 's/^/acl /'
        getfacl -n -d "$1" 2>&1 | sed 's/^/default acl /'
    elif [ -z "$rt_noea" ]; then
        echo "missing-tool getfacl"; echo "rt_dot: no getfacl on $(uname -s)" >&2; rt_st=1
    fi
    if command -v lsextattr >/dev/null 2>&1; then
        for rt_ns in user system; do
            for rt_x in $(lsextattr -q $rt_ns "$1" 2>/dev/null | tr ' \t' '\n\n' | sort); do
                echo "xattr $rt_ns $rt_x $(getextattr -q -x $rt_ns "$rt_x" "$1" | md5)"
            done
        done
    elif [ -z "$rt_noea" ]; then
        echo "missing-tool lsextattr"; echo "rt_dot: no lsextattr on $(uname -s)" >&2; rt_st=1
    fi
    return $rt_st
}

rt_dot_check() {  # BEFORE AFTER [forward|forward-mtime]
    [ -s "$1" ] && [ -s "$2" ] || { echo "  no record of the target's own metadata"; return 1; }
    rt_s=0
    # Without it (a stat format refused), the diff below would compare the
    # rest alone.  BSD: "flags HEX mode OCTAL owner UID:GID"; illumos: no
    # flags.
    for rt_f in "$1" "$2"; do
        grep -E -q '^(flags [0-9a-fA-F]+ )?mode [0-7]+ owner [0-9]+:[0-9]+$' "$rt_f" ||
            { echo "  no flags, mode and owner recorded in $rt_f"; rt_s=1; }
    done
    # A tool rt_dot needed and lacked: the same nothing on both sides isn't a match.
    if grep -q '^missing-tool ' "$1" "$2"; then
        echo "  $(grep -h '^missing-tool ' "$1" "$2" | sort -u | tr '\n' ' ')(ACLs or extended attributes not read)"; rt_s=1
    fi
    grep -v -e '^atime ' -e '^mtime ' "$1" >$rt_tmp/bko-rt.dot.1
    grep -v -e '^atime ' -e '^mtime ' "$2" >$rt_tmp/bko-rt.dot.2
    diff $rt_tmp/bko-rt.dot.1 $rt_tmp/bko-rt.dot.2 || rt_s=1
    for rt_k in atime mtime; do
        rt_a=$(sed -n "s/^$rt_k //p" "$1") rt_b=$(sed -n "s/^$rt_k //p" "$2")
        [ -n "$rt_a" ] || { echo "  no $rt_k recorded"; rt_s=1; continue; }
        [ "$rt_a" = "$rt_b" ] && continue
        case ${3:-},$rt_k in forward,*|forward-mtime,mtime) rt_fw=1 ;; *) rt_fw="" ;; esac
        if [ -n "$rt_fw" ] && $rt_awk -v a="$rt_a" -v b="$rt_b" 'BEGIN {
                split(a, x, "."); split(b, y, ".")
                exit !(y[1] + 0 > x[1] + 0 || (y[1] + 0 == x[1] + 0 && y[2] + 0 >= x[2] + 0)) }'; then
            echo "  (not a check) $rt_k $rt_a, now $rt_b: moved forward"
        else
            echo "  $rt_k $rt_a, now $rt_b"; rt_s=1
        fi
    done
    return $rt_s
}

rt_nodot() {
    $rt_awk '
        /^# file: / { facl = ($0 == "# file: .") }
        /^$/ || /^getfacl:/ { facl = 0 }
        dot && /^[ \t]/ { next }
        { dot = ($1 == "." || $1 == ".:" || (($1 == "user" || $1 == "system") && $2 == ".")) }
        !dot && !facl'
}

rt_nodot_diff() {  # A B
    rt_nodot <"$1" >$rt_tmp/bko-rt.nodot.1
    rt_nodot <"$2" >$rt_tmp/bko-rt.nodot.2
    diff $rt_tmp/bko-rt.nodot.1 $rt_tmp/bko-rt.nodot.2
}

rt_nodot_run() {  # COMMAND...
    "$@" >$rt_tmp/bko-rt.nodot.out 2>&1; rt_s=$?
    rt_nodot <$rt_tmp/bko-rt.nodot.out >$rt_tmp/bko-rt.nodot.rest
    cat $rt_tmp/bko-rt.nodot.rest
    [ $rt_s -eq 0 ] || [ ! -s $rt_tmp/bko-rt.nodot.rest ]
}

rt_mksock() {  # PATH
    rm -f "$1"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$1" && return
    fi
    if command -v perl >/dev/null 2>&1; then
        perl -MSocket -e 'my $s; socket($s, PF_UNIX, SOCK_STREAM, 0) && bind($s, sockaddr_un($ARGV[0])) or die "$ARGV[0]: $!\n"' "$1" &&
            return
    fi
    if [ ! -x $rt_tmp/bko-rt-mksock ] && command -v cc >/dev/null 2>&1; then
        cat >$rt_tmp/bko-rt-mksock.c <<'C'
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {  /* PATH */
    struct sockaddr_un a;
    int s;

    if (argc != 2 || strlen(argv[1]) >= sizeof(a.sun_path))
        return 2;
    memset(&a, 0, sizeof(a));
    a.sun_family = AF_UNIX;
    strcpy(a.sun_path, argv[1]);
    if ((s = socket(AF_UNIX, SOCK_STREAM, 0)) < 0 || bind(s, (struct sockaddr *)&a, sizeof(a)) != 0) {
        perror(argv[1]);
        return 1;
    }
    close(s);
    return 0;
}
C
        rt_l=""; [ "$(uname -s)" = SunOS ] && rt_l=-lsocket
        cc -o $rt_tmp/bko-rt-mksock $rt_tmp/bko-rt-mksock.c $rt_l 2>/dev/null
    fi
    [ -x $rt_tmp/bko-rt-mksock ] && { $rt_tmp/bko-rt-mksock "$1" && return; }
    if command -v nc >/dev/null 2>&1; then
        nc -lU "$1" </dev/null >/dev/null 2>&1 &
        rt_n=$!
        for rt_i in 1 2 3 4 5 6 7 8 9 10; do [ -S "$1" ] && break; sleep 1; done
        kill $rt_n 2>/dev/null; wait $rt_n 2>/dev/null
        [ -S "$1" ] && return
    fi
    echo "rt_mksock: no python3, perl, cc or nc -lU made a socket at $1" >&2
    return 1
}

