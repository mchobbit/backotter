#!/usr/bin/env python3
"""The image round's planner, on the Mac (docs/design/img-speed.md §3.3, §5.2 steps 1-3).

    img_round.py plan [--hosts H,H|all] [--full P,P|all] [--no-refresh] [--ref HOST]
                      [--remote-env K=V]... [--facts-dir DIR] [--today YYYY-MM-DD] [--json] RUN
    img_round.py run  [the same] [--to H,H|all] [--jobs N] [--lock-wait S] RUN
    img_round.py fanout [--to H,H|all] [--jobs N] RUN

stick/img-round.sh runs it.  `plan` executes nothing: it gathers the facts, decides each
platform's action, deals the build units and writes RUN/plan.json.

1. Facts.  One ssh per host, all at once: pick-host's room probe (cmp-run.sh --free and
   --ceiling, the load) and stick/img_round_facts.py (each platform's three digests, the
   current images and their provenance, the bko-bin and holt-bin SHA256SUMS digests).  The
   hosts are the build-role hosts of testvm/hosts.json (D4: not choubox), or --hosts; the
   reference host (--ref, default the fleet host, Chopin) is always asked too, since the
   plan's inputs are its (C7: Chopin's working copy of a guest, its server/slim link, the
   stick/ synced there).  --remote-env sets the hosts' S, BKO_IMG_DIR, BKO_DIGEST_CACHE ...
   (a trial from a private copy); --facts-dir reads HOST.facts instead (tests).
2. The round's sets.  Every reached builder's bko-bin and holt-bin SHA256SUMS must be the
   reference's, or the plan is refused and names the host (C1, C7, C10).  A host that isn't
   reached, or whose stick/ can't give facts, is named and gets nothing.
3. Plan.  Per platform, the newest current image that passed its proof (C4: a valid,
   committed ImgTest2 PASS of its test image's SHA-256 in the Mac's ledger whose +img panel
   passed or wasn't needed, with no FAIL of it since) is compared with the reference's
   digests, by §3.3's table: base changed (its inputs less the media, base_view) or os_epoch past the 14-day cadence (D3) -> full;
   bins changed -> refresh (full under --no-refresh, or while stick/ has no refresh of the
   platform: I3, I4); tests changed -> retest; else reuse.  No proven image, or none with a
   provenance (an image from before I1) -> full.  --full P forces one.
4. Deal.  A full or refresh unit goes to a host whose inputs for it are the plan's (C7: its
   base, tests and bins digests equal the reference's; a stale slim, another builder
   generation or FreeBSD fingerprint, unsynced stick/ files each refuse the host and are
   named; the media are not compared: base_view) and whose cmp-run.sh ceiling holds img-lanes.sh's reservation (D GiB, 10);
   a refresh only to a host that holds its parent.  stick/deal-units.py deals them
   (deal_images: longest first, earliest finish, Chopin penalised as pick-host does,
   room free now preferred).

5. run (I8): the Mac's lock (BKO_ROUND_DIR/.round.lock) and every used host's
   images/.round.lock (C10: HOLD_PY over ssh, exclusive, until the round lets go; a host whose
   lock stays held --lock-wait seconds, by another round or a promotion's install, refuses the
   round), then 1-4, then each host's img-lanes.sh --run RUN over its lanes (lanes_of), with
   BKO_PLAN_BKO_BIN, BKO_PLAN_HOLT_BIN and BKO_PLAN_BUILDER_P, then the fan-out (§10: DEST_PY
   checks a DEST's holt-bin and bko-bin first, img-xcopy.sh from the builder, VERIFY_PY hashes
   every holder's images again).  The remote environment is kept in plan.json, and fanout
   uses it.

RUN is a name (a directory of BKO_ROUND_DIR, default ~/backotter-work/img-rounds) or a path.
Exit 0 with a plan; 1 when a platform the plan builds has no host, or the reference gave no
digest of a platform (action none); 2 on bad usage; 3 when
the plan is refused (the reference not reached, the sets differ).
"""

import argparse
import concurrent.futures
import datetime
import fcntl
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
T2 = os.path.join(REPO, "testvm", "test2")
PLATFORMS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku")
CADENCE_DAYS = 14      # D3
IMG_MEM_GIB = 10.0     # img-lanes.sh's --reserve D 10
SCHEMA = 1
FAILING = ("FAIL", "LIMIT", "HARNESS-FAIL")


class Refused(Exception):
    pass


class Usage(Exception):
    pass


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


DEAL = _load("deal_units", os.path.join(HERE, "deal-units.py"))
PICK = _load("pick_host", os.path.join(REPO, "testvm", "pick-host"))


def hosts_mod():
    return _load("img_round_hosts", os.path.join(T2, "t2lib", "hosts.py"))


# --- C4: the ledger ----------------------------------------------------------------------------

def _test_sha(r, plat):
    v = (((r.get("inputs") or {}).get("bin") or {}).get(f"{plat}/test-image")) or ""
    return v[7:] if str(v).startswith("sha256:") else None


def proven(rows, plat, sha):
    """(ok, why): whether the test image SHA of PLAT passed its proof (C4).  A valid, committed
    img:PLAT run row of that image, PASS, whose panel passed or wasn't needed (storage pieces
    unchanged, or no panel for the platform), and no failing row of it since."""
    if not sha:
        return False, "its test image's SHA-256 is not known"
    mine = sorted((r for r in rows or () if r.get("cell") == f"img:{plat}" and r.get("valid") is True
                   and r.get("kind") == "run" and _test_sha(r, plat) == sha),
                  key=lambda r: str(r.get("t") or ""))
    if not mine:
        return False, f"no ImgTest2 row of its test image {sha[:12]} in the ledger"
    good, why = None, None
    for r in mine:
        p = (r.get("extra") or {}).get("panel") or {}
        if r.get("verdict") in FAILING:
            good, why = None, f"ImgTest2 {r.get('verdict')} at {r.get('t')}"
            continue
        if r.get("verdict") != "PASS":
            continue
        if r.get("committed") is not True:
            why = why or "its ImgTest2 PASS ran from an uncommitted copy"
            continue
        res = p.get("result")
        # (A row that records no panel at all proves nothing about it: "not needed" is
        # ImgTest2's own verdict, result "none" with needed false.)
        if res == "pass" or (res == "none" and p.get("needed") is False):
            good = r
        elif good is None:
            why = f"its +img panel is {res or 'not recorded'}"
    if good is not None:
        return True, f"ImgTest2 PASS {good.get('t')} ({good.get('host')})"
    return False, why or "no ImgTest2 PASS of it"


# --- the facts ---------------------------------------------------------------------------------

def facts_script(remote_env):
    """The one script each host runs: the environment, pick-host's probe, then the facts."""
    with open(os.path.join(HERE, "img_round_facts.py"), encoding="utf-8") as f:
        py = f.read()
    if "IMG_ROUND_EOF" in py:
        raise Refused("img_round_facts.py holds its own heredoc marker")
    lines = [f"export {k}={shlex.quote(v)}" for k, v in remote_env]
    return "\n".join(lines + [PICK.PROBE_SH, "python3 -I -B - <<'IMG_ROUND_EOF'", py, "IMG_ROUND_EOF", ""])


def parse_facts(text):
    """A host's output -> {"probe": pick-host's facts, "facts": img_round_facts.py's or None}."""
    probe = PICK.parse_probe(text)
    facts = None
    for line in str(text or "").splitlines():
        if line.startswith("facts {"):
            try:
                facts = json.loads(line[6:])
            except ValueError:
                facts = None
    return {"probe": probe, "facts": facts}


def gather_one(table, host, script, timeout):
    """HOST's facts over one ssh (TABLE: the hosts.py module, as pick-host's ssh_argv takes it)."""
    base, dests = PICK.ssh_argv(table, host, 6)
    last = "no answer"
    for dest in dests:
        try:
            r = subprocess.run(base + dest + ["sh -s"], input=script, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = f"timed out after {timeout}s"
            break
        except OSError as e:
            last = f"ssh: {e}"
            continue
        got = parse_facts(r.stdout)
        if got["facts"] is not None:
            return got
        err = (r.stderr or "").strip().splitlines()
        last = err[-1][:160] if err else f"no facts (exit {r.returncode})"
    return {"probe": {"ok": False}, "facts": None, "error": last}


def gather(table, hosts, remote_env, timeout=600):
    script = facts_script(remote_env)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        fut = {h: pool.submit(gather_one, table, h, script, timeout) for h in hosts}
        return {h: f.result() for h, f in fut.items()}


def read_facts_dir(d, hosts):
    out = {}
    for h in hosts:
        p = os.path.join(d, f"{h}.facts")
        try:
            with open(p, encoding="utf-8") as f:
                out[h] = parse_facts(f.read())
        except OSError:
            out[h] = {"probe": {"ok": False}, "facts": None, "error": f"no {p}"}
        if out[h]["facts"] is None and "error" not in out[h]:
            out[h]["error"] = "no facts line"
    return out


# --- the plan's comparisons --------------------------------------------------------------------

def _short(x):
    return (str(x)[:12] if x else "none")


def input_diff(a, b):
    """What differs between two base input sets (provenance's, a host's, or the plan's)."""
    a, b = a or {}, b or {}
    out = []
    sa, sb = a.get("stick") or {}, b.get("stick") or {}
    ch = sorted(k for k in set(sa) | set(sb) if sa.get(k) != sb.get(k))
    if ch:
        out.append("stick/ " + ", ".join(x[len("stick/"):] if x.startswith("stick/") else x for x in ch[:6])
                   + (f" and {len(ch) - 6} more" if len(ch) > 6 else ""))
    if a.get("media") != b.get("media"):
        out.append("media")
    if a.get("builder") != b.get("builder"):
        ba, bb = a.get("builder") or {}, b.get("builder") or {}
        def ident(x):
            return x.get("generation") or x.get("sha256") or x.get("kind") or "none"
        out.append(f"builder {bb.get('vm') or ba.get('vm')} ({_short(ident(ba))} vs {_short(ident(bb))})")
    if a.get("iso") != b.get("iso"):
        out.append(f"ISO ({_short(a.get('iso'))} vs {_short(b.get('iso'))})")
    if a.get("slim") != b.get("slim"):
        sa_, sb_ = a.get("slim") or {}, b.get("slim") or {}
        what = (f"{sa_.get('path')} vs {sb_.get('path')}" if sa_.get("path") != sb_.get("path")
                else "its manifest")
        out.append(f"slim ({what})")
    if a.get("host_tools") != b.get("host_tools"):
        ta, tb = a.get("host_tools") or {}, b.get("host_tools") or {}
        out.append("host tools " + ", ".join(f"{k} {ta.get(k)} vs {tb.get(k)}"
                                              for k in sorted(set(ta) | set(tb)) if ta.get(k) != tb.get(k)))
    return out


def base_view(inputs):
    """The base inputs the planner compares: all but the media.  stick/P/platform.conf (a base
    input) pins the media, and a full build's own fetch sets them (img_inputs.py moved() leaves
    them out for that reason), so a host's img-media/P/MEDIA, which only that host's last fetch
    wrote, would otherwise refuse every host that never built P, and make every image built on
    another host look changed on the reference.  A refresh reads no media."""
    return {k: v for k, v in (inputs or {}).items() if k != "media"}


def digest_why(ref, mine):
    """Why a host's digests for a platform aren't the plan's (C7), or None."""
    if not isinstance(mine, dict) or "error" in mine or "base" not in mine:
        return f"no digest ({(mine or {}).get('error', 'none')})"
    why = []
    if base_view(mine["base"]["inputs"]) != base_view(ref["base"]["inputs"]):
        why += input_diff(base_view(ref["base"]["inputs"]), base_view(mine["base"]["inputs"])) or ["its base digest"]
    if mine["tests"]["digest"] != ref["tests"]["digest"]:
        why.append("its tests (boot-test.py, checks, modes)")
    if mine["bins"]["digest"] != ref["bins"]["digest"]:
        why.append("its binaries")
    return ("its inputs differ from the plan's: " + "; ".join(why)) if why else None


def epoch_age(pv, today):
    e = ((pv or {}).get("base") or {}).get("os_epoch")
    try:
        return (today - datetime.date.fromisoformat(str(e)[:10])).days
    except (TypeError, ValueError):
        return None


def current_images(gathered, plat):
    """Every reached host's current image of PLAT: [(host, image facts, why-not-a-candidate)]."""
    out = []
    for h, g in gathered.items():
        im = ((g.get("facts") or {}).get("images") or {}).get(plat)
        if not im:
            continue
        pv = im.get("provenance")
        why = im.get("why")
        if why is None and pv is None:
            why = "no provenance (an image from before I1)"
        elif why is None and im.get("done_exit") != 0:
            why = f"done.exit is {im.get('done_exit')} (a build under way, or failed)"
        elif why is None and (pv.get("test") or {}).get("sha256") != im.get("test_sha256"):
            why = "its test image is not the one its provenance names"
        elif why is None and im.get("release_sha256") and (pv.get("release") or {}).get("sha256") != im.get("release_sha256"):
            why = "its release image is not the one its provenance names"
        out.append((h, im, why))
    return out


def decide(plat, ref, gathered, rows, opts, today, refresh_ready):
    """PLAT's row of the plan: {action, why, parent, holders, epoch, age_days, proven}."""
    out = {"action": None, "why": None, "parent": None, "holders": [], "epoch": None, "age_days": None}
    cands = current_images(gathered, plat)
    good = []
    notes = []
    for h, im, why in cands:
        if why:
            notes.append(f"{h}: {why}")
            continue
        ok, pwhy = proven(rows, plat, im["test_sha256"])
        if ok:
            good.append((h, im, pwhy))
        else:
            notes.append(f"{h}: not proven (C4): {pwhy}")
    if good:
        good.sort(key=lambda x: (str((x[1]["provenance"].get("built") or {}).get("when") or ""), x[1]["test_sha256"]))
        h, im, pwhy = good[-1]
        pv = im["provenance"]
        holders = [x[0] for x in good if x[1]["test_sha256"] == im["test_sha256"]
                   and x[1].get("release_sha256") == im.get("release_sha256")]
        out["parent"] = {"test_sha256": im["test_sha256"], "release_sha256": (pv.get("release") or {}).get("sha256"),
                         "image_id": (pv.get("release") or {}).get("image_id"), "mode": pv.get("mode"),
                         "built": pv.get("built"), "proof": pwhy}
        out["holders"] = holders
        out["epoch"] = (pv.get("base") or {}).get("os_epoch")
        out["age_days"] = epoch_age(pv, today)
    if not isinstance(ref, dict) or "base" not in ref:
        out.update(action="none", why=f"the reference has no digest of {plat} ({(ref or {}).get('error', 'none')})")
        return out
    if plat in opts["full"]:
        out.update(action="full", why="asked (--full)")
        return out
    if not good:
        if any(w is None or not w.startswith("no provenance") for _, _, w in cands):
            why = "no current image passed its proof (C4)"
        elif cands:
            why = "no provenance (an image from before I1)"
        else:
            why = "no current image"
        extra = [n for n in notes if "no provenance" not in n]
        out.update(action="full", why=why + (f" [{'; '.join(extra[:4])}]" if extra else ""))
        return out
    pv = good[-1][1]["provenance"]
    if base_view((pv.get("base") or {}).get("inputs")) != base_view(ref["base"]["inputs"]):
        diff = input_diff(base_view((pv.get("base") or {}).get("inputs")), base_view(ref["base"]["inputs"]))
        out.update(action="full", why="base changed: " + ("; ".join(diff) or "its digest"))
        return out
    age = out["age_days"]
    if age is None or age > CADENCE_DAYS:
        out.update(action="full", why=(f"past the cadence: its OS epoch is {age} days old (D3: {CADENCE_DAYS})"
                                       if age is not None else "its provenance has no OS epoch"))
        return out
    if (pv.get("bins") or {}).get("digest") != ref["bins"]["digest"]:
        if opts["no_refresh"]:
            out.update(action="full", why="bins changed, and --no-refresh")
        elif not refresh_ready.get(plat):
            out.update(action="full", why=f"bins changed; stick/ has no refresh of {plat} yet (I3, I4)")
        else:
            out.update(action="refresh", why="bins changed")
        return out
    if (pv.get("tests") or {}).get("digest") != ref["tests"]["digest"]:
        out.update(action="retest", why="tests changed")
        return out
    out.update(action="reuse", why="inputs unchanged, within the cadence")
    return out


def host_able(name, g, ref_dig, plat, action, holders):
    """(why-not or None, fits_now, reserve) for HOST taking PLAT's unit."""
    f, p = g.get("facts") or {}, g.get("probe") or {}
    why = digest_why(ref_dig, (f.get("digests") or {}).get(plat))
    d = ((f.get("images") or {}).get(plat) or {}).get("reserve_gib") or (1.0 if plat == "linux" else 6.0)
    res = (float(d), IMG_MEM_GIB)
    fits_now = False
    if why is None:
        c, crc = p.get("ceiling"), p.get("ceiling_rc")
        if crc not in (None, 0) or not c:
            why = p.get("ceiling_err") or "cmp-run.sh --ceiling said no figures"
        elif not PICK.fits(c, res):
            why = f"--reserve {PICK.num(res[0])} {PICK.num(res[1])} is over its ceiling {PICK.num(c[0])} {PICK.num(c[1])}"
    if why is None and action == "refresh" and name not in holders:
        why = "it doesn't hold the parent image"
    if why is None:
        room, ahead = PICK.room_of(p)
        fits_now = PICK.fits_room(room, res) and not ahead
    return why, fits_now, res


def plan_round(gathered, builders, ref, rows, opts, today, table=None):
    """The whole decision, touching nothing: GATHERED {host: {"probe", "facts", "error"?}} (the
    builders and REF), BUILDERS in tie order, ROWS the ledger.  Returns the plan dict."""
    plan = {"schema": SCHEMA, "ref": ref, "builders": list(builders), "refused": None, "hosts": {}, "platforms": {},
            "deal": None, "left": [], "today": today.isoformat(),
            "options": {"full": sorted(opts["full"]), "no_refresh": bool(opts["no_refresh"])}}
    rg = gathered.get(ref) or {}
    rf = rg.get("facts")
    if rf is None:
        plan["refused"] = f"the reference {ref} gave no facts ({rg.get('error', 'no answer')}): the plan's inputs are its"
        return plan
    if rf.get("errors"):
        plan["refused"] = f"the reference {ref}: " + "; ".join(rf["errors"])
        return plan
    sets = rf.get("sets") or {}
    plan["sets"] = sets
    if not sets.get("bko-bin") or not sets.get("holt-bin"):
        plan["refused"] = f"the reference {ref} has no " + " or ".join(
            f"{k}/SHA256SUMS" for k in ("bko-bin", "holt-bin") if not sets.get(k))
        return plan
    lpc = {}
    rows_by = {}
    if table is not None:
        rows_by = {r["name"]: r for r in table["hosts"]}
    bad_sets = []
    for h in builders:
        g = gathered.get(h) or {}
        f = g.get("facts")
        hp = {"reached": f is not None, "why": None, "class": (rows_by.get(h) or {}).get("class", "small")}
        if f is None:
            hp["why"] = f"not reached: {g.get('error', 'no answer')}"
        elif f.get("errors"):
            hp["why"] = "; ".join(f["errors"])
        else:
            diff = [k for k in ("bko-bin", "holt-bin") if (f.get("sets") or {}).get(k) != sets.get(k)]
            if diff:
                hp["why"] = " and ".join(f"its {k} SHA256SUMS {_short((f.get('sets') or {}).get(k))} is not {ref}'s "
                                         f"{_short(sets.get(k))}" for k in diff)
                bad_sets.append(f"{h} ({', '.join(diff)})")
        hp["sets"] = (f or {}).get("sets")
        lpc[h] = PICK.load_per_core(g.get("probe") or {}, rows_by.get(h) or {}) if f is not None else float("inf")
        hp["load_per_core"] = None if lpc[h] == float("inf") else round(lpc[h], 2)
        plan["hosts"][h] = hp
    if bad_sets:
        plan["refused"] = ("the builders' sets differ (C1, C7): " + "; ".join(bad_sets)
                           + " -- promote the same sets everywhere, or leave the host out with --hosts")
        return plan
    refresh_ready = rf.get("refresh") or {}
    usable = [h for h in builders if plan["hosts"][h]["why"] is None]
    units = []
    fits = {h: {} for h in usable}
    for p in PLATFORMS:
        ref_dig = (rf.get("digests") or {}).get(p)
        row = decide(p, ref_dig,
                     {h: gathered[h] for h in gathered if (gathered[h].get("facts") is not None)},
                     rows, opts, today, refresh_ready)
        row["able"] = {}
        if row["action"] in ("full", "refresh"):
            for h in usable:
                why, now, res = host_able(h, gathered[h], ref_dig, p, row["action"], row["holders"])
                row["able"][h] = why
                fits[h][p] = now
            able = [h for h in usable if row["able"][h] is None]
            if row["action"] == "refresh" and not able:
                row.update(action="full", why=row["why"] + "; no able host holds the parent")
                for h in usable:
                    why, now, res = host_able(h, gathered[h], ref_dig, p, "full", row["holders"])
                    row["able"][h] = why
                    fits[h][p] = now
                able = [h for h in usable if row["able"][h] is None]
            row["minutes"] = DEAL.img_minutes(p, row["action"])
            units.append({"platform": p, "action": row["action"], "able": able})
        elif row["action"] == "none":
            # (Neither built nor proven current: the plan can't stand for it, so it says so and
            # exits 1, as for a unit no host can take.)
            plan["left"].append({"platform": p, "why": row["why"]})
        plan["platforms"][p] = row
    ihosts = [DEAL.ImgHost(h, plan["hosts"][h]["class"], lpc[h], fits[h]) for h in usable]
    ihosts, placed, left = DEAL.deal_images(ihosts, units)
    for p, h in placed.items():
        plan["platforms"][p]["host"] = h
        plan["platforms"][p]["fits_now"] = fits[h].get(p)
    for p, why in left:
        plan["platforms"][p]["host"] = None
        ab = plan["platforms"][p]["able"]
        plan["left"].append({"platform": p, "why": why + (": " + "; ".join(f"{h}: {w}" for h, w in ab.items() if w)
                                                           if ab else "")})
    plan["deal"] = [{"host": h.name, "class": h.cls, "lanes": h.lanes, "est_min": round(h.minutes(), 1),
                     "units": [{"platform": u[0], "action": u[1], "minutes": u[2]} for u in h.units]} for h in ihosts]
    return plan


def render(plan):
    out = []
    if plan.get("refused"):
        return [f"img-round plan: refused: {plan['refused']}"]
    s = plan.get("sets") or {}
    out.append(f"img-round plan (inputs: {plan['ref']}'s; bko-bin {_short(s.get('bko-bin'))}, "
               f"holt-bin {_short(s.get('holt-bin'))}; {plan['today']})")
    out.append(f"  {'PLATFORM':<10} {'ACTION':<8} {'HOST':<9} {'EST':>5}  {'EPOCH':<10} {'AGE':>3}  WHY")
    for p in PLATFORMS:
        r = plan["platforms"].get(p)
        if not r:
            continue
        est = f"{r['minutes']:g}" if r.get("minutes") else "-"
        host = r.get("host") or ("-" if r["action"] not in ("full", "refresh") else "NONE")
        wait = "" if r.get("fits_now", True) else " (waits for room)"
        age = "-" if r.get("age_days") is None else str(r["age_days"])
        out.append(f"  {p:<10} {r['action']:<8} {host:<9} {est:>5}  {str(r.get('epoch') or '-'):<10} {age:>3}  "
                   f"{r['why']}{wait}")
    for d in plan.get("deal") or ():
        us = ", ".join(f"{u['platform']} {u['action']}" for u in d["units"]) or "-"
        out.append(f"  host {d['host']:<9} {d['class']:<5} {d['lanes']} lane(s)  est {d['est_min']:g} min: {us}")
    for h, hp in plan["hosts"].items():
        if hp.get("why"):
            out.append(f"  no units on {h}: {hp['why']}")
    for p in PLATFORMS:
        r = plan["platforms"].get(p) or {}
        bad = {h: w for h, w in (r.get("able") or {}).items() if w}
        if bad and r.get("host"):
            out.append(f"  {p} not on: " + "; ".join(f"{h} ({w})" for h, w in bad.items()))
    for x in plan.get("left") or ():
        out.append(f"  NOT PLACED: {x['platform']}: {x['why']}")
    n = {}
    for r in plan["platforms"].values():
        n[r["action"]] = n.get(r["action"], 0) + 1
    out.append("  " + ", ".join(f"{a} x{c}" for a, c in sorted(n.items())))
    return out


def run_dir(run):
    if "/" in run:
        return os.path.abspath(os.path.expanduser(run))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run):
        raise Usage(f"RUN is a name ([A-Za-z0-9._-]) or a path, not {run!r}")
    base = os.environ.get("BKO_ROUND_DIR") or os.path.expanduser("~/backotter-work/img-rounds")
    return os.path.join(base, run)


def pick_list(spec, allowed, what):
    if spec is None:
        return None
    xs = [x for x in spec.split(",") if x]
    if xs == ["all"]:
        return list(allowed)
    bad = [x for x in xs if x not in allowed]
    if bad or not xs:
        raise Usage(f"{what}: of {' '.join(allowed)} or all, not {','.join(bad) or repr(spec)}")
    return xs


# --- run and fanout (I8: §5.2 steps 4-5, §10, C10) ---------------------------------------------

LOCK_WAIT = 120          # s a host's round lock waits for a promotion's install to end
LOCK_GRACE = 900         # s a holder whose ssh is gone holds on, for the round to take it over
WAIT_NOTE_MIN = 5        # run's summary names a unit that waited longer for its reservation
SSH_OPTS = ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=10", "-o", "ControlMaster=no", "-o", "ControlPath=none"]


def ssh_argv(table, host, cmd):
    return ["ssh"] + SSH_OPTS + [table.ssh(host), cmd]


def env_sh(remote_env):
    return "".join(f"export {k}={shlex.quote(v)}\n" for k, v in remote_env)


def py_cmd(remote_env, code, *args):
    """A host's command: the round's environment, then python3 running CODE with ARGS."""
    return "bash -c " + shlex.quote(env_sh(remote_env) + "exec python3 -I -B -c " + shlex.quote(code) + " "
                                    + " ".join(shlex.quote(str(a)) for a in args))


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# The host's round lock (C10): images/.round.lock, held exclusive by this process (flock(2), as
# promote-bins.sh's and img-refresh-holt.sh's flock(1) take it shared) until the round says
# "release" on its stdin.  When its stdin ends without that (the Mac's ssh is gone: a network
# blip, or the Mac died), it holds on for GRACE seconds, in which the round's next holder of the
# same RUN takes over (it TERMs this one, named by the pid the file records), and then lets go.
HOLD_PY = r"""
# img-round-hold
import fcntl, os, subprocess, sys, time
run, who, wait, grace = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
work = os.environ.get("BKO_WORK") or "/home/m/backotter-work"
I = os.environ.get("BKO_IMG_DIR") or os.path.join(work, "images")
os.makedirs(I, exist_ok=True)
path = os.path.join(I, ".round.lock")
fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o664)
def first():
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readline().strip()
    except OSError:
        return ""
def cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
        return r.stdout
end = time.time() + wait
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except OSError:
        held = first()
        w = held.split()
        if w and w[0] == run and w[-1].rstrip(")").isdigit():   # (this round's last holder: take over)
            pid = int(w[-1].rstrip(")"))
            if pid != os.getpid() and "img-round-hold" in cmdline(pid):
                try:
                    os.kill(pid, 15)
                except OSError:
                    pass
        if time.time() >= end:
            print("BUSY " + (("image round " + held) if held else "a promotion or an img-refresh-holt.sh"), flush=True)
            sys.exit(3)
        time.sleep(1)
os.ftruncate(fd, 0)
os.write(fd, ("%s (img-round.sh from %s, since %s, pid %d)\n"
              % (run, who, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), os.getpid())).encode())
print("HELD " + path, flush=True)
for line in sys.stdin:
    if line.strip() == "release":
        os.ftruncate(fd, 0)
        sys.exit(0)
time.sleep(grace)
os.ftruncate(fd, 0)
"""


class HostLock:
    """One host's round lock, held by a holder over ssh until release()."""

    def __init__(self, table, host, run, remote_env, wait=LOCK_WAIT, grace=LOCK_GRACE):
        self.host, self.state, self.why = host, None, None
        who = os.uname().nodename.split(".")[0]
        try:
            self.p = subprocess.Popen(ssh_argv(table, host, py_cmd(remote_env, HOLD_PY, run, who, wait, grace)),
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as e:
            self.p, self.state, self.why = None, "unreached", f"ssh: {e}"

    def wait(self, timeout):
        """HELD, BUSY or unreached, once the holder has said."""
        if self.p is None:
            return self.state
        q = []
        t = threading.Thread(target=lambda: q.append(self.p.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        line = (q[0] if q else "").strip()
        if line.startswith("HELD "):
            self.state = "held"
        elif line.startswith("BUSY "):
            self.state, self.why = "busy", line[5:]
        else:
            p, err = self.p, ""
            if q:   # (its stdout ended: what it said)
                try:
                    err = (p.stderr.read() or "").strip().splitlines()
                    err = err[-1][:160] if err else ""
                except (OSError, ValueError):
                    pass
            self.state = "unreached"
            self.release()
            for f in (p.stdout, p.stderr):
                try:
                    f.close()
                except (OSError, ValueError):
                    pass
            self.state, self.why = "unreached", err or ("no answer in %ds" % timeout if not q else "no lock (exit %s)"
                                                         % p.returncode)
        return self.state

    def release(self):
        if self.p is None:
            return
        try:
            self.p.stdin.write("release\n")
            self.p.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            self.p.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.p.terminate()
            try:
                self.p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.p.kill()
        if self.state != "unreached":
            for f in (self.p.stdout, self.p.stderr):
                try:
                    f.close()
                except (OSError, ValueError):
                    pass
        self.p = None


class MacLock:
    """The Mac's round lock: BKO_ROUND_DIR/.round.lock, one round at a time."""

    def __init__(self, base, run):
        os.makedirs(base, exist_ok=True)
        self.path = os.path.join(base, ".round.lock")
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                held = f.readline().strip()
            os.close(self.fd)
            raise Refused(f"another round holds the Mac's lock ({self.path}): {held or 'its holder said nothing'}")
        os.ftruncate(self.fd, 0)
        os.write(self.fd, f"{run} (pid {os.getpid()}, since {utcnow()})\n".encode())

    def release(self):
        if self.fd is not None:
            os.ftruncate(self.fd, 0)
            os.close(self.fd)
            self.fd = None


class LockWatch(threading.Thread):
    """While the round runs: a holder whose ssh ended (a network blip) is replaced, the new
    holder taking the lock over from the old one in its grace.  EVENTS: what happened."""

    def __init__(self, table, held, run, remote_env, wait, every=10):
        super().__init__(daemon=True)
        self.table, self.held, self.run_, self.env, self.wait_, self.every = table, held, run, remote_env, wait, every
        self.events, self.halt = [], threading.Event()
        self.mu = threading.Lock()

    def run(self):
        while not self.halt.wait(self.every):
            for h in list(self.held):
                with self.mu:
                    lk = self.held[h]
                    if lk.p is None or lk.p.poll() is None or self.halt.is_set():
                        continue
                    new = HostLock(self.table, h, self.run_, self.env, self.wait_)
                    st = new.wait(self.wait_ + 60)
                    self.events.append({"host": h, "when": utcnow(), "retaken": st == "held",
                                        "why": None if st == "held" else new.why})
                    if st == "held":
                        self.held[h] = new
                        lk.release()

    def stop(self):
        self.halt.set()
        with self.mu:
            pass


def take_locks(table, hosts, run, remote_env, wait=LOCK_WAIT):
    """Every host's round lock, all at once: ({host: HostLock held}, {host: why unreached},
    {host: why busy}).  A busy host is a refusal (the caller lets go of the rest)."""
    locks = {h: HostLock(table, h, run, remote_env, wait) for h in hosts}
    held, gone, busy = {}, {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(locks))) as pool:
        st = dict(zip(locks, pool.map(lambda lk: lk.wait(wait + 60), locks.values())))
    for h, s in st.items():
        if s == "held":
            held[h] = locks[h]
        elif s == "busy":
            busy[h] = locks[h].why
            locks[h].release()
        else:
            gone[h] = locks[h].why
    return held, gone, busy


def lanes_of(units, n):
    """A host's units [(platform, action, minutes)] (longest first, as dealt) over its N lanes:
    each to the lane with the fewest minutes so far.  [[unit, ...], ...], empty lanes left out."""
    lanes = [[] for _ in range(max(1, n))]
    tot = [0.0] * len(lanes)
    for u in sorted(units, key=lambda u: (-u[2], u[0])):
        i = min(range(len(lanes)), key=lambda j: (tot[j], j))
        lanes[i].append(u)
        tot[i] += u[2]
    return [l for l in lanes if l]


def builder_line(ref_digest):
    """img_inputs.py identity's line for the reference's builder (img-one.sh's BKO_PLAN_BUILDER)."""
    b = (((ref_digest or {}).get("base") or {}).get("inputs") or {}).get("builder")
    return None if b is None else json.dumps(b, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def unit_token(plan, p, action):
    if action == "refresh":
        return f"{p}@{plan['platforms'][p]['parent']['test_sha256']}"
    return p


def build_units(plan, ref_digests):
    """{host: {"lanes": [[token...]...], "env": [(K, V)], "units": [platform...]}} from the deal."""
    out = {}
    sets = plan.get("sets") or {}
    for d in plan.get("deal") or ():
        if not d["units"]:
            continue
        ls = lanes_of([(u["platform"], u["action"], u["minutes"]) for u in d["units"]], d["lanes"])
        env = [("BKO_PLAN_BKO_BIN", sets["bko-bin"]), ("BKO_PLAN_HOLT_BIN", sets["holt-bin"])]
        for u in d["units"]:
            b = builder_line(ref_digests.get(u["platform"]))
            if b is not None and u["platform"] != "linux":
                env.append((f"BKO_PLAN_BUILDER_{u['platform']}", b))
        out[d["host"]] = {"lanes": [[unit_token(plan, x[0], x[1]) for x in l] for l in ls], "env": env,
                          "units": [x[0] for l in ls for x in l]}
    return out


def lanes_script(remote_env, run, host_units):
    """The host's side of its build: img-lanes.sh in its own session, its output in
    images/.round-RUN.log, its pid and then its exit in .round-RUN.pid and .round-RUN.exit (what
    the round reads when its ssh was lost: the lanes go on).  A "stop" line on stdin sends the
    session a HUP, as from a terminal that hung up; stdin ending does nothing."""
    lanes = " ".join(shlex.quote(" ".join(l)) for l in host_units["lanes"])
    r = shlex.quote(run)
    return env_sh(list(remote_env) + host_units["env"]) + f"""
trap '' PIPE
I=${{BKO_IMG_DIR:-${{BKO_WORK:-/home/m/backotter-work}}/images}}; mkdir -p "$I" || exit 1
x=$I/.round-{run}
rm -f "$x.exit" "$x.pid"
L=${{S:-/home/m/backotter-work/stick}}/img-lanes.sh
echo "img-round: start $(date +%s) $(hostname -s): img-lanes.sh --run {r} {lanes}"
exec 3<&0
if command -v setsid >/dev/null 2>&1; then setsid "$L" --run {r} {lanes} </dev/null >"$x.log" 2>&1 3<&- &
else "$L" --run {r} {lanes} </dev/null >"$x.log" 2>&1 3<&- & fi
p=$!
echo "$p" >"$x.pid"
( while kill -0 "$p" 2>/dev/null; do
      line=; read -r -t 5 line <&3; rc=$?
      if [ "$rc" -eq 0 ] && [ "$line" = stop ] && kill -0 "$p" 2>/dev/null; then
          echo "img-round: stopped: a HUP to the lanes"; kill -HUP -- "-$p" 2>/dev/null || kill -HUP "$p"; exit 0
      fi
      # (EOF: the round's ssh is gone, and the lanes go on; the round reads .round-RUN.exit.)
      [ "$rc" -eq 0 ] || [ "$rc" -gt 128 ] || exit 0
  done ) 3<&3 &
w=$!
wait "$p"; rc=$?
echo "$rc" >"$x.exit.part" && mv -f "$x.exit.part" "$x.exit"
kill "$w" 2>/dev/null
cat "$x.log"
echo "img-round: img-lanes.sh exit $rc"
exit "$rc"
"""


# A host's build whose ssh was lost: its lanes' pid and exit, and their output.
POLL_PY = r"""
import json, os, sys
work = os.environ.get("BKO_WORK") or "/home/m/backotter-work"
x = os.path.join(os.environ.get("BKO_IMG_DIR") or os.path.join(work, "images"), ".round-" + sys.argv[1])
def rd(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None
pid, ex = (rd(x + ".pid") or "").strip(), (rd(x + ".exit") or "").strip()
alive = False
if pid.isdigit():
    try:
        os.kill(int(pid), 0)
        alive = True
    except OSError:
        alive = False
print("poll " + json.dumps({"pid": pid or None, "alive": alive, "exit": int(ex) if ex.lstrip("-").isdigit() else None,
                            "log": rd(x + ".log") if ex else None}))
"""


# What a host's images say after a build: per platform, done.exit, the sums, the provenance's
# built and mode, bko-bin.sha256, and the times a unit started and ended (its own clock).
STATUS_PY = r'''
import hashlib, json, os, sys, time
work = os.environ.get("BKO_WORK") or "/home/m/backotter-work"
I = os.environ.get("BKO_IMG_DIR") or os.path.join(work, "images")
def rd(p, n=None):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            t = f.read()
        return t if n is None else "\n".join(t.splitlines()[-n:])
    except OSError:
        return None
def mt(p):
    try:
        return os.stat(p).st_mtime
    except OSError:
        return None
def size(d):
    # What img-xcopy.sh sends of images/P: everything but prev-*.
    n = 0
    for top, dirs, files in os.walk(d):
        if top == d:
            dirs[:] = [x for x in dirs if not x.startswith("prev-")]
        for f in files:
            try:
                n += os.lstat(os.path.join(top, f)).st_size
            except OSError:
                pass
    return n
out = {"now": time.time(), "images": I, "platforms": {}}
for p in sys.argv[1:]:
    d = os.path.join(I, p)
    pv = None
    try:
        pv = json.loads(rd(os.path.join(d, "provenance.json")) or "null")
    except ValueError:
        pv = None
    out["platforms"][p] = {"done_exit": (rd(os.path.join(d, "done.exit")) or "").strip() or None,
        "release_sums": rd(os.path.join(d, "SHA256SUMS")), "test_sums": rd(os.path.join(d, "test", "SHA256SUMS")),
        "bins": rd(os.path.join(d, "bko-bin.sha256")), "built": (pv or {}).get("built"), "mode": (pv or {}).get("mode"),
        "start": mt(os.path.join(d, "bko-bin.sha256")),
        "end": max([t for t in (mt(os.path.join(d, "done.exit")), mt(os.path.join(I, p + ".lane.log"))) if t] or [None]),
        "lane_log": rd(os.path.join(I, p + ".lane.log"), 6), "result": rd(os.path.join(d, "result.txt"), 8),
        "bytes": size(d)}
print("status " + json.dumps(out, sort_keys=True))
'''

# A fan-out destination's sets before the copy (§10): holt-bin's SHA256SUMS and each platform's
# bko-holt, and bko-bin's binaries, by SHA-256.
DEST_PY = r'''
import hashlib, json, os, sys
work = os.environ.get("BKO_WORK") or "/home/m/backotter-work"
B = os.environ.get("BKO_BIN") or os.path.join(work, "bko-bin")
H = os.environ.get("BKO_HOLT_BIN") or os.path.join(work, "holt-bin")
def sha(p):
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""):
                h.update(c)
        return h.hexdigest()
    except OSError:
        return None
I = os.environ.get("BKO_IMG_DIR") or os.path.join(work, "images")
def free(d):
    # The bytes free to this user where images/ is (its nearest existing ancestor).
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    try:
        st = os.statvfs(d or "/")
        return st.f_bavail * st.f_frsize
    except OSError:
        return None
out = {"holt_sums": sha(os.path.join(H, "SHA256SUMS")), "free": free(I), "platforms": {}}
for p in sys.argv[1:]:
    bd = os.path.join(B, p)
    try:
        names = sorted(n for n in os.listdir(bd) if n.endswith("-bko"))
    except OSError:
        names = []
    out["platforms"][p] = {"bko-holt": sha(os.path.join(H, p, "bko-holt")),
                           "bins": {n: sha(os.path.join(bd, n)) for n in names}}
print("dest " + json.dumps(out, sort_keys=True))
'''

# Every image a host holds of the round's platforms, hashed again against its SHA256SUMS.
VERIFY_PY = r'''
import hashlib, json, os, sys
work = os.environ.get("BKO_WORK") or "/home/m/backotter-work"
I = os.environ.get("BKO_IMG_DIR") or os.path.join(work, "images")
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 22), b""):
            h.update(c)
    return h.hexdigest()
out = {}
for p in sys.argv[1:]:
    d, r = os.path.join(I, p), {"ok": True, "why": [], "sums": {}}
    try:
        with open(os.path.join(d, "done.exit")) as f:
            r["done_exit"] = f.read().strip()
    except OSError:
        r["done_exit"] = None
    for sub in ("", "test"):
        sp = os.path.join(d, sub, "SHA256SUMS")
        try:
            with open(sp, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            r["ok"] = False; r["why"].append("no " + sp); continue
        r["sums"][sub or "release"] = text
        for line in text.splitlines():
            want, _, name = line.partition("  ")
            name = name.lstrip("*")
            if not name.endswith(".img"):
                continue
            try:
                got = sha(os.path.join(d, sub, name))
            except OSError:
                got = None
            if got != want:
                r["ok"] = False; r["why"].append("%s is %s, its SHA256SUMS says %s" % (os.path.join(sub, name), (got or "missing")[:12], want[:12]))
    out[p] = r
print("verify " + json.dumps(out, sort_keys=True))
'''


def run_py(table, host, remote_env, code, args, tag, timeout=900):
    """CODE on HOST: its "TAG {json}" line, or {"error": why}."""
    try:
        r = subprocess.run(ssh_argv(table, host, py_cmd(remote_env, code, *args)), capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s"}
    except OSError as e:
        return {"error": f"ssh: {e}"}
    for line in (r.stdout or "").splitlines():
        if line.startswith(tag + " {"):
            try:
                return json.loads(line[len(tag) + 1:])
            except ValueError:
                break
    err = (r.stderr or "").strip().splitlines()
    return {"error": err[-1][:200] if err else f"no answer (exit {r.returncode})"}


def pool_map(fn, items, jobs):
    items = list(items)
    if not items:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(jobs, len(items)))) as pool:
        return list(pool.map(fn, items))


def build(table, plan, units, remote_env, rdir, say, poll=30, lost_max=6 * 3600):
    """Each host's img-lanes.sh at once (step 4), its output in RUN/HOST.log.  A host whose ssh
    is lost (exit 255 before its exit was said) is polled (POLL_PY) until its lanes end, so a
    network blip doesn't cost a build.  A stop of the round (an interrupt) sends each host
    "stop", which HUPs its lanes.  {host: exit}."""
    procs = {}
    for h, hu in units.items():
        log = open(os.path.join(rdir, f"{h}.log"), "a", encoding="utf-8")
        say(f"  build on {h}: " + "  |  ".join(" ".join(l) for l in hu["lanes"]))
        try:
            procs[h] = (subprocess.Popen(ssh_argv(table, h, "bash -c " + shlex.quote(lanes_script(remote_env, plan["run"], hu))),
                                         stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True), log)
        except OSError as e:
            log.write(f"img-round: ssh: {e}\n")
            procs[h] = (None, log)

    def one(h):
        p, log = procs[h]
        rc = p.wait() if p is not None else 255
        if rc != 255:
            return rc
        log.write(f"img-round: {utcnow()}: the ssh to {h} was lost; its lanes go on, polled every {poll}s\n")
        log.flush()
        t0 = time.monotonic()
        while time.monotonic() - t0 < lost_max:
            st = run_py(table, h, remote_env, POLL_PY, [plan["run"]], "poll", timeout=120)
            if st.get("exit") is not None:
                log.write((st.get("log") or "") + f"img-round: img-lanes.sh exit {st['exit']} (read after the ssh was lost)\n")
                return st["exit"]
            if "error" not in st and not st.get("alive"):
                log.write(f"img-round: {h}'s lanes are gone with no exit said (pid {st.get('pid')})\n")
                return 254
            time.sleep(poll)
        log.write(f"img-round: gave up polling {h} after {lost_max}s\n")
        return 253
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(procs)))
    futs = {h: pool.submit(one, h) for h in procs}
    try:
        rcs = {h: f.result() for h, f in futs.items()}
        pool.shutdown()
    except BaseException:
        pool.shutdown(wait=False)
        for p, _ in procs.values():
            if p is not None and p.poll() is None:
                try:
                    p.stdin.write("stop\n")
                    p.stdin.flush()
                except (OSError, ValueError):
                    pass
        raise
    finally:
        for p, log in procs.values():
            if p is not None and p.stdin and not p.stdin.closed:
                try:
                    p.stdin.close()
                except (OSError, ValueError):
                    pass
            log.close()
    return rcs


def lane_start(rdir, h):
    try:
        with open(os.path.join(rdir, f"{h}.log"), encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"img-round: start (\d+) ", line)
                if m:
                    return float(m.group(1))
    except OSError:
        pass
    return None


def unit_rows(plan, units, statuses, rdir):
    """Per built platform: {host, action, done_exit, ok, minutes, waited, built_run, sums, bins, why}."""
    rows = {}
    for h, hu in units.items():
        st = statuses.get(h) or {}
        start0 = lane_start(rdir, h)
        for lane in hu["lanes"]:
            prev_end = start0
            for tok in lane:
                p = tok.split("@")[0]
                s = (st.get("platforms") or {}).get(p) or {}
                r = {"host": h, "action": plan["platforms"][p]["action"], "done_exit": s.get("done_exit"),
                     "built_run": (s.get("built") or {}).get("run"), "release_sums": s.get("release_sums"),
                     "test_sums": s.get("test_sums"), "bins": s.get("bins"), "bytes": s.get("bytes"), "minutes": None,
                     "waited": None}
                if s.get("start") and s.get("end") and s["end"] >= s["start"]:
                    r["minutes"] = round((s["end"] - s["start"]) / 60.0, 1)
                if s.get("start") and prev_end and s["start"] >= prev_end:
                    r["waited"] = round((s["start"] - prev_end) / 60.0, 1)
                prev_end = s.get("end") or prev_end
                why = None
                if st.get("error"):
                    why = f"no status from {h}: {st['error']}"
                elif r["built_run"] != plan["run"]:
                    why = (f"its images are not this round's (provenance built.run {r['built_run']!r}): "
                           + ((s.get("lane_log") or "").strip().splitlines() or ["no lane log"])[-1][:200])
                elif r["done_exit"] != "0":
                    why = f"done.exit {r['done_exit']}: " + ((s.get("result") or "").strip().splitlines() or [""])[-1][:200]
                r["ok"], r["why"] = why is None, why
                rows[p] = r
    return rows


def bins_of(text):
    """bko-bin.sha256's lines: {name: sha} (./restic-bko -> restic-bko)."""
    out = {}
    for line in (text or "").splitlines():
        sha_, _, name = line.partition("  ")
        if name.startswith("./"):
            out[name[2:]] = sha_
    return out


def dest_why(dest_facts, p, row, plan_holt):
    """Why DEST must not take P's image (§10), or None."""
    if "error" in dest_facts:
        return f"no facts: {dest_facts['error']}"
    want = bins_of(row.get("bins"))
    d = (dest_facts.get("platforms") or {}).get(p) or {}
    holt = want.get("bko-holt")
    if not (dest_facts.get("holt_sums") == plan_holt or (holt and d.get("bko-holt") == holt)):
        return (f"its holt-bin is neither the plan's set ({_short(dest_facts.get('holt_sums'))} vs {_short(plan_holt)}) "
                f"nor the image's bko-holt ({_short(d.get('bko-holt'))} vs {_short(holt)}): a copy could roll it back")
    bad = [n for n, s in want.items() if n != "bko-holt" and (d.get("bins") or {}).get(n) != s]
    if bad:
        return f"its bko-bin/{p} is not the image's ({', '.join(bad)}): its pin would write bins-stale"
    return None


FANOUT_FLOOR_GIB = 10    # GiB a fan-out leaves free on a DEST's images file system


def dest_room(room, d, dest_facts, p, row):
    """Why DEST D has no room for P's copy (D12), or None, and P's bytes taken from ROOM[D] (its
    free bytes above FANOUT_FLOOR_GIB, less the copies already given it).  The old set moves to
    prev-x and is not counted as freed.  A DEST or a unit that gave no size isn't checked."""
    need, free = row.get("bytes"), dest_facts.get("free")
    if not isinstance(need, int) or not isinstance(free, int):
        return None
    if d not in room:
        room[d] = free - int(FANOUT_FLOOR_GIB * (1 << 30))
    if need > room[d]:
        return (f"no room: {p}'s copy is {need / (1 << 30):.1f} GiB and its images/ has {free / (1 << 30):.1f} GiB free,"
                f" {max(room[d], 0) / (1 << 30):.1f} of it above the {FANOUT_FLOOR_GIB} GiB floor after this round's other"
                f" copies (prune its prev-* sets: stick/img-retain.sh)")
    room[d] -= need
    return None


def dest_env(remote_env):
    """img-xcopy.sh's DEST paths for the round's environment (the same layout on every host)."""
    e = dict(remote_env)
    out = list(remote_env)
    di = e.get("BKO_IMG_DIR") or (os.path.join(e["BKO_WORK"], "images") if e.get("BKO_WORK") else None)
    if di:
        out.append(("BKO_DEST_IMAGES", di))
    dh = e.get("BKO_HOLT_BIN") or (os.path.join(e["BKO_WORK"], "holt-bin") if e.get("BKO_WORK") else None)
    if dh:
        out.append(("BKO_DEST_HOLT_BIN", dh))
    if e.get("S"):
        out.append(("BKO_DEST_STICK", e["S"]))
    return out


def fanout(table, plan, rows, dests, remote_env, rdir, jobs, say):
    """Step 5, §10: every built platform that passed, from its builder to every other DEST (after
    DEST's sets are checked), then every holder's images hashed again.  Returns the fan-out
    record {platform: {host: "ok" | why}}."""
    good = {p: r for p, r in rows.items() if r["ok"]}
    plats = sorted(good)
    rec = {p: {r["host"]: "builder"} for p, r in good.items()}
    others = [d for d in dests if any(good[p]["host"] != d for p in plats)]
    dfacts = dict(zip(others, pool_map(lambda d: run_py(table, d, remote_env, DEST_PY, plats, "dest"), others, jobs)))
    pairs, room = {}, {}
    for p in plats:
        for d in dests:
            if d == good[p]["host"]:
                continue
            why = dest_why(dfacts[d], p, good[p], (plan.get("sets") or {}).get("holt-bin"))
            if not why:
                why = dest_room(room, d, dfacts[d], p, good[p])
            if why:
                rec[p][d] = "refused: " + why
            else:
                pairs.setdefault((good[p]["host"], d), []).append(p)
    denv = dest_env(remote_env)

    def copy(pair):
        src, d = pair
        ps = pairs[pair]
        cmd = "bash -c " + shlex.quote(env_sh(denv) + "exec \"${S:-/home/m/backotter-work/stick}\"/img-xcopy.sh "
                                       + shlex.quote(table.ssh(d)) + " " + " ".join(ps))
        logp = os.path.join(rdir, f"fanout-{src}-{d}.log")
        with open(logp, "w", encoding="utf-8") as log:
            try:
                rc = subprocess.run(ssh_argv(table, src, cmd), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    timeout=4 * 3600).returncode
            except (subprocess.TimeoutExpired, OSError) as e:
                log.write(f"img-round: {e}\n")
                rc = 255
        return pair, rc, logp
    if pairs:
        say(f"  fan-out: {sum(len(v) for v in pairs.values())} copies over {len(pairs)} host pairs")
    for (src, d), rc, logp in pool_map(copy, pairs, jobs):
        for p in pairs[(src, d)]:
            rec[p][d] = "copied" if rc == 0 else f"img-xcopy.sh exit {rc} ({os.path.basename(logp)})"
    # The proof that every holder has the same bytes: each one's images hashed again.
    holders = sorted({h for p in plats for h, s in rec[p].items() if s in ("builder", "copied")})
    ver = dict(zip(holders, pool_map(lambda h: run_py(table, h, remote_env, VERIFY_PY,
                                                       [p for p in plats if rec[p].get(h) in ("builder", "copied")],
                                                       "verify", timeout=3600), holders, jobs)))
    for p in plats:
        ref_sums = (good[p].get("release_sums"), good[p].get("test_sums"))
        for h in list(rec[p]):
            if rec[p][h] not in ("builder", "copied"):
                continue
            v = ver.get(h) or {}
            if "error" in v:
                rec[p][h] = f"not verified: {v['error']}"
                continue
            x = v.get(p) or {}
            sums = ((x.get("sums") or {}).get("release"), (x.get("sums") or {}).get("test"))
            if not x.get("ok"):
                rec[p][h] = "BAD: " + "; ".join(x.get("why") or ["no answer"])
            elif sums != ref_sums:
                rec[p][h] = "BAD: its SHA256SUMS are not the builder's"
            elif x.get("done_exit") != "0":
                rec[p][h] = f"BAD: done.exit {x.get('done_exit')}"
            else:
                rec[p][h] = "ok"
    return rec


def render_round(plan, rows, rec, rcs):
    out = [f"img-round run {plan['run']}: {plan['made']}"]
    out.append(f"  {'PLATFORM':<10} {'ACTION':<8} {'HOST':<9} {'MIN':>5} {'EXIT':>4}  HOLDERS")
    for p in PLATFORMS:
        pr = plan["platforms"].get(p) or {}
        r = rows.get(p)
        if r is None:
            out.append(f"  {p:<10} {pr.get('action', '-'):<8} {pr.get('host') or '-':<9} {'-':>5} {'-':>4}  "
                       f"{pr.get('why', '')}")
            continue
        f = rec.get(p) or {}
        okh = sorted(h for h, s in f.items() if s == "ok")
        mins = f"{r['minutes']:g}" if r.get("minutes") is not None else "-"
        out.append(f"  {p:<10} {r['action']:<8} {r['host']:<9} {mins:>5} {str(r['done_exit']):>4}  "
                   + (f"{len(okh)} identical: {', '.join(okh)}" if okh else "none"))
        if r["why"]:
            out.append(f"      FAILED: {r['why']}")
        for h, s in sorted(f.items()):
            if s not in ("ok",):
                out.append(f"      {h}: {s}")
        if (r.get("waited") or 0) > WAIT_NOTE_MIN:
            out.append(f"      waited {r['waited']:g} min for its reservation on {r['host']}")
    for h, rc in sorted(rcs.items()):
        if rc != 0:
            out.append(f"  {h}: img-lanes.sh exit {rc} ({h}.log)")
    for x in plan.get("left") or ():
        out.append(f"  NOT PLACED: {x['platform']}: {x['why']}")
    return out


def write_json(path, obj):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True, default=list)
        f.write("\n")
    os.replace(tmp, path)


def statuses_of(table, units, remote_env, jobs):
    hs = sorted(units)
    return dict(zip(hs, pool_map(lambda h: run_py(table, h, remote_env, STATUS_PY, units[h]["units"], "status"), hs, jobs)))


def round_hosts(table_rows, to):
    """Fan-out destinations: every enabled amd64 host (D12), or --to's."""
    allh = [r["name"] for r in table_rows if r.get("enabled", True) is not False and (r.get("arch") or "amd64") == "amd64"]
    if to in (None, "all"):
        return allh
    xs = [x for x in to.split(",") if x]
    bad = [x for x in xs if x not in allh]
    if bad or not xs:
        raise Usage(f"--to: of {' '.join(allh)} or all, not {','.join(bad) or repr(to)}")
    return xs


def do_run(c):
    """`run` (C): the Mac's lock, every host's round lock, the facts and the plan, the builds,
    the fan-out and its check.  C: {table, rows_of, run, rdir, builders, ref, opts, today,
    remote_env, dests, gather, rows, jobs, say, lock_wait, fanout_only}.  The exit code."""
    say = c["say"]
    mac = MacLock(os.path.dirname(c["rdir"]), c["run"])
    held, watch = {}, None
    try:
        if c.get("fanout_only"):
            try:
                with open(os.path.join(c["rdir"], "plan.json"), encoding="utf-8") as f:
                    plan = json.load(f)
            except (OSError, ValueError) as e:
                raise Refused(f"no plan of {c['run']} to fan out ({e})")
            c["remote_env"] = [tuple(x) for x in plan.get("remote_env") or ()]
            want = list(dict.fromkeys([d["host"] for d in plan.get("deal") or () if d["units"]] + c["dests"]))
        else:
            # A RUN names one round: its units count by provenance built.run, so a second run
            # of the name would take a unit that failed before it moved anything (exit 3, 4 or 5)
            # for the first run's image of that platform, built from that run's sets.
            try:
                ran = sorted(n for n in os.listdir(c["rdir"]) if n == "round.json" or n.endswith(".log"))
            except OSError:
                ran = []
            if ran:
                raise Refused(f"{c['run']} has run already ({', '.join(ran[:3])} in {c['rdir']}): a RUN is one "
                              f"round; give this one a new name, or `fanout {c['run']}` to finish that one's fan-out")
            want = list(dict.fromkeys(c["builders"] + [c["ref"]] + c["dests"]))
        held, gone, busy = take_locks(c["table"], want, c["run"], c["remote_env"], c.get("lock_wait", LOCK_WAIT))
        if busy:
            raise Refused("a host's round lock is held (C10): " + "; ".join(f"{h}: {w}" for h, w in sorted(busy.items())))
        for h, w in sorted(gone.items()):
            say(f"  {h}: no round lock ({w}): not used")
        watch = LockWatch(c["table"], held, c["run"], c["remote_env"], c.get("lock_wait", LOCK_WAIT),
                          c.get("watch_every", 10))
        watch.start()
        if c.get("fanout_only"):
            units = {d["host"]: {"units": [u["platform"] for u in d["units"]],
                                 "lanes": [[u["platform"] for u in d["units"]]]} for d in plan.get("deal") or () if d["units"]}
            units = {h: u for h, u in units.items() if h in held}
            rcs = {}
        else:
            if c["ref"] not in held:
                raise Refused(f"the reference {c['ref']} holds no round lock ({gone.get(c['ref'])}): the plan's inputs are its")
            builders = [h for h in c["builders"] if h in held]
            gathered = c["gather"](list(dict.fromkeys(builders + [c["ref"]])))
            plan = plan_round(gathered, builders, c["ref"], c["rows"], c["opts"], c["today"], c.get("table_rows"))
            plan["run"], plan["made"] = c["run"], utcnow()
            plan["remote_env"] = [list(x) for x in c["remote_env"]]
            os.makedirs(c["rdir"], exist_ok=True)
            write_json(os.path.join(c["rdir"], "plan.json"), plan)
            write_json(os.path.join(c["rdir"], "facts.json"), gathered)
            for line in render(plan):
                say(line)
            if plan["refused"]:
                return 3
            ref_dig = ((gathered.get(c["ref"]) or {}).get("facts") or {}).get("digests") or {}
            units = build_units(plan, ref_dig)
            rcs = build(c["table"], plan, units, c["remote_env"], c["rdir"], say, poll=c.get("poll", 30))
        statuses = statuses_of(c["table"], units, c["remote_env"], c["jobs"])
        rows = unit_rows(plan, units, statuses, c["rdir"])
        dests = [d for d in c["dests"] if d in held]
        rec = fanout(c["table"], plan, rows, dests, c["remote_env"], c["rdir"], c["jobs"], say)
        watch.stop()
        rnd = {"run": c["run"], "made": utcnow(), "units": rows, "fanout": rec, "lanes_exit": rcs, "dests": dests,
               "statuses": statuses, "lock_events": watch.events}
        write_json(os.path.join(c["rdir"], "fanout.json" if c.get("fanout_only") else "round.json"), rnd)
        lines = render_round(plan, rows, rec, rcs) + [
            f"  {e['host']}: its round lock's ssh ended at {e['when']}: "
            + ("taken again" if e["retaken"] else f"NOT taken again ({e['why']})") for e in watch.events]
        with open(os.path.join(c["rdir"], "summary.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        for line in lines:
            say(line)
        bad = (any(not r["ok"] for r in rows.values()) or any(s != "ok" for f in rec.values() for s in f.values())
               or any(rc != 0 for rc in rcs.values()) or bool(plan.get("left")))
        return 1 if bad else 0
    finally:
        if watch is not None:
            watch.stop()
        for lk in held.values():
            lk.release()
        mac.release()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="img-round.sh", add_help=True)
    ap.add_argument("cmd", choices=("plan", "run", "fanout"))
    ap.add_argument("--hosts")
    ap.add_argument("--full")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--ref")
    ap.add_argument("--remote-env", action="append", default=[])
    ap.add_argument("--facts-dir")
    ap.add_argument("--today")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--to")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--lock-wait", type=int, default=LOCK_WAIT)
    ap.add_argument("run")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        H = hosts_mod()
        table = H.load()
        builders_all = [r["name"] for r in table["hosts"] if r.get("enabled", True) is not False
                        and "build" in (r.get("roles") or ()) and (r.get("arch") or "amd64") == "amd64"]
        if a.hosts in (None, "all"):
            builders = builders_all
        else:
            builders = [x for x in a.hosts.split(",") if x]
            for x in builders:
                if x not in builders_all:
                    known = H.known(table) if hasattr(H, "known") else []
                    raise Usage(f"--hosts: {x} is not a build host (D4: {', '.join(builders_all)})"
                                + ("" if x in known else "; no such host"))
        builders = H.tie_order(builders, table)
        ref = a.ref or next((r["name"] for r in table["hosts"] if "fleet" in (r.get("roles") or ())), None)
        if not ref:
            raise Usage("no --ref and no fleet host in testvm/hosts.json")
        full = set(pick_list(a.full, PLATFORMS, "--full") or ())
        env = []
        for kv in a.remote_env:
            k, eq, v = kv.partition("=")
            if not eq or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k):
                raise Usage(f"--remote-env K=V, not {kv!r}")
            env.append((k, v))
        today = datetime.date.fromisoformat(a.today) if a.today else datetime.datetime.now(datetime.timezone.utc).date()
        rdir = run_dir(a.run)
        if a.cmd in ("run", "fanout"):
            def term(*_):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, term)
            sys.path.insert(0, T2)
            from t2lib import ledger
            c = {"table": H, "table_rows": table, "run": os.path.basename(rdir), "rdir": rdir, "builders": builders,
                 "ref": ref, "opts": {"full": full, "no_refresh": a.no_refresh}, "today": today, "remote_env": env,
                 "dests": round_hosts(table["hosts"], a.to), "jobs": a.jobs, "lock_wait": a.lock_wait,
                 "fanout_only": a.cmd == "fanout", "say": lambda x: print(x, flush=True),
                 "gather": ((lambda hs: read_facts_dir(a.facts_dir, hs)) if a.facts_dir
                            else (lambda hs: gather(H, hs, env, a.timeout))),
                 "rows": [] if a.cmd == "fanout" else ledger.view(fill_wear=False).rows}
            return do_run(c)
        ask = list(dict.fromkeys(builders + [ref]))
        gathered = read_facts_dir(a.facts_dir, ask) if a.facts_dir else gather(H, ask, env, a.timeout)
        sys.path.insert(0, T2)
        from t2lib import ledger
        rows = ledger.view(fill_wear=False).rows
        plan = plan_round(gathered, builders, ref, rows, {"full": full, "no_refresh": a.no_refresh}, today, table)
        plan["run"] = os.path.basename(rdir)
        plan["made"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        os.makedirs(rdir, exist_ok=True)
        tmp = os.path.join(rdir, ".plan.json.part")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, os.path.join(rdir, "plan.json"))
        with open(os.path.join(rdir, "facts.json"), "w", encoding="utf-8") as f:
            json.dump(gathered, f, indent=1, sort_keys=True, default=list)
        if a.json:
            print(json.dumps(plan, indent=1, sort_keys=True))
        else:
            print("\n".join(render(plan)))
            print(f"  plan: {os.path.join(rdir, 'plan.json')}")
        if plan["refused"]:
            return 3
        return 1 if plan["left"] else 0
    except Usage as e:
        print(f"img-round: {e}", file=sys.stderr)
        return 2
    except Refused as e:
        print(f"img-round: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
