#!/usr/bin/env bash
# One line per SSD of this host to /bkoscratch/wear/smart.log (WEAR_DIR),
# from its SMART data: what it has written in its life, and its wear.  Run
# hourly as root by bko-wear-smart.timer (wear-setup.sh installs it);
# wear-report.py turns the lines into writes per disk per day.
#
# smart.log, tab-separated:
#   UTC HOST DISK MODEL SERIAL BYTES_WRITTEN PERCENT_USED POWER_ON_HOURS UNSAFE_SHUTDOWNS
# (a field the disk doesn't report is "-").  NVMe counts written data in
# units of 512,000 bytes; SATA (Samsung's Total_LBAs_Written) in 512-byte
# sectors.
set -u
dir=${WEAR_DIR:-/bkoscratch/wear}
mkdir -p "$dir"
for d in /dev/nvme[0-9]n1 /dev/sd[a-z]; do
    [ -b "$d" ] || continue
    smartctl -j -a "$d" 2>/dev/null | python3 -I -c '
import json, sys, time, socket
d = json.load(sys.stdin)
def g(*p):
    v = d
    for k in p:
        if not isinstance(v, dict) or k not in v:
            return None
        v = v[k]
    return v
n = g("nvme_smart_health_information_log") or {}
w = pct = unsafe = None
if n:
    w = n.get("data_units_written")
    w = w * 512000 if w is not None else None
    pct, unsafe = n.get("percentage_used"), n.get("unsafe_shutdowns")
else:
    for a in g("ata_smart_attributes", "table") or []:
        if a.get("id") == 241:
            w = a["raw"]["value"] * 512
        elif a.get("id") == 177:
            pct = 100 - a.get("value", 100)
        elif a.get("id") == 235:
            unsafe = a["raw"]["value"]
if w is None:
    sys.exit(0)
f = lambda v: "-" if v is None else str(v)
print("\t".join([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), socket.gethostname().split(".")[0],
                 sys.argv[1].rsplit("/", 1)[-1], f(g("model_name")).replace(" ", "_"), f(g("serial_number")),
                 str(w), f(pct), f(g("power_on_time", "hours")), f(unsafe)]))
' "$d" >>"$dir/smart.log"
done
