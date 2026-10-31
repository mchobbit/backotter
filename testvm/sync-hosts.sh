#!/usr/bin/env bash
# Copy this repository's testvm/ and stick/ to the KVM test hosts, where the
# scripts run from ~/backotter-work: HOST:~/backotter-work/testvm and
# HOST:~/backotter-work/stick.  Run it here, from the checkout, after a
# change and before running anything on the hosts, so that every host runs
# the same scripts.
#
# Usage: sync-hosts.sh [--delete] [--dry-run] [HOST ...]
#
# HOST is an ssh destination, [USER@]NAME; the default is m@chopin.local and
# m@cinqcent.local.  Copied with rsync -rlp --checksum: what differs in
# content is copied and gets the time of the copy, and nothing goes by file
# times (never -t: Chopin's clock once ran two hours fast, which made
# rsync's time check skip changed files).  Files there that the repository
# doesn't have stay, unless --delete removes them (from testvm/ and stick/
# only: the rest of ~/backotter-work, vms, results and binaries, is never
# touched).  --dry-run lists what would change.  BKO_WORK_REMOTE names the
# directory on the hosts instead (relative to the login's home, or absolute).
#
# Needs: rsync (or macOS's openrsync) and ssh, here and on the hosts; bash
# 3.2 will do.

set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
remote=${BKO_WORK_REMOTE:-backotter-work}
die() { echo "sync-hosts: $*" >&2; exit 1; }

flags=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --delete) flags+=(--delete); shift ;;
        --dry-run) flags+=(--dry-run --itemize-changes); shift ;;
        -*) die "usage: sync-hosts.sh [--delete] [--dry-run] [HOST ...]" ;;
        *) break ;;
    esac
done
hosts=("$@")
[[ $# -gt 0 ]] || hosts=(m@chopin.local m@cinqcent.local)
[[ -d $repo/testvm && -d $repo/stick ]] || die "$repo has no testvm/ and stick/"

failed=()
for host in "${hosts[@]}"; do
    echo "== $host:$remote"
    # shellcheck disable=SC2029  # $remote is meant to expand here
    if ssh -o BatchMode=yes "$host" "mkdir -p '$remote'" &&
        rsync -rlp --checksum ${flags[@]+"${flags[@]}"} --exclude __pycache__ --exclude '*.pyc' \
            --exclude .DS_Store "$repo/testvm" "$repo/stick" "$host:$remote/"; then
        :
    else
        failed+=("$host")
    fi
done
[[ ${#failed[@]} -eq 0 ]] || die "failed: ${failed[*]}"
