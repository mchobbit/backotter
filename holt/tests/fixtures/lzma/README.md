# LZMA fixtures (src/lzma.rs, src/load/grubprefix.rs)

Made on the Mac with Python's `lzma` (liblzma), so the decoder is checked
against an encoder that isn't ours:

| File | What it is | Made by |
|---|---|---|
| `plain.lzma1` | raw LZMA1 (lc=3 lp=0 pb=2, 64 KiB dictionary, GRUB's mkimage settings; with liblzma's end marker) of the 70,000 bytes `lzma.rs`'s `plain()` makes (the same LCG as `mkplain.py`'s) | `python3 mkplain.py plain.lzma1` |
| `core-gpt3.img` | a small GRUB i386-pc core.img laid out as grub-mkimage makes one: diskboot.img's sector, lzma_decompress.img's header (compressed size 0x08, uncompressed 0x0c, Reed-Solomon redundancy 0x10), the LZMA1 kernel and modules (a stray "mimg" in the kernel, then the module info and a prefix module `(,gpt3)/@/boot/grub`), then the redundancy | `python3 mkcore.py core-gpt3.img '(,gpt3)/@/boot/grub'` |
| `core-root.img` | the same with the prefix `($root)/boot/grub` (no partition by number) | `python3 mkcore.py core-root.img '($root)/boot/grub'` |

The layout is that of guix15-bios's real core.img (GRUB 2.12 from Guix
1.5.0, read from its BIOS boot partition on chopin on 2026-11-24): 146,432
bytes, compressed 69,791, uncompressed 146,523, redundancy 73,281, the
module info 33,151 bytes into the kernel, the prefix `(,gpt3)/@/boot/grub`.
That image isn't kept here (boot code is left out of the corpus too);
`grubprefix.rs`'s ignored test `real_core_img` reads one named by
`BKO_CORE_IMG`.
