#!/usr/bin/env python3
"""Test2's host probe (contract 1.4; rulings R23, R26): what this KVM test host
has that keys and preflight read, as one JSON object.  It reads only, makes
no image sums (t2-host imgsum does, into a cache this reads), never prints a
serial, a MAC or a passphrase (only a guest file's sha256), and takes well
under 3 s.  Self-contained: stdlib only, Python 3.9+, no t2lib imports, so
that it runs from stdin:

    ssh m@HOST.local python3 -I - [ARGS] < facts_host.py
    t2-host facts [--want JSON]

ARGS: --want JSON ({"sets": [...], "plumbing": ["shared:PATH"...], "host_inputs": [PATH...],
"guests": [NAME...]: only those guests; default every one}),
--work DIR (~/backotter-work), --host-root DIR (T2_HOST_ROOT, /bkoscratch/test2),
--copy-root DIR (T2_COPY_ROOT, ~/backotter-work/t2/copy), --user NAME (the
work directory's owner; for the kvm group).  T2_FACTS_ROOT=DIR reads every
system path (/proc, /sys, /etc, /var/lib/dpkg, /bkoscratch, /mnt/bko-ram)
under DIR instead: a fake root for tests.

Fields: host, t, versions, preflight, sets, rounds, holt_bins, guests,
rescuers, plumbing, host_inputs, synced, copies, errors, took_s.
"""

import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

V = 1
ROOT = os.environ.get("T2_FACTS_ROOT", "").rstrip("/")
VIRTUAL = re.compile(r"(zram|loop|nbd|md|ram|sr|zd)\d+|dm-\d+")   # wear-report.py:37
GUEST_FILES = ("vm-run-options", "ssh-user", "console-passphrase", "luks-passphrase", "zfs-passphrase", "no-ssh")
PKGS = ("e2fsprogs", "xfsprogs", "btrfs-progs", "f2fs-tools", "jfsutils", "nilfs-tools", "dosfstools",
        "zfsutils-linux", "attr", "acl", "mdadm", "lvm2", "cryptsetup-bin", "util-linux", "binutils",
        "fdisk", "gdisk")
DEFAULT_PLUMBING = ("shared:cmp-run.sh", "shared:testvm/compare/iolog.sh")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")


def R(p):
    """A system path, under the fake root when there is one."""
    return ROOT + p if ROOT and p.startswith("/") else p


def text(path, limit=1 << 20):
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def first_line(path):
    t = text(path, 65536)
    return t.split("\n", 1)[0].strip() if t else None


def sha_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def run_first_line(argv, timeout=5):
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        out = (p.stdout or p.stderr).decode("utf-8", "replace").strip()
        return out.split("\n", 1)[0].strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def dir_digest(path):
    """dir: digest: sha256 over sorted "relpath<TAB>sha256" lines of the regular
    files, "relpath<TAB>->target" for symlinks, each with its newline."""
    lines = []
    for dp, dns, fns in os.walk(path):
        dns.sort()
        for n in list(dns):
            if os.path.islink(os.path.join(dp, n)):
                rel = os.path.relpath(os.path.join(dp, n), path).replace(os.sep, "/")
                lines.append(f"{rel}\t->{os.readlink(os.path.join(dp, n))}")
                dns.remove(n)
        for n in fns:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, path).replace(os.sep, "/")
            if os.path.islink(p):
                lines.append(f"{rel}\t->{os.readlink(p)}")
            elif os.path.isfile(p):
                lines.append(f"{rel}\t{sha_file(p)}")
    return hashlib.sha256("".join(l + "\n" for l in sorted(lines)).encode("utf-8")).hexdigest()


def dpkg_versions(names):
    """{package: version} of the installed ones of NAMES, from /var/lib/dpkg/status."""
    want, out = set(names), {}
    t = text(R("/var/lib/dpkg/status"), 64 << 20)
    if not t:
        return out
    for stanza in t.split("\n\n"):
        pkg = ver = status = None
        for line in stanza.split("\n"):
            if line.startswith("Package: "):
                pkg = line[9:].strip()
            elif line.startswith("Version: "):
                ver = line[9:].strip()
            elif line.startswith("Status: "):
                status = line[8:].strip()
        if pkg in want and ver and status and status.endswith(" installed"):
            out[pkg] = ver
    return out


def versions(home):
    v = {}
    k = first_line(R("/proc/sys/kernel/osrelease"))
    v["kernel"] = k or (os.uname().release if not ROOT else None)
    v["zfs"] = first_line(R("/sys/module/zfs/version"))
    q = R("/usr/bin/qemu-system-x86_64") if ROOT else shutil.which("qemu-system-x86_64")
    v["qemu"] = run_first_line([q, "--version"]) if q and os.path.exists(q) else None
    pk = dpkg_versions(PKGS + ("ovmf",))
    v["ovmf"] = pk.pop("ovmf", None)
    v["pkgs"] = {p: pk.get(p) for p in PKGS}
    v["rustc"] = rustc_version(home)
    go = None if ROOT else shutil.which("go")
    if not go:
        cands = sorted(glob.glob(os.path.join(home, "sdk/go*/bin/go")),
                       key=lambda p: [int(x) for x in re.findall(r"\d+", p.split("/sdk/")[-1])])
        go = cands[-1] if cands else None
    v["go"] = go_version(go)
    return v


def go_version(go):
    """The go toolchain's VERSION file's first line (go1.25.10), read, not run:
    running go writes its telemetry counters into the caller's home."""
    if not go:
        return None
    return first_line(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(go))), "VERSION"))


def rustc_version(home):
    """rustc --version of HOME's rustup default toolchain, run from the toolchain
    itself (the real rustc writes nothing; the ~/.cargo/bin proxy is rustup's,
    and isn't run as root); the proxy only without a rustup default, as a user."""
    tc = None
    for line in (text(os.path.join(home, ".rustup/settings.toml"), 65536) or "").split("\n"):
        m = re.match(r'\s*default_toolchain\s*=\s*"([^"/]+)"', line)
        if m:
            tc = m.group(1)
            break
    if tc:
        tdir = os.path.join(home, ".rustup/toolchains")
        names = [tc] + sorted(n for n in (os.listdir(tdir) if os.path.isdir(tdir) else []) if n.startswith(tc + "-"))
        for n in names:
            rc = os.path.join(tdir, n, "bin/rustc")
            if os.path.isfile(rc):
                return run_first_line([rc, "--version"])
    rc = os.path.join(home, ".cargo/bin/rustc")
    if os.path.exists(rc) and os.geteuid() != 0:
        return run_first_line([rc, "--version"])
    return None


def meminfo():
    out = {}
    for line in (text(R("/proc/meminfo")) or "").split("\n"):
        k, _, rest = line.partition(":")
        if rest.strip():
            try:
                out[k] = int(rest.split()[0])
            except ValueError:
                pass
    return out


def group_members(user):
    """True when USER is in group kvm (by /etc/group, or as its primary group)."""
    gid, members = None, []
    for line in (text(R("/etc/group")) or "").split("\n"):
        f = line.split(":")
        if len(f) >= 4 and f[0] == "kvm":
            gid, members = f[2], [m for m in f[3].split(",") if m]
    if gid is None:
        return False
    if user in members:
        return True
    for line in (text(R("/etc/passwd")) or "").split("\n"):
        f = line.split(":")
        if len(f) >= 4 and f[0] == user:
            return f[3] == gid
    return False


def statvfs(path):
    try:
        s = os.statvfs(path)
        return s.f_blocks * s.f_frsize, s.f_bavail * s.f_frsize
    except OSError:
        return None, None


def rows(path):
    t = text(path)
    return None if t is None else sum(1 for l in t.split("\n") if l.strip())


def preflight(user):
    p = {}
    tctl, nvme = None, {}
    for h in sorted(glob.glob(R("/sys/class/hwmon/hwmon*"))):
        name = first_line(os.path.join(h, "name"))
        t = first_line(os.path.join(h, "temp1_input"))
        if not t or not t.lstrip("-").isdigit():
            continue
        c = int(t) / 1000.0
        if name == "k10temp":
            tctl = c if tctl is None else max(tctl, c)
        elif name == "nvme":
            dev = os.path.basename(os.readlink(os.path.join(h, "device"))) if os.path.islink(os.path.join(h, "device")) else os.path.basename(h)
            nvme[dev] = c
    p["tctl_c"], p["nvme_c"] = tctl, nvme
    mounts = text(R("/proc/mounts")) or ""
    mounted = any(l.split()[1:2] == ["/mnt/bko-ram"] for l in mounts.split("\n") if l.strip())
    size, free = statvfs(R("/mnt/bko-ram")) if mounted else (None, None)
    p["ram"] = {"mounted": mounted, "size": size, "free": free,
                "reserve_rows": rows(R("/mnt/bko-ram/.reserve")), "wait_rows": rows(R("/mnt/bko-ram/.reserve.wait"))}
    mi = meminfo()
    p["mem_available_kib"] = mi.get("MemAvailable")
    p["swap_used_kib"] = (mi["SwapTotal"] - mi["SwapFree"]) if "SwapTotal" in mi and "SwapFree" in mi else None
    p["pswpout"] = None
    for line in (text(R("/proc/vmstat")) or "").split("\n"):
        if line.startswith("pswpout "):
            p["pswpout"] = int(line.split()[1])
    p["kvm"] = group_members(user)
    _, sfree = statvfs(R("/bkoscratch"))
    p["scratch_free_gb"] = None if sfree is None else round(sfree / 1e9, 1)
    disks = {}
    try:
        names = [n for n in os.listdir(R("/sys/block")) if not VIRTUAL.fullmatch(n)]
    except OSError:
        names = []
    stats = {}
    for line in (text(R("/proc/diskstats")) or "").split("\n"):
        f = line.split()
        if len(f) >= 10:
            stats[f[2]] = int(f[9])
    for n in sorted(names):
        if n in stats:
            disks[n] = {"model": (first_line(R(f"/sys/block/{n}/device/model")) or "-").replace(" ", "_"),
                        "sectors_written": stats[n]}
    p["diskstats"] = disks
    return p


def sums_file(path):
    """{OS/FILE: hex} of a SHA256SUMS ("HASH *OS/FILE" in bko-bin, "HASH  ./OS/FILE" in a round)."""
    out = {}
    for line in (text(path) or "").split("\n"):
        f = line.split(None, 1)
        if len(f) == 2 and HEX64.match(f[0]):
            n = f[1].strip()
            n = n[1:] if n.startswith("*") else n
            n = n[2:] if n.startswith("./") else n
            out[n] = f[0]
    return out


def set_dir(work, name):
    for d in (os.path.join(work, name, "bin"), os.path.join(work, name), name):
        if os.path.isfile(os.path.join(d, "SHA256SUMS")):
            return d
    return None


def commit_of(path):
    t = text(path, 65536)
    if not t:
        return None, None
    lines = t.split("\n")
    c = lines[0].split()[0] if lines[0].split() else None
    core = None
    for l in lines[1:]:
        f = l.split()
        if len(f) >= 2 and f[0] == "rustic_core" and HEX40.match(f[1]):
            core = f[1]
    return (c if c and HEX40.match(c) else None), core


def one_set(work, name):
    d = set_dir(work, name)
    if d is None:
        return None
    s = {"path": d, "sums": sums_file(os.path.join(d, "SHA256SUMS")), "commits": {}, "digests": {},
         "digest_texts": {}, "build_info": first_line(os.path.join(d, "BUILD-INFO"))}
    for osd in sorted(os.listdir(d)):
        od = os.path.join(d, osd)
        if not os.path.isdir(od):
            continue
        rc, _ = commit_of(os.path.join(od, "restic-commit"))
        if rc:
            s["commits"][f"{osd}/restic-bko"] = rc
        uc, core = commit_of(os.path.join(od, "rustic-commit"))
        if uc:
            s["commits"][f"{osd}/rustic-bko"] = {"commit": uc, "rustic_core": core}
        for f in sorted(os.listdir(od)):
            if f.endswith(".digest"):
                t = text(os.path.join(od, f), 65536) or ""
                n = f"{osd}/{f[:-len('.digest')]}"
                dl = [l[7:].strip() for l in t.split("\n") if l.startswith("digest ")]
                s["digests"][n] = dl[-1] if dl else None
                s["digest_texts"][n] = "".join(l + "\n" for l in t.split("\n")
                                               if l.strip() and not l.startswith(("built on ", "reused ")))
    return s


def rounds(work):
    out = {}
    for d in sorted(glob.glob(os.path.join(work, "round*", "bin"))):
        name = os.path.basename(os.path.dirname(d))[len("round"):]
        rc, _ = commit_of(os.path.join(d, "linux", "restic-commit"))
        uc, core = commit_of(os.path.join(d, "linux", "rustic-commit"))
        out[name] = {"path": d, "restic": rc, "rustic": uc, "rustic_core": core,
                     "sums": os.path.isfile(os.path.join(d, "SHA256SUMS"))}
    return out


def build_info_sums(code_dir):
    """{PLAT: sha256} from a holt-bin/CODE directory's BUILD-INFO files (R26:
    hashed at the build), "linux" for the top-level bko-holt."""
    out = {}
    for bi in [os.path.join(code_dir, "BUILD-INFO")] + sorted(glob.glob(os.path.join(code_dir, "*", "BUILD-INFO"))):
        t = text(bi)
        if not t:
            continue
        here = os.path.basename(os.path.dirname(bi))
        plat_default = "linux" if os.path.dirname(bi) == code_dir else here
        try:
            j = json.loads(t)
        except ValueError:
            j = None
        if isinstance(j, dict):
            for k in ("sums", "sha256s"):
                if isinstance(j.get(k), dict):
                    for p, h in j[k].items():
                        if isinstance(h, str) and HEX64.match(h):
                            out[p.split("/")[0] if "/" in p else (plat_default if p == "bko-holt" else p)] = h
            if isinstance(j.get("sha256"), str) and HEX64.match(j["sha256"]):
                out[j.get("plat") or plat_default] = j["sha256"]
            continue
        for line in t.split("\n"):
            toks = line.replace("=", " ").replace(":", " ").split()
            hexes = [x for x in toks if HEX64.match(x) and x != os.path.basename(code_dir)]   # not CODE itself
            named = any(w.lower() in ("sha256", "sha256sum") for w in toks) or any(x.endswith("bko-holt") for x in toks)
            if len(hexes) != 1 or not named:
                continue
            path = next((x for x in toks if x.endswith("bko-holt")), None)
            plat = plat_default
            if path and "/" in path:
                parent = path.rstrip("/").split("/")[-2]
                if os.path.isdir(os.path.join(code_dir, parent)):
                    plat = parent
            out.setdefault(plat, hexes[0])
    return out


def holt_bins(host_root, extra=(), home=""):
    """bko-holt sets: holttest1d's bin/, each HOST_ROOT/holt-bin/CODE, and the --holt-bin
    directories asked for (EXTRA: named as asked, their sums from their SHA256SUMS)."""
    out = {}
    for d in extra or ():
        q = R(d) if d.startswith("/") else expand_home(d, home)
        if os.path.isfile(os.path.join(q, "SHA256SUMS")):
            sums = {}
            for n, h in sums_file(os.path.join(q, "SHA256SUMS")).items():
                if n == "bko-holt":
                    sums["linux"] = h
                elif n.endswith("/bko-holt"):
                    sums[n[:-len("/bko-holt")]] = h
            out[d] = {"path": q, "sums": sums, "build_info": first_line(os.path.join(q, "BUILD-INFO"))}
    hb = R("/bkoscratch/holttest1d/bin")
    if os.path.isdir(hb):
        sums = {}
        if os.path.isfile(os.path.join(hb, "bko-holt")):
            sums["linux"] = sha_file(os.path.join(hb, "bko-holt"))
        for p in sorted(os.listdir(hb)):
            f = os.path.join(hb, p, "bko-holt")
            if os.path.isfile(f):
                sums[p] = sha_file(f)
        out["holttest1d"] = {"path": hb, "sums": sums, "build_info": first_line(os.path.join(hb, "BUILD-INFO"))}
    for d in sorted(glob.glob(os.path.join(host_root, "holt-bin", "*"))):
        if not os.path.isdir(d) or d.endswith(".part") or ".part." in os.path.basename(d):
            continue
        present = ["linux"] if os.path.isfile(os.path.join(d, "bko-holt")) else []
        present += [p for p in sorted(os.listdir(d)) if os.path.isfile(os.path.join(d, p, "bko-holt"))]
        known = build_info_sums(d)
        out[os.path.basename(d)] = {"path": d, "sums": {p: known.get(p) for p in present},
                                    "build_info": first_line(os.path.join(d, "BUILD-INFO"))}
    return out


def imgsum_cache(host_root):
    try:
        with open(os.path.join(host_root, "cache", "imgsum.json"), encoding="utf-8") as f:
            c = json.load(f)
        return c.get("files", {}) if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def cached_sum(cache, path):
    """The cached sha256 of PATH while its size, mtime and inode still match, else None."""
    e = cache.get(path)
    try:
        st = os.stat(path)
    except OSError:
        return None
    if isinstance(e, dict) and e.get("size") == st.st_size and e.get("mtime_ns") == st.st_mtime_ns \
            and e.get("ctime_ns") == st.st_ctime_ns and e.get("inode") == st.st_ino \
            and isinstance(e.get("sha256"), str):
        return e["sha256"]
    return None


def combine(sums):
    """A guest's img: sum from its disks' {basename: sha256}."""
    return "img:" + hashlib.sha256("".join(f"{n}\t{h}\n" for n, h in sorted(sums.items())).encode()).hexdigest()


def guest_disks(gdir, opts_text):
    disks = sorted(glob.glob(os.path.join(gdir, "*.qcow2")))
    toks = (opts_text or "").split()
    for i, t in enumerate(toks):
        if t == "--disk" and i + 1 < len(toks):
            p = toks[i + 1]
            p = p if os.path.isabs(p) else os.path.join(gdir, p)
            if p not in disks:
                disks.append(p)
    return disks


def guests(work, host_root, only=None):
    """Each vms/GUEST holding a qcow2 (ONLY: just these names)."""
    out = {}
    cache = imgsum_cache(host_root)
    for gdir in sorted(glob.glob(os.path.join(work, "vms", "*"))):
        g = os.path.basename(gdir)
        if only is not None and g not in only:
            continue
        if not os.path.isdir(gdir) or not glob.glob(os.path.join(gdir, "*.qcow2")):
            continue
        st = None
        try:
            with open(os.path.join(gdir, "image-state"), encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = None
        files = {}
        for n in GUEST_FILES:
            h = sha_file(os.path.join(gdir, n))
            files[n] = h if h else "absent"
        opts = text(os.path.join(gdir, "vm-run-options"))
        disks, sums, ok = [], {}, True
        for p in guest_disks(gdir, opts):
            try:
                s = os.stat(p)
            except OSError:
                disks.append({"path": p, "size": None, "mtime": None})
                ok = False
                continue
            disks.append({"path": p, "size": s.st_size, "mtime": int(s.st_mtime)})
            h = cached_sum(cache, p)
            if h is None:
                ok = False
            else:
                sums[os.path.basename(p)] = h
        out[g] = {"generation": (st or {}).get("generation"), "updates": (st or {}).get("updates"),
                  "files": files, "disks": disks, "imgsum": combine(sums) if ok and sums else None,
                  "firmware": "uefi" if "--uefi" in (opts or "").split() else "bios"}
    return out


def rescuers(host_root):
    images, binf = {}, {}
    cache = imgsum_cache(host_root)
    for p in sorted(glob.glob(R("/bkoscratch/holttest1d/rescuer/*.qcow2"))):
        try:
            s = os.stat(p)
        except OSError:
            continue
        sv = None
        sl = first_line(p + ".sha256")
        if sl and sl.split() and HEX64.match(sl.split()[0]):
            sv = sl.split()[0]
        h = cached_sum(cache, p)
        images[os.path.basename(p)] = {"sha256": sv, "size": s.st_size, "mtime": int(s.st_mtime),
                                       "imgsum": ("img:" + h) if h else None}
    bdir = R("/bkoscratch/holttest1d/bin")
    for p in sorted(glob.glob(os.path.join(bdir, "*.deb")) + [os.path.join(bdir, "bcachefs")]):
        if os.path.isfile(p):
            binf[os.path.basename(p)] = sha_file(p)
    return {"images": images, "bin": binf}


def expand_home(p, home):
    return home + p[1:] if p == "~" or p.startswith("~/") else p


def collect(want=None, work=None, host_root=None, copy_root=None, user=None):
    """The facts as a dict."""
    t0 = time.monotonic()
    want = want or {}
    if work is None:
        work = os.environ.get("T2_WORK") or (R(os.path.expanduser("~")) + "/backotter-work" if ROOT else
                                             os.path.expanduser("~/backotter-work"))
    work = os.path.abspath(os.path.expanduser(work))
    home = os.path.dirname(work)
    host_root = expand_home(os.path.expandvars(host_root or os.environ.get("T2_HOST_ROOT") or R("/bkoscratch/test2")),
                            home)
    copy_root = expand_home(os.path.expandvars(copy_root or os.environ.get("T2_COPY_ROOT")
                                               or os.path.join(work, "t2", "copy")), home)
    user = user or os.path.basename(home)
    f = {"v": V, "host": socket.gethostname().split(".")[0], "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "work": work, "host_root": host_root, "copy_root": copy_root, "errors": []}

    def part(name, fn, *a):
        try:
            f[name] = fn(*a)
        except Exception as e:   # a part that fails is null, and named in errors
            f[name] = None
            f["errors"].append(f"{name}: {type(e).__name__}: {e}")

    part("versions", versions, home)
    part("preflight", preflight, user)
    part("sets", lambda: {n: one_set(work, n) for n in (want.get("sets") or ["bko-bin"])})
    part("rounds", rounds, work)
    part("holt_bins", holt_bins, host_root, want.get("holt_bin_dirs"), home)
    part("guests", guests, work, host_root, set(want["guests"]) if want.get("guests") is not None else None)
    part("rescuers", rescuers, host_root)
    part("plumbing", lambda: {n: sha_file(os.path.join(work, n[len("shared:"):]))
                              for n in (want.get("plumbing") or DEFAULT_PLUMBING) if n.startswith("shared:")})

    def hin():
        out = {}
        for p in want.get("host_inputs") or []:
            q = expand_home(p, home)
            if os.path.isdir(q):
                out[p] = "dir:" + dir_digest(q)
            elif os.path.isfile(q):
                out[p] = "sha256:" + sha_file(q)
            else:
                out[p] = "absent"
        return out
    part("host_inputs", hin)
    part("synced", lambda: {d: first_line(os.path.join(work, d, ".backotter-commit")) for d in ("testvm", "stick")})
    part("copies", lambda: sorted(n for n in (os.listdir(copy_root) if os.path.isdir(copy_root) else [])
                                  if os.path.isfile(os.path.join(copy_root, n, ".t2-complete"))))
    f["took_s"] = round(time.monotonic() - t0, 3)
    return f


def main(argv):
    import argparse
    a = argparse.ArgumentParser(prog="facts_host.py", description="Test2's host probe: one JSON object.")
    a.add_argument("--want", default=None, help='JSON: {"sets": [...], "plumbing": [...], "host_inputs": [...]}')
    a.add_argument("--work")
    a.add_argument("--host-root")
    a.add_argument("--copy-root")
    a.add_argument("--user")
    o = a.parse_args(argv)
    want = json.loads(o.want) if o.want else {}
    if not want and "_T2_DATA" in globals():
        want = json.loads(globals()["_T2_DATA"])
    f = collect(want, o.work, o.host_root, o.copy_root, o.user)
    sys.stdout.write(json.dumps(f, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
