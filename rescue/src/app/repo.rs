//! The repository: where the backups are kept, and what rustic-bko says
//! about it (rescue-screens.md, "3. Repository", "4. Snapshot"; "Problems
//! and their help", bko-rescue's own codes).
//!
//! bko-rescue only ever reads the repository: `rustic-bko cat config`
//! (its ID, checked against the stick's) and `rustic-bko snapshots
//! --json`, each with `--password-file` in the session's directory.

use std::ffi::OsString;
use std::path::Path;

use crate::json::{self, Value};
use crate::text::{cap, Clean};

use super::sys::{Cmd, Output};
use super::time;

/// A snapshot list is read only up to this (rescue-tui.md, "Hard limits
/// in parsing").
pub const SNAPSHOTS_MAX: usize = 16 * 1024 * 1024;
/// The config file is small.
pub const CONFIG_MAX: usize = 64 * 1024;
/// How long one repository run may take: an sftp server far away.
pub const RUN_MS: u64 = 300_000;
/// The fewest hex digits of the stick's repository ID compared: a
/// shorter one says nothing, and is skipped.
pub const ID_MIN: usize = 8;

/// What rustic-bko's standard error says, matched in this order, case
/// aside: the first code whose words are in it.  rustic_core's wrong
/// password is its error C002 ("No suitable key found for the given
/// password"); the others are ssh's, opendal's and the OS's words.
pub const MARKERS: &[(Code, &[&str])] = &[
    (Code::WrongPassword, &["c002", "no suitable key found", "incorrect password"]),
    (Code::LoginFailed, &["host key verification failed", "permission denied", "authentication failed", "too many authentication failures", "no matching host key"]),
    (
        Code::Unreachable,
        &[
            "connection refused",
            "no route to host",
            "network is unreachable",
            "connection timed out",
            "could not resolve hostname",
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname",
        ],
    ),
    (Code::NotFound, &["no repository config file found", "no such file or directory", "not found"]),
];

/// bko-rescue's own problem codes from the repository.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Code {
    Unreachable,
    NotFound,
    LoginFailed,
    WrongPassword,
    IdMismatch,
    Error,
}

impl Code {
    pub fn name(self) -> &'static str {
        match self {
            Code::Unreachable => "repo_unreachable",
            Code::NotFound => "repo_not_found",
            Code::LoginFailed => "repo_login_failed",
            Code::WrongPassword => "wrong_password",
            Code::IdMismatch => "repo_id_mismatch",
            Code::Error => "repo_error",
        }
    }
}

/// Where the backups are, from an address.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Place {
    pub url: String,
    /// The server's name (`nas.local`); None for a local path.
    pub host: Option<String>,
    pub port: u16,
}

/// An address as rustic-bko takes it: `sftp://[user@]host[:port]/path`,
/// `rest:http[s]://host[:port]/path`, or an absolute path.  None for
/// anything else, or one with a password in it (holt-json.md, Secrets 9:
/// the password goes in its file).
pub fn place(url: &str) -> Option<Place> {
    let url = url.trim();
    if url.is_empty() || url.len() > cap::LINE || url.chars().any(|c| c.is_control() || c == ' ') {
        return None;
    }
    if url.starts_with('/') {
        return Some(Place { url: url.to_string(), host: None, port: 0 });
    }
    let (rest, default_port) = if let Some(r) = url.strip_prefix("sftp://") {
        (r, 22)
    } else if let Some(r) = url.strip_prefix("rest:https://") {
        (r, 443)
    } else if let Some(r) = url.strip_prefix("rest:http://") {
        (r, 80)
    } else {
        return None;
    };
    let (auth, path) = rest.split_once('/')?;
    if path.is_empty() {
        return None;
    }
    let hostport = match auth.rsplit_once('@') {
        Some((user, _)) if user.contains(':') => return None,
        Some((_, hp)) => hp,
        None => auth,
    };
    let (host, port) = match hostport.strip_prefix('[') {
        Some(v6) => {
            let (h, after) = v6.split_once(']')?;
            let port = match after.strip_prefix(':') {
                Some(p) => p.parse().ok()?,
                None if after.is_empty() => default_port,
                None => return None,
            };
            (h.to_string(), port)
        }
        None => match hostport.rsplit_once(':') {
            Some((h, p)) => (h.to_string(), p.parse().ok()?),
            None => (hostport.to_string(), default_port),
        },
    };
    if host.is_empty() || !host.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | ':' | '_')) {
        return None;
    }
    Some(Place { url: url.to_string(), host: Some(host), port })
}

/// rustic-bko's command line for `what` (`cat config`, `snapshots
/// --json`) on the repository.
pub fn cmd(rustic: &Path, url: &str, password_file: &Path, what: &[&str], max: usize) -> Cmd {
    let mut a: Vec<OsString> = vec!["--repository".into(), url.into(), "--password-file".into(), password_file.as_os_str().to_owned(), "--no-progress".into()];
    a.extend(what.iter().map(OsString::from));
    let mut c = Cmd::new(rustic, a);
    c.stdout_max = max;
    c.timeout_ms = RUN_MS;
    c
}

/// Why a run failed, by its exit and text; None when it didn't.
pub fn classify(o: &Output) -> Option<Code> {
    if o.code == Some(0) && !o.too_big && o.not_started.is_none() {
        return None;
    }
    if o.timed_out {
        return Some(Code::Unreachable);
    }
    if o.not_started.is_some() || o.too_big {
        return Some(Code::Error);
    }
    let e = o.stderr_text().to_lowercase();
    for (code, words) in MARKERS {
        if words.iter().any(|w| e.contains(w)) {
            return Some(*code);
        }
    }
    Some(Code::Error)
}

/// The repository's ID from `cat config`'s JSON.
pub fn config_id(stdout: &[u8]) -> Option<String> {
    let v = json::parse(std::str::from_utf8(stdout).ok()?).ok()?;
    let id = v.get_str("id")?;
    (id.len() >= ID_MIN && id.len() <= 128 && id.bytes().all(|b| b.is_ascii_hexdigit())).then(|| id.to_ascii_lowercase())
}

/// The stick's ID against the repository's (stick-config.md, "Which copy
/// wins"): the stick may keep its first hex digits only.  None when the
/// stick has none to compare (then nothing is checked, and the screen
/// doesn't say it was).
pub fn id_matches(stick: Option<&str>, repo: &str) -> Option<bool> {
    let s = stick?.trim().to_ascii_lowercase();
    if s.len() < ID_MIN || !s.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    Some(repo.starts_with(&s))
}

/// One backup.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Snap {
    pub id: String,
    /// Seconds since 1970, and the offset rustic-bko stored it with.
    pub time: i64,
    pub offset: i32,
    pub hostname: String,
    pub tags: Vec<String>,
    /// The bytes the backup read (`summary.total_bytes_processed`).
    pub bytes: Option<u64>,
}

impl Snap {
    pub fn short_id(&self) -> Clean {
        Clean::line(&self.id.chars().take(8).collect::<String>(), 8)
    }
}

fn snap(v: &Value) -> Option<Snap> {
    let id = v.get_str("id")?.to_string();
    if id.is_empty() || id.len() > 128 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let (time, offset) = time::parse_rfc3339(v.get_str("time")?)?;
    let tags = match v.get("tags") {
        Some(Value::Arr(a)) => a.iter().filter_map(Value::as_str).map(str::to_string).collect(),
        Some(Value::Str(s)) => s.split(',').filter(|t| !t.is_empty()).map(str::to_string).collect(),
        _ => Vec::new(),
    };
    Some(Snap {
        id,
        time,
        offset,
        hostname: v.get_str("hostname").unwrap_or("").to_string(),
        tags,
        bytes: v.at(&["summary", "total_bytes_processed"]).and_then(Value::as_u64),
    })
}

/// `snapshots --json`: a list of snapshots, or of groups, each with its
/// `snapshots` (rustic groups by host, label and paths unless told
/// not).  Newest first.  Entries that don't parse are left out and
/// counted.
pub fn snapshots(stdout: &[u8]) -> Result<(Vec<Snap>, usize), String> {
    let text = std::str::from_utf8(stdout).map_err(|_| "not UTF-8".to_string())?;
    let v = json::parse(text).map_err(|e| format!("not JSON: {e:?}"))?;
    let top = v.as_array().ok_or("not a list")?;
    let mut out = Vec::new();
    let mut bad = 0;
    for item in top {
        let list: Vec<&Value> = match item.get_arr("snapshots") {
            Some(g) => g.iter().collect(),
            None => vec![item],
        };
        for s in list {
            match snap(s) {
                Some(x) => out.push(x),
                None => bad += 1,
            }
        }
    }
    out.sort_by(|a, b| b.time.cmp(&a.time).then_with(|| a.id.cmp(&b.id)));
    Ok((out, bad))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn addresses() {
        let p = place("sftp://nas.local/backotter/lovelace").unwrap();
        assert_eq!((p.host.as_deref(), p.port), (Some("nas.local"), 22));
        assert_eq!(place("sftp://bko@nas.local:2222/x").unwrap().port, 2222);
        assert_eq!(place("rest:http://10.0.0.5:8000/lovelace").unwrap().host.as_deref(), Some("10.0.0.5"));
        assert_eq!(place("rest:https://b.example/x").unwrap().port, 443);
        assert_eq!(place("sftp://[fd00::5]:22/x").unwrap().host.as_deref(), Some("fd00::5"));
        assert_eq!(place("/mnt/backup/repo").unwrap().host, None);
        for bad in ["", "nas.local/x", "sftp://nas.local", "sftp://nas.local/", "sftp://u:pw@nas/x", "rest:http://u:pw@h/x", "s3:x", "sftp://na s/x", "sftp://nas;rm/x", "ftp://h/x"] {
            assert_eq!(place(bad), None, "{bad}");
        }
    }

    #[test]
    fn what_rustic_said() {
        let o = |code: i32, err: &str| Output { code: Some(code), stderr: err.as_bytes().to_vec(), ..Output::default() };
        assert_eq!(classify(&o(0, "")), None);
        assert_eq!(
            classify(&o(1, "[ERROR] The password that has been entered, seems to be incorrect. No suitable key found for the given password. [C002]")),
            Some(Code::WrongPassword)
        );
        assert_eq!(classify(&o(1, "ssh: connect to host nas.local port 22: Connection refused")), Some(Code::Unreachable));
        assert_eq!(classify(&o(1, "Host key verification failed.")), Some(Code::LoginFailed));
        assert_eq!(classify(&o(1, "No repository config file found for `x`. Please check the repository.")), Some(Code::NotFound));
        assert_eq!(classify(&o(101, "thread 'main' panicked")), Some(Code::Error));
        assert_eq!(classify(&Output { timed_out: true, ..Output::default() }), Some(Code::Unreachable));
        assert_eq!(classify(&Output::failed_to_start("No such file".into())), Some(Code::Error), "a missing rustic-bko isn't a missing repository");
    }

    #[test]
    fn ids() {
        assert_eq!(config_id(br#"{"version":2,"id":"FB1E7EC987523D2C0000","chunker_polynomial":"1"}"#).as_deref(), Some("fb1e7ec987523d2c0000"));
        assert_eq!(config_id(b"{}"), None);
        assert_eq!(id_matches(Some("fb1e7ec987523d2c"), "fb1e7ec987523d2c0000"), Some(true));
        assert_eq!(id_matches(Some("a3f9"), "a3f9000"), None, "too short to say");
        assert_eq!(id_matches(Some("deadbeef"), "fb1e7ec9"), Some(false));
        assert_eq!(id_matches(None, "fb1e7ec9"), None);
    }

    #[test]
    fn snapshot_lists() {
        let grouped = br#"[{"group_key":{"hostname":"lovelace"},"snapshots":[
            {"time":"2026-11-21T02:00:00.5+01:00","hostname":"lovelace","tags":[],"id":"3be04d2a11","summary":{"total_bytes_processed":61000000000}},
            {"time":"2026-11-22T02:00:00+01:00","hostname":"lovelace","tags":["weekly"],"id":"a7f3c90100"},
            {"time":"bad","id":"00"}]}]"#;
        let (s, bad) = snapshots(grouped).unwrap();
        assert_eq!(bad, 1);
        assert_eq!(s[0].id, "a7f3c90100", "newest first");
        assert_eq!((s[0].offset, s[0].tags.clone(), s[1].bytes), (3600, vec!["weekly".to_string()], Some(61_000_000_000)));
        let flat = br#"[{"time":"2026-11-22T01:00:00Z","hostname":"x","id":"ab"}]"#;
        assert_eq!(snapshots(flat).unwrap().0.len(), 1);
        assert!(snapshots(b"{}").is_err());
    }
}
