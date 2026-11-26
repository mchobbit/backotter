#!/usr/bin/env bash
# img-xcopy.sh DEST P...: push images/P (top level and test/, not prev-*) to
# DEST's images/P; DEST's old contents go to prev-xSTAMP.  DEST's done.exit is
# copied last, so that until then DEST's pin refuses the platform as a build
# under way.  The images stay raw there (img-speed.md §7; BKO_IMG_COMPRESS=1
# compresses DEST's copy, under a reservation, as before).  Once a platform
# whose done.exit is 0 is in, DEST's images/P keeps its last two prev-* sets
# (stick/img-retain.sh, D6, from DEST's stick/: BKO_DEST_STICK, default
# /home/m/backotter-work/stick); BKO_IMG_RETAIN=0 skips it.
#
# DEST is an ssh target (m@cinqcent.local) or a farm host's name (cinqcent, or
# cinqcent.local), which testvm/hosts.json's "ssh" turns into its target.  DEST
# is tried once before the first copy: a DEST ssh can't reach is a refusal that
# says how to name it.
#
# Before done.exit, DEST's holt-bin/P gets the bko-holt and COMMIT the image
# was built with (bko-bin.sha256's ./bko-holt line, bko-holt.commit), so that
# DEST's pin finds them (img-refresh-holt.sh's job, which needs HoltTest2's
# build there): taken from this host's holt-bin/P/bko-holt, refused when that
# is no longer the image's, copied to DEST by temp name, checked there by its
# sha256 and renamed, COMMIT gone in between; then DEST's holt-bin/SHA256SUMS
# is written anew by temp name and rename, as img-refresh-holt.sh writes it.
# There it holds images/.lock-P and holt-bin's flock, as that script does.  A
# platform whose holt-bin wasn't brought along still gets its done.exit (its
# pin then writes bins-stale, and says what it wanted), but counts as failed.
#
# BKO_WORK and BKO_IMG_DIR name this host's images directory, BKO_HOLT_BIN its
# holt-bin (default BKO_WORK/holt-bin); BKO_DEST_IMAGES names DEST's (default
# backotter-work/images: relative to DEST's home, or absolute), and
# BKO_DEST_HOLT_BIN DEST's holt-bin (default holt-bin beside DEST's images).
# From a test (T2_HOST_ROOT not the default) or from any images directory but
# the real one, BKO_DEST_IMAGES is required, so that a world's build can't
# replace DEST's real images or holt-bin.
#
# Nothing moves unless this host's build has ended (its done.exit is there, its
# images/.lock-P free); a shared lock on that keeps a build from starting here
# during the copy.  On DEST the move and each rsync hold images/.lock-P, and
# are refused while a build there does.  Exit 0, or the count of platforms that
# failed; 2 on bad usage or a refusal.
set -u
[[ $# -ge 2 ]] || { sed -n '2,/^set -u/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
D=$1; shift
here=$(cd "$(dirname "$0")" && pwd)
W=${BKO_WORK:-$HOME/backotter-work}
I=${BKO_IMG_DIR:-$W/images}
H=${BKO_HOLT_BIN:-$W/holt-bin}
DI=${BKO_DEST_IMAGES:-backotter-work/images}
DH=${BKO_DEST_HOLT_BIN:-$(dirname "$DI")/holt-bin}
DS=${BKO_DEST_STICK:-/home/m/backotter-work/stick}
same_dir() { local a b; a=$(stat -Lc %d:%i -- "$1" 2>/dev/null) && b=$(stat -Lc %d:%i -- "$2" 2>/dev/null) && [[ $a == "$b" ]]; }
if [[ -z ${BKO_DEST_IMAGES:-} ]] &&
   { [[ ${T2_HOST_ROOT:-/bkoscratch/test2} != /bkoscratch/test2 ]] || ! same_dir "$I" /home/m/backotter-work/images; }; then
    echo "img-xcopy: a test or a world ($I) copies to DEST's real images unless BKO_DEST_IMAGES says where" >&2; exit 2
fi
# A farm host's name: its row's ssh target.
hosts_sh=$here/../testvm/hosts.sh
if [[ $D != *[@:/]* && -f $hosts_sh ]] && . "$hosts_sh" && t=$(hosts_get "$D" ssh 2>/dev/null) && [[ -n $t ]]; then
    echo "img-xcopy: $D is $t (testvm/hosts.json)"; D=$t
fi
st=$(date -u +%Y%m%d-%H%M%S)
bad=0
ssh_d() { ssh -n -o BatchMode=yes "$D" "$@"; }
reached=""
reach() {
    [[ -z $reached ]] || return 0
    if ! ssh_d true; then
        local names
        names=$( { declare -F hosts_phrase >/dev/null || . "$hosts_sh"; } 2>/dev/null && hosts_phrase 2>/dev/null)
        echo "img-xcopy: ssh can't reach '$D': name a farm host${names:+ ($names)}, which testvm/hosts.json turns into its ssh target, or that target itself (m@cinqcent.local)" >&2
        exit 2
    fi
    reached=1
}
# DEST's holt-bin/P: the bko-holt and COMMIT the image P was built with.  0 and
# what it is now, or 1 and why not.
holt_along() {
    local p=$1 want c got tmp
    want=$(awk '$2 == "./bko-holt" { print $1 }' "$I/$p/bko-bin.sha256" 2>/dev/null)
    c=$(head -n 1 "$I/$p/bko-holt.commit" 2>/dev/null)
    [[ $want =~ ^[0-9a-f]{64}$ ]] || { echo "no ./bko-holt line in $I/$p/bko-bin.sha256"; return 1; }
    [[ $c =~ ^[0-9a-f]{40}$ ]] || { echo "no bko-holt.commit beside the image (ImgTest2 refuses it anyway: rebuild it)"; return 1; }
    got=$(sha256sum "$H/$p/bko-holt" 2>/dev/null | cut -d' ' -f1)
    [[ $got == "$want" ]] ||
        { echo "this host's $H/$p/bko-holt is '${got:0:12}', the image was built with ${want:0:12} (commit ${c:0:7}): rebuild the image, or bring DEST's holt-bin/$p to that build (stick/img-refresh-holt.sh $c CODE $p there)"; return 1; }
    tmp=.bko-holt.xcopy.$st.$$
    rsync -a --rsync-path="mkdir -p $DH/$p && flock -n $DI/.lock-$p rsync" "$H/$p/bko-holt" "$D:$DH/$p/$tmp" ||
        { echo "the rsync of $H/$p/bko-holt failed"; return 1; }
    # (Arguments are words: the paths have no spaces, as DI's never had.)
    ssh -o BatchMode=yes "$D" bash -s -- "$DH" "$DI" "$p" "$want" "$c" "$tmp" <<'EOF' 2>&1 || return 1
set -u
H=$1 I=$2 p=$3 want=$4 c=$5 t=$6 d=$1/$3
ALL="linux freebsd netbsd openbsd dragonfly illumos haiku"
bye() { rm -f "$d/$t" "$d/.COMMIT.xcopy.$$"; echo "$*"; exit 1; }
exec 8>>"$I/.lock-$p" && flock -n 8 || bye "a build of $p holds $I/.lock-$p there"
exec 7<"$H" && flock -w 300 7 || bye "another refresh holds $H there"
got=$(sha256sum "$d/$t" | cut -d' ' -f1)
[[ $got == "$want" ]] || bye "its copy there is '${got:0:12}', not ${want:0:12}"
chmod 755 "$d/$t" && rm -f "$H/SHA256SUMS" || bye "can't make way in $H"
ex=0
if echo "$c" >"$d/.COMMIT.xcopy.$$" && rm -f "$d/COMMIT" &&
   mv -f "$d/$t" "$d/bko-holt" && mv -f "$d/.COMMIT.xcopy.$$" "$d/COMMIT"; then
    :
else
    rm -f "$d/$t" "$d/.COMMIT.xcopy.$$"; echo "$d not replaced"; ex=1
fi
s=$H/.SHA256SUMS.tmp.$$
if (cd "$H" && for q in $(printf '%s\n' $ALL | LC_ALL=C sort); do
        [[ ! -f $q/bko-holt ]] || sha256sum "$q/bko-holt" || exit 1
    done) >"$s" && (cd "$H" && sha256sum -c --quiet --strict "${s##*/}") && mv -f "$s" "$H/SHA256SUMS"; then
    :
else
    rm -f "$s"; echo "$H/SHA256SUMS not written"; ex=1
fi
exit "$ex"
EOF
    echo "DEST's holt-bin/$p is bko-holt ${want:0:12}, commit ${c:0:7}"
}
for p in "$@"; do
    if [[ ! -f $I/$p/done.exit ]]; then
        echo "$p: not copied: no $I/$p/done.exit (a build under way, or stopped)" >&2; bad=$((bad + 1)); continue
    fi
    exec {lk}>>"$I/.lock-$p" || { echo "$p: not copied: can't open $I/.lock-$p" >&2; bad=$((bad + 1)); continue; }
    if ! flock -n -s "$lk"; then
        echo "$p: not copied: a build of it is under way here" >&2; bad=$((bad + 1)); exec {lk}>&-; continue
    fi
    reach
    f=0   # (a platform counts once, however many of its steps fail)
    # (flock -n in --rsync-path: the rsync on DEST runs under its platform's lock, or not at all.)
    if ssh_d "cd $DI && mkdir -p $p && flock -n .lock-$p sh -c 'cd $p && mkdir prev-x$st && find . -maxdepth 1 -mindepth 1 ! -name \"prev-*\" -exec mv -t prev-x$st/ {} +'" &&
       rsync -a --rsync-path="flock -n $DI/.lock-$p rsync" --exclude 'prev-*' --exclude done.exit "$I/$p/" "$D:$DI/$p/"; then
        if out=$(holt_along "$p"); then
            echo "$p: $out"
        else
            echo "$p: DEST's holt-bin/$p NOT brought in line with the image, its pin will write bins-stale: $out" >&2; f=1
        fi
        if rsync -a --rsync-path="flock -n $DI/.lock-$p rsync" "$I/$p/done.exit" "$D:$DI/$p/done.exit"; then
            echo "$p copied"
            if [[ ${BKO_IMG_COMPRESS:-0} == 1 ]]; then
                if ssh_d "d=\$(cd $DI && pwd) && /home/m/backotter-work/cmp-run.sh --reserve 2 6 $DS/compress-images.sh -p $p \"\$d\"" >/dev/null; then
                    echo "$p compressed there"
                else
                    echo "$p: not compressed there" >&2; f=1
                fi
            fi
            # (A failed retention is said, and doesn't fail the copy: the image is in.)
            if [[ ${BKO_IMG_RETAIN:-1} != 0 && $(cat "$I/$p/done.exit" 2>/dev/null) == 0 ]]; then
                out=$(ssh_d "d=\$(cd $DI && pwd) && $DS/img-retain.sh -p $p \"\$d\"" 2>&1) || echo "$p: retention there FAILED: ${out##*$'\n'}" >&2
                [[ -z $out ]] || echo "$p: there, ${out##*$'\n'}"
            fi
        else
            echo "$p: not copied (its done.exit)" >&2; f=1
        fi
    else
        echo "$p: not copied" >&2; f=1
    fi
    bad=$((bad + f))
    exec {lk}>&-
done
exit "$bad"
