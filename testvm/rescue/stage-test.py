#!/usr/bin/env python3
"""Proves stage-backup.sh, the backup tool's staging root (holt-json.md's
open question 14), with a real rustic-bko, restic-bko and bko-holt.

    stage-test.py BKO_HOLT RUSTIC_BKO RESTIC_BKO [OUT_FILE]

Root, on a Linux test host, with TMPDIR the reserved RAM directory
(cmp-run.sh --reserve): it works in a new directory there, in a private
mount namespace of its own (every mount it makes goes with it), and
removes the directory on a pass.  OUT_FILE gets what it found (stdout as
well; stage-recorded.txt beside this is the run that proved it).
Python's stdlib only.

A small system in RAM: a root file system (a directory on the RAM disk)
whose top holds directories, a file hard-linked into usr/bin, two symbolic
links (one dangling, one with a trusted.* extended attribute), a FIFO, a
sparse file and a hidden file, with set modes, owners, times and a user
extended attribute; three file systems
mounted on it: /home and /boot/efi (backed up) and /scratch (not); and a
read-only view of the root (a read-only bind).  The dump's header
directory holds two test headers (random bytes of uc24-fde's names, never
a real header); system.json's layout is uc24-fde's dump with their sizes
and SHA-256.  Then, for each archiver:

  - rw: stage-backup.sh --bind ROOT / --bind HOME /home --bind EFI
    /boot/efi --headers H --system-json J, the root writable: nothing of the
    source changes (every path's type, mode, owner, size, mtime, ctime,
    inode and contents, the root's own included; no /.bko-holt made), and
    no mount is left in the caller's namespace;
  - ro: the same from the read-only root;
  - the snapshot, restored whole (rustic-bko; restic-bko's own restore of
    its own too), is the system's tree (every path but / with its type,
    mode, owner, size, mtime, contents, link target, user xattrs, hard-link
    groups; /scratch empty, the hidden /home under the mount absent) plus
    /.bko-holt, /.bko-holt/luks-headers, its two headers and
    /.bko-holt/system.json, byte for byte;
  - restored by rustic-bko with load's four --glob arguments: the system's
    tree alone;
  - bko-holt snapshot-layout reads the snapshot's own layout back, and
    bko-holt luks-headers its headers, both exit 0, byte for byte, each
    with rustic-bko as bko-rescue runs them (restic-bko's repository too:
    holt's restic-bko argv can't run, holt-json.md's open question 15
    notes), and restic-bko's own dump of each, by hand;
  - a second rw backup, made next, finds the first as its parent (restic's paths are
    the fixed staging path).  Its archiver is a wrapper that leaves a
    process behind (setsid sleep), which must be gone when stage-backup.sh
    exits, and it runs with a stray BKO_STAGE_UNSHARED, which mustn't stop
    it unsharing;
  - --as-path /sub, with --exclude /swapfile --exclude /srv: restored
    whole, the tree holds only sub, and sub is the system's tree without
    the two; snapshot-layout and luks-headers of ID:/sub; a second one
    finds the first as its parent (restic-bko's paths are S/sub's);
  - the default stage, /run/bko-holt/stage, on a fresh tmpfs at /run (in
    the test's namespace) with no /run/bko-holt, a relative ARCHIVER, and
    the two excludes: /run/bko-holt is made 0700 and the stage left empty;
    restored whole and as load restores it, the tree without the two;
  - refusals: -x, -vx, --one-file-system=true, an absolute --glob or
    --exclude after --, no backup command, a relative --password-file for
    restic-bko (exit 2 each); --help (exit 2, the usage alone); a stage not
    in RAM, or whose missing parents' nearest is on disk (exit 1, nothing
    made); a bind under a read-only directory that doesn't hold its mount
    point (exit 1); the namespace marker set to the script's own PID
    without a namespace (exit 1, no mount made);
  - SIGTERM to stage-backup.sh while the archiver runs: the archiver gets
    it, its exit status (143) comes back, nothing is left.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(HERE, "stage-backup.sh")
CORPUS = os.path.join(HERE, "..", "..", "holt", "tests", "corpus", "uc24-fde", "src.json")
INSTALL_ID = "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90"
LOAD_GLOBS = ["!/.bko-holt/luks-headers/**", "!/.bko-holt/luks-headers", "!/.bko-holt/system.json", "!/.bko-holt"]
PASSWORD = b"correct horse"
T0 = 1700000000123456789
EXCLUDES = ["--exclude", "/swapfile", "--exclude", "/srv"]
LINGER = b"sleep\x00600.4242\x00"

said = []


def say(line):
    said.append(line)
    print(line, flush=True)


def run(*cmd, ok=(0,), **kw):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, **kw)
    if p.returncode not in ok:
        sys.exit("stage-test: %s: exit %d: %s%s" % (" ".join(map(str, cmd)), p.returncode,
                                                    p.stdout.decode(errors="replace")[-400:],
                                                    p.stderr.decode(errors="replace")[-1200:]))
    return p


def private_file(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def write(path, data, mode=0o644, uid=0, gid=0):
    with open(path, "wb") as f:
        f.write(data)
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def entry(path, deep):
    """One path's record: what a restore keeps, and with DEEP what a write
    to it would change too (ctime, inode, device)."""
    st = os.lstat(path)
    t = stat.S_IFMT(st.st_mode)
    r = {"type": t, "mode": stat.S_IMODE(st.st_mode), "uid": st.st_uid, "gid": st.st_gid,
         "mtime": st.st_mtime_ns}
    try:
        names = sorted(os.listxattr(path, follow_symlinks=False))
    except OSError:
        names = []
    r["xattrs"] = {n: os.getxattr(path, n, follow_symlinks=False).hex() for n in names
                   if n.startswith("user.") or n.startswith("trusted.")}
    if t == stat.S_IFREG:
        r["size"] = st.st_size
        with open(path, "rb") as f:
            r["sha256"] = hashlib.sha256(f.read()).hexdigest()
        r["nlink"] = st.st_nlink
    elif t == stat.S_IFLNK:
        r["target"] = os.readlink(path)
    if deep:
        r.update(ctime=st.st_ctime_ns, ino=st.st_ino, dev=st.st_dev, size=st.st_size)
    return r, (st.st_dev, st.st_ino) if t == stat.S_IFREG and st.st_nlink > 1 else None


def manifest(top, deep=False, prefix="", root=False):
    """Every path under TOP (crossing mounts), keyed by its path from TOP's
    (PREFIX/...), with the hard-link groups; TOP itself with ROOT."""
    out, links = {}, {}

    def add(p, rel):
        r, key = entry(p, deep)
        out[rel] = r
        if key:
            links.setdefault(key, set()).add(rel)
    if root:
        add(top, prefix or ".")
    for d, dirs, files in os.walk(top):
        dirs.sort()
        for n in sorted(dirs + files):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, top)
            add(p, os.path.join(prefix, rel) if prefix else rel)
    return out, links


def groups(links):
    return sorted(sorted(g) for g in links.values() if len(g) > 1)


def diff(a, b, what):
    if a == b:
        return
    lines = ["stage-test: %s differ:" % what]
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            lines.append("  %s: %s != %s" % (k, a.get(k), b.get(k)))
    sys.exit("\n".join(lines[:40]))


def mountinfo():
    with open("/proc/self/mountinfo") as f:
        return sorted(" ".join(l.split()[3:5]) for l in f)


def mount_tmpfs(where, mode="0755"):
    run("mount", "-t", "tmpfs", "-o", "size=32m,mode=" + mode, "stage-test", where)


def build(work):
    """The system: SRC (its root file system, with /home, /boot/efi and
    /scratch mounted on it) and RO, a read-only view of its root."""
    src = os.path.join(work, "src")
    os.mkdir(src)
    for d, m in (("etc", 0o755), ("usr", 0o755), ("usr/bin", 0o755), ("usr/lib", 0o755), ("var", 0o755),
                 ("var/lib", 0o755), ("tmp", 0o1777), ("home", 0o711), ("boot", 0o700), ("boot/efi", 0o700),
                 ("scratch", 0o755), ("srv", 0o750)):
        os.mkdir(os.path.join(src, d))
        os.chmod(os.path.join(src, d), m)
    os.chown(os.path.join(src, "srv"), 0, 33)
    write(os.path.join(src, "srv/data"), b"served\n", 0o640, 0, 33)
    write(os.path.join(src, "etc/hostname"), b"lovelace\n")
    write(os.path.join(src, "etc/shadow"), b"root:*:20000::::::\n", 0o640, 0, 42)
    write(os.path.join(src, "usr/bin/tool"), b"#!/bin/sh\necho tool\n", 0o4755)
    os.link(os.path.join(src, "usr/bin/tool"), os.path.join(src, "hardtop"))
    os.symlink("usr/bin", os.path.join(src, "bin"))
    # A symbolic link's own xattr (user.* can't be on one): the staging's
    # copy must keep it, or fail.
    os.setxattr(os.path.join(src, "bin"), "trusted.bko", b"link", follow_symlinks=False)
    os.symlink("/nonexistent/place", os.path.join(src, "dangle"))
    os.lchown(os.path.join(src, "dangle"), 1000, 1000)
    os.mkfifo(os.path.join(src, "fifo"), 0o600)
    with open(os.path.join(src, "swapfile"), "wb") as f:
        f.truncate(1 << 20)
        f.seek((1 << 20) - 5)
        f.write(b"tail\n")
    os.chmod(os.path.join(src, "swapfile"), 0o600)
    write(os.path.join(src, ".autorelabel"), b"")
    write(os.path.join(src, "var/lib/x"), b"x" * 5000, 0o644, 7, 8)
    os.setxattr(os.path.join(src, "var/lib/x"), "user.bko", b"one")
    os.setxattr(os.path.join(src, "var"), "user.bko", b"top")
    write(os.path.join(src, "home/under-mount"), b"hidden by /home's file system\n")
    # The other file systems.
    mount_tmpfs(os.path.join(src, "home"))
    os.mkdir(os.path.join(src, "home/u"), 0o700)
    os.chown(os.path.join(src, "home/u"), 1000, 1000)
    write(os.path.join(src, "home/u/notes"), b"notes\n", 0o600, 1000, 1000)
    mount_tmpfs(os.path.join(src, "boot/efi"), "0700")
    os.makedirs(os.path.join(src, "boot/efi/EFI/BOOT"))
    write(os.path.join(src, "boot/efi/EFI/BOOT/BOOTX64.EFI"), os.urandom(3000), 0o700)
    mount_tmpfs(os.path.join(src, "scratch"))
    write(os.path.join(src, "scratch/not-backed-up"), b"never in the snapshot\n")
    # Set times, every path, the mounts' roots and the root's own too.
    for d, dirs, files in os.walk(src, topdown=False):
        for i, n in enumerate(sorted(dirs + files)):
            os.utime(os.path.join(d, n), ns=(T0, T0 + 1000 * i), follow_symlinks=False)
    os.utime(src, ns=(T0, T0 + 7))
    ro = os.path.join(work, "ro")
    os.mkdir(ro)
    run("mount", "--bind", src, ro)
    run("mount", "-o", "remount,bind,ro", ro)
    try:
        open(os.path.join(ro, "etc/written"), "w")
        sys.exit("stage-test: the read-only root took a write")
    except OSError as e:
        assert e.errno == 30, e  # EROFS
    return src, ro


def expected(src, ro):
    """The snapshot's tree as the staging must give it: the root file
    system's own (RO, a non-recursive view), but for /home and /boot/efi,
    which are their file systems'; /scratch is the root's empty directory."""
    base, links = manifest(ro)
    for m in ("home", "boot/efi"):
        for k in [k for k in base if k == m or k.startswith(m + "/")]:
            del base[k]
        for k in [k for k in links]:
            links[k] = {p for p in links[k] if not (p == m or p.startswith(m + "/"))}
        sub, sl = manifest(os.path.join(src, m), prefix=m, root=True)
        base.update(sub)
        links.update({("m", m) + k: v for k, v in sl.items()})
    assert "scratch" in base and not any(k.startswith("scratch/") for k in base), "scratch"
    assert "home/under-mount" not in base and "home/u/notes" in base
    return base, groups(links)


def without(tree, names):
    return {k: v for k, v in tree.items() if not any(k == n or k.startswith(n + "/") for n in names)}


def lingering():
    found = []
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    if f.read() == LINGER:
                        found.append(pid)
            except OSError:
                pass
    return found


def snapshots(argv):
    listed = json.loads(run(*argv, "snapshots", "--json").stdout)
    flat = []

    def walk(v):
        if isinstance(v, dict) and "id" in v and "time" in v:
            flat.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(listed)
    flat.sort(key=lambda s: s["time"])
    return flat


def main(argv):
    if len(argv) not in (4, 5):
        sys.exit("usage: stage-test.py BKO_HOLT RUSTIC_BKO RESTIC_BKO [OUT_FILE]")
    if os.getuid() != 0:
        sys.exit("stage-test: needs root (it mounts)")
    ns = os.readlink("/proc/self/ns/mnt")
    if os.environ.get("BKO_STAGE_TEST_NS") is None:
        os.environ["BKO_STAGE_TEST_NS"] = ns
        os.execvp("unshare", ["unshare", "-m", "--", sys.executable, "-I", os.path.abspath(__file__)] + argv[1:])
    assert os.environ.pop("BKO_STAGE_TEST_NS") != ns, "not in a mount namespace of its own"
    run("mount", "--make-rprivate", "/")
    holt, rustic, restic = (os.path.abspath(a) for a in argv[1:4])
    out_file = os.path.abspath(argv[4]) if len(argv) == 5 else None
    work = tempfile.mkdtemp(prefix="bko-stage-test-")
    env = dict(os.environ, LC_ALL="C", XDG_CACHE_HOME=os.path.join(work, "cache"), RUSTIC_NO_CACHE="true",
               RESTIC_CACHE_DIR=os.path.join(work, "cache"), HOME=work)
    say("stage-test.py on %s, kernel %s" % (os.uname().nodename, os.uname().release))
    for name, b, v in (("bko-holt", holt, "--version"), ("rustic-bko", rustic, "--version"), ("restic-bko", restic, "version")):
        say("  %s: %s (%s)" % (name, run(b, v, env=env).stdout.decode().splitlines()[0].strip(), b))
    say("  util-linux: " + run("unshare", "--version").stdout.decode().strip())

    src, ro = build(work)
    want, want_links = expected(src, ro)
    say("The system: %d paths in the snapshot's tree (from a root with %d top entries, /home and /boot/efi mounted on it, /scratch mounted and not backed up)"
        % (len(want), len(os.listdir(src))))
    base = json.load(open(CORPUS))
    hdr = os.path.join(work, "hdr")
    os.mkdir(hdr, 0o700)
    os.chmod(hdr, 0o700)
    heads = {}
    for i, h in enumerate(base["storage"]["luks_headers"]):
        heads[h["file"]] = os.urandom((1 << 20) + 4096 * (i + 1))
        private_file(os.path.join(hdr, h["file"]), heads[h["file"]])
        h["bytes"] = len(heads[h["file"]])
        h["sha256"] = hashlib.sha256(heads[h["file"]]).hexdigest()
    layout = base
    sysjson = os.path.join(work, "system.json")
    private_file(sysjson, json.dumps({
        "format": "backotter-system", "schema": 1, "sequence": 42, "written": "2026-11-26T02:00:03Z",
        "written_by": "stage-test.py", "machine": {"install_id": INSTALL_ID, "hostname": "lovelace"},
        "os": {"id": "ubuntu-core", "version_id": "24"},
        "backup": {"engine": "rustic", "repository": {"kind": "local", "url": "/srv/bko"}},
        "layout": layout}, indent=2).encode())
    pw = os.path.join(work, "password")
    private_file(pw, PASSWORD)
    stage = os.path.join(work, "stage")
    binds = ["--bind", os.path.join(src, "home"), "/home", "--bind", os.path.join(src, "boot/efi"), "/boot/efi",
             "--headers", hdr, "--system-json", sysjson]
    bko_want = {".bko-holt": 0o700, ".bko-holt/luks-headers": 0o700, ".bko-holt/system.json": 0o600}
    bko_want.update({".bko-holt/luks-headers/" + f: 0o600 for f in heads})

    want_ex = without(want, ("swapfile", "srv"))
    assert len(want_ex) == len(want) - 3, len(want_ex)
    run_dir = "/run"
    for tool, arch in (("rustic", rustic), ("restic", restic)):
        repo = os.path.join(work, "repo-" + tool)
        if tool == "rustic":
            rp = [arch, "--repository", repo, "--password-file", pw, "--no-progress", "--no-cache"]
            tail = ["backup", "--host", "lovelace"]
        else:
            rp = [arch, "--repo", repo, "--password-file", pw, "--no-cache"]
            tail = ["backup", "--host", "lovelace", "-q"]
        run(*rp, "init", env=env)
        # An archiver that leaves a process behind it.
        wrap = os.path.join(work, "linger-" + tool)
        mark = os.path.join(work, "linger-" + tool + ".started")
        with open(wrap, "w") as f:
            f.write('#!/bin/sh\ncase "${1:-}" in --version|version) exec %s "$@" ;; esac\n'
                    'echo $$ >%s\nsetsid sleep 600.4242 </dev/null >/dev/null 2>&1 &\nexec %s "$@"\n' % (arch, mark, arch))
        os.chmod(wrap, 0o755)
        ids = {}
        variants = (("rw", src, {}), ("rw2", src, {"linger": True}), ("ro", ro, {}),
                    ("sub", src, {"sub": True}), ("sub2", src, {"sub": True}), ("def", src, {"default": True}))
        for variant, root, o in variants:
            vstage = stage
            if o.get("default"):
                # /run as a normal boot has it, without /run/bko-holt (in
                # our namespace only).
                mount_tmpfs(run_dir)
                vstage = "/run/bko-holt/stage"
            before, mi = manifest(src, deep=True, root=True), mountinfo()
            extra = (["--as-path", "/sub"] + EXCLUDES) if o.get("sub") else []
            stage_args = ["--stage", vstage] if not o.get("default") else []
            if o.get("default"):
                extra = EXCLUDES
            a0, cwd, venv = arch, None, env
            if o.get("linger"):
                a0, venv = wrap, dict(env, BKO_STAGE_UNSHARED="1")
            if o.get("default"):
                a0, cwd = "./" + os.path.basename(arch), os.path.dirname(arch)
            p = run(STAGE, "--bind", root, "/", *binds, *stage_args, *extra, "--", a0, *rp[1:], *tail,
                    "--tag", variant, env=venv, cwd=cwd)
            after = manifest(src, deep=True, root=True)
            diff(before[0], after[0], "%s %s: the source before and after" % (tool, variant))
            assert not os.path.lexists(os.path.join(src, ".bko-holt")), ".bko-holt made in the source"
            assert mountinfo() == mi, "a mount was left in the caller's namespace"
            assert os.listdir(vstage) == [], os.listdir(vstage)
            snap = snapshots(rp)[-1]
            ids[variant] = snap
            if variant == "rw":
                notes = [l for l in p.stderr.decode().splitlines() if l.startswith("stage-backup:")]
                say("%s rw: stage-backup.sh said: %s" % (tool, " | ".join(notes)))
                assert "(extended attributes checked)" in p.stderr.decode(), p.stderr
            say("%s %s: snapshot %s, paths %s, parent %s; the source unchanged (%d paths: type, mode, owner, size, mtime, ctime, inode, contents), no /.bko-holt in it, no mount left"
                % (tool, variant, snap["id"][:8], snap.get("paths"), (snap.get("parent") or "none")[:8], len(before[0])))
            if o.get("linger"):
                assert os.path.exists(mark), "the wrapper didn't run"
                assert lingering() == [], "a process the archiver left is still there: %s" % lingering()
                say("%s %s: the archiver's wrapper left a process (setsid sleep): gone when stage-backup.sh exited; a stray BKO_STAGE_UNSHARED=1 didn't stop the namespace"
                    % (tool, variant))
            if o.get("default"):
                st = os.stat("/run/bko-holt")
                assert stat.S_IMODE(st.st_mode) == 0o700, oct(st.st_mode)
                say("%s def: the default stage: /run/bko-holt made 0700 (none before), /run/bko-holt/stage left empty; ARCHIVER given as %s" % (tool, a0))
                run("umount", run_dir)
        want_paths = {"rustic": {"rw": ["/"], "sub": ["/sub"], "def": ["/"]},
                      "restic": {"rw": [stage + "/root"], "sub": [stage + "/root/sub"],
                                 "def": ["/run/bko-holt/stage/root"]}}[tool]
        for v, wp in want_paths.items():
            assert ids[v].get("paths") == wp, (v, ids[v].get("paths"), wp)
        for later, first in (("rw2", "rw"), ("sub2", "sub")):
            assert (ids[later].get("parent") or "")[:8] == ids[first]["id"][:8], (ids[later], ids[first])
            say("%s %s: its parent is %s's snapshot %s" % (tool, later, first, ids[first]["id"][:8]))

        for variant in ("rw", "ro", "def", "sub"):
            sid = ids[variant]["id"][:8]
            vwant = want if variant in ("rw", "ro") else want_ex
            # Whole: the system's tree and the backup tool's files.
            whole = os.path.join(work, "t-%s-%s-whole" % (tool, variant))
            if tool == "rustic":
                run(*rp, "restore", "--numeric-id", sid, whole, env=env)
            else:
                run(*rp, "restore", sid, "--target", whole, env=env)
            top = whole
            if variant == "sub":
                assert os.listdir(whole) == ["sub"], os.listdir(whole)
                top = os.path.join(whole, "sub")
            got, gl = manifest(top)
            bko = {k: v for k, v in got.items() if k == ".bko-holt" or k.startswith(".bko-holt/")}
            rest = {k: v for k, v in got.items() if k not in bko}
            diff(vwant, rest, "%s %s whole restore: the tree" % (tool, variant))
            assert groups(gl) == want_links, (groups(gl), want_links)
            assert {k: v["mode"] for k, v in bko.items()} == bko_want, bko
            for f, b in heads.items():
                assert open(os.path.join(top, ".bko-holt/luks-headers", f), "rb").read() == b, f
            assert open(os.path.join(top, ".bko-holt/system.json"), "rb").read() == open(sysjson, "rb").read()
            say("%s %s: restored whole by %s-bko, %sthe system's %d paths (type, mode, owner, size, mtime, contents, link targets, user and trusted xattrs, hard links %s)%s and /.bko-holt's %d (two headers and system.json, byte for byte)"
                % (tool, variant, tool, "only sub at the top, under it " if variant == "sub" else "", len(rest), want_links,
                   ", /swapfile and /srv left out" if vwant is want_ex else "", len(bko)))
            shutil.rmtree(whole)
            if variant == "sub":
                continue
            # As load restores it: rustic-bko with load's four --glob.
            loadt = os.path.join(work, "t-%s-%s-load" % (tool, variant))
            lp = [rustic, "--repository", repo, "--password-file", pw, "--no-progress", "--no-cache"]
            globs = [x for g in LOAD_GLOBS for x in ("--glob", g)]
            run(*lp, "restore", "--numeric-id", *globs, sid, loadt, env=dict(env, RUSTIC_STRICT_EXIT="true"))
            got, gl = manifest(loadt)
            diff(vwant, got, "%s %s load restore" % (tool, variant))
            assert groups(gl) == want_links
            say("%s %s: restored by rustic-bko with load's four --glob: the system's %d paths alone, no /.bko-holt"
                % (tool, variant, len(got)))
            shutil.rmtree(loadt)

        for variant, snap in (("rw", ids["rw"]["id"][:8]), ("ro", ids["ro"]["id"][:8]), ("sub", ids["sub"]["id"][:8] + ":/sub")):
            k = os.path.join(work, "k-%s-%s" % (tool, variant))
            os.mkdir(k, 0o700)
            os.chmod(k, 0o700)
            o = os.path.join(k, "layout.json")
            p = run(holt, "snapshot-layout", o, "--repo", repo, "--password-file", pw, "--snapshot", snap,
                    "--install-id", INSTALL_ID, "--archiver", rustic, "--json", env=env)
            doc = json.loads(p.stdout)
            assert doc["format"] == "bko-holt-snapshot-layout" and doc["luks_headers"] == 2 and doc["sequence"] == 42, doc
            assert json.loads(open(o, "rb").read()) == layout
            h = os.path.join(k, "h")
            p = run(holt, "luks-headers", o, h, "--repo", repo, "--password-file", pw, "--snapshot", snap,
                    "--archiver", rustic, "--json", env=env)
            hd = json.loads(p.stdout)
            assert len(hd["headers"]) == 2, hd
            for r in hd["headers"]:
                assert open(r["path"], "rb").read() == heads[r["file"]], r["file"]
                assert stat.S_IMODE(os.stat(r["path"]).st_mode) == 0o600
            say("%s %s: bko-holt snapshot-layout --snapshot %s (rustic-bko, as bko-rescue runs it): exit 0, sequence 42, the layout's JSON; luks-headers: exit 0, both headers byte for byte, 0600"
                % (tool, variant, snap))
            if tool == "restic":
                # restic-bko's own read of the same two (by hand: holt's
                # restic-bko argv can't run, holt-json.md's oq15 notes).
                got = run(*rp, "dump", snap, "/.bko-holt/system.json", env=env).stdout
                assert got == open(sysjson, "rb").read()
                for f, b in heads.items():
                    assert run(*rp, "dump", snap, "/.bko-holt/luks-headers/" + f, env=env).stdout == b, f
                say("restic %s: restic-bko dump %s of /.bko-holt/system.json and both headers, by hand: byte for byte" % (variant, snap))
            shutil.rmtree(k)

    # Refusals.
    nr = ["--repository", "/nonexistent"]
    for what, args in (("-x", [rustic, *nr, "backup", "-x"]),
                       ("-vx", [rustic, *nr, "backup", "-vx"]),
                       ("--one-file-system=true", [rustic, *nr, "backup", "--one-file-system=true"]),
                       ("an absolute --glob after --", [rustic, *nr, "backup", "--glob", "!/swapfile"]),
                       ("an absolute --exclude= after --", [restic, "--repo", "/nonexistent", "backup", "--exclude=/swapfile"]),
                       ("no backup command", [rustic, *nr, "snapshots"]),
                       ("a relative --password-file for restic-bko", [restic, "--repo", "/nonexistent", "--password-file", "pw", "backup"])):
        mi = mountinfo()
        p = run(STAGE, "--bind", src, "/", "--stage", stage, "--", *args, ok=(2,), env=env)
        assert mountinfo() == mi
        say("%s: exit 2: %s" % (what, p.stderr.decode().strip()))
    p = run(STAGE, "--help", ok=(2,), env=env)
    hl = p.stderr.decode().splitlines()
    assert 3 <= len(hl) <= 8 and hl[0].startswith("Usage") and "-- ARCHIVER" in hl[-1], hl
    say("--help: exit 2, the usage's %d lines alone" % len(hl))
    disk = "/var/tmp/bko-stage-test-%d" % os.getpid()
    p = run(STAGE, "--bind", src, "/", "--stage", disk, "--", rustic, *nr, "backup", ok=(1,), env=env)
    assert not os.path.lexists(disk), disk
    say("a stage on disk: exit 1, nothing made: " + p.stderr.decode().strip())
    p = run(STAGE, "--bind", src, "/", "--stage", disk + "/a/stage", "--", rustic, *nr, "backup", ok=(1,), env=env)
    assert not os.path.lexists(disk), disk
    say("a stage whose missing parents' nearest is on disk: exit 1, nothing made: " + p.stderr.decode().strip())
    p = run(STAGE, "--bind", ro, "/", "--bind", os.path.join(src, "home"), "/etc/nohere", "--stage", stage, "--",
            rustic, *nr, "backup", ok=(1,), env=env)
    say("a bind with no mount point under the read-only root: exit 1: " + p.stderr.decode().strip().splitlines()[-1])
    # The marker set to its own PID (bash -c's exec keeps it), so it doesn't
    # unshare: it must see it is in its caller's namespace and stop.
    mi = mountinfo()
    p = run("bash", "-c", 'BKO_STAGE_UNSHARED=$$ exec "$@"', "bash", STAGE, "--bind", src, "/", "--stage", stage, "--",
            rustic, *nr, "backup", ok=(1,), env=env)
    assert mountinfo() == mi and os.listdir(stage) == [], "it mounted in its caller's namespace"
    say("the marker set to its own PID, no namespace made: exit 1, no mount: " + p.stderr.decode().strip())
    # SIGTERM to stage-backup.sh's PID (the caller's) reaches the archiver,
    # whose status comes back; nothing is left in the namespace.
    slow = os.path.join(work, "slow-rustic")
    with open(slow, "w") as f:
        f.write('#!/bin/sh\ncase "${1:-}" in --version|version) exec %s "$@" ;; esac\nexec sleep 600.4243\n' % rustic)
    os.chmod(slow, 0o755)
    mi = mountinfo()
    pr = subprocess.Popen([STAGE, "--bind", src, "/", "--stage", stage, "--", slow, *nr, "backup"], env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    slept = b"sleep\x00600.4243\x00"
    for _ in range(100):
        if any(open("/proc/%s/cmdline" % q, "rb").read() == slept for q in os.listdir("/proc")
               if q.isdigit() and os.path.exists("/proc/%s/cmdline" % q)):
            break
        subprocess.run(["sleep", "0.1"])
    else:
        sys.exit("stage-test: the slow archiver never started")
    pr.send_signal(15)
    err = pr.communicate(timeout=30)[1]
    assert pr.returncode == 143, (pr.returncode, err)
    left = [q for q in os.listdir("/proc") if q.isdigit() and os.path.exists("/proc/%s/cmdline" % q)
            and open("/proc/%s/cmdline" % q, "rb").read() == slept]
    assert left == [] and mountinfo() == mi and os.listdir(stage) == [], (left, err)
    say("SIGTERM to stage-backup.sh while the archiver runs: passed on, exit 143 (the archiver's), nothing left, no mount")

    say("PASS")
    if out_file:
        with open(out_file, "w") as f:
            f.write("\n".join(said) + "\n")
    for m in ("ro", "src/scratch", "src/boot/efi", "src/home"):
        run("umount", os.path.join(work, m))
    shutil.rmtree(work)


if __name__ == "__main__":
    main(sys.argv)
