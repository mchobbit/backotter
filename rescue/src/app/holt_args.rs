//! Every bko-holt command line bko-rescue runs is built here, and only
//! here (rescue-tui.md, "Dry run", lock 1, as J6 amended it): in dry-run
//! mode this can't add `--commit`, whatever the caller asks.  It may add
//! `--erase` (J6: without `--commit` nothing is written, and J19 makes a
//! used disk need it even in a dry run).  The second lock is bko-holt's
//! own: `env` gives `BKO_HOLT_NO_COMMIT=1` in dry-run mode, and bko-holt
//! then refuses `--commit` itself (exit 2).
//!
//! RT11 runs `--version --json`, `stick --json`, `recovery-words OUT
//! --json` and `report-bug`; RT12 `stick --copy INSTALL_ID OUT --json`
//! (the layout, holt-json.md's open question 8), `luks-headers LAYOUT DIR
//! --repo URL --password-file FILE --snapshot ID --json` (the LUKS2
//! headers a dump kept, from the backup: open question 12), `snapshot-layout
//! OUT --repo URL --password-file FILE --snapshot ID [--install-id ID]
//! --json` (the layout the chosen snapshot's own system.json carries: open
//! question 15), the plans and status of Disks,
//! Passphrases, Resume and Review, and builds Restore's run from Review's
//! (rescue-screens.md, "The full argument list": the plan reviewed is the
//! run made, with `--through close --commit --expect-plan` in its stage's
//! place).

use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// The variable bko-holt refuses `--commit` under (holt-json.md, "The
/// dry run, end to end").
pub const NO_COMMIT: &str = "BKO_HOLT_NO_COMMIT";

/// Which part of load to run.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Stage {
    /// The plan: `--through plan`.
    Plan,
    /// `--only status`.
    Status,
    Through(String),
    Only(String),
}

/// A load run's arguments.  `commit` is what the caller asks for; in a
/// dry run it is never passed.
#[derive(Clone, Debug)]
pub struct Load {
    pub layout: PathBuf,
    pub stage: Stage,
    pub repo: Option<String>,
    pub password_file: Option<PathBuf>,
    pub key_files: Vec<(String, PathBuf)>,
    pub map: Vec<(String, String)>,
    pub erase: Vec<String>,
    pub snapshot: Option<String>,
    /// `--luks-header-dir DIR`: the LUKS2 headers the dump kept, fetched
    /// from the backup into the session's directory (layout.rs).
    pub luks_header_dir: Option<PathBuf>,
    pub expect_plan: Option<String>,
    /// `--fit-largest PCT`: a shrink or grow the user agreed to on Disks.
    pub fit_largest: Option<u64>,
    /// `--reformat MOUNT=TYPE`, from Advanced.
    pub reformat: Vec<(String, String)>,
    /// `--removable --no-nvram` (Failure's "install without an entry").
    pub removable: bool,
    pub commit: bool,
}

impl Load {
    pub fn new(layout: &Path, stage: Stage) -> Load {
        Load {
            layout: layout.to_path_buf(),
            stage,
            repo: None,
            password_file: None,
            key_files: Vec::new(),
            map: Vec::new(),
            erase: Vec::new(),
            snapshot: None,
            luks_header_dir: None,
            expect_plan: None,
            fit_largest: None,
            reformat: Vec::new(),
            removable: false,
            commit: false,
        }
    }

    /// The run Review's plan stands for: the same arguments, its stage
    /// `--through close` (or STAGE alone, for a resume), `--commit`, and
    /// the reviewed plan's hash, so bko-holt refuses it if anything moved
    /// since (J20).  In a dry run `argv` still never passes `--commit`.
    pub fn run_of(&self, stage: Stage, plan_sha256: &str) -> Load {
        let mut l = self.clone();
        l.stage = stage;
        l.expect_plan = Some(plan_sha256.to_string());
        l.commit = true;
        l
    }
}

pub enum HoltCmd<'a> {
    Version,
    Stick,
    /// The machine's layout, from its system.json on the stick, to `out`.
    StickCopy {
        install_id: &'a str,
        out: &'a Path,
    },
    /// The LUKS2 headers the layout kept, from the backup's snapshot, to
    /// the new directory `dir`.
    LuksHeaders {
        layout: &'a Path,
        dir: &'a Path,
        repo: &'a str,
        password_file: &'a Path,
        snapshot: Option<&'a str>,
    },
    /// The layout the snapshot's own system.json carries, to `out`, of
    /// the machine `install_id` when one was chosen on the stick.
    SnapshotLayout {
        out: &'a Path,
        repo: &'a str,
        password_file: &'a Path,
        snapshot: &'a str,
        install_id: Option<&'a str>,
    },
    /// The words go on standard input; the password to `out`.
    RecoveryWords {
        out: &'a Path,
    },
    ReportBug {
        out: &'a Path,
        no_redact: bool,
        strip: Option<&'a Path>,
        attach: &'a [PathBuf],
    },
    Load(&'a Load),
}

fn os(s: &str) -> OsString {
    OsString::from(s)
}

/// The arguments after bko-holt's path.
pub fn argv(cmd: &HoltCmd, dry_run: bool) -> Vec<OsString> {
    match cmd {
        HoltCmd::Version => vec![os("--version"), os("--json")],
        HoltCmd::Stick => vec![os("stick"), os("--json")],
        HoltCmd::StickCopy { install_id, out } => vec![os("stick"), os("--copy"), os(install_id), out.as_os_str().to_owned(), os("--json")],
        HoltCmd::LuksHeaders { layout, dir, repo, password_file, snapshot } => {
            let mut v = vec![os("luks-headers"), layout.as_os_str().to_owned(), dir.as_os_str().to_owned(), os("--repo"), os(repo), os("--password-file"), password_file.as_os_str().to_owned()];
            if let Some(s) = snapshot {
                v.extend([os("--snapshot"), os(s)]);
            }
            v.push(os("--json"));
            v
        }
        HoltCmd::SnapshotLayout { out, repo, password_file, snapshot, install_id } => {
            let mut v = vec![os("snapshot-layout"), out.as_os_str().to_owned(), os("--repo"), os(repo), os("--password-file"), password_file.as_os_str().to_owned(), os("--snapshot"), os(snapshot)];
            if let Some(i) = install_id {
                v.extend([os("--install-id"), os(i)]);
            }
            v.push(os("--json"));
            v
        }
        HoltCmd::RecoveryWords { out } => vec![os("recovery-words"), out.as_os_str().to_owned(), os("--json")],
        HoltCmd::ReportBug { out, no_redact, strip, attach } => {
            let mut v = vec![os("report-bug"), os("--output"), out.as_os_str().to_owned()];
            if *no_redact {
                v.push(os("--no-redact"));
            } else if let Some(s) = strip {
                v.push(os("--strip-file"));
                v.push(s.as_os_str().to_owned());
            }
            for a in attach.iter() {
                v.push(os("--attach"));
                v.push(a.as_os_str().to_owned());
            }
            v
        }
        HoltCmd::Load(l) => {
            let mut v = vec![os("load"), l.layout.as_os_str().to_owned(), os("--json")];
            match &l.stage {
                Stage::Plan => v.extend([os("--through"), os("plan")]),
                Stage::Status => v.extend([os("--only"), os("status")]),
                Stage::Through(s) => v.extend([os("--through"), os(s)]),
                Stage::Only(s) => v.extend([os("--only"), os(s)]),
            }
            if let Some(r) = &l.repo {
                v.extend([os("--repo"), os(r)]);
            }
            if let Some(p) = &l.password_file {
                v.extend([os("--password-file"), p.as_os_str().to_owned()]);
            }
            for (name, p) in &l.key_files {
                let mut kf = OsString::from(format!("{name}="));
                kf.push(p);
                v.extend([os("--key-file"), kf]);
            }
            for (from, to) in &l.map {
                v.extend([os("--map"), os(&format!("{from}={to}"))]);
            }
            for e in &l.erase {
                v.extend([os("--erase"), os(e)]);
            }
            if let Some(s) = &l.snapshot {
                v.extend([os("--snapshot"), os(s)]);
            }
            if let Some(d) = &l.luks_header_dir {
                v.extend([os("--luks-header-dir"), d.as_os_str().to_owned()]);
            }
            if let Some(h) = &l.expect_plan {
                v.extend([os("--expect-plan"), os(h)]);
            }
            if let Some(p) = l.fit_largest {
                v.extend([os("--fit-largest"), os(&p.to_string())]);
            }
            for (m, t) in &l.reformat {
                v.extend([os("--reformat"), os(&format!("{m}={t}"))]);
            }
            if l.removable {
                v.extend([os("--removable"), os("--no-nvram")]);
            }
            // Lock 1: never in a dry run, whatever was asked.
            if l.commit && !dry_run {
                v.push(os("--commit"));
            }
            v
        }
    }
}

/// What bko-holt's small environment adds: lock 2 in a dry run.
pub fn env(dry_run: bool) -> Vec<(String, String)> {
    if dry_run {
        vec![(NO_COMMIT.to_string(), "1".to_string())]
    } else {
        Vec::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn every(load: &Load) -> Vec<HoltCmd<'_>> {
        let p = Path::new("/run/bko-rescue/s-1-0/x");
        vec![
            HoltCmd::Version,
            HoltCmd::Stick,
            HoltCmd::StickCopy { install_id: "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90", out: p },
            HoltCmd::LuksHeaders { layout: p, dir: p, repo: "sftp://nas.local/backotter/lovelace", password_file: p, snapshot: Some("1a2b3c4d") },
            HoltCmd::SnapshotLayout { out: p, repo: "sftp://nas.local/backotter/lovelace", password_file: p, snapshot: "1a2b3c4d", install_id: Some("0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90") },
            HoltCmd::RecoveryWords { out: p },
            HoltCmd::ReportBug { out: p, no_redact: false, strip: Some(p), attach: &[] },
            HoltCmd::ReportBug { out: p, no_redact: true, strip: None, attach: &[] },
            HoltCmd::Load(load),
        ]
    }

    fn loads() -> Vec<Load> {
        let mut v = Vec::new();
        // Every stage a run is made with: Start again's close and
        // partition too.
        for stage in [Stage::Plan, Stage::Status, Stage::Through("close".into()), Stage::Only("boot".into()), Stage::Only("partition".into()), Stage::Only("close".into())] {
            let mut l = Load::new(Path::new("/run/bko-rescue/layout.json"), stage);
            l.repo = Some("sftp://nas.local/backotter/lovelace".into());
            l.password_file = Some("/run/bko-rescue/s-1-0/password".into());
            l.key_files.push(("cryptroot".into(), "/run/bko-rescue/s-1-0/k0".into()));
            l.map.push(("sda".into(), "nvme0n1".into()));
            l.erase.push("nvme0n1".into());
            l.snapshot = Some("1a2b3c4d".into());
            l.luks_header_dir = Some("/run/bko-rescue/s-1-0/h".into());
            l.expect_plan = Some("ab".repeat(32));
            l.fit_largest = Some(5);
            l.reformat.push(("/srv".into(), "xfs".into()));
            l.removable = true;
            // Every screen asks for --commit: the dry run still never
            // passes it.
            l.commit = true;
            v.push(l);
        }
        v
    }

    #[test]
    fn a_dry_run_never_adds_commit() {
        for l in loads() {
            for c in every(&l) {
                let a = argv(&c, true);
                assert!(!a.iter().any(|x| x.to_string_lossy().contains("--commit")), "{a:?}");
            }
            // It may add --erase (J6).
            assert!(argv(&HoltCmd::Load(&l), true).contains(&os("--erase")));
        }
        assert_eq!(env(true), vec![(NO_COMMIT.to_string(), "1".to_string())]);
    }

    #[test]
    fn restores_run_is_reviews_with_its_stage_commit_and_hash() {
        let mut review = Load::new(Path::new("/run/bko-rescue/s-1-0/layout.json"), Stage::Plan);
        review.map.push(("sda".into(), "/dev/disk/by-id/nvme-X".into()));
        review.erase.push("/dev/disk/by-id/nvme-X".into());
        review.fit_largest = Some(39);
        let words = |l: &Load, dry: bool| argv(&HoltCmd::Load(l), dry).iter().map(|x| x.to_string_lossy().to_string()).collect::<Vec<_>>();
        let r = words(&review, false);
        assert_eq!(&r[3..5], &["--through".to_string(), "plan".to_string()]);
        let run = review.run_of(Stage::Through("close".into()), &"cd".repeat(32));
        let w = words(&run, false);
        // The same list, its stage swapped, the hash and --commit added.
        let mut want = r.clone();
        want[4] = "close".into();
        let at = want.iter().position(|x| x == "--fit-largest").unwrap();
        want.splice(at..at, ["--expect-plan".to_string(), "cd".repeat(32)]);
        want.push("--commit".into());
        assert_eq!(w, want);
        // A dry run: the same, without --commit.
        let d = words(&run, true);
        assert_eq!(d, want[..want.len() - 1].to_vec());
    }

    #[test]
    fn a_restore_passes_commit_only_when_asked() {
        let mut l = loads().remove(2);
        let a = argv(&HoltCmd::Load(&l), false);
        assert_eq!(a.last(), Some(&os("--commit")));
        assert_eq!(a.iter().filter(|x| *x == "--commit").count(), 1);
        l.commit = false;
        assert!(!argv(&HoltCmd::Load(&l), false).contains(&os("--commit")));
        assert!(env(false).is_empty());
        let words: Vec<String> = argv(&HoltCmd::Load(&l), false).iter().map(|x| x.to_string_lossy().to_string()).collect();
        assert_eq!(
            words.join(" "),
            format!(
                "load /run/bko-rescue/layout.json --json --through close --repo sftp://nas.local/backotter/lovelace \
                 --password-file /run/bko-rescue/s-1-0/password --key-file cryptroot=/run/bko-rescue/s-1-0/k0 \
                 --map sda=nvme0n1 --erase nvme0n1 --snapshot 1a2b3c4d --luks-header-dir /run/bko-rescue/s-1-0/h \
                 --expect-plan {} --fit-largest 5 --reformat /srv=xfs --removable --no-nvram",
                "ab".repeat(32)
            )
        );
    }

    #[test]
    fn the_other_commands() {
        let p = Path::new("/r/o");
        let s = |c: HoltCmd| argv(&c, false).iter().map(|x| x.to_string_lossy().to_string()).collect::<Vec<_>>().join(" ");
        assert_eq!(s(HoltCmd::Version), "--version --json");
        assert_eq!(s(HoltCmd::Stick), "stick --json");
        assert_eq!(s(HoltCmd::StickCopy { install_id: "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90", out: p }), "stick --copy 0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90 /r/o --json");
        assert_eq!(s(HoltCmd::RecoveryWords { out: p }), "recovery-words /r/o --json");
        let (l, h, pw) = (Path::new("/r/layout.json"), Path::new("/r/h"), Path::new("/r/password"));
        assert_eq!(
            s(HoltCmd::LuksHeaders { layout: l, dir: h, repo: "sftp://nas/bko", password_file: pw, snapshot: Some("1a2b3c4d") }),
            "luks-headers /r/layout.json /r/h --repo sftp://nas/bko --password-file /r/password --snapshot 1a2b3c4d --json"
        );
        assert_eq!(s(HoltCmd::LuksHeaders { layout: l, dir: h, repo: "/srv/r", password_file: pw, snapshot: None }), "luks-headers /r/layout.json /r/h --repo /srv/r --password-file /r/password --json");
        assert_eq!(
            s(HoltCmd::SnapshotLayout { out: l, repo: "sftp://nas/bko", password_file: pw, snapshot: "1a2b3c4d", install_id: Some("0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90") }),
            "snapshot-layout /r/layout.json --repo sftp://nas/bko --password-file /r/password --snapshot 1a2b3c4d --install-id 0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90 --json"
        );
        assert_eq!(s(HoltCmd::SnapshotLayout { out: l, repo: "/srv/r", password_file: pw, snapshot: "1a2b3c4d", install_id: None }), "snapshot-layout /r/layout.json --repo /srv/r --password-file /r/password --snapshot 1a2b3c4d --json");
        assert_eq!(s(HoltCmd::ReportBug { out: p, no_redact: false, strip: Some(Path::new("/r/s")), attach: &[] }), "report-bug --output /r/o --strip-file /r/s");
        assert_eq!(s(HoltCmd::ReportBug { out: p, no_redact: true, strip: Some(Path::new("/r/s")), attach: &[] }), "report-bug --output /r/o --no-redact", "--strip-file means nothing there");
    }
}
