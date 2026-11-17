#!/usr/bin/env bash
# Install (or remove) the bko-farm collector on Bigfoot: it receives the
# farm hosts' journals and netconsole, and watches them with a heartbeat
# (testvm/farm-monitor/README.md).  Run on Bigfoot, from a copy of this
# directory.  Idempotent: run it again after changing a file here.
#
#   sudo ./install-collector.sh
#   sudo ./install-collector.sh --uninstall [--purge]
#
# Install:
#   1. package systemd-journal-remote.
#   2. system user bko-farm (no login, no password) for the two services.
#   3. /etc/bko-farm/hosts (only when missing: edits are kept),
#      /usr/local/lib/bko-farm/{heartbeat.py,netconsole-recv.py},
#      /var/log/bko-farm/{,netconsole/}, /var/lib/bko-farm/.
#   4. systemd-journal-remote on plain HTTP, TCP 19532 (the package's
#      socket), one journal file per sending address, under
#      /var/log/journal/remote/, capped (MaxUse=6G, KeepFree=10G,
#      MaxFileSize=128M): drop-ins only, the package's files untouched.
#   5. bko-farm-netconsole.service (UDP 6666) and bko-farm-heartbeat.service.
#   6. /etc/logrotate.d/bko-farm.
#   7. Bigfoot's ufw is NOT changed: the two rules the farm needs are
#      checked and printed, for the user to add.
#
# --uninstall: stops and removes everything 2-6 added, and disables
# journal-remote; what was received stays (owned by root then).
# --purge also deletes /var/log/bko-farm and /var/log/journal/remote, and
# the package when this script installed it.
set -euo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
here=$(cd "$(dirname "$0")" && pwd)

PKG=systemd-journal-remote
USER_=bko-farm
ETC=/etc/bko-farm
LIB=/usr/local/lib/bko-farm
LOG=/var/log/bko-farm
STATE=/var/lib/bko-farm
REMOTE=/var/log/journal/remote
PKG_MARK=$ETC/.pkg-installed-by-bko-farm
JR_CONF=/etc/systemd/journal-remote.conf.d/bko-farm.conf
JR_DROPIN=/etc/systemd/system/systemd-journal-remote.service.d/bko-farm.conf
UNITS=(bko-farm-heartbeat.service bko-farm-netconsole.service)
LOGROTATE=/etc/logrotate.d/bko-farm
LAN=192.168.68.0/24
UFW_CMDS=("ufw allow from $LAN to any port 19532 proto tcp"
          "ufw allow from $LAN to any port 6666 proto udp")

uninstall=0 purge=0
for a in "$@"; do
    case $a in
        --uninstall) uninstall=1 ;;
        --purge) purge=1 ;;
        -h|--help) sed -n '2,/^set -euo/s/^# \{0,1\}//p' "$0" | sed '$d'; exit 0 ;;
        *) echo "install-collector.sh: unknown argument $a (see --help)" >&2; exit 2 ;;
    esac
done

say() { echo "install-collector: $*"; }
die() { echo "install-collector: $*" >&2; exit 1; }

[[ $(id -u) == 0 ]] || die "run with sudo"
((purge == 0 || uninstall == 1)) || die "--purge goes with --uninstall"

pkg_installed() {
    dpkg-query -W -f='${db:Status-Status}' "$PKG" 2>/dev/null | grep -qx installed
}

ufw_check() {
    local st missing=0
    if ! command -v ufw >/dev/null; then
        say "ufw not installed: nothing to open"
        return 0
    fi
    st=$(ufw status 2>/dev/null) || st=
    if ! grep -q '^Status: active' <<<"$st"; then
        say "ufw is not active: nothing to open"
        return 0
    fi
    grep -Eq "^19532/tcp +ALLOW( IN)? +${LAN//./\\.}( |$)" <<<"$st" || missing=1
    grep -Eq "^6666/udp +ALLOW( IN)? +${LAN//./\\.}( |$)" <<<"$st" || missing=1
    if ((missing)); then
        echo
        say "ufw is active and does not let the farm in yet.  Run these two on Bigfoot"
        say "(this script never changes the firewall):"
        printf '    sudo %s\n' "${UFW_CMDS[@]}"
    else
        say "ufw: 19532/tcp and 6666/udp allowed from $LAN"
    fi
}

# ---------------------------------------------------------------- uninstall
if ((uninstall)); then
    systemctl disable --now "${UNITS[@]}" 2>/dev/null || true
    for u in "${UNITS[@]}"; do rm -f "/etc/systemd/system/$u"; done
    if systemctl cat systemd-journal-remote.socket >/dev/null 2>&1; then
        systemctl disable --now systemd-journal-remote.socket systemd-journal-remote.service 2>/dev/null || true
    fi
    rm -f "$JR_CONF" "$JR_DROPIN" "$LOGROTATE"
    rmdir --ignore-fail-on-non-empty "$(dirname "$JR_CONF")" "$(dirname "$JR_DROPIN")" 2>/dev/null || true
    systemctl daemon-reload
    rm -rf "$LIB" "$STATE"
    find "$ETC" -mindepth 1 ! -path "$PKG_MARK" -delete 2>/dev/null || true
    if ((purge)); then
        rm -rf "$LOG" "$REMOTE"
        if [[ -e $PKG_MARK ]]; then
            apt-get purge -y "$PKG"
            say "package $PKG purged"
        elif pkg_installed; then
            say "package $PKG was here before bko-farm; left installed"
        fi
        rm -rf "$ETC"
    else
        [[ -d $LOG ]] && chown -R root:root "$LOG"
        rmdir "$ETC" 2>/dev/null || true
        say "kept the received data: $LOG and $REMOTE (--purge deletes them)"
    fi
    if getent passwd "$USER_" >/dev/null; then
        userdel "$USER_"
    fi
    say "removed.  The ufw rules, if you added them, go with:"
    printf '    sudo ufw delete allow from %s to any port 19532 proto tcp\n' "$LAN"
    printf '    sudo ufw delete allow from %s to any port 6666 proto udp\n' "$LAN"
    exit 0
fi

# ---------------------------------------------------------------- install
for f in heartbeat.py netconsole-recv.py hosts; do
    [[ -f $here/$f ]] || die "run from a copy of testvm/farm-monitor (no $here/$f)"
done
for f in heartbeat.py netconsole-recv.py; do
    python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$here/$f" \
        || die "$here/$f does not parse"
done

# 1. package
mkdir -p "$ETC"
if ! pkg_installed; then
    say "installing $PKG"
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$PKG"
    touch "$PKG_MARK"
fi
systemctl cat systemd-journal-remote.socket >/dev/null 2>&1 \
    || die "$PKG is installed but there is no systemd-journal-remote.socket"

# 2. user
if ! getent passwd "$USER_" >/dev/null; then
    useradd --system --user-group --no-create-home --home-dir "$STATE" \
        --shell /usr/sbin/nologin --comment "bko-farm collector" "$USER_"
    say "created system user $USER_"
fi

# 3. files
[[ -e $ETC/hosts ]] || install -m 0644 "$here/hosts" "$ETC/hosts"
install -d -m 0755 "$LIB"
install -m 0755 "$here/heartbeat.py" "$LIB/heartbeat.py"
install -m 0755 "$here/netconsole-recv.py" "$LIB/netconsole-recv.py"
install -d -m 0755 -o "$USER_" -g "$USER_" "$LOG" "$LOG/netconsole"
# Files kept by an --uninstall without --purge are root's (and the user may
# come back with a new uid): hand them all back, or the services can't append.
chown -R "$USER_:$USER_" "$LOG"
install -d -m 0750 -o "$USER_" -g "$USER_" "$STATE"

# 4. journal-remote: plain HTTP on the package's socket (fd 3)
jr_bin=$(systemctl show -P ExecStart systemd-journal-remote.service 2>/dev/null \
    | sed -n 's/.*path=\([^ ;]*\).*/\1/p' | head -n1) || jr_bin=
for b in "$jr_bin" /usr/lib/systemd/systemd-journal-remote /lib/systemd/systemd-journal-remote; do
    if [[ -n $b && -x $b ]]; then jr_bin=$b; break; fi
done
[[ -x $jr_bin ]] || die "cannot find the systemd-journal-remote binary"
mkdir -p "$(dirname "$JR_CONF")" "$(dirname "$JR_DROPIN")"
cat >"$JR_CONF" <<'EOF'
# bko-farm (testvm/farm-monitor/install-collector.sh): one file per sender,
# and a cap on what the farm may take of Bigfoot's root fs.
[Remote]
SplitMode=host
MaxUse=6G
KeepFree=10G
MaxFileSize=128M
EOF
cat >"$JR_DROPIN" <<EOF
# bko-farm (testvm/farm-monitor/install-collector.sh): plain HTTP on the
# socket unit's port 19532 instead of the package's HTTPS (which needs
# certificates).  LAN only, no TLS, no client check: ufw limits it to $LAN.
[Service]
ExecStart=
ExecStart=$jr_bin --listen-http=-3 --output=$REMOTE/
EOF

# 5. our services
for u in "${UNITS[@]}"; do
    case $u in
        bko-farm-heartbeat.service)
            desc="bko-farm heartbeat: ping and ssh probe of the farm hosts every 15 s"
            exe=$LIB/heartbeat.py ;;
        bko-farm-netconsole.service)
            desc="bko-farm netconsole receiver (UDP 6666)"
            exe=$LIB/netconsole-recv.py ;;
    esac
    cat >"/etc/systemd/system/$u" <<EOF
# bko-farm (testvm/farm-monitor/install-collector.sh)
[Unit]
Description=$desc
Documentation=file://$exe
Wants=network-online.target
After=network-online.target avahi-daemon.service

[Service]
Type=simple
User=$USER_
Group=$USER_
ExecStart=/usr/bin/python3 -u $exe
Restart=always
RestartSec=5
SyslogIdentifier=${u%.service}
Nice=10
MemoryMax=128M
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=$LOG $STATE

[Install]
WantedBy=multi-user.target
EOF
done

# 6. logrotate (the services append and close per write: plain rotation)
cat >"$LOGROTATE" <<EOF
# bko-farm (testvm/farm-monitor/install-collector.sh)
$LOG/heartbeat.log $LOG/netconsole/*.log {
    su $USER_ $USER_
    weekly
    maxsize 50M
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
}
EOF

systemctl daemon-reload
systemctl enable systemd-journal-remote.socket "${UNITS[@]}"
systemctl start systemd-journal-remote.socket
# A running journal-remote keeps its old command line until restarted.
if systemctl is-active -q systemd-journal-remote.service; then
    systemctl restart systemd-journal-remote.service
fi
systemctl restart "${UNITS[@]}"
if command -v logrotate >/dev/null; then
    logrotate --debug "$LOGROTATE" >/dev/null 2>&1 || say "WARNING: logrotate --debug $LOGROTATE complains"
fi

# 7. firewall (checked, never changed)
echo
ufw_check

echo
say "done.  Now:"
for u in systemd-journal-remote.socket "${UNITS[@]}"; do
    printf '  %-32s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null || true)"
done
ss -Hlntu 2>/dev/null | awk '$5 ~ /:(19532|6666)$/ {print "  listening", $1, $5}' || true
echo
cat "$LOG/status" 2>/dev/null || say "no $LOG/status yet (the first cycle takes up to 15 s)"
cat <<EOF

Next: the ufw rules above (if printed), then on each farm host, from a copy
of testvm/farm-monitor:  sudo ./install-sender.sh
EOF
