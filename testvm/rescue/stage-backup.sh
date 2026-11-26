#!/usr/bin/env bash
# stage-backup.sh: the backup tool's staging root, for reference
# (docs/design/holt-json.md's open question 14; docs/design/stick-config.md,
# "LUKS2 headers in the backup", step 2, says what the backup tool must do,
# and this is that, for both archivers).  The backup tool isn't built: when
# it is, it does what this does, and this script is its test's yardstick.
#
# Usage (root, Linux, GNU coreutils and util-linux):
#   stage-backup.sh [--fs PATH]... [--bind SRC DEST]... [--headers DIR]
#                   [--system-json FILE] [--stage DIR] [--as-path P]
#                   [--exclude /PATTERN]...
#                   -- ARCHIVER [ARGS...] backup [BACKUP-ARGS...]
#
# It backs up one tree, S, whose paths are the system's, in one snapshot,
# and writes nothing to the system's disks:
#
#   - it runs in a private mount namespace of its own (unshare -m), so no
#     mount it makes is seen outside it.  The archiver runs as its child;
#     when the archiver exits, whatever is left in the namespace (an ssh
#     ControlMaster, say) is stopped, and the stage unmounted, so nothing
#     keeps the backed-up file systems busy.  Killed with SIGKILL, it
#     can't: the archiver then gets SIGTERM (its parent-death signal), but
#     what the archiver started may stay;
#   - S is a tmpfs, mounted at --stage DIR/root (default
#     /run/bko-holt/stage/root).  The nearest of DIR and its parents that
#     is there must be in RAM (tmpfs or ramfs); the missing ones are made,
#     mode 0700 (a normal boot has no /run/bko-holt);
#   - each file system backed up is bind-mounted read-only (non-recursive:
#     that file system alone) under S at its own path: --fs PATH binds PATH
#     at PATH, --bind SRC DEST binds SRC at DEST (a file system snapshot
#     mounted elsewhere, say).  Parents go before children.  A DEST below
#     another must be a directory there already (its mount point), or in
#     S's own tmpfs.  Each SRC is walked into first, before the namespace
#     is made, so an expired automount is mounted again; an automount
#     point bound unmounted is refused;
#   - the root file system (DEST /) can't be bound at S itself (S would
#     then have no room of its own for /.bko-holt), so each entry of its
#     top directory is: a directory or any other file is bind-mounted
#     read-only at S/NAME, and a symbolic link (which can't be bound) is
#     copied with its owner, mode, times, extended attributes and (with
#     SELinux) its label, a failure to keep any of them an error.  S's own
#     owner, mode and times are the root's (neither archiver keeps the root
#     directory's);
#   - --headers DIR (bko-holt dump --luks-header-dir's) is bound read-only
#     at S/.bko-holt/luks-headers, and --system-json FILE at
#     S/.bko-holt/system.json (open question 15).  S/.bko-holt is a 0700
#     directory of S's tmpfs.  A /.bko-holt of the root file system's own
#     (a mount point an older backup left?) is left out, with a note: the
#     name is the backup tool's.
#
# Then the archiver backs S up, the way it can:
#
#   rustic-bko   ARCHIVER ARGS... backup BACKUP-ARGS... --as-path P S
#                (P default /: the snapshot's paths are ["/"])
#   restic-bko   cd S && ARCHIVER ARGS... backup BACKUP-ARGS... .
#                restic has no --as-path.  "." backs up S's entries at the
#                tree's top (restic's resolveRelativeTargets), so the paths
#                in the tree are the system's.  The snapshot's `paths` is
#                S's own ("/run/bko-holt/stage/root"), fixed, so restic
#                still finds the parent.  With --as-path P, S's tree is
#                built at S/P and restic backs up P's relative path
#                (`backup sub`): the tree's top is P, and the snapshot's
#                `paths` is S/P's, one of its own for each P.  Since restic
#                runs in S, every path in ARGS (the repository, the
#                password file) must be absolute (a relative ARCHIVER is
#                made absolute).
#
# Excludes: both archivers match an absolute pattern against the path they
# read, which is S's (rustic-bko's --glob before --as-path maps it, restic-
# bko's --exclude), never the system's.  So --exclude /PATTERN, a system
# path, is given as rustic-bko's --glob '!TOP/PATTERN' or restic-bko's
# --exclude TOP/PATTERN (TOP the tree's top: S, or S/P under restic-bko's
# --as-path), and an absolute --glob or --exclude after -- is refused.  A
# relative one (no leading /) is the archiver's, as it is.  Patterns in a
# file (--glob-file, --exclude-file) or a rustic profile aren't seen.
#
# Which archiver it is comes from what it says it is (rustic-bko's
# --version, "rustic X+backotter.N"; restic-bko's `version`, "restic
# X+backotter.N ...").  Neither may be given --one-file-system (-x): S is
# a file system of its own, so it would leave everything out; the binds
# are what choose the file systems.  (A rustic profile's one-file-system
# isn't seen; the backup's read-back then finds no /.bko-holt/system.json.)
# Its exit status is the archiver's (2 when this refuses its arguments, 1
# when the staging fails).
set -euo pipefail
umask 022

prog=stage-backup
die() { echo "$prog: $*" >&2; exit "${2:-2}"; }
fail() { echo "$prog: $*" >&2; exit 1; }
say() { echo "$prog: $*" >&2; }

usage() {
    sed -n '/^# Usage/,/-- ARCHIVER/p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

ORIG_ARGS=("$@")
SRCS=() DESTS=() EXCLUDES=() HEADERS='' SYSJSON='' STAGE=/run/bko-holt/stage ASPATH=/
while [ $# -gt 0 ]; do
    case $1 in
        --fs) [ $# -ge 2 ] || usage; SRCS+=("$2"); DESTS+=("$2"); shift 2 ;;
        --bind) [ $# -ge 3 ] || usage; SRCS+=("$2"); DESTS+=("$3"); shift 3 ;;
        --headers) [ $# -ge 2 ] || usage; HEADERS=$2; shift 2 ;;
        --system-json) [ $# -ge 2 ] || usage; SYSJSON=$2; shift 2 ;;
        --stage) [ $# -ge 2 ] || usage; STAGE=$2; shift 2 ;;
        --as-path) [ $# -ge 2 ] || usage; ASPATH=$2; shift 2 ;;
        --exclude) [ $# -ge 2 ] || usage; EXCLUDES+=("$2"); shift 2 ;;
        --) shift; break ;;
        -h|--help) usage ;;
        *) die "unknown option $1 (the archiver's command goes after --)" ;;
    esac
done
[ $# -ge 2 ] || die "no archiver command after --"
[ ${#DESTS[@]} -gt 0 ] || die "nothing to back up: give --fs or --bind"
[ "$(uname -s)" = Linux ] || die "needs Linux (a private mount namespace)"
[ "$(id -u)" = 0 ] || die "needs root (it mounts)"

# A path as the snapshot names it: absolute, no empty, . or .. part, no
# trailing slash, no tab or newline.
canon() {
    case $1 in /) return 0 ;; /*) ;; *) return 1 ;; esac
    case $1 in */|*//*|*/./*|*/../*|*/.|*/..|*$'\n'*|*$'\t'*) return 1 ;; esac
}
if ! canon "$STAGE" || [ "$STAGE" = / ]; then die "--stage $STAGE isn't an absolute path"; fi
canon "$ASPATH" || die "--as-path $ASPATH isn't an absolute path"
for x in ${EXCLUDES[@]+"${EXCLUDES[@]}"}; do
    case $x in /?*) ;; *) die "--exclude $x isn't an absolute pattern (a system path, from /)" ;; esac
done
# A relative ARCHIVER would be lost by restic-bko's cd: made absolute.
case $1 in
    /*) ;;
    */*) a=$(realpath -e -- "$1") || die "$1: no such archiver"; set -- "$a" "${@:2}" ;;
esac
has_backup=
prev=
for a in "$@"; do
    case $a in
        -x|--one-file-system|--one-file-system=*)
            die "the archiver can't be given $a: S is a file system of its own" ;;
        --as-path|--as-path=*) die "the archiver's --as-path is this script's (--as-path before --)" ;;
        --glob=/*|--glob=!/*|--iglob=/*|--iglob=!/*|--exclude=/*|--iexclude=/*)
            die "$a: an absolute exclude after -- would match S's path, not the system's: give --exclude before --" ;;
        backup) has_backup=1 ;;
        --*) ;;
        -*x*)
            # Short options bundled (-vx): letters only, so not a value.
            case $a in -*[!a-zA-Z]*) ;; *) die "the archiver can't be given $a (-x): S is a file system of its own" ;; esac ;;
    esac
    case $prev in
        --glob|-g|--iglob|--exclude|-e|--iexclude)
            case $a in /*|!/*) die "$prev $a: an absolute exclude after -- would match S's path, not the system's: give --exclude before --" ;; esac ;;
    esac
    prev=$a
done
[ -n "$has_backup" ] || die "no backup command in the archiver's arguments"
n_root=0
for i in "${!DESTS[@]}"; do
    d=${DESTS[$i]} s=${SRCS[$i]}
    canon "$d" || die "$d isn't an absolute path"
    canon "$s" || die "$s isn't an absolute path"
    case $d in /.bko-holt|/.bko-holt/*) die "$d: /.bko-holt is the backup tool's own" ;; esac
    [ "$d" = / ] && n_root=$((n_root + 1))
    for j in "${!DESTS[@]}"; do
        [ "$i" -lt "$j" ] && [ "$d" = "${DESTS[$j]}" ] && die "$d is given twice"
    done
    # Walked into (not only looked at), so an expired automount is mounted
    # again here, in the system's namespace, before ours copies it.
    stat -- "$s/." >/dev/null 2>&1 || true
    if [ ! -d "$s" ] || [ -L "$s" ]; then die "$s isn't a directory"; fi
    [ "$(realpath -e -- "$s")" = "$s" ] || die "$s has a symbolic link in it: give the path it leads to"
    case $s/ in "$STAGE"/*) die "$s is inside --stage" ;; esac
done
[ "$n_root" -le 1 ] || die "/ is given twice"
if [ -n "$HEADERS" ]; then
    if [ ! -d "$HEADERS" ] || [ -L "$HEADERS" ]; then die "--headers $HEADERS isn't a directory"; fi
    [ "$(stat -c %a -- "$HEADERS")" = 700 ] || die "--headers $HEADERS isn't mode 0700 (the headers are key material)"
fi
if [ -n "$SYSJSON" ]; then
    if [ ! -f "$SYSJSON" ] || [ -L "$SYSJSON" ]; then die "--system-json $SYSJSON isn't a file"; fi
fi

# rustic-bko says it with --version, restic-bko with `version`.
ver=$("$1" --version 2>/dev/null | head -n 1) || true
case $ver in
    rustic\ *) kind=rustic ;;
    *)
        ver=$("$1" version 2>/dev/null | head -n 1) || true
        case $ver in
            restic\ *) kind=restic ;;
            *) die "$1 is neither restic-bko nor rustic-bko (its --version or version: ${ver:-nothing})" ;;
        esac ;;
esac
if [ "$kind" = restic ]; then
    # restic runs in S: a relative path would be S's.
    prev=
    for a in "$@"; do
        case $prev in
            --password-file|-p|--repository-file|--files-from|--files-from-raw|--files-from-verbatim|--exclude-file|--iexclude-file|--cacert|--tls-client-cert|--cache-dir)
                case $a in /*) ;; *) die "$prev $a: restic-bko runs in S, so the path must be absolute" ;; esac ;;
            --repo|-r)
                case $a in /*|*:*) ;; *) die "$prev $a: restic-bko runs in S, so the path must be absolute" ;; esac ;;
        esac
        case $a in
            --password-file=[!/]*|--repository-file=[!/]*|--files-from=[!/]*|--exclude-file=[!/]*|--iexclude-file=[!/]*|--cacert=[!/]*|--tls-client-cert=[!/]*|--cache-dir=[!/]*)
                die "$a: restic-bko runs in S, so the path must be absolute" ;;
            --repo=[!/]*) case $a in *:*) ;; *) die "$a: restic-bko runs in S, so the path must be absolute" ;; esac ;;
        esac
        prev=$a
    done
    for v in RESTIC_PASSWORD_FILE RESTIC_REPOSITORY_FILE RESTIC_CACERT RESTIC_CACHE_DIR; do
        val=${!v:-}
        case $val in ''|/*) ;; *) die "$v=$val: restic-bko runs in S, so the path must be absolute" ;; esac
    done
    case ${RESTIC_REPOSITORY:-/} in /*|*:*) ;; *) die "RESTIC_REPOSITORY=$RESTIC_REPOSITORY: restic-bko runs in S, so the path must be absolute" ;; esac
fi

# Into a mount namespace of our own, once.  unshare execs us again in the
# same process, so the marker is our PID: a stray one (exported, or an
# older run's) is another number, and we unshare all the same.  Then we
# check we are in neither init's namespace nor our caller's.
here_ns=$(readlink /proc/self/ns/mnt)
if [ "${BKO_STAGE_UNSHARED:-}" != "$$" ]; then
    self=$(realpath -e -- "$0")
    BKO_STAGE_UNSHARED=$$ exec unshare -m -- bash "$self" "${ORIG_ARGS[@]}"
fi
unset BKO_STAGE_UNSHARED
init_ns=$(readlink /proc/1/ns/mnt)
caller_ns=$(readlink "/proc/$PPID/ns/mnt" 2>/dev/null) || caller_ns=none
if [ -z "$here_ns" ] || [ "$here_ns" = "$init_ns" ] || [ "$here_ns" = "$caller_ns" ]; then
    fail "not in a mount namespace of its own"
fi
# Nothing of ours propagates out, whatever unshare's default.
mount --make-rprivate /

# Our mounts.  A read-only bind of SRC at DST, that file system only.
robind() {
    mount --bind "$1" "$2" || fail "can't bind $1 at $2"
    mount -o remount,bind,ro "$2" || fail "can't make $2 read-only"
}
in_ram() {
    case $(stat -f -c %T -- "$1" 2>/dev/null) in tmpfs|ramfs) return 0 ;; *) return 1 ;; esac
}

# The stage's parents: the nearest one there must be in RAM, and the
# missing ones are made 0700 (bko-holt's /run/bko-holt is, and a normal
# boot has none until a dump made it).
parent=$(dirname -- "$STAGE")
a=$parent
while [ ! -e "$a" ] && [ ! -L "$a" ]; do a=$(dirname -- "$a"); done
in_ram "$a" || fail "--stage's parent $parent isn't in RAM (tmpfs or ramfs): $a isn't"
(umask 077 && mkdir -p -- "$STAGE") || fail "can't make $STAGE"
in_ram "$STAGE" || fail "--stage $STAGE isn't in RAM (tmpfs or ramfs)"
mount -t tmpfs -o size=64m,mode=0700 bko-stage "$STAGE" || fail "can't mount a tmpfs at $STAGE"
S=$STAGE/root
# 0755 until the root's own owner, mode and times are given it (below).
mkdir -m 0755 "$S"
# The tree's root in the snapshot: S, or S/P for restic's --as-path.
if [ "$kind" = restic ] && [ "$ASPATH" != / ]; then
    TOP=$S$ASPATH
    mkdir -p -- "$TOP"
else
    TOP=$S
fi

# A copied symbolic link keeps all it has, or it is an error: cp's -a
# (--preserve=all) says nothing when it can't keep an xattr or a label.
cp_keep=mode,ownership,timestamps,xattr
[ ! -e /sys/fs/selinux/enforce ] || cp_keep=$cp_keep,context
xattrs() { getfattr -h -d -m - -e hex --absolute-names -- "$1" 2>/dev/null | sed 1d; }
have_getfattr=; command -v getfattr >/dev/null && have_getfattr=1

ROOTREF=
# Parents first: by depth, / before all.
order=$(for i in "${!DESTS[@]}"; do
    d=${DESTS[$i]}
    if [ "$d" = / ]; then n=0; else n=$(printf %s "$d" | tr -cd / | wc -c); fi
    printf '%d\t%s\t%d\n' "$n" "$d" "$i"
done | sort -t "$(printf '\t')" -k1,1n -k2,2 | cut -f3)

for i in $order; do
    s=${SRCS[$i]} d=${DESTS[$i]}
    if [ "$d" = / ]; then
        R=$STAGE/rootfs
        mkdir -m 0700 "$R"
        robind "$s" "$R"
        chown --reference="$R" "$TOP"
        chmod --reference="$R" "$TOP"
        nd=0 nf=0 nl=0
        while IFS= read -r -d '' e; do
            name=${e#"$R"/}
            if [ "$name" = .bko-holt ]; then
                say "$s/.bko-holt is the root file system's own (a mount point an older backup left?): left out, the name is the backup tool's"
                continue
            fi
            if [ -L "$e" ]; then
                cp -P --preserve="$cp_keep" -- "$e" "$TOP/$name" || fail "can't copy the symbolic link $s/$name with all it has"
                if [ -n "$have_getfattr" ] && [ "$(xattrs "$e")" != "$(xattrs "$TOP/$name")" ]; then
                    fail "the copy of the symbolic link $s/$name hasn't its extended attributes"
                fi
                nl=$((nl + 1))
            elif [ -d "$e" ]; then
                mkdir -m 0700 "$TOP/$name"
                robind "$e" "$TOP/$name"
                nd=$((nd + 1))
            else
                : >"$TOP/$name"
                robind "$e" "$TOP/$name"
                nf=$((nf + 1))
            fi
        done < <(find "$R" -mindepth 1 -maxdepth 1 -print0)
        ROOTREF=$R
        say "/ from $s: $nd directories and $nf other files bound read-only, $nl symbolic links copied${have_getfattr:+ (extended attributes checked)}"
        continue
    fi
    t=$TOP$d
    # No symbolic link on the way: a bind follows one, out of S.
    p=$TOP
    IFS=/ read -r -a parts <<<"${d#/}"
    for part in "${parts[@]}"; do
        p=$p/$part
        [ ! -L "$p" ] || fail "$d: ${p#"$TOP"} is a symbolic link in the tree above it"
    done
    if [ ! -e "$t" ]; then
        mkdir -p -- "$t" 2>/dev/null ||
            fail "$d isn't a directory of the file system above it (its mount point), and that one is read-only"
    fi
    [ -d "$t" ] || fail "$d isn't a directory in the tree above it"
    robind "$s" "$t"
    mountpoint -q "$t" || fail "$d isn't a mount point after its bind"
    # An automount point whose file system isn't mounted in our copy: the
    # snapshot would hold an empty directory (read from mountinfo, which
    # triggers nothing).
    [ "$(findmnt -n -o FSTYPE --mountpoint "$t" | tail -n 1)" != autofs ] ||
        fail "$s is an automount point with nothing mounted on it"
    say "$d from $s, read-only"
done

if [ -n "$HEADERS" ] || [ -n "$SYSJSON" ]; then
    mkdir -m 0700 "$TOP/.bko-holt"
fi
if [ -n "$HEADERS" ]; then
    mkdir -m 0700 "$TOP/.bko-holt/luks-headers"
    robind "$HEADERS" "$TOP/.bko-holt/luks-headers"
    say "/.bko-holt/luks-headers from $HEADERS, read-only ($(find "$HEADERS" -mindepth 1 -maxdepth 1 | wc -l) files)"
fi
if [ -n "$SYSJSON" ]; then
    : >"$TOP/.bko-holt/system.json"
    robind "$SYSJSON" "$TOP/.bko-holt/system.json"
    say "/.bko-holt/system.json from $SYSJSON, read-only"
fi

# The root's times last, once nothing more is made in it.
[ -z "$ROOTREF" ] || touch -h -r "$ROOTREF" "$TOP"

# The excludes, at the path each archiver reads.
xa=()
for x in ${EXCLUDES[@]+"${EXCLUDES[@]}"}; do
    if [ "$kind" = rustic ]; then xa+=(--glob "!$TOP$x"); else xa+=(--exclude "$TOP$x"); fi
done

# Every process left in our namespace, the archiver's children too: TERM,
# then KILL.  Never in init's or the caller's (checked above, and again).
sweep() {
    [ "$here_ns" != "$(readlink /proc/1/ns/mnt)" ] || return 0
    local sig n pid p tries
    for sig in TERM KILL; do
        tries=0
        while :; do
            n=0
            for p in /proc/[0-9]*; do
                pid=${p#/proc/}
                [ "$pid" != $$ ] || continue
                [ "$(readlink "$p/ns/mnt" 2>/dev/null)" = "$here_ns" ] || continue
                kill "-$sig" "$pid" 2>/dev/null && n=$((n + 1))
            done
            [ "$n" -gt 0 ] || return 0
            tries=$((tries + 1))
            [ "$tries" -le 50 ] || break
            sleep 0.1
        done
        say "$n processes the archiver left in the namespace didn't stop on SIG$sig"
    done
}

# The archiver, as our child: it gets SIGTERM if we die, and our TERM and
# HUP are passed on to it.  A terminal's ^C (SIGINT) reaches it itself, in
# our process group, so we only outlive it.
if [ "$kind" = rustic ]; then
    set -- "$@" ${xa[@]+"${xa[@]}"} --as-path "$ASPATH" "$S"
else
    cd "$S"
    if [ "$ASPATH" = / ]; then set -- "$@" ${xa[@]+"${xa[@]}"} .; else set -- "$@" ${xa[@]+"${xa[@]}"} "${ASPATH#/}"; fi
fi
exec 3<&0
setpriv --pdeathsig TERM -- "$@" <&3 3<&- &
apid=$!
exec 3<&-
trap 'kill -TERM "$apid" 2>/dev/null || true' TERM
trap 'kill -HUP "$apid" 2>/dev/null || true' HUP
trap : INT
rc=0
while :; do
    rc=0
    wait "$apid" || rc=$?
    # A trapped signal ends the wait early (over 128), the archiver still
    # running; or it ended meanwhile, and wait gives its own status.
    [ "$rc" -gt 128 ] || break
    if ! kill -0 "$apid" 2>/dev/null; then
        r=0
        wait "$apid" 2>/dev/null || r=$?
        [ "$r" = 127 ] || rc=$r
        break
    fi
done
trap - TERM INT HUP
cd /
sweep
umount -R "$STAGE" 2>/dev/null || umount -R -l "$STAGE" || say "can't unmount $STAGE (it goes with the namespace)"
exit "$rc"
