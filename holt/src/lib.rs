//! bko-holt: records a system's disk layout and boot setup
//! (disks, partition tables, RAID, LVM, LUKS, ZFS, btrfs, filesystems,
//! mounts, firmware, boot loader, kernels) as JSON (`dump`), and rebuilds the
//! disks from that JSON in stages (`load`), for BackOtter to record with each
//! backup and to rebuild the machine from.  Both sides share the on-disk
//! parsers: `load` checks every stage by reading the disks back with them.


pub mod blockdev;
pub mod boot;
pub mod bootcode;
pub mod bytes;
pub mod efi;
pub mod json;
pub mod load;
pub mod mdsb;
pub mod mounts;
pub mod osrel;
pub mod other;
pub mod parttable;
pub mod probe;
pub mod report;
#[cfg(test)]
mod scenarios;
pub mod sys;
#[doc(hidden)]
pub mod testutil;

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
    if let Some(fam) = other::Family::current() {
        return other::dump(sys, fam, &timestamp());
    }
    let os = osrel::read(sys);
    osrel::check_supported(os.as_ref())?;
    Ok(report::build(sys, os.as_ref(), &timestamp()))
}
