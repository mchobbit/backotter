//! Passphrases, the keyboard at start-up, the Resume offer, and Review
//! with Commands and Advanced (rescue-screens.md, sections 6 to 8).
//!
//! - Passphrases: one screen per `keys` group whose `unlock` is a
//!   passphrase, typed twice (once for a kept header, or a resume's
//!   containers); each to a new 0600 file in the session's directory,
//!   passed as `--key-file NAME=FILE` for each of the group's names.  An
//!   `unlock` this doesn't know blocks (Principle 6).
//! - Resume (J14 as it is until RT10o): `status --json` with the map
//!   and the keys; shown only when this session's records show bko-holt's
//!   work there.  Go on only when status's `resume.go_on` says so, which
//!   until the `open` stage is only within the session that made the
//!   disks; else Start again alone.
//! - Review: `plan --json` with the full argument list (holt_args.rs,
//!   `full_load`), `--erase` for every target but on Go on.  The ERASED
//!   list is the plan's `will_erase` (and every target the run writes);
//!   each is confirmed by the last six characters of its serial, typed
//!   (case aside).  Restore's run is then Review's arguments with
//!   `--through close --commit --expect-plan` (`Load::run_of`): RT12's
//!   second part runs it.

use crate::secret::Secret;
use crate::text::{cap, Clean};
use crate::ui::{self, Answer, Button, Chrome, Cycle, CycleRow, Message, Passphrase, Question, Review, SerialRow, TextView, Ui};

use super::disks::{disk_name, serial_of};
use super::holt_args::Stage;
use super::keyboard;
use super::plan::{self, gb, pct, Key, Plan};
use super::strings as s;
use super::sys;
use super::{App, Go, Step, B_KEYBOARD};

pub(crate) const B_COMMANDS: usize = 40;
pub(crate) const B_ADVANCED: usize = 41;
pub(crate) const B_GO_ON: usize = 42;
pub(crate) const B_START_AGAIN: usize = 43;
pub(crate) const B_UNDO: usize = 44;
pub(crate) const B_LEAVE: usize = 45;

/// Lines of the plan's text Commands shows, at most.
pub const COMMANDS_MAX: usize = 20_000;

/// How many characters of a serial confirm it (rescue-screens.md, "8.
/// Review").
pub const SERIAL_TAIL: usize = 6;

fn o(s: &str) -> Clean {
    Clean::ours(s)
}

/// The unlock kinds this asks a passphrase for; `temporary` asks nothing.
const ASKED: &[&str] = &["passphrase", "old_passphrase"];

/// A type as people write it.
pub fn fs_name(t: &str) -> String {
    match t {
        "xfs" => "XFS".to_string(),
        t => Clean::line(t, 16).as_str().to_string(),
    }
}

/// What confirms a disk: the last six characters of its serial, or, with
/// no serial of its own (none, or one another disk shares), its size and
/// model as the row shows them (rescue-screens.md: "A disk with no serial
/// asks for its size and model instead").
pub fn confirm_by(c: &plan::Disk) -> (String, bool) {
    match c.serial.as_deref().filter(|_| !c.serial_shared).map(|x| Clean::line(x, cap::SERIAL).as_str().to_string()) {
        Some(x) => {
            let n = x.chars().count();
            (x.chars().skip(n.saturating_sub(SERIAL_TAIL)).collect(), true)
        }
        None => {
            let mut e = gb(c.size_bytes);
            if let Some(m) = c.model.as_deref() {
                e.push(' ');
                e.push_str(Clean::line(m, cap::MODEL).as_str());
            }
            (e, false)
        }
    }
}

/// `2 3 1 8 E 6`: a serial a character at a time, for a screen reader.
pub fn spelled(x: &str) -> String {
    x.chars().map(String::from).collect::<Vec<_>>().join(" ")
}

impl App {
    /// The keys the user types, in the plan's order.
    pub(crate) fn asked_keys(&self) -> Vec<Key> {
        self.plan.as_ref().map(|p| p.keys.iter().filter(|k| k.unlock != "temporary").cloned().collect()).unwrap_or_default()
    }

    /// After Disks: the start-up keyboard offer and the passphrases, then
    /// Resume or Review.
    pub(crate) fn after_disks(&mut self, ui: &mut dyn Ui) -> Go {
        let keys = self.asked_keys();
        if let Some(k) = keys.iter().find(|k| !ASKED.contains(&k.unlock.as_str())) {
            return self.key_blocked(ui, k);
        }
        // Key files from an earlier plan whose groups are gone.
        let groups: Vec<String> = keys.iter().map(|k| k.group.clone()).collect();
        self.keys.retain(|(g, _, p)| {
            let keep = groups.contains(g);
            if !keep {
                sys::wipe_file(p);
            }
            keep
        });
        if keys.is_empty() {
            return self.after_keys(ui);
        }
        if let Some(g) = self.boot_keyboard(ui) {
            return g;
        }
        Go::To(Step::Passphrase(0))
    }

    /// A key this stick can't ask for: say so, never skip it.
    fn key_blocked(&mut self, ui: &mut dyn Ui, k: &Key) -> Go {
        let why = if k.unlock == "key_file_elsewhere" { s::KEY_FILE_ELSEWHERE.to_string() } else { s::unlock_unknown(Clean::line(&k.unlock, 32).as_str()) };
        let p = super::Problem {
            code: "key_unasked",
            screen: s::SCR_PASSPHRASES,
            title: s::KEY_BLOCKED_TITLE.to_string(),
            what: s::key_blocked_what(Clean::line(&k.group, 64).as_str()),
            why,
            todo: s::KEY_BLOCKED_TODO.to_string(),
            fixes: Vec::new(),
            again: false,
            back: Some(Go::Back),
        };
        self.problem(ui, p)
    }

    /// The keyboard at start-up (rescue-screens.md): once, before the
    /// first passphrase, when the machine's start-up prompt takes another
    /// layout than the one set.  Not over ssh or serial, not where this
    /// stick can't change the layout, not when the dump doesn't say
    /// (every dump before RT10l: then the passphrase screen says so).
    fn boot_keyboard(&mut self, ui: &mut dyn Ui) -> Option<Go> {
        if !self.keyboard.can_change || self.wh.ssh || self.wh.serial || self.boot_offered {
            return None;
        }
        let m = self.chosen()?.clone();
        let want = keyboard::boot_prompt(m.keyboard.as_ref())?;
        want.row()?;
        if want == self.keyboard.set {
            return None;
        }
        self.boot_offered = true;
        let (now, short) = (self.keyboard.set.name(), want.short());
        let mut q = Question::new(Chrome::ours(s::SCR_PASSPHRASES, s::BOOT_KB_TITLE), Clean::text(&s::boot_kb(m.name().as_str(), &now, &want.name()), cap::PROBLEM));
        q.yes = o(&s::btn_switch(&want.name()));
        q.no = o(&s::btn_keep(&short));
        q.buttons = vec![Button::back()];
        match ui.yes_no(&q) {
            Answer::Ok(true) => {
                if let Err(e) = self.set_layout(&want) {
                    return Some(self.layout_failed(ui, &want, e));
                }
                self.keyboard.set = want;
                self.keyboard.from = keyboard::From::Backup(m.name().as_str().to_string());
                None
            }
            Answer::Ok(false) => None,
            a => {
                self.boot_offered = false;
                Some(Go::of(a))
            }
        }
    }

    /// The keyboard line on Passphrases.
    fn boot_keyboard_line(&self) -> String {
        let name = self.host_name();
        if self.wh.ssh {
            return s::KEYBOARD_SSH.to_string();
        }
        if self.wh.serial {
            return s::KEYBOARD_SERIAL.to_string();
        }
        let set = self.keyboard.set.name();
        match self.chosen().and_then(|m| keyboard::boot_prompt(m.keyboard.as_ref())) {
            Some(x) if x == self.keyboard.set => s::kb_as_at_start(&set, &name),
            Some(x) => s::kb_not_at_start(&set, &name, &x.name()),
            None => s::kb_start_unknown(&set, &name),
        }
    }

    // ---- 6. Passphrases ----

    pub(crate) fn passphrase(&mut self, ui: &mut dyn Ui, n: usize) -> Go {
        let keys = self.asked_keys();
        let Some(k) = keys.get(n).cloned() else { return self.after_keys(ui) };
        let resume = self.status.as_ref().is_some_and(plan::Status::work_found);
        let once = k.unlock == "old_passphrase" || !k.new_ok || resume;
        let holds: Vec<String> = k.holds.iter().take(6).map(|h| Clean::line(h, 64).as_str().to_string()).collect();
        let names: Vec<String> = k.names.iter().take(6).map(|h| Clean::line(h, 64).as_str().to_string()).collect();
        let mut text = s::opens(&names, &holds);
        text.push_str("\n\n");
        text.push_str(if resume {
            s::PASS_RESUME
        } else if once {
            s::PASS_KEPT_HEADER
        } else {
            s::PASS_NEW
        });
        let labels = if once { vec![o(s::LABEL_PASSPHRASE)] } else { vec![o(s::LABEL_PASSPHRASE), o(s::LABEL_AGAIN)] };
        let mut d = Passphrase::new(Chrome::ours(s::SCR_PASSPHRASES, &s::passphrase_title(n + 1, keys.len())), Clean::text(&text, cap::PROBLEM), labels);
        d.after = Clean::text(&self.boot_keyboard_line(), cap::PROBLEM);
        d.chrome.keys = Some(o(s::PASSPHRASE_KEYS));
        if self.keyboard.can_change {
            d.buttons.insert(1, Button::other(s::BTN_KEYBOARD, None, B_KEYBOARD));
        }
        loop {
            match ui.passphrase(&d) {
                Answer::Ok(v) => {
                    let first: &Secret = match v.first() {
                        Some(x) => x,
                        None => return Go::Stay,
                    };
                    let len = first.len() as u64;
                    if first.is_empty() {
                        d.notice = Some(o(s::PASS_EMPTY));
                    } else if v.iter().any(|x| x.expose() != first.expose()) {
                        d.notice = Some(o(s::PASS_MISMATCH));
                    } else if len < k.min_bytes || len > k.max_bytes {
                        d.notice = Some(o(&s::pass_length(k.min_bytes, k.max_bytes)));
                    } else {
                        let i = self.plan.as_ref().and_then(|p| p.keys.iter().position(|x| x.group == k.group)).unwrap_or(n);
                        match self.session.secret_file(&format!("k{i}"), first) {
                            Ok(path) => {
                                self.keys.retain(|(g, _, _)| *g != k.group);
                                self.keys.push((k.group.clone(), k.names.clone(), path));
                                self.serials.clear();
                            }
                            Err(e) => return self.failed_here(ui, s::SESSION_FAILED, &e.to_string()),
                        }
                        drop(v);
                        return if n + 1 < keys.len() { Go::To(Step::Passphrase(n + 1)) } else { self.after_keys(ui) };
                    }
                }
                Answer::Button(B_KEYBOARD) => match self.keyboard_screen(ui) {
                    Go::Stay => {
                        d.after = Clean::text(&self.boot_keyboard_line(), cap::PROBLEM);
                    }
                    g => return g,
                },
                a => return Go::of(a),
            }
        }
    }

    /// The keys given: Resume when bko-holt's work is there, else Review.
    pub(crate) fn after_keys(&mut self, ui: &mut dyn Ui) -> Go {
        let Some(l) = self.full_load(Stage::Status, false) else { return Go::Back };
        match self.status_run(ui, &l) {
            Ok(st) => self.status = Some(st),
            Err(g) => return g,
        }
        if self.status.as_ref().is_some_and(plan::Status::work_found) {
            Go::To(Step::Resume)
        } else {
            self.resume = None;
            Go::To(Step::Review)
        }
    }

    // ---- 7. Resume offer ----

    pub(crate) fn resume_screen(&mut self, ui: &mut dyn Ui) -> Go {
        let Some(st) = self.status.clone() else { return Go::Swap(Step::Review) };
        let Some(p) = self.plan.clone() else { return Go::Back };
        let host = self.host_name();
        let mut text = s::resume_head(&host);
        for (_, node, _) in &self.map {
            if let Some(c) = p.candidate(node) {
                let serial = serial_of(c.serial.as_deref()).map(|x| format!(", serial {x}")).unwrap_or_default();
                text.push_str(&format!("\n  {}, {}{serial}", disk_name(c.model.as_deref(), &c.node), gb(c.size_bytes)));
            }
        }
        text.push('\n');
        let cells: Vec<String> = st
            .stages
            .iter()
            .filter_map(|r| {
                let label = crate::demo::stage_label(&r.name)?;
                let mark = if r.state == "done" { "[x]" } else { "[ ]" };
                let mut c = format!("{mark} {label}");
                if r.name == "restore" && r.state != "done" && st.restore_started {
                    c = format!("{c:<30}{}", s::STARTED_NOT_FINISHED);
                }
                Some(c)
            })
            .collect();
        // Two a row, as the mock; a long one alone.
        let mut i = 0;
        while i < cells.len() {
            if cells[i].len() <= 30 && cells.get(i + 1).is_some_and(|c| c.len() <= 30) {
                text.push_str(&format!("\n  {:<30}{}", cells[i], cells[i + 1]));
                i += 2;
            } else {
                text.push_str(&format!("\n  {}", cells[i]));
                i += 1;
            }
        }
        text.push_str("\n\n");
        let mut m;
        if st.go_on {
            text.push_str(s::RESUME_CHOICES);
            m = Message::new(Chrome::ours(s::SCR_RESUME, s::RESUME_TITLE), Clean::text(&text, cap::PROBLEM));
            m.buttons = vec![Button::other(s::BTN_GO_ON, None, B_GO_ON), Button::other(s::BTN_START_AGAIN, None, B_START_AGAIN), Button::back()];
        } else {
            let why: Vec<String> = st.why_not.iter().take(4).map(|w| Clean::line(w, cap::LINE).as_str().to_string()).collect();
            text.push_str(&s::resume_only_again(&why.join("; ")));
            m = Message::new(Chrome::ours(s::SCR_RESUME, s::RESUME_TITLE), Clean::text(&text, cap::PROBLEM));
            m.buttons = vec![Button::other(s::BTN_START_AGAIN, None, B_START_AGAIN), Button::back()];
        }
        match ui.message(&m) {
            Answer::Button(B_GO_ON) if st.go_on => {
                self.resume = Some(true);
                self.serials.clear();
                Go::To(Step::Review)
            }
            Answer::Button(B_START_AGAIN) => {
                self.resume = Some(false);
                self.serials.clear();
                Go::To(Step::Review)
            }
            Answer::Ok(()) | Answer::Button(_) => Go::Stay,
            a => Go::of(a),
        }
    }

    // ---- 8. Review ----

    pub(crate) fn review_screen(&mut self, ui: &mut dyn Ui) -> Go {
        let go_on = self.resume == Some(true);
        let Some(l) = self.full_load(Stage::Plan, !go_on) else { return Go::Back };
        let p = match self.plan_run(ui, &l, s::PLANNING_REVIEW) {
            Ok(p) => p,
            Err(g) => return g,
        };
        self.plan = Some(p.clone());
        let full = ui.full_screen();
        let host = self.host_name();
        let (when, date, bytes) = self.snap_when();
        // The stick's name for it (`Debian 13`), else the plan's.
        let os = self.chosen().and_then(|m| m.os.clone()).or_else(|| p.os_name.clone()).map(|x| Clean::line(&x, 64).as_str().to_string());
        let size = bytes.map(gb);
        let mut text = s::review_head(&host, os.as_deref(), &when, size.as_deref(), p.same_machine);
        let mut plain = text.clone();
        plain.push('\n');
        plain.push_str(s::NOTHING_CHANGED);
        text.push_str("\n\n");
        let next = self.status.as_ref().and_then(|st| st.next.clone()).and_then(|n| crate::demo::stage_label(&n)).unwrap_or("the next stage");
        text.push_str(&if go_on { s::review_go_on(next) } else { s::REVIEW_ERASE.to_string() });
        plain.push('\n');
        plain.push_str(&if go_on { s::review_go_on_plain(next) } else { s::REVIEW_ERASE_PLAIN.to_string() });
        // The rows: every target the run writes, in the old disks' order.
        let mut rows = Vec::new();
        let mut seen: Vec<String> = Vec::new();
        for (k, pair) in p.disks.iter().enumerate() {
            let Some(t) = &pair.target else { continue };
            if seen.contains(&t.node) || !(go_on || t.will_erase || !t.writes.is_empty()) {
                continue;
            }
            seen.push(t.node.clone());
            let Some(c) = p.candidate(&t.node) else { continue };
            let word = if go_on {
                s::WORD_GOES_ON
            } else if t.will_erase {
                s::WORD_ERASED
            } else {
                s::WORD_WRITTEN
            };
            let name = disk_name(c.model.as_deref(), &c.node);
            let (expect, by_serial) = confirm_by(c);
            let n = rows.len() + 1;
            let shown = serial_of(c.serial.as_deref()).unwrap_or_else(|| s::NO_SERIAL.to_string());
            let row = if by_serial { format!("{word:<8}{:>7}  {name:<21}  {shown}", gb(c.size_bytes)) } else { format!("{word:<8}{:>7}  {name}", gb(c.size_bytes)) };
            let _ = k;
            let (ask, wrong) = if by_serial {
                (s::ask_serial(n), s::wrong_serial(n, &name))
            } else {
                (s::ask_size(n, &expect), s::wrong_size(n, &name, &expect))
            };
            let head = format!("  {}", s::plain_disk(n, &name, &gb(c.size_bytes)));
            let tail = match &c.serial {
                Some(x) => format!("serial {}", spelled(Clean::line(x, cap::SERIAL).as_str())),
                None => s::NO_SERIAL.to_string(),
            };
            // On one line when it fits plain mode's 72 columns, else the
            // serial on its own, never broken.
            if head.len() + 1 + tail.len() <= crate::plain::WIDTH {
                plain.push_str(&format!("\n{head} {tail}"));
            } else {
                plain.push_str(&format!("\n{head}\n          {tail}"));
            }
            rows.push(SerialRow {
                text: Clean::line(&row, cap::LINE),
                expect,
                width: if by_serial { SERIAL_TAIL as u16 } else { 16 },
                ask: o(&ask),
                wrong: Clean::line(&wrong, cap::PROBLEM),
                confirmed: o(&s::confirmed(n)),
            });
        }
        let mut after: Vec<String> = Vec::new();
        for pair in &p.disks {
            let Some(t) = &pair.target else { continue };
            if t.same_identity {
                if let Some(c) = p.candidate(&t.node) {
                    after.push(s::own_disk_review(&disk_name(c.model.as_deref(), &c.node), &host, &super::disks::role(pair), &date));
                }
            }
        }
        let changes = self.changes(&p);
        let problem = p.problems.first().map(|x| Clean::line(&x.text, cap::PROBLEM).as_str().to_string());
        let problems_line = match &problem {
            Some(t) => s::review_problem(t),
            None => s::NO_PROBLEMS.to_string(),
        };
        let untouched: Vec<String> = p
            .candidates
            .iter()
            .filter(|c| !p.disks.iter().any(|d| d.target.as_ref().is_some_and(|t| t.node == c.node)))
            .map(|c| if c.refused.as_ref().is_some_and(|r| r.0 == "boot_disk") { s::THIS_STICK.to_string() } else { format!("the {}", disk_name(c.model.as_deref(), &c.node)) })
            .collect();
        let untouched = if untouched.is_empty() { s::NOTHING_ELSE.to_string() } else { untouched.join(", ") };
        if changes.is_empty() {
            after.push(format!("{}  {problems_line}", s::CHANGES_NONE));
            after.push(s::not_touched(&untouched));
        } else {
            for (i, c) in changes.iter().enumerate() {
                after.push(if i == 0 { format!("{:<10}{c}", s::CHANGES) } else { format!("{:<10}{c}", "") });
            }
            after.push(format!("{}  {problems_line}", s::not_touched(&untouched)));
        }
        plain.push('\n');
        plain.push_str(&after.join("\n"));
        after.push(s::NOTHING_CHANGED.to_string());
        let mut d = Review {
            chrome: Chrome::ours(s::SCR_REVIEW, s::REVIEW_TITLE),
            text: Clean::text(&text, cap::PROBLEM),
            rows,
            after: Clean::text(&after.join("\n"), cap::PROBLEM * 2),
            blocked: None,
            notice: None,
            plain: Clean::text(&plain, cap::PROBLEM * 4),
            buttons: Vec::new(),
        };
        let advanced = p.reformat_possible && !p.reformat.is_empty();
        let restore = if self.opts.dry_run { s::BTN_SHOW_WHAT } else { s::BTN_RESTORE };
        if full {
            d.buttons.push(Button::new(restore, None, ui::Act::Ok));
            d.buttons.push(Button::other(s::BTN_COMMANDS, Some('c'), B_COMMANDS));
            if advanced {
                d.buttons.push(Button::other(s::BTN_ADVANCED, None, B_ADVANCED));
            }
        } else {
            d.buttons.push(Button::new(if self.opts.dry_run { s::BTN_SHOW_WHAT } else { s::BTN_RESTORE_NOW }, Some('r'), ui::Act::Ok));
            d.buttons.push(Button::other(s::BTN_EVERY_COMMAND, Some('c'), B_COMMANDS));
            if advanced {
                d.buttons.push(Button::other(s::BTN_ADVANCED, Some('a'), B_ADVANCED));
            }
        }
        d.buttons.push(Button::back());
        d.chrome.keys = Some(o(s::REVIEW_KEYS));
        let mut typed = std::mem::take(&mut self.serials);
        loop {
            let a = ui.review(&d, &mut typed);
            match a {
                Answer::Ok(()) => {
                    // The Ui checked them; checked again here, so no Ui
                    // can start a restore unconfirmed.
                    if let Some(i) = d.rows.iter().enumerate().position(|(i, r)| !ui::matches(typed.get(i).map_or("", String::as_str), &r.expect)) {
                        d.notice = Some(d.rows[i].wrong.clone());
                        continue;
                    }
                    if let Some(x) = p.problems.first() {
                        self.serials = typed;
                        return self.review_problem(ui, x);
                    }
                    self.serials = typed;
                    let stage = match (go_on, self.status.as_ref().and_then(|st| st.next.clone())) {
                        (true, Some(n)) => Stage::Only(n),
                        _ => Stage::Through("close".to_string()),
                    };
                    self.reviewed = Some(l.run_of(stage, &p.plan_sha256));
                    return Go::To(Step::Restore);
                }
                Answer::Button(B_COMMANDS) => {
                    if let Some(g) = self.commands(ui, &p) {
                        self.serials = typed;
                        return g;
                    }
                }
                Answer::Button(B_ADVANCED) => match self.another_fs(ui, &p) {
                    Ok(true) => {
                        // What will be written changed: the serials go.
                        return Go::Stay;
                    }
                    Ok(false) => {}
                    Err(g) => {
                        self.serials = typed;
                        return g;
                    }
                },
                Answer::Back => {
                    self.serials.clear();
                    return Go::Back;
                }
                a => {
                    self.serials = typed;
                    return Go::of(a);
                }
            }
        }
    }

    /// Review's "Changes:" lines, from bko-holt's plan: fit's new sizes,
    /// the filesystems chosen, the passphrases.
    fn changes(&self, p: &Plan) -> Vec<String> {
        let mut v = Vec::new();
        for f in &p.fit {
            let (Some(a), Some(b)) = (f.old_sectors, f.new_sectors) else { continue };
            if a == b || a == 0 {
                continue;
            }
            let pair = p.disks.iter().find(|d| d.source.name == f.disk);
            let m = pair.and_then(|d| d.source.largest.as_ref()).and_then(|l| l.mount.clone().or(l.partition.clone())).unwrap_or_else(|| "the largest partition".into());
            let m = Clean::line(&m, 64).as_str().to_string();
            let change = pct((a.abs_diff(b) as f64) * 100.0 / a as f64);
            let (old, new) = (gb(a * f.sector), gb(b * f.sector));
            let decided = self.fit.iter().any(|x| x.0 == f.disk && x.2);
            v.push(if b < a { s::shrinks(&m, &change, &old, &new, decided) } else { s::grows(&m, &change, &old, &new) });
        }
        for (m, t) in &self.reformat {
            let was = p.reformat.iter().find(|r| r.mount == *m).map(|r| fs_name(&r.kind)).unwrap_or_default();
            v.push(s::made_as(Clean::line(m, 64).as_str(), &fs_name(t), &was));
        }
        if !self.keys.is_empty() && self.status.as_ref().is_none_or(|st| !st.work_found()) {
            v.push(s::CHANGE_PASSPHRASE.to_string());
        }
        if !p.reformat_possible {
            v.push(s::NO_OTHER_FS.to_string());
        }
        v
    }

    /// A problem the plan still has: its help screen, with the fix its
    /// code has (holt-json.md, "Plan problems").
    fn review_problem(&mut self, ui: &mut dyn Ui, x: &plan::Problem) -> Go {
        let fixes = match x.code.as_str() {
            c if plan::DISK_CODES.contains(&c) || c == "plan_changed" => vec![(s::BTN_TO_DISKS.to_string(), Go::Back)],
            c if plan::KEY_CODES.contains(&c) => vec![(s::BTN_TO_DISKS.to_string(), Go::Back)],
            _ => Vec::new(),
        };
        let p = super::Problem {
            code: "plan_problem",
            screen: s::SCR_REVIEW,
            title: s::REVIEW_PROBLEM_TITLE.to_string(),
            what: s::review_problem_what(Clean::line(&x.text, cap::PROBLEM).as_str()),
            why: if plan::ERASE_CODES.contains(&x.code.as_str()) { s::ERASE_PROBLEM_WHY.to_string() } else { s::REVIEW_PROBLEM_WHY.to_string() },
            todo: s::REVIEW_PROBLEM_TODO.to_string(),
            fixes,
            again: true,
            back: None,
        };
        self.problem(ui, p)
    }

    /// Commands: the plan's text, as text mode prints it (J18).
    fn commands(&mut self, ui: &mut dyn Ui, p: &Plan) -> Option<Go> {
        let lines: Vec<Clean> = p.text.lines().take(COMMANDS_MAX).map(|l| Clean::line(l, cap::LOG_LINE)).collect();
        let v = TextView::new(Chrome::ours(s::SCR_REVIEW, s::COMMANDS_TITLE), lines);
        match ui.view(&v) {
            Answer::Ok(()) | Answer::Back => None,
            a => Some(Go::of(a)),
        }
    }

    /// Advanced: another filesystem.  Ok(true) when the choice changed
    /// (Review plans again, its serials cleared).
    fn another_fs(&mut self, ui: &mut dyn Ui, p: &Plan) -> Result<bool, Go> {
        let mut rows = Vec::new();
        let mut types: Vec<Vec<String>> = Vec::new();
        for r in &p.reformat {
            let mut ts = vec![r.kind.clone()];
            let mut note = Vec::new();
            for (t, why) in &r.to {
                match why {
                    None => ts.push(t.clone()),
                    Some(w) => note.push(Clean::line(w, cap::LINE).as_str().to_string()),
                }
            }
            let start = self.reformat.iter().find(|(m, _)| *m == r.mount).and_then(|(_, t)| ts.iter().position(|x| x == t)).unwrap_or(0);
            let mount = Clean::line(&r.mount, 64).as_str().to_string();
            rows.push(CycleRow {
                label: o(&format!("{mount:<11}{:<8}->  ", fs_name(&r.kind))),
                choices: ts.iter().map(|t| o(&fs_name(t))).collect(),
                start,
                note: Clean::text(&note.join("\n"), cap::PROBLEM),
            });
            types.push(ts);
        }
        let mut d = Cycle {
            chrome: Chrome::ours(s::SCR_REVIEW, s::ADVANCED_FS_TITLE),
            text: o(s::ADVANCED_FS_TEXT),
            rows,
            after: o(s::ADVANCED_FS_AFTER),
            as_was: o(s::AS_IT_WAS),
            changed: o(s::CHANGED),
            cant: o(s::CANT_CHANGE),
            buttons: vec![Button::ok(), Button::other(s::BTN_UNDO_ALL, None, B_UNDO), Button::back()],
        };
        loop {
            match ui.cycle(&d) {
                Answer::Ok(choice) => {
                    let mut new = Vec::new();
                    for (i, c) in choice.iter().enumerate() {
                        if let (Some(r), Some(t)) = (p.reformat.get(i), types.get(i).and_then(|ts| ts.get(*c))) {
                            if *c > 0 {
                                new.push((r.mount.clone(), t.clone()));
                            }
                        }
                    }
                    let changed = new != self.reformat;
                    if changed {
                        self.reformat = new;
                        self.serials.clear();
                    }
                    return Ok(changed);
                }
                Answer::Button(B_UNDO) => {
                    for r in &mut d.rows {
                        r.start = 0;
                    }
                }
                Answer::Back => return Ok(false),
                a => return Err(Go::of(a)),
            }
        }
    }

    // ---- 9. Restore: RT12's second part ----

    pub(crate) fn restore_next(&mut self, ui: &mut dyn Ui) -> Go {
        let host = self.host_name();
        let (when, _, _) = self.snap_when();
        let mut m = Message::new(Chrome::ours(s::SCR_RESTORE, s::NEXT_TITLE), o(&s::next(&host, &when, self.opts.dry_run)));
        m.buttons = vec![Button::back(), Button::shell(), Button::other(s::BTN_LEAVE, None, B_LEAVE)];
        match ui.message(&m) {
            Answer::Button(B_LEAVE) => Go::Exit(0),
            Answer::Ok(()) => Go::Back,
            Answer::Back => {
                self.reviewed = None;
                Go::Back
            }
            a => Go::of(a),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_serial_is_confirmed_by_its_last_six() {
        let mut c = plan::Disk { node: "sdb".into(), size_bytes: 2_000_398_934_016, model: Some("WDC WD20EFRX-68EUZN0".into()), serial: Some("WD-WCC4M5XK7HPL".into()), ..plan::Disk::default() };
        assert_eq!(confirm_by(&c), ("XK7HPL".to_string(), true));
        assert!(ui::matches("xk7hpl", "XK7HPL") && ui::matches(" XK7 HPL ", "XK7HPL") && !ui::matches("XK7HP", "XK7HPL"));
        c.serial_shared = true;
        assert_eq!(confirm_by(&c), ("2.00 TB WDC WD20EFRX-68EUZN0".to_string(), false), "a shared serial tells nothing");
        c.serial = None;
        c.model = None;
        assert_eq!(confirm_by(&c).0, "2.00 TB");
        assert!(!ui::matches("", ""), "nothing never confirms");
        assert_eq!(spelled("2318E6"), "2 3 1 8 E 6");
    }
}
