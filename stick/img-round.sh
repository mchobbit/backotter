#!/usr/bin/env bash
# img-round.sh: the image round's dealer, on the Mac (docs/design/img-speed.md §5.2).
#
#   img-round.sh plan   [--hosts H,H|all] [--full P,P|all] [--no-refresh] [--ref HOST]
#                       [--remote-env K=V]... [--today YYYY-MM-DD] [--json] RUN
#   img-round.sh run    [same options] RUN          (I8: not built yet)
#   img-round.sh fanout [--to H,H|all] RUN          (I8: not built yet)
#
# plan executes nothing: one ssh per build host for its facts (each platform's digests,
# its images' provenance, its room, its bko-bin and holt-bin sets), §3.3's action per
# platform against the Mac's ledger (C4), and the build units dealt over the hosts
# (stick/deal-units.py's image units).  It writes RUN/plan.json and RUN/facts.json (RUN:
# a name under BKO_ROUND_DIR, default ~/backotter-work/img-rounds, or a path) and prints
# the plan.  stick/img_round.py holds the rules and says the options.  Exit 0 with a
# plan; 1 when a unit has no host or the reference gave no digest of a platform; 2 on
# bad usage; 3 when the plan is refused (the sets differ between builders, or the
# reference host gave no facts).
set -u
here=$(cd "$(dirname "$0")" && pwd)
case ${1:-} in
plan) exec python3 "$here/img_round.py" "$@" ;;
run|fanout) echo "img-round: $1 is I8's (docs/design/img-speed.md §14): not built yet" >&2; exit 2 ;;
*) sed -n '2,/^set -u/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2 ;;
esac
