#!/usr/bin/env bash
# stage-backup.sh: the backup tool's staging root, for reference
# (docs/design/holt-json.md's open question 14; docs/design/stick-config.md,
# "LUKS2 headers in the backup", step 2, says what the backup tool must do,
# and this is that, for both archivers).  The backup tool isn't built: when
# it is, it does what this does, and this script is its test's yardstick.
#
# Usage (root, Linux):
#   stage-backup.sh [--fs PATH]... [--bind SRC DEST]... [--headers DIR]
#                   [--system-json FILE] [--stage DIR] [--as-path P]
#                   -- ARCHIVER [ARGS...] backup [BACKUP-ARGS...]
#
# It backs up one tree, S, whose paths are the system's, in one snapshot,
# and writes nothing to the system's disks:
#
#   - it runs in a private mount namespace of its own (unshare -m), so no
#     mount it makes is seen outside it, and all of them go when the
#     archiver exits, however it exits;
#   - S is a tmpfs, mounted at --stage DIR/root (default
#     /run/bko-holt/stage/root; DIR's parent must be in RAM);
#   - each file system backed up is bind-mounted read-only (non-recursive:
#     that file system alone) under S at its own path: --fs PATH binds PATH
#     at PATH, --bind SRC DEST binds SRC at DEST (a file system snapshot
#     mounted elsewhere, say).  Parents go before children.  A DEST below
#     another must be a directory there already (its mount point), or in
#     S's own tmpfs;
#   - the root file system (DEST /) can't be bound at S itself (S would
#     then have no room of its own for /.bko-holt), so each entry of its
#     top directory is: a directory or any other file is bind-mounted
#     read-only at S/NAME, and a symbolic link (which can't be bound) is
#     copied with its owner, mode, times and extended attributes (cp -a).
#     S's own owner, mode and times are the root's (neither archiver keeps
#     the root directory's);
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
#                built at S/P instead.  Since restic runs in S, every path
#                in ARGS (the repository, the password file) must be
#                absolute, and an --exclude pattern that starts with / is
#                matched against S's path (/run/bko-holt/stage/root/...).
#
# Which archiver it is comes from what it says it is (rustic-bko's
# --version, "rustic X+backotter.N"; restic-bko's `version`, "restic
# X+backotter.N ...").  Neither may be given --one-file-system (-x): S is
# a file system of its own, so it would leave everything out; the binds
# are what choose the file systems.  Its exit status is the archiver's (2
# when this refuses its arguments, 1 when the staging fails).
set -euo pipefail
umask 022

prog=stage-backup
die() { echo "$prog: $*" >&2; exit "${2:-2}"; }
fail() { echo "$prog: $*" >&2; exit 1; }
say() { echo "$prog: $*" >&2; }

usage() {
    sed -n '/^# Usage/,/^#   -- ARCH/p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

ORIG_ARGS=("$@")
SRCS=() DESTS=() HEADERS='' SYSJSON='' STAGE=/run/bko-holt/stage ASPATH=/
while [ $# -gt 0 ]; do
    case $1 in
        --fs) [ $# -ge 2 ] || usage; SRCS+=("$2"); DESTS+=("$2"); shift 2 ;;
        --bind) [ $# -ge 3 ] || usage; SRCS+=("$2"); DESTS+=("$3"); shift 3 ;;
        --headers) [ $# -ge 2 ] || usage; HEADERS=$2; shift 2 ;;
        --system-json) [ $# -ge 2 ] || usage; SYSJSON=$2; shift 2 ;;
        --stage) [ $# -ge 2 ] || usage; STAGE=$2; shift 2 ;;
        --as-path) [ $# -ge 2 ] || usage; ASPATH=$2; shift 2 ;;
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
for a in "$@"; do
    case $a in
        -x|--one-file-system) die "the archiver can't be given $a: S is a file system of its own" ;;
        --as-path|--as-path=*) die "the archiver's --as-path is this script's (--as-path before --)" ;;
    esac
done
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

# Into a mount namespace of our own, once.  The child is told the parent's
# namespace and checks it is in another, so a stray variable can't make it
# mount in the system's.
here_ns=$(readlink /proc/self/ns/mnt)
if [ "${BKO_STAGE_PARENT_NS:-}" = "" ]; then
    self=$(realpath -e -- "$0")
    BKO_STAGE_PARENT_NS=$here_ns exec unshare -m -- bash "$self" "${ORIG_ARGS[@]}"
fi
[ "$here_ns" != "$BKO_STAGE_PARENT_NS" ] || fail "not in a mount namespace of its own"
unset BKO_STAGE_PARENT_NS
# Nothing of ours propagates out, whatever unshare's default.
mount --make-rprivate /

# Our mounts.  A read-only bind of SRC at DST, that file system only.
robind() {
    mount --bind "$1" "$2" || fail "can't bind $1 at $2"
    mount -o remount,bind,ro "$2" || fail "can't make $2 read-only"
}

parent=$(dirname -- "$STAGE")
case $(stat -f -c %T -- "$parent" 2>/dev/null) in
    tmpfs|ramfs) ;;
    *) fail "--stage's parent $parent isn't in RAM (tmpfs or ramfs)" ;;
esac
mkdir -p -- "$STAGE"
case $(stat -f -c %T -- "$STAGE") in
    tmpfs|ramfs) ;;
    *) fail "--stage $STAGE isn't in RAM (tmpfs or ramfs)" ;;
esac
mount -t tmpfs -o size=64m,mode=0700 bko-stage "$STAGE" || fail "can't mount a tmpfs at $STAGE"
S=$STAGE/root
mkdir -m 0755 "$S"
# The tree's root in the snapshot: S, or S/P for restic's --as-path.
if [ "$kind" = restic ] && [ "$ASPATH" != / ]; then
    TOP=$S$ASPATH
    mkdir -p -- "$TOP"
else
    TOP=$S
fi

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
                cp -a --no-dereference -- "$e" "$TOP/$name" || fail "can't copy the symbolic link $s/$name"
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
        say "/ from $s: $nd directories and $nf other files bound read-only, $nl symbolic links copied"
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

if [ "$kind" = rustic ]; then
    exec "$@" --as-path "$ASPATH" "$S"
fi
cd "$S"
exec "$@" .
