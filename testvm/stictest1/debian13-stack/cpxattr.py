# cpxattr.py SRC DST [SRC DST ...]: copy every xattr (POSIX ACLs too) of SRC
# onto DST, keeping DST's times.  The guest has no getfacl/setfacl.
import os, sys
a = sys.argv[1:]
for s, d in zip(a[::2], a[1::2]):
    if not os.path.lexists(d):
        print(f"cpxattr: no {d}", file=sys.stderr)
        continue
    st = os.lstat(d)
    for n in os.listxattr(s, follow_symlinks=False):
        os.setxattr(d, n, os.getxattr(s, n, follow_symlinks=False), follow_symlinks=False)
    os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)
