# For make-freebsd.sh --geli-ufs (docs/design/geli2.md): sourced by
# installerconfig's preamble from the install media's /etc/bko-geli-ufs.sh,
# in the installer's own shell.  bsdinstall's guided UFS has no "Encrypt"
# choice, so this stands in for its partitioning step and makes the GELI
# providers itself; the scripted install then goes on as usual, on the
# mounted tree.  The layout (vtbd0, GPT, UEFI):
#
#   p1  efi, 260 MiB, label efiboot0, /boot/efi (bootconfig puts loader.efi
#       on it, as for a guided install)
#   p2  freebsd-swap, 1 GiB, plain
#   p3  freebsd-ufs, label rootfs0: geli init -bg -e AES-XTS -l 256 -s 4096
#       with the passphrase in key slot 0 and a second one in slot 1
#       (geli setkey -n 1); newfs -U -j on vtbd0p3.eli, mounted /
#   p4  freebsd-ufs, the rest (about 1 GiB), label data0: geli init
#       -a HMAC/SHA256 -e AES-XTS -l 128 -P -K /etc/geli/p4.key, opened by
#       that key file alone; filled with zeros once attached (each sector's
#       checksum must be written before it can be read), newfs -U on
#       vtbd0p4.eli, mounted /data; rc.d/geli attaches it at boot, with the
#       key file read from the root (rc.conf geli_devices)
#
# The passphrases and the key file are on the install media, made by
# make-freebsd.sh from the guest dir's console-passphrase,
# console-passphrase-slot1 and geli-p4.key: /etc/bko-geli/pass0, pass1 and
# p4.key.  They go to geli as files (-J, -K), never on a command line or
# into the log.

BKO_GELI=/etc/bko-geli BKO_DISK=vtbd0

bko_geli_run() {
	f_dprintf "BackOtter geli-ufs: %s" "$*"
	"$@" || { f_dprintf "BackOtter geli-ufs: failed: %s" "$*"; return 1; }
}

# The partitioning step: partitions, providers, file systems, and the fstab
# that bsdinstall's mount step and the new system both read.
bko_geli_ufs_part() {
	local d=$BKO_DISK total root_mib
	bko_geli_run gpart create -s gpt $d || return 1
	bko_geli_run gpart add -a 1m -l efiboot0 -t efi -s 260m $d || return 1
	bko_geli_run newfs_msdos -F 32 -c 1 /dev/gpt/efiboot0 || return 1
	bko_geli_run gpart add -a 1m -l swap0 -t freebsd-swap -s 1g $d || return 1
	# p3 all but p4's 1 GiB (and a little for the alignment and GPT's
	# backup at the end), p4 the rest.
	total=$(diskinfo $d | awk '{ print int($3 / 1048576) }')
	root_mib=$((total - 260 - 1024 - 1024 - 4))
	bko_geli_run gpart add -a 1m -l rootfs0 -t freebsd-ufs -s ${root_mib}m $d || return 1
	bko_geli_run gpart add -a 1m -l data0 -t freebsd-ufs $d || return 1

	kldload -n aesni 2>/dev/null || :
	kldload -n geom_eli || return 1
	# The root: GELIBOOT, so loader.efi asks for the passphrase and reads
	# UFS from the provider; slot 1 gets the second passphrase.
	bko_geli_run geli init -bg -e AES-XTS -l 256 -s 4096 -J $BKO_GELI/pass0 ${d}p3 || return 1
	bko_geli_run geli attach -j $BKO_GELI/pass0 ${d}p3 || return 1
	bko_geli_run geli setkey -n 1 -J $BKO_GELI/pass1 ${d}p3 || return 1
	bko_geli_run newfs -U -j /dev/${d}p3.eli || return 1
	# /data: authenticated, a key file and no passphrase.
	bko_geli_run geli init -a HMAC/SHA256 -e AES-XTS -l 128 -P -K $BKO_GELI/p4.key ${d}p4 || return 1
	bko_geli_run geli attach -p -k $BKO_GELI/p4.key ${d}p4 || return 1
	# dd stops at the provider's end with an error; only that one is fine.
	f_dprintf "BackOtter geli-ufs: dd if=/dev/zero of=/dev/${d}p4.eli bs=1m"
	if ! dd if=/dev/zero of=/dev/${d}p4.eli bs=1m 2>/tmp/bko-dd.log; then
		grep -q 'end of device' /tmp/bko-dd.log || { cat /tmp/bko-dd.log; return 1; }
	fi
	f_dprintf "BackOtter geli-ufs: %s" "$(tail -1 /tmp/bko-dd.log)"
	bko_geli_run newfs -U /dev/${d}p4.eli || return 1

	printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
		'# Device' Mountpoint FStype Options Dump 'Pass#' \
		/dev/${d}p3.eli / ufs rw 1 1 \
		/dev/gpt/efiboot0 /boot/efi msdosfs rw 2 2 \
		/dev/${d}p2 none swap sw 0 0 \
		/dev/${d}p4.eli /data ufs rw 2 2 >"$PATH_FSTAB"
	# What the new system needs for them: the loader's GELI module, and
	# rc.d/geli for p4 (bsdinstall's config step adds these fragments to
	# its loader.conf and rc.conf).
	printf '%s\n' 'aesni_load="YES"' 'geom_eli_load="YES"' >"$BSDINSTALL_TMPBOOT/loader.conf.geli"
	printf '%s\n' "geli_devices=\"${d}p4\"" \
		"geli_${d}p4_flags=\"-p -k /etc/geli/p4.key\"" >"$BSDINSTALL_TMPETC/rc.conf.geli"
}

# After the mount step: p4's key file on the root, and a few known files on
# /data for HoltTest to find again.
bko_geli_ufs_mounted() {
	local c=$BSDINSTALL_CHROOT
	bko_geli_run mkdir -p -m 700 $c/etc/geli || return 1
	bko_geli_run install -m 600 $BKO_GELI/p4.key $c/etc/geli/p4.key || return 1
	bko_geli_run mkdir -p $c/data/geli2 || return 1
	echo "BackOtter geli2: a known file on /data (vtbd0p4.eli)" >$c/data/geli2/known.txt
	jot 20000 >$c/data/geli2/numbers.txt
	dd if=/dev/zero bs=64k count=16 2>/dev/null | tr '\0' 'g' >$c/data/geli2/g.bin
}

bsdinstall() {
	case $1 in
	scriptedpart) bko_geli_ufs_part ;;
	mount) command bsdinstall mount && bko_geli_ufs_mounted ;;
	*) command bsdinstall "$@" ;;
	esac
}
