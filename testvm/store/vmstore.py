"""vm-store: content-addressed copies of the test guests' disk images.

The logic behind testvm/vm-store (docs/design/image-store.md is the
contract).  One tree shape serves every role:

    ROOT/store.json                     {"schema":1,"role":"master|mirror|cache",...}
    ROOT/objects/sha256-HEX.qcow2       disk objects, 0444, never changed
    ROOT/objects/sha256-HEX.file        sidecar objects, 0400
    ROOT/objects/.incoming/             staging on the same file system
    ROOT/guests/G/versions/VID.json     a version's manifest, never rewritten
    ROOT/guests/G/versions/VID.json.sha256
    ROOT/guests/G/latest                "VID MANIFEST-SHA256"
    ROOT/live/G/VID/G/                  a version made into a guest folder
    ROOT/used/OBJECT                    a stamp: its mtime is the last ensure
    $BKO_WORK/vms/G -> ROOT/live/G/VID/G   a cache host's guest, made by link (gc rule f)
    ROOT/lock                           fetch, materialize and gc, one at a time

Chopin's root is the mirror, the only publisher; Bigfoot's is the master,
written only by push (rsync into its staging, then mv), and only Chopin
talks to it; the other hosts' are caches, filled from Chopin by ensure and
emptied by gc.  stdout carries only the
result; everything else goes to stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import errno
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SCHEMA = 1
TOOL = "vm-store/1"
TESTVM = Path(__file__).resolve().parent.parent
DEFAULT_MIRROR = "/bkoscratch/bko-store"
DEFAULT_CACHE = "/bkoscratch/bko-cache"
# D1: bt's top level, which the gmk backups copy without --del (append-only there);
# the staging dot-dir on the same dataset is never backed up.
DEFAULT_BF_ROOT = "/media/DantePool/bt/BkoStore"
DEFAULT_BF_STAGE = "/media/DantePool/bt/.bko-staging"
DEFAULT_BF_HOST = "m@bigfoot.local"
DEFAULT_CHOPIN = "m@chopin.local"
DEFAULT_SCAN = "/mnt/bko-ram /dev/shm /bkoscratch"
# Trees gc's overlay scan never walks: tool trees and chroots that hold no overlay of a
# store object (the Test2 gate's Arch chroot, /bkoscratch/tools/arch-root, root's 0700 dirs).
DEFAULT_SCAN_SKIP = "/bkoscratch/tools"
FLEET_LOCK = ".vm-update.fleet.lock"                       # vm-update's, in vms/
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=15"]
SSH_E = "ssh " + " ".join(SSH_OPTS)
REMOTE_TIMEOUT = 120
DEFAULT_RSYNC_TIMEOUT = 300                # VM_STORE_RSYNC_TIMEOUT: rsync --timeout to and from Bigfoot
# Bigfoot's health probe (bigfoot_health): the whole ssh round trip, and each step on
# Bigfoot (4 steps of STEP + timeout's 2 s grace, plus ssh's connect, fit in the whole).
# Kernel lines that mean a disk's USB bridge reset or dropped (the 2026-11-13 DantePool
# suspension: hub resets behind 4-1.4); an ERE, run by grep there and re here.
BF_PROBE_TIMEOUT = 45
BF_STEP_TIMEOUT = 5
# Bigfoot's xfer.lock (one stream at a time): rsync's --timeout also counts the silent
# wait for the lock, so each try's flock gives up first, with XFER_BUSY (no rsync exit
# status), and run_xfer tries again until XFER_LOCK_WAIT has gone by.
XFER_BUSY = 99
XFER_LOCK_WAIT = 3600
USB_RESET_ERE = r"usb [0-9]+-[0-9.]+: .*(reset .*USB device|USB disconnect|device descriptor read.*error|error -71)"
BIG_SIDECAR = 64 << 20
INCOMING_MAX_AGE = 7 * 86400
BLIND_AGE = 7 * 86400
DEFAULTS = {"cache": {"cap": "100G", "floor": "100G"}, "mirror": {"cap": None, "floor": "200G"},
            "master": {"cap": None, "floor": None}}
OBJ_RE = re.compile(r"sha256-([0-9a-f]{64})\.(qcow2|file)")
VID_RE = re.compile(r"[gi][0-9a-f]{16}")
GUEST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
HEX64 = re.compile(r"[0-9a-f]{64}")
LINK_TMP_RE = re.compile(r"\.(.+)\.vm-store-link\.(\d+)")      # link's temp link in vms/
# Sidecars not stored (section 1.8): logs, vm-update's working files, locks,
# this host's records, install media.  Matched against each name in the path.
SKIP_GLOBS = ["*.log", "build.exit", "*.png", ".vm-update*", "*.lock", "image-stat.json", "image-fingerprint.json",
              "rebuild-boot*", "*.iso", "*.iso.*", "*.img", "*.img.*", "*.raw*", "*.tar*", "*.tgz", "*.xz",
              "*.bz2", "*.zst", ".vm-store.json"]


class StoreError(Exception):
    rc = 1


class Usage(StoreError):
    rc = 2


class Busy(StoreError):
    rc = 3


class NoRoom(StoreError):
    rc = 4


class Unreachable(StoreError):
    """A source or Bigfoot didn't answer."""


class Unhealthy(Unreachable):
    """Bigfoot answered, but its pool isn't fit to use (bigfoot_gate)."""


def say(msg: str) -> None:
    print(f"vm-store: {msg}", file=sys.stderr, flush=True)


def gib(n: int | float) -> str:
    return f"{n / (1 << 30):.2f} GiB"


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_size(text: str | int | None) -> int | None:
    """100G, 512M, 3GiB, 0, or bytes; binary units.  None/"" stays None."""
    if text is None or text == "":
        return None
    if isinstance(text, int):
        return text
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)(?:i?B)?\s*", str(text), re.I)
    if not m:
        raise Usage(f"not a size: {text!r} (e.g. 100G)")
    return int(float(m.group(1)) * (1 << (10 * " KMGTP".index(m.group(2).upper() or " "))))


def parse_age(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*([smhd]?)\s*", text)
    if not m:
        raise Usage(f"not an age: {text!r} (e.g. 24h)")
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def in_window(spec: str, now: datetime.datetime | None = None) -> bool:
    """Whether NOW (local time) is inside HH:MM-HH:MM, which may wrap midnight."""
    m = re.fullmatch(r"(\d\d?):(\d\d)-(\d\d?):(\d\d)", spec.strip())
    if not m:
        raise Usage(f"VM_STORE_WINDOW is not HH:MM-HH:MM: {spec!r}")
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    now = now or datetime.datetime.now()
    t = now.hour * 60 + now.minute
    return a <= t < b if a <= b else (t >= a or t < b)


# --- Files ---------------------------------------------------------------------------

def fsync_dir(d: Path | str) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as e:                       # some file systems refuse it on a directory
        if e.errno not in (errno.EINVAL, errno.EBADF):
            raise
    finally:
        os.close(fd)


def fsync_file(path: Path | str) -> None:
    """Push PATH's data to disk before anything durable names it (a plain copy on
    XFS can otherwise come back short or empty after a power loss)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path: Path, data: bytes | str, mode: int = 0o644) -> None:
    """tmp, fsync, chmod, rename, fsync of the directory."""
    if isinstance(data, str):
        data = data.encode()
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.rename(tmp, path)
    fsync_dir(path.parent)


def allocated(path: Path | str) -> int:
    try:
        return os.lstat(path).st_blocks * 512
    except OSError:
        return 0


def tree_allocated(path: Path) -> int:
    total = 0
    for dp, _dns, fns in os.walk(path):
        total += sum(allocated(os.path.join(dp, f)) for f in fns)
    return total


def free_bytes(path: Path | str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


_GNU = {}


def gnu(tool: str) -> bool:
    """Whether TOOL here is GNU coreutils' (the hosts), not BSD's (the Mac)."""
    if tool not in _GNU:
        try:
            r = subprocess.run([tool, "--version"], capture_output=True, text=True)
            _GNU[tool] = r.returncode == 0 and "GNU" in r.stdout
        except OSError:
            _GNU[tool] = False
    return _GNU[tool]


def sha256_file(path: Path | str) -> str:
    """nice -n19 sha256sum where there is GNU's, else hashlib."""
    if gnu("sha256sum"):
        r = subprocess.run(["nice", "-n19", "sha256sum", "--", str(path)], capture_output=True, text=True)
        if r.returncode != 0:
            raise StoreError(f"sha256sum {path}: {r.stderr.strip()}")
        return r.stdout.split()[0]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_child(cmd: list[str], **kw) -> int:
    """Run CMD in a session of its own (a signal to its group spares us); stop it
    if we are stopped.  Its stdout goes to our stderr."""
    try:
        kw.setdefault("stdout", sys.stderr.fileno())
    except (AttributeError, OSError, ValueError):   # a StringIO (the tests)
        kw.setdefault("stdout", 2)
    try:
        p = subprocess.Popen(cmd, start_new_session=True, **kw)
    except OSError as e:
        raise StoreError(f"{cmd[0]}: {e}")
    try:
        return p.wait()
    except BaseException:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
        raise


class ReflinkFailed(StoreError):
    """copy_file(how="reflink") couldn't reflink: nothing is left at DST."""


_REFLINK: dict[tuple[int, int], bool] = {}


def reflink_ok(src: Path | str, dest_dir: Path) -> bool:
    """Whether cp --reflink=always of SRC into DEST_DIR should work: GNU cp, one
    file system, and a probe file in DEST_DIR reflinks (once per file system)."""
    if not gnu("cp"):
        return False
    try:
        a, b = os.stat(src).st_dev, os.stat(dest_dir).st_dev
    except OSError:
        return False
    if a != b:
        return False
    if (a, b) not in _REFLINK:
        tok = secrets.token_hex(8)
        p, q = dest_dir / f"probe.{tok}", dest_dir / f"probe.{tok}.copy"
        try:
            p.write_bytes(b"vm-store reflink probe\n" * 200)
            r = subprocess.run(["cp", "--reflink=always", "--", str(p), str(q)], capture_output=True, text=True)
            _REFLINK[(a, b)] = r.returncode == 0
        except OSError:
            return False
        finally:
            for f in (p, q):
                with contextlib.suppress(FileNotFoundError):
                    f.unlink()
    return _REFLINK[(a, b)]


def copy_file(src: Path, dst: Path, how: str = "auto") -> str:
    """SRC to DST: cp --reflink=always, else cp --sparse=always ("plain").
    Without GNU cp (the Mac) a sparse copy of our own.  HOW "reflink" is a
    reflink or ReflinkFailed, never a copy."""
    if how == "reflink":
        r = subprocess.run(["cp", "--reflink=always", "--", str(src), str(dst)], capture_output=True,
                           text=True) if gnu("cp") else None
        if r is not None and r.returncode == 0:
            return "reflink"
        with contextlib.suppress(FileNotFoundError):
            dst.unlink()
        raise ReflinkFailed(f"cp --reflink=always {src}: {r.stderr.strip() if r else 'no GNU cp here'}")
    if gnu("cp"):
        if how == "auto":
            r = subprocess.run(["cp", "--reflink=always", "--", str(src), str(dst)], capture_output=True, text=True)
            if r.returncode == 0:
                return "reflink"
            with contextlib.suppress(FileNotFoundError):
                dst.unlink()
        r = subprocess.run(["cp", "--sparse=always", "--", str(src), str(dst)], capture_output=True, text=True)
        if r.returncode != 0:
            raise StoreError(f"cp {src}: {r.stderr.strip()}")
        return "plain"
    block = 1 << 20
    zero = bytes(block)
    with open(src, "rb") as i, open(dst, "wb") as o:
        while chunk := i.read(block):
            if chunk == zero[:len(chunk)]:
                o.seek(len(chunk), os.SEEK_CUR)
            else:
                o.write(chunk)
        o.truncate()
        o.flush()
        os.fsync(o.fileno())
    return "plain"


def in_store(path: Path | str) -> bool:
    """Whether PATH is a store object: its realpath lies in (or below) some ROOT/objects/
    whose ROOT holds store.json.  By path, not by permission.  Other files under a
    root (a scratch guest, an overlay) are not objects."""
    d = os.path.dirname(os.path.realpath(path))
    while d not in ("/", ""):
        if os.path.basename(d) == "objects" and os.path.isfile(os.path.join(os.path.dirname(d), "store.json")):
            return True
        d = os.path.dirname(d)
    return False


# --- qcow2 headers and QEMU's locks --------------------------------------------------

def qcow2_header(path: Path | str, strict: bool = False) -> dict | None:
    """The fields of a qcow2 header vm-store needs, or None if PATH isn't qcow2.
    STRICT: an OSError (unreadable, missing) is raised, not taken for "not qcow2"."""
    try:
        with open(path, "rb") as f:
            hdr = f.read(104)
            if len(hdr) < 72 or hdr[:4] != b"QFI\xfb":
                return None
            version, b_off, b_len = struct.unpack(">IQI", hdr[4:20])
            size = struct.unpack(">Q", hdr[24:32])[0]
            nb_snap, snap_off = struct.unpack(">IQ", hdr[60:72])
            incompat = struct.unpack(">Q", hdr[72:80])[0] if version >= 3 and len(hdr) >= 80 else 0
            backing = None
            if b_off and b_len:
                f.seek(b_off)
                backing = f.read(b_len).decode("utf-8", "replace")
            snaps = []
            if nb_snap and snap_off:
                f.seek(snap_off)
                for _ in range(min(nb_snap, 1024)):
                    fixed = f.read(40)
                    if len(fixed) < 40:
                        break
                    id_len, name_len = struct.unpack(">HH", fixed[12:16])
                    extra = struct.unpack(">I", fixed[36:40])[0]
                    f.read(extra)
                    f.read(id_len)
                    snaps.append(f.read(name_len).decode("utf-8", "replace"))
                    used = 40 + extra + id_len + name_len
                    f.read((8 - used % 8) % 8)
    except OSError:
        if strict:
            raise
        return None
    return {"version": version, "backing": backing, "virtual_size": size, "corrupt": bool(incompat & 2),
            "snapshots": snaps}


def qcow2_backing(path: Path | str) -> str | None:
    """The backing file name a qcow2 header records (as written: maybe relative), or None."""
    h = qcow2_header(path)
    return h["backing"] if h else None


def disk_info(path: Path) -> dict:
    """{format, backing, corrupt, virtual_size, snapshots}: qemu-img info -U where
    there is qemu-img, else from the header itself."""
    if shutil.which("qemu-img"):
        r = subprocess.run(["qemu-img", "info", "-U", "--output=json", str(path)], capture_output=True, text=True)
        if r.returncode != 0:
            raise StoreError(f"qemu-img info {path}: {r.stderr.strip()}")
        info = json.loads(r.stdout)
        data = (info.get("format-specific") or {}).get("data") or {}
        return {"format": info.get("format"), "backing": info.get("backing-filename"),
                "corrupt": bool(data.get("corrupt")), "virtual_size": info.get("virtual-size"),
                "snapshots": [s.get("name") for s in info.get("snapshots") or []]}
    h = qcow2_header(path)
    if h is None:
        return {"format": "raw", "backing": None, "corrupt": False, "virtual_size": os.path.getsize(path),
                "snapshots": []}
    return dict(h, format="qcow2")


# struct flock: Linux's is l_type, l_whence, l_start, l_len, l_pid; Darwin's l_start, l_len, l_pid, l_type, l_whence.
_DARWIN = sys.platform == "darwin"


def _flock(kind: int, start: int, length: int) -> bytes:
    if _DARWIN:
        return struct.pack("qqihh", start, length, 0, kind, os.SEEK_SET)
    return struct.pack("hhqqi4x", kind, os.SEEK_SET, start, length, 0)


def _flock_type(raw: bytes) -> int:
    return struct.unpack("qqihh", raw)[3] if _DARWIN else struct.unpack("hhqqi4x", raw)[0]


def ofd_conflict(fd: int, start: int, length: int) -> bool:
    """Whether anyone (an OFD or POSIX lock elsewhere) holds a lock on bytes [start, start+length)."""
    return _flock_type(fcntl.fcntl(fd, fcntl.F_OFD_GETLK, _flock(fcntl.F_WRLCK, start, length))) != fcntl.F_UNLCK


def qemu_holds(path: Path | str) -> bool:
    """Whether a QEMU program has PATH open: its locks on bytes 100-299 (as vm-update's qemu_holds)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return True                            # can't tell: as if it had
    try:
        return ofd_conflict(fd, 100, 200)
    finally:
        os.close(fd)


class Fence:
    """Section 2.4 step 3: a read lock on byte 201 ("WRITE not shared") stops any
    new QEMU writer; a lock on byte 101 ("holds WRITE") means one is running."""

    def __init__(self, path: Path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.fcntl(self.fd, fcntl.F_OFD_SETLK, _flock(fcntl.F_RDLCK, 201, 1))
        except OSError as e:
            self.close()
            raise Busy(f"{path}: can't fence it ({e.strerror}): something holds QEMU's write lock")
        if ofd_conflict(self.fd, 101, 1):
            self.close()
            raise Busy(f"{path} is open for writing (a writable boot, or vm-mount without --ro); try later")

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)                  # closing the description lets go of its OFD locks
            self.fd = None


@contextlib.contextmanager
def flocked(path: Path, block: bool = True, busy: str | None = None):
    f = open(path, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
    except BlockingIOError:
        f.close()
        raise Busy(busy or f"{path} is held")
    try:
        yield f
    finally:
        f.close()


# --- Configuration -------------------------------------------------------------------

class Source:
    """Where ensure fetches from: NAME=[USER@]HOST:/PATH, or a bare local /PATH (tests)."""

    def __init__(self, name: str, spec: str, cfg: Config):
        self.name, self.spec = name, spec
        m = re.fullmatch(r"([^/:]+):(/.*)", spec)
        self.host, self.path = (m[1], m[2].rstrip("/") or "/") if m else (None, spec.rstrip("/") or "/")
        self.cfg = cfg                         # Bigfoot's: stage, bwlimit, health gate, rsync timeout
        self.down = False                      # it didn't answer: not tried again in this run
        self.gated: str | None = None          # Bigfoot's: "" once its pool passed the gate, else why not
        self.probed = False                    # Bigfoot's: a failed rsync ran the probe once

    @property
    def bigfoot(self) -> bool:
        """Named bigfoot, or on VM_STORE_BIGFOOT_HOST's host (by any name)."""
        if self.name == "bigfoot":
            return True
        bf = self.cfg.bf_host.rpartition("@")[2]
        return bool(bf and self.host) and self.host.rpartition("@")[2] == bf

    def __repr__(self) -> str:
        return f"{self.name}={self.spec}"

    def url(self, rel: str) -> str:
        p = f"{self.path}/{rel}"
        return f"{self.host}:{p}" if self.host else p

    def check(self) -> None:
        """Bigfoot's pool gate, once a run (a suspended pool hangs its readers): Unhealthy,
        and down, unless the pool is ONLINE.  Other sources pass."""
        if not self.bigfoot:
            return
        if self.gated is None:
            try:
                bigfoot_gate(self.cfg, f"source {self.name}", "not reading from it", pool_only=True)
                self.gated = ""
            except Unhealthy as e:
                self.gated = str(e)
        if self.gated:
            self.down = True
            raise Unhealthy(self.gated)

    def after_failure(self) -> str:
        """After a failed rsync from Bigfoot: the probe's verdict, once a run ("" for other
        sources); the source is down from then on if its pool isn't ONLINE."""
        if not self.bigfoot or self.probed:
            return ""
        self.probed = True
        _, text, detail = bigfoot_verdict(self.cfg, lock=True)
        if detail.get("health") != "ONLINE":
            self.down = True
        return text

    def read(self, rel: str) -> bytes | None:
        """REL's bytes, or None if the source answered without it; Unreachable if it didn't answer."""
        self.check()
        if self.host is None:
            if not os.path.isdir(self.path):
                self.down = True
                raise Unreachable(f"source {self.name}: no {self.path}")
            try:
                return Path(self.path, rel).read_bytes()
            except FileNotFoundError:
                return None
        try:
            r = subprocess.run(["ssh", *SSH_OPTS, self.host, "cat -- " + shlex.quote(f"{self.path}/{rel}")],
                               capture_output=True, timeout=REMOTE_TIMEOUT, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            self.down = True
            raise Unreachable(f"source {self.name}: {self.host} timed out")
        if r.returncode == 255:
            self.down = True
            raise Unreachable(f"source {self.name}: {r.stderr.decode(errors='replace').strip()}")
        return r.stdout if r.returncode == 0 else None

    def rsync_cmd(self, rel: str, dest: str) -> list[str]:
        cmd = ["nice", "-n19", "rsync", "-t", "--fsync", "--no-whole-file", "--sparse", "--partial-dir=.partial"]
        if self.host:
            cmd += ["-e", SSH_E]
        if self.bigfoot:
            cmd += [f"--bwlimit={self.cfg.bwlimit}", "--compress", "--compress-choice=zstd", "--compress-level=1",
                    xfer_rsync_path(self.cfg), f"--timeout={self.cfg.rsync_timeout}"]
        return cmd + [self.url(rel), dest]


class Config:
    def __init__(self, env: dict | None = None):
        env = os.environ if env is None else env
        self.env = env
        self.work = Path(env.get("BKO_WORK") or "~/backotter-work").expanduser()
        self.vms = self.work / "vms"
        root = env.get("VM_STORE_ROOT") or (DEFAULT_MIRROR if os.path.isdir(DEFAULT_MIRROR) else DEFAULT_CACHE)
        self.root = Path(root)
        self.bf_root = (env.get("VM_STORE_BIGFOOT") or DEFAULT_BF_ROOT).rstrip("/")
        self.bf_stage = (env.get("VM_STORE_BIGFOOT_STAGE") or DEFAULT_BF_STAGE).rstrip("/")
        # "" runs Bigfoot's side here, on local paths (the tests).
        self.bf_host = env.get("VM_STORE_BIGFOOT_HOST", DEFAULT_BF_HOST)
        self.bwlimit = env.get("VM_STORE_BWLIMIT_BIGFOOT") or "40000"
        self.window = env.get("VM_STORE_WINDOW") or "00:30-07:30"
        # Bigfoot's health gate (bigfoot_gate) and the I/O timeout of every rsync to or from
        # it: a suspended pool (failmode=wait) hangs a reader or writer there for good.
        self.rsync_timeout = env.get("VM_STORE_RSYNC_TIMEOUT") or str(DEFAULT_RSYNC_TIMEOUT)
        if not self.rsync_timeout.isdigit() or int(self.rsync_timeout) == 0:
            raise Usage(f"VM_STORE_RSYNC_TIMEOUT is seconds (> 0), not {self.rsync_timeout!r}")
        m = re.fullmatch(r"/media/([^/]+)/.*", self.bf_stage)
        self.bf_pool = env.get("VM_STORE_BF_POOL") or (m[1] if m else "DantePool")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", self.bf_pool):
            raise Usage(f"VM_STORE_BF_POOL is not a pool name: {self.bf_pool!r}")
        self.bf_reset_window = env.get("VM_STORE_BF_RESET_WINDOW") or "6h"
        try:
            self.bf_reset_secs = parse_age(self.bf_reset_window)
        except Usage:
            raise Usage(f"VM_STORE_BF_RESET_WINDOW is an age (e.g. 6h; 0: no USB reset check), "
                        f"not {self.bf_reset_window!r}")
        gate = env.get("VM_STORE_BF_GATE") or "1"
        if gate not in ("0", "1"):
            raise Usage(f"VM_STORE_BF_GATE is 0 or 1, not {gate!r}")
        self.bf_gate = gate == "1"
        self.copy = env.get("VM_STORE_COPY") or "auto"
        if self.copy not in ("auto", "plain"):
            raise Usage(f"VM_STORE_COPY is auto or plain, not {self.copy!r}")
        # Opt-in: publish counts a reflinked copy as no new space, so it may go ahead
        # below the floor.  Off by default, where every copy counts its allocated size.
        fr = env.get("VM_STORE_FLOOR_REFLINK") or "0"
        if fr not in ("0", "1"):
            raise Usage(f"VM_STORE_FLOOR_REFLINK is 0 or 1, not {fr!r}")
        self.floor_reflink = fr == "1"
        self.scan = (env.get("VM_STORE_SCAN") if env.get("VM_STORE_SCAN") is not None else DEFAULT_SCAN).split()
        self.scan_skip = (env.get("VM_STORE_SCAN_SKIP") if env.get("VM_STORE_SCAN_SKIP") is not None
                          else DEFAULT_SCAN_SKIP).split()
        self.min_age = parse_age(env.get("VM_STORE_MIN_AGE") or "24h")
        # D2: only Chopin talks to Bigfoot, so a cache's default source is Chopin alone, and
        # with Chopin down ensure uses what is cached (as --offline).  On Chopin, where the
        # mirror needs no source, VM_STORE_SOURCES may name bigfoot=... (a test cache root).
        spec = env.get("VM_STORE_SOURCES") or f"chopin={DEFAULT_CHOPIN}:{DEFAULT_MIRROR}"
        self.sources = []
        for i, tok in enumerate(spec.split()):
            name, _, s = tok.partition("=") if "=" in tok.split("/")[0] else (f"src{i}", "", tok)
            self.sources.append(Source(name, s, self))

    def source(self, name: str) -> Source:
        for s in self.sources:
            if s.name == name:
                return s
        raise Usage(f"no source {name!r} in VM_STORE_SOURCES ({' '.join(map(repr, self.sources))})")


class Store:
    def __init__(self, root: Path, cfg: Config):
        self.root, self.cfg = Path(root), cfg
        try:
            self.meta = json.loads((self.root / "store.json").read_text())
        except FileNotFoundError:
            raise StoreError(f"{self.root} is not a store: vm-store init {self.root} --role mirror|cache")
        except ValueError:
            raise StoreError(f"{self.root}/store.json isn't valid JSON")
        if self.meta.get("schema") != SCHEMA:
            raise StoreError(f"{self.root}/store.json: schema {self.meta.get('schema')}, not {SCHEMA}")
        self.role = self.meta.get("role")
        if self.role not in DEFAULTS:
            raise StoreError(f"{self.root}/store.json: unknown role {self.role!r}")
        self.cap = parse_size(cfg.env.get("VM_STORE_CAP") or self.meta.get("cap"))
        self.floor = parse_size(cfg.env.get("VM_STORE_FLOOR") or self.meta.get("floor")) or 0
        self.objects = self.root / "objects"
        self.incoming = self.objects / ".incoming"
        self.guests = self.root / "guests"
        self.live = self.root / "live"
        self.used = self.root / "used"
        for d in (self.incoming, self.guests, self.live, self.used):
            d.mkdir(parents=True, exist_ok=True)

    def lock(self):
        return flocked(self.root / "lock")

    def obj(self, name: str) -> Path:
        return self.objects / name

    def object_names(self) -> list[str]:
        return sorted(e.name for e in os.scandir(self.objects) if e.is_file(follow_symlinks=False)
                      and OBJ_RE.fullmatch(e.name))

    def use(self) -> int:
        """What the objects and .incoming hold on disk (st_blocks)."""
        return sum(allocated(self.obj(n)) for n in self.object_names()) + tree_allocated(self.incoming)

    def free(self) -> int:
        return free_bytes(self.root)

    def vdir(self, guest: str) -> Path:
        return self.guests / guest / "versions"

    def latest(self, guest: str) -> tuple[str, str] | None:
        try:
            return parse_latest(self.guests.joinpath(guest, "latest").read_text())
        except FileNotFoundError:
            return None

    def manifest(self, guest: str, vid: str) -> tuple[dict, str] | None:
        """A complete manifest here (its .sha256 there and matching): (manifest, sha), else None."""
        try:
            data = (self.vdir(guest) / f"{vid}.json").read_bytes()
            want = (self.vdir(guest) / f"{vid}.json.sha256").read_text().split()[0]
        except (FileNotFoundError, IndexError):
            return None
        sha = sha256_bytes(data)
        if sha != want:
            return None
        return json.loads(data), sha

    def manifests(self):
        """Every complete manifest here: (guest, vid, manifest, sha)."""
        for g in sorted(p.name for p in self.guests.iterdir() if p.is_dir()):
            vd = self.vdir(g)
            if not vd.is_dir():
                continue
            for f in sorted(vd.glob("*.json")):
                got = self.manifest(g, f.stem)
                if got:
                    yield g, f.stem, got[0], got[1]

    def stamp(self, name: str) -> None:
        p = self.used / name
        p.touch()
        os.utime(p)

    def stamp_time(self, name: str) -> float:
        try:
            return os.stat(self.used / name).st_mtime
        except FileNotFoundError:
            return 0.0


def parse_latest(text: str) -> tuple[str, str]:
    parts = text.split()
    if len(parts) != 2 or not VID_RE.fullmatch(parts[0]) or not HEX64.fullmatch(parts[1]):
        raise StoreError(f"not a latest line: {text.strip()!r}")
    return parts[0], parts[1]


def manifest_bytes(m: dict) -> bytes:
    return (json.dumps(m, indent=1, sort_keys=True) + "\n").encode()


def manifest_objects(m: dict) -> list[str]:
    out = []
    for d in m.get("disks", []):
        out += [o for o in d.get("chain", []) if o not in out]
    out += [f["object"] for f in m.get("files", {}).values() if f["object"] not in out]
    return out


def object_sizes(m: dict) -> dict[str, int]:
    sizes = {}
    for d in m.get("disks", []):
        for o in d.get("chain", []):
            sizes.setdefault(o, 0)
        if d.get("chain"):
            sizes[d["chain"][-1]] = d.get("file_size") or 0
    for f in m.get("files", {}).values():
        sizes[f["object"]] = f.get("size") or 0
    return sizes


def check_manifest(m: dict, guest: str, vid: str) -> None:
    if m.get("schema") != SCHEMA or m.get("guest") != guest or m.get("version") != vid:
        raise StoreError(f"manifest {guest}@{vid} is not schema {SCHEMA} for {guest}@{vid}")
    for o in manifest_objects(m):
        if not OBJ_RE.fullmatch(o):
            raise StoreError(f"manifest {guest}@{vid} names a bad object {o!r}")


def parse_ref(ref: str) -> tuple[str, str | None]:
    guest, _, vid = ref.partition("@")
    if not GUEST_RE.fullmatch(guest):
        raise Usage(f"not a guest name: {guest!r}")
    if vid in ("", "latest"):
        return guest, None
    if not VID_RE.fullmatch(vid):
        raise Usage(f"not a version id: {vid!r} (g or i and 16 hex)")
    return guest, vid


def vid_of_sums(sums: dict[str, str]) -> str:
    """An i VID: t2's img: rule (facts_host.combine) over the disks' {name: sha256}."""
    return "i" + sha256_bytes("".join(f"{n}\t{h}\n" for n, h in sorted(sums.items())).encode())[:16]


def vid_of_generation(gen: str) -> str:
    return "g" + gen[:16]


def remote_py(host: str | None, script: str, args: dict, timeout: int = REMOTE_TIMEOUT) -> dict:
    """Run SCRIPT with python3 on HOST (None or "": here), ARGS as its ARGS; its stdout is JSON."""
    text = f"ARGS = {json.dumps(args)!r}\nimport json as _j\nARGS = _j.loads(ARGS)\n{script}"
    cmd = ["ssh", *SSH_OPTS, host, "python3 -"] if host else [sys.executable, "-"]
    try:
        r = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Unreachable(f"{host or 'here'}: timed out")
    if r.returncode == 255 and host:
        raise Unreachable(f"{host}: {r.stderr.strip()}")
    if r.returncode != 0:
        raise StoreError(f"{host or 'here'}: {r.stderr.strip() or f'exit status {r.returncode}'}")
    return json.loads(r.stdout)


# What a root holds, from anywhere: objects and their sizes, manifests (sizes,
# the .sha256s, each .json's own sha256, with FULL the manifests), each latest.
LIST_PY = r"""
import hashlib, os, sys
root, full = ARGS["root"], ARGS.get("full")
out = {"store": None, "objects": {}, "meta": {}, "sha256": {}, "json_sha": {}, "latest": {}, "manifests": {}}
try:
    out["store"] = _j.load(open(os.path.join(root, "store.json")))
except (OSError, ValueError):
    pass
od = os.path.join(root, "objects")
if os.path.isdir(od):
    for e in os.scandir(od):
        if e.name.startswith("sha256-") and e.is_file(follow_symlinks=False):
            out["objects"][e.name] = e.stat().st_size
gd = os.path.join(root, "guests")
if os.path.isdir(gd):
    for g in sorted(os.listdir(gd)):
        lp = os.path.join(gd, g, "latest")
        if os.path.isfile(lp):
            out["latest"][g] = open(lp).read()
        vd = os.path.join(gd, g, "versions")
        if not os.path.isdir(vd):
            continue
        for n in sorted(os.listdir(vd)):
            fp, rel = os.path.join(vd, n), g + "/versions/" + n
            if n.startswith(".") or not os.path.isfile(fp):
                continue
            out["meta"][rel] = os.path.getsize(fp)
            if n.endswith(".json.sha256"):
                out["sha256"][rel] = open(fp).read()
            elif n.endswith(".json"):
                data = open(fp, "rb").read()
                out["json_sha"][rel] = hashlib.sha256(data).hexdigest()
                if full:
                    out["manifests"][rel] = data.decode("utf-8", "replace")
_j.dump(out, sys.stdout)
"""

# Bigfoot's side of push (section 2.5): staged files into ROOT by rename only,
# never over a file already there (one that differs is reported, not replaced):
# objects; then each manifest with its .sha256, only when the .json's sha256 is
# what the .sha256 says and (to complete it) its objects are all there, .json
# before .sha256; then each latest whose manifest is whole there; then the old
# staging.
ACCEPT_PY = r"""
import hashlib, os, sys, time
root, inc = ARGS["root"], os.path.join(ARGS["stage"], "incoming")
od = os.path.join(root, "objects")
done = {"objects": [], "meta": [], "latest": [], "skipped": []}
def fsync_dir(d):
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
def read(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None
def first(b):
    try:
        return b.decode().split()[0]
    except (AttributeError, UnicodeDecodeError, IndexError):
        return None
def drop(p):
    try:
        os.unlink(p)
    except OSError:
        pass
for n in ARGS["objects"]:
    src, dst = os.path.join(inc, n), os.path.join(od, n)
    if not os.path.isfile(src):
        continue                               # its rsync failed: manifests needing it are skipped below
    if os.path.lexists(dst):
        if os.lstat(dst).st_size != os.path.getsize(src):
            done["skipped"].append("objects/" + n + ": differs on bigfoot (another size); not replaced")
        drop(src)
        continue
    os.rename(src, dst)
    done["objects"].append(n)
fsync_dir(od)
metas = set(ARGS["meta"])
for jr in sorted({r[:-len(".sha256")] if r.endswith(".sha256") else r for r in metas}):
    sr = jr + ".sha256"
    js, ss = os.path.join(inc, "meta", jr), os.path.join(inc, "meta", sr)
    jd, sd = os.path.join(root, "guests", jr), os.path.join(root, "guests", sr)
    staged_j = read(js) if jr in metas else None
    staged_s = read(ss) if sr in metas else None
    have_j, have_s = read(jd), read(sd)
    if have_j is not None and staged_j is not None and have_j != staged_j:
        done["skipped"].append(jr + ": differs on bigfoot; not replaced")
        drop(js), drop(ss)
        continue
    if have_s is not None and staged_s is not None and first(have_s) != first(staged_s):
        done["skipped"].append(sr + ": differs on bigfoot; not replaced")
        drop(js), drop(ss)
        continue
    j = have_j if have_j is not None else staged_j
    s = have_s if have_s is not None else staged_s
    if j is None or s is None:
        done["skipped"].append(jr + ": the manifest and its .sha256 aren't both on bigfoot")
        continue
    if hashlib.sha256(j).hexdigest() != first(s):
        done["skipped"].append(jr + ": doesn't match its .sha256")
        drop(js), drop(ss)
        continue
    if have_s is None:
        try:
            m = _j.loads(j)
            objs = [o for d in m.get("disks", []) for o in d.get("chain", [])] + [f["object"] for f in m.get("files", {}).values()]
            missing = [o for o in objs if not os.path.isfile(os.path.join(od, o))]
        except (ValueError, KeyError, TypeError, AttributeError):
            missing = ["?"]
        if missing:
            done["skipped"].append(jr + ": objects missing on bigfoot: " + " ".join(missing[:3]))
            continue
    os.makedirs(os.path.dirname(jd), exist_ok=True)
    for have, staged, src, dst, rel in ((have_j, staged_j, js, jd, jr), (have_s, staged_s, ss, sd, sr)):
        if have is None:
            os.rename(src, dst)
            fsync_dir(os.path.dirname(dst))
            done["meta"].append(rel)
        elif staged is not None:
            drop(src)                          # the same bytes are there already
for g in ARGS["latest"]:
    src, dst = os.path.join(inc, "latest", g, "latest"), os.path.join(root, "guests", g, "latest")
    try:
        vid, sha = open(src).read().split()
    except (OSError, ValueError):
        vid, sha = None, None
    vd = os.path.join(root, "guests", g, "versions")
    j = read(os.path.join(vd, str(vid) + ".json")) if vid else None
    if sha is None or j is None or first(read(os.path.join(vd, vid + ".json.sha256"))) != sha \
            or hashlib.sha256(j).hexdigest() != sha:
        done["skipped"].append(g + "/latest: its manifest is not on bigfoot")
        continue
    os.rename(src, dst)
    fsync_dir(os.path.dirname(dst))
    done["latest"].append(g)
cutoff = time.time() - 7 * 86400
for dp, dns, fns in os.walk(inc, topdown=False):
    for f in fns:
        p = os.path.join(dp, f)
        try:
            if os.lstat(p).st_mtime < cutoff:
                os.unlink(p)
        except OSError:
            pass
    if dp != inc:
        try:
            os.rmdir(dp)
        except OSError:
            pass
_j.dump(done, sys.stdout)
"""

INIT_BF_PY = r"""
import os, sys
root, stage = ARGS["root"], ARGS["stage"]
if not os.path.isdir(stage):
    sys.exit(f"no {stage}: S0 makes it (sudo install -d -o m -g video -m 2775 {stage})")
for d in (os.path.join(stage, "incoming", ".partial"), os.path.join(stage, "incoming", "meta"),
          os.path.join(stage, "incoming", "latest"), os.path.join(root, "objects"), os.path.join(root, "guests")):
    os.makedirs(d, exist_ok=True)
sj = os.path.join(root, "store.json")
made = False
if os.path.exists(sj):
    role = _j.load(open(sj)).get("role")
    if role != "master":
        sys.exit(f"{sj} says role {role}, not master")
else:
    tmp = os.path.join(stage, "store.json.tmp")
    with open(tmp, "w") as f:
        f.write(_j.dumps({"schema": 1, "role": "master"}) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, sj)
    made = True
_j.dump({"root": root, "made": made}, sys.stdout)
"""


# --- init ----------------------------------------------------------------------------

def cmd_init(cfg: Config, a) -> int:
    if a.bigfoot:
        if a.root or a.role:
            raise Usage("init --bigfoot takes no ROOT or --role")
        bigfoot_gate(cfg, "init --bigfoot", "not writing to it", pool_only=True)
        out = remote_py(cfg.bf_host, INIT_BF_PY, {"root": cfg.bf_root, "stage": cfg.bf_stage})
        say(f"bigfoot: {out['root']} {'made' if out['made'] else 'was there'} (role master)")
        print(out["root"])
        return 0
    if not a.root or a.role not in ("mirror", "cache"):
        raise Usage("init ROOT --role mirror|cache [--cap SIZE --floor SIZE], or init --bigfoot")
    root = Path(a.root)
    sj = root / "store.json"
    meta = {"schema": SCHEMA, "role": a.role}
    if sj.exists():
        old = json.loads(sj.read_text())
        if old.get("role") != a.role:
            raise StoreError(f"{sj} says role {old.get('role')}, not {a.role}")
        meta = old
    for k in ("cap", "floor"):
        v = getattr(a, k)
        if v is not None:
            parse_size(v)
            meta[k] = v
        elif k not in meta and DEFAULTS[a.role][k] is not None:
            meta[k] = DEFAULTS[a.role][k]
    for d in ("objects/.incoming", "guests", "live", "used"):
        (root / d).mkdir(parents=True, exist_ok=True)
    write_atomic(sj, json.dumps(meta, sort_keys=True) + "\n")
    say(f"{root}: role {a.role}" + "".join(f", {k} {meta[k]}" for k in ("cap", "floor") if meta.get(k)))
    print(root)
    return 0


# --- publish -------------------------------------------------------------------------

def guest_disks(gdir: Path) -> list[tuple[str, str]]:
    """[(name, role)]: vm-update's Guest rule, the one boot *.qcow2 (not vm-run.*
    nor .vm-update.*) and the --disk names in vm-run-options."""
    opts_file = gdir / "vm-run-options"
    options = shlex.split(opts_file.read_text()) if opts_file.exists() else []
    extra = [options[i + 1] for i, o in enumerate(options[:-1]) if o == "--disk"]
    for e in extra:
        if os.path.isabs(e) or ".." in Path(e).parts:
            raise StoreError(f"{gdir}: vm-run-options' --disk {e} is not a name in the guest's folder")
        if not (gdir / e).is_file():
            raise StoreError(f"{gdir}: no {e}, which vm-run-options names")
    main = sorted(p.name for p in gdir.glob("*.qcow2")
                  if p.name not in extra and p.name[0] != "." and not p.name.startswith("vm-run."))
    if len(main) != 1:
        raise StoreError(f"{gdir} should have one boot image besides vm-run-options' --disk ones, has {main}")
    return [(main[0], "boot")] + [(e, "disk") for e in extra]


def sidecar_files(gdir: Path, disks: set[str]) -> list[str]:
    """Every regular file to store, relative to GDIR (the denylist of section 1.8)."""
    out = []
    for dp, dns, fns in os.walk(gdir):
        rel_dir = os.path.relpath(dp, gdir)
        keep = []
        for d in sorted(dns):
            rel = os.path.normpath(os.path.join(rel_dir, d))
            if os.path.islink(os.path.join(dp, d)):
                say(f"  skip {rel}/: a symlink")
            elif any(fnmatch.fnmatch(d, g) for g in SKIP_GLOBS):
                say(f"  skip {rel}/")
            else:
                keep.append(d)
        dns[:] = keep
        for f in sorted(fns):
            rel = os.path.normpath(os.path.join(rel_dir, f))
            p = os.path.join(dp, f)
            if rel in disks or rel == "image-state":
                continue
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                say(f"  skip {rel}: a symlink")
            elif not stat.S_ISREG(st.st_mode):
                say(f"  skip {rel}: not a regular file")
            elif any(fnmatch.fnmatch(f, g) for g in SKIP_GLOBS):
                say(f"  skip {rel}")
            elif f.endswith(".qcow2"):
                say(f"  skip {rel}: not one of the guest's disks")
            elif st.st_size > BIG_SIDECAR:
                say(f"  skip {rel}: over 64 MiB")
            else:
                out.append(rel)
    return out


def read_state(gdir: Path) -> dict | None:
    p = gdir / "image-state"
    if not p.exists():
        return None
    try:
        st = json.loads(p.read_text())
    except ValueError:
        raise StoreError(f"{p} isn't valid JSON")
    if not HEX64.fullmatch(str(st.get("generation"))) or not isinstance(st.get("updates"), int) \
            or not isinstance(st.get("sha256"), dict):
        raise StoreError(f"{p} lacks a generation, updates or sha256")
    return st


def stat_key(p: Path) -> tuple:
    st = os.stat(p)
    return st.st_size, st.st_mtime_ns, st.st_ino


def testvm_commit() -> str | None:
    try:
        return (TESTVM / ".backotter-commit").read_text().split()[0]
    except (OSError, IndexError):
        pass
    r = subprocess.run(["git", "-C", str(TESTVM), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def latest_rule(cur: tuple[str, dict | None] | None, new: dict, set_latest: bool) -> tuple[bool, str]:
    """Section 1.6: whether NEW moves latest from CUR (its VID and manifest), and why."""
    if set_latest:
        return True, "--set-latest"
    if cur is None:
        return True, "the first version"
    vid, old = cur
    if old is None:
        return False, f"latest names {vid}, whose manifest isn't here: --set-latest moves it"
    if new["version"][0] == "i":
        return False, f"{new['version']} is a rebuild (no image-state): latest stays {vid}; --set-latest moves it"
    if vid[0] == "g":
        if new["updates"] > (old.get("updates") or 0):
            return True, f"update {new['updates']} over {old.get('updates')}"
        return False, (f"{new['version']} is update {new['updates']}, latest {vid} is update {old.get('updates')}: "
                       f"latest stays; --set-latest moves it")
    shas = {d["name"]: d["chain"][-1] for d in new["disks"]}
    if shas == {d["name"]: d["chain"][-1] for d in old.get("disks", [])}:
        return True, f"vm-update's first state of {vid}'s disks"
    return False, f"{new['version']}'s disks differ from {vid}'s: latest stays; --set-latest moves it"


def crash_point(name: str) -> None:
    """Tests only: VM_STORE_TEST_CRASH=NAME dies here, as a crash would."""
    if os.environ.get("VM_STORE_TEST_CRASH") == name:
        os._exit(70)


def commit_version(store: Store, guest: str, vid: str, m: dict, staged: list[tuple[Path, str]],
                   set_latest: bool) -> tuple[bool, str]:
    """Section 2.4 step 9, under .publish.lock: objects, then the manifest, then latest.
    Returns (published, what happened)."""
    with flocked(store.root / ".publish.lock"):
        vd = store.vdir(guest)
        if (vd / f"{vid}.json.sha256").exists():
            # Before installing anything: an i VID names only the disks, so a re-publish
            # after a sidecar edit would otherwise leave objects no manifest names.
            for tmp, _name in staged:
                with contextlib.suppress(FileNotFoundError):
                    tmp.unlink()
            return False, "already published; " + relatest(store, guest, vid, set_latest)
        for tmp, name in staged:
            dst = store.obj(name)
            if dst.exists():
                tmp.unlink()                   # dedupe: the first bytes stay
                continue
            fsync_file(tmp)                    # its data on disk before the manifest names it
            os.chmod(tmp, 0o444 if name.endswith(".qcow2") else 0o400)
            os.rename(tmp, dst)
        fsync_dir(store.objects)
        crash_point("after-objects")
        vd.mkdir(parents=True, exist_ok=True)
        cur = store.latest(guest)
        cur_m = (store.manifest(guest, cur[0]) or (None,))[0] if cur else None
        move, why = latest_rule((cur[0], cur_m) if cur else None, m, set_latest)
        m["parent"] = cur[0] if move and cur else None
        data = manifest_bytes(m)
        sha = sha256_bytes(data)
        write_atomic(vd / f"{vid}.json", data, 0o444)
        write_atomic(vd / f"{vid}.json.sha256", f"{sha}  {vid}.json\n", 0o444)
        crash_point("after-manifest")
        if move:
            write_atomic(store.guests / guest / "latest", f"{vid} {sha}\n")
            return True, f"latest is now {vid} ({why})"
        return True, f"latest stays {cur[0]}: {why}"


def relatest(store: Store, guest: str, vid: str, set_latest: bool) -> str:
    """For a version published already, under .publish.lock: apply section 1.6 again.
    The rule gives what it gave at publish, so this only finishes a publish that
    died between its manifest and latest (or follows --set-latest)."""
    got = store.manifest(guest, vid)
    if not got:
        raise StoreError(f"{guest}@{vid}: its manifest doesn't match its .sha256 (vm-store verify)")
    cur = store.latest(guest)
    if cur and cur[0] == vid:
        return f"latest is {vid}"
    cur_m = (store.manifest(guest, cur[0]) or (None,))[0] if cur else None
    move, why = latest_rule((cur[0], cur_m) if cur else None, got[0], set_latest)
    if not move:
        return f"latest stays {cur[0]}: {why}"
    write_atomic(store.guests / guest / "latest", f"{vid} {got[1]}\n")
    return f"latest is now {vid} ({why})"


def publish_room(store: Store, guest: str, need: int, reflinked: bool = False) -> None:
    """Section 2.4's floor: the copies (NEED bytes) may not take free space below the
    floor.  REFLINKED (VM_STORE_FLOOR_REFLINK=1 and every disk reflinks): they write
    nothing new, so they pass even when free is below it already, with a warning."""
    free = store.free()
    if reflinked:
        if free < store.floor:
            say(f"{guest}: {gib(free)} free on {store.root} is below the floor of {gib(store.floor)}; "
                f"the reflinked copies take no new space (VM_STORE_FLOOR_REFLINK=1)")
        return
    if free - need < store.floor:
        raise NoRoom(f"{guest}: {gib(need)} more would leave {gib(free - need)} free on {store.root}, "
                     f"below the floor of {gib(store.floor)}")


def publish_one(cfg: Config, store: Store, guest: str, gdir: Path, a, follow: bool = False) -> int:
    """FOLLOW (publish --changed --stateless): latest follows vms/GUEST, as --set-latest."""
    if not GUEST_RE.fullmatch(guest):
        raise Usage(f"not a guest name: {guest!r}")
    if not gdir.is_dir():
        raise StoreError(f"no such guest folder: {gdir}")
    state = read_state(gdir)
    if a.expect_generation and (not state or not state["generation"].startswith(a.expect_generation)):
        raise StoreError(f"{guest}: generation {(state or {}).get('generation')} is not {a.expect_generation}")
    if state and (store.vdir(guest) / f"{vid_of_generation(state['generation'])}.json.sha256").exists():
        vid = vid_of_generation(state["generation"])
        if a.dry_run:
            say(f"{guest}: {vid} already published")
            return 0
        with flocked(store.root / ".publish.lock"):
            say(f"{guest}: {vid} already published; {relatest(store, guest, vid, a.set_latest or follow)}")
        print(f"{guest} {vid}")
        return 0
    disks = guest_disks(gdir)
    full = sum(allocated(gdir / n) for n, _ in disks)
    # Opt-in (VM_STORE_FLOOR_REFLINK=1): a reflinked copy shares the guest's blocks and
    # writes none of its own, so it needs no new space now.  vm-update's next merge into
    # vms/G unshares what it rewrites, and nothing checks that space against the floor.
    reflink = (cfg.floor_reflink and cfg.copy == "auto"
               and all(reflink_ok(os.path.realpath(gdir / n), store.incoming) for n, _ in disks))
    need = 0 if reflink else full
    publish_room(store, guest, need, reflinked=reflink)
    if a.dry_run:
        say(f"{guest}: would publish {', '.join(n for n, _ in disks)} ({gib(full)} allocated, {gib(need)} new"
            + (", reflinked" if reflink else "") + ")"
            + (f" as {vid_of_generation(state['generation'])}" if state else ""))
        sidecar_files(gdir, {n for n, _ in disks})
        return 0
    staged: list[tuple[Path, str]] = []
    tmps: list[Path] = []

    def stage_tmp() -> Path:
        p = store.incoming / f"tmp.{secrets.token_hex(8)}"
        tmps.append(p)
        return p

    try:
        fleet = cfg.vms / FLEET_LOCK
        lock_cm = (flocked(fleet, block=False, busy="a vm-update round or update is running (the fleet lock is held)")
                   if cfg.vms.is_dir() else contextlib.nullcontext())
        with lock_cm:
            state = read_state(gdir)           # again, now that no update can change it
            if a.expect_generation and (not state or not state["generation"].startswith(a.expect_generation)):
                raise StoreError(f"{guest}: generation {(state or {}).get('generation')} is not {a.expect_generation}")
            odd = [p.name for p in gdir.iterdir() if p.name == ".vm-update.pending"
                   or fnmatch.fnmatch(p.name, ".vm-update.*.qcow2") or fnmatch.fnmatch(p.name, "*.merging*")]
            if odd:
                raise StoreError(f"{guest}: an update is unfinished here ({', '.join(sorted(odd))}): vm-update --finish")
            disks = guest_disks(gdir)
            infos = {}
            for name, _ in disks:
                info = disk_info(gdir / name)
                if info["format"] != "qcow2":
                    raise StoreError(f"{guest}: {name} is {info['format']}, not qcow2")
                if info["backing"]:
                    raise StoreError(f"{guest}: {name} has a backing file ({info['backing']})")
                if info["corrupt"]:
                    raise StoreError(f"{guest}: {name} is marked corrupt")
                infos[name] = info
            fences = []
            try:
                for name, _ in disks:
                    fences.append(Fence(Path(os.path.realpath(gdir / name))))
                before, copies, how = {}, {}, set()
                for name, _ in disks:
                    src = Path(os.path.realpath(gdir / name))
                    before[name] = stat_key(src)
                    copies[name] = stage_tmp()
                    t = time.monotonic()
                    if reflink:
                        try:
                            how.add(copy_file(src, copies[name], "reflink"))
                        except ReflinkFailed as e:
                            # The room check counted it as no new space: a plain copy is checked again.
                            say(f"{guest}: {e}; copying it instead")
                            publish_room(store, guest, allocated(src))
                            how.add(copy_file(src, copies[name], "plain"))
                    else:
                        how.add(copy_file(src, copies[name], cfg.copy))
                    say(f"{guest}: copied {name} ({'/'.join(sorted(how))}, {time.monotonic() - t:.1f} s)")
            finally:
                for f in fences:
                    f.close()
            # Sidecars too while the fleet lock holds: an update can't change them under us.
            side_copies = {}
            for rel in sidecar_files(gdir, {n for n, _ in disks}):
                side_copies[rel] = (stage_tmp(), os.stat(gdir / rel).st_mode & 0o7777)
                shutil.copyfile(gdir / rel, side_copies[rel][0])
        for name, _ in disks:
            if stat_key(Path(os.path.realpath(gdir / name))) != before[name]:
                raise StoreError(f"{guest}: {name} changed during the copy; nothing published")
        sums = {}
        for name, _ in disks:
            sums[name] = sha256_file(copies[name])
            if state and state["sha256"].get(name) != sums[name]:
                raise StoreError(f"{guest}: {name} changed since vm-update recorded it in image-state "
                                 f"({sums[name][:12]}, image-state says {str(state['sha256'].get(name))[:12]})")
            staged.append((copies[name], f"sha256-{sums[name]}.qcow2"))
        files = {}
        for rel, (tmp, mode) in side_copies.items():
            h = sha256_file(tmp)
            files[rel] = {"object": f"sha256-{h}.file", "mode": f"{mode:04o}", "size": os.path.getsize(tmp)}
            staged.append((tmp, f"sha256-{h}.file"))
        vid = vid_of_generation(state["generation"]) if state else vid_of_sums(sums)
        m = {"schema": SCHEMA, "guest": guest, "version": vid,
             "generation": state["generation"] if state else None,
             "updates": state["updates"] if state else None, "parent": None, "published": utc_now(),
             "publisher": {"host": os.uname().nodename.split(".")[0], "from": str(gdir.resolve()),
                           "testvm_commit": testvm_commit(), "tool": TOOL,
                           "copy": "reflink" if how == {"reflink"} else "plain"},
             "note": a.note or "",
             "disks": [{"name": n, "role": r, "virtual_size": infos[n]["virtual_size"],
                        "file_size": os.path.getsize(copies[n]), "chain": [f"sha256-{sums[n]}.qcow2"],
                        "snapshots": infos[n]["snapshots"]} for n, r in disks],
             "files": files}
        done, what = commit_version(store, guest, vid, m, staged, a.set_latest or follow)
        tmps.clear()
        say(f"{guest}: {vid} {'published; ' + what if done else what}")
        print(f"{guest} {vid}")
        return 0
    finally:
        for p in tmps:
            with contextlib.suppress(FileNotFoundError):
                p.unlink()


def cmd_publish(cfg: Config, a) -> int:
    store = Store(cfg.root, cfg)
    if store.role != "mirror":
        raise StoreError(f"publish runs on the mirror (Chopin); {store.root} is a {store.role}")
    if a.changed and (a.guests or a.from_dir):
        raise Usage("--changed takes no GUEST or --from")
    if a.from_dir and len(a.guests) > 1:
        raise Usage("--from takes one GUEST")
    stateless = [g for v in a.stateless for g in v.replace(",", " ").split()]
    if stateless and not a.changed:
        raise Usage("--stateless goes with --changed (publish GUEST publishes a state-less guest by name)")
    for g in stateless:
        if not GUEST_RE.fullmatch(g):
            raise Usage(f"not a guest name: {g!r}")
    rcs = []
    if a.changed:
        todo, extra = [], []
        for g in sorted(set(stateless)):
            if not (cfg.vms / g).is_dir():
                say(f"{g}: --stateless names it, but there is no {cfg.vms / g}")
                rcs.append(1)
        for gdir in sorted(cfg.vms.iterdir()) if cfg.vms.is_dir() else []:
            if gdir.is_dir() and not (gdir / "image-state").exists() and gdir.name in stateless:
                # Opted in: its i VID is known only once its disks are hashed, so it is
                # always copied, and dropped at commit when that version is there already.
                try:
                    size = sum(allocated(gdir / n) for n, _ in guest_disks(gdir))
                except StoreError as e:
                    say(f"{gdir.name}: {e}")
                    rcs.append(e.rc)
                    continue
                todo.append((size, gdir.name, gdir, True))
                extra.append(gdir.name)
                continue
            if not gdir.is_dir() or not (gdir / "image-state").exists():
                continue
            try:
                st = read_state(gdir)
                vid = vid_of_generation(st["generation"])
                if (store.vdir(gdir.name) / f"{vid}.json.sha256").exists():
                    continue
                size = sum(allocated(gdir / n) for n, _ in guest_disks(gdir))
            except StoreError as e:
                say(f"{gdir.name}: {e}")
                rcs.append(e.rc)               # a guest that can't be judged is a failure, not "unchanged"
                continue
            todo.append((size, gdir.name, gdir, False))
        jobs = [(g, d, f) for _, g, d, f in sorted(todo)]
        changed = [g for g, _, f in jobs if not f]
        say(f"{len(changed)} guest(s) changed: {' '.join(changed) or 'none'}"
            + (f"; state-less, to hash: {' '.join(sorted(extra))}" if extra else ""))
    elif a.from_dir:
        d = Path(a.from_dir)
        jobs = [(a.guests[0] if a.guests else d.resolve().name, d, False)]
    elif a.guests:
        jobs = [(g, cfg.vms / g, False) for g in a.guests]
    else:
        raise Usage("publish GUEST... | --changed")
    for guest, gdir, follow in jobs:
        try:
            rcs.append(publish_one(cfg, store, guest, gdir, a, follow=follow))
        except StoreError as e:
            say(f"{guest}: {e}")
            rcs.append(e.rc)
    rc = next((c for c in (1, 4, 3, 2) if c in rcs), 0)
    if a.push and not a.dry_run:
        try:
            push(cfg, store, now=False, dry=False)
        except Busy as e:
            say(f"push not done: {e}")
        except StoreError as e:
            say(f"push failed (the publish stands; status shows what is unpushed): {e}")
            rc = rc or e.rc
    return rc


# --- Bigfoot's health ----------------------------------------------------------------

# Bigfoot's side of the probe, run by sh with POOL, SINCE (a journalctl --since, or 0:
# no kernel log check), the reset ERE and LOCK (xfer.lock's path, or -: not checked).
# Every step runs whatever the one before did, under its own timeout, and its section
# ends with its exit status:
#     @@ list / @@ status / @@ journal / @@ lock  ...output...  @@ rc N        then @@ end
# The kernel log is read by _TRANSPORT=kernel, not -k (which means this boot only), so
# resets before a reboot count while the journal is persistent.  The lock is tried only
# on an ONLINE pool (a suspended one hangs the open).
BF_HEALTH_SH = r"""
pool=$1 since=$2 re=$3 lock=$4
t() { if command -v timeout >/dev/null 2>&1; then timeout -k 2 STEP "$@"; else "$@"; fi; }
echo "@@ list"
h=$(t zpool list -H -o health "$pool" 2>&1)
rc=$?
printf '%s\n' "$h"
echo "@@ rc $rc"
echo "@@ status"
t zpool status "$pool" 2>&1
echo "@@ rc $?"
if [ "$since" != 0 ]; then
    echo "@@ journal"
    out=$(t sudo -n journalctl _TRANSPORT=kernel --since="$since" --no-pager -q 2>&1)
    rc=$?
    if [ "$rc" -eq 0 ]; then
        printf '%s\n' "$out" | grep -E -e "$re"
    else
        printf '%s\n' "$out" | head -n 3
    fi
    echo "@@ rc $rc"
fi
if [ "$lock" != - ] && [ "$h" = ONLINE ]; then
    echo "@@ lock"
    t flock -n -E BUSY "$lock" true 2>&1
    echo "@@ rc $?"
fi
echo "@@ end"
""".replace("STEP", str(BF_STEP_TIMEOUT)).replace("BUSY", str(XFER_BUSY))


def step_status(rc: int | None) -> str:
    """A probe step's exit status in words: 124/137 are timeout's (the step's own limit)."""
    return "timed out" if rc in (124, 137) else f"exit status {rc}"


def parse_health(text: str, pool: str, window: str | None, pool_only: bool = False,
                 cut: bool = False) -> tuple[bool, list[str], dict]:
    """BF_HEALTH_SH's output read: (ok, reasons, detail).  Not ok if the pool isn't
    ONLINE; unless POOL_ONLY, also while a scrub or resilver runs, or with a USB reset
    line in the kernel log of the last WINDOW (None: not checked).  A kernel log sudo
    can't read is a warning in detail, not a reason.  CUT: the output stops where the
    probe ran out of time; a step it didn't finish gives no reason (the caller names the
    timeout), what came before still counts."""
    sections: dict[str, dict] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("@@ "):
            word = line[3:].split()
            if word and word[0] == "rc" and cur:
                sections[cur]["rc"] = int(word[1]) if len(word) > 1 and word[1].isdigit() else None
                sections[cur]["done"] = True
                cur = None
            elif word:
                cur = word[0]
                sections[cur] = {"lines": [], "rc": None, "done": False}
            continue
        if cur:
            sections[cur]["lines"].append(line)
    reasons: list[str] = []
    detail: dict = {"pool": pool, "health": None, "scan": None, "resets": None, "last_reset": None,
                    "window": window, "xfer_lock": None, "warnings": []}
    lst = sections.get("list")
    words = [w for w in lst["lines"] if w.strip()] if lst else []
    if lst and lst["rc"] == 0 and len(words) == 1 and re.fullmatch(r"[A-Z]+", words[0].strip()):
        detail["health"] = words[0].strip()
        if detail["health"] != "ONLINE":
            reasons.append(f"pool {pool} is {detail['health']}")
    elif not cut or (lst and lst["done"]):
        why = (words[0].strip() if words else "") or ("no answer" if not lst else step_status(lst["rc"]))
        reasons.append(f"zpool list {pool} failed: {why}")
    st = sections.get("status")
    if st:
        status = "\n".join(st["lines"])
        m = re.search(r"\b(scrub|resilver) in progress", status)
        scan = re.search(r"^\s*scan:\s*(.*)$", status, re.M)
        detail["scan"] = scan[1].strip() if scan else None
        if m and not pool_only:
            reasons.append(f"a {m[1]} is in progress on {pool}")
        if st["done"] and st["rc"] != 0:
            detail["warnings"].append(f"zpool status {pool}: {step_status(st['rc'])}")
    if window is not None:
        jr = sections.get("journal")
        if not jr or not jr["done"]:
            if not cut:
                detail["warnings"].append("the kernel log was not checked (the probe stopped before it)")
        elif jr["rc"] != 0:
            first = next((x.strip() for x in jr["lines"] if x.strip()), step_status(jr["rc"]))
            detail["warnings"].append(f"USB resets not checked: sudo -n journalctl failed: {first}")
        else:
            hits = [x.strip() for x in jr["lines"] if re.search(USB_RESET_ERE, x)]
            detail["resets"] = len(hits)
            detail["last_reset"] = hits[-1] if hits else None
            if hits and not pool_only:
                reasons.append(f"{len(hits)} USB reset/disconnect line(s) in Bigfoot's kernel log in the last "
                               f"{window}; last: {hits[-1]}")
    lk = sections.get("lock")
    if lk and lk["done"]:
        detail["xfer_lock"] = {0: "free", XFER_BUSY: "busy"}.get(lk["rc"])
    if "end" not in sections and not cut:
        detail["warnings"].append("the probe didn't finish")
    return not reasons, reasons, detail


def drain(f, buf: list[bytes]) -> None:
    """F's lines into BUF until EOF (a reader thread's body); a closed F ends it."""
    with contextlib.suppress(OSError, ValueError):
        for line in iter(f.readline, b""):
            buf.append(line)


def bigfoot_health(cfg: Config, pool_only: bool = False, lock: bool = False) -> tuple[bool, list[str], dict]:
    """Bigfoot's health, in one ssh round trip under BF_PROBE_TIMEOUT: (ok, reasons,
    detail) by parse_health.  No answer in time is unhealthy, with whatever the probe
    said before it (a SUSPENDED pool, say, before zpool status hung).  POOL_ONLY: only
    the pool's health counts (readers), and the kernel log isn't read.  LOCK: also
    whether xfer.lock is free (detail's xfer_lock: free, busy, or None: not known)."""
    window = None if pool_only or not cfg.bf_reset_secs else cfg.bf_reset_window
    args = [cfg.bf_pool, f"-{cfg.bf_reset_secs}s" if window else "0", USB_RESET_ERE,
            f"{cfg.bf_stage}/xfer.lock" if lock else "-"]
    cmd = (["ssh", *SSH_OPTS, cfg.bf_host, "sh -s -- " + " ".join(map(shlex.quote, args))] if cfg.bf_host
           else ["sh", "-s", "--", *args])
    host = cfg.bf_host or "here"
    detail = {"host": host, "pool": cfg.bf_pool, "health": None, "window": window, "warnings": []}
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    except OSError as e:
        return False, [f"can't run {cmd[0]}: {e}"], detail
    out: list[bytes] = []
    err: list[bytes] = []
    readers = [threading.Thread(target=drain, args=(f, b), daemon=True) for f, b in ((p.stdout, out), (p.stderr, err))]
    timed_out = False
    try:
        for r in readers:
            r.start()
        with contextlib.suppress(OSError):  # it ended before reading it all
            p.stdin.write(BF_HEALTH_SH.encode())
            p.stdin.close()
        deadline = time.monotonic() + BF_PROBE_TIMEOUT
        for r in readers:
            r.join(max(0.0, deadline - time.monotonic()))
        timed_out = any(r.is_alive() for r in readers)
        if not timed_out:
            p.wait(max(1.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
    except BaseException:                     # a signal, ^C: never leave the ssh behind
        stop_probe(p, readers)
        raise
    if timed_out:
        stop_probe(p, readers)
        text = b"".join(out).decode(errors="replace")
        _, reasons, d = parse_health(text, cfg.bf_pool, window, pool_only, cut=True)
        d["host"] = host
        return False, [f"Bigfoot did not answer within {BF_PROBE_TIMEOUT}s", *reasons], d
    for f in (p.stdout, p.stderr):              # its readers saw EOF
        with contextlib.suppress(OSError):
            f.close()
    text = b"".join(out).decode(errors="replace")
    if "@@ " not in text:
        e = b"".join(err).decode(errors="replace").strip()
        why = e.splitlines()[-1] if e else f"exit status {p.returncode}"
        return False, [f"{host}: no answer from the probe: {why}"], detail
    ok, reasons, d = parse_health(text, cfg.bf_pool, window, pool_only)
    d["host"] = host
    return ok, reasons, d


def stop_probe(p: subprocess.Popen, readers: list[threading.Thread]) -> None:
    """Kill the probe's process group (a step may still hold its pipes) and let its readers end."""
    with contextlib.suppress(OSError):
        os.killpg(p.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        p.wait(5)
    for r in readers:
        r.join(2)
    if not any(r.is_alive() for r in readers):  # closing a pipe a reader is in could block
        for f in (p.stdin, p.stdout, p.stderr):
            with contextlib.suppress(OSError):
                f.close()


def bigfoot_verdict(cfg: Config, lock: bool = False) -> tuple[bool, str, dict]:
    """The full probe as one phrase for an error message: (ok, text, detail).  LOCK
    (after a failed rsync): also say if xfer.lock is busy, another transfer holding it
    past this one's lock wait."""
    ok, reasons, detail = bigfoot_health(cfg, lock=lock)
    busy = "; Bigfoot's xfer.lock is still held (another transfer, or a stuck one)" if detail.get("xfer_lock") == "busy" else ""
    if ok:
        return ok, f"Bigfoot looks healthy (pool {cfg.bf_pool} {detail.get('health')}){busy}", detail
    return ok, f"Bigfoot is not healthy: {'; '.join(reasons)}{busy}", detail


def bigfoot_gate(cfg: Config, what: str, refuse: str, pool_only: bool = False, dry: bool = False) -> dict | None:
    """Unhealthy("WHAT: Bigfoot is not healthy: ...; REFUSE") unless the probe passes
    (POOL_ONLY: the pool is ONLINE; readers).  DRY: say the verdict, never refuse.
    The probe's detail, or None if VM_STORE_BF_GATE=0 skipped it (loudly)."""
    if not cfg.bf_gate:
        say(f"WARNING: VM_STORE_BF_GATE=0: {what} without checking Bigfoot's health")
        return None
    ok, reasons, detail = bigfoot_health(cfg, pool_only=pool_only)
    for w in detail.get("warnings", []):
        say(f"{what}: bigfoot: warning: {w}")
    if ok:
        if dry:
            say(f"{what}: Bigfoot is healthy (pool {cfg.bf_pool} {detail.get('health')})")
        return detail
    msg = f"{what}: Bigfoot is not healthy: {'; '.join(reasons)}"
    if dry:
        say(f"{msg}; a real run would refuse (--dry-run)")
        return detail
    raise Unhealthy(f"{msg}; {refuse}")


def xfer_rsync_path(cfg: Config) -> str:
    """The --rsync-path of an rsync with Bigfoot: under its xfer.lock, waited for less
    long than the rsync's --timeout (which counts that silent wait), then XFER_BUSY."""
    t = int(cfg.rsync_timeout)
    return (f"--rsync-path=flock -w {max(1, t - min(30, t // 2))} -E {XFER_BUSY} {cfg.bf_stage}/xfer.lock "
            "nice -n19 rsync")


def run_xfer(cmd: list[str]) -> int:
    """run_child an rsync with Bigfoot, again while its xfer.lock stays busy (XFER_BUSY),
    up to XFER_LOCK_WAIT in all: the queue of one stream at a time."""
    t0 = time.monotonic()
    while True:
        rc = run_child(cmd)
        if rc != XFER_BUSY or time.monotonic() - t0 >= XFER_LOCK_WAIT:
            return rc
        say("bigfoot: xfer.lock is busy (another transfer); waiting for it again")


def cmd_bigfoot_health(cfg: Config, a) -> int:
    try:
        store = Store(cfg.root, cfg)
    except StoreError as e:
        raise Usage(f"bigfoot-health runs on the mirror (Chopin), the only host that talks to Bigfoot: {e}")
    if store.role != "mirror":
        raise Usage(f"bigfoot-health runs on the mirror (Chopin), the only host that talks to Bigfoot; "
                    f"{store.root} is a {store.role}")
    ok, reasons, detail = bigfoot_health(cfg, pool_only=a.pool_only)
    if a.json:
        print(json.dumps({"ok": ok, "reasons": reasons, **detail}, indent=1))
    else:
        for w in detail.get("warnings", []):
            say(f"bigfoot: warning: {w}")
        if ok:
            resets = (f", no USB resets in {detail['window']}" if detail.get("resets") == 0 else
                      ", USB resets not checked" if not a.pool_only else "")
            print(f"healthy: pool {cfg.bf_pool} {detail.get('health')}" + resets)
        else:
            print("unhealthy: " + "; ".join(reasons))
    return 0 if ok else 1


# --- push ----------------------------------------------------------------------------

def bigfoot_listing(cfg: Config, full: bool = False) -> dict:
    out = remote_py(cfg.bf_host, LIST_PY, {"root": cfg.bf_root, "full": full})
    if (out.get("store") or {}).get("role") != "master":
        raise StoreError(f"bigfoot: {cfg.bf_root} is not a master store (vm-store init --bigfoot)")
    return out


def local_listing(store: Store) -> dict:
    """What push may send: objects, and only complete manifests (both files, the .json
    matching its .sha256: a lone .json is an unfinished publish), each latest."""
    out = {"objects": {}, "meta": {}, "sha256": {}, "json_sha": {}, "latest": {}}
    for n in store.object_names():
        out["objects"][n] = os.path.getsize(store.obj(n))
    for g in sorted(p.name for p in store.guests.iterdir() if p.is_dir()):
        lp = store.guests / g / "latest"
        if lp.is_file():
            out["latest"][g] = lp.read_text()
    for g, vid, _m, sha in store.manifests():
        jr = f"{g}/versions/{vid}.json"
        sr = jr + ".sha256"
        out["meta"][jr] = (store.vdir(g) / f"{vid}.json").stat().st_size
        out["meta"][sr] = (store.vdir(g) / f"{vid}.json.sha256").stat().st_size
        out["sha256"][sr] = (store.vdir(g) / f"{vid}.json.sha256").read_text()
        out["json_sha"][jr] = sha
    return out


def first_word(text: str | None) -> str | None:
    parts = (text or "").split()
    return parts[0] if parts else None


def unpushed(here: dict, there: dict) -> dict:
    """What Bigfoot lacks.  A file there that differs from ours is not sent again
    (it is never replaced): differs() reports it."""
    meta = []
    for jr, sha in here["json_sha"].items():
        sr = jr + ".sha256"
        tj, ts = there.get("json_sha", {}).get(jr), first_word(there["sha256"].get(sr))
        if (tj is not None and tj != sha) or (ts is not None and ts != sha):
            continue
        if tj is None or ts is None:
            meta += [jr, sr]                   # a manifest goes with its .sha256
    return {"objects": sorted(n for n in here["objects"] if n not in there["objects"]),
            "meta": sorted(meta),
            "latest": sorted(g for g, t in here["latest"].items() if there["latest"].get(g) != t)}


def differs(here: dict, there: dict, keep=lambda rel: True) -> list[str]:
    """Files Bigfoot has with other bytes than ours (by size for objects, by sha256
    for manifests), and manifests there that don't match their own .sha256."""
    out = [f"objects/{n}: {there['objects'][n]} bytes on bigfoot, {s} here"
           for n, s in sorted(here["objects"].items()) if n in there["objects"] and there["objects"][n] != s]
    flagged = set()
    for jr, sha in sorted(here["json_sha"].items()):
        if not keep(jr):
            continue
        sr = jr + ".sha256"
        tj, ts = there.get("json_sha", {}).get(jr), first_word(there["sha256"].get(sr))
        if tj is not None and tj != sha:
            out.append(f"guests/{jr}: differs on bigfoot")
            flagged.add(jr)
        elif ts is not None and ts != sha:
            out.append(f"guests/{sr}: differs on bigfoot")
            flagged.add(jr)
    for jr, tj in sorted(there.get("json_sha", {}).items()):
        if not keep(jr) or jr in flagged:
            continue
        ts = first_word(there["sha256"].get(jr + ".sha256"))
        if ts is not None and ts != tj:
            out.append(f"guests/{jr}: doesn't match its .sha256 on bigfoot")
        elif ts is None and jr not in here["json_sha"]:
            out.append(f"guests/{jr}: on bigfoot without its .sha256, and not a manifest here")
    return out


def push_rsync(cfg: Config, src_dir: Path, names: list[str], dest: str) -> int:
    with tempfile.NamedTemporaryFile("w", prefix="vm-store-push.", suffix=".list", delete=False) as f:
        f.write("".join(n + "\n" for n in names))
        listing = f.name
    try:
        cmd = ["nice", "-n19", "rsync", "-t", "--chmod=Fa-w", "--fsync", "--no-whole-file", "--partial-dir=.partial",
               f"--bwlimit={cfg.bwlimit}", "--compress", "--compress-choice=zstd", "--compress-level=1",
               xfer_rsync_path(cfg), f"--timeout={cfg.rsync_timeout}"]
        if cfg.bf_host:
            cmd += ["-e", SSH_E]
        target = f"{cfg.bf_host}:{cfg.bf_stage}/incoming/{dest}" if cfg.bf_host else f"{cfg.bf_stage}/incoming/{dest}"
        return run_xfer(cmd + [f"--files-from={listing}", f"{src_dir}/", target])
    finally:
        os.unlink(listing)


def push(cfg: Config, store: Store, now: bool, dry: bool) -> dict:
    if store.role != "mirror":
        raise StoreError(f"push runs on the mirror (Chopin); {store.root} is a {store.role}")
    with flocked(store.root / ".push.lock", block=False, busy="another push is running"):
        if not now and not in_window(cfg.window):
            raise Busy(f"outside VM_STORE_WINDOW ({cfg.window}); --now pushes anyway")
        detail = bigfoot_gate(cfg, "push", "not pushing", dry=dry)   # --now skips the window, never this
        if dry and detail is not None and detail.get("health") != "ONLINE":
            h = detail.get("health")            # a suspended pool would hang the listing there
            why = f"pool {cfg.bf_pool} is {h}" if h else f"no health for pool {cfg.bf_pool}"
            say(f"push: not listing Bigfoot: {why} (--dry-run)")
            return {"objects": [], "meta": [], "latest": []}
        there = bigfoot_listing(cfg)
        here = local_listing(store)
        todo = unpushed(here, there)
        bad = differs(here, there)
        sizes = sum(os.path.getsize(store.obj(n)) for n in todo["objects"])
        say(f"push: {len(todo['objects'])} object(s), {gib(sizes)}; {len(todo['meta'])} manifest file(s); "
            f"{len(todo['latest'])} latest")
        for d in bad:
            say(f"push: {d}: not sent, never replaced there (vm-store verify --where bigfoot)")
        if dry:
            for k in ("objects", "meta", "latest"):
                for n in todo[k]:
                    print(f"{k} {n}")
            for d in bad:
                print(f"differs {d}")
            return todo
        if not any(todo.values()):
            if bad:
                raise StoreError(f"push: {len(bad)} file(s) differ on bigfoot; fix them there by hand")
            return todo
        failed, verdict = [], None
        for what, src, names, dest in (("objects", store.objects, todo["objects"], ""),
                                       ("manifests", store.guests, todo["meta"], "meta/"),
                                       ("latest", store.guests, [f"{g}/latest" for g in todo["latest"]], "latest/")):
            if not names:
                continue
            rc = push_rsync(cfg, src, names, dest)
            if rc == 0:
                continue
            failed.append(f"{what} (exit status {rc})")
            if verdict is None:                # once: 30 is rsync's I/O timeout, 12/35/255 a dropped link
                ok, verdict, _ = bigfoot_verdict(cfg, lock=True)
                msg = f"push: rsync of the {what} failed (exit status {rc}); {verdict}"
                if not ok and cfg.bf_gate:
                    raise StoreError(f"{msg}; stopped (push again once it is healthy)")
                say(msg)
        try:
            done = remote_py(cfg.bf_host, ACCEPT_PY, {"root": cfg.bf_root, "stage": cfg.bf_stage,
                                                      "objects": todo["objects"], "meta": todo["meta"],
                                                      "latest": todo["latest"]})
        except StoreError as e:
            raise StoreError(f"push: bigfoot's accept failed: {e}; {verdict or bigfoot_verdict(cfg)[1]}")
        for s in done["skipped"]:
            say(f"push: bigfoot skipped {s}")
        say(f"push: bigfoot took {len(done['objects'])} object(s), {len(done['meta'])} manifest file(s), "
            f"{len(done['latest'])} latest")
        if failed:
            raise StoreError(f"push: rsync of the {', '.join(failed)} failed ({verdict}); run push again to resume")
        if bad:
            raise StoreError(f"push: {len(bad)} file(s) differ on bigfoot; fix them there by hand")
        if done["skipped"]:
            raise StoreError("push: bigfoot skipped some files; run push again (vm-store verify --where bigfoot)")
        return todo


def cmd_push(cfg: Config, a) -> int:
    push(cfg, Store(cfg.root, cfg), a.now, a.dry_run)
    return 0


# --- ensure and fetch ----------------------------------------------------------------

def resolve_latest(store: Store, guest: str, srcs: list[Source], offline: bool) -> tuple[str, str, bytes | None]:
    """(VID, manifest sha, the source's latest line to cache once its manifest checks out, or None)."""
    if store.role == "mirror" or offline:
        got = store.latest(guest)
        if not got:
            raise StoreError(f"no latest for {guest} here" + (" (--offline)" if offline else ""))
        return got[0], got[1], None
    for s in srcs:
        try:
            data = s.read(f"guests/{guest}/latest")
        except Unreachable as e:
            say(f"{e}")
            continue
        if data is None:
            continue
        vid, sha = parse_latest(data.decode())
        return vid, sha, data
    got = store.latest(guest)
    if not got:
        raise StoreError(f"no source has a latest for {guest}, and none is cached here")
    say(f"warning: no source answered for {guest}; using the cached latest {got[0]}")
    return got[0], got[1], None


def resolve_manifest(store: Store, guest: str, vid: str, want: str | None, srcs: list[Source]) -> tuple[dict, str]:
    cached = store.manifest(guest, vid)
    if cached:
        if want and want != cached[1]:
            raise StoreError(f"store divergence: {guest}@{vid} is {cached[1][:12]} here, latest says {want[:12]}")
        check_manifest(cached[0], guest, vid)
        return cached
    if store.role == "mirror":
        raise StoreError(f"no manifest {guest}@{vid} here")
    errors = []
    for s in srcs:
        try:
            shaf = s.read(f"guests/{guest}/versions/{vid}.json.sha256")
            data = s.read(f"guests/{guest}/versions/{vid}.json") if shaf else None
        except Unreachable as e:
            say(f"{e}")
            continue
        if not shaf or data is None:
            continue
        stated, sha = shaf.decode().split()[0], sha256_bytes(data)
        if sha != stated or (want and sha != want):
            errors.append(f"{s.name}: manifest sha mismatch ({sha[:12]}, its .sha256 says {stated[:12]}"
                          + (f", latest says {want[:12]})" if want else ")"))
            say(errors[-1])
            continue
        m = json.loads(data)
        check_manifest(m, guest, vid)
        vd = store.vdir(guest)
        vd.mkdir(parents=True, exist_ok=True)
        write_atomic(vd / f"{vid}.json", data, 0o444)
        write_atomic(vd / f"{vid}.json.sha256", shaf, 0o444)
        return m, sha
    raise StoreError(f"no good manifest for {guest}@{vid} from any source" + (f": {'; '.join(errors)}" if errors else ""))


def fetch_object(store: Store, name: str, srcs: list[Source]) -> None:
    """Section 2.6 step 4: rsync into .incoming, check its sha, then rename into objects/."""
    hexd = OBJ_RE.fullmatch(name)[1]
    got = store.incoming / name
    for s in srcs:
        if s.down:
            continue
        try:
            s.check()
        except Unreachable as e:
            say(f"{e}")
            continue
        say(f"fetching {name[:19]}… from {s.name}")
        rc = (run_xfer if s.bigfoot else run_child)(s.rsync_cmd(f"objects/{name}", f"{store.incoming}/"))
        if rc != 0 or not got.is_file():
            verdict = s.after_failure() if rc != 0 else ""
            say(f"{s.name}: rsync of {name} failed (exit status {rc})" + (f"; {verdict}" if verdict else ""))
            continue
        if sha256_file(got) != hexd:
            say(f"{s.name}: {name} arrived with other bytes; trying the next source")
            got.unlink()
            continue
        os.chmod(got, 0o444 if name.endswith(".qcow2") else 0o400)
        os.rename(got, store.obj(name))
        fsync_dir(store.objects)
        return
    raise StoreError(f"no source gave good bytes for {name}: run `vm-store verify` on chopin")


def marker_ok(leaf: Path, sha: str) -> bool:
    try:
        return json.loads((leaf / ".vm-store.json").read_text()).get("manifest_sha256") == sha
    except (OSError, ValueError):
        return False


def materialize(store: Store, guest: str, vid: str, m: dict, sha: str) -> Path:
    """Section 1.7, under lock: live/G/VID/G/, made in a tmp dir and renamed."""
    live = store.live / guest / vid
    leaf = live / guest
    if live.exists():
        if marker_ok(leaf, sha):
            return leaf
        raise StoreError(f"store inconsistency: {live} isn't of manifest {sha[:12]}; it may be in use, "
                         f"so it is never rebuilt over")
    tmp = store.live / guest / f".{vid}.tmp.{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / guest).mkdir(parents=True)
    (tmp / "objects").symlink_to("../../../objects")
    for d in m["disks"]:
        link = tmp / guest / d["name"]
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(live / "objects" / d["chain"][-1], (live / guest / d["name"]).parent))
    for rel, f in m.get("files", {}).items():
        p = tmp / guest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(store.obj(f["object"]), p)
        fsync_file(p)
        os.chmod(p, int(f["mode"], 8))
    state = {"generation": m.get("generation"), "updates": m.get("updates"),
             "sha256": {d["name"]: OBJ_RE.fullmatch(d["chain"][-1])[1] for d in m["disks"]}}
    (tmp / guest / "image-state").write_text(json.dumps(state, indent=1) + "\n")
    fsync_file(tmp / guest / "image-state")
    (tmp / guest / ".vm-store.json").write_text(json.dumps(
        {"guest": guest, "version": vid, "manifest_sha256": sha, "materialized": utc_now()}, indent=1) + "\n")
    fsync_file(tmp / guest / ".vm-store.json")
    # Every file and directory durable before the rename makes it visible: after a power
    # loss an empty marker would be a permanent "store inconsistency".
    for dp, _dns, _fns in os.walk(tmp, topdown=False):
        fsync_dir(dp)
    os.rename(tmp, live)
    fsync_dir(live.parent)
    return leaf


def ensure(cfg: Config, store: Store, ref: str, offline: bool) -> dict:
    if store.role == "master":
        raise StoreError("ensure doesn't run on the master (Bigfoot)")
    guest, vid = parse_ref(ref)
    srcs = [] if store.role == "mirror" or offline else [
        s for s in cfg.sources if not (s.host is None and os.path.realpath(s.path) == os.path.realpath(store.root))]
    want = line = None
    if vid is None:
        vid, want, line = resolve_latest(store, guest, srcs, offline)
    m, sha = resolve_manifest(store, guest, vid, want, srcs)
    lp = store.guests / guest / "latest"

    def keep_latest() -> None:
        # Kept for --offline, and only once the version is whole here: a failed fetch
        # leaves the cached latest naming the last version that is.
        if line is not None and (not lp.exists() or lp.read_bytes() != line):
            write_atomic(lp, line)
    objs = manifest_objects(m)
    if store.role == "cache":
        for o in objs:                         # first: gc keeps the whole version from now on (rule c)
            store.stamp(o)
    leaf = store.live / guest / vid / guest
    result = {"dir": str(leaf), "version": vid, "disks": [d["name"] for d in m["disks"]], "objects": objs}
    if all(store.obj(o).is_file() for o in objs) and marker_ok(leaf, sha):
        keep_latest()
        return result
    with store.lock():
        sizes = object_sizes(m)
        for o in objs:
            if store.obj(o).is_file():
                continue
            if store.role == "mirror":
                raise StoreError(f"{guest}@{vid}: object {o} is missing from the mirror (damage): vm-store verify")
            if offline:
                raise StoreError(f"{guest}@{vid}: object {o} isn't here, and --offline fetches nothing")
            need_room(cfg, store, sizes.get(o, 0), protect=set(objs))
            fetch_object(store, o, srcs)
        materialize(store, guest, vid, m, sha)
        keep_latest()
    return result


def cmd_ensure(cfg: Config, a) -> int:
    r = ensure(cfg, Store(cfg.root, cfg), a.ref, a.offline)
    print(json.dumps(r) if a.json else r["dir"])
    return 0


def cmd_fetch(cfg: Config, a) -> int:
    store = Store(cfg.root, cfg)
    for ref in a.refs:
        r = ensure(cfg, store, ref, offline=False)
        say(f"{ref}: {r['version']} is here")
    return 0


# --- link ----------------------------------------------------------------------------

def link_into(store: Store, p: Path) -> tuple[str, str] | None:
    """(G, VID) when the link P resolves into this store's live/G/VID (dangling or not), else None."""
    rel = os.path.relpath(os.path.realpath(p), os.path.realpath(store.live)).split(os.sep)
    if len(rel) >= 2 and rel[0] not in ("..", ".") and VID_RE.fullmatch(rel[1]):
        return rel[0], rel[1]
    return None


def runs_on(guest: str, target: Path) -> list[str]:
    """What runs on the guest folder TARGET: QEMU programs holding its disk objects (rule
    a's lock test, vm-update's qemu_holds), and a comparison of GUEST (compare.py and
    compare-freebsd.sh hold no lock between their mounts; Linux /proc only)."""
    out = []
    if target.is_dir():
        for q in sorted(target.glob("**/*")):
            if q.is_symlink() and in_store(q) and os.path.isfile(q) and qemu_holds(os.path.realpath(q)):
                out.append(f"a QEMU program has {q.name} open ({os.path.realpath(q)})")
    for d in glob_proc():
        try:
            args = Path(d, "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if int(os.path.basename(d)) != os.getpid() and guest.encode() in args \
                and any(re.search(rb"compare(\.py|-freebsd\.sh)$", x) for x in args[:3]):
            out.append(f"a comparison of {guest} runs (pid {os.path.basename(d)})")
    return out


def glob_proc() -> list[str]:
    try:
        return [os.path.join("/proc", n) for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
        return []


def link_guests(cfg: Config, store: Store) -> list[str]:
    """link --all: each vms/G that is a link into this store (made by link or by hand)."""
    try:
        return sorted(e.name for e in os.scandir(cfg.vms) if e.is_symlink() and not e.name.startswith(".")
                      and (link_into(store, Path(e.path)) or ("", ""))[0] == e.name)
    except (FileNotFoundError, NotADirectoryError):
        return []


def link_one(cfg: Config, store: Store, ref: str, offline: bool, dry: bool) -> str:
    """vms/G -> ROOT/live/G/VID/G (ensure first), by one rename.  Returns the VID."""
    guest, want = parse_ref(ref)
    p = cfg.vms / guest

    def current() -> tuple[str, str] | None:
        if not p.is_symlink():
            if os.path.lexists(p):
                raise StoreError(f"{p} is not a link (a full guest folder): link never replaces it")
            return None
        cur = link_into(store, p)
        if cur is None or cur[0] != guest:
            raise StoreError(f"{p} links to {os.readlink(p)}, not into {store.live}/{guest}: "
                             f"link replaces only its own kind of link (remove it by hand)")
        return cur

    def not_running() -> None:
        busy = runs_on(guest, p) if p.is_symlink() and os.path.isdir(p) else []
        if busy:
            raise Busy(f"{guest}: {busy[0]}" + (f" (and {len(busy) - 1} more)" if len(busy) > 1 else "")
                       + "; link it later")

    cur = current()                            # before fetching anything
    if dry:
        if want is None:
            srcs = [] if offline else [s for s in cfg.sources if not (s.host is None and os.path.realpath(s.path)
                                                                       == os.path.realpath(store.root))]
            want = resolve_latest(store, guest, srcs, offline)[0]
        if cur and cur[1] == want and os.path.isdir(p):
            say(f"{guest}: already links to {want}")
        else:
            not_running()
            say(f"{guest}: would link it to {want}" + (f" (now {cur[1]})" if cur else ""))
        return want
    r = ensure(cfg, store, ref, offline)
    vid, leaf = r["version"], Path(r["dir"])
    if cur and cur[1] == vid and os.path.isdir(p):
        say(f"{guest}: {p} already links to {vid}")
        return vid
    cfg.vms.mkdir(parents=True, exist_ok=True)
    with store.lock():                         # gc waits: rule f sees the old link or the new one
        cur = current()
        not_running()
        try:
            ok = json.loads((leaf / ".vm-store.json").read_text()).get("version") == vid
        except (OSError, ValueError):
            ok = False
        if not ok:
            raise StoreError(f"{leaf} is not a whole {guest}@{vid} any more; run link again")
        tmp = cfg.vms / f".{guest}.vm-store-link.{os.getpid()}"     # LINK_TMP_RE
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        tmp.symlink_to(leaf)
        try:
            os.rename(tmp, p)                  # vms/G is the old link or the new one, never absent
        except OSError as e:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise StoreError(f"{p}: {e.strerror}; left as it was")
        fsync_dir(cfg.vms)
    say(f"{guest}: {p} -> {leaf}" + (f" (was {cur[1]})" if cur else " (new)"))
    return vid


def cmd_link(cfg: Config, a) -> int:
    store = Store(cfg.root, cfg)
    if store.role != "cache":
        raise StoreError(f"link runs on a cache (a host without a full vms/); {store.root} is a {store.role}")
    if a.all == bool(a.refs):
        raise Usage("link GUEST[@VID]... | --all")
    for e in os.scandir(cfg.vms) if cfg.vms.is_dir() else []:
        m = LINK_TMP_RE.fullmatch(e.name)
        if m and e.is_symlink():               # a dead link's temp link
            try:
                os.kill(int(m[2]), 0)
            except ProcessLookupError:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(e.path)
            except PermissionError:
                pass
    refs = link_guests(cfg, store) if a.all else a.refs
    if a.all and not refs:
        say(f"no link in {cfg.vms} points into {store.live}")
    rcs = []
    for ref in refs:
        try:
            vid = link_one(cfg, store, ref, a.offline, a.dry_run)
            print(f"{parse_ref(ref)[0]} {vid}")
            rcs.append(0)
        except StoreError as e:
            say(f"{ref}: {e}")
            rcs.append(e.rc)
    return next((c for c in (1, 4, 3, 2) if c in rcs), 0)


# --- gc ------------------------------------------------------------------------------

def os_tree(d: str) -> bool:
    """D is the root of an installed OS tree (a chroot, an unpacked image): etc/os-release
    (or usr/lib/os-release) and a usr/ dir."""
    return os.path.isdir(os.path.join(d, "usr")) and any(
        os.path.lexists(os.path.join(d, r)) for r in ("etc/os-release", "usr/lib/os-release"))


def scan_overlays(cfg: Config, store: Store, os_dirs: list[str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Rule b: {object: an overlay that has it in its backing chain}, and the dirs and
    images the scan couldn't read (rule d).  VM_STORE_SCAN_SKIP's trees are not walked.  A
    dir it can't read inside an OS tree (os_tree: a chroot's root-owned 0700 dirs) is the
    OS's own, not a place an overlay is made, so it is not blind: it goes to OS_DIRS
    instead.  An unreadable *.qcow2 is blind wherever it is."""
    objects_real = os.path.realpath(store.objects)
    skip = {os.path.realpath(d) for d in cfg.scan_skip}
    refs: dict[str, str] = {}
    blind: list[str] = []
    roots: list[str] = []                      # the OS trees met so far (walk is top-down)

    def in_os_tree(p: str) -> bool:
        return any(p.startswith(r + os.sep) for r in roots)

    def unreadable(e: OSError) -> None:
        if in_os_tree(e.filename):
            if os_dirs is not None:
                os_dirs.append(e.filename)
        else:
            blind.append(e.filename)

    for top in cfg.scan:
        if not os.path.isdir(top) or os.path.realpath(top) in skip:
            continue
        for dp, dns, fns in os.walk(top, onerror=unreadable):
            if skip:
                dns[:] = [d for d in dns if os.path.realpath(os.path.join(dp, d)) not in skip]
            if not in_os_tree(dp) and os_tree(dp):
                roots.append(dp)
            # Not the store roots' own objects/ (any root: they hold no overlays of ours).
            if os.path.isfile(os.path.join(dp, "store.json")) and "objects" in dns:
                dns.remove("objects")
            for f in fns:
                if not f.endswith(".qcow2"):
                    continue
                p = os.path.join(dp, f)
                if os.path.islink(p):
                    continue
                cur, seen = p, set()
                for _ in range(32):
                    try:
                        h = qcow2_header(cur, strict=True)
                    except FileNotFoundError:
                        break                  # gone meanwhile, or a dangling backing: not ours
                    except OSError:
                        # Unreadable (a root vm-run's 0600 mktemp overlay): it may sit on any
                        # object, so this scan is blind (rule d), not "no backing".
                        blind.append(cur)
                        break
                    b = h["backing"] if h else None
                    if not b:
                        break
                    if "://" in b or b.startswith("json:"):
                        break
                    nxt = os.path.realpath(os.path.join(os.path.dirname(cur), b))
                    if nxt in seen:
                        break
                    seen.add(nxt)
                    if os.path.dirname(nxt) == objects_real and OBJ_RE.fullmatch(os.path.basename(nxt)):
                        refs.setdefault(os.path.basename(nxt), p)
                    cur = nxt
    return refs, blind


def vms_links(cfg: Config, store: Store) -> tuple[dict[tuple[str, str], str], dict[str, str], list[str]]:
    """Rule f: what $BKO_WORK/vms/ uses of this store.  ({(G, VID): the vms/ entry whose
    link resolves into live/G/VID}, {object: the vms/ file whose link resolves to it},
    the paths it couldn't read).  A vms/G link (link's, or made by hand) and links inside
    a real vms/G both count; a dangling one too, so its version is never the one gc takes.
    An unreadable dir (a sudo compare.py's 0700 one) may hide a link: rule d, as the scan's."""
    live_real, objects_real = os.path.realpath(store.live), os.path.realpath(store.objects)
    versions: dict[tuple[str, str], str] = {}
    objs: dict[str, str] = {}
    blind: list[str] = []

    def note(p: str) -> None:
        real = os.path.realpath(p)
        if os.path.dirname(real) == objects_real and OBJ_RE.fullmatch(os.path.basename(real)):
            objs.setdefault(os.path.basename(real), p)
            return
        rel = os.path.relpath(real, live_real).split(os.sep)
        if len(rel) >= 2 and rel[0] not in ("..", ".") and VID_RE.fullmatch(rel[1]):
            versions.setdefault((rel[0], rel[1]), p)

    try:
        entries = sorted(os.scandir(cfg.vms), key=lambda e: e.name)
    except (FileNotFoundError, NotADirectoryError):
        return versions, objs, blind
    except OSError:
        return versions, objs, [str(cfg.vms)]
    for e in entries:
        if LINK_TMP_RE.fullmatch(e.name):
            continue                           # link's own, renamed over vms/G under lock or stale
        try:
            link, real_dir = e.is_symlink(), e.is_dir(follow_symlinks=False)
        except OSError:
            blind.append(e.path)
            continue
        if link:
            note(e.path)
        elif real_dir:
            # Not into linked dirs, but each link counts.
            for dp, dns, fns in os.walk(e.path, onerror=lambda err: blind.append(err.filename)):
                for f in fns + dns:
                    p = os.path.join(dp, f)
                    try:
                        if stat.S_ISLNK(os.lstat(p).st_mode):
                            note(p)
                    except FileNotFoundError:
                        pass                   # gone meanwhile
                    except OSError:
                        blind.append(p)        # listed but not searchable (mode r--)
    return versions, objs, blind


def version_objects(store: Store, guest: str, vid: str) -> list[str]:
    """The objects of GUEST@VID: by its manifest, else by live/G/VID's own links."""
    got = store.manifest(guest, vid)
    if got:
        return manifest_objects(got[0])
    leaf = store.live / guest / vid / guest
    return [os.path.basename(os.readlink(p)) for p in leaf.glob("**/*") if p.is_symlink()]


def in_use(cfg: Config, store: Store, protect: set[str] = frozenset(),
           os_dirs: list[str] | None = None) -> tuple[dict[str, str], list[str]]:
    """{object: why it is kept} by rules a-f, and what the overlay scan or the vms/ walk
    couldn't read (rule d).  OS_DIRS gets the scan's unreadable dirs inside OS trees."""
    names = store.object_names()
    why: dict[str, str] = {}
    for n in names:
        if qemu_holds(store.obj(n)):
            why[n] = "qemu lock"
    refs, blind = scan_overlays(cfg, store, os_dirs)
    for n, p in refs.items():
        why.setdefault(n, f"overlay {p}")
    present = set(names)
    linked, linked_objs, vms_blind = vms_links(cfg, store)
    blind = blind + vms_blind                  # rule d: a hidden link may use any object
    for o, p in linked_objs.items():                   # rule f, a disk linked by hand: as an overlay's
        if o in present:
            why.setdefault(o, f"linked from {p}")
    age = BLIND_AGE if blind else cfg.min_age
    now = time.time()
    for n in names:
        t = store.stamp_time(n)
        if now - t < age:
            why.setdefault(n, f"used {(now - t) / 3600:.0f}h ago" + (" (7-day age: unreadable scan dirs)" if blind else ""))
    for n in protect:
        why.setdefault(n, "this ensure's version")
    versions = [(g, v, manifest_objects(m)) for g, v, m, _ in store.manifests()]
    changed = True
    while changed:                             # rule e: a version is kept whole, chains and all
        changed = False
        for g, v, objs in versions:
            hit = next((o for o in objs if o in why), None)
            if hit:
                for o in objs:
                    if o not in why:
                        why[o] = f"version {g}@{v} ({hit[:19]}…: {why[hit]})"
                        changed = True
    # Rule f, a vms/G link (vm-store link): its whole version, chains and all, after rule e,
    # so that a sidecar object it shares with older versions keeps none of them for good.
    for (g, v), p in linked.items():
        for o in version_objects(store, g, v):
            if o in present:
                why.setdefault(o, f"linked from {p}")
    return why, blind


def gc(cfg: Config, store: Store, need: int = 0, protect: set[str] = frozenset(), dry: bool = False) -> dict:
    """Section 2.7: evict unused objects, least recently ensured first, until use+NEED <= cap
    and free-NEED >= floor.  NoRoom if that can't be reached."""
    if store.role != "cache":
        raise StoreError(f"gc runs on caches only in this version; {store.root} is a {store.role}")
    os_dirs: list[str] = []
    why, blind = in_use(cfg, store, protect, os_dirs)
    for d in blind:
        say(f"gc: can't read {d}: objects stamped in the last 7 days are kept")
    if os_dirs:
        say(f"gc: can't read {len(os_dirs)} dirs inside OS trees (as {os_dirs[0]}): "
            "an OS's own, not counted as blind")
    cap = store.cap if store.cap is not None else float("inf")
    use, free = store.use(), store.free()
    plan = {"use": use, "free": free, "cap": store.cap, "floor": store.floor, "need": need, "kept": why,
            "evicted": [], "blind": blind}

    def fits() -> bool:
        return use + need <= cap and free - need >= store.floor

    candidates = sorted((n for n in store.object_names() if n not in why), key=store.stamp_time)
    for n in candidates:
        if fits():
            break
        if time.time() - store.stamp_time(n) < (BLIND_AGE if blind else cfg.min_age):
            why[n] = "used just now"           # an ensure touched it meanwhile
            continue
        size = allocated(store.obj(n))
        if dry:
            use -= size
            free += size
        else:
            store.obj(n).unlink()
            with contextlib.suppress(FileNotFoundError):
                (store.used / n).unlink()
            use, free = store.use(), store.free()
        plan["evicted"].append(n)
        say(f"gc: {'would evict' if dry else 'evicted'} {n} ({gib(size)})")
    if not dry:
        gc_live(store, keep=set(vms_links(cfg, store)[0]))
        cutoff = time.time() - INCOMING_MAX_AGE
        for dp, _dns, fns in os.walk(store.incoming):
            for f in fns:
                p = os.path.join(dp, f)
                with contextlib.suppress(OSError):
                    if os.lstat(p).st_mtime < cutoff:
                        os.unlink(p)
    plan["use"], plan["free"] = use, free
    if not fits():
        reasons = "; ".join(f"{n[:19]}…: {w}" for n, w in sorted(why.items())[:8])
        raise NoRoom(f"{gib(use)} in use against a cap of {gib(cap) if cap != float('inf') else 'none'}, "
                     f"{gib(free)} free against a floor of {gib(store.floor)}, {gib(need)} needed: {reasons}")
    return plan


def gc_live(store: Store, keep: set[tuple[str, str]] = frozenset()) -> None:
    """Each live/G/VID whose objects are gone, and dead ensures' tmp dirs.  Never one
    in KEEP (a vms/ link points into it: removing it would leave the link dangling)."""
    for gd in store.live.iterdir() if store.live.is_dir() else []:
        for vd in gd.iterdir():
            if (gd.name, vd.name) in keep:
                continue
            m = re.fullmatch(r"\.(.+)\.tmp\.(\d+)", vd.name)
            if m:
                try:
                    os.kill(int(m[2]), 0)
                except ProcessLookupError:
                    shutil.rmtree(vd, ignore_errors=True)
                except PermissionError:
                    pass
                continue
            objs = version_objects(store, gd.name, vd.name)
            if any(not store.obj(o).is_file() for o in objs):
                shutil.rmtree(vd, ignore_errors=True)
                say(f"gc: removed {vd}")


def need_room(cfg: Config, store: Store, need: int, protect: set[str]) -> None:
    cap = store.cap if store.cap is not None else float("inf")
    if store.use() + need <= cap and store.free() - need >= store.floor:
        return
    gc(cfg, store, need=need, protect=protect)


def cmd_gc(cfg: Config, a) -> int:
    store = Store(cfg.root, cfg)
    with store.lock():
        try:
            plan = gc(cfg, store, dry=a.dry_run)
            rc = 0
        except NoRoom as e:
            if a.json:
                print(json.dumps({"error": str(e)}))
            raise
    if a.json:
        print(json.dumps(plan, indent=1))
    else:
        for n in plan["evicted"]:
            print(f"{'would evict' if a.dry_run else 'evicted'} {n}")
        for n, w in sorted(plan["kept"].items()):
            print(f"kept {n}: {w}")
        print(f"use {gib(plan['use'])}, free {gib(plan['free'])}")
    return rc


# --- list, status, verify ------------------------------------------------------------

def cmd_list(cfg: Config, a) -> int:
    if a.source:
        s = cfg.source(a.source)
        s.check()
        lst = remote_py(s.host, LIST_PY, {"root": s.path, "full": True})
    else:
        store = Store(cfg.root, cfg)
        lst = {"latest": {}, "manifests": {}}
        for g, v, m, _ in store.manifests():
            lst["manifests"][f"{g}/versions/{v}.json"] = json.dumps(m)
        for g in sorted(p.name for p in store.guests.iterdir() if (p / "latest").is_file()):
            lst["latest"][g] = (store.guests / g / "latest").read_text()
    out = {}
    for rel, text in lst["manifests"].items():
        g = rel.split("/")[0]
        if a.guest and g != a.guest:
            continue
        try:
            m = json.loads(text)
        except ValueError:
            continue
        latest = (lst["latest"].get(g) or "").split()[:1]
        out.setdefault(g, []).append({"version": m.get("version"), "latest": latest == [m.get("version")],
                                      "published": m.get("published"), "updates": m.get("updates"),
                                      "parent": m.get("parent"), "note": m.get("note"),
                                      "bytes": sum(d.get("file_size") or 0 for d in m.get("disks", []))})
    for g in out:
        out[g].sort(key=lambda r: (r["published"] or "", r["updates"] if r["updates"] is not None else -1))
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        for g in sorted(out):
            for r in out[g]:
                print(f"{g:<24} {r['version']} {'latest' if r['latest'] else '      '} {r['published']} "
                      f"updates={r['updates']} {gib(r['bytes'])}" + (f"  {r['note']}" if r["note"] else ""))
    return 0


def cmd_status(cfg: Config, a) -> int:
    store = Store(cfg.root, cfg)
    names = store.object_names()
    st = {"root": str(store.root), "role": store.role, "objects": len(names), "use": store.use(),
          "cap": store.cap, "free": store.free(), "floor": store.floor,
          "guests": len([p for p in store.guests.iterdir() if (p / "latest").is_file()]),
          "versions": sum(1 for _ in store.manifests())}
    if store.role == "cache":
        why, blind = in_use(cfg, store)
        st["in_use"], st["blind"] = why, blind
    if store.role == "mirror":
        try:
            bigfoot_gate(cfg, "status", "not reading from it", pool_only=True)
            here, there = local_listing(store), bigfoot_listing(cfg)
            st["unpushed"] = unpushed(here, there)
            st["differs"] = differs(here, there)
        except StoreError as e:
            st["unpushed"] = None
            st["bigfoot"] = str(e)
    if a.json:
        print(json.dumps(st, indent=1))
        return 0
    cap = gib(st["cap"]) if st["cap"] is not None else "none"
    print(f"{st['root']}: role {st['role']}, {st['objects']} objects, {st['guests']} guests, {st['versions']} versions")
    print(f"use {gib(st['use'])} of cap {cap}; free {gib(st['free'])}, floor {gib(st['floor'])}")
    for n, w in sorted(st.get("in_use", {}).items()):
        print(f"in use {n}: {w}")
    if "unpushed" in st:
        u = st["unpushed"]
        print(f"unpushed: bigfoot unreachable ({st['bigfoot']})" if u is None else
              f"unpushed: {len(u['objects'])} objects, {len(u['meta'])} manifest files, {len(u['latest'])} latest")
        for d in st.get("differs", []):
            print(f"differs on bigfoot: {d}")
    return 0


def verify_local(store: Store, only: list[str]) -> dict:
    problems: list[str] = []
    checked = {"objects": 0, "manifests": 0, "latest": 0}
    # A cache keeps its manifests forever and gc evicts their objects (section 1.3):
    # there an absent object is "not cached", counted, not a problem.
    cache = store.role == "cache"
    absent: set[str] = set()
    if cache:
        checked["absent"] = 0
    want_objs: set[str] | None = None
    if only:
        want_objs = set()
    gdirs = sorted(p for p in store.guests.iterdir() if p.is_dir() and (not only or p.name in only))
    for gd in gdirs:
        vd = store.vdir(gd.name)
        for f in sorted(vd.glob("*.json")) if vd.is_dir() else []:
            vid = f.stem
            shaf = vd / f"{vid}.json.sha256"
            if not shaf.exists():
                problems.append(f"{gd.name}@{vid}: no .sha256 (an unfinished publish; publishing it again completes it)")
                continue
            got = store.manifest(gd.name, vid)
            if not got:
                problems.append(f"{gd.name}@{vid}: manifest doesn't match its .sha256")
                continue
            m, _ = got
            checked["manifests"] += 1
            try:
                check_manifest(m, gd.name, vid)
            except StoreError as e:
                problems.append(str(e))
                continue
            if want_objs is not None:
                want_objs |= set(manifest_objects(m))
            for d in m["disks"]:
                top = store.obj(d["chain"][-1])
                if not top.is_file():
                    if cache:
                        absent.add(d["chain"][-1])
                    else:
                        problems.append(f"{gd.name}@{vid}: {d['name']}'s object {d['chain'][-1]} is missing")
                    continue
                if os.path.getsize(top) != d["file_size"]:
                    problems.append(f"{gd.name}@{vid}: {d['chain'][-1]} is {os.path.getsize(top)} bytes, "
                                    f"the manifest says {d['file_size']}")
                gone = [o for o in d["chain"][:-1] if not store.obj(o).is_file()]
                if cache and gone:
                    absent.update(gone)        # its base was evicted: no chain to follow here
                    continue
                chain = backing_chain(top)
                if chain != d["chain"]:
                    problems.append(f"{gd.name}@{vid}: {d['name']}'s backing chain is {chain}, the manifest says {d['chain']}")
            for rel, f2 in m.get("files", {}).items():
                p = store.obj(f2["object"])
                if not p.is_file():
                    if cache:
                        absent.add(f2["object"])
                    else:
                        problems.append(f"{gd.name}@{vid}: {rel}'s object {f2['object']} is missing")
                elif os.path.getsize(p) != f2["size"]:
                    problems.append(f"{gd.name}@{vid}: {f2['object']} is {os.path.getsize(p)} bytes, the manifest says {f2['size']}")
        lp = gd / "latest"
        if lp.is_file():
            checked["latest"] += 1
            try:
                vid, sha = parse_latest(lp.read_text())
                got = store.manifest(gd.name, vid)
                if not got or got[1] != sha:
                    problems.append(f"{gd.name}/latest names {vid} {sha[:12]}, which isn't a manifest here")
            except StoreError as e:
                problems.append(f"{gd.name}/latest: {e}")
    for n in store.object_names():
        if want_objs is not None and n not in want_objs:
            continue
        checked["objects"] += 1
        p = store.obj(n)
        mode = stat.S_IMODE(os.stat(p).st_mode)
        if mode != (0o444 if n.endswith(".qcow2") else 0o400):
            problems.append(f"{n}: mode {mode:04o}")
        if sha256_file(p) != OBJ_RE.fullmatch(n)[1]:
            problems.append(f"{n}: its bytes don't match its name")
    for e in os.scandir(store.objects):
        if e.name != ".incoming" and not OBJ_RE.fullmatch(e.name):
            problems.append(f"objects/{e.name}: not an object name")
    if cache:
        checked["absent"] = len(absent)
    return {"where": "local", "root": str(store.root), "checked": checked, "problems": problems}


def backing_chain(top: Path) -> list[str]:
    """The object names from base to TOP: qemu-img info -U --backing-chain, or the headers."""
    if shutil.which("qemu-img"):
        r = subprocess.run(["qemu-img", "info", "-U", "--backing-chain", "--output=json", str(top)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return [f"<qemu-img: {r.stderr.strip()}>"]
        info = json.loads(r.stdout)
        info = info if isinstance(info, list) else [info]
        return [os.path.basename(i["filename"]) for i in reversed(info)]
    chain, cur = [os.path.basename(top)], str(top)
    for _ in range(32):
        b = qcow2_backing(cur)
        if not b:
            break
        cur = os.path.realpath(os.path.join(os.path.dirname(cur), b))
        chain.insert(0, os.path.basename(cur))
    return chain


def verify_bigfoot(cfg: Config, store: Store, only: list[str]) -> dict:
    """Bigfoot's root against this one: object sizes, each manifest by the sha256 of
    its .json there (not only its .sha256 text), and each latest."""
    if store.role != "mirror":                 # D2: only Chopin talks to Bigfoot
        raise StoreError(f"verify --where bigfoot runs on the mirror (Chopin); {store.root} is a {store.role}")
    bigfoot_gate(cfg, "verify", "not reading from it", pool_only=True)
    there = bigfoot_listing(cfg)
    here = local_listing(store)
    keep = (lambda rel: rel.split("/")[0] in only) if only else (lambda rel: True)
    problems = differs(here, there, keep)
    unpushed_ = [f"objects/{n}" for n in here["objects"] if n not in there["objects"]]
    for jr, sha in here["json_sha"].items():
        if not keep(jr):
            continue
        sr = jr + ".sha256"
        tj, ts = there["json_sha"].get(jr), first_word(there["sha256"].get(sr))
        if tj is None and ts is None:
            unpushed_.append(f"guests/{jr}")
        elif (tj is None or ts is None) and (tj or ts) == sha:
            unpushed_.append(f"guests/{sr if ts is None else jr} (its pair is there)")
    for g, text in here["latest"].items():
        if not keep(g):
            continue
        if g not in there["latest"]:
            unpushed_.append(f"guests/{g}/latest")
        elif there["latest"][g] != text:
            unpushed_.append(f"guests/{g}/latest (bigfoot: {there['latest'][g].split()[0]})")
    for n in there["objects"]:
        if n not in here["objects"]:
            problems.append(f"objects/{n}: on bigfoot, not here")
    pool = None
    if cfg.bf_host:
        try:
            r = subprocess.run(["ssh", *SSH_OPTS, cfg.bf_host, "zpool status -x " + shlex.quote(cfg.bf_pool)],
                               capture_output=True, text=True, timeout=BF_PROBE_TIMEOUT, stdin=subprocess.DEVNULL)
            pool = (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            pool = f"no answer within {BF_PROBE_TIMEOUT}s"
        say(f"bigfoot: zpool status -x {cfg.bf_pool}: {pool}")
    return {"where": "bigfoot", "root": cfg.bf_root, "problems": problems, "unpushed": unpushed_, "zpool": pool}


def cmd_verify(cfg: Config, a) -> int:
    store = Store(cfg.root, cfg)
    r = verify_local(store, a.guests) if a.where == "local" else verify_bigfoot(cfg, store, a.guests)
    if a.json:
        print(json.dumps(r, indent=1))
    for p in r["problems"]:
        (say if a.json else print)(f"problem: {p}")
    if not a.json:
        for u in r.get("unpushed", []):
            print(f"unpushed: {u}")
        print(f"{len(r['problems'])} problem(s)" + (f", checked {r['checked']}" if "checked" in r else ""))
    return 1 if r["problems"] else 0


# --- CLI -----------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vm-store", description="Content-addressed copies of the test guests' images "
                                "(docs/design/image-store.md; testvm/store/README.md).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="make a store root")
    s.add_argument("root", nargs="?")
    s.add_argument("--role", choices=["mirror", "cache"])
    s.add_argument("--cap")
    s.add_argument("--floor")
    s.add_argument("--bigfoot", action="store_true")
    s = sub.add_parser("publish", help="store a guest's current state (Chopin)")
    s.add_argument("guests", nargs="*")
    s.add_argument("--changed", action="store_true")
    s.add_argument("--from", dest="from_dir")
    s.add_argument("--expect-generation")
    s.add_argument("--set-latest", action="store_true")
    s.add_argument("--note")
    s.add_argument("--push", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--stateless", action="append", default=[], metavar="GUEST[,GUEST...]")
    s = sub.add_parser("push", help="send Bigfoot what it lacks (Chopin)")
    s.add_argument("--now", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("ensure", help="fetch a version and print its guest folder")
    s.add_argument("ref", metavar="GUEST[@VID|@latest]")
    s.add_argument("--offline", action="store_true")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("fetch", help="ensure, printing nothing (prefetch)")
    s.add_argument("refs", nargs="+", metavar="GUEST[@VID]")
    s = sub.add_parser("link", help="point vms/GUEST at a version (a cache), fetching it first")
    s.add_argument("refs", nargs="*", metavar="GUEST[@VID]")
    s.add_argument("--all", action="store_true")
    s.add_argument("--offline", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("gc", help="make room in a cache")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("list", help="the versions here or at a source")
    s.add_argument("guest", nargs="?")
    s.add_argument("--source")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("status", help="use, room, what is in use, what is unpushed")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("verify", help="check the store here, or Bigfoot's against it")
    s.add_argument("--where", choices=["local", "bigfoot"], default="local")
    s.add_argument("guests", nargs="*")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("bigfoot-health", help="whether Bigfoot is fit to write to (Chopin): exit 0 yes, 1 no")
    s.add_argument("--pool-only", action="store_true", help="only the pool's health (what a reader needs)")
    s.add_argument("--json", action="store_true")
    return p


COMMANDS = {"init": cmd_init, "publish": cmd_publish, "push": cmd_push, "ensure": cmd_ensure, "fetch": cmd_fetch,
            "link": cmd_link, "gc": cmd_gc, "list": cmd_list, "status": cmd_status, "verify": cmd_verify,
            "bigfoot-health": cmd_bigfoot_health}


def main(argv: list[str] | None = None) -> int:
    try:
        a = parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    try:
        return COMMANDS[a.cmd](Config(), a)
    except StoreError as e:
        say(str(e))
        return e.rc


def cli() -> None:
    def stop(signum, _frame):
        raise SystemExit(128 + signum)        # unwinds: our tmp files and children go
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, stop)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
