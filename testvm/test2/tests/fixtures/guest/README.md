GuestTest2's fixtures.  The judge's tests (test_guest_judge.py) take mount lists from the corpus
(holt/tests/corpus/*/src.json) and build hook output in check-image.sh's and check-after.sh's format,
rule by rule; the formats are confirmed on real guests by host test T3.8.  Nothing here comes from a guest's
image: no raw dump, serial, LAN address or key.

The log sweep's tests (RealLogLines) use package-manager lines taken from the hosts' real
vms/G/update.log files, read in place on 2026-11-10 and copied by hand with generic package names only.

hook-*.out here are complete hook outputs for one PASSing debian12 run (test_guest.py).
solus49-before.out is the before hook (RULES="S-eopkg L-cbm K-cbm B-lin M-lin") run on solus49
on Chopin, 2026-11-14, from a --snapshot boot (test_guest_judge.py's Solus class).
slackware15-before.out and slackware15-uefi-before.out are the before hook (RULES="S-slackpkg
L-lilo K-slackware B-lin M-lin", and L-elilo for -uefi) run on each guest on Chopin, 2026-11-15,
from a --snapshot boot (test_guest_judge.py's Slackware class); they hold no UUID, MAC or address.
guix-reconfigure-unbuilt.out and guix-reconfigure-substituted.out are guix15-bios's `guix system
reconfigure` build lists from its update.log on Chopin (t2v-20261121T1624-bd34, head's guix and
linux-libre with no substitute yet; t2v-20261120T0601-2cfb, all substituted), read in place on
2026-11-24, the swap's UUID in a shepherd service's name replaced by a made-up one
(test_vm_update.py's Guix hold tests).
