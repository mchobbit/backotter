#!/bin/sh
# What a booted Linux guest's init says of its services, once they have
# settled: compare.py's boot test runs it over ssh (sh -s), in a restored
# guest and in the original, and compares the two.
#
# Prints, one item a line:
#   init NAME           systemd, openrc, runit, dinit, s6-rc, sysv-rc or
#                       unknown (by PID 1, and the tools beside it: Devuan's
#                       and Alpine's PID 1 is a plain init running OpenRC)
#   state STATE         running, or degraded if the init reports a service
#                       failed, or starting if its services were still
#                       changing at the deadline (T seconds, default 180);
#                       systemd's own word (is-system-running) under systemd
#   failed NAME         each service the init reports as failed
#   service NAME STATE  each service and its state, for the comparison with
#                       the original (not under systemd: its failed units
#                       say enough)
# A service's state is the init's own word (OpenRC's started, crashed...;
# runit's run, down...; dinit's started, stopped, failed...; s6-rc's up and
# down), or "restarting" for a supervised one up for less than 10 s in a
# system up for a minute, and, under sysv-rc, which has no record of its
# services, "status=N", the exit status of the service's own status action
# (0 running, 3 not running, 4 unknown: the LSB's).
#
# POSIX sh, busybox included.

PATH=/sbin:/usr/sbin:/bin:/usr/bin:$PATH
T=${T:-180}

pid1=$(cat /proc/1/comm 2>/dev/null)
case $pid1 in
    systemd) init=systemd ;;
    runit) init=runit ;;
    dinit) init=dinit ;;
    s6-svscan) init=s6-rc ;;
    *)  if command -v rc-status >/dev/null 2>&1 && [ -d /run/openrc ]; then init=openrc
        elif [ -d /etc/rcS.d ] && command -v runlevel >/dev/null 2>&1; then init=sysv-rc
        else init=unknown; fi ;;
esac
echo "init $init"

if [ $init = systemd ]; then
    # Nothing, if timeout stopped it: still starting.
    state=$(timeout "$T" systemctl is-system-running --wait 2>/dev/null)
    echo "state ${state:-starting}"
    systemctl --failed --no-legend --plain 2>/dev/null | awk '{ print "failed " $1 }'
    exit 0
fi

uptime_s() { cut -d. -f1 /proc/uptime; }

# Every service, "NAME STATE", sorted.
services() {
    case $init in
    openrc)
        # The runlevels a booted system is in (not shutdown, off, single
        # and the like), and the services pulled in besides.
        rc-status -a 2>/dev/null | awk '
            /^Runlevel: / { on = ($2 == "sysinit" || $2 == "boot" || $2 == "default"); next }
            /^Dynamic Runlevel: / { on = ($3 == "needed/wanted" || $3 == "hotplugged"); next }
            on && /\[/ { s = $0; sub(/^.*\[ */, "", s); sub(/ *\].*$/, "", s)
                # A supervise-daemon service shows its uptime and restart
                # count ("00:01:23 (0)", "2 day(s) ...") instead of started.
                split(s, w, " "); s = w[1]; if (s ~ /^[0-9]/) s = "started"
                print $1, s }' ;;
    runit)
        up=$(uptime_s)
        for d in /var/service/* /etc/service/* /run/runit/service/*; do
            [ -d "$d" ] || continue
            # "run: /var/service/sshd: (pid 503) 31s; run: log: ...": the
            # service's own part, before any log service's.
            sv status "$d" 2>&1 | sed 's/;.*//' | awk -v up="$up" -v n="${d##*/}" '{
                s = $1; sub(/:$/, "", s)
                for (i = 1; i <= NF; i++) if ($i ~ /^[0-9]+s$/) { t = $i; sub(/s$/, "", t) }
                if (s == "run" && t + 0 < 10 && up + 0 >= 60) s = "restarting"
                print n, s }'
        done ;;
    dinit)
        # "[{+}     ] sshd (pid: 314)": the box, ten characters wide, shows
        # the state, + for started, X for failed to start, s for skipped;
        # arrows a change under way.  ([[+]...] is a service marked active.)
        dinitctl list 2>/dev/null | awk '{
            box = substr($0, 1, 10); name = substr($0, 12); sub(/ .*$/, "", name)
            if (box ~ /X/) s = "failed"; else if (box ~ /<<|>>/) s = "changing"
            else if (box ~ /\+/) s = "started"; else if (box ~ /s/) s = "skipped"; else s = "stopped"
            print name, s }' ;;
    s6-rc)
        up=$(uptime_s)
        s6-rc -a list 2>/dev/null | while read -r n; do
            s=up
            # A longrun up for less than 10 s in a system up for a minute
            # keeps dying and being restarted.
            if [ -d "/run/service/$n" ]; then
                t=$(s6-svstat -o up,uptime "/run/service/$n" 2>/dev/null | awk '{ print $2 }')
                [ -n "$t" ] && [ "$t" -lt 10 ] && [ "$up" -ge 60 ] && s=restarting
            fi
            echo "$n $s"
        done
        s6-rc -da list 2>/dev/null | sed 's/$/ down/' ;;
    sysv-rc)
        rl=$(runlevel 2>/dev/null | awk '{ print $2 }')
        for s in /etc/rcS.d/S* /etc/rc"$rl".d/S*; do
            [ -x "$s" ] || continue
            n=${s##*/S[0-9][0-9]}
            timeout 20 "/etc/init.d/$n" status >/dev/null 2>&1 </dev/null
            echo "$n status=$?"
        done ;;
    esac | sort -u
}

# The services the init reports as failed, by name.
failed() {
    case $init in
    openrc) awk '$2 == "crashed" || $2 == "failed" || $2 == "stopped" { print $1 }' ;;
    runit) awk '$2 != "run" { print $1 }' ;;
    dinit) awk '$2 == "failed" { print $1 }' ;;
    # Down while the default bundle wants it, or restarting.
    s6-rc)
        want=$(s6-rc-db all-dependencies default 2>/dev/null)
        awk -v want="$want" 'BEGIN { n = split(want, w, "\n"); for (i = 1; i <= n; i++) wanted[w[i]] = 1 }
            ($2 == "down" && ($1 in wanted)) || $2 == "restarting" { print $1 }' ;;
    *) cat >/dev/null ;;
    esac
}

# Settled: the same services in the same states twice running, 10 s apart,
# with nothing in a passing state, and OpenRC and sysv-rc done with their
# runlevel.
settling() {
    case $init in
    openrc) [ "$(rc-status -r 2>/dev/null)" = default ] || return 0
            grep -qE ' (starting|stopping|inactive|scheduled)$' "$1" ;;
    sysv-rc) case $(runlevel 2>/dev/null) in *unknown*) return 0 ;; esac
             ps -e -o args= 2>/dev/null | grep -q '^/bin/sh /etc/init\.d/rc ' ;;
    dinit) grep -q ' changing$' "$1" ;;
    *) return 1 ;;
    esac
}

tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
deadline=$(($(uptime_s) + T)) state=starting
services >"$tmp/a"
while [ "$(uptime_s)" -lt "$deadline" ]; do
    sleep 10
    services >"$tmp/b"
    if cmp -s "$tmp/a" "$tmp/b" && ! settling "$tmp/b"; then state=running; break; fi
    mv "$tmp/b" "$tmp/a"
done
[ -f "$tmp/b" ] || cp "$tmp/a" "$tmp/b"
failed <"$tmp/b" >"$tmp/failed"
[ $state = running ] && [ -s "$tmp/failed" ] && state=degraded
[ $init = unknown ] && state="?"
echo "state $state"
sed 's/^/failed /' "$tmp/failed"
sed 's/^/service /' "$tmp/b"
