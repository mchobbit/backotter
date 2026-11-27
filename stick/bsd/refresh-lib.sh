# stick/bsd/refresh-lib.sh: what every VM platform's `build.sh refresh` shares
# (docs/design/img-speed.md §4.2, D1), in POSIX sh.  make-img-bsd.sh
# --refresh-from puts it in in/ beside the new binaries, the new
# backotter.json and the parent image; build.sh refresh sources it once it has
# attached the parent and mounted the file system that holds the binaries
# read-write (the attach, fsck and the id sites are the platform's own).
#
#   bko_fit ROOT BINDIR FREE_KIB NAME...
#       Whether in/NAME... fit in ROOT/BINDIR, where FREE_KIB is the file
#       system's free space (its size less what is used: root writes past
#       minfree) and the files they replace give their blocks back.  Says
#       what is short and returns BKO_NO_ROOM when they don't: the caller
#       exits with it, and make-img-bsd.sh with 6, so that the planner gives
#       the platform a full build (§4.2 step 4).  The same sum as
#       img_round.py's fits().
#   bko_swap ROOT BINDIR NAME...
#       Each in/NAME into ROOT/BINDIR/NAME, and in/backotter.json into
#       ROOT/.disk/backotter.json; a NAME the parent has and in/ hasn't is
#       removed, as a full build installs only what it has.  A file that is
#       there keeps its inode (owner, mode, flags), its bytes rewritten in
#       place; a new one is 0555 (the marker 0444), as a full build installs
#       it, or BKO_BIN_MODE where the platform's full build installs another
#       mode.  Each is then compared with its source.
#   bko_room ROOT BINDIR FREE_KIB TOTAL_KIB NAME...
#       The line make-img-bsd.sh records (provenance's room): BKO-ROOM
#       free_kib=N total_kib=N bins_kib=N, BINS_KIB the binaries' blocks now.

BKO_NO_ROOM=3

# bko_kib FILE: FILE's size in KiB, rounded up.
bko_kib() {
    _s=$(wc -c <"$1" | tr -d ' ')
    echo $(( (_s + 1023) / 1024 ))
}

# bko_du FILE: the KiB FILE's blocks take (0 if there is none).
bko_du() {
    if [ -f "$1" ]; then du -k "$1" | awk '{ print $1; exit }'; else echo 0; fi
}

bko_fit() {
    _root=$1 _bin=$2 _free=$3
    shift 3
    _need=0 _have=$_free
    for _n in "$@" .disk/backotter.json; do
        case $_n in .disk/*) _src=$in/backotter.json _dst=$_root/$_n ;; *) _src=$in/$_n _dst=$_root$_bin/$_n ;; esac
        if [ -f "$_src" ]; then
            # Its blocks, 2 % for the indirect blocks and 256 KiB for the fragments.
            _k=$(bko_kib "$_src")
            _need=$((_need + _k + _k / 50 + 256))
        fi
        _have=$((_have + $(bko_du "$_dst")))
    done
    if [ "$_need" -gt "$_have" ]; then
        echo "refresh: the new binaries need $_need KiB, and the parent's file system has $_have" \
            "($_free free and what they replace): build it in full" >&2
        return $BKO_NO_ROOM
    fi
    echo "refresh: the new binaries need $_need KiB of the $_have there is"
}

# bko_put SRC DST MODE: SRC's bytes into DST.
bko_put() {
    if [ -f "$2" ]; then
        cat "$1" >"$2" || return 1
    else
        cp "$1" "$2" && chmod "$3" "$2" || return 1
    fi
    cmp -s "$1" "$2" || { echo "refresh: $2 is not $1 after the copy" >&2; return 1; }
}

bko_swap() {
    _root=$1 _bin=$2
    shift 2
    [ -d "$_root$_bin" ] || { echo "refresh: no $_bin in the parent" >&2; return 1; }
    [ -f "$_root/.disk/backotter.json" ] || { echo "refresh: no /.disk/backotter.json in the parent" >&2; return 1; }
    for _n in "$@"; do
        if [ -f "$in/$_n" ]; then
            bko_put "$in/$_n" "$_root$_bin/$_n" "${BKO_BIN_MODE:-555}" || return 1
            echo "refresh: $_bin/$_n replaced"
        elif [ -e "$_root$_bin/$_n" ]; then
            rm -f "$_root$_bin/$_n" || return 1
            echo "refresh: $_bin/$_n removed (this set has none)"
        fi
    done
    bko_put "$in/backotter.json" "$_root/.disk/backotter.json" 444 || return 1
    echo "refresh: /.disk/backotter.json replaced"
    sync
}

bko_room() {
    _root=$1 _bin=$2 _free=$3 _total=$4
    shift 4
    _b=0
    for _n in "$@"; do _b=$((_b + $(bko_du "$_root$_bin/$_n"))); done
    echo "BKO-ROOM free_kib=$_free total_kib=$_total bins_kib=$_b"
}
