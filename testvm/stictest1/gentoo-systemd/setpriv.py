#!/usr/bin/env python3
"""setpriv for a guest whose util-linux has none (Gentoo's, without
USE=caps, which needs libcap-ng): only what nonroot-inner.sh uses,
    setpriv --reuid=UID --regid=GID --clear-groups COMMAND [ARG...]
no supplementary groups, then the real, effective and saved ids, then exec."""
import os, sys
a, uid, gid = sys.argv[1:], None, None
while a and a[0].startswith("--"):
    o = a.pop(0)
    if o.startswith("--reuid="): uid = int(o.split("=", 1)[1])
    elif o.startswith("--regid="): gid = int(o.split("=", 1)[1])
    elif o != "--clear-groups": sys.exit(f"setpriv (gentoo's shim): {o} isn't supported")
if not a: sys.exit("setpriv (gentoo's shim): no command")
os.setgroups([])
if gid is not None: os.setresgid(gid, gid, gid)
if uid is not None: os.setresuid(uid, uid, uid)
os.execvp(a[0], a)
