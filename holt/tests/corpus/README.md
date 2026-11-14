# H1: the dump corpus and its golden plans

One redacted `bko-holt dump` of each of HoltTest2's 61 sources, and the plan
`bko-holt load` makes of each (docs/design/test2.md, "Shared pieces to
build", H1).  `holt/tests/golden_plans.rs` plans every dump again and fails
when a plan changes or breaks an invariant, so a change to load shows here
first, as a diff of plans, and HoltTest2 runs the guests whose plans
changed.

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
fallback path).  The others weren't collected again ("Adding a source", below).  index.tsv's `host`, `run`, `result`,
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
   `os.kernel_version`, NetBSD's build address).  A word of
   `os.kernel_version` or `os.kernel_release` (which redact.rs leaves as
   they are) that is a hostname redact.rs replaced becomes that hostname's
   pseudonym (dragonfly6's build signature, debian13's release tag).
   index.tsv's `scrubbed` counts both.
4. Third-party boot code (FreeBSD's and NetBSD's boot blocks, illumos's and
   Sculpt's ESP images, MBR boot code) is replaced by a fixed filler of the
   same length (the bytes `bko-holt corpus: boot code left out, see
   holt/tests/corpus/README.md\n`, ending in a newline, repeated): every `esp_image[].b64`,
   `boot_code.image_b64`, `boot_code.code_b64` and `boot_code.gap_b64`.
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
scanned too), the same sha256 from both builds.  So did kali
(2026-11-14), built from a private directory in cinqcent's RAM disk (its
warm holt build at /bkoscratch/holt-warm/kali): cinqcent with its 92
strings and the raw dump's identifiers, chopin with its 91, the Mac with
the git e-mail, 0 findings (its plan scanned too), the same sha256 from
both builds.

## The golden plans

Each dump is planned as check_plan plans it, through holt's public API
only (holt/tests/common/mod.rs): a fake rescue system with a blank disk
`sdq`, `sdr`, ... of each dumped disk's size (1 GiB more for `+bigger`),
`--map NAME=TARGET`, `--repo /srv/bko-repo`, a rustic-bko that says it is
BackOtter's, every tool the plan asks for installed.  Unlike check_plan it
maps no RAM disk or ZFS volume (alpine324's 16 RAM disks would each be a
Problem), installs load's own binary where it runs from, and names it
`bko-holt` in the plan, which would otherwise hold the test binary's path.
All 64 plans exit 0, without Problems.  They are Linux's plans (load's
run directory is /run/bko-holt there, /var/run/bko-holt elsewhere): run
golden_plans.rs on a Linux host.

Each plan must keep five invariants: / mounted; every fstab mount on a
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
