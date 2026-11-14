//! OpenBSD's softraid volumes (docs/design/openbsd-crypto.md).  `dump`
//! reports each volume `bioctl softraid0` lists as a virtual disk on its
//! chunks (storage.softraid_volumes), with its level, its chunks, what
//! unlocks a CRYPTO one and its KDF rounds (read off the chunk's softraid
//! metadata), and the volume's own MBR, disklabel and partitions as a
//! plain disk's.  No key material is read or kept.
//!
//! `load` makes a CRYPTO volume again on its chunk's new partition with
//! `bioctl -c C`, gives it its own MBR (`fdisk -iy`) and the source's
//! disklabel with its DUID, and names its partitions by that DUID
//! (739d98841b65e745.a), which newfs and mount take as they take a device:
//! the volume's own device (sdN) is known only once bioctl has made it, so
//! the few commands that need it (fdisk, disklabel, installboot) find it as
//! install.sub does, from `bioctl softraid0`'s Device column, when they run.
//! close leaves the volume attached (bioctl -d would clear its bootable
//! flag).  Other levels and key-disk volumes are dumped but refused.

use super::labelled::disk_names;
use super::Family;
use crate::json::{Obj, Value};
use crate::load::commands::{Builder, Cmd};
use crate::load::layout::Layout;
use crate::load::RUN_DIR;
use crate::sys::Sys;

// ---- dump -------------------------------------------------------------

/// A volume as `bioctl softraid0` lists it.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Volume {
    pub controller: Option<String>,
    pub id: Option<u32>,
    pub status: String,
    /// In bytes (bioctl without -h).
    pub size: Option<u64>,
    /// Its disk (sd1).
    pub device: String,
    /// CRYPTO, RAID0, RAID1, RAID5, CONCAT, RAID1C.
    pub level: String,
    /// The data chunks (sd0a).
    pub chunks: Vec<String>,
    /// A CRYPTO volume's key disk, which bioctl lists as a chunk whose size
    /// is "key disk".
    pub key_disk: Option<String>,
}

fn is_sd(w: &str) -> bool {
    w.strip_prefix("sd").is_some_and(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()))
}

/// `bioctl softraid0`: a volume line ("softraid0 0 Online 17179566080 sd1
/// CRYPTO"), then a line per chunk ("0 Online 17179566080 0:0.0 noencl
/// <sd0a>").
pub fn parse_bioctl(text: &str) -> Vec<Volume> {
    let mut out: Vec<Volume> = Vec::new();
    for line in text.lines() {
        let w: Vec<&str> = line.split_whitespace().collect();
        if let Some(c) = w.iter().rev().find(|x| x.len() > 2 && x.starts_with('<') && x.ends_with('>')) {
            let Some(v) = out.last_mut() else { continue };
            let dev = c[1..c.len() - 1].to_string();
            if line.contains("key disk") {
                v.key_disk = Some(dev);
            } else {
                v.chunks.push(dev);
            }
            continue;
        }
        // The volume's line: its device, after status and size.
        let Some(i) = w.iter().position(|x| is_sd(x)).filter(|&i| i >= 2) else { continue };
        out.push(Volume {
            controller: (i >= 4).then(|| w[i - 4].to_string()),
            id: i.checked_sub(3).and_then(|j| w[j].parse().ok()),
            status: w[i - 2].to_string(),
            size: w[i - 1].parse().ok(),
            device: w[i].to_string(),
            level: w.get(i + 1).map(|l| l.to_string()).unwrap_or_default(),
            ..Volume::default()
        });
    }
    out
}

/// Where a chunk's softraid metadata starts: SR_META_OFFSET, 16 blocks of
/// 512 bytes; and how much of it dump reads (SR_META_SIZE, 64 blocks).
pub const META_OFFSET: u64 = 16 * 512;
pub const META_LEN: usize = 64 * 512;

/// The KDF hint types (SR_CRYPTOKDFT_*).
pub const KDF_PBKDF2: u32 = 1;
pub const KDF_KEYDISK: u32 = 2;
pub const KDF_BCRYPT_PBKDF: u32 = 3;

/// A CRYPTO volume's KDF hint, from its chunk's metadata: the type, and the
/// rounds of a passphrase's (PBKDF2 or bcrypt_pbkdf).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct KdfHint {
    pub kind: u32,
    pub rounds: Option<u32>,
}

impl KdfHint {
    pub fn name(&self) -> Option<&'static str> {
        match self.kind {
            KDF_PBKDF2 => Some("pbkdf2"),
            KDF_KEYDISK => Some("keydisk"),
            KDF_BCRYPT_PBKDF => Some("bcrypt_pbkdf"),
            _ => None,
        }
    }
}

/// The KDF hint in a chunk's softraid metadata (`meta`, from META_OFFSET):
/// struct sr_metadata (168 bytes; magic "marcCRAM", version 6), a struct
/// sr_meta_chunk (92 bytes) per chunk, then the optional metadata, each a
/// header (type, length) and its body.  The CRYPTO one's (type 1) holds the
/// hint 2152 bytes in: len, type, rounds, salt.  None for anything else.
pub fn kdf_hint(meta: &[u8]) -> Option<KdfHint> {
    use crate::bytes::le32;
    const SR_META_VERSION: u32 = 6;
    const SR_OPT_CRYPTO: u32 = 1;
    const HINT_AT: usize = 24 + 4 * 4 + 64 + 32 * 64;
    if meta.len() < 168 || &meta[0..8] != b"marcCRAM" || le32(meta, 8) != SR_META_VERSION {
        return None;
    }
    let chunks = le32(meta, 32) as usize;
    let opts = le32(meta, 40) as usize;
    let mut at = 168 + 92 * chunks;
    for _ in 0..opts.min(16) {
        if at + 8 > meta.len() {
            return None;
        }
        let (ty, len) = (le32(meta, at), le32(meta, at + 4) as usize);
        if ty == SR_OPT_CRYPTO {
            let h = at + HINT_AT;
            if len < HINT_AT + 12 || h + 12 > meta.len() {
                return None;
            }
            let kind = le32(meta, h + 4);
            // struct sr_crypto_pbkdf: len 140 (4 + 4 + 4 + 128 of salt).
            let rounds = (matches!(kind, KDF_PBKDF2 | KDF_BCRYPT_PBKDF) && le32(meta, h) == 140).then(|| le32(meta, h + 8));
            return Some(KdfHint { kind, rounds });
        }
        if len < 8 {
            return None;
        }
        at += len;
    }
    None
}

/// The volume's `softraid` record: what bioctl says of it, what unlocks a
/// CRYPTO one, and its KDF.
pub fn volume_json(v: &Volume, hint: Option<KdfHint>) -> Value {
    let crypto = matches!(v.level.as_str(), "CRYPTO" | "RAID1C");
    let key = crypto.then(|| if v.key_disk.is_some() || hint.is_some_and(|h| h.kind == KDF_KEYDISK) { "keydisk" } else { "passphrase" });
    Obj::new()
        .set("controller", v.controller.clone())
        .set("volume_id", v.id.map(u64::from))
        .set("level", v.level.as_str())
        .set("status", v.status.as_str())
        .set("size_bytes", v.size)
        .set("chunks", v.chunks.clone())
        .set("key", key)
        .set_some("key_disk", v.key_disk.clone())
        .set_some("kdf", hint.and_then(|h| h.name()))
        .set_some("kdf_rounds", hint.and_then(|h| h.rounds).map(u64::from))
        .build()
}

/// A node load names by DUID (OpenBSD: 739d98841b65e745.a, a softraid
/// volume's partition) as the device that has that DUID now (/dev/sd3a),
/// from hw.disknames.  None for any other node, or a DUID no disk has.
pub fn duid_path(sys: &Sys, node: &str) -> Option<String> {
    let (duid, letter) = duid_node(node)?;
    let names = disk_names(Family::OpenBsd, &sys.run("sysctl", &["-n", "hw.disknames"])?);
    names.into_iter().find(|(_, d)| d.as_deref() == Some(duid)).map(|(n, _)| format!("/dev/{n}{letter}"))
}

/// close's unmount of $m where the tree is on a softraid volume: umount,
/// and if that fails while fstat lists no file open on $m (a reference the
/// kernel holds, not a process's), umount -f, saying so; else stop.
pub const UMOUNT_BUSY: &str = "umount \"$m\" || { ! fstat -f \"$m\" | sed 1d | grep -q . && echo \"bko-holt: $m is busy with no file open on it: umount -f\" >&2 && umount -f \"$m\"; } || exit";

/// A node named by DUID (739d98841b65e745.a): its DUID and letter.  load
/// names a softraid volume's partitions so, and nothing else.
fn duid_node(node: &str) -> Option<(&str, &str)> {
    let (duid, letter) = node.split_once('.')?;
    (duid.len() == 16 && duid.bytes().all(|c| c.is_ascii_hexdigit()) && letter.len() == 1 && letter.bytes().all(|c| c.is_ascii_lowercase())).then_some((duid, letter))
}

/// Whether a node load made is a softraid volume's partition, which close
/// leaves attached (status's close row doesn't count it as open).
pub fn is_volume_node(node: &str) -> bool {
    duid_node(node).is_some()
}

// ---- load -------------------------------------------------------------

/// A softraid volume of a layout.
struct Vol {
    name: String,
    rec: Value,
    sr: Value,
    duid: Option<String>,
    /// Its disklabel's partitions: (name, letter).
    parts: Vec<(String, String)>,
}

impl Vol {
    fn str(&self, k: &str) -> String {
        self.sr.get(k).and_then(Value::as_str).unwrap_or("").to_string()
    }

    fn chunks(&self) -> Vec<String> {
        self.sr.get("chunks").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect()
    }
}

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

fn volumes(layout: &Layout) -> Vec<Vol> {
    layout
        .arr(&["storage", "softraid_volumes"])
        .iter()
        .filter_map(|v| {
            let parts = v.get("label_partitions").and_then(Value::as_array).into_iter().flatten().filter_map(|p| Some((s(p, "name")?, s(p, "letter")?))).collect();
            Some(Vol { name: s(v, "name")?, rec: v.clone(), sr: v.get("softraid").cloned().unwrap_or(Value::Null), duid: s(v, "duid"), parts })
        })
        .collect()
}

/// What's in the volumes' disklabel partitions, for format.
pub(crate) fn leaves(layout: &Layout) -> Vec<(String, Value)> {
    let mut out = Vec::new();
    for v in layout.arr(&["storage", "softraid_volumes"]) {
        for p in v.get("label_partitions").and_then(Value::as_array).into_iter().flatten() {
            if let (Some(n), Some(c)) = (s(p, "name"), p.get("content").filter(|c| !c.is_null())) {
                out.push((n, c.clone()));
            }
        }
    }
    out
}

/// A shell prefix that sets $v to the softraid volume whose chunk is
/// `chunk` (sd2a), from `bioctl softraid0`'s Device column, or stops.
fn find_volume(chunk: &str) -> String {
    format!(
        "v=$(bioctl softraid0 2>/dev/null | awk '{{ for (i = 1; i <= NF; i++) if ($i ~ /^sd[0-9]+$/) d = $i }} index($0, \"<{chunk}>\") {{ print d; exit }}'); [ -n \"$v\" ] || {{ echo 'no softraid volume on {chunk}' >&2; exit 1; }}; "
    )
}

/// The passphrase file bioctl -p reads: a copy of --key-file's, owned by
/// root and mode 600, as bioctl wants it.
fn pass_file(name: &str) -> String {
    format!("{RUN_DIR}/keys/{name}.pass")
}

/// The shell command that copies --key-file's FILE to `to` for bioctl -p:
/// made new (so root's, load's user), mode 600 exactly (bioctl refuses any
/// other mode, 400 too), and ending in a newline.  bioctl reads the
/// passphrase as the file's first line and refuses one with no newline
/// ("invalid passphrase length", OpenBSD 8.0), where cryptsetup and geli
/// take a file without one: so one is added when the file has none, and the
/// passphrase is the same either way.
fn pass_copy(from: &str, to: &str) -> String {
    let (f, t) = (crate::load::shell_quote(from), crate::load::shell_quote(to));
    format!("rm -f {t} && umask 077 && {{ cat {f} && if [ -n \"$(tail -c 1 {f})\" ]; then echo; fi; }} > {t} && chmod 600 {t}")
}

/// Whether a volume's own MBR is the one `fdisk -iy` makes: a single
/// OpenBSD partition from sector 64 to the end.
fn default_mbr(rec: &Value) -> bool {
    let sector = rec.get("logical_sector_size").and_then(Value::as_u64).unwrap_or(512).max(512);
    let total = rec.get("size_bytes").and_then(Value::as_u64).unwrap_or(0) / sector;
    let parts = rec.get("partitions").and_then(Value::as_array).cloned().unwrap_or_default();
    let n = |p: &Value, k: &str| p.get(k).and_then(Value::as_u64);
    parts.len() == 1 && s(&parts[0], "type").as_deref() == Some("0xa6") && n(&parts[0], "start_lba") == Some(64) && n(&parts[0], "sectors") == total.checked_sub(64)
}

impl Builder<'_> {
    /// Each softraid CRYPTO volume, once its chunk's disk is partitioned:
    /// bioctl makes it on the chunk, then its own MBR and the source's
    /// disklabel go on it, its DUID with it; what's in it is named by that
    /// DUID from here on.  Its closer detaches it.
    pub(crate) fn softraid_assemble(&mut self) -> Vec<Cmd> {
        let mut out = Vec::new();
        for v in volumes(self.layout) {
            let level = v.str("level");
            let chunks = v.chunks();
            let nodes: Vec<Option<String>> = chunks.iter().map(|c| self.names.dev(c)).collect();
            if chunks.is_empty() || nodes.iter().any(Option::is_none) {
                let missing: Vec<&str> = chunks.iter().zip(&nodes).filter(|(_, n)| n.is_none()).map(|(c, _)| c.as_str()).collect();
                let on = if chunks.is_empty() { "chunks the dump doesn't name".to_string() } else { missing.join(", ") };
                self.problems.push(format!("{}: the softraid {level} volume sits on {on}, which nothing in this layout makes (an unmapped disk?)", v.name));
                continue;
            }
            if self.fam != Some(Family::OpenBsd) {
                self.problems.push(format!("{}: softraid is OpenBSD's; bko-holt load makes it only on OpenBSD", v.name));
                continue;
            }
            let Some(duid) = v.duid.clone() else {
                self.problems.push(format!("{}: the softraid volume's disklabel has no DUID, which load names its partitions by", v.name));
                continue;
            };
            // What's in it is planned either way: a refusal is the volume's
            // own problem.
            for (n, letter) in &v.parts {
                self.names.made.insert(n.clone(), format!("{duid}.{letter}"));
            }
            let on = chunks.join(", ");
            if level != "CRYPTO" {
                self.problems.push(format!("{}: a softraid {level} volume (on {on}); bko-holt load makes softraid CRYPTO volumes only", v.name));
                continue;
            }
            if v.str("key") == "keydisk" {
                let kd = s(&v.sr, "key_disk").map(|k| format!(" ({k})")).unwrap_or_default();
                self.problems.push(format!("{}: a softraid CRYPTO volume a key disk{kd} unlocks; bko-holt load makes passphrase ones only", v.name));
                continue;
            }
            if chunks.len() != 1 {
                self.problems.push(format!("{}: a softraid CRYPTO volume on {} chunks ({on}); CRYPTO has one", v.name, chunks.len()));
                continue;
            }
            let Some(label) = s(&v.rec, "disklabel") else {
                self.problems.push(format!("{}: the dump has no disklabel for the softraid volume", v.name));
                continue;
            };
            let table = v.rec.at(&["partition_table", "type"]).and_then(Value::as_str).map(str::to_string);
            if table.as_deref().is_some_and(|t| t != "dos") {
                self.problems.push(format!("{}: the softraid volume holds a {} table; load makes the MBR fdisk -i makes", v.name, table.unwrap_or_default()));
                continue;
            }
            let node = nodes[0].clone().unwrap_or_default();
            let at = node.trim_start_matches("/dev/").to_string();
            let shown = format!("<volume of {at}>");
            self.notes.push(format!("softraid CRYPTO volume {} (on {}): --key-file takes {}, {duid}; it is {shown} until bioctl makes it", v.name, chunks[0], v.name));
            let given = self.opts.key_file(&v.name).or_else(|| self.opts.key_file(&duid)).map(str::to_string);
            let mut a: Vec<String> = ["bioctl", "-C", "force", "-c", "C", "-l", &node].iter().map(|x| x.to_string()).collect();
            let rounds = v.sr.get("kdf_rounds").and_then(Value::as_u64);
            let kdf = match (v.str("kdf").as_str(), rounds) {
                ("bcrypt_pbkdf", Some(r)) => {
                    a.extend(["-r".into(), r.to_string()]);
                    format!("bcrypt_pbkdf, {r} rounds as the source's")
                }
                ("pbkdf2", _) => {
                    self.notes.push(format!("{}: the source's softraid KDF is PBKDF2; bioctl makes bcrypt_pbkdf, with its own rounds", v.name));
                    "bcrypt_pbkdf, bioctl's rounds".to_string()
                }
                _ => "bcrypt_pbkdf, bioctl's rounds (the source's weren't read)".to_string(),
            };
            let why = match &given {
                Some(f) => {
                    let p = pass_file(&v.name);
                    out.push(Cmd::new(&["mkdir", "-p", "-m", "700", &format!("{RUN_DIR}/keys")]));
                    out.push(Cmd::new(&["sh", "-c", &pass_copy(f, &p)]).why(format!("bioctl -p takes a passphrase file owned by root, mode 600, its first line ending in a newline: {f}'s, copied, the newline added if it has none")));
                    a.extend(["-p".into(), p.clone()]);
                    self.closers.push(Cmd::new(&["rm", "-f", &p]));
                    format!("{}: softraid CRYPTO on {node} ({} on the source), {kdf}; the passphrase in {f}", v.name, chunks[0])
                }
                None => format!("{}: softraid CRYPTO on {node} ({} on the source), {kdf}; bioctl asks for the new passphrase, twice", v.name, chunks[0]),
            };
            a.push("softraid0".into());
            out.push(Cmd { argv: a, why: Some(why), ..Cmd::default() });
            if given.is_some() {
                out.push(Cmd::new(&["rm", "-f", &pass_file(&v.name)]).why(format!("{}'s passphrase is in the volume's metadata now", v.name)));
            }
            let find = find_volume(&at);
            // Not detached at close: softraid's bioctl -d (deleteraid)
            // rewrites the volume's flags to NOAUTOASSEMBLE alone, clearing
            // BIOC_SCBOOTABLE that installboot set (OpenBSD 8.0: 0x0c ->
            // 0x04), and /boot then won't boot it (sr0 isn't the boot
            // volume: it tries hd0a:/bsd).  The volume stays attached, as
            // install.sub leaves it, and the rescue system's shutdown saves
            // its metadata as it is.
            self.closers.push(Cmd::note(format!("{shown}, the softraid volume, stays attached: bioctl -d would clear the bootable flag installboot set; shut the rescue system down (or reboot) to detach it")));
            if table.is_some() {
                if !default_mbr(&v.rec) {
                    self.notes.push(format!("{}: the softraid volume's own MBR isn't the one fdisk -i makes; it's made as fdisk -i makes it", v.name));
                }
                out.push(Cmd::new(&["sh", "-c", &format!("{find}fdisk -iy \"$v\"")]).why(format!("{shown}: the new volume, bioctl softraid0's Device for <{at}>; its own MBR, as the source's")));
            }
            let proto = format!("{RUN_DIR}/{}.disklabel", v.name);
            out.push(Cmd::write_file(&proto, label));
            out.push(Cmd::new(&["sh", "-c", &format!("{find}disklabel -R \"$v\" {proto}")]).why(format!("{}'s disklabel, on {shown}", v.name)));
            out.push(Cmd::new(&["sh", "-c", &format!("{find}printf 'i\\n%s\\nw\\nq\\n' {duid} | disklabel -E \"$v\" >/dev/null")]).why(format!("{}'s disklabel UID, {duid}: its partitions are {duid}.a and so on from here", v.name)));
        }
        out
    }

    /// For a root in a softraid volume load makes: the shell prefix that
    /// finds the volume (installboot takes the volume, and writes the boot
    /// blocks to its chunk's disk and boot area).
    pub(crate) fn softraid_of(&self, src: &str) -> Option<String> {
        let v = volumes(self.layout).into_iter().find(|v| v.parts.iter().any(|(n, _)| n == src))?;
        if !self.names.made.contains_key(src) {
            return None;
        }
        let chunk = v.chunks().into_iter().next()?;
        let node = self.names.dev(&chunk)?;
        Some(find_volume(node.trim_start_matches("/dev/")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load::Stage;
    use crate::sys::FakeRunner;
    use crate::testutil::Tree;

    // What the openbsd8-crypt guest printed (docs/design/openbsd-crypto/,
    // O1's captures).
    const BIOCTL: &str = "Volume      Status               Size Device  \nsoftraid0 0 Online        17179566080 sd1     CRYPTO\n          0 Online        17179566080 0:0.0   noencl <sd0a>\n";
    const BIOCTL_V: &str = "Volume      Status               Size Device  \nsoftraid0 0 Online        17179566080 sd1     CRYPTO\n          0 Online        17179566080 0:0.0   noencl <sd0a>\n                                                     'unknown serial'\n";
    const DISKNAMES: &str = "cd0:,sd0:f036e7e3534d6f8b,sd1:739d98841b65e745,fd0:\n";
    const LABEL_SD0: &str = "# /dev/rsd0c:\ntype: SCSI\ndisk: SCSI disk\nlabel: Block Device    \nduid: f036e7e3534d6f8b\nflags:\nbytes/sector: 512\nsectors/track: 63\ntracks/cylinder: 255\nsectors/cylinder: 16065\ncylinders: 2088\ntotal sectors: 33554432\nboundstart: 64\nboundend: 33554432\n\n16 partitions:\n#                size           offset  fstype [fsize bsize   cpg]\n  a:         33554368               64    RAID                    \n  c:         33554432                0  unused                    \n";
    const LABEL_SD1: &str = "# /dev/rsd1c:\ntype: SCSI\ndisk: SCSI disk\nlabel: SR CRYPTO\nduid: 739d98841b65e745\nflags:\nbytes/sector: 512\nsectors/track: 63\ntracks/cylinder: 255\nsectors/cylinder: 16065\ncylinders: 2088\ntotal sectors: 33553840\nboundstart: 64\nboundend: 33553840\n\n16 partitions:\n#                size           offset  fstype [fsize bsize   cpg]\n  a:         29359456               64  4.2BSD   2048 16384 12960 # /\n  b:          4194304         29359520    swap                    # none\n  c:         33553840                0  unused                    \n";
    const FSTAB: &str = "739d98841b65e745.b none swap sw\n739d98841b65e745.a / ffs rw,wxallowed 1 1\n";
    const MOUNT: &str = "/dev/sd1a on / type ffs (local, wxallowed)\n";
    const DUID: &str = "739d98841b65e745";

    /// A chunk's softraid metadata as kdf-rounds.txt describes the guest's:
    /// one chunk, the CRYPTO optional metadata, its bcrypt_pbkdf hint (len
    /// 140, type 3, `rounds`) at byte 2412, then a salt.
    fn metadata(kind: u32, rounds: u32) -> Vec<u8> {
        let mut m = vec![0u8; META_LEN];
        m[0..8].copy_from_slice(b"marcCRAM");
        m[8..12].copy_from_slice(&6u32.to_le_bytes());
        m[32..36].copy_from_slice(&1u32.to_le_bytes());
        m[40..44].copy_from_slice(&1u32.to_le_bytes());
        m[168 + 8..168 + 12].copy_from_slice(b"sd0a");
        m[260..264].copy_from_slice(&1u32.to_le_bytes());
        m[264..268].copy_from_slice(&2480u32.to_le_bytes());
        m[284..288].copy_from_slice(&2u32.to_le_bytes());
        m[288..292].copy_from_slice(&3u32.to_le_bytes());
        for (i, b) in m[364..2412].iter_mut().enumerate() {
            *b = b"MASKEDKEY"[i % 9];
        }
        m[2412..2416].copy_from_slice(&140u32.to_le_bytes());
        m[2416..2420].copy_from_slice(&kind.to_le_bytes());
        m[2420..2424].copy_from_slice(&rounds.to_le_bytes());
        for (i, b) in m[2424..2552].iter_mut().enumerate() {
            *b = b"SALTSALTS"[i % 9];
        }
        m
    }

    #[test]
    fn bioctl_volumes() {
        let want = vec![Volume {
            controller: Some("softraid0".into()),
            id: Some(0),
            status: "Online".into(),
            size: Some(17179566080),
            device: "sd1".into(),
            level: "CRYPTO".into(),
            chunks: vec!["sd0a".into()],
            key_disk: None,
        }];
        assert_eq!(parse_bioctl(BIOCTL), want);
        assert_eq!(parse_bioctl(BIOCTL_V), want);
        assert_eq!(parse_bioctl(""), vec![]);
        // A key disk (its size "key disk"), and a second volume: RAID1.
        let t = "Volume      Status               Size Device  \nsoftraid0 0 Online        17179566080 sd2     CRYPTO\n          0 Online        17179566080 0:0.0   noencl <sd0a>\n          1 Online           key disk 0:1.0   noencl <sd1d>\nsoftraid0 1 Online        8589934592 sd3     RAID1\n          0 Online        8589934592 1:0.0   noencl <sd4a>\n          1 Online        8589934592 1:1.0   noencl <sd5a>\n";
        let v = parse_bioctl(t);
        assert_eq!(v.len(), 2);
        assert_eq!((v[0].device.as_str(), v[0].chunks.clone(), v[0].key_disk.as_deref()), ("sd2", vec!["sd0a".to_string()], Some("sd1d")));
        assert_eq!((v[1].device.as_str(), v[1].level.as_str(), v[1].chunks.clone(), v[1].id), ("sd3", "RAID1", vec!["sd4a".to_string(), "sd5a".to_string()], Some(1)));
    }

    #[test]
    fn kdf_rounds_from_the_chunk() {
        assert_eq!(kdf_hint(&metadata(3, 242)), Some(KdfHint { kind: KDF_BCRYPT_PBKDF, rounds: Some(242) }));
        assert_eq!(kdf_hint(&metadata(3, 242)).unwrap().name(), Some("bcrypt_pbkdf"));
        assert_eq!(kdf_hint(&metadata(2, 0)), Some(KdfHint { kind: KDF_KEYDISK, rounds: None }));
        let mut m = metadata(3, 242);
        m[8] = 5;
        assert_eq!(kdf_hint(&m), None, "another metadata version");
        assert_eq!(kdf_hint(&m[..100]), None);
        assert_eq!(kdf_hint(&vec![0u8; META_LEN]), None);
    }

    #[test]
    fn duid_nodes() {
        let t = Tree::new("softraid-duid");
        let s = Sys::new(t.root(), Box::new(FakeRunner::default().with("sysctl -n hw.disknames", "cd0:,sd0:f036e7e3534d6f8b,sd3:739d98841b65e745,sd2:")), true);
        assert_eq!(duid_path(&s, "739d98841b65e745.a").as_deref(), Some("/dev/sd3a"));
        assert_eq!(duid_path(&s, "0123456789abcdef.a"), None);
        assert_eq!(duid_path(&s, "/dev/sd0a"), None);
        assert_eq!(super::super::node_path(&s, "739d98841b65e745.b"), "/dev/sd3b");
        assert_eq!(super::super::node_path(&s, "/dev/mapper/root"), "/dev/mapper/root");
    }

    /// The prefix that finds the volume, run with the guest's bioctl.
    #[test]
    fn find_volume_by_its_chunk() {
        let t = Tree::new("softraid-find");
        t.file("/bin/bioctl", format!("#!/bin/sh\n[ \"$1\" = softraid0 ] || exit 1\ncat <<'X'\n{BIOCTL_V}X\n"));
        let bin = t.p("/bin");
        std::process::Command::new("chmod").arg("755").arg(bin.join("bioctl")).status().unwrap();
        let path = format!("{}:{}", bin.display(), std::env::var("PATH").unwrap_or_default());
        let run = |chunk: &str| std::process::Command::new("sh").arg("-c").arg(format!("{}echo \"$v\"", find_volume(chunk))).env("PATH", &path).output().unwrap();
        let o = run("sd0a");
        assert!(o.status.success());
        assert_eq!(String::from_utf8_lossy(&o.stdout), "sd1\n");
        let o = run("sd2a");
        assert!(!o.status.success());
        assert_eq!(String::from_utf8_lossy(&o.stderr), "no softraid volume on sd2a\n");
    }

    /// The openbsd8-crypt guest, shrunk: sd0's MBR (slot 3, OpenBSD) and the
    /// softraid metadata 16 sectors into its "a" (fstype RAID); sd1, the
    /// volume, its own MBR and UFS2 at its "a".
    fn guest() -> (Tree, FakeRunner) {
        use crate::parttable::build::mbr_entry;
        let mut sd0 = vec![0u8; 80 * 512 + META_LEN];
        mbr_entry(&mut sd0[..512], 3, 0x80, 0xa6, 64, 33554368);
        sd0[80 * 512..].copy_from_slice(&metadata(3, 242));
        let mut sd1 = vec![0u8; 64 * 512 + 131072];
        mbr_entry(&mut sd1[..512], 3, 0x80, 0xa6, 64, 33553776);
        sd1[64 * 512..].copy_from_slice(&crate::probe::ufs::sample::image(""));
        let t = Tree::new("softraid-guest");
        t.file("/dev/rsd0c", &sd0).file("/dev/rsd1c", &sd1).file("/etc/fstab", FSTAB);
        let ob = "magic\t19540119 (FFS2)\ttime\tSat Nov 14 01:06:00 2026\nncg\t71\tsize\t7339864\tblocks\t7108647\nbsize\t16384\tshift\t14\tmask\t0xffffc000\nfsize\t2048\tshift\t11\tmask\t0xfffff800\nminfree\t5%\toptim\ttime\tsymlinklen 120\nbpg\t12960\tfpg\t103680\tipg\t25920\n";
        let r = FakeRunner::default()
            .with("hostname", "obsd8-crypt\n")
            .with("sysctl -n hw.disknames", DISKNAMES)
            .with("disklabel sd0", LABEL_SD0)
            .with("disklabel sd1", LABEL_SD1)
            .with("bioctl softraid0", BIOCTL)
            .with("mount", MOUNT)
            .with("dumpfs /dev/rsd1a", ob)
            .with("swapctl -l", "Device      512-blocks     Used    Avail Capacity  Priority\n/dev/sd1b     4194304        0  4194304     0%    0\n");
        (t, r)
    }

    fn dump() -> Value {
        let (t, r) = guest();
        let sys = Sys::new(t.root(), Box::new(r), true);
        let os = crate::osrel::parse("ID=openbsd\nVERSION_ID=\"8.0\"\nPRETTY_NAME=\"OpenBSD 8.0\"\n");
        super::super::labelled::report(&sys, Family::OpenBsd, Some(&os), "2026-11-14T00:00:00Z")
    }

    fn str_at<'a>(v: &'a Value, path: &[&str]) -> Option<&'a str> {
        v.at(path).and_then(Value::as_str)
    }

    #[test]
    fn dump_reports_the_volume_as_a_virtual_disk() {
        let v = dump();
        // sd0 is the only disk; its "a" is the volume's chunk.
        let disks = v.at(&["storage", "disks"]).and_then(Value::as_array).unwrap();
        assert_eq!(disks.len(), 1);
        assert_eq!(str_at(&disks[0], &["name"]), Some("sd0"));
        assert_eq!(str_at(&disks[0], &["kind"]), Some("disk"));
        assert_eq!(disks[0].get("softraid"), None);
        let a = disks[0].at(&["label_partitions", "0"]).unwrap();
        assert_eq!((str_at(a, &["name"]), str_at(a, &["fstype"]), str_at(a, &["softraid_volume"])), (Some("sd0a"), Some("RAID"), Some("sd1")));
        assert_eq!(a.get("content"), Some(&Value::Null));
        // sd1, the volume: a disk's shape, and what bioctl and the chunk's
        // metadata say of it.
        let vol = v.at(&["storage", "softraid_volumes", "0"]).unwrap();
        assert_eq!((str_at(vol, &["name"]), str_at(vol, &["kind"]), str_at(vol, &["duid"])), (Some("sd1"), Some("softraid"), Some(DUID)));
        assert_eq!(vol.get("size_bytes").and_then(Value::as_u64), Some(33553840 * 512));
        assert_eq!(
            vol.get("softraid").unwrap().compact(),
            r#"{"controller":"softraid0","volume_id":0,"level":"CRYPTO","status":"Online","size_bytes":17179566080,"chunks":["sd0a"],"key":"passphrase","kdf":"bcrypt_pbkdf","kdf_rounds":242}"#
        );
        assert_eq!(str_at(vol, &["partition_table", "type"]), Some("dos"));
        assert_eq!(vol.at(&["partitions", "0", "start_lba"]).and_then(Value::as_u64), Some(64));
        assert_eq!(vol.at(&["partitions", "0", "sectors"]).and_then(Value::as_u64), Some(33553776));
        assert_eq!(str_at(vol, &["disklabel"]), Some(LABEL_SD1));
        let root = vol.at(&["label_partitions", "0"]).unwrap();
        assert_eq!((str_at(root, &["name"]), str_at(root, &["content", "type"])), (Some("sd1a"), Some("ufs")));
        assert_eq!(root.get("mounted_at"), Some(&Value::from(vec!["/"])));
        assert_eq!(str_at(root, &["newfs"]), Some("newfs -O 2 -b 16384 -f 2048 -i 8192 -m 5 -o time -s 29359456 DEVICE"));
        // fstab's DUIDs, mount's and swapctl's devices are the volume's.
        assert_eq!(str_at(&v, &["storage", "fstab", "0", "resolves_to"]), Some("sd1b"));
        assert_eq!(str_at(&v, &["storage", "fstab", "1", "resolves_to"]), Some("sd1a"));
        assert_eq!(str_at(&v, &["storage", "mounts", "0", "device"]), Some("sd1a"));
        assert_eq!(str_at(&v, &["storage", "swaps", "0", "device"]), Some("sd1b"));
        // No key material, and it plans the same redacted.
        let text = v.pretty();
        assert!(!text.contains("SALTS") && !text.contains("MASKED"), "{text}");
        if let Err(e) = crate::bugreport::check_plan(&text) {
            panic!("redaction: {e}");
        }
        // Without its metadata (no chunk to read): no KDF, bioctl's default.
        let (t, r) = guest();
        t.file("/dev/rsd0c", vec![0u8; 512]);
        let os = crate::osrel::parse("ID=openbsd\nVERSION_ID=\"8.0\"\n");
        let v = super::super::labelled::report(&Sys::new(t.root(), Box::new(r), true), Family::OpenBsd, Some(&os), "x");
        assert_eq!(v.at(&["storage", "softraid_volumes", "0", "softraid", "kdf_rounds"]), None);
        assert_eq!(str_at(&v, &["storage", "softraid_volumes", "0", "softraid", "key"]), Some("passphrase"));
        // A system with no softraid: no section.
        let (t, r) = guest();
        let r = r.with("bioctl softraid0", "").with("sysctl -n hw.disknames", "sd0:f036e7e3534d6f8b");
        let v = super::super::labelled::report(&Sys::new(t.root(), Box::new(r), true), Family::OpenBsd, Some(&os), "x");
        assert_eq!(v.at(&["storage", "softraid_volumes"]), None);
    }

    // ---- load ---------------------------------------------------------

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

    fn plan(v: Value, args: &[&str]) -> crate::load::commands::Built {
        use crate::load::{commands, target::Mapping, Parsed};
        let l = Layout::from_json(v).unwrap();
        let mut a = vec!["l.json".to_string()];
        a.extend(args.iter().map(|s| s.to_string()));
        let Ok(Parsed::Run(o)) = crate::load::parse_args(&a) else { unreachable!() };
        commands::build(&l, &o, &Mapping { pairs: vec![("sd0".into(), "sd2".into())], ..Mapping::default() })
    }

    /// A stage's commands as a shell would take them (files to write as
    /// `cat > PATH`), notes left out.
    fn stage(b: &crate::load::commands::Built, s: Stage) -> Vec<String> {
        b.stage(s)
            .iter()
            .filter(|c| !c.is_note())
            .map(|c| match &c.write {
                Some(p) => format!("cat > {p}"),
                None => c.argv.iter().map(|a| crate::load::shell_quote(a)).collect::<Vec<_>>().join(" "),
            })
            .collect()
    }

    fn sh(script: &str) -> String {
        format!("sh -c {}", crate::load::shell_quote(&format!("{}{script}", find_volume("sd2a"))))
    }

    #[test]
    fn crypto_root_is_made_on_its_chunk() {
        let b = plan(dump(), &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        // partition: sd0 as a plain disk, its "a" fstype RAID.
        let p = stage(&b, Stage::Partition);
        assert!(p.iter().any(|c| c == &format!("disklabel -R sd2 {RUN_DIR}/sd0.disklabel")), "{p:?}");
        assert!(p.iter().any(|c| c.contains("f036e7e3534d6f8b | disklabel -E sd2")), "{p:?}");
        // assemble: the volume on /dev/sd2a, its MBR and disklabel, its DUID.
        let a = stage(&b, Stage::Assemble);
        let proto = format!("{RUN_DIR}/sd1.disklabel");
        assert_eq!(
            a,
            vec![
                "bioctl -C force -c C -l /dev/sd2a -r 242 softraid0".to_string(),
                sh("fdisk -iy \"$v\""),
                format!("cat > {proto}"),
                sh(&format!("disklabel -R \"$v\" {proto}")),
                sh(&format!("printf 'i\\n%s\\nw\\nq\\n' {DUID} | disklabel -E \"$v\" >/dev/null")),
            ]
        );
        assert_eq!(b.stage(Stage::Assemble)[2].stdin.as_deref(), Some(LABEL_SD1));
        assert!(b.stage(Stage::Assemble)[0].why.as_deref().unwrap().ends_with("bcrypt_pbkdf, 242 rounds as the source's; bioctl asks for the new passphrase, twice"));
        assert!(b.notes.contains(&format!("softraid CRYPTO volume sd1 (on sd0a): --key-file takes sd1, {DUID}; it is <volume of sd2a> until bioctl makes it")), "{:?}", b.notes);
        assert!(b.made.contains(&("sd1a".to_string(), format!("{DUID}.a"))));
        // format and mount: by the DUID.
        assert_eq!(stage(&b, Stage::Format), vec![format!("newfs -O 2 -b 16384 -f 2048 -i 8192 -m 5 -o time -s 29359456 {DUID}.a")]);
        assert_eq!(stage(&b, Stage::Mount), vec!["mkdir -p /mnt/bko-target".to_string(), format!("mount -t ffs -o rw,wxallowed {DUID}.a /mnt/bko-target")]);
        assert!(b.filesystems.contains(&(format!("{DUID}.a"), "ufs".to_string(), None)));
        // boot: installboot on the volume.
        let bt = stage(&b, Stage::Boot);
        assert!(bt.contains(&sh("installboot -v -r /mnt/bko-target \"$v\" /usr/mdec/biosboot /usr/mdec/boot")), "{bt:?}");
        assert!(!bt.iter().any(|c| c.starts_with("installboot")), "{bt:?}");
        // close: unmounted, and the volume left attached (bioctl -d would
        // clear the bootable flag installboot set), with a note saying so.
        let c = stage(&b, Stage::Close);
        assert!(c[0].contains("umount"), "{c:?}");
        assert!(!c.iter().any(|x| x.contains("bioctl")), "{c:?}");
        assert!(b.stage(Stage::Close).iter().any(|x| x.is_note() && x.why.as_deref().is_some_and(|w| w.contains("<volume of sd2a>, the softraid volume, stays attached"))), "{:?}", b.stage(Stage::Close));
    }

    #[test]
    fn key_file_names_the_volume() {
        for name in ["sd1", DUID] {
            let b = plan(dump(), &["--key-file", &format!("{name}=/media/k/pass")]);
            assert!(b.problems.is_empty(), "{:?}", b.problems);
            let a = stage(&b, Stage::Assemble);
            let p = format!("{RUN_DIR}/keys/sd1.pass");
            assert_eq!(a[0], format!("mkdir -p -m 700 {RUN_DIR}/keys"));
            assert_eq!(a[1], format!("sh -c {}", crate::load::shell_quote(&pass_copy("/media/k/pass", &p))));
            assert_eq!(a[2], format!("bioctl -C force -c C -l /dev/sd2a -r 242 -p {p} softraid0"));
            assert_eq!(a[3], format!("rm -f {p}"));
            assert!(stage(&b, Stage::Close).contains(&format!("rm -f {p}")));
        }
    }

    /// close's unmount on a softraid layout: -f only when umount fails and
    /// fstat lists nothing open; a tree with a file open stops close.  The
    /// plain loop elsewhere (openbsd8's corpus plan) is unchanged.
    #[test]
    fn close_forces_only_a_busy_tree_with_nothing_open() {
        let has = |b: &crate::load::commands::Built| b.stage(Stage::Close).iter().any(|c| c.argv.get(2).is_some_and(|a| a.contains(UMOUNT_BUSY)));
        assert!(has(&plan(dump(), &[])));
        let mut v = dump();
        let Value::Obj(o) = at_mut(&mut v, &["storage"]) else { unreachable!() };
        o.retain(|(k, _)| k != "softraid_volumes");
        let plain = plan(v, &[]);
        assert!(!has(&plain) && stage(&plain, Stage::Close)[0].ends_with("do umount \"$m\" || exit; done'"), "{:?}", stage(&plain, Stage::Close));
        // The loop body against a fake umount (busy, or not, but for -f)
        // and fstat (a file open on the tree, or none).
        let t = Tree::new("softraid-umount");
        let bin = t.p("/bin");
        let log = t.p("/log");
        let fake = |name: &str, body: &str| {
            t.file(&format!("/bin/{name}"), format!("#!/bin/sh\n{body}\n"));
            std::process::Command::new("chmod").arg("755").arg(bin.join(name)).status().unwrap();
        };
        let run = |busy: bool, open: bool| {
            let _ = std::fs::remove_file(&log);
            fake("umount", &format!("echo \"umount $*\" >> {l}; [ \"$1\" = -f ] && exit 0; exit {}", if busy { 1 } else { 0 }, l = log.display()));
            fake("fstat", if open { "echo 'USER CMD PID FD MOUNT'; echo 'root ksh 1 wd /mnt/t'" } else { "echo 'USER CMD PID FD MOUNT'" });
            let path = format!("{}:{}", bin.display(), std::env::var("PATH").unwrap_or_default());
            let st = std::process::Command::new("sh").arg("-c").arg(format!("echo /mnt/t | while read -r m; do {UMOUNT_BUSY}; done")).env("PATH", &path).output().unwrap();
            (st.status.success(), std::fs::read_to_string(&log).unwrap_or_default(), String::from_utf8_lossy(&st.stderr).to_string())
        };
        let (ok, calls, err) = run(true, false);
        assert!(ok, "{calls} {err}");
        assert_eq!(calls, "umount /mnt/t\numount -f /mnt/t\n");
        assert_eq!(err, "bko-holt: /mnt/t is busy with no file open on it: umount -f\n");
        let (ok, calls, _) = run(true, true);
        assert!(!ok);
        assert_eq!(calls, "umount /mnt/t\n");
        let (ok, calls, err) = run(false, false);
        assert!(ok && err.is_empty());
        assert_eq!(calls, "umount /mnt/t\n");
    }

    /// The copy bioctl -p reads ends in exactly one newline, whether the
    /// key file had one or not, and is mode 600 (bioctl refuses 400 too),
    /// replacing what was there.
    #[test]
    fn the_passphrase_copy_ends_in_a_newline() {
        use std::os::unix::fs::PermissionsExt;
        let t = Tree::new("softraid-pass");
        let to = t.p("/keys/sd1.pass");
        let to = to.to_str().unwrap();
        for (given, want) in [(&b"s3cret pass"[..], &b"s3cret pass\n"[..]), (b"s3cret pass\n", b"s3cret pass\n"), (b"two\nlines", b"two\nlines\n")] {
            t.file("/k/pass file", given).file("/keys/sd1.pass", "stale");
            std::fs::set_permissions(to, std::fs::Permissions::from_mode(0o644)).unwrap();
            let from = t.p("/k/pass file");
            let st = std::process::Command::new("sh").arg("-c").arg(pass_copy(from.to_str().unwrap(), to)).status().unwrap();
            assert!(st.success());
            assert_eq!(std::fs::read(to).unwrap(), want);
            assert_eq!(std::fs::metadata(to).unwrap().permissions().mode() & 0o7777, 0o600);
        }
        // No key file: the copy fails, and bioctl isn't reached.
        let st = std::process::Command::new("sh").arg("-c").arg(pass_copy(t.p("/k/none").to_str().unwrap(), to)).status().unwrap();
        assert!(!st.success());
    }

    #[test]
    fn without_the_sources_rounds_bioctls_own() {
        let mut v = dump();
        let sr = at_mut(&mut v, &["storage", "softraid_volumes", "0", "softraid"]);
        let Value::Obj(o) = sr else { unreachable!() };
        o.retain(|(k, _)| k != "kdf" && k != "kdf_rounds");
        let b = plan(v, &[]);
        assert!(b.problems.is_empty(), "{:?}", b.problems);
        assert_eq!(stage(&b, Stage::Assemble)[0], "bioctl -C force -c C -l /dev/sd2a softraid0");
    }

    #[test]
    fn what_load_cannot_make_is_refused() {
        let refused = |change: &dyn Fn(&mut Value), want: &str| {
            let mut v = dump();
            change(at_mut(&mut v, &["storage", "softraid_volumes", "0"]));
            let b = plan(v, &[]);
            assert!(b.problems.iter().any(|p| p == want), "{:?}", b.problems);
            assert!(!stage(&b, Stage::Assemble).iter().any(|c| c.starts_with("bioctl")), "{:?}", stage(&b, Stage::Assemble));
            // What's in it is still planned, by the DUID.
            assert!(stage(&b, Stage::Format).iter().any(|c| c.ends_with(&format!("{DUID}.a"))));
        };
        refused(&|v| put(at_mut(v, &["softraid"]), "level", Value::from("RAID1")), "sd1: a softraid RAID1 volume (on sd0a); bko-holt load makes softraid CRYPTO volumes only");
        refused(&|v| put(at_mut(v, &["softraid"]), "level", Value::from("RAID1C")), "sd1: a softraid RAID1C volume (on sd0a); bko-holt load makes softraid CRYPTO volumes only");
        refused(&|v| put(at_mut(v, &["softraid"]), "level", Value::from("CONCAT")), "sd1: a softraid CONCAT volume (on sd0a); bko-holt load makes softraid CRYPTO volumes only");
        refused(
            &|v| {
                let sr = at_mut(v, &["softraid"]);
                put(sr, "key", Value::from("keydisk"));
                put(sr, "key_disk", Value::from("sd3d"));
            },
            "sd1: a softraid CRYPTO volume a key disk (sd3d) unlocks; bko-holt load makes passphrase ones only",
        );
        // Its chunk's disk not mapped: nothing makes the volume.
        let b = {
            use crate::load::{commands, target::Mapping, Parsed};
            let l = Layout::from_json(dump()).unwrap();
            let Ok(Parsed::Run(o)) = crate::load::parse_args(&["l.json".to_string()]) else { unreachable!() };
            commands::build(&l, &o, &Mapping::default())
        };
        assert!(b.problems.contains(&"sd1: the softraid CRYPTO volume sits on sd0a, which nothing in this layout makes (an unmapped disk?)".to_string()), "{:?}", b.problems);
        // Not OpenBSD's: softraid is refused elsewhere.
        let mut v = dump();
        put(at_mut(&mut v, &["os"]), "family", Value::from("netbsd"));
        put(at_mut(&mut v, &["os"]), "id", Value::from("netbsd"));
        put(at_mut(&mut v, &["os"]), "version_id", Value::from("11.0"));
        assert!(plan(v, &[]).problems.contains(&"sd1: softraid is OpenBSD's; bko-holt load makes it only on OpenBSD".to_string()));
    }

    #[test]
    fn status_finds_the_volume_by_its_duid() {
        // After assemble the volume is sd3 here, its DUID the source's.
        let b = plan(dump(), &[]);
        let t = Tree::new("softraid-status");
        t.file("/dev/sd3a", "").file("/dev/sd3b", "");
        let attached = Sys::new(t.root(), Box::new(FakeRunner::default().with("sysctl -n hw.disknames", "sd0:a1,sd2:f036e7e3534d6f8b,sd3:739d98841b65e745")), true);
        assert!(b.made.iter().all(|(_, n)| attached.exists(&super::super::node_path(&attached, n))), "{:?}", b.made);
        let detached = Sys::new(t.root(), Box::new(FakeRunner::default().with("sysctl -n hw.disknames", "sd0:a1,sd2:f036e7e3534d6f8b")), true);
        assert!(!b.made.iter().any(|(_, n)| detached.exists(&super::super::node_path(&detached, n))), "{:?}", b.made);
        // close leaves the volume attached: status's close row counts none
        // of its partitions as open, and only them.
        assert!(!b.made.is_empty() && b.made.iter().all(|(_, n)| is_volume_node(n)), "{:?}", b.made);
        for n in ["/dev/sd3a", "sd3a", "739d98841b65e745", "739d98841b65e745.ab", "739d98841b65e74.a", "739d98841b65e745.A", "/dev/mapper/root"] {
            assert!(!is_volume_node(n), "{n}");
        }
    }
}
