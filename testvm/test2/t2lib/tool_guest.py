"""t2-host tool guest: GuestTest2's helpers on a test host (contract 2.1, K3).

    identity GUEST... [--sha256] [--backfill]   each guest's image-state, image-stat.json, disks and
                                                qemu-img info -U, as JSON; --sha256 hashes the disks;
                                                --backfill writes image-stat.json when they match
    sweep [--failed-keep-days N]                what a killed run left: dead run folders, orphan shipped
                                                overlays in RAM, old failed/ records
    gate --guest G --out DIR                    vm-update's --merge-gate: exit 0 merges, 10 doesn't
    failed-state --guest G --run R --attempt A --out DIR [--after-row]
                                                a failed attempt's state, inside its reservation
    pkgdiff G GENa GENb                         the package difference of two generations' dumps
    prune-dumps [GUEST...]                      guest-dumps: all but the newest two generations

Everything but gate and failed-state also runs with no copy of the repository on the host: this
file goes over ssh's stdin (guest.py's remote_tool), as facts_host.py does, because preflight and
identity come before the run's private copy.  So only the standard library at the top level.
Roots: T2_HOST_ROOT (/bkoscratch/test2), T2_WORK (~/backotter-work).
"""

import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time

KEEP_DAYS_DEFAULT = 14
SHIPPED_MIN_AGE_S = 3600   # vm-update ships an overlay before it writes .vm-update.pending, and holds no lock here
OVERLAY_CAP_DEFAULT = 1024


def _home():
    return os.path.expanduser("~")


def work_root(ctx=None):
    w = getattr(ctx, "work", None) or os.environ.get("T2_WORK")
    return os.path.expanduser(w) if w else os.path.join(_home(), "backotter-work")


def host_root(ctx=None):
    h = getattr(ctx, "host_root", None) or os.environ.get("T2_HOST_ROOT") or "/bkoscratch/test2"
    return os.path.expanduser(h)


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, doc):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def guest_disks(gdir):
    """The guest's image file names, as vm-update's Guest has them: its boot image first."""
    extra = []
    try:
        with open(os.path.join(gdir, "vm-run-options"), encoding="utf-8") as f:
            o = f.read().split()
        extra = [o[i + 1] for i, x in enumerate(o[:-1]) if x == "--disk"]
    except OSError:
        pass
    main = sorted(n for n in os.listdir(gdir) if n.endswith(".qcow2") and n[0] != "." and n not in extra)
    return main + extra


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def qemu_info(path):
    """{ok, format, why...}: read-only, safe beside a running VM (-U)."""
    try:
        r = subprocess.run(["qemu-img", "info", "-U", "--output=json", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "why": f"qemu-img: {e}"}
    if r.returncode != 0:
        return {"ok": False, "why": r.stderr.decode("utf-8", "replace").strip()[-200:]}
    try:
        d = json.loads(r.stdout.decode("utf-8", "replace"))
    except ValueError:
        return {"ok": False, "why": "qemu-img: bad JSON"}
    fs = d.get("format-specific", {}).get("data", {}) if isinstance(d.get("format-specific"), dict) else {}
    return {"ok": True, "format": d.get("format"), "backing": d.get("backing-filename") or d.get("full-backing-filename"),
            "dirty": bool(d.get("dirty-flag")), "corrupt": bool(fs.get("corrupt")),
            "snapshots": [s.get("name") for s in d.get("snapshots") or []]}


def identity_of(work, guest, sha=False, backfill=False):
    gdir = os.path.join(work, "vms", guest)
    if not os.path.isdir(gdir):
        return {"missing": True}
    out = {"image_state": _json(os.path.join(gdir, "image-state")), "stat": _json(os.path.join(gdir, "image-stat.json")),
           "disks": {}, "qemu": {}}
    names = guest_disks(gdir)
    for n in names:
        p = os.path.join(gdir, n)
        try:
            s = os.stat(p)
        except OSError:
            out["disks"][n] = None
            continue
        out["disks"][n] = {"size": s.st_size, "mtime_ns": s.st_mtime_ns, "inode": s.st_ino}
        out["qemu"][n] = qemu_info(p)
    if sha or backfill:
        out["sha256"] = {}
        for n in names:
            try:
                out["sha256"][n] = sha256_file(os.path.join(gdir, n))
            except OSError:
                out["sha256"][n] = None
    if backfill:
        st = out["image_state"] or {}
        want = st.get("sha256") or {}
        if st.get("generation") and want and all(out["sha256"].get(n) == want.get(n) for n in names) and all(out["disks"].get(n) for n in names):
            # a backfill never touches image-state: rows_stic's watcher reads that one's mtime
            doc = {"generation": st["generation"], "disks": {n: out["disks"][n] for n in names}}
            _write_json(os.path.join(gdir, "image-stat.json"), doc)
            out["stat"], out["backfilled"] = doc, True
    return out


# --- what a killed run left ----------------------------------------------------------------------

def realhash8(vms):
    return hashlib.sha256(os.path.realpath(vms).encode()).hexdigest()[:8]


def ram_dirs(work, ram="/mnt/bko-ram"):
    """[(guest, dir)] of this world's /mnt/bko-ram/vm-update.* : the default world's are
    vm-update.GUEST, any other world's vm-update.HASH8.GUEST, HASH8 from its vms/ realpath."""
    vms = os.path.join(work, "vms")
    real = os.path.realpath(vms) == os.path.realpath(os.path.join(_home(), "backotter-work", "vms"))
    out = []
    for d in sorted(glob.glob(os.path.join(ram, "vm-update.*"))):
        rest = os.path.basename(d)[len("vm-update."):]
        if real:
            if os.path.isdir(os.path.join(vms, rest)):
                out.append((rest, d))
        elif rest.startswith(realhash8(vms) + "."):
            g = rest[9:]
            if os.path.isdir(os.path.join(vms, g)):
                out.append((g, d))
    return out


def flock_free(path):
    """Whether a vm-update could take the guest's lock: True when nobody holds it.  It never
    creates the file.  Returns an open fd holding it (so the check and the removal are one
    step), None when another process holds it, or -1 when there's no lock file (nothing to hold)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return -1
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def qemu_holds(path):
    """Whether a QEMU program has PATH open (its OFD locks, as vm-update's).  Can't tell: as if it had."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return True
    try:
        probe = struct.pack("hhqqi4x", fcntl.F_WRLCK, os.SEEK_SET, 100, 200, 0)
        got = struct.unpack("hhqqi4x", fcntl.fcntl(fd, fcntl.F_OFD_GETLK, probe))
        return got[0] != fcntl.F_UNLCK
    except (OSError, AttributeError):
        return True
    finally:
        os.close(fd)


def pid_cmdline(pid):
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except (OSError, ValueError):
        return None


def pid_environ(pid, name):
    """NAME in the environment PID started with, or None (gone, or not ours to read)."""
    try:
        with open(f"/proc/{int(pid)}/environ", "rb") as f:
            for item in f.read().split(b"\0"):
                k, eq, v = item.partition(b"=")
                if eq and k == name.encode():
                    return v.decode("utf-8", "replace")
    except (OSError, ValueError):
        pass
    return None


def names_run_folder(pid, run_dir):
    """Whether PID is still that run's vm-run: the folder is in its command line (a guest with a
    monitor socket) or its RAM_DIR, the way vm-update hands it over."""
    if run_dir in (pid_cmdline(pid) or ""):
        return True
    ram = pid_environ(pid, "RAM_DIR")
    return bool(ram) and os.path.realpath(ram) == os.path.realpath(run_dir)


def stop_owner_vm(owner, run_dir, say, wait_s=10):
    """A vm-run still alive for a dead vm-update: SIGTERM, by its recorded PID, only when that
    PID is still that run's (names_run_folder).  True when it's gone (or there was none)."""
    pid = owner.get("vm_run_pid")
    if not pid:
        return True
    if pid_cmdline(pid) is None:
        return True
    if not names_run_folder(pid, run_dir):
        return True   # the PID is another process's now
    try:
        os.kill(int(pid), signal.SIGTERM)
        say(f"sent TERM to the vm-run of a dead run: pid {pid}, {run_dir}")
    except OSError:
        pass
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if pid_cmdline(pid) is None:
            return True
        time.sleep(0.5)
    return pid_cmdline(pid) is None


def _age(path, now):
    try:
        return now - os.stat(path).st_mtime
    except OSError:
        return 0.0


def sweep(work, hroot, keep_days=KEEP_DAYS_DEFAULT, ram="/mnt/bko-ram", now=None, say=print):
    """Preflight's sweep.  What's dead goes, and is said; an overlay with a pending merge stays
    for --finish, and is said.  Nothing of a guest whose vm-update lock is held is touched."""
    now = now if now is not None else time.time()
    vms = os.path.join(work, "vms")
    out = {"removed": [], "kept": [], "pending": []}
    for guest, d in ram_dirs(work, ram):
        lock = flock_free(os.path.join(vms, guest, ".vm-update.lock"))
        if lock is None:
            out["kept"].append(f"{d}: its guest's vm-update runs")
            continue
        try:
            for rd in sorted(glob.glob(os.path.join(d, ".vm-update-run.*"))):
                overlays = glob.glob(os.path.join(rd, "*.qcow2"))
                owner = _json(os.path.join(rd, "owner.json")) or {}
                up = owner.get("vm_update_pid")
                if up and "vm-update" in (pid_cmdline(up) or ""):
                    out["kept"].append(f"{rd}: its vm-update runs")
                    continue
                if owner and not stop_owner_vm(owner, rd, say):
                    out["kept"].append(f"{rd}: its vm-run won't stop")
                    continue
                if any(qemu_holds(o) for o in overlays):
                    out["kept"].append(f"{rd}: a QEMU holds it")
                    continue
                shutil.rmtree(rd, ignore_errors=True)
                out["removed"].append(rd)
                say(f"removed {rd}, a dead run's folder")
            pending = os.path.exists(os.path.join(vms, guest, ".vm-update.pending"))
            for ov in sorted(glob.glob(os.path.join(d, ".vm-update.*"))):
                if os.path.basename(ov).startswith(".vm-update-run."):
                    continue
                if pending:
                    out["pending"].append(ov)
                    say(f"kept {ov}: a merge is pending, for vm-update --finish")
                else:
                    if qemu_holds(ov):
                        out["kept"].append(f"{ov}: a QEMU holds it")
                        continue
                    if _age(ov, now) < SHIPPED_MIN_AGE_S:   # a sender may be between its copy and writing .vm-update.pending
                        out["kept"].append(f"{ov}: too new to call an orphan")
                        continue
                    if os.path.isdir(ov):
                        shutil.rmtree(ov, ignore_errors=True)
                    else:
                        try:
                            os.remove(ov)
                        except OSError:
                            continue
                    out["removed"].append(ov)
                    say(f"removed {ov}, a shipped overlay with no pending merge")
            try:
                os.rmdir(d)
            except OSError:
                pass
        finally:
            if lock is not None and lock >= 0:
                os.close(lock)
    # a console probe's RAM folder, its owner dead (a TERM cleans up after itself; a KILL can't)
    real_work = os.path.realpath(work)
    for d in sorted(glob.glob(os.path.join(ram, "t2-guest-probe.*"))):
        owner = _json(os.path.join(d, "owner.json"))
        if not owner or os.path.realpath(str(owner.get("work"))) != real_work:
            continue   # another world's, or not ours to judge
        if "probe-" in (pid_cmdline(owner.get("pid")) or ""):
            out["kept"].append(f"{d}: its probe runs")
            continue
        if any(qemu_holds(os.path.join(root, f)) for root, _d, fs in os.walk(d) for f in fs if f.endswith(".qcow2")):
            out["kept"].append(f"{d}: a QEMU holds it")
            continue
        shutil.rmtree(d, ignore_errors=True)
        out["removed"].append(d)
        say(f"removed {d}, a dead console probe's folder")
    # failed/RUN/GUEST older than the policy's days
    limit = now - keep_days * 86400
    for g in sorted(glob.glob(os.path.join(hroot, "failed", "*", "*"))):
        try:
            if os.stat(g).st_mtime < limit:
                shutil.rmtree(g, ignore_errors=True)
                out["removed"].append(g)
                say(f"removed {g}, older than {keep_days} days")
                run_d = os.path.dirname(g)
                try:
                    os.rmdir(run_d)
                except OSError:
                    pass
        except OSError:
            continue
    return out


# --- a failed attempt's state --------------------------------------------------------------------

def tree_mib(path):
    n = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                n += os.lstat(os.path.join(root, f)).st_blocks * 512
            except OSError:
                pass
    return n / (1 << 20)


LOG_NAMES = ("update.log", "serial.log", "result.json", "steps.txt", "pkgset-before.txt", "pkgset-after.txt", "new-lines.json",
             "row-summary.json")


def failed_state(guest, run, attempt, out, hroot, flakes=(), cap_mib=OVERLAY_CAP_DEFAULT, after_row=False, say=print):
    """Before the row: the failed attempt's logs go to HOST_ROOT/failed/RUN/G/, and the overlay
    too when under the cap and no known flake's tail matches, else a note; the RAM folder
    vm-update --keep-failed left is removed either way.  After the row: a known-flake PASS
    deletes failed/RUN/G/'s overlay."""
    dest = os.path.join(hroot, "failed", run, guest)
    if after_row:
        summ = _json(os.path.join(out, "row-summary.json")) or {}
        if summ.get("class") == "known-flake":
            for o in glob.glob(os.path.join(dest, "overlay*")):
                if os.path.isdir(o):
                    shutil.rmtree(o, ignore_errors=True)
                else:
                    try:
                        os.remove(o)
                    except OSError:
                        pass
            say(f"failed-state: {guest}: known-flake PASS, overlay deleted")
        return {"action": "after-row"}
    res = _json(os.path.join(out, "result.json")) or {}
    overlay_dir = res.get("overlay_dir")
    tail = res.get("tail") or []
    flake = _match_flake(guest, tail, flakes)
    os.makedirs(dest, exist_ok=True)
    action = {"action": "logs", "flake": bool(flake), "overlay": None, "note": None}
    ram_dir = overlay_dir if overlay_dir and os.path.basename(overlay_dir.rstrip("/")).startswith(".vm-update-run.") and os.path.isdir(overlay_dir) else None
    try:
        for n in LOG_NAMES:
            p = os.path.join(out, n)
            if os.path.isfile(p):
                shutil.copy2(p, os.path.join(dest, f"a{attempt}-{n}"))
        for p in glob.glob(os.path.join(out, "hook-*.out")):
            shutil.copy2(p, os.path.join(dest, f"a{attempt}-{os.path.basename(p)}"))
        if ram_dir:
            mib = tree_mib(ram_dir)
            if flake:
                action["note"] = "a known flake's tail: logs only"
            elif mib < cap_mib:
                target = os.path.join(dest, f"overlay-a{attempt}")
                shutil.copytree(ram_dir, target, symlinks=True)
                action["overlay"] = target
            else:
                action["note"] = f"overlay {mib:.0f} MiB, over the {cap_mib} MiB cap: not kept"
    finally:   # the reservation goes with this job: whatever happened above, the RAM is given back
        if ram_dir:
            shutil.rmtree(ram_dir, ignore_errors=True)
    if action["note"]:
        with open(os.path.join(dest, "note.txt"), "a", encoding="utf-8") as f:
            f.write(f"attempt {attempt}: {action['note']}\n")
    say(f"failed-state: {guest}: {action}")
    return action


def _match_flake(guest, tail, flakes):
    text = "\n".join(tail) if isinstance(tail, (list, tuple)) else (tail or "")
    for f in flakes or ():
        if f.get("guest") not in (guest, "*"):
            continue
        try:
            if re.search(f.get("pattern") or r"(?!)", text, re.M):
                return f
        except re.error:
            continue
    return None


# --- the merge gate --------------------------------------------------------------------------------

def gate(guest, out, base, say=print):
    """Exit 0 merges; exit 10 doesn't (a new after:* line, or a hook that failed)."""
    from . import guest_judge as J
    from . import rows_guest as R
    try:
        sf = R.load_spec(base)
        entry = sf.entry(guest)
    except (OSError, ValueError, KeyError) as e:
        say(f"gate: no spec entry for {guest}: {e}")
        return 10
    checks = entry.get("checks") or {}
    tb = R._read(os.path.join(out, "hook-before.out"))
    ta = R._read(os.path.join(out, "hook-after.out"))
    ok1, why1 = J.check_hook(tb, J.required_image(checks) + J.required_after(checks))
    ok2, why2 = J.check_hook(ta, J.required_after(checks))
    hooks_ok = ok1 and ok2
    why = None if hooks_ok else "; ".join(x for x in (why1 and f"before: {why1}", why2 and f"after: {why2}") if x)
    res = R._json(os.path.join(out, "result.json")) or {}
    bad = sorted(n for n, h in (res.get("hooks") or {}).items() if (h or {}).get("rc") not in (0, None))
    if bad:   # vm-update wrote the hooks' exits just before it called this
        hooks_ok, why = False, "; ".join(x for x in (why, "exit status of " + ", ".join(bad)) if x)
    ok, text, _new = J.merge_gate(J.parse_sections(tb), J.parse_sections(ta), checks, R.classed_of(base, guest), hooks_ok, why)
    if ok:
        say("gate: merge")
        return 0
    say(f"gate: no merge: {text}")
    return 10


# --- two generations' packages --------------------------------------------------------------------

def pkgdiff(hroot, guest, gen_a, gen_b):
    """{added, removed, changed} between guest-dumps/G/GENa.pkgset and GENb.pkgset (the lines, by
    their first two fields); None when one isn't there.  Raw dumps never leave the host; the
    package names do."""
    def load(gen):
        p = os.path.join(hroot, "guest-dumps", guest, f"{gen}.pkgset")
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                out = {}
                for line in f:
                    fs = line.split()
                    if fs:
                        k = " ".join(fs[:2]) if fs[0] in ("pkg", "base", "nix-pkg", "syspatch") and len(fs) > 1 else fs[0]
                        out[k] = line.strip()
                return out
        except OSError:
            return None
    a, b = load(gen_a), load(gen_b)
    if a is None or b is None:
        return None
    return {"added": sorted(k for k in b if k not in a), "removed": sorted(k for k in a if k not in b),
            "changed": sorted(k for k in a if k in b and a[k] != b[k])}


def prune_dumps(hroot, guests=None, keep=2, say=print):
    """Keep the newest KEEP generations of guest-dumps/G/ (by mtime)."""
    base = os.path.join(hroot, "guest-dumps")
    names = guests or (sorted(os.listdir(base)) if os.path.isdir(base) else [])
    removed = []
    for g in names:
        files = glob.glob(os.path.join(base, g, "*.json"))
        files.sort(key=lambda p: os.stat(p).st_mtime, reverse=True)
        for p in files[keep:]:
            for q in (p, p[:-5] + ".pkgset"):
                try:
                    os.remove(q)
                    removed.append(q)
                except OSError:
                    pass
    say(f"prune-dumps: {len(removed)} file(s) removed")
    return removed


# --- the command line -------------------------------------------------------------------------------

def _flag(args, name):
    if name in args:
        args.remove(name)
        return True
    return False


def _opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def main(args, ctx=None):
    args = list(args)
    if not args:
        sys.stderr.write(__doc__)
        return 2
    sub, rest = args[0], args[1:]
    work, hroot = work_root(ctx), host_root(ctx)
    if sub == "identity":
        sha, backfill = _flag(rest, "--sha256"), _flag(rest, "--backfill")
        print(json.dumps({g: identity_of(work, g, sha, backfill) for g in rest}, sort_keys=True))
        return 0
    if sub == "sweep":
        days = float(_opt(rest, "--failed-keep-days", KEEP_DAYS_DEFAULT))
        res = sweep(work, hroot, days)
        print("sweep: " + json.dumps({k: len(v) for k, v in res.items()}, sort_keys=True))
        return 0
    if sub == "gate":
        return gate(_opt(rest, "--guest"), _opt(rest, "--out"), getattr(ctx, "base", None) or _base())
    if sub == "failed-state":
        after = _flag(rest, "--after-row")
        guest, run, attempt, out = _opt(rest, "--guest"), _opt(rest, "--run"), int(_opt(rest, "--attempt", 1)), _opt(rest, "--out")
        base = getattr(ctx, "base", None) or _base()
        from . import rows_guest as R
        pol = (R.policy_of(base).get("run") or {})
        failed_state(guest, run, attempt, out, hroot, R.flakes_of(base), int(pol.get("failed_overlay_mib", OVERLAY_CAP_DEFAULT)), after)
        return 0
    if sub == "pkgdiff":
        if len(rest) != 3:
            sys.stderr.write("usage: pkgdiff GUEST GENa GENb\n")
            return 2
        print(json.dumps(pkgdiff(hroot, *rest), sort_keys=True))
        return 0
    if sub == "prune-dumps":
        prune_dumps(hroot, rest or None)
        return 0
    sys.stderr.write(f"t2-host tool guest: unknown {sub}\n")
    return 2


def _base():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
