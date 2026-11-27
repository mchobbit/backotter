"""stick/boot-test.py's Console on a serial port that stalls (the netbsd-uefi flake, 2026-11-27).

NetBSD's com(4) on QEMU's 16550 now and then stops taking the port's interrupt: what the guest
prints, or what was typed to it, waits until another byte reaches the port.  A fake guest here
holds its answer until it gets one, as the stalled console did.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


def load_boot_test():
    spec = importlib.util.spec_from_file_location("boot_test_console", os.path.join(WT, "stick", "boot-test.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A guest whose console prints BEFORE, then nothing until a byte in WAKE arrives, then AFTER.
GUEST = r"""
import os, sys
before, wake, after = (a.encode().decode("unicode_escape").encode("latin-1") for a in sys.argv[1:4])
os.write(1, before)
while True:
    b = os.read(0, 1)
    if not b:
        sys.exit(0)
    if b in wake:
        os.write(1, after)
"""


class StalledConsole(unittest.TestCase):
    def setUp(self):
        self.bt = load_boot_test()
        self.bt.QUIET_S = 0.5
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def guest(self, before, wake, after):
        p = subprocess.Popen([sys.executable, "-c", GUEST, before, wake, after],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdin.close(), p.stdout.close()))
        log = open(os.path.join(self.tmp.name, "log"), "wb")
        self.addCleanup(log.close)
        return p, log

    def test_a_check_whose_answer_waits_for_another_byte(self):
        # The check line typed, its answer stuck in the guest until ^T (NetBSD's status character).
        p, log = self.guest("check x 'true'\\r\\n", "\\x14", "load: 0.00  cmd: sh 1139 [ttyin]\\r\\nBKO-CHECK x ok \\r\\n")
        con = self.bt.Console(p, log, nudge=self.bt.NUDGE.get("netbsd"))
        self.assertEqual(con.expect(self.bt.re.compile(rb"BKO-CHECK x .*\n"), timeout=5), 0)
        self.assertEqual(len(con.notes), 1)
        self.assertIn("quiet", con.notes[0])

    def test_a_prompt_with_a_syslog_line_after_it(self):
        # "backotter# " and then login's syslog line from ttyE1: the prompt is no longer the last
        # thing printed, and only a fresh one (Enter at an idle prompt) matches.
        p, log = self.guest("backotter# Nov 27 17:23:40 backotter login: root on tty ttyE1\\r\\r\\n", "\\r",
                            "\\r\\nbackotter# ")
        con = self.bt.Console(p, log, nudge=self.bt.NUDGE.get("netbsd"))
        self.assertEqual(con.expect(self.bt.PROMPT, timeout=5, nudge="\r"), 0)
        self.assertEqual(len(con.notes), 1)

    def test_a_console_that_stays_silent_is_named(self):
        p, log = self.guest("check x 'true'\\r\\n", "", "")
        con = self.bt.Console(p, log, nudge=self.bt.NUDGE.get("netbsd"))
        with self.assertRaises(TimeoutError) as cm:
            con.expect(self.bt.re.compile(rb"BKO-CHECK x .*\n"), timeout=2)
        self.assertIn("stayed silent through", str(cm.exception))
        self.assertEqual(con.notes, [str(cm.exception)])

    def test_the_boot_wait_is_not_nudged(self):
        # Before BackOtter's menu, a key would stop the boot loader's countdown.
        p, log = self.guest(">> NetBSD/x86 EFI Boot\r\n", "\x14\r", "Choose [1-4]: ")
        con = self.bt.Console(p, log, nudge=self.bt.NUDGE.get("netbsd"))
        with self.assertRaises(TimeoutError):
            con.expect(self.bt.MENU, timeout=1.5, nudge="")
        self.assertEqual(con.notes, [])

    def test_other_platforms_are_not_nudged(self):
        self.assertEqual(set(self.bt.NUDGE), {"netbsd"})
        p, log = self.guest("check x 'true'\\r\\n", "\\x14\\r", "BKO-CHECK x ok \\r\\n")
        con = self.bt.Console(p, log, nudge=self.bt.NUDGE.get("linux"))
        with self.assertRaises(TimeoutError):
            con.expect(self.bt.re.compile(rb"BKO-CHECK x .*\n"), timeout=1.5)
        self.assertEqual(con.notes, [])


if __name__ == "__main__":
    unittest.main()
