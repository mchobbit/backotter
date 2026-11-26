//! TrueNAS Community Edition 25.10 (docs/design/truenas.md): how to tell a
//! system is one, and what the dump records of its boot environments and
//! ESPs (boot.truenas).
//!
//! To os-release TrueNAS is Debian 12.  Its own marks are the
//! middleware's config database and TrueNAS's GRUB helper; the product
//! version is /etc/version.  Every install or update is a boot environment
//! `boot-pool/ROOT/VERSION` (crate::zfs_be's TrueNAS rule), a legacy,
//! read-only root with 15 child datasets, the bootfs one booted.  The ESP
//! on each boot-pool disk (partition 2, type EF00) isn't mounted:
//! `truenas_install` mounts it only to run grub-install.  dump reads each
//! one through a private read-only mount, as Proxmox's (crate::boot).

use crate::json::{Obj, Value};
use crate::sys::{RUN_DIR, RoMountWords, Sys, safe_name};
use crate::zfs_be::{BootEnv, Rule};

/// The middleware's config database and TrueNAS's GRUB helper: both there
/// on a TrueNAS system.
pub(crate) const MARKS: [&str; 2] = ["/data/freenas-v1.db", "/usr/local/bin/truenas-grub.py"];
/// The product version (`25.10.8`).
pub(crate) const VERSION: &str = "/etc/version";
pub(crate) const KERNEL_PROP: &str = "truenas:kernel_version";
pub(crate) const KEEP_PROP: &str = "zectl:keep";

/// Whether the running system is TrueNAS.
pub(crate) fn detect(sys: &Sys) -> bool {
    MARKS.iter().all(|f| sys.exists(f))
}

/// os.product: "TrueNAS 25.10.8".
pub(crate) fn product(sys: &Sys) -> Option<String> {
    let v = sys.read_trim(VERSION)?;
    let v = v.lines().next().unwrap_or("").trim().to_string();
    (!v.is_empty()).then(|| format!("TrueNAS {v}"))
}

/// One ESP of a boot-pool disk: its partition, and what a read of it found
/// (Value::Null when it couldn't be read).  boot.truenas's esps list has
/// its disk, volume ID and label.
pub(crate) struct Esp {
    pub device: String,
    pub contents: Value,
}

/// boot.truenas, and its ESPs for boot.esps.
pub(crate) struct TrueNas {
    pub json: Value,
    pub esps: Vec<Esp>,
}

/// A dataset's local user property K, from the dump's tools.datasets.
fn user_prop(tools: &Value, ds: &str, k: &str) -> Option<String> {
    tools
        .get("datasets")
        .and_then(Value::as_array)?
        .iter()
        .find(|d| d.get("name").and_then(Value::as_str) == Some(ds))?
        .at(&["properties", k, "value"])
        .and_then(Value::as_str)
        .map(str::to_string)
}

/// An inactive BE's /etc/version: its `etc` dataset mounted read-only in
/// dump's run directory, read, unmounted at once.  None (said) when it
/// can't be.
fn be_version(sys: &Sys, be: &BootEnv) -> Option<String> {
    let etc = be
        .children
        .iter()
        .find(|c| c.mountpoint == "/etc" || c.dataset == format!("{}/etc", be.dataset))?;
    if !sys.privileged || !safe_name(&be.name, b"-_.+") {
        return None;
    }
    // dump's own read-only mount of the BE's `etc` dataset:
    // `/run/bko-holt/truenas-be-NAME`.
    let at = format!("{RUN_DIR}/truenas-be-{}", be.name);
    let words = RoMountWords {
        cant_make: format!(
            "TrueNAS: can't make {at} to read boot environment {}'s /etc/version",
            be.name
        ),
        mount_failed: format!(
            "TrueNAS: a read-only mount of {} at {at} failed, so boot.truenas doesn't say boot environment {}'s version",
            etc.dataset, be.name
        ),
        still_mounted: format!(
            "TrueNAS: {} is still mounted read-only at {at}; `umount {at}` when this dump is done",
            etc.dataset
        ),
    };
    sys.with_ro_mount(
        "zfs",
        &crate::zfs_be::ro_options(etc.legacy()),
        &etc.dataset,
        &at,
        &words,
        |at| {
            sys.read_trim(&format!("{at}/version"))
                .map(|v| v.lines().next().unwrap_or("").trim().to_string())
                .filter(|v| !v.is_empty())
        },
    )?
}

/// boot.truenas for a TrueNAS system: its BEs (`bes`, crate::zfs_be::list
/// of the dump's tools, `tools`) and the ESPs of its boot-pool disks
/// (`esps`: device, disk, volume ID, label), each read through
/// `read_esp` (device -> contents; crate::boot's private read-only mount).
pub(crate) fn read(
    sys: &Sys,
    tools: &Value,
    bes: &[BootEnv],
    esps: Vec<(String, String, Option<String>, Option<String>)>,
    read_esp: &dyn Fn(&str) -> Option<Value>,
) -> TrueNas {
    let mut list = Vec::new();
    for be in bes.iter().filter(|b| b.rule == Rule::TrueNas) {
        // The booted one (a bootfs that isn't booted is warned of by
        // crate::zfs_be::backup_sources, and refused by load).
        let active = be.mounted;
        let version = if active {
            sys.read_trim(VERSION)
                .map(|v| v.lines().next().unwrap_or("").trim().to_string())
                .filter(|v| !v.is_empty())
        } else if be.locked {
            None
        } else {
            be_version(sys, be)
        };
        let clones: Vec<Value> = be
            .children
            .iter()
            .filter_map(|c| {
                c.origin.as_ref().map(|o| {
                    Obj::new()
                        .set("dataset", c.dataset.as_str())
                        .set("origin", o.as_str())
                        .build()
                })
            })
            .collect();
        list.push(
            Obj::new()
                .set("name", be.name.as_str())
                .set("dataset", be.dataset.as_str())
                .set("version", version)
                .set("kernel", user_prop(tools, &be.dataset, KERNEL_PROP))
                .set("keep", user_prop(tools, &be.dataset, KEEP_PROP))
                .set("active", active)
                .set("bootfs", be.bootfs)
                .set("origin", be.origin.clone())
                .set("clones", clones)
                .set("locked", be.locked)
                .build(),
        );
    }
    let mut out_esps = Vec::new();
    let mut esp_json = Vec::new();
    for (device, disk, uuid, label) in esps {
        let contents = read_esp(&device).unwrap_or(Value::Null);
        esp_json.push(
            Obj::new()
                .set("device", device.as_str())
                .set("disk", disk.as_str())
                .set("uuid", uuid)
                .set("label", label)
                .set("read", !contents.is_null())
                .build(),
        );
        out_esps.push(Esp { device, contents });
    }
    let json = Obj::new()
        .set("pool", crate::zfs_be::TRUENAS_POOL)
        .set(
            "version",
            sys.read_trim(VERSION)
                .map(|v| v.lines().next().unwrap_or("").trim().to_string()),
        )
        .set("bes", list)
        .set("esps", esp_json)
        .build();
    TrueNas {
        json,
        esps: out_esps,
    }
}

/// BOOT (the dump's boot section) with boot.truenas in it.
pub(crate) fn add_to_boot(boot: Value, t: Option<&TrueNas>) -> Value {
    let (Some(t), Value::Obj(mut o)) = (t, boot.clone()) else {
        return boot;
    };
    o.push(("truenas".into(), t.json.clone()));
    Value::Obj(o)
}
