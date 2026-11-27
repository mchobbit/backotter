#!/usr/bin/env python3
"""img-seed.sh: the Linux image's inputs on more build hosts (docs/design/img-speed.md §5.1, D5,
D15), from the Mac.

Usage: img-seed.sh [--from HOST] [--check] [--media] [--builder] HOST...
       img-seed.sh [--from HOST] [--check] [--media] --seeded

For each HOST: /bkoscratch/imgbuild/server (root's) and ~/backotter-work/server -> it; the source's
current slim (its `server/slim` link's slim-YYYYMMDD) and the ISO its report.txt names, copied
as root with tar --numeric-owner --xattrs --acls over ssh, host to host (a name the host has
already is not copied again); then slim's manifest and the ISO checked against the source's
(img_inputs.py digest linux on both: the base digest's `slim` and `iso`), and only then
`slim -> slim-YYYYMMDD`.  Then the host tools make-img.sh and the layer build need:
squashfs-tools 4.6 or later, xorriso 1.5 or later, dosfstools and mtools, each version the
source's (D15: they are base inputs, so a host whose versions differ gets no Linux unit), sudo -n,
and HTTPS to archive.ubuntu.com, security.ubuntu.com and apt.bcachefs.org.

--from HOST   the source (default: the fleet host, Chopin: the plan's slim is its server/slim)
--check       change nothing: say whether each host is ready
--seeded      the hosts: every enabled amd64 build host but the source that has the server
              directory; run it whenever slim is repointed on the source, so every seeded host
              follows (the planner refuses a host whose slim differs)
--media       also mend img-media/P links that dangle into /bkoscratch/imgbuild/img-media: make
              the directory and fill it with the source's media of P (all but MEDIA, which only a
              host's own fetch writes; the fetch verifies them anyway)
--builder     also copy the source's vms/freebsd15-builder where the host's link dangles into
              /bkoscratch/imgbuild/vms (or it has none): under the source's .vm-update.lock,
              refused while its disk is open, then every file's SHA-256 checked on both and the
              source's files unchanged since.  Its fingerprint file comes with it, so the host's
              FreeBSD identity is the source's until a build there takes its own (§3.2)

The host half is stick/img-seed-host.sh, sent over ssh (no synced copy needed); the digests run
each host's own stick/img_inputs.py, as the planner's facts do.  Exit 0 when every host is
ready, 1 when one is not (each reason said), 2 on bad usage, 3 when the source is unusable.

For tests: BKO_SEED_SSH (the ssh words, here and on the hosts), BKO_HOSTS_JSON (the table).
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOST_SH = os.path.join(HERE, "img-seed-host.sh")
# Literal: slim's report.txt names its ISO by this path, and the copy keeps report.txt as it is.
SERVER = "/bkoscratch/imgbuild/server"
MIN_VERSION = {"squashfs-tools": (4, 6), "xorriso": (1, 5)}
TOOLS = ("squashfs-tools", "xorriso", "dosfstools", "mtools")
HTTPS = ("archive.ubuntu.com", "security.ubuntu.com", "apt.bcachefs.org")


class Usage(Exception):
    pass


class Failed(Exception):
    pass


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def hosts_mod():
    return _load("img_seed_hosts", os.path.join(REPO, "testvm", "test2", "t2lib", "hosts.py"))


def ssh_words():
    return shlex.split(os.environ.get("BKO_SEED_SSH") or "ssh -T -o BatchMode=yes -o ConnectTimeout=10")


def remote(dest, *args, check=True, stream=False):
    """img-seed-host.sh ARGS on DEST: (rc, stdout).  STREAM: its output goes straight to ours."""
    with open(HOST_SH, encoding="utf-8") as f:
        script = f.read()
    cmd = "bash -c " + shlex.quote(script) + " img-seed-host " + " ".join(shlex.quote(a) for a in args)
    r = subprocess.run(ssh_words() + [dest, cmd], stdin=subprocess.DEVNULL, text=True,
                       stdout=None if stream else subprocess.PIPE,
                       stderr=None if stream else subprocess.PIPE)
    if check and r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        raise Failed(f"{args[0]} on {dest} failed (exit {r.returncode})" + (f": {err[-1]}" if err else ""))
    return r.returncode, r.stdout or ""


def parse_facts(text):
    """img-seed-host.sh facts' lines -> a dict: media and builder as {P: (KIND, TARGET)}."""
    out = {"media": {}}
    for line in text.splitlines():
        c = line.split("\t")
        if not c or not c[0]:
            continue
        if c[0] == "media" and len(c) >= 4:
            out["media"][c[1]] = (c[2], c[3])
        elif c[0] == "builder" and len(c) >= 3:
            out["builder"] = (c[1], c[2])
        elif c[0] in ("slims", "isos"):
            out[c[0]] = [x for x in (c[1] if len(c) > 1 else "").split() if x]
        elif len(c) >= 2:
            out[c[0]] = c[1]
    return out


def parse_tools(text):
    out = {"tool": {}, "https": {}, "sudo": None}
    for line in text.splitlines():
        c = line.split("\t")
        if c[0] == "tool" and len(c) == 3:
            out["tool"][c[1]] = None if c[2] == "-" else c[2]
        elif c[0] == "https" and len(c) == 3:
            out["https"][c[1]] = c[2] == "yes"
        elif c[0] == "sudo" and len(c) == 2:
            out["sudo"] = c[1] == "yes"
    return out


def upstream(version):
    """A dpkg version's upstream numbers: "1:4.6.1-1" -> (4, 6, 1)."""
    v = version.split(":", 1)[-1].split("-", 1)[0]
    nums = []
    for part in v.split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        nums.append(int(m.group()))
    return tuple(nums)


def tool_problems(tools, src_versions):
    """What keeps a host's tools from building Linux images like the source (D15)."""
    bad = []
    for t in TOOLS:
        v = tools["tool"].get(t)
        if not v:
            bad.append(f"no {t}")
            continue
        if t in MIN_VERSION and upstream(v) < MIN_VERSION[t]:
            bad.append(f"{t} {v} (needs {'.'.join(map(str, MIN_VERSION[t]))} or later)")
        sv = (src_versions or {}).get(t)
        if sv != v:
            bad.append(f"{t} {v}, the source's is {sv} (D15: no Linux unit there)")
    if not tools.get("sudo"):
        bad.append("no sudo -n (the layer build runs as root)")
    for u in HTTPS:
        if not tools["https"].get(u):
            bad.append(f"no HTTPS to {u}")
    return bad


def linux_inputs(dest, slim, iso):
    """The base digest's Linux inputs on DEST, from its own img_inputs.py: iso, slim, host_tools."""
    _, out = remote(dest, "digest", slim, iso)
    try:
        base = json.loads(out)["base"]["inputs"]
    except (ValueError, KeyError, TypeError) as e:
        raise Failed(f"digest on {dest} said no base ({e})")
    return {k: base.get(k) for k in ("iso", "slim", "host_tools")}


def input_problems(got, want):
    bad = []
    if got.get("iso") != want.get("iso"):
        bad.append(f"the ISO is {str(got.get('iso'))[:12]}, the source's {str(want.get('iso'))[:12]}")
    if got.get("slim") != want.get("slim"):
        g, w = got.get("slim") or {}, want.get("slim") or {}
        bad.append(f"slim is {g.get('path')} ({str(g.get('manifest'))[:12]}), the source's "
                   f"{w.get('path')} ({str(w.get('manifest'))[:12]})")
    return bad


def source_state(dest):
    """The source's slim name, ISO name and Linux inputs (raises Failed when it has none)."""
    f = parse_facts(remote(dest, "facts")[1])
    slim = f.get("slim", "-")
    if not re.fullmatch(r"slim-\d{8}", slim or ""):
        raise Failed(f"the source's server/slim is {slim!r}, not a slim-YYYYMMDD")
    iso_path = f.get("source_iso", "-")
    if os.path.dirname(iso_path) != SERVER:
        raise Failed(f"the source's slim names its ISO {iso_path!r}, not one in {SERVER}")
    iso = os.path.basename(iso_path)
    if iso not in f.get("isos", []):
        raise Failed(f"the source has no {iso}")
    return f, slim, iso, linux_inputs(dest, slim, iso)


def seed_host(name, dest, src_dest, slim, iso, want, opts, say):
    """Seed (or with opts.check, look at) one host.  Returns its problems ([]: ready)."""
    f = parse_facts(remote(dest, "facts")[1])
    bad = []
    if not opts.check:
        remote(dest, "server")
        _, out = remote(dest, "pull", src_dest, slim, iso)
        for line in out.splitlines():
            say(f"{name}: {line}")
        got = linux_inputs(dest, slim, iso)
        bad = input_problems(got, want)
        if bad:
            say(f"{name}: not repointed: " + "; ".join(bad))
        elif f.get("slim") != slim:
            say(f"{name}: " + remote(dest, "repoint", slim)[1].strip())
        f = parse_facts(remote(dest, "facts")[1])
    if f.get("server_ok") != "yes":
        bad.append(f"~/backotter-work/server is {f.get('server_link')}, not a link to {SERVER}")
    if f.get("slim") != slim:
        bad.append(f"server/slim is {f.get('slim')}, the source's {slim}")
    got = None
    if slim in f.get("slims", []) and iso in f.get("isos", []):
        got = linux_inputs(dest, slim, iso)
        for b in input_problems(got, want):
            if b not in bad:
                bad.append(b)
    elif opts.check:
        bad.append(f"no {slim} or no {iso}")
    bad += tool_problems(parse_tools(remote(dest, "tools")[1]), want.get("host_tools"))
    if opts.media:
        dangling = sorted(p for p, (k, _) in f["media"].items() if k == "dangling")
        if dangling and not opts.check:
            _, out = remote(dest, "media", src_dest, *dangling)
            for line in out.splitlines():
                say(f"{name}: {line}")
            f = parse_facts(remote(dest, "facts")[1])
        bad += [f"img-media/{p} dangles ({t})" for p, (k, t) in sorted(f["media"].items()) if k == "dangling"]
    if opts.builder:
        k, t = f.get("builder", ("none", "-"))
        if k in ("dangling", "none") and not opts.check:
            seed_builder(name, dest, src_dest, say)
            k, t = parse_facts(remote(dest, "facts")[1]).get("builder", ("none", "-"))
        if k in ("dangling", "none"):
            bad.append(f"vms/freebsd15-builder is {k}" + (f" ({t})" if t != "-" else ""))
    return bad


def seed_builder(name, dest, src_dest, say):
    before = remote(src_dest, "builder-stat")[1]
    remote(dest, "builder", src_dest, stream=True)
    say(f"{name}: hashing the builder's files on both hosts")
    a = remote(src_dest, "builder-hash")[1]
    b = remote(dest, "builder-hash")[1]
    after = remote(src_dest, "builder-stat")[1]
    if a != b or before != after:
        why = "its files changed on the source while it was copied" if before != after else \
              "the copy's SHA-256 differ from the source's"
        remote(dest, "builder-drop", check=False)
        raise Failed(f"the builder copy to {name} is no good: {why} (moved aside)")
    say(f"{name}: builder copy checked: {len(a.splitlines())} files, each SHA-256 the source's")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="img-seed.sh", description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--seeded", action="store_true")
    ap.add_argument("--media", action="store_true")
    ap.add_argument("--builder", action="store_true")
    ap.add_argument("hosts", nargs="*")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0

    def say(m):
        print(f"img-seed: {m}", flush=True)

    try:
        if a.seeded == bool(a.hosts):
            raise Usage("name the hosts, or --seeded")
        H = hosts_mod()
        table = H.load()
        rows = [r for r in table["hosts"] if r.get("enabled", True) is not False]
        builders = [r["name"] for r in rows if "build" in (r.get("roles") or ())
                    and (r.get("arch") or "amd64") == "amd64"]
        src = a.src or next((r["name"] for r in rows if "fleet" in (r.get("roles") or ())), None)
        if not src:
            raise Usage("no --from and no fleet host in testvm/hosts.json")
        if src not in builders:
            raise Usage(f"--from {src}: not an amd64 build host ({', '.join(builders)})")
        src_dest = H.ssh(src, table)
        try:
            _, slim, iso, want = source_state(src_dest)
        except Failed as e:
            say(f"source {src}: {e}")
            return 3
        say(f"source {src}: {slim}, {iso} (slim {want['slim']['manifest'][:12]}, ISO {str(want['iso'])[:12]})")
        if a.seeded:
            hosts = []
            for h in builders:
                if h == src:
                    continue
                try:
                    if parse_facts(remote(H.ssh(h, table), "facts")[1]).get("server_dir") == "yes":
                        hosts.append(h)
                except Failed as e:
                    say(f"{h}: {e}")
            say("seeded hosts: " + (", ".join(hosts) or "none"))
        else:
            hosts = a.hosts
            for h in hosts:
                if h == src:
                    raise Usage(f"{h} is the source")
                if h not in builders:
                    raise Usage(f"{h} is not an amd64 build host ({', '.join(builders)})")
        verdict = {}
        for h in hosts:
            try:
                verdict[h] = seed_host(h, H.ssh(h, table), src_dest, slim, iso, want, a, say)
            except Failed as e:
                verdict[h] = [str(e)]
        for h in hosts:
            say(f"{h}: " + ("ready" if not verdict[h] else "NOT READY: " + "; ".join(verdict[h])))
        return 0 if all(not v for v in verdict.values()) else 1
    except Usage as e:
        print(f"img-seed: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
