#!/usr/bin/env bash
# img-round.sh: the image round's dealer, on the Mac (docs/design/img-speed.md §5.2).
#
#   img-round.sh plan   [--hosts H,H|all] [--full P,P|all] [--no-refresh] [--ref HOST]
#                       [--remote-env K=V]... [--today YYYY-MM-DD] [--json] RUN
#   img-round.sh run    [same options] [--to H,H|all] [--jobs N] RUN
#   img-round.sh fanout [--to H,H|all] [--jobs N] RUN
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
#
# run (I8, §5.2 steps 4-5, §10, C10) holds the round lock from its facts to the end of its
# fan-out: BKO_ROUND_DIR/.round.lock here, and images/.round.lock on every host it uses
# (held by a holder over ssh: it goes when the round does), so that promote-bins.sh and
# img-refresh-holt.sh refuse those hosts meanwhile; a host whose lock is held refuses the
# round.  It plans as plan does, then each host runs `img-lanes.sh --run RUN` for its units
# (its lanes; a refresh is P@PARENT-SHA256), every unit started with the plan's bko-bin and
# holt-bin SHA256SUMS digests and builder identity (img-one.sh refuses a host whose sets
# moved), its output in RUN/HOST.log.  Then the fan-out: every platform that passed, from its
# builder to every other --to host (D12: every enabled amd64 host), once that host's holt-bin
# is the plan's or the image's and its bko-bin the image's; then every holder's copy hashed
# again.  RUN/round.json and RUN/summary.txt: per platform the action, the host, the
# minutes, done.exit and the holders whose bytes are identical.  fanout does the fan-out
# alone for a run's plan.  Exit 0 when every unit passed and every copy is identical; 1
# otherwise; 2 bad usage; 3 refused (a lock held, the sets differ, no reference facts).
set -u
here=$(cd "$(dirname "$0")" && pwd)
case ${1:-} in
plan|run|fanout) exec python3 "$here/img_round.py" "$@" ;;
*) sed -n '2,/^set -u/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2 ;;
esac
