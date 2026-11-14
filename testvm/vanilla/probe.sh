#!/bin/bash
# What make-vanilla.sh records of a vanilla3 boot, run as root in the guest
# over SSH (bash -s STAGE).  Lines "BKO key=value" are what make-vanilla.sh
# checks; the rest is first-boot.txt's record.  STAGE: first (the first
# boot, all of it), upgraded (after abroot upgrade, before the reboot), or
# boot (a later boot: which root, its marks, abroot status).
stage=${1:-boot}
set -u
x() { echo "+ $*"; "$@" 2>&1; }
have() { command -v "$1" >/dev/null; }
label_of() { lsblk -dno LABEL "$1" 2>/dev/null; }
rootdev=$(findmnt -vno SOURCE /)
echo "BKO root_dev=$rootdev"
echo "BKO root_label=$(label_of "$rootdev")"
echo "BKO bko_root=$(cat /etc/bko-root 2>/dev/null || echo none)"
sb=$(od -An -tu1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c 2>/dev/null | awk '{ print $NF }')
case $sb in 1) sb="SecureBoot enabled" ;; 0) sb="SecureBoot disabled" ;; *) sb=unknown ;; esac
echo "BKO sb_state=$sb"
echo "BKO cmdline=$(cat /proc/cmdline)"
echo "BKO running=$(systemctl is-system-running --wait 2>/dev/null)"
echo "BKO timer=$(systemctl is-enabled vanilla-updates.timer 2>/dev/null)"
echo "BKO ssh_enabled=$(systemctl is-enabled ssh.service 2>/dev/null)"
x mokutil --sb-state
x cat /proc/cmdline
x uname -r
x hostname
x abroot status
x abroot status --json
mnt=/run/bko-probe
mkdir -p $mnt/init $mnt/other $mnt/boot
mount -o ro LABEL=vos-init $mnt/init 2>/dev/null || mount -o ro /dev/vos-root/init $mnt/init
other=vos-b; [[ $(label_of "$rootdev") == vos-b ]] && other=vos-a
mount -o ro LABEL=$other $mnt/other
echo "BKO other_label=$other"
echo "BKO default=$(sed -n 's/^set default=//p' /boot/grub/grub.cfg)"
for l in vos-a vos-b; do
    echo "BKO init_$l=$(ls $mnt/init/$l 2>/dev/null | tr '\n' ' ')"
    echo "BKO abroot_cfg_$l=$(grep -E '^\s*linux' $mnt/init/$l/abroot.cfg 2>/dev/null | tail -1 | tr -s ' \t' ' ')"
done
echo "BKO other_abimage=$(tr -d '\n ' <$mnt/other/abimage.abr 2>/dev/null || echo none)"
echo "BKO own_abimage=$(tr -d '\n ' </abimage.abr 2>/dev/null || echo none)"
echo "BKO other_has_usr=$([[ -d $mnt/other/usr/bin ]] && echo yes || echo no)"
echo "BKO upper_a_bko_root=$(cat /var/lib/abroot/etc/vos-a/bko-root 2>/dev/null || echo none)"
echo "BKO upper_b_bko_root=$(cat /var/lib/abroot/etc/vos-b/bko-root 2>/dev/null || echo none)"
echo "=== master grub.cfg (entries)"; grep -nE '^set (default|timeout)|menuentry|configfile|^serial|^terminal' /boot/grub/grub.cfg
[[ -e /boot/grub/grub.cfg.future ]] && { echo "=== grub.cfg.future (entries)"; grep -nE '^set default|menuentry|configfile' /boot/grub/grub.cfg.future; }
for l in vos-a vos-b; do echo "=== init/$l"; ls -la $mnt/init/$l; echo "--- init/$l/abroot.cfg"; cat $mnt/init/$l/abroot.cfg 2>/dev/null; done
x cat /etc/abroot/kargs; echo
echo "=== findmnt"; findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS / /etc /var /boot /boot/efi
x lsblk -o NAME,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS
x systemctl --failed --no-legend
if [[ $stage == first ]]; then
    x lvs -a -o+seg_type,devices,pool_lv,data_percent,metadata_percent,chunk_size,discards,zero,lv_uuid
    x vgs -o+vg_uuid
    x pvs -o+pv_uuid
    x cat /etc/os-release
    x cat /usr/lib/os-release
    x ls -l /etc/os-release
    x cat /etc/abroot/abroot.json
    x cat /usr/share/abroot/abroot.json
    echo "=== master grub.cfg"; cat /boot/grub/grub.cfg
    x cat /var/lib/abroot/etc/vos-a/fstab
    x cat /etc/fstab
    echo "=== the lower /etc/fstab (vos-a's own)"; mkdir -p $mnt/self; mount -o ro "$rootdev" $mnt/self && { cat $mnt/self/etc/fstab; ls -la $mnt/self/ | head -40; umount $mnt/self; }
    x cat /abimage.abr
    x ls -la /
    x systemctl status vanilla-updates.timer --no-pager
    x cat /usr/sbin/init
    x ls /usr/share/init.d
    echo "=== the /etc upper vos-a"; ls -la /var/lib/abroot/etc/vos-a | head -60
    x ls -la /var/lib/abroot /var/lib/abroot/etc /var/lib/abroot/storage
    echo "=== binaries"
    for b in thin_check thin_dump btrfs cryptsetup python3 rsync efibootmgr getfattr setfattr lvm mokutil podman sudo; do
        printf '%-12s %s\n' $b "$(command -v $b || echo -)"
    done
    echo "=== xattrs: trusted.* in the /etc uppers and the storage"
    for d in /var/lib/abroot/etc /var/lib/abroot/storage; do
        if have getfattr; then
            n=$(getfattr -R -h -d -m '^trusted\.' --absolute-names $d 2>/dev/null | grep -c '^trusted\.')
            echo "BKO trusted_xattrs[$d]=$n"
            getfattr -R -h -d -m '^trusted\.' --absolute-names $d 2>/dev/null | grep -E '^(# file|trusted)' | head -30
            echo "--- names:"; getfattr -R -h -m '^trusted\.' --absolute-names $d 2>/dev/null | grep '^trusted' | sort | uniq -c
        elif have python3; then
            python3 -I - $d <<'EOF'
import os, sys, collections
d = sys.argv[1]; c = collections.Counter(); ex = []
for root, dirs, files in os.walk(d):
    for n in dirs + files:
        p = os.path.join(root, n)
        try: names = os.listxattr(p, follow_symlinks=False)
        except OSError: continue
        for a in names:
            if a.startswith("trusted."):
                c[a] += 1
                if len(ex) < 30: ex.append((p, a, os.getxattr(p, a, follow_symlinks=False)))
print(f"BKO trusted_xattrs[{d}]={sum(c.values())}")
for k, v in c.most_common(): print(f"  {v:6d} {k}")
for p, a, v in ex: print(f"  {p} {a}={v!r}")
EOF
        else
            echo "no getfattr nor python3"
        fi
    done
    echo "=== whiteouts (char 0:0)"
    for d in /var/lib/abroot/etc /var/lib/abroot/storage; do
        echo "BKO whiteouts[$d]=$(find $d -xdev -type c 2>/dev/null | wc -l)"
        find $d -xdev -type c 2>/dev/null | head -10 | xargs -r ls -l
    done
    echo "=== user.* overlay xattrs in the storage"
    have getfattr && getfattr -R -h -m '^user\.overlay' --absolute-names /var/lib/abroot/storage 2>/dev/null | grep '^user' | sort | uniq -c
    echo "=== security.* xattrs (IMA?) on a few files"
    have getfattr && getfattr -h -d -m '^security\.' /usr/bin/bash /usr/sbin/init /etc/passwd 2>/dev/null
    echo "=== LV UUIDs named anywhere"
    lvs --noheadings -o lv_name,lv_uuid vos-root vos-var 2>/dev/null | while read -r n u; do
        hits=$(grep -rlsF -e "$u" /etc /var/lib/abroot/etc /boot $mnt/init /usr/share/abroot /abimage.abr 2>/dev/null |
            grep -vE '^/etc/lvm/(backup|archive)/' | tr '\n' ' ')
        lvmhits=$(grep -rlsF -e "$u" /etc/lvm 2>/dev/null | tr '\n' ' ')
        echo "BKO lv_uuid[$n]=$u hits=[${hits% }] lvm_own=[${lvmhits% }]"
    done
    echo "=== room (du -sx per filesystem)"
    for d in / /var /boot /boot/efi $mnt/init $mnt/other /var/lib/abroot/storage; do
        printf '%-28s %s\n' "$d" "$(du -sxh $d 2>/dev/null | cut -f1)"
    done
    x df -h / /var /boot /boot/efi $mnt/init $mnt/other
fi
if [[ $stage == upgraded ]]; then
    x ls -la /boot/grub
    echo "=== master grub.cfg"; cat /boot/grub/grub.cfg
    x lvs -a -o+data_percent,metadata_percent
    echo "BKO other_bko_root_upper=$(cat /var/lib/abroot/etc/$other/bko-root 2>/dev/null || echo none)"
    echo "BKO other_timer_mask=$(readlink /var/lib/abroot/etc/$other/systemd/system/vanilla-updates.timer 2>/dev/null || echo none)"
    x cat /var/lib/abroot/etc/$other/fstab
    x cat $mnt/other/abimage.abr
    for d in /var $mnt/other /var/lib/abroot/storage; do printf '%-28s %s\n' "$d" "$(du -sxh $d 2>/dev/null | cut -f1)"; done
fi
if [[ $stage == boot ]]; then
    x cat /etc/fstab
    x lvs -a -o+data_percent,metadata_percent
fi
umount $mnt/init $mnt/other 2>/dev/null
exit 0
