"""Alpine (BackShot2Alpine's sources: alpine324, -uefi, -lvm, -luks): the
first family.  Its cases are byte-identical to the ones the generator drew
before the family interface (tests/golden/alpine.json; GENERATOR "2").

- Content drift: recipe alpine-small-2 on every source (the deep tree kept
  under PATH_MAX until rustic-bko keeps paths past it, fail-001);
  alpine-small-1, past it, is the corpus case's.
- Quirk mkinitfs (Alpine's own rule, backshot2.md "BackShot2Alpine"):
  mkinitfs includes a feature only if /etc/mkinitfs/mkinitfs.conf lists it,
  and load regenerates the initramfs with the source's list (commands.rs:
  `mkinitfs KVER` for each /lib/modules), so a case may change a geometry
  but never what needs a module the list lacks.  Refuses any drift that
  adds or drops a filesystem type, stack layer or md level; no phase 1
  edit does, so it holds by construction.  The dump records the generator
  (boot.initramfs.generator = mkinitfs) but not mkinitfs.conf's features:
  hence the whole set kept, not the list checked.
- Known fail-003 (testvm/backshot2/cases/fail-003-removable-force-extra):
  load's UEFI grub-install --removable adds --force-extra-removable,
  Debian's own option, which Alpine's GRUB 2.14 refuses: no --removable.
  (Every upstream GRUB does the same: grub_upstream.py steers the other
  families' UEFI GRUB sources around it.)
- Rule 2: the generic rows; versions from boot.packages (apk names, the
  generic PACKAGES).  The dump records e2fsprogs and xfsprogs, not
  cryptsetup, lvm2 or util-linux: those take their oldest rows (the -luks
  source's LUKS2 sector size is 512 only).
"""

from .base import Family, layers_quirk, os_id

RECIPES = {
    # A few hundred entries, ~40 MiB of data: small enough for every Alpine source.
    "alpine-small-1": {"files": 60, "large": 24 << 20, "deep": 1, "deep_bytes": 4300, "links": 8, "symlinks": 12, "names": 10, "nodes": 4},
    # The same, with the deep tree kept under PATH_MAX (3,800 bytes below
    # /srv/bs2, so under 4,096 with any mount point ht1.sh uses): rustic-bko's
    # backup skips what's past it with a warning and exits 0 (BackShot2
    # finding fail-001, testvm/backshot2/cases/), which would otherwise fail
    # every case with content.  The generator draws this one until that's fixed.
    "alpine-small-2": {"files": 60, "large": 24 << 20, "deep": 1, "deep_bytes": 3800, "links": 8, "symlinks": 12, "names": 10, "nodes": 4},
}

# The content recipe drawn: alpine-small-2 until rustic-bko keeps paths past
# PATH_MAX (fail-001).
RECIPE = "alpine-small-2"


def no_removable(dump, drifted, edits, opts):
    return [o for o in opts if o != "--removable"]


FAMILY = Family(
    name="alpine",
    priority=10,
    matches=lambda source, dump: os_id(dump) == "alpine",
    quirks=[layers_quirk("mkinitfs", "mkinitfs includes only the features mkinitfs.conf lists, and load "
                                     "regenerates the initramfs with the source's list")],
    recipes=RECIPES,
    content=lambda dump, fstype: RECIPE,
    known={"fail-003": "--removable runs grub-install --force-extra-removable, Debian's own option, which Alpine's GRUB refuses"},
    steer=no_removable,
    tier0="chroot",
)
