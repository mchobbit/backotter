#!/usr/bin/python3
"""bko-farm heartbeat: watch the farm hosts from Bigfoot.

Every 15 s, for each host in /etc/bko-farm/hosts: resolve its name (mDNS,
falling back to the last address that worked), ping it once and open TCP
port 22.  A host is

  up         sshd answered with its banner,
  ping-only  ping answered but sshd did not (no TCP connect, or a connect
             the kernel accepted with no banner behind it: a frozen
             userspace still completes the TCP handshake),
  down       neither answered.

A change of state is logged once it has been seen on CONFIRM probes in a
row (default 2), dated from the first of them.  Changes go to the journal
(SyslogIdentifier=bko-farm-heartbeat, from the unit) and to
/var/log/bko-farm/heartbeat.log; a return to up says how long the host was
not up.  /var/log/bko-farm/status is rewritten every cycle, one line per
host.  State survives a restart in /var/lib/bko-farm/heartbeat.json.

Settings come from the environment (the unit sets none, so these defaults
hold): BKO_FARM_HOSTS, BKO_FARM_LOG_DIR, BKO_FARM_STATE_DIR,
BKO_FARM_INTERVAL (s), BKO_FARM_CONFIRM.  --once runs one cycle and prints
the status (for a test by hand).
"""

import datetime
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HOSTS_FILE = os.environ.get("BKO_FARM_HOSTS", "/etc/bko-farm/hosts")
LOG_DIR = os.environ.get("BKO_FARM_LOG_DIR", "/var/log/bko-farm")
STATE_DIR = os.environ.get("BKO_FARM_STATE_DIR", "/var/lib/bko-farm")
INTERVAL = float(os.environ.get("BKO_FARM_INTERVAL", "15"))
CONFIRM = max(1, int(os.environ.get("BKO_FARM_CONFIRM", "2")))

LOG_FILE = os.path.join(LOG_DIR, "heartbeat.log")
STATUS_FILE = os.path.join(LOG_DIR, "status")
STATE_FILE = os.path.join(STATE_DIR, "heartbeat.json")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
RANK = {"up": 0, "ping-only": 1, "down": 2}

# sd-daemon priority prefixes, honoured by journald on stdout.
WARN, NOTICE, INFO = "<4>", "<5>", "<6>"


def now():
    return datetime.datetime.now().astimezone()


def iso(t):
    return t.isoformat(timespec="seconds") if t else "-"


def parse_iso(s):
    try:
        return datetime.datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def dur(seconds):
    seconds = int(max(0, seconds))
    d, rest = divmod(seconds, 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    if d:
        return f"{d}d{h:02d}h{m:02d}m"
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def is_ipv4(s):
    try:
        return isinstance(ipaddress.ip_address(s), ipaddress.IPv4Address)
    except ValueError:
        return False


def read_hosts(path):
    """[(name, address, seed-ip or None)], in file order; bad lines skipped."""
    hosts = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        emit(WARN, f"cannot read {path}: {e}")
        return hosts
    seen = set()
    for raw in lines:
        fields = raw.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) not in (2, 3) or not NAME_RE.match(fields[0]) \
                or not NAME_RE.match(fields[1]) \
                or (len(fields) == 3 and not is_ipv4(fields[2])) \
                or fields[0] in seen:
            emit(WARN, f"{path}: ignoring line: {raw.strip()}")
            continue
        seen.add(fields[0])
        hosts.append((fields[0], fields[1], fields[2] if len(fields) == 3 else None))
    return hosts


def resolve_all(address):
    """Every IPv4 address the name resolves to now (nss: files, mDNS, DNS)."""
    if is_ipv4(address):
        return [address]
    try:
        out = subprocess.run(["getent", "ahostsv4", address], capture_output=True,
                             text=True, timeout=3, check=False).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    found = []
    for line in out.splitlines():
        fields = line.split()
        if fields and is_ipv4(fields[0]) and fields[0] not in found:
            found.append(fields[0])
    return found


def ping(ip):
    try:
        return subprocess.run(["ping", "-n", "-q", "-c1", "-W2", ip],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5, check=False).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def ssh_probe(ip):
    """(tcp connected, banner seen)."""
    try:
        s = socket.create_connection((ip, 22), timeout=2)
    except OSError:
        return False, False
    try:
        s.settimeout(3)
        data = b""
        while b"\n" not in data and len(data) < 256:
            chunk = s.recv(256)
            if not chunk:
                break
            data += chunk
        return True, data.startswith(b"SSH-")
    except OSError:
        return True, False
    finally:
        s.close()


def probe(name, address, seed, last_ip):
    """One host: (ip used, resolved by name now, state, detail, any answer)."""
    resolved = resolve_all(address)
    if resolved:
        # Stay on the address we had when it is still among the answers
        # (cinqcent publishes its Wi-Fi address too).
        ip = next((a for a in (last_ip, seed) if a in resolved), resolved[0])
    else:
        ip = last_ip or seed
    if not ip:
        return None, False, "down", "name does not resolve", False
    pinged = ping(ip)
    tcp, banner = ssh_probe(ip)
    if banner:
        return ip, bool(resolved), "up", "", True
    if pinged:
        detail = "tcp/22 accepted, no ssh banner" if tcp else "no tcp/22"
        return ip, bool(resolved), "ping-only", detail, True
    if tcp:
        # Accepted without a banner and no ping: count it as not up.
        return ip, bool(resolved), "ping-only", "no ping; tcp/22 accepted, no ssh banner", True
    return ip, bool(resolved), "down", "", False


def emit(prio, msg, to_file=False, t=None):
    print(f"{prio}{msg}", flush=True)
    if to_file:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{iso(t or now())} {msg}\n")
        except OSError as e:
            print(f"{WARN}cannot write {LOG_FILE}: {e}", flush=True)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print(f"{WARN}cannot write {STATE_FILE}: {e}", flush=True)


def write_status(hosts, state, t):
    lines = [f"# bko-farm heartbeat {iso(t)}, every {INTERVAL:g} s; "
             "NAME STATE IP SINCE LAST-UP LAST-ANSWER DETAIL"]
    for name, _, _ in hosts:
        h = state.get(name, {})
        lines.append("{:<10} {:<9} {:<15} since={} last-up={} last-answer={}{}".format(
            name, h.get("state", "?"), h.get("ip") or "-", h.get("since") or "-",
            h.get("last_up") or "-", h.get("last_answer") or "-",
            f"  ({h['detail']})" if h.get("detail") else ""))
    tmp = STATUS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, STATUS_FILE)
    except OSError as e:
        print(f"{WARN}cannot write {STATUS_FILE}: {e}", flush=True)


def update(name, h, result, t):
    """Fold one probe result into host state h; log what changed."""
    ip, by_name, new, detail, answered = result
    ts = iso(t)
    if ip and h.get("ip") and ip != h["ip"]:
        emit(NOTICE, f"{name}: address {h['ip']} -> {ip}", True, t)
    if ip and (by_name or not h.get("ip")):
        h["ip"] = ip
    if answered:
        h["last_answer"] = ts
    if new == "up":
        h["last_up"] = ts
    h["detail"] = detail

    old = h.get("state")
    if old is None:
        h.update(state=new, since=ts, cand=None, cand_n=0, cand_t=None)
        if new != "up":
            h["left_up"] = ts
        emit(NOTICE if new == "up" else WARN,
             f"{name}: {new} at start ({ip or 'no address'}){', ' + detail if detail else ''}",
             True, t)
        return
    if new == old:
        h.update(cand=None, cand_n=0, cand_t=None)
        return
    # Leaving "up", any mix of not-up probes counts toward CONFIRM (a host
    # that flips between down and ping-only must not stay "up"); the state
    # taken is the latest one.  Between not-up states, and back to up, the
    # same state must repeat.
    key = "not-up" if old == "up" else new
    if h.get("cand") == key:
        h["cand_n"] = h.get("cand_n", 0) + 1
    else:
        h.update(cand=key, cand_n=1, cand_t=ts)
    if h["cand_n"] < CONFIRM:
        return
    since = h["cand_t"] or ts
    since_t = parse_iso(since) or t
    was_for = dur((since_t - (parse_iso(h.get("since")) or since_t)).total_seconds())
    if new == "up":
        left = parse_iso(h.get("left_up"))
        gone = f"after {dur((since_t - left).total_seconds())} not up" if left else "again"
        msg = f"{name}: {old} -> up at {since} {gone} (since {iso(left)}; {ip})"
        h["left_up"] = None
        emit(NOTICE, msg, True, t)
    else:
        if old == "up":
            h["left_up"] = since
        msg = (f"{name}: {old} -> {new} at {since} ({ip or 'no address'}; "
               f"was {old} for {was_for}; last up {h.get('last_up') or '-'}"
               f"{'; ' + detail if detail else ''})")
        emit(WARN if RANK[new] > RANK[old] else NOTICE, msg, True, t)
    h.update(state=new, since=since, cand=None, cand_n=0, cand_t=None)


def cycle(pool, state):
    hosts = read_hosts(HOSTS_FILE)
    t = now()
    futures = {name: pool.submit(probe, name, addr, seed, state.get(name, {}).get("ip"))
               for name, addr, seed in hosts}
    for name, fut in futures.items():
        try:
            result = fut.result(timeout=INTERVAL)
        except Exception as e:  # pylint: disable=broad-except
            emit(WARN, f"{name}: probe failed: {e}")
            continue
        update(name, state.setdefault(name, {}), result, t)
    for gone in [n for n in state if n not in futures]:
        del state[gone]
    save_state(state)
    write_status(hosts, state, t)
    return hosts


def main():
    once = "--once" in sys.argv[1:]
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    state = load_state()
    emit(INFO, f"starting: {HOSTS_FILE}, every {INTERVAL:g} s, "
               f"{CONFIRM} probe(s) to confirm a change")
    with ThreadPoolExecutor(max_workers=16) as pool:
        if once:
            cycle(pool, state)
            with open(STATUS_FILE, encoding="utf-8") as f:
                sys.stdout.write(f.read())
            return 0
        nxt = time.monotonic()
        while True:
            cycle(pool, state)
            nxt += INTERVAL
            delay = nxt - time.monotonic()
            if delay < 0:  # a slow cycle: start the next one now, no catch-up
                nxt = time.monotonic()
                delay = 0
            time.sleep(delay)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
