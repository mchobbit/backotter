//! `bko-holt stick --json`'s document (holt-json.md, `bko-holt-stick`,
//! D11): the stick's config partition and the machines on it, read into
//! what the Machine screen shows.  Every string from it is cleaned before
//! it is shown; unknown values fail closed (Principle 6): a machine
//! whose `state` isn't `ok` can't be chosen, a `config.state` that isn't
//! `ok` is a stick with no settings.

use crate::json::Value;
use crate::text::{cap, Clean};

use super::strings as s;

/// The schema of `bko-holt-stick` this reads.
pub const SCHEMA: u64 = 1;

/// Operating systems' names as people know them, by os-release's `ID`
/// (or bko-holt's own for the BSDs, illumos and Haiku); any other is
/// shown as it is, its first letter in upper case.
pub const OS_NAMES: &[(&str, &str)] = &[
    ("debian", "Debian"),
    ("ubuntu", "Ubuntu"),
    ("fedora", "Fedora"),
    ("rhel", "RHEL"),
    ("rocky", "Rocky"),
    ("almalinux", "AlmaLinux"),
    ("centos", "CentOS"),
    ("opensuse-leap", "openSUSE Leap"),
    ("opensuse-tumbleweed", "openSUSE Tumbleweed"),
    ("opensuse-microos", "openSUSE MicroOS"),
    ("arch", "Arch"),
    ("endeavouros", "EndeavourOS"),
    ("manjaro", "Manjaro"),
    ("cachyos", "CachyOS"),
    ("garuda", "Garuda"),
    ("gentoo", "Gentoo"),
    ("void", "Void"),
    ("alpine", "Alpine"),
    ("nixos", "NixOS"),
    ("guix", "Guix"),
    ("linuxmint", "Mint"),
    ("pop", "Pop!_OS"),
    ("elementary", "elementary OS"),
    ("kali", "Kali"),
    ("slackware", "Slackware"),
    ("devuan", "Devuan"),
    ("mx", "MX"),
    ("artix", "Artix"),
    ("solus", "Solus"),
    ("chimera", "Chimera"),
    ("aerynos", "AerynOS"),
    ("proxmox", "Proxmox"),
    ("freebsd", "FreeBSD"),
    ("netbsd", "NetBSD"),
    ("openbsd", "OpenBSD"),
    ("dragonfly", "DragonFly"),
    ("omnios", "OmniOS"),
    ("openindiana", "OpenIndiana"),
    ("illumos", "illumos"),
    ("haiku", "Haiku"),
];

#[derive(Clone, Debug)]
pub struct Machine {
    pub install_id: String,
    /// `ok`, or why it can't be chosen (`checksum_bad`, ...).
    pub state: String,
    pub why: Option<String>,
    pub hostname: Option<String>,
    pub os: Option<String>,
    /// system.json's `written` (UTC), when the backup last wrote it.
    pub written: Option<String>,
    pub this_computer: bool,
    pub repo_kind: Option<String>,
    pub repo_url: Option<String>,
    pub repository_id: Option<String>,
    pub keyboard: Option<Value>,
    pub time: Option<Value>,
    /// The password hint: shown after two wrong passwords, never logged,
    /// stripped from bug reports.
    pub hint: Option<String>,
}

impl Machine {
    /// It can be chosen: its settings are good, and its backups' address
    /// is one this stick can open (`repo::place`).
    pub fn ok(&self) -> bool {
        self.state == "ok" && self.hostname.is_some() && self.repo_url.as_deref().and_then(super::repo::place).is_some()
    }

    /// The hostname, cleaned, or `(no name)`.
    pub fn name(&self) -> Clean {
        match &self.hostname {
            Some(h) => Clean::line(h, cap::HOSTNAME),
            None => Clean::ours(s::NO_NAME),
        }
    }
}

#[derive(Clone, Debug)]
pub struct Stick {
    /// `ok`, `missing`, `damaged`, `two_sticks`, `unreadable`, or a state
    /// this doesn't know (then: no settings).
    pub state: String,
    pub why: Option<String>,
    /// Partition N's device, for the reports.
    pub dev: Option<String>,
    pub machines: Vec<Machine>,
}

fn opt_str(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string)
}

/// `Debian 13`, `FreeBSD 15.0`.
pub fn os_label(os: &Value) -> Option<String> {
    let id = os.get("id").and_then(Value::as_str)?;
    let name = match OS_NAMES.iter().find(|(k, _)| *k == id) {
        Some((_, n)) => n.to_string(),
        None => {
            let mut c = id.chars();
            c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
        }
    };
    Some(match os.get("version_id").and_then(Value::as_str) {
        Some(v) if !v.is_empty() => format!("{name} {v}"),
        _ => name,
    })
}

/// The document, checked: its format and schema, and the version the api
/// document gave.
pub fn read(v: &Value, version: Option<&str>) -> Result<Stick, String> {
    if v.get_str("format") != Some("bko-holt-stick") {
        return Err("not a bko-holt-stick document".to_string());
    }
    if v.get_u64("schema") != Some(SCHEMA) {
        return Err(format!("bko-holt-stick schema {:?}, not {SCHEMA}", v.get("schema").map(Value::compact)));
    }
    if let (Some(want), Some(got)) = (version, v.get_str("version")) {
        if want != got {
            return Err(format!("written by bko-holt {got}, not {want}"));
        }
    }
    let config = v.get("config").ok_or("no config")?;
    let state = config.get_str("state").unwrap_or("unknown").to_string();
    let mut machines = Vec::new();
    if state == "ok" {
        for m in v.arr_at(&["machines"]).iter().take(64) {
            let repo = m.get("repository");
            machines.push(Machine {
                install_id: opt_str(m, "install_id").unwrap_or_default(),
                state: m.get_str("state").unwrap_or("unknown").to_string(),
                why: opt_str(m, "why"),
                hostname: opt_str(m, "hostname"),
                os: m.get("os").and_then(os_label),
                written: opt_str(m, "written"),
                this_computer: m.get_bool("this_computer") == Some(true),
                repo_kind: repo.and_then(|r| opt_str(r, "kind")),
                repo_url: repo.and_then(|r| opt_str(r, "url")),
                repository_id: opt_str(m, "repository_id"),
                keyboard: m.get("keyboard").filter(|k| !k.is_null()).cloned(),
                time: m.get("time").filter(|t| !t.is_null()).cloned(),
                hint: opt_str(m, "password_hint"),
            });
        }
    }
    // Two rows "this computer" would be a bko-holt bug: then none is.
    if machines.iter().filter(|m| m.this_computer).count() > 1 {
        for m in &mut machines {
            m.this_computer = false;
        }
    }
    Ok(Stick { state, why: opt_str(config, "why"), dev: opt_str(config, "dev"), machines })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scenario(name: &str, call: &str) -> Value {
        let p = format!("{}/../testvm/rescue/scenarios/{name}/{call}/stdout", env!("CARGO_MANIFEST_DIR"));
        crate::json::parse(&std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{p}: {e}"))).unwrap()
    }

    #[test]
    fn the_recorded_sticks() {
        let st = read(&scenario("stick-three", "02-stick"), Some("0.2.6")).unwrap();
        assert_eq!(st.state, "ok");
        let names: Vec<_> = st.machines.iter().map(|m| (m.hostname.clone().unwrap(), m.os.clone().unwrap())).collect();
        assert_eq!(names[0], ("lovelace".to_string(), "Debian 13".to_string()));
        assert_eq!(names[2], ("hopper".to_string(), "FreeBSD 15.0".to_string()));
        assert!(st.machines.iter().all(|m| m.ok() && !m.this_computer));
        assert_eq!(st.machines[0].hint.as_deref(), Some("the boat's name, then the year we moved"));
        let st = read(&scenario("stick-this-computer", "02-stick"), None).unwrap();
        assert!(st.machines[0].this_computer);
        let st = read(&scenario("stick-failing", "02-stick"), None).unwrap();
        let states: Vec<&str> = st.machines.iter().map(|m| m.state.as_str()).collect();
        assert!(states.contains(&"checksum_bad") && states.contains(&"schema_unknown"));
        assert_eq!(st.machines.iter().filter(|m| m.ok()).count(), 1);
        let st = read(&scenario("stick-missing", "02-stick"), None).unwrap();
        assert_eq!((st.state.as_str(), st.machines.len()), ("missing", 0));
        assert!(read(&scenario("stick-three", "02-stick"), Some("0.3.0")).is_err(), "another version");
        assert!(read(&crate::json::parse(r#"{"format":"bko-holt-stick","schema":2}"#).unwrap(), None).is_err());
    }
}
