# What ImgTest2 records of the NetBSD image (typed by boot-test.py --manifest, a line
# at a time; keep each under 300 characters).  Each `mf KEY COMMAND` prints one line,
# "BKO-MF KEY VALUE", VALUE "-" when COMMAND printed nothing.  kernel, zfs, tool:* and
# pkg:* are the storage fields, whose change makes the panel due.
mf kernel 'uname -v'
mf image_id 'sed -n "s/.*\"image_id\": \"\([^\"]*\)\".*/\1/p" /.disk/backotter.json | head -n 1'
mf bin:restic-bko 'f=$(command -v restic-bko) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf bin:rustic-bko 'f=$(command -v rustic-bko) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf bin:bko-holt 'f=$(command -v bko-holt) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:gpt 'f=$(command -v gpt) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:dkctl 'f=$(command -v dkctl) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:disklabel 'f=$(command -v disklabel) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:fdisk 'f=$(command -v fdisk) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:newfs 'f=$(command -v newfs) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:newfs_msdos 'f=$(command -v newfs_msdos) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:fsck_ffs 'f=$(command -v fsck_ffs) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:mount_msdos 'f=$(command -v mount_msdos) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:zpool 'f=$(command -v zpool) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:zfs 'f=$(command -v zfs) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:lvm 'f=$(command -v lvm) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:raidctl 'f=$(command -v raidctl) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:cgdconfig 'f=$(command -v cgdconfig) && cksum -a sha256 "$f" | sed "s/.*= //"'
mf tool:pax 'f=$(command -v pax) && cksum -a sha256 "$f" | sed "s/.*= //"'
