#!/usr/bin/env bash
# Install (or remove) the farm's telemetry to Bigfoot, from the Mac, at the
# repo root (testvm/telemetry/README.md).  Idempotent: run it again after
# changing a file here and it reinstalls.
#
#   testvm/telemetry/telemetry-setup.sh [--dry-run] [HOST...]
#   testvm/telemetry/telemetry-setup.sh [--dry-run] --uninstall HOST...
#
# HOST default: chopin cinqcent cage copland czerny choubox.
#
# Install:
#   1. Bigfoot (m@bigfoot.local, sudo -n): user bkotel (no password, no
#      sudo, home /var/lib/bkotel), store /var/log/bko-telemetry (bkotel,
#      2750; m joins group bkotel to read it), /usr/local/bin/bko-telemetry-recv.
#      ~bkotel/.ssh and authorized_keys belong to root, so bkotel (which
#      unpacks what clients send) can't change its own keys.
#   2. Bigfoot's ed25519 host key, read from its /etc/ssh over that same ssh
#      session (which the Mac's known_hosts has checked: StrictHostKeyChecking
#      =yes), never from an unchecked scan.
#   3. Each HOST (sudo -n): /usr/local/sbin/bko-telemetry, the units,
#      /etc/default/bko-telemetry (only when missing), /var/lib/bko-telemetry
#      and its key (made when missing).  The host's ssh-keyscan of Bigfoot
#      must match step 2's key, else the host is skipped; then its
#      known_hosts gets exactly that key.
#   4. Bigfoot's ~bkotel/.ssh/authorized_keys: the host's one line
#      (comment bko-telemetry@HOST), replaced, with the forced command.
#   5. The host: timer enabled and started, one run now, its log line and
#      what arrived on Bigfoot.
#
# --uninstall: stops and removes the timer, units, script, config,
# /var/lib/bko-telemetry (key and spool) and /run/bko-telemetry on each HOST, and the host's line
# on Bigfoot.  What Bigfoot already stored stays (README: Removing).
#
# --dry-run prints the steps and changes nothing (it connects nowhere).
set -u
here=$(cd "$(dirname "$0")" && pwd)
BIGFOOT=bigfoot.local
SSHO=(-o BatchMode=yes -o ConnectTimeout=15)

dry=0 uninstall=0 hosts=()
for a in "$@"; do
    case $a in
        --dry-run) dry=1 ;;
        --uninstall) uninstall=1 ;;
        -h|--help) sed -n '2,/^set -u/s/^# \{0,1\}//p' "$0"; exit 0 ;;
        -*) echo "telemetry-setup.sh: unknown option $a (see --help)" >&2; exit 2 ;;
        *)
            if ! [[ $a =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]]; then
                echo "telemetry-setup.sh: bad host name $a" >&2; exit 2
            fi
            hosts+=("$a") ;;
    esac
done
if [ "${#hosts[@]}" = 0 ]; then
    [ "$uninstall" = 1 ] && { echo "telemetry-setup.sh: --uninstall needs the hosts named" >&2; exit 2; }
    hosts=(chopin cinqcent cage copland czerny choubox)
fi
for f in bko-telemetry bko-telemetry-recv bko-telemetry.service bko-telemetry.timer bko-telemetry.default; do
    [ -r "$here/$f" ] || { echo "telemetry-setup.sh: missing $here/$f" >&2; exit 1; }
done

say() { printf '%s\n' "$*"; }
die() { printf 'telemetry-setup.sh: %s\n' "$*" >&2; exit 1; }
host_ssh() {  # HOST CMD...
    local h=$1; shift
    case $h in
        chopin) ssh "${SSHO[@]}" -o HostKeyAlias=chopin.local m@192.168.68.194 "$@" ;;  # chopin.local doesn't always resolve
        *) ssh "${SSHO[@]}" "m@$h.local" "$@" ;;
    esac
}
# Bigfoot must already be in the Mac's known_hosts: its host key is read over this.
bf_ssh() { ssh "${SSHO[@]}" -o StrictHostKeyChecking=yes "m@$BIGFOOT" "$@"; }
b64() { base64 <"$1" | tr -d '\n'; }

# --- remote scripts (run by sudo -n bash -s; payloads in base64) ---

bigfoot_script() {
    printf "RECV='%s'\nPRUNE='%s'\n" "$(b64 "$here/bko-telemetry-recv")" "$(b64 "$here/bko-log-prune")"
    cat <<'EOF'
set -eu
if ! getent passwd bkotel >/dev/null; then
    useradd --system --user-group --home-dir /var/lib/bkotel --create-home \
        --shell /bin/sh --comment "BackOtter telemetry receiver" bkotel
fi
usermod -p '*' bkotel   # no password, but not "locked" (sshd may refuse locked accounts)
# root owns the home, .ssh and authorized_keys (sshd's StrictModes takes
# that); bkotel can read them but not change them
install -d -m 0750 -o root -g bkotel /var/lib/bkotel
install -d -m 0755 -o root -g root /var/lib/bkotel/.ssh
ak=/var/lib/bkotel/.ssh/authorized_keys
[ -e "$ak" ] || install -m 0644 -o root -g root /dev/null "$ak"
chown root:root "$ak"
chmod 0644 "$ak"
install -d -m 2750 -o bkotel -g bkotel /var/log/bko-telemetry
chown -R bkotel:bkotel /var/log/bko-telemetry
find /var/log/bko-telemetry -type d -exec chmod 2750 {} +
usermod -aG bkotel m
printf %s "$RECV" | base64 -d >/usr/local/bin/.bko-telemetry-recv.new
chmod 0755 /usr/local/bin/.bko-telemetry-recv.new
chown root:root /usr/local/bin/.bko-telemetry-recv.new
mv -f /usr/local/bin/.bko-telemetry-recv.new /usr/local/bin/bko-telemetry-recv
# the daily prune of every farm log store on Bigfoot (bko-log-prune)
printf %s "$PRUNE" | base64 -d >/usr/local/sbin/.bko-log-prune.new
chmod 0755 /usr/local/sbin/.bko-log-prune.new
mv -f /usr/local/sbin/.bko-log-prune.new /usr/local/sbin/bko-log-prune
cat >/etc/systemd/system/bko-log-prune.service <<'UNIT'
[Unit]
Description=BackOtter: prune the farm logs Bigfoot collects (telemetry, journal-remote, bko-farm) to 30 days

[Service]
Type=oneshot
Nice=10
IOSchedulingClass=idle
ExecStart=/usr/local/sbin/bko-log-prune
UNIT
cat >/etc/systemd/system/bko-log-prune.timer <<'UNIT'
[Unit]
Description=BackOtter: daily prune of the farm logs on Bigfoot

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now bko-log-prune.timer >/dev/null 2>&1
r=$(env SSH_ORIGINAL_COMMAND=ping runuser -u bkotel -- /usr/local/bin/bko-telemetry-recv selftest)
[ "$r" = "ok selftest" ] || { echo "receiver self-test failed: $r"; exit 1; }
echo "store: $(findmnt -no SOURCE,FSTYPE -T /var/log/bko-telemetry) $(df -h --output=avail /var/log/bko-telemetry | tail -n 1 | tr -d ' ') free"
case $(findmnt -no FSTYPE -T /var/log/bko-telemetry) in
    zfs) echo "WARNING: the store is on ZFS (DantePool?), not Bigfoot's own disk" ;;
esac
s=$(sshd -T 2>/dev/null | grep -Ei '^(allowusers|allowgroups|denyusers|denygroups) ' || true)
[ -z "$s" ] || echo "WARNING: sshd has user/group limits; bkotel must be let in: $s"
echo "HOSTKEY $(awk '$1 == "ssh-ed25519" {print $2}' /etc/ssh/ssh_host_ed25519_key.pub)"
echo "BIGFOOT-OK"
EOF
}

host_install_script() {  # HOST
    printf "H=%s\nSCRIPT='%s'\nSERVICE='%s'\nTIMER='%s'\nCONF='%s'\n" "$1" \
        "$(b64 "$here/bko-telemetry")" "$(b64 "$here/bko-telemetry.service")" \
        "$(b64 "$here/bko-telemetry.timer")" "$(b64 "$here/bko-telemetry.default")"
    cat <<'EOF'
set -eu
put() {  # B64 FILE MODE: write atomically (a running copy keeps its old file)
    printf %s "$1" | base64 -d >"$2.new"
    chmod "$3" "$2.new"
    mv -f "$2.new" "$2"
}
put "$SCRIPT" /usr/local/sbin/bko-telemetry 0755
put "$SERVICE" /etc/systemd/system/bko-telemetry.service 0644
put "$TIMER" /etc/systemd/system/bko-telemetry.timer 0644
[ -e /etc/default/bko-telemetry ] || put "$CONF" /etc/default/bko-telemetry 0644
st=/var/lib/bko-telemetry
install -d -m 0700 "$st" "$st/spool"
[ -s "$st/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C "bko-telemetry@$H" -f "$st/id_ed25519"
chmod 0600 "$st/id_ed25519"
echo "PUB $(cat "$st/id_ed25519.pub")"
BKO_TEL_HOST=bigfoot.local BKO_TEL_IP=192.168.68.197
. /etc/default/bko-telemetry
a=$(timeout 10 getent ahostsv4 "$BKO_TEL_HOST" | awk 'NR == 1 {print $1}' || true)
echo "ALIAS $BKO_TEL_HOST"
echo "SCAN $(timeout 20 ssh-keyscan -T 10 -t ed25519 "${a:-$BKO_TEL_IP}" 2>/dev/null | awk '$2 == "ssh-ed25519" {print $3; exit}')"
n=$(uname -n); n=${n%%.*}
[ "$n" = "$H" ] || echo "NOTE: uname -n says $n, the key line says $H"
EOF
}

host_pin_and_start_script() {  # ALIAS BLOB
    printf "ALIAS='%s'\nBLOB='%s'\n" "$1" "$2"
    cat <<'EOF'
set -eu
st=/var/lib/bko-telemetry
printf '%s ssh-ed25519 %s\n' "$ALIAS" "$BLOB" >"$st/known_hosts.new"
chmod 0644 "$st/known_hosts.new"
mv -f "$st/known_hosts.new" "$st/known_hosts"
systemctl daemon-reload
systemctl enable --now bko-telemetry.timer
systemctl start bko-telemetry.service || echo "the first run failed (systemctl status bko-telemetry.service)"
journalctl -u bko-telemetry.service -n 4 -o cat --no-pager
systemctl list-timers bko-telemetry.timer --no-pager | head -n 2
EOF
}

host_uninstall_script() {
    cat <<'EOF'
set -u
systemctl disable --now bko-telemetry.timer 2>/dev/null
systemctl stop bko-telemetry.service 2>/dev/null
rm -f /etc/systemd/system/bko-telemetry.service /etc/systemd/system/bko-telemetry.timer \
    /usr/local/sbin/bko-telemetry /etc/default/bko-telemetry
rm -rf /var/lib/bko-telemetry /run/bko-telemetry
systemctl daemon-reload
echo "removed"
EOF
}

authkeys_script() {  # HOST [BLOB]: drop HOST's line (and BLOB's), then add BLOB's if given
    printf "H=%s\nBLOB='%s'\n" "$1" "${2:-}"
    cat <<'EOF'
set -eu
ak=/var/lib/bkotel/.ssh/authorized_keys
tmp=$(mktemp /var/lib/bkotel/.ssh/authorized_keys.XXXXXX)
grep -v -e " bko-telemetry@$H\$" "$ak" >"$tmp" || true
if [ -n "$BLOB" ]; then
    grep -v -F -e " $BLOB " "$tmp" >"$tmp.2" || true
    mv -f "$tmp.2" "$tmp"
    printf 'restrict,command="/usr/local/bin/bko-telemetry-recv %s" ssh-ed25519 %s bko-telemetry@%s\n' \
        "$H" "$BLOB" "$H" >>"$tmp"
fi
chown root:root "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$ak"
echo "authorized_keys: $(grep -c . "$ak" || true) lines"
EOF
}

# --- uninstall ---

if [ "$uninstall" = 1 ]; then
    for h in "${hosts[@]}"; do
        if [ "$dry" = 1 ]; then
            say "[dry-run] $h: disable bko-telemetry.timer; remove the units, /usr/local/sbin/bko-telemetry," \
                "/etc/default/bko-telemetry, /var/lib/bko-telemetry"
            say "[dry-run] bigfoot: drop the bko-telemetry@$h line from ~bkotel/.ssh/authorized_keys" \
                "(/var/log/bko-telemetry/$h stays)"
            continue
        fi
        say "== $h: uninstall"
        host_uninstall_script | host_ssh "$h" 'sudo -n bash -s' || say "$h: uninstall failed (is it up?)"
        authkeys_script "$h" | bf_ssh 'sudo -n bash -s' || say "bigfoot: could not drop $h's key line"
        say "$h: done; its stored telemetry stays in bigfoot:/var/log/bko-telemetry/$h"
    done
    exit 0
fi

# --- install ---

if [ "$dry" = 1 ]; then
    say "[dry-run] bigfoot (m@$BIGFOOT, sudo -n): user bkotel, /var/lib/bkotel/.ssh, store /var/log/bko-telemetry" \
        "(bkotel 2750, m in group bkotel), /usr/local/bin/bko-telemetry-recv, bko-log-prune.timer (daily), a ping self-test"
    say "[dry-run] bigfoot: read its ed25519 host key over that checked ssh session"
    for h in "${hosts[@]}"; do
        say "[dry-run] $h: install /usr/local/sbin/bko-telemetry, bko-telemetry.{service,timer}," \
            "/etc/default/bko-telemetry if missing; /var/lib/bko-telemetry/id_ed25519 if missing; ssh-keyscan of Bigfoot"
        say "[dry-run] $h: refuse unless its scan of Bigfoot matches the key read from Bigfoot; pin it in /var/lib/bko-telemetry/known_hosts"
        say "[dry-run] bigfoot: authorized_keys line for $h:" \
            "restrict,command=\"/usr/local/bin/bko-telemetry-recv $h\" ssh-ed25519 <$h's key> bko-telemetry@$h"
        say "[dry-run] $h: systemctl enable --now bko-telemetry.timer; start bko-telemetry.service once; show the log line"
    done
    exit 0
fi

say "== bigfoot"
out=$(bigfoot_script | bf_ssh 'sudo -n bash -s' 2>&1)
printf '%s\n' "$out" | grep -Ev '^(BIGFOOT-OK|HOSTKEY .*)$'
printf '%s\n' "$out" | grep -q '^BIGFOOT-OK$' ||
    die "Bigfoot's setup failed (is bigfoot.local in the Mac's known_hosts? ssh m@bigfoot.local once by hand)"

# Bigfoot's host key, read over the session the Mac's known_hosts checked
bkey=$(printf '%s\n' "$out" | sed -n 's/^HOSTKEY //p' | head -n 1)
[[ $bkey =~ ^AAAA[A-Za-z0-9+/]+=*$ ]] || die "could not read Bigfoot's ed25519 host key"
fp=$(printf 'bigfoot ssh-ed25519 %s\n' "$bkey" | ssh-keygen -lf - | awk '{print $2}')
say "Bigfoot's key: $fp (read over the checked session)"

rc=0
for h in "${hosts[@]}"; do
    say "== $h"
    out=$(host_install_script "$h" | host_ssh "$h" 'sudo -n bash -s' 2>&1)
    pub=$(printf '%s\n' "$out" | sed -n 's/^PUB //p' | head -n 1)
    alias=$(printf '%s\n' "$out" | sed -n 's/^ALIAS //p' | head -n 1)
    scan=$(printf '%s\n' "$out" | sed -n 's/^SCAN //p' | head -n 1)
    printf '%s\n' "$out" | grep -Ev '^(PUB|ALIAS|SCAN) '
    set -- $pub
    if [ "${1:-}" != ssh-ed25519 ] || ! [[ ${2:-} =~ ^[A-Za-z0-9+/]+=*$ ]]; then
        say "$h: install failed or no key read back; skipped"; rc=1; continue
    fi
    blob=$2
    if ! [[ $alias =~ ^[A-Za-z0-9.:-]+$ ]]; then
        say "$h: odd BKO_TEL_HOST '$alias' in its /etc/default/bko-telemetry; skipped"; rc=1; continue
    fi
    if [ "$scan" != "$bkey" ]; then
        say "$h: its scan of Bigfoot (${scan:-nothing}) is not Bigfoot's own key: refused, skipped"; rc=1; continue
    fi
    say "$h: key $(printf 'x ssh-ed25519 %s\n' "$blob" | ssh-keygen -lf - | awk '{print $2}'); its scan of Bigfoot matches"
    authkeys_script "$h" "$blob" | bf_ssh 'sudo -n bash -s' || { say "$h: could not add its key line on Bigfoot"; rc=1; continue; }
    host_pin_and_start_script "$alias" "$bkey" | host_ssh "$h" 'sudo -n bash -s' || { say "$h: start failed"; rc=1; }
    say "$h: newest on Bigfoot: $(bf_ssh "sudo -n ls -1 /var/log/bko-telemetry/$h 2>/dev/null | tail -n 1" </dev/null)"
done
exit "$rc"
