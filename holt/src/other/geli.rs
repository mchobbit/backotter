//! FreeBSD's GELI providers (docs/design/geli.md): `dump` records how each
//! was made (probe/geli.rs reads its metadata), what's inside it when it's
//! attached, and whether a passphrase, a key file or both unlock it; `load`
//! makes it again with `geli init`, attaches it, and builds the pool or UFS
//! on its `.eli` device.  No key material is read, kept or written here:
//! the passphrase comes from --key-file or a prompt, and a key file the
//! restored system keeps is put in from the restored tree.

use super::freebsd;
use super::{Found, RawDisk};
use crate::bytes::FileSource;
use crate::json::{Obj, Value};
use crate::load::commands::{Builder, Cmd};
use crate::load::layout::Layout;
use crate::load::RUN_DIR;
use crate::mounts::FstabEntry;
use crate::probe::geli::{self as meta, Meta};
use crate::probe::{self, Content};
use crate::sys::Sys;

type Seen = [(RawDisk, Vec<Found>, Vec<(String, String)>)];

// ---- dump -------------------------------------------------------------

/// An attached provider, as `geli list` shows it.
#[derive(Debug, Clone, PartialEq)]
pub struct Attached {
    /// Its device name: vtbd0p3.eli, gpt/zfs0.eli.
    pub name: String,
    /// What it's attached on: vtbd0p3, gpt/zfs0.
    pub consumer: String,
    pub onetime: bool,
}

/// `geli list`: each geom's name, its first consumer, and whether it is
/// ONETIME.  Nothing else of it is kept.
pub fn parse_list(text: &str) -> Vec<Attached> {
    let mut out: Vec<Attached> = Vec::new();
    let mut in_consumers = false;
    for l in text.lines() {
        let t = l.trim();
        if let Some(n) = t.strip_prefix("Geom name:") {
            out.push(Attached { name: n.trim().to_string(), consumer: String::new(), onetime: false });
            in_consumers = false;
        } else if t == "Consumers:" {
            in_consumers = true;
        } else if t == "Providers:" {
            in_consumers = false;
        } else if let Some(f) = t.strip_prefix("Flags:") {
            if let Some(a) = out.last_mut() {
                a.onetime = f.split(',').any(|x| x.trim() == "ONETIME");
            }
        } else if in_consumers {
            if let (Some(a), Some((_, n))) = (out.last_mut(), t.split_once(". Name:")) {
                if a.consumer.is_empty() {
                    a.consumer = n.trim().to_string();
                }
            }
        }
    }
    out.retain(|a| !a.name.is_empty() && !a.consumer.is_empty());
    out
}

/// `NAME="value"` lines of loader.conf or rc.conf (the last one wins), with
/// comments and blank lines left out.
pub fn parse_conf(text: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for l in text.lines() {
        let l = l.trim();
        if l.starts_with('#') {
            continue;
        }
        let Some((k, v)) = l.split_once('=') else { continue };
        let k = k.trim();
        if k.is_empty() || !k.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
            continue;
        }
        let v = v.trim();
        let v = match v.strip_prefix('"') {
            Some(r) => r.split('"').next().unwrap_or(""),
            None => v.split('#').next().unwrap_or("").trim(),
        };
        out.retain(|(x, _)| x != k);
        out.push((k.to_string(), v.to_string()));
    }
    out
}

/// A provider's name as rc.d/geli and the loader put it in a variable's
/// name: gpt/zfs0 is gpt_zfs0.
fn var_name(dev: &str) -> String {
    dev.trim_start_matches("/dev/").replace(['/', '-', '.'], "_")
}

/// The key files that unlock a provider known by any of `names`, in order:
/// loader.conf's geli_X_keyfileN_name (N's order, for a boot provider), then
/// rc.conf's geli_X_flags' -k (rc.d/geli, for one in geli_devices); and
/// whether rc.d/geli attaches it with -p (no passphrase).
pub fn keyfiles(names: &[String], loader: &[(String, String)], rc: &[(String, String)]) -> (Vec<String>, bool) {
    let get = |c: &[(String, String)], k: &str| c.iter().find(|(x, _)| x == k).map(|(_, v)| v.clone());
    let vars: Vec<String> = names.iter().map(|n| var_name(n)).collect();
    let mut found: Vec<(u32, String)> = Vec::new();
    for (k, v) in loader {
        let Some(rest) = k.strip_prefix("geli_") else { continue };
        let Some((x, n)) = rest.strip_suffix("_name").and_then(|r| r.rsplit_once("_keyfile")) else { continue };
        let Ok(n) = n.parse::<u32>() else { continue };
        // The loader loads it for the provider its _type names
        // (PROV:geli_keyfileN), else the one in the variable's name.
        let ty = get(loader, &format!("geli_{x}_keyfile{n}_type"));
        let ours = match ty.as_deref().and_then(|t| t.split_once(":geli_keyfile")) {
            Some((p, _)) => names.iter().any(|m| m == p.trim_start_matches("/dev/")),
            None => vars.contains(&x.to_string()),
        };
        if ours && !v.is_empty() && get(loader, &format!("geli_{x}_keyfile{n}_load")).is_none_or(|l| l.eq_ignore_ascii_case("yes")) {
            found.push((n, v.clone()));
        }
    }
    found.sort();
    let mut files: Vec<String> = found.into_iter().map(|(_, f)| f).collect();
    let mut no_pass = false;
    let devices = get(rc, "geli_devices").unwrap_or_default();
    if let Some(dev) = devices.split_whitespace().find(|d| names.iter().any(|m| m == d.trim_start_matches("/dev/"))) {
        let flags = get(rc, &format!("geli_{}_flags", var_name(dev))).or_else(|| get(rc, "geli_default_flags")).unwrap_or_default();
        let mut w = flags.split_whitespace();
        while let Some(a) = w.next() {
            if a == "-p" {
                no_pass = true;
            } else if a == "-k" {
                if let Some(f) = w.next() {
                    if !files.iter().any(|x| x == f) {
                        files.push(f.to_string());
                    }
                }
            } else if a.starts_with('-') && !a.starts_with("--") && a.len() > 2 {
                // Joined flags (-pk FILE): -p, then -k's argument.
                no_pass |= a.contains('p');
                if a.ends_with('k') {
                    if let Some(f) = w.next() {
                        files.push(f.to_string());
                    }
                }
            }
        }
    }
    (files, no_pass)
}

/// What `dump` knows of GELI on this system beyond the metadata: what's
/// attached, and the loader's and rc.d/geli's configuration.
pub struct Source {
    pub attached: Vec<Attached>,
    pub loader: Vec<(String, String)>,
    pub rc: Vec<(String, String)>,
}

impl Source {
    pub fn read(sys: &Sys) -> Source {
        let mut rc = Vec::new();
        for f in ["/etc/rc.conf", "/etc/rc.conf.local"] {
            for (k, v) in parse_conf(&sys.read(f).unwrap_or_default()) {
                rc.retain(|(x, _): &(String, String)| *x != k);
                rc.push((k, v));
            }
        }
        Source { attached: parse_list(&sys.run("geli", &["list"]).unwrap_or_default()), loader: parse_conf(&sys.read("/boot/loader.conf").unwrap_or_default()), rc }
    }
}

/// The names a partition goes by for GELI: its own, its GPT label's and
/// GPT UUID's paths, and its GEOM label's.
fn names_of(f: &Found, glabel: Option<&str>) -> Vec<String> {
    let pj = |k: &str| f.json.0.iter().find(|(key, _)| key == k).and_then(|(_, v)| v.as_str().map(str::to_string)).filter(|s| !s.is_empty());
    let mut n = vec![f.name.clone()];
    n.extend(pj("partlabel").map(|l| format!("gpt/{l}")));
    n.extend(pj("uuid").map(|u| format!("gptid/{u}")));
    n.extend(glabel.map(|l| format!("label/{l}")));
    n
}

/// A provider's size: diskinfo's mediasize, else the metadata's sector size
/// below the consumer's last sector.
fn provider_size(sys: &Sys, dev: &str, part_size: u64, m: &Meta) -> u64 {
    sys.run("diskinfo", &[dev]).and_then(|t| t.split_whitespace().nth(2).and_then(|n| n.parse().ok())).unwrap_or_else(|| {
        let ss = u64::from(m.sectorsize.max(512));
        part_size.saturating_sub(512) / ss * ss
    })
}

/// A GELI partition's `geli` record, for `dump`: its metadata, the
/// provider it's attached as and what's in it, and what unlocks it.  The
/// contents inside an attached one are given back, by the provider's name,
/// for zfs_section; `part_extra` gets what belongs to the partition itself
/// (/.snap of a UFS inside, as for a plain UFS).
pub fn dump_partition(sys: &Sys, f: &Found, glabel: Option<&str>, seen: &Seen, mounts: &[FstabEntry], part_extra: &mut Obj) -> Vec<(String, Content)> {
    let Some(c) = f.contents.first().filter(|c| c.kind == "geli") else { return Vec::new() };
    let src = Source::read(sys);
    let m = meta::from_json(&{
        let mut o = c.details.clone();
        o.put("version", c.version.clone());
        o.build()
    });
    let att = src.attached.iter().find(|a| freebsd::resolve(&format!("/dev/{}", a.consumer), seen).as_deref() == Some(f.name.as_str()));
    let provider = att.map(|a| a.name.clone()).unwrap_or_else(|| format!("{}.eli", f.name));
    let mut names = names_of(f, glabel);
    if let Some(a) = att {
        if !names.contains(&a.consumer) {
            names.push(a.consumer.clone());
        }
    }
    let mut inner: Vec<Content> = Vec::new();
    let mut ufs = Obj::new();
    if let (Some(_), Some(m)) = (att, &m) {
        let dev = format!("/dev/{provider}");
        let size = provider_size(sys, &dev, f.size, m);
        if sys.privileged {
            match FileSource::open_sized(&sys.p(&dev), size) {
                Ok(s) => inner = probe::probe_all(&s),
                Err(e) => sys.warn(format!("{dev}: {e}")),
            }
        }
        if inner.first().is_some_and(|c| c.kind == "ufs") {
            if let Some(mp) = mounts.iter().find(|e| e.spec == dev || (e.spec.ends_with(".eli") && freebsd::resolve(&e.spec, seen).as_deref() == Some(f.name.as_str()))).map(|e| e.file.clone()) {
                part_extra.put("snap_dir", sys.is_dir(&format!("{}/.snap", if mp == "/" { "" } else { mp.as_str() })));
            }
            if let Some(t) = sys.run("dumpfs", &["-m", &dev]) {
                ufs.put("newfs", freebsd::newfs_of(&t, &dev));
            }
            if let Some(t) = sys.run("tunefs", &["-p", &dev]) {
                let mut o = Obj::new();
                for (k, v) in freebsd::parse_tunefs_p(&t) {
                    o.put(&k, v);
                }
                ufs.put("tunefs", o.build());
            }
        }
    }
    let (files, no_pass) = keyfiles(&names, &src.loader, &src.rc);
    let no_passphrase = no_pass || m.as_ref().is_some_and(|m| m.iterations == -1);
    let keyed_by = match (files.is_empty(), no_passphrase) {
        (true, false) => "passphrase",
        (_, true) => "keyfile",
        (false, false) => "both",
    };
    let mut rec = Obj::new().set("version", m.as_ref().map(|m| m.version));
    for (k, v) in &c.details.0 {
        rec.put(k, v.clone());
    }
    rec.put("provider", provider.as_str());
    rec.put("attached", att.is_some());
    rec.put("onetime", att.is_some_and(|a| a.onetime));
    rec.put("content", probe::content_json(&inner));
    rec.put("keyed_by", keyed_by);
    rec.put("keyfiles", files);
    for (k, v) in ufs.0 {
        rec.put(&k, v);
    }
    part_extra.put("geli", rec.build());
    inner.into_iter().map(|c| (provider.clone(), c)).collect()
}

/// A mount, fstab or swap entry's JSON, marked `eli` when its device is a
/// GELI provider (/dev/X.eli: `resolve` gives X's partition).
pub fn mark(v: Value, spec: &str) -> Value {
    match v {
        Value::Obj(mut o) if spec.ends_with(".eli") => {
            o.push(("eli".into(), Value::Bool(true)));
            Value::Obj(o)
        }
        v => v,
    }
}

/// A swap's JSON: on a .eli device of a partition with no GELI metadata,
/// it's onetime swap (rc's swapon attaches it with a fresh key each boot).
pub fn mark_swap(v: Value, file: &str, seen: &Seen) -> Value {
    let v = mark(v, file);
    if !file.ends_with(".eli") {
        return v;
    }
    let part = freebsd::resolve(file, seen);
    let has_meta = seen.iter().flat_map(|(_, f, _)| f.iter()).any(|f| Some(&f.name) == part.as_ref() && f.contents.first().is_some_and(|c| c.kind == "geli"));
    match v {
        Value::Obj(mut o) => {
            o.push(("onetime".into(), Value::Bool(!has_meta)));
            Value::Obj(o)
        }
        v => v,
    }
}

// ---- load -------------------------------------------------------------

/// A GELI provider of a layout, as load makes it again.
#[derive(Debug, Clone)]
struct Prov {
    /// The source partition (or whole disk) it's on.
    src: String,
    /// Its .eli name on the source: what the pool's vdev and fstab name.
    provider: String,
    rec: Value,
    meta: Option<Meta>,
    /// What --key-file NAME= takes for it.
    names: Vec<String>,
    disk_sector: u64,
    size: u64,
}

impl Prov {
    fn keyed_by(&self) -> &str {
        self.rec.get("keyed_by").and_then(Value::as_str).unwrap_or("passphrase")
    }

    fn keyfiles(&self) -> Vec<String> {
        self.rec.get("keyfiles").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect()
    }
}

fn providers(layout: &Layout) -> Vec<Prov> {
    let mut out = Vec::new();
    for d in &layout.disks {
        let whole = (d.name.clone(), d.content.clone(), None, d.size_bytes);
        let parts = d.parts.iter().map(|p| (p.name.clone(), p.content.clone(), p.label.clone(), p.sectors * d.sector_size));
        for (name, content, label, size) in std::iter::once(whole).chain(parts) {
            if content.get("type").and_then(Value::as_str) != Some("geli") {
                continue;
            }
            let rec = layout.device(&name).and_then(|v| v.get("geli")).cloned().unwrap_or_else(|| content.clone());
            let m = meta::from_json(&rec).or_else(|| meta::from_json(&content));
            let provider = rec.get("provider").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| format!("{name}.eli"));
            let mut names = vec![name.clone()];
            names.extend(label.map(|l| format!("gpt/{l}")));
            let base = provider.trim_end_matches(".eli").to_string();
            if !names.contains(&base) {
                names.push(base);
            }
            out.push(Prov { src: name, provider, rec, meta: m, names, disk_sector: d.sector_size, size });
        }
    }
    out
}

/// What's inside the GELI provider on `src`, for format: (its .eli name,
/// its content).
pub(crate) fn inner_leaf(layout: &Layout, src: &str) -> Option<(String, Value)> {
    let rec = layout.device(src)?.get("geli")?;
    let c = rec.get("content").filter(|c| !c.is_null())?;
    Some((rec.get("provider").and_then(Value::as_str)?.to_string(), c.clone()))
}

/// The source device an fstab entry's filesystem is on: the provider, for a
/// /dev/X.eli entry whose partition holds GELI's metadata.
pub(crate) fn fstab_src(layout: &Layout, entry: &Value, src: String) -> String {
    if entry.get("eli").and_then(Value::as_bool) != Some(true) {
        return src;
    }
    match layout.device(&src).and_then(|v| v.at(&["geli", "provider"])).and_then(Value::as_str) {
        Some(p) => p.to_string(),
        None => src,
    }
}

/// The file the passphrase load asks for once (D3) is kept in: mode 600, in
/// RAM, removed after its last use and at close.
fn pass_file(src: &str) -> String {
    format!("{RUN_DIR}/keys/{}.pass", src.replace('/', "_"))
}

/// Reads a new passphrase from the terminal without echo, into `file`.
fn prompt(src: &str, file: &str) -> Cmd {
    let script = format!(
        "umask 077; mkdir -p {RUN_DIR}/keys; stty -echo </dev/tty; printf 'GELI passphrase for the new {src}: ' >/dev/tty; IFS= read -r p </dev/tty; s=$?; stty echo </dev/tty; printf '\\n' >/dev/tty; [ $s -eq 0 ] && [ -n \"$p\" ] && printf %s \"$p\" > {file}"
    );
    Cmd::new(&["sh", "-c", &script]).why(format!("GELI on {src}: asks for the new passphrase, once (not echoed; kept in {file}, mode 600, until it's used)"))
}

fn tmp_key(src: &str) -> String {
    format!("{RUN_DIR}/keys/{}.key", src.replace('/', "_"))
}

impl Builder<'_> {
    /// The key the source's slot `slot` takes here from --key-file, by any
    /// of the provider's names (NAME, or NAME:1 for slot 1).
    fn geli_key(&self, p: &Prov, slot: u32) -> Option<String> {
        p.names.iter().find_map(|n| self.opts.key_file(&if slot == 0 { n.clone() } else { format!("{n}:{slot}") })).map(str::to_string)
    }

    /// Each GELI provider, after partitioning and before any pool or
    /// newfs: `geli init` as the source's, `geli attach`, its closer, and
    /// its .eli node for what goes on it.
    pub(crate) fn geli_assemble(&mut self, have: &mut Vec<String>) -> Vec<Cmd> {
        let mut out = Vec::new();
        for p in providers(self.layout) {
            let Some(dev) = self.names.dev(&p.src) else { continue };
            if !have.contains(&p.src) {
                continue;
            }
            if self.fam != Some(super::Family::FreeBsd) {
                self.problems.push(format!("{}: GELI is FreeBSD's; bko-holt load makes it only on FreeBSD", p.src));
                continue;
            }
            // What sits on it is planned either way: a refusal is the
            // provider's own problem.
            let eli = format!("{dev}.eli");
            self.names.made.insert(p.provider.clone(), eli.clone());
            have.push(p.provider.clone());
            let Some(m) = p.meta.clone() else {
                self.problems.push(format!("{}: GELI, but the dump's metadata for it can't be read", p.src));
                continue;
            };
            if !self.geli_refusals(&p, &m) {
                continue;
            }
            let mut names = p.names.join(", ");
            if m.slots().contains(&1) {
                names += &format!("; key slot 1: {}", p.names.iter().map(|n| format!("{n}:1")).collect::<Vec<_>>().join(", "));
            }
            self.notes.push(format!("GELI on {}: --key-file takes {names}", p.src));
            if p.rec.get("attached").and_then(Value::as_bool) == Some(false) {
                self.notes.push(format!("{}: its GELI provider wasn't attached when the dump was made, so what's in it isn't known; it is made empty", p.src));
            }
            let lost: Vec<String> = m.flag_names().into_iter().filter(|f| !["BOOT", "AUTH", "NODELETE", "GELIBOOT", "GELIDISPLAYPASS", "AUTORESIZE"].contains(&f.as_str())).collect();
            if !lost.is_empty() {
                self.notes.push(format!("{}: GELI flags {} aren't made by geli init", p.src, lost.join(", ")));
            }
            // The key init and attach take: the passphrase (--key-file, or
            // asked for once), or for a provider a key file unlocks, the
            // key file named or a temporary random one, replaced by the
            // restored key file in boot.
            let keyed = p.keyed_by().to_string();
            let given = self.geli_key(&p, 0);
            let (init_key, attach_key, pass_made): (Vec<String>, Vec<String>, Option<String>) = match (keyed.as_str(), &given) {
                ("passphrase", Some(f)) => (vec!["-J".into(), f.clone()], vec!["-j".into(), f.clone()], None),
                ("passphrase", None) => {
                    let f = pass_file(&p.src);
                    out.push(prompt(&p.src, &f));
                    self.closers.push(Cmd::new(&["rm", "-f", &f]));
                    (vec!["-J".into(), f.clone()], vec!["-j".into(), f.clone()], Some(f))
                }
                ("keyfile", Some(f)) => (vec!["-P".into(), "-K".into(), f.clone()], vec!["-p".into(), "-k".into(), f.clone()], None),
                _ => {
                    let k = tmp_key(&p.src);
                    out.push(Cmd::new(&["mkdir", "-p", "-m", "700", &format!("{RUN_DIR}/keys")]));
                    out.push(Cmd::new(&["dd", "if=/dev/urandom", &format!("of={k}"), "bs=64", "count=1", "status=none"]).why(format!(
                        "GELI on {} unlocks with {} on the restored system; until that's restored, a temporary key",
                        p.src,
                        if p.keyfiles().is_empty() { "a key file".to_string() } else { p.keyfiles().join(", ") }
                    )));
                    self.closers.push(Cmd::new(&["rm", "-f", &k]));
                    (vec!["-P".into(), "-K".into(), k.clone()], vec!["-p".into(), "-k".into(), k], None)
                }
            };
            let mut a: Vec<String> = vec!["geli".into(), "init".into()];
            for (bit, flag) in [(meta::FLAG_BOOT, "-b"), (meta::FLAG_GELIBOOT, "-g"), (meta::FLAG_GELIDISPLAYPASS, "-d"), (meta::FLAG_NODELETE, "-T")] {
                if m.flags & bit != 0 {
                    a.push(flag.into());
                }
            }
            if m.flags & meta::FLAG_AUTORESIZE == 0 {
                a.push("-R".into());
            }
            if m.auth() {
                a.extend(["-a".into(), m.aalgo.and_then(meta::aalgo_name).unwrap_or("?").to_string()]);
            }
            a.extend(["-e".into(), meta::ealgo_name(m.ealgo).unwrap_or("?").to_string(), "-l".into(), m.keylen.to_string(), "-s".into(), m.sectorsize.to_string()]);
            // The source's PKCS#5v2 cost (D2), where a passphrase is set now.
            if init_key[0] == "-J" && m.iterations >= 0 {
                a.extend(["-i".into(), m.iterations.to_string()]);
            }
            a.extend(["-V".into(), m.version.to_string()]);
            a.extend(init_key);
            // No metadata backup in the rescue system: boot writes the
            // restored system's (D5).
            a.extend(["-B".into(), "none".into(), dev.clone()]);
            let why = format!("GELI on {dev} ({}): {} {}-bit, {}-byte sectors, version {}, as the source's", p.src, meta::ealgo_name(m.ealgo).unwrap_or("?"), m.keylen, m.sectorsize, m.version);
            out.push(Cmd { argv: a, why: Some(why), ..Cmd::default() });
            let mut at: Vec<String> = vec!["geli".into(), "attach".into()];
            at.extend(attach_key);
            at.push(dev.clone());
            out.push(Cmd { argv: at, ..Cmd::default() });
            self.closers.push(Cmd::new(&["geli", "detach", &eli]));
            if m.auth() {
                let ss = m.sectorsize.max(512);
                let mins = p.size / (100 << 20) / 60;
                let script = format!(
                    "n=$(diskinfo {eli} | cut -f3); m=$((n / 1048576)); dd if=/dev/zero of={eli} bs=1m count=$m status=none && r=$(((n - m * 1048576) / {ss})) && {{ [ $r -eq 0 ] || dd if=/dev/zero of={eli} bs={ss} seek=$((m * 1048576 / {ss})) count=$r status=none; }}"
                );
                out.push(Cmd::new(&["sh", "-c", &script]).why(format!(
                    "{eli} is authenticated ({}): every sector's checksum is written once, all {} of it (about {} at 100 MB/s)",
                    m.aalgo.and_then(meta::aalgo_name).unwrap_or("?"),
                    crate::report::human(p.size),
                    if mins == 0 { "a minute".to_string() } else { format!("{mins} min") }
                )));
            }
            // Slot 1 (D4), when the source used it: with the passphrase
            // given for it.  A provider with both, after boot's slot 0.
            if keyed != "both" {
                out.extend(self.geli_slot1(&p, &m, &dev));
            }
            if let Some(f) = pass_made {
                out.push(Cmd::new(&["rm", "-f", &f]).why(format!("{}'s passphrase is in GELI's metadata now", p.src)));
            }
        }
        out
    }

    /// Key slot 1 as the source had it, and slot 0 removed where the source
    /// had only slot 1.
    fn geli_slot1(&mut self, p: &Prov, m: &Meta, dev: &str) -> Vec<Cmd> {
        let mut out = Vec::new();
        if !m.slots().contains(&1) {
            return out;
        }
        if let Some(f) = self.geli_key(p, 1) {
            out.push(Cmd::new(&["geli", "setkey", "-n", "1", "-J", &f, dev]).why(format!("{}: key slot 1, as the source's, with the passphrase in {f}", p.src)));
        }
        if !m.slots().contains(&0) {
            out.push(Cmd::new(&["geli", "delkey", "-n", "0", dev]).why(format!("{}: the source had key slot 1 only", p.src)));
        }
        out
    }

    /// What load refuses for a provider (in the plan, before any write).
    fn geli_refusals(&mut self, p: &Prov, m: &Meta) -> bool {
        let before = self.problems.len();
        let src = &p.src;
        if m.version > meta::VERSION_MAX {
            self.problems.push(format!("{src}: GELI metadata version {}; this rescue system's geli makes {} and older", m.version, meta::VERSION_MAX));
        } else {
            if !meta::EALGOS_MADE.contains(&m.ealgo) {
                self.problems.push(format!("{src}: GELI encryption {} ({}) isn't one this rescue system's geli makes", meta::ealgo_name(m.ealgo).unwrap_or("unknown"), m.ealgo));
            }
            if m.auth() && !m.aalgo.is_some_and(|a| meta::AALGOS_MADE.contains(&a)) {
                let a = m.aalgo.unwrap_or(0);
                self.problems.push(format!("{src}: GELI authentication {} ({a}) isn't one this rescue system's geli makes", meta::aalgo_name(a).unwrap_or("unknown")));
            }
            let ss = u64::from(m.sectorsize);
            if ss < p.disk_sector || ss % p.disk_sector.max(1) != 0 {
                self.problems.push(format!("{src}: GELI's {ss}-byte sectors don't fit the target's {}-byte ones", p.disk_sector));
            }
        }
        if m.slots().contains(&1) && self.geli_key(p, 1).is_none() {
            self.problems.push(format!("{src}: the source's GELI key slot 1 is in use; give its passphrase with --key-file {src}:1=FILE, or the restored system loses that way in"));
        }
        if p.keyed_by() == "keyfile" && p.keyfiles().is_empty() && self.geli_key(p, 0).is_none() {
            self.problems.push(format!("{src}: a GELI key file unlocks it, and the dump doesn't say which (loader.conf, rc.conf); give it with --key-file {src}=FILE"));
        }
        self.problems.len() == before
    }

    /// After the restore: the restored key files put in for the temporary
    /// ones, and /var/backups' metadata backup written again for the new
    /// provider (D5).
    pub(crate) fn geli_boot(&mut self) -> Vec<Cmd> {
        let t = self.opts.target.clone();
        let mut out = Vec::new();
        for p in providers(self.layout) {
            let (Some(dev), Some(m)) = (self.names.dev(&p.src), p.meta.clone()) else { continue };
            if !self.names.made.contains_key(&p.provider) {
                continue;
            }
            let keyed = p.keyed_by().to_string();
            if keyed == "both" || (keyed == "keyfile" && self.geli_key(&p, 0).is_none()) {
                let mut a: Vec<String> = vec!["geli".into(), "setkey".into(), "-n".into(), "0".into()];
                for k in p.keyfiles() {
                    a.extend(["-K".into(), format!("{t}{k}")]);
                }
                let mut pass = None;
                if keyed == "both" {
                    let f = match self.geli_key(&p, 0) {
                        Some(f) => f,
                        None => {
                            let f = pass_file(&p.src);
                            out.push(prompt(&p.src, &f));
                            self.closers.push(Cmd::new(&["rm", "-f", &f]));
                            pass = Some(f.clone());
                            f
                        }
                    };
                    if m.iterations >= 0 && m.slots() == [0] {
                        a.extend(["-i".into(), m.iterations.to_string()]);
                    }
                    a.extend(["-J".into(), f]);
                } else {
                    a.push("-P".into());
                }
                a.push(dev.clone());
                out.push(Cmd { argv: a, why: Some(format!("{}: the restored key file{} {}", p.src, if p.keyfiles().len() == 1 { "" } else { "s" }, p.keyfiles().join(", "))), ..Cmd::default() });
                out.push(Cmd::new(&["rm", "-f", &tmp_key(&p.src)]));
                if keyed == "both" {
                    out.extend(self.geli_slot1(&p, &m, &dev));
                }
                if let Some(f) = pass {
                    out.push(Cmd::new(&["rm", "-f", &f]));
                }
            }
            // geli init's backup of the source's metadata describes a
            // master key that no longer exists: `geli restore` from it
            // would destroy the new provider.
            let tgt = dev.trim_start_matches("/dev/").replace('/', "_");
            let mut cands = vec![p.src.replace('/', "_")];
            let base = p.provider.trim_end_matches(".eli").replace('/', "_");
            if !cands.contains(&base) {
                cands.push(base);
            }
            let list = cands.join(" ");
            let b = format!("{t}/var/backups");
            out.push(
                Cmd::new(&["sh", "-c", &format!("f=; for n in {list}; do [ ! -f {b}/$n.eli ] || f=1; done; [ -z \"$f\" ] || {{ (umask 077; geli backup {dev} {b}/{tgt}.eli) && for n in {list}; do [ $n = {tgt} ] || rm -f {b}/$n.eli; done; }}")])
                    .why(format!("{}: /var/backups' GELI metadata backup, written again for the new provider (the restored one is the old provider's)", p.src)),
            );
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// What `geli list` printed on the freebsd15-geli guest (geli.md, G1).
    pub const GUEST_LIST: &str = "Geom name: vtbd0p3.eli\nState: ACTIVE\nEncryptionAlgorithm: AES-XTS\nKeyLength: 256\nCrypto: accelerated software\nVersion: 7\nUsedKey: 0\nFlags: BOOT, GELIBOOT, AUTORESIZE\nKeysAllocated: 4\nKeysTotal: 4\nProviders:\n1. Name: vtbd0p3.eli\n   Mediasize: 14757654528 (14G)\n   Sectorsize: 4096\n   Mode: r1w1e1\nConsumers:\n1. Name: vtbd0p3\n   Mediasize: 14757658624 (14G)\n   Sectorsize: 512\n   Mode: r1w1e1\n";

    #[test]
    fn geli_list() {
        assert_eq!(parse_list(GUEST_LIST), vec![Attached { name: "vtbd0p3.eli".into(), consumer: "vtbd0p3".into(), onetime: false }]);
        let two = format!("{GUEST_LIST}\nGeom name: vtbd0p2.eli\nFlags: ONETIME, W-DETACH\nProviders:\n1. Name: vtbd0p2.eli\nConsumers:\n1. Name: gpt/swap0\n");
        let l = parse_list(&two);
        assert_eq!(l[1], Attached { name: "vtbd0p2.eli".into(), consumer: "gpt/swap0".into(), onetime: true });
        assert!(parse_list("").is_empty());
    }

    #[test]
    fn key_files_from_loader_and_rc_conf() {
        let n = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        // The guest: neither has geli_ lines.
        let loader = parse_conf("aesni_load=\"YES\"\ngeom_eli_load=\"YES\"\n");
        assert_eq!(keyfiles(&n(&["vtbd0p3", "gpt/zfs0"]), &loader, &[]), (vec![], false));
        // A boot provider's two key files, by the _type's provider.
        let loader = parse_conf(
            "geli_ada0p3_keyfile1_load=\"YES\"\ngeli_ada0p3_keyfile1_type=\"ada0p3:geli_keyfile1\"\ngeli_ada0p3_keyfile1_name=\"/boot/keys/b.key\"\n# a comment\ngeli_ada0p3_keyfile0_load=\"YES\"\ngeli_ada0p3_keyfile0_type=\"ada0p3:geli_keyfile0\"\ngeli_ada0p3_keyfile0_name=\"/boot/keys/a.key\"\ngeli_x_keyfile0_type=\"gpt/data:geli_keyfile0\"\ngeli_x_keyfile0_name=\"/boot/keys/d.key\"\ngeli_off_keyfile0_load=\"NO\"\ngeli_off_keyfile0_name=\"/boot/keys/off.key\"\n",
        );
        assert_eq!(keyfiles(&n(&["ada0p3"]), &loader, &[]).0, n(&["/boot/keys/a.key", "/boot/keys/b.key"]));
        assert_eq!(keyfiles(&n(&["ada0p4", "gpt/data"]), &loader, &[]).0, n(&["/boot/keys/d.key"]));
        assert!(keyfiles(&n(&["off"]), &loader, &[]).0.is_empty());
        // rc.d/geli: geli_devices and the provider's flags, gpt/ names as
        // gpt_ in the variable.
        let rc = parse_conf("geli_devices=\"ada1 gpt/backup-1\"\ngeli_ada1_flags=\"-p -k /etc/geli/ada1.key\"\ngeli_gpt_backup_1_flags=\"-k /etc/geli/b.key\"   # passphrase too\n");
        assert_eq!(keyfiles(&n(&["ada1"]), &[], &rc), (n(&["/etc/geli/ada1.key"]), true));
        assert_eq!(keyfiles(&n(&["ada2p1", "gpt/backup-1"]), &[], &rc), (n(&["/etc/geli/b.key"]), false));
        assert_eq!(keyfiles(&n(&["ada3"]), &[], &rc), (vec![], false));
        let rc = parse_conf("geli_devices=\"ada4\"\ngeli_default_flags=\"-pk /k\"\n");
        assert_eq!(keyfiles(&n(&["ada4"]), &[], &rc), (n(&["/k"]), true));
    }

    // ---- dump, on a fake FreeBSD ------------------------------------------

    const ESP_T: &str = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b";
    const SWAP_T: &str = "516e7cb5-6ecf-11d6-8ff8-00022d09712b";
    const UFS_T: &str = "516e7cb6-6ecf-11d6-8ff8-00022d09712b";

    /// A UEFI FreeBSD, 4 MiB: p1 the ESP, p2 onetime swap (vtbd0p2.eli in
    /// fstab), p3 GELI (the guest's metadata, with a salt and master keys
    /// of "SECRETKEY") holding UFS as / through vtbd0p3.eli.
    fn freebsd_ufs_on_geli() -> (crate::testutil::Tree, crate::sys::FakeRunner) {
        use crate::parttable::build::{write_gpt, GptSpec};
        let mut img = vec![0u8; 8192 * 512];
        write_gpt(&mut img, 512, &GptSpec {
            disk_guid: "bc000000-0000-4000-8000-000000000001",
            parts: vec![
                (ESP_T, "bc000000-0000-4000-8000-000000000002", 40, 99, 0, "efiboot0"),
                (SWAP_T, "bc000000-0000-4000-8000-000000000003", 100, 2047, 0, "swap0"),
                (UFS_T, "bc000000-0000-4000-8000-000000000004", 2048, 8150, 0, "root0"),
            ],
        });
        let mut sector = crate::probe::geli::sample::sector(7, 0x282, 22, 256, 0, 6103 * 512, 4096, 1, 3031038, 0);
        for (i, b) in sector[47..].iter_mut().enumerate() {
            *b = b"SECRETKEY"[i % 9];
        }
        img[8150 * 512..8151 * 512].copy_from_slice(&sector);
        let t = crate::testutil::Tree::new("geli-dump");
        t.file("/dev/vtbd0", &img).file("/dev/vtbd0p3.eli", crate::probe::ufs::sample::image("")).dir("/.snap");
        t.file("/etc/fstab", "# Device\tMountpoint\tFStype\tOptions\tDump\tPass#\n/dev/vtbd0p3.eli\t/\tufs\trw\t1\t1\n/dev/vtbd0p2.eli\tnone\tswap\tsw\t0\t0\n/dev/gpt/efiboot0\t/boot/efi\tmsdosfs\trw\t2\t2\n");
        t.file("/boot/loader.conf", "aesni_load=\"YES\"\ngeom_eli_load=\"YES\"\n");
        let r = crate::sys::FakeRunner::default()
            .with("sysctl -n kern.disks", "vtbd0")
            .with("sysctl -n machdep.bootmethod", "UEFI")
            .with("diskinfo -v vtbd0", "vtbd0\n\t512         \t# sectorsize\n\t4194304     \t# mediasize in bytes (4.0M)\n")
            .with("geli list", GUEST_LIST)
            .with("diskinfo /dev/vtbd0p3.eli", "/dev/vtbd0p3.eli\t4096\t131072\t32\n")
            .with("dumpfs -m /dev/vtbd0p3.eli", "# newfs command for /dev/vtbd0p3.eli (/dev/vtbd0p3.eli)\nnewfs -O 2 -U -a 32 -b 32768 -j -s 32 -t /dev/vtbd0p3.eli \n")
            .with("tunefs -p /dev/vtbd0p3.eli", "tunefs: POSIX.1e ACLs: (-a)                                disabled\ntunefs: volume label: (-L)                                 \n")
            .with("mount -p", "/dev/vtbd0p3.eli\t/\tufs\trw\t1 1\n/dev/gpt/efiboot0\t/boot/efi\tmsdosfs\trw\t2 2\n")
            .with("swapctl -l", "Device:       1024-blocks      Used:\n/dev/vtbd0p2.eli     971 0\n");
        (t, r)
    }

    #[test]
    fn dump_records_a_ufs_root_on_geli() {
        let (t, r) = freebsd_ufs_on_geli();
        let sys = Sys::new(t.root(), Box::new(r), true);
        let os = crate::osrel::parse("ID=freebsd\nVERSION_ID=\"15.1\"\n");
        let v = freebsd::report(&sys, super::super::Family::FreeBsd, Some(&os), "2026-11-14T00:00:00Z");
        let p3 = v.at(&["storage", "disks", "0", "partitions", "2"]).unwrap();
        assert_eq!(p3.at(&["content", "type"]).and_then(Value::as_str), Some("geli"));
        let g = p3.get("geli").unwrap();
        assert_eq!(g.at(&["content", "type"]).and_then(Value::as_str), Some("ufs"));
        let mut rest = g.clone();
        if let Value::Obj(o) = &mut rest {
            o.retain(|(k, _)| k != "content");
        }
        assert_eq!(
            rest.compact(),
            concat!(
                r#"{"version":7,"flags":["BOOT","GELIBOOT","AUTORESIZE"],"flags_raw":"0x282","ealgo":"AES-XTS","ealgo_id":22,"keylen":256,"aalgo":null,"aalgo_id":0,"provsize":3124736,"sectorsize":4096,"keys":[0],"iterations":3031038,"#,
                r#""provider":"vtbd0p3.eli","attached":true,"onetime":false,"keyed_by":"passphrase","keyfiles":[],"newfs":"newfs -O 2 -U -a 32 -b 32768 -j -s 32 -t DEVICE","tunefs":{"-a":"disabled","-L":""}}"#
            )
        );
        assert_eq!(p3.get("snap_dir"), Some(&Value::Bool(true)));
        assert_eq!(p3.get("mounted_at"), Some(&Value::from(vec!["/"])));
        // fstab, mounts and swaps name the partition, marked eli; the swap
        // is onetime (its partition has no metadata).
        let fstab = v.at(&["storage", "fstab"]).and_then(Value::as_array).unwrap();
        assert_eq!(fstab[0].get("resolves_to").and_then(Value::as_str), Some("vtbd0p3"));
        assert_eq!(fstab[0].get("eli"), Some(&Value::Bool(true)));
        assert_eq!(fstab[1].get("resolves_to").and_then(Value::as_str), Some("vtbd0p2"));
        assert_eq!(fstab[2].get("eli"), None);
        assert_eq!(v.at(&["storage", "mounts", "0", "device"]).and_then(Value::as_str), Some("vtbd0p3"));
        let sw = v.at(&["storage", "swaps", "0"]).unwrap();
        assert_eq!((sw.get("device").and_then(Value::as_str), sw.get("onetime")), (Some("vtbd0p2"), Some(&Value::Bool(true))));
        // No salt or master key bytes anywhere in it, and it plans.
        let text = v.pretty();
        assert!(!text.contains("SECRET") && !text.contains("ECRETK"), "{text}");
        let l = Layout::from_json(v.clone()).unwrap();
        assert_eq!(l.content("vtbd0p3.eli").and_then(|c| c.get("type")).and_then(Value::as_str), Some("ufs"));
        // A key file in loader.conf: keyed by both; -P on rc.d/geli's flags: by the key file.
        t.file("/boot/loader.conf", "geli_vtbd0p3_keyfile0_load=\"YES\"\ngeli_vtbd0p3_keyfile0_type=\"vtbd0p3:geli_keyfile0\"\ngeli_vtbd0p3_keyfile0_name=\"/boot/keys/root.key\"\n");
        let (_, r) = freebsd_ufs_on_geli();
        let v = freebsd::report(&Sys::new(t.root(), Box::new(r), true), super::super::Family::FreeBsd, Some(&os), "2026-11-14T00:00:00Z");
        let g = v.at(&["storage", "disks", "0", "partitions", "2", "geli"]).unwrap();
        assert_eq!(g.get("keyed_by").and_then(Value::as_str), Some("both"));
        assert_eq!(g.get("keyfiles"), Some(&Value::from(vec!["/boot/keys/root.key"])));
        // Redacted for a bug report, the key file's path goes too, and it
        // still plans the same.
        let (rv, _) = crate::redact::redact(&v);
        assert!(!rv.compact().contains("root.key"), "{}", rv.compact());
        if let Err(e) = crate::bugreport::check_plan(&v.pretty()) {
            panic!("redaction: {e}");
        }
        // Not attached: the provider's name, and nothing inside.
        let (_, r) = freebsd_ufs_on_geli();
        let r = r.with("geli list", "");
        let v = freebsd::report(&Sys::new(t.root(), Box::new(r), true), super::super::Family::FreeBsd, None, "x");
        let g = v.at(&["storage", "disks", "0", "partitions", "2", "geli"]).unwrap();
        assert_eq!((g.get("attached"), g.get("content"), g.get("provider").and_then(Value::as_str)), (Some(&Value::Bool(false)), Some(&Value::Null), Some("vtbd0p3.eli")));
    }

    #[test]
    fn a_pool_on_geli_is_the_providers() {
        // The guest's pool: its vdev is vtbd0p3.eli, a member under that
        // name, and its record's content the label's.
        let (t, r) = freebsd_ufs_on_geli();
        let cfg = crate::json::parse(
            r#"{"version": 5000, "name": "zroot", "state": 0, "txg": 4, "pool_guid": 777, "hostname": "h", "top_guid": 1, "guid": 1,
                "vdev_children": 1, "vdev_tree": {"type": "disk", "id": 0, "guid": 1, "path": "/dev/vtbd0p3.eli", "whole_disk": 1, "ashift": 12}}"#,
        )
        .unwrap();
        t.file("/dev/vtbd0p3.eli", crate::probe::zfs::pack::vdev(2 << 20, &cfg));
        let r = r.with("diskinfo /dev/vtbd0p3.eli", "/dev/vtbd0p3.eli\t4096\t2097152\t512\n");
        let v = freebsd::report(&Sys::new(t.root(), Box::new(r), true), super::super::Family::FreeBsd, None, "x");
        assert_eq!(v.at(&["storage", "zfs", "pools_from_labels", "0", "member_devices"]), Some(&Value::from(vec!["vtbd0p3.eli"])));
        assert_eq!(v.at(&["storage", "disks", "0", "partitions", "2", "geli", "content", "type"]).and_then(Value::as_str), Some("zfs_member"));
        assert_eq!(v.at(&["storage", "disks", "0", "partitions", "2", "content", "type"]).and_then(Value::as_str), Some("geli"));
    }

    #[test]
    fn resolve_eli_names() {
        let (t, r) = freebsd_ufs_on_geli();
        let sys = Sys::new(t.root(), Box::new(r), true);
        let d = freebsd::disks(&sys);
        let src = super::super::open(&sys, &d[0]).unwrap();
        let (_, found) = super::super::read_disk(&src, &d[0], &|n, t| freebsd::part_name("vtbd0", n, t));
        let seen = vec![(d[0].clone(), found, vec![])];
        for (spec, want) in [("/dev/vtbd0p3.eli", Some("vtbd0p3")), ("/dev/gpt/root0.eli", Some("vtbd0p3")), ("/dev/gpt/swap0.eli", Some("vtbd0p2")), ("/dev/vtbd0p2.eli", Some("vtbd0p2")), ("/dev/vtbd0p9.eli", None), ("vtbd0p3.eli", None)] {
            assert_eq!(freebsd::resolve(spec, &seen).as_deref(), want, "{spec}");
        }
        let sw = mark_swap(Obj::new().build(), "/dev/vtbd0p3.eli", &seen);
        assert_eq!(sw.get("onetime"), Some(&Value::Bool(false)));
        assert_eq!(mark(Obj::new().build(), "/dev/vtbd0p3").get("eli"), None);
    }

    /// machdep.bootmethod says UEFI or BIOS; without its answer (no such
    /// sysctl, or no sysctl) the firmware is unknown, with a warning, never
    /// bios (other::firmware_unknown).
    #[test]
    fn firmware_unknown_without_bootmethod() {
        let mode = |r: crate::sys::FakeRunner| {
            let (t, _) = freebsd_ufs_on_geli();
            let v = freebsd::report(&Sys::new(t.root(), Box::new(r), true), super::super::Family::FreeBsd, None, "x");
            let w: Vec<String> = v.get("warnings").and_then(Value::as_array).unwrap().iter().filter_map(Value::as_str).filter(|w| w.contains("firmware")).map(str::to_string).collect();
            (v.at(&["firmware", "mode"]).and_then(Value::as_str).unwrap().to_string(), v.at(&["boot", "loader", "firmware"]).and_then(Value::as_str).unwrap().to_string(), w)
        };
        assert_eq!(mode(freebsd_ufs_on_geli().1), ("uefi".into(), "uefi".into(), vec![]));
        assert_eq!(mode(freebsd_ufs_on_geli().1.with("sysctl -n machdep.bootmethod", "BIOS\n")), ("bios".into(), "bios".into(), vec![]));
        let mut r = freebsd_ufs_on_geli().1;
        r.answers.remove("sysctl -n machdep.bootmethod");
        let unknown = |why: &str| ("unknown".to_string(), "unknown".to_string(), vec![format!("the firmware mode (UEFI or BIOS) can't be told: {why}; the dump says unknown, and bko-holt load refuses it")]);
        assert_eq!(mode(r), unknown("`sysctl -n machdep.bootmethod` failed"));
        assert_eq!(mode(freebsd_ufs_on_geli().1.without("sysctl")), unknown("sysctl isn't installed"));
        assert_eq!(mode(freebsd_ufs_on_geli().1.with("sysctl -n machdep.bootmethod", "OpenFirmware\n")), unknown("`sysctl -n machdep.bootmethod` says \"openfirmware\""));
    }

    // ---- load: plans of FreeBSD layouts on GELI --------------------------

    fn at_mut<'a>(v: &'a mut Value, path: &[&str]) -> &'a mut Value {
        let mut v = v;
        for p in path {
            v = match v {
                Value::Arr(a) => &mut a[p.parse::<usize>().unwrap()],
                Value::Obj(o) => &mut o.iter_mut().find(|(k, _)| k == p).unwrap_or_else(|| panic!("no {p}")).1,
                _ => panic!("{p}: not a container"),
            };
        }
        v
    }

    fn put(v: &mut Value, key: &str, x: Value) {
        let Value::Obj(o) = v else { panic!("not an object") };
        match o.iter_mut().find(|(k, _)| k == key) {
            Some(slot) => slot.1 = x,
            None => o.push((key.to_string(), x)),
        }
    }

    fn j(t: &str) -> Value {
        crate::json::parse(t).unwrap()
    }

    /// The probe's content of the freebsd15-geli guest's provider.
    const GUEST_CONTENT: &str = r#"{"type":"geli","usage":"crypto","uuid":null,"label":null,"version":"7","flags":["BOOT","GELIBOOT","AUTORESIZE"],"flags_raw":"0x282","ealgo":"AES-XTS","ealgo_id":22,"keylen":256,"aalgo":null,"aalgo_id":0,"provsize":14757658624,"sectorsize":4096,"keys":[0],"iterations":3031038}"#;

    /// freebsd15's dump made the freebsd15-geli guest's: its pool on
    /// vtbd0p3.eli, with `change` made to vtbd0p3's geli record.
    fn zfs_on_geli(change: impl Fn(&mut Value)) -> Value {
        let text = std::fs::read_to_string(format!("{}/tests/corpus/freebsd15/src.json", env!("CARGO_MANIFEST_DIR"))).unwrap();
        let mut v = j(&text.replace("\"/dev/vtbd0p3\"", "\"/dev/vtbd0p3.eli\""));
        let p3 = at_mut(&mut v, &["storage", "disks", "0", "partitions", "2"]);
        let zfs = p3.get("content").cloned().unwrap();
        let mut rec = j(GUEST_CONTENT);
        let Value::Obj(o) = &mut rec else { unreachable!() };
        o.retain(|(k, _)| !["type", "usage", "uuid", "label"].contains(&k.as_str()));
        put(&mut rec, "version", Value::from(7u64));
        for (k, x) in [("provider", Value::from("vtbd0p3.eli")), ("attached", Value::Bool(true)), ("onetime", Value::Bool(false)), ("content", zfs), ("keyed_by", Value::from("passphrase")), ("keyfiles", Value::Arr(vec![]))] {
            put(&mut rec, k, x);
        }
        change(&mut rec);
        put(p3, "content", j(GUEST_CONTENT));
        put(p3, "geli", rec);
        v
    }

    /// The same disk with UFS as / on vtbd0p3.eli (no pool), and fstab's
    /// swap onetime on vtbd0p2.eli.
    fn ufs_on_geli(change: impl Fn(&mut Value)) -> Value {
        let mut v = zfs_on_geli(|r| {
            put(r, "content", j(r#"{"type":"ufs","usage":"filesystem","uuid":"6a1b2c3d01020304","label":null,"version":"2"}"#));
            put(r, "newfs", Value::from("newfs -O 2 -U -j -t DEVICE"));
            change(r);
        });
        put(at_mut(&mut v, &["storage"]), "zfs", j(r#"{"pools_from_labels": [], "zpool_cache": null, "tools": null}"#));
        put(
            at_mut(&mut v, &["storage"]),
            "fstab",
            j(r#"[{"spec": "/dev/vtbd0p3.eli", "file": "/", "type": "ufs", "options": "rw", "freq": 1, "passno": 1, "resolves_to": "vtbd0p3", "eli": true},
                 {"spec": "/dev/vtbd0p2.eli", "file": "none", "type": "swap", "options": "sw", "freq": 0, "passno": 0, "resolves_to": "vtbd0p2", "eli": true}]"#),
        );
        v
    }

    fn plan(v: Value, args: &[&str]) -> crate::load::commands::Built {
        use crate::load::{commands, target::Mapping, Parsed};
        let l = Layout::from_json(v).unwrap();
        let mut a = vec!["l.json".to_string()];
        a.extend(args.iter().map(|s| s.to_string()));
        let Ok(Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
        commands::build(&l, &o, &Mapping { pairs: vec![("vtbd0".into(), "vtbd1".into())], ..Mapping::default() })
    }

    fn stage(b: &crate::load::commands::Built, s: crate::load::Stage) -> Vec<String> {
        b.stage(s).iter().filter(|c| !c.is_note()).map(|c| c.argv.iter().map(|a| crate::load::shell_quote(a)).collect::<Vec<_>>().join(" ")).collect()
    }

    #[test]
    fn root_pool_on_geli_attended() {
        use crate::load::Stage;
        let b = plan(zfs_on_geli(|_| {}), &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let a = stage(&b, Stage::Assemble);
        let pass = format!("{RUN_DIR}/keys/vtbd0p3.pass");
        assert!(a[0].starts_with("sh -c 'umask 077; mkdir -p ") && a[0].contains("stty -echo </dev/tty") && a[0].ends_with(&format!("> {pass}'")), "{}", a[0]);
        assert_eq!(a[1], format!("geli init -b -g -e AES-XTS -l 256 -s 4096 -i 3031038 -V 7 -J {pass} -B none /dev/vtbd1p3"));
        assert_eq!(a[2], format!("geli attach -j {pass} /dev/vtbd1p3"));
        assert_eq!(a[3], format!("rm -f {pass}"));
        assert!(a[4].contains(" zroot /dev/vtbd1p3.eli;"), "{}", a[4]);
        assert!(b.stage(Stage::Assemble)[0].why.as_deref().unwrap().starts_with("GELI on vtbd0p3: asks for the new passphrase, once"));
        assert!(b.notes.contains(&"GELI on vtbd0p3: --key-file takes vtbd0p3, gpt/zfs0".to_string()), "{:?}", b.notes);
        assert!(b.made.contains(&("vtbd0p3.eli".to_string(), "/dev/vtbd1p3.eli".to_string())));
        // Close: the pool's export, then the provider's detach.
        let c = stage(&b, Stage::Close);
        let (e, d) = (c.iter().position(|x| x == "zpool export zroot").unwrap(), c.iter().position(|x| x == "geli detach /dev/vtbd1p3.eli").unwrap());
        assert!(e < d, "{c:?}");
        assert!(c.contains(&format!("rm -f {pass}")));
        // Boot: the metadata backup again for the new provider (D5).
        let bt = stage(&b, Stage::Boot);
        let bk = bt.iter().find(|x| x.contains("geli backup")).unwrap();
        assert!(bk.contains("for n in vtbd0p3; do [ ! -f /mnt/bko-target/var/backups/$n.eli ] || f=1; done"), "{bk}");
        assert!(bk.contains("geli backup /dev/vtbd1p3 /mnt/bko-target/var/backups/vtbd1p3.eli) && for n in vtbd0p3; do [ $n = vtbd1p3 ] || rm -f"), "{bk}");
        assert!(!bt.iter().any(|x| x.contains("setkey")));
    }

    #[test]
    fn key_file_names_the_passphrase() {
        use crate::load::Stage;
        for name in ["vtbd0p3", "gpt/zfs0"] {
            let b = plan(zfs_on_geli(|_| {}), &["--key-file", &format!("{name}=/media/k/pass")]);
            assert!(b.problems.is_empty(), "{:?}", b.problems);
            let a = stage(&b, Stage::Assemble);
            assert_eq!(a[0], "geli init -b -g -e AES-XTS -l 256 -s 4096 -i 3031038 -V 7 -J /media/k/pass -B none /dev/vtbd1p3");
            assert_eq!(a[1], "geli attach -j /media/k/pass /dev/vtbd1p3");
            assert!(!a.iter().any(|x| x.contains("/dev/tty") || x.contains(".pass")), "{a:?}");
        }
    }

    #[test]
    fn key_slot_1_needs_its_key_file() {
        use crate::load::Stage;
        let two = |r: &mut Value| put(r, "keys", Value::from(vec![0u64, 1]));
        let b = plan(zfs_on_geli(two), &["--key-file", "vtbd0p3=/k0"]);
        assert_eq!(b.problems, vec!["vtbd0p3: the source's GELI key slot 1 is in use; give its passphrase with --key-file vtbd0p3:1=FILE, or the restored system loses that way in"]);
        assert!(!stage(&b, Stage::Assemble).iter().any(|x| x.starts_with("geli")));
        let b = plan(zfs_on_geli(two), &["--key-file", "vtbd0p3=/k0", "--key-file", "gpt/zfs0:1=/k1"]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let a = stage(&b, Stage::Assemble);
        assert_eq!(a[2], "geli setkey -n 1 -J /k1 /dev/vtbd1p3");
        assert!(b.notes.iter().any(|n| n.ends_with("; key slot 1: vtbd0p3:1, gpt/zfs0:1")), "{:?}", b.notes);
        // Slot 1 alone: slot 0 goes once slot 1 is in.
        let b = plan(zfs_on_geli(|r| put(r, "keys", Value::from(vec![1u64]))), &["--key-file", "vtbd0p3:1=/k1"]);
        let a = stage(&b, Stage::Assemble);
        let i = a.iter().position(|x| x == "geli setkey -n 1 -J /k1 /dev/vtbd1p3").unwrap();
        assert_eq!(a[i + 1], "geli delkey -n 0 /dev/vtbd1p3");
    }

    #[test]
    fn authenticated_provider_is_written_through() {
        use crate::load::Stage;
        let b = plan(zfs_on_geli(|r| {
            put(r, "flags_raw", Value::from("0x52"));
            put(r, "aalgo_id", Value::from(18u64));
            put(r, "sectorsize", Value::from(512u64));
        }), &["--key-file", "vtbd0p3=/k"]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let a = stage(&b, Stage::Assemble);
        assert_eq!(a[0], "geli init -b -T -R -a HMAC/SHA256 -e AES-XTS -l 256 -s 512 -i 3031038 -V 7 -J /k -B none /dev/vtbd1p3");
        assert!(a[2].starts_with("sh -c 'n=$(diskinfo /dev/vtbd1p3.eli | cut -f3);") && a[2].contains("dd if=/dev/zero of=/dev/vtbd1p3.eli bs=1m count=$m"), "{}", a[2]);
        let why = b.stage(Stage::Assemble)[2].why.clone().unwrap();
        assert!(why.starts_with("/dev/vtbd1p3.eli is authenticated (HMAC/SHA256): every sector's checksum is written once, all 14 GiB of it (about 2 min"), "{why}");
    }

    #[test]
    fn what_this_rescue_geli_cannot_make_is_refused() {
        let cases: Vec<(Box<dyn Fn(&mut Value)>, &str)> = vec![
            (Box::new(|r| put(r, "version", Value::from(8u64))), "vtbd0p3: GELI metadata version 8; this rescue system's geli makes 7 and older"),
            (Box::new(|r| put(r, "ealgo_id", Value::from(3u64))), "vtbd0p3: GELI encryption Blowfish-CBC (3) isn't one this rescue system's geli makes"),
            (Box::new(|r| {
                put(r, "flags_raw", Value::from("0x292"));
                put(r, "aalgo_id", Value::from(6u64));
            }), "vtbd0p3: GELI authentication HMAC/MD5 (6) isn't one this rescue system's geli makes"),
            (Box::new(|r| put(r, "sectorsize", Value::from(256u64))), "vtbd0p3: GELI's 256-byte sectors don't fit the target's 512-byte ones"),
            (Box::new(|r| {
                put(r, "keyed_by", Value::from("keyfile"));
                put(r, "iterations", Value::from(-1i64));
            }), "vtbd0p3: a GELI key file unlocks it, and the dump doesn't say which (loader.conf, rc.conf); give it with --key-file vtbd0p3=FILE"),
        ];
        for (change, want) in cases {
            let b = plan(zfs_on_geli(change), &[]);
            assert_eq!(b.problems, vec![want.to_string()]);
        }
    }

    #[test]
    fn key_file_providers_get_the_restored_key_file() {
        use crate::load::Stage;
        let kf = |both: bool| move |r: &mut Value| {
            put(r, "keyed_by", Value::from(if both { "both" } else { "keyfile" }));
            put(r, "keyfiles", Value::from(vec!["/boot/keys/root.key"]));
            if !both {
                put(r, "iterations", Value::from(-1i64));
            }
        };
        let tmp = format!("{RUN_DIR}/keys/vtbd0p3.key");
        // A key file alone: a temporary key until the restored one is in.
        let b = plan(ufs_on_geli(kf(false)), &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        let a = stage(&b, Stage::Assemble);
        assert_eq!(a[1], format!("dd if=/dev/urandom of={tmp} bs=64 count=1 status=none"));
        assert_eq!(a[2], format!("geli init -b -g -e AES-XTS -l 256 -s 4096 -V 7 -P -K {tmp} -B none /dev/vtbd1p3"));
        assert_eq!(a[3], format!("geli attach -p -k {tmp} /dev/vtbd1p3"));
        let bt = stage(&b, Stage::Boot);
        let i = bt.iter().position(|x| x.starts_with("geli setkey")).unwrap();
        assert_eq!(bt[i], "geli setkey -n 0 -K /mnt/bko-target/boot/keys/root.key -P /dev/vtbd1p3");
        assert_eq!(bt[i + 1], format!("rm -f {tmp}"));
        // --key-file: the key file itself, and nothing to replace.
        let b = plan(ufs_on_geli(kf(false)), &["--key-file", "vtbd0p3=/media/root.key"]);
        let a = stage(&b, Stage::Assemble);
        assert_eq!(a[0], "geli init -b -g -e AES-XTS -l 256 -s 4096 -V 7 -P -K /media/root.key -B none /dev/vtbd1p3");
        assert!(!stage(&b, Stage::Boot).iter().any(|x| x.contains("setkey")));
        // Both: the passphrase asked for in boot, with the source's cost.
        let b = plan(ufs_on_geli(kf(true)), &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert!(stage(&b, Stage::Assemble).contains(&format!("geli init -b -g -e AES-XTS -l 256 -s 4096 -V 7 -P -K {tmp} -B none /dev/vtbd1p3")));
        let bt = stage(&b, Stage::Boot);
        let i = bt.iter().position(|x| x.starts_with("geli setkey")).unwrap();
        assert!(bt[i - 1].contains("stty -echo"), "{bt:?}");
        let pass = format!("{RUN_DIR}/keys/vtbd0p3.pass");
        assert_eq!(bt[i], format!("geli setkey -n 0 -K /mnt/bko-target/boot/keys/root.key -i 3031038 -J {pass} /dev/vtbd1p3"));
        assert_eq!(bt[i + 2], format!("rm -f {pass}"));
        let b = plan(ufs_on_geli(kf(true)), &["--key-file", "gpt/zfs0=/p"]);
        assert!(stage(&b, Stage::Boot).contains(&"geli setkey -n 0 -K /mnt/bko-target/boot/keys/root.key -i 3031038 -J /p /dev/vtbd1p3".to_string()));
    }

    #[test]
    fn ufs_root_on_geli_and_onetime_swap() {
        use crate::load::Stage;
        let b = plan(ufs_on_geli(|_| {}), &["--key-file", "vtbd0p3=/k"]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert!(stage(&b, Stage::Format).contains(&"newfs -O 2 -U -j -t /dev/vtbd1p3.eli".to_string()), "{:?}", stage(&b, Stage::Format));
        assert!(stage(&b, Stage::Mount).contains(&"mount -t ufs -o rw /dev/vtbd1p3.eli /mnt/bko-target".to_string()), "{:?}", stage(&b, Stage::Mount));
        assert!(b.filesystems.contains(&("/dev/vtbd1p3.eli".to_string(), "ufs".to_string(), None)));
        // The onetime swap: nothing made for it, nothing said.
        let all: Vec<String> = crate::load::Stage::WRITING.iter().flat_map(|s| stage(&b, *s)).collect();
        assert!(!all.iter().any(|x| x.contains("vtbd1p2")), "{all:?}");
        assert!(!b.notes.iter().any(|n| n.contains("vtbd0p2")), "{:?}", b.notes);
    }

    #[test]
    fn geli_only_on_freebsd() {
        let mut v = zfs_on_geli(|_| {});
        put(at_mut(&mut v, &["os"]), "family", Value::from("netbsd"));
        put(at_mut(&mut v, &["os"]), "id", Value::from("netbsd"));
        put(at_mut(&mut v, &["os"]), "version_id", Value::from("11.0"));
        let b = plan(v, &[]);
        assert!(b.problems.contains(&"vtbd0p3: GELI is FreeBSD's; bko-holt load makes it only on FreeBSD".to_string()), "{:?}", b.problems);
    }

    #[test]
    fn restore_spares_the_esps_mount_root() {
        // freebsd15-geli's restore: its ESP at /boot/efi is msdosfs, whose
        // root takes no owner, mode or times (EINVAL), so the archiver's
        // strict exit failed on those three warnings alone.
        use crate::load::Stage;
        let mut v = zfs_on_geli(|_| {});
        let Some(Value::Arr(f)) = at_mut(&mut v, &["storage"]).get("fstab").cloned() else { unreachable!() };
        let mut f = f;
        f.push(j(r#"{"spec": "/dev/gpt/efiboot0", "file": "/boot/efi", "type": "msdosfs", "options": "rw", "freq": 2, "passno": 2, "resolves_to": "vtbd0p1"}"#));
        put(at_mut(&mut v, &["storage"]), "fstab", Value::Arr(f));
        let b = plan(v, &["--repo", "/root/repo", "--password-file", "/root/pw", "--archiver", "/root/rustic-bko", "--snapshot", "latest:/mnt/src"]);
        let r: Vec<&crate::load::commands::Cmd> = b.stage(Stage::Restore).iter().filter(|c| !c.is_note()).collect();
        let allowed = r.iter().find(|c| c.write.as_deref() == Some(&format!("{RUN_DIR}/restore.fatroots"))).expect("the allowed warnings");
        assert_eq!(allowed.stdin.as_deref(), Some("[WARN] restore boot/efi: setting UID/GID failed.\n[WARN] restore boot/efi: setting User/Group failed.\n[WARN] restore boot/efi: chmod failed.\n[WARN] restore boot/efi: setting file times failed.\n"));
        let run = r.last().unwrap();
        assert_eq!(&run.argv[..2], ["sh", "-c"]);
        assert_eq!(&run.argv[3..6], ["sh", "/root/rustic-bko", "--repository"]);
        assert!(run.argv.ends_with(&["latest:/mnt/src".to_string(), "/mnt/bko-target".to_string()]), "{:?}", run.argv);
        assert_eq!(run.env, [("RUSTIC_STRICT_EXIT".to_string(), "true".to_string())]);
        // The script itself, with a stand-in archiver.
        let dir = crate::testutil::TempDir::new("fatroots");
        let d = dir.path().display().to_string();
        std::fs::write(format!("{d}/restore.fatroots"), allowed.stdin.as_deref().unwrap()).unwrap();
        let script = run.argv[2].replace(RUN_DIR, &d);
        let esc = "\x1b[0m\x1b[1m\x1b[31merror:\x1b[0m";
        let ok3 = "[WARN] restore boot/efi: setting UID/GID failed.\n[WARN] restore boot/efi: chmod failed.\n[WARN] restore boot/efi: setting file times failed.\n";
        let other = "[WARN] restore etc/passwd: chmod failed.\n";
        let fake = |stderr: String, code: u8| {
            let out = std::process::Command::new("sh").args(["-c", &script, "sh", "sh", "-c", &format!("printf '%s' \"$1\" >&2; exit {code}"), "fake", &stderr]).output().unwrap();
            (out.status.code(), String::from_utf8_lossy(&out.stderr).into_owned())
        };
        let (c, e) = fake(format!("{ok3}{esc} There were 3 errors (see the warnings above)\n"), 1);
        assert_eq!(c, Some(0), "{e}");
        assert!(e.contains("setting file times failed") && e.contains("no owner, mode or times on the root of /boot/efi"), "{e}");
        assert_eq!(fake(format!("{ok3}{other}{esc} There were 4 errors (see the warnings above)\n"), 1).0, Some(1));
        assert_eq!(fake("error: repository not found\n".into(), 1).0, Some(1));
        assert_eq!(fake(String::new(), 0).0, Some(0));
        assert_eq!(fake("boom\n".into(), 3).0, Some(3));
        // Without an msdosfs in fstab, the restore is the archiver's alone.
        let b = plan(zfs_on_geli(|_| {}), &["--repo", "/root/repo", "--archiver", "/root/rustic-bko", "--snapshot", "latest"]);
        let r: Vec<_> = b.stage(Stage::Restore).iter().filter(|c| !c.is_note()).collect();
        assert_eq!((r.len(), r[0].argv[0].as_str()), (1, "/root/rustic-bko"));
    }
}
