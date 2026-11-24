"""Content drift, for rustic-bko (backshot2.md, "Content drift"): a seeded
tree of awkward files planted under /srv/bs2 before the backup.

A case's "content" is {"recipe": NAME, "seed": N} and, once shrunk,
"only": [INDEX, ...], the entries of the recipe's plan it keeps.  plan()
turns that into a list of entries, the same on every replay; plant() makes
them under a mounted source root, as root, on the host (testvm's guests are
mounted read-write in a throwaway overlay).  sizes() is rule 1's share.

What is planted is only what the filesystem can hold (FS_CAN), and only what
rustic-bko keeps (docs/design/archiver-flags.md: owners by number, modes,
times, xattrs, ACLs and capabilities as xattrs, file flags, holes):
  sparse files with holes at chunk boundaries; hard links across
  directories; files of 0, 1, chunk-1, chunk and chunk+1 bytes and one
  large one; FIFOs and device nodes; symlinks long, dangling, relative and
  absolute; names of 255 bytes, not UTF-8, with newlines; a tree deeper than
  4,096 bytes of path; ACLs, capabilities, user.* xattrs; chattr +i and +a;
  mtimes before 1970 and after 2038.

The generic recipes (RECIPES), one per Linux filesystem, are what generic
Linux and NixOS draw (families/linux.py choose() picks one from the
dump); a family may add its own (Alpine's, Guix's).  A recipe may "drop"
features its filesystem holds but the source's (or what a drift makes of
it) doesn't: "-32bit" ones drop post2038, for an XFS without bigtime and an
ext without extra_isize (whose drift may draw 128-byte inodes).
"""

import errno
import hashlib
import os
import random
import struct

DIR = "/srv/bs2"
# rustic's chunker: content-defined, 512 KiB to 8 MiB, 1 MiB on average.
CHUNK = 1 << 20
CHUNK_MIN = 512 << 10

# The generic recipes, one per filesystem (since GENERATOR "6"), each
# alpine-small-1's shape: ~165 inodes, ~75 MiB of data (~95 over seeds 0-299;
# bound(), at any seed, ~190), the deep
# tree past PATH_MAX (rustic-bko keeps it since fail-001's fix).  "fs" says
# which filesystems a recipe is for (families/linux.py picks by it; plan()
# reads only the knobs and "drop").  Each family adds its own
# (families/*.py `recipes`: Alpine's alpine-small-1 and alpine-small-2),
# found by recipe_of().
SHAPE = {"files": 60, "large": 24 << 20, "deep": 1, "deep_bytes": 4300, "links": 8, "symlinks": 12, "names": 10, "nodes": 4}
RECIPES = {
    "ext-1": dict(SHAPE, fs=["ext2", "ext3", "ext4"]),
    "ext-1-32bit": dict(SHAPE, fs=["ext2", "ext3", "ext4"], drop=["post2038"]),
    "xfs-1": dict(SHAPE, fs=["xfs"]),
    "xfs-1-32bit": dict(SHAPE, fs=["xfs"], drop=["post2038"]),
    "btrfs-1": dict(SHAPE, fs=["btrfs"]),
    "f2fs-1": dict(SHAPE, fs=["f2fs"]),
    "jfs-1": dict(SHAPE, fs=["jfs"]),
    "nilfs2-1": dict(SHAPE, fs=["nilfs2"]),
    "bcachefs-1": dict(SHAPE, fs=["bcachefs"]),
}


def recipe_of(name):
    """The recipe NAME: generic, or a family's."""
    if name in RECIPES:
        return RECIPES[name]
    from . import families
    r = families.recipe(name)
    if r is None:
        raise KeyError(f"no content recipe {name!r}")
    return r


def fs_can_of(fs_type):
    """What a filesystem of FS_TYPE holds: FS_CAN's, and what the families add."""
    from . import families
    return set(FS_CAN.get(fs_type, set())) | set(families.fs_can(fs_type) or ())


# What each filesystem holds: xattrs (user.* and security.capability), POSIX
# ACLs, chattr +i/+a, times before 1970, after 2038, device nodes, and
# "rawnames": names of any bytes (not UTF-8 ones too).  FIFOs, hard links,
# holes and 255-byte names everywhere.  What matters is the kernel that
# plants and judges (the farm host's, Debian's) and the one that restores
# (the rescue's, Ubuntu's): both build every one of these filesystems with
# xattrs, POSIX ACLs and security labels, and NLS's default is utf8.
#   f2fs      times are 64-bit seconds and 32-bit nanoseconds on disk
#             (struct f2fs_inode), so both ends hold; FS_IOC_SETFLAGS takes
#             +i and +a; xattrs, ACLs and security.* (F2FS_FS_SECURITY).
#             Names are bytes unless casefolded (no corpus f2fs is).
#   jfs       times are unsigned 32-bit seconds (s_time_min 0, s_time_max
#             U32_MAX): after 2038 (to 2106) yes, before 1970 no.  Names
#             are kept as UTF-16 through the NLS table, so a name that
#             isn't UTF-8 is refused (EINVAL): no rawnames (unverified,
#             and coverage lost: no corpus JFS mount sets iocharset).  Holes are
#             judged by st_blocks (it has the generic SEEK_HOLE:
#             compare/fsmeta.py NO_SEEK_HOLE).
#   nilfs2    no xattrs at all (so no ACLs, no capabilities); 64-bit times;
#             +i and +a; holes judged by st_blocks as for jfs.
#   bcachefs  xattrs, ACLs, security.*; times signed 64-bit at the
#             filesystem's time precision (1 ns unless set at format);
#             +i and +a.
FS_CAN = {
    "ext4": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "ext3": {"xattr", "acl", "caps", "chattr", "pre1970", "nodes", "rawnames"},
    "ext2": {"xattr", "acl", "caps", "chattr", "pre1970", "nodes", "rawnames"},
    "xfs": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "btrfs": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "f2fs": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "jfs": {"xattr", "acl", "caps", "chattr", "post2038", "nodes"},
    "nilfs2": {"chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "bcachefs": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "vfat": set(),
}


def _name(rng, n=8):
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(n))


def _acl(entries):
    """A POSIX ACL xattr value: version 2, then (tag, perm, id) records."""
    out = struct.pack("<I", 2)
    for tag, perm, uid in entries:
        out += struct.pack("<HHI", tag, perm, uid)
    return out


ACL_USER_OBJ, ACL_USER, ACL_GROUP_OBJ, ACL_GROUP, ACL_MASK, ACL_OTHER = 1, 2, 4, 8, 0x10, 0x20
UNDEF = 0xFFFFFFFF


def _caps():
    """security.capability, VFS_CAP_REVISION_2: cap_net_bind_service (10) permitted+effective."""
    return struct.pack("<IIIII", 0x02000001, 1 << 10, 0, 0, 0)


# The longest symlink target each filesystem stores, in bytes: XFS's
# XFS_SYMLINK_MAXLEN; ext2/3/4 keep a target and its NUL in one block, so a
# block less one; btrfs keeps it inline in one leaf item, so its node size
# less the leaf header (101), the item (25) and the inline extent's header
# (21): BTRFS_MAX_INLINE_DATA_SIZE, 3,949 bytes at a 4 KiB node and past
# PATH_MAX from 8 KiB on (fs_symlink_max(); symlink_max() works it out from
# a layout).
SYMLINK_MAX = {"xfs": 1024}
BTRFS_INLINE_OVERHEAD = 101 + 25 + 21
PATH_MAX = 4096
LONG_DEFAULT = {"xfs": 500}   # a/long's "x/" pairs: 1,003 bytes on XFS, 4,083 elsewhere
LONG_PAIRS = 2040


def srv_mount(dump, path=None):
    """The mount in DUMP that holds PATH (the longest target above it), or
    None: what Hooks.recipe() and generate.planted_of() read its fstype from.
    PATH: DIR where it really is (families' srv(): ABRoot's /srv is a link
    to var/srv, so /var's mount)."""
    from . import dumpx
    if path is None:
        from . import families
        path = families.hooks(None, dump).srv_dir(dump)
    ms = sorted((m for m in dumpx.storage(dump).get("mounts") or []
                 if (path + "/").startswith(str(m.get("target") or "").rstrip("/") + "/")), key=lambda m: len(m["target"]))
    return ms[-1] if ms else None


def symlink_max(layout, path=None):
    """The longest symlink target the filesystem holding PATH (DIR, where it
    really is: srv_mount()) stores, in LAYOUT (a dump, or a drifted layout:
    what the restore makes it), or None where nothing tighter than PATH_MAX
    holds."""
    from . import dumpx
    m = srv_mount(layout, path)
    if m is None:
        return None
    dev = dumpx.devices(layout).get(m.get("device"))
    c = (dev or {}).get("content") or {}
    return fs_symlink_max(c.get("type") or m.get("fstype"), c.get("block_size"), c.get("node_size"))


def fs_symlink_max(fs_type, block_size=None, node_size=None):
    """The longest symlink target a filesystem of FS_TYPE with BLOCK_SIZE
    (bytes; statvfs's f_bsize will do) and, for btrfs, NODE_SIZE (a dump's
    node_size: statvfs has none, so a mounted btrfs says nothing) stores, or
    None where nothing tighter than PATH_MAX holds."""
    if fs_type in ("ext2", "ext3", "ext4", "f2fs", "nilfs2") and block_size:
        # (f2fs and nilfs2 refuse a target and its NUL longer than a block.)
        return int(block_size) - 1
    if fs_type == "btrfs" and node_size:
        n = int(node_size) - BTRFS_INLINE_OVERHEAD
        return n if n < PATH_MAX - 1 else None
    return SYMLINK_MAX.get(fs_type)


def tightest(*limits):
    """The smallest of LIMITS that isn't None, or None."""
    have = [x for x in limits if x is not None]
    return min(have) if have else None


def plan(content, fs_type, ext4_big_inodes=True, xfs_bigtime=True, symlink_max=None):
    """The entries of CONTENT on a filesystem of FS_TYPE, in planting order.
    SYMLINK_MAX (bytes) caps a/long, the long symlink, where a filesystem it
    must fit on stores less than FS_TYPE's default: the source's and the one
    restored onto (ext4 with smaller blocks; tightest() of the two, from
    fs_symlink_max() and symlink_max()); the entries themselves, and their
    indexes, are the same.

    An entry: {"i": INDEX, "kind": file|dir|hardlink|symlink|fifo|chr|blk,
    "path": hex of the bytes under DIR, ...}; files have "size" and "holes"
    ([[offset, length]]) and "fill" (the seed of their bytes), links "to",
    and any may have "mode", "uid", "gid", "mtime_ns", "xattrs" ({name:
    hex}) and "flags" ("i" or "a")."""
    recipe = recipe_of(content["recipe"])
    rng = random.Random(f"bs2-content:{content['recipe']}:{content['seed']}")
    can = fs_can_of(fs_type)
    if fs_type in ("ext4", "ext3", "ext2") and not ext4_big_inodes:
        can.discard("post2038")
    if fs_type == "xfs" and not xfs_bigtime:
        can.discard("post2038")
    can -= set(recipe.get("drop") or ())

    def nm(b):
        """A name as the filesystem takes it: its bytes, or (no rawnames) each byte as a Unicode character, in UTF-8."""
        return b if "rawnames" in can else b.decode("latin-1").encode()

    out = []

    def add(kind, path, **kw):
        e = {"i": len(out), "kind": kind, "path": path.hex()}
        e.update(kw)
        out.append(e)
        return e

    dirs = [b"a", b"b", b"c/d"]
    for d in [b"a", b"b", b"c", b"c/d"]:
        add("dir", d, mode=0o755)
    # Sizes around the chunker's: 0, 1, chunk-1, chunk, chunk+1, and its minimum's.
    sizes = [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, CHUNK_MIN - 1, CHUNK_MIN, CHUNK_MIN + 1]
    files = []
    for i in range(recipe["files"]):
        size = sizes[i] if i < len(sizes) else rng.choice([rng.randrange(0, 70000), rng.randrange(0, 3 * CHUNK)])
        p = rng.choice(dirs) + b"/f" + str(i).encode()
        e = add("file", p, size=size, holes=[], fill=rng.randrange(1 << 30), mode=rng.choice([0o644, 0o600, 0o755, 0o4755, 0o2711, 0o777]),
                uid=rng.choice([0, 0, 1000, 65534, 4242]), gid=rng.choice([0, 0, 1000, 65534, 4343]))
        files.append(e)
    # Sparse: holes at chunk boundaries, and one file that is nearly all hole.
    for j, (size, holes) in enumerate([(4 * CHUNK, [[CHUNK, CHUNK]]), (3 * CHUNK + 17, [[0, CHUNK], [2 * CHUNK, CHUNK]]),
                                       (64 * CHUNK, [[0, 63 * CHUNK]])]):
        files.append(add("file", b"a/sparse" + str(j).encode(), size=size, holes=holes, fill=rng.randrange(1 << 30), mode=0o644))
    files.append(add("file", b"b/large", size=recipe["large"], holes=[], fill=rng.randrange(1 << 30), mode=0o644))
    # Hard links across directories.
    for k in range(recipe["links"]):
        src = rng.choice([f for f in files if f["size"] < 2 * CHUNK])
        add("hardlink", rng.choice(dirs) + b"/hl" + str(k).encode(), to=src["path"])
    # Symlinks: long, dangling, relative, absolute.
    add("symlink", b"a/abs", to=b"/etc/os-release".hex())
    add("symlink", b"a/rel", to=b"../b/large".hex())
    add("symlink", b"a/dangling", to=b"nowhere/at/all".hex())
    # A symlink nearly as long as the filesystem allows (XFS: 1,024 bytes;
    # ext4: its block size less one): FS_TYPE's default, or less where
    # SYMLINK_MAX says the source or what it's restored onto stores less.
    pairs = LONG_DEFAULT.get(fs_type, LONG_PAIRS)
    if symlink_max is not None:
        pairs = max(1, min(pairs, (symlink_max - 3) // 2))
    add("symlink", b"a/long", to=(b"x/" * pairs + b"end").hex())
    for k in range(recipe["symlinks"] - 4):
        add("symlink", b"b/s" + str(k).encode(), to=rng.choice([b"../a", b"/tmp", b"f0", b"\xff\xfe"]).hex())
    # Names: 255 bytes, not UTF-8, with a newline.
    add("file", b"c/" + b"n" * 255, size=3, holes=[], fill=1, mode=0o644)
    add("file", nm(b"c/\xff\xfe\xfdnot-utf8"), size=5, holes=[], fill=2, mode=0o644)
    add("file", b"c/new\nline", size=7, holes=[], fill=3, mode=0o644)
    for k in range(recipe["names"] - 3):
        add("file", nm(b"c/" + bytes(rng.choice([0x20, 0x2a, 0x3f, 0x5c, 0xe2, 0x80, 0x99, 0x41]) for _ in range(rng.randrange(1, 40))) + str(k).encode()),
            size=rng.randrange(0, 100), holes=[], fill=k, mode=0o644)
    # A tree past 4,096 bytes of path.
    for t in range(recipe["deep"]):
        p = b"deep" + str(t).encode()
        add("dir", p, mode=0o755)
        while len(p) < recipe["deep_bytes"] - 61:
            p += b"/" + _name(rng, 60).encode()
            add("dir", p, mode=0o755)
        add("file", p + b"/leaf", size=11, holes=[], fill=t, mode=0o644)
    # FIFOs and device nodes.
    add("fifo", b"b/fifo", mode=0o644)
    if "nodes" in can:
        add("chr", b"b/null-like", mode=0o600, rdev=[1, 3])
        add("blk", b"b/loop-like", mode=0o600, rdev=[7, 200])
    # Attributes.
    xs = [f for f in files if f["size"] < 2 * CHUNK]
    if "xattr" in can:
        for f in rng.sample(xs, min(6, len(xs))):
            f.setdefault("xattrs", {})["user.bs2"] = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 200))).hex()
    if "acl" in can:
        for f in rng.sample(xs, 3):
            f.setdefault("xattrs", {})["system.posix_acl_access"] = _acl([(ACL_USER_OBJ, 6, UNDEF), (ACL_USER, 4, 4242), (ACL_GROUP_OBJ, 4, UNDEF),
                                                                          (ACL_MASK, 4, UNDEF), (ACL_OTHER, 0, UNDEF)]).hex()
        out[1].setdefault("xattrs", {})["system.posix_acl_default"] = _acl([(ACL_USER_OBJ, 7, UNDEF), (ACL_GROUP_OBJ, 5, UNDEF), (ACL_OTHER, 5, UNDEF)]).hex()
    if "caps" in can:
        f = rng.choice(xs)
        f.setdefault("xattrs", {})["security.capability"] = _caps().hex()
        f["mode"] = 0o755
    if "chattr" in can:
        a, b = rng.sample([f for f in files if f["size"] < CHUNK], 2)
        a["flags"] = "i"
        b["flags"] = "a"
    # Times.
    for f in rng.sample(files, 10):
        f["mtime_ns"] = rng.randrange(1_000_000_000, 1_800_000_000) * 10**9 + rng.randrange(10**9)
    if "pre1970" in can:
        files[2]["mtime_ns"] = -86400 * 365 * 20 * 10**9 + 123456789
    if "post2038" in can:
        files[3]["mtime_ns"] = (1 << 31) * 10**9 + 86400 * 10**9 * 400 + 7
    if "only" in content:
        keep = set(content["only"])
        out = [e for e in out if e["i"] in keep]
        paths = {e["path"] for e in out}
        out = [e for e in out if e["kind"] != "hardlink" or e["to"] in paths]
    return out


def bound(recipe):
    """(bytes, inodes): at least sizes() of RECIPE's (a name, or a recipe)
    plan for any seed, on any filesystem: each random file at its largest."""
    r = recipe_of(recipe) if isinstance(recipe, str) else recipe
    fixed = [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, CHUNK_MIN - 1, CHUNK_MIN, CHUNK_MIN + 1]
    n = r["files"]
    data = sum(fixed[:n]) + max(0, n - len(fixed)) * (3 * CHUNK - 1)
    data += 3 * CHUNK + (CHUNK + 17) + CHUNK          # the sparse files' data
    data += r["large"] + 3 + 5 + 7 + max(0, r["names"] - 3) * 99
    deep = r["deep"] * (r["deep_bytes"] // 61 + 3)    # each tree's dirs and leaf
    entries = 4 + n + 3 + 1 + r["symlinks"] + r["names"] + deep + 1 + 2
    data += deep * 11
    return data + entries * 4096, entries


def sizes(entries):
    """(bytes the entries take on a disk, roughly, and how many inodes)."""
    data = 0
    for e in entries:
        if e["kind"] == "file":
            hole = sum(h[1] for h in e.get("holes") or [])
            data += max(0, e["size"] - hole) + 4096
        elif e["kind"] in ("dir", "symlink"):
            data += 4096
    return data, sum(1 for e in entries if e["kind"] != "hardlink")


def _data(fill, n):
    """N pseudo-random bytes from FILL: the same on every replay, compressible as badly as real data."""
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out += hashlib.sha256(f"{fill}:{ctr}".encode()).digest() * 64
        ctr += 1
    return bytes(out[:n])


FS_IOC_GETFLAGS, FS_IOC_SETFLAGS = 0x80086601, 0x40086602
FLAG_BITS = {"i": 0x10, "a": 0x20}


class _Dirs:
    """Directory fds under BASE, opened component by component, so paths
    past PATH_MAX (the deep tree) are made and touched with the *at calls."""

    def __init__(self, base):
        self.base = base if isinstance(base, int) else os.open(base, os.O_RDONLY | os.O_DIRECTORY)
        self.fds = {b"": self.base}

    def dir(self, rel, make=False):
        if rel in self.fds:
            return self.fds[rel]
        parent, _, name = rel.rpartition(b"/")
        pfd = self.dir(parent, make)
        if make:
            try:
                os.mkdir(name, 0o755, dir_fd=pfd)
            except FileExistsError:
                pass
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        self.fds[rel] = fd
        return fd

    def at(self, rel, make=False):
        """(fd of REL's directory, REL's last name)."""
        parent, _, name = rel.rpartition(b"/")
        return self.dir(parent, make), name

    def close(self):
        for fd in self.fds.values():
            os.close(fd)


def _open_nofollow(root, rel, make=True):
    """A directory fd of ROOT + REL, its components made where missing (MAKE)
    and each opened by fd without the kernel following a symlink.  A
    relative link is followed here, inside the source (ABRoot's srv ->
    var/srv), its target's ".." by the directories really walked; an
    absolute target (it would resolve on the host) or a ".." past ROOT is
    refused (EXDEV), so nothing is ever planted outside the source."""
    stack = [os.open(root, os.O_RDONLY | os.O_DIRECTORY)]
    todo = [n for n in rel.split("/") if n]
    hops = 0
    try:
        while todo:
            name = todo.pop(0)
            if name == ".":
                continue
            if name == "..":
                if len(stack) == 1:
                    raise OSError(errno.EXDEV, "a link's .. leads out of the source")
                os.close(stack.pop())
                continue
            fd = stack[-1]
            try:
                stack.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd))
                continue
            except FileNotFoundError:
                if not make:
                    raise
                os.mkdir(name, 0o755, dir_fd=fd)
                stack.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd))
                continue
            except OSError as x:
                if x.errno not in (errno.ELOOP, errno.ENOTDIR):
                    raise
                try:
                    target = os.readlink(name, dir_fd=fd)
                except OSError:
                    raise x from None
            hops += 1
            if hops > 40:
                raise OSError(errno.ELOOP, f"{name}: too many links")
            if target.startswith("/"):
                raise OSError(errno.EXDEV, f"{name} links to {target}, which would resolve outside the source")
            todo = [n for n in target.split("/") if n] + todo
        return stack.pop()
    finally:
        for f in stack:
            os.close(f)


def real_dir(root):
    """The host's path of ROOT + DIR as plant() reaches it (made, links inside
    the source followed): what findmnt and statvfs should be asked about.
    Linux (/proc)."""
    fd = _open_nofollow(root, DIR)
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    finally:
        os.close(fd)


def plant(root, entries):
    """Makes ENTRIES under ROOT + DIR (ROOT: the source's root, mounted).
    Returns what it couldn't make, as [(index, why)]: those are the
    harness's own problem, and the case says so."""
    import fcntl
    import stat
    try:
        dirs = _Dirs(_open_nofollow(root, DIR))
    except OSError as x:
        return [(-1, f"{DIR} under {root}: {errno.errorcode.get(x.errno, x.errno)} {x.strerror} "
                     f"(absolute links and links out of the source are never followed: nothing planted)")]
    fails, flags = [], []
    made = []
    for e in entries:
        rel = bytes.fromhex(e["path"])
        try:
            k = e["kind"]
            if k == "dir":
                dirs.dir(rel, make=True)
            else:
                fd, name = dirs.at(rel, make=True)
                if k == "file":
                    f = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600, dir_fd=fd)
                    try:
                        os.ftruncate(f, e["size"])
                        pos = 0
                        for off, ln in sorted(e.get("holes") or []) + [[e["size"], 0]]:
                            if off > pos:
                                os.pwrite(f, _data(e["fill"] + pos, off - pos), pos)
                            pos = max(pos, off + ln)
                    finally:
                        os.close(f)
                elif k == "hardlink":
                    sfd, sname = dirs.at(bytes.fromhex(e["to"]))
                    os.link(sname, name, src_dir_fd=sfd, dst_dir_fd=fd, follow_symlinks=False)
                    continue
                elif k == "symlink":
                    os.symlink(bytes.fromhex(e["to"]), name, dir_fd=fd)
                elif k == "fifo":
                    os.mkfifo(name, 0o644, dir_fd=fd)
                elif k in ("chr", "blk"):
                    os.mknod(name, (stat.S_IFCHR if k == "chr" else stat.S_IFBLK) | e.get("mode", 0o600), os.makedev(*e["rdev"]), dir_fd=fd)
            fd, name = dirs.at(rel) if rel else (dirs.base, b".")
            if "uid" in e or "gid" in e:
                os.chown(name, e.get("uid", 0), e.get("gid", 0), dir_fd=fd, follow_symlinks=False)
            if "mode" in e and k != "symlink":
                os.chmod(name, e["mode"], dir_fd=fd)
            if e.get("xattrs") or e.get("flags"):
                o = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if k == "dir" else 0) | (os.O_NONBLOCK if k == "fifo" else 0), dir_fd=fd)
                try:
                    for n, v in (e.get("xattrs") or {}).items():
                        os.setxattr(o, n, bytes.fromhex(v))
                finally:
                    os.close(o)
            if e.get("flags"):
                flags.append((rel, e))
            made.append(e)
        except OSError as x:
            fails.append((e["i"], f"{e['kind']} {bytes.fromhex(e['path'])[:60]!r}: {errno.errorcode.get(x.errno, x.errno)} {x.strerror}"))
    # Times last, deepest first (making a child moves its directory's mtime), then flags.
    for e in sorted(made, key=lambda e: -len(e["path"])):
        if e["kind"] == "hardlink":
            continue
        rel = bytes.fromhex(e["path"])
        mt = e.get("mtime_ns", 1_700_000_000 * 10**9 + e["i"])
        try:
            fd, name = dirs.at(rel)
            os.utime(name, ns=(mt, mt), dir_fd=fd, follow_symlinks=False)
        except OSError as x:
            fails.append((e["i"], f"utime: {x.strerror}"))
    for rel, e in flags:
        try:
            fd, name = dirs.at(rel)
            o = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                cur = struct.unpack("l", fcntl.ioctl(o, FS_IOC_GETFLAGS, struct.pack("l", 0)))[0]
                fcntl.ioctl(o, FS_IOC_SETFLAGS, struct.pack("l", cur | FLAG_BITS[e["flags"]]))
            finally:
                os.close(o)
        except OSError as x:
            fails.append((e["i"], f"chattr +{e['flags']}: {x.strerror}"))
    dirs.close()
    return fails


def change(root, content, round_no):
    """Between two snapshots of a --snapshot case: ROUND_NO's seeded changes
    under DIR (a file rewritten, one added, one removed)."""
    rng = random.Random(f"bs2-change:{content['seed']}:{round_no}")
    base = _open_nofollow(root, DIR)
    try:
        try:
            os.mkdir("rounds", 0o755, dir_fd=base)
        except FileExistsError:
            pass
        fd = os.open("rounds", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
    finally:
        os.close(base)
    try:
        for name, data in ((f"r{round_no}", lambda: _data(rng.randrange(1 << 30), rng.randrange(1, 300000))),
                           ("changing", lambda: _data(round_no, 1000 + round_no))):
            f = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o666, dir_fd=fd)
            try:
                b = data()
                while b:
                    b = b[os.write(f, b):]
            finally:
                os.close(f)
        try:
            os.unlink(f"r{round_no - 2}", dir_fd=fd)
        except FileNotFoundError:
            pass
    finally:
        os.close(fd)


def deep_record(root):
    """{relative path (hex): attributes} of DIR's deep trees (deep*), walked by
    directory fds: past PATH_MAX, where fsmeta.py's paths can't go."""
    import hashlib as H
    import stat as S
    out = {}
    try:
        top = _open_nofollow(root, DIR, make=False)
    except FileNotFoundError:
        return out

    def walk(fd, rel):
        for name in sorted(os.listdir(fd)):
            name = os.fsencode(name)
            if not rel and not name.startswith(b"deep"):
                continue
            st = os.lstat(name, dir_fd=fd)
            r = rel + b"/" + name if rel else name
            e = {"mode": st.st_mode, "uid": st.st_uid, "gid": st.st_gid, "mtime": st.st_mtime_ns}
            if S.S_ISREG(st.st_mode):
                o = os.open(name, os.O_RDONLY, dir_fd=fd)
                try:
                    h = H.sha256()
                    while True:
                        b = os.read(o, 1 << 20)
                        if not b:
                            break
                        h.update(b)
                    e["sha256"], e["size"] = h.hexdigest(), st.st_size
                finally:
                    os.close(o)
            out[r.hex()] = e
            if S.S_ISDIR(st.st_mode):
                sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    walk(sub, r)
                finally:
                    os.close(sub)

    try:
        walk(top, b"")
    finally:
        os.close(top)
    return out


def _deep_name(k):
    name = bytes.fromhex(k)
    return f"{name[:40]!r}...{name[-40:]!r} ({len(name)} bytes)"


def deep_compare(a, b):
    """The differences between two deep_record()s, as lines."""
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            out.append(f"{_deep_name(k)}: {a.get(k)} -> {b.get(k)}")
    return out


def _is_deep(path_hex):
    return bytes.fromhex(path_hex).split(b"/", 1)[0].startswith(b"deep")


def deep_planned(content):
    """The paths (hex, under DIR) of CONTENT's deep trees (deep*: what
    deep_record() walks) that plant() is given, after its "only"; empty
    where the case plants no deep tree.  The deep trees' entries and indexes
    are the same on every filesystem, so ext4's plan stands for all."""
    if not content or "recipe" not in content:
        return []
    return [e["path"] for e in plan(content, "ext4") if _is_deep(e["path"])]


def deep_verdict(planned, before, after):
    """(verdict, lines) for the deep tree restored, from deep_planned()'s
    PLANNED and the deep_record()s of the source (BEFORE) and the restore
    (AFTER).  The verdict:
      "none"     the case plants no deep tree: nothing to compare (an empty
                 BEFORE is then expected, not a failure)
      "missing"  something planned isn't in BEFORE: the source never had the
                 tree, so a compare would prove nothing; LINES name what's gone
      "differs"  AFTER isn't BEFORE; LINES are deep_compare()'s
      "agrees"   AFTER is BEFORE
    The decision that there is a tree to compare comes from the plan, never
    from BEFORE being empty, so neither failure stands in for the other."""
    if not planned:
        return "none", []
    gone = [k for k in planned if k not in before]
    if gone:
        return "missing", [f"{_deep_name(k)}: planned, not in the source's record" for k in gone]
    diff = deep_compare(before, after)
    return ("differs" if diff else "agrees"), diff
