import lzma, sys
def plain():
    x = [12345]
    def nxt():
        x[0] = (x[0] * 1103515245 + 12345) & 0xffffffff
        return (x[0] >> 16) & 0x7fff
    words = [b"grub", b"prefix", b"(,gpt3)", b"/@/boot/grub", b"normal.mod", b"biosdisk", b"part_gpt", b"btrfs", b"\0\0\0\0", b"mimg"]
    out = bytearray()
    while len(out) < 70000:
        r = nxt()
        k = r % 7
        if k in (0, 1, 2):
            out += words[(r // 7) % len(words)]
        elif k == 3:
            out.append((r >> 3) & 0xff)
        elif k == 4 and len(out) > 5000:
            back = 1 + (nxt() * 3) % min(len(out) - 1, 60000)
            n = 2 + (nxt() % 300)
            frm = len(out) - back
            for i in range(n):
                out.append(out[frm + i % back])
        else:
            out.append(32)
    return bytes(out[:70000])
p = plain()
c = lzma.compress(p, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "lc": 3, "lp": 0, "pb": 2, "dict_size": 1 << 16}])
assert lzma.decompress(c, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "lc": 3, "lp": 0, "pb": 2, "dict_size": 1 << 16}]) == p
open(sys.argv[1], "wb").write(c)
import hashlib; print(len(c), hashlib.sha256(p).hexdigest()[:16])
