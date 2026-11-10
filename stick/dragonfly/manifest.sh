# What ImgTest2 records of the DragonFly image (typed by boot-test.py --manifest, a line
# at a time; keep each under 300 characters).  Each `mf KEY COMMAND` prints one line,
# "BKO-MF KEY VALUE", VALUE "-" when COMMAND printed nothing.  kernel, zfs, tool:* and
# pkg:* are the storage fields, whose change makes the panel due.
mf kernel 'sysctl -n kern.version | head -n 1'
mf image_id 'sed -n "s/.*\"image_id\": \"\([^\"]*\)\".*/\1/p" /.disk/backotter.json | head -n 1'
mf bin:restic-bko 'f=$(command -v restic-bko) && sha256 -q "$f"'
mf bin:rustic-bko 'f=$(command -v rustic-bko) && sha256 -q "$f"'
mf bin:bko-holt 'f=$(command -v bko-holt) && sha256 -q "$f"'
mf tool:fdisk 'f=$(command -v fdisk) && sha256 -q "$f"'
mf tool:disklabel64 'f=$(command -v disklabel64) && sha256 -q "$f"'
mf tool:gpt 'f=$(command -v gpt) && sha256 -q "$f"'
mf tool:newfs 'f=$(command -v newfs) && sha256 -q "$f"'
mf tool:newfs_hammer2 'f=$(command -v newfs_hammer2) && sha256 -q "$f"'
mf tool:hammer2 'f=$(command -v hammer2) && sha256 -q "$f"'
mf tool:newfs_msdos 'f=$(command -v newfs_msdos) && sha256 -q "$f"'
mf tool:mount_msdos 'f=$(command -v mount_msdos) && sha256 -q "$f"'
mf tool:cryptsetup 'f=$(command -v cryptsetup) && sha256 -q "$f"'
mf tool:lvm 'f=$(command -v lvm) && sha256 -q "$f"'
mf tool:dmsetup 'f=$(command -v dmsetup) && sha256 -q "$f"'
mf tool:camcontrol 'f=$(command -v camcontrol) && sha256 -q "$f"'
mf tool:cpdup 'f=$(command -v cpdup) && sha256 -q "$f"'
