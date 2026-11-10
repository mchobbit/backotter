//! golden_plans.rs's five invariants (its header says what each holds), and
//! how they read a plan: moved here from golden_plans.rs unchanged, so that
//! BackShot2's tier 0 (bs2_plan.rs) holds a drifted layout's plan to the same
//! ones.  Like the rest of common/, it uses only holt API as it was before
//! 3671e96.

#![allow(dead_code)]

use super::Planned;
use bko_holt::bytes::ByteSource;
use bko_holt::json::Value;

/// Where load's mount stage builds the restored tree (load::DEFAULT_TARGET).
pub const TARGET: &str = "/mnt/bko-target";

/// What the invariants found: violations, and what they left out on purpose.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct Check {
    pub violations: Vec<String>,
    pub skipped: Vec<String>,
    /// What each invariant looked at, for the log.
    pub checked: Vec<String>,
}

// ---- reading a plan ----

/// One command of a stage, with its here-document's lines.
#[derive(Debug, Clone)]
pub struct Step {
    pub cmd: String,
    pub body: Vec<String>,
}

impl Step {
    pub fn text(&self) -> String {
        std::iter::once(self.cmd.clone()).chain(self.body.iter().cloned()).collect::<Vec<_>>().join("\n")
    }
}

/// The plan's sections by name (super::sections).
pub struct Sections<'a>(pub Vec<(String, Vec<&'a str>)>);

impl<'a> Sections<'a> {
    pub fn of(text: &'a str) -> Sections<'a> {
        Sections(super::sections(text))
    }

    pub fn lines(&self, name: &str) -> Vec<&'a str> {
        self.0.iter().filter(|(n, _)| n == name).flat_map(|(_, l)| l.iter().copied()).collect()
    }

    /// A stage's commands: its lines less the header, comments outside a
    /// here-document, and blank lines.
    pub fn steps(&self, stage: &str) -> Vec<Step> {
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
            out.push(Step { cmd: l.to_string(), body: Vec::new() });
        }
        out
    }

    /// The header's list under TITLE ("# Problems", "# Notes:"): its
    /// "#   - " lines.
    pub fn list(&self, title: &str) -> Vec<String> {
        let head = self.lines("header");
        let Some(i) = head.iter().position(|l| l.starts_with(title)) else { return vec![] };
        head[i + 1..].iter().take_while(|l| l.starts_with("#   - ") || l.starts_with("#     ")).filter_map(|l| l.strip_prefix("#   - ")).map(str::to_string).collect()
    }
}

/// A command line's words, quotes and separators trimmed: good enough to
/// find paths and names in it.
pub fn words(s: &str) -> Vec<String> {
    s.split_whitespace().map(|w| w.trim_matches(|c| c == '\'' || c == '"' || c == ';' || c == '(' || c == ')').to_string()).filter(|w| !w.is_empty()).collect()
}

/// A program's name: a word's last path part (/sbin/mount is mount).
pub fn prog(w: &str) -> &str {
    w.rsplit('/').next().unwrap_or(w)
}

/// Shell words that open or close a compound command, or run the command
/// after them, without being a command themselves.
pub const LEADERS: [&str; 18] = ["if", "then", "else", "elif", "fi", "do", "done", "while", "until", "esac", "!", "{", "}", "(", ")", "exec", "time", "command"];

/// The simple commands of a plan line, each as its words with quotes
/// trimmed and redirections (`>FILE`, `2>&1`, `> FILE`) left out: the line
/// itself, or, for `sh -c 'SCRIPT'`, each command of SCRIPT.  Commands are
/// split at ;, &&, ||, | and newlines, and lose a leading if, do, {, done and
/// the like, and VAR=value assignments.
pub fn commands(line: &str) -> Vec<Vec<String>> {
    let t = line.trim();
    let script = match t.strip_prefix("sh -c ") {
        Some(rest) => rest.trim().replace("'\\''", "'"),
        None => t.to_string(),
    };
    let script = script.replace("&&", ";").replace("||", ";");
    let mut out = Vec::new();
    for piece in script.split([';', '|', '\n']) {
        let mut w: Vec<String> = Vec::new();
        let mut file_next = false;
        for x in piece.split_whitespace() {
            if file_next {
                file_next = false;
                continue;
            }
            let x = x.trim_matches(|c| c == '\'' || c == '"');
            if x.is_empty() {
                continue;
            }
            if x.contains('>') || x.contains('<') {
                // A redirection; `>` (`2>`, `&>`, `>>`, `<`) on its own takes the next word.
                file_next = x.trim_start_matches(|c: char| c.is_ascii_digit() || c == '&').chars().all(|c| c == '>' || c == '<');
                continue;
            }
            w.push(x.to_string());
        }
        while w.first().is_some_and(|x| LEADERS.contains(&x.as_str()) || (x.contains('=') && !x.starts_with('-') && !x.starts_with('/'))) {
            w.remove(0);
        }
        if !w.is_empty() {
            out.push(w);
        }
    }
    out
}

/// Whether STEP mounts something onto PATH: a `mount` (or mount_*, or
/// Redox's redoxfs) whose last operand is PATH.
pub fn mounts_at(s: &Step, path: &str) -> bool {
    commands(&s.cmd).iter().any(|c| {
        let p = prog(&c[0]);
        (p == "mount" || p.starts_with("mount_") || p == "redoxfs") && c.len() >= 3 && c.last().is_some_and(|l| l == path)
    })
}

/// The datasets STEP mounts with `zfs mount DATASET`.
pub fn zfs_mounted(s: &Step) -> Vec<String> {
    commands(&s.cmd).into_iter().filter(|c| c.len() >= 3 && prog(&c[0]) == "zfs" && c[1] == "mount").filter_map(|c| c.last().cloned()).collect()
}

/// Whether STEP restores the repository into PATH: `rustic-bko ... restore
/// ... PATH`.
pub fn restores_into(s: &Step, path: &str) -> bool {
    commands(&s.cmd).iter().any(|c| prog(&c[0]) == "rustic-bko" && c.iter().skip(1).any(|w| w == "restore") && c.last().is_some_and(|l| l == path))
}

/// Some(rooted) when STEP is a `zpool create` or `zpool import` of POOL;
/// rooted when it roots the pool at the target (-R, altroot=).
pub fn zpool_makes(s: &Step, pool: &str) -> Option<bool> {
    commands(&s.cmd).into_iter().find(|c| c.len() >= 3 && prog(&c[0]) == "zpool" && (c[1] == "create" || c[1] == "import") && c.iter().skip(2).any(|w| w == pool)).map(|c| c.windows(2).any(|p| p[0] == "-R" && p[1] == TARGET) || c.iter().any(|x| *x == format!("altroot={TARGET}")))
}

pub fn str_at<'a>(v: &'a Value, k: &str) -> Option<&'a str> {
    v.get(k).and_then(Value::as_str)
}

pub fn arr<'a>(v: &'a Value, path: &[&str]) -> &'a [Value] {
    v.at(path).and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[])
}

// ---- the invariants ----

/// The ZFS datasets the dump lists: (name, mountpoint).
pub fn datasets(dump: &Value) -> Vec<(String, String)> {
    arr(dump, &["storage", "zfs", "tools", "datasets"])
        .iter()
        .filter_map(|d| Some((str_at(d, "name")?.to_string(), str_at(d, "mountpoint").unwrap_or("").to_string())))
        .collect()
}

/// Whether a pool-making step (zpool create or import) of POOL roots it at
/// the target.
pub fn pool_rooted(assemble: &[Step], pool: &str) -> bool {
    assemble.iter().any(|s| zpool_makes(s, pool) == Some(true))
}

pub fn root_mounted(dump: &Value, sec: &Sections) -> Option<String> {
    root_mounted_by(dump, sec).err()
}

/// How the mount stage mounts / ("mount", "zfs mount"), or the violation.
pub fn root_mounted_by(dump: &Value, sec: &Sections) -> Result<&'static str, String> {
    let mount = sec.steps("mount");
    // A mount, or redoxfs, onto the target itself.
    if mount.iter().any(|s| mounts_at(s, TARGET)) {
        return Ok("mount");
    }
    // zfs mount of the / dataset, in a pool under -R TARGET.
    let ds = datasets(dump);
    let assemble = sec.steps("assemble");
    for s in &mount {
        for name in zfs_mounted(s) {
            if ds.iter().any(|(n, mp)| *n == name && mp == "/") && pool_rooted(&assemble, name.split('/').next().unwrap_or("")) {
                return Ok("zfs mount");
            }
        }
    }
    Err(format!("/ mounted: the mount stage mounts nothing on {TARGET} (no mount whose last operand it is, no zfs mount of a / dataset in a pool made with -R {TARGET})"))
}

pub const PSEUDO: [&str; 30] = [
    "proc", "procfs", "sysfs", "tmpfs", "devtmpfs", "devpts", "devfs", "fdescfs", "linprocfs", "linsysfs", "kernfs", "ptyfs", "mfs", "ramfs", "securityfs", "debugfs",
    "efivarfs", "hugetlbfs", "mqueue", "pstore", "bpf", "tracefs", "configfs", "fusectl", "binfmt_misc", "autofs", "cgroup", "cgroup2", "overlay", "swap",
];
pub const NETWORK: [&str; 9] = ["nfs", "nfs4", "cifs", "smbfs", "smb3", "sshfs", "fuse.sshfs", "9p", "virtiofs"];
pub const BIND: [&str; 2] = ["nullfs", "lofs"];

/// fstab's octal escapes (\040 for a space).
pub fn unescape(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\\' && i + 4 <= b.len() && b[i + 1..i + 4].iter().all(|c| (b'0'..=b'7').contains(c)) {
            out.push((b[i + 1] - b'0') * 64 + (b[i + 2] - b'0') * 8 + (b[i + 3] - b'0'));
            i += 4;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The fstab invariant: violations, skipped entries, entries checked.
pub fn fstab_planned_n(dump: &Value, sec: &Sections) -> (Vec<String>, Vec<String>, usize) {
    let (mut bad, mut skipped, mut n) = (Vec::new(), Vec::new(), 0);
    let mount = sec.steps("mount");
    let notes = sec.list("# Notes:");
    let ds = datasets(dump);
    for e in arr(dump, &["storage", "fstab"]) {
        let spec = str_at(e, "spec").unwrap_or("");
        let file = unescape(str_at(e, "file").unwrap_or(""));
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
        } else if ty.split(',').any(|t| NETWORK.contains(&t)) || spec.starts_with("//") || (spec.contains(':') && !spec.starts_with('/') && !spec.contains('=')) {
            Some("a network filesystem")
        } else if opts.iter().any(|o| *o == "bind" || *o == "rbind") || ty.split(',').any(|t| BIND.contains(&t)) {
            Some("a bind mount")
        } else {
            let device = ["UUID=", "LABEL=", "PARTUUID=", "PARTLABEL=", "NAME=", "/dev/"].iter().any(|p| spec.starts_with(p))
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
        let at = if file == "/" { TARGET.to_string() } else { format!("{TARGET}{}", file.trim_end_matches('/')) };
        let mounted = mount.iter().any(|s| mounts_at(s, &at)) || mount.iter().any(|s| zfs_mounted(s).iter().any(|d| ds.iter().any(|(n, mp)| n == d && *mp == file)));
        let noted = notes.iter().any(|n| n.starts_with(&format!("fstab's {file} ")) || n.starts_with(&format!("fstab's {file}:")));
        if !mounted && !noted {
            bad.push(format!("every fstab mount planned: fstab's {what} is neither mounted at {at} by the mount stage nor named in a Note"));
        }
    }
    (bad, skipped, n)
}

pub fn archiver_found(sec: &Sections) -> Vec<String> {
    let mut bad = Vec::new();
    for p in sec.list("# Problems (") {
        if p.contains("rustic-bko") || p.contains("archiver") || p.contains("restore can't run") || p.contains("restore needs rustic-bko") {
            bad.push(format!("the archiver found: Problem {p:?}"));
        }
    }
    if !sec.steps("restore").iter().any(|s| restores_into(s, TARGET)) {
        bad.push(format!("the archiver found: the restore stage runs no `rustic-bko ... restore ... {TARGET}`"));
    }
    bad
}

/// The dumped disk DEV is, or is a partition (or BSD label partition, or
/// illumos slice) of.
pub fn disk_of(dump: &Value, dev: &str) -> Option<String> {
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
                for q in p.get("label_partitions").and_then(Value::as_array).into_iter().flatten() {
                    if str_at(q, "name") == Some(dev) {
                        return Some(name.to_string());
                    }
                }
            }
        }
    }
    None
}

pub fn target_of<'a>(p: &'a Planned, disk: &str) -> Option<&'a str> {
    p.maps.iter().find(|(s, _, _)| s == disk).map(|(_, t, _)| t.as_str())
}

/// Whether TEXT names a node of target disk TGT (Linux's /dev/sdq1, the
/// BSDs' /dev/sdqp3 and raw /dev/rsdqc, illumos's /dev/dsk/sdqs1, Haiku's
/// /dev/disk/sdq/0).
pub fn names_target(text: &str, tgt: &str) -> bool {
    [format!("/dev/{tgt}"), format!("/dev/r{tgt}"), format!("/dev/dsk/{tgt}"), format!("/dev/rdsk/{tgt}"), format!("/dev/disk/{tgt}/")].iter().any(|f| text.contains(f.as_str()))
}

pub fn pool_disks(dump: &Value, sec: &Sections, p: &Planned) -> Vec<String> {
    pool_disks_n(dump, sec, p).0
}

/// pool_disks, and how many containers it checked.
pub fn pool_disks_n(dump: &Value, sec: &Sections, p: &Planned) -> (Vec<String>, usize) {
    let mut bad = Vec::new();
    let mut n = 0;
    let (assemble, format) = (sec.steps("assemble"), sec.steps("format"));
    let text = |steps: &[&Step]| steps.iter().map(|s| s.text()).collect::<Vec<_>>().join("\n");
    // WHAT's members, each with the text of the commands that must name its
    // disk's target; NONE when no command makes WHAT.
    let mut check = |what: String, members: Vec<(String, String)>, none: bool| {
        n += 1;
        if none {
            bad.push(format!("pool disks in use: {what}: no command makes it"));
            return;
        }
        for (m, t) in members {
            let Some(disk) = disk_of(dump, &m) else { continue };
            match target_of(p, &disk) {
                None => bad.push(format!("pool disks in use: {what}: member {m}'s disk {disk} has no target")),
                Some(tg) if !names_target(&t, tg) => bad.push(format!("pool disks in use: {what}: member {m} is on {disk} -> /dev/{tg}, which the command making it doesn't name")),
                Some(_) => {}
            }
        }
    };
    for a in arr(dump, &["storage", "md_arrays"]) {
        let name = str_at(a, "name").unwrap_or("?");
        let members: Vec<String> = arr(a, &["md", "members"]).iter().filter_map(|m| str_at(m, "device").map(str::to_string)).collect();
        let uuid = a.at(&["md", "uuid"]).and_then(Value::as_str);
        let makers: Vec<&Step> = assemble
            .iter()
            .filter(|s| commands(&s.cmd).iter().any(|c| prog(&c[0]) == "mdadm" && c.get(1).is_some_and(|x| x == "--create") && uuid.is_none_or(|u| c.iter().any(|w| *w == format!("--uuid={u}")) || c.windows(2).any(|w| w[0] == "--uuid" && w[1] == u))))
            .collect();
        let t = text(&makers);
        check(format!("md array {name}"), members.into_iter().map(|m| (m, t.clone())).collect(), makers.is_empty());
    }
    for pool in arr(dump, &["storage", "zfs", "pools_from_labels"]) {
        let name = str_at(pool, "name").unwrap_or("?");
        let members: Vec<String> = arr(pool, &["member_devices"]).iter().filter_map(|m| m.as_str().map(str::to_string)).collect();
        let makers: Vec<&Step> = assemble.iter().filter(|s| zpool_makes(s, name).is_some()).collect();
        let t = text(&makers);
        check(format!("ZFS pool {name}"), members.into_iter().map(|m| (m, t.clone())).collect(), makers.is_empty());
    }
    for vg in arr(dump, &["storage", "lvm", "volume_groups"]) {
        let name = str_at(vg, "name").unwrap_or("?");
        let pvs: Vec<(String, Option<String>)> = arr(vg, &["physical_volumes"]).iter().filter_map(|pv| Some((str_at(pv, "device")?.to_string(), str_at(pv, "id").map(str::to_string)))).collect();
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
                    Some(id) => makers.iter().copied().filter(|s| commands(&s.cmd).iter().any(|c| prog(&c[0]) == "pvcreate" && (c.windows(2).any(|w| w[0] == "--uuid" && w[1] == *id) || c.iter().any(|w| *w == format!("--uuid={id}"))))).collect(),
                    None => Vec::new(),
                };
                (m.clone(), text(if own.is_empty() { &makers } else { &own }))
            })
            .collect();
        check(format!("LVM volume group {name}"), members, makers.is_empty());
    }
    for fs in arr(dump, &["storage", "btrfs"]) {
        let devices: Vec<String> = arr(fs, &["devices"]).iter().filter_map(|d| d.as_str().map(str::to_string)).collect();
        if devices.len() < 2 {
            continue;
        }
        let uuid = str_at(fs, "uuid").unwrap_or("?");
        let makers: Vec<&Step> = format.iter().filter(|s| commands(&s.cmd).iter().any(|c| prog(&c[0]) == "mkfs.btrfs" && c.iter().any(|w| w.eq_ignore_ascii_case(uuid)))).collect();
        let t = text(&makers);
        check(format!("btrfs {uuid}"), devices.into_iter().map(|m| (m, t.clone())).collect(), makers.is_empty());
    }
    (bad, n)
}

/// Writes laid over zeros: what a device holds after load::writetable's.
pub struct Sparse<'a> {
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
            out[(from - offset) as usize..(to - offset) as usize].copy_from_slice(&b[(from - s) as usize..(to - s) as usize]);
        }
        out
    }
}

pub fn mbr_signature(t: &Value) -> Option<u32> {
    match t.get("disk_signature")? {
        Value::Str(s) => u32::from_str_radix(s.trim_start_matches("0x"), 16).ok(),
        x => x.as_u64().map(|n| n as u32),
    }
}

/// DISK's table as WRITES put it on a device of SIZE bytes, read back: the
/// dump's IDs, or what differs.
pub fn table_ids(disk: &Value, writes: &[(u64, Vec<u8>)], size: u64) -> Vec<String> {
    let name = str_at(disk, "name").unwrap_or("?");
    let t = disk.get("partition_table").cloned().unwrap_or(Value::Null);
    let ss = t.get("sector_size").and_then(Value::as_u64).or_else(|| disk.get("logical_sector_size").and_then(Value::as_u64)).unwrap_or(512);
    let src = Sparse { size, writes };
    let mut bad = Vec::new();
    match (str_at(&t, "type"), bko_holt::parttable::read_table(&src, ss)) {
        (Some("gpt"), Some(bko_holt::parttable::Table::Gpt { gpt, .. })) => {
            let want = str_at(&t, "disk_guid").unwrap_or("");
            if !gpt.disk_guid.eq_ignore_ascii_case(want) {
                bad.push(format!("disk IDs round-tripped: {name}: write-table's GPT reads back with disk GUID {} for {want}", gpt.disk_guid));
            }
            for p in arr(disk, &["partitions"]) {
                let (Some(n), Some(u)) = (p.get("number").and_then(Value::as_u64), str_at(p, "uuid")) else { continue };
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

pub fn disk_ids(dump: &Value, sec: &Sections, p: &Planned) -> Vec<String> {
    disk_ids_n(dump, sec, p).0
}

/// disk_ids, and how many tables it checked: (sfdisk, write-table).
pub fn disk_ids_n(dump: &Value, sec: &Sections, p: &Planned) -> (Vec<String>, (usize, usize)) {
    let mut bad = Vec::new();
    let (mut nsf, mut nwt) = (0, 0);
    let part = sec.steps("partition");
    for d in arr(dump, &["storage", "disks"]) {
        let Some(name) = str_at(d, "name") else { continue };
        let t = match d.get("partition_table") {
            Some(t) if !t.is_null() => t,
            _ => continue,
        };
        let Some((_, tgt, bytes)) = p.maps.iter().find(|(s, _, _)| s == name) else { continue };
        let kind = str_at(t, "type").unwrap_or("?");
        // A table load writes itself (other/'s platforms):
        // `bko-holt write-table layout.json NAME DEVICE ...`.
        let devices: Vec<String> = part
            .iter()
            .flat_map(|s| commands(&s.cmd))
            .filter_map(|c| {
                let i = c.iter().position(|w| w == "write-table")?;
                (c.get(i + 1).map(String::as_str) == Some(super::LAYOUT) && c.get(i + 2).map(String::as_str) == Some(name)).then(|| c.get(i + 3).cloned().unwrap_or_default())
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
                Err(e) => bad.push(format!("disk IDs round-tripped: {name}: write-table's plan fails: {e}")),
            }
            continue;
        }
        let Some(sf) = part.iter().find(|s| s.cmd.starts_with("sfdisk") && words(&s.cmd).iter().any(|w| *w == format!("/dev/{tgt}"))) else {
            bad.push(format!("disk IDs round-tripped: {name} ({kind}) -> /dev/{tgt}: the partition stage writes no table on it"));
            continue;
        };
        nsf += 1;
        let label_id = sf.body.iter().find_map(|l| l.strip_prefix("label-id: ")).unwrap_or("");
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
pub fn invariants(dump: &Value, p: &Planned) -> Check {
    let mut c = Check::default();
    if p.out.is_empty() {
        c.violations.push(format!("load made no plan (exit {}): {}", p.code, p.err.lines().next().unwrap_or("")));
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
    c.checked.push(format!("{} tables ({nsf} sfdisk, {nwt} write-table)", nsf + nwt));
    c
}

// ---- the run ----

