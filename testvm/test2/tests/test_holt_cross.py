"""img-speed.md I10, I11: holt-cross.sh's refusals (a check build never lands in CODE/PLAT/, §8.2a)
and holt_e2.py's E2 judges (§8.2), on fixture ELF files and documents.  No host needed; the
shell tests need bash 4 with realpath and timeout (the test hosts')."""

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
H1D = os.path.join(WT, "testvm", "holttest1d")
sys.path.insert(0, H1D)

import holt_e2  # noqa: E402


def modern_bash():   # (test_holt's)
    for b in ("bash", "/usr/local/bin/bash", "/opt/homebrew/bin/bash"):
        try:
            out = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip().isdigit() and int(out.stdout.strip()) >= 4:
            if subprocess.run([b, "-c", "type timeout realpath"], capture_output=True).returncode == 0:
                return b
    return None


def elf(path, interp="/libexec/ld-elf.so.1", needed=("libc.so.7",), rpath=None, flags_1=0x8000001,
        notes=(("FreeBSD", 1, struct.pack("<I", 1405000)),), wx=False, etype=3, osabi=None, code=b"",
        extra_ph=(), note_flags=2):
    """A small ELF64 file: PHDR-less, one LOAD over the whole file at vaddr 0, INTERP, DYNAMIC,
    GNU_RELRO, a note section, CODE in the file (the LOAD is R+E) and EXTRA_PH (types) as empty
    segments."""
    strtab = b"\0"
    offs = []
    for n in list(needed) + ([rpath] if rpath else []):
        offs.append(len(strtab))
        strtab += n.encode() + b"\0"
    notes_b = b""
    for owner, typ, desc in notes:
        o = owner.encode() + b"\0"
        notes_b += struct.pack("<III", len(o), len(desc), typ) + o.ljust((len(o) + 3) & ~3, b"\0") + \
            desc.ljust((len(desc) + 3) & ~3, b"\0")
    shstr = b"\0.shstrtab\0.note.tag\0"
    phnum = 4 + len(extra_ph)
    a_interp = 64 + 56 * phnum
    ib = interp.encode() + b"\0"
    a_str = a_interp + len(ib)
    a_dyn = (a_str + len(strtab) + 7) & ~7
    dyn = [(1, o) for o in offs[:len(needed)]] + ([(15, offs[-1])] if rpath else []) + \
        [(5, a_str), (0x6ffffffb, flags_1), (0, 0)]
    db = b"".join(struct.pack("<qQ", t, v) for t, v in dyn)
    a_note = a_dyn + len(db)
    a_shs = a_note + len(notes_b)
    a_code = a_shs + len(shstr)
    a_sh = (a_code + len(code) + 7) & ~7
    size = a_sh + 64 * 3
    lf = 7 if wx else 5
    ph = [(3, 4, a_interp, a_interp, a_interp, len(ib), len(ib), 1), (1, lf, 0, 0, 0, size, size, 0x1000),
          (2, 6, a_dyn, a_dyn, a_dyn, len(db), len(db), 8), (0x6474e552, 4, a_dyn, a_dyn, a_dyn, len(db), len(db), 1)]
    ph += [(t, 4, 0, 0, 0, 0, 0, 1) for t in extra_ph]
    if osabi is None:
        osabi = 9 if "ld-elf" in interp else 0
    hdr = b"\x7fELF" + bytes([2, 1, 1, osabi]) + b"\0" * 8 + \
        struct.pack("<HHIQQQIHHHHHH", etype, 62, 1, 0, 64, a_sh, 0, 64, 56, phnum, 64, 3, 1)
    b = bytearray(size)
    b[:64] = hdr
    for i, p in enumerate(ph):
        b[64 + 56 * i:64 + 56 * (i + 1)] = struct.pack("<IIQQQQQQ", *p)
    b[a_interp:a_interp + len(ib)] = ib
    b[a_str:a_str + len(strtab)] = strtab
    b[a_dyn:a_dyn + len(db)] = db
    b[a_note:a_note + len(notes_b)] = notes_b
    b[a_shs:a_shs + len(shstr)] = shstr
    b[a_code:a_code + len(code)] = code
    sh = [(0,) * 10, (1, 3, 0, 0, a_shs, len(shstr), 0, 0, 1, 0), (11, 7, note_flags, a_note, a_note, len(notes_b), 0, 0, 4, 0)]
    for i, s in enumerate(sh):
        b[a_sh + 64 * i:a_sh + 64 * (i + 1)] = struct.pack("<IIQQQQIIQQ", *s)
    with open(path, "wb") as f:
        f.write(bytes(b))
    return path


class E2Judges(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="t2-e2-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def p(self, name):
        return os.path.join(self.d, name)

    def run_cmd(self, argv):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = holt_e2.main(argv)
        return rc, out.getvalue()

    def test_shape_reads_what_step0_names(self):
        s = holt_e2.shape(elf(self.p("a"), needed=("libthr.so.3", "libc.so.7")))
        self.assertEqual((s["type"], s["interp"], s["needed"], s["relro"], s["bind_now"], s["wx"]),
                         ("PIE", "/libexec/ld-elf.so.1", ["libthr.so.3", "libc.so.7"], True, True, False))
        self.assertEqual(s["notes"], ["FreeBSD 1 " + struct.pack("<I", 1405000).hex()])
        self.assertEqual(s["segments"], ["DYNAMIC RW", "GNU_RELRO R", "INTERP R"])
        self.assertEqual(holt_e2.shape(elf(self.p("e"), etype=2, flags_1=1))["type"], "EXEC")

    def test_same_shape_passes(self):
        rc, out = self.run_cmd(["elf", "freebsd", elf(self.p("vm")), elf(self.p("x"))])
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.endswith("E2 step 0 freebsd: PASS\n"), out)

    def test_each_difference_fails(self):
        vm = elf(self.p("vm"))
        for name, kw in (("needed", {"needed": ("libc.so.7", "libm.so.5")}),
                         ("interp", {"interp": "/libexec/ld-elf32.so.1"}),
                         ("type", {"flags_1": 1}),
                         ("notes", {"notes": (("FreeBSD", 1, struct.pack("<I", 1500000)),)}),   # 15's ABI tag
                         ("wx", {"wx": True})):
            rc, out = self.run_cmd(["elf", "freebsd", vm, elf(self.p("x-" + name), **kw)])
            self.assertEqual(rc, 1, (name, out))
            self.assertIn(f"DIFF {name}", out)
            self.assertIn("E2 step 0 freebsd: FAIL", out)

    def test_explained_difference_passes_on_its_platform_only(self):
        vm = elf(self.p("vm"), interp="/usr/libexec/ld.elf_so", rpath="/usr/pkg/lib/libatomic",
                 notes=(("NetBSD", 1, struct.pack("<I", 1101000000)),))
        x = elf(self.p("x"), interp="/usr/libexec/ld.elf_so", notes=(("NetBSD", 1, struct.pack("<I", 1101000000)),))
        rc, out = self.run_cmd(["elf", "netbsd", vm, x])
        self.assertEqual(rc, 0, out)
        self.assertIn("explained rpath: VM ['/usr/pkg/lib/libatomic'] cross []", out)
        rc, out = self.run_cmd(["elf", "illumos", vm, x])
        self.assertEqual(rc, 1, out)

    # --- I11: OpenBSD's IBT handling (§8.2 step 0, D11) ----------------------------------------------
    OB = dict(interp="/usr/libexec/ld.so", needed=("libc.so.104.0",), osabi=12)
    IBT_NOTE = ("GNU", 5, struct.pack("<IIII", 0xc0000002, 4, 1, 0))
    PADS = holt_e2.ENDBR64 * 64 + b"\xc3" * 16

    def ob(self, name, pads=True, note=True, nobtcfi=False):
        return elf(self.p(name), notes=((("OpenBSD", 1, b"\0" * 4),) + ((self.IBT_NOTE,) if note else ())),
                   code=self.PADS if pads else b"\x90" * 300, extra_ph=(0x65a3dbe8,) if nobtcfi else (), **self.OB)

    def test_openbsd_ibt_is_read(self):
        self.assertEqual(holt_e2.shape(self.ob("a"))["ibt"], "landing pads, GNU property IBT")
        self.assertEqual(holt_e2.shape(self.ob("b", pads=False, note=False))["ibt"], "no landing pads, no GNU property IBT")
        self.assertEqual(holt_e2.shape(self.ob("c", nobtcfi=True))["ibt"], "NOBTCFI (opted out)")
        self.assertNotIn("ibt", holt_e2.shape(elf(self.p("f"))), "OpenBSD's alone (EI_OSABI 12)")

    def test_openbsd_ibt_difference_fails(self):
        vm = self.ob("vm")
        rc, out = self.run_cmd(["elf", "openbsd", vm, self.ob("same")])
        self.assertEqual(rc, 0, out)
        self.assertIn("same ibt: landing pads, GNU property IBT", out)
        for name, kw in (("nopads", {"pads": False}), ("nonote", {"note": False}), ("nobtcfi", {"nobtcfi": True})):
            rc, out = self.run_cmd(["elf", "openbsd", vm, self.ob(name, **kw)])
            self.assertEqual(rc, 1, (name, out))
            self.assertIn("DIFF ibt: VM landing pads, GNU property IBT cross ", out)

    def test_xonly_marks_a_code_segment_alone(self):
        x = elf(self.p("x"), note_flags=6, **self.OB)   # (its one LOAD holds an SHF_EXECINSTR section alone)
        rc, out = self.run_cmd(["xonly", x])
        self.assertEqual(rc, 0, out)
        self.assertEqual(holt_e2.shape(x)["load_flags"], ["E"])
        d = elf(self.p("d"), **self.OB)   # (a data section in it: the code might read it)
        with open(d, "rb") as f:
            before = f.read()
        rc, out = self.run_cmd(["xonly", d])
        self.assertEqual(rc, 1, out)
        self.assertIn("not code alone", out)
        with open(d, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_linkface_reads_the_interface_not_the_layout(self):
        # (OpenBSD's reorder_libs: the same library relinked in another order, other bytes)
        a = elf(self.p("a"), needed=("libc.so.104.0",), code=b"\x90" * 64, **{k: v for k, v in self.OB.items() if k != "needed"})
        b = elf(self.p("b"), needed=("libc.so.104.0",), code=b"\xcc" * 64, **{k: v for k, v in self.OB.items() if k != "needed"})
        ra, oa = self.run_cmd(["linkface", a])
        rb, ob = self.run_cmd(["linkface", b])
        self.assertEqual((ra, rb), (0, 0))
        self.assertEqual(oa, ob)
        self.assertIn("needed libc.so.104.0", oa)
        c = elf(self.p("c"), needed=("libc.so.105.0",), **{k: v for k, v in self.OB.items() if k != "needed"})
        self.assertNotEqual(self.run_cmd(["linkface", c])[1], oa)

    def test_version_needs_met_by_the_sysroot(self):
        vm = elf(self.p("vm"))
        x = elf(self.p("x"))
        a, b = holt_e2.shape(vm), holt_e2.shape(x)
        self.assertEqual(a, b)
        # unmet_versions: a library the sysroot lacks, or one without the version, is unmet.
        self.assertEqual(holt_e2.unmet_versions(["libc.so.8 DF506.0"], self.d), ["libc.so.8 DF506.0"])

    def test_api_ignores_the_build_fields_only(self):
        a = {"api": 3, "documents": {"dump": 4}, "features": ["zfs"], "image": {"dump_schemas": [1]}, "platform": "freebsd"}
        b = dict(a, features=["zfs", "x"], image={"dump_schemas": [1, 2]})
        for n, d in (("a", a), ("b", b), ("c", dict(a, documents={"dump": 5}))):
            with open(self.p(n), "w") as f:
                json.dump(d, f)
        rc, out = self.run_cmd(["api", self.p("a"), self.p("b"), "netbsd"])
        self.assertEqual(rc, 0, out)
        self.assertIn("build field differs (allowed): features", out)
        rc, out = self.run_cmd(["api", self.p("a"), self.p("c"), "netbsd"])
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF documents/dump", out)
        with open(self.p("bad"), "w") as f:
            f.write("bko-holt 0.2.6\n")   # an old one's text line
        self.assertEqual(self.run_cmd(["api", self.p("a"), self.p("bad")])[0], 1)

    def results(self, name, logs):
        d = self.p(name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "tests.tsv"), "w") as f:
            for b, (rc, text) in logs.items():
                f.write(f"{b}\t{rc}\t3\n")
                with open(os.path.join(d, b + ".log"), "w") as g:
                    g.write(text)
        return d

    def test_tests_cross_against_native(self):
        n = self.results("n", {"lib-bko_holt": (101, "test a ... ok\ntest b ... FAILED\ntest c ... ignored\n"),
                               "test-cli": (0, "test d ... ok\n")})
        x = self.results("x", {"lib-bko_holt": (101, "test a ... ok\ntest b ... FAILED\ntest c ... ignored\n"),
                               "test-cli": (0, "test d ... ok\n")})
        rc, out = self.run_cmd(["tests", n, x, "freebsd"])
        self.assertEqual(rc, 0, out)
        self.assertIn("1 fail natively too", out)
        x2 = self.results("x2", {"lib-bko_holt": (101, "test a ... FAILED\ntest b ... FAILED\n"),
                                 "test-cli": (0, "test d ... ok\n")})
        rc, out = self.run_cmd(["tests", n, x2, "freebsd"])
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF lib-bko_holt a: ok natively, FAILED cross-built", out)
        x3 = self.results("x3", {"lib-bko_holt": (101, "test a ... ok\ntest b ... ok\n"),
                                 "test-cli": (139, "")})   # a crash before any test
        rc, out = self.run_cmd(["tests", n, x3, "freebsd"])
        self.assertEqual(rc, 1, out)
        self.assertIn("ok cross-built only: ['b']", out)
        self.assertIn("test-cli", out.splitlines()[-1])
        rc, out = self.run_cmd(["tests", self.p("none"), x, "freebsd"])
        self.assertEqual(rc, 1)

    def test_artifacts(self):
        lines = [{"reason": "compiler-artifact", "target": {"kind": ["lib"], "name": "bko_holt"},
                  "profile": {"test": True}, "executable": "/t/deps/bko_holt-1"},
                 {"reason": "compiler-artifact", "target": {"kind": ["bin"], "name": "bko-holt"},
                  "profile": {"test": False}, "executable": "/t/bko-holt"},
                 {"reason": "compiler-artifact", "target": {"kind": ["lib"], "name": "bko_holt"},
                  "profile": {"test": False}, "executable": None},
                 {"reason": "build-finished", "success": True}]
        with open(self.p("c.json"), "w") as f:
            f.write("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
        rc, out = self.run_cmd(["artifacts", self.p("c.json")])
        self.assertEqual(out, "lib-bko_holt\t1\t/t/deps/bko_holt-1\nbin-bko-holt-program\t0\t/t/bko-holt\n")

    def test_dumps_by_judge(self):
        dump = {"v": 1, "disks": [{"name": "vtbd0", "size": 1 << 34}]}
        for run in ("vm", "x"):
            k = os.path.join(self.d, run, "freebsd14")
            os.makedirs(os.path.join(k, "dumps"))
            with open(os.path.join(k, "report.txt"), "w") as f:
                f.write("guest freebsd14\nRESULT PASS\n")
            for n in ("src", "restored"):
                with open(os.path.join(k, "dumps", n + ".json"), "w") as f:
                    json.dump(dump, f)
        rc, out = self.run_cmd(["dumps", "freebsd", self.p("vm"), self.p("x"), "freebsd14"])
        self.assertEqual(rc, 0, out)
        self.assertIn("same freebsd14", out)
        with open(os.path.join(self.d, "x", "freebsd14", "dumps", "src.json"), "w") as f:
            json.dump({"v": 1, "disks": [{"name": "vtbd0", "size": 1 << 35}]}, f)
        rc, out = self.run_cmd(["dumps", "freebsd", self.p("vm"), self.p("x"), "freebsd14"])
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF freebsd14 src", out)
        rc, out = self.run_cmd(["dumps", "freebsd", self.p("vm"), self.p("x"), "netbsd11"])
        self.assertEqual(rc, 1)
        self.assertIn("no records", out)


BASH = modern_bash()


@unittest.skipUnless(BASH, "needs bash 4 or newer with timeout and realpath (the test hosts')")
class HoltCrossRefuses(unittest.TestCase):
    TOOL = os.path.join(H1D, "holt-cross.sh")

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="t2-i10-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.work = os.path.join(self.d, "work")
        os.makedirs(os.path.join(self.work, "holt-bin"))
        self.cmp = os.path.join(self.d, "cmp-run.sh")
        with open(self.cmp, "w") as f:
            f.write('#!/usr/bin/env bash\necho called >"$(dirname "$0")/cmp-called"\n')
        os.chmod(self.cmp, 0o755)

    def run_tool(self, *args, **env):
        e = dict(os.environ, BKO_WORK=self.work, HOLT_CROSS_CACHE=os.path.join(self.d, "cache"),
                 HOLT_CMP_RUN=self.cmp, HOLT_LOGS=os.path.join(self.d, "logs"), **env)
        return subprocess.run([BASH, self.TOOL] + list(args), capture_output=True, text=True, env=e, timeout=60)

    def test_code_plat_is_refused(self):
        code = os.path.join(self.d, "t2", "holt-bin", "c" * 64)
        for out in (os.path.join(code, "freebsd"), os.path.join(code, "cross", "netbsd"), code):
            for args in (("freebsd",), ("--reserved", "t2h-x", "freebsd")):
                r = self.run_tool(*args, HOLT_OUT=out)
                self.assertEqual(r.returncode, 2, (out, args, r.stderr))
                self.assertIn("must end in cross/freebsd", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.d, "cmp-called")), "refused before the reservation")
        self.assertFalse(os.path.exists(os.path.join(code, "freebsd")))

    def test_shipped_set_is_refused(self):
        r = self.run_tool("netbsd", HOLT_OUT=os.path.join(self.work, "holt-bin", "x", "cross", "netbsd"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("never lands in a shipped set", r.stderr)
        link = os.path.join(self.d, "alias")
        os.symlink(os.path.join(self.work, "holt-bin"), link)
        r = self.run_tool("netbsd", HOLT_OUT=os.path.join(link, "cross", "netbsd"))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_platforms_not_built_here(self):
        for p, why in (("haiku", "VM build"), ("linux", "freebsd, netbsd, illumos, openbsd or dragonfly")):
            r = self.run_tool(p)
            self.assertEqual(r.returncode, 2, (p, r.stderr))
            self.assertIn(why, r.stderr)

    def test_tier3_code_plat_is_refused(self):   # (I11: openbsd and dragonfly as the others)
        code = os.path.join(self.d, "t2", "holt-bin", "c" * 64)
        for p in ("openbsd", "dragonfly"):
            r = self.run_tool(p, HOLT_OUT=os.path.join(code, p))
            self.assertEqual(r.returncode, 2, (p, r.stderr))
            self.assertIn(f"must end in cross/{p}", r.stderr)


FLOCK = shutil.which("flock") and shutil.which("sha256sum") and BASH


@unittest.skipUnless(FLOCK, "needs flock, sha256sum and GNU stat (the test hosts')")
class HoltCrossSysrootLock(unittest.TestCase):
    """A build holds CACHE/.lock-VM shared to its end, so that no maker replaces the sysroot
    it links against (a fake world: rustup, cargo, clang and ld.lld stubs, a current sysroot)."""

    def test_build_holds_the_sysroot_lock_shared(self):
        d = tempfile.mkdtemp(prefix="t2-i10l-")
        self.addCleanup(shutil.rmtree, d, True)
        home, work, cache, bind = (os.path.join(d, x) for x in ("home", "work", "cache", "bin"))
        lockf, probe = os.path.join(cache, ".lock-freebsd14"), os.path.join(d, "probe")

        def put(path, text, mode=0o644):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
            os.chmod(path, mode)
        put(os.path.join(home, ".cargo", "bin", "rustup"),
            '#!/bin/sh\ncase $1 in run) echo "rustc 1.94.1 (fake)" ;; target) echo x86_64-unknown-freebsd ;; esac\n', 0o755)
        put(os.path.join(home, ".cargo", "bin", "cargo"),
            '#!/usr/bin/env bash\nif flock -n -x "$LOCKF" true; then echo free >"$PROBE"; else echo held >"$PROBE"; fi\n'
            'mkdir -p "$CARGO_TARGET_DIR/x86_64-unknown-freebsd/release"\n'
            'echo fake >"$CARGO_TARGET_DIR/x86_64-unknown-freebsd/release/bko-holt"\n', 0o755)
        put(os.path.join(bind, "clang"), '#!/bin/sh\necho "fake clang 1"\n', 0o755)
        put(os.path.join(bind, "ld.lld"), '#!/bin/sh\necho "fake lld 1"\n', 0o755)
        img = os.path.join(work, "vms", "freebsd14", "freebsd14.qcow2")
        put(img, "image")
        put(os.path.join(work, "vms", "freebsd14", "id_ed25519"), "key")
        st = os.stat(img)
        sr = os.path.join(cache, "sysroot", "freebsd14")
        put(os.path.join(sr, ".image"), f"vms/freebsd14/freebsd14.qcow2\nfreebsd14.qcow2 {st.st_size} {int(st.st_mtime)}\n")
        put(os.path.join(sr, ".digest"), "ab" * 32 + "\n")
        src = os.path.join(d, "src")
        for n in ("Cargo.toml", "Cargo.lock", "LICENSE", "src/main.rs", "tests/t.rs"):
            put(os.path.join(src, n), "")
        os.makedirs(os.path.join(d, "tmp"))
        out, info = os.path.join(d, "t2", "CODE", "cross", "freebsd"), os.path.join(d, "info.json")
        env = dict(os.environ, HOME=home, BKO_WORK=work, HOLT_CROSS_CACHE=cache, HOLT_SRC=src, HOLT_OUT=out,
                   HOLT_LOGS=os.path.join(d, "logs"), HOLT_INFO=info, TMPDIR=os.path.join(d, "tmp"),
                   PATH=bind + os.pathsep + os.environ["PATH"], LOCKF=lockf, PROBE=probe)
        r = subprocess.run([BASH, os.path.join(H1D, "holt-cross.sh"), "freebsd"], capture_output=True, text=True,
                           env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(probe) as f:
            self.assertEqual(f.read().strip(), "held", "the lock is held while cargo builds")
        self.assertTrue(os.path.isfile(os.path.join(out, "bko-holt")))
        self.assertEqual(subprocess.run(["flock", "-n", "-x", lockf, "true"]).returncode, 0, "let go at the end")
        j = jload_file(info)
        self.assertEqual((j["build"], j["kind"]), ("cross:1.94.1:freebsd14@" + "ab" * 6, "cross"))
        self.assertTrue(j["clang"] and j["lld"], j)


@unittest.skipUnless(FLOCK, "needs flock, sha256sum and GNU stat (the test hosts')")
class HoltCrossTier3(unittest.TestCase):
    """I11: openbsd and dragonfly build with the pinned 1.94.1 and -Zbuild-std (rustup has no std
    for them; RUSTC_BOOTSTRAP=1 for -Z on a stable toolchain), OpenBSD's with -Zcf-protection=branch, and OpenBSD's lld takes -lNAME as OpenBSD's
    ld does (a fake world: rustup, cargo, clang and ld.lld stubs, a current sysroot)."""

    def build(self, plat, vm, target, libs=()):
        d = tempfile.mkdtemp(prefix="t2-i11-")
        self.addCleanup(shutil.rmtree, d, True)
        home, work, cache, bind = (os.path.join(d, x) for x in ("home", "work", "cache", "bin"))

        def put(path, text, mode=0o644):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
            os.chmod(path, mode)
        put(os.path.join(home, ".cargo", "bin", "rustup"),
            '#!/bin/sh\ncase "$1 $4" in\n "run --print") echo /fake/sysroot ;;\n run*) echo "rustc 1.94.1 (fake)" ;;\n'
            ' component*) echo rust-src ;;\n target*) ;;\nesac\n', 0o755)
        put(os.path.join(home, ".cargo", "bin", "cargo"),
            '#!/usr/bin/env bash\nprintf "%s\\n" "$RUSTUP_TOOLCHAIN" "$RUSTFLAGS" "$*" "${RUSTC_BOOTSTRAP:-}" >"$PROBE"\n'
            f'mkdir -p "$CARGO_TARGET_DIR/{target}/release"\ncp "$FAKE_ELF" "$CARGO_TARGET_DIR/{target}/release/bko-holt"\n', 0o755)
        put(os.path.join(bind, "clang"), '#!/bin/sh\necho "fake clang 1"\n', 0o755)
        put(os.path.join(bind, "ld.lld"), '#!/bin/sh\nprintf "%s\\n" "$@"\n', 0o755)
        img = os.path.join(work, "vms", vm, vm + ".qcow2")
        put(img, "image")
        put(os.path.join(work, "vms", vm, "id_ed25519"), "key")
        st = os.stat(img)
        sr = os.path.join(cache, "sysroot", vm)
        put(os.path.join(sr, ".image"), f"vms/{vm}/{vm}.qcow2\n{vm}.qcow2 {st.st_size} {int(st.st_mtime)}\n"
            + ("digest linkface\n" if plat == "openbsd" else ""))   # (I11's digest of reorder_libs' libraries)
        put(os.path.join(sr, ".digest"), "cd" * 32 + "\n")
        for lib in libs:
            put(os.path.join(sr, "usr", "lib", lib), "")
        src = os.path.join(d, "src")
        for n in ("Cargo.toml", "Cargo.lock", "LICENSE", "src/main.rs", "tests/t.rs"):
            put(os.path.join(src, n), "")
        tmp = os.path.join(d, "tmp")
        os.makedirs(tmp)
        out, info, probe = os.path.join(d, "t2", "CODE", "cross", plat), os.path.join(d, "info.json"), os.path.join(d, "probe")
        env = dict(os.environ, HOME=home, BKO_WORK=work, HOLT_CROSS_CACHE=cache, HOLT_SRC=src, HOLT_OUT=out,
                   HOLT_LOGS=os.path.join(d, "logs"), HOLT_INFO=info, TMPDIR=tmp,
                   PATH=bind + os.pathsep + os.environ["PATH"], PROBE=probe,
                   FAKE_ELF=elf(os.path.join(d, "fake-elf"), note_flags=6, interp="/usr/libexec/ld.so", osabi=12))
        r = subprocess.run([BASH, os.path.join(H1D, "holt-cross.sh"), plat], capture_output=True, text=True,
                           env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(probe) as f:
            tc, flags, args, boot = f.read().split("\n")[:4]
        self.assertEqual(boot, "1", "-Z on the stable toolchain")
        return tc, flags, args, jload_file(info), tmp

    def test_openbsd(self):
        tc, flags, args, info, tmp = self.build("openbsd", "openbsd8", "x86_64-unknown-openbsd",
                                                libs=("libc.so.103.0", "libc.so.104.0", "libc.so.97.2", "libc.a",
                                                      "libc++abi.so.10.0", "libcompiler_rt.a", "libm.so"))
        self.assertEqual(tc, "1.94.1")
        self.assertIn("-Zbuild-std=std,panic_unwind", args.split())
        self.assertNotIn("--offline", args.split(), "std's crates may come from crates.io")
        self.assertIn("--locked", args.split())
        self.assertIn("-Zcf-protection=branch", flags.split(), "IBT landing pads, std's included (D11)")
        self.assertIn("--remap-path-prefix=/fake/sysroot/lib/rustlib/src/rust=/rustc/src", flags.split())
        self.assertEqual(holt_e2.shape(info["out"])["load_flags"], ["E"], "execute-only, as OpenBSD's lld links")
        self.assertEqual((info["build"], info["toolchain"], info["tier"]),
                         ("cross:1.94.1:openbsd8@" + "cd" * 6, "1.94.1", 3))
        # The linker: -lNAME is the newest libNAME.so.MAJOR.MINOR (not libc.a), by name in DT_NEEDED.
        r = subprocess.run([os.path.join(tmp, "holt-cross-ld"), "-o", "x", "-L/usr/lib", "-lc", "-lc++abi", "-lcompiler_rt", "-lm",
                            "-lnone", "-l:libc.so.97.2"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["-o", "x", "-L" + os.path.join(os.path.dirname(tmp), "cache", "sysroot", "openbsd8", "usr", "lib"), "-l:libc.so.104.0", "-l:libc++abi.so.10.0", "-lcompiler_rt",
                                            "-lm", "-lnone", "-l:libc.so.97.2"])

    def test_dragonfly(self):
        tc, flags, args, info, tmp = self.build("dragonfly", "dragonfly6", "x86_64-unknown-dragonfly")
        self.assertEqual(tc, "1.94.1")
        self.assertIn("-Zbuild-std=std,panic_unwind", args.split())
        self.assertNotIn("-Zcf-protection=branch", flags.split(), "DragonFly's VM build has no landing pads")
        self.assertEqual(info["build"], "cross:1.94.1:dragonfly6@" + "cd" * 6)
        self.assertFalse(os.path.exists(os.path.join(tmp, "holt-cross-ld")))
        self.assertEqual(holt_e2.shape(info["out"])["load_flags"], ["RE"], "xonly is OpenBSD's alone")
        with open(os.path.join(tmp, "holt-cross-cc")) as f:
            cc = f.read()
        for w in ("-Wl,-pie", "-Wl,--dynamic-linker=/libexec/ld-elf.so.2", "-Wl,-rpath,/usr/lib/gcc80"):
            self.assertIn(w, cc, "as DragonFly's gcc links")


def jload_file(path):
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    unittest.main()
