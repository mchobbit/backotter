#!/bin/bash
# probe.sh STAGE: what a GNOME OS guest is, for make-gnomeos.sh (run in the
# guest as root, over SSH: bash -s STAGE <probe.sh).  "BKO key=value" lines
# are the facts make-gnomeos.sh tests; the rest is the record behind them
# (build.txt), the answers to GN1's checks (docs/design/gnome-os.md).
# Reads only, but for mounting the physical btrfs root read-only at
# /run/bko-probe-root (unmounted again) and triggering the /efi automount.
stage=${1:-?}
set -u
bko() { printf 'BKO %s=%s\n' "$1" "$2"; }
sec() { printf '\n--- %s\n' "$*"; }
has() { command -v "$1" >/dev/null 2>&1 && echo yes || echo no; }
. /usr/lib/os-release 2>/dev/null || . /etc/os-release
bko stage "$stage"
bko date "$(date -u +%FT%TZ)"
bko ntp_synced "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)"
bko id "${ID:-}"
bko version_id "${VERSION_ID:-}"
bko image_version "${IMAGE_VERSION:-}"
bko image_id "${IMAGE_ID:-}"
sec os-release; cat /usr/lib/os-release
sec /proc/cmdline; cat /proc/cmdline
bko cmdline_initial_setup "$(grep -o 'gnome.initial-setup=[^ ]*' /proc/cmdline || echo none)"
bko cmdline_wants_sshd "$(grep -q 'systemd.wants=sshd.service' /proc/cmdline && echo yes || echo no)"
bko usrhash "$(grep -o 'usrhash=[0-9a-f]*' /proc/cmdline | cut -d= -f2)"
bko kernel "$(uname -r)"
bko systemd "$(systemctl --version | head -1)"

# --- Secure Boot, TPM ---------------------------------------------------------
efivar() {  # NAME-GUID: the variable's data bytes (after the 4 attribute bytes), as hex
    local f=/sys/firmware/efi/efivars/$1
    [[ -r $f ]] && od -An -tx1 -v -j4 "$f" | tr -d ' \n'
}
sb=$(efivar SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c); sm=$(efivar SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c)
bko secure_boot "${sb:-none}"
bko setup_mode "${sm:-none}"
bko has_tpm2 "$(systemd-analyze has-tpm2 2>/dev/null | head -1)"
sec systemd-analyze has-tpm2; systemd-analyze has-tpm2 2>&1

# --- The disk -----------------------------------------------------------------
disk=/dev/$(lsblk -no PKNAME "$(findmnt -no SOURCE /usr 2>/dev/null | head -1)" 2>/dev/null | head -1)
[[ -b $disk ]] || disk=/dev/vda
bko disk "$disk"
sec lsblk; lsblk -o NAME,PARTN,PARTLABEL,PARTTYPE,PARTUUID,FSTYPE,SIZE,MOUNTPOINTS "$disk" 2>&1
sec sfdisk --dump; (sfdisk --dump "$disk" || /usr/sbin/sfdisk --dump "$disk") 2>&1
parts=$(lsblk -lnpo NAME,PARTN,PARTLABEL "$disk" | awk 'NF >= 3 { printf "%s:%s ", $2, $3 }')
bko partitions "$parts"
sec blkid -p, each partition
for p in $(lsblk -lnpo NAME "$disk" | tail -n +2); do
    echo "$p: $(blkid -p -o export "$p" 2>&1 | tr '\n' ' ')"
done
for n in 2 4; do
    p=$(lsblk -lnpo NAME,PARTN "$disk" | awk -v n=$n '$2 == n { print $1 }')
    [[ -n $p ]] && bko blkid_type_p$n "$(blkid -p -o value -s TYPE "$p" 2>/dev/null || echo none)"
done
# B's content: nonzero bytes in its first 64 MiB (a fresh qcow2 reads zeros).
for n in 4 5; do
    p=$(lsblk -lnpo NAME,PARTN "$disk" | awk -v n=$n '$2 == n { print $1 }')
    [[ -n $p ]] && bko b_nonzero_p$n "$(head -c 67108864 "$p" | tr -d '\0' | wc -c)"
done
sec dmsetup / verity; (dmsetup table 2>&1 || true); ls -l /dev/mapper 2>&1
sec findmnt; findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS 2>&1 | grep -v -E ' (proc|sysfs|cgroup2|devpts|mqueue|tracefs|debugfs|securityfs|pstore|bpf|configfs|fusectl|hugetlbfs|efivarfs|binfmt_misc)( |$)'
bko usr_fstype "$(findmnt -no FSTYPE /usr)"
bko root_source "$(findmnt -no SOURCE /)"
bko root_fstype "$(findmnt -no FSTYPE /)"
bko etc_fstype "$(findmnt -no FSTYPE /etc)"
sec /etc mount; findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS /etc 2>&1

# --- LUKS ---------------------------------------------------------------------
rootpart=$(lsblk -lnpo NAME,PARTLABEL "$disk" | awk '$2 == "root" { print $1 }')
bko root_part "$rootpart"
luks=no; [[ -n $rootpart ]] && [[ $(blkid -p -o value -s TYPE "$rootpart" 2>/dev/null) == crypto_LUKS ]] && luks=yes
bko encrypted "$luks"
if [[ $luks == yes ]]; then
    sec systemd-cryptenroll "$rootpart"; systemd-cryptenroll "$rootpart" 2>&1
    # The LUKS2 JSON area, read straight from the header (cryptsetup may be absent).
    sec LUKS2 JSON tokens and keyslots
    python3 -I - "$rootpart" <<'EOF' 2>&1
import json, struct, sys
with open(sys.argv[1], 'rb') as f:
    h = f.read(4096)
    size = struct.unpack('>Q', h[8:16])[0]
    f.seek(4096)
    j = json.loads(f.read(size - 4096).split(b'\0', 1)[0])
for k, v in sorted(j['keyslots'].items()):
    print('keyslot', k, v.get('type'), v.get('kdf', {}).get('type'))
for k, v in sorted(j['tokens'].items()):
    w = {x: v[x] for x in v if x not in ('tpm2-blob', 'tpm2-policy-hash', 'tpm2_pubkey', 'tpm2-pubkey', 'tpm2_srk', 'tpm2-srk', 'tpm2_salt', 'tpm2-salt', 'tpm2-primary-alg')}
    print('token', k, json.dumps(w, sort_keys=True))
    if v.get('type') == 'systemd-tpm2':
        print('BKO tpm2_pcrs=' + '+'.join(str(p) for p in v.get('tpm2-pcrs', [])))
        print('BKO tpm2_pcrlock=' + str(v.get('tpm2_pcrlock', v.get('tpm2-pcrlock', False))).lower())
        print('BKO tpm2_pubkey_pcrs=' + '+'.join(str(p) for p in v.get('tpm2_pubkey_pcrs', v.get('tpm2-pubkey-pcrs', []))))
print('BKO token_types=' + ','.join(sorted(v.get('type', '?') for v in j['tokens'].values())))
print('BKO keyslot_count=%d' % len(j['keyslots']))
EOF
fi
sec pcrlock
pl=/usr/lib/systemd/systemd-pcrlock
bko pcrlock_supported "$($pl is-supported 2>/dev/null | head -1)"
if [[ -e /var/lib/systemd/pcrlock.json ]]; then
    bko pcrlock_json yes
    bko pcrlock_json_pcrs "$(python3 -I -c 'import json; j = json.load(open("/var/lib/systemd/pcrlock.json")); print("+".join(str(p["pcr"]) for p in j.get("pcrValues", [])))' 2>/dev/null)"
    python3 -I -c 'import json; j = json.load(open("/var/lib/systemd/pcrlock.json")); [print(p["pcr"], len(p.get("values", []))) for p in j.get("pcrValues", [])]' 2>&1
else
    bko pcrlock_json no
fi
ls -l /var/lib/pcrlock.d /etc/pcrlock.d /usr/lib/pcrlock.d 2>&1 | head -60
[[ $luks == yes ]] && { echo "predict:"; timeout 120 $pl predict --location=770 2>&1 | head -80; }
[[ $luks == yes ]] && { echo "log (unrecognised):"; timeout 120 $pl log 2>&1 | grep -iE 'unrecogn|missing|^[[:space:]]*PCR' | head -40; }

# --- The ESP, the boot ----------------------------------------------------------
# The ESP: gpt-auto's automount at /efi or /boot (whichever is the ESP's).
sec automounts; systemctl list-units --all --no-legend '*.automount' 2>&1
esp=$(bootctl --print-esp-path 2>/dev/null)
[[ -n $esp ]] || esp=/efi
ls "$esp" >/dev/null 2>&1
bko esp_path "$esp"
au=$(systemd-escape -p --suffix=automount "$esp")
sec "$au"; systemctl show "$au" -p Where,TimeoutIdleUSec,ActiveState 2>&1; systemctl cat "$au" 2>&1 | head -30
bko esp_automount "$au $(systemctl is-active "$au" 2>/dev/null)"
bko efi_automount_idle "$(systemctl show "$au" -p TimeoutIdleUSec --value 2>/dev/null)"
bko efi_fstype "$(findmnt -no FSTYPE "$esp" | tail -1)"
sec ESP files; (cd "$esp" && find . -type f -printf '%s %p\n' 2>&1 | sort -k2)
sec loader.conf; cat "$esp/loader/loader.conf" 2>&1
bko loader_keys "$(ls "$esp/loader/keys" 2>/dev/null | tr '\n' ' ')"
sec loader/keys; find "$esp/loader/keys" -type f 2>&1
bko ukis "$(ls "$esp/EFI/Linux" 2>/dev/null | grep '\.efi$' | tr '\n' ' ')"
sec bootctl status; timeout 60 bootctl status --no-pager 2>&1
sec bootctl list; timeout 60 bootctl list --no-pager 2>&1
bko booted_entry "$(timeout 60 bootctl status --no-pager 2>/dev/null | sed -n 's/^ *Current Entry: *//p' | head -1)"
# BootCurrent and its Boot#### entry's description and file.
python3 -I - <<'EOF' 2>&1
import glob, struct
G = '8be4df61-93ca-11d2-aa0d-00e098032b8c'
def var(n):
    try:
        return open('/sys/firmware/efi/efivars/%s-%s' % (n, G), 'rb').read()[4:]
    except OSError:
        return None
def entry(n):
    d = var('Boot%04X' % n)
    if d is None:
        return '(none)'
    plen = struct.unpack('<H', d[4:6])[0]
    i = 6
    while d[i:i + 2] != b'\0\0':
        i += 2
    desc = d[6:i].decode('utf-16le')
    dp = d[i + 2:i + 2 + plen]
    files, j = [], 0
    while j + 4 <= len(dp):
        t, s, l = dp[j], dp[j + 1], struct.unpack('<H', dp[j + 2:j + 4])[0]
        if t == 4 and s == 4:
            files.append(dp[j + 4:j + l].decode('utf-16le').rstrip('\0'))
        if l < 4:
            break
        j += l
    return '%s | %s' % (desc, ' '.join(files) or '(no File node)')
bc = var('BootCurrent')
order = var('BootOrder')
if order:
    print('BootOrder', ' '.join('%04X' % struct.unpack('<H', order[k:k + 2])[0] for k in range(0, len(order), 2)))
for f in sorted(glob.glob('/sys/firmware/efi/efivars/Boot[0-9A-F][0-9A-F][0-9A-F][0-9A-F]-' + G)):
    n = int(f.split('/')[-1][4:8], 16)
    print('Boot%04X' % n, entry(n))
if bc:
    n = struct.unpack('<H', bc)[0]
    print('BKO boot_current=Boot%04X %s' % (n, entry(n)))
EOF
for v in LoaderImageIdentifier LoaderInfo StubInfo LoaderEntrySelected; do
    f=$(ls /sys/firmware/efi/efivars/$v-* 2>/dev/null | head -1)
    [[ -n $f ]] && bko "efi_$v" "$(tail -c +5 "$f" | tr -d '\0')"
done
bko mok_vars "$(ls /sys/firmware/efi/efivars/ 2>/dev/null | grep -c -i '^Mok')"

# --- Tools, services ------------------------------------------------------------
for t in cryptsetup veritysetup efibootmgr sfdisk parted mkfs.ext4 mkfs.btrfs avahi-daemon rsync python3 updatectl systemd-cryptenroll mokutil tar zstd; do
    bko "tool_$t" "$(has $t)"
done
bko avahi_unit "$(systemctl list-unit-files avahi-daemon.service --no-legend 2>/dev/null | awk '{print $2}' | head -1)"
bko sshd_active "$(systemctl is-active sshd.service 2>/dev/null)"
bko sshd_enabled "$(systemctl is-enabled sshd.service 2>/dev/null)"
bko sshd_wanted "$(ls /etc/systemd/system/multi-user.target.wants/ 2>/dev/null | grep -c '^sshd.service$')"
sec credentials; ls /run/credentials/@system 2>&1
bko initial_setup_running "$(ps -eo comm= | grep -c '^gnome-initial-s')"
bko initial_setup_done "$(ls /var/lib/gnome-initial-setup 2>/dev/null | tr '\n' ' ')$( [[ -e /etc/gnome-initial-setup-done ]] && echo etc-done)"
sec loginctl; loginctl list-sessions --no-legend 2>&1; loginctl list-users --no-legend 2>&1
sec homed; systemctl is-active systemd-homed.service 2>&1; homectl list 2>&1 | head
sec 'getent passwd (uid >= 1000)'; getent passwd | awk -F: '$3 >= 1000 && $3 < 60000'
sec failed units; systemctl --failed --no-legend 2>&1
bko failed_units "$(systemctl --failed --no-legend 2>/dev/null | awk '{print $2}' | tr '\n' ' ')"
bko system_state "$(systemctl is-system-running 2>/dev/null)"

# --- Updates ----------------------------------------------------------------------
sec updatectl list host; timeout 120 updatectl list host 2>&1 | head -40
sec sysupdate.d; ls /usr/lib/sysupdate.d /etc/sysupdate.d 2>&1
sec extensions; systemd-sysext status 2>&1 | head; systemd-confext status 2>&1 | head

# --- The physical root: /etc's upper, the marker, room --------------------------------
m=/run/bko-probe-root
mkdir -p "$m"
src=$(findmnt -no SOURCE / | sed 's/\[.*//')
if mount -o ro,subvol=/ "$src" "$m" 2>/dev/null || mount -o ro "$src" "$m" 2>/dev/null; then
    sec physical root top; ls -la "$m" 2>&1
    sec btrfs subvolumes; btrfs subvolume list "$m" 2>&1 | head -20
    sec 'etc upper: whiteouts (char 0:0) and opaque dirs'
    find "$m/etc" -type c -printf '%M %p %Hr\n' 2>/dev/null | head -40
    bko etc_whiteouts "$(find "$m/etc" -type c 2>/dev/null | wc -l)"
    opq=0
    while IFS= read -r d; do
        a=$(getfattr -h --absolute-names -d -m 'overlay' "$d" 2>/dev/null | grep -i opaque) && { echo "opaque: $d $a"; opq=$((opq + 1)); }
    done < <(find "$m/etc" -type d 2>/dev/null)
    bko etc_opaque "$opq"
    bko etc_upper_files "$(find "$m/etc" 2>/dev/null | wc -l)"
    sec etc upper files; find "$m/etc" -printf '%M %p\n' 2>/dev/null | head -150
    for f in etc/machine-id etc/fstab etc/os-release etc/hostname var/lib/systemd var/lib/systemd/random-seed var/lib/systemd/pcrlock.json var/lib/extensions.mutable root home etc/shadow etc/passwd; do
        [[ -e $m/$f || -L $m/$f ]] && echo "marker candidate present: $f" || echo "marker candidate absent: $f"
    done
    bko root_machine_id "$([[ -s $m/etc/machine-id ]] && echo yes || echo no)"
    bko root_has_fstab "$([[ -e $m/etc/fstab ]] && echo yes || echo no)"
    sec room; df -B1 "$m" 2>&1; btrfs filesystem usage -b "$m" 2>&1 | head -12
    bko root_used "$(df -B1 --output=used "$m" | tail -1 | tr -d ' ')"
    umount "$m"
else
    echo "could not mount $src read-only at $m"
fi
rmdir "$m" 2>/dev/null
bko probe_done yes
