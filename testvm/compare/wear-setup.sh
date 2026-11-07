#!/usr/bin/env bash
# Set up the wear log on a KVM test host (run once, as m with sudo, from
# ~/backotter-work): /bkoscratch/wear for every job's iolog.sh line
# (jobs.log, written by root and by m) and an hourly SMART reading of each
# SSD (smart.log, bko-wear-smart.timer running wear-smart.sh), the first one
# now.  wear-report.py reads both.
set -eu
here=$(dirname "$(readlink -f "$0")")
# 0777, not sticky: in a sticky world-writable directory the kernel's
# protected_regular keeps root from appending to m's file and m from root's.
sudo install -d -m 0777 /bkoscratch/wear
sudo chmod 0777 /bkoscratch/wear
sudo touch /bkoscratch/wear/jobs.log
sudo chmod 666 /bkoscratch/wear/jobs.log
sudo tee /etc/systemd/system/bko-wear-smart.service >/dev/null <<EOF
[Unit]
Description=BackOtter wear log: each SSD's SMART writes and wear
RequiresMountsFor=/bkoscratch

[Service]
Type=oneshot
ExecStart=$here/wear-smart.sh
EOF
sudo tee /etc/systemd/system/bko-wear-smart.timer >/dev/null <<EOF
[Unit]
Description=BackOtter wear log, hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now bko-wear-smart.timer
sudo systemctl start bko-wear-smart.service
tail -n 5 /bkoscratch/wear/smart.log
