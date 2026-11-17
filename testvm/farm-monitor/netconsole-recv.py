#!/usr/bin/python3
"""bko-farm netconsole receiver: the farm kernels' last words, on Bigfoot.

Listens on UDP 6666 (0.0.0.0) for netconsole packets and appends each line,
prefixed with Bigfoot's ISO time (milliseconds) and the sender's name, to
/var/log/bko-farm/netconsole/<source-ip>.log.  Names come from
/etc/bko-farm/hosts, re-resolved every 60 s (all addresses a name
resolves to, plus its seed IP), so a DHCP move is followed; an address not
in the list is logged as "?".  by-name/<name>.log is a symlink to the file
of the address that host last sent from.

A file over 256 MiB takes no more lines until logrotate has moved it (a
flood can't fill the disk); the drop is reported in the journal once an
hour.  Nothing a sender writes is ever run or parsed beyond splitting
lines.

Environment: BKO_FARM_HOSTS, BKO_FARM_NC_DIR, BKO_FARM_NC_PORT,
BKO_FARM_NC_BIND, BKO_FARM_NC_MAX (bytes).
"""

import datetime
import ipaddress
import os
import re
import socket
import subprocess
import sys
import threading
import time

HOSTS_FILE = os.environ.get("BKO_FARM_HOSTS", "/etc/bko-farm/hosts")
OUT_DIR = os.environ.get("BKO_FARM_NC_DIR", "/var/log/bko-farm/netconsole")
PORT = int(os.environ.get("BKO_FARM_NC_PORT", "6666"))
BIND = os.environ.get("BKO_FARM_NC_BIND", "0.0.0.0")
MAX_BYTES = int(os.environ.get("BKO_FARM_NC_MAX", str(256 << 20)))
REFRESH = 60

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")

names = {}            # ip -> host name, replaced whole by the refresher
names_lock = threading.Lock()


def log(msg):
    print(msg, flush=True)


def is_ipv4(s):
    try:
        return isinstance(ipaddress.ip_address(s), ipaddress.IPv4Address)
    except ValueError:
        return False


def read_hosts(path):
    hosts = []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                fields = raw.split("#", 1)[0].split()
                if len(fields) in (2, 3) and NAME_RE.match(fields[0]) \
                        and NAME_RE.match(fields[1]) \
                        and (len(fields) == 2 or is_ipv4(fields[2])):
                    hosts.append(fields)
    except OSError as e:
        log(f"<4>cannot read {path}: {e}")
    return hosts


def resolve_all(address):
    if is_ipv4(address):
        return [address]
    try:
        out = subprocess.run(["getent", "ahostsv4", address], capture_output=True,
                             text=True, timeout=3, check=False).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    return [line.split()[0] for line in out.splitlines()
            if line.split() and is_ipv4(line.split()[0])]


def refresh_names():
    global names
    last = {}
    while True:
        new = {}
        for fields in read_hosts(HOSTS_FILE):
            name, address = fields[0], fields[1]
            found = resolve_all(address)
            if found:
                last[name] = found
            for ip in last.get(name, []) + fields[2:]:
                new.setdefault(ip, name)
        with names_lock:
            names = new
        time.sleep(REFRESH)


def name_of(ip):
    with names_lock:
        return names.get(ip, "?")


def link_by_name(name, ip):
    if name == "?":
        return
    d = os.path.join(OUT_DIR, "by-name")
    link = os.path.join(d, f"{name}.log")
    target = os.path.join("..", f"{ip}.log")
    try:
        os.makedirs(d, exist_ok=True)
        if os.path.islink(link) and os.readlink(link) == target:
            return
        tmp = link + ".tmp"
        if os.path.lexists(tmp):
            os.unlink(tmp)
        os.symlink(target, tmp)
        os.replace(tmp, link)
    except OSError as e:
        log(f"<4>cannot link {link}: {e}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    threading.Thread(target=refresh_names, daemon=True).start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    sock.bind((BIND, PORT))
    log(f"<6>listening on udp {BIND}:{PORT}, writing {OUT_DIR}")
    seen = {}             # ip -> name it was last linked under
    dropped = {}          # ip -> lines dropped since the last report
    last_report = 0.0
    while True:
        data, (src, _) = sock.recvfrom(65535)
        try:
            ip = str(ipaddress.IPv4Address(src))
        except ValueError:
            continue
        name = name_of(ip)
        if seen.get(ip) != name:
            log(f"<5>netconsole from {ip} ({name})")
            seen[ip] = name
            link_by_name(name, ip)
        path = os.path.join(OUT_DIR, f"{ip}.log")
        text = data.decode("utf-8", "replace").replace("\0", "")
        lines = text.splitlines() or [""]
        try:
            if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
                dropped[ip] = dropped.get(ip, 0) + len(lines)
            else:
                stamp = datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")
                with open(path, "a", encoding="utf-8") as f:
                    f.write("".join(f"{stamp} {name} {line}\n" for line in lines))
        except OSError as e:
            log(f"<4>cannot write {path}: {e}")
        if dropped and time.monotonic() - last_report > 3600:
            for k, n in dropped.items():
                log(f"<4>{k}: {n} lines dropped, {k}.log is over {MAX_BYTES} bytes")
            dropped.clear()
            last_report = time.monotonic()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
