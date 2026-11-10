# What ImgTest2 records of the OpenBSD image (typed by boot-test.py --manifest, a line
# at a time; keep each under 300 characters).  Each `mf KEY COMMAND` prints one line,
# "BKO-MF KEY VALUE", VALUE "-" when COMMAND printed nothing.  kernel, zfs, tool:* and
# pkg:* are the storage fields, whose change makes the panel due.
mf kernel 'sysctl -n kern.version | head -n 1'
mf image_id 'sed -n "s/.*\"image_id\": \"\([^\"]*\)\".*/\1/p" /.disk/backotter.json | head -n 1'
mf bin:restic-bko 'f=$(command -v restic-bko) && sha256 -q "$f"'
mf bin:rustic-bko 'f=$(command -v rustic-bko) && sha256 -q "$f"'
mf bin:bko-holt 'f=$(command -v bko-holt) && sha256 -q "$f"'
mf tool:fdisk 'f=$(command -v fdisk) && sha256 -q "$f"'
mf tool:disklabel 'f=$(command -v disklabel) && sha256 -q "$f"'
mf tool:newfs 'f=$(command -v newfs) && sha256 -q "$f"'
mf tool:newfs_msdos 'f=$(command -v newfs_msdos) && sha256 -q "$f"'
mf tool:fsck_ffs 'f=$(command -v fsck_ffs) && sha256 -q "$f"'
mf tool:fsck_msdos 'f=$(command -v fsck_msdos) && sha256 -q "$f"'
mf tool:mount_msdos 'f=$(command -v mount_msdos) && sha256 -q "$f"'
mf tool:bioctl 'f=$(command -v bioctl) && sha256 -q "$f"'
mf tool:vnconfig 'f=$(command -v vnconfig) && sha256 -q "$f"'
mf tool:installboot 'f=$(command -v installboot) && sha256 -q "$f"'
mf tool:growfs 'f=$(command -v growfs) && sha256 -q "$f"'
mf tool:dump 'f=$(command -v dump) && sha256 -q "$f"'
mf tool:restore 'f=$(command -v restore) && sha256 -q "$f"'
