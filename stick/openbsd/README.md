# BackOtter's rescue image for OpenBSD

`backotter-live-openbsd-VERSION-amd64.img`, written raw to a USB stick, boots
OpenBSD 8.0 from the stick with BIOS or UEFI (not with Secure Boot on:
OpenBSD's EFI loader isn't signed), and shows BackOtter's console on the
screen and on the first serial port.  For now it is a placeholder: the
console, remote access over SSH, `restic-bko` and `rustic-bko`, and OpenBSD's
base tools (fdisk, disklabel, newfs, fsck, bioctl, dump/restore, ...).  No
restore logic yet.

## Building

On a KVM host laid out like chopin (see `../make-img-bsd.sh`):

    ./make-img-bsd.sh openbsd          # the image
    ./make-img-bsd.sh --test openbsd   # the image for boot-test.py
    ./boot-test.py --platform openbsd IMG bios
    ./boot-test.py --platform openbsd IMG uefi

The build VM is a throwaway copy (`--snapshot`) of the test guest
`testvm/make-openbsd.sh 8.0 vms/openbsd8` makes: its comp set compiles the
console's small autologin helper.  `fetch` (in `platform.conf`) takes
`base80.tgz` and `bsd.mp` from that guest's `install80.iso`, or downloads them
when it isn't there; both are checked against the release's `SHA256.sig` with
signify, on the host with the key in `platform.conf` and again in the VM with
its own copy.  `restic-bko` (required) and `rustic-bko` come from
`~/backotter-work/bko-bin/openbsd/`, built natively on OpenBSD: Go and Rust
binaries there link libc dynamically, and the image's base has `ld.so` and
`libc`.  The build takes about 2 minutes on choubox; the image is about
770 MB (the root about 610 MB, of which the two binaries 74 MB).

## How it's made

OpenBSD has no live system, so `build.sh` makes one the way the installer
makes a system: the base set, `bsd.mp` as `/bsd`, `/var/sysmerge/etc.tgz`
(inside base) for `/etc`, and `MAKEDEV all` for `/dev`.  Then:

| MBR slot | Partition (marker) | Contents                                    | disklabel |
|----------|--------------------|---------------------------------------------|-----------|
| 0        | 1                  | ESP, 16 MiB FAT: `efi/boot/BOOTX64.EFI`, `BOOTIA32.EFI` | `i` |
| 1        | 2                  | config partition, FAT16 `BACKOTTER`, 32 MiB (`marker.py`'s `config.img` as is; type 0x0E) | `j` |
| 3        | (4)                | OpenBSD's, active: `biosboot`, the disklabel, the root FFS | `a` |

`fdisk -b` makes slots 0 and 3, `fdisk -e` moves slot 3 past slot 1;
`disklabel -A` makes `a`, and adds the FAT slots as `i` and `j` itself.
`installboot` writes `biosboot` and `/boot` (BIOS); it puts EFI loaders only
on a GPT's ESP, so `build.sh` copies them to the MBR's ESP by hand.  `/etc/fstab`
names the root by the disklabel's DUID; the kernel finds it by the DUID the
boot loader passes, whatever the stick is called on the machine.  The marker
is `/.disk/backotter.json` in the root.

### A read-only root

The root FFS is never written: the kernel mounts it read-only, and `/etc/rc`
is patched (`build.sh` fails if the stock lines it replaces aren't there):

- `mount -uw /` becomes `/usr/local/lib/backotter/boot-setup`, which mounts
  memory file systems (mfs: GENERIC has no tmpfs) on `/tmp`, then `/etc`,
  `/var`, `/dev` (ptys need a writable `/dev`) and `/root`, each filled from
  the root's own copy with `mount_mfs -P`, and an empty `/home` (for remote
  access's user).  It also writes `/etc/hostname.IF` with `inet autoconf` and
  `inet6 autoconf` for every network card it finds, so netstart runs DHCP and
  SLAAC on all of them.  Nothing written survives a reboot.
- `chmod`/`ln` of `/bsd` are gone, and so is `/usr/libexec/reorder_kernel`
  (KARL), which relinks the kernel at every boot; `library_aslr=NO` in
  `rc.conf.local` stops the library relinking, and `/usr/share/relink` (their
  objects) isn't in the image.  So every stick boots the same kernel and
  library layout: the price of a read-only root.
- `make_keys` no longer runs `ssh-keygen -A`: no host keys until remote
  access is turned on.

`rc.conf.local` also turns off sshd, smtpd, sndiod and quota checks;
`/etc/myname` is `backotter`.

### Console

OpenBSD's getty can't log in by itself, so `/etc/ttys` runs
`/usr/local/lib/backotter/autologin` (compiled from `build.sh`) instead of
getty on `ttyC0` (the screen) and `tty00` (the first serial port, 115200,
`local`: no carrier needed).  Like getty it opens the terminal, makes it the
controlling terminal and sets sane modes, then runs `stick/bsd/console`.  On a
machine without that port it sleeps an hour instead of having init retry it
every few seconds.  The other virtual terminals (`ttyC1`-`ttyC5`, Ctrl-Alt-F2
...) keep getty: log in as root with no password, as the console's shell
would give anyway.

OpenBSD's boot loader and kernel use one console.  The image leaves them on
the screen; for the serial port, type `set tty com0` at the `boot>` prompt
(`stty com0 115200` first if needed).  The menu is on both either way.  A
`--test` image differs only in `/etc/boot.conf`'s `set timeout 1`: with the
kernel on com0, QEMU's VGA under SeaBIOS gets no wsdisplay ("wsdisplay at
vga1 not configured"), so there'd be no screen console to check.  Hence
`boot-test.py` sees the boot loader and kernel only as far as the menu
shows; a failed boot shows up as a timeout.

The boot loader says `cannot open hd0a:/etc/random.seed`: the image has no
seed file, since every stick would share it and the boot loader would mark it
used on the stick at each boot.  It seeds the kernel from the CPU instead
("random: good seed from bootblocks").

### Remote access

`stick/bsd/remote-access` as on the other BSDs: it adds the `backotter` user,
sets a one-time password for it and root, makes host keys and starts sshd
(`rcctl -f start`).  `sshd_config` begins with `stick/bsd/sshd_config.backotter`:
root can't log in over SSH.

## Limits (accepted)

- **No firmware.**  `fw_update` isn't run: the firmware OpenBSD fetches at
  install has licences that don't allow shipping it on the image.  Devices that
  need it don't work: most Wi-Fi (iwm, iwx, ...), a few Ethernet chips (e.g.
  bnx), and amdgpu/radeondrm (the console stays a plain framebuffer).  Common
  wired Ethernet (em, igc, ix, re, ...) needs none.
- **No errata.**  The sets are the release as shipped; syspatch's fixes
  aren't in the image (and syspatch can't run on a read-only root).  Rebuild
  from a newer release.
- No OpenZFS on OpenBSD; the marker says so.
- No Secure Boot.
- Network cards plugged in after boot (USB Ethernet) aren't configured:
  `ifconfig IF inet autoconf` by hand.
- The memory file systems live in RAM without swap: a machine with little
  memory fills up if much is written to `/tmp`.
