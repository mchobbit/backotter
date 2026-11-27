#!/usr/bin/env python3
"""E2, a cross-built bko-holt against its VM build (docs/design/img-speed.md §8.2; I10, I11).

  holt_e2.py elf PLATFORM VM_BIN CROSS_BIN [SYSROOT]
                                                step 0: the ELF shape (SYSROOT: the cross build's,
                                                holt-cross.sh's cache, for the symbol versions)
  holt_e2.py api VM.json CROSS.json             step 1: `bko-holt --version --json`
  holt_e2.py tests NATIVE_DIR CROSS_DIR [PLAT]   step 2: the test binaries' results, cross against native
                                                (each DIR: tests.tsv NAME RC SECS, NAME.log)
  holt_e2.py dumps PLATFORM VM_RECORDS CROSS_RECORDS GUEST...
                                                step 3: two HoltTest runs' dumps, by judge.py
  holt_e2.py artifacts CARGO.json               the executables a cargo --message-format=json run made
  holt_e2.py linkface FILE                      (holt-cross.sh, openbsd) what a link against the shared
                                                library FILE reads of it: its dynamic symbols (name,
                                                type, binding, visibility, defined, size), versions,
                                                SONAME and DT_NEEDED, sorted (not their addresses)
  holt_e2.py xonly FILE                         (holt-cross.sh, openbsd) mark FILE's code segment
                                                execute-only, as OpenBSD's own lld links it
  holt_e2.py fetch DEST RUN GUEST...            (on the Mac) step 3's records: each GUEST's K of
                                                HoltTest2 run RUN (or its rerun RUN-r2, when that
                                                is the one that passed), report.txt and dumps/, from
                                                whichever default amd64 host has it, into DEST/GUEST

Each prints its findings, one per line, and a last line "E2 step N PLATFORM: PASS" or
"... FAIL (why)"; exit 0 on PASS, 1 on FAIL, 2 on a usage error.

Step 0 reads the ELF files itself (64-bit little-endian, as every amd64 build is): the type
(EXEC, or DYN with DF_1_PIE: PIE), the interpreter, the program headers' types and flags
(not their addresses, sizes or count: the linkers lay LOAD segments out each their own way, so
LOAD is the set of flag combinations, and a W+X segment anywhere fails), RELRO and BIND_NOW,
the DT_NEEDED libraries in order, DT_RPATH and DT_RUNPATH, DT_FLAGS and DT_FLAGS_1, TEXTREL,
the symbol versions each library must define (.gnu.version_r, which illumos's .SUNW_version
is in the same format), and every note (owner, type, descriptor; a GNU build-id's descriptor
is the build's own).  A difference is a FAIL unless EXPLAINED names it for the platform,
with why it is no difference in behaviour; an explained one is printed as such.  For OpenBSD
(EI_OSABI 12) it also reads the IBT handling (D11), the only guard there is, since no farm CPU
enforces IBT: PT_OPENBSD_NOBTCFI, the GNU property note's IBT bit (which lld and GNU ld write only
when every object linked has it), and whether the executable segments are full of endbr64 landing
pads (at least one per 4 KiB) or have none to speak of.

Step 1 compares the two JSON documents, apart from the build fields (features,
dump_schemas and platform, wherever they are) and generated (the time it was written).  Step 2:
holt's tests assume Linux in places, so some fail on these systems whatever compiled them; it
passes when every test that passes built natively (the guest's Rust, in the same guest) passes
cross-built too, and every binary ran.  Step 3: for each GUEST, the newest K of each records directory
(RECORDS/RUN/GUEST/, the run's own) must hold report.txt with RESULT PASS, and judge.py
must find the two runs' source dumps equal and the two restored dumps equal (its
normalisation: what differs between two boots of one machine).
"""

import json
import os
import re
import struct
import subprocess
import sys

PT = {0: "NULL", 1: "LOAD", 2: "DYNAMIC", 3: "INTERP", 4: "NOTE", 5: "SHLIB", 6: "PHDR", 7: "TLS",
      0x6474e550: "GNU_EH_FRAME", 0x6474e551: "GNU_STACK", 0x6474e552: "GNU_RELRO", 0x6474e553: "GNU_PROPERTY",
      0x6464e550: "SUNW_UNWIND", 0x6ffffffa: "SUNWBSS", 0x6ffffffb: "SUNWSTACK",
      0x65a3dbe5: "OPENBSD_MUTABLE", 0x65a3dbe6: "OPENBSD_RANDOMIZE", 0x65a3dbe7: "OPENBSD_WXNEEDED", 0x65a3dbe8: "OPENBSD_NOBTCFI",
      0x65a41be6: "OPENBSD_BOOTDATA", 0x65a3dbe9: "OPENBSD_SYSCALLS"}
DT_NEEDED, DT_STRTAB, DT_RPATH, DT_TEXTREL, DT_RUNPATH, DT_FLAGS, DT_FLAGS_1, DT_BIND_NOW = 1, 5, 15, 22, 29, 30, 0x6ffffffb, 24
DT_VERNEED, DT_VERNEEDNUM, DT_VERDEF, DT_VERDEFNUM = 0x6ffffffe, 0x6fffffff, 0x6ffffffc, 0x6ffffffd
DF = {0x1: "ORIGIN", 0x2: "SYMBOLIC", 0x4: "TEXTREL", 0x8: "BIND_NOW", 0x10: "STATIC_TLS"}
DF_1 = {0x1: "NOW", 0x8: "NODELETE", 0x20: "INTERPOSE", 0x8000000: "PIE", 0x4: "GROUP", 0x80: "NOOPEN",
        0x100: "ORIGIN", 0x800: "NODEFLIB", 0x1000: "NODUMP"}

# Differences that change nothing the binary does, by platform: (finding prefix, why).
EXPLAINED = {
    "netbsd": [
        ("rpath: VM ['/usr/pkg/lib/libatomic']",
         "pkgsrc's rust links every program with -R/usr/pkg/lib/libatomic (rustc's own link args in the "
         "build guest, seen in its --print link-args); bko-holt needs no library from there (its DT_NEEDED "
         "are base libraries), and the images and guests have no such directory"),
        ("flags: VM [] cross ['BIND_NOW']",
         "-z now: GNU ld records it as DF_1_NOW alone, lld as DF_1_NOW and DF_BIND_NOW too; both are "
         "flags_1 NOW, and ld.elf_so binds every symbol at load for either"),
    ],
    "illumos": [
        ("rpath: VM ['/usr/gcc/14/lib/amd64'] cross []",
         "illumos's ld writes a -R directory as both DT_RPATH and DT_RUNPATH, lld as DT_RUNPATH alone "
         "(--enable-new-dtags); ld.so.1 searches the same /usr/gcc/14/lib/amd64 either way, and the "
         "runpath line is compared on its own"),
        # The VM build is linked by illumos's own ld, the cross build by lld: their layouts differ.
        ("load_flags: VM ['RE', 'RW'] cross ['R', 'RE', 'RW']",
         "lld gives read-only data a LOAD segment of its own (R) apart from the code (RE); no segment "
         "is looser than the VM build's, and none is writable and executable"),
        ("relro: VM False cross True",
         "lld marks the relocated read-only data PT_GNU_RELRO: a loader that honours it makes those pages "
         "read-only once relocated, one that doesn't leaves them as the VM build's are; nothing writes there"),
        ("segments: VM ['DYNAMIC RW', 'INTERP R', 'SUNW_UNWIND R'] cross ['DYNAMIC RW', 'GNU_EH_FRAME R', "
         "'GNU_RELRO R', 'GNU_STACK RW', 'INTERP R']",
         "GNU_EH_FRAME is illumos's PT_SUNW_EH_FRAME (the same value, sys/elf.h), naming .eh_frame_hdr as "
         "PT_SUNW_UNWIND does, and libgcc_s's unwinder reads either; GNU_STACK RW asks for the "
         "non-executable stack the VM build gets by default; GNU_RELRO: above"),
    ],
}


class Elf:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.b = f.read()
        b = self.b
        if b[:4] != b"\x7fELF" or b[4] != 2 or b[5] != 1:
            raise ValueError(f"{path}: not a 64-bit little-endian ELF file")
        self.osabi = b[7]
        (self.type, _m, _v, _e, phoff, shoff, _fl, _eh, phentsize, phnum, shentsize, shnum,
         shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", b, 16)
        self.ph = [struct.unpack_from("<IIQQQQQQ", b, phoff + i * phentsize) for i in range(phnum)]
        self.sh = [struct.unpack_from("<IIQQQQIIQQ", b, shoff + i * shentsize) for i in range(shnum)]
        so = self.sh[shstrndx][4] if shnum else 0
        self.sname = [self.cstr(so + s[0]) for s in self.sh] if shnum else []

    def cstr(self, off):
        e = self.b.index(b"\0", off)
        return self.b[off:e].decode("utf-8", "replace")

    def vaddr_off(self, va):
        for t, _f, off, vaddr, _pa, filesz, _ms, _al in self.ph:
            if t == 1 and vaddr <= va < vaddr + filesz:
                return off + (va - vaddr)
        raise ValueError(f"address {va:#x} in no LOAD segment")

    def dynamic(self):
        out = []
        for t, _f, off, _va, _pa, filesz, _ms, _al in self.ph:
            if t == 2:
                for i in range(0, filesz, 16):
                    tag, val = struct.unpack_from("<qQ", self.b, off + i)
                    if tag == 0:
                        break
                    out.append((tag, val))
        return out

    def notes(self):
        out = []
        for name, s in zip(self.sname, self.sh):
            if s[1] != 7:   # SHT_NOTE
                continue
            off, size, p = s[4], s[5], 0
            while p + 12 <= size:
                nsz, dsz, typ = struct.unpack_from("<III", self.b, off + p)
                p += 12
                owner = self.b[off + p:off + p + nsz].rstrip(b"\0").decode("utf-8", "replace")
                p += (nsz + 3) & ~3
                desc = self.b[off + p:off + p + dsz]
                p += (dsz + 3) & ~3
                out.append((owner, typ, desc))
        if not self.sh:   # (no section headers: the PT_NOTE segments instead)
            for t, _f, o, _va, _pa, filesz, _ms, _al in self.ph:
                if t != 4:
                    continue
                p = 0
                while p + 12 <= filesz:
                    nsz, dsz, typ = struct.unpack_from("<III", self.b, o + p)
                    p += 12
                    owner = self.b[o + p:o + p + nsz].rstrip(b"\0").decode("utf-8", "replace")
                    p += (nsz + 3) & ~3
                    out.append((owner, typ, self.b[o + p:o + p + dsz]))
                    p += (dsz + 3) & ~3
        return out


ENDBR64 = bytes.fromhex("f30f1efa")
GNU_PROPERTY_X86_FEATURE_1_AND, X86_FEATURE_1_IBT = 0xc0000002, 1


def ibt(e):
    """OpenBSD's IBT handling (§8.2 step 0, D11): its kernel kills a binary that branches
    indirectly to anything but an endbr64 on a CPU with IBT, unless PT_OPENBSD_NOBTCFI opts out."""
    if any(t == 0x65a3dbe8 for t, *_ in e.ph):
        return "NOBTCFI (opted out)"
    prop = False
    for owner, typ, desc in e.notes():
        if owner == "GNU" and typ == 5:   # NT_GNU_PROPERTY_TYPE_0: (type, size, data) records, 8-aligned
            p = 0
            while p + 8 <= len(desc):
                pt, ps = struct.unpack_from("<II", desc, p)
                if pt == GNU_PROPERTY_X86_FEATURE_1_AND and ps >= 4:
                    prop = prop or bool(struct.unpack_from("<I", desc, p + 8)[0] & X86_FEATURE_1_IBT)
                p += 8 + ((ps + 7) & ~7)
    size = pads = 0
    for t, fl, off, *_r in e.ph:
        if t == 1 and fl & 1:
            filesz = _r[3]
            size += filesz
            pads += e.b.count(ENDBR64, off, off + filesz)
    words = ["landing pads" if pads and pads * 4096 >= size else "no landing pads"]
    words.append("GNU property IBT" if prop else "no GNU property IBT")
    return ", ".join(words)


def flags_words(v, table):
    return sorted(n for bit, n in table.items() if v & bit) + ([f"{v & ~sum(table):#x}"] if v & ~sum(table) else [])


def shape(path):
    e = Elf(path)
    d = {"osabi": e.osabi}
    dyn = e.dynamic()
    tags = {}
    for t, v in dyn:
        tags.setdefault(t, []).append(v)
    f1 = (tags.get(DT_FLAGS_1) or [0])[0]
    d["type"] = "EXEC" if e.type == 2 else ("PIE" if e.type == 3 and f1 & 0x8000000 else f"type {e.type}")
    interp = [e.b[off:off + fs].rstrip(b"\0").decode() for t, _f, off, _va, _pa, fs, _ms, _al in e.ph if t == 3]
    d["interp"] = interp[0] if interp else None
    segs, loads, wx = set(), set(), False
    for t, fl, *_ in e.ph:
        fw = ("R" if fl & 4 else "") + ("W" if fl & 2 else "") + ("E" if fl & 1 else "")
        wx = wx or ("W" in fw and "E" in fw)
        if t == 1:
            loads.add(fw)
        elif t != 6:   # (PHDR: where the table is, a layout matter)
            segs.add(f"{PT.get(t, hex(t))} {fw}")
    d["segments"], d["load_flags"], d["wx"] = sorted(segs), sorted(loads), wx
    d["relro"] = any(t == 0x6474e552 for t, *_ in e.ph)
    if e.osabi == 12:   # ELFOSABI_OPENBSD
        d["ibt"] = ibt(e)
    if dyn:
        stro = e.vaddr_off(tags[DT_STRTAB][0])
        s = lambda o: e.cstr(stro + o)   # noqa: E731
        d["needed"] = [s(v) for v in tags.get(DT_NEEDED, [])]
        d["rpath"] = [s(v) for v in tags.get(DT_RPATH, [])]
        d["runpath"] = [s(v) for v in tags.get(DT_RUNPATH, [])]
        d["flags"] = flags_words((tags.get(DT_FLAGS) or [0])[0], DF)
        d["flags_1"] = flags_words(f1, DF_1)
        d["bind_now"] = DT_BIND_NOW in tags or "BIND_NOW" in d["flags"] or "NOW" in d["flags_1"]
        d["textrel"] = DT_TEXTREL in tags or "TEXTREL" in d["flags"]
        need = []
        if DT_VERNEED in tags:
            o = e.vaddr_off(tags[DT_VERNEED][0])
            for _ in range((tags.get(DT_VERNEEDNUM) or [0])[0]):
                _ver, cnt, file_, aux, nxt = struct.unpack_from("<HHIII", e.b, o)
                a = o + aux
                for _ in range(cnt):
                    _h, _fl, _other, name, anext = struct.unpack_from("<IHHII", e.b, a)
                    need.append(f"{s(file_)} {s(name)}")
                    if not anext:
                        break
                    a += anext
                if not nxt:
                    break
                o += nxt
        d["version_needs"] = sorted(need)
    else:
        d["needed"] = None
    notes = []
    for owner, typ, desc in e.notes():
        if owner == "GNU" and typ == 3:
            notes.append("GNU 3 build-id")
        else:
            notes.append(f"{owner} {typ} {desc.hex()}")
    d["notes"] = sorted(notes)
    return d


def version_defs(path):
    """The symbol versions a shared library defines (.gnu.version_d), as "LIB VERSION"."""
    e = Elf(path)
    tags = {}
    for t, v in e.dynamic():
        tags.setdefault(t, []).append(v)
    if DT_VERDEF not in tags or DT_STRTAB not in tags:
        return set()
    stro, o, out = e.vaddr_off(tags[DT_STRTAB][0]), e.vaddr_off(tags[DT_VERDEF][0]), set()
    for _ in range((tags.get(DT_VERDEFNUM) or [0])[0]):
        _v, flags, _ndx, _cnt, _h, aux, nxt = struct.unpack_from("<HHHHIII", e.b, o)
        if not flags & 1:   # (VER_FLG_BASE: the file's own name)
            out.add(e.cstr(stro + struct.unpack_from("<I", e.b, o + aux)[0]))
        if not nxt:
            break
        o += nxt
    return out


def unmet_versions(needs, sysroot):
    """Of NEEDS ("LIB VERSION"), those that the sysroot's LIB doesn't define (in lib/, usr/lib/)."""
    defs, unmet = {}, []
    for n in needs:
        lib, ver = n.split(" ", 1)
        if lib not in defs:
            p = next((os.path.join(sysroot, d, lib) for d in ("lib", "usr/lib")
                      if os.path.isfile(os.path.join(sysroot, d, lib))), None)
            defs[lib] = version_defs(p) if p else None
        if defs[lib] is None or ver not in defs[lib]:
            unmet.append(n)
    return unmet


def cmd_elf(plat, vm, cross, sysroot=None):
    a, b = shape(vm), shape(cross)
    out, bad = [], []
    for k in sorted(set(a) | set(b)):
        if a.get(k) == b.get(k):
            out.append(f"same {k}: {a.get(k)}")
            continue
        if k == "version_needs" and set(b.get(k) or ()) < set(a.get(k) or ()):
            out.append(f"explained {k}: the cross build needs a subset, it lacks {sorted(set(a[k]) - set(b[k]))} "
                       "(its std is the pinned toolchain's, older than the guest's packaged Rust: every "
                       "version it needs the VM build needs too, so it runs wherever the VM build runs)")
            continue
        if k == "version_needs" and sysroot and not unmet_versions(b.get(k) or (), sysroot):
            out.append(f"explained {k}: VM {a.get(k)} cross {b.get(k)} (the two call different libc symbols, "
                       "each std its own Rust's; every version the cross build needs is defined by its sysroot's "
                       f"library, {sysroot}: the guest's own, of the release the images carry)")
            continue
        f = f"{k}: VM {a.get(k)} cross {b.get(k)}"
        why = next((w for pre, w in EXPLAINED.get(plat, ()) if f.startswith(pre)), None)
        if why:
            out.append(f"explained {f} ({why})")
        else:
            out.append(f"DIFF {f}")
            bad.append(k)
    if b.get("wx"):
        out.append("DIFF the cross build has a writable and executable segment")
        bad.append("wx")
    out.append(f"E2 step 0 {plat}: " + ("PASS" if not bad else f"FAIL ({', '.join(bad)} differ)"))
    print("\n".join(out))
    return 0 if not bad else 1


BUILD_FIELDS = ("features", "dump_schemas", "platform", "generated")


def walk_diff(a, b, path, out):
    if path and path[-1] in BUILD_FIELDS:
        if a != b:
            out.append(("build", "/".join(path)))
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            walk_diff(a.get(k), b.get(k), path + [k], out)
    elif a != b:
        out.append(("api", "/".join(path) or "(the document)"))


def cmd_api(va, vb, plat=""):
    try:
        with open(va) as f:
            a = json.load(f)
        with open(vb) as f:
            b = json.load(f)
    except (OSError, ValueError) as e:
        print(f"E2 step 1 {plat}: FAIL ({e})")
        return 1
    d = []
    walk_diff(a, b, [], d)
    for kind, p in d:
        print(("build field differs (allowed): " if kind == "build" else "DIFF ") + p)
    bad = [p for kind, p in d if kind == "api"]
    print(f"E2 step 1 {plat}: " + ("PASS" if not bad else f"FAIL ({len(bad)} field(s) differ)"))
    return 0 if not bad else 1


TEST_LINE = re.compile(r"^test (\S+) \.\.\. (ok|FAILED|ignored)\b")


def test_results(d):
    """{binary: {test: ok|FAILED|ignored}} and {binary: rc} from D/tests.tsv (NAME RC SECS) and
    D/NAME.log (libtest's output)."""
    res, rcs = {}, {}
    try:
        with open(os.path.join(d, "tests.tsv")) as f:
            rows = [line.rstrip("\n").split("\t") for line in f if line.strip()]
    except OSError:
        return None, None
    for r in rows:
        rcs[r[0]] = r[1] if len(r) > 1 else "?"
        t = {}
        try:
            with open(os.path.join(d, r[0] + ".log"), errors="replace") as f:
                for line in f:
                    m = TEST_LINE.match(line)
                    if m:
                        t[m.group(1)] = m.group(2)
        except OSError:
            pass
        res[r[0]] = t
    return res, rcs


def cmd_tests(native, cross, plat=""):
    """Step 2: holt's tests, cross-built, against the same tests built natively in the same guest
    (holt's tests assume Linux in places, so some fail on these systems whatever builds them): every
    test that passes natively must pass cross-built, every binary must run, and every test the
    native build has the cross build has."""
    n, nrc = test_results(native)
    x, xrc = test_results(cross)
    if not x or not n:
        print(f"E2 step 2 {plat}: FAIL (no test results: {'native' if not n else 'cross'})")
        return 1
    bad, both, tot = [], 0, 0
    for b in sorted(set(n) | set(x)):
        nt, xt = n.get(b), x.get(b)
        if xt is None:
            bad.append(b)
            print(f"DIFF {b}: no cross-built test binary")
            continue
        if nt is None:
            print(f"note {b}: no native one (cross: {sum(v == 'ok' for v in xt.values())} ok)")
            continue
        if not xt and xrc.get(b) != "0":
            bad.append(b)
            print(f"DIFF {b}: the cross-built binary ran no test (exit {xrc.get(b)})")
            continue
        worse = sorted(t for t, v in nt.items() if v == "ok" and xt.get(t) != "ok")
        nf = sorted(t for t, v in nt.items() if v == "FAILED")
        tot += len(nt)
        both += sum(1 for t in nf if xt.get(t) == "FAILED")
        better = sorted(t for t in nf if xt.get(t) == "ok")
        for t in worse:
            print(f"DIFF {b} {t}: ok natively, {xt.get(t, 'missing')} cross-built")
        bad += [f"{b} {t}" for t in worse]
        print(f"{'same' if not worse else 'DIFF'} {b}: native {sum(v == 'ok' for v in nt.values())} ok "
              f"{len(nf)} failed, cross {sum(v == 'ok' for v in xt.values())} ok "
              f"{sum(v == 'FAILED' for v in xt.values())} failed" + (f"; ok cross-built only: {better}" if better else ""))
    print(f"E2 step 2 {plat}: " + ("PASS" if not bad else f"FAIL ({len(bad)}: {', '.join(bad[:8])})")
          + f", {tot} tests; {both} fail natively too (holt's tests that assume Linux)")
    return 0 if not bad else 1


def newest_k(records, guest):
    """RECORDS/GUEST (a run's directory) or RECORDS/*/GUEST: the K of the run's GUEST."""
    for p in (os.path.join(records, guest),):
        if os.path.isdir(p):
            return p
    return None


def report_result(k):
    try:
        with open(os.path.join(k, "report.txt"), errors="replace") as f:
            for line in f:
                if line.startswith("RESULT"):
                    return line.split()[1] if len(line.split()) > 1 else "?"
    except OSError:
        return None
    return "?"


def dump(k, name):
    """K/dumps/NAME.json, or its compressed copy (HT_ZIP: .gz, .zst, .xz) read into a temp file."""
    p = os.path.join(k, "dumps", name + ".json")
    if os.path.isfile(p):
        return p
    for ext, cmd in ((".zst", ["zstd", "-dc"]), (".gz", ["gzip", "-dc"]), (".xz", ["xz", "-dc"])):
        if os.path.isfile(p + ext):
            out = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"e2-{os.getpid()}-{abs(hash(k))}-{name}.json")
            with open(out, "wb") as f:
                subprocess.run(cmd + [p + ext], stdout=f, check=True)
            return out
    return None


def cmd_dumps(plat, rv, rc, guests):
    judge = os.path.join(os.path.dirname(os.path.abspath(__file__)), "judge.py")
    bad = []
    for g in guests:
        ka, kb = newest_k(rv, g), newest_k(rc, g)
        if not ka or not kb:
            print(f"DIFF {g}: no records ({ka or rv + '/' + g} / {kb or rc + '/' + g})")
            bad.append(g)
            continue
        ra, rb = report_result(ka), report_result(kb)
        if ra != "PASS" or rb != "PASS":
            print(f"DIFF {g}: VM run {ra}, cross run {rb}")
            bad.append(g)
            continue
        same = True
        for name in ("src", "restored"):
            a, b = dump(ka, name), dump(kb, name)
            if not a or not b:
                print(f"DIFF {g}: no {name} dump ({a} / {b})")
                same = False
                continue
            r = subprocess.run([sys.executable, judge, a, b], capture_output=True, text=True)
            if r.returncode != 0:
                same = False
                print(f"DIFF {g} {name}: judge.py exit {r.returncode}")
                for line in (r.stdout + r.stderr).splitlines()[:20]:
                    print(f"    {line}")
        if same:
            print(f"same {g}: both PASS, src and restored dumps equal (judge.py)")
        else:
            bad.append(g)
    print(f"E2 step 3 {plat}: " + ("PASS" if not bad else f"FAIL ({', '.join(bad)})") + f", {len(guests)} guests")
    return 0 if not bad else 1


def cmd_fetch(dest, run, guests):
    """Copy each GUEST's report.txt and dumps/ of RUN (else RUN-r2) from the host that ran it."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test2"))
    from t2lib import hosts as hosts_table
    rec = os.environ.get("HT_RECORDS", "/bkoscratch/holttest1d")
    hosts = [(h, hosts_table.ssh(h)) for h in hosts_table.names(default=True, arch="amd64")]
    bad = 0
    for g in guests:
        found = None
        for r in (run, run + "-r2"):
            for h, t in hosts:
                k = f"{rec}/{r}/{g}"
                q = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", t,
                                    f"grep -h '^RESULT' {k}/report.txt 2>/dev/null"], capture_output=True, text=True)
                if q.returncode == 0 and q.stdout.strip():
                    if found is None or q.stdout.split()[1:2] == ["PASS"]:
                        found = (h, t, k, q.stdout.strip())
            if found and found[3].split()[1:2] == ["PASS"]:
                break
        if not found:
            print(f"{g}: no K of {run} on {', '.join(h for h, _ in hosts)}")
            bad += 1
            continue
        h, t, k, res = found
        d = os.path.join(dest, g)
        os.makedirs(d, exist_ok=True)
        subprocess.run(["rsync", "-a", f"{t}:{k}/report.txt", f"{t}:{k}/dumps", d + "/"], check=False)
        print(f"{g}: {h}:{k} ({res})")
    return 1 if bad else 0


def cmd_linkface(path):
    """OpenBSD relinks libc and libcrypto in a random object order at every boot (rc's
    reorder_libs), so two copies of one guest's sysroot differ in those files' bytes but not in
    what a link reads of them: this prints that part, for the sysroot's digest."""
    e = Elf(path)
    tags = {}
    for t, v in e.dynamic():
        tags.setdefault(t, []).append(v)
    stro = e.vaddr_off(tags[DT_STRTAB][0]) if DT_STRTAB in tags else None
    lines = []
    if stro is not None:
        lines += [f"needed {e.cstr(stro + v)}" for v in tags.get(DT_NEEDED, [])]
        lines += [f"soname {e.cstr(stro + v)}" for v in tags.get(14, [])]   # DT_SONAME
    lines += [f"verdef {v}" for v in sorted(version_defs(path))]
    for sh in e.sh:
        if sh[1] != 11:   # SHT_DYNSYM
            continue
        so = e.sh[sh[6]][4]
        for i in range(1, sh[5] // 24):
            name, info, other, shndx, _value, size = struct.unpack_from("<IBBHQQ", e.b, sh[4] + i * 24)
            lines.append(f"sym {e.cstr(so + name)} {info} {other & 3} {'U' if shndx == 0 else 'D'} {size}")
    print("\n".join(sorted(lines)))
    return 0


def cmd_xonly(path):
    """OpenBSD's lld links amd64 code execute-only (its LOAD is PF_X alone, where upstream lld 19,
    which refuses --execute-only on x86-64, writes PF_R|PF_X).  Each R+X LOAD whose sections are all
    code (SHF_EXECINSTR) is made PF_X alone, in place; a segment holding anything else (data the code
    might read) refuses, exit 1, and FILE is left as it was."""
    e = Elf(path)
    phoff, phentsize = struct.unpack_from("<Q", e.b, 32)[0], struct.unpack_from("<H", e.b, 54)[0]
    marks = []
    for i, (t, fl, off, _va, _pa, filesz, _ms, _al) in enumerate(e.ph):
        if t != 1 or fl != 5:   # PT_LOAD, PF_R|PF_X
            continue
        inside = [(n, sh) for n, sh in zip(e.sname, e.sh) if sh[5] and sh[1] != 8 and sh[2] & 2 and off <= sh[4] < off + filesz]
        bad = [n for n, sh in inside if not sh[2] & 4]   # SHF_EXECINSTR
        if bad or not inside:
            print(f"xonly {path}: LOAD {i} holds {bad or 'no sections'}, not code alone: left as it is")
            return 1
        marks.append((i, [n for n, _ in inside]))
    if not marks:
        print(f"xonly {path}: no R+X LOAD")
        return 1
    with open(path, "r+b") as f:
        for i, names in marks:
            f.seek(phoff + i * phentsize + 4)
            f.write(struct.pack("<I", 1))   # PF_X
            print(f"xonly {path}: LOAD {i} ({' '.join(names)}) is now E")
    return 0


def cmd_artifacts(path):
    """cargo's --message-format=json: NAME<TAB>TEST<TAB>PATH for each executable it made (NAME: the
    target's kind and name, lib-bko_holt, test-cli, bin-bko-holt; TEST 1 for a test binary)."""
    seen = set()
    with open(path, errors="replace") as f:
        for line in f:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("reason") != "compiler-artifact" or not m.get("executable"):
                continue
            t = m["target"]
            test = 1 if (m.get("profile") or {}).get("test") else 0
            name = t["kind"][0] + "-" + t["name"] + ("" if test else "-program")
            if name not in seen:
                seen.add(name)
                print(f"{name}\t{test}\t{m['executable']}")
    return 0


def main(argv):
    if len(argv) in (4, 5) and argv[0] == "elf":
        return cmd_elf(*argv[1:])
    if len(argv) in (3, 4) and argv[0] == "api":
        return cmd_api(argv[1], argv[2], *(argv[3:]))
    if len(argv) in (3, 4) and argv[0] == "tests":
        return cmd_tests(argv[1], argv[2], *(argv[3:]))
    if len(argv) == 2 and argv[0] == "linkface":
        return cmd_linkface(argv[1])
    if len(argv) == 2 and argv[0] == "xonly":
        return cmd_xonly(argv[1])
    if len(argv) == 2 and argv[0] == "artifacts":
        return cmd_artifacts(argv[1])
    if len(argv) >= 4 and argv[0] == "fetch":
        return cmd_fetch(argv[1], argv[2], argv[3:])
    if len(argv) >= 5 and argv[0] == "dumps":
        return cmd_dumps(argv[1], argv[2], argv[3], argv[4:])
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
