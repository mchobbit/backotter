//! Builds H1's dump corpus (holt/tests/corpus, its README) from raw dumps:
//! `BKO_HOLT_CORPUS_RAW` holds GUEST/src.json for each guest and
//! provenance.tsv (testvm/test2/h1/collect-dumps.sh's), and the redacted
//! corpus goes to `BKO_HOLT_CORPUS_OUT`: GUEST/src.json and index.tsv.
//! It's `#[ignore]`d: it runs only by hand, on the host that holds the raw
//! dumps (they never leave it), with `--ignored`.
//!
//! For each dump, in this order:
//!   1. `bugreport::check_plan` on the raw dump: load's plan of its
//!      redaction is the raw dump's own, pseudonyms aside, and no identifier
//!      is left (the real proof: a corpus dump alone can only show that
//!      pseudonyms survive a second redaction);
//!   2. `redact::redact`, the dump on its own; twice, and both must be the
//!      same bytes;
//!   3. e-mail addresses in string values (kernel build signatures such as
//!      NetBSD's) become `builder-at-redacted`, which redact.rs has no kind
//!      for;
//!   4. third-party boot code (`esp_image[].b64`, `boot_code.image_b64`,
//!      `boot_code.code_b64`, `boot_code.gap_b64`) is replaced by a fixed
//!      filler of the same length, its redacted bytes' length and CRC-32 kept
//!      in index.tsv;
//!   5. the result is planned against the redacted, unfilled dump (the same
//!      plan, or index.tsv says how it differs), and check_plan runs on it
//!      too.
//!
//! The guests are provenance.tsv's: a dump in BKO_HOLT_CORPUS_RAW that it
//! doesn't list (an earlier collection's), a listed guest without its dump,
//! or a dump in BKO_HOLT_CORPUS_OUT that this build doesn't make stops the
//! build before it starts.  Every dump is built before anything is written;
//! the old index.tsv goes first and the new one is written last, so a killed
//! build leaves no index.tsv beside a half-made corpus.  Two builds of the
//! same raw dumps give the same bytes.

mod common;

use bko_holt::json::{self, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// What a filled blob's bytes become, repeated to the original's length.
const FILLER: &[u8] = b"bko-holt corpus: boot code left out, see holt/tests/corpus/README.md\n";
/// What an e-mail address in a string value becomes.
const EMAIL_SCRUB: &str = "builder-at-redacted";

/// The e-mail-shaped tokens of S, `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`,
/// as byte ranges.
fn email_spans(s: &str) -> Vec<(usize, usize)> {
    let b = s.as_bytes();
    let local = |c: u8| c.is_ascii_alphanumeric() || b"._%+-".contains(&c);
    let domain = |c: u8| c.is_ascii_alphanumeric() || c == b'.' || c == b'-';
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] != b'@' {
            i += 1;
            continue;
        }
        let mut st = i;
        while st > 0 && local(b[st - 1]) {
            st -= 1;
        }
        let mut en = i + 1;
        while en < b.len() && domain(b[en]) {
            en += 1;
        }
        // The longest domain that ends in a dot and 2 or more letters.
        let mut best = None;
        for e in (i + 2..=en).rev() {
            let d = &b[i + 1..e];
            if let Some(dot) = d.iter().rposition(|&c| c == b'.') {
                let tld = &d[dot + 1..];
                if dot > 0 && tld.len() >= 2 && tld.iter().all(u8::is_ascii_alphabetic) {
                    best = Some(e);
                    break;
                }
            }
        }
        match best {
            Some(e) if st < i => {
                out.push((st, e));
                i = e;
            }
            _ => i += 1,
        }
    }
    out
}

/// S with each whole-word HOST (no ASCII letter or digit either side)
/// become TO; adds the count to N.
fn replace_word(s: &str, host: &str, to: &str, n: &mut usize) -> String {
    let alnum = |c: Option<char>| c.is_some_and(|c| c.is_ascii_alphanumeric());
    let mut out = String::new();
    let mut last = 0;
    for (i, _) in s.match_indices(host) {
        let j = i + host.len();
        if !alnum(s[..i].chars().next_back()) && !alnum(s[j..].chars().next()) {
            out += &s[last..i];
            out += to;
            last = j;
            *n += 1;
        }
    }
    out + &s[last..]
}

/// redact.rs leaves `os` as it is, but the kernel's version strings can
/// hold the guest's own hostname, which it replaced everywhere else (a build
/// signature, user@HOST:/usr/obj; a hostname that is Debian's release tag):
/// the pseudonym there too, or the dump shows which name it stands for.
fn scrub_kernel_hosts(v: &Value, r: &bko_holt::redact::Redactor, n: &mut usize) -> Value {
    let hosts: Vec<_> = r.entries.iter().filter(|e| e.kind == bko_holt::redact::Kind::Hostname).collect();
    let Value::Obj(top) = v else { return v.clone() };
    Value::Obj(top.iter().map(|(k, x)| {
        let Value::Obj(os) = x else { return (k.clone(), x.clone()) };
        if k != "os" {
            return (k.clone(), x.clone());
        }
        (k.clone(), Value::Obj(os.iter().map(|(f, y)| match y {
            Value::Str(s) if f == "kernel_version" || f == "kernel_release" => {
                (f.clone(), Value::Str(hosts.iter().fold(s.clone(), |s, e| replace_word(&s, &e.original, &e.pseudonym, n))))
            }
            _ => (f.clone(), y.clone()),
        }).collect()))
    }).collect())
}

fn scrub_emails(v: &Value, n: &mut usize) -> Value {
    match v {
        Value::Str(s) => {
            let spans = email_spans(s);
            if spans.is_empty() {
                return v.clone();
            }
            let mut out = String::new();
            let mut last = 0;
            for (a, b) in spans {
                out += &s[last..a];
                out += EMAIL_SCRUB;
                last = b;
                *n += 1;
            }
            out += &s[last..];
            Value::Str(out)
        }
        Value::Arr(a) => Value::Arr(a.iter().map(|x| scrub_emails(x, n)).collect()),
        Value::Obj(kv) => Value::Obj(kv.iter().map(|(k, x)| (k.clone(), scrub_emails(x, n))).collect()),
        other => other.clone(),
    }
}

/// A blob filled: its JSON path, its bytes and their CRC-32.
struct Filled {
    path: String,
    bytes: usize,
    crc32: u32,
}

fn filler(n: usize) -> Vec<u8> {
    FILLER.iter().copied().cycle().take(n).collect()
}

/// Whether KEY, in an object under PARENT's key, is boot code the corpus
/// leaves out: a disk's or partition's `boot_code` images, an unmounted
/// ESP's chunks (other/mod.rs's esp_image).
fn is_boot_code(parent: &str, key: &str) -> bool {
    (parent == "boot_code" && matches!(key, "image_b64" | "code_b64" | "gap_b64")) || (parent == "esp_image" && key == "b64")
}

/// V with its boot code filled; PARENT is the key V sits under (an array's
/// elements sit under the array's key).  A boot-code value that isn't base64
/// is left as it is and its path put in BAD: that dump isn't made.
fn fill(v: &Value, path: &str, parent: &str, out: &mut Vec<Filled>, bad: &mut Vec<String>) -> Value {
    match v {
        Value::Obj(kv) => Value::Obj(
            kv.iter()
                .map(|(k, x)| {
                    let p = format!("{path}.{k}");
                    if let Value::Str(s) = x {
                        if is_boot_code(parent, k) {
                            let Some(raw) = bko_holt::bytes::unbase64(s) else {
                                bad.push(p.trim_start_matches('.').to_string());
                                return (k.clone(), x.clone());
                            };
                            out.push(Filled { path: p, bytes: raw.len(), crc32: bko_holt::bytes::crc32(&raw) });
                            return (k.clone(), Value::Str(bko_holt::bytes::base64(&filler(raw.len()))));
                        }
                    }
                    (k.clone(), fill(x, &p, k, out, bad))
                })
                .collect(),
        ),
        Value::Arr(a) => Value::Arr(a.iter().enumerate().map(|(i, x)| fill(x, &format!("{path}.{i}"), parent, out, bad)).collect()),
        other => other.clone(),
    }
}

type Provenance = BTreeMap<String, BTreeMap<String, String>>;

/// provenance.tsv (collect-dumps.sh's, beside the raw dumps) by guest: its
/// header's columns to the row's values.  It must be there, with a guest
/// column and one row a guest.
fn provenance(raw: &Path) -> Result<Provenance, String> {
    let p = raw.join("provenance.tsv");
    let text = std::fs::read_to_string(&p).map_err(|e| format!("{}: {e} (collect-dumps.sh writes it beside the raw dumps)", p.display()))?;
    let mut lines = text.lines();
    let cols: Vec<&str> = lines.next().unwrap_or("").split('\t').collect();
    if !cols.contains(&"guest") {
        return Err(format!("{}: no guest column", p.display()));
    }
    let mut out = BTreeMap::new();
    for (i, l) in lines.enumerate() {
        if l.is_empty() {
            continue;
        }
        let row: BTreeMap<String, String> = cols.iter().zip(l.split('\t')).map(|(c, v)| (c.to_string(), v.to_string())).collect();
        let Some(g) = row.get("guest").filter(|g| !g.is_empty()).cloned() else {
            return Err(format!("{}: line {} names no guest", p.display(), i + 2));
        };
        if out.insert(g.clone(), row).is_some() {
            return Err(format!("{}: two rows for {g}", p.display()));
        }
    }
    Ok(out)
}

/// The names of DIR's subdirectories that hold a src.json, sorted (none when
/// DIR isn't there).
fn dumps_in(dir: &Path) -> Result<Vec<String>, String> {
    let rd = match std::fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(format!("{}: {e}", dir.display())),
    };
    let mut v: Vec<String> = rd.filter_map(|e| e.ok()).filter(|e| e.path().join("src.json").is_file()).filter_map(|e| e.file_name().to_str().map(str::to_string)).collect();
    v.sort();
    Ok(v)
}

/// The guests to build: provenance.tsv's, each with RAW/GUEST/src.json.  A
/// dump provenance.tsv doesn't list (an earlier collection's, or no source
/// now) or a listed guest without its dump is an error.
fn raw_guests(raw: &Path, prov: &Provenance) -> Result<Vec<String>, String> {
    let dumps = dumps_in(raw)?;
    let extra: Vec<&str> = dumps.iter().filter(|g| !prov.contains_key(*g)).map(String::as_str).collect();
    let missing: Vec<&str> = prov.keys().filter(|g| !dumps.contains(g)).map(String::as_str).collect();
    let mut bad = Vec::new();
    if !extra.is_empty() {
        bad.push(format!("dumps that provenance.tsv doesn't list (an earlier collection's?): {}", extra.join(", ")));
    }
    if !missing.is_empty() {
        bad.push(format!("provenance.tsv's guests without GUEST/src.json: {}", missing.join(", ")));
    }
    if prov.is_empty() {
        bad.push("provenance.tsv lists no guest".to_string());
    }
    if bad.is_empty() {
        Ok(prov.keys().cloned().collect())
    } else {
        Err(format!("{}: {}", raw.display(), bad.join("; ")))
    }
}

/// The provenance columns index.tsv carries (raw_sha256 stays on the host).
const PROV: [&str; 8] = ["host", "run", "result", "report_time", "generated", "tool_version", "generation", "image"];

struct Built {
    text: String,
    row: Vec<String>,
    log: String,
    /// The raw dump's check_plan failure, when it failed but the corpus dump
    /// was still made (the plan differs, or it doesn't map back): the class
    /// for the log, the whole message for the host-only error file.
    raw_fail: Option<(String, String)>,
}

/// check_plan's message up to its first detail: what kind of failure,
/// without the identifiers the rest may quote from the raw dump.
fn failure_class(e: &str) -> &str {
    for c in ["the redacted dump doesn't map back to the original", "the plan of the redacted dump differs", "left in the redacted dump's plan", "left in the redacted dump", "not JSON", "load's arguments"] {
        if e.starts_with(c) {
            return c;
        }
    }
    "another failure"
}

/// A built dump, or why it wasn't: (class, message).
type BuildResult = Result<Built, (String, String)>;

fn build_one(guest: &str, raw_text: &str, prov: Option<&BTreeMap<String, String>>) -> BuildResult {
    let hard = |class: &str, detail: String| (class.to_string(), detail);
    let v = json::parse(raw_text).map_err(|e| hard("not JSON", e.message.to_string()))?;
    // 1. The proof on the raw dump.  A leak it finds stops this dump; a plan
    // that differs, or a redaction that doesn't map back, is reported (the
    // test fails at the end) but the dump is still made, its own leak checks
    // below standing in for the ones check_plan didn't reach.
    let (raw_check, raw_fail) = match bko_holt::bugreport::check_plan(raw_text) {
        Ok((ids, lines)) => (format!("ok:{ids}:{lines}"), None),
        Err(e) => {
            let class = failure_class(&e);
            if class.starts_with("left in") || class == "not JSON" || class == "another failure" {
                return Err(hard(&format!("check_plan on the raw dump: {class}"), e));
            }
            (format!("FAIL:{}", class.replace(' ', "-")), Some((class.to_string(), e)))
        }
    };
    // 2. Redaction, twice.
    let (red, r) = bko_holt::redact::redact(&v);
    let red_text = red.pretty();
    if bko_holt::redact::redact(&v).0.pretty() != red_text {
        return Err(hard("two redactions of the dump differ", String::new()));
    }
    let leaks = r.leaks(&red_text);
    if !leaks.is_empty() {
        return Err(hard("an identifier is left in the redacted dump", format!("{leaks:?}")));
    }
    let a = common::plan(&red_text, 0).map_err(|e| hard("the redacted dump can't be planned", e))?;
    let leaks = r.leaks(&(a.out.clone() + &a.err));
    if !leaks.is_empty() {
        return Err(hard("an identifier is left in the redacted dump's plan", format!("{leaks:?}")));
    }
    // 3, 4. E-mail addresses, boot code.
    let mut scrubbed = 0;
    let scrubbed_v = scrub_kernel_hosts(&scrub_emails(&red, &mut scrubbed), &r, &mut scrubbed);
    let (mut filled, mut undecodable) = (Vec::new(), Vec::new());
    let out = fill(&scrubbed_v, "", "", &mut filled, &mut undecodable);
    if !undecodable.is_empty() {
        return Err(hard("boot code that isn't base64", undecodable.join(" ")));
    }
    let text = out.pretty();
    // 5. The filled dump plans as the redacted one does, and passes check_plan.
    let b = common::plan(&text, 0).map_err(|e| hard("the corpus dump can't be planned", e))?;
    let fill_plan = if a == b {
        "same".to_string()
    } else {
        let (ta, tb) = (common::golden_text(&a), common::golden_text(&b));
        let problem = (b.out.contains("# Problems (") && !a.out.contains("# Problems (")) || b.code != a.code;
        format!("differs:{}{}:{}", common::lines_changed(&ta, &tb), if problem { ":problem" } else { "" }, common::stages_changed(&ta, &tb).join("+"))
    };
    let (n_after, _) = bko_holt::bugreport::check_plan(&text).map_err(|e| hard(&format!("check_plan on the corpus dump: {}", failure_class(&e)), e))?;
    let after = format!("ok:{n_after}");
    let empty = BTreeMap::new();
    let p = prov.unwrap_or(&empty);
    let mut row = vec![guest.to_string()];
    for c in PROV {
        row.push(p.get(c).filter(|x| !x.is_empty()).cloned().unwrap_or_else(|| "-".into()));
    }
    let blob_bytes: usize = filled.iter().map(|f| f.bytes).sum();
    row.extend([
        r.entries.len().to_string(),
        raw_check.clone(),
        scrubbed.to_string(),
        filled.len().to_string(),
        blob_bytes.to_string(),
        fill_plan.clone(),
        after.clone(),
        if filled.is_empty() { "-".into() } else { filled.iter().map(|f| format!("{}={}/{:08x}", f.path.trim_start_matches('.'), f.bytes, f.crc32)).collect::<Vec<_>>().join(" ") },
    ]);
    let head = match &raw_fail {
        None => {
            let (ids, lines) = raw_check.trim_start_matches("ok:").split_once(':').unwrap_or(("?", "?"));
            format!("ok    {guest}: {ids} identifiers, plan of {lines} lines")
        }
        Some((class, _)) => format!("RAW   {guest}: check_plan on the raw dump: {class} (the message, which may quote the raw dump, is in the raw directory's corpus-build-errors.txt)"),
    };
    let log = format!(
        "{head}; {scrubbed} e-mail address(es) scrubbed; {} boot-code blob(s), {blob_bytes} bytes, filled; filled plan {fill_plan}; check_plan of the corpus dump {after}",
        filled.len()
    );
    Ok(Built { text, row, log, raw_fail })
}

/// index.tsv's columns: the guest, its provenance (collect-dumps.sh's), what
/// the build did, and each filled blob as PATH=BYTES/CRC32.  check_plan_raw
/// is `ok:IDENTIFIERS:PLAN_LINES` or `FAIL:CLASS`; check_plan the corpus
/// dump's (`ok:IDENTIFIERS`).
const COLUMNS: [&str; 17] = [
    "guest", "host", "run", "result", "report_time", "generated", "tool_version", "generation", "image", "identifiers", "check_plan_raw", "scrubbed", "blobs",
    "blob_bytes", "fill_plan", "check_plan", "filled",
];

#[test]
#[ignore]
fn corpus_build() {
    let (Some(raw), Some(out)) = (std::env::var_os("BKO_HOLT_CORPUS_RAW"), std::env::var_os("BKO_HOLT_CORPUS_OUT")) else {
        panic!("set BKO_HOLT_CORPUS_RAW (the raw dumps: GUEST/src.json, provenance.tsv) and BKO_HOLT_CORPUS_OUT (the corpus to write)");
    };
    let (raw, out) = (PathBuf::from(raw), PathBuf::from(out));
    assert_ne!(raw.canonicalize().ok(), out.canonicalize().ok(), "BKO_HOLT_CORPUS_OUT must not be the raw directory");
    let prov = provenance(&raw).unwrap_or_else(|e| panic!("{e}"));
    let guests = raw_guests(&raw, &prov).unwrap_or_else(|e| panic!("{e}"));
    let stale: Vec<String> = dumps_in(&out).unwrap_or_else(|e| panic!("{e}")).into_iter().filter(|g| !guests.contains(g)).collect();
    assert!(stale.is_empty(), "{} holds dumps this build doesn't make: {} (build into an empty directory, or remove them)", out.display(), stale.join(", "));
    // Every dump is built before anything is written.
    let built: Vec<(&String, BuildResult)> = guests
        .iter()
        .map(|g| {
            let r = match std::fs::read(raw.join(g).join("src.json")).map(String::from_utf8) {
                Ok(Ok(text)) => build_one(g, &text, prov.get(g)),
                Ok(Err(_)) => Err(("not UTF-8".to_string(), String::new())),
                Err(e) => Err(("can't be read".to_string(), e.to_string())),
            };
            (g, r)
        })
        .collect();
    std::fs::create_dir_all(&out).unwrap();
    // The old index goes first: a build killed from here on leaves none.
    let _ = std::fs::remove_file(out.join("index.tsv"));
    let mut index = COLUMNS.join("\t") + "\n";
    let (mut hard, mut soft, mut errors) = (Vec::new(), Vec::new(), String::new());
    for (g, r) in built {
        match r {
            Ok(b) => {
                println!("{}", b.log);
                let d = out.join(g);
                std::fs::create_dir_all(&d).unwrap();
                common::write_atomic(&d.join("src.json"), b.text.as_bytes()).unwrap_or_else(|e| panic!("{e}"));
                index += &(b.row.join("\t") + "\n");
                if let Some((class, msg)) = b.raw_fail {
                    soft.push(format!("{g}: {class}"));
                    errors += &format!("{g}: {msg}\n");
                }
            }
            Err((class, msg)) => {
                // Nothing of this dump is written, and an older one goes.
                let _ = std::fs::remove_file(out.join(g).join("src.json"));
                println!("FAIL  {g}: {class} (no corpus dump made; the message is in the raw directory's corpus-build-errors.txt)");
                hard.push(format!("{g}: {class}"));
                errors += &format!("{g}: {class}: {msg}\n");
            }
        }
    }
    common::write_atomic(&out.join("index.tsv"), index.as_bytes()).unwrap_or_else(|e| panic!("{e}"));
    // The messages quote the raw dumps: they stay beside them.
    let elog = raw.join("corpus-build-errors.txt");
    if errors.is_empty() {
        let _ = std::fs::remove_file(&elog);
    } else {
        std::fs::write(&elog, &errors).unwrap();
    }
    println!(
        "{} of {} dumps built into {}; check_plan on the raw dump failed for {} of them, which are still in the corpus",
        guests.len() - hard.len(),
        guests.len(),
        out.display(),
        soft.len()
    );
    assert!(hard.is_empty() && soft.is_empty(), "not made: {:?}; made, but check_plan failed on the raw dump: {:?}", hard, soft);
}

#[test]
fn a_hostname_in_the_kernels_version_strings() {
    let d = json::parse(
        r#"{"host": {"hostname": "oldbox"}, "os": {"id": "x",
            "kernel_version": "X 6.4 #1: Mon root@oldbox:/usr/obj/oldboxes/sys/oldbox",
            "kernel_release": "6.12+oldbox-amd64", "pretty_name": "oldbox"}}"#,
    )
    .unwrap();
    let (red, r) = bko_holt::redact::redact(&d);
    let mut n = 0;
    let v = scrub_kernel_hosts(&red, &r, &mut n);
    let s = |k: &str| v.at(&["os", k]).and_then(Value::as_str).unwrap().to_string();
    assert_eq!(n, 3);
    assert_eq!(s("kernel_version"), "X 6.4 #1: Mon root@host1:/usr/obj/oldboxes/sys/host1");
    assert_eq!(s("kernel_release"), "6.12+host1-amd64");
    assert_eq!(s("pretty_name"), "oldbox", "only the kernel's strings");
    let mut n = 0;
    assert_eq!(scrub_kernel_hosts(&v, &r, &mut n).pretty(), v.pretty());
    assert_eq!(n, 0);
}

#[test]
fn e_mail_shapes() {
    // Shaped like a BSD kernel's build signature; the addresses are RFC 2606's.
    let s = "NetBSD 11.0 (GENERIC) #0: Sat Mar  1 00:00:00 UTC 2026\n\tbuilds@mkrepro.example.org:/usr/src/sys/arch/amd64/compile/GENERIC";
    assert_eq!(email_spans(s).len(), 1);
    let mut n = 0;
    let v = scrub_emails(&Value::Str(s.into()), &mut n);
    assert_eq!(n, 1);
    assert_eq!(v.as_str().unwrap(), s.replace("builds@mkrepro.example.org", EMAIL_SCRUB));
    for not in ["user@host", "a@b.c", "@example.org", "root@", "x@y.12"] {
        assert!(email_spans(not).is_empty(), "{not}");
    }
    assert_eq!(email_spans("root@www.example.com:/usr/obj").len(), 1);
}

#[test]
fn boot_code_is_filled_to_its_length() {
    let code = bko_holt::bytes::base64(&[0xeb, 0x63, 0x90, 1, 2, 3, 4]);
    let img = bko_holt::bytes::base64(&vec![7u8; 1500]);
    let v = json::parse(&format!(
        r#"{{"storage": {{"disks": [{{"boot_code": {{"kind": "grub2", "code_b64": "{code}", "first_bytes": "eb6390"}},
            "partitions": [{{"esp_image": [{{"offset": 0, "b64": "{img}"}}], "boot_code": {{"image_b64": "{img}"}}}}]}}]}},
            "boot": {{"efi": {{"entries": [{{"load_option_hex": "0100"}}]}}}}, "b64": "{img}"}}"#
    ))
    .unwrap();
    let (mut f, mut bad) = (Vec::new(), Vec::new());
    let o = fill(&v, "", "", &mut f, &mut bad);
    assert!(bad.is_empty(), "{bad:?}");
    let paths: Vec<&str> = f.iter().map(|x| x.path.as_str()).collect();
    assert_eq!(paths, [".storage.disks.0.boot_code.code_b64", ".storage.disks.0.partitions.0.esp_image.0.b64", ".storage.disks.0.partitions.0.boot_code.image_b64"]);
    assert_eq!(f[1].bytes, 1500);
    assert_eq!(f[1].crc32, bko_holt::bytes::crc32(&vec![7u8; 1500]));
    let back = |p: &[&str]| bko_holt::bytes::unbase64(o.at(p).and_then(Value::as_str).unwrap()).unwrap();
    assert_eq!(back(&["storage", "disks", "0", "partitions", "0", "esp_image", "0", "b64"]), filler(1500));
    assert_eq!(back(&["storage", "disks", "0", "boot_code", "code_b64"]).len(), 7);
    // Not boot code: kept.
    assert_eq!(o.at(&["storage", "disks", "0", "boot_code", "first_bytes"]), v.at(&["storage", "disks", "0", "boot_code", "first_bytes"]));
    assert_eq!(o.at(&["boot", "efi", "entries", "0", "load_option_hex"]), v.at(&["boot", "efi", "entries", "0", "load_option_hex"]));
    assert_eq!(o.get("b64"), v.get("b64"));
    assert_eq!(o.at(&["storage", "disks", "0", "partitions", "0", "esp_image", "0", "offset"]).and_then(Value::as_u64), Some(0));
    // Boot code that isn't base64 is named, not emptied.
    let torn = json::parse(r#"{"storage": {"disks": [{"boot_code": {"code_b64": "not base64!"}}]}}"#).unwrap();
    let (mut f, mut bad) = (Vec::new(), Vec::new());
    let o = fill(&torn, "", "", &mut f, &mut bad);
    assert_eq!(bad, ["storage.disks.0.boot_code.code_b64"]);
    assert!(f.is_empty());
    assert_eq!(o, torn);
}

#[test]
fn raw_dumps_come_from_provenance_tsv() {
    let dir = bko_holt::testutil::TempDir::new("corpus-build-raw");
    let raw = dir.path().join("raw");
    for g in ["a", "b"] {
        std::fs::create_dir_all(raw.join(g)).unwrap();
        std::fs::write(raw.join(g).join("src.json"), "{}").unwrap();
    }
    std::fs::create_dir_all(raw.join("empty")).unwrap();
    let tsv = |rows: &str| std::fs::write(raw.join("provenance.tsv"), format!("guest\thost\trun\n{rows}")).unwrap();
    assert!(provenance(&raw).unwrap_err().contains("provenance.tsv"), "none there");
    tsv("a\tchopin\tr4all\nb\tcinqcent\tr4all\n");
    let p = provenance(&raw).unwrap();
    assert_eq!(p["b"]["host"], "cinqcent");
    assert_eq!(raw_guests(&raw, &p).unwrap(), ["a", "b"]);
    // b's dump from an earlier collection, which this one didn't list.
    tsv("a\tchopin\tr4all\n");
    let e = raw_guests(&raw, &provenance(&raw).unwrap()).unwrap_err();
    assert!(e.contains("doesn't list (an earlier collection's?): b"), "{e}");
    // A listed guest without its dump.
    tsv("a\tchopin\tr4all\nb\tchopin\tr4all\nc\tchopin\tr4all\n");
    assert!(raw_guests(&raw, &provenance(&raw).unwrap()).unwrap_err().contains("without GUEST/src.json: c"));
    // Malformed: two rows for a guest, a row without one, no guest column.
    tsv("a\tchopin\tr4all\na\tcinqcent\tr4all\n");
    assert!(provenance(&raw).unwrap_err().contains("two rows for a"));
    tsv("\tchopin\tr4all\n");
    assert!(provenance(&raw).unwrap_err().contains("line 2 names no guest"));
    std::fs::write(raw.join("provenance.tsv"), "host\trun\n").unwrap();
    assert!(provenance(&raw).unwrap_err().contains("no guest column"));
    assert_eq!(dumps_in(&dir.path().join("nosuch")).unwrap(), Vec::<String>::new());
}
