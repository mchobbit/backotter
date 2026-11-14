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
    if has_rule K-deb || has_rule K-void; then ls -1 /boot/vmlinuz-* 2>/dev/null | grep -v '\.old$'; fi
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
