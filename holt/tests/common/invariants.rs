//! `golden_plans.rs`'s five invariants (its header says what each holds), and
//! how they read a plan: moved here from `golden_plans.rs`, so that
//! `BackShot2`'s tier 0 (`bs2_plan.rs`) holds a drifted layout's plan to the same
//! ones.  Like the rest of common/, it uses only holt API as it was before
//! 3671e96.

#![allow(dead_code)]

use super::Planned;
use bko_holt::bytes::ByteSource;
use bko_holt::json::Value;

/// Where load's mount stage builds the restored tree (`load::DEFAULT_TARGET`).
pub(crate) const TARGET: &str = "/mnt/bko-target";

/// What the invariants found: violations, and what they left out on purpose.
#[derive(Debug, Default, Clone, PartialEq)]
pub(crate) struct Check {
    pub violations: Vec<String>,
    pub skipped: Vec<String>,
    /// What each invariant looked at, for the log.
    pub checked: Vec<String>,
}

// ---- reading a plan ----

/// One command of a stage, with its here-document's lines.
#[derive(Debug, Clone)]
pub(crate) struct Step {
    pub cmd: String,
    pub body: Vec<String>,
}

impl Step {
    pub(crate) fn text(&self) -> String {
        std::iter::once(self.cmd.clone())
            .chain(self.body.iter().cloned())
            .collect::<Vec<_>>()
            .join("\n")
    }
}

/// The plan's sections by name (`super::sections`).
pub(crate) struct Sections<'a>(pub Vec<(String, Vec<&'a str>)>);

impl<'a> Sections<'a> {
    pub(crate) fn of(text: &'a str) -> Sections<'a> {
        Sections(super::sections(text))
    }

    pub(crate) fn lines(&self, name: &str) -> Vec<&'a str> {
        self.0
            .iter()
            .filter(|(n, _)| n == name)
            .flat_map(|(_, l)| l.iter().copied())
            .collect()
    }

    /// A stage's commands: its lines less the header, comments outside a
    /// here-document, and blank lines.
    pub(crate) fn steps(&self, stage: &str) -> Vec<Step> {
        let mut out: Vec<Step> = Vec::new();
        let mut in_doc: Option<String> = None;
        for l in self.lines(stage).into_iter().skip(1) {
            if let Some(end) = &in_doc {
                if l == end {
                    in_doc = None;
                } else if let Some(s) = out.last_mut() {
                    s.body.push(l.to_string());
                }
                continue;
            }
            if l.starts_with('#') || l.trim().is_empty() {
                continue;
            }
            if let Some(i) = l.rfind("<<") {
                let tag = l[i + 2..].trim().trim_matches(|c| c == '\'' || c == '"');
                if !tag.is_empty() && tag.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
                    in_doc = Some(tag.to_string());
                }
            }
            out.push(Step {
                cmd: l.to_string(),
                body: Vec::new(),
            });
        }
        out
    }

    /// The header's list under TITLE ("# Problems", "# Notes:"): its
    /// "#   - " lines.
    pub(crate) fn list(&self, title: &str) -> Vec<String> {
        let head = self.lines("header");
        let Some(i) = head.iter().position(|l| l.starts_with(title)) else {
            return vec![];
        };
        head[i + 1..]
            .iter()
            .take_while(|l| l.starts_with("#   - ") || l.starts_with("#     "))
            .filter_map(|l| l.strip_prefix("#   - "))
            .map(str::to_string)
            .collect()
    }
}

/// A command line's words, as sh reads them (lex), split at `;`, `(` and
/// `)` too: good enough to find paths and names in it.
pub(crate) fn words(s: &str) -> Vec<String> {
    lex(s, &[';', '(', ')'])
        .into_iter()
        .flatten()
        .map(|w| w.text)
        .filter(|w| !w.is_empty())
        .collect()
}

/// One shell word: its text with its quotes and backslashes undone, and
/// whether an unquoted `<` or `>` is in it (a redirection).
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Word {
    pub text: String,
    pub redirect: bool,
}

/// S's words as sh splits and unquotes them, None at each unquoted
/// separator (a character of SEPS, or `&&`).  Inside '...' every character
/// is itself; inside "..." a backslash escapes only $ ` " \ and a newline;
/// outside both a backslash escapes the next character.  So load's
/// shell_quote reads back as what it quoted: `'/mnt/my data'` is one word,
/// and `'\''` is a closing quote, a quote and an opening one.  A quote that
/// never closes runs to the end.
pub(crate) fn lex(s: &str, seps: &[char]) -> Vec<Option<Word>> {
    fn word(cur: &mut Option<Word>) -> &mut Word {
        cur.get_or_insert_with(|| Word {
            text: String::new(),
            redirect: false,
        })
    }
    let mut out = Vec::new();
    let mut cur: Option<Word> = None;
    let mut it = s.chars().peekable();
    while let Some(c) = it.next() {
        match c {
            '\'' => {
                let w = word(&mut cur);
                for d in it.by_ref() {
                    if d == '\'' {
                        break;
                    }
                    w.text.push(d);
                }
            }
            '"' => {
                let w = word(&mut cur);
                while let Some(d) = it.next() {
                    match d {
                        '"' => break,
                        '\\' => match it.peek().copied() {
                            Some('\n') => {
                                it.next();
                            }
                            Some(e @ ('$' | '`' | '"' | '\\')) => {
                                it.next();
                                w.text.push(e);
                            }
                            _ => w.text.push('\\'),
                        },
                        _ => w.text.push(d),
                    }
                }
            }
            '\\' => match it.next() {
                Some('\n') | None => {}
                Some(d) => word(&mut cur).text.push(d),
            },
            '&' if it.peek() == Some(&'&') => {
                it.next();
                out.extend(cur.take().map(Some));
                out.push(None);
            }
            c if seps.contains(&c) => {
                out.extend(cur.take().map(Some));
                out.push(None);
            }
            c if c.is_whitespace() => out.extend(cur.take().map(Some)),
            c => {
                let w = word(&mut cur);
                w.redirect |= c == '<' || c == '>';
                w.text.push(c);
            }
        }
    }
    out.extend(cur.take().map(Some));
    out
}

/// A program's name: a word's last path part (/sbin/mount is mount).
pub(crate) fn prog(w: &str) -> &str {
    w.rsplit('/').next().unwrap_or(w)
}

/// Shell words that open or close a compound command, or run the command
/// after them, without being a command themselves.
pub(crate) const LEADERS: [&str; 18] = [
    "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "esac", "!", "{", "}", "(",
    ")", "exec", "time", "command",
];

/// The simple commands of a plan line, each as its words (lex: quotes
/// undone) with redirections (`>FILE`, `2>&1`, `> FILE`) left out: the line
/// itself, or, for `sh -c 'SCRIPT'`, each command of SCRIPT.  Commands are
/// split at ;, &&, ||, | and newlines outside quotes, and lose a leading if,
/// do, {, done and the like, and VAR=value assignments.  A command that runs
/// `sh -c SCRIPT` (as `chroot DIR sh -c SCRIPT` does) is followed by
/// SCRIPT's own commands.  A `[VAR=value...] sh -c 'SCRIPT' NAME ARG...`
/// whose SCRIPT runs `"$@"` (load's wrapper of a command, as the restore's
/// on FreeBSD's msdosfs ESP) runs ARG... as a command too.
pub(crate) fn commands(line: &str) -> Vec<Vec<String>> {
    let t = line.trim();
    let mut bare = t;
    while let Some((w, rest)) = bare.split_once(' ') {
        if !is_assignment(w) || w.contains('\'') {
            break;
        }
        bare = rest.trim_start();
    }
    if let Some((script, args)) = bare.strip_prefix("sh -c '").and_then(quoted_script) {
        let mut out = script_commands(&script);
        if script.contains("\"$@\"") {
            // ARGS is `NAME ARG...`: NAME is $0, ARG... the command.
            let mut w: Vec<String> = script_commands(args)
                .into_iter()
                .next()
                .unwrap_or_default()
                .into_iter()
                .skip(1)
                .collect();
            while w
                .first()
                .is_some_and(|x| LEADERS.contains(&x.as_str()) || is_assignment(x))
            {
                w.remove(0);
            }
            if !w.is_empty() {
                out.push(w);
            }
        }
        return out;
    }
    match bare.strip_prefix("sh -c ") {
        // SCRIPT in double quotes, or bare: its first word.
        Some(rest) => script_commands(
            &lex(rest, &[])
                .into_iter()
                .flatten()
                .next()
                .map(|w| w.text)
                .unwrap_or_default(),
        ),
        None => script_commands(t),
    }
}

fn is_assignment(x: &str) -> bool {
    x.contains('=') && !x.starts_with('-') && !x.starts_with('/')
}

/// SCRIPT (unescaped) and what follows it, of the text after `sh -c '`:
/// up to the closing quote, `'\''` being a quote inside.  None when the
/// quote never closes.
fn quoted_script(q: &str) -> Option<(String, &str)> {
    let mut script = String::new();
    let mut i = 0;
    while i < q.len() {
        if q[i..].starts_with("'\\''") {
            script.push('\'');
            i += 4;
        } else if q[i..].starts_with('\'') {
            return Some((script, &q[i + 1..]));
        } else {
            let c = q[i..].chars().next()?;
            script.push(c);
            i += c.len_utf8();
        }
    }
    None
}

fn script_commands(script: &str) -> Vec<Vec<String>> {
    let mut out = Vec::new();
    let mut w: Vec<String> = Vec::new();
    let mut file_next = false;
    // A None at the end closes the last command.
    for t in lex(script, &[';', '|', '\n']).into_iter().chain([None]) {
        let Some(x) = t else {
            file_next = false;
            while w
                .first()
                .is_some_and(|x| LEADERS.contains(&x.as_str()) || is_assignment(x))
            {
                w.remove(0);
            }
            if w.is_empty() {
                continue;
            }
            // `sh -c SCRIPT` inside it (chroot DIR sh -c SCRIPT): SCRIPT's
            // commands too.
            let inner = w
                .windows(3)
                .find(|p| matches!(prog(&p[0]), "sh" | "bash") && p[1] == "-c")
                .map(|p| p[2].clone());
            out.push(std::mem::take(&mut w));
            if let Some(s) = inner {
                out.extend(script_commands(&s));
            }
            continue;
        };
        if file_next {
            file_next = false;
        } else if x.redirect {
            // A redirection; `>` (`2>`, `&>`, `>>`, `<`) on its own takes the next word.
            file_next = x
                .text
                .trim_start_matches(|c: char| c.is_ascii_digit() || c == '&')
                .chars()
                .all(|c| c == '>' || c == '<');
        } else if !x.text.is_empty() {
            w.push(x.text);
        }
    }
    out
}

/// Whether STEP mounts something onto PATH: a `mount` (or mount_*, or
/// Redox's redoxfs) whose last operand is PATH.
pub(crate) fn mounts_at(s: &Step, path: &str) -> bool {
    commands(&s.cmd).iter().any(|c| {
        let p = prog(&c[0]);
        (p == "mount" || p.starts_with("mount_") || p == "redoxfs")
            && c.len() >= 3
            && c.last().is_some_and(|l| l == path)
    })
}

/// The datasets STEP mounts with `zfs mount DATASET`.
pub(crate) fn zfs_mounted(s: &Step) -> Vec<String> {
    commands(&s.cmd)
        .into_iter()
        .filter(|c| c.len() >= 3 && prog(&c[0]) == "zfs" && c[1] == "mount")
        .filter_map(|c| c.last().cloned())
        .collect()
}

/// Whether STEP restores the repository into PATH: `rustic-bko ... restore
/// ... PATH`.
pub(crate) fn restores_into(s: &Step, path: &str) -> bool {
    commands(&s.cmd).iter().any(|c| {
        prog(&c[0]) == "rustic-bko"
            && c.iter().skip(1).any(|w| w == "restore")
            && c.last().is_some_and(|l| l == path)
    })
}

/// Some(rooted) when STEP is a `zpool create` or `zpool import` of POOL;
/// rooted when it roots the pool at the target (-R, altroot=).
pub(crate) fn zpool_makes(s: &Step, pool: &str) -> Option<bool> {
    commands(&s.cmd)
        .into_iter()
        .find(|c| {
            c.len() >= 3
                && prog(&c[0]) == "zpool"
                && (c[1] == "create" || c[1] == "import")
                && c.iter().skip(2).any(|w| w == pool)
        })
        .map(|c| {
            c.windows(2).any(|p| p[0] == "-R" && p[1] == TARGET)
                || c.iter().any(|x| *x == format!("altroot={TARGET}"))
        })
}

pub(crate) fn str_at<'a>(v: &'a Value, k: &str) -> Option<&'a str> {
    v.get(k).and_then(Value::as_str)
}

pub(crate) fn arr<'a>(v: &'a Value, path: &[&str]) -> &'a [Value] {
    v.at(path)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
}

// ---- the invariants ----

/// The ZFS datasets the dump lists: (name, mountpoint).
pub(crate) fn datasets(dump: &Value) -> Vec<(String, String)> {
    arr(dump, &["storage", "zfs", "tools", "datasets"])
        .iter()
        .filter_map(|d| {
            Some((
                str_at(d, "name")?.to_string(),
                str_at(d, "mountpoint").unwrap_or("").to_string(),
            ))
        })
        .collect()
}

/// Whether a pool-making step (zpool create or import) of POOL roots it at
/// the target.
pub(crate) fn pool_rooted(assemble: &[Step], pool: &str) -> bool {
    assemble.iter().any(|s| zpool_makes(s, pool) == Some(true))
}

pub(crate) fn root_mounted(dump: &Value, sec: &Sections<'_>) -> Option<String> {
    root_mounted_by(dump, sec).err()
}

/// How the mount stage mounts / ("mount", "zfs mount"), or the violation.
pub(crate) fn root_mounted_by(dump: &Value, sec: &Sections<'_>) -> Result<&'static str, String> {
    let mount = sec.steps("mount");
    // A mount, or redoxfs, onto the target itself.
    if mount.iter().any(|s| mounts_at(s, TARGET)) {
        return Ok("mount");
    }
    // Ubuntu Core has no / of its own, its root being the base snap
    // (docs/design/ubuntu-core.md): its four run-mode roots, each mounted
    // at TARGET/run/mnt as snap-bootstrap mounts them.
    if dump.at(&["boot", "snapcore"]).is_some()
        && ["ubuntu-seed", "ubuntu-boot", "ubuntu-save", "data"]
            .iter()
            .all(|r| {
                mount
                    .iter()
                    .any(|s| mounts_at(s, &format!("{TARGET}/run/mnt/{r}")))
            })
    {
        return Ok("snapcore");
    }
    // zfs mount of the / dataset, in a pool under -R TARGET.
    let ds = datasets(dump);
    let assemble = sec.steps("assemble");
    for s in &mount {
        for name in zfs_mounted(s) {
            if ds.iter().any(|(n, mp)| *n == name && mp == "/")
                && pool_rooted(&assemble, name.split('/').next().unwrap_or(""))
            {
                return Ok("zfs mount");
            }
        }
    }
    Err(format!(
        "/ mounted: the mount stage mounts nothing on {TARGET} (no mount whose last operand it is, no zfs mount of a / dataset in a pool made with -R {TARGET})"
    ))
}

pub(crate) const PSEUDO: [&str; 30] = [
    "proc",
    "procfs",
    "sysfs",
    "tmpfs",
    "devtmpfs",
    "devpts",
    "devfs",
    "fdescfs",
    "linprocfs",
    "linsysfs",
    "kernfs",
    "ptyfs",
    "mfs",
    "ramfs",
    "securityfs",
    "debugfs",
    "efivarfs",
    "hugetlbfs",
    "mqueue",
    "pstore",
    "bpf",
    "tracefs",
    "configfs",
    "fusectl",
    "binfmt_misc",
    "autofs",
    "cgroup",
    "cgroup2",
    "overlay",
    "swap",
];
pub(crate) const NETWORK: [&str; 9] = [
    "nfs",
    "nfs4",
    "cifs",
    "smbfs",
    "smb3",
    "sshfs",
    "fuse.sshfs",
    "9p",
    "virtiofs",
];
pub(crate) const BIND: [&str; 2] = ["nullfs", "lofs"];

/// fstab's octal escapes (\040 for a space), undone as `mounts::unescape`
/// undoes them: one past \377 isn't a byte, and stays as it is written.
/// The fstab invariant doesn't use it: the dump's `file` is decoded already
/// (`mounts::parse_fstab`).
pub(crate) fn unescape(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\\'
            && i + 4 <= b.len()
            && b[i + 1..i + 4].iter().all(|c| (b'0'..=b'7').contains(c))
        {
            let v = b[i + 1..i + 4]
                .iter()
                .fold(0u32, |v, c| v * 8 + u32::from(c - b'0'));
            if let Ok(byte) = u8::try_from(v) {
                out.push(byte);
                i += 4;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The fstab invariant: violations, skipped entries, entries checked.
pub(crate) fn fstab_planned_n(
    dump: &Value,
    sec: &Sections<'_>,
) -> (Vec<String>, Vec<String>, usize) {
    let (mut bad, mut skipped, mut n) = (Vec::new(), Vec::new(), 0);
    let mount = sec.steps("mount");
    let notes = sec.list("# Notes:");
    let ds = datasets(dump);
    for e in arr(dump, &["storage", "fstab"]) {
        let spec = str_at(e, "spec").unwrap_or("");
        // As the dump has it: dump decoded fstab's octal escapes already
        // (mounts::parse_fstab), and load mounts at TARGET plus it, quoted
        // by shell_quote, which lex undoes.  Decoding it again would take a
        // literal \040 in a mount point for a space.
        let file = str_at(e, "file").unwrap_or("").to_string();
        let ty = str_at(e, "type").unwrap_or("");
        let opts: Vec<&str> = str_at(e, "options").unwrap_or("").split(',').collect();
        let what = format!("{file} ({spec}, {ty})");
        let skip = if ty == "swap" {
            Some("swap")
        } else if !file.starts_with('/') {
            Some("no absolute mount point")
        } else if opts.contains(&"noauto") {
            Some("noauto")
        } else if ty.split(',').any(|t| PSEUDO.contains(&t)) {
            Some("a pseudo filesystem")
        } else if ty.split(',').any(|t| NETWORK.contains(&t))
            || spec.starts_with("//")
            || (spec.contains(':') && !spec.starts_with('/') && !spec.contains('='))
        {
            Some("a network filesystem")
        } else if opts.iter().any(|o| *o == "bind" || *o == "rbind")
            || ty.split(',').any(|t| BIND.contains(&t))
        {
            Some("a bind mount")
        } else if file == "/" && dump.at(&["boot", "snapcore"]).is_some() {
            // snap-bootstrap's /dev/root: the base snap, a squashfs file on
            // ubuntu-data (docs/design/ubuntu-core.md).
            Some("Ubuntu Core's /, the base snap (a file on ubuntu-data)")
        } else {
            let device = [
                "UUID=",
                "LABEL=",
                "PARTUUID=",
                "PARTLABEL=",
                "NAME=",
                "/dev/",
            ]
            .iter()
            .any(|p| spec.starts_with(p))
                || ty == "zfs"
                || ds.iter().any(|(n, _)| n == spec)
                || e.get("resolves_to").is_some_and(|r| !r.is_null());
            (!device).then_some("not on a dumped device or filesystem")
        };
        if let Some(why) = skip {
            skipped.push(format!("fstab {what}: {why}"));
            continue;
        }
        n += 1;
        let at = if file == "/" {
            TARGET.to_string()
        } else {
            format!("{TARGET}{}", file.trim_end_matches('/'))
        };
        let mounted = mount.iter().any(|s| mounts_at(s, &at))
            || mount.iter().any(|s| {
                zfs_mounted(s)
                    .iter()
                    .any(|d| ds.iter().any(|(n, mp)| n == d && *mp == file))
            });
        let noted = notes.iter().any(|n| {
            n.starts_with(&format!("fstab's {file} ")) || n.starts_with(&format!("fstab's {file}:"))
        });
        if !mounted && !noted {
            bad.push(format!("every fstab mount planned: fstab's {what} is neither mounted at {at} by the mount stage nor named in a Note"));
        }
    }
    (bad, skipped, n)
}

pub(crate) fn archiver_found(sec: &Sections<'_>) -> Vec<String> {
    let mut bad = Vec::new();
    for p in sec.list("# Problems (") {
        if p.contains("rustic-bko")
            || p.contains("archiver")
            || p.contains("restore can't run")
            || p.contains("restore needs rustic-bko")
        {
            bad.push(format!("the archiver found: Problem {p:?}"));
        }
    }
    if !sec
        .steps("restore")
        .iter()
        .any(|s| restores_into(s, TARGET))
    {
        bad.push(format!(
            "the archiver found: the restore stage runs no `rustic-bko ... restore ... {TARGET}`"
        ));
    }
    bad
}

/// The dumped disk DEV is, or is a partition (or BSD label partition, or
/// illumos slice) of.
pub(crate) fn disk_of(dump: &Value, dev: &str) -> Option<String> {
    let dev = dev.trim_start_matches("/dev/");
    for d in arr(dump, &["storage", "disks"]) {
        let name = str_at(d, "name")?;
        if name == dev {
            return Some(name.to_string());
        }
        for k in ["partitions", "label_partitions"] {
            for p in d.get(k).and_then(Value::as_array).into_iter().flatten() {
                if str_at(p, "name") == Some(dev) {
                    return Some(name.to_string());
                }
                for q in p
                    .get("label_partitions")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                {
                    if str_at(q, "name") == Some(dev) {
                        return Some(name.to_string());
                    }
                }
            }
        }
    }
    None
}

pub(crate) fn target_of<'a>(p: &'a Planned, disk: &str) -> Option<&'a str> {
    p.maps
        .iter()
        .find(|(s, _, _)| s == disk)
        .map(|(_, t, _)| t.as_str())
}

/// Whether TEXT names a node of target disk TGT (Linux's /dev/sdq1, the
/// BSDs' /dev/sdqp3 and raw /dev/rsdqc, illumos's /dev/dsk/sdqs1, Haiku's
/// /dev/disk/sdq/0).
pub(crate) fn names_target(text: &str, tgt: &str) -> bool {
    [
        format!("/dev/{tgt}"),
        format!("/dev/r{tgt}"),
        format!("/dev/dsk/{tgt}"),
        format!("/dev/rdsk/{tgt}"),
        format!("/dev/disk/{tgt}/"),
    ]
    .iter()
    .any(|f| text.contains(f.as_str()))
}

pub(crate) fn pool_disks(dump: &Value, sec: &Sections<'_>, p: &Planned) -> Vec<String> {
    pool_disks_n(dump, sec, p).0
}

/// `pool_disks`, and how many containers it checked.
pub(crate) fn pool_disks_n(dump: &Value, sec: &Sections<'_>, p: &Planned) -> (Vec<String>, usize) {
    let mut bad = Vec::new();
    let mut n = 0;
    let (assemble, format) = (sec.steps("assemble"), sec.steps("format"));
    let text = |steps: &[&Step]| {
        steps
            .iter()
            .map(|s| s.text())
            .collect::<Vec<_>>()
            .join("\n")
    };
    // WHAT's members, each with the text of the commands that must name its
    // disk's target; NONE when no command makes WHAT.
    let mut check = |what: String, members: Vec<(String, String)>, none: bool| {
        n += 1;
        if none {
            bad.push(format!("pool disks in use: {what}: no command makes it"));
            return;
        }
        for (m, t) in members {
            let Some(disk) = disk_of(dump, &m) else {
                continue;
            };
            match target_of(p, &disk) {
                None => bad.push(format!("pool disks in use: {what}: member {m}'s disk {disk} has no target")),
                Some(tg) if !names_target(&t, tg) => bad.push(format!("pool disks in use: {what}: member {m} is on {disk} -> /dev/{tg}, which the command making it doesn't name")),
                Some(_) => {}
            }
        }
    };
    for a in arr(dump, &["storage", "md_arrays"]) {
        let name = str_at(a, "name").unwrap_or("?");
        let members: Vec<String> = arr(a, &["md", "members"])
            .iter()
            .filter_map(|m| str_at(m, "device").map(str::to_string))
            .collect();
        let uuid = a.at(&["md", "uuid"]).and_then(Value::as_str);
        let makers: Vec<&Step> = assemble
            .iter()
            .filter(|s| {
                commands(&s.cmd).iter().any(|c| {
                    prog(&c[0]) == "mdadm"
                        && c.get(1).is_some_and(|x| x == "--create")
                        && uuid.is_none_or(|u| {
                            c.iter().any(|w| *w == format!("--uuid={u}"))
                                || c.windows(2).any(|w| w[0] == "--uuid" && w[1] == u)
                        })
                })
            })
            .collect();
        let t = text(&makers);
        check(
            format!("md array {name}"),
            members.into_iter().map(|m| (m, t.clone())).collect(),
            makers.is_empty(),
        );
    }
    for pool in arr(dump, &["storage", "zfs", "pools_from_labels"]) {
        let name = str_at(pool, "name").unwrap_or("?");
        let members: Vec<String> = arr(pool, &["member_devices"])
            .iter()
            .filter_map(|m| m.as_str().map(str::to_string))
            .collect();
        let makers: Vec<&Step> = assemble
            .iter()
            .filter(|s| zpool_makes(s, name).is_some())
            .collect();
        let t = text(&makers);
        check(
            format!("ZFS pool {name}"),
            members.into_iter().map(|m| (m, t.clone())).collect(),
            makers.is_empty(),
        );
    }
    for vg in arr(dump, &["storage", "lvm", "volume_groups"]) {
        let name = str_at(vg, "name").unwrap_or("?");
        let pvs: Vec<(String, Option<String>)> = arr(vg, &["physical_volumes"])
            .iter()
            .filter_map(|pv| {
                Some((
                    str_at(pv, "device")?.to_string(),
                    str_at(pv, "id").map(str::to_string),
                ))
            })
            .collect();
        // PVs on md arrays or LUKS are their containers' to check.
        if pvs.iter().all(|(m, _)| disk_of(dump, m).is_none()) {
            continue;
        }
        // vgcreate NAME, or a pvcreate of the VG's metadata file (load's /run/bko-holt/lvm/NAME.vg).
        let file = format!("/{name}.vg");
        let makers: Vec<&Step> = assemble
            .iter()
            .filter(|s| {
                commands(&s.cmd).iter().any(|c| match prog(&c[0]) {
                    "vgcreate" => c.iter().skip(1).any(|w| w == name),
                    "pvcreate" => c.iter().any(|w| w.ends_with(&file)),
                    _ => false,
                })
            })
            .collect();
        let members = pvs
            .iter()
            .map(|(m, id)| {
                // The PV's own pvcreate, by its --uuid, when the plan has one.
                let own: Vec<&Step> = match id {
                    Some(id) => makers
                        .iter()
                        .copied()
                        .filter(|s| {
                            commands(&s.cmd).iter().any(|c| {
                                prog(&c[0]) == "pvcreate"
                                    && (c.windows(2).any(|w| w[0] == "--uuid" && w[1] == *id)
                                        || c.iter().any(|w| *w == format!("--uuid={id}")))
                            })
                        })
                        .collect(),
                    None => Vec::new(),
                };
                (m.clone(), text(if own.is_empty() { &makers } else { &own }))
            })
            .collect();
        check(
            format!("LVM volume group {name}"),
            members,
            makers.is_empty(),
        );
    }
    for fs in arr(dump, &["storage", "btrfs"]) {
        let devices: Vec<String> = arr(fs, &["devices"])
            .iter()
            .filter_map(|d| d.as_str().map(str::to_string))
            .collect();
        if devices.len() < 2 {
            continue;
        }
        let uuid = str_at(fs, "uuid").unwrap_or("?");
        let makers: Vec<&Step> = format
            .iter()
            .filter(|s| {
                commands(&s.cmd).iter().any(|c| {
                    prog(&c[0]) == "mkfs.btrfs" && c.iter().any(|w| w.eq_ignore_ascii_case(uuid))
                })
            })
            .collect();
        let t = text(&makers);
        check(
            format!("btrfs {uuid}"),
            devices.into_iter().map(|m| (m, t.clone())).collect(),
            makers.is_empty(),
        );
    }
    (bad, n)
}

/// Writes laid over zeros: what a device holds after `load::writetable`'s.
pub(crate) struct Sparse<'a> {
    size: u64,
    writes: &'a [(u64, Vec<u8>)],
}

impl ByteSource for Sparse<'_> {
    fn size(&self) -> u64 {
        self.size
    }
    fn read_at(&self, offset: u64, len: usize) -> Vec<u8> {
        if offset >= self.size {
            return Vec::new();
        }
        let len = len.min((self.size - offset) as usize);
        let mut out = vec![0u8; len];
        let end = offset + len as u64;
        for (at, b) in self.writes {
            let (s, e) = (*at, *at + b.len() as u64);
            if e <= offset || s >= end {
                continue;
            }
            let from = s.max(offset);
            let to = e.min(end);
            out[(from - offset) as usize..(to - offset) as usize]
                .copy_from_slice(&b[(from - s) as usize..(to - s) as usize]);
        }
        out
    }
}

pub(crate) fn mbr_signature(t: &Value) -> Option<u32> {
    match t.get("disk_signature")? {
        Value::Str(s) => u32::from_str_radix(s.trim_start_matches("0x"), 16).ok(),
        x => x.as_u64().map(|n| n as u32),
    }
}

/// DISK's table as WRITES put it on a device of SIZE bytes, read back: the
/// dump's IDs, or what differs.
pub(crate) fn table_ids(disk: &Value, writes: &[(u64, Vec<u8>)], size: u64) -> Vec<String> {
    let name = str_at(disk, "name").unwrap_or("?");
    let t = disk.get("partition_table").cloned().unwrap_or(Value::Null);
    let ss = t
        .get("sector_size")
        .and_then(Value::as_u64)
        .or_else(|| disk.get("logical_sector_size").and_then(Value::as_u64))
        .unwrap_or(512);
    let src = Sparse { size, writes };
    let mut bad = Vec::new();
    match (
        str_at(&t, "type"),
        bko_holt::parttable::read_table(&src, ss),
    ) {
        (Some("gpt"), Some(bko_holt::parttable::Table::Gpt { gpt, .. })) => {
            let want = str_at(&t, "disk_guid").unwrap_or("");
            if !gpt.disk_guid.eq_ignore_ascii_case(want) {
                bad.push(format!("disk IDs round-tripped: {name}: write-table's GPT reads back with disk GUID {} for {want}", gpt.disk_guid));
            }
            for p in arr(disk, &["partitions"]) {
                let (Some(n), Some(u)) =
                    (p.get("number").and_then(Value::as_u64), str_at(p, "uuid"))
                else {
                    continue;
                };
                match gpt.parts.iter().find(|q| u64::from(q.number) == n) {
                    Some(q) if q.uuid.eq_ignore_ascii_case(u) => {}
                    Some(q) => bad.push(format!("disk IDs round-tripped: {name}: write-table's partition {n} reads back as {} for {u}", q.uuid)),
                    None => bad.push(format!("disk IDs round-tripped: {name}: write-table's GPT has no partition {n}")),
                }
            }
        }
        (Some("dos" | "mbr"), Some(bko_holt::parttable::Table::Mbr(m))) => {
            if let Some(want) = mbr_signature(&t) {
                if m.disk_signature != want {
                    bad.push(format!("disk IDs round-tripped: {name}: write-table's MBR reads back with signature 0x{:08x} for 0x{want:08x}", m.disk_signature));
                }
            }
        }
        (ty, got) => bad.push(format!(
            "disk IDs round-tripped: {name}: write-table's {} table reads back as {}",
            ty.unwrap_or("?"),
            match got {
                Some(bko_holt::parttable::Table::Gpt { .. }) => "a GPT",
                Some(bko_holt::parttable::Table::Mbr(_)) => "an MBR",
                None => "nothing",
            }
        )),
    }
    bad
}

pub(crate) fn disk_ids(dump: &Value, sec: &Sections<'_>, p: &Planned) -> Vec<String> {
    disk_ids_n(dump, sec, p).0
}

/// `disk_ids`, and how many tables it checked: (sfdisk, write-table).
pub(crate) fn disk_ids_n(
    dump: &Value,
    sec: &Sections<'_>,
    p: &Planned,
) -> (Vec<String>, (usize, usize)) {
    let mut bad = Vec::new();
    let (mut nsf, mut nwt) = (0, 0);
    let part = sec.steps("partition");
    for d in arr(dump, &["storage", "disks"]) {
        let Some(name) = str_at(d, "name") else {
            continue;
        };
        let t = match d.get("partition_table") {
            Some(t) if !t.is_null() => t,
            _ => continue,
        };
        let Some((_, tgt, bytes)) = p.maps.iter().find(|(s, _, _)| s == name) else {
            continue;
        };
        let kind = str_at(t, "type").unwrap_or("?");
        // A table load writes itself (other/'s platforms):
        // `bko-holt write-table layout.json NAME DEVICE ...`.
        let devices: Vec<String> = part
            .iter()
            .flat_map(|s| commands(&s.cmd))
            .filter_map(|c| {
                let i = c.iter().position(|w| w == "write-table")?;
                (c.get(i + 1).map(String::as_str) == Some(super::LAYOUT)
                    && c.get(i + 2).map(String::as_str) == Some(name))
                .then(|| c.get(i + 3).cloned().unwrap_or_default())
            })
            .collect();
        if !devices.is_empty() {
            nwt += 1;
            if let Some(dev) = devices.iter().find(|dev| !names_target(dev, tgt)) {
                bad.push(format!("disk IDs round-tripped: {name}: write-table writes its table onto {dev:?}, not a node of its target /dev/{tgt}"));
                continue;
            }
            match bko_holt::load::writetable::plan(d, *bytes) {
                Ok(w) => bad.extend(table_ids(d, &w, *bytes)),
                Err(e) => bad.push(format!(
                    "disk IDs round-tripped: {name}: write-table's plan fails: {e}"
                )),
            }
            continue;
        }
        let Some(sf) = part.iter().find(|s| {
            s.cmd.starts_with("sfdisk") && words(&s.cmd).iter().any(|w| *w == format!("/dev/{tgt}"))
        }) else {
            bad.push(format!("disk IDs round-tripped: {name} ({kind}) -> /dev/{tgt}: the partition stage writes no table on it"));
            continue;
        };
        nsf += 1;
        let label_id = sf
            .body
            .iter()
            .find_map(|l| l.strip_prefix("label-id: "))
            .unwrap_or("");
        match kind {
            "dos" | "mbr" => {
                if let Some(sig) = mbr_signature(t) {
                    let want = format!("0x{sig:08x}");
                    if !label_id.eq_ignore_ascii_case(&want) {
                        bad.push(format!("disk IDs round-tripped: {name}: label-id {label_id:?} for the disk signature {want}"));
                    }
                }
            }
            "gpt" => {
                let want = str_at(t, "disk_guid").unwrap_or("");
                if !label_id.eq_ignore_ascii_case(want) {
                    bad.push(format!("disk IDs round-tripped: {name}: label-id {label_id:?} for the disk GUID {want}"));
                }
                let body = sf.body.join("\n").to_ascii_lowercase();
                for q in arr(d, &["partitions"]) {
                    if let Some(u) = str_at(q, "uuid") {
                        if !body.contains(&format!("uuid={}", u.to_ascii_lowercase())) {
                            bad.push(format!("disk IDs round-tripped: {name}: partition {} ({u}) isn't given its uuid", q.get("number").and_then(Value::as_u64).unwrap_or(0)));
                        }
                    }
                }
            }
            other => bad.push(format!("disk IDs round-tripped: {name}: a {other} table")),
        }
    }
    (bad, (nsf, nwt))
}

/// The five invariants of one plan of DUMP.
pub(crate) fn invariants(dump: &Value, p: &Planned) -> Check {
    let mut c = Check::default();
    if p.out.is_empty() {
        c.violations.push(format!(
            "load made no plan (exit {}): {}",
            p.code,
            p.err.lines().next().unwrap_or("")
        ));
        return c;
    }
    let sec = Sections::of(&p.out);
    match root_mounted_by(dump, &sec) {
        Ok(how) => c.checked.push(format!("/ by {how}")),
        Err(e) => c.violations.push(e),
    }
    let (bad, skipped, n) = fstab_planned_n(dump, &sec);
    c.violations.extend(bad);
    c.skipped.extend(skipped);
    c.checked.push(format!("{n} fstab"));
    c.violations.extend(archiver_found(&sec));
    let (bad, n) = pool_disks_n(dump, &sec, p);
    c.violations.extend(bad);
    c.checked.push(format!("{n} pools"));
    let (bad, (nsf, nwt)) = disk_ids_n(dump, &sec, p);
    c.violations.extend(bad);
    c.checked.push(format!(
        "{} tables ({nsf} sfdisk, {nwt} write-table)",
        nsf + nwt
    ));
    c
}

// ---- the run ----
