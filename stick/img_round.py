#!/usr/bin/env python3
"""The image round's planner, on the Mac (docs/design/img-speed.md §3.3, §5.2 steps 1-3).

    img_round.py plan [--hosts H,H|all] [--full P,P|all] [--no-refresh] [--ref HOST]
                      [--remote-env K=V]... [--facts-dir DIR] [--today YYYY-MM-DD] [--json] RUN

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

RUN is a name (a directory of BKO_ROUND_DIR, default ~/backotter-work/img-rounds) or a path.
Exit 0 with a plan; 1 when a platform the plan builds has no host, or the reference gave no
digest of a platform (action none); 2 on bad usage; 3 when
the plan is refused (the reference not reached, the sets differ).
"""

import argparse
import concurrent.futures
import datetime
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys

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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="img-round.sh", add_help=True)
    ap.add_argument("cmd", choices=("plan",))
    ap.add_argument("--hosts")
    ap.add_argument("--full")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--ref")
    ap.add_argument("--remote-env", action="append", default=[])
    ap.add_argument("--facts-dir")
    ap.add_argument("--today")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--json", action="store_true")
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
