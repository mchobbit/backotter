# SticTest1: where a check's scratch goes, RAM first and the SSD for overflow
# (docs/design/img-speed.md F9, FD4), as build-holt.sh --reserved places a
# HoltTest build's.  Sourced by bash, by the checks whose --reserve is over a
# small host's RAM-disk ceiling (debian13-zfs, ubuntu24, ubuntu26-zfs: 13 5;
# ubuntu26-zfsmirror: 23 MEM).  qubes43 (16 4) stays big-only.
#
#   stic_scratch DISK MEM   sets STIC_RES, the cmp-run.sh words to run the
#                           check's inner part under (--reserve DISK MEM, or
#                           --reserve-ssd DISK MEM), STIC_SCRATCH, the root its
#                           scratch goes under (its scratch is
#                           STIC_SCRATCH/PREFIX/GUEST), and STIC_WHERE, ram or
#                           ssd; it says on stderr when it is ssd, and why
#
# By this host's scratch_mode in the host table (SCRATCH_MODE overrides):
#   ram (or a table that can't say, or a cmp-run.sh with no --reserve-ssd,
#     one not synced yet): the RAM disk, as before;
#   overflow: the RAM disk when DISK fits it right now (cmp-run.sh --free's
#     "after" figures: DISK within its RAM disk, DISK + MEM within its memory),
#     else the SSD when cmp-run.sh --ssd-free has room for DISK there now,
#     else the RAM disk's line as before;
#   ssd: the SSD when it has room for DISK now, else the RAM disk.
# In either mode a DISK over this host's RAM-disk ceiling (cmp-run.sh
# --ceiling, which would refuse it at once) goes to the SSD whatever its room
# now: cmp-run.sh --reserve-ssd waits in the line for it (and refuses one that
# could never have it).  On the SSD the scratch is under STIC_SSD_ROOT
# (default /bkoscratch), so drive.py's sweep of a dead run's /bkoscratch/MARK-*
# takes it; the memory is held as for any job, the RAM disk not at all, and
# the wear log's line says scratch=ssd (cmp-run.sh).  STIC_CMP_RUN (default
# WORK/cmp-run.sh) and STIC_RAM_ROOT (/mnt/bko-ram) are for tests.
# drive.py's parse_check and rows_stic's check_reserve read DISK and MEM from
# the stic_scratch line, and drive.py offers such a check to a host whose
# scratch_mode lets it overflow even when DISK is over its RAM-disk ceiling.

stic_scratch() {
    local d=$1 m=$2 cmp=${STIC_CMP_RUN:-${WORK:-$HOME/backotter-work}/cmp-run.sh} hpy mode cd fr ad am sr
    local num='^[0-9]+([.][0-9]+)?$'
    STIC_RES=(--reserve "$d" "$m") STIC_WHERE=ram STIC_SCRATCH=${STIC_RAM_ROOT:-/mnt/bko-ram}
    hpy=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../test2/t2lib/hosts.py
    mode=${SCRATCH_MODE:-$(python3 -I -B "$hpy" limit "$(hostname -s)" scratch_mode 2>/dev/null)}
    case $mode in overflow|ssd) ;; *) return 0 ;; esac
    grep -q -- '--reserve-ssd' "$cmp" 2>/dev/null || return 0
    cd=$("$cmp" --ceiling 2>/dev/null) && cd=${cd%% *} && [[ $cd =~ $num ]] || cd=""
    sr=$("$cmp" --ssd-free 2>/dev/null) && [[ $sr =~ $num ]] || sr=0
    if [[ -n $cd ]] && awk -v d="$d" -v c="$cd" 'BEGIN { exit !(d + 0 > c + 0.001) }'; then
        STIC_WHY="--reserve $d $m is over this host's RAM-disk ceiling $cd"
    else
        awk -v s="$sr" -v d="$d" 'BEGIN { exit !(s + 0 >= d + 0) }' || return 0
        if [[ $mode == overflow ]]; then
            fr=$("$cmp" --free 2>/dev/null) && read -r _ ad am < <(sed -n '3p' <<<"$fr") &&
                [[ ${ad:-} =~ $num && ${am:-} =~ $num ]] &&
                awk -v ad="$ad" -v am="$am" -v d="$d" -v m="$m" 'BEGIN { exit !(d + 0 <= ad + 0 && d + m <= am + 0) }' &&
                return 0
            STIC_WHY="the RAM disk has no room for $d GiB now"
        else
            STIC_WHY="scratch_mode ssd"
        fi
    fi
    STIC_RES=(--reserve-ssd "$d" "$m") STIC_WHERE=ssd STIC_SCRATCH=${STIC_SSD_ROOT:-/bkoscratch}
    echo "check.sh: scratch on the SSD, under $STIC_SCRATCH ($STIC_WHY)" >&2
}
