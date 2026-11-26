//! `bko-holt stick --json`: the rescue stick's config partition, read for
//! bko-rescue (holt-json.md, "bko-holt-stick"; rescue-tui.md's D11).
//!
//! The partition is found the way stick-config.md's "Finding the config at
//! boot" says: on the disk this system booted from only (the disk casper
//! mounted at /cdrom on Linux, the root file system's on the BSDs, /boot's
//! on Haiku), never by a scan for the label; on illumos, whose root is a RAM
//! disk on no disk, the disk whose partition of the marker's number has the
//! marker's size, FAT label and volume serial, and two such disks are
//! `two_sticks`.  It is read through that disk's own device by fatfs.rs,
//! mounted nowhere, and nothing on it is trusted: each file has its limit,
//! each machine's system.json its checksum.
//!
//! The layout isn't in the document: only what the Machine screen shows of
//! each machine, and its state.  `settings.toml` isn't read (no TOML reader
//! yet: holt-json.md's open question 4), which a note says.
//!
//! `bko-holt stick --copy INSTALL_ID OUT --json` (holt-json.md's open
//! question 8, "bko-holt-stick-copy") reads the partition exactly so, and
//! writes the disk layout the chosen machine's system.json carries (its
//! `layout`, a `bko-holt dump` document) to OUT, a new private file: parsed
//! from the bytes whose checksum was checked, with no second read of the
//! stick between the check and the write, and printed as dump prints it.
//! So OUT is byte for byte the dump only when system.json holds it as dump
//! printed it; it is always the same JSON.  Only the asked machine's
//! system.json is read.

use crate::bytes::{ByteSource, OffsetSource};
use crate::fatfs::{Entry, Volume};
use crate::json::{Obj, Value};
use crate::load::jsonout;
use crate::other::{Family, RawDisk};
use crate::sys::Sys;

pub const USAGE: &str = "Usage: bko-holt stick --json
       bko-holt stick --copy INSTALL_ID OUT --json

Reads the BackOtter rescue stick's config partition (backotter/ on its
BACKOTTER partition) off the disk this system was started from, without
mounting it, and writes what it holds as one JSON document
(`bko-holt-stick`, docs/design/holt-json.md) on standard output: the
partition's state, and each machine's hostname, OS, backup time and
sequence, DMI UUID, repository and checksum state.  For bko-rescue; run it
as root, on the rescue stick.

  --json          the document (the only form there is)
  --copy INSTALL_ID OUT
                  write the disk layout that machine's system.json carries
                  (the `bko-holt dump` in its `layout`), once the file's
                  checksum is checked, to OUT for `bko-holt load`: a new file
                  (mode 0600) in a directory of bko-holt's own user that no
                  one else may enter.  The document is then
                  `bko-holt-stick-copy`, or `bko-holt-error` saying why not
  --sysroot DIR   read a Linux tree under DIR (its proc/, sys/, dev/ and
                  cdrom/) instead of this system, running no commands; for
                  tests
  -h, --help      show this help

Exit status: 0 when the config partition was read (with --copy: and OUT
written), 1 when it is missing, damaged or unreadable, or with --copy when
the machine's layout can't be had (the document says why), 2 for bad
arguments or an OUT that isn't a new file in a private directory.
";

/// The system.json schemas this bko-holt reads.
pub const SYSTEM_SCHEMAS: &[u64] = &[1];

/// The dump schemas an image's bko-holt reads when its marker names none:
/// images built before markers carried `dump_schemas` (2026-11-26), whose
/// bko-holt reads dump schema 1 only (holt-json.md's open question 11).
pub const DUMP_SCHEMAS_UNNAMED: &[u64] = &[1];

/// The most machine directories listed; a stick with more says so.
pub const MACHINES_MAX: usize = 64;

/// The biggest system.json read (every image's config partition is 256 MiB
/// since 2026-11-26; a dump with ESP images can be tens of MiB).  The
/// backup tool writes none bigger (`room_check`).
pub const SYSTEM_MAX: u64 = 64 << 20;

/// The biggest marker, stick.json and system.json.sha256 read.
const SMALL_MAX: u64 = 64 << 10;

/// The longest string field passed on from a system.json (a hostname, a
/// URL, the hint): one longer makes the machine `unreadable`.
pub const FIELD_MAX: usize = 4096;

/// The biggest `keyboard` or `time` section passed on, as compact JSON.
pub const SECTION_MAX: usize = 16 << 10;

/// Where system.json keeps the password hint (rescue-screens' open
/// question 2, a new field the backup tool isn't writing yet): top level.
pub const HINT_KEY: &str = "password_hint";

/// DMI system UUIDs firmware gives every machine of a kind, so naming no
/// one computer (as dmidecode and fwupd treat them): never "this computer".
/// All zeros and all Fs are told by `dmi_uuid_names_one`; these are the
/// rest, lower case.
pub const DMI_UUID_PLACEHOLDERS: &[&str] = &[
    // AMI's and many boards' "To be filled by O.E.M." UUID.
    "03000200-0400-0500-0006-000700080009",
    "12345678-1234-5678-90ab-cddeefaabbcc",
];

/// Where system.json carries the disk layout (holt-json.md, open question 8,
/// a default chosen where the backup tool's spec was open): the whole
/// `bko-holt dump` document, as this top-level member.
pub const LAYOUT_KEY: &str = "layout";

/// The config partition's directory, and the machines' under it.
const DIR: &str = "backotter";
const MACHINES: &str = "backotter/machines";

/// `bko-holt stick ARGS`: the exit status.
pub fn main(args: &[String]) -> u8 {
    // --copy takes two values, and jsonout's flag places skip one.  The
    // help is looked for with both left out, so `--copy ID -h` is bad
    // arguments, not the help; --json with the first only, so a --copy
    // short of OUT (`--copy ID --json`) is still refused as JSON.
    const VALUE_FLAGS: &[&str] = &["--sysroot", "--copy"];
    if jsonout::asks_for_help(&without_copy_values(args), &["--sysroot"]) {
        print!("{USAGE}");
        return 0;
    }
    let json = jsonout::find_json_flag(args, VALUE_FLAGS).is_some();
    let refuse = |code: jsonout::ErrorCode, line: String| -> u8 {
        if json {
            jsonout::write_stdout(&jsonout::error_doc(2, code, &line));
        }
        eprintln!("{line}\n\n{USAGE}");
        2
    };
    if let Some(e) = jsonout::schema_error(args, VALUE_FLAGS) {
        return refuse(jsonout::ErrorCode::SchemaUnknown, format!("bko-holt stick: {e}"));
    }
    let mut sysroot: Option<String> = None;
    let mut copy: Option<(String, String)> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            a if jsonout::is_json_flag(a) => {}
            "--sysroot" => match it.next() {
                Some(d) if std::path::Path::new(d).is_dir() => sysroot = Some(d.clone()),
                Some(d) => return refuse(jsonout::ErrorCode::BadArgs, format!("bko-holt stick: --sysroot {d}: not a directory")),
                None => return refuse(jsonout::ErrorCode::BadArgs, "bko-holt stick: --sysroot needs a directory".into()),
            },
            "--copy" if copy.is_some() => return refuse(jsonout::ErrorCode::BadArgs, "bko-holt stick: --copy is given twice".into()),
            "--copy" => match (it.next(), it.next()) {
                (Some(id), Some(out)) if !out.starts_with('-') => copy = Some((id.clone(), out.clone())),
                _ => return refuse(jsonout::ErrorCode::BadArgs, "bko-holt stick: --copy needs an install ID and the file to write".into()),
            },
            other => return refuse(jsonout::ErrorCode::BadArgs, format!("bko-holt stick: unknown argument {other:?}")),
        }
    }
    if !json {
        eprint!("bko-holt stick: say --json: it writes the stick's config as JSON, for bko-rescue\n\n{USAGE}");
        return 2;
    }
    if let Some((id, out)) = &copy {
        // Both checked before the stick is read, as recovery-words checks
        // its OUT before a word.  An ID that isn't one isn't echoed.
        if !is_uuid(id) {
            return refuse(jsonout::ErrorCode::BadArgs, "bko-holt stick --copy: INSTALL_ID isn't an install ID (a UUID, the name of its directory under backotter/machines/)".into());
        }
        if !std::path::Path::new(out).is_absolute() {
            return refuse(jsonout::ErrorCode::OutUnsafe, "bko-holt stick --copy: OUT isn't an absolute path".into());
        }
        if let Some(why) = crate::recoverycmd::out_unsafe(out) {
            return refuse(jsonout::ErrorCode::OutUnsafe, format!("bko-holt stick --copy: {out}: {why}"));
        }
    }
    let (sys, fam) = match sysroot {
        // A tree's files are images anyone may read: as root for the reads.
        Some(d) => (Sys::new(d, Box::new(crate::sys::FakeRunner::default()), true), None),
        None => (Sys::host(), Family::current()),
    };
    let Some((id, out)) = copy else {
        let (doc, code) = document(&sys, fam);
        jsonout::write_stdout(&jsonout::line(&doc));
        return code;
    };
    match copy_layout(&sys, fam, &id, std::path::Path::new(&out)) {
        Ok(doc) => {
            jsonout::write_stdout(&jsonout::line(&doc));
            0
        }
        Err((code, line)) => {
            jsonout::write_stdout(&jsonout::error_doc(1, code, &line));
            eprintln!("{line}");
            1
        }
    }
}

/// ARGS without the two values of each `--copy` (up to `--`).
fn without_copy_values(args: &[String]) -> Vec<String> {
    let mut v = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        v.push(a.clone());
        if a == "--" {
            v.extend(it.cloned());
            break;
        }
        if a == "--copy" {
            it.next();
            it.next();
        }
    }
    v
}

/// What the image's marker (`/.disk/backotter.json`) says of the config
/// partition, and which system.json and dump schemas the image reads.
#[derive(Debug, Clone, PartialEq)]
pub struct Marker {
    pub image_id: String,
    pub platform: Option<String>,
    pub built: Option<String>,
    pub number: u32,
    pub sectors: u64,
    pub label: String,
    pub fat_serial: String,
    pub system_schemas: Vec<u64>,
    /// The dump schemas the image's bko-holt load reads, as the image build
    /// took them from its `bko-holt --version --json`; None in a marker
    /// from before they were recorded (see `dump_schemas_read`).
    pub dump_schemas: Option<Vec<u64>>,
}

impl Marker {
    /// The dump schemas the image's bko-holt load reads: the marker's, or
    /// DUMP_SCHEMAS_UNNAMED for a marker that names none.
    pub fn dump_schemas_read(&self) -> &[u64] {
        self.dump_schemas.as_deref().unwrap_or(DUMP_SCHEMAS_UNNAMED)
    }
}

/// Where the marker is: on the medium casper mounted (Linux), in /boot
/// (Haiku), else in the root file system.
pub fn marker_path(fam: Option<Family>) -> &'static str {
    match fam {
        None => "/cdrom/.disk/backotter.json",
        Some(Family::Haiku) => "/boot/.disk/backotter.json",
        Some(_) => "/.disk/backotter.json",
    }
}

/// The marker's fields, from its JSON TEXT.
pub fn parse_marker(text: &str) -> Result<Marker, String> {
    let v = crate::json::parse(text).map_err(|e| format!("not JSON ({e})"))?;
    if v.get_str("format") != Some("backotter-image") {
        return Err("its format isn't backotter-image".into());
    }
    let s = |k: &str| v.get_str(k).map(str::to_string);
    let c = v.get("config_partition").ok_or("it has no config_partition")?;
    let number = c.get_u64("number").and_then(|n| u32::try_from(n).ok()).filter(|n| (1..=128).contains(n)).ok_or("config_partition.number isn't a partition's number")?;
    let sectors = c.get_u64("sectors").filter(|n| *n > 0).ok_or("config_partition.sectors isn't a size")?;
    Ok(Marker {
        image_id: s("image_id").ok_or("it has no image_id")?,
        platform: s("platform"),
        built: s("built"),
        number,
        sectors,
        label: c.get_str("label").ok_or("config_partition.label is missing")?.to_string(),
        fat_serial: c.get_str("fat_serial").ok_or("config_partition.fat_serial is missing")?.to_string(),
        system_schemas: v.get_arr("system_schemas").map(|a| a.iter().filter_map(Value::as_u64).collect()).unwrap_or_default(),
        dump_schemas: v.get_arr("dump_schemas").map(|a| a.iter().filter_map(Value::as_u64).collect()),
    })
}

/// A state of the config partition other than `ok`, and why.
#[derive(Debug, Clone, PartialEq)]
struct NotOk {
    state: &'static str,
    why: String,
}

fn not_ok(state: &'static str, why: impl Into<String>) -> NotOk {
    NotOk { state, why: why.into() }
}

/// The boot disk, and the config partition on it.
#[derive(Debug, Clone)]
struct Located {
    node: String,
    found_by: &'static str,
    disk: RawDisk,
    /// The partition's offset and size on the disk, in bytes.
    start: u64,
    size: u64,
    /// Its own device, when the system names one.
    dev: Option<String>,
}

/// The `bko-holt-stick` document for this system (FAM: Linux when None),
/// and the exit status: 0 when the config partition was read, else 1.
pub fn document(sys: &Sys, fam: Option<Family>) -> (Value, u8) {
    let s = scan(sys, fam, None);
    (s.doc, s.code)
}

/// What `scan` found.
struct Scan {
    doc: Value,
    code: u8,
    /// The system.json bytes read of each machine directory named KEEP.
    kept: Vec<(String, Option<Vec<u8>>)>,
    /// With KEEP, the other machine directories, counted and not read.
    others: usize,
}

/// As `document`; with KEEP (--copy), only the machine directories named
/// KEEP (case aside) are read and judged, their system.json bytes kept
/// with the directory's name, and the rest are only counted: one machine's
/// copy doesn't read every machine's system.json.
fn scan(sys: &Sys, fam: Option<Family>, keep: Option<&str>) -> Scan {
    let mut d = Doc { boot_disk: Value::Null, partition: None, label: None, dev: None, image: Value::Null, stick: Value::Null, machines: Vec::new(), notes: Vec::new(), keep: keep.map(str::to_string), kept: Vec::new(), others: 0 };
    let r = read(sys, fam, &mut d);
    let (state, why) = match &r {
        Ok(()) => ("ok", None),
        Err(n) => (n.state, Some(n.why.clone())),
    };
    if r.is_err() {
        d.stick = Value::Null;
        d.machines.clear();
        d.kept.clear();
        d.others = 0;
    }
    let here_uuid = dmi_uuid_here(sys, fam);
    let mine = this_computer(&d.machines, here_uuid.as_deref());
    if mine.len() > 1 {
        // Cloned machines, or one reinstalled under a new install ID: the
        // UUID can't tell which, and the wrong one would be preselected.
        d.notes.push(note("this_computer_ambiguous", format!("{} machines on the stick were backed up from a computer with this one's DMI UUID: none is taken for this computer, choose by name", mine.len())));
    }
    for (k, m) in d.machines.iter_mut().enumerate() {
        m.put("this_computer", mine.len() == 1 && mine[0] == k);
    }
    let config = Obj::new().set("state", state).set("partition", d.partition).set("label", d.label).set("why", why).set("dev", d.dev);
    let doc = jsonout::header("bko-holt-stick")
        .set("boot_disk", d.boot_disk)
        .set("config", config)
        .set("image", d.image)
        .set("stick", d.stick)
        .set("here", Obj::new().set("dmi_uuid", here_uuid))
        .set("machines", d.machines)
        .set("notes", d.notes)
        .build();
    Scan { doc, code: if state == "ok" { 0 } else { 1 }, kept: d.kept, others: d.others }
}

/// `stick --copy ID OUT`: the `bko-holt-stick-copy` document once machine
/// ID's layout is in OUT (checked by the caller to be a new file in a
/// private directory), else the refusal's code and line.  The layout is
/// the system.json bytes whose checksum `scan` checked, held since and
/// parsed, printed as dump prints it: the stick isn't read again.
pub fn copy_layout(sys: &Sys, fam: Option<Family>, id: &str, out: &std::path::Path) -> Result<Value, (jsonout::ErrorCode, String)> {
    use jsonout::ErrorCode as E;
    let say = |what: String| format!("bko-holt stick --copy: {what}");
    let Scan { doc, code, kept, others } = scan(sys, fam, Some(id));
    if code != 0 {
        let state = doc.str_at(&["config", "state"]).unwrap_or("unreadable");
        let why = doc.str_at(&["config", "why"]).unwrap_or("");
        return Err((E::ConfigNotOk, say(format!("the config partition is {state}: {why}"))));
    }
    let rows: Vec<&Value> = doc.arr_at(&["machines"]).iter().filter(|m| m.get_str("install_id").is_some_and(|x| x.eq_ignore_ascii_case(id))).collect();
    let row = match rows.as_slice() {
        [r] => *r,
        [] => {
            let n = doc.arr_at(&["machines"]).len() + others;
            let over = doc.arr_at(&["notes"]).iter().any(|x| x.get_str("code") == Some("machines_over_limit"));
            let more = if over { format!(" (it lists the first {MACHINES_MAX} by name)") } else { String::new() };
            return Err((E::NoSuchMachine, say(format!("the stick has no machine {id}: it has {n}{more}"))));
        }
        many => return Err((E::TwoMachines, say(format!("{} directories of {MACHINES}/ are named {id}, case aside: it can't tell which is the machine's", many.len())))),
    };
    let path = row.get_str("path").unwrap_or_default().to_string();
    let host = row.get_str("hostname").map(|h| format!(" ({h})")).unwrap_or_default();
    match row.get_str("state") {
        Some("ok") => {}
        st => return Err((E::MachineNotOk, say(format!("machine {id}{host} is {}: {}", st.unwrap_or("unreadable"), row.get_str("why").unwrap_or(""))))),
    }
    // Its row is ok: its bytes were kept, are JSON and passed the checksum.
    let body = kept.into_iter().find(|(n, _)| n.eq_ignore_ascii_case(id)).and_then(|(_, b)| b).ok_or_else(|| (E::MachineNotOk, say(format!("{path} wasn't kept as it was read"))))?;
    let v = std::str::from_utf8(&body).ok().and_then(|t| crate::json::parse(t).ok()).ok_or_else(|| (E::MachineNotOk, say(format!("{path} isn't JSON"))))?;
    let layout = match v.get(LAYOUT_KEY) {
        Some(l @ Value::Obj(_)) => l,
        Some(_) => return Err((E::NoLayout, say(format!("{path}'s \"{LAYOUT_KEY}\" isn't a bko-holt dump document: it carries no disk layout")))),
        None => return Err((E::NoLayout, say(format!("{path} carries no disk layout: it has no \"{LAYOUT_KEY}\", where the backup tool puts the bko-holt dump")))),
    };
    // Its own check: what load makes of a layout before it reads one.
    if let Err(e) = crate::load::layout::Layout::from_json(layout.clone()) {
        return Err((E::LayoutRefused, say(format!("{path}'s {LAYOUT_KEY} isn't a layout bko-holt load takes: {e}"))));
    }
    // A dump that kept LUKS2 headers (an Ubuntu Core's encryption, or dump
    // --keep-luks-header) needs their files, which the backup holds and
    // `bko-holt luks-headers` fetches once the repository's password is in
    // (holt-json.md's open question 12): the document counts them, and a
    // list that couldn't be fetched is refused now, not by the first plan.
    let headers = match crate::luksfetch::entries(layout) {
        Ok(l) => l.len(),
        Err(e) => return Err((E::LayoutRefused, say(format!("{path}'s {LAYOUT_KEY} kept LUKS2 headers bko-holt can't fetch from the backup: {e}")))),
    };
    let text = layout.pretty();
    if let Err(e) = crate::recoverycmd::write_new(out, text.as_bytes()) {
        // O_EXCL: a file there now isn't this one's, and is left as it is.
        let left = if e.kind() == std::io::ErrorKind::AlreadyExists { "something is there now, left as it is" } else { "nothing left there" };
        return Err((E::CopyFailed, say(format!("{}: {e} ({left})", out.display()))));
    }
    Ok(jsonout::header("bko-holt-stick-copy")
        .set("install_id", row.get_str("install_id"))
        .set("path", path.as_str())
        .set("out", out.display().to_string())
        .set("bytes", text.len())
        .set("sha256", crate::bytes::sha256_hex(text.as_bytes()))
        .set("system_sha256", crate::bytes::sha256_hex(&body))
        .set("luks_headers", headers)
        .build())
}

/// The rows of MACHINES backed up from this computer, whose DMI UUID is
/// HERE: by the UUID (case aside), when it names one computer.
fn this_computer(machines: &[Value], here: Option<&str>) -> Vec<usize> {
    let Some(here) = here.filter(|h| dmi_uuid_names_one(h)) else { return Vec::new() };
    machines.iter().enumerate().filter(|(_, m)| m.get_str("dmi_uuid").is_some_and(|u| u.eq_ignore_ascii_case(here))).map(|(k, _)| k).collect()
}

/// Whether U is a DMI system UUID that tells one computer from another: a
/// UUID's shape, not all one digit (zeros, Fs), nor one of
/// `DMI_UUID_PLACEHOLDERS`.
pub fn dmi_uuid_names_one(u: &str) -> bool {
    let l = u.to_ascii_lowercase();
    let hex: Vec<u8> = l.bytes().filter(|&b| b != b'-').collect();
    is_uuid(&l) && !hex.iter().all(|&b| b == hex[0]) && !DMI_UUID_PLACEHOLDERS.contains(&l.as_str())
}

/// Whether S has a UUID's shape: 8-4-4-4-12 hex digits (either case).
pub fn is_uuid(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 36 && b.iter().enumerate().all(|(i, &c)| if matches!(i, 8 | 13 | 18 | 23) { c == b'-' } else { c.is_ascii_hexdigit() })
}

/// What `read` fills in as it goes.
struct Doc {
    boot_disk: Value,
    partition: Option<u32>,
    label: Option<String>,
    dev: Option<String>,
    image: Value,
    stick: Value,
    machines: Vec<Value>,
    notes: Vec<Value>,
    /// The machine directory whose system.json bytes are kept (--copy).
    keep: Option<String>,
    kept: Vec<(String, Option<Vec<u8>>)>,
    /// With `keep`, the machine directories not read.
    others: usize,
}

fn note(code: &str, text: impl Into<String>) -> Value {
    Obj::new().set("code", code).set("text", text.into()).build()
}

fn read(sys: &Sys, fam: Option<Family>, d: &mut Doc) -> Result<(), NotOk> {
    let mpath = marker_path(fam);
    let text = match sys.read_bytes(mpath) {
        Some(b) if b.len() as u64 > SMALL_MAX => return Err(not_ok("damaged", format!("{mpath}, the image's marker, is over {SMALL_MAX} bytes"))),
        Some(b) => String::from_utf8(b).map_err(|_| not_ok("damaged", format!("{mpath}, the image's marker, isn't UTF-8")))?,
        None => return Err(not_ok("missing", format!("this system wasn't started from a BackOtter rescue stick: it has no {mpath}"))),
    };
    let m = parse_marker(&text).map_err(|e| not_ok("damaged", format!("{mpath}, the image's marker: {e}")))?;
    d.partition = Some(m.number);
    d.label = Some(m.label.clone());
    d.image = Obj::new().set("image_id", m.image_id.as_str()).set("platform", m.platform.clone()).set("built", m.built.clone()).set("system_schemas", m.system_schemas.clone()).set("dump_schemas", m.dump_schemas.clone()).build();
    if !sys.privileged {
        return Err(not_ok("unreadable", "the config partition is read off the stick's device: run bko-holt stick as root"));
    }
    let loc = match fam {
        None => locate_linux(sys, &m)?,
        Some(Family::Illumos) => locate_by_marker(sys, &m)?,
        Some(f) => locate_by_root(sys, f, &m)?,
    };
    d.boot_disk = Obj::new().set("node", loc.node.as_str()).set("found_by", loc.found_by).build();
    d.dev = loc.dev.clone();
    let src = crate::other::open(sys, &loc.disk).ok_or_else(|| not_ok("unreadable", format!("{} can't be opened for reading", loc.disk.path)))?;
    let part = OffsetSource { inner: &src, start: loc.start, len: loc.size };
    let want = m.sectors.saturating_mul(512);
    if loc.size != want {
        return Err(not_ok("damaged", format!("partition {} of {} is {} bytes, and the image made it {want}: the stick was repartitioned after it was written", m.number, loc.node, loc.size)));
    }
    let vol = Volume::open(&part).map_err(|e| not_ok("damaged", format!("partition {} of {} isn't a FAT file system this can read ({e}): reformatted, or damaged", m.number, loc.node)))?;
    check_identity(&vol, &m).map_err(|e| not_ok("damaged", format!("partition {} of {}: {e}: it was reformatted after the stick was written", m.number, loc.node)))?;
    read_config(&vol, &m, d)
}

/// Whether VOL is the partition the image made: its volume serial and label.
fn check_identity(vol: &Volume, m: &Marker) -> Result<(), String> {
    let serial = vol.serial_text();
    if !serial.as_deref().is_some_and(|s| s.eq_ignore_ascii_case(&m.fat_serial)) {
        return Err(format!("its FAT serial is {}, not the image's {}", serial.as_deref().unwrap_or("missing"), m.fat_serial));
    }
    let labels = [vol.boot_label.clone(), vol.dir_label()];
    if !labels.iter().flatten().any(|l| l.eq_ignore_ascii_case(&m.label)) {
        return Err(format!("its label is {}, not {}", labels.iter().flatten().next().map_or("missing", String::as_str), m.label));
    }
    Ok(())
}

/// stick.json, the note on settings.toml, and every machine.
fn read_config(vol: &Volume, m: &Marker, d: &mut Doc) -> Result<(), NotOk> {
    let damaged = |e: String| not_ok("damaged", e);
    let dir = vol.find(DIR).map_err(|e| damaged(format!("{DIR}/ can't be read: {e}")))?.filter(|e| e.dir).ok_or_else(|| damaged(format!("the config partition has no {DIR}/ directory")))?;
    let entries = vol.read_dir(Some(&dir)).map_err(|e| damaged(format!("{DIR}/ can't be read: {e}")))?;
    let find = |name: &str| entries.iter().find(|e| e.name.eq_ignore_ascii_case(name));
    let sj = find("stick.json").filter(|e| !e.dir).ok_or_else(|| damaged(format!("{DIR}/stick.json is missing")))?;
    let text = vol.read_file(sj, SMALL_MAX).map_err(|e| damaged(format!("{DIR}/stick.json can't be read: {e}")))?;
    let v = std::str::from_utf8(&text).ok().and_then(|t| crate::json::parse(t).ok()).ok_or_else(|| damaged(format!("{DIR}/stick.json isn't JSON")))?;
    if v.get_str("format") != Some("backotter-stick") {
        return Err(damaged(format!("{DIR}/stick.json isn't a backotter-stick document")));
    }
    match v.get_str("image_id") {
        Some(id) if id == m.image_id => {}
        id => return Err(damaged(format!("{DIR}/stick.json is for image {}, and this image is {}", id.unwrap_or("(none)"), m.image_id))),
    }
    // The name is settings.toml's, which isn't read yet.
    d.stick = Obj::new().set("stick_id", v.get_str("stick_id")).set("name", Value::Null).build();
    if find("settings.toml").is_some() {
        d.notes.push(note(
            "settings_ignored",
            format!("{DIR}/settings.toml isn't read yet (bko-holt has no TOML reader: holt-json.md, open question 4): its name and repository_url overrides are ignored"),
        ));
    }
    let Some(mdir) = find("machines").filter(|e| e.dir) else {
        return Ok(());
    };
    let mut ids: Vec<&Entry> = Vec::new();
    let list = vol.read_dir(Some(mdir)).map_err(|e| damaged(format!("{MACHINES}/ can't be read: {e}")))?;
    // A machine's directory is its install ID, a UUID (stick-config.md's
    // system.json): any other name is no machine, and never goes into a
    // path or an install_id (`../../etc`).
    let (mut kids, others): (Vec<&Entry>, Vec<&Entry>) = list.iter().filter(|e| e.dir).partition(|e| is_uuid(&e.name));
    if !others.is_empty() {
        d.notes.push(note("machines_not_install_ids", format!("{} directories in {MACHINES}/ aren't named by an install ID (a UUID) and are left out", others.len())));
    }
    kids.sort_by(|a, b| a.name.cmp(&b.name));
    if kids.len() > MACHINES_MAX {
        d.notes.push(note("machines_over_limit", format!("{MACHINES}/ has {} machines: the first {MACHINES_MAX} by name are listed", kids.len())));
    }
    ids.extend(kids.into_iter().take(MACHINES_MAX));
    for e in ids {
        match d.keep.as_deref() {
            Some(k) if !k.eq_ignore_ascii_case(&e.name) => d.others += 1,
            Some(_) => {
                let (row, body) = machine(vol, e, m);
                d.kept.push((e.name.clone(), body));
                d.machines.push(row);
            }
            None => d.machines.push(machine(vol, e, m).0),
        }
    }
    Ok(())
}

/// One machine's entry, from its directory E, and the system.json bytes it
/// was judged by, when they could be read.
fn machine(vol: &Volume, e: &Entry, m: &Marker) -> (Value, Option<Vec<u8>>) {
    let id = e.name.as_str();
    let path = format!("{MACHINES}/{id}/system.json");
    let o = Obj::new().set("install_id", id).set("path", path.as_str());
    let files = vol.read_dir(Some(e));
    let find = |name: &str| files.as_ref().ok().and_then(|f| f.iter().find(|x| !x.dir && x.name.eq_ignore_ascii_case(name)).cloned());
    let body = match (&files, find("system.json")) {
        (Err(err), _) => Err(format!("{MACHINES}/{id}/ can't be read: {err}")),
        (Ok(_), None) => Err(format!("{path} is missing")),
        (Ok(_), Some(f)) => vol.read_file(&f, SYSTEM_MAX).map_err(|err| format!("{path} can't be read: {err}")),
    };
    let body = match body {
        Ok(b) => b,
        Err(why) => return (fields(o, "unreadable", Some(why), None, m), None),
    };
    let row = judge(vol, o, id, &path, &body, find("system.json.sha256"), m);
    (row, Some(body))
}

/// The row of machine ID, whose system.json at PATH is BODY and whose
/// .sha256 is SUM.
fn judge(vol: &Volume, o: Obj, id: &str, path: &str, body: &[u8], sum: Option<Entry>, m: &Marker) -> Value {
    let (state, why) = match sum.map(|f| vol.read_file(&f, SMALL_MAX)) {
        None => ("checksum_missing", Some(format!("{path}.sha256 is missing"))),
        Some(Err(err)) => ("checksum_missing", Some(format!("{path}.sha256 can't be read: {err}"))),
        Some(Ok(sum)) => match checksum_ok(&sum, body) {
            Ok(true) => ("ok", None),
            Ok(false) => ("checksum_bad", Some(format!("{path} doesn't match its .sha256: damaged, or edited by hand"))),
            Err(err) => ("checksum_bad", Some(format!("{path}.sha256 {err}"))),
        },
    };
    let v = match std::str::from_utf8(body).ok().map(crate::json::parse) {
        Some(Ok(v)) => v,
        _ if state != "ok" => return fields(o, state, why, None, m),
        _ => return fields(o, "unreadable", Some(format!("{path} isn't JSON")), None, m),
    };
    if v.get_str("format") != Some("backotter-system") {
        return fields(o, if state == "ok" { "unreadable" } else { state }, why.or(Some(format!("{path} isn't a backotter-system document"))), None, m);
    }
    match v.str_at(&["machine", "install_id"]) {
        Some(x) if x == id => {}
        // Named only when it is one: a long or odd string stays out.
        Some(x) if is_uuid(x) => return fields(o, "unreadable", Some(format!("{path} names install ID {x}, not its directory's")), None, m),
        Some(_) => return fields(o, "unreadable", Some(format!("{path} names an install ID that isn't a UUID, not its directory's")), None, m),
        None => return fields(o, "unreadable", Some(format!("{path} names no install ID (machine.install_id)")), None, m),
    }
    fields(o, state, why, Some(&v), m)
}

/// The machine's fields after `path`: STATE (made `schema_unknown` for a
/// schema this stick or bko-holt can't read), and what system.json V says.
fn fields(o: Obj, state: &str, why: Option<String>, v: Option<&Value>, m: &Marker) -> Value {
    let schema = v.and_then(|v| v.get_u64("schema"));
    let known = schema.is_some_and(|s| SYSTEM_SCHEMAS.contains(&s) && m.system_schemas.contains(&s));
    let (mut state, mut why) = (state.to_string(), why);
    if state == "ok" && !known {
        state = "schema_unknown".into();
        why = Some(match schema {
            Some(s) => format!("its schema is {s}; this stick reads {}", schemas_text(m)),
            None => "it gives no schema".into(),
        });
    }
    let text = |p: &[&str]| v.and_then(|v| v.str_at(p)).map(str::to_string);
    let mut long: Option<String> = None;
    let mut s = |name: &str, p: &[&str]| -> Value {
        match text(p) {
            Some(t) if t.len() > FIELD_MAX => {
                long.get_or_insert_with(|| name.to_string());
                Value::Null
            }
            Some(t) => Value::from(t),
            None => Value::Null,
        }
    };
    let hostname = s("hostname", &["machine", "hostname"]);
    let os = Obj::new().set("id", s("os.id", &["os", "id"])).set("version_id", s("os.version_id", &["os", "version_id"])).build();
    let written = s("written", &["written"]);
    let dmi = s("dmi_uuid", &["machine", "dmi", "system_uuid"]);
    let repo = v.and_then(|v| v.at(&["backup", "repository"])).map(|_| Obj::new().set("kind", s("repository.kind", &["backup", "repository", "kind"])).set("url", s("repository.url", &["backup", "repository", "url"])).build());
    let repo_id = s("repository_id", &["backup", "repository_id"]);
    let hint = s(HINT_KEY, &[HINT_KEY]);
    let mut big: Option<&str> = None;
    let section = |k: &'static str, big: &mut Option<&str>| -> Value {
        match v.and_then(|v| v.get(k)) {
            Some(x @ Value::Obj(_)) if x.compact().len() <= SECTION_MAX => x.clone(),
            Some(Value::Obj(_)) => {
                big.get_or_insert(k);
                Value::Null
            }
            _ => Value::Null,
        }
    };
    let keyboard = section("keyboard", &mut big);
    let time = section("time", &mut big);
    if state == "ok" || state == "schema_unknown" {
        if let Some(f) = long {
            state = "unreadable".into();
            why = Some(format!("its {f} is longer than {FIELD_MAX} bytes"));
        } else if let Some(k) = big {
            state = "unreadable".into();
            why = Some(format!("its {k} section is bigger than {SECTION_MAX} bytes"));
        }
    }
    o.set("state", state)
        .set("why", why)
        .set("schema", schema)
        .set("known_schema", known)
        .set("hostname", hostname)
        .set("os", os)
        .set("written", written)
        .set("sequence", v.and_then(|v| v.get_u64("sequence")))
        .set("dmi_uuid", dmi)
        .set("this_computer", false)
        .set("repository", repo.unwrap_or(Value::Null))
        .set("repository_id", repo_id)
        .set("keyboard", keyboard)
        .set("time", time)
        .set(HINT_KEY, hint)
        .build()
}

/// A warning of the write-time check, stick-config.md's "Is this stick good
/// enough?": the backup tool still writes system.json, and says TEXT.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StickWarning {
    /// `system_schema_unread`, `dump_schema_unread` or `stick_room_short`.
    pub code: &'static str,
    pub text: String,
}

/// The schema half of the write-time check, for the backup tool before it
/// writes SYSTEM (a `backotter-system` document) to the stick whose marker
/// is M: SYSTEM's schema must be in the marker's `system_schemas`, and its
/// `layout`'s (the dump's) schema in the marker's `dump_schemas`
/// (holt-json.md's open question 11), or that stick's restore refuses them.
/// A SYSTEM with no `layout`, or one that isn't an object, has no dump to
/// check.  Only the schema is checked, not the dump's `format`.  The backup tool isn't
/// built: this is the check it calls, here where the marker is read.
pub fn schema_warnings(m: &Marker, system: &Value) -> Vec<StickWarning> {
    let list = |l: &[u64]| if l.is_empty() { "none".to_string() } else { l.iter().map(u64::to_string).collect::<Vec<_>>().join(", ") };
    let mut w = Vec::new();
    let schema = system.get_u64("schema");
    if !schema.is_some_and(|s| m.system_schemas.contains(&s)) {
        let is = schema.map_or("has no schema".to_string(), |s| format!("is schema {s}"));
        w.push(StickWarning { code: "system_schema_unread", text: format!("this stick's image reads system.json schema {}, and this machine's system.json {is}: the stick can't restore it; write a newer image to the stick", list(&m.system_schemas)) });
    }
    // Only an object is a dump, as for stick --copy: a `layout` that isn't
    // one is copy's no_layout, not a schema this check can name.
    if let Some(layout @ Value::Obj(_)) = system.get(LAYOUT_KEY) {
        let read = m.dump_schemas_read();
        let is = layout.get_u64("schema");
        if !is.is_some_and(|s| read.contains(&s)) {
            let is = is.map_or("has no schema".to_string(), |s| format!("is dump schema {s}"));
            let from = if m.dump_schemas.is_none() { " (its marker is from before images named them)" } else { "" };
            w.push(StickWarning { code: "dump_schema_unread", text: format!("this stick's image reads dump schema {}{from}, and this machine's layout {is}: the stick's restore would refuse it (layout_refused); write a newer image to the stick", list(read)) });
        }
    }
    w
}

/// Every image's config partition since 2026-11-26, in bytes: marker.py's
/// CONFIG_MIB (holt-json.md's open question 13).  A smaller one is an
/// older image's (the BSDs', illumos' and Haiku's were 32 MiB).
pub const CONFIG_BYTES: u64 = 256 << 20;

/// A restore log on the stick, at most, and how many a machine keeps:
/// bko-rescue's LOG_GZ_MAX and LOGS_KEPT (rescue/src/app/restore.rs;
/// stick-config.md, "Restore logs").  bko-rescue writes the new log before
/// it removes the oldest, so a machine's logs need room for one more.
pub const LOG_MAX: u64 = 2 << 20;
pub const LOGS_KEPT: u64 = 10;

/// The config partition as the backup tool finds it before it writes one
/// machine's system.json (stick-config.md, "How it writes"): mounted, with
/// no `system.json.new` left in that machine's folder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Room {
    /// Bytes free: statvfs's `f_bavail` times `f_frsize`.
    pub free: u64,
    /// The cluster, statvfs's `f_frsize`: each file takes whole clusters.
    pub cluster: u64,
    /// The machine's system.json and system.prev.json now, each its size in
    /// bytes, or None when it isn't there.
    pub current: Option<u64>,
    pub prev: Option<u64>,
    /// The size of each file in the machine's `restore-logs/` now.
    pub logs: Vec<u64>,
}

/// Why the backup tool doesn't write system.json to the stick at all.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StickRefusal {
    /// `system_too_big` or `stick_full`.
    pub code: &'static str,
    pub text: String,
}

/// The room half of the write-time check (holt-json.md's open question
/// 13), for the backup tool before it writes a system.json of SYSTEM_LEN
/// bytes to the stick whose marker is M, its config partition as R says.
/// It refuses (`system_too_big`) a system.json over SYSTEM_MAX, which a
/// stick's bko-holt won't read, and (`stick_full`) one the partition can't
/// take: the write itself would fail.  It warns (`stick_room_short`) when,
/// once it is written, the partition can't also hold the machine's restore
/// logs: LOGS_KEPT of LOG_MAX, plus the one being written, less what its
/// logs take now.  The write follows the steps of "How it writes": the new
/// file is written while the old two stand, then system.json becomes
/// system.prev.json (freeing the old one), then the new `.sha256` is
/// written (each `.sha256` taken as one cluster).  The backup tool isn't
/// built: this is the check it calls, beside `schema_warnings`.
pub fn room_check(m: &Marker, system_len: u64, r: &Room) -> Result<Vec<StickWarning>, StickRefusal> {
    let mib = |n: u128| format!("{:.1} MiB", n as f64 / (1u64 << 20) as f64);
    let image = if u128::from(m.sectors) * 512 < u128::from(CONFIG_BYTES) {
        format!(", or write it with an image from 2026-11-26 or later (its config partition is {}, theirs 256 MiB)", mib(u128::from(m.sectors) * 512))
    } else {
        String::new()
    };
    if system_len > SYSTEM_MAX {
        return Err(StickRefusal { code: "system_too_big", text: format!("this machine's system.json is {}, over the {} a stick's bko-holt reads: it isn't written to the stick (the backup keeps its copy)", mib(system_len.into()), mib(SYSTEM_MAX.into())) });
    }
    let cl = u128::from(r.cluster.max(1));
    let up = |n: u64| u128::from(n).div_ceil(cl) * cl;
    let free = u128::from(r.free);
    let new = up(system_len);
    let sum = cl;
    // system.json becomes system.prev.json: the old one and its .sha256 go.
    let freed = match (r.current, r.prev) {
        (Some(_), Some(p)) => up(p) + sum,
        _ => 0,
    };
    if free < new || free + freed < new + sum {
        return Err(StickRefusal { code: "stick_full", text: format!("this machine's system.json needs {} and the stick's config partition has {} free: nothing is written, and the stick keeps the copy it had. Remove other machines' folders from the stick{image}", mib(new + sum), mib(free + freed)) });
    }
    let after = free + freed - new - sum;
    let logs: u128 = r.logs.iter().map(|&l| up(l)).sum();
    let need = (u128::from(LOGS_KEPT) + 1) * up(LOG_MAX);
    let mut w = Vec::new();
    if after + logs < need {
        w.push(StickWarning { code: "stick_room_short", text: format!("once this machine's system.json is written, the stick's config partition has {} free, and its restore logs may need {}: a restore may not keep its log on the stick. Remove other machines' folders from the stick{image}", mib(after), mib(need - logs)) });
    }
    Ok(w)
}

fn schemas_text(m: &Marker) -> String {
    let both: Vec<String> = SYSTEM_SCHEMAS.iter().filter(|s| m.system_schemas.contains(s)).map(u64::to_string).collect();
    if both.is_empty() { "none".into() } else { both.join(", ") }
}

/// Whether BODY's SHA-256 is the one SUM (`sha256sum`'s line:
/// `HEX  system.json`, or `HEX *system.json`) gives.  An error says why SUM
/// isn't such a line.
pub fn checksum_ok(sum: &[u8], body: &[u8]) -> Result<bool, String> {
    let t = std::str::from_utf8(sum).map_err(|_| "isn't text".to_string())?;
    let line = t.trim_start_matches('\u{feff}').lines().next().unwrap_or("").trim_end_matches('\r');
    let mut w = line.splitn(2, char::is_whitespace);
    let hex = w.next().unwrap_or("");
    if hex.len() != 64 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("isn't sha256sum's line (64 hex digits, then the file's name)".into());
    }
    let name = w.next().map(|n| n.trim_start().trim_start_matches('*').trim()).unwrap_or("");
    if !name.is_empty() && name != "system.json" {
        return Err(format!("names {name}, not system.json"));
    }
    Ok(hex.eq_ignore_ascii_case(&crate::bytes::sha256_hex(body)))
}

/// This computer's DMI system UUID: Linux's, from sysfs; none elsewhere
/// yet (as the plan document's `here.dmi_uuid`).
fn dmi_uuid_here(sys: &Sys, fam: Option<Family>) -> Option<String> {
    if fam.is_some() {
        return None;
    }
    sys.read_trim("/sys/class/dmi/id/product_uuid").filter(|u| !u.is_empty())
}

/// Linux: the disk casper mounted at /cdrom, and its partition N, as the
/// kernel numbers them (stick/linux/config-checks.sh does the same).
fn locate_linux(sys: &Sys, m: &Marker) -> Result<Located, NotOk> {
    let missing = |why: String| not_ok("missing", why);
    let mounts = crate::mounts::parse_mountinfo(&sys.read("/proc/self/mountinfo").unwrap_or_default());
    let src = mounts.iter().find(|x| x.target == "/cdrom").map(|x| x.source.clone()).ok_or_else(|| missing("nothing is mounted on /cdrom: this system wasn't started from a BackOtter rescue stick".into()))?;
    let name = src.strip_prefix("/dev/").unwrap_or(&src).to_string();
    let disk = if sys.is_dir(&format!("/sys/block/{name}")) {
        name.clone()
    } else {
        sys.list("/sys/block").into_iter().find(|d| !name.is_empty() && !name.contains('/') && sys.is_dir(&format!("/sys/block/{d}/{name}"))).ok_or_else(|| missing(format!("/cdrom is {src}, which is on no disk this system lists")))?
    };
    let base = format!("/sys/block/{disk}");
    let part = sys
        .list(&base)
        .into_iter()
        .find(|c| sys.read_u64(&format!("{base}/{c}/partition")) == Some(u64::from(m.number)))
        .ok_or_else(|| missing(format!("{disk} (where /cdrom is) has no partition {}: the image was copied by a tool that copies files (Rufus in ISO mode, Ventoy), not written raw, or it was started from a CD", m.number)))?;
    let sectors = |p: &str| sys.read_u64(&format!("{p}/size"));
    let (start, size) = match (sys.read_u64(&format!("{base}/{part}/start")), sectors(&format!("{base}/{part}"))) {
        (Some(a), Some(b)) => (a * 512, b * 512),
        _ => return Err(not_ok("unreadable", format!("{disk}'s partition {} has no start or size in sysfs", m.number))),
    };
    let disk_size = sectors(&base).unwrap_or(0) * 512;
    let sector = sys.read_u64(&format!("{base}/queue/logical_block_size")).unwrap_or(512);
    Ok(Located {
        node: disk.clone(),
        found_by: "cdrom_mount",
        disk: RawDisk { name: disk.clone(), path: format!("/dev/{disk}"), size: disk_size, sector, model: None, serial: None },
        start,
        size,
        dev: Some(format!("/dev/{part}")),
    })
}

/// Partition N of disk D through its table: offset and size in bytes.
fn table_extent(src: &dyn ByteSource, d: &RawDisk, n: u32) -> Option<(u64, u64, crate::parttable::Table)> {
    let t = crate::parttable::read_table(src, d.sector)?;
    let (_, start, sectors) = t.extents().into_iter().find(|(k, _, _)| *k == n)?;
    Some((start.checked_mul(d.sector)?, sectors.checked_mul(d.sector)?, t))
}

/// illumos: the disks whose partition N has the marker's size, label and
/// serial in its first sector, reading only that sector of each.
fn locate_by_marker(sys: &Sys, m: &Marker) -> Result<Located, NotOk> {
    let mut hits = Vec::new();
    for d in crate::other::load::raw_disks(sys, Family::Illumos) {
        let Some(src) = crate::other::open(sys, &d) else { continue };
        let Some((start, size, _)) = table_extent(&src, &d, m.number) else { continue };
        if size != m.sectors.saturating_mul(512) {
            continue;
        }
        let Some(b) = src.read_exact_at(start, 512) else { continue };
        // As check_identity: FAT12/16's or FAT32's record, case aside.
        let (serial, label) = crate::fatfs::boot_ids(&b);
        let serial = serial.map(|s| format!("{:04X}-{:04X}", s >> 16, s & 0xffff));
        if serial.is_some_and(|s| s.eq_ignore_ascii_case(&m.fat_serial)) && label.is_some_and(|l| l.eq_ignore_ascii_case(&m.label)) {
            let dev = format!("/dev/dsk/{}", crate::other::illumos::slice_name(&d.name, m.number));
            hits.push(Located { node: d.name.clone(), found_by: "marker_scan", disk: d, start, size, dev: Some(dev) });
        }
    }
    match hits.len() {
        0 => Err(not_ok("missing", format!("no disk has a partition {} of {} sectors labelled {}, serial {}: the stick's config partition is missing or was reformatted", m.number, m.sectors, m.label, m.fat_serial))),
        1 => Ok(hits.remove(0)),
        _ => Err(not_ok("two_sticks", format!("{} disks are sticks written from this image ({}): remove all but the one this system was started from", hits.len(), hits.iter().map(|h| h.node.as_str()).collect::<Vec<_>>().join(", ")))),
    }
}

/// The BSDs and Haiku: the disk the root file system (Haiku: /boot) is on,
/// and its partition N through its table.
fn locate_by_root(sys: &Sys, fam: Family, m: &Marker) -> Result<Located, NotOk> {
    let missing = |why: String| not_ok("missing", why);
    let disks = crate::other::load::raw_disks(sys, fam);
    let (mp, spec) = root_spec(sys, fam).ok_or_else(|| missing("can't tell which device the root file system is on".into()))?;
    let name = disk_of(sys, fam, &spec, &disks).ok_or_else(|| missing(format!("{mp} is on {spec}, which is on no disk this system lists")))?;
    let d = disks.iter().find(|d| d.name == name).cloned().ok_or_else(|| missing(format!("{mp} is on {name}, which this system doesn't list")))?;
    let src = crate::other::open(sys, &d).ok_or_else(|| not_ok("unreadable", format!("{} can't be opened for reading", d.path)))?;
    let (start, size, t) = table_extent(&src, &d, m.number).ok_or_else(|| missing(format!("{name} (where {mp} is) has no partition {}: the image was copied by a tool that copies files, not written raw", m.number)))?;
    let dev = part_dev(sys, fam, &d, m.number, start, &t);
    Ok(Located { node: name, found_by: if fam == Family::Haiku { "boot_mount" } else { "root_mount" }, disk: d, start, size, dev })
}

/// The mount point the boot disk is told by, and the device mounted there.
fn root_spec(sys: &Sys, fam: Family) -> Option<(&'static str, String)> {
    match fam {
        Family::Haiku => crate::other::haiku::parse_df(&sys.run("df", &[])?).into_iter().find(|x| x.0 == "/boot").map(|x| ("/boot", x.2)),
        Family::NetBsd => sys.run("sysctl", &["-n", "kern.root_device"]).map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).map(|s| ("/", format!("/dev/{s}"))),
        Family::OpenBsd => sys.run("mount", &[])?.lines().filter_map(crate::other::bsdlabel::parse_mount_line).find(|x| x.1 == "/").map(|x| ("/", x.0)),
        _ => crate::mounts::parse_fstab(&sys.run("mount", &["-p"])?).into_iter().find(|x| x.file == "/").map(|x| ("/", x.spec)),
    }
}

/// The whole disk (by DISKS' names) the device SPEC is on.
fn disk_of(sys: &Sys, fam: Family, spec: &str, disks: &[RawDisk]) -> Option<String> {
    let by_name = |n: &str| -> Option<String> {
        let n = n.trim_start_matches("/dev/");
        let n = n.strip_prefix('r').filter(|x| disks.iter().any(|d| x.starts_with(d.name.as_str()))).unwrap_or(n);
        disks.iter().filter(|d| inside(fam, n, &d.name)).max_by_key(|d| d.name.len()).map(|d| d.name.clone())
    };
    match fam {
        // /dev/disk/usb/0/0/2: the directory above it is the disk's.
        Family::Haiku => {
            let dir = spec.rsplit_once('/')?.0;
            let n = crate::other::haiku::name_of(dir);
            disks.iter().any(|d| d.name == n).then_some(n)
        }
        // A wedge (dk1): the disk dkctl says it's on.
        Family::NetBsd if spec.trim_start_matches("/dev/").starts_with("dk") => {
            let dk = spec.trim_start_matches("/dev/");
            disks.iter().find(|d| crate::other::labelled::parse_wedges(&sys.run("dkctl", &[&d.name, "listwedges"]).unwrap_or_default()).iter().any(|w| w.0 == dk)).map(|d| d.name.clone())
        }
        // A DUID (0123456789abcdef.a): the disk with it.
        Family::OpenBsd if !spec.starts_with('/') => {
            let duid = spec.split('.').next()?;
            disks.iter().find(|d| d.serial.as_deref() == Some(duid)).map(|d| d.name.clone())
        }
        // A GEOM label (/dev/ufs/BKOROOT): glabel's provider.
        Family::FreeBsd if by_name(spec).is_none() => {
            let l = spec.trim_start_matches("/dev/");
            let text = sys.run("glabel", &["status", "-s"])?;
            let prov = text.lines().find_map(|x| {
                let w: Vec<&str> = x.split_whitespace().collect();
                (w.len() >= 3 && w[0] == l).then(|| w[2].to_string())
            })?;
            by_name(&prov)
        }
        // A devfs alias (part-by-label, serno): its link, else the same
        // device number as one of the disks' slices.
        Family::DragonFly if by_name(spec).is_none() => {
            if let Some(t) = sys.readlink(spec) {
                let t = t.rsplit('/').next().unwrap_or(&t).to_string();
                if let Some(n) = by_name(&t) {
                    return Some(n);
                }
            }
            same_device(sys, spec, disks)
        }
        _ => by_name(spec),
    }
}

/// Whether device name N (no /dev/) is DISK or one of its partitions:
/// DISK then a slice or partition (s2, p3, s2a) or a disklabel letter.
fn inside(fam: Family, n: &str, disk: &str) -> bool {
    let Some(rest) = n.strip_prefix(disk) else { return false };
    if rest.is_empty() {
        return true;
    }
    let b = rest.as_bytes();
    match fam {
        Family::OpenBsd | Family::NetBsd => rest.len() == 1 && b[0].is_ascii_lowercase(),
        _ => (b[0] == b's' || b[0] == b'p') && b.len() > 1 && b[1].is_ascii_digit(),
    }
}

/// The disk one of whose /dev nodes has SPEC's device number.
fn same_device(sys: &Sys, spec: &str, disks: &[RawDisk]) -> Option<String> {
    use std::os::unix::fs::MetadataExt;
    let want = std::fs::metadata(sys.p(spec)).ok()?.rdev();
    if want == 0 {
        return None;
    }
    for d in disks {
        for e in sys.list("/dev") {
            if e.starts_with(&d.name) && std::fs::metadata(sys.p(&format!("/dev/{e}"))).is_ok_and(|m| m.rdev() == want) {
                return Some(d.name.clone());
            }
        }
    }
    None
}

/// Partition N's own device, as FAM names it, when it names one.
fn part_dev(sys: &Sys, fam: Family, d: &RawDisk, n: u32, start: u64, t: &crate::parttable::Table) -> Option<String> {
    let sector = d.sector.max(1);
    match fam {
        Family::FreeBsd => Some(format!("/dev/{}", crate::other::freebsd::part_name(&d.name, n, t))),
        Family::DragonFly => Some(format!("/dev/{}s{n}", d.name)),
        Family::Haiku => Some(crate::other::haiku::part_path(&d.name, n)),
        Family::Illumos => Some(format!("/dev/dsk/{}", crate::other::illumos::slice_name(&d.name, n))),
        Family::NetBsd => crate::other::labelled::parse_wedges(&sys.run("dkctl", &[&d.name, "listwedges"]).unwrap_or_default()).into_iter().find(|w| w.2 * sector == start).map(|w| format!("/dev/{}", w.0)),
        // The disklabel's partition at the MBR slot's place.
        Family::OpenBsd => {
            let l = crate::other::bsdlabel::parse(&sys.run_any("disklabel", &[&d.name])?);
            let bps = l.bytes_per_sector.max(512);
            l.parts.iter().find(|p| p.offset * bps == start && p.fstype.eq_ignore_ascii_case("msdos")).map(|p| format!("/dev/{}{}", d.name, p.letter))
        }
        Family::Redox | Family::Genode => None,
    }
}

/// Sticks for tests: config partitions and Linux trees booted from them.
/// Not cfg(test): tests/json_cli.rs runs `bko-holt stick` on them too.
#[doc(hidden)]
pub mod sample {
    use crate::fatfs::testimg::{dir, fat16, file, Node};
    use crate::testutil::Tree;

    pub const IMAGE_ID: &str = "6f1c2c1e-5b0e-4a51-9d8e-2a3c8f4e7b10";
    pub const LOVELACE: &str = "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90";
    pub const BABBAGE: &str = "1c2d3e4f-5a6b-4c7d-8e9f-a0b1c2d3e4f5";

    /// The marker as marker.py writes it, for a config partition N of
    /// 32768 sectors (16 MiB) with serial 3F2A-9C71.
    pub fn marker(n: u32) -> String {
        format!(
            "{{\n  \"format\": \"backotter-image\",\n  \"schema\": 1,\n  \"image_id\": \"{IMAGE_ID}\",\n  \"built\": \"2026-11-20T10:00:00Z\",\n  \"platform\": \"linux\",\n  \"system_schemas\": [1],\n  \"dump_schemas\": [1],\n  \"config_partition\": {{\"number\": {n}, \"sectors\": 32768, \"label\": \"BACKOTTER\", \"fat_serial\": \"3F2A-9C71\"}}\n}}\n"
        )
    }

    pub fn system(install_id: &str, hostname: &str, dmi: &str, extra: &str) -> String {
        format!(
            "{{\"format\": \"backotter-system\", \"schema\": 1, \"sequence\": 42, \"written\": \"2026-11-22T01:00:11Z\", \"written_by\": \"backotter 0.1.0\", \"machine\": {{\"install_id\": \"{install_id}\", \"hostname\": \"{hostname}\", \"dmi\": {{\"system_uuid\": \"{dmi}\"}}}}, \"os\": {{\"id\": \"debian\", \"version_id\": \"13\"}}, \"backup\": {{\"engine\": \"restic\", \"repository\": {{\"kind\": \"sftp\", \"url\": \"sftp://nas.local/backotter/{hostname}\"}}, \"repository_id\": \"a3f9\", \"credential\": {{\"kind\": \"prompt\"}}}}{extra}}}"
        )
    }

    pub fn sum(body: &str) -> String {
        format!("{}  system.json\n", crate::bytes::sha256_hex(body.as_bytes()))
    }

    pub fn machine_dir(id: &str, body: &str, sha: Option<&str>) -> Node {
        let mut v = vec![file("system.json", body)];
        if let Some(s) = sha {
            v.push(file("system.json.sha256", s));
        }
        dir(id, v)
    }

    pub fn stick_json() -> String {
        format!("{{\n  \"format\": \"backotter-stick\",\n  \"schema\": 1,\n  \"image_id\": \"{IMAGE_ID}\",\n  \"stick_id\": \"9d8c7b6a-0000-4000-8000-000000000001\"\n}}\n")
    }

    /// A config partition image holding MACHINES.
    pub fn config(machines: Vec<Node>) -> Vec<u8> {
        fat16(32768, 0x3f2a_9c71, "BACKOTTER", &[dir("backotter", vec![file("stick.json", stick_json()), file("settings.toml", "format = \"backotter-settings\"\nschema = 1\n"), dir("machines", machines)]), file("README.txt", "BackOtter\n")])
    }

    /// A stick disk sdc with a GPT whose partition 3 is CONFIG, in a tree
    /// where casper mounted sdc1 at /cdrom; this computer's DMI UUID HERE.
    pub fn linux_tree(config: &[u8], here: Option<&str>) -> Tree {
        let t = Tree::new("stick");
        let start = 4096u64;
        let n = (config.len() / 512) as u64;
        let total = start + n + 2048;
        let mut img = vec![0u8; (total * 512) as usize];
        let off = (start * 512) as usize;
        img[off..off + config.len()].copy_from_slice(config);
        t.disk("sdc", "8:32", &img, 512)
            .partition("sdc", "sdc1", "8:33", 1, 64, 2048, &img)
            .partition("sdc", "sdc2", "8:34", 2, 2112, 1024, &img)
            .partition("sdc", "sdc3", "8:35", 3, start, n, &img)
            .disk("nvme0n1", "259:0", &vec![0u8; 1 << 20], 512)
            .file("/proc/self/mountinfo", "29 1 7:0 / / ro - squashfs /dev/loop0 ro\n30 29 8:33 / /cdrom ro,noatime - iso9660 /dev/sdc1 ro\n")
            .file("/cdrom/.disk/backotter.json", marker(3));
        if let Some(h) = here {
            t.file("/sys/class/dmi/id/product_uuid", format!("{h}\n"));
        }
        t
    }

    /// A dump of one Debian 13 machine (one BIOS + MBR disk, one ext4), as
    /// system.json carries it in `layout` (holt-json.md, open question 8).
    pub const DUMP: &str = r#"{
  "format": "bko-holt", "schema": 1, "privileged": true,
  "host": {"hostname": "t"}, "os": {"id": "debian", "version_id": "13"},
  "storage": {
    "disks": [{"name": "vda", "kind": "disk", "size_bytes": 1073741824, "logical_sector_size": 512,
      "partition_table": {"type": "dos", "disk_signature": "0x00c0ffee"},
      "partitions": [{"name": "vda1", "number": 1, "start_lba": 2048, "sectors": 2095104, "type": "0x83", "bootable": true,
        "content": {"type": "ext4", "uuid": "11111111-2222-4333-8444-555555555555", "features": ["has_journal", "extent"]}}],
      "content": null, "boot_code": {"kind": "grub2"}}],
    "fstab": [{"spec": "UUID=11111111-2222-4333-8444-555555555555", "file": "/", "type": "ext4", "options": "errors=remount-ro", "resolves_to": "vda1"}]
  },
  "boot": {"loader": {"name": "grub", "firmware": "bios"}}
}"#;

    /// system.json's tail carrying LAYOUT as its `layout`.
    pub fn with_layout(layout: &str) -> String {
        format!(", \"layout\": {layout}")
    }

    /// A stick whose lovelace and babbage each carry a layout (babbage's
    /// host named babbage-host), with good checksums.
    pub fn two_with_layouts() -> Vec<Node> {
        let a = system(LOVELACE, "lovelace", "", &with_layout(DUMP));
        let b = system(BABBAGE, "babbage", "", &with_layout(&DUMP.replace("\"hostname\": \"t\"", "\"hostname\": \"babbage-host\"")));
        vec![machine_dir(LOVELACE, &a, Some(&sum(&a))), machine_dir(BABBAGE, &b, Some(&sum(&b)))]
    }

    pub fn three() -> Vec<Node> {
        let a = system(LOVELACE, "lovelace", "4C4C4544-0000-1111-2222-333344445555", ", \"password_hint\": \"the boat's name\"");
        let b = system(BABBAGE, "babbage", "11111111-2222-3333-4444-555555555555", "");
        let c = system("2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6", "hopper", "", "");
        vec![machine_dir(LOVELACE, &a, Some(&sum(&a))), machine_dir(BABBAGE, &b, Some(&sum(&b))), machine_dir("2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6", &c, Some(&sum(&c)))]
    }
}

#[cfg(test)]
mod tests {
    use super::sample::*;
    use super::*;
    use crate::fatfs::testimg::{dir, fat16, file};
    use crate::testutil::Tree;

    fn doc(t: &Tree) -> (Value, u8) {
        document(&Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), true), None)
    }

    #[test]
    fn reads_the_machines_off_the_boot_disk() {
        let t = linux_tree(&config(three()), Some("4c4c4544-0000-1111-2222-333344445555"));
        let (v, code) = doc(&t);
        assert_eq!(code, 0, "{}", v.pretty());
        assert_eq!(v.str_at(&["config", "state"]), Some("ok"));
        assert_eq!(v.u64_at(&["config", "partition"]), Some(3));
        assert_eq!(v.str_at(&["config", "dev"]), Some("/dev/sdc3"));
        assert_eq!(v.str_at(&["boot_disk", "node"]), Some("sdc"));
        assert_eq!(v.str_at(&["boot_disk", "found_by"]), Some("cdrom_mount"));
        assert_eq!(v.str_at(&["stick", "stick_id"]), Some("9d8c7b6a-0000-4000-8000-000000000001"));
        assert_eq!(v.str_at(&["image", "image_id"]), Some(IMAGE_ID));
        let ms = v.arr_at(&["machines"]);
        let names: Vec<&str> = ms.iter().filter_map(|m| m.get_str("hostname")).collect();
        assert_eq!(names, ["lovelace", "babbage", "hopper"]);
        assert!(ms.iter().all(|m| m.get_str("state") == Some("ok") && m.get_bool("known_schema") == Some(true)));
        assert_eq!(ms[0].get_bool("this_computer"), Some(true));
        assert_eq!(ms[1].get_bool("this_computer"), Some(false));
        assert_eq!(ms[0].get_str("password_hint"), Some("the boat's name"));
        assert!(ms[1].get("password_hint").is_some_and(Value::is_null));
        assert_eq!(ms[0].str_at(&["repository", "url"]), Some("sftp://nas.local/backotter/lovelace"));
        assert_eq!(ms[0].get_u64("sequence"), Some(42));
        assert_eq!(ms[0].get_str("path"), Some(format!("backotter/machines/{LOVELACE}/system.json").as_str()));
        // Not yet in system.json: null, never made up.
        assert!(ms[0].get("keyboard").is_some_and(Value::is_null) && ms[0].get("time").is_some_and(Value::is_null));
        let notes: Vec<&str> = v.arr_at(&["notes"]).iter().filter_map(|n| n.get_str("code")).collect();
        assert_eq!(notes, ["settings_ignored"]);
        assert_eq!(v.str_at(&["here", "dmi_uuid"]), Some("4c4c4544-0000-1111-2222-333344445555"));
        crate::testutil::golden_json("stick-json", "three", &v);
    }

    #[test]
    fn an_old_marker_names_no_dump_schemas() {
        // Every stick built before 2026-11-26: image.dump_schemas is null,
        // never made up as [1] or [] (holt-json.md's stick --json).
        let t = linux_tree(&config(three()), None);
        t.file("/cdrom/.disk/backotter.json", marker(3).replace("  \"dump_schemas\": [1],\n", ""));
        let (v, code) = doc(&t);
        assert_eq!(code, 0, "{}", v.pretty());
        assert!(v.at(&["image", "dump_schemas"]).is_some_and(Value::is_null), "{}", v.pretty());
        assert_eq!(v.at(&["image", "system_schemas"]).and_then(Value::as_array).map(Vec::len), Some(1));
        // A new one names its list.
        let (v, _) = doc(&linux_tree(&config(three()), None));
        let l: Vec<u64> = v.at(&["image", "dump_schemas"]).and_then(Value::as_array).unwrap().iter().filter_map(Value::as_u64).collect();
        assert_eq!(l, [1]);
    }

    #[test]
    fn a_machine_that_fails_its_checks_is_listed_with_why() {
        let good = system(LOVELACE, "lovelace", "", "");
        let edited = good.replace("lovelace\"", "lovelace2\"");
        let two = system(BABBAGE, "babbage", "", "").replace("\"schema\": 1", "\"schema\": 2");
        let other = system("ffffffff-0000-0000-0000-000000000000", "x", "", "");
        let c0 = system("c0000000-0000-0000-0000-000000000000", "c", "", "");
        let f0 = system("f0000000-0000-0000-0000-000000000000", "f", "", "");
        let ms = vec![
            machine_dir(LOVELACE, &edited, Some(&sum(&good))),
            machine_dir(BABBAGE, &two, Some(&sum(&two))),
            machine_dir("c0000000-0000-0000-0000-000000000000", &c0, None),
            machine_dir("d0000000-0000-0000-0000-000000000000", "not json", Some(&sum("not json"))),
            machine_dir("e0000000-0000-0000-0000-000000000000", &other, Some(&sum(&other))),
            machine_dir("f0000000-0000-0000-0000-000000000000", &f0, Some("cafe  system.json\n")),
        ];
        let t = linux_tree(&config(ms), None);
        let (v, code) = doc(&t);
        assert_eq!(code, 0);
        let st: Vec<(&str, &str)> = v.arr_at(&["machines"]).iter().map(|m| (m.get_str("install_id").unwrap(), m.get_str("state").unwrap())).collect();
        assert_eq!(
            st,
            [
                (LOVELACE, "checksum_bad"),
                (BABBAGE, "schema_unknown"),
                ("c0000000-0000-0000-0000-000000000000", "checksum_missing"),
                ("d0000000-0000-0000-0000-000000000000", "unreadable"),
                ("e0000000-0000-0000-0000-000000000000", "unreadable"),
                ("f0000000-0000-0000-0000-000000000000", "checksum_bad"),
            ]
        );
        let ms = v.arr_at(&["machines"]);
        // A damaged copy still says what it can: the name it was written with.
        assert_eq!(ms[0].get_str("hostname"), Some("lovelace2"));
        assert!(ms[0].get_str("why").unwrap().contains("doesn't match"));
        assert_eq!((ms[1].get_u64("schema"), ms[1].get_bool("known_schema")), (Some(2), Some(false)));
        assert!(ms[4].get_str("why").unwrap().contains("names install ID"));
        assert!(ms[5].get_str("why").unwrap().contains("64 hex digits"));
        assert!(ms.iter().all(|m| m.get_bool("this_computer") == Some(false)));
        assert!(v.get("here").unwrap().get("dmi_uuid").unwrap().is_null());
        crate::testutil::golden_json("stick-json", "failing", &v);
    }

    #[test]
    fn a_directory_or_install_id_that_is_no_uuid() {
        let good = system(LOVELACE, "lovelace", "", "");
        let none = system(BABBAGE, "babbage", "", "").replace(&format!("\"install_id\": \"{BABBAGE}\", "), "");
        let odd_id = "x".repeat(200_000);
        let odd = system(&odd_id, "hopper", "", "");
        let hopper = "2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6";
        let ms = vec![
            machine_dir("../../etc", &good, Some(&sum(&good))),
            machine_dir("notes", &good, Some(&sum(&good))),
            machine_dir(LOVELACE, &good, Some(&sum(&good))),
            machine_dir(BABBAGE, &none, Some(&sum(&none))),
            machine_dir(hopper, &odd, Some(&sum(&odd))),
        ];
        let t = linux_tree(&config(ms), None);
        let (v, code) = doc(&t);
        assert_eq!(code, 0, "{}", v.pretty());
        let ms = v.arr_at(&["machines"]);
        let st: Vec<(&str, &str)> = ms.iter().map(|m| (m.get_str("install_id").unwrap(), m.get_str("state").unwrap())).collect();
        assert_eq!(st, [(LOVELACE, "ok"), (BABBAGE, "unreadable"), (hopper, "unreadable")]);
        assert!(ms.iter().all(|m| !m.get_str("path").unwrap().contains("..")));
        assert!(ms[1].get_str("why").unwrap().contains("names no install ID"));
        let why = ms[2].get_str("why").unwrap();
        assert!(why.contains("isn't a UUID") && !why.contains("xxx"), "{why}");
        assert!(jsonout::line(&v).len() < 8192);
        let notes: Vec<&str> = v.arr_at(&["notes"]).iter().filter_map(|n| n.get_str("code")).collect();
        assert_eq!(notes, ["settings_ignored", "machines_not_install_ids"]);
        assert!(!jsonout::line(&v).contains("etc"));
        crate::testutil::golden_json("stick-json", "not-install-ids", &v);
    }

    #[test]
    fn this_computer_only_by_a_uuid_that_names_one() {
        let lv = "4c4c4544-0000-1111-2222-333344445555";
        let mk = |dmis: [&str; 3]| {
            let ids = [LOVELACE, BABBAGE, "2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6"];
            let names = ["lovelace", "babbage", "hopper"];
            config((0..3).map(|k| {
                let b = system(ids[k], names[k], dmis[k], "");
                machine_dir(ids[k], &b, Some(&sum(&b)))
            }).collect())
        };
        let mine = |v: &Value| v.arr_at(&["machines"]).iter().map(|m| m.get_bool("this_computer").unwrap()).collect::<Vec<_>>();
        let codes = |v: &Value| v.arr_at(&["notes"]).iter().filter_map(|n| n.get_str("code").map(str::to_string)).collect::<Vec<_>>();
        // One match: that row.
        let (v, _) = doc(&linux_tree(&mk([lv, "11111111-2222-3333-4444-555555555555", ""]), Some(&lv.to_uppercase())));
        assert_eq!(mine(&v), [true, false, false]);
        // Two: cloned, or reinstalled under a new install ID: neither, said so.
        let (v, _) = doc(&linux_tree(&mk([lv, lv, ""]), Some(lv)));
        assert_eq!(mine(&v), [false, false, false]);
        assert_eq!(codes(&v), ["settings_ignored", "this_computer_ambiguous"]);
        assert!(v.arr_at(&["notes"])[1].get_str("text").unwrap().contains("2 machines"));
        // Firmware's placeholders name no computer, here or on the stick.
        for p in ["00000000-0000-0000-0000-000000000000", "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF", "03000200-0400-0500-0006-000700080009", "Not Settable"] {
            let (v, _) = doc(&linux_tree(&mk([p, lv, ""]), Some(p)));
            assert_eq!(mine(&v), [false, false, false], "{p}");
            assert_eq!(codes(&v), ["settings_ignored"], "{p}");
            assert_eq!(v.str_at(&["here", "dmi_uuid"]), Some(p));
        }
        assert!(dmi_uuid_names_one(lv) && !dmi_uuid_names_one("") && !dmi_uuid_names_one("12345678-1234-5678-90AB-CDDEEFAABBCC"));
        assert!(is_uuid(LOVELACE) && is_uuid(&LOVELACE.to_uppercase()) && !is_uuid("../../etc") && !is_uuid(&LOVELACE.replace('-', "_")));
    }

    #[test]
    fn missing_reformatted_and_repartitioned() {
        // Not a stick at all: no marker.
        let t = Tree::new("stick");
        t.file("/proc/self/mountinfo", "");
        let (v, code) = doc(&t);
        assert_eq!((code, v.str_at(&["config", "state"])), (1, Some("missing")));
        assert!(v.str_at(&["config", "why"]).unwrap().contains("/cdrom/.disk/backotter.json"));
        assert!(v.get("boot_disk").unwrap().is_null() && v.get("stick").unwrap().is_null());
        crate::testutil::golden_json("stick-json", "missing", &v);
        // Copied by a file-copying tool: no partition 3.
        let t = linux_tree(&config(three()), None);
        std::fs::remove_dir_all(t.p("/sys/block/sdc/sdc3")).unwrap();
        let (v, code) = doc(&t);
        assert_eq!((code, v.str_at(&["config", "state"])), (1, Some("missing")));
        assert!(v.str_at(&["config", "why"]).unwrap().contains("written raw"));
        // Reformatted: another serial.
        let mut c = config(three());
        c[39] ^= 1;
        let (v, code) = doc(&linux_tree(&c, None));
        assert_eq!((code, v.str_at(&["config", "state"])), (1, Some("damaged")));
        assert!(v.str_at(&["config", "why"]).unwrap().contains("reformatted"), "{}", v.pretty());
        crate::testutil::golden_json("stick-json", "reformatted", &v);
        assert!(v.arr_at(&["machines"]).is_empty());
        // Not FAT any more.
        let (v, _) = doc(&linux_tree(&vec![0u8; 32768 * 512], None));
        assert_eq!(v.str_at(&["config", "state"]), Some("damaged"));
        // Repartitioned: partition 3 of another size.
        let t = linux_tree(&config(three()), None);
        t.file("/sys/block/sdc/sdc3/size", "30000\n");
        let (v, _) = doc(&t);
        assert_eq!(v.str_at(&["config", "state"]), Some("damaged"));
        assert!(v.str_at(&["config", "why"]).unwrap().contains("repartitioned"));
        // A stick.json from another image.
        let c = fat16(32768, 0x3f2a_9c71, "BACKOTTER", &[dir("backotter", vec![file("stick.json", stick_json().replace(IMAGE_ID, "00000000-0000-0000-0000-000000000000"))])]);
        let (v, _) = doc(&linux_tree(&c, None));
        assert_eq!(v.str_at(&["config", "state"]), Some("damaged"));
        assert!(v.str_at(&["config", "why"]).unwrap().contains("is for image"));
        // Not root: unreadable, said so.
        let t = linux_tree(&config(three()), None);
        let (v, code) = document(&Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), false), None);
        assert_eq!((code, v.str_at(&["config", "state"])), (1, Some("unreadable")));
    }

    #[test]
    fn no_machines_yet_and_fields_too_long() {
        let (v, code) = doc(&linux_tree(&config(vec![]), None));
        assert_eq!(code, 0);
        assert!(v.arr_at(&["machines"]).is_empty());
        let long = "h".repeat(FIELD_MAX + 1);
        let a = system(LOVELACE, &long, "", "");
        let big = format!(", \"time\": {{\"zone\": \"{}\"}}", "z".repeat(SECTION_MAX));
        let b = system(BABBAGE, "babbage", "", &big);
        let kb = ", \"keyboard\": {\"canonical\": {\"layout\": \"de\", \"variant\": \"nodeadkeys\"}}";
        let c = system("2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6", "hopper", "", kb);
        let (v, _) = doc(&linux_tree(&config(vec![machine_dir(LOVELACE, &a, Some(&sum(&a))), machine_dir(BABBAGE, &b, Some(&sum(&b))), machine_dir("2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6", &c, Some(&sum(&c)))]), None));
        let ms = v.arr_at(&["machines"]);
        assert_eq!(ms[0].get_str("state"), Some("unreadable"));
        assert!(ms[0].get("hostname").unwrap().is_null() && ms[0].get_str("why").unwrap().contains("hostname"));
        assert_eq!(ms[1].get_str("state"), Some("unreadable"));
        assert!(ms[1].get_str("why").unwrap().contains("time section"));
        assert_eq!(ms[2].get_str("state"), Some("ok"));
        assert_eq!(ms[2].str_at(&["keyboard", "canonical", "layout"]), Some("de"));
    }

    #[test]
    fn checksum_lines() {
        let h = crate::bytes::sha256_hex(b"x");
        assert_eq!(checksum_ok(format!("{h}  system.json\n").as_bytes(), b"x"), Ok(true));
        assert_eq!(checksum_ok(format!("{} *system.json\r\n", h.to_uppercase()).as_bytes(), b"x"), Ok(true));
        assert_eq!(checksum_ok(format!("{h}\n").as_bytes(), b"x"), Ok(true));
        assert_eq!(checksum_ok(format!("{h}  system.json\n").as_bytes(), b"y"), Ok(false));
        assert!(checksum_ok(format!("{h}  other.json\n").as_bytes(), b"x").unwrap_err().contains("other.json"));
        assert!(checksum_ok(b"", b"x").is_err());
    }

    #[test]
    fn write_time_schema_check() {
        let m = parse_marker(&marker(3)).unwrap();
        let sys = |extra: &str| crate::json::parse(&system(LOVELACE, "lovelace", "4c4c4544-0000", extra)).unwrap();
        let codes = |m: &Marker, v: &Value| schema_warnings(m, v).iter().map(|w| w.code).collect::<Vec<_>>();
        // Readable, with a layout and without one (no dump to check).
        assert!(schema_warnings(&m, &sys(", \"layout\": {\"format\": \"bko-holt\", \"schema\": 1}")).is_empty());
        assert!(schema_warnings(&m, &sys("")).is_empty());
        // A layout that isn't an object is no dump (stick --copy's no_layout), not a schema to name.
        assert!(schema_warnings(&m, &sys(", \"layout\": null")).is_empty());
        assert!(schema_warnings(&m, &sys(", \"layout\": \"x\"")).is_empty());
        // A dump schema the stick's bko-holt doesn't read: a warning, named.
        let w = schema_warnings(&m, &sys(", \"layout\": {\"format\": \"bko-holt\", \"schema\": 2}"));
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].code, "dump_schema_unread");
        assert_eq!(w[0].text, "this stick's image reads dump schema 1, and this machine's layout is dump schema 2: the stick's restore would refuse it (layout_refused); write a newer image to the stick");
        assert_eq!(codes(&m, &sys(", \"layout\": {\"format\": \"bko-holt\"}")), ["dump_schema_unread"]);
        assert!(schema_warnings(&m, &sys(", \"layout\": {\"schema\": \"1\"}"))[0].text.contains("layout has no schema"));
        // A newer stick that reads both.
        let both = parse_marker(&marker(3).replace("\"dump_schemas\": [1]", "\"dump_schemas\": [1, 2]")).unwrap();
        assert!(schema_warnings(&both, &sys(", \"layout\": {\"schema\": 2}")).is_empty());
        // A marker from before dump_schemas reads dump schema 1, and says so.
        let old = parse_marker(&marker(3).replace("  \"dump_schemas\": [1],\n", "")).unwrap();
        assert!(schema_warnings(&old, &sys(", \"layout\": {\"schema\": 1}")).is_empty());
        let w = schema_warnings(&old, &sys(", \"layout\": {\"schema\": 2}"));
        assert_eq!(w[0].text, "this stick's image reads dump schema 1 (its marker is from before images named them), and this machine's layout is dump schema 2: the stick's restore would refuse it (layout_refused); write a newer image to the stick");
        // system.json's own schema, against system_schemas, as before; both warn at once.
        let v2 = crate::json::parse(&system(LOVELACE, "lovelace", "4c4c4544-0000", ", \"layout\": {\"schema\": 2}").replacen("\"schema\": 1", "\"schema\": 2", 1)).unwrap();
        let w = schema_warnings(&m, &v2);
        assert_eq!(w.iter().map(|w| w.code).collect::<Vec<_>>(), ["system_schema_unread", "dump_schema_unread"]);
        assert_eq!(w[0].text, "this stick's image reads system.json schema 1, and this machine's system.json is schema 2: the stick can't restore it; write a newer image to the stick");
        let none = parse_marker(&marker(3).replace("\"system_schemas\": [1]", "\"system_schemas\": []")).unwrap();
        assert!(schema_warnings(&none, &sys(""))[0].text.starts_with("this stick's image reads system.json schema none,"));
    }

    #[test]
    fn write_time_room_check() {
        const MIB: u64 = 1 << 20;
        let big = parse_marker(&marker(3).replace("\"sectors\": 32768", "\"sectors\": 524288")).unwrap();
        let small = parse_marker(&marker(3)).unwrap();
        let room = |free: u64, current: Option<u64>, prev: Option<u64>, logs: Vec<u64>| Room { free, cluster: 4096, current, prev, logs };
        let codes = |r: Result<Vec<StickWarning>, StickRefusal>| match r {
            Ok(w) => w.iter().map(|w| w.code).collect::<Vec<_>>().join(","),
            Err(e) => format!("refused {}", e.code),
        };
        // A new stick with room: nothing to say.
        assert_eq!(room_check(&big, 3 * MIB, &room(250 * MIB, None, None, vec![])), Ok(vec![]));
        // SYSTEM_MAX is read; a byte more isn't, whatever the room.
        assert_eq!(codes(room_check(&big, SYSTEM_MAX, &room(250 * MIB, None, None, vec![]))), "");
        assert_eq!(codes(room_check(&big, SYSTEM_MAX + 1, &room(250 * MIB, None, None, vec![]))), "refused system_too_big");
        let e = room_check(&big, SYSTEM_MAX + MIB, &room(250 * MIB, None, None, vec![])).unwrap_err();
        assert_eq!(e.code, "system_too_big");
        assert_eq!(e.text, "this machine's system.json is 65.0 MiB, over the 64.0 MiB a stick's bko-holt reads: it isn't written to the stick (the backup keeps its copy)");
        // No room for the file and its .sha256: refused, and an older image's
        // smaller partition named.
        let e = room_check(&small, 2 * MIB, &room(MIB, None, None, vec![])).unwrap_err();
        assert_eq!(e.code, "stick_full");
        assert_eq!(e.text, "this machine's system.json needs 2.0 MiB and the stick's config partition has 1.0 MiB free: nothing is written, and the stick keeps the copy it had. Remove other machines' folders from the stick, or write it with an image from 2026-11-26 or later (its config partition is 16.0 MiB, theirs 256 MiB)");
        assert!(!room_check(&big, 2 * MIB, &room(MIB, None, None, vec![])).unwrap_err().text.contains("image"));
        // The new file is written while the old two stand: the old prev's
        // room comes too late for it.
        let (cur, prev) = (Some(5 * MIB), Some(5 * MIB));
        assert_eq!(codes(room_check(&big, 5 * MIB, &room(5 * MIB - 1, cur, prev, vec![]))), "refused stick_full");
        // Its .sha256 comes after the old prev is gone: 5 MiB free is enough, then.
        let need = 22 * MIB;
        assert_eq!(codes(room_check(&big, 5 * MIB, &room(5 * MIB, cur, prev, vec![]))), "stick_room_short");
        // Without a current system.json, the old prev isn't replaced, so frees nothing.
        assert_eq!(codes(room_check(&big, 5 * MIB, &room(5 * MIB, None, prev, vec![]))), "refused stick_full");
        // Each file takes whole clusters: one byte takes 4 KiB, and its .sha256 another.
        assert_eq!(codes(room_check(&big, 1, &room(8191, None, None, vec![]))), "refused stick_full");
        assert_eq!(codes(room_check(&big, 1, &room(8192, None, None, vec![]))), "stick_room_short");
        // The logs: 11 of 2 MiB (the 10 kept and the one being written), less what is there.
        let free = |logs_room: u64| 4 * MIB + 4096 + logs_room;
        assert_eq!(codes(room_check(&big, 4 * MIB, &room(free(need), None, None, vec![]))), "");
        let w = room_check(&big, 4 * MIB, &room(free(need - 4096), None, None, vec![])).unwrap();
        assert_eq!(w[0].code, "stick_room_short");
        assert_eq!(w[0].text, "once this machine's system.json is written, the stick's config partition has 22.0 MiB free, and its restore logs may need 22.0 MiB: a restore may not keep its log on the stick. Remove other machines' folders from the stick");
        let ten = vec![2 * MIB; 10];
        assert_eq!(codes(room_check(&big, 4 * MIB, &room(free(2 * MIB), None, None, ten.clone()))), "");
        assert_eq!(codes(room_check(&big, 4 * MIB, &room(free(2 * MIB - 4096), None, None, ten))), "stick_room_short");
        // Logs take whole clusters too: ten of one byte take 40 KiB.
        assert_eq!(codes(room_check(&big, 4 * MIB, &room(free(22 * MIB - 10 * 4096), None, None, vec![1; 10]))), "");
        assert_eq!(codes(room_check(&big, 4 * MIB, &room(free(22 * MIB - 10 * 4096 - 1), None, None, vec![1; 10]))), "stick_room_short");
        // A cluster of 0 (no statvfs) counts bytes.
        assert_eq!(codes(room_check(&big, 10, &Room { free: 11, cluster: 0, current: None, prev: None, logs: vec![] })), "stick_room_short");
        assert_eq!(codes(room_check(&big, 10, &Room { free: 10, cluster: 0, current: None, prev: None, logs: vec![] })), "refused stick_full");
    }

    #[test]
    fn markers() {
        let m = parse_marker(&marker(4)).unwrap();
        assert_eq!((m.number, m.sectors, m.label.as_str(), m.fat_serial.as_str()), (4, 32768, "BACKOTTER", "3F2A-9C71"));
        assert_eq!(m.system_schemas, [1]);
        assert_eq!(m.dump_schemas.as_deref(), Some(&[1u64][..]));
        // A marker from before dump_schemas: its image reads dump schema 1.
        let old = parse_marker(&marker(4).replace("  \"dump_schemas\": [1],\n", "")).unwrap();
        assert_eq!(old.dump_schemas, None);
        assert_eq!(old.dump_schemas_read(), [1]);
        assert_eq!(parse_marker(&marker(4).replace("\"dump_schemas\": [1]", "\"dump_schemas\": [1, 2]")).unwrap().dump_schemas_read(), [1, 2]);
        assert!(parse_marker("{}").unwrap_err().contains("backotter-image"));
        let e = crate::json::parse("{").unwrap_err();
        assert_eq!(parse_marker("{").unwrap_err(), format!("not JSON ({} at byte {})", e.message, e.offset));
        assert!(parse_marker(&marker(0)).unwrap_err().contains("number"));
        assert_eq!(marker_path(None), "/cdrom/.disk/backotter.json");
        assert_eq!(marker_path(Some(Family::Haiku)), "/boot/.disk/backotter.json");
        assert_eq!(marker_path(Some(Family::OpenBsd)), "/.disk/backotter.json");
    }

    /// An MBR disk image whose slot SLOT is CONFIG (FreeBSD's, Haiku's and
    /// OpenBSD's images keep the config partition in an MBR slot).
    fn mbr_disk(slot: usize, config: &[u8]) -> Vec<u8> {
        let start = 2048u32;
        let n = (config.len() / 512) as u32;
        let mut img = vec![0u8; ((start + n + 2048) * 512) as usize];
        crate::parttable::build::mbr_entry(&mut img[..512], 0, 0x80, 0xa5, 64, 1024);
        crate::parttable::build::mbr_entry(&mut img[..512], slot, 0, 0x0e, start, n);
        img[start as usize * 512..start as usize * 512 + config.len()].copy_from_slice(config);
        img
    }

    #[test]
    fn freebsd_finds_it_on_the_root_file_systems_disk() {
        let t = Tree::new("stick");
        let stick = mbr_disk(2, &config(three()));
        let mut decoy = stick.clone();
        decoy[2048 * 512 + 39] ^= 1;
        t.file("/dev/da0", &decoy).file("/dev/da1", &stick).file("/.disk/backotter.json", marker(3));
        let info = |n: usize| format!("da{n}\t# Disk name\n512\t# sectorsize\n{}\t# mediasize in bytes\n\t# Disk descr.\n\t# Disk ident.\n", stick.len());
        let r = crate::sys::FakeRunner::default()
            .with("sysctl -n kern.disks", "da1 da0 cd0")
            .with("diskinfo -v da0", &info(0))
            .with("diskinfo -v da1", &info(1))
            .with("mount -p", "/dev/ufs/BKOROOT\t/\tufs\tro\t1\t1\ntmpfs\t/tmp\ttmpfs\trw\t0\t0\n")
            .with("glabel status -s", "ufs/BKOROOT  N/A  da1s2a\n");
        let (v, code) = document(&Sys::new(t.root(), Box::new(r), true), Some(Family::FreeBsd));
        assert_eq!(code, 0, "{}", v.pretty());
        assert_eq!((v.str_at(&["boot_disk", "node"]), v.str_at(&["boot_disk", "found_by"])), (Some("da1"), Some("root_mount")));
        assert_eq!(v.str_at(&["config", "dev"]), Some("/dev/da1s3"));
        assert_eq!(v.arr_at(&["machines"]).len(), 3);
        // Here, no DMI UUID yet (as the plan document's here off Linux).
        assert!(v.arr_at(&["machines"]).iter().all(|m| m.get_bool("this_computer") == Some(false)));
        // The root on the decoy's disk: its serial isn't the image's.
        let r = crate::sys::FakeRunner::default()
            .with("sysctl -n kern.disks", "da0")
            .with("diskinfo -v da0", &info(0))
            .with("mount -p", "/dev/da0s2a\t/\tufs\tro\t1\t1\n");
        let (v, code) = document(&Sys::new(t.root(), Box::new(r), true), Some(Family::FreeBsd));
        assert_eq!((code, v.str_at(&["config", "state"])), (1, Some("damaged")));
    }

    #[test]
    fn illumos_finds_it_by_the_markers_partition_and_refuses_two() {
        let mut gpt = vec![0u8; (4096 + 32768 + 2048) * 512];
        crate::parttable::build::write_gpt(
            &mut gpt,
            512,
            &crate::parttable::build::GptSpec {
                disk_guid: "11111111-2222-3333-4444-555555555555",
                parts: vec![
                    ("c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "00000000-0000-0000-0000-000000000001", 64, 1087, 0, "esp"),
                    ("6a85cf4d-1dd2-11b2-99a6-080020736631", "00000000-0000-0000-0000-000000000002", 1088, 2111, 0, "boot"),
                    ("6a85cf4d-1dd2-11b2-99a6-080020736631", "00000000-0000-0000-0000-000000000003", 2112, 4095, 0, "root"),
                    ("ebd0a0a2-b9e5-4433-87c0-68b6b72699c7", "00000000-0000-0000-0000-000000000004", 4096, 4096 + 32767, 0, "BACKOTTER"),
                ],
            },
        );
        let c = config(three());
        gpt[4096 * 512..4096 * 512 + c.len()].copy_from_slice(&c);
        // Its boot sector's label in lower case: still the marker's, as
        // check_identity has it.
        gpt[4096 * 512 + 43..4096 * 512 + 52].copy_from_slice(b"backotter");
        let t = Tree::new("stick");
        t.file("/dev/rdsk/c1t0d0p0", vec![0u8; 1 << 20]).file("/dev/rdsk/c2t0d0p0", &gpt).file("/.disk/backotter.json", marker(4));
        let list = |n: &[&str]| n.iter().map(|d| format!("USB\t{d}\tKingston\tDT\t{}\tyes\tno\n", gpt.len())).collect::<String>();
        let r = crate::sys::FakeRunner::default().with("diskinfo -Hp", &list(&["c1t0d0", "c2t0d0"]));
        let (v, code) = document(&Sys::new(t.root(), Box::new(r), true), Some(Family::Illumos));
        assert_eq!(code, 0, "{}", v.pretty());
        assert_eq!((v.str_at(&["boot_disk", "node"]), v.str_at(&["boot_disk", "found_by"])), (Some("c2t0d0"), Some("marker_scan")));
        assert_eq!(v.str_at(&["config", "dev"]), Some("/dev/dsk/c2t0d0s3"));
        // A second stick from the same image: it can't tell which.
        t.file("/dev/rdsk/c3t0d0p0", &gpt);
        let r = crate::sys::FakeRunner::default().with("diskinfo -Hp", &list(&["c1t0d0", "c2t0d0", "c3t0d0"]));
        let (v, code) = document(&Sys::new(t.root(), Box::new(r), true), Some(Family::Illumos));
        assert_eq!((code, v.str_at(&["config", "state"])), (1, Some("two_sticks")));
        assert!(v.str_at(&["config", "why"]).unwrap().contains("c2t0d0, c3t0d0"));
        crate::testutil::golden_json("stick-json", "two-sticks", &v);
    }

    #[test]
    fn device_names_inside_disks() {
        let disks: Vec<RawDisk> = ["da0", "da1", "da10"].iter().map(|n| RawDisk { name: n.to_string(), path: format!("/dev/{n}"), size: 0, sector: 512, model: None, serial: None }).collect();
        let sys = Sys::new(Tree::new("stick").root(), Box::new(crate::sys::FakeRunner::default()), true);
        assert_eq!(disk_of(&sys, Family::FreeBsd, "/dev/da10s2a", &disks).as_deref(), Some("da10"));
        assert_eq!(disk_of(&sys, Family::FreeBsd, "/dev/da1p3", &disks).as_deref(), Some("da1"));
        assert_eq!(disk_of(&sys, Family::FreeBsd, "/dev/da2s1", &disks), None);
        let fb = Sys::new(Tree::new("stick").root(), Box::new(crate::sys::FakeRunner::default().with("glabel status -s", "ufs/BKOROOT  N/A  da1s2a\n")), true);
        assert_eq!(disk_of(&fb, Family::FreeBsd, "/dev/ufs/BKOROOT", &disks).as_deref(), Some("da1"));
        let sd: Vec<RawDisk> = ["sd0", "sd1"].iter().map(|n| RawDisk { name: n.to_string(), path: format!("/dev/r{n}c"), size: 0, sector: 512, model: None, serial: Some(format!("{n}0123456789abcd")) }).collect();
        assert_eq!(disk_of(&sys, Family::OpenBsd, "/dev/sd1a", &sd).as_deref(), Some("sd1"));
        assert_eq!(disk_of(&sys, Family::OpenBsd, "sd10123456789abcd.a", &sd).as_deref(), Some("sd1"));
        let nb = Sys::new(Tree::new("stick").root(), Box::new(crate::sys::FakeRunner::default().with("dkctl sd1 listwedges", "/dev/rsd1: 3 wedges:\ndk3: root, 2097152 blocks at 64, type: ffs\n")), true);
        assert_eq!(disk_of(&nb, Family::NetBsd, "/dev/dk3", &sd).as_deref(), Some("sd1"));
        let hk: Vec<RawDisk> = vec![RawDisk { name: "usb_0_0".into(), path: "/dev/disk/usb/0/0/raw".into(), size: 0, sector: 512, model: None, serial: None }];
        assert_eq!(disk_of(&sys, Family::Haiku, "/dev/disk/usb/0/0/0", &hk).as_deref(), Some("usb_0_0"));
    }
    // ---- --copy (open question 8) ----

    fn private_dir() -> crate::testutil::TempDir {
        use std::os::unix::fs::PermissionsExt;
        let d = crate::testutil::TempDir::new("stick-copy");
        std::fs::set_permissions(d.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        d
    }

    fn sys_of(t: &Tree) -> Sys {
        Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), true)
    }

    /// An error as main writes it: its document, for a golden.
    fn refused(r: Result<Value, (jsonout::ErrorCode, String)>, want: jsonout::ErrorCode) -> (Value, String) {
        let (code, line) = r.expect_err("a refusal");
        assert_eq!(code, want, "{line}");
        assert!(line.starts_with("bko-holt stick --copy: ") && !line.contains('\n'), "{line}");
        (crate::json::parse(&jsonout::error_doc(1, code, &line)).unwrap(), line)
    }

    #[test]
    fn copy_writes_the_checked_layout_of_the_machine_asked_for() {
        use std::os::unix::fs::PermissionsExt;
        let ms = two_with_layouts();
        let t = linux_tree(&config(ms), None);
        let d = private_dir();
        let out = d.path().join("layout.json");
        let v = copy_layout(&sys_of(&t), None, BABBAGE, &out).unwrap();
        let text = std::fs::read_to_string(&out).unwrap();
        // The dump babbage's system.json carries, whole, as dump prints it.
        let b = system(BABBAGE, "babbage", "", &with_layout(&DUMP.replace("\"hostname\": \"t\"", "\"hostname\": \"babbage-host\"")));
        let carried = crate::json::parse(&b).unwrap().get(LAYOUT_KEY).unwrap().clone();
        assert_eq!(crate::json::parse(&text).unwrap(), carried);
        assert_eq!(text, carried.pretty());
        assert_eq!(crate::json::parse(&text).unwrap().str_at(&["host", "hostname"]), Some("babbage-host"));
        // load takes it as it takes a dump.
        assert!(crate::load::layout::Layout::from_json(crate::json::parse(&text).unwrap()).is_ok());
        assert_eq!(std::fs::metadata(&out).unwrap().permissions().mode() & 0o7777, 0o600);
        assert_eq!(v.get_str("format"), Some("bko-holt-stick-copy"));
        assert_eq!((v.get_str("install_id"), v.get_str("path")), (Some(BABBAGE), Some(format!("{MACHINES}/{BABBAGE}/system.json").as_str())));
        assert_eq!(v.get_u64("bytes"), Some(text.len() as u64));
        assert_eq!(v.get_str("sha256"), Some(crate::bytes::sha256_hex(text.as_bytes()).as_str()));
        assert_eq!(v.get_str("system_sha256"), Some(crate::bytes::sha256_hex(b.as_bytes()).as_str()));
        assert_eq!(v.get_str("out"), Some(out.to_str().unwrap()));
        let keys: Vec<&str> = v.as_object().unwrap().iter().map(|(k, _)| k.as_str()).collect();
        assert_eq!(keys, ["format", "schema", "version", "generated", "dry_run", "install_id", "path", "out", "bytes", "sha256", "system_sha256", "luks_headers"]);
        let mut g = v.clone();
        g.put("out", "OUT");
        crate::testutil::golden_json("stick-copy-json", "ok", &g);
        // The ID in capitals: the same machine, case aside.
        let out2 = d.path().join("again.json");
        let v = copy_layout(&sys_of(&t), None, &LOVELACE.to_uppercase(), &out2).unwrap();
        assert_eq!(v.get_str("install_id"), Some(LOVELACE));
        assert_eq!(crate::json::parse(&std::fs::read_to_string(&out2).unwrap()).unwrap(), crate::json::parse(DUMP).unwrap());
        // OUT there already: refused, and the file there left as it is.
        let (_, line) = refused(copy_layout(&sys_of(&t), None, LOVELACE, &out), jsonout::ErrorCode::CopyFailed);
        assert!(line.contains("layout.json") && line.contains("left as it is") && !line.contains("nothing left"), "{line}");
        assert_eq!(std::fs::read_to_string(&out).unwrap(), text);
    }

    #[test]
    fn copy_refuses_what_it_cant_take() {
        use jsonout::ErrorCode as E;
        let d = private_dir();
        let out = d.path().join("layout.json");
        let gone = |out: &std::path::Path| assert!(!out.exists(), "{} written", out.display());
        // No such machine.
        let t = linux_tree(&config(two_with_layouts()), None);
        let (_, line) = refused(copy_layout(&sys_of(&t), None, "2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6", &out), E::NoSuchMachine);
        assert!(line.contains("it has 2"), "{line}");
        gone(&out);
        // Two directories named by it, case aside.
        let up = LOVELACE.to_uppercase();
        let a = system(LOVELACE, "lovelace", "", &with_layout(DUMP));
        let b = system(&up, "lovelace", "", &with_layout(DUMP));
        let t = linux_tree(&config(vec![machine_dir(LOVELACE, &a, Some(&sum(&a))), machine_dir(&up, &b, Some(&sum(&b)))]), None);
        let (dv, _) = document(&sys_of(&t), None);
        assert_eq!(dv.arr_at(&["machines"]).len(), 2, "{}", dv.pretty());
        let (_, line) = refused(copy_layout(&sys_of(&t), None, LOVELACE, &out), E::TwoMachines);
        assert!(line.contains("2 directories"), "{line}");
        gone(&out);
        // A checksum that doesn't match: not copied, said why.
        let edited = a.replace("\"vda1\"", "\"vda9\"");
        let t = linux_tree(&config(vec![machine_dir(LOVELACE, &edited, Some(&sum(&a)))]), None);
        let (v, line) = refused(copy_layout(&sys_of(&t), None, LOVELACE, &out), E::MachineNotOk);
        assert!(line.contains("(lovelace) is checksum_bad: ") && line.contains("doesn't match its .sha256"), "{line}");
        crate::testutil::golden_json("stick-copy-json", "checksum-bad", &v);
        gone(&out);
        // Damaged: no .sha256, or not JSON.
        let t = linux_tree(&config(vec![machine_dir(LOVELACE, &a, None), machine_dir(BABBAGE, "{\"format\": \"backot", Some(&sum("{\"format\": \"backot")))]), None);
        let (_, line) = refused(copy_layout(&sys_of(&t), None, LOVELACE, &out), E::MachineNotOk);
        assert!(line.contains("checksum_missing"), "{line}");
        let (_, line) = refused(copy_layout(&sys_of(&t), None, BABBAGE, &out), E::MachineNotOk);
        assert!(line.contains("unreadable") && line.contains("isn't JSON"), "{line}");
        gone(&out);
        // Today's system.json, with no layout in it; or one that isn't a document.
        let plain = system(LOVELACE, "lovelace", "", "");
        let odd = system(BABBAGE, "babbage", "", ", \"layout\": \"see the backup\"");
        let t = linux_tree(&config(vec![machine_dir(LOVELACE, &plain, Some(&sum(&plain))), machine_dir(BABBAGE, &odd, Some(&sum(&odd)))]), None);
        let (v, line) = refused(copy_layout(&sys_of(&t), None, LOVELACE, &out), E::NoLayout);
        assert!(line.contains("carries no disk layout"), "{line}");
        crate::testutil::golden_json("stick-copy-json", "no-layout", &v);
        let (_, line) = refused(copy_layout(&sys_of(&t), None, BABBAGE, &out), E::NoLayout);
        assert!(line.contains("isn't a bko-holt dump document"), "{line}");
        gone(&out);
        // A layout load wouldn't take: another format, an OS it doesn't support.
        let other = system(LOVELACE, "lovelace", "", &with_layout(&DUMP.replace("\"bko-holt\"", "\"other\"")));
        let os = system(BABBAGE, "babbage", "", &with_layout(&DUMP.replace("\"debian\"", "\"plan9\"")));
        let t = linux_tree(&config(vec![machine_dir(LOVELACE, &other, Some(&sum(&other))), machine_dir(BABBAGE, &os, Some(&sum(&os)))]), None);
        let (_, line) = refused(copy_layout(&sys_of(&t), None, LOVELACE, &out), E::LayoutRefused);
        assert!(line.contains("not a bko-holt dump"), "{line}");
        let (_, line) = refused(copy_layout(&sys_of(&t), None, BABBAGE, &out), E::LayoutRefused);
        assert!(line.contains("plan9"), "{line}");
        gone(&out);
        // A dump that kept LUKS2 headers: copied, the document counting
        // them, for bko-rescue to fetch from the backup (open question 12).
        let one = format!("{{\"device\": \"vda1\", \"file\": \"luks2-header-1111.img\", \"bytes\": 16777216, \"sha256\": \"{}\"}}", "ab".repeat(32));
        let kept = DUMP.replace("\"fstab\":", &format!("\"luks_headers\": [{one}],\n    \"fstab\":"));
        assert!(crate::load::layout::Layout::from_json(crate::json::parse(&kept).unwrap()).is_ok());
        let none = DUMP.replace("\"fstab\":", "\"luks_headers\": [],\n    \"fstab\":");
        let a = system(LOVELACE, "lovelace", "", &with_layout(&kept));
        let b = system(BABBAGE, "babbage", "", &with_layout(&none));
        let t = linux_tree(&config(vec![machine_dir(LOVELACE, &a, Some(&sum(&a))), machine_dir(BABBAGE, &b, Some(&sum(&b)))]), None);
        let v = copy_layout(&sys_of(&t), None, LOVELACE, &out).expect("a kept header: copied");
        assert_eq!(v.get_u64("luks_headers"), Some(1));
        std::fs::remove_file(&out).unwrap();
        let v = copy_layout(&sys_of(&t), None, BABBAGE, &out).expect("none kept: copied");
        assert_eq!(v.get_u64("luks_headers"), Some(0));
        std::fs::remove_file(&out).unwrap();
        // One that couldn't be fetched (a file name dump never makes): said now.
        let bad = DUMP.replace("\"fstab\":", &format!("\"luks_headers\": [{}],\n    \"fstab\":", one.replace("luks2-header-1111.img", "../../etc/shadow")));
        let c = system(LOVELACE, "lovelace", "", &with_layout(&bad));
        let t = linux_tree(&config(vec![machine_dir(LOVELACE, &c, Some(&sum(&c)))]), None);
        let (_, line) = refused(copy_layout(&sys_of(&t), None, LOVELACE, &out), E::LayoutRefused);
        assert!(line.contains("can't fetch from the backup") && line.contains("isn't a header file's name"), "{line}");
        gone(&out);
        // The config partition itself: missing, or reformatted.
        let t = Tree::new("stick");
        t.file("/proc/self/mountinfo", "");
        let (_, line) = refused(copy_layout(&sys_of(&t), None, LOVELACE, &out), E::ConfigNotOk);
        assert!(line.contains("is missing: "), "{line}");
        let mut c = config(two_with_layouts());
        c[39] ^= 1;
        let (_, line) = refused(copy_layout(&sys_of(&linux_tree(&c, None)), None, LOVELACE, &out), E::ConfigNotOk);
        assert!(line.contains("is damaged: ") && line.contains("reformatted"), "{line}");
        // Not root.
        let t = linux_tree(&config(two_with_layouts()), None);
        let (_, line) = refused(copy_layout(&Sys::new(t.root(), Box::new(crate::sys::FakeRunner::default()), false), None, LOVELACE, &out), E::ConfigNotOk);
        assert!(line.contains("is unreadable: "), "{line}");
        gone(&out);
        // OUT in a directory that isn't there: nothing left behind.
        let (_, line) = refused(copy_layout(&sys_of(&t), None, LOVELACE, &d.path().join("no/such/layout.json")), E::CopyFailed);
        assert!(line.contains("nothing left there"), "{line}");
    }

    #[test]
    fn a_stick_document_keeps_no_bytes() {
        // --json alone: what --copy keeps is never kept, nor the layout shown.
        let t = linux_tree(&config(two_with_layouts()), None);
        let s = scan(&sys_of(&t), None, None);
        assert_eq!(s.code, 0);
        assert!(s.kept.is_empty() && s.others == 0);
        assert_eq!(s.doc.arr_at(&["machines"]).len(), 2);
        assert!(!jsonout::line(&s.doc).contains("ext4") && !jsonout::line(&s.doc).contains("\"layout\""));
        // --copy reads the machine asked for only; the other is counted.
        let s = scan(&sys_of(&t), None, Some(BABBAGE));
        assert_eq!(s.kept.len(), 1);
        assert_eq!(s.kept[0].0, BABBAGE);
        assert_eq!(s.others, 1);
        let rows: Vec<&str> = s.doc.arr_at(&["machines"]).iter().filter_map(|m| m.get_str("install_id")).collect();
        assert_eq!(rows, [BABBAGE]);
    }

    #[test]
    fn copy_values_are_never_flags() {
        let a = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(without_copy_values(&a(&["--copy", "ID", "-h", "--json"])), a(&["--copy", "--json"]));
        assert_eq!(without_copy_values(&a(&["--json", "--copy", "--help", "/o"])), a(&["--json", "--copy"]));
        assert_eq!(without_copy_values(&a(&["--", "--copy", "x", "y"])), a(&["--", "--copy", "x", "y"]));
        assert_eq!(without_copy_values(&a(&["--copy"])), a(&["--copy"]));
    }
}
