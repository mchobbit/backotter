"""HoltTest2's runner (contract 2.3; rulings R1, R7, R10, R14, R15, R16, R17).

run(ctx, plan) executes a plan that plan.py made (the family's scope is decided
there, never here): ctx.run is run.py's RunState (the run id, its directories,
the copy on each host, write_cells(), replan(), the StopEngine).  In order:

  1. bko-holt (step 5a): CODE (keys.holt_code: sha256 over holt/** less target/,
     tests/ and *.md) names HOST_ROOT/holt-bin/CODE/, laid out as
     /bkoscratch/holttest1d/bin is (bko-holt, PLAT/bko-holt).  Each platform the
     due cells need that no test host has (any of them, not only --hosts: builds
     aren't reproducible, R14) is built on a build host of --hosts (the host
     table's role build, big or small since img-speed.md F8, 2026-11-27: chopin,
     cinqcent, copland, cage, czerny; else one holding the run's copy), as m,
     detached:
         build-holt.sh --reserved RUN PLAT    (from the copy; HOLT_SRC=COPY/holt)
     which reserves HOLT_RESERVE GiB of scratch (3, else 1.1 x the last builds'
     peak + 0.25), keeps cargo's target/ and the build guest's -snapshot file in
     /mnt/bko-ram/t2b-RUN-PLAT (RAM first, the SSD for overflow: by the host's
     scratch_mode, /bkoscratch/t2b-RUN-PLAT under cmp-run.sh --reserve-ssd when
     the RAM disk has no room for it now, which a small host's often hasn't
     beside the run's own cells), takes a port of 2600-2689 under ht1-queue.sh's
     locks, and is tagged ht1:RUN:build-PLAT in the wear log (R16).  A binary is
     copied to the hosts that lack it (scp, host to host; through the Mac if that
     fails) and checked by its sha256, never written over one that is there; then
     each host's CODE/SHA256SUMS, CODE/BUILD-INFO and CODE/build-PLAT.json are
     written for the builds it holds: on every host of --hosts, and best effort
     on the other default hosts (ctx.options holt_ship or T2_HOLT_SHIP "known":
     on every known host, the small ones too).  ctx.options["holt_bin"]
     (--holt-bin DIR) tests a ready set instead.  A failed build other than linux's
     loses its platform alone (holt.json lost_plats): its cells are reported skipped,
     saying why, the run fails, and the other platforms' cells run; a failed linux
     build, a build still running after 4 h, or no cell left stops the run (stop-family,
     no guest starts).  One run at a time builds and ships a
     CODE (MAC_ROOT/runs/.lock-holt-bin-CODE16, waited for): holt runs over other cells
     run side by side (run.CellLocks).
  0. +img cells (ImgTest2's panel) run on the pinned rescue image's own binaries: no CODE
     build for them, and no fast gate when they are all there is.  The pin of their
     platforms (`t2-host tool img pin P...`) is made on each host before the image sums.
  1a. Image sums: a due cell's guest with no generation, or one-time rescuer copy
     with no .sha256, gets its sum cached by `t2-host imgsum` on each host that may
     run it (contract 1.4), and ctx.facts get it, so the re-plan keys it as the
     row will (an img:unknown input makes every row invalid).
  2. The fast holt gate (gate.run_layers(ctx, ["holt"], "fast")): a failure
     starts no guest.  Then the plan is made again (R15: the plan diff now known)
     and cells it no longer selects are dropped, those it adds added (with their
     image sums and platforms: step 1a and 1 again for them).
  3. cells.json on every host (RunState.write_cells, the rekeyed plan: R1).
  4. The queue: the due cells' GVs as todo lines on the queue host's (queue_host: the
     run's default host first, then hosts.tie_key's order, kept in holt.json: F15)
     /bkoscratch/holttest1d/queue/RUN/todo, canaries first, then the guests whose
     golden plan changed, then the rest, each part biggest first
     (ht1-queue.sh --order), its +img lines among themselves longest first
     (img_longest_first); a runner on every host from the copy, as m,
     detached (BASEPORT 2600, MAXJOBS the host's holt_jobs in testvm/hosts.json, 3 by
     default; the others with QHOST the queue host),
     ONLY_ELSEWHERE from the plan's per-host keys (pins, R17's UEFI rescuer, the hosts
     whose ceiling a cell's reservation fits: a cell no host fits isn't due at all), with
         HT_HOLT_BIN=HOST_ROOT/holt-bin/CODE HT_RAM=1 T2_RUN=RUN
         T2_CELLS=HOST_ROOT/runs/RUN/cells.json T2_HOST_ROOT=HOST_ROOT PYTHONDONTWRITEBYTECODE=1
     (and HT_WORK, HT_RECORDS when the Mac's environment has them: tests; DISK_OVER=1 on a
     host whose holt_disk_mode is on: a line over its RAM-disk ceiling runs on /bkoscratch).
  5. The wait: each host's queue/RUN/done, the runners' PIDs and logs.  A runner
     has ended when it reads as gone and its runner-HOST.log says queue done,
     stopped or aborted, or it reads as gone twice; an ssh that fails is never
     "gone", nor an unreadable ledger "no row".  Every new done row's ledger row
     feeds the stop rules (stop-os OS takes that OS's lines out of the todo, an
     offline guest being an OS of its own; stop-starts, stop-family and stop-run
     touch /bkoscratch/holttest1d/queue/RUN/stop on every host, the file ht1-queue.sh
     reads; not HOST_ROOT/runs/RUN/stop).  Ctrl-C, TERM or HUP on the Mac's t2 (in the
     main thread: a composite's child has its guards) writes that stop too, its line
     saying why, for every queue under way, then ends the t2 (exit 130): the runners
     start nothing more, their running jobs finish, and `t2 resume RUN` collects the
     rows (holt.json keeps the stop: no rerun).  Each poll also feeds each host's swap-out to the
     swap rule, which holds that host alone (run.py's HostHolds: queue/RUN/max-HOST 0 there,
     so its runner starts nothing new and the other runners take the todo's lines, a line
     only it may run waiting; max-HOST back as it was when the hold ends, or when the todo
     is empty), and this run's bytes (jobs.log, tag ht1:RUN...: R22) to the write stop rule; a cell with
     a write allowance (its spec entry's write_allow, write_allow_bytes) has its own
     bytes per attempt (tags ht1:RUN:GV, ht1:RUN-r2:GV), up to the allowance, excused
     (allowed_bytes), and so are the bytes a failed run's state wrote as it moved from RAM
     to K (jobs.log's keep=), the report still giving the true total.  A done row with no ledger row gets
     one: built from K by `t2-host row holt` when K holds the run's records (the
     hook failed), else rows_holt.backfill_row's LOST or HARNESS (R7), appended
     on its host; the row fed on is the one read back from the ledger.
     A host that stops answering or reboots is held the same way (run.py's HostWatch,
     fed by Remote.seen each poll): its HARNESS and LOST rows are host-lost (judge_lost),
     counted by no stop rule; its runner, gone with the reboot, is started again once
     when it is back and the todo still has lines (restart_runner), and the jobs the reboot
     killed (their lines in todo.taken with no done row: reboot_killed) are host-lost too,
     rerun once in RUN-r2.
  6. Rerun once: GVs whose row is a FAIL (contract 1.9; a HARNESS or LOST result
     says nothing of the cell, but a host-lost one, rerun_why host-lost) go into queue RUN-r2 with cells-r2.json (attempt 2,
     rerun_of the first row), T2_RUN=RUN, unless stop-run, stop-family or
     stop-starts fired, queue/RUN/stop or abort is there, or stop-os stopped the
     cell's OS.  So does a PASS that doesn't count because its run made the
     one-time rescuer copy the plan keyed missing (extra.rescuer_made): the copy
     goes into ctx.facts, is summed (1a) and the plan made again first, and its
     cells-r2.json entry says rerun_why rescuer-made, under which a pass is a PASS.
     The copy is the host's for good (ht1-bsd.sh makes it once, under a lock), so the
     other cells that boot it on that host (omnios58's copy: every illumos cell) are
     keyed again as soon as its row lands (rekey_made): the copy summed, the plan made
     again, and their cells.json entries there get the planned key at its sum, if
     their rows haven't landed yet and the copy is all the key changes by.  One whose
     row landed first (keyed missing or img:unknown at a copy a job on its host made
     in this queue) is rerun as the maker is.
  7. The result (contract 1.9's RunResult) for run.py's report.  It fails (exit 1)
     on a stop, a cell that isn't a PASS, a due cell no runner ran, a job with no
     row, or a PASS whose row doesn't count (valid false).

resume(ctx, plan) picks a run up from MAC_ROOT/runs/RUN/holt.json (R10):
finished builds and queues aren't redone, live runners are waited on again.
"""

import json
import math
import os
import re
import shlex
import signal
import threading
import time

from . import holtbuild
from . import hosts as hosts_table
from . import keys, utc_now
from .rows_holt import backfill_row, cell_of, gv_of

_STAMP = re.compile(r"t2[a-z]-(\d{8}T\d{4}-[0-9a-f]{4})(?:-img)?(?:-r\d)?\Z")   # (stic.py's: a run id's stamp)

QUEUE = "/bkoscratch/holttest1d/queue"
HOLT_L = "/bkoscratch/holttest1d"
BASEPORT, MAXJOBS = 2600, 3     # MAXJOBS: a host's holt_jobs when the host table has none


def max_jobs(host):
    """ht1-queue.sh's MAXJOBS for HOST's runner: its holt_jobs in the host table (its row's,
    else its class's, else MAXJOBS's 3)."""
    return int(hosts_table.limit(host, "holt_jobs"))


def disk_mode(host):
    """Whether HOST's holt_disk_mode is on in the host table (ht1-queue.sh's DISK_OVER)."""
    return hosts_table.limit(host, "holt_disk_mode") is True
POLL_S = 20
BUILD_TIMEOUT_S = 4 * 3600
# img-speed.md I10, I11 (§8.1, §8.3): the platforms whose VM build gets a cross check build beside
# it (holt-cross.sh: tier 2, rustup's pinned 1.94.1; openbsd and dragonfly tier 3, the same
# 1.94.1 building std from its rust-src, D10 as built).  A check build is never tested by a cell nor shipped: it lands in
# CODE/cross/PLAT/ on its build host alone, and E2 (holt-e2.sh) runs on it privately.
CROSS_PLATS = ("freebsd", "netbsd", "illumos", "openbsd", "dragonfly")
# GiB of RAM disk a cross check build reserves: a tier-3 build's target/ holds std's build too.
CROSS_RESERVE = {"openbsd": 3, "dragonfly": 3}
CROSS_TIMEOUT_S = 3600
QUEUE_TIMEOUT_S = 16 * 3600
DEFAULT_RESERVE_GIB = 3.0
STOPPING = ("stop-starts", "stop-family", "stop-run")


class NoCopy(Exception):
    """No private copy on a host to run from."""


# --- cells, platforms, todo lines -------------------------------------------------------------

def todo_line(cell):
    """ht1-queue.sh's todo line of holt:GUEST[+VARIANT]."""
    return cell[5:] if cell.startswith("holt:") else cell


def base_guest(cell):
    return todo_line(cell).partition("+")[0]


def is_img(cell):
    """A +img cell: the rescue image rebuilds, with its own binaries."""
    return cell.endswith("+img")


def spec_of(ctx):
    sf = ctx.specs.get("holt") if getattr(ctx, "specs", None) is not None else None
    return sf


def entry_of(sf, cell):
    if sf is None:
        return {}
    name = todo_line(cell)
    g, _, v = name.partition("+")
    try:
        return sf.entry(g, v or None)
    except KeyError:
        return {}


def plat_of(sf, cell):
    """The platform of the cell's bko-holt: its spec entry's plat (linux for the Linux
    and offline kinds)."""
    e = entry_of(sf, cell)
    return e.get("plat") or "linux"


def os_of(sf, cell):
    """The stop rules' OS of a cell: its bko-holt platform, but an offline guest's own
    name (redox and sculpt2610 run on linux's bko-holt, but their FAILs say nothing
    of Linux guests: two of them must not stop every Linux guest)."""
    e = entry_of(sf, cell)
    if e.get("kind") == "offline":
        return base_guest(cell)
    return e.get("plat") or "linux"


KIND_DRIVER = {"linux": "ht1.sh", "bsd": "ht1-bsd.sh", "offline": "ht1-offline.sh"}


def needed_platforms(sf, cells):
    return sorted({plat_of(sf, c) for c in cells})


def write_allow_bytes(sf, cell):
    """The bytes of CELL's own writes a run may leave out of the write stop rule (RunGuards):
    its spec entry's write_allow ({"gb": N, "measured": "..."} or N, GB of 1e9), or 0.
    Only a plain cell (holt:GUEST) has one: a variant's (+img in RAM, bigger) isn't the
    measured run."""
    if "+" in todo_line(cell):
        return 0
    w = entry_of(sf, cell).get("write_allow")
    gb = w.get("gb") if isinstance(w, dict) else w
    try:
        gb = float(gb)
    except (TypeError, ValueError):
        return 0
    return int(gb * 1e9) if gb > 0 and math.isfinite(gb) else 0


_RETRY_TAG = re.compile(r"(-r\d+):(.*)\Z", re.S)


def allowed_bytes(by_tag, prefix, allow):
    """{cell: bytes} the write allowances excuse: BY_TAG is this run's jobs.log sums by tag
    ({tag: bytes}, Remote.tag_bytes by_tag), PREFIX its tag (ht1:RUN), ALLOW {gv: (cell,
    bytes)}.  Each attempt of a cell (PREFIX:GV[:...], PREFIX-rN:GV[:...]) is a run of
    its own: its bytes count up to the allowance, the rest is the stop rule's."""
    per = {}
    for tag, n in (by_tag or {}).items():
        if not isinstance(n, (int, float)) or n <= 0:
            continue
        if tag.startswith(prefix + ":"):
            q, rest = prefix, tag[len(prefix) + 1:]
        else:
            m = _RETRY_TAG.match(tag[len(prefix):]) if tag.startswith(prefix) else None
            if not m:
                continue
            q, rest = prefix + m.group(1), m.group(2)
        gv = rest.split(":", 1)[0]
        if gv in allow:
            per[(q, gv)] = per.get((q, gv), 0) + n
    out = {}
    for (q, gv), n in sorted(per.items()):
        cell, cap = allow[gv]
        out[cell] = out.get(cell, 0) + min(n, cap)
    return {c: v for c, v in out.items() if v > 0}


def holt_rel(plat):
    """The binary's path in a set: bko-holt (linux) or PLAT/bko-holt."""
    return "bko-holt" if plat == "linux" else f"{plat}/bko-holt"


def holt_build_hosts(hosts, table=None):
    """The hosts of HOSTS, in their order, a bko-holt build may run on: the host table's build
    hosts (role build), big or small.  Until img-speed.md F8 (2026-11-27) only the table's first
    class (chopin, cinqcent): a small M900's RAM disk had no room for a build beside the run's
    cells; build-holt.sh --reserved now puts its scratch on /bkoscratch when the RAM disk has no
    room for it (RAM first, the SSD for overflow, by the host's scratch_mode).  Runner.build_hosts'
    rule, and t2 batch's (batchfile.refusals, the scheduler's feasible sets): one predicate, so
    the batch never places a HoltTest2 where its stop rule would end it.  Raises when the table
    doesn't load."""
    role = hosts_table.with_role("build", table)
    return [h for h in hosts if h in role]


def order_groups(pcs):
    """The due cells in three parts: the canaries (canary:plumbing, canary:host), the guests
    whose golden plan changed (holt:plan-diff, holt:variants), the rest.  ht1-queue.sh
    --order then puts each part biggest first."""
    first, diff, rest = [], [], []
    for pc in pcs:
        rule = pc.rule or ""
        if rule.startswith("canary:"):
            first.append(pc.cell)
        elif rule in ("holt:plan-diff", "holt:variants") or any("holt:plan-diff" in r for r in pc.reasons or ()):
            diff.append(pc.cell)
        else:
            rest.append(pc.cell)
    return [first, diff, rest]


def img_longest_first(lines, walls):
    """LINES (ht1-queue.sh's order: biggest D first) with their +img lines, among themselves,
    longest estimated wall first (WALLS: {line: wall_s}), in the places the +img lines held:
    the other lines keep theirs.  A +img line has a small D and runs in a RAM slot only, so
    the D order put the panel's longest line (dragonfly6+img, 16 gm, a D of 3) last, and a
    panel spread over six hosts ended a line's length after the rest (img-speed.md §10, I9)."""
    at = [i for i, x in enumerate(lines) if x.endswith("+img")]
    img = sorted((lines[i] for i in at), key=lambda x: -(walls.get(x) or 0))   # (stable: ties keep the D order)
    out = list(lines)
    for i, x in zip(at, img):
        out[i] = x
    return out


def only_elsewhere(pcs, hosts, pins=None):
    """{host: [names]}: what a host's runner leaves in the todo for the others
    (ht1-queue.sh's ONLY_ELSEWHERE, which skips a line by its name or its base guest).  From
    classes.json's holt.pins (PINS: {guest: host}) and the plan's per-host keys: a cell has
    keys only for the hosts that may run it (the pins again, R17's UEFI rescuer, the hosts
    whose ceiling its ram-size.sh reservation fits); one with none may run anywhere its pin
    allows.  A base guest none of whose cells may run on the host is named (its variants go
    with it); else each of its cells that may not, as GUEST+VARIANT."""
    by_guest = {}
    for pc in pcs:
        by_guest.setdefault(base_guest(pc.cell), []).append((todo_line(pc.cell), dict(getattr(pc, "keys", None) or {})))
    pins = pins or {}
    out = {}
    for h in hosts:
        names = set()
        for g, items in by_guest.items():
            if (g in pins and pins[g] != h) or all(ks and h not in ks for _, ks in items):
                names.add(g)
            else:
                names |= {n for n, ks in items if ks and h not in ks}
        out[h] = sorted(names)
    return out


# --- bko-holt's builds ---------------------------------------------------------------------------

def reserve_gib(peaks_kib):
    """R16: 1.1 x the largest of the last builds' peaks, plus 0.25 GiB, rounded up to 0.1."""
    ps = [p for p in peaks_kib if isinstance(p, (int, float)) and p > 0]
    if not ps:
        return DEFAULT_RESERVE_GIB
    g = 1.1 * max(ps) / 1048576.0 + 0.25
    return max(1.0, math.ceil(g * 10) / 10.0)


def build_info_text(code, commit, dirty, infos, run):
    """CODE/BUILD-INFO: a first line naming the code, then one line per platform built
    (facts_host.py's build_info_sums() reads its sha256s, R26)."""
    c = (commit or "?")[:7] + (".dirty" if dirty else "")
    out = [f"bko-holt code {code} (holt {c}), HoltTest2's builds (testvm/test2/t2lib/holt.py)"]
    for plat in sorted(infos):
        i = infos[plat]
        out.append(f"sha256 {i['sha256']} {holt_rel(plat)} rustc {' '.join(str(i.get('rustc') or '?').split())}"
                   f" host {i.get('host') or '?'} time {i.get('time') or '?'} run {i.get('run') or run}")
    return "\n".join(out) + "\n"


def sha256sums_text(infos):
    return "".join(f"{infos[p]['sha256']} *{holt_rel(p)}\n" for p in sorted(infos))


# --- the hosts ----------------------------------------------------------------------------------

class Remote:
    """What holt.run asks of the hosts (through t2lib.hostrun; tests give a fake)."""

    def __init__(self):
        from . import hostrun
        self.h = hostrun

    def sh(self, host, cmd, input=None, timeout=60):
        r = self.h.ssh(host, cmd, input=input, timeout=timeout)
        return r.rc, r.text, r.errtext

    def read(self, host, path):
        rc, out, _ = self.sh(host, f"cat {self.h.rq(path)} 2>/dev/null", timeout=60)
        return out if rc == 0 else None

    def write(self, host, path, text):
        q = self.h.rq(path)
        d = self.h.rq(os.path.dirname(path))
        rc, _, err = self.sh(host, f"mkdir -p {d} && cat > {q}.tmp && mv -f {q}.tmp {q}", input=text.encode(),
                             timeout=60)
        if rc != 0:
            raise OSError(f"{host}: can't write {path}: {err.strip()[-200:]}")

    def exists(self, host, path):
        """True or False; None when the host couldn't be asked (falsy, but ensure_holt
        never takes it for 'not built': it would build again over a binary that is there)."""
        rc, out, _ = self.sh(host, f"if test -e {self.h.rq(path)}; then echo yes; else echo no; fi", timeout=30)
        last = (out or "").strip().splitlines()[-1:] if rc == 0 else []
        return {"yes": True, "no": False}.get(last[0] if last else None)

    def touch(self, host, path):
        self.sh(host, f"mkdir -p {self.h.rq(os.path.dirname(path))} && touch {self.h.rq(path)}", timeout=30)

    def sha256(self, host, path):
        rc, out, _ = self.sh(host, f"sha256sum {self.h.rq(path)} 2>/dev/null", timeout=120)
        f = out.split()
        return f[0] if rc == 0 and f and re.fullmatch(r"[0-9a-f]{64}", f[0]) else None

    def start(self, host, cmd, log, pidfile):
        self.sh(host, f"mkdir -p {self.h.rq(os.path.dirname(log))} {self.h.rq(os.path.dirname(pidfile))}", timeout=30)
        return self.h.detached(host, cmd, log, pidfile)

    def alive(self, host, pid, needle):
        """True while PID runs on HOST with NEEDLE in its command line, False once it
        doesn't, None when the host couldn't be asked (ssh failed or timed out: a
        Mac waking from sleep must not read as every runner gone)."""
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if host == self.h.LOCAL:
            return self.h.alive(host, pid, needle)
        rc, out, _ = self.sh(host, f"if test -r /proc/{pid}/cmdline && tr '\\0' ' ' < /proc/{pid}/cmdline"
                                   f" | grep -qF -- {shlex.quote(needle)}; then echo alive; else echo gone; fi",
                             timeout=30)
        last = (out or "").strip().splitlines()[-1:] if rc == 0 else []
        return {"alive": True, "gone": False}.get(last[0] if last else None)

    def glob_read(self, host, pattern):
        """{path: text} of the small files matching PATTERN (a shell glob; ~/ allowed)."""
        p = pattern.replace("~/", "$HOME/", 1) if pattern.startswith("~/") else pattern
        rc, out, _ = self.sh(host, f"for f in {p}; do [ -f \"$f\" ] && printf '%s\\t%s\\n' \"$f\" \"$(head -c 4096 \"$f\" | tr '\\n\\t' '  ')\"; done; true",
                             timeout=60)
        res = {}
        for line in out.splitlines():
            path, _, text = line.partition("\t")
            if path:
                res[path] = text
        return res

    def copy(self, src_host, src, dst_host, dst):
        """SRC on SRC_HOST to DST on DST_HOST (DST.part first): scp host to host, else
        through this machine.  True once DST is there."""
        part = dst + ".part"
        self.sh(dst_host, f"mkdir -p {self.h.rq(os.path.dirname(dst))}", timeout=30)
        tgt = f"{self.h.dest(dst_host)}:{dst.replace('~/', '', 1) if dst.startswith('~/') else dst}.part"
        rc, _, _ = self.sh(src_host, f"scp -q -o BatchMode=yes -o ConnectTimeout=8 {self.h.rq(src)} {shlex.quote(tgt)}",
                           timeout=600)
        if rc != 0:
            r = self.h.ssh(src_host, f"cat {self.h.rq(src)}", timeout=600)
            if r.rc != 0:
                return False
            rc2, _, _ = self.sh(dst_host, f"cat > {self.h.rq(part)}", input=r.out, timeout=600)
            if rc2 != 0:
                return False
        return self.sh(dst_host, f"chmod 755 {self.h.rq(part)} && mv -f {self.h.rq(part)} {self.h.rq(dst)}",
                       timeout=30)[0] == 0

    def promote(self, host, src, dst):
        """SRC to DST on HOST (img-speed.md I12: a cross build into CODE/PLAT/), by DST.part and
        a rename that never writes over a DST that is there.  True once DST is SRC's bytes."""
        q, d, part = self.h.rq(src), self.h.rq(dst), self.h.rq(dst + ".part")
        rc, _, _ = self.sh(host, f"test ! -e {d} && mkdir -p {self.h.rq(os.path.dirname(dst))} && cp {q} {part} && "
                                 f"chmod 755 {part} && cmp -s {q} {part} && mv -n {part} {d} && cmp -s {q} {d}; "
                                 f"s=$?; rm -f {part}; exit $s", timeout=120)
        return rc == 0

    def retire(self, host, hd, rel, jrel):
        """HD/REL (a bko-holt) and HD/JREL (its build json) to HD/retired/SHA12/ on HOST, and
        REL's lines out of HD's SHA256SUMS and BUILD-INFO (img-speed.md I12: a build of another
        kind than the run ships).  A copy already there with the same bytes is kept and REL
        removed.  The directory it went to, or None."""
        sh = ("set -e; cd {hd}; s=$(sha256sum {rel} | cut -c1-12); d=retired/$s; mkdir -p \"$d/$(dirname {rel})\"; "
              "t=\"$d/{rel}\"; if [ -e \"$t\" ]; then cmp -s {rel} \"$t\"; rm -f {rel}; else mv -n {rel} \"$t\"; "
              "test ! -e {rel}; fi; if [ -e {jrel} ]; then mkdir -p \"$d/$(dirname {jrel})\"; "
              "mv -f {jrel} \"$d/{jrel}\"; fi; "
              "for f in SHA256SUMS BUILD-INFO; do [ -f $f ] || continue; "
              "awk -v f={rel} '{{n = ($1 == \"sha256\") ? $3 : $2; sub(/^\\*/, \"\", n)}} n != f' $f > $f.tmp.$$; "
              "mv -f $f.tmp.$$ $f; done; echo \"$PWD/$d\"").format(
            hd=self.h.rq(hd), rel=self.h.rq(rel), jrel=self.h.rq(jrel))
        rc, out, _ = self.sh(host, sh, timeout=120)
        last = (out or "").strip().splitlines()[-1:] if rc == 0 else []
        return last[0] if last else None

    def order(self, host, script, lines):
        if not lines:
            return []
        rc, out, err = self.sh(host, " ".join([self.h.rq(script), "--order"] + [shlex.quote(x) for x in lines]),
                               timeout=120)
        got = [x.strip() for x in out.splitlines() if x.strip()]
        if rc != 0 or sorted(got) != sorted(lines):
            return list(lines)   # (the plan's order: ht1-queue.sh's own refusal shows at its start)
        return got

    def ledger_rows(self, host, ledger, needle):
        """The host's ledger rows whose line holds NEEDLE (the run id); [] when it has
        none (or no ledger yet), None when it couldn't be read (an ssh that failed is
        never 'no row': holt.run would write a HARNESS row over a real one)."""
        q = self.h.rq(ledger)
        rc, out, _ = self.sh(host, f"[ -e {q} ] || exit 0; grep -F -- {shlex.quote(needle)} {q}; [ $? -le 1 ]",
                             timeout=60)
        if rc != 0:
            return None
        rows = []
        for line in out.splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    def append_rows(self, host, tool, rows, env):
        """t2-host append on HOST (rows as JSON on stdin): the ids it printed."""
        envs = " ".join(f"{k}={self.h.rq(v)}" for k, v in sorted(env.items()))
        rc, out, err = self.sh(host, f"env {envs} python3 -B {self.h.rq(tool)} append",
                               input=json.dumps(rows).encode(), timeout=120)
        if rc != 0:
            raise OSError(f"{host}: t2-host append: exit {rc}: {err.strip()[-200:]}")
        return [x.strip() for x in out.splitlines() if x.strip()]

    def row_from_k(self, host, tool, args, env):
        """t2-host row holt ARGS on HOST, as ht_end's hook runs it (for a job whose hook
        left no row, its K there): the ids it printed, or None when it failed."""
        envs = " ".join(f"{k}={self.h.rq(v)}" for k, v in sorted(env.items()))
        rc, out, _ = self.sh(host, f"env {envs} timeout -k 5 120 python3 -B {self.h.rq(tool)} row holt "
                                   + " ".join(shlex.quote(a) for a in args) + " </dev/null", timeout=180)
        ids = [x.strip() for x in out.splitlines() if x.strip()]
        return ids if rc == 0 and ids else None

    def imgsum(self, host, tool, paths, env):
        """t2-host imgsum PATHS on HOST (it hashes the disk images not in its cache, and
        caches them in HOST_ROOT/cache/imgsum.json): {path: sha256 or None}, or None."""
        envs = " ".join(f"{k}={self.h.rq(v)}" for k, v in sorted(env.items()))
        rc, out, _ = self.sh(host, f"env {envs} python3 -B {self.h.rq(tool)} imgsum "
                                   + " ".join(self.h.rq(p) for p in paths) + " </dev/null", timeout=3600)
        try:
            got = json.loads((out or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None
        return got if isinstance(got, dict) else None

    def take_lines(self, host, todo, guests, why):
        """Under the todo's lock, the lines of GUESTS (their base names) move from todo to
        todo.stopped (stop-os).  Returns the lines taken."""
        script = r'''
import fcntl, os, sys
todo, why = sys.argv[1], sys.argv[2]
guests = set(sys.argv[3:])
with open(todo + ".lock", "a") as lk:
    fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        lines = open(todo).read().splitlines()
    except OSError:
        lines = []
    keep, took = [], []
    for l in lines:
        s = l.split("#", 1)[0].strip()
        g = s.split("=")[0].split("%")[0].split("+")[0]
        (took if s and g in guests else keep).append(l)
    if took:
        with open(todo + ".new", "w") as f:
            f.write("".join(x + "\n" for x in keep))
        os.replace(todo + ".new", todo)
        with open(todo + ".stopped", "a") as f:
            f.write("".join(f"{x}  # {why}\n" for x in took))
    print("\n".join(took))
'''
        r = self.h.ssh(host, script=script, args=[todo, why] + sorted(guests), timeout=90)
        return [x for x in r.text.splitlines() if x.strip()] if r.rc == 0 else []

    def pin_images(self, host, tool, plats, env):
        """t2-host tool img pin PLATS on HOST: {plat: result} (tool_img.py's), or None when it
        couldn't be asked."""
        envs = " ".join(f"{k}={self.h.rq(v)}" for k, v in sorted(env.items()))
        rc, out, _ = self.sh(host, f"env {envs} python3 -B {self.h.rq(tool)} tool img pin "
                                   + " ".join(shlex.quote(p) for p in plats) + " </dev/null", timeout=1800)
        try:
            got = json.loads((out or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None
        return got if rc == 0 and isinstance(got, dict) else None

    def seen(self, host):
        """A look at HOST for the host rule (run.HostWatch): None when it didn't answer, else
        {boot, up_s} (hostjob.host_state's)."""
        from . import hostjob
        return hostjob.host_seen(self, host)

    def pswpout(self, host):
        rc, out, _ = self.sh(host, "awk '$1 == \"pswpout\" { print $2 }' /proc/vmstat", timeout=30)
        try:
            return int(out.strip()) * 4096 if rc == 0 else None
        except ValueError:
            return None

    def tag_bytes(self, host, prefix, since, by_tag=False, kept=False):
        """Bytes this run's jobs wrote to physical disks (jobs.log lines whose tag= starts
        with PREFIX and that ended at or after SINCE, epoch), R22: never the whole disk's.
        BY_TAG: {tag: bytes} instead (the write allowances' share, allowed_bytes).  KEPT:
        (that, the same of the part a failed run's state wrote as it was kept: the lines'
        keep=, iolog.sh --keep-since), which the write stop rule doesn't count; and every
        byte of a line whose scratch was on the SSD (its scratch=ssd: cmp-run.sh
        --reserve-ssd, RAM first and the SSD for overflow), which it doesn't count either
        (wear.Job.kept_disks says the same of the report's figures: test_kept_state.py
        holds the three readers to one answer)."""
        script = r'''
import json, re, sys, time, calendar
prefix, since = sys.argv[1], float(sys.argv[2])
by_tag = len(sys.argv) > 3 and sys.argv[3] in ("by-tag", "by-tag-kept")
with_kept = len(sys.argv) > 3 and sys.argv[3] == "by-tag-kept"
virt = re.compile(r"((zram|loop|nbd|md|ram|sr|zd)\d+|dm-\d+)\Z")
total = 0
sums = {}
kept = {}
try:
    lines = open("/bkoscratch/wear/jobs.log", errors="replace").read().splitlines()
except OSError:
    lines = []
# TIME HOST SCOPE RC SECS DISK=BYTES[,DISK=BYTES] WHAT tag=TAG serial=... (tab-separated)
for l in lines:
    f = l.split("\t")
    tag = next((x[4:] for x in f if x.startswith("tag=")), "")
    if len(f) < 6 or not (tag == prefix or tag.startswith(prefix + ":") or tag.startswith(prefix + "-r2:")):
        continue
    try:
        t = calendar.timegm(time.strptime(f[0][:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        t = since
    if t < since:
        continue
    disks = {}
    for part in f[5].split(","):
        k, _, v = part.partition("=")
        if v.isdigit() and k and not virt.match(k):
            disks[k] = disks.get(k, 0) + int(v)
    n = sum(disks.values())
    total += n
    sums[tag] = sums.get(tag, 0) + n
    if "scratch=ssd" in f[7:]:
        # its scratch was on the SSD (cmp-run.sh --reserve-ssd): every byte of it, as wear.Job.kept_disks
        if n:
            kept[tag] = kept.get(tag, 0) + n
        continue
    for part in next((x[5:] for x in f[6:] if x.startswith("keep=")), "").split(","):
        k, _, v = part.partition("=")
        if v.isdigit() and disks.get(k):
            kept[tag] = kept.get(tag, 0) + min(int(v), disks[k])
if with_kept:
    print(json.dumps({"by": sums, "kept": kept}, sort_keys=True))
else:
    print(json.dumps(sums, sort_keys=True) if by_tag else total)
'''
        mode = ["by-tag-kept"] if kept else (["by-tag"] if by_tag else [])
        r = self.h.ssh(host, script=script, args=[prefix, str(since)] + mode, timeout=60)
        try:
            if r.rc != 0:
                return (None, None) if kept else None
            if kept:
                got = json.loads(r.text.strip())
                by, kb = (got.get("by"), got.get("kept")) if isinstance(got, dict) else (None, None)
                if not isinstance(by, dict):
                    return None, None
                by = {str(k): int(v) for k, v in by.items()}
                kb = {str(k): int(v) for k, v in (kb or {}).items()} if isinstance(kb, dict) else {}
                return (by, kb) if by_tag else (sum(by.values()), sum(kb.values()))
            if not by_tag:
                return int(r.text.strip())
            got = json.loads(r.text.strip())
            return {str(k): int(v) for k, v in got.items()} if isinstance(got, dict) else None
        except (ValueError, TypeError, AttributeError):
            return None


# --- the run's own state (resume) -------------------------------------------------------------------

class State:
    """MAC_ROOT/runs/RUN/holt.json: what this runner has done, for resume (R10)."""

    def __init__(self, path):
        self.path = path
        self.d = {}
        try:
            with open(path, encoding="utf-8") as f:
                self.d = json.load(f)
        except (OSError, ValueError):
            self.d = {}
        self.d.setdefault("v", 1)
        self.d.setdefault("builds", {})
        self.d.setdefault("cross", {})
        self.d.setdefault("queues", {})

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.d, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.path)

    def __getitem__(self, k):
        return self.d.get(k)

    def __setitem__(self, k, v):
        self.d[k] = v
        self.save()


def queue_host(hosts, table=None):
    """The host a run's queues live on (img-speed.md F15): its todo, the lines every other
    host's runner pops over ssh (QHOST).  Of HOSTS, a default host before one that is not,
    then hosts.tie_key's order (a big host before a small one, the cooler before the hotter,
    then the table's): cinqcent before chopin, whose cooler throttles, so a throttling or
    rebooting Chopin no longer stalls every host's pops.  None for no hosts."""
    hs = list(hosts or [])
    if not hs:
        return None
    t = table if table is not None else hosts_table.load()
    dflt = set(hosts_table.names(default=True, table=t))
    return min(hs, key=lambda h: (hosts_table.short(h) not in dflt, hosts_table.tie_key(h, t)))


# --- the runner -----------------------------------------------------------------------------------

class Runner:
    def __init__(self, ctx, plan, remote=None, sleep=None, poll=POLL_S, clock=None, gate=None):
        self.ctx, self.plan = ctx, plan
        self.gate_fn = gate
        self.rs = ctx.run
        self.remote = remote or Remote()
        self.sleep = sleep or time.sleep
        self.clock = clock or time.time
        self.poll = poll
        self.run_id = self.rs.run
        self.hosts = list(self.rs.hosts)
        self.sf = spec_of(ctx)
        self.cls = (ctx.classes or {}).get("holt") or {}
        self.opts = getattr(ctx, "options", None) or {}
        self.state = State(os.path.join(self.rs.mac_dir, "holt.json"))
        # F15: the queue's host is the run's most reliable host (queue_host), no longer its first;
        # kept in holt.json, so a resume finds the todo where its queues put it.  A holt.json with
        # queues but no qhost is a run started before F15: its todo is on its first host.
        self.qhost = self.state["qhost"] or (self.hosts[0] if self.state["queues"] and self.hosts
                                             else queue_host(self.hosts))
        if self.qhost and not self.state["qhost"]:
            self.state["qhost"] = self.qhost
        self.stops = []
        self.notes = []
        self.problems = []     # what makes the run fail that no stop rule said (a runner that didn't start ...)
        self.rows = {}         # origin -> row
        self.done = {}         # qrun -> {(host, gv): done row}, as read so far
        self.taken = set()     # (qrun, gv) lines stop-os took out
        self.gate_cells = list(self.state["gate_cells"] or [])
        self.t0 = self.state["t0"] or self.clock()
        if not self.state["t0"]:
            self.state["t0"] = self.t0
        self.swap0 = dict(self.state["swap0"] or {})
        self.allowed = dict(self.state["write_allowed"] or {})   # host -> {cell: bytes a write allowance excused}
        self.kept = dict(self.state["write_kept"] or {})         # host -> bytes failed runs' kept state wrote
        self._sigs = {}
        if self.state["interrupted"]:   # (a resume of a run stopped by a signal: that stop stands)
            self.stops.append(self.state["interrupted"].get("stop") or "stop-starts: interrupted")
        self.keep_copy()

    def keep_copy(self):
        """R10: the copy on each host in holt.json, and back from it on a resume.  run.py's
        resume takes the copy from cells-HOST.json, which step 3 writes only after the builds
        and the gate (hours, when a build waits for its room): a run stopped before then
        would resume with no copy to run anything from."""
        saved = self.state["copy"] or {}
        cur = self.rs.copy if isinstance(self.rs.copy, dict) else {}
        for h in self.hosts:
            if not (cur.get(h) or {}).get("path") and (saved.get(h) or {}).get("path"):
                cur[h] = dict(saved[h])
        self.rs.copy = cur
        if any((c or {}).get("path") for c in cur.values()) and saved != cur:
            self.state["copy"] = {h: dict(c) for h, c in cur.items()}

    # paths, as the hosts see them
    def host_root(self):
        from . import hostrun
        return hostrun.host_path(self.rs.host_root)

    def pass_through(self):
        """The test roots a job needs too, when the Mac's environment has them."""
        return {k: os.environ[k] for k in ("T2_WORK", "T2_IMAGES") if os.environ.get(k)}

    def copy_path(self, host):
        p = ((self.rs.copy or {}).get(host) or {}).get("path")
        if not p:
            raise NoCopy(f"no private copy on {host} for run {self.run_id} (holt.json has none either)")
        return p

    def tool(self, host, name):
        return os.path.join(self.copy_path(host), "testvm", "holttest1d", name)

    def t2_host(self, host):
        return os.path.join(self.copy_path(host), "testvm", "test2", "t2-host")

    def ledger_path(self, host):
        return os.path.join(self.host_root(), "ledger", f"{host}.jsonl")

    def log(self, text):
        try:
            self.rs.log(text)
        except Exception:   # a log that fails stops nothing
            pass

    def all_hosts(self):
        """--hosts first, then the other default test hosts (plan.DEFAULT_HOSTS); with
        ctx.options holt_ship (or T2_HOLT_SHIP) "known", every known amd64 host (KNOWN_HOSTS:
        the small ones too, not an arm64 one such as bigfoot: these bko-holt builds are amd64)
        instead: the hosts a build is looked for on and shipped to."""
        try:
            from . import plan as planmod
            every = list(getattr(planmod, "DEFAULT_HOSTS", ()) or ())
        except ImportError:
            every = []
        if (self.opts.get("holt_ship") or os.environ.get("T2_HOLT_SHIP")) == "known":
            from . import KNOWN_HOSTS
            from . import hosts as hosts_table
            every += [h for h in KNOWN_HOSTS if h not in every and hosts_table.host_arch(h) == "amd64"]
        return self.hosts + [h for h in every if h not in self.hosts]

    def build_hosts(self):
        """Where a bko-holt build may run: the host table's build hosts (role build, big or
        small: holt_build_hosts) among --hosts, in --hosts' order; else the ones that hold this
        run's copy (it builds from there).  [] when there are none."""
        try:
            mine = holt_build_hosts(self.hosts)
            if mine:
                return mine
            role = holt_build_hosts(hosts_table.with_role("build"))
        except Exception:   # noqa: BLE001 - a table that doesn't load builds nowhere (the reason says so)
            return []
        return [h for h in role if ((self.rs.copy or {}).get(h) or {}).get("path")]

    def started_pid(self, host, pid, pidfile, needle):
        """The PID a detached start returned, else the one its PID file holds once it runs
        (the start's ssh can fail after the process began: never leave it unwatched)."""
        if pid is not None:
            return pid
        t = (self.remote.read(host, pidfile) or "").strip()
        if t.isdigit() and self.remote.alive(host, int(t), needle):
            return int(t)
        return None

    # --- 1. bko-holt -------------------------------------------------------------------------------
    def holt_dir(self):
        hb = self.opts.get("holt_bin")
        if hb:
            return hb
        return os.path.join(self.host_root(), "holt-bin", self.code)

    def kinds(self):
        """{PLAT: KIND} of the platforms this run ships the cross build of (the plan's, from
        holtbuild.kinds: img-speed.md I12); none with --holt-bin."""
        if self.opts.get("holt_bin"):
            return {}
        return dict((self.plan.set or {}).get("holt_build") or {})

    def ensure_holt(self, pcs):
        """_ensure_holt under MAC_ROOT/runs/.lock-holt-bin-CODE: holt runs over other cells go
        side by side (run.CellLocks), and one at a time builds and ships a CODE (builds aren't
        reproducible, R14); the next waits, then finds it built.  Returns None, or why not."""
        if self.opts.get("holt_bin") or getattr(self.rs, "mac_root", None) is None:
            return self._ensure_holt(pcs)
        from . import run as runmod
        lk = runmod.FamilyLock(f"holt-bin-{self.code[:16]}", self.rs.mac_root)
        end, said = self.clock() + BUILD_TIMEOUT_S, False
        while True:
            try:
                lk.acquire(self.run_id, wait=0.5)
                break
            except runmod.Busy as e:
                if not said:
                    self.log(f"bko-holt code {self.code[:12]}: {e}: waiting for it to build and ship")
                    said = True
                if self.clock() > end:
                    return f"bko-holt code {self.code[:12]}: {e}, for over {BUILD_TIMEOUT_S // 3600} h"
                self.sleep(self.poll)
        try:
            return self._ensure_holt(pcs)
        finally:
            lk.release()

    def _ensure_holt(self, pcs):
        """bko-holt for every platform the cells need, in holt_dir() on every host of
        --hosts (and, best effort, on the other test hosts).  A platform any test host
        has already is copied from there, never built again (builds aren't reproducible,
        R14), and a binary already in a host's CODE/ is never written over.  Returns None,
        or why not (the run then starts no guest).  A build other than linux's that ended
        and failed loses only its platform: holt.json's lost_plats {PLAT: why} gets it,
        due() leaves out the cells it builds for, result() says each is skipped, and the
        other platforms are shipped and their cells run (all_lost(): when no cell is
        left, the run stops as it did)."""
        plats = needed_platforms(self.sf, [pc.cell for pc in pcs])
        hd = self.holt_dir()
        if self.opts.get("holt_bin"):
            miss = [f"{h}:{holt_rel(p)}" for h in self.hosts for p in plats
                    if not self.remote.exists(h, os.path.join(hd, holt_rel(p)))]
            return f"--holt-bin {hd} lacks {', '.join(miss)}" if miss else None
        bh = self.build_hosts()
        every = self.all_hosts()
        every += [h for h in bh if h not in every]   # (a build host outside --hosts holds what it builds)
        outside = [h for h in every if h not in self.hosts]
        kinds = {p: k for p, k in self.kinds().items() if p in plats}
        if kinds:   # (I12: a build of another kind in CODE/PLAT/ goes to CODE/retired/, never tested as this one)
            err = self.retire_other_kinds(hd, every, kinds)
            if err:
                return err
        back = [p for p in plats if p in holtbuild.CROSS_ABLE and p not in kinds]
        if back:   # (and a cross build of a platform on its VM build: D11, its VM reference failed)
            err = self.retire_cross_builds(hd, every, back)
            if err:
                return err
        infos = self.known_infos(hd, every)
        builds = self.state["builds"]
        for p, b in builds.items():   # this run's own builds win (a resume)
            if b.get("rc") == 0 and isinstance(b.get("info"), dict):
                infos[p] = b["info"]
        have, unsure = {h: set() for h in every}, set()
        for h in every:
            for p in plats:
                e = self.remote.exists(h, os.path.join(hd, holt_rel(p)))
                if e is None:
                    e = self.remote.exists(h, os.path.join(hd, holt_rel(p)))
                if e:
                    have[h].add(p)
                elif e is None and h in self.hosts:   # (an outside host that is down never blocks a build:
                    unsure.add(p)                     # nothing is ever copied over what it holds)
        todo = [p for p in plats if not any(p in have[h] for h in every)]
        if [p for p in todo if p in unsure]:
            return (f"can't tell whether {hd} holds {', '.join(holt_rel(p) for p in todo if p in unsure)} on "
                    f"{', '.join(self.hosts)} (ssh failed): nothing built over it")
        # Builds not done yet: start each (round-robin over the build hosts of --hosts,
        # linux on the first; never on a host without the build role).
        order = sorted(todo, key=lambda p: (p != "linux", p))
        start = [p for p in order if not (builds.get(p) and (builds[p].get("rc") == 0 or
                                                              (builds[p].get("pid") and builds[p].get("rc") is None)))]
        if start and not bh:
            return (f"no build host (role build in testvm/hosts.json) in --hosts "
                    f"{', '.join(self.hosts)} to build bko-holt {', '.join(start)} on, and none holds this "
                    f"run's copy: add one to --hosts, or give --holt-bin")
        for i, p in enumerate(order):
            b = builds.get(p)
            if b and b.get("rc") == 0:
                continue
            if b and b.get("pid") and b.get("rc") is None:
                continue   # resume: still running (or ended: its exit file says)
            if p in kinds:
                ready = self.ready_cross(hd, p, kinds[p], every)
                if ready:   # (cross builds are reproducible: a check build of this kind is the build)
                    builds[p] = ready
                    self.state.save()
                    continue
            host = bh[i % len(bh)]
            builds[p] = self.start_build(host, p, cross=p in kinds)
            self.state.save()
        self.start_crosses([p for p in order if p in CROSS_PLATS and p not in kinds], bh, every)
        err = self.wait_builds(order)
        err = self.ship_crosses(hd, [p for p in order if p in kinds], kinds) or err
        if err:
            # (A failed build that ended, other than linux's, loses its platform's cells
            # alone; a build still running, or linux's failing, stops the run as before.)
            bad = [p for p in order if (builds.get(p) or {}).get("rc") != 0]
            if not bad or "linux" in bad or any(builds[p].get("rc") is None for p in bad):
                return err
            lost = dict(self.state["lost_plats"] or {})
            for p in bad:
                lost[p] = self.build_failure(p)
                self.log(f"bko-holt {p}: {lost[p]}: its cells are skipped, the other platforms' run")
            self.state["lost_plats"] = lost
            order = [p for p in order if p not in bad]
            plats = [p for p in plats if p not in bad]
        for p in order:
            b = builds[p]
            infos[p] = b["info"]
            have.setdefault(b["host"], set()).add(p)
        # Every host of --hosts gets every platform (checked by sha256).
        good = {}   # plat -> hosts whose binary is the build
        bad_out = set()
        for p in plats:
            path = os.path.join(hd, holt_rel(p))
            holders = [h for h in every if p in have[h]]
            if not holders:
                return f"no host has {holt_rel(p)} after the builds"
            want = (infos.get(p) or {}).get("sha256") or self.remote.sha256(holders[0], path)
            if not want:
                return f"{holders[0]}: no sha256 of {path}"
            infos.setdefault(p, {"sha256": want, "host": holders[0]})
            good[p] = []
            for h in holders:
                got = self.remote.sha256(h, path)
                if got == want:
                    good[p].append(h)
                elif h in self.hosts:
                    return f"{h}: {path} is {str(got)[:12]}, its build {want[:12]}"
                else:
                    bad_out.add(h)
                    self.notes.append(f"{h}: {path} is {str(got)[:12]}, not the build {want[:12]}: left as it is")
            if not good[p]:
                return f"no host has the build of {path} (sha256 {want[:12]})"
            for h in self.hosts:
                if h in good[p]:
                    continue
                if not self.remote.copy(good[p][0], path, h, path) or self.remote.sha256(h, path) != want:
                    return f"couldn't copy {path} from {good[p][0]} to {h} (sha256 {want[:12]})"
                have[h].add(p)
                good[p].append(h)
                self.log(f"bko-holt {p}: copied from {good[p][0]} to {h}, sha256 {want[:12]} checked")
        commit = (self.ctx.copy_preview or {}).get("commit") or self.ctx.head
        dirty = (self.ctx.copy_preview or {}).get("dirty")
        for h in self.hosts:
            self.write_code_files(h, hd, self.infos_on(h, hd, infos, plats, good), commit, dirty)
        # The other test hosts get the builds too (the contract's scp to the other host,
        # whatever --hosts says): best effort, a note when one can't, and never over a
        # binary that is there already.
        for h in [x for x in outside if x not in bad_out]:
            try:
                for p in plats:
                    path = os.path.join(hd, holt_rel(p))
                    want = infos[p]["sha256"]
                    if h in good[p]:
                        continue
                    if self.remote.exists(h, path):
                        raise OSError(f"{path} is there already and isn't the build {want[:12]}")
                    if not self.remote.copy(good[p][0], path, h, path) or self.remote.sha256(h, path) != want:
                        raise OSError(f"couldn't copy {path} (sha256 {want[:12]})")
                    good[p].append(h)
                self.write_code_files(h, hd, self.infos_on(h, hd, infos, plats, good), commit, dirty)
                self.log(f"bko-holt code {self.code[:12]}: shipped to {h} too")
            except (OSError, KeyError) as e:
                self.notes.append(f"bko-holt code {self.code[:12]} not shipped to {h}: {e}")
        self.wait_crosses()
        return None

    # --- the switched platforms (img-speed.md I12, §8.3): their cross build is the one shipped ---------
    def retire_other_kinds(self, hd, every, kinds):
        """On each host, a CODE/PLAT/bko-holt of a switched PLAT whose CODE/build-PLAT.json
        records another kind (a VM build of this CODE made before the switch, or a cross build
        of another toolchain or sysroot) goes to CODE/retired/SHA12/ with its build-PLAT.json,
        and its SHA256SUMS and BUILD-INFO lines go: never written over, never tested as the
        kind the plan keyed.  Returns None, or why not."""
        for h in every:
            for p in sorted(kinds):
                path = os.path.join(hd, holt_rel(p))
                e = self.remote.exists(h, path)
                if e is None and h in self.hosts:
                    return f"can't tell whether {h} holds {path} (ssh failed): nothing built over it"
                if not e:
                    continue
                t = self.remote.read(h, os.path.join(hd, f"build-{p}.json"))
                try:
                    info = json.loads(t) if t else None
                except ValueError:
                    info = None
                got = holtbuild.kind_of(info)
                if got == kinds[p] and info.get("sha256") == self.remote.sha256(h, path):
                    continue
                d = self.remote.retire(h, hd, holt_rel(p), f"build-{p}.json")
                if not d:
                    if h in self.hosts:
                        return f"{h}: couldn't move {path} ({got}, not {kinds[p]}) to {hd}/retired/"
                    self.notes.append(f"{h}: {path} is a {got} build, not {kinds[p]}, and couldn't be moved aside")
                    continue
                self.log(f"bko-holt {p}: {h}'s {got} build moved to {d} (this run ships {kinds[p]})")
        return None

    def retire_cross_builds(self, hd, every, plats):
        """On each host, a cross build in CODE/PLAT/ (its CODE/build-PLAT.json says so) of a PLAT
        this run ships the VM build of goes to CODE/retired/SHA12/, so that the run builds the VM
        build anew.  That is a platform whose VM reference failed after its switch (D11: back on
        its VM build until a reference passes again), or T2_HOLT_BUILD=vm.  Without this, the
        cross build would stay the one shipped and copied into images, its rows keyed cross
        under a plan that keys vm.  A VM build, or one with no build json, stays as before.
        Returns None, or why not."""
        for h in every:
            jsons = self.remote.glob_read(h, os.path.join(hd, "build-*.json"))
            for p in sorted(plats):
                try:
                    info = json.loads(jsons.get(os.path.join(hd, f"build-{p}.json")) or "null")
                except ValueError:
                    info = None
                got = holtbuild.kind_of(info)
                if got == "vm" or not self.remote.exists(h, os.path.join(hd, holt_rel(p))):
                    continue
                d = self.remote.retire(h, hd, holt_rel(p), f"build-{p}.json")
                if d:
                    self.log(f"bko-holt {p}: {h}'s {got} build moved to {d} (this run ships its VM build)")
                elif h in self.hosts:
                    return f"{h}: couldn't move {os.path.join(hd, holt_rel(p))} ({got}, not vm) to {hd}/retired/"
                else:
                    self.notes.append(f"{h}: {os.path.join(hd, holt_rel(p))} is a {got} build, not a VM "
                                      f"build, and couldn't be moved aside")
        return None

    def ready_cross(self, hd, p, kind, every):
        """A check build of KIND in CODE/cross/PLAT/ on a host of EVERY, made the shipped build
        there (CODE/PLAT/): a builds entry, or None.  One of another kind there is moved to
        CODE/retired/ first, so that this run's build may go there."""
        path = os.path.join(self.cross_dir(p), "bko-holt")
        for h in every:
            if not self.remote.exists(h, path):
                continue
            t = self.remote.read(h, os.path.join(hd, "cross", f"build-{p}.json"))
            try:
                info = json.loads(t) if t else None
            except ValueError:
                info = None
            if holtbuild.kind_of(info) == kind and info.get("sha256") == self.remote.sha256(h, path):
                if not self.remote.promote(h, path, os.path.join(hd, holt_rel(p))):
                    continue
                self.log(f"bko-holt {p}: {h}'s cross build {info['sha256'][:12]} ({kind}) is the one shipped")
                return {"host": h, "rc": 0, "info": info, "tool": "holt-cross.sh", "shipped": True,
                        "started": utc_now(), "from_check": True}
            if self.remote.retire(h, hd, f"cross/{p}/bko-holt", f"cross/build-{p}.json"):
                self.log(f"bko-holt {p}: {h}'s cross check build of {holtbuild.kind_of(info)} moved to retired/")
        return None

    def ship_crosses(self, hd, plats, kinds):
        """This run's cross builds of the switched PLATS: one whose kind is the plan's goes from
        CODE/cross/PLAT/ to CODE/PLAT/ on its build host (with CODE/cross/build-PLAT.json); one
        of another kind (the toolchain or the sysroot moved) fails its platform: it isn't the
        build E2 passed on.  Returns why, or None."""
        why = []
        for p in plats:
            b = self.state["builds"].get(p) or {}
            if b.get("rc") != 0 or b.get("shipped"):
                continue
            got = holtbuild.kind_of(b.get("info"))
            src = os.path.join(self.cross_dir(p), "bko-holt")
            if got != kinds[p]:
                b["rc"] = 1
                b["why"] = (f"its kind is {got}, not {holtbuild.BUILDS}'s {kinds[p]}: E2 on the new kind first "
                            f"(testvm/holttest1d/holt-ref.py run {p} --repin)")
            elif self.remote.sha256(b["host"], src) != b["info"].get("sha256") or \
                    not self.remote.promote(b["host"], src, os.path.join(hd, holt_rel(p))):
                b["rc"], b["why"] = 1, f"couldn't put {src} in CODE/{holt_rel(p)} on {b['host']}"
            else:
                b["shipped"] = True
                self.remote.write(b["host"], os.path.join(hd, "cross", f"build-{p}.json"),
                                  json.dumps(b["info"], sort_keys=True) + "\n")
                self.log(f"bko-holt {p}: cross build {b['info']['sha256'][:12]} ({got}) shipped from {b['host']}")
            self.state.save()
            if b.get("rc") != 0:
                why.append(self.build_failure(p))
        return "; ".join(why) or None

    # --- the cross check builds (img-speed.md I10, §8.2a: CODE/cross/PLAT/, never CODE/PLAT/) ---------
    def cross_dir(self, plat):
        return os.path.join(self.holt_dir(), "cross", plat)

    def start_crosses(self, plats, bh, every):
        """A cross check build of each of PLATS (the platforms this run builds in a VM) that
        this run hasn't started and no host holds in CODE/cross/PLAT/ already (never built
        over, as CODE/'s builds aren't), on the build host after the VM build's."""
        cross = self.state["cross"]
        for i, p in enumerate(sorted(plats)):
            if p in cross or not bh:
                continue
            path = os.path.join(self.cross_dir(p), "bko-holt")
            if any(self.remote.exists(h, path) for h in every):
                continue
            host = bh[(i + 1) % len(bh)]
            cross[p] = self.start_build(host, p, cross=True)
            self.state.save()

    def wait_crosses(self):
        """The check builds' ends: a failure, or one still running after CROSS_TIMEOUT_S, is a
        note and nothing more (the run tests the VM builds); a build's info goes beside it as
        CODE/cross/build-PLAT.json on its host."""
        cross = self.state["cross"]
        plats = sorted(p for p, b in cross.items() if not b.get("noted"))
        if not plats:
            return
        err = self.wait_builds(plats, key="cross", timeout=CROSS_TIMEOUT_S)
        for p in plats:
            b = cross[p]
            if b.get("rc") == 0:
                jp = os.path.join(self.holt_dir(), "cross", f"build-{p}.json")
                self.remote.write(b["host"], jp, json.dumps(b["info"], sort_keys=True) + "\n")
                self.log(f"bko-holt {p}: cross check build on {b['host']}, sha256 {b['info']['sha256'][:12]} "
                         f"({b['info'].get('build')}): not shipped, not tested here (E2)")
            elif b.get("rc") is None:
                self.notes.append(f"bko-holt {p} cross check build still running on {b['host']} after "
                                  f"{CROSS_TIMEOUT_S // 60} min: left to end by itself")
            else:
                self.notes.append(self.build_failure(p, key="cross") + ": a check build, the run goes on")
            b["noted"] = True
        self.state.save()
        return err

    def infos_on(self, h, hd, infos, plats, good):
        """The builds host H's CODE/ holds: the ones checked here, and earlier runs' others
        that are there."""
        return {p: i for p, i in infos.items()
                if (p in plats and h in good.get(p, ())) or
                (p not in plats and self.remote.exists(h, os.path.join(hd, holt_rel(p))))}

    def write_code_files(self, h, hd, infos, commit, dirty):
        """CODE/SHA256SUMS, CODE/BUILD-INFO and CODE/build-PLAT.json on host H, for the
        builds it holds (INFOS)."""
        bi = build_info_text(self.code, commit, dirty, infos, self.run_id)
        for name, text in (("SHA256SUMS", sha256sums_text(infos)), ("BUILD-INFO", bi)):
            if self.remote.read(h, os.path.join(hd, name)) != text:
                self.remote.write(h, os.path.join(hd, name), text)
        for p, i in infos.items():
            jp = os.path.join(hd, f"build-{p}.json")
            text = json.dumps(i, sort_keys=True) + "\n"
            if self.remote.read(h, jp) != text:
                self.remote.write(h, jp, text)

    def known_infos(self, hd, hosts):
        """{plat: build info} from the hosts' CODE/build-PLAT.json (earlier runs' builds)."""
        out = {}
        for h in hosts:
            for path, text in self.remote.glob_read(h, os.path.join(hd, "build-*.json")).items():
                m = re.search(r"build-([a-z]+)\.json$", path)
                try:
                    j = json.loads(text)
                except ValueError:
                    continue
                if m and isinstance(j, dict) and re.fullmatch(r"[0-9a-f]{64}", str(j.get("sha256"))):
                    out.setdefault(m.group(1), j)
        return out

    def build_logs(self, host, plat):
        return os.path.join(self.rs.host_run_dir(host), f"build-{plat}")

    def start_build(self, host, plat, cross=False):
        """PLAT's VM build (build-holt.sh), or with CROSS its check build (holt-cross.sh) into
        CODE/cross/PLAT/ (img-speed.md §8.2a)."""
        hd = self.holt_dir()
        out = hd if plat == "linux" else os.path.join(hd, plat)
        logs = self.build_logs(host, plat)
        tool, res_default = "build-holt.sh", None
        if cross:
            out, logs, tool = self.cross_dir(plat), self.build_logs(host, "cross-" + plat), "holt-cross.sh"
            res_default = CROSS_RESERVE.get(plat, 1.5)
        peaks = []
        for path, text in self.remote.glob_read(host, os.path.join(self.host_root(), "runs", "t2h-*",
                                                                     f"build-{plat}", "peak-kib")).items():
            try:
                peaks.append(int(text.strip()))
            except ValueError:
                pass
        res = reserve_gib(sorted(peaks)[-3:]) if res_default is None else res_default
        from . import hostrun
        # (A build started again on a resume: the last one's exit and info.json mustn't be
        # read as this one's.)
        self.remote.sh(host, f"rm -f {hostrun.rq(os.path.join(logs, 'exit'))} {hostrun.rq(os.path.join(logs, 'info.json'))}",
                       timeout=30)
        env = {"HOLT_SRC": os.path.join(self.copy_path(host), "holt"), "HOLT_OUT": out, "HOLT_LOGS": logs,
               "HOLT_INFO": os.path.join(logs, "info.json"), "HOLT_RESERVE": f"{res:g}", "PYTHONDONTWRITEBYTECODE": "1"}
        cmd = ("env " + " ".join(f"{k}={hostrun.rq(v)}" for k, v in sorted(env.items())) + " "
               + " ".join([hostrun.rq(self.tool(host, tool)), "--reserved", self.run_id, plat]))
        pidfile = os.path.join(logs, "wrapper.pid")
        pid = self.remote.start(host, cmd, os.path.join(logs, "wrapper.out"), pidfile)
        pid = self.started_pid(host, pid, pidfile, tool)
        what = ("cross build" if plat in self.kinds() else "cross check build") if cross else "building"
        self.log(f"bko-holt {plat}: {what} on {host} (pid {pid}, --reserve {res:g} 4, code {self.code[:12]})")
        # (Its scratch: the RAM disk's, or the SSD's when the RAM disk had no room: build-holt.sh
        # picks, so both are recorded for a dead run's sweep; holt-cross.sh's is the RAM disk's.)
        if cross:
            self.rs.record_made(host, f"/mnt/bko-ram/t2x-{self.run_id}-{plat}", pid)
        else:
            self.rs.record_made(host, f"/mnt/bko-ram/t2b-{self.run_id}-{plat}", pid)
            self.rs.record_made(host, f"/bkoscratch/t2b-{self.run_id}-{plat}", pid)
        b = {"host": host, "pid": pid, "rc": None, "info": None, "reserve": res, "logs": logs, "started": utc_now(),
             "tool": tool}
        if pid is None:
            b["rc"], b["why"] = 1, "its wrapper didn't start"
        return b

    def wait_builds(self, plats, key="builds", timeout=BUILD_TIMEOUT_S):
        builds = self.state[key]
        end = self.clock() + timeout
        while True:
            pending = []
            for p in plats:
                b = builds.get(p)
                if not b or b.get("rc") is not None:
                    continue
                ex = self.remote.read(b["host"], os.path.join(b["logs"], "exit"))
                if ex is not None and ex.strip().lstrip("-").isdigit():
                    b["rc"] = int(ex.strip())
                    if b["rc"] == 0:
                        t = self.remote.read(b["host"], os.path.join(b["logs"], "info.json"))
                        try:
                            b["info"] = json.loads(t or "")
                        except ValueError:
                            b["info"] = None
                        if not isinstance(b["info"], dict) or not re.fullmatch(r"[0-9a-f]{64}",
                                                                               str(b["info"].get("sha256"))):
                            b["rc"] = 1
                            b["why"] = "no info.json from the build"
                        else:
                            b["info"]["run"] = self.run_id
                    self.state.save()
                    self.log(f"bko-holt {p} on {b['host']}: exit {b['rc']}")
                    continue
                a = self.remote.alive(b["host"], b["pid"], b.get("tool") or "build-holt.sh") if b.get("pid") else False
                if a is False:
                    # (Its exit file may have landed just before it ended: looked at next time.)
                    if b.get("gone"):
                        b["rc"], b["why"] = 1, "the build's wrapper ended without an exit file"
                        self.state.save()
                        continue
                    b["gone"] = True
                elif a:
                    b.pop("gone", None)
                pending.append(p)   # (alive, or the host couldn't be asked: looked at next time)
            if not pending:
                break
            if self.clock() > end:
                left = ", ".join(f"{p} ({builds[p]['host']} pid {builds[p].get('pid')})" for p in pending)
                return (f"bko-holt builds still running after {timeout / 3600:g} h: {left}; "
                        "they are left to end by themselves (each removes its RAM dir)")
            self.sleep(self.poll)
        bad = [p for p in plats if (builds.get(p) or {}).get("rc") != 0]
        if bad:
            return "; ".join(self.build_failure(p, key) for p in bad)
        return None

    def build_failure(self, p, key="builds"):
        """Why platform P's build of this run failed (its host, exit and logs)."""
        b = self.state[key].get(p) or {}
        return (f"bko-holt {p}{' cross check' if key == 'cross' else ''} build failed on {b.get('host')} (exit {b.get('rc')}"
                + (f": {b['why']}" if b.get("why") else "")
                + f"; logs {b.get('host')}:{b.get('logs')})")

    # --- 1a. image sums ------------------------------------------------------------------------------
    def ensure_imgsums(self, pcs):
        """The image sums the cells' keys need and the hosts' caches lack (contract 1.4: the
        planner never hashes; a guest with no generation, or a one-time rescuer copy with no
        .sha256, keys as img:unknown, and a row with an unknown input never counts, until
        t2-host imgsum has cached its sum).  ctx.facts get the sums as facts_host.py would
        give them, for the re-plan.  Returns how many were added."""
        added = 0
        facts = getattr(self.ctx, "facts", None)
        if not isinstance(facts, dict):
            return 0
        subst = getattr(keys, "_subst", None)
        for h in self.hosts:
            f = facts.get(h)
            if not isinstance(f, dict):
                continue
            gs = f.get("guests") or {}
            imgs = (f.get("rescuers") or {}).get("images") or {}
            want_g, want_i = {}, {}
            for pc in pcs:
                if getattr(pc, "keys", None) and h not in pc.keys:
                    continue
                e = entry_of(self.sf, pc.cell)
                guest = base_guest(pc.cell)
                names = [guest]
                for x in e.get("rescuer") or []:
                    if isinstance(x, str) and not x.startswith(("@", "bin/")):
                        names.append(subst(x, guest, e.get("plat"), e.get("kind")) if subst else x)
                for n in names:
                    if n.endswith((".qcow2", ".img", ".raw")):
                        im = imgs.get(n)
                        if isinstance(im, dict) and not im.get("sha256") and not im.get("imgsum"):
                            want_i[n] = os.path.join(HOLT_L, "rescuer", n)
                        continue
                    n = n[len("guest:"):] if n.startswith("guest:") else n
                    g = gs.get(n)
                    if isinstance(g, dict) and not g.get("generation") and not g.get("imgsum"):
                        ds = [d.get("path") for d in g.get("disks") or [] if isinstance(d, dict)]
                        if ds and all(ds):
                            want_g[n] = ds
            paths = sorted({p for ds in want_g.values() for p in ds} | set(want_i.values()))
            if not paths:
                continue
            self.log(f"imgsum on {h}: {len(paths)} image(s) of {', '.join(sorted(set(want_g) | set(want_i)))}")
            try:
                got = self.remote.imgsum(h, self.t2_host(h), paths, {"T2_HOST_ROOT": self.host_root()})
            except NoCopy as e:
                got, why = None, str(e)
            else:
                why = "t2-host imgsum failed"
            if got is None:
                self.notes.append(f"{h}: no image sums for {', '.join(sorted(set(want_g) | set(want_i)))} ({why}): "
                                  "their rows won't count")
                continue
            from . import facts_host
            for n, ds in want_g.items():
                sums = {os.path.basename(p): got.get(p) for p in ds}
                if all(sums.values()):
                    gs[n]["imgsum"] = facts_host.combine(sums)
                    added += 1
            for n, p in want_i.items():
                if got.get(p):
                    imgs[n]["imgsum"] = "img:" + got[p]
                    added += 1
            miss = [p for p in paths if not got.get(p)]
            if miss:
                self.notes.append(f"{h}: t2-host imgsum gave no sum for {', '.join(miss[:5])}")
        return added

    # --- 0. the pins of +img cells ---------------------------------------------------------------------
    def ensure_pins(self, pcs):
        """The rescue image pinned on each host that may run a +img cell, as ensure_imgsums
        does the sums: HoltTest2 G+img works alone, and the re-plan keys what the jobs will
        boot.  A host where the pin was refused keeps the cell out of its queue
        (self.pin_bad), and a cell no host can pin says why."""
        wanted = {}
        for pc in pcs:
            if is_img(pc.cell):
                for h in self.hosts:
                    if not getattr(pc, "keys", None) or h in pc.keys:
                        wanted.setdefault(h, set()).add(plat_of(self.sf, pc.cell))
        fails = {}
        for h, plats in wanted.items():
            env = dict(self.pass_through(), T2_HOST_ROOT=self.host_root(), PYTHONDONTWRITEBYTECODE="1")
            try:
                got = self.remote.pin_images(h, self.t2_host(h), sorted(plats), env)
            except NoCopy as e:
                got, why = None, str(e)
            else:
                why = "t2-host tool img pin failed"
            if got is None:
                got = {p: {"ok": False, "why": why} for p in plats}
            for p, r in got.items():
                if r.get("ok"):
                    self.note_pin(h, p, r)
                    if r.get("stale"):
                        fails.setdefault(h, {})[p] = f"bins-stale: {', '.join(r['stale'])}"
                else:
                    fails.setdefault(h, {})[p] = r.get("why") or "no pin"
        for h, by in fails.items():
            for p, why in sorted(by.items()):
                self.notes.append(f"{h}: {p}'s rescue image isn't usable here: {why}")
        self.pin_bad = {}
        for h, by in fails.items():
            self.pin_bad[h] = sorted({base_guest(pc.cell) for pc in pcs if is_img(pc.cell)
                                      and plat_of(self.sf, pc.cell) in by})
        self.state["pin_bad"] = self.pin_bad

    def note_pin(self, h, plat, r):
        """The facts as a fresh probe would give them after this pin (facts_host.one_image)."""
        im = ((((self.ctx.facts or {}).get(h) or {}).get("images") or {}).get(plat))
        if isinstance(im, dict):
            im["pin"] = {"sha256": r.get("sha256"), "current": True, "bins_stale": bool(r.get("stale"))}

    # --- 2. the fast holt gate, the re-plan ---------------------------------------------------------
    def real_gate(self, ctx):
        """t2lib.gate's fast layers for holt (gate:holt), when its gate plan has them due:
        run_layers' result, or None when none is due (or the gate isn't built)."""
        try:
            from . import gate
        except ImportError:
            self.notes.append("gate:holt not run: t2lib/gate.py is not built")
            return None
        fam = gate.family_layers("holt") if hasattr(gate, "family_layers") else {"fast": ["holt"]}
        names = list((fam or {}).get("fast") or ["holt"])
        gp = gate.gate_plan(ctx) if hasattr(gate, "gate_plan") else None
        if gp is not None:
            st = {pc.cell: pc.status for pc in gp.cells}
            due = [n for n in names if st.get("gate:" + n, "due") == "due"]
            for n in names:
                if n not in due:
                    self.notes.append(f"gate:{n} {st.get('gate:' + n)}: not run")
            names = due
        opts = getattr(ctx, "options", None) or {}
        if opts.get("gate_universe") is not None:   # (--universe, test only: a composite's gate cells)
            keep = set(opts["gate_universe"])
            for n in names:
                if "gate:" + n not in keep:
                    self.notes.append(f"gate:{n}: not in the composite's --universe: not run")
            names = [n for n in names if "gate:" + n in keep]
        if opts.get("gate_fresh") and names:
            names, failed = self.fresh_left(ctx, names)
            if failed:
                return {"cells": [], "failed": failed}
        if not names:
            return None
        return gate.run_layers(ctx, names, "fast", plan=gp)

    def fresh_left(self, ctx, names):
        """options.gate_fresh (a SweepTest2's children): gate.gate_plan has every layer due, fresh,
        so a layer a run of this stamp already ran (this run before a resume) would run again, as
        stic.py's fresh_left.  (NAMES less those, [the ones whose newest row there didn't pass])."""
        m = _STAMP.match(self.run_id or "")
        if not m:
            return names, []
        newest = {}
        for r in getattr(ctx, "ledger", None) or []:
            c = r.get("cell")
            rm = _STAMP.match(r.get("run") or "")
            if c and c[len("gate:"):] in names and r.get("kind") == "run" and rm and rm.group(1) == m.group(1):
                if c not in newest or (r.get("t") or "", r.get("id", "")) >= (newest[c].get("t") or "", newest[c].get("id", "")):
                    newest[c] = r
        failed = []
        for c, r in sorted(newest.items()):
            ok = r.get("verdict") == "PASS" and r.get("valid") is not False
            self.notes.append(f"{c} already ran in this sweep ({r.get('run')}, row {r.get('id')}, {r.get('verdict')})"
                              + ("" if ok else ": not run twice, and the gate didn't pass"))
            if not ok:
                failed.append(c)
        return [n for n in names if "gate:" + n not in newest], failed

    def gate(self):
        """(stop text or None): the fast holt gate, if due (contract 2.3 flow 3)."""
        try:
            res = (self.gate_fn or self.real_gate)(self.ctx)
        except Exception as e:   # the gate's own failure is the harness's: no guest starts
            return f"the fast holt gate failed to run: {type(e).__name__}: {e}"
        if not res:
            return None
        if isinstance(res, dict) and res.get("failed"):
            return f"fast holt gate: {res['failed'][0]} already ran in this sweep and didn't pass"
        self.gate_cells = list((res.get("cells") if isinstance(res, dict) else getattr(res, "cells", None)) or [])
        self.state["gate_cells"] = self.gate_cells
        stop = res.get("stop") if isinstance(res, dict) else getattr(res, "stop", None)
        if stop in ("stop-family", "stop-run"):
            return f"fast holt gate: {stop}"
        for c in (res.get("cells") if isinstance(res, dict) else getattr(res, "cells", None)) or []:
            if c.get("verdict") in ("FAIL", "FLAKY-FAIL", "HARNESS", "LOST"):
                return f"fast holt gate: {c.get('cell')} {c.get('verdict')}"
        return None

    def pull_rows(self):
        """The merged ledger after a pull (the gate's new row in it), or None."""
        pull = self.opts.get("pull")
        if callable(pull):
            return pull()
        try:
            from . import ledger
            ledger.pull(self.hosts)
            return ledger.view().rows
        except Exception as e:   # the rows the plan had
            self.notes.append(f"no pull before the re-plan: {type(e).__name__}: {e}")
            return None

    def replan(self, pull=True):
        try:
            rows = self.pull_rows() if pull else None
            plan, added, dropped = self.rs.replan(self.ctx.facts, rows)
        except Exception as e:   # the plan as it was
            self.notes.append(f"no re-plan after the gate: {type(e).__name__}: {e}")
            return
        if added:
            self.notes.append("due after the re-plan: " + ", ".join(added))
        if dropped:
            self.notes.append("dropped after the re-plan: " + ", ".join(dropped))
        self.plan = plan

    # --- 4-6. queues ---------------------------------------------------------------------------------
    def queue_env(self, host, cells_name):
        hr = self.host_root()
        env = dict(self.pass_through(), HT_RAM="1", T2_RUN=self.run_id, T2_HOST_ROOT=hr,
                   T2_CELLS=os.path.join(self.rs.host_run_dir(host), cells_name), PYTHONDONTWRITEBYTECODE="1")
        if not self.state["all_img"]:   # (+img cells take the pin's binaries)
            env["HT_HOLT_BIN"] = self.holt_dir()
        for k in ("HT_WORK", "HT_RECORDS"):
            v = os.environ.get(k)
            if v:
                env[k] = v
        if disk_mode(host):   # a line over this host's RAM-disk ceiling: on /bkoscratch (the plan's holt_need_here)
            env["DISK_OVER"] = "1"
        return env

    def start_queue(self, qrun, pcs, cells_name):
        """The todo (on the queue host) and a runner on every host.  holt.json is saved after
        the todo and after each runner, so a resume neither writes the todo again (lines
        already taken would come back) nor starts a second runner on a host."""
        q = self.state["queues"].setdefault(qrun, {})
        if q.get("started"):
            return
        if not q.get("todo_written"):
            lines = []
            walls = {todo_line(pc.cell): (getattr(pc, "est", None) or {}).get("wall_s") for pc in pcs}
            for part in order_groups(pcs):
                lines += img_longest_first(self.remote.order(self.qhost, self.tool(self.qhost, "ht1-queue.sh"),
                                                             [todo_line(c) for c in part]), walls)
            self.remote.write(self.qhost, f"{QUEUE}/{qrun}/todo", "".join(x + "\n" for x in lines))
            q.update(todo=lines, todo_written=utc_now())
            q.setdefault("runners", {})
            self.state.save()
        q.setdefault("runners", {})
        oe = only_elsewhere(pcs, self.hosts, self.cls.get("pins") or {})
        for h, gs in (getattr(self, "pin_bad", None) or self.state["pin_bad"] or {}).items():
            if h in oe:
                oe[h] = sorted(set(oe[h]) | set(gs))
        q["cells_name"] = cells_name
        q["elsewhere"] = {h: list(v) for h, v in oe.items() if v}   # (a runner started again: restart_runner)
        for h in self.hosts:
            if h in q["runners"]:
                continue
            self.start_runner(qrun, h, cells_name, oe.get(h))
        q["started"] = utc_now()
        self.state.save()

    def start_runner(self, qrun, h, cells_name, elsewhere=None):
        """Queue QRUN's runner on host H (ELSEWHERE: its ONLY_ELSEWHERE guests); holt.json saved after."""
        from . import hostrun
        q = self.state["queues"][qrun]
        env = self.queue_env(h, cells_name)
        if elsewhere:
            env["ONLY_ELSEWHERE"] = " ".join(elsewhere)
        args = [qrun, str(BASEPORT), str(max_jobs(h))] + ([f"{self.qhost}.local"] if h != self.qhost else [])
        cmd = ("env " + " ".join(f"{k}={hostrun.rq(v)}" for k, v in sorted(env.items())) + " "
               + " ".join([hostrun.rq(self.tool(h, "ht1-queue.sh"))] + args))
        d = self.rs.host_run_dir(h)
        out = os.path.join(d, f"runner-{qrun}.out")
        pidfile = os.path.join(d, f"runner-{qrun}.pid")
        pid = self.remote.start(h, cmd, out, pidfile)
        pid = self.started_pid(h, pid, pidfile, "ht1-queue.sh")
        q["runners"][h] = {"pid": pid, "out": out}
        self.state.save()
        if pid is None:
            self.problems.append(f"queue {qrun}: the runner on {h} didn't start ({h}:{out})")
        self.log(f"queue {qrun}: runner on {h} (pid {pid}){', leaving ' + ' '.join(elsewhere) + ' to the others' if elsewhere else ''}")
        return pid

    def restart_runner(self, qrun, h):
        """The host rule's requeue for a queue's own host (2026-11-19): H's runner is gone because H
        rebooted after the queue started (run.HostWatch), H is back and no longer held, and the todo
        still has lines: a new runner there, once per host and queue, so the lines only H may run
        (pins, ceilings, images) run on it rather than wait for ever.  True when it started one;
        "wait" while all that holds but H is still held (it settles: the queue waits for it, the
        hold's own limit bounding the wait); else False."""
        q = self.state["queues"][qrun]
        st = self.rs.stops
        rb, held = getattr(st, "rebooted", None), getattr(st, "held", None)
        if rb is None or h in (q.get("restarted") or {}) or not q.get("started") or self.stops:
            return False
        try:
            if not rb(h, q["started"]):
                return False
            hold = h in ((held() if held is not None else {}) or {})
        except Exception:   # noqa: BLE001 - a rule that can't be asked starts nothing
            return False
        if any(getattr(st, f, False) for f in ("stop_run", "stop_family", "stop_starts")) or q.get("guards_tripped"):
            return False
        todo = self.remote.read(self.qhost, f"{QUEUE}/{qrun}/todo")
        if not todo or not any(x.split("#", 1)[0].strip() for x in todo.splitlines()):
            return False
        for f in ("abort", "stop"):
            if self.remote.exists(h, f"{QUEUE}/{qrun}/{f}"):
                return False
        if hold:
            return "wait"
        q.setdefault("restarted", {})[h] = utc_now()
        self.state.save()
        self.log(f"queue {qrun}: {h} rebooted and is back: its runner again (the host rule)")
        self.notes.append(f"queue {qrun}: {h}'s runner started again after its host rebooted (the host rule)")
        cells_name = q.get("cells_name") or ("cells-r2.json" if qrun.endswith("-r2") else "cells.json")
        return self.start_runner(qrun, h, cells_name, (q.get("elsewhere") or {}).get(h)) is not None

    def reboot_killed(self, qrun, h):
        """The jobs H's runner had under way when H rebooted (2026-11-19 review): the reboot took
        the runner and their RAM work dirs, so their lines stay in the queue host's todo.taken
        with no done row, and nothing else sees them.  Once H's runner is gone and H booted after
        that runner began (the host rule: rebooted), each line of H there with no done row of H
        is host-lost: the queue's killed {gv: {host, cell, line, why}}, rerun once in RUN-r2
        (host_lost_cells, rerun_why host-lost), counted by no stop rule.  Looked at once per
        runner of H (a todo.taken that can't be read: next time).  {gv: entry} of the new ones."""
        q = self.state["queues"][qrun]
        st = self.rs.stops
        rb = getattr(st, "rebooted", None)
        since = (q.get("restarted") or {}).get(h) or q.get("started")
        if rb is None or not since or (q.get("killed_seen") or {}).get(h) == since:
            return {}
        try:
            if not rb(h, since):
                return {}
        except Exception:   # noqa: BLE001 - a rule that can't be asked: nothing is host-lost
            return {}
        taken = self.remote.read(self.qhost, f"{QUEUE}/{qrun}/todo.taken")
        if taken is None:
            return {}
        done = {(dh, gv) for dh, gv in (self.done.get(qrun) or {})}
        killed = q.setdefault("killed", {})
        new = {}
        for line in taken.splitlines():
            ln, _, th = line.split("#", 1)[0].strip().rpartition(" ")
            if not ln or th != h:
                continue
            cell = "holt:" + re.sub(r"(=[0-9.]+)?(%[0-9.]+)?$", "", ln.strip())
            gv = gv_of(cell)
            if (h, gv) in done or gv in killed:
                continue
            killed[gv] = new[gv] = {"host": h, "line": ln.strip(), "cell": cell,
                                    "why": f"{h} rebooted under it (its runner began {since}): in {qrun}'s "
                                           "todo.taken with no done row"}
        q.setdefault("killed_seen", {})[h] = since
        self.state.save()
        add = getattr(st, "add_host_lost", None)
        for gv, k in sorted(new.items()):
            cell = k["cell"]
            self.log(f"queue {qrun}: {cell} on {h}: host lost ({k['why']}): not counted"
                     + (", rerun once" if not qrun.endswith("-r2") else ""))
            if add is not None:
                try:
                    add(f"{cell} killed on {h}: host lost ({k['why']}): no stop rule counts it")
                except Exception:   # noqa: BLE001 - only the report's note
                    pass
        return new

    def done_rows(self, qrun):
        """The queue's done rows read so far (a host that can't be read this time keeps what
        an earlier look read)."""
        out = self.done.setdefault(qrun, {})
        for h in self.hosts:
            t = self.remote.read(h, f"{QUEUE}/{qrun}/done") or ""
            head = None
            for line in t.splitlines():
                f = line.split("\t")
                if head is None:
                    head = f
                    continue
                if len(f) == len(head):
                    r = dict(zip(head, f))
                    out[(r.get("host") or h, r.get("gv"))] = r
        return out

    def row_for(self, host, origin):
        """(row or None, known): known is False when the host's ledger couldn't be read."""
        if origin in self.rows:
            return self.rows[origin], True
        got = self.remote.ledger_rows(host, self.ledger_path(host), self.run_id)
        if got is None:
            return None, False
        for r in got:
            if r.get("origin"):
                self.rows[r["origin"]] = r
        return self.rows.get(origin), True

    def take_row(self, qrun, attempt, h, gv, d):
        """(row, known) of a done job: its ledger row, else one written now (R7)."""
        row, known = self.row_for(h, f"ht1:{qrun}:{gv}:{attempt}")
        if not known:
            return None, False
        if row is None:
            return self.backfill(qrun, attempt, h, gv, d)
        return row, True

    def judge_lost(self, row, host):
        """Why ROW (HARNESS or LOST) is its host's going away under it (hostjob.host_lost: the host
        rule over the row's start to its end; a look at the host first when it knows of nothing),
        kept in holt.json's host_lost {origin: why} for the rerun (once, rerun_why host-lost); or None."""
        if row.get("verdict") not in ("HARNESS", "LOST"):
            return None
        lost = self.state["host_lost"] or {}
        o = row.get("origin") or ""
        if o in lost:
            return lost[o]
        from . import hostjob
        why = hostjob.host_lost(self.rs.stops, host, row.get("start"), row.get("t"),
                                seen=getattr(self.remote, "seen", None), text=row.get("note"))
        if why and o:
            lost[o] = why
            self.state["host_lost"] = lost
            self.log(f"{row.get('cell')} {row.get('verdict')} on {host}: host lost ({why}): not counted"
                     + (", rerun once" if o.endswith(":1") else ""))
        return why

    def feed(self, row, host):
        res = {"cell": row.get("cell"), "family": "holt", "part": None, "verdict": row.get("verdict"),
               "class": row.get("class"), "needs": row.get("needs") or {}, "os": os_of(self.sf, row.get("cell") or ""),
               "host": host, "flake": row.get("class") == "known-flake", "start": row.get("start"), "end": row.get("t")}
        try:
            res["host_lost"] = self.judge_lost(row, host)
        except Exception:   # noqa: BLE001 - a rule that can't be asked: the row counts as it would
            res["host_lost"] = None
        try:
            return self.rs.stops.feed(res)
        except Exception:   # a stop engine that fails stops nothing
            return "ok"

    def act(self, qrun, what):
        """A stop rule's answer for queue QRUN."""
        if what == "ok":
            return
        if what.startswith("stop-os"):
            osn = what.split(" ", 1)[1] if " " in what else ""
            guests = {base_guest(f"holt:{x}") for x in (self.state["queues"][qrun].get("todo") or [])
                      if os_of(self.sf, f"holt:{x}") == osn}
            took = self.remote.take_lines(self.qhost, f"{QUEUE}/{qrun}/todo", guests, what)
            self.taken |= {(qrun, gv_of("holt:" + x.split("#")[0].strip())) for x in took}
            self.stops.append(f"{what}: took {len(took)} line(s) out of {qrun}'s todo")
            self.log(f"queue {qrun}: {what}: {', '.join(took) or 'nothing left to take'}")
        elif what in STOPPING:
            for h in self.hosts:
                self.remote.touch(h, f"{QUEUE}/{qrun}/stop")
            why = self.stop_why(what)   # (the rule that fired, in the log and the stop: never a stop with no cause named)
            self.stops.append(f"{what}: touched {QUEUE}/{qrun}/stop on {', '.join(self.hosts)}"
                              + (f" ({why})" if why else ""))
            self.log(f"queue {qrun}: {what}: {why or 'the stop rules gave no reason'}: nothing more starts")

    def stop_why(self, what):
        """The stop engine's own words for its latest WHAT (the last of its stops that begins
        "WHAT: "), else the run-wide guards' reasons, else ''."""
        try:
            for s in reversed(list(getattr(self.rs.stops, "stops", None) or [])):
                if isinstance(s, str) and s.startswith(what + ": "):
                    return s[len(what) + 2:]
            from . import hostjob
            g = hostjob.guards_of(self.rs)
            return "; ".join(g.why()) if g is not None else ""
        except Exception:   # noqa: BLE001 - only the words
            return ""

    def said_end(self, qrun, h):
        """The runner's log ends as ht1-queue.sh ends: queue done, stopped or aborted."""
        t = (self.remote.read(h, f"{QUEUE}/{qrun}/runner-{h}.log") or "").strip().splitlines()
        return bool(t) and re.search(r"\b(queue done|stopped|aborted)\b", t[-1]) is not None

    def wait_queue(self, qrun, attempt):
        """Until every runner has ended (it reads as gone, and its log says so or it reads as
        gone twice: a failed ssh is never 'gone'), feeding each done job's row to the stop
        rules as it lands."""
        q = self.state["queues"][qrun]
        seen = set(tuple(x) for x in q.get("seen") or [])
        gone = {}
        end = self.clock() + QUEUE_TIMEOUT_S
        while True:
            done = self.done_rows(qrun)
            for (h, gv), d in sorted(done.items()):
                if (h, gv) in seen:
                    continue
                row, known = self.take_row(qrun, attempt, h, gv, d)
                if not known:
                    continue   # (its ledger couldn't be read: looked at next time)
                seen.add((h, gv))
                if row is not None:
                    self.act(qrun, self.feed(row, h))
                    if attempt == 1 and (row.get("extra") or {}).get("rescuer_made"):
                        try:
                            self.rekey_made(qrun, h, row, seen | set(done))   # (done: landed, or landing)
                        except Exception as e:   # noqa: BLE001 - its rows land as they would without it
                            self.notes.append(f"{h}: not keyed again at the one-time rescuer: {type(e).__name__}: {e}")
            q["seen"] = sorted(seen)
            self.state.save()
            self.host_checks(qrun)
            self.check_guards(qrun)
            live = []
            for h, r in (q.get("runners") or {}).items():
                if not r.get("pid"):
                    continue
                a = self.remote.alive(h, r["pid"], "ht1-queue.sh")
                if a is False:
                    gone[h] = gone.get(h, 0) + 1
                    if gone[h] < 2 and not self.said_end(qrun, h):
                        live.append(h)
                        continue
                    self.reboot_killed(qrun, h)   # (first: the lines its old runner had under way)
                    if self.restart_runner(qrun, h):   # (True: started again; "wait": once its host settles)
                        gone[h] = 0
                        live.append(h)
                else:
                    gone[h] = 0
                    live.append(h)   # (alive, or the host couldn't be asked)
            if not live:
                for h, r in (q.get("runners") or {}).items():
                    tail = (self.remote.read(h, r.get("out") or "") or "").strip().splitlines()
                    if tail and not any(x for x in done if x[0] == h):
                        self.notes.append(f"queue {qrun}: {h}'s runner ended with no job: {tail[-1][:200]}")
                break
            if self.clock() > end:
                who = ", ".join(f"{h} pid {(q.get('runners') or {}).get(h, {}).get('pid')}" for h in live)
                self.stops.append(f"stop-starts: queue {qrun} still running after {QUEUE_TIMEOUT_S // 3600} h "
                                  f"(its runners, {who}, start nothing more and end after their jobs)")
                for h in self.hosts:
                    self.remote.touch(h, f"{QUEUE}/{qrun}/stop")
                break
            self.sleep(self.poll)
        # One last look: a row may have landed after the runner's done row; and every done
        # job's row in hand (on a resume, from the hosts' ledgers: none is written twice).
        for (h, gv), d in sorted(self.done_rows(qrun).items()):
            origin = f"ht1:{qrun}:{gv}:{attempt}"
            if (h, gv) in seen:
                if origin not in self.rows:
                    self.row_for(h, origin)
                continue
            known = False
            for i in range(3):
                row, known = self.take_row(qrun, attempt, h, gv, d)
                if known:
                    break
                self.sleep(self.poll)
            if not known:
                self.problems.append(f"queue {qrun}: {gv}'s row on {h} couldn't be read ({h}'s ledger): "
                                     "none written over it")
                continue
            seen.add((h, gv))
            if row is not None:
                self.act(qrun, self.feed(row, h))
        q["seen"] = sorted(seen)
        left = [x for x in (self.remote.read(self.qhost, f"{QUEUE}/{qrun}/todo") or "").splitlines()
                if x.split("#", 1)[0].strip()]
        if left:
            self.notes.append(f"queue {qrun}: {len(left)} todo line(s) left when its runners ended: "
                              + ", ".join(left[:10]))
        q["finished"] = utc_now()
        self.state.save()

    def check_guards(self, qrun):
        """A run-wide stop of the composite this run is part of (RunGuards): nothing more
        starts, once per queue."""
        q = self.state["queues"][qrun]
        if q.get("guards_tripped"):
            return
        try:
            from . import hostjob
            trip = hostjob.tripped(self.rs)
        except Exception:   # noqa: BLE001 - a guard that can't be read stops nothing
            trip = False
        if trip:
            q["guards_tripped"] = utc_now()
            self.state.save()
            for h in self.hosts:
                self.remote.touch(h, f"{QUEUE}/{qrun}/stop")
            try:
                why = "; ".join(hostjob.guards_of(self.rs).why())
            except Exception:   # noqa: BLE001 - only the words
                why = ""
            why = f" ({why})" if why else ""
            self.stops.append(f"stop-starts: a run-wide stop rule tripped{why}: touched {QUEUE}/{qrun}/stop on "
                              f"{', '.join(self.hosts)}")
            self.log(f"queue {qrun}: a run-wide stop rule tripped{why}: nothing more starts")

    def write_allows(self):
        """{gv: (cell, bytes)}: the plan's cells with a write allowance (write_allow_bytes)."""
        out = {}
        for pc in getattr(self.plan, "cells", None) or ():
            b = write_allow_bytes(self.sf, pc.cell)
            if b > 0:
                out[gv_of(pc.cell)] = (pc.cell, b)
        return out

    def sync_holds(self, qrun):
        """The swap rule's and the host rule's held hosts (the stop engine's held(), {host: why}) for queue QRUN:
        ht1-queue.sh's queue/RUN/max-HOST at 0 on a held host, so its runner starts nothing new
        (its jobs go on; the other runners take the todo's lines, and a line only it may run
        waits), and back to what it was (none: the host's holt_jobs) when the hold ends.  An
        empty todo ends every hold: a held runner with nothing left to start may end."""
        fn = getattr(self.rs.stops, "held", None)
        try:
            held = {str(h): str(w) for h, w in (fn() if fn is not None else {}).items()}
        except Exception:   # noqa: BLE001 - holds that can't be read hold nothing
            held = {}
        q = (self.state["queues"] or {}).get(qrun)
        if q is None:
            return
        applied = dict(q.get("held") or {})
        runners = q.get("runners") or {}
        held = {h: w for h, w in held.items() if h in runners}
        if not held and not applied:
            return
        why_end = "the hold is over"
        if held:
            todo = self.remote.read(self.qhost, f"{QUEUE}/{qrun}/todo")
            if todo is not None and not any(x.split("#", 1)[0].strip() for x in todo.splitlines()):
                held, why_end = {}, "the todo is empty"
        changed = False
        for h in sorted(set(held) - set(applied)):
            path = f"{QUEUE}/{qrun}/max-{h}"
            prev = self.remote.read(h, path)
            try:
                self.remote.write(h, path, "0\n")
            except OSError as e:
                self.log(f"queue {qrun}: hold on {h} not set: {e}")
                continue
            applied[h] = {"prev": prev if (prev or "").strip() else None, "why": held[h], "t": utc_now()}
            changed = True
            self.log(f"queue {qrun}: HOLD {h}: {held[h]}: its runner starts nothing new (max-{h} 0); "
                     "the other runners take its lines")
        for h in sorted(set(applied) - set(held)):
            path = f"{QUEUE}/{qrun}/max-{h}"
            try:
                self.remote.write(h, path, applied[h].get("prev") or f"{max_jobs(h)}\n")
            except OSError as e:
                self.log(f"queue {qrun}: hold on {h} not lifted (tried again next poll): {e}")
                continue
            del applied[h]
            changed = True
            self.log(f"queue {qrun}: {h}: {why_end}: its runner starts again")
        if changed:
            q["held"] = applied
            self.state.save()

    def host_checks(self, qrun):
        """The host-wide stop rules: a look at the host (the host rule, run.HostWatch: one that
        stops answering or reboots is held alone), swap-out (HostHolds: it holds that host alone;
        then sync_holds), this run's bytes (R22).
        The bytes come by tag, with the part of each a failed run's kept state wrote (the
        R to K move, jobs.log's keep=): that part is fed as KEPT, and with a write allowance
        in the plan the allowed cells' own (allowed_bytes, of their bytes less KEPT) as
        ALLOWED: the rule counts the rest."""
        prefix = f"ht1:{self.run_id}"
        allow = self.write_allows()
        seen_fn = getattr(self.remote, "seen", None)
        for h in self.hosts:
            if seen_fn is not None:   # the host rule (run.HostWatch): no answer, a reboot
                from . import hostjob
                try:
                    seen = seen_fn(h)
                except Exception:   # noqa: BLE001 - a look that fails is no answer
                    seen = None
                self.act(qrun, hostjob.feed_seen(self.rs.stops, h, seen))
                if seen is None:
                    continue
            sw = self.remote.pswpout(h)
            if sw is not None and h not in self.swap0:
                self.swap0[h] = sw
                self.state["swap0"] = self.swap0
            allowed, kept, ours, old = None, 0, None, False
            try:
                got = self.remote.tag_bytes(h, prefix, self.t0 - 60, by_tag=True, kept=True)
            except TypeError:   # a remote with no kept: as before it had one
                old = True
            else:
                by, kb = got if isinstance(got, tuple) and len(got) == 2 else (None, None)
                if isinstance(by, dict):
                    kb = kb if isinstance(kb, dict) else {}
                    ours, kept = sum(by.values()), sum(kb.values())
                    if allow:
                        allowed = allowed_bytes({t: n - kb.get(t, 0) for t, n in by.items()}, prefix, allow)
            if old:
                plain = not allow
                if allow:
                    try:
                        by = self.remote.tag_bytes(h, prefix, self.t0 - 60, by_tag=True)
                    except TypeError:   # a remote with no by_tag: no allowance, the plain sum as before
                        plain = True
                    else:
                        ours = sum(by.values()) if isinstance(by, dict) else None
                        allowed = allowed_bytes(by, prefix, allow) if isinstance(by, dict) else None
                if plain:
                    ours = self.remote.tag_bytes(h, prefix, self.t0 - 60)
            if ours is not None:
                if allowed:
                    self.allowed[h] = dict(allowed)
                else:
                    self.allowed.pop(h, None)
                if (self.state["write_allowed"] or {}) != self.allowed:
                    self.state["write_allowed"] = {k: dict(v) for k, v in self.allowed.items()}
                km = self.__dict__.setdefault("kept", {})
                if kept:
                    km[h] = kept
                else:
                    km.pop(h, None)
                if (self.state["write_kept"] or {}) != km:
                    self.state["write_kept"] = dict(km)
            kw = {"allowed": allowed} if allowed else {}
            if kept:
                kw["kept"] = kept
            try:
                what = self.rs.stops.feed_host(h, swap_out_bytes=(sw - self.swap0[h]) if sw is not None else None,
                                               ours_bytes=ours, **kw)
            except Exception as e:   # noqa: BLE001 - a feed that fails stops nothing, but says so (once)
                what = "ok"
                msg = f"host checks on {h}: the stop rules' feed failed: {type(e).__name__}: {e}"
                seen = self.__dict__.setdefault("_feed_errors", set())
                if msg not in seen:
                    seen.add(msg)
                    self.log(msg)
            self.act(qrun, what)
        self.sync_holds(qrun)

    def k_of(self, qrun, gv):
        recs = os.environ.get("HT_RECORDS") or HOLT_L
        return os.path.join(recs, qrun, gv)

    def backfill(self, qrun, attempt, host, gv, d):
        """R7: the row of a job whose hook left none.  When K holds its records (the hook
        failed or timed out) the row is built from them, as the hook would have; otherwise
        (a refusal before ht_begin, a run that died) rows_holt.backfill_row's LOST or
        HARNESS row.  Either way the row fed on is the one the ledger holds after, read
        back by its origin: (row, known), known False when that read failed (the next
        look reads it, and writes nothing again)."""
        try:
            rc = int(d.get("rc"))
        except (TypeError, ValueError):
            rc = None
        k = self.k_of(qrun, gv)
        kept = (self.remote.read(host, os.path.join(k, "logs", "kept.txt")) or "").strip()
        kept = kept.splitlines()[0] if kept else ""
        origin = f"ht1:{qrun}:{gv}:{attempt}"
        cells_name = "cells-r2.json" if attempt == 2 else "cells.json"
        if rc is not None and rc < 128 and not kept.startswith("swept:") and \
                self.remote.exists(host, os.path.join(k, "report.txt")):
            kind = entry_of(self.sf, cell_of(gv)).get("kind") or "linux"
            env = {"T2_HOST_ROOT": self.host_root(), "T2_RUN": self.run_id,
                   "T2_CELLS": os.path.join(self.rs.host_run_dir(host), cells_name)}
            args = ["--k", k, "--run", qrun, "--gv", gv, "--driver", KIND_DRIVER.get(kind, "ht1.sh"),
                    "--rc", str(rc), "--code", str(rc)]
            ids = self.remote.row_from_k(host, self.t2_host(host), args, env)
            if ids:
                self.rows.pop(origin, None)
                row, known = self.row_for(host, origin)
                if row is not None or not known:
                    self.log(f"queue {qrun}: {gv} on {host}: its hook left no row; built from {k}: {ids[0]}")
                    return row, known
        cells = self.cells_entry(host, qrun, gv)
        start = None
        try:
            from . import parse_utc
            e = parse_utc(d.get("end_utc"))
            rs_ = float(d.get("run_s")) if str(d.get("run_s", "-")).replace(".", "", 1).isdigit() else 0.0
            start = utc_now(e - rs_) if e else None
        except Exception:
            start = None
        cp = (self.rs.copy or {}).get(host) or {}
        row = backfill_row(host, self.run_id, qrun, gv, attempt, rc, kept, start, cells, self.rs.word.text,
                           copy=cp, logs=f"{host}:{k}")
        try:
            ids = self.remote.append_rows(host, self.t2_host(host), [row], {"T2_HOST_ROOT": self.host_root()})
        except (OSError, NoCopy) as e:
            self.notes.append(f"{host}: no backfill row for {gv}: {e}")
            self.rows[origin] = row
            return row, True
        row["id"] = ids[0] if ids else None
        # (Same origin there already, a row that landed late: the ledger's wins.)
        self.rows.pop(origin, None)
        got, known = self.row_for(host, origin)
        if not known:
            return None, False
        row = got if got is not None else row
        self.rows[origin] = row
        self.log(f"queue {qrun}: {gv} on {host} left no row (exit {rc}): {row.get('verdict')} row {row.get('id')}")
        return row, True

    def cells_entry(self, host, qrun, gv):
        name = "cells-r2.json" if qrun.endswith("-r2") else "cells.json"
        p = os.path.join(self.rs.mac_dir, f"{name[:-5]}-{host}.json")
        try:
            with open(p, encoding="utf-8") as f:
                return (json.load(f).get("cells") or {}).get(gv)
        except (OSError, ValueError):
            return None

    # --- the whole run ---------------------------------------------------------------------------------
    def due(self, lost=False):
        """The plan's due holt cells, less those whose platform's build failed (lost_plats:
        an +img cell runs on the pin's binaries, never lost); LOST: those alone."""
        gone = self.state["lost_plats"] or {}
        return [pc for pc in self.plan.cells if pc.status == "due" and pc.family == "holt"
                and lost == bool(gone and not is_img(pc.cell) and plat_of(self.sf, pc.cell) in gone)]

    def run(self):
        start = self.state["start"] or utc_now()
        self.state["start"] = start
        pcs = self.due()
        if not pcs:   # (a resume's cells may all be a failed build's: those are skipped, exit 1)
            return self.result(start, [], exit_=None if self.due(lost=True) else 0)
        self.code = (self.plan.set or {}).get("holt_code") or keys.holt_code(self.ctx.src)
        self.state["code"] = self.code
        self.catch_signals()
        try:
            return self.flow(start, pcs)
        except NoCopy as e:   # no copy to run from (a resume of a run that had none)
            self.stops.append(f"stop-family: {e}")
            return self.result(start, self.due(), exit_=1)
        except KeyboardInterrupt as e:
            self.interrupted(e.args[0] if e.args and e.args[0] else "SIGINT")
            raise
        finally:
            self.restore_signals()

    # -- signals: Ctrl-C, TERM or HUP on the Mac's t2 stops the queues' starts --
    def catch_signals(self):
        """SIGTERM and SIGHUP as Ctrl-C (KeyboardInterrupt), for run()'s life; in the main
        thread only (a composite's child thread has the composite's guards instead)."""
        if threading.current_thread() is not threading.main_thread():
            return

        def handler(signum, frame):
            raise KeyboardInterrupt(signal.Signals(signum).name)
        for s in (signal.SIGTERM, signal.SIGHUP):
            try:
                self._sigs.setdefault(s, signal.getsignal(s))
                signal.signal(s, handler)
            except (ValueError, OSError, AttributeError):
                self._sigs.pop(s, None)

    def restore_signals(self):
        for s, h in list(self._sigs.items()):
            try:
                signal.signal(s, h if h is not None else signal.SIG_DFL)
            except (ValueError, OSError, TypeError):
                pass
        self._sigs = {}

    def interrupted(self, why):
        """Ctrl-C (or SIGTERM, SIGHUP) on the Mac's t2: every queue of this run that has a
        todo gets its stop on every host (QUEUE/QRUN/stop, saying why: the runners log it),
        so its runners start nothing more; their running jobs finish, and `t2 resume RUN`
        collects their rows (holt.json keeps the stop: no rerun queue starts).  Further
        signals are ignored while the stops are written."""
        if threading.current_thread() is threading.main_thread():
            for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                try:
                    self._sigs.setdefault(s, signal.getsignal(s))
                    signal.signal(s, signal.SIG_IGN)
                except (ValueError, OSError, AttributeError):
                    pass
        text = f"t2 on the Mac: {why}: nothing more starts\n"
        qs = [q for q, d in sorted((self.state["queues"] or {}).items())
              if d.get("todo_written") and not d.get("finished")]
        for qrun in qs:
            path = f"{QUEUE}/{qrun}/stop"
            ok = []
            for h in self.hosts:
                try:
                    self.remote.write(h, path, text)
                    ok.append(h)
                except Exception as e:   # noqa: BLE001 - one host's failure stops none of the others
                    self.log(f"queue {qrun}: {why}: couldn't write {h}:{path}: {str(e)[:200]}")
            self.log(f"queue {qrun}: {why}: wrote {path} on {', '.join(ok) or 'no host'}: nothing more starts "
                     f"(running jobs finish; `t2 resume {self.run_id}` collects their rows)")
        if not qs:   # (no queue under way: a resume starts them as it would have)
            self.log(f"{why}: no queue of {self.run_id} under way: nothing on the hosts to stop")
            return
        stop = (f"stop-starts: interrupted ({why}): wrote {', '.join(QUEUE + '/' + q + '/stop' for q in qs)} "
                f"on {', '.join(self.hosts)}")
        self.stops.append(stop)
        self.state["interrupted"] = {"at": utc_now(), "why": why, "queues": qs, "stop": stop}

    def flow(self, start, pcs):
        if self.state["all_img"] is None:
            self.state["all_img"] = all(is_img(pc.cell) for pc in pcs)
        if not self.state["built"]:
            built = [pc for pc in pcs if not is_img(pc.cell)]   # +img cells: the pin's binaries
            why = (self.ensure_holt(built) or self.all_lost()) if built else None
            if why:
                self.stops.append("stop-family: " + why)
                return self.result(start, pcs, exit_=1)
            pcs = self.due()   # (less the cells of a platform whose build failed)
            built = [pc for pc in pcs if not is_img(pc.cell)]
            self.state["plats"] = needed_platforms(self.sf, [pc.cell for pc in built])
            self.state["built"] = utc_now()
        if not self.state["gated"]:
            self.ensure_pins(pcs)
            self.ensure_imgsums(pcs)
            # (+img cells alone have nothing of the checkout's bko-holt to gate; a sweep's
            # options.gate_fresh still runs the holt layers, by gate.gate_plan.)
            why = None if self.state["all_img"] and not self.opts.get("gate_fresh") else self.gate()
            if why:
                self.stops.append("stop-family: " + why)
                return self.result(start, pcs, exit_=1)
            self.replan()
            # (A re-plan can add +img cells: their pins, then their sums.)
            first = {pc.cell for pc in pcs}
            if any(is_img(pc.cell) and pc.cell not in first for pc in self.due()):
                self.ensure_pins(self.due())
            # (Cells the re-plan added may need image sums of their own: then once more.)
            if self.ensure_imgsums(self.due()):
                self.replan(pull=False)
            self.state["gated"] = utc_now()
        pcs = self.due()
        if not pcs:
            self.notes.append("nothing due after the gate's re-plan")
            return self.result(start, [], exit_=None if self.due(lost=True) else 0)
        self.state["all_img"] = all(is_img(pc.cell) for pc in pcs)
        # Platforms the re-plan's cells need that the first builds didn't make (R1, R15).
        built = [pc for pc in pcs if not is_img(pc.cell)]
        more = [p for p in needed_platforms(self.sf, [pc.cell for pc in built]) if p not in (self.state["plats"] or [])]
        if more:
            why = self.ensure_holt(built) or self.all_lost()
            if why:
                self.stops.append("stop-family: " + why)
                return self.result(start, pcs, exit_=1)
            pcs = self.due()
            more = [p for p in more if p not in (self.state["lost_plats"] or {})]
            self.state["plats"] = sorted(set(self.state["plats"] or []) | set(more))
        if not self.state["cells"]:
            self.state["cells"] = {h: p for h, p in self.rs.write_cells(self.plan).items()}
        # attempt 1
        q1 = self.run_id
        self.start_queue(q1, pcs, "cells.json")
        self.wait_queue(q1, 1)
        # attempt 2: the FAILs (contract 1.9: a FAIL reruns once), and the PASSes that couldn't
        # count because their run made the one-time rescuer, unless a stop rule says no
        hlost = self.host_lost_cells(q1)   # (first: failed()'s note leaves them out)
        failed = self.failed(q1)
        made = self.made(q1)
        failed.update(made)
        hlost = {c: v for c, v in hlost.items() if c not in failed}
        failed.update(hlost)
        if failed:
            why = self.no_rerun(q1)
            st = self.rs.stops
            if why:
                self.notes.append(f"no rerun of {', '.join(sorted(failed))}: {why}")
                failed = {}
            stopped = set(getattr(st, "stopped_os", None) or ())
            off = sorted(c for c in failed if os_of(self.sf, c) in stopped)
            if off:
                self.notes.append(f"not rerun ({', '.join(sorted({os_of(self.sf, c) for c in off}))} stopped by "
                                  f"stop-os): {', '.join(off)}")
                failed = {c: v for c, v in failed.items() if c not in off}
        q2 = f"{self.run_id}-r2"
        if failed and not (self.state["queues"].get(q2) or {}).get("todo_written"):
            for c in self.sum_made(q1, failed):
                if c in made:
                    del failed[c]
        if failed:
            reruns = {}
            for cell, (host, gv) in failed.items():
                r1 = self.rows.get(f"ht1:{q1}:{gv}:1") or {}
                reruns[cell] = {"attempt": 2, "rerun_of": r1.get("id"), "flake": None}
                if cell in made:
                    reruns[cell]["rerun_why"] = "rescuer-made"
                elif cell in hlost:
                    reruns[cell]["rerun_why"] = "host-lost"
            if made.keys() & reruns.keys():
                self.notes.append("rerun (attempt 1 made its one-time rescuer, or ran before its sum, so its row couldn't count): "
                                  + ", ".join(sorted(made.keys() & reruns.keys())))
            if hlost.keys() & reruns.keys():
                self.notes.append("rerun (attempt 1's host went away under it: host lost, no stop rule counted it): "
                                  + ", ".join(sorted(hlost.keys() & reruns.keys())))
            if not (self.state["queues"].get(q2) or {}).get("todo_written"):
                self.rs.write_cells(self.plan, attempt=2, reruns=reruns)
            self.start_queue(q2, [pc for pc in self.due() if pc.cell in reruns], "cells-r2.json")
            self.wait_queue(q2, 2)
        return self.result(start, pcs)

    def copies_made(self, qrun):
        """{host: {basename: path}} of the one-time rescuer copies queue QRUN's jobs made
        (their rows' extra.rescuer_made)."""
        out = {}
        for (h, gv), d in sorted((self.done.get(qrun) or {}).items()):
            r = self.rows.get(f"ht1:{qrun}:{gv}:1") or {}
            for p in (r.get("extra") or {}).get("rescuer_made") or []:
                out.setdefault(h, {})[os.path.basename(p)] = p
        return out

    @staticmethod
    def copies_of(row, copies):
        """The one-time rescuer copies ROW's run couldn't count by: the ones it made itself,
        else those of COPIES ({basename: path}, made on its host by another job of the
        queue) that its rescuer inputs key as missing or img:unknown."""
        own = (row.get("extra") or {}).get("rescuer_made") or []
        if own:
            return list(own)
        resc = (row.get("inputs") or {}).get("rescuer") or {}
        return [p for n, p in sorted((copies or {}).items())
                if resc.get("rescuer/" + n) in ("missing", keys.UNKNOWN_IMG)]

    def made(self, qrun):
        """{cell: (host, gv)} of the jobs whose row is a PASS that doesn't count because of a
        one-time rescuer copy made in this queue: its run made it (extra.rescuer_made), or
        another job on its host did and its row keyed it missing or img:unknown (it landed
        before rekey_made could key it at the copy's sum).  The cell's first row that can
        count is a rerun at the copy's sum."""
        out = {}
        copies = self.copies_made(qrun)
        for (h, gv), d in sorted((self.done.get(qrun) or {}).items()):
            r = self.rows.get(f"ht1:{qrun}:{gv}:1") or {}
            if r.get("verdict") == "PASS" and r.get("valid") is False and self.copies_of(r, copies.get(h)):
                out[self.cell_of_gv(gv)] = (h, gv)
        return out

    def rekey_made(self, qrun, host, row, seen):
        """A one-time rescuer copy a job on HOST just made (ROW's extra.rescuer_made), while
        queue QRUN (attempt 1) runs: the copy is summed (ensure_imgsums) and the plan made
        again, and HOST's cells.json entries of the other due cells that boot it, whose rows
        haven't landed (not in SEEN), get their planned key at the copy's sum, so their rows
        can count: every job of theirs on HOST boots that copy from now on (ht1-bsd.sh makes
        it once, under a lock, and never again).  An entry is changed only when the copy is
        all the new key differs by (with it back as missing or img:unknown, the key is the
        planned one).  Returns the cells re-keyed."""
        paths = (row.get("extra") or {}).get("rescuer_made") or []
        facts = getattr(self.ctx, "facts", None)
        f = facts.get(host) if isinstance(facts, dict) else None
        if not paths or not isinstance(f, dict):
            return []
        names = {os.path.basename(p) for p in paths}
        subst = getattr(keys, "_subst", None)
        maker = row.get("cell")

        def boots(pc):
            e = entry_of(self.sf, pc.cell)
            guest = base_guest(pc.cell)
            for x in e.get("rescuer") or []:
                if isinstance(x, str) and not x.startswith(("@", "bin/")):
                    if (subst(x, guest, e.get("plat"), e.get("kind")) if subst else x) in names:
                        return True
            return False
        sibs = [pc for pc in self.due() if pc.cell != maker and (not pc.keys or host in pc.keys)
                and (host, gv_of(pc.cell)) not in seen and boots(pc)]
        if not sibs:
            return []
        resc = f["rescuers"] = f.get("rescuers") or {}
        imgs = resc["images"] = resc.get("images") or {}
        for n in names:
            imgs.setdefault(n, {"sha256": None, "size": None, "mtime": None, "imgsum": None})
        self.ensure_imgsums([pc for pc in self.due() if pc.cell == maker] + sibs)
        if not all((imgs.get(n) or {}).get("sha256") or (imgs.get(n) or {}).get("imgsum") for n in names):
            return []   # (no sum: their rows land invalid, and made() reruns them)
        self.replan(pull=False)
        mac = os.path.join(self.rs.mac_dir, f"cells-{host}.json")
        try:
            with open(mac) as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as e:
            self.notes.append(f"{host}: no cells.json to key again at the one-time rescuer ({e})")
            return []
        cells = doc.get("cells") or {}
        new = {pc.cell: pc for pc in self.due()}
        done = []
        for pc in sibs:
            ent = cells.get(gv_of(pc.cell))
            npc = new.get(pc.cell)
            nk = (npc.keys or {}).get(host) if npc is not None else None
            old = (ent or {}).get("planned_key")
            if not ent or not nk or not old or nk == old:
                continue
            inp = (getattr(npc, "inp", None) or {}).get(host)
            if inp:   # (the copy alone: with it as it was planned, the key is the planned one)
                def back(v):
                    alt = {g: dict(x) for g, x in inp.items() if isinstance(x, dict)}
                    for n in names:
                        if "rescuer/" + n in alt.get("rescuer", {}):
                            alt["rescuer"]["rescuer/" + n] = v
                    return keys.key(pc.cell, alt)
                if old not in (back("missing"), back(keys.UNKNOWN_IMG)):
                    continue
            ent["planned_key"] = nk
            done.append(pc.cell)
        if not done:
            return []
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        try:
            self.remote.write(host, os.path.join(self.rs.host_run_dir(host), "cells.json"), text)
        except OSError as e:
            self.notes.append(f"{host}: cells.json not keyed again at the one-time rescuer: {e}")
            return []
        with open(mac + ".tmp", "w") as fh:
            fh.write(text)
        os.replace(mac + ".tmp", mac)
        self.log(f"queue {qrun}: {host} made the one-time rescuer {', '.join(sorted(names))}: "
                 f"keyed again at its sum: {', '.join(done)}")
        self.notes.append(f"keyed at the one-time rescuer {maker} made on {host} before their rows landed: "
                          + ", ".join(done))
        return done

    def sum_made(self, qrun, cells):
        """The one-time rescuer copies attempt 1 of CELLS made (or ran before the sums of:
        copies_of), put in ctx.facts as facts_host.rescuers would show them and summed
        (ensure_imgsums), then the plan made again, so cells-r2.json's planned keys carry
        their img: sums.  Returns the cells whose copies got no sum (their rows still
        couldn't count)."""
        facts = getattr(self.ctx, "facts", None)
        copies = self.copies_made(qrun)
        want = {}
        for cell, (h, gv) in cells.items():
            r = self.rows.get(f"ht1:{qrun}:{gv}:1") or {}
            for p in self.copies_of(r, copies.get(h)):
                f = facts.get(h) if isinstance(facts, dict) else None
                if isinstance(f, dict):
                    resc = f["rescuers"] = f.get("rescuers") or {}
                    imgs = resc["images"] = resc.get("images") or {}
                    imgs.setdefault(os.path.basename(p), {"sha256": None, "size": None, "mtime": None, "imgsum": None})
                want.setdefault(cell, []).append((h, os.path.basename(p)))
        if not want:
            return []
        if self.ensure_imgsums([pc for pc in self.due() if pc.cell in want]):
            self.replan(pull=False)

        def summed(h, n):
            f = facts.get(h) if isinstance(facts, dict) else None
            im = (((f or {}).get("rescuers") or {}).get("images") or {}).get(n) or {}
            return bool(im.get("sha256") or im.get("imgsum"))
        bad = sorted(c for c, hn in want.items() if not all(summed(h, n) for h, n in hn))
        if bad:
            self.notes.append("no sum of the one-time rescuer attempt 1 made, so no rerun of it can count: "
                              + ", ".join(bad))
        return bad

    def failed(self, qrun):
        """{cell: (host, gv)} of the jobs whose row is a FAIL.  A HARNESS or LOST result
        (a refusal, a signal, a sweep) says nothing of the cell: it isn't rerun, and an
        attempt 2 that passed after it would read as FLAKY-FAIL."""
        out, other = {}, []
        for (h, gv), d in sorted((self.done.get(qrun) or {}).items()):
            if str(d.get("rc")) == "0":
                continue
            cell = self.cell_of_gv(gv)
            r = self.rows.get(f"ht1:{qrun}:{gv}:1") or {}
            if r.get("verdict") == "FAIL":
                out[cell] = (h, gv)
            elif f"ht1:{qrun}:{gv}:1" not in (self.state["host_lost"] or {}):   # (those: host_lost_cells)
                other.append(f"{cell} {r.get('verdict') or 'no row'} (exit {d.get('rc')})")
        if other:
            self.notes.append("not rerun (reruns are for FAILs): " + ", ".join(other))
        return out

    def host_lost_cells(self, qrun):
        """{cell: (host, gv)} of attempt 1's HARNESS and LOST rows that are host-lost (judge_lost):
        rerun once, rerun_why host-lost (a pass then is a PASS)."""
        out = {}
        for (h, gv), d in sorted((self.done.get(qrun) or {}).items()):
            o = f"ht1:{qrun}:{gv}:1"
            r = self.rows.get(o) or {}
            if r.get("verdict") in ("HARNESS", "LOST") and o not in (self.state["host_lost"] or {}):
                try:   # (judged again: one that landed before its host went away is host-lost now)
                    self.judge_lost(r, h)
                except Exception:   # noqa: BLE001 - a rule that can't be asked: no rerun of it
                    pass
            if o in (self.state["host_lost"] or {}) and r.get("verdict") in ("HARNESS", "LOST"):
                out[self.cell_of_gv(gv)] = (h, gv)
        done = {g for _, g in (self.done.get(qrun) or {})}
        for gv, k in sorted((((self.state["queues"] or {}).get(qrun) or {}).get("killed") or {}).items()):
            if gv not in done:   # (a reboot killed its job: no done row, reboot_killed)
                out[k.get("cell") or self.cell_of_gv(gv)] = (k.get("host"), gv)
        return out

    def all_lost(self):
        """Why the run stops when a platform's failed build (lost_plats) left no due cell to
        run, else None."""
        gone = self.state["lost_plats"] or {}
        if gone and not self.due():
            return "; ".join(gone[p] for p in sorted(gone))
        return None

    def no_rerun(self, qrun):
        st = self.rs.stops
        for flag, what in (("stop_run", "stop-run"), ("stop_family", "stop-family"), ("stop_starts", "stop-starts")):
            if getattr(st, flag, False):
                return f"{what} stopped the run"
        if self.state["interrupted"]:
            return f"the run was interrupted ({self.state['interrupted'].get('why')})"
        for h in self.hosts:
            for f in ("abort", "stop"):
                if self.remote.exists(h, f"{QUEUE}/{qrun}/{f}"):
                    return f"{h}:{QUEUE}/{qrun}/{f} is there"
        return None

    def cell_of_gv(self, gv):
        for pc in self.plan.cells:
            if gv_of(pc.cell) == gv:
                return pc.cell
        return cell_of(gv)

    def result(self, start, pcs, exit_=None):
        cells, bad = [], []
        stopped = bool(self.stops or (getattr(self.rs.stops, "stops", None) or []))
        for pc in pcs:
            gv = gv_of(pc.cell)
            att = []
            for qrun, a in ((self.run_id, 1), (f"{self.run_id}-r2", 2)):
                r = self.rows.get(f"ht1:{qrun}:{gv}:{a}")
                hd = next(((h, d) for (h, g), d in (self.done.get(qrun) or {}).items() if g == gv), None)
                if r or hd:
                    att.append((r, hd))
            if not att:
                killed = (((self.state["queues"] or {}).get(self.run_id) or {}).get("killed") or {}).get(gv)
                if any(g == gv for _, g in self.taken):
                    outcome, why = "skipped", "stop-os took it out of the todo"
                elif killed:
                    outcome, why = "no-row", f"host lost: {killed.get('why')}"
                    bad.append(f"{pc.cell} host lost, not rerun")
                elif stopped:
                    outcome, why = "skipped", "a stop rule stopped the run first"
                else:
                    outcome, why = "not-started", "due, but no runner ran it"
                    bad.append(f"{pc.cell} not started")
                cells.append({"cell": pc.cell, "outcome": outcome, "rule": pc.rule, "reasons": list(pc.reasons or [])
                              + [why], "rows": [], "verdict": None, "gm": 0.0, "wall_s": 0, "gb": None,
                              "host": None})
                continue
            last = att[-1][0]
            rows = [r.get("id") for r, _ in att if r and r.get("id")]
            host = (att[-1][1] or (None,))[0] or (last or {}).get("host")
            verdict = (last or {}).get("verdict")
            c = {"cell": pc.cell, "outcome": "ran" if last else "no-row", "rule": pc.rule,
                 "reasons": list(pc.reasons or []), "rows": rows, "verdict": verdict,
                 "class": (last or {}).get("class"), "valid": (last or {}).get("valid"),
                 "gm": round(sum(float((r or {}).get("gm") or 0) for r, _ in att), 2),
                 "wall_s": sum(int((r or {}).get("wall_s") or 0) for r, _ in att), "gb": None,
                 "host": host, "attempts": len(att)}
            cells.append(c)
            if last is None:
                bad.append(f"{pc.cell} no row")
            elif verdict != "PASS":
                bad.append(f"{pc.cell} {verdict}")
            elif last.get("valid") is False:
                note = str(last.get("note") or "")
                bad.append(f"{pc.cell} PASS, but its row {last.get('id')} doesn't count (valid false: {note[:140]})")
        # The cells of a platform whose bko-holt build failed (lost_plats): skipped, saying why.
        gone = self.state["lost_plats"] or {}
        seen = {pc.cell for pc in pcs}
        for pc in self.due(lost=True):
            if pc.cell in seen:
                continue
            p = plat_of(self.sf, pc.cell)
            cells.append({"cell": pc.cell, "outcome": "skipped", "rule": pc.rule,
                          "reasons": list(pc.reasons or []) + [f"no bko-holt {p}: {gone[p]}"], "rows": [],
                          "verdict": None, "gm": 0.0, "wall_s": 0, "gb": None, "host": None})
            bad.append(f"{pc.cell} skipped (no bko-holt {p})")
        lost_notes = []
        for p in sorted(gone):
            n = [pc.cell for pc in self.due(lost=True) if plat_of(self.sf, pc.cell) == p and pc.cell not in seen]
            if n:
                lost_notes.append(f"{gone[p]}: {len(n)} cell(s) skipped ({', '.join(n[:8])}"
                                  + (f", and {len(n) - 8} more" if len(n) > 8 else "") + ")")
        for c in self.gate_cells:
            if (c.get("outcome") or "ran") == "ran" and c.get("verdict") not in ("PASS", None):
                bad.append(f"{c.get('cell')} {c.get('verdict')}")
        cells = [dict(c, outcome=c.get("outcome") or "ran") for c in self.gate_cells] + cells
        notes = list(self.notes) + lost_notes + list(self.problems)
        if bad:
            notes.append("not passed: " + "; ".join(bad[:12]) + (f"; and {len(bad) - 12} more" if len(bad) > 12 else ""))
        ex = exit_ if exit_ is not None else (1 if (bad or self.stops or self.problems or stopped) else 0)
        stops = list(self.stops) + [s for s in (getattr(self.rs.stops, "stops", None) or []) if s not in self.stops]
        cp = None
        try:
            cp = next((c.get("id") for c in (self.rs.copy or {}).values()), None)
        except AttributeError:
            cp = None
        return {"run": self.run_id, "word": self.rs.word.word, "family": "holt", "start": start, "end": utc_now(),
                "cells": cells, "stops": stops, "notes": notes, "wear": {}, "exit": ex,
                "hosts": list(self.hosts), "copy": cp, "holt_code": getattr(self, "code", None),
                "write_allowed": {h: dict(v) for h, v in sorted(self.allowed.items()) if v},
                "write_kept": {h: v for h, v in sorted(getattr(self, "kept", {}).items()) if v},
                "write_allow_gb": {c: b / 1e9 for c, b in sorted(self.write_allows().values())}}


def run(ctx, plan, remote=None, sleep=None, poll=POLL_S, clock=None, gate=None):
    """HoltTest2's flow for PLAN (contract 2.3): a RunResult dict for run.py's report.
    REMOTE, SLEEP, CLOCK and GATE (a callable(ctx) standing in for the fast holt gate)
    are for tests."""
    return Runner(ctx, plan, remote, sleep, poll, clock, gate).run()


def resume(ctx, plan, remote=None, sleep=None, poll=POLL_S, clock=None, gate=None):
    """R10: the same run again (MAC_ROOT/runs/RUN/holt.json says what is done): finished
    builds and queues stand, live runners are waited on, the rest of the flow runs."""
    r = Runner(ctx, plan, remote, sleep, poll, clock, gate)
    if not r.state["start"]:
        r.notes.append("nothing of this run was done yet: it runs from the start")
    return r.run()
