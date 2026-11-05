//! bko-laydump: describes a Debian 13 system's disk layout and boot setup
//! (disks, partition tables, RAID, LVM, LUKS, ZFS, btrfs, filesystems,
//! mounts, firmware, boot loader, kernels) as JSON, for BackOtter to record
//! with each backup and to rebuild the machine from.

#[cfg(not(target_os = "linux"))]
compile_error!("bko-laydump reads Linux's /sys, /proc and /dev: it builds on Linux only");

pub mod blockdev;
pub mod boot;
pub mod bootcode;
pub mod bytes;
pub mod efi;
pub mod json;
pub mod mounts;
pub mod osrel;
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

/// Builds the report for `sys`, after checking it's Debian 13.
pub fn dump(sys: &sys::Sys) -> Result<json::Value, String> {
    let os = osrel::read(sys);
    osrel::check_debian13(os.as_ref())?;
    Ok(report::build(sys, os.as_ref(), &timestamp()))
}
