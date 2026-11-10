# What ImgTest2 records of the Haiku image (typed by boot-test.py --manifest, a line
# at a time; keep each under 300 characters).  Each `mf KEY COMMAND` prints one line,
# "BKO-MF KEY VALUE", VALUE "-" when COMMAND printed nothing.  kernel, zfs, tool:* and
# pkg:* are the storage fields, whose change makes the panel due.
mf kernel 'uname -v | cut -d" " -f1'
mf image_id 'sed -n "s/.*\"image_id\": \"\([^\"]*\)\".*/\1/p" /boot/.disk/backotter.json | head -n 1'
mf bin:rustic-bko 'f=$(command -v rustic-bko) && sha256sum "$f" | cut -d" " -f1'
mf bin:bko-holt 'f=$(command -v bko-holt) && sha256sum "$f" | cut -d" " -f1'
mf tool:mkfs 'f=$(command -v mkfs) && sha256sum "$f" | cut -d" " -f1'
mf tool:mount 'f=$(command -v mount) && sha256sum "$f" | cut -d" " -f1'
mf tool:unmount 'f=$(command -v unmount) && sha256sum "$f" | cut -d" " -f1'
mf tool:diskimage 'f=$(command -v diskimage) && sha256sum "$f" | cut -d" " -f1'
mf tool:makebootable 'f=$(command -v makebootable) && sha256sum "$f" | cut -d" " -f1'
mf tool:writembr 'f=$(command -v writembr) && sha256sum "$f" | cut -d" " -f1'
mf tool:copyattr 'f=$(command -v copyattr) && sha256sum "$f" | cut -d" " -f1'
mf tool:listattr 'f=$(command -v listattr) && sha256sum "$f" | cut -d" " -f1'
mf tool:addattr 'f=$(command -v addattr) && sha256sum "$f" | cut -d" " -f1'
mf tool:catattr 'f=$(command -v catattr) && sha256sum "$f" | cut -d" " -f1'
mf tool:fs-bfs 'f=/boot/system/add-ons/kernel/file_systems/bfs; test -e "$f" && sha256sum "$f" | cut -d" " -f1'
mf tool:fs-fat 'f=/boot/system/add-ons/kernel/file_systems/fat; test -e "$f" && sha256sum "$f" | cut -d" " -f1'
mf tool:fs-exfat 'f=/boot/system/add-ons/kernel/file_systems/exfat; test -e "$f" && sha256sum "$f" | cut -d" " -f1'
mf tool:fs-ext2 'f=/boot/system/add-ons/kernel/file_systems/ext2; test -e "$f" && sha256sum "$f" | cut -d" " -f1'
mf tool:fs-btrfs 'f=/boot/system/add-ons/kernel/file_systems/btrfs; test -e "$f" && sha256sum "$f" | cut -d" " -f1'
mf tool:fs-ntfs 'f=/boot/system/add-ons/kernel/file_systems/ntfs; test -e "$f" && sha256sum "$f" | cut -d" " -f1'
