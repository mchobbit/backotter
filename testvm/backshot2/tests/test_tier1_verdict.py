"""Unit tests for tier 1's verdict (bs2lib/tier1.py): a RESULT: PASS is a PASS
only when every phase the HoltTest1 header names began and ended, and
ht1-bs2.sh's own numbers (bs2_rs_sectors, BS2_TIER) never reach bash
arithmetic malformed.  The logs are fabricated after bs2a-20261114-0655's
false PASS (cage, alpine324-7e0f4c1ce06601d9), a real PASS and a FAIL:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BS2 = os.path.dirname(HERE)
sys.path.insert(0, BS2)

from bs2lib import tier1 as T1  # noqa: E402

HEAD = ("HoltTest1 alpine324, run bs2a-20261114-0655-t1, 2026-11-14T06:28:37Z, phases: source backup rebuild verify\n"
        "binaries: bko-holt dbdb5ae8c6aa (/bkoscratch/backshot2/bin/bko-holt)\n"
        "note: set for debugging or tests: HT_QUICK=1\n")
SRC_BACKUP = ("== source: alpine324\n"
              "PASS the prepared source p-20261114-062249-56897: its boot, dump (...)\n"
              "PASS the case's 4 edits applied to the fresh dump (targets 17179869184, options none)\n"
              "== backup (alpine324-7e0f4c1ce06601d9: 1 snapshot(s), content 1)\n"
              "PASS content planted: 165 entries\n"
              "PASS rustic-bko backup 1 of 1 (184M)\n")
REBUILD = ("== rebuild\n"
           "note: rescuer: resumed from its saved state 0123456789abcdef (saved 2026-11-14T06:20:00Z); clock ..., RNG reseeded; vdb:33554432 vdc:33554432 hot-plugged\n"
           "PASS rescuer up, share mounted\n"
           "PASS load close (12 s)\n")
VERIFY = ("== verify\n"
          "PASS fsmeta: the files agree but for the boot stage's (0 differences)\n"
          "PASS the restored disk boots by itself, SSH with the guest's key\n"
          "PASS the dumps agree (0 fields)\n")
SUMMARY = "RAM disk: peak 0.3 GiB, reserved 2 GiB; memory: peak 0.0 GiB anon\n"
TSV_HEAD = "phase\tstart_utc\tseconds\tpeak_disk_kib\tpeak_anon_kib\treserved_gib\tmode\tfails\n"


def tsv(*phases):
    return TSV_HEAD + "".join(f"{p}\t2026-11-14T06:28:37Z\t5\t31148\t18092\t2\tram\t0\n" for p in phases)


# The false PASS: an expansion error in a rebuild hook abandoned ht1.sh's phase
# loop; its summary and RESULT ran, with neither rebuild's end nor verify.
FALSE_REPORT = HEAD + SRC_BACKUP + "== rebuild\n" + SUMMARY + "RESULT: PASS\n"
FALSE_TSV = tsv("source", "backup")
ERR = ("/home/m/backotter-work/bs2-wt/testvm/backshot2/ht1-bs2.sh: line 499: / 512: syntax error: "
       'operand expected (error token is "/ 512")')
FALSE_LOG = (HEAD + SRC_BACKUP + "== rebuild\n"
             "qemu-img: Could not open '/mnt/bko-ram/backshot2/x/dst-0.qcow2': Failed to get shared \"write\" lock\n"
             "json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n"
             + ERR + "\n" + SUMMARY + "RESULT: PASS\n"
             "ht1.sh: moving /mnt/bko-ram/backshot2/x to /bkoscratch/backshot2/x (0.0 GiB)\n"
             "ht1.sh: the run is in /bkoscratch/backshot2/x\n")

PASS_REPORT = HEAD + SRC_BACKUP + REBUILD + VERIFY + SUMMARY + "RESULT: PASS\n"
PASS_TSV = tsv("source", "backup", "rebuild", "verify")

FAIL_REPORT = (HEAD + SRC_BACKUP + REBUILD.replace("PASS load close (12 s)", "FAIL load close: exit 1 (logs/rebuild-close.err)")
               + "stopped after rebuild\n" + SUMMARY + "RESULT: FAIL (1)\n")
FAIL_TSV = tsv("source", "backup", "rebuild")


class TestVerdict(unittest.TestCase):
    def test_the_false_pass_is_harness(self):
        v, check, why = T1.verdict_of(FALSE_REPORT, FALSE_TSV, FALSE_LOG)
        self.assertEqual(v, "HARNESS")
        self.assertTrue(check.startswith("phase rebuild never ended; phase verify never ran (log ends: "), check)
        self.assertIn("syntax error: operand expected", check)
        self.assertEqual(why, [check])

    def test_the_false_pass_without_its_log_ends_at_the_report(self):
        v, check, _ = T1.verdict_of(FALSE_REPORT, FALSE_TSV)
        self.assertEqual(v, "HARNESS")
        self.assertEqual(check, "phase rebuild never ended; phase verify never ran (log ends: == rebuild)")

    def test_a_real_pass(self):
        self.assertEqual(T1.verdict_of(PASS_REPORT, PASS_TSV, PASS_REPORT), ("PASS", None, []))

    def test_a_fail_stays_a_fail(self):
        v, check, why = T1.verdict_of(FAIL_REPORT, FAIL_TSV, FAIL_REPORT)
        self.assertEqual(v, "FAIL")
        self.assertEqual(check, "load close")
        self.assertEqual(why, ["FAIL load close: exit 1 (logs/rebuild-close.err)"])

    def test_a_harness_fail_stays_harness(self):
        rep = FAIL_REPORT.replace("FAIL load close: exit 1 (logs/rebuild-close.err)", "FAIL rescuer boots")
        self.assertEqual(T1.verdict_of(rep, FAIL_TSV)[:2], ("HARNESS", "rescuer boots"))

    def test_no_phases_tsv_is_no_pass(self):
        v, check, _ = T1.verdict_of(PASS_REPORT, None, PASS_REPORT)
        self.assertEqual(v, "HARNESS")
        self.assertTrue(check.startswith("phase source never ended; phase backup never ended; "
                                         "phase rebuild never ended; phase verify never ended"), check)

    def test_a_phase_with_no_header_line_never_ran(self):
        rep = PASS_REPORT.replace("== verify\n", "")
        v, check, _ = T1.verdict_of(rep, PASS_TSV, rep)
        self.assertEqual((v, check.split(" (")[0]), ("HARNESS", "phase verify never ran"))

    def test_no_phases_in_the_header(self):
        rep = PASS_REPORT.replace(", phases: source backup rebuild verify", "")
        v, check, _ = T1.verdict_of(rep, PASS_TSV, rep)
        self.assertEqual(v, "HARNESS")
        self.assertTrue(check.startswith("no phases in the HoltTest1 header"), check)

    def test_the_preparations_two_phases(self):
        rep = HEAD.replace(" rebuild verify", "") + SRC_BACKUP + SUMMARY + "RESULT: PASS\n"
        self.assertIsNone(T1.phase_gaps(rep, tsv("source", "backup"), rep))
        self.assertEqual(T1.phase_gaps(rep, tsv("source"), rep), "phase backup never ended (log ends: PASS rustic-bko backup 1 of 1 (184M))")

    def test_no_result_line(self):
        self.assertEqual(T1.verdict_of(HEAD + SRC_BACKUP, tsv("source"))[:2], ("HARNESS", "no RESULT line"))

    def test_log_end(self):
        self.assertEqual(T1.log_end(FALSE_LOG), ERR)
        self.assertEqual(T1.log_end("a\nb\n\n"), "b")
        self.assertEqual(T1.log_end(""), "nothing")
        self.assertEqual(T1.log_end("x\ndisk (/bkoscratch, HT_RAM=0): peak 1 GiB\nRESULT: PASS\n"), "x")


class TestShellNumbers(unittest.TestCase):
    """ht1-bs2.sh sourced as ht1.sh does, with a stand-in qemu-img."""

    def run_sh(self, script, qemu_img, tier="1"):
        with tempfile.TemporaryDirectory() as d:
            q = os.path.join(d, "qemu-img")
            with open(q, "w") as f:
                f.write("#!/bin/sh\n" + qemu_img + "\n")
            os.chmod(q, os.stat(q).st_mode | stat.S_IXUSR)
            env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}", BS2_CASE="c", BS2_NAME="n", BS2_TIER=tier,
                       BS2_HOST=os.path.join(BS2, "bs2-host"))
            return subprocess.run(["bash", "-c", 'set -uo pipefail; . "$0"; ' + script, os.path.join(BS2, "ht1-bs2.sh")],
                                  env=env, capture_output=True, text=True)

    def test_sectors(self):
        r = self.run_sh('bs2_rs_sectors x', """[ "$1 $2" = "info -U" ] || exit 9; echo '{"virtual-size": 17179869184}'""")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "33554432"))

    def test_sectors_of_a_locked_image_is_exit_1_not_an_abort(self):
        # (As qemu-img info without -U on a disk a running QEMU has hot-plugged.)
        r = self.run_sh('bs2_rs_sectors x || echo fell-back; echo next',
                        """echo "qemu-img: Could not open 'x': Failed to get shared \\"write\\" lock" >&2; exit 1""")
        self.assertEqual(r.stdout.split(), ["fell-back", "next"])
        self.assertNotIn("syntax error", r.stderr)

    def test_sectors_unaligned_or_garbage(self):
        for out in ("""echo '{"virtual-size": 1000}'""", "echo '{}'", "echo 'nonsense'"):
            r = self.run_sh('bs2_rs_sectors x || echo no; echo next', out)
            self.assertEqual(r.stdout.split(), ["no", "next"], out)
            self.assertNotIn("syntax error", r.stderr)

    def test_tier_is_checked(self):
        r = self.run_sh("echo sourced", "exit 1", tier="1 / 2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("BS2_TIER is 1 or 2", r.stderr)


if __name__ == "__main__":
    unittest.main()
