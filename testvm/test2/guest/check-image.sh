# GuestTest2's image checks (contract 2.1 step 2), for the pre-update boot, as root over
# ssh sh -s.  guest/job.sh puts RULES="S-apt L-grub K-deb ..." in front; it prints sections
# (=== file PATH, === cmd NAME) of what those rules need, and guest_judge.judge_image
# decides on the host.  Direct reads only: nothing here changes the guest.  POSIX sh,
# busybox and the BSDs' included.
PATH=/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/usr/gnu/bin:/usr/sbin:/usr/bin:/sbin:/bin:/boot/system/bin:$PATH
export PATH
has_rule() { case " $RULES " in *" $1 "*) return 0 ;; esac; return 1; }
qt() { sed 's/^===/= ==/'; }
emit() { for f in "$@"; do [ -f "$f" ] && [ -r "$f" ] || continue; echo "=== file $f"; qt <"$f"; echo; done; }
cmd() { echo "=== cmd $1"; }

cmd image-rules; echo "$RULES"
emit /etc/os-release
# --- package sources
has_rule S-apt && emit /etc/apt/sources.list /etc/apt/sources.list.d/*
has_rule S-dnf && emit /etc/yum.repos.d/fedora-updates.repo /etc/yum.repos.d/rocky*.repo
has_rule S-pac && emit /etc/pacman.d/mirrorlist /etc/pacman.d/endeavouros-mirrorlist /etc/pacman.d/cachyos-mirrorlist /etc/pacman.d/chaotic-mirrorlist
has_rule S-zyp && emit /etc/zypp/repos.d/*.repo
has_rule S-moss && emit /etc/moss/repo.d/*.kdl /etc/moss/system-model.kdl
has_rule S-apk && emit /etc/apk/repositories
has_rule S-xbps && emit /usr/share/xbps.d/* /etc/xbps.d/*
has_rule S-nix && emit /etc/nixos/configuration.nix
has_rule S-portage && emit /etc/portage/make.conf /etc/portage/binrepos.conf /etc/portage/binrepos.conf/* \
    /etc/portage/repos.conf /etc/portage/repos.conf/* /usr/share/portage/config/repos.conf
if has_rule S-fbsd || has_rule S-gbsd || has_rule S-dfly; then
    emit /etc/pkg/*.conf /usr/local/etc/pkg/repos/*.conf
fi
if has_rule S-fbsd; then
    cmd pkgbase
    if pkg -N >/dev/null 2>&1 && pkg info -e FreeBSD-runtime; then echo yes; else echo no; fi
fi
has_rule S-hbsd && emit /etc/hbsd-update.conf
has_rule S-nbsd && emit /usr/pkg/etc/pkgin/repositories.conf /usr/pkg/etc/sysupgrade.conf
has_rule S-obsd && emit /etc/installurl
if has_rule S-ips; then cmd pkg-publisher; pkg publisher -H 2>&1 | qt; fi
if has_rule S-haiku; then cmd pkgman-repos; pkgman list-repos 2>&1 | qt; fi
# Solus (solus.md): "NAME [active|inactive]", then the URL indented; -N: no colour codes.
if has_rule S-eopkg; then cmd eopkg-lr; eopkg lr -N 2>&1 | qt; fi
# Slackware (slackware.md): slackpkg's mirrors (it takes the one uncommented line) and its
# CHECKGPG and CHECKMD5 settings.
if has_rule S-slackpkg; then
    cmd slackpkg-mirrors; grep -v '^[[:space:]]*#' /etc/slackpkg/mirrors 2>/dev/null | grep . | qt
    cmd slackpkg-conf; grep -E '^[[:space:]]*(CHECKGPG|CHECKMD5)=' /etc/slackpkg/slackpkg.conf 2>/dev/null | qt
fi
# --- the loader's default entry
if has_rule L-grub; then
    emit /boot/grub/grub.cfg /boot/grub2/grub.cfg /grub/grub.cfg /boot/grub/grubenv /boot/grub2/grubenv /etc/default/grub
fi
if has_rule L-bls; then
    emit /boot/grub2/grubenv /boot/grub/grubenv /etc/default/grub
    cmd bls-entries; ls -1 /boot/loader/entries 2>/dev/null | qt
fi
if has_rule L-sd; then
    emit /boot/efi/loader/loader.conf /efi/loader/loader.conf /boot/loader/loader.conf
    cmd sd-entries
    for d in /boot/efi /efi /boot; do
        [ -d "$d/loader/entries" ] || continue
        ls -1 "$d/loader/entries" 2>/dev/null | qt
        break
    done
fi
has_rule L-limine && emit /boot/limine.conf
# Proxmox VE (proxmox.md): proxmox-boot-tool's ESPs, the IDs in /etc/kernel/proxmox-boot-uuids,
# which nothing mounts: each read through a read-only mount of its own here (or where it is
# mounted).  "boot K" for each /boot/vmlinuz-K-pve; per ESP "ID mode systemd-boot|grub",
# "ID kernel K" for each EFI/proxmox/K/vmlinuz-K ("ID kernel K no-initrd" without its
# initrd.img-K), "ID entry NAME" for each loader/entries/NAME.conf, "ID default PATTERN" from
# loader/loader.conf; "ID missing" (no such device) or "ID unreadable" (no read-only mount).
if has_rule L-pve; then
    emit /etc/kernel/proxmox-boot-uuids
    cmd pve-esps
    for k in /boot/vmlinuz-*-pve; do [ -e "$k" ] && echo "boot ${k#/boot/vmlinuz-}"; done
    grep -v '^[[:space:]]*#' /etc/kernel/proxmox-boot-uuids 2>/dev/null | while read -r u _; do
        [ -n "$u" ] || continue
        dev=/dev/disk/by-uuid/$u
        if [ ! -e "$dev" ]; then echo "$u missing"; continue; fi
        m=$(findmnt -n -o TARGET -S "UUID=$u" 2>/dev/null | head -n 1) d=
        if [ -z "$m" ] && d=$(mktemp -d) &&
            mount -t vfat -o ro,nodev,nosuid,noexec "$dev" "$d" 2>/dev/null; then m=$d; fi
        if [ -z "$m" ]; then echo "$u unreadable"; [ -z "$d" ] || rmdir "$d"; continue; fi
        if [ -e "$m/EFI/systemd/systemd-bootx64.efi" ]; then echo "$u mode systemd-boot"; else echo "$u mode grub"; fi
        for f in "$m"/EFI/proxmox/*/vmlinuz-*; do
            [ -e "$f" ] || continue
            k=${f##*/vmlinuz-}
            if [ -e "${f%/*}/initrd.img-$k" ]; then echo "$u kernel $k"; else echo "$u kernel $k no-initrd"; fi
        done
        for f in "$m"/loader/entries/*.conf; do [ -e "$f" ] && { n=${f##*/}; echo "$u entry ${n%.conf}"; }; done
        sed -n 's/^[[:space:]]*default[[:space:]][[:space:]]*\([^[:space:]]*\).*/\1/p' "$m/loader/loader.conf" 2>/dev/null |
            sed "s|^|$u default |"
        if [ -n "$d" ]; then umount "$d" 2>/dev/null; rmdir "$d"; fi
    done
fi
# Solus (solus.md): clr-boot-manager's ESP, which nothing mounts at boot (fstab leaves /boot
# out): read where it is mounted on /boot, or through a read-only mount of its own here (the
# partition systemd-boot's LoaderDevicePartUUID names, else the disk's ESP type), not CBM's
# read-write mount-boot.  "file REL" for each file, "sha256 HASH REL" and "kind REL
# shim|systemd-boot|other" for each .efi, "entry NAME linux|initrd PATH" for each
# loader/entries/NAME.conf, "default PATTERN" from loader.conf, "kernel NAME" for each
# /usr/lib64/kernel/com.solus-project.* (what CBM keeps); "esp unreadable" with no ESP.
if has_rule L-cbm; then
    cmd cbm-esp
    for k in /usr/lib64/kernel/com.solus-project.*; do [ -e "$k" ] && echo "kernel ${k##*/}"; done
    m= d=
    [ "$(findmnt -n -o FSTYPE /boot 2>/dev/null)" = vfat ] && m=/boot
    if [ -z "$m" ]; then
        v=/sys/firmware/efi/efivars/LoaderDevicePartUUID-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f
        u=$(tail -c +5 "$v" 2>/dev/null | tr -d '\000' | tr 'A-F' 'a-f')
        dev=
        [ -n "$u" ] && [ -e "/dev/disk/by-partuuid/$u" ] && dev=/dev/disk/by-partuuid/$u
        [ -n "$dev" ] || dev=$(lsblk -nrpo PATH,PARTTYPE 2>/dev/null |
            awk 'tolower($2) == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b" { print $1; exit }')
        if [ -n "$dev" ] && d=$(mktemp -d) &&
            mount -t vfat -o ro,nodev,nosuid,noexec "$dev" "$d" 2>/dev/null; then m=$d; fi
    fi
    if [ -z "$m" ]; then
        echo "esp unreadable"
    else
        (cd "$m" && find . -type f | sed 's|^\./||' | LC_ALL=C sort) | while read -r f; do
            echo "file $f"
            case $f in
            *.efi | *.EFI)
                echo "sha256 $(sha256sum "$m/$f" | awk '{ print $1 }') $f"
                if grep -aq 'LoaderInfo: systemd-boot' "$m/$f"; then echo "kind $f systemd-boot"
                elif grep -aq 'UEFI SHIM' "$m/$f"; then echo "kind $f shim"
                else echo "kind $f other"; fi ;;
            esac
        done
        for f in "$m"/loader/entries/*.conf; do
            [ -e "$f" ] || continue
            n=${f##*/}
            sed -n -e 's/^[[:space:]]*linux[[:space:]][[:space:]]*\([^[:space:]]*\).*/linux \1/p' \
                -e 's/^[[:space:]]*initrd[[:space:]][[:space:]]*\([^[:space:]]*\).*/initrd \1/p' "$f" |
                sed "s|^|entry ${n%.conf} |"
        done
        sed -n 's/^[[:space:]]*default[[:space:]][[:space:]]*\([^[:space:]]*\).*/default \1/p' "$m/loader/loader.conf" 2>/dev/null
    fi
    if [ -n "$d" ]; then umount "$d" 2>/dev/null; rmdir "$d"; fi
fi
# Slackware's LILO (slackware.md): lilo.conf, then "map yes|no" (/boot/map, which lilo
# writes), "mbr lilo|other|unreadable" (LILO's signature at offset 6 of the boot= device's
# first sector), "image PATH yes|no" for each image=, and "fstab-root DEV", fstab's / device
# (Slackware's fstab and lilo.conf name devices, not UUIDs).
if has_rule L-lilo; then
    emit /etc/lilo.conf
    cmd lilo-state
    if [ -e /boot/map ]; then echo "map yes"; else echo "map no"; fi
    b=$(sed -n 's/^[[:space:]]*boot[[:space:]]*=[[:space:]]*\([^[:space:]]*\).*/\1/p' /etc/lilo.conf 2>/dev/null | head -n 1)
    if [ -n "$b" ] && [ -r "$b" ]; then
        if [ "$(dd if="$b" bs=512 count=1 2>/dev/null | od -A n -t x1 -j 6 -N 4 | tr -d ' \n')" = 4c494c4f ]; then
            echo "mbr lilo"
        else echo "mbr other"; fi
    else echo "mbr unreadable"; fi
    for i in $(sed -n 's/^[[:space:]]*image[[:space:]]*=[[:space:]]*\([^[:space:]]*\).*/\1/p' /etc/lilo.conf 2>/dev/null); do
        if [ -e "$i" ]; then echo "image $i yes"; else echo "image $i no"; fi
    done
    awk '$1 !~ /^#/ && $2 == "/" { print "fstab-root " $1; exit }' /etc/fstab 2>/dev/null
fi
# Slackware's ELILO (slackware.md): the ESP at /boot/efi (fstab's), "sha256 HASH DIR/NAME" for
# each file in EFI/Slackware (eliloconfig's) and EFI/BOOT (the fallback copy), "conf LINE" for
# EFI/Slackware/elilo.conf, "fstab-root DEV"; "esp unmounted" when nothing is at /boot/efi.
if has_rule L-elilo; then
    cmd elilo-esp
    awk '$1 !~ /^#/ && $2 == "/" { print "fstab-root " $1; exit }' /etc/fstab 2>/dev/null
    m=$(findmnt -n -o TARGET /boot/efi 2>/dev/null)
    if [ -z "$m" ]; then
        echo "esp unmounted"
    else
        for d in EFI/Slackware EFI/BOOT; do
            for f in "$m/$d"/*; do
                [ -f "$f" ] && echo "sha256 $(sha256sum "$f" | awk '{ print $1 }') $d/${f##*/}"
            done
        done
        [ -r "$m/EFI/Slackware/elilo.conf" ] && sed 's/^/conf /' "$m/EFI/Slackware/elilo.conf"
    fi
fi
# ZFSBootMenu (zfsbootmenu.md): ZBM's EFI images at /boot/efi, "image NAME SHA256" for each
# EFI/ZBM/*.EFI ("esp unmounted" when nothing is there); / ("root DATASET"), its pool's bootfs,
# uname -r and hostid; and the pool's boot environments, the datasets with mountpoint=/, each
# read where it is mounted (the booted one) or through a read-only mount of its own here (as
# bko-holt's dump does): "be DATASET kernel K" for each /boot/vmlinuz-K ("... no-initrd" without
# its initramfs-K.img), "be DATASET none" with none, "be DATASET unreadable" (no read-only mount).
if has_rule L-zbm; then
    cmd zbm
    m=$(findmnt -n -o TARGET /boot/efi 2>/dev/null)
    if [ -z "$m" ]; then
        echo "esp unmounted"
    else
        for f in "$m"/EFI/ZBM/*; do
            case $f in
            *.EFI | *.efi) [ -f "$f" ] && echo "image ${f##*/} $(sha256sum "$f" | awk '{ print $1 }')" ;;
            esac
        done
    fi
    root=$(findmnt -n -o SOURCE,FSTYPE / 2>/dev/null | awk '$2 == "zfs" { print $1 }')
    echo "root ${root:--}"
    echo "running $(uname -r)"
    echo "hostid $(hostid 2>/dev/null)"
    if [ -n "$root" ]; then
        pool=${root%%/*}
        echo "bootfs $(zpool get -H -o value bootfs "$pool" 2>/dev/null)"
        zfs list -H -o name,mountpoint -t filesystem -r "$pool" 2>/dev/null | awk -F '\t' '$2 == "/" { print $1 }' |
            while read -r be; do
                d=
                if [ "$be" = "$root" ]; then
                    b=/boot
                elif d=$(mktemp -d) && mount -t zfs -o ro,zfsutil "$be" "$d" 2>/dev/null; then
                    b=$d/boot
                else
                    echo "be $be unreadable"
                    [ -z "$d" ] || rmdir "$d"
                    continue
                fi
                n=0
                for k in "$b"/vmlinuz-*; do
                    [ -f "$k" ] || continue
                    v=${k##*/vmlinuz-} n=$((n + 1))
                    if [ -e "$b/initramfs-$v.img" ]; then echo "be $be kernel $v"; else echo "be $be kernel $v no-initrd"; fi
                done
                [ "$n" -gt 0 ] || echo "be $be none"
                if [ -n "$d" ]; then umount "$d" 2>/dev/null; rmdir "$d"; fi
            done
    fi
fi
if has_rule L-fbsd; then
    emit /boot/loader.conf
    cmd zfs-boot
    root=$(mount -p | awk '$2 == "/" { print $1 ":" $3; exit }')
    case $root in
    *:zfs)
        root=${root%:zfs}
        echo "root $root"
        echo "bootfs $(zpool get -H -o value bootfs "${root%%/*}" 2>/dev/null)" ;;
    esac
fi
has_rule L-nbsd && emit /boot.cfg
has_rule L-obsd && emit /etc/boot.conf
has_rule L-dfly && emit /boot/loader.conf
if has_rule L-ill; then cmd beadm; beadm list -H 2>/dev/null | qt; fi
# --- ZFS's mount units (#20)
if has_rule M-zfs; then
    emit /etc/fstab
    cmd zfs-list; zfs list -H -o name,mountpoint 2>/dev/null | qt
    cmd zfs-list-cache; ls -1 /etc/zfs/zfs-list.cache 2>/dev/null | qt
fi
# --- free space
if has_rule K-haiku; then cmd haiku; echo yes; fi
cmd df
if has_rule K-haiku; then df 2>&1 | qt; else df -Pk 2>&1 | qt; fi
# --- installed kernels, one line each
if ! has_rule K-none; then
    cmd kernels
    if has_rule K-deb || has_rule K-void || has_rule K-pve; then ls -1 /boot/vmlinuz-* 2>/dev/null | grep -v '\.old$'; fi
    # Gentoo: installkernel's vmlinuz-KV, or kernel-KV under systemd's kernel-install.
    if has_rule K-gentoo; then ls -1 /boot/vmlinuz-* /boot/kernel-* 2>/dev/null | grep -v '\.old$'; fi
    # AnduinOS: Ubuntu's vmlinuz-K, each with dracut's initrd.img-K (both mode 0600: names
    # only, by test -e, so no read of them).
    if has_rule K-anduinos; then
        for k in /boot/vmlinuz-*; do
            case $k in *.old | '/boot/vmlinuz-*') continue ;; esac
            if [ -e "/boot/initrd.img-${k#/boot/vmlinuz-}" ]; then echo "$k"; else echo "$k no-initrd"; fi
        done
    fi
    # PikaOS (pikaos.md): Debian's vmlinuz-K, each with booster's booster.img-K beside it (names
    # only, by test -e; .bak and .dpkg-bak copies aren't kernels).
    if has_rule K-pika; then
        for k in /boot/vmlinuz-*; do
            case $k in *.old | *.bak | *.dpkg-bak | '/boot/vmlinuz-*') continue ;; esac
            if [ -e "/boot/booster.img-${k#/boot/vmlinuz-}" ]; then echo "$k"; else echo "$k no-booster"; fi
        done
    fi
    # Aeon (aeon.md, K-sdbu): the running snapshot's /usr/lib/modules/KVER/ that hold a vmlinuz,
    # each with sdbootutil's copy on the ESP, ESP/TOKEN/KVER/linux-HASH (names only, by test -e).
    if has_rule K-sdbu; then
        esp=
        for e in /boot/efi /efi /boot; do [ -d "$e/loader/entries" ] && { esp=$e; break; }; done
        for d in /usr/lib/modules/*/; do
            [ -e "${d}vmlinuz" ] || continue
            v=${d%/} v=${v##*/} found=
            for f in "$esp"/*/"$v"/linux-*; do [ -n "$esp" ] && [ -e "$f" ] && { found=1; break; }; done
            if [ -n "$found" ]; then echo "$d"; else echo "$d no-esp"; fi
        done
    fi
    # Solus (solus.md): clr-boot-manager's kernels in /usr/lib64/kernel, each with the
    # initrd-NAME its package ships (names only, by test -e).
    if has_rule K-cbm; then
        for k in /usr/lib64/kernel/com.solus-project.*; do
            [ -e "$k" ] || continue
            if [ -e "/usr/lib64/kernel/initrd-${k##*/}" ]; then echo "$k"; else echo "$k no-initrd"; fi
        done
    fi
    # Ubuntu Core (ubuntu-core.md): modeenv's current_kernels (pc-kernel_REV.snap), each with the
    # kernel.efi snapd extracted to ubuntu-boot's EFI/ubuntu/K/ ("kernel K [no-kernel-efi]"), and
    # what the run GRUB's EFI/ubuntu/kernel.efi links to ("link K", "link none").
    if has_rule K-snapcore; then
        b=/run/mnt/ubuntu-boot/EFI/ubuntu
        for k in $(sed -n 's/^current_kernels=//p' /var/lib/snapd/modeenv 2>/dev/null | head -n 1 | tr ',' ' '); do
            if [ -f "$b/$k/kernel.efi" ]; then echo "kernel $k"; else echo "kernel $k no-kernel-efi"; fi
        done
        l=$(readlink "$b/kernel.efi" 2>/dev/null) l=${l%/kernel.efi}
        echo "link ${l##*/}" | sed 's/^link $/link none/'
    fi
    # Slackware (slackware.md): each /boot/vmlinuz-TYPE-VERSION (the links vmlinuz and
    # vmlinuz-TYPE aside).
    if has_rule K-slackware; then
        for k in /boot/vmlinuz-*; do [ -f "$k" ] && [ ! -L "$k" ] && echo "$k"; done
    fi
    if has_rule K-fed || has_rule K-ostree; then ls -1 /boot/loader/entries/*.conf 2>/dev/null | grep -v rescue; fi
    # ABRoot (Vanilla OS): each root's kernels in the init LV (label vos-init), one directory a
    # root, which only ABRoot mounts: read through a read-only mount of it here (noload: no
    # journal replay), or where it is mounted.  "LABEL vmlinuz-K"; "LABEL none" for a root with none.
    if has_rule K-abroot; then
        m=$(findmnt -n -o TARGET -S LABEL=vos-init 2>/dev/null | head -n 1) d=
        if [ -z "$m" ] && dev=$(blkid -L vos-init 2>/dev/null) && d=$(mktemp -d) &&
            mount -o ro,noload,nodev,nosuid,noexec "$dev" "$d" 2>/dev/null; then m=$d; fi
        if [ -n "$m" ]; then
            for r in "$m"/*/; do
                l=$(basename "$r")
                [ "$l" = lost+found ] || [ "$l" = '*' ] && continue
                ks=$(ls -1 "$r" 2>/dev/null | grep '^vmlinuz-')
                if [ -n "$ks" ]; then printf '%s\n' "$ks" | sed "s|^|$l |"; else echo "$l none"; fi
            done
        fi
        if [ -n "$d" ]; then umount "$d" 2>/dev/null; rmdir "$d"; fi
    fi
    if has_rule K-pac; then
        for d in /usr/lib/modules/*/; do [ -e "${d}vmlinuz" ] && echo "$d"; done
    fi
    if has_rule K-apk; then ls -d /lib/modules/*/ 2>/dev/null; fi
    if has_rule K-moss; then ls -1 /efi/loader/entries/*.conf /boot/loader/entries/*.conf 2>/dev/null; fi
    if has_rule K-nix; then ls -d /nix/var/nix/profiles/system-*-link 2>/dev/null; fi
    if has_rule K-fbsd; then
        be=$(bectl list -H 2>/dev/null | awk '{ print $1 }')
        if [ -n "$be" ]; then echo "$be"; else ls -d /boot/kernel /boot/kernel.old 2>/dev/null; fi
    fi
    if has_rule K-nbsd || has_rule K-alpbsd; then ls -1 /netbsd /onetbsd 2>/dev/null; fi
    if has_rule K-obsd; then ls -1 /bsd /bsd.sp /bsd.mp /obsd 2>/dev/null; fi
    if has_rule K-dfly; then ls -d /boot/kernel /boot/kernel.old 2>/dev/null; fi
    if has_rule K-ill; then beadm list -H 2>/dev/null | awk -F';' '{ print $1 }'; fi
    if has_rule K-haiku; then echo current; ls -d /boot/system/packages/administrative/state_* 2>/dev/null; fi
fi
# Slackware (slackware.md, K-slackware): what the loader boots against /boot's kernels.
# "kernel NAME MTIME SHA256" for each /boot/vmlinuz-TYPE-VERSION, "map MTIME" for LILO's
# /boot/map, "esp-image NAME SHA256|missing" for each image= of ELILO's EFI/Slackware/elilo.conf.
if has_rule K-slackware; then
    cmd slackware-boot
    for k in /boot/vmlinuz-*; do
        [ -f "$k" ] && [ ! -L "$k" ] || continue
        echo "kernel ${k##*/} $(stat -c %Y "$k") $(sha256sum "$k" | awk '{ print $1 }')"
    done
    [ -e /boot/map ] && echo "map $(stat -c %Y /boot/map)"
    d=/boot/efi/EFI/Slackware
    for i in $(sed -n 's/^[[:space:]]*image[[:space:]]*=[[:space:]]*\([^[:space:]]*\).*/\1/p' "$d/elilo.conf" 2>/dev/null); do
        if [ -f "$d/$i" ]; then echo "esp-image $i $(sha256sum "$d/$i" | awk '{ print $1 }')"; else echo "esp-image $i missing"; fi
    done
fi
