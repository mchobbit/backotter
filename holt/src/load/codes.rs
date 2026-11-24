//! Stable codes for plan problems and notes (docs/design/holt-json.md,
//! "Codes"; RT10b).
//!
//! A `Problem` or a `Note` is today's text with a code and the fields a
//! screen acts on beside it.  Its `Display` (and `Deref` to `str`) is the
//! text alone, so every text plan, status and error line is byte for byte
//! what it was.  A plain `String` becomes one coded `other` (`From`), and
//! `Problems`/`Notes` take anything that converts, so a site not yet coded
//! compiles and prints as before (J4); its code is `other`, which blocks
//! like any problem.
//!
//! `bko-holt-error`'s codes are jsonout.rs's; the candidates' refusals
//! (RT10c) and the run's failures (RT10f) come with their steps.

use crate::json::{Obj, Value};
use std::fmt;

/// A code family: problems or notes.
pub trait Code: Copy + PartialEq + fmt::Debug + 'static {
    /// What an uncoded site gets.
    const OTHER: Self;
    const ALL: &'static [Self];
    fn as_str(self) -> &'static str;
    fn parse(s: &str) -> Option<Self> {
        Self::ALL.iter().copied().find(|c| c.as_str() == s)
    }
}

macro_rules! codes {
    ($(#[$m:meta])* $name:ident { $($variant:ident => $text:literal,)* }) => {
        $(#[$m])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum $name {
            $($variant,)*
        }

        impl $name {
            /// The code as the documents write it.
            pub fn as_str(self) -> &'static str {
                <Self as Code>::as_str(self)
            }
        }

        impl Code for $name {
            const OTHER: Self = $name::Other;
            const ALL: &'static [$name] = &[$($name::$variant,)*];
            fn as_str(self) -> &'static str {
                match self {
                    $($name::$variant => $text,)*
                }
            }
        }
    };
}

codes! {
    /// Plan problems (exit 4): holt-json.md's "Plan problems" table.
    ProblemCode {
        MapUnknownSource => "map_unknown_source",
        NoSuchDisk => "no_such_disk",
        NotMapped => "not_mapped",
        TargetTwice => "target_twice",
        BootDisk => "boot_disk",
        RunningRoot => "running_root",
        TargetTooSmall => "target_too_small",
        SectorSize => "sector_size",
        ReadOnly => "read_only",
        InUse => "in_use",
        Held => "held",
        NotBlank => "not_blank",
        HeldUnreleasable => "held_unreleasable",
        OursBusy => "ours_busy",
        FittedOtherPct => "fitted_other_pct",
        EraseNotTarget => "erase_not_target",
        DuplicateVg => "duplicate_vg",
        PoolLeftOut => "pool_left_out",
        WedgeClash => "wedge_clash",
        LayoutInconsistent => "layout_inconsistent",
        FitRefused => "fit_refused",
        FitOverPct => "fit_over_pct",
        DataWontFit => "data_wont_fit",
        DataUnknown => "data_unknown",
        KeyNeeded => "key_needed",
        KeptHeaderKeyNeeded => "kept_header_key_needed",
        KeyFileUnrestorable => "key_file_unrestorable",
        KeyNameUnusable => "key_name_unusable",
        KeyFileUnsafe => "key_file_unsafe",
        PasswordNeeded => "password_needed",
        RepoHasPassword => "repo_has_password",
        EraseRequired => "erase_required",
        RemakeAfterRestore => "remake_after_restore",
        PlanChanged => "plan_changed",
        ArchiverMissing => "archiver_missing",
        ArchiverNotBko => "archiver_not_bko",
        ToolMissing => "tool_missing",
        LvmTooOld => "lvm_too_old",
        ThinUnsupported => "thin_unsupported",
        ZfsFeatureUnknown => "zfs_feature_unknown",
        ZfsCompatMissing => "zfs_compat_missing",
        ReformatRefused => "reformat_refused",
        FromBugReport => "from_bug_report",
        NotRoot => "not_root",
        Unsupported => "unsupported",
        Other => "other",
    }
}

codes! {
    /// Plan notes: holt-json.md's "Notes".
    NoteCode {
        TargetBigger => "target_bigger",
        FitChange => "fit_change",
        FitGrowElsewhere => "fit_grow_elsewhere",
        PoolRenamed => "pool_renamed",
        PartitionRuns => "partition_runs",
        Release => "release",
        SourceEmpty => "source_empty",
        LuksPbkdf => "luks_pbkdf",
        Reformat => "reformat",
        Token => "token",
        StaysAttached => "stays_attached",
        Other => "other",
    }
}

/// fit_refused's `reason` (holt-json.md's table): why fit can't change
/// this layout for that disk.  `other` and any value a reader doesn't know
/// show the text.
pub const FIT_REASONS: &[&str] = &[
    "other_family",
    "no_table",
    "no_partition",
    "tied",
    "logical",
    "moves_bytes",
    "fat_start",
    "mbr_reach",
    "table_too_small",
    "inodes",
    "xfs_ag",
    "kept_luks_header",
    "swap",
    "member",
    "multi_device",
    "fs_unsupported",
    "no_usage",
    "other",
];

/// reformat_refused's `reason` (holt-json.md's table): `other` where the
/// mount isn't one --reformat can find (no fstab entry, not on the
/// layout's disks, no filesystem), which bko-rescue never offers; a reader
/// shows the text.
pub const REFORMAT_REASONS: &[&str] = &[
    "not_linux",
    "own_tools",
    "grub_reads",
    "not_convertible",
    "same_type",
    "multi_device",
    "mounted_elsewhere",
    "no_fsck",
    "old_kernel",
    "hibernation",
    "no_uuid",
    "twice",
    "edited",
    "other",
];

/// A problem or a note: its code, today's text, and the fields about its
/// subject (`disk`, `target`, `name`, ...), in the order they were given.
#[derive(Clone, PartialEq)]
pub struct Coded<C: Code> {
    pub code: C,
    pub text: String,
    pub fields: Vec<(String, Value)>,
}

pub type Problem = Coded<ProblemCode>;
pub type Note = Coded<NoteCode>;

impl<C: Code> Coded<C> {
    pub fn new(code: C, text: impl Into<String>) -> Self {
        Coded { code, text: text.into(), fields: Vec::new() }
    }

    /// Adds the subject field KEY (replacing one given before).
    pub fn with(mut self, key: &str, v: impl Into<Value>) -> Self {
        let v = v.into();
        match self.fields.iter_mut().find(|(k, _)| k == key) {
            Some(slot) => slot.1 = v,
            None => self.fields.push((key.to_string(), v)),
        }
        self
    }

    pub fn field(&self, key: &str) -> Option<&Value> {
        self.fields.iter().find(|(k, _)| *k == key).map(|(_, v)| v)
    }

    pub fn str_field(&self, key: &str) -> Option<&str> {
        self.field(key).and_then(Value::as_str)
    }

    /// `{"code", "text", fields...}`, as the documents carry it.
    pub fn to_json(&self) -> Value {
        let mut o = Obj::new().set("code", self.code.as_str()).set("text", self.text.as_str());
        for (k, v) in &self.fields {
            o.put(k, v.clone());
        }
        o.build()
    }

    /// to_json's object read back (a plain string is an `other`, as
    /// before codes); a code this bko-holt doesn't know is `other` too.
    pub fn from_json(v: &Value) -> Option<Self> {
        if let Some(t) = v.as_str() {
            return Some(t.into());
        }
        let kv = v.as_object()?;
        let text = v.get("text")?.as_str()?;
        let code = v.get("code").and_then(Value::as_str).and_then(C::parse).unwrap_or(C::OTHER);
        let mut x = Coded::new(code, text);
        for (k, f) in kv.iter().filter(|(k, _)| k != "code" && k != "text") {
            x = x.with(k, f.clone());
        }
        Some(x)
    }
}

impl<C: Code> fmt::Display for Coded<C> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.text)
    }
}

/// As the text's: what a test's `{:?}` of a list of problems has always
/// shown.
impl<C: Code> fmt::Debug for Coded<C> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(&self.text, f)
    }
}

impl<C: Code> std::ops::Deref for Coded<C> {
    type Target = str;
    fn deref(&self) -> &str {
        &self.text
    }
}

impl<C: Code> std::borrow::Borrow<str> for Coded<C> {
    fn borrow(&self) -> &str {
        &self.text
    }
}

impl<C: Code> From<String> for Coded<C> {
    fn from(text: String) -> Self {
        Coded::new(C::OTHER, text)
    }
}

impl<C: Code> From<&str> for Coded<C> {
    fn from(text: &str) -> Self {
        Coded::new(C::OTHER, text)
    }
}

impl<C: Code> From<&String> for Coded<C> {
    fn from(text: &String) -> Self {
        Coded::new(C::OTHER, text.as_str())
    }
}

impl<C: Code> PartialEq<str> for Coded<C> {
    fn eq(&self, o: &str) -> bool {
        self.text == o
    }
}

impl<C: Code> PartialEq<&str> for Coded<C> {
    fn eq(&self, o: &&str) -> bool {
        self.text == *o
    }
}

impl<C: Code> PartialEq<String> for Coded<C> {
    fn eq(&self, o: &String) -> bool {
        self.text == *o
    }
}

/// A list of problems or notes: a Vec that takes a String as readily as a
/// coded one, so a site not yet coded pushes as it always has.
#[derive(Clone, PartialEq)]
pub struct List<C: Code>(pub Vec<Coded<C>>);

pub type Problems = List<ProblemCode>;
pub type Notes = List<NoteCode>;

impl<C: Code> Default for List<C> {
    fn default() -> Self {
        List(Vec::new())
    }
}

impl<C: Code> List<C> {
    pub fn new() -> Self {
        List(Vec::new())
    }

    pub fn push(&mut self, x: impl Into<Coded<C>>) {
        self.0.push(x.into());
    }

    pub fn extend<T: Into<Coded<C>>>(&mut self, it: impl IntoIterator<Item = T>) {
        self.0.extend(it.into_iter().map(Into::into));
    }

    /// Whether one of them has the text TEXT.
    pub fn has_text(&self, text: &str) -> bool {
        self.0.iter().any(|x| x.text == text)
    }

    /// has_text, for a list's callers that had a Vec<String>
    /// (`problems.contains(&text)`).
    pub fn contains(&self, text: &impl AsRef<str>) -> bool {
        self.has_text(text.as_ref())
    }

    pub fn retain(&mut self, f: impl FnMut(&Coded<C>) -> bool) {
        self.0.retain(f);
    }

    /// Their texts, as the lists were before codes.
    pub fn texts(&self) -> Vec<String> {
        self.0.iter().map(|x| x.text.clone()).collect()
    }

    pub fn to_json(&self) -> Value {
        Value::Arr(self.0.iter().map(Coded::to_json).collect())
    }
}

impl<C: Code> fmt::Debug for List<C> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_list().entries(self.0.iter()).finish()
    }
}

impl<C: Code> std::ops::Deref for List<C> {
    type Target = [Coded<C>];
    fn deref(&self) -> &[Coded<C>] {
        &self.0
    }
}

impl<C: Code> IntoIterator for List<C> {
    type Item = Coded<C>;
    type IntoIter = std::vec::IntoIter<Coded<C>>;
    fn into_iter(self) -> Self::IntoIter {
        self.0.into_iter()
    }
}

impl<'a, C: Code> IntoIterator for &'a List<C> {
    type Item = &'a Coded<C>;
    type IntoIter = std::slice::Iter<'a, Coded<C>>;
    fn into_iter(self) -> Self::IntoIter {
        self.0.iter()
    }
}

impl<C: Code, T: Into<Coded<C>>> FromIterator<T> for List<C> {
    fn from_iter<I: IntoIterator<Item = T>>(it: I) -> Self {
        List(it.into_iter().map(Into::into).collect())
    }
}

impl<C: Code, T: Into<Coded<C>>> From<Vec<T>> for List<C> {
    fn from(v: Vec<T>) -> Self {
        v.into_iter().collect()
    }
}

impl<C: Code, S: AsRef<str>> PartialEq<Vec<S>> for List<C> {
    fn eq(&self, o: &Vec<S>) -> bool {
        self.0.len() == o.len() && self.0.iter().zip(o).all(|(a, b)| a.text == b.as_ref())
    }
}

impl<C: Code, S: AsRef<str>, const N: usize> PartialEq<[S; N]> for List<C> {
    fn eq(&self, o: &[S; N]) -> bool {
        self.0.len() == N && self.0.iter().zip(o).all(|(a, b)| a.text == b.as_ref())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every code in holt-json.md's tables, in their order, and nothing
    /// else, so the spec and this file can't drift apart.  Read from the
    /// repository's docs/ when the tree has it (a copy of holt/ alone
    /// skips the check).
    #[test]
    fn the_spec_lists_every_code() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../docs/design/holt-json.md");
        let Ok(spec) = std::fs::read_to_string(path) else {
            eprintln!("{path} isn't here: the codes aren't checked against the spec");
            return;
        };
        let section = |from: &str, to: &str| -> String {
            let a = spec.find(from).unwrap_or_else(|| panic!("no {from:?} in holt-json.md"));
            let b = a + spec[a..].find(to).unwrap_or_else(|| panic!("no {to:?} after {from:?}"));
            spec[a..b].to_string()
        };
        // The problems table: the first column of each row.
        let rows = section("### Plan problems (exit 4)", "### Notes");
        let table: Vec<&str> = rows.lines().filter_map(|l| l.strip_prefix("| `")).filter_map(|l| l.split_once('`')).map(|(c, _)| c).collect();
        let ours: Vec<&str> = ProblemCode::ALL.iter().map(|c| c.as_str()).collect();
        assert_eq!(table, ours);
        // The notes: each of ours is a `code` in the paragraph.
        let notes = section("### Notes", "### Failures");
        for c in NoteCode::ALL {
            assert!(notes.contains(&format!("`{}`", c.as_str())), "{c:?} isn't in holt-json.md's notes");
        }
        // The reasons, as fit_refused's and reformat_refused's rows give them.
        let row = |code: &str| rows.lines().find(|l| l.starts_with(&format!("| `{code}`"))).unwrap_or_else(|| panic!("no {code} row")).to_string();
        let (fit_row, ref_row) = (row("fit_refused"), row("reformat_refused"));
        for r in FIT_REASONS {
            assert!(fit_row.contains(&format!("`{r}`")), "{r}");
        }
        for r in REFORMAT_REASONS {
            assert!(ref_row.contains(&format!("`{r}`")), "{r}");
        }
    }

    /// Every reason fit.rs and reformat.rs give by name is in their list
    /// (and so in the spec's row, above): a new one can't drift past both.
    #[test]
    fn every_reason_given_is_listed() {
        let given = |src: &str, call: &str| -> Vec<String> {
            src.match_indices(call)
                .filter(|(i, _)| !src[..*i].ends_with(|c: char| c.is_alphanumeric() || c == '_' || c == '.'))
                .filter_map(|(i, _)| src[i + call.len()..].split_once('"').map(|(r, _)| r.to_string()))
                .collect()
        };
        let fit = given(include_str!("fit.rs"), "why(\"");
        let reformat = given(include_str!("reformat.rs"), "refused(\"");
        assert!(fit.len() > 20 && reformat.len() > 10, "{fit:?} {reformat:?}");
        for r in &fit {
            assert!(FIT_REASONS.contains(&r.as_str()), "fit.rs gives {r:?}, not in FIT_REASONS");
        }
        for r in &reformat {
            assert!(REFORMAT_REASONS.contains(&r.as_str()), "reformat.rs gives {r:?}, not in REFORMAT_REASONS");
        }
    }

    #[test]
    fn text_is_the_display() {
        let p = Problem::new(ProblemCode::NotBlank, "vda -> /dev/vdb: the target isn't blank (ext4); give --erase /dev/vdb to wipe it")
            .with("disk", "vda")
            .with("target", "vdb");
        assert_eq!(p.to_string(), p.text);
        assert_eq!(format!("{p:?}"), format!("{:?}", p.text));
        assert!(p.contains("isn't blank") && p.starts_with("vda ->"));
        assert_eq!(p.str_field("disk"), Some("vda"));
        assert_eq!(
            p.to_json().compact(),
            r#"{"code":"not_blank","text":"vda -> /dev/vdb: the target isn't blank (ext4); give --erase /dev/vdb to wipe it","disk":"vda","target":"vdb"}"#
        );
        let o: Problem = format!("x {}", 1).into();
        assert_eq!((o.code, o.text.as_str()), (ProblemCode::Other, "x 1"));
        let n: Note = "a note".into();
        assert_eq!(n.code, NoteCode::Other);
        // Through JSON and back, as --reformat's findings travel.
        assert_eq!(Problem::from_json(&p.to_json()), Some(p.clone()));
        assert_eq!(Problem::from_json(&Value::from("plain")).map(|x| (x.code, x.text)), Some((ProblemCode::Other, "plain".to_string())));
        let unknown = crate::json::parse(r#"{"code": "from_the_future", "text": "t", "disk": "vda"}"#).unwrap();
        let u = Problem::from_json(&unknown).unwrap();
        assert_eq!((u.code, u.str_field("disk")), (ProblemCode::Other, Some("vda")));
        assert_eq!(ProblemCode::parse("not_blank"), Some(ProblemCode::NotBlank));
        assert_eq!(NoteCode::parse("nope"), None);
    }

    #[test]
    fn lists_take_strings_and_coded_alike() {
        let mut l = Problems::new();
        l.push("one".to_string());
        l.push(Problem::new(ProblemCode::NotRoot, "two"));
        l.extend(vec!["three".to_string()]);
        l.extend(Problems::from(vec!["four"]).iter().cloned());
        assert_eq!(l, vec!["one", "two", "three", "four"]);
        assert_eq!(l.len(), 4);
        assert_eq!(l.join("; "), "one; two; three; four");
        assert!(l.has_text("two") && !l.has_text("tw"));
        assert_eq!(l.iter().map(|p| p.code.as_str()).collect::<Vec<_>>(), ["other", "not_root", "other", "other"]);
        assert_eq!(format!("{l:?}"), r#"["one", "two", "three", "four"]"#);
        l.retain(|p| p.code == ProblemCode::Other);
        assert_eq!(l.texts(), vec!["one", "three", "four"]);
    }
}
