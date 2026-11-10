"""ImgTest2's runner (contract 2.2; rows 1.3): run(ctx, plan) executes a plan that plan.py
made, and resume(ctx, plan) picks one up from MAC_ROOT/runs/RUN/img.json.

For each due img:P, on a host its key still needs:

  1. the refusal is the plan's (plan.py's img_refuse_pass: the binaries' PASSes).
  2. pin: `t2-host tool img pin P...` on each host that may run it (tool_img.py), then the
     plan again, so that the keys name the pinned image.  A pin that is refused (a build
     under way, no image) makes the cell not-runnable.
  3. the boot tests, one job per host per image: testvm/test2/img/boot.sh under
     `cmp-run.sh --reserve 1 5`, WEAR_TAG=img:RUN:P, its records in
     HOST_ROOT/runs/RUN/img/P/.  Their outcome is read per check from boot-MODE.out.  A FAIL
     runs once more as attempt 2 (RUN-r2); a PASS then is FLAKY-FAIL, or PASS/known-flake
     when attempt 1's FAILs match img/flakes.json.
  4. the manifest: manifest.json, BKO-MF lines typed in the first mode, kept in
     extra.manifest; manifest:bins checks the image's binaries against bko-bin.sha256.
  5. the panel, when the word says (release, All) or the storage fields differ from the
     manifest of the newest PASS whose image passed its whole panel, or there is none.  One
     child holt lifecycle runs them all, run id t2h-STAMP-img, and each image then gets a
     second row at the boot row's key (origin img:RUN:P:A.panel) saying pass or fail.  A cell
     with rule img:panel-owed goes straight to this step.

The rows are written from the Mac through the copy's t2lib, on the host that ran the job.
"""

import copy
import json
import os
import re
import threading
import time

from . import hostjob, utc_now
from .gate_judge import counts_of, cut

SUBDIR = "img"
RESERVE = (1, 5)
JOB_TIMEOUT_S = 2 * 3600
JOB_WAIT_S = 4 * 3600
POLL_S = 20
DEFAULT_MODES = ("bios", "uefi")
STORAGE_KEYS = ("kernel", "zfs")
STORAGE_PREFIXES = ("tool:", "pkg:")
STOPPED_WHY = "stopped before it ran"
FAILING = ("FAIL", "FLAKY-FAIL")
CLASS = {"PASS": "pass", "FAIL": "fail", "FLAKY-FAIL": "fail", "HARNESS": "harness", "LOST": "lost"}

MODE_HEAD = re.compile(r"^(\S+) \((usb|cdrom)\): reached login in (\d+) s\s*$")
CHECK_LINE = re.compile(r"^  (ok|FAIL)\s+(\S+)\s*(.*)$")
FAILED_BOOT = re.compile(r"^(\S+): (.*?) \(see .*\)\s*$")


# --- reading a job's records --------------------------------------------------------------------

def parse_boot_out(text):
    """boot-test.py's output: {"reached": bool, "checks": [(name, ok, detail)], "why": text or
    None}."""
    res = {"reached": False, "checks": [], "why": None}
    for line in (text or "").splitlines():
        if MODE_HEAD.match(line):
            res["reached"] = True
            continue
        m = CHECK_LINE.match(line)
        if m:
            res["checks"].append((m.group(2), m.group(1) == "ok", m.group(3).strip()))
            continue
        m = FAILED_BOOT.match(line)
        if m and res["why"] is None:
            res["why"] = m.group(2)
    return res


def boot_needs(modes, files):
    """(needs, fails) of the boot tests: boot:MODE:CHECK for each check of each mode, boot:MODE
    when a mode failed before its checks.  FILES: {name: text} of the job's directory."""
    needs, fails = {}, []
    for mode in modes:
        text = files.get(f"boot-{mode}.out")
        rc = (files.get(f"boot-{mode}.rc") or "").strip()
        got = parse_boot_out(text)
        if not got["checks"]:
            why = got["why"] or ("no output" if text is None else f"no checks ran (exit {rc or '?'})")
            needs[f"boot:{mode}"] = "FAIL"
            fails.append(cut(f"boot:{mode}: {why}"))
            continue
        for name, ok, detail in got["checks"]:
            needs[f"boot:{mode}:{name}"] = "PASS" if ok else "FAIL"
            if not ok:
                fails.append(cut(f"boot:{mode}:{name}: {detail}"))
        if rc not in ("", "0") and all(ok for _, ok, _ in got["checks"]):
            needs[f"boot:{mode}"] = "FAIL"
            fails.append(cut(f"boot:{mode}: exit {rc} though its checks passed"))
    return needs, fails


def manifest_of(files):
    """manifest.json's fields, or None."""
    try:
        m = json.loads(files.get("manifest.json") or "")
    except ValueError:
        return None
    return m if isinstance(m, dict) and m else None


def check_manifest_bins(manifest, bins):
    """(need, fails): the image's binaries, as its manifest reads them, against bko-bin.sha256's
    (BINS: {NAME: hex})."""
    if not manifest:
        return "FAIL", ["manifest:bins: no manifest"]
    if not bins:
        return "FAIL", ["manifest:bins: no bko-bin.sha256 to compare with"]
    bad = []
    for name, want in sorted(bins.items()):
        got = manifest.get("bin:" + name)
        if got != want:
            bad.append(f"manifest:bins: {name} is {str(got)[:12]}, bko-bin.sha256 says {want[:12]}")
    for key, value in sorted(manifest.items()):
        if key.startswith("bin:") and key[4:] not in bins and value not in ("-", ""):
            bad.append(f"manifest:bins: {key[4:]} is in the image and not in bko-bin.sha256")
    return ("FAIL" if bad else "PASS"), bad


def storage_fields(manifest):
    return {k: v for k, v in (manifest or {}).items() if k in STORAGE_KEYS or k.startswith(STORAGE_PREFIXES)}


def changed_words(old, new):
    """What differs between two storage field sets, as a report says it."""
    diff = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
    out = []
    if "kernel" in diff:
        out.append("kernel")
    if "zfs" in diff:
        out.append("zfs")
    if any(k.startswith(STORAGE_PREFIXES) for k in diff):
        out.append("storage tools")
    return " and ".join(out)


def panel_reference(rows, cell):
    """The newest valid, committed PASS run of CELL whose image passed its whole panel, or None
    (a PASS from a dirty copy doesn't count until it is committed: plan.py's pass-uncommitted)."""
    best = None
    for r in rows or ():
        if (r.get("cell") != cell or r.get("verdict") != "PASS" or r.get("valid") is not True
                or r.get("kind") != "run" or r.get("committed") is not True):
            continue
        ex = r.get("extra") or {}
        if (ex.get("panel") or {}).get("result") == "pass" and isinstance(ex.get("manifest"), dict):
            if best is None or str(r.get("t") or "") >= str(best.get("t") or ""):
                best = r
    return best


def panel_decision(plat, manifest, rows, panel_cfg, release=False, all_form=False):
    """Step 5's rule: {needed, why, cells, result: none|owed|blocked}.  PANEL_CFG: classes.json's
    holt.img_panel[PLAT]."""
    cells = [f"holt:{g}+img" for g in (panel_cfg or {}).get("guests") or ()]
    new = storage_fields(manifest)
    why = None
    if release:
        why = "release"
    elif all_form:
        why = "ImgTest2All"
    else:
        ref = panel_reference(rows, f"img:{plat}")
        if ref is None:
            why = "no earlier panel"
        else:
            words = changed_words(storage_fields(ref["extra"]["manifest"]), new)
            if words:
                why = f"{words} changed"
    if why is None:
        return {"needed": False, "why": "storage pieces unchanged", "cells": cells, "result": "none"}
    blocked = (panel_cfg or {}).get("blocked")
    if blocked:
        return {"needed": True, "why": f"blocked: {blocked}", "cells": cells, "result": "blocked"}
    if not cells:
        return {"needed": False, "why": "no panel for this platform", "cells": [], "result": "none"}
    return {"needed": True, "why": why, "cells": cells, "result": "owed"}


def row_image(r):
    """The sha256 of the rescue image a holt:G+img row booted: its meta.img, or its rescuer/img-PLAT
    input.  None when the row doesn't say."""
    sha = ((r.get("meta") or {}).get("img") or {}).get("sha256")
    if sha:
        return sha
    for name, value in ((r.get("inputs") or {}).get("rescuer") or {}).items():
        if name.startswith("rescuer/img-") and str(value).startswith("sha256:"):
            return str(value)[7:]
    return None


def panel_cell_results(after_plan, rows, cells, images=None):
    """{cell: PASS|FAIL|None} of the panel's cells, after it ran: the newest valid decisive row at
    a key the cell has now (None: it didn't run, or says nothing: a HARNESS or LOST).  A cell the
    plan has no key for, or can't run, is None whatever older rows say.  IMAGES {cell: sha256}:
    only rows of that image count, so the other host's different image answers for nothing."""
    out = {}
    for c in cells:
        out[c] = None
        pc = after_plan.cell(c) if after_plan is not None else None
        keys = set((getattr(pc, "keys", None) or {}).values()) if pc is not None else set()
        if not keys or getattr(pc, "status", None) == "not-runnable":
            continue
        if images is not None and not images.get(c):
            continue
        best = None
        for r in rows or ():
            if r.get("cell") != c or r.get("valid") is not True or r.get("key") not in keys:
                continue
            if images is not None and row_image(r) != images[c]:
                continue
            if best is None or str(r.get("t") or "") >= str(best.get("t") or ""):
                best = r
        v = (best or {}).get("verdict")
        out[c] = "PASS" if v == "PASS" else "FAIL" if v in FAILING else None
    return out


# --- the run's state ------------------------------------------------------------------------------

class State:
    """MAC_ROOT/runs/RUN/img.json: what this runner has done, for resume."""

    def __init__(self, path):
        self.path = path
        self.mu = threading.RLock()
        try:
            with open(path, encoding="utf-8") as f:
                self.d = json.load(f)
        except (OSError, ValueError):
            self.d = {}
        self.d.setdefault("v", 1)
        self.d.setdefault("cells", {})
        self.d.setdefault("pins", {})

    def save(self):
        with self.mu:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.tmp.{os.getpid()}.{threading.get_ident()}"
            for _ in range(20):
                try:   # (a worker thread may be filling its own cell's dict)
                    text = json.dumps(self.d, indent=1, sort_keys=True)
                    break
                except RuntimeError:
                    time.sleep(0.01)
            else:
                return
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            os.replace(tmp, self.path)

    def cell(self, plat):
        with self.mu:
            return self.d["cells"].setdefault(plat, {"attempts": {}})


class Outcome:
    def __init__(self, verdict, needs=None, fails=None, why=None):
        self.verdict, self.needs, self.fails, self.why = verdict, needs or {}, fails or [], why
        self.manifest, self.wall_s, self.waits_s, self.still_running = None, 0, 0, False


# --- the runner -----------------------------------------------------------------------------------

class Runner:
    def __init__(self, ctx, plan, io=None, sleep=None, clock=None, poll=POLL_S, panel=None, pull=None):
        from . import hostrun
        self.ctx, self.plan, self.rs = ctx, plan, ctx.run
        self.env = hostjob.Env(self.rs, hostrun.host_path(self.rs.host_root), io=io)
        self.io = self.env.io
        self.sleep = sleep or time.sleep
        self.clock = clock or time.time
        self.poll = poll
        self.panel_fn = panel
        self.pull_fn = pull
        self.run_id = self.rs.run
        self.word = self.rs.word
        self.hosts = list(self.rs.hosts)
        self.opts = getattr(ctx, "options", None) or {}
        self.cls = ctx.classes or {}
        specs = getattr(ctx, "specs", None)
        self.sf = specs.get("img") if specs is not None else None
        self.state = State(os.path.join(self.rs.mac_dir, "img.json"))
        self.t0 = self.state.d.get("t0") or self.clock()
        self.state.d["t0"] = self.t0
        self.notes, self.stops, self.problems = [], [], []
        self.out_cells = {}
        self.not_runnable = {}
        self.panel_images = None   # {cell: sha256 of the boot row's image}, for the panel's results
        self.mu = threading.Lock()
        self.keep_copy()

    def keep_copy(self):
        """The copy on each host in img.json, and back from it on a resume (cells-HOST.json may
        not be written yet)."""
        saved = self.state.d.get("copy") or {}
        cur = self.rs.copy if isinstance(self.rs.copy, dict) else {}
        for h in self.hosts:
            if not (cur.get(h) or {}).get("path") and (saved.get(h) or {}).get("path"):
                cur[h] = dict(saved[h])
        self.rs.copy = cur
        if any((c or {}).get("path") for c in cur.values()) and saved != cur:
            self.state.d["copy"] = {h: dict(c) for h, c in cur.items()}
            self.state.save()

    def log(self, text):
        try:
            self.rs.log("img: " + text)
        except Exception:   # noqa: BLE001 - a log that fails stops nothing
            pass

    # --- what the plan says ----------------------------------------------------------------------
    def due(self):
        return [pc for pc in self.plan.cells if pc.status == "due" and pc.family == "img"]

    def plat(self, pc):
        return pc.cell.partition(":")[2]

    def modes(self, plat):
        try:
            m = self.sf.entry(plat).get("modes") if self.sf is not None else None
        except KeyError:
            m = None
        return tuple(m or DEFAULT_MODES)

    def panel_cfg(self, plat):
        return ((self.cls.get("holt") or {}).get("img_panel") or {}).get(plat) or {}

    def hosts_of(self, pc):
        ks = getattr(pc, "keys", None) or {}
        return [h for h in self.hosts if not ks or h in ks]

    def target(self, pc, ok_hosts):
        """Where the cell runs: the plan's placement, when that host could pin it."""
        for h in (getattr(pc, "placed", None), getattr(pc, "host", None)):
            if h and h in ok_hosts:
                return h
        return ok_hosts[0] if ok_hosts else None

    # --- step 2: the pins ------------------------------------------------------------------------
    def host_env(self):
        e = {"T2_HOST_ROOT": self.env.host_root, "T2_COPY_ROOT": os.environ.get("T2_COPY_ROOT") or "",
             "PYTHONDONTWRITEBYTECODE": "1"}
        for k in ("T2_WORK", "T2_IMAGES", "BKO_WORK", "T2_LEDGER"):
            if os.environ.get(k):
                e[k] = os.environ[k]
        return {k: v for k, v in e.items() if v}

    def pin_cmd(self, host, plats):
        from . import hostrun
        envs = " ".join(f"{k}={hostrun.rq(v)}" for k, v in sorted(self.host_env().items()))
        tool = os.path.join(self.env.copy_path(host), "testvm", "test2", "t2-host")
        return f"env {envs} python3 -B {hostrun.rq(tool)} tool img pin {' '.join(plats)} </dev/null"

    def pin(self, pcs):
        """Pins on each host that may run each cell; ctx.facts as a fresh probe would say."""
        wanted = {}
        for pc in pcs:
            for h in self.hosts_of(pc):
                wanted.setdefault(h, set()).add(self.plat(pc))
        for h, plats in sorted(wanted.items()):
            have = self.state.d["pins"].get(h) or {}
            todo = sorted(p for p in plats if not (have.get(p) or {}).get("ok"))
            if not todo:
                continue
            try:
                rc, out, err = self.io.sh(h, self.pin_cmd(h, todo), timeout=1800)
            except RuntimeError as e:
                rc, out, err = 1, "", str(e)
            try:
                got = json.loads((out or "").strip().splitlines()[-1])
                if rc != 0 or not isinstance(got, dict):
                    raise ValueError
            except (ValueError, IndexError):
                got = {p: {"ok": False, "why": f"t2-host tool img pin failed: {(err or '').strip()[-150:]}"} for p in todo}
            have = dict(have, **got)
            self.state.d["pins"][h] = have
            self.state.save()
            for p, r in got.items():
                self.log(f"{h}: pin {p}: " + (f"{r.get('how')} {str(r.get('sha256'))[:12]}" if r.get("ok") else r.get("why") or ""))
                self.note_pin(h, p, r)

    def note_pin(self, h, plat, r):
        im = ((((self.ctx.facts or {}).get(h) or {}).get("images") or {}).get(plat))
        if isinstance(im, dict) and r.get("ok"):
            im["pin"] = {"sha256": r.get("sha256"), "current": True, "bins_stale": bool(r.get("stale"))}

    def pin_of(self, host, plat):
        return (self.state.d["pins"].get(host) or {}).get(plat) or {}

    def replan(self):
        """Step 2's re-plan: the keys now name the pinned images."""
        try:
            plan, added, dropped = self.rs.replan(self.ctx.facts, None)
        except Exception as e:   # noqa: BLE001 - the plan as it was
            self.notes.append(f"no re-plan after the pins: {type(e).__name__}: {e}")
            return
        if added:
            self.notes.append("due after the pins: " + ", ".join(added))
        if dropped:
            self.notes.append("no longer due after the pins: " + ", ".join(dropped))
        self.plan = plan

    # --- step 3: one job ----------------------------------------------------------------------------
    def job_cmd(self, plat, host, attempt):
        q = hostjob._q
        pin = f"{self.env.host_root.rstrip('/')}/img-pin/{plat}/rescuer.img"
        out = hostjob.job_dir(self.env, plat, attempt, SUBDIR)
        ram = f"/mnt/bko-ram/{self.env.attempt_run(attempt)}-img-{plat}"
        return (f"bash testvm/test2/img/boot.sh --ram {q(ram)} {plat} {q(pin)} {q(out)} {' '.join(self.modes(plat))}", ram)

    def job_ended(self, host, out):
        """host_job's result once .exit has a status, else None."""
        x = self.io.fetch(host, out, [".exit", ".submitted", ".began", ".ended"])
        ex = (x.get(".exit") or "").strip()
        if not ex.lstrip("-").isdigit():
            return None
        return int(ex), out, {k[1:]: int(v.strip()) for k, v in x.items() if v.strip().lstrip("-").isdigit()}, None

    def job_pid(self, host, out):
        """(answered, pid text) of the job's .pid: no answer isn't the same as no file."""
        rc, text, _ = self.io.sh(host, f"cat {hostjob._q(out + '/.pid')} 2>/dev/null; exit 0", timeout=30)
        return rc == 0, (text or "").strip()

    def reattach(self, host, plat, attempt):
        """A job this run started before it stopped: wait for its end, on HOST, where it was
        started.  host_job's result.  A host that doesn't answer isn't a job that ended."""
        out = hostjob.job_dir(self.env, plat, attempt, SUBDIR)
        needle = f"/runs/{self.env.attempt_run(attempt)}/{SUBDIR}/{plat}/"
        end, pid, unknown = self.clock() + JOB_TIMEOUT_S, "", 0
        while self.clock() < end:
            done = self.job_ended(host, out)
            if done:
                return done
            answered = True
            if not pid.isdigit():
                answered, pid = self.job_pid(host, out)
            if answered and not pid.isdigit():
                state = False
            else:
                state = self.io.alive(host, int(pid), needle) if answered else None
            if state is False:
                done = self.job_ended(host, out)   # (it may have ended since the first look)
                if done:
                    return done
                if self.job_pid(host, out)[0]:
                    return None, out, {}, hostjob.JobFailure(f"attempt {attempt}'s job on {host} ended with no exit status (resume)")
                state = None
            unknown = unknown + 1 if state is None else 0
            if unknown >= hostjob.UNKNOWN_MAX:
                return None, out, {}, hostjob.JobFailure(f"no answer from {host} in {unknown} polls (resume)", still_running=True)
            self.sleep(self.poll)
        return None, out, {}, hostjob.JobFailure(f"attempt {attempt}'s job on {host} still runs (resume)", still_running=True)

    def run_job(self, host, plat, attempt, st):
        cmd, ram = self.job_cmd(plat, host, attempt)
        rec = st["attempts"].setdefault(str(attempt), {})
        if rec.get("started"):
            return self.reattach(rec.get("host") or host, plat, attempt)
        rec["started"] = utc_now()
        rec["host"] = host
        self.state.save()
        return hostjob.host_job(self.env, host, plat, attempt, cmd, reserve=RESERVE,
                                wear_tag=f"img:{self.run_id}:{plat}", timeout_s=JOB_TIMEOUT_S, wait_s=JOB_WAIT_S,
                                ram=ram, subdir=SUBDIR)

    def read_records(self, host, out, plat):
        """The job's records, read a second time when some are missing (a fetch that fails
        reads as nothing)."""
        need = [f"boot-{m}.{x}" for m in self.modes(plat) for x in ("out", "rc")] + [".began"]
        files = self.io.fetch(host, out, need + ["manifest.json"])
        if any(n not in files for n in need):
            self.sleep(min(self.poll, 5))
            files = dict(files, **self.io.fetch(host, out, need + ["manifest.json"]))
        return files

    def harness_why(self, host, out, plat, rc, files):
        """Why this job says nothing about the image (a HARNESS), or None."""
        if ".began" not in files:
            return f"the job never began, or its records can't be read (exit {rc}; {out}/job.log)"
        names = [f"boot-{m}.{x}" for m in self.modes(plat) for x in ("out", "rc")]
        lost = [n for n in names if n not in files]
        if lost:
            return f"no {', '.join(lost[:4])} from the job (exit {rc}; {out})"
        for m in self.modes(plat):
            text, mrc = files[f"boot-{m}.out"], files[f"boot-{m}.rc"].strip()
            got = parse_boot_out(text)
            if got["checks"]:
                continue
            if mrc not in ("0", "1") or not text.strip():
                return f"boot-test.py exited {mrc or '?'} in mode {m}, with no checks ({out}/boot-{m}.out)"
            if "Traceback (most recent call last)" in text:
                return f"boot-test.py crashed in mode {m} ({out}/boot-{m}.out)"
            if got["why"] == "QEMU exited":
                log = self.io.fetch(host, out, [f"boot-{m}.log"]).get(f"boot-{m}.log")
                if log is not None and not log.strip():
                    return f"QEMU exited with no console output in mode {m} ({out}/boot-{m}.out)"
        return None

    def judge(self, pc, host, plat, attempt, job):
        rc, out, times, why = job
        o = Outcome("HARNESS", why=None)
        if rc is None:
            o.why = str(why)
            o.still_running = bool(getattr(why, "still_running", False))
            o.fails = [cut(f"job: {why}")]
            return o, out, times
        if rc not in (0, 1):
            o.why = f"boot.sh exited {rc}"
            o.fails = [cut(f"job: boot.sh exited {rc} ({out}/job.log)")]
            return o, out, times
        files = self.read_records(host, out, plat)
        o.why = self.harness_why(host, out, plat, rc, files)
        if o.why:
            o.fails = [cut(f"job: {o.why}")]
            return o, out, times
        needs, fails = boot_needs(self.modes(plat), files)
        o.manifest = manifest_of(files)
        bins = self.image_bins(host, plat)
        n, f = check_manifest_bins(o.manifest, bins)
        needs["manifest:bins"] = n
        fails += f
        for name, res in sorted((getattr(pc, "binaries", None) or {}).items()):
            needs[f"refuse:{name}"] = "PASS"
        if plat == "linux":
            rs = self.pin_of(host, plat).get("release_same") or {}
            needs["release-same"] = "PASS" if rs.get("same") is True else "FAIL"
            if rs.get("same") is not True:
                fails.append(cut(f"release-same: {rs.get('why') or 'not compared'}"))
        else:
            needs["release-same"] = "SKIP"
        o.needs, o.fails = needs, fails
        o.verdict = "FAIL" if any(v == "FAIL" for v in needs.values()) else "PASS"
        return o, out, times

    def image_bins(self, host, plat):
        im = (((self.ctx.facts or {}).get(host) or {}).get("images") or {}).get(plat) or {}
        return im.get("bins") or self.pin_of(host, plat).get("bins") or {}

    # --- rows ---------------------------------------------------------------------------------------
    def measure(self, host, cell):
        from . import gate, keys
        try:
            specs = self.ctx.specs
            data = {"test2": self.env.copy_path(host).rstrip("/") + "/testvm/test2", "measure": [cell], "opts": {},
                    "want": keys.wants(specs, ("img",)), "env": self.host_env()}
            r = self.io.script(host, gate.HOST_SCRIPT, data, timeout=180)
            return (r.get("measure") or {}).get(cell) or {"error": "no measure"}
        except Exception as e:   # noqa: BLE001 - an unmeasured row is invalid, never a crash
            return {"error": f"{type(e).__name__}: {e}"}

    def append(self, host, row):
        from . import gate
        data = {"test2": self.env.copy_path(host).rstrip("/") + "/testvm/test2", "append": [row], "env": self.host_env()}
        try:
            r = self.io.script(host, gate.HOST_SCRIPT, data, timeout=120)
            ids = r.get("ids") or [["error", "no id"]]
            return tuple(ids[0])
        except (RuntimeError, ValueError) as e:
            return ("error", str(e))

    def row(self, pc, host, plat, attempt, o, m, start, rule, reasons, extra, rerun_of=None, origin=None, log=None):
        err, unknown = (m or {}).get("error"), (m or {}).get("unknown") or []
        planned = (getattr(pc, "keys", None) or {}).get(host)
        valid = not err and not unknown and (planned is None or m.get("key") == planned)
        notes = []
        if err:
            notes.append(f"inputs not measured: {err}")
        elif planned and m.get("key") != planned:
            notes.append("its inputs as run differ from the plan's")
        if unknown:
            notes.append("not measured: " + ", ".join(unknown[:5]))
        if o.why:
            notes.append(cut(o.why))
        if plat != "linux" and "release-same" in o.needs:
            notes.append("release-same: separate builds; release build not tested")
        ex = dict(extra)
        if rerun_of:
            ex["rerun_of"] = rerun_of
        tag = f"img:{self.run_id}:{plat}"
        word = getattr(self.word, "text", None) or "ImgTest2"
        return {"origin": origin or f"img:{self.run_id}:{plat}:{attempt}", "family": "img", "cell": pc.cell, "kind": "run",
                "run": self.run_id, "word": cut(word), "rule": rule, "reasons": [cut(r) for r in reasons][:20],
                "attempt": attempt, "start": start, "inputs": (m or {}).get("inputs") or {}, "spec": (m or {}).get("spec"),
                "planned_key": planned, "valid": bool(valid), "verdict": o.verdict, "class": CLASS[o.verdict],
                "needs": o.needs, "counts": counts_of(o.needs), "fails": o.fails[:20], "gm": round(o.wall_s / 60.0, 3),
                "wall_s": int(o.wall_s), "waits_s": int(o.waits_s), "wear": {tag: None}, "ram_peak_kib": None, "seed": None,
                "logs": log, "meta": {"run_family": "img", "plat": plat}, "extra": ex, "via": None,
                "note": cut("; ".join(notes), 1000) if notes else None}

    def write(self, host, row):
        rid = self.append(host, row)
        if rid and rid[0] == "error":
            self.problems.append(f"{row['cell']}: its row wasn't written: {cut(rid[1], 120)}")
            return None
        return rid[0]

    # --- the stop rules --------------------------------------------------------------------------------
    def feed(self, pc, plat, host, verdict, needs, flake):
        res = {"cell": pc.cell, "family": "img", "part": None, "verdict": verdict, "class": CLASS.get(verdict),
               "needs": needs, "os": plat, "host": host, "flake": flake}
        try:
            what = self.rs.stops.feed(res)
        except Exception:   # noqa: BLE001 - a stop engine that fails stops nothing
            return
        if what and what != "ok":
            self.stops.append(f"{what}: {pc.cell} {verdict}")

    def may_start(self, plat):
        if hostjob.tripped(self.rs):
            return False
        try:
            return bool(self.rs.stops.may_start(plat))
        except Exception:   # noqa: BLE001
            return True

    # --- step 3-5, one image --------------------------------------------------------------------------
    def boot_cell(self, pc, host):
        plat = self.plat(pc)
        st = self.state.cell(plat)
        st["host"] = host
        rule = pc.rule or "img:new"
        reasons = list(pc.reasons or [])
        flake, last_o, row_ids, first_verdict, prev = None, None, st.setdefault("rows", []), None, None
        for attempt in (1, 2):
            start = utc_now()
            # (A resume's re-plan may place the cell elsewhere: a started job stays where it is.)
            host = (st["attempts"].get(str(attempt)) or {}).get("host") or host
            st["host"] = host
            job = self.run_job(host, plat, attempt, st)
            o, out, t = self.judge(pc, host, plat, attempt, job)
            o.wall_s, o.waits_s = hostjob.job_times(t) if t else (0, 0)
            klass = None
            if attempt == 2 and first_verdict == "FAIL":
                from . import run as runmod
                o.verdict, klass = runmod.rerun_verdict(o.verdict, flake)
                if o.verdict == "FLAKY-FAIL":
                    o.why = "passed on the rerun after a FAIL: flaky"
            if attempt == 1:
                first_verdict = o.verdict
                if o.verdict == "FAIL":
                    from . import run as runmod
                    flake = runmod.match_flake(plat, "\n".join(o.fails), getattr(self.rs, "flakes", None) or [])
            extra = self.boot_extra(pc, plat, host, o)
            m = self.measure(host, pc.cell)
            row = self.row(pc, host, plat, attempt, o, m, start, rule if attempt == 1 else "rerun",
                           reasons if attempt == 1 else ([f"attempt 1: {first_verdict}"] + reasons)[:20], extra, prev,
                           log=f"{host}:{out}")
            if klass:
                row["class"] = klass
            if klass == "known-flake":
                row["note"] = cut("; ".join(x for x in (row.get("note"), f"known flake: {flake.get('why')}") if x), 1000)
            rid = self.write(host, row)
            if rid:
                row_ids.append(rid)
                prev = rid
            o.extra = extra
            st.update(verdict=o.verdict, klass=row["class"], attempt=attempt, needs=o.needs, fails=o.fails, extra=extra,
                      wall_s=st.get("wall_s", 0) + o.wall_s, boot_wall_s=int(o.wall_s), boot_waits_s=int(o.waits_s),
                      flake=bool(flake), key=m.get("key"))
            self.state.save()
            self.feed(pc, plat, host, o.verdict, o.needs, bool(flake))
            self.log(f"{pc.cell} on {host}: attempt {attempt} {o.verdict}" + (f": {o.fails[0]}" if o.fails else ""))
            last_o = o
            if o.verdict != "FAIL" or o.still_running or attempt == 2 or not self.may_start(plat):
                break
        st["done"] = utc_now()
        self.copy_manifest(host, plat, st)
        self.state.save()
        return last_o

    def copy_manifest(self, host, plat, st):
        """rows_holt's panel rows read RUN/img/P/manifest.json: an attempt 2's goes there when
        attempt 1 wrote none."""
        if st.get("attempt") != 2 or not (st.get("extra") or {}).get("manifest"):
            return
        q = hostjob._q
        first = hostjob.job_dir(self.env, plat, 1, SUBDIR) + "/manifest.json"
        second = hostjob.job_dir(self.env, plat, 2, SUBDIR) + "/manifest.json"
        self.io.sh(host, f"[ -s {q(first)} ] || cp {q(second)} {q(first)}", timeout=30)

    def boot_extra(self, pc, plat, host, o):
        cfg = self.panel_cfg(plat)
        rows = list(getattr(self.ctx, "ledger", None) or [])
        release = bool(getattr(self.word, "release", False))
        all_form = getattr(self.word, "form", None) == "All"
        if o.verdict == "PASS":
            panel = panel_decision(plat, o.manifest, rows, cfg, release=release, all_form=all_form)
        else:
            panel = {"needed": False, "why": "boot not passed", "cells": [], "result": "none"}
        tested = False
        if plat == "linux" and o.verdict == "PASS":
            tested = o.needs.get("release-same") == "PASS"
        return {"manifest": o.manifest, "modes": list(self.modes(plat)), "release": release, "release_tested": tested,
                "refuse": {n: (r or {}).get("state") for n, r in sorted((getattr(pc, "binaries", None) or {}).items())},
                "panel": panel}

    def owed_cell(self, pc, host):
        """Rule img:panel-owed: the boot row's standing; no boot tests."""
        plat = self.plat(pc)
        st = self.state.cell(plat)
        ks = set((getattr(pc, "keys", None) or {}).values())
        rows = [r for r in (getattr(self.ctx, "ledger", None) or []) if r.get("cell") == pc.cell and r.get("key") in ks
                and (r.get("extra") or {}).get("panel")]
        rows.sort(key=lambda r: str(r.get("t") or ""))
        boots = [r for r in rows if not str(r.get("origin") or "").endswith(".panel")]
        src = boots[-1] if boots else rows[-1] if rows else None
        if src is None:
            self.notes.append(f"{pc.cell}: its panel is owed, and no row says what it owes")
            st.update(verdict=None, done=utc_now())
            return None
        ex = dict(src.get("extra") or {})
        p = dict(ex.get("panel") or {})
        cfg = self.panel_cfg(plat)
        if cfg.get("blocked"):
            p.update(needed=True, why=f"blocked: {cfg['blocked']}", result="blocked")
        else:
            # (a boot row whose panel wasn't needed owes one now only because the word says so:
            # its why, "storage pieces unchanged", would misread as the reason it ran)
            was_needed = p.get("needed") and p.get("result") not in (None, "none")
            why = p.get("why") if was_needed else ("release" if getattr(self.word, "release", False)
                                                   else "ImgTest2All" if getattr(self.word, "form", "") == "All" else None)
            p.update(needed=True, why=why or p.get("why") or "owed", result="owed")
        if not p.get("cells"):
            p["cells"] = [f"holt:{g}+img" for g in cfg.get("guests") or ()]
        ex["panel"] = p
        st.update(verdict="PASS", klass="pass", attempt=int(src.get("attempt") or 1), needs=dict(src.get("needs") or {}),
                  fails=list(src.get("fails") or []), extra=ex, wall_s=0, boot_wall_s=int(src.get("wall_s") or 0),
                  boot_waits_s=int(src.get("waits_s") or 0), owed_from=src.get("id"), key=src.get("key"),
                  host=src.get("host") if src.get("host") in self.hosts else host, done=utc_now())
        st["needs"].pop("panel", None)
        self.state.save()
        return st

    def worker(self, host, pcs):
        for pc in pcs:
            plat = self.plat(pc)
            st = self.state.cell(plat)
            if st.get("done"):
                continue
            if not self.may_start(plat):
                with self.mu:
                    self.out_cells[pc.cell] = {"cell": pc.cell, "outcome": "skipped", "rule": pc.rule, "reasons": list(pc.reasons or [])
                                               + ["a stop rule stopped the run first"], "rows": [], "verdict": None, "gm": 0.0,
                                               "wall_s": 0, "gb": None, "host": None}
                continue
            try:
                if pc.rule == "img:panel-owed":
                    self.owed_cell(pc, host)
                else:
                    self.boot_cell(pc, host)
            except Exception as e:   # noqa: BLE001 - one image's harness failure isn't the others'
                self.problems.append(f"{pc.cell}: {type(e).__name__}: {e}")
                st.update(verdict="HARNESS", done=utc_now(), needs={}, fails=[cut(f"{type(e).__name__}: {e}")])
                self.state.save()

    def boots(self, placed):
        """{host: [pc]}: a worker per host, one image at a time on each; host checks here, each poll."""
        threads = [threading.Thread(target=self.worker, args=(h, pcs), daemon=True) for h, pcs in placed.items()]
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            try:
                for what in hostjob.host_checks(self.env, self.rs, f"img:{self.run_id}", self.t0):
                    if what not in self.stops:
                        self.stops.append(what)
            except Exception as e:   # noqa: BLE001
                self.log(f"host checks: {type(e).__name__}: {e}")
            for t in threads:
                t.join(self.poll / max(1, len(threads)))
        for t in threads:
            t.join()

    # --- step 5: the panel ---------------------------------------------------------------------------
    def stamp(self):
        return self.run_id.split("-", 1)[1]

    def run_panel(self, cells):
        """The child holt lifecycle over CELLS; {cell: PASS|FAIL|None}, once it has ended."""
        if self.panel_fn is not None:
            return self.panel_fn(cells)
        from . import plan as planmod, run as runmod, words
        ctx = copy.copy(self.ctx)
        ctx.options = dict(self.opts)
        ctx.options.pop("universe", None)
        ctx.facts = self.panel_facts()
        known = planmod.known_names(ctx)
        all_form = getattr(self.word, "form", None) == "All"
        if all_form:
            word = words.parse(["HoltTest2"] + [c[5:] for c in cells], known)
        else:
            word = words.parse(["HoltTest2"], known)
            ctx.options["universe"] = list(cells)
        shared = runmod.Shared(self.stamp(), self.rs.copy, hostjob.guards_of(self.rs), plan=None, suffix="img")
        run_id = runmod.new_run_id("holt", self.stamp(), "img")
        self.state.d.setdefault("panel_run", run_id)
        self.state.save()
        started = os.path.exists(os.path.join(runmod.run_dir(run_id, self.rs.mac_root), "holt.json"))
        done = os.path.exists(os.path.join(runmod.run_dir(run_id, self.rs.mac_root), "report.txt"))
        if done:
            rc = 0
        elif started:
            rc = runmod.resume(run_id, ctx, mac_root=self.rs.mac_root, out=self.rs.log, pull=self.opts.get("pull"), shared=shared)
        else:
            rc = runmod.lifecycle(word, ctx, mac_root=self.rs.mac_root, host_root=self.rs.host_root, out=self.rs.log,
                                  pull=self.opts.get("pull"), shared=shared)
        self.log(f"the panel's run {run_id} ended: exit {rc}")
        rows = self.pulled_rows()
        after = None
        try:
            w = words.parse(["HoltTest2"], known)
            # (The child's facts: its rows are keyed with the image sums it worked out.)
            c = copy.copy(ctx)
            c.options = dict(self.opts, universe=list(cells))
            c.ledger = rows
            after = planmod.make(w, c)
        except Exception as e:   # noqa: BLE001 - then every panel row reads as it says
            self.notes.append(f"the panel's cells weren't re-planned: {type(e).__name__}: {e}")
        return panel_cell_results(after, rows, cells, self.panel_images)

    def panel_facts(self):
        """Facts for the child's plan: the boots took minutes, and an ImgTest2 word asks only
        for its own family's."""
        from . import facts as factsmod, keys
        try:
            want = keys.wants(self.ctx.specs, ("holt", "gate", "img"), ["bko-bin"])
            got = factsmod.probe_all(list(self.ctx.hosts), want, save=False)
            ok = {h: f for h, f in got.items() if f.get("ok")}
            if ok:
                return ok
        except Exception as e:   # noqa: BLE001 - the run's own facts stand
            self.notes.append(f"no fresh facts for the panel: {type(e).__name__}: {e}")
        return self.ctx.facts

    def pulled_rows(self):
        pull = self.pull_fn or self.opts.get("pull")
        try:
            if callable(pull):
                rows = pull()
                if rows is not None:
                    self.ctx.ledger = list(rows)
        except Exception as e:   # noqa: BLE001 - the rows the plan had
            self.notes.append(f"no pull after the panel: {type(e).__name__}: {e}")
        return list(getattr(self.ctx, "ledger", None) or [])

    def panels(self):
        """The owed panels of this run: run, then each image's second row."""
        owed = {}
        for pc in self.plan.cells:
            if pc.family != "img":
                continue
            plat = self.plat(pc)
            st = self.state.d["cells"].get(plat)
            p = ((st or {}).get("extra") or {}).get("panel") or {}
            if (st or {}).get("verdict") == "PASS" and p.get("result") == "owed" and not st.get("panel_row"):
                owed[plat] = (pc, st, p)
        for plat, (pc, st, p) in list(owed.items()):
            stale = self.panel_stale(plat, st)
            if stale:
                st["panel_stale"] = stale
                st["panel_note"] = "bins-stale: " + ", ".join(stale)
                self.log(f"{pc.cell}: its panel can't run: {st['panel_note']}")
                del owed[plat]
            else:
                st.pop("panel_stale", None)
        self.state.save()
        if not owed:
            return
        if hostjob.tripped(self.rs) or any(not self.may_start(plat) for plat in owed):
            for plat, (pc, st, p) in owed.items():
                st["panel_note"] = STOPPED_WHY
            self.state.save()
            self.notes.append("the panels weren't run: a stop rule stopped the run first")
            return
        cells = sorted({c for _, _, p in owed.values() for c in p["cells"]})
        self.log(f"panel: {len(cells)} cell(s): {', '.join(cells)}")
        self.panel_images = {c: self.boot_image(plat, st) for plat, (_, st, p) in owed.items() for c in p["cells"]}
        try:
            res = self.run_panel(cells) or {}
        except Exception as e:   # noqa: BLE001 - owed, and the run says so
            self.problems.append(f"the panel didn't run: {type(e).__name__}: {e}")
            res = None
        for plat, (pc, st, p) in owed.items():
            if res is None:
                st["panel_note"] = "the panel didn't run"
                self.state.save()
                continue
            got = {c: res.get(c) for c in p["cells"]}
            if any(v is None for v in got.values()) and not any(v == "FAIL" for v in got.values()):
                st["panel_note"] = STOPPED_WHY if not any(got.values()) else "not every cell ran"
                st["panel_got"] = got
                self.state.save()
                continue
            self.panel_row(pc, plat, st, p, got)

    def boot_image(self, plat, st):
        """The sha256 of the image the boot row tested: the pin on its host."""
        return self.pin_of(st.get("host"), plat).get("sha256")

    def panel_stale(self, plat, st):
        """Contract 2.2's bins-stale: the binaries that keep this image's panel from running,
        when no host has a pin of the image with all of them.  [] when one has."""
        sha = self.boot_image(plat, st)
        pins = [p for p in (self.pin_of(h, plat) for h in self.hosts) if p.get("ok") and (not sha or p.get("sha256") == sha)]
        if not pins or any(not p.get("stale") for p in pins):
            return []
        return sorted({n for p in pins for n in p["stale"]})

    def panel_row(self, pc, plat, st, p, got):
        passed = all(v == "PASS" for v in got.values())
        host = st.get("host") or self.hosts[0]
        needs = dict(st.get("needs") or {}, panel="PASS" if passed else "FAIL")
        o = Outcome("PASS" if passed else "FAIL", needs=needs,
                    fails=list(st.get("fails") or []) + [cut(f"panel: {c} {v or 'not run'}") for c, v in sorted(got.items()) if v != "PASS"])
        o.manifest = (st.get("extra") or {}).get("manifest")
        # (The boot's cost: estimates.py takes this row's for the cell's, as it does the boot row's.)
        o.wall_s, o.waits_s = st.get("boot_wall_s") or 0, st.get("boot_waits_s") or 0
        extra = dict(st.get("extra") or {})
        extra["panel"] = dict(p, result="pass" if passed else "fail", passed=sum(1 for v in got.values() if v == "PASS"),
                              total=len(got))
        extra["boot_row"] = (st.get("rows") or [st.get("owed_from")])[-1]
        attempt = int(st.get("attempt") or 1)
        origin = f"img:{self.run_id}:{plat}:{attempt}.panel"
        m = self.measure(host, pc.cell)
        row = self.row(pc, host, plat, attempt, o, m, utc_now(), pc.rule or "img:new", list(pc.reasons or []), extra,
                       origin=origin, log=f"{host}:{self.env.host_root}/runs/{self.run_id}/img/{plat}")
        rid = self.write(host, row)
        st["panel_row"] = rid or "unwritten"
        if rid:
            st.setdefault("rows", []).append(rid)
        st.update(verdict=o.verdict, klass=CLASS[o.verdict], needs=needs, fails=o.fails, extra=extra)
        self.state.save()
        self.feed(pc, plat, host, o.verdict, needs, False)

    # --- the whole run -----------------------------------------------------------------------------
    def run(self):
        start = self.state.d.get("start") or utc_now()
        self.state.d["start"] = start
        self.state.save()
        pcs = self.due()
        if pcs:
            self.pin(pcs)
            self.replan()
            pcs = self.due()
            try:
                self.rs.write_cells(self.plan)
            except Exception as e:   # noqa: BLE001 - nothing of img reads cells.json but a resume
                self.notes.append(f"cells.json not written: {type(e).__name__}: {e}")
            placed = {}
            for pc in pcs:
                plat = self.plat(pc)
                ok = [h for h in self.hosts_of(pc) if self.pin_of(h, plat).get("ok")]
                host = self.target(pc, ok)
                if host is None:
                    why = next((self.pin_of(h, plat).get("why") for h in self.hosts_of(pc) if self.pin_of(h, plat).get("why")), None)
                    self.not_runnable[pc.cell] = why or "no host could pin it"
                    continue
                placed.setdefault(host, []).append(pc)
            self.boots(placed)
            self.panels()
        return self.result(start)

    def panel_text(self, st):
        """What the report's line says of the image's panel."""
        p = ((st or {}).get("extra") or {}).get("panel") or {}
        res = p.get("result")
        if res == "pass":
            return f"panel {p.get('passed')}/{p.get('total')} PASS ({p.get('why')})"
        if res == "fail":
            return f"panel {p.get('passed')}/{p.get('total')} PASS ({p.get('why')}): FAILED"
        if res == "blocked":
            return f"panel NOT TESTED: {p.get('why')}"
        if res == "owed":
            if (st or {}).get("panel_stale"):
                return "panel not runnable: bins-stale: " + ", ".join(st["panel_stale"])
            return f"panel OWED ({(st or {}).get('panel_note') or STOPPED_WHY})"
        if (st or {}).get("verdict") == "PASS":
            return f"panel not needed ({p.get('why') or 'storage pieces unchanged'})"
        return "panel not run"

    def block_lines(self):
        out = []
        for pc in self.plan.cells:
            if pc.family != "img":
                continue
            plat = self.plat(pc)
            st = self.state.d["cells"].get(plat)
            if not st or st.get("verdict") is None:
                continue
            boot = st.get("verdict")
            if ((st.get("extra") or {}).get("panel") or {}).get("result") == "fail":
                boot = "PASS"   # (the row is the panel's FAIL: the boot tests had passed)
            line = f"{pc.cell:<13}boot {boot:<7}{self.panel_text(st)}"
            if boot == "PASS" and plat != "linux":
                line += "    release build not tested"
            out.append(line)
        for c, why in sorted(self.not_runnable.items()):
            out.append(f"{c:<13}not runnable: {why}")
        return out

    def sweep_warning(self):
        if not getattr(self.word, "release", False):
            return
        try:
            from . import run as runmod
            last = runmod.last_sweep(self.rs.mac_root)
            days = float(((self.cls.get("policy") or {}).get("sweep") or {}).get("days") or 7)
            when = (last or {}).get("t") if isinstance(last, dict) else None
            from . import parse_utc
            t = parse_utc(when) if when else None
            now = self.clock()
            if not t or now - t > days * 86400:
                self.notes.append(f"warning: no complete SweepTest2 in the last {days:g} days (release isn't refused)")
        except Exception as e:   # noqa: BLE001 - only a warning
            self.notes.append(f"no sweep date to warn from: {type(e).__name__}: {e}")

    def result(self, start):
        cells, bad = [], []
        release = bool(getattr(self.word, "release", False))
        for pc in self.plan.cells:
            if pc.family != "img" or pc.status != "due":
                continue
            plat = self.plat(pc)
            st = self.state.d["cells"].get(plat)
            if pc.cell in self.not_runnable:
                cells.append({"cell": pc.cell, "outcome": "not-runnable", "rule": pc.rule,
                              "reasons": list(pc.reasons or []) + [self.not_runnable[pc.cell]], "rows": [], "verdict": None,
                              "gm": 0.0, "wall_s": 0, "gb": None, "host": None})
                continue
            if not st or st.get("verdict") is None:
                c = self.out_cells.get(pc.cell) or {"cell": pc.cell, "outcome": "not-started", "rule": pc.rule,
                                                    "reasons": list(pc.reasons or []) + ["due, but no worker ran it"], "rows": [],
                                                    "verdict": None, "gm": 0.0, "wall_s": 0, "gb": None, "host": None}
                cells.append(c)
                bad.append(f"{pc.cell} {c['outcome']}")
                continue
            p = (st.get("extra") or {}).get("panel") or {}
            v = st.get("verdict")
            cells.append({"cell": pc.cell, "outcome": "ran", "rule": pc.rule, "reasons": list(pc.reasons or []),
                          "rows": list(st.get("rows") or []), "verdict": v, "class": st.get("klass"),
                          "gm": round(float(st.get("wall_s") or 0) / 60.0, 2), "wall_s": int(st.get("wall_s") or 0), "gb": None,
                          "host": st.get("host"), "needs": st.get("needs"), "fails": (st.get("fails") or [])[:20]})
            if v != "PASS":
                bad.append(f"{pc.cell} {v}")
            elif p.get("result") == "owed":
                bad.append(f"{pc.cell}: its panel is owed")
            if release and (v != "PASS" or p.get("result") not in ("pass",)):
                bad.append(f"{pc.cell}: release needs boot PASS and every panel cell PASS")
        refused = [pc.cell for pc in self.plan.cells if pc.family == "img" and getattr(pc, "refused", False)]
        refused += sorted(self.not_runnable)
        self.sweep_warning()
        notes = list(self.notes) + list(self.problems)
        if bad:
            notes.append("not passed: " + "; ".join(bad[:12]) + (f"; and {len(bad) - 12} more" if len(bad) > 12 else ""))
        ex = 2 if refused else 1 if (bad or self.stops or self.problems) else 0
        cp = next((c.get("id") for c in (self.rs.copy or {}).values() if isinstance(c, dict)), None)
        return {"run": self.run_id, "word": getattr(self.word, "word", "ImgTest2"), "family": "img", "start": start, "end": utc_now(),
                "cells": cells, "stops": list(self.stops), "notes": notes, "wear": {}, "exit": ex,
                "hosts": list(self.hosts), "copy": cp, "blocks": self.block_lines()}


def run(ctx, plan, io=None, sleep=None, clock=None, poll=POLL_S, panel=None, pull=None):
    """ImgTest2's flow for PLAN: a RunResult dict for run.py's report.  IO, SLEEP, CLOCK, PANEL (a
    callable(cells) -> {cell: PASS|FAIL|None}, standing in for the child holt run) and PULL are for
    tests."""
    return Runner(ctx, plan, io, sleep, clock, poll, panel, pull).run()


def resume(ctx, plan, io=None, sleep=None, clock=None, poll=POLL_S, panel=None, pull=None):
    """The same run again: finished images and panels stand, jobs still running are waited on."""
    return Runner(ctx, plan, io, sleep, clock, poll, panel, pull).run()
