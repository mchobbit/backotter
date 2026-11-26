//! What `golden_plans.rs` and `corpus_build.rs` share: how a corpus dump is
//! planned, the golden text of a plan, and how two plans differ by stage.
//!
//! The planning is `bugreport::check_plan`'s, with the public API only: a
//! fake rescue system (`testutil::Tree`) with a blank disk sd{q+i} of each
//! dumped disk's size (GROW bytes more for the `+bigger` variant, as
//! ht1.sh's bigger rebuilds onto disks 1 GiB bigger), `--map NAME=TARGET`,
//! `--repo /srv/bko-repo`, `--key-file NAME=/srv/bko-keys/NAME` for each
//! ZFS encryption root with a passphrase and each LUKS2 header the dump kept
//! (with `--luks-header-dir` and a test's header files), a rustic-bko whose `--version` says it's
//! `BackOtter`'s, and every tool the plan asks for installed.  Three things
//! differ from `check_plan`, for plans that read the same on every build:
//! RAM disks and ZFS volumes get no target (load leaves them out, and a
//! --map for one is a Problem), load's own binary (the test's) is installed
//! where it runs from, and the plan names it `bko-holt`.  It uses only
//! `bko_holt::{testutil::Tree, sys::{Sys, FakeRunner}, load::{parse_args,
//! run, Parsed, layout::Layout, plan::check}, json}`, which are as they were
//! before 3671e96, so the test runs on older trees too (the replay of #16).

#![allow(dead_code)]

pub(crate) mod invariants;

use bko_holt::json::{self, Value};
use bko_holt::load::{Parsed, parse_args, run};
use bko_holt::sys::{FakeRunner, Sys};
use bko_holt::testutil::Tree;

/// The fake archiver's answer to `--version`.
pub(crate) const RUSTIC_VERSION: &str = "rustic 0.11.4+backotter.4\n";
/// The repository every golden plan restores from.
pub(crate) const REPO: &str = "/srv/bko-repo";
/// Where a golden plan's --key-file keys are (a ZFS passphrase root's).
pub(crate) const KEYS: &str = "/srv/bko-keys";
/// Where a golden plan's kept LUKS2 headers are (--luks-header-dir), as the
/// plan names it.
pub(crate) const HEADERS: &str = "/srv/bko-luks-headers";
/// The layout's name in the plan's first line and its "To run it" line.
pub(crate) const LAYOUT: &str = "layout.json";
/// The +bigger variant's targets: this much bigger than the source's disks.
pub(crate) const BIGGER: u64 = 1 << 30;

/// One plan: load's exit status, standard output and standard error, and
/// the disk mapping it ran with (source disk, target, target bytes).
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Planned {
    pub code: u8,
    pub out: String,
    pub err: String,
    pub maps: Vec<(String, String, u64)>,
}

/// The dumped disks load maps: (name, size in bytes, logical sector size).
/// Like load's `Layout::from_json`, and unlike `check_plan`, it leaves out RAM
/// disks and ZFS volumes: a --map for one is only a Problem ("the layout has
/// no disk ram0"), and alpine324 has 16 of them.
pub(crate) fn dumped_disks(v: &Value) -> Vec<(String, u64, u32)> {
    let mut out = Vec::new();
    for d in v
        .at(&["storage", "disks"])
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let Some(name) = d.get("name").and_then(Value::as_str) else {
            continue;
        };
        if d.get("kind").and_then(Value::as_str) == Some("ram")
            || d.get("links")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .any(|l| l.starts_with("/dev/zvol/"))
        {
            continue;
        }
        let size = d
            .get("size_bytes")
            .and_then(Value::as_u64)
            .unwrap_or(1 << 34);
        let ss = d
            .get("logical_sector_size")
            .and_then(Value::as_u64)
            .unwrap_or(512) as u32;
        out.push((name.to_string(), size, ss));
    }
    out
}

/// Plans `LAYOUT_TEXT` onto blank targets GROW bytes bigger than its disks.
pub(crate) fn plan(layout_text: &str, grow: u64) -> Result<Planned, String> {
    plan_onto(layout_text, &|_, size| size + grow, &[])
}

/// Plans `LAYOUT_TEXT` onto blank targets of `SIZE_OF(disk`, its size) bytes,
/// with EXTRA arguments after load's own (`BackShot2`'s tier 0: a drifted
/// layout, its case's targets and load options).  The targets are
/// sdq, sdr, ... (`sd_target`).
pub(crate) fn plan_onto(
    layout_text: &str,
    size_of: &dyn Fn(&str, u64) -> u64,
    extra: &[String],
) -> Result<Planned, String> {
    plan_onto_named(layout_text, size_of, extra, &sd_target)
}

/// The I-th dumped disk's target in golden plans: sdq, sdr, ..., sdz.
pub(crate) fn sd_target(i: usize, _disk: &str) -> Result<String, String> {
    if i >= 10 {
        return Err(format!("{} disks: the targets sdq..sdz hold 10", i + 1));
    }
    Ok(format!("sd{}", (b'q' + i as u8) as char))
}

/// `plan_onto`, with the I-th dumped disk DISK's target named `TARGET_NAME(I`,
/// DISK): `BackShot2`'s tier 0 names them as its tier 1 rescuer has them
/// (vdb, vdc, ...: virtio disks after the rescuer's own vda), since load's
/// Slackware check judges a target by its name's driver class and place.
/// A vd target's device number is `virtio_blk`'s (major 253), any other's
/// sd's (major 8); load reads neither's major.
pub(crate) fn plan_onto_named(
    layout_text: &str,
    size_of: &dyn Fn(&str, u64) -> u64,
    extra: &[String],
    target_name: &dyn Fn(usize, &str) -> Result<String, String>,
) -> Result<Planned, String> {
    let v = json::parse(layout_text).map_err(|e| format!("not JSON: {}", e.message))?;
    let t = Tree::new("golden-plan");
    t.file("/proc/self/mountinfo", "")
        .file("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n");
    // A rescue system of the dump's own architecture, which load holds it
    // to (docs/design/arm64.md, A3): the kernel's name, as os.machine (or,
    // before A3, os.architecture) gives it; x86_64 for a dump with neither.
    let machine = ["machine", "architecture"]
        .iter()
        .find_map(|k| v.at(&["os", k]).and_then(Value::as_str))
        .unwrap_or("x86_64");
    t.file("/proc/sys/kernel/arch", format!("{machine}\n"));
    let mut args = vec![LAYOUT.to_string()];
    let mut maps = Vec::new();
    for (i, (name, size, ss)) in dumped_disks(&v).into_iter().enumerate() {
        let tgt = target_name(i, &name)?;
        if tgt.is_empty()
            || !tgt
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
            || maps.iter().any(|m: &(String, String, u64)| m.1 == tgt)
        {
            return Err(format!(
                "disk {name}'s target {tgt:?} isn't a kernel disk name of its own"
            ));
        }
        let bytes = size_of(&name, size);
        let major = if tgt.starts_with("vd") { 253 } else { 8 };
        t.disk(&tgt, &format!("{major}:{}", 16 * i), &[], ss).file(
            &format!("/sys/block/{tgt}/size"),
            format!("{}\n", bytes / 512),
        );
        args.push("--map".into());
        args.push(format!("{name}={tgt}"));
        maps.push((name, tgt, bytes));
    }
    args.extend(["--repo".to_string(), REPO.to_string()]);
    // A ZFS encryption root with a passphrase (void-zbm-enc's zroot) gets
    // its key from --key-file, as HoltTest1 gives the guest's: a fixed test
    // path, KEYS/NAME, never a real key.  Raw and hex keys (Ubuntu's
    // keystore) plan without one, as before.
    let mut keys = Vec::new();
    for ds in v
        .at(&["storage", "zfs", "tools", "datasets"])
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let (Some(name), Some(root)) = (
            ds.get("name").and_then(Value::as_str),
            ds.get("encryptionroot").and_then(Value::as_str),
        ) else {
            continue;
        };
        if name == root && ds.get("keyformat").and_then(Value::as_str) == Some("passphrase") {
            let f = format!("{KEYS}/{}", name.replace('/', "_"));
            args.extend(["--key-file".to_string(), format!("{name}={f}")]);
            keys.push(f);
        }
    }
    // A LUKS2 header the dump kept (storage.luks_headers: Ubuntu Core's
    // FDE, docs/design/ubuntu-core.md) is restored from the file dump wrote
    // and opened with a key it already has, from --key-file by its LUKS
    // label: a fixed test path, KEYS/LABEL, as for ZFS above.  The header
    // files are a test's: each one a line naming it, in HEADERS of the fake
    // rescue system, given with --luks-header-dir and named HEADERS in the
    // plan; the layout planned holds their SHA-256 in place of the real
    // headers' (load checks a header file by it, and the corpus holds no
    // header).  Never a real header or key.
    let mut layout_text = layout_text.to_string();
    let mut headers = Vec::new();
    for h in v
        .at(&["storage", "luks_headers"])
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let Some(file) = h.get("file").and_then(Value::as_str) else {
            continue;
        };
        let name = h
            .get("label")
            .and_then(Value::as_str)
            .or_else(|| h.get("device").and_then(Value::as_str))
            .unwrap_or(file);
        let f = format!("{KEYS}/{}", name.replace('/', "_"));
        if !keys.contains(&f) {
            args.extend(["--key-file".to_string(), format!("{name}={f}")]);
            keys.push(f);
        }
        let body = format!("golden-plan test LUKS2 header {file}\n");
        if let Some(real) = h
            .get("sha256")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
        {
            layout_text = layout_text.replace(real, &sha256_hex(body.as_bytes()));
        }
        headers.push((file.to_string(), body));
    }
    let header_dir = t.p(HEADERS).to_string_lossy().into_owned();
    if !headers.is_empty() {
        args.extend(["--luks-header-dir".to_string(), header_dir.clone()]);
        for (file, body) in &headers {
            t.file(&format!("{HEADERS}/{file}"), body.as_str());
        }
    }
    let layout_text = layout_text.as_str();
    args.extend(extra.iter().cloned());
    let opts = match parse_args(&args) {
        Ok(Parsed::Run(o)) => o,
        Ok(Parsed::Help) => return Err(format!("load's arguments {args:?} asked for help")),
        Err(e) => return Err(format!("load's arguments {args:?}: {e}")),
    };
    t.file("/usr/sbin/rustic-bko", "");
    // Each key's mode pinned to 0644, not the umask's: a --json plan
    // refuses a key group or others may read and its text names the mode
    // (key_file_unsafe), so under umask 002 the blessed plans would read
    // 664.  The file check fails first, so KEYS's own mode never shows.
    for f in &keys {
        t.file(f, "golden-plan test key\n");
        std::fs::set_permissions(t.p(f), std::os::unix::fs::PermissionsExt::from_mode(0o644))
            .unwrap();
    }
    // The rescue image carries OpenZFS's feature sets (zpool create -o
    // compatibility=grub2, e.g. TrueNAS's boot-pool).
    t.file("/usr/share/zfs/compatibility.d/grub2", "");
    // ZFSBootMenu's guide's pool (void-zbm): OpenZFS 2.3 and later ship it.
    t.file("/usr/share/zfs/compatibility.d/openzfs-2.3-linux", "");
    // load runs itself (write-table, md-lbs) as std::env::current_exe(),
    // which is this test's binary: installed where it is, as a rescue
    // system has bko-holt, and named `bko-holt` in the plan, which would
    // otherwise hold the build's own path.
    let exe = std::env::current_exe()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default();
    if exe.starts_with('/') {
        t.file(&exe, "");
    }
    let sys = || {
        Sys::new(
            t.root(),
            Box::new(FakeRunner::default().with("/usr/sbin/rustic-bko --version", RUSTIC_VERSION)),
            true,
        )
    };
    // Every tool the plan asks for, installed as a rescue system has it: a
    // path where it says, a name in /usr/sbin.
    let mut res = run(&opts, layout_text, &sys());
    for _ in 0..4 {
        // A --json plan (plan_json.rs): its document's text, which is text
        // mode's plan of the same arguments.
        let doc = if res.1.starts_with('{') {
            json::parse(&res.1).ok()
        } else {
            None
        };
        let text = doc
            .as_ref()
            .and_then(|d| d.get("text"))
            .and_then(Value::as_str)
            .unwrap_or(&res.1);
        let missing: Vec<String> = text
            .lines()
            .filter_map(|l| {
                l.trim_start_matches("#   - ")
                    .split_once(" isn't installed here")
                    .map(|(p, _)| p.to_string())
            })
            .collect();
        if missing.is_empty() {
            break;
        }
        for m in missing {
            if m.starts_with('/') {
                t.file(&m, "");
            } else {
                t.file(
                    &format!("/usr/sbin/{}", m.rsplit('/').next().unwrap_or(&m)),
                    "",
                );
            }
        }
        res = run(&opts, layout_text, &sys());
    }
    let named = |s: String| {
        let s = if headers.is_empty() {
            s
        } else {
            s.replace(&header_dir, HEADERS)
        };
        if exe.len() > 1 {
            s.replace(&exe, "bko-holt")
        } else {
            s
        }
    };
    let mut out = named(res.1);
    // What `status` looks for after each stage: load builds it with the
    // commands but never prints it, and a change to it moves no command.
    if let Ok(l) = bko_holt::load::layout::Layout::from_json(v.clone()) {
        let b = bko_holt::load::plan::check(&l, &opts, &sys()).built;
        if !out.is_empty() && !out.ends_with('\n') {
            out.push('\n');
        }
        for (src, node) in &b.made {
            out += &format!("{GOLDEN_MARK} status made {src} {node}\n");
        }
        for p in &b.pools {
            out += &format!("{GOLDEN_MARK} status pool {p}\n");
        }
        for (node, ty, uuid) in &b.filesystems {
            out += &format!(
                "{GOLDEN_MARK} status filesystem {node} {ty} {}\n",
                uuid.as_deref().unwrap_or("-")
            );
        }
        for d in &b.datasets {
            out += &format!("{GOLDEN_MARK} status dataset {d}\n");
        }
        for m in &b.mount_points {
            out += &format!("{GOLDEN_MARK} status mount {m}\n");
        }
    }
    Ok(Planned {
        code: res.0,
        out,
        err: named(res.2),
        maps,
    })
}

/// SHA-256 of DATA in lower-case hex (FIPS 180-4).  Here, not holt's
/// `bytes::sha256_hex`, which is newer than 3671e96 (the replay of #16).
pub(crate) fn sha256_hex(data: &[u8]) -> String {
    const K: [u32; 64] = [
        0x428a_2f98,
        0x7137_4491,
        0xb5c0_fbcf,
        0xe9b5_dba5,
        0x3956_c25b,
        0x59f1_11f1,
        0x923f_82a4,
        0xab1c_5ed5,
        0xd807_aa98,
        0x1283_5b01,
        0x2431_85be,
        0x550c_7dc3,
        0x72be_5d74,
        0x80de_b1fe,
        0x9bdc_06a7,
        0xc19b_f174,
        0xe49b_69c1,
        0xefbe_4786,
        0x0fc1_9dc6,
        0x240c_a1cc,
        0x2de9_2c6f,
        0x4a74_84aa,
        0x5cb0_a9dc,
        0x76f9_88da,
        0x983e_5152,
        0xa831_c66d,
        0xb003_27c8,
        0xbf59_7fc7,
        0xc6e0_0bf3,
        0xd5a7_9147,
        0x06ca_6351,
        0x1429_2967,
        0x27b7_0a85,
        0x2e1b_2138,
        0x4d2c_6dfc,
        0x5338_0d13,
        0x650a_7354,
        0x766a_0abb,
        0x81c2_c92e,
        0x9272_2c85,
        0xa2bf_e8a1,
        0xa81a_664b,
        0xc24b_8b70,
        0xc76c_51a3,
        0xd192_e819,
        0xd699_0624,
        0xf40e_3585,
        0x106a_a070,
        0x19a4_c116,
        0x1e37_6c08,
        0x2748_774c,
        0x34b0_bcb5,
        0x391c_0cb3,
        0x4ed8_aa4a,
        0x5b9c_ca4f,
        0x682e_6ff3,
        0x748f_82ee,
        0x78a5_636f,
        0x84c8_7814,
        0x8cc7_0208,
        0x90be_fffa,
        0xa450_6ceb,
        0xbef9_a3f7,
        0xc671_78f2,
    ];
    let mut h: [u32; 8] = [
        0x6a09_e667,
        0xbb67_ae85,
        0x3c6e_f372,
        0xa54f_f53a,
        0x510e_527f,
        0x9b05_688c,
        0x1f83_d9ab,
        0x5be0_cd19,
    ];
    let mut m = data.to_vec();
    m.push(0x80);
    while m.len() % 64 != 56 {
        m.push(0);
    }
    m.extend_from_slice(&((data.len() as u64) * 8).to_be_bytes());
    for block in m.chunks(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                block[4 * i],
                block[4 * i + 1],
                block[4 * i + 2],
                block[4 * i + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let mut v = h;
        for i in 0..64 {
            let s1 = v[4].rotate_right(6) ^ v[4].rotate_right(11) ^ v[4].rotate_right(25);
            let ch = (v[4] & v[5]) ^ (!v[4] & v[6]);
            let t1 = v[7]
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = v[0].rotate_right(2) ^ v[0].rotate_right(13) ^ v[0].rotate_right(22);
            let maj = (v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]);
            let t2 = s0.wrapping_add(maj);
            v = [
                t1.wrapping_add(t2),
                v[0],
                v[1],
                v[2],
                v[3].wrapping_add(t1),
                v[4],
                v[5],
                v[6],
            ];
        }
        for (a, b) in h.iter_mut().zip(v) {
            *a = a.wrapping_add(b);
        }
    }
    h.iter().map(|x| format!("{x:08x}")).collect()
}

/// Writes TEXT to PATH whole or not at all: a `.NAME.part` beside it, then a
/// rename, so a killed run leaves the old file or the new one, never half.
pub(crate) fn write_atomic(path: &std::path::Path, text: &[u8]) -> Result<(), String> {
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let tmp = path.with_file_name(format!(".{name}.part"));
    std::fs::write(&tmp, text).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))
}

/// The marker of `golden_plans.rs`'s own lines after load's output.
pub(crate) const GOLDEN_MARK: &str = "# golden_plans.rs:";

/// A plan as plan.txt holds it: load's standard output as it is, then the
/// exit status and standard error.
pub(crate) fn golden_text(p: &Planned) -> String {
    let mut s = p.out.clone();
    if !s.is_empty() && !s.ends_with('\n') {
        s.push('\n');
    }
    if p.err.is_empty() {
        s += &format!("{GOLDEN_MARK} exit {}, nothing on stderr\n", p.code);
    } else {
        s += &format!("{GOLDEN_MARK} exit {}; stderr:\n", p.code);
        for l in p.err.lines() {
            s += &format!("#   {l}\n");
        }
    }
    s
}

/// load's stages, in the order plans print them.
pub(crate) const STAGES: [&str; 7] = [
    "partition",
    "assemble",
    "format",
    "mount",
    "restore",
    "boot",
    "close",
];

/// The name in a stage header line, `# ---- STAGE ----`.
pub(crate) fn stage_header(line: &str) -> Option<&str> {
    let name = line.strip_prefix("# ---- ")?.strip_suffix(" ----")?;
    (!name.is_empty()
        && name
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-'))
    .then_some(name)
}

/// TEXT split into its sections: `header` (everything before the first
/// stage header), each stage (from its header line), and `trailer`: from the
/// first line after the last stage header (anywhere, without one) that
/// starts "# Nothing was written" (plan.rs's last line) or is
/// `golden_text`'s own, to the end.  testvm/test2/t2lib/plandiff.py splits the
/// same way.
pub(crate) fn sections(text: &str) -> Vec<(String, Vec<&str>)> {
    let lines: Vec<&str> = text.lines().collect();
    let last_stage = lines.iter().rposition(|l| stage_header(l).is_some());
    let from = last_stage.map(|i| i + 1).unwrap_or(0);
    let trailer = (from..lines.len()).find(|&i| {
        lines[i].starts_with("# Nothing was written") || lines[i].starts_with(GOLDEN_MARK)
    });
    let mut out: Vec<(String, Vec<&str>)> = vec![("header".to_string(), Vec::new())];
    for (i, l) in lines.iter().enumerate() {
        if Some(i) == trailer {
            out.push(("trailer".to_string(), Vec::new()));
        } else if let Some(name) = stage_header(l).filter(|_| trailer.is_none_or(|t| i < t)) {
            out.push((name.to_string(), Vec::new()));
        }
        out.last_mut().unwrap().1.push(l);
    }
    out
}

/// The sections whose lines differ between OLD and NEW (one only in either
/// counts), in the order: header, the stages as STAGES orders them, any
/// other stage by first appearance, trailer.
pub(crate) fn stages_changed(old: &str, new: &str) -> Vec<String> {
    let (a, b) = (sections(old), sections(new));
    let mut names: Vec<String> = vec!["header".into()];
    names.extend(STAGES.iter().map(|s| s.to_string()));
    for (n, _) in a.iter().chain(b.iter()) {
        if !names.contains(n) && n != "trailer" {
            names.push(n.clone());
        }
    }
    names.push("trailer".into());
    let get = |s: &[(String, Vec<&str>)], n: &str| -> Option<Vec<String>> {
        let parts: Vec<&Vec<&str>> = s.iter().filter(|(m, _)| m == n).map(|(_, l)| l).collect();
        (!parts.is_empty()).then(|| parts.into_iter().flatten().map(|l| l.to_string()).collect())
    };
    names
        .into_iter()
        .filter(|n| get(&a, n) != get(&b, n))
        .collect()
}

/// How many lines a line diff of OLD and NEW removes and adds (the longest
/// common subsequence kept).
pub(crate) fn lines_changed(old: &str, new: &str) -> usize {
    let a: Vec<&str> = old.lines().collect();
    let b: Vec<&str> = new.lines().collect();
    let pre = a.iter().zip(&b).take_while(|(x, y)| x == y).count();
    let suf = a[pre..]
        .iter()
        .rev()
        .zip(b[pre..].iter().rev())
        .take_while(|(x, y)| x == y)
        .count();
    let (a, b) = (&a[pre..a.len() - suf], &b[pre..b.len() - suf]);
    if a.is_empty() || b.is_empty() {
        return a.len() + b.len();
    }
    // LCS by rows; plans are a few thousand lines at most.
    let mut prev = vec![0u32; b.len() + 1];
    let mut cur = vec![0u32; b.len() + 1];
    for x in a {
        for (j, y) in b.iter().enumerate() {
            cur[j + 1] = if x == y {
                prev[j] + 1
            } else {
                prev[j + 1].max(cur[j])
            };
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    a.len() + b.len() - 2 * prev[b.len()] as usize
}
