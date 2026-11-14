# The KVM test hosts' table, testvm/hosts.json, for the shell: what
# testvm/test2/t2lib/hosts.py prints, found beside this file (the checkout's
# testvm/, ~/backotter-work/testvm on the hosts, a private copy's testvm/).
# BKO_HOSTS_JSON names another table (tests).  Sourced by bash; 3.2 will do.
#
#   hosts_names [--all] [--default] [--role R] [--class C] [--arch A] [--field F]
#                         the enabled hosts' names (--all: every row), one a
#                         line, in the table's order (--field: that field;
#                         --arch: only those of architecture A, amd64 or arm64)
#   hosts_get NAME FIELD [--all]
#                         one field of NAME's row (NAME may be HOST.DOMAIN;
#                         a list: space-separated; null: an empty line)
#   hosts_phrase [--all]  "chopin, cinqcent, copland or cage", for messages
#   hosts_limit NAME LIMIT
#                         one of NAME's limits (stic_slots, guest_slots,
#                         holt_jobs, base_gib, holt_disk_mode): its row's,
#                         else its class's, else the default (a name in no
#                         row: the default)
#   hosts_arch NAME       NAME's architecture: amd64 (also when its row has no
#                         arch, or NAME is in no row) or arm64
#   hosts_guest_arch GUEST
#                         GUEST's architecture: arm64 for a name *-arm64, else amd64
#   hosts_job_prefix NAME [USER]
#                         the words that run a heavy job in NAME's job_slice,
#                         one a line (sudo -n systemd-run --scope --quiet
#                         --slice=SLICE -- setpriv --reuid=USER --regid=USER
#                         --init-groups --reset-env --), nothing when it has
#                         none: read them into an array and put CMD after them
#                         (--reset-env: env NAME=VALUE... first, for the
#                         environment CMD needs)
#
# Each returns hosts.py's status: 0, 1 no such host (or a reserved row,
# enabled false), 2 a bad table or usage; the reason goes to stderr.
#
# Needs: python3 (3.9 or newer).

BKO_HOSTS_PY=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/test2/t2lib/hosts.py

hosts_names() { python3 -I -B "$BKO_HOSTS_PY" names "$@"; }
hosts_get() { python3 -I -B "$BKO_HOSTS_PY" get "$@"; }
hosts_phrase() { python3 -I -B "$BKO_HOSTS_PY" phrase "$@"; }
hosts_limit() { python3 -I -B "$BKO_HOSTS_PY" limit "$@"; }
hosts_arch() { python3 -I -B "$BKO_HOSTS_PY" arch "$@"; }
hosts_guest_arch() { python3 -I -B "$BKO_HOSTS_PY" guest-arch "$@"; }
hosts_job_prefix() { python3 -I -B "$BKO_HOSTS_PY" job-prefix "$@"; }
