//! Disks (rescue-screens.md, section 5): the overview, a list per old disk, and
//! the size screens: the offer for a disk up to 10 % smaller or bigger,
//! the decision for a bigger shrink whose data fits, the refusal with
//! the figures when it doesn't, and keep-or-grow for a much bigger disk.
//!
//! - Every plan is `plan --json` with the arguments so far (`full_load`,
//!   built in holt_args.rs); the overview runs one on each visit, so
//!   what it shows is bko-holt's for the choices made.
//! - Every figure is bko-holt's (`sizes`, `fit`, the refusals); the 10 %
//!   line is `plan::OFFER_PCT`.  Growing to fill a disk waits for RT10n's
//!   `--fit SRC=fill`: until then the extra stays free and the screen
//!   says so.  The BSDs', illumos's and Haiku's mounts have no sizes
//!   before RT10m, so their verdicts are bko-holt's `cant_resize`.
//! - Nothing here writes: a plan never does.

use crate::text::{cap, Clean};
use crate::ui::{Answer, Button, Chrome, Menu, Message, Radio, Ui};

use super::holt_args::{HoltCmd, Load, Stage};
use super::plan::{self, fits, gb, pct, Disk, Fits, Pair, Plan, Size};
use super::strings as s;
use super::{document, same_version, App, Go, B_RAW};

/// How long a plan may take: it probes every disk.
pub const PLAN_MS: u64 = 300_000;

pub(crate) const B_DONE: usize = 30;
pub(crate) const B_SHRINK: usize = 31;
pub(crate) const B_KEEP: usize = 32;
pub(crate) const B_ANOTHER: usize = 33;

/// A radio row's cells after its `(*) `.
const RADIO_ROW: usize = crate::widgets::list::ROW_W as usize - 4;

fn o(s: &str) -> Clean {
    Clean::ours(s)
}

/// A disk's name on screen: its model, else its kernel name.
pub fn disk_name(model: Option<&str>, node: &str) -> String {
    Clean::line(model.filter(|m| !m.trim().is_empty()).unwrap_or(node), cap::MODEL).as_str().to_string()
}

pub fn serial_of(serial: Option<&str>) -> Option<String> {
    serial.map(|x| Clean::line(x, cap::SERIAL).as_str().to_string())
}

/// What an old disk held: the system, or its mount points.
pub fn role(p: &Pair) -> String {
    if p.source.mounts.iter().any(|m| m == "/") {
        return s::ROLE_SYSTEM.to_string();
    }
    match p.source.mounts.first() {
        Some(_) => p.source.mounts.iter().take(3).map(|m| Clean::line(m, 64).as_str().to_string()).collect::<Vec<_>>().join(", "),
        None => s::ROLE_NOTHING.to_string(),
    }
}

/// `Old disk 1: Samsung SSD 870 EVO 500GB, 500 GB, the system`.
pub fn old_disk(i: usize, p: &Pair) -> String {
    s::old_disk(i + 1, &disk_name(p.source.model.as_deref(), &p.source.name), &gb(p.source.size_bytes), &role(p))
}

/// What is on a disk now, in words.
pub fn contents(d: &Disk) -> String {
    if d.blank || d.contents.is_empty() {
        return s::BLANK.to_string();
    }
    let parts: Vec<String> = d
        .contents
        .iter()
        .take(2)
        .map(|c| {
            let mut x = Clean::line(c.kind.as_deref().unwrap_or("unknown"), 16).as_str().to_string();
            if let Some(l) = c.label.as_deref().filter(|l| !l.is_empty()) {
                x.push_str(&format!(" \"{}\"", Clean::line(l, 32).as_str()));
            }
            if let Some(u) = c.used_bytes {
                x.push_str(&format!(", {} used", gb(u)));
            }
            x
        })
        .collect();
    let n = d.contents.iter().filter(|c| c.dev != d.node).count();
    let mut t = parts.join("; ");
    if d.contents.len() > 2 {
        t.push_str("; ...");
    }
    s::has_data(n, &t)
}

/// A refused disk's reason, in words; bko-holt's text for a code this
/// doesn't know.
pub fn refusal(code: &str, text: &str) -> String {
    match code {
        "boot_disk" => s::REFUSED_STICK.to_string(),
        "backup_drive" => s::REFUSED_BACKUP.to_string(),
        "running_root" => s::REFUSED_ROOT.to_string(),
        "in_use" => s::REFUSED_IN_USE.to_string(),
        "read_only" => s::REFUSED_READ_ONLY.to_string(),
        "sector_size" => s::REFUSED_SECTOR.to_string(),
        _ => Clean::line(text, cap::LINE).as_str().to_string(),
    }
}

/// The partition a size verdict changes, as the screens name it.
fn mount_of(sz: &Size) -> String {
    Clean::line(sz.mount.as_deref().or(sz.partition.as_deref()).unwrap_or("the largest partition"), 64).as_str().to_string()
}

/// A candidate's size line for one old disk, and why it can't be taken
/// when it can't.
fn size_line(src: &Pair, c: &Disk) -> (Option<String>, Option<String>) {
    let sz = c.size_for(&src.source.name);
    let diff = gb(c.size_bytes.abs_diff(src.source.size_bytes));
    let Some(z) = sz else { return (None, None) };
    let m = mount_of(z);
    let (old, new) = (z.old_bytes.map(gb).unwrap_or_default(), z.new_bytes.map(gb).unwrap_or_default());
    match fits(sz) {
        Fits::Same => (None, None),
        Fits::LittleSmaller(_) if z.verdict == "smaller_slack" => (Some(s::row_slack(&diff)), None),
        Fits::LittleSmaller(_) => (Some(s::row_smaller(&diff, &m, &old, &new)), None),
        Fits::MuchSmaller(_) | Fits::BeyondLimit => (Some(s::row_much_smaller(&m, &old, &new, &pct(z.change_pct.unwrap_or(0.0)))), None),
        Fits::LittleBigger(_) => (Some(s::row_bigger(&diff, &m, &old, &new)), None),
        Fits::MuchBigger => (Some(s::row_much_bigger(&diff)), None),
        Fits::Refused => match z.verdict.as_str() {
            "data_wont_fit" => {
                let (used, most, need) = (z.used_bytes.map(gb).unwrap_or_default(), z.most_bytes.map(gb).unwrap_or_default(), z.needed_bytes.map(gb).unwrap_or_default());
                (Some(s::row_wont_fit(&m, &used, &most)), Some(s::wont_fit(&m, &used, &most, &need)))
            }
            "cant_resize" => {
                let why = z.why.as_deref().map(|w| Clean::line(w, cap::LINE).as_str().to_string()).unwrap_or_else(|| s::CANT_RESIZE_WHY.to_string());
                (Some(s::row_cant_resize(&m)), Some(s::cant_resize(&why)))
            }
            v => {
                let v = Clean::line(v, 32).as_str().to_string();
                (Some(s::row_unknown(&v)), Some(s::unknown_verdict(&v)))
            }
        },
    }
}

impl App {
    /// The arguments every plan of this session has, `stage` its stage:
    /// the layout, the repository and its password file, the snapshot,
    /// the key files, every mapping chosen, `--erase` for each mapped
    /// target (a dry run too, J6) unless `erase` is false, the fit and the
    /// filesystems chosen.  The one place they are put together, so
    /// Review's plan and Restore's run are one list.
    pub(crate) fn full_load(&self, stage: Stage, erase: bool) -> Option<Load> {
        let layout = self.layout.clone()?;
        let mut l = Load::new(&layout, stage);
        l.repo = self.target().map(|t| t.place.url);
        l.password_file = self.password.clone();
        l.snapshot = self.snap.clone();
        l.luks_header_dir = self.headers.as_ref().filter(|(_, from)| *from == self.snap).map(|(d, _)| d.clone());
        for (group, names, path) in &self.keys {
            let _ = group;
            for n in names {
                l.key_files.push((n.clone(), path.clone()));
            }
        }
        for (src, _, arg) in &self.map {
            l.map.push((src.clone(), arg.clone()));
            if erase {
                l.erase.push(arg.clone());
            }
        }
        l.fit_largest = self.fit.iter().map(|f| f.1).max();
        l.reformat = self.reformat.clone();
        Some(l)
    }

    /// Run a load that writes one document (a plan, a status): its
    /// document, read; else where to go, its problem screen shown.
    pub(crate) fn holt_doc(&mut self, ui: &mut dyn Ui, l: &Load, format: &str, title: &str, text: &str) -> Result<crate::json::Value, Go> {
        let Some(mut cmd) = self.holt_cmd(&HoltCmd::Load(l)) else { return Err(Go::Back) };
        // A status is small; a plan carries the stages and the text.
        cmd.stdout_max = if l.stage == Stage::Status { plan::STATUS_MAX } else { plan::PLAN_MAX };
        cmd.timeout_ms = PLAN_MS;
        let out = self.run_job(ui, s::SCR_DISKS, title, o(text), cmd)?;
        let version = self.version.clone();
        let doc = document(&out, format).and_then(|v| same_version(v, 1, version.as_deref()));
        match doc {
            Ok(v) => Ok(v),
            Err(why) => Err(self.plan_failed(ui, &why)),
        }
    }

    /// A plan: run, read, kept as the session's latest.
    pub(crate) fn plan_run(&mut self, ui: &mut dyn Ui, l: &Load, text: &str) -> Result<Plan, Go> {
        let v = self.holt_doc(ui, l, "bko-holt-plan", s::PLANNING_TITLE, text)?;
        match plan::read_plan(&v) {
            Ok(p) => Ok(p),
            Err(why) => Err(self.plan_failed(ui, &why)),
        }
    }

    /// bko-holt couldn't plan: its words, Try again, Shell, Report, Back.
    fn plan_failed(&mut self, ui: &mut dyn Ui, why: &str) -> Go {
        let p = super::Problem {
            code: "plan_failed",
            screen: s::SCR_DISKS,
            title: s::PLAN_FAILED_TITLE.to_string(),
            what: s::plan_failed_what(Clean::line(why, cap::PROBLEM).as_str()),
            why: s::PLAN_FAILED_WHY.to_string(),
            todo: s::PLAN_FAILED_TODO.to_string(),
            fixes: Vec::new(),
            again: true,
            back: Some(Go::Back),
        };
        self.problem(ui, p)
    }

    /// No layout to plan from: why, and the ways on.  A system.json that
    /// carries none (bko-holt's `no_layout`: the backup tool left it out
    /// for the stick's room, holt-json.md's open question 16) has its own:
    /// a newer backup holds it (open question 15), or, from a bko-holt
    /// that can't read a snapshot's own, this bko-rescue can't fetch it.
    fn no_layout(&mut self, ui: &mut dyn Ui, n: &super::layout::NoLayout) -> Go {
        // The backup asked first holds none of its own either (open
        // question 15): a newer backup does.
        // The stick's row says why it holds none (open question 18): the
        // advice for that reason alone.  Not said (an older bko-holt): the
        // words that offer room only if bko-holt's say so.
        // Too big for any stick: the first words never offer another.
        let advice = self.chosen().and_then(super::stick::Machine::no_layout).map(|(_, a)| a);
        let too_big = self.chosen().is_some_and(super::stick::Machine::layout_too_big);
        let (in_backup, on_stick) = if too_big {
            (s::NO_LAYOUT_IN_BACKUP_FIRST_TOO_BIG, s::NO_LAYOUT_ON_STICK_FIRST_TOO_BIG)
        } else {
            (s::NO_LAYOUT_IN_BACKUP_FIRST, s::NO_LAYOUT_ON_STICK_FIRST)
        };
        let (why, todo) = match (n.none_on_stick, n.none_in_backup, advice) {
            (true, true, Some(a)) => (s::NO_LAYOUT_IN_BACKUP_WHY, s::no_layout_todo(in_backup, a)),
            (true, true, None) => (s::NO_LAYOUT_IN_BACKUP_WHY, s::NO_LAYOUT_IN_BACKUP_TODO.to_string()),
            (true, false, Some(a)) => (s::NO_LAYOUT_ON_STICK_WHY, s::no_layout_todo(on_stick, a)),
            (true, false, None) => (s::NO_LAYOUT_ON_STICK_WHY, s::NO_LAYOUT_ON_STICK_TODO.to_string()),
            _ => (s::NO_LAYOUT_WHY, s::NO_LAYOUT_TODO.to_string()),
        };
        let p = super::Problem {
            code: "no_layout",
            screen: s::SCR_DISKS,
            title: s::NO_LAYOUT_TITLE.to_string(),
            what: Clean::line(&n.what, cap::PROBLEM).as_str().to_string(),
            why: why.to_string(),
            todo,
            fixes: Vec::new(),
            again: true,
            back: Some(Go::Back),
        };
        self.problem(ui, p)
    }

    /// When the chosen backup was made: in full, and its date alone.
    pub(crate) fn snap_when(&self) -> (String, String, Option<u64>) {
        let zone = match self.chosen() {
            Some(m) => self.zone_of(m),
            None => super::time::Zone::Offset,
        };
        match self.snaps.iter().find(|x| Some(&x.id) == self.snap.as_ref()) {
            Some(x) => {
                let full = zone.show(x.time, x.offset);
                let date: String = full.chars().take(11).collect();
                (full, date, x.bytes)
            }
            None => (String::new(), String::new(), None),
        }
    }

    pub(crate) fn host_name(&self) -> String {
        match self.chosen() {
            Some(m) => m.name().as_str().to_string(),
            None => self.host.as_deref().map_or_else(|| "this machine".to_string(), |h| Clean::line(h, cap::HOSTNAME).as_str().to_string()),
        }
    }

    /// Put old disk SRC on candidate C, with the fit agreed (None: as it
    /// is).
    fn map_to(&mut self, src: &str, c: &Disk, fit: Option<(u64, bool)>) {
        self.map.retain(|(s, n, _)| s != src && *n != c.node);
        self.map.push((src.to_string(), c.node.clone(), c.arg()));
        self.fit.retain(|(s, _, _)| s != src);
        if let Some((p, decided)) = fit {
            self.fit.push((src.to_string(), p, decided));
            // bko-holt refuses --reformat with --fit-largest (a new type
            // has no figures to resize from): Advanced's choice goes, and
            // Review doesn't offer it again.
            self.reformat.clear();
        }
        self.serials.clear();
    }

    // ---- The overview ----

    pub(crate) fn disks(&mut self, ui: &mut dyn Ui) -> Go {
        match self.layout_file(ui) {
            Ok(_) => {}
            Err(super::layout::LayoutErr::None(n)) => return self.no_layout(ui, &n),
            Err(super::layout::LayoutErr::Go(g)) => return g,
        }
        // The LUKS2 headers the layout kept, from the backup, before the
        // first plan (holt-json.md's open question 12).
        if let Err(g) = self.header_dir(ui) {
            return g;
        }
        let Some(l) = self.full_load(Stage::Plan, true) else { return Go::Back };
        let p = match self.plan_run(ui, &l, s::PLANNING) {
            Ok(p) => p,
            Err(g) => return g,
        };
        self.plan = Some(p.clone());
        let host = self.host_name();
        let (_, date, _) = self.snap_when();
        let full = ui.full_screen();
        let mut items = Vec::new();
        let mut plain = format!("{}\n", s::had_disks(&host, p.disks.len()));
        let mut own = Vec::new();
        for (i, pair) in p.disks.iter().enumerate() {
            let head = old_disk(i, pair);
            let mut rows = vec![head.clone()];
            plain.push_str(&format!("{head}.\n"));
            match (&pair.target, pair.target.as_ref().and_then(|t| p.candidate(&t.node))) {
                (Some(t), Some(c)) => {
                    let name = disk_name(c.model.as_deref(), &c.node);
                    let same = t.same_identity && t.matched_by != "map";
                    let goes = if same {
                        let by = if t.matched_by == "wwid" { s::BY_WWID.to_string() } else { serial_of(c.serial.as_deref()).map(|x| s::by_serial(&x)).unwrap_or_else(|| s::BY_IDENTITY.to_string()) };
                        s::same_disk(&by)
                    } else {
                        s::goes_to(&name, &gb(c.size_bytes), serial_of(c.serial.as_deref()).as_deref())
                    };
                    rows.push(format!("  -> {goes}"));
                    let chosen = if t.matched_by == "map" { s::CHOSEN_BY_YOU } else { "" };
                    let erased = format!("     {}", s::WILL_BE_ERASED);
                    rows.push(format!("{erased}{:>w$}", chosen, w = 66usize.saturating_sub(erased.len())));
                    plain.push_str(&format!("  {}\n", s::plain_goes(&goes, !chosen.is_empty())));
                    if same {
                        own.push(s::own_disk(i + 1, &host, &role(pair), &date));
                        plain.push_str(&format!("  {}\n", s::plain_own(&date)));
                    } else {
                        plain.push_str(&format!("  {}\n", s::PLAIN_ERASED));
                    }
                }
                _ => {
                    rows.push(format!("  -> {}", s::NO_TARGET));
                    plain.push_str(&format!("  {}\n", s::PLAIN_NO_TARGET));
                }
            }
            items.push((o(&rows.join("\n")), Clean::default()));
        }
        // Where the layout came from, first (layout.rs's `layout_note`).
        let mut after: Vec<String> = self.layout_note.iter().cloned().chain(own).collect();
        let mut plain_after: Vec<String> = self.layout_note.iter().cloned().collect();
        for c in p.candidates.iter().filter(|c| c.target_of.is_none()) {
            let name = disk_name(c.model.as_deref(), &c.node);
            match &c.refused {
                None => {
                    let serial = serial_of(c.serial.as_deref()).map(|x| format!(", serial {x}")).unwrap_or_default();
                    after.push(format!("{:<14}{name}, {}{serial}\n{:<14}{}", s::NOT_USED, gb(c.size_bytes), "", contents(c)));
                    plain_after.push(format!("{} {name}, {}{serial}.\n  {}.", s::NOT_USED, gb(c.size_bytes), s::it(&contents(c))));
                }
                Some((code, text)) => {
                    let why = refusal(code, text);
                    after.push(format!("{:<14}{name}, {}: {why}", s::NOT_OFFERED, gb(c.size_bytes)));
                    plain_after.push(format!("{} {name}, {}: {why}.", s::NOT_OFFERED, gb(c.size_bytes)));
                }
            }
        }
        let mut m = Menu::new(Chrome::ours(s::SCR_DISKS, s::DISKS_TITLE), o(&s::had_disks_choose(&host, p.disks.len())), items);
        m.after = Clean::text(&after.join("\n"), cap::PROBLEM);
        m.chrome.keys = Some(o(s::DISKS_KEYS));
        m.notice = self.disks_notice.take().map(|n| Clean::text(&n, cap::PROBLEM));
        // On the first old disk with nowhere to go, else on OK.
        match p.disks.iter().position(|d| d.target.is_none()) {
            Some(i) => m.start = i,
            None => m.focus_button = Some(0),
        }
        if full {
            m.buttons = vec![Button::other(s::BTN_OK, None, B_DONE), Button::back()];
        } else {
            m.buttons = vec![Button::other(s::BTN_THESE_ARE_RIGHT, Some('o'), B_DONE), Button::back(), Button::shell()];
            plain.push_str(&plain_after.join("\n"));
            m.plain_text = Some(Clean::text(&plain, cap::PROBLEM * 4));
            m.plain_items = (0..p.disks.len()).map(|i| o(&s::change_where(i + 1))).collect();
        }
        // Inside Disks (a disk's list, its size screens, OK's plans and
        // what follows them), Back and a problem's Back come back to this
        // overview; only its own Back goes to Snapshot.
        let here = |g: Go| if g == Go::Back { Go::Stay } else { g };
        match ui.menu(&m) {
            Answer::Ok(i) if i < p.disks.len() => match self.one_disk(ui, &p, i) {
                Some(g) => here(g),
                None => Go::Stay,
            },
            Answer::Button(B_DONE) => here(self.disks_done(ui, &p)),
            a => Go::of(a),
        }
    }

    /// OK on the overview: every old disk has somewhere to go, the plan
    /// with all of them named has no problem of the disks', and on.
    fn disks_done(&mut self, ui: &mut dyn Ui, p: &Plan) -> Go {
        if let Some(i) = p.disks.iter().position(|d| d.target.is_none()) {
            self.disks_notice = Some(s::missing_target(i + 1));
            return Go::Stay;
        }
        // Each mapping named, so the plan reviewed can't move with the
        // disks found.
        for d in &p.disks {
            let Some(c) = p.target_of(&d.source.name) else { continue };
            if !self.map.iter().any(|(s, n, _)| *s == d.source.name && *n == c.node) {
                let src = d.source.name.clone();
                let keep: Vec<(String, u64, bool)> = self.fit.iter().filter(|f| f.0 == src).cloned().collect();
                let c = c.clone();
                self.map_to(&src, &c, keep.first().map(|f| (f.1, f.2)));
            }
        }
        let Some(l) = self.full_load(Stage::Plan, true) else { return Go::Back };
        let p = match self.plan_run(ui, &l, s::PLANNING) {
            Ok(p) => p,
            Err(g) => return g,
        };
        let first = p.problems_in(plan::DISK_CODES).next().map(|x| x.text.clone());
        if let Some(t) = first {
            self.disks_notice = Some(s::disk_problem(Clean::line(&t, cap::PROBLEM).as_str()));
            self.plan = Some(p);
            return Go::Stay;
        }
        self.plan = Some(p);
        self.status = None;
        self.resume = None;
        // What bko-holt finds on these disks already: a resume's
        // passphrase is the one given then.
        let Some(l) = self.full_load(Stage::Status, false) else { return Go::Back };
        match self.status_run(ui, &l) {
            Ok(st) => self.status = Some(st),
            Err(g) => return g,
        }
        self.after_disks(ui)
    }

    pub(crate) fn status_run(&mut self, ui: &mut dyn Ui, l: &Load) -> Result<plan::Status, Go> {
        let v = self.holt_doc(ui, l, "bko-holt-status", s::STATUS_TITLE, s::STATUS_TEXT)?;
        match plan::read_status(&v) {
            Ok(st) => Ok(st),
            Err(why) => Err(self.plan_failed(ui, &why)),
        }
    }

    // ---- One old disk ----

    /// The list for old disk I: None to go back to the overview (which
    /// plans again), else where to.
    fn one_disk(&mut self, ui: &mut dyn Ui, p: &Plan, i: usize) -> Option<Go> {
        let pair = p.disks.get(i)?.clone();
        let src = pair.source.name.clone();
        let offered: Vec<Disk> = p.candidates.iter().filter(|c| c.refused.is_none() && c.target_of.as_deref().is_none_or(|t| t == src)).cloned().collect();
        let host = self.host_name();
        let mut text = format!("{}\n  {}", s::from_backup_of(&host), old_disk(i, &pair));
        if let Some(x) = serial_of(pair.source.serial.as_deref()) {
            text.push_str(&format!("\n  serial {x}"));
        }
        text.push_str("\n\n");
        text.push_str(s::PUT_IT_ON);
        let mut items = Vec::new();
        let mut plain_items = Vec::new();
        let mut blocked = Vec::new();
        for (j, c) in offered.iter().enumerate() {
            let name = disk_name(c.model.as_deref(), &c.node);
            let serial = serial_of(c.serial.as_deref()).map(|x| format!("serial {x}")).unwrap_or_else(|| s::NO_SERIAL.to_string());
            let (line, why) = size_line(&pair, c);
            // The serial on a row of its own when it doesn't fit beside
            // the name: never cut.
            let first = format!("{name:<24}{:>7}  {serial}", gb(c.size_bytes));
            let mut rows = if first.chars().count() <= RADIO_ROW { vec![first] } else { vec![format!("{name:<24}{:>7}", gb(c.size_bytes)), format!("  {serial}")] };
            // The rest indented, wrapped at the row's end, never cut.
            for l in std::iter::once(contents(c)).chain(line.clone()) {
                for w in crate::text::wrap(&Clean::text(&l, cap::PROBLEM), RADIO_ROW - 2) {
                    rows.push(format!("  {}", w.as_str()));
                }
            }
            items.push(Clean::text(&rows.join("\n"), cap::PROBLEM));
            let short = match fits(c.size_for(&src)) {
                Fits::MuchSmaller(_) | Fits::BeyondLimit => format!(": {}", s::MUCH_SMALLER),
                Fits::Refused => format!(": {}", s::TOO_SMALL),
                Fits::LittleSmaller(_) => format!(": {}", s::A_LITTLE_SMALLER),
                Fits::LittleBigger(_) | Fits::MuchBigger => format!(": {}", s::BIGGER),
                Fits::Same => String::new(),
            };
            plain_items.push(o(&format!("{name}, {}, {serial}{short}", gb(c.size_bytes))));
            if let Some(w) = why {
                blocked.push((j, Clean::text(&w, cap::PROBLEM)));
            }
        }
        let mut after = Vec::new();
        for c in p.candidates.iter().filter(|c| c.refused.is_none() && c.target_of.as_deref().is_some_and(|t| t != src)) {
            let n = p.disks.iter().position(|d| Some(d.source.name.as_str()) == c.target_of.as_deref()).map_or(0, |k| k + 1);
            after.push(format!("{:<15}{}, {}", s::ALREADY_USED, disk_name(c.model.as_deref(), &c.node), s::for_old_disk(n)));
        }
        for c in p.candidates.iter().filter(|c| c.refused.is_some()) {
            let (code, t) = c.refused.clone().unwrap_or_default();
            after.push(format!("{:<15}{}, {}: {}", s::NOT_OFFERED, disk_name(c.model.as_deref(), &c.node), gb(c.size_bytes), refusal(&code, &t)));
        }
        if offered.is_empty() {
            let m = Message::new(Chrome::ours(s::SCR_DISKS, &s::one_disk_title(i + 1, p.disks.len())), Clean::text(&format!("{text}\n\n{}\n\n{}", s::NO_CANDIDATES, after.join("\n")), cap::PROBLEM));
            return match ui.message(&m) {
                Answer::Ok(()) | Answer::Back => None,
                a => Some(Go::of(a)),
            };
        }
        let current = pair.target.as_ref().and_then(|t| offered.iter().position(|c| c.node == t.node));
        let mut r = Radio::new(Chrome::ours(s::SCR_DISKS, &s::one_disk_title(i + 1, p.disks.len())), Clean::text(&text, cap::PROBLEM), items, Some(current.unwrap_or(0)));
        r.after = Clean::text(&after.join("\n"), cap::PROBLEM);
        r.blocked = blocked;
        r.plain_items = plain_items;
        r.chrome.keys = Some(o(s::ONE_DISK_KEYS));
        loop {
            match ui.radio(&r) {
                Answer::Ok(j) => {
                    let c = offered.get(j)?;
                    r.chosen = Some(j);
                    match self.take(ui, p, &pair, c) {
                        Ok(true) => return None,
                        Ok(false) => continue,
                        Err(g) => return Some(g),
                    }
                }
                Answer::Back => return None,
                a => return Some(Go::of(a)),
            }
        }
    }

    /// Candidate C taken for PAIR: true when it is (back to the
    /// overview), false to choose again, else where to go.
    fn take(&mut self, ui: &mut dyn Ui, p: &Plan, pair: &Pair, c: &Disk) -> Result<bool, Go> {
        let src = pair.source.name.clone();
        let z = c.size_for(&src).cloned().unwrap_or_default();
        match fits(c.size_for(&src)) {
            Fits::Same => {
                self.map_to(&src, c, None);
                Ok(true)
            }
            Fits::MuchBigger => self.much_bigger(ui, p, pair, c),
            Fits::LittleSmaller(pc) | Fits::LittleBigger(pc) => self.size_offer(ui, p, pair, c, &z, pc),
            Fits::MuchSmaller(pc) => self.decide(ui, p, pair, c, &z, Some(pc)),
            Fits::BeyondLimit => self.decide(ui, p, pair, c, &z, None),
            // The list doesn't take a refused row; this is the same.
            Fits::Refused => Ok(false),
        }
    }

    /// A second plan with the fit asked, the pair mapped: its plan, or
    /// where to go.  Its run's output is Raw output's.
    fn fit_plan(&mut self, ui: &mut dyn Ui, src: &str, c: &Disk, pc: u64) -> Result<Plan, Go> {
        let mut l = self.full_load(Stage::Plan, true).ok_or(Go::Back)?;
        l.map.retain(|(s, _)| s != src);
        l.erase.retain(|e| *e != c.arg());
        l.map.push((src.to_string(), c.arg()));
        l.erase.push(c.arg());
        l.fit_largest = Some(l.fit_largest.unwrap_or(0).max(pc));
        self.plan_run(ui, &l, s::PLANNING_FIT)
    }

    /// The lines the second plan adds: another disk's largest partition
    /// growing too, as `--fit-largest` applies to every mapped disk.
    fn elsewhere(&self, fp: &Plan, src: &str) -> Vec<String> {
        let mut v = Vec::new();
        for f in fp.fit.iter().filter(|f| f.disk != src) {
            let (Some(a), Some(b)) = (f.old_sectors, f.new_sectors) else { continue };
            if b <= a {
                continue;
            }
            let pair = fp.disks.iter().find(|d| d.source.name == f.disk);
            let m = pair.and_then(|d| d.source.largest.as_ref()).and_then(|l| l.mount.clone().or(l.partition.clone())).unwrap_or_else(|| "the largest partition".into());
            let name = fp.candidate(&f.target).map(|c| disk_name(c.model.as_deref(), &c.node)).unwrap_or_else(|| f.target.clone());
            v.push(s::grows_elsewhere(&name, Clean::line(&m, 64).as_str(), &gb(a * f.sector), &gb(b * f.sector)));
        }
        v
    }

    /// The offer: a disk up to 10 % smaller (or only free space lost),
    /// or up to 10 % bigger.
    fn size_offer(&mut self, ui: &mut dyn Ui, p: &Plan, pair: &Pair, c: &Disk, z: &Size, pc: u64) -> Result<bool, Go> {
        let src = pair.source.name.clone();
        let fp = match self.fit_plan(ui, &src, c, pc) {
            Ok(x) => x,
            Err(Go::Stay) => return Ok(false),
            Err(g) => return Err(g),
        };
        let smaller = c.size_bytes < pair.source.size_bytes;
        let (old_name, new_name) = (disk_name(pair.source.model.as_deref(), &src), disk_name(c.model.as_deref(), &c.node));
        let n = p.disks.iter().position(|d| d.source.name == src).map_or(1, |k| k + 1);
        let diff = gb(c.size_bytes.abs_diff(pair.source.size_bytes));
        let mut text = s::was_is(n, &old_name, &gb(pair.source.size_bytes), &new_name, &gb(c.size_bytes), &diff, smaller);
        // bko-holt's second plan has the last word: a refusal of this
        // pair there is shown, and nothing can be taken.
        let refused = fp.problems.iter().find(|x| x.disk.as_deref() == Some(src.as_str()) && plan::DISK_CODES.contains(&x.code.as_str()));
        let m = mount_of(z);
        let (old, new) = (z.old_bytes.map(gb).unwrap_or_default(), z.new_bytes.map(gb).unwrap_or_default());
        let system = z.mount.as_deref() == Some("/");
        if let Some(x) = refused {
            text.push_str("\n\n");
            text.push_str(&s::fit_refused(Clean::line(&x.text, cap::PROBLEM).as_str()));
        } else if smaller && z.verdict == "smaller_slack" {
            text.push_str("\n\n");
            text.push_str(s::SLACK_TEXT);
        } else if smaller {
            text.push_str("\n\n");
            text.push_str(&s::shrink_text(system, &m, &old, &new, &pct(z.change_pct.unwrap_or(0.0)), z.used_bytes.map(gb).as_deref()));
        } else {
            text.push_str("\n\n");
            text.push_str(&s::grow_text(system, &m, &old, &new, &pct(z.change_pct.unwrap_or(0.0)), &diff));
        }
        for e in self.elsewhere(&fp, &src) {
            text.push('\n');
            text.push_str(&e);
        }
        if smaller && refused.is_none() {
            text.push_str("\n\n");
            text.push_str(s::OR_ANOTHER);
        }
        let title = if smaller { s::LITTLE_SMALLER_TITLE } else { s::LITTLE_BIGGER_TITLE };
        let mut msg = Message::new(Chrome::ours(s::SCR_DISKS, title), Clean::text(&text, cap::PROBLEM));
        msg.buttons = Vec::new();
        if refused.is_none() {
            msg.buttons.push(Button::other(if smaller { s::BTN_SHRINK } else { s::BTN_GROW }, None, B_SHRINK));
            if !smaller {
                msg.buttons.push(Button::other(s::BTN_KEEP_SIZES, None, B_KEEP));
            }
        }
        // A bigger disk's offer has Keep in its place (four buttons don't
        // fit the box); Esc and Back are the disk list either way.
        if smaller || refused.is_some() {
            msg.buttons.push(Button::other(s::BTN_ANOTHER_DISK, None, B_ANOTHER));
        }
        msg.buttons.push(Button::other(s::BTN_RAW, Some('o'), B_RAW));
        // A fix is never the default: another disk, or keeping the sizes.
        msg.focus = if refused.is_some() { 0 } else { 1 };
        msg.chrome.keys = Some(o(s::OFFER_KEYS));
        loop {
            match ui.message(&msg) {
                Answer::Button(B_SHRINK) => {
                    self.map_to(&src, c, Some((pc, false)));
                    return Ok(true);
                }
                Answer::Button(B_KEEP) => {
                    self.map_to(&src, c, None);
                    return Ok(true);
                }
                Answer::Button(B_RAW) => {
                    if let Some(g) = self.raw_view(ui) {
                        return Err(g);
                    }
                }
                Answer::Button(_) | Answer::Ok(()) | Answer::Back => return Ok(false),
                a => return Err(Go::of(a)),
            }
        }
    }

    /// More than 10 % smaller, the data fitting: the user decides, and
    /// nothing is chosen at first.  PC None: bko-holt can't shrink this
    /// far yet (`beyond_limit`), and only another disk is offered.
    fn decide(&mut self, ui: &mut dyn Ui, p: &Plan, pair: &Pair, c: &Disk, z: &Size, pc: Option<u64>) -> Result<bool, Go> {
        let src = pair.source.name.clone();
        let mut refused: Option<String> = None;
        if let Some(pc) = pc {
            match self.fit_plan(ui, &src, c, pc) {
                Ok(fp) => {
                    refused = fp.problems.iter().find(|x| x.disk.as_deref() == Some(src.as_str()) && plan::DISK_CODES.contains(&x.code.as_str())).map(|x| x.text.clone());
                }
                Err(Go::Stay) => return Ok(false),
                Err(g) => return Err(g),
            }
        }
        let (old_name, new_name) = (disk_name(pair.source.model.as_deref(), &src), disk_name(c.model.as_deref(), &c.node));
        let n = p.disks.iter().position(|d| d.source.name == src).map_or(1, |k| k + 1);
        let diff = gb(c.size_bytes.abs_diff(pair.source.size_bytes));
        let m = mount_of(z);
        let (old, new) = (z.old_bytes.map(gb).unwrap_or_default(), z.new_bytes.map(gb).unwrap_or_default());
        let used = z.used_bytes.map(gb).unwrap_or_default();
        let mut text = s::was_is(n, &old_name, &gb(pair.source.size_bytes), &new_name, &gb(c.size_bytes), &diff, true);
        text.push_str("\n\n");
        text.push_str(&s::decide_sizes(&m, &pct(z.change_pct.unwrap_or(0.0)), &old, &new, &used, pc.is_some()));
        let mut rows = Vec::new();
        match (&refused, pc) {
            (Some(why), _) => {
                text.push_str("\n\n");
                text.push_str(&s::fit_refused(Clean::line(why, cap::PROBLEM).as_str()));
            }
            (None, Some(_)) => {
                text.push_str("\n\n");
                text.push_str(s::DECIDE_WHY);
                rows.push(o(&s::shrink_row(&m, &new, &new_name)));
            }
            (None, None) => {
                text.push_str("\n\n");
                text.push_str(s::BEYOND_LIMIT);
            }
        }
        rows.push(o(s::CHOOSE_ANOTHER));
        let shrink_row = rows.len() == 2;
        let mut r = Radio::new(Chrome::ours(s::SCR_DISKS, s::MUCH_SMALLER_TITLE), Clean::text(&text, cap::PROBLEM), rows.clone(), None);
        r.none = o(s::CHOOSE_ONE_FIRST);
        r.plain_items = rows;
        r.buttons = vec![Button::ok(), Button::other(s::BTN_RAW, Some('o'), B_RAW), Button::back()];
        r.chrome.keys = Some(o(s::DECIDE_KEYS));
        loop {
            match ui.radio(&r) {
                Answer::Ok(0) if shrink_row => {
                    self.map_to(&src, c, pc.map(|x| (x, true)));
                    return Ok(true);
                }
                Answer::Button(B_RAW) => {
                    if let Some(g) = self.raw_view(ui) {
                        return Err(g);
                    }
                }
                Answer::Ok(_) | Answer::Back | Answer::Button(_) => return Ok(false),
                a => return Err(Go::of(a)),
            }
        }
    }

    /// More than 10 % bigger: keep the old sizes, chosen at first.  Grow
    /// to fill waits for `--fit SRC=fill` (RT10n): until bko-holt gives
    /// `sizes[].grow`, its row isn't there and the text says so.
    fn much_bigger(&mut self, ui: &mut dyn Ui, p: &Plan, pair: &Pair, c: &Disk) -> Result<bool, Go> {
        let src = pair.source.name.clone();
        let (old_name, new_name) = (disk_name(pair.source.model.as_deref(), &src), disk_name(c.model.as_deref(), &c.node));
        let n = p.disks.iter().position(|d| d.source.name == src).map_or(1, |k| k + 1);
        let diff = gb(c.size_bytes.abs_diff(pair.source.size_bytes));
        let mut text = s::was_is(n, &old_name, &gb(pair.source.size_bytes), &new_name, &gb(c.size_bytes), &diff, false);
        text.push_str("\n\n");
        text.push_str(&s::much_bigger_text(&diff));
        let rows = vec![o(&s::keep_row(&diff))];
        let mut r = Radio::new(Chrome::ours(s::SCR_DISKS, s::MUCH_BIGGER_TITLE), Clean::text(&text, cap::PROBLEM), rows.clone(), Some(0));
        r.plain_items = rows;
        r.empty_takes = true;
        r.buttons = vec![Button::ok(), Button::other(s::BTN_RAW, Some('o'), B_RAW), Button::back()];
        r.chrome.keys = Some(o(s::MUCH_BIGGER_KEYS));
        loop {
            match ui.radio(&r) {
                Answer::Ok(_) => {
                    self.map_to(&src, c, None);
                    return Ok(true);
                }
                Answer::Button(B_RAW) => {
                    if let Some(g) = self.raw_view(ui) {
                        return Err(g);
                    }
                }
                Answer::Back | Answer::Button(_) => return Ok(false),
                a => return Err(Go::of(a)),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn contents_and_refusals_in_words() {
        let mut d = Disk { node: "sdb".into(), blank: true, ..Disk::default() };
        assert_eq!(contents(&d), s::BLANK);
        d.blank = false;
        d.contents = vec![plan::Content { dev: "sdb1".into(), kind: Some("ext4".into()), label: Some("photos".into()), used_bytes: Some(312_000_000_000) }];
        assert_eq!(contents(&d), "has data: 1 partition, ext4 \"photos\", 312 GB used");
        assert_eq!(refusal("boot_disk", "x"), s::REFUSED_STICK);
        assert_eq!(refusal("frobbed", "sdq is frobbed\u{1b}[2J"), "sdq is frobbed?[2J", "an unknown code: bko-holt's text, cleaned");
    }
}
