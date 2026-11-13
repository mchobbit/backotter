#!/usr/bin/env python3
"""The KVM test hosts' table, testvm/hosts.json: its loader and its command line.

Every script that names the hosts takes them from the table: t2lib's KNOWN_HOSTS and
DEFAULT_HOSTS, drive.py's HOSTS, plan.py's GATE_FALLBACK, vm-update's fleet, bs2's ssh
targets, the gate layers' host_reserve by class; the shell's (guest-slot.sh, run-all.sh,
sync-hosts.sh, stick/build-round.sh) through testvm/hosts.sh, which runs this file.  It
is self-contained (stdlib only, Python 3.9 or newer, no import from t2lib): drive.py,
vm-update and bs2 load it by its path, and t2lib imports it as t2lib.hosts.

The table is found relative to this file (TESTVM/hosts.json, TESTVM being three
directories up: the checkout's testvm/, ~/backotter-work/testvm on the hosts, a private
copy's testvm/); BKO_HOSTS_JSON names another (tests).  A table that doesn't check
(check_table) is a HostsError: nothing falls back to built-in hosts.

A row (hosts.json's notes say more):
    name             the short host name (hostname -s), a-z first
    ssh              USER@HOST, the ssh destination (HOST: addr())
    ip, host_key_alias  an address for when mDNS fails and the name known_hosts pins it
                     under (null: none)
    slot_range       "LO-HI", guest-slot.sh's range (run-all.sh: PORT_BASE LO, GUEST_PORTS)
    stic_ports       [LO, HI], drive.py's check ports, inside slot_range
    class            one of the table's classes (big, small)
    roles            of the table's roles (build, fleet, store-mirror, bigfoot-link, cache)
    default          a host of a run without --hosts
    enabled          false: a reserved row (a host to come); no list or lookup has it
                     unless asked for every row (all_=True, --all)

A host's limits (LIMITS: each optional, in its row, else the table's class_limits for its
class, else the default; limits() and limit() resolve them, and a name not in the table
gets the defaults):
    stic_slots       3     SticTest's checks of ours at once there (drive.py --slots)
    guest_slots      3     guest slots drive.py counts as there (1-3: every check.sh takes
                           slot_take 3 on the host's range, so more could never be had)
    holt_jobs        3     HoltTest jobs at once there (ht1-queue.sh's MAXJOBS)
    base_gib         6     cmp-run.sh's BASE_GIB there, the GiB kept for the system
                           (BASE_GIB in the environment still wins)
    holt_disk_mode   false true: a holt line over the host's RAM-disk ceiling (its D) whose
                           memory fits runs on /bkoscratch there (HT_RAM=0, --reserve 0 M:
                           ht1-queue.sh's DISK_OVER), and placement checks its memory only
                           (not a +img line: ht1.sh runs +img only in the RAM disk)
    bs2_slots        3     BackShot2's tier 1 and 2 cases at once there (bs2-host queue
                           --slots), 1-6: its queue's ports are 2943-2948 (2949 is a by-hand
                           tier 1's)
The defaults are what every host had before the table had limits.

Command line (what hosts.sh wraps; exit 0 ok, 1 no such host, 2 usage or a bad table):
    hosts.py names [--all] [--default] [--role R] [--class C] [--field F]
                   the names, one a line, in the table's order (--field: that field instead)
    hosts.py get NAME FIELD [--all]
                   one field of NAME's row (NAME may be HOST.DOMAIN; a list: its
                   items, space-separated; null: an empty line)
    hosts.py phrase [--all]   the known names as "a, b, c or d", for messages
    hosts.py limit NAME FIELD  NAME's limit FIELD (limits above; a name not in the table: the
                              default), for cmp-run.sh's BASE_GIB
    hosts.py check            the table checks: prints its path
"""

import json
import os
import re
import sys

ENV = "BKO_HOSTS_JSON"
TESTVM = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TABLE = os.path.join(TESTVM, "hosts.json")
FIELDS = ("name", "ssh", "ip", "host_key_alias", "slot_range", "stic_ports", "class", "roles", "default",
          "enabled")
REQUIRED = ("name", "ssh", "slot_range", "stic_ports", "class", "roles", "default", "enabled")
# A host's limits and their defaults (the docstring): optional row fields, and the fields of
# the table's class_limits {CLASS: {FIELD: VALUE}}.
LIMITS = {"stic_slots": 3, "guest_slots": 3, "holt_jobs": 3, "base_gib": 6, "holt_disk_mode": False, "bs2_slots": 3}
FIELDS += tuple(LIMITS)
NAME_RE = re.compile(r"[a-z][a-z0-9-]*\Z")
SSH_RE = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+\Z")
RANGE_RE = re.compile(r"(\d+)-(\d+)\Z")

_cache = {}


class HostsError(Exception):
    """The table can't be read, doesn't check, or has no such host."""


class TableError(HostsError):
    """The table can't be read or doesn't check."""


def path():
    """The table's path: BKO_HOSTS_JSON, else TESTVM/hosts.json."""
    return os.environ.get(ENV) or TABLE


def parse_range(s):
    """"LO-HI" -> (LO, HI), or None when it isn't one."""
    m = RANGE_RE.match(s) if isinstance(s, str) else None
    if not m or int(m.group(1)) > int(m.group(2)):
        return None
    return int(m.group(1)), int(m.group(2))


def limit_error(k, v):
    """Why V can't be limit K's value (a string), or None: it can."""
    if k == "holt_disk_mode":
        return None if type(v) is bool else "true or false"
    if k == "base_gib":
        return None if type(v) in (int, float) and 0 <= v <= 64 else "a number of GiB, 0 to 64"
    if k == "guest_slots":
        return None if type(v) is int and 1 <= v <= 3 else "1 to 3 (every check.sh's slot_take 3)"
    if k == "bs2_slots":
        return None if type(v) is int and 1 <= v <= 6 else "1 to 6 (bs2-host queue's ports 2943-2948)"
    return None if type(v) is int and 1 <= v <= 16 else "a whole number, 1 to 16"


def check_table(t):
    """The table's errors, a list of strings (empty: it checks)."""
    errs = []
    if not isinstance(t, dict):
        return ["the table is not a JSON object"]
    if t.get("v") != 1:
        errs.append(f"v is {t.get('v')!r}, not 1")
    classes, roles, rows = t.get("classes"), t.get("roles"), t.get("hosts")
    if not isinstance(classes, dict) or not classes:
        errs.append("classes: an object of class names")
        classes = {}
    if not isinstance(roles, dict):
        errs.append("roles: an object of role names")
        roles = {}
    cl = t.get("class_limits", {})
    if not isinstance(cl, dict):
        errs.append("class_limits: an object of {CLASS: {LIMIT: VALUE}}")
        cl = {}
    for c, lims in cl.items():
        if c not in classes:
            errs.append(f"class_limits: class {c!r}: one of {', '.join(classes)}")
        if not isinstance(lims, dict):
            errs.append(f"class_limits.{c}: an object of {{LIMIT: VALUE}}")
            continue
        for k, v in lims.items():
            if k not in LIMITS:
                errs.append(f"class_limits.{c}: no such limit {k!r} (limits: {', '.join(LIMITS)})")
            elif limit_error(k, v):
                errs.append(f"class_limits.{c}: {k} {v!r}: {limit_error(k, v)}")
    if not isinstance(rows, list) or not rows:
        return errs + ["hosts: a list of rows"]
    seen, ranges = set(), []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            errs.append(f"hosts[{i}]: not an object")
            continue
        n = r.get("name")
        at = f"hosts[{i}] ({n})" if isinstance(n, str) else f"hosts[{i}]"
        for k in REQUIRED:
            if k not in r:
                errs.append(f"{at}: no {k}")
        for k in r:
            if k not in FIELDS:
                errs.append(f"{at}: no such field {k!r} (fields: {', '.join(FIELDS)})")
        if not isinstance(n, str) or not NAME_RE.match(n):
            errs.append(f"{at}: name {n!r}: lower-case letters, digits and -, a letter first")
        elif n in seen:
            errs.append(f"{at}: name {n} twice")
        else:
            seen.add(n)
        if "ssh" in r and not (isinstance(r["ssh"], str) and SSH_RE.match(r["ssh"])):
            errs.append(f"{at}: ssh {r['ssh']!r}: USER@HOST")
        for k in ("ip", "host_key_alias"):
            if r.get(k) is not None and not (isinstance(r[k], str) and r[k]):
                errs.append(f"{at}: {k}: a string or null")
        sr = parse_range(r.get("slot_range"))
        if "slot_range" in r and sr is None:
            errs.append(f"{at}: slot_range {r['slot_range']!r}: \"LO-HI\", LO <= HI")
        if sr:
            for other, (lo, hi) in ranges:
                if sr[0] <= hi and lo <= sr[1]:
                    errs.append(f"{at}: slot_range {r['slot_range']} overlaps {other}'s")
            ranges.append((n, sr))
        sp = r.get("stic_ports")
        if "stic_ports" in r:
            if not (isinstance(sp, list) and len(sp) == 2 and all(type(p) is int for p in sp) and sp[0] <= sp[1]):
                errs.append(f"{at}: stic_ports {sp!r}: [LO, HI], LO <= HI")
            elif sr and not (sr[0] <= sp[0] and sp[1] <= sr[1]):
                errs.append(f"{at}: stic_ports {sp} outside slot_range {r['slot_range']}")
        if "class" in r and r["class"] not in classes:
            errs.append(f"{at}: class {r['class']!r}: one of {', '.join(classes)}")
        rl = r.get("roles")
        if "roles" in r:
            if not (isinstance(rl, list) and all(isinstance(x, str) for x in rl) and len(set(rl)) == len(rl)):
                errs.append(f"{at}: roles: a list of role names, each once")
            else:
                for x in rl:
                    if x not in roles:
                        errs.append(f"{at}: role {x!r}: one of {', '.join(roles)}")
        for k in ("default", "enabled"):
            if k in r and type(r[k]) is not bool:
                errs.append(f"{at}: {k}: true or false")
        for k in LIMITS:
            if k in r and limit_error(k, r[k]):
                errs.append(f"{at}: {k} {r[k]!r}: {limit_error(k, r[k])}")
        if r.get("default") is True and r.get("enabled") is False:
            errs.append(f"{at}: a default host must be enabled")
    if not any(isinstance(r, dict) and r.get("default") is True for r in rows):
        errs.append("no default host")
    return errs


def load(p=None):
    """The checked table at P (default path()), read once per path and mtime."""
    p = p or path()
    try:
        st = os.stat(p)
        key = (p, st.st_mtime_ns, st.st_size)
        if key in _cache:
            return _cache[key]
        with open(p, encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError) as e:
        raise TableError(f"the host table {p}: {e}") from None
    errs = check_table(t)
    if errs:
        raise TableError(f"the host table {p}: " + "; ".join(errs))
    _cache[key] = t
    return t


def rows(all_=False, table=None):
    """The rows in the table's order: the enabled ones, or every one."""
    t = table if table is not None else load()
    return [r for r in t["hosts"] if all_ or r["enabled"]]


def names(all_=False, default=None, role=None, cls=None, table=None):
    """Names, in the table's order: enabled (all_: every row), default (None: either),
    with ROLE, of class CLS."""
    return tuple(r["name"] for r in rows(all_, table)
                 if (default is None or r["default"] is default) and (role is None or role in r["roles"])
                 and (cls is None or r["class"] == cls))


def known(table=None):
    """What --hosts may name: every enabled host."""
    return names(table=table)


def defaults(table=None):
    """The hosts of a run without --hosts."""
    return names(default=True, table=table)


def with_role(role, table=None):
    return names(role=role, table=table)


def short(hostname):
    """chopin.local -> chopin."""
    return str(hostname).split(".")[0]


def phrase(ns):
    """("a", "b", "c") -> "a, b or c"."""
    ns = list(ns)
    return ", ".join(ns[:-1]) + " or " + ns[-1] if len(ns) > 1 else "".join(ns)


def find(name, all_=False, table=None):
    """NAME's row (NAME or NAME.DOMAIN), or None: not in the table (not enabled, unless all_)."""
    n = short(name)
    for r in rows(all_, table):
        if r["name"] == n:
            return r
    return None


def row(name, all_=False, table=None):
    """NAME's row; a HostsError names the known ones when there's none."""
    r = find(name, all_, table)
    if r is None:
        t = table if table is not None else load()
        if not all_ and find(name, True, t) is not None:
            raise HostsError(f"{short(name)} is not enabled in the host table (a reserved row)")
        raise HostsError(f"no such host {short(name)} (known: {', '.join(names(all_, table=t))})")
    return r


def host_class(name, table=None):
    """NAME's class, or None when it isn't a known host."""
    r = find(name, table=table)
    return r["class"] if r else None


def limits(name, table=None):
    """NAME's limits, {LIMIT: VALUE} for every one of LIMITS: its row's, else its class's
    (class_limits), else the default.  A name not in the table (a reserved row's included)
    gets its row's and class's still, a name in no row the defaults."""
    t = table if table is not None else load()
    r = find(name, True, t) or {}
    cls = (t.get("class_limits") or {}).get(r.get("class")) or {}
    return {k: r[k] if k in r else cls.get(k, d) for k, d in LIMITS.items()}


def limit(name, field, table=None):
    """One of NAME's limits (limits())."""
    if field not in LIMITS:
        raise HostsError(f"no such limit {field} (limits: {', '.join(LIMITS)})")
    return limits(name, table)[field]


def class_rank(name, table=None):
    """NAME's class's place in the table's classes (0 the first, the biggest: big 0, small 1);
    0 for a name in no row.  Placement prefers, on a host, the cells no host of a later class
    could take."""
    t = table if table is not None else load()
    r = find(name, True, t)
    return list(t["classes"]).index(r["class"]) if r else 0


def ssh(name, table=None):
    return row(name, table=table)["ssh"]


def addr(name, table=None):
    """The host part of NAME's ssh destination: chopin.local."""
    return ssh(name, table).split("@", 1)[1]


def slot_range(name, all_=False, table=None):
    """(LO, HI) of NAME's guest-slot range."""
    return parse_range(row(name, all_, table)["slot_range"])


def stic_ports(name, table=None):
    return tuple(row(name, table=table)["stic_ports"])


def ssh_fallback(name, table=None):
    """ssh arguments that reach NAME by its ip, known_hosts checking it as host_key_alias
    (["-o", "HostKeyAlias=ALIAS", "USER@IP"]); None when the row has no ip."""
    r = row(name, table=table)
    if not r.get("ip"):
        return None
    user = r["ssh"].split("@", 1)[0]
    alias = r.get("host_key_alias") or addr(name, table)
    return ["-o", f"HostKeyAlias={alias}", f"{user}@{r['ip']}"]


# --- the command line --------------------------------------------------------------------

def _value(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return " ".join(_value(x) for x in v)
    return str(v)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = ("usage: hosts.py names [--all] [--default] [--role R] [--class C] [--field F] | "
             "get NAME FIELD [--all] | limit NAME FIELD | phrase [--all] | check")
    if not argv:
        print(usage, file=sys.stderr)
        return 2
    word, rest = argv[0], argv[1:]
    opts, pos = {}, []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--all", "--default"):
            opts[a] = True
        elif a in ("--role", "--class", "--field"):
            if i + 1 >= len(rest):
                print(f"hosts.py: {a} needs a value", file=sys.stderr)
                return 2
            opts[a] = rest[i + 1]
            i += 1
        elif a.startswith("-"):
            print(f"hosts.py: no such option {a}\n{usage}", file=sys.stderr)
            return 2
        else:
            pos.append(a)
        i += 1
    try:
        t = load()
        if word == "names" and not pos:
            f = opts.get("--field", "name")
            if f not in FIELDS:
                print(f"hosts.py: no such field {f} (fields: {', '.join(FIELDS)})", file=sys.stderr)
                return 2
            for n in names(opts.get("--all", False), True if opts.get("--default") else None,
                           opts.get("--role"), opts.get("--class"), table=t):
                print(_value(row(n, True, t).get(f)))
            return 0
        if word == "get" and len(pos) == 2 and not set(opts) - {"--all"}:
            if pos[1] not in FIELDS:
                print(f"hosts.py: no such field {pos[1]} (fields: {', '.join(FIELDS)})", file=sys.stderr)
                return 2
            print(_value(row(pos[0], opts.get("--all", False), t).get(pos[1])))
            return 0
        if word == "limit" and len(pos) == 2 and not opts:
            if pos[1] not in LIMITS:
                print(f"hosts.py: no such limit {pos[1]} (limits: {', '.join(LIMITS)})", file=sys.stderr)
                return 2
            print(_value(limit(pos[0], pos[1], t)))
            return 0
        if word == "phrase" and not pos:
            print(phrase(names(opts.get("--all", False), table=t)))
            return 0
        if word == "check" and not pos and not opts:
            print(path())
            return 0
    except HostsError as e:
        print(f"hosts.py: {e}", file=sys.stderr)
        return 2 if isinstance(e, TableError) else 1
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
