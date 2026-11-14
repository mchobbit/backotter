#!/bin/bash
# The scripted dom0 session of make-qubes.sh's first boot, after Qubes's
# initial setup made the templates and the default qubes
# (docs/design/qubes.md, "Revisions on purpose").  make-qubes.sh's
# qubes-drive.py types this directory into dom0 over the serial console (as
# a base64 tar: dom0 has no network) and runs it as root from there:
#   - installs the check unit (bko-qubes-check.sh in /usr/local/sbin,
#     bko-qubes-check.service enabled), which runs from the next boot on;
#   - keeps two revisions of work's private volume (revisions_to_keep 2,
#     the default is 1), so work gets a chain of three;
#   - starts and stops work and personal twice and the Debian template once,
#     writing /home/user/bko-seed.txt in work and personal in each round
#     with that round's text (bko-qubes-check.sh checks round 2's), so each
#     revision differs from the next;
#   - lists qubes_dom0's LVs with their origins.
# It prints BKO-QUBES-SETUP lines, and last BKO-QUBES-SETUP DONE PASS|FAIL.
#
# Usage: revisions.sh        (as root in dom0, from its own directory)
set -u
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Nothing here reads the console: qubes-drive.py runs this from dom0's
# serial login shell, and `timeout` puts its command in a process group of
# its own, so a `qvm-run --pass-io` reading that terminal is stopped by
# SIGTTIN and hangs until the timeout (Q1's first run: "seed in work after
# 300 s").
exec </dev/null
here=$(dirname "$(realpath "$0")")
ok=1
say() { echo "BKO-QUBES-SETUP $*"; }
fail() { say "FAIL $*"; ok=0; }
seed_text() { printf 'bko-qubes seed %s round %s\n' "$1" "$2"; }
timed() {  # WHAT CMD...: run CMD, say how long it took or that it failed
    local what=$1 t0=$SECONDS out; shift
    if out=$("$@" 2>&1); then say "$what $((SECONDS - t0)) s"
    else fail "$what after $((SECONDS - t0)) s: $(tr '\n' ' ' <<<"$out" | cut -c1-300)"; fi
}

# Should dom0's getty have come before initial-setup ended, wait for it.
t0=$SECONDS
while [[ $(systemctl is-active initial-setup.service) =~ ^(active|activating)$ ]] && ((SECONDS - t0 < 3600)); do sleep 10; done
say "initial-setup.service $(systemctl is-active initial-setup.service) (waited $((SECONDS - t0)) s)"

if ! { install -m 755 "$here/bko-qubes-check.sh" /usr/local/sbin/bko-qubes-check.sh &&
    install -m 644 "$here/bko-qubes-check.service" /etc/systemd/system/bko-qubes-check.service &&
    systemctl daemon-reload && systemctl enable bko-qubes-check.service 2>&1; }; then
    fail "could not install the check unit"
fi
say "check unit $(systemctl is-enabled bko-qubes-check.service)"

say "qubes: $(qvm-ls --raw-data --fields NAME,STATE,CLASS,VIRT_MODE | tr '\n' ' ')"
timed "revisions_to_keep 2 on work:private" qvm-volume config work:private revisions_to_keep 2
say "work:private revisions_to_keep $(qvm-volume info work:private revisions_to_keep)"

for round in 1 2; do
    for q in work personal; do
        timed "round $round: qvm-start $q" timeout 900 qvm-start "$q"
        seed=$(seed_text "$q" "$round" | base64 -w0)
        timed "round $round: seed in $q" timeout 300 qvm-run --pass-io --no-gui "$q" \
            "echo $seed | base64 -d >/home/user/bko-seed.txt && sync"
        timed "round $round: qvm-shutdown $q" timeout 600 qvm-shutdown --wait "$q"
    done
    if [[ $round == 1 ]]; then
        timed "round $round: qvm-start debian-13-xfce" timeout 900 qvm-start debian-13-xfce
        timed "round $round: qvm-shutdown debian-13-xfce" timeout 600 qvm-shutdown --wait debian-13-xfce
    fi
done
timed "qvm-shutdown --all" timeout 900 qvm-shutdown --wait --all

lvs --noheadings --separator '|' -o lv_name,pool_lv,origin,lv_attr,lv_size qubes_dom0 | sed 's/^ */BKO-QUBES-SETUP lv /'
for q in work personal; do
    say "$q:private revisions: $(qvm-volume info "$q:private" revisions | tr '\n' ' ')"
done
# For the record (docs/design/qubes.md): the ESP's loaders, the boot's options.
say "esp: $(find /boot/efi/EFI -type f | sort | tr '\n' ' ')"
say "cmdline: $(cat /proc/cmdline)"
say "grub: $(grep -E '^GRUB_CMDLINE' /etc/default/grub | tr '\n' ' ')"
sync
if ((ok)); then say "DONE PASS"; else say "DONE FAIL"; fi
