"""U6.2: t2lib/leakscan.py: its patterns, blob decoding, host and raw-dump
strings, findings that never show what they found, and the clean corpus."""

import base64
import binascii
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from t2lib import leakscan  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CORPUS = os.path.join(REPO, "holt", "tests", "corpus")

# Planted values: synthetic, shaped like the real thing.
MAC = "52:54:00:ab:cd:ef"
SERIAL = "PLANTSER0042XYZ"
UUID = "1f2e3d4c-5b6a-4978-8a9b-0c1d2e3f4a5b"


def u16(s):
    return s.encode("utf-16le")


def dump_with(**over):
    """A small redacted-looking dump; OVER replaces its top-level parts."""
    d = {
        "format": "bko-holt", "schema": 1,
        "host": {"hostname": "host-1", "machine_id": "bd000000000040008000000000000001"},
        "storage": {"disks": [{"name": "vda", "serial": "serial1", "wwid": None,
                               "partition_table": {"type": "gpt", "disk_guid": "BC000000-0000-4000-8000-000000000001"},
                               "partitions": [{"name": "vda1", "uuid": "bc000000-0000-4000-8000-000000000002",
                                               "type": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"}]}]},
        "boot": {"efi": {"entries": [{"description": "UEFI PXEv4 (MAC:bc0000000001)",
                                      "load_option_hex": binascii.hexlify(b"\x01\x00\x00\x00" + u16("UEFI PXEv4 (MAC:bc0000000001)") + b"\x00\x00").decode()}]}},
    }
    d.update(over)
    return d


def scan_obj(obj, **kw):
    with tempfile.TemporaryDirectory() as d:
        g = os.path.join(d, "guest")
        os.makedirs(g)
        p = os.path.join(g, "src.json")
        with open(p, "w") as f:
            json.dump(obj, f)
        return leakscan.scan_paths([p], **kw)


def classes(found):
    return sorted(f.cls for f in found)


class Patterns(unittest.TestCase):
    def test_macs(self):
        for t in (MAC, MAC.replace(":", "-"), "UEFI PXEv4 (MAC:525400abcdef)", "MAC(525400ABCDEF,0x1)"):
            self.assertEqual([c for c, _ in leakscan.pattern_findings(t, False)], ["mac"], t)
        # redact.rs's pseudonyms in every separator form (bc, six or more
        # zeros, a counter of up to 4 hex digits), zeros, broadcast.
        for t in ("bc:00:00:00:00:01", "BC-00-00-00-00-1F", "(MAC:bc0000000001)", "MAC(BC00000000A3)", "bc:00:00:00:ff:ff", "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
            self.assertEqual(leakscan.pattern_findings(t, False), [], t)
        # Real MACs that start like a pseudonym, and MACs joined to a name by a dash.
        for t in ("bc:00:00:12:34:56", "BC-00-00-A1-B2-C3", "MAC:bc0000fedcba", "bc:00:00:01:00:00", "eth0-52:54:00:12:34:56", "x-52-54-00-12-34-56", "if=52:54:00:12:34:56"):
            self.assertEqual([c for c, _ in leakscan.pattern_findings(t, False)], ["mac"], t)
        # Not MACs: md UUIDs, times, longer runs (from either end), hex glued on.
        for t in ("aaaa1111:bbbb2222:cccc3333:dddd4444", "12:34:56", "aa:bb:cc:dd:ee:ff:00", "a0:52:54:00:12:34:56", "2026-11-08", "2026-11-08-12-34-56", "f52:54:00:12:34:56"):
            self.assertEqual(leakscan.pattern_findings(t, False), [], t)

    def test_build_signatures(self):
        self.assertEqual([c for c, _ in leakscan.pattern_findings("v6.4 #0: root@oldbox:/usr/obj/sys", False)], ["host"])
        for t in ("root@host1:/usr/obj/sys", "user@host", "m@chopin.local", "a@b: not a path"):
            self.assertEqual([c for c, _ in leakscan.pattern_findings(t, True) if c == "host"], [], t)

    def test_addresses(self):
        found = leakscan.pattern_findings("10.1.2.3 172.20.0.9 192.168.4.5 169.254.1.1 8.8.8.8 172.32.0.1 fe80::1 fd12:3456::7 2001:db8::1", False)
        self.assertEqual([s for c, s in found if c == "ip"], ["IPv4 private", "IPv4 private", "IPv4 private", "IPv4 link-local", "IPv6 link-local", "IPv6 unique-local"])
        # Versions and numbers with more parts aren't addresses.
        self.assertEqual(leakscan.pattern_findings("v1.10.0.0.1 6.12.115+deb13.1 10.0.13", False), [])

    def test_email_keys_hosts(self):
        self.assertEqual(classes([leakscan.Finding("f", "w", c, s) for c, s in leakscan.pattern_findings("builds@mkrepro.example.org", False)]), ["email"])
        self.assertEqual(leakscan.pattern_findings("builder-at-redacted", False), [])
        for t in ("-----BEGIN OPENSSH PRIVATE KEY-----", "ssh-ed25519 AAAAC3NzaC1", "ssh-rsa AAAAB3Nza"):
            self.assertEqual([c for c, _ in leakscan.pattern_findings(t, False)], ["key"], t)
        self.assertEqual([c for c, _ in leakscan.pattern_findings("the CHOPIN host and cinqcent.local", False)], ["host", "host"])
        self.assertEqual(leakscan.pattern_findings("the CHOPIN host", True), [])


class Scrub(unittest.TestCase):
    def test_scrub_text(self):
        t, n = leakscan.scrub_text("mount failed on 192.168.1.9 for root@build.example.org (MAC:525400abcdef, MAC:bc0000000001)")
        self.assertEqual(n, 3)
        self.assertEqual(t, "mount failed on <ip> for <email> (<mac>, MAC:bc0000000001)")
        needles = [leakscan.needle_for("serial", SERIAL)]
        t, n = leakscan.scrub_text("disk %s and %s again, on chopin" % (SERIAL, SERIAL.lower()), needles)
        self.assertEqual((t, n), ("disk <host string> and <host string> again, on <host>", 3))
        self.assertEqual(leakscan.scrub_text("nothing here"), ("nothing here", 0))


class Files(unittest.TestCase):
    def test_redacted_uefi_dump_is_clean(self):
        self.assertEqual(scan_obj(dump_with()), [])

    def test_utf16_mac_in_a_blob(self):
        hexed = binascii.hexlify(b"\x01\x00\x00\x00\x00" + u16("UEFI PXEv4 (MAC:525400abcdef)") + b"\x00\x00").decode()
        d = dump_with(boot={"efi": {"entries": [{"description": "UEFI PXEv4", "load_option_hex": hexed}]}})
        found = scan_obj(d)
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0].cls, "mac")
        self.assertIn("load_option_hex", found[0].where)
        self.assertIn("utf-16le@1", found[0].shape)
        self.assertNotIn("525400abcdef", str(found[0]).lower())

    def test_base64_blob_and_keys(self):
        b64 = base64.b64encode(b"\x00\x01 root@build.example.net \x02").decode()
        found = scan_obj(dump_with(storage={"disks": [{"name": "vda", "boot_code": {"code_b64": b64}}], "x 10.9.8.7": 1}))
        self.assertEqual(classes(found), ["email", "ip"], found)
        self.assertTrue(any(f.where == "$.storage[#1] (a key)" for f in found), found)
        self.assertTrue(all("example.net" not in str(f) and "10.9.8.7" not in str(f) for f in found))

    def test_symlinks_are_their_targets_names(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "real"))
            with open(os.path.join(d, "real", "f"), "w") as f:
                f.write("clean\n")
            os.symlink("../nosuch", os.path.join(d, "dangling"))
            os.symlink("real", os.path.join(d, "dirlink"))
            os.symlink("/srv/192.168.1.7/x", os.path.join(d, "leaky"))
            errors = []
            found = leakscan.scan_paths([d], errors=errors)
            self.assertEqual(errors, [])
            self.assertEqual([(os.path.basename(f.path), f.cls) for f in found], [("leaky (a symlink)", "ip")], found)
            self.assertEqual(sorted(os.path.basename(f) for f, _ in leakscan.files_under([d])), ["dangling", "dirlink", "f", "leaky"])
            # A symlink named on the command line is followed.
            self.assertEqual(leakscan.scan_paths([os.path.join(d, "dirlink")]), [])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads everything")
    def test_an_unreadable_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "secret")
            with open(p, "w") as f:
                f.write("x\n")
            os.chmod(p, 0)
            try:
                errors = []
                self.assertEqual(leakscan.scan_paths([d], errors=errors), [])
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("secret: can't be read", errors[0])
                with self.assertRaises(leakscan.ScanError):
                    leakscan.scan_paths([d])
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(leakscan.main(["scan", d]), 2)
            finally:
                os.chmod(p, 0o600)

    def test_plan_text_and_allowed_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "plan.txt"), "w") as f:
                f.write("# Source: host-1\nmount /dev/sdq1 /mnt/bko-target\n# made on chopin\n")
            with open(os.path.join(d, "index.tsv"), "w") as f:
                f.write("guest\thost\ndebian13\tchopin\n")
            with open(os.path.join(d, "README.md"), "w") as f:
                f.write("Collected on chopin and cinqcent.\n")
            found = leakscan.scan_paths([d])
            self.assertEqual([(os.path.basename(f.path), f.where, f.cls) for f in found], [("plan.txt", "line 3", "host")])
            self.assertEqual(leakscan.scan_paths([d], allow_hosts=True), [])


class Strings(unittest.TestCase):
    def strings_file(self, d, pairs):
        p = os.path.join(d, "strings")
        with open(p, "w") as f:
            for k, v in pairs:
                f.write(json.dumps({"k": k, "v": v}) + "\n")
        return p

    def test_bad_strings_lines_are_errors(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ('{"k": "serial", "v": "PLANTSER00', '42', '{"k": "serial"}', '["serial", "x"]'):
                p = self.strings_file(d, [("serial", SERIAL)])
                with open(p, "a") as f:
                    f.write(bad + "\n")
                with self.assertRaises(leakscan.ScanError) as e:
                    leakscan.read_strings(p)
                self.assertIn("line 2", str(e.exception))
                self.assertNotIn("PLANTSER", str(e.exception))
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(leakscan.main(["scan", "--strings", p, p]), 2)

    def test_git_email_without_an_address_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            nosuch = os.path.join(d, "nosuch")
            with self.assertRaises(leakscan.ScanError):
                leakscan.git_email_needles(nosuch)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(leakscan.main(["scan", "--git-email", nosuch, d]), 2)

    def test_planted_serial_and_mac(self):
        with tempfile.TemporaryDirectory() as d:
            needles = leakscan.read_strings(self.strings_file(d, [("serial", SERIAL), ("mac", MAC), ("passphrase", "short"), ("ip", "127.0.0.1"), ("ip", "fe80::5")]))
        self.assertEqual([n.kind for n in needles], ["serial", "mac"], "short values, loopback and link-local are left out")
        dirty = dump_with(storage={"disks": [{"name": "vda", "serial": SERIAL.lower(),
                                              "boot_code": {"code_b64": base64.b64encode(b"\x00" + binascii.unhexlify(MAC.replace(":", "")) + b"\x00").decode()}}]})
        found = scan_obj(dirty, needles=needles)
        self.assertEqual(sorted((f.cls, f.shape) for f in found), [("host string", "mac, in the blob's bytes"), ("host string", "serial")], found)
        for f in found:
            self.assertNotIn(SERIAL.lower(), str(f).lower())
            self.assertNotIn("abcdef", str(f).lower())
        self.assertEqual(scan_obj(dump_with(), needles=needles), [])

    def test_raw_dump_identifiers(self):
        raw = dump_with(host={"hostname": "alice-pc", "machine_id": "aaaabbbbccccddddeeeeffff00001111"},
                        boot={"efi": {"entries": [{"description": "UEFI PXEv4 (MAC:525400abcdef)"}]}},
                        storage={"disks": [{"name": "vda", "serial": SERIAL, "wwid": "naa.5002538e40a1b2c3",
                                            "partition_table": {"type": "dos", "disk_signature": "7a6b5c4d"},
                                            "partitions": [{"name": "vda1", "uuid": "7a6b5c4d-01", "content": {"uuid": UUID}},
                                                           {"name": "vda2", "uuid": "00000000-02", "type": "0x83"}]}]})
        needles = leakscan.raw_needles(raw)
        kinds = sorted(n.kind for n in needles)
        self.assertIn("raw dump uuid", kinds)
        self.assertIn("raw dump serial", kinds)
        self.assertIn("raw dump id", kinds)   # the disk signature, the machine-id
        self.assertIn("raw dump MAC", kinds)
        self.assertFalse(any(t in ("00000000", "00000000-02") for n in needles for t in n.texts), "zero ids identify nothing")
        self.assertEqual(sum(t == "7a6b5c4d" for n in needles for t in n.texts), 1, "an MBR PARTUUID's signature, once")
        with tempfile.TemporaryDirectory() as d:
            rawdir = os.path.join(d, "raw", "guest")
            os.makedirs(rawdir)
            with open(os.path.join(rawdir, "src.json"), "w") as f:
                json.dump(raw, f)
            g = os.path.join(d, "corpus", "guest")
            os.makedirs(g)
            p = os.path.join(g, "src.json")
            with open(p, "w") as f:
                json.dump(dump_with(), f)
            self.assertEqual(leakscan.scan_paths([p], raw_dir=os.path.join(d, "raw")), [])
            # The raw UUID left in, as text and as GPT's mixed-endian bytes.
            mixed = leakscan._uuid_bytes(UUID)[1]
            with open(p, "w") as f:
                json.dump(dump_with(x=UUID.upper(), y={"image_b64": base64.b64encode(b"\x00" + mixed).decode()}), f)
            found = leakscan.scan_paths([p], raw_dir=os.path.join(d, "raw"))
            self.assertEqual(sorted(f.shape for f in found), ["raw dump uuid", "raw dump uuid, in the blob's bytes"], found)
            self.assertTrue(all(UUID[:8] not in str(f).lower() for f in found))
            # Every scanned src.json needs its raw twin, which must parse.
            stats, errors = {}, []
            g2 = os.path.join(d, "corpus", "other")
            os.makedirs(g2)
            with open(os.path.join(g2, "src.json"), "w") as f:
                json.dump(dump_with(), f)
            leakscan.scan_paths([os.path.join(d, "corpus")], raw_dir=os.path.join(d, "raw"), errors=errors, stats=stats)
            self.assertEqual(stats, {"src_json": 2, "raw_twins": 1})
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("other/src.json: no raw twin", errors[0])
            os.makedirs(os.path.join(d, "raw", "other"))
            with open(os.path.join(d, "raw", "other", "src.json"), "w") as f:
                f.write("{torn")
            errors = []
            leakscan.scan_paths([g2], raw_dir=os.path.join(d, "raw"), errors=errors)
            self.assertIn("isn't JSON", errors[0])
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(leakscan.main(["scan", "--raw", os.path.join(d, "raw"), g2]), 2)
                self.assertEqual(leakscan.main(["scan", "--raw", os.path.join(d, "nosuch"), g2]), 2)
                self.assertEqual(leakscan.main(["scan", "--raw", os.path.join(d, "raw"), g]), 1)

    def test_host_strings_reads_passphrases(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "g1"))
            with open(os.path.join(d, "g1", "luks-passphrase"), "w") as f:
                f.write("correct horse battery\n")
            got = leakscan.host_strings(d)
        self.assertIn(("passphrase", "correct horse battery"), got)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plan.txt")
            with open(p, "w") as f:
                f.write("mount /dev/sdq1 /mnt/bko-target\n")
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(leakscan.main(["scan", p]), 0)
                with open(p, "a") as f:
                    f.write("# from 192.168.7.7\n")
                self.assertEqual(leakscan.main(["scan", p]), 1)
                self.assertEqual(leakscan.main(["scan"]), 2)
                self.assertEqual(leakscan.main(["scan", os.path.join(d, "nosuch")]), 2)
                self.assertEqual(leakscan.main(["frob"]), 2)
                self.assertEqual(leakscan.main(["strings", "x"]), 2)


@unittest.skipUnless(os.path.isfile(os.path.join(CORPUS, "index.tsv")), "no corpus in holt/tests/corpus")
class Corpus(unittest.TestCase):
    """The committed corpus: (a) finds nothing in it (t2 self-test's run)."""

    def test_clean(self):
        found = leakscan.scan_paths([CORPUS])
        self.assertEqual([str(f) for f in found], [])

    def test_a_redacted_uefi_dump_has_mac_pseudonyms_only(self):
        uefi = [g for g in ("aerynos", "ubuntu26-uefi", "fedora44-uefi", "pop24") if os.path.isfile(os.path.join(CORPUS, g, "src.json"))]
        self.assertTrue(uefi)
        for g in uefi:
            with open(os.path.join(CORPUS, g, "src.json")) as f:
                text = f.read()
            self.assertEqual(leakscan.scan_paths([os.path.join(CORPUS, g, "src.json")]), [], g)
            self.assertIn("load_option_hex", text, g)


if __name__ == "__main__":
    unittest.main()
