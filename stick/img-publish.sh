#!/usr/bin/env bash
# img-publish.sh: publish a set of rescue images for download, from the Mac
# (docs/design/img-speed.md §7, C8, D7).  The user starts it, or CarpetBeater's
# step; nothing publishes by itself after farm rebuilds.
#
#   img-publish.sh [--host H] [--only P,P] [--out DIR] [--today YYYY-MM-DD]
#                  [--remote-env K=V]... [--dry-run] SET
#
# The farm keeps its images raw (BKO_IMG_COMPRESS=0).  This picks a build host
# that is not Chopin (--host, else the least loaded one that holds every
# platform's image), refuses a platform whose current image didn't pass C4's
# rows (ImgTest2 and the +img panel, in the Mac's ledger) or whose OS epoch is
# past the 14-day cadence (D3), naming it, and then, there, copies each release
# image to images/publish/SET/P/ (or --out's), compresses it with xz -T0 under
# a reservation, checks that it decompresses to the raw image's SHA-256 and
# writes P/SHA256SUMS with both (stick/img-publish-host.sh).  images/P/ is
# only read.  stick/img_publish.py holds the rules.  Exit 0 when published (or
# publishable, with --dry-run); 1 when the host step failed; 2 on bad usage;
# 3 when refused.
set -u
here=$(cd "$(dirname "$0")" && pwd)
case ${1:-} in
''|-h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2 ;;
esac
exec python3 "$here/img_publish.py" "$@"
