#!/usr/bin/env bash
# img-seed-host.sh: img-seed.sh's half on a host (docs/design/img-speed.md §5.1, D5).
# Run by stick/img_seed.py over ssh as `bash -c "$(cat this file)" img-seed-host CMD ARGS`, so
# that a host needs no synced copy of it; it also runs here directly (the t2 tests).
#
#   facts                    what the host holds: tab-separated lines, said below
#   server                   make IB (this user's), IB/server (root's, 0755) and
#                            WORK/server -> IB/server
#   pull SRC NAME...         copy IB/server/NAME (slim-YYYYMMDD, the ISO) from host SRC (its
#                            ssh destination) over ssh, as root (tar --numeric-owner --xattrs
#                            --acls), through IB/server/.seed-part; a NAME already here is
#                            left as it is (img-seed.sh's digest check judges it)
#   repoint NAME             IB/server/slim -> NAME, by one rename
#   digest NAME ISO          img_inputs.py digest linux with that slim and ISO (JSON)
#   tools                    the host tools' dpkg versions, sudo -n, HTTPS to the archives
#   media SRC P...           img-media/P dangles into IB/img-media: make its directory and
#                            fill it with SRC's media of P but MEDIA (a host's own fetch
#                            writes that), through a .seed-part directory
#   builder-stat             the FreeBSD builder's files: NAME SIZE MTIME, sorted (on SRC)
#   builder SRC              vms/freebsd15-builder dangles into IB/vms (or is absent): copy
#                            SRC's folder under SRC's .vm-update.lock (as vm-update and the
#                            builds take it), refused while a process has its disk open
#   builder-drop             move a builder copy that failed its check aside, to
#                            IB/vms/.seed-bad-freebsd15-builder (never deleted here)
#   builder-hash             NAME SHA256 for each of the builder's files, sorted
#
# Paths: IB is /bkoscratch/imgbuild, WORK ~/backotter-work.  For tests: BKO_SEED_ROOT
# prefixes every absolute path, BKO_SEED_WORK replaces WORK, BKO_SEED_SUDO replaces
# `sudo -n` (empty: none), BKO_SEED_CURL replaces curl, BKO_SEED_SPARSE replaces tar's
# --sparse, BKO_SEED_SSH ssh, and BKO_SEED_MARGIN_KIB the room a builder copy
# leaves free (default 20 GiB, over a tenth of the copy).  They are read on each host (the nested ssh too), and
# a real ssh carries none of them.
#
# facts lines: server_dir yes|no; server_link TARGET|-; server_ok yes|no (it is a link to IB/server); slim TARGET|-; slims NAME...;
# isos NAME...; source_iso PATH|- (slim's report.txt "Source ISO:"); media P KIND TARGET;
# builder KIND TARGET (KIND: dir, link, dangling, other, none); free_kib N (IB's file system).
# Exit: 0; 1 a step failed (said on stderr); 2 bad usage.
set -euo pipefail

R=${BKO_SEED_ROOT-}
W=${BKO_SEED_WORK:-$HOME/backotter-work}
IB=$R/bkoscratch/imgbuild
SRV=$IB/server
read -r -a SUDO <<<"${BKO_SEED_SUDO-sudo -n}"
read -r -a CURL <<<"${BKO_SEED_CURL-curl}"
read -r -a SSH <<<"${BKO_SEED_SSH-ssh -T -o BatchMode=yes -o ConnectTimeout=10}"
SPARSE=${BKO_SEED_SPARSE---sparse}
TARX=(--numeric-owner --xattrs --acls)
BUILDER=freebsd15-builder

die() { echo "img-seed-host: $*" >&2; exit 1; }
sudo_() { ${SUDO[@]+"${SUDO[@]}"} "$@"; }

# KIND TARGET of a path: dir, link (to something that exists), dangling, other, none.
kind() {
    local p=$1
    if [[ -L $p ]]; then
        if [[ -e $p ]]; then printf 'link\t%s\n' "$(readlink "$p")"; else printf 'dangling\t%s\n' "$(readlink "$p")"; fi
    elif [[ -d $p ]]; then printf 'dir\t-\n'
    elif [[ -e $p ]]; then printf 'other\t-\n'
    else printf 'none\t-\n'; fi
}

# The nested ssh's command: sets the same R, WORK and sudo on the source as here.
src_prelude='set -euo pipefail; R=${BKO_SEED_ROOT-}; W=${BKO_SEED_WORK:-$HOME/backotter-work}; read -r -a SUDO <<<"${BKO_SEED_SUDO-sudo -n}"; IB=$R/bkoscratch/imgbuild'

facts() {
    local d n
    printf 'server_dir\t%s\n' "$([[ -d $SRV ]] && echo yes || echo no)"
    printf 'server_link\t%s\n' "$( [[ -L $W/server ]] && readlink "$W/server" || { [[ -e $W/server ]] && echo other || echo -; })"
    printf 'server_ok\t%s\n' "$( [[ -L $W/server && $(readlink "$W/server") == "$SRV" ]] && echo yes || echo no)"
    printf 'slim\t%s\n' "$( [[ -L $SRV/slim ]] && readlink "$SRV/slim" || echo -)"
    local slims=() isos=()
    if [[ -d $SRV ]]; then
        for d in "$SRV"/slim-*; do [[ -d $d && ! -L $d ]] && slims+=("${d##*/}"); done
        for d in "$SRV"/*.iso; do [[ -f $d ]] && isos+=("${d##*/}"); done
    fi
    printf 'slims\t%s\n' "${slims[*]-}"
    printf 'isos\t%s\n' "${isos[*]-}"
    printf 'source_iso\t%s\n' "$(sed -n 's/^Source ISO: *//p' "$SRV/slim/report.txt" 2>/dev/null | head -n 1 | grep . || echo -)"
    if [[ -d $W/img-media ]]; then
        for d in "$W"/img-media/*; do
            [[ -e $d || -L $d ]] || continue
            n=${d##*/}
            printf 'media\t%s\t%s\n' "$n" "$(kind "$d")"
        done
    fi
    printf 'builder\t%s\n' "$(kind "$W/vms/$BUILDER")"
    d=$R/bkoscratch; [[ -d $IB ]] && d=$IB
    printf 'free_kib\t%s\n' "$(df -Pk "$d" | awk 'NR == 2 { print $4 }')"
}

server() {
    # IB is this user's (as Chopin's is), so that img-media/ and vms/ can be made in it.
    [[ -d $IB ]] || mkdir -p "$IB" || die "can't make $IB"
    [[ -w $IB ]] || sudo_ chown "$(id -u):$(id -g)" "$IB" || die "$IB isn't writable"
    [[ -d $SRV ]] || { sudo_ mkdir -p "$SRV" && sudo_ chmod 755 "$SRV"; } || die "can't make $SRV"
    if [[ -L $W/server ]]; then
        [[ $(readlink "$W/server") == "$SRV" ]] || die "$W/server is a link to $(readlink "$W/server"), not $SRV"
    elif [[ -e $W/server ]]; then
        die "$W/server is not a link: leaving it"
    else
        ln -s "$SRV" "$W/server"
    fi
}

pull() {
    local src=$1 n part=$SRV/.seed-part
    shift
    [[ -d $SRV ]] || die "no $SRV (run server first)"
    for n in "$@"; do
        [[ $n == */* || $n == .* || -z $n ]] && die "bad name $n"
        [[ -e $SRV/$n ]] && { echo "pull: $n is here already"; continue; }
        sudo_ rm -rf "$part" && sudo_ mkdir -p "$part"
        echo "pull: $n from $src"
        # shellcheck disable=SC2016
        "${SSH[@]}" "$src" "$src_prelude"'; cd "$IB/server" && ${SUDO[@]+"${SUDO[@]}"} tar -c --numeric-owner --xattrs --acls -- '"$(printf %q "$n")" </dev/null |
            sudo_ tar -C "$part" -x "${TARX[@]}" || { sudo_ rm -rf "$part"; die "copying $n from $src failed"; }
        [[ -e $part/$n ]] || { sudo_ rm -rf "$part"; die "$src sent no $n"; }
        sudo_ mv "$part/$n" "$SRV/$n" && sudo_ rm -rf "$part"
    done
}

repoint() {
    local n=$1
    [[ $n == slim-* && $n != */* && -d $SRV/$n ]] || die "no $SRV/$n to point slim at"
    sudo_ python3 -c 'import os, sys
t, link = sys.argv[1], sys.argv[2]
tmp = link + ".seed-new"
if os.path.lexists(tmp):
    os.remove(tmp)
os.symlink(t, tmp)
os.replace(tmp, link)' "$n" "$SRV/slim"
    echo "repoint: slim -> $n"
}

digest() {
    local n=$1 iso=$2
    [[ -d $SRV/$n ]] || die "no $SRV/$n"
    [[ -f $SRV/$iso ]] || die "no $SRV/$iso"
    BKO_WORK=$W S=$W/stick SL=$SRV/$n ISO=$SRV/$iso python3 -B "$W/stick/img_inputs.py" digest linux
}

tools() {
    local p v u
    for p in squashfs-tools xorriso dosfstools mtools; do
        if [[ -n ${BKO_IMG_HOST_TOOLS:-} ]]; then
            v=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1]).get(sys.argv[2]) or "-")' "$BKO_IMG_HOST_TOOLS" "$p")
        else
            v=$(dpkg-query -W -f '${Version}' "$p" 2>/dev/null) || v=-
        fi
        printf 'tool\t%s\t%s\n' "$p" "${v:--}"
    done
    printf 'sudo\t%s\n' "$(sudo_ true 2>/dev/null && echo yes || echo no)"
    for u in archive.ubuntu.com security.ubuntu.com apt.bcachefs.org; do
        printf 'https\t%s\t%s\n' "$u" "$("${CURL[@]}" -sS -o /dev/null --max-time 20 "https://$u/" 2>/dev/null && echo yes || echo no)"
    done
}

media() {
    local src=$1 p t part
    shift
    for p in "$@"; do
        [[ $p == */* || -z $p ]] && die "bad platform $p"
        [[ -L $W/img-media/$p && ! -e $W/img-media/$p ]] || { echo "media: $p doesn't dangle: leaving it"; continue; }
        t=$(readlink "$W/img-media/$p")
        [[ $t == "$IB/img-media/$p" ]] || die "img-media/$p points at $t, not $IB/img-media/$p: leaving it"
        mkdir -p "$IB/img-media"
        part=$IB/img-media/.seed-part-$p
        rm -rf "$part" && mkdir -p "$part"
        echo "media: $p from $src"
        # shellcheck disable=SC2016
        "${SSH[@]}" "$src" "$src_prelude"'; cd "$W/img-media/'"$p"'/" && tar -c --exclude=./MEDIA --exclude=MEDIA .' </dev/null |
            tar -C "$part" -x || { rm -rf "$part"; die "copying $p's media from $src failed"; }
        rm -f "$part/MEDIA"
        mv "$part" "$t"
    done
}

# The builder's files, NAME SIZE MTIME (in the folder the link reaches).
builder_stat() {
    cd "$W/vms/$BUILDER/" && python3 -c 'import os
for n in sorted(os.listdir(".")):
    if os.path.isfile(n) and not os.path.islink(n):
        st = os.stat(n)
        print(n, st.st_size, st.st_mtime_ns)'
}

builder_hash() {
    cd "$W/vms/$BUILDER/" && python3 -c 'import hashlib, os
for n in sorted(os.listdir(".")):
    if os.path.isfile(n) and not os.path.islink(n):
        h = hashlib.sha256()
        with open(n, "rb") as f:
            for b in iter(lambda: f.read(1 << 22), b""):
                h.update(b)
        print(n, h.hexdigest())'
}

builder() {
    local src=$1 k t part need free
    k=$(kind "$W/vms/$BUILDER" | cut -f1)
    t=$IB/vms/$BUILDER
    case $k in
    dangling) [[ $(readlink "$W/vms/$BUILDER") == "$t" ]] ||
                  die "vms/$BUILDER points at $(readlink "$W/vms/$BUILDER"), not $t: leaving it" ;;
    none) ;;
    *) echo "builder: vms/$BUILDER is a $k: leaving it"; return 0 ;;
    esac
    [[ ! -e $t ]] || die "$t is here but vms/$BUILDER doesn't reach it: leaving both"
    # shellcheck disable=SC2016
    need=$("${SSH[@]}" "$src" "$src_prelude"'; du -sk "$W/vms/'"$BUILDER"'/" | cut -f1' </dev/null) || die "can't size $src's builder"
    free=$(df -Pk "$R/bkoscratch" | awk 'NR == 2 { print $4 }')
    (( free > need + need / 10 + ${BKO_SEED_MARGIN_KIB:-20971520} )) ||
        die "$((need / 1048576)) GiB to copy, $((free / 1048576)) GiB free: not enough room"
    mkdir -p "$IB/vms"
    part=$IB/vms/.seed-part-$BUILDER
    rm -rf "$part" && mkdir -p "$part"
    echo "builder: $BUILDER from $src ($((need / 1048576)) GiB), under its .vm-update.lock there"
    # The source side: under the guest's lock, refused while any process has a file of it
    # open (a running builder), then one tar.  Exit 5 there: in use.
    # shellcheck disable=SC2016
    "${SSH[@]}" "$src" "$src_prelude"'; cd "$W/vms/'"$BUILDER"'/"
        exec 9>>.vm-update.lock
        flock -w 60 9 || { echo "img-seed-host: '"$BUILDER"' is locked (a build or vm-update)" >&2; exit 5; }
        d=$(pwd -P)
        for f in /proc/[0-9]*/fd/*; do
            case $(readlink "$f" 2>/dev/null) in "$d"/*.qcow2) echo "img-seed-host: '"$BUILDER"' is in use (${f#/proc/})" >&2; exit 5 ;; esac
        done
        tar -c '"$SPARSE"' -f - .' </dev/null |
        tar -C "$part" -x || { rm -rf "$part"; die "copying $src's $BUILDER failed"; }
    [[ -n $(ls -A "$part") ]] || { rm -rf "$part"; die "$src sent an empty $BUILDER"; }
    mv "$part" "$t"
    [[ -L $W/vms/$BUILDER ]] || ln -s "$t" "$W/vms/$BUILDER"
    echo "builder: copied to $t"
}

[[ $# -ge 1 ]] || { echo "usage: img-seed-host.sh CMD ARGS (see its header)" >&2; exit 2; }
cmd=$1; shift
case $cmd in
facts) facts ;;
server) server ;;
pull) [[ $# -ge 2 ]] || exit 2; pull "$@" ;;
repoint) [[ $# -eq 1 ]] || exit 2; repoint "$1" ;;
digest) [[ $# -eq 2 ]] || exit 2; digest "$1" "$2" ;;
tools) tools ;;
media) [[ $# -ge 2 ]] || exit 2; media "$@" ;;
builder-stat) builder_stat ;;
builder) [[ $# -eq 1 ]] || exit 2; builder "$1" ;;
builder-hash) builder_hash ;;
builder-drop)
    [[ -d $IB/vms/$BUILDER ]] || exit 0
    [[ ! -e $IB/vms/.seed-bad-$BUILDER ]] || die "an older $IB/vms/.seed-bad-$BUILDER is in the way"
    mv "$IB/vms/$BUILDER" "$IB/vms/.seed-bad-$BUILDER" ;;
*) echo "img-seed-host: no command $cmd" >&2; exit 2 ;;
esac
