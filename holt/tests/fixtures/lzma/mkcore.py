import lzma, struct, sys
# A small GRUB i386-pc core.img laid out as grub-mkimage makes one (GRUB 2.12, guix15-bios's):
# diskboot.img's sector, lzma_decompress.img (here 0x300 bytes of filler with its header:
# compressed size at 0x08, uncompressed at 0x0c, Reed-Solomon redundancy at 0x10, the part
# it doesn't cover at 0x14), the LZMA1 stream (lc=3 lp=0 pb=2, no end marker needed: the
# size is known), then the redundancy (filler here).  The kernel holds a stray "mimg" (code
# that compares with the magic, as the real kernel's does) before the module info.
prefix = sys.argv[2].encode()
kernel = bytes((i * 7) & 0xff for i in range(6000)) + b"mimg\x75\x03\x03\x40\x08\xc3\x55\x89" + bytes(range(256)) * 8
mods = struct.pack("<II", 0, 8 + 96) + b"part_gpt" * 12
mods += struct.pack("<II", 3, 8 + 20) + prefix + b"\0" * (20 - len(prefix))
info = b"mimg" + struct.pack("<II", 12, 12 + len(mods))
payload = kernel + info + mods
comp = lzma.compress(payload, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "lc": 3, "lp": 0, "pb": 2, "dict_size": 1 << 16}])
red = 700
dec = bytearray(b"\xea" * 0x300)
struct.pack_into("<IIII", dec, 8, len(comp), len(payload), red, 0x268)
core = b"\x52\x56\xbe\x1b" + b"\0" * 508 + bytes(dec) + comp + bytes((i * 13 + 5) & 0xff for i in range(red))
open(sys.argv[1], "wb").write(core)
print(len(core), len(comp), len(payload))
