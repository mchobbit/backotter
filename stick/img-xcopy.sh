#!/usr/bin/env bash
# img-xcopy.sh DEST P...: push images/P (top level and test/, not prev-*) to
# DEST's images/P; DEST's old contents go to prev-xSTAMP, its copy of the new
# image is compressed there, under a reservation.  DEST's done.exit is copied
# last, so that until then DEST's pin refuses the platform as a build under way.
#
# BKO_WORK and BKO_IMG_DIR name this host's images directory; BKO_DEST_IMAGES
# names DEST's (default backotter-work/images: relative to DEST's home, or
# absolute).  From a test (T2_HOST_ROOT not the default) or from any images
# directory but the real one, BKO_DEST_IMAGES is required, so that a world's
# build can't replace DEST's real images.
#
# Nothing moves unless this host's build has ended (its done.exit is there, its
# images/.lock-P free); a shared lock on that keeps a build from starting here
# during the copy.  On DEST the move and each rsync hold images/.lock-P, and
# are refused while a build there does.  Exit 0, or the count of platforms that
# failed; 2 on bad usage or a refusal.
set -u
[[ $# -ge 2 ]] || { sed -n '2,/^set -u/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
D=$1; shift
W=${BKO_WORK:-$HOME/backotter-work}
I=${BKO_IMG_DIR:-$W/images}
DI=${BKO_DEST_IMAGES:-backotter-work/images}
same_dir() { local a b; a=$(stat -Lc %d:%i -- "$1" 2>/dev/null) && b=$(stat -Lc %d:%i -- "$2" 2>/dev/null) && [[ $a == "$b" ]]; }
if [[ -z ${BKO_DEST_IMAGES:-} ]] &&
   { [[ ${T2_HOST_ROOT:-/bkoscratch/test2} != /bkoscratch/test2 ]] || ! same_dir "$I" /home/m/backotter-work/images; }; then
    echo "img-xcopy: a test or a world ($I) copies to DEST's real images unless BKO_DEST_IMAGES says where" >&2; exit 2
fi
st=$(date -u +%Y%m%d-%H%M%S)
bad=0
ssh_d() { ssh -n -o BatchMode=yes "$D" "$@"; }
for p in "$@"; do
    if [[ ! -f $I/$p/done.exit ]]; then
        echo "$p: not copied: no $I/$p/done.exit (a build under way, or stopped)" >&2; bad=$((bad + 1)); continue
    fi
    exec {lk}>>"$I/.lock-$p" || { echo "$p: not copied: can't open $I/.lock-$p" >&2; bad=$((bad + 1)); continue; }
    if ! flock -n -s "$lk"; then
        echo "$p: not copied: a build of it is under way here" >&2; bad=$((bad + 1)); exec {lk}>&-; continue
    fi
    # (flock -n in --rsync-path: the rsync on DEST runs under its platform's lock, or not at all.)
    if ssh_d "cd $DI && mkdir -p $p && flock -n .lock-$p sh -c 'cd $p && mkdir prev-x$st && find . -maxdepth 1 -mindepth 1 ! -name \"prev-*\" -exec mv -t prev-x$st/ {} +'" &&
       rsync -a --rsync-path="flock -n $DI/.lock-$p rsync" --exclude 'prev-*' --exclude done.exit "$I/$p/" "$D:$DI/$p/" &&
       rsync -a --rsync-path="flock -n $DI/.lock-$p rsync" "$I/$p/done.exit" "$D:$DI/$p/done.exit"; then
        echo "$p copied"
        if ssh_d "d=\$(cd $DI && pwd) && /home/m/backotter-work/cmp-run.sh --reserve 2 6 /home/m/backotter-work/stick/compress-images.sh -p $p \"\$d\"" >/dev/null; then
            echo "$p compressed there"
        else
            echo "$p: not compressed there" >&2; bad=$((bad + 1))
        fi
    else
        echo "$p: not copied" >&2; bad=$((bad + 1))
    fi
    exec {lk}>&-
done
exit "$bad"
