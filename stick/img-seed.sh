#!/usr/bin/env bash
# img-seed.sh: the Linux image's inputs (the ISO and slim) on more build hosts, from the Mac
# (docs/design/img-speed.md §5.1, D5, D15).
#
#   img-seed.sh [--from HOST] [--check] [--media] [--builder] HOST...
#   img-seed.sh [--from HOST] [--check] [--media] --seeded
#
# Copies the source's (default Chopin's) current slim and its ISO into each HOST's
# /bkoscratch/imgbuild/server, as root with tar --numeric-owner --xattrs --acls, host to host;
# checks slim's manifest and the ISO against the source's before it points server/slim at it;
# checks the host tools (squashfs-tools >= 4.6, xorriso >= 1.5, dosfstools, mtools, each the
# source's version), sudo -n and HTTPS to the archives.  --seeded repoints every seeded build
# host after slim moved on the source.  --media mends dangling img-media/P links, --builder a
# dangling vms/freebsd15-builder (a checked copy of the source's).  --check changes nothing.
# stick/img_seed.py says the rest.  Exit 0 every host ready; 1 one is not; 2 bad usage; 3 the
# source is unusable.
set -u
here=$(cd "$(dirname "$0")" && pwd)
exec python3 "$here/img_seed.py" "$@"
