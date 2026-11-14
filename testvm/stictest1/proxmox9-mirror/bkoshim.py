#!/usr/bin/python3
"""tumbleweed's bkoshim.py; proxmox9 and proxmox9-mirror use it as
setfattr and setfacl (Proxmox VE has neither the attr nor the acl package;
libcap2-bin gives setcap).
Stand-ins for the tools tumbleweed lacks (no attr, acl or libcap-progs
packages): called as setfattr (-h, -n NAME, -v VALUE, -x NAME), setfacl
(-m/-d ENTRY[,ENTRY], tags u g m o, numeric or named ids), or as
"setcapraw PATH v2|v3 CAPNUM[,CAPNUM] [ROOTID]" (effective + permitted)."""
import grp, os, pwd, struct, sys

me = os.path.basename(sys.argv[0])
a = sys.argv[1:]


def val(v: str) -> bytes:
    if v.startswith("0x"):
        return bytes.fromhex(v[2:])
    if len(v) >= 2 and v[0] == v[-1] == '"':
        v = v[1:-1]
    return v.encode("utf-8", "surrogateescape")


if me == "setfattr":
    follow, name, value, remove = True, None, b"", None
    i = 0
    while i < len(a) and a[i].startswith("-"):
        if a[i] == "-h": follow = False
        elif a[i] == "-n": name = a[i + 1]; i += 1
        elif a[i] == "-v": value = val(a[i + 1]); i += 1
        elif a[i] == "-x": remove = a[i + 1]; i += 1
        i += 1
    rc = 0
    for p in a[i:]:
        try:
            if remove: os.removexattr(p, remove, follow_symlinks=follow)
            else: os.setxattr(p, name, value, follow_symlinks=follow)
        except OSError as e:
            print(f"setfattr: {p}: {e.strerror}", file=sys.stderr); rc = 1
    sys.exit(rc)

if me == "setfacl":
    TAG = {"u": 1, "user": 1, "g": 4, "group": 4, "m": 16, "mask": 16, "o": 32, "other": 32}
    U = 0xffffffff
    default, specs, files = False, [], []
    i = 0
    while i < len(a):
        if a[i] == "-d": default = True
        elif a[i] == "-m": specs += a[i + 1].split(","); i += 1
        else: files.append(a[i])
        i += 1

    def perm(s): return sum(b for c, b in zip("rwx", (4, 2, 1)) if c in s)

    def read(p, n, mode):
        try:
            b = os.getxattr(p, n)
            return {(t, q): pm for t, pm, q in struct.iter_unpack("<HHI", b[4:])}
        except OSError:
            if n.endswith("default"): return {}
            return {(1, U): (mode >> 6) & 7, (4, U): (mode >> 3) & 7, (32, U): mode & 7}
    rc = 0
    for p in files:
        n = "system.posix_acl_default" if default else "system.posix_acl_access"
        e = read(p, n, os.stat(p).st_mode)
        if default and not e:
            e = read(p, "system.posix_acl_access", os.stat(p).st_mode)
            e = {k: v for k, v in e.items() if k[0] in (1, 4, 16, 32) and k[1] == U}
        for s in specs:
            d = s.startswith("d:")
            if d: s = s[2:]
            t, q, pm = (s.split(":") + ["", ""])[:3]
            t = TAG[t]
            if q:
                t = {1: 2, 4: 8}[t]
                q = int(q) if q.isdigit() else (pwd.getpwnam(q).pw_uid if t == 2 else grp.getgrnam(q).gr_gid)
            else:
                q = U
            e[(t, q)] = perm(pm)
        if any(k[0] in (2, 8) for k in e) and not any(s.split(":")[0] in ("m", "mask") for s in specs):
            e[(16, U)] = 0
            for k, v in e.items():
                if k[0] in (2, 4, 8): e[(16, U)] |= v
        b = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", t, pm, q) for (t, q), pm in sorted(e.items()))
        try:
            os.setxattr(p, n, b)
        except OSError as x:
            print(f"setfacl: {p}: {x.strerror}", file=sys.stderr); rc = 1
    sys.exit(rc)

if me == "setcapraw":
    p, ver, caps = a[0], a[1], [int(c) for c in a[2].split(",")]
    lo = sum(1 << c for c in caps if c < 32); hi = sum(1 << (c - 32) for c in caps if c >= 32)
    if ver == "v2":
        b = struct.pack("<IIIII", 0x02000001, lo, 0, hi, 0)
    elif ver == "v3":
        b = struct.pack("<IIIIII", 0x03000001, lo, 0, hi, 0, int(a[3]))
    else:  # permitted only, not effective
        b = struct.pack("<IIIII", 0x02000000, lo, 0, hi, 0)
    os.setxattr(p, "security.capability", b)
    sys.exit(0)
print(f"bkoshim: unknown name {me}", file=sys.stderr)
sys.exit(2)
