# The non-root fixture's xattr and ACLs, set by their owner (no setfacl on the guest)
import os, struct
X = 0xffffffff
def acl(e): return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", t, p, i) for t, p, i in e)
os.setxattr("d/f", "user.nr", b"1")
os.setxattr("d/f", "system.posix_acl_access", acl([(1, 6, X), (2, 4, 1234), (4, 4, X), (0x10, 4, X), (0x20, 0, X)]))
os.setxattr("d", "system.posix_acl_default", acl([(1, 7, X), (2, 5, 1234), (4, 5, X), (0x10, 5, X), (0x20, 5, X)]))
