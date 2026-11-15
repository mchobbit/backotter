//! bko-holt: records a system's disk layout and boot setup
//! (disks, partition tables, RAID, LVM, LUKS, ZFS, btrfs, filesystems,
//! mounts, firmware, boot loader, kernels) as JSON (`dump`), and rebuilds the
//! disks from that JSON in stages (`load`), for BackOtter to record with each
//! backup and to rebuild the machine from.  Both sides share the on-disk
//! parsers: `load` checks every stage by reading the disks back with them.


pub mod abroot;
pub mod arch;
pub mod blockdev;
pub mod boot;
pub mod bootcode;
pub mod bugreport;
pub mod bytes;
pub mod efi;
pub mod json;
pub mod load;
pub mod luksheader;
pub mod mdsb;
pub mod mounts;
pub mod osrel;
pub mod ostree;
pub mod other;
pub mod parttable;
pub mod probe;
pub mod qubes;
pub mod redact;
pub mod report;
#[cfg(test)]
mod scenarios;
pub mod snapcore;
pub mod store;
pub mod sys;
#[doc(hidden)]
pub mod testutil;
pub mod transactional;
pub mod truenas;
pub mod zfs_be;
pub mod zfsbootmenu;

use std::time::{SystemTime, UNIX_EPOCH};

/// The report's timestamp: now, or SOURCE_DATE_EPOCH when set (for
/// reproducible output).
pub fn timestamp() -> String {
    let secs = std::env::var("SOURCE_DATE_EPOCH")
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok())
        .unwrap_or_else(|| SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0));
    report::rfc3339(secs)
}

/// `bko-holt dump`: the report for `sys`, after checking it's a supported system.
pub fn dump(sys: &sys::Sys) -> Result<json::Value, String> {
    dump_only(sys, &[])
}

/// `bko-holt dump --only-disk`: as `dump`, leaving out every whole disk not
/// in `only` (none left out when it's empty).
pub fn dump_only(sys: &sys::Sys, only: &[String]) -> Result<json::Value, String> {
    dump_with(sys, only, &luksheader::Options::default())
}

/// `bko-holt dump` with --keep-luks-header and --luks-header-dir
/// (crate::luksheader).
pub fn dump_with(sys: &sys::Sys, only: &[String], luks: &luksheader::Options) -> Result<json::Value, String> {
    if let Some(fam) = other::Family::current() {
        if !only.is_empty() {
            return Err("--only-disk works on Linux only".to_string());
        }
        return other::dump(sys, fam, &timestamp());
    }
    let os = osrel::read(sys);
    osrel::check_supported(os.as_ref())?;
    let v = report::build_with(sys, os.as_ref(), &timestamp(), only, luks);
    // What the report found that a backup can't follow (Solus's ESP, not
    // mounted: report::refusal).
    match snapcore::refusal(&v).or_else(|| report::refusal(&v)) {
        Some(e) => Err(e),
        None => Ok(v),
    }
}
