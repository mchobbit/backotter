# H1: the dump corpus and its golden plans

One redacted `bko-holt dump` of each of the corpus's 84 guests, and the plan
`bko-holt load` makes of each (docs/design/test2.md, "Shared pieces to
build", H1).  `holt/tests/golden_plans.rs` plans every dump again and fails
when a plan changes or breaks an invariant, so a change to load shows here
first, as a diff of plans, and HoltTest2 runs the guests whose plans
changed.  Most of the 84 are HoltTest2's sources (testvm/test2/classes.json);
some are not sources yet, and golden_plans.rs logs each source with no dump
(`NO DUMP`).

| File | What it is |
|---|---|
| `GUEST/src.json` | The source dump, redacted (below) |
| `GUEST/plan.txt` | Its golden plan: load's standard output, golden_plans.rs's `status` lines (what `status` looks for after each stage: what load builds but never prints), then its line with the exit status and standard error |
| `GUEST/plan+bigger.txt` | The plan onto targets 1 GiB bigger, for the guests in `variants.txt` |
| `variants.txt` | The variant plans: `aerynos+bigger`, `debian13-stack+bigger`, `mint22+bigger` (classes.json's `holt.variants.bigger`) |
| `index.tsv` | A row per dump: where it came from, and what the build did to it |

## Where the dumps came from

HoltTest1 records each source's dump as `/bkoscratch/holttest1d/RUN/GUEST/dumps/src.json`.
`testvm/test2/h1/collect-dumps.sh` took, for each source, the newest one on
chopin or cinqcent whose report says `RESULT: PASS` (a guest without one
would take the newest that passed its source phase, flagged
`FAIL-after-source`).  Cinqcent's runs named here (r4all, eosmj2, lk1,
chim3) moved on 2026-11-13 to
`chopin:/bkoscratch/archive/cinqcent-20261113/holttest1d/RUN`.  On 2026-11-08, 45 of them came from r4all, the
HoltTest1All of that night, all PASS, all dumped by bko-holt 0.2.4: 21 from
chopin, 24 from cinqcent.  The three guests built on 2026-11-09
(endeavouros, endeavouros-grub, manjaro) were added on 2026-11-10, from
HoltTest1 runs of 2026-11-09, again PASS and bko-holt 0.2.4: endeavouros
(eosmj3) and endeavouros-grub (eosmj5) from chopin, manjaro (eosmj2) from
cinqcent.  Those runs used a bko-holt built outside this tree, with commit
bf6797b, whose `bootloader_id` also looks at what's on the ESP; since the
merge into main (2026-11-10) this tree's load does too, so their golden
plans say `--bootloader-id=endeavouros` and `manjaro`, as the runs did.
ubuntu26-luks (`make-ubuntu.sh
--uefi --luks`, Subiquity's guided LVM on LUKS), built on 2026-11-10, was
added the same day from its HoltTest1 run lk1 on cinqcent, PASS, bko-holt
0.2.4.  chimera (`make-bootstrap.sh chimera`: BIOS, GRUB, / on XFS), built
on 2026-11-11, was added the same day from its HoltTest1 run chim2 on
chopin, PASS, bko-holt 0.2.4.  kinoite44 (`make-fedora.sh --ostree`: Fedora
Kinoite 44, an ostree system; docs/design/bazzite.md), built on 2026-11-11,
was added the same day from its HoltTest1 run bzb6a on chopin, PASS, dumped
by the bko-holt Bazzite's B6 built from this tree for that run (version
0.2.4, with the ostree code), so its dump is the first here with
`boot.ostree` and `storage.backup_sources`.  chimera-uefi (`make-bootstrap.sh
chimera-uefi`: UEFI, systemd-boot, LUKS2 holding LVM, its key file in the
initramfs) and chimera-zfs (`make-chimera-zfs.sh`: UEFI, GRUB, / on ZFS),
built on 2026-11-11, were added the same day from their HoltTest1 runs chim4
on chopin and chim3 on cinqcent, PASS, bko-holt 0.2.4.  gentoo
(`make-bootstrap.sh gentoo`: UEFI, GRUB in an ESP at /efi, LVM with / on
ext4 and /home on XFS, OpenRC; docs/design/gentoo.md), built on 2026-11-12,
is the first not from a HoltTest1 run: its G2 dump (`bko-holt dump` of the
live guest under `vm-run --snapshot` on chopin, bko-holt 0.2.4 built from
this tree, the image's sha256 the same before and after), so its index.tsv
row has run `g2`, result `dump` and no report_time; its provenance.tsv was
written by hand (docs/design/gentoo.md's G2b), the build and scans as
below.  gentoo-systemd (`make-bootstrap.sh gentoo-systemd`: UEFI, GRUB in
the ESP that is /boot, LUKS2 holding LVM, systemd's dracut with the key
file; G3), built on 2026-11-12, was added the same way from its G3 dump
(run `g3`, chopin, the image's sha256 the same before and after).
cachyos-zfs (`make-cachyos-zfs.sh`: CachyOS from its live ISO as its
Calamares installs ZFS with Limine: UEFI, a 4 GiB ESP as /boot, the pool
zpcachyos with ROOT/cos/{root,home,varcache,varlog}), built on 2026-11-13,
was added the same way from a dump of the live guest on copland (run `cz1`,
bko-holt 0.2.5 built from this tree; the guest booted on a plain q35 with
its install's UEFI variables, so through Limine's NVRAM entry, under a
throwaway overlay, the image's sha256 the same before and after).
pikaos4 (`make-pikaos.sh pikaos4`: PikaOS 4 from its GNOME ISO by its own
pikainstall; UEFI, an ESP at /boot/efi, an ext4 /boot, / on XFS, booster,
rEFInd; docs/design/pikaos.md), built on 2026-11-14, was added the same way
from a dump of the live guest on cinqcent (run `pk3`, Chopin's queue full,
bko-holt 0.2.6 built from this tree; on a plain q35 with a copy of its
install's UEFI variables, so through rEFInd's NVRAM entry, the disk under
QEMU's `snapshot=on`, the image's sha256 the same before and after).  It is
in the corpus before it is one of classes.json's HoltTest2 sources, so
golden_plans.rs notes it until it is.  pikaos4-luks (`make-pikaos.sh
pikaos4-luks`: the same, but `crypt_root` LUKS2 holding Btrfs `@` and
`@home`, rEFInd only as the fallback EFI/BOOT/BOOTX64.EFI, no NVRAM
entry), built on 2026-11-14, was added the same way (run `pk4`, cinqcent),
booted through console-boot.py, which typed its console-passphrase at
booster's prompt; the image's sha256 the same before and after.
anduinos2 (`make-anduinos.sh`: AnduinOS 2.0.5 by its own installer's
executor, unattended; UEFI with Secure Boot on and its MOK in shim's
MokList, a 2 MiB BIOS boot partition, a 1 GiB ESP at /boot/efi, swap, one
btrfs with six subvolumes and the Disk Snapshots Manager's two read-only
baselines; docs/design/anduinos.md), built on 2026-11-14, was added the
same way from a dump of the live guest on cinqcent (run `a3`, Chopin's
queue full, bko-holt 0.2.6 built from this tree; its own vm-run-options,
so under Secure Boot through the fallback path, the disk under
`vm-run --snapshot`, the image's sha256 the same before and after).
neon24 (`make-from-live.sh neon ext4 ... neon24`: KDE neon 24.04 User
Edition from its live ISO by Calamares; UEFI with Secure Boot, a 300 MiB
ESP at /boot/efi, ext4 /, a /swapfile, GRUB through shim in EFI/neon;
docs/design/kde-neon.md), built on 2026-11-14, was added the same day from
its HoltTest1 run `ht1u` on chopin, PASS (cinqcent's `ht1u` passed too, an
hour earlier), dumped by the static bko-holt 0.2.6 built from this tree
for that run (with neon's osrel.rs row and update-grub), the image the
same as the run recorded.  Its golden plan has what that run's boot stage
ran: `update-initramfs -u -k all`, `grub-install --bootloader-id=neon
--no-nvram`, `update-grub`, then the NVRAM entry for `\EFI\neon\shimx64.efi`
(the run itself was `--no-nvram`).
garuda (`make-arch.sh --live garuda`: Garuda Linux dr460nized 260819 from
its live ISO as its Calamares installs offline; UEFI, a 300 MiB ESP at
/boot/efi, one btrfs with `@`, `@home`, `@root`, `@srv`, `@cache`, `@log`
and `@tmp`, `.snapshots` nested in `@`, dracut through
garuda-dracut-support, GRUB in EFI/Garuda; docs/design/garuda.md), built on
2026-11-14, was added the same day from its HoltTest1 run `ga4-4` on
chopin, PASS, dumped by the bko-holt 0.2.6 built from this tree for that
run (with Garuda's osrel.rs row and dracut-rebuild), the image the same as
the run recorded.  Its source had snap-pac's snapshots 1-4 from GA1's
update, so its golden plan makes the four shared snapshots
`@/.snapshots/1..4/snapshot` in the restore stage (restore `--exact
--clone-from`), then `dracut-rebuild` and `grub-install
--bootloader-id=Garuda --no-nvram` in the chroot.
biglinux (`make-arch.sh --live biglinux`: BigLinux 2026-10-03 k618 from
its live ISO as its Calamares installs offline; UEFI, a 300 MiB ESP at
/boot/efi, one btrfs with `@`, `@home` and systemd-swap's `@swapfile`,
mkinitcpio, GRUB in EFI/BigLinux; docs/design/biglinux.md), built on
2026-11-14, was added the same day from its HoltTest1 run `bl4-3` on
cinqcent, PASS, dumped by the bko-holt 0.2.6 built from this tree for that
run (with BigLinux's osrel.rs row and the default_subvol fix), the image
the same as the run recorded.  Its source had three Timeshift snapshots
at the top level (big-first-boot's and `prep/biglinux-timeshift.sh`'s two),
so its golden plan notes them as not made, runs `set-default` of the top
level for the superblock's default_subvol flag, and after `grub-install
--bootloader-id=BigLinux --no-nvram` defragments /boot and /usr/share/grub
`--nocomp`.
freebsd15-geli (bsdinstall's zfsboot with "Encrypt Disks": UEFI, GPT with
an ESP at /boot/efi, plain swap, and the pool zroot on a GELI provider,
AES-XTS 256, 4K sectors, GELIBOOT; docs/design/geli.md), a HoltTest2
source since GELI's G6 (2026-11-14), was added the same day from its
HoltTest1 run `geli3` on chopin, PASS, its source dumped live by the
bko-holt 0.2.6 built from this tree for that run, the guest booted through
console-boot.py (its loader's passphrase prompt), the image the same as
the run recorded.  Its dump has the provider's `geli` record (version,
flags, algorithms, sector size, iterations, key slots; never a key or its
salt), and its golden plan makes the provider again with `geli init -b -g
... -i 3031038 -J`, attaches it, builds zroot on `/dev/sdqp3.eli`, writes
`/var/backups`' metadata backup again, and restores through load's
msdosfs wrapper (`sh -c '..."$@"...' sh rustic-bko ... restore`), which
invariants.rs's `commands` now reads through.
freebsd15-geli-bios (bsdinstall's zfsboot with "Encrypt Disks" and "Encrypt
Swap" on BIOS: gptzfsboot, zroot on a GELIBOOT provider, onetime swap
`vtbd0p2.eli`) and freebsd15-geli-ufs (UEFI, a UFS root on a GELIBOOT
provider with key slots 0 and 1, and an authenticated (HMAC/SHA256)
provider for /data opened by the key file /etc/geli/p4.key;
docs/design/geli2.md), HoltTest2 sources since GELI's round 2 (H5,
2026-11-17), were added the same day from their HoltTest1 runs on
cinqcent: `geli2a` (the bios guest, the promoted holt-bin's bko-holt 0.2.6)
and `geli2b` (the ufs guest, the bko-holt 0.2.6 built from this tree with
round 2's newfs fix), both PASS, the images the same as the runs recorded.
freebsd15-geli-bios's golden plan is attended: it asks for the new
passphrase and runs `geli init -b -g ... -i 2782171 -J`. freebsd15-geli-ufs's
is refused (exit 4): the corpus plans take no `--key-file`, and its source
uses key slot 1, which load won't drop silently (geli.md D4). Its stages
still show the key-file provider made with a temporary key, written
through by dd, newfs'd with `-s 3602431` on the 4096-byte-sector root
provider (dumpfs's `-s 28819448` is in 512-byte units), and given the
restored key file with `geli setkey -n 0 -K ... -P`.
kali (`make-debian.sh --kali 2026.3 ext4`: Kali Linux 2026.3 by d-i with
Kali's preseed; UEFI with Secure Boot off, GPT with an 883 MiB ESP at
/boot/efi, ext4 /, swap; GRUB in EFI/kali and the same file as
EFI/BOOT/BOOTX64.EFI, no shim on the ESP; initramfs-tools, kernels 7.1.5
and 7.2.15; docs/design/kali.md), built on 2026-11-14, was added the same
day from a dump of the live guest on cinqcent (run `k3`, Chopin's queue
full, the static bko-holt 0.2.6 built from this tree with Kali's osrel.rs
row and update-grub; its own vm-run-options, so through the fallback path
on a fresh variable store, the disk under `vm-run --snapshot`, the image's
sha256 the same before and after).  It is in the corpus before it is one
of classes.json's HoltTest2 sources, so golden_plans.rs notes it until it
is.  Its golden plan runs `update-initramfs -u -k all`, `grub-install
--target=x86_64-efi --bootloader-id=kali --no-nvram` and `update-grub`
with os-prober off, and makes no NVRAM entry (the source booted the
fallback path).  guix15 (`make-guix.sh guix15`: GNU Guix System 1.5.0 from
its installation ISO by `guix system init`; UEFI, GPT with a 512 MiB ESP at
/boot/efi, swap, / ext4 by UUID, grub-efi-bootloader in EFI/Guix, one
generation; docs/design/guix.md) and guix15-bios (`make-guix.sh
guix15-bios`: the same, BIOS: a BIOS
boot partition, swap, one btrfs by label with `@`, `@gnu` and `@home`,
grub-bootloader on the MBR), built on 2026-11-14, were added the same day
from dumps of the live guests on chopin (run `gx3`, the static bko-holt
0.2.6 built from this tree with GX2's store-system code, `b1119c11...`;
guix15 on a plain q35 with a copy of its install's UEFI variables, so
through GRUB's NVRAM entry, both disks under `vm-run --snapshot`, the
images' sha256 the same before and after; their provenance.tsv written by
hand, as gentoo's).  They are in the corpus before they are classes.json's
HoltTest2 sources, so golden_plans.rs notes them until they are.  Their
dumps have `boot.store` (each generation's boot parameters, grub.cfg's
searches and roots, and on guix15-bios grub-install's boot.img and
core.img in /boot/grub/i386-pc; no GRUB in either store).  guix15's golden
plan runs nothing in the chroot: its boot stage makes only the NVRAM entry
for `\EFI\Guix\grubx64.efi`.  guix15-bios's makes the three subvolumes and
the swap (fstab doesn't name it), and its boot stage writes the source's
own boot.img and core.img with the rescue's `grub-bios-setup`.  oi2610
(`make-openindiana.sh oi2610`: OpenIndiana Hipster 2026.10 from its text
ISO by its own text installer, driven over the console; UEFI, the whole
disk from `zpool create -B`: a 256 MiB ESP nothing mounts, rpool with the
boot environment `openindiana` and its own /var, the dump and swap
volumes; illumos' loader as EFI/BOOT/BOOTX64.EFI, no NVRAM entry;
docs/design/openindiana.md) and oi2610-bios (`make-openindiana.sh --cloud`:
OpenIndiana's supplemental cloud image of 2026-11-07; BIOS, its boot
environment `b5afac2a` with mountpoint=legacy as OmniOS's cloud image's,
swap but no dump volume), built on 2026-11-14, were added the same day
from dumps of the live guests on copland (run `o1`, Chopin's queue full,
the illumos bko-holt 0.2.6 built from this tree with O2's /etc/release
fallback, OpenIndiana having no os-release; their own vm-run-options, so
oi2610 through the fallback path on a fresh variable store, both disks
under `vm-run --snapshot`, the images' sha256 the same before and after;
their provenance.tsv written by hand).  They are in the corpus before they
are classes.json's HoltTest2 sources, so golden_plans.rs notes them until
they are.  oi2610's golden plan makes the dump volume empty, mounts
/var after /, and runs only `bootadm update-archive` at boot (the ESP came
back with the table); oi2610-bios's mounts its legacy boot environment
with `mount -F zfs` and puts back gptzfsboot with `installboot`.
proxmox9 (`make-proxmox.sh proxmox9`: Proxmox VE 9.2 from
`proxmox-ve_9.2-1.iso` by its automated installer, ZFS `raid0`; UEFI with
Secure Boot off, GPT with a BIOS boot partition, a 512 MiB ESP that nothing
mounts and `rpool` with `ROOT/pve-1`, `data` (a 64 MiB zvol and a CT-style
subvol dataset) and `var-lib-vz`; systemd-boot kept by `proxmox-boot-tool`;
pmxcfs at /etc/pve; docs/design/proxmox.md) and proxmox9-mirror (the same,
`raid1` on two disks: `rpool` a mirror, an ESP on each disk, two NVRAM
entries), built on 2026-11-14 and updated by vm-update (kernel
7.0.14-28-pve beside 7.0.2-6-pve), were added the same day from dumps of
the live guests on chopin (runs `p3` and `p4`, the static bko-holt 0.2.6
built from this tree with P2's Proxmox code, `323e4069...`; their own
vm-run-options with a copy of each install's UEFI variables, so through
systemd-boot's NVRAM entries, the disks under `vm-run --snapshot`, the
images' sha256 the same before and after; their provenance.tsv written by
hand, with each guest's image-state generation).  They are in the corpus
before they are classes.json's HoltTest2 sources, so golden_plans.rs notes
them until they are.  Their dumps are the first with `boot.proxmox_boot`
(the ESP list of `/etc/kernel/proxmox-boot-uuids`, each ESP's mode and
contents read by a private read-only mount, the kernel command line) and
`os.product`, the ESPs in `boot.esps` with no mount point; pmxcfs is a
dump warning, not a backup source.  Their golden plans make each ESP with
its volume ID, make no zpool.cache (the sources have none), and at boot run
`update-initramfs -u -k all`, `proxmox-boot-tool init` on each ESP in the
list's order, one `proxmox-boot-tool refresh`, and write the ESP list back;
they make no NVRAM entry, each source entry being one that `init`'s
`bootctl install` makes again.
truenas25 (`make-truenas.sh truenas25`: TrueNAS CE 25.10.6 from its ISO by
its own installer, then updated to 25.10.7 and 25.10.8 by its offline
update files; UEFI with Secure Boot off, GPT with a BIOS boot partition, a
512 MiB ESP that nothing mounts and `boot-pool` (`compatibility=grub2`)
with the boot environments `ROOT/25.10.6` (kept), `ROOT/25.10.7` and
`ROOT/25.10.8` (booted), 15 children each, `grub` at /boot/grub and
`.system` at /var/db/system; GRUB for both firmwares; docs/design/truenas.md),
built on 2026-11-14, was added the same day from a dump of the live guest
on chopin (run `t3`, the static bko-holt 0.2.6 built from this tree with
T2's TrueNAS code, `464fe4a7...`; its own vm-run-options with a copy of its
install's UEFI variables, so through GRUB's TrueNAS-0 NVRAM entry, 8 GiB,
the disk under `vm-run --snapshot`, the image's sha256 the same before and
after; its provenance.tsv written by hand).  It is in the corpus before it
is one of classes.json's HoltTest2 sources, so golden_plans.rs notes it
until it is.  Its dump is the first with `boot.truenas` (each BE's version,
kernel, keep flag and clones, the ESPs found as each `boot-pool` disk's
EF00 partition) and the first whose `storage.backup_sources` hold
inactive ZFS boot environments: each at `/run/bko-be/V` with its children,
no `from`, and a read-only `zfs` mount hint.  Its golden plan makes every
BE and child with its local properties but `readonly`, notes the inactive
BEs' clones as full copies, mounts `boot-pool/grub` and `.system`'s
datasets at their dumped paths and the inactive BEs under
`run/bko-be` for the one restore, then unmounts them and removes the
directory; at boot it runs `grub-install --target=i386-pc` on the disk,
`grub-install --target=x86_64-efi --bootloader-id=debian --no-nvram` into
the ESP mounted only for it, copies it to `EFI/boot/bootx64.efi`, runs one
`update-grub` and no `update-initramfs`, makes the TrueNAS-0 NVRAM entry,
and sets `readonly=on` back on the twelve datasets that had it before the
pool is exported.
void-zbm (`make-void-zfs.sh`: glibc Void Linux on a ZFS root booted by
ZFSBootMenu, built as ZFSBootMenu's Void UEFI guide does it; GPT with a
512 MiB ESP at /boot/efi holding only ZFSBootMenu's v3.1.0 release image
as `EFI/ZBM/VMLINUZ.EFI` and `VMLINUZ-BACKUP.EFI`, and `zroot`
(`compatibility=openzfs-2.3-linux`) with the boot environments
`ROOT/void` (the bootfs, kernels 6.18 and 7.2) and `ROOT/void-6.18` (a
clone, its own `org.zfsbootmenu:commandline`) and `home`; two NVRAM
entries; docs/design/zfsbootmenu.md), built on 2026-11-14, was added the
same day from a dump of the live guest on chopin (run `z4`, the static
bko-holt 0.2.6 built from this tree with Z2's ZFSBootMenu code,
`8f5e153b...`; its own vm-run-options, whose `--guest-vars` boots a copy
of its UEFI variables under `--snapshot`, so through ZFSBootMenu's NVRAM
entry, the disk under `vm-run --snapshot`, the image's and the variable
store's sha256 the same before and after; its provenance.tsv written by
hand, generation `-`: the guest has no image-state yet).  It is in the
corpus before it is one of classes.json's HoltTest2 sources, so
golden_plans.rs notes it until it is.  Its dump is the first with
`boot.zfsbootmenu` (each image's ESP path, SHA-256, kernel, stub and
embedded command line; the pool and its bootfs; each boot environment's
kernels and initramfs files, the inactive one's read by a private
read-only mount) and the loader `zfsbootmenu`, and its
`storage.backup_sources` hold the inactive boot environment at
`/run/bko-be/void-6.18` with its read-only `zfs` mount hint.  Building it
found a redaction bug, fixed in redact.rs before it entered the tree: the
pool's hostid, ZFSBootMenu's 0x00bab10c, got a pseudonym whose hex form
had no letters, so the kernel command line's `spl.spl_hostid=0x00bab10c`
mapped back upper-case (an 8-digit hostid's pseudonym now has a letter in
its hex form).  The fake rescue system has the `openzfs-2.3-linux`
feature set beside `grub2` (holt/tests/common/mod.rs).  Its golden plan
makes the pool with the source's features, `compatibility` and no
zpool.cache, the datasets with their `org.zfsbootmenu:commandline`, sets
`bootfs`, notes the clone as a full copy, mounts the inactive boot
environment under `run/bko-be` for the one restore, then unmounts it and
removes the directory; at boot it installs no loader (notes give both
images' SHA-256), runs dracut for the booted boot environment's kernels
only, and makes the two NVRAM entries again in the source's order.
void-zbm-enc (`make-void-zfs.sh --encrypt`: void-zbm's layout, but `zroot`
an encryption root, aes-256-gcm with a passphrase and
`keylocation=file:///etc/zfs/zroot.key`, the key file in each boot
environment's initramfs, and `org.zfsbootmenu:keysource=zroot/ROOT/void`
on `zroot`; ZFSBootMenu asks for the passphrase on every boot), built on
2026-11-14, was added on 2026-11-16 from a dump of the live guest on
chopin (run `z5`, the static bko-holt 0.2.6 built from d23c9ab,
`c6f179fb...`, the guest booted as void-zbm's was, its passphrase typed at
ZFSBootMenu's prompt by `console-boot.py` from the guest's own
`zfs-passphrase`; the image's and the variable store's sha256 the same
before and after; its provenance.tsv written by hand, generation `-`).
It is in the corpus before it is one of classes.json's HoltTest2 sources,
so golden_plans.rs notes it until it is.  Its dump has the encryption
root's properties (`encryption`, `keyformat`, `keylocation`,
`encryptionroot`) and the `keysource`, and no key material: dump reads
neither the key file nor the passphrase.  Its golden plan makes the pool
an encryption root unlocked from `--key-file zroot=/srv/bko-keys/zroot`
(`keylocation=file:///srv/bko-keys/zroot`) with the `keysource` on it;
at boot, after the restore, it checks the restored key file (its name a
pseudonym, `keyfile1.key`) against that file with `cmp` before setting
`keylocation` back to the source's; the rest is void-zbm's plan.
vanilla3 (`make-vanilla.sh`: Vanilla OS 3.0 by Albius from its live ISO
with the installer's own recipe; UEFI with Secure Boot, a 1 GiB ext4
`vos-boot` at /boot with the master grub.cfg, a 512 MiB ESP, the VG
`vos-root` with the `init` LV and the thin pool `root` holding the btrfs
roots `root-a` and `root-b`, and the VG `vos-var` with /var; ABRoot v2,
root B booted; docs/design/vanillaos.md), built on 2026-11-14, was added
the same day from its HoltTest1 run `v7-1` on chopin, PASS (cinqcent's
`v7-2` passed too), dumped by the bko-holt 0.2.6 built from this tree
for that run (`323e4069...`, with V2's ABRoot code), the image the same
as the run recorded.  It is in the corpus before it is one of
classes.json's HoltTest2 sources, so golden_plans.rs notes it until it is.
Its dump is the first with `boot.abroot` and with a thin pool, and its
`storage.backup_sources` has `/boot/init` and `/part-future` with `from:
null` and their read-only mount hints.  Its golden plan vgcfgrestores
both groups without the thin part, makes the pool with `lvcreate --type
thin-pool` (64k chunks, passdown, zero) and the two thin LVs by name,
gives `vos-a` and `vos-b` the source's `-d single -m dup`, mounts the
init LV at `boot/init`, root A at `part-future` and /var beside fstab's
tree, and at boot runs nothing in a chroot: the atimes, ABRoot's
consistency check of grub.cfg and both `abroot.cfg`, the log and the
tail, and no NVRAM entry (the source booted the fallback path).  Building
it found two redaction bugs, fixed in redact.rs before it entered the
tree: the LV UUID of `lvol0_pmspare`, all letters, wasn't taken for an
LVM UUID (scan's pattern wants a digit; an `id` or `*uuid` key's whole
value now counts), and the redacted dump redacted again by check_plan gave
a VG the pseudonym a PV had in dashed form only (the LVM pseudonym now
avoids both forms).  leap16 (`make-opensuse.sh leap 16.1`: openSUSE Leap
16.1 from its offline ISO by Agama, unattended; UEFI with Secure Boot on,
GPT with a 512 MiB ESP at /boot/efi, one btrfs with openSUSE's subvolumes
and snapper's `@/.snapshots`, the root `@/.snapshots/1/snapshot`, a swap
partition; GRUB 2 through shim in EFI/opensuse, dracut, SELinux enforcing;
docs/design/leap16.md), built on 2026-11-14, was added the same day from a
dump of the live guest on chopin (run `l3`, the static bko-holt 0.2.6
built from this tree with Leap's osrel.rs row; its own vm-run-options, so
under Secure Boot through shim's fallback on a fresh variable store, the
disk under `vm-run --snapshot`, the image's sha256 the same before and
after).  It is in the corpus before it is one of classes.json's HoltTest2
sources, so golden_plans.rs notes it until it is.  Its golden plan keeps
the restored shim and GRUB, runs dracut and `grub2-mkconfig` with os-prober
off, makes the `opensuse-secureboot` entry for `\EFI\opensuse\shim.efi`
and relabels what the boot stage wrote with `setfiles`.  microos
(`make-opensuse.sh microos`: openSUSE MicroOS 20261111 from its KVM image
and combustion; UEFI with Secure Boot on, hybrid GPT with a BIOS boot
partition, a 20 MiB ESP, one btrfs read-only root in snapper's slots with
each slot's `/etc` a nested subvolume, health-checker, transactional-update,
SELinux enforcing; docs/design/aeon.md), built on 2026-11-14, was added the
same day from its HoltTest1 run `aeon2c` on cinqcent, PASS, dumped after
`prep/microos-tu.sh` (slots 1-7, slot 3 booted after a rollback, slot 7 the
pending default) by the static bko-holt 0.2.6 built from this tree for that
run (`8f5e153b...`, with M2's transactional arm), the image the same as the
run recorded (collected with `--hosts cinqcent`: chopin's `aeon1` report is
newer, but its dump predates M5's fixes).  Its dump is the first with
`boot.transactional` and the first whose protective MBR has a disk
signature (KIWI's): its golden plan makes every slot and its nested `/etc`
(and `etc.syncpoint`) with the source's IDs noted, restores the slots
`--clone-from` the booted one, writes the signature back with `dd` after
sfdisk, and at boot regenerates nothing (the transactional arm's checks,
health-check.state's ID, GRUB's BIOS boot code from the restored slot).
Building it found two redaction bugs, fixed in redact.rs before it entered
the tree: a GPT dump's protective-MBR signature was never learned (it is
only in the entry's PARTUUID, and redact.rs left MBR PARTUUIDs to a
`disk_signature` key a GPT dump hasn't), so it stayed in src.json and
would have in the plan, also as load's octal `printf` bytes, which redact.rs
now maps too; and the redacted dump redacted again by check_plan gave the
signature a hex pseudonym whose digits were the ESP's FAT volume ID's
pseudonym without its dash (the hex pseudonym now avoids the dump's digits
whatever their separators).  golden_plans.rs's backup_sources check now
takes a btrfs source nested in another source's subvolume at the same path
(the slot's `/etc`, bind-mounted onto itself) as reached through that
source's mount.  solus49 (`make-solus.sh`: Solus 4.9 Xfce from its live
ISO as its Calamares installs, replayed by `solus/install.sh`, then `eopkg
up`; UEFI with Secure Boot on and Solus's MOK in shim's MokList, GPT with a
2 GiB ESP (GPT name `EFI`), an 8 MiB BIOS boot partition and ext4 /, the ESP
out of fstab; clr-boot-manager's shim and systemd-boot in
EFI/com.solus-project, kernels 6.18.21 and 7.3.4 with prebuilt initrds;
docs/design/solus.md), built on 2026-11-14, was added the same day from a
dump of the live guest on chopin (run `s3`, the static bko-holt 0.2.6 built
from this tree with S2's Solus code, `e4a471b4...`; its own vm-run-options,
so under Secure Boot through the fallback path on a fresh variable store,
the disk under `vm-run --snapshot`, the image's sha256 the same before and
after; its provenance.tsv written by hand).  It was dumped after
`clr-boot-manager mount-boot`: without it, dump refuses, the ESP being out
of fstab and unmounted.  It is in the corpus before it is one of
classes.json's HoltTest2 sources, so golden_plans.rs notes it until it is.
Its dump is the first with `boot.loader.manager` (`clr-boot-manager`) and an
ESP with `in_fstab: false`.  Its golden plan mounts the ESP at /boot for the
restore, keeps the BIOS boot partition, and at boot installs no loader,
rebuilds no initramfs and runs no clr-boot-manager (notes say why): it makes
only the "Solus Linux Bootloader" NVRAM entry for
`\EFI\com.solus-project\bootloaderx64.efi`, as the source had.
arm64-grub-shim and arm64-systemd-boot (docs/design/arm64.md, A3), added
on 2026-11-14, are the corpus's only fixtures: no guest's.  ARM64 has no
guests yet (A5), so each is `bko-holt dump` of a fake ARM64 Debian 13 tree
in holt/src/arch.rs's tests (`arm64_grub_shim`, `arm64_systemd_boot`):
`/proc/sys/kernel/arch` aarch64, UEFI, GPT with an ESP and ext4 /.
arm64-grub-shim has Debian's arm64 shim and GRUB (`EFI/debian/shimaa64.efi`,
`grubaa64.efi`, `mmaa64.efi`, shim as `EFI/BOOT/BOOTAA64.EFI` with
`fbaa64.efi`, `/boot/grub/arm64-efi`, grub-efi-arm64-signed), the ESP at
/boot/efi and the firmware booted shim's NVRAM entry; arm64-systemd-boot
has the ESP as /boot (the Arch and Fedora layout) holding
`EFI/systemd/systemd-bootaa64.efi` and its copy at the fallback path, the
firmware booted its NVRAM entry.  Their identifiers are made up, so they
were never redacted, and corpus_build doesn't make them:
`arm64_corpus_fixtures_are_these_dumps` holds each src.json to its tree's
dump (the tool's version aside; `os.architecture`, the dumping binary's
own, written as the aarch64 bko-holt's), and `BKO_HOLT_GOLDEN=bless`
rewrites them.  index.tsv's rows say `fixture`, run `a3`, no host; their
check_plan values are that test's.  Their golden plans run `grub-install
--target=arm64-efi --bootloader-id=debian --no-nvram` and `update-grub`,
then the entry for `\EFI\debian\shimaa64.efi`; and `bootctl install
--no-variables`, then the entry for `\EFI\systemd\systemd-bootaa64.efi`.
Load plans them only as an ARM64 rescue system: common/mod.rs gives the
fake rescue system each dump's own architecture, as check_plan does.
uc24 and uc24-fde (`make-ubuntu-core.sh`: Ubuntu Core 24 from Canonical's
signed `ubuntu-core-24-amd64.img.xz` with a NoCloud CIDATA drive, the pc
gadget's GPT of BIOS Boot, ubuntu-seed (vfat, the ESP type), ubuntu-boot,
ubuntu-save and ubuntu-data, then `snap refresh` and a second recovery
system; uc24 installed with no TPM, so unencrypted; uc24-fde with swtpm and
Secure Boot, so ubuntu-save and ubuntu-data are LUKS2 volumes whose keys
are sealed to the TPM; docs/design/ubuntu-core.md), built on 2026-11-14,
were added on 2026-11-15 from U2's dumps of the live guests on copland (run
`u3`, the static bko-holt 0.2.6 built from this tree with U2's Ubuntu Core
code, `b0bcc7acabb7...`, as root in run mode, where snap-bootstrap has
opened both volumes, so no recovery key was needed; each guest on a qcow2
overlay and copies of its UEFI variables and, for uc24-fde, its swtpm
state, so through shim's NVRAM entry and an unattended unlock; the image
never written; their provenance.tsv written by hand).  Their dumps are the
first with `boot.snapcore` (the four partitions by GPT name, modeenv, the
model, os-release from the base snap, the seed's two recovery systems,
every snap revision, the MBR code and BIOS Boot's bytes) and, on uc24-fde,
`storage.luks_headers` (each LUKS2 header kept as a file by `cryptsetup
luksHeaderBackup`: its name, size and SHA-256, never the header).  Their
golden plans make the gadget's table exact, `mkfs.fat -i` the seed, and
mount the four roots at `/mnt/bko-target/run/mnt` (no `/`: Ubuntu Core's
root is the base snap); uc24-fde's put both headers back with
`luksHeaderRestore` and open them with `--key-file` (no luksFormat, no
luksAddKey).  At boot they regenerate nothing: the check of modeenv against
the restored trees, `bko-holt snapcore-boot-areas`, the log and the atimes;
no grub-install, update-grub, chroot or NVRAM entry.  The kept headers' key
files and header files are the golden plans' own (common/mod.rs, "The
golden plans" below).  The plans are the same from a825828's load (U3) as
from this tree's.
elementary8 (`make-from-live.sh elementary`: elementary OS 8.1 from its
live ISO, its squashfs copied and the boot loader installed as distinst
does, in distinst's layout; UEFI with Secure Boot, GPT with a 278 MiB ESP
at /boot/efi and one LVM PV, the VG `data` with `root` (ext4 /) and `swap`;
shim and signed GRUB in EFI/ubuntu, with distinst's grub.cfg on the ESP;
docs/design/elementary.md) and elementary8-luks (`--luks`: the same with an
ext4 /boot of 1 GiB and the PV inside LUKS2 `cryptdata`, crypttab
`cryptdata UUID=... none luks`, on a 20G disk), built on 2026-11-14 and
2026-11-15, were added on 2026-11-15 from dumps of the live guests on
chopin (run `e4`, the static bko-holt 0.2.6 built with E2's elementary
row, `8f5e153b...`; their own vm-run-options, so under Secure Boot through
shim's fallback on a fresh variable store, the disks under `vm-run
--snapshot`, elementary8-luks booted through console-boot.py, which typed
its console-passphrase at cryptsetup-initramfs's prompt; the images'
sha256 the same before and after; their provenance.tsv written by hand,
generation `-`: the guests have no image-state yet).  Their golden plans
make the VG `data` again with `pvcreate --uuid` and `vgcfgrestore` (on
elementary8-luks inside `cryptsetup luksFormat --type=luks2 ... --uuid`
and `cryptsetup open ... cryptdata`, an ext4 /boot mounted beside), and at
boot run `update-initramfs -u -k all`, `grub-install --target=x86_64-efi
--bootloader-id=ubuntu --no-nvram` and `update-grub` with os-prober off,
then make the `elementary OS 8` NVRAM entry for `\EFI\ubuntu\shimx64.efi`.
Their `scrubbed` 8 is step 3's e-mail scrub of the ESP's GRUB locale
files named NAME@VARIANT with a `.mo` suffix (`sr@latin` and the like) in
`EFI/ubuntu/grub/locale`: each becomes the path
`EFI/ubuntu/grub/locale/builder-at-redacted`, so the ESP's file list has
eight files at that one path, their sizes kept (load only looks names up
there, and BackShot2's ESP usage only sums the sizes).  They are in the
corpus before either has had a HoltTest1 PASS (elementary8's
`elem6c2`/`elem6q2` FAILed only on os-prober's grub.cfg entries, fixed in
1677865; elementary8-luks has had no run).
hardenedbsd15-zfs (`make-hardenedbsd.sh 15 23 zfs`: HardenedBSD
15.1-STABLE-HBSD from 15-STABLE's installer build 23, hardenedbsd15's twin
on a ZFS root as ghostbsd26-zfs is ghostbsd26's; BIOS + GPT with
freebsd-boot on p1, 2 GB of swap on p2 and the pool `zroot` on p3 with
bsdinstall's boot environments and datasets, the base system only, not even
pkg), built on 2026-11-15, was added the same day from a dump of the live
guest on chopin (run `hz1`, the promoted static bko-holt 0.2.6 from
holt-bin, `763b88c4...`, checked against its SHA256SUMS; the disk under
`vm-run --snapshot`, the image's sha256 the same before and after; its
provenance.tsv written by hand, generation `-`: the guest has no
image-state yet).  It is in the corpus before it has had a HoltTest run.
Its golden plan writes the source's table and boot code with `bko-holt
write-table`, makes `zroot` with the source's feature flags, ashift 12 and
its GUID, the datasets with their properties (`zroot/usr/src` too, which
ghostbsd26-zfs hasn't), sets `bootfs`, and at boot points the restored
zpool.cache at the new pool; nothing else is regenerated.
bs1-ubuntu2610-zfsenc is the corpus's first dump of a real machine, not a
guest's: BackShot1's Thinkpad (bs1-backup's `bko-holt dump --only-disk
nvme0n1`, bko-holt 0.2.6, 2026-11-18) on Ubuntu 26.10 as its desktop
installer lays out "ZFS + encryption": GPT with a 1 GiB ESP at /boot/efi,
an ext4 /boot, swap as plain dm-crypt (crypttab `dm_crypt-0 PARTUUID=...
/dev/urandom ...,plain,swap`, made at every boot with a random key) and
rpool with native encryption, its key `file:///run/keystore/rpool/system.key`
on the keystore, LUKS2 `keystore-rpool` on the zvol rpool/keystore (zd0),
ext4, which the initramfs mounts at /run/keystore/rpool; dracut, GRUB
through shim.  That 0.2.6 dump left zd0 out (--only-disk kept whole disks by
name, and a zvol is one) and listed the backup drive's and the keystore's
mounts in backup_sources, which this tree's dump no longer does (report.rs
build_with, not_backed_up).  So its raw dump is the real one as this tree's
dump makes it, done by hand before the build (corpus_build ran on it as
on any other): zd0 added with what the dump itself records of it (the
zvol's `volsize`, dm-0's slave and LUKS UUID, its `/dev/zvol/rpool/keystore`
link) and a `crypto_LUKS` content of type, UUID and version only, since
nothing in the dump has its LUKS2 header's cipher, offset or keyslots; and
the five backup_sources not backed up taken out.  Its provenance.tsv was
written by hand: host `realworld`, run `bs1-backup`, generation and image
`-`.  It is in the corpus before it is any HoltTest2 source (no guest has
this layout), so golden_plans.rs notes it.  Its golden plan makes the plain
swap's partition and nothing on it (a Note says crypttab makes it at
boot), creates the zvol `encryption=off` under the encrypted rpool,
formats the keystore with cryptsetup's defaults and the source's UUID
(asking for the new passphrase; BackShot1 gives `--key-file
keystore-rpool=`its drive's key), makes its ext4 and installs rpool's new
raw key there, then at boot sets rpool's keylocation back, runs dracut,
`grub-install --bootloader-id=ubuntu --no-nvram` and `update-grub`, and
makes the `Ubuntu` entry for `\EFI\ubuntu\shimx64.efi`.  The redaction
named `dm_crypt-0` `label-1` (it is also the swap's label).
ubuntu24-uefi (`make-ubuntu.sh --uefi`, 24.04.5: Subiquity's default, GPT
with an ESP at /boot/efi and one ext4 /, UEFI under Secure Boot) and
ubuntu24-luks (`--uefi --luks`: ubuntu26-luks's guided LVM on LUKS2 on
24.04.5, an ext4 /boot beside the ESP and `dm_crypt-0` holding
`ubuntu-vg/ubuntu-lv`, ext4 /), ubuntu26-uefi's and ubuntu26-luks's
24.04 twins built on 2026-11-14, were added on 2026-11-17 from their
HoltTest1 runs u24c on chopin, PASS, bko-holt 0.2.4 (collected on
2026-11-14 into a private directory, the image the same as the run
recorded).  Both were already HoltTest2 sources in classes.json, so
golden_plans.rs had them as unplanned until now.  Their golden plans are
their twins' but for what the sources are: mke2fs without `orphan_file`
and `metadata_csum_seed` (24.04's ext4s were made without them), and
`update-initramfs -u -k all` where the twins run dracut (24.04 is on
initramfs-tools); ubuntu24-luks's `cryptsetup luksFormat` has the twin's
cipher, key size, offset and LUKS2 area sizes and the source keyslot's own
argon2id cost (1004442 KiB, 4 iterations, where ubuntu26-luks has 97932
and 59), then `cryptsetup open ... dm_crypt-0` and `vgcfgrestore`, and both
keep GRUB and shim (`grub-install --no-nvram`, `update-grub`, the `Ubuntu`
entry for `\EFI\ubuntu\shimx64.efi`).
The others weren't collected again ("Adding a source", below).  index.tsv's `host`, `run`, `result`,
`report_time` (report.txt's mtime), `generated` (the dump's own time),
`tool_version` and `generation` (the guest's image-state generation at
collection; `-` for the guests without one) say which, and `image` whether
the guest's qcow2 still had the size, mtime and inode its run recorded
(`changed` for 34 of r4all's: debian13's, for one, kept its size and inode
and only its mtime moved, to 03:52Z on 2026-11-08, after r4all; `same` for
the eight added since).

The raw dumps hold the guests' serials, UUIDs and host names.  They stay on
chopin, in the collector's directory (by default `/bkoscratch/test2/h1-raw`,
mode 0700), and never reach the Mac or the repository.  The four added on
2026-11-10 were collected into private directories and deleted after the
build, so that default directory holds only r4all's 45 until the next full
collection.

## What was done to them

`holt/tests/corpus_build.rs` (an `#[ignore]`d test, run by hand where the
raw dumps are), for each dump:

1. `bugreport::check_plan` on the raw dump: load's plan of its redaction is
   its own, pseudonyms aside, and no identifier is left.  index.tsv's
   `check_plan_raw` is `ok:IDENTIFIERS:PLAN_LINES`, or the failure's class.
2. `redact::redact`, the dump on its own (twice: the same bytes), then its
   own leak checks on the redacted dump and on its plan
   (`Redactor::leaks`).
3. Every e-mail-shaped token in a string value becomes
   `builder-at-redacted`: redact.rs has no kind for them, and two dumps
   carry a kernel build signature (alpinebsd's and netbsd11's
   `os.kernel_version`, NetBSD's build address), as are the eight GRUB
   locale names, NAME@VARIANT with a `.mo` suffix, on elementary8's and elementary8-luks's
   ESPs.  A word of
   `os.kernel_version` or `os.kernel_release` (which redact.rs leaves as
   they are) that is a hostname redact.rs replaced becomes that hostname's
   pseudonym (dragonfly6's build signature, debian13's release tag).
   index.tsv's `scrubbed` counts both.
4. Third-party boot code (FreeBSD's and NetBSD's boot blocks, illumos's and
   Sculpt's ESP images, MBR boot code) is replaced by a fixed filler of the
   same length (the bytes `bko-holt corpus: boot code left out, see
   holt/tests/corpus/README.md\n`, ending in a newline, repeated): every `esp_image[].b64`,
   `boot_code.image_b64`, `boot_code.code_b64` and `boot_code.gap_b64`,
   and Ubuntu Core's `boot.snapcore.boot_areas.mbr_code_b64` (the pc
   gadget's 440 bytes) and `bios_boot.image_b64` (since uc24's).
   index.tsv's `filled` lists each as `PATH=BYTES/CRC32`, the length and
   CRC-32 of its redacted bytes; `blobs` and `blob_bytes` sum them (139
   blobs, 7.7 MB decoded, in 15 dumps).  EFI load options (`load_option_hex`) and
   `first_bytes` stay: they are data, and redact.rs has redacted them.
5. The filled dump is planned against the unfilled one (`fill_plan`:
   `same` for all 61) and passes check_plan itself (`check_plan`).

Two builds of the same raw dumps give the same bytes.

### A raw dump that fails step 1

All 61 raw dumps pass check_plan.  A dump that one day fails it is still put
in the corpus (step 2's leak checks, step 5 and leakscan below still apply);
corpus_build then fails at its end, and so does `redact_dumps` over the raw
dumps.  Nothing would show that dump's golden plan is its guest's own, so
golden_plans.rs reads index.tsv and lists each such dump (and its variants)
in diff.json's `differ` with `"unverified"`, even when the plan is the same,
and HoltTest2 runs them on any plan-visible change instead of carrying them.
Check mode only logs them.

### Checked for leaks

`testvm/test2/t2lib/leakscan.py` found nothing:

- (a) patterns, on chopin and on the Mac: MAC addresses other than
  redact.rs's pseudonyms, private and link-local addresses, e-mail
  addresses, keys, the hosts' names; in every string value, every blob
  (decoded, read as latin-1 and as UTF-16LE), every plan; the Mac's run with
  the repository's git e-mail as a string too;
- (b) with both hosts' strings (the guests' passphrase files, disk serials
  and WWIDs, LAN addresses, MACs; 125 of 8 bytes or more) and each raw
  dump's own identifiers (serials, WWIDs, UUIDs as text and as bytes,
  machine-ids, GUIDs, MACs).  `collect-dumps.sh --scan` now runs (b) on each
  host against that host's own strings, which never leave it (the corpus,
  public anyway, is copied there), and the raw identifiers on chopin; a
  corpus dump without its raw twin, a torn strings line or a git without an
  e-mail is an incomplete scan, exit 2.

The three added on 2026-11-10 went through (a) and (b) the same way, 0
findings: chopin with its 66 strings and their raw dumps' identifiers,
cinqcent with its 69, and (a) again on the Mac with the git e-mail, before
they entered the working tree.  So did ubuntu26-luks, scanned from cinqcent
(chopin with its 66 strings, cinqcent with its 71 and the raw dump's
identifiers) and on the Mac, 0 findings.  So did chimera (2026-11-11), collected
on chopin into private directories that were deleted after the build: chopin
with its 78 strings and the raw dump's identifiers, cinqcent with its 82,
and the Mac with the git e-mail, 0 findings.  Its two builds gave the same
sha256.  So did kinoite44 (2026-11-11), collected on chopin into a private
directory in its RAM disk, deleted after the build: chopin with its 79 strings
and the raw dump's identifiers, cinqcent with its 83, the Mac with the git
e-mail, 0 findings, and the same sha256 from both builds.  So did chimera-uefi
and chimera-zfs (2026-11-11), collected into private directories to be
deleted after the build: chopin's and cinqcent's scans 0 findings, the Mac
with the git e-mail 0 findings, and two builds of each the same sha256.
So did gentoo (2026-11-12), built from a private directory in chopin's RAM
disk: chopin with its 83 strings and the raw dump's identifiers, cinqcent
with its 89, the Mac with the git e-mail, 0 findings, the same sha256 from
both builds.  So did gentoo-systemd (2026-11-12, the same way): chopin
with its 84 strings and the raw dump's identifiers, cinqcent with its 88,
the Mac, 0 findings, the same sha256 from both builds.  So did cachyos-zfs
(2026-11-13), built from a private directory in copland's RAM disk (Chopin
was down): copland with its 76 strings and the raw dump's identifiers,
cinqcent with its 77, the Mac with the git e-mail, 0 findings, the same
sha256 from both builds.  So did pikaos4 (2026-11-14), built from a private
directory in cinqcent's RAM disk: cinqcent with its 81 strings and the raw
dump's identifiers, chopin with its 80, the Mac with the git e-mail, 0
findings (its plan scanned too), the same sha256 from both builds.  So did
pikaos4-luks (2026-11-14, the same way, its two builds run outside the
ledger, which a queue of HoltTest1 runs held): cinqcent with its 89
strings (its console-passphrase among them) and the raw dump's identifiers,
chopin with its 90, the Mac with the git e-mail, 0 findings, its plan
scanned too, the same sha256 from both builds.  So did
anduinos2 (2026-11-14), built from a private directory in cinqcent's RAM
disk: cinqcent with its 81 strings and the raw dump's identifiers, chopin
with its 80, the Mac with the git e-mail, 0 findings (its plan scanned
too), the same sha256 from both builds.  So did neon24 (2026-11-14), built
from a private directory on chopin (/bkoscratch, its warm holt build):
chopin with its 80 strings and the raw dump's identifiers, cinqcent with
its 81, the Mac with the git e-mail, 0 findings (its plan scanned too),
the same sha256 from both builds.  So did garuda (2026-11-14), built from
a private directory on chopin (/bkoscratch, its warm holt build): chopin
with its 90 strings and the raw dump's identifiers, cinqcent with its 89,
the Mac with the git e-mail, 0 findings (its plan scanned too), the same
sha256 from both builds.  So did biglinux (2026-11-14), built from a
private directory on cinqcent (/bkoscratch/holt-warm/bl3, deleted after):
chopin with 91 of its 96 strings, cinqcent with 93 of its 97 and the raw
dump's identifiers, the Mac with the git e-mail, 0 findings (its plan
scanned too), the same sha256 from both builds.  So did freebsd15-geli (2026-11-14),
built from a private directory in chopin's RAM disk (its warm holt build
at /bkoscratch/holt-warm/geli): chopin with its 90 strings (its
console-passphrase among them) and the raw dump's identifiers, cinqcent
with its 89, the Mac with the git e-mail, 0 findings, and a check of the
src.json, index row and plan for the passphrase's bytes (as is and as
UTF-16LE): none; the same sha256 from both builds.  So did freebsd15-geli-bios and
freebsd15-geli-ufs (2026-11-17), built twice from a private directory on
chopin (/bkoscratch, its warm holt build at /bkoscratch/holt-warm/geli2):
chopin with its 143 strings and the raw dumps' identifiers, cinqcent with
its 121, the Mac with the git e-mail, 0 findings (their plans scanned
too). A separate check of the src.json files, index rows and plans looked
for both guests' passphrases, the slot-1 passphrase and p4's key file (as
they are, as UTF-16LE, in hex and in base64) and found none. Both builds
gave the same sha256.  So did kali
(2026-11-14), built from a private directory in cinqcent's RAM disk (its
warm holt build at /bkoscratch/holt-warm/kali): cinqcent with its 92
strings and the raw dump's identifiers, chopin with its 91, the Mac with
the git e-mail, 0 findings (its plan scanned too), the same sha256 from
both builds.  So did guix15 and guix15-bios (2026-11-14), built from a
private directory on chopin (/bkoscratch, its warm holt build at
/bkoscratch/holt-warm/guix) and again on cinqcent from a host-to-host copy
of the raw dumps: chopin with its 94 strings and the raw dumps'
identifiers, cinqcent with its 95, the Mac with the git e-mail, 0 findings
(their plans scanned too), the same sha256 from all three builds (src.json
`1936101...` and `c504094...`, the rows `9a3227c...`).  So did proxmox9
and proxmox9-mirror (2026-11-14), built from a private directory on chopin
(/bkoscratch, its warm holt build at /bkoscratch/holt-warm/proxmox): chopin
with its 98 strings and the raw dumps' identifiers, cinqcent with its 99,
the Mac with the git e-mail, 0 findings (their plans scanned too), the same
sha256 from both builds (src.json `8efb9b6c...` and `d0b597ea...`).  So did
vanilla3 (2026-11-14), built from a private directory on chopin
(/bkoscratch, its warm holt build at /bkoscratch/holt-warm/vanilla): chopin
with its 98 strings and the raw dump's identifiers, cinqcent with its 99,
the Mac with the git e-mail, 0 findings (its plan scanned too), the same
sha256 from both builds (src.json `a95eef1e...`).  Its first build had
one finding in (b), the raw `lvol0_pmspare` UUID that redact.rs had left
(above); it never left chopin, and the build after the fix had none.  So
did truenas25 (2026-11-14), built from a private directory on chopin
(/bkoscratch, its warm holt build at /bkoscratch/holt-warm/truenas):
chopin with 98 of its 103 strings and the raw dump's identifiers, cinqcent
with 100 of its 106, the Mac with the git e-mail, 0 findings (its plan
scanned too), and a check of the src.json and index row for its
admin-password's bytes (as is and as UTF-16LE): none; the same sha256 from
both builds (src.json `0b5f0b6f...`).  So did void-zbm (2026-11-14), built
from a private directory on chopin (/bkoscratch, its warm holt build at
/bkoscratch/holt-warm/zbm): chopin with 101 of its 106 strings and the raw
dump's identifiers, the Mac with the git e-mail, 0 findings (its plan
scanned too), the same sha256 from both builds (src.json `7c1de82a...`).
Cinqcent's scan with its own strings came later (2026-11-15, from
chopin with `--hosts cinqcent`): 116 strings, 0 findings.  So did leap16
(2026-11-14), built from a private directory on chopin (/bkoscratch, its
warm holt build at /bkoscratch/holt-warm/l16-opensuse): chopin with its
101 strings and the raw dump's identifiers, cinqcent with its 106, the Mac
with the git e-mail, 0 findings (its plan scanned too), the same sha256
from both builds (src.json `23a09f8e...`).  Its first build stopped at check_plan's leak
check, which isn't a leak: the warm build's directory was named after the
guest, whose host name is `leap16`, and check_plan's plan holds the test
binary's path (golden_plans.rs names it `bko-holt`; check_plan doesn't).  So did
microos (2026-11-14), built from a private directory on chopin
(/bkoscratch/m3, its warm holt build at /bkoscratch/holt-warm/m3-microos):
chopin with its 101 strings and the raw dump's identifiers, cinqcent with
its 106, the Mac with the git e-mail, 0 findings (its plan scanned too),
the same sha256 from both builds (src.json `6d605e1f...`, plan.txt
`a8e87cb3...`).  Its first builds had one finding in (b), the protective
MBR's signature (above); they never left chopin, and the builds after the
fix had none.  So did solus49 (2026-11-14), built from a private directory
on chopin (/bkoscratch, its warm holt build at /bkoscratch/holt-warm/solus):
chopin with 101 of its 106 strings and the raw dump's identifiers, cinqcent
with 106 of its 112, the Mac with the git e-mail, 0 findings (its plan
scanned too), the same sha256 from both builds (src.json `89c50dd1...`,
the row `9022931f...`).  So did uc24 and uc24-fde (2026-11-15), built from a
private directory on chopin (/bkoscratch, its warm holt build at
/bkoscratch/holt-warm/uc-u7) from a host-to-host copy of the raw dumps from
copland: chopin with 107 of its 112 strings and the raw dumps' identifiers,
cinqcent with 110 of its 116, copland with 86 of its 91, the Mac with the
git e-mail, 0 findings (their plans scanned too), and a check on chopin of
the src.json files, the rows and the plans for uc24-fde's recovery key (as
typed, as its 40 digits, each 5-digit group, and its 16 key bytes raw, in
hex and in base64; as is and as UTF-16LE) and for any recovery-key-shaped
string (eight dashed groups of five digits): none; the same sha256 from
both builds (src.json `f6654a89...` and `0f0883f7...`).  So did elementary8 and elementary8-luks (2026-11-15), built
from a private directory on chopin (/bkoscratch/elem-holt, its warm holt
build at /bkoscratch/holt-warm/elementary): chopin with 109 of its 114
strings (elementary8-luks's console-passphrase among them) and the raw
dumps' identifiers, cinqcent with 110 of its 116, the Mac with the git
e-mail, 0 findings (their plans scanned too), the same sha256 from both
builds (src.json `44ab47f9...` and `1b38135b...`, the rows `ccfea5b7...`).
So did hardenedbsd15-zfs (2026-11-15), built from a private directory on
chopin (its warm holt build at /bkoscratch/holt-warm/hbsdzfs): chopin with
its 110 strings and the raw dump's identifiers, cinqcent with its 116, the
Mac with the git e-mail, 0 findings (its plan scanned too), the same sha256
from both builds (src.json `8aab1604...`, and index.tsv).  So did
bs1-ubuntu2610-zfsenc (2026-11-18), built from a private directory on
chopin (/bkoscratch/holt-warm/zfsenc, its warm holt build there): chopin
with 113 of its 118 strings and the raw dump's identifiers, cinqcent with
116 of its 122, the Mac with the git e-mail, 0 findings (its plan scanned
too), the same sha256 from both builds (src.json `db660138...`).  So did void-zbm-enc (2026-11-16), built
from a private directory in chopin's RAM disk (its warm holt build at
/bkoscratch/holt-warm/zbm): chopin with 134 of its 139 strings (its
zfs-passphrase among them) and the raw dump's identifiers, cinqcent with
110 of its 116, the Mac with the git e-mail, 0 findings (its plan scanned
too), and a check on chopin of the src.json, the index row and the plan
for the passphrase's bytes (as is, as UTF-16LE and in hex): none; the same
sha256 from both builds (src.json `fff7adb5...`, the row `4389b276...`).
So did ubuntu24-uefi and ubuntu24-luks (2026-11-17), built from a private
directory on chopin (/bkoscratch, its warm holt build at
/bkoscratch/holt-warm/u24): chopin with 110 of its 115 strings
(ubuntu24-luks's console-passphrase among them) and the raw dumps'
identifiers, cinqcent with 111 of its 117, the Mac with the git e-mail, 0
findings (their plans scanned too), the same sha256 from both builds
(src.json `adbecc72...` and `9cffbfc9...`, index.tsv `1a433419...`).

## The golden plans

Each dump is planned as check_plan plans it, through holt's public API
only (holt/tests/common/mod.rs): a fake rescue system with a blank disk
`sdq`, `sdr`, ... of each dumped disk's size (1 GiB more for `+bigger`),
`--map NAME=TARGET`, `--repo /srv/bko-repo`, a rustic-bko that says it is
BackOtter's, every tool the plan asks for installed.  Unlike check_plan it
maps no RAM disk or ZFS volume (alpine324's 16 RAM disks would each be a
Problem), installs load's own binary where it runs from, and names it
`bko-holt` in the plan, which would otherwise hold the test binary's path.
A ZFS encryption root with a passphrase gets `--key-file NAME=/srv/bko-keys/NAME`,
and each LUKS2 header the dump kept (`storage.luks_headers`, uc24-fde's two)
`--key-file LABEL=/srv/bko-keys/LABEL` by its LUKS label, where a real load
gives the recovery key's bytes (`bko-holt recovery-key-file`): fixed test
paths, never a real key.  Load
restores a kept header from the file dump wrote and checks it by its
SHA-256, and the corpus holds no header, so the fake rescue system has a
test file for each (a line naming it), given with `--luks-header-dir` and
named `/srv/bko-luks-headers` in the plan, and the dump planned has their
SHA-256 in place of the real headers' (in memory only: src.json keeps the
dump's).
All 84 plans exit 0, without Problems.  They are Linux's plans (load's
run directory is /run/bko-holt there, /var/run/bko-holt elsewhere): run
golden_plans.rs on a Linux host.

Each plan must keep five invariants: / mounted (on Ubuntu Core, which has
none, its four run-mode roots at /mnt/bko-target/run/mnt); every fstab mount on a
dumped device or filesystem planned (or named in a Note); the archiver
found; the disks of every ZFS pool, md array, LVM volume group and
multi-device btrfs named by the command making it; the disk IDs
round-tripped (an MBR's signature and a GPT's GUIDs in sfdisk's input, and
for the tables load writes itself, `load::writetable::plan`'s bytes read
back by `parttable`).  golden_plans.rs's header says how each is checked;
each has a synthetic negative test there (`golden_invariant_*`).

One more check ties `dump` to load (docs/design/bazzite.md, decision 4):
`golden_backup_sources_are_loads_mounts` plans every dump that has
`storage.backup_sources` (the tree BackShot1 backs up, which `dump` writes
since Bazzite's B2) and fails unless its paths are exactly the mount points
the plan's mount stage mounts at /mnt/bko-target (a `zfs mount` counts by its
dataset's mountpoint).  Load builds its tree from fstab, `dump` from the
running mounts, so this holds the two rules together.  The older dumps have
no `backup_sources` and are passed over; kinoite44's, chimera-uefi's,
chimera-zfs's, gentoo's and gentoo-systemd's have it.
`golden_backup_sources_check` is its synthetic test.

```
cd holt
cargo test --test golden_plans                                     # check: any difference fails
BKO_HOLT_GOLDEN=report BKO_HOLT_PLANS_OUT=DIR cargo test --test golden_plans -- --nocapture
                                                                   # DIR/NAME.plan and DIR/diff.json; only invariants fail
BKO_HOLT_GOLDEN=bless cargo test --test golden_plans -- --nocapture
                                                                   # rewrite the plans that differ, unless an invariant fails
```

diff.json is `{"v":1, "corpus":61, "plans":64, "planned":[NAME...],
"sources":62, "differ":{NAME:{"stages":[...], "lines_changed":N[, "new":true]
[, "unplanned":WHY][, "unverified":CHECK_PLAN_RAW]}}, "invariants":{NAME:[...]},
"invariants_skipped":{NAME:[...]}}`; `stages` are the plan's sections that
changed (header, partition, assemble, format, mount, restore, boot, close,
trailer), as `testvm/test2/t2lib/plandiff.py` splits them.

The corpus must hold together in every mode: a directory with a plan but no
src.json, a `plan+VARIANT.txt` that variants.txt doesn't list, or variant
plans without a variants.txt fail the test.  Where
`testvm/test2/classes.json` is beside holt/ (a checkout or a private copy),
every HoltTest2 source (holt.families, holt.no_family) needs a dump here and
every `holt.variants.bigger` guest a `plan+bigger.txt`; a cell without one is
a difference, `"unplanned"` in diff.json, so it runs instead of being
carried.  `testvm/test2/tests/test_plandiff.py` checks the same lists on the
Mac.  Every test in golden_plans.rs
is named `golden_*`, and GateTest2 runs the rest of holt's tests with
`cargo test -- --skip golden_`.

After a change to load: run `report`, read the plans that differ, and
`bless` them in the same commit as the change.  HoltTest2 then runs the
guests whose plans changed.

## Rebuilding it

On chopin, as m, from the root of a private copy of the tree, with the Rust
build in a reserved RAM directory (NAME, yours).  RAW and OUT are absolute
paths: cmp-run.sh changes to ~/backotter-work, and cargo test runs in holt/.

```
testvm/test2/h1/collect-dumps.sh --out RAW         # default T2_HOST_ROOT/h1-raw
~/backotter-work/cmp-run.sh --reserve 2 4 env CARGO_TARGET_DIR=/mnt/bko-ram/NAME/target \
    BKO_HOLT_CORPUS_RAW=RAW BKO_HOLT_CORPUS_OUT=OUT ~/.cargo/bin/cargo test \
    --manifest-path "$PWD/holt/Cargo.toml" --test corpus_build -- --ignored --nocapture
                                                   # twice, into two OUTs: the same sha256s
testvm/test2/h1/collect-dumps.sh --scan OUT --out RAW
                                                   # leakscan (a) and (b) on both hosts: 0 findings
rm -rf /mnt/bko-ram/NAME
```

Then copy OUT's `GUEST/src.json` and `index.tsv` to a staging directory on
the Mac, outside the repository, and scan it there first:
`leakscan.py scan --git-email . STAGING` must give 0 findings and exit 0.
Only then copy them into holt/tests/corpus (corpus_build refuses an OUT
that holds dumps it doesn't make, so build into an empty one), and bless
the plans.  A corpus dump never enters the working tree with a finding, and
a plan is never blessed with a broken invariant.  The corpus is 12.5 MB (61
dumps of 3 KB to 3.3 MB, the 64 plans 0.5 MB); 15 MB is its budget.

### Adding a source

A new guest doesn't need the others collected again.  It must already be
one of classes.json's HoltTest2 sources: otherwise golden_plans.rs only
notes it, and test_plandiff.py fails.
RAW and OUT here are new, private directories: never the default RAW,
because a collection removes every dump that isn't one of its sources.
Both absolute, and run from the copy's root, as above.

```
printf '%s\n' GUEST... > SOURCES
testvm/test2/h1/collect-dumps.sh --sources SOURCES --out RAW
~/backotter-work/cmp-run.sh --reserve 2 4 env CARGO_TARGET_DIR=/mnt/bko-ram/NAME/target \
    BKO_HOLT_CORPUS_RAW=RAW BKO_HOLT_CORPUS_OUT=OUT ~/.cargo/bin/cargo test \
    --manifest-path "$PWD/holt/Cargo.toml" --test corpus_build -- --ignored --nocapture
                                                   # twice, into two OUTs: the same sha256s
testvm/test2/h1/collect-dumps.sh --scan OUT --out RAW
                                                   # 0 findings on both hosts
rm -rf /mnt/bko-ram/NAME
```

Scan OUT on the Mac as above.  Then copy OUT's `GUEST/src.json` in, merge
the rows of OUT's index.tsv into index.tsv in guest order (the columns are
the same), and check the merge, because golden_plans.rs doesn't notice a
dump with no row.  From holt/tests/corpus this must print nothing:

```
diff <(tail -n +2 index.tsv | cut -f1) <(for d in */src.json; do echo "${d%/src.json}"; done | LC_ALL=C sort)
```

Then run `report`.  `differ` must name only the new guests, each
with `"new":true`, and `invariants` must be empty.  Then `bless`, check
that no other guest's files changed, and run the plain check.  Delete RAW
and OUT, and `/mnt/bko-ram/NAME` if `report` or `bless` built there again.
`collect-dumps.sh --scan` over the whole corpus needs every
dump's raw twin, so it needs a full collection first.
