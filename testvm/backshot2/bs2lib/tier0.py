"""Tier 0 (backshot2.md, "Tiers" and "Oracles"): on a host, no VM.

For each case: its drifted layout (case.replay), load's plan of it onto its
targets with its options (holt/tests/bs2_plan.rs, golden_plans.rs's planner
and invariants), then dry runs in the SOURCE's own tools, chrooted into a
copy of the source guest's root (tools_root): the plan's sfdisk script and
every mke2fs and mkfs.xfs it runs, on sparse scratch files of the right
sizes, read back with the source's sfdisk --json, dumpe2fs and mkfs.xfs -N.
What they make must be what the drifted layout records.

Verdicts:
  PASS     load planned it (exit 0), the invariants hold, the dry runs agree
  FAIL     load refused or broke an invariant, or what its commands make
           differs from the layout while the generator's own command for the
           same values (canon_*) makes it: a bko-holt finding
  INVALID  the source's tools refuse the generator's own command, or make
           something else: the generator broke rule 2 (fix it, never a FAIL)
  HARNESS  the dry runs couldn't run (a tool missing, a chroot problem)

Everything works in WORK (the RAM disk), deleted by the caller on a pass.
"""

import json
import os
import re
import shlex
import subprocess

from . import case as C, dumpx, geometry, sweep

EXT_FIELDS = {"Block count": "blocks", "Inode count": "inodes", "Block size": "block_size", "Inode size": "inode_size",
              "Reserved GDT blocks": "reserved_gdt_blocks", "Inodes per group": "inodes_per_group",
              "Blocks per group": "blocks_per_group", "First block": "first_data_block",
              "Flex block group size": "flex_bg_size", "Reserved block count": "reserved_blocks"}
XFS_FIELDS = ("blocks", "ag_count", "ag_blocks", "log_blocks", "inode_size", "sector_size")
# What --fit-largest may change on the partition it fits (judge.py's FIT_GEOMETRY, and the size).
FIT_CHANGES = {"blocks", "inodes", "inodes_per_group", "reserved_blocks", "reserved_gdt_blocks", "ag_count", "ag_blocks", "log_blocks"}


# ---- the plan --------------------------------------------------------------------

def plan_jobs(cases, work):
    """Writes each case's drifted layout and the planner's jobs file; returns its path."""
    os.makedirs(f"{work}/layouts", exist_ok=True)
    jobs = []
    for name, case, drifted, targets in cases:
        path = f"{work}/layouts/{name}.json"
        with open(path, "w") as f:
            json.dump(drifted, f)
        opts = []
        o = list(case.get("load_options") or [])
        i = 0
        while i < len(o):
            if o[i] == "--snapshot":
                opts += ["--snapshot", "latest"]   # the ID is the backup's; any ID plans the same
                i += 2
                continue
            opts.append(o[i])
            i += 1
        jobs.append(json.dumps({"id": name, "layout": path, "targets": targets, "options": opts}))
    p = f"{work}/jobs.jsonl"
    with open(p, "w") as f:
        f.write("\n".join(jobs) + "\n")
    return p


def run_planner(planner, jobs, out):
    """PLANNER: the built bs2_plan test binary."""
    env = dict(os.environ, BS2_PLAN_JOBS=jobs, BS2_PLAN_OUT=out)
    r = subprocess.run([planner, "bs2_plan_jobs", "--exact", "--test-threads", "1"], env=env, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"the planner failed: {r.stdout[-2000:]}{r.stderr[-2000:]}")
    res = {}
    with open(out) as f:
        for line in f:
            x = json.loads(line)
            res[x["id"]] = x
    return res


def stage_lines(plan, stage):
    """A stage's lines of the plan text, header to the next header."""
    out, on = [], False
    for l in plan.splitlines():
        m = re.match(r"# ---- (\w+) ----$", l)
        if m:
            on = m.group(1) == stage
            continue
        if on:
            out.append(l)
    return out


def sfdisk_scripts(plan):
    """[(target device, script text)] of the partition stage's sfdisk here-documents."""
    out, lines, i = [], stage_lines(plan, "partition"), 0
    while i < len(lines):
        m = re.match(r"sfdisk .* (/dev/\S+) <<'EOF'$", lines[i])
        if m:
            body = []
            i += 1
            while i < len(lines) and lines[i] != "EOF":
                body.append(lines[i])
                i += 1
            out.append((m.group(1), "\n".join(body) + "\n"))
        i += 1
    return out


def format_cmds(plan):
    """The format stage's mke2fs, tune2fs and mkfs.xfs commands, as argv lists."""
    out = []
    for l in stage_lines(plan, "format"):
        if l.startswith("#") or not l.strip():
            continue
        try:
            argv = shlex.split(l)
        except ValueError:
            continue
        if argv and argv[0] in ("mke2fs", "tune2fs", "mkfs.xfs"):
            out.append(argv)
    return out


def uuid_of(argv):
    if argv[0] == "mke2fs" and "-U" in argv:
        return argv[argv.index("-U") + 1]
    if argv[0] == "mkfs.xfs":
        for a in argv:
            m = re.search(r"uuid=([0-9a-f-]{36})", a)
            if m:
                return m.group(1)
    return None


# ---- the dry runs --------------------------------------------------------------------

class Tools:
    """The source's own tools: a copy of its root at ROOT, run by chroot as
    root, with a scratch directory (in RAM) bound at ROOT/bs2-scratch."""

    def __init__(self, root, scratch):
        # A bind of ROOT of this run's own, so two runs on one source never
        # share (or unmount) each other's /dev, /proc and scratch.
        self.tools, self.scratch = root, scratch
        self.root = f"{os.path.dirname(scratch)}/root"
        self.inside = "/bs2-scratch"

    def __enter__(self):
        os.makedirs(self.scratch, exist_ok=True)
        os.makedirs(self.root, exist_ok=True)
        subprocess.run(["mount", "--bind", self.tools, self.root], check=True)
        subprocess.run(["mount", "--make-private", self.root], check=False)
        os.makedirs(f"{self.root}{self.inside}", exist_ok=True)
        subprocess.run(["mount", "--bind", self.scratch, f"{self.root}{self.inside}"], check=True)
        for p in ("dev", "proc"):
            os.makedirs(f"{self.root}/{p}", exist_ok=True)
            subprocess.run(["mount", "--rbind" if p == "dev" else "--bind", f"/{p}", f"{self.root}/{p}"], check=True)
            subprocess.run(["mount", "--make-rslave", f"{self.root}/{p}"], check=False)
        return self

    def __exit__(self, *a):
        subprocess.run(["umount", "-R", self.root], check=False)
        try:
            os.rmdir(self.root)
        except OSError:
            pass

    def run(self, argv, stdin=None, timeout=120):
        r = subprocess.run(["chroot", self.root, "/usr/bin/env", "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL=C"] + argv,
                           input=stdin, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def scratch_file(self, name, size):
        p = f"{self.scratch}/{name}"
        with open(p, "wb") as f:
            f.truncate(size)
        return f"{self.inside}/{name}", p

    def has(self, prog):
        return any(os.path.exists(f"{self.root}{d}/{prog}") for d in ("/sbin", "/usr/sbin", "/bin", "/usr/bin"))

    def run_host_or_mine(self, argv, stdin=None):
        """ARGV in the source's tools when it has them, else the host's (on the scratch file's host path)."""
        if self.has(argv[0]):
            return self.run(argv, stdin)
        argv = [a.replace(self.inside, self.scratch) for a in argv]
        r = subprocess.run(argv, input=stdin, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def drop(self, path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def read_ext(tools, inside):
    """The ext superblock's geometry, read here (Alpine's e2fsprogs has no
    dumpe2fs: it's in e2fsprogs-extra)."""
    import struct
    real = tools.scratch + inside[len(tools.inside):]
    try:
        with open(real, "rb") as f:
            f.seek(1024)
            sb = f.read(1024)
    except OSError as e:
        return None, str(e)
    if len(sb) < 1024 or struct.unpack_from("<H", sb, 56)[0] != 0xEF53:
        return None, "no ext superblock"
    u32 = lambda o: struct.unpack_from("<I", sb, o)[0]
    feat_incompat = u32(96)
    is64 = bool(feat_incompat & 0x80)
    got = {
        "inodes": u32(0), "blocks": u32(4) | ((u32(336) << 32) if is64 else 0),
        "reserved_blocks": u32(8) | ((u32(340) << 32) if is64 else 0), "first_data_block": u32(20),
        "block_size": 1024 << u32(24), "blocks_per_group": u32(32), "inodes_per_group": u32(40),
        "inode_size": struct.unpack_from("<H", sb, 88)[0], "reserved_gdt_blocks": struct.unpack_from("<H", sb, 206)[0],
        "flex_bg_size": 1 << sb[372],
    }
    return got, ""


def read_xfs(out):
    """mkfs.xfs -N's report: the geometry it would make."""
    got = {}
    pat = {"ag_count": r"agcount=(\d+)", "ag_blocks": r"agsize=(\d+)", "inode_size": r"isize=(\d+)", "sector_size": r"sectsz=(\d+)"}
    for k, p in pat.items():
        m = re.search(p, out)
        if m:
            got[k] = int(m.group(1))
    m = re.search(r"^data\s+=\s+bsize=\d+\s+blocks=(\d+)", out, re.M)
    if m:
        got["blocks"] = int(m.group(1))
    m = re.search(r"^log\s+=.*?blocks=(\d+)", out, re.M)
    if m:
        got["log_blocks"] = int(m.group(1))
    return got


def compare(want, got, fields, skip=()):
    return [f"{k}: layout {want.get(k)}, made {got.get(k)}" for k in fields if k in want and k not in skip and want.get(k) != got.get(k)]


def canon_mke2fs(c, inside):
    """The generator's own mke2fs for content C, with -E resize= worked out
    as mke2fs works it out (geometry.mke2fs_resize_for), not as load does."""
    from .geometry import mke2fs_resize_for
    feats = [f for f in c.get("features") or [] if f not in ("needs_recovery", "orphan_present", "mmp")]
    a = ["mke2fs", "-F", "-q", "-t", c.get("type") or "ext4", "-O", "none," + ",".join(feats), "-b", str(c["block_size"]), "-I", str(c["inode_size"]),
         "-N", str(c["inodes"]), "-g", str(c["blocks_per_group"])]
    if c.get("flex_bg_size") and "flex_bg" in feats:
        a += ["-G", str(c["flex_bg_size"])]
    ext = ["lazy_itable_init=1", "lazy_journal_init=1", "nodiscard"]
    if c.get("descriptor_size", 0) > 32:
        ext.append(f"desc_size={c['descriptor_size']}")
    if "resize_inode" in feats and c.get("reserved_gdt_blocks"):
        ext.append(f"resize={mke2fs_resize_for(c)}")
    a += ["-E", ",".join(ext), inside, f"{c['blocks'] * c['block_size'] // 1024}k"]
    return a


def canon_xfs(c, inside):
    on = lambda f: "1" if f in (c.get("features") or []) else "0"
    bs = c["block_size"]
    return ["mkfs.xfs", "-N", "-f", "-m", f"crc={on('crc')},finobt={on('finobt')},rmapbt={on('rmapbt')},reflink={on('reflink')}",
            "-i", f"size={c['inode_size']}", "-b", f"size={bs}", "-s", f"size={c['sector_size']}",
            "-d", f"size={c['blocks'] * bs},agsize={c['ag_blocks'] * bs}", "-l", f"size={c['log_blocks'] * bs}", inside]


def with_lazy(argv):
    a = list(argv)
    extra = "lazy_itable_init=1,lazy_journal_init=1,nodiscard"
    if "-E" in a:
        i = a.index("-E")
        a[i + 1] += "," + extra
    else:
        a[1:1] = ["-E", extra]
    return a


def dry_runs(tools, name, plan, drifted, targets, fitted, shifts=None, fit_blocks=None):
    """[(verdict, why)] from the dry runs; empty: they agree.  FITTED: the
    device names --fit-largest resizes (their sizes are the fit's);
    FIT_BLOCKS, the block counts judge.py's fit gives their filesystems.

    A fitted ext filesystem is expected as resize2fs would leave the
    layout's at its fitted size (geometry.ext4_resize2fs), and the
    generator's own command is run for that: so a load that makes another
    geometry at that size (fail-005) is a FAIL, not judged against the
    layout's unfitted record."""
    out = []
    by_uuid = {}
    for dev, c in dumpx.filesystems(drifted):
        if c.get("uuid"):
            by_uuid[c["uuid"]] = (dev, c)
    # The table.
    for tgt, script in sfdisk_scripts(plan):
        disk_name = next((dk["name"] for dk in dumpx.disks(drifted)), None)
        size = targets.get(disk_name) or dumpx.disk(drifted)["size_bytes"]
        inside, real = tools.scratch_file(f"{name}.disk", size)
        body = re.sub(rf"^{re.escape(tgt)}(\d+) :", lambda m: f"{inside}{m.group(1)} :", script, flags=re.M)
        if not tools.has("sfdisk"):
            body = body.replace(inside, real)
        rc, txt = tools.run_host_or_mine(["sfdisk", "--wipe", "always", inside], stdin=body)
        if rc:
            out.append(("FAIL", f"the source's sfdisk refuses load's table: {txt.strip().splitlines()[-1] if txt.strip() else rc}"))
        else:
            rc, js = tools.run_host_or_mine(["sfdisk", "--json", inside])
            try:
                made = {int(re.search(r"(\d+)$", p["node"]).group(1)): (p["start"], p["size"]) for p in json.loads(js)["partitiontable"].get("partitions", [])}
            except (ValueError, KeyError, AttributeError):
                made = None
            if made is not None:
                dk = dumpx.disk(drifted)
                want = {p["number"]: (p["start_lba"] + (shifts or {}).get(p["name"], 0), p["sectors"]) for p in dk["partitions"] if not p.get("extended")}
                for n, se in want.items():
                    if n in made and made[n] != se and p_name(dk, n) not in fitted:
                        out.append(("FAIL", f"partition {n}: layout start {se[0]} size {se[1]}, load's sfdisk made {made[n]}"))
                if set(want) - set(made):
                    out.append(("FAIL", f"load's table has no partition {sorted(set(want) - set(made))}"))
        tools.drop(real)
    # The filesystems.
    cmds = format_cmds(plan)
    for i, argv in enumerate(cmds):
        if argv[0] == "tune2fs":
            continue
        u = uuid_of(argv)
        if u not in by_uuid:
            continue
        dev, want = by_uuid[u]
        devnode = next((a for a in argv if a.startswith("/dev/")), None)
        skip = FIT_CHANGES if dev["name"] in fitted else set()
        modelled = False
        if argv[0] == "mke2fs" and dev["name"] in fitted and want.get("blocks_per_group"):
            nb = (fit_blocks or {}).get(dev["name"]) or cmd_bytes(argv) // want["block_size"]
            try:
                want = dict(want, **geometry.ext4_resize2fs(want, nb))
                modelled = True
            except geometry.Invalid as e:
                out.append(("INVALID", f"{dev['name']} ext4: resize2fs to {nb} blocks: {e}"))
                continue
        size = max(dumpx.size_of(dev), (want.get("blocks") or 0) * (want.get("block_size") or 0), cmd_bytes(argv)) + (1 << 20)
        inside, real = tools.scratch_file(f"{name}.fs", size)
        mine = [inside if a == devnode else a for a in argv]
        if argv[0] == "mke2fs":
            rc, txt = tools.run(with_lazy(mine))
            got = None
            if rc == 0:
                for t in cmds[i + 1:]:
                    if t[0] == "tune2fs" and devnode in t:
                        tools.run_host_or_mine([inside if a == devnode else a for a in t])
                        break
                got, txt = read_ext(tools, inside)
            diffs = compare(want, got or {}, EXT_FIELDS.values(), skip) if got else [f"refused: {txt.strip()[-200:]}"]
            if diffs:
                crc, ctxt = tools.run(canon_mke2fs(want, inside))
                cgot = read_ext(tools, inside)[0] if crc == 0 else None
                # The resize2fs model is checked whole, against the source's mke2fs.
                cdiffs = compare(want, cgot or {}, [k for k in EXT_FIELDS.values() if k != "reserved_blocks"],
                                 () if modelled else skip) if cgot else [f"refused: {ctxt.strip()[-200:]}"]
                verdict = "INVALID" if cdiffs else "FAIL"
                out.append((verdict, f"{dev['name']} ext4: load's mke2fs: {'; '.join(diffs)}"
                                     + (f"; the generator's own: {'; '.join(cdiffs)}" if cdiffs else "")))
        elif argv[0] == "mkfs.xfs":
            rc, txt = tools.run(["mkfs.xfs", "-N"] + mine[1:])
            got = read_xfs(txt) if rc == 0 else None
            diffs = compare(want, got or {}, XFS_FIELDS, skip) if got else [f"refused: {txt.strip()[-200:]}"]
            if diffs:
                crc, ctxt = tools.run(canon_xfs(want, inside))
                cgot = read_xfs(ctxt) if crc == 0 else None
                cdiffs = compare(want, cgot or {}, XFS_FIELDS, skip) if cgot else [f"refused: {ctxt.strip()[-200:]}"]
                verdict = "INVALID" if cdiffs else "FAIL"
                out.append((verdict, f"{dev['name']} xfs: load's mkfs.xfs: {'; '.join(diffs)}"
                                     + (f"; the generator's own: {'; '.join(cdiffs)}" if cdiffs else "")))
        tools.drop(real)
    return out


def cmd_bytes(argv):
    """The filesystem size a mkfs command asks for, in bytes (0: none given)."""
    if argv[0] == "mke2fs" and re.fullmatch(r"\d+k", argv[-1]):
        return int(argv[-1][:-1]) * 1024
    for a in argv:
        m = re.search(r"(?:^|,)size=(\d+)(?:,|$)", a)
        if m and "agsize" in a:
            return int(m.group(1))
    return 0


def p_name(dk, n):
    p = dumpx.part(dk, n)
    return p["name"] if p else None


def fit_shifts(drifted, targets, case):
    """{partition: sectors} the fit moves the partitions after the largest by (judge.py's plan)."""
    if "--fit-largest" not in (case.get("load_options") or []):
        return {}
    from . import fitcalc
    pl = fitcalc.plan(drifted, targets, sweep.expected(case)["fit"])
    out = {}
    for dd in (pl or {}).get("disks", {}).values():
        if dd.get("amount") and dd.get("name"):
            dk = dumpx.disk(drifted, dd["disk"])
            parts = dk.get("partitions") or []
            for j in dd["moved"]:
                out[parts[j]["name"]] = dd["amount"] // dd["ss"]
    return out


def fitted_devices(drifted, targets, case):
    """The devices --fit-largest resizes: the largest partition and what's on it."""
    if "--fit-largest" not in (case.get("load_options") or []):
        return set()
    from . import fitcalc
    pct = sweep.expected(case)["fit"]
    pl = fitcalc.plan(drifted, targets, pct)
    out = set()
    for dd in (pl or {}).get("disks", {}).values():
        if dd.get("amount") and dd.get("name"):
            dk = dumpx.disk(drifted, dd["disk"])
            part = next(x for x in dk["partitions"] if x["name"] == dd["name"])
            out.add(part["name"])
            out |= {x["name"] for x in fitcalc._down(drifted, part)}
    return out


def fit_blocks(drifted, targets, case):
    """{device name: blocks} the fit gives each filesystem it remakes (fitcalc.fs_blocks)."""
    if "--fit-largest" not in (case.get("load_options") or []):
        return {}
    from . import fitcalc
    return fitcalc.fs_blocks(drifted, targets, sweep.expected(case)["fit"])


def judge_one(name, case, drifted, targets, res, tools, caps):
    """A case's tier 0 verdict and its reasons."""
    if "error" in res:
        return "HARNESS", [res["error"]]
    if res["code"] != 0:
        why = [l[6:] for l in res["out"].splitlines() if l.startswith("#   - ")] or [res["err"].strip()[-300:]]
        if any("isn't built yet" in w for w in why) and "--new-uuids" in (case.get("load_options") or []):
            return "UNSUPPORTED", why
        return "FAIL", [f"load plan exit {res['code']}: " + "; ".join(why)[:600]]
    if res["violations"]:
        return "FAIL", [f"invariant: {v}" for v in res["violations"]]
    if tools is None:
        return "PASS", ["planned; no dry runs (no source tools here)"]
    try:
        found = dry_runs(tools, name, res["out"], drifted, targets, fitted_devices(drifted, targets, case),
                         fit_shifts(drifted, targets, case), fit_blocks(drifted, targets, case))
    except (OSError, subprocess.SubprocessError) as e:
        return "HARNESS", [f"dry runs: {e}"]
    for v in ("INVALID", "FAIL"):
        if any(x[0] == v for x in found):
            return v, [x[1] for x in found if x[0] == v]
    return "PASS", [f"planned, {'; '.join(res.get('checked') or [])}; dry runs agree"]


def run(source_dump, cases, work, planner, tools_root=None, caps=None):
    """[(name, verdict, reasons)] for CASES ([(name, case)]) of one source."""
    prepared = []
    for name, case in cases:
        drifted = C.replay(case, source_dump)
        targets = C.targets(case, drifted)
        prepared.append((name, case, drifted, targets))
    jobs = plan_jobs(prepared, work)
    res = run_planner(planner, jobs, f"{work}/plans.jsonl")
    out = []
    if tools_root:
        with Tools(tools_root, f"{work}/scratch") as t:
            for name, case, drifted, targets in prepared:
                v, why = judge_one(name, case, drifted, targets, res[name], t, caps or {})
                out.append((name, v, why))
    else:
        for name, case, drifted, targets in prepared:
            v, why = judge_one(name, case, drifted, targets, res[name], None, caps or {})
            out.append((name, v, why))
    return out, res
