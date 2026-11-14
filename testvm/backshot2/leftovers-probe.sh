# The leftovers set's probes and seeds (ht1-bs2.sh's bs2_before_load,
# bs2_after_load, bs2_restored_probe): read by `sh -s -- MODE DISK ...` in
# the rescuer (Debian's tools) or the restored Alpine (busybox and
# util-linux, maybe not all of it).  POSIX sh.
#
#   seed DISK KIND START SECTORS   a new GPT with one partition at START, and
#                                  KIND's stale layer on it (md12 md10 zfs btrfs)
#   probe DISK                     what's active or importable: SUMMARY line last
#   after DISK N:TYPE...           each partition N's signatures against TYPE:
#                                  STALE, WRONG and AMBIVALENT lines
#   restored DISK                  the restored system's view: SUMMARY line last
#   release DISK                   (bs2-host leftovers --release) stop what the
#                                  rescuer started on DISK: its VGs deactivated,
#                                  md arrays stopped, pools exported; writes nothing
#                                  to DISK's data, as a user would after load's refusal

mode=$1 disk=$2
shift 2
has() { command -v "$1" >/dev/null 2>&1; }
parts() { for p in "$disk"[0-9]* "$disk"p[0-9]*; do [ -b "$p" ] && echo "$p"; done; }

case $mode in
seed)
    kind=$1 start=$2 sectors=$3
    set -e
    # The partition only: the old one's content stays, as a reinstall's
    # partitioner leaves it, and the layer goes on top.
    printf 'label: gpt\nstart=%s, size=%s, type=linux\n' "$start" "$sectors" | sfdisk -q --wipe never --wipe-partitions never "$disk"
    udevadm settle
    p=${disk}1
    case $kind in
    md12|md10)
        md=${kind#md}; md="${md%?}.${md#?}"
        yes | mdadm --create /dev/md/bs2old --run --metadata="$md" --level=1 --raid-devices=2 "$p" missing
        udevadm settle
        mkfs.ext4 -q -F -L bs2old /dev/md/bs2old
        mdadm --stop /dev/md/bs2old ;;
    zfs)
        zpool create -f -o ashift=12 -O mountpoint=none bs2old "$p"
        zpool export bs2old ;;
    btrfs)
        mkfs.btrfs -q -f -L bs2old "$p" ;;
    *) echo "no seed $kind" >&2; exit 2 ;;
    esac
    sync
    wipefs "$p"
    echo "seeded $kind on $p" ;;

probe|restored)
    echo "== lsblk"; lsblk -o NAME,TYPE,FSTYPE,SIZE,MOUNTPOINT "$disk" 2>&1 || true
    for p in "$disk" $(parts); do
        if has wipefs; then echo "== wipefs $p"; wipefs "$p" 2>&1; fi
        echo "== blkid $p"; blkid "$p" 2>&1
    done
    echo "== mdstat"; cat /proc/mdstat 2>&1
    echo "== dmsetup"; ls /dev/mapper 2>&1; has dmsetup && dmsetup table 2>&1
    has lvs && { echo "== lvs"; lvs -a -o vg_name,lv_name,lv_active,devices 2>&1; }
    has zpool && { echo "== zpool"; zpool list -H -o name 2>&1; zpool import 2>&1 | head -30; }
    echo "== holders"; for h in /sys/block/"${disk#/dev/}"/*/holders/*; do [ -e "$h" ] && echo "$h"; done
    echo "== dmesg"; dmesg 2>/dev/null | grep -iE 'md/raid|md: |md[0-9]+:|zfs|btrfs|lvm|device-mapper|crypt|ext4-fs|xfs' | tail -30
    s=""
    m=$(awk '/^md/{print $1}' /proc/mdstat 2>/dev/null | tr '\n' ' '); [ -n "$m" ] && s="$s md arrays: $m;"
    d=$(ls /dev/mapper 2>/dev/null | grep -v '^control$' | tr '\n' ' '); [ -n "$d" ] && s="$s dm devices: $d;"
    if has lvs; then
        v=$(lvs --noheadings -o vg_name,lv_name --select lv_active=active 2>/dev/null | awk '{print $1 "/" $2}' | tr '\n' ' ')
        [ -n "$v" ] && s="$s active LVs: $v;"
    fi
    if has zpool; then
        z=$(zpool list -H -o name 2>/dev/null | tr '\n' ' '); [ -n "$z" ] && s="$s imported pools: $z;"
        z=$(zpool import 2>/dev/null | awk '$1 == "pool:" {print $2}' | tr '\n' ' '); [ -n "$z" ] && s="$s importable pools: $z;"
    fi
    h=$(for x in /sys/block/"${disk#/dev/}"/*/holders/*; do [ -e "$x" ] && echo "${x#/sys/block/}"; done | tr '\n' ' ')
    [ -n "$h" ] && s="$s holders: $h;"
    if [ "$mode" = restored ]; then
        t=$(for p in $(parts); do echo "${p#/dev/}=$(blkid "$p" 2>/dev/null | sed -n 's/.* TYPE="\([^"]*\)".*/\1/p')"; done | tr '\n' ' ')
        s="$s types: $t;"
    fi
    echo "SUMMARY ${s:- nothing active, nothing importable}" ;;

after)
    # Without wipefs no STALE line could show: a WRONG line, not a clean run.
    if ! has wipefs; then
        echo "WRONG ${disk#/dev/}: no wipefs here, the old signatures can't be read"
        echo "leftovers-probe: after needs wipefs" >&2
        exit 2
    fi
    for want in "$@"; do
        n=${want%%:*} t=${want#*:}
        p=$disk$n
        [ -e "$p" ] || { echo "WRONG ${p#/dev/}: no such partition (the layout's $t)"; continue; }
        raw=$(wipefs --noheadings -O TYPE "$p" 2>&1) || { echo "WRONG ${p#/dev/}: wipefs failed: $(echo "$raw" | head -1)"; continue; }
        sigs=$(echo "$raw" | sort -u | tr '\n' ' ')
        echo "== $p: wipefs: $sigs"
        wipefs "$p" 2>&1
        for s in $sigs; do
            [ "$s" = "$t" ] || echo "STALE ${p#/dev/}: $s (the layout's $t; $(wipefs --noheadings -O TYPE,OFFSET "$p" | awk -v s="$s" '$1 == s {printf "%s ", $2}')offset)"
        done
        got=$(blkid -p -o value -s TYPE "$p" 2>&1); rc=$?
        echo "== $p: blkid -p: $got (exit $rc)"
        case $got in
            *ambivalent*) echo "AMBIVALENT ${p#/dev/}: blkid -p: $got" ;;
            "$t") ;;
            *) [ "$t" = nothing ] && [ -z "$got" ] || echo "WRONG ${p#/dev/}: blkid -p says '${got}', the layout's $t" ;;
        esac
    done ;;

release)
    n=0
    if has pvs; then
        for vg in $(pvs --noheadings -o pv_name,vg_name 2>/dev/null | awk -v d="$disk" 'index($1, d) == 1 && $2 != "" {print $2}' | sort -u); do
            vgchange -an "$vg" && echo "RELEASED vg $vg" && n=$((n + 1))
        done
    fi
    for h in /sys/block/"${disk#/dev/}"/*/holders/md*; do
        [ -e "$h" ] || continue
        md=$(basename "$h"); mdadm --stop "/dev/$md" && echo "RELEASED md $md" && n=$((n + 1))
    done
    if has zpool; then
        for z in $(zpool list -H -o name 2>/dev/null); do
            zpool status -P "$z" 2>/dev/null | grep -q "$disk" && zpool export "$z" && echo "RELEASED pool $z" && n=$((n + 1))
        done
    fi
    echo "SUMMARY $n released" ;;

*) echo "leftovers-probe: no mode $mode" >&2; exit 2 ;;
esac
