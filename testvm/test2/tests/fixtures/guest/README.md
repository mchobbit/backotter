GuestTest2's fixtures.  The judge's tests (test_guest_judge.py) take mount lists from the corpus
(holt/tests/corpus/*/src.json) and build hook output in check-image.sh's and check-after.sh's format,
rule by rule; the formats are confirmed on real guests by host test T3.8.  Nothing here comes from a guest's
image: no raw dump, serial, LAN address or key.

The log sweep's tests (RealLogLines) use package-manager lines taken from the hosts' real
vms/G/update.log files, read in place on 2026-11-10 and copied by hand with generic package names only.

hook-*.out here are complete hook outputs for one PASSing debian12 run (test_guest.py).
