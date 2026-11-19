#!/usr/bin/env bash
# Install (or remove) the bko-farm sender on one farm host (chopin,
# cinqcent, cage, copland, czerny, choubox): its journal streams to Bigfoot
# and its kernel console goes there over netconsole
# (testvm/farm-monitor/README.md).
# Run on the host, from a copy of this directory, after the collector is
# installed and Bigfoot's ufw lets the farm in.  Idempotent.
#
#   sudo ./install-sender.sh [--collector-ip IP] [--console-loglevel N]
#                            [--no-panic] [COLLECTOR]
#   sudo ./install-sender.sh --uninstall [--purge]
#
# COLLECTOR: Bigfoot's name or IPv4, default bigfoot.local.
# --collector-ip: the address used when COLLECTOR doesn't resolve, default
#   192.168.68.197 (Bigfoot's DHCP lease on 2026-11-17).
# --console-loglevel: messages below N reach netconsole, default 5
#   (warnings and KERN_DEFAULT backtraces); 7 adds info, e.g. hung-task
#   stacks (README: Log level).
# --no-panic: leave kernel.panic and the lockup sysctls alone.
#
# Install:
#   1. package systemd-journal-remote (it ships systemd-journal-upload);
#      when this script installed it, the receiver sockets it may have
#      enabled here are disabled again (this host only sends).
#   2. /etc/systemd/journal-upload.conf.d/bko-farm.conf: URL=http://HOST:19532,
#      HOST the .local name when this host resolves it (nss-mdns), else the
#      IP (and it says so).  A drop-in makes systemd-journal-upload restart
#      every 30 s for ever, so it rides out Bigfoot being away.  On the
#      first install its cursor starts an hour back, not at the start of
#      the local journal.
#   3. /usr/local/sbin/bko-netconsole, /etc/default/bko-netconsole,
#      bko-netconsole.service (boot) and bko-netconsole.timer (every
#      10 min): the netconsole target, re-pointed when anything moved, and
#      the console log level raised from 4 to 5 (see README: Log level).
#   4. kernel.panic=10, kernel.softlockup_panic=1, kernel.hardlockup_panic=1
#      in /etc/sysctl.d/91-bko-farm-panic.conf, each only when no sysctl
#      file sets it and it is 0 now (Cage's /etc/sysctl.d/90-bko-lockup.conf
#      from 2026-11-13 already sets all three: nothing is added there).
#
# --uninstall: undoes 2-4 (target removed, log level and those sysctls put
# back, units and files deleted, journal-upload disabled).  --purge also
# removes journal-upload's cursor and the package, if this script
# installed the package.
set -euo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
here=$(cd "$(dirname "$0")" && pwd)

COLLECTOR=bigfoot.local
COLLECTOR_IP=192.168.68.197
PKG=systemd-journal-remote
STATE=/var/lib/bko-farm
PKG_MARK=$STATE/pkg-installed-by-bko-farm
UPLOAD_CONF=/etc/systemd/journal-upload.conf.d/bko-farm.conf
UPLOAD_DROPIN=/etc/systemd/system/systemd-journal-upload.service.d/bko-farm.conf
NC_BIN=/usr/local/sbin/bko-netconsole
NC_DEFAULT=/etc/default/bko-netconsole
NC_SERVICE=/etc/systemd/system/bko-netconsole.service
NC_TIMER=/etc/systemd/system/bko-netconsole.timer
PANIC_CONF=/etc/sysctl.d/91-bko-farm-panic.conf

uninstall=0 purge=0 panic=1 collector_set=0 loglevel=5
while (($#)); do
    case $1 in
        --uninstall) uninstall=1 ;;
        --purge) purge=1 ;;
        --no-panic) panic=0 ;;
        --collector-ip)
            [[ $# -ge 2 ]] || { echo "install-sender.sh: --collector-ip needs an address" >&2; exit 2; }
            COLLECTOR_IP=$2; shift ;;
        --console-loglevel)
            [[ ${2:-} =~ ^[1-8]$ ]] || { echo "install-sender.sh: --console-loglevel takes 1-8" >&2; exit 2; }
            loglevel=$2; shift ;;
        -h|--help) sed -n '2,/^set -euo/s/^# \{0,1\}//p' "$0" | sed '$d'; exit 0 ;;
        -*) echo "install-sender.sh: unknown option $1 (see --help)" >&2; exit 2 ;;
        *)
            ((collector_set == 0)) || { echo "install-sender.sh: one COLLECTOR only" >&2; exit 2; }
            COLLECTOR=$1; collector_set=1 ;;
    esac
    shift
done

say() { echo "install-sender: $*"; }
die() { echo "install-sender: $*" >&2; exit 1; }
is_ipv4() { [[ $1 =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; }

[[ $(id -u) == 0 ]] || die "run with sudo"
[[ $COLLECTOR =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ ]] || die "bad collector name $COLLECTOR"
is_ipv4 "$COLLECTOR_IP" || die "bad --collector-ip $COLLECTOR_IP"
((purge == 0 || uninstall == 1)) || die "--purge goes with --uninstall"

pkg_installed() {
    dpkg-query -W -f='${db:Status-Status}' "$PKG" 2>/dev/null | grep -qx installed
}

# journal-upload's state directory: DynamicUser= puts it under
# /var/lib/private, with /var/lib/systemd/journal-upload a symlink to it.
upload_state_file() {
    local sd dyn
    sd=$(systemctl show -P StateDirectory systemd-journal-upload.service 2>/dev/null) || sd=
    dyn=$(systemctl show -P DynamicUser systemd-journal-upload.service 2>/dev/null) || dyn=
    sd=${sd%% *}
    sd=${sd:-systemd/journal-upload}
    if [[ $dyn == yes ]]; then
        echo "/var/lib/private/$sd/state"
    else
        echo "/var/lib/$sd/state"
    fi
}

# ---------------------------------------------------------------- uninstall
if ((uninstall)); then
    systemctl disable --now bko-netconsole.timer bko-netconsole.service 2>/dev/null || true
    if [[ -x $NC_BIN ]]; then
        "$NC_BIN" --remove || say "bko-netconsole --remove failed; check /sys/kernel/config/netconsole"
    fi
    rm -f "$NC_BIN" "$NC_DEFAULT" "$NC_SERVICE" "$NC_TIMER"

    if systemctl cat systemd-journal-upload.service >/dev/null 2>&1; then
        systemctl disable --now systemd-journal-upload.service 2>/dev/null || true
    fi
    rm -f "$UPLOAD_CONF" "$UPLOAD_DROPIN"
    rmdir --ignore-fail-on-non-empty "$(dirname "$UPLOAD_CONF")" "$(dirname "$UPLOAD_DROPIN")" 2>/dev/null || true

    if [[ -f $PANIC_CONF ]]; then
        while IFS='=' read -r key _; do
            key=${key//[[:space:]]/}
            [[ $key =~ ^kernel\.[a-z_]+$ ]] || continue
            sysctl -q -w "$key=0" && say "$key back to 0"
        done <"$PANIC_CONF"
        rm -f "$PANIC_CONF"
    fi
    systemctl daemon-reload

    if ((purge)); then
        rm -f "$(upload_state_file)" /var/lib/systemd/journal-upload/state
        if [[ -e $PKG_MARK ]]; then
            apt-get purge -y "$PKG"
            say "package $PKG purged"
        elif pkg_installed; then
            say "package $PKG was here before bko-farm; left installed"
        fi
        rm -rf "$STATE"
    else
        # Keep only the note that this script installed the package, for a
        # later --uninstall --purge.
        find "$STATE" -mindepth 1 ! -path "$PKG_MARK" -delete 2>/dev/null || true
        rmdir "$STATE" 2>/dev/null || true
    fi
    say "removed.  Bigfoot keeps what it received (/var/log/journal/remote, /var/log/bko-farm)."
    exit 0
fi

# ---------------------------------------------------------------- install
[[ -f $here/bko-netconsole ]] || die "run from a copy of testvm/farm-monitor (no $here/bko-netconsole)"
bash -n "$here/bko-netconsole" || die "$here/bko-netconsole has a syntax error"
mkdir -p "$STATE"

# 1. package
if ! pkg_installed; then
    say "installing $PKG (ships systemd-journal-upload)"
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$PKG"
    touch "$PKG_MARK"
    for u in systemd-journal-remote.socket systemd-journal-gatewayd.socket; do
        if systemctl is-enabled -q "$u" 2>/dev/null || systemctl is-active -q "$u" 2>/dev/null; then
            systemctl disable --now "$u"
            say "disabled $u (the package's receiver; this host only sends)"
        fi
    done
fi
systemctl cat systemd-journal-upload.service >/dev/null 2>&1 \
    || die "$PKG is installed but there is no systemd-journal-upload.service (package split changed?)"

# 2. journal upload
collector_ip=
if is_ipv4 "$COLLECTOR"; then
    url_host=$COLLECTOR collector_ip=$COLLECTOR
    say "collector $COLLECTOR (a fixed IP: update it if Bigfoot's lease moves)"
elif collector_ip=$(getent ahostsv4 "$COLLECTOR" | awk 'NR==1{print $1}') && is_ipv4 "$collector_ip"; then
    url_host=$COLLECTOR
    say "collector $COLLECTOR resolves here ($collector_ip): the URL uses the name, so a DHCP move is followed"
else
    url_host=$COLLECTOR_IP collector_ip=$COLLECTOR_IP
    say "WARNING: $COLLECTOR does not resolve on this host (getent ahostsv4); the URL uses $COLLECTOR_IP," \
        "which goes stale if Bigfoot's lease moves.  Fix nss-mdns and re-run to use the name."
fi
mkdir -p "$(dirname "$UPLOAD_CONF")" "$(dirname "$UPLOAD_DROPIN")"
cat >"$UPLOAD_CONF" <<EOF
# bko-farm (testvm/farm-monitor/install-sender.sh): stream this journal to
# Bigfoot's systemd-journal-remote.  Plain HTTP, LAN only, no TLS.
[Upload]
URL=http://$url_host:19532
EOF
cat >"$UPLOAD_DROPIN" <<'EOF'
# bko-farm (testvm/farm-monitor/install-sender.sh): keep retrying while the
# collector is away, without ever hitting the start limit.
[Unit]
StartLimitIntervalSec=0

[Service]
Restart=always
RestartSec=30
RestartSteps=0
EOF
systemctl daemon-reload

# First install only: start an hour back, not at the head of the local
# journal (journal-upload with no saved cursor sends everything it has).
state_file=$(upload_state_file)
if [[ ! -e $state_file && ! -e /var/lib/systemd/journal-upload/state ]]; then
    cursor=$(journalctl --since=-1h -o export --no-pager 2>/dev/null | sed -n '1{s/^__CURSOR=//p;q}') || true
    if [[ -z ${cursor:-} ]]; then
        cursor=$(journalctl -n1 -o export --no-pager 2>/dev/null | sed -n '1{s/^__CURSOR=//p;q}') || true
    fi
    if [[ -n ${cursor:-} ]]; then
        if [[ $state_file == /var/lib/private/* && ! -d /var/lib/private ]]; then
            install -d -m 0700 /var/lib/private
        fi
        install -d -m 0755 "$(dirname "$state_file")"
        printf '# This is private data. Do not parse.\nLAST_CURSOR=%s\n' "$cursor" >"$state_file"
        say "journal-upload starts after $cursor (an hour back)"
    else
        say "WARNING: no journal cursor found; journal-upload will send the whole local journal"
    fi
fi
systemctl enable systemd-journal-upload.service
systemctl restart systemd-journal-upload.service
# shellcheck disable=SC2016  # $1 expands in the child bash
if timeout 3 bash -c 'exec 3<>"/dev/tcp/$1/19532"' _ "$collector_ip" 2>/dev/null; then
    say "collector $collector_ip:19532 answers"
else
    say "WARNING: $collector_ip:19532 does not answer (collector not installed, or Bigfoot's ufw" \
        "rule missing); journal-upload retries every 30 s"
fi

# 3. netconsole
install -m 0755 "$here/bko-netconsole" "$NC_BIN"
cat >"$NC_DEFAULT" <<EOF
# bko-netconsole settings (testvm/farm-monitor/install-sender.sh writes
# this on every install).
COLLECTOR=$COLLECTOR
COLLECTOR_IP=$COLLECTOR_IP
PORT=6666
# Messages below this level reach the console (and so netconsole): 5 lets
# warnings (4) and KERN_DEFAULT backtraces through, not notice/info.
CONSOLE_LOGLEVEL=$loglevel
EXTENDED=1
EOF
cat >"$NC_SERVICE" <<EOF
[Unit]
Description=bko-farm: point netconsole at the collector ($COLLECTOR)
Documentation=file://$NC_BIN
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=$NC_BIN

[Install]
WantedBy=multi-user.target
EOF
cat >"$NC_TIMER" <<'EOF'
[Unit]
Description=bko-farm: re-point netconsole every 10 min (DHCP, next hop)

[Timer]
OnBootSec=3min
OnUnitActiveSec=10min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable bko-netconsole.service bko-netconsole.timer
systemctl restart bko-netconsole.service || say "WARNING: bko-netconsole failed: journalctl -u bko-netconsole"
systemctl start bko-netconsole.timer

# 4. panic on lockups, unless something already decides it
if ((panic)); then
    declare -A want=([kernel.panic]=10 [kernel.softlockup_panic]=1 [kernel.hardlockup_panic]=1)
    add=()
    for key in kernel.panic kernel.softlockup_panic kernel.hardlockup_panic; do
        proc=/proc/sys/${key//.//}
        if [[ ! -e $proc ]]; then
            say "$key: not in this kernel; skipped"; continue
        fi
        set_by=$(grep -lsE "^[[:space:]]*-?${key//./[./]}[[:space:]]*=" \
            /etc/sysctl.conf /etc/sysctl.d/*.conf /run/sysctl.d/*.conf \
            /usr/local/lib/sysctl.d/*.conf /usr/lib/sysctl.d/*.conf 2>/dev/null \
            | grep -vx "$PANIC_CONF" | head -n1) || true
        if [[ -n $set_by ]]; then
            say "$key: set by $set_by ($(cat "$proc") now); left alone"
        elif [[ $(cat "$proc") != 0 ]]; then
            say "$key: $(cat "$proc") already (kernel command line?); left alone"
        else
            add+=("$key = ${want[$key]}")
        fi
    done
    if ((${#add[@]})); then
        {
            echo "# bko-farm (testvm/farm-monitor/install-sender.sh): a lockup panics,"
            echo "# a panic reboots after 10 s; netconsole carries the panic out first."
            printf '%s\n' "${add[@]}"
        } >"$PANIC_CONF"
        sysctl -q -p "$PANIC_CONF"
        say "set: ${add[*]}"
    elif [[ -f $PANIC_CONF ]]; then
        say "panic sysctls: $PANIC_CONF kept as it is"
    fi
fi

echo
say "done.  Now:"
"$NC_BIN" --status
systemctl --no-pager --lines=0 status systemd-journal-upload.service 2>&1 | sed -n '1,3p' || true
cat <<EOF

Test (see README):
  logger -t bko-farm-test "hello from \$(hostname)"
  echo "<4>bko-farm netconsole test" | sudo tee /dev/kmsg
then on Bigfoot:
  sudo journalctl -D /var/log/journal/remote -t bko-farm-test -n 5
  tail /var/log/bko-farm/netconsole/by-name/$(hostname -s).log
EOF
