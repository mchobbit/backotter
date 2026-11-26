#!/usr/bin/env bash
# img-digest.sh PLATFORM: PLATFORM's three digests (base, bins, tests) and its
# input list, as JSON (docs/design/img-speed.md §3.2).  Reads only: never
# touches images/.  img-digest.sh --lint: every stick/ file is some
# platform's input or listed as none in stick/img-inputs.tsv (exit 1 if not).
# See stick/img_inputs.py for the environment it reads.
here=$(cd "$(dirname "$0")" && pwd)
export S=${S:-$here}
if [[ ${1:-} == --lint ]]; then exec python3 "$here/img_inputs.py" lint; fi
[[ $# -eq 1 ]] || { echo "usage: img-digest.sh PLATFORM | --lint" >&2; exit 2; }
exec python3 "$here/img_inputs.py" digest "$1"
