#!/usr/bin/env bash
# A whole test round on one KVM host, unattended: checks the binaries, runs
# every platform's round trips (the scripts here) at once within the
# host's limits, and prints a verdict table.  Agents only read the failures.
#
# Usage: run-all.sh [--host HOST] [--platforms LIST] [--only REGEX]
#                   [--dry-run] BIN_DIR LOG_DIR
#   --host       whose platforms, ports and limits (default: this hostname)
#   --platforms  comma- or space-separated, the other host's skipped (an
#                empty list: nothing; default: all of the host's):
#                  chopin:   linux sparse zfsguests dragonfly
#                  cinqcent: linux sparse freebsd netbsd openbsd_illumos
#                  a small host (copland, cage; testvm/hosts.json's class):
#                            linux sparse (no host ZFS); SPLIT: a share
#                linux and sparse are every host's, each with its share of
#                the file systems (LINUX_FS, SPARSE_FS)
#                linux      fs_roundtrip.py on zfs ext4 xfs btrfs f2fs bcachefs
#                           tmpfs jfs, zfs --onto ext4; nonroot-rt.sh linux
#                sparse     sparse-rt.sh on ext4 xfs btrfs zfs zfs-nocomp
#                zfsguests  zfs-guest-rt.sh ubuntu24, ubuntu26-zfsmirror
#                dragonfly  dragonfly6: run-guest-rt.sh, nonroot, sparse-rt.sh
#                openbsd_illumos
#                           openbsd8, omnios58: run-guest-rt.sh, nonroot;
#                           sparse-rt.sh in omnios58
#                freebsd    freebsd15, freebsd15-ufs: run-guest-rt.sh,
#                           nonroot, sparse-rt.sh
#                netbsd     netbsd11, alpinebsd: run-guest-rt.sh, nonroot;
#                           sparse-rt.sh in netbsd11's build image (cc)
#   --only       run only the steps whose PLATFORM/STEP matches (grep -E)
#   --dry-run    check the binaries and print every step's command; run none
#   BIN_DIR      the round's binaries: OS/restic-bko, OS/rustic-bko and their
#                restic-commit, rustic-commit (OS linux freebsd openbsd netbsd
#                dragonfly illumos; omnios58 runs illumos', alpinebsd netbsd's)
#   LOG_DIR      PLATFORM/STEP.log (and the scripts' own logs beside it),
#                summary.txt and summary.json (the table), rows.jsonl
#
# Environment:
#   OLD_BIN      the round before's bin directory, every OS in it:
#                sparse-rt.sh's and fs_roundtrip.py's snapshot identity
#                (SPEC3 2); OLD_BIN=none: not checked (a note); unset: refused
#   EXPECT_RESTIC_COMMIT, EXPECT_RUSTIC_COMMIT, EXPECT_RUSTIC_CORE_COMMIT
#                the Mac's HEADs, which the commit files must name (prefixes
#                of 7 or more; build-round.sh prints the line to set them);
#                unset: refused, unless UNCHECKED_BINARIES=1 (a note: only
#                that every OS's commit files agree).  EXPECT_RESTIC_DIGEST,
#                EXPECT_RESTIC_VERSION, EXPECT_RUSTIC_VERSION (e.g.
#                0.19.1+backotter.3): checked if set.  Refuses to run on a
#                mismatch, a missing binary, commit file or rustic_core line,
#                or a SHA256SUMS line that fails.
#   PORT_BASE    ports are PORT_BASE + 10..19 (default the first port of the
#                host's slot_range, testvm/hosts.json: 2700 chopin, 2900
#                cinqcent, 2800 copland, 3000 cage; e.g. 2750 for 2760-2769)
#   MAX_GUESTS   guests at once (default chopin 6, cinqcent, copland and cage
#                3); COUNT_OTHERS=1 (every host's default but chopin's):
#                host-wide, with guest-slot.sh's slots on GUEST_PORTS (the
#                host's slot_range: 2700-2799 chopin, 2900-2999 cinqcent,
#                2800-2899 copland, 3000-3099 cage), which
#                stick/build-round.sh's guests and the SticTest1 checks take
#                too, and every QEMU of ours there without one
#   LINUX_FS     the linux steps run here, of zfs zfs-onto-ext4 bcachefs f2fs
#                ext4 xfs btrfs tmpfs jfs nonroot (default chopin: zfs
#                zfs-onto-ext4 bcachefs xfs btrfs nonroot, the only bcachefs
#                tools are Chopin's; cinqcent: ext4 f2fs tmpfs jfs; copland
#                and cage: ext4 xfs btrfs f2fs tmpfs jfs, what they have mkfs
#                for, without ZFS (zfs, zfs-onto-ext4 and nonroot's pool) or
#                bcachefs tools); all: every one
#   SPARSE_FS    the sparse steps run here, of ext4 xfs btrfs zfs zfs-nocomp
#                (default chopin: xfs btrfs; cinqcent: zfs zfs-nocomp ext4;
#                copland and cage: ext4 xfs btrfs); all: every one.
#                ANY_HOST=1 makes both default to all (on copland and cage
#                too, ZFS's steps then fail)
#   HOST_JOBS    fs_roundtrip.py and nonroot-rt.sh linux runs at once (4);
#                SPARSE_JOBS sparse-rt.sh runs on the host at once (2)
#   JOBS_CAP     at most this many of each at once, whatever HOST_JOBS and
#                SPARSE_JOBS say (default 1 on the small hosts, copland and
#                cage, none elsewhere): their 10 GiB RAM disk (cmp-run.sh's
#                ceiling 9.0 9.2) holds one fs_roundtrip.py in RAM, about
#                6.1 GiB at its peak, not two (12.2 GiB on chopin and
#                cinqcent, whatever their file systems); 0: no cap
#   STEP_TIMEOUT seconds, every step (default by kind, 1.5-6 h: round 9's
#                times several over, as a host as loaded as Chopin was in its
#                round 10 (load 100) takes)
#   RUN_PREFIX   names of pools, mount points, images (default bkora: pools
#                bkora-fs-*, bkora-sp-*, bkora-nr; /mnt/bkora-nr-ext4 and
#                -zfs); one run per prefix
#   BKO_WORK     where vms/ is (default ~/backotter-work); the scripts work
#                in LOG_DIR/work, which links to its vms/
#   RAM_DIR      the guests' --ram overlays (default LOG_DIR/work/ram, on
#                disk: round 9 filled /dev/shm)
#   BIG          sparse-rt.sh's big file on the host, GiB (20); GUEST_BIG in
#                guests (2).  IMG_GB, when set, its image or tmpfs on the host
#                (sparse-rt.sh's default 16 otherwise)
#   BASELINE     an earlier LOG_DIR/summary.json: nonroot's per-direction
#                counts must be the same as there
#   ALLOW_SKIP   comma-separated targets that may be skipped: a file system
#                fs_roundtrip.py can't make here (e.g. bcachefs), or a step
#                run-all.sh can't run (sparse-netbsd11); else a FAIL row
#   KEEP_WORK=1  keep fs_roundtrip.py's repositories
#   ANY_HOST=1   run another host's platforms here too
#   SPLIT        H,H,... (testvm/hosts.json's names) or all (every enabled
#                amd64 host; an arm64 one, bigfoot, is refused): a
#                round those hosts share, each step run by one of
#                them only, in place of the profiles' shares (which overlap
#                once copland or cage join chopin and cinqcent).  Every host
#                deals the same: bcachefs stays on chopin and netbsd on
#                cinqcent when they take part; ZFS's steps (zfs,
#                zfs-onto-ext4, nonroot, sparse zfs and zfs-nocomp) go to big
#                hosts; the other file systems and guest platforms to the
#                host with the fewest of their kind so far (two guest
#                platforms on one PORT_BASE offset never share a host).  The
#                share and every step's command: --dry-run.  On a host
#                without a guest's image (vms/GUEST: a cache host's may be
#                evicted) that step is a FAIL row, as is, on the first host
#                of the split, a step no host of it can run.  LINUX_FS,
#                SPARSE_FS and --platforms still choose among the share,
#                never past it (another host's steps are skipped, so ZFS's
#                stay the big hosts' and no step runs twice); MAX_GUESTS, JOBS_CAP and the rest stay the host's.  Unset:
#                the profiles, as before.  Not with ANY_HOST
#   RT_RAM, PYTHONDONTWRITEBYTECODE
#                passed on to the root steps when set (RT_RAM=0: their work
#                on disk; unset, the scripts' default, RAM where
#                /mnt/bko-ram is mounted)
# fs_roundtrip.py's results go to fs-roundtrip/ beside the testvm/ this
# script is in (compare.py's WORK_BASE, the copy's root when run from a
# private copy), and from there into LOG_DIR/linux/FS/.
#
# Steps of a platform run one after another in chains (one per port);
# chains and platforms run at once, a step waiting for a guest slot (or a
# host job slot).  A guest that doesn't come up is retried once.  A step
# that crashes or times out is a FAIL with its log's tail.  The verdicts
# are verdict.py's (accepted differences: its header).  Exits 0 only if
# every step passes; 2 if it refused to run.  On exit, Ctrl-C or TERM (a
# background job of a script ignores INT: TERM it) it stops its own
# processes (by BKO_RUNALL_ID in their environment, QEMU's too), and
# destroys its pools, mounts and loop devices (RUN_PREFIX).
#
# Needs: bash, python3, flock, timeout, setsid; root steps (linux, sparse):
# root or sudo -n; and what each script needs.

set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
die() { echo "run-all: $*" >&2; exit 2; }
usage() { sed -n '6,35p' "$0" >&2; exit 2; }

host="" plats="" pset="" only="" dry=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --host) host=${2:-}; shift 2 ;;
        --platforms) plats=${2:-} pset=1; shift 2 ;;
        --only) only=${2:-}; shift 2 ;;
        --dry-run) dry=1; shift ;;
        -h|--help) usage ;;
        -*) die "no such option: $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || usage
[[ -d $1 ]] || die "no bin directory $1"
BIN=$(cd "$1" && pwd) LOG=$2
[[ $LOG == /* ]] || LOG=$PWD/$LOG
[[ -n $host ]] || host=$(hostname -s 2>/dev/null || hostname)
host=${host%%.*}
# The host's row in testvm/hosts.json: its class picks the profile (a big
# host's is its own), its slot_range the ports.
# shellcheck source=../hosts.sh
. "$here/../hosts.sh"
hclass=$(hosts_get "$host" class) && grange=$(hosts_get "$host" slot_range) ||
    die "no such host: $host (--host $(hosts_phrase))"
# Its guests and binaries are amd64 ones: never on an arm64 host (Bigfoot).
harch=$(hosts_arch "$host") && [[ $harch == amd64 ]] ||
    die "$host is an ${harch:-unknown} host (testvm/hosts.json's arch): run-all.sh's guests and binaries are amd64"
[[ $hclass == small ]] && prof=small || prof=$host
case $prof in
    chopin) all="linux sparse zfsguests dragonfly"
        lfs="zfs zfs-onto-ext4 bcachefs xfs btrfs nonroot" sfs="xfs btrfs"
        : "${MAX_GUESTS:=6}" "${COUNT_OTHERS:=0}" ;;
    cinqcent) all="linux sparse freebsd netbsd openbsd_illumos"
        lfs="ext4 f2fs tmpfs jfs" sfs="zfs zfs-nocomp ext4"
        : "${MAX_GUESTS:=3}" "${COUNT_OTHERS:=1}" ;;
    # The small hosts (copland, cage: 27 GiB, a 10 GiB RAM disk, no host
    # ZFS, guests from the image cache): profiled like Cinqcent, each on its
    # own range, one job of a kind at a time (JOBS_CAP); not build hosts, not
    # default hosts anywhere yet.  Their profile's file systems overlap the
    # big hosts' and each other's: SPLIT deals a round out without overlap.
    small) all="linux sparse"
        lfs="ext4 xfs btrfs f2fs tmpfs jfs" sfs="ext4 xfs btrfs"
        : "${MAX_GUESTS:=3}" "${COUNT_OTHERS:=1}" "${JOBS_CAP:=1}" ;;
    *) die "no run-all.sh profile for $host, a $hclass host (a big host's is its own: chopin or cinqcent)" ;;
esac
: "${PORT_BASE:=${grange%-*}}" "${GUEST_PORTS:=$grange}"
ALL_LFS="zfs zfs-onto-ext4 bcachefs f2fs ext4 xfs btrfs tmpfs jfs nonroot" ALL_SFS="ext4 xfs btrfs zfs zfs-nocomp"
# SPLIT: the round's steps dealt out over the hosts that share it, each one
# to a single host (split_share; every host computes the same deal from the
# same SPLIT and the table, whatever order SPLIT names them in).
split="" split_lost=""
split_share() {  # SPLIT HOST -> "hosts H...", "linux FS...", "sparse FS...", "plats P...", "lost KIND NAME WHY"...
    python3 -I -B - "$BKO_HOSTS_PY" "$1" "$2" <<'EOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bko_hosts", sys.argv[1])
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
want, me = sys.argv[2], sys.argv[3]
try:
    known = H.names(arch="amd64")   # run-all.sh's guests and binaries are amd64 ones
    ns = list(known) if want == "all" else [H.short(x) for x in want.replace(",", " ").split()]
    other = [n for n in ns if n in H.known() and n not in known]
    if other:
        sys.exit(f"SPLIT: {' '.join(other)}: not an amd64 host (testvm/hosts.json's arch), and run-all.sh's "
                 f"guests and binaries are amd64 (of {H.phrase(known)}, or all)")
    bad = [n for n in ns if n not in known]
    if bad or not ns:
        sys.exit(f"SPLIT: no such host: {' '.join(bad) or '(none named)'} (of {H.phrase(known)}, or all)")
    hs = [n for n in known if n in ns]   # the table's order: the same deal on every host
    cls = {h: H.host_class(h) for h in hs}
except H.HostsError as e:
    sys.exit(f"SPLIT: {e}")
if me not in hs:
    sys.exit(f"SPLIT: {me} is not one of its hosts ({' '.join(hs)})")
# KIND NAME WEIGHT NEEDS HOME PORTS: NEEDS big, a big host (host ZFS: zfs, its
# --onto, nonroot's pool, sparse's pools); HOME, the host it stays on when that
# host shares the round (bcachefs: the only tools are Chopin's; netbsd:
# vms-bko-build's netbsd11-rust.qcow2 is Cinqcent's), else any big host;
# PORTS, the PORT_BASE offsets a guest platform takes (no two on one host).
# WEIGHT is a rough size (a guest platform: its steps).
UNITS = [("linux", "bcachefs", 1, "big", "chopin", ())] + \
    [("linux", n, 1, "big", None, ()) for n in ("zfs", "zfs-onto-ext4", "nonroot")] + \
    [("linux", n, 1, None, None, ()) for n in ("ext4", "xfs", "btrfs", "f2fs", "tmpfs", "jfs")] + \
    [("sparse", n, 1, "big", None, ()) for n in ("zfs", "zfs-nocomp")] + \
    [("sparse", n, 1, None, None, ()) for n in ("ext4", "xfs", "btrfs")] + \
    [("plats", "netbsd", 5, "big", "cinqcent", (15, 16)), ("plats", "freebsd", 6, None, None, (10, 11)),
     ("plats", "openbsd_illumos", 5, None, None, (12, 13)), ("plats", "dragonfly", 3, None, None, (10, 11)),
     ("plats", "zfsguests", 2, None, None, (17, 18))]
# Pinned ones first, then those only some hosts can run, the heavier first;
# each to the host with the least of its kind so far (the table's order on a tie).
first = sorted(range(len(UNITS)), key=lambda i: (UNITS[i][4] not in hs, UNITS[i][3] is None, -UNITS[i][2], i))
load, ports, got, lost = {}, {h: set() for h in hs}, {}, []
for i in first:
    kind, name, w, need, home, pp = UNITS[i]
    can = [home] if home in hs else [h for h in hs if need is None or cls[h] == need]
    ok = [h for h in can if not ports[h] & set(pp)]
    if not ok:
        why = ("no host of SPLIT can run it" + (": it needs a big host (host ZFS or its tools)" if not can else
                                                 ": its ports go to another guest platform on " + " ".join(can)))
        lost.append(f"lost {kind} {name} {why}")
        continue
    h = min(ok, key=lambda x: (load.get((kind, x), 0), hs.index(x)))
    load[(kind, h)] = load.get((kind, h), 0) + w
    ports[h] |= set(pp)
    got.setdefault((kind, h), []).append(i)
print("hosts " + " ".join(hs))
for kind in ("linux", "sparse", "plats"):
    print(kind + "".join(" " + UNITS[i][1] for i in sorted(got.get((kind, me), []))))
if me == hs[0]:   # the split's first host has the FAIL rows of what none runs
    print("\n".join(lost))
EOF
}
if [[ -n ${SPLIT:-} ]]; then
    [[ -z ${ANY_HOST:-} ]] || die "SPLIT and ANY_HOST: one or the other"
    share=$(split_share "$SPLIT" "$host") || die "can't split the round (SPLIT=$SPLIT)"
    split=$(sed -n 's/^hosts //p' <<<"$share")
    lfs=$(sed -n 's/^linux *//p' <<<"$share") sfs=$(sed -n 's/^sparse *//p' <<<"$share")
    # LINUX_FS and SPARSE_FS choose among the share, never past it: another
    # host's steps (or a big host's, ZFS's on a small one) are skipped here.
    among() {  # VAR ALL SHARE: VAR's steps (all: ALL's) that SHARE has
        local v=$1 want=${!1} x="" f
        [[ $want == all ]] && want=$2
        for f in $want; do
            [[ " $2 " == *" $f "* ]] || die "$v: no such step: $f"
            if [[ " $3 " == *" $f "* ]]; then x+=" $f"
            else echo "run-all: $v $f is another host's share of SPLIT ($split): skipped"; fi
        done
        echo "=${x# }"
    }
    if [[ -n ${LINUX_FS:-} ]]; then x=$(among LINUX_FS "$ALL_LFS" "$lfs") || exit 2
        grep -v '^=' <<<"$x" || :; lfs=$(sed -n 's/^=//p' <<<"$x"); fi
    if [[ -n ${SPARSE_FS:-} ]]; then x=$(among SPARSE_FS "$ALL_SFS" "$sfs") || exit 2
        grep -v '^=' <<<"$x" || :; sfs=$(sed -n 's/^=//p' <<<"$x"); fi
    LINUX_FS="" SPARSE_FS=""   # the share's, chosen among: lfs and sfs
    all=""; [[ -n $lfs ]] && all+=" linux"; [[ -n $sfs ]] && all+=" sparse"
    all="${all# } $(sed -n 's/^plats *//p' <<<"$share")" all=${all# } all=${all% }
    split_lost=$(grep '^lost ' <<<"$share")
fi
[[ -n ${ANY_HOST:-} ]] && lfs=all sfs=all
LINUX_FS=${LINUX_FS:-$lfs} SPARSE_FS=${SPARSE_FS:-$sfs}
[[ $LINUX_FS == all ]] && LINUX_FS=$ALL_LFS
[[ $SPARSE_FS == all ]] && SPARSE_FS=$ALL_SFS
for f in $LINUX_FS; do [[ " $ALL_LFS " == *" $f "* ]] || die "LINUX_FS: no such step: $f"; done
for f in $SPARSE_FS; do [[ " $ALL_SFS " == *" $f "* ]] || die "SPARSE_FS: no such step: $f"; done
plats=${plats//,/ }
[[ -z $pset || $plats == all ]] && plats=$all
mine=""
for p in $plats; do
    case " linux sparse zfsguests dragonfly openbsd_illumos freebsd netbsd " in
        *" $p "*) ;; *) die "no such platform: $p" ;; esac
    if [[ " $all " == *" $p "* || -n ${ANY_HOST:-} ]]; then mine+=" $p"
    elif [[ -n $split ]]; then echo "run-all: $p is another host's share of SPLIT ($split): skipped"
    else echo "run-all: $p is the other host's: skipped (ANY_HOST=1 runs it here)"; fi
done
plats=${mine# }
[[ -n $plats || -n $split_lost ]] || { echo "run-all: nothing to run on $host${split:+ (its share of SPLIT: none)}"; exit 0; }
HOST_JOBS=${HOST_JOBS:-4} SPARSE_JOBS=${SPARSE_JOBS:-2} JOBS_CAP=${JOBS_CAP:-0}
[[ $HOST_JOBS =~ ^[1-9][0-9]*$ && $SPARSE_JOBS =~ ^[1-9][0-9]*$ && $JOBS_CAP =~ ^[0-9]+$ ]] ||
    die "HOST_JOBS and SPARSE_JOBS: 1 or more; JOBS_CAP: 0 or more"
if (( JOBS_CAP > 0 )); then
    (( HOST_JOBS > JOBS_CAP )) && { echo "run-all: HOST_JOBS $HOST_JOBS -> $JOBS_CAP on $host (JOBS_CAP)"; HOST_JOBS=$JOBS_CAP; }
    (( SPARSE_JOBS > JOBS_CAP )) && { echo "run-all: SPARSE_JOBS $SPARSE_JOBS -> $JOBS_CAP on $host (JOBS_CAP)"; SPARSE_JOBS=$JOBS_CAP; }
fi
PFX=${RUN_PREFIX:-bkora}
[[ $PFX =~ ^bko[a-z0-9]+$ ]] || die "RUN_PREFIX must be bko followed by letters or digits"
SRCWORK=${BKO_WORK:-$HOME/backotter-work}
[[ -d $SRCWORK/vms ]] || die "no $SRCWORK/vms (set BKO_WORK)"
OLD=${OLD_BIN:-}
[[ -n $OLD ]] || die "OLD_BIN unset: the round before's bin directory (SPEC3 2's identity), or OLD_BIN=none"
[[ $OLD == none ]] && OLD=""
[[ -z $OLD || -d $OLD ]] || die "no OLD_BIN directory $OLD"
[[ -n $OLD ]] && OLD=$(cd "$OLD" && pwd)
C=$here W=$LOG/work
notes=() lost=()
allowed() { [[ ",${ALLOW_SKIP:-}," == *",$1,"* ]]; }
lose() {  # PLATFORM STEP WHY: a check that won't run, a FAIL row unless allowed
    if allowed "$2"; then notes+=("$1/$2 not run (ALLOW_SKIP): $3"); else lost+=("$1"$'\t'"$2"$'\t'"$3"); fi
}
P() { echo $((PORT_BASE + $1)); }
# What SPLIT gives no host: a FAIL row each, on the split's first host.
while read -r _ k n why; do
    [[ -n ${n:-} ]] || continue
    case $k in linux) lose linux "$n" "$why" ;; sparse) lose sparse "sparse-$n" "$why" ;; *) lose "$n" all "$why" ;; esac
done <<<"$split_lost"
noguest() {  # PLATFORM STEP GUEST: under SPLIT, a guest with no image here is that step lost (true)
    [[ -n $split && ! -e $SRCWORK/vms/$3 ]] || return 1
    lose "$1" "$2" "no $SRCWORK/vms/$3 on $host (SPLIT gave this host $1; a cache host's image may have been evicted)"
}

# --- the binaries -------------------------------------------------------------
need_os=""
for p in $plats; do
    case $p in
        linux|sparse|zfsguests) need_os+=" linux" ;;
        dragonfly) need_os+=" dragonfly" ;;
        openbsd_illumos) need_os+=" openbsd illumos" ;;
        freebsd) need_os+=" freebsd" ;;
        netbsd) need_os+=" netbsd" ;;
    esac
done
need_os=$(printf '%s\n' $need_os | sort -u | tr '\n' ' ')
bad=() seen_restic="" seen_rustic="" seen_core="" seen_digest="" first=""
prefix_ok() { [[ -z $2 || ( ${#2} -ge 7 && $1 == "$2"* ) ]]; }  # ACTUAL EXPECTED
agree() {  # NAME VAR VALUE: VAR holds the first OS's value, empty or not
    local n=$1 v=$3
    if [[ " $first " != *" $2 "* ]]; then printf -v "$2" %s "$v"; first+=" $2"
    elif [[ ${!2} != "$v" ]]; then bad+=("$n differs between OSes: '${!2}' and '$v'"); fi
}
echo "== binaries in $BIN ($host: $need_os)"
for os in $need_os; do
    for t in restic rustic; do
        f=$BIN/$os/$t-bko
        if [[ ! -s $f ]]; then bad+=("no $f"); continue; fi
        if [[ $os == linux && $(uname -s) == Linux ]]; then
            if [[ $t == restic ]]; then v=$("$f" version 2>&1 | head -n 1); else v=$("$f" --version 2>&1 | head -n 1); fi
        else
            v=$(grep -aoE "[0-9]+\.[0-9]+\.[0-9]+\+backotter\.[0-9]+" "$f" | sort -u | tr '\n' ' ')
        fi
        ev=EXPECT_${t^^}_VERSION
        [[ -z ${!ev:-} || " $v " == *"${!ev}"[!0-9]* ]] || bad+=("$f: version $v, not ${!ev}")
        echo "   $os/$t-bko: $v"
        cf=$BIN/$os/$t-commit
        if [[ ! -s $cf ]]; then bad+=("no $cf"); continue; fi
        c=$(head -n 1 "$cf" | cut -d' ' -f1)
        if [[ $t == restic ]]; then
            d=$(sed -n 's/^source digest //p' "$cf")
            [[ -n $d ]] || bad+=("$cf: no source digest line")
            agree "restic commit" seen_restic "$c"; agree "restic source digest" seen_digest "$d"
            prefix_ok "$c" "${EXPECT_RESTIC_COMMIT:-}" || bad+=("$cf: restic $c, not ${EXPECT_RESTIC_COMMIT}")
            [[ -z ${EXPECT_RESTIC_DIGEST:-} || $d == "$EXPECT_RESTIC_DIGEST" ]] ||
                bad+=("$cf: source digest $d, not $EXPECT_RESTIC_DIGEST")
            echo "      restic $c, source digest ${d:-none}"
        else
            k=$(sed -n 's/^rustic_core \([0-9a-f]*\).*/\1/p' "$cf")
            [[ -n $k ]] || bad+=("$cf: no rustic_core commit line")
            agree "rustic commit" seen_rustic "$c"; agree "rustic_core commit" seen_core "$k"
            prefix_ok "$c" "${EXPECT_RUSTIC_COMMIT:-}" || bad+=("$cf: rustic $c, not ${EXPECT_RUSTIC_COMMIT}")
            prefix_ok "$k" "${EXPECT_RUSTIC_CORE_COMMIT:-}" ||
                bad+=("$cf: rustic_core $k, not ${EXPECT_RUSTIC_CORE_COMMIT}")
            echo "      rustic $c, rustic_core ${k:-none}"
        fi
        if [[ -f $BIN/SHA256SUMS ]] && grep -q " \./$os/$t-bko\$" "$BIN/SHA256SUMS"; then
            (cd "$BIN" && grep " \./$os/$t-bko\$" SHA256SUMS | sha256sum -c --quiet >/dev/null 2>&1) ||
                bad+=("$f: not the binary SHA256SUMS lists")
        fi
    done
done
if [[ -z ${EXPECT_RESTIC_COMMIT:-} || -z ${EXPECT_RUSTIC_COMMIT:-} || -z ${EXPECT_RUSTIC_CORE_COMMIT:-} ]]; then
    if [[ ${UNCHECKED_BINARIES:-} == 1 ]]; then
        notes+=("binaries not checked against the Mac's commits (UNCHECKED_BINARIES=1); they agree with each other")
    else
        bad+=("EXPECT_RESTIC_COMMIT, EXPECT_RUSTIC_COMMIT and EXPECT_RUSTIC_CORE_COMMIT unset (UNCHECKED_BINARIES=1 runs anyway)")
    fi
fi
# The harness this needs (round 9's): a copy without it fails every row.
for x in nonroot-rt.sh:NONROOT_MNT fs_roundtrip.py:BKO_OLD_RESTIC fs_roundtrip.py:sparse_blocks \
    zfs-guest-rt.sh:OLD_RUSTIC sparse-rt.sh:SPARSE_ZFS_OPTS guest-slot.sh:slot_take; do
    grep -q "${x#*:}" "$C/${x%%:*}" 2>/dev/null || bad+=("$C/${x%%:*} has no ${x#*:}: not this run-all.sh's harness")
done
oldpair() {  # OS: OLD_BIN/OS's two binaries, if both are there
    [[ -n $OLD && -s $OLD/$1/restic-bko && -s $OLD/$1/rustic-bko ]]
}
if [[ -z $OLD ]]; then notes+=("OLD_BIN=none: snapshot identity (SPEC3 2) not checked")
else
    for os in $need_os; do oldpair "$os" || bad+=("no $OLD/$os/restic-bko or rustic-bko: SPEC3 2's identity (OLD_BIN=none: none)"); done
fi
if [[ ${#bad[@]} -gt 0 ]]; then
    printf 'run-all: refused: %s\n' "${bad[@]}" >&2
    [[ -n $dry ]] || exit 2
fi

# --- the steps ----------------------------------------------------------------
# step PLATFORM CHAIN NAME KIND GUESTS SEMAPHORE TIMEOUT ROOT JUDGE-ARGS CMD
# CMD is a bash snippet, run in LOG_DIR/PLATFORM.  Steps of a chain run in
# order; chains run at once.
S_PLAT=() S_CHAIN=() S_NAME=() S_KIND=() S_GUESTS=() S_SEM=() S_TO=() S_ROOT=() S_JARGS=() S_CMD=()
step() {
    [[ -z $only ]] || grep -qE -- "$only" <<<"$1/$3" || return 0
    S_PLAT+=("$1") S_CHAIN+=("$1/$2") S_NAME+=("$3") S_KIND+=("$4") S_GUESTS+=("$5") S_SEM+=("$6")
    S_TO+=("${STEP_TIMEOUT:-$7}") S_ROOT+=("$8") S_JARGS+=("$9") S_CMD+=("${10}")
}
q() { printf '%q ' "$@"; }
js() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\t'/\\t}; printf '"%s"' "$s"; }
if [[ $(id -u) == 0 ]]; then SUDO=""; else SUDO="sudo -n"; fi
ID=${BKO_RUNALL_ID:-$PFX-$$-$(date +%s)}
export BKO_RUNALL_ID=$ID BKO_WORK=$W RAM_DIR=${RAM_DIR:-$W/ram}
# sudo keeps none of the environment: RT_RAM and PYTHONDONTWRITEBYTECODE,
# when set, go on to the root steps (GateTest2's loop.sh sets both: RAM only,
# and no root-owned __pycache__ in its copy's testvm/compare).
renv=""
[[ -z ${RT_RAM:-} ]] || renv+=" RT_RAM=$(printf %q "$RT_RAM")"
[[ -z ${PYTHONDONTWRITEBYTECODE:-} ]] || renv+=" PYTHONDONTWRITEBYTECODE=$(printf %q "$PYTHONDONTWRITEBYTECODE")"
rootenv() { echo "$SUDO env BKO_RUNALL_ID=$ID BKO_WORK=$W$renv"; }

guest_rt() {  # PLATFORM CHAIN GUEST PORT OS
    local n=4 to=14400
    noguest "$1" "rt-$3" "$3" && return
    [[ $3 == omnios* || $3 == oi* ]] && n=8 to=21600
    step "$1" "$2" "rt-$3" guest-rt 1 - $to "" "--expect $n" \
        "RT_LOGS=$(q "$LOG/$1/rt") $(q "$C/run-guest-rt.sh" "$3" "$4" "$BIN/$5/restic-bko" "$BIN/$5/rustic-bko")"
}
nonroot() {  # PLATFORM CHAIN GUEST PORT OS
    noguest "$1" "nonroot-$3" "$3" && return
    step "$1" "$2" "nonroot-$3" nonroot 1 - 5400 "" "$(q --targets "$3" --full "$LOG/$1/nonroot/$3.log")$base" \
        "rm -f $(q "$LOG/$1/nonroot/$3.log")
        NONROOT_LOGS=$(q "$LOG/$1/nonroot") RESTIC=$(q "$BIN/$5/restic-bko") RUSTIC=$(q "$BIN/$5/rustic-bko") $(q "$C/nonroot-rt.sh" "$3" "$4")"
}
guest_sparse() {  # PLATFORM CHAIN GUEST PORT OS [ENV...]
    local p=$1 ch=$2 g=$3 port=$4 os=$5 files args="/root/restic-bko /root/rustic-bko"
    shift 5
    noguest "$p" "sparse-$g" "$g" && return
    files=("$C/sparse-rt.sh" "$C/rt-common.sh" "$BIN/$os/restic-bko" "$BIN/$os/rustic-bko")
    if oldpair "$os"; then
        files+=("$W/old/$os/restic-bko-old" "$W/old/$os/rustic-bko-old")
        args+=" /root/restic-bko-old /root/rustic-bko-old"
    fi
    step "$p" "$ch" "sparse-$g" sparse 1 - 11400 "" "$(oldpair "$os" && echo --identity)" \
        "$* T=10800 $(q "$C/guest-run.sh" "$g" "$port" "cd /root && BIG=${GUEST_BIG:-2} sh sparse-rt.sh $args" "${files[@]}")"
}

base=${BASELINE:+ $(q --baseline "$BASELINE")}
for p in $plats; do
    case $p in
    linux)
        for n in $LINUX_FS; do
            [[ $n == nonroot ]] && continue
            fs=${n/-onto-/ --onto }
            # bcachefs format sometimes aborts in malloc (round 8): again.
            step linux "$n" "$n" fsrt 0 host 14400 1 \
                "$(q --results "$LOG/linux/$n" --target "$n" --allow-skip "${ALLOW_SKIP:-}")${OLD:+ --identity}" "
                t=\$(mktemp); for i in 1 2 3 4 5; do
                    $(rootenv) RESTIC_BKO=$(q "$BIN/linux/restic-bko") RUSTIC_BKO=$(q "$BIN/linux/rustic-bko") \
                        ${OLD:+BKO_OLD_RESTIC=$(q "$OLD/linux/restic-bko")} BKO_FSRT_PREFIX=$PFX-fs \
                        BKO_MKFS_BCACHEFS=\"\${BKO_MKFS_BCACHEFS:-}\" python3 $(q "$C/fs_roundtrip.py") $fs 2>&1 | tee \$t
                    rc=\${PIPESTATUS[0]}; grep -q 'failed (-6): malloc' \$t || break; echo \"== again (\$i): malloc abort\"
                done; rm -f \$t
                r=$(q "$here/../../fs-roundtrip/$PFX-fs/$n")
                mkdir -p $n && $SUDO sh -c \"cp \$r/*.json $n/ 2>/dev/null; chown -R $(id -u):$(id -g) $n\"
                [ -n \"\${KEEP_WORK:-}\" ] || $SUDO rm -rf \"\$r\"
                exit \$rc"
        done
        # The pool and mount points are this run's (RUN_PREFIX).
        [[ " $LINUX_FS " == *" nonroot "* ]] &&
        step linux nonroot nonroot-linux nonroot 0 host 5400 1 \
            "$(q --targets "linux zfs,linux ext4" --full "$LOG/linux/nonroot/linux.log")$base" "
            $SUDO rm -f $(q "$LOG/linux/nonroot/linux.log")
            $(rootenv) NONROOT_POOL=$PFX-nr NONROOT_MNT=/mnt/$PFX-nr NONROOT_LOGS=$(q "$LOG/linux/nonroot") \
                RESTIC=$(q "$BIN/linux/restic-bko") RUSTIC=$(q "$BIN/linux/rustic-bko") $(q "$C/nonroot-rt.sh") linux"
        ;;
    sparse)
        for run in $SPARSE_FS; do
            fs=${run%-nocomp} opts=""; [[ $run == zfs-nocomp ]] && opts="-O compression=off"
            step sparse "$run" "sparse-$run" sparse 0 sparse 10800 1 "${OLD:+--identity}" \
                "$(rootenv) SPARSE_PREFIX=$PFX-sp SPARSE_FS=$fs SPARSE_RUN=$run SPARSE_ZFS_OPTS=$(q "$opts") BIG=${BIG:-20} ${IMG_GB:+IMG_GB=$(q "$IMG_GB")} \
                    $(q "$C/sparse-rt.sh" "$BIN/linux/restic-bko" "$BIN/linux/rustic-bko") \
                    ${OLD:+$(q "$OLD/linux/restic-bko" "$OLD/linux/rustic-bko")}"
        done ;;
    zfsguests)
        i=17
        for g in ubuntu24 ubuntu26-zfsmirror; do
            noguest zfsguests "zfs-$g" "$g" && { i=$((i + 1)); continue; }
            step zfsguests "$g" "zfs-$g" zfsguest 1 - 21600 "" "$(q --results "$LOG/zfsguests/zfs-$g")${OLD:+ --identity}" \
                "BKO_BIN=$(q "$BIN") ${OLD:+OLD_RESTIC=$(q "$OLD/linux/restic-bko") OLD_RUSTIC=$(q "$OLD/linux/rustic-bko")} \
                    ZFS_GUEST_LOGS=$(q "$LOG/zfsguests/zfs-$g") $(q "$C/zfs-guest-rt.sh" "$g" "$(P $i)")"
            i=$((i + 1))
        done ;;
    dragonfly)
        guest_rt dragonfly a dragonfly6 "$(P 10)" dragonfly
        nonroot dragonfly b dragonfly6 "$(P 11)" dragonfly
        guest_sparse dragonfly b dragonfly6 "$(P 11)" dragonfly ;;
    openbsd_illumos)
        guest_rt openbsd_illumos a openbsd8 "$(P 12)" openbsd
        nonroot openbsd_illumos a openbsd8 "$(P 12)" openbsd
        guest_rt openbsd_illumos b omnios58 "$(P 13)" illumos
        nonroot openbsd_illumos b omnios58 "$(P 13)" illumos
        guest_sparse openbsd_illumos b omnios58 "$(P 13)" illumos ;;
    freebsd)
        i=10
        for g in freebsd15 freebsd15-ufs; do
            guest_rt freebsd "$g" "$g" "$(P $i)" freebsd
            nonroot freebsd "$g" "$g" "$(P $i)" freebsd
            guest_sparse freebsd "$g" "$g" "$(P $i)" freebsd
            i=$((i + 1))
        done ;;
    netbsd)
        guest_rt netbsd a netbsd11 "$(P 15)" netbsd
        nonroot netbsd a netbsd11 "$(P 15)" netbsd
        # netbsd11 has no cc or python3: its build image has cc.
        img=$SRCWORK/vms-bko-build/netbsd11-rust.qcow2
        # Its key beside it, when build-rustic-native.sh kept one (the
        # guest's own may be newer than the image).
        key=$SRCWORK/vms-bko-build/netbsd11.id_ed25519
        if [[ -f $img && -f $key ]]; then
            guest_sparse netbsd a netbsd11 "$(P 15)" netbsd "VM_IMAGE=$(q "$img") VM_IMAGE_KEY=$(q "$key")"
        elif [[ -f $img ]]; then guest_sparse netbsd a netbsd11 "$(P 15)" netbsd "VM_IMAGE=$(q "$img")"
        else lose netbsd sparse-netbsd11 "no $img: sparse-rt.sh not run on NetBSD"; fi
        guest_rt netbsd b alpinebsd "$(P 16)" netbsd
        nonroot netbsd b alpinebsd "$(P 16)" netbsd ;;
    esac
done
[[ ${#S_NAME[@]} -gt 0 || ( -n $split && ${#lost[@]} -gt 0 ) ]] || die "no steps (--only $only)"
roots=""
for i in "${!S_NAME[@]}"; do [[ -n ${S_ROOT[$i]} ]] && roots=1; done
if [[ -n $roots && -n $SUDO && -z $dry ]]; then
    sudo -n true 2>/dev/null || die "the linux and sparse steps need root or sudo -n"
fi
: "${BKO_MKFS_BCACHEFS:=}"
A=${BKO_ARCH_ROOT:-/bkoscratch/tools/arch-root}
if [[ -z $BKO_MKFS_BCACHEFS && -x $A/usr/bin/bcachefs ]]; then
    # Chopin has no mkfs.bcachefs: the Arch ISO's, with its own libraries.
    BKO_MKFS_BCACHEFS="env GLIBC_TUNABLES=glibc.malloc.tcache_count=0 $A/usr/lib/ld-linux-x86-64.so.2 --library-path $A/usr/lib $A/usr/bin/bcachefs format"
fi
export BKO_MKFS_BCACHEFS

if [[ -n $dry ]]; then
    [[ -z $split ]] || echo "== SPLIT $split: $host's share: ${plats:-none}"
    echo "== $host: ports $PORT_BASE+, at most $MAX_GUESTS guests (others counted: $COUNT_OTHERS, $GUEST_PORTS)," \
        "$HOST_JOBS host jobs, $SPARSE_JOBS sparse jobs; prefix $PFX; OLD_BIN ${OLD:-none}; linux: $LINUX_FS; sparse: $SPARSE_FS"
    for i in "${!S_NAME[@]}"; do
        echo "-- ${S_CHAIN[$i]}: ${S_NAME[$i]} (${S_KIND[$i]}, guests ${S_GUESTS[$i]}, slot ${S_SEM[$i]}," \
            "timeout ${S_TO[$i]} s${S_ROOT[$i]:+, root}) -> $LOG/${S_PLAT[$i]}/${S_NAME[$i]}.log"
        sed 's/^ */    /' <<<"${S_CMD[$i]}" | grep -v '^ *$'
        echo "    judged: verdict.py judge ${S_KIND[$i]} LOG ${S_JARGS[$i]}"
    done
    [[ ${#notes[@]} -eq 0 ]] || printf 'note: %s\n' "${notes[@]}"
    [[ ${#lost[@]} -eq 0 ]] || printf 'not run (a FAIL row): %s\n' "${lost[@]}"
    [[ ${#bad[@]} -eq 0 ]] || { echo "run-all: would refuse (above)"; exit 2; }
    exit 0
fi

# --- running ------------------------------------------------------------------
mkdir -p "$LOG" "$W/ram" "$LOG/.sem" "$LOG/rows" || die "can't make $LOG"
exec 8>"/tmp/run-all-$PFX.lock"
flock -n 8 || die "another run-all.sh with RUN_PREFIX=$PFX is running"
rm -f "$LOG"/rows/* "$LOG"/.sem/* "$LOG/rows.jsonl"
for d in vms vms-bko-build netbsd-tools; do
    [[ -e $SRCWORK/$d && ! -e $W/$d ]] && ln -s "$SRCWORK/$d" "$W/$d"
done
for os in $need_os; do
    if oldpair "$os"; then
        mkdir -p "$W/old/$os"
        ln -sf "$OLD/$os/restic-bko" "$W/old/$os/restic-bko-old"; ln -sf "$OLD/$os/rustic-bko" "$W/old/$os/rustic-bko-old"
    fi
done

tagged() {  # the PIDs with our BKO_RUNALL_ID (the scan itself has none)
    env -u BKO_RUNALL_ID ${roots:+$SUDO} sh -c 'for e in /proc/[0-9]*/environ; do
        grep -qzx "BKO_RUNALL_ID=$1" "$e" 2>/dev/null && { p=${e#/proc/}; echo "${p%/environ}"; }; done' - "$ID"
}
cleaned="" chain_pids=()
cleanup() {
    [[ -z $cleaned ]] || return; cleaned=1
    trap '' INT TERM
    local pids n
    # The chains first (forks of this shell, untagged), so that none starts
    # another step.
    [[ ${#chain_pids[@]} -eq 0 ]] || kill -TERM "${chain_pids[@]}" 2>/dev/null
    pids=$(tagged)
    if [[ -n $pids ]]; then
        echo "run-all: stopping $(wc -w <<<"$pids") processes of this run" >&2
        ${roots:+$SUDO} kill -TERM $pids 2>/dev/null
        # The scripts' EXIT traps power their guests off (up to a minute).
        for n in $(seq 45); do pids=$(tagged); [[ -n $pids ]] || break; sleep 2; done
        [[ -z $pids ]] || ${roots:+$SUDO} kill -KILL $pids 2>/dev/null
    fi
    if [[ -n $roots ]]; then
        $SUDO sh -c '
            P=$1
            zpool list -H -o name 2>/dev/null | grep "^$P-" | while read -r z; do zpool destroy -f "$z"; done
            findmnt -rn -o TARGET | grep -E "^/mnt/$P-(fs|sp|nr-ext4|nr-zfs)(/|\$)" | sort -r | while read -r m; do umount -l "$m"; done
            losetup -a 2>/dev/null | grep -E "\((/var/tmp/$P-(fs|sp)|$2)" | cut -d: -f1 | while read -r l; do losetup -d "$l"; done
            rm -rf /var/tmp/$P-fs-*.img /var/tmp/$P-sp /mnt/$P-fs /mnt/$P-sp /mnt/$P-nr-ext4 /mnt/$P-nr-zfs "$2"' \
            - "$PFX" "$LOG/linux/nonroot/work" 2>/dev/null
        # fs_roundtrip.py's repositories, of a step stopped before it removed them.
        [[ -n ${KEEP_WORK:-} ]] || $SUDO rm -rf "$here/../../fs-roundtrip/$PFX-fs"
    fi
    rm -rf "$W/ram"
}
trap cleanup EXIT
trap 'trap "" INT TERM HUP; echo "run-all: interrupted" >&2; exit 130' INT TERM HUP

# A semaphore of this run: NAME N MAX.  Guests with COUNT_OTHERS=1: a
# host-wide slot (guest-slot.sh) instead, one a step.
# shellcheck source=guest-slot.sh
. "$C/guest-slot.sh"
acquire() {
    local f=$LOG/.sem/$1
    if [[ $1 == guests && $COUNT_OTHERS == 1 ]]; then slot_take "$3" "$GUEST_PORTS"; return; fi
    until (flock 9; c=$(cat "$f" 2>/dev/null || echo 0)
           [[ $((c + $2)) -le $3 ]] || exit 1
           echo $((c + $2)) >"$f") 9>"$f.lock"; do
        sleep 10
    done
}
release() {
    local f=$LOG/.sem/$1
    if [[ $1 == guests && $COUNT_OTHERS == 1 ]]; then slot_give; return; fi
    (flock 9; c=$(cat "$f" 2>/dev/null || echo 0); echo $((c - $2)) >"$f") 9>"$f.lock"
}
max_of() { case $1 in guests) echo "$MAX_GUESTS" ;; host) echo "$HOST_JOBS" ;; sparse) echo "$SPARSE_JOBS" ;; esac; }

BOOTFAIL="didn't come up|QEMU exited|port [0-9]+ is in use"
run_step() {  # INDEX
    local i=$1 p=${S_PLAT[$1]} n=${S_NAME[$1]} log rc t0 wall try=1
    log=$LOG/$p/$n.log
    mkdir -p "$LOG/$p"
    [[ ${S_GUESTS[$i]} -gt 0 ]] && acquire guests "${S_GUESTS[$i]}" "$MAX_GUESTS"
    [[ ${S_SEM[$i]} != - ]] && acquire "${S_SEM[$i]}" 1 "$(max_of "${S_SEM[$i]}")"
    echo "$(date +%T) start ${S_CHAIN[$i]}: $n"
    t0=$SECONDS
    while :; do
        (cd "$LOG/$p" && exec timeout -k 120 "${S_TO[$i]}" bash -c "${S_CMD[$i]}") >"$log" 2>&1 </dev/null
        rc=$?
        # A guest that didn't come up: once more (round 9: ubuntu26-zfsmirror).
        if [[ ${S_GUESTS[$i]} -gt 0 && $try -lt 2 && $rc != 124 ]] && grep -qE "$BOOTFAIL" "$log"; then
            mv "$log" "$log.try$try"; try=$((try + 1)); sleep 30; continue
        fi
        break
    done
    wall=$((SECONDS - t0))
    [[ ${S_SEM[$i]} != - ]] && release "${S_SEM[$i]}" 1
    [[ ${S_GUESTS[$i]} -gt 0 ]] && release guests "${S_GUESTS[$i]}"
    local rows=$LOG/rows/$(printf %03d "$i").jsonl jargs jrc
    eval "jargs=(${S_JARGS[$i]})"
    python3 "$C/verdict.py" judge "${S_KIND[$i]}" "$log" --platform "$p" --step "$n" --rc "$rc" --wall "$wall" \
        "${jargs[@]}" >"$rows" 2>"$rows.err"
    jrc=$?
    if [[ $jrc != 0 || ! -s $rows ]]; then  # no verdict is a FAIL, never a missing row
        printf '{"platform": %s, "step": %s, "target": "", "direction": "", "verdict": "FAIL", "wall": "%s", "checks": [%s, %s], "log": %s}\n' \
            "$(js "$p")" "$(js "$n")" "$wall" "$(js "verdict.py judge exit $jrc, no rows (step exit $rc)")" \
            "$(js "$(tail -n 3 "$rows.err" | tr '\n' ' ')")" "$(js "$log")" >>"$rows"
    fi
    rm -f "$rows.err"
    echo "$(date +%T) done  ${S_CHAIN[$i]}: $n: exit $rc, $wall s, $(grep -vc '"verdict": "PASS"' "$rows") not passed"
}
run_chain() {  # CHAIN
    local i
    for i in "${!S_NAME[@]}"; do [[ ${S_CHAIN[$i]} == "$1" ]] && run_step "$i"; done
}

t_start=$SECONDS
echo "== $host $(date -u '+%F %T UTC'): ${#S_NAME[@]} steps of $plats; logs in $LOG; run id $ID"
chains=$(printf '%s\n' "${S_CHAIN[@]}" | awk '!seen[$0]++')
for ch in $chains; do run_chain "$ch" & chain_pids+=($!); sleep 1; done
wait
chain_pids=()
cat "$LOG"/rows/*.jsonl >"$LOG/rows.jsonl" 2>/dev/null
{
    printf '{"host": %s, "platforms": %s, "bin": %s, "old_bin": %s, "wall": %s, "id": %s,\n' \
        "$(js $host)" "$(js "$plats")" "$(js "$BIN")" "$(js "$OLD")" $((SECONDS - t_start)) "$(js "$ID")"
    printf ' "restic": %s, "restic_digest": %s, "rustic": %s, "rustic_core": %s, "notes": [' \
        "$(js "$seen_restic")" "$(js "$seen_digest")" "$(js "$seen_rustic")" "$(js "$seen_core")"
    sep=""; for n in "${notes[@]}"; do printf '%s%s' "$sep" "$(js "$n")"; sep=", "; done
    printf '],\n "steps": ['
    sep=""; for i in "${!S_NAME[@]}"; do printf '%s%s' "$sep" "$(js "${S_PLAT[$i]}/${S_NAME[$i]}")"; sep=", "; done
    printf '],\n "lost": ['
    sep=""
    for x in "${lost[@]}"; do
        IFS=$'\t' read -r a b c <<<"$x"
        printf '%s{"platform": %s, "step": %s, "why": %s}' "$sep" "$(js "$a")" "$(js "$b")" "$(js "$c")"; sep=", "
    done
    printf ']}\n'
} >"$LOG/meta.json"
python3 "$C/verdict.py" summary "$LOG"
