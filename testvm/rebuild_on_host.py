"""testvm/rebuild-on's host half (docs/design/img-speed.md §12 F12), sent over ssh as
`python3 -I - SUB ARGS` (no synced copy needed); it also runs here directly (the t2 tests).

  list [--hash] NAME...        ROOT/NAME's files, as JSON: {"root", "avail" (bytes free on ROOT's
                               file system), "dirs": {NAME: {REL: [kind, size, sha256|null]}}};
                               kind f (a file) or l (a symlink; size its target's text length);
                               a NAME that isn't there is left out
  listdir [--hash] DIR FILE... the same for FILEs of DIR (relative: to the home), symlinks
                               followed, as {"dir": ABS, "files": {...}}
  copy SRC SRCDIR DEST [--dedup] [--deref] [FILE...]
                               SRCDIR's FILEs (none: all of it) from host SRC ("local": here),
                               by tar over ssh, into DEST, which must not exist: into a part
                               folder beside it, then renamed.  --dedup: each copied file over
                               64 MiB that has a twin (size and SHA-256) elsewhere under ROOT
                               becomes a hard link to it.  --deref: symlinks copied as the
                               files they name (tar -h)
  check W                      a rebuild's folder (rebuild-one.sh's REBUILD_DIR/vms/GUEST): did it
                               build (build.exit 0) and come up (rebuild-boot.json), and its disks
                               (regular *.qcow2) with their SHA-256: JSON {"ok", "why", "disks"}
  start DIR TAG GUEST D M [TESTVM]
                               DIR/testvm from TESTVM (a folder here; default ~/backotter-work/testvm)
                               under DIR/.testvm.lock held exclusive (refused while a rebuild runs
                               from it), then, detached and holding that lock shared, from
                               ~/backotter-work: WEAR_TAG=rebuild:TAG CMP_CMD="env REBUILD_DIR=DIR
                               DIR/testvm/rebuild-one.sh" cmp-run.sh TAG 1 GUEST=D%M; its exit (the
                               job's, from cmp-logs/TAG/done) lands in DIR/rebuild-on/TAG.exit
  status DIR TAG               JSON {"exit": N|null, "log": the last lines}

Environment: BKO_REBUILD_INPUTS (ROOT, default /bkoscratch/rebuild-inputs), BKO_REBUILD_SSH (the
ssh words for SRC, default ssh -o BatchMode=yes -o ConnectTimeout=15), BKO_CMP_RUN (default
~/backotter-work/cmp-run.sh), BKO_REBUILD_LINK_MIN (--dedup's 64 MiB, for tests).  Exit 0; 1 refused or failed (the reason on stderr); 2 bad usage.
"""

import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys

ROOT = os.environ.get("BKO_REBUILD_INPUTS") or "/bkoscratch/rebuild-inputs"
BIG = int(os.environ.get("BKO_REBUILD_LINK_MIN") or 64 << 20)   # (tests set it smaller)


def die(msg, rc=1):
    print(f"rebuild-on-host: {msg}", file=sys.stderr)
    sys.exit(rc)


def home(p):
    return p if os.path.isabs(p) else os.path.join(os.path.expanduser("~"), p)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def entry(p, hashed, follow=False):
    if os.path.islink(p) and not follow:
        t = os.readlink(p)
        return ["l", len(t), t]
    st = os.stat(p)
    return ["f", st.st_size, sha(p) if hashed else None]


def walk(top, hashed):
    out = {}
    for d, dns, fns in os.walk(top):
        dns.sort()
        for n in sorted(fns) + [x for x in dns if os.path.islink(os.path.join(d, x))]:
            p = os.path.join(d, n)
            if os.path.islink(p) or os.path.isfile(p):
                out[os.path.relpath(p, top)] = entry(p, hashed)
    return out


def avail(p):
    while not os.path.exists(p):
        p = os.path.dirname(p)
    s = os.statvfs(p)
    return s.f_bavail * s.f_frsize


def cmd_list(args):
    hashed = bool(args) and args[0] == "--hash"
    names = args[1:] if hashed else args
    dirs = {n: walk(os.path.join(ROOT, n), hashed) for n in names if os.path.isdir(os.path.join(ROOT, n))}
    print(json.dumps({"root": ROOT, "avail": avail(ROOT), "dirs": dirs}))


def cmd_listdir(args):
    hashed = bool(args) and args[0] == "--hash"
    if hashed:
        args = args[1:]
    if not args:
        die("listdir [--hash] DIR FILE...", 2)
    d = os.path.abspath(home(args[0]))
    if not os.path.isdir(d):
        die(f"no folder {d}")
    files = {}
    for f in args[1:]:
        p = os.path.join(d, f)
        if not (os.path.isfile(p) or os.path.islink(p)):
            die(f"no file {p}")
        files[f] = entry(p, hashed, follow=True)   # as rebuild-one.sh takes it (cp -L)
    print(json.dumps({"dir": d, "files": files}))


def twins(top, skip):
    """ROOT's files over 64 MiB outside SKIP, by size."""
    by = {}
    for d, dns, fns in os.walk(top):
        dns[:] = [x for x in dns if os.path.join(d, x) != skip and not x.startswith(".")]
        for n in fns:
            p = os.path.join(d, n)
            if not os.path.islink(p) and os.path.isfile(p) and os.path.getsize(p) > BIG:
                by.setdefault(os.path.getsize(p), []).append(p)
    return by


def dedup(dest):
    by, n, size, memo = twins(ROOT, dest), 0, 0, {}
    for rel, (kind, sz, _) in walk(dest, False).items():
        p = os.path.join(dest, rel)
        if kind != "f" or sz <= BIG or sz not in by:
            continue
        mine = sha(p)
        for q in by[sz]:
            if os.stat(q).st_ino == os.stat(p).st_ino:
                break
            if q not in memo:
                memo[q] = sha(q)
            if memo[q] == mine:
                tmp = p + ".link-tmp"
                os.link(q, tmp)
                os.replace(tmp, p)
                n, size = n + 1, size + sz
                break
    print(f"linked {n} file(s), {size / (1 << 30):.1f} GiB, to their twins under {ROOT}")


def cmd_copy(args):
    dd, deref = "--dedup" in args, "--deref" in args
    args = [a for a in args if a not in ("--dedup", "--deref")]
    if len(args) < 3:
        die("copy SRC SRCDIR DEST [--dedup] [--deref] [FILE...]", 2)
    src, srcdir, dest, files = args[0], args[1], os.path.abspath(home(args[2])), args[3:]
    if os.path.lexists(dest):
        die(f"{dest} is there already: move it aside first")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = os.path.join(os.path.dirname(dest), f".{os.path.basename(dest)}.part-{os.getpid()}")
    os.makedirs(part)
    tar = "tar -C " + shlex.quote(srcdir) + (" -h" if deref else "") + " -cf - -- " + (" ".join(shlex.quote(f) for f in files) or ".")
    ssh = shlex.split(os.environ.get("BKO_REBUILD_SSH") or "ssh -o BatchMode=yes -o ConnectTimeout=15")
    send = ["bash", "-c", tar] if src == "local" else ssh + [src, tar]
    try:
        a = subprocess.Popen(send, stdout=subprocess.PIPE)
        b = subprocess.run(["tar", "-C", part, "-xpf", "-"], stdin=a.stdout)
        a.stdout.close()
        if a.wait() or b.returncode:
            raise OSError(f"tar from {src}:{srcdir} failed ({a.returncode}, {b.returncode})")
        os.rename(part, dest)
    except OSError as e:
        subprocess.run(["rm", "-rf", part])
        die(str(e))
    print(f"copied {src}:{srcdir} to {dest}")
    if dd:
        dedup(dest)


def cmd_check(args):
    if len(args) != 1:
        die("check W", 2)
    w = home(args[0])
    why, disks = [], {}
    try:
        rc = open(os.path.join(w, "build.exit")).read().strip()
    except OSError:
        rc = None
    if rc != "0":
        why.append(f"build.exit is {rc!r}, not 0")
    try:
        boot = json.load(open(os.path.join(w, "rebuild-boot.json"))).get("boot") or {}
    except (OSError, ValueError):
        boot = None
    if boot is None:
        why.append("no rebuild-boot.json")
    elif not (boot.get("ssh") or boot.get("sculpt_check_exit") == 0 or boot.get("qubes_check_exit") == 0):
        why.append(f"its boot check failed: {json.dumps(boot)[:200]}")
    if os.path.isdir(w):
        for n in sorted(os.listdir(w)):
            p = os.path.join(w, n)
            if n.endswith(".qcow2") and not os.path.islink(p) and os.path.isfile(p) and not n.startswith("vm-run."):
                disks[n] = [os.path.getsize(p), sha(p)]
    if not disks:
        why.append("no disk")
    print(json.dumps({"ok": not why, "why": why, "disks": disks}))


def cmd_start(args):
    if len(args) not in (5, 6):
        die("start DIR TAG GUEST D M [TESTVM]", 2)
    d, tag, guest, disk, mem = args[:5]
    src = home(args[5]) if len(args) == 6 else home("backotter-work/testvm")
    d = os.path.abspath(home(d))
    if not os.path.isdir(src):
        die(f"no {src}")
    os.makedirs(os.path.join(d, "rebuild-on"), exist_ok=True)
    fd = os.open(os.path.join(d, ".testvm.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        die(f"a rebuild is running from {d}/testvm: wait for it, or give another --dir")
    ex = os.path.join(d, "rebuild-on", tag + ".exit")
    if os.path.exists(ex) or os.path.exists(os.path.join(d, "rebuild-on", tag + ".log")):
        die(f"tag {tag} has run in {d} already")
    if os.path.realpath(src) != os.path.realpath(os.path.join(d, "testvm")):
        subprocess.run(["rm", "-rf", os.path.join(d, "testvm")], check=True)
        subprocess.run(["cp", "-a", src, os.path.join(d, "testvm")], check=True)
    fcntl.flock(fd, fcntl.LOCK_SH)
    cmp = os.environ.get("BKO_CMP_RUN") or home("backotter-work/cmp-run.sh")
    q = shlex.quote
    job = (f"cd {q(home('backotter-work'))} && "
           f"WEAR_TAG={q('rebuild:' + tag)} CMP_CMD={q('env REBUILD_DIR=' + d + ' ' + d + '/testvm/rebuild-one.sh')} "
           f"{q(cmp)} {q(tag)} 1 {q(guest + '=' + disk + '%' + mem)}; "
           f"rc=$(awk -v g={q(guest)} '$1 == g {{ r = $2 }} END {{ print r }}' cmp-logs/{q(tag)}/done 2>/dev/null); "
           f"echo \"${{rc:-99}}\" >{q(ex)}.tmp && mv {q(ex)}.tmp {q(ex)}")
    log = open(os.path.join(d, "rebuild-on", tag + ".log"), "w")
    p = subprocess.Popen(["bash", "-c", job], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         pass_fds=(fd,), start_new_session=True)
    print(json.dumps({"pid": p.pid, "dir": d, "tag": tag, "log": log.name}))


def cmd_status(args):
    if len(args) != 2:
        die("status DIR TAG", 2)
    d = os.path.join(os.path.abspath(home(args[0])), "rebuild-on")
    try:
        rc = int(open(os.path.join(d, args[1] + ".exit")).read().strip())
    except (OSError, ValueError):
        rc = None
    try:
        tail = open(os.path.join(d, args[1] + ".log"), errors="replace").read().splitlines()[-8:]
    except OSError:
        tail = None
    print(json.dumps({"exit": rc, "log": tail}))


CMDS = {"list": cmd_list, "listdir": cmd_listdir, "copy": cmd_copy, "check": cmd_check,
        "start": cmd_start, "status": cmd_status}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        die("SUB: " + ", ".join(CMDS), 2)
    CMDS[sys.argv[1]](sys.argv[2:])
